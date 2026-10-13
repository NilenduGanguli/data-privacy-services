"""Report roll-ups and the reports API (M5 Task 12): value-free daily aggregates by layer, the
Overview summary, sweeps, evidence summaries and API roll-up documents — per tenant only."""

from __future__ import annotations

import asyncio
import functools
import json
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from dps import db, jobs
from dps.config import Settings
from dps.jobs import JobKind, JobPhase
from dps.reports import refresh_rollups, rollup_lock_key
from tests.fixtures.db import _truncate, api_settings

pytestmark = pytest.mark.db
SENTINELS = ("Zebulon", "Quixote", "123-45-6789", "SENTINEL-TRACE-zq9")
OBFUSCATE_COUNTS = {
    "findings_total": 4,
    "findings_by_type": {"SSN": 2, "EMAIL": 2},
    "methods_used": {"fpe_digits": 3, "keep": 5},
    "verification_passed": True,
}


def _today() -> date:
    return datetime.now(UTC).date()


async def _audit(
    tenant: str,
    action: str,
    counts: dict[str, Any],
    *,
    outcome: str = "ok",
    mode: str | None = None,
) -> None:
    async with db.acquire(tenant) as conn:
        await conn.execute(
            f"INSERT INTO {db.schema_ident()}.dps_audit_log (tenant_id, key_id, trace_id, route, "
            "action, mode, policy, outcome, status, counts) "
            "VALUES ($1, 'static-x', 'SENTINEL-TRACE-zq9', '/api/v1/x', $2, $3, 'dpdp-default', $4, "
            "200, $5::jsonb)",
            tenant,
            action,
            mode,
            outcome,
            counts,
        )


def _pack(
    tenant: str,
    job_id: str,
    evidence_id: str,
    *,
    mode: str,
    ticket: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "pack_version": "dps.evidence.v1",
        "id": evidence_id,
        "tenant_id": tenant,
        "job_id": job_id,
        "job_kind": "run",
        "outcome": "succeeded",
        "mode": mode,
        "label": "x",
        "purpose": {"ticket": ticket, "legal_basis": "test"},
        "source": {"engine": "postgres", "object": "bank.customers"},
        "target": {"object": "uat.customers", "write_mode": "table"},
        "policy": {"name": "dpdp-default", "hash": "h", "version": "1"},
        "columns": [{"path": "$.EMAIL", "entity_type": "EMAIL", "method": "email", "values": 3}],
        "reveal_map": [{"path": "$.EMAIL", "method": "Zebulon"}],
        "partitions": [{"no": 0, "rows": 1, "state": "done"}],
        "counts": {"rows_written": 1000},
        "verification": {"passed": True, "hits": 0},
        **(extra or {}),
    }


async def seed(tenant: str = "r1") -> dict[str, str]:
    """Audit rows, a finished run job with three partitions (one leaked), two evidence packs."""
    for _ in range(2):
        await _audit(tenant, "obfuscate", OBFUSCATE_COUNTS, mode="obfuscate")
    await _audit(tenant, "job.create", {})
    await _audit(tenant, "admin.key.create", {"tenants": 1})
    job = await jobs.create_job(
        tenant_id=tenant,
        kind=JobKind.run,
        phase=JobPhase.prepare,
        mode="obfuscate",
        spec={"target": {"write_mode": "table", "object": {"owner": "uat", "name": "customers"}}},
        object_ref="bank.customers",
    )
    synth = await jobs.create_job(
        tenant_id=tenant, kind=JobKind.run, phase=JobPhase.prepare, mode="synthesize", spec={}
    )
    s = db.schema_ident()
    progress = {
        "rows_written": 1000,
        "values_by_method": {"fpe_digits": 900, "keep": 50, "copula_resample": 3},
        "partitions_total": 3,
        "partitions_done": 2,
    }
    async with db.acquire(tenant) as conn:
        await conn.execute(
            f"UPDATE {s}.dps_job SET state = 'succeeded', finished_at = now(), "
            "progress = $2::jsonb, error = 'Zebulon Quixote' WHERE id = $1",
            job.id,
            progress,
        )
        for no, (state, code) in enumerate([("done", None), ("done", None), ("failed", "leakage")]):
            await conn.execute(
                f"INSERT INTO {s}.dps_job_partition (job_id, tenant_id, partition_no, state, "
                "rows_written, finished_at, error_code) VALUES ($1, $2, $3, $4, 10, now(), $5)",
                job.id,
                tenant,
                no,
                state,
                code,
            )
        packs = {
            "ev_obf": _pack(
                tenant,
                job.id,
                "ev_obf",
                mode="obfuscate",
                ticket="CHG-1",
                extra={"errors": [{"where": "$.SSN", "code": "leakage"}]},
            ),  # fmt: skip
            "ev_syn": _pack(
                tenant,
                synth.id,
                "ev_syn",
                mode="synthesize",
                ticket="CHG-2",
                extra={
                    "fidelity": {"score": 0.96},
                    "privacy": {"exact_row_matches": 0},
                    "columns": [],
                },
            ),
        }
        for evidence_id, pack in packs.items():
            await conn.execute(
                f"INSERT INTO {s}.dps_evidence (id, tenant_id, job_id, pack, pack_sha256) "
                "VALUES ($1, $2, $3, $4::jsonb, 'abc')",
                evidence_id,
                tenant,
                pack["job_id"],
                pack,
            )
    return {"job": job.id, "synth": synth.id}


async def _daily(tenant: str) -> list[dict[str, Any]]:
    async with db.acquire(tenant) as conn:
        rows = await conn.fetch(
            f"SELECT * FROM {db.schema_ident()}.dps_report_daily ORDER BY 1, 2, 3, 4"
        )
    return [dict(r) for r in rows]


async def _entities(tenant: str) -> dict[tuple[str, str], int]:
    async with db.acquire(tenant) as conn:
        rows = await conn.fetch(
            f"SELECT layer, entity_type, findings FROM {db.schema_ident()}.dps_report_entity_daily"
        )
    return {(r["layer"], r["entity_type"]): r["findings"] for r in rows}


async def test_refresh_rolls_up_audit_rows_by_layer_and_day(migrated_db, db_settings) -> None:  # noqa: ANN001
    await seed("r1")
    assert await refresh_rollups("r1", _today(), _today(), settings=db_settings)
    rows = await _daily("r1")
    [obf] = [r for r in rows if r["layer"] == "api" and r["action"] == "obfuscate"]
    assert obf["calls"] == 2 and obf["findings"] == 8 and obf["values_transformed"] == 6
    assert obf["verify_passed"] == 2 and obf["verify_failed"] == 0 and obf["mode"] == "obfuscate"
    assert any(r["layer"] == "admin" and r["action"] == "admin.key.create" for r in rows)
    assert not any(r["layer"] == "api" and r["action"] == "job.create" for r in rows)
    entities = await _entities("r1")
    assert entities[("api", "SSN")] == 4 and entities[("api", "EMAIL")] == 4
    assert entities[("conn", "EMAIL")] == 3
    async with db.acquire("r1") as conn:
        refreshed = await conn.fetchval(
            f"SELECT count(*) FROM {db.schema_ident()}.dps_report_refresh WHERE day = $1", _today()
        )
    assert refreshed == 1


async def test_refresh_is_idempotent_and_locked(migrated_db, db_settings) -> None:  # noqa: ANN001
    await seed("r1")
    assert await refresh_rollups("r1", _today() - timedelta(days=2), _today(), settings=db_settings)
    first = await _daily("r1")
    assert await refresh_rollups("r1", _today() - timedelta(days=2), _today(), settings=db_settings)
    strip = [{k: v for k, v in r.items() if k != "refreshed_at"} for r in await _daily("r1")]
    assert strip == [{k: v for k, v in r.items() if k != "refreshed_at"} for r in first]
    async with db.acquire("r1") as conn, conn.transaction():
        await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", rollup_lock_key("r1"))
        assert not await refresh_rollups("r1", _today(), _today(), settings=db_settings)
    async with db.acquire("r1") as conn:
        days = await conn.fetchval(f"SELECT count(*) FROM {db.schema_ident()}.dps_report_refresh")
    assert days == 3  # empty days are marked refreshed too


async def test_jobs_and_partitions_roll_up(migrated_db, db_settings) -> None:  # noqa: ANN001
    await seed("r1")
    await refresh_rollups("r1", _today(), _today(), settings=db_settings)
    rows = await _daily("r1")
    [run] = [r for r in rows if r["action"] == "job.run" and r["mode"] == "obfuscate"]
    assert (
        run["layer"] == "conn" and run["rows_written"] == 1000 and run["values_transformed"] == 900
    )
    parts = [r for r in rows if r["action"] == "job.partition"]
    assert (
        sum(r["verify_passed"] for r in parts) == 2 and sum(r["verify_failed"] for r in parts) == 1
    )
    assert sum(r["calls"] for r in parts) == 3


@pytest.fixture
def reports_app(db_settings: Settings, tmp_path: Path) -> Iterator[Any]:
    from fastapi.testclient import TestClient

    from dps.app import create_app

    settings = api_settings(db_settings, tmp_path)
    try:
        with TestClient(create_app(settings)) as client:
            client.portal.call(functools.partial(seed, "r1"))  # type: ignore[union-attr]
            yield client
    finally:
        asyncio.run(_truncate(db_settings))


H = {"X-API-KEY": "dps_full", "X-DPS-Tenant": "r1"}


def _clean(text: str) -> None:
    for sentinel in SENTINELS:
        assert sentinel not in text


def test_summary_over_http(reports_app) -> None:  # noqa: ANN001
    r = reports_app.get("/api/v1/reports/summary", params={"days": 14}, headers=H)
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["trend"]) == 14 and body["trend"][-1]["day"] == _today().isoformat()
    assert body["tiles"]["values_protected"] == 6 + 900
    assert body["trend"][-1]["obfuscated"] == 906 and body["trend"][-1]["synthesized"] == 0
    assert body["findings_by_type"][0]["entity_type"] in {"SSN", "EMAIL"}
    assert body["findings_by_type"][0]["sensitivity"] in {"LOW", "MEDIUM", "HIGH", "CRITICAL"}
    assert 0 <= body["posture"]["score"] <= 100
    assert body["tiles"]["raw_pii_sent_to_llm"] == 0 and body["tiles"]["jobs_completed"] >= 1
    assert body["layers"]["api"]["requests"] == 2 and body["layers"]["conn"]["rows"] == 1000
    assert len(body["recent_jobs"]) == 2 and body["days"] == 14
    _clean(r.text)
    bad = reports_app.get("/api/v1/reports/summary", params={"days": 91}, headers=H)
    assert bad.status_code == 422


def test_reports_jobs_lists_failed_sweeps(reports_app, db_settings) -> None:  # noqa: ANN001
    r = reports_app.get("/api/v1/reports/jobs", params={"days": 7}, headers=H)
    assert r.status_code == 200, r.text
    body = r.json()
    [failed] = [f for f in body["failed_sweeps"] if f["where"] == "job"]
    assert failed["partitions"] == [2] and failed["error_code"] == "leakage"
    assert failed["paths"] == ["$.SSN"]
    assert body["totals"]["sweeps_failed"] == 1 and body["totals"]["packs"] == 2
    assert body["totals"]["audit_rows"] == 4
    today = [x for x in body["sweeps"] if x["day"] == _today().isoformat()]
    assert {x["layer"] for x in today} == {"api", "conn", "ui"}
    _clean(r.text)


def test_evidence_list_summaries_and_filters(reports_app) -> None:  # noqa: ANN001
    r = reports_app.get("/api/v1/reports/evidence", headers=H)
    assert r.status_code == 200, r.text
    items = {i["id"]: i for i in r.json()["items"]}
    assert set(items) == {"ev_obf", "ev_syn"}
    assert items["ev_syn"]["summary"]["fidelity_score"] == 0.96
    assert items["ev_syn"]["summary"]["exact_row_matches"] == 0
    assert items["ev_obf"]["layer"] == "conn" and items["ev_obf"]["summary"]["ticket"] == "CHG-1"
    assert "reveal_map" not in r.text and "partitions" not in r.text
    _clean(r.text)
    failed = reports_app.get("/api/v1/reports/evidence", params={"outcome": "failed"}, headers=H)
    assert failed.json()["items"] == []
    found = reports_app.get("/api/v1/reports/evidence", params={"q": "chg-2"}, headers=H)
    assert [i["id"] for i in found.json()["items"]] == ["ev_syn"]
    page = reports_app.get("/api/v1/reports/evidence", params={"limit": 1}, headers=H).json()
    nxt = reports_app.get(
        "/api/v1/reports/evidence", params={"limit": 1, "cursor": page["next_cursor"]}, headers=H
    ).json()
    assert len(page["items"]) == 1 and len(nxt["items"]) == 1 and nxt["next_cursor"] is None
    assert page["items"][0]["id"] != nxt["items"][0]["id"]
    detect = {"X-API-KEY": "dps_detect", "X-DPS-Tenant": "t1"}
    assert reports_app.get("/api/v1/reports/evidence", headers=detect).status_code == 403


def test_api_rollups_list_and_json_document(reports_app) -> None:  # noqa: ANN001
    r = reports_app.get("/api/v1/reports/api-rollups", params={"days": 1}, headers=H)
    assert r.status_code == 200, r.text
    [obf] = [i for i in r.json()["items"] if i["action"] == "obfuscate"]
    assert obf["calls"] == 2 and obf["findings"] == 8 and obf["values"] == 6
    day = _today().isoformat()
    doc = reports_app.get(
        f"/api/v1/reports/api-rollups/{day}", params={"action": "obfuscate"}, headers=H
    )
    assert doc.status_code == 200 and "attachment" in doc.headers["content-disposition"]
    assert f"api-rollup-{day}-obfuscate.json" in doc.headers["content-disposition"]
    body = doc.json()
    assert set(body) == {
        "day",
        "action",
        "calls",
        "findings",
        "values",
        "verify_passed",
        "verify_failed",
        "by_outcome",
        "entities",
        "generated_at",
        "sha256",
    }
    assert body["calls"] == 2 and body["by_outcome"] == {"ok": 2} and body["entities"]["SSN"] == 4
    missing = reports_app.get(
        f"/api/v1/reports/api-rollups/{day}", params={"action": "nope"}, headers=H
    )
    assert missing.status_code == 404 and missing.json()["code"] == "rollup_not_found"
    _clean(doc.text)


def test_rollups_are_tenant_isolated(reports_app) -> None:  # noqa: ANN001
    assert reports_app.get("/api/v1/reports/summary", headers=H).status_code == 200
    other = reports_app.get("/api/v1/reports/summary", headers={**H, "X-DPS-Tenant": "r2"})
    body = other.json()
    assert body["tiles"]["values_protected"] == 0 and body["findings_by_type"] == []
    assert body["recent_jobs"] == [] and body["layers"]["api"]["requests"] == 0

    async def peek() -> int:
        async with db.acquire("r2") as conn:
            return int(
                await conn.fetchval(
                    f"SELECT (SELECT count(*) FROM {db.schema_ident()}.dps_report_daily) + "
                    f"(SELECT count(*) FROM {db.schema_ident()}.dps_report_entity_daily "
                    "WHERE tenant_id = 'r1')"
                )
            )

    assert reports_app.portal.call(peek) == 0
    assert json.dumps(body).count("r1") == 0
