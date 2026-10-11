"""Durable Postgres job queue (contract §3) — jobs and partitions in the metadata database.

Mirrors ``di/jobs.py``. Claims are two-level (a cap-aware, lock-free candidate CTE feeding an inner
``FOR UPDATE SKIP LOCKED`` with a state re-check, taken in FIFO order), leases are renewed by
heartbeats, and every worker-side write is fenced on ``locked_by`` + ``state = 'running'`` so a
reaped zombie writes zero rows. Per-tenant caps count running job rows plus running partitions
and are enforced within one claim call (cross-call soft, exactly as in Document AI Services). The
public :class:`Job` never carries ``key_enc``/``spec_enc``.

Claims, heartbeats, reaping and stats run on the worker pool with no tenant GUC (the
``worker_claim`` policies grant cross-tenant visibility of the two queue tables only); admin
operations run on the API pool under the tenant GUC. Nothing here logs or raises a value: errors
carry codes, and the ``error`` column only ever receives the caller's value-free text.
"""

from __future__ import annotations

import base64
import os
import random
import socket
import time
from datetime import datetime
from enum import StrEnum
from typing import Any

import asyncpg
from pydantic import BaseModel, Field

from dps import db
from dps.ids import new_id
from dps.jobs_models import JobProgress
from dps.layers.connectors.base import EncodedKey
from dps.observability import JOBS_CLAIMED, get_logger

logger = get_logger(__name__)


class JobKind(StrEnum):
    """What a job row does (contract §3.1)."""

    discover = "discover"
    run = "run"
    scan = "scan"
    retention = "retention"


class JobState(StrEnum):
    """Job row state; ``waiting``/``paused`` belong to run jobs in the ``partitions`` phase."""

    queued = "queued"
    running = "running"
    waiting = "waiting"
    paused = "paused"
    succeeded = "succeeded"
    failed = "failed"
    dead = "dead"
    canceled = "canceled"


class JobPhase(StrEnum):
    """Where a job is in its kind's phase sequence."""

    discover = "discover"
    prepare = "prepare"
    partitions = "partitions"
    finalize = "finalize"
    scan = "scan"
    retention = "retention"


class PartitionState(StrEnum):
    """State of one run partition."""

    queued = "queued"
    running = "running"
    done = "done"
    failed = "failed"
    dead = "dead"
    canceled = "canceled"


TERMINAL_JOB_STATES = frozenset(
    {JobState.succeeded, JobState.failed, JobState.dead, JobState.canceled}
)
ACTIVE_JOB_STATES = (JobState.queued, JobState.running, JobState.waiting, JobState.paused)
SYSTEM_TENANT = "_system"
NOTIFY_CHANNEL = "dps_job_new"
_MIN_LIMIT, _MAX_LIMIT = 1, 200

_PUBLIC = (
    "id, tenant_id, kind, state, phase, mode, group_id, connection_id, target_connection_id, "
    "object_ref, snapshot_id, spec, purpose, kid, progress, plan, error_code, error, created_by, "
    "created_at, updated_at, started_at, finished_at, expires_at"
)
_INTERNAL = _PUBLIC + ", attempts, max_attempts, key_enc, spec_enc, locked_by, lease_expires_at"
_AGG_COLUMNS = (
    "p_total",
    "p_done",
    "p_failed",
    "rows_read",
    "rows_written",
    "rows_skipped",
    "vbm",
    "checked",
)


class QueueFull(Exception):
    """The tenant already has ``max_queued`` active jobs (429 ``queue_full``)."""

    code = "queue_full"
    status = 429


class KeyMaterialRequired(Exception):
    """A retry needs key material the job no longer holds (409 ``key_material_required``)."""

    code = "key_material_required"
    status = 409


class JobStateError(Exception):
    """An admin control that the job's state does not allow (409, value-free detail)."""

    status = 409

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


class Job(BaseModel):
    """Public view of a job row (contract §3.2) — never ``key_enc``, never spec secrets."""

    id: str
    tenant_id: str
    kind: JobKind
    state: JobState
    phase: JobPhase
    mode: str | None = None
    group_id: str | None = None
    connection_id: str | None = None
    target_connection_id: str | None = None
    object_ref: str | None = None
    snapshot_id: str | None = None
    spec: dict[str, Any] = Field(default_factory=dict)
    purpose: dict[str, str] = Field(default_factory=dict)
    kid: str | None = None
    progress: JobProgress = Field(default_factory=JobProgress)
    plan: dict[str, Any] | None = None
    error_code: str | None = None
    error: str | None = None
    created_by: str | None = None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    expires_at: datetime | None = None


class JobInternal(Job):
    """What workers load (worker pool): the sealed blobs and the lease fields as well."""

    attempts: int
    max_attempts: int
    key_enc: bytes | None = None
    spec_enc: bytes | None = None
    locked_by: str | None = None
    lease_expires_at: datetime | None = None


class ClaimedJob(JobInternal):
    """A leased job row."""

    lease_expires_at: datetime


class ClaimedPartition(BaseModel):
    """A leased partition; the keys are encoded (contract §4.1) and never logged."""

    id: int
    job_id: str
    tenant_id: str
    partition_no: int
    lower_key: EncodedKey | None = None
    upper_key: EncodedKey | None = None
    last_key: EncodedKey | None = None
    batch_no: int
    attempts: int
    max_attempts: int
    lease_expires_at: datetime
    rows_written: int = 0
    resumed: int = 0


class PartitionView(BaseModel):
    """API view of a partition: ``checkpoint_fp`` is a fingerprint, never the key itself."""

    partition_no: int
    state: PartitionState
    rows_read: int
    rows_written: int
    rows_skipped: int
    batch_no: int
    attempts: int
    resumed: int
    checkpoint_fp: str | None
    started_at: datetime | None
    finished_at: datetime | None
    updated_at: datetime
    error_code: str | None
    rows_per_second: float | None


class ReapResult(BaseModel):
    """What one reaper pass changed."""

    jobs_requeued: int = 0
    jobs_dead: int = 0
    partitions_requeued: int = 0
    partitions_dead: int = 0
    partitions_released: int = 0
    workers_pruned: int = 0


class JobStats(BaseModel):
    """The Jobs page health strip (``GET /api/v1/jobs/stats``)."""

    workers_online: int
    workers_busy: int
    workers_draining: int
    partitions_waiting: int
    oldest_waiting_s: float | None
    rows_per_s: float
    by_state: dict[str, int]
    running: int
    tenant_max_running: int
    lease_reclaims_24h: int


def _clamp(limit: int) -> int:
    return max(_MIN_LIMIT, min(_MAX_LIMIT, limit))


def _encode_cursor(created_at: datetime, job_id: str) -> str:
    raw = f"{created_at.isoformat()}|{job_id}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(cursor: str) -> tuple[datetime, str]:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode("utf-8")
        ts, sep, job_id = raw.partition("|")
        if not sep or not job_id:
            raise ValueError
        return datetime.fromisoformat(ts), job_id
    except (ValueError, UnicodeDecodeError):
        raise ValueError("malformed cursor") from None


def _row_job(row: asyncpg.Record, model: type[Job] = Job) -> Any:
    """A row of :func:`_select` → ``model``, with the live partition aggregate in ``progress``."""
    data = dict(row)
    progress = JobProgress.model_validate(data.pop("progress") or {})
    if data.get("p_total"):
        progress.partitions_total = int(data["p_total"])
        progress.partitions_done = int(data["p_done"])
        progress.partitions_failed = int(data["p_failed"])
        progress.rows_read = int(data["rows_read"])
        progress.rows_written = int(data["rows_written"])
        progress.rows_skipped = int(data["rows_skipped"])
        progress.values_by_method = {k: int(v) for k, v in (data.get("vbm") or {}).items()}
        progress.verification = {**progress.verification, "checked": int(data["checked"])}
    for extra in _AGG_COLUMNS:
        data.pop(extra, None)
    return model.model_validate(
        {
            **data,
            "progress": progress,
            "spec": data.get("spec") or {},
            "purpose": data.get("purpose") or {},
        }
    )


def _select(cols: str, where: str) -> str:
    """SELECT with the live partition aggregate merged into ``progress`` (contract §3.3)."""
    s = db.schema_ident()
    qcols = ", ".join(f"j.{c.strip()}" for c in cols.split(","))
    return (
        f"SELECT {qcols}, agg.p_total, agg.p_done, agg.p_failed, agg.rows_read, agg.rows_written, "
        f"agg.rows_skipped, agg.vbm, agg.checked FROM {s}.dps_job j LEFT JOIN LATERAL ("
        "SELECT count(*) AS p_total, count(*) FILTER (WHERE p.state = 'done') AS p_done, "
        "count(*) FILTER (WHERE p.state IN ('failed','dead')) AS p_failed, "
        "COALESCE(sum(p.rows_read), 0)::bigint AS rows_read, "
        "COALESCE(sum(p.rows_written), 0)::bigint AS rows_written, "
        "COALESCE(sum(p.rows_skipped), 0)::bigint AS rows_skipped, "
        f"{s}.dps_jsonb_sum(p.values_by_method) AS vbm, "
        "COALESCE(sum(p.verify_checked), 0)::bigint AS checked "
        f"FROM {s}.dps_job_partition p WHERE p.job_id = j.id) agg ON true WHERE {where}"
    )


async def _touch_tenant(conn: asyncpg.Connection, tenant_id: str) -> None:
    await conn.execute(
        f"INSERT INTO {db.schema_ident()}.dps_tenant (tenant_id) VALUES ($1) "
        "ON CONFLICT (tenant_id) DO NOTHING",
        tenant_id,
    )


async def create_job(
    *,
    tenant_id: str,
    kind: JobKind,
    phase: JobPhase,
    spec: dict[str, Any],
    mode: str | None = None,
    sealed_secret: bytes | None = None,
    sealed_selection: bytes | None = None,
    kid: str | None = None,
    group_id: str | None = None,
    connection_id: str | None = None,
    target_connection_id: str | None = None,
    object_ref: str | None = None,
    snapshot_id: str | None = None,
    purpose: dict[str, str] | None = None,
    created_by: str | None = None,
    idempotency_key: str | None = None,
    priority: int = 100,
    job_id: str | None = None,
) -> Job:
    """Insert a queued job (cap-checked) and notify workers.

    An idempotent replay (same ``idempotency_key``) returns the original job — before the queue
    cap is checked, so a replay is never refused as ``queue_full``. ``job_id`` lets a caller that
    seals with the id in the AAD mint it first. Raises :class:`QueueFull`.
    """
    if idempotency_key is not None:
        existing = await find_by_idempotency(tenant_id, idempotency_key)
        if existing is not None:
            return existing
    settings = db.active_settings()
    s = db.schema_ident()
    job_id = job_id or new_id("s" if kind == JobKind.scan else "j")
    try:
        async with db.acquire(tenant_id) as conn, conn.transaction():
            await _touch_tenant(conn, tenant_id)
            cap = await conn.fetchval(
                f"SELECT COALESCE((SELECT max_queued FROM {s}.dps_tenant WHERE tenant_id = $1), $2)",
                tenant_id,
                settings.job_tenant_max_queued,
            )
            active = await conn.fetchval(
                f"SELECT count(*) FROM {s}.dps_job WHERE tenant_id = $1 "
                "AND state = ANY($2::text[])",
                tenant_id,
                [x.value for x in ACTIVE_JOB_STATES],
            )
            if active >= cap:
                raise QueueFull()
            await conn.execute(
                f"INSERT INTO {s}.dps_job (id, tenant_id, kind, phase, mode, group_id, "
                "connection_id, target_connection_id, object_ref, snapshot_id, spec, purpose, "
                "key_enc, spec_enc, kid, priority, max_attempts, idempotency_key, created_by) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11::jsonb, $12::jsonb, $13, "
                "$14, $15, $16, $17, $18, $19)",
                job_id,
                tenant_id,
                kind.value,
                phase.value,
                mode,
                group_id,
                connection_id,
                target_connection_id,
                object_ref,
                snapshot_id,
                spec,
                purpose or {},
                sealed_secret,
                sealed_selection,
                kid,
                priority,
                settings.job_max_attempts,
                idempotency_key,
                created_by,
            )
            await conn.execute("SELECT pg_notify($1, $2)", NOTIFY_CHANNEL, kind.value)
    except asyncpg.UniqueViolationError:
        # A concurrent request with the same idempotency key won the insert.
        if idempotency_key is None:
            raise
        winner = await find_by_idempotency(tenant_id, idempotency_key)
        if winner is None:
            raise
        return winner
    job = await get_job(tenant_id, job_id)
    if job is None:  # RLS hides a row we just inserted only if the GUC binding is broken
        raise RuntimeError("created job is not visible to its tenant")
    return job


async def get_job(tenant_id: str, job_id: str) -> Job | None:
    """The tenant's job (RLS-scoped), or None."""
    async with db.acquire(tenant_id) as conn:
        row = await conn.fetchrow(
            _select(_PUBLIC, "j.tenant_id = $1 AND j.id = $2"), tenant_id, job_id
        )
    return _row_job(row) if row else None


async def find_by_idempotency(tenant_id: str, key: str) -> Job | None:
    """The tenant's job created with idempotency key ``key``, or None."""
    async with db.acquire(tenant_id) as conn:
        row = await conn.fetchrow(
            _select(_PUBLIC, "j.tenant_id = $1 AND j.idempotency_key = $2"), tenant_id, key
        )
    return _row_job(row) if row else None


async def list_jobs(
    tenant_id: str,
    *,
    kind: JobKind | None = None,
    state: JobState | None = None,
    group_id: str | None = None,
    limit: int = 50,
    cursor: str | None = None,
) -> tuple[list[Job], str | None]:
    """Newest first; keyset pagination on ``(created_at, id)``. ``ValueError`` on a bad cursor."""
    conds: list[str] = ["j.tenant_id = $1"]
    params: list[Any] = [tenant_id]
    for column, value in (("kind", kind), ("state", state), ("group_id", group_id)):
        if value is not None:
            params.append(str(value))
            conds.append(f"j.{column} = ${len(params)}")
    if cursor:
        ts, cid = _decode_cursor(cursor)
        params += [ts, cid]
        conds.append(f"(j.created_at, j.id) < (${len(params) - 1}::timestamptz, ${len(params)})")
    page = _clamp(limit)
    sql = (
        _select(_PUBLIC, " AND ".join(conds))
        + f" ORDER BY j.created_at DESC, j.id DESC LIMIT {page + 1}"
    )
    async with db.acquire(tenant_id) as conn:
        rows = await conn.fetch(sql, *params)
    items: list[Job] = [_row_job(r) for r in rows[:page]]
    nxt = _encode_cursor(items[-1].created_at, items[-1].id) if len(rows) > page and items else None
    return items, nxt


async def load_job_internal(job_id: str) -> JobInternal | None:
    """Worker-side full row (sealed blobs included); never returned by the API."""
    async with db.acquire_queue(None) as conn:
        row = await conn.fetchrow(_select(_INTERNAL, "j.id = $1"), job_id)
    return _row_job(row, JobInternal) if row else None


async def claim_jobs(worker_id: str, *, kinds: tuple[JobKind, ...], batch: int) -> list[ClaimedJob]:
    """Claim up to ``batch`` due job rows across tenants (FIFO, per-tenant running cap).

    The inner locking select takes the eligible rows in FIFO order (``ORDER BY`` the candidate
    ordinal), so a batch smaller than the eligible set claims the oldest, highest-priority rows.
    """
    if batch <= 0:
        return []
    settings = db.active_settings()
    s = db.schema_ident()
    sql = f"""
WITH running AS (
    SELECT tenant_id, count(*) AS n FROM (
        SELECT tenant_id FROM {s}.dps_job WHERE state = 'running'
        UNION ALL
        SELECT tenant_id FROM {s}.dps_job_partition WHERE state = 'running') r
    GROUP BY tenant_id),
ranked AS (
    SELECT j.id, j.tenant_id, j.priority, j.run_after, j.created_at,
           row_number() OVER (PARTITION BY j.tenant_id
                              ORDER BY j.priority, j.run_after, j.created_at, j.id) AS rn
    FROM {s}.dps_job j
    WHERE j.state = 'queued' AND j.run_after <= now() AND j.kind = ANY($3::text[])),
eligible AS (
    SELECT ranked.id,
           row_number() OVER (ORDER BY ranked.priority, ranked.run_after, ranked.created_at,
                                       ranked.id) AS ord
    FROM ranked
    LEFT JOIN running rc ON rc.tenant_id = ranked.tenant_id
    LEFT JOIN {s}.dps_tenant t ON t.tenant_id = ranked.tenant_id
    WHERE ranked.rn <= GREATEST(COALESCE(t.max_running, $4) - COALESCE(rc.n, 0), 0)
    ORDER BY ranked.priority, ranked.run_after, ranked.created_at, ranked.id
    LIMIT $5)
UPDATE {s}.dps_job j
SET state = 'running', locked_by = $1, attempts = j.attempts + 1,
    lease_expires_at = now() + make_interval(secs => $2),
    started_at = COALESCE(j.started_at, now()), updated_at = now()
FROM (SELECT q.id FROM {s}.dps_job q JOIN eligible e ON e.id = q.id
      WHERE q.state = 'queued'
      ORDER BY e.ord
      FOR UPDATE OF q SKIP LOCKED LIMIT $6) c
WHERE j.id = c.id
RETURNING j.id"""
    async with db.acquire_queue(None) as conn:
        async with conn.transaction():
            rows = await conn.fetch(
                sql,
                worker_id,
                float(settings.job_lease_seconds),
                [k.value for k in kinds],
                settings.job_tenant_max_running,
                max(batch * 4, 50),
                batch,
            )
        ids = [r["id"] for r in rows]
        full = (
            await conn.fetch(
                _select(_INTERNAL, "j.id = ANY($1::text[])")
                + " ORDER BY j.priority, j.run_after, j.created_at, j.id",
                ids,
            )
            if ids
            else []
        )
    claimed: list[ClaimedJob] = [_row_job(r, ClaimedJob) for r in full]
    for job in claimed:
        JOBS_CLAIMED.labels(job.kind.value, job.phase.value).inc()
    return claimed


async def heartbeat(
    worker_id: str, *, job_ids: list[str], partition_ids: list[int]
) -> tuple[list[str], list[int]]:
    """Renew leases; returns what this worker still owns (anything missing was reclaimed)."""
    settings = db.active_settings()
    s = db.schema_ident()
    owned_jobs: list[str] = []
    owned_parts: list[int] = []
    async with db.acquire_queue(None) as conn:
        if job_ids:
            rows = await conn.fetch(
                f"UPDATE {s}.dps_job SET lease_expires_at = now() + make_interval(secs => $1), "
                "updated_at = now() "
                "WHERE id = ANY($2::text[]) AND locked_by = $3 AND state = 'running' RETURNING id",
                float(settings.job_lease_seconds),
                job_ids,
                worker_id,
            )
            owned_jobs = [r["id"] for r in rows]
        if partition_ids:
            rows = await conn.fetch(
                f"UPDATE {s}.dps_job_partition "
                "SET lease_expires_at = now() + make_interval(secs => $1), updated_at = now() "
                "WHERE id = ANY($2::bigint[]) AND locked_by = $3 AND state = 'running' "
                "RETURNING id",
                float(settings.partition_lease_seconds),
                partition_ids,
                worker_id,
            )
            owned_parts = [r["id"] for r in rows]
    return owned_jobs, owned_parts


async def advance_job(
    worker_id: str,
    job_id: str,
    *,
    phase: JobPhase,
    state: JobState,
    progress: dict[str, Any] | None = None,
    error_code: str | None = None,
    error: str | None = None,
    purge_secret: bool = False,
    plan: dict[str, Any] | None = None,
    delay_seconds: float = 0.0,
) -> bool:
    """Fenced phase/state transition of a claimed job row. ``False`` = lease lost: stop now.

    A phase change resets ``attempts``; a non-running state releases the lease; a terminal state
    sets ``finished_at`` and ``expires_at`` (tenant retention, else ``DPS_RETENTION_DAYS``).
    """
    settings = db.active_settings()
    s = db.schema_ident()
    terminal = state in TERMINAL_JOB_STATES
    release_lock = state != JobState.running
    async with db.acquire_queue(None) as conn, conn.transaction():
        tag = await conn.execute(
            f"""UPDATE {s}.dps_job j SET
                phase = $3, state = $4, progress = j.progress || $5::jsonb,
                error_code = COALESCE($6, j.error_code), error = COALESCE($7, j.error),
                key_enc = CASE WHEN $8 THEN NULL ELSE j.key_enc END,
                plan = COALESCE($9::jsonb, j.plan),
                attempts = CASE WHEN j.phase <> $3 THEN 0 ELSE j.attempts END,
                locked_by = CASE WHEN $10 THEN NULL ELSE j.locked_by END,
                lease_expires_at = CASE WHEN $10 THEN NULL ELSE j.lease_expires_at END,
                run_after = CASE WHEN $4 = 'queued'
                                 THEN now() + make_interval(secs => $11) ELSE j.run_after END,
                finished_at = CASE WHEN $12 THEN now() ELSE NULL END,
                expires_at = CASE WHEN $12 THEN now() + make_interval(days => COALESCE(
                    (SELECT t.retention_days FROM {s}.dps_tenant t
                     WHERE t.tenant_id = j.tenant_id), $13))
                    ELSE j.expires_at END,
                updated_at = now()
            WHERE j.id = $1 AND j.locked_by = $2 AND j.state = 'running'""",
            job_id,
            worker_id,
            phase.value,
            state.value,
            progress or {},
            error_code,
            error[:500] if error is not None else None,
            purge_secret,
            plan,
            release_lock,
            float(delay_seconds),
            terminal,
            settings.retention_days,
        )
        applied = db.rowcount(tag) > 0
        if applied and state == JobState.queued:
            await conn.execute("SELECT pg_notify($1, $2)", NOTIFY_CHANNEL, phase.value)
    return applied


async def retry_job(
    worker_id: str,
    job_id: str,
    *,
    error_code: str,
    error: str,
    backoff_base: float,
    backoff_cap: float,
) -> JobState | None:
    """Requeue after a transient failure (backoff + jitter), or dead-letter when attempts are spent.

    Fenced; ``None`` means the lease was lost. A dead-lettered job's key is purged.
    """
    settings = db.active_settings()
    s = db.schema_ident()
    async with db.acquire_queue(None) as conn:
        row = await conn.fetchrow(
            f"""UPDATE {s}.dps_job j SET
                state = CASE WHEN j.attempts >= j.max_attempts THEN 'dead' ELSE 'queued' END,
                error_code = $3, error = $4, locked_by = NULL, lease_expires_at = NULL,
                key_enc = CASE WHEN j.attempts >= j.max_attempts THEN NULL ELSE j.key_enc END,
                run_after = now() + least($5, $6 * 2 ^ j.attempts) * (0.5 + random())
                            * interval '1 second',
                finished_at = CASE WHEN j.attempts >= j.max_attempts THEN now() END,
                expires_at = CASE WHEN j.attempts >= j.max_attempts
                    THEN now() + make_interval(days => COALESCE(
                        (SELECT t.retention_days FROM {s}.dps_tenant t
                         WHERE t.tenant_id = j.tenant_id), $7)) END,
                updated_at = now()
            WHERE j.id = $1 AND j.locked_by = $2 AND j.state = 'running' RETURNING j.state""",
            job_id,
            worker_id,
            error_code,
            error[:500],
            float(backoff_cap),
            float(backoff_base),
            settings.retention_days,
        )
        if row is not None and row["state"] == JobState.queued.value:
            await conn.execute("SELECT pg_notify($1, 'retry')", NOTIFY_CHANNEL)
    return JobState(row["state"]) if row else None


async def release(worker_id: str, *, job_ids: list[str], partition_ids: list[int]) -> None:
    """Voluntary requeue on drain/pause: no attempt is charged."""
    s = db.schema_ident()
    async with db.acquire_queue(None) as conn:
        if job_ids:
            await conn.execute(
                f"UPDATE {s}.dps_job SET state = 'queued', locked_by = NULL, "
                "lease_expires_at = NULL, run_after = now(), attempts = GREATEST(attempts - 1, 0), "
                "updated_at = now() WHERE id = ANY($1::text[]) AND locked_by = $2 "
                "AND state = 'running'",
                job_ids,
                worker_id,
            )
        if partition_ids:
            await conn.execute(
                f"UPDATE {s}.dps_job_partition SET state = 'queued', locked_by = NULL, "
                "lease_expires_at = NULL, run_after = now(), attempts = GREATEST(attempts - 1, 0), "
                "updated_at = now() WHERE id = ANY($1::bigint[]) AND locked_by = $2 "
                "AND state = 'running'",
                partition_ids,
                worker_id,
            )
        await conn.execute("SELECT pg_notify($1, 'released')", NOTIFY_CHANNEL)


async def register_worker(worker_id: str, *, role: str, version: str) -> None:
    """Upsert this process into ``dps_worker`` (role: ``worker`` | ``browser-worker``)."""
    async with db.acquire_queue(None) as conn:
        await conn.execute(
            f"INSERT INTO {db.schema_ident()}.dps_worker (worker_id, role, version) "
            "VALUES ($1, $2, $3) "
            "ON CONFLICT (worker_id) DO UPDATE SET last_seen = now(), draining = false",
            worker_id,
            role,
            version,
        )


async def worker_heartbeat(worker_id: str, *, busy: int, draining: bool) -> None:
    """Refresh ``last_seen``, the busy count and the draining flag."""
    async with db.acquire_queue(None) as conn:
        await conn.execute(
            f"UPDATE {db.schema_ident()}.dps_worker SET last_seen = now(), busy = $2, "
            "draining = $3 WHERE worker_id = $1",
            worker_id,
            busy,
            draining,
        )


async def unregister_worker(worker_id: str) -> None:
    """Remove this process from ``dps_worker`` (clean shutdown)."""
    async with db.acquire_queue(None) as conn:
        await conn.execute(
            f"DELETE FROM {db.schema_ident()}.dps_worker WHERE worker_id = $1", worker_id
        )


async def ensure_retention_job(interval_hours: int) -> bool:
    """Insert at most one retention job per interval bucket (idempotency key); True when inserted."""
    s = db.schema_ident()
    bucket = int(time.time() // (max(1, interval_hours) * 3600))
    async with db.acquire_queue(None) as conn, conn.transaction():
        await conn.execute(
            f"INSERT INTO {s}.dps_tenant (tenant_id) VALUES ($1) ON CONFLICT DO NOTHING",
            SYSTEM_TENANT,
        )
        tag = await conn.execute(
            f"INSERT INTO {s}.dps_job (id, tenant_id, kind, phase, spec, idempotency_key, "
            "priority) VALUES ($1, $2, 'retention', 'retention', '{}'::jsonb, $3, 200) "
            "ON CONFLICT (tenant_id, idempotency_key) WHERE idempotency_key IS NOT NULL "
            "DO NOTHING",
            new_id("j"),
            SYSTEM_TENANT,
            f"retention:{bucket}",
        )
        inserted = db.rowcount(tag) > 0
        if inserted:
            await conn.execute("SELECT pg_notify($1, 'retention')", NOTIFY_CHANNEL)
    return inserted


def worker_id() -> str:
    """Unique per process start: ``<hostname>:<pid>:<6 hex>`` — the fencing identity."""
    return f"{socket.gethostname()}:{os.getpid()}:{random.randint(0, 0xFFFFFF):06x}"
