"""Structure profiles and generator-spec proposers for synthesize-from-schema (spec §5.6).

P1 holds: the LLM never sees a value. A :class:`StructureProfile` is all that may leave DPS —
field names (aliased when a name itself looks sensitive), inferred types, value *shapes*
(``XX-9999``), orders of magnitude, null and distinct rates, DPS's own entity verdicts, and
categorical labels only when they are safe (≤ 12 distinct, letters only, each seen at least twice,
nothing detected in them). Exact statistics stay in :class:`FieldStats`, which is never put in a
prompt; the rule-based proposer (no LLM) uses them, rounded to two significant figures.

Every profile turns into a spec that validates: field names become unique identifiers nesting at
most four levels (a name that cannot be one, or that collides, is aliased ``field_N``), at most
``MAX_FIELDS`` fields are accepted (else ``schema_unsupported``), numbers that are not finite or
exceed ``MAX_ABS`` are not treated as numbers, and a field whose proposal still fails validation
falls back to ``text``. DDL is scanned in linear time (comments and quoted text included).
"""

from __future__ import annotations

import json
import math
import re
import statistics
from collections import Counter
from dataclasses import dataclass, replace
from datetime import date
from typing import Any, Literal, NoReturn, TypeGuard

from pydantic import BaseModel, Field, TypeAdapter, ValidationError

from dps.engine.detect.ensemble import Detector
from dps.engine.detect.structural import classify_key
from dps.engine.findings import Finding, Locator
from dps.engine.shapes import value_shape
from dps.engine.synth.llm_text import LeakToModelError, assert_placeholder_safe
from dps.engine.synth.schema_gen import (
    ENTITY_GENERATORS,
    MAX_ABS,
    MAX_SCALE,
    Constraint,
    FieldSpec,
    GeneratorSpec,
    OrderConstraint,
    SchemaError,
)
from dps.engine.taxonomy import entity_info
from dps.engine.verify import Verification, verify_output
from dps.llm.gateway import LlmCallError, LlmGateway, LlmUnavailable

FieldType = Literal["string", "integer", "number", "boolean", "date", "datetime", "uuid", "unknown"]
ENUM_MAX = 12
ENUM_MIN_COUNT = 2
MAX_SAMPLE_ROWS = 1000
MAX_DDL_CHARS = 100_000
STRUCTURAL_FLOOR = 0.7
VALUE_HIT_RATE = 0.5
#: A generator spec holds at most this many fields (``GeneratorSpec.fields``).
MAX_FIELDS = 200
#: Field names nest at most this many levels (``FieldSpec`` names).
MAX_NAME_DEPTH = 4
#: JSON Schema objects nested deeper than this are not walked.
MAX_SCHEMA_DEPTH = 32
#: Integer digits a DDL precision may ask for (Oracle's maximum; PostgreSQL allows 1000).
MAX_DIGITS = 38
#: ``lognormal`` parameters the executor accepts (``schema_gen._check_params``).
LOGNORMAL_MAX_MU, LOGNORMAL_MAX_SIGMA = 100.0, 20.0
#: Low-cardinality attributes the surrogate engine may draw equal to an original (M0
#: ``surrogates._IDENTITY_OK``: forcing a different value would reveal it), so the sample sweep
#: does not look for them — a generated ``Female`` is not a copy of a sampled ``Female``.
SWEEP_EXEMPT = frozenset({"GENDER", "NATIONALITY"})
#: Entity types whose surrogate is persona-coherent or rule-valid: always the ``entity`` generator.
DEDICATED_ENTITIES = frozenset(
    {
        "PERSON_NAME",
        "DATE_OF_BIRTH",
        "GENDER",
        "NATIONALITY",
        "AGE",
        "EMAIL",
        "PHONE",
        "STREET_ADDRESS",
        "CITY",
        "POSTAL_CODE",
        "SSN",
        "ITIN",
        "AADHAAR",
        "PAN_IN",
        "SIN",
        "CARD_PAN",
        "CARD_EXPIRY",
        "CVV",
        "IBAN",
        "ROUTING_ABA",
        "IP_ADDRESS",
        "MAC_ADDRESS",
        "URL",
        "USERNAME",
        "ORG_NAME",
    }
)
_SAFE_LABEL = re.compile(r"^[A-Za-z][A-Za-z _-]{0,23}$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DATETIME = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?$")
_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_NOT_IDENT = re.compile(r"[^A-Za-z0-9_]")
_PATTERN_LITERALS = set("Aa9X\\")
_CONSTRAINTS: TypeAdapter[list[Constraint]] = TypeAdapter(list[Constraint])

SPEC_SYSTEM = (
    "You design synthetic-data generators. You receive a JSON description of a table's structure: "
    "field names, types, value shapes (letters become x or X, digits 9), orders of magnitude, null rates "
    "and the entity type a privacy engine detected. Reply with ONE JSON object and nothing else: "
    '{"fields": [{"name": str, "generator": str, "params": {}, "null_rate": float, "unique": bool}], '
    '"constraints": [], "notes": [str]}. Generators and params: sequence{start,step,prefix,width}, uuid{}, '
    "integer{min,max}, decimal{min,max,scale}, normal{mean,std,min,max,scale}, "
    "lognormal{mu,sigma,min,max,scale}, choice{values,weights}, boolean{p_true}, "
    "date{start,end,format}, datetime{start,end}, pattern{template: A upper, a lower, 9 digit, "
    "X upper-or-digit}, text{words_min,words_max}, entity{entity_type,format}, constant{value}. Use "
    "entity for every field that has an entity_type. Constraints: {kind: order, fields: [...]} or "
    "{kind: rule, field, when: [{field, op: lt|le|gt|ge|eq|ne, value, then}], else}. Use exactly the "
    "field names given. Never write personal data."
)


class FieldProfile(BaseModel):
    """What may be said about one field to a model: structure, never values."""

    name: str
    type: FieldType
    nullable: bool = True
    null_rate: float | None = None
    distinct_ratio: float | None = None
    entity_type: str | None = None
    entity_source: Literal["structural", "values", "schema", "none"] = "none"
    shapes: list[str] = Field(default_factory=list)
    length: dict[str, int] | None = None
    magnitude: dict[str, Any] | None = None
    enum: list[str] | None = None
    year_range: list[int] | None = None
    primary_key: bool = False


class StructureProfile(BaseModel):
    source: Literal["json_schema", "ddl", "sample"]
    name: str = "dataset"
    fields: list[FieldProfile]
    rows_sampled: int | None = None


class FieldStats(BaseModel):
    """Exact statistics of a field — never serialised into a prompt or a response."""

    minimum: float | None = None
    maximum: float | None = None
    median: float | None = None
    p90: float | None = None
    decimals: int = 0
    true_rate: float | None = None
    enum_counts: dict[str, int] = Field(default_factory=dict)
    date_min: str | None = None
    date_max: str | None = None
    originals: list[str] = Field(default_factory=list)  # sensitive sample values (leakage checks)


class ProfileResult(BaseModel):
    profile: StructureProfile
    stats: dict[str, FieldStats] = Field(default_factory=dict)


@dataclass(frozen=True)
class DdlColumn:
    name: str
    type_name: str
    length: int | None
    precision: int | None
    scale: int | None
    nullable: bool
    primary_key: bool


# -- helpers --------------------------------------------------------------------------------------
def _ident(name: str, index: int) -> str:
    cleaned = _NOT_IDENT.sub("_", name.strip())[:63] or f"field_{index}"
    return cleaned if not cleaned[0].isdigit() else f"_{cleaned[:62]}"


def _finite(value: Any) -> TypeGuard[int | float]:
    """A number the executor can work with: int or float, finite, ``|x| ≤ MAX_ABS``."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return False
    return (isinstance(value, int) or math.isfinite(value)) and abs(value) <= MAX_ABS


def _too_many_fields() -> NoReturn:
    raise SchemaError("schema_unsupported", f"at most {MAX_FIELDS} fields are supported")


class _Namer:
    """Valid, unique field names: identifiers, dotted for nesting (≤ ``MAX_NAME_DEPTH`` levels),
    never equal to another name nor nesting under or over one. A name that cannot be one is
    aliased ``field_N``."""

    def __init__(self) -> None:
        self._taken: set[str] = set()
        self._prefixes: set[str] = set()

    def _free(self, name: str) -> bool:
        parts = name.split(".")
        return (
            name not in self._taken
            and name not in self._prefixes
            and not any(".".join(parts[:i]) in self._taken for i in range(1, len(parts)))
        )

    def name(self, parts: list[str], index: int, *, alias: bool = False) -> str:
        """The name for a field whose raw path is ``parts`` (``index`` numbers aliases)."""
        if len(self._taken) >= MAX_FIELDS:
            _too_many_fields()
        candidate = (
            ".".join(_ident(part, index) for part in parts)
            if not alias and 1 <= len(parts) <= MAX_NAME_DEPTH
            else ""
        )
        if not candidate or not self._free(candidate):
            candidate, extra = f"field_{index}", 1
            while not self._free(candidate):
                extra += 1
                candidate = f"field_{index}_{extra}"
        self._taken.add(candidate)
        parts = candidate.split(".")
        self._prefixes.update(".".join(parts[:i]) for i in range(1, len(parts)))
        return candidate


def _structural(name: str) -> str | None:
    candidates = classify_key(f"$.{name}")
    return (
        candidates[0].entity_type
        if candidates and candidates[0].score >= STRUCTURAL_FLOOR
        else None
    )


def _order(value: float) -> int:
    return 0 if value == 0 else int(math.floor(math.log10(abs(value))))


def _magnitude(lo: float, hi: float, integer: bool) -> dict[str, Any]:
    return {
        "min_order": _order(lo),
        "max_order": _order(hi),
        "negative": lo < 0,
        "integer": integer,
    }


def _sig2(value: float) -> float:
    if value == 0:
        return 0.0
    return round(value, -_order(value) + 1)


def _decimals(values: list[float]) -> int:
    best = 0
    for v in values:
        text = repr(float(v))
        if "e" not in text and "." in text:
            best = max(best, len(text.split(".")[1].rstrip("0")))
    return min(best, 6)


def _infer_type(values: list[Any]) -> FieldType:
    if not values:
        return "unknown"
    if all(isinstance(v, bool) for v in values):
        return "boolean"
    if all(isinstance(v, int) and _finite(v) for v in values):
        return "integer"
    if all(_finite(v) for v in values):
        return "number"
    if any(isinstance(v, dict | list) for v in values):
        return "unknown"
    strings = [str(v) for v in values]
    for pattern, kind in ((_UUID, "uuid"), (_DATE, "date"), (_DATETIME, "datetime")):
        if all(pattern.match(s) for s in strings):
            return kind  # type: ignore[return-value]
    return "string"


def _flatten(row: dict[str, Any], prefix: str = "", depth: int = 0) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in row.items():
        if isinstance(value, dict) and depth < MAX_SCHEMA_DEPTH:
            out.update(_flatten(value, f"{prefix}{key}.", depth + 1))
        else:
            out[f"{prefix}{key}"] = value
    return out


# -- JSON Schema ----------------------------------------------------------------------------------
def profile_from_json_schema(schema: dict[str, Any], *, name: str = "dataset") -> ProfileResult:
    """Profile a JSON Schema object (nested objects flatten to dotted names; arrays are skipped)."""
    if schema.get("type", "object") != "object" or not isinstance(schema.get("properties"), dict):
        raise SchemaError("schema_unsupported", "expected a JSON Schema object with properties")
    fields: list[FieldProfile] = []
    stats: dict[str, FieldStats] = {}
    namer = _Namer()

    def names(value: Any) -> set[str]:
        return {v for v in value if isinstance(v, str)} if isinstance(value, list) else set()

    def walk(properties: dict[str, Any], path: list[str], required: set[str]) -> None:
        for key, sub in properties.items():
            if not isinstance(sub, dict):
                continue
            kind = sub.get("type", "string")
            nullable = key not in required
            if isinstance(kind, list):
                nullable = nullable or "null" in kind
                kind = next((k for k in kind if k != "null"), "string")
            if kind == "object" and isinstance(sub.get("properties"), dict):
                if len(path) < MAX_SCHEMA_DEPTH:
                    walk(sub["properties"], [*path, key], names(sub.get("required")))
                continue
            if kind == "array":
                continue
            fmt = sub.get("format")
            ftype: FieldType = "string"
            if kind in ("integer", "number", "boolean"):
                ftype = kind
            if fmt == "date":
                ftype = "date"
            elif fmt == "date-time":
                ftype = "datetime"
            elif fmt == "uuid":
                ftype = "uuid"
            fname = namer.name([*path, key], len(fields) + 1)
            entity = "EMAIL" if fmt == "email" else _structural(key)
            profile = FieldProfile(
                name=fname,
                type=ftype,
                nullable=nullable,
                entity_type=entity,
                entity_source="schema" if fmt == "email" else "structural" if entity else "none",
            )
            st = FieldStats()
            enum = sub.get("enum")
            if isinstance(enum, list) and entity is None and enum and len(enum) <= 50:
                labels = [str(v) for v in enum]
                if all(_SAFE_LABEL.fullmatch(v) for v in labels):
                    profile.enum = labels
                    st.enum_counts = dict.fromkeys(labels, 1)
            lo, hi = sub.get("minimum"), sub.get("maximum")
            if _finite(lo) and _finite(hi) and ftype in ("integer", "number"):
                profile.magnitude = _magnitude(float(lo), float(hi), ftype == "integer")
                st.minimum, st.maximum = float(lo), float(hi)
            shortest, longest = sub.get("minLength", 0), sub.get("maxLength")
            if _finite(longest) and isinstance(longest, int) and isinstance(shortest, int):
                profile.length = {"min": max(0, shortest), "max": max(0, longest)}
            fields.append(profile)
            stats[fname] = st

    walk(schema["properties"], [], names(schema.get("required")))
    if not fields:
        raise SchemaError("schema_unsupported", "the schema has no scalar properties")
    return ProfileResult(
        profile=StructureProfile(source="json_schema", name=name, fields=fields), stats=stats
    )


# -- DDL ------------------------------------------------------------------------------------------
_CREATE = re.compile(
    r"create\s+(?:global\s+temporary\s+)?table\s+(?:if\s+not\s+exists\s+)?([^\s(]+)\s*\(",
    re.IGNORECASE,
)
_COLUMN = re.compile(
    r'^\s*("(?:[^"]|"")+"|`[^`]+`|\[[^\]]+\]|[A-Za-z_][A-Za-z0-9_$#]*)\s*(.*)$', re.DOTALL
)
_TYPE = re.compile(
    r"^([A-Za-z][A-Za-z0-9_]*(?:\s+(?:varying|precision))?)\s*"
    r"(?:\(\s*(\d+)\s*(?:,\s*(-?\d+)\s*)?(?:byte|char)?\s*\))?",
    re.IGNORECASE,
)
_TABLE_ITEMS = (
    "constraint",
    "primary key",
    "unique",
    "foreign key",
    "check",
    "key ",
    "index ",
    "exclude",
)


_QUOTES = "'\"`"


def _strip_comments(sql: str) -> str:
    """``--`` and ``/* */`` comments become a space; quoted text is kept as is. One linear pass
    (a lazy ``/* .*? */`` regex is quadratic on unterminated comments)."""
    out: list[str] = []
    i, n, quote = 0, len(sql), ""
    while i < n:
        ch = sql[i]
        if quote:
            if ch == quote:
                quote = ""
        elif ch in _QUOTES:
            quote = ch
        elif sql.startswith("--", i):
            end = sql.find("\n", i)
            i = n if end < 0 else end
            out.append(" ")
            continue
        elif sql.startswith("/*", i):
            end = sql.find("*/", i + 2)
            i = n if end < 0 else end + 2
            out.append(" ")
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _body_end(text: str, start: int) -> int | None:
    """The index just past the ``)`` that closes the ``(`` before ``start`` (quotes skipped)."""
    depth, quote = 1, ""
    for i in range(start, len(text)):
        ch = text[i]
        if quote:
            if ch == quote:
                quote = ""
        elif ch in _QUOTES:
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return i + 1
    return None


def _split_top_level(body: str) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    depth, quote = 0, ""
    for ch in body:
        if quote:
            current.append(ch)
            if ch == quote:
                quote = ""
            continue
        if ch in _QUOTES:
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "," and depth == 0:
            parts.append("".join(current).strip())
            current = []
            continue
        current.append(ch)
    parts.append("".join(current).strip())
    return [p for p in parts if p]


def _unquote(identifier: str) -> str:
    identifier = identifier.strip()
    if identifier[:1] in '"`[' and len(identifier) > 1:
        return identifier[1:-1].replace('""', '"')
    return identifier


def parse_ddl(ddl: str) -> tuple[str, list[DdlColumn]]:
    """``CREATE TABLE`` → (table name, columns). Parsed, never executed."""
    if len(ddl) > MAX_DDL_CHARS:
        raise SchemaError(
            "ddl_too_large", f"DDL is limited to {MAX_DDL_CHARS} characters", status=413
        )
    text = _strip_comments(ddl)
    match = _CREATE.search(text)
    if match is None:
        raise SchemaError("ddl_unparsed", "expected CREATE TABLE <name> ( … )")
    i = _body_end(text, match.end())
    if i is None:
        raise SchemaError("ddl_unparsed", "unbalanced parentheses in CREATE TABLE")
    columns: list[DdlColumn] = []
    primary: set[str] = set()
    for item in _split_top_level(text[match.end() : i - 1]):
        lowered = item.lower()
        if lowered.startswith(_TABLE_ITEMS):
            pk = re.search(r"primary\s+key\s*\(([^)]*)\)", item, re.IGNORECASE)
            if pk:
                primary |= {_unquote(c) for c in pk.group(1).split(",")}
            continue
        column = _COLUMN.match(item)
        if column is None:
            continue
        kind = _TYPE.match(column.group(2))
        if kind is None:
            continue
        first, second = kind.group(2), kind.group(3)
        tail = column.group(2)[kind.end() :].lower()
        columns.append(
            DdlColumn(
                name=_unquote(column.group(1)),
                type_name=re.sub(r"\s+", " ", kind.group(1).lower()),
                length=int(first) if first and second is None else None,
                precision=int(first) if first else None,
                scale=int(second) if second else None,
                nullable="not null" not in tail and "primary key" not in tail,
                primary_key="primary key" in tail,
            )
        )
    if not columns:
        raise SchemaError("ddl_unparsed", "no columns found in CREATE TABLE")
    table = _unquote(match.group(1).split(".")[-1])
    return table, [
        replace(c, primary_key=True, nullable=False) if c.name in primary else c for c in columns
    ]


def _ddl_type(column: DdlColumn) -> FieldType:
    t = column.type_name
    if t in ("uuid", "uniqueidentifier"):
        return "uuid"
    if t in ("boolean", "bool", "bit"):
        return "boolean"
    if t == "date":
        return "date"
    if t.startswith(("timestamp", "datetime")):
        return "datetime"
    if t in (
        "int",
        "integer",
        "bigint",
        "smallint",
        "tinyint",
        "serial",
        "bigserial",
        "smallserial",
        "int2",
        "int4",
        "int8",
    ):
        return "integer"
    if t in ("number", "numeric", "decimal"):
        return "integer" if column.precision is not None and not column.scale else "number"
    if t in (
        "float",
        "real",
        "double precision",
        "double",
        "binary_double",
        "binary_float",
        "money",
    ):
        return "number"
    if t in (
        "char",
        "varchar",
        "varchar2",
        "nvarchar",
        "nvarchar2",
        "nchar",
        "text",
        "clob",
        "nclob",
        "string",
        "character",
        "character varying",
    ):
        return "string"
    return "unknown"


def profile_from_ddl(ddl: str, *, name: str | None = None) -> ProfileResult:
    """Profile a ``CREATE TABLE`` statement (types, lengths, precision, keys, nullability)."""
    table, columns = parse_ddl(ddl)
    if len(columns) > MAX_FIELDS:
        _too_many_fields()
    fields: list[FieldProfile] = []
    stats: dict[str, FieldStats] = {}
    namer = _Namer()
    for index, column in enumerate(columns, start=1):
        fname = namer.name([column.name], index)
        ftype = _ddl_type(column)
        entity = _structural(column.name)
        profile = FieldProfile(
            name=fname,
            type=ftype,
            nullable=column.nullable,
            primary_key=column.primary_key,
            entity_type=entity,
            entity_source="structural" if entity else "none",
        )
        st = FieldStats()
        if ftype == "string" and column.length:
            profile.length = {"min": 0, "max": column.length}
        if ftype in ("integer", "number") and column.precision:
            digits = max(0, min(MAX_DIGITS, column.precision - (column.scale or 0)))
            top = 10.0**digits - 1
            profile.magnitude = _magnitude(0.0, top, ftype == "integer")
            st.minimum, st.maximum, st.decimals = 0.0, top, column.scale or 0
        fields.append(profile)
        stats[fname] = st
    profile_name = name or _ident(table, 0)
    return ProfileResult(
        profile=StructureProfile(source="ddl", name=profile_name, fields=fields), stats=stats
    )


# -- sample ---------------------------------------------------------------------------------------
def _value_entity(values: list[Any], detector: Detector) -> str | None:
    votes: Counter[str] = Counter()
    checked = 0
    for value in values[:50]:
        text = str(value)
        if not text.strip():
            continue
        checked += 1
        for finding in detector.detect(text=text).findings:
            covered = (
                len(text)
                if finding.locator.whole
                else (finding.locator.end or 0) - (finding.locator.start or 0)
            )
            if covered >= 0.8 * len(text.strip()):
                votes[finding.entity_type] += 1
                break
    if not votes or not checked:
        return None
    entity, count = votes.most_common(1)[0]
    return entity if count / checked >= VALUE_HIT_RATE else None


def profile_from_sample(
    rows: list[dict[str, Any]], detector: Detector, *, name: str = "dataset"
) -> ProfileResult:
    """Profile de-identified (or not — DPS does not trust it) sample rows into an LLM-safe shape."""
    if not rows:
        raise SchemaError("sample_empty", "the sample has no rows")
    if len(rows) > MAX_SAMPLE_ROWS:
        raise SchemaError(
            "sample_too_large", f"samples are limited to {MAX_SAMPLE_ROWS} rows", status=413
        )
    flats = [_flatten(row) for row in rows]
    keys: dict[str, None] = {}
    for flat in flats:
        keys.update(dict.fromkeys(flat))
    if len(keys) > MAX_FIELDS:
        _too_many_fields()
    columns = {key: [flat.get(key) for flat in flats] for key in keys}
    fields: list[FieldProfile] = []
    stats: dict[str, FieldStats] = {}
    namer = _Namer()
    for index, (key, values) in enumerate(columns.items(), start=1):
        sensitive_name = bool(detector.detect(text=key).findings)
        fname = namer.name(key.split("."), index, alias=sensitive_name)
        present = [v for v in values if v is not None and v != ""]
        ftype = _infer_type(present)
        entity = None if sensitive_name else _structural(key.split(".")[-1])
        source: Literal["structural", "values", "schema", "none"] = (
            "structural" if entity else "none"
        )
        if entity is None and ftype in ("string", "integer", "date"):
            entity = _value_entity(present, detector)
            source = "values" if entity else "none"
        profile = FieldProfile(
            name=fname,
            type=ftype,
            null_rate=round(1 - len(present) / len(values), 3),
            nullable=len(present) < len(values),
            entity_type=entity,
            entity_source=source,
        )
        st = FieldStats()
        if present:
            profile.distinct_ratio = round(
                len({json.dumps(v, default=str) for v in present}) / len(present), 3
            )
        strings = [str(v) for v in present if isinstance(v, str)]
        if strings and ftype in ("string", "unknown"):
            profile.shapes = [s for s, _ in Counter(value_shape(v) for v in strings).most_common(3)]
            profile.length = {"min": min(map(len, strings)), "max": max(map(len, strings))}
        numbers = [float(v) for v in present if _finite(v)]
        if numbers and ftype in ("integer", "number"):
            ordered = sorted(numbers)
            profile.magnitude = _magnitude(ordered[0], ordered[-1], ftype == "integer")
            st.minimum, st.maximum = ordered[0], ordered[-1]
            st.median = statistics.median(ordered)
            st.p90 = ordered[min(len(ordered) - 1, int(0.9 * len(ordered)))]
            st.decimals = _decimals(numbers) if ftype == "number" else 0
        if ftype == "boolean":
            st.true_rate = sum(1 for v in present if v is True) / len(present)
        if ftype in ("date", "datetime") and strings:
            st.date_min, st.date_max = min(strings), max(strings)
            if entity != "DATE_OF_BIRTH":
                profile.year_range = [int(st.date_min[:4]), int(st.date_max[:4])]
        if entity is None and ftype == "string" and strings:
            counts = Counter(strings)
            if (
                len(counts) <= ENUM_MAX
                and all(_SAFE_LABEL.fullmatch(v) for v in counts)
                and all(n >= ENUM_MIN_COUNT for n in counts.values())
                and not any(detector.detect(text=v).findings for v in counts)
            ):
                profile.enum = sorted(counts)
                st.enum_counts = dict(counts)
        if entity is not None or sensitive_name:
            st.originals = [str(v) for v in present]
        fields.append(profile)
        stats[fname] = st
    structure = StructureProfile(source="sample", name=name, fields=fields, rows_sampled=len(rows))
    return ProfileResult(profile=structure, stats=stats)


# -- proposers ------------------------------------------------------------------------------------
def _template(shape: str) -> str:
    out: list[str] = []
    for ch in shape:
        if ch == "x":
            out.append("a")
        elif ch == "X":
            out.append("A")
        elif ch == "9":
            out.append("9")
        else:
            out.append("\\" + ch if ch in _PATTERN_LITERALS else ch)
    return "".join(out)


def _heuristic_field(p: FieldProfile, s: FieldStats, rows_sampled: int | None) -> FieldSpec:
    null_rate = round(min(p.null_rate or 0.0, 0.95), 2) if p.nullable else 0.0
    base: dict[str, Any] = {"name": p.name, "null_rate": null_rate, "entity_type": p.entity_type}
    if p.entity_type in DEDICATED_ENTITIES:
        return FieldSpec(**base, generator="entity", params={"entity_type": p.entity_type})
    if p.primary_key and p.type == "integer":
        return FieldSpec(
            **{**base, "null_rate": 0.0}, generator="sequence", params={"start": 1}, unique=True
        )
    if p.type == "uuid":
        return FieldSpec(**base, generator="uuid", unique=p.primary_key)
    if p.type == "boolean":
        return FieldSpec(
            **base,
            generator="boolean",
            params={"p_true": round(s.true_rate if s.true_rate is not None else 0.5, 2)},
        )
    if p.type == "integer":
        lo = int(_sig2(s.minimum)) if s.minimum is not None else 0
        hi = int(_sig2(s.maximum)) if s.maximum is not None else 1000
        return FieldSpec(
            **base, generator="integer", params={"min": min(lo, hi), "max": max(lo, hi)}
        )
    if p.type == "number":
        low = _sig2(s.minimum) if s.minimum is not None else 0.0
        high = _sig2(s.maximum) if s.maximum is not None else 1000.0
        scale = min(max(s.decimals, 0) or 2, MAX_SCALE)
        if (
            s.minimum is not None
            and s.minimum >= 0
            and s.median
            and s.p90
            and s.median > 0
            and s.p90 / s.median > 3
            and abs(math.log(s.median)) <= LOGNORMAL_MAX_MU
            and math.log(s.p90 / s.median) / 1.2816 <= LOGNORMAL_MAX_SIGMA
        ):
            sigma = round(math.log(s.p90 / s.median) / 1.2816, 3)
            return FieldSpec(
                **base,
                generator="lognormal",
                params={
                    "mu": round(math.log(s.median), 3),
                    "sigma": sigma,
                    "min": 0,
                    "max": high,
                    "scale": scale,
                },
            )
        return FieldSpec(
            **base,
            generator="decimal",
            params={"min": min(low, high), "max": max(low, high), "scale": scale},
        )
    if p.type in ("date", "datetime"):
        first = int((s.date_min or "")[:4] or (p.year_range or [2015])[0])
        last = int((s.date_max or "")[:4] or (p.year_range or [0, date.today().year])[-1])
        first = min(max(first, 1), 9999)  # year 0000 matches the shape but is no date
        last = min(max(last, first), 9999)
        if p.type == "date":
            return FieldSpec(
                **base,
                generator="date",
                params={"start": f"{first:04d}-01-01", "end": f"{last:04d}-12-31"},
            )
        return FieldSpec(
            **base,
            generator="datetime",
            params={
                "start": f"{first:04d}-01-01T00:00:00+00:00",
                "end": f"{last:04d}-12-31T23:59:59+00:00",
            },
        )
    if p.enum:
        weights = [s.enum_counts.get(v, 1) for v in p.enum]
        return FieldSpec(**base, generator="choice", params={"values": p.enum, "weights": weights})
    if p.shapes:
        unique = p.primary_key or (p.distinct_ratio == 1.0 and (rows_sampled or 0) >= 20)
        return FieldSpec(
            **base, generator="pattern", params={"template": _template(p.shapes[0])}, unique=unique
        )
    if p.entity_type in ENTITY_GENERATORS:
        return FieldSpec(**base, generator="entity", params={"entity_type": p.entity_type})
    longest = (p.length or {}).get("max", 60)
    words_max = max(1, min(200, longest // 6))
    return FieldSpec(
        **base,
        generator="text",
        params={"words_min": max(1, words_max // 3), "words_max": words_max},
    )


def _text_field(p: FieldProfile) -> FieldSpec:
    return FieldSpec(
        name=p.name,
        generator="text",
        params={"words_min": 1, "words_max": 3},
        null_rate=round(min(p.null_rate or 0.0, 0.95), 2) if p.nullable else 0.0,
        entity_type=p.entity_type,
    )


def heuristic_spec(result: ProfileResult, *, locale: str) -> GeneratorSpec:
    """The rule-based proposal (no LLM): deterministic, uses the private statistics.

    A field whose proposal does not validate (statistics outside what a generator accepts)
    becomes short ``text``; the notes say how many did, never which values.
    """
    fields: list[FieldSpec] = []
    fallbacks = 0
    for p in result.profile.fields:
        try:
            fields.append(
                _heuristic_field(
                    p, result.stats.get(p.name, FieldStats()), result.profile.rows_sampled
                )
            )
        except ValidationError:
            fields.append(_text_field(p))
            fallbacks += 1
    name = _NOT_IDENT.sub("_", result.profile.name)[:64] or "dataset"
    notes = ["proposed by DPS rules (no LLM)"]
    if fallbacks:
        notes.append(f"{fallbacks} field(s) fell back to text (statistics out of range)")
    return GeneratorSpec(name=name, locale=locale, fields=fields, source="heuristic", notes=notes)


def render_prompt(profile: StructureProfile) -> list[dict[str, str]]:
    """The only messages the gateway ever receives for a spec proposal."""
    body = json.dumps(profile.model_dump(mode="json", exclude_none=True), sort_keys=True)
    return [{"role": "system", "content": SPEC_SYSTEM}, {"role": "user", "content": body}]


def profile_originals(result: ProfileResult) -> list[str]:
    """Every sample value of a field DPS judged sensitive (safe enum labels excluded): the leak
    guard checks the prompt against them before any call. The profile carries no raw values by
    construction; this is defence in depth."""
    allowed = {label for p in result.profile.fields for label in (p.enum or [])}
    out: list[str] = []
    for st in result.stats.values():
        out += [v for v in st.originals if v not in allowed]
    return out


def _llm_constraints(raw: Any, kept_by_dps: set[str]) -> tuple[list[dict[str, Any]], int] | None:
    """The model's constraints that neither write nor reorder a field in ``kept_by_dps`` (dumped
    for ``GeneratorSpec``), and how many were dropped; ``None`` when they do not parse.

    A ``rule`` replaces its field's generated value with a constant the model chose, and an
    ``order`` moves values between its fields, so either would undo the merge's rule that a field
    DPS detected keeps DPS's generator (a rule on a detected SSN made every row an issued SSN).
    """
    try:
        parsed = _CONSTRAINTS.validate_python(raw or [])
    except ValidationError:
        return None
    kept: list[dict[str, Any]] = []
    for constraint in parsed:
        touched = (
            set(constraint.fields)
            if isinstance(constraint, OrderConstraint)
            else {constraint.field}
        )
        if not touched & kept_by_dps:
            kept.append(constraint.model_dump(mode="json", by_alias=True))
    return kept, len(parsed) - len(kept)


def merge_llm_spec(text: str, heuristic: GeneratorSpec, result: ProfileResult) -> GeneratorSpec:
    """Parse the model's reply; keep its choices only for non-sensitive fields (a model cannot
    downgrade a detected SSN to an integer). Its constraints may not write (``rule``) or reorder
    (``order``) a sensitive field either: those are dropped with a note naming how many, and
    constraints that do not validate are all dropped with a note.

    Raises:
        ValueError: the reply holds no JSON object, its ``fields`` is not a list, or it proposes
            none of the profile's fields (an unusable reply).
    """
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object in the reply")
    try:
        data = json.loads(text[start : end + 1])
    except RecursionError:
        raise ValueError("the reply nests too deeply") from None
    if not isinstance(data, dict) or not isinstance(data.get("fields", []), list):
        raise ValueError("the reply has no list of fields")
    proposed = {
        f["name"]: f
        for f in data.get("fields", [])
        if isinstance(f, dict) and isinstance(f.get("name"), str)
    }
    if not proposed.keys() & {f.name for f in heuristic.fields}:
        raise ValueError("the reply proposes none of the fields")
    sensitive = {p.name for p in result.profile.fields if p.entity_type}
    fields: list[FieldSpec] = []
    for fallback in heuristic.fields:
        candidate = proposed.get(fallback.name)
        if candidate is None or fallback.name in sensitive:
            fields.append(fallback)
            continue
        try:
            fields.append(
                FieldSpec.model_validate({**candidate, "name": fallback.name, "entity_type": None})
            )
        except ValidationError:
            fields.append(fallback)
    raw_notes = data.get("notes")
    notes = [str(n)[:200] for n in raw_notes][:20] if isinstance(raw_notes, list) else []
    base = {
        "name": heuristic.name,
        "locale": heuristic.locale,
        "fields": [f.model_dump() for f in fields],
        "source": "llm",
    }
    invalid = "llm constraints dropped (invalid)"
    constraints = _llm_constraints(data.get("constraints"), sensitive)
    if constraints is None:
        return GeneratorSpec.model_validate({**base, "notes": [*notes, invalid]})
    kept, dropped = constraints
    if dropped:
        notes.append(
            f"{dropped} llm constraint(s) dropped: a constraint may not write or reorder a field "
            "DPS detected as sensitive"
        )
    try:
        return GeneratorSpec.model_validate({**base, "notes": notes, "constraints": kept})
    except ValidationError:
        return GeneratorSpec.model_validate({**base, "notes": [*notes, invalid]})


async def propose_spec(
    result: ProfileResult, gateway: LlmGateway | None, *, locale: str, use_llm: bool = True
) -> tuple[GeneratorSpec, dict[str, Any]]:
    """A generator spec for the profile: the model's (validated, merged) when a real provider is
    configured and the leak guard passes, else the heuristic one. ``info`` is log-safe."""
    info: dict[str, Any] = {"llm_calls": 0, "source": "heuristic", "fallback": None}
    heuristic = heuristic_spec(result, locale=locale)
    if (
        not use_llm
        or gateway is None
        or gateway.provider in ("", "stub")
        or not gateway.available()
    ):
        return heuristic, info
    messages = render_prompt(result.profile)
    try:
        assert_placeholder_safe(messages[1]["content"], profile_originals(result))
    except LeakToModelError:
        info["fallback"] = "leak_guard"
        return heuristic, info
    info["llm_calls"] = 1
    try:
        reply = await gateway.complete(
            messages, temperature=0.2, max_tokens=4096, purpose="genspec"
        )
        spec = merge_llm_spec(reply.text, heuristic, result)
    except (LlmUnavailable, LlmCallError) as exc:
        info["fallback"] = type(exc).__name__
        return heuristic, info
    except (ValueError, ValidationError):
        info["fallback"] = "llm_invalid_spec"
        return heuristic, info
    info["source"] = "llm"
    return spec, info


def verify_against_sample(rows: list[dict[str, Any]], result: ProfileResult) -> Verification:
    """Leakage sweep of generated rows against the sample's sensitive values (M0 verifier).

    ``SWEEP_EXEMPT`` types (gender, nationality) are not looked for: their generated values come
    from a handful of words, so a sampled ``Female`` would fail every healthy run.
    """
    originals: list[tuple[Finding, str]] = []
    for profile in result.profile.fields:
        stats = result.stats.get(profile.name)
        if stats is None or not stats.originals or profile.entity_type in SWEEP_EXEMPT:
            continue
        entity = profile.entity_type or "BUSINESS_SENSITIVE"
        finding = Finding(
            locator=Locator(path=f"$.{profile.name}"),
            entity_type=entity,
            score=1.0,
            detectors=["sample"],
            sensitivity=entity_info(entity).sensitivity,
        )
        originals += [(finding, value) for value in stats.originals]
    return verify_output(originals, rows)
