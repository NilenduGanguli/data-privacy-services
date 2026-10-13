"""D-A: a synthesize run keeps a source value only in a column reviewed as not personal whose
written sample is clean (``kept_review``; counts and column names only)."""

from __future__ import annotations

from typing import Any

from dps.config import Settings
from dps.engine.policy import load_preset
from dps.engine.service import PrivacyEngine
from dps.layers.connectors.base import ColumnInfo, ObjectRef, TableShape
from dps.layers.connectors.jobs_run import kept_review

SHAPE = TableShape(
    object=ObjectRef(owner="bank", name="accounts"),
    key=["id"],
    key_source="primary_key",
    columns=[
        ColumnInfo(path="$.id", name="id", data_type="bigint", kind="int", is_key=True),
        ColumnInfo(path="$.contact", name="contact", data_type="text", kind="text"),
        ColumnInfo(path="$.branch", name="branch", data_type="text", kind="text"),
        ColumnInfo(path="$.ref_no", name="ref_no", data_type="bigint", kind="int"),
    ],
)


def _detector() -> Any:
    engine = PrivacyEngine(
        Settings(_env_file=None, llm_provider="stub", ner_enabled=False, fpe_enabled=False)
    )
    return engine.detector(load_preset("gdpr-pseudonymise"), "en_US")


def test_kept_columns_with_personal_data_are_flagged_by_name_and_count_only() -> None:
    rows = [
        {"id": i, "contact": f"holder.{i}@example-mail.in", "branch": "B1", "ref_no": 1000 + i}
        for i in range(20)
    ]
    out = kept_review(
        rows, SHAPE, kept={"contact", "branch", "ref_no"}, detector=_detector(), min_score=0.6
    )
    assert out == {"rows": 20, "kept_column_hits": 20, "kept_columns": ["contact"]}
    assert "example-mail" not in str(out)
    # columns outside the kept set are never read
    assert kept_review(rows, SHAPE, kept={"branch"}, detector=_detector(), min_score=0.6) == {
        "rows": 20,
        "kept_column_hits": 0,
        "kept_columns": [],
    }


def test_rare_checksum_hits_in_a_numeric_column_are_noise() -> None:
    aadhaar_like = 234123412346  # a Verhoeff-valid 12-digit number, once in 40 rows
    rows = [{"id": i, "ref_no": aadhaar_like if i == 0 else 10_000 + i} for i in range(40)]
    out = kept_review(rows, SHAPE, kept={"ref_no"}, detector=_detector(), min_score=0.6)
    assert out["kept_columns"] == []
    every = [{"id": i, "ref_no": (234123412346, 499118665246)[i % 2]} for i in range(40)]
    out = kept_review(every, SHAPE, kept={"ref_no"}, detector=_detector(), min_score=0.6)
    assert out["kept_columns"] == ["ref_no"] and out["kept_column_hits"] == 40
