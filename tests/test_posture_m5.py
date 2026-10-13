"""M5 production posture (Task 16, spec §10): secrets from files, TLS-only egress, explicit CORS,
strict verification at boot and at request time, the runtime posture report, docs off in prod."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import pytest

from dps.config import Settings
from dps.posture import SECRET_ENV, PostureError, assert_production_posture, posture_problems

KEK = base64.b64encode(b"k" * 32).decode()
TOKEN = "tok-sentinel-998877"


def _files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name in SECRET_ENV:  # a literal secret in the test runner's environment must not count
        monkeypatch.delenv(name, raising=False)
    for name, value in {
        "DPS_JOB_KEK": KEK,
        "DPS_API_KEYS": '[{"name":"k","key_sha256":"'
        + "a" * 64
        + '","tenant_ids":["t1"],"scopes":["detect"]}]',
        "DPS_TOKENIZER_TOKEN": TOKEN,
    }.items():
        path = tmp_path / name
        path.write_text(value, encoding="utf-8")
        monkeypatch.setenv(f"{name}_FILE", str(path))


def _prod(**kw: Any) -> Settings:
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
        tokenizer_provider="http",
        tokenizer_url="https://tokenizer.bank.example",
        migrations_mode="verify",
        inline_secrets_allowed=False,
        rls_enabled=True,
        pg_host="db.bank.example",
        pg_ssl_root_cert="/etc/ssl/pg.pem",
        verify_warn_allowed=False,
        llm_egress_check="strict",
    )
    base.update(kw)
    return Settings(**base)


def test_good_m5_production_passes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _files(tmp_path, monkeypatch)
    assert posture_problems(_prod()) == []
    assert_production_posture(_prod())


@pytest.mark.parametrize(
    "change,needle",
    [
        ({"egress_tls_required": False}, "DPS_EGRESS_TLS_REQUIRED"),
        ({"tokenizer_url": "http://tokenizer.bank.example"}, "DPS_TOKENIZER_URL"),
        ({"cors_origins": "*"}, "DPS_CORS_ORIGINS"),
        ({"cors_origins": "https://a.example, *"}, "DPS_CORS_ORIGINS"),
        ({"embedded_worker": True}, "DPS_EMBEDDED_WORKER"),
        ({"audit_strict": False}, "DPS_AUDIT_STRICT"),
        ({"s3_endpoint_url": "http://minio:9000"}, "DPS_S3_ENDPOINT_URL"),
        ({"ssl_cert_file": ""}, "DPS_SSL_CERT_FILE"),
        ({"job_kek": ""}, "DPS_JOB_KEK"),
        ({"tokenizer_token": ""}, "DPS_TOKENIZER_TOKEN_FILE"),
        ({"verify_warn_allowed": True}, "DPS_VERIFY_WARN_ALLOWED"),
        ({"llm_egress_check": "warn"}, "DPS_LLM_EGRESS_CHECK"),
    ],
)
def test_bad_m5_production_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: dict[str, Any], needle: str
) -> None:
    _files(tmp_path, monkeypatch)
    with pytest.raises(PostureError) as err:
        assert_production_posture(_prod(**change))
    assert needle in str(err.value) and TOKEN not in str(err.value) and KEK not in str(err.value)
    assert "minio" not in str(err.value) and "tokenizer.bank" not in str(err.value)


def test_a_short_kek_never_gets_past_the_settings(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="32 bytes") as err:
        _prod(job_kek=base64.b64encode(b"short").decode())
    assert base64.b64encode(b"short").decode() not in str(err.value)


def test_boot_refuses_a_non_strict_preset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _files(tmp_path, monkeypatch)
    monkeypatch.setattr("dps.posture.non_strict_policies", lambda s: ["analytics-safe"])
    with pytest.raises(PostureError) as err:
        assert_production_posture(_prod())
    assert "policy analytics-safe must use verify.leakage_check=strict" in str(err.value)


def test_every_shipped_preset_is_strict() -> None:
    from dps.posture import non_strict_policies

    assert non_strict_policies(Settings(_env_file=None)) == []


def test_relaxed_verification_is_refused_at_request_time() -> None:
    from dps.engine.service import PrivacyEngine
    from dps.errors import CodedError

    engine = PrivacyEngine(Settings(_env_file=None, llm_provider="stub", verify_warn_allowed=False))
    with pytest.raises(CodedError) as err:
        engine.policy({"preset": "gdpr-pseudonymise", "verify": {"leakage_check": "warn"}})
    assert err.value.code == "verify_warn_disallowed" and err.value.status == 422
    engine.policy("gdpr-pseudonymise")  # strict policies are unaffected
    local = PrivacyEngine(Settings(_env_file=None, llm_provider="stub"))
    local.policy({"preset": "gdpr-pseudonymise", "verify": {"leakage_check": "warn"}})


def test_relaxed_verification_is_refused_over_http_and_for_runs() -> None:
    from fastapi.testclient import TestClient

    from dps.app import create_app
    from dps.jobs_models import SpecError
    from tests.connectors.test_targets import UAT_TABLE, _check, spec

    strict = Settings(
        _env_file=None, auth_enabled=False, llm_provider="stub", verify_warn_allowed=False
    )
    client = TestClient(create_app(strict))
    warn = {"preset": "gdpr-pseudonymise", "verify": {"leakage_check": "warn"}}
    for route in ("/api/v1/obfuscate", "/api/v1/detect"):
        r = client.post(route, json={"text": "jane@example.com", "policy": warn})
        assert (r.status_code, r.json()["code"]) == (422, "verify_warn_disallowed"), r.text
    v = client.post("/api/v1/policies/validate", json={"policy": warn})
    assert (v.status_code, v.json()["code"]) == (422, "verify_warn_disallowed")
    for run in (
        spec(**UAT_TABLE, options={"verify": "warn"}),
        spec(**UAT_TABLE).model_copy(update={"policy": warn}),
    ):
        with pytest.raises(SpecError) as err:
            _check(run, settings=strict)
        assert (err.value.code, err.value.status) == ("verify_warn_disallowed", 422)
    _check(spec(**UAT_TABLE, options={"verify": "warn"}), settings=Settings(_env_file=None))


def test_literal_secret_env_vars_are_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _files(tmp_path, monkeypatch)
    monkeypatch.setenv("DPS_TOKENIZER_TOKEN", TOKEN)
    problems = posture_problems(_prod())
    assert any(
        "DPS_TOKENIZER_TOKEN must be provided as DPS_TOKENIZER_TOKEN_FILE" in p for p in problems
    )
    assert TOKEN not in json.dumps(problems)


async def test_posture_report_is_value_free_and_not_enforced_locally() -> None:
    from dps.posture import posture_report

    report = await posture_report(
        Settings(_env_file=None, env="local", bootstrap_api_key="dps_sentinel_local_key"),
        environ={},
    )
    ids = {c.id for c in report.checks}
    assert ids == {
        "auth_enabled",
        "bootstrap_key_absent",
        "api_keys_from_file",
        "tls_egress",
        "audit_strict",
        "leakage_strict",
        "llm_not_stub",
        "kek_from_secret",
        "keys_rotated",
        "rls_posture",
        "migrations_verified",
    }
    assert report.enforced is False and report.total == 11
    assert "dps_sentinel_local_key" not in report.model_dump_json()
    by_id = {c.id: c for c in report.checks}
    assert by_id["bootstrap_key_absent"].ok is False
    assert by_id["rls_posture"].detail == "database unavailable"
    assert by_id["leakage_strict"].enforced_at_boot is True and by_id["leakage_strict"].ok is False
    assert report.passed == sum(c.ok for c in report.checks)


async def test_posture_report_passes_a_good_production(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os

    from dps.posture import posture_report

    _files(tmp_path, monkeypatch)
    report = await posture_report(_prod(pg_host=""), environ=dict(os.environ))
    by_id = {c.id: c.ok for c in report.checks}
    assert report.enforced is True
    assert all(by_id[i] for i in ("auth_enabled", "bootstrap_key_absent", "api_keys_from_file"))
    assert all(by_id[i] for i in ("tls_egress", "audit_strict", "llm_not_stub", "kek_from_secret"))
    assert TOKEN not in report.model_dump_json() and KEK not in report.model_dump_json()


def test_docs_and_cors_follow_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastapi.testclient import TestClient

    from dps.app import create_app

    local = TestClient(
        create_app(Settings(_env_file=None, env="local", auth_enabled=False, llm_provider="stub"))
    )
    assert local.get("/docs").status_code == 200
    assert local.get("/api/v1/openapi.json").status_code == 200
    _files(tmp_path, monkeypatch)
    prod = TestClient(create_app(_prod(tokenizer_provider="", tokenizer_url="", pg_host="")))
    assert prod.get("/api/v1/openapi.json").status_code == 404
    assert prod.get("/docs").status_code in (404, 200)  # 200 = the console's SPA fallback
    pre = prod.options(
        "/api/v1/detect",
        headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"},
    )
    assert pre.headers.get("access-control-allow-origin") is None
    good = prod.options(
        "/api/v1/detect",
        headers={
            "Origin": "https://console.bank.example",
            "Access-Control-Request-Method": "PUT",
        },
    )
    assert good.headers.get("access-control-allow-origin") == "https://console.bank.example"
    same_origin = TestClient(
        create_app(
            Settings(
                _env_file=None,
                env="local",
                auth_enabled=False,
                llm_provider="stub",
                cors_origins="",
            )
        )
    )
    r = same_origin.get("/health", headers={"Origin": "https://console.bank.example"})
    assert "access-control-allow-origin" not in r.headers


def test_admin_posture_route_needs_admin_and_no_database() -> None:
    from fastapi.testclient import TestClient

    from dps.app import create_app

    keys = (
        '[{"name": "adm", "key": "dps_adm_x", "tenant_ids": ["*"], "scopes": ["admin"]}, '
        '{"name": "det", "key": "dps_det_x", "tenant_ids": ["t1"], "scopes": ["detect"]}]'
    )
    app = create_app(
        Settings(_env_file=None, env="local", api_keys=keys, llm_provider="stub", pg_host="")
    )
    with TestClient(app) as c:
        denied = c.get("/api/v1/admin/posture", headers={"X-API-KEY": "dps_det_x"})
        assert denied.status_code == 403
        r = c.get("/api/v1/admin/posture", headers={"X-API-KEY": "dps_adm_x"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["total"] == 11 and body["enforced"] is False
        assert "dps_adm_x" not in r.text and "dps_det_x" not in r.text


@pytest.mark.db
async def test_posture_report_reads_keys_and_tenant_policies(migrated_db: Settings) -> None:
    from dps import db
    from dps.engine.policy import load_preset
    from dps.policy_store import create_policy, publish, save_draft
    from dps.posture import posture_report

    s = db.schema_ident(migrated_db)
    async with db.acquire(None) as conn:
        await conn.execute(
            f"INSERT INTO {s}.dps_api_key (id, key_hash, name, tenant_ids, scopes, created_at) "
            "VALUES ('k_old', repeat('b', 64), 'ledger-recon-batch', '{t1}', '{detect}', "
            "now() - interval '113 days 2 hours')"
        )
    await db_tenant(migrated_db, "t1")
    await create_policy("t1", "retail", "gdpr-pseudonymise", actor="tester")
    body = load_preset("gdpr-pseudonymise").model_dump(mode="json")
    body["verify"] = {"leakage_check": "warn"}
    await save_draft("t1", "retail", body, actor="tester")
    await publish("t1", "retail", change_ticket=None, note=None, actor="a", settings=migrated_db)
    report = await posture_report(migrated_db.model_copy(update={"verify_warn_allowed": False}))
    by_id = {c.id: c for c in report.checks}
    assert by_id["keys_rotated"].ok is False
    assert by_id["keys_rotated"].detail == "ledger-recon-batch is 113 days old"
    assert by_id["leakage_strict"].ok is False
    assert (
        "tenant policy t1/retail must use verify.leakage_check=strict"
        in by_id["leakage_strict"].detail
    )
    assert by_id["rls_posture"].ok is True and by_id["migrations_verified"].ok is True
    assert "b" * 64 not in report.model_dump_json()


async def db_tenant(settings: Settings, tenant: str) -> None:
    """A ``dps_tenant`` row, so the tenant directory lists the tenant."""
    from dps import db

    async with db.acquire(None) as conn:
        await conn.execute(
            f"INSERT INTO {db.schema_ident(settings)}.dps_tenant (tenant_id) VALUES ($1) "
            "ON CONFLICT DO NOTHING",
            tenant,
        )
