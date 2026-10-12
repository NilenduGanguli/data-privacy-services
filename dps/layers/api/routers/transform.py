"""POST /api/v1/obfuscate, /synthesize, /reveal."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from dps.auth import require_scope
from dps.engine.service import PrivacyEngine, RevealResponse, TransformResponse
from dps.layers.api.common import payload_from, policy_label, run_engine
from dps.layers.api.ndjson import (
    OPENAPI_NDJSON,
    NdjsonEnvelope,
    add_ndjson_route,
    stream_lines,
    stream_reveal_array,
    wants_ndjson,
)
from dps.layers.api.schemas import EngineRequest, RevealOut, RevealRequest, TransformOut
from dps.ratelimit import enforce_rate_limit

router = APIRouter(prefix="/api/v1", tags=["engine"])
# NDJSON-body twins first: each matches only Content-Type application/x-ndjson.
add_ndjson_route(router, "/obfuscate", mode="obfuscate", scope="obfuscate")
add_ndjson_route(router, "/synthesize", mode="synthesize", scope="synthesize")
add_ndjson_route(router, "/reveal", mode="reveal", scope="reveal")


@router.post(
    "/obfuscate",
    response_model=TransformOut,
    dependencies=[Depends(require_scope("obfuscate")), Depends(enforce_rate_limit)],
    openapi_extra=OPENAPI_NDJSON,
)
async def obfuscate(body: EngineRequest, request: Request) -> TransformOut | StreamingResponse:
    """Obfuscate under a per-request (or caller-held) key; returns key material and hints.

    A ``data`` array with ``Accept: application/x-ndjson`` streams one line per element.
    """
    engine: PrivacyEngine = request.app.state.engine
    if wants_ndjson(request) and isinstance(body.data, list) and body.text is None:
        envelope = NdjsonEnvelope(policy=body.policy, options=body.options)
        return await stream_lines(request, mode="obfuscate", envelope=envelope, items=body.data)

    async def call() -> TransformResponse:
        payload = payload_from(request, body.data, body.text)
        return await run_in_threadpool(engine.obfuscate, payload, body.policy, body.options)

    result, tid = await run_engine(
        request,
        mode="obfuscate",
        policy_name=policy_label(body.policy, engine.settings.default_policy),
        call=call,
    )
    return TransformOut(**result.model_dump(), trace_id=tid)


@router.post(
    "/synthesize",
    response_model=TransformOut,
    dependencies=[Depends(require_scope("synthesize")), Depends(enforce_rate_limit)],
    openapi_extra=OPENAPI_NDJSON,
)
async def synthesize(body: EngineRequest, request: Request) -> TransformOut | StreamingResponse:
    """Replace sensitive values with consistent synthetic surrogates.

    A ``data`` array with ``Accept: application/x-ndjson`` streams one line per element.
    """
    engine: PrivacyEngine = request.app.state.engine
    if wants_ndjson(request) and isinstance(body.data, list) and body.text is None:
        envelope = NdjsonEnvelope(policy=body.policy, options=body.options)
        return await stream_lines(request, mode="synthesize", envelope=envelope, items=body.data)

    async def call() -> TransformResponse:
        payload = payload_from(request, body.data, body.text)
        return await engine.synthesize(payload, body.policy, body.options)

    result, tid = await run_engine(
        request,
        mode="synthesize",
        policy_name=policy_label(body.policy, engine.settings.default_policy),
        call=call,
    )
    return TransformOut(**result.model_dump(), trace_id=tid)


@router.post(
    "/reveal",
    response_model=RevealOut,
    dependencies=[Depends(require_scope("reveal")), Depends(enforce_rate_limit)],
    openapi_extra=OPENAPI_NDJSON,
)
async def reveal(body: RevealRequest, request: Request) -> RevealOut | StreamingResponse:
    """Undo an obfuscation with the caller's key material (a separate scope).

    A ``data`` array with ``Accept: application/x-ndjson`` is revealed as one document (its hints
    address the whole array) and streamed one line per element.
    """
    engine: PrivacyEngine = request.app.state.engine
    if wants_ndjson(request) and isinstance(body.data, list) and body.text is None:
        return await stream_reveal_array(
            request,
            data=body.data,
            key_material=body.key_material,
            reveal_hints=body.reveal_hints,
        )

    async def call() -> RevealResponse:
        payload = payload_from(request, body.data, body.text)
        return await run_in_threadpool(engine.reveal, payload, body.key_material, body.reveal_hints)

    result, tid = await run_engine(request, mode="reveal", policy_name="-", call=call)
    return RevealOut(**result.model_dump(), trace_id=tid)
