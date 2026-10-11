from __future__ import annotations

from dps.engine.findings import Finding, Locator, Plan, PlanEntry, hash_value
from dps.engine.taxonomy import Sensitivity


def test_locator_whole_vs_span() -> None:
    assert Locator(path="$.ssn").whole is True
    assert Locator(path="$.notes", start=3, end=9).whole is False


def test_finding_defaults_and_hash_is_not_the_value() -> None:
    f = Finding(
        locator=Locator(path="$.ssn"),
        entity_type="SSN",
        score=0.99,
        detectors=["pattern:us_ssn"],
        sensitivity=Sensitivity.CRITICAL,
        value_hash=hash_value("123-45-6789"),
    )
    assert f.evidence == [] and len(f.value_hash or "") == 16
    assert "123" not in (f.value_hash or "")
    assert hash_value(" 123-45-6789 ") == hash_value("123-45-6789")


def test_plan_counts_reversible_entries() -> None:
    f = Finding(
        locator=Locator(path="$.a"),
        entity_type="EMAIL",
        score=0.9,
        detectors=["x"],
        sensitivity=Sensitivity.MEDIUM,
    )
    plan = Plan(
        mode="obfuscate",
        record_sensitivity=Sensitivity.MEDIUM,
        entries=[
            PlanEntry(finding=f, method="email", params={}, reason="policy:EMAIL", reversible=True),
            PlanEntry(
                finding=f, method="redact", params={}, reason="policy:EMAIL", reversible=False
            ),
        ],
    )
    assert plan.reversible_count == 1 and plan.methods_used() == {"email": 1, "redact": 1}
