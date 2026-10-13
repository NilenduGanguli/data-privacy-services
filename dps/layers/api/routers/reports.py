"""``/api/v1/reports`` (contract §9): the Overview and Reports & audit pages' data (M5).

Value-free daily roll-ups (``dps.reports``), refreshed for the requested window first. Scopes:
``detect`` for the summary, sweeps and API roll-ups; ``jobs`` for the evidence list (scan packs only
for keys that also hold ``scan``). Every answer is the caller's tenant only (RLS).

PDFs (M5 Task 14): ``GET /evidence/{id}/pdf`` (``evidence_pdf_router``; the scope rule of
``GET /evidence/{id}``) renders the pack once, stores it as an ``evidence_pdf`` artefact, links it
from ``dps_evidence.pdf_artefact_id`` and streams the stored bytes afterwards; ``format=pdf`` on an
API roll-up renders a one-page PDF. ``DPS_REPORT_PDF_ENABLED=false`` answers 404 ``pdf_disabled``.
"""

from __future__ import annotations

import asyncio
import json
from datetime import date
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response

from dps import db, reports
from dps.auth import require_any_scope, require_scope
from dps.config import Settings
from dps.jobs import JobKind
from dps.layers.api.deps import audit_action, guarded, principal_of, require_db, tenant_of
from dps.layers.api.errors import DpsHttpError
from dps.layers.api.routers.evidence import artefacts_of, job_kinds, require_kind_scope
from dps.layers.connectors.evidence import EvidenceRecord, get_evidence
from dps.ratelimit import enforce_rate_limit
from dps.storage import ArtefactService

router = APIRouter(prefix="/api/v1/reports", tags=["reports"])
_DETECT = [Depends(require_scope("detect")), Depends(enforce_rate_limit), Depends(require_db)]
_JOBS = [Depends(require_scope("jobs")), Depends(enforce_rate_limit), Depends(require_db)]
Days = Annotated[int, Query(ge=1, le=90)]
#: ``action`` of an API roll-up: an audit action name (``obfuscate``, ``document.detect``, …).
ACTION_PATTERN = r"^[a-z][a-z0-9_.:-]{0,63}$"


def _settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


@router.get("/summary", dependencies=_DETECT)
async def summary(request: Request, days: Days = 14) -> dict[str, Any]:
    """Overview tiles, trend, findings by type, layers, posture and recent jobs."""
    out = await guarded(reports.summary(tenant_of(request), days, _settings(request)))
    return out.model_dump(mode="json")


@router.get("/jobs", dependencies=_DETECT)
async def sweeps(request: Request, days: Days = 14) -> dict[str, Any]:
    """Leakage sweeps per day and layer, totals and the sweeps that failed closed."""
    out = await guarded(reports.jobs_report(tenant_of(request), days, _settings(request)))
    return out.model_dump(mode="json")


@router.get("/evidence", dependencies=_JOBS)
async def evidence(
    request: Request,
    days: Days = 30,
    layer: Literal["conn", "ui"] | None = None,
    outcome: Literal["ok", "failed"] | None = None,
    q: Annotated[str | None, Query(max_length=128)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
) -> dict[str, Any]:
    """Evidence packs of the window with value-free summaries (newest first)."""
    tenant = tenant_of(request)
    await guarded(reports.ensure_fresh(tenant, *reports.span(days), settings=_settings(request)))
    items, nxt = await guarded(
        reports.evidence_list(
            tenant,
            days=days,
            layer=layer,
            outcome=outcome,
            q=q,
            limit=limit,
            cursor=cursor,
            include_scans=principal_of(request).has_scope("scan"),
        )
    )
    return {"items": [i.model_dump(mode="json") for i in items], "next_cursor": nxt}


@router.get("/api-rollups", dependencies=_DETECT)
async def api_rollups(request: Request, days: Days = 14) -> dict[str, Any]:
    """Layer 1 calls per day and action."""
    tenant = tenant_of(request)
    await guarded(reports.ensure_fresh(tenant, *reports.span(days), settings=_settings(request)))
    items = await guarded(reports.api_rollups(tenant, days))
    return {"items": [i.model_dump(mode="json") for i in items]}


async def rollup_document(request: Request, day: date, action: str) -> dict[str, Any]:
    """The roll-up document of one day × action (404 ``rollup_not_found``)."""
    tenant = tenant_of(request)
    await guarded(reports.ensure_fresh(tenant, day, day, settings=_settings(request)))
    try:
        return await reports.api_rollup_doc(tenant, day, action)
    except reports.RollupNotFound:
        raise DpsHttpError(
            404, "rollup_not_found", "no API roll-up for that day and action"
        ) from None


def _pdf_enabled(request: Request) -> None:
    if not _settings(request).report_pdf_enabled:
        raise DpsHttpError(404, "pdf_disabled", "PDF reports are disabled (DPS_REPORT_PDF_ENABLED)")


@router.get("/api-rollups/{day}", dependencies=_DETECT)
async def api_rollup(
    request: Request,
    day: date,
    action: Annotated[str, Query(pattern=ACTION_PATTERN)],
    format: Literal["json", "pdf"] = "json",  # noqa: A002 - the contract's query parameter
) -> Response:
    """Download one API roll-up document (JSON, or a one-page PDF)."""
    if format == "pdf":
        _pdf_enabled(request)
    doc = await rollup_document(request, day, action)
    name = f"api-rollup-{day.isoformat()}-{action}"
    if format == "pdf":
        from dps.layers.reports.pdf import render_rollup_pdf

        body = await asyncio.to_thread(render_rollup_pdf, doc)
        return Response(
            body,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'attachment; filename="{name}.pdf"',
                "Cache-Control": "no-store",
            },
        )
    return Response(
        json.dumps(doc, indent=2),
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="{name}.json"',
            "Cache-Control": "no-store",
        },
    )


# --- evidence PDF (contract §9: "M5 adds GET /evidence/{id}/pdf") ---------------------------------
evidence_pdf_router = APIRouter(prefix="/api/v1", tags=["evidence"])
_EVIDENCE = [
    Depends(require_any_scope("jobs", "scan")),
    Depends(enforce_rate_limit),
    Depends(require_db),
]


async def _stored_pdf(service: ArtefactService, tenant: str, artefact_id: str) -> bytes | None:
    row = await service.get_row(tenant, artefact_id)
    return await service.store.get(row.object_key) if row is not None else None


async def _render_and_store(request: Request, record: EvidenceRecord) -> bytes:
    """Render the pack once, store it as an ``evidence_pdf`` artefact and link it; a concurrent
    first request that linked its own copy first wins and ours is removed."""
    from dps.layers.reports.pdf import render_evidence_pdf

    tenant, service = tenant_of(request), artefacts_of(request)
    pack = record.pack.model_dump(mode="json")
    body = await asyncio.to_thread(render_evidence_pdf, pack, pack_sha256=record.pack_sha256)
    artefact_id = await service.save(
        tenant_id=tenant,
        job_id=record.job_id,
        kind="evidence_pdf",
        data=body,
        content_type="application/pdf",
        meta={"evidence_id": record.id, "pack_sha256": record.pack_sha256},
    )
    s = db.schema_ident()
    async with db.acquire(tenant) as conn:
        linked = await conn.fetchval(
            f"UPDATE {s}.dps_evidence SET pdf_artefact_id = $2 "
            "WHERE id = $1 AND pdf_artefact_id IS NULL RETURNING id",
            record.id,
            artefact_id,
        )
    if linked is None:  # another request linked its rendering first: serve that one
        row = await service.get_row(tenant, artefact_id)
        if row is not None:
            await service.store.delete(row.object_key)
            async with db.acquire(tenant) as conn:
                await conn.execute(f"DELETE FROM {s}.dps_artefact WHERE id = $1", artefact_id)
        current = await get_evidence(tenant, record.id)
        if current is not None and current.pdf_artefact_id:
            stored = await _stored_pdf(service, tenant, current.pdf_artefact_id)
            if stored is not None:
                return stored
        return body
    audit_action(
        request,
        action="evidence.pdf",
        status=200,
        job_id=record.job_id,
        counts={"bytes": len(body)},
    )
    return body


@evidence_pdf_router.get("/evidence/{evidence_id}/pdf", dependencies=_EVIDENCE)
async def evidence_pdf(evidence_id: str, request: Request) -> Response:
    """The printable pack as PDF: rendered on first request, then streamed from its artefact."""
    _pdf_enabled(request)

    async def body() -> bytes:
        tenant = tenant_of(request)
        record = await get_evidence(tenant, evidence_id)
        if record is None:
            raise DpsHttpError(404, "evidence_not_found", "evidence pack not found")
        kind = (await job_kinds(tenant, [record.job_id])).get(record.job_id, JobKind.run.value)
        require_kind_scope(principal_of(request), kind)
        if record.pdf_artefact_id:
            stored = await _stored_pdf(artefacts_of(request), tenant, record.pdf_artefact_id)
            if stored is not None:
                return stored
        return await _render_and_store(request, record)

    pdf = await guarded(body())
    return Response(
        pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="evidence-{evidence_id}.pdf"',
            "Cache-Control": "no-store",
        },
    )
