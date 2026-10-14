"""Gaussian-copula engine (spec §5.6, contract §6.6): keyed deterministic sampling, marginals,
correlations, nulls, rare-category folding, tail clamping, the reject set and serialisation."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("scipy")

from dps.engine.metrics import correlation_delta, ks_statistic, total_variation  # noqa: E402
from dps.engine.synth.copula import (  # noqa: E402
    CopulaError,
    GaussianCopula,
    nearest_correlation,
    uniforms,
)

SEED = bytes(range(32))


def _data(n: int = 3000) -> tuple[dict[str, list], dict[str, str]]:
    rng = np.random.default_rng(11)
    balance = rng.lognormal(mean=9, sigma=1.0, size=n)
    limit = balance * 1.5 + rng.normal(scale=2000, size=n)
    seg = np.where(balance > np.median(balance), "AFFLUENT", "MASS")
    opened = [date(2015, 1, 1) + timedelta(days=int(d)) for d in rng.integers(0, 3000, size=n)]
    rare = ["RARE-" + str(i) if i < 3 else "COMMON" for i in range(n)]
    cols = {
        "$.BALANCE": [Decimal(f"{b:.2f}") for b in balance],
        "$.CREDIT_LIMIT": [None if i % 20 == 0 else int(v) for i, v in enumerate(limit)],
        "$.SEGMENT": seg.tolist(),
        "$.OPENED": opened,
        "$.ACTIVE": [bool(b > 5000) for b in balance],
        "$.UPDATED_AT": [
            datetime(2026, 1, 1) + timedelta(seconds=int(s)) for s in rng.integers(0, 10**7, n)
        ],
        "$.TAG": rare,
    }
    kinds = {
        "$.BALANCE": "decimal",
        "$.CREDIT_LIMIT": "int",
        "$.SEGMENT": "categorical",
        "$.OPENED": "date",
        "$.ACTIVE": "bool",
        "$.UPDATED_AT": "timestamp",
        "$.TAG": "categorical",
    }
    return cols, kinds


@pytest.fixture(scope="module")
def model() -> GaussianCopula:
    cols, kinds = _data()
    return GaussianCopula.fit(cols, kinds, rare_floor=0.01, seed=SEED, scales={"$.BALANCE": 2})


def test_uniform_stream_is_keyed_and_in_open_interval() -> None:
    u = uniforms(SEED, "row:a", 20)
    assert u.shape == (20,) and np.all((u > 0) & (u < 1))
    assert np.array_equal(u, uniforms(SEED, "row:a", 20))
    assert not np.array_equal(u, uniforms(SEED, "row:b", 20))
    assert not np.array_equal(u, uniforms(bytes(32), "row:a", 20))
    assert np.array_equal(uniforms(SEED, "row:a", 5), u[:5])  # a prefix of the same stream


def test_nearest_correlation_repairs_an_indefinite_matrix() -> None:
    bad = np.array([[1.0, 0.9, -0.9], [0.9, 1.0, 0.9], [-0.9, 0.9, 1.0]])
    fixed = nearest_correlation(bad)
    assert np.allclose(np.diag(fixed), 1.0) and np.linalg.eigvalsh(fixed).min() > 0
    np.linalg.cholesky(fixed)


def test_rows_are_typed_deterministic_and_cover_modelled_columns(model: GaussianCopula) -> None:
    rows = model.sample_rows([f"row:{i}" for i in range(5)])
    assert rows == model.sample_rows([f"row:{i}" for i in range(5)])
    assert rows[0] == model.sample_row("row:0")
    assert set(model.columns) == {
        "BALANCE",
        "CREDIT_LIMIT",
        "SEGMENT",
        "OPENED",
        "ACTIVE",
        "UPDATED_AT",
        "TAG",
    }
    r = rows[0]
    assert isinstance(r["BALANCE"], Decimal) and r["BALANCE"].as_tuple().exponent == -2
    assert r["CREDIT_LIMIT"] is None or isinstance(r["CREDIT_LIMIT"], int)
    assert isinstance(r["OPENED"], date) and isinstance(r["ACTIVE"], bool)
    assert isinstance(r["UPDATED_AT"], datetime) and r["UPDATED_AT"].tzinfo is None
    assert model.sample_rows(["row:0"], attempt=1)[0] != r
    assert model.engine == "copula" and model.fit_rows == 3000 and model.sample_rows([]) == []


def test_rare_categories_are_never_emitted(model: GaussianCopula) -> None:
    tags = {r["TAG"] for r in model.sample_rows([f"t:{i}" for i in range(2000)])}
    assert tags == {"COMMON"}


def test_marginals_correlations_and_nulls_are_preserved(model: GaussianCopula) -> None:
    cols, _ = _data()
    synth = model.sample_rows([f"s:{i}" for i in range(5000)])
    s = {k: [r[k[2:]] for r in synth] for k in ("$.BALANCE", "$.CREDIT_LIMIT", "$.SEGMENT")}
    assert ks_statistic(cols["$.BALANCE"], s["$.BALANCE"]) < 0.06
    assert total_variation(cols["$.SEGMENT"], s["$.SEGMENT"]) < 0.05
    pair = ("$.BALANCE", "$.CREDIT_LIMIT")
    assert correlation_delta({k: cols[k] for k in pair}, {k: s[k] for k in pair}) < 0.05
    null_rate = sum(v is None for v in s["$.CREDIT_LIMIT"]) / 5000
    assert abs(null_rate - 0.05) < 0.02


def test_tails_are_clamped(model: GaussianCopula) -> None:
    cols, _ = _data()
    real_max = max(cols["$.BALANCE"])
    synth_max = max(r["BALANCE"] for r in model.sample_rows([f"m:{i}" for i in range(5000)]))
    assert synth_max < real_max


def test_timezone_aware_timestamps_come_back_aware() -> None:
    stamps = [datetime(2026, 1, 1, tzinfo=UTC) + timedelta(hours=i) for i in range(60)]
    m = GaussianCopula.fit({"$.TS": stamps}, {"$.TS": "timestamp"}, rare_floor=0.01, seed=SEED)
    value = m.sample_row("a")["TS"]
    assert value.tzinfo is not None and stamps[0] <= value <= stamps[-1]


def test_reject_set_and_digest() -> None:
    cols, kinds = _data(200)
    real_rows = [{k[2:]: cols[k][i] for k in cols} for i in range(200)]
    m = GaussianCopula.fit(
        cols,
        kinds,
        rare_floor=0.01,
        seed=SEED,
        reject_rows=real_rows,
        reject_columns=sorted(real_rows[0]),
    )
    assert m.reject_size == 200 and m.is_rejected(real_rows[7])
    synthetic = m.sample_row("x")
    assert not m.is_rejected(synthetic)
    assert m.is_rejected(synthetic, extra=frozenset({m.row_digest(synthetic)}))


def test_serialisation_round_trip_needs_the_seed(model: GaussianCopula) -> None:
    blob = model.to_bytes()
    again = GaussianCopula.from_bytes(blob, SEED)
    assert again.sample_rows(["row:1", "row:2"]) == model.sample_rows(["row:1", "row:2"])
    other = GaussianCopula.from_bytes(blob, bytes(32))
    assert again.sample_rows(["row:1"]) != other.sample_rows(["row:1"])
    assert again.excluded == model.excluded and len(blob) < 200_000  # sealed by the caller
    assert np.allclose(again.correlation, model.correlation) and again.columns == model.columns
    for junk in (b"not a model", b"", blob[:100]):
        with pytest.raises(CopulaError):
            GaussianCopula.from_bytes(junk, SEED)


def test_serialisation_keeps_the_reject_set() -> None:
    cols, kinds = _data(120)
    rows = [{k[2:]: cols[k][i] for k in cols} for i in range(120)]
    m = GaussianCopula.fit(
        cols, kinds, rare_floor=0.01, seed=SEED, reject_rows=rows, reject_columns=sorted(rows[0])
    )
    again = GaussianCopula.from_bytes(m.to_bytes(), SEED)
    assert again.reject_size == 120 and again.is_rejected(rows[3])
    assert again.reject_columns == sorted(rows[0])


def test_fit_errors_name_columns_not_values() -> None:
    with pytest.raises(CopulaError, match="at least 50 rows"):
        GaussianCopula.fit({"$.A": [1, 2]}, {"$.A": "int"}, rare_floor=0.01, seed=SEED)
    only_rare = {"$.CODE": [f"C{i}" for i in range(100)]}
    with pytest.raises(CopulaError, match="no column") as err:
        GaussianCopula.fit(only_rare, {"$.CODE": "categorical"}, rare_floor=0.01, seed=SEED)
    assert "C1" not in str(err.value)
    m = GaussianCopula.fit(
        {**only_rare, "$.N": list(range(100))},
        {"$.CODE": "categorical", "$.N": "int"},
        rare_floor=0.01,
        seed=SEED,
    )
    assert m.excluded == {"$.CODE": "no_frequent_category"} and m.columns == ["N"]
    with pytest.raises(CopulaError, match=r"\$\.N"):
        GaussianCopula.fit({"$.N": list(range(60))}, {"$.N": "text"}, rare_floor=0.01, seed=SEED)
    with pytest.raises(CopulaError, match="same number of rows"):
        GaussianCopula.fit(
            {"$.A": list(range(60)), "$.B": list(range(61))},
            {"$.A": "int", "$.B": "int"},
            rare_floor=0.01,
            seed=SEED,
        )
    with pytest.raises(CopulaError, match="no kind"):
        GaussianCopula.fit({"$.A": list(range(60))}, {}, rare_floor=0.01, seed=SEED)
