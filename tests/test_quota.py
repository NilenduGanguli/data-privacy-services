"""Daily row quotas (M5 Task 10): rows counted per UTC day at checkpoint (fenced), new runs
refused at accept once the quota is used, partitions of a tenant over quota left queued."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import asyncpg
import pytest

from dps import db, jobs
from dps.config import Settings
from dps.jobs import JobKind, JobPhase
from dps.layers.connectors.base import KeyRange

pytestmark = pytest.mark.db


async def _partitioned(tenant: str, n: int = 2) -> str:
    job = await jobs.create_job(tenant_id=tenant, kind=JobKind.run, phase=JobPhase.prepare, spec={})
    [claimed] = await jobs.claim_jobs("prep", kinds=(JobKind.run,), batch=1)
    assert claimed.id == job.id
    ranges = [KeyRange(lower=None, upper=None) for _ in range(n)]
    assert await jobs.add_partitions("prep", job.id, ranges) == n
    return job.id


async def _usage(tenant: str) -> int | None:
    async with db.acquire(tenant) as conn:
        value = await conn.fetchval(
            f"SELECT rows_written FROM {db.schema_ident()}.dps_tenant_usage WHERE tenant_id = $1 "
            "AND day = (now() AT TIME ZONE 'UTC')::date",
            tenant,
        )
    return None if value is None else int(value)


async def _checkpoint(worker: str, partition_id: int, rows: int) -> bool:
    return await jobs.checkpoint(
        worker,
        partition_id,
        last_key=None,
        batch_no=1,
        rows_read=rows,
        rows_written=rows,
        rows_skipped=0,
        values_by_method={},
        verify_checked=0,
    )


async def test_checkpoint_counts_rows_per_utc_day(migrated_db) -> None:  # noqa: ANN001
    await _partitioned("q1", 1)
    [part] = await jobs.claim_partitions("w1", batch=1)
    assert await _checkpoint("w1", part.id, 500)
    assert await _checkpoint("w1", part.id, 500)
    assert await _usage("q1") == 1000
    assert not await _checkpoint("intruder", part.id, 700)  # fenced out: counts nothing
    assert await _checkpoint("w1", part.id, 0)  # an empty batch writes no usage row change
    assert await _usage("q1") == 1000


async def test_claim_skips_partitions_of_a_tenant_over_quota(migrated_db) -> None:  # noqa: ANN001
    s = db.schema_ident()
    for tenant in ("q3", "q4"):
        await _partitioned(tenant, 2)
    async with db.acquire(None) as conn:
        await conn.execute(
            f"INSERT INTO {s}.dps_tenant (tenant_id, daily_row_quota) VALUES ('q3', 10) "
            "ON CONFLICT (tenant_id) DO UPDATE SET daily_row_quota = 10"
        )
    async with db.acquire("q3") as conn:
        await conn.execute(
            f"INSERT INTO {s}.dps_tenant_usage (tenant_id, day, rows_written) "
            "VALUES ('q3', (now() AT TIME ZONE 'UTC')::date, 10)"
        )
    claimed = await jobs.claim_partitions("w1", batch=10)
    assert claimed and {p.tenant_id for p in claimed} == {"q4"}
    async with db.acquire("q3") as conn:
        await conn.execute(f"DELETE FROM {s}.dps_tenant_usage WHERE tenant_id = 'q3'")
    again = await jobs.claim_partitions("w2", batch=10)
    assert {p.tenant_id for p in again} == {"q3"}


def _sql(settings: Settings, tenant: str | None, *statements: str) -> None:
    """Run statements as the app role on a connection of its own (the app's pools belong to the
    test client's event loop)."""

    async def run() -> None:
        conn = await asyncpg.connect(
            host=settings.pg_host,
            port=settings.pg_port,
            user=settings.pg_user,
            password=settings.pg_password,
            database=settings.pg_database,
        )
        try:
            if tenant is not None:
                await conn.execute("SELECT set_config('app.tenant_id', $1, false)", tenant)
            for statement in statements:
                await conn.execute(statement.replace("__S__", f'"{settings.pg_schema}"'))
        finally:
            await conn.close()

    asyncio.run(run())


def _body() -> dict[str, Any]:
    return {
        "mode": "obfuscate",
        "snapshot_id": "snap_x",
        "source": {"connection_id": "c_x", "object": {"owner": "bank", "name": "customers"}},
        "target": {
            "write_mode": "table",
            "connection_id": "c_y",
            "object": {"owner": "bank_uat", "name": "customers"},
        },
        "purpose": {"ticket": "CHG-1", "legal_basis": "test"},
    }


def test_run_is_refused_when_the_daily_quota_is_used(api_client, db_settings) -> None:  # noqa: ANN001
    client, sink = api_client
    _sql(
        db_settings,
        None,
        "INSERT INTO __S__.dps_tenant (tenant_id, daily_row_quota) VALUES ('t1', 100)",
    )
    _sql(
        db_settings,
        "t1",
        "INSERT INTO __S__.dps_tenant_usage (tenant_id, day, rows_written) "
        "VALUES ('t1', (now() AT TIME ZONE 'UTC')::date, 100)",
    )
    headers = {"X-API-KEY": "dps_jobs", "X-DPS-Tenant": "t1"}
    r = client.post("/api/v1/jobs/run", json=_body(), headers=headers)
    assert r.status_code == 429 and r.json()["code"] == "daily_row_quota_exceeded"
    assert any(e.action == "job.create" and e.outcome == "rejected" for e in sink.events)
    # a tenant without a quota is not refused for it (the fake ids fail later, on lookup)
    other = client.post("/api/v1/jobs/run", json=_body(), headers={**headers, "X-DPS-Tenant": "t2"})
    assert other.status_code != 429 and json.loads(other.text)["code"] != "daily_row_quota_exceeded"
