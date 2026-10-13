"""Request interception for the UI scanner — decision D4: DPS never submits anything.

The guard is installed on the scan's BrowserContext before the first page exists, so it sees every
request of every page, popup and iframe of that context:

* **Methods.** Only GET and HEAD leave the browser. Every other method — a form POST, an XHR/fetch
  autosave, a ``navigator.sendBeacon`` ping, PUT/PATCH/DELETE, a CORS preflight — is aborted,
  whatever page issued it and whether or not the page was filled. A GET that asks the server to
  treat it as another method (an ``X-HTTP-Method-Override``-style header or a ``_method`` query
  key) counts as that other method. There is no switch to change any of this.
* **Hosts, on every hop.** Requests to hosts outside ``CrawlScope.network_host_allowed`` are
  aborted by the route handler. Playwright routes only the first hop of a request, so the context
  is also created with the guard's **scope proxy** (``proxy.ScopeProxy``, wired by
  ``browser.new_scan_context``). Chromium opens every connection of the context through it,
  including redirect hops and worker traffic. The proxy refuses a host the scope does not allow;
  for plain HTTP (and plaintext inside a tunnel: a ``ws://`` socket) it also applies the method
  rule and the fill-value rule. A hop it refuses is counted with resource type ``unrouted`` and
  page ``None``.
* **Filled pages.** From ``await arm(page)`` until the page is closed, every navigation request
  (any frame — a GET form submission is a navigation) and every request a script makes (XHR,
  fetch, EventSource, beacons and any other non-passive type) is aborted: such a request could
  carry a fill value in a header or encoded beyond recognition. Only passive resources
  (stylesheets, images, fonts, scripts, media) still load. A popup opened by a filled page is a
  filled page. ``release`` never un-fills a page that is still open.
* **Locked documents.** ``arm`` also *locks* every frame of the page (``ISOLATE_JS`` lock): the
  workers it started are terminated, and ``navigator.sendBeacon`` (returns ``false``), the
  ``keepalive`` flag of ``fetch`` (dropped), ``new WebSocket`` and ``new Worker`` (refused) stop
  working. From the first ``arm`` on, every new document of the scan is locked at birth
  (``LOCK_JS``). These are the channels a route cannot see: a dedicated worker's socket, and
  requests that outlive their page. Each refused use is counted (reason ``api_removed``, resource
  type ``beacon``/``keepalive``/``websocket``/``worker``) through a page binding.
* **Fill values.** Once a page has been armed, any request on any page of the scan whose URL
  carries a fill value (normalised, decoded up to four times) is aborted: a draft the page kept
  in local storage can resurface on a later page.
* **Closing pages.** A request a page sends while its target is torn down is not routed (Chromium
  sends ``sendBeacon``, ``keepalive`` and even plain fetches from ``pagehide``/``unload``
  handlers), and switching the route to "abort everything" does not change that. So every page,
  popup and error page is closed with ``ScanGuard.close(page)`` (``browser.close_scan_context``
  for a whole context): the route aborts everything the page sends (reason ``closing``), the page
  is navigated to ``about:blank`` while the route is live, so its unload handlers run then (a
  ``beforeunload`` prompt is answered "leave"), and only then is it closed with
  ``run_before_unload=False``. A page that cannot be navigated away stays open, every request of
  it aborted, and goes with the context: closing a context runs no unload handlers.
* **WebSockets** of a page are closed before they connect (``connect_to_server`` is never called).
* **Isolation script** (``ISOLATE_JS``, always installed in every frame before any page script):
  service-worker registration is refused in every realm, ``window.close()`` is a no-op,
  ``SharedWorker`` and ``RTCPeerConnection`` are gone, ``beforeunload`` listeners are ignored,
  and the lockable wrappers above are in place (installed at birth, so a page cannot keep a
  reference to an original).
* **JavaScript hardening** (``HARDEN_JS``, on by default) turns ``form.submit()`` and
  ``form.requestSubmit()`` into counted no-ops and cancels every submit event in the capture
  phase, so a page whose JS submits on change does not even attempt the request. It is defence in
  depth: ``tests/scanner/test_no_submit.py`` proves zero submissions with it switched off.
* **Fail closed.** If the decision cannot be computed, the request is aborted (``guard_error``).

The guard depends on how the browser and the context are made (``browser.py``):
``service_workers="block"`` plus ``ISOLATE_JS``, because Playwright cannot route what a service
worker sends or intercepts; ``LAUNCH_ARGS`` (``--disable-shared-workers``: a SharedWorker's
requests and sockets are never routed; ``--disable-quic`` and
``--force-webrtc-ip-handling-policy=disable_non_proxied_udp``: no UDP around the proxy); and the
scope proxy on the context.

Residual risks, outside what a route or the proxy can see. They are not submissions, and they
cannot carry a form value without page JS built to do it:

* The method and URL of an HTTPS request that is not routed: a redirect hop (only to an allowed
  host, and a hop of a GET is a GET); a ``wss://`` socket of a dedicated worker of an *unfilled*
  page (filled pages terminate their workers and refuse new ones); and a speculation-rules
  prefetch, which Chromium fetches itself, unrouted, even for a filled page (a GET to an allowed
  host; over plain HTTP the proxy refuses it by its ``Sec-Purpose`` header, and no launch flag of
  this Chromium turns it off).
* A window that a page of *another* origin closes (a cross-origin ``w.close()`` reaches the
  browser's own ``close``) runs its unload handlers during teardown. That page is an unfilled one
  (a filled page's popups never load), the proxy holds those requests to allowed hosts, and over
  plain HTTP it refuses every non-GET.
* Reporting-API reports (``Report-To``), sent through the proxy, so only to allowed hosts.

Counters carry the method (non-standard methods as ``OTHER``), resource type, decision, reason and
a value-free host (``stored_host``) — never a URL or a value. ``BlockedRequest.url`` stays in
worker memory for the crawler to redact, and is excluded from ``repr``.
"""

from __future__ import annotations

import contextlib
import re
from collections import Counter
from collections.abc import Callable, Collection, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Literal
from urllib.parse import parse_qsl, unquote_plus, urlsplit

from playwright.async_api import BrowserContext, Dialog, Page, Request, Route, WebSocketRoute
from playwright.async_api import Error as PlaywrightError

from dps.engine.verify import normalise
from dps.layers.scanner.models import RequestCount
from dps.layers.scanner.proxy import ProxySettings, ScopeProxy
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
#: resource type of a hop only the scope proxy saw (a redirect hop or a request no route saw)
UNROUTED = "unrouted"
CLOSE_NAVIGATION_TIMEOUT_MS = 5000  # ``ScanGuard.close``: the about:blank navigation
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

#: Always installed, in every frame, before any page script (unlike the optional ``HARDEN_JS``): it
#: keeps every request where the route or the proxy can see it.
#:
#: * Playwright's ``service_workers="block"`` only replaces ``navigator.serviceWorker.register`` on
#:   the instance, so registration through ``ServiceWorkerContainer.prototype.register`` is refused
#:   here too; nothing a service worker sends is routed.
#: * ``window.close()`` is a no-op: a window that closes itself runs its unload handlers while its
#:   target is torn down, where no route sees their requests (``ScanGuard.close`` navigates away
#:   first instead). ``beforeunload`` listeners are ignored, so no prompt holds that navigation.
#: * ``SharedWorker`` (also off by ``LAUNCH_ARGS``) and ``RTCPeerConnection`` are deleted.
#: * ``sendBeacon``, ``fetch`` (``keepalive``), ``WebSocket`` and ``Worker`` are wrapped *now*, so a
#:   page never holds an original; the wrappers refuse once the document is locked
#:   (``__dpsGuardLock``, called by ``ScanGuard.arm`` and by ``LOCK_JS``), and locking terminates
#:   the workers the document started. The wrappers stay writable (frameworks such as zone.js wrap
#:   them again and call through); ``__dpsGuardLock`` cannot be replaced.
ISOLATE_JS = r"""
(() => {
  const W = window;
  const fixed = (o, k, v) => { try { Object.defineProperty(o, k, { value: v, configurable: false, writable: false, enumerable: false }); } catch (e) {} };
  const swap = (o, k, v) => { try { Object.defineProperty(o, k, { value: v, configurable: true, writable: true, enumerable: false }); } catch (e) {} };
  const R0 = W.__dpsGuardReport;
  const report = (kind, method, url) => {
    try {
      const r = typeof R0 === 'function' ? R0 : W.__dpsGuardReport;
      let u = '';
      try { u = new URL(String(url), W.location.href).href; } catch (e) {}
      if (typeof r === 'function') { Promise.resolve(r(kind, String(method || 'GET'), u)).catch(() => 0); }
    } catch (e) {}
  };
  const refused = (what) => new DOMException(what + ' is blocked on a filled page in a DPS scan', 'SecurityError');
  let locked = false;
  const workers = new Set();

  const C = W.ServiceWorkerContainer;
  if (C) {
    fixed(C.prototype, 'register', function () { return Promise.reject(new DOMException('service workers are blocked in a DPS scan', 'SecurityError')); });
  }
  fixed(W, 'close', function () {});
  for (const name of ['SharedWorker', 'RTCPeerConnection', 'webkitRTCPeerConnection']) {
    try { delete W[name]; } catch (e) {}
  }

  const ET = W.EventTarget && W.EventTarget.prototype;
  const addListener = ET && ET.addEventListener;
  if (addListener) {
    swap(ET, 'addEventListener', function addEventListener(type, listener, options) {
      if (this === W && type === 'beforeunload') { return undefined; }
      return addListener.call(this, type, listener, options);
    });
  }
  try { Object.defineProperty(W, 'onbeforeunload', { get() { return null; }, set(v) {}, configurable: false }); } catch (e) {}

  const NP = W.Navigator && W.Navigator.prototype;
  const beacon = NP && NP.sendBeacon;
  if (beacon) {
    swap(NP, 'sendBeacon', function sendBeacon(url) {
      if (locked) { report('beacon', 'POST', url); return false; }
      return beacon.apply(this, arguments);
    });
  }

  const fetchFn = W.fetch;
  const Req = W.Request;
  if (fetchFn) {
    swap(W, 'fetch', function fetch(input, init) {
      if (locked) {
        // build the Request once (as fetch would): its flags cannot change after this read
        let req;
        try { req = new Req(input, init); } catch (e) { return Promise.reject(e); }
        if (req.keepalive) {
          report('keepalive', req.method, req.url);
          try { req = new Req(req, { keepalive: false }); } catch (e) { return Promise.reject(refused('keepalive')); }
        }
        return fetchFn.call(this, req);
      }
      return fetchFn.call(this, input, init);
    });
  }

  const wrapCtor = (name, onCreate) => {
    const Orig = W[name];
    if (typeof Orig !== 'function') { return; }
    const Wrapped = function (url) {
      if (!new.target) { throw new TypeError("Failed to construct '" + name + "': Please use the 'new' operator."); }
      if (locked) { report(name === 'WebSocket' ? 'websocket' : 'worker', 'GET', url); throw refused(name); }
      const made = Reflect.construct(Orig, arguments, new.target === Wrapped ? Orig : new.target);
      if (onCreate) { onCreate(made); }
      return made;
    };
    Wrapped.prototype = Orig.prototype;
    for (const k of Object.getOwnPropertyNames(Orig)) {
      if (!(k in Wrapped)) { try { Object.defineProperty(Wrapped, k, Object.getOwnPropertyDescriptor(Orig, k)); } catch (e) {} }
    }
    swap(Orig.prototype, 'constructor', Wrapped);
    swap(W, name, Wrapped);
  };
  wrapCtor('WebSocket', null);
  wrapCtor('Worker', (w) => workers.add(w));

  fixed(W, '__dpsGuardLock', function () {
    locked = true;
    for (const w of workers) { try { w.terminate(); } catch (e) {} }
    workers.clear();
    return true;
  });
})();
"""

#: Added to the context at the first ``arm``: every later document is locked at birth.
LOCK_JS = "window.__dpsGuardLock && window.__dpsGuardLock();"
_LOCK_CALL = "() => typeof window.__dpsGuardLock === 'function' && window.__dpsGuardLock() === true"
#: the page binding ``ISOLATE_JS`` reports refused uses through, and what it may report
REPORT_BINDING = "__dpsGuardReport"
REPORTED_KINDS: frozenset[str] = frozenset({"beacon", "keepalive", "websocket", "worker"})
_MAX_REPORTED_URL = 8192


@dataclass(frozen=True, slots=True)
class Decision:
    """The guard's verdict on one request.

    ``reason``: ``ok`` | ``scheme`` | ``method`` | ``host`` | ``filled_navigation`` |
    ``filled_value`` | ``filled_request`` | ``closing`` | ``guard_error``; at the scope proxy also
    ``service_worker`` and ``speculative``; for a use of a locked API (no request exists)
    ``api_removed``.
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
    """Installs the interception on one scan's BrowserContext and keeps value-free counters.

    Lifecycle: ``start_proxy()`` before the context exists (its result is the context's
    ``proxy=``), ``install(context)`` before the first page, ``close(page)`` for every page, and
    ``stop_proxy()`` after the context is closed. ``browser.new_scan_context`` does the first two,
    ``browser.close_scan_context`` the last two.
    """

    def __init__(self, *, host_allowed: Callable[[str], bool]) -> None:
        self._host_allowed = host_allowed
        self._filled: set[Page] = set()
        self._needles: set[str] = set()
        self._page_no: dict[Page, int | None] = {}
        self._counts: Counter[_CountKey] = Counter()
        self._blocked: list[BlockedRequest] = []
        self._closing: set[Page] = set()
        self._closes_in_flight = 0
        self._context: BrowserContext | None = None
        self._locked_at_birth = False
        self._proxy = ScopeProxy(self._check_hop)
        self.blocked_total = 0
        self.websockets_closed = 0
        self.pages_left_open = 0
        self.locks_failed = 0

    @property
    def proxy_running(self) -> bool:
        """Whether the scope proxy is listening."""
        return self._proxy.running

    async def start_proxy(self) -> ProxySettings:
        """Start the scope proxy (idempotent); the result is the context's ``proxy=`` option."""
        return await self._proxy.start()

    async def stop_proxy(self) -> None:
        """Stop the scope proxy (idempotent). Call it after the context is closed."""
        await self._proxy.close()

    async def install(self, context: BrowserContext, *, harden_js: bool = True) -> None:
        """Route every request and WebSocket of ``context`` through the guard (before any page).

        ``context`` must have been created with ``proxy=await self.start_proxy()``.
        """
        self._context = context
        # first: Playwright's WebSocket routing replaces ``window.WebSocket`` in every document,
        # and ``ISOLATE_JS`` must wrap that replacement, not be replaced by it
        await context.route_web_socket(re.compile(r".*"), self._close_websocket)
        await context.expose_binding(REPORT_BINDING, self._report)
        await context.add_init_script(script=ISOLATE_JS)
        if harden_js:
            await context.add_init_script(script=HARDEN_JS)
        await context.route("**/*", self._handle)

    def bind(self, page: Page, page_no: int | None) -> None:
        """Attribute ``page``'s requests to crawl page ``page_no``."""
        self._page_no[page] = page_no

    async def arm(self, page: Page, fill_values: Iterable[str]) -> bool:
        """Mark ``page`` filled and lock its documents; call it before any value is set.

        Needles stay for the rest of the scan and are checked on every page: a later page that
        would carry an earlier persona's value in a URL is caught too. The route rules apply at
        once. Then every frame of the page is locked (its workers terminated; beacons, keepalive,
        sockets and new workers refused) and, from the first call on, every new document of the
        scan is locked at birth. Returns ``False`` when a live frame could not be locked
        (``locks_failed``): the caller must not fill that page.
        """
        for value in fill_values:
            needle = normalise(value)
            if len(needle) >= MIN_NEEDLE:
                self._needles.add(needle)
        self._mark_filled(page)
        return await self._lock(page)

    async def _lock(self, page: Page) -> bool:
        ok = True
        if self._context is not None and not self._locked_at_birth:
            try:
                await self._context.add_init_script(script=LOCK_JS)
                self._locked_at_birth = True
            except PlaywrightError:
                ok = False
        for frame in list(page.frames):
            try:
                done = bool(await frame.evaluate(_LOCK_CALL))
            except PlaywrightError:
                done = False
            if not done and not frame.is_detached():
                ok = False
        if not ok:
            self.locks_failed += 1
        return ok

    def release(self, page: Page) -> None:
        """Forget ``page``'s attribution. A filled page stays filled until it is closed."""
        self._page_no.pop(page, None)
        if page.is_closed():
            self._filled.discard(page)

    def is_closing(self, page: Page) -> bool:
        """Whether ``page`` is being closed, or was left open by ``close`` (everything it sends is
        aborted)."""
        return page in self._closing

    async def close(self, page: Page) -> bool:
        """Close ``page`` so that nothing it sends while it goes leaves the browser.

        From now on the route aborts every request of the page (reason ``closing``; while a close
        is in flight also every request no page can be found for). The page is then navigated to
        ``about:blank`` (not routed, so allowed from a filled page too): its ``pagehide`` /
        ``visibilitychange`` / ``unload`` handlers run during that navigation, while the route is
        live, and a ``beforeunload`` prompt is answered "leave". Only then is the page closed, with
        ``run_before_unload=False``. A request sent while a page's target is torn down is not
        routed, so a page is never closed without that navigation: if it fails (a hung renderer),
        the page is left open with every request aborted and goes with the context (closing a
        context runs no unload handlers); ``pages_left_open`` counts it. Returns whether the page
        is closed. Use it for every page, popup and error page.
        """
        if page.is_closed():
            self._forget(page)
            return True
        if page not in self._closing:
            self._closing.add(page)
            page.once("close", self._forget)
        self._closes_in_flight += 1
        page.on("dialog", self._leave)
        try:
            await page.goto("about:blank", wait_until="commit", timeout=CLOSE_NAVIGATION_TIMEOUT_MS)
        except PlaywrightError:
            if not page.is_closed() and page.url != "about:blank":
                self.pages_left_open += 1
                return False
        finally:
            self._closes_in_flight -= 1
            with contextlib.suppress(Exception):
                page.remove_listener("dialog", self._leave)
        with contextlib.suppress(PlaywrightError):
            await page.close(run_before_unload=False)
        self._forget(page)
        return True

    @staticmethod
    async def _leave(dialog: Dialog) -> None:
        """While a page is being closed: "leave" for a ``beforeunload`` prompt, dismiss others."""
        with contextlib.suppress(PlaywrightError):
            if dialog.type == "beforeunload":
                await dialog.accept()
            else:
                await dialog.dismiss()

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
        if page.is_closed():
            self._closing.discard(page)

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
        url = request.url
        host = (urlsplit(url).hostname or "").lower()
        allowed = bool(host) and self._host_allowed(host)
        closing = page in self._closing if page is not None else self._closes_in_flight > 0
        if closing:
            decision = Decision(False, "closing")
        else:
            decision = decide(
                method=request.method,
                url=url,
                is_navigation=request.is_navigation_request(),
                page_filled=await self._is_filled(page),
                host_allowed=allowed,
                needles=self._needles,
                resource_type=request.resource_type,
                method_override=any(k.lower() in OVERRIDE_HEADERS for k in request.headers),
            )
        page_no = self._page_no.get(page) if page is not None else None
        return decision, page_no, stored_method(request.method), stored_host(host, allowed=allowed)

    def _record(
        self,
        decision: Decision,
        page_no: int | None,
        method: str,
        resource_type: str | None,
        host: str,
        url: str,
    ) -> None:
        verdict: Literal["allowed", "blocked"] = "allowed" if decision.allow else "blocked"
        self._counts[(page_no, method, resource_type, verdict, decision.reason, host)] += 1
        SCAN_REQUESTS.labels(verdict, method, decision.reason).inc()
        if decision.allow:
            return
        self.blocked_total += 1
        if len(self._blocked) < MAX_BLOCKED_KEPT:
            self._blocked.append(
                BlockedRequest(page_no, method, resource_type, decision.reason, url)
            )

    def _report(
        self, source: Mapping[str, object], kind: object, method: object, url: object
    ) -> None:
        """``ISOLATE_JS`` reports a refused use of a locked API (no request exists to route).

        Page-callable, so only a known ``kind`` is counted, the method and host are reduced to
        their value-free forms like any other, and the URL stays in memory only."""
        if not isinstance(kind, str) or kind not in REPORTED_KINDS:
            return
        text = url[:_MAX_REPORTED_URL] if isinstance(url, str) else ""
        try:
            host = (urlsplit(text).hostname or "").lower()
            allowed = bool(host) and self._host_allowed(host)
        except ValueError:
            host, allowed = "", False
        page = source.get("page")
        page_no = self._page_no.get(page) if isinstance(page, Page) else None
        verb = stored_method(method) if isinstance(method, str) else "OTHER"
        stored = stored_host(host, allowed=allowed)
        self._record(Decision(False, "api_removed"), page_no, verb, kind, stored, text)

    def _check_hop(self, method: str, url: str, headers: Mapping[str, str]) -> bool:
        """The scope proxy's question for one hop (``CONNECT`` passes ``https://host:port/``).

        Plain HTTP gets the whole unfilled-page policy (a body or an upgrade is not a GET); a
        tunnel gets the host rule, since what it carries is TLS. Only refusals are counted: an
        allowed hop was usually routed, and counted, already.
        """
        host, allowed = "", False
        try:
            host = (urlsplit(url).hostname or "").lower()
            allowed = bool(host) and self._host_allowed(host)
            if method.upper() == "CONNECT":
                decision = Decision(True, "ok") if allowed else Decision(False, "host")
            elif "service-worker" in headers:  # a service worker's script (ISOLATE_JS stops it)
                decision = Decision(False, "service_worker")
            elif "sec-purpose" in headers:  # a speculative prefetch/prerender: never routed
                decision = Decision(False, "speculative")
            else:
                length = headers.get("content-length", "0").strip()
                body = length not in ("", "0") or "transfer-encoding" in headers
                decision = decide(
                    method="POST" if body or "upgrade" in headers else method,
                    url=url,
                    is_navigation=False,
                    page_filled=False,
                    host_allowed=allowed,
                    needles=self._needles,
                    method_override=any(k in OVERRIDE_HEADERS for k in headers),
                )
        except Exception:  # noqa: BLE001 - fail closed: an undecidable hop never leaves
            decision = Decision(False, "guard_error")
        if not decision.allow:
            stored = stored_host(host, allowed=allowed)
            self._record(decision, None, stored_method(method), UNROUTED, stored, url)
        return decision.allow

    async def _handle(self, route: Route) -> None:
        request = route.request
        try:
            decision, page_no, method, host = await self._decide(request)
        except Exception:  # noqa: BLE001 - fail closed: an undecidable request never leaves
            decision, page_no, method, host = Decision(False, "guard_error"), None, "OTHER", "-"
        self._record(decision, page_no, method, request.resource_type, host, request.url)
        try:
            if decision.allow:
                await route.continue_()
                return
            await route.abort("blockedbyclient")
        except PlaywrightError:
            return  # the page or context closed while the request was paused

    async def _close_websocket(self, ws: WebSocketRoute) -> None:
        self.websockets_closed += 1
        try:
            await ws.close()
        except PlaywrightError:
            return
