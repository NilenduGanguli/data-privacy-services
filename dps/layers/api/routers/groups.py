"""``/api/v1/job-groups`` (contract §9, scope ``jobs``).

A job group shares one key and one synthesis seed across runs (cross-table and cross-system
consistency, §6.5). Both are generated at create and returned **once** (``Cache-Control:
no-store``); DPS stores only the key id and the seed's fingerprint.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from dps import jobs
from dps.auth import require_scope
from dps.connections import Connection, get_connection
from dps.groups import (
    GROUP_NAME_RE,
    create_group,
    discard_group,
    get_group,
    list_groups,
    save_last_check,
)
from dps.jobs import JobKind
from dps.jobs_models import ClassificationSnapshot
from dps.layers.api.deps import (
    audit_action,
    guarded,
    kek_of,
    page_args,
    principal_of,
    rejected,
    require_db,
    tenant_of,
)
from dps.layers.api.errors import DpsHttpError
from dps.layers.connectors.base import ConnectorError
from dps.layers.connectors.consistency import (
    ConsistencyCheckRequest,
    ConsistencyCheckResult,
    ConsistencyError,
    run_consistency_check,
)
from dps.ratelimit import enforce_rate_limit
from dps.secrets import SecretUnavailable
from dps.snapshots import get_snapshot

router = APIRouter(prefix="/api/v1", tags=["job-groups"])
_JOBS = [Depends(require_scope("jobs")), Depends(enforce_rate_limit), Depends(require_db)]
#: Connector codes of an unreachable member target (503); every other connector code is 502.
_UNAVAILABLE = frozenset({"connection_failed", "secret_unavailable", "engine_unavailable"})
_CHECK_ACTION = "group.consistency_check"


class GroupRequest(BaseModel):
    """Body of ``POST /job-groups``."""

    model_config = ConfigDict(hide_input_in_errors=True)
    name: str = Field(pattern=GROUP_NAME_RE.pattern)


@router.post("/job-groups", status_code=201, dependencies=_JOBS)
async def create(body: GroupRequest, request: Request, response: Response) -> dict[str, Any]:
    """Create a group; the key material and seed are in this answer and nowhere else."""

    tenant = tenant_of(request)
    try:
        group, key, seed = await create_group(
            tenant, body.name, created_by=principal_of(request).key_id
        )
    except Exception as exc:  # noqa: BLE001 - mapped to a value-free {"detail", "code"}
        raise rejected(request, "group.create", exc) from None
    try:
        audit_action(request, action="group.create", status=201)
    except DpsHttpError as exc:
        # F-14: the key and seed are returned once; a group whose answer fails is removed
        await discard_group(tenant, group.id)
        with contextlib.suppress(DpsHttpError):  # best effort: the sink may still be full
            rejected(request, "group.create", exc)
        raise
    response.headers["Cache-Control"] = "no-store"
    return {
        "group": group.model_dump(mode="json"),
        "key_material": key.model_dump(mode="json"),
        "synthesis_seed": seed.model_dump(mode="json"),
    }


@router.get("/job-groups", dependencies=_JOBS)
async def list_(request: Request, limit: int = 50, cursor: str | None = None) -> dict[str, Any]:
    """The tenant's groups, newest first (identifiers only)."""

    async def body() -> dict[str, Any]:
        items, nxt = await list_groups(tenant_of(request), **page_args(limit, cursor))
        return {"items": [g.model_dump(mode="json") for g in items], "next_cursor": nxt}

    return await guarded(body())


@router.get("/job-groups/{group_id}", dependencies=_JOBS)
async def get(group_id: str, request: Request) -> dict[str, Any]:
    """A group and its jobs (newest first, at most 200)."""

    async def body() -> dict[str, Any]:
        tenant = tenant_of(request)
        group = await get_group(tenant, group_id)
        if group is None:
            raise DpsHttpError(404, "group_not_found", "job group not found")
        members, _ = await jobs.list_jobs(tenant, group_id=group_id, limit=200)
        return {
            "group": group.model_dump(mode="json"),
            "jobs": [j.model_dump(mode="json") for j in members],
        }

    return await guarded(body())


def _check_error(exc: Exception) -> Exception:
    """A value-free HTTP error for a check that could not run (fixed details, codes only)."""
    if isinstance(exc, ConsistencyError):
        return DpsHttpError(exc.status, exc.code, "the consistency check cannot run for this group")
    if isinstance(exc, SecretUnavailable):
        return DpsHttpError(503, "secret_unavailable", "a member target could not be read")
    if isinstance(exc, ConnectorError):
        status = 503 if exc.code in _UNAVAILABLE else 502
        return DpsHttpError(status, exc.code, "a member target could not be read")
    if isinstance(exc, TimeoutError):
        return DpsHttpError(504, "check_timeout", "the consistency check timed out")
    return exc


async def _consistency(
    group_id: str, body: ConsistencyCheckRequest, request: Request
) -> ConsistencyCheckResult:
    tenant = tenant_of(request)
    settings = request.app.state.settings
    group = await get_group(tenant, group_id)
    if group is None:
        raise DpsHttpError(404, "group_not_found", "job group not found")
    members, _ = await jobs.list_jobs(tenant, kind=JobKind.run, group_id=group_id, limit=200)
    snapshots: dict[str, ClassificationSnapshot] = {}
    for sid in {j.snapshot_id for j in members if j.snapshot_id}:
        snapshot = await get_snapshot(tenant, sid)
        if snapshot is not None:
            snapshots[sid] = snapshot
    conn_ids = {
        (j.spec.get("target") or {}).get("connection_id")
        or (j.spec.get("source") or {}).get("connection_id")
        for j in members
    }
    connections: dict[str, Connection] = {}
    for cid in sorted(c for c in conn_ids if c):
        conn = await get_connection(tenant, cid)
        if conn is not None:
            connections[cid] = conn
    result = await asyncio.wait_for(
        run_consistency_check(
            group=group,
            jobs=members,
            snapshots=snapshots,
            connections=connections,
            request=body,
            settings=settings,
            kek=kek_of(request),
        ),
        timeout=settings.connector_statement_timeout_seconds,
    )
    await save_last_check(tenant, group_id, result.model_dump(mode="json"))
    return result


@router.post(
    "/job-groups/{group_id}/consistency-check",
    response_model=ConsistencyCheckResult,
    dependencies=_JOBS,
)
async def consistency_check(
    group_id: str, body: ConsistencyCheckRequest, request: Request
) -> ConsistencyCheckResult:
    """An n-way join over the members' masked targets; counts only (contract §6.5).

    The result is stored on the group (``last_check``) and audited as
    ``group.consistency_check`` with outcome ``passed``/``failed`` and counts.
    """
    try:
        result = await _consistency(group_id, body, request)
    except Exception as exc:  # noqa: BLE001 - mapped to a value-free {"detail", "code"}
        raise rejected(request, _CHECK_ACTION, _check_error(exc)) from None
    audit_action(
        request,
        action=_CHECK_ACTION,
        outcome="passed" if result.passed else "failed",
        counts={
            "members": len(result.members),
            "pairs": result.total_pairs,
            "consistent_pairs": result.consistent_pairs,
            "sampled": result.sampled,
        },
    )
    return result
