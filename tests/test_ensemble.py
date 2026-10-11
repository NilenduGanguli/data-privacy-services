from __future__ import annotations

import pytest

from dps.engine.detect.base import NerAdapter
from dps.engine.detect.ensemble import Detector, DetectorConfig
from dps.engine.detect.patterns import Hit
from dps.engine.detect.structural import KeyCandidate
from dps.engine.taxonomy import Sensitivity

SAMPLE = {
    "customer_id": "CUST-884120",
    "full_name": "Ananya Krishnan",
    "dob": "1988-03-14",
    "aadhaar": "2341 2341 2346",
    "pan": "4532 7811 2297 4417",  # Luhn-valid
    "email": "ananya.k@example-mail.in",
    "phone": "+91 98451 20377",
    "active": True,
    "risk_score": 712,
    "notes": "Called on 14 Mar about card ending 4417; mail ann.k@example-mail.in",
}


def _by_path(result):  # noqa: ANN001, ANN202 - test helper
    out: dict[str, list] = {}
    for f in result.findings:
        out.setdefault(f.locator.path, []).append(f)
    return out


def test_structured_record_findings() -> None:
    det = Detector(DetectorConfig(locale="en_IN"))
    res = det.detect(data=SAMPLE)
    by = _by_path(res)
    aadhaar = by["$.aadhaar"][0]
    assert aadhaar.entity_type == "AADHAAR" and aadhaar.locator.whole and aadhaar.score >= 0.98
    assert (
        any(d.startswith("checksum") for d in aadhaar.evidence)
        or "checksum:AADHAAR" in aadhaar.evidence
    )
    assert by["$.pan"][0].entity_type == "CARD_PAN"  # value shape beats the ambiguous key
    assert by["$.email"][0].entity_type == "EMAIL" and by["$.phone"][0].entity_type == "PHONE"
    assert by["$.dob"][0].entity_type == "DATE_OF_BIRTH"
    assert by["$.full_name"][0].entity_type == "PERSON_NAME"  # structural alone, 0.9 >= floor
    assert "$.active" not in by and "$.risk_score" not in by
    spans = by["$.notes"]
    assert any(f.entity_type == "EMAIL" and not f.locator.whole for f in spans)
    assert res.record_sensitivity is Sensitivity.CRITICAL


def test_numbers_under_sensitive_keys_and_threshold() -> None:
    det = Detector(DetectorConfig())
    assert det.detect(data={"ssn": 123456789}).findings[0].entity_type == "SSN"
    assert det.detect(data={"name": "Widget 3000"}).findings == []
    assert det.detect(data={"customer_id": "CUST-1"}).findings[0].entity_type == "CUSTOMER_ID"


def test_text_mode_yields_spans_on_root() -> None:
    res = Detector(DetectorConfig()).detect(text="Call (415) 555-0132 or mail a@b.co today")
    types = {f.entity_type: f for f in res.findings}
    assert {"PHONE", "EMAIL"} <= set(types)
    assert types["EMAIL"].locator.path == "$" and types["EMAIL"].locator.start is not None


class FakeNer(NerAdapter):
    languages = ("en",)

    def analyze(self, text: str, language: str) -> list[Hit]:
        i = text.find("Ann Smith")
        return [Hit("PERSON_NAME", i, i + 9, 0.85, "ner:fake", "ner:PERSON")] if i >= 0 else []


def test_ner_adapter_is_used_when_enabled() -> None:
    det = Detector(DetectorConfig(ner_enabled=True), ner=FakeNer())
    res = det.detect(text="Ann Smith lives here")
    assert res.findings[0].entity_type == "PERSON_NAME" and "ner:fake" in res.findings[0].detectors
    assert (
        Detector(DetectorConfig(ner_enabled=False), ner=FakeNer()).detect(text="Ann Smith").findings
        == []
    )


class FakeCustom:
    def declared(self, path: str) -> KeyCandidate | None:
        return (
            KeyCandidate("BUSINESS_SENSITIVE", 0.99, "declared:$.loan.score")
            if path == "$.loan.score"
            else None
        )

    def scan(self, text: str, key_hint: str | None) -> list[Hit]:
        i = text.find("EMP-")
        return (
            [Hit("EMPLOYEE_ID", i, i + 10, 0.8, "custom:emp", "custom-pattern:emp")]
            if i >= 0
            else []
        )


def test_custom_tier_declares_fields_and_adds_hits() -> None:
    det = Detector(DetectorConfig(), custom=FakeCustom())
    res = det.detect(data={"loan": {"score": 712}, "memo": "raised by EMP-123456 today"})
    by = _by_path(res)
    assert (
        by["$.loan.score"][0].entity_type == "BUSINESS_SENSITIVE"
        and by["$.loan.score"][0].locator.whole
    )
    memo = by["$.memo"][0]
    assert memo.entity_type == "EMPLOYEE_ID" and (memo.locator.start, memo.locator.end) == (10, 20)


def test_exactly_one_input_is_required() -> None:
    det = Detector(DetectorConfig())
    with pytest.raises(ValueError):
        det.detect()
    with pytest.raises(ValueError):
        det.detect(data={}, text="x")


def test_results_never_carry_the_values() -> None:
    dumped = Detector(DetectorConfig(locale="en_IN")).detect(data=SAMPLE).model_dump_json()
    for value in ("Ananya", "2341 2341 2346", "example-mail", "98451", "4417", "1988-03-14"):
        assert value not in dumped


def test_grouped_card_in_free_text_is_one_span() -> None:
    res = Detector(DetectorConfig()).detect(
        data={"memo": "paid with 4141 7058 6492 0836 yesterday"}
    )
    (finding,) = res.findings
    assert finding.entity_type == "CARD_PAN" and (finding.locator.start, finding.locator.end) == (
        10,
        29,
    )


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Aadhaar 2341 2341 2346 9845120377", [("AADHAAR", 8, 22), ("PHONE", 23, 33)]),
        ("Aadhaar no 2341 2341 2346 2 copies attached", [("AADHAAR", 11, 25)]),
        ("Aadhaar 2341-2341-2346 12 Mar 2020", [("AADHAAR", 8, 22), ("DATE", 23, 34)]),
        ("ref 1 2341 2341 2346", [("AADHAAR", 6, 20)]),
    ],
)
def test_aadhaar_next_to_another_number_is_a_finding(
    text: str, expected: list[tuple[str, int, int]]
) -> None:
    res = Detector(DetectorConfig(locale="en_IN")).detect(text=text)
    assert [(f.entity_type, f.locator.start, f.locator.end) for f in res.findings] == expected
