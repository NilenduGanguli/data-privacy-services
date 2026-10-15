"""Retention, extended by M5 (Task 13): one module, one scheduler, one system tenant — the M5 tables
age out, roll-ups are refreshed, counts land in ``progress.metrics``, admins can run it on demand."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import dps.retention
from dps import db, jobs
from dps.jobs import JobKind, JobPhase, JobState
from dps.retention import RetentionCounts, enqueue_retention, retention_job, run_retention
from dps.storage import ArtefactService

pytestmark = pytest.mark.db


@pytest.fixture
def service(object_store, migrated_db) -> ArtefactService:  # noqa: ANN001
    return ArtefactService(object_store, acquire=db.acquire_queue)


async def _expired(
    tenant: str, *, kind: JobKind = JobKind.run, phase: JobPhase = JobPhase.prepare
) -> str:
    job = await jobs.create_job(
        tenant_id=tenant, kind=kind, phase=phase, spec={}, sealed_secret=b"\x01k"
    )
    async with db.acquire_queue(tenant) as conn:
        await conn.execute(
            f"UPDATE {db.schema_ident()}.dps_job SET state = 'succeeded', "
            "finished_at = now() - interval '40 days', expires_at = now() - interval '1 day' "
            "WHERE id = $1",
            job.id,
        )
    return job.id


async def _count(tenant: str | None, sql: str, *args: object) -> int:
    async with db.acquire_queue(tenant) as conn:
        return int(await conn.fetchval(sql.replace("__S__", db.schema_ident()), *args))


async def test_dry_run_counts_without_deleting(migrated_db, service) -> None:  # noqa: ANN001
    job_id = await _expired("t1")
    await service.save(
        tenant_id="t1",
        job_id=job_id,
        kind="evidence_json",
        data=b"{}",
        content_type="application/json",
    )
    counts = await run_retention(settings=migrated_db, artefacts=service, dry_run=True)
    assert isinstance(counts, RetentionCounts) and counts.jobs == 1 and counts.objects == 1
    assert counts.tenants >= 1
    assert await jobs.get_job("t1", job_id) is not None
    assert await _count("t1", "SELECT count(*) FROM __S__.dps_artefact") == 1


async def test_expired_jobs_their_objects_and_scan_rows_are_deleted(
    migrated_db, service, object_store
) -> None:  # noqa: ANN001
    s = db.schema_ident()
    scan = await _expired("t1", kind=JobKind.scan, phase=JobPhase.scan)
    run = await _expired("t1")
    keep = await jobs.create_job(tenant_id="t1", kind=JobKind.run, phase=JobPhase.prepare, spec={})
    art = await service.save(
        tenant_id="t1", job_id=scan, kind="screenshot", data=b"png", content_type="image/png"
    )
    row = await service.get_row("t1", art)
    assert row is not None and await object_store.exists(row.object_key)
    async with db.acquire_queue("t1") as conn:
        await conn.execute(
            f"INSERT INTO {s}.dps_evidence (id, tenant_id, job_id, pack, pack_sha256) "
            "VALUES ('ev_s', 't1', $1, '{}', 'h')",
            scan,
        )
        for no in range(2):
            await conn.execute(
                f"INSERT INTO {s}.dps_job_partition (job_id, tenant_id, partition_no) VALUES ($1, 't1', $2)",
                run,
                no,
            )
        scan_tables = (
            await conn.fetchval("SELECT to_regclass($1)", f"{s}.dps_scan_page") is not None
        )
        if scan_tables:  # M4's 030 (after the lanes merge): rows cascade from dps_job
            await conn.execute(  # M4's 030 as built: template NOT NULL (contract §2.4 "may add")
                f"INSERT INTO {s}.dps_scan_page (tenant_id, job_id, page_no, url_redacted, depth, "
                "status, template) VALUES ('t1', $1, 1, '/', 0, 'done', 'demo-site/')",
                scan,
            )
            await conn.execute(
                f"INSERT INTO {s}.dps_scan_request (tenant_id, job_id, method, decision, reason, host) "
                "VALUES ('t1', $1, 'POST', 'blocked', 'submit', 'demo-site')",
                scan,
            )
    counts = await run_retention(settings=migrated_db, artefacts=service, dry_run=False)
    assert counts.jobs == 2 and counts.objects == 1
    assert await jobs.get_job("t1", scan) is None and await jobs.get_job("t1", run) is None
    assert await jobs.get_job("t1", keep.id) is not None
    assert not await object_store.exists(row.object_key)
    for table in ("dps_artefact", "dps_evidence", "dps_job_partition"):
        assert await _count("t1", f"SELECT count(*) FROM __S__.{table}") == 0, table
    if scan_tables:
        for table in ("dps_scan_page", "dps_scan_request"):
            assert await _count("t1", f"SELECT count(*) FROM __S__.{table}") == 0, table
    assert "dps_scan" not in Path(dps.retention.__file__).read_text(encoding="utf-8")


async def test_snapshots_proposed_and_superseded_age_out_accepted_stay(
    migrated_db, service
) -> None:  # noqa: ANN001
    async with db.acquire_queue("t1") as conn:
        for sid, status in (("sn_p", "proposed"), ("sn_s", "superseded"), ("sn_a", "accepted")):
            await conn.execute(
                f"INSERT INTO {db.schema_ident()}.dps_snapshot (id, tenant_id, connection_id, engine, "
                "object_ref, policy_name, policy_hash, shape, columns, sample_rows, content_hash, "
                "status, created_at) VALUES ($1, 't1', 'c', 'postgres', $2, 'p', 'h', '{}', '[]', 0, "
                "'h', $3, now() - interval '40 days')",
                sid,
                f"a.{sid}",
                status,
            )
        await conn.execute(f"INSERT INTO {db.schema_ident()}.dps_tenant (tenant_id) VALUES ('t1')")
    counts = await run_retention(settings=migrated_db, artefacts=service, dry_run=False)
    assert counts.snapshots == 2
    async with db.acquire_queue("t1") as conn:
        left = [
            r["id"] for r in await conn.fetch(f"SELECT id FROM {db.schema_ident()}.dps_snapshot")
        ]
    assert left == ["sn_a"]


async def test_inline_secrets_are_purged_after_expiry(migrated_db, service) -> None:  # noqa: ANN001
    async with db.acquire_queue("t1") as conn:
        for cid, when in (
            ("c_old", "now() - interval '1 hour'"),
            ("c_new", "now() + interval '1 hour'"),
        ):
            await conn.execute(
                f"INSERT INTO {db.schema_ident()}.dps_connection (id, tenant_id, name, engine, params, "
                f"secret_enc, secret_expires_at) VALUES ($1, 't1', $1, 'postgres', '{{}}', "
                f"'\\x01'::bytea, {when})",
                cid,
            )
    counts = await run_retention(settings=migrated_db, artefacts=service, dry_run=False)
    assert counts.secrets_purged == 1
    sql = "SELECT secret_enc IS NULL FROM __S__.dps_connection WHERE id = $1"
    assert await _count("t1", sql, "c_old") == 1 and await _count("t1", sql, "c_new") == 0


async def test_terminal_jobs_lose_key_enc(migrated_db, service) -> None:  # noqa: ANN001
    done = await jobs.create_job(
        tenant_id="t1", kind=JobKind.run, phase=JobPhase.prepare, spec={}, sealed_secret=b"\x01k"
    )
    live = await jobs.create_job(
        tenant_id="t1", kind=JobKind.run, phase=JobPhase.prepare, spec={}, sealed_secret=b"\x01k"
    )
    async with db.acquire_queue("t1") as conn:
        await conn.execute(
            f"UPDATE {db.schema_ident()}.dps_job SET state = 'succeeded' WHERE id = $1", done.id
        )
        await conn.execute(
            f"UPDATE {db.schema_ident()}.dps_job SET state = 'running' WHERE id = $1", live.id
        )
    counts = await run_retention(settings=migrated_db, artefacts=service, dry_run=False)
    assert counts.keys_purged == 1
    a, b = await jobs.load_job_internal(done.id), await jobs.load_job_internal(live.id)
    assert a is not None and a.key_enc is None and b is not None and b.key_enc is not None


async def test_audit_log_is_never_touched(migrated_db, service) -> None:  # noqa: ANN001
    await _expired("t1")
    s = db.schema_ident()
    now = datetime.now(UTC)
    owner = await db.open_migration_connection(migrated_db)
    made: list[str] = []
    try:
        for days in (35, 300):  # partitions for months older than every retention window
            month = (now - timedelta(days=days)).date().replace(day=1)
            nxt = (month + timedelta(days=32)).replace(day=1)
            name = f"dps_audit_log_{month:%Y_%m}"
            if await owner.fetchval("SELECT to_regclass($1)", f"{s}.{name}") is None:
                await owner.execute(
                    f'CREATE TABLE {s}."{name}" PARTITION OF {s}.dps_audit_log '
                    f"FOR VALUES FROM ('{month.isoformat()}') TO ('{nxt.isoformat()}')"
                )
                made.append(name)
        async with db.acquire_queue("t1") as conn:
            for age in ("1 minute", "35 days", "300 days"):
                await conn.execute(
                    f"INSERT INTO {s}.dps_audit_log (tenant_id, route, action, outcome, ts) "
                    f"VALUES ('t1', '/x', 'detect', 'ok', now() - interval '{age}')"
                )
        before = await _count("t1", "SELECT count(*) FROM __S__.dps_audit_log")
        counts = await run_retention(settings=migrated_db, artefacts=service, dry_run=False)
        assert counts.jobs == 1
        assert await _count("t1", "SELECT count(*) FROM __S__.dps_audit_log") == before == 3
    finally:
        for name in made:
            await owner.execute(f'DROP TABLE {s}."{name}"')
        await owner.close()


async def test_rollup_and_usage_rows_age_out(migrated_db, service) -> None:  # noqa: ANN001
    s = db.schema_ident()
    async with db.acquire_queue("t1") as conn:
        await conn.execute(f"INSERT INTO {s}.dps_tenant (tenant_id) VALUES ('t1')")
        for age in (401, 1):
            day = f"(now() AT TIME ZONE 'UTC')::date - {age}"
            await conn.execute(
                f"INSERT INTO {s}.dps_report_daily (tenant_id, day, layer, action, outcome) "
                f"VALUES ('t1', {day}, 'api', 'detect', 'ok')"
            )
            await conn.execute(
                f"INSERT INTO {s}.dps_report_entity_daily (tenant_id, day, layer, entity_type) "
                f"VALUES ('t1', {day}, 'api', 'EMAIL')"
            )
            await conn.execute(
                f"INSERT INTO {s}.dps_report_refresh (tenant_id, day) VALUES ('t1', {day})"
            )
            await conn.execute(
                f"INSERT INTO {s}.dps_tenant_usage (tenant_id, day) VALUES ('t1', {day})"
            )
    dry = await run_retention(settings=migrated_db, artefacts=service, dry_run=True)
    assert dry.rollup_rows == 3 and dry.usage_rows == 1
    counts = await run_retention(settings=migrated_db, artefacts=service, dry_run=False)
    assert counts.rollup_rows == 3 and counts.usage_rows == 1
    for table in (
        "dps_report_daily",
        "dps_report_entity_daily",
        "dps_report_refresh",
        "dps_tenant_usage",
    ):
        assert await _count("t1", f"SELECT count(*) FROM __S__.{table}") == 1, table


async def test_system_tenant_retention_jobs_expire_like_any_other(migrated_db, service) -> None:  # noqa: ANN001
    old = await jobs.create_job(
        tenant_id=jobs.SYSTEM_TENANT, kind=JobKind.retention, phase=JobPhase.retention, spec={}
    )
    fresh = await jobs.create_job(
        tenant_id=jobs.SYSTEM_TENANT, kind=JobKind.retention, phase=JobPhase.retention, spec={}
    )
    async with db.acquire_queue(jobs.SYSTEM_TENANT) as conn:
        await conn.execute(
            f"UPDATE {db.schema_ident()}.dps_job SET state = 'succeeded', expires_at = now() - interval '1 day' "
            "WHERE id = $1",
            old.id,
        )
        await conn.execute(
            f"UPDATE {db.schema_ident()}.dps_job SET state = 'succeeded', expires_at = now() + interval '9 days' "
            "WHERE id = $1",
            fresh.id,
        )
    await run_retention(settings=migrated_db, artefacts=service, dry_run=False)
    assert await jobs.get_job(jobs.SYSTEM_TENANT, old.id) is None
    assert await jobs.get_job(jobs.SYSTEM_TENANT, fresh.id) is not None


async def test_one_scheduler_one_job_per_interval(migrated_db) -> None:  # noqa: ANN001
    results = await asyncio.gather(*(jobs.ensure_retention_job(6) for _ in range(4)))
    assert sorted(results) == [False, False, False, True]
    n = await _count(
        jobs.SYSTEM_TENANT,
        "SELECT count(*) FROM __S__.dps_job WHERE kind = 'retention' AND tenant_id = $1",
        jobs.SYSTEM_TENANT,
    )
    assert n == 1 and not hasattr(dps.retention, "RetentionScheduler")


async def test_handler_keeps_m2_counters_adds_metrics_and_refreshes_rollups(
    migrated_db, handler_ctx
) -> None:  # noqa: ANN001
    await jobs.create_job(tenant_id="t9", kind=JobKind.run, phase=JobPhase.prepare, spec={})
    await jobs.ensure_retention_job(6)
    [claimed] = await jobs.claim_jobs(handler_ctx.worker_id, kinds=(JobKind.retention,), batch=1)
    await retention_job(handler_ctx, claimed)
    done = await jobs.load_job_internal(claimed.id)
    assert done is not None and done.state == JobState.succeeded
    assert set(done.progress.values_by_method) >= {
        "retention:jobs",
        "retention:artefacts",
        "retention:snapshots",
        "retention:secrets",
        "retention:keys",
    }
    metrics = done.progress.metrics["retention"]
    assert metrics["tenants"] >= 1 and set(metrics) == set(RetentionCounts.model_fields)
    sql = "SELECT count(*) FROM __S__.dps_report_refresh WHERE tenant_id = 't9' AND day = (now() AT TIME ZONE 'UTC')::date"
    assert await _count("t9", sql) == 1
    events = [e for e in handler_ctx.audit.events if e.action == "retention.run"]
    assert len(events) == 1 and events[0].tenant_id == jobs.SYSTEM_TENANT
    assert events[0].counts["jobs"] == 0 and events[0].outcome == "ok"


async def test_enqueue_retention_is_owned_by_the_system_tenant(migrated_db) -> None:  # noqa: ANN001
    job = await enqueue_retention(dry_run=True, created_by="key_1")
    assert job.kind == JobKind.retention and job.tenant_id == jobs.SYSTEM_TENANT
    assert job.spec == {"dry_run": True} and job.created_by == "key_1"


def test_admin_route_enqueues_a_retention_job(api_client) -> None:  # noqa: ANN001
    client, sink = api_client
    full = {"X-API-KEY": "dps_full"}
    r = client.post("/api/v1/admin/retention/run", json={"dry_run": True}, headers=full)
    assert r.status_code == 202, r.text
    job = r.json()["job"]
    assert (
        job["kind"] == "retention"
        and job["tenant_id"] == "_system"
        and job["spec"] == {"dry_run": True}
    )
    assert any(e.action == "admin.retention.run" for e in sink.events)
    jobs_key = {"X-API-KEY": "dps_jobs", "X-DPS-Tenant": "t1"}
    assert client.post("/api/v1/admin/retention/run", json={}, headers=jobs_key).status_code == 403
