"""Request interception for the UI scanner — decision D4: DPS never submits anything.

The guard is installed on the scan's BrowserContext before the first page exists, so it sees every
request of every page, popup and iframe of that context:

* **Methods.** Only GET and HEAD leave the browser. Every other method — a form POST, an XHR/fetch
  autosave, a ``navigator.sendBeacon`` ping, PUT/PATCH/DELETE, a CORS preflight — is aborted,
  whatever page issued it and whether or not the page was filled. A GET that asks the server to
  treat it as another method (an ``X-HTTP-Method-Override``-style header or a ``_method`` query
  key) counts as that other method. There is no switch to change any of this. The rule does not
  rest on the route alone (M4 verify r2 N1): a popup's first document is scripted by its opener
  before Playwright can route it, and a beacon or ``keepalive`` request outlives its page. So
  ``ISOLATE_JS`` applies it in every realm, before a request exists: ``sendBeacon`` is always
  refused (it is always a POST; counted ``api_removed``), ``keepalive`` is always dropped (counted
  ``api_removed``), and ``fetch`` / ``XMLHttpRequest`` refuse any other method or override (counted
  with reason ``method``, resource type ``fetch`` / ``xhr``, as the route would count them).
* **Hosts, on every hop.** Requests to hosts outside ``CrawlScope.network_host_allowed`` are
  aborted by the route handler. Playwright routes only the first hop of a request, so the context
  is also created with the guard's **scope proxy** (``proxy.ScopeProxy``, wired by
  ``browser.new_scan_context``). Chromium opens every connection of the context through it,
  including redirect hops and worker traffic. The proxy refuses a host the scope does not allow;
  for plain HTTP (and plaintext inside a tunnel: a ``ws://`` socket) it also applies the method
  rule and the fill-value rule. A hop it refuses is counted with resource type ``unrouted`` and
  page ``None``.
* **Addresses, on every hop** (S5, security review). The proxy dials an allowed hop only through
  ``_dial_hop``: the host is resolved once (``netguard.resolve_addresses``) and every address it
  resolves to must pass ``netguard.forbidden_ip_reason`` — no link-local or cloud metadata
  address, multicast, unspecified or reserved address (IPv4-mapped, NAT64 and 6to4 IPv6
  unwrapped), and no loopback unless ``allow_loopback`` (``env=local``) — and the proxy connects
  to those addresses itself, so a registered name that resolves (or is rebound) to the metadata
  endpoint is never reached. A refusal is counted with reason ``host_forbidden``. Chromium
  resolves no name itself (``browser.RESOLVER_RULES``): every lookup of a scan happens here.
* **Scope paths, on every hop.** The scan's deny list holds for every request of the scan (any
  resource type, any frame), and its allow list for every navigation (main frame and sub-frames):
  ``path_verdict`` (``CrawlScope.request_path_verdict``) is applied by the route handler and, for
  plain HTTP, by the scope proxy on every redirect hop. So a navigation the scanner causes itself
  (an SPA discovery click on ``<a href="#" onclick="location='/logout'">`` or a ``javascript:``
  link, a frame, a redirect) never reaches a denied path; each refusal is counted with reason
  ``deny`` / ``not_allowed``. The proxy tells a navigation hop by its ``Sec-Fetch-Mode: navigate``
  / ``Sec-Fetch-Dest`` document-like value, an ``Upgrade-Insecure-Requests`` header or an
  ``Accept`` that asks for HTML.
* **Filled pages.** From ``await arm(page)`` until the page is closed, every navigation request
  (any frame — a GET form submission is a navigation) and every request a script makes (XHR,
  fetch, EventSource, beacons and any other non-passive type) is aborted: such a request could
  carry a fill value in a header or encoded beyond recognition. Only passive resources
  (stylesheets, images, fonts, scripts, media) still load. A popup opened by a filled page is a
  filled page, and so is every page of the context that is open when ``arm`` is called (a popup
  the page opened before the fill, an unrelated tab): the filled page can hand any of them a value
  (``postMessage``, ``BroadcastChannel``, storage). ``release`` never un-fills a page that is
  still open.
* **Locked documents.** ``arm`` also *locks* every frame of every open page of the context
  (``ISOLATE_JS`` lock), not only the armed page's: the workers they started are terminated, and
  ``new WebSocket`` and ``new Worker`` are refused (beacons and ``keepalive`` already are, in
  every document). From the first ``arm`` on, every
  new document of the scan is locked at birth (``LOCK_JS``): ``arm`` registers that script
  *before* it lists the open pages, then locks every page the context reports until one round
  trip later a pass finds none it has not locked, so a popup opened while ``arm`` runs is locked
  either at birth or by that loop (M4 verify r1 F1). ``arm`` returns ``False`` when any live
  frame of any open page could not be locked, or pages kept opening for ``_SETTLE_PASSES``
  passes. These are the channels a route cannot see: a dedicated worker's socket, and
  requests that outlive their page. Each refused use is counted (reason ``api_removed``, resource
  type ``beacon``/``keepalive``/``websocket``/``worker``) through a page binding.
* **Inert documents** (M4 verify r2 N1). A page can open ``window.open('')`` and use the popup's
  realm at once: Playwright has not attached to the new target yet, so nothing that realm sends
  is routed, and over HTTPS the scope proxy sees only the tunnel. A document of a window born at
  ``about:blank``, while it is ``about:blank`` (Chromium swaps the popup's first document for a
  second ``about:blank`` one in the same window, and ``document.open()`` gives a document its
  opener's URL, so a document is marked when it is first seen at ``about:blank``: on
  ``window.open``'s return, on ``document.open``/``write``, or on a request), and every frame
  born in one, is *inert*: ``fetch``, ``XMLHttpRequest``, ``EventSource``, ``WebSocket`` and
  ``Worker`` refuse every request (counted ``api_removed``, through the opener's binding when the
  popup has none yet), and the document gets a ``Content-Security-Policy`` that loads nothing
  (no image, script, style, frame, ping or form action). A navigation of the popup is routed (the
  popup's own target), and the same-origin page it loads is not inert. Filled or not: a popup the
  filled page opens after ``arm`` sends nothing, whether or not the crawler closes it in time.
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
  ``SharedWorker``, ``RTCPeerConnection``, ``fetchLater``, ``WebSocketStream`` and
  ``WebTransport`` are gone on every page, filled or not (no route sees what they send: a
  ``fetchLater`` POST over HTTPS, a stream socket), ``beforeunload`` listeners are ignored, and
  the wrappers above (methods, locks, inert documents) are in place (installed at birth, so a
  page cannot keep a reference to an original).
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
  host, and a hop of a GET is a GET); a ``wss://`` socket (``WebSocket`` or ``WebSocketStream``,
  which ``ISOLATE_JS`` cannot reach in a worker) of a dedicated worker of a page while no page of
  the scan is filled (``arm`` locks every open page of the context, terminating the workers of a
  popup opened before the fill too, and every later document is locked at birth, so no worker
  exists from the first fill on that could be handed a value); and a speculation-rules
  prefetch, which Chromium fetches itself, unrouted, even for a filled page (a GET to an allowed
  host; over plain HTTP the proxy refuses it by its ``Sec-Purpose`` header, and no launch flag of
  this Chromium turns it off).
* A window that a page of *another* origin closes (a cross-origin ``w.close()`` reaches the
  browser's own ``close``) runs its unload handlers during teardown. That page is an unfilled one
  (a filled page's popups never load), the proxy holds those requests to allowed hosts, and over
  plain HTTP it refuses every non-GET.
* Reporting-API reports (``Report-To``), sent through the proxy, so only to allowed hosts.
* A redirect hop over HTTPS to a denied (or, for a navigation, not allowed) path of an allowed
  host: neither the route (first hops only) nor the proxy (it sees the tunnel, not the path) can
  refuse it. It is a GET, and the crawler never processes its response as a page (its path is
  re-checked and skipped). It is not hidden either: the guard sees the hop as it is sent and counts
  it as **allowed** with reason ``deny`` / ``not_allowed`` (``scope_paths_reached`` in the
  evidence pack), so no record claims that list held.

Counters carry the method (non-standard methods as ``OTHER``), resource type, decision, reason and
a value-free host (``stored_host``) — never a URL or a value. ``BlockedRequest.url`` stays in
worker memory for the crawler to redact, and is excluded from ``repr``.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
from collections import Counter
from collections.abc import Awaitable, Callable, Collection, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Literal
from urllib.parse import parse_qsl, unquote_plus, urlsplit

from playwright.async_api import (
    BrowserContext,
    Dialog,
    Frame,
    Page,
    Request,
    Route,
    WebSocketRoute,
)
from playwright.async_api import Error as PlaywrightError

from dps.engine.verify import normalise
from dps.layers.scanner.models import RequestCount
from dps.layers.scanner.proxy import ProxySettings, ScopeProxy
from dps.netguard import forbidden_ip_reason, is_ip, resolve_addresses
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
#: (host, port) -> every address the host resolves to (``netguard.resolve_addresses``)
Resolve = Callable[[str, int], Awaitable[list[str]]]
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
#: * Deleted from the window and its prototype chain, filled page or not, because nothing they send
#:   is routed: ``SharedWorker`` (also off by ``LAUNCH_ARGS``), ``RTCPeerConnection``,
#:   ``fetchLater`` (a deferred request the browser sends itself, POST and body included, on a timer
#:   or when the document goes; HTTPS only), ``WebSocketStream`` (a socket ``route_web_socket``
#:   does not replace) and ``WebTransport`` (QUIC is off by ``LAUNCH_ARGS`` as well). Deleted, a
#:   use is a ``ReferenceError``: there is nothing to refuse, so nothing is counted.
#: * ``sendBeacon``, ``fetch``, ``XMLHttpRequest`` (``open``/``setRequestHeader``/``send``),
#:   ``WebSocket``, ``Worker``, ``EventSource``, ``window.open`` and ``Document.prototype.open`` /
#:   ``write`` / ``writeln`` are wrapped *now*, so a page never holds an original. In every realm a
#:   beacon is refused, ``keepalive`` dropped and a method other than GET/HEAD (or an override)
#:   refused, so no route is needed for the method rule (M4 verify r2 N1); in an inert document
#:   (see the module docstring) every request is refused and a CSP loads nothing; ``WebSocket``
#:   and ``Worker`` refuse once the document is locked (``__dpsGuardLock``, called by
#:   ``ScanGuard.arm`` and by ``LOCK_JS``), and locking terminates the workers the document
#:   started. The wrappers stay writable (frameworks such as zone.js wrap them again and call
#:   through); ``__dpsGuardLock`` and ``__dpsInert`` cannot be replaced.
ISOLATE_JS = r"""
(() => {
  const W = window;
  const fixed = (o, k, v) => { try { Object.defineProperty(o, k, { value: v, configurable: false, writable: false, enumerable: false }); } catch (e) {} };
  const swap = (o, k, v) => { try { Object.defineProperty(o, k, { value: v, configurable: true, writable: true, enumerable: false }); } catch (e) {} };
  // the report binding of this realm, else of the realm that made it (an inert popup can be
  // scripted before Playwright has added the binding to it)
  const R0 = W.__dpsGuardReport;
  let R1 = null;
  try { const o = W.opener || (W !== W.top ? W.parent : null); if (o && typeof o.__dpsGuardReport === 'function') { R1 = o.__dpsGuardReport; } } catch (e) {}
  const report = (kind, method, url, why) => {
    try {
      const own = typeof R0 === 'function' ? R0 : W.__dpsGuardReport;
      const r = typeof own === 'function' ? own : R1;
      let u = '';
      try { u = new URL(String(url), W.location.href).href; } catch (e) {}
      if (typeof r === 'function') { Promise.resolve(r(kind, String(method || 'GET'), u, why || 'api_removed')).catch(() => 0); }
    } catch (e) {}
  };
  const refused = (what) => new DOMException(what + ' is blocked in a DPS scan', 'SecurityError');
  let locked = false;
  const workers = new Set();

  // Inert documents: the documents of a window born at about:blank (a popup, which its opener
  // scripts before Playwright has attached to it, so nothing it sends is routed) while they are
  // about:blank, and every frame born inside one. Chromium swaps the popup's first document for
  // another about:blank document (same window, this script does not run again), and
  // document.open() gives a document the opener's URL: so a document is marked inert (an own,
  // fixed property) whenever it is seen at about:blank, by window.open's return, document.open /
  // write, or a request. The same-origin page the popup then loads reuses the window too, and
  // that page is routed: it is not inert. An inert document gets a CSP that loads nothing.
  let inertRealm = false;
  try {
    if (W === W.top) { inertRealm = W.location.protocol === 'about:'; }
    else { const p = W.parent.__dpsInert; inertRealm = typeof p === 'function' && p() === true; }
  } catch (e) { inertRealm = false; }
  const MARK = '__dpsInertDocument';
  const inertDocs = new WeakSet();
  const CSP = "default-src 'none'; script-src 'none'; style-src 'none'; img-src 'none'; media-src 'none'; font-src 'none'; connect-src 'none'; frame-src 'none'; child-src 'none'; worker-src 'none'; object-src 'none'; manifest-src 'none'; form-action 'none'; base-uri 'none'";
  const marked = (d) => { try { return inertDocs.has(d) || !!Object.getOwnPropertyDescriptor(d, MARK); } catch (e) { return true; } };
  const markInert = (d) => {
    if (!d || inertDocs.has(d)) { return; }
    inertDocs.add(d);
    fixed(d, MARK, true);
    try {
      const m = d.createElement('meta');
      m.httpEquiv = 'Content-Security-Policy';
      m.content = CSP;
      let head = d.head;
      if (!head) {
        const root = d.documentElement || d.appendChild(d.createElement('html'));
        head = root.insertBefore(d.createElement('head'), root.firstChild);
      }
      head.insertBefore(m, head.firstChild);
    } catch (e) {}
  };
  const inert = () => {
    if (!inertRealm) { return false; }
    const d = W.document;
    if (marked(d)) { markInert(d); return true; }
    if (W.location.protocol === 'about:') { markInert(d); return true; }
    return false;
  };
  fixed(W, '__dpsInert', inert);
  inert();
  // the opener's first chance to touch a popup's (swapped) document comes after window.open
  // returns, and a document.open() / write() on an about:blank document comes before its URL
  // changes: both mark it first
  const touch = (win) => { try { const f = win && win.__dpsInert; if (typeof f === 'function') { f(); } } catch (e) {} };
  const openFn = W.open;
  if (typeof openFn === 'function') {
    swap(W, 'open', function open() {
      const win = openFn.apply(this, arguments);
      touch(win);
      return win;
    });
  }
  const DP = W.Document && W.Document.prototype;
  for (const name of ['open', 'write', 'writeln']) {
    const orig = DP && DP[name];
    if (typeof orig !== 'function') { continue; }
    swap(DP, name, function () {
      try { if (String(this.URL).startsWith('about:')) { touch(this.defaultView); } } catch (e) {}
      return orig.apply(this, arguments);
    });
  }
  // only GET and HEAD leave the browser (decision D4), in every realm, whether or not a route
  // can see it; a method override asks the server for another method
  const OVERRIDES = ['x-http-method-override', 'x-http-method', 'x-method-override'];
  const methodOk = (method, url, hasHeader) => {
    const m = String(method || 'GET').toUpperCase();
    if (m !== 'GET' && m !== 'HEAD') { return false; }
    for (const h of OVERRIDES) { if (hasHeader(h)) { return false; } }
    try {
      for (const k of new URL(String(url), W.location.href).searchParams.keys()) {
        if (k.trim().toLowerCase() === '_method') { return false; }
      }
    } catch (e) {}
    return true;
  };

  const C = W.ServiceWorkerContainer;
  if (C) {
    fixed(C.prototype, 'register', function () { return Promise.reject(new DOMException('service workers are blocked in a DPS scan', 'SecurityError')); });
  }
  fixed(W, 'close', function () {});
  // never routed, filled page or not: gone from the window and its prototype chain
  for (const name of ['SharedWorker', 'RTCPeerConnection', 'webkitRTCPeerConnection',
                      'fetchLater', 'WebSocketStream', 'WebTransport']) {
    for (let o = W; o; o = Object.getPrototypeOf(o)) {
      try { if (Object.prototype.hasOwnProperty.call(o, name)) { delete o[name]; } } catch (e) {}
    }
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

  // a beacon is always a POST, and it outlives its page: refused in every realm
  const NP = W.Navigator && W.Navigator.prototype;
  if (NP && NP.sendBeacon) {
    swap(NP, 'sendBeacon', function sendBeacon(url) {
      report('beacon', 'POST', url);
      return false;
    });
  }

  const fetchFn = W.fetch;
  const Req = W.Request;
  if (fetchFn) {
    swap(W, 'fetch', function fetch(input, init) {
      // build the Request once (as fetch would): its method, headers and flags cannot change
      // after this read
      let req;
      try { req = new Req(input, init); } catch (e) { return Promise.reject(e); }
      if (inert()) {
        report('fetch', req.method, req.url);
        return Promise.reject(new TypeError('Failed to fetch: ' + refused('fetch').message));
      }
      if (!methodOk(req.method, req.url, (h) => req.headers.has(h))) {
        report('fetch', req.method, req.url, 'method');
        return Promise.reject(new TypeError('Failed to fetch: ' + refused('fetch').message));
      }
      if (req.keepalive) {
        // a keepalive request outlives its page, where no route sees it: the flag is dropped
        report('keepalive', req.method, req.url);
        try { req = new Req(req, { keepalive: false }); } catch (e) { return Promise.reject(refused('keepalive')); }
      }
      return fetchFn.call(this, req);
    });
  }

  const XP = W.XMLHttpRequest && W.XMLHttpRequest.prototype;
  if (XP && XP.open && XP.send && XP.setRequestHeader) {
    const xopen = XP.open, xsend = XP.send, xheader = XP.setRequestHeader;
    const xstate = new WeakMap();
    swap(XP, 'open', function open(method, url) {
      xstate.set(this, { method: String(method), url: String(url), headers: new Set() });
      return xopen.apply(this, arguments);
    });
    swap(XP, 'setRequestHeader', function setRequestHeader(name, value) {
      const st = xstate.get(this);
      const out = xheader.apply(this, arguments);
      if (st) { st.headers.add(String(name).trim().toLowerCase()); }
      return out;
    });
    swap(XP, 'send', function send() {
      const st = xstate.get(this);
      const bad = inert() ? 'api_removed'
        : (st && !methodOk(st.method, st.url, (h) => st.headers.has(h)) ? 'method' : null);
      if (bad === null) { return xsend.apply(this, arguments); }
      report('xhr', st ? st.method : 'GET', st ? st.url : '', bad);
      // as a refused request looks to the page: an error, asynchronously
      const x = this;
      setTimeout(() => {
        for (const t of ['error', 'loadend']) { try { x.dispatchEvent(new ProgressEvent(t)); } catch (e) {} }
      }, 0);
      return undefined;
    });
  }

  const wrapCtor = (name, kind, refuse, onCreate) => {
    const Orig = W[name];
    if (typeof Orig !== 'function') { return; }
    const Wrapped = function (url) {
      if (!new.target) { throw new TypeError("Failed to construct '" + name + "': Please use the 'new' operator."); }
      if (refuse()) { report(kind, 'GET', url); throw refused(name); }
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
  wrapCtor('WebSocket', 'websocket', () => locked || inert(), null);
  wrapCtor('Worker', 'worker', () => locked || inert(), (w) => workers.add(w));
  wrapCtor('EventSource', 'eventsource', inert, null);

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
_LOCK_ATTEMPTS = 3  # a frame mid-navigation is retried (its next document is locked at birth)
#: ``arm``: passes of "lock the pages not locked yet, one round trip, look again" before it gives
#: up (``False``) on a page that keeps opening pages while it is being armed
_SETTLE_PASSES = 50
_LOCK_RETRY_S = 0.1
#: the page binding ``ISOLATE_JS`` reports refused uses through, and what it may report
REPORT_BINDING = "__dpsGuardReport"
REPORTED_KINDS: frozenset[str] = frozenset(
    {"beacon", "keepalive", "websocket", "worker", "fetch", "xhr", "eventsource"}
)
#: the reasons ``ISOLATE_JS`` may give: a refused API, or a method other than GET/HEAD
REPORTED_REASONS: frozenset[str] = frozenset({"api_removed", "method"})
_MAX_REPORTED_URL = 8192


@dataclass(frozen=True, slots=True)
class Decision:
    """The guard's verdict on one request.

    ``reason``: ``ok`` | ``scheme`` | ``method`` | ``host`` | ``filled_navigation`` |
    ``filled_value`` | ``filled_request`` | ``closing`` | ``guard_error``; at the scope proxy also
    ``service_worker``, ``speculative`` and ``host_forbidden`` (an allowed host that resolves to
    a forbidden address, S5); for a use of a locked API (no request exists)
    ``api_removed``; for the scope's path rail ``deny`` (any request) and ``not_allowed`` (a
    navigation). An *allowed* row with reason ``deny`` / ``not_allowed`` records an HTTPS redirect
    hop that reached such a path (it could not be refused, see the module docstring).
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
    path: str = "ok",
) -> Decision:
    """The whole policy as a pure function (the route handler only gathers its inputs).

    ``resource_type`` ``None`` means unknown and is treated as a script request on a filled page;
    ``method_override`` is true when the request carries a method-override header. ``path`` is
    the scope's verdict on the URL's path (``ok`` | ``deny`` | ``not_allowed``): ``deny`` refuses
    any request, ``not_allowed`` a navigation.
    """
    if urlsplit(url).scheme.lower() not in ("http", "https"):
        return Decision(False, "scheme")
    if method.upper() not in ALLOWED_METHODS or method_override or _overrides_method(url):
        return Decision(False, "method")
    if not host_allowed:
        return Decision(False, "host")
    if path == "deny":
        return Decision(False, "deny")
    if path != "ok" and is_navigation:
        return Decision(False, "not_allowed" if path == "not_allowed" else "guard_error")
    if page_filled and is_navigation:
        return Decision(False, "filled_navigation")
    if needles and _carries_value(url, needles):
        return Decision(False, "filled_value")
    if page_filled and resource_type not in PASSIVE_TYPES:
        return Decision(False, "filled_request")
    return Decision(True, "ok")


#: ``Sec-Fetch-Dest`` values of a document load (a page, a frame, an embedded document)
NAVIGATION_DESTS: frozenset[str] = frozenset(
    {"document", "iframe", "frame", "embed", "object", "fencedframe"}
)


def is_navigation_hop(headers: Mapping[str, str]) -> bool:
    """Whether a hop the scope proxy sees (headers by lower-case name) loads a document. Fails
    towards ``True``: any navigation marker Chromium sends, or an ``Accept`` that asks for HTML."""
    if headers.get("sec-fetch-mode", "").strip().lower() == "navigate":
        return True
    if headers.get("sec-fetch-dest", "").strip().lower() in NAVIGATION_DESTS:
        return True
    if "upgrade-insecure-requests" in headers:
        return True
    return "text/html" in headers.get("accept", "").lower()


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

    def __init__(
        self,
        *,
        host_allowed: Callable[[str], bool],
        path_verdict: Callable[[str], str] | None = None,
        allow_loopback: bool = True,
        resolve: Resolve | None = None,
    ) -> None:
        """``host_allowed(host)``: the scope's host rule. ``path_verdict(url)``: the scope's path
        rail (``ok`` | ``deny`` | ``not_allowed``; ``CrawlScope.request_path_verdict``), ``None``
        for none (guard unit tests). ``allow_loopback``: the address rule lets loopback through
        (``env=local`` only; the crawler passes it). ``resolve``: the host lookup of the address
        rule (``netguard.resolve_addresses``; tests inject a fake)."""
        self._host_allowed = host_allowed
        self._path_verdict = path_verdict
        self._allow_loopback = allow_loopback
        self._resolve: Resolve = resolve or resolve_addresses
        self._filled: set[Page] = set()
        self._needles: set[str] = set()
        self._page_no: dict[Page, int | None] = {}
        self._counts: Counter[_CountKey] = Counter()
        self._blocked: list[BlockedRequest] = []
        self._closing: set[Page] = set()
        self._closes_in_flight = 0
        self._context: BrowserContext | None = None
        self._locked_at_birth = False
        self._proxy = ScopeProxy(self._check_hop, dial=self._dial_hop)
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
        context.on("request", self._on_request)

    def bind(self, page: Page, page_no: int | None) -> None:
        """Attribute ``page``'s requests to crawl page ``page_no``."""
        self._page_no[page] = page_no

    async def arm(self, page: Page, fill_values: Iterable[str]) -> bool:
        """Mark ``page`` filled and lock the scan's documents; call it before any value is set.

        Needles stay for the rest of the scan and are checked on every page: a later page that
        would carry an earlier persona's value in a URL is caught too. The route rules apply at
        once, to ``page`` and to every other page of the context that is open now (a popup opened
        before the fill can be handed a value by ``postMessage`` or ``BroadcastChannel``). Every
        frame of every open page is locked (their workers terminated; beacons, keepalive, sockets
        and new workers refused) and, from the first call on, every new document of the scan is
        locked at birth.

        Order matters (M4 verify r1 F1): the lock-at-birth script is registered first, and only
        then are the open pages listed, marked filled and locked; after one round trip to the
        browser (so every page event sent before it has reached ``context.pages``) any page not
        locked yet is marked and locked too, until a pass finds none. A page opened before the
        script was registered is in a list taken after it; one opened later is locked at birth.

        Returns ``False`` (``locks_failed``) when a live frame could not be locked, the script
        could not be registered, or pages were still appearing after ``_SETTLE_PASSES`` passes:
        the caller must not fill that page.
        """
        for value in fill_values:
            needle = normalise(value)
            if len(needle) >= MIN_NEEDLE:
                self._needles.add(needle)
        ok = True
        # First the lock-at-birth script, THEN the list of open pages (M4 verify r1 F1): a page
        # that opens after the script is registered is locked at birth, and every page that
        # opened before it is in a list taken after it. Taken the other way round, a popup opened
        # in between was in neither, and its worker relayed a value on a wss:// socket.
        if self._context is not None and not self._locked_at_birth:
            try:
                await self._context.add_init_script(script=LOCK_JS)
                self._locked_at_birth = True
            except PlaywrightError:
                ok = False
        done: set[Page] = set()
        pending = self._open_pages(page)
        for _ in range(_SETTLE_PASSES):
            for each in pending:
                self._mark_filled(each)
            if not await self._lock(pending):
                ok = False
            done.update(pending)
            # one round trip: every page event Playwright sent before it has reached
            # ``context.pages`` (the connection delivers events in order)
            await self._round_trip(page)
            pending = [p for p in self._open_pages(page) if p not in done]
            if not pending:
                break
        else:
            ok = False  # pages kept appearing: the last ones were locked, none can be vouched for
        if not ok:
            self.locks_failed += 1
        return ok

    def _open_pages(self, page: Page) -> list[Page]:
        """``page`` first, then every other open page of the context."""
        others = list(self._context.pages) if self._context is not None else []
        return [page, *(p for p in others if p is not page and not p.is_closed())]

    async def _round_trip(self, page: Page) -> None:
        """One request to the browser and its answer (``page``'s, or the context's if ``page``
        cannot evaluate: it is navigating or closed). Without a context there is nothing to
        wait for: no other page can exist."""
        if self._context is None:
            return
        try:
            await page.evaluate("0")
        except PlaywrightError:
            if self._context is not None:
                with contextlib.suppress(PlaywrightError):
                    await self._context.cookies("http://localhost.invalid/")

    async def _lock(self, pages: list[Page]) -> bool:
        """Lock every frame of ``pages``; ``False`` when a live frame could not be locked."""
        ok = True
        for page in pages:
            for frame in list(page.frames):
                if not await self._lock_frame(frame):
                    ok = False
        return ok

    @staticmethod
    async def _lock_frame(frame: Frame) -> bool:
        """Lock one frame's document; a frame that is navigating (a popup on its way to
        ``about:blank``) is retried, since its next document is locked at birth. ``True`` when it
        is locked or gone (detached)."""
        for attempt in range(_LOCK_ATTEMPTS):
            try:
                if await frame.evaluate(_LOCK_CALL):
                    return True
            except PlaywrightError:
                pass
            if frame.is_detached():
                return True
            if attempt + 1 < _LOCK_ATTEMPTS:
                await asyncio.sleep(_LOCK_RETRY_S)
        return False

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
                path=self._path(url),
            )
        page_no = self._page_no.get(page) if page is not None else None
        return decision, page_no, stored_method(request.method), stored_host(host, allowed=allowed)

    def _path(self, url: str) -> str:
        return self._path_verdict(url) if self._path_verdict is not None else "ok"

    def _on_request(self, request: Request) -> None:
        """Count an HTTPS redirect hop to a denied (or, navigating, not allowed) path as what it
        is: a request that left. Nothing can refuse it (the route sees first hops only, the proxy
        only the tunnel); plain-HTTP hops are refused, and counted, by the proxy instead."""
        try:
            if request.redirected_from is None or self._path_verdict is None:
                return
            url = request.url
            if urlsplit(url).scheme.lower() != "https":
                return
            verdict = self._path_verdict(url)
            if verdict == "ok" or (verdict != "deny" and not request.is_navigation_request()):
                return
            host = (urlsplit(url).hostname or "").lower()
            allowed = bool(host) and self._host_allowed(host)
            if not allowed:  # the proxy refuses the tunnel (and counts it): nothing left
                return
            page = self._page_of(request)
            page_no = self._page_no.get(page) if page is not None else None
            self._record(
                Decision(True, verdict if verdict in ("deny", "not_allowed") else "guard_error"),
                page_no,
                stored_method(request.method),
                request.resource_type,
                stored_host(host, allowed=allowed),
                url,
            )
        except Exception:  # noqa: BLE001 - an event listener must never raise into Playwright
            return

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
        self,
        source: Mapping[str, object],
        kind: object,
        method: object,
        url: object,
        why: object = "api_removed",
    ) -> None:
        """``ISOLATE_JS`` reports a request it refused itself (none exists to route): a use of a
        refused API (reason ``api_removed``) or a method other than GET/HEAD (reason ``method``).

        Page-callable, so only a known ``kind`` and ``why`` are counted (anything else counts as
        ``api_removed``), the method and host are reduced to their value-free forms like any
        other, and the URL stays in memory only. A popup's first document reports through its
        opener's binding when it has none of its own yet (the row is then attributed to the
        opener's page); a popup is never bound to a crawl page, so its own rows carry none."""
        if not isinstance(kind, str) or kind not in REPORTED_KINDS:
            return
        reason = why if isinstance(why, str) and why in REPORTED_REASONS else "api_removed"
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
        self._record(Decision(False, reason), page_no, verb, kind, stored, text)

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
                    is_navigation=is_navigation_hop(headers),
                    page_filled=False,
                    host_allowed=allowed,
                    needles=self._needles,
                    method_override=any(k in OVERRIDE_HEADERS for k in headers),
                    path=self._path(url),
                )
        except Exception:  # noqa: BLE001 - fail closed: an undecidable hop never leaves
            decision = Decision(False, "guard_error")
        if not decision.allow:
            stored = stored_host(host, allowed=allowed)
            self._record(decision, None, stored_method(method), UNROUTED, stored, url)
        return decision.allow

    async def _dial_hop(self, method: str, url: str, host: str, port: int) -> list[str] | None:
        """The scope proxy's question before it dials an allowed hop (S5): every address ``host``
        resolves to, when each passes the address rule; ``None`` — counted ``host_forbidden`` —
        when any is link-local or a cloud metadata address, multicast, unspecified, reserved or
        (unless ``allow_loopback``) loopback. A name that does not resolve raises ``OSError``
        (the proxy's 502: a failed dial, not a refusal)."""
        try:
            addresses = [host] if is_ip(host) else await self._resolve(host, port)
        except UnicodeError:  # not a name a resolver can encode (IDNA): it resolves to nothing
            raise OSError("the host does not resolve") from None
        if not addresses:
            raise OSError("the host has no address")
        for address in addresses:
            try:
                reason = forbidden_ip_reason(address, allow_loopback=self._allow_loopback)
            except ValueError:
                reason = "non-routable address"
            if reason is not None:
                stored = stored_host(host, allowed=self._host_allowed(host))
                self._record(
                    Decision(False, "host_forbidden"),
                    None,
                    stored_method(method),
                    UNROUTED,
                    stored,
                    url,
                )
                return None
        return addresses

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
