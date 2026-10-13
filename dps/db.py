"""Metadata database: asyncpg pools, the RLS tenant GUC, migrations and audit partitions.

Mirrors ``di/db.py``: an API pool logged in as ``dps_app`` and a worker pool logged in as
``dps_worker_login`` (the only login in the ``dps_worker`` group, which the ``worker_claim``
policies on the queue tables target). Every tenant-scoped statement runs inside
``acquire(tenant_id)``, which binds ``app.tenant_id`` for the checkout and resets it on release;
``search_path`` is a startup parameter so asyncpg's ``RESET ALL`` on release keeps it. All SQL in
DPS is schema-qualified anyway. DDL only ever runs on the migration connection (``dps_owner``)
under ``pg_advisory_lock(hashtext('dps.migrate.' || schema))``; applied files are checksummed in
``dps_migration_ledger`` and never edited.

Nothing here logs or raises a value: messages carry file names, table names and counts only.
"""

from __future__ import annotations

import hashlib
import json
import re
import ssl
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from pathlib import Path

import asyncpg

from dps.config import Settings, get_settings
from dps.observability import get_logger

logger = get_logger(__name__)

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
TENANT_GUC = "app.tenant_id"
TENANT_TABLES: tuple[str, ...] = (
    "dps_connection",
    "dps_job_group",
    "dps_job",
    "dps_job_partition",
    "dps_snapshot",
    "dps_artefact",
    "dps_evidence",
    "dps_audit_log",
    "dps_tenant_usage",  # M5 (040)
)
GLOBAL_TABLES: tuple[str, ...] = ("dps_tenant", "dps_api_key", "dps_worker", "dps_migration_ledger")
#: Policies besides tenant_isolation that the posture check accepts, per table. A set per table so
#: later milestones can add theirs (M5: owner_directory on dps_job/dps_connection, worker_usage on
#: dps_tenant_usage).
ALLOWED_EXTRA_POLICIES: dict[str, frozenset[str]] = {
    "dps_job": frozenset({"worker_claim", "owner_directory"}),
    "dps_job_partition": frozenset({"worker_claim"}),
    "dps_connection": frozenset({"owner_directory"}),
    "dps_tenant_usage": frozenset({"worker_usage"}),
}
_SCHEMA_RE = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
_AUDIT_PART_RE = re.compile(r"^dps_audit_log_(\d{4})_(\d{2})$")

_pool: asyncpg.Pool | None = None
_worker_pool: asyncpg.Pool | None = None
_settings: Settings | None = None


class MigrationDrift(RuntimeError):
    """Shipped migration files and the ledger disagree (pending or changed files, by name only)."""


def db_configured(settings: Settings) -> bool:
    """True when a metadata database is configured (Layer 1 runs without one)."""
    return bool(settings.pg_host)


def active_settings() -> Settings:
    """The settings the pools were initialised with (falls back to the process settings)."""
    return _settings or get_settings()


def schema_ident(settings: Settings | None = None) -> str:
    """The validated, double-quoted schema identifier, e.g. ``'"dps"'``."""
    name = (settings or active_settings()).pg_schema
    if not _SCHEMA_RE.fullmatch(name):
        raise ValueError("pg_schema must match ^[a-z_][a-z0-9_]{0,62}$")
    return f'"{name}"'


def rowcount(command_tag: str) -> int:
    """Rows affected from an asyncpg command tag (``"UPDATE 3"`` → 3)."""
    parts = command_tag.split()
    return int(parts[-1]) if parts and parts[-1].isdigit() else 0


def migration_files() -> list[Path]:
    """Shipped ``NNN_*.sql`` files in apply order."""
    return sorted(MIGRATIONS_DIR.glob("[0-9][0-9][0-9]_*.sql"))


def _checksum(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _ssl_context(settings: Settings) -> ssl.SSLContext | None:
    """Verified TLS (CA file + hostname) when ``pg_ssl_root_cert`` is set; else asyncpg's default."""
    if not settings.pg_ssl_root_cert:
        return None
    ctx = ssl.create_default_context(cafile=settings.pg_ssl_root_cert)
    ctx.check_hostname = True
    ctx.verify_mode = ssl.CERT_REQUIRED
    return ctx


async def _init_conn(conn: asyncpg.Connection) -> None:
    for typ in ("json", "jsonb"):
        await conn.set_type_codec(typ, encoder=json.dumps, decoder=json.loads, schema="pg_catalog")


def _server_settings(settings: Settings, app_name: str) -> dict[str, str]:
    return {
        "application_name": app_name,
        "search_path": f"{schema_ident(settings)}, public",
        "timezone": "UTC",
    }


async def init_pool(settings: Settings | None = None) -> asyncpg.Pool:
    """The API pool (``dps_app``), created once per process."""
    global _pool, _settings
    if _pool is not None:
        return _pool
    settings = settings or active_settings()
    _settings = settings
    _pool = await asyncpg.create_pool(
        host=settings.pg_host,
        port=settings.pg_port,
        user=settings.pg_user,
        password=settings.pg_password or None,
        database=settings.pg_database,
        ssl=_ssl_context(settings),
        min_size=settings.pg_pool_min,
        max_size=settings.pg_pool_max,
        command_timeout=60,
        max_inactive_connection_lifetime=300,
        init=_init_conn,
        server_settings=_server_settings(settings, "dps-app"),
    )
    return _pool


async def init_worker_pool(settings: Settings | None = None) -> asyncpg.Pool:
    """The worker pool (``dps_worker_login``): cross-tenant queue access via ``worker_claim``."""
    global _worker_pool, _settings
    if _worker_pool is not None:
        return _worker_pool
    settings = settings or active_settings()
    _settings = _settings or settings
    user, password = settings.pg_worker_login
    _worker_pool = await asyncpg.create_pool(
        host=settings.pg_host,
        port=settings.pg_port,
        user=user,
        password=password or None,
        database=settings.pg_database,
        ssl=_ssl_context(settings),
        min_size=1,
        max_size=max(4, settings.worker_concurrency * 2 + 4),
        command_timeout=60,
        max_inactive_connection_lifetime=300,
        init=_init_conn,
        server_settings=_server_settings(settings, "dps-worker"),
    )
    return _worker_pool


async def close_pools() -> None:
    """Close both pools (tests call this between event loops)."""
    global _pool, _worker_pool, _settings
    for pool in (_pool, _worker_pool):
        if pool is not None:
            await pool.close()
    _pool = _worker_pool = None
    _settings = None


async def _bind(conn: asyncpg.Connection, tenant_id: str | None) -> bool:
    if tenant_id is None:
        return False
    await conn.execute("SELECT set_config($1, $2, false)", TENANT_GUC, tenant_id)
    return True


@asynccontextmanager
async def acquire(tenant_id: str | None) -> AsyncIterator[asyncpg.Connection]:
    """An API-pool connection with ``app.tenant_id`` bound (``None`` binds nothing → no rows)."""
    pool = await init_pool(_settings)
    async with pool.acquire() as conn:
        bound = await _bind(conn, tenant_id)
        try:
            yield conn
        finally:
            if bound:
                await conn.execute("SELECT set_config($1, '', false)", TENANT_GUC)


@asynccontextmanager
async def acquire_queue(tenant_id: str | None = None) -> AsyncIterator[asyncpg.Connection]:
    """A worker-pool connection; ``None`` = cross-tenant queue access, else the GUC is bound."""
    pool = await init_worker_pool(_settings)
    async with pool.acquire() as conn:
        bound = await _bind(conn, tenant_id)
        try:
            yield conn
        finally:
            if bound:
                await conn.execute("SELECT set_config($1, '', false)", TENANT_GUC)


async def open_migration_connection(settings: Settings | None = None) -> asyncpg.Connection:
    """One dedicated connection as the migration role; the caller closes it."""
    settings = settings or active_settings()
    user, password = settings.pg_migration_login
    conn = await asyncpg.connect(
        host=settings.pg_host,
        port=settings.pg_port,
        user=user,
        password=password or None,
        database=settings.pg_database,
        ssl=_ssl_context(settings),
        server_settings={"application_name": "dps-migrate", "timezone": "UTC"},
    )
    await _init_conn(conn)
    return conn


async def _ensure_ledger(conn: asyncpg.Connection, schema: str) -> None:
    await conn.execute(
        f"CREATE TABLE IF NOT EXISTS {schema}.dps_migration_ledger ("
        " filename text PRIMARY KEY, checksum text NOT NULL,"
        " applied_at timestamptz NOT NULL DEFAULT now())"
    )


async def _ledger(conn: asyncpg.Connection, schema: str) -> dict[str, str]:
    exists = await conn.fetchval("SELECT to_regclass($1)", f"{schema}.dps_migration_ledger")
    if exists is None:
        return {}
    rows = await conn.fetch(f"SELECT filename, checksum FROM {schema}.dps_migration_ledger")
    return {r["filename"]: r["checksum"] for r in rows}


def _drift(ledger: dict[str, str], *, include_pending: bool) -> list[str]:
    shipped = {p.name: _checksum(p) for p in migration_files()}
    problems = [
        f"{name}: checksum drift"
        for name, cs in ledger.items()
        if name in shipped and shipped[name] != cs
    ]
    problems += [f"{name}: applied but no longer shipped" for name in ledger if name not in shipped]
    if include_pending:
        problems += [f"{name}: pending" for name in shipped if name not in ledger]
    return sorted(problems)


async def run_migrations(
    settings: Settings | None = None, *, connection: asyncpg.Connection | None = None
) -> list[str]:
    """Apply pending files in order under the advisory lock; return the names applied now.

    The lock is taken before ``CREATE SCHEMA`` (two concurrent ``CREATE SCHEMA IF NOT EXISTS``
    race on ``pg_namespace``). A changed or vanished applied file refuses the whole run
    (:class:`MigrationDrift`, file names only) and nothing is replayed.
    """
    settings = settings or active_settings()
    schema = schema_ident(settings)
    lock_key = f"dps.migrate.{settings.pg_schema}"
    conn = connection or await open_migration_connection(settings)
    applied: list[str] = []
    try:
        await conn.execute("SELECT pg_advisory_lock(hashtext($1))", lock_key)
        try:
            await conn.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
            await _ensure_ledger(conn, schema)
            ledger = await _ledger(conn, schema)
            problems = _drift(ledger, include_pending=False)
            if problems:
                raise MigrationDrift("refusing to migrate: " + "; ".join(problems))
            for path in migration_files():
                checksum = _checksum(path)
                if ledger.get(path.name) == checksum:
                    continue
                sql = path.read_text(encoding="utf-8").replace("__SCHEMA__", schema)
                async with conn.transaction():
                    await conn.execute(sql)
                    await conn.execute(
                        f"INSERT INTO {schema}.dps_migration_ledger (filename, checksum) "
                        "VALUES ($1, $2)",
                        path.name,
                        checksum,
                    )
                applied.append(path.name)
                logger.info("migration_applied", migration=path.name)
            await ensure_audit_partitions(conn, settings)
        finally:
            await conn.execute("SELECT pg_advisory_unlock(hashtext($1))", lock_key)
    finally:
        if connection is None:
            await conn.close()
    return applied


async def verify_migrations(settings: Settings | None = None) -> None:
    """Raise :class:`MigrationDrift` naming every pending or changed file (runtime role, no DDL)."""
    settings = settings or active_settings()
    pool = await init_pool(settings)
    async with pool.acquire() as conn:
        ledger = await _ledger(conn, schema_ident(settings))
    problems = _drift(ledger, include_pending=True)
    if problems:
        raise MigrationDrift("migrations_mode=verify: " + "; ".join(problems))


def _add_months(d: date, n: int) -> date:
    m0 = d.month - 1 + n
    return date(d.year + m0 // 12, m0 % 12 + 1, 1)


async def ensure_audit_partitions(
    conn: asyncpg.Connection, settings: Settings, months_ahead: int = 3
) -> None:
    """Create ``dps_audit_log_YYYY_MM`` for this month (UTC) and ``months_ahead`` more (owner)."""
    schema = schema_ident(settings)
    if await conn.fetchval("SELECT to_regclass($1)", f"{schema}.dps_audit_log") is None:
        return
    first = datetime.now(UTC).date().replace(day=1)
    for i in range(months_ahead + 1):
        month = _add_months(first, i)
        nxt = _add_months(month, 1)
        # Bounds are computed here, never user input; DDL takes no bind parameters.
        await conn.execute(
            f'CREATE TABLE IF NOT EXISTS {schema}."dps_audit_log_{month:%Y_%m}" '
            f"PARTITION OF {schema}.dps_audit_log FOR VALUES FROM ('{month.isoformat()}') "
            f"TO ('{nxt.isoformat()}')"
        )


async def drop_expired_audit_partitions(conn: asyncpg.Connection, settings: Settings) -> list[str]:
    """Drop audit partitions whose whole month is older than ``audit_retention_months`` (owner)."""
    schema = schema_ident(settings)
    cutoff = _add_months(datetime.now(UTC).date().replace(day=1), -settings.audit_retention_months)
    rows = await conn.fetch(
        "SELECT c.relname FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid "
        "WHERE i.inhparent = to_regclass($1)",
        f"{schema}.dps_audit_log",
    )
    dropped: list[str] = []
    for name in sorted(r["relname"] for r in rows):
        match = _AUDIT_PART_RE.fullmatch(name)
        if match and date(int(match[1]), int(match[2]), 1) < cutoff:
            await conn.execute(f'DROP TABLE {schema}."{name}"')
            dropped.append(name)
    return dropped


def evaluate_rls_posture(
    *,
    rls_enabled: bool,
    rolsuper: bool,
    rolbypassrls: bool,
    policies_by_table: dict[str, list[str]],
    predicate_ok: dict[str, bool],
    forced: dict[str, bool],
    partition_leaks: list[str],
    worker_member: bool = False,
) -> list[str]:
    """Pure: observed RLS facts → violations (empty = sound).

    ``predicate_ok[table]`` is true when ``tenant_isolation`` filters reads *and* checks writes on
    ``app.tenant_id``; ``worker_member`` is true when the runtime (API) role is a member of
    ``dps_worker``, whose ``worker_claim`` policies would open the queue tables across tenants.
    """
    problems: list[str] = []
    if not rls_enabled:
        problems.append("DPS_RLS_ENABLED is false")
    if rolsuper:
        problems.append("runtime role is a superuser: RLS is bypassed")
    if rolbypassrls:
        problems.append("runtime role has BYPASSRLS: RLS is bypassed")
    if worker_member:
        problems.append(
            "runtime role is a member of dps_worker: worker_claim opens the queue tables"
        )
    for table in TENANT_TABLES:
        names = policies_by_table.get(table, [])
        if "tenant_isolation" not in names:
            problems.append(f"{table}: missing tenant_isolation policy")
        elif not predicate_ok.get(table, False):
            problems.append(f"{table}: tenant_isolation does not reference app.tenant_id")
        if not forced.get(table, False):
            problems.append(f"{table}: row level security is not FORCE-enabled")
        allowed = {"tenant_isolation", *ALLOWED_EXTRA_POLICIES.get(table, frozenset())}
        extra = sorted(n for n in names if n not in allowed)
        if extra:
            problems.append(f"{table}: unexpected policies {extra}")
    if partition_leaks:
        problems.append(
            "runtime role has direct privileges on audit partitions: "
            + ", ".join(sorted(partition_leaks))
        )
    return problems


def _guc_predicate(qual: str | None, with_check: str | None) -> bool:
    """``USING`` names the GUC and ``WITH CHECK`` does too (absent = ``USING`` applies)."""
    return TENANT_GUC in (qual or "") and (with_check is None or TENANT_GUC in with_check)


async def assert_rls_posture(settings: Settings | None = None) -> list[str]:
    """Observe the runtime role, policies, FORCE flags and partition grants; evaluate them."""
    settings = settings or active_settings()
    schema = settings.pg_schema
    pool = await init_pool(settings)
    async with pool.acquire() as conn:
        role = await conn.fetchrow(
            "SELECT rolsuper, rolbypassrls, pg_has_role(current_user, 'dps_worker', 'MEMBER') "
            "AS worker_member FROM pg_roles WHERE rolname = current_user"
        )
        rows = await conn.fetch(
            "SELECT tablename, policyname, qual, with_check FROM pg_policies WHERE schemaname = $1",
            schema,
        )
        flags = await conn.fetch(
            "SELECT c.relname, c.relforcerowsecurity AND c.relrowsecurity AS forced "
            "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = $1 AND c.relname = ANY($2::text[])",
            schema,
            list(TENANT_TABLES),
        )
        parts = await conn.fetch(
            "SELECT c.relname FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid "
            "WHERE i.inhparent = to_regclass($1)",
            f'"{schema}".dps_audit_log',
        )
        leaks = [
            f"{schema}.{p['relname']}"
            for p in parts
            if await conn.fetchval(
                "SELECT has_table_privilege(current_user, $1, 'SELECT') "
                "OR has_table_privilege(current_user, $1, 'INSERT') "
                "OR has_table_privilege(current_user, $1, 'UPDATE') "
                "OR has_table_privilege(current_user, $1, 'DELETE')",
                f'"{schema}"."{p["relname"]}"',
            )
        ]
    policies: dict[str, list[str]] = {}
    predicate_ok: dict[str, bool] = {}
    for r in rows:
        policies.setdefault(r["tablename"], []).append(r["policyname"])
        if r["policyname"] == "tenant_isolation":
            predicate_ok[r["tablename"]] = _guc_predicate(r["qual"], r["with_check"])
    return evaluate_rls_posture(
        rls_enabled=settings.rls_enabled,
        rolsuper=bool(role["rolsuper"]) if role else True,
        rolbypassrls=bool(role["rolbypassrls"]) if role else True,
        policies_by_table=policies,
        predicate_ok=predicate_ok,
        forced={r["relname"]: bool(r["forced"]) for r in flags},
        partition_leaks=leaks,
        worker_member=bool(role["worker_member"]) if role else True,
    )
