"""Oracle and MongoDB fixtures for M3 integration tests (contract §12.2 names).

Endpoints come only from ``DPS_TEST_ORACLE_DSN`` / ``DPS_TEST_MONGO_URI``; when unset, the default is
built from the compose host-port variables ``DPS_ORACLE_PORT`` / ``DPS_MONGO_PORT`` (contract §0,
amendment 2026-10-12), so a lane moves compose and the tests together.

SYSTEM is used only to create and drop a per-session owner and a read-only user. Code under test never
connects as SYSTEM, so a missing grant fails here exactly as it would in a bank's least-privilege
setup, and parallel runs never share objects (Wega lessons 4 and 5). The drivers are imported inside
the fixtures, so a host without the ``connectors`` extra still collects the unit suite.
"""

from __future__ import annotations

import os
import secrets
import socket
import time
from collections.abc import Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import pytest

from tests.services import host_port, service_url

if TYPE_CHECKING:
    import oracledb
    import pymongo

#: Oracle Free opens its PDB a while after the listener accepts TCP; first start takes 1-3 min.
ORACLE_SESSION_WAIT_REQUIRED_S = 180
ORACLE_SESSION_WAIT_S = 5
MONGO_PRIMARY_WAIT_REQUIRED_S = 60
MONGO_PRIMARY_WAIT_S = 5


def _required() -> bool:
    return os.environ.get("DPS_TEST_REQUIRE_SERVICES") == "1"


def oracle_dsn() -> str:
    """``host:port/service`` of the lane's Oracle Free (``DPS_TEST_ORACLE_DSN`` first)."""
    return service_url("oracle")


def mongo_uri() -> str:
    """The lane's MongoDB URI with ``directConnection=true`` (``DPS_TEST_MONGO_URI`` first)."""
    return service_url("mongo")


def _tcp_ok(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _unavailable(hint: str) -> None:
    """Skip with ``hint`` (the start command, never a DSN), or fail when services are required."""
    if _required():
        pytest.fail(f"service required but unreachable — {hint}")
    pytest.skip(hint)


@dataclass(frozen=True)
class OracleTestSchema:
    """A per-session owner (tables, sequences, quota) and a read-only login (per-table grants)."""

    dsn: str
    owner: str
    owner_password: str
    ro_user: str
    ro_password: str

    def __repr__(self) -> str:
        return f"OracleTestSchema(owner={self.owner!r}, ro_user={self.ro_user!r})"

    def grant_select(self, admin: oracledb.Connection, table: str) -> None:
        """``GRANT SELECT`` on one of the owner's tables to the read-only login."""
        with admin.cursor() as cur:
            cur.execute(f'GRANT SELECT ON "{self.owner}"."{table.upper()}" TO "{self.ro_user}"')


@pytest.fixture(scope="session")
def oracle_admin() -> Iterator[oracledb.Connection]:
    """SYSTEM on the lane's Oracle Free — only to create and drop test users (autocommit)."""
    oracledb = pytest.importorskip("oracledb")
    dsn = oracle_dsn()
    host, port = host_port(dsn)
    if not _tcp_ok(host, port):
        _unavailable("start: docker compose --profile oracle up -d oracle")
    user = os.environ.get("DPS_TEST_ORACLE_USER", "system")
    password = os.environ.get("DPS_TEST_ORACLE_PASSWORD", "dps_local_oracle")
    wait = ORACLE_SESSION_WAIT_REQUIRED_S if _required() else ORACLE_SESSION_WAIT_S
    deadline = time.monotonic() + wait
    while True:
        try:
            conn = oracledb.connect(user=user, password=password, dsn=dsn, tcp_connect_timeout=10)
            break
        except oracledb.Error:
            if time.monotonic() > deadline:
                _unavailable("oracle listener is up but the PDB does not accept sessions yet")
            time.sleep(3)
    conn.autocommit = True
    yield conn
    conn.close()


def _create_schema(admin: oracledb.Connection) -> OracleTestSchema:
    """``DPS_T_<6 hex>`` (owner: tables, sequences, quota) and ``DPS_T_<6 hex>_RO`` (CREATE
    SESSION only)."""
    tag = secrets.token_hex(3).upper()
    schema = OracleTestSchema(
        dsn=oracle_dsn(),
        owner=f"DPS_T_{tag}",
        owner_password="p" + secrets.token_hex(12),
        ro_user=f"DPS_T_{tag}_RO",
        ro_password="p" + secrets.token_hex(12),
    )
    with admin.cursor() as cur:
        cur.execute(
            f'CREATE USER "{schema.owner}" IDENTIFIED BY "{schema.owner_password}" '
            "DEFAULT TABLESPACE USERS QUOTA UNLIMITED ON USERS"
        )
        cur.execute(f'GRANT CREATE SESSION, CREATE TABLE, CREATE SEQUENCE TO "{schema.owner}"')
        cur.execute(f'CREATE USER "{schema.ro_user}" IDENTIFIED BY "{schema.ro_password}"')
        cur.execute(f'GRANT CREATE SESSION TO "{schema.ro_user}"')
    return schema


def _drop_schema(admin: oracledb.Connection, schema: OracleTestSchema) -> None:
    oracledb = pytest.importorskip("oracledb")
    for user in (schema.ro_user, schema.owner):
        for _ in range(10):
            try:
                with admin.cursor() as cur:
                    cur.execute(f'DROP USER "{user}" CASCADE')
                break
            except oracledb.DatabaseError:  # ORA-01940: a pool still holds a session — retry
                time.sleep(1)


@pytest.fixture(scope="session")
def oracle_schema(oracle_admin: oracledb.Connection) -> Iterator[OracleTestSchema]:
    """``DPS_T_<6 hex>`` (owner) and ``DPS_T_<6 hex>_RO`` (CREATE SESSION only), dropped at the end."""
    pytest.importorskip("oracledb")
    schema = _create_schema(oracle_admin)
    yield schema
    _drop_schema(oracle_admin, schema)


@pytest.fixture(scope="session")
def oracle_target_schema(oracle_admin: oracledb.Connection) -> Iterator[OracleTestSchema]:
    """A second per-session owner, the login of a **separate** target connection (the demo's
    ``CRM`` → ``CRM_MASKED``): it holds no grant on :func:`oracle_schema`'s tables, so it cannot
    see the source object. Dropped at the end."""
    pytest.importorskip("oracledb")
    schema = _create_schema(oracle_admin)
    yield schema
    _drop_schema(oracle_admin, schema)


@pytest.fixture
def oracle_conn(oracle_schema: OracleTestSchema) -> Iterator[oracledb.Connection]:
    """A session as the test owner (autocommit off)."""
    oracledb = pytest.importorskip("oracledb")
    conn = oracledb.connect(
        user=oracle_schema.owner, password=oracle_schema.owner_password, dsn=oracle_schema.dsn
    )
    yield conn
    conn.close()


@pytest.fixture(scope="session")
def mongo_client() -> Iterator[pymongo.MongoClient[dict[str, Any]]]:
    """A synchronous client for setup and assertions only (code under test uses its own)."""
    pymongo = pytest.importorskip("pymongo")
    uri = mongo_uri()
    host, port = host_port(uri)
    if not _tcp_ok(host, port):
        _unavailable("start: docker compose --profile sources up -d mongo")
    client: pymongo.MongoClient[dict[str, Any]] = pymongo.MongoClient(
        uri, serverSelectionTimeoutMS=5000
    )
    wait = MONGO_PRIMARY_WAIT_REQUIRED_S if _required() else MONGO_PRIMARY_WAIT_S
    deadline = time.monotonic() + wait
    while True:
        try:
            if client.admin.command("hello").get("isWritablePrimary"):
                break
        except pymongo.errors.PyMongoError:
            pass
        if time.monotonic() > deadline:
            client.close()
            _unavailable("mongo is up but the replica set has no primary yet")
        time.sleep(1)
    yield client
    client.close()


@pytest.fixture
def mongo_db(mongo_client: pymongo.MongoClient[dict[str, Any]]) -> Iterator[str]:
    """A fresh database name ``dps_t_<8 hex>``, dropped after the test."""
    name = "dps_t_" + secrets.token_hex(4)
    yield name
    mongo_client.drop_database(name)
