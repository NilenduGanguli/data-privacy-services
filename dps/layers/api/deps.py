"""Shared dependencies and helpers of the M2 routers (contract §8, §9).

- :func:`require_db` — 503 ``db_unavailable`` while the metadata database is not configured or
  not reachable (Layer 1 routes never depend on it).
- :func:`tenant_of` — the caller's tenant (``X-DPS-Tenant``, resolved by the auth dependency); the
  job platform stores tenants in ``dps_tenant`` (lower-case ids), so any other id is 400
  ``tenant_invalid`` before a query runs.
- :func:`kek_of` — the job KEK loaded at startup (``None`` when unset).
- :func:`idempotency_key` — the ``Idempotency-Key`` header, shape-checked.
- :func:`audit_action` — one value-free audit event per M2 action (route template, action,
  outcome, status, job id, counts); a strict sink that cannot take it answers 503.
- :func:`audited` — runs a route body, maps its error with :func:`~dps.layers.api.errors.to_http`
  and audits the outcome either way.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable
from typing import Any

from fastapi import Request

from dps.audit import AuditEvent, AuditUnavailable
from dps.auth import Principal
from dps.layers.api.common import route_template, trace_id
from dps.layers.api.errors import DpsHttpError, to_http
from dps.observability import REQUESTS, get_logger

logger = get_logger(__name__)

IDEMPOTENCY_HEADER = "Idempotency-Key"
_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
#: ``dps_tenant.tenant_id``'s CHECK (migration 002), without the reserved ``_`` start.
_DB_TENANT = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")


def require_db(request: Request) -> None:
    """FastAPI dependency: 503 ``db_unavailable`` unless the metadata database is up."""
    state = getattr(request.app.state, "db_state", "unconfigured")
    if state == "unconfigured":
        raise DpsHttpError(503, "db_unavailable", "the metadata database is not configured")
    if state != "up":
        raise DpsHttpError(503, "db_unavailable", "the metadata database is unavailable")


def tenant_of(request: Request) -> str:
    """The tenant this call acts for (set by the auth dependency).

    Raises:
        DpsHttpError: 400 ``tenant_invalid`` for an id the job platform cannot store.
    """
    tenant = str(getattr(request.state, "tenant_id", ""))
    if not _DB_TENANT.fullmatch(tenant):
        raise DpsHttpError(
            400, "tenant_invalid", "the jobs API takes lower-case tenant ids ([a-z0-9_-], 1-63)"
        )
    return tenant


def principal_of(request: Request) -> Principal:
    """The authenticated principal (set by the auth dependency)."""
    principal: Principal = request.state.principal
    return principal


def kek_of(request: Request) -> bytes | None:
    """The job KEK the app loaded at startup, or ``None``."""
    kek = getattr(request.app.state, "kek", None)
    return kek if isinstance(kek, bytes) else None


def idempotency_key(request: Request) -> str | None:
    """The ``Idempotency-Key`` header (contract §9), or ``None``.

    Raises:
        DpsHttpError: 422 ``idempotency_key_invalid`` when it does not match
            ``^[A-Za-z0-9_.:-]{1,128}$`` (the key is never echoed).
    """
    value = request.headers.get(IDEMPOTENCY_HEADER)
    if value is None:
        return None
    if not _IDEMPOTENCY_KEY.fullmatch(value):
        raise DpsHttpError(
            422, "idempotency_key_invalid", "Idempotency-Key must match ^[A-Za-z0-9_.:-]{1,128}$"
        )
    return value


def audit_action(
    request: Request,
    *,
    action: str,
    outcome: str = "ok",
    status: int = 200,
    job_id: str | None = None,
    counts: dict[str, int] | None = None,
) -> None:
    """Write one value-free audit event for an M2 action and count the request.

    Raises:
        DpsHttpError: 503 ``audit_unavailable`` when a strict sink refuses the event. Any other
            sink failure is logged (class name only) and never fails the request.
    """
    route = route_template(request)
    REQUESTS.labels(route, outcome).inc()
    tid = getattr(request.state, "trace_id", None) or trace_id(request)
    request.state.trace_id = tid
    principal = getattr(request.state, "principal", None)
    event = AuditEvent(
        trace_id=tid,
        route=route,
        tenant_id=str(getattr(request.state, "tenant_id", "") or "-"),
        key_id=principal.key_id if principal is not None else "anonymous",
        mode="",
        policy="",
        outcome=outcome,
        action=action,
        status=status,
        job_id=job_id,
        counts=dict(counts or {}),
    )
    try:
        request.app.state.audit.write(event)
    except AuditUnavailable:
        raise DpsHttpError(503, "audit_unavailable", "audit unavailable") from None
    except Exception as exc:  # noqa: BLE001 - a non-strict sink failure never fails the request
        logger.warning("audit_write_failed", error=type(exc).__name__)


def rejected(
    request: Request, action: str, exc: Exception, *, job_id: str | None = None
) -> DpsHttpError:
    """Map ``exc`` with :func:`to_http`, audit it as ``rejected`` (4xx) or ``error`` (5xx) and
    return the error for the caller to raise (``raise rejected(...) from None``)."""
    err = to_http(exc)
    audit_action(
        request,
        action=action,
        outcome="rejected" if err.status < 500 else "error",
        status=err.status,
        job_id=job_id,
    )
    return err


async def audited[T](
    request: Request,
    action: str,
    body: Awaitable[T],
    *,
    status: int = 200,
    counts: dict[str, int] | None = None,
    job_id: str | None = None,
) -> T:
    """Await a route body; audit ``ok`` with ``status``, or the mapped error's status as
    ``rejected`` (4xx) / ``error`` (5xx) and raise it (value-free, no exception chain)."""
    try:
        result = await body
    except Exception as exc:  # noqa: BLE001 - mapped to a value-free {"detail", "code"}
        raise rejected(request, action, exc, job_id=job_id) from None
    audit_action(request, action=action, status=status, counts=counts, job_id=job_id)
    return result


async def guarded[T](body: Awaitable[T]) -> T:
    """Await a read-only route body, mapping its error with :func:`to_http` (not audited)."""
    try:
        return await body
    except Exception as exc:  # noqa: BLE001 - mapped to a value-free {"detail", "code"}
        raise to_http(exc) from None


def page_args(limit: int, cursor: str | None) -> dict[str, Any]:
    """``limit`` clamped to 1..200 (contract §9) and the opaque cursor."""
    return {"limit": max(1, min(200, limit)), "cursor": cursor or None}
