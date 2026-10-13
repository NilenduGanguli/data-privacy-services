from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from dps import db

pytestmark = pytest.mark.db
FILES = [p.name for p in db.migration_files()]


async def _owner(settings):  # noqa: ANN001, ANN202
    return await db.open_migration_connection(settings)


async def test_files_are_the_contract_set() -> None:
    assert FILES == [
        "001_schema_and_roles.sql",
        "002_tenancy_and_keys.sql",
        "003_connections.sql",
        "004_jobs_and_partitions.sql",
        "005_snapshots.sql",
        "006_evidence_and_artefacts.sql",
        "007_audit_log.sql",
        "008_rls_policies.sql",
        "009_grants.sql",
        "010_job_idempotency_fp.sql",
        "020_job_group_last_check.sql",  # M3: the group's latest consistency check
    ]


async def test_fresh_database_applies_every_file_once(fresh_db) -> None:  # noqa: ANN001
    s = await fresh_db("once")
    assert await db.run_migrations(s) == FILES
    assert await db.run_migrations(s) == []
    conn = await _owner(s)
    try:
        assert await conn.fetchval('SELECT count(*) FROM "dps".dps_migration_ledger') == len(FILES)
        tables = {
            r["tablename"]
            for r in await conn.fetch("SELECT tablename FROM pg_tables WHERE schemaname='dps'")
        }
        globals_ = {"dps_tenant", "dps_api_key", "dps_worker", "dps_migration_ledger"}
        assert set(db.GLOBAL_TABLES) == globals_ and set(db.TENANT_TABLES) | globals_ <= tables
    finally:
        await conn.close()


async def test_concurrent_runners_apply_each_file_exactly_once(fresh_db) -> None:  # noqa: ANN001
    s = await fresh_db("race")
    a, b = await asyncio.gather(db.run_migrations(s), db.run_migrations(s))
    assert sorted(a + b) == FILES and not set(a) & set(b)


async def test_drift_is_refused_and_never_replayed(fresh_db) -> None:  # noqa: ANN001
    s = await fresh_db("drift")
    await db.run_migrations(s)
    conn = await _owner(s)
    try:
        await conn.execute(
            "UPDATE \"dps\".dps_migration_ledger SET checksum = 'x' "
            "WHERE filename = '003_connections.sql'"
        )
    finally:
        await conn.close()
    with pytest.raises(db.MigrationDrift) as err:
        await db.run_migrations(s)
    assert "003_connections.sql" in str(err.value) and "CREATE TABLE" not in str(err.value)
    await db.init_pool(s)
    with pytest.raises(db.MigrationDrift):
        await db.verify_migrations(s)


async def test_verify_names_pending_files(fresh_db) -> None:  # noqa: ANN001
    s = await fresh_db("pending")
    await db.run_migrations(s)
    conn = await _owner(s)
    try:
        await conn.execute(
            "DELETE FROM \"dps\".dps_migration_ledger WHERE filename = '009_grants.sql'"
        )
    finally:
        await conn.close()
    await db.init_pool(s)
    with pytest.raises(db.MigrationDrift) as err:
        await db.verify_migrations(s)
    assert "009_grants.sql: pending" in str(err.value)


async def test_alternate_schema_name(fresh_db) -> None:  # noqa: ANN001
    s = await fresh_db("alt", pg_schema="dps_alt")
    await db.run_migrations(s)
    conn = await _owner(s)
    try:
        assert await conn.fetchval("SELECT to_regclass('\"dps_alt\".dps_job') IS NOT NULL")
    finally:
        await conn.close()


async def test_audit_partitions_ahead_and_maintenance(fresh_db) -> None:  # noqa: ANN001
    s = await fresh_db("audit", audit_retention_months=13)
    await db.run_migrations(s)
    conn = await _owner(s)
    try:
        names = {
            r["relname"]
            for r in await conn.fetch(
                "SELECT c.relname FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid "
                "WHERE i.inhparent = '\"dps\".dps_audit_log'::regclass"
            )
        }
        now = datetime.now(UTC)
        assert f"dps_audit_log_{now:%Y_%m}" in names and len(names) == 4
        await conn.execute(
            'CREATE TABLE "dps".dps_audit_log_2020_01 PARTITION OF "dps".dps_audit_log '
            "FOR VALUES FROM ('2020-01-01') TO ('2020-02-01')"
        )
        assert await db.drop_expired_audit_partitions(conn, s) == ["dps_audit_log_2020_01"]
    finally:
        await conn.close()


async def test_migrate_cli_exit_codes(fresh_db, tmp_path: Path) -> None:  # noqa: ANN001
    s = await fresh_db("cli")
    env = {
        **os.environ,
        "DPS_PG_HOST": s.pg_host,
        "DPS_PG_PORT": str(s.pg_port),
        "DPS_PG_DATABASE": s.pg_database,
        "DPS_PG_USER": "dps_app",
        "DPS_PG_PASSWORD": "dps_app",
        "DPS_PG_MIGRATION_USER": "dps_owner",
        "DPS_PG_MIGRATION_PASSWORD": "dps_owner",
    }

    def run(*args: str) -> int:
        # cwd=tmp_path: no .env on disk can change the settings the CLI reads.
        return subprocess.run(
            [sys.executable, "-m", "dps.migrate", *args],
            env=env,
            cwd=tmp_path,
            capture_output=True,
            timeout=120,
        ).returncode

    assert run("--verify") == 1
    assert run() == 0 and run("--verify") == 0 and run("--maintain") == 0


async def test_migrate_cli_failures_exit_cleanly_without_secrets(
    fresh_db,  # noqa: ANN001
    tmp_path: Path,
) -> None:
    s = await fresh_db("clifail")
    secret = "not-the-owner-password-7Qx"
    base = {
        **os.environ,
        "DPS_PG_HOST": s.pg_host,
        "DPS_PG_PORT": str(s.pg_port),
        "DPS_PG_DATABASE": s.pg_database,
        "DPS_PG_MIGRATION_USER": "dps_owner",
        "DPS_PG_MIGRATION_PASSWORD": secret,
    }

    def run(env: dict[str, str]) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            [sys.executable, "-m", "dps.migrate"],
            env=env,
            cwd=tmp_path,
            capture_output=True,
            timeout=120,
        )

    failed = run(base)  # wrong password: a failed migration run
    output = failed.stdout + failed.stderr
    assert failed.returncode == 1 and secret.encode() not in output
    assert b"Traceback" not in output
    assert run({**base, "DPS_PG_HOST": ""}).returncode == 2  # no database configured
