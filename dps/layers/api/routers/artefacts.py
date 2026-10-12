"""``GET /api/v1/artefacts/{id}`` (contract §9, §13): stream a stored artefact.

Scope ``jobs`` or ``scan`` on the route, then the owning job's kind (403 ``scope_required``). The
download is ``attachment; filename="<artefact id>.<ext>"`` — never a value or a label — with
``Cache-Control: no-store``. The first chunk is read before the answer starts, so a missing
object is a clean 404 instead of a broken stream. Reads are audited (``artefact.read``).
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from dps.auth import require_any_scope
from dps.jobs import JobKind
from dps.layers.api.deps import audited, principal_of, require_db, tenant_of
from dps.layers.api.errors import DpsHttpError
from dps.layers.api.routers.evidence import artefacts_of, job_kinds, require_kind_scope
from dps.ratelimit import enforce_rate_limit
from dps.storage import ARTEFACT_EXT, Artefact

router = APIRouter(prefix="/api/v1", tags=["artefacts"])
_READ = [
    Depends(require_any_scope("jobs", "scan")),
    Depends(enforce_rate_limit),
    Depends(require_db),
]


@router.get("/artefacts/{artefact_id}", dependencies=_READ, response_model=None)
async def download(artefact_id: str, request: Request) -> StreamingResponse:
    """Stream the artefact's bytes."""

    async def open_() -> tuple[Artefact, bytes, AsyncIterator[bytes]]:
        tenant = tenant_of(request)
        service = artefacts_of(request)
        row = await service.get_row(tenant, artefact_id)
        if row is None:
            raise DpsHttpError(404, "artefact_not_found", "artefact not found")
        if row.job_id is not None:
            kind = (await job_kinds(tenant, [row.job_id])).get(row.job_id, JobKind.run.value)
            require_kind_scope(principal_of(request), kind)
        _, chunks = await service.open(tenant, artefact_id)
        first = await anext(chunks, b"")
        return row, first, chunks

    row, first, chunks = await audited(request, "artefact.read", open_(), job_id=None)

    async def body() -> AsyncIterator[bytes]:
        if first:
            yield first
        async for chunk in chunks:
            yield chunk

    filename = f"{row.id}.{ARTEFACT_EXT.get(row.kind, 'bin')}"
    return StreamingResponse(
        body(),
        media_type=row.content_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
            "Content-Length": str(row.bytes),
        },
    )
