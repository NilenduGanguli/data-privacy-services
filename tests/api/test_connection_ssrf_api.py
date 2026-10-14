"""S3 review F-08 over HTTP: a ``jobs`` key cannot register, move, or later use a connection
whose endpoint is an SSRF target (cloud metadata, numeric encodings, …) — 422 ``host_forbidden``
at create/patch, and at use for a row stored before the rule (``/test`` used to dial it)."""

from __future__ import annotations

import asyncio
import json
from urllib.parse import urlparse

import asyncpg
import pytest

from tests.api.test_api_connections import JOBS, PG

pytestmark = pytest.mark.db
S3 = {"name": "uat-bucket", "engine": "s3", "roles": ["target"], "secret_ref": "dps/src/test-pg"}


def test_ssrf_endpoints_are_refused_at_create_patch_and_use(
    api_client, db_settings, pg_admin_dsn
) -> None:  # noqa: ANN001
    c, _ = api_client
    for params in (
        {"bucket": "uat", "endpoint_url": "https://169.254.169.254/"},
        {"bucket": "uat", "endpoint_url": "https://[::ffff:169.254.169.254]/"},
    ):
        r = c.post("/api/v1/connections", json={**S3, "params": params}, headers=JOBS)
        assert (r.status_code, r.json()["code"]) == (422, "host_forbidden"), r.text
        assert "169.254" not in r.text
    pg = {"name": "src", "engine": "postgres", "secret_ref": "dps/src/test-pg"}
    numeric = c.post(
        "/api/v1/connections", json={**pg, "params": {**PG, "host": "0x7f000001"}}, headers=JOBS
    )
    assert (numeric.status_code, numeric.json()["code"]) == (422, "host_forbidden")
    made = c.post("/api/v1/connections", json={**pg, "params": PG}, headers=JOBS)
    assert made.status_code == 201, made.text
    moved = c.patch(
        f"/api/v1/connections/{made.json()['id']}",
        json={"params": {**PG, "host": "169.254.169.254"}, "secret_ref": "dps/src/test-pg"},
        headers=JOBS,
    )
    assert (moved.status_code, moved.json()["code"]) == (422, "host_forbidden")
    # a row stored before the rule (written behind the API): refused at use, nothing dialled
    bucket = c.post(
        "/api/v1/connections",
        json={**S3, "params": {"bucket": "uat", "endpoint_url": "https://s3.bank.internal/"}},
        headers=JOBS,
    )
    assert bucket.status_code == 201, bucket.text
    _set_params(
        pg_admin_dsn,
        db_settings.pg_database,
        bucket.json()["id"],
        {"bucket": "uat", "endpoint_url": "https://169.254.169.254/"},
    )
    used = c.post(
        f"/api/v1/connections/{bucket.json()['id']}/test", json={"role": "target"}, headers=JOBS
    )
    assert (used.status_code, used.json()["code"]) == (422, "host_forbidden"), used.text


def _set_params(admin_dsn: str, database: str, connection_id: str, params: dict[str, str]) -> None:
    async def run() -> None:
        dsn = urlparse(admin_dsn)._replace(path=f"/{database}").geturl()
        conn = await asyncpg.connect(dsn)
        try:
            await conn.execute(
                "UPDATE dps.dps_connection SET params = $2::jsonb WHERE id = $1",
                connection_id,
                json.dumps(params),
            )
        finally:
            await conn.close()

    asyncio.run(run())
