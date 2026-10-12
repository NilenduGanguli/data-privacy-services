"""The request guard (decision D4): the pure policy, the guard's source, and a browser proof
against a local server that counts everything it receives."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx
import pytest

pytest.importorskip("playwright")
from dps.layers.scanner.guard import (  # noqa: E402
    ALLOWED_METHODS,
    HARDEN_JS,
    BlockedRequest,
    ScanGuard,
    decide,
    stored_host,
    stored_method,
)

ROOT = Path(__file__).resolve().parents[2]
BASE: dict[str, Any] = dict(
    is_navigation=False, page_filled=False, host_allowed=True, needles=frozenset()
)


# --- the policy as a pure function ---------------------------------------------------------
@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE", "OPTIONS", "post", "CONNECT"])
def test_every_non_get_method_is_blocked_filled_or_not(method: str) -> None:
    for filled in (False, True):
        d = decide(
            method=method,
            url="http://demo-site:8410/api/autosave",
            **{**BASE, "page_filled": filled},
        )
        assert d.allow is False and d.reason == "method"


def test_get_and_head_pass_on_unfilled_pages() -> None:
    assert ALLOWED_METHODS == {"GET", "HEAD"}
    for m in ("GET", "HEAD", "get"):
        assert decide(method=m, url="http://demo-site:8410/apply", **BASE).allow


def test_unregistered_hosts_and_other_schemes_are_blocked() -> None:
    blocked = decide(
        method="GET", url="http://tracker.invalid/p.gif", **{**BASE, "host_allowed": False}
    )
    assert blocked.reason == "host"
    assert decide(method="GET", url="ftp://demo-site/x", **BASE).reason == "scheme"


def test_filled_pages_never_navigate() -> None:
    d = decide(
        method="GET",
        url="http://demo-site:8410/help/track/result?email=x",
        **{**BASE, "page_filled": True, "is_navigation": True},
    )
    assert (d.allow, d.reason) == (False, "filled_navigation")


def test_filled_pages_never_send_a_fill_value_in_a_url() -> None:
    needles = frozenset({"917322064418"})
    d = decide(
        method="GET",
        url="http://demo-site:8410/api/lookup?aadhaar=9173%202206%204418",
        **{**BASE, "page_filled": True, "needles": needles},
    )
    assert (d.allow, d.reason) == (False, "filled_value")
    ok = decide(
        method="GET",
        url="http://demo-site:8410/static/app.css",
        resource_type="stylesheet",
        **{**BASE, "page_filled": True, "needles": needles},
    )
    assert ok.allow


def test_harden_js_neutralises_programmatic_submission() -> None:
    for needle in (
        "HTMLFormElement.prototype",
        "'submit'",
        "'requestSubmit'",
        "preventDefault",
        "__dpsGuard",
    ):
        assert needle in HARDEN_JS


# --- additions beyond the plan (see M4-DEVIATIONS, Task 6) ---------------------------------
def test_a_method_override_is_a_method() -> None:
    url = "http://demo-site:8410/api/profile"
    assert decide(method="GET", url=url, method_override=True, **BASE).reason == "method"
    for query in ("?_method=POST", "?_METHOD=delete&x=1"):
        assert decide(method="GET", url=url + query, **BASE).reason == "method"


def test_a_value_encoded_more_than_once_is_still_found() -> None:
    needles = frozenset({"917322064418", "meeravenkatesan"})
    for url in (
        "http://demo-site:8410/a?q=9173%25202206%25204418",  # encoded twice
        "http://demo-site:8410/a?q=9173%2525202206%252520%25204418",  # three times
        "http://demo-site:8410/a?n=Meera+Venkatesan",
        "http://demo-site:8410/a/MEERA-VENKATESAN",
    ):
        d = decide(method="GET", url=url, **{**BASE, "page_filled": True, "needles": needles})
        assert (d.allow, d.reason) == (False, "filled_value"), url


def test_fill_values_are_checked_on_every_page_once_armed() -> None:
    """A draft the page kept (local storage) can resurface on a later, unfilled page."""
    d = decide(
        method="GET",
        url="http://demo-site:8410/apply/address?draft=9173-2206-4418",
        is_navigation=True,
        page_filled=False,
        host_allowed=True,
        needles=frozenset({"917322064418"}),
    )
    assert (d.allow, d.reason) == (False, "filled_value")


@pytest.mark.parametrize(
    "resource_type", ["xhr", "fetch", "eventsource", "websocket", "other", None]
)
def test_filled_pages_make_no_script_requests_at_all(resource_type: str | None) -> None:
    """A GET fetch can carry a fill value in a header or encoded beyond recognition."""
    d = decide(
        method="GET",
        url="http://demo-site:8410/api/check",
        resource_type=resource_type,
        **{**BASE, "page_filled": True},
    )
    assert (d.allow, d.reason) == (False, "filled_request")
    assert decide(
        method="GET", url="http://demo-site:8410/api/check", resource_type=resource_type, **BASE
    ).allow


@pytest.mark.parametrize("resource_type", ["stylesheet", "image", "font", "script", "media"])
def test_filled_pages_still_load_passive_resources(resource_type: str) -> None:
    d = decide(
        method="GET",
        url="http://demo-site:8410/static/x",
        resource_type=resource_type,
        **{**BASE, "page_filled": True, "needles": frozenset({"917322064418"})},
    )
    assert d.allow


def test_stored_host_and_method_are_value_free() -> None:
    assert stored_host("demo-site", allowed=True) == "demo-site"
    assert stored_host("tracker.invalid", allowed=False) == "tracker.invalid"
    assert stored_host("ananya.krishnan.tracker.invalid", allowed=False) == "*.tracker.invalid"
    for host in ("x.9173.invalid", "10.0.0.5", "a_b.c", "x.ananya2.in"):
        assert stored_host(host, allowed=False) == "{host}", host
    assert stored_host("917322064418.x.invalid", allowed=False) == "*.x.invalid"
    assert stored_host("", allowed=False) == "-"
    assert stored_method("post") == "POST" and stored_method("BQPKR4432L") == "OTHER"


def test_a_blocked_request_never_shows_its_url() -> None:
    b = BlockedRequest(3, "POST", "fetch", "method", "http://demo-site:8410/a?pan=BQPKR4432L")
    assert "BQPKR4432L" not in repr(b) and "pan" not in repr(b)


def test_the_guard_source_has_one_continue_and_the_fixed_method_set() -> None:
    src = (ROOT / "dps/layers/scanner/guard.py").read_text(encoding="utf-8")
    assert src.count("route.continue_(") == 1
    assert 'ALLOWED_METHODS: frozenset[str] = frozenset({"GET", "HEAD"})' in src
    assert ".click(" not in src and ".press(" not in src and ".dispatch_event(" not in src


# --- browser proof against a server that counts what it receives --------------------------
class _Counts:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.non_get: list[str] = []
        self.gets: list[str] = []
        self.upgrades = 0
        self.hosts: set[str] = set()


_PAGE = """<!doctype html><html><head><meta charset="utf-8"><link rel="stylesheet" href="/s.css"></head>
<body><form id="post" method="post" action="/post-form"><input name="v" value="x">
<button type="submit">Go</button></form>
<form id="get" method="get" action="/get-form"><input name="q" value="y"></form>
<a id="nav" href="/next">next</a></body></html>"""

_WORKER = (
    "onmessage = () => { fetch('/from-worker', {method: 'POST', body: 'x'}).catch(() => 0); };"
)


def _handler(counts: _Counts) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: Any) -> None:  # quiet
            return

        def _count_host(self) -> None:
            with counts.lock:
                counts.hosts.add((self.headers.get("host") or "").split(":")[0])

        def do_GET(self) -> None:  # noqa: N802
            self._count_host()
            with counts.lock:
                if self.headers.get("upgrade"):
                    counts.upgrades += 1
                if self.path != "/__counts":
                    counts.gets.append(self.path)
            if self.path.startswith("/page"):
                body, ctype = _PAGE.encode(), "text/html; charset=utf-8"
            elif self.path == "/worker.js":
                body, ctype = _WORKER.encode(), "text/javascript"
            elif self.path == "/s.css":
                body, ctype = b"body{font:14px sans-serif}", "text/css"
            else:
                body, ctype = b"ok", "text/plain"
            self.send_response(200)
            self.send_header("content-type", ctype)
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _other(self) -> None:
            self._count_host()
            with counts.lock:
                counts.non_get.append(f"{self.command} {self.path}")
            self.send_response(204)
            self.end_headers()

        do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = _other  # noqa: N815

    return Handler


@pytest.fixture
def counting_server() -> Iterator[tuple[str, _Counts]]:
    counts = _Counts()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(counts))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", counts
    finally:
        server.shutdown()
        server.server_close()


async def _attempt(page: Any, js: str) -> None:
    try:
        await page.evaluate(js)
    except Exception:  # noqa: BLE001 - an aborted navigation may destroy the context
        pass
    await page.wait_for_timeout(200)


@pytest.mark.browser
async def test_positive_control_the_server_counts_what_it_receives(
    counting_server: tuple[str, _Counts],
) -> None:
    url, counts = counting_server
    httpx.post(f"{url}/control", content=b"x")
    httpx.get(f"{url}/control-get")
    assert counts.non_get == ["POST /control"] and "/control-get" in counts.gets


@pytest.mark.browser
async def test_nothing_but_get_leaves_an_unfilled_page(
    chromium: Any, counting_server: tuple[str, _Counts]
) -> None:
    url, counts = counting_server
    guard = ScanGuard(host_allowed=lambda h: h == "127.0.0.1")
    ctx = await chromium.new_context(service_workers="block")
    await guard.install(ctx, harden_js=False)
    page = await ctx.new_page()
    guard.bind(page, 1)
    await page.goto(f"{url}/page")
    for js in (
        "fetch('/fetch-post', {method: 'POST', body: 'x'}).catch(() => 0)",
        "fetch('/fetch-override', {headers: {'X-HTTP-Method-Override': 'PUT'}}).catch(() => 0)",
        "(() => { const x = new XMLHttpRequest(); x.open('PUT', '/xhr-put'); x.send('x'); })()",
        "navigator.sendBeacon('/beacon', 'x')",
        "new Worker('/worker.js').postMessage('go')",
        "(() => { try { new WebSocket(location.origin.replace('http', 'ws') + '/ws'); } catch (e) {} })()",
        f"fetch('{url.replace('127.0.0.1', 'localhost')}/other-host').catch(() => 0)",
        "document.getElementById('post').requestSubmit()",
    ):
        await _attempt(page, js)
    await page.wait_for_timeout(400)
    counted = guard.drain_counts()
    await ctx.close()
    assert counts.non_get == [] and counts.upgrades == 0
    assert "localhost" not in counts.hosts
    assert not any(p.startswith(("/fetch-override", "/from-worker")) for p in counts.gets)
    blocked = {(c.method, c.reason) for c in counted if c.decision == "blocked"}
    assert {("POST", "method"), ("PUT", "method"), ("GET", "method"), ("GET", "host")} <= blocked
    assert guard.websockets_closed >= 1
    assert all(c.method in ALLOWED_METHODS for c in counted if c.decision == "allowed")


@pytest.mark.browser
async def test_a_filled_page_and_its_popups_send_nothing(
    chromium: Any, counting_server: tuple[str, _Counts]
) -> None:
    url, counts = counting_server
    guard = ScanGuard(host_allowed=lambda h: h == "127.0.0.1")
    ctx = await chromium.new_context(service_workers="block")
    await guard.install(ctx, harden_js=True)
    page = await ctx.new_page()
    guard.bind(page, 2)
    await page.goto(f"{url}/page")
    guard.arm(page, ["9173 2206 4418", "Meera Venkatesan", "ab"])
    for js in (
        "document.getElementById('get').submit()",  # neutralised by HARDEN_JS (counted)
        "document.getElementById('get').requestSubmit()",
    ):
        await _attempt(page, js)
    # read now: a blocked navigation below commits Chromium's error page in this tab
    attempts = await page.evaluate("window.__dpsGuard.submitAttempts")
    for js in (
        "fetch('/lookup').catch(() => 0)",
        "new Image().src = '/px.gif?a=9173%202206%204418'",
        "new Image().src = '/px.gif?a=plain'",
        "window.open('/popup?x=1')",
        "location.href = '/next?q=1'",
    ):
        await _attempt(page, js)
    guard.release(page)  # still open: stays filled until it closes
    await _attempt(page, "location.href = '/after-release'")
    await page.close()
    later = await ctx.new_page()
    await later.goto(f"{url}/page?ok=1")  # a fresh page navigates again
    await _attempt(later, "new Image().src = '/px.gif?b=9173-2206-4418'")
    reasons = {c.reason for c in guard.drain_counts() if c.decision == "blocked"}
    await ctx.close()
    assert attempts >= 2
    assert "/px.gif?a=plain" in counts.gets and "/page?ok=1" in counts.gets  # positive controls
    sent = " ".join(counts.gets)
    for leaked in ("/get-form", "/lookup", "9173", "/popup", "/next", "/after-release"):
        assert leaked not in sent, leaked
    assert counts.non_get == []
    assert {"filled_navigation", "filled_request", "filled_value"} <= reasons
