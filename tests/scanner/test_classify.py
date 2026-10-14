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
    classify_fields,
    classify_rendered,
    compile_html_pattern,
    pattern_entities,
    placeholder_shape,
    redact_text,
    value_classifier,
)
from dps.layers.scanner.models import FieldFacts, OptionFacts, TextFacts

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
    # a bare "Name" is a weak cue (0.55) on its own; "Nominee name" is not any more (below)
    c = classify_field(F(label="Name"), detector=det, min_score=0.6)
    assert c.entity_type is None and c.detectors == [] and c.confidence < 0.6


@pytest.mark.parametrize(
    "label,entity",
    [
        ("Nominee name", "PERSON_NAME"),
        ("Account holder", "PERSON_NAME"),
        ("Beneficiary name", "PERSON_NAME"),
        ("Address line 1", "STREET_ADDRESS"),
        ("Document number", "NATIONAL_ID"),
    ],
)
def test_role_names_address_lines_and_document_numbers_are_personal(
    det: Detector, label: str, entity: str
) -> None:
    """Main's discovery fix (integrated verify r1, B1) taught the shared structural dictionary
    whose name a column holds, numbered address lines and document numbers; a form label is read
    with the same dictionary."""
    c = classify_field(F(label=label), detector=det, min_score=0.6)
    assert c.entity_type == entity and c.detectors == ["label"]


def _select(
    key: str, field_no: int, label: str, options: list[tuple[str, str, bool]], **kw: Any
) -> FieldFacts:
    return F(
        key=key,
        field_no=field_no,
        tag="select",
        input_type="select-one",
        label=label,
        options=[
            OptionFacts(value=v, text=t, disabled=False, selected=chosen)
            for v, t, chosen in options
        ],
        **kw,
    )


CHANNEL = [("email", "E-mail", True), ("mobile", "Mobile", False)]


@pytest.mark.parametrize(
    "value_field",
    [
        {"label": "Value"},
        {"label": "Contact value"},
        {"label": "", "name": "contact_value"},
        {"label": "Your contact details"},
    ],
)
def test_a_contact_value_next_to_a_channel_is_mixed_pii(
    det: Detector, value_field: dict[str, Any]
) -> None:
    """Main's mixed-PII rule by name: a ``value`` (``contact value``, ``contact details``…) next to
    a ``channel`` (``contact method``…) field of the same form holds an e-mail on one record and a
    phone number on the next — never "not personal", even while it is empty."""
    fields = [
        _select("0:1", 1, "Preferred contact method", CHANNEL),
        F(key="0:2", field_no=2, **value_field),
    ]
    classes = classify_fields(fields, detector=det, min_score=0.6)
    c = classes["0:2"]
    assert c.entity_type == "MIXED_PII" and c.confidence == 0.7 and c.sensitivity == "MEDIUM"
    assert c.detectors == ["name"] and c.evidence == ["mixed:value_next_to_channel"]
    assert classes["0:1"].entity_type is None  # the channel itself says nothing personal


def test_a_value_without_a_channel_in_its_form_is_not_mixed(det: Detector) -> None:
    lone = classify_fields([F(key="0:2", field_no=2, label="Value")], detector=det, min_score=0.6)
    assert lone["0:2"].entity_type is None
    elsewhere = classify_fields(
        [
            _select("0:1", 1, "Contact method", CHANNEL, form_index=1),
            F(key="0:2", field_no=2, form_index=2, label="Value"),
            F(key="0:3", field_no=3, form_index=1, label="Loan value"),  # a word, not the name
        ],
        detector=det,
        min_score=0.6,
    )
    assert elsewhere["0:2"].entity_type is None and elsewhere["0:3"].entity_type is None
    above = classify_fields(
        [_select("0:1", 1, "Contact method", CHANNEL), F(key="0:2", field_no=2, label="Value")],
        detector=det,
        min_score=0.75,  # the policy's bar is above the name rule's 0.7
    )
    assert above["0:2"].entity_type is None


def test_a_field_showing_personal_data_is_never_non_personal(det: Detector) -> None:
    """Main's kept-with-hits rule: personal data in what a field shows makes it personal even
    when its label says nothing — free text with a phone number in it, a selected option that is
    an e-mail address. Evidence names entity types only."""
    notes = F(
        key="0:4",
        field_no=4,
        tag="textarea",
        input_type="textarea",
        label="Notes",
        value="Please call me on +91 98451 20377 after six",
    )
    chosen = _select(
        "0:5", 5, "Choose", [("1", "ananya.k@example-mail.in", True), ("2", "Other", False)]
    )
    plain = F(key="0:6", field_no=6, label="Notes", value="Deliver after six")
    pw = F(key="0:7", field_no=7, input_type="password", label="Notes")
    classes = classify_fields([notes, chosen, plain, pw], detector=det, min_score=0.6)
    free = classes["0:4"]
    assert free.entity_type == "FREE_TEXT" and free.detectors == ["value"]
    assert free.evidence == ["value_span:PHONE"] and free.sensitivity is not None
    assert classes["0:5"].entity_type == "EMAIL" and classes["0:5"].evidence == ["value:EMAIL"]
    assert classes["0:6"].entity_type is None and classes["0:7"].entity_type == "PASSWORD"
    dumped = " ".join(c.model_dump_json() for c in classes.values())
    assert "98451" not in dumped and "ananya" not in dumped and "example-mail" not in dumped


def test_classify_fields_keeps_what_classify_field_decides(det: Detector) -> None:
    fields = [
        F(key="0:1", field_no=1, label="Aadhaar number"),
        F(key="0:2", field_no=2, label="Full name", autocomplete="name"),
    ]
    classes = classify_fields(fields, detector=det, min_score=0.6)
    for f in fields:
        assert classes[f.key] == classify_field(f, detector=det, min_score=0.6)


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
