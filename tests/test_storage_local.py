"""LocalObjectStore: atomic writes, key confinement, streaming, idempotent delete, file modes
(offline)."""

from __future__ import annotations

import errno
import hashlib
import os
import stat
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from dps.config import Settings
from dps.storage import (
    ARTEFACT_EXT,
    LocalObjectStore,
    S3ObjectStore,
    StorageError,
    artefact_store,
)

#: The umask the image's entrypoint sets for every role (docker/entrypoint.sh, M5 Task 24).
IMAGE_UMASK = 0o002


async def _chunks():  # noqa: ANN202
    for part in (b"a" * 10, b"b" * 5):
        yield part


@pytest.fixture
def image_umask() -> Iterator[None]:
    """Run under the image's umask (a test may set another); the process umask comes back after."""
    old = os.umask(IMAGE_UMASK)
    try:
        yield
    finally:
        os.umask(old)


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


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


async def test_a_stored_object_is_shared_with_the_group_and_never_with_other_users(  # noqa: ANN001
    tmp_path, image_umask
) -> None:
    """Under the image's umask every object is 0660: any UID of the volume's group (group 0 in
    the image: compose's 10001, an OpenShift namespace UID, the next UID after a redeploy) reads
    and replaces it, and other users get nothing. mkstemp's own 0600 locked the next UID out of
    every earlier artefact and export (review S4 r1 B1: 500/502 on older jobs' Evidence pages)."""
    store = LocalObjectStore(tmp_path)
    await store.put("t/t1/j_1/art_1.html", b"<p>pack</p>", content_type="text/html")
    await store.put("x/j_1/part-00001.csv", _chunks(), content_type="text/csv")  # spooled parts
    await store.put("t/t1/j_1/art_1.html", b"<p>again</p>", content_type="text/html")  # replaced
    assert _mode(tmp_path / "t/t1/j_1/art_1.html") == 0o660
    assert _mode(tmp_path / "x/j_1/part-00001.csv") == 0o660
    assert await store.get("t/t1/j_1/art_1.html") == b"<p>again</p>"


@pytest.mark.parametrize(
    ("mask", "mode"),
    [(0o000, 0o660), (0o002, 0o660), (0o022, 0o640), (0o077, 0o600)],
    ids=["umask-000", "umask-002-image", "umask-022", "umask-077"],
)
async def test_a_stricter_umask_narrows_a_stored_object_and_others_never_get_access(  # noqa: ANN001
    tmp_path, image_umask, mask: int, mode: int
) -> None:
    """The object is created with 0660 and the kernel applies the process umask: an operator's
    stricter umask still narrows it, and not even umask 0 opens it to other users."""
    os.umask(mask)
    await LocalObjectStore(tmp_path).put("k/obj.bin", b"x", content_type="text/plain")
    assert _mode(tmp_path / "k" / "obj.bin") == mode


async def test_an_s3_upload_spools_privately_in_the_temp_directory(  # noqa: ANN001
    monkeypatch, image_umask
) -> None:
    """A spool for an S3 upload lives in the system temp directory (shared on a host): owner
    only, whatever the umask. Only objects in a local store are shared with the group."""
    pytest.importorskip("boto3")
    store = S3ObjectStore(
        bucket="b",
        endpoint_url="http://127.0.0.1:1",
        region="us-east-1",
        access_key_id="id",
        secret_access_key="secret",
        force_path_style=True,
    )
    modes: list[int] = []

    class _Client:
        def upload_file(self, filename: str, *_: Any, **__: Any) -> None:
            modes.append(_mode(Path(filename)))

    monkeypatch.setattr(store, "_client", _Client())
    os.umask(0o000)
    await store.put("k/obj.bin", _chunks(), content_type="application/octet-stream")
    assert modes == [0o600]


async def test_an_unreadable_object_is_a_storage_error_on_every_read(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    """``get``, ``stream`` and ``exists`` answer ``storage_error`` (the API's 502) for an object
    the process may not read, never a bare OSError (a 500 ``internal_error`` on
    ``GET /artefacts/{id}``); the error names the class only, never the path."""
    store = LocalObjectStore(tmp_path)
    await store.put("t/t1/j_1/art_1.html", b"<p>pack</p>", content_type="text/html")
    real_open, real_stat = Path.open, Path.stat

    def denied_open(self: Path, *args: Any, **kwargs: Any) -> Any:
        if self.name == "art_1.html":
            raise PermissionError(errno.EACCES, "Permission denied", str(self))
        return real_open(self, *args, **kwargs)

    def denied_stat(self: Path, *args: Any, **kwargs: Any) -> Any:
        if self.name == "art_1.html":
            raise PermissionError(errno.EACCES, "Permission denied", str(self))
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", denied_open)
    monkeypatch.setattr(Path, "stat", denied_stat)
    for read in (
        lambda: store.get("t/t1/j_1/art_1.html"),
        lambda: _collect(store.stream("t/t1/j_1/art_1.html")),
        lambda: store.exists("t/t1/j_1/art_1.html"),
    ):
        with pytest.raises(StorageError) as err:
            await read()
        assert (err.value.code, err.value.detail) == ("storage_error", "PermissionError")
        assert "art_1" not in str(err.value) and "t1" not in str(err.value)


async def test_a_read_failure_mid_stream_is_a_storage_error_and_closes_the_file(  # noqa: ANN001
    tmp_path, monkeypatch
) -> None:
    store = LocalObjectStore(tmp_path)
    await store.put("p/obj.bin", b"a" * 10, content_type="application/octet-stream")

    class _Failing:
        closed = False

        def read(self, _size: int) -> bytes:
            raise OSError(errno.EIO, "Input/output error")

        def close(self) -> None:
            self.closed = True

    handle = _Failing()
    monkeypatch.setattr(Path, "open", lambda *_a, **_k: handle)
    with pytest.raises(StorageError) as err:
        await _collect(store.stream("p/obj.bin"))
    assert (err.value.code, err.value.detail) == ("storage_error", "OSError")
    assert handle.closed


async def _collect(chunks: Any) -> bytes:
    return b"".join([c async for c in chunks])
