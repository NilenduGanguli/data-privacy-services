"""Request interception for the UI scanner — decision D4: DPS never submits anything.

The guard is installed on the scan's BrowserContext before the first page exists, so it sees every
request of every page, popup and iframe of that context:

* **Methods.** Only GET and HEAD leave the browser. Every other method — a form POST, an XHR/fetch
  autosave, a ``navigator.sendBeacon`` ping, PUT/PATCH/DELETE, a CORS preflight — is aborted,
  whatever page issued it and whether or not the page was filled. A GET that asks the server to
  treat it as another method (an ``X-HTTP-Method-Override``-style header or a ``_method`` query
  key) counts as that other method. There is no switch to change any of this.
* **Hosts.** Requests to hosts outside ``CrawlScope.network_host_allowed`` are aborted.
* **Filled pages.** From ``arm(page)`` until the page is closed, every navigation request (any
  frame — a GET form submission is a navigation) and every request a script makes (XHR, fetch,
  EventSource, beacons and any other non-passive type) is aborted: such a request could carry a
  fill value in a header or encoded beyond recognition. Only passive resources (stylesheets,
  images, fonts, scripts, media) still load. A popup opened by a filled page is a filled page.
  ``release`` never un-fills a page that is still open.
* **Fill values.** Once a page has been armed, any request on any page of the scan whose URL
  carries a fill value (normalised, decoded up to four times) is aborted: a draft the page kept
  in local storage can resurface on a later page.
* **WebSockets** are closed before they connect (``connect_to_server`` is never called).
* **JavaScript hardening** (``HARDEN_JS``, on by default) turns ``form.submit()`` and
  ``form.requestSubmit()`` into counted no-ops and cancels every submit event in the capture phase,
  so a page whose JS submits on change does not even attempt the request. It is defence in depth:
  ``tests/scanner/test_no_submit.py`` proves zero submissions with it switched off.
* **Fail closed.** If the decision cannot be computed, the request is aborted (``guard_error``).

Playwright cannot route requests that a service worker intercepts, so scan contexts are created
with ``service_workers="block"`` (``browser.new_scan_context``). Playwright routes only the first
hop of a redirect chain; the crawler re-checks the final host after every navigation, and filled
pages never navigate at all.

Counters carry the method (non-standard methods as ``OTHER``), resource type, decision, reason and
a value-free host (``stored_host``) — never a URL or a value. ``BlockedRequest.url`` stays in
worker memory for the crawler to redact, and is excluded from ``repr``.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable, Collection, Iterable
from dataclasses import dataclass, field
from typing import Literal
from urllib.parse import parse_qsl, unquote_plus, urlsplit

from playwright.async_api import BrowserContext, Page, Request, Route, WebSocketRoute
from playwright.async_api import Error as PlaywrightError

from dps.engine.verify import normalise
from dps.layers.scanner.models import RequestCount
from dps.observability import SCAN_REQUESTS

ALLOWED_METHODS: frozenset[str] = frozenset({"GET", "HEAD"})
MIN_NEEDLE = 4
#: request types a filled page may still load (nothing a script can put data into but its URL)
PASSIVE_TYPES: frozenset[str] = frozenset(
    {"stylesheet", "image", "media", "font", "script", "texttrack", "manifest"}
)
#: request headers that ask a server to treat a GET as another method
OVERRIDE_HEADERS: frozenset[str] = frozenset(
    {"x-http-method-override", "x-http-method", "x-method-override"}
)
#: methods recorded by name; anything else (page-chosen tokens) is recorded as OTHER
KNOWN_METHODS: frozenset[str] = frozenset(
    {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "CONNECT", "TRACE"}
)
MAX_BLOCKED_KEPT = 2000  # blocked requests kept in memory for the navigation log
_MAX_DECODES = 4
_PLAIN_LABEL = re.compile(r"^[a-z](?:[a-z-]{0,61}[a-z])?$")

HARDEN_JS = r"""
(() => {
  if (window.__dpsGuard) { return; }
  Object.defineProperty(window, '__dpsGuard', { value: { submitAttempts: 0 }, configurable: false, writable: false });
  const attempt = () => { window.__dpsGuard.submitAttempts += 1; };
  const lock = (proto, name) => Object.defineProperty(proto, name, {
    value: function () { attempt(); }, configurable: false, writable: false });
  lock(HTMLFormElement.prototype, 'submit');
  lock(HTMLFormElement.prototype, 'requestSubmit');
  window.addEventListener('submit', (event) => { attempt(); event.preventDefault(); event.stopImmediatePropagation(); }, true);
})();
"""


@dataclass(frozen=True, slots=True)
class Decision:
    """The guard's verdict on one request.

    ``reason``: ``ok`` | ``scheme`` | ``method`` | ``host`` | ``filled_navigation`` |
    ``filled_value`` | ``filled_request`` | ``guard_error``.
    """

    allow: bool
    reason: str


def _readings(url: str) -> list[str]:
    """``url`` normalised as is and after each round of percent-decoding (up to four)."""
    out: list[str] = []
    text = url
    for _ in range(_MAX_DECODES + 1):
        out.append(normalise(text))
        decoded = unquote_plus(text)
        if decoded == text:
            break
        text = decoded
    return out


def _carries_value(url: str, needles: Collection[str]) -> bool:
    return any(needle in reading for reading in _readings(url) for needle in needles)


def _overrides_method(url: str) -> bool:
    query = urlsplit(url).query
    return any(key.strip().lower() == "_method" for key, _ in parse_qsl(query, True))


def decide(
    *,
    method: str,
    url: str,
    is_navigation: bool,
    page_filled: bool,
    host_allowed: bool,
    needles: Collection[str],
    resource_type: str | None = None,
    method_override: bool = False,
) -> Decision:
    """The whole policy as a pure function (the route handler only gathers its inputs).

    ``resource_type`` ``None`` means unknown and is treated as a script request on a filled page;
    ``method_override`` is true when the request carries a method-override header.
    """
    if urlsplit(url).scheme.lower() not in ("http", "https"):
        return Decision(False, "scheme")
    if method.upper() not in ALLOWED_METHODS or method_override or _overrides_method(url):
        return Decision(False, "method")
    if not host_allowed:
        return Decision(False, "host")
    if page_filled and is_navigation:
        return Decision(False, "filled_navigation")
    if needles and _carries_value(url, needles):
        return Decision(False, "filled_value")
    if page_filled and resource_type not in PASSIVE_TYPES:
        return Decision(False, "filled_request")
    return Decision(True, "ok")


def stored_method(method: str) -> str:
    """The method as counters record it: a standard name, else ``OTHER`` (a page chooses the
    token of a custom method, so it could carry a value)."""
    upper = method.upper()
    return upper if upper in KNOWN_METHODS else "OTHER"


def stored_host(host: str, *, allowed: bool) -> str:
    """The host as counters record it, value-free.

    An allowed host is the start host or a registered one and is kept. Any other host is chosen by
    the page and may carry data in its labels (``<value>.tracker.example``): only its last two
    labels are kept, when both are plain words (``*.`` marks dropped labels); otherwise ``{host}``.
    """
    if not host:
        return "-"
    if allowed:
        return host
    labels = host.lower().rstrip(".").split(".")
    tail = labels[-2:]
    if len(tail) < 2 or not all(_PLAIN_LABEL.match(label) for label in tail):
        return "{host}"
    return ("*." if len(labels) > 2 else "") + ".".join(tail)


@dataclass(frozen=True)
class BlockedRequest:
    """One aborted request, for the navigation log. ``url`` stays in memory (never in ``repr``);
    the crawler redacts it before anything is stored."""

    page_no: int | None
    method: str
    resource_type: str | None
    reason: str
    url: str = field(repr=False)


_CountKey = tuple[int | None, str, str | None, Literal["allowed", "blocked"], str, str]


class ScanGuard:
    """Installs the interception on one scan's BrowserContext and keeps value-free counters."""

    def __init__(self, *, host_allowed: Callable[[str], bool]) -> None:
        self._host_allowed = host_allowed
        self._filled: set[Page] = set()
        self._needles: set[str] = set()
        self._page_no: dict[Page, int | None] = {}
        self._counts: Counter[_CountKey] = Counter()
        self._blocked: list[BlockedRequest] = []
        self.blocked_total = 0
        self.websockets_closed = 0

    async def install(self, context: BrowserContext, *, harden_js: bool = True) -> None:
        """Route every request and WebSocket of ``context`` through the guard (before any page)."""
        if harden_js:
            await context.add_init_script(script=HARDEN_JS)
        await context.route("**/*", self._handle)
        await context.route_web_socket(re.compile(r".*"), self._close_websocket)

    def bind(self, page: Page, page_no: int | None) -> None:
        """Attribute ``page``'s requests to crawl page ``page_no``."""
        self._page_no[page] = page_no

    def arm(self, page: Page, fill_values: Iterable[str]) -> None:
        """Mark ``page`` filled. Needles stay for the rest of the scan and are checked on every
        page: a later page that would carry an earlier persona's value in a URL is caught too."""
        for value in fill_values:
            needle = normalise(value)
            if len(needle) >= MIN_NEEDLE:
                self._needles.add(needle)
        self._mark_filled(page)

    def release(self, page: Page) -> None:
        """Forget ``page``'s attribution. A filled page stays filled until it is closed."""
        self._page_no.pop(page, None)
        if page.is_closed():
            self._filled.discard(page)

    def drain_counts(self) -> list[RequestCount]:
        """The counters since the last drain, as ``dps_scan_request`` rows."""
        out = [
            RequestCount(
                page_no=p, method=m, resource_type=r, decision=d, reason=why, host=h, count=n
            )
            for (p, m, r, d, why, h), n in self._counts.items()
        ]
        self._counts.clear()
        return out

    def drain_blocked(self) -> list[BlockedRequest]:
        """The blocked requests since the last drain (memory only; at most ``MAX_BLOCKED_KEPT``)."""
        out, self._blocked = self._blocked, []
        return out

    def _mark_filled(self, page: Page) -> None:
        if page in self._filled:
            return
        self._filled.add(page)
        page.once("close", self._forget)

    def _forget(self, page: Page) -> None:
        self._filled.discard(page)
        self._page_no.pop(page, None)

    @staticmethod
    def _page_of(request: Request) -> Page | None:
        try:
            return request.frame.page
        except PlaywrightError:  # service worker or detached frame: no page to attribute
            return None

    async def _is_filled(self, page: Page | None) -> bool:
        if page is None:  # unattributable: filled while any page is
            return bool(self._filled)
        if page in self._filled:
            return True
        opener = await page.opener()
        hops = 0
        while opener is not None and hops < 8:  # a popup of a filled page is filled
            if opener in self._filled:
                self._mark_filled(page)
                self._page_no.setdefault(page, self._page_no.get(opener))
                return True
            opener = await opener.opener()
            hops += 1
        return False

    async def _decide(self, request: Request) -> tuple[Decision, int | None, str, str]:
        page = self._page_of(request)
        filled = await self._is_filled(page)
        url = request.url
        host = (urlsplit(url).hostname or "").lower()
        allowed = bool(host) and self._host_allowed(host)
        decision = decide(
            method=request.method,
            url=url,
            is_navigation=request.is_navigation_request(),
            page_filled=filled,
            host_allowed=allowed,
            needles=self._needles,
            resource_type=request.resource_type,
            method_override=any(k.lower() in OVERRIDE_HEADERS for k in request.headers),
        )
        page_no = self._page_no.get(page) if page is not None else None
        return decision, page_no, stored_method(request.method), stored_host(host, allowed=allowed)

    async def _handle(self, route: Route) -> None:
        request = route.request
        try:
            decision, page_no, method, host = await self._decide(request)
        except Exception:  # noqa: BLE001 - fail closed: an undecidable request never leaves
            decision, page_no, method, host = Decision(False, "guard_error"), None, "OTHER", "-"
        verdict: Literal["allowed", "blocked"] = "allowed" if decision.allow else "blocked"
        self._counts[(page_no, method, request.resource_type, verdict, decision.reason, host)] += 1
        SCAN_REQUESTS.labels(verdict, method, decision.reason).inc()
        try:
            if decision.allow:
                await route.continue_()
                return
            self.blocked_total += 1
            if len(self._blocked) < MAX_BLOCKED_KEPT:
                self._blocked.append(
                    BlockedRequest(
                        page_no, method, request.resource_type, decision.reason, request.url
                    )
                )
            await route.abort("blockedbyclient")
        except PlaywrightError:
            return  # the page or context closed while the request was paused

    async def _close_websocket(self, ws: WebSocketRoute) -> None:
        self.websockets_closed += 1
        try:
            await ws.close()
        except PlaywrightError:
            return
