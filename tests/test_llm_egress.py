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


# -- the real prompts through the real egress check ------------------------------------------------
# The T3 and rewrite tests script ``complete`` itself, above the verifier; these go through it.


def _label_prompt(data: dict[str, Any]) -> list[dict[str, str]]:
    from dps.engine.detect.ensemble import Detector, DetectorConfig
    from dps.engine.detect.llm_label import leftover_fields, prompt_fields, render_prompt

    detector = Detector(DetectorConfig(locale="en_IN"))
    values = {f"$.{k}": [v] for k, v in data.items()}
    sent, _ = prompt_fields(leftover_fields(values), detector)
    return render_prompt(sent)


def _spec_prompt(rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    from dps.engine.detect.ensemble import Detector, DetectorConfig
    from dps.engine.synth.schema_profile import profile_from_sample, render_prompt

    detector = Detector(DetectorConfig(locale="en_IN"))
    return render_prompt(profile_from_sample(rows, detector, name="customers").profile)


def _rewrite_prompt(templated: str) -> list[dict[str, str]]:
    from dps.engine.synth.llm_text import REWRITE_SYSTEM

    return [{"role": "system", "content": REWRITE_SYSTEM}, {"role": "user", "content": templated}]


_ROWS = [
    {"cust_id": 1001, "segment": "retail", "balance": 48250.5, "opened": "2019-04-11"},
    {"cust_id": 1002, "segment": "premier", "balance": 812300.0, "opened": "2016-09-02"},
]


def test_system_prompts_carry_no_call_data() -> None:
    """The verifier skips a registered fixed system prompt: each must be a fixed constant."""
    from dps.llm.egress import fixed_system_prompts

    for messages in (_label_prompt({"a": "x1"}), _spec_prompt(_ROWS), _rewrite_prompt("hi")):
        assert messages[0]["content"] in fixed_system_prompts()
    assert _label_prompt({"a": "x1"})[0] == _label_prompt({"beneficiary_nm": "QX-7Z"})[0]
    assert _spec_prompt(_ROWS)[0] == _spec_prompt([{"z": "q"}, {"z": "r"}])[0]
    assert _rewrite_prompt("{PERSON_NAME_1}")[0] == _rewrite_prompt("hi {DATE_1}")[0]


def test_instructions_that_look_like_values_do_not_block() -> None:
    from dps.engine.detect.llm_label import system_prompt

    # the labelling prompt's type list holds USERNAME: a SWIFT BIC's shape (country NA)
    system = [{"role": "system", "content": system_prompt()}]
    assert "USERNAME" in system[0]["content"]
    assert residual_values([*system, {"role": "user", "content": "{PERSON_NAME_1}"}]) == 0
    # the same token in call data still counts
    assert residual_values([*system, {"role": "user", "content": "bic USERNAME"}]) == 1
    # FINAL r1 privacy-ops B1: only a registered fixed prompt is exempt; other system text is
    # scanned in full (patterns and protected values)
    adhoc = [{"role": "system", "content": "Entity types: SWIFT_BIC, USERNAME, VOTER_ID"}]
    assert residual_values(adhoc) == 1
    assert residual_values(adhoc, protected=["USERNAME"]) == 1
    assert residual_values(system, protected=["USERNAME"]) == 0


@pytest.mark.parametrize(
    "messages",
    [
        _label_prompt({"beneficiary_nm": "Kavya R", "risk_band": "medium", "ref_x7": "rel-mgr"}),
        _spec_prompt(_ROWS),
        _rewrite_prompt("Customer {PERSON_NAME_1} (email {EMAIL_1}) called on {DATE_1}."),
    ],
    ids=["label", "genspec", "rewrite"],
)
def test_the_real_prompts_pass_the_verifier(messages: list[dict[str, str]]) -> None:
    assert residual_values(messages) == 0


async def test_t3_labels_through_the_real_complete() -> None:
    """T3 end to end through ``complete`` (strict): the verifier must let a value-free prompt out."""
    import json

    from dps.engine.service import Payload, PrivacyEngine, RequestOptions
    from dps.llm.gateway import LlmResult

    settings = Settings(
        _env_file=None,
        env="local",
        llm_provider="stellar",
        stellar_base_url="https://stellar.test",
        coin_url="https://coin.test",
        llm_egress_check="strict",
        fpe_enabled=False,
        ner_enabled=False,
    )
    sent: list[list[dict[str, str]]] = []

    class ProviderSeam(LlmGateway):
        async def _stellar(self, messages, temperature, max_tokens):  # noqa: ANN001, ANN202
            sent.append(messages)
            reply = [
                {"ref": "$.acct.beneficiary_nm", "entity_type": "PERSON_NAME", "confidence": 0.9}
            ]
            return LlmResult(text=json.dumps(reply), provider="stellar", model="test")

    engine = PrivacyEngine(settings, gateway=ProviderSeam(settings))
    policy = {
        "preset": "gdpr-pseudonymise",
        "detect": {"tiers": ["structural", "patterns", "llm_label"]},
    }
    data = {"acct": {"beneficiary_nm": "Kavya Raghunathan", "risk_band": "medium"}}
    found = await engine.label_leftovers(Payload(data=data), policy, RequestOptions())
    assert len(sent) == 1 and found.llm_calls == 1
    assert [(f.locator.path, f.entity_type) for f in found] == [
        ("$.acct.beneficiary_nm", "PERSON_NAME")
    ]
    assert "Kavya" not in json.dumps(sent) and "medium" not in sent[0][1]["content"]
