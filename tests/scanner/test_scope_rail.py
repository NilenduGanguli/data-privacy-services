"""Review B2: the scan's deny and allow lists hold for navigations the scanner itself causes.

A counting server on a registered host offers what a real site's "Sign out" control often is (an
``href="#"`` link with an ``onclick`` navigation, a ``javascript:`` link), a link that redirects to
a denied path, an iframe on a denied path, and a page outside an allow list. SPA discovery clicks
those, the crawler follows the redirect, and the frame loads: the server must count **0** requests
to every denied (or not allowed) path, and the request log must count each one as blocked with
reason ``deny`` / ``not_allowed``. Controls: the allowed routes and an image outside the allow list
(a sub-resource, not a navigation) are served.
"""

from __future__ import annotations

import socket
import threading
from collections import Counter
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

pytest.importorskip("playwright")
from playwright.async_api import Error as PlaywrightError  # noqa: E402

from dps.config import Settings  # noqa: E402
from dps.engine.service import PrivacyEngine  # noqa: E402
from dps.layers.scanner.guard import (  # noqa: E402
    UNROUTED,
    ScanGuard,
    decide,
    is_navigation_hop,
)
from dps.layers.scanner.runtime import MemoryScanSink  # noqa: E402
from dps.layers.scanner.urls import CrawlScope, Origin  # noqa: E402
from tests.scanner.helpers import make_runtime  # noqa: E402

DENY_HOME = b"""<!doctype html><html><head><title>Home</title></head><body>
<h1>Account</h1>
<nav><a href="#" onclick="location.href='/logout'; return false;">Sign out</a>
<a href="javascript:location.assign('/admin/purge')">Purge cache</a>
<a href="/go">Continue</a>
<span role="link" onclick="location.href='/settings'">Settings</span></nav>
<iframe src="/admin/frame" title="admin"></iframe>
</body></html>"""
ALLOW_HOME = b"""<!doctype html><html><head><title>Home</title></head><body>
<h1>Account</h1><img src="/static/logo.png" alt="">
<nav><a href="#" onclick="location.href='/elsewhere'; return false;">Elsewhere</a>
<a href="/home/go">Continue</a>
<span role="link" onclick="location.href='/home/settings'">Settings</span></nav>
</body></html>"""
# Review r2 B1: a hash-routed SPA. The fragment never reaches the network, so the request guard
# cannot see ``#/logout``; its route handler does what an SPA logout does (an API call that is not
# itself on the deny list, and clearing the client session). SPA discovery must not click it.
HASH_HOME = b"""<!doctype html><html><head><title>Home</title></head><body>
<h1>Account</h1>
<nav><a href="#/logout">Sign out</a> <a href="#!/admin/purge?all=1">Purge</a>
<a href="#/profile">Profile</a></nav>
<script>
function route() {
  if (location.hash === '#/logout') { fetch('/api/session/end'); localStorage.clear(); }
  if (location.hash.startsWith('#!/admin/purge')) { fetch('/api/cache/purge'); }
  if (location.hash === '#/profile') { fetch('/api/profile'); }
}
window.addEventListener('hashchange', route);
</script>
</body></html>"""
# The replay click: the same ordinal points to the allowed route at discovery and, once that
# route has run, to the denied one (a page that re-renders its menu). The replay must not click it.
FLIP_HOME = b"""<!doctype html><html><head><title>Home</title></head><body>
<h1>Account</h1>
<nav><a id="m" href="#/profile">Menu</a></nav>
<script>
if (localStorage.getItem('flip') === '1') document.getElementById('m').setAttribute('href', '#/logout');
function route() {
  if (location.hash === '#/logout') { fetch('/api/session/end'); }
  if (location.hash === '#/profile') { fetch('/api/profile'); localStorage.setItem('flip', '1'); }
}
window.addEventListener('hashchange', route);
</script>
</body></html>"""
# M4 verify r1 R3-B1: an ``href="#"`` link whose own click handler routes to a denied path without
# a request the guard could refuse — a hash route set by script, a ``pushState``. The scanner
# cannot know before the click (the target is "#"); the route runs. The record must say so.
_ROUTER = b"""<script>
function route() {
  if (location.hash === '#/logout') { fetch('/api/session/end'); }
  if (location.hash === '#/profile') { fetch('/api/profile'); }
}
window.addEventListener('hashchange', route);
</script>"""
ONCLICK_HASH_HOME = (
    b"""<!doctype html><html><head><title>Home</title></head><body><h1>Account</h1>
<nav><a href="#" onclick="location.hash='#/logout'; return false">Sign out</a>
<a href="#/profile">Profile</a></nav>"""
    + _ROUTER
    + b"</body></html>"
)
PUSHSTATE_HOME = (
    b"""<!doctype html><html><head><title>Home</title></head><body><h1>Account</h1>
<nav><a href="#" onclick="history.pushState({}, '', '/logout'); fetch('/api/session/end');
return false">Sign out</a> <a href="#/profile">Profile</a></nav>"""
    + _ROUTER
    + b"</body></html>"
)
# replay: at discovery the handler routes to #/profile (allowed, queued); once that route ran it
# routes to #/logout, so the replay click lands on a denied route
FLIP_HANDLER_HOME = b"""<!doctype html><html><head><title>Home</title></head><body>
<h1>Account</h1>
<nav><a id="m" href="#" onclick="location.hash = localStorage.getItem('flip') === '1' ? '#/logout' : '#/profile'; return false">Menu</a></nav>
<script>
function route() {
  if (location.hash === '#/logout') { fetch('/api/session/end'); }
  if (location.hash === '#/profile') { fetch('/api/profile'); localStorage.setItem('flip', '1'); }
}
window.addEventListener('hashchange', route);
</script>
</body></html>"""
PAGE = b"<!doctype html><html><head><title>P</title></head><body><p>page</p></body></html>"
REDIRECTS = {"/go": "/logout", "/home/go": "/outside"}


class _Site(BaseHTTPRequestHandler):
    hits: Counter[str] = Counter()
    home: bytes = b""

    def do_GET(self) -> None:  # noqa: N802 - http.server's name
        path = self.path.split("?")[0]
        type(self).hits[path] += 1
        if path in REDIRECTS:
            self.send_response(302)
            self.send_header("Location", REDIRECTS[path])
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        body = self.home if path in ("/", "/home") else PAGE
        self.send_response(200)
        kind = "application/json" if path.startswith("/api/") else "text/html"
        self.send_header("Content-Type", "image/png" if path.endswith(".png") else kind)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a: Any) -> None:
        pass


def _serve(home: bytes) -> Iterator[tuple[str, Counter[str]]]:
    handler = type("Site", (_Site,), {"hits": Counter(), "home": home})
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    srv = ThreadingHTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{port}", handler.hits
    finally:
        srv.shutdown()
        srv.server_close()


@pytest.fixture
def deny_site() -> Iterator[tuple[str, Counter[str]]]:
    yield from _serve(DENY_HOME)


@pytest.fixture
def allow_site() -> Iterator[tuple[str, Counter[str]]]:
    yield from _serve(ALLOW_HOME)


@pytest.fixture
def hash_site() -> Iterator[tuple[str, Counter[str]]]:
    yield from _serve(HASH_HOME)


@pytest.fixture
def flip_site() -> Iterator[tuple[str, Counter[str]]]:
    yield from _serve(FLIP_HOME)


def _blocked(sink: MemoryScanSink, reason: str, resource_type: str | None = None) -> int:
    return sum(
        r.count
        for r in sink.requests
        if r.decision == "blocked"
        and r.reason == reason
        and (resource_type is None or r.resource_type == resource_type)
    )


def _allowed_with(sink: MemoryScanSink, reasons: tuple[str, ...]) -> int:
    return sum(r.count for r in sink.requests if r.decision == "allowed" and r.reason in reasons)


# --- the rule as pure functions -----------------------------------------------------------------
SCOPE = CrawlScope(
    start=Origin.of("http://demo-site:8410/apply"),
    same_origin=True,
    registered_hosts=frozenset({"demo-site"}),
    allow=("/apply", "/static"),
    deny=("/logout", "/admin"),
)
BASE: dict[str, Any] = dict(page_filled=False, host_allowed=True, needles=frozenset())


@pytest.mark.parametrize(
    ("url", "verdict"),
    [
        ("http://demo-site:8410/apply/x", "ok"),
        ("http://demo-site:8410/LOGOUT", "deny"),
        ("http://demo-site:8410/admin/purge?x=1", "deny"),
        ("http://demo-site:8410/%61dmin", "deny"),
        ("http://demo-site:8410/profile", "not_allowed"),
    ],
)
def test_request_path_verdict(url: str, verdict: str) -> None:
    assert SCOPE.request_path_verdict(url) == verdict


@pytest.mark.parametrize("navigation", [False, True])
def test_a_denied_path_refuses_every_request_kind(navigation: bool) -> None:
    for kind in ("document", "image", "xhr", "script", None):
        d = decide(
            method="GET",
            url="http://demo-site:8410/logout",
            is_navigation=navigation,
            resource_type=kind,
            path="deny",
            **BASE,
        )
        assert (d.allow, d.reason) == (False, "deny")


def test_not_allowed_refuses_navigations_only() -> None:
    nav = decide(method="GET", url="http://h/x", is_navigation=True, path="not_allowed", **BASE)
    img = decide(
        method="GET",
        url="http://h/x.png",
        is_navigation=False,
        resource_type="image",
        path="not_allowed",
        **BASE,
    )
    assert (nav.allow, nav.reason) == (False, "not_allowed") and img.allow is True
    # the method rule still comes first: a POST to a denied path is a refused submission
    post = decide(method="POST", url="http://h/logout", is_navigation=True, path="deny", **BASE)
    assert post.reason == "method"


@pytest.mark.parametrize(
    ("headers", "navigation"),
    [
        ({"sec-fetch-mode": "navigate"}, True),
        ({"sec-fetch-dest": "iframe"}, True),
        ({"upgrade-insecure-requests": "1"}, True),
        ({"accept": "text/html,application/xhtml+xml"}, True),
        ({"accept": "*/*", "sec-fetch-mode": "cors", "sec-fetch-dest": "empty"}, False),
        ({"accept": "image/avif,image/webp", "sec-fetch-dest": "image"}, False),
    ],
)
def test_navigation_hops_at_the_proxy(headers: dict[str, str], navigation: bool) -> None:
    assert is_navigation_hop(headers) is navigation


def test_the_proxy_refuses_and_counts_scope_paths_on_redirect_hops() -> None:
    guard = ScanGuard(
        host_allowed=SCOPE.network_host_allowed, path_verdict=SCOPE.request_path_verdict
    )
    nav = {"sec-fetch-mode": "navigate"}
    assert guard._check_hop("GET", "http://demo-site:8410/logout", {}) is False
    assert guard._check_hop("GET", "http://demo-site:8410/profile", nav) is False
    assert guard._check_hop("GET", "http://demo-site:8410/profile.png", {}) is True
    assert guard._check_hop("GET", "http://demo-site:8410/apply/2", nav) is True
    rows = {(r.reason, r.resource_type, r.decision): r.count for r in guard.drain_counts()}
    assert rows == {("deny", UNROUTED, "blocked"): 1, ("not_allowed", UNROUTED, "blocked"): 1}


class _FakeRequest:
    def __init__(self, url: str, *, redirected: bool, navigation: bool = True) -> None:
        self.url, self.method, self.resource_type = url, "GET", "document"
        self.redirected_from = object() if redirected else None
        self._navigation = navigation

    @property
    def frame(self) -> Any:
        raise PlaywrightError("no frame")  # as for a hop no page can be attributed to

    def is_navigation_request(self) -> bool:
        return self._navigation


def test_an_https_redirect_hop_to_a_denied_path_is_counted_as_reached() -> None:
    """Nothing can refuse an HTTPS redirect hop; it is counted as allowed with reason ``deny``
    (``scope_paths_reached``), so no record claims the list held."""
    guard = ScanGuard(
        host_allowed=SCOPE.network_host_allowed, path_verdict=SCOPE.request_path_verdict
    )
    for request in (
        _FakeRequest("https://demo-site/logout", redirected=True),
        _FakeRequest("https://demo-site/profile", redirected=True),
        _FakeRequest("https://demo-site/profile", redirected=True, navigation=False),
        _FakeRequest("https://demo-site/apply/2", redirected=True),
        _FakeRequest("https://demo-site/logout", redirected=False),  # routed: the route decides
        _FakeRequest("http://demo-site/logout", redirected=True),  # plain HTTP: the proxy decides
        _FakeRequest("https://other.example/logout", redirected=True),  # tunnel refused
    ):
        guard._on_request(request)  # type: ignore[arg-type]
    rows = {(r.reason, r.decision): r.count for r in guard.drain_counts()}
    assert rows == {("deny", "allowed"): 1, ("not_allowed", "allowed"): 1}


# --- the rule in a real crawl --------------------------------------------------------------------
@pytest.mark.browser
async def test_the_deny_list_holds_for_clicks_redirects_and_frames(
    chromium: Any,
    scan_settings: Settings,
    scan_engine: PrivacyEngine,
    deny_site: tuple[str, Counter[str]],
) -> None:
    from dps.layers.scanner.crawler import ScanRunner

    base, hits = deny_site
    sink = MemoryScanSink()
    rt = make_runtime(
        base,
        scan_settings,
        scan_engine,
        path="/",
        cookies=False,
        screenshots=False,
        deny=("/logout", "/admin"),
        max_depth=2,
    )
    await ScanRunner(
        settings=scan_settings, engine=scan_engine, sink=sink, browser=chromium, runtime=rt
    ).run()
    denied = {p: hits[p] for p in ("/logout", "/admin/purge", "/admin/frame")}
    assert denied == {"/logout": 0, "/admin/purge": 0, "/admin/frame": 0}
    assert hits["/settings"] >= 1 and hits["/go"] >= 1  # controls: the scan did click and follow
    # the logout click, the purge click, the frame and the redirect hop: each refused, and counted
    assert _blocked(sink, "deny", "document") >= 3  # two clicks and the frame, at the route
    assert _blocked(sink, "deny", "unrouted") >= 1  # the redirect hop, at the scope proxy
    assert _allowed_with(sink, ("deny", "not_allowed")) == 0
    # no record says a denied page was visited; the navigation log shows each refusal
    rows = {p.url_redacted.removeprefix(base): p for p in sink.pages.values()}
    for path in ("/logout", "/admin/purge", "/admin/frame"):
        assert path not in rows or (rows[path].status, rows[path].status_reason) == (
            "skipped",
            "deny",
        ), path
    refusals = [e for e in sink.events if e.kind == "request" and e.note_code == "deny"]
    assert len(refusals) >= 4


@pytest.mark.browser
async def test_the_allow_list_holds_for_navigations_not_for_resources(
    chromium: Any,
    scan_settings: Settings,
    scan_engine: PrivacyEngine,
    allow_site: tuple[str, Counter[str]],
) -> None:
    from dps.layers.scanner.crawler import ScanRunner

    base, hits = allow_site
    sink = MemoryScanSink()
    rt = make_runtime(
        base,
        scan_settings,
        scan_engine,
        path="/home",
        cookies=False,
        screenshots=False,
        deny=(),
        allow=("/home",),
        max_depth=2,
    )
    await ScanRunner(
        settings=scan_settings, engine=scan_engine, sink=sink, browser=chromium, runtime=rt
    ).run()
    assert {p: hits[p] for p in ("/elsewhere", "/outside")} == {"/elsewhere": 0, "/outside": 0}
    assert hits["/home/settings"] >= 1 and hits["/home/go"] >= 1  # controls
    assert hits["/static/logo.png"] >= 1  # a sub-resource is not a navigation
    assert _blocked(sink, "not_allowed", "document") >= 1  # the click, at the route
    assert _blocked(sink, "not_allowed", "unrouted") >= 1  # the redirect hop, at the proxy
    assert _allowed_with(sink, ("deny", "not_allowed")) == 0


@pytest.mark.browser
async def test_spa_discovery_never_clicks_a_hash_route_the_deny_list_names(
    chromium: Any,
    scan_settings: Settings,
    scan_engine: PrivacyEngine,
    hash_site: tuple[str, Counter[str]],
) -> None:
    """Review r2 B1: ``<a href="#/logout">`` is not clicked (its route handler would run); the
    record says it was skipped as ``deny``, and a value-free ``spa_click`` skip event says why."""
    from dps.layers.scanner.crawler import ScanRunner

    base, hits = hash_site
    sink = MemoryScanSink()
    rt = make_runtime(
        base,
        scan_settings,
        scan_engine,
        path="/",
        cookies=False,
        screenshots=False,
        deny=("/logout", "/admin"),
        max_depth=2,
    )
    await ScanRunner(
        settings=scan_settings, engine=scan_engine, sink=sink, browser=chromium, runtime=rt
    ).run()
    assert {p: hits[p] for p in ("/api/session/end", "/api/cache/purge")} == {
        "/api/session/end": 0,
        "/api/cache/purge": 0,
    }
    assert hits["/api/profile"] >= 1  # control: discovery clicked the allowed route
    skips = [e for e in sink.events if e.kind == "skip" and e.note_code == "deny"]
    assert len(skips) >= 2
    assert all("#" not in e.target and "?" not in e.target for e in skips)  # redacted: no fragment
    refused = [e for e in sink.events if e.kind == "spa" and e.result == "skipped"]
    assert len(refused) >= 2 and {e.note_code for e in refused} == {"deny"}  # one per refusal
    assert all("#" not in e.target and "?" not in e.target for e in refused)
    clicked = [p for p in sink.pages.values() if p.discovered_via == "spa_click"]
    assert clicked and all(p.status != "skipped" for p in clicked)
    denied = [p for p in sink.pages.values() if (p.status, p.status_reason) == ("skipped", "deny")]
    assert denied


@pytest.mark.browser
async def test_the_replay_click_never_clicks_a_hash_route_the_deny_list_names(
    chromium: Any,
    scan_settings: Settings,
    scan_engine: PrivacyEngine,
    flip_site: tuple[str, Counter[str]],
) -> None:
    """Review r2 B1, replay half: the queued route's replay finds its clickable now pointing to
    ``#/logout``; it is not clicked and the route's page is recorded as skipped with ``deny``."""
    from dps.layers.scanner.crawler import ScanRunner

    base, hits = flip_site
    sink = MemoryScanSink()
    rt = make_runtime(
        base,
        scan_settings,
        scan_engine,
        path="/",
        cookies=False,
        screenshots=False,
        deny=("/logout",),
        max_depth=2,
    )
    await ScanRunner(
        settings=scan_settings, engine=scan_engine, sink=sink, browser=chromium, runtime=rt
    ).run()
    assert hits["/api/profile"] >= 1  # control: discovery clicked the route while it was allowed
    assert hits["/api/session/end"] == 0
    replays = [p for p in sink.pages.values() if p.discovered_via == "spa_click"]
    assert [(p.status, p.status_reason) for p in replays] == [("skipped", "deny")]
    refused = [e for e in sink.events if e.kind == "navigate" and e.result == "skipped"]
    assert [e.note_code for e in refused] == ["deny"]


# --- M4 verify r1 R3-B1: a click whose own handler reached a denied route -----------------------
async def _crawl(
    chromium: Any, settings: Settings, engine: PrivacyEngine, base: str
) -> tuple[MemoryScanSink, Any]:
    from dps.layers.scanner.crawler import ScanRunner

    sink = MemoryScanSink()
    rt = make_runtime(
        base,
        settings,
        engine,
        path="/",
        cookies=False,
        screenshots=False,
        deny=("/logout",),
        max_depth=2,
    )
    outcome = await ScanRunner(
        settings=settings, engine=engine, sink=sink, browser=chromium, runtime=rt
    ).run()
    return sink, outcome


@pytest.mark.browser
@pytest.mark.parametrize("home", [ONCLICK_HASH_HOME, PUSHSTATE_HOME], ids=["hash", "pushstate"])
async def test_a_click_whose_handler_reached_a_denied_route_is_recorded_as_reached(
    chromium: Any, scan_settings: Settings, scan_engine: PrivacyEngine, home: bytes
) -> None:
    """The discovery click on ``href="#"`` ran a denied route in the same document (the counting
    server proves it: ``/api/session/end`` was called). No record may say the deny list held for
    it: no skipped ``deny`` row and no queued route for it; a value-free ``spa``/``reached`` event
    names the refused route instead, and ``scope_routes_reached`` counts it."""
    for base, hits in _serve(home):
        sink, outcome = await _crawl(chromium, scan_settings, scan_engine, base)
        assert hits["/api/session/end"] >= 1  # the route ran: the scanner cannot prevent this
        assert hits["/api/profile"] >= 1 and hits["/logout"] == 0  # control; no request to it
        reached = [e for e in sink.events if e.kind == "spa" and e.result == "reached"]
        assert reached and {e.note_code for e in reached} == {"deny"}
        assert all("#" not in e.target and "?" not in e.target for e in reached)
        assert outcome.scope_routes_reached == len(reached)
        assert not [p for p in sink.pages.values() if p.status_reason == "deny"]
        assert not [e for e in sink.events if e.kind == "skip"]
        queued = [p for p in sink.pages.values() if p.discovered_via == "spa_click"]
        assert all(p.status == "done" for p in queued)  # only the allowed route was queued


@pytest.mark.browser
async def test_a_replay_click_that_lands_on_a_denied_route_stops_that_tab(
    chromium: Any, scan_settings: Settings, scan_engine: PrivacyEngine
) -> None:
    """The replay of a queued route clicks the same ``href="#"`` element, whose handler now routes
    to ``#/logout``: the tab is not processed (no extraction, no fill), the route's row is failed
    with ``scope_reached`` (not skipped with ``deny``), and a ``spa``/``reached`` event counts it."""
    for base, hits in _serve(FLIP_HANDLER_HOME):
        sink, outcome = await _crawl(chromium, scan_settings, scan_engine, base)
        assert hits["/api/profile"] >= 1  # discovery queued the route while it was allowed
        assert hits["/api/session/end"] >= 1  # the replay's click ran the denied route
        replays = [p for p in sink.pages.values() if p.discovered_via == "spa_click"]
        assert [(p.status, p.status_reason) for p in replays] == [("failed", "scope_reached")]
        assert not [p for p in sink.pages.values() if p.status_reason == "deny"]
        reached = [e for e in sink.events if e.kind == "spa" and e.result == "reached"]
        assert reached and outcome.scope_routes_reached == len(reached)
        assert {e.page_no for e in reached} >= {replays[0].page_no}
