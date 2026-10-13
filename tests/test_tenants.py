"""Tenants (M5 Task 10): effective settings over defaults, validated ids and hosts, the tenant
directory that crosses tenants without opening RLS, retention re-stamping, caller-filtered lists."""

from __future__ import annotations

from datetime import timedelta

import pytest
from pydantic import ValidationError

from dps import db, jobs
from dps.auth import Principal
from dps.jobs import JobKind, JobPhase
from dps.tenants import (
    TenantError,
    TenantPatch,
    TenantSpec,
    create_tenant,
    list_tenants,
    tenant_directory,
    update_tenant,
)

pytestmark = pytest.mark.db
ROOT = Principal(key_id="root", name="root", tenant_ids=["*"], scopes=["*"])


async def test_create_list_update_with_effective_defaults(migrated_db, db_settings) -> None:  # noqa: ANN001
    view = await create_tenant(
        TenantSpec(tenant_id="retail-apac", display_name="Retail", daily_row_quota=50_000_000),
        ROOT,
        db_settings,
    )
    assert view.exists and view.display_name == "Retail"
    assert view.effective.retention_days == db_settings.retention_days == 30
    assert view.effective.max_running == db_settings.job_tenant_max_running == 8
    assert view.effective.max_queued == db_settings.job_tenant_max_queued
    assert (
        view.overrides["daily_row_quota"] == 50_000_000 and view.overrides["retention_days"] is None
    )
    updated = await update_tenant(
        "retail-apac", TenantPatch(retention_days=7, max_running=2), ROOT, db_settings
    )
    assert updated.effective.retention_days == 7 and updated.effective.max_running == 2
    assert updated.effective.daily_row_quota == 50_000_000 and updated.display_name == "Retail"
    reset = await update_tenant("retail-apac", TenantPatch(retention_days=None), ROOT, db_settings)
    assert reset.effective.retention_days == 30 and reset.effective.max_running == 2
    with pytest.raises(TenantError) as again:
        await create_tenant(TenantSpec(tenant_id="retail-apac"), ROOT, db_settings)
    assert again.value.status == 409 and again.value.code == "tenant_exists"
    with pytest.raises(TenantError) as missing:
        await update_tenant("nobody", TenantPatch(note="x"), ROOT, db_settings)
    assert missing.value.status == 404 and missing.value.code == "tenant_not_found"


def test_ids_and_hosts_are_validated() -> None:
    with pytest.raises(ValidationError):
        TenantSpec(tenant_id="Bad Id")
    with pytest.raises(ValidationError):
        TenantSpec(tenant_id="_system")
    spec = TenantSpec(
        tenant_id="t1",
        registered_hosts=[
            "https://Onboarding-UAT.bank.internal:443/apply",
            "demo-site",
            "demo-site",
        ],
    )
    assert spec.registered_hosts == ["demo-site", "onboarding-uat.bank.internal"]
    for bad in ("https://user:pw@host", "*.bank.internal", "a b"):
        with pytest.raises(ValidationError) as err:
            TenantSpec(tenant_id="t1", registered_hosts=["ok.example", bad])
        text = str(err.value)
        assert "pw" not in text and "user" not in text and "entry 1" in text
    with pytest.raises(ValidationError) as err:
        TenantPatch(registered_hosts=["https://user:pw@host"])
    assert "pw" not in str(err.value)


async def test_directory_crosses_tenants_without_bypassing_rls(migrated_db) -> None:  # noqa: ANN001
    for tenant in ("a", "b"):
        await jobs.create_job(tenant_id=tenant, kind=JobKind.run, phase=JobPhase.prepare, spec={})
    async with db.acquire_queue("c") as conn:
        await conn.execute(
            f"INSERT INTO {db.schema_ident()}.dps_connection (id, tenant_id, name, engine, params) "
            "VALUES ('c_only', 'c', 'x', 'postgres', '{}')"
        )
    directory = await tenant_directory()
    assert {"a", "b", "c"} <= set(directory)
    s = db.schema_ident()
    async with db.acquire("a") as conn:
        assert await conn.fetchval(f"SELECT count(*) FROM {s}.dps_job") == 1
    async with db.acquire(None) as conn:
        assert await conn.fetchval(f"SELECT count(*) FROM {s}.dps_job") == 0
        assert await conn.fetchval(f"SELECT count(*) FROM {s}.dps_connection") == 0
    await jobs.ensure_retention_job(6)
    assert "_system" not in await tenant_directory()
    assert "_system" in await tenant_directory(include_system=True)


async def test_rls_posture_accepts_the_m5_policies(migrated_db, db_settings) -> None:  # noqa: ANN001
    assert await db.assert_rls_posture(db_settings) == []
    async with db.acquire(None) as conn:
        rows = await conn.fetch(
            "SELECT tablename, policyname FROM pg_policies WHERE schemaname = $1",
            db_settings.pg_schema,
        )
    names = {(r["tablename"], r["policyname"]) for r in rows}
    assert ("dps_job", "owner_directory") in names and (
        "dps_connection",
        "owner_directory",
    ) in names
    assert ("dps_tenant_usage", "worker_usage") in names
    assert not any(t == "dps_tenant" for t, _ in names)


async def test_retention_change_restamps_terminal_jobs(migrated_db, db_settings) -> None:  # noqa: ANN001
    await create_tenant(TenantSpec(tenant_id="keep"), ROOT, db_settings)
    done = await jobs.create_job(
        tenant_id="keep", kind=JobKind.run, phase=JobPhase.prepare, spec={}
    )
    live = await jobs.create_job(
        tenant_id="keep", kind=JobKind.run, phase=JobPhase.prepare, spec={}
    )
    s = db.schema_ident()
    async with db.acquire("keep") as conn:
        await conn.execute(
            f"UPDATE {s}.dps_job SET state = 'succeeded', finished_at = now() - interval '10 days', "
            "expires_at = now() + interval '20 days' WHERE id = $1",
            done.id,
        )
        await conn.execute(f"UPDATE {s}.dps_job SET state = 'running' WHERE id = $1", live.id)
        await conn.execute(
            f"INSERT INTO {s}.dps_artefact (id, tenant_id, job_id, kind, store, object_key, "
            "content_type, bytes, sha256) VALUES ('a_1', 'keep', $1, 'evidence_json', 'local', "
            "'k', 'application/json', 2, 'x')",
            done.id,
        )
    await update_tenant("keep", TenantPatch(retention_days=5), ROOT, db_settings)
    async with db.acquire("keep") as conn:
        row = await conn.fetchrow(
            f"SELECT finished_at, expires_at FROM {s}.dps_job WHERE id = $1", done.id
        )
        art = await conn.fetchval(f"SELECT expires_at FROM {s}.dps_artefact WHERE id = 'a_1'")
        running = await conn.fetchval(f"SELECT expires_at FROM {s}.dps_job WHERE id = $1", live.id)
    assert abs(row["expires_at"] - (row["finished_at"] + timedelta(days=5))) < timedelta(seconds=1)
    assert art == row["expires_at"] and running is None


async def test_list_is_filtered_by_the_callers_grants(migrated_db, db_settings) -> None:  # noqa: ANN001
    for tenant in ("a", "b"):
        await create_tenant(TenantSpec(tenant_id=tenant), ROOT, db_settings)
    one = Principal(key_id="k", name="k", tenant_ids=["a"], scopes=["admin"])
    assert [v.tenant_id for v in await list_tenants(one, db_settings)] == ["a"]
    both = [v.tenant_id for v in await list_tenants(ROOT, db_settings)]
    assert {"a", "b"} <= set(both) and not any(t.startswith("_") for t in both)
    with pytest.raises(TenantError) as err:
        await create_tenant(TenantSpec(tenant_id="z"), one, db_settings)
    assert err.value.status == 403 and err.value.code == "tenant_forbidden"
    with pytest.raises(TenantError) as err:
        await update_tenant("b", TenantPatch(note="n"), one, db_settings)
    assert err.value.status == 403


async def test_view_counts_usage_and_keys(migrated_db, db_settings) -> None:  # noqa: ANN001
    from dps.auth_db import create_key

    await create_tenant(TenantSpec(tenant_id="u1"), ROOT, db_settings)
    await jobs.create_job(tenant_id="u1", kind=JobKind.run, phase=JobPhase.prepare, spec={})
    await create_key(
        name="u1-key",
        tenant_ids=["u1"],
        scopes=["detect"],
        expires_in_days=5,
        actor=ROOT,
        settings=db_settings,
    )
    async with db.acquire("u1") as conn:
        await conn.execute(
            f"INSERT INTO {db.schema_ident()}.dps_tenant_usage (tenant_id, day, rows_written) "
            "VALUES ('u1', (now() AT TIME ZONE 'UTC')::date, 42)"
        )
    [view] = [v for v in await list_tenants(ROOT, db_settings) if v.tenant_id == "u1"]
    assert view.usage.queued == 1 and view.usage.running == 0 and view.usage.rows_today == 42
    assert view.keys == {"active": 1, "disabled": 0}
