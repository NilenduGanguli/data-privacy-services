from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from dps.layers.connectors.base import (
    ColumnInfo,
    IncompatibleValue,
    KeyRange,
    LedgerMark,
    ObjectRef,
    PkOffset,
    TableShape,
    UnsupportedKey,
    decode_key,
    encode_key,
    from_engine_text,
    key_fingerprint,
    key_tuple,
    to_engine_text,
)


def col(name: str, kind: str, **kw) -> ColumnInfo:  # noqa: ANN003
    return ColumnInfo(
        path=f"$.{name}", name=name, data_type=kw.pop("data_type", kind), kind=kind, **kw
    )


def test_object_ref_parse_and_fqn() -> None:
    assert ObjectRef.parse("bank.customers") == ObjectRef(owner="bank", name="customers")
    assert ObjectRef.parse("profiles").fqn() == "profiles"
    for bad in ("bank.cust omers", 'a"b', "a.b.c", ""):
        with pytest.raises(ValueError):
            ObjectRef.parse(bad)


@pytest.mark.parametrize(
    "kind,value,text",
    [
        ("int", 12345678, "12345678"),
        ("decimal", Decimal("1E+2"), "100"),
        ("decimal", Decimal("12.50"), "12.50"),
        ("char", "AB12      ", "AB12"),
        ("date", date(1988, 3, 14), "1988-03-14"),
        (
            "timestamp",
            datetime(2025, 1, 2, 3, 4, 5, 123456, tzinfo=UTC),
            "2025-01-02T03:04:05.123456+00:00",
        ),
        ("bool", True, "true"),
        ("uuid", uuid.UUID(int=1), "00000000-0000-0000-0000-000000000001"),
        ("text", "Ananya Krishnan", "Ananya Krishnan"),
        ("text", None, None),
    ],
)
def test_value_codec_round_trip(kind: str, value: object, text: str | None) -> None:
    c = col(
        "c",
        kind,
        length=10 if kind == "char" else None,
        precision=12 if kind == "decimal" else None,
        scale=2 if kind == "decimal" else None,
    )
    assert to_engine_text(value, c) == text
    expected = value.ljust(10) if kind == "char" and isinstance(value, str) else value
    assert from_engine_text(text, c) == expected  # Decimal compares numerically: 100.00 == 1E+2


def test_incompatible_and_overflow_errors_carry_no_value() -> None:
    with pytest.raises(IncompatibleValue) as err:
        from_engine_text("k7fq2wxb3n", col("n", "int"))
    assert err.value.code == "incompatible_method" and "k7fq" not in str(err.value)
    with pytest.raises(IncompatibleValue) as err2:
        from_engine_text("x" * 13, col("s", "text", length=12))
    assert err2.value.code == "length_overflow" and "xxxx" not in str(err2.value)
    with pytest.raises(IncompatibleValue):
        from_engine_text("123456.78", col("d", "decimal", precision=6, scale=2))
    with pytest.raises(IncompatibleValue):
        from_engine_text("abc", col("b", "binary"))


def test_key_codec_round_trip_and_padding() -> None:
    cols = [col("id", "int"), col("code", "char", length=6), col("at", "timestamp")]
    values = (90000001, "AB    ", datetime(2025, 5, 6, 7, 8, 9, 1, tzinfo=UTC))
    enc = encode_key(values, cols)
    assert enc[0] == {"t": "int", "v": "90000001"} and enc[1] == {"t": "char", "v": "AB    "}
    assert decode_key(enc, cols) == values
    with pytest.raises(UnsupportedKey):
        encode_key((1.5,), [col("f", "float")])


def test_key_tuple_and_fingerprint() -> None:
    shape = TableShape(
        object=ObjectRef(owner="bank", name="t"),
        columns=[col("a", "int"), col("b", "text")],
        key=["a", "b"],
        key_source="primary_key",
    )
    assert key_tuple({"b": "x", "a": 1, "z": 0}, shape) == (1, "x")
    fp = key_fingerprint(encode_key((10000001,), [col("a", "int")]))
    assert (
        fp is not None and len(fp) == 8 and "10000001" not in fp and key_fingerprint(None) is None
    )


def test_pk_offset_exactly_one() -> None:
    assert PkOffset(offset=90_000_000).offset == 90_000_000 and PkOffset(prefix="X-").prefix == "X-"
    for bad in ({}, {"offset": 5, "prefix": "X"}, {"offset": 0}):
        with pytest.raises(ValueError):
            PkOffset(**bad)


# --- added (M2 build): value-free failure paths of the codecs -------------------------------


def test_null_key_and_huge_decimal_fail_without_values() -> None:
    with pytest.raises(UnsupportedKey) as err:
        encode_key((None,), [col("id", "int")])
    assert "None" not in str(err.value)
    wide = col("d", "decimal", precision=60, scale=2)  # wider than the default decimal context
    assert from_engine_text("1" * 50 + ".5", wide) == Decimal("1" * 50 + ".50")
    with pytest.raises(IncompatibleValue) as err2:
        from_engine_text("9" * 59, wide)
    assert err2.value.code == "length_overflow" and "999" not in str(err2.value)


def test_key_bearing_models_never_echo_keys_in_errors() -> None:
    bad_key = [{"t": "int", "v": 48213977}]  # an int where the codec stores text
    with pytest.raises(ValueError) as err:
        LedgerMark(job_id="j_1", partition_no=0, batch_no=1, last_key=bad_key)  # type: ignore[arg-type]
    assert "48213977" not in str(err.value)
    with pytest.raises(ValueError) as err2:
        KeyRange(lower=[{"t": "int", "v": 48213977}])  # type: ignore[list-item]
    assert "48213977" not in str(err2.value)
