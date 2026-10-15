"""Task 16 (end to end): a scan job from the queue to its evidence — the handler, the DB sink, the
exports, the evidence pack and the key purge — against the metadata DB and the demo site.

Every claim is counted: the demo site's ``/__received`` counters (nothing submitted), the request
log (no allowed non-GET), the stored rows, artefacts and exports (no demo value anywhere).
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
from collections.abc import AsyncIterator
from typing import Any

import pytest

from dps import db, jobs
from dps.engine.service import PrivacyEngine
from dps.engine.synth.surrogates import generate_synthesis_seed
from dps.engine.transform.crypto import generate_key_material
from dps.engine.verify import normalise
from dps.ids import new_id
from dps.jobs import ClaimedJob, Job, JobKind, JobPhase, JobState
from dps.jobs_models import JobSecret, ScanSpec
from dps.layers.scanner.classify import value_classifier
from dps.layers.scanner.models import redact_scan_spec, validate_scan
from dps.layers.scanner.urls import redact_url
from dps.secrets import seal
from dps.worker import HandlerContext
from tests.connectors.helpers import db_key_enc
from tests.scanner.helpers import DEMO_VALUE_KEYS, demo_cookie_state, received

pytestmark = [pytest.mark.db, pytest.mark.browser, pytest.mark.slow]
TENANT = "t-scan"
#: every demo value a page renders (customer data for the leak checks)
LEAK_KEYS = (*DEMO_VALUE_KEYS, "customer_id", "account_number", "application_ref", "dob")


@pytest.fixture
async def scan_ctx(
    handler_ctx: HandlerContext, scan_engine: PrivacyEngine
) -> AsyncIterator[HandlerContext]:
    """The M2 handler context with the scanner's test settings and the test FPE engine; the
    process browser is closed afterwards."""
    from dps.layers.scanner.browser import close_browser

    settings = handler_ctx.settings.model_copy(
        update={
            "env": "local",
            "scan_settle_ms": 150,
            "scan_page_timeout_seconds": 15,
            "scan_registered_hosts": "127.0.0.1,localhost,demo-site",
        }
    )
    try:
        yield dataclasses.replace(handler_ctx, settings=settings, engine=scan_engine)
    finally:
        await close_browser()


async def _submit(
    ctx: HandlerContext,
    kek: bytes,
    url: str,
    *,
    mode: str = "obfuscate",
    auth: dict[str, Any] | None = None,
    max_pages: int = 30,
    accept_as: str = TENANT,
) -> Job:
    """Create the job the way the API does (Task 18 reuses the same steps). ``accept_as``: the
    tenant the accept-time checks run for (another one only to write a row the API would refuse,
    as an old row or a direct insert could hold); the job is always the test tenant's."""
    body: dict[str, Any] = {
        "url": url,
        "mode": mode,
        "scope": {"max_pages": max_pages, "deny": ["/logout", "/admin"]},
        "fill": {"personas": 3, "locale": "en_IN"},
    }
    if auth is not None:
        body["auth"] = auth
    spec = ScanSpec.model_validate(body)
    validated = validate_scan(
        spec,
        settings=ctx.settings,
        hosts=frozenset(ctx.settings.scan_registered_hosts_list),
        tenant_id=accept_as,
    )
    detector = ctx.engine.detector(ctx.engine.policy(None), "en_IN")
    key, seed = generate_key_material("key"), generate_synthesis_seed()
    job_id = new_id("s")
    secret = JobSecret(
        key_material=key.model_dump(mode="json"),
        synthesis_seed=seed.model_dump(mode="json"),
        scan_auth=spec.auth.storage_state,
        scan_start_url=validated.start_url,
    )
    sealed = seal(kek, secret.model_dump_json().encode("utf-8"), aad=f"dps_job.key:{job_id}")
    return await jobs.create_job(
        job_id=job_id,
        tenant_id=TENANT,
        kind=JobKind.scan,
        phase=JobPhase.scan,
        spec=redact_scan_spec(
            validated, redact_url(validated.start_url, value_classifier(detector))
        ),
        mode=mode,
        sealed_secret=sealed,
        kid=key.kid,
        created_by="k-test",
    )


async def _claim(ctx: HandlerContext) -> ClaimedJob:
    for _ in range(50):
        claimed = await jobs.claim_jobs(ctx.worker_id, kinds=(JobKind.scan,), batch=1)
        if claimed:
            return claimed[0]
        await asyncio.sleep(0.1)
    raise AssertionError("no scan job to claim")


async def _rows(sql: str, *args: Any) -> list[dict[str, Any]]:
    async with db.acquire_queue(TENANT) as conn:
        return [dict(r) for r in await conn.fetch(sql.format(s=db.schema_ident()), *args)]


async def _artefact(ctx: HandlerContext, artefact_id: str) -> tuple[str, bytes]:
    row, chunks = await ctx.artefacts.open(TENANT, artefact_id)
    data = b"".join([c async for c in chunks])
    return row.content_type, data


async def test_scan_job_end_to_end(
    migrated_db: Any,
    scan_ctx: HandlerContext,
    kek: bytes,
    demo_site_url: str,
    demo_data: dict[str, str],
) -> None:
    from dps.layers.scanner import scan_job

    before = received(demo_site_url)
    job = await _submit(
        scan_ctx,
        kek,
        f"{demo_site_url}/apply",
        auth={"storage_state": demo_cookie_state("127.0.0.1")},
    )
    await scan_job(scan_ctx, await _claim(scan_ctx))
    after = received(demo_site_url)
    for k in ("non_get", "form_get", "logout_hits", "admin_hits", "by_path"):
        assert after[k] == before[k], k
    done = await jobs.get_job(TENANT, job.id)
    assert done is not None and done.state == JobState.succeeded, (done.state, done.error_code)
    assert (done.progress.pages or 0) >= 15 and done.progress.evidence_id
    assert done.progress.outcome == "succeeded"
    assert await db_key_enc(job.id) is None
    pages = await _rows("SELECT * FROM {s}.dps_scan_page WHERE job_id = $1", job.id)
    assert sum(1 for p in pages if p["status"] == "done") >= 15
    ident = next(p for p in pages if p["url_redacted"].endswith("/apply/identity"))
    variants = {"original", "annotated", "filled_synthesize", "filled_obfuscate"}
    assert variants <= set(ident["artefacts"])
    for variant in variants:
        ctype, data = await _artefact(scan_ctx, ident["artefacts"][variant])
        assert ctype == "image/png" and data.startswith(b"\x89PNG")
    requests = await _rows("SELECT * FROM {s}.dps_scan_request WHERE job_id = $1", job.id)
    assert any(r["decision"] == "blocked" and r["method"] == "POST" for r in requests)
    assert not [
        r for r in requests if r["decision"] == "allowed" and r["method"] not in ("GET", "HEAD")
    ]
    [ev] = await _rows("SELECT * FROM {s}.dps_evidence WHERE job_id = $1", job.id)
    assert ev["id"] == done.progress.evidence_id and ev["html_artefact_id"]
    pack = ev["pack"]
    assert pack["job_kind"] == "scan" and pack["outcome"] == "succeeded"
    assert pack["scan"]["submissions"] == 0 and pack["scan"]["non_get_allowed"] == 0
    assert pack["scan"]["blocked_requests"].get("POST", 0) >= 1
    assert pack["label"] == "pseudonymised — still personal data"
    ctype, html = await _artefact(scan_ctx, ev["html_artefact_id"])
    assert ctype.startswith("text/html") and b"Submissions: 0" in html
    exports = {
        a["kind"]: a["id"]
        for a in await _rows(
            "SELECT kind, id FROM {s}.dps_artefact WHERE job_id = $1 AND kind LIKE 'export_%'",
            job.id,
        )
    }
    assert set(exports) == {"export_json", "export_csv"}
    _, raw_json = await _artefact(scan_ctx, exports["export_json"])
    _, raw_csv = await _artefact(scan_ctx, exports["export_csv"])
    export = json.loads(raw_json)
    assert export["personas"] == 3 and export["display_persona"] == 1
    fields = await _rows("SELECT * FROM {s}.dps_scan_field WHERE job_id = $1", job.id)
    aadhaar = next(
        f for f in fields if f["page_no"] == ident["page_no"] and f["entity_type"] == "AADHAAR"
    )
    row = next(
        x
        for x in export["fields"]
        if x["page_no"] == aadhaar["page_no"] and x["field_no"] == aadhaar["field_no"]
    )
    assert row["values"][0]["synthesize"] == aadhaar["fill_synthesize"]
    assert row["values"][0]["obfuscate"] == aadhaar["fill_obfuscate"]
    assert row["values"][1]["synthesize"] != aadhaar["fill_synthesize"]
    # one audit row per navigation, no URL in any of them
    events = await _rows("SELECT * FROM {s}.dps_scan_event WHERE job_id = $1", job.id)
    navigations = [e for e in events if e["kind"] == "navigate"]
    audited = [e for e in scan_ctx.audit.events if e.action == "scan.navigate"]  # type: ignore[attr-defined]
    assert len(audited) == len(navigations) >= 15
    assert any(e.action == "scan.finalize" for e in scan_ctx.audit.events)  # type: ignore[attr-defined]
    # no demo value in any row, the evidence, the exports or a capture map
    captures = await _rows(
        "SELECT id FROM {s}.dps_artefact WHERE job_id = $1 AND kind = 'capture' "
        "AND content_type = 'application/json'",
        job.id,
    )
    stored = [json.dumps(r, default=str) for r in (*pages, *fields, *requests, *events)]
    stored += [json.dumps(pack), raw_json.decode(), raw_csv.decode(), html.decode()]
    stored += [(await _artefact(scan_ctx, c["id"]))[1].decode() for c in captures]
    stored += [json.dumps(done.model_dump(mode="json"))]
    haystack = normalise(" ".join(stored))
    # positive control: the haystack holds what was stored (a persona's name is in the export)
    assert normalise(export["persona_set"][0]["attributes"]["name"]) in haystack
    for k in LEAK_KEYS:
        if k in demo_data and len(normalise(demo_data[k])) >= 4:
            assert normalise(demo_data[k]) not in haystack, k


async def test_cancel_running_scan_finalizes_canceled(
    migrated_db: Any, scan_ctx: HandlerContext, kek: bytes, demo_site_url: str
) -> None:
    from dps.layers.scanner import scan_finalize_job, scan_job

    job = await _submit(
        scan_ctx,
        kek,
        f"{demo_site_url}/apply",
        mode="synthesize",
        auth={"storage_state": demo_cookie_state("127.0.0.1")},
    )
    task = asyncio.create_task(scan_job(scan_ctx, await _claim(scan_ctx)))
    for _ in range(600):
        done_rows = await _rows(
            "SELECT 1 FROM {s}.dps_scan_page WHERE job_id = $1 AND status = 'done'", job.id
        )
        if done_rows or task.done():
            break
        await asyncio.sleep(0.1)
    assert not task.done(), "the scan finished before it could be canceled"
    canceled = await jobs.cancel(TENANT, job.id)
    assert canceled is not None
    assert (canceled.phase, canceled.state) == (JobPhase.finalize, JobState.queued)
    await asyncio.wait_for(task, timeout=120)  # it returns after LeaseLost
    claimed = await _claim(scan_ctx)
    assert claimed.id == job.id and claimed.phase == JobPhase.finalize
    await scan_finalize_job(scan_ctx, claimed)
    final = await jobs.get_job(TENANT, job.id)
    assert final is not None and final.state == JobState.canceled
    assert final.progress.outcome == "canceled" and final.progress.evidence_id
    assert await db_key_enc(job.id) is None
    [ev] = await _rows("SELECT pack FROM {s}.dps_evidence WHERE job_id = $1", job.id)
    assert ev["pack"]["outcome"] == "canceled" and ev["pack"]["scan"]["non_get_allowed"] == 0
    kinds = {
        a["kind"]
        for a in await _rows("SELECT kind FROM {s}.dps_artefact WHERE job_id = $1", job.id)
    }
    assert {"export_json", "export_csv", "evidence_html"} <= kinds
    with pytest.raises(jobs.JobStateError):
        await jobs.cancel(TENANT, job.id)


async def test_a_queued_scan_is_canceled_at_once(
    migrated_db: Any, scan_ctx: HandlerContext, kek: bytes, demo_site_url: str
) -> None:
    job = await _submit(scan_ctx, kek, f"{demo_site_url}/apply")
    canceled = await jobs.cancel(TENANT, job.id)
    assert canceled is not None and canceled.state == JobState.canceled
    assert await db_key_enc(job.id) is None


async def test_missing_storage_state_secret_fails_the_job(
    migrated_db: Any, scan_ctx: HandlerContext, kek: bytes, demo_site_url: str
) -> None:
    from dps.layers.scanner import scan_job

    job = await _submit(
        scan_ctx,
        kek,
        f"{demo_site_url}/profile",
        auth={"storage_state_ref": "dps/ui/does-not-exist"},
    )
    await scan_job(scan_ctx, await _claim(scan_ctx))
    failed = await jobs.get_job(TENANT, job.id)
    assert failed is not None and failed.state == JobState.failed
    assert failed.error_code == "secret_unavailable" and failed.error == "secret_unavailable"
    assert await db_key_enc(job.id) is None


async def test_storage_state_ref_resolves_under_the_tenant_folder_only(
    migrated_db: Any, scan_ctx: HandlerContext, kek: bytes, demo_site_url: str
) -> None:
    """SEC-2/SEC-5: the browser-worker reads ``DPS_SECRET_DIR/<tenant>/<ref>/storage_state.json``
    through main's ``resolve_secret_ref(tenant_id, ref, settings)``. Another tenant's copy of the
    same reference is never read; a top-level folder of the pre-scoping layout that is not the
    tenant's makes the reference refused; a reference that names another tenant's folder is refused
    when the scan is accepted and again when the worker loads it (cross-tenant)."""
    import shutil
    from pathlib import Path

    from dps.layers.scanner import ScanSetupError, open_runtime
    from dps.layers.scanner.models import ScanSpecError
    from dps.layers.scanner.store import ScanStore

    root = Path(scan_ctx.settings.secret_dir)
    state = json.dumps(demo_cookie_state("127.0.0.1"))
    store = ScanStore(scan_ctx.settings.pg_schema)

    def put(rel: str) -> None:
        folder = root / rel
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "storage_state.json").write_text(state, encoding="utf-8")

    async def refused(claimed: ClaimedJob) -> str:
        with pytest.raises(ScanSetupError) as err:
            await open_runtime(scan_ctx, claimed, store)
        return str(err.value)

    put("t-other/dps/ui/demo")
    job = await _submit(
        scan_ctx, kek, f"{demo_site_url}/profile", auth={"storage_state_ref": "dps/ui/demo"}
    )
    claimed = await _claim(scan_ctx)
    assert claimed.id == job.id
    assert await refused(claimed) == "secret_unavailable"  # only another tenant has it
    put("dps/ui/demo")  # the pre-scoping layout: a top-level folder named like the first segment
    put(f"{TENANT}/dps/ui/demo")
    assert await refused(claimed) == "secret_unavailable"
    shutil.rmtree(root / "dps")
    runtime = await open_runtime(scan_ctx, claimed, store)
    assert runtime.storage_state == demo_cookie_state("127.0.0.1")
    assert runtime.start_url.endswith("/profile")
    assert "demo-valid" not in repr(runtime)

    foreign = {"storage_state_ref": "t-other/dps/ui/demo"}
    with pytest.raises(ScanSpecError) as err:  # what POST /scans answers for the test tenant
        await _submit(scan_ctx, kek, f"{demo_site_url}/profile", auth=foreign)
    assert (err.value.code, err.value.status) == ("secret_ref_foreign_tenant", 403)
    other = await _submit(
        scan_ctx, kek, f"{demo_site_url}/profile", auth=foreign, accept_as="t-other"
    )
    claimed_other = await _claim(scan_ctx)
    assert claimed_other.id == other.id and claimed_other.tenant_id == TENANT
    assert await refused(claimed_other) == "secret_unavailable"
