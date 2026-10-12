"""Files targets on MinIO (contract §13 key layout)."""

from __future__ import annotations

import hashlib
import io
from decimal import Decimal
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from dps.layers.connectors.base import FilesSpec
from dps.layers.connectors.files import FilesWriter
from dps.storage import S3ObjectStore
from tests.connectors.test_files import COLS, MARK, ROWS

pytestmark = [pytest.mark.integration, pytest.mark.s3]


async def test_parquet_part_on_s3(s3_store: S3ObjectStore, tmp_path: Path) -> None:
    w = FilesWriter(
        s3_store,
        FilesSpec(format="parquet", compression="zstd", prefix="files-test"),
        job_id="j_s3files",
        base_prefix="tests",
        columns=COLS,
        spool_root=tmp_path / "spool",
    )
    assert (await w.prepare(dry_run=False)).checks[0]["ok"]
    await w.write_batch(ROWS, mark=MARK)
    [part] = (await w.finish_partition("j_s3files", 0))["files"]
    try:
        assert part["key"] == "tests/files-test/j_s3files/part-00000-000.parquet"
        data = await s3_store.get(part["key"])
        assert hashlib.sha256(data).hexdigest() == part["sha256"]
        table = pq.read_table(io.BytesIO(data))
        assert table.num_rows == 5 and table.column("amount").to_pylist()[2] == Decimal("2.50")
    finally:
        await s3_store.delete(part["key"])
    assert not await s3_store.exists(part["key"])
