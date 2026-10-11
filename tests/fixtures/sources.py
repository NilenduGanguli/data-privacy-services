"""Source-database and object-store fixtures (contract §12.2).

``source_pg`` gives each test a fresh schema ``t_<hex>`` in the demo ``bank`` database of the
lane's ``source-pg`` (``DPS_TEST_SOURCE_PG_DSN``, default built from ``DPS_SOURCE_PG_PORT``),
readable by the local login ``dps_reader`` and writable by ``dps_writer`` (both created by
``docker/source-pg/initdb/01_bank_schema.sql``; local passwords equal the role name). The schema is
dropped afterwards. Rows put there by tests are synthetic.
"""

from __future__ import annotations

import secrets
from collections.abc import AsyncIterator
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
            f'ALTER DEFAULT PRIVILEGES IN SCHEMA "{schema}" '
            "GRANT USAGE, SELECT ON SEQUENCES TO dps_writer"
        )
        await conn.execute(f'GRANT CREATE ON SCHEMA "{schema}" TO dps_writer')
        yield SourcePg(dsn=dsn, schema=schema, admin=conn)
    finally:
        await conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        await conn.close()
