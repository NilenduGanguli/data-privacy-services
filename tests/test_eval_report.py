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
