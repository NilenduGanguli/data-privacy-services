from __future__ import annotations

from datetime import UTC, datetime

import asyncpg
import pytest

from dps import db

pytestmark = pytest.mark.db
APPEND_ONLY_FOR_THE_API = ("dps_audit_log", "dps_evidence")  # no DELETE grant (009)


# Minimal valid row per tenant table, keyed by tenant (dps_audit_log and dps_job_partition need parents).
async def _seed(conn: asyncpg.Connection, tenant: str) -> None:
    await conn.execute(
        "INSERT INTO dps.dps_connection (id, tenant_id, name, engine, params) "
        "VALUES ($1, $2, 'c1', 'postgres', '{}')",
        f"c_{tenant}",
        tenant,
    )
    await conn.execute(
        "INSERT INTO dps.dps_job_group (id, tenant_id, name, kid, seed_fingerprint) "
        "VALUES ($1, $2, 'g', 'k_1', 'f')",
        f"grp_{tenant}",
        tenant,
    )
    await conn.execute(
        "INSERT INTO dps.dps_job (id, tenant_id, kind, phase, spec) "
        "VALUES ($1, $2, 'run', 'prepare', '{}')",
        f"j_{tenant}",
        tenant,
    )
    await conn.execute(
        "INSERT INTO dps.dps_job_partition (job_id, tenant_id, partition_no) VALUES ($1, $2, 0)",
        f"j_{tenant}",
        tenant,
    )
    await conn.execute(
        "INSERT INTO dps.dps_snapshot (id, tenant_id, connection_id, engine, object_ref, "
        "policy_name, policy_hash, shape, columns, sample_rows, content_hash) "
        "VALUES ($1, $2, 'c', 'postgres', 'bank.x', 'p', 'h', '{}', '[]', 0, 'h')",
        f"snap_{tenant}",
        tenant,
    )
    await conn.execute(
        "INSERT INTO dps.dps_artefact (id, tenant_id, job_id, kind, store, object_key, "
        "content_type, bytes, sha256) "
        "VALUES ($1, $2, $3, 'manifest', 'local', 'k', 'application/json', 1, 'h')",
        f"art_{tenant}",
        tenant,
        f"j_{tenant}",
    )
    await conn.execute(
        "INSERT INTO dps.dps_evidence (id, tenant_id, job_id, pack, pack_sha256) "
        "VALUES ($1, $2, $3, '{}', 'h')",
        f"ev_{tenant}",
        tenant,
        f"j_{tenant}",
    )
    await conn.execute(
        "INSERT INTO dps.dps_audit_log (tenant_id, route, action, outcome) "
        "VALUES ($1, '/api/v1/jobs', 'job.create', 'ok')",
        tenant,
    )


async def _seed_scan(conn: asyncpg.Connection, tenant: str) -> None:
    """One row in each M4 scan table (030), hanging off a scan job of the same tenant."""
    await conn.execute(
        "INSERT INTO dps.dps_job (id, tenant_id, kind, phase, spec) "
        "VALUES ($1, $2, 'scan', 'scan', '{}')",
        f"s_{tenant}",
        tenant,
    )
    await conn.execute(
        "INSERT INTO dps.dps_scan_page (tenant_id, job_id, page_no, url_redacted, depth, status, "
        "template) VALUES ($1, $2, 1, 'http://h/', 0, 'done', 'h/')",
        tenant,
        f"s_{tenant}",
    )
    await conn.execute(
        "INSERT INTO dps.dps_scan_field (tenant_id, job_id, page_no, form_index, field_no, "
        "selector, tag) VALUES ($1, $2, 1, 0, 1, 'input', 'input')",
        tenant,
        f"s_{tenant}",
    )
    await conn.execute(
        "INSERT INTO dps.dps_scan_request (tenant_id, job_id, method, decision, reason, host) "
        "VALUES ($1, $2, 'POST', 'blocked', 'method', 'h')",
        tenant,
        f"s_{tenant}",
    )
    await conn.execute(
        "INSERT INTO dps.dps_scan_event (tenant_id, job_id, seq, kind, result, target_redacted, "
        "note_code) VALUES ($1, $2, 1, 'navigate', '200', '', 'start')",
        tenant,
        f"s_{tenant}",
    )


async def test_posture_is_clean_for_the_api_role(migrated_db) -> None:  # noqa: ANN001
    assert await db.assert_rls_posture(migrated_db) == []


@pytest.mark.parametrize("table", db.TENANT_TABLES)
async def test_each_tenant_table_is_isolated(migrated_db, table: str) -> None:  # noqa: ANN001
    for tenant in ("t1", "t2"):
        async with db.acquire(tenant) as conn:
            await _seed(conn, tenant)
            await _seed_scan(conn, tenant)
    async with db.acquire("t2") as conn:
        assert await conn.fetchval(f"SELECT count(*) FROM dps.{table} WHERE tenant_id = 't1'") == 0
        assert await conn.fetchval(f"SELECT count(*) FROM dps.{table}") >= 1
        delete = f"DELETE FROM dps.{table} WHERE tenant_id = 't1'"
        if table in APPEND_ONLY_FOR_THE_API:
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await conn.execute(delete)
        else:
            assert db.rowcount(await conn.execute(delete)) == 0
    async with db.acquire(None) as conn:
        assert await conn.fetchval(f"SELECT count(*) FROM dps.{table}") == 0  # unbound GUC
    async with db.acquire("t1") as conn:  # t2's attempts left t1's row in place
        assert await conn.fetchval(f"SELECT count(*) FROM dps.{table}") >= 1


async def test_with_check_blocks_writing_for_another_tenant(migrated_db) -> None:  # noqa: ANN001
    async with db.acquire("t2") as conn:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.execute(
                "INSERT INTO dps.dps_connection (id, tenant_id, name, engine, params) "
                "VALUES ('c_x', 't1', 'x', 'postgres', '{}')"
            )


async def test_worker_role_sees_queue_tables_across_tenants_only(migrated_db) -> None:  # noqa: ANN001
    for tenant in ("t1", "t2"):
        async with db.acquire(tenant) as conn:
            await _seed(conn, tenant)
    async with db.acquire_queue(None) as conn:
        assert await conn.fetchval("SELECT count(*) FROM dps.dps_job") == 2
        assert await conn.fetchval("SELECT count(*) FROM dps.dps_job_partition") == 2
        assert await conn.fetchval("SELECT count(*) FROM dps.dps_connection") == 0
    async with db.acquire(None) as conn:  # dps_app is not a worker
        assert await conn.fetchval("SELECT count(*) FROM dps.dps_job") == 0


async def test_audit_log_and_evidence_are_append_only(migrated_db) -> None:  # noqa: ANN001
    async with db.acquire("t1") as conn:
        await _seed(conn, "t1")
        # The API role holds no UPDATE/DELETE grant on the audit log (009) ...
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.execute("UPDATE dps.dps_audit_log SET outcome = 'x'")
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.execute("DELETE FROM dps.dps_audit_log")
        with pytest.raises(asyncpg.RaiseError, match="append-only"):
            await conn.execute("UPDATE dps.dps_evidence SET pack = '{\"x\":1}'")
        assert (
            db.rowcount(
                await conn.execute("UPDATE dps.dps_evidence SET html_artefact_id = 'art_1'")
            )
            == 1
        )
    # ... and the trigger refuses even the owner, who holds every privilege (FORCE RLS: bind t1).
    owner = await db.open_migration_connection(migrated_db)
    try:
        await owner.execute("SELECT set_config('app.tenant_id', 't1', false)")
        with pytest.raises(asyncpg.RaiseError, match="append-only"):
            await owner.execute("UPDATE dps.dps_audit_log SET outcome = 'x'")
        with pytest.raises(asyncpg.RaiseError, match="append-only"):
            await owner.execute("DELETE FROM dps.dps_audit_log")
        assert await owner.fetchval("SELECT count(*) FROM dps.dps_audit_log") == 1
    finally:
        await owner.close()


async def test_no_direct_privilege_on_audit_partitions(migrated_db) -> None:  # noqa: ANN001
    async with db.acquire(None) as conn:
        parts = [
            r["relname"]
            for r in await conn.fetch(
                "SELECT c.relname FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid "
                "WHERE i.inhparent = 'dps.dps_audit_log'::regclass"
            )
        ]
        assert parts
        for p in parts:
            assert (
                await conn.fetchval(
                    "SELECT has_table_privilege(current_user, $1, 'SELECT')", f"dps.{p}"
                )
                is False
            )


async def test_posture_names_each_tampered_fact(fresh_db) -> None:  # noqa: ANN001
    # The API pool logs in as the worker login: the misconfiguration that would let worker_claim
    # open the queue tables to API requests (contract §2.3: dps_app is never in dps_worker).
    s = await fresh_db("posture", pg_user="dps_worker_login", pg_password="dps_worker_login")
    await db.run_migrations(s)
    month = f"dps_audit_log_{datetime.now(UTC):%Y_%m}"
    owner = await db.open_migration_connection(s)
    try:
        await owner.execute(
            'ALTER POLICY tenant_isolation ON "dps".dps_job USING (true) WITH CHECK (true)'
        )
        await owner.execute(
            'ALTER POLICY tenant_isolation ON "dps".dps_connection USING '
            "(tenant_id = current_setting('app.tenant_id', true)) WITH CHECK (true)"
        )
        await owner.execute('ALTER TABLE "dps".dps_snapshot NO FORCE ROW LEVEL SECURITY')
        await owner.execute('CREATE POLICY open_door ON "dps".dps_artefact USING (true)')
        await owner.execute(f'GRANT SELECT ON "dps"."{month}" TO dps_app_rw')
    finally:
        await owner.close()
    joined = "; ".join(await db.assert_rls_posture(s))
    assert "dps_job: tenant_isolation does not reference app.tenant_id" in joined
    assert "dps_connection: tenant_isolation does not reference app.tenant_id" in joined
    assert "dps_snapshot: row level security is not FORCE-enabled" in joined
    assert "dps_artefact: unexpected policies ['open_door']" in joined
    assert month in joined and "dps_worker" in joined
