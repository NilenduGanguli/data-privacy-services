"""D3 through the API on PostgreSQL, Oracle and MongoDB: every write into a ``production``-tagged
connection needs ``admin``, whatever the write mode (S1-M3 post-merge verification r1, security B1).

The gate used to sit inside the ``in_place``/``append`` rule only: a ``jobs`` key could load a
``table`` (``truncate_insert`` emptied an existing production collection) or write ``files`` into a
production-tagged connection, and could restart an admin's failed production run through
``POST /jobs/{id}/retry``. No Oracle or MongoDB server is needed: validation, submission and retry
never connect to the source.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import asyncpg
import pytest

from dps import db
from dps.config import Settings
from dps.engine.policy import MethodSpec
from dps.snapshots import create_snapshot
from tests.api.test_api_connections import PG
from tests.api.test_api_connections_m3_security import MDB, ORA
from tests.connectors.factories import classified_columns, make_snapshot

pytestmark = pytest.mark.db
FULL = {"X-API-KEY": "dps_full", "X-DPS-Tenant": "t1"}
JOBS = {"X-API-KEY": "dps_jobs", "X-DPS-Tenant": "t1"}
PURPOSE = {"ticket": "CHG-48213", "legal_basis": "legitimate interest"}
#: engine, params, secret ref, object reference, source object, a copy's object; the UAT
#: target holds another, non-production credential (``UAT_REFS``)
#: A non-production credential per engine (validation only, never opened): a connection holding
#: the production connection's own credential is production too (D3 bound to the credentials)
UAT_REFS = {
    "postgres": "dps/src/test-pg",
    "oracle": "dps/tgt/oracle-crm-masked",
    "mongo": "dps/tgt/mongo-crm-uat",
}
ENGINES = [
    pytest.param(
        "postgres",
        PG,
        "dps/src/test-pg-rw",
        "bank.customers",
        {"owner": "bank", "name": "customers"},
        {"owner": "bank", "name": "customers_copy"},
        id="postgres",
    ),
    pytest.param(
        "oracle",
        ORA,
        "dps/src/oracle-crm-ro",
        "CRM.CUSTOMERS",
        {"owner": "CRM", "name": "CUSTOMERS"},
        {"owner": "CRM", "name": "CUSTOMERS_COPY"},
        id="oracle",
    ),
    pytest.param(
        "mongo",
        MDB,
        "dps/src/mongo-crm",
        "profiles",
        {"name": "profiles"},
        {"name": "profiles_copy"},
        id="mongo",
    ),
]


def _in_place_columns() -> list[Any]:
    """The test snapshot's columns, reviewed so every in-place output fits its column."""
    fits = MethodSpec(method="mask_partial")
    return [
        c.model_copy(update={"method": fits}) if c.name in ("ssn", "full_name") else c
        for c in classified_columns()
    ]


def _connect(settings: Settings, *, user: str | None = None) -> Any:
    return asyncpg.connect(
        host=settings.pg_host,
        port=settings.pg_port,
        user=user or settings.pg_user,
        password=user or settings.pg_password,
        database=settings.pg_database,
    )


def _accepted(c: Any, settings: Settings, connection_id: str, object_ref: str) -> str:
    """A proposed snapshot inserted on its own connection (the app's pools belong to the client's
    loop), then accepted through the API."""

    async def insert() -> str:
        conn = await _connect(settings)
        try:
            for typ in ("json", "jsonb"):
                await conn.set_type_codec(
                    typ, encoder=json.dumps, decoder=json.loads, schema="pg_catalog"
                )
            await conn.execute("SELECT set_config('app.tenant_id', 't1', false)")
            snap = make_snapshot(
                "t1",
                connection_id=connection_id,
                object_ref=object_ref,
                columns=_in_place_columns(),
            )
            await create_snapshot(snap, conn=conn)
            return snap.id
        finally:
            await conn.close()

    snap_id = asyncio.run(insert())
    accepted = c.post(f"/api/v1/snapshots/{snap_id}/accept", headers=FULL)
    assert accepted.status_code == 200, accepted.text
    return snap_id


def _fail(settings: Settings, job_id: str) -> None:
    """What a failed run leaves behind (its sealed key kept): the worker login's update."""

    async def run() -> None:
        conn = await _connect(settings, user="dps_worker_login")
        try:
            await conn.execute(
                f"UPDATE {db.schema_ident(settings)}.dps_job SET state = 'failed', "
                "finished_at = now() WHERE id = $1",
                job_id,
            )
        finally:
            await conn.close()

    asyncio.run(run())


def _create(c: Any, headers: dict[str, str], **body: Any) -> str:
    r = c.post("/api/v1/connections", json=body, headers=headers)
    assert r.status_code == 201, (r.status_code, r.json().get("code"))
    return str(r.json()["id"])


def _run(src: str, snap: str, source: dict[str, Any], target: dict[str, Any]) -> dict[str, Any]:
    return {
        "mode": "obfuscate",
        "snapshot_id": snap,
        "source": {"connection_id": src, "object": source},
        "target": target,
        "purpose": PURPOSE,
    }


@pytest.mark.parametrize(("engine", "params", "ref", "object_ref", "source", "copy"), ENGINES)
def test_every_write_into_a_production_connection_needs_admin(
    api_client: Any,
    db_settings: Settings,
    engine: str,
    params: dict[str, Any],
    ref: str,
    object_ref: str,
    source: dict[str, Any],
    copy: dict[str, Any],
) -> None:
    c, _ = api_client
    src = _create(
        c,
        JOBS,
        name=f"{engine}-src",
        engine=engine,
        params=params,
        secret_ref=ref,
        roles=["source"],
    )
    prod = _create(
        c,
        FULL,
        name=f"{engine}-prod",
        engine=engine,
        params=params,
        secret_ref=ref,
        roles=["target"],
        tags=["production"],
    )
    prod_pvc = _create(
        c,
        FULL,
        name="prod-exports",
        engine="pvc",
        params={"base_path": "exports"},
        roles=["target"],
        tags=["production"],
    )
    snap = _accepted(c, db_settings, src, object_ref)
    table = {"write_mode": "table", "connection_id": prod, "object": copy}
    targets = {
        "table truncate_insert": table,
        "table upsert": {**table, "table_load": "upsert"},
        "files": {"write_mode": "files", "connection_id": prod_pvc, "files": {"format": "jsonl"}},
    }
    before = c.get("/api/v1/jobs?kind=run", headers=FULL).json()["items"]
    for what, target in targets.items():
        body = _run(src, snap, source, target)
        for route in ("/api/v1/jobs/run/validate", "/api/v1/jobs/run"):
            refused = c.post(route, json=body, headers=JOBS)
            assert refused.status_code == 403, (what, route, refused.status_code)
            assert refused.json()["code"] == "admin_scope_required", (what, route)
        allowed = c.post("/api/v1/jobs/run/validate", json=body, headers=FULL)
        assert allowed.status_code == 200 and allowed.json() == {"ok": True}, (
            what,
            allowed.status_code,
            allowed.json().get("code"),
        )
    # no refused run left a job behind
    assert c.get("/api/v1/jobs?kind=run", headers=FULL).json()["items"] == before
    # the same jobs key may still write a non-production target (its own, non-production login)
    uat = _create(
        c,
        JOBS,
        name=f"{engine}-uat",
        engine=engine,
        params=params,
        secret_ref=UAT_REFS[engine],
        roles=["target"],
    )
    ok = c.post(
        "/api/v1/jobs/run/validate",
        json=_run(src, snap, source, {**table, "connection_id": uat}),
        headers=JOBS,
    )
    assert ok.status_code == 200 and ok.json() == {"ok": True}


@pytest.mark.parametrize(("engine", "params", "ref", "object_ref", "source", "copy"), ENGINES)
@pytest.mark.parametrize("write", ["in_place", "table"])
def test_retrying_a_production_write_needs_admin_again(
    api_client: Any,
    db_settings: Settings,
    engine: str,
    params: dict[str, Any],
    ref: str,
    object_ref: str,
    source: dict[str, Any],
    copy: dict[str, Any],
    write: str,
) -> None:
    c, _ = api_client
    # the jobs key names the credential before it is a production one (afterwards naming it
    # needs admin, S3 review F-01); reading it stays a jobs right
    reader = _create(
        c,
        JOBS,
        name=f"{engine}-src",
        engine=engine,
        params=params,
        secret_ref=ref,
        roles=["source"],
    )
    prod = _create(
        c,
        FULL,
        name=f"{engine}-prod",
        engine=engine,
        params=params,
        secret_ref=ref,
        roles=["source", "target"],
        tags=["production"],
    )
    if write == "in_place":
        src, target = prod, {"write_mode": "in_place", "confirm_write": True}
    else:
        src = reader
        target = {"write_mode": "table", "connection_id": prod, "object": copy}
    snap = _accepted(c, db_settings, src, object_ref)
    submitted = c.post("/api/v1/jobs/run", json=_run(src, snap, source, target), headers=FULL)
    assert submitted.status_code == 202, (submitted.status_code, submitted.json().get("code"))
    job_id = submitted.json()["job"]["id"]
    _fail(db_settings, job_id)
    refused = c.post(f"/api/v1/jobs/{job_id}/retry", headers=JOBS)
    assert refused.status_code == 403 and refused.json()["code"] == "admin_scope_required"
    assert c.get(f"/api/v1/jobs/{job_id}", headers=JOBS).json()["state"] == "failed"
    retried = c.post(f"/api/v1/jobs/{job_id}/retry", headers=FULL)
    assert retried.status_code == 200 and retried.json()["state"] == "queued"
