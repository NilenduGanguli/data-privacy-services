"""The retention job (contract §3.6): one module, one scheduler, one system tenant.

``retention_job`` is the handler of ``retention`` job rows (owned by ``_system``, scheduled at most
once per ``DPS_RETENTION_INTERVAL_HOURS`` by :func:`dps.jobs.ensure_retention_job` from every
worker's reaper loop). For each tenant (``dps_tenant`` ∪ the tenants that own jobs), on the worker
pool with that tenant's GUC bound, it:

- deletes up to :data:`JOBS_PER_TENANT` terminal jobs whose ``expires_at`` has passed — their
  artefact objects first (:meth:`~dps.storage.ArtefactService.delete_for_job`), then the rows, which
  cascade to partitions, evidence and artefact rows (snapshots keep living, ``job_id`` set null);
- deletes ``proposed``/``superseded`` snapshots created before the tenant's retention window,
  except one a live job still names;
- nulls ``dps_connection.secret_enc`` whose ``secret_expires_at`` has passed;
- nulls ``key_enc`` on every remaining terminal job (defence in depth; finalize already purges).

It never touches ``dps_audit_log`` (``python -m dps.migrate --maintain`` owns it) or exported
files (the customer's output). Counters go to ``progress.values_by_method`` as
``retention:jobs|artefacts|snapshots|secrets|keys`` (M5 adds its own); ``dry_run`` counts only.
A transient failure requeues the job with backoff — every step is idempotent.

**M5 (contract §3.6, same module, same scheduler, same system tenant).** The handler body is
:func:`run_retention`, which ``retention_job`` wraps; tenants come from the tenant directory
(``dps_tenant`` ∪ job owners ∪ connection owners, the system tenant included), so expired
retention jobs are deleted by the same per-tenant loop as everyone's. Per tenant, after M2's
statements: daily roll-up, refresh-marker and usage rows older than ``DPS_ROLLUP_RETENTION_DAYS``.
M4's scan rows need no statement: they cascade from ``dps_job``. Afterwards the last three days'
roll-ups are refreshed for every tenant with recent jobs. The counts also go to
``progress.metrics.retention`` (:class:`RetentionCounts`), ``values_by_method`` gains
``retention:rollup_rows`` / ``retention:usage_rows`` when they are non-zero (M2's five keys are
always there), and one value-free ``retention.run`` audit event is written for the system tenant.
:func:`enqueue_retention` is the on-demand run (``POST /admin/retention/run``).
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import asyncpg
from pydantic import BaseModel, ValidationError

from dps import db, jobs
from dps.audit import AuditEvent
from dps.auth import RESERVED_TENANT_PREFIX
from dps.config import Settings
from dps.jobs import (
    SYSTEM_TENANT,
    TERMINAL_JOB_STATES,
    ClaimedJob,
    Job,
    JobKind,
    JobPhase,
    JobState,
)
from dps.jobs_models import RetentionSpec
from dps.observability import get_logger
from dps.storage import ArtefactService, StorageError

if TYPE_CHECKING:
    from dps.worker import HandlerContext

logger = get_logger(__name__)

#: Expired jobs deleted per tenant and run (the next run takes the rest).
JOBS_PER_TENANT = 500
COUNTERS = (
    "retention:jobs",
    "retention:artefacts",
    "retention:snapshots",
    "retention:secrets",
    "retention:keys",
)
_TERMINAL = sorted(s.value for s in TERMINAL_JOB_STATES)
_ACTIVE = [s.value for s in jobs.ACTIVE_JOB_STATES]


#: M5 counters (contract §3.3), written to ``values_by_method`` only when non-zero.
M5_COUNTERS = ("retention:rollup_rows", "retention:usage_rows")
#: Tables aged out by ``DPS_ROLLUP_RETENTION_DAYS`` (M5): roll-ups, refresh markers, usage.
ROLLUP_TABLES = ("dps_report_daily", "dps_report_entity_daily", "dps_report_refresh")
USAGE_TABLE = "dps_tenant_usage"
#: Days of roll-ups recomputed after a run (today and the two before it).
REFRESH_DAYS = 3


class RetentionCounts(BaseModel):
    """What one run removed (or, dry run, would remove) — counts only."""

    tenants: int = 0
    jobs: int = 0
    objects: int = 0
    snapshots: int = 0
    secrets_purged: int = 0
    keys_purged: int = 0
    rollup_rows: int = 0
    usage_rows: int = 0

    def counters(self) -> dict[str, int]:
        """M2's ``retention:*`` counter map (+ the M5 keys when non-zero)."""
        out = {
            "retention:jobs": self.jobs,
            "retention:artefacts": self.objects,
            "retention:snapshots": self.snapshots,
            "retention:secrets": self.secrets_purged,
            "retention:keys": self.keys_purged,
        }
        if self.rollup_rows:
            out["retention:rollup_rows"] = self.rollup_rows
        if self.usage_rows:
            out["retention:usage_rows"] = self.usage_rows
        return out


class _Lost(Exception):
    """The retention job's lease is gone: stop without touching its row."""


#: ``on_tenant(index, total, counts_so_far)`` → ``False`` stops the run (the lease is gone).
ProgressFn = Callable[[int, int, RetentionCounts], Awaitable[bool]]


async def _tenants() -> list[str]:
    """Every tenant of the directory, the system tenant included (worker pool)."""
    from dps.tenants import tenant_directory  # M5 Task 10

    return await tenant_directory(include_system=True, worker=True)


async def _retention_days(conn: asyncpg.Connection, tenant: str, default: int) -> int:
    days = await conn.fetchval(
        f"SELECT retention_days FROM {db.schema_ident()}.dps_tenant WHERE tenant_id = $1", tenant
    )
    return int(days) if days is not None else default


async def _sweep(
    settings: Settings, artefacts: ArtefactService, tenant: str, *, dry_run: bool
) -> dict[str, int]:
    """One tenant's pass (M2's statements); returns its counters."""
    s = db.schema_ident()
    counts = dict.fromkeys(COUNTERS, 0)
    async with db.acquire_queue(tenant) as conn:
        expired = [
            str(r["id"])
            for r in await conn.fetch(
                f"SELECT id FROM {s}.dps_job WHERE tenant_id = $1 AND state = ANY($2::text[]) "
                "AND expires_at < now() ORDER BY expires_at, id LIMIT $3",
                tenant,
                _TERMINAL,
                JOBS_PER_TENANT,
            )
        ]
        days = await _retention_days(conn, tenant, settings.retention_days)
    if dry_run:
        async with db.acquire_queue(tenant) as conn:
            counts["retention:jobs"] = len(expired)
            counts["retention:artefacts"] = int(
                await conn.fetchval(
                    f"SELECT count(*) FROM {s}.dps_artefact WHERE tenant_id = $1 "
                    "AND job_id = ANY($2::text[])",
                    tenant,
                    expired,
                )
            )
    else:
        for job_id in expired:
            # objects first (idempotent): an interrupted run is completed by the next one
            counts["retention:artefacts"] += await artefacts.delete_for_job(tenant, job_id)
        if expired:
            async with db.acquire_queue(tenant) as conn:
                tag = await conn.execute(
                    f"DELETE FROM {s}.dps_job WHERE tenant_id = $1 AND id = ANY($2::text[]) "
                    "AND state = ANY($3::text[]) AND expires_at < now()",
                    tenant,
                    expired,
                    _TERMINAL,
                )
            counts["retention:jobs"] = db.rowcount(tag)
    snapshots = (
        f"FROM {s}.dps_snapshot sn WHERE sn.tenant_id = $1 "
        "AND sn.status IN ('proposed', 'superseded') "
        "AND sn.created_at < now() - make_interval(days => $2) "
        f"AND NOT EXISTS (SELECT 1 FROM {s}.dps_job j WHERE j.tenant_id = sn.tenant_id "
        "AND j.snapshot_id = sn.id AND j.state = ANY($3::text[]))"
    )
    secrets = "WHERE tenant_id = $1 AND secret_enc IS NOT NULL AND secret_expires_at < now()"
    # the expired jobs are deleted, not purged: a dry run must not count their keys twice
    keys = "WHERE tenant_id = $1 AND key_enc IS NOT NULL AND state = ANY($2::text[])"
    async with db.acquire_queue(tenant) as conn:
        if dry_run:
            counts["retention:snapshots"] = int(
                await conn.fetchval(f"SELECT count(*) {snapshots}", tenant, days, _ACTIVE)
            )
            counts["retention:secrets"] = int(
                await conn.fetchval(f"SELECT count(*) FROM {s}.dps_connection {secrets}", tenant)
            )
            counts["retention:keys"] = int(
                await conn.fetchval(
                    f"SELECT count(*) FROM {s}.dps_job {keys} AND id <> ALL($3::text[])",
                    tenant,
                    _TERMINAL,
                    expired,
                )
            )
            return counts
        counts["retention:snapshots"] = db.rowcount(
            await conn.execute(
                f"DELETE FROM {s}.dps_snapshot WHERE id IN (SELECT sn.id {snapshots})",
                tenant,
                days,
                _ACTIVE,
            )
        )
        counts["retention:secrets"] = db.rowcount(
            await conn.execute(
                f"UPDATE {s}.dps_connection SET secret_enc = NULL, updated_at = now() {secrets}",
                tenant,
            )
        )
        counts["retention:keys"] = db.rowcount(
            await conn.execute(
                f"UPDATE {s}.dps_job SET key_enc = NULL, updated_at = now() {keys}",
                tenant,
                _TERMINAL,
            )
        )
    return counts


async def _age_out(tenant: str, *, keep_days: int, dry_run: bool) -> tuple[int, int]:
    """M5: roll-up/refresh rows and usage rows older than ``keep_days`` → ``(rollup, usage)``."""
    s = db.schema_ident()
    cutoff = datetime.now(UTC).date() - timedelta(days=keep_days)
    verb = "SELECT count(*)" if dry_run else "DELETE"
    totals = []
    async with db.acquire_queue(tenant) as conn:
        for group in (ROLLUP_TABLES, (USAGE_TABLE,)):
            n = 0
            for table in group:
                sql = f"{verb} FROM {s}.{table} WHERE tenant_id = $1 AND day < $2"
                if dry_run:
                    n += int(await conn.fetchval(sql, tenant, cutoff))
                else:
                    n += db.rowcount(await conn.execute(sql, tenant, cutoff))
            totals.append(n)
    return totals[0], totals[1]


async def _refresh_recent(tenant: str, settings: Settings) -> None:
    """Recompute the last :data:`REFRESH_DAYS` days of roll-ups when the tenant had recent jobs."""
    from dps.reports import refresh_rollups, utc_today  # M5 Task 12

    last = utc_today()
    first = last - timedelta(days=REFRESH_DAYS - 1)
    since = datetime(first.year, first.month, first.day, tzinfo=UTC)
    async with db.acquire_queue(tenant) as conn:
        recent = await conn.fetchval(
            f"SELECT EXISTS (SELECT 1 FROM {db.schema_ident()}.dps_job WHERE tenant_id = $1 "
            "AND (created_at >= $2 OR finished_at >= $2))",
            tenant,
            since,
        )
    if recent:
        await refresh_rollups(tenant, first, last, settings=settings, worker=True)


async def run_retention(
    *,
    settings: Settings,
    artefacts: ArtefactService,
    dry_run: bool,
    on_tenant: ProgressFn | None = None,
) -> RetentionCounts:
    """One retention pass over every tenant (module docstring); returns the counts.

    A database error in one tenant's pass is logged (class name only) and the next tenant runs;
    an object-store error propagates (the handler retries the whole, idempotent run).

    Raises:
        StorageError: deleting an artefact object failed.
    """
    totals = RetentionCounts()
    tenants = await _tenants()
    totals.tenants = len(tenants)
    for index, tenant in enumerate(tenants, start=1):
        try:
            counts = await _sweep(settings, artefacts, tenant, dry_run=dry_run)
            rollup, usage = await _age_out(
                tenant, keep_days=settings.rollup_retention_days, dry_run=dry_run
            )
            if not dry_run and not tenant.startswith(RESERVED_TENANT_PREFIX):
                await _refresh_recent(tenant, settings)
        except asyncpg.PostgresError as exc:
            logger.warning("retention_tenant_failed", tenant_id=tenant, error=type(exc).__name__)
            continue
        totals.jobs += counts["retention:jobs"]
        totals.objects += counts["retention:artefacts"]
        totals.snapshots += counts["retention:snapshots"]
        totals.secrets_purged += counts["retention:secrets"]
        totals.keys_purged += counts["retention:keys"]
        totals.rollup_rows += rollup
        totals.usage_rows += usage
        if on_tenant is not None and not await on_tenant(index, len(tenants), totals):
            raise _Lost()
    return totals


def _audit_run(
    ctx: HandlerContext, job: ClaimedJob, counts: RetentionCounts, dry_run: bool
) -> None:
    """One value-free ``retention.run`` event owned by the system tenant (never fails the job)."""
    event = AuditEvent(
        trace_id=uuid.uuid4().hex[:16],
        route="retention",
        tenant_id=SYSTEM_TENANT,
        key_id="system",
        mode="retention",
        policy="-",
        outcome="dry_run" if dry_run else "ok",
        action="retention.run",
        job_id=job.id,
        counts=counts.model_dump(),
    )
    try:
        ctx.audit.write(event)
    except Exception as exc:  # noqa: BLE001 - the log line of the job is the fallback record
        logger.warning("audit_write_failed", job_id=job.id, error=type(exc).__name__)


async def retention_job(ctx: HandlerContext, job: ClaimedJob) -> None:
    """Contract §3.6 handler (module docstring); value-free counters, fenced progress."""
    try:
        spec = RetentionSpec.model_validate(job.spec or {})
    except ValidationError:
        await jobs.advance_job(
            ctx.worker_id,
            job.id,
            phase=JobPhase.retention,
            state=JobState.failed,
            error_code="spec_invalid",
            error="the stored retention spec is not valid",
            progress={"outcome": "failed"},
        )
        return

    async def progress(index: int, total: int, counts: RetentionCounts) -> bool:
        return await jobs.advance_job(
            ctx.worker_id,
            job.id,
            phase=JobPhase.retention,
            state=JobState.running,
            progress={"stage": f"{index}/{total} tenants", "values_by_method": counts.counters()},
        )

    try:
        totals = await run_retention(
            settings=ctx.settings, artefacts=ctx.artefacts, dry_run=spec.dry_run, on_tenant=progress
        )
    except _Lost:
        return
    except Exception as exc:  # noqa: BLE001 - every step is idempotent: retry the run
        code = exc.code if isinstance(exc, StorageError) else "retention_error"
        logger.warning("retention_failed", job_id=job.id, error=type(exc).__name__)
        await jobs.retry_job(
            ctx.worker_id,
            job.id,
            error_code=code,
            error=type(exc).__name__,
            backoff_base=ctx.settings.job_retry_base_seconds,
            backoff_cap=ctx.settings.job_retry_max_seconds,
        )
        return
    await jobs.advance_job(
        ctx.worker_id,
        job.id,
        phase=JobPhase.retention,
        state=JobState.succeeded,
        progress={
            "outcome": "dry_run" if spec.dry_run else "succeeded",
            "stage": f"{totals.tenants} tenants",
            "values_by_method": totals.counters(),
            "metrics": {"retention": totals.model_dump()},
        },
    )
    _audit_run(ctx, job, totals, spec.dry_run)
    logger.info("retention_done", job_id=job.id, counts=totals.counters())


async def enqueue_retention(*, dry_run: bool, created_by: str | None) -> Job:
    """An on-demand retention job owned by the system tenant (no idempotency key: not a
    scheduled bucket). The scheduler stays :func:`dps.jobs.ensure_retention_job`."""
    return await jobs.create_job(
        tenant_id=SYSTEM_TENANT,
        kind=JobKind.retention,
        phase=JobPhase.retention,
        spec=RetentionSpec(dry_run=dry_run).model_dump(mode="json"),
        created_by=created_by,
        priority=200,
    )
