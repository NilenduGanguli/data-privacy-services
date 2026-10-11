from __future__ import annotations

import asyncio

import pytest

from dps import db, jobs
from dps.jobs import JobKind, JobPhase, JobState

pytestmark = pytest.mark.db


async def _job(
    tenant: str = "t1", kind: JobKind = JobKind.run, phase: JobPhase = JobPhase.prepare, **kw
):  # noqa: ANN003, ANN202
    return await jobs.create_job(
        tenant_id=tenant, kind=kind, phase=phase, spec={"source": {"parallelism": 2}}, **kw
    )


async def test_create_get_list_with_cursor_and_filters(migrated_db) -> None:  # noqa: ANN001
    made = [await _job() for _ in range(3)] + [
        await _job(kind=JobKind.discover, phase=JobPhase.discover)
    ]
    page1, cursor = await jobs.list_jobs("t1", limit=2)
    page2, cursor2 = await jobs.list_jobs("t1", limit=2, cursor=cursor)
    assert cursor is not None and cursor2 is None  # four jobs: two full pages, then no cursor
    assert [j.id for j in page1 + page2] == [j.id for j in reversed(made)]
    assert len((await jobs.list_jobs("t1", kind=JobKind.discover))[0]) == 1
    assert (await jobs.get_job("t1", made[0].id)).state == JobState.queued
    assert await jobs.get_job("t2", made[0].id) is None  # RLS
    with pytest.raises(ValueError):
        await jobs.list_jobs("t1", cursor="not-a-cursor")


async def test_idempotency_key_returns_the_first_job(migrated_db) -> None:  # noqa: ANN001
    first = await _job(idempotency_key="req-1")
    again = await _job(idempotency_key="req-1")
    assert again.id == first.id


async def test_queue_full_per_tenant(migrated_db) -> None:  # noqa: ANN001
    async with db.acquire_queue(None) as conn:
        await conn.execute("INSERT INTO dps.dps_tenant (tenant_id, max_queued) VALUES ('t1', 2)")
    await _job()
    await _job()
    with pytest.raises(jobs.QueueFull):
        await _job()
    await _job("t2")


async def test_claim_order_kinds_and_lease(migrated_db) -> None:  # noqa: ANN001
    low = await _job(priority=200)
    high = await _job(priority=10)
    disc = await _job(kind=JobKind.discover, phase=JobPhase.discover)
    claimed = await jobs.claim_jobs("w1", kinds=(JobKind.run,), batch=5)
    assert [c.id for c in claimed] == [high.id, low.id]
    assert all(
        c.state == JobState.running and c.locked_by == "w1" and c.attempts == 1 for c in claimed
    )
    assert [c.id for c in await jobs.claim_jobs("w2", kinds=(JobKind.discover,), batch=5)] == [
        disc.id
    ]


async def test_concurrent_claimers_never_double_claim(migrated_db) -> None:  # noqa: ANN001
    async with db.acquire_queue(None) as conn:
        await conn.execute("INSERT INTO dps.dps_tenant (tenant_id, max_running) VALUES ('t1', 100)")
    ids = {(await _job()).id for _ in range(40)}

    async def claimer(name: str) -> list[str]:
        got: list[str] = []
        while batch := await jobs.claim_jobs(name, kinds=(JobKind.run,), batch=3):
            got += [c.id for c in batch]
        return got

    results = await asyncio.gather(*(claimer(f"w{i}") for i in range(8)))
    flat = [i for r in results for i in r]
    assert len(flat) == len(set(flat)) == 40 and set(flat) == ids


async def test_tenant_running_cap(migrated_db) -> None:  # noqa: ANN001
    async with db.acquire_queue(None) as conn:
        await conn.execute("INSERT INTO dps.dps_tenant (tenant_id, max_running) VALUES ('t1', 2)")
    for _ in range(5):
        await _job()
    other = await _job("t2")
    claimed = await jobs.claim_jobs("w1", kinds=(JobKind.run,), batch=10)
    assert sum(c.tenant_id == "t1" for c in claimed) == 2 and other.id in {c.id for c in claimed}
    assert await jobs.claim_jobs("w2", kinds=(JobKind.run,), batch=10) == []


async def test_advance_job_terminal_purges_key_and_sets_expiry(migrated_db) -> None:  # noqa: ANN001
    job = await _job(sealed_secret=b"\x01sealed")
    await jobs.claim_jobs("w1", kinds=(JobKind.run,), batch=1)
    assert await jobs.advance_job(
        "w1",
        job.id,
        phase=JobPhase.finalize,
        state=JobState.succeeded,
        progress={"outcome": "succeeded"},
        purge_secret=True,
    )
    internal = await jobs.load_job_internal(job.id)
    assert internal is not None and internal.key_enc is None and internal.finished_at is not None
    assert (
        29 <= (internal.expires_at - internal.finished_at).days <= 30
        and internal.progress.outcome == "succeeded"
    )


async def test_phase_change_resets_attempts_and_requeues(migrated_db) -> None:  # noqa: ANN001
    job = await _job()
    await jobs.claim_jobs("w1", kinds=(JobKind.run,), batch=1)
    assert await jobs.advance_job(
        "w1", job.id, phase=JobPhase.finalize, state=JobState.queued, delay_seconds=0
    )
    [again] = await jobs.claim_jobs("w2", kinds=(JobKind.run,), batch=1)
    assert again.phase == JobPhase.finalize and again.attempts == 1


async def test_retry_job_backoff_then_dead(migrated_db) -> None:  # noqa: ANN001
    job = await jobs.create_job(
        tenant_id="t1", kind=JobKind.discover, phase=JobPhase.discover, spec={}
    )
    for expected in (JobState.queued, JobState.queued, JobState.dead):
        async with db.acquire_queue(None) as conn:
            await conn.execute("UPDATE dps.dps_job SET run_after = now() WHERE id = $1", job.id)
        [c] = await jobs.claim_jobs("w1", kinds=(JobKind.discover,), batch=1)
        assert (
            await jobs.retry_job(
                "w1",
                c.id,
                error_code="source_error",
                error="connection refused",
                backoff_base=0.0,
                backoff_cap=0.0,
            )
            == expected
        )


async def test_release_does_not_charge_an_attempt(migrated_db) -> None:  # noqa: ANN001
    job = await _job()
    await jobs.claim_jobs("w1", kinds=(JobKind.run,), batch=1)
    await jobs.release("w1", job_ids=[job.id], partition_ids=[])
    internal = await jobs.load_job_internal(job.id)
    assert (
        internal.state == JobState.queued and internal.attempts == 0 and internal.locked_by is None
    )


async def test_worker_registry_and_retention_scheduling(migrated_db) -> None:  # noqa: ANN001
    await jobs.register_worker("w1", role="worker", version="0.1.0")
    await jobs.worker_heartbeat("w1", busy=2, draining=False)
    assert (
        await jobs.ensure_retention_job(6) is True and await jobs.ensure_retention_job(6) is False
    )
    [r] = await jobs.claim_jobs("w1", kinds=(JobKind.retention,), batch=5)
    assert r.tenant_id == jobs.SYSTEM_TENANT and r.phase == JobPhase.retention
    await jobs.unregister_worker("w1")


def test_worker_id_shape() -> None:
    host, pid, suffix = jobs.worker_id().rsplit(":", 2)
    assert host and pid.isdigit() and len(suffix) == 6


# --- added (M2 build) -------------------------------------------------------------------------


async def test_claims_are_fifo_when_the_batch_is_smaller_than_the_eligible_set(migrated_db) -> None:  # noqa: ANN001
    async with db.acquire_queue(None) as conn:
        await conn.execute("INSERT INTO dps.dps_tenant (tenant_id, max_running) VALUES ('t1', 100)")
    later = [await _job(priority=100) for _ in range(5)]
    first = [await _job(priority=50) for _ in range(5)]
    expected = [j.id for j in first + later]
    got: list[str] = []
    while batch := await jobs.claim_jobs("w1", kinds=(JobKind.run,), batch=3):
        got += [c.id for c in batch]
    assert got == expected


async def test_idempotent_replay_is_not_refused_by_a_full_queue(migrated_db) -> None:  # noqa: ANN001
    async with db.acquire_queue(None) as conn:
        await conn.execute("INSERT INTO dps.dps_tenant (tenant_id, max_queued) VALUES ('t1', 1)")
    first = await _job(idempotency_key="req-9")
    assert (await _job(idempotency_key="req-9")).id == first.id
    with pytest.raises(jobs.QueueFull):
        await _job(idempotency_key="req-10")


async def test_public_job_never_carries_sealed_blobs(migrated_db) -> None:  # noqa: ANN001
    job = await _job(sealed_secret=b"\x01key", sealed_selection=b"\x01sel")
    dumped = (await jobs.get_job("t1", job.id)).model_dump()
    assert "key_enc" not in dumped and "spec_enc" not in dumped and "locked_by" not in dumped
    internal = await jobs.load_job_internal(job.id)
    assert internal.key_enc == b"\x01key" and internal.spec_enc == b"\x01sel"
