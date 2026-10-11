"""Partition planning from DB-ordered key samples (no Python ordering of keys)."""

from __future__ import annotations

import random

from dps.layers.connectors.base import ColumnInfo, decode_key
from dps.layers.connectors.partitions import partition_count, plan_partitions

INT = [ColumnInfo(path="$.id", name="id", data_type="bigint", kind="int")]
PAIR = INT + [ColumnInfo(path="$.n", name="n", data_type="int", kind="int")]
TEXT = [ColumnInfo(path="$.code", name="code", data_type="text", kind="text")]


def test_partition_count_bounds() -> None:
    assert partition_count(0, partition_rows=50_000, max_partitions=256) == 0
    assert partition_count(1, partition_rows=50_000, max_partitions=256) == 1
    assert partition_count(1_948_210, partition_rows=50_000, max_partitions=256) == 39
    assert partition_count(10**9, partition_rows=50_000, max_partitions=256) == 256
    assert partition_count(-5, partition_rows=50_000, max_partitions=256) == 0
    assert partition_count(10, partition_rows=0, max_partitions=4) == 4  # no division by zero


def test_ranges_are_contiguous_open_ended_and_ordered_by_sample_position() -> None:
    sample = [(10_000_000 + i,) for i in range(0, 1000, 10)]  # already DB-ordered
    ranges = plan_partitions(sample, count=4, columns=INT)
    assert len(ranges) == 4 and ranges[0].lower is None and ranges[-1].upper is None
    for a, b in zip(ranges, ranges[1:], strict=False):
        assert a.upper == b.lower
    assert [decode_key(r.lower, INT)[0] for r in ranges[1:]] == [  # type: ignore[arg-type]
        10_000_250,
        10_000_500,
        10_000_750,
    ]


def test_duplicate_boundaries_collapse_and_upper_applies_to_last_range() -> None:
    sample = [(1,)] * 50 + [(2,)] * 50
    ranges = plan_partitions(sample, count=4, columns=INT, upper=(3,))
    assert len(ranges) == 2 and decode_key(ranges[-1].upper, INT) == (3,)  # type: ignore[arg-type]


def test_composite_keys_and_tiny_samples() -> None:
    sample = [(1, 1), (1, 2), (2, 1), (2, 2)]
    ranges = plan_partitions(sample, count=2, columns=PAIR)
    assert decode_key(ranges[1].lower, PAIR) == (2, 1)  # type: ignore[arg-type]
    assert len(plan_partitions([(5,)], count=8, columns=INT)) == 1
    assert len(plan_partitions([], count=1, columns=INT, upper=(9,))) == 1
    assert len(plan_partitions([(1,), (2,)], count=0, columns=INT)) == 1


def test_every_key_falls_in_exactly_one_range() -> None:
    rng = random.Random(7)
    keys = sorted(rng.sample(range(1, 10**6), 5000))
    sample = [(k,) for k in sorted(rng.sample(keys, 300))]  # DB-ordered sample
    ranges = plan_partitions(sample, count=16, columns=INT, upper=(keys[-1] + 1,))
    bounds = [
        (
            None if r.lower is None else decode_key(r.lower, INT)[0],
            None if r.upper is None else decode_key(r.upper, INT)[0],
        )
        for r in ranges
    ]
    for key in keys:
        hits = [
            i
            for i, (lo, hi) in enumerate(bounds)
            if (lo is None or key >= lo) and (hi is None or key < hi)
        ]
        assert len(hits) == 1


def test_text_keys_follow_the_sample_order_not_python_order() -> None:
    # A case-insensitive collation orders "b" before "C"; Python would put "C" first.
    sample = [("a",), ("b",), ("C",), ("d",)]
    ranges = plan_partitions(sample, count=2, columns=TEXT)
    assert decode_key(ranges[1].lower, TEXT) == ("C",)  # type: ignore[arg-type]
