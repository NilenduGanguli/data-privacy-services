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


async def test_summary_llm_tiles_are_per_tenant(migrated_db, db_settings) -> None:  # noqa: ANN001
    from dps.reports import summary

    await _audit("t1", "obfuscate", {"llm_calls": 3, "llm_blocked": 1}, mode="obfuscate")
    await _audit("t1", "synthesize", {"llm_calls": "x", "llm_raw_values": None}, mode="synthesize")
    await _audit("t2", "synthesize", {"llm_calls": 5}, mode="synthesize")
    job = await jobs.create_job(
        tenant_id="t1", kind=JobKind.run, phase=JobPhase.prepare, mode="synthesize", spec={}
    )
    async with db.acquire("t1") as conn:  # a job handler that called the LLM (Task 8B)
        await conn.execute(
            f"UPDATE {db.schema_ident()}.dps_job SET state = 'succeeded', finished_at = now(), "
            "progress = $2::jsonb WHERE id = $1",
            job.id,
            {"metrics": {"llm": {"calls": 2, "raw_values": 0, "blocked": 0}}},
        )
    t1 = (await summary("t1", 14, db_settings)).tiles
    assert t1["llm_calls"] == 5 and t1["llm_blocked"] == 1 and t1["raw_pii_sent_to_llm"] == 0
    t2 = (await summary("t2", 14, db_settings)).tiles
    assert t2["llm_calls"] == 5 and t2["llm_blocked"] == 0 and t2["raw_pii_sent_to_llm"] == 0


async def test_run_counters_come_from_the_partitions_as_the_worker_keeps_them(  # noqa: ANN001
    migrated_db, db_settings
) -> None:
    """A real run job keeps ``rows_written`` and ``values_by_method`` on its partitions (the job's
    ``progress`` JSON has neither; ``GET /jobs/{id}`` sums them): the roll-up, the trend and the
    Layer 2 card must count them (found by the M5 console walk against the lane stack)."""
    from dps.reports import summary

    tenant = "r1"
    s = db.schema_ident()
    job = await jobs.create_job(
        tenant_id=tenant, kind=JobKind.run, phase=JobPhase.prepare, mode="obfuscate", spec={}
    )
    async with db.acquire(tenant) as conn:
        await conn.execute(
            f"INSERT INTO {s}.dps_connection (id, tenant_id, name, engine, params) "
            "VALUES ('conn_r1', $1, 'bank-pg', 'postgres', '{}'::jsonb)",
            tenant,
        )
        await conn.execute(
            f"UPDATE {s}.dps_job SET state = 'succeeded', finished_at = now(), "
            "connection_id = 'conn_r1', progress = '{\"outcome\": \"succeeded\"}'::jsonb "
            "WHERE id = $1",
            job.id,
        )
        for no in range(2):
            await conn.execute(
                f"INSERT INTO {s}.dps_job_partition (job_id, tenant_id, partition_no, state, "
                "rows_written, values_by_method, finished_at) "
                "VALUES ($1, $2, $3, 'done', 250, $4::jsonb, now())",
                job.id,
                tenant,
                no,
                {"fpe_digits": 200, "email": 100, "keep": 250},
            )
    assert await refresh_rollups(tenant, _today(), _today(), settings=db_settings)
    [run] = [r for r in await _daily(tenant) if r["action"] == "job.run"]
    assert run["rows_written"] == 500 and run["values_transformed"] == 600
    out = await summary(tenant, 14, db_settings)
    assert out.tiles["values_protected"] == 600 and out.trend[-1].obfuscated == 600
    assert out.layers["conn"]["rows"] == 500
    assert out.layers["conn"]["by_engine"] == {"postgres": 500}


# --- S2 console r2 B1: one meaning of a UI sweep on the tile, the chart, the card and Overview ---
async def _scan(
    tenant: str, state: str, *, error_code: str | None = None, pack: dict[str, Any] | None = None
) -> str:
    """A finished scan job (``state``), with its evidence pack when ``pack`` is given."""
    job = await jobs.create_job(
        tenant_id=tenant, kind=JobKind.scan, phase=JobPhase.scan, mode="synthesize", spec={}
    )
    s = db.schema_ident()
    async with db.acquire(tenant) as conn:
        await conn.execute(
            f"UPDATE {s}.dps_job SET state = $2, finished_at = now(), error_code = $3, "
            "error = $3 WHERE id = $1",
            job.id,
            state,
            error_code,
        )
        if pack is not None:
            body = {
                "pack_version": "dps.evidence.v1",
                "id": f"ev_{job.id}",
                "tenant_id": tenant,
                "job_id": job.id,
                "job_kind": "scan",
                "mode": "synthesize",
                "source": {"kind": "ui", "origin": "https://uat.bank.example"},
                "scan": {"pages": 3, "submissions": 0, "non_get_allowed": 0},
                **pack,
            }
            await conn.execute(
                f"INSERT INTO {s}.dps_evidence (id, tenant_id, job_id, pack, pack_sha256) "
                "VALUES ($1, $2, $3, $4::jsonb, 'abc')",
                body["id"],
                tenant,
                job.id,
                body,
            )
    return job.id


async def _scans(tenant: str) -> dict[str, str]:
    """A scan that succeeded (its sweep passed); one whose own verification failed
    (``submission_detected``: the request log held a non-GET the guard allowed); one that failed
    before it crawled (``secret_unavailable``: no pack, nothing swept); one canceled."""
    passed = {"outcome": "succeeded", "verification": {"passed": True}, "errors": []}
    caught = {
        "outcome": "failed",
        "verification": {"passed": False},
        "errors": [{"code": "submission_detected", "where": "guard"}],
    }
    canceled = {"outcome": "canceled", "verification": {"passed": True}, "errors": []}
    return {
        "passed": await _scan(tenant, "succeeded", pack=passed),
        "caught": await _scan(tenant, "failed", error_code="submission_detected", pack=caught),
        "setup": await _scan(tenant, "failed", error_code="secret_unavailable"),
        "canceled": await _scan(tenant, "canceled", pack=canceled),
    }


def test_a_ui_sweep_means_the_same_on_every_reports_surface(reports_app) -> None:  # noqa: ANN001
    """S2 console r2 B1. A scan that fails before it sweeps anything (``secret_unavailable``: no
    pack) counted as a failed UI sweep on the tile and the chart, while the Failed sweeps card never
    listed a scan and the Overview posture left scans out. Now a UI sweep is a scan's own
    verification: a scan that succeeded passed; a scan whose evidence records a verification error
    failed and is listed; a scan that failed before crawling or was canceled is no sweep. The
    tile's UI failures equal the card's, and the Overview posture counts the same sweeps."""
    ids = reports_app.portal.call(functools.partial(_scans, "r1"))
    r = reports_app.get("/api/v1/reports/jobs", params={"days": 14}, headers=H)
    assert r.status_code == 200, r.text
    body = r.json()
    ui = [x for x in body["sweeps"] if x["layer"] == "ui"]
    listed = [f for f in body["failed_sweeps"] if f["where"] == "scan"]
    assert sum(x["failed"] for x in ui) == len(listed) == 1, (ui, listed)
    assert sum(x["passed"] for x in ui) == 1
    [caught] = listed
    assert caught["job_id"] == ids["caught"] and caught["error_code"] == "submission_detected"
    assert caught["day"] == _today().isoformat() and caught["partitions"] == []
    assert body["totals"]["sweeps_failed"] == 1 + 1  # the leakage partition and the caught scan
    summary = reports_app.get("/api/v1/reports/summary", params={"days": 14}, headers=H).json()
    # API 2/2, partitions 2 of 3, UI 1 of 2: 5 of 7 sweeps passed
    assert summary["posture"]["sweeps_passed_pct"] == round(100 * 5 / 7, 1)
    _clean(r.text)
