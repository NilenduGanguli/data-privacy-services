"""Policy tooling: presets, validate, plan preview."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from dps.auth import require_scope
from dps.engine.policy import preset_summaries
from dps.engine.service import EngineError, PlanResponse, PrivacyEngine
from dps.errors import CodedError
from dps.layers.api.common import CodedHTTPException, payload_from, run_engine
from dps.layers.api.policy_refs import policy_label, resolve, resolved_policy
from dps.layers.api.schemas import PlanOut, PlanRequest, PolicyOut, ValidatePolicyRequest
from dps.ratelimit import enforce_rate_limit

router = APIRouter(prefix="/api/v1/policies", tags=["policies"])


@router.get(
    "/presets", dependencies=[Depends(require_scope("detect")), Depends(enforce_rate_limit)]
)
def presets() -> list[dict[str, Any]]:
    """Every preset's name, description, version, config hash, mode and reversibility."""
    return preset_summaries()


@router.post(
    "/validate",
    response_model=PolicyOut,
    dependencies=[Depends(require_scope("detect")), Depends(enforce_rate_limit)],
)
async def validate(body: ValidatePolicyRequest, request: Request) -> PolicyOut:
    """The effective policy for a preset name, an inline policy or an ``@name[:vN]`` reference;
    400 naming the broken rule (422 ``verify_warn_disallowed`` where verification is strict)."""
    engine: PrivacyEngine = request.app.state.engine
    ref = await resolve(request, body.policy)
    try:
        policy = engine.policy(ref)
    except EngineError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except CodedError as exc:
        raise CodedHTTPException(exc.status, exc.code, exc.detail) from None
    return PolicyOut(**policy.model_dump())


@router.post(
    "/plan",
    response_model=PlanOut,
    dependencies=[Depends(require_scope("detect")), Depends(enforce_rate_limit)],
)
async def plan(body: PlanRequest, request: Request) -> PlanOut:
    """What obfuscate / synthesize would do, per finding, with reasons — without doing it."""
    engine: PrivacyEngine = request.app.state.engine
    ref = await resolved_policy(request, body.policy, mode=f"plan:{body.mode}")  # M5: @name[:vN]

    async def call() -> PlanResponse:
        payload = payload_from(request, body.data, body.text)
        extra = await engine.label_leftovers(payload, ref, body.options)  # T3 (opt-in)
        return await run_in_threadpool(
            lambda: engine.plan(payload, ref, body.options, mode=body.mode, extra_findings=extra)
        )

    result, tid = await run_engine(
        request,
        mode=f"plan:{body.mode}",
        policy_name=policy_label(ref, engine.settings.default_policy),
        call=call,
    )
    return PlanOut(**result.model_dump(), trace_id=tid)
