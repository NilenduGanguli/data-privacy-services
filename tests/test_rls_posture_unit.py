from __future__ import annotations

from dps.db import ALLOWED_EXTRA_POLICIES, TENANT_TABLES, evaluate_rls_posture

GOOD_POLICIES = {
    t: ["tenant_isolation", *sorted(ALLOWED_EXTRA_POLICIES.get(t, frozenset()))]
    for t in TENANT_TABLES
}


def _eval(**kw):  # noqa: ANN003, ANN202
    base = dict(
        rls_enabled=True,
        rolsuper=False,
        rolbypassrls=False,
        policies_by_table=GOOD_POLICIES,
        predicate_ok={t: True for t in TENANT_TABLES},
        forced={t: True for t in TENANT_TABLES},
        partition_leaks=[],
    )
    base.update(kw)
    return evaluate_rls_posture(**base)


def test_sound_posture_has_no_problems() -> None:
    assert _eval() == []
    assert _eval(worker_member=False) == []


def test_each_violation_is_named() -> None:
    assert any("superuser" in p for p in _eval(rolsuper=True))
    assert any("BYPASSRLS" in p for p in _eval(rolbypassrls=True))
    assert any("RLS_ENABLED" in p for p in _eval(rls_enabled=False))
    missing = dict(GOOD_POLICIES, dps_connection=[])
    assert any(
        "dps_connection: missing tenant_isolation" in p for p in _eval(policies_by_table=missing)
    )
    extra = dict(GOOD_POLICIES, dps_snapshot=["tenant_isolation", "open_door"])
    assert any("dps_snapshot: unexpected" in p for p in _eval(policies_by_table=extra))
    forced = {**{t: True for t in TENANT_TABLES}, "dps_job": False}
    assert any("not FORCE" in p for p in _eval(forced=forced))
    predicate = {**{t: True for t in TENANT_TABLES}, "dps_job": False}
    assert any("app.tenant_id" in p for p in _eval(predicate_ok=predicate))
    leaks = ["dps.dps_audit_log_2026_10"]
    assert any("dps_audit_log_2026_10" in p for p in _eval(partition_leaks=leaks))
    assert any("dps_worker" in p for p in _eval(worker_member=True))
