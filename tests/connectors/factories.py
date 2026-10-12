"""Test helpers: the demo customers shape, synthetic rows and classification snapshots."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from dps.config import Settings
from dps.engine.policy import load_preset
from dps.engine.service import PrivacyEngine
from dps.ids import new_id
from dps.jobs_models import ClassificationSnapshot, ColumnClassification, ObjectRef
from dps.layers.connectors.base import ColumnInfo, Row, TableShape

_ENGINE = PrivacyEngine(
    Settings(_env_file=None, llm_provider="stub", ner_enabled=False, fpe_enabled=False)
)
POLICY_NAME = "gdpr-pseudonymise"


def col(name: str, kind: str, **kw: Any) -> ColumnInfo:
    """A ``ColumnInfo`` at ``$.<name>`` (``data_type`` defaults to the kind)."""
    return ColumnInfo(
        path=f"$.{name}", name=name, data_type=kw.pop("data_type", kind), kind=kind, **kw
    )  # type: ignore[arg-type]


def customers_shape(owner: str = "bank") -> TableShape:
    """The customers shape used by the discovery and snapshot tests."""
    return TableShape(
        object=ObjectRef(owner=owner, name="customers"),
        key=["customer_id"],
        key_source="primary_key",
        columns=[
            col("customer_id", "int", is_key=True, nullable=False),
            col("full_name", "text", length=120),
            col("email", "text", length=254),
            col("ssn", "text", length=11),
            col("dob", "date"),
            col("segment", "text", length=16),
            col("risk_score", "int"),
            col("notes", "text"),
            col("photo", "binary"),
        ],
    )


def customers_rows(n: int = 60) -> list[Row]:
    """Synthetic rows for :func:`customers_shape` (made-up names on a reserved test domain)."""
    return [
        {
            "customer_id": 10_000_000 + i,
            "full_name": f"Ananya Krishnan{i}",
            "email": f"user{i}@example-mail.in",
            "ssn": f"{900 + i % 9:03d}-45-{1000 + i:04d}",
            "dob": date(1980, 1 + i % 12, 1 + i % 28),
            "segment": ["retail", "premium"][i % 2],
            "risk_score": i % 900,
            "notes": (
                f"Spoke to the client; reach them at user{i}@example-mail.in"
                if i % 3 == 0
                else "Routine review"
            ),
            "photo": b"\x89PNG",
        }
        for i in range(n)
    ]


def classified_columns(shape: TableShape | None = None) -> list[ColumnClassification]:
    """Classify :func:`customers_rows` with the offline engine (no FPE, no NER)."""
    from dps.layers.connectors.discovery import classify_columns

    policy = load_preset(POLICY_NAME)
    return classify_columns(
        shape or customers_shape(),
        customers_rows(),
        policy=policy,
        detector=_ENGINE.detector(policy, "en_IN"),
        fpe_enabled=False,
    )


def make_snapshot(
    tenant: str,
    *,
    connection_id: str,
    object_ref: str,
    columns: list[ColumnClassification] | None = None,
) -> ClassificationSnapshot:
    """A proposed snapshot of the customers shape (``content_hash`` over its columns)."""
    from dps.snapshots import content_hash

    cols = columns if columns is not None else classified_columns()
    policy = load_preset(POLICY_NAME)
    return ClassificationSnapshot(
        id=new_id("snap"),
        tenant_id=tenant,
        job_id=None,
        connection_id=connection_id,
        engine="postgres",
        object_ref=object_ref,
        status="proposed",
        policy_name=policy.name,
        policy_hash=policy.config_hash(),
        shape=customers_shape(),
        columns=cols,
        sample_rows=60,
        overrides=[],
        content_hash=content_hash(cols, []),
        created_at=datetime.now(UTC),
        accepted_at=None,
        accepted_by=None,
    )
