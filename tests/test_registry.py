"""Connector registry and files-target stores (offline)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from dps.config import Settings
from dps.connections import Connection, Credentials
from dps.jobs_models import ConnectionParams
from dps.layers.connectors.base import ConnectorError
from dps.layers.connectors.registry import open_connector, open_target_store, test_store
from dps.storage import LocalObjectStore, S3ObjectStore, target_store


def _conn(engine: str, **params) -> Connection:  # noqa: ANN003
    now = datetime.now(UTC)
    return Connection(
        id="c_1",
        tenant_id="t1",
        name="x",
        engine=engine,
        params=params,
        secret_ref=None,
        has_secret=False,
        secret_expires_at=None,
        roles=["target"],
        tags=[],
        capabilities={},
        last_test=None,
        created_by=None,
        created_at=now,
        updated_at=now,
    )


@pytest.mark.parametrize(
    "engine,code",
    [
        # oracle, mongo: tests/connectors/test_registry_m3.py (M3 wires their connectors)
        ("s3", "engine_not_tabular"),
        ("pvc", "engine_not_tabular"),
    ],
)
async def test_engines_without_a_tabular_connector(engine: str, code: str) -> None:
    with pytest.raises(ConnectorError) as err:
        await open_connector(
            _conn(engine),
            Settings(_env_file=None),  # type: ignore[call-arg]
            action="test",
            kek=None,
            credentials=Credentials(),
        )
    assert err.value.code == code and err.value.retryable is False


async def test_pvc_store_is_confined_and_testable(tmp_path) -> None:  # noqa: ANN001
    settings = Settings(_env_file=None, export_root=str(tmp_path))  # type: ignore[call-arg]
    store = target_store(_conn("pvc", base_path="exports/crm"), settings)
    assert isinstance(store, LocalObjectStore)
    assert store.root == (tmp_path / "exports" / "crm").resolve()
    result = await test_store(store, _conn("pvc", base_path="exports/crm"))
    assert result.ok and [c["name"] for c in result.checks] == ["put_object", "delete_object"]
    assert not any((tmp_path / "exports" / "crm").rglob("*.txt"))  # the probe is gone
    assert isinstance(
        await open_target_store(_conn("pvc", base_path="x"), settings, kek=None), LocalObjectStore
    )
    # the spec model already refuses "..", so build the row as a corrupted stored one would look
    sneaky = Connection.model_construct(
        **{
            **dict(_conn("pvc", base_path="x")),
            "params": ConnectionParams.model_construct(base_path="../../etc"),
        }
    )
    with pytest.raises(ValueError):
        target_store(sneaky, settings)


async def test_a_failing_store_is_reported_by_code(tmp_path) -> None:  # noqa: ANN001
    blocker = tmp_path / "ro"
    blocker.write_text("a file where the store root should be")
    result = await test_store(LocalObjectStore(blocker), _conn("pvc", base_path="ro"))
    assert result.ok is False and result.error_code == "storage_error"
    assert result.checks[0]["name"] == "put_object" and result.checks[0]["ok"] is False


def test_s3_store_takes_keys_from_the_credentials() -> None:
    pytest.importorskip("boto3")
    settings = Settings(_env_file=None, s3_endpoint_url="http://127.0.0.1:1")  # type: ignore[call-arg]
    store = target_store(
        _conn("s3", bucket="dps-exports", region="eu-west-1"),
        settings,
        Credentials(username="AKIA", password="pw"),  # type: ignore[arg-type]
    )
    assert isinstance(store, S3ObjectStore) and store.bucket == "dps-exports"
    with pytest.raises(ValueError):
        target_store(_conn("postgres", host="h", database="d"), settings)


def test_the_probe_helper_is_not_collected_as_a_test() -> None:
    assert getattr(test_store, "__test__", True) is False
