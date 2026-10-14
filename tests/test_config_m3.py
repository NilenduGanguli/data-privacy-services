from __future__ import annotations

import pytest

from dps.config import Settings


def test_m3_defaults() -> None:
    s = Settings(_env_file=None)
    assert (s.oracle_pool_max, s.oracle_call_timeout_ms, s.oracle_module) == (4, 30000, "dps")
    assert s.mongo_server_selection_timeout_ms == 5000


@pytest.mark.parametrize(
    "field,value",
    [
        ("oracle_pool_max", 0),
        ("oracle_pool_max", 65),
        ("oracle_call_timeout_ms", 999),
        ("oracle_module", "dps module"),
        ("oracle_module", "x" * 49),
        ("mongo_server_selection_timeout_ms", 100),
    ],
)
def test_m3_bad_values_are_refused_without_echo(field: str, value: object) -> None:
    with pytest.raises(ValueError) as info:
        Settings(_env_file=None, **{field: value})
    assert repr(value) not in str(info.value)


def test_m3_env_names(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DPS_ORACLE_POOL_MAX", "2")
    monkeypatch.setenv("DPS_ORACLE_MODULE", "dps-uat")
    s = Settings(_env_file=None)
    assert s.oracle_pool_max == 2 and s.oracle_module == "dps-uat"
