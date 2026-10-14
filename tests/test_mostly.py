"""M5 Task 7: the optional MOSTLY AI pool engine (``synth-ml`` extra; never installed by default).

Offline with an injected trainer (what pandas hands back: floats for decimals, ISO strings for
dates, NaN for nulls). The ``ml`` test runs the real SDK and skips without the extra.
"""

from __future__ import annotations

import base64
import builtins
import math
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import pytest

pytest.importorskip("scipy")

import dps.engine.synth.mostly as mostly_module  # noqa: E402
from dps.engine.synth.mostly import (  # noqa: E402
    MostlySampler,
    TabularUnavailable,
    _sdk_trainer,
    mostly_available,
)

SEED = bytes(range(32))
COLS: dict[str, list[Any]] = {
    "$.BALANCE": [Decimal(f"{i}.25") for i in range(300)],
    "$.SEGMENT": ["A", "B", "C"] * 100,
    "$.OPENED": [date(2020, 1, 1 + i % 28) for i in range(300)],
}
KINDS = {"$.BALANCE": "decimal", "$.SEGMENT": "categorical", "$.OPENED": "date"}


def fake_trainer(
    data: dict[str, list[Any]], size: int, minutes: int, dp: Any = None
) -> list[dict[str, Any]]:
    # what pandas hands back: floats for decimals, ISO strings/Timestamps for dates, NaN for nulls
    return [
        {"BALANCE": float(i) + 0.125, "SEGMENT": "B", "OPENED": f"2021-06-0{1 + i % 9}"}
        for i in range(size)
    ]


def test_pool_sampler_is_deterministic_and_typed() -> None:
    s = MostlySampler.fit(
        COLS,
        KINDS,
        seed=SEED,
        pool_rows=50,
        max_training_minutes=1,
        scales={"$.BALANCE": 2},
        trainer=fake_trainer,
    )
    rows = s.sample_rows(["a", "b", "c"])
    assert rows == s.sample_rows(["a", "b", "c"]) and s.columns == ["BALANCE", "OPENED", "SEGMENT"]
    assert isinstance(rows[0]["BALANCE"], Decimal) and rows[0]["BALANCE"].as_tuple().exponent == -2
    assert isinstance(rows[0]["OPENED"], date) and rows[0]["SEGMENT"] == "B" and s.pool_rows == 50
    assert len({r["BALANCE"] for r in s.sample_rows([f"k{i}" for i in range(200)])}) > 10
    assert s.sample_row("a") == rows[0] and s.engine == "mostly"
    assert s.sample_rows(["a"], attempt=1) != s.sample_rows(["a"]) or s.pool_rows == 1


def test_round_trip_and_reject_set() -> None:
    real = [
        {
            "BALANCE": COLS["$.BALANCE"][i],
            "SEGMENT": COLS["$.SEGMENT"][i],
            "OPENED": COLS["$.OPENED"][i],
        }
        for i in range(300)
    ]
    s = MostlySampler.fit(
        COLS,
        KINDS,
        seed=SEED,
        pool_rows=20,
        max_training_minutes=1,
        trainer=fake_trainer,
        reject_rows=real,
        reject_columns=["BALANCE", "OPENED", "SEGMENT"],
    )
    again = MostlySampler.from_bytes(s.to_bytes(), SEED)
    assert again.sample_rows(["x", "y"]) == s.sample_rows(["x", "y"]) and again.is_rejected(real[4])
    assert not again.is_rejected(again.sample_row("x"))
    # another seed reads the same pool but picks other rows and keys the reject set differently
    other = MostlySampler.from_bytes(s.to_bytes(), bytes(32))
    assert not other.is_rejected(real[4])


def test_values_are_coerced_to_their_column_kind() -> None:
    kinds = {
        "$.N": "int",
        "$.F": "float",
        "$.B": "bool",
        "$.T": "timestamp",
        "$.D": "decimal",
        "$.C": "categorical",
    }

    def trainer(
        data: dict[str, list[Any]], size: int, minutes: int, dp: Any = None
    ) -> list[dict[str, Any]]:
        return [
            {
                "N": 4.6,
                "F": "2.5",
                "B": "True",
                "T": "2021-06-01T10:20:30+00:00",
                "D": math.nan,
                "C": 7,
            },
            {"N": None, "F": math.nan, "B": 0, "T": "NaT", "D": "1.005", "C": None},
        ][:size]

    cols = {p: [None] * 60 for p in kinds}
    s = MostlySampler.fit(
        cols,
        kinds,
        seed=SEED,
        pool_rows=2,
        max_training_minutes=1,
        scales={"$.D": 1},
        trainer=trainer,
    )
    first, second = s._pool  # noqa: SLF001 - the coerced pool itself
    assert dict(zip(s.columns, first, strict=True)) == {
        "B": True,
        "C": "7",
        "D": None,
        "F": 2.5,
        "N": 5,
        "T": datetime.fromisoformat("2021-06-01T10:20:30+00:00"),
    }
    assert dict(zip(s.columns, second, strict=True)) == {
        "B": False,
        "C": None,
        "D": Decimal("1.0"),
        "F": None,
        "N": None,
        "T": None,
    }


def test_bad_generator_output_and_blobs_fail_without_values() -> None:
    def garbage(
        data: dict[str, list[Any]], size: int, minutes: int, dp: Any = None
    ) -> list[dict[str, Any]]:
        return [{"OPENED": "not-a-date-4111111111111111"}]

    with pytest.raises(TabularUnavailable) as err:
        MostlySampler.fit(
            {"$.OPENED": COLS["$.OPENED"]},
            {"$.OPENED": "date"},
            seed=SEED,
            pool_rows=1,
            max_training_minutes=1,
            trainer=garbage,
        )
    assert "4111" not in str(err.value)
    with pytest.raises(TabularUnavailable):
        MostlySampler.fit(
            COLS,
            KINDS,
            seed=SEED,
            pool_rows=5,
            max_training_minutes=1,
            trainer=lambda d, s, m, dp: [],
        )
    for blob in (b"", b"not zlib", base64.b64encode(b"x")):
        with pytest.raises(TabularUnavailable):
            MostlySampler.from_bytes(blob, SEED)


def test_missing_sdk_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    real_import = builtins.__import__

    def deny(name: str, *a: Any, **k: Any) -> Any:
        if name.startswith("mostlyai"):
            raise ImportError(name)
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", deny)
    monkeypatch.setattr(mostly_module, "_find_spec", lambda name: None)
    assert mostly_available() is False
    with pytest.raises(TabularUnavailable):
        _sdk_trainer({"A": [1, 2]}, 10, 1)


def _fake_sdk(monkeypatch: pytest.MonkeyPatch, generated: Any) -> None:
    """Install a stand-in ``mostlyai.sdk`` whose generator hands back ``generated``."""
    import sys
    import types

    class _Dataset:
        def data(self) -> Any:
            return generated

    class _MostlyAI:
        def __init__(self, **_: Any) -> None:
            pass

        def train(self, **_: Any) -> object:
            return object()

        def generate(self, *_: Any, **__: Any) -> _Dataset:
            return _Dataset()

    package = types.ModuleType("mostlyai")
    sdk = types.ModuleType("mostlyai.sdk")
    sdk.MostlyAI = _MostlyAI  # type: ignore[attr-defined]
    package.sdk = sdk  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "mostlyai", package)
    monkeypatch.setitem(sys.modules, "mostlyai.sdk", sdk)


def test_sdk_output_is_narrowed_to_one_frame(monkeypatch: pytest.MonkeyPatch) -> None:
    pd = pytest.importorskip("pandas")
    values = [734251, 918273]
    frame = pd.DataFrame({"A": values})
    expected = [{"A": v} for v in values]
    _fake_sdk(monkeypatch, frame)
    assert _sdk_trainer({"A": values}, 2, 1) == expected
    _fake_sdk(monkeypatch, {"rows": frame})  # the SDK's per-table dict form
    assert _sdk_trainer({"A": values}, 2, 1) == expected
    for unusable in ({"other": frame}, [{"A": values[0]}], None):
        _fake_sdk(monkeypatch, unusable)
        with pytest.raises(TabularUnavailable) as err:  # fails closed
            _sdk_trainer({"A": values}, 2, 1)
        assert all(str(v) not in str(err.value) for v in values)  # no values in the error


@pytest.mark.ml
def test_real_sdk_trains_and_generates() -> None:
    pytest.importorskip("mostlyai")
    pytest.importorskip("pandas")
    data = {"BALANCE": [float(i % 97) for i in range(400)], "SEGMENT": ["A", "B"] * 200}
    rows = _sdk_trainer(data, 25, 1)
    assert len(rows) == 25 and set(rows[0]) == {"BALANCE", "SEGMENT"}


async def test_prepare_with_the_mostly_engine_records_the_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import numpy as np

    from dps.layers.connectors.tabular import load_sampler
    from tests.test_tabular_prepare import _prepare, _rows

    def trainer(
        data: dict[str, list[Any]], size: int, minutes: int, dp: Any = None
    ) -> list[dict[str, Any]]:
        rng = np.random.default_rng(size)
        balances = rng.lognormal(8, 1, size)
        return [
            {"BALANCE": float(b), "SEGMENT": "AFFLUENT" if b > 3000 else "MASS"} for b in balances
        ]

    monkeypatch.setattr(mostly_module, "_sdk_trainer", trainer)
    dp = mostly_module.DpConfig(max_epsilon=3.0, delta=1e-6)
    prepared, seed = await _prepare(
        _rows(600),
        engine_name="mostly",
        synth_tabular_engine="copula,mostly",
        mostly_pool_rows=200,
        dp=dp,
    )
    m = prepared.metrics
    assert m["tabular"]["dp"] == {
        "enabled": True,
        "max_epsilon": 3.0,
        "delta": 1e-6,
        "achieved_epsilon": None,
    }
    assert prepared.engine == "mostly" and m["tabular"]["engine"] == "mostly"
    assert m["tabular"]["pool_rows"] == 200 and m["privacy"]["exact_row_matches"] == 0
    assert m["fidelity"]["engine"] == "mostly" and m["privacy"]["membership_inference"]["run"]
    from dps.engine.synth.surrogates import seed_bytes

    sampler = load_sampler("mostly", prepared.model_b64, seed_bytes(seed))
    assert isinstance(sampler, MostlySampler) and sampler.columns == ["BALANCE", "SEGMENT"]


def test_validation_needs_the_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    from dps.config import Settings
    from tests.connectors.test_targets import UAT_TABLE, _check, _code, spec

    settings = Settings(
        _env_file=None,
        env="local",
        auth_enabled=False,
        llm_provider="stub",
        synth_tabular_engine="copula,mostly",
    )
    run = spec(**UAT_TABLE, mode="synthesize", options={"tabular": "mostly"})
    monkeypatch.setattr(mostly_module, "_find_spec", lambda name: None)
    assert _code(run, settings) == ("tabular_invalid", 422)
    monkeypatch.setattr(mostly_module, "_find_spec", lambda name: object())
    _check(run, settings=settings)
