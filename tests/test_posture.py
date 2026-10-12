from __future__ import annotations

import pytest

from dps.config import Settings
from dps.posture import PostureError, assert_production_posture, posture_problems


def _prod(**kw):  # noqa: ANN003 - test helper
    base = dict(
        _env_file=None,
        env="prod",
        auth_enabled=True,
        api_keys='[{"name":"k","key_sha256":"ab"}]',
        llm_provider="",
        audit_enabled=True,
    )
    base.update(kw)
    return Settings(**base)


def test_local_is_never_checked() -> None:
    assert_production_posture(Settings(_env_file=None, env="local", auth_enabled=False))


def test_good_production_passes() -> None:
    assert_production_posture(_prod())


@pytest.mark.parametrize(
    "bad",
    [
        dict(auth_enabled=False),
        dict(bootstrap_api_key="x"),
        dict(api_keys=""),
        dict(llm_provider="stub"),
        dict(
            llm_provider="stellar",
            stellar_base_url="https://s",
            coin_url="https://c",
            ssl_cert_file="",
        ),
        # Spec §10: TLS-only egress in staging/prod, even with a CA bundle configured.
        dict(
            llm_provider="stellar",
            stellar_base_url="http://s",
            coin_url="https://c",
            ssl_cert_file="/etc/ca.pem",
        ),
        dict(
            llm_provider="stellar",
            stellar_base_url="https://s",
            coin_url="http://c",
            ssl_cert_file="/etc/ca.pem",
        ),
        dict(audit_enabled=False),
        # SEC-1: an unverified COIN mint needs the documented VDI exception as well.
        dict(coin_tls_insecure=True),
    ],
)
def test_bad_production_is_refused(bad: dict) -> None:
    with pytest.raises(PostureError):
        assert_production_posture(_prod(**bad))


def test_good_stellar_production_passes() -> None:
    assert_production_posture(
        _prod(
            llm_provider="stellar",
            stellar_base_url="https://stellar.internal/v1",
            coin_url="HTTPS://coin.internal/token",
            ssl_cert_file="/etc/ca.pem",
        )
    )


@pytest.mark.parametrize("provider", ["", "vertex", "stellar"])
def test_plain_http_egress_names_the_setting_never_the_url(provider: str) -> None:
    secret_ish = "http://svc-user:pa55word@egress.internal/v1"
    problems = posture_problems(
        _prod(
            llm_provider=provider,
            stellar_base_url=secret_ish,
            coin_url=secret_ish,
            ssl_cert_file="/etc/ca.pem",
        )
    )
    assert "DPS_STELLAR_BASE_URL must be https in staging/prod" in problems
    assert "DPS_COIN_URL must be https in staging/prod" in problems
    joined = "; ".join(problems)
    for fragment in ("pa55word", "svc-user", "egress.internal", "http://"):
        assert fragment not in joined


def test_plain_http_egress_is_allowed_outside_production() -> None:
    settings = Settings(
        _env_file=None,
        env="local",
        stellar_base_url="http://localhost:8080/v1",
        coin_url="http://localhost:8081/token",
    )
    assert posture_problems(settings) == []


_GOOD_STELLAR = dict(
    llm_provider="stellar",
    stellar_base_url="https://stellar.internal/v1",
    coin_url="https://coin.internal/token",
    ssl_cert_file="/etc/ca.pem",
)


@pytest.mark.parametrize("env", ["staging", "prod"])
def test_insecure_coin_mint_is_refused_in_production_without_the_exception(env: str) -> None:
    problems = posture_problems(_prod(env=env, coin_tls_insecure=True, **_GOOD_STELLAR))
    assert problems == [
        "DPS_COIN_TLS_INSECURE=true requires DPS_ALLOW_INSECURE_COIN_MINT=true in staging/prod "
        "(the documented VDI exception)"
    ]
    # The exception flag alone changes nothing, and the two together pass.
    assert posture_problems(_prod(env=env, allow_insecure_coin_mint=True, **_GOOD_STELLAR)) == []
    assert_production_posture(
        _prod(env=env, coin_tls_insecure=True, allow_insecure_coin_mint=True, **_GOOD_STELLAR)
    )


def test_insecure_coin_mint_is_allowed_outside_production() -> None:
    for env in ("local", "dev"):
        settings = Settings(_env_file=None, env=env, coin_tls_insecure=True, **_GOOD_STELLAR)
        assert posture_problems(settings) == []


def test_azure_ocr_needs_key_and_https_in_production() -> None:
    problems = posture_problems(_prod(ocr_provider="azure", azure_vision_endpoint="http://gw:8080"))
    assert any("DPS_AZURE_VISION_KEY" in p for p in problems)
    assert any("https" in p for p in problems)
    ok = posture_problems(
        _prod(
            ocr_provider="azure",
            azure_vision_endpoint="https://gw.bank.internal",
            azure_vision_key="k",
        )
    )
    assert not any("AZURE" in p for p in ok)


def test_local_tesseract_is_allowed_and_auto_must_resolve(monkeypatch) -> None:  # noqa: ANN001
    import dps.config as config

    monkeypatch.setattr(config.shutil, "which", lambda cmd: None)
    nothing = posture_problems(_prod(ocr_provider="auto"))
    assert any("OCR is enabled (auto)" in p for p in nothing)
    assert not any("OCR" in p for p in posture_problems(_prod(ocr_provider="tesseract")))
    plain = _prod(ocr_provider="auto", azure_vision_endpoint="http://gw:8080", azure_vision_key="k")
    assert any("DPS_AZURE_VISION_ENDPOINT" in p for p in posture_problems(plain))
    monkeypatch.setattr(config.shutil, "which", lambda cmd: "/usr/bin/tesseract")
    assert not any("OCR" in p for p in posture_problems(_prod(ocr_provider="auto")))
