"""The per-request report (spec §5.7): counts, methods, verification, configuration hash. No values."""

from __future__ import annotations

from collections import Counter
from typing import Any

from pydantic import BaseModel, Field

from dps import __version__
from dps.engine.findings import Finding, Plan
from dps.engine.verify import Verification


class Report(BaseModel):
    """What was found and done for one request — counts, names and hashes only."""

    mode: str
    policy: str
    policy_hash: str
    engine_version: str = __version__
    findings_total: int
    findings_by_type: dict[str, int]
    findings_by_detector: dict[str, int]
    record_sensitivity: str
    methods_used: dict[str, int]
    fallbacks: dict[str, int] = Field(default_factory=dict)
    reversible_values: int
    verification: Verification | None = None
    llm: dict[str, Any] = Field(default_factory=lambda: {"calls": 0, "raw_pii_sent": 0})
    duration_ms: float


def build_report(
    *,
    mode: str,
    policy_name: str,
    policy_hash: str,
    findings: list[Finding],
    plan: Plan | None,
    methods_used: dict[str, int],
    notes: dict[str, int],
    reversible_values: int,
    verification: Verification | None,
    llm_calls: int,
    duration_ms: float,
) -> Report:
    """Assemble the report from what the request produced (findings carry no values).

    ``fallbacks`` counts one per value that did not get its policy method: the planner's static
    fallbacks (a ``|fallback:<why>`` reason, e.g. ``fpe_disabled``) plus the ``notes`` a method
    reported while running (e.g. ``fpe_domain_too_small``). ``plan`` is the applied plan, or
    ``None`` when nothing was transformed.
    """
    by_type = Counter(f.entity_type for f in findings)
    by_detector = Counter(d.split(":")[0] for f in findings for d in f.detectors)
    fallbacks = Counter(notes)
    for entry in plan.entries if plan else ():
        fallbacks.update(p for p in entry.reason.split("|")[1:] if p.startswith("fallback:"))
    return Report(
        mode=mode,
        policy=policy_name,
        policy_hash=policy_hash,
        findings_total=len(findings),
        findings_by_type=dict(by_type),
        findings_by_detector=dict(by_detector),
        record_sensitivity=str(plan.record_sensitivity) if plan else "LOW",
        methods_used=methods_used,
        fallbacks=dict(fallbacks),
        reversible_values=reversible_values,
        verification=verification,
        llm={"calls": llm_calls, "raw_pii_sent": 0},
        duration_ms=round(duration_ms, 2),
    )
