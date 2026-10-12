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
    encode_features,
    exact_row_matches,
    fidelity_report,
    generalize_qi,
    nndr,
    qi_unique_overlap,
    qi_uniqueness,
    row_hash,
)
from dps.engine.policy import Policy
from dps.engine.synth.constraints import ColumnSpec, ConstraintError, ConstraintSet
from dps.engine.synth.copula import CopulaError, GaussianCopula
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
#: Decimal digits an integer column holds when the catalogue reports no precision (PostgreSQL),
#: so a ``unique`` constraint never draws a number its column cannot store (Task 4 note).
INT_DIGITS: dict[str, int] = {
    "smallint": 4,
    "int2": 4,
    "integer": 9,
    "int": 9,
    "int4": 9,
    "bigint": 18,
    "int8": 18,
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
) -> TabularSampler:
    """Fit ``engine`` on ``train`` (CPU-bound: run it in a thread).

    Raises:
        CopulaError: the copula cannot be fitted (value-free).
    """
    data = {c.path: [r.get(c.name) for r in train] for c in columns}
    kinds = {c.path: c.kind for c in columns}
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
        CopulaError: the bytes are not a model of the engine's format.
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
    sampler: TabularSampler = GaussianCopula.from_bytes(data, seed)
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
) -> dict[str, Any]:
    """Fidelity and privacy of the preview (value-free: numbers, column paths, counts)."""
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
            "membership_inference": {
                "run": False,
                "reason": f"not available for the {engine} engine",
            },
        },
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
) -> TabularPrepared:
    """Fit, preview, measure and gate. Never writes.

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
    key_names = set(shape.key)
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
        )
    except CopulaError as exc:
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
    if metrics["privacy"]["exact_row_matches"]:
        raise TabularGateError("leakage", "synthetic preview rows equal real rows")
    if settings.privacy_nndr_enforce and not metrics["privacy"]["nndr"]["passed"]:
        raise TabularGateError(
            "privacy_gate", "nearest-neighbour distance ratio is below the configured target"
        )
    engine_literal: Literal["copula", "mostly"] = "mostly" if engine_name == "mostly" else "copula"
    return TabularPrepared(
        engine=engine_literal,
        model_b64=base64.b64encode(sampler.to_bytes()).decode("ascii"),
        metrics=metrics,
    )
