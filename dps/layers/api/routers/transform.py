"""POST /api/v1/obfuscate, /synthesize, /reveal."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from starlette.concurrency import run_in_threadpool

from dps.auth import require_scope
from dps.engine.service import PrivacyEngine, RevealResponse, TransformResponse
from dps.layers.api.common import payload_from, policy_label, run_engine
from dps.layers.api.schemas import EngineRequest, RevealOut, RevealRequest, TransformOut
from dps.ratelimit import enforce_rate_limit

router = APIRouter(prefix="/api/v1", tags=["engine"])


@router.post(
    "/obfuscate",
    response_model=TransformOut,
    dependencies=[Depends(require_scope("obfuscate")), Depends(enforce_rate_limit)],
)
async def obfuscate(body: EngineRequest, request: Request) -> TransformOut:
    """Obfuscate under a per-request (or caller-held) key; returns key material and hints."""
    engine: PrivacyEngine = request.app.state.engine

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
)
async def synthesize(body: EngineRequest, request: Request) -> TransformOut:
    """Replace sensitive values with consistent synthetic surrogates."""
    engine: PrivacyEngine = request.app.state.engine

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
)
async def reveal(body: RevealRequest, request: Request) -> RevealOut:
    """Undo an obfuscation with the caller's key material (a separate scope)."""
    engine: PrivacyEngine = request.app.state.engine

    async def call() -> RevealResponse:
        payload = payload_from(request, body.data, body.text)
        return await run_in_threadpool(engine.reveal, payload, body.key_material, body.reveal_hints)

    result, tid = await run_engine(request, mode="reveal", policy_name="-", call=call)
    return RevealOut(**result.model_dump(), trace_id=tid)
