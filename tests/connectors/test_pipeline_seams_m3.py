"""Static checks that the M2 handlers use the M3 seams (AST, no database)."""

from __future__ import annotations

import ast
from pathlib import Path

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


def test_oracle_sources_pass_the_pipeline_engine_gates() -> None:
    """Discovery (``submit_discover``) and runs (``validate_run``) admit exactly the engines whose
    connector is wired in the registry *and* whose rows the run pipeline has been proven on.
    MongoDB's connector is in the registry (Task 12) but joins the gates with the pipeline's
    document-path work and its end-to-end test (Tasks 15 and 19)."""
    from dps.layers.connectors.base import PIPELINE_ENGINES

    assert {"postgres", "oracle"} <= PIPELINE_ENGINES and "mongo" not in PIPELINE_ENGINES
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
