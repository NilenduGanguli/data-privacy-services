from __future__ import annotations

import pytest
from pydantic import ValidationError

from dps.engine.findings import Finding, Locator
from dps.engine.planner import build_plan, explain_plan
from dps.engine.policy import Policy, load_preset
from dps.engine.taxonomy import Sensitivity


def _f(entity: str, path: str, score: float = 0.95) -> Finding:
    from dps.engine.taxonomy import entity_info

    return Finding(
        locator=Locator(path=path),
        entity_type=entity,
        score=score,
        detectors=["t"],
        sensitivity=entity_info(entity).sensitivity,
    )


FINDINGS = [
    _f("SSN", "$.ssn"),
    _f("EMAIL", "$.email"),
    _f("CITY", "$.city"),
    _f("GADGET_ID", "$.g"),
]


def test_obfuscate_plan_follows_policy_and_falls_back_without_fpe() -> None:
    plan = build_plan(FINDINGS, load_preset("gdpr-pseudonymise"), "obfuscate", fpe_enabled=True)
    by = {e.finding.entity_type: e for e in plan.entries}
    assert (
        by["SSN"].method == "fpe_digits"
        and by["SSN"].params == {"keep_suffix": 4, "leading_zero": "preserve"}
        and by["SSN"].reversible
    )
    assert by["EMAIL"].method == "email" and by["GADGET_ID"].method == "det_token"
    assert by["GADGET_ID"].reason == "policy:default_sensitive" and by["SSN"].reason == "policy:SSN"
    assert plan.record_sensitivity is Sensitivity.CRITICAL
    nofpe = build_plan(FINDINGS, load_preset("gdpr-pseudonymise"), "obfuscate", fpe_enabled=False)
    ssn = next(e for e in nofpe.entries if e.finding.entity_type == "SSN")
    assert (
        ssn.method == "det_token"
        and ssn.reason.endswith("fallback:fpe_disabled")
        and ssn.params == {}
    )


def test_synthesize_plan_keeps_non_sensitive_unless_overridden() -> None:
    plan = build_plan(FINDINGS, load_preset("synthetic-realistic"), "synthesize", fpe_enabled=False)
    by = {e.finding.entity_type: e for e in plan.entries}
    assert by["SSN"].method == "surrogate" and by["EMAIL"].method == "surrogate"
    assert by["CITY"].method == "keep" and by["CITY"].reason == "policy:keep_non_sensitive"
    strict = build_plan(
        [_f("CITY", "$.city")], load_preset("synthetic-strict"), "synthesize", fpe_enabled=False
    )
    assert strict.entries[0].method == "surrogate"


def test_explain_plan_is_value_free() -> None:
    plan = build_plan(FINDINGS, load_preset("pci-dss"), "obfuscate", fpe_enabled=True)
    rows = explain_plan(plan)
    assert rows[0].keys() == {
        "path",
        "span",
        "entity_type",
        "score",
        "method",
        "params",
        "reason",
        "reversible",
    }
    assert all("ssn" not in str(r["params"]).lower() for r in rows)


# --- additions beyond the plan (see docs/superpowers/build/M0-DEVIATIONS.md, Task 16) ----------
def test_unknown_modes_are_refused() -> None:
    with pytest.raises(ValueError):
        build_plan(FINDINGS, load_preset("pci-dss"), "reveal", fpe_enabled=True)


def test_span_findings_explain_their_span_and_entries_keep_finding_order() -> None:
    span = Finding(
        locator=Locator(path="$.notes", start=5, end=26),
        entity_type="EMAIL",
        score=0.9,
        detectors=["pattern:email"],
        sensitivity=Sensitivity.MEDIUM,
        value_hash="0123456789abcdef",
    )
    plan = build_plan([*FINDINGS, span], load_preset("pci-dss"), "obfuscate", fpe_enabled=True)
    assert [e.finding.locator.path for e in plan.entries] == [
        "$.ssn",
        "$.email",
        "$.city",
        "$.g",
        "$.notes",
    ]
    rows = explain_plan(plan)
    assert rows[-1]["span"] == [5, 26] and rows[0]["span"] is None
    assert "0123456789abcdef" not in str(
        rows
    )  # the finding's value hash stays out of the plan rows


def test_synthesize_overrides_are_key_less_and_beat_keep_non_sensitive() -> None:
    # M5 security review F-12: a keyed method (here FPE) is no longer a synthesize override —
    # synthetic output must have no path back to the original — so the policy is refused
    with pytest.raises(ValidationError, match="key-less"):
        Policy.model_validate(
            {"synthesize": {"overrides": {"SSN": {"method": "fpe_digits", "keep_suffix": 4}}}}
        )
    policy = Policy.model_validate(
        {
            "synthesize": {
                "default": {"method": "surrogate"},
                "overrides": {"AGE": {"method": "redact"}},
            }
        }
    )
    plan = build_plan([*FINDINGS, _f("AGE", "$.age")], policy, "synthesize", fpe_enabled=False)
    by = {e.finding.entity_type: e for e in plan.entries}
    assert by["SSN"].method == "surrogate" and by["SSN"].reversible is False
    assert by["AGE"].method == "redact" and by["AGE"].reversible is False
    assert by["AGE"].reason == "policy:AGE"  # an explicit override beats keep_non_sensitive
    assert plan.mode == "synthesize" and plan.reversible_count == 0


def test_empty_findings_give_an_empty_low_plan() -> None:
    plan = build_plan([], load_preset("gdpr-pseudonymise"), "obfuscate", fpe_enabled=True)
    assert (
        plan.entries == []
        and plan.record_sensitivity is Sensitivity.LOW
        and explain_plan(plan) == []
    )


def test_fpe_digits_entries_preserve_the_leading_zero_unless_the_policy_says_otherwise() -> None:
    from dps.engine.findings import PlanEntry

    policy = Policy(
        treat={
            "SSN": {"method": "fpe_digits", "keep_suffix": 4},
            "ACCOUNT_NUMBER": {"method": "fpe_digits", "leading_zero": "any"},
        }
    )
    findings = [_f("SSN", "$.ssn"), _f("ACCOUNT_NUMBER", "$.acct"), _f("EMAIL", "$.email")]
    by = {
        e.finding.entity_type: e
        for e in build_plan(findings, policy, "obfuscate", fpe_enabled=True).entries
    }
    assert by["SSN"].params == {"keep_suffix": 4, "leading_zero": "preserve"}
    assert by["ACCOUNT_NUMBER"].params == {"leading_zero": "any"}
    assert by["EMAIL"].params == {}
    # the policy object is not changed, and any PlanEntry built elsewhere gets the same default
    assert policy.treat["SSN"].params == {"keep_suffix": 4}
    entry = PlanEntry(finding=_f("SSN", "$.x"), method="fpe_digits", reason="t", reversible=True)
    assert entry.params == {"leading_zero": "preserve"}
    assert (
        PlanEntry(finding=_f("SSN", "$.x"), method="det_token", reason="t", reversible=True).params
        == {}
    )
