"""Key and value codecs for Oracle's TIMESTAMP(7-9), CHAR and ROWID keys (contract §4.1)."""

from __future__ import annotations

from datetime import datetime

from dps.layers.connectors.base import (
    ColumnInfo,
    decode_key,
    encode_key,
    from_engine_text,
    to_engine_text,
)

TS9 = ColumnInfo(
    path="$.TS", name="TS", data_type="TIMESTAMP(9)", kind="timestamp", ts_precision=9, is_key=True
)
TS6 = ColumnInfo(path="$.T6", name="T6", data_type="TIMESTAMP(6)", kind="timestamp", ts_precision=6)
REF = ColumnInfo(path="$.R", name="R", data_type="CHAR(12)", kind="char", length=12, is_key=True)
RID = ColumnInfo(
    path="$.ROWID", name="ROWID", data_type="ROWID", kind="int", is_key=True, pseudo=True
)


def test_ts9_keys_keep_nine_digits() -> None:
    key = ("2025-03-01T10:00:00.123456789",)
    enc = encode_key(key, [TS9])
    assert enc == [{"t": "ts", "v": "2025-03-01T10:00:00.123456789"}]
    assert decode_key(enc, [TS9]) == key


def test_char_and_rowid_keys() -> None:
    assert decode_key(encode_key(("K17         ",), [REF]), [REF]) == ("K17         ",)
    big = 2**100 + 12345
    assert decode_key(encode_key((big,), [RID]), [RID]) == (big,)


def test_ts9_values_pass_through_the_engine_text_codec() -> None:
    text = "2025-03-01T10:00:00.123456789"
    assert to_engine_text(text, TS9) == text
    assert from_engine_text(text, TS9) == text  # fromisoformat would drop the last three digits
    shifted = "2024-11-17T10:00:00.000000123"
    assert from_engine_text(shifted, TS9) == shifted


def test_timestamps_up_to_microseconds_still_decode_to_datetimes() -> None:
    assert from_engine_text("2025-03-01T10:00:00.123456", TS6) == datetime(
        2025, 3, 1, 10, 0, 0, 123456
    )
