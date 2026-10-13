"""LLM egress verifier and per-tenant exposure accounting (M5 Task 16B, spec P1/§10).

The verifier is the last check before any byte leaves for the model: a prompt holding a raw
sensitive value is blocked under ``strict`` (nothing sent, counted per tenant) and only counted —
loudly, the alarm counters — under ``warn``. Every call is counted per tenant and lands in the
request's audit counts.
"""

from __future__ import annotations

from typing import Any

import pytest
from prometheus_client import REGISTRY, Counter

from dps import observability
from dps.config import Settings
from dps.llm.egress import LlmEgressBlocked, residual_values
from dps.llm.gateway import LlmCallError, LlmGateway
from dps.observability import CURRENT_TENANT, LLM_STATS, LlmStats, record_llm


def _sample(name: str, tenant: str) -> float:
    return REGISTRY.get_sample_value(name, {"tenant_id": tenant}) or 0.0


def tenant(value: str | None) -> None:
    """Bind ``CURRENT_TENANT`` in the running test's own context (each test runs in its own)."""
    CURRENT_TENANT.set(value)


def _user(text: str) -> list[dict[str, str]]:
    return [{"role": "system", "content": "Rewrite the text."}, {"role": "user", "content": text}]


def test_residual_values_finds_protected_and_validated_values() -> None:
    assert residual_values(_user("rewrite {EMAIL_1} for Ananya"), protected=["Ananya"]) == 1
    assert residual_values(_user("card 4111 1111 1111 1111"), protected=[]) == 1
    assert residual_values(_user("{EMAIL_1} wrote {DATE_2}"), protected=[]) == 0
    assert residual_values(_user("mail jane.doe@example.com now"), protected=[]) == 1
    # value shapes (letters x/X, digits 9) are not values; short protected values match words only
    assert residual_values(_user('{"shapes": ["xxx.x@x.xx", "9999 9999"]}'), protected=[]) == 0
    assert residual_values(_user("planning the annual review"), protected=["Ann"]) == 0
    assert residual_values(_user("ask Ann about it"), protected=["Ann"]) == 1
    # one value counted once, however often and however found
    assert residual_values(_user("Ananya, ANANYA and ananya"), protected=["Ananya", "ananya"]) == 1


async def test_strict_blocks_and_counts_per_tenant() -> None:
    gateway = LlmGateway(Settings(_env_file=None, llm_provider="stub", llm_egress_check="strict"))
    tenant("t1")
    blocked = _sample("dps_llm_exposure_blocked_by_tenant_total", "t1")
    raw_global = observability.LLM_RAW_PII._value.get()  # noqa: SLF001
    with pytest.raises(LlmEgressBlocked) as err:
        await gateway.complete(_user("rewrite {EMAIL_1} for Ananya"), protected=["Ananya"])
    assert isinstance(err.value, LlmCallError)  # callers fall back on LlmCallError already
    assert "Ananya" not in str(err.value)
    assert _sample("dps_llm_exposure_blocked_by_tenant_total", "t1") == blocked + 1
    assert observability.LLM_RAW_PII._value.get() == raw_global  # noqa: SLF001


async def test_warn_sends_and_raises_the_alarm_counters(monkeypatch: pytest.MonkeyPatch) -> None:
    # M0's process counter must stay 0 for the rest of the session: count into a private one
    private = Counter("dps_llm_raw_pii_sent_test", "test double", registry=None)
    monkeypatch.setattr(observability, "LLM_RAW_PII", private)
    gateway = LlmGateway(Settings(_env_file=None, llm_provider="stub", llm_egress_check="warn"))
    tenant("t1")
    raw = _sample("dps_llm_raw_pii_sent_by_tenant_total", "t1")
    result = await gateway.complete(_user("rewrite {EMAIL_1} for Ananya"), protected=["Ananya"])
    assert result.text  # sent: warn is for local diagnosis only (posture requires strict)
    assert _sample("dps_llm_raw_pii_sent_by_tenant_total", "t1") == raw + 1
    assert private._value.get() == 1.0  # noqa: SLF001


async def test_calls_are_counted_per_tenant() -> None:
    gateway = LlmGateway(Settings(_env_file=None, llm_provider="stub"))
    a = REGISTRY.get_sample_value(
        "dps_llm_calls_by_tenant_total", {"tenant_id": "a", "purpose": "rewrite"}
    )
    b = REGISTRY.get_sample_value(
        "dps_llm_calls_by_tenant_total", {"tenant_id": "b", "purpose": "rewrite"}
    )
    tenant("a")
    await gateway.complete(_user("a clean {PERSON_NAME_1} text"))
    await gateway.complete(_user("another clean text"))
    tenant("b")
    await gateway.complete(_user("{EMAIL_1}"))
    after_a = REGISTRY.get_sample_value(
        "dps_llm_calls_by_tenant_total", {"tenant_id": "a", "purpose": "rewrite"}
    )
    after_b = REGISTRY.get_sample_value(
        "dps_llm_calls_by_tenant_total", {"tenant_id": "b", "purpose": "rewrite"}
    )
    assert after_a == (a or 0.0) + 2 and after_b == (b or 0.0) + 1


def test_record_llm_without_a_tenant_or_stats_is_safe() -> None:
    before = REGISTRY.get_sample_value(
        "dps_llm_calls_by_tenant_total", {"tenant_id": "-", "purpose": "probe"}
    )
    record_llm(purpose="probe")
    after = REGISTRY.get_sample_value(
        "dps_llm_calls_by_tenant_total", {"tenant_id": "-", "purpose": "probe"}
    )
    assert after == (before or 0.0) + 1
    stats = LlmStats()
    token = LLM_STATS.set(stats)
    try:
        record_llm(purpose="probe", blocked=True)
    finally:
        LLM_STATS.reset(token)
    assert (stats.calls, stats.raw_values, stats.blocked) == (0, 0, 1)


def test_audit_carries_llm_counts() -> None:
    from fastapi.testclient import TestClient

    from dps.app import create_app
    from dps.audit import MemoryAuditSink

    sink = MemoryAuditSink()
    settings = Settings(
        _env_file=None, auth_enabled=False, llm_provider="stub", llm_text_rewrite=True
    )
    client = TestClient(create_app(settings, audit_sink=sink))
    r = client.post(
        "/api/v1/synthesize",
        json={"text": "Contact jane.doe@example.com today.", "options": {"llm_rewrite": True}},
    )
    assert r.status_code == 200, r.text
    assert r.json()["report"]["llm"]["calls"] == 1
    event = next(e for e in sink.events if e.mode == "synthesize")
    assert event.counts["llm_calls"] == 1 and event.counts["llm_raw_values"] == 0
    assert event.counts["llm_blocked"] == 0
    plain = client.post("/api/v1/detect", json={"text": "jane.doe@example.com"})
    assert plain.status_code == 200
    detect_event = next(e for e in sink.events if e.mode == "detect")
    assert "llm_calls" not in detect_event.counts  # no LLM call, no LLM counts


async def test_synthesize_text_falls_back_when_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    from dps.engine.service import Payload, PrivacyEngine, RequestOptions
    from dps.engine.synth import llm_text

    real = llm_text.rewrite_placeholders

    async def leaky(templated: str, gateway: Any, **kw: Any) -> str:
        # a templating bug that left one original in the prompt (the caller's guard missed it)
        return await real(templated + " cc jane.doe@example.com", gateway, **kw)

    monkeypatch.setattr(llm_text, "rewrite_placeholders", leaky)
    settings = Settings(_env_file=None, llm_provider="stub", llm_text_rewrite=True)
    engine = PrivacyEngine(settings, gateway=LlmGateway(settings))
    blocked = _sample("dps_llm_exposure_blocked_by_tenant_total", "-")
    result = await engine.synthesize(
        Payload(text="Contact jane.doe@example.com today."),
        None,
        RequestOptions(llm_rewrite=True),
    )
    assert result.report.llm["calls"] == 0 and result.report.llm["used"] is False
    assert result.report.llm["fallback"] == "egress_blocked"
    assert "jane.doe@example.com" not in str(result.result)
    assert _sample("dps_llm_exposure_blocked_by_tenant_total", "-") == blocked + 1


async def test_egress_decisions_log_no_values(
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import logging

    from dps.observability import configure_logging

    configure_logging("DEBUG", json_output=True)
    caplog.set_level(logging.DEBUG)
    monkeypatch.setattr(
        observability, "LLM_RAW_PII", Counter("dps_llm_raw_pii_sent_scan", "x", registry=None)
    )
    secret = "Zebulon-Quixote-4471"
    for check in ("strict", "warn"):
        gateway = LlmGateway(Settings(_env_file=None, llm_provider="stub", llm_egress_check=check))
        try:
            await gateway.complete(
                _user(f"hello {secret} jane.doe@example.com"), protected=[secret]
            )
        except LlmEgressBlocked as exc:
            assert secret not in str(exc) and "jane.doe" not in str(exc)
    captured = capsys.readouterr()
    blob = "\n".join((captured.out, captured.err, caplog.text))
    assert "llm_egress_blocked" in blob and "llm_egress_raw_values_sent" in blob
    for value in (secret, "jane.doe@example.com", "jane.doe", "Zebulon"):
        assert value not in blob
