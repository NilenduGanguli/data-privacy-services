"""Discovery never keeps a personal-data column as "not personal" (integrated verify r1, B1).

The 10-table demo's UAT copy kept ``contacts.value`` (e-mails and mobiles in one column),
``kyc_documents.doc_number`` (Aadhaar, SSN, CURP, NINO numbers), ``cards.cardholder_name``,
``addresses.line1`` and ``transactions.counterparty_name`` in clear: per-type scoring left a mixed
column below the threshold, and the names gave no cue. These tests classify the demo's own rows
(``tools/seed_sources.generate_rows``: surrogate values, no real data) offline.
"""

from __future__ import annotations

import re
from functools import cache
from typing import Any

import pytest

from dps.config import Settings
from dps.engine.policy import MethodSpec, load_preset
from dps.engine.service import PrivacyEngine
from dps.engine.taxonomy import Sensitivity
from dps.jobs_models import ColumnClassification
from dps.layers.connectors.base import ColumnInfo, ObjectRef, TableShape
from dps.layers.connectors.discovery import (
    KEPT_HIT_MIN_RATE,
    classify_columns,
    mixed_method,
    suggest_methods,
)
from dps.snapshots import MIXED_PII
from tools.seed_sources import TABLE_DDL, generate_rows

ENGINE = PrivacyEngine(
    Settings(_env_file=None, llm_provider="stub", ner_enabled=False, fpe_enabled=True)  # type: ignore[call-arg]
)
POLICY = load_preset("gdpr-pseudonymise")
_KINDS = {
    "bigint": "int",
    "smallint": "int",
    "numeric": "decimal",
    "date": "date",
    "timestamptz": "timestamp",
    "boolean": "bool",
    "text": "text",
}


def shape_of(table: str) -> TableShape:
    """The demo table's shape from its DDL (kinds and lengths as the PostgreSQL connector sees)."""
    columns = []
    for line in TABLE_DDL[table].strip().splitlines():
        name, data_type = line.strip().split()[:2]
        sized = re.fullmatch(r"(varchar|char)\((\d+)\),?", data_type)
        base = data_type.split("(")[0].rstrip(",")
        columns.append(
            ColumnInfo(
                path=f"$.{name}",
                name=name,
                data_type=data_type.rstrip(","),
                kind=("text" if sized.group(1) == "varchar" else "char") if sized else _KINDS[base],  # type: ignore[arg-type]
                length=int(sized.group(2)) if sized else None,
                nullable="NOT NULL" not in line,
                is_key="PRIMARY KEY" in line,
            )
        )
    key = [c.name for c in columns if c.is_key]
    return TableShape(
        object=ObjectRef(owner="bank", name=table),
        columns=columns,
        key=key,
        key_source="primary_key",
    )


@cache
def demo_rows() -> dict[str, list[dict[str, Any]]]:
    return generate_rows(rows=400, seed=11)


def classify(table: str) -> dict[str, ColumnClassification]:
    cols = classify_columns(
        shape_of(table),
        demo_rows()[table][:300],
        policy=POLICY,
        detector=ENGINE.detector(POLICY, "en_IN"),
        fpe_enabled=True,
    )
    return {c.name: c for c in cols}


def test_contact_values_of_several_types_are_one_mixed_column() -> None:
    value = classify("contacts")["value"]
    assert value.entity_type == MIXED_PII and value.type_note == "mixed_entities"
    assert {"EMAIL", "PHONE"} <= set(value.value_entities)
    # GB mobiles in Ofcom's drama range are not valid numbers: the detectors miss those rows,
    # and the whole column is still replaced
    assert value.value_hit_rate > 0.9 and value.confidence >= POLICY.detect.min_score
    # varchar(254): a det_token could overflow it, a keyed pseudonym of 16 characters fits
    assert value.method == MethodSpec(method="hmac_pseudonym", params={"length": 16})
    assert value.synthesize_method is not None and value.synthesize_method.method == "surrogate"
    assert value.sensitivity is Sensitivity.MEDIUM


def test_identity_document_numbers_are_classified() -> None:
    doc = classify("kyc_documents")["doc_number"]
    # Aadhaar, SSN, NINO and CURP numbers in one column: the name says an identity document
    assert doc.entity_type == "NATIONAL_ID" and doc.method.method == "det_token"
    assert doc.sensitivity is Sensitivity.CRITICAL and "structural" in doc.detectors


@pytest.mark.parametrize(
    "table,column,entity",
    [
        ("cards", "cardholder_name", "PERSON_NAME"),
        ("transactions", "counterparty_name", "PERSON_NAME"),
        ("addresses", "line1", "STREET_ADDRESS"),
        ("addresses", "line2", "STREET_ADDRESS"),
        ("addresses", "city", "CITY"),
    ],
)
def test_columns_whose_names_now_say_what_they_hold(table: str, column: str, entity: str) -> None:
    col = classify(table)[column]
    assert col.entity_type == entity, col.entity_type


@pytest.mark.parametrize(
    "table", ["contacts", "cards", "kyc_documents", "addresses", "transactions", "customers"]
)
def test_no_kept_column_holds_personal_data_unflagged(table: str) -> None:
    for col in classify(table).values():
        if col.entity_type is not None or col.kind in ("binary", "lob_binary"):
            continue
        # a kept column is either clean or a decision ("kept_with_hits"), never "not personal"
        if col.value_entities:
            assert col.type_note == "kept_with_hits" or col.value_hit_rate < KEPT_HIT_MIN_RATE
        else:
            assert col.value_hit_rate == 0.0, col.name


def _shape(*cols: ColumnInfo) -> TableShape:
    return TableShape(
        object=ObjectRef(owner="s", name="t"), columns=list(cols), key=["id"], key_source="none"
    )


def _col(name: str, kind: str = "text", **kw: Any) -> ColumnInfo:
    return ColumnInfo(path=f"$.{name}", name=name, data_type=kind, kind=kind, **kw)  # type: ignore[arg-type]


def _classify(shape: TableShape, rows: list[dict[str, Any]]) -> dict[str, ColumnClassification]:
    det = ENGINE.detector(POLICY, "en_IN")
    cols = classify_columns(shape, rows, policy=POLICY, detector=det, fpe_enabled=True)
    return {c.name: c for c in cols}


def test_a_value_column_next_to_a_channel_is_mixed_by_its_name() -> None:
    # handles no detector recognises: the name and the channel column decide
    rows = [{"id": i, "channel": "chat", "value": f"kavya_r{i}"} for i in range(40)]
    cols = _classify(_shape(_col("id", "int"), _col("channel"), _col("value")), rows)
    assert cols["value"].entity_type == MIXED_PII and "structural" in cols["value"].detectors
    alone = _classify(_shape(_col("id", "int"), _col("value")), rows)["value"]
    assert alone.entity_type is None  # without the channel column, "value" says nothing


def test_a_minority_of_personal_values_is_a_decision_not_not_personal() -> None:
    rows = [
        {"id": i, "remark": f"user{i}@example-mail.in" if i % 5 == 0 else "no change"}
        for i in range(60)
    ]
    remark = _classify(_shape(_col("id", "int"), _col("remark", length=200)), rows)["remark"]
    assert remark.entity_type is None and remark.method.method == "keep"
    assert remark.type_note == "kept_with_hits" and remark.value_entities == ["EMAIL"]
    assert remark.value_hit_rate == pytest.approx(0.2, abs=0.01)


def test_the_mixed_method_fits_the_column() -> None:
    assert mixed_method(_col("v")) == MethodSpec(method="det_token")  # unbounded text
    assert mixed_method(_col("v", length=32)).method == "hmac_pseudonym"
    assert mixed_method(_col("v", "char", length=10)) == MethodSpec(
        method="mask_partial", params={"keep_last": 0}
    )
    obf, syn, note = suggest_methods(
        MIXED_PII,
        _col("v", "int"),
        policy=POLICY,
        fpe_enabled=True,
        min_digits=8,
        text_column=False,
    )
    assert obf.method == "null" and syn.method == "null" and note == "mixed_entities"


def test_numeric_ids_that_pass_a_checksum_by_chance_are_not_flagged() -> None:
    from dps.engine.detect.checksums import verhoeff_check_digit

    def aadhaar_shaped(n: int) -> int:
        body = f"9{n:010d}"
        return int(body + verhoeff_check_digit(body))

    rows = [
        {"id": 900000000001 + i, "ref": aadhaar_shaped(i) if i % 10 == 0 else i} for i in range(200)
    ]
    cols = _classify(_shape(_col("id", "int"), _col("ref", "int")), rows)
    for name in ("id", "ref"):
        assert cols[name].entity_type is None and cols[name].type_note is None, name
        assert cols[name].value_entities == [] and cols[name].value_hit_rate == 0.0, name
