"""Test helpers: the demo customers shape, synthetic rows and classification snapshots."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any

from dps.config import Settings
from dps.engine.policy import MethodSpec, load_preset
from dps.engine.service import PrivacyEngine
from dps.ids import new_id
from dps.jobs_models import ClassificationSnapshot, ColumnClassification, ObjectRef
from dps.layers.connectors.base import ColumnInfo, Row, TableShape

if TYPE_CHECKING:
    from dps.connections import Connection

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


def make_connection(
    connection_id: str,
    *,
    engine: str = "postgres",
    roles: list[str] | tuple[str, ...],
    tags: tuple[str, ...] | list[str] = (),
    params: dict[str, Any] | None = None,
    name: str | None = None,
    tenant_id: str = "t1",
) -> Connection:
    """An in-memory ``Connection`` (no database row): postgres defaults to a local host."""
    from dps.connections import Connection
    from dps.jobs_models import ConnectionParams

    now = datetime.now(UTC)
    defaults: dict[str, Any] = (
        {"host": "127.0.0.1", "port": 5432, "database": "bank"} if engine == "postgres" else {}
    )
    return Connection(
        id=connection_id,
        tenant_id=tenant_id,
        name=name or connection_id.replace("_", "-"),
        engine=engine,  # type: ignore[arg-type]
        params=ConnectionParams.model_validate(params if params is not None else defaults),
        secret_ref=None if engine == "pvc" else "dps/src/test",
        has_secret=False,
        secret_expires_at=None,
        roles=list(roles),  # type: ignore[arg-type]
        tags=list(tags),
        capabilities={},
        last_test=None,
        created_by=None,
        created_at=now,
        updated_at=now,
    )


def _classified(
    column: ColumnInfo,
    entity: str | None,
    method: MethodSpec,
    synthesize: MethodSpec,
    *,
    text: bool = False,
    anchor: bool = False,
) -> ColumnClassification:
    from dps.engine.taxonomy import entity_info

    return ColumnClassification(
        path=column.path,
        name=column.name,
        data_type=column.data_type,
        kind=column.kind,
        is_key=column.is_key,
        entity_type=entity,
        confidence=0.9 if entity else 0.0,
        sensitivity=entity_info(entity).sensitivity if entity else None,
        detectors=["structural"] if entity else [],
        value_hit_rate=0.9 if entity else 0.0,
        null_rate=0.0,
        distinct_ratio=1.0,
        shapes=["Xxxxx"] if column.kind in ("text", "char") else ["9999"],
        text_column=text,
        anchor=anchor,
        suggested=method,
        method=method,
        synthesize_suggested=synthesize,
        synthesize_method=synthesize,
    )


def accepted_shape(object_ref: str) -> TableShape:
    """The six-column customers shape of :func:`accepted_snapshot`."""
    ref = ObjectRef.parse(object_ref)
    return TableShape(
        object=ref,
        key=["customer_id"],
        key_source="primary_key",
        columns=[
            col("customer_id", "int", data_type="bigint", is_key=True, nullable=False),
            col("full_name", "text", data_type="character varying(120)", length=120),
            col("email", "text", data_type="character varying(254)", length=254),
            col("dob", "date"),
            col("country_code", "char", data_type="character(2)", length=2, nullable=False),
            col("notes", "text"),
        ],
    )


def accepted_snapshot(
    connection_id: str,
    object_ref: str,
    *,
    key_method: MethodSpec | None = None,
    notes_method: MethodSpec | None = None,
    tenant: str = "t1",
) -> ClassificationSnapshot:
    """An **accepted** customers snapshot: ``customer_id`` (key, int, anchor), ``full_name``
    (fpe_text), ``email`` (email), ``dob`` (date_shift), ``country_code`` (kept) and ``notes``
    (free text). Synthesize methods: key kept, the rest surrogates."""
    from dps.snapshots import content_hash

    shape = accepted_shape(object_ref)
    keep, surrogate = MethodSpec(method="keep"), MethodSpec(method="surrogate")
    c = {column.name: column for column in shape.columns}
    columns = [
        _classified(c["customer_id"], "CUSTOMER_ID", key_method or keep, keep, anchor=True),
        _classified(c["full_name"], "PERSON_NAME", MethodSpec(method="fpe_text"), surrogate),
        _classified(
            c["email"], "EMAIL", MethodSpec(method="email", params={"domain": "keep"}), surrogate
        ),
        _classified(
            c["dob"],
            "DATE_OF_BIRTH",
            MethodSpec(method="date_shift", params={"window_days": 365}),
            surrogate,
        ),
        _classified(c["country_code"], None, keep, keep),
        _classified(
            c["notes"],
            "FREE_TEXT",
            notes_method or MethodSpec(method="det_token"),
            surrogate,
            text=True,
        ),
    ]
    policy = load_preset(POLICY_NAME)
    now = datetime.now(UTC)
    return ClassificationSnapshot(
        id=new_id("snap"),
        tenant_id=tenant,
        job_id=None,
        connection_id=connection_id,
        engine="postgres",
        object_ref=object_ref,
        status="accepted",
        policy_name=policy.name,
        policy_hash=policy.config_hash(),
        shape=shape,
        columns=columns,
        sample_rows=200,
        overrides=[],
        content_hash=content_hash(columns, []),
        created_at=now,
        accepted_at=now,
        accepted_by="owner-key",
    )
