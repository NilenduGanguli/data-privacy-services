"""Key-space partitioning (spec §7 "Execution", contract §4).

Boundaries are taken **by position** from a key sample the database returned in ``ORDER BY key``
order, so the ranges always agree with the collation that the keyset predicates use: Python never
orders two keys, it only compares neighbouring boundaries for equality (equal ones collapse).
Ranges are ``[lower, upper)``; the first is open below, the last is bounded by the global
``upper`` (``max_rows`` or a same-table append) or open. The connector's ``sample_keys(...,
upper=...)`` only returns keys below that bound, so every boundary is below it.

Keys are customer data: the encoded ranges go only to ``dps_job_partition.{lower_key,upper_key}``.
"""

from __future__ import annotations

import math
from typing import Any

from dps.layers.connectors.base import ColumnInfo, EncodedKey, KeyRange, encode_key


def partition_count(rows: int, *, partition_rows: int, max_partitions: int) -> int:
    """Partitions for ``rows`` selected rows: ``ceil(rows / partition_rows)`` clamped to
    ``[1, max_partitions]``; 0 when nothing is selected."""
    if rows <= 0:
        return 0
    return max(1, min(max_partitions, math.ceil(rows / max(1, partition_rows))))


def plan_partitions(
    sample: list[tuple[Any, ...]],
    *,
    count: int,
    columns: list[ColumnInfo],
    upper: tuple[Any, ...] | None = None,
) -> list[KeyRange]:
    """Contiguous ranges from a DB-ordered key sample (all sample keys are below ``upper``).

    Returns at most ``count`` ranges (fewer when boundaries collapse or the sample is tiny), and
    always at least one.
    """
    top = encode_key(upper, columns) if upper is not None else None
    if count <= 1 or len(sample) < 2:
        return [KeyRange(lower=None, upper=top)]
    bounds: list[tuple[Any, ...]] = []
    for i in range(1, count):
        candidate = sample[(i * len(sample)) // count]
        if candidate != sample[0] and (not bounds or candidate != bounds[-1]):
            bounds.append(candidate)
    encoded = [encode_key(b, columns) for b in bounds]
    lowers: list[EncodedKey | None] = [None, *encoded]
    uppers: list[EncodedKey | None] = [*encoded, top]
    return [KeyRange(lower=lo, upper=hi) for lo, hi in zip(lowers, uppers, strict=True)]
