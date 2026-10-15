"""A canceled run counts every batch its target holds (S1-M4 regression r1 B1).

``jobs.cancel`` flips every running partition to ``canceled`` at once. A holder that had already
committed a batch (and, for a ledgered plan, its ledger mark) when the cancel landed found its
checkpoint fenced out (``state = 'running'``) and abandoned the partition: the batch reached the
target but never the partition's ``rows_written`` / ``batch_no``, the job's progress, the evidence
pack, Reports or the tenant's daily usage — and, for a ledgered plan, the cancel finalize then
cleared the ledger mark that recorded it. Live on main @ ``8ea220a`` the packs said 1 000 / 3 000 /
4 000 rows while the targets held 2 000 / 4 000 / 5 000.

Each test cancels the job from inside the target writer, right after it commits a batch, with two
partitions claimed (both running), and compares the pack with the target itself (counts only).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from dps import db, jobs
from dps.auth import LOCAL_PRINCIPAL
from dps.connections import Connection
from dps.jobs import JobState
from dps.layers.connectors import jobs_run
from dps.layers.connectors.evidence import get_evidence
from dps.layers.connectors.postgres import PostgresWriter
from dps.layers.connectors.submit import submit_run
from dps.worker import HandlerContext
from tests.connectors.helpers import discover_and_accept, drive
from tests.connectors.test_run_modes import IN_PLACE_REVIEW, run_spec
from tests.fixtures.sources import SourcePg

pytestmark = [pytest.mark.db, pytest.mark.integration, pytest.mark.pg_source]
RegisterSource = Callable[..., Awaitable[Connection]]


async def _usage(tenant: str) -> int:
    async with db.acquire(tenant) as conn:
        value = await conn.fetchval(
            f"SELECT rows_written FROM {db.schema_ident()}.dps_tenant_usage WHERE tenant_id = $1 "
            "AND day = (now() AT TIME ZONE 'UTC')::date",
            tenant,
        )
    return int(value or 0)


def _cancel_after_commit(
    monkeypatch: pytest.MonkeyPatch, job_ids: list[str], *, after: int = 1
) -> dict[str, int]:
    """Patch the PostgreSQL writer: once ``after`` batches are committed to the target, cancel
    the job (``job_ids[0]``) — the holder's next step is its checkpoint."""
    real = PostgresWriter.write_batch
    seen = {"batches": 0, "rows": 0}

    async def write_batch(self: PostgresWriter, rows: list[Any], *, mark: Any) -> int:
        written = await real(self, rows, mark=mark)
        seen["batches"] += 1
        seen["rows"] += written
        if seen["batches"] == after:
            await jobs.cancel("t1", job_ids[0])
        return written

    monkeypatch.setattr(PostgresWriter, "write_batch", write_batch)
    return seen


async def _pack_rows(job: jobs.Job) -> tuple[int, dict[str, Any]]:
    assert job.progress.evidence_id is not None
    ev = await get_evidence("t1", job.progress.evidence_id)
    assert ev is not None and ev.pack.outcome == "canceled"
    return int(ev.pack.counts["rows_written"]), ev.pack.verification


async def test_a_canceled_table_load_counts_the_batch_in_flight(
    handler_ctx: HandlerContext,
    source_pg: SourcePg,
    register_source: RegisterSource,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ``table`` load canceled right after a batch commits, with two partitions running: the
    job, its partitions, the evidence pack and the tenant's daily usage count every row the
    target holds."""
    obj = await source_pg.customers_table(rows=2000, name="cxl_table")
    src = await register_source("t1", name="cxl-src")
    uat = await register_source("t1", ref="dps/src/test-pg-rw", roles={"target"}, name="cxl-uat")
    snap = await discover_and_accept(handler_ctx, "t1", src.id, obj)
    target = {
        "write_mode": "table",
        "connection_id": uat.id,
        "object": {"owner": source_pg.schema, "name": "cxl_table_out"},
    }
    sub = await submit_run(
        run_spec(src.id, obj, snap.id, target),
        tenant_id="t1",
        principal=LOCAL_PRINCIPAL,
        settings=handler_ctx.settings,
        kek=handler_ctx.kek,
    )
    usage_before = await _usage("t1")
    seen = _cancel_after_commit(monkeypatch, [sub.job.id])
    job = await drive(handler_ctx, "t1", sub.job.id)
    assert job.state == JobState.canceled and job.progress.outcome == "canceled"
    in_target = await source_pg.admin.fetchval(
        f'SELECT count(*) FROM "{source_pg.schema}".cxl_table_out'
    )
    assert in_target == seen["rows"] and 0 < in_target < 2000
    parts = await jobs.list_partitions("t1", job.id)
    assert len(parts) == 2 and all(p.state == jobs.PartitionState.canceled for p in parts)
    assert sum(p.rows_written for p in parts) == in_target
    assert sum(p.batch_no for p in parts) == seen["batches"]
    assert job.progress.rows_written == in_target
    rows, verification = await _pack_rows(job)
    assert rows == in_target
    assert verification["batches"] == seen["batches"]
    assert await _usage("t1") - usage_before == in_target


async def test_a_canceled_in_place_run_counts_its_batch_and_clears_its_ledger(
    handler_ctx: HandlerContext,
    source_pg: SourcePg,
    register_source: RegisterSource,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ledgered plan (``in_place``) canceled right after its second batch commits: the pack
    counts every row changed in place, and the ledger is cleared only after its marks agree with
    the partitions."""
    obj = await source_pg.customers_table(rows=2000, name="cxl_inplace")
    table = f'"{source_pg.schema}".cxl_inplace'
    before = {
        r["customer_id"]: r["email"] for r in await source_pg.admin.fetch(f"SELECT * FROM {table}")
    }
    rw = await register_source(
        "t1", ref="dps/src/test-pg-rw", roles={"source", "target"}, name="cxl-inplace-rw"
    )
    snap = await discover_and_accept(handler_ctx, "t1", rw.id, obj, overrides=IN_PLACE_REVIEW)
    target = {"write_mode": "in_place", "confirm_write": True, "expected_rows": 2000}
    sub = await submit_run(
        run_spec(rw.id, obj, snap.id, target),
        tenant_id="t1",
        principal=LOCAL_PRINCIPAL,
        settings=handler_ctx.settings,
        kek=handler_ctx.kek,
    )
    usage_before = await _usage("t1")
    seen = _cancel_after_commit(monkeypatch, [sub.job.id], after=2)
    job = await drive(handler_ctx, "t1", sub.job.id)
    assert job.state == JobState.canceled
    after = {
        r["customer_id"]: r["email"] for r in await source_pg.admin.fetch(f"SELECT * FROM {table}")
    }
    changed = sum(1 for k, v in after.items() if v != before[k])
    assert changed == seen["rows"] and 0 < changed < 2000
    rows, verification = await _pack_rows(job)
    assert rows == changed and job.progress.rows_written == changed
    assert verification["write_ledger_cleared"] is True
    ledger = f'SELECT count(*) FROM "{source_pg.schema}".dps_write_ledger WHERE job_id = $1'
    assert await source_pg.admin.fetchval(ledger, job.id) == 0
    assert await _usage("t1") - usage_before == changed


async def test_a_canceled_ledgered_run_recovers_a_batch_its_dead_holder_never_checkpointed(
    handler_ctx: HandlerContext,
    source_pg: SourcePg,
    register_source: RegisterSource,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The holder commits a batch with its ledger mark, the job is canceled, and the holder dies
    before it checkpoints (its lease runs out): the cancel finalize records the batch from the
    ledger before it clears it — the ledger was the only record of it — and charges the usage."""
    obj = await source_pg.customers_table(rows=2000, name="cxl_dead")
    table = f'"{source_pg.schema}".cxl_dead'
    before = {
        r["customer_id"]: r["email"] for r in await source_pg.admin.fetch(f"SELECT * FROM {table}")
    }
    rw = await register_source(
        "t1", ref="dps/src/test-pg-rw", roles={"source", "target"}, name="cxl-dead-rw"
    )
    snap = await discover_and_accept(handler_ctx, "t1", rw.id, obj, overrides=IN_PLACE_REVIEW)
    target = {"write_mode": "in_place", "confirm_write": True, "expected_rows": 2000}
    sub = await submit_run(
        run_spec(rw.id, obj, snap.id, target),
        tenant_id="t1",
        principal=LOCAL_PRINCIPAL,
        settings=handler_ctx.settings,
        kek=handler_ctx.kek,
    )
    usage_before = await _usage("t1")
    seen = _cancel_after_commit(monkeypatch, [sub.job.id])
    real_checkpoint = jobs.checkpoint
    calls = {"n": 0}

    async def dying_checkpoint(worker_id: str, partition_id: int, **kw: Any) -> bool:
        calls["n"] += 1
        if calls["n"] == 1:  # the holder dies before this checkpoint; its lease runs out
            async with db.acquire_queue(None) as conn:
                await conn.execute(
                    f"UPDATE {db.schema_ident()}.dps_job_partition SET lease_expires_at = "
                    "now() - interval '1 second' WHERE id = $1",
                    partition_id,
                )
            raise jobs_run._Lost()
        return await real_checkpoint(worker_id, partition_id, **kw)

    monkeypatch.setattr(jobs, "checkpoint", dying_checkpoint)
    job = await drive(handler_ctx, "t1", sub.job.id)
    assert job.state == JobState.canceled
    after = {
        r["customer_id"]: r["email"] for r in await source_pg.admin.fetch(f"SELECT * FROM {table}")
    }
    changed = sum(1 for k, v in after.items() if v != before[k])
    assert changed == seen["rows"] and changed > 0
    rows, verification = await _pack_rows(job)
    assert rows == changed and job.progress.rows_written == changed
    assert verification["write_ledger_cleared"] is True
    ledger = f'SELECT count(*) FROM "{source_pg.schema}".dps_write_ledger WHERE job_id = $1'
    assert await source_pg.admin.fetchval(ledger, job.id) == 0
    assert await _usage("t1") - usage_before == changed
