"""M5 settings (contract §10): defaults, the tabular engine list, validation, CORS origins."""

from __future__ import annotations

import pytest

from dps.config import Settings


def test_m5_defaults() -> None:
    s = Settings(_env_file=None)
    assert s.synth_tabular_engine == "copula" and s.tabular_engines == ["copula"]
    assert (s.copula_fit_rows, s.copula_holdout_rows, s.copula_metrics_rows) == (
        100_000,
        20_000,
        5_000,
    )
    assert s.copula_rare_floor == 0.01 and s.copula_max_resample == 8
    assert s.privacy_nndr_target == 0.95 and s.privacy_nndr_enforce is False
    assert s.fidelity_column_limit == 0.05 and s.fidelity_corr_limit == 0.10
    assert s.key_cache_seconds == 30 and s.report_pdf_enabled is True
    assert s.key_max_ttl_days == 365 and s.key_rotation_max_age_days == 90
    assert s.tokenizer_provider == "" and s.tokenizer_batch == 500
    assert s.egress_tls_required is False and s.cors_origins == "*"
    assert s.mostly_pool_rows == 20_000 and s.rollup_retention_days == 400
    # posture: false / strict in staging and prod (Task 16)
    assert s.verify_warn_allowed is True and s.llm_egress_check == "strict"
    assert (s.utility_max_rows, s.mia_min_holdout, s.mia_auc_target) == (5_000, 50, 0.60)
    assert s.mostly_dp_enabled is False
    assert (s.mostly_dp_max_epsilon, s.mostly_dp_delta) == (5.0, 1e-5)


@pytest.mark.parametrize(
    "value,expected",
    [("copula", ["copula"]), ("copula,mostly", ["copula", "mostly"]), ("", [])],
)
def test_tabular_engine_list(value: str, expected: list[str]) -> None:
    assert Settings(_env_file=None, synth_tabular_engine=value).tabular_engines == expected


@pytest.mark.parametrize(
    "field,value",
    [
        ("synth_tabular_engine", "ctgan"),
        ("tokenizer_provider", "voltage"),
        ("tokenizer_url", "ftp://x"),
        ("copula_rare_floor", 0.5),
        ("copula_max_resample", 0),
        ("llm_egress_check", "off"),
        ("mostly_dp_max_epsilon", 0.0),
    ],
)
def test_m5_settings_are_validated(field: str, value: object) -> None:
    with pytest.raises(ValueError):
        Settings(_env_file=None, **{field: value})


def test_cors_origin_list() -> None:
    assert Settings(_env_file=None, cors_origins="").cors_origin_list == []
    assert Settings(
        _env_file=None, cors_origins="https://a.example, https://b.example"
    ).cors_origin_list == ["https://a.example", "https://b.example"]


def test_url_errors_never_echo_the_url() -> None:
    with pytest.raises(ValueError) as err:
        Settings(_env_file=None, tokenizer_url="ftp://user:pa55word@vault.example")
    assert "pa55word" not in str(err.value)
