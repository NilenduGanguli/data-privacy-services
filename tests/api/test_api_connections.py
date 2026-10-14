"""``/api/v1/connections`` (contract §9): CRUD, scopes, tenant isolation, secrecy, test, describe,
objects and the wizard's count with its selection checklist."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

import pytest

from tests.fixtures.services import service_url

pytestmark = pytest.mark.db
_SRC = urlparse(service_url("source-pg"))
PG = {"host": _SRC.hostname or "127.0.0.1", "port": _SRC.port or 5432, "database": "bank"}
FULL = {"X-API-KEY": "dps_full", "X-DPS-Tenant": "t1"}
JOBS = {"X-API-KEY": "dps_jobs", "X-DPS-Tenant": "t1"}


def test_connection_crud_scopes_and_secrecy(api_client) -> None:  # noqa: ANN001
    c, sink = api_client
    body = {
        "name": "dev-pg",
        "engine": "postgres",
        "params": PG,
        "credentials": {"username": "u", "password": "pw-123-distinct"},
    }
    r = c.post("/api/v1/connections", json=body, headers=JOBS)
    assert r.status_code == 201 and r.json()["has_secret"] and "pw-123-distinct" not in r.text
    conn_id = r.json()["id"]
    dup = c.post("/api/v1/connections", json=body, headers=JOBS)
    assert dup.status_code == 409 and dup.json()["code"] == "connection_name_taken"
    prod = {
        **body,
        "name": "prod-pg",
        "credentials": None,
        "secret_ref": "dps/src/x",
        "tags": ["production"],
    }
    refused = c.post("/api/v1/connections", json=prod, headers=JOBS)
    assert refused.status_code == 403 and refused.json()["code"] == "admin_scope_required"
    assert c.post("/api/v1/connections", json=prod, headers=FULL).status_code == 201
    tag = c.patch(f"/api/v1/connections/{conn_id}", json={"tags": ["production"]}, headers=JOBS)
    assert tag.status_code == 403
    renamed = c.patch(f"/api/v1/connections/{conn_id}", json={"name": "dev-pg-2"}, headers=JOBS)
    assert renamed.status_code == 200 and renamed.json()["name"] == "dev-pg-2"
    assert "pw-123-distinct" not in renamed.text
    listed = c.get("/api/v1/connections?limit=1", headers=JOBS).json()
    assert len(listed["items"]) == 1 and listed["next_cursor"]
    rest = c.get(f"/api/v1/connections?limit=1&cursor={listed['next_cursor']}", headers=JOBS)
    assert len(rest.json()["items"]) == 1 and rest.json()["next_cursor"] is None
    bad_cursor = c.get("/api/v1/connections?cursor=nope", headers=JOBS)
    assert bad_cursor.status_code == 400 and bad_cursor.json()["code"] == "cursor_invalid"
    other = {**JOBS, "X-DPS-Tenant": "t2"}
    assert c.get(f"/api/v1/connections/{conn_id}", headers=other).status_code == 404
    assert c.delete(f"/api/v1/connections/{conn_id}", headers=other).status_code == 404
    missing = c.get("/api/v1/connections/c_missing", headers=JOBS)
    assert missing.status_code == 404 and missing.json()["code"] == "connection_not_found"
    assert c.delete(f"/api/v1/connections/{conn_id}", headers=JOBS).status_code == 204
    assert c.get(f"/api/v1/connections/{conn_id}", headers=JOBS).status_code == 404
    no_scope = {"X-API-KEY": "dps_detect", "X-DPS-Tenant": "t1"}
    assert c.get("/api/v1/connections", headers=no_scope).status_code == 403
    assert [e.action for e in sink.events][:2] == ["connection.create", "connection.create"]
    created = [e for e in sink.events if e.action == "connection.create"]
    assert [e.outcome for e in created] == ["ok", "rejected", "rejected", "ok"]
    assert all(e.route == "/api/v1/connections" for e in created)
    assert {e.action for e in sink.events} >= {"connection.update", "connection.delete"}


def test_secret_refs_are_scoped_to_the_callers_tenant(api_client) -> None:  # noqa: ANN001
    c, sink = api_client
    root = Path(c.app.state.settings.secret_dir)
    only_t1 = root / "t1" / "dps" / "src" / "t1-only"
    only_t1.mkdir(parents=True)
    (only_t1 / "username").write_text("dps_reader")
    (only_t1 / "password").write_text("t1-only-pw-distinct")
    t2 = {**JOBS, "X-DPS-Tenant": "t2"}
    body = {"name": "steal", "engine": "postgres", "params": PG, "roles": ["source"]}
    refused = c.post(
        "/api/v1/connections", json={**body, "secret_ref": "t1/dps/src/t1-only"}, headers=t2
    )
    assert refused.status_code == 403 and refused.json()["code"] == "secret_ref_foreign_tenant"
    assert "t1-only" not in refused.text
    # the same name in t2 is t2's own secret folder, which holds no such secret
    made = c.post("/api/v1/connections", json={**body, "secret_ref": "dps/src/t1-only"}, headers=t2)
    assert made.status_code == 201
    tested = c.post(
        f"/api/v1/connections/{made.json()['id']}/test", json={"role": "source"}, headers=t2
    )
    assert tested.status_code == 200 and tested.json()["error_code"] == "secret_unavailable"
    redirect = c.patch(
        f"/api/v1/connections/{made.json()['id']}",
        json={"secret_ref": "t1/dps/src/t1-only"},
        headers=t2,
    )
    assert redirect.status_code == 403 and redirect.json()["code"] == "secret_ref_foreign_tenant"
    shared = c.post(
        "/api/v1/connections", json={**body, "secret_ref": "_shared/dps/src/t1-only"}, headers=t2
    )
    assert shared.status_code == 403 and shared.json()["code"] == "secret_ref_not_shared"
    traversal = c.post(
        "/api/v1/connections", json={**body, "secret_ref": "dps/../t1/dps/src/t1-only"}, headers=t2
    )
    assert traversal.status_code == 422
    assert "t1-only-pw-distinct" not in str([e.model_dump() for e in sink.events])
    rejected = [e for e in sink.events if e.outcome == "rejected"]
    assert {e.status for e in rejected} >= {403} and all(e.tenant_id == "t2" for e in rejected)


def test_production_changes_need_admin_and_endpoint_changes_need_credentials(api_client) -> None:  # noqa: ANN001
    c, sink = api_client
    prod = c.post(
        "/api/v1/connections",
        json={
            "name": "prod-pg",
            "engine": "postgres",
            "params": PG,
            "secret_ref": "dps/src/test-pg",
            "tags": ["production"],
        },
        headers=FULL,
    ).json()
    url = f"/api/v1/connections/{prod['id']}"
    redirect = c.patch(url, json={"params": {**PG, "host": "attacker.example"}}, headers=JOBS)
    assert redirect.status_code == 403 and redirect.json()["code"] == "admin_scope_required"
    for body in ({"name": "prod-pg-2"}, {"secret_ref": "dps/src/test-pg-rw"}, {"tags": []}):
        r = c.patch(url, json=body, headers=JOBS)
        assert r.status_code == 403 and r.json()["code"] == "admin_scope_required", body
    gone = c.delete(url, headers=JOBS)
    assert gone.status_code == 403 and gone.json()["code"] == "admin_scope_required"
    assert c.get(url, headers=JOBS).json()["params"]["host"] == PG["host"]
    # another tenant cannot even see it, admin key or not
    for key in (JOBS, FULL):
        other = {**key, "X-DPS-Tenant": "t2"}
        assert c.patch(url, json={"name": "x-pg"}, headers=other).status_code == 404
        assert c.delete(url, headers=other).status_code == 404

    dev = c.post(
        "/api/v1/connections",
        json={
            "name": "dev-pg",
            "engine": "postgres",
            "params": PG,
            "credentials": {"username": "u", "password": "pw-old-distinct"},
        },
        headers=JOBS,
    ).json()
    durl = f"/api/v1/connections/{dev['id']}"
    moved = c.patch(durl, json={"params": {**PG, "host": "attacker.example"}}, headers=JOBS)
    assert moved.status_code == 403
    assert moved.json()["code"] == "connection_endpoint_change_requires_credentials"
    assert "attacker" not in moved.text
    with_creds = c.patch(
        durl,
        json={
            "params": {**PG, "host": "uat.example"},
            "credentials": {"username": "u", "password": "pw-new-distinct"},
        },
        headers=JOBS,
    )
    assert with_creds.status_code == 200 and with_creds.json()["params"]["host"] == "uat.example"
    assert with_creds.json()["has_secret"] and "pw-new" not in with_creds.text
    by_admin = c.patch(durl, json={"params": {**PG, "host": "replica.example"}}, headers=FULL)
    assert by_admin.status_code == 200 and by_admin.json()["has_secret"]
    assert c.delete(url, headers=FULL).status_code == 204

    updates = [e for e in sink.events if e.action == "connection.update"]
    assert [(e.outcome, e.status) for e in updates][:1] == [("rejected", 403)]
    assert updates[0].counts == {"field.params.host": 1}
    assert {k for e in updates for k in e.counts} >= {
        "field.name",
        "field.secret_ref",
        "field.tags",
        "field.credentials",
        "field.params.host",
    }
    dumped = str([e.model_dump() for e in sink.events])
    for value in ("attacker", "uat.example", "replica", "pw-old", "pw-new", "test-pg-rw"):
        assert value not in dumped, value
    deletes = [e for e in sink.events if e.action == "connection.delete"]
    assert [(e.outcome, e.status) for e in deletes] == [
        ("rejected", 403),
        ("rejected", 404),
        ("rejected", 404),
        ("ok", 204),
    ]


def test_a_connection_test_takes_no_param_overrides() -> None:
    """SEC-3: the test route can only test the stored endpoint (no overrides to gate)."""
    from dps.layers.api.routers.connections import ConnectionTestRequest

    assert set(ConnectionTestRequest.model_fields) == {"role", "object", "write_mode"}


def test_reserved_and_upper_case_tenants_never_reach_the_database(api_client) -> None:  # noqa: ANN001
    c, _ = api_client
    reserved = c.get("/api/v1/connections", headers={**FULL, "X-DPS-Tenant": "_system"})
    assert reserved.status_code == 403 and reserved.json()["code"] == "tenant_reserved"
    upper = c.get("/api/v1/connections", headers={**FULL, "X-DPS-Tenant": "Retail"})
    assert upper.status_code == 400 and upper.json()["code"] == "tenant_invalid"


@pytest.mark.integration
@pytest.mark.pg_source
def test_test_describe_objects_and_count(api_client, source_pg_sync) -> None:  # noqa: ANN001
    c, sink = api_client
    conn_id = c.post(
        "/api/v1/connections",
        json={
            "name": "src",
            "engine": "postgres",
            "params": PG,
            "secret_ref": "dps/src/test-pg",
            "roles": ["source", "target"],
        },
        headers=JOBS,
    ).json()["id"]
    obj = f"{source_pg_sync.schema}.customers"
    tested = c.post(
        f"/api/v1/connections/{conn_id}/test",
        json={"role": "source", "object": obj},
        headers=JOBS,
    ).json()
    assert tested["ok"]
    assert {x["name"] for x in tested["checks"]} >= {"connect", "select_source", "no_dml_on_source"}
    assert c.get(f"/api/v1/connections/{conn_id}", headers=JOBS).json()["last_test"]["ok"]
    objects = c.get(
        f"/api/v1/connections/{conn_id}/objects?owner={source_pg_sync.schema}", headers=JOBS
    ).json()
    assert {"owner": source_pg_sync.schema, "name": "customers"} in objects["items"]
    assert objects["next_cursor"] is None
    shape = c.get(f"/api/v1/connections/{conn_id}/describe?object={obj}", headers=JOBS).json()
    assert shape["key"] == ["customer_id"]
    gone = c.get(f"/api/v1/connections/{conn_id}/describe?object={obj}_nope", headers=JOBS)
    assert gone.status_code == 422 and gone.json()["code"] == "object_not_found"
    counted = c.post(
        f"/api/v1/connections/{conn_id}/count",
        headers=JOBS,
        json={
            "object": {"owner": source_pg_sync.schema, "name": "customers"},
            "selection": {"where": "country_code IN ('IN', 'US')"},
        },
    ).json()
    assert counted["rows"] == 200 and counted["binds"] == 2
    assert "'IN'" not in counted["runs_as"] and "'US'" not in counted["runs_as"]
    assert counted["runs_as"].startswith("SELECT ") and "ORDER BY" in counted["runs_as"]
    assert all(ch["ok"] for ch in counted["checks"])
    bad = c.post(
        f"/api/v1/connections/{conn_id}/count",
        headers=JOBS,
        json={
            "object": {"owner": source_pg_sync.schema, "name": "customers"},
            "selection": {"query": "SELECT * FROM bank.customers FOR UPDATE"},
        },
    )
    assert bad.status_code == 422 and bad.json()["code"] == "selection_for_update"
    check = next(ch for ch in bad.json()["checks"] if ch["name"] == "no_into_lock_procs")
    assert check["ok"] is False
    tested_target = c.post(
        f"/api/v1/connections/{conn_id}/test",
        json={"role": "target", "object": obj, "write_mode": "in_place"},
        headers=JOBS,
    ).json()
    assert tested_target["ok"] is False  # dps_reader cannot UPDATE: the wizard shows why
    update = next(ch for ch in tested_target["checks"] if ch["name"] == "update_target")
    assert update["ok"] is False
    assert {e.action for e in sink.events} >= {"connection.test", "connection.count"}


@pytest.mark.integration
@pytest.mark.pg_source
def test_a_refused_login_is_a_failed_test_not_an_error(api_client) -> None:  # noqa: ANN001
    c, _ = api_client
    conn_id = c.post(
        "/api/v1/connections",
        json={
            "name": "wrong-pw",
            "engine": "postgres",
            "params": PG,
            "credentials": {"username": "dps_reader", "password": "not-the-password"},
        },
        headers=JOBS,
    ).json()["id"]
    r = c.post(f"/api/v1/connections/{conn_id}/test", json={"role": "source"}, headers=JOBS)
    assert r.status_code == 200 and r.json()["ok"] is False
    assert r.json()["error_code"] == "connection_failed" and "not-the-password" not in r.text
    missing_ref = c.post(
        "/api/v1/connections",
        json={"name": "no-ref", "engine": "postgres", "params": PG, "secret_ref": "dps/src/none"},
        headers=JOBS,
    ).json()["id"]
    r = c.post(f"/api/v1/connections/{missing_ref}/test", json={"role": "source"}, headers=JOBS)
    assert r.status_code == 200 and r.json()["error_code"] == "secret_unavailable"
    counted = c.post(
        f"/api/v1/connections/{missing_ref}/count",
        headers=JOBS,
        json={"object": "bank.customers", "selection": {}},
    )
    assert counted.status_code == 409 and counted.json()["code"] == "secret_unavailable"
