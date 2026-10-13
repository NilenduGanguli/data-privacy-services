"""Run jobs end to end in every write mode: submit → prepare → partitions → finalize (in process)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest

from dps import jobs
from dps.auth import LOCAL_PRINCIPAL
from dps.connections import Connection
from dps.engine.batch import reveal_row
from dps.engine.policy import MethodSpec
from dps.engine.transform.crypto import key_material_from_caller
from dps.jobs import JobState
from dps.jobs_models import ClassificationSnapshot, ObjectRef, RunSpec, SnapshotOverride
from dps.layers.connectors.evidence import get_evidence
from dps.layers.connectors.submit import submit_run
from dps.worker import HandlerContext
from tests.connectors.helpers import (
    db_key_enc,
    discover_and_accept,
    drive,
    json_from_export,
    register_pvc,
)
from tests.fixtures.sources import SourcePg

pytestmark = [pytest.mark.db, pytest.mark.integration, pytest.mark.pg_source]
PURPOSE = {"ticket": "CHG-48213", "legal_basis": "legitimate interest — UAT provisioning"}
RegisterSource = Callable[..., Awaitable[Connection]]
#: In place the key is never rewritten, and every output must fit the existing column: a
#: det_token cannot fit national_id varchar(18) (reviewed to fpe_digits, reversible), and a PAN
#: (4 digits) is below the FPE domain, so tax_id char(10) is masked one-way.
IN_PLACE_REVIEW = [
    SnapshotOverride(path="$.customer_id", method=MethodSpec(method="keep")),
    SnapshotOverride(path="$.national_id", method=MethodSpec(method="fpe_digits")),
    SnapshotOverride(path="$.tax_id", method=MethodSpec(method="mask_partial")),
]


def _same_but_masked(restored: dict[str, Any], before: dict[str, Any]) -> None:
    """Every column revealed or kept equals the source; tax_id is masked one-way."""
    assert {k: v for k, v in restored.items() if k != "tax_id"} == {
        k: v for k, v in before.items() if k != "tax_id"
    }
    if before["tax_id"] is not None:
        assert restored["tax_id"] != before["tax_id"]
        assert restored["tax_id"][-4:] == before["tax_id"][-4:]


def run_spec(
    conn_id: str,
    obj: ObjectRef,
    snap_id: str,
    target: dict[str, Any],
    mode: str = "obfuscate",
    *,
    source: dict[str, Any] | None = None,
    **extra: Any,
) -> RunSpec:
    """A run of ``obj`` with two-way parallelism, 1000-row partitions and 200-row batches;
    ``source`` merges extra source fields (a ``selection``, other batch sizes)."""
    return RunSpec.model_validate(
        {
            "mode": mode,
            "snapshot_id": snap_id,
            "purpose": PURPOSE,
            "target": target,
            "source": {
                "connection_id": conn_id,
                "object": obj.model_dump(),
                "parallelism": 2,
                "partition_rows": 1000,
                "batch_rows": 200,
                **(source or {}),
            },
            **extra,
        }
    )


def _anchor(snap: ClassificationSnapshot) -> tuple[str, str] | None:
    return next(((c.name, c.entity_type) for c in snap.columns if c.anchor and c.entity_type), None)


async def test_table_mode_obfuscate_into_a_created_uat_table(
    handler_ctx: HandlerContext, source_pg: SourcePg, register_source: RegisterSource
) -> None:
    obj = await source_pg.customers_table(rows=2500)
    src = await register_source("t1")
    uat = await register_source("t1", ref="dps/src/test-pg-rw", roles={"target"}, name="uat")
    snap = await discover_and_accept(handler_ctx, "t1", src.id, obj)
    target = {
        "write_mode": "table",
        "connection_id": uat.id,
        "object": {"owner": source_pg.schema, "name": "customers_uat"},
    }
    sub = await submit_run(
        run_spec(src.id, obj, snap.id, target),
        tenant_id="t1",
        principal=LOCAL_PRINCIPAL,
        settings=handler_ctx.settings,
        kek=handler_ctx.kek,
    )
    assert sub.key_material and sub.key_material["dek"] and not sub.replay
    assert "dek" not in str(sub.job.spec) and sub.job.kid == sub.key_material["kid"]
    job = await drive(handler_ctx, "t1", sub.job.id)
    assert job.state == JobState.succeeded, job.error_code
    assert job.progress.partitions_done == 3 and job.progress.rows_written == 2500
    src_rows = {
        r["customer_id"]: r
        for r in await source_pg.admin.fetch(f'SELECT * FROM "{source_pg.schema}".customers')
    }
    out_rows = await source_pg.admin.fetch(f'SELECT * FROM "{source_pg.schema}".customers_uat')
    assert len(out_rows) == 2500
    assert not {r["email"] for r in out_rows} & {r["email"] for r in src_rows.values()}
    assert {r["customer_id"] for r in out_rows} != set(src_rows)  # keys tokenized (fpe)
    assert job.progress.evidence_id is not None
    ev = await get_evidence("t1", job.progress.evidence_id)
    assert ev is not None and ev.pack.outcome == "succeeded" and ev.pack.verification["passed"]
    assert ev.pack.counts["rows_written"] == 2500
    assert ev.pack.target["write_mode"] == "table" and ev.pack.target["created"] is True
    assert ev.pack.verification["post_sample"]["rows"] == 200
    assert await db_key_enc(job.id) is None  # key purged at terminal state
    events = [e for e in handler_ctx.audit.events if e.action == "job.finalize"]  # type: ignore[attr-defined]
    assert events and events[-1].job_id == job.id and events[-1].outcome == "succeeded"
    # every source row comes back with the returned key (keys revealed first, then the anchor)
    key = key_material_from_caller(sub.key_material)
    policy = handler_ctx.engine.policy(snap.policy_name)
    some = out_rows[0]
    restored = reveal_row(
        dict(some),
        reveal_map=ev.pack.reveal_map,
        shape=snap.shape,
        key=key,
        engine=handler_ctx.engine,
        policy=policy,
        anchor=_anchor(snap),
    )
    original = src_rows[restored["customer_id"]]
    for entry in ev.pack.reveal_map:
        name = entry["path"][2:]
        assert entry["reveal"] and restored[name] == original[name], name


async def test_table_mode_query_subset_writes_only_the_projected_columns(
    handler_ctx: HandlerContext, source_pg: SourcePg, register_source: RegisterSource
) -> None:
    """M3 G2 B1: a query that names a subset of the columns reads, creates and writes exactly
    those columns (keys always); no other column exists in the target to be NULL-filled."""
    obj = await source_pg.customers_table(rows=300, name="q_customers")
    src = await register_source("t1")
    uat = await register_source("t1", ref="dps/src/test-pg-rw", roles={"target"}, name="uat-q")
    snap = await discover_and_accept(handler_ctx, "t1", src.id, obj)
    target = {
        "write_mode": "table",
        "connection_id": uat.id,
        "object": {"owner": source_pg.schema, "name": "q_customers_uat"},
    }
    sel = {"query": f'SELECT customer_id, email, segment FROM "{source_pg.schema}".q_customers'}
    sub = await submit_run(
        run_spec(src.id, obj, snap.id, target, source={"selection": sel}),
        tenant_id="t1",
        principal=LOCAL_PRINCIPAL,
        settings=handler_ctx.settings,
        kek=handler_ctx.kek,
    )
    job = await drive(handler_ctx, "t1", sub.job.id)
    assert job.state == JobState.succeeded, job.error_code
    assert job.progress.rows_written == 300
    cols = await source_pg.admin.fetch(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = $1 AND table_name = 'q_customers_uat' ORDER BY ordinal_position",
        source_pg.schema,
    )
    assert [r["column_name"] for r in cols] == ["customer_id", "email", "segment"]
    out = f'"{source_pg.schema}".q_customers_uat'
    src_t = f'"{source_pg.schema}".q_customers'
    count = source_pg.admin.fetchval
    assert await count(f"SELECT count(*) FROM {out}") == 300
    assert await count(f"SELECT count(*) FROM {out} WHERE email IS NULL") == 0
    # source vs target (counts only): no source e-mail survived; segment kept as a multiset
    assert await count(f"SELECT count(*) FROM {out} o JOIN {src_t} s ON o.email = s.email") == 0
    assert (
        await count(
            f"SELECT count(*) FROM (SELECT segment, count(*) FROM {src_t} GROUP BY segment "
            f"EXCEPT SELECT segment, count(*) FROM {out} GROUP BY segment) d"
        )
        == 0
    )
    ev = await get_evidence("t1", job.progress.evidence_id or "")
    assert ev is not None and ev.pack.verification["passed"]


async def test_in_place_round_trips_with_the_returned_key(
    handler_ctx: HandlerContext, source_pg: SourcePg, register_source: RegisterSource
) -> None:
    obj = await source_pg.customers_table(rows=600, name="restore_customers")
    table = f'"{source_pg.schema}".restore_customers'
    before = {
        r["customer_id"]: dict(r) for r in await source_pg.admin.fetch(f"SELECT * FROM {table}")
    }
    rw = await register_source(
        "t1", ref="dps/src/test-pg-rw", roles={"source", "target"}, name="restore-rw"
    )
    snap = await discover_and_accept(handler_ctx, "t1", rw.id, obj, overrides=IN_PLACE_REVIEW)
    target = {"write_mode": "in_place", "confirm_write": True, "expected_rows": 600}
    sub = await submit_run(
        run_spec(rw.id, obj, snap.id, target),
        tenant_id="t1",
        principal=LOCAL_PRINCIPAL,
        settings=handler_ctx.settings,
        kek=handler_ctx.kek,
    )
    job = await drive(handler_ctx, "t1", sub.job.id)
    assert job.state == JobState.succeeded, job.error_code
    after = {
        r["customer_id"]: dict(r) for r in await source_pg.admin.fetch(f"SELECT * FROM {table}")
    }
    assert after.keys() == before.keys()  # keys never rewritten in place
    assert all(after[k]["full_name"] != before[k]["full_name"] for k in after)
    ledger = f'SELECT count(*) FROM "{source_pg.schema}".dps_write_ledger WHERE job_id = $1'
    assert await source_pg.admin.fetchval(ledger, job.id) == 0
    assert job.progress.evidence_id is not None
    ev = await get_evidence("t1", job.progress.evidence_id)
    assert ev is not None
    key = key_material_from_caller(sub.key_material or {})
    policy = handler_ctx.engine.policy(snap.policy_name)
    revealed = {e["path"][2:] for e in ev.pack.reveal_map if e["reveal"]}
    assert {"full_name", "email", "date_of_birth", "national_id", "notes"} <= revealed
    for cid, row in after.items():
        restored = reveal_row(
            row,
            reveal_map=ev.pack.reveal_map,
            shape=snap.shape,
            key=key,
            engine=handler_ctx.engine,
            policy=policy,
            anchor=_anchor(snap),
        )
        _same_but_masked(restored, before[cid])


async def test_in_place_resume_never_applies_a_batch_twice(
    handler_ctx: HandlerContext,
    source_pg: SourcePg,
    register_source: RegisterSource,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    obj = await source_pg.customers_table(rows=600, name="resume_customers")
    table = f'"{source_pg.schema}".resume_customers'
    before = {
        r["customer_id"]: dict(r) for r in await source_pg.admin.fetch(f"SELECT * FROM {table}")
    }
    rw = await register_source(
        "t1", ref="dps/src/test-pg-rw", roles={"source", "target"}, name="resume-rw"
    )
    snap = await discover_and_accept(handler_ctx, "t1", rw.id, obj, overrides=IN_PLACE_REVIEW)
    handler_ctx.settings = handler_ctx.settings.model_copy(update={"job_retry_base_seconds": 0.0})
    real_checkpoint = jobs.checkpoint
    calls = {"n": 0}

    async def crash_once(*args: Any, **kwargs: Any) -> bool:
        calls["n"] += 1
        if calls["n"] == 2:  # batch 2 is committed with its ledger mark, the checkpoint is lost
            raise ConnectionError("worker died between the write and the checkpoint")
        return await real_checkpoint(*args, **kwargs)

    monkeypatch.setattr(jobs, "checkpoint", crash_once)
    sub = await submit_run(
        run_spec(rw.id, obj, snap.id, {"write_mode": "in_place", "confirm_write": True}),
        tenant_id="t1",
        principal=LOCAL_PRINCIPAL,
        settings=handler_ctx.settings,
        kek=handler_ctx.kek,
    )
    job = await drive(handler_ctx, "t1", sub.job.id)
    assert job.state == JobState.succeeded, job.error_code
    assert calls["n"] >= 4 and job.progress.rows_written == 600
    [part] = await jobs.list_partitions("t1", job.id)
    assert part.resumed == 1 and part.rows_written == 600
    assert job.progress.evidence_id is not None
    ev = await get_evidence("t1", job.progress.evidence_id)
    assert ev is not None and ev.pack.counts["resumed_partitions"] == 1
    key = key_material_from_caller(sub.key_material or {})
    policy = handler_ctx.engine.policy(snap.policy_name)
    for row in await source_pg.admin.fetch(f"SELECT * FROM {table}"):
        restored = reveal_row(
            dict(row),
            reveal_map=ev.pack.reveal_map,
            shape=snap.shape,
            key=key,
            engine=handler_ctx.engine,
            policy=policy,
            anchor=_anchor(snap),
        )
        _same_but_masked(restored, before[row["customer_id"]])  # one application per row


async def test_append_offset_to_the_same_table(
    handler_ctx: HandlerContext, source_pg: SourcePg, register_source: RegisterSource
) -> None:
    obj = await source_pg.customers_table(rows=400, name="append_customers")
    rw = await register_source(
        "t1", ref="dps/src/test-pg-rw", roles={"source", "target"}, name="append-rw"
    )
    snap = await discover_and_accept(handler_ctx, "t1", rw.id, obj)
    target = {
        "write_mode": "append",
        "confirm_write": True,
        "pk_strategy": {"kind": "offset", "offset": 90_000_000},
    }
    sub = await submit_run(
        run_spec(rw.id, obj, snap.id, target, mode="synthesize"),
        tenant_id="t1",
        principal=LOCAL_PRINCIPAL,
        settings=handler_ctx.settings,
        kek=handler_ctx.kek,
    )
    assert sub.key_material is None and sub.synthesis_seed
    job = await drive(handler_ctx, "t1", sub.job.id)
    assert job.state == JobState.succeeded, job.error_code
    assert job.progress.rows_written == 400
    ids = [
        r["customer_id"]
        for r in await source_pg.admin.fetch(
            f'SELECT customer_id FROM "{source_pg.schema}".append_customers ORDER BY 1'
        )
    ]
    assert len(ids) == 800 and ids[400:] == [i + 90_000_000 for i in ids[:400]]
    assert job.progress.evidence_id is not None
    ev = await get_evidence("t1", job.progress.evidence_id)
    assert ev is not None and ev.pack.label.startswith("synthetic") and ev.pack.reveal_map == []
    assert ev.pack.consistency["kid"] is None and ev.pack.consistency["seed_fingerprint"]


async def test_files_parquet_to_a_pvc_path_with_a_manifest(
    handler_ctx: HandlerContext,
    source_pg: SourcePg,
    register_source: RegisterSource,
) -> None:
    obj = await source_pg.customers_table(rows=1500, name="files_customers")
    src = await register_source("t1")
    pvc = await register_pvc("t1", handler_ctx, base_path="exports")
    snap = await discover_and_accept(handler_ctx, "t1", src.id, obj)
    target = {
        "write_mode": "files",
        "connection_id": pvc.id,
        "files": {"format": "parquet", "prefix": "crm"},
    }
    sub = await submit_run(
        run_spec(src.id, obj, snap.id, target),
        tenant_id="t1",
        principal=LOCAL_PRINCIPAL,
        settings=handler_ctx.settings,
        kek=handler_ctx.kek,
    )
    job = await drive(handler_ctx, "t1", sub.job.id)
    assert job.state == JobState.succeeded, job.error_code
    manifest = json_from_export(handler_ctx, f"exports/crm/{job.id}/manifest.json")
    assert manifest["rows_total"] == 1500 and len(manifest["objects"]) == 2
    assert {c["name"] for c in manifest["columns"]} >= {"customer_id", "email"}
    for item in manifest["objects"]:
        assert (Path(handler_ctx.settings.export_root) / "exports" / item["key"]).is_file()
    assert job.progress.evidence_id is not None
    ev = await get_evidence("t1", job.progress.evidence_id)
    assert ev is not None
    assert (
        ev.pack.target["files"]["objects"] == 2 and ev.pack.target["files"]["manifest_artefact_id"]
    )
    assert ev.pack.target["files"]["rows"] == 1500


async def test_dry_run_writes_nothing(
    handler_ctx: HandlerContext, source_pg: SourcePg, register_source: RegisterSource
) -> None:
    obj = await source_pg.customers_table(rows=300, name="dry_customers")
    src = await register_source("t1")
    uat = await register_source("t1", ref="dps/src/test-pg-rw", roles={"target"}, name="uat-dry")
    snap = await discover_and_accept(handler_ctx, "t1", src.id, obj)
    target = {
        "write_mode": "table",
        "connection_id": uat.id,
        "object": {"owner": source_pg.schema, "name": "never_made"},
    }
    sub = await submit_run(
        run_spec(src.id, obj, snap.id, target, options={"dry_run": True}),
        tenant_id="t1",
        principal=LOCAL_PRINCIPAL,
        settings=handler_ctx.settings,
        kek=handler_ctx.kek,
    )
    job = await drive(handler_ctx, "t1", sub.job.id)
    assert job.state == JobState.succeeded and job.progress.outcome == "dry_run"
    assert job.progress.verification["checked"] > 0 and job.progress.partitions_total == 0
    exists = f"SELECT to_regclass('\"{source_pg.schema}\".never_made')"
    assert await source_pg.admin.fetchval(exists) is None
    assert job.progress.evidence_id is not None
    ev = await get_evidence("t1", job.progress.evidence_id)
    assert ev is not None and ev.pack.outcome == "dry_run" and ev.pack.counts["rows_written"] == 0
