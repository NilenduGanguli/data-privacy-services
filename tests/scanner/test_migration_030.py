from __future__ import annotations

import asyncpg
import pytest

from dps import jobs
from dps.db import TENANT_TABLES, assert_rls_posture
from dps.jobs import JobKind, JobPhase

pytestmark = pytest.mark.db
TABLES = ("dps_scan_page", "dps_scan_field", "dps_scan_request", "dps_scan_event")
_PAGE = (
    "INSERT INTO dps.dps_scan_page (tenant_id, job_id, page_no, url_redacted, depth, status, "
    "template) VALUES ($1, $2, $3, 'http://h/', 0, $4, 'h/')"
)
_FIELD = (
    "INSERT INTO dps.dps_scan_field (tenant_id, job_id, page_no, form_index, field_no, selector, "
    "tag, fill_status) VALUES ($1, $2, 1, 0, 1, 'form#f > input', 'input', $3)"
)
_EVENT = (
    "INSERT INTO dps.dps_scan_event (tenant_id, job_id, seq, kind, result, target_redacted, "
    "note_code) VALUES ($1, $2, $3, $4, '200', '', 'start')"
)
_REQUEST = (
    "INSERT INTO dps.dps_scan_request (tenant_id, job_id, page_no, method, resource_type, "
    "decision, reason, host, count) VALUES ('t-a', $1, NULL, 'POST', 'fetch', 'blocked', "
    "'method', 'demo-site', $2) "
    "ON CONFLICT (job_id, (COALESCE(page_no, -1)), method, (COALESCE(resource_type, '')), "
    "decision, reason, host) DO UPDATE SET count = dps.dps_scan_request.count + EXCLUDED.count"
)


async def _scan(tenant: str) -> str:
    job = await jobs.create_job(
        tenant_id=tenant,
        kind=JobKind.scan,
        phase=JobPhase.scan,
        spec={"url": "x"},
        mode="synthesize",
    )
    return job.id


def test_scan_tables_are_tenant_tables() -> None:
    assert set(TABLES) <= set(TENANT_TABLES)


async def test_scan_tables_exist_with_forced_rls(migrated_db, tenant_conn) -> None:  # noqa: ANN001
    async with tenant_conn("t-a") as conn:
        rows = await conn.fetch(
            "SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class "
            "WHERE relname = ANY($1::text[]) AND relkind = 'r'",
            list(TABLES),
        )
    assert {r["relname"] for r in rows} == set(TABLES)
    assert all(r["relrowsecurity"] and r["relforcerowsecurity"] for r in rows)
    assert await assert_rls_posture() == []


async def test_tenant_isolation_and_with_check(migrated_db, queue, tenant_conn) -> None:  # noqa: ANN001
    job_id = await _scan("t-a")
    async with tenant_conn("t-a") as conn:
        await conn.execute(_PAGE, "t-a", job_id, 1, "queued")
        await conn.execute(_FIELD, "t-a", job_id, "not_filled")
        await conn.execute(_EVENT, "t-a", job_id, 1, "navigate")
    async with tenant_conn("t-b") as conn:
        for table in TABLES:
            assert await conn.fetchval(f"SELECT count(*) FROM dps.{table}") == 0
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.execute(_EVENT, "t-a", job_id, 2, "navigate")
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.execute(_PAGE, "t-a", job_id, 2, "queued")
    async with tenant_conn(None) as conn:  # an unbound GUC sees nothing
        assert await conn.fetchval("SELECT count(*) FROM dps.dps_scan_page") == 0


async def test_request_upsert_accumulates_and_cascade_deletes(
    migrated_db, queue, tenant_conn
) -> None:  # noqa: ANN001
    job_id = await _scan("t-a")
    async with tenant_conn("t-a") as conn:
        await conn.execute(_REQUEST, job_id, 2)
        await conn.execute(_REQUEST, job_id, 3)
        assert (
            await conn.fetchval("SELECT count FROM dps.dps_scan_request WHERE job_id = $1", job_id)
            == 5
        )
        await conn.execute(_PAGE, "t-a", job_id, 1, "done")
        await conn.execute(_FIELD, "t-a", job_id, "filled")
        await conn.execute(_EVENT, "t-a", job_id, 1, "fill")
        await conn.execute("DELETE FROM dps.dps_job WHERE id = $1", job_id)
        for table in TABLES:
            assert (
                await conn.fetchval(f"SELECT count(*) FROM dps.{table} WHERE job_id = $1", job_id)
                == 0
            )


async def test_registered_hosts_column_defaults_empty(migrated_db, tenant_conn) -> None:  # noqa: ANN001
    async with tenant_conn("t-a") as conn:
        await conn.execute(
            "INSERT INTO dps.dps_tenant (tenant_id) VALUES ('t-a') ON CONFLICT DO NOTHING"
        )
        assert (
            await conn.fetchval(
                "SELECT registered_hosts FROM dps.dps_tenant WHERE tenant_id = 't-a'"
            )
            == []
        )


async def test_check_constraints_refuse_unknown_codes(migrated_db, queue, tenant_conn) -> None:  # noqa: ANN001
    job_id = await _scan("t-a")
    async with tenant_conn("t-a") as conn:
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(_PAGE, "t-a", job_id, 1, "submitted")
        await conn.execute(_PAGE, "t-a", job_id, 1, "done")
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(_FIELD, "t-a", job_id, "submitted")
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(_EVENT, "t-a", job_id, 1, "submit")
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(
                "INSERT INTO dps.dps_scan_page (tenant_id, job_id, page_no, url_redacted, depth, "
                "status, template, discovered_via) VALUES ('t-a', $1, 2, 'u', 0, 'queued', 't', 'form')",
                job_id,
            )
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(
                "INSERT INTO dps.dps_scan_request (tenant_id, job_id, method, decision, reason, host)"
                " VALUES ('t-a', $1, 'POST', 'submitted', 'method', 'h')",
                job_id,
            )


async def test_the_navigation_log_is_append_only_for_the_api(
    migrated_db, queue, tenant_conn
) -> None:  # noqa: ANN001
    job_id = await _scan("t-a")
    async with tenant_conn("t-a") as conn:
        await conn.execute(_EVENT, "t-a", job_id, 1, "navigate")
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.execute("UPDATE dps.dps_scan_event SET result = 'x'")
        with pytest.raises(asyncpg.UniqueViolationError):
            await conn.execute(_EVENT, "t-a", job_id, 1, "navigate")
