"""The planner: resolve one method per finding from the policy, with reasons (spec §5.4–5.5).

Static fallbacks (FPE disabled) happen here so the plan shown to a reviewer is what will run;
value-dependent fallbacks (domain too small, unparseable date) happen in ``run_method`` and are
reported through the outcome notes.
"""

from __future__ import annotations

from typing import Any, Literal, cast

from dps.engine.findings import Finding, Plan, PlanEntry
from dps.engine.policy import Policy
from dps.engine.taxonomy import Sensitivity, entity_info, record_sensitivity
from dps.engine.transform.methods import METHODS

_FPE_METHODS = {"fpe_digits", "fpe_text"}
_MODES = ("obfuscate", "synthesize")


def build_plan(
    findings: list[Finding],
    policy: Policy,
    mode: str,
    *,
    fpe_enabled: bool,
    external_enabled: bool = True,
) -> Plan:
    """One :class:`PlanEntry` per finding, in finding order.

    ``external_enabled`` False (no tokenization adapter configured, M5) marks every
    ``external_tokenize`` entry ``|external:unconfigured``, so a plan preview shows why the
    request will fail.

    Raises:
        ValueError: ``mode`` is not ``obfuscate`` or ``synthesize``.
    """
    if mode not in _MODES:
        raise ValueError(f"mode must be obfuscate or synthesize, got {mode!r}")
    entries: list[PlanEntry] = []
    for finding in findings:
        spec = policy.method_for(finding.entity_type, mode)
        method, params = spec.method, dict(spec.params)
        if mode == "synthesize":
            explicit = finding.entity_type in policy.synthesize.overrides
            reason = f"policy:{finding.entity_type}" if explicit else "policy:synthesize_default"
            if (
                not explicit
                and policy.synthesize.keep_non_sensitive
                and entity_info(finding.entity_type).sensitivity is Sensitivity.LOW
            ):
                method, params, reason = "keep", {}, "policy:keep_non_sensitive"
        else:
            reason = (
                f"policy:{finding.entity_type}"
                if finding.entity_type in policy.treat
                else "policy:default_sensitive"
            )
        if method in _FPE_METHODS and not fpe_enabled:
            method, params, reason = "det_token", {}, f"{reason}|fallback:fpe_disabled"
        if method == "external_tokenize" and not external_enabled:
            reason = f"{reason}|external:unconfigured"
        entries.append(
            PlanEntry(
                finding=finding,
                method=method,
                params=params,
                reason=reason,
                reversible=METHODS[method].reversible,
            )
        )
    return Plan(
        mode=cast(Literal["obfuscate", "synthesize"], mode),
        record_sensitivity=record_sensitivity(f.entity_type for f in findings),
        entries=entries,
    )


def explain_plan(plan: Plan) -> list[dict[str, Any]]:
    """Reviewer-facing rows: locator, type, score, method, params, reason. No values."""
    rows = []
    for e in plan.entries:
        loc = e.finding.locator
        rows.append(
            {
                "path": loc.path,
                "span": None if loc.whole else [loc.start, loc.end],
                "entity_type": e.finding.entity_type,
                "score": e.finding.score,
                "method": e.method,
                "params": e.params,
                "reason": e.reason,
                "reversible": e.reversible,
            }
        )
    return rows
