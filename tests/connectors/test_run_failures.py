"""Run jobs that must fail or stop cleanly: row-count mismatch, preflight leakage, cancel."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from dps import jobs
from dps.auth import LOCAL_PRINCIPAL
from dps.connections import Connection
from dps.engine.policy import MethodSpec
from dps.jobs import JobState, PartitionState
from dps.jobs_models import ClassificationSnapshot, ObjectRef, SnapshotOverride, SpecError
from dps.layers.connectors.evidence import get_evidence
from dps.layers.connectors.jobs_run import RUNTIMES, prepare
from dps.layers.connectors.submit import submit_run
from dps.worker import HandlerContext
from tests.connectors.helpers import db_key_enc, discover_and_accept, drive
from tests.connectors.test_run_modes import IN_PLACE_REVIEW, run_spec
from tests.fixtures.sources import SourcePg

pytestmark = [pytest.mark.db, pytest.mark.integration, pytest.mark.pg_source]
RegisterSource = Callable[..., Awaitable[Connection]]


async def _setup(
    handler_ctx: HandlerContext,
    source_pg: SourcePg,
    register_source: RegisterSource,
    name: str,
    **review: Any,
) -> tuple[ObjectRef, Connection, Connection, ClassificationSnapshot]:
    obj = await source_pg.customers_table(rows=300, name=name)
    src = await register_source("t1", name=f"src-{name}")
    uat = await register_source(
        "t1", ref="dps/src/test-pg-rw", roles={"target"}, name=f"uat-{name}"
    )
    snap = await discover_and_accept(handler_ctx, "t1", src.id, obj, **review)
    return obj, src, uat, snap


async def test_row_count_mismatch_fails_before_any_write(
    handler_ctx: HandlerContext, source_pg: SourcePg, register_source: RegisterSource
) -> None:
    obj = await source_pg.customers_table(rows=300, name="mismatch")
    rw = await register_source(
        "t1", ref="dps/src/test-pg-rw", roles={"source", "target"}, name="mm-rw"
    )
    snap = await discover_and_accept(handler_ctx, "t1", rw.id, obj, overrides=IN_PLACE_REVIEW)
    before = await source_pg.admin.fetch(f'SELECT * FROM "{source_pg.schema}".mismatch ORDER BY 1')
    target = {"write_mode": "in_place", "confirm_write": True, "expected_rows": 10}
    sub = await submit_run(
        run_spec(rw.id, obj, snap.id, target),
        tenant_id="t1",
        principal=LOCAL_PRINCIPAL,
        settings=handler_ctx.settings,
        kek=handler_ctx.kek,
    )
    job = await drive(handler_ctx, "t1", sub.job.id)
    assert (job.state, job.error_code) == (JobState.failed, "row_count_mismatch")
    assert job.error is not None and "300" in job.error and "10" in job.error  # counts only
    assert job.progress.evidence_id is not None
    ev = await get_evidence("t1", job.progress.evidence_id)
    assert ev is not None and ev.pack.outcome == "failed"
    assert ev.pack.errors[0] == {"code": "row_count_mismatch", "where": "prepare"}
    after = await source_pg.admin.fetch(f'SELECT * FROM "{source_pg.schema}".mismatch ORDER BY 1')
    assert after == before
    ledger = f"SELECT to_regclass('\"{source_pg.schema}\".dps_write_ledger')"
    assert await source_pg.admin.fetchval(ledger) is None
    assert await db_key_enc(job.id) is None


async def test_in_place_refuses_a_transformed_key_at_submit(
    handler_ctx: HandlerContext, source_pg: SourcePg, register_source: RegisterSource
) -> None:
    obj = await source_pg.customers_table(rows=50, name="keyed")
    rw = await register_source(
        "t1", ref="dps/src/test-pg-rw", roles={"source", "target"}, name="keyed-rw"
    )
    snap = await discover_and_accept(handler_ctx, "t1", rw.id, obj)  # customer_id → fpe_digits
    with pytest.raises(SpecError) as err:
        await submit_run(
            run_spec(rw.id, obj, snap.id, {"write_mode": "in_place", "confirm_write": True}),
            tenant_id="t1",
            principal=LOCAL_PRINCIPAL,
            settings=handler_ctx.settings,
            kek=handler_ctx.kek,
        )
    assert (err.value.code, err.value.status) == ("pk_transform_in_place", 422)


async def test_preflight_leakage_fails_closed(
    handler_ctx: HandlerContext, source_pg: SourcePg, register_source: RegisterSource
) -> None:
    obj = await source_pg.customers_table(rows=300, name="leaky")
    src = await register_source("t1", name="src-leaky")
    uat = await register_source("t1", ref="dps/src/test-pg-rw", roles={"target"}, name="uat-leaky")
    names = [
        r["full_name"]
        for r in await source_pg.admin.fetch(f'SELECT full_name FROM "{source_pg.schema}".leaky')
    ]
    # a deliberately broken review: every name becomes the first customer's real name
    broken = [
        SnapshotOverride(
            path="$.full_name", method=MethodSpec(method="dictionary", params={"values": names[:1]})
        )
    ]
    snap = await discover_and_accept(handler_ctx, "t1", src.id, obj, overrides=broken)
    target = {
        "write_mode": "table",
        "connection_id": uat.id,
        "object": {"owner": source_pg.schema, "name": "leaky_out"},
    }
    sub = await submit_run(
        run_spec(src.id, obj, snap.id, target),
        tenant_id="t1",
        principal=LOCAL_PRINCIPAL,
        settings=handler_ctx.settings,
        kek=handler_ctx.kek,
    )
    job = await drive(handler_ctx, "t1", sub.job.id)
    assert (job.state, job.error_code) == (JobState.failed, "leakage")
    assert names[0] not in (job.error or "")
    exists = f"SELECT to_regclass('\"{source_pg.schema}\".leaky_out')"
    assert await source_pg.admin.fetchval(exists) is None
    assert job.progress.evidence_id is not None
    ev = await get_evidence("t1", job.progress.evidence_id)
    assert ev is not None and not ev.pack.verification["passed"]
    assert names[0] not in ev.model_dump_json()  # the review's literal never reaches evidence


async def test_cancel_mid_run_ends_canceled_with_evidence(
    handler_ctx: HandlerContext, source_pg: SourcePg, register_source: RegisterSource
) -> None:
    obj, src, uat, snap = await _setup(handler_ctx, source_pg, register_source, "cancel")
    target = {
        "write_mode": "table",
        "connection_id": uat.id,
        "object": {"owner": source_pg.schema, "name": "cancel_out"},
    }
    sub = await submit_run(
        run_spec(src.id, obj, snap.id, target),
        tenant_id="t1",
        principal=LOCAL_PRINCIPAL,
        settings=handler_ctx.settings,
        kek=handler_ctx.kek,
    )
    [c] = await jobs.claim_jobs(handler_ctx.worker_id, kinds=(jobs.JobKind.run,), batch=1)
    await prepare(handler_ctx, c)
    await jobs.cancel("t1", sub.job.id)
    job = await drive(handler_ctx, "t1", sub.job.id)
    assert job.state == JobState.canceled and job.progress.outcome == "canceled"
    assert {p.state for p in await jobs.list_partitions("t1", job.id)} == {PartitionState.canceled}
    assert job.progress.evidence_id is not None
    ev = await get_evidence("t1", job.progress.evidence_id)
    assert ev is not None and ev.pack.outcome == "canceled"
    assert await db_key_enc(job.id) is None and sub.job.id not in RUNTIMES.job_ids()


async def test_same_table_append_refuses_an_offset_inside_the_key_span(
    handler_ctx: HandlerContext, source_pg: SourcePg, register_source: RegisterSource
) -> None:
    """A shifted key that lands in a key gap below the table's largest key would sit inside a
    partition range and be read (and appended) again: prepare refuses such an offset."""
    obj = await source_pg.customers_table(rows=40, name="gapped")
    table = f'"{source_pg.schema}".gapped'
    await source_pg.admin.execute(  # one row far above the others leaves a gap of ~1000 keys
        f"INSERT INTO {table} SELECT (jsonb_populate_record(NULL::{table}, "
        f"to_jsonb(c) || jsonb_build_object('customer_id', 10001000))).* FROM {table} c "
        "WHERE customer_id = (SELECT min(customer_id) FROM " + table + ")"
    )
    keys = "SELECT customer_id FROM " + table + " ORDER BY 1"
    before = [r["customer_id"] for r in await source_pg.admin.fetch(keys)]
    assert len(before) == 41 and before[-1] == 10001000 and before[-2] < 10000100
    rw = await register_source(
        "t1", ref="dps/src/test-pg-rw", roles={"source", "target"}, name="gapped-rw"
    )
    # offset mechanics: an obfuscate append (a synthesize run never shifts a real key, D-A B2)
    snap = await discover_and_accept(handler_ctx, "t1", rw.id, obj, overrides=IN_PLACE_REVIEW)

    async def append(offset: int) -> jobs.Job:
        spec = run_spec(
            rw.id,
            obj,
            snap.id,
            {
                "write_mode": "append",
                "confirm_write": True,
                "pk_strategy": {"kind": "offset", "offset": offset},
            },
        )
        spec = spec.model_copy(update={"source": spec.source.model_copy(update={"batch_rows": 10})})
        sub = await submit_run(
            spec,
            tenant_id="t1",
            principal=LOCAL_PRINCIPAL,
            settings=handler_ctx.settings,
            kek=handler_ctx.kek,
        )
        return await drive(handler_ctx, "t1", sub.job.id)

    job = await append(100)  # 10000001 + 100 < 10001001: new keys would land in unread ranges
    assert (job.state, job.error_code) == (JobState.failed, "pk_strategy_invalid")
    assert [r["customer_id"] for r in await source_pg.admin.fetch(keys)] == before
    job = await append(1_000_000)  # every new key sorts above every existing key
    assert job.state == JobState.succeeded, job.error_code
    assert job.progress.rows_written == 41
    after = [r["customer_id"] for r in await source_pg.admin.fetch(keys)]
    assert after == before + [k + 1_000_000 for k in before]  # each row appended exactly once
