from __future__ import annotations

import base64
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

import dps.app as app_module
from dps.app import create_app
from dps.audit import MemoryAuditSink, NullAuditSink
from dps.config import Settings
from dps.engine.service import (
    DetectResponse,
    EngineError,
    Payload,
    PrivacyEngine,
    RequestOptions,
    RevealResponse,
)
from dps.layers.api.common import run_engine
from dps.llm.gateway import get_gateway
from dps.posture import PostureError

KEYS = [
    {"name": "full", "key": "dps_full", "tenant_ids": ["*"], "scopes": ["*"]},
    {"name": "ro", "key": "dps_ro", "tenant_ids": ["retail"], "scopes": ["detect"]},
]
SAMPLE = {
    "customer_id": "CUST-884120",
    "full_name": "Ananya Krishnan",
    "email": "ananya.k@example-mail.in",
    "aadhaar": "2341 2341 2346",
    "risk_score": 712,
    "notes": "mail ann.k@example-mail.in",
}
FULL = {"X-API-KEY": "dps_full"}
RO = {"X-API-KEY": "dps_ro"}


def _settings(**kw: Any) -> Settings:
    base: dict[str, Any] = dict(
        _env_file=None,
        env="local",
        auth_enabled=True,
        api_keys=json.dumps(KEYS),
        llm_provider="stub",
        fpe_enabled=False,
        ner_enabled=False,
        max_text_chars=200,
        max_items=50,
        rate_limit_enabled=False,
    )
    return Settings(**{**base, **kw})


@pytest.fixture
def client() -> Iterator[tuple[TestClient, MemoryAuditSink]]:
    # The lifespan (readiness) only runs when TestClient is used as a context manager.
    sink = MemoryAuditSink()
    with TestClient(create_app(_settings(), audit_sink=sink)) as c:
        yield c, sink


def test_ops_endpoints(client) -> None:  # noqa: ANN001
    c, _ = client
    assert c.get("/health").json() == {"status": "ok"}
    ready = c.get("/readyz")
    assert (
        ready.status_code == 200
        and ready.json()["ready"] is True
        and "llm-gateway" in ready.json()["components"]
    )
    assert b"dps_llm_raw_pii_sent_total 0.0" in c.get("/metrics").content
    headers = c.get("/health").headers
    assert headers["x-content-type-options"] == "nosniff" and headers["x-frame-options"] == "DENY"


def test_auth_and_scopes(client) -> None:  # noqa: ANN001
    c, _ = client
    assert c.post("/api/v1/detect", json={"data": SAMPLE}).status_code == 401
    assert (
        c.post("/api/v1/detect", json={"data": SAMPLE}, headers={"X-API-KEY": "nope"}).status_code
        == 401
    )
    assert c.post("/api/v1/obfuscate", json={"data": SAMPLE}, headers=RO).status_code == 403
    ok = c.post("/api/v1/detect", json={"data": SAMPLE}, headers=RO)
    assert (
        ok.status_code == 200
        and ok.json()["record_sensitivity"] == "CRITICAL"
        and ok.json()["trace_id"]
    )


def test_obfuscate_reveal_round_trip_over_http(client) -> None:  # noqa: ANN001
    c, sink = client
    r = c.post(
        "/api/v1/obfuscate", json={"data": SAMPLE, "policy": "gdpr-pseudonymise"}, headers=FULL
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert (
        body["result"]["risk_score"] == 712
        and body["key_material"]["dek"]
        and body["report"]["verification"]["passed"]
    )
    assert body["result"]["email"] != SAMPLE["email"]
    back = c.post(
        "/api/v1/reveal",
        json={
            "data": body["result"],
            "key_material": body["key_material"],
            "reveal_hints": body["reveal_hints"],
        },
        headers=FULL,
    )
    assert back.status_code == 200 and back.json()["result"] == SAMPLE
    assert (
        sink.events[-2].mode == "obfuscate"
        and sink.events[-1].mode == "reveal"
        and sink.events[-1].outcome == "ok"
    )
    assert "ananya" not in json.dumps([e.model_dump(mode="json") for e in sink.events]).lower()


def test_synthesize_presets_plan_validate(client) -> None:  # noqa: ANN001
    c, _ = client
    s = c.post(
        "/api/v1/synthesize", json={"data": SAMPLE, "policy": "synthetic-realistic"}, headers=FULL
    )
    assert (
        s.status_code == 200
        and s.json()["result"]["full_name"] != SAMPLE["full_name"]
        and s.json()["synthesis_seed"]
    )
    presets = c.get("/api/v1/policies/presets", headers=RO).json()
    assert {p["name"] for p in presets} >= {"gdpr-pseudonymise", "pci-dss"}
    plan = c.post(
        "/api/v1/policies/plan",
        json={"data": SAMPLE, "policy": "pci-dss", "mode": "obfuscate"},
        headers=RO,
    )
    assert plan.status_code == 200 and any(
        row["entity_type"] == "AADHAAR" for row in plan.json()["plan"]
    )
    bad = c.post(
        "/api/v1/policies/validate",
        json={"policy": {"treat": {"SSN": {"method": "teleport"}}}},
        headers=RO,
    )
    # S1 (M5 Task 17): the rule is named, the submitted value is not echoed
    assert bad.status_code == 400 and "unknown method" in bad.json()["detail"]
    assert "teleport" not in bad.json()["detail"]
    good = c.post(
        "/api/v1/policies/validate",
        json={"policy": {"preset": "pci-dss", "output": {"token_style": "envelope"}}},
        headers=RO,
    )
    assert good.status_code == 200 and good.json()["output"]["token_style"] == "envelope"


def test_limits_and_errors(client) -> None:  # noqa: ANN001
    c, _ = client
    assert c.post("/api/v1/detect", json={"text": "x" * 201}, headers=RO).status_code == 413
    assert (
        c.post(
            "/api/v1/detect", json={"data": {f"k{i}": i for i in range(51)}}, headers=RO
        ).status_code
        == 413
    )
    assert (
        c.post("/api/v1/detect", json={"data": {"a": 1}, "text": "both"}, headers=RO).status_code
        == 422
    )
    assert (
        c.post("/api/v1/detect", json={"data": SAMPLE, "policy": "nope"}, headers=RO).status_code
        == 400
    )
    assert (
        c.post(
            "/api/v1/reveal",
            json={
                "data": {"a": "b"},
                "key_material": {
                    "alg": "x",
                    "kid": "k",
                    "dek": "c2hvcnQ",
                    "created_at": "2026-01-01T00:00:00Z",
                },
            },
            headers=FULL,
        ).status_code
        == 400
    )
    assert c.get("/api/v1/nothing", headers=FULL).status_code == 404


# --- hardening beyond the plan --------------------------------------------------------------------
SECRET_DEK = "U0VDUkVUX0RFS19ORVZFUl9FQ0hPRUQ"


def test_request_validation_errors_never_echo_the_body(client) -> None:  # noqa: ANN001
    c, _ = client
    missing_key = c.post(
        "/api/v1/reveal",
        json={"data": {"ssn": "123-45-6789", "name": "Ananya Krishnan"}},
        headers=FULL,
    )
    assert missing_key.status_code == 422
    bad_scope = c.post(
        "/api/v1/obfuscate",
        headers=FULL,
        json={
            "data": SAMPLE,
            "options": {"consistency": {"scope": "galaxy", "key_material": {"dek": SECRET_DEK}}},
        },
    )
    assert bad_scope.status_code == 422
    bad_text = c.post(
        "/api/v1/detect", json={"text": ["Ananya Krishnan", "123-45-6789"]}, headers=RO
    )
    assert bad_text.status_code == 422
    broken = c.post(
        "/api/v1/detect",
        content=b'{"text": "Ananya Krishnan 123-45-6789"',
        headers={**RO, "content-type": "application/json"},
    )
    assert broken.status_code == 422
    for r in (missing_key, bad_scope, bad_text, broken):
        assert all(set(e) == {"type", "loc", "msg"} for e in r.json()["detail"]), r.text
        for value in ("123-45-6789", "Ananya", SECRET_DEK, "galaxy"):
            assert value not in r.text


def test_trace_ids_are_taken_from_the_header_only_when_safe(client) -> None:  # noqa: ANN001
    c, sink = client
    ok = c.post(
        "/api/v1/detect", json={"text": "hello"}, headers={**RO, "X-Request-ID": "req-42.a_b"}
    )
    assert ok.json()["trace_id"] == "req-42.a_b" and sink.events[-1].trace_id == "req-42.a_b"
    for unsafe in ("Ananya Krishnan", "x" * 65, "a\tb"):
        r = c.post("/api/v1/detect", json={"text": "hello"}, headers={**RO, "X-Request-ID": unsafe})
        assert r.json()["trace_id"] != unsafe and len(r.json()["trace_id"]) == 16


def test_rejections_and_engine_errors_are_audited_value_free(client) -> None:  # noqa: ANN001
    c, sink = client
    assert c.post("/api/v1/detect", json={"text": "x" * 201}, headers=RO).status_code == 413
    assert (sink.events[-1].mode, sink.events[-1].outcome, sink.events[-1].tenant_id) == (
        "detect",
        "rejected",
        "retail",
    )
    assert (
        c.post(
            "/api/v1/detect", json={"data": SAMPLE, "policy": "Ananya Krishnan"}, headers=RO
        ).status_code
        == 400
    )
    # M1 (Task 2): a typed name that is no preset is never audited verbatim; an inline policy
    # is labelled by its shipped preset (else ``custom``) plus a hash of its body, never its
    # ``name`` or any other part of the body.
    assert (sink.events[-1].outcome, sink.events[-1].policy) == ("bad_request", "invalid")
    assert (
        c.post("/api/v1/detect", json={"data": SAMPLE, "policy": "ananya"}, headers=RO).status_code
        == 400
        and sink.events[-1].policy == "unknown"
    )
    inline = {"name": "ananya", "treat": {"EMAIL": {"method": "redact"}}}
    r = c.post("/api/v1/obfuscate", json={"data": SAMPLE, "policy": inline}, headers=FULL)
    assert (
        r.status_code == 200
        and sink.events[-1].policy.startswith("custom+inline:")
        and sink.events[-1].findings_total > 0
    )
    preset = c.post(
        "/api/v1/obfuscate", json={"data": SAMPLE, "policy": {"preset": "pci-dss"}}, headers=FULL
    )
    assert preset.status_code == 200 and sink.events[-1].policy.startswith("pci-dss+inline:")
    plan = c.post("/api/v1/policies/plan", json={"data": SAMPLE, "mode": "synthesize"}, headers=RO)
    assert plan.status_code == 200 and sink.events[-1].mode == "plan:synthesize"
    assert sink.events[-1].findings_total == len(plan.json()["findings"]) > 0
    assert "ananya" not in json.dumps([e.model_dump(mode="json") for e in sink.events]).lower()


def test_tenant_header_rules_over_http(client) -> None:  # noqa: ANN001
    c, sink = client
    assert (
        c.post(
            "/api/v1/detect", json={"text": "hi"}, headers={**RO, "X-DPS-Tenant": "cards"}
        ).status_code
        == 403
    )
    bad = c.post(
        "/api/v1/detect", json={"text": "hi"}, headers={**FULL, "X-DPS-Tenant": "Ananya Krishnan"}
    )
    assert bad.status_code == 400 and "Ananya" not in bad.text
    assert (
        c.post(
            "/api/v1/detect", json={"text": "hi"}, headers={**FULL, "X-DPS-Tenant": "wealth"}
        ).status_code
        == 200
    )
    assert sink.events[-1].tenant_id == "wealth" and sink.events[-1].key_id.startswith("static-")
    unauth = c.post("/api/v1/detect", json={"text": "hi"})
    assert (
        unauth.headers["www-authenticate"].startswith("ApiKey")
        and unauth.headers["x-frame-options"] == "DENY"
    )


def test_rate_limit_answers_429_with_retry_after() -> None:
    settings = _settings(rate_limit_enabled=True, rate_limit_rps=1.0, rate_limit_burst=2)
    with TestClient(create_app(settings, audit_sink=MemoryAuditSink())) as c:
        codes = [
            c.post("/api/v1/detect", json={"text": "hi"}, headers=RO).status_code for _ in range(3)
        ]
        assert codes == [200, 200, 429]
        limited = c.post("/api/v1/detect", json={"text": "hi"}, headers=RO)
        assert limited.headers["retry-after"] == "1"
        assert (
            c.post("/api/v1/detect", json={"text": "hi"}, headers=FULL).status_code == 200
        )  # own bucket


def test_body_limit_by_header_and_by_stream() -> None:
    with TestClient(create_app(_settings(max_payload_mb=1, max_text_chars=5_000_000))) as c:
        big = json.dumps({"text": "a" * (1024 * 1024 + 10)}).encode()
        sized = c.post(
            "/api/v1/detect", content=big, headers={**RO, "content-type": "application/json"}
        )
        assert sized.status_code == 413 and "bytes" in sized.json()["detail"]

        def chunks(body: bytes) -> Iterator[bytes]:
            for i in range(0, len(body), 64 * 1024):
                yield body[i : i + 64 * 1024]

        streamed = c.post(
            "/api/v1/detect",
            content=chunks(big),
            headers={**RO, "content-type": "application/json"},
        )
        assert streamed.status_code == 413 and "bytes" in streamed.json()["detail"]
        small = c.post(
            "/api/v1/detect",
            content=chunks(b'{"text": "hello"}'),
            headers={**RO, "content-type": "application/json"},
        )
        assert small.status_code == 200


def test_deep_documents_are_refused_not_crashed(client) -> None:  # noqa: ANN001
    c, _ = client
    deep: Any = "Ananya Krishnan"
    for _ in range(300):
        deep = {"a": deep}
    r = c.post("/api/v1/detect", json={"data": deep}, headers=RO)
    assert r.status_code == 422 and "Ananya" not in r.text
    # The engine itself recurses (obfuscate fails between 400 and 600 levels); 100 levels work.
    ok: Any = {"email": "ananya.k@example-mail.in"}
    for _ in range(99):
        ok = {"a": ok}
    assert c.post("/api/v1/obfuscate", json={"data": ok}, headers=FULL).status_code == 200


class _Exploding:
    """A malformed engine result: building the response raises with a value in the message."""

    def model_dump(self) -> dict[str, Any]:
        raise RuntimeError("Ananya Krishnan 123-45-6789")


class _BrokenEngine(PrivacyEngine):
    """Raises with a customer value in the message, or returns a result the router cannot use."""

    def detect(self, payload: Payload, policy_ref: Any, options: RequestOptions) -> DetectResponse:
        raise RuntimeError("Ananya Krishnan 123-45-6789")

    def reveal(
        self,
        payload: Payload,
        key_material: dict[str, Any],
        hints: list[dict[str, Any]],
        *,
        on_detokenize: Any = None,
    ) -> RevealResponse:
        return _Exploding()  # type: ignore[return-value]


def test_unexpected_errors_are_500_and_never_reach_the_log_with_values(capsys) -> None:  # noqa: ANN001
    settings = _settings()
    sink = MemoryAuditSink()
    with TestClient(create_app(settings, engine=_BrokenEngine(settings), audit_sink=sink)) as c:
        r = c.post("/api/v1/detect", json={"text": "hello"}, headers=FULL)
        assert r.status_code == 500 and r.json() == {"detail": "internal error"}
        assert sink.events[-1].outcome == "error"
        km = {"alg": "x", "kid": "k", "dek": "c2hvcnQ", "created_at": "2026-01-01T00:00:00Z"}
        bad_out = c.post(
            "/api/v1/reveal", json={"data": {"a": "b"}, "key_material": km}, headers=FULL
        )
        assert bad_out.status_code == 500 and bad_out.json() == {"detail": "internal error"}
        assert bad_out.headers["x-content-type-options"] == "nosniff"
    captured = capsys.readouterr()
    assert (
        "Ananya" not in captured.out + captured.err
        and "123-45-6789" not in captured.out + captured.err
    )
    assert (
        "engine_error" in captured.out + captured.err
        and "unhandled_error" in captured.out + captured.err
    )


def test_spa_mount_serves_index_with_csp_and_refuses_traversal(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>DPS</title>")
    (dist / "assets" / "app-1a2b.js").write_text("console.log(1)")
    (dist / "favicon.svg").write_text("<svg/>")
    (tmp_path / "secret.txt").write_text("outside the dist")
    monkeypatch.setattr(app_module, "FRONTEND_DIST", dist)
    with TestClient(create_app(_settings())) as c:
        root = c.get("/")
        assert root.status_code == 200 and "DPS" in root.text
        assert "frame-ancestors 'none'" in root.headers["content-security-policy"]
        assert root.headers["cache-control"] == "no-cache"
        asset = c.get("/assets/app-1a2b.js")
        assert asset.status_code == 200 and "immutable" in asset.headers["cache-control"]
        assert c.get("/favicon.svg").text == "<svg/>"
        deep_link = c.get("/playground/run")
        assert deep_link.status_code == 200 and "DPS" in deep_link.text
        assert c.get("/api/v1/nothing").status_code == 404 and c.get("/api").status_code == 404
        for path in ("/..%2Fsecret.txt", "/%2e%2e/secret.txt", "/assets/..%2F..%2Fsecret.txt"):
            assert "outside the dist" not in c.get(path).text
        assert c.get("/health").json() == {
            "status": "ok"
        }  # the catch-all does not shadow ops routes


def test_hsts_outside_local_audit_switch_and_openapi() -> None:
    with TestClient(create_app(_settings(env="dev"))) as c:
        assert "max-age" in c.get("/health").headers["strict-transport-security"]
        spec = c.get("/api/v1/openapi.json").json()
        assert {
            "/api/v1/detect",
            "/api/v1/obfuscate",
            "/api/v1/synthesize",
            "/api/v1/reveal",
        } <= set(spec["paths"])
    with TestClient(create_app(_settings())) as c:
        assert "strict-transport-security" not in c.get("/health").headers
    assert isinstance(create_app(_settings(audit_enabled=False)).state.audit, NullAuditSink)


def test_readiness_reflects_the_injected_engine_and_posture_refuses_unsafe_config() -> None:
    settings = _settings(llm_provider="")
    with TestClient(create_app(settings, engine=PrivacyEngine(settings, gateway=None))) as c:
        llm = c.get("/readyz").json()["components"]["llm-gateway"]
        assert llm["ok"] is False and llm["provider"] == "none"
    # The probe reports the gateway the engine actually uses, not a second one built from settings.
    stub = get_gateway(_settings(llm_provider="stub"))
    with TestClient(create_app(settings, engine=PrivacyEngine(settings, gateway=stub))) as c:
        llm = c.get("/readyz").json()["components"]["llm-gateway"]
        assert llm["ok"] is True and llm["provider"] == "stub"
    with pytest.raises(PostureError):
        create_app(_settings(env="prod", auth_enabled=False))
    with pytest.raises(ValueError) as err:
        create_app(_settings(api_keys='[{"name": "x", "key": "dps_leaky_secret"'))
    assert "dps_leaky_secret" not in str(err.value)


def test_readiness_names_the_coin_tls_mode_and_insecure_mode_warns_once(capsys) -> None:  # noqa: ANN001
    with TestClient(create_app(_settings())) as c:
        assert c.get("/readyz").json()["components"]["llm-gateway"]["coin_tls"] == "verified"
    assert "coin_tls_insecure" not in "".join(capsys.readouterr())

    secret = base64.b64encode(b"s3nt1nel-coin-secret").decode()
    settings = _settings(
        coin_tls_insecure=True,
        coin_url="https://svc-user:pa55word@coin.internal/token",
        coin_client_secret=secret,
    )
    with TestClient(create_app(settings)) as c:
        assert c.get("/readyz").json()["components"]["llm-gateway"]["coin_tls"] == "insecure"
    out = "".join(capsys.readouterr())
    assert out.count("coin_tls_insecure") == 1
    for fragment in (secret, "s3nt1nel", "pa55word", "svc-user", "coin.internal"):
        assert fragment not in out


class _FailingSink:
    def write(self, event: Any) -> None:
        raise RuntimeError("Ananya Krishnan")


def test_a_failing_audit_sink_never_fails_the_request(capsys) -> None:  # noqa: ANN001
    with TestClient(create_app(_settings(), audit_sink=_FailingSink())) as c:
        r = c.post("/api/v1/detect", json={"data": SAMPLE}, headers=RO)
        assert r.status_code == 200 and r.json()["findings"]
    captured = capsys.readouterr()
    assert "audit_write_failed" in captured.out + captured.err
    assert "Ananya" not in captured.out + captured.err


def test_run_engine_takes_policy_name_and_sync_or_async_calls() -> None:
    # Later milestones call ``run_engine(request, mode=..., policy_name=..., call=...)`` with
    # sync callables (M1's CodedError test) and coroutines (the documents and schema routers).
    sink = MemoryAuditSink()
    app = create_app(_settings(), audit_sink=sink)

    def sync_ok() -> dict[str, str]:
        return {"via": "sync"}

    async def async_ok() -> dict[str, str]:
        return {"via": "async"}

    def sync_fail() -> dict[str, str]:
        raise EngineError("unknown preset")

    calls = {"sync": sync_ok, "async": async_ok, "fail": sync_fail}

    async def probe(request: Request, kind: str) -> dict[str, str]:
        result, tid = await run_engine(
            request, mode=f"test:{kind}", policy_name="gdpr-pseudonymise", call=calls[kind]
        )
        return {**result, "trace_id": tid}

    app.add_api_route("/api/v1/_probe/{kind}", probe, methods=["POST"])
    with TestClient(app) as c:
        assert c.post("/api/v1/_probe/sync").json()["via"] == "sync"
        assert c.post("/api/v1/_probe/async").json()["via"] == "async"
        failed = c.post("/api/v1/_probe/fail")
        assert failed.status_code == 400 and failed.json() == {
            "detail": "unknown preset",
            "code": "bad_request",  # M1 (Task 2): additive machine code
        }
    assert [(e.mode, e.outcome, e.policy) for e in sink.events] == [
        ("test:sync", "ok", "gdpr-pseudonymise"),
        ("test:async", "ok", "gdpr-pseudonymise"),
        ("test:fail", "bad_request", "gdpr-pseudonymise"),
    ]
