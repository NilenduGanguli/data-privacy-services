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
import hmac
import os
import random
import socket
import time
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

import asyncpg
from pydantic import BaseModel, Field

from dps import db
from dps.errors import IDEMPOTENCY_KEY_REUSED, IDEMPOTENCY_KEY_REUSED_DETAIL
from dps.ids import new_id
from dps.jobs_models import JobProgress
from dps.layers.connectors.base import EncodedKey, KeyRange, key_fingerprint
from dps.observability import JOBS_CLAIMED, PARTITIONS_FINISHED, get_logger
from dps.secrets import load_kek

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


class IdempotencyKeyReused(JobStateError):
    """The ``Idempotency-Key`` names a job created from another request: 422
    ``idempotency_key_reused`` — the Layer 1 code (contract §9) — never a replay of that job."""

    status = 422

    def __init__(self) -> None:
        super().__init__(IDEMPOTENCY_KEY_REUSED, IDEMPOTENCY_KEY_REUSED_DETAIL)


def _same_request(stored: str | None, given: str | None) -> bool:
    """Whether a durable key's stored request fingerprint admits ``given``. A job admitted without
    a fingerprint (no KEK, or before migration 010) keeps the key-only replay."""
    return stored is None or given is None or hmac.compare_digest(stored, given)


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


def encode_cursor(created_at: datetime, job_id: str) -> str:
    """Opaque keyset cursor for ``(created_at, id)`` pagination (jobs, connections)."""
    raw = f"{created_at.isoformat()}|{job_id}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, str]:
    """Inverse of :func:`encode_cursor`; ``ValueError("malformed cursor")`` otherwise."""
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode("utf-8")
        ts, sep, job_id = raw.partition("|")
        if not sep or not job_id:
            raise ValueError
        return datetime.fromisoformat(ts), job_id
    except (ValueError, UnicodeDecodeError):
        raise ValueError("malformed cursor") from None


def _row_job(row: asyncpg.Record | dict[str, Any], model: type[Job] = Job) -> Any:
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


#: Advisory-lock namespace serialising one tenant's job admissions (cap check + insert).
_ADMIT_LOCK = "dps_job_admit"


class _Replayed(Exception):
    """Internal: the idempotency key was admitted by a concurrent request (ends the transaction)."""


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
    idempotency_fp: str | None = None,
    priority: int = 100,
    job_id: str | None = None,
) -> Job:
    """Insert a queued job (cap-checked) and notify workers.

    An idempotent replay (same ``idempotency_key`` and, when both are known, the same request
    fingerprint ``idempotency_fp``) returns the original job — before the queue cap is checked,
    so a replay is never refused as ``queue_full``; the same key with another fingerprint raises
    :class:`IdempotencyKeyReused` (contract §9). Admission is serialised per
    tenant (a transaction-scoped advisory lock taken before the count), so concurrent submissions
    cannot all pass the cap on the same count; a replay that waited behind the lock finds the
    original there. ``job_id`` lets a caller that seals with the id in the AAD mint it first.
    Raises :class:`QueueFull`, :class:`IdempotencyKeyReused`.
    """
    if idempotency_key is not None:
        existing = await find_by_idempotency(tenant_id, idempotency_key, fingerprint=idempotency_fp)
        if existing is not None:
            return existing
    settings = db.active_settings()
    s = db.schema_ident()
    job_id = job_id or new_id("s" if kind == JobKind.scan else "j")
    try:
        async with db.acquire(tenant_id) as conn, conn.transaction():
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended($1::text, 0))",
                f"{_ADMIT_LOCK}:{settings.pg_schema}:{tenant_id}",
            )
            if idempotency_key is not None:
                winner = await conn.fetchrow(
                    f"SELECT id, idempotency_fp FROM {s}.dps_job "
                    "WHERE tenant_id = $1 AND idempotency_key = $2",
                    tenant_id,
                    idempotency_key,
                )
                if winner is not None:
                    if not _same_request(winner["idempotency_fp"], idempotency_fp):
                        raise IdempotencyKeyReused()
                    job_id = winner["id"]
                    raise _Replayed()
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
                "key_enc, spec_enc, kid, priority, max_attempts, idempotency_key, created_by, "
                "idempotency_fp) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11::jsonb, $12::jsonb, $13, "
                "$14, $15, $16, $17, $18, $19, $20)",
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
                idempotency_fp,
            )
            await conn.execute("SELECT pg_notify($1, $2)", NOTIFY_CHANNEL, kind.value)
    except _Replayed:
        pass  # a concurrent request with the same key was admitted first: return its job
    except asyncpg.UniqueViolationError:
        # A concurrent request with the same idempotency key won the insert.
        if idempotency_key is None:
            raise
        winner = await find_by_idempotency(tenant_id, idempotency_key, fingerprint=idempotency_fp)
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


async def find_by_idempotency(
    tenant_id: str, key: str, *, fingerprint: str | None = None
) -> Job | None:
    """The tenant's job created with idempotency key ``key``, or None.

    Raises:
        IdempotencyKeyReused: ``fingerprint`` is given and the job was admitted with another
            request fingerprint (the same key, another request; contract §9).
    """
    async with db.acquire(tenant_id) as conn:
        row = await conn.fetchrow(
            _select(_PUBLIC + ", idempotency_fp", "j.tenant_id = $1 AND j.idempotency_key = $2"),
            tenant_id,
            key,
        )
    if row is None:
        return None
    data = dict(row)
    if not _same_request(data.pop("idempotency_fp"), fingerprint):
        raise IdempotencyKeyReused()
    return _row_job(data)


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
        ts, cid = decode_cursor(cursor)
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
    nxt = encode_cursor(items[-1].created_at, items[-1].id) if len(rows) > page and items else None
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


async def reseal_secret(worker_id: str, job_id: str, sealed_secret: bytes) -> bool:
    """Replace ``key_enc`` while this worker holds the job in ``prepare`` (M5: the sealed tabular
    model joins the job secret). Fenced like :func:`advance_job`; ``False`` = lease lost (or the
    job left ``prepare``): stop now."""
    async with db.acquire_queue(None) as conn:
        tag = await conn.execute(
            f"UPDATE {db.schema_ident()}.dps_job SET key_enc = $3, updated_at = now() "
            "WHERE id = $1 AND locked_by = $2 AND state = 'running' AND phase = 'prepare'",
            job_id,
            worker_id,
            sealed_secret,
        )
    return db.rowcount(tag) > 0


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


# --- partitions, the finalize transition, reaper, controls, stats (Task 9) -------------------

_MAYBE_FINALIZE = """UPDATE {s}.dps_job j
SET phase = 'finalize', state = 'queued', run_after = now(), attempts = 0, updated_at = now()
WHERE j.id = $1 AND j.phase = 'partitions' AND j.state IN ('waiting', 'paused')
  AND NOT EXISTS (SELECT 1 FROM {s}.dps_job_partition p
                  WHERE p.job_id = j.id AND p.state IN ('queued', 'running'))
RETURNING j.id"""

#: Jobs the finalize transition is owed to: the reaper re-checks each under its job lock.
_FINISHABLE_JOBS = """SELECT j.id FROM {s}.dps_job j
WHERE j.phase = 'partitions' AND j.state IN ('waiting', 'paused')
  AND NOT EXISTS (SELECT 1 FROM {s}.dps_job_partition p
                  WHERE p.job_id = j.id AND p.state IN ('queued', 'running'))
ORDER BY j.id"""


async def _lock_job(conn: asyncpg.Connection, job_id: str) -> None:
    """Lock the job row (always BEFORE touching its partitions in the same transaction)."""
    await conn.execute(
        f"SELECT 1 FROM {db.schema_ident()}.dps_job WHERE id = $1 FOR UPDATE", job_id
    )


async def _maybe_finalize(conn: asyncpg.Connection, job_id: str) -> bool:
    """Move a partitions-phase job to finalize when no partition is queued/running.

    Exactly once: every caller holds the job-row lock (:func:`_lock_job`) in the same
    transaction, so the last finisher's ``NOT EXISTS`` sees every earlier finisher's commit.
    """
    row = await conn.fetchrow(_MAYBE_FINALIZE.format(s=db.schema_ident()), job_id)
    if row is not None:
        await conn.execute("SELECT pg_notify($1, 'finalize')", NOTIFY_CHANNEL)
    return row is not None


async def add_partitions(
    worker_id: str,
    job_id: str,
    ranges: list[KeyRange],
    *,
    plan: dict[str, Any] | None = None,
    progress: dict[str, Any] | None = None,
) -> int:
    """Atomically replace the job's partitions and move it to ``partitions/waiting`` (fenced).

    Returns ``len(ranges)``, or 0 when the fence failed (the caller treats that as a lost lease).
    Partitions left by an earlier, crashed prepare attempt are deleted first.
    """
    settings = db.active_settings()
    s = db.schema_ident()
    async with db.acquire_queue(None) as conn, conn.transaction():
        tenant = await conn.fetchval(
            f"SELECT tenant_id FROM {s}.dps_job WHERE id = $1 AND locked_by = $2 "
            "AND state = 'running' FOR UPDATE",
            job_id,
            worker_id,
        )
        if tenant is None:
            return 0
        await conn.execute(f"DELETE FROM {s}.dps_job_partition WHERE job_id = $1", job_id)
        await conn.executemany(
            f"INSERT INTO {s}.dps_job_partition (job_id, tenant_id, partition_no, lower_key, "
            "upper_key, max_attempts) VALUES ($1, $2, $3, $4::jsonb, $5::jsonb, $6)",
            [
                (job_id, tenant, i, r.lower, r.upper, settings.partition_max_attempts)
                for i, r in enumerate(ranges)
            ],
        )
        await conn.execute(
            f"UPDATE {s}.dps_job SET phase = 'partitions', state = 'waiting', locked_by = NULL, "
            "lease_expires_at = NULL, attempts = 0, plan = COALESCE($2::jsonb, plan), "
            "progress = progress || $3::jsonb, updated_at = now() WHERE id = $1",
            job_id,
            plan,
            {**(progress or {}), "partitions_total": len(ranges)},
        )
        await conn.execute("SELECT pg_notify($1, 'partition')", NOTIFY_CHANNEL)
    return len(ranges)


async def claim_partitions(worker_id: str, *, batch: int) -> list[ClaimedPartition]:
    """Claim due partitions of ``waiting`` run jobs: per-job ``parallelism`` and per-tenant caps.

    The tenant cap ranks only the partitions each job may still start (its ``parallelism`` minus
    its running partitions), so an older job's queued backlog never starves a newer job of the
    tenant's free slots; the inner locking select takes the eligible rows in FIFO order.
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
job_running AS (
    SELECT job_id, count(*) AS n FROM {s}.dps_job_partition
    WHERE state = 'running' GROUP BY job_id),
ranked AS (
    SELECT p.id, p.tenant_id, p.job_id, p.partition_no, j.priority, j.created_at AS job_created,
           COALESCE((j.spec #>> '{{source,parallelism}}')::int, 4) AS par,
           row_number() OVER (PARTITION BY p.job_id ORDER BY p.run_after, p.partition_no) AS rn_job
    FROM {s}.dps_job_partition p JOIN {s}.dps_job j ON j.id = p.job_id
    WHERE p.state = 'queued' AND p.run_after <= now()
      AND j.state = 'waiting' AND j.phase = 'partitions'),
job_ok AS (
    SELECT r.id, r.tenant_id, r.job_id, r.partition_no, r.priority, r.job_created,
           row_number() OVER (PARTITION BY r.tenant_id
                              ORDER BY r.priority, r.job_created, r.job_id, r.partition_no)
               AS rn_tenant
    FROM ranked r LEFT JOIN job_running jr ON jr.job_id = r.job_id
    WHERE r.rn_job <= GREATEST(r.par - COALESCE(jr.n, 0), 0)),
eligible AS (
    SELECT o.id,
           row_number() OVER (ORDER BY o.priority, o.job_created, o.job_id, o.partition_no) AS ord
    FROM job_ok o
    LEFT JOIN running tr ON tr.tenant_id = o.tenant_id
    LEFT JOIN {s}.dps_tenant t ON t.tenant_id = o.tenant_id
    WHERE o.rn_tenant <= GREATEST(COALESCE(t.max_running, $3) - COALESCE(tr.n, 0), 0)
    ORDER BY o.priority, o.job_created, o.job_id, o.partition_no
    LIMIT $4)
UPDATE {s}.dps_job_partition p
SET state = 'running', locked_by = $1, attempts = p.attempts + 1,
    lease_expires_at = now() + make_interval(secs => $2),
    started_at = COALESCE(p.started_at, now()), updated_at = now()
FROM (SELECT q.id FROM {s}.dps_job_partition q JOIN eligible e ON e.id = q.id
      WHERE q.state = 'queued'
      ORDER BY e.ord
      FOR UPDATE OF q SKIP LOCKED LIMIT $5) c
WHERE p.id = c.id
RETURNING p.id, p.job_id, p.tenant_id, p.partition_no, p.lower_key, p.upper_key, p.last_key,
          p.batch_no, p.attempts, p.max_attempts, p.lease_expires_at, p.rows_written, p.resumed"""
    async with db.acquire_queue(None) as conn, conn.transaction():
        rows = await conn.fetch(
            sql,
            worker_id,
            float(settings.partition_lease_seconds),
            settings.job_tenant_max_running,
            max(batch * 4, 50),
            batch,
        )
    parts = [ClaimedPartition.model_validate(dict(r)) for r in rows]
    return sorted(parts, key=lambda p: (p.job_id, p.partition_no))


async def checkpoint(
    worker_id: str,
    partition_id: int,
    *,
    last_key: EncodedKey,
    batch_no: int,
    rows_read: int,
    rows_written: int,
    rows_skipped: int,
    values_by_method: dict[str, int],
    verify_checked: int,
) -> bool:
    """Record one committed batch (deltas) and renew the lease; fenced. ``False`` = stop now."""
    settings = db.active_settings()
    s = db.schema_ident()
    async with db.acquire_queue(None) as conn:
        tag = await conn.execute(
            f"UPDATE {s}.dps_job_partition SET last_key = $3::jsonb, batch_no = $4, "
            "rows_read = rows_read + $5, rows_written = rows_written + $6, "
            "rows_skipped = rows_skipped + $7, "
            f"values_by_method = {s}.dps_jsonb_add(values_by_method, $8::jsonb), "
            "verify_checked = verify_checked + $9, "
            "lease_expires_at = now() + make_interval(secs => $10), updated_at = now() "
            "WHERE id = $1 AND locked_by = $2 AND state = 'running'",
            partition_id,
            worker_id,
            last_key,
            batch_no,
            rows_read,
            rows_written,
            rows_skipped,
            values_by_method,
            verify_checked,
            float(settings.partition_lease_seconds),
        )
    return db.rowcount(tag) > 0


async def finish_partition(
    worker_id: str,
    partition_id: int,
    state: PartitionState,
    *,
    error_code: str | None = None,
    error: str | None = None,
    meta: dict[str, Any] | None = None,
) -> bool:
    """Terminal partition state (fenced) + the exactly-once finalize transition of its job."""
    s = db.schema_ident()
    async with db.acquire_queue(None) as conn, conn.transaction():
        job_id = await conn.fetchval(
            f"SELECT job_id FROM {s}.dps_job_partition WHERE id = $1", partition_id
        )
        if job_id is None:
            return False
        await _lock_job(conn, job_id)
        tag = await conn.execute(
            f"UPDATE {s}.dps_job_partition SET state = $3, "
            "error_code = COALESCE($4, error_code), error = COALESCE($5, error), "
            "meta = meta || COALESCE($6::jsonb, '{}'::jsonb), locked_by = NULL, "
            "lease_expires_at = NULL, finished_at = now(), updated_at = now() "
            "WHERE id = $1 AND locked_by = $2 AND state = 'running'",
            partition_id,
            worker_id,
            state.value,
            error_code,
            (error or "")[:500] or None,
            meta,
        )
        if db.rowcount(tag) == 0:
            return False
        await _maybe_finalize(conn, job_id)
    PARTITIONS_FINISHED.labels(state.value).inc()
    return True


async def retry_partition(
    worker_id: str,
    partition_id: int,
    *,
    error_code: str,
    error: str,
    backoff_base: float,
    backoff_cap: float,
) -> PartitionState | None:
    """Requeue with backoff (counts as a resume), or dead-letter at ``max_attempts`` (fenced)."""
    s = db.schema_ident()
    async with db.acquire_queue(None) as conn, conn.transaction():
        job_id = await conn.fetchval(
            f"SELECT job_id FROM {s}.dps_job_partition WHERE id = $1", partition_id
        )
        if job_id is None:
            return None
        await _lock_job(conn, job_id)
        row = await conn.fetchrow(
            f"""UPDATE {s}.dps_job_partition SET
                state = CASE WHEN attempts >= max_attempts THEN 'dead' ELSE 'queued' END,
                resumed = resumed + CASE WHEN attempts >= max_attempts THEN 0 ELSE 1 END,
                error_code = $3, error = $4, locked_by = NULL, lease_expires_at = NULL,
                run_after = now() + least($5, $6 * 2 ^ attempts) * (0.5 + random())
                            * interval '1 second',
                finished_at = CASE WHEN attempts >= max_attempts THEN now() END,
                updated_at = now()
            WHERE id = $1 AND locked_by = $2 AND state = 'running' RETURNING state""",
            partition_id,
            worker_id,
            error_code,
            error[:500],
            float(backoff_cap),
            float(backoff_base),
        )
        if row is None:
            return None
        if row["state"] == PartitionState.dead.value:
            await _maybe_finalize(conn, job_id)
        else:
            await conn.execute("SELECT pg_notify($1, 'partition')", NOTIFY_CHANNEL)
    if row["state"] == PartitionState.dead.value:
        PARTITIONS_FINISHED.labels(PartitionState.dead.value).inc()
    return PartitionState(row["state"])


async def reset_partition_progress(worker_id: str, partition_id: int) -> bool:
    """Zero a partition's checkpoint (``files`` targets restart a partition from its lower bound)."""
    async with db.acquire_queue(None) as conn:
        tag = await conn.execute(
            f"UPDATE {db.schema_ident()}.dps_job_partition SET last_key = NULL, batch_no = 0, "
            "rows_read = 0, rows_written = 0, rows_skipped = 0, values_by_method = '{}'::jsonb, "
            "verify_checked = 0, meta = '{}'::jsonb, updated_at = now() "
            "WHERE id = $1 AND locked_by = $2 AND state = 'running'",
            partition_id,
            worker_id,
        )
    return db.rowcount(tag) > 0


async def abandon_partition(worker_id: str, partition_id: int) -> None:
    """Drop this worker's lock on a partition that was canceled under it (unblocks cleanup)."""
    async with db.acquire_queue(None) as conn:
        await conn.execute(
            f"UPDATE {db.schema_ident()}.dps_job_partition SET locked_by = NULL, "
            "lease_expires_at = NULL WHERE id = $1 AND locked_by = $2 AND state = 'canceled'",
            partition_id,
            worker_id,
        )


async def partition_control(
    worker_id: str, partition_id: int
) -> Literal["continue", "pause", "cancel", "lost"]:
    """What a partition holder should do after a committed batch."""
    s = db.schema_ident()
    async with db.acquire_queue(None) as conn:
        row = await conn.fetchrow(
            f"SELECT p.state AS pstate, p.locked_by, j.state AS jstate "
            f"FROM {s}.dps_job_partition p JOIN {s}.dps_job j ON j.id = p.job_id WHERE p.id = $1",
            partition_id,
        )
    if row is None:
        return "lost"
    if row["pstate"] == "canceled" or row["jstate"] == "canceled":
        return "cancel"
    if row["locked_by"] != worker_id or row["pstate"] != "running":
        return "lost"
    return "pause" if row["jstate"] == "paused" else "continue"


async def list_partitions(tenant_id: str, job_id: str) -> list[PartitionView]:
    """Value-free partition rows for the console (checkpoint shown as a fingerprint only).

    The fingerprint is keyed under the job KEK and salted with the job id
    (:func:`~dps.layers.connectors.base.key_fingerprint`); without a KEK there is none.
    """
    kek = load_kek(db.active_settings())
    async with db.acquire(tenant_id) as conn:
        rows = await conn.fetch(
            "SELECT partition_no, state, rows_read, rows_written, rows_skipped, batch_no, "
            "attempts, resumed, last_key, started_at, finished_at, updated_at, error_code, "
            "CASE WHEN started_at IS NOT NULL AND rows_written > 0 THEN rows_written / "
            "GREATEST(extract(epoch FROM COALESCE(finished_at, updated_at) - started_at), 0.001) "
            "END AS rps "
            f"FROM {db.schema_ident()}.dps_job_partition WHERE job_id = $1 ORDER BY partition_no",
            job_id,
        )
    return [
        PartitionView(
            partition_no=r["partition_no"],
            state=PartitionState(r["state"]),
            rows_read=r["rows_read"],
            rows_written=r["rows_written"],
            rows_skipped=r["rows_skipped"],
            batch_no=r["batch_no"],
            attempts=r["attempts"],
            resumed=r["resumed"],
            checkpoint_fp=(
                key_fingerprint(r["last_key"], kek=kek, job_id=job_id) if kek is not None else None
            ),
            started_at=r["started_at"],
            finished_at=r["finished_at"],
            updated_at=r["updated_at"],
            error_code=r["error_code"],
            rows_per_second=float(r["rps"]) if r["rps"] is not None else None,
        )
        for r in rows
    ]


async def reap(*, backoff_base: float, backoff_cap: float) -> ReapResult:
    """Requeue expired leases with backoff, dead-letter exhausted ones, prune offline workers.

    Each statement is its own transaction. Last, every pass finalizes each ``partitions``-phase
    job (waiting or paused) that has no queued or running partition left, one short transaction
    per job (job lock first), so the reaper never holds a partition lock while it waits for a job
    lock and cannot deadlock a finisher. Sweeping all such jobs, not just those dead-lettered in
    this pass, makes the pass self-healing: a pass interrupted after its dead-letter committed
    (SIGTERM, a kill, a reset connection) is finalized by the next one (contract §3.1).
    """
    settings = db.active_settings()
    s = db.schema_ident()
    backoff = "now() + least($1, $2 * 2 ^ attempts) * (0.5 + random()) * interval '1 second'"
    result = ReapResult()
    async with db.acquire_queue(None) as conn:
        rows = await conn.fetch(
            f"UPDATE {s}.dps_job SET state = 'queued', locked_by = NULL, lease_expires_at = NULL, "
            f"run_after = {backoff}, updated_at = now() WHERE state = 'running' "
            "AND lease_expires_at < now() AND attempts < max_attempts RETURNING id",
            float(backoff_cap),
            float(backoff_base),
        )
        result.jobs_requeued = len(rows)
        rows = await conn.fetch(
            f"UPDATE {s}.dps_job j SET state = 'dead', key_enc = NULL, "
            "error_code = COALESCE(error_code, 'lease_lost'), "
            "error = COALESCE(error, 'lease expired; max attempts reached'), locked_by = NULL, "
            "lease_expires_at = NULL, finished_at = now(), "
            "expires_at = now() + make_interval(days => COALESCE("
            f"(SELECT t.retention_days FROM {s}.dps_tenant t WHERE t.tenant_id = j.tenant_id), "
            "$1)), updated_at = now() "
            "WHERE state = 'running' AND lease_expires_at < now() AND attempts >= max_attempts "
            "RETURNING id",
            settings.retention_days,
        )
        result.jobs_dead = len(rows)
        rows = await conn.fetch(
            f"UPDATE {s}.dps_job_partition SET state = 'queued', locked_by = NULL, "
            f"lease_expires_at = NULL, resumed = resumed + 1, run_after = {backoff}, "
            "updated_at = now() WHERE state = 'running' AND lease_expires_at < now() "
            "AND attempts < max_attempts RETURNING job_id",
            float(backoff_cap),
            float(backoff_base),
        )
        result.partitions_requeued = len(rows)
        result.partitions_dead = db.rowcount(
            await conn.execute(
                f"UPDATE {s}.dps_job_partition SET state = 'dead', "
                "error_code = COALESCE(error_code, 'lease_lost'), locked_by = NULL, "
                "lease_expires_at = NULL, finished_at = now(), updated_at = now() "
                "WHERE state = 'running' AND lease_expires_at < now() "
                "AND attempts >= max_attempts"
            )
        )
        if result.partitions_dead:
            PARTITIONS_FINISHED.labels(PartitionState.dead.value).inc(result.partitions_dead)
        result.partitions_released = db.rowcount(
            await conn.execute(
                f"UPDATE {s}.dps_job_partition SET locked_by = NULL, lease_expires_at = NULL "
                "WHERE state = 'canceled' AND locked_by IS NOT NULL AND lease_expires_at < now()"
            )
        )
        heartbeat_s = max(1.0, settings.partition_lease_seconds / 4)
        result.workers_pruned = db.rowcount(
            await conn.execute(
                f"DELETE FROM {s}.dps_worker WHERE last_seen < now() - make_interval(secs => $1)",
                3 * heartbeat_s,
            )
        )
        if result.jobs_requeued or result.partitions_requeued:
            await conn.execute("SELECT pg_notify($1, 'reaped')", NOTIFY_CHANNEL)
        finishable = await conn.fetch(_FINISHABLE_JOBS.format(s=s))
        for job_id in [r["id"] for r in finishable]:
            async with conn.transaction():
                await _lock_job(conn, job_id)
                await _maybe_finalize(conn, job_id)
    return result


async def cancel(tenant_id: str, job_id: str) -> Job | None:
    """Cancel (contract §3.1): run jobs get a cancel-finalize; other kinds become ``canceled`` now.

    ``None`` when the tenant has no such job; :class:`JobStateError` (``job_terminal``,
    ``job_finalizing``) when it can no longer be canceled.
    """
    settings = db.active_settings()
    s = db.schema_ident()
    async with db.acquire(tenant_id) as conn, conn.transaction():
        row = await conn.fetchrow(
            f"SELECT kind, state, phase FROM {s}.dps_job WHERE id = $1 FOR UPDATE", job_id
        )
        if row is None:
            return None
        if JobState(row["state"]) in TERMINAL_JOB_STATES:
            raise JobStateError("job_terminal", "the job has already finished")
        if row["phase"] == JobPhase.finalize.value:
            raise JobStateError(
                "job_finalizing", "the job is finalizing and can no longer be canceled"
            )
        # Running partitions keep locked_by: their holders' next fenced write fails and they
        # abandon; the reaper releases the lock of a holder that never comes back.
        await conn.execute(
            f"UPDATE {s}.dps_job_partition SET state = 'canceled', finished_at = now(), "
            "updated_at = now() WHERE job_id = $1 AND state IN ('queued', 'running')",
            job_id,
        )
        if row["kind"] == JobKind.run.value:
            await conn.execute(
                f"UPDATE {s}.dps_job SET phase = 'finalize', state = 'queued', run_after = now(), "
                "attempts = 0, locked_by = NULL, lease_expires_at = NULL, "
                'progress = progress || \'{"outcome": "canceled"}\'::jsonb, updated_at = now() '
                "WHERE id = $1",
                job_id,
            )
            await conn.execute("SELECT pg_notify($1, 'finalize')", NOTIFY_CHANNEL)
        else:
            await conn.execute(
                f"UPDATE {s}.dps_job j SET state = 'canceled', locked_by = NULL, "
                "lease_expires_at = NULL, key_enc = NULL, finished_at = now(), "
                'progress = progress || \'{"outcome": "canceled"}\'::jsonb, '
                "expires_at = now() + make_interval(days => COALESCE((SELECT t.retention_days "
                f"FROM {s}.dps_tenant t WHERE t.tenant_id = j.tenant_id), $2)), "
                "updated_at = now() WHERE id = $1",
                job_id,
                settings.retention_days,
            )
    return await get_job(tenant_id, job_id)


async def pause(tenant_id: str, job_id: str) -> Job | None:
    """``waiting`` run job → ``paused`` (running partitions stop after their current batch)."""
    return await _flip(
        tenant_id,
        job_id,
        "waiting",
        "paused",
        "job_not_pausable",
        "only a run job that is processing partitions can be paused",
    )


async def resume(tenant_id: str, job_id: str) -> Job | None:
    """``paused`` → ``waiting`` (and finalize when every partition already finished)."""
    return await _flip(
        tenant_id, job_id, "paused", "waiting", "job_not_paused", "the job is not paused"
    )


async def _flip(
    tenant_id: str, job_id: str, src: str, dst: str, code: str, detail: str
) -> Job | None:
    s = db.schema_ident()
    async with db.acquire(tenant_id) as conn, conn.transaction():
        row = await conn.fetchrow(
            f"SELECT state, phase FROM {s}.dps_job WHERE id = $1 FOR UPDATE", job_id
        )
        if row is None:
            return None
        if row["state"] != src or row["phase"] != JobPhase.partitions.value:
            raise JobStateError(code, detail)
        await conn.execute(
            f"UPDATE {s}.dps_job SET state = $2, updated_at = now() WHERE id = $1", job_id, dst
        )
        if dst == JobState.waiting.value:
            await _maybe_finalize(conn, job_id)
            await conn.execute("SELECT pg_notify($1, 'partition')", NOTIFY_CHANNEL)
    return await get_job(tenant_id, job_id)


async def retry(tenant_id: str, job_id: str, *, sealed_secret: bytes | None = None) -> Job | None:
    """Requeue a ``failed``/``dead`` run job from its checkpoints (contract §3.1).

    Failed, dead and canceled partitions go back to ``queued`` (attempts reset) and resume from
    their checkpoints; a job without partitions restarts at ``prepare``. Raises
    :class:`KeyMaterialRequired` when the key was purged and none is resubmitted.
    """
    s = db.schema_ident()
    async with db.acquire(tenant_id) as conn, conn.transaction():
        row = await conn.fetchrow(
            f"SELECT kind, state, key_enc IS NOT NULL AS has_key FROM {s}.dps_job "
            "WHERE id = $1 FOR UPDATE",
            job_id,
        )
        if row is None:
            return None
        if row["kind"] != JobKind.run.value or row["state"] not in ("failed", "dead"):
            raise JobStateError("job_not_retryable", "only failed or dead run jobs can be retried")
        if not row["has_key"] and sealed_secret is None:
            raise KeyMaterialRequired()
        parts = await conn.fetchval(
            f"SELECT count(*) FROM {s}.dps_job_partition WHERE job_id = $1", job_id
        )
        if parts:
            await conn.execute(
                f"UPDATE {s}.dps_job_partition SET state = 'queued', attempts = 0, "
                "run_after = now(), error_code = NULL, error = NULL, locked_by = NULL, "
                "lease_expires_at = NULL, finished_at = NULL, updated_at = now() "
                "WHERE job_id = $1 AND state IN ('failed', 'dead', 'canceled')",
                job_id,
            )
        phase, state = ("partitions", "waiting") if parts else ("prepare", "queued")
        await conn.execute(
            f"UPDATE {s}.dps_job SET phase = $2, state = $3, attempts = 0, run_after = now(), "
            "error_code = NULL, error = NULL, finished_at = NULL, expires_at = NULL, "
            'key_enc = COALESCE($4, key_enc), progress = progress || \'{"outcome": ""}\'::jsonb, '
            "locked_by = NULL, lease_expires_at = NULL, updated_at = now() WHERE id = $1",
            job_id,
            phase,
            state,
            sealed_secret,
        )
        if parts:
            # every partition was already done: go straight to finalize
            await _maybe_finalize(conn, job_id)
        await conn.execute("SELECT pg_notify($1, 'retry')", NOTIFY_CHANNEL)
    return await get_job(tenant_id, job_id)


async def queue_stats() -> list[dict[str, Any]]:
    """Per-table, per-state counts and oldest age — the queue gauges (worker pool, cross-tenant)."""
    s = db.schema_ident()
    async with db.acquire_queue(None) as conn:
        rows = await conn.fetch(
            "SELECT 'dps_job' AS table, state, count(*) AS n, "
            f"extract(epoch FROM now() - min(created_at)) AS oldest_s FROM {s}.dps_job "
            "GROUP BY state UNION ALL "
            "SELECT 'dps_job_partition', state, count(*), "
            f"extract(epoch FROM now() - min(run_after)) FROM {s}.dps_job_partition "
            "GROUP BY state"
        )
    return [dict(r) for r in rows]


async def tenant_stats(tenant_id: str) -> JobStats:
    """The Jobs page health strip (mockup 07) for one tenant (worker counts are global)."""
    settings = db.active_settings()
    s = db.schema_ident()
    heartbeat_s = max(1.0, settings.partition_lease_seconds / 4)
    async with db.acquire(tenant_id) as conn:
        w = await conn.fetchrow(
            "SELECT count(*) AS online, count(*) FILTER (WHERE busy > 0) AS busy, "
            f"count(*) FILTER (WHERE draining) AS draining FROM {s}.dps_worker "
            "WHERE role = 'worker' AND last_seen > now() - make_interval(secs => $1)",
            3 * heartbeat_s,
        )
        waiting = await conn.fetchrow(
            "SELECT count(*) AS n, extract(epoch FROM now() - min(p.run_after)) AS oldest "
            f"FROM {s}.dps_job_partition p JOIN {s}.dps_job j ON j.id = p.job_id "
            "WHERE p.state = 'queued' AND j.state = 'waiting'"
        )
        running = await conn.fetchval(
            f"SELECT (SELECT count(*) FROM {s}.dps_job WHERE state = 'running') + "
            f"(SELECT count(*) FROM {s}.dps_job_partition WHERE state = 'running')"
        )
        rps = await conn.fetchval(
            f"SELECT COALESCE(sum((progress->>'rows_per_second')::float), 0) FROM {s}.dps_job "
            "WHERE state IN ('waiting', 'paused') AND phase = 'partitions'"
        )
        by_state = {
            r["state"]: int(r["n"])
            for r in await conn.fetch(
                f"SELECT state, count(*) AS n FROM {s}.dps_job "
                "WHERE created_at > now() - interval '7 days' GROUP BY state"
            )
        }
        cap = await conn.fetchval(
            f"SELECT max_running FROM {s}.dps_tenant WHERE tenant_id = $1", tenant_id
        )
        reclaims = await conn.fetchval(
            f"SELECT COALESCE(sum(resumed), 0) FROM {s}.dps_job_partition "
            "WHERE updated_at > now() - interval '24 hours'"
        )
    return JobStats(
        workers_online=w["online"],
        workers_busy=w["busy"],
        workers_draining=w["draining"],
        partitions_waiting=waiting["n"],
        oldest_waiting_s=float(waiting["oldest"]) if waiting["oldest"] is not None else None,
        rows_per_s=float(rps or 0),
        by_state=by_state,
        running=int(running),
        tenant_max_running=int(cap or settings.job_tenant_max_running),
        lease_reclaims_24h=int(reclaims),
    )


async def sample_throughput(*, min_interval_s: float = 10.0) -> int:
    """Append a ``(ts, rows/s)`` point (≤ 60 kept) to each processing run job; returns jobs sampled."""
    s = db.schema_ident()
    sampled = 0
    async with db.acquire_queue(None) as conn, conn.transaction():
        rows = await conn.fetch(
            "SELECT j.id, j.progress, j.started_at, now() AS now, "
            f"(SELECT COALESCE(sum(p.rows_written), 0) FROM {s}.dps_job_partition p "
            f"WHERE p.job_id = j.id) AS written FROM {s}.dps_job j "
            "WHERE j.kind = 'run' AND j.phase = 'partitions' AND j.state IN ('waiting', 'paused') "
            "FOR UPDATE OF j SKIP LOCKED"
        )
        for r in rows:
            progress = r["progress"] or {}
            mark = progress.get("throughput_mark")
            last_ts = datetime.fromisoformat(mark[0]) if mark else (r["started_at"] or r["now"])
            last_rows = int(mark[1]) if mark else 0
            elapsed = (r["now"] - last_ts).total_seconds()
            if elapsed < min_interval_s or elapsed <= 0:
                continue
            rate = round(max(0, int(r["written"]) - last_rows) / elapsed, 1)
            points = (progress.get("throughput") or [])[-59:] + [[r["now"].isoformat(), rate]]
            await conn.execute(
                f"UPDATE {s}.dps_job SET progress = progress || $2::jsonb, updated_at = now() "
                "WHERE id = $1",
                r["id"],
                {
                    "throughput": points,
                    "throughput_mark": [r["now"].isoformat(), int(r["written"])],
                    "rows_per_second": rate,
                },
            )
            sampled += 1
    return sampled
