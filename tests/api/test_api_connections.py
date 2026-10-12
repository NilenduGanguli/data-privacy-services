"""``/api/v1/connections`` (contract §9): CRUD, scopes, tenant isolation, secrecy, test, describe,
objects and the wizard's count with its selection checklist."""

from __future__ import annotations

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
