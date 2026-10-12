from __future__ import annotations

import pytest

from dps.engine.detect.custom import (
    DeclaredField,
    PatternSpecModel,
    PolicyCustomDetector,
    RecognizerSpec,
    load_recognizers_yaml,
)
from dps.engine.taxonomy import Sensitivity, entity_info

EMP = RecognizerSpec(
    name="employee-id",
    supported_entity="EMPLOYEE_ID",
    patterns=[{"name": "emp", "regex": r"\bEMP-\d{6}\b", "score": 0.8}],
    context=["employee"],
)
CODES = RecognizerSpec(
    name="project-codes",
    supported_entity="PROJECT_CODE",
    deny_list=["Blue Falcon", "Nightjar"],
    sensitivity=Sensitivity.HIGH,
)


def test_declared_fields_match_exact_and_glob() -> None:
    det = PolicyCustomDetector(
        [],
        [
            DeclaredField(path="$.loan.internal_score", entity_type="BUSINESS_SENSITIVE"),
            DeclaredField(path="$.accounts[*].iban", entity_type="IBAN", score=0.9),
        ],
    )
    assert det.declared("$.loan.internal_score").entity_type == "BUSINESS_SENSITIVE"  # type: ignore[union-attr]
    assert det.declared("$.accounts[3].iban").score == 0.9  # type: ignore[union-attr]
    assert det.declared("$.other") is None


def test_custom_pattern_with_context_boost_and_deny_list() -> None:
    det = PolicyCustomDetector([EMP, CODES], [])
    plain = det.scan("id EMP-123456", None)
    boosted = det.scan("employee EMP-123456", None)
    assert plain[0].entity_type == "EMPLOYEE_ID" and plain[0].score == 0.8
    assert boosted[0].score == pytest.approx(0.9)
    hits = det.scan("codename blue falcon shipped", None)
    assert hits[0].entity_type == "PROJECT_CODE" and hits[0].score == 0.9
    assert entity_info("PROJECT_CODE").sensitivity is Sensitivity.HIGH


def test_specs_are_validated() -> None:
    with pytest.raises(ValueError):
        RecognizerSpec(
            name="bad",
            supported_entity="EMPLOYEE_ID",
            patterns=[{"name": "x", "regex": "(", "score": 0.5}],
        )
    with pytest.raises(ValueError):
        RecognizerSpec(name="bad", supported_entity="lowercase", deny_list=["x"])
    with pytest.raises(ValueError):
        RecognizerSpec(name="empty", supported_entity="X")


def test_yaml_loader(tmp_path) -> None:
    f = tmp_path / "rec.yaml"
    f.write_text(
        "recognizers:\n  - name: badge\n    supported_entity: BADGE_ID\n    patterns:\n"
        "      - {name: b, regex: 'B-\\d{4}', score: 0.7}\n",
        encoding="utf-8",
    )
    specs = load_recognizers_yaml(f)
    assert specs[0].supported_entity == "BADGE_ID" and specs[0].patterns[0].score == 0.7


def test_declared_globs_follow_json_path_syntax() -> None:
    det = PolicyCustomDetector(
        [],
        [
            DeclaredField(path="$.accounts[*].iban", entity_type="IBAN"),
            DeclaredField(path="$.*.tax_ref", entity_type="TAX_REF"),
            DeclaredField(path="$.tags[0]", entity_type="CASE_ID"),
        ],
    )
    assert det.declared("$.accounts[12].iban") is not None
    assert det.declared("$.accounts.iban") is None
    assert det.declared("$.accounts[3].iban_note") is None
    assert det.declared("$.customer.tax_ref").entity_type == "TAX_REF"  # type: ignore[union-attr]
    assert det.declared("$.a.b.tax_ref") is None  # one * is one segment
    assert det.declared("$.tags[0]") is not None and det.declared("$.tags[1]") is None


def test_sensitivity_on_a_builtin_entity_is_refused_at_validation() -> None:
    # The taxonomy owns built-in tiers; register_entity would raise later, inside a request.
    with pytest.raises(ValueError, match="built-in"):
        RecognizerSpec(
            name="x", supported_entity="IBAN", deny_list=["x"], sensitivity=Sensitivity.LOW
        )
    with pytest.raises(ValueError, match="built-in"):
        DeclaredField(path="$.a", entity_type="EMAIL", sensitivity=Sensitivity.CRITICAL)
    assert RecognizerSpec(name="x", supported_entity="IBAN", deny_list=["x"]).sensitivity is None
    assert PolicyCustomDetector([], [DeclaredField(path="$.a", entity_type="IBAN")]).declared("$.a")


def test_validation_errors_never_echo_policy_terms() -> None:
    # Deny-list entries and patterns are the sensitive terms themselves.
    with pytest.raises(ValueError) as err:
        RecognizerSpec(
            name="codes",
            supported_entity="IBAN",
            deny_list=["Blue Falcon"],
            sensitivity=Sensitivity.HIGH,
        )
    assert "Blue Falcon" not in str(err.value)
    with pytest.raises(ValueError) as err:
        RecognizerSpec(
            name="r",
            supported_entity="X",
            patterns=[{"name": "p", "regex": "(Blue Falcon", "score": 0.5}],
        )
    assert "Blue Falcon" not in str(err.value)


def test_empty_matches_and_blank_deny_entries_never_become_hits() -> None:
    zed = RecognizerSpec(
        name="z", supported_entity="ZED", patterns=[{"name": "z", "regex": r"\d*", "score": 0.9}]
    )
    hits = PolicyCustomDetector([zed], []).scan("ab 12", None)
    assert [(h.start, h.end) for h in hits] == [(3, 5)]
    with pytest.raises(ValueError):
        RecognizerSpec(name="blank", supported_entity="ZED", deny_list=["Nightjar", "  "])


def test_allow_list_suppresses_exact_matches() -> None:
    rec = RecognizerSpec(
        name="emp",
        supported_entity="EMPLOYEE_ID",
        patterns=(PatternSpecModel(name="emp", regex=r"\bEMP-\d{6}\b", score=0.9),),
        allow_list=("EMP-000000",),
    )
    text = "EMP-204417 escalated; fixture EMP-000000 is seeded"
    assert [text[h.start : h.end] for h in PolicyCustomDetector([rec], []).scan(text, None)] == [
        "EMP-204417"
    ]


def test_allow_list_applies_to_deny_list_hits_and_refuses_blank_entries() -> None:
    rec = RecognizerSpec(
        name="codes",
        supported_entity="PROJECT_CODE",
        deny_list=("Nightjar", "Blue Falcon"),
        allow_list=("Nightjar",),
    )
    text = "Nightjar ships before Blue Falcon"
    hits = PolicyCustomDetector([rec], []).scan(text, None)
    assert [text[h.start : h.end] for h in hits] == ["Blue Falcon"]
    with pytest.raises(ValueError, match="non-blank"):
        RecognizerSpec(
            name="codes", supported_entity="PROJECT_CODE", deny_list=("x",), allow_list=(" ",)
        )


def test_allow_lists_are_per_recognizer_even_with_one_name() -> None:
    pattern = PatternSpecModel(name="emp", regex=r"\bEMP-\d{6}\b", score=0.9)
    seeded = RecognizerSpec(
        name="emp", supported_entity="EMPLOYEE_ID", patterns=(pattern,), allow_list=("EMP-000000",)
    )
    plain = RecognizerSpec(name="emp", supported_entity="STAFF_ID", patterns=(pattern,))
    hits = PolicyCustomDetector([seeded, plain], []).scan("EMP-000000", None)
    assert [h.entity_type for h in hits] == ["STAFF_ID"]
