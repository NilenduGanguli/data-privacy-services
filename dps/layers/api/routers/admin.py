"""``/api/v1/admin`` (contract §9, scope ``admin``): API keys, tenants, audit search, queue health.

Every route needs the ``admin`` scope and the metadata database. A key is shown once — in the
``201`` answer of create/rotate, with ``Cache-Control: no-store`` — and never listed, logged or
audited. Grants never escalate (:func:`dps.auth_db.check_grant`): a tenant admin manages keys and
tenants within its own grants only. Every mutating route writes one value-free audit event
``admin.<object>.<verb>`` (counts only); reads are not audited. Errors are ``{"detail", "code"}``.
"""

from __future__ import annotations

from collections.abc import Awaitable
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Body, Depends, Query, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from dps import db
from dps.audit_query import LAYERS, OUTCOME_GROUPS, decode_cursor, query_audit, stream_audit_csv
from dps.auth import RESERVED_TENANT_PREFIX, TENANT_ID, WILDCARD, Principal, require_scope
from dps.auth_db import (
    GrantError,
    create_key,
    disable_key,
    enable_key,
    list_keys,
    rotate_key,
)
from dps.config import Settings
from dps.layers.api.deps import audit_action, principal_of, require_db, tenant_of
from dps.layers.api.errors import DpsHttpError, to_http
from dps.posture import posture_report
from dps.ratelimit import enforce_rate_limit
from dps.tenants import (
    TenantError,
    TenantPatch,
    TenantSpec,
    create_tenant,
    list_tenants,
    tenant_directory,
    update_tenant,
)

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])
_ADMIN = [Depends(require_scope("admin")), Depends(enforce_rate_limit), Depends(require_db)]
#: Audit search window (default) and the longest window one request may span.
AUDIT_DEFAULT_DAYS = 14
AUDIT_MAX_DAYS = 400
_HIDDEN = ConfigDict(hide_input_in_errors=True, extra="forbid")
DEFAULT_GRACE_HOURS = 24


class KeyCreate(BaseModel):
    """Body of ``POST /admin/keys``."""

    model_config = _HIDDEN
    name: str = Field(max_length=63)
    tenant_ids: list[str] = Field(min_length=1, max_length=100)
    scopes: list[str] = Field(min_length=1, max_length=8)
    expires_in_days: int


class KeyRotate(BaseModel):
    """Body of ``POST /admin/keys/{id}/rotate``."""

    model_config = _HIDDEN
    grace_hours: int = Field(DEFAULT_GRACE_HOURS, ge=0)


def _settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def _mapped(exc: Exception) -> DpsHttpError:
    if isinstance(exc, GrantError | TenantError):
        return DpsHttpError(exc.status, exc.code, exc.detail)
    return to_http(exc)


async def _audited[T](
    request: Request,
    action: str,
    body: Awaitable[T],
    *,
    status: int = 200,
    counts: dict[str, int] | None = None,
) -> T:
    """Await a mutating body; audit it ``ok`` or ``rejected``/``error`` with the mapped status."""
    try:
        result = await body
    except Exception as exc:  # noqa: BLE001 - mapped to a value-free {"detail", "code"}
        err = _mapped(exc)
        audit_action(
            request,
            action=action,
            outcome="rejected" if err.status < 500 else "error",
            status=err.status,
        )
        raise err from None
    audit_action(request, action=action, status=status, counts=counts)
    return result


async def _guarded[T](body: Awaitable[T]) -> T:
    try:
        return await body
    except Exception as exc:  # noqa: BLE001 - mapped to a value-free {"detail", "code"}
        raise _mapped(exc) from None


async def _refresh_keys(request: Request, *invalidate: str) -> None:
    """Make a key change effective on this replica now (others follow within one refresh)."""
    store = getattr(request.app.state, "db_keys", None)
    if store is None:
        return
    for key_id in invalidate:
        store.invalidate(key_id)
    try:
        await store.refresh()
    except Exception:  # noqa: BLE001, S110 - the background refresher retries; never fails the call
        pass


# --- keys ----------------------------------------------------------------------------------------
@router.get("/keys", dependencies=_ADMIN)
async def keys(request: Request) -> dict[str, Any]:
    """DB and static keys whose tenants the caller covers (never a key or its hash)."""
    views = await _guarded(
        list_keys(
            actor=principal_of(request),
            static=request.app.state.auth.get("static"),
            settings=_settings(request),
        )
    )
    return {"items": [v.model_dump(mode="json") for v in views], "next_cursor": None}


@router.post("/keys", status_code=201, dependencies=_ADMIN)
async def create(body: KeyCreate, request: Request, response: Response) -> dict[str, Any]:
    """Mint a key; the raw key is in this answer only."""
    created = await _audited(
        request,
        "admin.key.create",
        create_key(
            name=body.name,
            tenant_ids=body.tenant_ids,
            scopes=body.scopes,
            expires_in_days=body.expires_in_days,
            actor=principal_of(request),
            settings=_settings(request),
        ),
        status=201,
        counts={"tenants": len(body.tenant_ids), "scopes": len(body.scopes)},
    )
    await _refresh_keys(request)
    response.headers["Cache-Control"] = "no-store"
    return created.model_dump(mode="json")


@router.post("/keys/{key_id}/rotate", status_code=201, dependencies=_ADMIN)
async def rotate(
    key_id: str,
    request: Request,
    response: Response,
    body: Annotated[KeyRotate | None, Body()] = None,
) -> dict[str, Any]:
    """Mint the successor (raw key once); the old key lives ``grace_hours`` more (0 = now)."""
    grace = body.grace_hours if body is not None else DEFAULT_GRACE_HOURS
    limit = _settings(request).key_rotation_max_grace_hours
    if grace > limit:
        raise DpsHttpError(422, "invalid_grace", f"grace_hours must be within 0..{limit}")
    new, old = await _audited(
        request,
        "admin.key.rotate",
        rotate_key(
            key_id, grace_hours=grace, actor=principal_of(request), settings=_settings(request)
        ),
        status=201,
        counts={"grace_hours": grace},
    )
    await _refresh_keys(request, *([key_id] if old.status != "active" else []))
    response.headers["Cache-Control"] = "no-store"
    return {"new": new.model_dump(mode="json"), "old": old.model_dump(mode="json")}


@router.post("/keys/{key_id}/disable", dependencies=_ADMIN)
async def disable(key_id: str, request: Request) -> dict[str, Any]:
    """Disable a key now (this replica at once, the others within one refresh)."""
    view = await _audited(
        request,
        "admin.key.disable",
        disable_key(key_id, actor=principal_of(request), settings=_settings(request)),
    )
    await _refresh_keys(request, key_id)
    return view.model_dump(mode="json")


@router.post("/keys/{key_id}/enable", dependencies=_ADMIN)
async def enable(key_id: str, request: Request) -> dict[str, Any]:
    """Re-enable a disabled key (never a rotated or an expired one)."""
    view = await _audited(
        request,
        "admin.key.enable",
        enable_key(key_id, actor=principal_of(request), settings=_settings(request)),
    )
    await _refresh_keys(request)
    return view.model_dump(mode="json")


# --- tenants -------------------------------------------------------------------------------------
@router.get("/tenants", dependencies=_ADMIN)
async def tenants(request: Request) -> dict[str, Any]:
    """Tenants the caller is granted: overrides, effective values, usage, key counts."""
    views = await _guarded(list_tenants(principal_of(request), _settings(request)))
    return {"items": [v.model_dump(mode="json") for v in views], "next_cursor": None}


@router.post("/tenants", status_code=201, dependencies=_ADMIN)
async def create_tenant_(body: TenantSpec, request: Request) -> dict[str, Any]:
    """Create a tenant record (409 ``tenant_exists`` when one exists)."""
    view = await _audited(
        request,
        "admin.tenant.create",
        create_tenant(body, principal_of(request), _settings(request)),
        status=201,
        counts={"hosts": len(body.registered_hosts)},
    )
    return view.model_dump(mode="json")


@router.patch("/tenants/{tenant_id}", dependencies=_ADMIN)
async def update_tenant_(tenant_id: str, body: TenantPatch, request: Request) -> dict[str, Any]:
    """Change a tenant's overrides, hosts, name or note (only the fields sent)."""
    view = await _audited(
        request,
        "admin.tenant.update",
        update_tenant(tenant_id, body, principal_of(request), _settings(request)),
        counts={"fields": len(body.model_fields_set)},
    )
    return view.model_dump(mode="json")


# --- audit ---------------------------------------------------------------------------------------
async def _audit_tenants(request: Request, value: str | None) -> list[str]:
    """The tenants an audit search reads: the request tenant by default; ``*`` = the directory
    (plus the caller's explicit grants) filtered by grants; else an explicit comma list."""
    principal: Principal = principal_of(request)
    if not value:
        return [tenant_of(request)]
    if value.strip() == WILDCARD:
        wildcard = WILDCARD in principal.tenant_ids
        ids = set(await tenant_directory(include_system=wildcard))
        ids |= {t for t in principal.tenant_ids if t != WILDCARD}
        return sorted(t for t in ids if principal.can_access(t))
    out: list[str] = []
    for tenant in (t.strip() for t in value.split(",")):
        if not tenant:
            continue
        if not TENANT_ID.fullmatch(tenant) and not tenant.startswith(RESERVED_TENANT_PREFIX):
            raise DpsHttpError(422, "invalid_tenant", "tenants must be tenant ids or *")
        if tenant.startswith(RESERVED_TENANT_PREFIX) and WILDCARD not in principal.tenant_ids:
            raise DpsHttpError(403, "tenant_forbidden", "the tenant is not granted to this key")
        if not principal.can_access(tenant):
            raise DpsHttpError(403, "tenant_forbidden", "the tenant is not granted to this key")
        out.append(tenant)
    return sorted(set(out))


def _window(since: datetime | None, until: datetime | None) -> tuple[datetime, datetime]:
    end = until or datetime.now(UTC) + timedelta(seconds=1)
    start = since or end - timedelta(days=AUDIT_DEFAULT_DAYS)
    if start.tzinfo is None or end.tzinfo is None:
        raise DpsHttpError(422, "invalid_window", "from and to need a time zone (ISO 8601)")
    if start >= end or end - start > timedelta(days=AUDIT_MAX_DAYS):
        raise DpsHttpError(
            422, "invalid_window", f"from must be before to, at most {AUDIT_MAX_DAYS} days apart"
        )
    return start, end


async def _key_names(request: Request) -> dict[str, str]:
    views = await list_keys(
        actor=principal_of(request),
        static=request.app.state.auth.get("static"),
        settings=_settings(request),
    )
    return {v.id: v.name for v in views}


@router.get("/audit", dependencies=_ADMIN, response_model=None)
async def audit(
    request: Request,
    since: Annotated[datetime | None, Query(alias="from")] = None,
    until: Annotated[datetime | None, Query(alias="to")] = None,
    action: Annotated[str | None, Query(max_length=64)] = None,
    layer: Literal["api", "conn", "ui", "admin"] | None = None,
    outcome: Literal["ok", "failed", "blocked"] | None = None,
    q: Annotated[str | None, Query(max_length=128)] = None,
    tenants: Annotated[str | None, Query(max_length=2000)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
    format: Literal["json", "csv"] = "json",  # noqa: A002 - the contract's query parameter
) -> dict[str, Any] | StreamingResponse:
    """Audit rows across the caller's tenants, newest first; ``format=csv`` streams them all."""
    assert layer is None or layer in LAYERS  # noqa: S101 - Literal-checked by FastAPI
    assert outcome is None or outcome in OUTCOME_GROUPS  # noqa: S101
    start, end = _window(since, until)
    try:
        position = decode_cursor(cursor) if cursor else None
    except ValueError:
        raise DpsHttpError(422, "invalid_cursor", "the cursor is not one this API issued") from None
    scope = await _audit_tenants(request, tenants)
    names = await _guarded(_key_names(request))
    filters: dict[str, Any] = {
        "tenants": scope,
        "since": start,
        "until": end,
        "action": action or None,
        "layer": layer,
        "outcome": outcome,
        "q": q,
        "key_names": names,
    }
    if format == "csv":
        name = f"dps-audit-{start.date().isoformat()}-{end.date().isoformat()}.csv"
        return StreamingResponse(
            stream_audit_csv(**filters),
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="{name}"',
                "Cache-Control": "no-store",
            },
        )
    rows, nxt = await _guarded(query_audit(**filters, limit=limit, cursor=position))
    return {"items": [r.model_dump(mode="json") for r in rows], "next_cursor": nxt}


# --- retention -----------------------------------------------------------------------------------
class RetentionRun(BaseModel):
    """Body of ``POST /admin/retention/run``."""

    model_config = _HIDDEN
    dry_run: bool = False


@router.post("/retention/run", status_code=202, dependencies=_ADMIN)
async def retention_run(
    request: Request, body: Annotated[RetentionRun | None, Body()] = None
) -> dict[str, Any]:
    """Queue an on-demand retention job (owned by ``_system``; it sweeps every tenant, so only a
    key granted every tenant may start it)."""
    from dps.retention import enqueue_retention

    principal = principal_of(request)
    if WILDCARD not in principal.tenant_ids:
        raise DpsHttpError(
            403,
            "tenant_forbidden",
            "retention runs across tenants: a key for every tenant is needed",
        )
    dry_run = body.dry_run if body is not None else False
    job = await _audited(
        request,
        "admin.retention.run",
        enqueue_retention(dry_run=dry_run, created_by=principal.key_id),
        status=202,
        counts={"dry_run": int(dry_run)},
    )
    return {"job": job.model_dump(mode="json")}


# --- queue ---------------------------------------------------------------------------------------
async def _queue(principal: Principal) -> dict[str, Any]:
    s = db.schema_ident()
    ids = set(await tenant_directory(include_system=WILDCARD in principal.tenant_ids))
    ids |= {t for t in principal.tenant_ids if t != WILDCARD}
    totals: dict[tuple[str, str], dict[str, float]] = {}
    for tenant in sorted(t for t in ids if principal.can_access(t)):
        async with db.acquire(tenant) as conn:
            rows = await conn.fetch(
                "SELECT 'dps_job' AS tbl, state, count(*) AS n, "
                "extract(epoch FROM now() - min(created_at)) AS oldest_s "
                f"FROM {s}.dps_job WHERE tenant_id = $1 GROUP BY state UNION ALL "
                "SELECT 'dps_job_partition', state, count(*), "
                "extract(epoch FROM now() - min(run_after)) "
                f"FROM {s}.dps_job_partition WHERE tenant_id = $1 GROUP BY state",
                tenant,
            )
        for r in rows:
            slot = totals.setdefault((r["tbl"], r["state"]), {"n": 0, "oldest_s": 0.0})
            slot["n"] += int(r["n"])
            slot["oldest_s"] = max(slot["oldest_s"], float(r["oldest_s"] or 0.0))
    async with db.acquire(None) as conn:
        workers = await conn.fetch(
            "SELECT worker_id, role, version, busy, draining, last_seen, "
            "last_seen > now() - interval '60 seconds' AS online "
            f"FROM {s}.dps_worker ORDER BY role, worker_id"
        )
    return {
        "queues": [
            {"table": t, "state": st, "n": int(v["n"]), "oldest_s": round(v["oldest_s"], 1)}
            for (t, st), v in sorted(totals.items())
        ],
        "workers": [
            {
                "worker_id": w["worker_id"],
                "role": w["role"],
                "version": w["version"],
                "online": bool(w["online"]),
                "busy": int(w["busy"]),
                "draining": bool(w["draining"]),
                "last_seen": w["last_seen"].isoformat(),
            }
            for w in workers
        ],
    }


@router.get("/queue", dependencies=_ADMIN)
async def queue(request: Request) -> dict[str, Any]:
    """Job and partition counts by state (the caller's tenants) and the worker registry."""
    return await _guarded(_queue(principal_of(request)))


@router.get("/posture", dependencies=[Depends(require_scope("admin")), Depends(enforce_rate_limit)])
async def posture(request: Request) -> dict[str, Any]:
    """The runtime posture report (M5 Task 16): eleven value-free checks, evaluated as if
    production; ``enforced`` in staging/prod. Database checks read "database unavailable"
    while it is down (this route itself does not need the database). Tenant policies and stale
    keys are scoped to the caller's tenants, like ``/admin/keys`` (review G4 B1)."""
    report = await posture_report(_settings(request), principal=principal_of(request))
    return report.model_dump(mode="json")
