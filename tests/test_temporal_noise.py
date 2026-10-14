"""WP-I (m3 merge): a date or timestamp value never carries a personal identifier inside its digits.

Oracle ``TIMESTAMP(9)`` renders nine fractional digits (``2019-05-02T12:07:01.697811902``); about
one value in nine holds a span the phone pattern accepts. Main's post-write gate (integrated verify
r1, B1) counted those spans in a kept, unclassified timestamp column as personal data, so every
Oracle CRM run (``LAST_CONTACT_AT``) read ``review_required``. A temporal column's value is one
value: only a whole-value finding (a date of birth kept as "not personal") counts — in discovery,
in the post-write sample and in the synthesize kept review alike.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from dps.config import Settings
from dps.engine.policy import load_preset
from dps.engine.service import PrivacyEngine
from dps.layers.connectors.base import ColumnInfo, ObjectRef, TableShape
from dps.layers.connectors.discovery import classify_columns
from dps.layers.connectors.jobs_run import kept_review, scan_written
from tools.seed_model import seed_customers

ENGINE = PrivacyEngine(
    Settings(_env_file=None, llm_provider="stub", ner_enabled=False, fpe_enabled=False)  # type: ignore[call-arg]
)
POLICY = load_preset("gdpr-pseudonymise")
DETECTOR = ENGINE.detector(POLICY, "en_IN")

SHAPE = TableShape(
    object=ObjectRef(owner="CRM", name="CUSTOMERS"),
    key=["CUSTOMER_ID"],
    key_source="primary_key",
    columns=[
        ColumnInfo(
            path="$.CUSTOMER_ID", name="CUSTOMER_ID", data_type="NUMBER(12)", kind="int",
            is_key=True,
        ),
        ColumnInfo(
            path="$.LAST_CONTACT_AT", name="LAST_CONTACT_AT", data_type="TIMESTAMP(9)",
            kind="timestamp", ts_precision=9,
        ),
        ColumnInfo(path="$.DOB", name="DOB", data_type="DATE", kind="date"),
    ],
)  # fmt: skip


def _rows() -> list[dict[str, Any]]:
    return [
        {
            "CUSTOMER_ID": c.customer_id,
            "LAST_CONTACT_AT": c.last_contact_at,
            "DOB": dt.datetime(c.dob.year, c.dob.month, c.dob.day),
        }
        for c in seed_customers(200, 7)
    ]


def test_spans_inside_a_timestamp_are_noise_after_the_write() -> None:
    out = scan_written(
        _rows(),
        SHAPE,
        written={"CUSTOMER_ID", "LAST_CONTACT_AT"},
        planned=set(),
        unclassified={"LAST_CONTACT_AT"},
        detector=DETECTOR,
        min_score=0.6,
    )
    assert out["kept_columns"] == [] and out["kept_column_hits"] == 0


def test_a_date_of_birth_kept_as_not_personal_still_gives_review() -> None:
    out = scan_written(
        _rows(),
        SHAPE,
        written={"CUSTOMER_ID", "DOB"},
        planned=set(),
        unclassified={"DOB"},
        detector=DETECTOR,
        min_score=0.6,
    )
    assert out["kept_columns"] == ["DOB"] and out["kept_column_hits"] == 200


def test_the_synthesize_kept_review_agrees() -> None:
    rows = _rows()
    out = kept_review(rows, SHAPE, kept={"LAST_CONTACT_AT"}, detector=DETECTOR, min_score=0.6)
    assert out["kept_columns"] == []
    out = kept_review(rows, SHAPE, kept={"DOB"}, detector=DETECTOR, min_score=0.6)
    assert out["kept_columns"] == ["DOB"]


def test_discovery_reports_no_hits_for_a_timestamp() -> None:
    cols = {
        c.name: c
        for c in classify_columns(
            SHAPE, _rows(), policy=POLICY, detector=DETECTOR, fpe_enabled=False
        )
    }
    ts = cols["LAST_CONTACT_AT"]
    assert ts.entity_type is None and ts.type_note != "kept_with_hits"
    assert ts.value_hit_rate == 0.0 and ts.value_entities == []
    assert cols["DOB"].entity_type == "DATE_OF_BIRTH"
