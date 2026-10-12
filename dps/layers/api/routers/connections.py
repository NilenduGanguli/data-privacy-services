"""``/api/v1/connections`` (contract §9, scope ``jobs``; the ``production`` tag needs ``admin``).

SEC-3: updating or deleting a production-tagged connection needs ``admin``; moving a connection
that holds credentials to another endpoint needs its credentials in the same request or ``admin``
(:mod:`dps.connections`). The update's audit event names the changed fields
(``counts["field.<name>"]``), never a value. The test route takes no param overrides.

Credentials never appear in a response (``has_secret`` only); a connection test stores its
value-free checks as ``last_test``; ``count`` validates and binds the selection before any query
runs and answers the wizard's checklist (mockup 06 step 2) together with the value-free statement
DPS will page with (``runs_as``) and the number of bound parameters.
"""

from __future__ import annotations

from typing import Any, Literal, cast

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from dps.auth import require_scope
from dps.config import Settings
from dps.connections import (
    Connection,
    ConnectionPatch,
    changed_fields,
    create_connection,
    delete_connection,
    get_connection,
    list_connections,
    load_credentials,
    record_test,
    update_connection,
)
from dps.jobs_models import ConnectionSpec, Selection
from dps.layers.api.deps import (
    audit_action,
    audited,
    guarded,
    kek_of,
    page_args,
    principal_of,
    rejected,
    require_db,
    tenant_of,
)
from dps.layers.api.errors import DpsHttpError
from dps.layers.connectors.base import (
    ColumnInfo,
    ConnectionTestResult,
    Connector,
    ConnectorError,
    Engine,
    ObjectRef,
    TableShape,
    WriteMode,
)
from dps.layers.connectors.registry import open_connector, open_target_store, test_store
from dps.layers.connectors.selection import (
    SelectionError,
    failed_checks,
    parameterize_literals,
    selection_checks,
    validate_selection,
)
from dps.ratelimit import enforce_rate_limit
from dps.secrets import SecretUnavailable

router = APIRouter(prefix="/api/v1", tags=["connections"])
_HIDDEN = ConfigDict(hide_input_in_errors=True)
_JOBS = [Depends(require_scope("jobs")), Depends(enforce_rate_limit), Depends(require_db)]
_TABULAR = ("postgres", "oracle", "mongo")
PRODUCTION = "production"


class ConnectionTestRequest(BaseModel):
    """Body of ``POST /connections/{id}/test``."""

    model_config = _HIDDEN
    role: Literal["source", "target"] = "source"
    object: ObjectRef | str | None = None
    write_mode: WriteMode | None = None


class CountRequest(BaseModel):
    """Body of ``POST /connections/{id}/count``."""

    model_config = _HIDDEN
    object: ObjectRef | str
    selection: Selection = Field(default_factory=Selection)


def _settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def _object(value: ObjectRef | str | None) -> ObjectRef | None:
    if value is None or isinstance(value, ObjectRef):
        return value
    try:
        return ObjectRef.parse(value)
    except ValueError:
        raise DpsHttpError(422, "object_invalid", "object must be NAME or OWNER.NAME") from None


async def _connection(request: Request, connection_id: str) -> Connection:
    conn = await get_connection(tenant_of(request), connection_id)
    if conn is None:
        raise DpsHttpError(404, "connection_not_found", "connection not found")
    return conn


def _require_admin_for_production(request: Request) -> None:
    if not principal_of(request).has_scope("admin"):
        raise DpsHttpError(403, "admin_scope_required", "the production tag needs the admin scope")


async def _open(request: Request, conn: Connection, action: str) -> Connector:
    if conn.engine not in _TABULAR:
        raise DpsHttpError(
            422, "engine_not_tabular", "file stores have no tables; they are files targets"
        )
    return await open_connector(conn, _settings(request), action=action, kek=kek_of(request))


def _dump(conn: Connection) -> dict[str, Any]:
    return conn.model_dump(mode="json")


@router.post("/connections", status_code=201, dependencies=_JOBS)
async def create(body: ConnectionSpec, request: Request) -> dict[str, Any]:
    """Register a connection (inline credentials are sealed; ``secret_ref`` is a name)."""

    async def run() -> dict[str, Any]:
        tenant = tenant_of(request)
        if PRODUCTION in body.tags:
            _require_admin_for_production(request)
        conn = await create_connection(
            tenant,
            body,
            settings=_settings(request),
            kek=kek_of(request),
            created_by=principal_of(request).key_id,
        )
        return _dump(conn)

    return await audited(request, "connection.create", run(), status=201)


@router.get("/connections", dependencies=_JOBS)
async def list_(request: Request, limit: int = 50, cursor: str | None = None) -> dict[str, Any]:
    """The tenant's connections, newest first."""

    async def run() -> dict[str, Any]:
        items, nxt = await list_connections(tenant_of(request), **page_args(limit, cursor))
        return {"items": [_dump(c) for c in items], "next_cursor": nxt}

    return await guarded(run())


@router.get("/connections/{connection_id}", dependencies=_JOBS)
async def get(connection_id: str, request: Request) -> dict[str, Any]:
    """One connection (``has_secret``, never the secret)."""

    async def run() -> dict[str, Any]:
        return _dump(await _connection(request, connection_id))

    return await guarded(run())


@router.patch("/connections/{connection_id}", dependencies=_JOBS)
async def patch(connection_id: str, body: ConnectionPatch, request: Request) -> dict[str, Any]:
    """Update name, params, secret, roles or tags. Any change of a connection that is or becomes
    production-tagged needs admin; an endpoint change of a connection holding credentials needs
    them again (``credentials`` / ``secret_ref``) or admin."""
    fields: dict[str, int] = {}

    async def run() -> dict[str, Any]:
        tenant = tenant_of(request)
        current = await _connection(request, connection_id)
        fields.update({f"field.{name}": 1 for name in changed_fields(current, body)})
        updated = await update_connection(
            tenant,
            connection_id,
            body,
            settings=_settings(request),
            kek=kek_of(request),
            admin=principal_of(request).has_scope("admin"),
        )
        if updated is None:
            raise DpsHttpError(404, "connection_not_found", "connection not found")
        return _dump(updated)

    try:
        result = await run()
    except Exception as exc:  # noqa: BLE001 - mapped to a value-free {"detail", "code"}
        raise rejected(request, "connection.update", exc, counts=fields) from None
    audit_action(request, action="connection.update", counts=fields)
    return result


@router.delete("/connections/{connection_id}", status_code=204, dependencies=_JOBS)
async def delete(connection_id: str, request: Request) -> Response:
    """Delete a connection (409 ``connection_in_use`` while a live job uses it; a
    production-tagged one needs admin)."""

    async def run() -> None:
        admin = principal_of(request).has_scope("admin")
        if not await delete_connection(tenant_of(request), connection_id, admin=admin):
            raise DpsHttpError(404, "connection_not_found", "connection not found")

    await audited(request, "connection.delete", run(), status=204)
    return Response(status_code=204)


def _failed(engine: str, check: str, code: str) -> ConnectionTestResult:
    return ConnectionTestResult(
        ok=False,
        engine=cast(Any, engine),
        checks=[{"name": check, "ok": False, "severity": "required", "code": code}],
        error_code=code,
    )


async def _test(request: Request, conn: Connection, body: ConnectionTestRequest) -> Any:
    settings, kek = _settings(request), kek_of(request)
    if body.role not in conn.roles:
        raise DpsHttpError(422, "connection_role", f"the connection has no {body.role} role")
    if conn.engine in ("s3", "pvc"):
        try:
            store = await open_target_store(conn, settings, kek=kek)
        except SecretUnavailable:
            return _failed(conn.engine, "credentials", "secret_unavailable")
        except ValueError:
            return _failed(conn.engine, "store", "target_invalid")
        return await test_store(store, conn)
    if conn.engine not in _TABULAR:  # pragma: no cover - the engine column is constrained
        raise DpsHttpError(422, "engine_unavailable", "unknown engine")
    try:
        creds = await load_credentials(conn, settings=settings, kek=kek)
    except SecretUnavailable:
        return _failed(conn.engine, "credentials", "secret_unavailable")
    try:
        connector = await open_connector(conn, settings, action="test", kek=kek, credentials=creds)
    except ConnectorError as exc:
        if exc.code == "engine_unavailable":
            raise
        return _failed(conn.engine, "connect", "connection_failed")
    try:
        return await connector.test(
            role=body.role, obj=_object(body.object), write_mode=body.write_mode
        )
    finally:
        await connector.close()


@router.post("/connections/{connection_id}/test", dependencies=_JOBS)
async def test(connection_id: str, body: ConnectionTestRequest, request: Request) -> dict[str, Any]:
    """Value-free connectivity and least-privilege checks for a role; stored as ``last_test``."""

    async def run() -> dict[str, Any]:
        tenant = tenant_of(request)
        conn = await _connection(request, connection_id)
        result: ConnectionTestResult = await _test(request, conn, body)
        await record_test(tenant, connection_id, result)
        return result.model_dump(mode="json")

    return await audited(request, "connection.test", run())


@router.get("/connections/{connection_id}/objects", dependencies=_JOBS)
async def objects(connection_id: str, request: Request, owner: str | None = None) -> dict[str, Any]:
    """Tables the connection's login may read (optionally of one owner/schema)."""

    async def run() -> dict[str, Any]:
        connector = await _open(request, await _connection(request, connection_id), "browse")
        try:
            items = await connector.list_objects(owner or None)
        finally:
            await connector.close()
        return {"items": [o.model_dump(mode="json") for o in items], "next_cursor": None}

    return await guarded(run())


@router.get("/connections/{connection_id}/describe", dependencies=_JOBS)
async def describe(
    connection_id: str, request: Request, object: str = Query(min_length=1, max_length=300)
) -> dict[str, Any]:
    """Columns, kinds and the paging key of ``OWNER.NAME``."""

    async def run() -> dict[str, Any]:
        obj = _object(object)
        assert obj is not None
        connector = await _open(request, await _connection(request, connection_id), "describe")
        try:
            shape = await connector.describe(obj)
        finally:
            await connector.close()
        return shape.model_dump(mode="json")

    return await guarded(run())


def _projection(shape: TableShape, sel: Selection) -> list[ColumnInfo]:
    if not sel.columns:
        return shape.columns
    wanted = set(sel.columns) | set(shape.key)
    return [c for c in shape.columns if c.name in wanted]


def runs_as(engine: Engine, shape: TableShape, bound: Selection) -> str | None:
    """The value-free statement DPS pages with: the selection's **template** (literals are
    ``:lN`` binds) in the connector's own SQL; ``None`` for engines without SQL."""
    if engine != "postgres":
        return None
    from dps.layers.connectors.postgres import select_sql

    body = bound.query or bound.where or ""
    sql, _ = select_sql(
        shape, bound, projection=_projection(shape, bound), body_sql=body, body_args=[]
    )
    return sql


def _bind(sel: Selection, engine: Engine, shape: TableShape | None) -> Selection:
    try:
        bound = parameterize_literals(sel, engine)
        validate_selection(bound, engine, shape)
    except SelectionError as exc:
        checks = failed_checks(sel, engine, shape, exc.code)
        raise DpsHttpError(422, exc.code, str(exc), extra={"checks": checks}) from None
    return bound


@router.post("/connections/{connection_id}/count", dependencies=_JOBS)
async def count(connection_id: str, body: CountRequest, request: Request) -> dict[str, Any]:
    """Rows the selection matches, with the checklist; a refused selection is 422
    ``{"detail", "code", "checks"}`` and no query runs."""

    async def run() -> dict[str, Any]:
        conn = await _connection(request, connection_id)
        if "source" not in conn.roles:
            raise DpsHttpError(422, "connection_role", "counting reads a source connection")
        obj = _object(body.object)
        assert obj is not None
        engine = cast(Engine, conn.engine)
        bound = _bind(body.selection, engine, None)  # refused before anything is opened
        connector = await _open(request, conn, "count")
        try:
            shape = await connector.describe(obj)
            bound = _bind(body.selection, engine, shape)
            rows = await connector.count(shape, bound, bound.params)
        finally:
            await connector.close()
        return {
            "rows": rows,
            "checks": selection_checks(bound, engine, shape),
            "runs_as": runs_as(engine, shape, bound),
            "binds": len(bound.params),
        }

    return await audited(request, "connection.count", run())
