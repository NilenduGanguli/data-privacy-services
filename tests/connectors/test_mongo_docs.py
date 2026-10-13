"""BSON labels, path-shape inference from a sample and the _id bounds, BSON type restoration of
written leaves, upsert ``$set`` documents (offline)."""

from __future__ import annotations

import datetime as dt
import decimal
import uuid

import pytest

bson = pytest.importorskip("bson")
from bson import Decimal128, Int64, ObjectId  # noqa: E402

from dps.layers.connectors import mongo_docs as md  # noqa: E402
from dps.layers.connectors.base import ObjectRef, encode_key  # noqa: E402

OBJ = ObjectRef(name="profiles")


def _doc(i: int, **extra: object) -> dict:
    return {
        "_id": ObjectId(),
        "customerId": f"CUST-{10000000 + i}",
        "riskScore": Int64(700 + i),
        "profile": {"fullName": f"Person {i}", "dob": dt.datetime(1980, 1, 1)},
        "accounts": [{"iban": f"GB00TEST{i:010d}", "balance": Decimal128("10.50")}],
        **extra,
    }


def test_bson_labels() -> None:
    values = (
        None,
        True,
        Int64(1),
        5,
        2**40,
        1.5,
        Decimal128("1"),
        "x",
        dt.datetime(2020, 1, 1),
        ObjectId(),
        b"\x00",
        uuid.uuid4(),
        {"a": 1},
    )
    assert [md.bson_type(v) for v in values] == [
        "null",
        "bool",
        "long",
        "int",
        "long",
        "double",
        "decimal",
        "string",
        "date",
        "objectId",
        "binData",
        "uuid",
        "other",
    ]


def test_top_level() -> None:
    assert md.top_level("$.contact.addresses[*].line1") == "contact"
    assert md.top_level("$._id") == "_id"
    assert md.top_level("$['a.b'].c") == "a.b"


def test_infer_shape_paths_kinds_and_key() -> None:
    docs = [_doc(i) for i in range(10)] + [_doc(10, nick=None)]
    shape = md.infer_shape(OBJ, docs, row_estimate=11)
    by = {c.path: c for c in shape.columns}
    assert shape.columns[0].path == "$._id" and shape.key == ["_id"]
    assert shape.key_source == "primary_key" and shape.row_estimate == 11
    assert by["$.accounts[*].iban"].kind == "text" and by["$.accounts[*].iban"].name == "accounts"
    assert by["$.accounts[*].balance"].data_type == "decimal"
    assert by["$.riskScore"].data_type == "long" and by["$.riskScore"].kind == "int"
    assert by["$.profile.dob"].kind == "timestamp" and by["$._id"].kind == "object_id"
    assert by["$._id"].is_key and not by["$.customerId"].is_key
    assert by["$.nick"].nullable is True and by["$.customerId"].nullable is False
    assert by["$.nick"].data_type == "null" and by["$.nick"].kind == "other"
    # every column of a shape is addressable: the key column by name, paths unique
    assert shape.key_columns()[0].path == "$._id"
    assert len({c.path for c in shape.columns}) == len(shape.columns)


def test_ints_and_longs_are_one_long_column_others_are_mixed() -> None:
    docs = [{"_id": 1, "n": 5, "m": "x"}, {"_id": 2, "n": Int64(2**40), "m": 3}]
    by = {c.path: c for c in md.infer_shape(OBJ, docs, row_estimate=None).columns}
    assert (by["$.n"].data_type, by["$.n"].kind) == ("long", "int")
    assert (by["$.m"].data_type, by["$.m"].kind) == ("mixed(int,string)", "other")


def test_mixed_and_document_ids_have_no_range_key() -> None:
    mixed = [_doc(1), {**_doc(2), "_id": "s-2"}]
    assert md.infer_shape(OBJ, mixed, row_estimate=None).key_source == "none"
    ints = [{**_doc(1), "_id": 1}, {**_doc(2), "_id": Int64(2**40)}]
    shape = md.infer_shape(OBJ, ints, row_estimate=None)
    assert shape.key == ["_id"] and shape.key_columns()[0].kind == "int"
    compound = [{**_doc(1), "_id": {"a": 1}}]
    shape = md.infer_shape(OBJ, compound, row_estimate=None)
    assert shape.key == [] and shape.key_source == "none"
    # a document _id never becomes a key column, so no column claims to be one
    assert not any(c.is_key for c in shape.columns)
    assert md.infer_shape(OBJ, [], row_estimate=0).key_source == "none"


def test_unpageable_id_types_have_no_range_key() -> None:
    """Only _id types the key codec round-trips to the same BSON value are paged by range."""
    for value in (1.5, Decimal128("1"), True, b"\x01"):
        assert md.infer_shape(OBJ, [{"_id": value}], row_estimate=1).key_source == "none"
    for value in ("K1", dt.datetime(2020, 1, 1), uuid.uuid4(), ObjectId()):
        shape = md.infer_shape(OBJ, [{"_id": value}], row_estimate=1)
        assert shape.key == ["_id"]
        encode_key((value,), shape.key_columns())  # the codec accepts the key kind


def test_id_bounds_of_another_type_make_the_shape_keyless() -> None:
    """The sample saw only ObjectIds; the smallest or largest _id says otherwise (review B2)."""
    docs = [{"_id": ObjectId(), "v": i} for i in range(5)]
    assert md.infer_shape(OBJ, docs, row_estimate=5).key == ["_id"]
    for bounds in ([7, docs[0]["_id"]], [docs[0]["_id"], "S1"], [{"a": 1}, docs[1]["_id"]]):
        shape = md.infer_shape(OBJ, docs, row_estimate=7, id_bounds=bounds)
        assert shape.key == [] and shape.key_source == "none"
        assert not any(c.is_key for c in shape.columns)
    same = md.infer_shape(OBJ, docs, row_estimate=5, id_bounds=[docs[0]["_id"], docs[4]["_id"]])
    assert same.key == ["_id"]
    ints = [{"_id": 1}, {"_id": Int64(2)}]
    assert md.infer_shape(OBJ, ints, row_estimate=2, id_bounds=[1, Int64(9)]).key == ["_id"]
    assert md.infer_shape(OBJ, ints, row_estimate=2, id_bounds=[1, 2.5]).key == []
    binary = bson.Binary(b"0123456789abcdef", 0)  # another binData subtype sorts outside UUIDs
    uids = [{"_id": uuid.uuid4()}]
    assert md.infer_shape(OBJ, uids, row_estimate=2, id_bounds=[binary, uids[0]["_id"]]).key == []


def test_restore_leaf_returns_the_original_leaf_type() -> None:
    assert md.restore_leaf(Int64(5), 731) == Int64(731)
    assert isinstance(md.restore_leaf(Int64(5), 731), Int64)
    kept = md.restore_leaf(5, 731)  # an int32 original stays int32
    assert kept == 731 and type(kept) is int
    assert isinstance(md.restore_leaf(5, 2**40), Int64)  # unless it needs 64 bits
    assert md.restore_leaf(Decimal128("1.50"), decimal.Decimal("10.50")) == Decimal128("10.50")
    assert md.restore_leaf(1.5, 2) == 2.0 and isinstance(md.restore_leaf(1.5, 2), float)
    assert md.restore_leaf(dt.datetime(2020, 1, 1), dt.date(1979, 6, 2)) == dt.datetime(1979, 6, 2)
    uid = uuid.uuid4()
    assert md.restore_leaf(uuid.uuid4(), str(uid)) == uid
    assert md.restore_leaf(Int64(5), "42") == Int64(42)
    assert md.restore_leaf(Int64(5), "k7fq2wxb3n") == "k7fq2wxb3n"  # cannot be restored: as is


def test_restore_leaf_never_converts_a_matching_or_untyped_value() -> None:
    value = Int64(9)
    assert md.restore_leaf(Int64(1), value) is value
    text = "0042"
    assert md.restore_leaf("x", text) is text  # a string original is not a number
    oid = "0123456789abcdef01234567"
    assert md.restore_leaf(ObjectId(), oid) is oid  # ObjectIds are never transformed
    assert md.restore_leaf(True, "true") == "true"
    assert md.restore_leaf(None, 5) == 5


def test_upsert_fields_set_through_sub_documents_only() -> None:
    doc = {
        "_id": 1,
        "name": "T",
        "profile": {"fullName": "F", "addr": {"pin": "560001"}},
        "accounts": [{"iban": "GB00TEST"}],
        "empty": {},
        "n": None,
    }
    fields = {
        "name": "T",
        "profile.fullName": "F",
        "profile.addr.pin": "560001",
        "accounts": [{"iban": "GB00TEST"}],
        "n": None,
    }
    assert md.upsert_fields(doc) == fields
    assert md.upsert_update(doc) == {"$set": fields, "$setOnInsert": {"empty": {}}}
    assert md.upsert_fields({"_id": 1}) == {}
    assert md.upsert_update({"_id": 1}) == {"$set": {}}
    for bad in (
        {"a.b": 1},
        {"$x": 1},
        {"p": {"a.b": 1}},
        {"p": {"": 1}},
        {"p": {}, "q": {"$y": 1}},
    ):
        assert md.upsert_fields({"_id": 1, **bad}) is None
        assert md.upsert_update({"_id": 1, **bad}) is None


def test_upsert_update_never_sets_an_empty_sub_document_over_a_match() -> None:
    """G3 r2 B2: an empty sub-document (a sparse source) carries no field, so it is only
    ``$setOnInsert``; a matched document keeps its sub-document."""
    assert md.upsert_update({"_id": 1, "profile": {}, "name": "N"}) == {
        "$set": {"name": "N"},
        "$setOnInsert": {"profile": {}},
    }
    assert md.upsert_update({"_id": 1, "profile": {"addr": {}, "dob": "D"}}) == {
        "$set": {"profile.dob": "D"},
        "$setOnInsert": {"profile.addr": {}},
    }


def test_make_bson_safe_is_recursive() -> None:
    value = {
        "a": [decimal.Decimal("1.2"), {"d": dt.date(2020, 1, 2)}],
        "t": dt.datetime(2020, 1, 1),
    }
    v = md.make_bson_safe(value)
    assert v == {
        "a": [Decimal128("1.2"), {"d": dt.datetime(2020, 1, 2)}],
        "t": dt.datetime(2020, 1, 1),
    }
    bson.encode(v)  # encodes without InvalidDocument
    assert value["a"][0] == decimal.Decimal("1.2")  # the input is not changed


def test_field_names_with_brackets_keep_their_name() -> None:
    """Review G3 r3 B2: a field called ``iban[1]`` is one column named ``iban[1]``."""
    assert md.top_level("$['iban[1]']") == "iban[1]"
    assert md.top_level("$['a[0]x'].b") == "a[0]x"
    docs = [{"_id": i, "iban[1]": f"GB{i}", "acct": {"no[2]": f"N{i}"}} for i in range(5)]
    by = {c.path: c for c in md.infer_shape(OBJ, docs, row_estimate=5).columns}
    assert by["$['iban[1]']"].name == "iban[1]" and by["$.acct['no[2]']"].name == "acct"
