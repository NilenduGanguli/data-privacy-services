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
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import asyncpg
from pydantic import ValidationError

from dps import db, jobs
from dps.jobs import TERMINAL_JOB_STATES, ClaimedJob, JobPhase, JobState
from dps.jobs_models import RetentionSpec
from dps.observability import get_logger
from dps.storage import StorageError

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


class _Lost(Exception):
    """The retention job's lease is gone: stop without touching its row."""


async def _tenants() -> list[str]:
    """Every tenant with a ``dps_tenant`` row or a job (worker pool, cross-tenant read)."""
    s = db.schema_ident()
    async with db.acquire_queue(None) as conn:
        rows = await conn.fetch(
            f"SELECT tenant_id FROM {s}.dps_tenant UNION "
            f"SELECT DISTINCT tenant_id FROM {s}.dps_job ORDER BY 1"
        )
    return [str(r["tenant_id"]) for r in rows]


async def _retention_days(conn: asyncpg.Connection, tenant: str, default: int) -> int:
    days = await conn.fetchval(
        f"SELECT retention_days FROM {db.schema_ident()}.dps_tenant WHERE tenant_id = $1", tenant
    )
    return int(days) if days is not None else default


async def _sweep(ctx: HandlerContext, tenant: str, *, dry_run: bool) -> dict[str, int]:
    """One tenant's pass; returns its counters."""
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
        days = await _retention_days(conn, tenant, ctx.settings.retention_days)
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
            counts["retention:artefacts"] += await ctx.artefacts.delete_for_job(tenant, job_id)
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
    totals = dict.fromkeys(COUNTERS, 0)
    try:
        tenants = await _tenants()
        for index, tenant in enumerate(tenants, start=1):
            for key, value in (await _sweep(ctx, tenant, dry_run=spec.dry_run)).items():
                totals[key] += value
            if not await jobs.advance_job(
                ctx.worker_id,
                job.id,
                phase=JobPhase.retention,
                state=JobState.running,
                progress={"stage": f"{index}/{len(tenants)} tenants", "values_by_method": totals},
            ):
                raise _Lost()
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
            "stage": f"{len(tenants)} tenants",
            "values_by_method": totals,
        },
    )
    logger.info("retention_done", job_id=job.id, counts=totals)
