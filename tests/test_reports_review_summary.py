"""WP-I (M5 merge): the M5 evidence list carries main's ``review_required`` verdict.

Main's post-write gate (integrated verify r1, B1) and M5's D-A review both end in a verification
with ``status: review_required``. ``GET /reports/evidence`` summarises packs for the Reports page;
without the flag the console could only say "failed closed" for a run that needs a reviewer.
"""

from __future__ import annotations

from typing import Any

from dps.reports import _evidence_summary


def _pack(verification: dict[str, Any]) -> dict[str, Any]:
    return {"mode": "synthesize", "verification": verification, "counts": {"rows_written": 9}}


def test_a_review_required_pack_says_so_in_the_summary() -> None:
    s = _evidence_summary(
        _pack(
            {
                "passed": False,
                "status": "review_required",
                "review_required": True,
                "hits": 0,
                "review_columns": ["contact"],
            }
        )
    )
    assert s["verification_passed"] is False and s["hits"] == 0
    assert s["review_required"] is True and s["verification_status"] == "review_required"
    assert "contact" not in s.values()  # column names stay in the pack, never in the list


def test_a_passed_pack_and_an_old_pack_without_status() -> None:
    passed = _evidence_summary(
        _pack({"passed": True, "status": "passed", "review_required": False, "hits": 0})
    )
    assert passed["review_required"] is False and passed["verification_status"] == "passed"
    legacy = _evidence_summary(_pack({"passed": True, "hits": 0}))
    assert legacy["review_required"] is False and legacy["verification_status"] is None
