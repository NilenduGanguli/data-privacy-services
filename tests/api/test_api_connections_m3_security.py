"""WP-I (m3 merge): main's SEC-2 and SEC-3 rules on M3's Oracle and MongoDB connections, through the
API (contract §5.1). No Oracle or MongoDB server is needed: create, patch and delete never connect.

* SEC-3: a jobs-scope key cannot change or delete a production Oracle/MongoDB connection; on a
  non-production one every endpoint-defining param (Oracle host/port/service name/TLS protocol,
  MongoDB host/port/database/replica set/TLS) needs the credentials restated, the rest does not.
* SEC-2: refs are tenant-relative; naming another tenant's folder or an escaping path is refused.
"""

from __future__ import annotations

from typing import Any

import pytest

pytestmark = pytest.mark.db
FULL = {"X-API-KEY": "dps_full", "X-DPS-Tenant": "t1"}
JOBS = {"X-API-KEY": "dps_jobs", "X-DPS-Tenant": "t1"}
ORA = {"host": "oracle", "port": 1521, "service_name": "FREEPDB1"}
MDB = {"host": "mongo", "port": 27017, "database": "crm", "options": {"directConnection": "true"}}


def _create(c: Any, name: str, engine: str, params: dict[str, Any], **extra: Any) -> str:
    body = {"name": name, "engine": engine, "params": params, "roles": ["source"], **extra}
    r = c.post("/api/v1/connections", json=body, headers=extra.pop("headers", FULL))
    assert r.status_code == 201, (r.status_code, r.json().get("code"))
    return str(r.json()["id"])


@pytest.mark.parametrize(
    ("engine", "params", "ref"),
    [("oracle", ORA, "dps/src/oracle-crm-ro"), ("mongo", MDB, "dps/src/mongo-crm")],
)
def test_production_m3_connections_need_admin(api_client, engine, params, ref) -> None:  # noqa: ANN001
    c, _ = api_client
    cid = _create(c, f"prod-{engine}", engine, params, secret_ref=ref, tags=["production"])
    url = f"/api/v1/connections/{cid}"
    for body in (
        {"params": {**params, "host": "attacker.example"}},
        {"name": f"prod-{engine}-2"},
        {"tags": []},
    ):
        r = c.patch(url, json=body, headers=JOBS)
        assert r.status_code == 403 and r.json()["code"] == "admin_scope_required", body
        assert "attacker" not in r.text
    gone = c.delete(url, headers=JOBS)
    assert gone.status_code == 403 and gone.json()["code"] == "admin_scope_required"
    assert c.get(url, headers=JOBS).json()["params"]["host"] == params["host"]
    assert c.delete(url, headers=FULL).status_code == 204


ENDPOINT_CHANGES = [
    ("oracle", ORA, {"host": "ora2.example"}),
    ("oracle", ORA, {"port": 2484}),
    ("oracle", ORA, {"service_name": "OTHERPDB"}),
    ("oracle", ORA, {"options": {"protocol": "tcp"}}),
    ("mongo", MDB, {"host": "mongo2.example"}),
    ("mongo", MDB, {"database": "other"}),
    ("mongo", MDB, {"options": {"directConnection": "true", "replicaSet": "rs9"}}),
    ("mongo", MDB, {"options": {"directConnection": "true", "tls": "false"}}),
]


@pytest.mark.parametrize(("engine", "params", "change"), ENDPOINT_CHANGES)
def test_an_m3_endpoint_change_needs_credentials(api_client, engine, params, change) -> None:  # noqa: ANN001
    c, _ = api_client
    start = (
        {**params, "options": {**params.get("options", {}), "protocol": "tcps"}}
        if engine == "oracle"
        else params
    )
    cid = _create(
        c, f"dev-{engine}", engine, start,
        credentials={"username": "u", "password": "pw-old-distinct"}, headers=JOBS,
    )  # fmt: skip
    url = f"/api/v1/connections/{cid}"
    moved = c.patch(url, json={"params": {**start, **change}}, headers=JOBS)
    assert moved.status_code == 403, change
    assert moved.json()["code"] == "connection_endpoint_change_requires_credentials"
    restated = c.patch(
        url,
        json={
            "params": {**start, **change},
            "credentials": {"username": "u", "password": "pw-new"},
        },
        headers=JOBS,
    )
    assert restated.status_code == 200 and "pw-new" not in restated.text, change
    # a harmless change needs nothing
    renamed = c.patch(url, json={"name": f"dev-{engine}-2"}, headers=JOBS)
    assert renamed.status_code == 200


@pytest.mark.parametrize("ref", ["t2/dps/src/oracle-crm-ro", "../t2/dps/src/mongo-crm", "/abs/x"])
def test_an_m3_secret_ref_never_names_another_tenants_folder(api_client, ref) -> None:  # noqa: ANN001
    c, _ = api_client
    body = {
        "name": "x-ora",
        "engine": "oracle",
        "params": ORA,
        "secret_ref": ref,
        "roles": ["source"],
    }
    r = c.post("/api/v1/connections", json=body, headers=FULL)
    if ref.startswith("t2/"):  # another tenant's folder
        assert r.status_code == 403 and r.json()["code"] == "secret_ref_foreign_tenant"
    else:  # an escaping or absolute path never passes validation
        assert r.status_code == 422, (ref, r.status_code)
    assert "connection.create" not in r.text
