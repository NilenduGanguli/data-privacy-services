"""The L2/L3 → engine bridge (contract §6.4): rows in, transformed rows out, through ``apply_plan``.

Pure CPU: callers run :meth:`BatchTransformer.transform` in ``asyncio.to_thread``. Whole-value
findings come from the accepted snapshot (detector ``"snapshot"``); free-text columns are scanned
per row and planned with the column's method (obfuscate: ``det_token`` envelopes, so a reveal needs
no stored hints) or with ``build_plan`` (synthesize). A new surrogate engine is made per batch,
which bounds its persona cache.

**Anchors** (contract §6.2): each row's raw anchor is ``<ENTITY>:<hash>`` of the snapshot's anchor
column — the string M0's ``anchor_for`` builds for the same entity, so L1 and L2 agree on date
shifts and personas for the same customer — else ``row:<hash(key)>``. ``apply_plan`` keys it
before use whenever key material is present.

**Verification per row** (deviation from "per batch", see the M2 plan). Each row is checked
against its own transformed outputs only (kept and unplanned columns are never searched):

* ``fixed_point`` — a whole value of at least 4 normalised characters came out unchanged;
* ``contained`` — an original of at least 8 normalised characters appears in any of the row's
  transformed outputs (cross-field leakage, e.g. a name copied into free text nobody detected);
* ``name_part`` — a part (at least 8 characters) of a transformed name appears in an output more
  often than the same column's input held it outside the name (M0's name-part rule at the
  cross-field length, by the same volume reasoning).

A whole-batch sweep would flag a short original inside another row's realistic surrogate or FPE
output and fail healthy jobs at L2 volumes. Under ``strict`` any hit raises :class:`LeakageError`
and the batch is not written. Errors and hits carry paths, entity types and counts — never values.

**Document rows** (M3, MongoDB). A column whose path is not one key of a flat row
(``$.profile.fullName``, ``$.accounts[*].iban``) is read and written at every concrete path it
addresses in the row (``[*]`` expanded per document); the engine sees those leaves at their
concrete paths (the paths L1 uses for the same JSON document). Values no plan covers are never
converted: they leave as the driver's objects (``Int64``, ``Decimal128``, ``ObjectId``,
``datetime``), by reference, and the input rows are never changed (each container on a written
path is shallow-copied once). A leaf a plan *wrote* keeps its original leaf's type when the caller
passes ``leaf_restore`` (MongoDB: ``mongo_docs.restore_leaf``, so an ``Int64`` stays an ``Int64``
and an int32 stays an int32); a leaf that held text keeps the engine's text (a value the discovery
sample saw in a numeric field but which is a string here is not turned into a number).
Leaves whose generalised path the shape does not know (fields the discovery sample never saw) follow
``unclassified``: ``detect`` runs the detector over string leaves and replaces the spans
(``det_token`` envelopes in obfuscate mode), ``redact`` replaces every such scalar with the
``redact`` method's label, ``keep`` leaves them. Flat rows (PostgreSQL, Oracle) are unchanged.

**Reversible outputs survive the column** (contract §6.5, amendment 2026-10-12). ``fpe_digits``
plans default to ``leading_zero: preserve`` (:class:`ColumnPlan`, like every plan entry), and an
integer, decimal or float column refuses any other setting (``incompatible_method``): a token
there that starts with ``0`` would be stored without that digit and reveal would return another
number. Any reversible output the target column would store differently (a dropped leading zero,
a decimal re-scaled to the column's scale) fails the batch with ``incompatible_method`` instead
of being written.
"""

from __future__ import annotations

import copy
import dataclasses
import json
from collections import Counter
from collections.abc import Callable
from typing import Any, Literal, NamedTuple, Protocol, cast

from pydantic import BaseModel, ConfigDict, Field, model_validator

from dps.engine.findings import (
    Finding,
    Locator,
    Plan,
    PlanEntry,
    hash_value,
    with_plan_defaults,
)
from dps.engine.jsonpath import child_path, flatten, get_path, parse_path
from dps.engine.planner import build_plan
from dps.engine.policy import MethodSpec, Policy
from dps.engine.service import PrivacyEngine
from dps.engine.synth.surrogates import SynthesisSeed
from dps.engine.taxonomy import entity_info, record_sensitivity
from dps.engine.transform.apply import (
    ApplyOptions,
    RevealHint,
    _keyed_anchor,
    apply_plan,
    reveal,
)
from dps.engine.transform.crypto import KeyMaterial, key_check_value
from dps.engine.transform.methods import ISO_DATE, ISO_DATETIME, METHODS, MethodError
from dps.engine.verify import NAME_ENTITY, Verification, name_parts, normalise
from dps.layers.connectors.base import (
    ColumnInfo,
    ColumnKind,
    IncompatibleValue,
    Row,
    TableShape,
    from_engine_text,
    to_engine_text,
)
from dps.layers.connectors.docpaths import expand_wildcards, generalise, top_level
from dps.observability import FINDINGS, TRANSFORMS, VERIFY_FAIL

#: Methods that need no key material (usable in synthesize mode).
KEYLESS_METHODS = frozenset(
    {
        "surrogate",
        "keep",
        "null",
        "redact",
        "bucket",
        "generalize",
        "mask_partial",
        "dictionary",
        "noise",
    }
)
#: Row country code → surrogate locale (``policy.locale.by_country_field``).
LOCALE_BY_COUNTRY = {
    "US": "en_US",
    "IN": "en_IN",
    "GB": "en_GB",
    "UK": "en_GB",
    "CA": "en_CA",
    "MX": "es_MX",
}
_FPE = frozenset({"fpe_digits", "fpe_text"})
#: Output kinds whose values are numbers: an ``fpe_digits`` token must keep its leading digit.
NUMERIC_KINDS = frozenset({"int", "decimal", "float"})
#: Minimum normalised lengths: an unchanged whole value, and a value (or a name part) found
#: elsewhere in the row's outputs.
EXACT_MIN, CONTAIN_MIN = 4, 8
_MAX_HITS = 50
_HIDDEN = ConfigDict(hide_input_in_errors=True)


class _Unit(NamedTuple):
    """One planned value of a row: the plan, the concrete path, the output codec column."""

    plan: ColumnPlan
    path: str
    out: ColumnInfo


#: Plan entries of unclassified document leaves use these (contract §5.3 ``unclassified``).
UNCLASSIFIED_ENTITY = "UNCLASSIFIED"
_UNCLASSIFIED_TEXT = ColumnInfo(path="$", name="", data_type="", kind="text")


def _put(doc: dict[str, Any], path: str, value: Any) -> None:
    """Set ``path`` in ``doc``, creating containers on the way (array slots padded with None)."""
    parts = parse_path(path)
    node: Any = doc
    for part, nxt in zip(parts, parts[1:], strict=False):
        empty: Any = [] if isinstance(nxt, int) else {}
        if isinstance(part, int):
            while len(node) <= part:
                node.append(None)
            if node[part] is None:
                node[part] = empty
        elif part not in node:
            node[part] = empty
        node = node[part]
    last = parts[-1]
    if isinstance(last, int):
        while len(node) <= last:
            node.append(None)
    node[last] = value


def _set_copied(root: dict[str, Any], path: str, value: Any, fresh: set[int]) -> None:
    """Set ``path`` in ``root``, shallow-copying each container on the way the first time it is
    written: the input row is never changed, untouched values keep their identity."""
    parts = parse_path(path)
    node: Any = root
    for part in parts[:-1]:
        child = node[part]
        if id(child) not in fresh:
            child = copy.copy(child)
            node[part] = child
            fresh.add(id(child))
        node = child
    node[parts[-1]] = value


def _never_transformed(value: Any) -> bool:
    """ObjectIds and binary values are never transformed (contract §4.1), even unclassified."""
    if isinstance(value, bytes | bytearray | memoryview):
        return True
    return type(value).__name__ in ("ObjectId", "Binary", "Regex", "Code", "MinKey", "MaxKey")


class TabularSampler(Protocol):
    """M5's per-row tabular sampler (contract §6.6); M2 always passes ``None``."""

    def sample_row(self, anchor: str) -> dict[str, Any]: ...


class ColumnPlan(BaseModel):
    """One planned column, built once per job from the accepted snapshot + resolved policy.

    ``kind``, ``length``, ``precision``, ``scale`` and ``ts_precision`` describe the **output**
    (target) column.
    """

    model_config = _HIDDEN
    path: str
    name: str
    entity_type: str
    method: MethodSpec
    text_column: bool
    reversible: bool
    kind: str
    length: int | None
    precision: int | None = None
    scale: int | None = None
    #: fractional-second digits of a timestamp output (M3: Oracle TIMESTAMP(7-9) travels as FF9
    #: text, so a date shift keeps its nanoseconds and its reveal stays exact)
    ts_precision: int | None = None

    @model_validator(mode="after")
    def _plan_defaults(self) -> ColumnPlan:
        # a new MethodSpec: the snapshot's or the policy's object is never changed
        params = with_plan_defaults(self.method.method, self.method.params)
        if params != self.method.params:
            self.method = self.method.model_copy(update={"params": params})
        return self

    def output_column(self) -> ColumnInfo:
        """The target column as the value codec sees it."""
        return ColumnInfo(
            path=self.path,
            name=self.name,
            data_type="",
            kind=cast(ColumnKind, self.kind),
            length=self.length,
            precision=self.precision,
            scale=self.scale,
            ts_precision=self.ts_precision,
        )


class BatchResult(BaseModel):
    """Transformed rows plus value-free counters for the job and the evidence pack."""

    model_config = _HIDDEN
    rows: list[Row]
    values_by_method: dict[str, int]
    fallbacks: dict[str, int]
    verification: Verification
    skipped: int = 0
    values_by_column: dict[str, int] = Field(default_factory=dict)


class LeakageError(Exception):
    """The per-row sweep found originals in the output under ``strict``; nothing is written."""

    code = "leakage"

    def __init__(self, hits: int) -> None:
        super().__init__(
            f"leakage: {hits} original value(s) found in the transformed batch; nothing was written"
        )
        self.hits = hits


class IncompatibleMethod(Exception):
    """A method's output cannot be stored in its column (``incompatible_method`` /
    ``length_overflow``); names the path, never the value."""

    def __init__(self, path: str, code: str) -> None:
        super().__init__(f"{code}: the method output for {path} does not fit the target column")
        self.path, self.code = path, code


def row_anchor(row: Row, shape: TableShape, anchor: tuple[str, str] | None) -> str:
    """Contract §6.2: ``<ENTITY>:<hash>`` of the anchor column (the L1 formula), else
    ``row:<hash(canonical key text)>``."""
    if anchor is not None:
        name, entity = anchor
        value = to_engine_text(row.get(name), shape.column(name))
        if value:
            return f"{entity}:{hash_value(value)}"
    key_text = "|".join(to_engine_text(row.get(k), shape.column(k)) or "" for k in shape.key)
    return f"row:{hash_value(key_text)}"


def _outside(text: str, names: list[str]) -> list[str]:
    """The pieces of ``text`` around every occurrence of ``names`` (longest first)."""
    pieces = [text]
    for name in sorted({n for n in names if n}, key=len, reverse=True):
        pieces = [part for piece in pieces for part in piece.split(name)]
    return pieces


def verify_row(
    originals: list[tuple[ColumnPlan, str, bool]],
    outputs: dict[str, str | None],
    *,
    inputs: dict[str, str] | None = None,
) -> list[dict[str, str]]:
    """Per-row leakage sweep (module docstring): fixed points, containment, name parts.

    ``originals`` holds ``(column, original text, whole value?)`` for every transformed value of
    the row (for a free-text span, ``column`` carries the span's entity type); ``outputs`` maps the
    path of every planned, non-kept column to its transformed text. ``inputs`` (the same paths'
    input texts) enables the name-part rule. Hits are ``{"path", "entity_type", "rule"}``, ``path``
    being where in the output the original was found, at most one per path and entity type.
    """
    hays = {path: normalise(text) for path, text in outputs.items() if text}
    hits: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def hit(path: str, entity: str, rule: str) -> None:
        if (path, entity) not in seen:
            seen.add((path, entity))
            hits.append({"path": path, "entity_type": entity, "rule": rule})

    for column, original, whole in originals:
        needle = normalise(original)
        if whole and len(needle) >= EXACT_MIN and hays.get(column.path) == needle:
            hit(column.path, column.entity_type, "fixed_point")
        elif len(needle) >= CONTAIN_MIN:
            for path, hay in hays.items():
                if needle in hay:
                    hit(path, column.entity_type, "contained")
    if inputs is not None:
        names: dict[str, list[str]] = {}
        for column, original, _ in originals:
            if column.entity_type == NAME_ENTITY:
                names.setdefault(column.path, []).append(original)
        parts = name_parts([n for group in names.values() for n in group], CONTAIN_MIN)
        for path, hay in hays.items():
            held = [normalise(p) for p in _outside(inputs.get(path, ""), names.get(path, []))]
            if any(hay.count(part) > sum(h.count(part) for h in held) for part in parts):
                hit(path, NAME_ENTITY, "name_part")
    return hits


def _checked(originals: list[tuple[ColumnPlan, str, bool]]) -> int:
    count = 0
    for _, original, whole in originals:
        size = len(normalise(original))
        if size >= CONTAIN_MIN or (whole and size >= EXACT_MIN):
            count += 1
    return count


class BatchTransformer:
    """Transforms batches of rows of one table for one job (contract §6.4)."""

    def __init__(
        self,
        engine: PrivacyEngine,
        *,
        policy: Policy,
        mode: Literal["obfuscate", "synthesize"],
        columns: list[ColumnPlan],
        shape: TableShape,
        key: KeyMaterial | None,
        seed: SynthesisSeed | None,
        locale: str,
        unclassified: str = "detect",
        tabular: TabularSampler | None = None,
        anchor: tuple[str, str] | None = None,
        verify: Literal["strict", "warn"] = "strict",
        leaf_restore: Callable[[Any, Any], Any] | None = None,
    ) -> None:
        """Validate the mode's prerequisites and the plans against ``shape``.

        ``leaf_restore(original, written)`` (document sources) returns a written value in its
        original leaf's driver type; it is called only for values a plan wrote.

        Raises:
            ValueError: obfuscate without key material, synthesize without a seed, a plan (or
                the anchor) naming a column the table does not have, or a plan whose method
                params lack their plan defaults (copied around :class:`ColumnPlan`'s validator).
            IncompatibleMethod: ``incompatible_method`` for a synthesize-mode method that needs
                key material, or ``fpe_digits`` without ``leading_zero: preserve`` on a numeric
                column.
        """
        if mode == "synthesize":
            if seed is None:
                raise ValueError("synthesize mode needs a synthesis seed")
            bad = [
                c.path
                for c in columns
                if c.method.method not in KEYLESS_METHODS and not c.text_column
            ]
            if bad:
                raise IncompatibleMethod(bad[0], "incompatible_method")
        elif key is None:
            raise ValueError("obfuscate mode needs key material")
        if any(
            c.method.params != with_plan_defaults(c.method.method, c.method.params) for c in columns
        ):
            # a plan copied around its validator: the reveal map would not show what ran
            raise ValueError("a column plan lacks its method defaults; build it with ColumnPlan()")
        lossy = [
            c.path
            for c in columns
            if c.method.method == "fpe_digits"
            and c.kind in NUMERIC_KINDS
            and c.method.params.get("leading_zero") != "preserve"
        ]
        if lossy:
            raise IncompatibleMethod(lossy[0], "incompatible_method")
        known = {c.name for c in shape.columns}
        if any(c.name not in known for c in columns) or (anchor and anchor[0] not in known):
            raise ValueError("a column plan names a column the table does not have")
        self.by_path = {c.path: c for c in shape.columns}
        if any(not top_level(c.output_column()) and c.path not in self.by_path for c in columns):
            raise ValueError("a column plan names a document path the table does not have")
        # top-level keys that are columns of their own (every SQL column, flat Mongo fields).
        # SQL rows: never searched for unclassified leaves (a jsonb value is its column's). Document
        # rows (a leaf-restoring source, or a shape with nested paths): a flat field is searched
        # unless a plan covers it whole, so a field the sample saw as a scalar but a later
        # document holds as a sub-document or an array never passes its nested values in clear
        # (review G3 r3 B1); a scalar under that name is the shape's own path and stays skipped.
        self.flat_names = {c.name for c in shape.columns if top_level(c)}
        self.documents = leaf_restore is not None or not all(top_level(c) for c in shape.columns)
        self.planned_flat = {c.name for c in columns if top_level(c.output_column())}
        self.engine, self.policy, self.mode = engine, policy, mode
        self.shape, self.key, self.seed, self.locale = shape, key, seed, locale
        self.anchor, self.verify_mode = anchor, verify
        self.unclassified, self.tabular = unclassified, tabular
        self.leaf_restore = leaf_restore
        self.whole = [c for c in columns if not c.text_column]
        self.text = [c for c in columns if c.text_column]
        self.detector = engine.detector(policy, locale) if self.text else None
        self.out_cols = {c.name: c.output_column() for c in columns if top_level(c.output_column())}

    def _entry(self, finding: Finding, spec: MethodSpec) -> PlanEntry:
        method, params, reason = spec.method, dict(spec.params), "snapshot"
        if method in _FPE and not self.engine.fpe_enabled:
            method, params, reason = "det_token", {}, "snapshot|fallback:fpe_disabled"
        return PlanEntry(
            finding=finding,
            method=method,
            params=params,
            reason=reason,
            reversible=METHODS[method].reversible,
        )

    def _locale(self, row: Row) -> str:
        field = self.policy.locale.by_country_field
        name = field.split(".")[-1] if field else None
        value = str(row.get(name) or "").strip().upper() if name else ""
        return LOCALE_BY_COUNTRY.get(value, self.locale)

    def _spans(
        self, col: ColumnPlan, text: str, findings_seen: Counter[tuple[str, str]]
    ) -> list[Finding]:
        if self.detector is None:
            return []
        found: list[Finding] = []
        for f in self.detector.detect(text=text).findings:
            start = f.locator.start if f.locator.start is not None else 0
            end = f.locator.end if f.locator.end is not None else len(text)
            if end <= start:
                continue
            # a text that is one whole value is still planned as a span (an envelope token)
            found.append(
                f.model_copy(update={"locator": Locator(path=col.path, start=start, end=end)})
            )
            for d in f.detectors:
                findings_seen[(f.entity_type, d.split(":")[0])] += 1
        return found

    @staticmethod
    def _column_key(col: ColumnPlan) -> str:
        """``values_by_column`` key: the column name (flat rows), the path (document paths)."""
        return col.name if top_level(col.output_column()) else col.path

    def _values(self, row: Row, col: ColumnPlan) -> list[tuple[_Unit, str]]:
        """The non-null values ``col`` addresses in ``row`` as engine text, with their paths."""
        out_col = col.output_column()
        if top_level(out_col):
            text = to_engine_text(row.get(col.name), self.shape.column(col.name))
            return [(_Unit(col, col.path, self.out_cols[col.name]), text)] if text else []
        source = self.by_path[col.path]
        found: list[tuple[_Unit, str]] = []
        for path in expand_wildcards(row, col.path):
            text = to_engine_text(get_path(row, path), source)
            if text:
                found.append((_Unit(col, path, out_col), text))
        return found

    def _plan_spans(
        self,
        col: ColumnPlan,
        text: str,
        found: list[Finding],
        originals: list[tuple[ColumnPlan, str, bool]],
    ) -> list[PlanEntry]:
        """Plan entries for the spans found in one text value (``col.path`` = where it is)."""
        if self.mode == "obfuscate":
            planned = [self._entry(f, col.method) for f in found]
        else:
            planned = build_plan(
                found, self.policy, "synthesize", fpe_enabled=self.engine.fpe_enabled
            ).entries
        for entry in planned:
            if entry.method == "keep":
                continue  # kept on purpose: neither transformed nor searched for
            loc = entry.finding.locator
            span = text[loc.start or 0 : loc.end]
            as_span = col.model_copy(update={"entity_type": entry.finding.entity_type})
            originals.append((as_span, span, False))
        return planned

    def _unclassified(
        self, row: Row, findings_seen: Counter[tuple[str, str]]
    ) -> list[tuple[_Unit, str, list[Finding]]]:
        """Document leaves outside the shape (``unclassified`` = ``detect`` or ``redact``).

        ``detect``: string leaves with their detector spans (leaves without a finding are
        skipped). ``redact``: every scalar leaf except booleans, ObjectIds and binary, with no
        spans (a whole-value ``redact``).
        """
        out: list[tuple[_Unit, str, list[Finding]]] = []
        for name, value in row.items():
            if name in self.flat_names and (not self.documents or name in self.planned_flat):
                continue
            for path, leaf in flatten(value, child_path("$", str(name))):
                if generalise(path) in self.by_path or leaf is None or isinstance(leaf, bool):
                    continue
                if self.unclassified == "detect":
                    if not isinstance(leaf, str) or not leaf:
                        continue
                    if self.detector is None:
                        self.detector = self.engine.detector(self.policy, self.locale)
                    plan = self._unclassified_plan(path, "det_token")
                    found = self._spans(plan, leaf, findings_seen)
                    if found:
                        out.append((_Unit(plan, path, self._text_out(path)), leaf, found))
                elif self.unclassified == "redact":
                    if _never_transformed(leaf):
                        continue
                    text = to_engine_text(leaf, _UNCLASSIFIED_TEXT)
                    if text:
                        plan = self._unclassified_plan(path, "redact")
                        out.append((_Unit(plan, path, self._text_out(path)), text, []))
        return out

    @staticmethod
    def _text_out(path: str) -> ColumnInfo:
        return _UNCLASSIFIED_TEXT.model_copy(update={"path": path})

    @staticmethod
    def _unclassified_plan(path: str, method: str) -> ColumnPlan:
        return ColumnPlan(
            path=path,
            name=str(parse_path(path)[0]),
            entity_type=UNCLASSIFIED_ENTITY,
            method=MethodSpec(method=method),
            text_column=method != "redact",
            reversible=method != "redact",
            kind="text",
            length=None,
        )

    def _stored(self, row: Row, unit: _Unit, result: str | None) -> Any:
        """The value written at ``unit.path``: the output column's codec, then (document rows) the
        original leaf's type — a leaf that held text keeps the engine's text."""
        column = unit.out
        original: Any = None
        if self.leaf_restore is not None and result is not None:
            try:
                original = get_path(row, unit.path)
            except (KeyError, IndexError, TypeError, ValueError):
                original = None
            if isinstance(original, str):
                return result
        try:
            stored = from_engine_text(result, column)
        except IncompatibleValue as exc:
            raise IncompatibleMethod(column.path, exc.code) from None
        if self.leaf_restore is not None and original is not None:
            stored = self.leaf_restore(original, stored)
        return stored

    def transform(self, rows: list[Row]) -> BatchResult:
        """Transform ``rows`` (not mutated); raises :class:`LeakageError` under ``strict``.

        Raises:
            LeakageError: an original survived in a row's output and verification is strict.
            IncompatibleMethod: a method's output does not fit its column.
            MethodError: a method prerequisite is missing (value-free message).
        """
        surrogates = (
            self.engine.surrogate_engine(self.seed, self.locale) if self.seed is not None else None
        )
        base = ApplyOptions(
            policy=self.policy,
            key=self.key,
            surrogates=surrogates,
            fpe_enabled=self.engine.fpe_enabled,
            cipher_factory=self.engine.cipher_factory,
            token_style="envelope",
            locale=self.locale,
        )
        out_rows: list[Row] = []
        methods: Counter[str] = Counter()
        notes: Counter[str] = Counter()
        per_column: Counter[str] = Counter()
        findings_seen: Counter[tuple[str, str]] = Counter()
        hits: list[dict[str, str]] = []
        checked = 0
        for row in rows:
            doc: dict[str, Any] = {}
            units: list[tuple[_Unit, str]] = []  # (unit, input text) of every non-null value
            for col in self.whole + self.text:
                for unit, text in self._values(row, col):
                    _put(doc, unit.path, text)
                    units.append((unit, text))
            entries: list[PlanEntry] = []
            originals: list[tuple[ColumnPlan, str, bool]] = []
            searched: list[tuple[_Unit, str]] = []
            for unit, text in units:
                col = unit.plan
                at = col if unit.path == col.path else col.model_copy(update={"path": unit.path})
                if not col.text_column:
                    if col.method.method == "keep":
                        continue
                    finding = Finding(
                        locator=Locator(path=unit.path),
                        entity_type=col.entity_type,
                        score=1.0,
                        detectors=["snapshot"],
                        sensitivity=entity_info(col.entity_type).sensitivity,
                        value_hash=hash_value(text),
                    )
                    entries.append(self._entry(finding, col.method))
                    originals.append((at, text, True))
                    searched.append((unit, text))
                    per_column[self._column_key(col)] += 1
                    continue
                searched.append((unit, text))  # free text is always searched, findings or not
                found = self._spans(at, text, findings_seen)
                per_column[self._column_key(col)] += len(found)
                entries += self._plan_spans(at, text, found, originals)
            if self.unclassified != "keep":
                for unit, text, found in self._unclassified(row, findings_seen):
                    _put(doc, unit.path, text)
                    searched.append((unit, text))
                    per_column[generalise(unit.path)] += max(1, len(found)) if found else 1
                    if found:
                        entries += self._plan_spans(unit.plan, text, found, originals)
                    else:
                        finding = Finding(
                            locator=Locator(path=unit.path),
                            entity_type=UNCLASSIFIED_ENTITY,
                            score=1.0,
                            detectors=["unclassified"],
                            sensitivity=entity_info(UNCLASSIFIED_ENTITY).sensitivity,
                            value_hash=hash_value(text),
                        )
                        entries.append(self._entry(finding, unit.plan.method))
                        originals.append((unit.plan, text, True))
            out = dict(row)
            fresh: set[int] = {id(out)}  # containers of ``out`` copied from the row (written to)
            if entries:
                plan = Plan(
                    mode=self.mode,
                    record_sensitivity=record_sensitivity(e.finding.entity_type for e in entries),
                    entries=entries,
                )
                options = dataclasses.replace(
                    base, anchor=row_anchor(row, self.shape, self.anchor), locale=self._locale(row)
                )
                applied = apply_plan(plan=plan, options=options, data=doc)
                methods.update(applied.methods_used)
                notes.update(applied.notes)
                outputs: dict[str, str | None] = {}
                reversible = {h.path for h in applied.reveal_hints}
                for unit, _ in searched:
                    try:
                        value = get_path(applied.output, unit.path)
                    except (KeyError, IndexError, TypeError):
                        value = None
                    result = None if value is None else str(value)
                    column = unit.out
                    outputs[unit.path] = result
                    stored = self._stored(row, unit, result)
                    if unit.path in reversible and to_engine_text(stored, column) != result:
                        # the column would change a token reveal must read back exactly
                        raise IncompatibleMethod(column.path, "incompatible_method")
                    if top_level(column) and unit.path == column.path:
                        out[column.name] = stored
                    else:
                        _set_copied(out, unit.path, stored, fresh)
                checked += _checked(originals)
                inputs = {unit.path: text for unit, text in searched}
                hits += verify_row(originals, outputs, inputs=inputs)
            out_rows.append(out)
        for method, count in methods.items():
            TRANSFORMS.labels(self.mode, method).inc(count)
        for (entity, detector), count in findings_seen.items():
            FINDINGS.labels(entity, detector).inc(count)
        verification = Verification(passed=not hits, checked=checked, hits=hits[:_MAX_HITS])
        if hits:
            VERIFY_FAIL.labels(self.mode).inc()
            if self.verify_mode == "strict":
                raise LeakageError(len(hits))
        return BatchResult(
            rows=out_rows,
            values_by_method=dict(methods),
            fallbacks=dict(notes),
            verification=verification,
            values_by_column=dict(per_column),
        )


def plans_digest(columns: list[ColumnPlan]) -> str:
    """Stable JSON of the column plans (for the run plan / config hash)."""
    return json.dumps(
        [c.model_dump(mode="json") for c in columns], sort_keys=True, separators=(",", ":")
    )


# --- reveal (contract §6.2; ``tools/reveal_rows.py``) -------------------------------------------


def _phone_prefix(token: str, params: dict[str, Any]) -> int:
    """Length of the country code a ``phone`` token kept verbatim (0 when none was kept)."""
    import phonenumbers

    if params.get("keep_country_code", True) is False or not token.strip().startswith("+"):
        return 0
    try:
        return len(str(phonenumbers.parse(token, None).country_code))
    except phonenumbers.NumberParseException:
        return 0


def _candidates(
    entry: dict[str, Any], token: str, column: ColumnInfo, anchor: str
) -> list[tuple[str, dict[str, Any]]]:
    """``(method, reverse params)`` to try, ``det_token`` first: it authenticates, so it settles
    whether the value ran a fallback (det_token instead of FPE) before an FPE inverse, which
    would decrypt anything, is tried."""
    method, params = entry["method"], entry.get("params") or {}
    out: list[tuple[str, dict[str, Any]]] = [("det_token", {})]
    if method == "email":
        domain = params.get("domain", "keep")
        out += [("email", {"sub": "det_token", "domain": domain})]
        out += [("email", {"sub": "fpe_text", "domain": domain})]
    elif method == "phone":
        first = _phone_prefix(token, params)
        out += [("phone", {"keep_prefix": n}) for n in dict.fromkeys([first, 0, 1, 2, 3])]
    elif method == "date_shift":
        fmt = {"date": ISO_DATE, "timestamp": ISO_DATETIME}.get(column.kind)
        out += [("date_shift", {"anchor": anchor, **({"fmt": fmt} if fmt else {})})]
    elif method != "det_token":
        out += [(method, {})]
    return out


def _reproduces(
    entry: dict[str, Any], column: ColumnInfo, plain: str, token: str, options: ApplyOptions
) -> bool:
    """True when the forward plan maps ``plain`` back to ``token`` (fallbacks included)."""
    finding = Finding(
        locator=Locator(path=column.path),
        entity_type=entry["entity_type"],
        score=1.0,
        detectors=["snapshot"],
        sensitivity=entity_info(entry["entity_type"]).sensitivity,
        value_hash=hash_value(plain),
    )
    plan = Plan(
        mode="obfuscate",
        record_sensitivity=record_sensitivity([entry["entity_type"]]),
        entries=[
            PlanEntry(
                finding=finding,
                method=entry["method"],
                params=dict(entry.get("params") or {}),
                reason="reveal-check",
                reversible=True,
            )
        ],
    )
    try:
        out = apply_plan(plan=plan, options=options, data={column.name: plain})
    except MethodError:
        return False
    return out.output.get(column.name) == token


def _reveal_value(
    entry: dict[str, Any],
    value: Any,
    column: ColumnInfo,
    *,
    key: KeyMaterial,
    options: ApplyOptions,
    raw_anchor: str,
    field_scoped: bool,
) -> Any:
    token = to_engine_text(value, column)
    if not token:
        return value
    if entry.get("text_column"):
        doc = reveal(output={column.name: token}, key=key, hints=[], options=options)
        return from_engine_text(doc[column.name], column)
    anchor = _keyed_anchor(raw_anchor, key)
    kcv = key.kcv or key_check_value(key)
    check = dataclasses.replace(options, anchor=raw_anchor)
    for method, reverse_params in _candidates(entry, token, column, anchor):
        hint = RevealHint(
            path=column.path,
            entity_type=entry["entity_type"],
            method=method,
            params=dict(entry.get("params") or {}),
            reverse_params=reverse_params,
            anchor=anchor,
            kid=key.kid,
            kcv=kcv,
            field_scoped=field_scoped,
        )
        try:
            plain = reveal(output={column.name: token}, key=key, hints=[hint], options=options)
        except MethodError:
            continue
        text = str(plain[column.name])
        if _reproduces(entry, column, text, token, check):
            return from_engine_text(text, column)
    raise MethodError(f"the value at {column.path} could not be revealed with this key material")


def reveal_row(
    row: Row,
    *,
    reveal_map: list[dict[str, Any]],
    shape: TableShape,
    key: KeyMaterial,
    engine: PrivacyEngine,
    policy: Policy,
    anchor: tuple[str, str] | None,
    kid: str | None = None,
) -> Row:
    """Reveal one L2 output row with the caller's key and an evidence pack's ``reveal_map``.

    The anchor's inputs (the anchor column, else the key columns) are revealed first and the row
    anchor is recomputed from them, as ``date_shift`` needs it (contract §6.2). Entries with
    ``reveal: false`` and columns not in the map are returned as they are. Each whole value is
    tried as ``det_token`` first (an FPE fallback) and then as its method, and a candidate is
    accepted only when the forward method maps it back to the stored token, so a fallback or an
    ambiguous parameter can never reveal a wrong value silently. Free-text columns reveal their
    envelopes.

    **Another key** is caught by ``kid`` — pass the evidence pack's ``consistency.kid``: FF1 and
    the shape-keeping methods decrypt any token to a well-formed value under any key, and the
    forward check maps that value back to the same token under the same wrong key, so without the
    key id a wrong key is detected only by the authenticated methods (``det_token`` / AES-SIV) the
    row happens to hold. A mistyped DEK with the right id is refused earlier by its key check value
    (:func:`~dps.engine.transform.crypto.key_material_from_caller`), as in L1's reveal.

    Raises:
        MethodError: ``kid`` is not the key's id, or a value does not reveal under ``key`` (a
            tampered token, an authenticated token of another key); the message names the path,
            never the value.
    """
    if kid is not None and kid != key.kid:
        raise MethodError("the key's id does not match the evidence pack's key id")
    out = dict(row)
    entries = [e for e in reveal_map if e.get("reveal")]
    if not entries:
        return out
    by_path = {c.path: c for c in shape.columns}
    options = ApplyOptions(
        policy=policy,
        key=key,
        surrogates=None,
        fpe_enabled=engine.fpe_enabled,
        cipher_factory=engine.cipher_factory,
        token_style="envelope",
        locale=policy.locale.default or engine.settings.default_locale,
    )
    field_scoped = policy.consistency.field_scoped
    first = {anchor[0]} if anchor else set(shape.key)
    known = [e for e in entries if e["path"] in by_path]
    inputs = [e for e in known if by_path[e["path"]].name in first]
    rest = [e for e in known if by_path[e["path"]].name not in first]
    for group in (inputs, rest):
        raw = row_anchor(out, shape, anchor)  # recomputed once the anchor inputs are revealed
        for entry in group:
            column = by_path[entry["path"]]
            out[column.name] = _reveal_value(
                entry,
                out.get(column.name),
                column,
                key=key,
                options=options,
                raw_anchor=raw,
                field_scoped=field_scoped,
            )
    return out
