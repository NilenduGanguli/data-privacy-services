"""S3ObjectStore against MinIO (``docker compose --profile sources up -d minio minio-init``)."""

from __future__ import annotations

import os

import pytest

from dps.storage import StorageError

pytestmark = [pytest.mark.integration, pytest.mark.s3]


async def test_round_trip_and_multipart(s3_store) -> None:  # noqa: ANN001
    big = os.urandom(17 * 1024 * 1024)
    obj = await s3_store.put("tests/big.bin", big, content_type="application/octet-stream")
    assert obj.store == "s3" and obj.bytes == len(big)
    assert await s3_store.get("tests/big.bin") == big
    assert b"".join([c async for c in s3_store.stream("tests/big.bin")]) == big
    await s3_store.delete("tests/big.bin")
    assert not await s3_store.exists("tests/big.bin")


async def test_missing_object_and_bad_keys(s3_store) -> None:  # noqa: ANN001
    assert not await s3_store.exists("tests/never-written.bin")
    with pytest.raises(StorageError) as err:
        await s3_store.get("tests/never-written.bin")
    assert err.value.code == "storage_not_found"
    await s3_store.delete("tests/never-written.bin")  # idempotent, like the local store
    with pytest.raises(StorageError) as bad:
        await s3_store.put("../escape", b"x", content_type="text/plain")
    assert bad.value.code == "storage_key_invalid"
