"""LocalObjectStore: atomic writes, key confinement, streaming, idempotent delete (offline)."""

from __future__ import annotations

import hashlib

import pytest

from dps.config import Settings
from dps.storage import (
    ARTEFACT_EXT,
    LocalObjectStore,
    S3ObjectStore,
    StorageError,
    artefact_store,
)


async def _chunks():  # noqa: ANN202
    for part in (b"a" * 10, b"b" * 5):
        yield part


async def test_put_get_stream_delete(tmp_path) -> None:  # noqa: ANN001
    store = LocalObjectStore(tmp_path)
    obj = await store.put("t/t1/j_1/art_1.json", b'{"ok":true}', content_type="application/json")
    assert obj.store == "local" and obj.bytes == 11
    assert obj.sha256 == hashlib.sha256(b'{"ok":true}').hexdigest()
    assert await store.get("t/t1/j_1/art_1.json") == b'{"ok":true}'
    assert await store.exists("t/t1/j_1/art_1.json")
    streamed = await store.put("x/y.bin", _chunks(), content_type="application/octet-stream")
    assert streamed.bytes == 15
    assert b"".join([c async for c in store.stream("x/y.bin", chunk=4)]) == b"a" * 10 + b"b" * 5
    await store.delete("x/y.bin")
    await store.delete("x/y.bin")  # idempotent
    assert not await store.exists("x/y.bin")


@pytest.mark.parametrize(
    "key", ["../escape", "/abs", "a/../../b", "", "a\\b", "a b", "a//b", "a/./b", "a/"]
)
async def test_keys_cannot_escape_or_carry_odd_characters(tmp_path, key: str) -> None:  # noqa: ANN001
    with pytest.raises(StorageError) as err:
        await LocalObjectStore(tmp_path).put(key, b"x", content_type="text/plain")
    assert err.value.code == "storage_key_invalid"


async def test_missing_object(tmp_path) -> None:  # noqa: ANN001
    store = LocalObjectStore(tmp_path)
    with pytest.raises(StorageError) as err:
        await store.get("nope.json")
    assert err.value.code == "storage_not_found"
    with pytest.raises(StorageError) as err2:
        _ = [c async for c in store.stream("nope.json")]
    assert err2.value.code == "storage_not_found"


async def test_overwrite_is_atomic_and_a_failed_stream_leaves_nothing(tmp_path) -> None:  # noqa: ANN001
    store = LocalObjectStore(tmp_path)
    await store.put("p/obj.bin", b"first", content_type="application/octet-stream")
    await store.put("p/obj.bin", b"second", content_type="application/octet-stream")
    assert await store.get("p/obj.bin") == b"second"

    async def broken():  # noqa: ANN202
        yield b"partial"
        raise RuntimeError("source went away")

    with pytest.raises(RuntimeError):
        await store.put("p/other.bin", broken(), content_type="application/octet-stream")
    assert not await store.exists("p/other.bin")
    assert sorted(p.name for p in (tmp_path / "p").iterdir()) == ["obj.bin"]  # no spool left


def test_artefact_store_follows_the_settings(tmp_path) -> None:  # noqa: ANN001
    local = artefact_store(Settings(_env_file=None, artefact_dir=str(tmp_path / "a")))  # type: ignore[call-arg]
    assert isinstance(local, LocalObjectStore) and local.root == (tmp_path / "a").resolve()
    pytest.importorskip("boto3")
    s3 = artefact_store(
        Settings(  # type: ignore[call-arg]
            _env_file=None,
            artefact_store="s3",
            s3_endpoint_url="http://127.0.0.1:1",
            s3_access_key_id="id",
            s3_secret_access_key="secret",
        )
    )
    assert isinstance(s3, S3ObjectStore) and s3.name == "s3" and s3.bucket == "dps-artefacts"
    assert ARTEFACT_EXT["evidence_html"] == "html" and ARTEFACT_EXT["manifest"] == "json"
