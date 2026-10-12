"""The report says what the leakage sweep could not see (M0 verification round 1, B1).

The sweep checks the originals of the values the detectors found. With NER off, a name in free
text without a cue is never found, so "passed" alone overstated it: ``verification.coverage``
now names the detectors that ran and the gaps, and ``status`` is ``partial`` when there are gaps.
"""

from __future__ import annotations

import json

from dps.config import Settings
from dps.engine.findings import Finding, Locator
from dps.engine.service import Payload, PrivacyEngine, RequestOptions
from dps.engine.taxonomy import Sensitivity
from dps.engine.verify import (
    NER_GAP,
    Coverage,
    Verification,
    assess_coverage,
    field_name_only_entities,
    label_only_entities,
    label_only_gap,
    name_only_gap,
    verify_output,
)

TEXT = "Spoke to the customer about her card ending 1111 and the new address."
#: Free text also hides every type a field name alone reveals (integrated verify r1, B2).
NAME_ONLY = name_only_gap(field_name_only_entities(ner=False))
NAME_ONLY_NER = name_only_gap(field_name_only_entities(ner=True))
#: ... and the types it finds in text only next to their label (an account number alone).
LABEL_ONLY = label_only_gap(label_only_entities())


def _finding(path: str, entity: str, start: int | None = None, end: int | None = None) -> Finding:
    return Finding(
        locator=Locator(path=path, start=start, end=end),
        entity_type=entity,
        score=0.9,
        detectors=["t"],
        sensitivity=Sensitivity.MEDIUM,
    )


def test_free_text_swept_without_ner_is_partial_coverage() -> None:
    cov = assess_coverage(text=TEXT, findings=[], kept=set(), detectors=["patterns"], ner="off")
    assert cov.free_text_fields == 1 and not cov.complete
    assert cov.gaps == [NER_GAP["off"], NAME_ONLY, LABEL_ONLY]
    assert cov.basis == "detected-values" and cov.ner == "off"
    result = verify_output([], TEXT, coverage=cov)
    assert result.passed and result.status == "partial" and result.coverage == cov


def test_ner_on_closes_the_names_gap_but_not_the_field_name_only_types() -> None:
    cov = assess_coverage(
        text=TEXT, findings=[], kept=set(), detectors=["patterns", "ner"], ner="on"
    )
    assert cov.gaps == [NAME_ONLY_NER, LABEL_ONLY] and not cov.complete
    assert verify_output([], TEXT, coverage=cov).status == "partial"
    # a policy whose recognizers find every one of them in text closes it
    every = assess_coverage(
        text=TEXT,
        findings=[],
        kept=set(),
        detectors=["patterns", "ner", "custom"],
        ner="on",
        name_only=[],
        label_only=[],
    )
    assert every.complete and every.gaps == []
    assert verify_output([], TEXT, coverage=every).status == "passed"


def test_ner_that_failed_to_load_is_a_gap_too() -> None:
    cov = assess_coverage(
        text=TEXT, findings=[], kept=set(), detectors=["patterns"], ner="unavailable"
    )
    assert not cov.complete and cov.gaps == [NER_GAP["unavailable"], NAME_ONLY, LABEL_ONLY]


def test_json_counts_only_text_leaves_that_were_not_wholly_replaced() -> None:
    data = {
        "customer_id": "CUST-884120",
        "full_name": "Ananya Krishnan",  # two words, but wholly replaced
        "address": "14 Lavelle Road, Bengaluru 560001",  # wholly replaced
        "city": "Bengaluru",  # one word: a code-like value, not free text
        "notes": "Called on 2026-03-14 about card ending 1111",  # free text with a span finding
        "tags": ["vip customer", "kyc due"],
        "risk_score": 712,
    }
    findings = [
        _finding("$.full_name", "PERSON_NAME"),
        _finding("$.address", "STREET_ADDRESS"),
        _finding("$.notes", "DATE", 10, 20),
    ]
    cov = assess_coverage(
        data=data, findings=findings, kept=set(), detectors=["structural", "patterns"], ner="off"
    )
    assert cov.free_text_fields == 3 and not cov.complete  # notes + two tags
    kept_name = assess_coverage(
        data=data,
        findings=findings,
        kept={("$.full_name", None, None)},  # kept in clear: its text is not replaced
        detectors=["structural", "patterns"],
        ner="off",
    )
    assert kept_name.free_text_fields == 4
    structured = {"customer_id": "CUST-884120", "full_name": "Ananya Krishnan", "n": 1}
    cov2 = assess_coverage(
        data=structured,
        findings=[_finding("$.full_name", "PERSON_NAME")],
        kept=set(),
        detectors=["structural", "patterns"],
        ner="off",
    )
    assert cov2.free_text_fields == 0 and cov2.complete and cov2.gaps == []


def test_status_is_derived_and_failed_wins() -> None:
    gap = Coverage(
        detectors=["patterns"], ner="off", free_text_fields=1, complete=False, gaps=["x"]
    )
    assert Verification(passed=True, checked=1).status == "passed"
    assert Verification(passed=True, checked=1, coverage=gap).status == "partial"
    failed = Verification(
        passed=False, checked=1, hits=[{"path": "$", "entity_type": "EMAIL"}], coverage=gap
    )
    assert failed.status == "failed"
    assert Verification(passed=True, checked=1, status="failed").status == "passed"  # never trusted


def test_gap_statements_are_constant_and_value_free() -> None:
    for gap in NER_GAP.values():
        assert "Ananya" not in gap and "NER" in gap
    assert NAME_ONLY.endswith(", ".join(field_name_only_entities(ner=False)))
    assert LABEL_ONLY.endswith("ACCOUNT_NUMBER, CUSTOMER_ID, STREET_ADDRESS")


def _engine(**kw: object) -> PrivacyEngine:
    s = Settings(
        _env_file=None, llm_provider="stub", ner_enabled=False, default_locale="en_IN", **kw
    )  # type: ignore[arg-type]
    return PrivacyEngine(s)


def test_engine_reports_coverage_for_text_and_structured_json() -> None:
    engine = _engine()
    text = engine.obfuscate(Payload(text=TEXT), None, RequestOptions())
    v = text.report.verification
    assert v is not None and v.status == "partial" and v.coverage is not None
    assert v.coverage.ner == "off" and v.coverage.gaps == [NER_GAP["off"], NAME_ONLY, LABEL_ONLY]
    assert "patterns" in v.coverage.detectors and "ner" not in v.coverage.detectors
    record = {
        "customer_id": "CUST-884120",
        "full_name": "Ananya Krishnan",
        "email": "a.k@example.in",
    }
    structured = engine.obfuscate(Payload(data=record), None, RequestOptions())
    sv = structured.report.verification
    assert sv is not None and sv.status == "passed" and sv.coverage is not None
    assert sv.coverage.complete and "structural" in sv.coverage.detectors
    dumped = json.dumps(text.report.model_dump(mode="json"))
    assert '"coverage"' in dumped and '"status": "partial"' in dumped


async def test_synthesize_reports_coverage_too() -> None:
    out = await _engine().synthesize(Payload(text=TEXT), None, RequestOptions())
    v = out.report.verification
    assert v is not None and v.status == "partial" and v.coverage is not None
    assert v.coverage.gaps == [NER_GAP["off"], NAME_ONLY, LABEL_ONLY]
