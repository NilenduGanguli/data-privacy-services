"""``/api/v1/job-groups`` (contract §9, scope ``jobs``).

A job group shares one key and one synthesis seed across runs (cross-table and cross-system
consistency, §6.5). Both are generated at create and returned **once** (``Cache-Control:
no-store``); DPS stores only the key id and the seed's fingerprint.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from dps import jobs
from dps.auth import require_scope
from dps.groups import GROUP_NAME_RE, create_group, get_group, list_groups
from dps.layers.api.deps import (
    audited,
    guarded,
    page_args,
    principal_of,
    require_db,
    tenant_of,
)
from dps.layers.api.errors import DpsHttpError
from dps.ratelimit import enforce_rate_limit

router = APIRouter(prefix="/api/v1", tags=["job-groups"])
_JOBS = [Depends(require_scope("jobs")), Depends(enforce_rate_limit), Depends(require_db)]


class GroupRequest(BaseModel):
    """Body of ``POST /job-groups``."""

    model_config = ConfigDict(hide_input_in_errors=True)
    name: str = Field(pattern=GROUP_NAME_RE.pattern)


@router.post("/job-groups", status_code=201, dependencies=_JOBS)
async def create(body: GroupRequest, request: Request, response: Response) -> dict[str, Any]:
    """Create a group; the key material and seed are in this answer and nowhere else."""

    async def run() -> dict[str, Any]:
        group, key, seed = await create_group(
            tenant_of(request), body.name, created_by=principal_of(request).key_id
        )
        return {
            "group": group.model_dump(mode="json"),
            "key_material": key.model_dump(mode="json"),
            "synthesis_seed": seed.model_dump(mode="json"),
        }

    out = await audited(request, "group.create", run(), status=201)
    response.headers["Cache-Control"] = "no-store"
    return out


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
