"""Findings and plans — the data the detectors produce and the planner consumes."""

from __future__ import annotations

import hashlib
from collections import Counter
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from dps.engine.taxonomy import Sensitivity


def hash_value(value: Any) -> str:
    """A short, salt-free content hash for dedupe, anchors and reports.

    It is a correlation id, not a secret, and is never logged with the value. It does not hide
    low-entropy values (an SSN or a birth date can be recovered by enumerating the space), so it
    must only travel where the caller is already entitled to the finding.
    """
    canon = str(value).strip().lower()
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()[:16]


class Locator(BaseModel):
    """Where a finding lives: a JSON path, optionally narrowed to a span within a string."""

    path: str = "$"
    start: int | None = None
    end: int | None = None

    @property
    def whole(self) -> bool:
        """True when the finding covers the whole leaf rather than a span of it."""
        return self.start is None


class Finding(BaseModel):
    """One detected sensitive value (never the value itself)."""

    locator: Locator
    entity_type: str
    score: float = Field(ge=0.0, le=1.0)
    detectors: list[str]
    sensitivity: Sensitivity
    evidence: list[str] = Field(default_factory=list)
    value_hash: str | None = None


#: Parameters every *new* plan entry gets when its method spec leaves them out (contract §6.5,
#: amendment 2026-10-12). ``fpe_digits`` preserves the leading zero, so the same digits give the
#: same token in a document, a text column and an integer or decimal column, and an integer
#: column never drops a token's first digit. The default lives here, not in the method, so a
#: reveal hint or reveal map without the param still means plain FF1 (tokens from v0.1.0).
PLAN_PARAM_DEFAULTS: dict[str, dict[str, Any]] = {"fpe_digits": {"leading_zero": "preserve"}}


def with_plan_defaults(method: str, params: dict[str, Any]) -> dict[str, Any]:
    """``params`` plus :data:`PLAN_PARAM_DEFAULTS` for ``method`` that it does not set (a copy)."""
    missing = {k: v for k, v in PLAN_PARAM_DEFAULTS.get(method, {}).items() if k not in params}
    return {**params, **missing}


class PlanEntry(BaseModel):
    """One finding and the method the policy resolved for it (params completed with
    :data:`PLAN_PARAM_DEFAULTS`, so reveal hints record them)."""

    finding: Finding
    method: str
    params: dict[str, Any] = Field(default_factory=dict)
    reason: str
    reversible: bool

    @model_validator(mode="after")
    def _plan_defaults(self) -> PlanEntry:
        self.params = with_plan_defaults(self.method, self.params)
        return self


class Plan(BaseModel):
    """The resolved plan for one request."""

    mode: Literal["obfuscate", "synthesize"]
    record_sensitivity: Sensitivity
    entries: list[PlanEntry] = Field(default_factory=list)

    @property
    def reversible_count(self) -> int:
        """How many entries use a reversible method."""
        return sum(1 for e in self.entries if e.reversible)

    def methods_used(self) -> dict[str, int]:
        """``{method: count}`` over the entries."""
        return dict(Counter(e.method for e in self.entries))
