"""WP-I (m3 merge): main's post-write review (``scan_written``, ``kept_review``) on MongoDB
documents. A MongoDB field has one column per leaf path, all sharing the field's name; keyed by
name, one classified leaf (``$.contact.email``) vouched for an unclassified sibling
(``$.contact.note``) holding personal data, and the run read "passed". Keys are paths now."""

from __future__ import annotations

from typing import Any

from dps.config import Settings
from dps.engine.policy import load_preset
from dps.engine.service import PrivacyEngine
from dps.layers.connectors.base import ColumnInfo, ObjectRef, TableShape
from dps.layers.connectors.jobs_run import (
    column_key,
    kept_review,
    scan_written,
    synthetic_kept_columns,
    verification_outcome,
)
from dps.layers.connectors.targets import RunPlan

ENGINE = PrivacyEngine(
    Settings(_env_file=None, llm_provider="stub", ner_enabled=False, fpe_enabled=False)  # type: ignore[call-arg]
)
DETECTOR = ENGINE.detector(load_preset("gdpr-pseudonymise"), "en_IN")


def _col(path: str, name: str, kind: str = "text") -> ColumnInfo:
    return ColumnInfo(path=path, name=name, data_type="string", kind=kind)  # type: ignore[arg-type]


SHAPE = TableShape(
    object=ObjectRef(owner=None, name="profiles"),
    key=["_id"],
    key_source="primary_key",
    columns=[
        ColumnInfo(path="$._id", name="_id", data_type="objectId", kind="object_id", is_key=True),
        _col("$.contact.email", "contact"),
        _col("$.contact.note", "contact"),
        _col("$.segment", "segment"),
    ],
)
ROWS: list[dict[str, Any]] = [
    {
        "_id": f"65f0c0ffee0000000000{i:04d}",
        "contact": {"email": f"tok_{i:06d}", "note": f"call holder.{i}@example-mail.in"},
        "segment": "retail",
    }
    for i in range(10)
]


def test_column_keys_are_names_for_flat_columns_and_paths_for_leaves() -> None:
    assert column_key("$.segment", "segment") == "segment"
    assert column_key("$['segment']", "segment") == "segment"
    assert column_key("$.contact.note", "contact") == "$.contact.note"


def test_an_unclassified_sibling_of_a_planned_leaf_gives_review_required() -> None:
    out = scan_written(
        ROWS,
        SHAPE,
        written={"_id", "contact", "segment"},
        planned={"$.contact.email"},
        unclassified={"$.contact.note", "segment", "_id"},
        detector=DETECTOR,
        min_score=0.6,
    )
    assert out["kept_columns"] == ["$.contact.note"] and out["kept_column_hits"] == 10
    assert "example-mail" not in str(out)
    passed, status, review = verification_outcome(0, False, out)
    assert (passed, status, review) == (False, "review_required", True)


def _plan(write_mode: str) -> RunPlan:
    return RunPlan.model_validate(
        {
            "columns": [
                {
                    "path": "$.contact.email",
                    "name": "contact",
                    "entity_type": "EMAIL",
                    "method": {"method": "surrogate"},
                    "text_column": False,
                    "reversible": False,
                    "kind": "text",
                    "length": None,
                }
            ],
            "key": ["_id"],
            "projection": ["_id", "contact", "segment"],
            "target": {
                "job_id": "j",
                "write_mode": write_mode,
                "connection_id": "c",
                "engine": "mongo",
                "key": ["_id"],
                "write_columns": ["contact", "segment"],
            },
            "source_object": "crm.profiles",
            "policy_name": "gdpr-pseudonymise",
            "policy_hash": "h",
            "snapshot_id": "s",
            "snapshot_hash": "h",
            "locale": "en_IN",
            "batch_rows": 100,
        }
    )


def test_synthesize_kept_review_reads_the_unmodelled_sibling_leaf() -> None:
    kept = synthetic_kept_columns(_plan("table"), None, SHAPE, set())
    assert kept == {"$.contact.note", "segment"}  # the planned leaf and the key are not kept
    out = kept_review(ROWS, SHAPE, kept=kept, detector=DETECTOR, min_score=0.6)
    assert out == {"rows": 10, "kept_column_hits": 10, "kept_columns": ["$.contact.note"]}
