"""S7: every object route answers 404 for another tenant's id, and a key cannot select a tenant
it is not granted (403). Tenant ``ta`` owns a connection, an accepted snapshot, a queued run job
(with partitions route), a job group, a policy with a version, and an evidence pack with its
artefacts; a key granted only ``tb`` — with every scope — sees none of them."""

from __future__ import annotations

import asyncio
import dataclasses
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import asyncpg
import pytest
from fastapi.testclient import TestClient

from dps.app import create_app
from dps.audit import MemoryAuditSink
from dps.config import Settings
from dps.layers.connectors.evidence import build_pack, save_evidence
from dps.storage import ArtefactService, LocalObjectStore
from tests.api.test_api_connections import PG
from tests.api.test_api_jobs import seed_snapshot
from tests.connectors.factories import evidence_inputs
from tests.fixtures.db import _truncate, api_settings

pytestmark = pytest.mark.db

KEYS = json.dumps(
    [
        {"name": "ta-all", "key": "dps_ta_all_test_only", "tenant_ids": ["ta"], "scopes": ["*"]},
        {"name": "tb-all", "key": "dps_tb_all_test_only", "tenant_ids": ["tb"], "scopes": ["*"]},
    ]
)
TA = {"X-API-KEY": "dps_ta_all_test_only", "X-DPS-Tenant": "ta"}
TB = {"X-API-KEY": "dps_tb_all_test_only", "X-DPS-Tenant": "tb"}
PURPOSE = {"ticket": "CHG-48213", "legal_basis": "legitimate interest"}


def _save_evidence(settings: Settings, artefact_dir: Path, tenant: str, job_id: str) -> Any:  # noqa: ANN401
    """Store an evidence pack for ``job_id`` on its own connection (the app's pools belong to the
    client's event loop)."""

    async def run() -> Any:  # noqa: ANN401
        conn = await asyncpg.connect(
            host=settings.pg_host,
            port=settings.pg_port,
            user=settings.pg_user,
            password=settings.pg_password,
            database=settings.pg_database,
        )
        try:
            for typ in ("json", "jsonb"):
                await conn.set_type_codec(
                    typ, encoder=json.dumps, decoder=json.loads, schema="pg_catalog"
                )

            @asynccontextmanager
            async def acquire(tenant_id: str | None) -> AsyncIterator[asyncpg.Connection]:
                await conn.execute("SELECT set_config('app.tenant_id', $1, false)", tenant_id)
                yield conn

            inputs = evidence_inputs(tenant=tenant)
            inputs = dataclasses.replace(
                inputs, job=inputs.job.model_copy(update={"id": job_id, "tenant_id": tenant})
            )
            artefacts = ArtefactService(LocalObjectStore(artefact_dir), acquire=acquire)
            return await save_evidence(
                build_pack(inputs), artefacts=artefacts, acquire=acquire, expires_at=None
            )
        finally:
            await conn.close()

    return asyncio.run(run())


def test_object_routes_answer_404_across_tenants(db_settings: Settings, tmp_path: Path) -> None:
    settings = api_settings(db_settings, tmp_path, api_keys=KEYS)
    try:
        with TestClient(create_app(settings, audit_sink=MemoryAuditSink())) as c:
            creds = {"username": "u", "password": "pw-distinct-1"}

            def connection(name: str, roles: list[str]) -> str:
                body = {"name": name, "engine": "postgres", "params": PG, "credentials": creds}
                r = c.post("/api/v1/connections", json={**body, "roles": roles}, headers=TA)
                assert r.status_code == 201, r.text
                return str(r.json()["id"])

            src, uat = connection("ta-src", ["source"]), connection("ta-uat", ["target"])
            snap = seed_snapshot(db_settings, "ta", src, "bank.customers")
            assert c.post(f"/api/v1/snapshots/{snap}/accept", headers=TA).status_code == 200
            run = {
                "mode": "obfuscate",
                "snapshot_id": snap,
                "source": {"connection_id": src, "object": {"owner": "bank", "name": "customers"}},
                "target": {
                    "write_mode": "table",
                    "connection_id": uat,
                    "object": {"owner": "bank_uat", "name": "customers"},
                },
                "purpose": PURPOSE,
            }
            job = c.post("/api/v1/jobs/run", json=run, headers=TA)
            assert job.status_code == 202, job.text
            job_id = job.json()["job"]["id"]
            group = c.post("/api/v1/job-groups", json={"name": "ta-refresh"}, headers=TA)
            assert group.status_code == 201, group.text
            group_id = group.json()["group"]["id"]
            policy = c.post(
                "/api/v1/policies",
                json={"name": "ta-policy", "base_preset": "gdpr-pseudonymise"},
                headers=TA,
            )
            assert policy.status_code == 201, policy.text
            record = _save_evidence(db_settings, Path(settings.artefact_dir), "ta", job_id)

            owned = [
                ("GET", f"/api/v1/connections/{src}"),
                ("PATCH", f"/api/v1/connections/{src}"),
                ("DELETE", f"/api/v1/connections/{src}"),
                ("POST", f"/api/v1/connections/{src}/test"),
                ("GET", f"/api/v1/jobs/{job_id}"),
                ("GET", f"/api/v1/jobs/{job_id}/partitions"),
                ("POST", f"/api/v1/jobs/{job_id}/cancel"),
                ("GET", f"/api/v1/snapshots/{snap}"),
                ("POST", f"/api/v1/snapshots/{snap}/accept"),
                ("GET", f"/api/v1/job-groups/{group_id}"),
                ("GET", f"/api/v1/evidence/{record.id}"),
                ("GET", f"/api/v1/evidence/{record.id}/html"),
                ("GET", f"/api/v1/evidence/{record.id}/pdf"),
                ("GET", f"/api/v1/artefacts/{record.json_artefact_id}"),
                ("GET", "/api/v1/policies/ta-policy"),
                ("GET", "/api/v1/policies/ta-policy/versions/1"),
            ]
            for method, path in owned:  # the owner sees them (the walk is meaningful)
                if method == "GET":
                    assert c.get(path, headers=TA).status_code == 200, path
            for method, path in owned:
                body = {"name": "x-pg"} if method == "PATCH" else {}
                r = c.request(method, path, json=body, headers=TB)
                assert r.status_code == 404, f"{method} {path} answered {r.status_code}"
            listed = c.get("/api/v1/jobs", headers=TB).json()["items"]
            assert all(item["id"] != job_id for item in listed)
            assert (
                c.get(
                    f"/api/v1/connections/{src}", headers={**TB, "X-DPS-Tenant": "ta"}
                ).status_code
                == 403
            )
            assert c.get(f"/api/v1/connections/{src}", headers=TA).status_code == 200  # untouched
    finally:
        asyncio.run(_truncate(db_settings))
