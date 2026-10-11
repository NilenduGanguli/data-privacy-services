from __future__ import annotations

import asyncio

import pytest

from dps import db, jobs
from dps.jobs import JobKind, JobPhase, JobState, PartitionState
from dps.layers.connectors.base import KeyRange

pytestmark = pytest.mark.db


def _ranges(n: int) -> list[KeyRange]:
    keys = [[{"t": "int", "v": str(10_000_000 + i * 1000)}] for i in range(n + 1)]
    return [
        KeyRange(lower=None if i == 0 else keys[i], upper=None if i == n - 1 else keys[i + 1])
        for i in range(n)
    ]


async def _prepared(n: int, parallelism: int = 8, tenant: str = "t1") -> str:
    job = await jobs.create_job(
        tenant_id=tenant,
        kind=JobKind.run,
        phase=JobPhase.prepare,
        spec={"source": {"parallelism": parallelism}},
        sealed_secret=b"\x01k",
    )
    [c] = await jobs.claim_jobs("prep", kinds=(JobKind.run,), batch=1)
    assert await jobs.add_partitions("prep", c.id, _ranges(n), plan={"columns": []}) == n
    return job.id


async def test_add_partitions_is_atomic_and_fenced(migrated_db) -> None:  # noqa: ANN001
    job_id = await _prepared(4)
    job = await jobs.get_job("t1", job_id)
    assert (job.phase, job.state, job.progress.partitions_total, job.plan) == (
        JobPhase.partitions,
        JobState.waiting,
        4,
        {"columns": []},
    )
    assert await jobs.add_partitions("someone-else", job_id, _ranges(2)) == 0


async def test_claim_partitions_respects_parallelism_and_pause(migrated_db) -> None:  # noqa: ANN001
    job_id = await _prepared(6, parallelism=2)
    first = await jobs.claim_partitions("w1", batch=10)
    assert (
        len(first) == 2 and [p.partition_no for p in first] == [0, 1] and first[0].lower_key is None
    )
    assert await jobs.claim_partitions("w2", batch=10) == []
    await jobs.pause("t1", job_id)
    await jobs.finish_partition("w1", first[0].id, PartitionState.done)
    assert await jobs.claim_partitions("w2", batch=10) == []  # paused: no new claims
    await jobs.resume("t1", job_id)
    assert len(await jobs.claim_partitions("w2", batch=10)) == 1


async def test_checkpoint_deltas_merge_and_fencing(migrated_db) -> None:  # noqa: ANN001
    await _prepared(1)
    [p] = await jobs.claim_partitions("w1", batch=1)
    key = [{"t": "int", "v": "10000500"}]
    for _ in range(2):
        assert await jobs.checkpoint(
            "w1",
            p.id,
            last_key=key,
            batch_no=1,
            rows_read=10,
            rows_written=9,
            rows_skipped=1,
            values_by_method={"det_token": 9, "fallback:fpe_disabled": 2},
            verify_checked=9,
        )
    assert not await jobs.checkpoint(
        "w2",
        p.id,
        last_key=key,
        batch_no=3,
        rows_read=1,
        rows_written=1,
        rows_skipped=0,
        values_by_method={},
        verify_checked=0,
    )
    [view] = await jobs.list_partitions("t1", p.job_id)
    assert (view.rows_read, view.rows_written, view.rows_skipped) == (20, 18, 2)
    assert view.checkpoint_fp is not None and "10000500" not in view.checkpoint_fp
    job = await jobs.get_job("t1", p.job_id)
    assert job.progress.values_by_method == {"det_token": 18, "fallback:fpe_disabled": 4}


async def test_finalize_transition_happens_exactly_once(migrated_db, queue_settings) -> None:  # noqa: ANN001
    queue_settings(job_tenant_max_running=100)
    job_id = await _prepared(20, parallelism=20)
    parts = await jobs.claim_partitions("w1", batch=20)
    assert len(parts) == 20
    results = await asyncio.gather(
        *(jobs.finish_partition("w1", p.id, PartitionState.done) for p in parts)
    )
    assert all(results)
    job = await jobs.get_job("t1", job_id)
    assert (job.phase, job.state) == (JobPhase.finalize, JobState.queued)
    [fin] = await jobs.claim_jobs("w9", kinds=(JobKind.run,), batch=5)
    assert fin.id == job_id and fin.phase == JobPhase.finalize


async def test_dead_partition_still_finalizes(migrated_db, queue_settings) -> None:  # noqa: ANN001
    queue_settings(partition_lease_seconds=1, partition_max_attempts=1)
    job_id = await _prepared(1)
    async with db.acquire_queue(None) as conn:
        await conn.execute(
            "UPDATE dps.dps_job_partition SET max_attempts = 1 WHERE job_id = $1", job_id
        )
    await jobs.claim_partitions("w1", batch=1)
    await asyncio.sleep(1.3)
    result = await jobs.reap(backoff_base=0.0, backoff_cap=0.0)
    assert result.partitions_dead == 1
    job = await jobs.get_job("t1", job_id)
    assert job.phase == JobPhase.finalize and job.progress.partitions_failed == 1


async def test_lease_reclaim_requeues_and_counts_resume(migrated_db, queue_settings) -> None:  # noqa: ANN001
    queue_settings(partition_lease_seconds=1)
    await _prepared(1)
    [p] = await jobs.claim_partitions("w1", batch=1)
    await asyncio.sleep(1.3)
    assert (await jobs.reap(backoff_base=0.0, backoff_cap=0.0)).partitions_requeued == 1
    assert not await jobs.checkpoint(
        "w1",
        p.id,
        last_key=[{"t": "int", "v": "1"}],
        batch_no=1,
        rows_read=1,
        rows_written=1,
        rows_skipped=0,
        values_by_method={},
        verify_checked=0,
    )
    [again] = await jobs.claim_partitions("w2", batch=1)
    assert again.id == p.id and again.resumed == 1 and again.attempts == 2


async def test_retry_partition_backoff_then_dead(migrated_db) -> None:  # noqa: ANN001
    job_id = await _prepared(1)
    async with db.acquire_queue(None) as conn:
        await conn.execute(
            "UPDATE dps.dps_job_partition SET max_attempts = 2 WHERE job_id = $1", job_id
        )
    for expected in (PartitionState.queued, PartitionState.dead):
        [p] = await jobs.claim_partitions("w1", batch=1)
        assert (
            await jobs.retry_partition(
                "w1",
                p.id,
                error_code="target_error",
                error="timeout",
                backoff_base=0.0,
                backoff_cap=0.0,
            )
            == expected
        )
        async with db.acquire_queue(None) as conn:
            await conn.execute(
                "UPDATE dps.dps_job_partition SET run_after = now() WHERE job_id = $1", job_id
            )
    assert (await jobs.get_job("t1", job_id)).phase == JobPhase.finalize


async def test_partition_control_and_cancel(migrated_db) -> None:  # noqa: ANN001
    job_id = await _prepared(3)
    [a, b] = await jobs.claim_partitions("w1", batch=2)
    assert await jobs.partition_control("w1", a.id) == "continue"
    assert await jobs.partition_control("w2", a.id) == "lost"
    await jobs.pause("t1", job_id)
    assert await jobs.partition_control("w1", a.id) == "pause"
    await jobs.resume("t1", job_id)
    canceled = await jobs.cancel("t1", job_id)
    assert canceled.phase == JobPhase.finalize and canceled.state == JobState.queued
    assert canceled.progress.outcome == "canceled"
    assert await jobs.partition_control("w1", a.id) == "cancel"
    assert not await jobs.checkpoint(
        "w1",
        a.id,
        last_key=[{"t": "int", "v": "1"}],
        batch_no=1,
        rows_read=1,
        rows_written=1,
        rows_skipped=0,
        values_by_method={},
        verify_checked=0,
    )
    await jobs.abandon_partition("w1", a.id)
    states = {v.partition_no: v.state for v in await jobs.list_partitions("t1", job_id)}
    assert set(states.values()) == {PartitionState.canceled}
    with pytest.raises(jobs.JobStateError) as err:
        await jobs.cancel("t1", job_id)
    assert err.value.code == "job_finalizing"
    assert b.id != a.id


async def test_retry_requires_key_material_and_requeues(migrated_db) -> None:  # noqa: ANN001
    job_id = await _prepared(2)
    parts = await jobs.claim_partitions("w1", batch=2)
    await jobs.finish_partition("w1", parts[0].id, PartitionState.done)
    await jobs.finish_partition(
        "w1", parts[1].id, PartitionState.failed, error_code="leakage", error="leak"
    )
    [fin] = await jobs.claim_jobs("w1", kinds=(JobKind.run,), batch=1)
    await jobs.advance_job(
        "w1",
        fin.id,
        phase=JobPhase.finalize,
        state=JobState.failed,
        progress={"outcome": "failed"},
        purge_secret=True,
    )
    with pytest.raises(jobs.KeyMaterialRequired):
        await jobs.retry("t1", job_id)
    job = await jobs.retry("t1", job_id, sealed_secret=b"\x01new")
    assert (job.phase, job.state, job.progress.outcome) == (
        JobPhase.partitions,
        JobState.waiting,
        "",
    )
    [p] = await jobs.claim_partitions("w2", batch=5)
    assert p.partition_no == 1 and p.attempts == 1


async def test_reset_progress_and_throughput_and_stats(migrated_db) -> None:  # noqa: ANN001
    job_id = await _prepared(1)
    [p] = await jobs.claim_partitions("w1", batch=1)
    await jobs.checkpoint(
        "w1",
        p.id,
        last_key=[{"t": "int", "v": "1"}],
        batch_no=1,
        rows_read=5,
        rows_written=5,
        rows_skipped=0,
        values_by_method={"keep": 5},
        verify_checked=5,
    )
    assert await jobs.sample_throughput(min_interval_s=0) == 1
    assert len((await jobs.get_job("t1", job_id)).progress.throughput) == 1
    assert await jobs.reset_partition_progress("w1", p.id)
    [view] = await jobs.list_partitions("t1", job_id)
    assert (view.rows_written, view.batch_no, view.checkpoint_fp) == (0, 0, None)
    await jobs.register_worker("w1", role="worker", version="t")
    stats = await jobs.tenant_stats("t1")
    assert stats.workers_online == 1 and stats.running == 1 and stats.tenant_max_running == 8
    assert {r["table"] for r in await jobs.queue_stats()} == {"dps_job", "dps_job_partition"}


# --- added (M2 build) -------------------------------------------------------------------------


async def test_tenant_cap_counts_only_partitions_a_job_may_run(migrated_db) -> None:  # noqa: ANN001
    async with db.acquire_queue(None) as conn:
        await conn.execute("INSERT INTO dps.dps_tenant (tenant_id, max_running) VALUES ('t1', 3)")
    narrow = await _prepared(5, parallelism=1)  # older job: one slot, four partitions waiting
    wide = await _prepared(5, parallelism=2)
    claimed = await jobs.claim_partitions("w1", batch=10)
    by_job = {jid: sum(p.job_id == jid for p in claimed) for jid in (narrow, wide)}
    assert by_job == {
        narrow: 1,
        wide: 2,
    }  # the older job's queued backlog does not starve the newer
    assert await jobs.claim_partitions("w2", batch=10) == []  # tenant cap reached


async def test_controls_refuse_states_they_do_not_apply_to(migrated_db) -> None:  # noqa: ANN001
    disc = await jobs.create_job(
        tenant_id="t1",
        kind=JobKind.discover,
        phase=JobPhase.discover,
        spec={},
        sealed_secret=b"\x01k",
    )
    for control, code in ((jobs.pause, "job_not_pausable"), (jobs.resume, "job_not_paused")):
        with pytest.raises(jobs.JobStateError) as err:
            await control("t1", disc.id)
        assert err.value.code == code and err.value.status == 409
    with pytest.raises(jobs.JobStateError) as err2:
        await jobs.retry("t1", disc.id)
    assert err2.value.code == "job_not_retryable"
    canceled = await jobs.cancel("t1", disc.id)  # not a run job: canceled at once, key purged
    assert canceled.state == JobState.canceled and canceled.progress.outcome == "canceled"
    assert canceled.finished_at is not None and canceled.expires_at is not None
    assert (await jobs.load_job_internal(disc.id)).key_enc is None
    with pytest.raises(jobs.JobStateError) as err3:
        await jobs.cancel("t1", disc.id)
    assert err3.value.code == "job_terminal"
    assert await jobs.cancel("t2", disc.id) is None  # another tenant's job is invisible


async def test_canceling_a_paused_job_finalizes_and_releases_held_partitions(
    migrated_db, queue_settings
) -> None:  # noqa: ANN001
    queue_settings(partition_lease_seconds=1)
    job_id = await _prepared(2)
    [held, _other] = await jobs.claim_partitions("w1", batch=2)
    await jobs.pause("t1", job_id)
    await jobs.cancel("t1", job_id)
    await asyncio.sleep(1.3)
    result = await jobs.reap(backoff_base=0.0, backoff_cap=0.0)  # the holder never came back
    assert result.partitions_released == 2 and result.partitions_requeued == 0
    assert await jobs.partition_control("w1", held.id) == "cancel"
    [fin] = await jobs.claim_jobs("w9", kinds=(JobKind.run,), batch=1)
    assert (
        fin.id == job_id and fin.phase == JobPhase.finalize and fin.progress.outcome == "canceled"
    )
