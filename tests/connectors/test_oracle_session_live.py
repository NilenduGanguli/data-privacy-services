"""``OraclePool``, the value-free error mapping and the fetch types on the lane's Oracle Free.

docker compose --profile oracle up -d oracle
DPS_TEST_REQUIRE_SERVICES=1 pytest -q -m oracle tests/connectors/test_oracle_session_live.py
"""

from __future__ import annotations

import decimal
from collections.abc import Iterator
from typing import Any

import pytest

from dps.layers.connectors.base import ConnectorError
from tests.fixtures.m3_services import OracleTestSchema

oracledb = pytest.importorskip("oracledb")

from dps.layers.connectors import oracle as ora  # noqa: E402

pytestmark = [pytest.mark.integration, pytest.mark.oracle]


def _pool(schema: OracleTestSchema, **kw: Any) -> ora.OraclePool:
    args: dict[str, Any] = dict(
        user=schema.owner,
        password=schema.owner_password,
        dsn=schema.dsn,
        size=1,
        call_timeout_ms=30000,
        connect_timeout_s=5.0,
        module="dps",
        client_identifier="j_live",
    )
    args.update(kw)
    return ora.OraclePool(**args)


@pytest.fixture
def owner_pool(oracle_schema: OracleTestSchema) -> Iterator[ora.OraclePool]:
    pool = _pool(oracle_schema)
    yield pool
    pool.close()


def test_sessions_are_tagged_and_binary_collated(owner_pool: ora.OraclePool) -> None:
    with owner_pool.acquire(action="partitions") as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT SYS_CONTEXT('USERENV', 'MODULE'), SYS_CONTEXT('USERENV', 'ACTION'), "
            "SYS_CONTEXT('USERENV', 'CLIENT_IDENTIFIER') FROM dual"
        )
        assert cur.fetchone() == ("dps", "partitions", "j_live")
        cur.execute(
            "SELECT parameter, value FROM nls_session_parameters "
            "WHERE parameter IN ('NLS_SORT', 'NLS_COMP')"
        )
        assert dict(cur.fetchall()) == {"NLS_SORT": "BINARY", "NLS_COMP": "BINARY"}
        assert conn.call_timeout == 30000


def test_release_rolls_back_what_the_block_left_open(
    owner_pool: ora.OraclePool, oracle_conn: Any
) -> None:
    with oracle_conn.cursor() as cur:
        cur.execute("CREATE TABLE POOL_PROBE (ID NUMBER(10) PRIMARY KEY)")
    with owner_pool.acquire(action="write") as conn, conn.cursor() as cur:
        cur.execute("INSERT INTO POOL_PROBE (ID) VALUES (1)")  # never committed
    with owner_pool.acquire(action="read") as conn, conn.cursor() as cur:  # the same session
        cur.execute("SELECT COUNT(*) FROM POOL_PROBE")
        assert cur.fetchone() == (0,)
    with oracle_conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM POOL_PROBE")
        assert cur.fetchone() == (0,)


def _timed_out(pool: ora.OraclePool, sql: str) -> ConnectorError:
    with pytest.raises(ConnectorError) as info:
        with pool.acquire(action="slow", call_timeout_ms=1000) as conn:
            try:
                with conn.cursor() as cur:
                    cur.execute(sql)
                    cur.fetchall()
            except oracledb.Error as exc:
                raise ora.connector_error("source_error", exc) from None
    return info.value


def test_a_hung_call_becomes_a_statement_timeout(owner_pool: ora.OraclePool) -> None:
    # A long SQL call: the driver breaks it at call_timeout and the session stays usable.
    err = _timed_out(owner_pool, "SELECT COUNT(*) FROM all_objects a, all_objects b, all_objects c")
    assert (err.code, err.detail, err.retryable) == ("statement_timeout", "DPY-4024", False)
    with owner_pool.acquire(action="after") as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM dual")
        assert cur.fetchone() == (1,)


def test_a_call_the_server_cannot_break_drops_the_session(owner_pool: ora.OraclePool) -> None:
    # PL/SQL asleep does not answer the break within the timeout either: the thin driver closes
    # the connection (DPY-4011). The pool must drop it — python-oracledb 26.0.0 crashes the process
    # (SIGBUS) when call_timeout is set on such a connection — and the next checkout is fresh.
    err = _timed_out(owner_pool, "BEGIN DBMS_SESSION.SLEEP(5); END;")
    assert (err.code, err.detail, err.retryable) == ("connection_failed", "DPY-4011", True)
    with owner_pool.acquire(action="after") as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM dual")
        assert cur.fetchone() == (1,)


def test_refused_unreachable_and_unknown_service_logins(
    oracle_schema: OracleTestSchema,
) -> None:
    wrong = _pool(oracle_schema, password="not-the-password")  # one failed login on a test user
    with pytest.raises(ConnectorError) as refused, wrong.acquire(action="test"):
        pass
    assert (refused.value.code, refused.value.detail) == ("connection_failed", "ORA-01017")
    assert refused.value.retryable is False and "not-the-password" not in repr(refused.value)
    wrong.close()
    host = oracle_schema.dsn.split(":")[0]
    closed = _pool(oracle_schema, dsn=f"{host}:1/FREEPDB1", connect_timeout_s=2.0)
    with pytest.raises(ConnectorError) as unreachable, closed.acquire(action="test"):
        pass
    assert unreachable.value.code == "connection_failed" and unreachable.value.retryable is True
    closed.close()
    service = oracle_schema.dsn.rsplit("/", 1)[0] + "/NO_SUCH_SERVICE"
    unknown = _pool(oracle_schema, dsn=service)
    with pytest.raises(ConnectorError) as missing, unknown.acquire(action="test"):
        pass
    assert missing.value.code == "connection_failed" and missing.value.retryable is True
    # thin mode wraps the listener's ORA-12514 in DPY-6005 (cannot connect)
    assert missing.value.detail in ("ORA-12514", "DPY-6001", "DPY-6005")
    unknown.close()


def test_driver_messages_with_values_never_reach_the_error(
    owner_pool: ora.OraclePool, oracle_conn: Any
) -> None:
    with oracle_conn.cursor() as cur:
        cur.execute("CREATE TABLE DUP_PROBE (ID VARCHAR2(40) PRIMARY KEY)")
        cur.execute("INSERT INTO DUP_PROBE (ID) VALUES ('SENTINEL-VALUE-77')")
    oracle_conn.commit()
    with owner_pool.acquire(action="write") as conn:
        try:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO DUP_PROBE (ID) VALUES ('SENTINEL-VALUE-77')")
        except oracledb.IntegrityError as exc:
            raw = str(exc)
            err = ora.connector_error("target_error", exc)
    # Oracle 23ai's ORA-00001 message names the duplicate row's values; DPS keeps the code only.
    assert "ORA-00001" in raw and "SENTINEL-VALUE-77" in raw  # the driver text quotes the value
    assert (err.code, err.detail) == ("target_error", "ORA-00001")
    assert "SENTINEL" not in str(err) and "SENTINEL" not in repr(err)
    with owner_pool.acquire(action="read") as conn:
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM NO_SUCH_TABLE")
        except oracledb.DatabaseError as exc:
            assert ora.connector_error("source_error", exc).detail == "ORA-00942"


def test_fetch_types(owner_pool: ora.OraclePool, oracle_conn: Any) -> None:
    long_text = "Notes 🙂 " * 900  # > 4000 bytes, astral characters
    with oracle_conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE FETCH_PROBE (ID NUMBER(12) PRIMARY KEY, AMOUNT NUMBER(14,2), "
            "BIG NUMBER, RATIO BINARY_DOUBLE, NOTES CLOB, NNOTES NCLOB, IMG BLOB, "
            "J_TEXT VARCHAR2(200) CHECK (J_TEXT IS JSON), J_CLOB CLOB CHECK (J_CLOB IS JSON))"
        )
        cur.setinputsizes(
            notes=oracledb.DB_TYPE_CLOB, nnotes=oracledb.DB_TYPE_NCLOB, img=oracledb.DB_TYPE_BLOB
        )
        cur.execute(
            "INSERT INTO FETCH_PROBE VALUES (:id, :amount, :big, :ratio, :notes, :nnotes, :img, "
            ":jt, :jc)",
            {
                "id": 10000001,
                "amount": decimal.Decimal("12345.67"),
                "big": decimal.Decimal("9007199254740993"),
                "ratio": 0.5,
                "notes": long_text,
                "nnotes": long_text,
                "img": b"\x00\x01" * 3000,
                "jt": '{"a": 1}',
                "jc": '{"b": [1, 2]}',
            },
        )
    oracle_conn.commit()
    with owner_pool.acquire(action="read") as conn, conn.cursor() as cur:
        cur.outputtypehandler = ora.output_type_handler
        cur.execute(
            "SELECT ID, AMOUNT, BIG, RATIO, NOTES, NNOTES, IMG, J_TEXT, J_CLOB FROM FETCH_PROBE"
        )
        row = cur.fetchone()
    assert row[0] == 10000001 and type(row[0]) is int
    assert row[1] == decimal.Decimal("12345.67") and isinstance(row[1], decimal.Decimal)
    assert row[2] == decimal.Decimal("9007199254740993")  # exact above 2**53
    assert isinstance(row[3], float)
    assert row[4] == long_text and row[5] == long_text  # whole str, never a LOB locator
    assert row[6] == b"\x00\x01" * 3000
    assert row[7] == '{"a": 1}' and isinstance(row[8], str)  # IS JSON stays text, not a dict
