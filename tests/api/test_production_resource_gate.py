"""D3 bound to the credentials, not the connection row (S3 finding, auth-tenancy #1).

A ``secret_ref`` held by a ``production``-tagged connection is a production credential: a key
without ``admin`` cannot name it in another connection (create or patch), and an ``in_place`` /
``append`` through *any* connection that holds it needs ``admin`` — so a ``jobs`` key can no longer
clone the production connection without its tag and rewrite production in place. A retry of a
production ``in_place``/``append`` job re-checks the same rule."""

from __future__ import annotations

from typing import Any

import pytest

from dps.auth import Principal
from dps.jobs_models import SpecError
from dps.layers.connectors.targets import check_retry_scope
from tests.api.test_api_connections import FULL, JOBS, PG
from tests.api.test_api_jobs import PURPOSE, seed_snapshot
from tests.connectors.factories import make_connection

PROD_REF = "dps/src/test-pg-rw"


def _create(c: Any, name: str, headers: dict[str, str], **kw: Any) -> Any:  # noqa: ANN401
    body = {"name": name, "engine": "postgres", "params": PG, "roles": ["source", "target"], **kw}
    return c.post("/api/v1/connections", json=body, headers=headers)


def _in_place(conn_id: str, snap: str) -> dict[str, Any]:
    return {
        "mode": "obfuscate",
        "snapshot_id": snap,
        "source": {"connection_id": conn_id, "object": {"owner": "bank", "name": "customers"}},
        "target": {"write_mode": "in_place", "confirm_write": True},
        "purpose": PURPOSE,
    }


@pytest.mark.db
def test_a_jobs_key_cannot_clone_a_production_connection(api_client, db_settings) -> None:  # noqa: ANN001
    c, sink = api_client
    prod = _create(c, "bank-prod", FULL, secret_ref=PROD_REF, tags=["production"])
    assert prod.status_code == 201, prod.text
    seen = c.get(f"/api/v1/connections/{prod.json()['id']}", headers=JOBS).json()
    assert seen["secret_ref"] == PROD_REF  # a name, not a secret: visible to the jobs key
    # the exploit: the same params and secret_ref, no production tag
    clone = _create(c, "prod-clone", JOBS, secret_ref=seen["secret_ref"])
    assert clone.status_code == 403 and clone.json()["code"] == "admin_scope_required"
    # nor by moving another connection onto the production credential
    dev = _create(c, "bank-dev", JOBS, secret_ref="dps/src/test-pg")
    assert dev.status_code == 201, dev.text
    moved = c.patch(
        f"/api/v1/connections/{dev.json()['id']}", json={"secret_ref": PROD_REF}, headers=JOBS
    )
    assert moved.status_code == 403 and moved.json()["code"] == "admin_scope_required"
    assert c.get(f"/api/v1/connections/{dev.json()['id']}", headers=JOBS).json()["secret_ref"] == (
        "dps/src/test-pg"
    )
    refused = [e for e in sink.events if e.outcome == "rejected"]
    assert {e.action for e in refused} >= {"connection.create", "connection.update"}


@pytest.mark.db
def test_in_place_through_any_holder_of_a_production_credential_needs_admin(
    api_client, db_settings
) -> None:  # noqa: ANN001
    c, _ = api_client
    assert (
        _create(c, "bank-prod", FULL, secret_ref=PROD_REF, tags=["production"]).status_code == 201
    )
    # an untagged holder of the production credential (an admin made it, or it predates the rule)
    clone = _create(c, "prod-clone", FULL, secret_ref=PROD_REF)
    assert clone.status_code == 201, clone.text
    clone_id = clone.json()["id"]
    snap = seed_snapshot(db_settings, "t1", clone_id, "bank.customers")
    assert c.post(f"/api/v1/snapshots/{snap}/accept", headers=JOBS).status_code == 200
    for route in ("/api/v1/jobs/run/validate", "/api/v1/jobs/run"):
        refused = c.post(route, json=_in_place(clone_id, snap), headers=JOBS)
        assert refused.status_code == 403, refused.text
        assert refused.json()["code"] == "admin_scope_required"
    # the admin passes the gate (the run's later checks still apply)
    admin = c.post("/api/v1/jobs/run/validate", json=_in_place(clone_id, snap), headers=FULL)
    assert admin.json().get("code") != "admin_scope_required"
    # a holder of a non-production credential keeps the jobs-scope rules
    dev = _create(c, "bank-dev", JOBS, secret_ref="dps/src/test-pg")
    dev_snap = seed_snapshot(db_settings, "t1", dev.json()["id"], "bank.customers")
    assert c.post(f"/api/v1/snapshots/{dev_snap}/accept", headers=JOBS).status_code == 200
    plain = c.post(
        "/api/v1/jobs/run/validate", json=_in_place(dev.json()["id"], dev_snap), headers=JOBS
    )
    assert plain.json().get("code") != "admin_scope_required"


def _job(write_mode: str, *, target: str | None = None) -> Any:  # noqa: ANN401
    from datetime import UTC, datetime

    from dps.jobs import Job, JobKind, JobPhase, JobState

    now = datetime.now(UTC)
    return Job(
        id="j_1",
        tenant_id="t1",
        kind=JobKind.run,
        state=JobState.failed,
        phase=JobPhase.prepare,
        mode="obfuscate",
        connection_id="c_src",
        target_connection_id=target,
        spec={"target": {"write_mode": write_mode}},
        created_at=now,
        updated_at=now,
    )


def test_a_retry_of_a_production_write_rechecks_admin() -> None:
    jobs_key = Principal(key_id="k", name="jobs", tenant_ids=["t1"], scopes=["jobs"])
    admin_key = Principal(key_id="a", name="admin", tenant_ids=["t1"], scopes=["jobs", "admin"])
    prod = make_connection("c_src", roles=["source", "target"], tags=["production"])
    clone = make_connection("c_src", roles=["source", "target"])  # untagged, same credential
    refs = frozenset({clone.secret_ref or ""})
    for conn, production_refs in ((prod, frozenset()), (clone, refs)):
        for mode in ("in_place", "append"):
            with pytest.raises(SpecError) as refused:
                check_retry_scope(
                    _job(mode), conn, principal=jobs_key, production_refs=production_refs
                )
            assert refused.value.code == "admin_scope_required" and refused.value.status == 403
            check_retry_scope(
                _job(mode), conn, principal=admin_key, production_refs=production_refs
            )
    # table/files writes and non-production connections keep the jobs-scope retry
    check_retry_scope(_job("table", target="c_uat"), prod, principal=jobs_key, production_refs=refs)
    check_retry_scope(_job("in_place"), clone, principal=jobs_key, production_refs=frozenset())
