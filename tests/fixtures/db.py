"""Metadata-DB fixtures: one migrated database per test session, pools per test (contract §12.2).

The admin DSN comes from ``DPS_TEST_PG_DSN`` (default built from ``DPS_DB_PORT``, see
``tests.fixtures.services``). Each session creates ``dps_test_<pid>`` owned by ``dps_owner``,
migrates it once and drops it at the end; ``fresh_db`` makes empty databases for runner tests.
Role passwords are the local demo literals (equal to the role name), never production values.
"""

from __future__ import annotations

import asyncio
import base64
import os
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from types import ModuleType
from urllib.parse import urlparse

import asyncpg
import pytest
import pytest_asyncio

from dps import db
from dps.config import Settings
from tests.fixtures.services import require_service

TEST_KEK = bytes(range(32))  # test-only constant; never a production value
ROLES_SQL = """
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='dps_owner') THEN
    CREATE ROLE dps_owner LOGIN PASSWORD 'dps_owner'
      NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOINHERIT;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='dps_app_rw') THEN
    CREATE ROLE dps_app_rw NOLOGIN NOSUPERUSER NOBYPASSRLS;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='dps_worker') THEN
    CREATE ROLE dps_worker NOLOGIN NOSUPERUSER NOBYPASSRLS;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='dps_app') THEN
    CREATE ROLE dps_app LOGIN PASSWORD 'dps_app' NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='dps_worker_login') THEN
    CREATE ROLE dps_worker_login LOGIN PASSWORD 'dps_worker_login'
      NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
  END IF;
END$$;
GRANT dps_app_rw TO dps_app;
GRANT dps_app_rw, dps_worker TO dps_worker_login;
"""


def make_db_settings(admin_dsn: str, database: str, **overrides: object) -> Settings:
    """Settings that point every login at ``database`` with the local demo passwords."""
    parsed = urlparse(admin_dsn)
    base: dict[str, object] = dict(
        _env_file=None,
        env="local",
        auth_enabled=False,
        llm_provider="stub",
        pg_host=parsed.hostname or "127.0.0.1",
        pg_port=parsed.port or 5432,
        pg_database=database,
        pg_user="dps_app",
        pg_password="dps_app",
        pg_worker_user="dps_worker_login",
        pg_worker_password="dps_worker_login",
        pg_migration_user="dps_owner",
        pg_migration_password="dps_owner",
        migrations_mode="off",
        pg_pool_min=1,
        pg_pool_max=8,
        job_kek=base64.b64encode(TEST_KEK).decode(),
    )
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


async def create_database(admin_dsn: str, name: str) -> None:
    """(Re)create ``name`` owned by ``dps_owner``; creates the contract roles when missing."""
    conn = await asyncpg.connect(admin_dsn)
    try:
        await conn.execute(ROLES_SQL)
        await conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await conn.execute(f'CREATE DATABASE "{name}" OWNER dps_owner')
        await conn.execute(f"ALTER DATABASE \"{name}\" SET timezone TO 'UTC'")
        await conn.execute(f'GRANT CONNECT ON DATABASE "{name}" TO dps_app_rw, dps_worker')
    finally:
        await conn.close()


async def drop_database(admin_dsn: str, name: str) -> None:
    """Drop ``name`` (terminating its sessions)."""
    conn = await asyncpg.connect(admin_dsn)
    try:
        await conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    finally:
        await conn.close()


async def _migrate(settings: Settings) -> None:
    conn = await db.open_migration_connection(settings)
    try:
        await db.run_migrations(settings, connection=conn)
    finally:
        await conn.close()


async def _truncate(settings: Settings) -> None:
    conn = await db.open_migration_connection(settings)
    schema = db.schema_ident(settings)
    try:
        await conn.execute(
            f"TRUNCATE {schema}.dps_audit_log, {schema}.dps_evidence, {schema}.dps_artefact, "
            f"{schema}.dps_snapshot, {schema}.dps_job_partition, {schema}.dps_job, "
            f"{schema}.dps_job_group, {schema}.dps_connection, {schema}.dps_tenant, "
            f"{schema}.dps_worker, {schema}.dps_api_key RESTART IDENTITY CASCADE"
        )
    finally:
        await conn.close()


@pytest.fixture(scope="session")
def pg_admin_dsn() -> str:
    """Superuser DSN of the metadata Postgres (skips/fails when it is down)."""
    return require_service("dps-db")


@pytest.fixture(scope="session")
def db_settings(pg_admin_dsn: str) -> Iterator[Settings]:
    """A migrated ``dps_test_<pid>`` database for the whole session."""
    name = f"dps_test_{os.getpid()}"
    asyncio.run(create_database(pg_admin_dsn, name))
    settings = make_db_settings(pg_admin_dsn, name)
    asyncio.run(_migrate(settings))
    yield settings
    asyncio.run(drop_database(pg_admin_dsn, name))


@pytest_asyncio.fixture
async def migrated_db(db_settings: Settings) -> AsyncIterator[Settings]:
    """Pools bound to this test's loop; all tables truncated afterwards."""
    await db.init_pool(db_settings)
    await db.init_worker_pool(db_settings)
    try:
        yield db_settings
    finally:
        await db.close_pools()
        await _truncate(db_settings)


@pytest.fixture
def kek() -> bytes:
    """The test KEK (32 bytes) the ``db_settings`` sealing uses."""
    return TEST_KEK


@pytest.fixture
def tenant_conn(migrated_db: Settings) -> Callable[..., object]:
    """``async with tenant_conn("t1") as conn:`` — the API pool with the tenant GUC bound."""
    return db.acquire


@pytest.fixture
def queue(migrated_db: Settings) -> ModuleType:
    """The ``dps.jobs`` module (Task 8) bound to the test database."""
    import dps.jobs

    return dps.jobs


@pytest_asyncio.fixture
async def fresh_db(pg_admin_dsn: str) -> AsyncIterator[Callable[..., Awaitable[Settings]]]:
    """Factory for empty, unmigrated databases (migration-runner tests); dropped afterwards."""
    made: list[str] = []

    async def make(suffix: str = "x", **overrides: object) -> Settings:
        name = f"dps_fresh_{os.getpid()}_{suffix}"
        await create_database(pg_admin_dsn, name)
        made.append(name)
        return make_db_settings(pg_admin_dsn, name, **overrides)

    yield make
    await db.close_pools()
    for name in made:
        await drop_database(pg_admin_dsn, name)
