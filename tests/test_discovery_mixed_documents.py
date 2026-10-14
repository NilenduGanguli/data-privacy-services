"""WP-I (m3 merge): main's mixed-PII discovery fix applies to MongoDB document leaves.

Main's B1 fix (``MIXED_PII``: a column of several personal-data types is replaced whole, never
kept) reads the column *name* (``value`` next to ``channel``). A MongoDB leaf's ``name`` is its
top-level field (``contacts`` for ``$.contacts[*].value``), so the name cue never fired for a
document: the leaf segment and its siblings under the same parent are what count.
"""

from __future__ import annotations

from typing import Any

from dps.config import Settings
from dps.engine.policy import load_preset
from dps.engine.service import PrivacyEngine
from dps.layers.connectors.base import ColumnInfo, ObjectRef, TableShape
from dps.layers.connectors.discovery import MIXED_NAME_SCORE, _mixed_name_score, classify_columns
from dps.snapshots import MIXED_PII
from tools.seed_sources import generate_rows

ENGINE = PrivacyEngine(
    Settings(_env_file=None, llm_provider="stub", ner_enabled=False, fpe_enabled=True)  # type: ignore[call-arg]
)
POLICY = load_preset("gdpr-pseudonymise")


def _col(path: str, name: str, kind: str = "text") -> ColumnInfo:
    return ColumnInfo(path=path, name=name, data_type="string", kind=kind)  # type: ignore[arg-type]


SHAPE = TableShape(
    object=ObjectRef(owner=None, name="profiles"),
    key=["_id"],
    key_source="primary_key",
    columns=[
        ColumnInfo(path="$._id", name="_id", data_type="objectId", kind="object_id", is_key=True),
        _col("$.contacts[*].channel", "contacts"),
        _col("$.contacts[*].value", "contacts"),
        _col("$.prefs.value", "prefs"),
        _col("$.meta.channel", "meta"),
    ],
)


def test_the_mixed_name_cue_reads_a_document_leaf_and_its_siblings() -> None:
    by_path = {c.path: c for c in SHAPE.columns}
    assert _mixed_name_score(by_path["$.contacts[*].value"], SHAPE) == MIXED_NAME_SCORE
    # a channel under another parent is not this value's channel
    assert _mixed_name_score(by_path["$.prefs.value"], SHAPE) == 0.0
    assert _mixed_name_score(by_path["$.contacts[*].channel"], SHAPE) == 0.0
    # SQL columns keep main's rule: a ``value`` column next to a ``channel`` column
    sql = TableShape(
        object=ObjectRef(owner="bank", name="contacts"),
        key=["id"],
        key_source="primary_key",
        columns=[_col("$.id", "id", "int"), _col("$.channel", "channel"), _col("$.value", "value")],
    )
    assert _mixed_name_score(sql.columns[2], sql) == MIXED_NAME_SCORE


def _documents() -> list[dict[str, Any]]:
    contacts = generate_rows(rows=200, seed=11)["contacts"]
    by_customer: dict[Any, list[dict[str, Any]]] = {}
    for c in contacts:
        by_customer.setdefault(c["customer_id"], []).append(
            {"channel": c["channel"], "value": c["value"]}
        )
    return [
        {"_id": i, "contacts": items, "prefs": {"value": "dark"}, "meta": {"channel": "web"}}
        for i, items in enumerate(by_customer.values())
    ]


def test_a_mixed_contact_leaf_is_mixed_pii_never_kept() -> None:
    cols = {
        c.path: c
        for c in classify_columns(
            SHAPE,
            _documents(),
            policy=POLICY,
            detector=ENGINE.detector(POLICY, "en_IN"),
            fpe_enabled=True,
        )
    }
    value = cols["$.contacts[*].value"]
    assert value.entity_type == MIXED_PII and value.method.method != "keep"
    assert value.type_note == "mixed_entities" and len(value.value_entities) >= 2
    assert cols["$.prefs.value"].entity_type is None  # a preference, not a contact
