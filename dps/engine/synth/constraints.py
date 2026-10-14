"""Constraints applied to synthetic tabular rows after sampling (spec §5.6).

Order per row: ``unique`` (a keyed Feistel permutation of the row's integer key — unique by
construction, deterministic, resumable), then ``min``/``max``/``positive`` (clip), then ``when``
rules (first match wins). Rules come from ``policy.synthesize.constraints``; their literals are
configuration, not customer data, but errors still name columns and rules only.
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from dps.engine.metrics import canonical, to_float
from dps.engine.policy import TabularConstraint, WhenCondition

MIN_UNIQUE_DIGITS = 6
DEFAULT_UNIQUE_DIGITS = 15
_NUMERIC = frozenset({"int", "decimal", "float"})
_ORDERED = _NUMERIC | {"date", "timestamp"}
_FEISTEL_ROUNDS = 4
_SMALLEST_FLOAT = 1e-9


class ConstraintError(ValueError):
    """A constraint cannot be applied (incompatible column, key or shape); value-free."""


@dataclass(frozen=True)
class ColumnSpec:
    """What a constraint needs to know about an output column (no values).

    ``precision`` is the number of decimal digits the column holds (``numeric(p, s)``; for an
    integer type the caller passes the digits that fit it: 4 for ``smallint``, 9 for
    ``integer``, 18 for ``bigint``); ``shape`` is a value shape (``XXX-99999999``, contract §5.4).
    """

    name: str
    kind: str
    precision: int | None = None
    scale: int | None = None
    shape: str | None = None


def feistel_permute(x: int, domain: int, keys: Sequence[bytes]) -> int:
    """A keyed bijection on ``range(domain)``.

    A balanced Feistel network (HMAC-SHA256 round function, one round per key) over the smallest
    even bit width covering ``domain``, cycle-walked back into range (fewer than 4 walks on
    average). The same keys always give the same permutation.
    """
    if domain < 2 or not 0 <= x < domain:
        raise ConstraintError("value outside the permutation domain")
    bits = max(2, (domain - 1).bit_length())
    bits += bits % 2
    half = bits // 2
    mask = (1 << half) - 1
    width = (half + 7) // 8 or 1
    y = x
    while True:
        left, right = y >> half, y & mask
        for key in keys:
            mixed = hmac.new(key, right.to_bytes(width, "big"), hashlib.sha256).digest()
            left, right = right, left ^ (int.from_bytes(mixed[:16], "big") & mask)
        y = (left << half) | right
        if y < domain:
            return y


def _coerce(value: Any, spec: ColumnSpec) -> Any:
    """``value`` (a policy literal or a sampled value) in the column's Python type."""
    if value is None:
        return None
    if spec.kind == "int":
        return int(value)
    if spec.kind == "decimal":
        number = Decimal(str(value))
        if spec.scale is not None:
            return number.quantize(Decimal(1).scaleb(-spec.scale))
        return number
    if spec.kind == "float":
        return float(value)
    if spec.kind == "date":
        if isinstance(value, date) and not isinstance(value, datetime):
            return value
        return date.fromisoformat(str(value))
    if spec.kind == "timestamp":
        return value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    if spec.kind == "bool":
        return value if isinstance(value, bool) else str(value).lower() in ("true", "1", "yes")
    return str(value)


def _comparable(actual: Any, expected: Any) -> tuple[float, float] | None:
    if isinstance(expected, str) and isinstance(actual, date):
        try:
            if isinstance(actual, datetime):
                expected = datetime.fromisoformat(expected)
            else:
                expected = date.fromisoformat(expected)
        except ValueError:
            return None
    a, b = to_float(actual), to_float(expected)
    return None if a is None or b is None else (a, b)


def _matches(cond: WhenCondition, row: Mapping[str, Any]) -> bool:
    actual = row.get(cond.path[2:])
    if cond.op == "is_null":
        return actual is None
    if cond.op == "not_null":
        return actual is not None
    if actual is None:
        return False
    if cond.op in ("in", "not_in"):
        options = cond.value if isinstance(cond.value, list) else [cond.value]
        hit = any(canonical(actual) == canonical(o) for o in options)
        return hit if cond.op == "in" else not hit
    if cond.op == "==":
        return canonical(actual) == canonical(cond.value)
    if cond.op == "!=":
        return canonical(actual) != canonical(cond.value)
    pair = _comparable(actual, cond.value)
    if pair is None:
        return False
    a, b = pair
    return {"<": a < b, "<=": a <= b, ">": a > b, ">=": a >= b}[cond.op]


def _smallest_positive(spec: ColumnSpec) -> Any:
    if spec.kind == "int":
        return 1
    if spec.kind == "decimal":
        return Decimal(1).scaleb(-(spec.scale or 0))
    return _SMALLEST_FLOAT


@dataclass
class _Unique:
    spec: ColumnSpec
    domain: int
    keys: list[bytes]
    template: str | None  # text shape with letters fixed, '9' = digit slot


@dataclass
class _Range:
    spec: ColumnSpec
    lo: Any
    hi: Any
    positive: bool


class ConstraintSet:
    """The constraints of one policy that apply to one table, ready to run per row."""

    def __init__(
        self,
        constraints: Sequence[TabularConstraint],
        columns: Mapping[str, ColumnSpec],
        *,
        seed: bytes,
        object_fqn: str,
    ) -> None:
        """Select the constraints for ``object_fqn`` and check them against ``columns``.

        Raises:
            ConstraintError: a rule the column cannot take (a range on text, a unique rule
                without enough digit positions, a literal that does not fit the column kind).
        """
        self._unique: list[_Unique] = []
        self._ranges: list[_Range] = []
        self._rules: list[tuple[ColumnSpec, list[tuple[WhenCondition, Any]]]] = []
        self._described: list[dict[str, str]] = []
        self.skipped: list[dict[str, str]] = []
        for c in constraints:
            name = c.path[2:]
            if c.object and c.object.upper() != object_fqn.upper():
                self.skipped.append({"path": c.path, "reason": "other_object"})
                continue
            spec = columns.get(name)
            if spec is None:
                self.skipped.append({"path": c.path, "reason": "no_column"})
                continue
            if any(rule.if_.path[2:] not in columns for rule in c.when):
                # a condition on a column this table lacks would always read as null
                self.skipped.append({"path": c.path, "reason": "no_condition_column"})
                continue
            if c.unique:
                self._unique.append(self._build_unique(spec, seed))
                self._described.append({"path": c.path, "rule": "unique"})
            elif c.min is not None or c.max is not None or c.positive:
                if spec.kind not in _ORDERED or (c.positive and spec.kind not in _NUMERIC):
                    raise ConstraintError(f"range rule on {c.path} needs a numeric or date column")
                try:
                    lo, hi = _coerce(c.min, spec), _coerce(c.max, spec)
                except (ValueError, TypeError, InvalidOperation):
                    raise ConstraintError(
                        f"range rule on {c.path}: min/max does not fit the column kind"
                    ) from None
                self._ranges.append(_Range(spec, lo, hi, c.positive))
                only_positive = c.positive and c.min is None and c.max is None
                self._described.append(
                    {"path": c.path, "rule": "positive" if only_positive else "range"}
                )
            if c.when:
                try:
                    rules = [(rule.if_, _coerce(rule.then, spec)) for rule in c.when]
                except (ValueError, TypeError, InvalidOperation):
                    raise ConstraintError(
                        f"when rule on {c.path}: a 'then' value does not fit the column kind"
                    ) from None
                self._rules.append((spec, rules))
                self._described.append({"path": c.path, "rule": "when"})

    @staticmethod
    def _build_unique(spec: ColumnSpec, seed: bytes) -> _Unique:
        keys = [
            hmac.new(seed, f"dps.unique|{spec.name}|{r}".encode(), hashlib.sha256).digest()
            for r in range(_FEISTEL_ROUNDS)
        ]
        if spec.kind == "int" or (spec.kind == "decimal" and not spec.scale):
            if spec.precision is not None and spec.precision < MIN_UNIQUE_DIGITS:
                raise ConstraintError(
                    f"unique rule on $.{spec.name} needs a column of at least "
                    f"{MIN_UNIQUE_DIGITS} digits"
                )
            digits = spec.precision or DEFAULT_UNIQUE_DIGITS
            return _Unique(spec, 10**digits, keys, None)
        if spec.kind == "categorical":
            shape = spec.shape or ""
            slots = shape.count("9")
            if slots < MIN_UNIQUE_DIGITS:
                raise ConstraintError(
                    f"unique rule on $.{spec.name} needs a value shape with at least "
                    f"{MIN_UNIQUE_DIGITS} digit positions"
                )
            letters = hmac.new(
                seed, f"dps.unique.letters|{spec.name}".encode(), hashlib.sha256
            ).digest()
            out: list[str] = []
            i = 0
            for ch in shape:
                if ch in "Xx":
                    letter = chr(ord("A") + letters[i % len(letters)] % 26)
                    out.append(letter if ch == "X" else letter.lower())
                    i += 1
                else:
                    out.append(ch)
            return _Unique(spec, 10**slots, keys, "".join(out))
        raise ConstraintError(f"unique rule on $.{spec.name} needs an integer or text column")

    @property
    def needs_key_int(self) -> bool:
        """True when a ``unique`` rule needs each row's integer key."""
        return bool(self._unique)

    def describe(self) -> list[dict[str, str]]:
        """Value-free summary for the evidence pack: ``[{"path", "rule"}]``."""
        return list(self._described)

    def apply(self, row: dict[str, Any], *, key_int: int | None) -> None:
        """Enforce every constraint on ``row`` (column name → value), in place.

        Raises:
            ConstraintError: a ``unique`` rule without an integer key, or a key outside the
                rule's domain (value-free).
        """
        for u in self._unique:
            if key_int is None:
                raise ConstraintError(
                    f"unique rule on $.{u.spec.name} needs a single integer key column"
                )
            if not 0 <= key_int < u.domain:
                raise ConstraintError(
                    f"unique rule on $.{u.spec.name}: the key is outside its domain"
                )
            n = feistel_permute(key_int, u.domain, u.keys)
            if u.template is None:
                row[u.spec.name] = _coerce(n, u.spec)
            else:
                digits = iter(str(n).zfill(u.template.count("9")))
                row[u.spec.name] = "".join(next(digits) if ch == "9" else ch for ch in u.template)
        for r in self._ranges:
            name = r.spec.name
            x = to_float(row.get(name))
            if x is None:
                continue
            lo, hi = to_float(r.lo), to_float(r.hi)
            if lo is not None and x < lo:
                row[name] = r.lo
            elif hi is not None and x > hi:
                row[name] = r.hi
            current = to_float(row[name])
            if r.positive and current is not None and current <= 0:
                row[name] = _smallest_positive(r.spec)
            if r.spec.kind == "decimal":
                row[name] = _coerce(row[name], r.spec)
        for spec, rules in self._rules:
            for condition, then in rules:
                if _matches(condition, row):
                    row[spec.name] = then
                    break
