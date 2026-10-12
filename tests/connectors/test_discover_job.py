"""The discover_job handler against the metadata DB and the lane's source-pg."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from dps import db, jobs, snapshots
from dps.connections import Connection
from dps.ids import PREFIX_JOB, new_id
from dps.jobs import ClaimedJob, Job, JobKind, JobPhase, JobState
from dps.jobs_models import DiscoverSpec, ObjectRef, SealedSelection, Selection, redact_spec
from dps.layers.connectors.discovery import discover_job
from dps.layers.connectors.selection import parameterize_literals
from dps.secrets import seal_json
from dps.worker import HandlerContext
from tests.fixtures.sources import SourcePg

pytestmark = [pytest.mark.db, pytest.mark.integration, pytest.mark.pg_source]

RegisterSource = Callable[..., Awaitable[Connection]]


async def _submit(
    kek: bytes, conn: Connection, objects: list[ObjectRef], sel: Selection, **spec_kw: Any
) -> Job:
    spec = DiscoverSpec(connection_id=conn.id, objects=objects, selection=sel, **spec_kw)
    job_id = new_id(PREFIX_JOB)
    sealed = (
        seal_json(
            kek, SealedSelection(where=sel.where, params=sel.params), aad=f"dps_job.spec:{job_id}"
        )
        if sel.where or sel.query
        else None
    )
    return await jobs.create_job(
        tenant_id=conn.tenant_id,
        kind=JobKind.discover,
        phase=JobPhase.discover,
        job_id=job_id,
        spec=redact_spec(spec, engine="postgres"),
        connection_id=conn.id,
        object_ref=objects[0].fqn(),
        sealed_selection=sealed,
    )


async def _claim(ctx: HandlerContext) -> ClaimedJob:
    [claimed] = await jobs.claim_jobs(ctx.worker_id, kinds=(JobKind.discover,), batch=1)
    return claimed


async def test_discover_job_writes_a_value_free_proposed_snapshot(
    migrated_db: Any,
    handler_ctx: HandlerContext,
    source_pg: SourcePg,
    register_source: RegisterSource,
    kek: bytes,
) -> None:
    obj = await source_pg.customers_table(rows=300)
    conn = await register_source("t1")
    sel = parameterize_literals(Selection(where="country_code = 'IN'"), "postgres")
    spec = DiscoverSpec(connection_id=conn.id, objects=[obj], selection=sel, sample_rows=200)
    job_id = "j_" + "0" * 26
    job = await jobs.create_job(
        tenant_id="t1",
        kind=JobKind.discover,
        phase=JobPhase.discover,
        job_id=job_id,
        spec=redact_spec(spec, engine="postgres"),
        connection_id=conn.id,
        object_ref=obj.fqn(),
        sealed_selection=seal_json(
            kek, SealedSelection(where=sel.where, params=sel.params), aad=f"dps_job.spec:{job_id}"
        ),
    )
    [claimed] = await jobs.claim_jobs(handler_ctx.worker_id, kinds=(JobKind.discover,), batch=1)
    await discover_job(handler_ctx, claimed)
    done = await jobs.get_job("t1", job.id)
    assert done is not None and done.state == JobState.succeeded
    assert len(done.progress.snapshot_ids) == 1 and done.progress.outcome == "succeeded"
    snap = await snapshots.get_snapshot("t1", done.progress.snapshot_ids[0])
    assert snap is not None and snap.status == "proposed" and snap.sample_rows > 0
    assert snap.object_ref == obj.fqn() and snap.job_id == job.id and snap.connection_id == conn.id
    names = {col.name: col for col in snap.columns}
    assert names["email"].entity_type == "EMAIL" and names["full_name"].entity_type == "PERSON_NAME"
    assert names["customer_id"].anchor and names["notes"].text_column
    stored = json.dumps(snap.model_dump(mode="json"))
    sample = await source_pg.admin.fetch(
        f'SELECT full_name, email, mobile FROM "{source_pg.schema}".customers LIMIT 20'
    )
    assert not any(v in stored for r in sample for v in r.values() if v)
    assert "IN" not in json.dumps([c.shapes for c in snap.columns])
    async with db.acquire("t1") as c:
        spec_text = await c.fetchval("SELECT spec::text FROM dps.dps_job WHERE id = $1", job.id)
    assert "'IN'" not in spec_text and ":l1" in spec_text


async def test_several_objects_make_one_snapshot_each(
    migrated_db: Any,
    handler_ctx: HandlerContext,
    source_pg: SourcePg,
    register_source: RegisterSource,
    kek: bytes,
) -> None:
    first = await source_pg.customers_table(rows=60)
    second = await source_pg.table(
        "contacts",
        "contact_id bigint PRIMARY KEY, email varchar(254) NOT NULL",
        [(i, f"person{i}@example-mail.in") for i in range(80)],
    )
    conn = await register_source("t1")
    job = await _submit(kek, conn, [first, second], Selection(), sample_rows=50)
    await discover_job(handler_ctx, await _claim(handler_ctx))
    done = await jobs.get_job("t1", job.id)
    assert done is not None and done.state == JobState.succeeded
    snaps = [await snapshots.get_snapshot("t1", s) for s in done.progress.snapshot_ids]
    assert [s.object_ref for s in snaps if s] == [first.fqn(), second.fqn()]


async def test_failures_are_coded_and_value_free(
    migrated_db: Any,
    handler_ctx: HandlerContext,
    source_pg: SourcePg,
    register_source: RegisterSource,
    kek: bytes,
) -> None:
    obj = await source_pg.customers_table(rows=10)
    conn = await register_source("t1")
    # an unknown column in the selection: the selection rule's code, not a retry
    bad = parameterize_literals(Selection(where="no_such_col = 'secret-9876'"), "postgres")
    job = await _submit(kek, conn, [obj], bad)
    await discover_job(handler_ctx, await _claim(handler_ctx))
    failed = await jobs.get_job("t1", job.id)
    assert failed is not None and failed.state == JobState.failed
    assert failed.error_code == "selection_unknown_column" and "9876" not in (failed.error or "")
    # a missing table
    gone = await _submit(kek, conn, [obj.model_copy(update={"name": "missing"})], Selection())
    await discover_job(handler_ctx, await _claim(handler_ctx))
    missing = await jobs.get_job("t1", gone.id)
    assert missing is not None and (missing.state, missing.error_code) == (
        JobState.failed,
        "object_not_found",
    )
    # a secret_ref that does not resolve
    nosecret = await register_source("t1", ref="dps/src/not-mounted")
    unsecret = await _submit(kek, nosecret, [obj], Selection())
    await discover_job(handler_ctx, await _claim(handler_ctx))
    s = await jobs.get_job("t1", unsecret.id)
    assert s is not None and (s.state, s.error_code) == (JobState.failed, "secret_unavailable")
    # a target-only connection is not a source
    target_only = await register_source("t1", roles={"target"})
    wrong = await _submit(kek, target_only, [obj], Selection())
    await discover_job(handler_ctx, await _claim(handler_ctx))
    w = await jobs.get_job("t1", wrong.id)
    assert w is not None and (w.state, w.error_code) == (JobState.failed, "connection_role")


async def test_an_unreachable_source_is_retried(
    migrated_db: Any,
    handler_ctx: HandlerContext,
    source_pg: SourcePg,
    register_source: RegisterSource,
    kek: bytes,
) -> None:
    obj = await source_pg.customers_table(rows=10)
    conn = await register_source("t1", port=1)  # nothing listens there
    job = await _submit(kek, conn, [obj], Selection())
    await discover_job(handler_ctx, await _claim(handler_ctx))
    retried = await jobs.get_job("t1", job.id)
    assert retried is not None and retried.state == JobState.queued
    assert retried.error_code == "connection_failed"
