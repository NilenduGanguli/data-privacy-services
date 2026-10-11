from __future__ import annotations

import pytest
from pydantic import ValidationError

from dps.jobs_models import (
    ClassificationSnapshot,
    ColumnClassification,
    ConnectionSpec,
    DiscoverSpec,
    JobProgress,
    JobSecret,
    ObjectRef,
    PkOffset,
    Purpose,
    RunSpec,
    Selection,
    SnapshotOverride,
    SpecError,
    TargetSpec,
)
from dps.layers.connectors.base import ResolvedTarget

PG = {"host": "source-pg", "port": 5432, "database": "bank"}


def test_connection_spec_rules() -> None:
    ok = ConnectionSpec(
        name="bank-pg-ro", engine="postgres", params=PG, secret_ref="dps/src/bank-pg-ro"
    )
    assert ok.roles == {"source"} and ok.tags == []
    inline = ConnectionSpec(
        name="dev-pg",
        engine="postgres",
        params=PG,
        credentials={"username": "u", "password": "hunter2-not-shown"},
    )
    assert inline.credentials is not None and "hunter2-not-shown" not in repr(inline)
    bad = [
        dict(name="x", engine="postgres", params=PG),  # no secret
        dict(
            name="x",
            engine="postgres",
            params=PG,
            secret_ref="a",
            credentials={"username": "u", "password": "p"},
        ),
        dict(name="Bad Name", engine="postgres", params=PG, secret_ref="a"),
        dict(name="x", engine="postgres", params=PG, secret_ref="../etc"),
        dict(name="x", engine="postgres", params={"database": "bank"}, secret_ref="a"),  # no host
        dict(name="x", engine="s3", params={}, secret_ref="a", roles={"target"}),  # no bucket
        dict(name="x", engine="pvc", params={"base_path": "x"}, secret_ref="a", roles={"target"}),
        dict(name="x", engine="s3", params={"bucket": "b"}, secret_ref="a", roles={"source"}),
        dict(name="x", engine="postgres", params={**PG, "options": {"evil": "1"}}, secret_ref="a"),
        dict(
            name="x",
            engine="postgres",
            params=PG,
            credentials={"username": "u", "password": "p"},
            tags=["production"],
        ),
    ]
    for kw in bad:
        with pytest.raises(ValidationError):
            ConnectionSpec(**kw)


def test_selection_exclusivity_and_param_names() -> None:
    sel = Selection(where="created_at >= :since", params={"since": "2025-01-01"})
    assert sel.params["since"] == "2025-01-01"
    for kw in (
        dict(where="a = 1", query="SELECT 1"),
        dict(where="a = 1", mongo_filter={"a": 1}),
        dict(params={"Bad-Name": 1}),
        dict(columns=[]),
        dict(max_rows=0),
    ):
        with pytest.raises(ValidationError):
            Selection(**kw)


def test_target_and_pk_strategy_parsing() -> None:
    t = TargetSpec.model_validate(
        {
            "write_mode": "append",
            "pk_strategy": {"kind": "offset", "offset": 90000000},
            "confirm_write": True,
        }
    )
    assert (
        isinstance(t.pk_strategy, PkOffset)
        and t.table_load == "truncate_insert"
        and t.create_if_missing
    )
    with pytest.raises(ValidationError):
        TargetSpec.model_validate({"write_mode": "append", "pk_strategy": {"kind": "random"}})


def test_run_and_discover_specs() -> None:
    run = RunSpec.model_validate(
        {
            "mode": "obfuscate",
            "snapshot_id": "snap_1",
            "source": {"connection_id": "c_1", "object": {"owner": "bank", "name": "customers"}},
            "target": {
                "write_mode": "table",
                "connection_id": "c_2",
                "object": {"owner": "bank_uat", "name": "customers"},
            },
            "purpose": {
                "ticket": "CHG-48213",
                "legal_basis": "legitimate interest — UAT provisioning",
            },
        }
    )
    assert (
        run.source.parallelism == 4
        and run.options.verify == "strict"
        and run.consistency.scope == "key"
    )
    with pytest.raises(ValidationError):
        Purpose(ticket="ab", legal_basis="x")
    with pytest.raises(ValidationError):  # selection only with a single object
        DiscoverSpec(
            connection_id="c_1",
            objects=[ObjectRef(name="a"), ObjectRef(name="b")],
            selection=Selection(where="a = 1"),
        )
    assert DiscoverSpec(connection_id="c_1", objects=[ObjectRef(name="a")]).mode == "obfuscate"


def test_progress_secret_and_snapshot_models_round_trip() -> None:
    p = JobProgress(rows_read=10, throughput=[("2026-10-11T10:00:00Z", 1200.0)])
    assert JobProgress.model_validate_json(p.model_dump_json()) == p and p.outcome == ""
    assert JobSecret().key_material is None
    col = ColumnClassification(
        path="$.email",
        name="email",
        data_type="varchar(254)",
        kind="text",
        is_key=False,
        entity_type="EMAIL",
        confidence=0.99,
        sensitivity="MEDIUM",
        detectors=["structural"],
        value_hit_rate=0.98,
        null_rate=0.0,
        distinct_ratio=1.0,
        shapes=["xxxx@xxxx.xx"],
        text_column=False,
        anchor=False,
        suggested={"method": "email"},
        method={"method": "email"},
    )
    snap = ClassificationSnapshot(
        id="snap_1",
        tenant_id="t",
        job_id=None,
        connection_id="c",
        engine="postgres",
        object_ref="bank.customers",
        status="proposed",
        policy_name="p",
        policy_hash="h",
        shape={
            "object": {"owner": "bank", "name": "customers"},
            "columns": [],
            "key": [],
            "key_source": "none",
        },
        columns=[col],
        sample_rows=10,
        overrides=[],
        content_hash="h",
        created_at="2026-10-11T00:00:00Z",
        accepted_at=None,
        accepted_by=None,
    )
    assert (
        ClassificationSnapshot.model_validate_json(snap.model_dump_json()).columns[0].method.method
        == "email"
    )
    assert (
        SnapshotOverride(path="$.email", method={"method": "det_token"}).method.method
        == "det_token"
    )


def test_spec_error_carries_code_and_status() -> None:
    err = SpecError("confirm_write_required", 422, "in_place and append need confirm_write: true")
    assert (err.code, err.status) == ("confirm_write_required", 422) and "confirm_write" in str(err)


# --- added (M2 build) -------------------------------------------------------------------------


def test_file_stores_are_target_only() -> None:
    ok = ConnectionSpec(
        name="exports", engine="s3", params={"bucket": "dps-out"}, secret_ref="a", roles={"target"}
    )
    assert ok.roles == {"target"}
    for roles in ({"source"}, {"source", "target"}):
        with pytest.raises(ValidationError):
            ConnectionSpec(
                name="exports",
                engine="s3",
                params={"bucket": "dps-out"},
                secret_ref="a",
                roles=roles,
            )


def test_spec_validation_errors_never_echo_values() -> None:
    with pytest.raises(ValidationError) as err:
        Selection(where="pan = :pan", params={"pan": {"v": "4111111111111111"}})
    assert "4111111111111111" not in str(err.value)
    with pytest.raises(ValidationError) as err2:
        ConnectionSpec(
            name="dev-pg",
            engine="postgres",
            params=PG,
            credentials={"username": "u", "password": 48213977},
        )
    assert "48213977" not in str(err2.value)


def test_resolved_target_defaults() -> None:
    t = ResolvedTarget(job_id="j_1", write_mode="files", connection_id="c_1", engine="s3")
    assert t.object is None and t.key == [] and t.write_columns == [] and t.column_types == {}
    assert t.table_load == "truncate_insert" and not t.generated_key and t.target_shape is None
