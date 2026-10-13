"""The request guard (decision D4): the pure policy, the guard's source, and a browser proof
against a local server that counts everything it receives."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import ipaddress
import socketserver
import ssl
import threading
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl

import httpx
import pytest

pytest.importorskip("playwright")
from playwright.async_api import Error as PlaywrightError  # noqa: E402

from dps.config import Settings  # noqa: E402
from dps.layers.scanner.browser import (  # noqa: E402
    LAUNCH_ARGS,
    close_scan_context,
    new_scan_context,
)
from dps.layers.scanner.guard import (  # noqa: E402
    ALLOWED_METHODS,
    HARDEN_JS,
    ISOLATE_JS,
    LOCK_JS,
    UNROUTED,
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

#: a page that flushes a "draft" whenever it goes away, the way real onboarding apps do
_UNLOAD = """<!doctype html><html><body onbeforeunload="return 'stay'"><p>draft</p><script>
const send = (tag) => {
  navigator.sendBeacon('/beacon-' + tag, 'draft');
  fetch('/ka-' + tag, {method: 'POST', body: 'draft', keepalive: true}).catch(() => 0);
};
addEventListener('pagehide', () => send('pagehide'));
addEventListener('beforeunload', (e) => { send('beforeunload'); e.preventDefault(); e.returnValue = ''; });
addEventListener('unload', () => send('unload'));
document.addEventListener('visibilitychange', () => send('visibility'));
</script></body></html>"""

#: a popup that flushes on the way out and then closes itself
_SELF_CLOSE = _UNLOAD.replace("</script>", "setTimeout(() => window.close(), 150);</script>")

_SHARED = (
    "onconnect = () => { fetch('/from-shared', {method: 'PUT', body: 'x'}).catch(() => 0); "
    "try { new WebSocket('ws://' + location.host + '/shared-ws'); } catch (e) {} };"
)
#: a dedicated worker: opens a plain ws:// socket, POSTs, or reports it is alive, on request
_WS_WORKER = (
    "onmessage = (e) => { if (e.data === 'ws') { const w = new WebSocket('ws://' + location.host"
    " + '/worker-ws'); w.onopen = () => postMessage('open'); w.onerror = () => postMessage('error'); }"
    " else if (e.data === 'post') { fetch('/worker-post', {method: 'POST', body: 'x'}).catch(() => 0); }"
    " else { postMessage('alive'); } };"
)
#: the HTTPS proof page: a link with a ping, and a CSP whose violations are reported by POST
_TLS_PAGE = """<!doctype html><html><head><meta charset="utf-8"></head><body>
<a id="ping" href="/pinged-nav" ping="/ping-target">next</a></body></html>"""
_TLS_CSP = "img-src 'self'; report-uri /csp-report"
#: G2-r2 B1: a page that opens a popup at load (before any fill) and later relays a value to it
_RELAY_PAGE = """<!doctype html><html><body><input id="name"><script>
window.pop = window.open('/relay-popup', 'relay');
window.leak = () => { const v = document.getElementById('name').value;
  try { pop.postMessage(v, '*'); } catch (e) {}
  try { new BroadcastChannel('dps-relay').postMessage(v); } catch (e) {} };
</script></body></html>"""
#: the popup: a dedicated worker started at load sends whatever it is handed on a wss:// socket;
#: the popup's own realm tries a socket and a new worker too
_RELAY_POPUP = """<!doctype html><html><body><p>popup</p><script>
const w = new Worker(URL.createObjectURL(new Blob([`onmessage = (e) => {
  const s = new WebSocket('wss://' + new URL(self.origin).host + '/leak-worker?d=' + btoa(e.data));
  s.onopen = () => s.send(e.data); }`], {type: 'text/javascript'})));
const relay = (tag, v) => {
  try { w.postMessage(v); } catch (e) {}
  try { new WebSocket('wss://' + location.host + '/leak-' + tag + '?d=' + btoa(v)); } catch (e) {}
  try { new Worker('/wsw.js'); } catch (e) {}
};
addEventListener('message', (e) => relay('pm', e.data));
new BroadcastChannel('dps-relay').onmessage = (e) => relay('bc', e.data);
window.ready = true;
</script></body></html>"""
_SERVICE = (
    "self.addEventListener('install', () => self.skipWaiting()); "
    "self.addEventListener('activate', () => fetch('/from-sw', {method: 'POST', body: 'x'}));"
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
            port = self.server.server_address[1]
            if self.path.startswith("/redir-other"):  # an open redirect to a host out of scope
                self.send_response(302)
                self.send_header("location", f"http://localhost:{port}/landed-other")
                self.send_header("content-length", "0")
                self.end_headers()
                return
            if self.path.startswith("/redir-foreign"):  # to the out-of-scope listener
                query = dict(parse_qsl(self.path.partition("?")[2]))
                self.send_response(302)
                self.send_header(
                    "location", f"{query['scheme']}://localhost:{query['port']}/landed-foreign"
                )
                self.send_header("content-length", "0")
                self.end_headers()
                return
            if self.path.startswith("/redir-ok"):  # positive control: a redirect within scope
                self.send_response(302)
                self.send_header("location", "/landed-ok")
                self.send_header("content-length", "0")
                self.end_headers()
                return
            pages = {
                "/unload": _UNLOAD,
                "/self-close": _SELF_CLOSE,
                "/tls-page": _TLS_PAGE,
                "/relay": _RELAY_PAGE,
                "/relay-popup": _RELAY_POPUP,
            }
            route = self.path.split("?", 1)[0]
            headers = {"/tls-page": [("content-security-policy", _TLS_CSP)]}.get(route, [])
            if self.path.startswith("/page"):
                body, ctype = _PAGE.encode(), "text/html; charset=utf-8"
            elif route in pages:
                body, ctype = pages[route].encode(), "text/html; charset=utf-8"
            elif self.path == "/worker.js":
                body, ctype = _WORKER.encode(), "text/javascript"
            elif self.path == "/wsw.js":
                body, ctype = _WS_WORKER.encode(), "text/javascript"
            elif self.path == "/shw.js":
                body, ctype = _SHARED.encode(), "text/javascript"
            elif self.path.startswith("/sw"):
                body, ctype = _SERVICE.encode(), "text/javascript"
            elif self.path == "/s.css":
                body, ctype = b"body{font:14px sans-serif}", "text/css"
            else:
                body, ctype = b"ok", "text/plain"
            self.send_response(200)
            for name, value in headers:
                self.send_header(name, value)
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
    assert await guard.arm(page, ["9173 2206 4418", "Meera Venkatesan", "ab"])
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


# --- review round 1 (M4-G2, design D-C): closing pages, workers, sockets, redirect hops ----
class _Accepted:
    """What a raw listener received: connections accepted and bytes read."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.connections = 0
        self.bytes = 0


@pytest.fixture
def foreign_listener() -> Iterator[tuple[int, _Accepted]]:
    """A host the scope does NOT allow (reached as ``localhost:<port>``): a raw TCP listener that
    counts every connection it accepts, so "nothing arrived" includes a TLS ClientHello."""
    seen = _Accepted()

    class Handler(socketserver.BaseRequestHandler):
        def handle(self) -> None:
            with seen.lock:
                seen.connections += 1
            self.request.settimeout(0.5)
            with contextlib.suppress(OSError):
                while data := self.request.recv(65536):  # until the peer pauses or closes
                    with seen.lock:
                        seen.bytes += len(data)
            with contextlib.suppress(OSError):
                self.request.sendall(b"HTTP/1.1 200 OK\r\ncontent-length: 2\r\n\r\nok")

    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1], seen
    finally:
        server.shutdown()
        server.server_close()


async def _plain_close_sends_unload_posts(chromium: Any, url: str, counts: _Counts) -> None:
    """Positive control: an unguarded page closed with ``page.close()`` does send its draft."""
    ctx = await chromium.new_context()
    page = await ctx.new_page()
    await page.goto(f"{url}/unload?control=1")
    await page.close()
    await asyncio.sleep(0.8)
    await ctx.close()
    with counts.lock:
        sent, counts.non_get[:] = list(counts.non_get), []
    assert any(p.startswith("POST /beacon-") or p.startswith("POST /ka-") for p in sent), sent


@pytest.mark.browser
@pytest.mark.parametrize("filled", [False, True])
async def test_closing_a_page_through_the_guard_lets_no_unload_request_out(
    chromium: Any, counting_server: tuple[str, _Counts], filled: bool
) -> None:
    """The route alone (no scope proxy: over plain HTTP the proxy would refuse these POSTs on its
    own, and over HTTPS it cannot see a method): every request a page sends while it goes is
    aborted, beforeunload dialogs included."""
    url, counts = counting_server
    await _plain_close_sends_unload_posts(chromium, url, counts)
    guard = ScanGuard(host_allowed=lambda h: h == "127.0.0.1")
    ctx = await chromium.new_context(service_workers="block")
    await guard.install(ctx, harden_js=False)
    pages = []
    for n in range(3):
        page = await ctx.new_page()
        guard.bind(page, n + 1)
        await page.goto(f"{url}/unload?n={n}")
        await page.mouse.click(5, 5)  # user activation: beforeunload may now ask to stay
        if filled:
            assert await guard.arm(page, ["9173 2206 4418"])
        pages.append(page)
    error_page = await ctx.new_page()  # an error page (a blocked navigation) is closed alike
    await _attempt(error_page, f"location.href = '{url.replace('127.0.0.1', 'localhost')}/x'")
    for page in [*pages, error_page]:
        assert await guard.close(page) is True and page.is_closed()
    await asyncio.sleep(0.8)
    counted = guard.drain_counts()
    await ctx.close()
    assert counts.non_get == []
    assert guard.pages_left_open == 0
    blocked = {(c.method, c.reason) for c in counted if c.decision == "blocked"}
    assert ("POST", "closing") in blocked  # the route saw the draft flush and aborted it


class _StuckPage:
    """A page whose navigation away fails (a renderer that hangs)."""

    url = "http://127.0.0.1/filled"

    def __init__(self) -> None:
        self.closed = False
        self.frames: list[Any] = []

    def is_closed(self) -> bool:
        return self.closed

    def once(self, event: str, handler: Any) -> None:
        return None

    def on(self, event: str, handler: Any) -> None:
        return None

    def remove_listener(self, event: str, handler: Any) -> None:
        return None

    async def goto(self, url: str, **kw: Any) -> None:
        raise PlaywrightError("navigation cancelled")

    async def close(self, **kw: Any) -> None:
        self.closed = True


async def test_a_page_that_cannot_navigate_away_is_left_open_and_aborted() -> None:
    """Closing it would run its unload handlers where no route sees them; closing the context
    later runs none. Until then everything it sends is aborted (``closing``)."""
    guard = ScanGuard(host_allowed=lambda h: True)
    page: Any = _StuckPage()
    assert await guard.arm(page, ["9173 2206 4418"])
    assert await guard.close(page) is False
    assert page.closed is False and guard.pages_left_open == 1
    assert guard.is_closing(page) and await guard._is_filled(page)


@pytest.mark.browser
async def test_a_window_cannot_close_itself_out_of_the_routes_sight(
    chromium: Any, counting_server: tuple[str, _Counts], scan_settings: Settings
) -> None:
    url, counts = counting_server
    guard = ScanGuard(host_allowed=lambda h: h == "127.0.0.1")
    ctx = await new_scan_context(
        chromium, scan_settings, storage_state=None, locale="en_IN", guard=guard, harden_js=False
    )
    try:
        opener = await ctx.new_page()
        await opener.goto(f"{url}/page")
        async with ctx.expect_page() as info:
            await opener.evaluate("window.open('/self-close')")
        popup = await info.value
        await asyncio.sleep(0.8)
        assert not popup.is_closed()  # ISOLATE_JS: window.close() is a no-op
    finally:
        await close_scan_context(ctx, guard)
    assert counts.non_get == [] and guard.pages_left_open == 0


@pytest.mark.browser
async def test_close_scan_context_closes_every_page_through_the_guard(
    chromium: Any, counting_server: tuple[str, _Counts], scan_settings: Settings
) -> None:
    url, counts = counting_server
    guard = ScanGuard(host_allowed=lambda h: h == "127.0.0.1")
    ctx = await new_scan_context(
        chromium, scan_settings, storage_state=None, locale="en_IN", guard=guard
    )
    for n in range(2):
        page = await ctx.new_page()
        await page.goto(f"{url}/unload?ctx={n}")
    await close_scan_context(ctx, guard)
    await close_scan_context(ctx, guard)  # idempotent
    await asyncio.sleep(0.5)
    assert counts.non_get == [] and not guard.proxy_running
    assert any(c.reason == "closing" for c in guard.drain_counts())


@pytest.mark.browser
@pytest.mark.parametrize("harden_js", [False, True])
async def test_no_shared_or_service_worker_runs_in_a_scan_context(
    chromium: Any, counting_server: tuple[str, _Counts], scan_settings: Settings, harden_js: bool
) -> None:
    assert "--disable-shared-workers" in LAUNCH_ARGS
    url, counts = counting_server
    guard = ScanGuard(host_allowed=lambda h: h == "127.0.0.1")
    ctx = await new_scan_context(
        chromium,
        scan_settings,
        storage_state=None,
        locale="en_IN",
        guard=guard,
        harden_js=harden_js,
    )
    try:
        page = await ctx.new_page()
        await page.goto(f"{url}/page")
        if harden_js:
            assert await guard.arm(page, ["9173 2206 4418"])
        assert await page.evaluate("typeof SharedWorker") == "undefined"
        await _attempt(page, "new SharedWorker('/shw.js').port.start()")
        registered = await page.evaluate(
            "ServiceWorkerContainer.prototype.register.call(navigator.serviceWorker, '/sw1.js')"
            ".then(() => 'registered', () => 'refused')"
        )
        framed = await page.evaluate(
            "(() => { const f = document.createElement('iframe'); document.body.append(f); "
            "return f.contentWindow.ServiceWorkerContainer.prototype.register"
            ".call(navigator.serviceWorker, '/sw2.js').then(() => 'registered', () => 'refused'); })()"
        )
        await asyncio.sleep(1.0)
        controlled = await page.evaluate("!!navigator.serviceWorker.controller")
    finally:
        await close_scan_context(ctx, guard)
    assert registered == framed == "refused" and controlled is False
    assert not any(p.startswith(("/shw.js", "/from-shared", "/sw")) for p in counts.gets)
    assert counts.non_get == [] and counts.upgrades == 0


async def test_a_service_worker_is_refused_even_by_the_proxy(
    counting_server: tuple[str, _Counts],
) -> None:
    """Defence in depth: over plain HTTP the scope proxy refuses a service worker's script."""
    url, counts = counting_server
    guard = ScanGuard(host_allowed=lambda h: h == "127.0.0.1")
    status, _ = await _through_proxy(
        guard, f"GET {url}/sw.js HTTP/1.1\r\nService-Worker: script\r\n"
    )
    await guard.stop_proxy()
    assert status == 403 and "/sw.js" not in counts.gets


# --- the D-C proof: a counting server receives nothing; the guard counts each attempt ------
_LOCKED_ATTEMPTS = {
    "beacon": "navigator.sendBeacon('/beacon-armed', 'draft')",
    "keepalive_post": "fetch('/ka-armed', {method: 'POST', body: 'draft', keepalive: true})"
    ".then(() => 'sent', () => 'failed')",
    "keepalive_get": "fetch(new Request('/ka-get-armed', {keepalive: true}))"
    ".then(() => 'sent', () => 'failed')",
    "keepalive_getter": "(() => { let n = 0; const init = {get keepalive() { return n++ > 0; }}; "
    "return fetch('/ka-getter-armed', init).then(() => 'sent', () => 'failed'); })()",
    "websocket": "(() => { try { new WebSocket('ws://' + location.host + '/ws-armed'); "
    "return 'opened'; } catch (e) { return 'refused'; } })()",
    "worker": "(() => { try { new Worker('/wsw.js'); return 'started'; } "
    "catch (e) { return 'refused'; } })()",
    "worker_via_prototype": "(() => { try { new Worker.prototype.constructor('/wsw.js'); "
    "return 'started'; } catch (e) { return 'refused'; } })()",
    "shared_worker": "typeof SharedWorker",
    "rtc": "typeof RTCPeerConnection",
}


@pytest.mark.browser
async def test_a_filled_page_can_beacon_keepalive_socket_or_start_workers_nowhere(
    chromium: Any, counting_server: tuple[str, _Counts], scan_settings: Settings
) -> None:
    url, counts = counting_server
    guard = ScanGuard(host_allowed=lambda h: h == "127.0.0.1")
    ctx = await new_scan_context(
        chromium, scan_settings, storage_state=None, locale="en_IN", guard=guard, harden_js=False
    )
    try:
        page = await ctx.new_page()
        guard.bind(page, 4)
        await page.goto(f"{url}/page")
        # a worker started before the fill, ready to carry values out on a socket
        alive = await page.evaluate(
            "new Promise((done) => { window.__w = new Worker('/wsw.js'); "
            "__w.onmessage = (e) => done(e.data); __w.postMessage('ping'); "
            "setTimeout(() => done('timeout'), 3000); })"
        )
        assert alive == "alive"
        assert await guard.arm(page, ["9173 2206 4418"])
        results = {name: await page.evaluate(js) for name, js in _LOCKED_ATTEMPTS.items()}
        stale = await page.evaluate(
            "new Promise((done) => { __w.onmessage = (e) => done(e.data); "
            "__w.postMessage('ws'); __w.postMessage('post'); setTimeout(() => done('silent'), 1500); })"
        )
        framed = await page.evaluate(  # a fresh frame of the filled page is locked at birth
            "(() => { const f = document.createElement('iframe'); document.body.append(f); "
            "return f.contentWindow.navigator.sendBeacon('/beacon-frame', 'draft'); })()"
        )
        await asyncio.sleep(0.5)
        counted = guard.drain_counts()
    finally:
        await close_scan_context(ctx, guard)
    await asyncio.sleep(0.5)
    assert results == {
        "beacon": False,
        "keepalive_post": "failed",  # the route aborts it; keepalive was stripped first
        "keepalive_get": "failed",
        "keepalive_getter": "failed",
        "websocket": "refused",
        "worker": "refused",
        "worker_via_prototype": "refused",
        "shared_worker": "undefined",
        "rtc": "undefined",
    }
    assert stale == "silent" and framed is False  # the old worker was terminated at arm
    assert counts.non_get == [] and counts.upgrades == 0
    assert not any("armed" in p or "worker-" in p or "frame" in p for p in counts.gets)
    removed = {
        (c.page_no, c.method, c.resource_type)
        for c in counted
        if (c.decision, c.reason) == ("blocked", "api_removed")
    }
    assert {(4, "POST", "beacon"), (4, "POST", "keepalive"), (4, "GET", "keepalive")} <= removed
    assert {(4, "GET", "websocket"), (4, "GET", "worker")} <= removed
    keepalive = [c for c in counted if (c.resource_type, c.reason) == ("keepalive", "api_removed")]
    # the getter's first read (false) is the only one: the Request is built once, so the flag the
    # wrapper checked is the flag the browser uses, and nothing is reported for it
    assert sum(c.count for c in keepalive) == 2
    assert all(c.host in ("127.0.0.1", "-") for c in counted)


@pytest.mark.browser
async def test_documents_after_the_first_fill_are_locked_at_birth(
    chromium: Any, counting_server: tuple[str, _Counts], scan_settings: Settings
) -> None:
    url, counts = counting_server
    guard = ScanGuard(host_allowed=lambda h: h == "127.0.0.1")
    ctx = await new_scan_context(
        chromium, scan_settings, storage_state=None, locale="en_IN", guard=guard
    )
    try:
        first = await ctx.new_page()
        await first.goto(f"{url}/page")
        before = await first.evaluate("typeof WebSocket")
        assert await guard.arm(first, ["9173 2206 4418"])
        later = await ctx.new_page()
        await later.goto(f"{url}/page?later=1")
        after = await later.evaluate(_LOCKED_ATTEMPTS["websocket"])
        beacon = await later.evaluate(_LOCKED_ATTEMPTS["beacon"])
    finally:
        await close_scan_context(ctx, guard)
    assert before == "function" and after == "refused" and beacon is False
    assert counts.non_get == [] and counts.upgrades == 0
    assert "LOCK_JS" in Path(ROOT / "dps/layers/scanner/guard.py").read_text(encoding="utf-8")
    assert "__dpsGuardLock" in LOCK_JS


@pytest.mark.browser
async def test_a_workers_websocket_never_reaches_the_server(
    chromium: Any, counting_server: tuple[str, _Counts], scan_settings: Settings
) -> None:
    """Playwright's ``route_web_socket`` does not see a dedicated worker's socket; the scope proxy
    refuses the plaintext upgrade inside the tunnel Chromium opens for it."""
    url, counts = counting_server
    guard = ScanGuard(host_allowed=lambda h: h == "127.0.0.1")
    ctx = await new_scan_context(
        chromium, scan_settings, storage_state=None, locale="en_IN", guard=guard
    )
    try:
        page = await ctx.new_page()
        await page.goto(f"{url}/page")
        answer = await page.evaluate(
            "new Promise((done) => { const w = new Worker('/wsw.js'); "
            "w.onmessage = (e) => done(e.data); w.postMessage('ws'); "
            "setTimeout(() => done('timeout'), 4000); })"
        )
        counted = guard.drain_counts()
    finally:
        await close_scan_context(ctx, guard)
    assert answer == "error" and counts.upgrades == 0
    assert "/worker-ws" not in counts.gets
    hops = {(c.method, c.resource_type, c.decision, c.reason) for c in counted}
    assert ("GET", UNROUTED, "blocked", "method") in hops


@pytest.mark.browser
async def test_a_speculative_prefetch_is_refused_by_the_proxy(
    chromium: Any, counting_server: tuple[str, _Counts], scan_settings: Settings
) -> None:
    """Chromium fetches speculation-rules prefetches itself, where no route sees them (not even on
    a filled page). Over plain HTTP the scope proxy refuses them by their ``Sec-Purpose`` header;
    over HTTPS they stay a residual risk (a GET to an allowed host, see guard.py)."""
    url, counts = counting_server
    guard = ScanGuard(host_allowed=lambda h: h == "127.0.0.1")
    ctx = await new_scan_context(
        chromium, scan_settings, storage_state=None, locale="en_IN", guard=guard
    )
    try:
        page = await ctx.new_page()
        await page.goto(f"{url}/page")
        assert await guard.arm(page, ["9173 2206 4418"])
        await page.evaluate(
            "() => { const s = document.createElement('script'); s.type = 'speculationrules'; "
            "s.textContent = JSON.stringify({prefetch: [{source: 'list', "
            "urls: ['/spec-prefetch?v=9173-2206', '/spec-plain']}]}); document.head.append(s); }"
        )
        await asyncio.sleep(1.5)
        counted = guard.drain_counts()
    finally:
        await close_scan_context(ctx, guard)
    assert not any(p.startswith("/spec-") for p in counts.gets)
    reasons = {(c.resource_type, c.reason) for c in counted if c.decision == "blocked"}
    assert (UNROUTED, "speculative") in reasons


@pytest.mark.browser
async def test_redirect_hops_never_leave_the_scope(
    chromium: Any,
    counting_server: tuple[str, _Counts],
    foreign_listener: tuple[int, _Accepted],
    scan_settings: Settings,
) -> None:
    url, counts = counting_server
    port, foreign = foreign_listener
    guard = ScanGuard(host_allowed=lambda h: h == "127.0.0.1")
    ctx = await new_scan_context(
        chromium, scan_settings, storage_state=None, locale="en_IN", guard=guard
    )
    try:
        for n, scheme in enumerate(("http", "https")):
            page = await ctx.new_page()
            guard.bind(page, n + 1)
            await page.goto(f"{url}/page")
            target = f"/redir-foreign?scheme={scheme}&port={port}"
            for js in (
                f"new Image().src = '{target}&via=img'",
                f"fetch('{target}&via=fetch').catch(() => 0)",
                "new Image().src = '/redir-ok'",
            ):
                await _attempt(page, js)
            with contextlib.suppress(PlaywrightError):
                await page.goto(f"{url}{target}&via=nav")
            await page.wait_for_timeout(300)
            assert await guard.close(page)
        counted = guard.drain_counts()
    finally:
        await close_scan_context(ctx, guard)
    assert foreign.connections == 0 and foreign.bytes == 0  # nothing reached the foreign host
    assert "localhost" not in counts.hosts
    assert "/landed-ok" in counts.gets  # positive control: an in-scope redirect is followed
    hops = [c for c in counted if c.resource_type == UNROUTED]
    assert sum(c.count for c in hops) >= 6
    assert {(c.page_no, c.method, c.decision, c.reason) for c in hops} == {
        (None, "GET", "blocked", "host"),  # plain HTTP: the hop itself
        (None, "CONNECT", "blocked", "host"),  # HTTPS: the tunnel
    }
    assert {c.host for c in hops} == {"{host}"}  # a one-label page-chosen host is not kept


async def test_the_foreign_listener_counts_what_reaches_it(
    foreign_listener: tuple[int, _Accepted],
) -> None:
    """Positive control for the redirect proof."""
    port, seen = foreign_listener
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(b"\x16\x03\x01hello")
    await writer.drain()
    await asyncio.wait_for(reader.read(), 5)
    writer.close()
    assert seen.connections == 1 and seen.bytes == 8


async def _through_proxy(guard: ScanGuard, head: str, extra: bytes = b"") -> tuple[int, bytes]:
    """Send one raw request to ``guard``'s scope proxy; the status code and the whole answer."""
    proxy = await guard.start_proxy()
    host, port = proxy["server"].removeprefix("http://").split(":")
    reader, writer = await asyncio.open_connection(host, int(port))
    writer.write(head.encode("latin-1") + b"\r\n" + extra)
    await writer.drain()
    data = await asyncio.wait_for(reader.read(), 10)
    writer.close()
    status = int(data.split(b" ", 2)[1]) if data.startswith(b"HTTP/1.") else 0
    return status, data


async def _tunnel(guard: ScanGuard, authority: str) -> tuple[Any, Any]:
    proxy = await guard.start_proxy()
    host, port = proxy["server"].removeprefix("http://").split(":")
    reader, writer = await asyncio.open_connection(host, int(port))
    writer.write(f"CONNECT {authority} HTTP/1.1\r\n\r\n".encode())
    assert (await reader.readuntil(b"\r\n\r\n")).startswith(b"HTTP/1.1 200")
    return reader, writer


async def test_the_scope_proxy_enforces_the_guard_on_every_hop(
    counting_server: tuple[str, _Counts],
) -> None:
    url, counts = counting_server
    other = url.replace("127.0.0.1", "localhost")
    authority = url.removeprefix("http://")
    guard = ScanGuard(host_allowed=lambda h: h == "127.0.0.1")
    await guard.arm(_StuckPage(), ["9173 2206 4418"])  # needles of an earlier filled page
    try:
        ok, body = await _through_proxy(guard, f"GET {url}/via-proxy HTTP/1.1\r\nHost: x\r\n")
        assert ok == 200 and body.endswith(b"ok") and b"onnection: close" in body
        refused = {
            "post": await _through_proxy(
                guard, f"POST {url}/p HTTP/1.1\r\nContent-Length: 1\r\n", b"x"
            ),
            "body": await _through_proxy(
                guard, f"GET {url}/b HTTP/1.1\r\nContent-Length: 1\r\n", b"x"
            ),
            "override": await _through_proxy(
                guard, f"GET {url}/o HTTP/1.1\r\nX-HTTP-Method-Override: PUT\r\n"
            ),
            "query": await _through_proxy(guard, f"GET {url}/q?_method=DELETE HTTP/1.1\r\n"),
            "upgrade": await _through_proxy(
                guard, f"GET {url}/u HTTP/1.1\r\nUpgrade: websocket\r\n"
            ),
            "host": await _through_proxy(guard, f"GET {other}/h HTTP/1.1\r\n"),
            "value": await _through_proxy(guard, f"GET {url}/v?a=9173%202206%204418 HTTP/1.1\r\n"),
            "scheme": await _through_proxy(guard, "GET ftp://127.0.0.1/f HTTP/1.1\r\n"),
            "connect": await _through_proxy(
                guard, f"CONNECT {other.removeprefix('http://')} HTTP/1.1\r\n"
            ),
            "junk": await _through_proxy(guard, "NOT A REQUEST\r\n"),
        }
        for name, (status, _) in refused.items():
            assert status in (400, 403), name
        # an allowed tunnel: a plaintext GET inside it gets the plain-HTTP rules and one answer
        reader, writer = await _tunnel(guard, authority)
        writer.write(b"GET /through-tunnel HTTP/1.1\r\nHost: x\r\n\r\n")
        assert (await asyncio.wait_for(reader.read(), 10)).endswith(b"ok")
        writer.close()
        # ... and a plaintext upgrade (a ws:// socket, which Chromium tunnels) or POST is refused
        for inner in (
            b"GET /tunnel-ws HTTP/1.1\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n\r\n",
            b"POST /tunnel-post HTTP/1.1\r\nContent-Length: 1\r\n\r\nx",
        ):
            reader, writer = await _tunnel(guard, authority)
            writer.write(inner)
            assert (await asyncio.wait_for(reader.read(), 10)).startswith(b"HTTP/1.1 403")
            writer.close()
    finally:
        await guard.stop_proxy()
        await guard.stop_proxy()  # idempotent
    assert counts.non_get == [] and "localhost" not in counts.hosts and counts.upgrades == 0
    assert sorted(counts.gets) == ["/through-tunnel", "/via-proxy"]  # nothing refused arrived
    rows = guard.drain_counts()
    assert all(r.resource_type == UNROUTED and r.decision == "blocked" for r in rows)
    reasons = {r.reason for r in rows}
    assert {"method", "host", "filled_value", "scheme"} <= reasons
    assert all("9173" not in r.host for r in rows)


async def test_a_tls_tunnel_to_an_allowed_host_carries_its_bytes(
    foreign_listener: tuple[int, _Accepted],
) -> None:
    """Positive control: what starts like a TLS record is tunnelled untouched (method rules for
    HTTPS stay with the route)."""
    port, seen = foreign_listener
    guard = ScanGuard(host_allowed=lambda h: h == "127.0.0.1")
    try:
        reader, writer = await _tunnel(guard, f"127.0.0.1:{port}")
        writer.write(b"\x16\x03\x01hello")
        await writer.drain()
        assert (await asyncio.wait_for(reader.read(), 10)).endswith(b"ok")
        writer.close()
    finally:
        await guard.stop_proxy()
    assert seen.connections == 1 and seen.bytes == 8


# --- review G2 round 1: HTTPS, where the scope proxy sees a tunnel and nothing inside it -----
@pytest.fixture(scope="module")
def tls_cert(tmp_path_factory: pytest.TempPathFactory) -> tuple[str, str]:
    """A throwaway self-signed certificate for 127.0.0.1 (test-only key, in a temp dir)."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    folder = tmp_path_factory.mktemp("tls")
    crt, pem = folder / "server.crt", folder / "server.key"
    crt.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    pem.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return str(crt), str(pem)


@pytest.fixture
def https_counting_server(tls_cert: tuple[str, str]) -> Iterator[tuple[str, _Counts]]:
    """The counting server over TLS: the bank's targets are ``https://``, and there the scope
    proxy only sees a CONNECT, so the route and ``ISOLATE_JS`` are all that stand in the way."""
    counts = _Counts()
    handler = type("TlsHandler", (_handler(counts),), {"timeout": 5})
    server = _QuietServer(("127.0.0.1", 0), handler)
    server.daemon_threads = True
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(*tls_cert)
    # the handshake runs in the handler's thread, so a silent client cannot stall the server
    server.socket = tls.wrap_socket(server.socket, server_side=True, do_handshake_on_connect=False)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"https://127.0.0.1:{server.server_address[1]}", counts
    finally:
        server.shutdown()
        server.server_close()


class _QuietServer(ThreadingHTTPServer):
    """Chromium aborts some handshakes with the self-signed certificate before it ignores the
    error and retries; those aborted connections are not worth a traceback."""

    def handle_error(self, request: Any, client_address: Any) -> None:
        return


class _TrustingBrowser:
    """``new_scan_context``'s context exactly, except that it trusts the test certificate."""

    def __init__(self, browser: Any) -> None:
        self._browser = browser
        self.version = browser.version

    async def new_context(self, **options: Any) -> Any:
        return await self._browser.new_context(**{**options, "ignore_https_errors": True})


#: what the guard cannot route over HTTPS: a deferred POST and a stream socket
_UNROUTED_APIS = {
    "fetch_later": "(() => { try { fetchLater('/fl-timer', {method: 'POST', body: 'draft', "
    "activateAfter: 200}); fetchLater('/fl-unload', {method: 'POST', body: 'draft'}); "
    "return 'sent'; } catch (e) { return e.name; } })()",
    "websocket_stream": "(() => { try { const s = new WebSocketStream('wss://' + location.host "
    "+ '/wss-stream'); s.opened.then((o) => o.writable.getWriter().write('draft'), () => 0); "
    "return 'opened'; } catch (e) { return e.name; } })()",
}
_UNROUTED_TYPES = (
    "[typeof fetchLater, typeof WebSocketStream, typeof Window.prototype.fetchLater, "
    "'fetchLater' in window, 'WebSocketStream' in window]"
)
_IN_FRAMES = (
    "(() => { const out = []; for (const make of [(f) => 0, (f) => { f.srcdoc = '<p>x</p>'; }]) { "
    "const f = document.createElement('iframe'); make(f); document.body.append(f); "
    "const w = f.contentWindow; out.push(typeof w.fetchLater, typeof w.WebSocketStream); "
    "try { w.fetchLater('/fl-frame', {method: 'POST', body: 'draft', activateAfter: 100}); "
    "out.push('sent'); } catch (e) { out.push('refused'); } } return out; })()"
)


@pytest.mark.browser
async def test_https_positive_control_an_unguarded_page_delivers_what_the_guard_must_stop(
    chromium: Any, https_counting_server: tuple[str, _Counts]
) -> None:
    """Without the guard, this Chromium sends ``fetchLater`` POSTs and opens ``WebSocketStream``
    sockets over TLS, and the counting server sees them: the proofs below are not vacuous."""
    url, counts = https_counting_server
    ctx = await chromium.new_context(ignore_https_errors=True)
    try:
        page = await ctx.new_page()
        await page.goto(f"{url}/tls-page")
        assert await page.evaluate("[typeof fetchLater, typeof WebSocketStream]") == [
            "function",
            "function",
        ]
        sent = {name: await page.evaluate(js) for name, js in _UNROUTED_APIS.items()}
        await _attempt(page, "new Worker('/wsw.js').postMessage('post')")
        await _attempt(page, f"new Image().src = '{url.replace('127.0.0.1', 'localhost')}/x.png'")
        await page.wait_for_timeout(1500)
        await page.close()  # the unload fetchLater is sent now
        await asyncio.sleep(1.0)
    finally:
        await ctx.close()
    assert sent == {"fetch_later": "sent", "websocket_stream": "opened"}
    received = " ".join(counts.non_get)
    for path in ("POST /fl-timer", "POST /worker-post", "POST /csp-report"):
        assert path in received, path
    assert counts.upgrades >= 1


@pytest.mark.browser
@pytest.mark.parametrize("filled", [False, True], ids=["unfilled", "filled"])
async def test_https_fetch_later_and_websocket_stream_are_gone_in_every_realm(
    chromium: Any,
    https_counting_server: tuple[str, _Counts],
    scan_settings: Settings,
    filled: bool,
) -> None:
    """G2-r1 B1/B2: neither API is ever routed, so ``ISOLATE_JS`` deletes both in every document,
    filled or not: 0 POSTs and 0 upgrades reach the server. A plain ``wss://`` WebSocket is closed
    by the route (unfilled) or refused and counted ``api_removed`` (filled)."""
    url, counts = https_counting_server
    guard = ScanGuard(host_allowed=lambda h: h == "127.0.0.1")
    ctx = await new_scan_context(
        _TrustingBrowser(chromium),
        scan_settings,
        storage_state=None,
        locale="en_IN",
        guard=guard,
        harden_js=False,
    )
    try:
        page = await ctx.new_page()
        guard.bind(page, 5)
        await page.goto(f"{url}/tls-page")
        if filled:
            assert await guard.arm(page, ["9173 2206 4418"])
        types = await page.evaluate(_UNROUTED_TYPES)
        used = {name: await page.evaluate(js) for name, js in _UNROUTED_APIS.items()}
        framed = await page.evaluate(_IN_FRAMES)
        socket = await page.evaluate(
            "(() => { try { new WebSocket('wss://' + location.host + '/wss-plain'); "
            "return 'opened'; } catch (e) { return 'refused'; } })()"
        )
        await page.wait_for_timeout(1500)
        counted = guard.drain_counts()
    finally:
        await close_scan_context(ctx, guard)
    await asyncio.sleep(1.0)  # an unload fetchLater would be sent by now
    assert "/tls-page" in counts.gets  # positive control: the page itself came over TLS
    assert counts.non_get == [] and counts.upgrades == 0
    assert not any(p.startswith(("/fl-", "/wss-")) for p in counts.gets)
    assert types == ["undefined", "undefined", "undefined", False, False]
    assert used == {"fetch_later": "ReferenceError", "websocket_stream": "ReferenceError"}
    assert framed == ["undefined", "undefined", "refused"] * 2
    removed = {
        (c.page_no, c.method, c.resource_type)
        for c in counted
        if (c.decision, c.reason) == ("blocked", "api_removed")
    }
    if filled:
        assert socket == "refused" and (5, "GET", "websocket") in removed
    else:
        assert socket == "opened" and guard.websockets_closed >= 1


@pytest.mark.browser
async def test_https_nothing_but_get_leaves_where_the_proxy_is_blind(
    chromium: Any, https_counting_server: tuple[str, _Counts], scan_settings: Settings
) -> None:
    """The plain-HTTP proofs, over TLS: a worker's POST, an XHR PUT, an ``<a ping>``, a CSP
    report and the unload beacons and keepalive POSTs of unfilled and filled pages closed through
    ``close_scan_context``. The route (not the proxy) stops every one of them."""
    url, counts = https_counting_server
    guard = ScanGuard(host_allowed=lambda h: h == "127.0.0.1")
    ctx = await new_scan_context(
        _TrustingBrowser(chromium),
        scan_settings,
        storage_state=None,
        locale="en_IN",
        guard=guard,
        harden_js=False,
    )
    try:
        drafts = []
        for n, fill in ((1, False), (2, True)):
            draft = await ctx.new_page()
            guard.bind(draft, n)
            await draft.goto(f"{url}/unload?n={n}")
            if fill:
                assert await guard.arm(draft, ["9173 2206 4418"])
            drafts.append(draft)
        page = await ctx.new_page()
        guard.bind(page, 3)
        await page.goto(f"{url}/tls-page")
        for js in (
            "fetch('/page-post', {method: 'POST', body: 'x'}).catch(() => 0)",
            "(() => { const x = new XMLHttpRequest(); x.open('PUT', '/xhr-put'); x.send('x'); })()",
            "new Worker('/wsw.js').postMessage('post')",
            f"new Image().src = '{url.replace('127.0.0.1', 'localhost')}/x.png'",
        ):
            await _attempt(page, js)
        await page.wait_for_timeout(800)
        await _attempt(page, "document.getElementById('ping').click()")
        await page.wait_for_timeout(800)
    finally:
        await close_scan_context(ctx, guard)
    await asyncio.sleep(1.0)
    counted = guard.drain_counts()
    assert counts.non_get == [] and counts.upgrades == 0
    # positive controls: the pages and the ping link's own navigation came over TLS
    assert {"/tls-page", "/unload?n=1", "/unload?n=2", "/pinged-nav"} <= set(counts.gets)
    assert not any(p.startswith(("/ping-target", "/csp-report", "/beacon-")) for p in counts.gets)
    blocked = {(c.method, c.reason) for c in counted if c.decision == "blocked"}
    assert {("POST", "method"), ("PUT", "method")} <= blocked  # CSP keeps the image itself home
    assert {r for _, r in blocked} & {"closing", "api_removed"}
    assert all(c.method in ALLOWED_METHODS for c in counted if c.decision == "allowed")


@pytest.mark.browser
@pytest.mark.parametrize("guarded", [False, True], ids=["unguarded", "guarded"])
async def test_https_a_popup_opened_before_the_fill_relays_nothing(
    chromium: Any,
    https_counting_server: tuple[str, _Counts],
    scan_settings: Settings,
    guarded: bool,
) -> None:
    """G2-r2 B1: a popup the page opened at load, before the fill, with a dedicated worker of its
    own. The filled page hands it the value by ``postMessage`` and ``BroadcastChannel``; the
    worker would send it on a ``wss://`` socket, which no route and no proxy rule can see over
    HTTPS. ``arm`` locks every open page of the context, so the worker is gone and the popup's
    later socket and worker are refused and counted. Unguarded, the value arrives."""
    url, counts = https_counting_server
    value = "Meera Venkatesan"
    guard = ScanGuard(host_allowed=lambda h: h == "127.0.0.1")
    if guarded:
        ctx = await new_scan_context(
            _TrustingBrowser(chromium),
            scan_settings,
            storage_state=None,
            locale="en_IN",
            guard=guard,
        )
    else:
        ctx = await chromium.new_context(ignore_https_errors=True)
    try:
        page = await ctx.new_page()
        guard.bind(page, 6)
        async with ctx.expect_page() as opened:
            await page.goto(f"{url}/relay")
        popup = await opened.value
        await popup.wait_for_function("window.ready === true", timeout=5000)
        other = await ctx.new_page()  # an unrelated tab, open at the fill
        await other.goto(f"{url}/tls-page")
        assert len(ctx.pages) == 3
        if guarded:
            assert await guard.arm(page, [value])
        await page.evaluate("(v) => { document.getElementById('name').value = v; }", value)
        await page.evaluate("() => leak()")
        await _attempt(other, "fetch('/other-fetch', {headers: {'x-v': 'v'}}).catch(() => 0)")
        await page.wait_for_timeout(2000)
        counted = guard.drain_counts()
    finally:
        if guarded:
            await close_scan_context(ctx, guard)
        else:
            await ctx.close()
    with counts.lock:
        leaked = [p for p in counts.gets if p.startswith("/leak-")]
        upgrades = counts.upgrades
    assert "/relay" in counts.gets and "/relay-popup" in counts.gets  # both pages came over TLS
    encoded = base64.b64encode(value.encode()).decode().rstrip("=")
    if not guarded:  # positive control: the relay works, and the worker's socket carries the value
        assert "/other-fetch" in counts.gets
        assert upgrades >= 2
        assert any(p.startswith("/leak-worker") and encoded in p for p in leaked)
        return
    assert upgrades == 0 and leaked == [] and counts.non_get == []
    assert "/other-fetch" not in counts.gets  # every page open at the fill is a filled page
    assert ("GET", "filled_request") in {(c.method, c.reason) for c in counted}
    removed = {
        (c.method, c.resource_type)
        for c in counted
        if (c.decision, c.reason) == ("blocked", "api_removed")
    }
    assert {("GET", "websocket"), ("GET", "worker")} <= removed
    assert guard.locks_failed == 0


def test_isolate_js_is_always_installed_and_blocks_what_no_route_sees() -> None:
    for needle in (
        "ServiceWorkerContainer",
        "'register'",
        "'close'",
        "SharedWorker",
        "RTCPeerConnection",
        "sendBeacon",
        "keepalive",
        "WebSocket",
        "Worker",
        "terminate",
        "beforeunload",
        "__dpsGuardLock",
        "'fetchLater'",
        "'WebSocketStream'",
    ):
        assert needle in ISOLATE_JS, needle
    src = (ROOT / "dps/layers/scanner/guard.py").read_text(encoding="utf-8")
    install = src[src.index("    async def install(") : src.index("    def bind(")]
    isolate = install.index("script=ISOLATE_JS")
    assert isolate < install.index("if harden_js")
    # the report binding exists, and Playwright's WebSocket replacement is wrapped, not wrapping
    assert install.index("expose_binding") < isolate
    assert install.index("route_web_socket") < isolate


def test_the_launch_flags_keep_every_connection_on_the_proxy() -> None:
    for flag in (
        "--disable-shared-workers",
        "--disable-quic",
        "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
    ):
        assert flag in LAUNCH_ARGS, flag
