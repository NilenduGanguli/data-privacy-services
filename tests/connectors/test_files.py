"""Files targets: CSV/Parquet/JSONL part files per partition and the manifest (offline)."""

from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from dps.layers.connectors.base import ColumnInfo, FilesSpec, LedgerConflict, LedgerMark
from dps.layers.connectors.files import (
    EXTENSIONS,
    FilesWriter,
    arrow_schema,
    build_manifest,
    write_manifest,
)
from dps.storage import LocalObjectStore

COLS = [
    ColumnInfo(path="$.id", name="id", data_type="bigint", kind="int"),
    ColumnInfo(
        path="$.amount",
        name="amount",
        data_type="numeric(14,2)",
        kind="decimal",
        precision=14,
        scale=2,
    ),
    ColumnInfo(path="$.day", name="day", data_type="date", kind="date"),
    ColumnInfo(path="$.at", name="at", data_type="timestamp with time zone", kind="timestamp"),
    ColumnInfo(path="$.name", name="name", data_type="text", kind="text"),
]
ROWS = [
    {
        "id": i,
        "amount": Decimal(f"{i}.50"),
        "day": date(2025, 1, 1 + i),
        "at": datetime(2025, 1, 1, tzinfo=UTC),
        "name": None if i == 2 else f"t{i}",
    }
    for i in range(5)
]
MARK = LedgerMark(job_id="j_f", partition_no=0, batch_no=1, last_key=[], rows=0)


@pytest.mark.parametrize(
    "fmt,compression",
    [("csv", "none"), ("csv", "gzip"), ("jsonl", "none"), ("jsonl", "gzip"), ("parquet", "snappy")],
)
async def test_formats_round_trip(tmp_path: Path, fmt: str, compression: str) -> None:
    store = LocalObjectStore(tmp_path / "out")
    w = FilesWriter(
        store,
        FilesSpec(format=fmt, compression=compression, prefix="crm/customers"),  # type: ignore[arg-type]
        job_id="j_f",
        base_prefix="exports",
        columns=COLS,
        spool_root=tmp_path / "spool",
    )
    assert await w.write_batch(ROWS, mark=MARK) == 5
    meta = await w.finish_partition("j_f", 0)
    [part] = meta["files"]
    assert part["key"].startswith("exports/crm/customers/j_f/part-00000-000.") and part["rows"] == 5
    assert part["key"].endswith(EXTENSIONS[fmt] + (".gz" if compression == "gzip" else ""))
    data = await store.get(part["key"])
    assert part["sha256"] == hashlib.sha256(data).hexdigest() and part["bytes"] == len(data)
    if fmt == "parquet":
        table = pq.read_table(io.BytesIO(data))
        assert table.num_rows == 5 and table.column("amount").to_pylist()[1] == Decimal("1.50")
        assert table.column("day").to_pylist()[0] == date(2025, 1, 1)
        assert table.schema.field("at").type == pa.timestamp("us", tz="UTC")
    else:
        text = (gzip.decompress(data) if compression == "gzip" else data).decode()
        rows = (
            list(csv.DictReader(io.StringIO(text)))
            if fmt == "csv"
            else [json.loads(x) for x in text.splitlines()]
        )
        assert len(rows) == 5 and str(rows[1]["amount"]) == "1.50" and rows[2]["name"] in ("", None)
        assert rows[0]["day"] == "2025-01-01"
    assert not (tmp_path / "spool").exists() or not any((tmp_path / "spool").iterdir())


async def test_rows_per_file_split_reset_and_manifest(tmp_path: Path) -> None:
    store = LocalObjectStore(tmp_path / "out")
    spec = FilesSpec(format="jsonl", compression="none", rows_per_file=1000)
    w = FilesWriter(
        store, spec, job_id="j_f", base_prefix="", columns=COLS, spool_root=tmp_path / "spool"
    )
    many = [dict(ROWS[0], id=i) for i in range(2500)]
    await w.write_batch(many, mark=MARK)
    await w.reset_partition(0)  # a resumed partition starts again
    await w.write_batch(many[:1200], mark=MARK)
    await w.write_batch(many[1200:], mark=MARK.model_copy(update={"batch_no": 2}))
    meta = await w.finish_partition("j_f", 0)
    assert [f["rows"] for f in meta["files"]] == [1000, 1000, 500]
    assert [f["key"] for f in meta["files"]] == [
        "j_f/part-00000-000.jsonl",
        "j_f/part-00000-001.jsonl",
        "j_f/part-00000-002.jsonl",
    ]
    manifest = build_manifest(
        job_id="j_f",
        spec=spec,
        columns=COLS,
        parts=meta["files"],
        created_at=datetime(2026, 10, 11, tzinfo=UTC),
    )
    assert manifest["rows_total"] == 2500 and [c["name"] for c in manifest["columns"]][0] == "id"
    assert manifest["columns"][1] == {"name": "amount", "type": "numeric(14,2)"}
    obj = await write_manifest(store, w.base_key(), manifest)
    assert obj.key == "j_f/manifest.json"
    assert json.loads(await store.get(obj.key))["objects"][2]["rows"] == 500


async def test_partitions_are_independent_and_deterministic(tmp_path: Path) -> None:
    store = LocalObjectStore(tmp_path / "out")
    spec = FilesSpec(format="csv", compression="gzip", prefix="x")
    w = FilesWriter(
        store, spec, job_id="j_d", base_prefix="", columns=COLS, spool_root=tmp_path / "spool"
    )
    await w.write_batch(ROWS[:2], mark=MARK)
    await w.write_batch(ROWS[2:], mark=MARK.model_copy(update={"partition_no": 1}))
    first = await w.finish_partition("j_d", 0)
    second = await w.finish_partition("j_d", 1)
    assert first["files"][0]["key"] == "x/j_d/part-00000-000.csv.gz"
    assert second["files"][0]["key"] == "x/j_d/part-00001-000.csv.gz"
    assert (first["files"][0]["rows"], second["files"][0]["rows"]) == (2, 3)
    # a restarted partition rewrites the same object with the same bytes
    await w.reset_partition(0)
    await w.write_batch(ROWS[:2], mark=MARK)
    again = await w.finish_partition("j_d", 0)
    assert again["files"][0]["sha256"] == first["files"][0]["sha256"]
    assert await w.finish_partition("j_d", 7) == {"files": []}  # an empty partition


async def test_writer_protocol_no_ops_probe_and_value_conversion(tmp_path: Path) -> None:
    store = LocalObjectStore(tmp_path / "out")
    w = FilesWriter(
        store,
        FilesSpec(format="jsonl", compression="snappy"),
        job_id="j_p",
        base_prefix="exp",
        columns=[
            ColumnInfo(path="$.k", name="k", data_type="uuid", kind="uuid"),
            ColumnInfo(path="$.b", name="b", data_type="bytea", kind="binary"),
            ColumnInfo(path="$.ok", name="ok", data_type="boolean", kind="bool"),
            ColumnInfo(path="$.n", name="n", data_type="numeric", kind="decimal"),
        ],
        spool_root=tmp_path / "spool",
    )
    report = await w.prepare(dry_run=False)
    assert report.checks and report.checks[0]["ok"]
    assert not list((tmp_path / "out").rglob("*probe*"))  # the probe object was removed
    assert await w.read_ledger("j_p", 0) is None and await w.clear_ledger("j_p") == 0

    async def keys():  # type: ignore[no-untyped-def]
        yield [(1,)]

    assert await w.precheck_collisions(keys()) == 0
    u = UUID(int=7)
    await w.write_batch([{"k": u, "b": b"\x01\xff", "ok": True, "n": Decimal("1E+3")}], mark=MARK)
    [part] = (await w.finish_partition("j_p", 0))["files"]
    assert part["key"] == "exp/j_p/part-00000-000.jsonl"  # snappy applies to parquet only
    row = json.loads(await store.get(part["key"]))
    assert row == {"k": str(u), "b": "01ff", "ok": True, "n": "1000"}
    schema = arrow_schema(
        [
            ColumnInfo(path="$.n", name="n", data_type="numeric", kind="decimal"),
            ColumnInfo(
                path="$.t", name="t", data_type="timestamp without time zone", kind="timestamp"
            ),
            ColumnInfo(
                path="$.w",
                name="w",
                data_type="numeric(60,2)",
                kind="decimal",
                precision=60,
                scale=2,
            ),
        ]
    )
    assert schema.field("n").type == pa.string() and schema.field("w").type == pa.string()
    assert schema.field("t").type == pa.timestamp("us")
    await w.close()


async def test_a_failing_store_fails_the_probe(tmp_path: Path) -> None:
    from dps.layers.connectors.targets import PrepareFailure

    blocked = tmp_path / "blocked"
    blocked.write_text("a file where the store root should be", encoding="utf-8")
    w = FilesWriter(
        LocalObjectStore(blocked),
        FilesSpec(),
        job_id="j_x",
        base_prefix="",
        columns=COLS,
        spool_root=tmp_path / "spool",
    )
    assert (await w.prepare(dry_run=True)).checks == []
    with pytest.raises(PrepareFailure) as err:
        await w.prepare(dry_run=False)
    assert err.value.code == "target_invalid"


async def test_two_holders_sharing_one_writer_cannot_interleave_a_spool(tmp_path: Path) -> None:
    """A zombie and the re-claimed holder of one partition share the job's cached writer: a
    spool takes each batch number exactly once, in order, so its part file never mixes them."""
    store = LocalObjectStore(tmp_path / "out")
    spec = FilesSpec(format="jsonl", compression="none")
    w = FilesWriter(
        store, spec, job_id="j_z", base_prefix="", columns=COLS, spool_root=tmp_path / "spool"
    )

    def mark(batch: int) -> LedgerMark:
        return MARK.model_copy(update={"batch_no": batch})

    await w.write_batch(ROWS[:1], mark=mark(1))  # holder A
    await w.write_batch(ROWS[1:2], mark=mark(2))
    await w.reset_partition(0)  # A's lease is lost; B re-claims and starts again
    await w.write_batch(ROWS[:1], mark=mark(1))  # holder B
    with pytest.raises(LedgerConflict):  # the zombie's next batch is out of sequence
        await w.write_batch(ROWS[2:3], mark=mark(3))
    with pytest.raises(LedgerConflict):  # a batch the spool already holds
        await w.write_batch(ROWS[:1], mark=mark(1))
    with pytest.raises(LedgerConflict):  # the zombie finishing: the spool is B's, at batch 1
        await w.finish_partition("j_z", 0, batch_no=2)
    await w.write_batch(ROWS[1:3], mark=mark(2))
    await w.write_batch(ROWS[3:], mark=mark(3))
    meta = await w.finish_partition("j_z", 0, batch_no=3)
    [part] = meta["files"]
    lines = (await store.get(part["key"])).decode().splitlines()
    assert part["rows"] == 5 and [json.loads(line)["id"] for line in lines] == [0, 1, 2, 3, 4]
    with pytest.raises(LedgerConflict):  # a zombie writing after the upload starts no new spool
        await w.write_batch(ROWS[4:], mark=mark(4))
    with pytest.raises(LedgerConflict):  # nor uploads anything
        await w.finish_partition("j_z", 0, batch_no=3)
    assert await w.finish_partition("j_z", 0, batch_no=0) == {"files": []}  # an empty partition
    await w.close()
    assert not (tmp_path / "spool").exists() or not any((tmp_path / "spool").iterdir())
