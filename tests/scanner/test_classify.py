"""Task 9: field and rendered-text classification (spec §8 "Classify")."""

from __future__ import annotations

import time
from typing import Any

import pytest

from dps.config import Settings
from dps.engine.detect.ensemble import Detector
from dps.engine.policy import load_preset
from dps.engine.service import PrivacyEngine
from dps.layers.scanner.classify import (
    autocomplete_token,
    classify_field,
    classify_rendered,
    compile_html_pattern,
    pattern_entities,
    placeholder_shape,
    redact_text,
    value_classifier,
)
from dps.layers.scanner.models import FieldFacts, TextFacts

POLICY = load_preset("gdpr-pseudonymise")


@pytest.fixture
def det(settings: Settings) -> Detector:
    return PrivacyEngine(settings).detector(POLICY, "en_IN")


def F(**kw: Any) -> FieldFacts:  # noqa: N802
    base: dict[str, Any] = dict(
        key="0:1",
        field_no=1,
        form_index=1,
        frame_index=0,
        in_shadow=False,
        tag="input",
        input_type="text",
        name=None,
        id=None,
        label="",
        label_source="none",
        placeholder=None,
        title=None,
        aria_label=None,
        autocomplete=None,
        pattern=None,
        maxlength=None,
        minlength=None,
        required=False,
        readonly=False,
        disabled=False,
        visible=True,
        inputmode=None,
        selector="input",
    )
    return FieldFacts.model_validate(base | kw)


@pytest.mark.parametrize(
    "attr,expected",
    [
        ("section-a shipping street-address", ("street-address", "named")),
        ("off", (None, "off")),
        (None, (None, "absent")),
        ("on", (None, "on")),
        ("bogus-token", (None, "unknown")),
    ],
)
def test_autocomplete_token(attr: str | None, expected: tuple[str | None, str]) -> None:
    assert autocomplete_token(attr) == expected


def test_autocomplete_token_wins_with_high_confidence(det: Detector) -> None:
    c = classify_field(F(label="Full name", autocomplete="name"), detector=det, min_score=0.6)
    assert c.entity_type == "PERSON_NAME" and c.confidence == 0.99
    assert {"token", "label"} <= set(c.detectors)
    assert "autocomplete:name" in c.evidence


def test_label_and_pattern_classify_aadhaar(det: Detector) -> None:
    c = classify_field(
        F(label="Aadhaar number", pattern=r"\d{4} \d{4} \d{4}"), detector=det, min_score=0.6
    )
    assert c.entity_type == "AADHAAR" and c.confidence >= 0.98 and c.sensitivity == "CRITICAL"
    assert "pattern:AADHAAR" in c.evidence and any(e.startswith("label:") for e in c.evidence)


def test_label_pattern_and_maxlength_classify_pan(det: Detector) -> None:
    c = classify_field(
        F(label="PAN", maxlength=10, pattern="[A-Z]{5}[0-9]{4}[A-Z]"), detector=det, min_score=0.6
    )
    assert c.entity_type == "PAN_IN" and {"label", "pattern", "maxlength"} <= set(c.detectors)


def test_input_type_alone(det: Detector) -> None:
    assert classify_field(F(input_type="email"), detector=det, min_score=0.6).entity_type == "EMAIL"
    pw = classify_field(F(input_type="password"), detector=det, min_score=0.6)
    assert pw.entity_type == "PASSWORD"


def test_prefilled_value_classifies_an_ambiguous_label_without_leaking(det: Detector) -> None:
    c = classify_field(
        F(label="Contact detail", value="ananya.k@example-mail.in"), detector=det, min_score=0.6
    )
    assert c.entity_type == "EMAIL" and "value" in c.detectors
    assert not any("ananya" in e for e in c.evidence)
    assert "ananya" not in repr(c) and "example-mail" not in c.model_dump_json()


def test_below_threshold_is_unclassified(det: Detector) -> None:
    c = classify_field(F(label="Nominee name"), detector=det, min_score=0.6)
    assert c.entity_type is None and c.detectors == [] and c.confidence < 0.6


def test_labels_with_dots_and_brackets_are_read_whole(det: Detector) -> None:
    # a label is page text, not a JSON path: "No." or "(e.g. …)" must not cut it to its tail
    c = classify_field(F(label="Aadhaar No. (e.g. 1234)"), detector=det, min_score=0.6)
    assert c.entity_type == "AADHAAR"


def test_patterns_are_probed_safely() -> None:
    assert compile_html_pattern("(a+)+") is None and compile_html_pattern("[") is None
    assert pattern_entities(r"\d{6}") == ["POSTAL_CODE"]
    assert pattern_entities(r"\d{9,18}") == []  # matches many probes: no signal
    assert pattern_entities("[A-Z]{4}0[A-Z0-9]{6}") == ["IFSC"]


@pytest.mark.parametrize(
    "pattern",
    [
        "((a+))+",  # nested quantifier behind an extra group
        "(?:a{1,9}){1,9}x",  # nested counted repeats
        "(a|a)*b",  # alternation under a star
        r"\d*\d*\d*\d*\d*\d*\d*\d*\d*\d*x",  # many overlapping variable repeats
        "(a|aa)?" * 30 + "b",  # optional alternations, each doubling the paths
        r"(a)\1",  # back-reference
        "(?=a)a",  # look-ahead
        "a" * 201,  # too long
    ],
)
def test_hostile_patterns_are_refused(pattern: str) -> None:
    assert compile_html_pattern(pattern) is None
    t0 = time.perf_counter()
    assert pattern_entities(pattern) == []
    assert time.perf_counter() - t0 < 0.5


@pytest.mark.parametrize(
    "pattern",
    [
        r"[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}",
        r"\+?[0-9 ]{10,14}",
        r"\d{2}/\d{2}/\d{4}",
        r"(?:\d{3}-){2}\d{4}",
        "[A-Za-z ]+",
    ],
)
def test_ordinary_patterns_compile(pattern: str) -> None:
    assert compile_html_pattern(pattern) is not None


def test_rendered_values_and_row_header_hints(det: Detector) -> None:
    texts = [
        TextFacts(frame_index=0, i=0, text="Full name", in_table=True, label_hint=None),
        TextFacts(
            frame_index=0, i=1, text="Ananya Krishnan", in_table=True, label_hint="Full name"
        ),
        TextFacts(
            frame_index=0, i=2, text="Questions? Call us any time.", in_table=False, label_hint=None
        ),
        TextFacts(
            frame_index=0, i=3, text=" ananya.k@example-mail.in ", in_table=False, label_hint=None
        ),
    ]
    found = {(r.i, r.entity_type) for r in classify_rendered(texts, detector=det)}
    assert found == {(1, "PERSON_NAME"), (3, "EMAIL")}


def test_rendered_findings_carry_offsets_not_text(det: Detector) -> None:
    texts = [
        TextFacts(
            frame_index=0,
            i=0,
            text="Mail: ananya.k@example-mail.in",
            in_table=False,
            label_hint=None,
        )
    ]
    (r,) = classify_rendered(texts, detector=det)
    assert texts[0].text[r.start : r.end] == "ananya.k@example-mail.in"
    assert "ananya" not in r.model_dump_json()
    assert classify_rendered([], detector=det) == []


def test_redaction_helpers(det: Detector) -> None:
    assert redact_text("Welcome back, ananya.k@example-mail.in", det) == "Welcome back, <EMAIL>"
    assert redact_text(None, det) is None
    assert len(redact_text("word " * 100, det) or "") <= 120
    classify = value_classifier(det)
    assert classify("ananya.k@example-mail.in") == "EMAIL" and classify("en") is None
    assert placeholder_shape("XXXX 1234 abcd") == "XXXX 9999 xxxx"
    assert placeholder_shape(None) is None and len(placeholder_shape("9" * 40) or "") == 24


def test_engine_exposes_its_detector(settings: Settings) -> None:
    assert isinstance(PrivacyEngine(settings).detector(POLICY, "en_US"), Detector)
