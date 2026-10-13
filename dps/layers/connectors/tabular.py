"""Tabular synthesis for L2 synthesize jobs (contract §6.6, §7.1, §7.3; spec §5.6, §5.7).

At ``prepare``: sample at most ``fit + holdout`` rows of the selection, put them in a keyed,
order-independent order (so the same seed over the same rows fits the same model, whatever order
the database returned them in), split a deterministic holdout, fit the engine on the rest, run a
deterministic *preview* of the first fit rows through the real ``BatchTransformer`` (exactly the
rows the job writes for those sources), measure fidelity and privacy on it, and gate before
anything is written. The fitted model is returned base64-encoded for the caller to seal into
``JobSecret.tabular_model``; it is never stored unsealed, never logged and purged with the job key.

Which columns are modelled (:func:`tabular_columns`): top-level, non-key columns the accepted
snapshot classifies non-sensitive (no entity type, not free text) whose kind the engine models,
categorical ones only up to ``max_distinct_ratio`` distinct values. Other non-sensitive columns
keep their source values and are listed in the evidence (``tabular.excluded``) with a reason.
Sensitive columns are not listed: they get surrogates as before.

Every metric, gate detail and evidence field is value-free: numbers, column paths, counts, codes.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
from collections import OrderedDict
from collections.abc import Callable, Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from dps.config import Settings
from dps.engine.batch import BatchTransformer, LeakageError, TabularSampler
from dps.engine.metrics import (
    canonical,
    encode_features,
    exact_row_matches,
    fidelity_report,
    generalize_qi,
    membership_inference_proxy,
    nndr,
    qi_unique_overlap,
    qi_uniqueness,
    row_hash,
    utility_tstr,
)
from dps.engine.policy import Policy
from dps.engine.synth.constraints import ColumnSpec, ConstraintError, ConstraintSet
from dps.engine.synth.copula import CopulaError, GaussianCopula
from dps.engine.synth.keys import INT_DIGITS  # digits an integer column holds (Task 4 note)
from dps.engine.synth.mostly import TabularUnavailable
from dps.engine.synth.personas import seed_int
from dps.jobs_models import ClassificationSnapshot
from dps.layers.connectors.base import Connector, Row, TableShape

#: Fewest sampled rows a tabular job accepts (the engine refuses fewer fit rows on its own).
MIN_ROWS = 50
#: Connector kind → tabular kind; text-like columns are categories.
KIND_MAP: dict[str, str] = {
    "int": "int",
    "decimal": "decimal",
    "float": "float",
    "date": "date",
    "timestamp": "timestamp",
    "bool": "bool",
    "text": "categorical",
    "char": "categorical",
}
#: Quasi-identifier entity types and how each is coarsened before uniqueness is counted.
QI_ENTITIES: dict[str, str] = {
    "DATE_OF_BIRTH": "decade",
    "AGE": "band10",
    "GENDER": "exact",
    "POSTAL_CODE": "prefix3",
    "CITY": "exact",
    "NATIONALITY": "exact",
}
GATE_CODES = frozenset({"tabular_invalid", "leakage", "privacy_gate", "incompatible_method"})
_CACHE: OrderedDict[tuple[str, str, str], TabularSampler] = OrderedDict()
_CACHE_MAX = 8
_HIDDEN = ConfigDict(hide_input_in_errors=True)


class TabularColumn(BaseModel):
    """One modelled column: its path, name, tabular kind and output precision/scale/shape."""

    model_config = _HIDDEN
    path: str
    name: str
    kind: str
    scale: int | None = None
    precision: int | None = None
    shape: str | None = None


class TabularGateError(Exception):
    """``prepare`` must fail the job with ``code``; ``detail`` is value-free."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code, self.detail = code, detail


class TabularPrepared(BaseModel):
    """What ``prepare`` seals (``model_b64``) and stores in ``progress.metrics`` (``metrics``)."""

    model_config = _HIDDEN
    engine: Literal["copula", "mostly"]
    model_b64: str
    metrics: dict[str, Any]


def _top_level(path: str) -> bool:
    return path.startswith("$.") and "." not in path[2:] and "[" not in path


def _int_key(shape: TableShape) -> bool:
    """True when the table has one integer key column (what ``unique`` constraints need)."""
    if len(shape.key) != 1:
        return False
    try:
        column = shape.column(shape.key[0])
    except KeyError:
        return False
    return column.kind == "int" or (column.kind == "decimal" and not column.scale)


def tabular_columns(
    shape: TableShape,
    snapshot: ClassificationSnapshot,
    *,
    max_distinct_ratio: float = 0.5,
    projection: Sequence[str] | None = None,
) -> tuple[list[TabularColumn], list[dict[str, str]]]:
    """Modellable columns and the non-sensitive columns left as source values (with reasons).

    ``projection`` (the run's projected column names) leaves every other column out entirely.
    """
    info = {c.path: c for c in shape.columns}
    keys = set(shape.key)
    wanted = set(projection) if projection is not None else None
    out: list[TabularColumn] = []
    excluded: list[dict[str, str]] = []
    for cc in sorted(snapshot.columns, key=lambda c: c.path):
        if cc.is_key or cc.name in keys or cc.entity_type is not None or cc.text_column:
            continue
        if wanted is not None and cc.name not in wanted:
            continue
        column = info.get(cc.path)
        if column is None or not _top_level(cc.path):
            excluded.append({"path": cc.path, "reason": "nested"})
            continue
        kind = KIND_MAP.get(column.kind)
        if kind is None:
            excluded.append({"path": cc.path, "reason": f"kind:{column.kind}"})
            continue
        if kind == "categorical" and cc.distinct_ratio > max_distinct_ratio:
            excluded.append({"path": cc.path, "reason": "high_cardinality"})
            continue
        precision = column.precision
        if kind == "int" and precision is None:
            precision = INT_DIGITS.get(column.data_type.strip().lower())
        out.append(
            TabularColumn(
                path=cc.path,
                name=column.name,
                kind=kind,
                scale=column.scale,
                precision=precision,
                shape=cc.shapes[0] if cc.shapes else None,
            )
        )
    return out, excluded


def split_holdout(
    rows: list[Row], anchors: list[str], seed: bytes, *, max_holdout: int
) -> tuple[list[Row], list[Row]]:
    """~20 % of rows (by a keyed hash of the anchor), capped at ``max_holdout``, never fitted."""
    train: list[Row] = []
    holdout: list[Row] = []
    for row, anchor in zip(rows, anchors, strict=True):
        if len(holdout) < max_holdout and seed_int(seed, f"holdout|{anchor}") % 100 < 20:
            holdout.append(row)
        else:
            train.append(row)
    return train, holdout


def keyed_order(rows: list[Row], anchors: list[str], seed: bytes) -> tuple[list[Row], list[str]]:
    """``rows`` (and their anchors) in an order fixed by the seed and the anchors alone."""
    order = sorted(range(len(rows)), key=lambda i: (seed_int(seed, f"order|{anchors[i]}"), i))
    return [rows[i] for i in order], [anchors[i] for i in order]


def build_constraints(
    policy: Policy, columns: list[TabularColumn], *, seed: bytes, object_fqn: str
) -> ConstraintSet | None:
    """The policy's constraints over the sampled ``columns`` (``None`` when it has none).

    Raises:
        ConstraintError: a rule the column cannot take (value-free).
    """
    if not policy.synthesize.constraints:
        return None
    specs = {c.name: ColumnSpec(c.name, c.kind, c.precision, c.scale, c.shape) for c in columns}
    return ConstraintSet(policy.synthesize.constraints, specs, seed=seed, object_fqn=object_fqn)


def check_constraints(constraints: ConstraintSet | None, shape: TableShape) -> None:
    """Refuse a ``unique`` rule on a table without one integer key (it is keyed by that key).

    Raises:
        ConstraintError: value-free.
    """
    if constraints is not None and constraints.needs_key_int and not _int_key(shape):
        raise ConstraintError("a unique rule needs a table keyed by one integer column")


def fit_sampler(
    engine: str,
    train: list[Row],
    columns: list[TabularColumn],
    *,
    seed: bytes,
    settings: Settings,
    reject_rows: list[Row],
    reject_columns: list[str],
    dp: Any = None,
) -> TabularSampler:
    """Fit ``engine`` on ``train`` (``dp``: the MOSTLY engine's ``DpConfig``, Task 7B) (CPU-bound or a local training run: run it in a thread).

    Raises:
        CopulaError: the copula cannot be fitted; TabularUnavailable: the MOSTLY engine cannot
            run (value-free).
    """
    data = {c.path: [r.get(c.name) for r in train] for c in columns}
    kinds = {c.path: c.kind for c in columns}
    if engine == "mostly":
        from dps.engine.synth.mostly import MostlySampler  # the optional synth-ml extra

        return MostlySampler.fit(
            data,
            kinds,
            seed=seed,
            pool_rows=settings.mostly_pool_rows,
            max_training_minutes=settings.mostly_max_training_minutes,
            scales={c.path: c.scale or 0 for c in columns},
            reject_rows=reject_rows,
            reject_columns=reject_columns,
            dp=dp,
        )
    return GaussianCopula.fit(
        data,
        kinds,
        rare_floor=settings.copula_rare_floor,
        seed=seed,
        scales={c.path: c.scale or 0 for c in columns if c.kind == "decimal"},
        reject_rows=reject_rows,
        reject_columns=reject_columns,
    )


def load_sampler(engine: str, model_b64: str, seed: bytes) -> TabularSampler:
    """Deserialise a sealed model (cached per process; the cache key holds digests only).

    Raises:
        CopulaError / TabularUnavailable: the bytes are not a model of the engine's format.
    """
    key = (
        engine,
        hashlib.sha256(model_b64.encode("ascii")).hexdigest(),
        hashlib.sha256(seed).hexdigest(),
    )
    cached = _CACHE.get(key)
    if cached is not None:
        _CACHE.move_to_end(key)
        return cached
    data = base64.b64decode(model_b64)
    sampler: TabularSampler
    if engine == "mostly":
        from dps.engine.synth.mostly import MostlySampler

        sampler = MostlySampler.from_bytes(data, seed)
    else:
        sampler = GaussianCopula.from_bytes(data, seed)
    _CACHE[key] = sampler
    while len(_CACHE) > _CACHE_MAX:
        _CACHE.popitem(last=False)
    return sampler


def runtime_sampler(
    engine: str,
    model_b64: str,
    seed: bytes,
    *,
    policy: Policy,
    shape: TableShape,
    snapshot: ClassificationSnapshot,
    object_fqn: str,
) -> tuple[TabularSampler, ConstraintSet | None]:
    """The sealed model and the policy's constraints over the columns it writes (partitions).

    Raises:
        CopulaError: the model cannot be read; ConstraintError: a rule the column cannot take.
    """
    sampler = load_sampler(engine, model_b64, seed)
    written = set(sampler.columns)
    columns = [c for c in tabular_columns(shape, snapshot)[0] if c.name in written]
    constraints = build_constraints(policy, columns, seed=seed, object_fqn=object_fqn)
    check_constraints(constraints, shape)
    return sampler, constraints


def _qi_columns(
    policy: Policy, snapshot: ClassificationSnapshot, columns: list[TabularColumn]
) -> list[tuple[str, str, str]]:
    """``(path, name, generalisation)`` for the quasi-identifiers of this table."""
    by_path = {c.path: c for c in snapshot.columns}
    kinds = {c.path: c.kind for c in columns}
    chosen: list[tuple[str, str, str]] = []
    paths = list(policy.synthesize.quasi_identifiers) or [
        c.path for c in snapshot.columns if c.entity_type in QI_ENTITIES
    ]
    for path in paths:
        cc = by_path.get(path)
        if cc is None or not _top_level(path):
            continue
        how = QI_ENTITIES.get(cc.entity_type or "", "")
        if not how:
            kind = kinds.get(path)
            how = (
                "decade"
                if kind in ("date", "timestamp")
                else "band10"
                if kind in ("int", "decimal", "float")
                else "exact"
            )
        chosen.append((path, cc.name, how))
    return chosen


def _generalised(rows: list[Row], qi: list[tuple[str, str, str]]) -> list[dict[str, Any]]:
    return [{name: generalize_qi(r.get(name), how) for _, name, how in qi} for r in rows]


def _too_small(holdout_rows: list[Row], settings: Settings) -> str | None:
    if len(holdout_rows) < settings.mia_min_holdout:
        return f"holdout has {len(holdout_rows)} rows (< {settings.mia_min_holdout})"
    return None


def _utility(
    label: str | None,
    columns: list[TabularColumn],
    preview_source: list[Row],
    synth_rows: list[Row],
    holdout_rows: list[Row],
    settings: Settings,
) -> dict[str, Any]:
    """Train on synthetic, test on real for the policy's ``utility_label`` (spec §5.7)."""
    if not label:
        return {"run": False, "reason": "no utility_label in the policy"}
    target = next((c for c in columns if c.path == label), None)
    if target is None:
        return {"run": False, "reason": "utility_label is not a modelled column"}
    small = _too_small(holdout_rows, settings)
    if small:
        return {"run": False, "reason": small}
    features = [c for c in columns if c.path != label]
    if not features:
        return {"run": False, "reason": "no modelled feature column besides the label"}
    kinds = {c.path: c.kind for c in features}
    cap = settings.utility_max_rows

    def side(rows: list[Row]) -> tuple[dict[str, list[Any]], list[Any]]:
        kept = [r for r in rows if r.get(target.name) is not None][:cap]
        return {c.path: [r.get(c.name) for r in kept] for c in features}, [
            r.get(target.name) for r in kept
        ]

    real, real_y = side(preview_source)
    synth, synth_y = side(synth_rows)
    test, test_y = side(holdout_rows)
    if not real_y or not synth_y or not test_y:
        return {"run": False, "reason": "the label column is null in one of the samples"}
    task: Literal["classification", "regression"] = (
        "classification" if target.kind in ("categorical", "bool") else "regression"
    )
    out = utility_tstr(
        encode_features(real, kinds),
        real_y,
        encode_features(synth, kinds, real),
        synth_y,
        encode_features(test, kinds, real),
        test_y,
        task=task,
    )
    return {**out, "label": label, "task": task}


def measure(
    *,
    real_rows: list[Row],
    train_rows: list[Row],
    holdout_rows: list[Row],
    preview_source: list[Row],
    synth_rows: list[Row],
    columns: list[TabularColumn],
    qi: list[tuple[str, str, str]],
    reject_columns: list[str],
    settings: Settings,
    engine: str,
    preview_resamples: int,
    utility_label: str | None = None,
) -> dict[str, Any]:
    """Fidelity, privacy and utility of the preview (value-free: numbers, paths, counts)."""
    kinds = {c.path: c.kind for c in columns}

    def by_path(rows: list[Row]) -> dict[str, list[Any]]:
        return {c.path: [r.get(c.name) for r in rows] for c in columns}

    fidelity = fidelity_report(
        by_path(real_rows),
        by_path(synth_rows),
        kinds,
        column_limit=settings.fidelity_column_limit,
        corr_limit=settings.fidelity_corr_limit,
    )
    reference = by_path(train_rows)
    train_x = encode_features(reference, kinds)
    synth_x = encode_features(by_path(synth_rows), kinds, reference)
    holdout_x = (
        encode_features(by_path(holdout_rows), kinds, reference)
        if len(holdout_rows) >= 10
        else None
    )
    nn = nndr(train_x, synth_x, sample=2000, seed=0, holdout=holdout_x)
    real_hashes = {row_hash(r, reject_columns) for r in real_rows}
    names = [name for _, name, _ in qi]
    real_qi, synth_qi = _generalised(preview_source, qi), _generalised(synth_rows, qi)
    passed = bool(nn["measured"]) and nn["ratio"] >= settings.privacy_nndr_target
    small = _too_small(holdout_rows, settings)
    mia: dict[str, Any] = (
        {"run": False, "reason": small}
        if small or holdout_x is None
        else membership_inference_proxy(train_x, holdout_x, synth_x, sample=2000, seed=0)
    )
    if mia.get("run"):
        mia |= {"target": settings.mia_auc_target, "passed": mia["auc"] <= settings.mia_auc_target}
    return {
        "fidelity": {
            **fidelity,
            "engine": engine,
            "measured_on": {
                "real_rows": len(real_rows),
                "synthetic_rows": len(synth_rows),
                "holdout_rows": len(holdout_rows),
                "basis": "deterministic preview of the rows this job writes",
            },
        },
        "privacy": {
            "enforced": True,
            "engine": engine,
            "measured": True,
            "exact_row_matches": exact_row_matches(real_hashes, synth_rows, reject_columns),
            "exact_row_resamples": preview_resamples,
            "nndr": {**nn, "target": settings.privacy_nndr_target, "passed": passed},
            "nndr_enforced": settings.privacy_nndr_enforce,
            "qi_uniqueness": {
                "columns": [p for p, _, _ in qi],
                "source": round(qi_uniqueness(real_qi, names), 4),
                "synthetic": round(qi_uniqueness(synth_qi, names), 4),
                "unique_overlap": qi_unique_overlap(real_qi, synth_qi, names),
            },
            "membership_inference": mia,
        },
        "utility": _utility(
            utility_label, columns, preview_source, synth_rows, holdout_rows, settings
        ),
    }


async def prepare_tabular(
    *,
    settings: Settings,
    engine_name: str,
    connector: Connector,
    shape: TableShape,
    selection: Any,
    params: dict[str, Any],
    snapshot: ClassificationSnapshot,
    policy: Policy,
    seed: bytes,
    object_fqn: str,
    make_transformer: Callable[[TabularSampler, ConstraintSet | None], BatchTransformer],
    anchor_of: Callable[[Row], str],
    projection: Sequence[str] | None = None,
    dp: Any = None,
    pseudonyms: Sequence[str] = (),
) -> TabularPrepared:
    """Fit, preview, measure and gate. Never writes.

    ``pseudonyms`` (D-A): identifier columns the run replaces by keyed pseudonyms; they are left
    out of the exact-row comparison (a pseudonym never equals the real value, so comparing on it
    would let a row that copies every other column through).

    Raises:
        TabularGateError: ``tabular_invalid`` (engine off, no column, too few rows, the model
            cannot be fitted), ``incompatible_method`` (a constraint the table cannot take),
            ``leakage`` (the preview could not avoid a real row) or ``privacy_gate`` (NNDR below
            target while enforced). Details are value-free.
    """
    if engine_name not in settings.tabular_engines:
        raise TabularGateError(
            "tabular_invalid", f"tabular engine {engine_name} is not enabled on this deployment"
        )
    columns, excluded = tabular_columns(shape, snapshot, projection=projection)
    if not columns:
        raise TabularGateError("tabular_invalid", "no column qualifies for tabular synthesis")
    sampled = await connector.sample(
        shape, selection, params, settings.copula_fit_rows + settings.copula_holdout_rows
    )
    if len(sampled) < MIN_ROWS:
        raise TabularGateError(
            "tabular_invalid",
            f"tabular synthesis needs at least {MIN_ROWS} sampled rows, got {len(sampled)}",
        )
    rows, anchors = keyed_order(sampled, [anchor_of(r) for r in sampled], seed)
    del sampled
    train, holdout = split_holdout(rows, anchors, seed, max_holdout=settings.copula_holdout_rows)
    key_names = set(shape.key) | set(pseudonyms)
    wanted = set(projection) if projection is not None else None
    reject_columns = sorted(
        c.name
        for c in shape.columns
        if c.name not in key_names and _top_level(c.path) and (wanted is None or c.name in wanted)
    )
    try:
        sampler = await asyncio.to_thread(
            fit_sampler,
            engine_name,
            train,
            columns,
            seed=seed,
            settings=settings,
            reject_rows=rows,
            reject_columns=reject_columns,
            dp=dp,
        )
    except (CopulaError, TabularUnavailable) as exc:
        raise TabularGateError("tabular_invalid", str(exc)) from exc
    written = set(sampler.columns)
    excluded += [
        {"path": path, "reason": reason}
        for path, reason in dict(getattr(sampler, "excluded", {})).items()
    ]
    columns = [c for c in columns if c.name in written]
    preview = train[: settings.copula_metrics_rows]
    try:
        constraints = build_constraints(policy, columns, seed=seed, object_fqn=object_fqn)
        check_constraints(constraints, shape)
        result = await asyncio.to_thread(make_transformer(sampler, constraints).transform, preview)
    except ConstraintError as exc:
        raise TabularGateError("incompatible_method", str(exc)) from exc
    except LeakageError as exc:
        detail = (
            "every redraw of a synthetic preview row equalled a real row (too few distinct "
            "value combinations to synthesize); nothing was written"
            if exc.rule == "exact_row_match"
            else "the synthetic preview failed the leakage sweep; nothing was written"
        )
        raise TabularGateError("leakage", detail) from exc
    metrics = await asyncio.to_thread(
        measure,
        real_rows=rows,
        train_rows=train,
        holdout_rows=holdout,
        preview_source=preview,
        synth_rows=result.rows,
        columns=columns,
        qi=_qi_columns(policy, snapshot, columns),
        reject_columns=reject_columns,
        settings=settings,
        engine=engine_name,
        preview_resamples=int(result.values_by_method.get(f"{engine_name}_resample", 0)),
        utility_label=policy.synthesize.utility_label,
    )
    metrics["tabular"] = {
        "engine": engine_name,
        "columns": [c.path for c in columns],
        "excluded": sorted(excluded, key=lambda e: e["path"]),
        "constraints": constraints.describe() if constraints else [],
        "constraints_skipped": constraints.skipped if constraints else [],
        "fit_rows": len(train),
        "holdout_rows": len(holdout),
        "preview_rows": len(preview),
    }
    if engine_name == "mostly":
        # with more source rows than pool rows, synthetic rows repeat (Task 7)
        metrics["tabular"]["pool_rows"] = int(getattr(sampler, "pool_rows", 0))
        metrics["tabular"]["dp"] = dict(getattr(sampler, "dp", {"enabled": False}))
    if metrics["privacy"]["exact_row_matches"]:
        raise TabularGateError("leakage", "synthetic preview rows equal real rows")
    if settings.privacy_nndr_enforce and not metrics["privacy"]["nndr"]["passed"]:
        raise TabularGateError(
            "privacy_gate", "nearest-neighbour distance ratio is below the configured target"
        )
    mia = metrics["privacy"]["membership_inference"]
    if settings.privacy_nndr_enforce and mia.get("run") and not mia.get("passed"):
        raise TabularGateError(
            "privacy_gate", "the membership-inference proxy AUC is above the configured target"
        )
    engine_literal: Literal["copula", "mostly"] = "mostly" if engine_name == "mostly" else "copula"
    return TabularPrepared(
        engine=engine_literal,
        model_b64=base64.b64encode(sampler.to_bytes()).decode("ascii"),
        metrics=metrics,
    )


# -- MOSTLY linked generation (Task 7B) -------------------------------------------------------

#: Keyset page size when reading the child table for training.
LINKED_PAGE_ROWS = 10_000
_ORDERABLE = frozenset({"int", "decimal", "float", "date", "timestamp"})


class LinkedPrepared(BaseModel):
    """What ``prepare`` seals (``model_b64``, a :class:`~dps.engine.synth.linked.LinkedPool`),
    stores in ``progress.metrics`` and writes per table (output columns: name, type, kind)."""

    model_config = _HIDDEN
    model_b64: str
    metrics: dict[str, Any]
    parent_columns: list[dict[str, str]]
    child_columns: list[dict[str, str]]
    parent_rows: int
    child_rows: int


def _key_kind(shape: TableShape, name: str) -> str | None:
    column = shape.column(name)
    if column.kind == "int" or (column.kind == "decimal" and not column.scale):
        return "int"
    if column.kind in ("text", "char"):
        return "text"
    return None


def _output_columns(shape: TableShape) -> list[dict[str, str]]:
    return [
        {"name": c.name, "data_type": c.data_type, "kind": c.kind}
        for c in shape.columns
        if _top_level(c.path)
    ]


def _check_layout(
    linked: Any,
    parent_shape: TableShape,
    child_shape: TableShape,
    child_snapshot: ClassificationSnapshot,
) -> None:
    invalid = "tabular_invalid"
    if len(parent_shape.key) != 1 or len(child_shape.key) != 1:
        raise TabularGateError(invalid, "linked generation needs one key column in each table")
    if linked.parent_key != parent_shape.key[0]:
        raise TabularGateError(invalid, "parent_key must be the parent table's key column")
    child_names = {c.name for c in child_shape.columns}
    for name in (linked.foreign_key, linked.order_by):
        if name is not None and name not in child_names:
            raise TabularGateError(invalid, f"the child table has no column {name}")
    if linked.foreign_key == child_shape.key[0]:
        raise TabularGateError(invalid, "the foreign key cannot be the child's key")
    if _key_kind(parent_shape, parent_shape.key[0]) is None or (
        _key_kind(child_shape, child_shape.key[0]) is None
    ):
        raise TabularGateError(invalid, "linked generation writes integer or text keys only")
    if linked.order_by is not None:
        review = next((c for c in child_snapshot.columns if c.name == linked.order_by), None)
        if review is not None and (review.entity_type is not None or review.text_column):
            raise TabularGateError(invalid, f"order_by {linked.order_by} is classified sensitive")
        if KIND_MAP.get(child_shape.column(linked.order_by).kind) not in _ORDERABLE:
            raise TabularGateError(invalid, f"order_by {linked.order_by} is not a number or date")


def _sensitive(
    shape: TableShape, snapshot: ClassificationSnapshot, links: set[str]
) -> dict[str, Any]:
    from dps.engine.synth.linked import SensitiveColumn

    out: dict[str, Any] = {}
    for cc in snapshot.columns:
        if (
            cc.entity_type is None
            or cc.text_column
            or cc.is_key
            or cc.name in links
            or cc.name in shape.key
            or not _top_level(cc.path)
        ):
            continue
        out[cc.name] = SensitiveColumn(
            cc.entity_type, shape.column(cc.name).kind, cc.shapes[0] if cc.shapes else None
        )
    return out


def _by_path(rows: list[Row], names: list[str]) -> dict[str, list[Any]]:
    return {f"$.{n}": [r.get(n) for r in rows] for n in names}


async def prepare_linked(
    *,
    settings: Settings,
    connector: Connector,
    parent_shape: TableShape,
    parent_snapshot: ClassificationSnapshot,
    child_shape: TableShape,
    child_snapshot: ClassificationSnapshot,
    linked: Any,
    selection: Any,
    params: dict[str, Any],
    seed: bytes,
    dp: Any,
    locale: str,
    trainer: Any = None,
) -> LinkedPrepared:
    """Sample parents and their children, train, generate, measure and gate (never writes).

    ``linked`` is the run's :class:`~dps.jobs_models.LinkedSpec`, ``dp`` a
    :class:`~dps.engine.synth.mostly.DpConfig` or ``None``.

    Raises:
        TabularGateError: ``tabular_invalid`` (layout, keys, too few parents, the generator),
            ``leakage`` (a generated row equals a sampled real row) or ``privacy_gate``.
    """
    from dps.engine.batch import row_anchor
    from dps.engine.metrics import ks_statistic
    from dps.engine.synth.linked import LinkedLayout, LinkedPool, LinkedTable, sequence_lengths
    from dps.jobs_models import Selection
    from dps.layers.connectors.base import KeyRange

    if "mostly" not in settings.tabular_engines:
        raise TabularGateError("tabular_invalid", "the mostly engine is not enabled")
    _check_layout(linked, parent_shape, child_shape, child_snapshot)
    fk, order_by, pk = linked.foreign_key, linked.order_by, linked.parent_key
    links = {fk} | ({order_by} if order_by else set())
    pcols = [c for c in tabular_columns(parent_shape, parent_snapshot)[0] if c.name != pk]
    ccols = [c for c in tabular_columns(child_shape, child_snapshot)[0] if c.name != fk]
    if order_by is not None and order_by not in {c.name for c in ccols}:
        column = child_shape.column(order_by)
        ccols.append(
            TabularColumn(
                path=column.path,
                name=column.name,
                kind=KIND_MAP[column.kind],
                scale=column.scale,
                precision=column.precision,
            )
        )
    sampled = await connector.sample(
        parent_shape, selection, params, settings.copula_fit_rows + settings.copula_holdout_rows
    )
    if len(sampled) < MIN_ROWS:
        raise TabularGateError(
            "tabular_invalid",
            f"linked generation needs at least {MIN_ROWS} sampled parents, got {len(sampled)}",
        )
    parents, anchors = keyed_order(
        sampled, [row_anchor(r, parent_shape, None) for r in sampled], seed
    )
    del sampled
    train, holdout = split_holdout(parents, anchors, seed, max_holdout=settings.copula_holdout_rows)
    train_keys = {canonical(r.get(pk)) for r in train}
    holdout_keys = {canonical(r.get(pk)) for r in holdout}
    train_children: list[Row] = []
    holdout_children: list[Row] = []
    async for batch in connector.read_partition(
        child_shape, Selection(), {}, KeyRange(), after_key=None, batch=LINKED_PAGE_ROWS
    ):
        for row in batch:
            key = canonical(row.get(fk))
            if key in train_keys:
                train_children.append(row)
            elif key in holdout_keys:
                holdout_children.append(row)
        if len(train_children) + len(holdout_children) >= settings.mostly_linked_child_rows:
            break
    parent_table = LinkedTable(
        role="parent",
        columns={n: [r.get(n) for r in train] for n in [pk, *(c.name for c in pcols)]},
        kinds={c.name: c.kind for c in pcols},
        output=[c["name"] for c in _output_columns(parent_shape)],
        key=pk,
        key_kind=_key_kind(parent_shape, pk) or "int",
        scales={c.name: c.scale or 0 for c in pcols},
        sensitive=_sensitive(parent_shape, parent_snapshot, set()),
    )
    child_key = child_shape.key[0]
    child_table = LinkedTable(
        role="child",
        columns={n: [r.get(n) for r in train_children] for n in [fk, *(c.name for c in ccols)]},
        kinds={c.name: c.kind for c in ccols},
        output=[c["name"] for c in _output_columns(child_shape)],
        key=child_key,
        key_kind=_key_kind(child_shape, child_key) or "int",
        scales={c.name: c.scale or 0 for c in ccols},
        sensitive=_sensitive(child_shape, child_snapshot, links),
    )
    layout = LinkedLayout(parent_key=pk, foreign_key=fk, order_by=order_by)
    try:
        pool = await asyncio.to_thread(
            LinkedPool.fit,
            parent_table,
            child_table,
            layout=layout,
            subjects=linked.subjects,
            seed=seed,
            max_training_minutes=settings.mostly_max_training_minutes,
            dp=dp,
            trainer=trainer,
            locale=locale,
        )
    except TabularUnavailable as exc:
        raise TabularGateError("tabular_invalid", str(exc)) from exc
    # exact rows over every written non-key, non-link column (sensitive and null ones too): a
    # generated row equal to a sampled real row is dropped (a dropped parent takes its children)
    preject = [n for n in parent_table.output if n != pk]
    creject = [n for n in child_table.output if n not in {child_key, fk}]
    real_parents = {row_hash(r, preject) for r in parents}
    real_children = {row_hash(r, creject) for r in [*train_children, *holdout_children]}
    generated = (len(pool.parent_rows), len(pool.child_rows))
    dropped = {r[pk] for r in pool.parent_rows if row_hash(r, preject) in real_parents}
    pool.parent_rows = [r for r in pool.parent_rows if r[pk] not in dropped]
    pool.child_rows = [
        r
        for r in pool.child_rows
        if r[fk] not in dropped and row_hash(r, creject) not in real_children
    ]
    rejections = generated[0] + generated[1] - len(pool.parent_rows) - len(pool.child_rows)
    if (generated[0] and not pool.parent_rows) or (generated[1] and not pool.child_rows):
        raise TabularGateError(
            "leakage",
            "every generated row of a table equalled a sampled real row; nothing was written",
        )

    def measure_linked() -> dict[str, Any]:
        pnames, cnames = [c.name for c in pcols], [c.name for c in ccols]
        pkinds = {f"$.{c.name}": c.kind for c in pcols}
        ckinds = {f"$.{c.name}": c.kind for c in ccols}
        tables = {
            "parent": fidelity_report(
                _by_path(train, pnames),
                _by_path(pool.parent_rows, pnames),
                pkinds,
                column_limit=settings.fidelity_column_limit,
                corr_limit=settings.fidelity_corr_limit,
            ),
            "child": fidelity_report(
                _by_path(train_children, cnames),
                _by_path(pool.child_rows, cnames),
                ckinds,
                column_limit=settings.fidelity_column_limit,
                corr_limit=settings.fidelity_corr_limit,
            ),
        }
        source_lengths = sequence_lengths(train_children, fk, [r.get(pk) for r in train])
        synthetic_lengths = sequence_lengths(
            pool.child_rows, fk, [r.get(pk) for r in pool.parent_rows]
        )
        matches = exact_row_matches(real_parents, pool.parent_rows, preject) + exact_row_matches(
            real_children, pool.child_rows, creject
        )
        reference = _by_path(train_children, cnames)
        train_x = encode_features(reference, ckinds)
        synth_x = encode_features(_by_path(pool.child_rows, cnames), ckinds, reference)
        holdout_x = (
            encode_features(_by_path(holdout_children, cnames), ckinds, reference)
            if len(holdout_children) >= 10
            else None
        )
        nn = nndr(train_x, synth_x, sample=2000, seed=0, holdout=holdout_x)
        small = _too_small(holdout_children, settings)
        mia: dict[str, Any] = (
            {"run": False, "reason": small}
            if small or holdout_x is None
            else membership_inference_proxy(train_x, holdout_x, synth_x, sample=2000, seed=0)
        )
        if mia.get("run"):
            mia |= {
                "target": settings.mia_auc_target,
                "passed": mia["auc"] <= settings.mia_auc_target,
            }
        scores = [t["score"] for t in tables.values()]
        return {
            "fidelity": {
                "engine": "mostly",
                "score": round(sum(scores) / len(scores), 4),
                "tables": tables,
                "measured_on": {
                    "real_parents": len(train),
                    "real_children": len(train_children),
                    "synthetic_parents": len(pool.parent_rows),
                    "synthetic_children": len(pool.child_rows),
                    "basis": "the generated dataset this job writes",
                },
            },
            "privacy": {
                "enforced": True,
                "engine": "mostly",
                "measured": True,
                "exact_row_matches": matches,
                "exact_row_rejections": rejections,
                "nndr": {
                    **nn,
                    "target": settings.privacy_nndr_target,
                    "passed": bool(nn["measured"]) and nn["ratio"] >= settings.privacy_nndr_target,
                },
                "nndr_enforced": settings.privacy_nndr_enforce,
                "nndr_table": "child",
                "membership_inference": mia,
                "dp": pool.dp,
            },
            "utility": {"run": False, "reason": "not measured for linked generation"},
            "tabular": {
                "engine": "mostly",
                "columns": {"parent": [c.path for c in pcols], "child": [c.path for c in ccols]},
                "fit_rows": len(train),
                "child_fit_rows": len(train_children),
                "holdout_rows": len(holdout),
                "dp": pool.dp,
                "linked": {
                    "parent": parent_shape.object.fqn(),
                    "child": child_shape.object.fqn(),
                    "subjects": len(pool.parent_rows),
                    "child_rows": len(pool.child_rows),
                    "ordered": order_by is not None,
                    "sequence_length": {
                        "source_mean": round(sum(source_lengths) / max(1, len(source_lengths)), 4),
                        "synthetic_mean": round(
                            sum(synthetic_lengths) / max(1, len(synthetic_lengths)), 4
                        ),
                        "ks": round(ks_statistic(source_lengths, synthetic_lengths), 4),
                    },
                },
            },
        }

    metrics = await asyncio.to_thread(measure_linked)
    privacy = metrics["privacy"]
    if privacy["exact_row_matches"]:
        raise TabularGateError("leakage", "generated rows equal sampled real rows")
    if settings.privacy_nndr_enforce and not privacy["nndr"]["passed"]:
        raise TabularGateError(
            "privacy_gate", "nearest-neighbour distance ratio is below the configured target"
        )
    mia = privacy["membership_inference"]
    if settings.privacy_nndr_enforce and mia.get("run") and not mia.get("passed"):
        raise TabularGateError(
            "privacy_gate", "the membership-inference proxy AUC is above the configured target"
        )
    return LinkedPrepared(
        model_b64=base64.b64encode(pool.to_bytes()).decode("ascii"),
        metrics=metrics,
        parent_columns=_output_columns(parent_shape),
        child_columns=_output_columns(child_shape),
        parent_rows=len(pool.parent_rows),
        child_rows=len(pool.child_rows),
    )


def load_linked_pool(model_b64: str, seed: bytes) -> Any:
    """The sealed :class:`~dps.engine.synth.linked.LinkedPool` (cached per process by digest).

    Raises:
        TabularUnavailable: the bytes are not a linked pool.
    """
    from dps.engine.synth.linked import LinkedPool

    key = ("linked", hashlib.sha256(model_b64.encode("ascii")).hexdigest(), "")
    cached = _LINKED_CACHE.get(key)
    if cached is not None:
        _LINKED_CACHE.move_to_end(key)
        return cached
    pool = LinkedPool.from_bytes(base64.b64decode(model_b64), seed)
    _LINKED_CACHE[key] = pool
    while len(_LINKED_CACHE) > _CACHE_MAX:
        _LINKED_CACHE.popitem(last=False)
    return pool


_LINKED_CACHE: OrderedDict[tuple[str, str, str], Any] = OrderedDict()
