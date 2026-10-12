from __future__ import annotations

import decimal
from typing import Any

import pytest

oracledb = pytest.importorskip("oracledb")

from dps.jobs_models import ConnectionParams  # noqa: E402
from dps.layers.connectors import oracle as ora  # noqa: E402
from dps.layers.connectors.base import ConnectorError  # noqa: E402


class FakeConn:
    def __init__(self, broken: bool = False) -> None:
        self.broken, self.rolled_back = broken, 0
        self.call_timeout = self.module = self.action = self.client_identifier = None

    def rollback(self) -> None:
        if self.broken:
            raise oracledb.DatabaseError("connection lost")
        self.rolled_back += 1


class FakePool:
    def __init__(self, conn: FakeConn, error: BaseException | None = None) -> None:
        self.conn, self.error = conn, error
        self.released: list[FakeConn] = []
        self.dropped: list[FakeConn] = []
        self.closed: bool | None = None

    def acquire(self) -> FakeConn:
        if self.error is not None:
            raise self.error
        return self.conn

    def release(self, c: FakeConn) -> None:
        self.released.append(c)

    def drop(self, c: FakeConn) -> None:
        self.dropped.append(c)

    def close(self, force: bool = False) -> None:
        self.closed = force


def _pool(
    monkeypatch: pytest.MonkeyPatch, conn: FakeConn, error: BaseException | None = None
) -> tuple[ora.OraclePool, dict[str, Any], FakePool]:
    captured: dict[str, Any] = {}
    fake = FakePool(conn, error)

    def create_pool(**kw: Any) -> FakePool:
        captured.update(kw)
        return fake

    monkeypatch.setattr(oracledb, "create_pool", create_pool)
    pool = ora.OraclePool(
        user="dps_ro",
        password="SENTINEL-PW",
        dsn="db.internal:1521/CRMPRD",
        size=4,
        call_timeout_ms=30000,
        connect_timeout_s=10.0,
        module="dps",
        client_identifier="j_01",
    )
    return pool, captured, fake


def test_pool_is_lazy_and_follows_the_wega_rules(monkeypatch: pytest.MonkeyPatch) -> None:
    pool, captured, _ = _pool(monkeypatch, FakeConn())
    assert captured == {}  # nothing opened at construction
    with pool.acquire(action="partitions"):
        pass
    assert captured["expire_time"] == 1 and captured["tcp_connect_timeout"] == 10.0
    assert (captured["min"], captured["max"], captured["increment"]) == (0, 4, 1)
    assert captured["getmode"] == oracledb.POOL_GETMODE_TIMEDWAIT
    assert captured["wait_timeout"] == 30000
    assert captured["session_callback"] is ora._init_session


def test_acquire_tags_the_session_and_always_rolls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = FakeConn()
    pool, _, fake = _pool(monkeypatch, conn)
    with pool.acquire(action="partitions") as c:
        assert (c.call_timeout, c.module, c.action, c.client_identifier) == (
            30000,
            "dps",
            "partitions",
            "j_01",
        )
    assert conn.rolled_back == 1 and fake.released == [conn]
    with pool.acquire(action="count", call_timeout_ms=300000) as c:
        assert c.call_timeout == 300000
    with pytest.raises(RuntimeError), pool.acquire(action="read"):
        raise RuntimeError("the block failed")
    assert conn.rolled_back == 3 and fake.released == [conn, conn, conn]


def test_broken_sessions_are_dropped_not_reused(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = FakeConn(broken=True)
    pool, _, fake = _pool(monkeypatch, conn)
    with pool.acquire(action="read"):
        pass
    assert fake.dropped == [conn] and fake.released == []


def test_close_is_forced_and_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    pool, _, fake = _pool(monkeypatch, FakeConn())
    pool.close()  # never opened: nothing to close
    assert fake.closed is None
    with pool.acquire(action="read"):
        pass
    pool.close()
    pool.close()
    assert fake.closed is True


def test_repr_hides_password_and_dsn(monkeypatch: pytest.MonkeyPatch) -> None:
    pool, _, _ = _pool(monkeypatch, FakeConn())
    assert "SENTINEL" not in repr(pool) and "CRMPRD" not in repr(pool)
    assert "db.internal" not in repr(pool)


def test_pool_size_must_be_positive() -> None:
    with pytest.raises(ValueError):
        ora.OraclePool(
            user="u",
            password="p",
            dsn="h:1521/S",
            size=0,
            call_timeout_ms=1000,
            connect_timeout_s=1.0,
            module="dps",
            client_identifier=None,
        )


class _Err:
    def __init__(self, full_code: str) -> None:
        self.full_code = full_code
        self.message = f"{full_code}: unique constraint violated SENTINEL-VALUE-42"


def test_driver_errors_become_value_free() -> None:
    try:
        try:
            raise oracledb.IntegrityError(_Err("ORA-00001"))
        except oracledb.Error as exc:
            raise ora.connector_error("target_error", exc) from None
    except ConnectorError as err:
        assert err.code == "target_error" and err.detail == "ORA-00001"
        assert str(err) == "target_error: ORA-00001" and "SENTINEL" not in repr(err)
        assert err.__cause__ is None and err.__suppress_context__ is True
        assert err.retryable is False


@pytest.mark.parametrize("code", ["ORA-01017", "ORA-12514", "ORA-12541", "DPY-6005", "ORA-28000"])
def test_connection_class_codes(code: str) -> None:
    err = ora.connector_error("source_error", oracledb.DatabaseError(_Err(code)))
    assert err.code == "connection_failed" and err.detail == code


@pytest.mark.parametrize("code", ["ORA-01017", "ORA-01045", "ORA-28000", "ORA-28001"])
def test_refused_logins_are_not_retried(code: str) -> None:
    # A retried wrong password counts towards FAILED_LOGIN_ATTEMPTS and locks the service account.
    err = ora.connector_error("source_error", oracledb.DatabaseError(_Err(code)))
    assert err.code == "connection_failed" and err.retryable is False


@pytest.mark.parametrize(
    "code", ["ORA-12541", "ORA-12514", "ORA-03113", "ORA-03135", "DPY-6005", "DPY-4011", "DPY-4005"]
)
def test_network_failures_are_retried(code: str) -> None:
    err = ora.connector_error("target_error", oracledb.DatabaseError(_Err(code)))
    assert err.code == "connection_failed" and err.retryable is True


@pytest.mark.parametrize("code", ["DPY-4024", "ORA-03156", "ORA-01013"])
def test_call_timeouts_are_statement_timeouts(code: str) -> None:
    err = ora.connector_error("source_error", oracledb.DatabaseError(_Err(code)))
    assert (err.code, err.detail, err.retryable) == ("statement_timeout", code, False)


def test_unknown_driver_shapes_never_leak() -> None:
    assert ora.driver_code(oracledb.DatabaseError("free text SENTINEL")) == "ORA-unknown"
    assert ora.driver_code(oracledb.DatabaseError(_Err("ORA-00001 SENTINEL"))) == "ORA-unknown"
    assert ora.driver_code(ValueError()) == "ORA-unknown"
    assert ora.driver_code(oracledb.DatabaseError(_Err("PLS-00201"))) == "PLS-00201"


def test_acquire_failures_are_value_free_connection_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool, _, _ = _pool(monkeypatch, FakeConn(), oracledb.DatabaseError(_Err("ORA-01017")))
    with pytest.raises(ConnectorError) as info, pool.acquire(action="test"):
        pass
    err = info.value
    assert (err.code, err.detail, err.retryable) == ("connection_failed", "ORA-01017", False)
    assert err.__suppress_context__ is True and "SENTINEL" not in repr(err)


class _Meta:
    def __init__(
        self, type_code: Any, precision: int = 0, scale: int = 0, is_json: bool = False
    ) -> None:
        self.type_code, self.precision, self.scale, self.is_json = (
            type_code,
            precision,
            scale,
            is_json,
        )


class _Cur:
    arraysize = 50

    def var(self, typ: Any, arraysize: int) -> tuple[Any, int]:
        return (typ, arraysize)


@pytest.mark.parametrize(
    "meta,expected",
    [
        (_Meta(oracledb.DB_TYPE_CLOB), (oracledb.DB_TYPE_LONG, 50)),
        (_Meta(oracledb.DB_TYPE_CLOB, is_json=True), (oracledb.DB_TYPE_LONG, 50)),
        (_Meta(oracledb.DB_TYPE_NCLOB), (oracledb.DB_TYPE_LONG_NVARCHAR, 50)),
        (_Meta(oracledb.DB_TYPE_BLOB), (oracledb.DB_TYPE_LONG_RAW, 50)),
        (_Meta(oracledb.DB_TYPE_VARCHAR, is_json=True), (oracledb.DB_TYPE_VARCHAR, 50)),
        (_Meta(oracledb.DB_TYPE_NUMBER, precision=14, scale=2), (decimal.Decimal, 50)),
        (_Meta(oracledb.DB_TYPE_NUMBER, precision=0, scale=-127), (decimal.Decimal, 50)),
        (_Meta(oracledb.DB_TYPE_NUMBER, precision=12, scale=0), None),
        (_Meta(oracledb.DB_TYPE_VARCHAR), None),
    ],
)
def test_output_type_handler(meta: _Meta, expected: Any) -> None:
    assert ora.output_type_handler(_Cur(), meta) == expected  # type: ignore[arg-type]


def test_easy_connect_validates_host_and_service() -> None:
    assert (
        ora.easy_connect(ConnectionParams(host="oracle", port=1521, service_name="FREEPDB1"))
        == "oracle:1521/FREEPDB1"
    )
    assert ora.easy_connect(ConnectionParams(host="db.bank.internal", service_name="CRM")) == (
        "db.bank.internal:1521/CRM"
    )
    for bad in (
        ConnectionParams(host="h?x=1", port=1521, service_name="S"),
        ConnectionParams(host="h", port=1521, service_name="S)(ADDRESS="),
        ConnectionParams(host=None, port=1521, service_name="S"),
        ConnectionParams(host="h", port=1521, service_name=None),
    ):
        with pytest.raises(ConnectorError) as info:
            ora.easy_connect(bad)
        assert info.value.code == "connection_invalid"
