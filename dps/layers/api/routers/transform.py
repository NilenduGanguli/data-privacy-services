"""POST /api/v1/obfuscate, /synthesize, /reveal."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from dps.auth import require_scope
from dps.engine.service import PrivacyEngine, RevealResponse, TransformResponse
from dps.layers.api.common import payload_from, policy_label, run_engine, with_idempotency
from dps.layers.api.ndjson import (
    OPENAPI_NDJSON,
    NdjsonEnvelope,
    add_ndjson_route,
    stream_lines,
    stream_reveal_array,
    streams_array,
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

    A ``data`` array with ``Accept: application/x-ndjson`` streams one line per element; an
    ``Idempotency-Key`` makes a retry a replay — byte-identical only with caller key material.
    """
    engine: PrivacyEngine = request.app.state.engine
    policy = policy_label(body.policy, engine.settings.default_policy)

    async def run() -> TransformOut | StreamingResponse:
        if streams_array(body.data, body.text, request):
            envelope = NdjsonEnvelope(policy=body.policy, options=body.options)
            return await stream_lines(request, mode="obfuscate", envelope=envelope, items=body.data)

        async def call() -> TransformResponse:
            payload = payload_from(request, body.data, body.text)
            extra = await engine.label_leftovers(payload, body.policy, body.options)  # T3 (opt-in)
            return await run_in_threadpool(
                engine.obfuscate, payload, body.policy, body.options, extra_findings=extra
            )

        result, tid = await run_engine(request, mode="obfuscate", policy_name=policy, call=call)
        return TransformOut(**result.model_dump(), trace_id=tid)

    return await with_idempotency(
        request, await request.body(), run, mode="obfuscate", policy=policy
    )


@router.post(
    "/synthesize",
    response_model=TransformOut,
    dependencies=[Depends(require_scope("synthesize")), Depends(enforce_rate_limit)],
    openapi_extra=OPENAPI_NDJSON,
)
async def synthesize(body: EngineRequest, request: Request) -> TransformOut | StreamingResponse:
    """Replace sensitive values with consistent synthetic surrogates.

    A ``data`` array with ``Accept: application/x-ndjson`` streams one line per element; an
    ``Idempotency-Key`` makes a retry a replay — identical only with a caller seed (and no LLM
    rewrite, which is not deterministic).
    """
    engine: PrivacyEngine = request.app.state.engine
    policy = policy_label(body.policy, engine.settings.default_policy)

    async def run() -> TransformOut | StreamingResponse:
        if streams_array(body.data, body.text, request):
            envelope = NdjsonEnvelope(policy=body.policy, options=body.options)
            return await stream_lines(
                request, mode="synthesize", envelope=envelope, items=body.data
            )

        async def call() -> TransformResponse:
            payload = payload_from(request, body.data, body.text)
            extra = await engine.label_leftovers(payload, body.policy, body.options)  # T3 (opt-in)
            return await engine.synthesize(payload, body.policy, body.options, extra_findings=extra)

        result, tid = await run_engine(request, mode="synthesize", policy_name=policy, call=call)
        return TransformOut(**result.model_dump(), trace_id=tid)

    return await with_idempotency(
        request, await request.body(), run, mode="synthesize", policy=policy
    )


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

    async def run() -> RevealOut | StreamingResponse:
        if streams_array(body.data, body.text, request):
            return await stream_reveal_array(
                request,
                data=body.data,
                key_material=body.key_material,
                reveal_hints=body.reveal_hints,
            )

        async def call() -> RevealResponse:
            payload = payload_from(request, body.data, body.text)
            return await run_in_threadpool(
                engine.reveal, payload, body.key_material, body.reveal_hints
            )

        result, tid = await run_engine(request, mode="reveal", policy_name="-", call=call)
        return RevealOut(**result.model_dump(), trace_id=tid)

    return await with_idempotency(request, await request.body(), run, mode="reveal")
