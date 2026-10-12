"""``/api/v1/snapshots`` (contract §9, scope ``jobs``): classification review.

Overrides are accepted only while a snapshot is ``proposed`` (409 ``snapshot_not_proposed``);
accepting supersedes the accepted snapshot of the same connection and object and records the
principal's name. Snapshots hold value shapes only, never values.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from dps.auth import require_scope
from dps.jobs_models import ClassificationSnapshot, SnapshotOverride
from dps.layers.api.deps import audited, guarded, page_args, principal_of, require_db, tenant_of
from dps.layers.api.errors import DpsHttpError
from dps.ratelimit import enforce_rate_limit
from dps.snapshots import accept_snapshot, get_snapshot, list_snapshots, set_overrides

router = APIRouter(prefix="/api/v1", tags=["snapshots"])
_JOBS = [Depends(require_scope("jobs")), Depends(enforce_rate_limit), Depends(require_db)]


class SnapshotPatch(BaseModel):
    """Body of ``PATCH /snapshots/{id}``: overrides merge per path with earlier ones."""

    model_config = ConfigDict(hide_input_in_errors=True)
    overrides: list[SnapshotOverride] = Field(min_length=1, max_length=2000)


def _dump(snapshot: ClassificationSnapshot) -> dict[str, Any]:
    return snapshot.model_dump(mode="json", exclude_unset=False)


@router.get("/snapshots", dependencies=_JOBS)
async def list_(
    request: Request,
    connection_id: str | None = None,
    object: str | None = None,
    status: Literal["proposed", "accepted", "superseded"] | None = None,
    limit: int = 50,
    cursor: str | None = None,
) -> dict[str, Any]:
    """The tenant's snapshots, newest first (filters: connection, object ``OWNER.NAME``, status)."""

    async def body() -> dict[str, Any]:
        items, nxt = await list_snapshots(
            tenant_of(request),
            connection_id=connection_id,
            object_ref=object,
            status=status,
            **page_args(limit, cursor),
        )
        return {"items": [_dump(s) for s in items], "next_cursor": nxt}

    return await guarded(body())


@router.get("/snapshots/{snapshot_id}", dependencies=_JOBS)
async def get(snapshot_id: str, request: Request) -> dict[str, Any]:
    """One snapshot: shape, per-column classification, suggested and chosen methods."""

    async def body() -> dict[str, Any]:
        snapshot = await get_snapshot(tenant_of(request), snapshot_id)
        if snapshot is None:
            raise DpsHttpError(404, "snapshot_not_found", "no such snapshot")
        return _dump(snapshot)

    return await guarded(body())


@router.patch("/snapshots/{snapshot_id}", dependencies=_JOBS)
async def patch(snapshot_id: str, body: SnapshotPatch, request: Request) -> dict[str, Any]:
    """Apply review overrides to a proposed snapshot."""

    async def run() -> dict[str, Any]:
        updated = await set_overrides(
            tenant_of(request),
            snapshot_id,
            body.overrides,
            fpe_enabled=request.app.state.engine.fpe_enabled,
        )
        return _dump(updated)

    return await audited(
        request, "snapshot.update", run(), counts={"overrides": len(body.overrides)}
    )


@router.post("/snapshots/{snapshot_id}/accept", dependencies=_JOBS)
async def accept(snapshot_id: str, request: Request) -> dict[str, Any]:
    """Accept a proposed snapshot (``accepted_by`` = the key's name)."""

    async def run() -> dict[str, Any]:
        accepted = await accept_snapshot(
            tenant_of(request), snapshot_id, accepted_by=principal_of(request).name
        )
        return _dump(accepted)

    return await audited(request, "snapshot.accept", run())
