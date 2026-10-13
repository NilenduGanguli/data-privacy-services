"""MongoConnector on the lane's MongoDB replica set: describe, list, count, samples, keyset reads.

docker compose --profile sources up -d mongo
DPS_TEST_REQUIRE_SERVICES=1 pytest -q -m mongo tests/connectors/test_mongo_connector.py
"""

from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest

pymongo = pytest.importorskip("pymongo")
from bson import ObjectId  # noqa: E402
from bson.binary import UuidRepresentation  # noqa: E402

from dps.jobs_models import ConnectionParams, SampleSpec, Selection  # noqa: E402
from dps.layers.connectors.base import (  # noqa: E402
    ConnectorError,
    KeyRange,
    ObjectRef,
    Row,
    TableShape,
    encode_key,
    key_tuple,
)
from dps.layers.connectors.mongo import MongoConnector, connector_error, mongo_code  # noqa: E402
from dps.layers.connectors.partitions import plan_partitions  # noqa: E402
from dps.layers.connectors.selection import SelectionError  # noqa: E402
from tests.connectors.mongo_fixtures import connector, profile  # noqa: E402
from tests.fixtures.m3_services import mongo_uri  # noqa: E402

pytestmark = [pytest.mark.integration, pytest.mark.mongo]
PROFILES = ObjectRef(name="profiles")


@pytest.fixture
async def c(mongo_client: Any, mongo_db: str) -> AsyncIterator[MongoConnector]:
    conn = connector(mongo_uri(), mongo_db)
    await conn.open()
    yield conn
    await conn.close()


def _insert(client: Any, db: str, docs: list[dict[str, Any]], name: str = "profiles") -> None:
    client[db][name].insert_many(docs)


async def _read(
    c: MongoConnector,
    shape: TableShape,
    selection: Selection | None = None,
    key_range: KeyRange | None = None,
    *,
    after: tuple[Any, ...] | None = None,
    batch: int = 50,
) -> list[list[Row]]:
    sel = selection or Selection()
    return [
        page
        async for page in c.read_partition(
            shape, sel, {}, key_range or KeyRange(), after_key=after, batch=batch
        )
    ]


def _ids(pages: list[list[Row]]) -> list[Any]:
    return [d["_id"] for page in pages for d in page]


def _profiled(client: Any, db: str, comment: str) -> list[dict[str, Any]]:
    return list(client[db]["system.profile"].find({"command.comment": comment}))


async def test_describe_profiles_shape(c: MongoConnector, mongo_client: Any, mongo_db: str) -> None:
    _insert(mongo_client, mongo_db, [profile(i) for i in range(60)])
    shape = await c.describe(PROFILES)
    by = {col.path: col for col in shape.columns}
    assert shape.object == PROFILES and shape.row_estimate == 60
    assert shape.key == ["_id"] and shape.key_source == "primary_key"
    assert shape.columns[0].path == "$._id" and by["$._id"].kind == "object_id"
    line1 = by["$.contact.addresses[*].line1"]
    assert line1.kind == "text" and line1.name == "contact"
    assert by["$.accounts[*].balance"].data_type == "decimal"
    assert by["$.riskScore"].data_type == "long" and by["$.profile.dob"].kind == "timestamp"
    assert by["$.tags[*]"].nullable  # half the documents have an empty array


async def test_describe_missing_collection(c: MongoConnector) -> None:
    with pytest.raises(ConnectorError) as err:
        await c.describe(ObjectRef(name="nothing_here"))
    assert err.value.code == "object_not_found"
    for bad in (ObjectRef(owner="crm", name="profiles"), ObjectRef(name="a$b")):
        with pytest.raises(ConnectorError) as err:
            await c.describe(bad)
        assert err.value.code == "object_invalid"


async def test_list_objects_excludes_internal(
    c: MongoConnector, mongo_client: Any, mongo_db: str
) -> None:
    db = mongo_client[mongo_db]
    db.command("profile", 2)
    _insert(mongo_client, mongo_db, [profile(1)])
    db["dps_write_ledger"].insert_one({"_id": "j:0", "batch_no": 0})
    db["system.profile"].estimated_document_count()
    db.command("create", "v_profiles", viewOn="profiles", pipeline=[])
    db.command("profile", 0)
    objects = await c.list_objects()
    assert [o.name for o in objects] == ["profiles"]
    assert all(o.owner is None for o in objects)
    assert len(await c.list_objects(limit=0)) == 1  # clamped to 1..5000 like the SQL engines


async def test_count_with_filter_and_max_rows(
    c: MongoConnector, mongo_client: Any, mongo_db: str
) -> None:
    _insert(mongo_client, mongo_db, [profile(i) for i in range(30)])
    shape = await c.describe(PROFILES)
    premium = {"segment": "premium"}
    assert await c.count(shape, Selection(mongo_filter=premium), {}) == 10
    assert await c.count(shape, Selection(mongo_filter=premium, max_rows=3), {}) == 3
    assert await c.count(shape, Selection(), {}) == 30
    wrapped = {"profile.dob": {"$gte": {"$date": "1980-01-21T00:00:00Z"}}}
    assert await c.count(shape, Selection(mongo_filter=wrapped), {}) == 10


async def test_denied_filter_never_reaches_the_server(
    c: MongoConnector, mongo_client: Any, mongo_db: str
) -> None:
    _insert(mongo_client, mongo_db, [profile(i) for i in range(5)])
    shape = await c.describe(PROFILES)
    mongo_client[mongo_db].command("profile", 2)
    try:
        for bad in ({"$where": "1"}, {"a": {"$function": {}}}, {"$expr": {"$eq": [1, 1]}}):
            with pytest.raises(SelectionError) as err:
                await c.count(shape, Selection(mongo_filter=bad), {})
            assert err.value.code == "selection_operator_denied"
            with pytest.raises(SelectionError):
                await _read(c, shape, Selection(mongo_filter=bad))
        with pytest.raises(SelectionError) as err:
            await c.count(shape, Selection(where="segment = 'x'"), {})
        assert err.value.code == "selection_engine_mismatch"
    finally:
        mongo_client[mongo_db].command("profile", 0)
    assert _profiled(mongo_client, mongo_db, c.comment) == []


async def test_read_partition_pages_all_docs_once(
    c: MongoConnector, mongo_client: Any, mongo_db: str
) -> None:
    docs = [profile(i) for i in range(100)]
    _insert(mongo_client, mongo_db, docs)
    shape = await c.describe(PROFILES)
    pages = await _read(c, shape, batch=13)
    assert len(pages) == 8 and all(0 < len(p) <= 13 for p in pages)
    assert _ids(pages) == sorted(d["_id"] for d in docs)
    by_id = {d["_id"]: d for d in docs}
    assert all(d == by_id[d["_id"]] for page in pages for d in page)  # nested arrays intact
    rest = await _read(c, shape, after=(_ids(pages)[40],), batch=13)
    assert _ids(rest) == _ids(pages)[41:]
    # a resume point decoded from the ledger arrives as hex text (M2's key codec)
    hexed = await _read(c, shape, after=(str(_ids(pages)[40]),), batch=13)
    assert _ids(hexed) == _ids(pages)[41:]


async def test_int_and_string_ids_page_in_order(
    c: MongoConnector, mongo_client: Any, mongo_db: str
) -> None:
    _insert(mongo_client, mongo_db, [profile(i, oid=False) for i in range(25)][::-1])
    keyed = [{**profile(i), "_id": f"K{i:03d}"} for i in range(25)]
    _insert(mongo_client, mongo_db, keyed[::-1], name="keyed")
    shape = await c.describe(PROFILES)
    assert shape.column("_id").kind == "int"
    assert _ids(await _read(c, shape, batch=4)) == list(range(25))
    shape = await c.describe(ObjectRef(name="keyed"))
    assert shape.column("_id").kind == "text"
    assert _ids(await _read(c, shape, batch=4)) == [f"K{i:03d}" for i in range(25)]


async def test_uuid_ids_page_in_order(c: MongoConnector, mongo_client: Any, mongo_db: str) -> None:
    docs = [{"_id": uuid.uuid4(), "n": i} for i in range(20)]
    mongo_client[mongo_db].get_collection(
        "u",
        codec_options=mongo_client[mongo_db].codec_options.with_options(
            uuid_representation=UuidRepresentation.STANDARD
        ),
    ).insert_many(docs)
    shape = await c.describe(ObjectRef(name="u"))
    assert shape.key == ["_id"] and shape.column("_id").kind == "uuid"
    pages = await _read(c, shape, batch=6)
    assert sorted(_ids(pages), key=lambda u: u.bytes) == _ids(pages) and len(_ids(pages)) == 20
    middle = _ids(pages)[9]
    encoded = encode_key((middle,), shape.key_columns())
    lower = KeyRange(lower=encoded)
    assert _ids(await _read(c, shape, key_range=lower, batch=6)) == _ids(pages)[9:]


async def test_partition_coverage_from_sample_keys(
    c: MongoConnector, mongo_client: Any, mongo_db: str
) -> None:
    docs = [profile(i) for i in range(300)]
    _insert(mongo_client, mongo_db, docs)
    shape = await c.describe(PROFILES)
    sample = await c.sample_keys(shape, Selection(), {}, 60)
    assert 0 < len(sample) <= 60 and sample == sorted(sample)  # the database's order
    ranges = plan_partitions(sample, count=6, columns=shape.key_columns())
    assert len(ranges) > 1 and isinstance(ranges[1].lower[0]["v"], str)  # type: ignore[index]
    seen: list[Any] = []
    for key_range in ranges:
        seen += _ids(await _read(c, shape, key_range=key_range, batch=40))
    assert len(seen) == len(set(seen)) == 300 and set(seen) == {d["_id"] for d in docs}


async def test_sample_keys_stay_below_the_upper_bound(
    c: MongoConnector, mongo_client: Any, mongo_db: str
) -> None:
    _insert(mongo_client, mongo_db, [profile(i, oid=False) for i in range(1000)])
    shape = await c.describe(PROFILES)
    keys = await c.sample_keys(shape, Selection(), {}, 50, upper=(400,))
    assert keys and all(k[0] < 400 for k in keys) and keys == sorted(keys)
    assert max(k[0] for k in keys) > 200  # spread over the bounded set, not its lowest keys
    assert await c.key_at_offset(shape, Selection(), {}, 0) == (0,)
    assert await c.key_at_offset(shape, Selection(), {}, 400) == (400,)
    assert await c.key_at_offset(shape, Selection(), {}, 1000) is None
    retail = Selection(mongo_filter={"segment": "retail"})
    assert await c.key_at_offset(shape, retail, {}, 2) == (4,)


async def test_mixed_id_types_single_partition(
    c: MongoConnector, mongo_client: Any, mongo_db: str
) -> None:
    docs = [{**profile(i), "_id": v} for i, v in enumerate([1, "a", ObjectId()])]
    docs += [{**profile(i), "_id": i} for i in range(10, 40)]
    _insert(mongo_client, mongo_db, docs)
    shape = await c.describe(PROFILES)
    assert shape.key_source == "none" and shape.key == []
    assert await c.sample_keys(shape, Selection(), {}, 10) == []
    pages = await _read(c, shape, batch=7)  # one partition, paged without a keyset
    assert all(len(p) <= 7 for p in pages)
    assert sorted(map(str, _ids(pages))) == sorted(str(d["_id"]) for d in docs)
    assert len(_ids(pages)) == 33


async def test_projection_reads_the_top_level_fields_of_selected_paths(
    c: MongoConnector, mongo_client: Any, mongo_db: str
) -> None:
    """A dotted column reads its whole top-level field (``read_projection`` plans it so): a
    dotted MongoDB projection drops array elements and empties sparse sub-documents (G3 r2 B1)."""
    _insert(mongo_client, mongo_db, [profile(i) for i in range(10)])
    shape = await c.describe(PROFILES)
    for columns in (["profile.fullName", "segment"], ["profile.fullName", "profile", "segment"]):
        pages = await _read(c, shape, Selection(columns=columns))
        docs = [d for page in pages for d in page]
        assert len(docs) == 10
        assert all(set(d) == {"_id", "profile", "segment"} for d in docs)
        assert all(set(d["profile"]) == {"fullName", "dob"} for d in docs)
    sampled = await c.sample(shape, Selection(columns=["segment"]), {}, 5)
    assert len(sampled) == 5 and all(set(d) == {"_id", "segment"} for d in sampled)


async def test_sample_spec_is_deterministic(
    c: MongoConnector, mongo_client: Any, mongo_db: str
) -> None:
    _insert(mongo_client, mongo_db, [profile(i) for i in range(200)])
    shape = await c.describe(PROFILES)
    sel = Selection(sample=SampleSpec(percent=30, seed=11))
    first = _ids(await _read(c, shape, sel, batch=17))
    assert first == _ids(await _read(c, shape, sel, batch=50))  # the same documents, any batch
    assert 30 <= len(first) <= 90
    assert await c.count(shape, sel, {}) == len(first)
    other = _ids(await _read(c, shape, Selection(sample=SampleSpec(percent=30, seed=12))))
    assert other != first
    kept = await c.key_at_offset(shape, sel, {}, 5)
    assert kept == (first[5],)


async def test_reads_use_majority_and_carry_a_comment(
    c: MongoConnector, mongo_client: Any, mongo_db: str
) -> None:
    _insert(mongo_client, mongo_db, [profile(i) for i in range(5)])
    shape = await c.describe(PROFILES)
    assert c.read_collection("profiles").read_concern.level == "majority"
    mongo_client[mongo_db].command("profile", 2)
    try:
        await _read(c, shape)
        await c.count(shape, Selection(), {})
        await c.sample(shape, Selection(), {}, 2)
    finally:
        mongo_client[mongo_db].command("profile", 0)
    ops = {p["op"] for p in _profiled(mongo_client, mongo_db, "dps:test:j_test")}
    assert c.comment == "dps:test:j_test" and {"query", "command"} <= ops
    finds = [
        p
        for p in _profiled(mongo_client, mongo_db, "dps:test:j_test")
        if p["command"].get("find") == "profiles"
    ]
    assert finds and all(p["command"]["readConcern"]["level"] == "majority" for p in finds)
    assert all(p["command"].get("maxTimeMS") == c.max_time_ms for p in finds)


async def test_bad_uri_is_connection_failed_and_value_free(mongo_client: Any) -> None:
    bad = connector(
        "mongodb://u:SENTINEL-PW@127.0.0.1:1/x", "x", mongo_server_selection_timeout_ms=500
    )
    started = time.monotonic()
    with pytest.raises(ConnectorError) as err:
        await bad.open()
    # the server selection timeout bounds an unreachable server, not the statement timeout
    assert time.monotonic() - started < 10
    assert err.value.code == "connection_failed" and err.value.retryable
    assert err.value.__suppress_context__ and err.value.__cause__ is None
    for text in (str(err.value), repr(err.value), repr(bad), str(err.value.args)):
        assert "SENTINEL" not in text
    result = await bad.test(role="source")
    assert not result.ok and result.error_code == "connection_failed"
    assert "SENTINEL" not in result.model_dump_json()
    await bad.close()


def test_from_secret_rules() -> None:
    from dps.config import Settings

    settings = Settings(_env_file=None)
    built = MongoConnector.from_secret(
        {"username": "app", "password": "SENTINEL-PW"},
        ConnectionParams(
            host="mongo.internal",
            port=27017,
            database="crm",
            options={"authSource": "admin", "replicaSet": "rs0", "tls": "true"},
        ),
        settings,
        action="discover",
        client_id="j_01",
    )
    assert built.comment == "dps:discover:j_01" and "SENTINEL" not in repr(built)
    assert repr(built) == "MongoConnector(database='crm')"
    with pytest.raises(ConnectorError) as err:
        MongoConnector.from_secret(
            {}, ConnectionParams(host="h"), settings, action="a", client_id=None
        )
    assert err.value.code == "database_required"
    for params in (
        ConnectionParams(host="bad host", database="crm"),
        ConnectionParams(host="h", database="crm", options={"authSource": "a b"}),
        ConnectionParams(database="crm"),
    ):
        with pytest.raises(ConnectorError) as err:
            MongoConnector.from_secret({}, params, settings, action="a", client_id=None)
        assert err.value.code == "connection_invalid"
    with pytest.raises(ConnectorError) as err:
        MongoConnector.from_secret(
            {"uri": "mongodb://u:SENTINEL-PW@h:notaport/x"},
            ConnectionParams(),
            settings,
            action="a",
            client_id=None,
        )
    assert err.value.code == "connection_invalid" and "SENTINEL" not in str(err.value)
    from_uri = MongoConnector.from_secret(
        {"uri": "mongodb://h:1/crm_uat?directConnection=true"},
        ConnectionParams(),
        settings,
        action="a",
        client_id=None,
    )
    assert repr(from_uri) == "MongoConnector(database='crm_uat')"


def test_driver_errors_keep_codes_only() -> None:
    from pymongo.errors import (
        BulkWriteError,
        DuplicateKeyError,
        OperationFailure,
        ServerSelectionTimeoutError,
    )

    dup = DuplicateKeyError('E11000 dup key: { customerId: "SENTINEL" }', 11000)
    assert mongo_code(dup) == "mongo:11000"
    bulk = BulkWriteError({"writeErrors": [{"code": 11000, "errmsg": "SENTINEL"}]})
    assert mongo_code(bulk) == "mongo:11000"
    assert mongo_code(BulkWriteError({"writeErrors": []})) == "mongo:bulk"
    err = connector_error("target_error", OperationFailure("SENTINEL", 2))
    assert (err.code, err.detail, err.retryable) == ("target_error", "mongo:2", False)
    down = connector_error("source_error", ServerSelectionTimeoutError("SENTINEL"))
    assert (down.code, down.retryable) == ("connection_failed", True)
    refused = connector_error("source_error", OperationFailure("SENTINEL", 18))
    assert (refused.code, refused.retryable) == ("connection_failed", False)
    for e in (err, down, refused):
        assert "SENTINEL" not in str(e)


async def test_connection_test_reports_transactions(
    c: MongoConnector, mongo_client: Any, mongo_db: str
) -> None:
    _insert(mongo_client, mongo_db, [profile(1)])
    result = await c.test(role="target", obj=PROFILES, write_mode="in_place")
    assert result.ok and result.capabilities["transactions"] is True
    names = {ch["name"] for ch in result.checks}
    assert {"connect", "update_target", "transactions", "create_ledger"} <= names
    assert all("severity" in ch for ch in result.checks)
    source = await c.test(role="source", obj=PROFILES)
    assert source.ok and {"connect", "select_source"} <= {ch["name"] for ch in source.checks}
    missing = await c.test(role="target", obj=ObjectRef(name="nope"), write_mode="in_place")
    assert not missing.ok
    created = await c.test(role="target", obj=ObjectRef(name="nope"), write_mode="table")
    assert created.ok and "create_target" in {ch["name"] for ch in created.checks}
    assert result.server_version and result.server_version.startswith("7.")


def test_key_tuple_of_documents() -> None:
    from dps.layers.connectors.mongo_docs import infer_shape

    docs = [profile(1)]
    shape = infer_shape(PROFILES, docs, row_estimate=1)
    assert key_tuple(docs[0], shape) == (docs[0]["_id"],)
