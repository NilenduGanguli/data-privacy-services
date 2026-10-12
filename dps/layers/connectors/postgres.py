"""PostgreSQL connector (asyncpg) — read side and writer (contract §4, spec §7 engine notes).

Sessions. The read pool's sessions start with ``default_transaction_read_only=on``,
``statement_timeout`` (``DPS_CONNECTOR_STATEMENT_TIMEOUT_SECONDS``), ``lock_timeout=5s``,
``idle_in_transaction_session_timeout`` (twice the statement timeout) and ``application_name =
dps:<action>:<job id>``, so a DBA's ``pg_stat_activity`` shows DPS. Every read runs inside its own
``BEGIN READ ONLY`` transaction; writers (Task 19) get a separate read-write pool.

Reads. Keyset paging uses row-value comparison (``key >= lower AND key < upper AND key > after
ORDER BY key LIMIT batch``), one short read-only transaction per page and no long snapshot; every
ordering comes from ``ORDER BY`` in the database (its collation), never from Python. Bind values
are coerced with the prepared statement's parameter types, because asyncpg will not turn a JSON
string into a ``date``/``timestamptz``/``numeric`` parameter by itself. ``selection.sample`` is a
deterministic, update-stable filter on a hash of the key; ``TABLESAMPLE`` is used only for
discovery samples and key sampling (an in-place update moves tuples, which would change it).

Writes (:class:`PostgresWriter`, on the separate read-write pool). For ``in_place`` and ``append``
every ``write_batch`` is ONE transaction: lock the partition's row in the target's write ledger
(``dps_write_ledger`` in the target schema or ``ledger_owner``), compare its batch number with the
one this writer last saw (a mismatch means another holder wrote meanwhile → :class:`LedgerConflict`,
nothing written), apply the rows, upsert the ledger mark, commit. The mark is therefore always the
last applied batch, so a resumed partition never applies a batch twice (re-obfuscating an
obfuscated row would corrupt it irreversibly). ``in_place`` is one set-based ``UPDATE … FROM
unnest(…)`` (key columns never in the ``SET`` list); ``append`` is a binary ``COPY`` (a unique
violation is :class:`PkCollision`); ``table`` stages rows in a session temp table with binary
``COPY`` and upserts with ``INSERT … SELECT … ON CONFLICT (key)`` — idempotent, so it needs no
ledger. Column types reach SQL casts only after :data:`~dps.layers.connectors.targets.TYPE_RE`.

Errors carry a stable code and an exception class name only — never a driver message, a value, a
key or a credential. Identifiers are interpolated only through ``selection.quote_ident``.
"""

from __future__ import annotations

import hashlib
import json
import ssl
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, time
from decimal import Decimal, InvalidOperation
from typing import Any, Literal
from uuid import UUID

import asyncpg

from dps.config import Settings
from dps.connections import Connection, Credentials
from dps.db import rowcount
from dps.engine.jsonpath import child_path
from dps.jobs_models import Selection
from dps.layers.connectors.base import (
    BatchWriter,
    ColumnInfo,
    ColumnKind,
    ConnectionTestResult,
    ConnectorError,
    Engine,
    JsonScalar,
    KeyRange,
    LedgerMark,
    ObjectRef,
    PrepareReport,
    ResolvedTarget,
    Row,
    TableShape,
    WriteMode,
    decode_key,
)
from dps.layers.connectors.selection import compile_query, compile_where, quote_ident
from dps.layers.connectors.targets import TYPE_RE, PrepareFailure
from dps.secrets import SecretUnavailable, resolve_secret_ref

PG_KINDS: dict[str, ColumnKind] = {
    "int2": "int",
    "int4": "int",
    "int8": "int",
    "numeric": "decimal",
    "float4": "float",
    "float8": "float",
    "text": "text",
    "varchar": "text",
    "citext": "text",
    "name": "text",
    "bpchar": "char",
    "date": "date",
    "timestamp": "timestamp",
    "timestamptz": "timestamp",
    "bool": "bool",
    "bytea": "binary",
    "json": "json",
    "jsonb": "json",
    "uuid": "uuid",
}
#: Failures worth a retry (the server or the network, not the request).
_RETRYABLE: tuple[type[BaseException], ...] = (
    asyncpg.ConnectionDoesNotExistError,
    asyncpg.TooManyConnectionsError,
    asyncpg.CannotConnectNowError,
    ConnectionError,
    OSError,
    TimeoutError,
)
#: Login failures that a retry cannot fix (wrong password, no such role or database, TLS refused).
_LOGIN_REFUSED: tuple[type[BaseException], ...] = (
    asyncpg.InvalidAuthorizationSpecificationError,
    asyncpg.InvalidCatalogNameError,
    ssl.SSLError,
)
#: Non-core functions that execute a SQL string or export data. The selection validator refuses
#: them, but a source login able to run them is not least-privilege (advisory test check).
_SQL_STRING_FUNCTIONS = ("connectby", "xpath_table", "pg_background_launch", "query_export_to_s3")
_SQL_STRING_PREFIXES = ("dblink%", "crosstab%")
_LIMIT_BATCH_MAX = 1_000_000


def _q(name: str) -> str:
    return quote_ident(name, "postgres")


def coerce_bind(value: Any, pg_type: str) -> Any:
    """Turn a JSON scalar into the Python type asyncpg needs for a parameter of ``pg_type``.

    Values that are not JSON scalars (decoded keys, row values) pass through unchanged.

    Raises:
        ValueError: the value cannot be read as ``pg_type`` (the message names the type only).
    """
    if value is None or not isinstance(value, str | int | float | bool):
        return value
    try:
        if pg_type in ("int2", "int4", "int8"):
            if isinstance(value, float) and not value.is_integer():
                raise ValueError
            return int(value)
        if pg_type == "numeric":
            return Decimal(str(value))
        if pg_type in ("float4", "float8"):
            return float(value)
        if pg_type == "bool":
            if isinstance(value, bool):
                return value
            text = str(value).strip().lower()
            if text in ("true", "t", "1", "yes", "y", "on"):
                return True
            if text in ("false", "f", "0", "no", "n", "off"):
                return False
            raise ValueError
        if pg_type == "date":
            return date.fromisoformat(str(value))
        if pg_type in ("timestamp", "timestamptz"):
            dt = datetime.fromisoformat(str(value))
            if pg_type == "timestamptz" and dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            return dt
        if pg_type == "time":
            return time.fromisoformat(str(value))
        if pg_type == "uuid":
            return UUID(str(value))
        if pg_type == "bytea":
            return bytes.fromhex(str(value))
    except (ValueError, InvalidOperation, OverflowError):
        raise ValueError(f"a bound value is not a valid {pg_type}") from None
    return value if isinstance(value, str) else str(value)


class _Binds:
    """Bind positions that continue after the body's ``$1..$n``."""

    def __init__(self, start: list[Any]) -> None:
        self.args = list(start)

    def row(self, values: tuple[Any, ...]) -> str:
        marks = []
        for v in values:
            self.args.append(v)
            marks.append(f"${len(self.args)}")
        return marks[0] if len(marks) == 1 else "(" + ", ".join(marks) + ")"


def select_sql(
    shape: TableShape,
    sel: Selection,
    *,
    projection: list[ColumnInfo],
    body_sql: str,
    body_args: list[Any],
    lower: tuple[Any, ...] | None = None,
    upper: tuple[Any, ...] | None = None,
    after: tuple[Any, ...] | None = None,
    limit: int | None = None,
    count: bool = False,
    order: bool = True,
    tablesample: float | None = None,
) -> tuple[str, list[Any]]:
    """Read/count SQL over a table (``body_sql`` = compiled where) or a wrapped query
    (``body_sql`` = compiled query); keyset bounds bind after ``body_args``.

    Raises:
        SelectionError: an identifier that cannot be quoted safely.
    """
    alias = "q" if sel.query else "t"
    binds = _Binds(body_args)
    if sel.query:
        source = f"({body_sql}) AS q"
        conds: list[str] = []
    else:
        sample = f" TABLESAMPLE SYSTEM ({tablesample:.4f})" if tablesample else ""
        source = f"{_q(shape.object.owner or 'public')}.{_q(shape.object.name)} AS t{sample}"
        conds = [f"({body_sql})"] if body_sql else []
    keys = [f"{alias}.{_q(k)}" for k in shape.key]
    key_expr = keys[0] if len(keys) == 1 else "(" + ", ".join(keys) + ")"
    if lower is not None:
        conds.append(f"{key_expr} >= {binds.row(lower)}")
    if upper is not None:
        conds.append(f"{key_expr} < {binds.row(upper)}")
    if after is not None:
        conds.append(f"{key_expr} > {binds.row(after)}")
    if sel.sample is not None:
        texts = ", ".join(f"{k}::text" for k in keys)
        conds.append(
            f"(abs(hashtextextended(concat_ws('|', {texts}), {int(sel.sample.seed or 0)})) "
            f"% 1000000) < {int(round(sel.sample.percent * 10000))}"
        )
    cols = "count(*)" if count else ", ".join(f"{alias}.{_q(c.name)}" for c in projection)
    sql = f"SELECT {cols} FROM {source}" + (" WHERE " + " AND ".join(conds) if conds else "")
    if order and not count and keys:
        sql += " ORDER BY " + ", ".join(keys)
    if limit is not None:
        sql += f" LIMIT {int(limit)}"
    return sql, binds.args


def _typmod_fields(typname: str, typmod: int) -> dict[str, int | None]:
    if typname in ("varchar", "bpchar") and typmod > 4:
        return {"length": typmod - 4}
    if typname == "numeric" and typmod >= 4:
        return {"precision": ((typmod - 4) >> 16) & 0xFFFF, "scale": (typmod - 4) & 0xFFFF}
    if typname in ("timestamp", "timestamptz"):
        return {"ts_precision": typmod if typmod >= 0 else 6}
    return {}


def _check(name: str, ok: bool, severity: str, **extra: Any) -> dict[str, Any]:
    return {"name": name, "ok": bool(ok), "severity": severity, **extra}


def _require_key(shape: TableShape) -> None:
    if not shape.key:
        raise ConnectorError("no_key", "the object has no primary key or unique NOT NULL index")


class PostgresConnector:
    """``Connector`` for PostgreSQL (contract §4.2): reads, and :class:`PostgresWriter`."""

    engine: Engine = "postgres"

    def __init__(
        self,
        conn: Connection,
        credentials: Credentials,
        settings: Settings,
        *,
        action: str,
        job_id: str | None = None,
    ) -> None:
        self.conn, self._creds, self.settings = conn, credentials, settings
        self.action, self.job_id = action, job_id
        self._read: asyncpg.Pool | None = None
        self._write: asyncpg.Pool | None = None
        self._param_types: dict[str, list[str]] = {}

    # -- sessions -------------------------------------------------------------------------------
    def _ssl(self) -> ssl.SSLContext | bool | str:
        mode = self.conn.params.options.get("sslmode", "prefer")
        if mode == "disable":
            return False
        if mode in ("allow", "prefer"):
            return mode  # asyncpg tries TLS and falls back, as libpq does
        if mode in ("verify-ca", "verify-full"):
            ref = self.conn.params.options.get("sslrootcert_ref")
            try:
                cadata = resolve_secret_ref(ref, self.settings).get("ca.pem") if ref else None
            except SecretUnavailable:
                raise ConnectorError(
                    "secret_unavailable", "the CA bundle reference does not resolve"
                ) from None
            ctx = (
                ssl.create_default_context(cadata=cadata)
                if cadata
                else ssl.create_default_context()
            )
            ctx.check_hostname = mode == "verify-full"
            return ctx
        if mode == "require":
            ctx = ssl.create_default_context()
            ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
            return ctx
        raise ConnectorError("connection_invalid", "unsupported sslmode")

    def _server_settings(self, *, read_only: bool) -> dict[str, str]:
        ms = int(self.settings.connector_statement_timeout_seconds * 1000)
        out = {
            "application_name": f"dps:{self.action}:{self.job_id or '-'}"[:63],
            "statement_timeout": str(ms),
            "lock_timeout": "5000",
            "idle_in_transaction_session_timeout": str(ms * 2),
            "timezone": "UTC",
        }
        if read_only:
            out["default_transaction_read_only"] = "on"
        return out

    async def _pool(self, *, read_only: bool) -> asyncpg.Pool:
        p = self.conn.params
        tls = self._ssl()
        try:
            return await asyncpg.create_pool(
                host=p.host,
                port=p.port or 5432,
                database=p.database,
                user=self._creds.username,
                password=self._creds.password.get_secret_value() or None,
                ssl=tls,
                min_size=1,
                max_size=self.settings.worker_concurrency + 1,
                timeout=self.settings.connector_connect_timeout_seconds,
                command_timeout=self.settings.connector_statement_timeout_seconds + 5,
                server_settings=self._server_settings(read_only=read_only),
            )
        except _LOGIN_REFUSED as exc:
            raise ConnectorError("connection_failed", type(exc).__name__) from None
        except (*_RETRYABLE, asyncpg.PostgresError, asyncpg.InterfaceError) as exc:
            raise ConnectorError("connection_failed", type(exc).__name__, retryable=True) from None

    async def open(self) -> None:
        """Create the read-only pool (idempotent)."""
        if self._read is None:
            self._read = await self._pool(read_only=True)

    async def write_pool(self) -> asyncpg.Pool:
        """A separate read-write pool (writers only); never used for source reads."""
        if self._write is None:
            self._write = await self._pool(read_only=False)
        return self._write

    async def close(self) -> None:
        """Close both pools."""
        for pool in (self._read, self._write):
            if pool is not None:
                await pool.close()
        self._read = self._write = None

    @asynccontextmanager
    async def read_transaction(self) -> AsyncIterator[asyncpg.Connection]:
        """``BEGIN READ ONLY`` on a read-only, time-limited session."""
        if self._read is None:
            raise RuntimeError("open() the connector first")
        async with self._read.acquire() as conn, conn.transaction(readonly=True):
            yield conn

    async def _fetch(
        self, conn: asyncpg.Connection, sql: str, args: list[Any]
    ) -> list[asyncpg.Record]:
        try:
            stmt = await conn.prepare(sql)
            types = self._param_types.setdefault(sql, [t.name for t in stmt.get_parameters()])
            if len(types) != len(args):
                raise ConnectorError("source_error", "bind count mismatch")
            try:
                coerced = [coerce_bind(v, t) for v, t in zip(args, types, strict=True)]
            except ValueError:
                raise ConnectorError(
                    "selection_param_invalid", "a bound value does not match its parameter type"
                ) from None
            return list(await stmt.fetch(*coerced))
        except asyncpg.QueryCanceledError:
            raise ConnectorError(
                "statement_timeout", "the statement exceeded the connector timeout"
            ) from None
        except _RETRYABLE as exc:
            raise ConnectorError("source_error", type(exc).__name__, retryable=True) from None
        except asyncpg.UndefinedTableError:
            raise ConnectorError(
                "object_not_found", "the table does not exist or is not readable"
            ) from None
        except (asyncpg.PostgresError, asyncpg.InterfaceError) as exc:
            raise ConnectorError("source_error", type(exc).__name__) from None

    async def fetch_internal(self, sql: str, args: list[Any]) -> list[asyncpg.Record]:
        """Run one read in its own read-only transaction (tests, writers' privilege checks)."""
        async with self.read_transaction() as conn:
            return await self._fetch(conn, sql, args)

    # -- catalogue ------------------------------------------------------------------------------
    async def list_objects(self, owner: str | None = None, *, limit: int = 500) -> list[ObjectRef]:
        """Ordinary and partitioned tables the login may SELECT (DPS bookkeeping tables hidden)."""
        rows = await self.fetch_internal(
            "SELECT n.nspname AS owner, c.relname AS name FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE c.relkind IN ('r', 'p') AND n.nspname NOT IN ('pg_catalog', 'information_schema') "
            "AND n.nspname NOT LIKE 'pg\\_%' "
            "AND c.relname NOT IN ('dps_write_ledger', '_dps_seed_info') "
            "AND has_table_privilege(c.oid, 'SELECT') AND ($1::text IS NULL OR n.nspname = $1) "
            "ORDER BY 1, 2 LIMIT $2",
            [owner, max(1, min(limit, 5000))],
        )
        out = []
        for r in rows:
            try:
                out.append(ObjectRef(owner=r["owner"], name=r["name"]))
            except ValueError:
                continue  # names DPS cannot quote safely are not offered
        return out

    async def describe(self, obj: ObjectRef) -> TableShape:
        """Columns (kinds, lengths, precision), the paging key and the planner's row estimate."""
        owner = obj.owner or "public"
        cols = await self.fetch_internal(
            "SELECT a.attname, format_type(a.atttypid, a.atttypmod) AS data_type, t.typname, "
            "a.atttypmod, a.attnotnull, a.atthasdef, a.attidentity <> '' AS is_identity "
            "FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid "
            "JOIN pg_namespace n ON n.oid = c.relnamespace JOIN pg_type t ON t.oid = a.atttypid "
            "WHERE n.nspname = $1 AND c.relname = $2 AND c.relkind IN ('r', 'p') "
            "AND a.attnum > 0 AND NOT a.attisdropped "
            "AND has_table_privilege(c.oid, 'SELECT') ORDER BY a.attnum",
            [owner, obj.name],
        )
        if not cols:
            raise ConnectorError("object_not_found", "the table does not exist or is not readable")
        idx = await self.fetch_internal(
            "SELECT i.indisprimary, array_agg(a.attname ORDER BY k.ord) AS cols, "
            "bool_and(a.attnotnull) AS not_null "
            "FROM pg_index i JOIN pg_class c ON c.oid = i.indrelid "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "JOIN pg_class ic ON ic.oid = i.indexrelid "
            "JOIN LATERAL unnest(i.indkey::int2[]) WITH ORDINALITY AS k(attnum, ord) "
            "ON k.ord <= i.indnkeyatts "
            "JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = k.attnum "
            "WHERE n.nspname = $1 AND c.relname = $2 AND (i.indisprimary OR i.indisunique) "
            "AND i.indexprs IS NULL AND i.indpred IS NULL AND i.indisvalid "
            "GROUP BY i.indisprimary, ic.relname, i.indnkeyatts "
            "ORDER BY i.indisprimary DESC, i.indnkeyatts, ic.relname",
            [owner, obj.name],
        )
        key: list[str] = []
        source: Literal["primary_key", "unique_index", "rowid", "none"] = "none"
        for r in idx:
            if r["indisprimary"] or r["not_null"]:
                key = list(r["cols"])
                source = "primary_key" if r["indisprimary"] else "unique_index"
                break
        estimate = await self.fetch_internal(
            "SELECT c.reltuples::bigint AS n FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = $1 AND c.relname = $2",
            [owner, obj.name],
        )
        columns = [
            ColumnInfo(
                path=child_path("$", r["attname"]),
                name=r["attname"],
                data_type=r["data_type"],
                kind=PG_KINDS.get(r["typname"], "other"),
                nullable=not r["attnotnull"],
                is_key=r["attname"] in key,
                has_default=r["atthasdef"],
                is_identity=r["is_identity"],
                **_typmod_fields(r["typname"], r["atttypmod"]),
            )
            for r in cols
        ]
        n = estimate[0]["n"] if estimate else None
        return TableShape(
            object=ObjectRef(owner=owner, name=obj.name),
            columns=columns,
            key=key,
            key_source=source,
            row_estimate=int(n) if n is not None and n >= 0 else None,
        )

    # -- reads ----------------------------------------------------------------------------------
    def _body(self, sel: Selection) -> tuple[str, list[str]]:
        return compile_query(sel, "postgres") if sel.query else compile_where(sel, "postgres")

    def _args(self, sel: Selection, params: dict[str, JsonScalar]) -> tuple[str, list[Any]]:
        body, names = self._body(sel)
        missing = [n for n in names if n not in params]
        if missing:
            raise ConnectorError("selection_param_missing", "a bind parameter has no value")
        return body, [params[n] for n in names]

    def _projection(self, shape: TableShape, sel: Selection) -> list[ColumnInfo]:
        if not sel.columns:
            return shape.columns
        wanted = set(sel.columns) | set(shape.key)
        return [c for c in shape.columns if c.name in wanted]

    async def count(
        self, shape: TableShape, selection: Selection, params: dict[str, JsonScalar]
    ) -> int:
        """Rows the selection matches (capped at ``max_rows``)."""
        body, args = self._args(selection, params)
        sql, a = select_sql(
            shape, selection, projection=[], body_sql=body, body_args=args, count=True
        )
        rows = await self.fetch_internal(sql, a)
        total = int(rows[0][0])
        return min(total, selection.max_rows) if selection.max_rows else total

    async def sample(
        self, shape: TableShape, selection: Selection, params: dict[str, JsonScalar], n: int
    ) -> list[Row]:
        """Up to ``n`` rows for discovery: ``TABLESAMPLE SYSTEM`` on large tables, else random."""
        body, args = self._args(selection, params)
        proj = self._projection(shape, selection)
        n = max(1, int(n))
        estimate = shape.row_estimate or 0
        if not selection.query and estimate > n * 20:
            pct = min(100.0, max(0.01, 100.0 * n * 4 / estimate))
            sql, a = select_sql(
                shape,
                selection,
                projection=proj,
                body_sql=body,
                body_args=args,
                limit=n,
                order=False,
                tablesample=pct,
            )
            rows = await self.fetch_internal(sql, a)
            if len(rows) >= min(n, 50):
                return [dict(r) for r in rows]
        sql, a = select_sql(
            shape, selection, projection=proj, body_sql=body, body_args=args, order=False
        )
        rows = await self.fetch_internal(sql + f" ORDER BY random() LIMIT {n}", a)
        return [dict(r) for r in rows]

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

        ``row_number() OVER (ORDER BY key)`` ranks in the database collation and every
        ``ceil(total / n)``-th key is kept, ``total`` being the size of the same bounded set (one
        scan). Above two million estimated rows the inner select reads a ``TABLESAMPLE SYSTEM``
        of about ``20·n`` rows instead: the boundaries are then approximate quantiles, which only
        affects partition balance, never coverage.
        """
        _require_key(shape)
        body, args = self._args(selection, params)
        n = max(1, int(n))
        estimate = shape.row_estimate or 0
        sampled = (
            None if selection.query or estimate <= 2_000_000 else min(100.0, 2000.0 * n / estimate)
        )
        inner, a = select_sql(
            shape,
            selection,
            projection=shape.key_columns(),
            body_sql=body,
            body_args=args,
            upper=upper,
            order=False,
            tablesample=sampled,
        )
        cols = ", ".join(_q(k) for k in shape.key)
        sql = (
            f"SELECT {cols} FROM (SELECT {cols}, row_number() OVER (ORDER BY {cols}) AS rn, "
            f"count(*) OVER () AS total FROM ({inner}) s0) s "
            f"WHERE s.rn % GREATEST(1, CEIL(s.total::numeric / {n})::bigint) = 0 ORDER BY s.rn"
        )
        rows = await self.fetch_internal(sql, a)
        return [tuple(r[k] for k in shape.key) for r in rows][:n]

    async def key_at_offset(
        self,
        shape: TableShape,
        selection: Selection,
        params: dict[str, JsonScalar],
        offset: int,
    ) -> tuple[Any, ...] | None:
        """The key at ``offset`` (0-based) in key order, or None past the end."""
        _require_key(shape)
        body, args = self._args(selection, params)
        sql, a = select_sql(
            shape, selection, projection=shape.key_columns(), body_sql=body, body_args=args
        )
        rows = await self.fetch_internal(sql + f" OFFSET {max(0, int(offset))} LIMIT 1", a)
        return tuple(rows[0][k] for k in shape.key) if rows else None

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
        """Keyset pages within ``[lower, upper)`` after ``after_key``; one read-only transaction
        per page."""
        _require_key(shape)
        body, args = self._args(selection, params)
        cols = shape.key_columns()
        lower = decode_key(key_range.lower, cols) if key_range.lower else None
        upper = decode_key(key_range.upper, cols) if key_range.upper else None
        proj = self._projection(shape, selection)
        size = max(1, min(int(batch), _LIMIT_BATCH_MAX))
        cursor = after_key
        while True:
            sql, a = select_sql(
                shape,
                selection,
                projection=proj,
                body_sql=body,
                body_args=args,
                lower=lower,
                upper=upper,
                after=cursor,
                limit=size,
            )
            rows = await self.fetch_internal(sql, a)
            if not rows:
                return
            yield [dict(r) for r in rows]
            cursor = tuple(rows[-1][k] for k in shape.key)
            if len(rows) < size:
                return

    # -- connection test ------------------------------------------------------------------------
    async def _table(self, conn: asyncpg.Connection, obj: ObjectRef) -> asyncpg.Record | None:
        return await conn.fetchrow(
            "SELECT c.oid, c.reltuples::bigint AS n FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = $1 AND c.relname = $2 AND c.relkind IN ('r', 'p')",
            obj.owner or "public",
            obj.name,
        )

    @staticmethod
    async def _table_priv(conn: asyncpg.Connection, oid: int | None, privs: str) -> bool:
        if oid is None:
            return False
        return bool(await conn.fetchval("SELECT has_table_privilege($1::oid, $2)", oid, privs))

    @staticmethod
    async def _schema_create(conn: asyncpg.Connection, schema: str) -> bool:
        ok = await conn.fetchval(
            "SELECT has_schema_privilege(n.oid, 'CREATE') FROM pg_namespace n WHERE n.nspname = $1",
            schema,
        )
        return bool(ok)

    async def _source_checks(
        self, conn: asyncpg.Connection, obj: ObjectRef | None
    ) -> list[dict[str, Any]]:
        checks: list[dict[str, Any]] = []
        if obj is not None:
            table = await self._table(conn, obj)
            oid = table["oid"] if table else None
            if table is None:
                checks.append(_check("select_source", False, "required", detail="missing"))
            else:
                n = int(table["n"])
                detail = f"{n} rows by statistics" if n >= 0 else "no statistics"
                checks.append(
                    _check(
                        "select_source",
                        await self._table_priv(conn, oid, "SELECT"),
                        "required",
                        detail=detail,
                    )
                )
            dml = await self._table_priv(conn, oid, "INSERT, UPDATE, DELETE, TRUNCATE")
            checks.append(_check("no_dml_on_source", table is not None and not dml, "advisory"))
        held = await conn.fetchval(
            "SELECT count(*) FROM pg_proc p WHERE (p.proname = ANY($1::text[]) "
            "OR p.proname LIKE ANY($2::text[])) AND has_function_privilege(p.oid, 'EXECUTE')",
            list(_SQL_STRING_FUNCTIONS),
            list(_SQL_STRING_PREFIXES),
        )
        checks.append(
            _check("no_sql_string_functions", not held, "advisory", detail=f"{int(held)} held")
        )
        return checks

    async def _target_checks(
        self, conn: asyncpg.Connection, obj: ObjectRef | None, write_mode: WriteMode | None
    ) -> list[dict[str, Any]]:
        if obj is None or write_mode in (None, "files"):
            return []
        owner = obj.owner or "public"
        table = await self._table(conn, obj)
        oid = table["oid"] if table else None
        checks: list[dict[str, Any]] = []
        if write_mode == "table":
            if table is None:
                ok = await self._schema_create(conn, owner)
                checks.append(_check("create_target", ok, "required"))
            else:
                checks.append(
                    _check("insert_target", await self._table_priv(conn, oid, "INSERT"), "required")
                )
                checks.append(
                    _check("select_target", await self._table_priv(conn, oid, "SELECT"), "required")
                )
                checks.append(
                    _check(
                        "truncate_target", await self._table_priv(conn, oid, "TRUNCATE"), "advisory"
                    )
                )
            return checks
        if write_mode == "in_place":
            checks.append(
                _check("update_target", await self._table_priv(conn, oid, "UPDATE"), "required")
            )
            checks.append(
                _check("select_target", await self._table_priv(conn, oid, "SELECT"), "required")
            )
        else:  # append
            checks.append(
                _check("insert_target", await self._table_priv(conn, oid, "INSERT"), "required")
            )
        checks.append(_check("ledger_schema", await self._schema_create(conn, owner), "required"))
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
        except ConnectorError as exc:
            return ConnectionTestResult(
                ok=False,
                engine="postgres",
                checks=[_check("connect", False, "required", code=exc.code)],
                capabilities={},
                error_code="connection_failed",
            )
        checks: list[dict[str, Any]] = []
        version: str | None = None
        num = 0
        try:
            async with self.read_transaction() as conn:
                version = await conn.fetchval("SHOW server_version")
                num = int(await conn.fetchval("SELECT current_setting('server_version_num')"))
                tls = await conn.fetchval(
                    "SELECT ssl FROM pg_stat_ssl WHERE pid = pg_backend_pid()"
                )
                checks.append(_check("connect", True, "required", detail="tls" if tls else "plain"))
                app = await conn.fetchval("SELECT current_setting('application_name')")
                checks.append(_check("session_tagged", str(app).startswith("dps:"), "advisory"))
                if role == "source":
                    checks += await self._source_checks(conn, obj)
                else:
                    checks += await self._target_checks(conn, obj, write_mode)
        except asyncpg.QueryCanceledError:
            checks.append(_check("privileges", False, "required", code="statement_timeout"))
        except (*_RETRYABLE, asyncpg.PostgresError, asyncpg.InterfaceError) as exc:
            checks.append(_check("privileges", False, "required", code=type(exc).__name__))
        ok = all(c["ok"] for c in checks if c["severity"] == "required")
        return ConnectionTestResult(
            ok=ok,
            engine="postgres",
            server_version=version,
            checks=checks,
            capabilities={
                "transactions": True,
                "merge": num >= 150000,
                "identity": True,
                "sample": True,
                "rowid": False,
                "copy": True,
            },
        )

    # -- write side ----------------------------------------------------------------------------
    async def writer(self, target: ResolvedTarget, source_shape: TableShape) -> BatchWriter:
        """A :class:`PostgresWriter` for an ``in_place``, ``append`` or ``table`` target.

        Raises:
            ConnectorError: ``target_invalid`` for a ``files`` target or one without an object.
        """
        if target.write_mode == "files" or target.object is None:
            raise ConnectorError("target_invalid", "a PostgreSQL writer needs a table target")
        return PostgresWriter(self, target, source_shape)


#: The write ledger (contract §4.2 ``DPS_WRITE_LEDGER``; PostgreSQL folds names to lower case).
LEDGER_TABLE = "dps_write_ledger"


class LedgerConflict(ConnectorError):
    """Another holder advanced this partition's ledger: nothing was written (stop quietly)."""

    def __init__(self) -> None:
        super().__init__("ledger_conflict", "another worker advanced this partition")


class PkCollision(ConnectorError):
    """An appended key already exists in the target (a unique violation; count-free)."""

    def __init__(self) -> None:
        super().__init__("pk_collision", "an appended key already exists in the target")


def _target_error(exc: BaseException) -> ConnectorError:
    """A write-side driver failure → ``target_error`` (retryable for server/network failures)."""
    if isinstance(exc, _RETRYABLE):
        return ConnectorError("target_error", type(exc).__name__, retryable=True)
    if isinstance(exc, asyncpg.QueryCanceledError):
        return ConnectorError("statement_timeout", "the statement exceeded the connector timeout")
    return ConnectorError("target_error", type(exc).__name__)


class PostgresWriter:
    """``BatchWriter`` for PostgreSQL targets (module docstring: atomicity and resume)."""

    def __init__(
        self, connector: PostgresConnector, target: ResolvedTarget, source_shape: TableShape
    ) -> None:
        if target.object is None:
            raise ConnectorError("target_invalid", "a PostgreSQL writer needs a table target")
        self.c, self.t, self.src = connector, target, source_shape
        self.owner, self.name = target.object.owner or "public", target.object.name
        self.fqn = f"{_q(self.owner)}.{_q(self.name)}"
        self.ledger = f"{_q(target.ledger_owner or self.owner)}.{LEDGER_TABLE}"
        self._seen: dict[int, int] = {}
        digest = hashlib.sha256(f"{target.job_id}:{self.fqn}".encode()).hexdigest()[:10]
        self.stage = f"dps_stage_{digest}"

    def _type(self, column: str) -> str:
        shape = self.t.target_shape
        if shape is not None:
            data_type = shape.column(column).data_type
        else:
            data_type = self.t.column_types.get(column, "")
        if not TYPE_RE.fullmatch(data_type):
            raise PrepareFailure("target_invalid", "a column type cannot be used in generated SQL")
        return data_type

    async def _privilege(self, conn: asyncpg.Connection, relation: str, privilege: str) -> bool:
        return bool(await conn.fetchval("SELECT has_table_privilege($1, $2)", relation, privilege))

    async def _require(self, conn: asyncpg.Connection, *privileges: str) -> None:
        for privilege in privileges:
            if not await self._privilege(conn, self.fqn, privilege):
                raise PrepareFailure(
                    "target_invalid", f"the target login lacks {privilege} on the target table"
                )

    async def prepare(self, *, dry_run: bool) -> PrepareReport:
        """Create (or truncate) a ``table`` target, or check privileges and create the ledger
        for ``in_place``/``append``; ``dry_run`` only checks.

        Raises:
            PrepareFailure: ``target_invalid`` (missing table or privilege, no ledger possible),
                ``pk_strategy_invalid`` (a generated key without identity/default, or a sequence
                behind the table).
            ConnectorError: ``target_error`` / ``statement_timeout`` from the server.
        """
        pool = await self.c.write_pool()
        try:
            async with pool.acquire() as conn:
                return await self._prepare(conn, dry_run=dry_run)
        except (PrepareFailure, ConnectorError):
            raise
        except asyncpg.InsufficientPrivilegeError:
            raise PrepareFailure(
                "target_invalid", "the target login lacks a privilege the write mode needs"
            ) from None
        except asyncpg.InvalidSchemaNameError:
            raise PrepareFailure("target_invalid", "the target schema does not exist") from None
        except (*_RETRYABLE, asyncpg.PostgresError, asyncpg.InterfaceError) as exc:
            raise _target_error(exc) from None

    async def _prepare(self, conn: asyncpg.Connection, *, dry_run: bool) -> PrepareReport:
        report = PrepareReport()
        mode = self.t.write_mode
        exists = bool(await conn.fetchval("SELECT to_regclass($1) IS NOT NULL", self.fqn))
        if mode == "table":
            if not exists:
                if not self.t.create_if_missing:
                    raise PrepareFailure("target_invalid", "the target table does not exist")
                cols = ", ".join(
                    f"{_q(n)} {self._type(n)}{' NOT NULL' if n in self.t.key else ''}"
                    for n in self.t.write_columns
                )
                keys = ", ".join(_q(k) for k in self.t.key)
                if not dry_run:
                    await conn.execute(f"CREATE TABLE {self.fqn} ({cols}, PRIMARY KEY ({keys}))")
                    report.created = True
                report.checks.append({"name": "create_target", "ok": True, "dry_run": dry_run})
                return report
            needed = ["INSERT", "UPDATE", "SELECT"]
            if self.t.table_load == "truncate_insert":
                needed.append("TRUNCATE")
            await self._require(conn, *needed)
            report.existing_rows = await conn.fetchval(
                "SELECT GREATEST(reltuples, 0)::bigint FROM pg_class WHERE oid = $1::regclass",
                self.fqn,
            )
            if self.t.table_load == "truncate_insert" and not dry_run:
                await conn.execute(f"TRUNCATE {self.fqn}")
                report.truncated = True
            report.checks.append({"name": "existing_target", "ok": True})
            return report
        if not exists:
            raise PrepareFailure("target_invalid", "the target table does not exist")
        await self._require(conn, "UPDATE" if mode == "in_place" else "INSERT", "SELECT")
        if mode == "append" and self.t.generated_key:
            await self._check_generated(conn)
        if mode == "append" and self.t.same_as_source:
            top = await conn.fetchval(f"SELECT max({_q(self.t.key[0])}) FROM {self.fqn}")
            report.append_upper = int(top) + 1 if top is not None else None
        if not dry_run:
            await self._create_ledger(conn)
            report.ledger = True
        report.checks.append({"name": "write_ledger", "ok": True, "dry_run": dry_run})
        return report

    async def _create_ledger(self, conn: asyncpg.Connection) -> None:
        try:
            await conn.execute(
                f"CREATE TABLE IF NOT EXISTS {self.ledger} (job_id text NOT NULL, "
                "partition_no int NOT NULL, batch_no int NOT NULL, last_key jsonb NOT NULL, "
                "rows bigint NOT NULL DEFAULT 0, updated_at timestamptz NOT NULL DEFAULT now(), "
                "PRIMARY KEY (job_id, partition_no))"
            )
        except asyncpg.UniqueViolationError:
            pass  # a concurrent prepare created it first
        except asyncpg.InsufficientPrivilegeError:
            raise PrepareFailure("target_invalid", "cannot create the write ledger table") from None
        for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
            if not await self._privilege(conn, self.ledger, privilege):
                raise PrepareFailure(
                    "target_invalid", f"the target login lacks {privilege} on the write ledger"
                )

    async def _check_generated(self, conn: asyncpg.Connection) -> None:
        shape = self.t.target_shape
        if len(self.t.key) != 1 or shape is None:
            raise PrepareFailure("pk_strategy_invalid", "generate needs a single-column key")
        key = self.t.key[0]
        info = shape.column(key)
        if not (info.is_identity or info.has_default):
            raise PrepareFailure(
                "pk_strategy_invalid", "the key has no identity, default or sequence"
            )
        seq = await conn.fetchval("SELECT pg_get_serial_sequence($1, $2)", self.fqn, key)
        if seq is None:
            return  # a default that is not a sequence (e.g. gen_random_uuid()): nothing to compare
        try:
            row = await conn.fetchrow(
                "SELECT pg_sequence_last_value($1::regclass) AS last, s.seqstart AS start, "
                "s.seqincrement AS inc FROM pg_sequence s WHERE s.seqrelid = $1::regclass",
                seq,
            )
        except asyncpg.InsufficientPrivilegeError:
            raise PrepareFailure(
                "target_invalid", "cannot read the key sequence (SELECT on it is needed)"
            ) from None
        if row is None:
            return
        nxt = row["start"] if row["last"] is None else row["last"] + row["inc"]
        top = await conn.fetchval(f"SELECT max({_q(key)}) FROM {self.fqn}")
        if top is not None and row["inc"] > 0 and nxt <= top:
            raise PrepareFailure(
                "pk_strategy_invalid", "the key sequence is behind the table's largest key"
            )

    async def precheck_collisions(self, keys: AsyncIterator[list[tuple[Any, ...]]]) -> int:
        """How many of the streamed (transformed) keys already exist in the target."""
        pool = await self.c.write_pool()
        total = 0
        try:
            async with pool.acquire() as conn:
                async for batch in keys:
                    if not batch:
                        continue
                    if len(self.t.key) == 1:
                        k = self.t.key[0]
                        total += await conn.fetchval(
                            f"SELECT count(*) FROM {self.fqn} "
                            f"WHERE {_q(k)} = ANY($1::{self._type(k)}[])",
                            [v[0] for v in batch],
                        )
                    else:
                        arrays = [[v[i] for v in batch] for i in range(len(self.t.key))]
                        unnest = ", ".join(
                            f"${i + 1}::{self._type(k)}[]" for i, k in enumerate(self.t.key)
                        )
                        names = ", ".join(_q(k) for k in self.t.key)
                        on = " AND ".join(f"t.{_q(k)} = u.{_q(k)}" for k in self.t.key)
                        total += await conn.fetchval(
                            f"SELECT count(*) FROM {self.fqn} t "
                            f"JOIN unnest({unnest}) AS u({names}) ON {on}",
                            *arrays,
                        )
        except (PrepareFailure, ConnectorError):
            raise
        except (*_RETRYABLE, asyncpg.PostgresError, asyncpg.InterfaceError) as exc:
            raise _target_error(exc) from None
        return int(total)

    async def read_ledger(self, job_id: str, partition_no: int) -> LedgerMark | None:
        """The partition's last committed mark (also the base the next batch is checked against)."""
        pool = await self.c.write_pool()
        try:
            async with pool.acquire() as conn:
                if await conn.fetchval("SELECT to_regclass($1)", self.ledger) is None:
                    self._seen[partition_no] = 0
                    return None
                row = await conn.fetchrow(
                    f"SELECT batch_no, last_key::text AS k, rows FROM {self.ledger} "
                    "WHERE job_id = $1 AND partition_no = $2",
                    job_id,
                    partition_no,
                )
        except (*_RETRYABLE, asyncpg.PostgresError, asyncpg.InterfaceError) as exc:
            raise _target_error(exc) from None
        self._seen[partition_no] = row["batch_no"] if row else 0
        if row is None:
            return None
        return LedgerMark(
            job_id=job_id,
            partition_no=partition_no,
            batch_no=row["batch_no"],
            last_key=json.loads(row["k"]),
            rows=row["rows"],
        )

    async def write_batch(self, rows: list[Row], *, mark: LedgerMark) -> int:
        """Write ``rows`` and (in_place/append) the ledger ``mark`` in one transaction."""
        if not rows:
            return 0
        pool = await self.c.write_pool()
        ledgered = self.t.write_mode in ("in_place", "append")
        try:
            async with pool.acquire() as conn, conn.transaction():
                if ledgered:
                    current = (
                        await conn.fetchval(
                            f"SELECT batch_no FROM {self.ledger} WHERE job_id = $1 "
                            "AND partition_no = $2 FOR UPDATE",
                            mark.job_id,
                            mark.partition_no,
                        )
                        or 0
                    )
                    if current != self._seen.get(mark.partition_no, 0):
                        raise LedgerConflict()
                if self.t.write_mode == "in_place":
                    written = await self._update(conn, rows)
                elif self.t.write_mode == "append":
                    written = await self._insert(conn, rows)
                else:
                    written = await self._upsert(conn, rows)
                if ledgered:
                    await conn.execute(
                        f"INSERT INTO {self.ledger} (job_id, partition_no, batch_no, last_key, "
                        "rows, updated_at) VALUES ($1, $2, $3, $4::jsonb, $5, now()) "
                        "ON CONFLICT (job_id, partition_no) DO UPDATE SET "
                        "batch_no = EXCLUDED.batch_no, last_key = EXCLUDED.last_key, "
                        "rows = EXCLUDED.rows, updated_at = now()",
                        mark.job_id,
                        mark.partition_no,
                        mark.batch_no,
                        json.dumps(mark.last_key),
                        mark.rows,
                    )
        except asyncpg.UniqueViolationError:
            raise PkCollision() from None
        except (LedgerConflict, PrepareFailure):
            raise
        except (*_RETRYABLE, asyncpg.PostgresError, asyncpg.InterfaceError) as exc:
            raise _target_error(exc) from None
        if ledgered:
            self._seen[mark.partition_no] = mark.batch_no
        return written

    async def _update(self, conn: asyncpg.Connection, rows: list[Row]) -> int:
        names = list(self.t.key) + [c for c in self.t.write_columns if c not in self.t.key]
        arrays = [[row.get(n) for row in rows] for n in names]
        unnest = ", ".join(f"${i + 1}::{self._type(n)}[]" for i, n in enumerate(names))
        sets = ", ".join(f"{_q(c)} = u.{_q(c)}" for c in names if c not in self.t.key)
        on = " AND ".join(f"t.{_q(k)} = u.{_q(k)}" for k in self.t.key)
        listed = ", ".join(_q(n) for n in names)
        tag = await conn.execute(
            f"UPDATE {self.fqn} AS t SET {sets} FROM unnest({unnest}) AS u({listed}) WHERE {on}",
            *arrays,
        )
        return rowcount(tag)

    async def _insert(self, conn: asyncpg.Connection, rows: list[Row]) -> int:
        cols = list(self.t.write_columns)
        tag = await conn.copy_records_to_table(
            self.name,
            schema_name=self.owner,
            columns=cols,
            records=[tuple(r.get(c) for c in cols) for r in rows],
        )
        return rowcount(tag)

    async def _upsert(self, conn: asyncpg.Connection, rows: list[Row]) -> int:
        cols = list(self.t.write_columns)
        await conn.execute(
            f"CREATE TEMP TABLE IF NOT EXISTS {_q(self.stage)} (LIKE {self.fqn} INCLUDING DEFAULTS) "
            "ON COMMIT DELETE ROWS"
        )
        await conn.copy_records_to_table(
            self.stage,
            schema_name="pg_temp",
            columns=cols,
            records=[tuple(r.get(c) for c in cols) for r in rows],
        )
        listed = ", ".join(_q(c) for c in cols)
        rest = [c for c in cols if c not in self.t.key]
        action = (
            "DO UPDATE SET " + ", ".join(f"{_q(c)} = EXCLUDED.{_q(c)}" for c in rest)
            if rest
            else "DO NOTHING"
        )
        conflict = ", ".join(_q(k) for k in self.t.key)
        tag = await conn.execute(
            f"INSERT INTO {self.fqn} ({listed}) SELECT {listed} FROM pg_temp.{_q(self.stage)} "
            f"ON CONFLICT ({conflict}) {action}"
        )
        return rowcount(tag)

    async def finish_partition(self, job_id: str, partition_no: int) -> dict[str, Any]:
        """Nothing to upload for a database target."""
        return {}

    async def clear_ledger(self, job_id: str) -> int:
        """Finalize: delete the job's ledger rows (the ledger table itself stays)."""
        pool = await self.c.write_pool()
        try:
            async with pool.acquire() as conn:
                if await conn.fetchval("SELECT to_regclass($1)", self.ledger) is None:
                    return 0
                return rowcount(
                    await conn.execute(f"DELETE FROM {self.ledger} WHERE job_id = $1", job_id)
                )
        except (*_RETRYABLE, asyncpg.PostgresError, asyncpg.InterfaceError) as exc:
            raise _target_error(exc) from None

    async def close(self) -> None:
        """The connector owns the pools; nothing to release here."""
        return None
