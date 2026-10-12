"""Oracle connector over python-oracledb thin mode (contract §4; spec §7 engine notes).

The pool is synchronous on purpose and used only from worker threads (``asyncio.to_thread``): the Wega
measurement showed blocking driver work on the event loop producing silent duplicates, and a
synchronous pool used only off the loop cannot do that. Pool options are crash-point decisions, not
tuning: ``expire_time=1`` detects a dead peer within a minute, ``call_timeout`` turns a hung call into
an error, ``tcp_connect_timeout`` bounds a black-holed listener, and module/action/client_identifier
make every DPS session attributable in the DBA's audit.

Driver errors never leave this module with their message: python-oracledb messages can quote a value
(Oracle 23ai's ORA-00001 names the duplicate row's values), so :func:`connector_error` keeps only the
error *code* and callers raise it ``from None``.

A session that failed inside a checkout is never touched again by the caller: when the server does
not answer the break of a timed-out call (PL/SQL asleep), the thin driver closes the connection
(DPY-4011), and python-oracledb 26.0.0 crashes the process (SIGBUS) if ``call_timeout`` is then set
on it. :meth:`OraclePool.acquire` rolls back on release and drops a session whose rollback fails,
so such a connection never returns to the pool.
"""

from __future__ import annotations

import contextlib
import decimal
import re
import threading
from collections.abc import Iterator
from typing import Any, Final

import oracledb

from dps.jobs_models import ConnectionParams
from dps.layers.connectors.base import ConnectorError

_EXPIRE_TIME_MIN: Final = 1
_POOL_WAIT_TIMEOUT_MS: Final = 30_000
_IDENTIFIER_MAX: Final = 64  # v$session.module / action / client_identifier hold 64 bytes
_DRIVER_CODE: Final = re.compile(r"(ORA|DPY|TNS|PLS)-\d{4,5}")
_HOST: Final = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?|\[[0-9A-Fa-f:]{2,39}\]")
_SERVICE: Final = re.compile(r"[A-Za-z][A-Za-z0-9_.$#-]{0,127}")
#: Logins the server refused: a retry cannot fix them, and a retried wrong password counts towards
#: the profile's FAILED_LOGIN_ATTEMPTS and locks the service account (invalid credentials, no
#: CREATE SESSION, account locked, password expired, no common authentication protocol).
_LOGIN_REFUSED: Final = frozenset({"ORA-01017", "ORA-01045", "ORA-28000", "ORA-28001", "ORA-28040"})
#: Cannot reach the database, or lost it mid-call: unknown or not-yet-registered service (a PDB
#: still opening), no listener, connect timeout, end of file on the channel, the thin driver's
#: connect failures, a closed connection, and a pool that stayed exhausted for its wait timeout.
_UNREACHABLE: Final = frozenset(
    {
        "ORA-12505",
        "ORA-12514",
        "ORA-12541",
        "ORA-12170",
        "ORA-12537",
        "ORA-12547",
        "ORA-03113",
        "ORA-03114",
        "ORA-03135",
        "TNS-12541",
        "DPY-1001",
        "DPY-4005",
        "DPY-4011",
        "DPY-6000",
        "DPY-6001",
        "DPY-6003",
        "DPY-6005",
    }
)
#: A call that outlived ``call_timeout`` (thin: DPY-4024; thick: ORA-03156 / ORA-01013).
_CALL_TIMEOUT: Final = frozenset({"DPY-4024", "ORA-03156", "ORA-01013"})
INPUT_TYPES: Final[dict[str, Any]] = {
    "char": oracledb.DB_TYPE_CHAR,
    "nchar": oracledb.DB_TYPE_NCHAR,
    "nvarchar": oracledb.DB_TYPE_NVARCHAR,
    "clob": oracledb.DB_TYPE_CLOB,
    "nclob": oracledb.DB_TYPE_NCLOB,
    "blob": oracledb.DB_TYPE_BLOB,
}


def driver_code(exc: BaseException) -> str:
    """The ``ORA-nnnnn`` / ``DPY-nnnn`` code of a driver error — never its message.

    python-oracledb carries an ``_Error`` object as ``args[0]`` whose ``full_code`` is the code; the
    message may quote a value (a constraint violation can echo a key), so it is never read.
    """
    err = exc.args[0] if getattr(exc, "args", None) else None
    full = getattr(err, "full_code", None)
    if isinstance(full, str) and _DRIVER_CODE.fullmatch(full):
        return full
    return "ORA-unknown"


def connector_error(code: str, exc: BaseException) -> ConnectorError:
    """A value-free ConnectorError for ``exc``; raise it ``from None`` so the driver text is dropped.

    ``code`` (``source_error``, ``target_error``, …) applies unless the driver code says otherwise:
    a refused login is ``connection_failed`` (not retryable), an unreachable or lost database is
    ``connection_failed`` (retryable), a call over ``call_timeout`` is ``statement_timeout``.
    """
    dc = driver_code(exc)
    if dc in _LOGIN_REFUSED:
        return ConnectorError("connection_failed", dc)
    if dc in _UNREACHABLE:
        return ConnectorError("connection_failed", dc, retryable=True)
    if dc in _CALL_TIMEOUT:
        return ConnectorError("statement_timeout", dc)
    return ConnectorError(code, dc)


def output_type_handler(cursor: oracledb.Cursor, metadata: oracledb.FetchInfo) -> Any:
    """Fetch types for every DPS read (contract §4.2 Oracle notes).

    CLOB/NCLOB/BLOB come back whole as ``str``/``bytes`` (never a LOB locator addressed by offsets,
    which count UTF-16 units); a VARCHAR2 with an IS JSON check stays text (python-oracledb would
    return a dict); NUMBER with a non-zero or unknown scale is a Decimal (never a float, which
    corrupts ids above 2**53 and amounts' last digits).
    """
    code = metadata.type_code
    if code is oracledb.DB_TYPE_CLOB:
        return cursor.var(oracledb.DB_TYPE_LONG, arraysize=cursor.arraysize)
    if code is oracledb.DB_TYPE_NCLOB:
        return cursor.var(oracledb.DB_TYPE_LONG_NVARCHAR, arraysize=cursor.arraysize)
    if code is oracledb.DB_TYPE_BLOB:
        return cursor.var(oracledb.DB_TYPE_LONG_RAW, arraysize=cursor.arraysize)
    if metadata.is_json:
        return cursor.var(code, arraysize=cursor.arraysize)
    if code is oracledb.DB_TYPE_NUMBER and (metadata.scale != 0 or metadata.precision == 0):
        return cursor.var(decimal.Decimal, arraysize=cursor.arraysize)
    return None


def easy_connect(params: ConnectionParams) -> str:
    """``host:port/service`` from validated parameters (a ``dsn`` secret file overrides it).

    Raises:
        ConnectorError: ``connection_invalid`` — a host or service that is not a plain name (an
            Easy Connect string or a descriptor fragment could otherwise be smuggled in).
    """
    host, service = params.host or "", params.service_name or ""
    if not _HOST.fullmatch(host) or not _SERVICE.fullmatch(service):
        raise ConnectorError("connection_invalid", "host and service_name must be plain names")
    return f"{host}:{params.port or 1521}/{service}"


def _init_session(conn: oracledb.Connection, requested_tag: str | None) -> None:
    """Once per new pooled session: binary collation, so DB key order == Python code-point order."""
    with conn.cursor() as cur:
        cur.execute("ALTER SESSION SET NLS_SORT = BINARY NLS_COMP = BINARY")


class OraclePool:
    """A fixed-ceiling python-oracledb pool, created on first use; checkouts are tagged and rolled
    back. Synchronous: use it only from worker threads (``asyncio.to_thread``)."""

    def __init__(
        self,
        *,
        user: str,
        password: str,
        dsn: str,
        size: int,
        call_timeout_ms: int,
        connect_timeout_s: float,
        module: str,
        client_identifier: str | None,
    ) -> None:
        if size < 1:
            raise ValueError("Oracle pool size must be at least 1")
        self._user, self._password, self._dsn = user, password, dsn
        self._size, self._call_timeout_ms = size, call_timeout_ms
        self._connect_timeout_s, self._module = connect_timeout_s, module[:_IDENTIFIER_MAX]
        self._client_identifier = (client_identifier or "")[:_IDENTIFIER_MAX]
        self._pool: oracledb.ConnectionPool | None = None
        self._lock = threading.Lock()

    def __repr__(self) -> str:
        return f"OraclePool(user={self._user!r}, size={self._size})"

    def _ensure(self) -> oracledb.ConnectionPool:
        with self._lock:
            if self._pool is None:
                self._pool = oracledb.create_pool(
                    user=self._user,
                    password=self._password,
                    dsn=self._dsn,
                    min=0,
                    max=self._size,
                    increment=1,
                    getmode=oracledb.POOL_GETMODE_TIMEDWAIT,
                    wait_timeout=_POOL_WAIT_TIMEOUT_MS,
                    expire_time=_EXPIRE_TIME_MIN,
                    tcp_connect_timeout=self._connect_timeout_s,
                    session_callback=_init_session,
                )
            return self._pool

    @contextlib.contextmanager
    def acquire(
        self, *, action: str, call_timeout_ms: int | None = None
    ) -> Iterator[oracledb.Connection]:
        """Check a session out, tagged; whatever the block left open is rolled back on release.

        A session whose rollback fails is dropped from the pool, never reused.

        Raises:
            ConnectorError: the session could not be opened (value-free, from the driver code).
        """
        try:
            pool = self._ensure()
            conn = pool.acquire()
        except oracledb.Error as exc:
            raise connector_error("connection_failed", exc) from None
        try:
            conn.call_timeout = call_timeout_ms or self._call_timeout_ms
            conn.module = self._module
            conn.action = action[:_IDENTIFIER_MAX]
            conn.client_identifier = self._client_identifier
            yield conn
        finally:
            try:
                conn.rollback()
            except oracledb.Error:
                with contextlib.suppress(oracledb.Error):
                    pool.drop(conn)
            else:
                pool.release(conn)

    def close(self) -> None:
        """Close the pool, forcing its sessions closed. Safe to call twice."""
        with self._lock:
            pool, self._pool = self._pool, None
        if pool is not None:
            with contextlib.suppress(oracledb.Error):
                pool.close(force=True)
