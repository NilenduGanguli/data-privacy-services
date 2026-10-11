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

    Raises:
        ConnectorError: ``engine_not_tabular`` for ``s3``/``pvc`` (file targets only) and
            ``engine_unavailable`` for engines whose connector arrives later (``oracle``,
            ``mongo``: milestone M3); neither is retryable.
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
    raise ConnectorError(
        "engine_unavailable", f"the {conn.engine} connector arrives with milestone M3"
    )


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
