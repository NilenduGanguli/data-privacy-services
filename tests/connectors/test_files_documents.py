"""Files targets for the M3 engines (offline; S1-M3 post-merge verification r1, engines B3 and B4).

* B3 — a MongoDB field holding a sub-document or an array went to ``json.dumps`` whole, without a
  converter for its nested ``datetime`` / ``Decimal128`` leaves (``TypeError`` in every format), and
  Parquet typed the field from its first leaf (``profile`` became a timestamp). A document field is
  now one JSON-text column and every nested leaf follows the scalar rules.
* B4 — Oracle delivers ``TIMESTAMP(7-9)`` (and ``TIMESTAMP WITH TIME ZONE``) as exact text, which a
  microsecond Parquet timestamp refused (``ArrowTypeError``): such a column is Parquet text now.
* A row locator (the ``ROWID`` pseudo key of a key-less Oracle table) is never written to a file.
"""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from dps.jobs_models import RunSpec
from dps.layers.connectors.base import (
    ColumnInfo,
    FilesSpec,
    LedgerMark,
    ObjectRef,
    TableShape,
)
from dps.layers.connectors.docpaths import DOCUMENT_TYPE
from dps.layers.connectors.files import FilesWriter, arrow_schema, file_columns
from dps.layers.connectors.targets import resolve_target
from dps.storage import LocalObjectStore
from tests.connectors.factories import make_connection

bson = pytest.importorskip("bson")  # the [connectors] extra (pymongo ships bson)

MARK = LedgerMark(job_id="j_doc", partition_no=0, batch_no=1, last_key=[], rows=0)


def _profiles(n: int) -> list[dict[str, Any]]:
    from tests.connectors.mongo_fixtures import profile

    return [profile(i) for i in range(n)]


def _mongo_shape(docs: list[dict[str, Any]]) -> TableShape:
    from dps.layers.connectors.mongo_docs import infer_shape

    return infer_shape(ObjectRef(name="profiles"), docs, row_estimate=len(docs))


async def _write(tmp_path: Path, fmt: str, columns: list[ColumnInfo], rows: list[Any]) -> bytes:
    store = LocalObjectStore(tmp_path / "out")
    w = FilesWriter(
        store,
        FilesSpec(format=fmt, compression="none" if fmt != "parquet" else "snappy"),  # type: ignore[arg-type]
        job_id="j_doc",
        base_prefix="",
        columns=columns,
        spool_root=tmp_path / "spool",
    )
    assert await w.write_batch(rows, mark=MARK) == len(rows)
    [part] = (await w.finish_partition("j_doc", 0))["files"]
    assert part["rows"] == len(rows)
    return await store.get(part["key"])


def test_a_document_field_is_one_json_text_column() -> None:
    docs = _profiles(6)
    shape = _mongo_shape(docs)
    names = list(dict.fromkeys(c.name for c in shape.columns))
    cols = {c.name: c for c in file_columns(shape, names)}
    for field in ("profile", "contact", "kyc", "accounts", "tags"):
        assert (cols[field].data_type, cols[field].kind, cols[field].path) == (
            DOCUMENT_TYPE,
            "json",
            f"$.{field}",
        ), field
    assert cols["_id"].kind == "object_id" and cols["riskScore"].kind == "int"
    assert cols["customerId"].kind == "text" and cols["notes"].kind == "text"
    schema = arrow_schema(list(cols.values()))
    assert schema.field("profile").type == pa.string()  # not its first leaf's timestamp
    assert schema.field("accounts").type == pa.string()


@pytest.mark.parametrize("fmt", ["jsonl", "csv", "parquet"])
async def test_mongo_documents_write_in_every_format(tmp_path: Path, fmt: str) -> None:
    docs = _profiles(8)
    shape = _mongo_shape(docs)
    names = list(dict.fromkeys(c.name for c in shape.columns))
    data = await _write(tmp_path, fmt, file_columns(shape, names), docs)
    if fmt == "jsonl":
        rows = [json.loads(line) for line in data.decode().splitlines()]
        profile, accounts, kyc = rows[3]["profile"], rows[3]["accounts"], rows[3]["kyc"]
    else:
        if fmt == "csv":
            rows = list(csv.DictReader(io.StringIO(data.decode())))
        else:
            table = pq.read_table(io.BytesIO(data))
            assert table.schema.field("riskScore").type == pa.int64()
            rows = table.to_pylist()
        profile, accounts, kyc = (json.loads(rows[3][f]) for f in ("profile", "accounts", "kyc"))
    assert len(rows) == 8
    source = docs[3]
    assert profile["dob"] == source["profile"]["dob"].isoformat()  # a nested datetime: ISO-8601
    assert accounts[0]["balance"] == "3.50"  # a nested Decimal128: its digits
    assert accounts[0]["iban"] == source["accounts"][0]["iban"]
    assert kyc["verifiedAt"] == datetime(2024, 5, 1).isoformat()
    assert str(rows[3]["_id"]) == str(source["_id"])  # an ObjectId: its 24-hex text
    assert int(rows[3]["riskScore"]) == int(source["riskScore"])  # an Int64: an int


def test_nested_bson_leaves_follow_the_scalar_rules() -> None:
    from dps.layers.connectors.files import _plain, _text_value

    oid = bson.ObjectId()
    doc = {
        "a": [{"d": bson.Decimal128("1E+3"), "n": bson.Int64(7)}],
        "o": oid,
        "b": b"\x01\xff",
        "f": float("nan"),
        "t": (1, 2),
    }
    plain = _plain(doc)
    assert plain == {
        "a": [{"d": "1000", "n": 7}],
        "o": str(oid),
        "b": "01ff",
        "f": "nan",
        "t": [1, 2],
    }
    assert type(plain["a"][0]["n"]) is int
    assert json.loads(_text_value(doc)) == plain


def _oracle_ts_columns() -> list[ColumnInfo]:
    return [
        ColumnInfo(path="$.ID", name="ID", data_type="NUMBER(12)", kind="int", is_key=True),
        ColumnInfo(
            path="$.LAST_CONTACT_AT",
            name="LAST_CONTACT_AT",
            data_type="TIMESTAMP(9)",
            kind="timestamp",
            ts_precision=9,
        ),
        ColumnInfo(
            path="$.SEEN_AT",
            name="SEEN_AT",
            data_type="TIMESTAMP(6) WITH TIME ZONE",
            kind="timestamp",
            ts_precision=6,
        ),
        ColumnInfo(
            path="$.CREATED_AT",
            name="CREATED_AT",
            data_type="TIMESTAMP(6)",
            kind="timestamp",
            ts_precision=6,
        ),
    ]


async def test_oracle_text_timestamps_stay_exact_text_in_parquet(tmp_path: Path) -> None:
    rows = [
        {
            "ID": i,
            "LAST_CONTACT_AT": f"2025-02-01T10:00:00.{i:06d}123",
            "SEEN_AT": f"2025-02-01T10:00:00.{i:09d}+05:30",
            "CREATED_AT": datetime(2025, 1, 1, 9, 0, 0, i),
        }
        for i in range(5)
    ]
    data = await _write(tmp_path, "parquet", _oracle_ts_columns(), rows)
    table = pq.read_table(io.BytesIO(data))
    assert table.schema.field("LAST_CONTACT_AT").type == pa.string()
    assert table.schema.field("SEEN_AT").type == pa.string()
    assert table.schema.field("CREATED_AT").type == pa.timestamp("us")
    assert table.column("LAST_CONTACT_AT").to_pylist() == [r["LAST_CONTACT_AT"] for r in rows]
    assert table.column("SEEN_AT").to_pylist() == [r["SEEN_AT"] for r in rows]
    # PostgreSQL's zoned timestamps are datetimes: still a Parquet timestamp in UTC
    pg = ColumnInfo(path="$.at", name="at", data_type="timestamp with time zone", kind="timestamp")
    assert arrow_schema([pg]).field("at").type == pa.timestamp("us", tz="UTC")


def _keyless_shape() -> TableShape:
    return TableShape(
        object=ObjectRef(owner="CRM", name="ADDRESS_HISTORY"),
        key=["ROWID"],
        key_source="rowid",
        columns=[
            ColumnInfo(
                path="$.CUSTOMER_ID", name="CUSTOMER_ID", data_type="NUMBER(12)", kind="int"
            ),
            ColumnInfo(path="$.CITY", name="CITY", data_type="VARCHAR2(80)", kind="text"),
            ColumnInfo(
                path="$.ROWID",
                name="ROWID",
                data_type="ROWID",
                kind="int",
                is_key=True,
                nullable=False,
                pseudo=True,
            ),
        ],
    )


def test_a_row_locator_is_never_a_file_column() -> None:
    shape = _keyless_shape()
    names = ["CUSTOMER_ID", "CITY", "ROWID"]
    assert [c.name for c in file_columns(shape, names)] == ["CUSTOMER_ID", "CITY"]
    spec = RunSpec.model_validate(
        {
            "mode": "synthesize",
            "snapshot_id": "snap_1",
            "source": {
                "connection_id": "c_ora",
                "object": {"owner": "CRM", "name": "ADDRESS_HISTORY"},
            },
            "target": {
                "write_mode": "files",
                "connection_id": "c_s3",
                "files": {"format": "parquet"},
            },
            "purpose": {"ticket": "CHG-1", "legal_basis": "legitimate interest"},
        }
    )
    resolved, _ = resolve_target(
        spec,
        job_id="j_1",
        source_conn=make_connection("c_ora", engine="oracle", roles=["source"], params={}),
        target_conn=make_connection(
            "c_s3", engine="s3", roles=["target"], params={"bucket": "dps-exports"}
        ),
        source_shape=shape,
        target_shape=None,
        plans=[],
        projection=names,
    )
    assert resolved.write_columns == ["CUSTOMER_ID", "CITY"]
    assert set(resolved.column_types) == {"CUSTOMER_ID", "CITY"}


def test_a_files_manifest_types_a_document_field_as_a_document() -> None:
    docs = _profiles(4)
    shape = _mongo_shape(docs)
    names = list(dict.fromkeys(c.name for c in shape.columns))
    spec = RunSpec.model_validate(
        {
            "mode": "obfuscate",
            "snapshot_id": "snap_1",
            "source": {"connection_id": "c_mdb", "object": {"name": "profiles"}},
            "target": {"write_mode": "files", "connection_id": "c_s3", "files": {}},
            "purpose": {"ticket": "CHG-1", "legal_basis": "legitimate interest"},
        }
    )
    resolved, _ = resolve_target(
        spec,
        job_id="j_1",
        source_conn=make_connection("c_mdb", engine="mongo", roles=["source"], params={}),
        target_conn=make_connection(
            "c_s3", engine="s3", roles=["target"], params={"bucket": "dps-exports"}
        ),
        source_shape=shape,
        target_shape=None,
        plans=[],
        projection=names,
    )
    assert resolved.column_types["profile"] == DOCUMENT_TYPE
    assert resolved.column_types["accounts"] == DOCUMENT_TYPE
    assert resolved.write_columns == names
