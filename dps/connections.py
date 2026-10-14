"""Connection registry (contract §5.1): ``dps_connection`` rows under the tenant GUC.

A connection names *where* data lives (``params``) and *how to authenticate*: a ``secret_ref``
(files under the tenant's ``DPS_SECRET_DIR/<tenant_id>/<ref>/``, or an allow-listed
``_shared/<ref>/``; checked when stored and again when loaded, see :mod:`dps.secrets`; the
production path) or inline credentials (dev only,
``DPS_INLINE_SECRETS_ALLOWED``), sealed with the job KEK into ``secret_enc`` (AAD
``dps_connection.secret:<id>``) and purged after ``DPS_INLINE_SECRET_TTL_HOURS``. ``pvc`` file
targets take neither. Credentials are only ever opened by :func:`load_credentials`; the public
:class:`Connection` carries ``has_secret`` and never ``secret_enc``.

**Production credentials** (S3 security review, D3 bound to the resource): a ``secret_ref`` that a
``production``-tagged connection holds is a production credential. Without ``admin`` no other
connection may name it (create or patch: 403 ``admin_scope_required``), and ``in_place``/``append``
through any connection that holds it needs ``admin`` (:func:`production_refs`, used by
``validate_run`` and the retry check) — a ``jobs`` key cannot clone the production connection
without its tag and rewrite production data in place.

**Who may change a connection** (security review SEC-3). Any mutation of a connection that is or
becomes tagged ``production`` (update, delete, credential rotation) needs the ``admin`` scope. On a
connection that holds credentials (a sealed secret or a ``secret_ref``), a change to a param that
decides where those credentials are sent (:data:`ENDPOINT_PARAMS`) needs the credentials again in
the same request (new inline credentials, or the ``secret_ref`` restated) or ``admin``; otherwise
403 ``connection_endpoint_change_requires_credentials``, before anything is written.

Every function takes an optional ``conn`` so workers can call it on ``db.acquire_queue(tenant)``;
without one it uses ``db.acquire(tenant_id)``. Errors are :class:`ConnectionStoreError` (code,
HTTP status, value-free detail) or :class:`~dps.secrets.SecretUnavailable`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError

from dps import db
from dps.config import Settings
from dps.errors import safe_error_message
from dps.ids import PREFIX_CONNECTION, new_id
from dps.jobs import ACTIVE_JOB_STATES, decode_cursor, encode_cursor
from dps.jobs_models import ConnectionParams, ConnectionSpec, ConnRole, InlineCredentials
from dps.layers.connectors.base import ConnectionTestResult, ConnEngine
from dps.secrets import (
    SECRET_REF_PATTERN,
    SealError,
    SecretRefRefused,
    SecretUnavailable,
    check_secret_ref,
    open_json,
    resolve_secret_ref,
    seal_json,
)

_HIDDEN = ConfigDict(hide_input_in_errors=True)
_MIN_LIMIT, _MAX_LIMIT = 1, 200
_COLS = (
    "id, tenant_id, name, engine, params, secret_ref, secret_enc IS NOT NULL AS has_secret, "
    "secret_expires_at, roles, tags, capabilities, last_test, created_by, created_at, updated_at"
)


#: Per engine, the params that decide which server (and which database, bucket or TLS trust)
#: a connection's credentials are sent to. ``options.<key>`` names an entry of ``params.options``.
#: Names that are not ``ConnectionParams`` fields yet (``dbname``; Oracle ``sid`` / ``dsn``; Mongo
#: ``uri`` / ``replica_set``) cover the M3 engines' params once they land; a param added later must
#: be classified here or in :data:`NOT_ENDPOINT_PARAMS` (``tests/test_connection_endpoints.py``).
ENDPOINT_PARAMS: dict[str, frozenset[str]] = {
    "postgres": frozenset(
        {
            "host",
            "port",
            "database",
            "dbname",
            "options.sslmode",
            "options.sslrootcert_ref",
        }
    ),
    "oracle": frozenset(
        {
            "host",
            "port",
            "service_name",
            "sid",
            "dsn",
            "options.sslmode",
            "options.sslrootcert_ref",
            "options.tls",
            "options.protocol",  # M5: tcp vs tcps decides whether credentials travel in clear
        }
    ),
    "mongo": frozenset(
        {
            "uri",
            "host",
            "port",
            "replica_set",
            "database",
            "options.replicaSet",
            "options.directConnection",
            "options.tls",
        }
    ),
    "s3": frozenset({"endpoint_url", "bucket", "region"}),
    "pvc": frozenset(),  # takes no credentials
}
#: Params that never decide where credentials go (a change needs no credentials).
NOT_ENDPOINT_PARAMS = frozenset(
    {"prefix", "base_path", "options.connect_timeout", "options.authSource"}
)


def _param(dumped: Mapping[str, Any], name: str) -> Any:
    if name.startswith("options."):
        return (dumped.get("options") or {}).get(name.removeprefix("options."))
    return dumped.get(name)


def endpoint_changes(engine: str, old: ConnectionParams, new: ConnectionParams) -> list[str]:
    """The endpoint-defining params (:data:`ENDPOINT_PARAMS`) whose value differs, sorted
    (names only). An engine without a list treats every param as endpoint-defining."""
    a, b = old.model_dump(mode="json"), new.model_dump(mode="json")
    names = ENDPOINT_PARAMS.get(engine)
    if names is None:
        keys = {k for k in a.keys() | b.keys() if k != "options"}
        keys |= {f"options.{k}" for k in (a.get("options") or {}).keys() | (b.get("options") or {})}
        names = frozenset(keys)
    return sorted(n for n in names if _param(a, n) != _param(b, n))


def changed_fields(current: Connection, patch: ConnectionPatch) -> list[str]:
    """Field names a patch changes, for the audit log (``params.<field>``, never a value);
    supplied credentials always count as a change."""
    out: set[str] = set()
    if patch.name is not None and patch.name != current.name:
        out.add("name")
    if patch.roles is not None and set(patch.roles) != set(current.roles):
        out.add("roles")
    if patch.tags is not None and patch.tags != current.tags:
        out.add("tags")
    if patch.secret_ref is not None and patch.secret_ref != current.secret_ref:
        out.add("secret_ref")
    if patch.credentials is not None:
        out.add("credentials")
    if patch.params is not None:
        a, b = current.params.model_dump(mode="json"), patch.params.model_dump(mode="json")
        for key in (a.keys() | b.keys()) - {"options"}:
            if a.get(key) != b.get(key):
                out.add(f"params.{key}")
        opts_a, opts_b = a.get("options") or {}, b.get("options") or {}
        for key in opts_a.keys() | opts_b.keys():
            if opts_a.get(key) != opts_b.get(key):
                out.add(f"params.options.{key}")
    return sorted(out)


async def production_refs(
    tenant_id: str, *, conn: asyncpg.Connection | None = None
) -> frozenset[str]:
    """The ``secret_ref`` names the tenant's ``production``-tagged connections hold (module
    docstring: production credentials)."""
    async with _using(tenant_id, conn) as c:
        rows = await c.fetch(
            f"SELECT DISTINCT secret_ref FROM {db.schema_ident()}.dps_connection "
            "WHERE tenant_id = $1 AND 'production' = ANY(tags) AND secret_ref IS NOT NULL",
            tenant_id,
        )
    return frozenset(str(r["secret_ref"]) for r in rows)


def holds_production_credentials(c: Connection, refs: frozenset[str]) -> bool:
    """True when ``c`` is tagged ``production`` or holds a production credential (``refs``)."""
    return c.production or (c.secret_ref is not None and c.secret_ref in refs)


def _production_credential_refused() -> ConnectionStoreError:
    return ConnectionStoreError(
        "admin_scope_required",
        403,
        "this secret_ref is a production credential: naming it needs the admin scope",
    )


def _admin_required() -> ConnectionStoreError:
    return ConnectionStoreError(
        "admin_scope_required", 403, "changing a production-tagged connection needs the admin scope"
    )


def _aad(connection_id: str) -> str:
    return f"dps_connection.secret:{connection_id}"


class Credentials(BaseModel):
    """Opened credentials of a connection (memory only; never stored, logged or returned)."""

    model_config = _HIDDEN
    username: str = ""
    password: SecretStr = SecretStr("")
    extra: dict[str, SecretStr] = Field(default_factory=dict)


class _SealedCredentials(BaseModel):
    """What ``secret_enc`` seals; exists only inside the sealed blob and in memory."""

    model_config = _HIDDEN
    username: str = Field(repr=False)
    password: str = Field(repr=False)
    extra: dict[str, str] = Field(default_factory=dict, repr=False)


class Connection(BaseModel):
    """A ``dps_connection`` row as the API and workers see it (no sealed secret)."""

    model_config = _HIDDEN
    id: str
    tenant_id: str
    name: str
    engine: ConnEngine
    params: ConnectionParams
    secret_ref: str | None
    has_secret: bool
    secret_expires_at: datetime | None
    roles: list[ConnRole]
    tags: list[str]
    capabilities: dict[str, bool]
    last_test: ConnectionTestResult | None
    created_by: str | None
    created_at: datetime
    updated_at: datetime

    @property
    def production(self) -> bool:
        """True when tagged ``production`` (in-place/append writes and re-tagging need admin)."""
        return "production" in self.tags


class ConnectionPatch(BaseModel):
    """A partial update; omitted fields keep their stored value (the engine never changes)."""

    model_config = _HIDDEN
    name: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9-]{1,62}$")
    params: ConnectionParams | None = None
    secret_ref: str | None = Field(default=None, pattern=SECRET_REF_PATTERN)
    credentials: InlineCredentials | None = None
    roles: set[ConnRole] | None = None
    tags: list[str] | None = None


class ConnectionStoreError(Exception):
    """A refused registry operation: ``code`` and ``status`` map to ``{"detail", "code"}``."""

    def __init__(self, code: str, status: int, detail: str) -> None:
        super().__init__(detail)
        self.code, self.status, self.detail = code, status, detail


@asynccontextmanager
async def _using(
    tenant_id: str, conn: asyncpg.Connection | None
) -> AsyncIterator[asyncpg.Connection]:
    if conn is not None:
        yield conn
    else:
        async with db.acquire(tenant_id) as fresh:
            yield fresh


def _row(row: Mapping[str, Any]) -> Connection:
    data = dict(row)
    data["capabilities"] = data.get("capabilities") or {}
    return Connection.model_validate(data)


def _require_inline_allowed(settings: Settings, kek: bytes | None) -> bytes:
    if not settings.inline_secrets_allowed:
        raise ConnectionStoreError(
            "inline_secrets_disabled", 422, "inline credentials are disabled; use secret_ref"
        )
    if kek is None:
        raise ConnectionStoreError("kek_unconfigured", 503, "DPS_JOB_KEK is not configured")
    return kek


def _check_refs(
    tenant_id: str, secret_ref: str | None, params: ConnectionParams, settings: Settings
) -> None:
    """The connection's secret references (``secret_ref``, ``options.sslrootcert_ref``) must be
    the tenant's own or shared with it (:func:`dps.secrets.check_secret_ref`)."""
    for ref in (secret_ref, params.options.get("sslrootcert_ref")):
        if not ref:
            continue
        try:
            check_secret_ref(tenant_id, ref, settings)
        except SecretRefRefused as exc:
            raise ConnectionStoreError(exc.code, exc.status, exc.detail) from None


def _seal(
    kek: bytes, credentials: InlineCredentials, connection_id: str, settings: Settings
) -> tuple[bytes, datetime]:
    plain = _SealedCredentials(
        username=credentials.username,
        password=credentials.password.get_secret_value(),
        extra={k: v.get_secret_value() for k, v in credentials.extra.items()},
    )
    sealed = seal_json(kek, plain, aad=_aad(connection_id))
    return sealed, datetime.now(UTC) + timedelta(hours=settings.inline_secret_ttl_hours)


async def touch_tenant(tenant_id: str, *, conn: asyncpg.Connection | None = None) -> None:
    """Insert the tenant's ``dps_tenant`` row when missing (retention enumerates tenants)."""
    async with _using(tenant_id, conn) as c:
        await c.execute(
            f"INSERT INTO {db.schema_ident()}.dps_tenant (tenant_id) VALUES ($1) "
            "ON CONFLICT (tenant_id) DO NOTHING",
            tenant_id,
        )


async def create_connection(
    tenant_id: str,
    spec: ConnectionSpec,
    *,
    settings: Settings,
    kek: bytes | None,
    created_by: str | None,
    admin: bool = False,
) -> Connection:
    """Insert a connection (inline credentials sealed). Raises :class:`ConnectionStoreError`:
    ``inline_secrets_disabled`` 422, ``kek_unconfigured`` 503, ``connection_name_taken`` 409,
    ``secret_ref_invalid`` 422, ``secret_ref_foreign_tenant`` / ``secret_ref_not_shared`` 403,
    ``admin_scope_required`` 403 (``admin`` false and ``secret_ref`` a production credential)."""
    _check_refs(tenant_id, spec.secret_ref, spec.params, settings)
    sealing_kek = _require_inline_allowed(settings, kek) if spec.credentials is not None else None
    conn_id = new_id(PREFIX_CONNECTION)
    sealed: bytes | None = None
    expires: datetime | None = None
    if spec.credentials is not None and sealing_kek is not None:
        sealed, expires = _seal(sealing_kek, spec.credentials, conn_id, settings)
    async with db.acquire(tenant_id) as conn, conn.transaction():
        if (
            not admin
            and spec.secret_ref is not None
            and spec.secret_ref in await production_refs(tenant_id, conn=conn)
        ):
            raise _production_credential_refused()
        await touch_tenant(tenant_id, conn=conn)
        try:
            await conn.execute(
                f"INSERT INTO {db.schema_ident()}.dps_connection (id, tenant_id, name, engine, "
                "params, secret_ref, secret_enc, secret_expires_at, roles, tags, created_by) "
                "VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7, $8, $9, $10, $11)",
                conn_id,
                tenant_id,
                spec.name,
                spec.engine,
                spec.params.model_dump(mode="json"),
                spec.secret_ref,
                sealed,
                expires,
                sorted(spec.roles),
                spec.tags,
                created_by,
            )
        except asyncpg.UniqueViolationError:
            raise ConnectionStoreError(
                "connection_name_taken", 409, "a connection with this name exists"
            ) from None
        row = await conn.fetchrow(
            f"SELECT {_COLS} FROM {db.schema_ident()}.dps_connection WHERE id = $1", conn_id
        )
    if row is None:  # RLS hides a row we just inserted only if the GUC binding is broken
        raise RuntimeError("created connection is not visible to its tenant")
    return _row(row)


async def get_connection(
    tenant_id: str, connection_id: str, *, conn: asyncpg.Connection | None = None
) -> Connection | None:
    """The tenant's connection (RLS-scoped), or None."""
    async with _using(tenant_id, conn) as c:
        row = await c.fetchrow(
            f"SELECT {_COLS} FROM {db.schema_ident()}.dps_connection "
            "WHERE tenant_id = $1 AND id = $2",
            tenant_id,
            connection_id,
        )
    return _row(row) if row else None


async def list_connections(
    tenant_id: str, *, limit: int = 50, cursor: str | None = None
) -> tuple[list[Connection], str | None]:
    """Newest first; keyset pagination on ``(created_at, id)``. ``ValueError`` on a bad cursor."""
    params: list[Any] = [tenant_id]
    where = "tenant_id = $1"
    if cursor:
        ts, cid = decode_cursor(cursor)
        params += [ts, cid]
        where += " AND (created_at, id) < ($2::timestamptz, $3)"
    page = max(_MIN_LIMIT, min(_MAX_LIMIT, limit))
    async with db.acquire(tenant_id) as conn:
        rows = await conn.fetch(
            f"SELECT {_COLS} FROM {db.schema_ident()}.dps_connection WHERE {where} "
            f"ORDER BY created_at DESC, id DESC LIMIT {page + 1}",
            *params,
        )
    items = [_row(r) for r in rows[:page]]
    nxt = encode_cursor(items[-1].created_at, items[-1].id) if len(rows) > page and items else None
    return items, nxt


async def update_connection(
    tenant_id: str,
    connection_id: str,
    patch: ConnectionPatch,
    *,
    settings: Settings,
    kek: bytes | None,
    admin: bool = False,
) -> Connection | None:
    """Apply ``patch`` (validated against the contract's connection rules); None if absent.

    ``admin``: the caller holds the ``admin`` scope. Without it, a connection that is or would
    become production-tagged is not changed (``admin_scope_required`` 403), and an endpoint
    change of a connection holding credentials needs ``credentials`` or ``secret_ref`` in the
    same patch (``connection_endpoint_change_requires_credentials`` 403); both are checked under
    the row lock before anything is written.

    Secrets: ``credentials`` re-seals (and drops a ``secret_ref``), ``secret_ref`` drops the sealed
    blob, neither keeps what is stored. Changing ``params`` or the secret clears ``last_test``
    and ``capabilities`` (the old test no longer describes the connection). Raises
    :class:`ConnectionStoreError` (``connection_invalid`` 422, ``inline_secrets_disabled`` 422,
    ``kek_unconfigured`` 503, ``connection_name_taken`` 409, the ``secret_ref_*`` codes of
    :func:`create_connection` for a reference the patch sets).
    """
    s = db.schema_ident()
    async with db.acquire(tenant_id) as conn, conn.transaction():
        row = await conn.fetchrow(
            f"SELECT {_COLS}, secret_enc FROM {s}.dps_connection "
            "WHERE tenant_id = $1 AND id = $2 FOR UPDATE",
            tenant_id,
            connection_id,
        )
        if row is None:
            return None
        current = _row({k: v for k, v in dict(row).items() if k != "secret_enc"})
        sealed: bytes | None = row["secret_enc"]
        becomes_production = patch.tags is not None and "production" in patch.tags
        if not admin and (current.production or becomes_production):
            raise _admin_required()
        if (
            not admin
            and patch.secret_ref is not None
            and patch.secret_ref != current.secret_ref
            and patch.secret_ref in await production_refs(tenant_id, conn=conn)
        ):
            raise _production_credential_refused()
        holds_credentials = sealed is not None or current.secret_ref is not None
        resupplied = patch.credentials is not None or patch.secret_ref is not None
        if (
            not admin
            and holds_credentials
            and not resupplied
            and patch.params is not None
            and endpoint_changes(current.engine, current.params, patch.params)
        ):
            raise ConnectionStoreError(
                "connection_endpoint_change_requires_credentials",
                403,
                "changing where this connection points needs its credentials (credentials or "
                "secret_ref) in the same request, or the admin scope",
            )
        secret_ref = current.secret_ref
        expires = current.secret_expires_at
        secret_changed = False
        if patch.credentials is not None:
            sealing_kek = _require_inline_allowed(settings, kek)
            sealed, expires = _seal(sealing_kek, patch.credentials, connection_id, settings)
            secret_ref, secret_changed = None, True
        elif patch.secret_ref is not None:
            sealed, expires, secret_ref, secret_changed = None, None, patch.secret_ref, True
        params = patch.params if patch.params is not None else current.params
        merged: dict[str, Any] = {
            "name": patch.name or current.name,
            "engine": current.engine,
            "params": params.model_dump(mode="json"),
            "secret_ref": secret_ref,
            "roles": set(patch.roles) if patch.roles is not None else set(current.roles),
            "tags": patch.tags if patch.tags is not None else current.tags,
        }
        if patch.credentials is not None or (current.engine != "pvc" and secret_ref is None):
            # an inline secret (new, kept or already purged) stands in for validation only
            merged["credentials"] = {"username": "-", "password": "-"}
        try:
            spec = ConnectionSpec.model_validate(merged)
        except ValidationError as exc:
            errors = exc.errors()
            first = safe_error_message(errors[0]) if errors else "invalid"  # S1: no echo
            raise ConnectionStoreError("connection_invalid", 422, first) from None
        if patch.secret_ref is not None or patch.params is not None:
            _check_refs(tenant_id, secret_ref, spec.params, settings)
        stale = secret_changed or patch.params is not None
        try:
            await conn.execute(
                f"UPDATE {s}.dps_connection SET name = $2, params = $3::jsonb, secret_ref = $4, "
                "secret_enc = $5, secret_expires_at = $6, roles = $7, tags = $8, "
                "last_test = CASE WHEN $9 THEN NULL ELSE last_test END, "
                "capabilities = CASE WHEN $9 THEN '{}'::jsonb ELSE capabilities END, "
                "updated_at = now() WHERE id = $1",
                connection_id,
                spec.name,
                spec.params.model_dump(mode="json"),
                secret_ref,
                sealed,
                expires,
                sorted(spec.roles),
                spec.tags,
                stale,
            )
        except asyncpg.UniqueViolationError:
            raise ConnectionStoreError(
                "connection_name_taken", 409, "a connection with this name exists"
            ) from None
        updated = await conn.fetchrow(
            f"SELECT {_COLS} FROM {s}.dps_connection WHERE id = $1", connection_id
        )
    return _row(updated) if updated else None


async def delete_connection(tenant_id: str, connection_id: str, *, admin: bool = False) -> bool:
    """Delete the connection; ``connection_in_use`` (409) while a non-terminal job uses it,
    ``admin_scope_required`` (403) for a production-tagged one unless ``admin``."""
    s = db.schema_ident()
    async with db.acquire(tenant_id) as conn, conn.transaction():
        tags = await conn.fetchval(
            f"SELECT tags FROM {s}.dps_connection WHERE tenant_id = $1 AND id = $2 FOR UPDATE",
            tenant_id,
            connection_id,
        )
        if tags is not None and "production" in tags and not admin:
            raise _admin_required()
        busy = await conn.fetchval(
            f"SELECT 1 FROM {s}.dps_job WHERE tenant_id = $1 "
            "AND (connection_id = $2 OR target_connection_id = $2) "
            "AND state = ANY($3::text[]) LIMIT 1",
            tenant_id,
            connection_id,
            [x.value for x in ACTIVE_JOB_STATES],
        )
        if busy:
            raise ConnectionStoreError(
                "connection_in_use", 409, "a queued or running job uses this connection"
            )
        tag = await conn.execute(
            f"DELETE FROM {s}.dps_connection WHERE tenant_id = $1 AND id = $2",
            tenant_id,
            connection_id,
        )
    return db.rowcount(tag) > 0


async def record_test(
    tenant_id: str,
    connection_id: str,
    result: ConnectionTestResult,
    *,
    conn: asyncpg.Connection | None = None,
) -> None:
    """Store the last connection test (value-free checks) and its capabilities."""
    async with _using(tenant_id, conn) as c:
        await c.execute(
            f"UPDATE {db.schema_ident()}.dps_connection SET last_test = $3::jsonb, "
            "capabilities = $4::jsonb, updated_at = now() WHERE tenant_id = $1 AND id = $2",
            tenant_id,
            connection_id,
            result.model_dump(mode="json"),
            result.capabilities,
        )


async def load_credentials(
    c: Connection,
    *,
    settings: Settings,
    kek: bytes | None,
    conn: asyncpg.Connection | None = None,
) -> Credentials:
    """Open the connection's credentials (``pvc``: none).

    Raises:
        SecretUnavailable: the ``secret_ref`` does not resolve, or the inline secret is purged,
            expired or cannot be opened with this KEK.
        ConnectionStoreError: ``kek_unconfigured`` (503) for an inline secret without a KEK.
    """
    if c.engine == "pvc":
        return Credentials()
    if c.secret_ref:
        files = resolve_secret_ref(c.tenant_id, c.secret_ref, settings)
        return Credentials(
            username=files.pop("username", ""),
            password=SecretStr(files.pop("password", "")),
            extra={k: SecretStr(v) for k, v in files.items()},
        )
    async with _using(c.tenant_id, conn) as cx:
        row = await cx.fetchrow(
            f"SELECT secret_enc, secret_expires_at FROM {db.schema_ident()}.dps_connection "
            "WHERE tenant_id = $1 AND id = $2",
            c.tenant_id,
            c.id,
        )
    if row is None or row["secret_enc"] is None:
        raise SecretUnavailable()
    expires = row["secret_expires_at"]
    if expires is not None and expires <= datetime.now(UTC):
        raise SecretUnavailable()
    if kek is None:
        raise ConnectionStoreError("kek_unconfigured", 503, "DPS_JOB_KEK is not configured")
    try:
        plain = open_json(kek, row["secret_enc"], _SealedCredentials, aad=_aad(c.id))
    except SealError:
        raise SecretUnavailable() from None
    return Credentials(
        username=plain.username,
        password=SecretStr(plain.password),
        extra={k: SecretStr(v) for k, v in plain.extra.items()},
    )
