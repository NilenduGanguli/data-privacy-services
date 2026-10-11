"""Policy tooling: presets, validate, plan preview."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from dps.auth import require_scope
from dps.engine.policy import list_presets
from dps.engine.service import EngineError, PlanResponse, PrivacyEngine
from dps.layers.api.common import payload_from, policy_label, run_engine
from dps.layers.api.schemas import PlanOut, PlanRequest, PolicyOut, ValidatePolicyRequest
from dps.ratelimit import enforce_rate_limit

router = APIRouter(prefix="/api/v1/policies", tags=["policies"])


@router.get(
    "/presets", dependencies=[Depends(require_scope("detect")), Depends(enforce_rate_limit)]
)
def presets() -> list[dict[str, str]]:
    """Every preset's name and description."""
    return list_presets()


@router.post(
    "/validate",
    response_model=PolicyOut,
    dependencies=[Depends(require_scope("detect")), Depends(enforce_rate_limit)],
)
def validate(body: ValidatePolicyRequest, request: Request) -> PolicyOut:
    """The effective policy for a preset name or inline policy; 400 naming the broken rule."""
    engine: PrivacyEngine = request.app.state.engine
    try:
        policy = engine.policy(body.policy)
    except EngineError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return PolicyOut(**policy.model_dump())


@router.post(
    "/plan",
    response_model=PlanOut,
    dependencies=[Depends(require_scope("detect")), Depends(enforce_rate_limit)],
)
async def plan(body: PlanRequest, request: Request) -> PlanOut:
    """What obfuscate / synthesize would do, per finding, with reasons — without doing it."""
    engine: PrivacyEngine = request.app.state.engine

    async def call() -> PlanResponse:
        payload = payload_from(request, body.data, body.text)
        return await run_in_threadpool(
            lambda: engine.plan(payload, body.policy, body.options, mode=body.mode)
        )

    result, tid = await run_engine(
        request,
        mode=f"plan:{body.mode}",
        policy_name=policy_label(body.policy, engine.settings.default_policy),
        call=call,
    )
    return PlanOut(**result.model_dump(), trace_id=tid)
