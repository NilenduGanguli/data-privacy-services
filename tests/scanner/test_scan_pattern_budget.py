"""S3 security review F-02 on the scanner's path (the M4 merge into ``fsec``): a scan whose policy
pattern runs past its CPU budget fails at once with ``custom_pattern_too_slow``.

The refusal is deterministic (each pattern run is charged its own CPU), so — as an L2 job — the scan
is never retried: it ends ``failed`` with the code, its key purged and a failed evidence pack that
names the code. The crawl stops where the refusal happened, keeps what it recorded and saves the
request counts without running a pattern again (the call's allowance is spent). Before the fix the
refusal escaped the handler: the job stayed ``running`` until its lease expired and the reaper
retried it, each attempt burning the pattern's CPU again.
"""

from __future__ import annotations

from typing import Any

import pytest

from dps import jobs
from dps.engine.detect.regex_guard import PatternTooSlow
from dps.jobs import JobKind, JobState
from dps.layers.scanner import scan_job
from dps.worker import HandlerContext
from tests.connectors.helpers import db_key_enc
from tests.scanner.test_scan_finalize import _evidence
from tests.scanner.test_scan_job_e2e import TENANT, _claim, _rows, _submit, scan_ctx

pytestmark = [pytest.mark.db]
__all__ = ["scan_ctx"]  # the fixture, re-exported for this module
URL = "http://127.0.0.1:8410/apply"  # never navigated: the browser is a stand-in


async def _no_browser(_settings: Any) -> object:
    return object()


async def _failed_with_the_code(ctx: HandlerContext, job_id: str) -> None:
    final = await jobs.get_job(TENANT, job_id)
    assert final is not None and final.state == JobState.failed
    assert final.error_code == "custom_pattern_too_slow"
    assert await db_key_enc(job_id) is None  # purged with the failure
    [ev] = await _evidence(job_id)
    assert ev["outcome"] == "failed"
    [pack] = await _rows(
        "SELECT pack->'errors' AS errors FROM {s}.dps_evidence WHERE id = $1", ev["id"]
    )
    assert {"code": "custom_pattern_too_slow", "where": "crawl"} in pack["errors"]
    assert await jobs.claim_jobs(ctx.worker_id, kinds=(JobKind.scan,), batch=1) == []  # no retry


async def test_a_crawl_a_policy_pattern_refuses_fails_the_scan_at_once(
    migrated_db: Any, scan_ctx: HandlerContext, kek: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _Context:
        def on(self, _event: str, _handler: Any) -> None:
            return None

    async def new_context(*_args: Any, **_kwargs: Any) -> _Context:
        return _Context()

    async def close_context(*_args: Any) -> None:
        return None

    async def too_slow(_self: Any, _target: Any) -> None:
        raise PatternTooSlow()

    monkeypatch.setattr("dps.layers.scanner.browser.get_browser", _no_browser)
    monkeypatch.setattr("dps.layers.scanner.crawler.new_scan_context", new_context)
    monkeypatch.setattr("dps.layers.scanner.crawler.close_scan_context", close_context)
    monkeypatch.setattr("dps.layers.scanner.crawler.ScanRunner._process", too_slow)
    job = await _submit(scan_ctx, kek, URL, max_pages=3)
    await scan_job(scan_ctx, await _claim(scan_ctx))
    await _failed_with_the_code(scan_ctx, job.id)
    # the crawl kept what it recorded: the start page, queued when the refusal stopped it
    pages = await _rows("SELECT page_no, status FROM {s}.dps_scan_page WHERE job_id = $1", job.id)
    assert [(p["page_no"], p["status"]) for p in pages] == [(1, "queued")]


async def test_a_refusal_that_escapes_the_crawl_fails_the_scan_too(
    migrated_db: Any, scan_ctx: HandlerContext, kek: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def too_slow(_self: Any) -> None:
        raise PatternTooSlow()

    monkeypatch.setattr("dps.layers.scanner.browser.get_browser", _no_browser)
    monkeypatch.setattr("dps.layers.scanner.crawler.ScanRunner.run", too_slow)
    job = await _submit(scan_ctx, kek, URL, max_pages=2)
    await scan_job(scan_ctx, await _claim(scan_ctx))
    await _failed_with_the_code(scan_ctx, job.id)
