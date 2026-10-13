"""``/api/v1/reports`` (contract §9): the Overview and Reports & audit pages' data (M5).

Value-free daily roll-ups (``dps.reports``), refreshed for the requested window first. Scopes:
``detect`` for the summary, sweeps and API roll-ups; ``jobs`` for the evidence list (scan packs only
for keys that also hold ``scan``). Every answer is the caller's tenant only (RLS).
"""

from __future__ import annotations

import json
from datetime import date
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response

from dps import reports
from dps.auth import require_scope
from dps.config import Settings
from dps.layers.api.deps import guarded, principal_of, require_db, tenant_of
from dps.layers.api.errors import DpsHttpError
from dps.ratelimit import enforce_rate_limit

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


@router.get("/api-rollups/{day}", dependencies=_DETECT)
async def api_rollup(
    request: Request,
    day: date,
    action: Annotated[str, Query(pattern=ACTION_PATTERN)],
    format: Literal["json"] = "json",  # noqa: A002 - the contract's query parameter (pdf: Task 14)
) -> Response:
    """Download one API roll-up document."""
    doc = await rollup_document(request, day, action)
    return Response(
        json.dumps(doc, indent=2),
        media_type="application/json",
        headers={
            "Content-Disposition": (
                f'attachment; filename="api-rollup-{day.isoformat()}-{action}.json"'
            ),
            "Cache-Control": "no-store",
        },
    )
