"""MongoDB write fidelity (M3 G3 review r1 B1-B3) on the lane's replica set.

* B1: values no plan covers reach the target as the same raw BSON (type and bytes), in ``table``
  and ``in_place`` mode, for full (unprojected) documents, mixed int32/int64 fields and values the
  discovery sample never saw; a planned leaf keeps its original leaf's BSON type.
* B2: an ``_id`` of another BSON type that the ``$sample`` missed makes the collection key-less (a
  range read would silently skip it); a foreign ``_id`` inside the key's bracket fails a read closed.
* B3: a projected ``table`` upsert leaves the fields outside the projection of matched documents.

docker compose --profile sources up -d mongo
DPS_TEST_REQUIRE_SERVICES=1 pytest -q -m mongo tests/connectors/test_mongo_write_fidelity.py
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest

pytest.importorskip("fastfpe")
pytest.importorskip("pymongo")
import bson  # noqa: E402
from bson import Binary, Decimal128, Int64, ObjectId  # noqa: E402
from bson.codec_options import CodecOptions  # noqa: E402
from bson.raw_bson import RawBSONDocument  # noqa: E402

from dps.config import Settings  # noqa: E402
from dps.engine.batch import BatchTransformer, ColumnPlan  # noqa: E402
from dps.engine.policy import MethodSpec, load_preset  # noqa: E402
from dps.engine.service import PrivacyEngine  # noqa: E402
from dps.engine.transform.crypto import generate_key_material  # noqa: E402
from dps.jobs_models import Selection  # noqa: E402
from dps.layers.connectors.base import (  # noqa: E402
    ConnectorError,
    KeyRange,
    LedgerMark,
    ObjectRef,
    Row,
    TableShape,
    encode_key,
)
from dps.layers.connectors.jobs_run import leaf_restorer  # noqa: E402
from dps.layers.connectors.mongo import MongoConnector  # noqa: E402
from dps.layers.connectors.mongo_docs import infer_shape  # noqa: E402
from dps.layers.connectors.partitions import plan_partitions  # noqa: E402
from tests.connectors.mongo_fixtures import connector  # noqa: E402
from tests.connectors.oracle_fixtures import make_target  # noqa: E402
from tests.fixtures.m3_services import mongo_uri  # noqa: E402

pytestmark = [pytest.mark.integration, pytest.mark.mongo]
POLICY = load_preset("gdpr-pseudonymise")
ENGINE = PrivacyEngine(
    Settings(_env_file=None, fpe_enabled=True, llm_provider="stub", ner_enabled=False)
)
SRC = ObjectRef(name="src")
RAW = CodecOptions(document_class=RawBSONDocument)


@pytest.fixture
async def c(mongo_client: Any, mongo_db: str) -> AsyncIterator[MongoConnector]:
    conn = connector(mongo_uri(), mongo_db)
    await conn.open()
    yield conn
    await conn.close()


async def _read(
    c: MongoConnector, shape: TableShape, sel: Selection | None = None, rng: KeyRange | None = None
) -> list[Row]:
    out: list[Row] = []
    async for page in c.read_partition(
        shape, sel or Selection(), {}, rng or KeyRange(), after_key=None, batch=50
    ):
        out += page
    return out


def _mark(shape: TableShape, rows: list[Row]) -> LedgerMark:
    return LedgerMark(
        job_id="j_test",
        partition_no=0,
        batch_no=1,
        last_key=encode_key((rows[-1]["_id"],), shape.key_columns()),
        rows=len(rows),
    )


def _plan(shape: TableShape, path: str, entity: str, method: str, **params: Any) -> ColumnPlan:
    col = next(x for x in shape.columns if x.path == path)
    return ColumnPlan(
        path=path,
        name=col.name,
        entity_type=entity,
        method=MethodSpec(method=method, params=dict(params)),
        text_column=False,
        reversible=True,
        kind=col.kind,
        length=None,
    )


def _transformer(shape: TableShape, plans: list[ColumnPlan]) -> BatchTransformer:
    return BatchTransformer(
        ENGINE,
        policy=POLICY,
        mode="obfuscate",
        columns=plans,
        shape=shape,
        key=generate_key_material("fidelity"),
        seed=None,
        locale="en_IN",
        unclassified="keep",
        leaf_restore=leaf_restorer("mongo"),
    )


def _docs(n: int) -> list[dict[str, Any]]:
    """Synthetic documents: int32 and int64 in one field, nested typed leaves, arrays."""
    return [
        {
            "_id": ObjectId(),
            "name": f"Probe Person {i:03d}",
            "score": i if i % 2 == 0 else Int64(i),  # sampled label: long
            "acct": 10_000_000 + i if i % 2 == 0 else Int64(10_000_000 + i),
            "meta": {
                "code": i,
                "when": dt.datetime(2024, 1, 1) + dt.timedelta(days=i),
                "ref": ObjectId(),
                "uid": Binary.from_uuid(uuid.UUID(int=i + 1)),
            },
            "lines": [{"amount": Decimal128(f"{i}.25"), "qty": i}, {"ratio": 0.5 + i}],
        }
        for i in range(n)
    ]


def _drift() -> list[dict[str, Any]]:
    """Written after discovery: text values in fields the sample saw as typed."""
    return [
        {
            "_id": ObjectId(),
            "name": "Probe Drift A",
            "score": "0042",
            "acct": 10_000_999,
            "meta": {
                "code": "007",
                "when": "2024-02-03",
                "ref": "0123456789abcdef01234567",
                "uid": "00000000-0000-0000-0000-00000000002a",
            },
            "lines": [{"amount": "3.50", "qty": "12"}],
        }
    ]


def _field_bytes(doc: RawBSONDocument, field: str) -> bytes:
    """The raw BSON element of one top-level field (type byte and value bytes)."""
    return bson.encode({"v": doc[field]}, codec_options=RAW)


@pytest.mark.parametrize("mode", ["table", "in_place"])
async def test_untouched_leaves_keep_their_raw_bson_on_full_documents(
    c: MongoConnector, mongo_client: Any, mongo_db: str, mode: str
) -> None:
    src = mongo_client[mongo_db]["src"]
    src.insert_many(_docs(40))
    shape = await c.describe(SRC)
    assert next(x for x in shape.columns if x.path == "$.score").data_type == "long"
    src.insert_many(_drift())  # out of the discovery sample
    before = {d["_id"]: d for d in mongo_client[mongo_db].get_collection("src", RAW).find()}
    rows = await _read(c, shape)  # full documents: no projection
    assert len(rows) == 41
    out = _transformer(shape, [_plan(shape, "$.name", "PERSON_NAME", "fpe_text")]).transform(rows)
    target = "tgt" if mode == "table" else "src"
    writer = await c.writer(
        make_target(shape, write_mode=mode, engine="mongo", object=ObjectRef(name=target)), shape
    )
    await writer.prepare(dry_run=False)
    assert await writer.write_batch(out.rows, mark=_mark(shape, out.rows)) == 41
    after = {d["_id"]: d for d in mongo_client[mongo_db].get_collection(target, RAW).find()}
    assert set(after) == set(before)
    changed = survivors = 0
    for _id, old in before.items():
        new = after[_id]
        assert set(new.keys()) == set(old.keys())
        for field in old.keys():
            if field == "name":
                survivors += new["name"] == old["name"]
                continue
            changed += _field_bytes(new, field) != _field_bytes(old, field)
    assert changed == 0  # every untouched leaf: same BSON type, same bytes
    assert survivors == 0  # the planned leaf was transformed in every document


async def test_planned_leaves_keep_their_original_bson_type(
    c: MongoConnector, mongo_client: Any, mongo_db: str
) -> None:
    src = mongo_client[mongo_db]["src"]
    src.insert_many(_docs(20))
    shape = await c.describe(SRC)
    src.insert_many(_drift())
    rows = await _read(c, shape)
    plans = [_plan(shape, "$.acct", "ACCOUNT_NUMBER", "fpe_digits")]
    out = _transformer(shape, plans).transform(rows)
    writer = await c.writer(
        make_target(shape, write_mode="table", engine="mongo", object=ObjectRef(name="tgt")), shape
    )
    await writer.prepare(dry_run=False)
    await writer.write_batch(out.rows, mark=_mark(shape, out.rows))
    stored = {d["_id"]: d for d in mongo_client[mongo_db]["tgt"].find()}
    same_type = equal = 0
    for row in rows:
        old, new = row["acct"], stored[row["_id"]]["acct"]
        same_type += type(new) is type(old)  # Int64 stays Int64, int32 stays int32
        equal += new == old
    assert same_type == len(rows) == 21 and equal == 0


async def test_a_foreign_id_type_the_sample_missed_makes_the_collection_keyless(
    mongo_client: Any, mongo_db: str
) -> None:
    coll = mongo_client[mongo_db]["c"]
    coll.insert_many([{"_id": ObjectId(), "v": i} for i in range(20_000)])
    coll.insert_many([{"_id": 10_000, "v": -1}, {"_id": "S1", "v": -2}])
    c = connector(mongo_uri(), mongo_db, discovery_sample_rows=10)
    await c.open()
    try:
        shapes = [await c.describe(ObjectRef(name="c")) for _ in range(3)]
        assert [s.key_source for s in shapes] == ["none"] * 3
        assert all(s.key == [] and not any(x.is_key for x in s.columns) for s in shapes)
        total = await c.count(shapes[0], Selection(), {})
        assert total == 20_002 and len(await _read(c, shapes[0])) == total  # nothing skipped
    finally:
        await c.close()


async def test_a_uniform_id_collection_stays_keyed_and_reads_every_partition(
    c: MongoConnector, mongo_client: Any, mongo_db: str
) -> None:
    mongo_client[mongo_db]["c"].insert_many([{"_id": ObjectId(), "v": i} for i in range(3_000)])
    shape = await c.describe(ObjectRef(name="c"))
    assert shape.key == ["_id"] and shape.key_source == "primary_key"
    keys = await c.sample_keys(shape, Selection(), {}, 60)
    read = 0
    for rng in plan_partitions(keys, count=4, columns=shape.key_columns()):
        read += len(await _read(c, shape, rng=rng))
    assert read == 3_000


async def test_a_foreign_id_inside_the_key_bracket_fails_the_read_closed(
    c: MongoConnector, mongo_client: Any, mongo_db: str
) -> None:
    coll = mongo_client[mongo_db]["c"]
    docs = [{"_id": i, "v": i} for i in range(1, 101)]
    coll.insert_many(docs)
    shape = infer_shape(ObjectRef(name="c"), docs, row_estimate=100)  # the reviewed snapshot
    assert shape.key == ["_id"]
    coll.insert_one({"_id": 50.5, "v": 0})  # a double between the ints: same BSON bracket
    with pytest.raises(ConnectorError) as err:
        await _read(c, shape)
    assert err.value.code == "key_type_mixed" and "50.5" not in str(err.value)


async def test_a_projected_upsert_keeps_the_other_fields_of_matched_documents(
    c: MongoConnector, mongo_client: Any, mongo_db: str
) -> None:
    docs = [
        {
            "_id": ObjectId(),
            "name": f"N{i}",
            "segment": "retail",
            "profile": {"fullName": f"Full {i}", "dob": dt.datetime(1980, 1, 1 + i)},
            "accounts": [{"iban": f"GB00TEST{i:04d}", "balance": Decimal128("1.50")}],
        }
        for i in range(12)
    ]
    mongo_client[mongo_db]["src"].insert_many(docs)
    # the existing, wider target holds 10 of the 12 documents plus a field of its own
    mongo_client[mongo_db]["tgt"].insert_many(
        [{**d, "tgtOnly": i} for i, d in enumerate(docs[:10])]
    )
    shape = await c.describe(SRC)
    rows = await _read(c, shape, Selection(columns=["name", "profile.fullName"]))
    assert all(set(r) == {"_id", "name", "profile"} for r in rows)
    masked = [
        {**r, "name": f"T{i}", "profile": {**r["profile"], "fullName": f"F{i}"}}
        for i, r in enumerate(rows)
    ]
    target = make_target(
        shape,
        write_mode="table",
        engine="mongo",
        object=ObjectRef(name="tgt"),
        table_load="upsert",
        write_columns=["_id", "name", "profile"],
    )
    writer = await c.writer(target, shape)
    report = await writer.prepare(dry_run=False)
    assert not report.truncated and not report.created
    assert await writer.write_batch(masked, mark=_mark(shape, masked)) == 12
    tgt = mongo_client[mongo_db]["tgt"]
    assert tgt.count_documents({}) == 12
    kept = 0
    for i, d in enumerate(docs[:10]):
        new = tgt.find_one({"_id": d["_id"]})
        assert new is not None
        kept += (
            new["segment"] == d["segment"]
            and new["tgtOnly"] == i
            and new["accounts"] == d["accounts"]
            and new["profile"]["dob"] == d["profile"]["dob"]
        )
    assert kept == 10  # fields outside the projection survive on every matched document
    names = [d["name"] for d in docs] + [d["profile"]["fullName"] for d in docs]
    assert tgt.count_documents({"name": {"$in": names}}) == 0
    assert tgt.count_documents({"profile.fullName": {"$in": names}}) == 0
    for d in docs[10:]:  # inserted documents hold the projected top-level fields only
        new = tgt.find_one({"_id": d["_id"]})
        assert new is not None and set(new) == {"_id", "name", "profile"}
        assert set(new["profile"]) == {"fullName", "dob"}


def _mixed(i: int) -> dict[str, Any]:
    """Arrays that mix sub-documents with scalars, sparse elements and nested arrays."""
    return {
        "_id": ObjectId(),
        "name": f"Mixed {i:03d}",
        "accounts": [
            {"iban": f"GB82WEST1234569876{i:04d}", "bal": i},
            f"legacy-note-{i}",
            {"bal": i + 1},
            {"iban": f"GB94BARC1020153009{i:04d}", "bal": i + 2},
        ],
        "matrix": [
            [{"iban": f"DE89370400440532{i:06d}"}, "x"],
            "y",
            [7, {"iban": f"FR76300060000112{i:06d}", "k": i}],
        ],
    }


def _ibans(doc: dict[str, Any]) -> set[str]:
    found: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "iban":
                    found.add(value)
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(doc)
    return found


def _skeleton(node: Any) -> Any:
    """The structure of a value: element counts, element kinds and keys, non-IBAN scalars."""
    if isinstance(node, dict):
        return {k: ("iban" if k == "iban" else _skeleton(v)) for k, v in node.items()}
    if isinstance(node, list):
        return [_skeleton(v) for v in node]
    return node


async def test_a_projected_in_place_write_keeps_array_positions(
    c: MongoConnector, mongo_client: Any, mongo_db: str
) -> None:
    """R2-B1: dotted ``columns`` through arrays that mix sub-documents with scalars (and arrays of
    arrays) read every element at its stored position, so each masked IBAN lands on its own
    element: 0 source IBANs survive and no element gains, loses or moves a key."""
    src = mongo_client[mongo_db]["src"]
    src.insert_many([_mixed(i) for i in range(20)])
    before = {d["_id"]: d for d in src.find()}
    shape = await c.describe(SRC)
    rows = await _read(c, shape, Selection(columns=["accounts.iban", "matrix.iban"]))
    assert len(rows) == 20
    plans = [
        _plan(shape, "$.accounts[*].iban", "IBAN", "det_token"),
        _plan(shape, "$.matrix[*][*].iban", "IBAN", "det_token"),
    ]
    out = _transformer(shape, plans).transform(rows)
    target = make_target(
        shape, write_mode="in_place", engine="mongo", write_columns=["_id", "accounts", "matrix"]
    )
    writer = await c.writer(target, shape)
    await writer.prepare(dry_run=False)
    assert await writer.write_batch(out.rows, mark=_mark(shape, out.rows)) == 20
    after = {d["_id"]: d for d in src.find()}
    source_ibans = set().union(*(_ibans(d) for d in before.values()))
    assert len(source_ibans) == 80
    survivors = sum(len(_ibans(d) & source_ibans) for d in after.values())
    moved = sum(_skeleton(after[k]) != _skeleton(old) for k, old in before.items())
    assert survivors == 0 and moved == 0
    assert all(len(_ibans(d)) == 4 for d in after.values())  # one token per source IBAN


async def test_a_projected_upsert_keeps_matched_fields_a_sparse_row_lacks(
    c: MongoConnector, mongo_client: Any, mongo_db: str
) -> None:
    """R2-B2: a source sub-document without the projected leaf (sparse) or empty must not wipe
    the matched target document's sub-document; an inserted document keeps the source's shape."""
    sparse, empty, new = ObjectId(), ObjectId(), ObjectId()
    mongo_client[mongo_db]["src"].insert_many(
        [
            {"_id": sparse, "name": "S", "profile": {"dob": "src-dob"}},
            {"_id": empty, "name": "E", "profile": {}},
            {"_id": new, "name": "N", "profile": {}},
        ]
    )
    held = {"fullName": "TF", "dob": "tgt-dob", "nested": {"pin": "560001"}}
    mongo_client[mongo_db]["tgt"].insert_many(
        [{"_id": sparse, "profile": dict(held)}, {"_id": empty, "profile": dict(held)}]
    )
    shape = await c.describe(SRC)
    rows = await _read(c, shape, Selection(columns=["name", "profile.fullName"]))
    target = make_target(
        shape,
        write_mode="table",
        engine="mongo",
        object=ObjectRef(name="tgt"),
        table_load="upsert",
        write_columns=["_id", "name", "profile"],
    )
    writer = await c.writer(target, shape)
    await writer.prepare(dry_run=False)
    assert await writer.write_batch(rows, mark=_mark(shape, rows)) == 3
    tgt = {d["_id"]: d for d in mongo_client[mongo_db]["tgt"].find()}
    assert tgt[sparse]["profile"]["fullName"] == "TF"
    assert tgt[sparse]["profile"]["nested"] == {"pin": "560001"}
    assert tgt[empty]["profile"] == held
    assert tgt[new]["profile"] == {} and tgt[new]["name"] == "N"
