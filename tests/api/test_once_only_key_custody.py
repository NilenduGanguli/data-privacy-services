"""S3 review F-14: the key or seed DPS generates for a job or a job group is returned once — so the
row must not outlive an answer that never delivered it. A strict audit sink that refuses the creation
event (503 ``audit_unavailable``) used to leave a queued job sealed under a key nobody holds (its
``Idempotency-Key`` replay then returned the job without the key) and a group whose key was lost.
Now the unannounced job or group is removed before the 503, so a retry starts afresh."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from dps.app import create_app
from dps.audit import AuditEvent, AuditUnavailable, MemoryAuditSink
from dps.config import Settings
from tests.api.test_api_connections import JOBS
from tests.api.test_api_jobs import _connection, _run, seed_snapshot
from tests.fixtures.db import _truncate, api_settings

pytestmark = pytest.mark.db


class _RefusingSink(MemoryAuditSink):
    """Refuses the next event of each action in ``refuse`` (a full strict queue), records the rest."""

    def __init__(self, refuse: set[str]) -> None:
        super().__init__()
        self.refuse = set(refuse)

    def write(self, event: AuditEvent) -> None:
        if event.action in self.refuse and event.outcome == "ok":
            self.refuse.discard(event.action)
            raise AuditUnavailable()
        super().write(event)


def test_a_refused_creation_audit_leaves_no_job_and_no_group_behind(
    db_settings: Settings, tmp_path: Path
) -> None:
    sink = _RefusingSink({"job.create", "group.create"})
    try:
        with TestClient(create_app(api_settings(db_settings, tmp_path), audit_sink=sink)) as c:
            src = _connection(c, "bank-src", "dps/src/test-pg", ["source"], JOBS)
            uat = _connection(c, "bank-uat", "dps/src/test-pg-rw", ["target"], JOBS)
            snap = seed_snapshot(db_settings, "t1", src, "bank.customers")
            assert c.post(f"/api/v1/snapshots/{snap}/accept", headers=JOBS).status_code == 200
            table = {
                "write_mode": "table",
                "connection_id": uat,
                "object": {"owner": "bank_uat", "name": "customers"},
            }
            headers = {**JOBS, "Idempotency-Key": "custody-1"}
            first = c.post("/api/v1/jobs/run", json=_run(src, snap, table), headers=headers)
            assert (first.status_code, first.json()["code"]) == (503, "audit_unavailable")
            assert "key_material" not in first.text
            assert c.get("/api/v1/jobs", headers=JOBS).json()["items"] == []  # nothing queued
            again = c.post("/api/v1/jobs/run", json=_run(src, snap, table), headers=headers)
            assert again.status_code == 202, again.text  # a fresh job, and its key, this time
            assert again.json()["key_material"]["dek"]
            group = c.post("/api/v1/job-groups", json={"name": "custody"}, headers=JOBS)
            assert (group.status_code, group.json()["code"]) == (503, "audit_unavailable")
            assert c.get("/api/v1/job-groups", headers=JOBS).json()["items"] == []
            made = c.post("/api/v1/job-groups", json={"name": "custody"}, headers=JOBS)
            assert made.status_code == 201 and made.json()["key_material"]["dek"]
            rejected: list[Any] = [e for e in sink.events if e.outcome in ("error", "rejected")]
            assert {e.action for e in rejected} >= {"job.create", "group.create"}
    finally:
        asyncio.run(_truncate(db_settings))
