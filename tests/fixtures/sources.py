"""Source-database and object-store fixtures (contract §12.2).

``source_pg`` gives each test a fresh schema ``t_<hex>`` in the demo ``bank`` database of the
lane's ``source-pg`` (``DPS_TEST_SOURCE_PG_DSN``, default built from ``DPS_SOURCE_PG_PORT``),
readable by the local login ``dps_reader`` and writable by ``dps_writer`` (both created by
``docker/source-pg/initdb/01_bank_schema.sql``; local passwords equal the role name). The schema is
dropped afterwards. Rows put there by tests are synthetic.
"""

from __future__ import annotations

import asyncio
import secrets
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

import asyncpg
import pytest
import pytest_asyncio
from pydantic import SecretStr

from tests.fixtures.services import require_service

if TYPE_CHECKING:
    from dps.connections import Connection, Credentials
    from dps.layers.connectors.base import ObjectRef
    from dps.storage import LocalObjectStore, S3ObjectStore
    from dps.worker import HandlerContext


@pytest.fixture
def object_store(tmp_path: Path) -> LocalObjectStore:
    """A local object store under the test's tmp directory."""
    from dps.storage import LocalObjectStore

    return LocalObjectStore(tmp_path / "objects")


@pytest.fixture
def s3_store() -> S3ObjectStore:
    """The ``dps-artefacts`` bucket on the lane's MinIO (local demo credentials)."""
    pytest.importorskip("boto3")
    from dps.storage import S3ObjectStore

    endpoint = require_service("minio")
    return S3ObjectStore(
        bucket="dps-artefacts",
        endpoint_url=endpoint,
        region="us-east-1",
        access_key_id="minioadmin",
        secret_access_key="minioadmin",
        force_path_style=True,
    )


CUSTOMERS_DDL = (
    "customer_id bigint PRIMARY KEY, full_name varchar(120) NOT NULL, date_of_birth date, "
    "national_id varchar(18), id_type varchar(12) NOT NULL, tax_id char(10), email varchar(254), "
    "mobile varchar(20), country_code char(2) NOT NULL, segment varchar(16) NOT NULL, "
    "risk_score smallint NOT NULL, kyc_status varchar(12) NOT NULL, "
    "relationship_manager_id bigint, notes text, created_at timestamptz NOT NULL, "
    "updated_at timestamptz NOT NULL"
)
#: Test-only synthesis seed (32 zero bytes): the demo rows are the same on every run.
_ROWS_SEED = "A" * 43


def customers_records(n: int) -> list[tuple[Any, ...]]:
    """``n`` synthetic customers rows (personas from the surrogate engine, reserved domains)."""
    from dps.engine.synth.surrogates import SurrogateEngine, SynthesisSeed, seed_bytes

    engine = SurrogateEngine(seed_bytes(SynthesisSeed(seed=_ROWS_SEED)), default_locale="en_IN")
    created = datetime(2024, 1, 1, tzinfo=UTC)
    out: list[tuple[Any, ...]] = []
    for i in range(n):
        country = ["IN", "US"][i % 2]
        persona = engine.persona(f"customer:{i}", "en_IN" if country == "IN" else "en_US")
        out.append(
            (
                10_000_001 + i,
                persona.full_name,
                persona.dob,
                f"{(7_000_000_000 + i * 7919) % 10**12:012d}" if country == "IN" else None,
                "AADHAAR" if country == "IN" else "SSN",
                f"ABCPE{1000 + i % 9000:04d}F" if country == "IN" else None,
                f"{persona.email_local}@example-mail.in",
                f"+91 9{(800_000_000 + i * 137) % 10**9:09d}" if country == "IN" else None,
                country,
                ["retail", "premium", "private"][i % 3],
                i % 900,
                ["verified", "pending"][i % 2],
                None,
                f"Prefers e-mail: {persona.email_local}@example-mail.in" if i % 4 == 0 else None,
                created,
                created,
            )
        )
    return out


@dataclass
class SourcePg:
    """One test's schema in the demo ``bank`` database, with a superuser connection to it."""

    dsn: str
    schema: str
    admin: asyncpg.Connection

    @property
    def host(self) -> str:
        """Host of the source server (from the DSN)."""
        return urlparse(self.dsn).hostname or "127.0.0.1"

    @property
    def port(self) -> int:
        """Port of the source server (from the DSN)."""
        return urlparse(self.dsn).port or 5432

    @property
    def database(self) -> str:
        """Database of the DSN (``bank``)."""
        return urlparse(self.dsn).path.lstrip("/") or "bank"

    async def table(self, name: str, ddl_columns: str, rows: list[tuple[Any, ...]]) -> ObjectRef:
        """``CREATE TABLE <schema>.<name> (<ddl_columns>)`` and copy ``rows`` into it."""
        from dps.layers.connectors.base import ObjectRef

        await self.admin.execute(f'CREATE TABLE "{self.schema}"."{name}" ({ddl_columns})')
        if rows:
            await self.admin.copy_records_to_table(name, records=rows, schema_name=self.schema)
        return ObjectRef(owner=self.schema, name=name)

    async def customers_table(self, rows: int = 300, name: str = "customers") -> ObjectRef:
        """``<name>`` (default ``customers``) with the demo DDL (``01_bank_schema.sql``, without
        its foreign key) and ``rows`` deterministic synthetic rows from the surrogate engine
        (fixed seed, no real PII)."""
        return await self.table(name, CUSTOMERS_DDL, customers_records(rows))

    def connection(
        self, *, user: str = "dps_reader", roles: tuple[str, ...] = ("source",)
    ) -> Connection:
        """An in-memory ``Connection`` pointing at the source server's ``bank`` database."""
        from dps.connections import Connection
        from dps.jobs_models import ConnectionParams

        now = datetime.now(UTC)
        return Connection(
            id="c_" + "0" * 26,
            tenant_id="t1",
            name=f"src-{user.replace('_', '-')}",
            engine="postgres",
            params=ConnectionParams(host=self.host, port=self.port, database=self.database),
            secret_ref=None,
            has_secret=False,
            secret_expires_at=None,
            roles=list(roles),  # type: ignore[arg-type]
            tags=[],
            capabilities={},
            last_test=None,
            created_by=None,
            created_at=now,
            updated_at=now,
        )

    def credentials(self, user: str = "dps_reader") -> Credentials:
        """Local demo credentials: the password equals the login (``postgres`` → ``postgres``)."""
        from dps.connections import Credentials

        return Credentials(username=user, password=SecretStr(user))


@pytest_asyncio.fixture
async def source_pg() -> AsyncIterator[SourcePg]:
    """A fresh schema ``t_<hex>`` in the ``bank`` database (superuser), readable by dps_reader;
    dropped after the test."""
    dsn = require_service("source-pg")
    schema = f"t_{secrets.token_hex(4)}"
    conn = await asyncpg.connect(dsn)
    try:
        await _create_schema(conn, schema)
        yield SourcePg(dsn=dsn, schema=schema, admin=conn)
    finally:
        await conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        await conn.close()


@dataclass
class SourcePgSync:
    """A test schema in the ``bank`` database for synchronous tests (TestClient flows)."""

    dsn: str
    schema: str


async def _create_schema(conn: asyncpg.Connection, schema: str) -> None:
    await conn.execute(f'CREATE SCHEMA "{schema}"')
    await conn.execute(f'GRANT USAGE ON SCHEMA "{schema}" TO dps_reader, dps_writer')
    await conn.execute(
        f'ALTER DEFAULT PRIVILEGES IN SCHEMA "{schema}" GRANT SELECT ON TABLES TO dps_reader'
    )
    await conn.execute(
        f'ALTER DEFAULT PRIVILEGES IN SCHEMA "{schema}" '
        "GRANT SELECT, INSERT, UPDATE, DELETE, TRUNCATE ON TABLES TO dps_writer"
    )
    await conn.execute(
        f'ALTER DEFAULT PRIVILEGES IN SCHEMA "{schema}" GRANT USAGE, SELECT ON SEQUENCES TO dps_writer'
    )
    await conn.execute(f'GRANT CREATE ON SCHEMA "{schema}" TO dps_writer')


@pytest.fixture
def source_pg_sync() -> Iterator[SourcePgSync]:
    """A fresh schema with ``customers`` (200 synthetic rows), set up and dropped with
    ``asyncio.run`` so synchronous tests (a ``TestClient`` owns its own loop) can use it."""
    dsn = require_service("source-pg")
    schema = f"t_{secrets.token_hex(4)}"

    async def setup() -> None:
        conn = await asyncpg.connect(dsn)
        try:
            await _create_schema(conn, schema)
            await SourcePg(dsn=dsn, schema=schema, admin=conn).customers_table(rows=200)
        finally:
            await conn.close()

    async def teardown() -> None:
        conn = await asyncpg.connect(dsn)
        try:
            await conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        finally:
            await conn.close()

    asyncio.run(setup())
    try:
        yield SourcePgSync(dsn=dsn, schema=schema)
    finally:
        asyncio.run(teardown())


#: The tenants the suite's connections use; each gets its own copy of the source secrets.
SECRET_TENANTS = ("t1", "t2")


def write_ref(
    root: Path, ref: str, files: dict[str, str], tenants: tuple[str, ...] = SECRET_TENANTS
) -> None:
    """One ``secret_ref`` folder (``files``: file name → local demo literal) under each tenant's
    folder of ``root`` — refs are tenant-scoped (SEC-2): ``<root>/<tenant>/<ref>/<file>``."""
    for tenant in tenants:
        folder = root / tenant / ref
        folder.mkdir(parents=True, exist_ok=True)
        for name, value in files.items():
            (folder / name).write_text(value, encoding="utf-8")


def write_source_secrets(root: Path, tenants: tuple[str, ...] = SECRET_TENANTS) -> Path:
    """Write the local ``secret_ref`` directories under each tenant's folder of ``root``
    (contract §5.1, refs are tenant-scoped): ``<tenant>/dps/src/test-pg`` = dps_reader,
    ``…/test-pg-rw`` = dps_writer, ``…/test-pg-admin`` = postgres (local demo logins;
    password = login)."""
    for tenant in tenants:
        for ref, login in (
            ("dps/src/test-pg", "dps_reader"),
            ("dps/src/test-pg-rw", "dps_writer"),
            ("dps/src/test-pg-admin", "postgres"),
        ):
            folder = root / tenant / ref
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "username").write_text(login + "\n", encoding="utf-8")
            (folder / "password").write_text(login + "\n", encoding="utf-8")
    return root


@pytest.fixture
def source_secrets(handler_ctx: HandlerContext) -> Path:
    """:func:`write_source_secrets` under the handler's ``secret_dir``."""
    return write_source_secrets(Path(handler_ctx.settings.secret_dir))


@pytest.fixture
def register_source(
    handler_ctx: HandlerContext, source_secrets: Path
) -> Callable[..., Awaitable[Connection]]:
    """``await register_source("t1", ref=..., roles=..., name=..., tags=..., port=...)`` stores a
    PostgreSQL connection to the lane's source-pg (database ``bank``) under that tenant."""
    from dps.connections import create_connection
    from dps.jobs_models import ConnectionParams, ConnectionSpec

    dsn = require_service("source-pg")
    parsed = urlparse(dsn)

    async def register(
        tenant: str,
        *,
        ref: str = "dps/src/test-pg",
        roles: set[str] | None = None,
        name: str | None = None,
        tags: tuple[str, ...] = (),
        port: int | None = None,
    ) -> Connection:
        spec = ConnectionSpec(
            name=name or f"test-pg-{secrets.token_hex(4)}",
            engine="postgres",
            params=ConnectionParams(
                host=parsed.hostname or "127.0.0.1",
                port=port or parsed.port or 5432,
                database=parsed.path.lstrip("/") or "bank",
            ),
            secret_ref=ref,
            roles=roles or {"source"},  # type: ignore[arg-type]
            tags=list(tags),
        )
        return await create_connection(
            tenant, spec, settings=handler_ctx.settings, kek=handler_ctx.kek, created_by="test"
        )

    return register
