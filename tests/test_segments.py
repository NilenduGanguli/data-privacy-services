from __future__ import annotations

from typing import Any

from dps.config import Settings
from dps.engine.detect.ensemble import Detector, DetectorConfig
from dps.engine.planner import build_plan
from dps.engine.policy import resolve_policy
from dps.engine.service import Payload, PrivacyEngine, RequestOptions
from dps.engine.synth.llm_text import placeholder_segments, synthesize_text, to_placeholders
from dps.engine.transform.apply import ApplyOptions, ApplyResult, apply_plan
from dps.engine.transform.crypto import generate_key_material
from dps.llm.gateway import LlmGateway, LlmResult

TEXT = "Mail ananya.k@example-mail.in or call +91 98451 20377 today"


def _apply(*, text: str | None = None, data: Any = None, style: str = "natural") -> ApplyResult:
    policy = resolve_policy(
        {"preset": "gdpr-pseudonymise", "treat": {"EMAIL": {"method": "det_token"}}},
        default_name="gdpr-pseudonymise",
    )
    found = Detector(DetectorConfig()).detect(data=data, text=text)
    plan = build_plan(found.findings, policy, "obfuscate", fpe_enabled=False)
    options = ApplyOptions(
        policy=policy,
        key=generate_key_material(),
        surrogates=None,
        fpe_enabled=False,
        token_style=style,
    )
    return apply_plan(plan=plan, options=options, data=data, text=text)


def test_span_segments_slice_the_output() -> None:
    result = _apply(text=TEXT)
    spans = [s for s in result.segments if s.start is not None]
    assert {s.entity_type for s in spans} >= {"EMAIL", "PHONE"}
    for s in spans:
        assert result.output[s.out_start : s.out_end] == s.replacement
        assert s.replacement != TEXT[s.start : s.end]


def test_envelope_segments_cover_the_envelope() -> None:
    result = _apply(text=TEXT, style="envelope")
    email = next(s for s in result.segments if s.entity_type == "EMAIL")
    assert result.output[email.out_start : email.out_end].startswith("[[PII:EMAIL:")


def test_whole_value_segments() -> None:
    result = _apply(data={"email": "ananya.k@example-mail.in", "n": 1})
    seg = next(s for s in result.segments if s.path == "$.email")
    assert seg.start is None and seg.out_start is None and seg.replacement == result.output["email"]


class FakeSynth:
    def surrogate(self, entity_type: str, original: str, locale: str, anchor: str) -> str:
        return f"<{entity_type}:{len(original)}>"


async def test_placeholder_segments_match_the_output() -> None:
    text = "Mail a.b@example-mail.in or c.d@example-mail.in today"
    findings = Detector(DetectorConfig()).detect(text=text).findings
    output, info = await synthesize_text(
        text,
        findings,
        surrogates=FakeSynth(),
        locale="en_US",
        anchor="a",
        gateway=None,
        llm_rewrite=False,
    )
    segments = info["segments"]
    assert len(segments) == 2 and all(
        output[s.out_start : s.out_end] == s.replacement for s in segments
    )


async def test_placeholder_shaped_text_keeps_exact_segments() -> None:
    # M0 as built fills only this text's own labels and leaves any other placeholder-shaped
    # string as it is (tests/test_llm_text.py), so the deterministic output always rebuilds
    # and the segments stay exact (M1-DEVIATIONS, Task 3).
    text = "Template {EMAIL_9} then a.b@example-mail.in"
    findings = Detector(DetectorConfig()).detect(text=text).findings
    output, info = await synthesize_text(
        text,
        findings,
        surrogates=FakeSynth(),
        locale="en_US",
        anchor="a",
        gateway=None,
        llm_rewrite=False,
    )
    (seg,) = info["segments"]
    assert output.startswith("Template {EMAIL_9} then ")
    assert text[seg.start : seg.end] == "a.b@example-mail.in"
    assert output[seg.out_start : seg.out_end] == seg.replacement == "<EMAIL:19>"


def test_placeholder_segments_refuse_an_output_they_cannot_rebuild() -> None:
    text = "Mail a.b@example-mail.in today"
    ph = to_placeholders(text, Detector(DetectorConfig()).detect(text=text).findings)
    (label,) = ph.mapping
    fills, methods = {label: "<x>"}, {label: "surrogate"}
    (seg,) = placeholder_segments(text, ph, "Mail <x> today", fills, methods)
    assert (seg.start, seg.end, seg.out_start, seg.out_end, seg.method) == (
        5,
        24,
        5,
        8,
        "surrogate",
    )
    assert placeholder_segments(text, ph, "Write to <x> today", fills, methods) == []


async def test_placeholder_segments_follow_the_plan() -> None:
    text = "Mail ann.k@example-mail.in by 2026-01-05 via https://portal.example-bank.in/x"
    findings = Detector(DetectorConfig()).detect(text=text).findings
    realistic = resolve_policy("synthetic-realistic", default_name="gdpr-pseudonymise")
    plan = build_plan(findings, realistic, "synthesize", fpe_enabled=False)
    output, info = await synthesize_text(
        text,
        findings,
        surrogates=FakeSynth(),
        locale="en_US",
        anchor="a",
        gateway=None,
        llm_rewrite=False,
        plan=plan,
    )
    segments = info["segments"]
    assert sorted(s.method for s in segments) == ["keep", "keep", "surrogate"]
    assert all(output[s.out_start : s.out_end] == s.replacement for s in segments)


class RewritingGateway(LlmGateway):
    def __init__(self, text: str) -> None:
        super().__init__(Settings(_env_file=None, llm_provider="stub"))
        self._text = text

    async def complete(self, messages, **kw):  # noqa: ANN001, ANN003, ANN201
        return LlmResult(text=self._text, provider="stub", model="stub")


async def test_a_used_rewrite_has_no_segments() -> None:
    text = "Mail a.b@example-mail.in today"
    findings = Detector(DetectorConfig()).detect(text=text).findings
    output, info = await synthesize_text(
        text,
        findings,
        surrogates=FakeSynth(),
        locale="en_US",
        anchor="a",
        gateway=RewritingGateway("Please write to {EMAIL_1} soon."),
        llm_rewrite=True,
    )
    assert info["llm_used"] is True and output == "Please write to <EMAIL:19> soon."
    assert info["segments"] == []


async def test_service_returns_segments_only_when_asked() -> None:
    settings = Settings(_env_file=None, llm_provider="stub", fpe_enabled=False, ner_enabled=False)
    engine = PrivacyEngine(settings, gateway=LlmGateway(settings))
    assert engine.obfuscate(Payload(text=TEXT), None, RequestOptions()).segments is None
    asked = engine.obfuscate(Payload(text=TEXT), None, RequestOptions(return_segments=True))
    assert asked.segments and all(
        asked.result[g.out_start : g.out_end] == g.replacement for g in asked.segments
    )
    syn = await engine.synthesize(
        Payload(text=TEXT), "synthetic-realistic", RequestOptions(return_segments=True)
    )
    assert syn.segments and all(
        syn.result[g.out_start : g.out_end] == g.replacement for g in syn.segments
    )
