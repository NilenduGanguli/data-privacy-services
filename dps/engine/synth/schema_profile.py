"""Structure profiles and generator-spec proposers for synthesize-from-schema (spec §5.6).

P1 holds: the LLM never sees a value. :func:`render_prompt` sends only structure — field names
(for a sample, a key the detector flags or that is not identifier-like is sent as ``field_N``,
since a key can be data such as a person's name or an e-mail address; the alias lives in the prompt
only, and the profile, the spec and the generated rows keep the caller's names), inferred types,
value *shapes* (``XX-9999``), orders of magnitude, null and distinct rates, DPS's own entity
verdicts, and for a categorical field only how many labels it has (``enum_size``). The labels
themselves (``FieldProfile.enum``: ≤ 12 distinct, letters only, each seen at least twice, nothing
detected in them) stay in DPS: without NER a column of first names passes every one of those tests,
so they are returned to the caller but never put in a prompt, and an enum field keeps DPS's
``choice`` whatever the model proposes; a model ``choice`` on any other field whose values are value
shapes or placeholders (all it could write without labels) is ignored too. Exact statistics stay in
:class:`FieldStats`, which is never put in a prompt; the rule-based proposer (no LLM) uses them,
sampled ranges rounded to two significant figures (one that rounding collapses to a single value
spans its orders of magnitude instead) and declared JSON Schema / DDL bounds exactly.

Synthesis from a sample never returns a sample's sensitive value: the executor steers around any
generated value that holds one (:func:`sample_guard`, ``execute_spec(avoid=...)``), and
:func:`verify_against_sample` refuses what is left. An all-digit original is matched as a whole
run of digits, so a sampled ID is not "found" inside a generated date or amount; types generated
from a small fixed space (gender, nationality, city, card expiry: ``SWEEP_EXEMPT``) are not looked
for; and a field whose key the detector flags is never categorical (its values are swept, so DPS
must not write them back).

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
from collections.abc import Callable, Iterator
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal
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
from dps.engine.verify import SWEEP_MIN_LEN, Verification, normalise, output_strings
from dps.llm.egress import LlmEgressBlocked
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
#: Attributes generated from a small fixed space, which the sample sweep does not look for: a
#: generated ``Female``, ``Mumbai`` or ``09/29`` is not a copy of a sampled one. Gender and
#: nationality are M0's (``surrogates._IDENTITY_OK``: forcing a different value would reveal the
#: original). Cities (318 under ``en_IN``) and card expiry dates (48 ``MM/YY`` values) are the same
#: case at volume (S8): four sampled cities were drawn in 72% of 100-row runs; a 1 000-row sample
#: holds about 300 of the 318 cities, so no run could steer around them (still refused); and where
#: steering works it removes the sample's cities from the output, which then shows which cities the
#: sample held by their absence — the reason M0 never forces gender or nationality to differ.
SWEEP_EXEMPT = frozenset({"GENDER", "NATIONALITY", "CITY", "CARD_EXPIRY"})
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
#: A sample key segment the model may see as is: identifier-like and no run of four digits.
_PLAIN_KEY = re.compile(r"(?!.*\d{4})[A-Za-z_][A-Za-z0-9_-]{0,62}")
#: An integer whose digits spell a calendar date (``20190411``): the one bare number NER may type.
_YYYYMMDD = re.compile(r"(?:1[89]|2[01])\d\d(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])")
#: A value *shape* (``xxxxxx``, ``XX-9999``: x, X, 9 and punctuation only) or a placeholder
#: (``<label_1>``, ``{SEGMENT_1}``, ``category_2``, ``REDACTED``): what a model that never saw a
#: field's labels writes into a ``choice`` in their place.
_SHAPE_VALUE = re.compile(r"[xX9\W_]*[xX9][xX9\W_]*")
_PLACEHOLDER_VALUE = re.compile(
    r"<[^<>]*>|\[[^\[\]]*\]|\{[^{}]*\}"
    r"|(?i:(?:label|value|val|category|cat|option|opt|enum|item|choice)"
    r"(?:[ _-]?\d{1,3}|[ _-][a-z]))"
    r"|(?i:placeholder|redacted|masked)(?:[ _-]?\d{1,3})?"
)
_PATTERN_LITERALS = set("Aa9X\\")
#: A run of digits in a normalised string (the sample sweep matches all-digit originals whole).
_DIGIT_RUN = re.compile(r"[0-9]+")
_CONSTRAINTS: TypeAdapter[list[Constraint]] = TypeAdapter(list[Constraint])

SPEC_SYSTEM = (
    "You design synthetic-data generators. You receive a JSON description of a table's structure: "
    "field names, types, value shapes (letters become x or X, digits 9), orders of magnitude, null rates "
    "and the entity type a privacy engine detected; for a categorical field only how many labels it "
    "has (enum_size), never the labels. Reply with ONE JSON object and nothing else: "
    '{"fields": [{"name": str, "generator": str, "params": {}, "null_rate": float, "unique": bool}], '
    '"constraints": [], "notes": [str]}. Generators and params: sequence{start,step,prefix,width}, uuid{}, '
    "integer{min,max}, decimal{min,max,scale}, normal{mean,std,min,max,scale}, "
    "lognormal{mu,sigma,min,max,scale}, choice{values,weights}, boolean{p_true}, "
    "date{start,end,format}, datetime{start,end}, pattern{template: A upper, a lower, 9 digit, "
    "X upper-or-digit}, text{words_min,words_max}, entity{entity_type,format}, constant{value}. Use "
    "entity for every field that has an entity_type. DPS generates fields with an entity_type or an "
    "enum_size itself: no constraint may write or reorder them. Constraints: {kind: order, fields: "
    "[...]} or {kind: rule, field, when: [{field, op: lt|le|gt|ge|eq|ne, value, then}], else}. Use "
    "exactly the field names given. Never write personal data."
)


class FieldProfile(BaseModel):
    """Structure of one field. ``enum`` holds categorical labels for the caller and the heuristic
    proposer only — :func:`render_prompt` sends their count (``enum_size``) instead — and
    ``prompt_alias`` (never serialised) marks a sample key the model sees as ``field_N`` while
    ``name`` stays the caller's."""

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
    prompt_alias: bool = Field(default=False, exclude=True)


class StructureProfile(BaseModel):
    source: Literal["json_schema", "ddl", "sample"]
    name: str = "dataset"
    fields: list[FieldProfile]
    rows_sampled: int | None = None


class FieldStats(BaseModel):
    """Exact statistics of a field — never serialised into a prompt or a response. Declared
    bounds (JSON Schema, DDL) keep their type, so an integer bound stays exact (``10**20 - 1``)."""

    minimum: int | float | None = None
    maximum: int | float | None = None
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

    def name(self, parts: list[str], index: int) -> str:
        """The name for a field whose raw path is ``parts`` (``index`` numbers aliases)."""
        if len(self._taken) >= MAX_FIELDS:
            _too_many_fields()
        candidate = (
            ".".join(_ident(part, index) for part in parts)
            if 1 <= len(parts) <= MAX_NAME_DEPTH
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


def _order_span(lo: float, hi: float, *, integer: bool) -> tuple[float, float]:
    """``[lo, hi]`` widened to the whole orders of magnitude it lies in (1001..1004 → 1000..9999,
    -5..30 → -9..99): bounds that say what the profile's ``magnitude`` says, never a sampled
    value."""

    def edge(value: float, upper: bool) -> float:
        if value == 0:
            return 0.0
        inner = 10.0 ** _order(value)
        outer = min(10.0 * inner - (1 if integer else 0), MAX_ABS)
        bound = outer if upper == (value > 0) else inner
        return bound if value > 0 else -bound

    return edge(lo, False), edge(hi, True)


def _range(s: FieldStats, *, sampled: bool, integer: bool) -> tuple[float, float]:
    """A numeric generator's ``(min, max)``. Declared bounds (JSON Schema, DDL) are the column's
    contract and are kept exactly (``NUMBER(5,2)`` → 0..999.99). An observed (sample) range is
    rounded to two significant figures; when rounding collapses it to one value (1001..1004 both
    round to 1000, so every row would be 1000) it spans its orders of magnitude instead
    (:func:`_order_span`)."""
    if s.minimum is None or s.maximum is None:
        low = _sig2(s.minimum) if s.minimum is not None else 0.0
        high = _sig2(s.maximum) if s.maximum is not None else 1000.0
        return min(low, high), max(low, high)
    if not sampled:
        return min(s.minimum, s.maximum), max(s.minimum, s.maximum)
    low, high = sorted((_sig2(s.minimum), _sig2(s.maximum)))
    if low < high:
        return low, high
    return _order_span(s.minimum, s.maximum, integer=integer)


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
                st.minimum, st.maximum = lo, hi
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


def _column_top(digits: int, scale: int, *, integer: bool) -> int | float:
    """The largest value a numeric column with ``digits`` integer digits and ``scale`` decimals
    holds: exact for an integer column (``NUMBER(3)`` → 999), and for a decimal one the largest
    float whose decimal form is not above it (``NUMBER(5,2)`` → 999.99; ``NUMBER(18,2)``'s
    9999999999999999.99 is no float and becomes 9999999999999998.0, never 1e16)."""
    if integer:
        return 10**digits - 1
    exact = Decimal(10) ** digits - Decimal(1).scaleb(-scale)
    top = float(exact)
    while Decimal(repr(top)) > exact:
        top = math.nextafter(top, -math.inf)
    return top


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
            scale = column.scale or 0
            digits = max(0, min(MAX_DIGITS, column.precision - scale))
            top = _column_top(digits, scale, integer=ftype == "integer")
            profile.magnitude = _magnitude(0.0, float(top), ftype == "integer")
            st.minimum, st.maximum, st.decimals = 0, top, scale
        fields.append(profile)
        stats[fname] = st
    profile_name = name or _ident(table, 0)
    return ProfileResult(
        profile=StructureProfile(source="ddl", name=profile_name, fields=fields), stats=stats
    )


# -- sample ---------------------------------------------------------------------------------------
def _calendar_number(value: Any) -> bool:
    """An integer whose digits spell a calendar date (``20190411``), as core-banking tables store
    dates."""
    if isinstance(value, bool) or not isinstance(value, int):
        return False
    text = str(value)
    if not _YYYYMMDD.fullmatch(text):
        return False
    try:
        date(int(text[:4]), int(text[4:6]), int(text[6:]))
    except ValueError:
        return False
    return True


def _value_entity(values: list[Any], detector: Detector) -> str | None:
    """The entity type at least ``VALUE_HIT_RATE`` of the (first 50) values are, else ``None``.

    NER alone does not vote on a JSON number (int or float) unless its digits spell a calendar
    date (``20190411``): spaCy reads almost any bare 4- to 12-digit number as a year or a date
    (``1001``, ``12345``), which typed an integer ID column ``DATE``. Pattern hits on numbers (a
    card, a phone or an Aadhaar written as a number) still vote, and strings — ISO dates among
    them — are read as before.
    """
    votes: Counter[str] = Counter()
    checked = 0
    for value in values[:50]:
        text = str(value)
        if not text.strip():
            continue
        checked += 1
        number = isinstance(value, int | float) and not isinstance(value, bool)
        for finding in detector.detect(text=text).findings:
            if (
                number
                and all(d.startswith("ner:") for d in finding.detectors)
                and not (finding.entity_type == "DATE" and _calendar_number(value))
            ):
                continue
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
        parts = key.split(".")
        # A key can be data (a person's name, an e-mail address). One the detector flags, or one
        # that is not identifier-like, reaches the model as ``field_N`` (``prompt_alias``); the
        # profile, the spec and the generated rows keep the caller's name.
        sensitive_name = bool(detector.detect(text=key).findings)
        fname = namer.name(parts, index)
        present = [v for v in values if v is not None and v != ""]
        ftype = _infer_type(present)
        entity = None if sensitive_name else _structural(parts[-1])
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
            prompt_alias=sensitive_name or not all(_PLAIN_KEY.fullmatch(part) for part in parts),
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
        # a flagged key's values are swept as originals (below), so DPS's own ``choice`` must not
        # write them back: such a field is shaped, never categorical
        if entity is None and ftype == "string" and strings and not sensitive_name:
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
            st.originals = [_canon(v) for v in present]
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
    sampled = rows_sampled is not None  # statistics observed in a sample, not declared bounds
    if p.type == "integer":
        lo, hi = _range(s, sampled=sampled, integer=True)
        # the integers inside the range: a declared ``minimum: 1.5`` is 2, never 1
        return FieldSpec(
            **base, generator="integer", params={"min": math.ceil(lo), "max": math.floor(hi)}
        )
    if p.type == "number":
        low, high = _range(s, sampled=sampled, integer=False)
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

    Sampled numeric ranges are rounded to two significant figures (one that rounding collapses
    spans its orders of magnitude) and declared bounds are kept exactly (see :func:`_range`). A field
    whose proposal does not validate (statistics outside what a generator accepts)
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


def _prompt_names(profile: StructureProfile) -> dict[str, str]:
    """Each field's name → the name the model sees: itself, or ``field_N`` for a sample key that is
    not identifier-like (unique, and never equal to or a prefix of a name sent as is)."""
    taken: set[str] = set()
    for f in profile.fields:
        if not f.prompt_alias:
            parts = f.name.split(".")
            taken.update(".".join(parts[:i]) for i in range(1, len(parts) + 1))
    names: dict[str, str] = {}
    for index, f in enumerate(profile.fields, start=1):
        if not f.prompt_alias:
            names[f.name] = f.name
            continue
        alias, extra = f"field_{index}", 1
        while alias in taken:
            extra += 1
            alias = f"field_{index}_{extra}"
        taken.add(alias)
        names[f.name] = alias
    return names


def render_prompt(profile: StructureProfile) -> list[dict[str, str]]:
    """The only messages the gateway ever receives for a spec proposal: the profile with each
    categorical field's labels replaced by their count and data-like sample keys aliased."""
    names = _prompt_names(profile)
    payload = profile.model_dump(mode="json", exclude_none=True)
    for field in payload["fields"]:
        field["name"] = names[field["name"]]
        labels = field.pop("enum", None)
        if labels is not None:
            field["enum_size"] = len(labels)
    body = json.dumps(payload, sort_keys=True)
    return [{"role": "system", "content": SPEC_SYSTEM}, {"role": "user", "content": body}]


def profile_originals(result: ProfileResult) -> list[str]:
    """Every sample value of a field DPS judged sensitive: the leak guard checks the prompt
    against them before any call. The prompt carries no raw values by construction (no labels,
    no data-like keys); this is defence in depth."""
    out: list[str] = []
    for st in result.stats.values():
        out += st.originals
    return out


def _llm_constraints(
    raw: Any, kept_by_dps: set[str], real: dict[str, str]
) -> tuple[list[dict[str, Any]], int] | None:
    """The model's constraints, with the prompt's names mapped back through ``real``, that neither
    write nor reorder a field in ``kept_by_dps`` (dumped for ``GeneratorSpec``), and how many were
    dropped; ``None`` when they do not parse. A name the prompt did not hold is left for the spec's
    own check to refuse.

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
        if isinstance(constraint, OrderConstraint):
            touched = {real.get(n, n) for n in constraint.fields}
            constraint = constraint.model_copy(
                update={"fields": [real.get(n, n) for n in constraint.fields]}
            )
        else:
            touched = {real.get(constraint.field, constraint.field)}
            cases = [
                case.model_copy(update={"field": real.get(case.field, case.field)})
                for case in constraint.when
            ]
            constraint = constraint.model_copy(
                update={"field": real.get(constraint.field, constraint.field), "when": cases}
            )
        if not touched & kept_by_dps:
            kept.append(constraint.model_dump(mode="json", by_alias=True))
    return kept, len(parsed) - len(kept)


def _invented_labels(candidate: dict[str, Any], shapes: set[str]) -> bool:
    """A model ``choice`` with a value shape or a placeholder among its values: one of the field's
    own shapes (what the prompt showed it), any shape of two or more characters, or a placeholder
    (``_PLACEHOLDER_VALUE``)."""
    params = candidate.get("params")
    values = params.get("values") if isinstance(params, dict) else None
    if candidate.get("generator") != "choice" or not isinstance(values, list):
        return False
    for value in values:
        text = value.strip() if isinstance(value, str) else ""
        if text and (
            text in shapes
            or (len(text) >= 2 and _SHAPE_VALUE.fullmatch(text))
            or _PLACEHOLDER_VALUE.fullmatch(text)
        ):
            return True
    return False


def merge_llm_spec(text: str, heuristic: GeneratorSpec, result: ProfileResult) -> GeneratorSpec:
    """Parse the model's reply (written in the prompt's names, see :func:`render_prompt`); keep its
    choices only for fields DPS does not generate itself — a detected field keeps its generator (a
    model cannot downgrade a detected SSN to an integer) and a categorical one its ``choice`` over
    the labels the model never saw. On any other field a model ``choice`` whose values are value
    shapes or placeholders (``xxxxxx``, ``<label_1>``: the model has no labels to give) is ignored
    and the heuristic generator kept, with a note naming how many. Its constraints may not write
    (``rule``) or reorder (``order``) a field DPS generates itself either: those are dropped with a
    note naming how many, and constraints that do not validate are all dropped with a note.

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
    real = {sent: name for name, sent in _prompt_names(result.profile).items()}
    proposed = {
        real[f["name"]]: f
        for f in data.get("fields", [])
        if isinstance(f, dict) and isinstance(f.get("name"), str) and f["name"] in real
    }
    if not proposed.keys() & {f.name for f in heuristic.fields}:
        raise ValueError("the reply proposes none of the fields")
    kept_by_dps = {p.name for p in result.profile.fields if p.entity_type or p.enum}
    shapes = {p.name: set(p.shapes) for p in result.profile.fields}
    fields: list[FieldSpec] = []
    shaped = 0
    for fallback in heuristic.fields:
        candidate = proposed.get(fallback.name)
        if candidate is None or fallback.name in kept_by_dps:
            fields.append(fallback)
            continue
        if _invented_labels(candidate, shapes.get(fallback.name, set())):
            fields.append(fallback)
            shaped += 1
            continue
        try:
            fields.append(
                FieldSpec.model_validate({**candidate, "name": fallback.name, "entity_type": None})
            )
        except ValidationError:
            fields.append(fallback)
    raw_notes = data.get("notes")
    notes = [str(n)[:200] for n in raw_notes][:20] if isinstance(raw_notes, list) else []
    if shaped:
        notes.append(
            f"{shaped} llm choice generator(s) ignored: their values were value shapes or "
            "placeholders (the model never sees labels); the DPS rule-based generator was kept"
        )
    base = {
        "name": heuristic.name,
        "locale": heuristic.locale,
        "fields": [f.model_dump() for f in fields],
        "source": "llm",
    }
    invalid = "llm constraints dropped (invalid)"
    constraints = _llm_constraints(data.get("constraints"), kept_by_dps, real)
    if constraints is None:
        return GeneratorSpec.model_validate({**base, "notes": [*notes, invalid]})
    kept, dropped = constraints
    if dropped:
        notes.append(
            f"{dropped} llm constraint(s) dropped: a constraint may not write or reorder a field "
            "DPS generates itself (detected or categorical)"
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
    originals = profile_originals(result)
    try:
        assert_placeholder_safe(messages[1]["content"], originals)
    except LeakToModelError:
        info["fallback"] = "leak_guard"
        return heuristic, info
    info["llm_calls"] = 1
    try:
        reply = await gateway.complete(
            messages, temperature=0.2, max_tokens=4096, purpose="genspec", protected=originals
        )
        spec = merge_llm_spec(reply.text, heuristic, result)
    except LlmEgressBlocked:  # M5: the egress verifier stopped it — nothing was sent
        info["llm_calls"] = 0
        info["fallback"] = "egress_blocked"
        return heuristic, info
    except (LlmUnavailable, LlmCallError) as exc:
        info["fallback"] = type(exc).__name__
        return heuristic, info
    except (ValueError, ValidationError):
        info["fallback"] = "llm_invalid_spec"
        return heuristic, info
    info["source"] = "llm"
    return spec, info


def _canon(value: Any) -> str:
    """A value as the sample sweep reads it: an integral float as its integer (a generated
    ``1001.0`` is the sampled ID ``1001``)."""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _canonical(node: Any) -> Any:
    """``node`` with every integral float read as its integer (see :func:`_canon`)."""
    if isinstance(node, dict):
        return {key: _canonical(value) for key, value in node.items()}
    if isinstance(node, list):
        return [_canonical(value) for value in node]
    if isinstance(node, float) and node.is_integer():
        return int(node)
    return node


def _sweep_originals(result: ProfileResult) -> list[tuple[Finding, str]]:
    """Every sample value the sweep looks for, with the finding it is reported as."""
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
    return originals


class _SampleValues:
    """The sample's sensitive values as the sweep looks for them, shared by the executor's guard
    (:func:`sample_guard`) and the sweep (:func:`verify_against_sample`), so the two agree on every
    value. Both read a value after :func:`~dps.engine.verify.normalise` (an integral float as its
    integer, :func:`_canon`) and skip originals shorter than ``SWEEP_MIN_LEN``. An all-digit
    original (an ID, a date's digits) is found only as a whole run of digits, with no digit next
    to it; any other original as a substring, as in M0. Text originals are indexed by their first
    ``SWEEP_MIN_LEN`` characters, so a value costs one lookup per position, whatever the size of
    the sample (the M0 sweep compares every original with every output string)."""

    def __init__(self, originals: list[tuple[Finding, str]]) -> None:
        self.findings: list[Finding] = []
        self.needles: list[str] = []
        digits: dict[str, list[int]] = {}
        index: dict[str, list[int]] = {}
        for finding, original in originals:
            needle = normalise(original)
            if len(needle) < SWEEP_MIN_LEN:
                continue
            slot = len(self.needles)
            self.needles.append(needle)
            self.findings.append(finding)
            if _DIGIT_RUN.fullmatch(needle):
                digits.setdefault(needle, []).append(slot)
            else:
                index.setdefault(needle[:SWEEP_MIN_LEN], []).append(slot)
        self._digits = {needle: tuple(slots) for needle, slots in digits.items()}
        self._index = {head: tuple(slots) for head, slots in index.items()}

    def __bool__(self) -> bool:
        return bool(self.needles)

    def _windows(self, hay: str) -> Iterator[int]:
        """The text originals ``hay`` holds (an original may be yielded more than once)."""
        for i in range(len(hay) - SWEEP_MIN_LEN + 1):
            for slot in self._index.get(hay[i : i + SWEEP_MIN_LEN], ()):
                if hay.startswith(self.needles[slot], i):
                    yield slot

    def found(self, hay: str) -> set[int]:
        """Every original the normalised string ``hay`` holds."""
        out = {slot for run in _DIGIT_RUN.findall(hay) for slot in self._digits.get(run, ())}
        out.update(self._windows(hay))
        return out

    def __call__(self, value: Any) -> bool:
        """The guard: true when a generated ``value`` holds one of the originals."""
        if value is None or isinstance(value, bool):
            return False
        hay = normalise(_canon(value))
        if any(run in self._digits for run in _DIGIT_RUN.findall(hay)):
            return True
        return next(self._windows(hay), None) is not None


def sample_guard(result: ProfileResult) -> Callable[[Any], bool] | None:
    """For synthesis from a sample: true for a generated value that holds one of the sample's
    sensitive values exactly as :func:`verify_against_sample` finds them, so the executor steers
    around it (``execute_spec(avoid=...)``); ``None`` when the sample has none to look for."""
    values = _SampleValues(_sweep_originals(result))
    return values if values else None


def verify_against_sample(rows: list[dict[str, Any]], result: ProfileResult) -> Verification:
    """Leakage sweep of generated rows against the sample's sensitive values: every key and value
    of the rows is searched, and a hit is reported once per output path and entity type, in the
    order of the originals (M0 ``verify_output`` semantics, matched as :class:`_SampleValues`).

    ``SWEEP_EXEMPT`` types (gender, nationality, city, card expiry) are not looked for: they are
    generated from a small fixed space, so a coincidence says nothing (see ``SWEEP_EXEMPT``). An
    all-digit original (an ID, a date's digits) is found only as a whole run of digits once
    separators are removed, and an integral float as its integer: sampled ID ``1001`` is found in
    a generated ``1001``, ``1001.0``, ``CUST-1001`` or ``x1001@…`` (and in ``10.01``, the same
    digits), not inside ``2010-01-04``, ``21001.5`` or ``1001.25``. Every other original is found
    as a substring, as in M0. The executor steers around every value :func:`sample_guard` flags,
    so a hit here is a spec literal or a value that could not get away from the sample: the route
    refuses the run (422 ``leakage``).
    """
    values = _SampleValues(_sweep_originals(result))
    found: list[tuple[int, int, str]] = []
    if values:
        for position, (path, _key, text) in enumerate(output_strings(_canonical(rows))):
            found += [(slot, position, path) for slot in values.found(normalise(text))]
    hits: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for slot, _position, path in sorted(found):
        entity = values.findings[slot].entity_type
        if (path, entity) not in seen:
            seen.add((path, entity))
            hits.append({"path": path, "entity_type": entity})
    return Verification(passed=not hits, checked=len(values.needles), hits=hits)
