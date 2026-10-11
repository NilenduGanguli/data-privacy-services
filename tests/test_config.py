from __future__ import annotations

import pytest

from dps.config import Settings


def test_defaults_are_local_and_safe() -> None:
    s = Settings(_env_file=None)
    assert s.env == "local" and s.port == 8400 and s.auth_enabled is True
    assert s.fpe_enabled is False and s.llm_provider == ""
    assert s.is_production is False
    # SEC-1: the COIN mint verifies TLS unless explicitly told otherwise.
    assert s.coin_tls_insecure is False and s.allow_insecure_coin_mint is False
    assert s.coin_ca_file == ""


def test_coin_tls_settings_read_their_env_names(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DPS_COIN_CA_FILE", "/etc/dps/coin-ca.pem")
    monkeypatch.setenv("DPS_COIN_TLS_INSECURE", "true")
    monkeypatch.setenv("DPS_ALLOW_INSECURE_COIN_MINT", "true")
    s = Settings(_env_file=None)
    assert s.coin_ca_file == "/etc/dps/coin-ca.pem"
    assert s.coin_tls_insecure is True and s.allow_insecure_coin_mint is True


def test_env_prefix_and_file_indirection(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    secret = tmp_path / "coin_secret"
    secret.write_text("c2VjcmV0\n", encoding="utf-8")
    monkeypatch.setenv("DPS_ENV", "dev")
    monkeypatch.setenv("DPS_COIN_CLIENT_SECRET_FILE", str(secret))
    s = Settings(_env_file=None)
    assert s.env == "dev"
    assert s.coin_client_secret == "c2VjcmV0"  # stripped, from the file


def test_explicit_env_var_beats_file(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    secret = tmp_path / "k"
    secret.write_text("from-file", encoding="utf-8")
    monkeypatch.setenv("DPS_COIN_SCOPE_FILE", str(secret))
    monkeypatch.setenv("DPS_COIN_SCOPE", "from-env")
    assert Settings(_env_file=None).coin_scope == "from-env"


@pytest.mark.parametrize(
    "field,value", [("env", "qa"), ("llm_provider", "openai"), ("stellar_base_url", "not a url")]
)
def test_invalid_values_are_refused(field: str, value: str) -> None:
    with pytest.raises(ValueError):
        Settings(_env_file=None, **{field: value})


def test_derived_properties() -> None:
    s = Settings(_env_file=None, env="prod", max_payload_mb=2, ner_languages="en, es")
    assert s.is_production and s.max_payload_bytes == 2 * 1024 * 1024
    assert s.ner_languages_list == ["en", "es"]


def test_validation_errors_never_echo_the_input() -> None:
    with pytest.raises(ValueError) as bad_url:
        Settings(_env_file=None, coin_url="ftp://user:s3cr3t-pw@coin.example")
    with pytest.raises(ValueError) as bad_int:
        Settings(_env_file=None, max_payload_mb="s3cr3t-pw")
    assert "s3cr3t-pw" not in str(bad_url.value) and "s3cr3t-pw" not in str(bad_int.value)


def test_m1_document_defaults() -> None:
    s = Settings(_env_file=None)
    assert (s.ocr_provider, s.max_document_mb, s.max_document_pages, s.pdf_render_dpi) == (
        "",
        20,
        200,
        200,
    )
    assert s.max_document_bytes == 20 * 1024 * 1024 and s.schema_max_rows == 10_000
    assert (
        s.ocr_configured is False
        and s.redact_image_format == "jpeg"
        and s.ocr_health_path == "/health"
    )
    on = Settings(
        _env_file=None,
        ocr_provider="AZURE",
        azure_vision_endpoint="http://ocr:5000",
        azure_vision_key="k",
    )
    assert on.ocr_provider == "azure" and on.ocr_configured is True


@pytest.mark.parametrize(
    "field,value",
    [
        ("ocr_provider", "paddle"),
        ("redact_image_format", "tiff"),
        ("pdf_render_dpi", "30"),
        ("ocr_health_path", "health"),
        ("azure_vision_endpoint", "ftp://ocr"),
        ("max_document_mb", "0"),
    ],
)
def test_m1_settings_refuse_bad_values(field: str, value: str) -> None:
    with pytest.raises(ValueError):
        Settings(_env_file=None, **{field: value})


def test_bad_ocr_endpoint_is_not_echoed() -> None:
    with pytest.raises(ValueError) as exc:
        Settings(_env_file=None, azure_vision_endpoint="ftp://user:s3cret-pw@ocr")
    assert "s3cret-pw" not in str(exc.value)
