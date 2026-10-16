"""The post-write sample gates verification (integrated verify r1, B1).

A finding in a column the accepted snapshot called *not personal* means raw personal data went
to the target untransformed: the pack must say ``review required``, never "verified · 0 leaks".
Columns classified but kept on purpose (a key kept in place, ``CITY: keep``) are reported apart.
"""

from __future__ import annotations

from typing import Any

import pytest

from dps.config import Settings
from dps.engine.service import PrivacyEngine
from dps.layers.connectors.base import ColumnInfo, ObjectRef, TableShape
from dps.layers.connectors.evidence import build_pack, pack_sha256, render_html
from dps.layers.connectors.jobs_run import scan_written, verification_outcome
from tests.connectors.factories import evidence_inputs

ENGINE = PrivacyEngine(
    Settings(_env_file=None, llm_provider="stub", ner_enabled=False, fpe_enabled=False)  # type: ignore[call-arg]
)
DETECTOR = ENGINE.detector(ENGINE.policy("gdpr-pseudonymise"), "en_IN")


def _col(name: str, kind: str = "text", **kw: Any) -> ColumnInfo:
    return ColumnInfo(path=f"$.{name}", name=name, data_type=kind, kind=kind, **kw)  # type: ignore[arg-type]


SHAPE = TableShape(
    object=ObjectRef(owner="bank_uat", name="contacts"),
    columns=[
        _col("contact_id", "int", is_key=True),
        _col("customer_id", "int"),
        _col("channel"),
        _col("value", length=254),
        _col("city"),
        _col("verified_at", "timestamp"),
    ],
    key=["contact_id"],
    key_source="primary_key",
)
#: Synthetic rows on reserved test domains: an e-mail left raw in ``value``.
ROWS = [
    {
        "contact_id": 70000001 + i,
        "customer_id": 10000001 + i,
        "channel": "email",
        "value": f"kavya.raman{i}@example.com",
        "city": "Pune",
        "verified_at": "2024-03-01 10:00:00+00",
    }
    for i in range(20)
]
WRITTEN = {c.name for c in SHAPE.columns}


def test_personal_data_in_a_not_personal_column_is_counted_by_column() -> None:
    out = scan_written(
        [dict(r) for r in ROWS],
        SHAPE,
        written=WRITTEN,
        planned={"customer_id"},
        unclassified={"contact_id", "channel", "value", "verified_at"},
        detector=DETECTOR,
        min_score=0.6,
    )
    assert out["rows"] == 20 and out["kept_column_hits"] == 20
    assert out["kept_columns"] == ["value"]  # dates in timestamps are LOW: not counted
    assert verification_outcome(0, False, out) == (False, "review_required", True)


def test_columns_kept_on_purpose_do_not_gate() -> None:
    # value transformed, city classified CITY and kept by the policy: nothing to review
    out = scan_written(
        [dict(r) for r in ROWS],
        SHAPE,
        written=WRITTEN,
        planned={"customer_id", "value"},
        unclassified={"contact_id", "channel", "verified_at"},
        detector=DETECTOR,
        min_score=0.6,
    )
    assert out["kept_column_hits"] == 0 and out["kept_columns"] == []
    assert verification_outcome(0, False, out) == (True, "passed", False)


def _aadhaar_shaped(n: int) -> str:
    """A 12-digit id starting 9 whose last digit is the Verhoeff check digit (synthetic)."""
    from dps.engine.detect.checksums import verhoeff_check_digit

    body = f"9{n:010d}"
    return body + verhoeff_check_digit(body)


def test_ids_that_pass_a_checksum_by_chance_are_noise_not_personal_data() -> None:
    # 12-digit ids from 900000000001: one in ten is a Verhoeff-valid "Aadhaar" by chance
    shape = TableShape(
        object=ObjectRef(owner="bank_perf", name="transactions"),
        columns=[_col("txn_id", "int", is_key=True), _col("ref", "int"), _col("remark")],
        key=["txn_id"],
        key_source="primary_key",
    )
    rows = [
        {
            "txn_id": 900000000001 + i,
            "ref": int(_aadhaar_shaped(i)) if i % 10 == 0 else i,
            "remark": None,
        }
        for i in range(200)
    ]
    out = scan_written(
        rows,
        shape,
        written={"txn_id", "ref", "remark"},
        planned=set(),
        unclassified={"txn_id", "ref", "remark"},
        detector=DETECTOR,
        min_score=0.6,
    )
    assert out["kept_column_hits"] == 0 and out["kept_columns"] == []
    assert out["numeric_noise_hits"] > 0
    assert verification_outcome(0, False, out)[1] == "passed"
    # a numeric column that IS Aadhaar numbers reaches the bar
    real = [{"txn_id": 1 + i, "ref": int(_aadhaar_shaped(i)), "remark": None} for i in range(50)]
    out = scan_written(
        real,
        shape,
        written={"ref"},
        planned=set(),
        unclassified={"txn_id", "ref", "remark"},
        detector=DETECTOR,
        min_score=0.6,
    )
    assert out["kept_columns"] == ["ref"] and out["kept_column_hits"] == 50
    # in a text column, any personal-data finding counts
    text = [{"txn_id": 1, "ref": 2, "remark": "call +91 98450 12345"}]
    out = scan_written(
        text,
        shape,
        written={"remark"},
        planned=set(),
        unclassified={"txn_id", "ref", "remark"},
        detector=DETECTOR,
        min_score=0.6,
    )
    assert out["kept_columns"] == ["remark"]


@pytest.mark.parametrize(
    "hits,leakage,kept,expected",
    [
        (0, False, 0, (True, "passed", False)),
        (2, False, 0, (False, "failed", False)),
        (0, True, 0, (False, "failed", False)),
        (1, False, 5, (False, "failed", True)),
        (0, False, 5, (False, "review_required", True)),
    ],
)
def test_verification_outcome(
    hits: int, leakage: bool, kept: int, expected: tuple[bool, str, bool]
) -> None:
    assert verification_outcome(hits, leakage, {"kept_column_hits": kept}) == expected
    if not kept:
        assert verification_outcome(hits, leakage, None) == expected


def test_the_pack_never_says_verified_when_review_is_required() -> None:
    inputs = evidence_inputs(mode="obfuscate")
    post = {"rows": 200, "detector_hits": 3, "kept_column_hits": 186, "kept_columns": ["value"]}
    passed, status, review = verification_outcome(0, False, post)
    inputs.verification = {
        **inputs.verification,
        "passed": passed,
        "status": status,
        "review_required": review,
        "post_sample": post,
    }
    pack = build_pack(inputs)
    assert pack.verification["passed"] is False and pack.verification["review_required"]
    page = render_html(pack, sha=pack_sha256(pack))
    assert "verified ·" not in page and "review required · 186" in page
    assert "Review required" in page and "value" in page
    clean = build_pack(evidence_inputs(mode="obfuscate"))
    assert "verified · 0 leaks" in render_html(clean, sha=pack_sha256(clean))


@pytest.mark.parametrize("outcome", ["failed", "canceled"])
def test_a_job_that_did_not_finish_is_never_verified(outcome: str) -> None:
    """N3: a failed or canceled run (0 rows, length_overflow at prepare) is not "passed · 0 leaks"."""
    assert verification_outcome(0, False, None, outcome=outcome) == (False, "incomplete", False)
    assert verification_outcome(0, False, {"kept_column_hits": 2}, outcome=outcome) == (
        False,
        "incomplete",
        True,
    )
    assert verification_outcome(3, False, None, outcome=outcome) == (False, "failed", False)


@pytest.mark.parametrize("outcome", ["succeeded", "dry_run"])
def test_finished_runs_keep_their_verdict(outcome: str) -> None:
    assert verification_outcome(0, False, None, outcome=outcome) == (True, "passed", False)


# -- WP-I (M5 merge): synthesize's D-A review and main's post-write gate agree ------------------


def _verification(passed: bool, status: str, review: bool) -> dict[str, Any]:
    return {"hits": 0, "passed": passed, "status": status, "review_required": review}


def test_synthetic_review_turns_a_pass_into_review_required() -> None:
    from dps.layers.connectors.jobs_run import apply_synthetic_review

    v = _verification(True, "passed", False)
    apply_synthetic_review(v, ["contact"])
    assert (v["passed"], v["status"], v["review_required"]) == (False, "review_required", True)
    assert v["review_columns"] == ["contact"]


@pytest.mark.parametrize("status", ["failed", "incomplete"])
def test_synthetic_review_never_downgrades_a_failure(status: str) -> None:
    from dps.layers.connectors.jobs_run import apply_synthetic_review

    v = _verification(False, status, False)
    apply_synthetic_review(v, ["national_id"])
    assert (v["passed"], v["status"], v["review_required"]) == (False, status, True)


def test_no_synthetic_review_keeps_mains_post_write_verdict() -> None:
    """An empty D-A review must not clear a review the post-write sample already required."""
    from dps.layers.connectors.jobs_run import apply_synthetic_review

    v = _verification(False, "review_required", True)
    apply_synthetic_review(v, [])
    assert (v["passed"], v["status"], v["review_required"]) == (False, "review_required", True)
    assert v["review_columns"] == []
    clean = _verification(True, "passed", False)
    apply_synthetic_review(clean, [])
    assert (clean["passed"], clean["status"], clean["review_required"]) == (True, "passed", False)


# -- S3 review round 2 (R2-B1): a post-write sample that could not run fails closed -------------


@pytest.mark.parametrize("code", ["custom_pattern_too_slow", "target_unavailable"])
def test_a_post_write_sample_that_could_not_run_needs_review(code: str) -> None:
    """A refused policy pattern (or an unreachable target) left the post-write sample without a
    verdict: like the identifier check, that fails closed — ``review_required``, never "passed"."""
    post = {"error": code}
    assert verification_outcome(0, False, post) == (False, "review_required", True)
    assert verification_outcome(3, False, post) == (False, "failed", True)
    assert verification_outcome(0, False, post, outcome="failed") == (False, "incomplete", True)
    inputs = evidence_inputs(mode="obfuscate")
    passed, status, review = verification_outcome(0, False, post)
    inputs.verification = {
        **inputs.verification,
        "passed": passed,
        "status": status,
        "review_required": review,
        "post_sample": post,
    }
    pack = build_pack(inputs)
    page = render_html(pack, sha=pack_sha256(pack))
    assert "verified ·" not in page
    assert "post-write sample did not run" in page and code in page
    assert "found personal data in" not in page
