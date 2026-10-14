"""M4 verify r2 N1: a popup's first document (``about:blank``) is a realm no route sees.

A hostile in-scope HTTPS page opens ``window.open('')`` and uses the popup's realm at once, before
Playwright has attached to the popup: its requests are never routed, and over HTTPS the scope
proxy only sees the tunnel. ``ISOLATE_JS`` therefore refuses, in every realm, a beacon and any
method but GET/HEAD (and drops ``keepalive``), and makes such an *inert* document (and every frame
born in it) load nothing at all. Every proof here runs against a counting server over TLS, with
an unguarded positive control, and counts paths only.
"""

from __future__ import annotations

import base64
import ssl
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler
from typing import Any

import pytest

from dps.config import Settings
from dps.engine.service import PrivacyEngine
from dps.layers.scanner.browser import close_scan_context, new_scan_context
from dps.layers.scanner.guard import ALLOWED_METHODS, ScanGuard
from dps.layers.scanner.runtime import MemoryScanSink
from tests.scanner.helpers import make_runtime
from tests.scanner.test_guard import _QuietServer, _TrustingBrowser, tls_cert  # noqa: F401

#: the hostile page: at load, a popup's realm sends a beacon, a keepalive POST, a fetch POST, an
#: XHR PUT and a fetch DELETE; on every input, a new popup's realm sends the value (base64 in a
#: GET fetch and an XHR GET, plain in an image and in an XHR POST body). ``/diag-*`` images come
#: from the page's own realm and only prove that the popups opened.
_HOSTILE = """<!doctype html><html><head><title>Apply</title></head><body><h1>Apply</h1>
<form method="post" action="/apply-submit">
<label for="email">Email</label><input id="email" name="email" type="email" autocomplete="email">
<label for="nm">Full name</label><input id="nm" name="full_name" autocomplete="name">
<button type="submit">Send</button></form>
<script>
const T = (f) => { try { f(); } catch (e) {} };
T(() => {
  const w = window.open('', 'p0');
  new Image().src = '/diag-load-' + (w ? 'win' : 'null');
  T(() => w.navigator.sendBeacon('/hostile-beacon', 'draft'));
  T(() => w.fetch('/hostile-keepalive', {method: 'POST', body: 'draft', keepalive: true}).catch(() => 0));
  T(() => w.fetch('/hostile-post', {method: 'POST', body: 'draft'}).catch(() => 0));
  T(() => { const x = new w.XMLHttpRequest(); x.open('PUT', '/hostile-put'); x.send('draft'); });
  T(() => w.fetch('/hostile-delete', {method: 'DELETE'}).catch(() => 0));
});
let n = 0;
const leak = (ev) => T(() => {
  const v = ev.target.value;
  if (!v) { return; }
  const w = window.open('', 'f' + (++n));
  new Image().src = '/diag-fill-' + (w ? 'win' : 'null');
  const d = btoa(unescape(encodeURIComponent(v)));
  T(() => w.fetch('/hostile-get-fetch?d=' + d).catch(() => 0));
  T(() => { const x = new w.XMLHttpRequest(); x.open('GET', '/hostile-get-xhr?d=' + d); x.send(); });
  T(() => { const i = new w.Image(); i.src = '/hostile-get-img?v=' + encodeURIComponent(v); });
  T(() => { const x = new w.XMLHttpRequest(); x.open('POST', '/hostile-fill-post'); x.send(v); });
});
document.addEventListener('input', leak, true);
document.addEventListener('change', leak, true);
</script></body></html>"""

#: what the popup realms send at load
_AT_LOAD = (
    "POST /hostile-beacon",
    "POST /hostile-keepalive",
    "POST /hostile-post",
    "PUT /hostile-put",
    "DELETE /hostile-delete",
)


class _Seen:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.requests: list[str] = []  # "METHOD /path" (and the query, for the value checks)

    def paths(self) -> list[str]:
        with self.lock:
            return list(self.requests)


@pytest.fixture
def hostile_https(tls_cert: tuple[str, str]) -> Iterator[tuple[str, _Seen]]:  # noqa: F811
    seen = _Seen()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        timeout = 5

        def _any(self) -> None:
            length = int(self.headers.get("content-length") or 0)
            if length:
                self.rfile.read(length)
            with seen.lock:
                seen.requests.append(f"{self.command} {self.path}")
            body = _HOSTILE.encode() if self.path.split("?")[0] in ("/", "/apply") else b"{}"
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_GET = do_HEAD = do_POST = do_PUT = do_DELETE = do_PATCH = _any  # noqa: N815

        def log_message(self, *args: Any) -> None:
            return

    server = _QuietServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(*tls_cert)
    server.socket = tls.wrap_socket(server.socket, server_side=True, do_handshake_on_connect=False)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"https://127.0.0.1:{server.server_address[1]}", seen
    finally:
        server.shutdown()
        server.server_close()


def _hostile(requests: list[str]) -> list[str]:
    return [r for r in requests if " /hostile-" in r]


async def _until(page: Any, seen: _Seen, wanted: tuple[str, ...], *, timeout_s: float) -> None:
    """Wait until the server has seen a request for every path in ``wanted`` (or time is up)."""
    for _ in range(int(timeout_s * 10)):
        have = {r.split(" ")[1].split("?")[0] for r in seen.paths()}
        if all(w in have for w in wanted):
            return
        await page.wait_for_timeout(100)


@pytest.mark.browser
async def test_positive_control_unguarded_popup_realms_deliver_every_hostile_request(
    chromium: Any, hostile_https: tuple[str, _Seen]
) -> None:
    """Without the guard, this Chromium delivers every request the popup realms send, the value
    included: the scan proofs below are not vacuous."""
    url, seen = hostile_https
    value = "Meera Venkatesan"
    ctx = await chromium.new_context(ignore_https_errors=True)
    try:
        page = await ctx.new_page()
        await page.goto(f"{url}/apply")
        await page.fill("#nm", value)
        wanted = tuple(w.split(" ")[1] for w in _AT_LOAD)
        # a positive control: waiting longer never weakens a guarded assertion, and a loaded host
        # (several stacks on one Mac) can take more than 8 s to deliver the last popup request
        await _until(
            page, seen, (*wanted, "/hostile-get-fetch", "/hostile-fill-post"), timeout_s=30
        )
    finally:
        await ctx.close()
    got = _hostile(seen.paths())
    for want in _AT_LOAD:
        assert any(r.split("?")[0] == want for r in got), want
    encoded = base64.b64encode(value.encode()).decode()
    assert any(r.startswith("GET /hostile-get-fetch?d=") and encoded in r for r in got)
    assert any(r.startswith("GET /hostile-get-xhr?d=") and encoded in r for r in got)
    assert any(r.startswith("GET /hostile-get-img?v=") for r in got)
    assert any(r.startswith("POST /hostile-fill-post") for r in got)


@pytest.mark.browser
@pytest.mark.parametrize("run", [0, 1, 2])
async def test_a_scan_of_a_hostile_https_page_lets_nothing_out_of_its_popup_realms(
    chromium: Any,
    hostile_https: tuple[str, _Seen],
    scan_settings: Settings,
    scan_engine: PrivacyEngine,
    run: int,
) -> None:
    """The production path: a full ``ScanRunner`` scan (``new_scan_context``, hardening on) of the
    hostile page over HTTPS. The counting server receives no hostile request at all, neither the
    load-time POST/PUT/DELETE/beacon/keepalive nor the post-fill value; the page is filled (the
    post-fill popups opened) and the guard counts what it refused."""
    from dps.layers.scanner.crawler import ScanRunner

    url, seen = hostile_https
    sink = MemoryScanSink()
    runtime = make_runtime(
        url,
        scan_settings,
        scan_engine,
        path="/apply",
        cookies=False,
        screenshots=False,
        max_depth=1,
        personas=1,
    )
    await ScanRunner(
        settings=scan_settings,
        engine=scan_engine,
        sink=sink,
        browser=_TrustingBrowser(chromium),
        runtime=runtime,
        harden_js=True,
    ).run()
    got = seen.paths()
    assert any(r.startswith("GET /apply") for r in got)  # the page itself came over TLS
    assert any(r.startswith("GET /diag-load-win") for r in got)  # the load-time popup opened
    assert any(r.startswith("GET /diag-fill-win") for r in got)  # so did the post-fill ones
    assert _hostile(got) == []
    assert all(r.split(" ")[0] in ALLOWED_METHODS for r in got)
    assert [p.status for p in sink.pages.values() if p.page_no == 1] == ["done"]
    rows = sink.requests
    assert all(r.method in ALLOWED_METHODS for r in rows if r.decision == "allowed")
    refused = {(r.method, r.resource_type, r.reason) for r in rows if r.decision == "blocked"}
    assert ("POST", "beacon", "api_removed") in refused
    assert ("GET", "fetch", "api_removed") in refused  # the post-fill GET carrying the value
    assert ("GET", "xhr", "api_removed") in refused
    assert {("POST", "fetch"), ("PUT", "xhr"), ("DELETE", "fetch")} <= {
        (m, t) for m, t, _ in refused
    }
    assert sum(r.count for r in rows if r.decision == "blocked") > 0


#: what the unguarded positive control must deliver from the popups' documents
_INERT_WANTED = (
    "/hostile-img",
    "/hostile-script.js",
    "/hostile-form",
    "/hostile-ping",
    "/hostile-child-post",
    "/hostile-docopen-img",
    "/hostile-noopener",
)


@pytest.mark.browser
@pytest.mark.parametrize("guarded", [False, True], ids=["unguarded", "guarded"])
async def test_an_inert_popup_document_loads_nothing_even_after_document_open(
    chromium: Any, hostile_https: tuple[str, _Seen], scan_settings: Settings, guarded: bool
) -> None:
    """The popup's first document, scripted by its opener: subresources (an image, a script, a
    stylesheet, a frame), a form POST, an ``<a ping>``, a frame born in it, a socket, a worker and
    an EventSource, also after ``document.open()`` (which changes the URL, not the document) and
    from a popup whose ``opener`` was set to ``null``. Guarded: none of it reaches the server (a
    navigation of the popup is routed, and the page counts what it refused). Unguarded: all of it
    does."""
    url, seen = hostile_https
    script = """() => {
      const T = (f) => { try { f(); } catch (e) {} };
      const w = window.open('', 'i0');
      T(() => { const d = w.document; d.body.innerHTML = '<img src="/hostile-img">'
        + '<link rel="stylesheet" href="/hostile-css"><iframe src="/hostile-frame"></iframe>'; });
      T(() => { const s = w.document.createElement('script'); s.src = '/hostile-script.js'; w.document.body.append(s); });
      const q = window.open('', 'i3');  // its own popup: the POST navigates it away
      T(() => { const f = q.document.createElement('form'); f.method = 'post'; f.action = '/hostile-form';
        q.document.body.append(f); HTMLFormElement.prototype.submit.call(f); });
      T(() => { const f = w.document.createElement('iframe'); w.document.body.append(f);
        f.contentWindow.fetch('/hostile-child-post', {method: 'POST', body: 'x'}).catch(() => 0);
        f.contentWindow.fetch('/hostile-child-get').catch(() => 0); });
      T(() => { new w.WebSocket('wss://' + location.host + '/hostile-ws'); });
      T(() => { new w.EventSource('/hostile-es'); });
      const v = window.open('', 'i1');
      T(() => { v.document.open(); v.document.write('<img src="/hostile-docopen-img"><script src="/hostile-docopen.js"><\\/script>'); v.document.close(); });
      T(() => v.fetch('/hostile-docopen-fetch').catch(() => 0));
      const z = window.open('', 'i2'); z.opener = null;
      T(() => z.fetch('/hostile-noopener', {method: 'POST', body: 'x'}).catch(() => 0));
      T(() => z.fetch('/hostile-noopener-get').catch(() => 0));
      T(() => { const a = z.document.createElement('a'); a.href = '/popup-nav'; a.ping = '/hostile-ping';
        z.document.body.append(a); a.click(); });
      return true;
    }"""
    guard = ScanGuard(host_allowed=lambda h: h == "127.0.0.1")
    if guarded:
        ctx = await new_scan_context(
            _TrustingBrowser(chromium),
            scan_settings,
            storage_state=None,
            locale="en_IN",
            guard=guard,
            harden_js=False,
        )
    else:
        ctx = await chromium.new_context(ignore_https_errors=True)
    try:
        page = await ctx.new_page()
        guard.bind(page, 9)
        await page.goto(f"{url}/blank-host")
        assert await page.evaluate(script) is True
        if guarded:
            await page.wait_for_timeout(1500)
            await _until(page, seen, ("/popup-nav",), timeout_s=5)
        else:
            await _until(page, seen, _INERT_WANTED, timeout_s=30)  # the positive control
        counted = guard.drain_counts()
    finally:
        if guarded:
            await close_scan_context(ctx, guard)
        else:
            await ctx.close()
    got = _hostile(seen.paths())
    if not guarded:
        for want in _INERT_WANTED:
            assert any(r.split(" ")[1].split("?")[0] == want for r in got), want
        return
    assert got == []
    assert any(r.startswith("GET /popup-nav") for r in seen.paths())  # routed, and allowed
    refused = {(c.method, c.resource_type) for c in counted if c.reason == "api_removed"}
    assert {("POST", "fetch"), ("GET", "fetch"), ("GET", "websocket"), ("GET", "eventsource")} <= (
        refused
    )


def test_the_report_binding_counts_only_known_kinds_and_reasons() -> None:
    """``__dpsGuardReport`` is page-callable: an unknown kind is dropped, an unknown reason counts
    as ``api_removed``, a custom method as ``OTHER``, and no URL reaches a counter."""
    guard = ScanGuard(host_allowed=lambda h: h == "127.0.0.1")
    secret = "https://127.0.0.1/x?d=TWVlcmE"
    guard._report({"page": None}, "fetch", "POST", secret, "method")
    guard._report({"page": None}, "xhr", "PUT", secret)
    guard._report({"page": None}, "eventsource", "GET", secret, "ok")
    guard._report({"page": None}, "document", "GET", secret, "method")
    guard._report({"page": None}, "fetch", "MEERA", "https://meera.tracker.example/", "method")
    rows = {(c.method, c.resource_type, c.decision, c.reason, c.host) for c in guard.drain_counts()}
    assert rows == {
        ("POST", "fetch", "blocked", "method", "127.0.0.1"),
        ("PUT", "xhr", "blocked", "api_removed", "127.0.0.1"),
        ("GET", "eventsource", "blocked", "api_removed", "127.0.0.1"),
        ("OTHER", "fetch", "blocked", "method", "*.tracker.example"),
    }
    assert "TWVlcmE" not in repr(guard.drain_blocked())
