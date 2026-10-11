"""Request and response bodies for /api/v1 (responses reuse the engine's models plus a trace id).

Request bodies carry customer data, key material and seeds, so their validation errors never
echo the input (``hide_input_in_errors``; the app's 422 handler also drops ``input``/``ctx``).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from dps.engine.policy import Policy
from dps.engine.service import (
    DetectResponse,
    PlanResponse,
    RequestOptions,
    RevealResponse,
    TransformResponse,
)

_HIDDEN = ConfigDict(hide_input_in_errors=True)


class EngineRequest(BaseModel):
    """Body of detect / obfuscate / synthesize: exactly one of ``data`` or ``text``."""

    model_config = _HIDDEN

    data: Any | None = None
    text: str | None = None
    policy: str | dict[str, Any] | None = None
    options: RequestOptions = Field(default_factory=RequestOptions)


class PlanRequest(EngineRequest):
    """Body of the plan preview: an engine request plus the mode to plan for."""

    mode: Literal["obfuscate", "synthesize"] = "obfuscate"


class RevealRequest(BaseModel):
    """Body of reveal: the obfuscated payload, the caller's key material and optional hints."""

    model_config = _HIDDEN

    data: Any | None = None
    text: str | None = None
    key_material: dict[str, Any]
    reveal_hints: list[dict[str, Any]] = Field(default_factory=list)


class ValidatePolicyRequest(BaseModel):
    """Body of policy validation: a preset name or an inline policy."""

    model_config = _HIDDEN

    policy: str | dict[str, Any]


class DetectOut(DetectResponse):
    """``DetectResponse`` plus the trace id."""

    trace_id: str


class PlanOut(PlanResponse):
    """``PlanResponse`` plus the trace id."""

    trace_id: str


class TransformOut(TransformResponse):
    """``TransformResponse`` plus the trace id."""

    trace_id: str


class RevealOut(RevealResponse):
    """``RevealResponse`` plus the trace id."""

    trace_id: str


class PolicyOut(Policy):
    """The effective policy after resolving presets and overrides."""
