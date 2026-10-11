"""POST /api/v1/detect — findings only."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from starlette.concurrency import run_in_threadpool

from dps.auth import require_scope
from dps.engine.service import DetectResponse, PrivacyEngine
from dps.layers.api.common import payload_from, policy_label, run_engine
from dps.layers.api.schemas import DetectOut, EngineRequest
from dps.ratelimit import enforce_rate_limit

router = APIRouter(prefix="/api/v1", tags=["engine"])


@router.post(
    "/detect",
    response_model=DetectOut,
    dependencies=[Depends(require_scope("detect")), Depends(enforce_rate_limit)],
)
async def detect(body: EngineRequest, request: Request) -> DetectOut:
    """Findings (types, locators, scores — never values) and the record's sensitivity."""
    engine: PrivacyEngine = request.app.state.engine

    async def call() -> DetectResponse:
        payload = payload_from(request, body.data, body.text)
        # CPU-bound and synchronous: off the event loop so probes stay responsive.
        return await run_in_threadpool(engine.detect, payload, body.policy, body.options)

    result, tid = await run_engine(
        request,
        mode="detect",
        policy=policy_label(body.policy, engine.settings.default_policy),
        call=call,
    )
    return DetectOut(**result.model_dump(), trace_id=tid)
