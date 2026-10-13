"""The registry's Oracle branch: credentials flattened for ``from_secret``, the job id as the
session's ``client_identifier``, an opened connector, ``engine_unavailable`` without the driver.

Credentials are passed (or ``load_credentials`` replaced) instead of resolving a ``secret_ref``
directory: the queued SEC-2 fix scopes refs per tenant, and this test must not depend on the layout.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import SecretStr

from dps.config import Settings
from dps.connections import Connection, Credentials
from dps.jobs_models import ConnectionParams
from dps.layers.connectors import registry
from dps.layers.connectors.base import ConnectorError

pytest.importorskip("oracledb")

from dps.layers.connectors.oracle import OracleConnector  # noqa: E402

SETTINGS = Settings(_env_file=None)


def connection_row(**fields: Any) -> Connection:
    """M2's ``Connection`` row model with defaults for the fields a test does not set."""
    now = datetime.now(UTC)
    base: dict[str, Any] = {
        "id": "c_test",
        "tenant_id": "t_test",
        "name": "ora",
        "engine": "oracle",
        "params": ConnectionParams(host="oracle", port=1521, service_name="FREEPDB1"),
        "secret_ref": "dps/src/ora",
        "has_secret": False,
        "secret_expires_at": None,
        "roles": ["source", "target"],
        "tags": [],
        "capabilities": {},
        "last_test": None,
        "created_by": None,
        "created_at": now,
        "updated_at": now,
    }
    base.update(fields)
    return Connection(**base)


@pytest.fixture
def opened(monkeypatch: pytest.MonkeyPatch) -> list[OracleConnector]:
    calls: list[OracleConnector] = []

    async def no_open(self: OracleConnector) -> None:  # no network in a unit test
        calls.append(self)

    monkeypatch.setattr(OracleConnector, "open", no_open)
    return calls


async def test_registry_builds_oracle_connectors_with_session_tags(
    opened: list[OracleConnector],
) -> None:
    creds = Credentials(
        username="dps_ro",
        password=SecretStr("SENTINEL-PW"),
        extra={"dsn": SecretStr("oracle:1521/FREEPDB1")},
    )
    c = await registry.open_connector(
        connection_row(), SETTINGS, action="partitions", kek=None, job_id="j_01", credentials=creds
    )
    try:
        assert isinstance(c, OracleConnector) and opened == [c]
        assert c._action == "partitions" and c._pool._client_identifier == "j_01"
        assert c._pool._dsn == "oracle:1521/FREEPDB1" and c._pool._user == "dps_ro"
        assert "SENTINEL" not in repr(c) and "SENTINEL" not in repr(c._pool)
    finally:
        await c.close()


async def test_registry_loads_credentials_and_falls_back_to_easy_connect(
    opened: list[OracleConnector], monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[str] = []

    async def load(conn: Connection, *, settings: Settings, kek: bytes | None) -> Credentials:
        seen.append(conn.id)
        return Credentials(username="crm", password=SecretStr("pw"))

    monkeypatch.setattr(registry, "load_credentials", load)
    c = await registry.open_connector(connection_row(), SETTINGS, action="discover", kek=None)
    try:
        assert seen == ["c_test"] and isinstance(c, OracleConnector)
        assert c._pool._dsn == "oracle:1521/FREEPDB1" and c._pool._client_identifier == ""
    finally:
        await c.close()


async def test_a_secret_without_a_password_is_refused(opened: list[OracleConnector]) -> None:
    with pytest.raises(ConnectorError) as err:
        await registry.open_connector(
            connection_row(),
            SETTINGS,
            action="test",
            kek=None,
            credentials=Credentials(username="crm"),
        )
    assert err.value.code == "secret_unavailable" and not opened


async def test_a_failed_open_closes_the_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    closed: list[OracleConnector] = []

    async def refuse(self: OracleConnector) -> None:
        raise ConnectorError("connection_failed", "ORA-01017")

    async def close(self: OracleConnector) -> None:
        closed.append(self)

    monkeypatch.setattr(OracleConnector, "open", refuse)
    monkeypatch.setattr(OracleConnector, "close", close)
    creds = Credentials(username="crm", password=SecretStr("pw"))
    with pytest.raises(ConnectorError) as err:
        await registry.open_connector(
            connection_row(), SETTINGS, action="test", kek=None, credentials=creds
        )
    assert err.value.code == "connection_failed" and len(closed) == 1


async def test_without_the_driver_oracle_is_engine_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "dps.layers.connectors.oracle", None)  # import fails
    creds = Credentials(username="crm", password=SecretStr("pw"))
    with pytest.raises(ConnectorError) as err:
        await registry.open_connector(
            connection_row(), SETTINGS, action="test", kek=None, credentials=creds
        )
    assert err.value.code == "engine_unavailable" and not err.value.retryable


async def test_mongo_stays_unavailable_until_its_connector_lands() -> None:
    conn = connection_row(engine="mongo", params=ConnectionParams(database="crm"))
    with pytest.raises(ConnectorError) as err:
        await registry.open_connector(
            conn, SETTINGS, action="test", kek=None, credentials=Credentials()
        )
    assert err.value.code == "engine_unavailable"
