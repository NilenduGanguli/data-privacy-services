from __future__ import annotations

from dps.engine.findings import Finding, Locator
from dps.engine.taxonomy import Sensitivity
from dps.evaluation.corpus import CorpusRecord, Label
from dps.evaluation.scoring import evaluate, render_markdown, score_record

TEXT = "Ann Smith mailed a@b.co about card 4111 1111 1111 1111 on 2024-01-02"


def finding(
    entity: str, start: int | None, end: int | None, path: str = "$", score: float = 0.9
) -> Finding:
    return Finding(
        locator=Locator(path=path, start=start, end=end),
        entity_type=entity,
        score=score,
        detectors=["test"],
        sensitivity=Sensitivity.HIGH,
    )


def test_score_record_counts_tp_fp_fn_and_confusions() -> None:
    record = CorpusRecord(
        id="t",
        jurisdiction="US",
        locale="en_US",
        kind="text",
        text=TEXT,
        labels=[
            Label(start=0, end=9, entity_type="PERSON_NAME"),
            Label(start=17, end=23, entity_type="EMAIL"),
            Label(start=35, end=54, entity_type="CARD_PAN"),
            Label(start=58, end=68, entity_type="DATE"),
        ],
    )
    counts, confusion = score_record(
        record,
        [
            finding("EMAIL", 17, 23),  # exact → TP
            finding("CARD_PAN", 35, 50),  # IoU 15/19 → TP
            finding("AADHAAR", 58, 68),  # right span, wrong type → FN DATE, FP AADHAAR, confusion
            finding("PHONE", 24, 29),  # nothing there → FP
        ],
    )
    assert counts["EMAIL"].tp == 1 and counts["CARD_PAN"].tp == 1
    assert counts["PERSON_NAME"].fn == 1 and counts["DATE"].fn == 1
    assert counts["AADHAAR"].fp == 1 and counts["PHONE"].fp == 1
    assert confusion == {("DATE", "AADHAAR"): 1}


def test_whole_value_labels_match_whole_findings() -> None:
    record = CorpusRecord(
        id="j",
        jurisdiction="US",
        locale="en_US",
        kind="json",
        data={"ssn": "123-45-6789", "notes": "x"},
        labels=[Label(path="$.ssn", entity_type="SSN")],
    )
    counts, _ = score_record(record, [finding("SSN", None, None, path="$.ssn")])
    assert counts["SSN"].tp == 1 and counts["SSN"].fn == 0


def test_evaluate_runs_the_detector_and_renders() -> None:
    record = CorpusRecord(
        id="e",
        jurisdiction="US",
        locale="en_US",
        kind="text",
        text="mail a.b@example.com or call (415) 555-0132",
        labels=[
            Label(start=5, end=20, entity_type="EMAIL"),
            Label(start=29, end=43, entity_type="PHONE"),
        ],
    )
    report = evaluate([record], corpus_sha256="0" * 64)
    by = {s.entity_type: s for s in report.by_type}
    assert (
        by["EMAIL"].recall == 1.0
        and by["PHONE"].recall == 1.0
        and report.overall["micro_precision"] == 1.0
    )
    assert [t["min_score"] for t in report.thresholds] == [0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]
    markdown = render_markdown(report)
    assert "| EMAIL | 1 | 1 | 0 | 0 |" in markdown and "example.com" not in markdown
