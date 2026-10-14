"""Fidelity and privacy metrics (spec §5.7, contract §7.3): numbers and column paths only."""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("scipy")

from dps.engine.metrics import (  # noqa: E402
    canonical,
    correlation_delta,
    correlation_matrix_delta,
    encode_features,
    exact_row_matches,
    fidelity_report,
    generalize_qi,
    ks_statistic,
    nndr,
    null_rate_delta,
    qi_unique_overlap,
    qi_uniqueness,
    row_digest,
    row_hash,
    to_float,
    total_variation,
)

RNG = np.random.default_rng(7)


def test_to_float_and_canonical_agree_across_types() -> None:
    assert to_float(True) == 1.0 and to_float(Decimal("2.50")) == 2.5 and to_float(None) is None
    assert to_float(date(2026, 1, 2)) == float(date(2026, 1, 2).toordinal())
    assert to_float(datetime(1970, 1, 1, 0, 0, 10)) == 10.0
    assert to_float("12") is None and to_float(float("nan")) is None
    assert canonical(1) == canonical(1.0) == canonical(Decimal("1.00")) == "1"
    assert (
        canonical(Decimal("100")) == "100" and canonical(None) is None and canonical(True) is True
    )
    assert canonical(b"\x01\xff") == "01ff" and canonical(date(2026, 1, 2)) == "2026-01-02"


def test_ks_is_zero_for_identical_and_one_for_disjoint() -> None:
    a = RNG.normal(size=500).tolist()
    assert ks_statistic(a, a) == 0.0
    assert ks_statistic([1, 2, 3], [10, 11, 12]) == 1.0
    assert 0.0 < ks_statistic(a, (RNG.normal(size=500) + 0.3).tolist()) < 0.5
    assert ks_statistic([], []) == 0.0 and ks_statistic([1.0], []) == 1.0


def test_tvd_and_null_rate_delta() -> None:
    assert total_variation(["a", "b"], ["b", "a"]) == 0.0
    assert total_variation(["a", "a"], ["b", "b"]) == 1.0
    assert total_variation(["a", "b", None], ["a", "a"]) == pytest.approx(0.5)
    assert null_rate_delta([1, None, None, 4], [1, 2, 3, None]) == pytest.approx(-0.25)


def test_correlation_deltas() -> None:
    x = RNG.normal(size=400)
    real = {"$.A": x.tolist(), "$.B": (x * 2 + RNG.normal(scale=0.1, size=400)).tolist()}
    same = {"$.A": real["$.A"], "$.B": real["$.B"]}
    broken = {"$.A": real["$.A"], "$.B": RNG.permutation(real["$.B"]).tolist()}
    assert correlation_delta(real, same) == pytest.approx(0.0)
    assert correlation_delta(real, broken) > 0.8
    names, matrix = correlation_matrix_delta(real, broken)
    assert names == ["$.A", "$.B"] and matrix[0][0] == 0.0 and matrix[1][0] == matrix[0][1] > 0.8


def test_row_hash_digest_and_exact_matches() -> None:
    cols = ["A", "B"]
    real = [{"A": 1, "B": "x"}, {"A": 2, "B": "y"}]
    hashes = {row_hash(r, cols) for r in real}
    synth = [{"A": Decimal("1.0"), "B": "x"}, {"A": 3, "B": "x"}]
    assert exact_row_matches(hashes, synth, cols) == 1
    key = b"k" * 32
    assert row_digest(real[0], cols, key) == row_digest({"A": 1.0, "B": "x"}, cols, key)
    assert row_digest(real[0], cols, key) != row_digest(real[0], cols, b"j" * 32)
    assert 0 <= row_digest(real[0], cols, key) < 2**64


def test_encode_features_scales_and_one_hots() -> None:
    cols = {"$.N": [0, 50, 100, None], "$.C": ["a", "b", "a", None]}
    x = encode_features(cols, {"$.N": "int", "$.C": "categorical"})
    # columns sorted: $.C block (a, b, other, null) / √2, then $.N (scaled, null indicator)
    assert x.shape == (4, 6) and np.all((x >= 0) & (x <= 1))
    assert x[3, 3] == pytest.approx(2**-0.5) and x[3, 5] == 1.0 and x[2, 4] == 1.0


def test_nndr_detects_copies_and_accepts_fresh_draws() -> None:
    real = RNG.normal(size=(2000, 3))
    holdout = RNG.normal(size=(500, 3))
    fresh = RNG.normal(size=(500, 3))
    copies = real[:500].copy()
    good = nndr(real, fresh, seed=1, holdout=holdout)
    bad = nndr(real, copies, seed=1, holdout=holdout)
    assert 0.8 < good["ratio"] < 1.25 and good["holdout"] == 1.0
    assert bad["ratio"] < 0.05 and bad["zero_distance_share"] == 1.0
    loo = nndr(real, fresh, seed=1)
    assert loo["holdout"] == 0.0 and loo["ratio"] > 0.5
    assert nndr(real[:1], fresh, seed=1)["measured"] == 0.0


def test_nndr_counts_exact_copies_of_large_values_as_zero_distance() -> None:
    # |q|² + |r|² − 2q·r loses the exact zero for large coordinates; the distance is recomputed.
    real = RNG.normal(loc=1e6, scale=1e3, size=(300, 4))
    out = nndr(real, real[:100].copy(), seed=3)
    assert out["zero_distance_share"] == 1.0 and out["synth_to_real_median"] == 0.0


def test_qi_uniqueness_overlap_and_generalisation() -> None:
    rows = [{"G": "F", "Z": "560"}, {"G": "F", "Z": "560"}, {"G": "M", "Z": "400"}]
    assert qi_uniqueness(rows, ["G", "Z"]) == pytest.approx(1 / 3)
    assert qi_uniqueness(rows, []) == 0.0
    synth = [{"G": "M", "Z": "400"}, {"G": "X", "Z": "999"}]
    assert qi_unique_overlap(rows, synth, ["G", "Z"]) == 1
    assert generalize_qi(date(1987, 5, 1), "decade") == "1980s"
    assert generalize_qi(43, "band10") == "40-49" and generalize_qi("560034", "prefix3") == "560"
    assert generalize_qi(None, "exact") is None


def test_fidelity_report_shape_and_score() -> None:
    x = RNG.normal(size=300)
    real = {"$.N": x.tolist(), "$.M": (x + 1).tolist(), "$.C": ["alpha", "beta", "gamma"] * 100}
    kinds = {"$.N": "float", "$.M": "float", "$.C": "categorical"}
    rep = fidelity_report(real, real, kinds, column_limit=0.05, corr_limit=0.1)
    assert rep["score"] == 1.0 and rep["corr_delta"] == 0.0
    assert set(rep["ks"]) == {"$.N", "$.M"} and set(rep["tvd"]) == {"$.C"}
    assert all(c["within_limit"] for c in rep["columns"])
    assert rep["corr_matrix"]["columns"] == ["$.M", "$.N"]
    assert "alpha" not in json.dumps(rep)  # numbers and paths only
