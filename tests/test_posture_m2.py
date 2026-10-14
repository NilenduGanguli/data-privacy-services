from __future__ import annotations

import base64

import pytest

from dps.config import Settings
from dps.posture import PostureError, assert_production_posture, posture_problems

KEK = base64.b64encode(bytes(range(32))).decode()


def _prod(**kw):  # noqa: ANN003, ANN202 - test helper
    base = dict(
        _env_file=None,
        env="prod",
        auth_enabled=True,
        api_keys='[{"name":"k","key_sha256":"ab"}]',
        llm_provider="",
        audit_enabled=True,
        # M5 (spec §10): TLS-only egress, explicit CORS origins, strict verification
        egress_tls_required=True,
        cors_origins="https://console.bank.example",
        verify_warn_allowed=False,
        pg_host="db.internal",
        job_kek=KEK,
        inline_secrets_allowed=False,
        rls_enabled=True,
        migrations_mode="verify",
        audit_strict=True,
        pg_ssl_root_cert="/etc/ssl/pg-ca.pem",
        embedded_worker=False,
    )
    base.update(kw)
    return Settings(**base)


def test_good_m2_production_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DPS_JOB_KEK", raising=False)
    assert_production_posture(_prod())


def test_m2_checks_skip_without_a_database() -> None:
    assert (
        posture_problems(
            _prod(
                pg_host="",
                job_kek="",
                inline_secrets_allowed=True,
                migrations_mode="auto",
                audit_strict=False,
            )
        )
        == []
    )


@pytest.mark.parametrize(
    "bad,needle",
    [
        (dict(job_kek=""), "DPS_JOB_KEK_FILE"),
        (dict(inline_secrets_allowed=True), "INLINE_SECRETS"),
        (dict(rls_enabled=False), "RLS_ENABLED"),
        (dict(migrations_mode="auto"), "MIGRATIONS_MODE"),
        (dict(audit_strict=False), "AUDIT_STRICT"),
        (dict(pg_ssl_root_cert=""), "PG_SSL_ROOT_CERT"),
        (dict(embedded_worker=True), "EMBEDDED_WORKER"),
    ],
)
def test_bad_m2_production_is_refused(
    monkeypatch: pytest.MonkeyPatch, bad: dict, needle: str
) -> None:
    monkeypatch.delenv("DPS_JOB_KEK", raising=False)
    with pytest.raises(PostureError) as err:
        assert_production_posture(_prod(**bad))
    assert needle in str(err.value)


def test_literal_kek_env_var_is_refused_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DPS_JOB_KEK", KEK)
    problems = posture_problems(_prod())
    assert any("DPS_JOB_KEK_FILE" in p for p in problems)
    assert all(KEK not in p for p in problems)
