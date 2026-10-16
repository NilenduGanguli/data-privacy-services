"""Policy store (M5 Task 15): tenant policies, drafts, immutable published versions, diffs and
``@name[:vN]`` references resolved at accept time — per tenant only, never a customer value."""

from __future__ import annotations

import json
from typing import Any

import asyncpg
import pytest

from dps import db
from dps.config import Settings
from dps.engine.policy import load_preset
from dps.policy_store import (
    PolicyStoreError,
    create_policy,
    get_policy,
    publish,
    resolve_ref,
    save_draft,
)
from tests.api.test_api_jobs import _run, seeded  # noqa: F401 - the fixture is used by name

pytestmark = pytest.mark.db
FULL_T1 = {"X-API-KEY": "dps_full", "X-DPS-Tenant": "t1"}
FULL_T2 = {"X-API-KEY": "dps_full", "X-DPS-Tenant": "t2"}
JOBS_T1 = {"X-API-KEY": "dps_jobs", "X-DPS-Tenant": "t1"}
NAME = "gdpr-pseudonymise"
TEXT = "Contact jane.doe@example.com about SSN 123-45-6789."


def _tuned(min_score: float = 0.55, **extra: Any) -> dict[str, Any]:
    body = load_preset(NAME).model_dump(mode="json")
    body["detect"]["min_score"] = min_score
    body.update(extra)
    return body


async def _owner(settings: Settings, tenant: str) -> asyncpg.Connection:
    conn = await db.open_migration_connection(settings)
    await conn.execute("SELECT set_config('app.tenant_id', $1, false)", tenant)
    return conn


async def test_adopting_a_preset_publishes_v1_with_the_preset_hash(migrated_db: Settings) -> None:
    detail = await create_policy("t1", NAME, NAME, actor="tester")
    assert detail.active_version == 1
    assert detail.active_hash == load_preset(NAME).config_hash()
    assert detail.versions[0].note == "shipped preset" and detail.versions[0].version == 1
    assert detail.draft is False and detail.base_preset == NAME
    with pytest.raises(PolicyStoreError) as err:
        await create_policy("t1", NAME, NAME, actor="tester")
    assert err.value.status == 409 and err.value.code == "policy_exists"


async def test_draft_publish_and_versions_are_immutable(migrated_db: Settings) -> None:
    await create_policy("t1", NAME, NAME, actor="tester")
    draft = await save_draft("t1", NAME, _tuned(), actor="editor")
    assert draft.draft is True and draft.draft_changes == 1 and draft.draft_by == "editor"
    version = await publish("t1", NAME, change_ticket="CHG-47950", note="tighter", actor="approver")
    assert version.version == 2 and version.change_ticket == "CHG-47950"
    after = await get_policy("t1", NAME)
    assert after.active_version == 2 and after.draft is False and after.draft_body is None
    assert after.active_hash == version.hash and [v.version for v in after.versions] == [2, 1]
    with pytest.raises(PolicyStoreError) as err:
        await publish("t1", NAME, change_ticket=None, note=None, actor="approver")
    assert err.value.status == 409 and err.value.code == "no_draft"
    owner = await _owner(migrated_db, "t1")
    s = db.schema_ident(migrated_db)
    try:
        with pytest.raises(asyncpg.RaiseError, match="immutable"):
            await owner.execute(f"UPDATE {s}.dps_policy_version SET note = 'x'")
        with pytest.raises(asyncpg.RaiseError, match="immutable"):
            await owner.execute(f"DELETE FROM {s}.dps_policy_version")
    finally:
        await owner.close()
    async with db.acquire("t1") as conn:  # the app role may not even try
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.execute(f"UPDATE {s}.dps_policy_version SET note = 'x'")


async def test_invalid_draft_is_refused_with_field_names_only(migrated_db: Settings) -> None:
    await create_policy("t1", NAME, NAME, actor="tester")
    body = _tuned()
    body["treat"]["SSN"] = {"method": "teleport"}
    with pytest.raises(PolicyStoreError) as err:
        await save_draft("t1", NAME, body, actor="editor")
    assert err.value.status == 400 and err.value.code == "invalid_policy"
    assert "treat.SSN.method" in err.value.detail and "unknown method" in err.value.detail
    assert "teleport" not in err.value.detail  # S1: the field and rule, never the value
    assert (await get_policy("t1", NAME)).draft is False


async def test_reserved_and_bad_names(migrated_db: Settings) -> None:
    for name in ("presets", "Bad", "x", "validate", "a" * 70):
        with pytest.raises(PolicyStoreError) as err:
            await create_policy("t1", name, NAME, actor="tester")
        assert err.value.status == 422 and err.value.code == "invalid_name"
    with pytest.raises(PolicyStoreError) as err:
        await create_policy("t1", "retail", "no-such-preset", actor="tester")
    assert err.value.status == 422 and err.value.code == "invalid_preset"
    with pytest.raises(PolicyStoreError) as missing:
        await get_policy("t1", "retail")
    assert missing.value.status == 404 and missing.value.code == "policy_not_found"


async def test_resolve_ref_grammar_and_states(migrated_db: Settings) -> None:
    assert await resolve_ref(None, "t1") is None and await resolve_ref(NAME, "t1") == NAME
    inline = {"preset": NAME}
    assert await resolve_ref(inline, "t1") == inline
    await create_policy("t1", NAME, NAME, actor="tester")
    body = await resolve_ref(f"@{NAME}", "t1")
    assert isinstance(body, dict) and body["name"] == NAME and body["version"] == 1
    for bad, code, status in (
        ("@Bad", "invalid_policy_ref", 400),
        ("@x", "invalid_policy_ref", 400),
        ("@nope", "policy_not_found", 404),
        (f"@{NAME}:v9", "policy_not_found", 404),
    ):
        with pytest.raises(PolicyStoreError) as err:
            await resolve_ref(bad, "t1")
        assert (err.value.code, err.value.status) == (code, status)
    with pytest.raises(PolicyStoreError) as other:
        await resolve_ref(f"@{NAME}", "t2")  # another tenant's policy does not exist for t2
    assert other.value.code == "policy_not_found"


async def test_publish_refuses_relaxed_verification_when_disallowed(
    migrated_db: Settings,
) -> None:
    await create_policy("t1", NAME, NAME, actor="tester")
    await save_draft("t1", NAME, _tuned(verify={"leakage_check": "warn"}), actor="editor")
    strict = migrated_db.model_copy(update={"verify_warn_allowed": False})
    with pytest.raises(PolicyStoreError) as err:
        await publish("t1", NAME, change_ticket=None, note=None, actor="a", settings=strict)
    assert err.value.status == 422 and err.value.code == "verify_warn_disallowed"
    assert (await get_policy("t1", NAME)).active_version == 1
    relaxed = migrated_db.model_copy(update={"verify_warn_allowed": True})
    version = await publish("t1", NAME, change_ticket=None, note=None, actor="a", settings=relaxed)
    assert version.version == 2


# --- over HTTP ------------------------------------------------------------------------------


def _adopt_and_tune(c: Any) -> dict[str, str]:
    created = c.post("/api/v1/policies", json={"name": NAME, "base_preset": NAME}, headers=FULL_T1)
    assert created.status_code == 201, created.text
    v1_hash = created.json()["active_hash"]
    draft = c.put(f"/api/v1/policies/{NAME}/draft", json={"body": _tuned()}, headers=FULL_T1)
    assert draft.status_code == 200 and draft.json()["draft_changes"] == 1, draft.text
    published = c.post(
        f"/api/v1/policies/{NAME}/publish",
        json={"change_ticket": "CHG-47950", "note": "tighter"},
        headers=FULL_T1,
    )
    assert published.status_code == 201 and published.json()["version"] == 2, published.text
    return {"v1": v1_hash, "v2": published.json()["hash"]}


def test_at_reference_resolves_active_and_pinned_versions_over_http(
    api_client, db_settings: Settings
) -> None:  # noqa: ANN001
    c, sink = api_client
    hashes = _adopt_and_tune(c)
    assert hashes["v1"] != hashes["v2"]

    def obfuscate(policy: str) -> Any:
        return c.post("/api/v1/obfuscate", json={"text": TEXT, "policy": policy}, headers=FULL_T1)

    active = obfuscate(f"@{NAME}")
    assert active.status_code == 200, active.text
    assert active.json()["report"]["policy_hash"] == hashes["v2"]
    pinned = obfuscate(f"@{NAME}:v1")
    assert pinned.status_code == 200 and pinned.json()["report"]["policy_hash"] == hashes["v1"]
    for ref, status, code in (
        ("@nope", 404, "policy_not_found"),
        (f"@{NAME}:v9", 404, "policy_not_found"),
        ("@Not-A-Ref", 400, "invalid_policy_ref"),
    ):
        r = obfuscate(ref)
        assert (r.status_code, r.json()["code"]) == (status, code), r.text
    detected = c.post(
        "/api/v1/detect", json={"text": TEXT, "policy": f"@{NAME}:v1"}, headers=FULL_T1
    )
    assert detected.status_code == 200, detected.text
    streamed = c.post(  # NDJSON response of a JSON array, and an NDJSON request body
        "/api/v1/obfuscate",
        json={"data": [{"email": "jane.doe@example.com"}], "policy": f"@{NAME}:v1"},
        headers={**FULL_T1, "Accept": "application/x-ndjson"},
    )
    assert streamed.status_code == 200, streamed.text
    assert json.loads(streamed.text.splitlines()[0])["policy"]["hash"] == hashes["v1"]
    envelope = json.dumps({"policy": f"@{NAME}"}) + "\n" + json.dumps({"email": "a@b.example"})
    body_stream = c.post(
        "/api/v1/obfuscate",
        content=envelope + "\n",
        headers={**FULL_T1, "Content-Type": "application/x-ndjson"},
    )
    assert body_stream.status_code == 200, body_stream.text
    assert json.loads(body_stream.text.splitlines()[0])["policy"]["hash"] == hashes["v2"]
    unknown_stream = c.post(
        "/api/v1/obfuscate",
        content=json.dumps({"policy": "@nope"}) + "\n{}\n",
        headers={**FULL_T1, "Content-Type": "application/x-ndjson"},
    )
    assert (unknown_stream.status_code, unknown_stream.json()["code"]) == (404, "policy_not_found")
    # a policy row that was never published (inserted directly)
    import asyncio

    async def unpublished() -> None:
        owner = await _owner(db_settings, "t1")
        try:
            await owner.execute(
                f"INSERT INTO {db.schema_ident(db_settings)}.dps_policy (tenant_id, name) "
                "VALUES ('t1', 'retail-kyc')"
            )
        finally:
            await owner.close()

    asyncio.run(unpublished())
    r = obfuscate("@retail-kyc")
    assert (r.status_code, r.json()["code"]) == (409, "policy_not_published")
    labels = {e.policy for e in sink.events if e.mode == "obfuscate"}
    assert f"{NAME}@v2" in labels and f"{NAME}@v1" in labels
    refused = [e for e in sink.events if e.mode == "obfuscate" and e.outcome != "ok"]
    assert {e.outcome for e in refused} >= {"policy_not_found", "policy_not_published"}
    mutations = [e.action for e in sink.events if (e.action or "").startswith("admin.policy.")]
    assert mutations == ["admin.policy.create", "admin.policy.draft", "admin.policy.publish"]
    assert all("CHG-47950" not in json.dumps(e.model_dump(mode="json")) for e in sink.events)


def test_run_spec_records_the_resolved_body(seeded, db_settings: Settings) -> None:  # noqa: ANN001,F811
    c = seeded["c"]
    _adopt_and_tune(c)
    table = {
        "write_mode": "table",
        "connection_id": seeded["uat"],
        "object": {"owner": "bank_uat", "name": "customers"},
    }
    body = _run(seeded["src"], seeded["snap_src"], table, policy=f"@{NAME}")
    r = c.post("/api/v1/jobs/run", json=body, headers=JOBS_T1)
    assert r.status_code == 202, r.text
    stored = r.json()["job"]["spec"]["policy"]
    assert stored["name"] == NAME and stored["version"] == 2
    missing = c.post(
        "/api/v1/jobs/run",
        json=_run(seeded["src"], seeded["snap_src"], table, policy="@nope"),
        headers=JOBS_T1,
    )
    assert (missing.status_code, missing.json()["code"]) == (404, "policy_not_found")
    import asyncio

    async def read() -> str | None:
        owner = await _owner(db_settings, "t1")
        try:
            value = await owner.fetchval(
                f"SELECT spec->'policy'->>'version' FROM {db.schema_ident(db_settings)}.dps_job "
                "WHERE id = $1",
                r.json()["job"]["id"],
            )
            return str(value) if value is not None else None
        finally:
            await owner.close()

    assert asyncio.run(read()) == "2"

    def latest_pct() -> float:
        out = c.get("/api/v1/reports/summary", params={"days": 2}, headers=JOBS_T1)
        assert out.status_code == 200, out.text
        return float(out.json()["posture"]["policies_latest_pct"])

    assert latest_pct() == 100.0  # the one run job ran the active version
    c.put(f"/api/v1/policies/{NAME}/draft", json={"body": _tuned(0.5)}, headers=FULL_T1)
    assert c.post(f"/api/v1/policies/{NAME}/publish", headers=FULL_T1).status_code == 201
    assert latest_pct() == 0.0  # v3 is active now: the job ran an older version


def test_diff_and_yaml_routes(api_client) -> None:  # noqa: ANN001
    c, _ = api_client
    _adopt_and_tune(c)
    diff = c.get(f"/api/v1/policies/{NAME}/diff", params={"from": 1, "to": 2}, headers=FULL_T1)
    assert diff.status_code == 200, diff.text
    assert diff.json()["changes"] == [
        {"op": "change", "path": "detect.min_score", "before": 0.6, "after": 0.55}
    ]
    no_draft = c.get(
        f"/api/v1/policies/{NAME}/diff", params={"from": 1, "to": "draft"}, headers=FULL_T1
    )
    assert (no_draft.status_code, no_draft.json()["code"]) == (409, "no_draft")
    bad = c.get(f"/api/v1/policies/{NAME}/diff", params={"from": 1, "to": "x"}, headers=FULL_T1)
    assert bad.status_code == 422
    c.put(f"/api/v1/policies/{NAME}/draft", json={"body": _tuned(0.5)}, headers=FULL_T1)
    to_draft = c.get(
        f"/api/v1/policies/{NAME}/diff", params={"from": 2, "to": "draft"}, headers=FULL_T1
    )
    assert to_draft.json()["to"] == "draft" and len(to_draft.json()["changes"]) == 1
    discarded = c.delete(f"/api/v1/policies/{NAME}/draft", headers=FULL_T1)
    assert discarded.status_code == 200 and discarded.json()["draft"] is False
    yaml_doc = c.get(
        f"/api/v1/policies/{NAME}/versions/2", params={"format": "yaml"}, headers=FULL_T1
    )
    assert yaml_doc.status_code == 200 and yaml_doc.headers["content-type"].startswith("text/yaml")
    assert "attachment" in yaml_doc.headers["content-disposition"]
    assert "min_score: 0.55" in yaml_doc.text
    as_json = c.get(f"/api/v1/policies/{NAME}/versions/1", headers=FULL_T1)
    assert as_json.json()["detect"]["min_score"] == 0.6
    gone = c.get(f"/api/v1/policies/{NAME}/versions/7", headers=FULL_T1)
    assert (gone.status_code, gone.json()["code"]) == (404, "version_not_found")


def test_policies_are_tenant_isolated_and_mutations_need_admin(api_client) -> None:  # noqa: ANN001
    c, _ = api_client
    _adopt_and_tune(c)
    other = c.get(f"/api/v1/policies/{NAME}", headers=FULL_T2)
    assert (other.status_code, other.json()["code"]) == (404, "policy_not_found")
    listing = c.get("/api/v1/policies", headers=FULL_T2).json()
    assert listing["tenant"] == [] and {p["name"] for p in listing["presets"]} >= {NAME, "pci-dss"}
    mine = c.get("/api/v1/policies", headers=FULL_T1).json()
    assert [(p["name"], p["active_version"]) for p in mine["tenant"]] == [(NAME, 2)]
    preset = next(p for p in mine["presets"] if p["name"] == NAME)
    assert set(preset) == {"name", "version", "hash", "description", "mode", "reversible", "jobs"}
    denied = c.post(
        "/api/v1/policies", json={"name": "retail", "base_preset": NAME}, headers=JOBS_T1
    )
    assert denied.status_code == 403
    still = c.get("/api/v1/policies/presets", headers=JOBS_T1)  # M0's route keeps its meaning
    assert still.status_code == 200 and isinstance(still.json(), list)


async def test_job_counts_include_runs_on_the_snapshot_policy(migrated_db: Settings) -> None:
    """A run without ``spec.policy`` runs the policy its snapshot was classified under
    (``plan.policy_name``): the Policies list and the Overview "policy pack" count it (found by
    the M5 console walk: every lane job read "0 jobs")."""
    from dps import jobs
    from dps.jobs import JobKind, JobPhase
    from dps.policy_store import list_policies

    job = await jobs.create_job(
        tenant_id="t1", kind=JobKind.run, phase=JobPhase.prepare, mode="obfuscate", spec={}
    )
    async with db.acquire("t1") as conn:
        await conn.execute(
            f"UPDATE {db.schema_ident()}.dps_job SET plan = $2::jsonb WHERE id = $1",
            job.id,
            {"policy_name": NAME, "policy_hash": "h"},
        )
    presets, _ = await list_policies("t1")
    assert {p.name: p.jobs for p in presets}[NAME] == 1


def test_run_validate_resolves_policy_references_as_the_run_does(seeded) -> None:  # noqa: ANN001,F811
    """WP-I (M5 merge): main's ``POST /jobs/run/validate`` (wizard step 5) answers what
    ``POST /jobs/run`` would, so it resolves ``@name[:vN]`` the same way: a published tenant
    policy validates, an unknown one is the run's own 404."""
    c = seeded["c"]
    _adopt_and_tune(c)
    table = {
        "write_mode": "table",
        "connection_id": seeded["uat"],
        "object": {"owner": "bank_uat", "name": "customers"},
    }
    url = "/api/v1/jobs/run/validate"
    body = _run(seeded["src"], seeded["snap_src"], table, policy=f"@{NAME}")
    ok = c.post(url, json=body, headers=JOBS_T1)
    assert (ok.status_code, ok.json()) == (200, {"ok": True}), ok.text
    unknown = _run(seeded["src"], seeded["snap_src"], table, policy="@nope")
    for route in (url, "/api/v1/jobs/run"):
        r = c.post(route, json=unknown, headers=JOBS_T1)
        assert (r.status_code, r.json()["code"]) == (404, "policy_not_found"), route
