"""The production posture of the browser-worker role (M4 × M5, spec §10).

``python -m dps.browser_worker`` refuses to boot in staging/prod under every M4 and M5 rule that
concerns what it runs — secrets as mounted files, the job KEK, TLS-only egress, strict verification,
audit, RLS and migrations, a headless browser and no unmasked originals — before it touches the
database or Chromium. It is never refused for a component it does not run and whose secret it never
mounts: the HTTP API (API keys, CORS), documents OCR and the external tokenizer (a scan makes its
tokens with the scan key only).
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import pytest

from dps.config import Settings
from dps.posture import SECRET_ENV, PostureError, assert_production_posture, posture_problems

KEK = base64.b64encode(b"b" * 32).decode()
ROLE = "browser-worker"


@pytest.fixture
def mounted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The secrets a browser-worker mounts: the job KEK only (no API keys, OCR key or token)."""
    for name in SECRET_ENV:  # a literal secret in the test runner's environment must not count
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(f"{name}_FILE", raising=False)
    kek = tmp_path / "kek"
    kek.write_text(KEK, encoding="utf-8")
    monkeypatch.setenv("DPS_JOB_KEK_FILE", str(kek))


def _prod(**kw: Any) -> Settings:
    """A production browser-worker sharing the app's configuration (OCR on Azure, the tokenizer
    enabled, an explicit CORS list) without the app's secrets."""
    base: dict[str, Any] = dict(
        _env_file=None,
        env="prod",
        auth_enabled=True,
        llm_provider="",
        audit_enabled=True,
        audit_strict=True,
        egress_tls_required=True,
        cors_origins="https://console.bank.example",
        ssl_cert_file="/etc/ssl/org-ca.pem",
        ocr_provider="azure",
        azure_vision_endpoint="https://ocr.bank.example",
        tokenizer_provider="http",
        tokenizer_url="https://tokenizer.bank.example",
        migrations_mode="verify",
        inline_secrets_allowed=False,
        rls_enabled=True,
        pg_host="db.bank.example",
        pg_ssl_root_cert="/etc/ssl/pg.pem",
        verify_warn_allowed=False,
        llm_egress_check="strict",
        browser_headless=True,
        scan_store_unmasked_originals=False,
        browser_metrics_port=0,
    )
    base.update(kw)
    return Settings(**base)


def test_the_browser_worker_needs_no_api_ocr_or_tokenizer_secret(mounted: None) -> None:
    settings = _prod()
    assert posture_problems(settings, role=ROLE) == []
    assert_production_posture(settings, role=ROLE)
    app = " ".join(posture_problems(settings))  # the app's own rules are unchanged
    for name in ("DPS_API_KEYS", "DPS_AZURE_VISION_KEY", "DPS_TOKENIZER_TOKEN_FILE"):
        assert name in app
    assert posture_problems(_prod(cors_origins="*"), role=ROLE) == []
    assert "DPS_CORS_ORIGINS" in " ".join(posture_problems(_prod(cors_origins="*")))


@pytest.mark.parametrize(
    "change,needle",
    [
        ({"browser_headless": False}, "DPS_BROWSER_HEADLESS"),
        ({"scan_store_unmasked_originals": True}, "DPS_SCAN_STORE_UNMASKED_ORIGINALS"),
        ({"egress_tls_required": False}, "DPS_EGRESS_TLS_REQUIRED"),
        ({"ssl_cert_file": ""}, "DPS_SSL_CERT_FILE"),
        ({"verify_warn_allowed": True}, "DPS_VERIFY_WARN_ALLOWED"),
        ({"llm_egress_check": "warn"}, "DPS_LLM_EGRESS_CHECK"),
        ({"job_kek": ""}, "DPS_JOB_KEK"),
        ({"inline_secrets_allowed": True}, "DPS_INLINE_SECRETS_ALLOWED"),
        ({"rls_enabled": False}, "DPS_RLS_ENABLED"),
        ({"migrations_mode": "auto"}, "DPS_MIGRATIONS_MODE"),
        ({"audit_strict": False}, "DPS_AUDIT_STRICT"),
        ({"pg_ssl_root_cert": ""}, "DPS_PG_SSL_ROOT_CERT"),
        ({"auth_enabled": False}, "DPS_AUTH_ENABLED"),
        ({"bootstrap_api_key": "dps_local_dev_key"}, "DPS_BOOTSTRAP_API_KEY"),
        ({"tokenizer_url": "http://tokenizer.bank.example"}, "DPS_TOKENIZER_URL"),
    ],
)
def test_the_browser_worker_refuses_an_unsafe_production(
    mounted: None, change: dict[str, Any], needle: str
) -> None:
    with pytest.raises(PostureError) as err:
        assert_production_posture(_prod(**change), role=ROLE)
    assert needle in str(err.value)
    assert KEK not in str(err.value) and "bank.example" not in str(err.value)


def test_a_literal_secret_env_var_refuses_the_browser_worker(
    mounted: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DPS_PG_WORKER_PASSWORD", "pw-sentinel-4242")
    with pytest.raises(PostureError) as err:
        assert_production_posture(_prod(), role=ROLE)
    assert "DPS_PG_WORKER_PASSWORD" in str(err.value) and "pw-sentinel-4242" not in str(err.value)


def test_the_process_checks_its_role_before_the_database_or_chromium(
    mounted: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``amain`` runs the browser-worker posture first: a violation stops it before anything is
    opened; a production configuration without the app's secrets gets past it (to the database)."""
    import asyncio

    import dps.browser_worker as bw

    class Reached(Exception):
        pass

    async def no_db(*_: Any, **__: Any) -> None:
        raise Reached

    monkeypatch.setattr(bw, "init_pool", no_db)
    with pytest.raises(PostureError):
        asyncio.run(bw.amain(_prod(browser_headless=False)))
    with pytest.raises(Reached):
        asyncio.run(bw.amain(_prod()))
