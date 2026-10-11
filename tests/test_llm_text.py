from __future__ import annotations

from typing import Any

import pytest

from dps.config import Settings
from dps.engine.detect.ensemble import Detector, DetectorConfig
from dps.engine.findings import Finding
from dps.engine.planner import build_plan
from dps.engine.policy import resolve_policy
from dps.engine.synth.llm_text import (
    LeakToModelError,
    assert_placeholder_safe,
    synthesize_text,
    to_placeholders,
)
from dps.llm.gateway import LlmCallError, LlmGateway, LlmResult
from dps.observability import LLM_RAW_PII

TEXT = "Ann Smith (ann.k@example-mail.in) called; reply to ann.k@example-mail.in by 2026-01-05."


class FakeSynth:
    def surrogate(self, entity_type: str, original: str, locale: str, anchor: str) -> str:
        return {"EMAIL": "meera@example.com", "DATE": "2026-02-01", "PERSON_NAME": "Meera V"}[
            entity_type
        ]


def _findings(text: str = TEXT) -> list[Finding]:
    return Detector(DetectorConfig()).detect(text=text).findings


def test_placeholders_are_typed_and_consistent() -> None:
    ph = to_placeholders(TEXT, _findings())
    assert ph.text.count("{EMAIL_1}") == 2 and "{DATE_1}" in ph.text
    # the name before the contact details is found without NER (M0 verification r1, B1)
    assert ph.text.startswith("{PERSON_NAME_1} ({EMAIL_1})") and "Ann Smith" not in ph.text
    assert "example-mail" not in ph.text and len(ph.mapping) == 3
    assert_placeholder_safe(ph.text, [o for _, o in ph.mapping.values()])
    with pytest.raises(LeakToModelError):
        assert_placeholder_safe(ph.text + " ann.k@example-mail.in", ["ann.k@example-mail.in"])


async def test_deterministic_fill_without_llm() -> None:
    out, info = await synthesize_text(
        TEXT,
        _findings(),
        surrogates=FakeSynth(),
        locale="en_US",
        anchor="r1",
        gateway=None,
        llm_rewrite=False,
    )
    assert "meera@example.com" in out and "example-mail" not in out and info["llm_calls"] == 0


class RewritingGateway(LlmGateway):
    def __init__(self, text: str) -> None:
        super().__init__(Settings(_env_file=None, llm_provider="stub"))
        self._text = text
        self.sent: list[list[dict[str, str]]] = []
        self.kwargs: dict[str, Any] = {}

    async def complete(self, messages, **kw):  # noqa: ANN001, ANN003, ANN201
        self.sent.append(messages)
        self.kwargs = kw
        return LlmResult(text=self._text, provider="stub", model="stub")


async def test_llm_rewrite_keeps_placeholders_or_falls_back() -> None:
    before = LLM_RAW_PII._value.get()  # noqa: SLF001
    good = RewritingGateway(
        "{PERSON_NAME_1} ({EMAIL_1}) wrote and {EMAIL_1} asked for a reply before {DATE_1}."
    )
    out, info = await synthesize_text(
        TEXT,
        _findings(),
        surrogates=FakeSynth(),
        locale="en_US",
        anchor="r1",
        gateway=good,
        llm_rewrite=True,
    )
    assert out.startswith("Meera V (meera@example.com) wrote")
    assert info["llm_calls"] == 1 and info["llm_used"]
    bad = RewritingGateway("I dropped every placeholder.")
    out2, info2 = await synthesize_text(
        TEXT,
        _findings(),
        surrogates=FakeSynth(),
        locale="en_US",
        anchor="r1",
        gateway=bad,
        llm_rewrite=True,
    )
    assert "meera@example.com" in out2 and info2["llm_used"] is False
    assert info2["fallback"] == "placeholders_missing"
    assert LLM_RAW_PII._value.get() == before == 0.0  # noqa: SLF001


# --- added beyond the plan ---------------------------------------------------------------------


def _all_sent(gateway: RewritingGateway) -> str:
    return " ".join(m["content"] for messages in gateway.sent for m in messages)


async def test_a_value_that_is_the_whole_text_never_reaches_the_model() -> None:
    """A text that *is* one value gives a whole-value finding (no span); it must be replaced too."""
    text = "  ann.k@example-mail.in "
    findings = _findings(text)
    assert findings and findings[0].locator.whole
    assert to_placeholders(text, findings).text == "  {EMAIL_1} "
    gw = RewritingGateway("{EMAIL_1}")
    out, info = await synthesize_text(
        text,
        findings,
        surrogates=FakeSynth(),
        locale="en_US",
        anchor="r1",
        gateway=gw,
        llm_rewrite=True,
    )
    assert "example-mail" not in _all_sent(gw) and out == "meera@example.com"
    assert info["llm_used"] is True


async def test_the_prompt_is_placeholders_only_with_the_spec_settings() -> None:
    gw = RewritingGateway("{EMAIL_1} and {EMAIL_1} by {DATE_1}")
    await synthesize_text(
        TEXT,
        _findings(),
        surrogates=FakeSynth(),
        locale="en_US",
        anchor="r1",
        gateway=gw,
        llm_rewrite=True,
    )
    (messages,) = gw.sent
    assert [m["role"] for m in messages] == ["system", "user"]
    assert messages[1]["content"] == to_placeholders(TEXT, _findings()).text
    assert "example-mail" not in _all_sent(gw) and "2026-01-05" not in _all_sent(gw)
    assert "Ann Smith" not in _all_sent(gw)
    assert gw.kwargs["temperature"] == 0.7 and gw.kwargs["purpose"] == "rewrite"


async def test_text_that_already_looks_like_a_placeholder_is_kept_and_never_reused() -> None:
    text = "Template {EMAIL_1} goes to ann.k@example-mail.in today"
    ph = to_placeholders(text, _findings(text))
    assert ph.text == "Template {EMAIL_1} goes to {EMAIL_2} today"
    out, _ = await synthesize_text(
        text,
        _findings(text),
        surrogates=FakeSynth(),
        locale="en_US",
        anchor="r1",
        gateway=None,
        llm_rewrite=False,
    )
    assert out == "Template {EMAIL_1} goes to meera@example.com today"


async def test_the_leak_guard_skips_the_model_instead_of_sending() -> None:
    second_email = [f for f in _findings() if f.entity_type == "EMAIL"][1]
    findings = [f for f in _findings() if f != second_email]  # that occurrence was "missed"
    gw = RewritingGateway("{EMAIL_1}")
    out, info = await synthesize_text(
        TEXT,
        findings,
        surrogates=FakeSynth(),
        locale="en_US",
        anchor="r1",
        gateway=gw,
        llm_rewrite=True,
    )
    assert gw.sent == [] and info["llm_calls"] == 0 and info["fallback"] == "leak_guard"
    assert "meera@example.com" in out  # the deterministic path still ran


async def test_a_rewrite_that_brings_an_original_back_fails_closed() -> None:
    gw = RewritingGateway(
        "{PERSON_NAME_1} {EMAIL_1} wrote ann.k@example-mail.in and {EMAIL_1} before {DATE_1}"
    )
    out, info = await synthesize_text(
        TEXT,
        _findings(),
        surrogates=FakeSynth(),
        locale="en_US",
        anchor="r1",
        gateway=gw,
        llm_rewrite=True,
    )
    assert "example-mail" not in out and info["llm_used"] is False
    assert info["fallback"] == "verification" and out.startswith("Meera V (meera@example.com)")


async def test_a_rewrite_that_invents_new_values_fails_closed() -> None:
    def screen(text: str) -> int:
        return len(Detector(DetectorConfig()).detect(text=text).findings)

    gw = RewritingGateway(
        "{PERSON_NAME_1} {EMAIL_1} wrote; cc bob.lee@corp-mail.com and {EMAIL_1} by {DATE_1}."
    )
    out, info = await synthesize_text(
        TEXT,
        _findings(),
        surrogates=FakeSynth(),
        locale="en_US",
        anchor="r1",
        gateway=gw,
        llm_rewrite=True,
        screen=screen,
    )
    assert "corp-mail" not in out and info["fallback"] == "llm_introduced_entities"


async def test_gateway_failures_and_unavailability_fall_back() -> None:
    class Failing(RewritingGateway):
        async def complete(self, messages, **kw):  # noqa: ANN001, ANN003, ANN201
            raise LlmCallError("stellar call failed: ConnectTimeout")

    out, info = await synthesize_text(
        TEXT,
        _findings(),
        surrogates=FakeSynth(),
        locale="en_US",
        anchor="r1",
        gateway=Failing(""),
        llm_rewrite=True,
    )
    # M1 (Task 3): the deterministic output's segments ride along; the service pops them.
    segments = info.pop("segments")
    assert len(segments) == 4 and all(
        out[s.out_start : s.out_end] == s.replacement for s in segments
    )
    assert info == {
        "llm_calls": 1,
        "llm_used": False,
        "fallback": "LlmCallError",
        "placeholders": 3,
        "methods_used": {"surrogate": 4},
        "notes": {},
    }
    assert "meera@example.com" in out
    off = LlmGateway(Settings(_env_file=None, llm_provider=""))
    _, info2 = await synthesize_text(
        TEXT,
        _findings(),
        surrogates=FakeSynth(),
        locale="en_US",
        anchor="r1",
        gateway=off,
        llm_rewrite=True,
    )
    assert info2["llm_calls"] == 0 and info2["fallback"] is None


async def test_the_plan_decides_each_placeholder_fill() -> None:
    text = "Mail ann.k@example-mail.in by 2026-01-05 via https://portal.example-bank.in/x"
    findings = _findings(text)
    realistic = resolve_policy("synthetic-realistic", default_name="gdpr-pseudonymise")
    plan = build_plan(findings, realistic, "synthesize", fpe_enabled=False)
    out, info = await synthesize_text(
        text,
        findings,
        surrogates=FakeSynth(),
        locale="en_US",
        anchor="r1",
        gateway=None,
        llm_rewrite=False,
        plan=plan,
    )
    assert out == "Mail meera@example.com by 2026-01-05 via https://portal.example-bank.in/x"
    assert info["methods_used"] == {"surrogate": 1, "keep": 2}
    strict = resolve_policy("synthetic-strict", default_name="gdpr-pseudonymise")
    plan2 = build_plan(findings, strict, "synthesize", fpe_enabled=False)
    out2, info2 = await synthesize_text(
        text,
        findings,
        surrogates=FakeSynth(),
        locale="en_US",
        anchor="r1",
        gateway=None,
        llm_rewrite=False,
        plan=plan2,
    )
    assert out2 == "Mail meera@example.com by 2026-02-01 via [REDACTED]"
    assert info2["methods_used"] == {"surrogate": 2, "redact": 1}


async def test_a_rewrite_that_brings_a_name_part_back_fails_closed() -> None:
    """The model never saw the name; a rewrite that holds a part of it anyway is not used."""
    gw = RewritingGateway("{PERSON_NAME_1} ({EMAIL_1}) wrote to the Smith family before {DATE_1}.")
    out, info = await synthesize_text(
        TEXT,
        _findings(),
        surrogates=FakeSynth(),
        locale="en_US",
        anchor="r1",
        gateway=gw,
        llm_rewrite=True,
    )
    assert "Smith" not in out and info["llm_used"] is False and info["fallback"] == "verification"


class DeclaringSynth(FakeSynth):
    def __init__(self) -> None:
        self.declared: list[tuple[str, list[tuple[str, str]]]] = []

    def declare_record(self, anchor: str, originals: Any) -> None:
        self.declared.append((anchor, list(originals)))


async def test_the_record_values_are_declared_before_any_surrogate() -> None:
    synth = DeclaringSynth()
    await synthesize_text(
        TEXT,
        _findings(),
        surrogates=synth,
        locale="en_US",
        anchor="r1",
        gateway=None,
        llm_rewrite=False,
    )
    assert len(synth.declared) == 1 and synth.declared[0][0] == "r1"
    assert sorted(synth.declared[0][1]) == [
        ("DATE", "2026-01-05"),
        ("EMAIL", "ann.k@example-mail.in"),
        ("PERSON_NAME", "Ann Smith"),
    ]
