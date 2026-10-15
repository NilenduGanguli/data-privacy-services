"""Synthesize rows from a generator spec (spec §5.6 "synthesize from schema").

A ``GeneratorSpec`` names one generator per field. The LLM may *propose* a spec (see
``schema_profile``); this module *executes* it, offline and deterministically: the same spec,
seed and row count give the same rows, byte for byte. PII-typed fields come from the surrogate
engine with one persona per row (name, e-mail and date of birth agree) and rule-valid, never
issued identifiers; nothing is drawn from real data. The spec is a reviewable, reusable artefact.
Synthesis from a sample also passes a predicate that flags a value holding one of the sample's
sensitive values (``execute_spec(avoid=...)``), and the executor steers around such a value
instead of writing it: a drawn value is drawn again, a sequence skips it, and an entity value
takes the row's whole persona with it, so name, e-mail and birth date still agree. The sample
only ever shapes what is *not* produced: a row that held no sample value is the same with or
without the sample (a sequence that skipped one stays shifted from there on).

Two entity types are relative to today, as everywhere in the surrogate engine: ``AGE`` agrees with
the row's date of birth as of the day of execution, and ``CARD_EXPIRY`` lies one to four years
after the current year. Every other value is a pure function of (spec, seed, row).

Every parameter is checked before anything runs, so a spec that validates cannot fail during
execution except with ``unique_exhausted``: numbers are finite and bounded (``MAX_ABS``), scales
are 0..``MAX_SCALE``, and constraints never rewrite a unique field or mix types and formats.
"""

from __future__ import annotations

import csv
import hashlib
import hmac
import io
import json
import math
import operator
import random
import re
import uuid
from collections import Counter
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Any, Literal, NoReturn

from faker import Faker
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from dps.engine.synth.personas import normalise_locale, seed_int
from dps.engine.synth.surrogates import SurrogateEngine
from dps.errors import CodedError

SPEC_VERSION = "dps.genspec.v1"
GeneratorName = Literal[
    "sequence",
    "uuid",
    "integer",
    "decimal",
    "normal",
    "lognormal",
    "choice",
    "boolean",
    "date",
    "datetime",
    "pattern",
    "text",
    "entity",
    "constant",
]
#: Entity types the ``entity`` generator produces. Credentials are never synthesised.
ENTITY_GENERATORS = frozenset(
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
        "DEVICE_ID",
        "ORG_NAME",
        "CUSTOMER_ID",
        "ACCOUNT_NUMBER",
        "EMPLOYEE_ID",
        "CASE_ID",
    }
)
#: Shape templates handed to the surrogate engine (it replaces digits and keeps separators).
#: Every entity in ``ENTITY_GENERATORS`` has one: the engine returns a blank original unchanged.
#: Where the engine ignores the original's content, the template only picks a layout (``+ phone``:
#: the row persona's country, international format; ``YYYY-MM-DD``: the persona's birth date).
ENTITY_TEMPLATES: dict[str, str] = {
    "PERSON_NAME": "First Last",
    "DATE_OF_BIRTH": "YYYY-MM-DD",
    "GENDER": "Gender",
    "NATIONALITY": "Nationality",
    "AGE": "age",
    "CARD_EXPIRY": "MM/YY",
    "IBAN": "GB00 AAAA 0000 0000 0000 00",
    "IP_ADDRESS": "0.0.0.0",
    "MAC_ADDRESS": "00:00:00:00:00:00",
    "URL": "https://host/path",
    "ORG_NAME": "Org",
    "SSN": "000-00-0000",
    "ITIN": "900-70-0000",
    "AADHAAR": "0000 0000 0000",
    "PAN_IN": "AAAAA0000A",
    "SIN": "000 000 000",
    "CARD_PAN": "0000 0000 0000 0000",
    "CVV": "000",
    "ROUTING_ABA": "000000000",
    "PHONE": "+ phone",
    "CUSTOMER_ID": "CUST-000000",
    "ACCOUNT_NUMBER": "00000000000000",
    "EMPLOYEE_ID": "EMP-000000",
    "CASE_ID": "CASE-000000",
    "DEVICE_ID": "0000000000000000",
    "STREET_ADDRESS": "1 Main Street",
    "CITY": "City",
    "POSTAL_CODE": "00000",
    "EMAIL": "first.last@example.com",
    "USERNAME": "user",
}
DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d %b %Y", "%Y%m%d")
SORTABLE_DATE_FORMATS = ("%Y-%m-%d", "%Y%m%d")
MAX_UNIQUE_RETRIES = 50
#: How ``execute_spec(avoid=...)`` steers a generator around a value that holds a sample value:
#: ``redraw`` draws that value again; ``skip`` moves a sequence on to its next value (it stays
#: unique and ascending); ``persona`` draws every ``entity`` value of the row again from a new
#: persona. ``constant``, ``choice`` and ``boolean`` are never steered: they write the spec's own
#: literals or a flag, and a sample value one of them writes is left for the sample sweep to refuse.
STEERING: dict[str, Literal["redraw", "skip", "persona"]] = {
    "integer": "redraw",
    "decimal": "redraw",
    "normal": "redraw",
    "lognormal": "redraw",
    "date": "redraw",
    "datetime": "redraw",
    "pattern": "redraw",
    "text": "redraw",
    "uuid": "redraw",  # 32 hex digits: a sampled 4-digit ID is a whole digit run in a few in 10^4
    "sequence": "skip",
    "entity": "persona",
}
#: New draws (values, sequence steps or personas) one value may take to get away from the
#: sample. A value still holding a sample value after them is kept, and the sweep refuses the run:
#: from then on the executor stops steering (the run is lost, so further draws would only cost).
MAX_SAMPLE_REDRAWS = 20
#: Largest magnitude of a float-valued parameter (keeps uniform/gauss draws finite).
MAX_ABS = 1e100
MAX_SCALE = 10
_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}(\.[A-Za-z_][A-Za-z0-9_]{0,62}){0,3}$")
_PREFIX = re.compile(r"^[A-Za-z0-9_-]{0,16}$")
_FORMAT = re.compile(r"^(?=.*[0-9A-Za-z])[0-9A-Za-z .+@_/-]{1,40}$")
_PATTERN_CLASSES = {
    "A": "ABCDEFGHJKLMNPQRSTUVWXYZ",
    "a": "abcdefghijkmnopqrstuvwxyz",
    "9": "0123456789",
    "X": "ABCDEFGHJKLMNPQRSTUVWXYZ0123456789",
}
_ALLOWED: dict[str, set[str]] = {
    "sequence": {"start", "step", "prefix", "width"},
    "uuid": set(),
    "integer": {"min", "max"},
    "decimal": {"min", "max", "scale"},
    "normal": {"mean", "std", "min", "max", "scale"},
    "lognormal": {"mu", "sigma", "min", "max", "scale"},
    "choice": {"values", "weights"},
    "boolean": {"p_true"},
    "date": {"start", "end", "format"},
    "datetime": {"start", "end"},
    "pattern": {"template"},
    "text": {"words_min", "words_max"},
    "entity": {"entity_type", "format"},
    "constant": {"value"},
}
Scalar = str | int | float | bool | None


class SchemaError(CodedError):
    """A spec, profile or request the schema synthesizer refuses (422 unless stated)."""

    status = 422


def _is_number(value: Any) -> bool:
    """A finite int or float of magnitude ≤ ``MAX_ABS`` (never a bool, NaN or infinity)."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return False
    return (isinstance(value, int) or math.isfinite(value)) and abs(value) <= MAX_ABS


def _is_scalar(value: Any) -> bool:
    """A JSON scalar that serialises: no containers, no NaN or infinity."""
    if isinstance(value, float):
        return math.isfinite(value)
    return value is None or isinstance(value, str | int | bool)


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _iso_date(value: Any) -> date:
    return date.fromisoformat(str(value))


def _iso_datetime(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _check_params(name: str, generator: str, p: dict[str, Any]) -> None:  # noqa: C901 - one rule per generator
    def fail(why: str) -> NoReturn:
        raise ValueError(f"field {name!r} ({generator}): {why}")

    unknown = set(p) - _ALLOWED[generator]
    if unknown:
        fail(f"unknown params {sorted(unknown)}")
    if generator == "sequence":
        if not _is_int(p.get("start", 1)) or not _is_int(p.get("step", 1)) or p.get("step", 1) == 0:
            fail("start and step must be integers, step non-zero")
        if not isinstance(p.get("prefix", ""), str) or not _PREFIX.fullmatch(p.get("prefix", "")):
            fail("prefix must be ≤ 16 of [A-Za-z0-9_-]")
        if not _is_int(p.get("width", 0)) or not 0 <= p.get("width", 0) <= 18:
            fail("width must be 0..18")
    elif generator == "integer":
        if not (_is_int(p.get("min")) and _is_int(p.get("max"))) or p["min"] > p["max"]:
            fail("needs integer min ≤ max")
    elif generator == "decimal":
        if not (_is_number(p.get("min")) and _is_number(p.get("max"))) or p["min"] > p["max"]:
            fail(f"needs numeric min ≤ max (|x| ≤ {MAX_ABS:g})")
    elif generator in ("normal", "lognormal"):
        centre, spread = ("mean", "std") if generator == "normal" else ("mu", "sigma")
        if not (_is_number(p.get(centre)) and _is_number(p.get(spread))) or p[spread] <= 0:
            fail(f"needs numeric {centre} and {spread} > 0")
        if generator == "lognormal" and (abs(p["mu"]) > 100 or p["sigma"] > 20):
            fail("needs |mu| ≤ 100 and sigma ≤ 20")
        for bound in ("min", "max"):
            if bound in p and not _is_number(p[bound]):
                fail(f"{bound} must be numeric")
        if "min" in p and "max" in p and p["min"] > p["max"]:
            fail("min must be ≤ max")
    elif generator == "choice":
        _check_choice(p, fail)
    elif generator == "boolean":
        if not _is_number(p.get("p_true", 0.5)) or not 0 <= p.get("p_true", 0.5) <= 1:
            fail("p_true must be 0..1")
    elif generator == "date":
        try:
            first, last = _iso_date(p["start"]), _iso_date(p["end"])
        except (KeyError, ValueError):
            fail("needs ISO dates start and end")
        if first > last:
            fail("start must be ≤ end")
        if p.get("format", DATE_FORMATS[0]) not in DATE_FORMATS:
            fail(f"format must be one of {list(DATE_FORMATS)}")
    elif generator == "datetime":
        try:
            first_dt, last_dt = _iso_datetime(p["start"]), _iso_datetime(p["end"])
        except (KeyError, ValueError):
            fail("needs ISO datetimes start and end")
        if first_dt > last_dt:
            fail("start must be ≤ end")
    elif generator == "pattern":
        if not isinstance(p.get("template"), str) or not 1 <= len(p["template"]) <= 64:
            fail("template must be 1..64 characters")
    elif generator == "text":
        lo, hi = p.get("words_min", 3), p.get("words_max", 12)
        if not (_is_int(lo) and _is_int(hi)) or not 1 <= lo <= hi <= 200:
            fail("needs 1 ≤ words_min ≤ words_max ≤ 200")
    elif generator == "entity":
        if p.get("entity_type") not in ENTITY_GENERATORS:
            fail(f"entity_type must be one of {sorted(ENTITY_GENERATORS)}")
        if "format" in p and (
            not isinstance(p["format"], str) or not _FORMAT.fullmatch(p["format"])
        ):
            fail("format must be ≤ 40 of [0-9A-Za-z .+@_/-], with a letter or digit")
    elif generator == "constant":
        if not _is_scalar(p.get("value")):
            fail("value must be a scalar")
    if generator in ("decimal", "normal", "lognormal"):
        scale = p.get("scale", 2)
        if not _is_int(scale) or not 0 <= scale <= MAX_SCALE:
            fail(f"scale must be an integer 0..{MAX_SCALE}")


def _check_choice(p: dict[str, Any], fail: Callable[[str], NoReturn]) -> None:
    values, weights = p.get("values"), p.get("weights")
    if not isinstance(values, list) or not 1 <= len(values) <= 200:
        fail("values must be a list of 1..200 scalars")
    if not all(_is_scalar(v) for v in values):
        fail("values must be scalars")
    if weights is not None and (
        not isinstance(weights, list)
        or len(weights) != len(values)
        or any(not _is_number(w) or w < 0 for w in weights)
        or sum(weights) <= 0
    ):
        fail("weights must be non-negative numbers, one per value, not all zero")


def _sort_class(field: FieldSpec) -> tuple[str | int, ...] | None:
    """What an order constraint may sort this field with (``None``: not sortable).

    Sorting moves values between fields, so the fields must agree on type and format: dates in
    one sortable format, datetimes, integers, or numbers of one scale.
    """
    g, p = field.generator, field.params
    if g == "date":
        fmt = p.get("format", DATE_FORMATS[0])
        return ("date", fmt) if fmt in SORTABLE_DATE_FORMATS else None
    if g == "datetime":
        return ("datetime",)
    if g == "sequence":
        return None if p.get("prefix") or p.get("width") else ("integer",)
    if g == "integer":
        return ("integer",)
    if g in ("decimal", "normal", "lognormal"):
        scale = int(p.get("scale", 2))
        return ("integer",) if scale == 0 and g != "decimal" else ("number", scale)
    return None


class FieldSpec(BaseModel):
    """One output field: a generator, its parameters, a null rate and a uniqueness flag."""

    model_config = ConfigDict(extra="forbid")
    name: str
    generator: GeneratorName
    params: dict[str, Any] = Field(default_factory=dict)
    null_rate: float = Field(default=0.0, ge=0.0, le=0.95)
    unique: bool = False
    entity_type: str | None = None  # what DPS detected (informational; ``entity`` reads params)

    @field_validator("name")
    @classmethod
    def _valid_name(cls, value: str) -> str:
        if not _NAME.fullmatch(value):
            raise ValueError(
                "field names are identifiers, optionally dotted for nesting (≤ 4 levels)"
            )
        return value

    @model_validator(mode="after")
    def _valid_params(self) -> FieldSpec:
        _check_params(self.name, self.generator, self.params)
        return self


class OrderConstraint(BaseModel):
    """The named fields are ascending within every row (dates, datetimes or numbers)."""

    model_config = ConfigDict(extra="forbid")
    kind: Literal["order"] = "order"
    fields: list[str] = Field(min_length=2, max_length=5)


class RuleCase(BaseModel):
    """``field op value`` → ``then``."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    field: str
    op: Literal["lt", "le", "gt", "ge", "eq", "ne"]
    value: str | int | float | bool
    then: str | int | float | bool | None


class RuleConstraint(BaseModel):
    """``field`` = the first matching case's ``then``, else ``else`` (``status = f(balance)``)."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True, allow_inf_nan=False)
    kind: Literal["rule"] = "rule"
    field: str
    when: list[RuleCase] = Field(min_length=1, max_length=10)
    otherwise: str | int | float | bool | None = Field(default=None, alias="else")


Constraint = Annotated[OrderConstraint | RuleConstraint, Field(discriminator="kind")]


class GeneratorSpec(BaseModel):
    """A reusable, reviewable recipe for synthetic rows (``dps.genspec.v1``)."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    spec_version: Literal["dps.genspec.v1"] = "dps.genspec.v1"
    name: str = Field(default="dataset", pattern=r"^[A-Za-z0-9_.-]{1,64}$")
    locale: str = "en_US"
    fields: list[FieldSpec] = Field(min_length=1, max_length=200)
    constraints: list[Constraint] = Field(default_factory=list)
    source: Literal["llm", "heuristic", "caller"] = "caller"
    notes: list[str] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def _consistent(self) -> GeneratorSpec:
        names = [f.name for f in self.fields]
        duplicates = sorted({n for n in names if names.count(n) > 1})
        if duplicates:
            raise ValueError(f"duplicate field names {duplicates}")
        by_name = {f.name: f for f in self.fields}
        for name in names:
            parts = name.split(".")
            for i in range(1, len(parts)):
                if ".".join(parts[:i]) in by_name:
                    raise ValueError(
                        f"field {name!r} nests under the scalar field {'.'.join(parts[:i])!r}"
                    )
        for constraint in self.constraints:
            if isinstance(constraint, OrderConstraint):
                if len(set(constraint.fields)) != len(constraint.fields):
                    raise ValueError("order constraint names a field twice")
                kinds = set()
                for name in constraint.fields:
                    spec = by_name.get(name)
                    if spec is None:
                        raise ValueError(f"order constraint names unknown field {name!r}")
                    kind = _sort_class(spec)
                    if kind is None:
                        raise ValueError(f"order constraint field {name!r} is not sortable")
                    if spec.unique:
                        raise ValueError(f"order constraint field {name!r} is unique")
                    kinds.add(kind)
                if len(kinds) != 1:
                    raise ValueError(
                        "order constraint fields must all be integers, numbers of one scale, "
                        "dates of one format or datetimes"
                    )
            else:
                for name in (constraint.field, *(case.field for case in constraint.when)):
                    if name not in by_name:
                        raise ValueError(f"rule constraint names unknown field {name!r}")
                if by_name[constraint.field].unique:
                    raise ValueError(f"rule constraint field {constraint.field!r} is unique")
        return self

    def spec_hash(self) -> str:
        """sha256 of the canonical spec, ignoring ``source`` and ``notes``."""
        canon = json.dumps(
            self.model_dump(mode="json", by_alias=True, exclude={"source", "notes"}),
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canon.encode("utf-8")).hexdigest()


class SchemaResult(BaseModel):
    rows: list[dict[str, Any]]
    spec_hash: str
    generators_used: dict[str, int]
    nulls: int
    unique_retries: int
    sample_redraws: int = 0


def _pattern(template: str, rng: random.Random) -> str:
    out: list[str] = []
    escaped = False
    for ch in template:
        if escaped:
            out.append(ch)
            escaped = False
        elif ch == "\\":
            escaped = True
        elif ch in _PATTERN_CLASSES:
            out.append(rng.choice(_PATTERN_CLASSES[ch]))
        else:
            out.append(ch)
    return "".join(out)


def _bounded(value: float, p: dict[str, Any]) -> float | int:
    if "min" in p:
        value = max(value, float(p["min"]))
    if "max" in p:
        value = min(value, float(p["max"]))
    scale = int(p.get("scale", 2))
    return round(value, scale) if scale > 0 else int(round(value))


def _sequence(p: dict[str, Any], index: int) -> int | str:
    """The ``index``-th value of a ``sequence`` (``index`` is the row, plus any steps skipped)."""
    n = int(p.get("start", 1)) + index * int(p.get("step", 1))
    prefix, width = str(p.get("prefix", "")), int(p.get("width", 0))
    return f"{prefix}{str(n).zfill(width)}" if prefix or width else n


def _generate(
    spec: FieldSpec,
    row: int,
    rng: random.Random,
    surrogates: SurrogateEngine,
    fake: Faker,
    locale: str,
) -> Any:
    p, g = spec.params, spec.generator
    if g == "sequence":
        return _sequence(p, row)
    if g == "uuid":
        return str(uuid.UUID(int=rng.getrandbits(128), version=4))
    if g == "integer":
        return rng.randint(int(p["min"]), int(p["max"]))
    if g == "decimal":
        return round(rng.uniform(float(p["min"]), float(p["max"])), int(p.get("scale", 2)))
    if g == "normal":
        return _bounded(rng.gauss(float(p["mean"]), float(p["std"])), p)
    if g == "lognormal":
        return _bounded(rng.lognormvariate(float(p["mu"]), float(p["sigma"])), p)
    if g == "choice":
        return rng.choices(p["values"], weights=p.get("weights"))[0]
    if g == "boolean":
        return rng.random() < float(p.get("p_true", 0.5))
    if g == "date":
        start, end = _iso_date(p["start"]), _iso_date(p["end"])
        return (start + timedelta(days=rng.randint(0, (end - start).days))).strftime(
            str(p.get("format", DATE_FORMATS[0]))
        )
    if g == "datetime":
        start_dt, end_dt = _iso_datetime(p["start"]), _iso_datetime(p["end"])
        seconds = rng.randint(0, int((end_dt - start_dt).total_seconds()))
        return (start_dt + timedelta(seconds=seconds)).isoformat()
    if g == "pattern":
        return _pattern(str(p["template"]), rng)
    if g == "text":
        fake.seed_instance(rng.getrandbits(64))
        words = rng.randint(int(p.get("words_min", 3)), int(p.get("words_max", 12)))
        return str(fake.sentence(nb_words=words, variable_nb_words=False))
    if g == "entity":
        # A row has no original values, only templates, so there is no record to declare
        # (contract §0, amendment 2026-10-12): the row's persona is the anchor's own.
        entity = str(p["entity_type"])
        template = str(p.get("format") or ENTITY_TEMPLATES[entity])
        return surrogates.surrogate(entity, template, locale, "row")
    return p.get("value")


_OPS: dict[str, Callable[[Any, Any], bool]] = {
    "lt": operator.lt,
    "le": operator.le,
    "gt": operator.gt,
    "ge": operator.ge,
    "eq": operator.eq,
    "ne": operator.ne,
}


def _sort_key(value: Any) -> Any:
    """Datetimes compare as instants (offsets may differ); everything else as itself."""
    if isinstance(value, str) and "T" in value:
        return _iso_datetime(value)
    return value


def _apply_constraints(
    values: dict[str, Any], constraints: list[OrderConstraint | RuleConstraint]
) -> None:
    for constraint in constraints:
        if isinstance(constraint, OrderConstraint):
            present = [n for n in constraint.fields if values.get(n) is not None]
            ordered = sorted((values[n] for n in present), key=_sort_key)
            for name, value in zip(present, ordered, strict=True):
                values[name] = value
            continue
        result: Any = constraint.otherwise
        for case in constraint.when:
            current = values.get(case.field)
            if current is None:
                continue
            try:
                matched = _OPS[case.op](current, case.value)
            except TypeError:
                matched = False
            if matched:
                result = case.then
                break
        values[constraint.field] = result


def _nest(values: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name, value in values.items():
        node = out
        *parents, leaf = name.split(".")
        for part in parents:
            node = node.setdefault(part, {})
        node[leaf] = value
    return out


def _row_seed(seed: bytes, spec_hash: str, row: int) -> bytes:
    return hmac.new(seed, f"dps.schema|{spec_hash}|row|{row}".encode(), hashlib.sha256).digest()


class _Steering:
    """What ``execute_spec(avoid=...)`` steers (``STEERING``) and what it has spent.

    A field a ``rule`` constraint writes is not steered: the rule overwrites it in every row, so
    its generated values never reach the output. Once a value is kept although it holds a sample
    value, the sweep will refuse the run, so nothing is steered any more (``lost``).
    """

    def __init__(self, spec: GeneratorSpec, avoid: Callable[[Any], bool] | None) -> None:
        rewritten = {c.field for c in spec.constraints if isinstance(c, RuleConstraint)}
        self.avoid = avoid
        self.how: dict[str, str] = (
            {}
            if avoid is None
            else {
                f.name: STEERING[f.generator]
                for f in spec.fields
                if f.generator in STEERING and f.name not in rewritten
            }
        )
        self.offsets: dict[str, int] = {}  # sequence steps skipped so far, per field
        self.redraws = 0
        self.lost = False

    def holds(self, field: FieldSpec, value: Any) -> bool:
        """True when ``field`` is steered and ``value`` holds a sample value."""
        return (
            self.avoid is not None
            and not self.lost
            and field.name in self.how
            and value is not None
            and self.avoid(value)
        )


def _skip(field: FieldSpec, row: int, value: Any, steer: _Steering) -> Any:
    """A sequence value that holds a sample value is skipped: the sequence moves on (and every
    later row with it), so it stays unique and ascending."""
    offset = steer.offsets.get(field.name, 0)
    skipped = 0
    while steer.holds(field, value):
        if skipped == MAX_SAMPLE_REDRAWS:
            steer.lost = True
            break
        skipped += 1
        value = _sequence(field.params, row + offset + skipped)
    steer.offsets[field.name] = offset + skipped
    steer.redraws += skipped
    return value


def _new_persona(
    spec: GeneratorSpec,
    row: int,
    row_seed: bytes,
    values: dict[str, Any],
    seen: dict[str, set[str]],
    steer: _Steering,
    fake: Faker,
) -> None:
    """When an ``entity`` value of the row holds a sample value, every entity value of the row is
    drawn again from a new persona (``persona|N`` under the row seed), so the row's name, e-mail
    and birth date still agree; drawing one field again would take another persona's e-mail.
    Rows whose entity values hold none are left as they are."""
    fields = [f for f in spec.fields if f.generator == "entity" and values[f.name] is not None]
    if not any(steer.holds(f, values[f.name]) for f in fields):
        return
    for attempt in range(1, MAX_SAMPLE_REDRAWS + 1):
        steer.redraws += 1
        persona_seed = hmac.new(row_seed, f"persona|{attempt}".encode(), hashlib.sha256).digest()
        engine = SurrogateEngine(persona_seed, default_locale=spec.locale)
        fresh = {
            f.name: _generate(
                f,
                row,
                random.Random(seed_int(persona_seed, f"field|{f.name}")),
                engine,
                fake,
                spec.locale,
            )
            for f in fields
        }
        if any(f.unique and json.dumps(fresh[f.name], default=str) in seen[f.name] for f in fields):
            continue
        if not any(steer.holds(f, fresh[f.name]) for f in fields):
            values.update(fresh)
            return
    steer.lost = True


def execute_spec(
    spec: GeneratorSpec,
    *,
    rows: int,
    seed: bytes,
    avoid: Callable[[Any], bool] | None = None,
) -> SchemaResult:
    """Generate ``rows`` rows. Deterministic in (spec, seed, rows, avoid); CPU-bound (run in a
    thread).

    ``avoid`` (synthesis from a sample: ``schema_profile.sample_guard``) is true for a value that
    holds one of the sample's sensitive values; the executor steers around such a value as
    ``STEERING`` says, up to ``MAX_SAMPLE_REDRAWS`` new draws, so an ID, a date or an e-mail that
    happens to hold a sampled one is replaced before the sample sweep sees it. A value still
    holding one after that is kept and the sweep refuses the run. ``sample_redraws`` counts the
    draws steering discarded. Without ``avoid`` the rows are exactly those of the spec and seed
    alone; with it, a row differs from them only where it held a sample value (and a skipping
    sequence, from there on).
    """
    if rows < 1:
        raise SchemaError("invalid_rows", "rows must be at least 1")
    spec_hash = spec.spec_hash()
    fake = Faker(normalise_locale(spec.locale))
    seen: dict[str, set[str]] = {f.name: set() for f in spec.fields if f.unique}
    used: Counter[str] = Counter()
    nulls = retries = 0
    steer = _Steering(spec, avoid)
    out: list[dict[str, Any]] = []
    for row in range(rows):
        row_seed = _row_seed(seed, spec_hash, row)
        surrogates = SurrogateEngine(row_seed, default_locale=spec.locale)
        values: dict[str, Any] = {}
        for field in spec.fields:
            rng = random.Random(seed_int(row_seed, f"field|{field.name}"))
            if field.null_rate and not field.unique and rng.random() < field.null_rate:
                values[field.name] = None
                nulls += 1
                continue
            how = steer.how.get(field.name)
            value = _generate(
                field, row + steer.offsets.get(field.name, 0), rng, surrogates, fake, spec.locale
            )
            if how == "skip":
                value = _skip(field, row, value, steer)
            attempt = duplicates = redrawn = 0
            while True:
                if field.unique and json.dumps(value, default=str) in seen[field.name]:
                    duplicates += 1
                    retries += 1
                    if duplicates > MAX_UNIQUE_RETRIES:
                        raise SchemaError(
                            "unique_exhausted",
                            f"field {field.name!r} cannot produce {rows} unique values",
                        )
                elif how == "redraw" and steer.holds(field, value):
                    if redrawn == MAX_SAMPLE_REDRAWS:
                        steer.lost = True
                        break
                    redrawn += 1
                else:
                    break
                attempt += 1
                retry = hmac.new(
                    row_seed, f"retry|{field.name}|{attempt}".encode(), hashlib.sha256
                ).digest()
                value = _generate(
                    field,
                    row,
                    random.Random(seed_int(retry, "field")),
                    SurrogateEngine(retry, default_locale=spec.locale),
                    fake,
                    spec.locale,
                )
            steer.redraws += redrawn
            values[field.name] = value
            used[field.generator] += 1
        if steer.how and not steer.lost:
            _new_persona(spec, row, row_seed, values, seen, steer, fake)
        for name, taken in seen.items():
            taken.add(json.dumps(values[name], default=str))
        _apply_constraints(values, spec.constraints)
        out.append(_nest(values))
    return SchemaResult(
        rows=out,
        spec_hash=spec_hash,
        generators_used=dict(used),
        nulls=nulls,
        unique_retries=retries,
        sample_redraws=steer.redraws,
    )


def _flat(row: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in row.items():
        if isinstance(value, dict):
            out.update(_flat(value, f"{prefix}{key}."))
        else:
            out[f"{prefix}{key}"] = value
    return out


def rows_to_csv(rows: list[dict[str, Any]], spec: GeneratorSpec) -> str:
    """CSV with one column per spec field (dotted names for nested fields); null → empty cell."""
    header = [f.name for f in spec.fields]
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(header)
    for row in rows:
        flat = _flat(row)
        writer.writerow(["" if flat.get(h) is None else flat.get(h) for h in header])
    return buf.getvalue()
