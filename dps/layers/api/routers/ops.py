"""Liveness, readiness and metrics."""

from __future__ import annotations

from fastapi import APIRouter, Response
from fastapi.responses import JSONResponse

from dps.observability import READINESS, metrics_payload

router = APIRouter()


@router.get("/health", include_in_schema=False)
async def health() -> dict[str, str]:
    """Liveness: the process answers — on the event loop itself, so it never waits for a worker
    thread the engine routes may all be holding (S3 review round 3)."""
    return {"status": "ok"}


@router.get("/readyz", include_in_schema=False)
def readyz() -> JSONResponse:
    """Readiness: 200 when every required component is up, else 503, with per-component detail."""
    snap = READINESS.snapshot()
    return JSONResponse(snap, status_code=200 if snap["ready"] else 503)


@router.get("/metrics", include_in_schema=False)
def metrics() -> Response:
    """Prometheus exposition."""
    body, content_type = metrics_payload()
    return Response(content=body, media_type=content_type)
