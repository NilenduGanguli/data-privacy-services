"""BSON labels, path-shape inference from a sample, and BSON type restoration (offline)."""

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


def test_coerce_restores_bson_types() -> None:
    shape = md.infer_shape(OBJ, [_doc(1)], row_estimate=1)
    out = {
        "_id": "0123456789abcdef01234567",
        "customerId": "CUST-73104482",
        "riskScore": 731,
        "profile": {"fullName": "Tilqoe Mzvhrpkat", "dob": dt.date(1979, 6, 2)},
        "accounts": [{"iban": "GB00TEST0000000001", "balance": decimal.Decimal("10.50")}],
    }
    md.coerce_bson(out, shape)
    assert isinstance(out["_id"], ObjectId) and isinstance(out["riskScore"], Int64)
    assert out["riskScore"] == 731
    assert out["profile"]["dob"] == dt.datetime(1979, 6, 2)
    assert out["accounts"][0]["balance"] == Decimal128("10.50")
    assert out["customerId"] == "CUST-73104482"
    token = {"riskScore": "k7fq2wxb3n"}
    md.coerce_bson(token, shape)
    assert token["riskScore"] == "k7fq2wxb3n"  # cannot be restored: left as is


def test_coerce_text_numbers_and_every_array_element() -> None:
    docs = [{"_id": 1, "n": Int64(1), "d": 1.5, "a": [{"v": Decimal128("1")}]}]
    shape = md.infer_shape(OBJ, docs, row_estimate=1)
    out = {"_id": 1, "n": "42", "d": "2.25", "a": [{"v": "3.5"}, {"v": "4"}, {"w": 1}]}
    md.coerce_bson(out, shape)
    assert out["n"] == Int64(42) and isinstance(out["n"], Int64)
    assert out["d"] == 2.25 and isinstance(out["d"], float)
    assert out["a"] == [{"v": Decimal128("3.5")}, {"v": Decimal128("4")}, {"w": 1}]
    md.coerce_bson({"_id": 2}, shape)  # absent paths are nothing to restore


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
