"""The MongoDB filter allow-list (contract §5.2) and Extended-JSON literal compilation (offline)."""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest

pytest.importorskip("bson")
from bson import Decimal128, Int64, ObjectId  # noqa: E402

from dps.layers.connectors.selection import (  # noqa: E402
    MAX_FILTER_NODES,
    Selection,
    SelectionError,
    check_for,
    compile_mongo_filter,
    mongo_filter_template,
    validate_mongo_filter,
    validate_selection,
)

OK: list[dict[str, Any]] = [
    {"segment": "retail"},
    {"riskScore": {"$gte": 600, "$lt": 900}},
    {"$or": [{"contact.email": {"$exists": True}}, {"tags": {"$all": ["vip", "salary"]}}]},
    {"accounts": {"$elemMatch": {"currency": "INR", "balance": {"$gt": 0}}}},
    {"accounts": {"$size": 2}},
    {"profile.fullName": {"$regex": "(?i)^an"}},
    {"riskScore": {"$not": {"$gt": 900}}},
    {"createdAt": {"$gte": {"$date": "2025-01-01T00:00:00Z"}}},
    {"_id": {"$in": [{"$oid": "0123456789abcdef01234567"}]}},
    {"address": {"city": "Pune", "pin": "411001"}},
    {},
    {"$nor": [{"segment": "x"}], "kyc.status": {"$type": ["string", 10]}},
    {"scores": {"$elemMatch": {"$gte": 1, "$lt": 5}}},
    {"n": {"$eq": None}, "flag": False},
]
DENIED: list[dict[str, Any]] = [
    {"$where": "this.a == 1"},
    {"$expr": {"$eq": ["$a", "$b"]}},
    {"a": {"$function": {"body": "x", "args": [], "lang": "js"}}},
    {"$and": [{"a": 1}, {"$where": "1"}]},
    {"a": {"$elemMatch": {"$where": "1"}}},
    {"a": {"$not": {"$expr": 1}}},
    {"a": {"$regex": "x", "$options": "i"}},
    {"a": {"$accumulator": {}}},
    {"$jsonSchema": {}},
    {"a": {"b": {"$where": "1"}}},
    {"$text": {"$search": "x"}},
    {"loc": {"$near": [0, 0]}},
    {"a": {"$in": [{"$where": "1"}]}},
    {"a": {"$eq": {"$function": {}}}},
    {"a": {"$all": [{"$elemMatch": {"$where": "1"}}]}},
    {"$or": [{"$comment": "x"}]},
    {"a": {"$elemMatch": {"b": {"$where": "1"}}}},
    {"a": [{"$where": "1"}]},
]


@pytest.mark.parametrize("f", OK)
def test_allowed_filters(f: dict[str, Any]) -> None:
    validate_mongo_filter(f)
    validate_selection(Selection(mongo_filter=f), "mongo", None)


@pytest.mark.parametrize("f", DENIED)
def test_denied_operators(f: dict[str, Any]) -> None:
    with pytest.raises(SelectionError) as info:
        validate_mongo_filter(f)
    assert info.value.code == "selection_operator_denied"
    with pytest.raises(SelectionError):
        compile_mongo_filter(f)  # compilation validates first


@pytest.mark.parametrize(
    "f,code",
    [
        ({"bad-field": 1}, "selection_identifier_invalid"),
        ({"a..b": 1}, "selection_identifier_invalid"),
        ({"$and": []}, "selection_invalid"),
        ({"$or": {"a": 1}}, "selection_invalid"),
        ({"a": {"$in": 5}}, "selection_invalid"),
        ({"a": {"$size": -1}}, "selection_invalid"),
        ({"a": {"$size": True}}, "selection_invalid"),
        ({"a": {"$exists": "yes"}}, "selection_invalid"),
        ({"a": {"$gt": 1, "b": 2}}, "selection_invalid"),
        ({"a": {"$not": 5}}, "selection_invalid"),
        ({"a": {"$regex": 5}}, "selection_invalid"),
        ({"a": {"$type": True}}, "selection_invalid"),
        ({"a": {"$elemMatch": {}}}, "selection_invalid"),
        ({"a": {"$and": [{"b": 1}]}}, "selection_invalid"),
        ({"$eq": 1}, "selection_invalid"),
        ({"a": {"$regex": "x" * 257}}, "selection_regex_too_long"),
        ({"a": {"$in": list(range(1001))}}, "selection_too_complex"),
        ({"a": {"$date": "not-a-date"}}, "selection_invalid"),
        ({"a": {"$oid": "xyz"}}, "selection_invalid"),
        ({"a": {"$oid": {"$where": "1"}}}, "selection_invalid"),
        ({"a": {"$numberLong": "12x"}}, "selection_invalid"),
        ({"a": {"$numberLong": str(2**63)}}, "selection_invalid"),
        ({"a": {"$numberDecimal": "ten"}}, "selection_invalid"),
        ({"a": {"$date": True}}, "selection_invalid"),
    ],
)
def test_structural_errors(f: dict[str, Any], code: str) -> None:
    with pytest.raises(SelectionError) as info:
        validate_mongo_filter(f)
        compile_mongo_filter(f)
    assert info.value.code == code
    assert check_for(code) is not None  # the wizard's checklist names the failing entry


def test_errors_are_value_free() -> None:
    for f in (
        {"a": {"$oid": "SENTINEL-OID"}},
        {"SENTINEL-FIELD": 1},
        {"$where": "SENTINEL-JS"},
        {"a": {"$regex": "SENTINEL" * 40}},
    ):
        with pytest.raises(SelectionError) as info:
            compile_mongo_filter(f)
        assert "SENTINEL" not in str(info.value) and "SENTINEL" not in repr(info.value.args)
        assert info.value.__cause__ is None


def test_depth_limit() -> None:
    f: dict[str, Any] = {"a": 1}
    for _ in range(8):
        f = {"$and": [f]}
    validate_mongo_filter(f)  # eight levels are allowed
    with pytest.raises(SelectionError) as info:
        validate_mongo_filter({"$and": [f]})
    assert info.value.code == "selection_too_complex"
    deep: Any = 1
    for _ in range(12):
        deep = {"k": deep}
    with pytest.raises(SelectionError) as info:
        validate_mongo_filter({"a": deep})  # a literal embedded document counts as well
    assert info.value.code == "selection_too_complex"


def test_node_budget() -> None:
    wide = {"$or": [{f"f{i}": {"$gt": i, "$lt": i + 1}} for i in range(MAX_FILTER_NODES)]}
    with pytest.raises(SelectionError) as info:
        validate_mongo_filter(wide)
    assert info.value.code == "selection_too_complex"
    validate_mongo_filter({"a": {"$in": list(range(1000))}})  # scalars in $in cost nothing


def test_compile_converts_ejson_literals_without_mutating() -> None:
    f = {
        "createdAt": {"$gte": {"$date": "2025-01-01T00:00:00Z"}},
        "n": {"$numberLong": "9007199254740993"},
        "amt": {"$lt": {"$numberDecimal": "10.50"}},
        "_id": {"$oid": "0123456789abcdef01234567"},
        "ids": {"$in": [{"$oid": "0123456789abcdef01234568"}, "plain"]},
        "ms": {"$date": 1735689600000},
        "local": {"$date": "2025-01-01T05:30:00"},
    }
    out = compile_mongo_filter(f)
    assert out["createdAt"]["$gte"] == dt.datetime(2025, 1, 1, tzinfo=dt.UTC)
    assert out["n"] == Int64(9007199254740993) and isinstance(out["n"], Int64)
    assert out["amt"]["$lt"] == Decimal128("10.50")
    assert out["_id"] == ObjectId("0123456789abcdef01234567")
    assert out["ids"]["$in"] == [ObjectId("0123456789abcdef01234568"), "plain"]
    assert out["ms"] == dt.datetime(2025, 1, 1, tzinfo=dt.UTC)
    assert out["local"] == dt.datetime(2025, 1, 1, 5, 30, tzinfo=dt.UTC)
    assert f["n"] == {"$numberLong": "9007199254740993"}  # the input is unchanged
    assert compile_mongo_filter(None) == {} and compile_mongo_filter({}) == {}


def test_template_hides_wrapped_literals() -> None:
    f = {"_id": {"$oid": "0123456789abcdef01234567"}, "t": {"$gte": {"$date": "2025-01-01"}}}
    assert mongo_filter_template(f) == {"_id": {"$oid": "?"}, "t": {"$gte": {"$date": "?"}}}


def test_engine_mismatch() -> None:
    cases = [
        (Selection(where="a = 1"), "mongo"),
        (Selection(query="SELECT 1"), "mongo"),
        (Selection(mongo_filter={"a": 1}), "oracle"),
        (Selection(mongo_filter={"a": 1}), "postgres"),
        (Selection(mongo_filter={"a": 1}, params={"x": 1}), "mongo"),
        (Selection(params={"x": 1}), "mongo"),
    ]
    for selection, engine in cases:
        with pytest.raises(SelectionError) as info:
            validate_selection(selection, engine, None)  # type: ignore[arg-type]
        assert info.value.code == "selection_engine_mismatch"
