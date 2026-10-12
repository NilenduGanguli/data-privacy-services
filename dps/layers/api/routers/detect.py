"""POST /api/v1/detect — findings only."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from dps.auth import require_scope
from dps.engine.service import DetectResponse, PrivacyEngine
from dps.layers.api.common import payload_from, policy_label, run_engine, with_idempotency
from dps.layers.api.ndjson import (
    OPENAPI_NDJSON,
    NdjsonEnvelope,
    add_ndjson_route,
    stream_lines,
    streams_array,
)
from dps.layers.api.schemas import DetectOut, EngineRequest
from dps.ratelimit import enforce_rate_limit

router = APIRouter(prefix="/api/v1", tags=["engine"])
# The NDJSON-body twin is registered first: it matches only Content-Type application/x-ndjson.
add_ndjson_route(router, "/detect", mode="detect", scope="detect")


@router.post(
    "/detect",
    response_model=DetectOut,
    dependencies=[Depends(require_scope("detect")), Depends(enforce_rate_limit)],
    openapi_extra=OPENAPI_NDJSON,
)
async def detect(body: EngineRequest, request: Request) -> DetectOut | StreamingResponse:
    """Findings (types, locators, scores — never values) and the record's sensitivity.

    A ``data`` array with ``Accept: application/x-ndjson`` streams one line per element; an
    ``Idempotency-Key`` makes a retry a replay (contract §9).
    """
    engine: PrivacyEngine = request.app.state.engine
    policy = policy_label(body.policy, engine.settings.default_policy)

    async def run() -> DetectOut | StreamingResponse:
        if streams_array(body.data, body.text, request):
            envelope = NdjsonEnvelope(policy=body.policy, options=body.options)
            return await stream_lines(request, mode="detect", envelope=envelope, items=body.data)

        async def call() -> DetectResponse:
            payload = payload_from(request, body.data, body.text)
            extra = await engine.label_leftovers(payload, body.policy, body.options)  # T3 (opt-in)
            # CPU-bound and synchronous: off the event loop so probes stay responsive.
            return await run_in_threadpool(
                engine.detect, payload, body.policy, body.options, extra_findings=extra
            )

        result, tid = await run_engine(request, mode="detect", policy_name=policy, call=call)
        return DetectOut(**result.model_dump(), trace_id=tid)

    return await with_idempotency(request, await request.body(), run, mode="detect", policy=policy)
