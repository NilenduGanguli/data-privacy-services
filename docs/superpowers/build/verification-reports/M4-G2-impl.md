# M4 G2 fix — implementation report (M4-G2-B1..B4, design D-C)

Worktree `/Users/neelu/document_intelligence/dps-wt-m4`, branch `m4`. Commit **`97695cd`**
`fix(m4): scanner network policy below the page — …` (author NilenduGanguli
<nilendu.ganguli@gmail.com>, no trailers). Working tree clean afterwards. No Docker containers were
started (the browser tests use the demo site started by the fixture with the host's node, and a
counting stdlib server); nothing to bring down.

## Resume state
The earlier agent left ~770 uncommitted lines (new `proxy.py`; `guard.py`, `browser.py`, `dom.py`,
`models.py`, demo `forms.js`, 4 test files). All 64 of its tests passed when I started. I reviewed it
against D-C, kept its core (scope proxy, about:blank close, unread-frame listing) and finished the
gaps listed below. Nothing was discarded.

## Probes first (pinned Chromium, Playwright 1.55.0 / headless shell 1187)
| Probe | Result |
|---|---|
| `page.close()` with pagehide/unload handlers | beacon, keepalive AND plain fetch POSTs arrive |
| route "abort all" + `page.close(run_before_unload=False)` (the literal D-C text) | still arrive — target torn down, nothing routed |
| abort all + navigate `about:blank` + close | 0 arrive |
| `context.close()` with such a page open | 0 arrive |
| dedicated worker `new WebSocket('ws://…')` | `route_web_socket` never sees it; reaches server via a plaintext CONNECT tunnel |
| `--disable-shared-workers` | SharedWorker undefined |
| `goto(about:blank)` with a beforeunload prompt after a gesture | hangs unless dialog answered |
| speculation-rules prefetch on a filled page | browser-fetched, never routed; no feature flag / blink flag / CDP setBlockedURLs stops it |

## What was done (by finding)
- **B3 redirect/SSRF** — `proxy.py` `ScopeProxy` on every scan context (`proxy=` with bypass
  `<-loopback>`): CONNECT only to allowed hosts (all hops); plain HTTP only GET/HEAD, no body, no
  override, no upgrade, no `Service-Worker`, no `Sec-Purpose`, allowed host, no fill value; one
  request per connection, `Connection: close`. **New:** plaintext inside a tunnel (first byte not a
  TLS handshake record = a `ws://` socket) gets the plain-HTTP rules → a worker's ws upgrade is
  refused. Refused hops are counted `resource_type="unrouted"`, `page_no=None`, value-free host.
- **B1 unload traffic** — `ScanGuard.close(page)`: page added to `_closing` (route aborts all its
  requests, reason `closing`; unattributable ones too while a close is in flight), dialog handler
  answers `beforeunload` "leave", navigate to `about:blank` while routed, then
  `page.close(run_before_unload=False)`. If the navigation fails the page stays open with all
  requests aborted (`is_closing`, `pages_left_open`) and goes with the context. New
  `browser.close_scan_context(context, guard)` (every page via guard.close → context.close → proxy
  stop; idempotent). `new_scan_context(…, guard=, harden_js=)` starts the proxy and installs the
  guard before any page; stops the proxy on failure.
- **B1/B2 APIs no route sees** — `ISOLATE_JS` (always, every frame, before page scripts): SW
  registration refused in all realms, `window.close` no-op, `SharedWorker`/`RTCPeerConnection`
  deleted, window `beforeunload` listeners ignored, and lockable wrappers installed at birth for
  `sendBeacon` (→false), `fetch` keepalive (Request built once, rebuilt without keepalive),
  `WebSocket`, `Worker` (refused when locked; worker instances tracked). `__dpsGuardLock`
  (non-replaceable) locks and terminates the document's workers. `route_web_socket` is registered
  before `ISOLATE_JS` (Playwright replaces `window.WebSocket`; ours must wrap it — found by a failing
  test). **`ScanGuard.arm` is now async → bool**: route rules, lock every frame, and on first call
  `LOCK_JS` so every later document is locked at birth; False = a live frame not locked = do not
  fill. Refused uses counted via page binding `__dpsGuardReport` (reason `api_removed`, kinds
  allow-listed, method/host value-free, URL memory-only). `LAUNCH_ARGS` += `--disable-quic`
  (with `--disable-shared-workers`, `--force-webrtc-ip-handling-policy=disable_non_proxied_udp`,
  `--dns-prefetch-disable`).
- **B4 frames** — `PageSnapshot.frames_failed` (same-origin frames that failed while read, also when
  they then detached); unread frames listed with `read=False` and box; `dom.unread_frame_boxes`
  returns `None` (capture withheld, fail closed) when `frames_failed` is non-empty or an unread
  frame cannot be located, else the boxes to paint.
- **New finding (not in the review)**: speculation-rules prefetch is unroutable. Proxy refuses it over
  plain HTTP (`Sec-Purpose`, reason `speculative`, tested). Over HTTPS it is a residual (GET to an
  allowed host, URL unseen) — documented in the `guard.py` docstring with the other residuals.

## Deviations (all in `docs/superpowers/build/M4-DEVIATIONS.md`, section "Review round 1 fixes (G2)")
1. D-C's "abort-all then close with run_before_unload=False" is insufficient (probe); the about:blank
   navigation under abort-all is kept, and a page that cannot navigate is never closed.
2. The lock covers workers (terminated) and every document after the first fill, not only "filled
   pages" (a pre-fill worker could carry values out on an unrouted socket).
3. `arm` async + bool; `new_scan_context` requires `guard`; new `close_scan_context`, `is_closing`,
   `proxy_running`, `locks_failed`, `frames_failed`, reasons `closing`, `api_removed`, `speculative`.
   No contract (`m1-m5-contract.md`) name is affected (none of these names appear there).
4. M4 plan text amended for later tasks (code as built wins): T12 `guarded_context` and capture
   `await guard.arm` (withheld `guard_lock`) + frame rule; T13 tests; T14 runner (guard before
   context, `close_scan_context` in finally, popups/`_close` via `guard.close`, fill skipped on
   `arm` False, `_on_dialog` leaves beforeunload to `guard.close`); T15 proof (`await guard.arm`,
   `guard.close` — the demo now flushes a draft on pagehide, so a bare `page.close()` would leak).

## Proof tests (counting server, 0 received + counted by the guard)
| Case | Test | Server | Guard count |
|---|---|---|---|
| beacon (filled) | `test_a_filled_page_can_beacon_keepalive_socket_or_start_workers_nowhere` | 0 non-GET, no `/beacon-armed` | (4,POST,beacon,api_removed) |
| keepalive POST and GET (filled) | same | 0 | keepalive api_removed ×2 + route abort |
| WebSocket (filled; and a worker's ws on an unfilled page) | same; `test_a_workers_websocket_never_reaches_the_server` | upgrades 0 | websocket api_removed; (GET,unrouted,blocked,method) |
| Worker / `Worker.prototype.constructor` / pre-fill worker | same | 0 | worker api_removed; stale worker silent |
| SharedWorker / service worker | `test_no_shared_or_service_worker_runs_in_a_scan_context`, `…refused_even_by_the_proxy` | nothing fetched | proxy 403 |
| unload beacon/keepalive/plain POST, filled+unfilled, beforeunload prompt | `test_closing_a_page_through_the_guard_lets_no_unload_request_out[False/True]`, `test_close_scan_context_…`, demo `test_a_draft_is_flushed_…` | non_get 0 (positive control: plain close delivers) | (POST,closing) / api_removed |
| redirect to foreign host, http + https, img/fetch/nav | `test_redirect_hops_never_leave_the_scope` (raw TCP listener) | **0 connections, 0 bytes** | (GET,unrouted,host) + (CONNECT,unrouted,host), host `{host}` |
| speculative prefetch (HTTP) | `test_a_speculative_prefetch_is_refused_by_the_proxy` | 0 | (unrouted,speculative) |
| failed frame → capture withheld | `test_a_child_frame_that_fails_is_recorded_unread`, `…fails_and_detaches_still_withholds` | — | `unread_frame_boxes is None` |

Mutation checks (each made its proof fail, then restored, verified by diff): tunnel inspection off →
2 fail; about:blank step removed → 3 fail; lock off → 2 fail; proxy removed from context → 2 fail.
Keepalive-getter TOCTOU: wrapper changed to build the Request once (test entry kept; the count cannot
discriminate the old code — noted in the test).

## Gates (final, on the committed tree)
- `./lane.sh ruff format dps tests tools` — 266 files unchanged after one reformat; `ruff check` — All
  checks passed; `./lane.sh mypy dps` — Success, 111 files.
- `./lane.sh pytest -q` — **1708 passed, 189 skipped** (skips: service markers whose containers this
  group does not need — db/s3/pg_source/ocr/oracle etc.; none in the touched tasks).
- Touched tasks' integration: `DPS_TEST_REQUIRE_SERVICES=1 pytest tests/scanner -m browser` — 37
  passed, 0 skipped; `tests/scanner/{test_guard,test_dom_browser,test_demo_site}.py` 73 passed, run 3
  times (no flakes); whole `tests/scanner` 265 passed, 16 skipped (DB store/migration tests, T3/T5,
  untouched, need dps-db).
- Frontend: not touched (no npm gates needed).
- No values in logs: `proxy.py`/`guard.py` contain no logging/print; counters are value-free
  (`stored_host`, `stored_method`, allow-listed kinds); URLs only in `BlockedRequest` memory.

## Open issues / for the reviewer (G1+G2 re-review)
1. Residuals (documented, not fixable at this layer without TLS interception): HTTPS
   speculation-rules prefetch URL from a filled page; `wss://` of a dedicated worker of an
   *unfilled* page; HTTPS redirect-hop URL (host-checked only). A MITM proxy would need
   `ignore_https_errors` or a trusted CA — a security weakening, not done.
2. Crawler (T14) must honour: every page/popup closed via `guard.close`, contexts via
   `close_scan_context`, `arm` False → no fill, `_on_dialog` must not answer beforeunload while
   `guard.is_closing(page)`. Captures (T12) must use `unread_frame_boxes` and withhold on `None`.
3. Lock-at-birth after the first fill removes WebSocket/Worker/beacon from later pages of the scan
   (fidelity trade-off for D4); later SPA pages that need them will render degraded.
4. The G1+G2 independent review (T1–T8 incl. URL fix `9fcc13c`) has NOT been done by this agent —
   it is the next WP-M4 step for an independent reviewer.
5. HTTPS paths of the proxy were tested by raw CONNECT (TLS byte pass-through, foreign CONNECT
   refused) and a browser https redirect hop; no TLS target with a trusted cert was exercised.
