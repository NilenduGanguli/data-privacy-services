"""The M2 flow through HTTP only, with the embedded worker: connect, count, discover, review,
run into a UAT table, then read the evidence and its artefacts (contract §9, spec §11 M2)."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import asyncpg
import pytest
from fastapi.testclient import TestClient

from dps.app import create_app
from dps.audit import MemoryAuditSink
from dps.config import Settings
from dps.engine.transform import fpe
from tests.api.test_api_connections import JOBS, PG
from tests.fixtures.db import _truncate, api_settings
from tests.fixtures.sources import SourcePgSync

pytestmark = [pytest.mark.db, pytest.mark.pg_source, pytest.mark.integration, pytest.mark.slow]


@pytest.fixture
def worker_client(db_settings: Settings, tmp_path: Path) -> Iterator[TestClient]:
    """An app on the test database with the embedded worker (2 tasks, fast polling)."""
    if not fpe.fpe_available():
        pytest.skip("the [fpe] extra is not installed: integer keys need FF1")
    settings = api_settings(
        db_settings,
        tmp_path,
        embedded_worker=True,
        worker_concurrency=2,
        job_poll_interval_seconds=0.2,
        job_reaper_interval_seconds=1.0,
        fpe_enabled=True,
        ner_enabled=False,
    )
    try:
        with TestClient(create_app(settings, audit_sink=MemoryAuditSink())) as client:
            yield client
    finally:
        asyncio.run(_truncate(db_settings))


def _poll(c: TestClient, job_id: str, *, timeout: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while True:
        job = c.get(f"/api/v1/jobs/{job_id}", headers=JOBS).json()
        if job["state"] in ("succeeded", "failed", "dead", "canceled"):
            return job
        assert time.monotonic() < deadline, f"job still {job['state']}/{job['phase']}"
        time.sleep(0.3)


def _names(dsn: str, schema: str) -> list[str]:
    async def run() -> list[str]:
        conn = await asyncpg.connect(dsn)
        try:
            rows = await conn.fetch(f'SELECT full_name, email FROM "{schema}".customers')
        finally:
            await conn.close()
        return [v for r in rows for v in (r["full_name"], r["email"]) if v]

    return asyncio.run(run())


def test_the_whole_flow_over_http(worker_client: TestClient, source_pg_sync: SourcePgSync) -> None:
    c = worker_client
    schema = source_pg_sync.schema
    bodies: list[str] = []

    def call(method: str, path: str, **kw: Any) -> Any:  # noqa: ANN401
        r = c.request(method, path, headers={**JOBS, **kw.pop("headers", {})}, **kw)
        bodies.append(r.text)
        return r

    src = call(
        "POST",
        "/api/v1/connections",
        json={"name": "src", "engine": "postgres", "params": PG, "secret_ref": "dps/src/test-pg"},
    ).json()["id"]
    uat = call(
        "POST",
        "/api/v1/connections",
        json={
            "name": "uat",
            "engine": "postgres",
            "params": PG,
            "secret_ref": "dps/src/test-pg-rw",
            "roles": ["target"],
        },
    ).json()["id"]
    obj = {"owner": schema, "name": "customers"}
    counted = call(
        "POST", f"/api/v1/connections/{src}/count", json={"object": obj, "selection": {}}
    )
    assert counted.status_code == 200 and counted.json()["rows"] == 200
    disc = call("POST", "/api/v1/jobs/discover", json={"connection_id": src, "objects": [obj]})
    assert disc.status_code == 202, disc.text
    done = _poll(c, disc.json()["job"]["id"], timeout=60)
    assert done["state"] == "succeeded", done.get("error_code")
    [snap_id] = done["progress"]["snapshot_ids"]
    snap = call("GET", f"/api/v1/snapshots/{snap_id}").json()
    assert snap["status"] == "proposed" and snap["object_ref"] == f"{schema}.customers"
    accepted = call("POST", f"/api/v1/snapshots/{snap_id}/accept")
    assert accepted.status_code == 200
    run = call(
        "POST",
        "/api/v1/jobs/run",
        json={
            "mode": "obfuscate",
            "snapshot_id": snap_id,
            "source": {"connection_id": src, "object": obj, "parallelism": 2, "batch_rows": 50},
            "target": {
                "write_mode": "table",
                "connection_id": uat,
                "object": {"owner": schema, "name": "customers_uat"},
            },
            "purpose": {"ticket": "CHG-1001", "legal_basis": "legitimate interest"},
        },
    )
    assert run.status_code == 202, run.text
    key = run.json()["key_material"]
    job = _poll(c, run.json()["job"]["id"], timeout=120)
    assert job["state"] == "succeeded", job.get("error_code")
    assert job["progress"]["rows_written"] == 200
    parts = call("GET", f"/api/v1/jobs/{job['id']}/partitions").json()["items"]
    assert parts and all(p["state"] == "done" for p in parts)
    listed = call("GET", f"/api/v1/evidence?job_id={job['id']}").json()["items"]
    assert len(listed) == 1 and listed[0]["passed"] is True and listed[0]["rows_written"] == 200
    ev = call("GET", f"/api/v1/evidence/{listed[0]['id']}").json()
    canonical = json.dumps(
        ev["pack"], sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    assert hashlib.sha256(canonical).hexdigest() == ev["pack_sha256"]
    html = call("GET", f"/api/v1/evidence/{listed[0]['id']}/html")
    assert html.status_code == 200 and html.headers["content-type"].startswith("text/html")
    assert ev["pack_sha256"] in html.text
    assert "default-src 'none'" in html.headers["content-security-policy"]
    kinds = {a["kind"]: a for a in ev["artefacts"]}
    assert {"evidence_json", "evidence_html"} <= set(kinds)
    raw = c.get(f"/api/v1/artefacts/{kinds['evidence_json']['id']}", headers=JOBS)
    assert raw.status_code == 200 and raw.headers["cache-control"] == "no-store"
    disposition = raw.headers["content-disposition"]
    assert disposition == f'attachment; filename="{kinds["evidence_json"]["id"]}.json"'
    assert hashlib.sha256(raw.content).hexdigest() == ev["pack_sha256"]
    stats = call("GET", "/api/v1/jobs/stats").json()
    assert stats["workers_online"] == 1
    for value in _names(source_pg_sync.dsn, schema):
        assert not any(value in body for body in bodies), "a source value reached a response"
    assert key["dek"] not in "".join(bodies[bodies.index(run.text) + 1 :])
