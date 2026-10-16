"""M5 Task 8B (L3): a scan's T3 labelling figures reach ``progress.metrics.llm`` — with each
page's progress (``DbScanSink.progress``), at finalize from the crawl's ``RunOutcome.llm``, and
again from the finalize plan when a finalize is retried — and a scan that never asked for T3
records none. Counts only; against the metadata DB."""

from __future__ import annotations

from typing import Any

import pytest

from dps import jobs
from dps.layers.scanner import DbScanSink, _run_outcome, finalize_scan, open_runtime
from dps.layers.scanner.runtime import RunOutcome
from dps.layers.scanner.store import ScanStore
from dps.worker import HandlerContext
from tests.scanner.test_scan_job_e2e import TENANT, _claim, _submit, scan_ctx

pytestmark = [pytest.mark.db]
__all__ = ["scan_ctx"]  # the fixture, re-exported for this module
URL = "http://127.0.0.1:8410/profile"  # never navigated: the crawl is not run
FIGURES = {"calls": 3, "raw_values": 0, "blocked": 1}


async def test_the_crawl_figures_reach_the_progress_and_survive_a_retried_finalize(
    migrated_db: Any, scan_ctx: HandlerContext, kek: bytes
) -> None:
    job = await _submit(scan_ctx, kek, URL, max_pages=2)
    claimed = await _claim(scan_ctx)
    store = ScanStore(scan_ctx.settings.pg_schema)
    runtime = await open_runtime(scan_ctx, claimed, store)
    sink = DbScanSink(scan_ctx, claimed, store)
    assert await sink.progress({"pages": 1, "fields": 4, "blocked_requests": 2, "llm": FIGURES})
    running = await jobs.get_job(TENANT, job.id)
    assert running is not None and running.progress.metrics == {"llm": FIGURES}
    assert (running.progress.pages, running.progress.fields) == (1, 4)
    outcome = RunOutcome(pages_visited=1, llm=dict(FIGURES))
    assert await finalize_scan(
        scan_ctx, claimed, runtime, store=store, outcome=outcome, result="succeeded"
    )
    done = await jobs.get_job(TENANT, job.id)
    assert done is not None and done.progress.metrics == {"llm": FIGURES}
    # what a retried finalize reads back from the job's plan keeps the figures
    restored = _run_outcome({"pages_visited": 1, "llm": FIGURES, "unknown": 1})
    assert restored is not None and restored.llm == FIGURES


async def test_a_scan_without_t3_records_no_llm_figures(
    migrated_db: Any, scan_ctx: HandlerContext, kek: bytes
) -> None:
    job = await _submit(scan_ctx, kek, URL, max_pages=2)
    claimed = await _claim(scan_ctx)
    store = ScanStore(scan_ctx.settings.pg_schema)
    runtime = await open_runtime(scan_ctx, claimed, store)
    assert await DbScanSink(scan_ctx, claimed, store).progress({"pages": 1, "fields": 0})
    assert await finalize_scan(
        scan_ctx, claimed, runtime, store=store, outcome=RunOutcome(), result="succeeded"
    )
    done = await jobs.get_job(TENANT, job.id)
    assert done is not None and "llm" not in done.progress.metrics
