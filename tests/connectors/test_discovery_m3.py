"""Discovery seams for Oracle and MongoDB shapes: forced keep, wildcard paths (offline).

Classification itself is M2's shared ``classify_columns`` (and the fix ``main`` is making to its
auto-keep rule): M3 only changes how a column's sampled values are read and which columns are kept
without running value detectors.
"""

from __future__ import annotations

from dps.config import Settings
from dps.engine.policy import load_preset
from dps.engine.service import PrivacyEngine
from dps.layers.connectors.base import ColumnInfo, ObjectRef, TableShape
from dps.layers.connectors.discovery import classify_columns

ENGINE = PrivacyEngine(
    Settings(_env_file=None, llm_provider="stub", ner_enabled=False, fpe_enabled=False)
)
POLICY = load_preset("gdpr-pseudonymise")
DETECTOR = ENGINE.detector(POLICY, "en_IN")
CARDS = ["4111111111111111", "5555555555554444", "4012888888881881", "378282246310005"]


def _classify(shape: TableShape, rows: list[dict[str, object]]) -> dict[str, object]:
    cols = classify_columns(shape, rows, policy=POLICY, detector=DETECTOR, fpe_enabled=False)
    return {c.name: c for c in cols}


def test_a_rowid_pseudo_key_is_kept_without_value_detection() -> None:
    rowid = ColumnInfo(
        path="$.ROWID", name="ROWID", data_type="ROWID", kind="int", is_key=True, pseudo=True
    )
    card = ColumnInfo(path="$.PAN", name="PAN", data_type="VARCHAR2(19)", kind="text", length=19)
    shape = TableShape(
        object=ObjectRef(owner="CRM", name="H"), columns=[card, rowid], key=["ROWID"],
        key_source="rowid",
    )  # fmt: skip
    # packed ROWIDs are integers; these pass Luhn and look like card numbers
    rows = [{"PAN": c, "ROWID": int(c)} for c in CARDS * 10]
    cols = _classify(shape, rows)
    pan, rid = cols["PAN"], cols["ROWID"]
    assert pan.entity_type == "CARD_PAN" and pan.method.method != "keep"  # type: ignore[attr-defined]
    assert rid.entity_type is None and rid.method.method == "keep"  # type: ignore[attr-defined]
    assert rid.detectors == [] and rid.value_hit_rate == 0.0  # type: ignore[attr-defined]
    assert rid.shapes == [] and rid.null_rate == 0.0  # type: ignore[attr-defined]


def test_a_stored_rowid_column_is_kept_even_when_its_name_looks_sensitive() -> None:
    """A real ROWID column addresses rows: its name must not make DPS transform it (the
    writer could not bind a token into it); a binary column keeps M2's name-only rule."""
    ref = ColumnInfo(
        path="$.ACCOUNT_NUMBER", name="ACCOUNT_NUMBER", data_type="ROWID", kind="other"
    )
    scan = ColumnInfo(path="$.EMAIL", name="EMAIL", data_type="BLOB", kind="lob_binary")
    shape = TableShape(
        object=ObjectRef(owner="CRM", name="LINKS"), columns=[ref, scan], key=[], key_source="none"
    )
    rows = [{"ACCOUNT_NUMBER": "AAAR3sAAEAAAACXAAA", "EMAIL": b"%PDF"} for _ in range(20)]
    cols = _classify(shape, rows)
    stored, blob = cols["ACCOUNT_NUMBER"], cols["EMAIL"]
    assert stored.entity_type is None and stored.method.method == "keep"  # type: ignore[attr-defined]
    assert stored.detectors == [] and stored.shapes == []  # type: ignore[attr-defined]
    assert blob.entity_type == "EMAIL" and blob.method.method == "keep"  # type: ignore[attr-defined]
    assert blob.type_note == "binary_kept" and blob.shapes == []  # type: ignore[attr-defined]


def test_wildcard_document_paths_are_classified_by_their_values() -> None:
    emails = ColumnInfo(path="$.contact.emails[*]", name="contact", data_type="string", kind="text")
    shape = TableShape(
        object=ObjectRef(name="profiles"), columns=[emails], key=["_id"], key_source="primary_key"
    )
    rows: list[dict[str, object]] = [
        {"_id": i, "contact": {"emails": [f"p{i}@example.com", f"q{i}@example.org"]}}
        for i in range(30)
    ]
    rows.append({"_id": 99, "contact": {"emails": []}})
    col = _classify(shape, rows)["contact"]
    assert col.entity_type == "EMAIL" and col.value_hit_rate > 0.9  # type: ignore[attr-defined]
    assert col.shapes and all("@" in s and not s.startswith("{") for s in col.shapes)  # type: ignore[attr-defined]
    assert 0.0 < col.null_rate < 0.1  # type: ignore[attr-defined]  # one document of 31 has none
