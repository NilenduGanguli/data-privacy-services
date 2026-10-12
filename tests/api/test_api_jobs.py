"""``/api/v1`` jobs, snapshots, job groups, evidence and artefacts (contract §9) without a worker:
validation codes, once-only key return, idempotent replays, controls, review and lookups."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import asyncpg
import pytest

from dps import db
from dps.config import Settings
from dps.engine.transform.crypto import generate_key_material
from dps.snapshots import create_snapshot
from tests.api.test_api_connections import FULL, JOBS, PG
from tests.connectors.factories import make_snapshot

pytestmark = pytest.mark.db
PURPOSE = {"ticket": "CHG-48213", "legal_basis": "legitimate interest"}


def seed_snapshot(settings: Settings, tenant: str, connection_id: str, object_ref: str) -> str:
    """Insert a proposed snapshot on its own connection (the app's pools belong to the client's
    event loop) and return its id."""

    async def run() -> str:
        conn = await asyncpg.connect(
            host=settings.pg_host,
            port=settings.pg_port,
            user=settings.pg_user,
            password=settings.pg_password,
            database=settings.pg_database,
        )
        try:
            for typ in ("json", "jsonb"):
                await conn.set_type_codec(
                    typ, encoder=json.dumps, decoder=json.loads, schema="pg_catalog"
                )
            await conn.execute("SELECT set_config('app.tenant_id', $1, false)", tenant)
            snap = make_snapshot(tenant, connection_id=connection_id, object_ref=object_ref)
            await create_snapshot(snap, conn=conn)
            return snap.id
        finally:
            await conn.close()

    return asyncio.run(run())


def _connection(
    c: Any, name: str, ref: str, roles: list[str], headers: dict[str, str], **kw: Any
) -> str:  # noqa: ANN401
    r = c.post(
        "/api/v1/connections",
        json={
            "name": name,
            "engine": "postgres",
            "params": PG,
            "secret_ref": ref,
            "roles": roles,
            **kw,
        },
        headers=headers,
    )
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


def _run(src: str, snap: str, target: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {
        "mode": "obfuscate",
        "snapshot_id": snap,
        "source": {"connection_id": src, "object": {"owner": "bank", "name": "customers"}},
        "target": target,
        "purpose": PURPOSE,
        **extra,
    }


@pytest.fixture
def seeded(api_client, db_settings) -> dict[str, Any]:  # noqa: ANN001
    """Source, UAT and production connections; accepted snapshots of bank.customers on the
    source and on the production connection; a proposed snapshot to review."""
    c, _ = api_client
    src = _connection(c, "bank-src", "dps/src/test-pg", ["source"], JOBS)
    uat = _connection(c, "bank-uat", "dps/src/test-pg-rw", ["target"], JOBS)
    prod = _connection(
        c, "bank-prod", "dps/src/test-pg-rw", ["source", "target"], FULL, tags=["production"]
    )
    snaps = {
        "src": seed_snapshot(db_settings, "t1", src, "bank.customers"),
        "prod": seed_snapshot(db_settings, "t1", prod, "bank.customers"),
        "review": seed_snapshot(db_settings, "t1", src, "bank.customers_review"),
    }
    for key in ("src", "prod"):
        accepted = c.post(f"/api/v1/snapshots/{snaps[key]}/accept", headers=JOBS)
        assert accepted.status_code == 200 and accepted.json()["accepted_by"] == "jobs"
    return {
        "c": c,
        "settings": db_settings,
        "src": src,
        "uat": uat,
        "prod": prod,
        **{f"snap_{k}": v for k, v in snaps.items()},
    }


def test_run_validation_codes_and_once_only_keys(seeded) -> None:  # noqa: ANN001
    c, src, uat, prod = seeded["c"], seeded["src"], seeded["uat"], seeded["prod"]
    in_place = {"write_mode": "in_place"}
    no_confirm = c.post(
        "/api/v1/jobs/run", json=_run(prod, seeded["snap_prod"], in_place), headers=JOBS
    )
    assert no_confirm.status_code == 422 and no_confirm.json()["code"] == "confirm_write_required"
    confirmed = {**in_place, "confirm_write": True}
    no_admin = c.post(
        "/api/v1/jobs/run", json=_run(prod, seeded["snap_prod"], confirmed), headers=JOBS
    )
    assert no_admin.status_code == 403 and no_admin.json()["code"] == "admin_scope_required"
    table = {
        "write_mode": "table",
        "connection_id": uat,
        "object": {"owner": "bank_uat", "name": "customers"},
    }
    body = _run(src, seeded["snap_src"], table)
    r = c.post("/api/v1/jobs/run", json=body, headers=JOBS)
    assert r.status_code == 202, r.text
    out = r.json()
    assert out["key_material"]["dek"] and out["job"]["state"] == "queued"
    assert out["job"]["phase"] == "prepare" and r.headers["cache-control"] == "no-store"
    assert out["job"]["spec"]["source"]["selection"]["params"] == {}
    assert out["key_material"]["dek"] not in json.dumps(out["job"]) and "dek" not in json.dumps(
        out["job"]["spec"]
    )
    first = c.post("/api/v1/jobs/run", json=body, headers={**JOBS, "Idempotency-Key": "abc"})
    again = c.post("/api/v1/jobs/run", json=body, headers={**JOBS, "Idempotency-Key": "abc"})
    assert first.status_code == 202 and again.status_code == 200
    assert again.headers["idempotent-replay"] == "true"
    assert (
        again.json()["job"]["id"] == first.json()["job"]["id"]
        and "key_material" not in again.json()
    )
    bad_key = c.post(
        "/api/v1/jobs/run", json=body, headers={**JOBS, "Idempotency-Key": "no spaces"}
    )
    assert bad_key.status_code == 422 and bad_key.json()["code"] == "idempotency_key_invalid"
    missing = c.post(
        "/api/v1/jobs/run", json=_run("c_missing", seeded["snap_src"], table), headers=JOBS
    )
    assert missing.status_code == 404 and missing.json()["code"] == "connection_not_found"
    not_accepted = c.post(
        "/api/v1/jobs/run", json=_run(src, seeded["snap_review"], table), headers=JOBS
    )
    assert (
        not_accepted.status_code == 409 and not_accepted.json()["code"] == "snapshot_not_accepted"
    )


def test_job_lookups_stats_and_controls(seeded) -> None:  # noqa: ANN001
    c, src, uat = seeded["c"], seeded["src"], seeded["uat"]
    table = {
        "write_mode": "table",
        "connection_id": uat,
        "object": {"owner": "bank_uat", "name": "customers"},
    }
    job = c.post(
        "/api/v1/jobs/run", json=_run(src, seeded["snap_src"], table), headers=JOBS
    ).json()["job"]
    listed = c.get("/api/v1/jobs?kind=run&state=queued&limit=10", headers=JOBS).json()
    assert [j["id"] for j in listed["items"]] == [job["id"]] and listed["next_cursor"] is None
    assert c.get("/api/v1/jobs?kind=nope", headers=JOBS).status_code == 422
    assert c.get(f"/api/v1/jobs/{job['id']}", headers=JOBS).json()["id"] == job["id"]
    other = c.get(f"/api/v1/jobs/{job['id']}", headers={**JOBS, "X-DPS-Tenant": "t2"})
    assert other.status_code == 404 and other.json()["code"] == "job_not_found"
    assert c.get(f"/api/v1/jobs/{job['id']}/partitions", headers=JOBS).json() == {"items": []}
    stats = c.get("/api/v1/jobs/stats", headers=JOBS).json()
    assert stats["workers_online"] == 0 and stats["tenant_max_running"] == 8
    assert stats["by_state"] == {"queued": 1}
    paused = c.post(f"/api/v1/jobs/{job['id']}/pause", headers=JOBS)
    assert paused.status_code == 409 and paused.json()["code"] == "job_not_pausable"
    resumed = c.post(f"/api/v1/jobs/{job['id']}/resume", headers=JOBS)
    assert resumed.status_code == 409 and resumed.json()["code"] == "job_not_paused"
    retried = c.post(f"/api/v1/jobs/{job['id']}/retry", headers=JOBS)
    assert retried.status_code == 409 and retried.json()["code"] == "job_not_retryable"
    canceled = c.post(f"/api/v1/jobs/{job['id']}/cancel", headers=JOBS)
    assert canceled.status_code == 200
    assert (
        canceled.json()["phase"] == "finalize"
        and canceled.json()["progress"]["outcome"] == "canceled"
    )
    twice = c.post(f"/api/v1/jobs/{job['id']}/cancel", headers=JOBS)
    assert twice.status_code == 409 and twice.json()["code"] == "job_finalizing"
    assert c.post("/api/v1/jobs/j_missing/cancel", headers=JOBS).status_code == 404


def test_retry_needs_the_purged_key_back(seeded) -> None:  # noqa: ANN001
    c, src, uat = seeded["c"], seeded["src"], seeded["uat"]
    table = {
        "write_mode": "table",
        "connection_id": uat,
        "object": {"owner": "bank_uat", "name": "customers"},
    }
    out = c.post("/api/v1/jobs/run", json=_run(src, seeded["snap_src"], table), headers=JOBS).json()
    job_id = out["job"]["id"]

    async def fail_and_purge() -> None:  # what a failed finalize leaves behind
        conn = await asyncpg.connect(
            host=seeded["settings"].pg_host,
            port=seeded["settings"].pg_port,
            user="dps_worker_login",
            password="dps_worker_login",
            database=seeded["settings"].pg_database,
        )
        try:
            await conn.execute(
                f"UPDATE {db.schema_ident(seeded['settings'])}.dps_job SET state = 'failed', "
                "key_enc = NULL, finished_at = now() WHERE id = $1",
                job_id,
            )
        finally:
            await conn.close()

    asyncio.run(fail_and_purge())
    bare = c.post(f"/api/v1/jobs/{job_id}/retry", headers=JOBS)
    assert bare.status_code == 409 and bare.json()["code"] == "key_material_required"
    other = generate_key_material("key").model_dump(mode="json")
    wrong = c.post(
        f"/api/v1/jobs/{job_id}/retry", json={"consistency": {"key_material": other}}, headers=JOBS
    )
    assert wrong.status_code == 409 and wrong.json()["code"] == "key_material_mismatch"
    right = c.post(
        f"/api/v1/jobs/{job_id}/retry",
        json={"consistency": {"key_material": out["key_material"]}},
        headers=JOBS,
    )
    assert right.status_code == 200 and right.json()["state"] == "queued"
    assert right.json()["phase"] == "prepare" and out["key_material"]["dek"] not in right.text


def test_snapshot_review(seeded) -> None:  # noqa: ANN001
    c = seeded["c"]
    accepted = c.patch(
        f"/api/v1/snapshots/{seeded['snap_src']}",
        json={"overrides": [{"path": "$.segment", "method": {"method": "redact"}}]},
        headers=JOBS,
    )
    assert accepted.status_code == 409 and accepted.json()["code"] == "snapshot_not_proposed"
    reviewed = c.patch(
        f"/api/v1/snapshots/{seeded['snap_review']}",
        json={"overrides": [{"path": "$.segment", "method": {"method": "redact"}}]},
        headers=JOBS,
    )
    assert reviewed.status_code == 200
    segment = next(col for col in reviewed.json()["columns"] if col["name"] == "segment")
    assert segment["override"] is True and segment["method"]["method"] == "redact"
    listed = c.get(
        f"/api/v1/snapshots?connection_id={seeded['src']}&object=bank.customers_review&status=proposed",
        headers=JOBS,
    ).json()
    assert [s["id"] for s in listed["items"]] == [seeded["snap_review"]]
    got = c.get(f"/api/v1/snapshots/{seeded['snap_review']}", headers=JOBS).json()
    assert got["status"] == "proposed" and got["overrides"][0]["path"] == "$.segment"
    done = c.post(f"/api/v1/snapshots/{seeded['snap_review']}/accept", headers=FULL)
    assert done.status_code == 200 and done.json()["accepted_by"] == "full"
    missing = c.get("/api/v1/snapshots/snap_missing", headers=JOBS)
    assert missing.status_code == 404 and missing.json()["code"] == "snapshot_not_found"


def test_job_groups_return_keys_once(seeded) -> None:  # noqa: ANN001
    c, src, uat = seeded["c"], seeded["src"], seeded["uat"]
    made = c.post("/api/v1/job-groups", json={"name": "bank-uat-refresh"}, headers=JOBS)
    assert made.status_code == 201 and made.headers["cache-control"] == "no-store"
    group, key = made.json()["group"], made.json()["key_material"]
    assert key["dek"] and made.json()["synthesis_seed"]["seed"]
    assert group["kid"] == key["kid"]
    shown = c.get(f"/api/v1/job-groups/{group['id']}", headers=JOBS)
    assert shown.status_code == 200 and key["dek"] not in shown.text
    assert made.json()["synthesis_seed"]["seed"] not in shown.text
    assert shown.json()["jobs"] == []
    assert (
        c.post("/api/v1/job-groups", json={"name": "bank-uat-refresh"}, headers=JOBS).json()["code"]
        == "group_name_taken"
    )
    bad = c.post("/api/v1/job-groups", json={"name": "Not Valid"}, headers=JOBS)
    assert bad.status_code == 422
    table = {
        "write_mode": "table",
        "connection_id": uat,
        "object": {"owner": "bank_uat", "name": "customers"},
    }
    other = generate_key_material("key").model_dump(mode="json")
    mismatch = c.post(
        "/api/v1/jobs/run",
        json=_run(
            src,
            seeded["snap_src"],
            table,
            group_id=group["id"],
            consistency={"key_material": other},
        ),
        headers=JOBS,
    )
    assert mismatch.status_code == 409 and mismatch.json()["code"] == "group_key_mismatch"
    ok = c.post(
        "/api/v1/jobs/run",
        json=_run(
            src, seeded["snap_src"], table, group_id=group["id"], consistency={"key_material": key}
        ),
        headers=JOBS,
    )
    assert ok.status_code == 202 and "key_material" not in ok.json()  # the caller holds it
    listed = c.get("/api/v1/job-groups", headers=JOBS).json()
    assert [g["id"] for g in listed["items"]] == [group["id"]]
    members = c.get(f"/api/v1/job-groups/{group['id']}", headers=JOBS).json()["jobs"]
    assert [j["id"] for j in members] == [ok.json()["job"]["id"]]
    assert c.get("/api/v1/job-groups/grp_missing", headers=JOBS).json()["code"] == "group_not_found"


def test_discover_submission_and_unknown_evidence(seeded) -> None:  # noqa: ANN001
    c, src = seeded["c"], seeded["src"]
    body = {
        "connection_id": src,
        "objects": [{"owner": "bank", "name": "customers"}],
        "selection": {"where": "country_code = 'IN'"},
    }
    r = c.post("/api/v1/jobs/discover", json=body, headers={**JOBS, "Idempotency-Key": "d-1"})
    assert r.status_code == 202 and r.json()["job"]["kind"] == "discover"
    assert "'IN'" not in json.dumps(r.json())
    again = c.post("/api/v1/jobs/discover", json=body, headers={**JOBS, "Idempotency-Key": "d-1"})
    assert again.status_code == 200 and again.headers["idempotent-replay"] == "true"
    assert again.json()["job"]["id"] == r.json()["job"]["id"]
    uat_discover = c.post(
        "/api/v1/jobs/discover",
        json={"connection_id": seeded["uat"], "objects": [{"owner": "bank", "name": "customers"}]},
        headers=JOBS,
    )
    assert uat_discover.status_code == 422 and uat_discover.json()["code"] == "connection_role"
    ev = c.get("/api/v1/evidence/unknown", headers=JOBS)
    assert ev.status_code == 404 and ev.json()["code"] == "evidence_not_found"
    assert c.get("/api/v1/evidence/unknown/html", headers=JOBS).status_code == 404
    art = c.get("/api/v1/artefacts/unknown", headers=JOBS)
    assert art.status_code == 404 and art.json()["code"] == "artefact_not_found"
    assert c.get("/api/v1/evidence", headers=JOBS).json() == {"items": [], "next_cursor": None}
    detect_only = {"X-API-KEY": "dps_detect", "X-DPS-Tenant": "t1"}
    assert c.get("/api/v1/evidence", headers=detect_only).status_code == 403


def test_audit_actions(seeded, api_client) -> None:  # noqa: ANN001
    c, sink = api_client
    table = {
        "write_mode": "table",
        "connection_id": seeded["uat"],
        "object": {"owner": "bank_uat", "name": "customers"},
    }
    job = c.post(
        "/api/v1/jobs/run", json=_run(seeded["src"], seeded["snap_src"], table), headers=JOBS
    ).json()["job"]
    c.post(f"/api/v1/jobs/{job['id']}/cancel", headers=JOBS)
    c.post("/api/v1/job-groups", json={"name": "audit-group"}, headers=JOBS)
    actions = {(e.action, e.outcome) for e in sink.events}
    assert {
        ("snapshot.accept", "ok"),
        ("job.create", "ok"),
        ("job.cancel", "ok"),
        ("group.create", "ok"),
    } <= actions
    created = next(e for e in sink.events if e.action == "job.create" and e.outcome == "ok")
    assert created.job_id == job["id"] and created.status == 202
    assert created.route == "/api/v1/jobs/run"
    dumped = json.dumps([e.model_dump(mode="json") for e in sink.events])
    assert "bank_uat" not in dumped and "dek" not in dumped


def test_job_routes_share_layer_1_idempotency_names_and_codes(seeded) -> None:  # noqa: ANN001
    """One ``Idempotency-Key`` contract for Layer 1 (M1) and the job routes (M2, contract §9):
    the same header names, the same refusal for a malformed key, and 422
    ``idempotency_key_reused`` when a key comes back with another request — never a silent
    replay of a different job."""
    c, src, uat = seeded["c"], seeded["src"], seeded["uat"]
    table = {
        "write_mode": "table",
        "connection_id": uat,
        "object": {"owner": "bank_uat", "name": "customers"},
    }
    other = {**table, "object": {"owner": "bank_uat", "name": "customers_other"}}
    key = {"Idempotency-Key": "integ-reuse-1"}
    first = c.post(
        "/api/v1/jobs/run", json=_run(src, seeded["snap_src"], table), headers={**JOBS, **key}
    )
    assert first.status_code == 202, first.text
    reused = c.post(
        "/api/v1/jobs/run", json=_run(src, seeded["snap_src"], other), headers={**JOBS, **key}
    )
    assert reused.status_code == 422 and reused.json()["code"] == "idempotency_key_reused"
    assert "key_material" not in reused.json() and "idempotent-replay" not in reused.headers
    replay = c.post(
        "/api/v1/jobs/run", json=_run(src, seeded["snap_src"], table), headers={**JOBS, **key}
    )
    assert replay.status_code == 200 and replay.headers["idempotent-replay"] == "true"
    assert replay.json()["job"]["id"] == first.json()["job"]["id"]
    # the same key on another job route is another request
    discover = {"connection_id": src, "objects": [{"owner": "bank", "name": "customers"}]}
    across = c.post("/api/v1/jobs/discover", json=discover, headers={**JOBS, **key})
    assert across.status_code == 422 and across.json()["code"] == "idempotency_key_reused"
    d_key = {"Idempotency-Key": "integ-reuse-2"}
    d1 = c.post("/api/v1/jobs/discover", json=discover, headers={**JOBS, **d_key})
    d2 = c.post("/api/v1/jobs/discover", json=discover, headers={**JOBS, **d_key})
    assert d1.status_code == 202 and d2.status_code == 200
    assert d2.headers["idempotent-replay"] == "true"
    other_obj = {**discover, "objects": [{"owner": "bank", "name": "accounts"}]}
    d3 = c.post("/api/v1/jobs/discover", json=other_obj, headers={**JOBS, **d_key})
    assert d3.status_code == 422 and d3.json()["code"] == "idempotency_key_reused"
    # Layer 1 and the job routes refuse a malformed key with the same code and detail
    bad = {"Idempotency-Key": "no spaces"}
    l1 = c.post("/api/v1/detect", json={"data": {"a": "b"}}, headers={**FULL, **bad})
    l2 = c.post("/api/v1/jobs/discover", json=discover, headers={**JOBS, **bad})
    assert l1.status_code == l2.status_code == 422
    assert l1.json() == l2.json() and l1.json()["code"] == "idempotency_key_invalid"
    # the replay header is exposed to the console on every route (CORS)
    pre = c.options(
        "/api/v1/jobs/run",
        headers={"Origin": "http://console.test", "Access-Control-Request-Method": "POST"},
    )
    assert "Idempotency-Key" in pre.headers.get("access-control-allow-headers", "")


def test_run_validate_answers_what_the_run_would_without_queueing(seeded) -> None:  # noqa: ANN001
    """M2 verify r2 issue 1: the wizard asks the server before it claims "all checks passed".
    ``POST /jobs/run/validate`` runs the run's own validation and answers its code; no job row,
    no key material."""
    c, src, prod = seeded["c"], seeded["src"], seeded["prod"]
    before = c.get("/api/v1/jobs/stats", headers=JOBS).json()
    url = "/api/v1/jobs/run/validate"
    append = {
        "write_mode": "append",
        "confirm_write": True,
        "pk_strategy": {"kind": "offset", "offset": 900000000},
    }
    # append through a source-only connection: the server's target-role rule
    role = c.post(url, json=_run(src, seeded["snap_src"], append), headers=JOBS)
    assert role.status_code == 422 and role.json()["code"] == "connection_role"
    assert (
        c.post("/api/v1/jobs/run", json=_run(src, seeded["snap_src"], append), headers=JOBS).json()[
            "code"
        ]
        == "connection_role"
    )  # the same answer the run gets
    # an offset strategy with neither an offset nor a prefix: the server's model rule
    empty = {**append, "pk_strategy": {"kind": "offset"}}
    no_offset = c.post(url, json=_run(prod, seeded["snap_prod"], empty), headers=FULL)
    assert no_offset.status_code == 422
    assert "offset" in json.dumps(no_offset.json()["detail"])
    # production in place without admin, and unconfirmed: the run's codes
    confirmed = {"write_mode": "in_place", "confirm_write": True}
    no_admin = c.post(url, json=_run(prod, seeded["snap_prod"], confirmed), headers=JOBS)
    assert no_admin.status_code == 403 and no_admin.json()["code"] == "admin_scope_required"
    table = {
        "write_mode": "table",
        "connection_id": seeded["uat"],
        "object": {"owner": "bank_uat", "name": "customers"},
    }
    ok = c.post(url, json=_run(src, seeded["snap_src"], table), headers=JOBS)
    assert ok.status_code == 200 and ok.json() == {"ok": True}
    assert ok.headers["cache-control"] == "no-store"
    after = c.get("/api/v1/jobs/stats", headers=JOBS).json()
    assert after == before  # nothing was queued
    assert (
        c.post(
            url,
            json=_run(src, seeded["snap_src"], table),
            headers={"X-API-KEY": "dps_detect", "X-DPS-Tenant": "t1"},
        ).status_code
        == 403
    )  # the jobs scope, like the run
