"""Static checks that the M2 handlers use the M3 seams (AST, no database)."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2] / "dps" / "layers" / "connectors"


def _calls(module: str, name: str) -> list[ast.Call]:
    tree = ast.parse((ROOT / module).read_text(encoding="utf-8"))
    return [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and getattr(n.func, "id", getattr(n.func, "attr", None)) == name
    ]


def test_every_handler_connector_carries_the_job_id() -> None:
    for module in ("jobs_run.py", "discovery.py"):
        calls = _calls(module, "open_connector")
        assert calls, module
        assert all(any(k.arg == "job_id" for k in c.keywords) for c in calls), module


def test_discovery_reads_values_through_column_values_and_forced_keep() -> None:
    assert _calls("discovery.py", "column_values") and _calls("discovery.py", "forced_keep")


def test_finalize_clears_the_write_ledger() -> None:
    assert _calls("jobs_run.py", "clear_ledger")


def test_oracle_and_mongo_sources_pass_the_pipeline_engine_gates() -> None:
    """Discovery (``submit_discover``) and runs (``validate_run``) admit exactly the engines whose
    connector is wired in the registry *and* whose rows the run pipeline has been proven on.
    MongoDB joined with the run handler's path-based planning and checks (Task 19,
    ``test_mongo_pipeline.py`` and the end-to-end tests)."""
    from dps.layers.connectors.base import PIPELINE_ENGINES

    assert PIPELINE_ENGINES == {"postgres", "oracle", "mongo"}
    for module in ("submit.py", "targets.py"):
        text = (ROOT / module).read_text(encoding="utf-8")
        assert "PIPELINE_ENGINES" in text and '.engine != "postgres"' not in text, module


def test_post_sample_skips_row_locators_and_reads_document_paths() -> None:
    """The finalize re-read scans what discovery would: no ROWID pseudo key (a packed ROWID can
    pass a Luhn check), wildcard paths counted once per document."""
    from dps.config import Settings
    from dps.engine.policy import load_preset
    from dps.engine.service import PrivacyEngine
    from dps.layers.connectors.base import ColumnInfo, ObjectRef, TableShape
    from dps.layers.connectors.jobs_run import post_sample_hits

    engine = PrivacyEngine(
        Settings(_env_file=None, llm_provider="stub", ner_enabled=False, fpe_enabled=False)
    )
    detector = engine.detector(load_preset("gdpr-pseudonymise"), "en_IN")
    rowid = ColumnInfo(
        path="$.ROWID", name="ROWID", data_type="ROWID", kind="int", is_key=True, pseudo=True
    )
    # a masked column whose name says nothing (the detector also reads column names)
    token = ColumnInfo(path="$.REF", name="REF", data_type="VARCHAR2(254)", kind="text")
    kept = ColumnInfo(path="$.NOTE", name="NOTE", data_type="VARCHAR2(200)", kind="text")
    emails = ColumnInfo(path="$.contact.emails[*]", name="contact", data_type="string", kind="text")
    shape = TableShape(
        object=ObjectRef(owner="CRM", name="H"), columns=[rowid, token, kept, emails],
        key=["ROWID"], key_source="rowid",
    )  # fmt: skip
    rows = [
        {
            "ROWID": 4111111111111111,
            "REF": "tok_QX7RZK2M",
            "NOTE": "write to a.b@example.com",
            "contact": {"emails": ["p@example.com", "q@example.org"]},
        }
        for _ in range(5)
    ]
    written = {"ROWID", "REF", "NOTE", "contact"}
    kept_hits, transformed_hits = post_sample_hits(
        rows, shape, written=written, planned={"REF", "contact"}, detector=detector
    )
    assert (kept_hits, transformed_hits) == (5, 5)  # NOTE per row; contact once per document
    kept_hits, _ = post_sample_hits(
        rows, shape, written={"ROWID"}, planned=set(), detector=detector
    )
    assert kept_hits == 0


def test_finalize_clears_a_ledger_any_write_mode_prepared() -> None:
    """Oracle ledgers a key-less ``table`` load (ROWID pages); finalize must clear it too, not
    only the ``in_place``/``append`` ledgers M2's PostgreSQL writer keeps."""
    text = (ROOT / "jobs_run.py").read_text(encoding="utf-8")
    assert '"ledger": report.ledger' in text and 'plan.prepare.get("ledger")' in text


def test_the_run_transformer_restores_leaf_types_by_source_engine() -> None:
    """Review G3 r1 B1: every run transformer is built for its source engine, and MongoDB sources
    restore written leaves to their original BSON type (``mongo_docs.restore_leaf``)."""
    calls = _calls("jobs_run.py", "_transformer")
    assert len(calls) == 2
    assert all(any(k.arg == "engine" for k in c.keywords) for c in calls)
    builds = _calls("jobs_run.py", "BatchTransformer")
    assert builds and all(any(k.arg == "leaf_restore" for k in c.keywords) for c in builds)
    from dps.layers.connectors.jobs_run import leaf_restorer

    assert leaf_restorer("postgres") is None and leaf_restorer("oracle") is None
    try:
        from dps.layers.connectors.mongo_docs import restore_leaf
    except ImportError:  # the mongo extra is not installed: nothing to restore with
        return
    assert leaf_restorer("mongo") is restore_leaf


def _document_snapshot() -> Any:
    """An accepted snapshot of a MongoDB collection whose columns share top-level names."""
    from datetime import UTC, datetime

    from dps.engine.policy import MethodSpec
    from dps.jobs_models import ClassificationSnapshot, ColumnClassification
    from dps.layers.connectors.base import ColumnInfo, ObjectRef, TableShape

    def info(path: str, name: str, kind: str, data_type: str, key: bool = False) -> ColumnInfo:
        return ColumnInfo(path=path, name=name, data_type=data_type, kind=kind, is_key=key)  # type: ignore[arg-type]

    shape = TableShape(
        object=ObjectRef(name="profiles"),
        columns=[
            info("$._id", "_id", "object_id", "objectId", key=True),
            info("$.profile.fullName", "profile", "text", "string"),
            info("$.profile.dob", "profile", "timestamp", "date"),
        ],
        key=["_id"],
        key_source="primary_key",
    )
    methods = {"$.profile.fullName": "fpe_text", "$.profile.dob": "date_shift"}
    entities = {"$.profile.fullName": "PERSON_NAME", "$.profile.dob": "DATE_OF_BIRTH"}
    now = datetime.now(UTC)
    columns = [
        ColumnClassification(
            path=c.path, name=c.name, data_type=c.data_type, kind=c.kind, is_key=c.is_key,
            entity_type=entities.get(c.path), confidence=0.9, sensitivity=None, detectors=[],
            value_hit_rate=1.0, null_rate=0.0, distinct_ratio=1.0, shapes=[], text_column=False,
            anchor=False, suggested=MethodSpec(method=methods.get(c.path, "keep")),
            method=MethodSpec(method=methods.get(c.path, "keep")),
        )
        for c in shape.columns
    ]  # fmt: skip
    return ClassificationSnapshot(
        id="snap_d", tenant_id="t1", job_id=None, connection_id="c_m", engine="mongo",
        object_ref="profiles", status="accepted", policy_name="p", policy_hash="h", shape=shape,
        columns=columns, sample_rows=10, overrides=[], content_hash="x", created_at=now,
        accepted_at=now, accepted_by="k",
    )  # fmt: skip


def test_document_columns_are_planned_and_checked_by_path() -> None:
    """Task 19: ``$.profile.fullName`` and ``$.profile.dob`` are both called ``profile``; the run
    handler plans and checks each by its path (by name, the date took the text column's kind and
    the live check called the snapshot stale)."""
    from dps.layers.connectors.jobs_run import _check_snapshot
    from dps.layers.connectors.targets import PrepareFailure, build_column_plans, column_at

    snap = _document_snapshot()
    plans = build_column_plans(
        snap, mode="obfuscate", write_mode="table", pk_strategy=None, projection=["_id", "profile"]
    )
    assert {p.path: p.kind for p in plans} == {
        "$.profile.fullName": "text",
        "$.profile.dob": "timestamp",
    }
    _check_snapshot(snap, snap.shape)  # no false "changed type"
    shape = snap.shape
    assert column_at(shape, "$.profile.dob", "profile") is shape.columns[2]
    assert column_at(shape, "$.profile.nick", "profile") is None  # a sibling is not the column
    gone = shape.model_copy(update={"columns": shape.columns[:2]})
    with pytest.raises(PrepareFailure) as stale:
        _check_snapshot(snap, gone)
    assert stale.value.code == "snapshot_stale"


def test_a_same_collection_append_generates_object_ids() -> None:
    """Task 19: ``generate`` on a same-collection MongoDB append (an ObjectId key) is admitted;
    an ``offset`` on an ObjectId key is still refused."""
    from dps.auth import LOCAL_PRINCIPAL
    from dps.config import Settings
    from dps.jobs_models import RunSpec, SpecError
    from dps.layers.connectors.targets import validate_run
    from tests.connectors.factories import make_connection

    snap = _document_snapshot()
    conn = make_connection("c_m", engine="mongo", roles=["source", "target"], params={})

    def spec(pk: dict[str, Any]) -> RunSpec:
        return RunSpec.model_validate(
            {
                "mode": "obfuscate",
                "snapshot_id": "snap_d",
                "purpose": {"ticket": "CHG-1", "legal_basis": "test"},
                "source": {"connection_id": "c_m", "object": {"name": "profiles"}},
                "target": {"write_mode": "append", "confirm_write": True, "pk_strategy": pk},
                "consistency": {"key_material": {"kid": "k"}},
            }
        )

    def run(pk: dict[str, Any]) -> None:
        validate_run(
            spec(pk), principal=LOCAL_PRINCIPAL, source_conn=conn, target_conn=None,
            snapshot=snap, settings=Settings(_env_file=None), kek=b"k" * 32,
        )  # fmt: skip

    run({"kind": "generate"})
    with pytest.raises(SpecError) as offset:
        run({"kind": "offset", "offset": 1000})
    assert offset.value.code == "pk_strategy_invalid"
