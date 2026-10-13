"""Tenants (M5, contract §2.4, §8.1): overrides, effective settings, daily usage, quotas, directory.

``dps_tenant`` is GLOBAL configuration (no RLS): one row per tenant holding overrides — ``NULL``
means "the settings default". Effective values (:func:`effective_tenant`) are cached per process
for :data:`CACHE_SECONDS`. Daily usage (``dps_tenant_usage``, a tenant table) is counted by the
worker at every checkpoint (:func:`dps.jobs.checkpoint`); :func:`check_daily_quota` refuses new runs
once the day's rows reach the quota, and the partition claim leaves a tenant's partitions queued
until 00:00 UTC. The tenant directory is the one cross-tenant read of tenant tables the app role
has: ``dps_tenant_directory()`` (migration 040) returns ids only.

Ids starting with ``_`` are DPS pseudo-tenants (``_system``, contract §3.6); the admin API can
never create, list or edit one. Nothing here logs or raises a value: errors name the rule.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from dps import db
from dps.auth import RESERVED_TENANT_PREFIX, WILDCARD, Principal
from dps.config import Settings
from dps.jobs import SYSTEM_TENANT
from dps.netguard import normalize_host
from dps.observability import get_logger

logger = get_logger(__name__)

__all__ = [
    "CACHE_SECONDS",
    "SYSTEM_TENANT",
    "TENANT_ID_PATTERN",
    "EffectiveTenant",
    "TenantError",
    "TenantPatch",
    "TenantSpec",
    "TenantUsage",
    "TenantView",
    "check_daily_quota",
    "create_tenant",
    "effective_tenant",
    "list_tenants",
    "tenant_directory",
    "tenant_usage",
    "update_tenant",
]

TENANT_ID_PATTERN = r"^[a-z0-9][a-z0-9_-]{0,62}$"
CACHE_SECONDS = 30.0
#: Override columns of ``dps_tenant`` (besides display name, note and hosts).
_OVERRIDES = ("retention_days", "max_running", "max_queued", "daily_row_quota")
_HIDDEN = ConfigDict(hide_input_in_errors=True, extra="forbid")
_cache: dict[str, tuple[float, EffectiveTenant]] = {}


class TenantError(Exception):
    """A tenant operation is refused (status + code + value-free detail)."""

    def __init__(self, status: int, code: str, detail: str) -> None:
        super().__init__(detail)
        self.status, self.code, self.detail = status, code, detail


def _hosts(value: list[str] | None) -> list[str] | None:
    if value is None:
        return None
    out: set[str] = set()
    for index, entry in enumerate(value):
        try:
            out.add(normalize_host(entry))
        except ValueError:
            raise ValueError(f"registered_hosts: entry {index} is not a valid host") from None
    return sorted(out)


class TenantPatch(BaseModel):
    """Fields to change; only the fields present in the request are written (``null`` resets an
    override to the settings default)."""

    model_config = _HIDDEN
    display_name: str | None = Field(None, max_length=120)
    retention_days: int | None = Field(None, ge=1, le=3650)
    max_running: int | None = Field(None, ge=1, le=256)
    max_queued: int | None = Field(None, ge=1, le=100_000)
    daily_row_quota: int | None = Field(None, ge=0)
    registered_hosts: list[str] | None = Field(None, max_length=100)
    note: str | None = Field(None, max_length=500)

    @field_validator("registered_hosts")
    @classmethod
    def _registered_hosts(cls, value: list[str] | None) -> list[str] | None:
        return _hosts(value)


class TenantSpec(BaseModel):
    """A new tenant record (``POST /admin/tenants``)."""

    model_config = _HIDDEN
    tenant_id: str = Field(pattern=TENANT_ID_PATTERN)
    display_name: str | None = Field(None, max_length=120)
    retention_days: int | None = Field(None, ge=1, le=3650)
    max_running: int | None = Field(None, ge=1, le=256)
    max_queued: int | None = Field(None, ge=1, le=100_000)
    daily_row_quota: int | None = Field(None, ge=0)
    registered_hosts: list[str] = Field(default_factory=list, max_length=100)
    note: str | None = Field(None, max_length=500)

    @field_validator("registered_hosts")
    @classmethod
    def _registered_hosts(cls, value: list[str]) -> list[str]:
        return _hosts(value) or []


class TenantUsage(BaseModel):
    """Live usage: running (incl. waiting) and queued jobs, rows written today (UTC)."""

    running: int = 0
    queued: int = 0
    rows_today: int = 0


class EffectiveTenant(BaseModel):
    """Overrides resolved over the settings defaults."""

    tenant_id: str
    retention_days: int
    max_running: int
    max_queued: int
    daily_row_quota: int | None = None
    registered_hosts: list[str] = Field(default_factory=list)


class TenantView(BaseModel):
    """One tenant as the Admin page shows it (configuration, usage and key counts only)."""

    tenant_id: str
    display_name: str | None = None
    note: str | None = None
    overrides: dict[str, int | None]
    effective: EffectiveTenant
    usage: TenantUsage
    keys: dict[str, int]
    exists: bool
    created_at: datetime | None = None
    updated_at: datetime | None = None


def _s() -> str:
    return db.schema_ident()


async def tenant_directory(*, include_system: bool = False, worker: bool = False) -> list[str]:
    """Every tenant id with a ``dps_tenant`` row, a job or a connection (ids only).

    Pseudo-tenants (``_system``) are included only when asked (retention); ``worker=True`` reads
    through the worker pool (the retention handler runs in a worker process).
    """
    acquire = db.acquire_queue if worker else db.acquire
    async with acquire(None) as conn:
        rows = await conn.fetch(f"SELECT tenant_id FROM {_s()}.dps_tenant_directory()")
    ids = [str(r["tenant_id"]) for r in rows]
    if include_system:
        return ids
    return [t for t in ids if not t.startswith(RESERVED_TENANT_PREFIX)]


async def _row(tenant_id: str) -> Any:
    async with db.acquire(None) as conn:
        return await conn.fetchrow(
            f"SELECT * FROM {_s()}.dps_tenant WHERE tenant_id = $1", tenant_id
        )


def _effective(tenant_id: str, row: Any, settings: Settings) -> EffectiveTenant:
    def pick(column: str, default: int) -> int:
        value = row[column] if row is not None else None
        return int(value) if value is not None else default

    quota = row["daily_row_quota"] if row is not None else None
    hosts = list(row["registered_hosts"] or []) if row is not None else []
    return EffectiveTenant(
        tenant_id=tenant_id,
        retention_days=pick("retention_days", settings.retention_days),
        max_running=pick("max_running", settings.job_tenant_max_running),
        max_queued=pick("max_queued", settings.job_tenant_max_queued),
        daily_row_quota=int(quota) if quota is not None else None,
        registered_hosts=hosts,
    )


async def effective_tenant(tenant_id: str, settings: Settings) -> EffectiveTenant:
    """Overrides over defaults, cached per process for :data:`CACHE_SECONDS`."""
    now = time.monotonic()
    hit = _cache.get(tenant_id)
    if hit is not None and hit[0] > now:
        return hit[1]
    value = _effective(tenant_id, await _row(tenant_id), settings)
    _cache[tenant_id] = (now + CACHE_SECONDS, value)
    return value


def _invalidate(tenant_id: str) -> None:
    _cache.pop(tenant_id, None)


async def tenant_usage(tenant_id: str) -> TenantUsage:
    """Running/waiting and queued jobs and today's written rows (RLS-scoped)."""
    s = _s()
    async with db.acquire(tenant_id) as conn:
        row = await conn.fetchrow(
            f"SELECT (SELECT count(*) FROM {s}.dps_job WHERE tenant_id = $1 "
            "AND state IN ('running', 'waiting'))::int AS running, "
            f"(SELECT count(*) FROM {s}.dps_job WHERE tenant_id = $1 "
            "AND state = 'queued')::int AS queued, "
            f"coalesce((SELECT rows_written FROM {s}.dps_tenant_usage WHERE tenant_id = $1 "
            "AND day = (now() AT TIME ZONE 'UTC')::date), 0)::bigint AS rows_today",
            tenant_id,
        )
    return TenantUsage(running=row["running"], queued=row["queued"], rows_today=row["rows_today"])


async def _key_counts(tenant_id: str) -> dict[str, int]:
    async with db.acquire(None) as conn:
        row = await conn.fetchrow(
            "SELECT count(*) FILTER (WHERE disabled_at IS NULL "
            "AND (expires_at IS NULL OR expires_at > now())) AS active, "
            "count(*) FILTER (WHERE disabled_at IS NOT NULL) AS disabled "
            f"FROM {_s()}.dps_api_key WHERE $1 = ANY(tenant_ids) OR '*' = ANY(tenant_ids)",
            tenant_id,
        )
    return {"active": int(row["active"]), "disabled": int(row["disabled"])}


async def _view(tenant_id: str, settings: Settings) -> TenantView:
    row = await _row(tenant_id)
    effective = _effective(tenant_id, row, settings)
    _cache[tenant_id] = (time.monotonic() + CACHE_SECONDS, effective)
    return TenantView(
        tenant_id=tenant_id,
        display_name=row["display_name"] if row is not None else None,
        note=row["note"] if row is not None else None,
        overrides={c: (row[c] if row is not None else None) for c in _OVERRIDES},
        effective=effective,
        usage=await tenant_usage(tenant_id),
        keys=await _key_counts(tenant_id),
        exists=row is not None,
        created_at=row["created_at"] if row is not None else None,
        updated_at=row["updated_at"] if row is not None else None,
    )


def _require_access(actor: Principal, tenant_id: str) -> None:
    if tenant_id.startswith(RESERVED_TENANT_PREFIX) or not actor.can_access(tenant_id):
        raise TenantError(403, "tenant_forbidden", "the tenant is not granted to this key")


async def list_tenants(actor: Principal, settings: Settings) -> list[TenantView]:
    """The directory (no pseudo-tenants) plus the actor's explicit grants, filtered by grants."""
    ids = set(await tenant_directory())
    ids |= {t for t in actor.tenant_ids if t != WILDCARD}
    visible = sorted(
        t for t in ids if not t.startswith(RESERVED_TENANT_PREFIX) and actor.can_access(t)
    )
    return [await _view(t, settings) for t in visible]


async def create_tenant(spec: TenantSpec, actor: Principal, settings: Settings) -> TenantView:
    """Insert the tenant's record. 409 ``tenant_exists`` when a row exists (use PATCH)."""
    _require_access(actor, spec.tenant_id)
    async with db.acquire(None) as conn:
        tag = await conn.execute(
            f"INSERT INTO {_s()}.dps_tenant (tenant_id, display_name, retention_days, max_running, "
            "max_queued, daily_row_quota, registered_hosts, note) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8) ON CONFLICT (tenant_id) DO NOTHING",
            spec.tenant_id,
            spec.display_name,
            spec.retention_days,
            spec.max_running,
            spec.max_queued,
            spec.daily_row_quota,
            spec.registered_hosts,
            spec.note,
        )
    if db.rowcount(tag) == 0:
        raise TenantError(409, "tenant_exists", "the tenant exists; change it with PATCH")
    _invalidate(spec.tenant_id)
    logger.info("tenant_created", tenant_id=spec.tenant_id)
    return await _view(spec.tenant_id, settings)


async def update_tenant(
    tenant_id: str, patch: TenantPatch, actor: Principal, settings: Settings
) -> TenantView:
    """Write the fields present in ``patch``; a retention change re-stamps the expiry of the
    tenant's terminal jobs, their evidence and artefacts in the same transaction."""
    _require_access(actor, tenant_id)
    fields = {f: getattr(patch, f) for f in patch.model_fields_set}
    if "registered_hosts" in fields and fields["registered_hosts"] is None:
        fields["registered_hosts"] = []
    s = _s()
    async with db.acquire(tenant_id) as conn, conn.transaction():
        exists = await conn.fetchval(
            f"SELECT 1 FROM {s}.dps_tenant WHERE tenant_id = $1 FOR UPDATE", tenant_id
        )
        if exists is None:
            raise TenantError(404, "tenant_not_found", "tenant not found")
        if fields:
            names = sorted(fields)
            sets = ", ".join(f"{n} = ${i + 2}" for i, n in enumerate(names))
            await conn.execute(
                f"UPDATE {s}.dps_tenant SET {sets}, updated_at = now() WHERE tenant_id = $1",
                tenant_id,
                *[fields[n] for n in names],
            )
        if "retention_days" in fields:
            days = fields["retention_days"] or settings.retention_days
            await conn.execute(
                f"UPDATE {s}.dps_job SET expires_at = finished_at + make_interval(days => $2), "
                "updated_at = now() WHERE tenant_id = $1 AND finished_at IS NOT NULL",
                tenant_id,
                days,
            )
            for table in ("dps_artefact", "dps_evidence"):
                await conn.execute(
                    f"UPDATE {s}.{table} a SET expires_at = j.expires_at FROM {s}.dps_job j "
                    "WHERE a.job_id = j.id AND a.tenant_id = $1 AND j.tenant_id = $1 "
                    "AND j.finished_at IS NOT NULL",
                    tenant_id,
                )
    _invalidate(tenant_id)
    logger.info("tenant_updated", tenant_id=tenant_id, count=len(fields))
    return await _view(tenant_id, settings)


async def check_daily_quota(tenant_id: str, settings: Settings) -> None:
    """Raise ``TenantError(429, "daily_row_quota_exceeded")`` once today's rows reach the quota.

    Reads the quota uncached (one primary-key lookup): a quota an admin just set applies to the
    next submission on every replica, like the claim-side check.
    """
    quota = _effective(tenant_id, await _row(tenant_id), settings).daily_row_quota
    if quota is None:
        return
    if (await tenant_usage(tenant_id)).rows_today >= quota:
        raise TenantError(
            429,
            "daily_row_quota_exceeded",
            "the tenant's daily row quota is used; new jobs are accepted after 00:00 UTC",
        )
