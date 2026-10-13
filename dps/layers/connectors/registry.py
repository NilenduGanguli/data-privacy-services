"""Connection → connector / object store (contract §1). M3 adds the oracle and mongo branches."""

from __future__ import annotations

from typing import Any

from dps.config import Settings
from dps.connections import Connection, Credentials, load_credentials
from dps.layers.connectors.base import ConnectionTestResult, Connector, ConnectorError
from dps.storage import ObjectStore, StorageError, target_store


async def open_connector(
    conn: Connection,
    settings: Settings,
    *,
    action: str,
    kek: bytes | None,
    job_id: str | None = None,
    credentials: Credentials | None = None,
) -> Connector:
    """An **opened** connector for a database connection (the caller closes it).

    ``job_id`` tags the sessions (PostgreSQL ``application_name``, Oracle ``client_identifier``,
    MongoDB ``comment``). The Oracle and MongoDB drivers are imported only here, so a host without
    the ``connectors`` extra still serves PostgreSQL.

    Raises:
        ConnectorError: ``engine_not_tabular`` for ``s3``/``pvc`` (file targets only),
            ``engine_unavailable`` for ``oracle``/``mongo`` without the driver extra,
            ``secret_unavailable`` for an Oracle secret without a username or password,
            ``database_required``/``connection_invalid`` for a MongoDB connection without a
            database or with a URI/host that does not parse, and the connector's own
            ``connection_failed`` (retryable only for a network failure).
    """
    if conn.engine in ("s3", "pvc"):
        raise ConnectorError(
            "engine_not_tabular", "file stores are targets for the files write mode"
        )
    if conn.engine == "postgres":
        from dps.layers.connectors.postgres import PostgresConnector  # Task 15

        creds = credentials or await load_credentials(conn, settings=settings, kek=kek)
        connector = PostgresConnector(conn, creds, settings, action=action, job_id=job_id)
        await connector.open()
        return connector
    if conn.engine == "oracle":
        try:
            from dps.layers.connectors.oracle import OracleConnector
        except ImportError:
            raise ConnectorError(
                "engine_unavailable", "the oracle driver extra is not installed"
            ) from None
        creds = credentials or await load_credentials(conn, settings=settings, kek=kek)
        oracle = OracleConnector.from_secret(
            _flat_secret(creds), conn.params, settings, action=action, client_id=job_id
        )
        try:
            await oracle.open()
        except BaseException:
            await oracle.close()
            raise
        return oracle
    if conn.engine == "mongo":
        try:
            from dps.layers.connectors.mongo import MongoConnector
        except ImportError:
            raise ConnectorError(
                "engine_unavailable", "the mongo driver extra is not installed"
            ) from None
        creds = credentials or await load_credentials(conn, settings=settings, kek=kek)
        mongo = MongoConnector.from_secret(
            _flat_secret(creds), conn.params, settings, action=action, client_id=job_id
        )
        try:
            await mongo.open()
        except BaseException:
            await mongo.close()
            raise
        return mongo
    raise ConnectorError("engine_unavailable", f"no tabular connector for {conn.engine}")


def _flat_secret(creds: Credentials) -> dict[str, str]:
    """``username``/``password`` and the extra files (``dsn``, ``uri``) as plain strings, for a
    connector's ``from_secret`` (which keeps them out of ``repr`` and errors)."""
    secret = {"username": creds.username, "password": creds.password.get_secret_value()}
    secret.update({name: value.get_secret_value() for name, value in creds.extra.items()})
    return secret


async def open_target_store(
    conn: Connection, settings: Settings, *, kek: bytes | None
) -> ObjectStore:
    """The object store of a ``files`` target connection, with its credentials opened."""
    creds = (
        None if conn.engine == "pvc" else await load_credentials(conn, settings=settings, kek=kek)
    )
    return target_store(conn, settings, creds)


async def test_store(store: ObjectStore, conn: Connection) -> ConnectionTestResult:
    """Least-privilege check for a files target: put and delete a probe object (no customer data)."""
    key = f"dps-probe/{conn.id}.txt"  # object keys must start with a letter or digit
    checks: list[dict[str, Any]] = []
    try:
        await store.put(key, b"dps connection test", content_type="text/plain")
        checks.append({"name": "put_object", "ok": True, "severity": "required"})
        await store.delete(key)
        checks.append({"name": "delete_object", "ok": True, "severity": "required"})
    except StorageError as exc:
        checks.append(
            {
                "name": "put_object" if not checks else "delete_object",
                "ok": False,
                "severity": "required",
                "code": exc.code,
            }
        )
    ok = bool(checks) and all(c["ok"] for c in checks)
    return ConnectionTestResult(
        ok=ok,
        engine=conn.engine,
        checks=checks,
        capabilities={"objects": ok},
        error_code=None if ok else "storage_error",
    )


# A contract name (§1) that starts with "test_": keep pytest from collecting it where imported.
test_store.__test__ = False  # type: ignore[attr-defined]
