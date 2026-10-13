"""Admin API (M5 Task 11): keys, tenants, audit search with CSV export, queue health — admin only,
value-free, every mutation audited."""

from __future__ import annotations

import asyncio
import csv
import functools
import io
import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from dps import db
from dps.auth import Principal, hash_key
from dps.auth_db import create_key
from dps.config import Settings
from tests.fixtures.db import _truncate, api_settings

pytestmark = pytest.mark.db
ROOT = Principal(key_id="root", name="root", tenant_ids=["*"], scopes=["*"])
ROUTES = [
    ("GET", "/api/v1/admin/keys", None),
    ("POST", "/api/v1/admin/keys", {}),
    ("POST", "/api/v1/admin/keys/key_x/rotate", {}),
    ("POST", "/api/v1/admin/keys/key_x/disable", None),
    ("POST", "/api/v1/admin/keys/key_x/enable", None),
    ("GET", "/api/v1/admin/tenants", None),
    ("POST", "/api/v1/admin/tenants", {}),
    ("PATCH", "/api/v1/admin/tenants/t_x", {}),
    ("GET", "/api/v1/admin/audit", None),
    ("GET", "/api/v1/admin/queue", None),
]


class Admin:
    def __init__(self, client: Any, settings: Settings) -> None:
        self.client, self.settings = client, settings

    def call(self, fn: Any, *args: Any, **kwargs: Any) -> Any:
        """Run a coroutine function on the app's event loop and pool (TestClient's portal)."""
        return self.client.portal.call(functools.partial(fn, *args, **kwargs))

    def mint(self, name: str, tenants: list[str], scopes: list[str]) -> dict[str, str]:
        created = self.call(
            create_key,
            name=name,
            tenant_ids=tenants,
            scopes=scopes,
            expires_in_days=30,
            actor=ROOT,
            settings=self.settings,
        )
        self.call(self.client.app.state.db_keys.refresh)
        return {"X-API-KEY": created.key}


@pytest.fixture
def admin(
    db_settings: Settings, tmp_path: Path
) -> Iterator[tuple[Admin, dict[str, str], dict[str, str]]]:
    from fastapi.testclient import TestClient

    from dps.app import create_app

    settings = api_settings(db_settings, tmp_path, bootstrap_api_key="")
    try:
        with TestClient(create_app(settings)) as client:
            a = Admin(client, settings)
            yield a, a.mint("root-admin", ["*"], ["*"]), a.mint("detect-only", ["a"], ["detect"])
    finally:
        asyncio.run(_truncate(db_settings))


async def _insert_audit(rows: list[dict[str, Any]]) -> None:
    for r in rows:
        async with db.acquire(r["tenant_id"]) as conn:
            await conn.execute(
                f"INSERT INTO {db.schema_ident()}.dps_audit_log (ts, tenant_id, key_id, route, "
                "action, mode, policy, outcome, status, job_id, counts) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11::jsonb)",
                r["ts"],
                r["tenant_id"],
                r.get("key_id", "static-x"),
                r.get("route", "/api/v1/obfuscate"),
                r["action"],
                r.get("mode"),
                r.get("policy"),
                r["outcome"],
                r.get("status", 200),
                r.get("job_id"),
                r.get("counts", {}),
            )


async def _audit_rows(tenant: str) -> list[dict[str, Any]]:
    async with db.acquire(tenant) as conn:
        rows = await conn.fetch(
            f"SELECT action, outcome, counts FROM {db.schema_ident()}.dps_audit_log ORDER BY id"
        )
    return [dict(r) for r in rows]


def test_every_admin_route_requires_admin(admin) -> None:  # noqa: ANN001
    a, _, detect = admin
    for method, path, body in ROUTES:
        kwargs = {"json": body} if body is not None else {}
        assert a.client.request(method, path, headers=detect, **kwargs).status_code == 403, path
        assert a.client.request(method, path, **kwargs).status_code == 401, path


def test_key_lifecycle_over_http(admin) -> None:  # noqa: ANN001
    a, root, _ = admin
    c = a.client
    r = c.post(
        "/api/v1/admin/keys",
        json={"name": "kyc", "tenant_ids": ["a"], "scopes": ["detect"], "expires_in_days": 30},
        headers=root,
    )
    assert r.status_code == 201 and r.headers["cache-control"] == "no-store"
    raw, key_id = r.json()["key"], r.json()["record"]["id"]
    assert raw.startswith("dps_")
    listed = c.get("/api/v1/admin/keys", headers=root)
    assert listed.status_code == 200 and listed.json()["next_cursor"] is None
    assert any(k["id"] == key_id for k in listed.json()["items"])
    assert raw not in listed.text and hash_key(raw) not in listed.text
    assert any(k["source"] == "static" for k in listed.json()["items"])
    new_hdr = {"X-API-KEY": raw}
    assert c.post("/api/v1/detect", json={"text": "x"}, headers=new_hdr).status_code == 200
    rot = c.post(f"/api/v1/admin/keys/{key_id}/rotate", json={"grace_hours": 0}, headers=root)
    assert rot.status_code == 201 and rot.headers["cache-control"] == "no-store"
    assert rot.json()["old"]["status"] == "disabled" and "key" not in rot.json()["old"]
    rotated = {"X-API-KEY": rot.json()["new"]["key"]}
    new_id = rot.json()["new"]["record"]["id"]
    assert c.post("/api/v1/detect", json={"text": "x"}, headers=new_hdr).status_code == 401
    assert c.post("/api/v1/detect", json={"text": "x"}, headers=rotated).status_code == 200
    off = c.post(f"/api/v1/admin/keys/{new_id}/disable", headers=root)
    assert off.status_code == 200 and off.json()["status"] == "disabled"
    assert c.post("/api/v1/detect", json={"text": "x"}, headers=rotated).status_code == 401
    on = c.post(f"/api/v1/admin/keys/{new_id}/enable", headers=root)
    assert on.status_code == 200 and on.json()["status"] == "active"
    assert c.post("/api/v1/detect", json={"text": "x"}, headers=rotated).status_code == 200
    again = c.post(f"/api/v1/admin/keys/{key_id}/enable", headers=root)
    assert again.status_code == 409 and again.json()["code"] == "key_rotated"
    too_long = c.post(
        f"/api/v1/admin/keys/{new_id}/rotate", json={"grace_hours": 999}, headers=root
    )
    assert too_long.status_code == 422
    missing = c.post("/api/v1/admin/keys/key_nope/disable", headers=root)
    assert missing.status_code == 404 and missing.json()["code"] == "key_not_found"


def test_a_tenant_admin_cannot_escalate_over_http(admin) -> None:  # noqa: ANN001
    a, _, _ = admin
    tenant_admin = a.mint("tenant-admin", ["a"], ["admin", "detect"])
    r = a.client.post(
        "/api/v1/admin/keys",
        json={"name": "wide", "tenant_ids": ["*"], "scopes": ["detect"], "expires_in_days": 5},
        headers=tenant_admin,
    )
    assert r.status_code == 403 and r.json()["code"] == "tenant_escalation"
    r = a.client.post(
        "/api/v1/admin/keys",
        json={"name": "wide", "tenant_ids": ["a"], "scopes": ["reveal"], "expires_in_days": 5},
        headers=tenant_admin,
    )
    assert r.status_code == 403 and r.json()["code"] == "scope_escalation"
    listed = a.client.get("/api/v1/admin/keys", headers=tenant_admin).json()["items"]
    assert all("*" not in k["tenant_ids"] for k in listed)


def test_tenant_crud_over_http(admin) -> None:  # noqa: ANN001
    a, root, _ = admin
    c = a.client
    r = c.post(
        "/api/v1/admin/tenants",
        json={
            "tenant_id": "cards-in",
            "daily_row_quota": 20_000_000,
            "registered_hosts": ["https://cards-apply-uat.bank.internal"],
        },
        headers=root,
    )
    assert r.status_code == 201, r.text
    assert r.json()["effective"]["registered_hosts"] == ["cards-apply-uat.bank.internal"]
    p = c.patch("/api/v1/admin/tenants/cards-in", json={"retention_days": 45}, headers=root)
    assert p.status_code == 200 and p.json()["effective"]["retention_days"] == 45
    listed = c.get("/api/v1/admin/tenants", headers=root)
    [row] = [t for t in listed.json()["items"] if t["tenant_id"] == "cards-in"]
    assert row["usage"]["rows_today"] == 0 and listed.json()["next_cursor"] is None
    unknown = c.patch("/api/v1/admin/tenants/nobody", json={"note": "x"}, headers=root)
    assert unknown.status_code == 404 and unknown.json()["code"] == "tenant_not_found"
    dup = c.post("/api/v1/admin/tenants", json={"tenant_id": "cards-in"}, headers=root)
    assert dup.status_code == 409 and dup.json()["code"] == "tenant_exists"
    bad = c.post(
        "/api/v1/admin/tenants",
        json={"tenant_id": "x1", "registered_hosts": ["https://user:secretpw@host"]},
        headers=root,
    )
    assert bad.status_code == 422 and "secretpw" not in bad.text


def _seed_audit(a: Admin) -> list[dict[str, Any]]:
    from dps.tenants import TenantSpec, create_tenant

    for tenant in ("a", "b"):  # "*" expands to the tenant directory (records, jobs, connections)
        a.call(create_tenant, TenantSpec(tenant_id=tenant), ROOT, a.settings)
    now = datetime.now(UTC)
    specs = [
        ("a", "obfuscate", "ok"),
        ("a", "obfuscate", "verification_failed"),
        ("a", "job.create", "ok"),
        ("a", "job.cancel", "rejected"),
        ("a", "scan.navigate", "ok"),
        ("a", "admin.key.create", "ok"),
        ("b", "obfuscate", "ok"),
        ("b", "job.create", "ok"),
        ("a", "obfuscate", "ok"),
        ("a", "detect", "ok"),
        ("b", "detect", "rejected"),
        ("a", "detect", "ok"),
    ]
    rows = [
        {
            "ts": now - timedelta(seconds=i),
            "tenant_id": tenant,
            "action": action,
            "outcome": outcome,
            "job_id": f"j_{i:02d}" if action.startswith("job.") else None,
            "counts": {"findings_total": 2, "methods_used": {"fpe_digits": 1, "email": 1}},
        }
        for i, (tenant, action, outcome) in enumerate(specs)
    ]
    a.call(_insert_audit, rows)
    return rows


def test_audit_outcome_groups_cover_job_finalize_outcomes(admin) -> None:  # noqa: ANN001
    """``job.finalize`` rows carry the job's outcome (``succeeded``, ``dry_run``, ``failed``,
    ``canceled``, ``dead``): the console's OK / Failed / Blocked filters must find them (found by
    the M5 console walk: "OK" hid every finished job)."""
    a, root, _ = admin
    from dps.tenants import TenantSpec, create_tenant

    a.call(create_tenant, TenantSpec(tenant_id="a"), ROOT, a.settings)
    now = datetime.now(UTC)
    outcomes = ["succeeded", "dry_run", "failed", "dead", "canceled", "replayed"]
    a.call(
        _insert_audit,
        [
            {"ts": now - timedelta(seconds=i), "tenant_id": "a", "action": "job.finalize",
             "outcome": o, "job_id": f"j_{i:02d}", "counts": {}}
            for i, o in enumerate(outcomes)
        ],
    )  # fmt: skip
    hdr = {**root, "X-DPS-Tenant": "a"}

    def found(group: str) -> set[str]:
        r = a.client.get("/api/v1/admin/audit", params={"outcome": group}, headers=hdr)
        assert r.status_code == 200, r.text
        return {i["outcome"] for i in r.json()["items"]}

    assert found("ok") == {"succeeded", "dry_run", "replayed"}
    assert found("failed") == {"failed", "dead"}
    assert found("blocked") == {"canceled"}


def test_audit_filters_paging_and_tenants(admin) -> None:  # noqa: ANN001
    a, root, _ = admin
    c = a.client
    rows = _seed_audit(a)
    a_hdr = {**root, "X-DPS-Tenant": "a"}

    def items(**params: Any) -> list[dict[str, Any]]:
        r = c.get("/api/v1/admin/audit", params=params, headers=a_hdr)
        assert r.status_code == 200, r.text
        return list(r.json()["items"])

    assert {i["action"] for i in items(layer="conn")} == {"job.create", "job.cancel"}
    assert {i["layer"] for i in items(layer="ui")} == {"ui"}
    assert [i["outcome"] for i in items(outcome="failed")] == ["verification_failed"]
    assert {i["outcome"] for i in items(outcome="blocked")} == {"rejected"}
    assert [i["job_id"] for i in items(q="j_02")] == ["j_02"]
    assert {i["action"] for i in items(action="job")} == {"job.create", "job.cancel"}
    first = c.get("/api/v1/admin/audit", params={"limit": 5, "tenants": "*"}, headers=a_hdr).json()
    second = c.get(
        "/api/v1/admin/audit",
        params={"limit": 5, "tenants": "*", "cursor": first["next_cursor"]},
        headers=a_hdr,
    ).json()
    third = c.get(
        "/api/v1/admin/audit",
        params={"limit": 5, "tenants": "*", "cursor": second["next_cursor"]},
        headers=a_hdr,
    ).json()
    pages = [first["items"], second["items"], third["items"]]
    assert [len(p) for p in pages] == [5, 5, 2] and third["next_cursor"] is None
    seen = [(i["ts"], i["id"]) for p in pages for i in p]
    assert len(set(seen)) == len(rows) and seen == sorted(seen, reverse=True)
    assert {i["tenant_id"] for p in pages for i in p} == {"a", "b"}
    assert all(
        i["findings"] == 2 and i["methods"] == {"fpe_digits": 1, "email": 1}
        for p in pages
        for i in p
    )
    limited = a.mint("a-admin", ["a"], ["admin"])
    forbidden = c.get("/api/v1/admin/audit", params={"tenants": "zzz"}, headers=limited)
    assert forbidden.status_code == 403 and forbidden.json()["code"] == "tenant_forbidden"
    mine = c.get("/api/v1/admin/audit", params={"tenants": "*"}, headers=limited).json()["items"]
    assert mine and {i["tenant_id"] for i in mine} == {"a"}
    bad = c.get("/api/v1/admin/audit", params={"cursor": "%%%"}, headers=a_hdr)
    assert bad.status_code == 422 and bad.json()["code"] == "invalid_cursor"
    window = c.get(
        "/api/v1/admin/audit",
        params={"from": "2020-01-01T00:00:00Z", "to": "2026-01-01T00:00:00Z"},
        headers=a_hdr,
    )
    assert window.status_code == 422 and window.json()["code"] == "invalid_window"


def test_audit_csv_is_streamed_and_formula_safe(admin) -> None:  # noqa: ANN001
    a, root, _ = admin
    now = datetime.now(UTC)
    a.call(
        _insert_audit,
        [
            {
                "ts": now,
                "tenant_id": "a",
                "action": "obfuscate",
                "outcome": "ok",
                "route": '=HYPERLINK("x")',
                "counts": {"methods_used": {"fpe_digits": 3, "email": 1}},
            }
        ],
    )
    r = a.client.get(
        "/api/v1/admin/audit", params={"format": "csv"}, headers={**root, "X-DPS-Tenant": "a"}
    )
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert "dps-audit-" in r.headers["content-disposition"]
    lines = list(csv.reader(io.StringIO(r.text)))
    assert lines[0] == [
        "ts",
        "tenant_id",
        "key_name",
        "key_id",
        "route",
        "action",
        "layer",
        "mode",
        "policy",
        "findings",
        "methods",
        "outcome",
        "status",
        "job_id",
    ]
    [row] = [line for line in lines[1:] if line[5] == "obfuscate"]
    assert row[4] == '\'=HYPERLINK("x")' and row[10] == "email=1;fpe_digits=3"


def test_audit_never_contains_values(admin) -> None:  # noqa: ANN001
    a, root, _ = admin
    c = a.client
    hdr = {**root, "X-DPS-Tenant": "a"}
    r = c.post(
        "/api/v1/obfuscate",
        json={"data": {"ssn": "123-45-6789", "name": "Zebulon Quixote"}},
        headers=hdr,
    )
    assert r.status_code == 200, r.text
    for _ in range(50):  # the DB sink writes in the background
        found = c.get("/api/v1/admin/audit", params={"action": "obfuscate"}, headers=hdr).json()[
            "items"
        ]
        if found:
            break
        a.call(asyncio.sleep, 0.1)
    assert found
    as_json = c.get("/api/v1/admin/audit", headers=hdr).text
    as_csv = c.get("/api/v1/admin/audit", params={"format": "csv"}, headers=hdr).text
    for text in (as_json, as_csv):
        assert "123-45-6789" not in text and "Zebulon" not in text and "Quixote" not in text


def test_admin_actions_are_audited(admin) -> None:  # noqa: ANN001
    a, root, _ = admin
    hdr = {**root, "X-DPS-Tenant": "a"}
    r = a.client.post(
        "/api/v1/admin/keys",
        json={"name": "audited", "tenant_ids": ["a"], "scopes": ["detect"], "expires_in_days": 5},
        headers=hdr,
    )
    assert r.status_code == 201
    for _ in range(50):
        rows = [x for x in a.call(_audit_rows, "a") if x["action"] == "admin.key.create"]
        if rows:
            break
        a.call(asyncio.sleep, 0.1)
    assert rows and rows[0]["outcome"] == "ok"
    assert "dps_" not in json.dumps([x["counts"] for x in rows])


def test_queue_endpoint_shape(admin) -> None:  # noqa: ANN001
    a, root, _ = admin
    r = a.client.get("/api/v1/admin/queue", headers=root)
    assert r.status_code == 200 and set(r.json()) == {"queues", "workers"}
