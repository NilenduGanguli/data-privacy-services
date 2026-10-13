"""MongoWriter on the lane's MongoDB replica set: table upserts, transactional in-place and append
with the write ledger, key strategies, value-free errors.

docker compose --profile sources up -d mongo
DPS_TEST_REQUIRE_SERVICES=1 pytest -q -m mongo tests/connectors/test_mongo_writer.py
"""

from __future__ import annotations

import asyncio
import decimal
from collections.abc import AsyncIterator
from typing import Any

import pytest

pymongo = pytest.importorskip("pymongo")
import bson  # noqa: E402
from bson import Decimal128, Int64, ObjectId  # noqa: E402
from bson.codec_options import CodecOptions  # noqa: E402
from bson.raw_bson import RawBSONDocument  # noqa: E402
from pymongo.errors import OperationFailure  # noqa: E402

from dps.jobs_models import Selection  # noqa: E402
from dps.layers.connectors.base import (  # noqa: E402
    ConnectorError,
    KeyRange,
    LedgerConflict,
    LedgerMark,
    ObjectRef,
    PkGenerate,
    PkOffset,
    PkToken,
    Row,
    TableShape,
    decode_key,
    encode_key,
)
from dps.layers.connectors.mongo import MongoConnector, MongoWriter  # noqa: E402
from dps.layers.connectors.postgres import PkCollision  # noqa: E402
from dps.layers.connectors.targets import PrepareFailure  # noqa: E402
from tests.connectors.mongo_fixtures import connector, profile  # noqa: E402
from tests.connectors.oracle_fixtures import make_target  # noqa: E402
from tests.fixtures.m3_services import mongo_uri  # noqa: E402

pytestmark = [pytest.mark.integration, pytest.mark.mongo]
PROFILES = ObjectRef(name="profiles")
UAT = ObjectRef(name="profiles_uat")


@pytest.fixture
async def c(mongo_client: Any, mongo_db: str) -> AsyncIterator[MongoConnector]:
    conn = connector(mongo_uri(), mongo_db)
    await conn.open()
    yield conn
    await conn.close()


def _coll(client: Any, db: str, name: str = "profiles") -> Any:
    return client[db][name]


async def _rows(c: MongoConnector, shape: TableShape, sel: Selection | None = None) -> list[Row]:
    out: list[Row] = []
    async for page in c.read_partition(
        shape, sel or Selection(), {}, KeyRange(), after_key=None, batch=100
    ):
        out += page
    return out


def _mark(shape: TableShape, last: Row, batch: int, rows: int, part: int = 0) -> LedgerMark:
    return LedgerMark(
        job_id="j_test",
        partition_no=part,
        batch_no=batch,
        last_key=encode_key((last["_id"],), shape.key_columns()),
        rows=rows,
    )


def _target(shape: TableShape, mode: str, **kw: Any) -> Any:
    return make_target(shape, write_mode=mode, engine="mongo", **kw)


async def _writer(c: MongoConnector, shape: TableShape, mode: str, **kw: Any) -> MongoWriter:
    writer = await c.writer(_target(shape, mode, **kw), shape)
    assert isinstance(writer, MongoWriter)
    return writer


def _mask(rows: list[Row]) -> list[Row]:
    """What the engine hands the writer: text names, written numeric leaves in their original
    leaf's type (``BatchTransformer`` with ``mongo_docs.restore_leaf``), Decimals where the codec
    produced them (the writer makes them ``Decimal128``)."""
    out = []
    for i, r in enumerate(rows):
        d = {**r, "profile": {**r["profile"], "fullName": f"T-{i:05d}"}}
        d["riskScore"] = Int64(int(r["riskScore"]) + 1)
        d["accounts"] = [{**a, "balance": decimal.Decimal("1.25")} for a in r["accounts"]]
        out.append(d)
    return out


async def test_table_mode_upserts_idempotently_and_keeps_bson_types(
    c: MongoConnector, mongo_client: Any, mongo_db: str
) -> None:
    _coll(mongo_client, mongo_db).insert_many([profile(i) for i in range(40)])
    shape = await c.describe(PROFILES)
    rows = await _rows(c, shape)
    masked = _mask(rows)
    writer = await _writer(c, shape, "table", object=UAT)
    report = await writer.prepare(dry_run=False)
    assert report.created and not report.ledger
    assert {"name": "indexes_not_copied", "ok": True, "severity": "info"} in report.checks
    for _ in range(2):  # a resumed table load replays its batches
        assert await writer.write_batch(masked[:25], mark=_mark(shape, masked[24], 1, 25)) == 25
        assert await writer.write_batch(masked[25:], mark=_mark(shape, masked[39], 2, 40)) == 15
    uat = _coll(mongo_client, mongo_db, "profiles_uat")
    docs = {d["_id"]: d for d in uat.find()}
    assert len(docs) == 40 and set(docs) == {r["_id"] for r in rows}
    for r in rows:
        d = docs[r["_id"]]
        assert isinstance(d["riskScore"], Int64) and d["riskScore"] == r["riskScore"] + 1
        assert d["accounts"][0]["balance"] == Decimal128("1.25")
        assert d["profile"]["dob"] == r["profile"]["dob"] and d["contact"] == r["contact"]
    names = [r["profile"]["fullName"] for r in rows]
    assert uat.count_documents({"profile.fullName": {"$in": names}}) == 0  # 0 survivors
    assert not mongo_client[mongo_db]["dps_write_ledger"].count_documents({})


async def test_truncate_insert_once_and_upsert_keeps_other_documents(
    c: MongoConnector, mongo_client: Any, mongo_db: str
) -> None:
    _coll(mongo_client, mongo_db).insert_many([profile(i) for i in range(40)])
    _coll(mongo_client, mongo_db, "profiles_uat").insert_many([{"junk": i} for i in range(5)])
    shape = await c.describe(PROFILES)
    rows = await _rows(c, shape)
    upsert = await _writer(c, shape, "table", object=UAT, table_load="upsert")
    report = await upsert.prepare(dry_run=False)
    assert not report.truncated and not report.created
    await upsert.write_batch(rows[:10], mark=_mark(shape, rows[9], 1, 10))
    assert _coll(mongo_client, mongo_db, "profiles_uat").count_documents({}) == 15
    writer = await _writer(c, shape, "table", object=UAT)
    assert (await writer.prepare(dry_run=True)).truncated is False
    assert _coll(mongo_client, mongo_db, "profiles_uat").count_documents({}) == 15
    report = await writer.prepare(dry_run=False)
    assert report.truncated and not report.created
    assert await writer.write_batch(rows, mark=_mark(shape, rows[-1], 1, 40)) == 40
    assert _coll(mongo_client, mongo_db, "profiles_uat").count_documents({}) == 40


async def test_in_place_sets_leaves_only(
    c: MongoConnector, mongo_client: Any, mongo_db: str
) -> None:
    _coll(mongo_client, mongo_db).insert_many([profile(i) for i in range(30)])
    before = {d["_id"]: d for d in _coll(mongo_client, mongo_db).find()}
    shape = await c.describe(PROFILES)
    rows = await _rows(c, shape, Selection(columns=["profile.fullName"]))
    assert all(set(r["profile"]) == {"fullName"} for r in rows)
    changed = [{**r, "profile": {"fullName": f"T-{i:05d}"}} for i, r in enumerate(rows)]
    writer = await _writer(c, shape, "in_place", write_columns=["profile"])
    report = await writer.prepare(dry_run=False)
    assert report.ledger and not report.created
    assert await writer.write_batch(changed[:20], mark=_mark(shape, changed[19], 1, 20)) == 20
    assert await writer.write_batch(changed[20:], mark=_mark(shape, changed[29], 2, 30)) == 10
    after = {d["_id"]: d for d in _coll(mongo_client, mongo_db).find()}
    for i, r in enumerate(rows):
        old, new = before[r["_id"]], after[r["_id"]]
        assert new["profile"]["fullName"] == f"T-{i:05d}"
        assert new["profile"]["dob"] == old["profile"]["dob"]
        for field in ("contact", "accounts", "kyc", "riskScore", "segment", "notes", "tags"):
            assert new[field] == old[field]
    names = [d["profile"]["fullName"] for d in before.values()]
    assert _coll(mongo_client, mongo_db).count_documents({"profile.fullName": {"$in": names}}) == 0
    # untouched fields are byte-identical BSON in the collection, not just equal after decoding
    raw = CodecOptions(document_class=RawBSONDocument)
    stored = {
        d["_id"]: d
        for d in mongo_client[mongo_db].get_collection("profiles", codec_options=raw).find()
    }
    for _id, old in before.items():
        for field in ("contact", "accounts", "kyc"):
            assert bson.encode({"v": stored[_id][field]}) == bson.encode({"v": old[field]})
        assert stored[_id]["riskScore"] == old["riskScore"]
    mark = await writer.read_ledger("j_test", 0)
    assert mark is not None and mark.batch_no == 2 and mark.rows == 30
    assert decode_key(mark.last_key, shape.key_columns())[0] == str(changed[29]["_id"])


async def test_in_place_ledger_and_batch_commit_together(
    c: MongoConnector, mongo_client: Any, mongo_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _coll(mongo_client, mongo_db).insert_many([profile(i) for i in range(10)])
    before = list(_coll(mongo_client, mongo_db).find().sort("_id"))
    shape = await c.describe(PROFILES)
    rows = await _rows(c, shape)
    changed = [{**r, "segment": "CHANGED"} for r in rows]
    writer = await _writer(c, shape, "in_place", write_columns=["segment"])
    await writer.prepare(dry_run=False)
    real = writer.target_collection

    class Failing:
        def __init__(self, inner: Any) -> None:
            self.inner = inner

        async def bulk_write(self, *args: Any, **kwargs: Any) -> Any:
            raise OperationFailure("SENTINEL rows refused", 2)

    monkeypatch.setattr(writer, "target_collection", lambda: Failing(real()))
    with pytest.raises(ConnectorError) as err:
        await writer.write_batch(changed, mark=_mark(shape, changed[-1], 1, 10))
    assert err.value.code == "target_error" and err.value.detail == "mongo:2"
    assert "SENTINEL" not in str(err.value)
    assert mongo_client[mongo_db]["dps_write_ledger"].count_documents({}) == 0
    assert list(_coll(mongo_client, mongo_db).find().sort("_id")) == before
    assert await writer.read_ledger("j_test", 0) is None


async def test_ledger_fences_replayed_and_skipped_batches(
    c: MongoConnector, mongo_client: Any, mongo_db: str
) -> None:
    _coll(mongo_client, mongo_db).insert_many([profile(i) for i in range(10)])
    shape = await c.describe(PROFILES)
    rows = await _rows(c, shape)
    writer = await _writer(c, shape, "in_place", write_columns=["segment"])
    await writer.prepare(dry_run=False)
    first = [{**r, "segment": "FIRST"} for r in rows]
    await writer.write_batch(first, mark=_mark(shape, rows[-1], 1, 10))
    for batch in (1, 3):  # the same batch again (a zombie), and a skipped one
        with pytest.raises(LedgerConflict):
            again = [{**r, "segment": "SECOND"} for r in rows]
            await writer.write_batch(again, mark=_mark(shape, rows[-1], batch, 10))
    assert {d["segment"] for d in _coll(mongo_client, mongo_db).find()} == {"FIRST"}
    # an empty ledgered batch still records its mark, so the numbering stays contiguous
    assert await writer.write_batch([], mark=_mark(shape, rows[-1], 2, 10)) == 0
    mark = await writer.read_ledger("j_test", 0)
    assert mark is not None and mark.batch_no == 2


async def test_a_concurrent_holder_is_fenced(
    c: MongoConnector, mongo_client: Any, mongo_db: str
) -> None:
    _coll(mongo_client, mongo_db).insert_many([profile(i) for i in range(50)])
    shape = await c.describe(PROFILES)
    rows = await _rows(c, shape)
    other = connector(mongo_uri(), mongo_db)
    await other.open()
    try:
        writers = [
            await _writer(conn, shape, "in_place", write_columns=["segment"]) for conn in (c, other)
        ]
        await writers[0].prepare(dry_run=False)
        outcomes = await asyncio.gather(
            *(
                w.write_batch(
                    [{**r, "segment": f"HOLDER{n}"} for r in rows],
                    mark=_mark(shape, rows[-1], 1, 50),
                )
                for n, w in enumerate(writers)
            ),
            return_exceptions=True,
        )
    finally:
        await other.close()
    conflicts = [o for o in outcomes if isinstance(o, LedgerConflict)]
    assert len(conflicts) == 1 and sorted(o for o in outcomes if isinstance(o, int)) == [50]
    assert len({d["segment"] for d in _coll(mongo_client, mongo_db).find()}) == 1  # one holder


async def test_append_generate_assigns_new_ids(
    c: MongoConnector, mongo_client: Any, mongo_db: str
) -> None:
    _coll(mongo_client, mongo_db).insert_many([profile(i) for i in range(10)])
    shape = await c.describe(PROFILES)
    rows = await _rows(c, shape)
    masked = _mask(rows)
    writer = await _writer(c, shape, "append", pk_strategy=PkGenerate())
    report = await writer.prepare(dry_run=False)
    assert report.ledger and report.append_upper is None and report.append_upper_key
    assert await writer.write_batch(masked, mark=_mark(shape, rows[-1], 1, 10)) == 10
    docs = list(_coll(mongo_client, mongo_db).find())
    new = [d for d in docs if d["_id"] not in {r["_id"] for r in rows}]
    assert len(docs) == 20 and len(new) == 10 and all(isinstance(d["_id"], ObjectId) for d in new)
    assert sorted(d["profile"]["fullName"] for d in new) == [f"T-{i:05d}" for i in range(10)]
    assert all(isinstance(d["riskScore"], Int64) for d in new)
    bound = decode_key(report.append_upper_key, shape.key_columns())[0]
    assert all(str(d["_id"]) >= bound for d in new)  # every appended id is above the read bound


async def test_append_offset_on_int_ids_and_collision_precheck(
    c: MongoConnector, mongo_client: Any, mongo_db: str
) -> None:
    _coll(mongo_client, mongo_db).insert_many([profile(i, oid=False) for i in range(1, 11)])
    shape = await c.describe(PROFILES)
    rows = await _rows(c, shape)
    writer = await _writer(c, shape, "append", pk_strategy=PkOffset(offset=1000))
    report = await writer.prepare(dry_run=False)
    assert report.append_upper == 11

    async def keys(shift: int) -> AsyncIterator[list[tuple[Any, ...]]]:
        yield [(r["_id"] + shift,) for r in rows[:5]]
        yield []
        yield [(r["_id"] + shift,) for r in rows[5:]]

    assert await writer.precheck_collisions(keys(1000)) == 0
    assert await writer.precheck_collisions(keys(5)) == 5
    shifted = [{**r, "_id": r["_id"] + 1000} for r in rows]
    assert await writer.write_batch(shifted, mark=_mark(shape, rows[-1], 1, 10)) == 10
    ids = sorted(d["_id"] for d in _coll(mongo_client, mongo_db).find())
    assert ids == [*range(1, 11), *range(1001, 1011)]
    collide = [{**r, "_id": r["_id"] + 5} for r in rows]
    with pytest.raises(PkCollision):
        await writer.write_batch(collide, mark=_mark(shape, rows[-1], 2, 20))
    assert _coll(mongo_client, mongo_db).count_documents({}) == 20  # the batch rolled back


async def test_pk_strategy_rules(c: MongoConnector, mongo_client: Any, mongo_db: str) -> None:
    _coll(mongo_client, mongo_db).insert_many([profile(i) for i in range(3)])
    _coll(mongo_client, mongo_db, "ints").insert_many([profile(i, oid=False) for i in range(3)])
    _coll(mongo_client, mongo_db, "texts").insert_many(
        [{**profile(i), "_id": f"K{i:03d}"} for i in range(3)]
    )
    oids = await c.describe(PROFILES)
    ints = await c.describe(ObjectRef(name="ints"))
    texts = await c.describe(ObjectRef(name="texts"))
    refused = [
        (oids, PkOffset(offset=10), PROFILES),
        (oids, PkOffset(prefix="X"), PROFILES),
        (oids, PkToken(), PROFILES),
        (ints, PkOffset(prefix="X"), ObjectRef(name="ints")),
        (ints, PkGenerate(), ObjectRef(name="ints")),  # ObjectIds would mix with ints
        (texts, PkOffset(offset=10), ObjectRef(name="texts")),
        (texts, PkOffset(prefix="A-"), ObjectRef(name="texts")),  # sorts below K…: re-read
        (texts, PkToken(), ObjectRef(name="texts")),  # tokens land anywhere in the range
        (oids, PkGenerate(), ObjectRef(name="ints")),  # the target holds int ids
    ]
    for shape, strategy, obj in refused:
        writer = await _writer(c, shape, "append", pk_strategy=strategy, object=obj)
        with pytest.raises(PrepareFailure) as err:
            await writer.prepare(dry_run=True)
        assert err.value.code == "pk_strategy_invalid", (shape.object, strategy)
    for shape, strategy in ((oids, PkGenerate()), (ints, PkOffset(offset=10))):
        writer = await _writer(c, shape, "append", pk_strategy=strategy)
        await writer.prepare(dry_run=True)
    writer = await _writer(c, texts, "append", pk_strategy=PkOffset(prefix="Z-"))
    report = await writer.prepare(dry_run=True)
    assert decode_key(report.append_upper_key, texts.key_columns()) == ("Z-",)
    other = await _writer(c, ints, "append", pk_strategy=PkToken(), object=UAT)
    _coll(mongo_client, mongo_db, "profiles_uat").insert_one({"_id": 1})
    await other.prepare(dry_run=True)  # another collection: tokens cannot be read back


async def test_same_collection_append_bounds(
    c: MongoConnector, mongo_client: Any, mongo_db: str
) -> None:
    _coll(mongo_client, mongo_db, "ints").insert_many([profile(i, oid=False) for i in range(1, 11)])
    ints = await c.describe(ObjectRef(name="ints"))
    report = await (await _writer(c, ints, "append", pk_strategy=PkOffset(offset=100))).prepare(
        dry_run=True
    )
    assert report.append_upper == 11 and report.append_upper_key is None
    _coll(mongo_client, mongo_db).insert_many([profile(i) for i in range(10)])
    oids = await c.describe(PROFILES)
    existing = [d["_id"] for d in _coll(mongo_client, mongo_db).find()]
    report = await (await _writer(c, oids, "append", pk_strategy=PkGenerate())).prepare(
        dry_run=True
    )
    later = ObjectId()
    bound = ObjectId(decode_key(report.append_upper_key, oids.key_columns())[0])
    assert all(e < bound for e in existing) and bound < later


async def test_transactions_unsupported_without_a_replica_set(
    c: MongoConnector, mongo_client: Any, mongo_db: str
) -> None:
    _coll(mongo_client, mongo_db).insert_many([profile(i) for i in range(3)])
    shape = await c.describe(PROFILES)
    c._replica = False  # what a standalone server reports in hello
    for mode, kw in (("in_place", {}), ("append", {"pk_strategy": PkGenerate()})):
        writer = await _writer(c, shape, mode, **kw)
        with pytest.raises(PrepareFailure) as err:
            await writer.prepare(dry_run=False)
        assert err.value.code == "transactions_unsupported"
    report = await (await _writer(c, shape, "table", object=UAT)).prepare(dry_run=False)
    assert report.created


async def test_duplicate_key_error_is_value_free(
    c: MongoConnector, mongo_client: Any, mongo_db: str
) -> None:
    _coll(mongo_client, mongo_db).insert_many([profile(i) for i in range(2)])
    _coll(mongo_client, mongo_db, "profiles_uat").create_index("customerId", unique=True)
    shape = await c.describe(PROFILES)
    rows = [{**r, "customerId": "SENTINEL-DUP-1"} for r in await _rows(c, shape)]
    writer = await _writer(c, shape, "table", object=UAT)
    await writer.prepare(dry_run=False)
    with pytest.raises(ConnectorError) as err:
        await writer.write_batch(rows, mark=_mark(shape, rows[-1], 1, 2))
    assert isinstance(err.value, PkCollision) and err.value.code == "pk_collision"
    for text in (str(err.value), repr(err.value), repr(err.value.args)):
        assert "SENTINEL" not in text
    assert err.value.__suppress_context__ and err.value.__cause__ is None


async def test_path_unsupported_for_dotted_field_names(
    c: MongoConnector, mongo_client: Any, mongo_db: str
) -> None:
    _coll(mongo_client, mongo_db).insert_many([{**profile(1), "odd": {"a.b": "x"}}])
    shape = await c.describe(PROFILES)
    rows = await _rows(c, shape)
    writer = await _writer(c, shape, "in_place", write_columns=["odd"])
    await writer.prepare(dry_run=False)
    with pytest.raises(PrepareFailure) as err:
        await writer.write_batch(rows, mark=_mark(shape, rows[-1], 1, 1))
    assert err.value.code == "path_unsupported"
    assert await writer.read_ledger("j_test", 0) is None  # nothing was recorded


async def test_targets_must_exist_and_dry_runs_change_nothing(
    c: MongoConnector, mongo_client: Any, mongo_db: str
) -> None:
    _coll(mongo_client, mongo_db).insert_many([profile(i) for i in range(3)])
    shape = await c.describe(PROFILES)
    for mode, kw in (
        ("in_place", {"object": UAT}),
        ("append", {"object": UAT, "pk_strategy": PkGenerate()}),
        ("table", {"object": UAT, "create_if_missing": False}),
    ):
        writer = await _writer(c, shape, mode, **kw)
        with pytest.raises(PrepareFailure) as err:
            await writer.prepare(dry_run=False)
        assert err.value.code == "target_missing"
    report = await (await _writer(c, shape, "table", object=UAT)).prepare(dry_run=True)
    assert not report.created
    await (await _writer(c, shape, "in_place")).prepare(dry_run=True)
    assert set(mongo_client[mongo_db].list_collection_names()) == {"profiles"}


async def test_read_ledger_and_clear(c: MongoConnector, mongo_client: Any, mongo_db: str) -> None:
    _coll(mongo_client, mongo_db).insert_many([profile(i) for i in range(6)])
    shape = await c.describe(PROFILES)
    rows = await _rows(c, shape)
    writer = await _writer(c, shape, "in_place", write_columns=["segment"])
    await writer.prepare(dry_run=False)
    marks = [_mark(shape, rows[2], 1, 3, part=0), _mark(shape, rows[5], 1, 3, part=1)]
    await writer.write_batch(rows[:3], mark=marks[0])
    await writer.write_batch(rows[3:], mark=marks[1])
    assert await writer.read_ledger("j_test", 0) == marks[0]
    assert await writer.read_ledger("j_test", 1) == marks[1]
    assert await writer.finish_partition("j_test", 0, batch_no=1) == {}
    assert await writer.clear_ledger("j_test") == 2
    assert await writer.read_ledger("j_test", 0) is None
    assert await writer.clear_ledger("j_test") == 0
    await writer.close()
