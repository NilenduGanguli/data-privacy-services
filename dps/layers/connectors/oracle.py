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

import asyncio
import contextlib
import decimal
import re
import threading
from collections.abc import AsyncIterator, Callable, Iterator, Mapping
from datetime import UTC, datetime
from typing import Any, Final, Literal, TypeVar

import oracledb
import sqlglot
from sqlglot import exp

from dps.config import Settings
from dps.jobs_models import ConnectionParams, Selection
from dps.layers.connectors import oracle_sql as osql
from dps.layers.connectors.base import (
    ColumnInfo,
    ConnectionTestResult,
    ConnectorError,
    Engine,
    JsonScalar,
    KeyRange,
    ObjectRef,
    Row,
    TableShape,
    WriteMode,
    decode_key,
)
from dps.layers.connectors.selection import (
    SelectionError,
    compile_query,
    compile_where,
    parameterize_literals,
    quote_ident,
    validate_selection,
)
from dps.observability import CONNECTOR_CALLS, get_logger

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
    # TIMESTAMP(0-6): the thin driver binds a bare datetime as DATE and drops the fraction
    "timestamp": oracledb.DB_TYPE_TIMESTAMP,
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


# --- the connector (contract §4.2) ------------------------------------------------------------------

logger = get_logger(__name__)
T = TypeVar("T")
_CAPS: Final = {
    "transactions": True,
    "merge": True,
    "identity": True,
    "sample": True,
    "rowid": True,
    "copy": False,
}
_COLS_SQL: Final = (
    "SELECT column_name, data_type, data_length, char_length, char_used, data_precision, "
    "data_scale, nullable, NVL(default_length, 0), identity_column FROM all_tab_cols "
    "WHERE owner = :o AND table_name = :t AND hidden_column = 'NO' AND virtual_column = 'NO' "
    "AND user_generated = 'YES' ORDER BY column_id"
)
_KEYS_SQL: Final = (
    "SELECT c.constraint_type, c.constraint_name, cc.column_name FROM all_constraints c "
    "JOIN all_cons_columns cc ON cc.owner = c.owner AND cc.constraint_name = c.constraint_name "
    "AND cc.table_name = c.table_name WHERE c.owner = :o AND c.table_name = :t "
    "AND c.constraint_type IN ('P', 'U') AND c.status = 'ENABLED' "
    "ORDER BY CASE c.constraint_type WHEN 'P' THEN 0 ELSE 1 END, c.constraint_name, cc.position"
)
_UIDX_SQL: Final = (
    "SELECT i.index_name, ic.column_name FROM all_indexes i JOIN all_ind_columns ic "
    "ON ic.index_owner = i.owner AND ic.index_name = i.index_name "
    "WHERE i.table_owner = :o AND i.table_name = :t AND i.uniqueness = 'UNIQUE' "
    "AND i.index_type = 'NORMAL' ORDER BY i.index_name, ic.column_position"
)
_ROWS_SQL: Final = "SELECT num_rows FROM all_tables WHERE owner = :o AND table_name = :t"
_LIST_SQL: Final = (
    "SELECT owner, table_name FROM all_tables WHERE {owner_clause} AND nested = 'NO' "
    "AND secondary = 'N' AND table_name NOT LIKE 'BIN$%' AND table_name <> :ledger "
    "ORDER BY owner, table_name FETCH FIRST :lim ROWS ONLY"
)
_PRIV_SQL: Final = (
    "SELECT (SELECT COUNT(*) FROM all_tab_privs WHERE table_schema = :o AND table_name = :t "
    "AND privilege = :p AND grantee IN (USER, 'PUBLIC')) + (SELECT COUNT(*) FROM role_tab_privs "
    "WHERE owner = :o AND table_name = :t AND privilege = :p) FROM dual"
)
_LIST_MAX: Final = 5000
_SAMPLE_PERCENT_MIN: Final = 0.000001  # Oracle refuses a SAMPLE percent below 0.000001
_SAMPLE_FALLBACK_ROWS: Final = 50


def _groups(rows: list[tuple[Any, ...]], name_at: int, col_at: int) -> list[list[str]]:
    """Column lists per constraint/index name, in first-seen order."""
    out: dict[str, list[str]] = {}
    for r in rows:
        out.setdefault(r[name_at], []).append(r[col_at])
    return list(out.values())


def _ts9_text(value: Any) -> Any:
    """A datetime that reached a TIMESTAMP(7-9) value or key → FF9 text (FF9 text passes)."""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%dT%H:%M:%S.%f") + "000"
    return value


def _tz_text(value: Any) -> Any:
    """A datetime that reached a TIMESTAMP WITH TIME ZONE value or key → FF9+offset text.

    The key codec decodes a zoned ``ts`` key of precision ≤ 6 to an aware datetime; the
    statement converts it back with ``TO_TIMESTAMP_TZ(:k, 'YYYY-MM-DD"T"HH24:MI:SS.FF9TZH:TZM')``.
    """
    if not isinstance(value, datetime):
        return value
    aware = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    offset = aware.strftime("%z")  # +HHMM
    return aware.strftime("%Y-%m-%dT%H:%M:%S.%f") + "000" + offset[:3] + ":" + offset[3:]


def _bind_key(col: ColumnInfo, value: Any) -> Any:
    """A key value as its statement binds it (ROWID text, FF9 text, blank-padded CHAR)."""
    if col.pseudo:
        return osql.key_to_rowid(int(value))
    if osql.is_tz(col):
        return _tz_text(value)
    if osql.is_ts9(col):
        return _ts9_text(value)
    if osql.input_type(col) in ("char", "nchar"):
        return osql.pad_char(value, col.length)
    return value


def _sizes(bind_types: Mapping[str, osql.InputType]) -> dict[str, Any]:
    return {name: INPUT_TYPES[t] for name, t in bind_types.items()}


def _check(name: str, ok: bool, severity: str = "required", **extra: Any) -> dict[str, Any]:
    return {"name": name, "ok": ok, "severity": severity, **extra}


class OracleConnector:
    """Contract §4.2 ``Connector`` for Oracle; every driver call runs in a worker thread.

    Reads never lock: one statement and one pooled session per page (no long-held cursor, so no
    ORA-01555 exposure), the next page strictly after the last row's key. Selections are bound,
    never interpolated: literals become ``:lN`` binds (M2's ``parameterize_literals``) and named
    binds take their values from ``params``.
    """

    engine: Engine = "oracle"

    def __init__(self, *, pool: OraclePool, settings: Settings, action: str) -> None:
        self._pool, self._settings, self._action = pool, settings, action
        self._user: str | None = None
        self._version: str | None = None

    @classmethod
    def from_secret(
        cls,
        secret: Mapping[str, str],
        params: ConnectionParams,
        settings: Settings,
        *,
        action: str,
        client_id: str | None,
    ) -> OracleConnector:
        """Build from a resolved secret (``username``, ``password``, optional ``dsn``).

        Raises:
            ConnectorError: ``secret_unavailable`` (no username/password), ``connection_invalid``
                (no ``dsn`` and parameters that are not plain host/service names).
        """
        user, password = secret.get("username", ""), secret.get("password", "")
        if not user or not password:
            raise ConnectorError("secret_unavailable", "the secret has no username or password")
        dsn = (secret.get("dsn") or "").strip() or easy_connect(params)
        pool = OraclePool(
            user=user,
            password=password,
            dsn=dsn,
            size=settings.oracle_pool_max,
            call_timeout_ms=settings.oracle_call_timeout_ms,
            connect_timeout_s=float(settings.connector_connect_timeout_seconds),
            module=settings.oracle_module,
            client_identifier=client_id,
        )
        return cls(pool=pool, settings=settings, action=action)

    def __repr__(self) -> str:
        return f"OracleConnector(action={self._action!r}, pool={self._pool!r})"

    # -- plumbing ---------------------------------------------------------------------------------
    @property
    def _long_ms(self) -> int:
        """``call_timeout`` of whole-selection statements (count, samples, key offsets)."""
        return int(self._settings.connector_statement_timeout_seconds * 1000)

    async def _call(
        self,
        op: str,
        fn: Callable[..., T],
        *args: Any,
        code: str = "source_error",
        **kwargs: Any,
    ) -> T:
        """Run ``fn`` in a worker thread; a driver error becomes a value-free ConnectorError."""
        try:
            result = await asyncio.to_thread(fn, *args, **kwargs)
        except oracledb.Error as exc:
            CONNECTOR_CALLS.labels("oracle", op, "error").inc()
            err = connector_error(code, exc)
            logger.warning("oracle_call_failed", op=op, driver_code=err.detail)
            raise err from None
        except ConnectorError:
            CONNECTOR_CALLS.labels("oracle", op, "error").inc()
            raise
        CONNECTOR_CALLS.labels("oracle", op, "ok").inc()
        return result

    def _rows(
        self,
        sql: str,
        binds: Mapping[str, Any],
        *,
        action: str,
        timeout_ms: int | None = None,
        sizes: Mapping[str, Any] | None = None,
        arraysize: int | None = None,
    ) -> list[tuple[Any, ...]]:
        """One statement on one pooled session; LOBs and JSON come back as text (fetch types)."""
        with self._pool.acquire(action=action, call_timeout_ms=timeout_ms) as conn:
            with conn.cursor() as cur:
                if arraysize:
                    cur.arraysize = arraysize
                    cur.prefetchrows = arraysize
                cur.outputtypehandler = output_type_handler
                if sizes:
                    cur.setinputsizes(**sizes)
                cur.execute(sql, dict(binds))
                return list(cur.fetchall())

    def _ping(self) -> tuple[str, str]:
        with self._pool.acquire(action="connect") as conn, conn.cursor() as cur:
            cur.execute("SELECT USER FROM dual")
            row = cur.fetchone()
            return str(row[0] if row else ""), str(conn.version)

    async def open(self) -> None:
        """Open a session (the pool is created on first use) and learn the session user."""
        self._user, self._version = await self._call(
            "connect", self._ping, code="connection_failed"
        )

    async def close(self) -> None:
        """Close the pool, forcing its sessions closed."""
        await asyncio.to_thread(self._pool.close)

    def _owner(self, obj: ObjectRef) -> ObjectRef:
        """``obj`` with Oracle's folded names; no owner means the session user."""
        name = quote_ident(obj.name, "oracle").strip('"')
        if obj.owner:
            return ObjectRef(owner=quote_ident(obj.owner, "oracle").strip('"'), name=name)
        if self._user is None:
            raise ConnectorError("connection_failed", "the connector is not open")
        return ObjectRef(owner=self._user, name=name)

    # -- metadata ---------------------------------------------------------------------------------
    async def list_objects(self, owner: str | None = None, *, limit: int = 500) -> list[ObjectRef]:
        """Tables the login can see (one owner, or every non-Oracle-maintained one), sorted."""
        if owner:
            clause = "owner = :o"
            binds: dict[str, Any] = {"o": quote_ident(owner, "oracle").strip('"')}
        else:
            clause, binds = (
                "owner IN (SELECT username FROM all_users WHERE oracle_maintained = 'N')",
                {},
            )
        sql = _LIST_SQL.format(owner_clause=clause)
        binds |= {"ledger": osql.LEDGER_TABLE, "lim": max(1, min(int(limit), _LIST_MAX))}
        rows = await self._call("list", self._rows, sql, binds, action="list")
        out: list[ObjectRef] = []
        for o, t in rows:
            try:
                out.append(ObjectRef(owner=o, name=t))
            except ValueError:
                continue  # names DPS cannot quote safely are not offered
        return out

    def _describe_sync(self, obj: ObjectRef) -> TableShape:
        binds = {"o": obj.owner, "t": obj.name}
        with self._pool.acquire(action="describe") as conn, conn.cursor() as cur:
            cur.execute(_COLS_SQL, binds)
            cols = cur.fetchall()
            if not cols:
                raise ConnectorError(
                    "object_not_found", "the table does not exist or is not readable"
                )
            cur.execute(_KEYS_SQL, binds)
            cons = cur.fetchall()
            cur.execute(_UIDX_SQL, binds)
            uidx = cur.fetchall()
            cur.execute(_ROWS_SQL, binds)
            est = cur.fetchone()
        catalog = [
            osql.CatalogColumn(
                name=r[0],
                data_type=r[1],
                data_length=int(r[2] or 0),
                char_length=int(r[3] or 0),
                char_used=r[4],
                data_precision=None if r[5] is None else int(r[5]),
                data_scale=None if r[6] is None else int(r[6]),
                nullable=r[7] == "Y",
                has_default=int(r[8] or 0) > 0,
                is_identity=r[9] == "YES",
            )
            for r in cols
        ]
        not_null = {c.name for c in catalog if not c.nullable}
        key: list[str] = []
        source: Literal["primary_key", "unique_index", "rowid"] = "rowid"
        pk = [r for r in cons if r[0] == "P"]
        if pk:
            key, source = [r[2] for r in pk], "primary_key"
        else:
            for cand in _groups([r for r in cons if r[0] == "U"], 1, 2) + _groups(uidx, 0, 1):
                if set(cand) <= not_null:
                    key, source = cand, "unique_index"
                    break
        columns = [osql.column_info(c, is_key=c.name in key) for c in catalog]
        if not key:
            columns.append(osql.rowid_column())
            key = ["ROWID"]
        estimate = None if not est or est[0] is None else int(est[0])
        return TableShape(
            object=obj, columns=columns, key=key, key_source=source, row_estimate=estimate
        )

    async def describe(self, obj: ObjectRef) -> TableShape:
        """Columns (closed type table), the key (PK, unique NOT NULL, else ROWID), the estimate.

        Raises:
            ConnectorError: ``object_not_found`` when the session cannot see the table.
            SelectionError: ``selection_identifier_invalid`` for a name Oracle cannot take.
        """
        if self._user is None:
            await self.open()
        return await self._call("describe", self._describe_sync, self._owner(obj))

    # -- selection --------------------------------------------------------------------------------
    def _compile(
        self, shape: TableShape, selection: Selection, params: Mapping[str, JsonScalar]
    ) -> tuple[str | None, str | None, dict[str, Any]]:
        """``(where, query, binds)`` of a validated, fully bound selection.

        ``selection.sample`` joins the where as a hash of the key (:func:`osql.sample_filter`),
        so a count, every partition and a resumed page see the same rows.

        Raises:
            SelectionError: ``selection_query_needs_key`` (a query over a key-less table cannot be
                paged), a rule of ``validate_selection``, ``selection_param_missing``.
        """
        if selection.query is not None and shape.key_source == "rowid":
            raise SelectionError(
                "selection_query_needs_key", "a query cannot page a key-less table"
            )
        validate_selection(selection, "oracle", shape)
        bound = parameterize_literals(selection, "oracle")
        generated = {n: v for n, v in bound.params.items() if n not in selection.params}
        where: str | None = None
        query: str | None = None
        names: list[str] = []
        if bound.where is not None:
            where, names = compile_where(bound, "oracle")
        elif bound.query is not None:
            query, names = compile_query(bound, "oracle")
        binds: dict[str, Any] = {}
        for name in names:
            if name in generated:
                binds[name] = generated[name]
            elif name in params:
                binds[name] = params[name]
            else:
                raise SelectionError("selection_param_missing", "a bound parameter has no value")
        if selection.sample is not None:
            hashed = osql.sample_filter(
                osql.key_columns(shape),
                float(selection.sample.percent),
                int(selection.sample.seed or 0),
            )
            where = f"({where}) AND {hashed}" if where else hashed
        return where or None, query, binds

    @staticmethod
    def _projection(shape: TableShape, selection: Selection, query: str | None) -> list[ColumnInfo]:
        """Columns a read yields: a query's own select list, narrowed by ``columns``; keys always."""
        names: set[str] | None = None
        if query is not None:
            parsed = sqlglot.parse_one(query, read="oracle")
            selects = parsed.named_selects if isinstance(parsed, exp.Select) else ["*"]
            if "*" not in selects:
                names = set(selects)
        if selection.columns:
            wanted = {name.upper() for name in selection.columns}
            names = wanted if names is None else names & wanted
        if names is None:
            return list(shape.columns)
        names |= set(shape.key)
        return [c for c in shape.columns if c.name in names]

    @staticmethod
    def _row(columns: list[ColumnInfo], values: tuple[Any, ...]) -> Row:
        row: Row = {}
        for col, value in zip(columns, values, strict=True):
            row[col.name] = osql.rowid_to_key(value) if col.pseudo and value is not None else value
        return row

    @staticmethod
    def _percent(estimate: int | None, n: int) -> float | None:
        """``SAMPLE`` percent drawing about ``2·n`` rows; None for small or unanalysed tables."""
        if estimate is None or estimate <= 2 * n:
            return None
        return min(99.0, max(_SAMPLE_PERCENT_MIN, 200.0 * n / estimate))

    # -- reads ------------------------------------------------------------------------------------
    async def count(
        self, shape: TableShape, selection: Selection, params: dict[str, JsonScalar]
    ) -> int:
        """Rows the selection matches (capped at ``max_rows``)."""
        where, query, binds = self._compile(shape, selection, params)
        sql = osql.count_sql(shape, where=where, query=query, sample=None)
        rows = await self._call(
            "count", self._rows, sql, binds, action="count", timeout_ms=self._long_ms
        )
        total = int(rows[0][0])
        return min(total, selection.max_rows) if selection.max_rows else total

    async def sample(
        self, shape: TableShape, selection: Selection, params: dict[str, JsonScalar], n: int
    ) -> list[Row]:
        """Up to ``n`` rows for discovery: ``SAMPLE`` on large tables, else the first ``n``."""
        where, query, binds = self._compile(shape, selection, params)
        cols = self._projection(shape, selection, query)
        n = max(1, int(n))
        percent = None if query is not None else self._percent(shape.row_estimate, n)
        while True:
            plan = osql.sample_sql(shape, columns=cols, where=where, query=query, percent=percent)
            rows = await self._call(
                "sample",
                self._rows,
                plan.sql,
                {**binds, "n": n},
                action="sample",
                timeout_ms=self._long_ms,
            )
            if percent is None or len(rows) >= min(n, _SAMPLE_FALLBACK_ROWS):
                return [self._row(plan.columns, r) for r in rows]
            percent = None  # a selective where emptied the sample: read without SAMPLE

    async def sample_keys(
        self,
        shape: TableShape,
        selection: Selection,
        params: dict[str, JsonScalar],
        n: int,
        *,
        upper: tuple[Any, ...] | None = None,
    ) -> list[tuple[Any, ...]]:
        """Up to ``n`` keys spread over the selection below ``upper``, in the database's order.

        NVARCHAR2/NCHAR keys return ``[]`` (one partition): Oracle orders them by UTF-16 code
        units, Python by code points, so boundaries could overlap for supplementary characters.
        """
        keys = osql.key_columns(shape)
        if any(osql.input_type(k) in ("nchar", "nvarchar") for k in keys):
            return []
        where, query, binds = self._compile(shape, selection, params)
        plan = osql.sample_sql(
            shape,
            columns=keys,
            where=where,
            query=query,
            percent=None if query is not None else self._percent(shape.row_estimate, 10 * n),
            keys_only=True,
            upper=upper is not None,
        )
        b: dict[str, Any] = {**binds, "n": max(1, int(n))}
        if upper is not None:
            for i, (col, value) in enumerate(zip(keys, upper, strict=True)):
                b[f"hi{i}"] = _bind_key(col, value)
        rows = await self._call(
            "sample_keys",
            self._rows,
            plan.sql,
            b,
            action="sample",
            timeout_ms=self._long_ms,
            sizes=_sizes(plan.bind_types),
        )
        out = [tuple(self._row(plan.columns, r)[k.name] for k in keys) for r in rows]
        return out[: max(1, int(n))]

    async def key_at_offset(
        self,
        shape: TableShape,
        selection: Selection,
        params: dict[str, JsonScalar],
        offset: int,
    ) -> tuple[Any, ...] | None:
        """The key at ``offset`` (0-based) in key order, or None past the end."""
        where, query, binds = self._compile(shape, selection, params)
        keys = osql.key_columns(shape)
        plan = osql.read_sql(
            shape,
            columns=keys,
            where=where,
            query=query,
            sample=None,
            lower=False,
            upper=False,
            after=False,
        )
        sql = plan.sql.replace(
            "FETCH FIRST :batch ROWS ONLY", "OFFSET :off ROWS FETCH NEXT 1 ROWS ONLY"
        )
        rows = await self._call(
            "key_at_offset",
            self._rows,
            sql,
            {**binds, "off": max(0, int(offset))},
            action="count",
            timeout_ms=self._long_ms,
        )
        return tuple(self._row(plan.columns, rows[0])[k.name] for k in keys) if rows else None

    async def read_partition(
        self,
        shape: TableShape,
        selection: Selection,
        params: dict[str, JsonScalar],
        key_range: KeyRange,
        *,
        after_key: tuple[Any, ...] | None,
        batch: int,
    ) -> AsyncIterator[list[Row]]:
        """Keyset pages of ``[lower, upper)`` after ``after_key``, ordered by key; never locks.

        One statement (``FETCH FIRST :batch``) and one pooled session per page, ``arraysize =
        prefetchrows = batch``; CHAR keys bind typed and padded, TIMESTAMP(7-9) keys as FF9 text,
        ROWID keys through ``CHARTOROWID``.
        """
        where, query, binds = self._compile(shape, selection, params)
        cols = self._projection(shape, selection, query)
        keys = osql.key_columns(shape)
        lower = decode_key(key_range.lower, keys) if key_range.lower else None
        upper = decode_key(key_range.upper, keys) if key_range.upper else None
        size = max(1, int(batch))
        after = after_key
        while True:
            plan = osql.read_sql(
                shape,
                columns=cols,
                where=where,
                query=query,
                sample=None,
                lower=lower is not None,
                upper=upper is not None,
                after=after is not None,
            )
            b: dict[str, Any] = {**binds, "batch": size}
            for prefix, values in (("lo", lower), ("hi", upper), ("af", after)):
                if values is not None:
                    for i, (col, value) in enumerate(zip(keys, values, strict=True)):
                        b[f"{prefix}{i}"] = _bind_key(col, value)
            raw = await self._call(
                "read",
                self._rows,
                plan.sql,
                b,
                action=self._action,
                sizes=_sizes(plan.bind_types),
                arraysize=size,
            )
            if not raw:
                return
            rows = [self._row(plan.columns, r) for r in raw]
            yield rows
            if len(rows) < size:
                return
            after = tuple(rows[-1][k.name] for k in keys)

    # -- connection test --------------------------------------------------------------------------
    def _test_sync(
        self, role: str, obj: ObjectRef | None, write_mode: str | None
    ) -> list[dict[str, Any]]:
        checks: list[dict[str, Any]] = []
        with self._pool.acquire(action="test") as conn, conn.cursor() as cur:
            cur.execute("SELECT USER FROM dual")
            row = cur.fetchone()
            user = str(row[0]) if row else ""
            cur.execute("SELECT privilege FROM session_privs")
            privs = {r[0] for r in cur.fetchall()}
            checks.append(_check("session_tagged", True, "advisory"))
            if obj is None:
                return checks
            o = self._owner(obj)
            table = osql.qualified(o)
            own_ddl = (o.owner == user and "CREATE TABLE" in privs) or "CREATE ANY TABLE" in privs

            def can(priv: str) -> bool:
                if o.owner == user or f"{priv} ANY TABLE" in privs:
                    return True
                cur.execute(_PRIV_SQL, {"o": o.owner, "t": o.name, "p": priv})
                got = cur.fetchone()
                return bool(got and int(got[0]) > 0)

            def run(name: str, fn: Callable[[], bool], severity: str = "required") -> None:
                try:
                    checks.append(_check(name, bool(fn()), severity))
                except oracledb.Error as exc:
                    checks.append(_check(name, False, severity, code=driver_code(exc)))

            def selectable() -> bool:
                cur.execute(f"SELECT 1 FROM {table} WHERE 1 = 0")  # noqa: S608 - quoted names
                return True

            if role == "source":
                run("select_source", selectable)
                run(
                    "no_dml_on_source",
                    lambda: not any(can(p) for p in ("INSERT", "UPDATE", "DELETE")),
                    "advisory",
                )
                return checks
            cur.execute(_ROWS_SQL, {"o": o.owner, "t": o.name})
            present = cur.fetchone() is not None
            if write_mode == "table" and not present:
                run("create_target", lambda: own_ddl)
            elif write_mode in ("table", "append"):
                run("insert_target", lambda: can("INSERT"))
                if write_mode == "table":
                    run("update_target", lambda: can("UPDATE"))
            elif write_mode == "in_place":
                run("select_target", selectable)
                run("update_target", lambda: can("UPDATE"))
            if write_mode in ("in_place", "append"):
                run("create_ledger", lambda: own_ddl)
        return checks

    async def test(
        self,
        *,
        role: Literal["source", "target"],
        obj: ObjectRef | None = None,
        write_mode: WriteMode | None = None,
    ) -> ConnectionTestResult:
        """Value-free connectivity and privilege checks; never raises for a refused login."""
        try:
            await self.open()
        except ConnectorError as err:
            return ConnectionTestResult(
                ok=False,
                engine="oracle",
                checks=[_check("connect", False, code=err.code, detail=err.detail)],
                capabilities=_CAPS,
                error_code="connection_failed",
            )
        checks = [_check("connect", True)]
        try:
            checks += await self._call("test", self._test_sync, role, obj, write_mode)
        except ConnectorError as err:
            checks.append(_check("privileges", False, code=err.code, detail=err.detail))
        ok = all(c["ok"] for c in checks if c["severity"] == "required")
        return ConnectionTestResult(
            ok=ok,
            engine="oracle",
            server_version=self._version,
            checks=checks,
            capabilities=_CAPS,
        )
