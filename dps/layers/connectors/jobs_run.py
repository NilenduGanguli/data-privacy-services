"""Run job handlers: ``prepare`` → partitions → ``finalize`` (contract §3.1, spec §7 "Execution").

``prepare`` (one fenced job row) opens the sealed material, checks the live table against the
accepted snapshot, counts the selection, builds the column plans and the resolved target,
transforms a preflight sample (nothing is written before it passes), pre-checks append key
collisions, prepares the target (create/truncate, ledger, store probe) and splits the key space
into partitions, storing the value-free :class:`~dps.layers.connectors.targets.RunPlan`.

``run_partition`` (one fenced partition) reads keyset pages, transforms them in a thread, writes
each batch with its ledger mark in ONE target transaction and checkpoints after the commit. A
resumed ``in_place``/``append`` partition takes the target ledger as authoritative (it can be one
batch ahead of the checkpoint after a crash), so no batch is ever applied twice; ``table`` resumes
after its checkpoint (idempotent upserts) and ``files`` restarts from its lower bound.

**Tabular synthesis** (M5, contract §6.6): a synthesize run with ``options.tabular`` fits the
engine in ``prepare`` on a bounded sample, previews it through the real transformer, measures and
gates it before anything is written (:mod:`dps.layers.connectors.tabular`), re-seals the job
secret with the fitted model (``jobs.reseal_secret``) and stores the value-free figures in
``progress.metrics``; partitions load the sealed model (and the policy's constraints) once per
runtime.

**Linked generation** (M5 Task 7B, ``options.linked``): ``prepare`` trains the MOSTLY generator on
the parent and child tables, measures and gates the generated dataset, seals it as the job's
tabular model and adds two partitions (0 = parent, 1 = child). Each partition writes its table's
generated rows to its own folder of the files target (``<files.prefix>/<table>/<job_id>/part-…``);
a restarted partition rewrites its objects (deterministic content). ``finalize`` writes one
manifest (``<files.prefix>/<job_id>/manifest.json``) with a ``tables`` map.

``finalize`` (one fenced job row) decides the outcome, writes the files manifest last, clears the
write ledger of a finished job, samples written rows for the evidence, stores the evidence pack,
audits ``job.finalize`` and moves the job to its terminal state, purging the sealed key.

Nothing here logs or stores a value, key, seed or selection literal; failures carry codes and
exception class names only.
"""

from __future__ import annotations

import asyncio
import contextlib
import tempfile
import time
import uuid
from collections import Counter, defaultdict
from collections.abc import AsyncIterator, Awaitable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from pydantic import ValidationError

from dps import __version__, db, jobs
from dps.audit import AuditEvent
from dps.connections import (
    Connection,
    ConnectionStoreError,
    get_connection,
    load_credentials,
)
from dps.engine.batch import (
    NUMERIC_KINDS,
    BatchResult,
    BatchTransformer,
    IncompatibleMethod,
    LeakageError,
    PlanConflict,
    TabularSampler,
    row_anchor,
)
from dps.engine.detect.ensemble import Detector
from dps.engine.policy import Policy
from dps.engine.service import EngineError
from dps.engine.synth.surrogates import seed_bytes, seed_from_caller
from dps.engine.taxonomy import Sensitivity
from dps.engine.transform.crypto import key_material_from_caller
from dps.engine.transform.external import ExternalServiceError
from dps.jobs import ClaimedJob, ClaimedPartition, JobInternal, JobPhase, JobState, PartitionState
from dps.jobs_models import (
    ClassificationSnapshot,
    JobSecret,
    RunSpec,
    SealedSelection,
    Selection,
    SpecError,
)
from dps.layers.connectors.base import (
    BatchWriter,
    ColumnInfo,
    Connector,
    ConnectorError,
    Engine,
    KeyRange,
    LedgerConflict,
    LedgerMark,
    PkOffset,
    PkToken,
    PrepareReport,
    Row,
    TableShape,
    decode_key,
    encode_key,
    to_engine_text,
)
from dps.layers.connectors.evidence import EvidenceInputs, build_pack, save_evidence
from dps.layers.connectors.files import (
    FilesWriter,
    build_manifest,
    effective_compression,
    manifest_bytes,
    write_manifest,
)
from dps.layers.connectors.identifier_check import check_identifiers, identifier_columns
from dps.layers.connectors.partitions import partition_count, plan_partitions
from dps.layers.connectors.postgres import PkCollision
from dps.layers.connectors.registry import open_connector, open_target_store
from dps.layers.connectors.selection import SelectionError, validate_selection
from dps.layers.connectors.targets import (
    PrepareFailure,
    RunPlan,
    apply_pk_strategy,
    build_column_plans,
    kept_attribute,
    pseudonym_columns,
    resolve_target,
)
from dps.observability import LEASES_LOST, ROWS_WRITTEN, get_logger
from dps.secrets import SealError, SecretUnavailable, open_json, seal_json
from dps.snapshots import get_snapshot
from dps.storage import ObjectStore, StorageError

if TYPE_CHECKING:
    from dps.engine.synth.constraints import ConstraintSet
    from dps.worker import HandlerContext

logger = get_logger(__name__)

#: Rows a ``finalize`` re-reads from a database target for the post-write detector sample.
POST_SAMPLE_ROWS = 200
#: Keys per streamed page of the append collision pre-check.
PRECHECK_PAGE = 10_000
#: How long a cancel-finalize waits before checking canceled partitions' leases again.
CANCEL_WAIT_SECONDS = 5.0
_TERMINAL = {
    "succeeded": JobState.succeeded,
    "dry_run": JobState.succeeded,
    "failed": JobState.failed,
    "canceled": JobState.canceled,
}


class _Lost(Exception):
    """A fenced write found the lease gone: stop without touching the row again."""


def _constraint_errors() -> tuple[type[Exception], ...]:
    """``ConstraintError`` when the ``tabular`` extra is installed (M2 runs work without it)."""
    try:
        from dps.engine.synth.constraints import ConstraintError
    except ImportError:  # pragma: no cover - the image and the dev venv install the extra
        return ()
    return (ConstraintError,)


#: A tabular constraint the table cannot take, raised while transforming (value-free).
CONSTRAINT_ERRORS = _constraint_errors()


@dataclass
class RunRuntime:
    """What a worker process opens once per running job (connectors, writer, transformer)."""

    job_id: str
    spec: RunSpec
    plan: RunPlan
    shape: TableShape
    source: Connector
    target: Connector | None
    store: ObjectStore | None
    writer: BatchWriter
    transformer: BatchTransformer
    users: int = 0
    last_used: float = field(default_factory=time.monotonic)

    async def close(self) -> None:
        """Close the writer and the connectors (errors are swallowed: best effort)."""
        with contextlib.suppress(Exception):
            await self.writer.close()
        if self.target is not None and self.target is not self.source:
            with contextlib.suppress(Exception):
                await self.target.close()
        with contextlib.suppress(Exception):
            await self.source.close()


# -- shared helpers -------------------------------------------------------------------------------


def _secret(ctx: HandlerContext, job: JobInternal) -> JobSecret:
    if job.key_enc is None:
        raise SecretUnavailable()
    return open_json(ctx.kek, job.key_enc, JobSecret, aad=f"dps_job.key:{job.id}")


def _has_selection(sel: Selection) -> bool:
    return bool(sel.where or sel.query or sel.mongo_filter is not None or sel.params)


def _spec(ctx: HandlerContext, job: JobInternal) -> RunSpec:
    """The stored spec with the sealed selection (literals and binds) put back."""
    try:
        spec = RunSpec.model_validate(job.spec)
    except ValidationError:
        raise PrepareFailure("spec_invalid", "the stored run spec is not valid") from None
    if job.spec_enc is None:
        if _has_selection(spec.source.selection):
            raise SecretUnavailable()
        sealed = SealedSelection()
    else:
        sealed = open_json(ctx.kek, job.spec_enc, SealedSelection, aad=f"dps_job.spec:{job.id}")
    selection = spec.source.selection.model_copy(update=sealed.model_dump())
    return spec.model_copy(
        update={"source": spec.source.model_copy(update={"selection": selection})}
    )


async def _connections(job: JobInternal, spec: RunSpec) -> tuple[Connection, Connection | None]:
    async with db.acquire_queue(job.tenant_id) as conn:
        source = await get_connection(job.tenant_id, spec.source.connection_id, conn=conn)
        target = (
            await get_connection(job.tenant_id, spec.target.connection_id, conn=conn)
            if spec.target.connection_id
            else None
        )
    if source is None or (spec.target.connection_id and target is None):
        raise PrepareFailure("connection_missing", "a connection of this job no longer exists")
    return source, target


async def _open(ctx: HandlerContext, job: JobInternal, conn: Connection, action: str) -> Connector:
    async with db.acquire_queue(job.tenant_id) as c:
        creds = await load_credentials(conn, settings=ctx.settings, kek=ctx.kek, conn=c)
    return await open_connector(
        conn, ctx.settings, action=action, kek=ctx.kek, job_id=job.id, credentials=creds
    )


def _policy(ctx: HandlerContext, spec: RunSpec, name: str) -> Policy:
    try:
        return ctx.engine.policy(spec.policy or name)
    except EngineError:
        raise PrepareFailure("policy_invalid", "the run policy is not valid") from None


def _transformer(
    ctx: HandlerContext,
    spec: RunSpec,
    secret: JobSecret,
    plan: RunPlan,
    policy: Policy,
    shape: TableShape,
    *,
    tabular: TabularSampler | None = None,
    constraints: ConstraintSet | None = None,
) -> BatchTransformer:
    if spec.mode == "obfuscate" and not secret.key_material:
        raise SecretUnavailable()
    if spec.mode == "synthesize" and not secret.synthesis_seed:
        raise SecretUnavailable()
    key = key_material_from_caller(secret.key_material) if secret.key_material else None
    seed = seed_from_caller(secret.synthesis_seed) if secret.synthesis_seed else None
    anchor = (plan.anchor[0], plan.anchor[1]) if plan.anchor and len(plan.anchor) == 2 else None
    return BatchTransformer(
        ctx.engine,
        policy=policy,
        mode=spec.mode,
        columns=plan.columns,
        shape=shape,
        key=key if spec.mode == "obfuscate" else None,
        seed=seed if spec.mode == "synthesize" else None,
        locale=plan.locale,
        unclassified=spec.options.unclassified,
        anchor=anchor,
        verify=spec.options.verify,
        tabular=tabular,
        constraints=constraints,
        max_resample=ctx.settings.copula_max_resample,
        pseudonyms=plan.pseudonyms if spec.mode == "synthesize" else (),
    )


def _same_connection(spec: RunSpec, source: Connection) -> bool:
    return spec.target.write_mode in ("in_place", "append") and spec.target.connection_id in (
        None,
        source.id,
    )


def _files_writer(
    store: ObjectStore,
    spec: RunSpec,
    target_conn: Connection,
    job_id: str,
    shape: TableShape,
    projection: list[str],
) -> FilesWriter:
    assert spec.target.files is not None  # validate_run guarantees it for files targets
    return FilesWriter(
        store,
        spec.target.files,
        job_id=job_id,
        base_prefix=target_conn.params.prefix,
        columns=[shape.column(n) for n in projection],
    )


def _counters(result: BatchResult) -> dict[str, int]:
    counters = {
        **result.values_by_method,
        **{f"fallback:{k}": v for k, v in result.fallbacks.items()},
        **{f"col:{k}": v for k, v in result.values_by_column.items()},
    }
    if result.verification.hits:
        counters["verify:hits"] = len(result.verification.hits)
    return counters


async def _stage(ctx: HandlerContext, job: ClaimedJob, stage: str) -> None:
    if not await jobs.advance_job(
        ctx.worker_id, job.id, phase=job.phase, state=JobState.running, progress={"stage": stage}
    ):
        raise _Lost()


async def _fail_to_finalize(ctx: HandlerContext, job: ClaimedJob, code: str, detail: str) -> None:
    """A prepare failure: finalize writes the failed evidence pack and purges the key."""
    if not await jobs.advance_job(
        ctx.worker_id,
        job.id,
        phase=JobPhase.finalize,
        state=JobState.queued,
        progress={"outcome": "failed", "stage": ""},
        error_code=code,
        error=detail[:500],
    ):
        LEASES_LOST.labels("job").inc()


async def _retry(ctx: HandlerContext, job: ClaimedJob, code: str, detail: str) -> None:
    await jobs.retry_job(
        ctx.worker_id,
        job.id,
        error_code=code,
        error=detail,
        backoff_base=ctx.settings.job_retry_base_seconds,
        backoff_cap=ctx.settings.job_retry_max_seconds,
    )


# -- prepare ---------------------------------------------------------------------------------------


async def prepare(ctx: HandlerContext, job: ClaimedJob) -> None:
    """Contract §3.1 ``prepare``: plan, preflight, prepare the target, add partitions.

    Every refusal moves the job to a failed finalize with a code (``snapshot_stale``,
    ``no_key``, ``row_count_mismatch``, ``target_invalid``, ``length_overflow``,
    ``incompatible_method``, ``leakage``, ``pk_collision``, ``secret_unavailable``,
    ``plan_conflict``, a selection or connector code); a retryable connector failure is retried with backoff; anything unexpected
    is retried as ``internal_error`` (class name only).
    """
    try:
        await _prepare(ctx, job)
    except _Lost:
        LEASES_LOST.labels("job").inc()
    except PrepareFailure as exc:
        await _fail_to_finalize(ctx, job, exc.code, exc.detail)
    except (SpecError, SelectionError) as exc:
        await _fail_to_finalize(ctx, job, exc.code, str(exc))
    except IncompatibleMethod as exc:
        await _fail_to_finalize(ctx, job, exc.code, str(exc))
    except CONSTRAINT_ERRORS as exc:
        await _fail_to_finalize(ctx, job, "incompatible_method", str(exc))
    except LeakageError as exc:
        await _fail_to_finalize(ctx, job, "leakage", str(exc))
    except PlanConflict as exc:  # deterministic: a retry would refuse it again (value-free)
        await _fail_to_finalize(ctx, job, "plan_conflict", str(exc))
    except (SealError, SecretUnavailable):
        await _fail_to_finalize(
            ctx, job, "secret_unavailable", "the job's sealed material could not be opened"
        )
    except ConnectionStoreError as exc:
        await _fail_to_finalize(ctx, job, exc.code, exc.detail)
    except (ConnectorError, StorageError) as exc:
        retryable = isinstance(exc, ConnectorError) and exc.retryable
        if retryable and job.attempts < job.max_attempts:
            await _retry(ctx, job, exc.code, str(exc))
        else:
            await _fail_to_finalize(ctx, job, exc.code, str(exc))
    except ExternalServiceError:  # M5: the bank tokenization service failed — transient
        if job.attempts < job.max_attempts:
            await _retry(ctx, job, "tokenization_unavailable", "tokenization service unavailable")
        else:
            await _fail_to_finalize(
                ctx, job, "tokenization_unavailable", "tokenization service unavailable"
            )
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # unexpected: retry; never put the message in the row
        logger.error("prepare_failed", job_id=job.id, error=type(exc).__name__)
        await _retry(ctx, job, "internal_error", type(exc).__name__)


def _check_snapshot(snapshot: ClassificationSnapshot, shape: TableShape) -> None:
    for col in snapshot.columns:
        try:
            live = shape.column(col.name)
        except KeyError:
            raise PrepareFailure(
                "snapshot_stale", f"column {col.path} is no longer in the table"
            ) from None
        if live.kind != col.kind:
            raise PrepareFailure("snapshot_stale", f"column {col.path} changed type since review")


async def _prepare(ctx: HandlerContext, job: ClaimedJob) -> None:
    settings = ctx.settings
    spec, secret = _spec(ctx, job), _secret(ctx, job)
    source_conn, target_conn = await _connections(job, spec)
    async with db.acquire_queue(job.tenant_id) as conn:
        snapshot = await get_snapshot(job.tenant_id, spec.snapshot_id, conn=conn)
    if snapshot is None:
        raise PrepareFailure("snapshot_stale", "the accepted snapshot no longer exists")
    policy = _policy(ctx, spec, snapshot.policy_name)
    mode = spec.target.write_mode
    await _stage(ctx, job, "connecting")
    source = await _open(ctx, job, source_conn, "prepare")
    target: Connector | None = None
    try:
        shape = await source.describe(spec.source.object)
        _check_snapshot(snapshot, shape)
        if not shape.key:
            raise PrepareFailure("no_key", "the table has no primary key or unique not-null index")
        selection = spec.source.selection
        validate_selection(selection, cast(Engine, source.engine), shape)
        if spec.options.linked is not None:
            await _prepare_linked(
                ctx, job, spec, secret, snapshot, policy, source, shape, source_conn, target_conn
            )
            return
        projection = [
            c.name
            for c in shape.columns
            if not selection.columns or c.name in set(selection.columns) | set(shape.key)
        ]
        reviewed = {c.name for c in snapshot.columns}
        unreviewed = [n for n in projection if n not in reviewed]
        if unreviewed and mode != "in_place" and spec.options.unclassified != "keep":
            raise PrepareFailure(
                "snapshot_stale",
                f"{len(unreviewed)} column(s) were added since review: discover again",
            )
        await _stage(ctx, job, "counting rows")
        rows = await source.count(shape, selection, selection.params)
        if spec.target.expected_rows is not None and rows > spec.target.expected_rows:
            raise PrepareFailure(
                "row_count_mismatch",
                f"{rows} rows are selected but {spec.target.expected_rows} were confirmed",
            )
        plans = build_column_plans(
            snapshot,
            mode=spec.mode,
            write_mode=mode,
            pk_strategy=spec.target.pk_strategy,
            projection=projection,
        )
        pseudonyms = pseudonym_columns(
            snapshot,
            mode=spec.mode,
            write_mode=mode,
            pk_strategy=spec.target.pk_strategy,
            projection=projection,
            shape=shape,
        )
        store: ObjectStore | None = None
        target_shape: TableShape | None = None
        if mode == "files":
            assert target_conn is not None  # validate_run: files targets name a connection
            try:
                store = await open_target_store(target_conn, settings, kek=ctx.kek)
            except ValueError:
                raise PrepareFailure("target_invalid", "the files target is not usable") from None
        else:
            if _same_connection(spec, source_conn):
                target = source
            else:
                assert target_conn is not None
                target = await _open(ctx, job, target_conn, "prepare")
            try:
                target_shape = await target.describe(spec.target.object or spec.source.object)
            except ConnectorError as exc:
                if exc.code != "object_not_found":
                    raise
        resolved, plans = resolve_target(
            spec,
            job_id=job.id,
            source_conn=source_conn,
            target_conn=target_conn or source_conn,
            source_shape=shape,
            target_shape=target_shape,
            plans=plans,
            projection=projection,
            pseudonyms=[p.name for p in pseudonyms],
        )
        anchor = next(
            ([c.name, c.entity_type] for c in snapshot.columns if c.anchor and c.entity_type),
            None,
        )
        plan = RunPlan(
            columns=plans,
            anchor=anchor,
            key=list(shape.key),
            projection=projection,
            target=resolved,
            source_object=shape.object.fqn(),
            policy_name=policy.name,
            policy_hash=policy.config_hash(),
            snapshot_id=snapshot.id,
            snapshot_hash=snapshot.content_hash,
            locale=policy.locale.default or settings.default_locale,
            batch_rows=spec.source.batch_rows or settings.batch_rows,
            same_table_append=mode == "append" and resolved.same_as_source,
            pseudonyms=pseudonyms,
        )
        tabular: TabularSampler | None = None
        constraints: ConstraintSet | None = None
        metrics: dict[str, Any] = {}
        if spec.mode == "synthesize" and spec.options.tabular != "none":
            await _stage(ctx, job, "fitting the tabular model")
            secret, tabular, constraints, metrics = await _prepare_tabular(
                ctx, job, spec, secret, plan, policy, shape, snapshot, source
            )
        transformer = _transformer(
            ctx, spec, secret, plan, policy, shape, tabular=tabular, constraints=constraints
        )
        if spec.mode == "synthesize":
            copied = copied_personal_columns(
                plan, snapshot, set(tabular.columns) if tabular is not None else set()
            )
            if copied:  # D-A invariant (G2 review r1): never reached, fails closed if it is
                raise PrepareFailure(
                    "leakage",
                    f"{len(copied)} classified column(s) or key(s) would be copied into "
                    "synthetic rows; nothing was written",
                )
        await _stage(ctx, job, "preflight")
        sample = await source.sample(
            shape, selection, selection.params, max(1, min(settings.preflight_rows, rows))
        )
        preflight = await asyncio.to_thread(transformer.transform, sample)
        del sample
        kept_prewrite: dict[str, Any] | None = None
        if spec.mode == "synthesize":
            kept_prewrite = await asyncio.to_thread(
                kept_review,
                preflight.rows,
                shape,
                kept=synthetic_kept_columns(
                    plan,
                    snapshot,
                    [c.name for c in shape.columns],
                    set(tabular.columns) if tabular is not None else set(),
                ),
                detector=ctx.engine.detector(policy, plan.locale),
                min_score=policy.detect.min_score,
            )
        writer: BatchWriter
        if mode == "files":
            assert store is not None and target_conn is not None
            writer = _files_writer(store, spec, target_conn, job.id, shape, projection)
        else:
            assert target is not None
            writer = await target.writer(resolved, shape)
        collisions = 0
        strategy = spec.target.pk_strategy
        if mode == "append" and strategy is not None and strategy.kind in ("offset", "token"):
            await _stage(ctx, job, "checking key collisions")
            collisions = await writer.precheck_collisions(
                _transformed_keys(source, shape, selection, spec, transformer)
            )
            if collisions:
                raise PrepareFailure(
                    "pk_collision", f"{collisions} transformed keys already exist in the target"
                )
        await _stage(ctx, job, "preparing target")
        report = await writer.prepare(dry_run=spec.options.dry_run)
        if plan.same_table_append and isinstance(strategy, PkOffset):
            await _check_same_table_offset(source, shape, selection, strategy, report)
        plan.prepare = {
            "created": report.created,
            "truncated": report.truncated,
            "collisions": collisions,
            "existing_rows": report.existing_rows,
        }
        if kept_prewrite is not None:
            plan.prepare["kept_review"] = kept_prewrite
        base: dict[str, Any] = {"rows_estimated": rows, "stage": ""}
        if metrics:
            base["metrics"] = metrics
        if spec.options.dry_run:
            if not await jobs.advance_job(
                ctx.worker_id,
                job.id,
                phase=JobPhase.finalize,
                state=JobState.queued,
                plan=plan.model_dump(mode="json"),
                progress={
                    **base,
                    "outcome": "dry_run",
                    "values_by_method": _counters(preflight),
                    "verification": {
                        "checked": preflight.verification.checked,
                        "hits": len(preflight.verification.hits),
                    },
                },
            ):
                raise _Lost()
            return
        max_rows = selection.max_rows
        total = min(rows, max_rows) if max_rows else rows
        if total == 0:
            if not await jobs.advance_job(
                ctx.worker_id,
                job.id,
                phase=JobPhase.finalize,
                state=JobState.queued,
                plan=plan.model_dump(mode="json"),
                progress=base,
            ):
                raise _Lost()
            return
        upper = (
            await source.key_at_offset(shape, selection, selection.params, max_rows)
            if max_rows
            else None
        )
        if plan.same_table_append and report.append_upper is not None:
            bound = report.append_upper
            upper = (bound,) if upper is None else (min(int(upper[0]), bound),)
        count = partition_count(
            total,
            partition_rows=spec.source.partition_rows or settings.partition_rows,
            max_partitions=settings.max_partitions,
        )
        keys = await source.sample_keys(
            shape, selection, selection.params, min(count * 50, 20_000), upper=upper
        )
        ranges = plan_partitions(keys, count=count, columns=shape.key_columns(), upper=upper)
        added = await jobs.add_partitions(
            ctx.worker_id, job.id, ranges, plan=plan.model_dump(mode="json"), progress=base
        )
        if added != len(ranges):
            raise _Lost()
        logger.info("prepare_done", job_id=job.id, partitions=len(ranges), rows=total)
    finally:
        if target is not None and target is not source:
            await target.close()
        await source.close()


async def _prepare_tabular(
    ctx: HandlerContext,
    job: ClaimedJob,
    spec: RunSpec,
    secret: JobSecret,
    plan: RunPlan,
    policy: Policy,
    shape: TableShape,
    snapshot: ClassificationSnapshot,
    source: Connector,
) -> tuple[JobSecret, TabularSampler, ConstraintSet | None, dict[str, Any]]:
    """Fit, preview, measure and gate the tabular engine; re-seal the secret with the model.

    Returns the re-sealed secret, the sampler and constraints the preflight and partitions use,
    and the value-free metrics for ``progress.metrics``.

    Raises:
        PrepareFailure: a tabular gate (``tabular_invalid``, ``incompatible_method``,
            ``leakage``, ``privacy_gate``); ``_Lost`` when the job row's lease is gone.
    """
    from dps.layers.connectors.tabular import TabularGateError, prepare_tabular, runtime_sampler

    if not secret.synthesis_seed:
        raise SecretUnavailable()
    seed = seed_bytes(seed_from_caller(secret.synthesis_seed))
    selection = spec.source.selection

    def make(sampler: TabularSampler, constraints: ConstraintSet | None) -> BatchTransformer:
        # D-A B1: a kept attribute the engine models is drawn, not surrogated (its plan goes;
        # the stored plan the partitions rebuild from is the one without it)
        drawn = set(sampler.columns)
        plan.columns = [p for p in plan.columns if p.name not in drawn or p.name not in personal]
        return _transformer(
            ctx, spec, secret, plan, policy, shape, tabular=sampler, constraints=constraints
        )

    personal = {c.name for c in snapshot.columns if kept_attribute(c, set(shape.key))}

    try:
        prepared = await prepare_tabular(
            settings=ctx.settings,
            engine_name=spec.options.tabular,
            connector=source,
            shape=shape,
            selection=selection,
            params=selection.params,
            snapshot=snapshot,
            policy=policy,
            seed=seed,
            object_fqn=plan.source_object,
            make_transformer=make,
            anchor_of=lambda row: row_anchor(row, shape, None),
            projection=plan.projection,
            dp=_dp_config(ctx, spec),
            pseudonyms=[p.name for p in plan.pseudonyms],
        )
    except TabularGateError as exc:
        raise PrepareFailure(exc.code, exc.detail) from None
    resealed = secret.model_copy(update={"tabular_model": prepared.model_b64})
    sealed = seal_json(ctx.kek, resealed, aad=f"dps_job.key:{job.id}")
    if not await jobs.reseal_secret(ctx.worker_id, job.id, sealed):
        raise _Lost()  # the reaper re-queues prepare, which refits deterministically
    sampler, constraints = runtime_sampler(
        prepared.engine,
        prepared.model_b64,
        seed,
        policy=policy,
        shape=shape,
        snapshot=snapshot,
        object_fqn=plan.source_object,
    )
    logger.info(
        "tabular_prepared",
        job_id=job.id,
        engine=prepared.engine,
        columns=len(prepared.metrics["tabular"]["columns"]),
    )
    return resealed, sampler, constraints, prepared.metrics


def _dp_config(ctx: HandlerContext, spec: RunSpec) -> Any:
    """The MOSTLY differential-privacy budget of this run (``options.tabular_dp``, else the
    deployment default), or ``None``; the copula takes none."""
    from dps.engine.synth.mostly import DpConfig

    settings = ctx.settings
    wanted = spec.options.tabular_dp
    enabled = settings.mostly_dp_enabled if wanted is None else wanted
    if spec.options.tabular != "mostly" or not enabled:
        return None
    return DpConfig(max_epsilon=settings.mostly_dp_max_epsilon, delta=settings.mostly_dp_delta)


def _linked_dirs(parent: str, child: str) -> tuple[str, str]:
    """Folder names of the two generated tables (their object names, kept apart)."""
    return parent, child if child != parent else f"{child}_child"


def _linked_writer(
    store: ObjectStore,
    spec: RunSpec,
    target_conn: Connection,
    job_id: str,
    table: dict[str, Any],
    *,
    spool: str = "",
) -> FilesWriter:
    """A files writer for one generated table: ``<files.prefix>/<table dir>/<job_id>/part-…``."""
    files = spec.target.files
    assert files is not None  # validate_run: linked generation writes files
    prefix = "/".join(p for p in (files.prefix.strip("/"), table["dir"]) if p)
    columns = [
        ColumnInfo(path=f"$.{c['name']}", name=c["name"], data_type=c["data_type"], kind=c["kind"])
        for c in table["columns"]
    ]
    root = Path(tempfile.gettempdir()) / "dps-spool" / job_id / f"linked-{spool or table['dir']}"
    return FilesWriter(
        store,
        files.model_copy(update={"prefix": prefix}),
        job_id=job_id,
        base_prefix=target_conn.params.prefix,
        columns=columns,
        spool_root=root,
    )


async def _prepare_linked(
    ctx: HandlerContext,
    job: ClaimedJob,
    spec: RunSpec,
    secret: JobSecret,
    snapshot: ClassificationSnapshot,
    policy: Policy,
    source: Connector,
    shape: TableShape,
    source_conn: Connection,
    target_conn: Connection | None,
) -> None:
    """Task 7B: train and gate the linked generator, seal its pool, add the two partitions."""
    from dps.layers.connectors.tabular import TabularGateError, prepare_linked

    settings = ctx.settings
    linked = spec.options.linked
    assert linked is not None
    if spec.target.write_mode != "files" or target_conn is None or spec.target.files is None:
        raise PrepareFailure("tabular_invalid", "linked generation writes a files target")
    if not secret.synthesis_seed:
        raise SecretUnavailable()
    async with db.acquire_queue(job.tenant_id) as conn:
        child_snapshot = await get_snapshot(job.tenant_id, linked.child_snapshot_id, conn=conn)
    if child_snapshot is None or child_snapshot.status != "accepted":
        raise PrepareFailure("snapshot_stale", "the child table's accepted snapshot is gone")
    child_shape = await source.describe(linked.child)
    _check_snapshot(child_snapshot, child_shape)
    try:
        store = await open_target_store(target_conn, settings, kek=ctx.kek)
    except ValueError:
        raise PrepareFailure("target_invalid", "the files target is not usable") from None
    dp = _dp_config(ctx, spec)
    await _stage(ctx, job, "training the linked generator")
    selection = spec.source.selection
    try:
        prepared = await prepare_linked(
            settings=settings,
            connector=source,
            parent_shape=shape,
            parent_snapshot=snapshot,
            child_shape=child_shape,
            child_snapshot=child_snapshot,
            linked=linked,
            selection=selection,
            params=selection.params,
            seed=seed_bytes(seed_from_caller(secret.synthesis_seed)),
            dp=dp,
            locale=policy.locale.default or settings.default_locale,
        )
    except TabularGateError as exc:
        raise PrepareFailure(exc.code, exc.detail) from None
    parent_dir, child_dir = _linked_dirs(shape.object.name, child_shape.object.name)
    tables = {
        "parent": {
            "object": shape.object.fqn(),
            "dir": parent_dir,
            "columns": prepared.parent_columns,
            "rows": prepared.parent_rows,
        },
        "child": {
            "object": child_shape.object.fqn(),
            "dir": child_dir,
            "columns": prepared.child_columns,
            "rows": prepared.child_rows,
        },
    }
    probe = _linked_writer(store, spec, target_conn, job.id, tables["parent"], spool="probe")
    try:
        await probe.prepare(dry_run=spec.options.dry_run)
    finally:
        await probe.close()
    resealed = secret.model_copy(update={"tabular_model": prepared.model_b64})
    sealed = seal_json(ctx.kek, resealed, aad=f"dps_job.key:{job.id}")
    if not await jobs.reseal_secret(ctx.worker_id, job.id, sealed):
        raise _Lost()
    projection = [c["name"] for c in prepared.parent_columns]
    resolved, _ = resolve_target(
        spec,
        job_id=job.id,
        source_conn=source_conn,
        target_conn=target_conn,
        source_shape=shape,
        target_shape=None,
        plans=[],
        projection=projection,
    )
    plan = RunPlan(
        columns=[],
        anchor=None,
        key=list(shape.key),
        projection=projection,
        target=resolved,
        source_object=shape.object.fqn(),
        policy_name=policy.name,
        policy_hash=policy.config_hash(),
        snapshot_id=snapshot.id,
        snapshot_hash=snapshot.content_hash,
        locale=policy.locale.default or settings.default_locale,
        batch_rows=spec.source.batch_rows or settings.batch_rows,
        prepare={
            "created": False,
            "truncated": False,
            "collisions": 0,
            "existing_rows": None,
            "linked": tables,
        },
    )
    base: dict[str, Any] = {
        "rows_estimated": prepared.parent_rows + prepared.child_rows,
        "stage": "",
        "metrics": prepared.metrics,
    }
    if spec.options.dry_run:
        if not await jobs.advance_job(
            ctx.worker_id,
            job.id,
            phase=JobPhase.finalize,
            state=JobState.queued,
            plan=plan.model_dump(mode="json"),
            progress={**base, "outcome": "dry_run"},
        ):
            raise _Lost()
        return
    added = await jobs.add_partitions(
        ctx.worker_id,
        job.id,
        [KeyRange(), KeyRange()],
        plan=plan.model_dump(mode="json"),
        progress=base,
    )
    if added != 2:
        raise _Lost()
    logger.info("prepare_done", job_id=job.id, partitions=2, rows=base["rows_estimated"])


async def _runtime_tabular(
    job: JobInternal,
    spec: RunSpec,
    secret: JobSecret,
    plan: RunPlan,
    policy: Policy,
    shape: TableShape,
) -> tuple[TabularSampler, ConstraintSet | None]:
    """The sealed model and its constraints for this job's partitions (once per runtime)."""
    from dps.engine.synth.copula import CopulaError
    from dps.engine.synth.mostly import TabularUnavailable
    from dps.layers.connectors.tabular import runtime_sampler

    if not secret.synthesis_seed or not secret.tabular_model:
        raise SecretUnavailable()
    async with db.acquire_queue(job.tenant_id) as conn:
        snapshot = await get_snapshot(job.tenant_id, spec.snapshot_id, conn=conn)
    if snapshot is None:
        raise PrepareFailure("snapshot_stale", "the accepted snapshot no longer exists")
    try:
        return runtime_sampler(
            spec.options.tabular,
            secret.tabular_model,
            seed_bytes(seed_from_caller(secret.synthesis_seed)),
            policy=policy,
            shape=shape,
            snapshot=snapshot,
            object_fqn=plan.source_object,
        )
    except (CopulaError, TabularUnavailable):
        raise PrepareFailure(
            "tabular_invalid", "the sealed tabular model could not be read"
        ) from None
    except CONSTRAINT_ERRORS as exc:
        raise PrepareFailure("incompatible_method", str(exc)) from None


async def _check_same_table_offset(
    source: Connector,
    shape: TableShape,
    selection: Selection,
    strategy: PkOffset,
    report: PrepareReport,
) -> None:
    """A same-table ``offset`` must lift every selected key above the table's largest key.

    The read range is clipped to ``append_upper`` (``max(key) + 1``), but a shifted key below
    that bound (a key gap wider than the offset) would sit inside a partition range and be read
    and appended again; requiring ``min(selected key) + offset >= append_upper`` rules it out.
    """
    if report.append_upper is None:
        return  # an empty table: nothing is selected
    first = await source.key_at_offset(shape, selection, selection.params, 0)
    if first is not None and int(first[0]) + (strategy.offset or 0) < report.append_upper:
        raise PrepareFailure(
            "pk_strategy_invalid",
            "a same-table offset must lift every selected key above the table's largest key",
        )


async def _transformed_keys(
    source: Connector,
    shape: TableShape,
    selection: Selection,
    spec: RunSpec,
    transformer: BatchTransformer,
) -> AsyncIterator[list[tuple[Any, ...]]]:
    """The selection's keys as the run will write them (offset applied, or token-transformed)."""
    key_sel = selection.model_copy(update={"columns": list(shape.key)})
    strategy = spec.target.pk_strategy
    assert strategy is not None
    async for batch in source.read_partition(
        shape, key_sel, selection.params, KeyRange(), after_key=None, batch=PRECHECK_PAGE
    ):
        if isinstance(strategy, PkToken):
            batch = (await asyncio.to_thread(transformer.transform, batch)).rows
        else:
            batch = apply_pk_strategy(batch, strategy=strategy, key=shape.key)
        yield [tuple(r[k] for k in shape.key) for r in batch]


# -- runtime cache --------------------------------------------------------------------------------


class RuntimeCache:
    """Per process: one opened source/target/writer/transformer per running job."""

    def __init__(self) -> None:
        self._items: dict[str, RunRuntime] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def job_ids(self) -> list[str]:
        """Jobs with an open runtime in this process (ops and tests)."""
        return list(self._items)

    async def get(self, ctx: HandlerContext, job: JobInternal) -> RunRuntime:
        """The job's runtime, built once from its stored :class:`RunPlan` (``users += 1``)."""
        lock = self._locks.setdefault(job.id, asyncio.Lock())
        async with lock:
            runtime = self._items.get(job.id)
            if runtime is None:
                runtime = await self._build(ctx, job)
                self._items[job.id] = runtime
            runtime.users += 1
            runtime.last_used = time.monotonic()
            return runtime

    async def _build(self, ctx: HandlerContext, job: JobInternal) -> RunRuntime:
        if job.plan is None:
            raise PrepareFailure("spec_invalid", "the job has no run plan")
        plan = RunPlan.model_validate(job.plan)
        spec, secret = _spec(ctx, job), _secret(ctx, job)
        source_conn, target_conn = await _connections(job, spec)
        policy = _policy(ctx, spec, plan.policy_name)
        source = await _open(ctx, job, source_conn, "run")
        target: Connector | None = None
        try:
            shape = await source.describe(spec.source.object)
            store: ObjectStore | None = None
            writer: BatchWriter
            if plan.target.write_mode == "files":
                assert target_conn is not None
                try:
                    store = await open_target_store(target_conn, ctx.settings, kek=ctx.kek)
                except ValueError:
                    raise PrepareFailure(
                        "target_invalid", "the files target is not usable"
                    ) from None
                writer = _files_writer(store, spec, target_conn, job.id, shape, plan.projection)
            else:
                if _same_connection(spec, source_conn):
                    target = source
                else:
                    assert target_conn is not None
                    target = await _open(ctx, job, target_conn, "run")
                writer = await target.writer(plan.target, shape)
            tabular: TabularSampler | None = None
            constraints: ConstraintSet | None = None
            if secret.tabular_model:
                tabular, constraints = await _runtime_tabular(
                    job, spec, secret, plan, policy, shape
                )
            transformer = _transformer(
                ctx, spec, secret, plan, policy, shape, tabular=tabular, constraints=constraints
            )
        except BaseException:
            if target is not None and target is not source:
                with contextlib.suppress(Exception):
                    await target.close()
            with contextlib.suppress(Exception):
                await source.close()
            raise
        return RunRuntime(
            job_id=job.id,
            spec=spec,
            plan=plan,
            shape=shape,
            source=source,
            target=target,
            store=store,
            writer=writer,
            transformer=transformer,
        )

    def release(self, job_id: str) -> None:
        """One user of the job's runtime is done."""
        runtime = self._items.get(job_id)
        if runtime is not None:
            runtime.users = max(0, runtime.users - 1)
            runtime.last_used = time.monotonic()

    async def close_idle(self, idle_seconds: float = 120.0) -> int:
        """Close runtimes nobody used for ``idle_seconds`` (the worker's reaper loop)."""
        now = time.monotonic()
        idle = [
            job_id
            for job_id, rt in self._items.items()
            if rt.users == 0 and now - rt.last_used >= idle_seconds
        ]
        for job_id in idle:
            await self.drop(job_id)
        return len(idle)

    async def drop(self, job_id: str) -> None:
        """Close and forget the job's runtime (finalize)."""
        runtime = self._items.pop(job_id, None)
        self._locks.pop(job_id, None)
        if runtime is not None:
            await runtime.close()

    async def close_all(self) -> None:
        """Close every runtime (worker drain)."""
        for job_id in list(self._items):
            await self.drop(job_id)


RUNTIMES = RuntimeCache()


# -- partitions -----------------------------------------------------------------------------------


async def run_partition(ctx: HandlerContext, job: JobInternal, part: ClaimedPartition) -> None:
    """Contract §3.1: transform and write one partition, checkpointing every committed batch.

    ``leakage`` / ``incompatible_method`` / ``length_overflow`` / ``pk_collision`` /
    ``secret_unavailable`` fail the partition (nothing of the failing batch was written); a lost
    lease or a ledger conflict stops quietly (another holder owns it); anything else is retried
    with backoff and dead-lettered at ``max_attempts``.
    """
    runtime: RunRuntime | None = None
    try:
        if part.job_id != job.id:
            raise ValueError("the partition belongs to another job")
        if job.plan and (job.plan.get("prepare") or {}).get("linked"):
            await _run_linked_partition(ctx, job, part)
            return
        runtime = await RUNTIMES.get(ctx, job)
        await _run_partition(ctx, job, runtime, part)
    except _Lost:
        LEASES_LOST.labels("partition").inc()
    except LedgerConflict:
        LEASES_LOST.labels("ledger").inc()
    except LeakageError as exc:
        await _fail_partition(ctx, part, "leakage", str(exc))
    except IncompatibleMethod as exc:
        await _fail_partition(ctx, part, exc.code, str(exc))
    except PkCollision as exc:
        await _fail_partition(ctx, part, exc.code, str(exc))
    except CONSTRAINT_ERRORS as exc:
        await _fail_partition(ctx, part, "incompatible_method", str(exc))
    except PrepareFailure as exc:
        await _fail_partition(ctx, part, exc.code, exc.detail)
    except (SealError, SecretUnavailable):
        await _fail_partition(
            ctx, part, "secret_unavailable", "the job's sealed material could not be opened"
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # ConnectorError, StorageError and the unexpected: retry
        code = exc.code if isinstance(exc, ConnectorError | StorageError) else "internal_error"
        if isinstance(exc, ExternalServiceError):  # M5: the tokenization service — transient
            code = "tokenization_unavailable"
        if code == "internal_error":
            logger.error("partition_failed", job_id=job.id, error=type(exc).__name__)
        await jobs.retry_partition(
            ctx.worker_id,
            part.id,
            error_code=code,
            error=type(exc).__name__,
            backoff_base=ctx.settings.job_retry_base_seconds,
            backoff_cap=ctx.settings.job_retry_max_seconds,
        )
    finally:
        if runtime is not None:
            RUNTIMES.release(job.id)


async def _run_linked_partition(
    ctx: HandlerContext, job: JobInternal, part: ClaimedPartition
) -> None:
    """Write one generated table of a linked job (0 = parent, 1 = child) to the files target.

    The rows come from the sealed pool, so a restarted partition rewrites the same objects; each
    call has its own writer and spool folder.
    """
    from dps.engine.synth.mostly import TabularUnavailable
    from dps.layers.connectors.tabular import load_linked_pool

    spec, secret = _spec(ctx, job), _secret(ctx, job)
    plan = RunPlan.model_validate(job.plan)
    role = "parent" if part.partition_no == 0 else "child"
    table = plan.prepare["linked"][role]
    if not secret.tabular_model or not secret.synthesis_seed:
        raise SecretUnavailable()
    try:
        pool = load_linked_pool(
            secret.tabular_model, seed_bytes(seed_from_caller(secret.synthesis_seed))
        )
    except TabularUnavailable:
        raise PrepareFailure(
            "tabular_invalid", "the sealed linked pool could not be read"
        ) from None
    rows: list[dict[str, Any]] = pool.parent_rows if role == "parent" else pool.child_rows
    _, target_conn = await _connections(job, spec)
    if target_conn is None:
        raise PrepareFailure("target_invalid", "the files target is gone")
    try:
        store = await open_target_store(target_conn, ctx.settings, kek=ctx.kek)
    except ValueError:
        raise PrepareFailure("target_invalid", "the files target is not usable") from None
    worker = ctx.worker_id
    writer = _linked_writer(
        store,
        spec,
        target_conn,
        job.id,
        table,
        spool=f"p{part.partition_no}-{uuid.uuid4().hex[:8]}",
    )
    position = ColumnInfo(path="$.__row", name="__row", data_type="bigint", kind="int")
    try:
        if (part.batch_no > 0 or part.attempts > 1) and not await jobs.reset_partition_progress(
            worker, part.id
        ):
            raise _Lost()
        batch_no = 0
        size = max(1, plan.batch_rows)
        for start in range(0, len(rows), size):
            chunk = rows[start : start + size]
            batch_no += 1
            encoded = encode_key((start + len(chunk),), [position])  # a row position, no value
            written = await writer.write_batch(
                chunk,
                mark=LedgerMark(
                    job_id=job.id,
                    partition_no=part.partition_no,
                    batch_no=batch_no,
                    last_key=encoded,
                    rows=start + len(chunk),
                ),
            )
            ROWS_WRITTEN.labels("synthesize", "files").inc(written)
            if not await jobs.checkpoint(
                worker,
                part.id,
                last_key=encoded,
                batch_no=batch_no,
                rows_read=len(chunk),
                rows_written=written,
                rows_skipped=0,
                values_by_method={"mostly": written},
                verify_checked=0,
            ):
                if await jobs.partition_control(worker, part.id) == "cancel":
                    await jobs.abandon_partition(worker, part.id)
                raise _Lost()
            control = await jobs.partition_control(worker, part.id)
            if control == "pause":
                await jobs.release(worker, job_ids=[], partition_ids=[part.id])
                return
            if control == "cancel":
                await jobs.abandon_partition(worker, part.id)
                return
            if control == "lost":
                raise _Lost()
        meta = await writer.finish_partition(job.id, part.partition_no, batch_no=batch_no)
        meta["table"] = role
        if not await jobs.finish_partition(worker, part.id, PartitionState.done, meta=meta):
            raise _Lost()
    finally:
        await writer.close()


async def _fail_partition(
    ctx: HandlerContext, part: ClaimedPartition, code: str, detail: str
) -> None:
    if not await jobs.finish_partition(
        ctx.worker_id, part.id, PartitionState.failed, error_code=code, error=detail
    ):
        LEASES_LOST.labels("partition").inc()


async def _run_partition(
    ctx: HandlerContext, job: JobInternal, rt: RunRuntime, part: ClaimedPartition
) -> None:
    worker, mode = ctx.worker_id, rt.plan.target.write_mode
    cols = rt.shape.key_columns()
    after = decode_key(part.last_key, cols) if part.last_key else None
    batch_no, read_total = part.batch_no, part.rows_written
    if mode in ("in_place", "append"):
        mark = await rt.writer.read_ledger(job.id, part.partition_no)
        if (mark.batch_no if mark is not None else 0) < part.batch_no:
            # the ledger commits before the checkpoint, so it is never behind it unless its rows
            # were removed: which batches were applied is unknown, so never write again
            raise PrepareFailure(
                "ledger_conflict", "the target's write ledger is behind the partition checkpoint"
            )
        if mark is not None and mark.batch_no > part.batch_no:
            # the last batch committed with its ledger mark but its checkpoint did not: the
            # ledger is authoritative, so record it and continue after it
            delta = max(0, mark.rows - part.rows_written)
            if not await jobs.checkpoint(
                worker,
                part.id,
                last_key=mark.last_key,
                batch_no=mark.batch_no,
                rows_read=delta,
                rows_written=delta,
                rows_skipped=0,
                values_by_method={},
                verify_checked=0,
            ):
                raise _Lost()
            after, batch_no, read_total = decode_key(mark.last_key, cols), mark.batch_no, mark.rows
    elif mode == "files" and (part.batch_no > 0 or part.attempts > 1):
        assert isinstance(rt.writer, FilesWriter)
        await rt.writer.reset_partition(part.partition_no)
        if not await jobs.reset_partition_progress(worker, part.id):
            raise _Lost()
        after, batch_no, read_total = None, 0, 0
    selection = rt.spec.source.selection
    key_range = KeyRange(lower=part.lower_key, upper=part.upper_key)
    strategy = rt.spec.target.pk_strategy
    async for rows in rt.source.read_partition(
        rt.shape, selection, selection.params, key_range, after_key=after, batch=rt.plan.batch_rows
    ):
        last = tuple(rows[-1][k] for k in rt.shape.key)
        result = await asyncio.to_thread(rt.transformer.transform, rows)
        out = result.rows
        if mode == "append" and strategy is not None:
            out = apply_pk_strategy(out, strategy=strategy, key=rt.shape.key)
        batch_no += 1
        read_total += len(rows)
        encoded = encode_key(last, cols)
        written = await rt.writer.write_batch(
            out,
            mark=LedgerMark(
                job_id=job.id,
                partition_no=part.partition_no,
                batch_no=batch_no,
                last_key=encoded,
                rows=read_total,
            ),
        )
        ROWS_WRITTEN.labels(rt.spec.mode, mode).inc(written)
        if not await jobs.checkpoint(
            worker,
            part.id,
            last_key=encoded,
            batch_no=batch_no,
            rows_read=len(rows),
            rows_written=written,
            rows_skipped=max(0, len(rows) - written),
            values_by_method=_counters(result),
            verify_checked=result.verification.checked,
        ):
            if await jobs.partition_control(worker, part.id) == "cancel":
                await jobs.abandon_partition(worker, part.id)
            raise _Lost()
        control = await jobs.partition_control(worker, part.id)
        if control == "pause":
            await jobs.release(worker, job_ids=[], partition_ids=[part.id])
            return
        if control == "cancel":
            await jobs.abandon_partition(worker, part.id)
            return
        if control == "lost":
            raise _Lost()
    meta = await rt.writer.finish_partition(job.id, part.partition_no, batch_no=batch_no)
    if not await jobs.finish_partition(worker, part.id, PartitionState.done, meta=meta):
        raise _Lost()


# -- finalize -------------------------------------------------------------------------------------


@dataclass
class _Partition:
    no: int
    state: str
    rows_read: int
    rows_written: int
    rows_skipped: int
    batch_no: int
    attempts: int
    resumed: int
    error_code: str | None
    meta: dict[str, Any]
    started_at: datetime | None
    finished_at: datetime | None
    updated_at: datetime


async def _partitions(job: JobInternal) -> list[_Partition]:
    """Value-free partition facts (never a key) for the outcome, the manifest and evidence."""
    async with db.acquire_queue(job.tenant_id) as conn:
        rows = await conn.fetch(
            "SELECT partition_no, state, rows_read, rows_written, rows_skipped, batch_no, "
            "attempts, resumed, error_code, meta, started_at, finished_at, updated_at "
            f"FROM {db.schema_ident()}.dps_job_partition WHERE job_id = $1 ORDER BY partition_no",
            job.id,
        )
    return [
        _Partition(
            no=r["partition_no"],
            state=r["state"],
            rows_read=r["rows_read"],
            rows_written=r["rows_written"],
            rows_skipped=r["rows_skipped"],
            batch_no=r["batch_no"],
            attempts=r["attempts"],
            resumed=r["resumed"],
            error_code=r["error_code"],
            meta=r["meta"] or {},
            started_at=r["started_at"],
            finished_at=r["finished_at"],
            updated_at=r["updated_at"],
        )
        for r in rows
    ]


async def _live_canceled(job_id: str) -> int:
    async with db.acquire_queue(None) as conn:
        return int(
            await conn.fetchval(
                f"SELECT count(*) FROM {db.schema_ident()}.dps_job_partition WHERE job_id = $1 "
                "AND state = 'canceled' AND locked_by IS NOT NULL AND lease_expires_at > now()",
                job_id,
            )
        )


async def _existing_evidence(job: JobInternal) -> str | None:
    """A pack an interrupted earlier finalize of this job already stored (packs are immutable)."""
    async with db.acquire_queue(job.tenant_id) as conn:
        value = await conn.fetchval(
            f"SELECT id FROM {db.schema_ident()}.dps_evidence WHERE job_id = $1 "
            "ORDER BY created_at LIMIT 1",
            job.id,
        )
    return str(value) if value is not None else None


async def finalize(ctx: HandlerContext, job: ClaimedJob) -> None:
    """Contract §3.1 ``finalize``: outcome, manifest, ledger, evidence, audit, terminal state.

    A canceled job waits (re-queued) while a canceled partition still holds a live lease. A
    transient failure is retried (the reaper dead-letters and purges at ``max_attempts``).
    """
    try:
        await _finalize(ctx, job)
    except _Lost:
        LEASES_LOST.labels("job").inc()
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # transient (target, store, audit): retry with backoff
        code = exc.code if isinstance(exc, ConnectorError | StorageError) else "finalize_error"
        logger.error("finalize_failed", job_id=job.id, error=type(exc).__name__)
        await _retry(ctx, job, code, type(exc).__name__)


def copied_personal_columns(
    plan: RunPlan, snapshot: ClassificationSnapshot, sampled: set[str]
) -> list[str]:
    """D-A: written columns a synthesize run would copy although discovery classified them, or
    keys it would write with their real values — neither planned, pseudonymised nor drawn by the
    tabular sampler. Empty by construction (:func:`~targets.build_column_plans`,
    :func:`~targets.pseudonym_columns`); ``prepare`` fails closed on anything else."""
    if plan.target.write_mode == "in_place":
        return []  # in place rewrites the planned and pseudonymised columns only
    written = set(plan.target.write_columns)
    if not plan.target.generated_key:
        written |= set(plan.key)
    replaced = {p.name for p in plan.columns} | {p.name for p in plan.pseudonyms} | sampled
    classified = {c.name for c in snapshot.columns if c.entity_type and not c.text_column}
    return sorted((written & (classified | set(plan.key))) - replaced)


def synthetic_kept_columns(
    plan: RunPlan,
    snapshot: ClassificationSnapshot | None,
    table_columns: Sequence[str],
    sampled: set[str],
) -> set[str]:
    """D-A: what a synthesize run writes as **source values** although nothing modelled them —
    written columns that are unclassified (no entity type, not free text), not a key, not
    planned, not pseudonymised and not drawn by the tabular sampler (``sampled``). In place, every
    column of the table is output."""
    classified = {
        c.name for c in (snapshot.columns if snapshot else []) if c.entity_type or c.text_column
    }
    written = (
        set(table_columns)
        if plan.target.write_mode == "in_place"
        else set(plan.target.write_columns) | set(plan.key)
    )
    replaced = {p.name for p in plan.columns} | {p.name for p in plan.pseudonyms}
    return written - classified - set(plan.key) - replaced - sampled


def kept_review(
    rows: Sequence[Row],
    shape: TableShape,
    *,
    kept: set[str],
    detector: Detector,
    min_score: float,
) -> dict[str, Any]:
    """D-A: MEDIUM+ detector findings in synthetic rows' source-kept columns (counts and column
    names only; the rows are not kept).

    A numeric column counts only when its hits reach discovery's bar (hit rate × mean score at
    or above ``min_score``): a column of 12-digit ids holds a checksum-valid "Aadhaar" by chance.
    """
    cells: Counter[str] = Counter()
    present: Counter[str] = Counter()
    scores: dict[str, list[float]] = defaultdict(list)
    columns = {c.name: c for c in shape.columns if c.name in kept}
    for row in rows:
        for name, column in columns.items():
            text = to_engine_text(row.get(name), column)
            if not text:
                continue
            present[name] += 1
            found = [
                f
                for f in detector.detect(data={name: text}).findings
                if f.sensitivity.rank >= Sensitivity.MEDIUM.rank
            ]
            if found:
                cells[name] += 1
                scores[name].append(max(f.score for f in found))
    flagged = sorted(
        name
        for name in cells
        if columns[name].kind not in NUMERIC_KINDS
        or cells[name] / present[name] * (sum(scores[name]) / len(scores[name])) >= min_score
    )
    return {
        "rows": len(rows),
        "kept_column_hits": sum(cells[n] for n in flagged),
        "kept_columns": flagged,
    }


def _sampled_columns(job: JobInternal) -> set[str]:
    """Names of the columns the tabular sampler drew (``progress.metrics.tabular.columns``)."""
    tabular = (job.progress.metrics or {}).get("tabular") or {}
    paths = tabular.get("columns") if isinstance(tabular, dict) else None
    if not isinstance(paths, list):
        return set()
    return {str(p).removeprefix("$.") for p in paths}


async def _post_sample(
    ctx: HandlerContext,
    job: JobInternal,
    spec: RunSpec,
    plan: RunPlan,
    source_conn: Connection,
    target_conn: Connection | None,
    snapshot: ClassificationSnapshot | None = None,
) -> dict[str, Any]:
    """Re-read up to 200 written rows and run the detector over them (counts only); a
    synthesize run also reviews its source-kept columns (D-A, ``synthetic_kept``)."""
    conn = source_conn if _same_connection(spec, source_conn) else target_conn
    if conn is None or plan.target.object is None:
        return {}
    connector = await _open(ctx, job, conn, "finalize")
    try:
        shape = await connector.describe(plan.target.object)
        rows = await connector.sample(shape, Selection(), {}, POST_SAMPLE_ROWS)
    finally:
        await connector.close()
    planned = {p.name for p in plan.columns}
    # in place, every column of the table is output (kept columns too); else what was written
    written = (
        {c.name for c in shape.columns}
        if plan.target.write_mode == "in_place"
        else set(plan.target.write_columns) | set(plan.key)
    )
    policy = _policy(ctx, spec, plan.policy_name)
    detector = ctx.engine.detector(policy, plan.locale)

    def scan(sample: list[dict[str, Any]]) -> tuple[int, int]:
        kept_hits = transformed_hits = 0
        for row in sample:
            for column in shape.columns:
                if column.name not in written:
                    continue
                text = to_engine_text(row.get(column.name), column)
                if not text:
                    continue
                if detector.detect(data={column.name: text}).findings:
                    if column.name in planned:
                        transformed_hits += 1
                    else:
                        kept_hits += 1
        return kept_hits, transformed_hits

    kept_hits, transformed_hits = await asyncio.to_thread(scan, rows)
    count = len(rows)
    out: dict[str, Any] = {
        "rows": count,
        "detector_hits": transformed_hits,
        "kept_column_hits": kept_hits,
    }
    if spec.mode == "synthesize":
        kept = synthetic_kept_columns(
            plan, snapshot, [c.name for c in shape.columns], _sampled_columns(job)
        )
        out["synthetic_kept"] = await asyncio.to_thread(
            kept_review,
            rows,
            shape,
            kept=kept,
            detector=detector,
            min_score=policy.detect.min_score,
        )
    rows.clear()  # the sample never outlives the count
    return out


def _written_identifiers(plan: RunPlan, snapshot: ClassificationSnapshot | None) -> dict[str, str]:
    """``{column: entity type}`` of the identifier-family columns the run wrote."""
    written = (
        list(plan.projection)
        if plan.target.write_mode == "in_place"
        else [*plan.target.write_columns, *plan.key]
    )
    return identifier_columns(plan.columns, snapshot, written)


async def _identifier_check(
    ctx: HandlerContext,
    job: JobInternal,
    spec: RunSpec,
    plan: RunPlan,
    source_conn: Connection,
    target_conn: Connection | None,
    snapshot: ClassificationSnapshot | None,
) -> dict[str, Any]:
    """D-A (M5 verify r2): every written identifier value, against every source value and the
    issuers' never-issued ranges (``identifier_check.py``; counts only). In place, the table
    holds only synthetic rows now: the ranges are checked, the source cannot be. An append to
    the source table itself mixes both, so it is not run."""
    columns = _written_identifiers(plan, snapshot)
    if not columns:
        return {}
    if plan.same_table_append:
        return {"not_run": "same_table_append"}
    same = _same_connection(spec, source_conn)
    conn = source_conn if same else target_conn
    if conn is None or plan.target.object is None:
        return {"error": "target_unreadable"}
    target = await _open(ctx, job, conn, "finalize")
    source: Connector | None = None
    try:
        target_shape = await target.describe(plan.target.object)
        if plan.target.write_mode == "in_place":
            return await check_identifiers(
                columns, target=target, target_shape=target_shape, source=None, source_shape=None
            )
        source = target if same else await _open(ctx, job, source_conn, "finalize")
        source_shape = await source.describe(spec.source.object)
        return await check_identifiers(
            columns,
            target=target,
            target_shape=target_shape,
            source=source,
            source_shape=source_shape,
        )
    finally:
        if source is not None and source is not target:
            await source.close()
        await target.close()


async def _write_manifest(
    ctx: HandlerContext,
    job: JobInternal,
    spec: RunSpec,
    plan: RunPlan,
    target_conn: Connection,
    parts: list[_Partition],
) -> dict[str, Any]:
    assert spec.target.files is not None
    store = await open_target_store(target_conn, ctx.settings, kek=ctx.kek)
    if plan.prepare.get("linked"):
        return await _write_linked_manifest(ctx, job, spec, plan, target_conn, parts, store)
    files = [f for p in parts for f in (p.meta.get("files") or [])]
    columns = [
        ColumnInfo(
            path=f"$.{n}", name=n, data_type=plan.target.column_types.get(n, ""), kind="other"
        )
        for n in plan.projection
    ]
    writer = FilesWriter(
        store,
        spec.target.files,
        job_id=job.id,
        base_prefix=target_conn.params.prefix,
        columns=columns,
    )
    manifest = build_manifest(
        job_id=job.id,
        spec=spec.target.files,
        columns=columns,
        parts=files,
        created_at=datetime.now(UTC),
    )
    stored = await write_manifest(store, writer.base_key(), manifest)
    artefact = await ctx.artefacts.save(
        tenant_id=job.tenant_id,
        job_id=job.id,
        kind="manifest",
        data=manifest_bytes(manifest),
        content_type="application/json",
        meta={"object_key": stored.key, "sha256": stored.sha256},
    )
    return {
        "objects": len(files),
        "rows": manifest["rows_total"],
        "manifest_artefact_id": artefact,
        "manifest_key": stored.key,
        "compression": manifest["compression"],
    }


async def _write_linked_manifest(
    ctx: HandlerContext,
    job: JobInternal,
    spec: RunSpec,
    plan: RunPlan,
    target_conn: Connection,
    parts: list[_Partition],
    store: ObjectStore,
) -> dict[str, Any]:
    """One manifest for a linked job: ``tables`` = {parent, child: object, dir, columns,
    objects, rows_total}; written last, at ``<files.prefix>/<job_id>/manifest.json``."""
    files_spec = spec.target.files
    assert files_spec is not None
    now = datetime.now(UTC)
    tables: dict[str, Any] = {}
    for no, role in enumerate(("parent", "child")):
        table = plan.prepare["linked"][role]
        columns = [
            ColumnInfo(
                path=f"$.{c['name']}", name=c["name"], data_type=c["data_type"], kind=c["kind"]
            )
            for c in table["columns"]
        ]
        part_files = [f for p in parts if p.no == no for f in (p.meta.get("files") or [])]
        one = build_manifest(
            job_id=job.id, spec=files_spec, columns=columns, parts=part_files, created_at=now
        )
        tables[role] = {
            "object": table["object"],
            "dir": table["dir"],
            "columns": one["columns"],
            "objects": one["objects"],
            "rows_total": one["rows_total"],
        }
    manifest = {
        "manifest_version": "dps.files.v1",
        "job_id": job.id,
        "format": files_spec.format,
        "compression": effective_compression(files_spec),
        "linked": True,
        "tables": tables,
        "rows_total": sum(t["rows_total"] for t in tables.values()),
        "created_at": now.isoformat(),
    }
    base = FilesWriter(
        store, files_spec, job_id=job.id, base_prefix=target_conn.params.prefix, columns=[]
    ).base_key()
    stored = await write_manifest(store, base, manifest)
    artefact = await ctx.artefacts.save(
        tenant_id=job.tenant_id,
        job_id=job.id,
        kind="manifest",
        data=manifest_bytes(manifest),
        content_type="application/json",
        meta={"object_key": stored.key, "sha256": stored.sha256},
    )
    return {
        "objects": sum(len(t["objects"]) for t in tables.values()),
        "rows": manifest["rows_total"],
        "manifest_artefact_id": artefact,
        "manifest_key": stored.key,
        "compression": manifest["compression"],
    }


async def _finalize(ctx: HandlerContext, job: ClaimedJob) -> None:
    requested = job.progress.outcome
    if requested == "canceled" and await _live_canceled(job.id):
        if not await jobs.advance_job(
            ctx.worker_id,
            job.id,
            phase=JobPhase.finalize,
            state=JobState.queued,
            progress={"stage": "waiting for canceled partitions"},
            delay_seconds=CANCEL_WAIT_SECONDS,
        ):
            raise _Lost()
        return
    parts = await _partitions(job)
    failed = [p for p in parts if p.state in ("failed", "dead")]
    outcome: str
    if requested in ("dry_run", "canceled", "failed"):
        outcome = requested
    else:
        outcome = "failed" if failed else "succeeded"
    spec = RunSpec.model_validate(job.spec)  # the redacted spec: templates and ids only
    plan = RunPlan.model_validate(job.plan) if job.plan else None
    source_conn, target_conn = await _connections(job, spec)
    async with db.acquire_queue(job.tenant_id) as conn:
        snapshot = await get_snapshot(job.tenant_id, spec.snapshot_id, conn=conn)
    mode = spec.target.write_mode
    files: dict[str, Any] | None = None
    post_sample: dict[str, Any] | None = None
    ledger_cleared: bool | None = None
    if plan is not None and outcome == "succeeded":
        if mode == "files" and target_conn is not None:
            files = await _write_manifest(ctx, job, spec, plan, target_conn, parts)
        elif mode != "files":
            post_sample = await _best_effort(
                _post_sample(ctx, job, spec, plan, source_conn, target_conn, snapshot)
            )
        if spec.mode == "synthesize":
            ids = (
                {"not_run": "files_target"}
                if mode == "files"
                else await _best_effort(
                    _identifier_check(ctx, job, spec, plan, source_conn, target_conn, snapshot)
                )
            )
            if ids and _written_identifiers(plan, snapshot):
                post_sample = {**(post_sample or {}), "identifier_check": ids}
    if plan is not None and mode in ("in_place", "append") and outcome in ("succeeded", "canceled"):
        # a failed job keeps its ledger: a retry resumes from it (never re-applies a batch)
        cleared = await _best_effort(_clear_ledger(ctx, job, spec, plan, source_conn, target_conn))
        ledger_cleared = "error" not in cleared
    evidence_id = await _existing_evidence(job)
    if evidence_id is None:
        evidence_id = await _store_evidence(
            ctx,
            job,
            spec,
            plan,
            snapshot,
            source_conn,
            target_conn,
            parts,
            outcome,
            files,
            post_sample,
            ledger_cleared,
        )
    counts = {
        "rows_written": int(job.progress.rows_written),
        "partitions": len(parts),
        "partitions_failed": len(failed),
    }
    ctx.audit.write(
        AuditEvent(
            trace_id=job.id,
            route="worker:finalize",
            tenant_id=job.tenant_id,
            key_id=job.created_by or "-",
            mode=job.mode or "",
            policy=plan.policy_name if plan else "-",
            action="job.finalize",
            outcome=outcome,
            job_id=job.id,
            counts=counts,
        )
    )
    first_error = next((p for p in failed if p.error_code), None)
    if not await jobs.advance_job(
        ctx.worker_id,
        job.id,
        phase=JobPhase.finalize,
        state=_TERMINAL[outcome],
        progress={"outcome": outcome, "evidence_id": evidence_id, "stage": ""},
        error_code=first_error.error_code if first_error and not job.error_code else None,
        error=f"partition {first_error.no} failed" if first_error and not job.error_code else None,
        purge_secret=True,
    ):
        raise _Lost()
    await RUNTIMES.drop(job.id)
    logger.info("finalize_done", job_id=job.id, outcome=outcome, evidence_id=evidence_id)


async def _best_effort(step: Awaitable[dict[str, Any]]) -> dict[str, Any]:
    """Run an informational finalize step; a target or secret failure becomes ``{"error":
    code}`` in the evidence instead of blocking the pack (and the key purge) behind retries."""
    try:
        return await step
    except (ConnectorError, StorageError, ConnectionStoreError) as exc:
        return {"error": exc.code}
    except (SecretUnavailable, SealError):
        return {"error": "secret_unavailable"}


async def _clear_ledger(
    ctx: HandlerContext,
    job: JobInternal,
    spec: RunSpec,
    plan: RunPlan,
    source_conn: Connection,
    target_conn: Connection | None,
) -> dict[str, Any]:
    conn = source_conn if _same_connection(spec, source_conn) else target_conn
    if conn is None:
        return {"error": "connection_missing"}
    connector = await _open(ctx, job, conn, "finalize")
    try:
        shape = plan.target.target_shape or await connector.describe(spec.source.object)
        writer = await connector.writer(plan.target, shape)
        deleted = await writer.clear_ledger(job.id)
        await writer.close()
    finally:
        await connector.close()
    return {"deleted": deleted}


async def _store_evidence(
    ctx: HandlerContext,
    job: JobInternal,
    spec: RunSpec,
    plan: RunPlan | None,
    snapshot: ClassificationSnapshot | None,
    source_conn: Connection,
    target_conn: Connection | None,
    parts: list[_Partition],
    outcome: str,
    files: dict[str, Any] | None,
    post_sample: dict[str, Any] | None,
    ledger_cleared: bool | None,
) -> str:
    progress = job.progress
    counters = progress.values_by_method
    hits = int(counters.get("verify:hits", 0)) + int(progress.verification.get("hits", 0))
    leakage = job.error_code == "leakage" or any(p.error_code == "leakage" for p in parts)
    verification: dict[str, Any] = {
        "mode": spec.options.verify,
        "batches": sum(p.batch_no for p in parts),
        "values_checked": int(progress.verification.get("checked", 0)),
        "hits": hits,
        "passed": hits == 0 and not leakage,
        "post_sample": post_sample,
    }
    if ledger_cleared is not None:
        verification["write_ledger_cleared"] = ledger_cleared
    if job.mode == "synthesize" and plan is not None:
        # D-A: a source value is kept only in a column reviewed as not personal whose written
        # sample (before the write: the preflight; after it: the post-write sample) is clean
        reviews = [plan.prepare.get("kept_review"), (post_sample or {}).get("synthetic_kept")]
        review = {c for r in reviews if r for c in r.get("kept_columns", [])}
        # M5 verify r2: a written identifier found in the source or outside the never-issued
        # ranges needs review; a check that could not run fails closed on every such column
        ids = (post_sample or {}).get("identifier_check") or {}
        review |= set(ids.get("review_columns") or [])
        if "error" in ids:
            review |= set(_written_identifiers(plan, snapshot))
        flagged = sorted(review)
        verification["review_required"] = bool(flagged)
        verification["review_columns"] = flagged
        if flagged:
            verification["passed"] = False
    errors: list[dict[str, str]] = []
    if job.error_code:
        errors.append({"code": job.error_code, "where": "prepare"})
    errors += [
        {"code": p.error_code, "where": f"partition {p.no}"}
        for p in parts
        if p.error_code and p.state in ("failed", "dead")
    ]
    consistency = job.spec.get("consistency") or {}
    caller = (
        consistency.get("caller_key") if job.mode == "obfuscate" else consistency.get("caller_seed")
    )
    views = [
        jobs.PartitionView(
            partition_no=p.no,
            state=PartitionState(p.state),
            rows_read=p.rows_read,
            rows_written=p.rows_written,
            rows_skipped=p.rows_skipped,
            batch_no=p.batch_no,
            attempts=p.attempts,
            resumed=p.resumed,
            checkpoint_fp=None,
            started_at=p.started_at,
            finished_at=p.finished_at,
            updated_at=p.updated_at,
            error_code=p.error_code,
            rows_per_second=None,
        )
        for p in parts
    ]
    policy_version: str | None = None
    if plan is not None:
        with contextlib.suppress(PrepareFailure):
            policy_version = str(_policy(ctx, spec, plan.policy_name).version)
    inputs = EvidenceInputs(
        job=job,
        spec=job.spec,
        plan=plan,
        snapshot=snapshot,
        source_conn=source_conn,
        target_conn=target_conn,
        partitions=views,
        outcome=cast(Any, outcome),
        server_version=source_conn.last_test.server_version if source_conn.last_test else None,
        prepare=plan.prepare if plan else {},
        files=files,
        verification=verification,
        key_custody={
            "returned": not caller,
            "wrapped": bool(consistency.get("wrapped")),
            "stored_by_dps": False,
            "purged_at": datetime.now(UTC).isoformat(),
        },
        errors=errors,
        engine_version=__version__,
        image_digest=ctx.settings.image_digest or None,
        policy_version=policy_version,
    )
    record = await save_evidence(
        build_pack(inputs), artefacts=ctx.artefacts, acquire=db.acquire_queue, expires_at=None
    )
    return record.id
