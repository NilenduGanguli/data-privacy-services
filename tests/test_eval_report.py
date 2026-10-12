from __future__ import annotations

import json
from pathlib import Path

import pytest

from dps.evaluation.corpus import load_corpus
from dps.evaluation.scoring import evaluate

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "tests" / "eval" / "corpus"
REPORT = ROOT / "docs" / "technical" / "detector-eval-m1.json"
#: Checksum-validated types: the detectors must find them nearly perfectly on clean text.
FLOOR_TYPES = (
    "AADHAAR",
    "CARD_PAN",
    "CURP",
    "EMAIL",
    "IBAN",
    "NHS_NUMBER",
    "NINO",
    "PAN_IN",
    "RFC",
    "SIN",
    "SSN",
)
FLOOR = 0.95


@pytest.fixture(scope="module")
def current():  # noqa: ANN201
    records, digest = load_corpus([CORPUS])
    return evaluate(records, corpus_sha256=digest)


def test_committed_report_is_current(current) -> None:  # noqa: ANN001
    committed = json.loads(REPORT.read_text(encoding="utf-8"))
    fresh = current.model_dump(mode="json")
    assert committed["corpus"] == fresh["corpus"], "corpus changed: run tools/eval_detectors.py run"
    assert committed["overall"] == fresh["overall"] and committed["by_type"] == fresh["by_type"], (
        "detectors changed: regenerate docs/technical/detector-eval-m1.{json,md} and commit them"
    )


def test_checksum_types_meet_the_floor(current) -> None:  # noqa: ANN001
    by = {s.entity_type: s for s in current.by_type}
    for entity in FLOOR_TYPES:
        assert by[entity].precision is not None and by[entity].precision >= FLOOR, entity
        assert by[entity].recall is not None and by[entity].recall >= FLOOR, entity


def test_free_text_rows_cover_labelled_identifiers(current) -> None:  # noqa: ANN001
    """Account numbers and customer ids are evaluated inside free text, not only by key
    (integrated verify r1, B2), and the committed report shows the free-text table."""
    committed = json.loads(REPORT.read_text(encoding="utf-8"))
    assert committed["by_type_free_text"] == current.model_dump(mode="json")["by_type_free_text"]
    text = {s.entity_type: s for s in current.by_type_free_text}
    for entity in ("ACCOUNT_NUMBER", "CUSTOMER_ID"):
        assert text[entity].support >= 50, entity
        assert text[entity].recall is not None and text[entity].recall >= FLOOR, entity
        assert text[entity].precision is not None and text[entity].precision >= FLOOR, entity
    markdown = REPORT.with_suffix(".md").read_text(encoding="utf-8")
    assert "## In free text" in markdown and "| ACCOUNT_NUMBER |" in markdown
