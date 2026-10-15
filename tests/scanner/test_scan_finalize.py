"""Task 16 (review B1): a scan's finalize is fenced and idempotent.

The finalize moves the job to phase ``finalize`` through the lease fence before it writes anything,
so a cancel either wins (the worker writes nothing; the cancel-finalize writes the only pack) or is
refused with ``job_finalizing``. A finalize interrupted after any write is retried (reaper requeue)
into exactly one pack whose HTML artefact exists and one export pair; the audit records the outcome
the job actually ends with. Every claim is a count over the metadata DB.
"""

from __future__ import annotations

from typing import Any

import pytest

from dps import db, jobs
from dps.jobs import JobPhase, JobState
from dps.layers.scanner import finalize_scan, open_runtime, scan_finalize_job, scan_job
from dps.layers.scanner.models import RequestCount
from dps.layers.scanner.runtime import RunOutcome
from dps.layers.scanner.store import ScanStore
from dps.worker import HandlerContext
from tests.connectors.helpers import db_key_enc
from tests.scanner.test_scan_job_e2e import TENANT, _claim, _rows, _submit, scan_ctx

pytestmark = [pytest.mark.db]
__all__ = ["scan_ctx"]  # the fixture, re-exported for this module
URL = "http://127.0.0.1:8410/profile"  # never navigated: these tests start after the crawl


async def _evidence(job_id: str) -> list[dict[str, Any]]:
    return await _rows(
        "SELECT e.id, e.pack->>'outcome' AS outcome, e.html_artefact_id, a.id AS art, "
        "e.pack->'scan'->>'fills_skipped' AS fills_skipped "
        "FROM {s}.dps_evidence e LEFT JOIN {s}.dps_artefact a ON a.id = e.html_artefact_id "
        "WHERE e.job_id = $1 ORDER BY e.created_at",
        job_id,
    )


async def _kinds(job_id: str) -> dict[str, int]:
    rows = await _rows(
        "SELECT kind, count(*) AS n FROM {s}.dps_artefact WHERE job_id = $1 GROUP BY kind",
        job_id,
    )
    return {r["kind"]: int(r["n"]) for r in rows}


def _finalize_audits(ctx: HandlerContext) -> list[str]:
    return [e.outcome for e in ctx.audit.events if e.action == "scan.finalize"]  # type: ignore[attr-defined]


async def _expire_and_reap(job_id: str) -> None:
    """The worker died: its lease expires and the reaper requeues the row (same phase)."""
    async with db.acquire_queue(None) as conn:
        await conn.execute(
            f"UPDATE {db.schema_ident()}.dps_job SET lease_expires_at = now() - interval '1 second' "
            "WHERE id = $1",
            job_id,
        )
    await jobs.reap(backoff_base=0.0, backoff_cap=0.0)


async def test_cancel_before_the_finalize_fence_writes_nothing(
    migrated_db: Any, scan_ctx: HandlerContext, kek: bytes
) -> None:
    """The crawl is done, the user cancels, the worker finalizes with its old claim: it writes
    nothing, and the cancel-finalize writes the job's only pack and export pair."""
    job = await _submit(scan_ctx, kek, URL, max_pages=2)
    claimed = await _claim(scan_ctx)
    store = ScanStore(scan_ctx.settings.pg_schema)
    runtime = await open_runtime(scan_ctx, claimed, store)
    await jobs.cancel(TENANT, job.id)
    result = await finalize_scan(
        scan_ctx, claimed, runtime, store=store, outcome=RunOutcome(), result="succeeded"
    )
    assert result is None
    assert await _evidence(job.id) == [] and await _kinds(job.id) == {}
    assert _finalize_audits(scan_ctx) == []
    assert await db_key_enc(job.id) is not None  # the cancel-finalize purges it

    fin = await _claim(scan_ctx)
    assert fin.phase == JobPhase.finalize
    await scan_finalize_job(scan_ctx, fin)
    final = await jobs.get_job(TENANT, job.id)
    assert final is not None and final.state == JobState.canceled
    [ev] = await _evidence(job.id)
    assert ev["outcome"] == "canceled" and ev["art"] is not None
    assert final.progress.evidence_id == ev["id"]
    assert await _kinds(job.id) == {"export_json": 1, "export_csv": 1, "evidence_html": 1}
    assert _finalize_audits(scan_ctx) == ["canceled"]
    assert await db_key_enc(job.id) is None


async def test_a_cancel_during_finalize_is_refused(
    migrated_db: Any, scan_ctx: HandlerContext, kek: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Once the finalize has started (the first artefact is being written), a cancel answers
    ``job_finalizing`` and the job ends with the one pack it was writing."""
    job = await _submit(scan_ctx, kek, URL, max_pages=2)
    claimed = await _claim(scan_ctx)
    store = ScanStore(scan_ctx.settings.pg_schema)
    runtime = await open_runtime(scan_ctx, claimed, store)
    refusals: list[str] = []
    real_save = scan_ctx.artefacts.save

    async def save_and_cancel(**kwargs: Any) -> str:
        if not refusals:
            try:
                await jobs.cancel(TENANT, job.id)
                refusals.append("accepted")
            except jobs.JobStateError as exc:
                refusals.append(exc.code)
        return await real_save(**kwargs)

    monkeypatch.setattr(scan_ctx.artefacts, "save", save_and_cancel)
    evidence_id = await finalize_scan(
        scan_ctx, claimed, runtime, store=store, outcome=RunOutcome(), result="succeeded"
    )
    assert refusals == ["job_finalizing"]
    final = await jobs.get_job(TENANT, job.id)
    assert final is not None and final.state == JobState.succeeded
    [ev] = await _evidence(job.id)
    assert ev["id"] == evidence_id == final.progress.evidence_id and ev["outcome"] == "succeeded"
    assert await _kinds(job.id) == {"export_json": 1, "export_csv": 1, "evidence_html": 1}
    assert _finalize_audits(scan_ctx) == ["succeeded"]
    assert await db_key_enc(job.id) is None


async def test_a_crash_after_the_evidence_insert_is_retried_into_the_same_pack(
    migrated_db: Any, scan_ctx: HandlerContext, kek: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The terminal transition fails after the pack is stored; the reaped retry reuses the pack
    (its HTML artefact intact) and ends the job with the pack's outcome."""
    job = await _submit(scan_ctx, kek, URL, max_pages=2)
    claimed = await _claim(scan_ctx)
    store = ScanStore(scan_ctx.settings.pg_schema)
    runtime = await open_runtime(scan_ctx, claimed, store)
    real = jobs.advance_job

    async def boom(*a: Any, **k: Any) -> bool:
        if k.get("purge_secret"):
            raise ConnectionError("simulated crash")
        return await real(*a, **k)

    monkeypatch.setattr(jobs, "advance_job", boom)
    with pytest.raises(ConnectionError):
        await finalize_scan(
            scan_ctx, claimed, runtime, store=store, outcome=RunOutcome(), result="succeeded"
        )
    monkeypatch.setattr(jobs, "advance_job", real)
    [first] = await _evidence(job.id)
    assert _finalize_audits(scan_ctx) == []  # no audit for an outcome the job does not have yet

    await _expire_and_reap(job.id)
    retry = await _claim(scan_ctx)
    assert retry.id == job.id and retry.phase == JobPhase.finalize
    await scan_finalize_job(scan_ctx, retry)
    final = await jobs.get_job(TENANT, job.id)
    assert final is not None and final.state == JobState.succeeded
    [ev] = await _evidence(job.id)
    assert ev["id"] == first["id"] == final.progress.evidence_id
    assert ev["outcome"] == "succeeded" and ev["art"] is not None
    assert await _kinds(job.id) == {"export_json": 1, "export_csv": 1, "evidence_html": 1}
    assert _finalize_audits(scan_ctx) == ["succeeded"]
    assert await db_key_enc(job.id) is None


async def test_a_crash_before_the_evidence_insert_is_retried_with_the_crawl_outcome(
    migrated_db: Any, scan_ctx: HandlerContext, kek: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exports and the HTML are written, the pack insert fails: the retry writes one pack (with the
    crawl's counters, kept on the job row) and removes the crashed attempt's artefacts."""
    job = await _submit(scan_ctx, kek, URL, max_pages=2)
    claimed = await _claim(scan_ctx)
    store = ScanStore(scan_ctx.settings.pg_schema)
    runtime = await open_runtime(scan_ctx, claimed, store)

    async def fail_insert(*a: Any, **k: Any) -> str:
        raise ConnectionError("simulated crash")

    monkeypatch.setattr(ScanStore, "insert_evidence", fail_insert)
    with pytest.raises(ConnectionError):
        await finalize_scan(
            scan_ctx,
            claimed,
            runtime,
            store=store,
            outcome=RunOutcome(fills_skipped=2),
            result="succeeded",
        )
    monkeypatch.undo()
    assert await _evidence(job.id) == []
    assert await _kinds(job.id) == {"export_json": 1, "export_csv": 1, "evidence_html": 1}

    await _expire_and_reap(job.id)
    retry = await _claim(scan_ctx)
    assert retry.phase == JobPhase.finalize
    await scan_finalize_job(scan_ctx, retry)
    final = await jobs.get_job(TENANT, job.id)
    assert final is not None and final.state == JobState.succeeded
    [ev] = await _evidence(job.id)
    assert ev["outcome"] == "succeeded" and ev["art"] is not None and ev["fills_skipped"] == "2"
    assert await _kinds(job.id) == {"export_json": 1, "export_csv": 1, "evidence_html": 1}
    assert _finalize_audits(scan_ctx) == ["succeeded"]


async def test_a_scan_retry_never_deletes_what_a_pack_references(
    migrated_db: Any, scan_ctx: HandlerContext, kek: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A scan-phase attempt of a job that already has a pack (a row from before the fence) keeps
    the pack's artefacts and finalizes with it instead of crawling again."""
    job = await _submit(scan_ctx, kek, URL, max_pages=2)
    claimed = await _claim(scan_ctx)
    store = ScanStore(scan_ctx.settings.pg_schema)
    runtime = await open_runtime(scan_ctx, claimed, store)
    real = jobs.advance_job

    async def boom(*a: Any, **k: Any) -> bool:
        if k.get("purge_secret"):
            raise ConnectionError("simulated crash")
        return await real(*a, **k)

    monkeypatch.setattr(jobs, "advance_job", boom)
    with pytest.raises(ConnectionError):
        await finalize_scan(
            scan_ctx, claimed, runtime, store=store, outcome=RunOutcome(), result="succeeded"
        )
    monkeypatch.setattr(jobs, "advance_job", real)
    async with db.acquire_queue(None) as conn:  # the row as an older build left it
        await conn.execute(
            f"UPDATE {db.schema_ident()}.dps_job SET phase = 'scan', state = 'queued', "
            "locked_by = NULL, lease_expires_at = NULL, run_after = now() WHERE id = $1",
            job.id,
        )
    again = await _claim(scan_ctx)
    assert again.phase == JobPhase.scan
    await scan_job(scan_ctx, again)
    final = await jobs.get_job(TENANT, job.id)
    assert final is not None and final.state == JobState.succeeded
    [ev] = await _evidence(job.id)
    assert ev["art"] is not None and final.progress.evidence_id == ev["id"]
    assert await _kinds(job.id) == {"export_json": 1, "export_csv": 1, "evidence_html": 1}


async def test_an_allowed_non_get_fails_the_scan_with_submission_detected(
    migrated_db: Any, scan_ctx: HandlerContext, kek: bytes
) -> None:
    """Review N2: a non-GET the guard allowed (unreachable by construction) is measured and fails
    the scan with ``submission_detected``; the key is still purged."""
    job = await _submit(scan_ctx, kek, URL, max_pages=2)
    claimed = await _claim(scan_ctx)
    store = ScanStore(scan_ctx.settings.pg_schema)
    runtime = await open_runtime(scan_ctx, claimed, store)
    async with db.acquire_queue(TENANT) as conn:
        await store.add_request_counts(
            conn,
            TENANT,
            job.id,
            [
                RequestCount(
                    page_no=1,
                    method="POST",
                    resource_type="document",
                    decision="allowed",
                    reason="ok",
                    host="127.0.0.1",
                    count=1,
                )
            ],
        )
    await finalize_scan(
        scan_ctx, claimed, runtime, store=store, outcome=RunOutcome(), result="succeeded"
    )
    final = await jobs.get_job(TENANT, job.id)
    assert final is not None and final.state == JobState.failed
    assert final.error_code == "submission_detected"
    [pack] = await _rows(
        "SELECT pack->'scan'->>'non_get_allowed' AS n, pack->>'outcome' AS o "
        "FROM {s}.dps_evidence WHERE job_id = $1",
        job.id,
    )
    assert pack == {"n": "1", "o": "failed"}
    assert _finalize_audits(scan_ctx) == ["failed"]
    assert await db_key_enc(job.id) is None
