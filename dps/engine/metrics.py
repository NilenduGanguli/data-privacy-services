"""Synthetic-data fidelity and privacy metrics (spec §5.7, contract §7.3).

Inputs are bounded in-memory samples held by the worker during ``prepare``; every function
returns numbers (and column paths) only. Nothing here logs, persists or returns a value.

* **Fidelity**: two-sample Kolmogorov–Smirnov per numeric column, total-variation distance per
  categorical column, null-rate deltas, and the mean absolute difference of the Spearman
  correlation matrices; :func:`fidelity_report` combines them into the evidence block.
* **Privacy**: exact-row matches (unkeyed :func:`row_hash` for in-memory comparison, keyed
  :func:`row_digest` for the sealed reject set), the nearest-neighbour distance ratio
  (:func:`nndr`) on :func:`encode_features` vectors, and quasi-identifier uniqueness.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import numpy as np
from scipy import stats

NUMERIC_KINDS = frozenset({"int", "decimal", "float", "date", "timestamp"})
_REFERENCE_MAX = 10_000
_CHUNK = 512
_TOP_CATEGORIES = 20
_ZERO = 1e-9


def to_float(value: Any) -> float | None:
    """Numeric view of a value.

    Numbers as-is, bools 0/1, dates as ordinal days, timestamps as epoch seconds (a naive
    timestamp is read as UTC). Anything else, and a non-finite number, is ``None``.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, int | float | Decimal):
        try:
            out = float(value)
        except (OverflowError, ValueError):
            return None
        return out if math.isfinite(out) else None
    if isinstance(value, datetime):
        return (value if value.tzinfo else value.replace(tzinfo=UTC)).timestamp()
    if isinstance(value, date):
        return float(value.toordinal())
    return None


def canonical(value: Any) -> str | bool | None:
    """A comparable form: ``1 == 1.0 == Decimal("1.00")`` → ``"1"``; dates ISO; bytes hex."""
    if value is None or isinstance(value, bool | str):
        return value
    if isinstance(value, int | float | Decimal):
        try:
            number = value if isinstance(value, Decimal) else Decimal(str(value))
            return format(number.normalize(), "f")
        except InvalidOperation:
            return str(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, bytes | bytearray | memoryview):
        return bytes(value).hex()
    return str(value)


def _numbers(values: Sequence[Any]) -> np.ndarray:
    return np.asarray([x for v in values if (x := to_float(v)) is not None], dtype=float)


def ks_statistic(real: Sequence[Any], synth: Sequence[Any]) -> float:
    """Two-sample Kolmogorov–Smirnov statistic on the non-null numeric views."""
    r, s = _numbers(real), _numbers(synth)
    if r.size == 0 or s.size == 0:
        return 0.0 if r.size == s.size == 0 else 1.0
    return float(stats.ks_2samp(r, s).statistic)


def total_variation(real: Sequence[str | None], synth: Sequence[str | None]) -> float:
    """½ Σ |p_real − p_synth| over the non-null categories."""
    rc = Counter(v for v in real if v is not None)
    sc = Counter(v for v in synth if v is not None)
    rn, sn = sum(rc.values()), sum(sc.values())
    if rn == 0 or sn == 0:
        return 0.0 if rn == sn == 0 else 1.0
    return 0.5 * sum(abs(rc[k] / rn - sc[k] / sn) for k in set(rc) | set(sc))


def null_rate_delta(real: Sequence[Any], synth: Sequence[Any]) -> float:
    """Synthetic null rate minus real null rate (signed)."""

    def rate(values: Sequence[Any]) -> float:
        return sum(1 for v in values if v is None) / len(values) if values else 0.0

    return rate(synth) - rate(real)


def _spearman(data: Mapping[str, Sequence[Any]], names: list[str]) -> np.ndarray:
    cols = [
        np.asarray([np.nan if (x := to_float(v)) is None else x for v in data[n]], dtype=float)
        for n in names
    ]
    out = np.eye(len(names))
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            both = np.isfinite(cols[i]) & np.isfinite(cols[j])
            a, b = cols[i][both], cols[j][both]
            rho = 0.0
            if a.size >= 3 and np.ptp(a) > 0 and np.ptp(b) > 0:
                rho = float(stats.spearmanr(a, b).statistic)
            out[i, j] = out[j, i] = 0.0 if math.isnan(rho) else rho
    return out


def correlation_matrix_delta(
    real: Mapping[str, Sequence[Any]], synth: Mapping[str, Sequence[Any]]
) -> tuple[list[str], list[list[float]]]:
    """|Spearman(real) − Spearman(synth)| over the columns both share, rounded to 4 places."""
    names = sorted(set(real) & set(synth))
    if len(names) < 2:
        return names, [[0.0] * len(names) for _ in names]
    delta = np.abs(_spearman(real, names) - _spearman(synth, names))
    return names, [[round(float(v), 4) for v in row] for row in delta]


def correlation_delta(
    real: Mapping[str, Sequence[Any]], synth: Mapping[str, Sequence[Any]]
) -> float:
    """Mean absolute Spearman-correlation difference over distinct column pairs."""
    names, matrix = correlation_matrix_delta(real, synth)
    pairs = [matrix[i][j] for i in range(len(names)) for j in range(i)]
    return float(np.mean(pairs)) if pairs else 0.0


def _row_payload(row: Mapping[str, Any], cols: Sequence[str]) -> bytes:
    return json.dumps(
        [canonical(row.get(c)) for c in cols], separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def row_hash(row: Mapping[str, Any], cols: Sequence[str]) -> str:
    """Unkeyed sha256 of the canonical row — in-memory comparisons only, never persisted."""
    return hashlib.sha256(_row_payload(row, cols)).hexdigest()


def row_digest(row: Mapping[str, Any], cols: Sequence[str], key: bytes) -> int:
    """Keyed 64-bit digest (HMAC-SHA256) — the form the sealed copula reject set stores."""
    mac = hmac.new(key, _row_payload(row, cols), hashlib.sha256).digest()
    return int.from_bytes(mac[:8], "big")


def exact_row_matches(
    real_hashes: set[str], synth_rows: Iterable[Mapping[str, Any]], cols: list[str]
) -> int:
    """How many synthetic rows equal a real row on every column in ``cols``."""
    return sum(1 for row in synth_rows if row_hash(row, cols) in real_hashes)


def encode_features(
    columns: Mapping[str, Sequence[Any]],
    kinds: Mapping[str, str],
    reference: Mapping[str, Sequence[Any]] | None = None,
) -> np.ndarray:
    """Rows → features in [0, 1], column paths in sorted order.

    Numbers (and bools): min–max on the reference's 1st–99th percentiles plus a null indicator.
    Categories: one-hot over the reference's 20 most frequent (+ other, + null), scaled by 1/√2 so
    a mismatch costs 1 — the same as a full-range numeric difference.
    """
    ref = reference if reference is not None else columns
    n = len(next(iter(columns.values()))) if columns else 0
    blocks: list[np.ndarray] = []
    for path in sorted(kinds):
        values = columns[path]
        if kinds[path] in NUMERIC_KINDS or kinds[path] == "bool":
            ref_x = _numbers(ref[path])
            lo, hi = 0.0, 1.0
            if ref_x.size:
                lo, hi = float(np.percentile(ref_x, 1)), float(np.percentile(ref_x, 99))
            span = (hi - lo) or 1.0
            x = np.asarray([np.nan if (f := to_float(v)) is None else f for v in values], float)
            null = np.isnan(x)
            blocks.append(np.clip((np.where(null, lo, x) - lo) / span, 0.0, 1.0)[:, None])
            blocks.append(null.astype(float)[:, None])
        else:
            counts = Counter(canonical(v) for v in ref[path] if v is not None)
            ranked = sorted(counts.items(), key=lambda kv: (-kv[1], str(kv[0])))
            top = [c for c, _ in ranked[:_TOP_CATEGORIES]]
            index = {c: i for i, c in enumerate(top)}
            onehot = np.zeros((len(values), len(top) + 2))
            for r, v in enumerate(values):
                onehot[r, len(top) + 1 if v is None else index.get(canonical(v), len(top))] = 1.0
            blocks.append(onehot / math.sqrt(2.0))
    return np.hstack(blocks) if blocks else np.zeros((n, 0))


def _nearest(
    queries: np.ndarray, reference: np.ndarray, exclude: np.ndarray | None = None
) -> np.ndarray:
    """Euclidean distance from each query row to its nearest reference row (chunked brute force).

    ``exclude[i]`` is the reference index query ``i`` must skip (leave-one-out). The nearest row
    is found on the expanded form |q|² + |r|² − 2q·r and its distance recomputed directly, so an
    exact copy measures exactly 0 whatever the magnitude of its values.
    """
    ref_sq = np.einsum("ij,ij->i", reference, reference)
    out = np.empty(len(queries))
    for start in range(0, len(queries), _CHUNK):
        q = queries[start : start + _CHUNK]
        d2 = np.einsum("ij,ij->i", q, q)[:, None] + ref_sq[None, :] - 2.0 * (q @ reference.T)
        np.maximum(d2, 0.0, out=d2)
        if exclude is not None:
            d2[np.arange(len(q)), exclude[start : start + len(q)]] = np.inf
        nearest = reference[np.argmin(d2, axis=1)]
        out[start : start + len(q)] = np.sqrt(np.einsum("ij,ij->i", q - nearest, q - nearest))
    return out


def nndr(
    real: np.ndarray,
    synth: np.ndarray,
    *,
    sample: int = 2000,
    seed: int = 0,
    holdout: np.ndarray | None = None,
) -> dict[str, float]:
    """Nearest-neighbour distance ratio.

    The median distance synthetic → real over the median distance holdout → real (``holdout``
    given) or real → other real (leave-one-out). ≈ 1 means synthetic rows sit no closer to real
    rows than unseen real rows do; ≪ 1 means copying. ``measured`` is 0 when there is too little
    data (fewer than two real rows, no synthetic row, no feature).
    """
    empty = {
        "ratio": 0.0,
        "synth_to_real_median": 0.0,
        "real_to_real_median": 0.0,
        "synth_rows": 0.0,
        "real_rows": float(len(real)),
        "holdout": 0.0,
        "zero_distance_share": 0.0,
        "measured": 0.0,
    }
    if len(real) < 2 or len(synth) == 0 or real.ndim != 2 or real.shape[1] == 0:
        return empty
    rng = np.random.default_rng(seed)

    def pick(data: np.ndarray, limit: int) -> np.ndarray:
        return data if len(data) <= limit else data[rng.choice(len(data), limit, replace=False)]

    ref = pick(real, _REFERENCE_MAX)
    q_s = pick(synth, sample)
    d_s = _nearest(q_s, ref)
    with_holdout = holdout is not None and len(holdout) > 0
    if holdout is not None and with_holdout:
        d_r = _nearest(pick(holdout, sample), ref)
    else:
        idx = rng.choice(len(ref), min(sample, len(ref)), replace=False)
        d_r = _nearest(ref[idx], ref, exclude=idx)
    med_s, med_r = float(np.median(d_s)), float(np.median(d_r))
    ratio = round(med_s / med_r, 4) if med_r > 0 else (1.0 if med_s > 0 else 0.0)
    return {
        "ratio": ratio,
        "synth_to_real_median": round(med_s, 6),
        "real_to_real_median": round(med_r, 6),
        "synth_rows": float(len(q_s)),
        "real_rows": float(len(ref)),
        "holdout": 1.0 if with_holdout else 0.0,
        "zero_distance_share": round(float(np.mean(d_s < _ZERO)), 4),
        "measured": 1.0,
    }


def _qi_keys(rows: Iterable[Mapping[str, Any]], qis: list[str]) -> list[tuple[Any, ...]]:
    return [tuple(canonical(r.get(q)) for q in qis) for r in rows]


def qi_uniqueness(rows: Iterable[Mapping[str, Any]], quasi_identifiers: list[str]) -> float:
    """Share of rows whose quasi-identifier combination occurs exactly once."""
    if not quasi_identifiers:
        return 0.0
    keys = _qi_keys(rows, quasi_identifiers)
    if not keys:
        return 0.0
    counts = Counter(keys)
    return sum(1 for k in keys if counts[k] == 1) / len(keys)


def qi_unique_overlap(
    real_rows: Iterable[Mapping[str, Any]],
    synth_rows: Iterable[Mapping[str, Any]],
    quasi_identifiers: list[str],
) -> int:
    """How many QI-unique synthetic combinations equal a QI-unique real combination."""
    if not quasi_identifiers:
        return 0
    real = Counter(_qi_keys(real_rows, quasi_identifiers))
    synth = Counter(_qi_keys(synth_rows, quasi_identifiers))
    unique_real = {k for k, c in real.items() if c == 1}
    return sum(1 for k, c in synth.items() if c == 1 and k in unique_real)


def generalize_qi(value: Any, how: str) -> str | None:
    """Coarsen a quasi-identifier before uniqueness is counted.

    ``how``: ``decade`` (a date → ``"1980s"``), ``band10`` (a number → ``"40-49"``), ``prefix3``
    (the first three characters) or ``exact`` (the canonical text, lower-cased).
    """
    if value is None:
        return None
    if how == "decade" and isinstance(value, date):
        return f"{value.year // 10 * 10}s"
    if how == "band10" and (x := to_float(value)) is not None:
        lo = int(x // 10 * 10)
        return f"{lo}-{lo + 9}"
    if how == "prefix3":
        return str(canonical(value))[:3]
    return str(canonical(value)).lower()


def fidelity_report(
    real: Mapping[str, Sequence[Any]],
    synth: Mapping[str, Sequence[Any]],
    kinds: Mapping[str, str],
    *,
    column_limit: float,
    corr_limit: float,
) -> dict[str, Any]:
    """Per-column KS/TVD and null-rate delta, the Spearman matrix delta, and the score.

    The score is ``1 − mean column distance − mean correlation delta`` (floored at 0; mockup 10).
    Keys are column paths and metric names; values are numbers — never a column value.
    """
    columns: list[dict[str, Any]] = []
    ks: dict[str, float] = {}
    tvd: dict[str, float] = {}
    nulls: dict[str, float] = {}
    for path in sorted(kinds):
        if kinds[path] in NUMERIC_KINDS:
            distance, metric = ks_statistic(real[path], synth[path]), "ks"
            ks[path] = round(distance, 4)
        else:
            real_text = [None if v is None else str(canonical(v)) for v in real[path]]
            synth_text = [None if v is None else str(canonical(v)) for v in synth[path]]
            distance, metric = total_variation(real_text, synth_text), "tvd"
            tvd[path] = round(distance, 4)
        delta = round(null_rate_delta(real[path], synth[path]), 4)
        nulls[path] = delta
        columns.append(
            {
                "path": path,
                "kind": kinds[path],
                "metric": metric,
                "distance": round(distance, 4),
                "null_rate_delta": delta,
                "within_limit": bool(distance <= column_limit),
            }
        )
    numeric = [p for p in kinds if kinds[p] in NUMERIC_KINDS]
    names, matrix = correlation_matrix_delta(
        {p: real[p] for p in numeric}, {p: synth[p] for p in numeric}
    )
    pairs = [matrix[i][j] for i in range(len(names)) for j in range(i)]
    corr = round(float(np.mean(pairs)), 4) if pairs else 0.0
    mean_col = float(np.mean([c["distance"] for c in columns])) if columns else 0.0
    return {
        "ks": ks,
        "tvd": tvd,
        "null_rate_delta": nulls,
        "columns": columns,
        "corr_delta": corr,
        "corr_max": round(max(pairs, default=0.0), 4),
        "corr_within_limit": bool(corr <= corr_limit),
        "corr_matrix": {"columns": names, "delta": matrix},
        "limits": {"column": column_limit, "corr": corr_limit},
        "score": round(max(0.0, 1.0 - mean_col - corr), 4),
    }
