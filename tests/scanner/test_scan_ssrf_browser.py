"""S5 (security review) on the browser side: Chromium as the browser-worker launches it resolves
nothing itself — every name is resolved and checked by the scan's scope proxy — and the
browser-worker checks a scan's start URL again before any browser starts (the proxy and
``POST /scans`` proofs: ``tests/security/test_scan_ssrf.py``)."""

from __future__ import annotations

import dataclasses
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from dps.config import Settings
from dps.layers.scanner.guard import ScanGuard
from tests.scanner.test_scan_job_e2e import scan_ctx

__all__ = ["scan_ctx"]  # the fixture, re-exported for this module
DNS = {"uat.bank.test": ["127.0.0.1"], "rebind.bank.test": ["169.254.169.254"]}


async def _resolve(host: str, _port: int) -> list[str]:
    if host not in DNS:
        raise OSError("no such host")
    return DNS[host]


class _Seen:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.paths: list[str] = []


@pytest.fixture
def counting_server() -> Iterator[tuple[int, _Seen]]:
    seen = _Seen()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: Any) -> None:
            return

        def do_GET(self) -> None:  # noqa: N802
            with seen.lock:
                seen.paths.append(self.path)
            body = b"<!doctype html><title>ok</title><p>ok</p>"
            self.send_response(200)
            self.send_header("content-type", "text/html")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield int(server.server_address[1]), seen
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.browser
async def test_the_production_launch_reaches_nothing_but_through_the_proxy(
    counting_server: tuple[int, _Seen], scan_settings: Settings
) -> None:
    """Chromium as the browser-worker launches it: a context without the scope proxy reaches no
    name and no address (only the proxy's own); a scan context reaches a registered name through
    the proxy, which resolves and checks it, and never a name that resolves to a metadata address."""
    pw = pytest.importorskip("playwright.async_api")
    from dps.layers.scanner.browser import close_scan_context, launch_args, new_scan_context

    port, seen = counting_server

    async def goto(ctx: Any, url: str) -> str:
        page = await ctx.new_page()
        try:
            response = await page.goto(url, timeout=8000)
            return f"ok {response.status if response else 0}"
        except pw.Error as exc:
            return str(exc).split("\n")[0].split(" at ")[0]
        finally:
            await page.close()

    async with pw.async_playwright() as p:
        try:
            browser = await p.chromium.launch(headless=True, args=launch_args())
        except pw.Error:
            pytest.skip("run: python -m playwright install chromium")
        try:
            plain = await browser.new_context()
            direct = [
                await goto(plain, f"http://localhost:{port}/direct-name"),
                await goto(plain, f"http://[::1]:{port}/direct-v6"),
            ]
            await plain.close()
            guard = ScanGuard(
                host_allowed=lambda h: h in ("localhost", "rebind.bank.test"),
                resolve=lambda h, p_: _resolve("uat.bank.test" if h == "localhost" else h, p_),
            )
            ctx = await new_scan_context(
                browser, scan_settings, storage_state=None, locale="en_IN", guard=guard
            )
            try:
                proxied = await goto(ctx, f"http://localhost:{port}/via-proxy")
                metadata = await goto(ctx, f"http://rebind.bank.test:{port}/metadata")
                counted = guard.drain_counts()
            finally:
                await close_scan_context(ctx, guard)
        finally:
            await browser.close()
    assert all("ERR_NAME_NOT_RESOLVED" in d for d in direct), direct
    assert proxied == "ok 200" and seen.paths == ["/via-proxy"]  # the proxy resolved the name
    assert metadata == "ok 403"  # the proxy's refusal, never the metadata address
    assert ("host_forbidden", "blocked") in {(c.reason, c.decision) for c in counted}


@pytest.mark.db
async def test_the_browser_worker_checks_the_start_url_again_before_any_browser(
    migrated_db: Any, scan_ctx: Any, kek: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A scan accepted while its host resolved to an internal address, whose name now resolves
    to the metadata address (DNS rebinding between accept and run): the browser-worker refuses it
    before any browser starts — failed with ``host_forbidden``, its key purged, no retry."""
    from dps import jobs
    from dps.jobs import JobKind, JobState
    from dps.layers.scanner import scan_job
    from tests.connectors.helpers import db_key_enc
    from tests.scanner.test_scan_job_e2e import TENANT, _claim, _submit

    async def no_browser(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("a browser was started for a refused scan")

    hosts = f"{scan_ctx.settings.scan_registered_hosts},uat.bank.test"
    ctx = dataclasses.replace(
        scan_ctx, settings=scan_ctx.settings.model_copy(update={"scan_registered_hosts": hosts})
    )
    job = await _submit(ctx, kek, "https://uat.bank.test/apply", max_pages=2)
    monkeypatch.setattr("dps.netguard.default_resolver", lambda h: ["169.254.169.254"])
    monkeypatch.setattr("dps.layers.scanner.browser.get_browser", no_browser)
    await scan_job(ctx, await _claim(ctx))
    final = await jobs.get_job(TENANT, job.id)
    assert final is not None and final.state == JobState.failed
    assert final.error_code == "host_forbidden"
    assert await db_key_enc(job.id) is None
    assert await jobs.claim_jobs(ctx.worker_id, kinds=(JobKind.scan,), batch=1) == []
