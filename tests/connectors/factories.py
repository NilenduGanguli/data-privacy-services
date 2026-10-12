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
    from dps.layers.connectors.evidence import EvidenceInputs

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


def evidence_inputs(
    mode: str = "obfuscate",
    *,
    connection_name: str = "bank-pg-ro",
    outcome: str = "succeeded",
    tenant: str = "t1",
) -> EvidenceInputs:
    """A finished ``table`` run of ``bank.customers`` into ``bank_uat.customers``: a redacted spec
    (where template ``"created_at" >= :l1``), a run plan for ``full_name`` (fpe_text), ``email``
    (email) and ``dob`` (date_shift, anchored on ``customer_id``), the accepted snapshot, two
    done partitions and a passed verification. Value-free by construction."""
    from datetime import timedelta

    from dps.engine.batch import ColumnPlan
    from dps.jobs import JobInternal, JobKind, JobPhase, JobState, PartitionState, PartitionView
    from dps.jobs_models import JobProgress
    from dps.layers.connectors.base import ResolvedTarget
    from dps.layers.connectors.evidence import EvidenceInputs
    from dps.layers.connectors.targets import RunPlan

    snap = accepted_snapshot("c_src", "bank.customers", tenant=tenant)
    shape = snap.shape
    obfuscate = mode == "obfuscate"
    methods = {
        "full_name": ("PERSON_NAME", MethodSpec(method="fpe_text")),
        "email": ("EMAIL", MethodSpec(method="email", params={"domain": "keep"})),
        "dob": ("DATE_OF_BIRTH", MethodSpec(method="date_shift", params={"window_days": 365})),
    }
    plans = [
        ColumnPlan(
            path=shape.column(name).path,
            name=name,
            entity_type=entity,
            method=method if obfuscate else MethodSpec(method="surrogate"),
            text_column=False,
            reversible=obfuscate,
            kind=shape.column(name).kind,
            length=None if shape.column(name).kind == "text" else shape.column(name).length,
        )
        for name, (entity, method) in methods.items()
    ]
    job_id = new_id("j")
    projection = [c.name for c in shape.columns]
    target = ResolvedTarget(
        job_id=job_id,
        write_mode="table",
        connection_id="c_uat",
        engine="postgres",
        object=ObjectRef(owner="bank_uat", name="customers"),
        key=["customer_id"],
        write_columns=projection,
        column_types={c.name: c.data_type for c in shape.columns},
    )
    prepare = {"created": True, "truncated": False, "collisions": 0, "existing_rows": None}
    plan = RunPlan(
        columns=plans,
        anchor=["customer_id", "CUSTOMER_ID"],
        key=["customer_id"],
        projection=projection,
        target=target,
        source_object="bank.customers",
        policy_name=snap.policy_name,
        policy_hash=snap.policy_hash,
        snapshot_id=snap.id,
        snapshot_hash=snap.content_hash,
        locale="en_IN",
        batch_rows=1000,
        prepare=prepare,
    )
    kid = "k_" + "3f" * 8 if obfuscate else None
    spec: dict[str, Any] = {
        "mode": mode,
        "snapshot_id": snap.id,
        "source": {
            "connection_id": "c_src",
            "object": {"owner": "bank", "name": "customers"},
            "selection": {
                "where": '"created_at" >= :l1',
                "params": {"l1": "<bound>"},
                "query": None,
                "columns": None,
                "max_rows": None,
                "sample": None,
                "mongo_filter": None,
            },
            "parallelism": 2,
            "batch_rows": None,
            "partition_rows": None,
        },
        "target": {
            "write_mode": "table",
            "connection_id": "c_uat",
            "object": {"owner": "bank_uat", "name": "customers"},
            "create_if_missing": True,
            "table_load": "truncate_insert",
            "pk_strategy": None,
            "files": None,
            "ledger_owner": None,
            "confirm_write": False,
            "expected_rows": None,
        },
        "policy": None,
        "consistency": {
            "scope": "key",
            "field_scoped": False,
            "kid": kid,
            "caller_key": False,
            "seed_fingerprint": None if obfuscate else "0123456789abcdef",
            "wrapped": False,
        },
        "group_id": None,
        "purpose": {"ticket": "CHG-48213", "legal_basis": "legitimate interest"},
        "options": {
            "dry_run": False,
            "verify": "strict",
            "unclassified": "detect",
            "tabular": "none",
        },
    }
    now = datetime.now(UTC)
    counters = {"col:full_name": 2500, "col:email": 2500, "col:dob": 2400}
    if obfuscate:
        counters |= {"fpe_text": 2500, "email": 2500, "date_shift": 2400}
        counters["fallback:fpe_domain_too_small"] = 2
    else:
        counters["surrogate"] = 7400
    progress = JobProgress(
        rows_estimated=2500,
        rows_read=2500,
        rows_written=2500,
        partitions_total=2,
        partitions_done=2,
        values_by_method=counters,
        verification={"checked": 7400},
    )
    job = JobInternal(
        id=job_id,
        tenant_id=tenant,
        kind=JobKind.run,
        state=JobState.running,
        phase=JobPhase.finalize,
        mode=mode,
        connection_id="c_src",
        target_connection_id="c_uat",
        object_ref="bank.customers",
        snapshot_id=snap.id,
        spec=spec,
        purpose=spec["purpose"],
        kid=kid,
        progress=progress,
        created_by="k2",
        created_at=now,
        updated_at=now,
        started_at=now - timedelta(seconds=30),
        attempts=1,
        max_attempts=3,
    )
    partitions = [
        PartitionView(
            partition_no=i,
            state=PartitionState.done,
            rows_read=1250,
            rows_written=1250,
            rows_skipped=0,
            batch_no=2,
            attempts=1 + i,
            resumed=i,
            checkpoint_fp="a1b2c3d4",
            started_at=now,
            finished_at=now,
            updated_at=now,
            error_code=None,
            rows_per_second=100.0,
        )
        for i in range(2)
    ]
    verification = {
        "mode": "strict",
        "batches": 4,
        "values_checked": 7400,
        "hits": 0,
        "passed": True,
        "post_sample": {"rows": 200, "detector_hits": 3, "kept_column_hits": 0},
    }
    return EvidenceInputs(
        job=job,
        spec=spec,
        plan=plan,
        snapshot=snap,
        source_conn=make_connection("c_src", roles=["source"], name=connection_name),
        target_conn=make_connection("c_uat", roles=["target"], name="bank-uat"),
        partitions=partitions,
        outcome=outcome,  # type: ignore[arg-type]
        server_version="16.4",
        prepare=prepare,
        files=None,
        verification=verification,
        key_custody={"returned": obfuscate, "wrapped": False, "purged_at": now.isoformat()},
        errors=[],
        engine_version="0.1.0",
        image_digest=None,
    )
