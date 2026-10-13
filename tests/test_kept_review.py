"""D-A: a synthesize run keeps a source value only in a column reviewed as not personal whose
written sample is clean (``kept_review``; counts and column names only)."""

from __future__ import annotations

from typing import Any

from dps.config import Settings
from dps.engine.policy import load_preset
from dps.engine.service import PrivacyEngine
from dps.layers.connectors.base import ColumnInfo, ObjectRef, TableShape
from dps.layers.connectors.jobs_run import copied_personal_columns, kept_review
from dps.layers.connectors.targets import RunPlan, pseudonym_columns
from tests.connectors.factories import accepted_snapshot

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


def test_the_prepare_guard_names_classified_columns_and_keys_a_run_would_copy() -> None:
    """D-A invariant (G2 review r1): a synthesize plan that would write a classified column or
    a key as its source value fails prepare (``leakage``); a correct plan passes."""
    snap = accepted_snapshot("c_src", "bank.customers")
    projection = [c.name for c in snap.shape.columns]
    target: dict[str, Any] = {
        "job_id": "j",
        "write_mode": "table",
        "connection_id": "c",
        "engine": "postgres",
        "key": ["customer_id"],
        "write_columns": projection,
    }
    plan = RunPlan.model_validate(
        {
            "columns": [],
            "key": ["customer_id"],
            "projection": projection,
            "target": target,
            "source_object": "bank.customers",
            "policy_name": "p",
            "policy_hash": "h",
            "snapshot_id": snap.id,
            "snapshot_hash": snap.content_hash,
            "locale": "en_US",
            "batch_rows": 100,
        }
    )
    assert copied_personal_columns(plan, snap, set()) == [
        "customer_id",
        "dob",
        "email",
        "full_name",
    ]
    plan.pseudonyms = pseudonym_columns(
        snap, mode="synthesize", write_mode="table", pk_strategy=None, projection=projection
    )
    assert copied_personal_columns(plan, snap, {"dob", "email", "full_name"}) == []
    # in place only planned and pseudonymised columns are written
    in_place = plan.model_copy(
        update={"target": plan.target.model_copy(update={"write_mode": "in_place"})}
    )
    assert copied_personal_columns(in_place, snap, set()) == []
