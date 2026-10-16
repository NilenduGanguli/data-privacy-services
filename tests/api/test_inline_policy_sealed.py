"""S3 review F-18: an inline policy's body (deny/allow-list terms are often the sensitive terms
themselves — named individuals a privacy officer always masks) is sealed with the job KEK in
``spec_enc``, like selection literals; ``dps_job.spec`` and every ``GET /jobs*`` keep a value-free
label (name, version, sha256). The worker gets the body back from the sealed blob."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import asyncpg
import pytest

from dps.jobs import JobInternal
from dps.layers.connectors.jobs_run import _spec
from tests.api.test_api_connections import JOBS
from tests.api.test_api_jobs import _run, seeded  # noqa: F401 - the fixture
from tests.fixtures.db import TEST_KEK

pytestmark = pytest.mark.db
SENTINEL = "Zebulon Quixote"
POLICY: dict[str, Any] = {
    "preset": "gdpr-pseudonymise",
    "name": "vip-masking",
    "detect": {
        "custom_recognizers": [
            {"name": "vips", "supported_entity": "VIP_NAME", "deny_list": [SENTINEL]}
        ]
    },
}


def _row(settings: Any, job_id: str) -> dict[str, Any]:  # noqa: ANN401
    async def run() -> dict[str, Any]:
        conn = await asyncpg.connect(
            host=settings.pg_host,
            port=settings.pg_port,
            user=settings.pg_user,
            password=settings.pg_password,
            database=settings.pg_database,
        )
        try:
            await conn.execute("SELECT set_config('app.tenant_id', 't1', false)")
            row = await conn.fetchrow(
                "SELECT spec::text AS spec, spec_enc FROM dps.dps_job WHERE id = $1", job_id
            )
            assert row is not None
            return dict(row)
        finally:
            await conn.close()

    return asyncio.run(run())


def test_an_inline_policy_never_rests_in_clear_in_a_job_row(seeded) -> None:  # noqa: ANN001, F811
    c, settings = seeded["c"], seeded["settings"]
    table = {
        "write_mode": "table",
        "connection_id": seeded["uat"],
        "object": {"owner": "bank_uat", "name": "customers"},
    }
    run = c.post(
        "/api/v1/jobs/run",
        json=_run(seeded["src"], seeded["snap_src"], table, policy=POLICY),
        headers=JOBS,
    )
    assert run.status_code == 202, run.text
    discover = c.post(
        "/api/v1/jobs/discover",
        json={
            "connection_id": seeded["src"],
            "objects": [{"owner": "bank", "name": "customers"}],
            "policy": POLICY,
        },
        headers=JOBS,
    )
    assert discover.status_code == 202, discover.text
    for job_id in (run.json()["job"]["id"], discover.json()["job"]["id"]):
        assert SENTINEL not in run.text + discover.text
        assert SENTINEL not in c.get(f"/api/v1/jobs/{job_id}", headers=JOBS).text
        row = _row(settings, job_id)
        assert SENTINEL not in row["spec"] and row["spec_enc"] is not None
        label = json.loads(row["spec"])["policy"]
        assert label["sealed"] is True and label["name"] == "vip-masking" and label["sha256"]
    assert SENTINEL not in c.get("/api/v1/jobs", headers=JOBS).text
    # the worker reopens the body from the sealed blob
    job_id = run.json()["job"]["id"]
    stored = _row(settings, job_id)
    job = SimpleNamespace(id=job_id, spec=json.loads(stored["spec"]), spec_enc=stored["spec_enc"])
    spec = _spec(SimpleNamespace(kek=TEST_KEK), job)  # type: ignore[arg-type]
    assert isinstance(spec.policy, dict)
    assert spec.policy["detect"]["custom_recognizers"][0]["deny_list"] == [SENTINEL]
    assert JobInternal  # the worker's own type carries the same two fields
