"""``@name[:vN]`` policy references for the routers (M5 Task 15, contract §5.5).

Wherever a request takes ``policy``, the string ``@<name>`` means the tenant's active published
version of ``<name>`` and ``@<name>:v<N>`` a pinned one. :func:`resolved_policy` turns it into the
stored body at accept time (a :class:`StoredPolicy`), so the engine and the job record see exactly
what ran; anything else (a preset name, an inline policy, ``None``) passes through unchanged.
:func:`policy_label` names a stored policy ``<name>@v<N>`` in audit and logs.

The policy store needs the metadata database: it is imported only when a reference is used, so
a Layer 1 install without the ``db`` extra keeps working with presets and inline policies.
Errors are coded HTTP errors (``{"detail", "code"}``): malformed → 400 ``invalid_policy_ref``;
unknown → 404 ``policy_not_found``; never published → 409 ``policy_not_published``; no database →
400 ``policy_store_unavailable`` (503 ``db_unavailable`` while a configured one is down).
"""

from __future__ import annotations

import re
import time
from typing import Any

import structlog
from fastapi import Request

from dps.layers.api.common import CodedHTTPException, begin_call, end_call
from dps.layers.api.common import policy_label as _inline_label

#: ``dps_tenant.tenant_id``'s shape (migration 002): the store keeps only such tenants.
_DB_TENANT = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")


class StoredPolicy(dict[str, Any]):
    """A published tenant policy version as a plain policy body (``name`` + ``version`` set).

    Only :func:`resolved_policy` makes one, from the store; a caller's inline dict never is one,
    so it can never be labelled as a stored version in the audit trail.
    """

    def __init__(self, body: dict[str, Any]) -> None:
        super().__init__(body)
        self.ref_name = str(body.get("name", ""))
        self.ref_version = int(body.get("version", 0))


def is_ref(policy: object) -> bool:
    """True for a string that asks for the policy store (``@...``)."""
    return isinstance(policy, str) and policy.startswith("@")


def policy_label(policy: str | dict[str, Any] | None, default: str) -> str:
    """``<name>@v<N>`` for a stored policy; otherwise M0's label (preset, ``…+inline:<hash>``)."""
    if isinstance(policy, StoredPolicy):
        return f"{policy.ref_name}@v{policy.ref_version}"
    return _inline_label(policy, default)


async def resolve(
    request: Request, policy: str | dict[str, Any] | None
) -> str | dict[str, Any] | None:
    """Resolve ``policy`` for the request's tenant (no audit; see :func:`resolved_policy`).

    Raises:
        CodedHTTPException: the reference cannot be resolved (codes in the module docstring).
    """
    if not is_ref(policy):
        return policy
    state = getattr(request.app.state, "db_state", "unconfigured")
    if state == "unconfigured":
        raise CodedHTTPException(
            400, "policy_store_unavailable", "policy references need the metadata database"
        )
    if state != "up":
        raise CodedHTTPException(503, "db_unavailable", "the metadata database is unavailable")
    tenant = str(getattr(request.state, "tenant_id", ""))
    if not _DB_TENANT.fullmatch(tenant):
        raise CodedHTTPException(404, "policy_not_found", "no such policy")
    from dps.policy_store import PolicyStoreError, resolve_ref

    try:
        body = await resolve_ref(policy, tenant)
    except PolicyStoreError as exc:
        raise CodedHTTPException(exc.status, exc.code, exc.detail) from None
    assert isinstance(body, dict)
    return StoredPolicy(body)


async def resolved_policy(
    request: Request, policy: str | dict[str, Any] | None, *, mode: str = "-"
) -> str | dict[str, Any] | None:
    """:func:`resolve`, auditing a refusal as one event of ``mode`` (outcome = the code).

    Use it where the reference is resolved outside :func:`~dps.layers.api.common.run_engine`.
    """
    try:
        return await resolve(request, policy)
    except CodedHTTPException as exc:
        tid = begin_call(request)
        try:
            end_call(
                request,
                mode=mode,
                policy="-",
                tid=tid,
                started=time.perf_counter(),
                outcome=exc.code,
                status_code=exc.status_code,
            )
        finally:
            structlog.contextvars.unbind_contextvars("trace_id", "route")
        raise
