"""The retention job (contract §3.6): expired jobs and their artefacts, stale snapshots, expired
inline secrets and leftover sealed keys — counted, value-free, and dry-run aware."""

from __future__ import annotations

import pytest

from dps import db, jobs
from dps.jobs import JobKind, JobPhase, JobState
from dps.retention import retention_job

pytestmark = pytest.mark.db


async def _seed(handler_ctx) -> tuple[str, str, str]:  # noqa: ANN001
    old = await jobs.create_job(
        tenant_id="t1", kind=JobKind.run, phase=JobPhase.prepare, spec={}, sealed_secret=b"\x01k"
    )
    art = await handler_ctx.artefacts.save(
        tenant_id="t1",
        job_id=old.id,
        kind="evidence_json",
        data=b"{}",
        content_type="application/json",
    )
    fresh = await jobs.create_job(
        tenant_id="t1", kind=JobKind.run, phase=JobPhase.prepare, spec={}, sealed_secret=b"\x01k"
    )
    s = db.schema_ident()
    async with db.acquire_queue("t1") as conn:
        await conn.execute(
            f"UPDATE {s}.dps_job SET state = 'succeeded', expires_at = now() - interval '1 day' "
            "WHERE id = $1",
            old.id,
        )
        await conn.execute(f"UPDATE {s}.dps_job SET state = 'failed' WHERE id = $1", fresh.id)
        await conn.execute(
            f"INSERT INTO {s}.dps_connection (id, tenant_id, name, engine, params, secret_enc, "
            "secret_expires_at) VALUES ('c_x', 't1', 'x', 'postgres', '{}', '\\x01'::bytea, "
            "now() - interval '1 hour')"
        )
        await conn.execute(
            f"INSERT INTO {s}.dps_snapshot (id, tenant_id, connection_id, engine, object_ref, "
            "policy_name, policy_hash, shape, columns, sample_rows, content_hash, created_at) "
            "VALUES ('snap_old', 't1', 'c_x', 'postgres', 'a.b', 'p', 'h', '{}', '[]', 0, 'h', "
            "now() - interval '90 days')"
        )
    return old.id, fresh.id, art


async def test_retention_removes_expired_material(migrated_db, handler_ctx, kek) -> None:  # noqa: ANN001
    old, fresh, art = await _seed(handler_ctx)
    await jobs.ensure_retention_job(6)
    [r] = await jobs.claim_jobs(handler_ctx.worker_id, kinds=(JobKind.retention,), batch=1)
    await retention_job(handler_ctx, r)
    done = await jobs.load_job_internal(r.id)
    assert done is not None
    counts = done.progress.values_by_method
    assert done.state == JobState.succeeded and done.progress.outcome == "succeeded"
    assert counts["retention:jobs"] == 1 and counts["retention:artefacts"] == 1
    assert counts["retention:secrets"] == 1 and counts["retention:snapshots"] == 1
    assert counts["retention:keys"] == 1
    assert await jobs.get_job("t1", old) is None
    assert await handler_ctx.artefacts.get_row("t1", art) is None
    fresh_row = await jobs.load_job_internal(fresh)
    assert fresh_row is not None and fresh_row.key_enc is None
    async with db.acquire_queue("t1") as conn:
        secret = await conn.fetchval(
            f"SELECT secret_enc FROM {db.schema_ident()}.dps_connection WHERE id = 'c_x'"
        )
        snaps = await conn.fetchval(f"SELECT count(*) FROM {db.schema_ident()}.dps_snapshot")
    assert secret is None and snaps == 0


async def test_dry_run_counts_and_changes_nothing(migrated_db, handler_ctx, kek) -> None:  # noqa: ANN001
    old, fresh, art = await _seed(handler_ctx)
    job = await jobs.create_job(
        tenant_id=jobs.SYSTEM_TENANT,
        kind=JobKind.retention,
        phase=JobPhase.retention,
        spec={"dry_run": True},
    )
    [r] = await jobs.claim_jobs(handler_ctx.worker_id, kinds=(JobKind.retention,), batch=1)
    assert r.id == job.id
    await retention_job(handler_ctx, r)
    done = await jobs.load_job_internal(r.id)
    assert done is not None and done.state == JobState.succeeded
    assert done.progress.outcome == "dry_run"
    counts = done.progress.values_by_method
    assert counts == {
        "retention:jobs": 1,
        "retention:artefacts": 1,
        "retention:snapshots": 1,
        "retention:secrets": 1,
        "retention:keys": 1,  # the expired job would be deleted, not purged: counted once
    }
    assert await jobs.get_job("t1", old) is not None
    assert await handler_ctx.artefacts.get_row("t1", art) is not None
    fresh_row = await jobs.load_job_internal(fresh)
    assert fresh_row is not None and fresh_row.key_enc is not None


async def test_retention_keeps_live_material(migrated_db, handler_ctx, kek) -> None:  # noqa: ANN001
    """Running jobs, unexpired terminal jobs, accepted and recent snapshots, snapshots a live job
    uses and unexpired secrets are never touched; a job of another tenant is swept under that
    tenant only."""
    s = db.schema_ident()
    live = await jobs.create_job(
        tenant_id="t2", kind=JobKind.run, phase=JobPhase.prepare, spec={}, sealed_secret=b"\x01k"
    )
    async with db.acquire_queue("t2") as conn:
        await conn.execute(
            f"INSERT INTO {s}.dps_connection (id, tenant_id, name, engine, params, secret_enc, "
            "secret_expires_at) VALUES ('c_y', 't2', 'y', 'postgres', '{}', '\\x01'::bytea, "
            "now() + interval '1 hour')"
        )
        for sid, status in (("snap_acc", "accepted"), ("snap_used", "proposed")):
            await conn.execute(
                f"INSERT INTO {s}.dps_snapshot (id, tenant_id, connection_id, engine, "
                "object_ref, policy_name, policy_hash, shape, columns, sample_rows, "
                "content_hash, status, created_at) VALUES ($1, 't2', 'c_y', 'postgres', $2, "
                "'p', 'h', '{}', '[]', 0, 'h', $3, now() - interval '90 days')",
                sid,
                f"a.{sid}",
                status,
            )
        await conn.execute(
            f"UPDATE {s}.dps_job SET snapshot_id = 'snap_used' WHERE id = $1", live.id
        )
    await jobs.ensure_retention_job(6)
    [r] = await jobs.claim_jobs(handler_ctx.worker_id, kinds=(JobKind.retention,), batch=1)
    await retention_job(handler_ctx, r)
    done = await jobs.load_job_internal(r.id)
    assert done is not None and done.state == JobState.succeeded
    assert all(v == 0 for v in done.progress.values_by_method.values())
    still = await jobs.load_job_internal(live.id)
    assert still is not None and still.key_enc is not None
    async with db.acquire_queue("t2") as conn:
        snaps = await conn.fetchval(f"SELECT count(*) FROM {s}.dps_snapshot")
        secret = await conn.fetchval(f"SELECT secret_enc FROM {s}.dps_connection WHERE id = 'c_y'")
    assert snaps == 2 and secret is not None
