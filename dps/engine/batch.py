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

**Reversible outputs survive the column** (contract §6.5, amendment 2026-10-12). ``fpe_digits``
plans default to ``leading_zero: preserve`` (:class:`ColumnPlan`, like every plan entry), and an
integer, decimal or float column refuses any other setting (``incompatible_method``): a token
there that starts with ``0`` would be stored without that digit and reveal would return another
number. Any reversible output the target column would store differently (a dropped leading zero,
a decimal re-scaled to the column's scale) fails the batch with ``incompatible_method`` instead
of being written.

**Tabular synthesis** (M5, contract §6.6). With a :class:`TabularSampler` (synthesize mode only),
after the planned columns are transformed the sampler's columns (non-key, unplanned) are
overwritten with one synthetic draw per row, keyed by the row's key anchor
(``row:<hash(key)>``, unique per row even when rows share an entity anchor), then the policy's
constraints run, and a row equal on every reject column to a real row — of the fit sample, the
holdout or the batch's own sources — is redrawn (the stream salted by ``attempt``). More than
``max_resample`` redraws for one row fails the batch closed: ``LeakageError("exact_row_match")``.
"""

from __future__ import annotations

import dataclasses
import json
from collections import Counter
from collections.abc import Mapping, Sequence
from collections.abc import Set as AbstractSet
from typing import TYPE_CHECKING, Any, Literal, Protocol, cast

from pydantic import BaseModel, ConfigDict, Field, model_validator

from dps.engine.findings import (
    Finding,
    Locator,
    Plan,
    PlanEntry,
    hash_value,
    with_plan_defaults,
)
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
from dps.observability import FINDINGS, TABULAR_RESAMPLES, TRANSFORMS, VERIFY_FAIL

if TYPE_CHECKING:  # the constraints module needs the [tabular] extra; M2 jobs run without it
    from dps.engine.synth.constraints import ConstraintSet

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


class TabularSampler(Protocol):
    """M5's per-row tabular sampler (contract §6.6): ``GaussianCopula``, or the MOSTLY pool.

    Rows are keyed by column **name**; the same model, seed, anchor and attempt always give the
    same row. ``row_digest``/``is_rejected`` decide exact-row matches over the sampler's reject
    columns (the non-key output columns).
    """

    @property
    def engine(self) -> str: ...  # "copula" | "mostly"

    @property
    def columns(self) -> list[str]: ...  # column NAMES the sampler writes

    def sample_row(self, anchor: str) -> dict[str, Any]: ...

    def sample_rows(self, anchors: Sequence[str], *, attempt: int = 0) -> list[dict[str, Any]]: ...

    def row_digest(self, row: Mapping[str, Any]) -> int: ...

    def is_rejected(
        self, row: Mapping[str, Any], *, extra: AbstractSet[int] = frozenset()
    ) -> bool: ...

    def to_bytes(self) -> bytes: ...


class ColumnPlan(BaseModel):
    """One planned column, built once per job from the accepted snapshot + resolved policy.

    ``kind``, ``length``, ``precision`` and ``scale`` describe the **output** (target) column.
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
    """The per-row sweep found originals in the output under ``strict``, or a synthetic row kept
    equalling a real row (``rule="exact_row_match"``, the message is then the rule alone); nothing
    is written. Value-free."""

    code = "leakage"

    def __init__(self, hits: int, *, rule: str | None = None) -> None:
        message = f"leakage: {hits} original value(s) found in the transformed batch; "
        super().__init__(rule if rule is not None else message + "nothing was written")
        self.hits, self.rule = hits, rule


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
        constraints: ConstraintSet | None = None,
        max_resample: int = 8,
    ) -> None:
        """Validate the mode's prerequisites and the plans against ``shape``.

        Raises:
            ValueError: obfuscate without key material, synthesize without a seed, a plan (or
                the anchor) naming a column the table does not have, or a plan whose method
                params lack their plan defaults (copied around :class:`ColumnPlan`'s validator);
                a tabular sampler outside synthesize mode, on a table without a key, or writing
                a key column, a planned column or a column the table does not have.
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
        if tabular is not None:
            if mode != "synthesize":
                raise ValueError("tabular sampling runs in synthesize mode only")
            if not shape.key:
                raise ValueError("tabular sampling needs a table key")
            writable = known - set(shape.key) - {c.name for c in columns}
            if any(name not in writable for name in tabular.columns):
                raise ValueError(
                    "the tabular sampler writes a key column, a planned column or a column "
                    "the table does not have"
                )
        if max_resample < 0:
            raise ValueError("max_resample must not be negative")
        self.engine, self.policy, self.mode = engine, policy, mode
        self.shape, self.key, self.seed, self.locale = shape, key, seed, locale
        self.anchor, self.verify_mode = anchor, verify
        self.unclassified, self.tabular = unclassified, tabular
        self.constraints, self.max_resample = constraints, max_resample
        self._int_key = False
        if tabular is not None and len(shape.key) == 1 and shape.key[0] in known:
            key_column = shape.column(shape.key[0])
            self._int_key = key_column.kind == "int" or (
                key_column.kind == "decimal" and not key_column.scale
            )
        self.whole = [c for c in columns if not c.text_column]
        self.text = [c for c in columns if c.text_column]
        self.detector = engine.detector(policy, locale) if self.text else None
        self.out_cols = {c.name: c.output_column() for c in columns}

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
            tokenizer=self.engine.tokenizer,
            external_cache=self._pretokenize(rows),
            binding_keys=self.engine.token_binding,
        )
        out_rows: list[Row] = []
        methods: Counter[str] = Counter()
        notes: Counter[str] = Counter()
        per_column: Counter[str] = Counter()
        findings_seen: Counter[tuple[str, str]] = Counter()
        hits: list[dict[str, str]] = []
        checked = 0
        tabular_anchors: list[str] = []
        key_ints: list[int | None] = []
        for row in rows:
            if self.tabular is not None:
                # keyed by the row key: rows sharing an entity anchor still get their own draws
                tabular_anchors.append(row_anchor(row, self.shape, None))
                key_ints.append(self._key_int(row))
            doc: dict[str, str] = {}
            for col in self.whole + self.text:
                text = to_engine_text(row.get(col.name), self.shape.column(col.name))
                if text:
                    doc[col.name] = text
            entries: list[PlanEntry] = []
            originals: list[tuple[ColumnPlan, str, bool]] = []
            searched: set[str] = set()
            for col in self.whole:
                if col.name not in doc or col.method.method == "keep":
                    continue
                finding = Finding(
                    locator=Locator(path=col.path),
                    entity_type=col.entity_type,
                    score=1.0,
                    detectors=["snapshot"],
                    sensitivity=entity_info(col.entity_type).sensitivity,
                    value_hash=hash_value(doc[col.name]),
                )
                entries.append(self._entry(finding, col.method))
                originals.append((col, doc[col.name], True))
                searched.add(col.name)
                per_column[col.name] += 1
            for col in self.text:
                if col.name not in doc:
                    continue
                searched.add(col.name)  # free text is always searched, findings or not
                found = self._spans(col, doc[col.name], findings_seen)
                per_column[col.name] += len(found)
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
                    span = doc[col.name][loc.start or 0 : loc.end]
                    as_span = col.model_copy(update={"entity_type": entry.finding.entity_type})
                    originals.append((as_span, span, False))
                entries += planned
            out = dict(row)
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
                for name in searched:
                    value = applied.output.get(name)
                    text = None if value is None else str(value)
                    column = self.out_cols[name]
                    outputs[column.path] = text
                    try:
                        stored = from_engine_text(text, column)
                    except IncompatibleValue as exc:
                        raise IncompatibleMethod(column.path, exc.code) from None
                    if column.path in reversible and to_engine_text(stored, column) != text:
                        # the column would change a token reveal must read back exactly
                        raise IncompatibleMethod(column.path, "incompatible_method")
                    out[name] = stored
                checked += _checked(originals)
                inputs = {self.out_cols[name].path: doc[name] for name in searched}
                hits += verify_row(originals, outputs, inputs=inputs)
            out_rows.append(out)
        resample_counts: dict[str, int] = {}
        if self.tabular is not None:
            written, resamples = self._apply_tabular(rows, out_rows, tabular_anchors, key_ints)
            engine_name = self.tabular.engine
            methods[engine_name] += sum(written.values())
            per_column.update(written)
            if resamples:
                resample_counts[f"{engine_name}_resample"] = resamples
                TABULAR_RESAMPLES.inc(resamples)
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
            values_by_method={**methods, **resample_counts},
            fallbacks=dict(notes),
            verification=verification,
            values_by_column=dict(per_column),
        )

    def _pretokenize(self, rows: list[Row]) -> dict[tuple[str, str], str] | None:
        """M5 ``external_tokenize``: one call per entity type and profile for the batch's whole
        values (spans in free text still go through ``apply``'s per-row pre-pass).

        Raises:
            ExternalServiceError: the tokenization service failed (value-free).
        """
        tokenizer = self.engine.tokenizer
        columns = [c for c in self.whole if c.method.method == "external_tokenize"]
        if tokenizer is None or not columns:
            return None
        groups: dict[tuple[str, str | None], set[str]] = {}
        for col in columns:
            info = self.shape.column(col.name)
            profile = col.method.params.get("profile")
            key = (col.entity_type, str(profile) if profile is not None else None)
            for row in rows:
                text = to_engine_text(row.get(col.name), info)
                if text:
                    groups.setdefault(key, set()).add(text)
        cache: dict[tuple[str, str], str] = {}
        for (entity, profile), values in groups.items():
            ordered = sorted(values)
            tokens = tokenizer.tokenize(entity, ordered, profile=profile)
            cache.update({(entity, v): t for v, t in zip(ordered, tokens, strict=True)})
        return cache

    def _key_int(self, row: Row) -> int | None:
        """The row's single integer key (for ``unique`` constraints), else ``None``."""
        if not self._int_key:
            return None
        value = row.get(self.shape.key[0])
        if value is None or isinstance(value, bool):
            return None
        try:
            return int(value)
        except (TypeError, ValueError, ArithmeticError):
            return None

    def _apply_tabular(
        self,
        sources: list[Row],
        outputs: list[Row],
        anchors: list[str],
        key_ints: list[int | None],
    ) -> tuple[Counter[str], int]:
        """Overwrite the sampler's columns with synthetic values, enforce constraints, and redraw
        any row equal to a real row (fit sample, holdout, or a source row of this batch) on every
        reject column. More than ``max_resample`` redraws for one row fails the batch closed.

        Returns ``(non-null values written per column, redraws)``.

        Raises:
            LeakageError: ``exact_row_match`` — a row stayed equal to a real row.
            ConstraintError: a ``unique`` rule without a usable integer key (value-free).
        """
        sampler = self.tabular
        if sampler is None or not outputs:
            return Counter(), 0
        sampled = sampler.sample_rows(anchors)
        batch_digests = frozenset(sampler.row_digest(src) for src in sources)
        written: Counter[str] = Counter()
        rejections = 0
        for i, out in enumerate(outputs):
            candidate, attempt = sampled[i], 0
            while True:
                out.update(candidate)
                if self.constraints is not None:
                    self.constraints.apply(out, key_int=key_ints[i])
                if not sampler.is_rejected(out, extra=batch_digests):
                    break
                attempt += 1
                if attempt > self.max_resample:
                    raise LeakageError(1, rule="exact_row_match")
                rejections += 1
                candidate = sampler.sample_rows([anchors[i]], attempt=attempt)[0]
            written.update(name for name, value in candidate.items() if value is not None)
        return written, rejections


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
