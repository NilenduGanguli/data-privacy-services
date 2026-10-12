"""The worker ``Consumer`` (contract §3.5): leased claims, dispatch, lost-lease cancellation, drain."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import pytest

from dps import db, jobs
from dps.jobs import JobKind, JobPhase, JobState, PartitionState
from dps.layers.connectors.base import KeyRange
from dps.worker import Consumer

pytestmark = pytest.mark.db


async def _wait(predicate: Callable[[], Awaitable[bool]], timeout: float = 10.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not await predicate():
        assert asyncio.get_running_loop().time() < deadline, "timed out"
        await asyncio.sleep(0.05)


async def _state(job_id: str, state: JobState) -> bool:
    job = await jobs.get_job("t1", job_id)
    return job is not None and job.state == state


async def test_consumer_dispatches_jobs_and_partitions(
    migrated_db, handler_ctx, queue_settings
) -> None:  # noqa: ANN001
    settings = queue_settings(job_poll_interval_seconds=0.1, job_reaper_interval_seconds=0.2)
    seen: list[str] = []

    async def on_job(ctx, job):  # noqa: ANN001, ANN202
        seen.append(f"{job.phase}:{job.id}")
        if job.phase == JobPhase.prepare:
            await jobs.add_partitions(ctx.worker_id, job.id, [KeyRange(), KeyRange()])
        else:
            await jobs.advance_job(ctx.worker_id, job.id, phase=job.phase, state=JobState.succeeded)

    async def on_part(ctx, job, part):  # noqa: ANN001, ANN202
        seen.append(f"part:{part.partition_no}")
        await jobs.finish_partition(ctx.worker_id, part.id, PartitionState.done)

    run_job = await jobs.create_job(
        tenant_id="t1",
        kind=JobKind.run,
        phase=JobPhase.prepare,
        spec={"source": {"parallelism": 2}},
    )
    consumer = Consumer(
        settings,
        kinds=(JobKind.run,),
        handlers={JobPhase.prepare: on_job, JobPhase.finalize: on_job},
        partition_handler=on_part,
        concurrency=4,
        ctx=handler_ctx,
    )
    assert consumer.worker_id == handler_ctx.worker_id and consumer.in_flight == 0
    task = asyncio.create_task(consumer.run())
    await _wait(lambda: _state(run_job.id, JobState.succeeded))
    consumer.stop()
    await task
    assert seen[0] == f"prepare:{run_job.id}" and seen[-1] == f"finalize:{run_job.id}"
    assert sorted(s for s in seen if s.startswith("part:")) == ["part:0", "part:1"]
    async with db.acquire_queue(None) as conn:  # drained workers unregister
        left = await conn.fetchval(
            f"SELECT count(*) FROM {db.schema_ident()}.dps_worker WHERE worker_id = $1",
            handler_ctx.worker_id,
        )
    assert left == 0


async def test_reclaimed_task_is_canceled_and_drain_releases(
    migrated_db, handler_ctx, queue_settings
) -> None:  # noqa: ANN001
    settings = queue_settings(
        job_lease_seconds=2, job_poll_interval_seconds=0.1, job_drain_timeout_seconds=0.2
    )
    started = asyncio.Event()
    canceled = asyncio.Event()

    async def slow(ctx, job):  # noqa: ANN001, ANN202
        started.set()
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            canceled.set()
            raise

    a = await jobs.create_job(
        tenant_id="t1", kind=JobKind.discover, phase=JobPhase.discover, spec={}
    )
    consumer = Consumer(
        settings,
        kinds=(JobKind.discover,),
        handlers={JobPhase.discover: slow},
        partition_handler=None,
        concurrency=1,
        ctx=handler_ctx,
    )
    task = asyncio.create_task(consumer.run())
    await asyncio.wait_for(started.wait(), 5)
    async with db.acquire_queue(None) as conn:  # someone else "owns" it now
        await conn.execute(
            f"UPDATE {db.schema_ident()}.dps_job SET locked_by = 'other' WHERE id = $1", a.id
        )
    await asyncio.wait_for(canceled.wait(), 5)
    started.clear()
    b = await jobs.create_job(
        tenant_id="t1", kind=JobKind.discover, phase=JobPhase.discover, spec={}
    )
    await asyncio.wait_for(started.wait(), 5)
    consumer.stop()
    await task
    internal = await jobs.load_job_internal(b.id)
    assert internal is not None
    assert internal.state == JobState.queued and internal.attempts == 0
    assert internal.locked_by is None


async def test_a_job_without_a_handler_is_released_untouched(
    migrated_db, handler_ctx, queue_settings
) -> None:  # noqa: ANN001
    settings = queue_settings(job_poll_interval_seconds=0.1)
    job = await jobs.create_job(tenant_id="t1", kind=JobKind.run, phase=JobPhase.prepare, spec={})
    consumer = Consumer(
        settings,
        kinds=(JobKind.run,),
        handlers={},
        partition_handler=None,
        concurrency=1,
        ctx=handler_ctx,
    )
    task = asyncio.create_task(consumer.run())
    await asyncio.sleep(0.5)
    consumer.stop()
    await task
    internal = await jobs.load_job_internal(job.id)
    assert internal is not None and internal.state == JobState.queued
    assert internal.attempts == 0 and internal.locked_by is None


async def test_a_partition_reclaimed_by_the_same_worker_cancels_the_zombie_first(
    migrated_db, handler_ctx, queue_settings
) -> None:  # noqa: ANN001
    """A partition whose lease expired and that this same worker claims again must not run twice
    at once: the zombie task is canceled and has ended before the new holder starts."""
    settings = queue_settings(job_poll_interval_seconds=0.1, job_drain_timeout_seconds=0.2)
    events: list[str] = []
    first_started = asyncio.Event()
    second_started = asyncio.Event()

    async def on_prepare(ctx, job):  # noqa: ANN001, ANN202
        await jobs.add_partitions(ctx.worker_id, job.id, [KeyRange()])

    async def on_part(ctx, job, part):  # noqa: ANN001, ANN202
        call = "first" if not first_started.is_set() else "second"
        events.append(f"{call}:start")
        if call == "first":
            first_started.set()
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                events.append("first:canceled")
                raise
        second_started.set()
        await jobs.finish_partition(ctx.worker_id, part.id, PartitionState.done)

    run_job = await jobs.create_job(
        tenant_id="t1",
        kind=JobKind.run,
        phase=JobPhase.prepare,
        spec={"source": {"parallelism": 1}},
    )
    consumer = Consumer(
        settings,
        kinds=(JobKind.run,),
        handlers={JobPhase.prepare: on_prepare},
        partition_handler=on_part,
        concurrency=4,
        ctx=handler_ctx,
    )
    task = asyncio.create_task(consumer.run())
    await asyncio.wait_for(first_started.wait(), 5)
    async with db.acquire_queue(None) as conn:  # what the reaper does after a lease expiry
        await conn.execute(
            f"UPDATE {db.schema_ident()}.dps_job_partition SET state = 'queued', "
            "locked_by = NULL, lease_expires_at = NULL, run_after = now() WHERE job_id = $1",
            run_job.id,
        )
        await conn.execute("SELECT pg_notify($1, 'partition')", jobs.NOTIFY_CHANNEL)
    await asyncio.wait_for(second_started.wait(), 5)
    consumer.stop()
    await task
    assert events == ["first:start", "first:canceled", "second:start"]
