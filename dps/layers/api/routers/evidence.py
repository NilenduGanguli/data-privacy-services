"""``/api/v1/evidence`` (contract §7, §9): evidence packs of run (and, from M4, scan) jobs.

Scope ``jobs`` or ``scan`` on the route; then the owning job's kind decides: scan packs need
``scan``, run/discover packs ``jobs`` (403 ``scope_required``). ``GET /evidence/{id}`` answers the
pack, its sha256 over the canonical JSON and its artefacts; ``/html`` is the attested printable page
(the stored ``evidence_html`` artefact) under ``Content-Security-Policy: default-src 'none';
style-src 'unsafe-inline'``. Packs are value-free by construction.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from dps import db
from dps.auth import Principal, require_any_scope
from dps.jobs import JobKind
from dps.layers.api.deps import guarded, page_args, principal_of, require_db, tenant_of
from dps.layers.api.errors import DpsHttpError
from dps.layers.connectors.evidence import EvidenceRecord, get_evidence, list_evidence, render_html
from dps.ratelimit import enforce_rate_limit
from dps.storage import ArtefactService

router = APIRouter(prefix="/api/v1", tags=["evidence"])
_READ = [
    Depends(require_any_scope("jobs", "scan")),
    Depends(enforce_rate_limit),
    Depends(require_db),
]
#: The printable page carries its own inline styles and nothing else.
EVIDENCE_CSP = "default-src 'none'; style-src 'unsafe-inline'"


def scope_for_kind(kind: str) -> str:
    """The scope that reads a job's evidence and artefacts (§8.1)."""
    return "scan" if kind == JobKind.scan.value else "jobs"


def require_kind_scope(principal: Principal, kind: str) -> None:
    """403 ``scope_required`` unless the principal holds the scope of the owning job's kind."""
    scope = scope_for_kind(kind)
    if not principal.has_scope(scope):
        raise DpsHttpError(403, "scope_required", f"scope {scope!r} required for this job's output")


async def job_kinds(tenant_id: str, job_ids: list[str]) -> dict[str, str]:
    """``{job_id: kind}`` for the tenant's jobs among ``job_ids`` (RLS-scoped)."""
    if not job_ids:
        return {}
    async with db.acquire(tenant_id) as conn:
        rows = await conn.fetch(
            f"SELECT id, kind FROM {db.schema_ident()}.dps_job "
            "WHERE tenant_id = $1 AND id = ANY($2::text[])",
            tenant_id,
            job_ids,
        )
    return {str(r["id"]): str(r["kind"]) for r in rows}


def artefacts_of(request: Request) -> ArtefactService:
    """The API's artefact service (``db.acquire``; built at startup)."""
    service = getattr(request.app.state, "artefacts", None)
    if not isinstance(service, ArtefactService):
        raise DpsHttpError(503, "db_unavailable", "the metadata database is unavailable")
    return service


async def _record(request: Request, evidence_id: str) -> EvidenceRecord:
    tenant = tenant_of(request)
    record = await get_evidence(tenant, evidence_id)
    if record is None:
        raise DpsHttpError(404, "evidence_not_found", "evidence pack not found")
    kind = (await job_kinds(tenant, [record.job_id])).get(record.job_id, JobKind.run.value)
    require_kind_scope(principal_of(request), kind)
    return record


@router.get("/evidence", dependencies=_READ)
async def list_(
    request: Request, job_id: str | None = None, limit: int = 50, cursor: str | None = None
) -> dict[str, Any]:
    """Evidence packs, newest first (Reports, mockup 09); only the kinds the key may read."""

    async def body() -> dict[str, Any]:
        tenant, principal = tenant_of(request), principal_of(request)
        items, nxt = await list_evidence(tenant, job_id=job_id, **page_args(limit, cursor))
        if not (principal.has_scope("jobs") and principal.has_scope("scan")):
            kinds = await job_kinds(tenant, sorted({i.job_id for i in items}))
            items = [
                i
                for i in items
                if principal.has_scope(scope_for_kind(kinds.get(i.job_id, JobKind.run.value)))
            ]
        return {"items": [i.model_dump(mode="json") for i in items], "next_cursor": nxt}

    return await guarded(body())


@router.get("/evidence/{evidence_id}", dependencies=_READ)
async def get(evidence_id: str, request: Request) -> dict[str, Any]:
    """The pack, ``pack_sha256`` (sha256 of the canonical JSON) and its artefacts."""

    async def body() -> dict[str, Any]:
        record = await _record(request, evidence_id)
        service, tenant = artefacts_of(request), tenant_of(request)
        artefacts = []
        for artefact_id in (
            record.json_artefact_id,
            record.html_artefact_id,
            record.pdf_artefact_id,
        ):
            row = await service.get_row(tenant, artefact_id) if artefact_id else None
            if row is not None:
                artefacts.append(
                    {
                        "id": row.id,
                        "kind": row.kind,
                        "bytes": row.bytes,
                        "content_type": row.content_type,
                    }
                )
        return {
            "id": record.id,
            "job_id": record.job_id,
            "created_at": record.created_at.isoformat(),
            "pack": record.pack.model_dump(mode="json"),
            "pack_sha256": record.pack_sha256,
            "artefacts": artefacts,
        }

    return await guarded(body())


@router.get("/evidence/{evidence_id}/html", dependencies=_READ, response_class=HTMLResponse)
async def html(evidence_id: str, request: Request) -> HTMLResponse:
    """The printable pack (the stored rendering; re-rendered from the pack if it is missing)."""

    async def body() -> str:
        record = await _record(request, evidence_id)
        if record.html_artefact_id:
            service = artefacts_of(request)
            row = await service.get_row(tenant_of(request), record.html_artefact_id)
            if row is not None:
                return (await service.store.get(row.object_key)).decode("utf-8")
        return render_html(record.pack, sha=record.pack_sha256)

    page = await guarded(body())
    return HTMLResponse(
        page, headers={"Content-Security-Policy": EVIDENCE_CSP, "Cache-Control": "no-store"}
    )
