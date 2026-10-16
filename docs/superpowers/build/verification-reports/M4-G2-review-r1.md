# M4 review: G1 and G2 (Tasks 1 to 8 plus the G2-fix), round 1

**Reviewer:** independent, read-only. **Branch:** `m4` @ `97695cd` in `/Users/neelu/document_intelligence/dps-wt-m4`. Working tree was clean before and after the review.
**Verdict: FAIL.** There are 2 D4 blockers and 1 leak blocker. Every gate is green.

Probes are in `/Users/neelu/.cache/dps-build/reports/probe-m4-G2/`: `p1`..`p7`, outputs `pytest_full.txt`, `static.txt`, `p6_out.txt`, and a throwaway self-signed cert. No repository file was modified.

## 1. Gates (run by me)

| Gate | Result |
|---|---|
| `./lane.sh ruff format --check dps tests tools` | 266 files already formatted (exit 0). I used `--check` so the repo is not modified |
| `./lane.sh ruff check dps tests tools` | All checks passed |
| `./lane.sh mypy dps` | Success: no issues found in 111 source files |
| `./lane.sh pytest -q` (full) | **1826 passed, 71 skipped**, exit 0, 97 s. Every skip belongs to another milestone's services that this group does not need (source-pg, minio, ocr mock, app, tesseract, ner/presidio). There are no scanner, config_m4 or db skips |
| `DPS_TEST_REQUIRE_SERVICES=1 ./lane.sh pytest tests/scanner tests/test_config_m4.py tests/db` on this lane's `dps-db` (`docker compose up -d dps-db`), with the demo site auto-started by the fixture | **389 passed**, 0 skipped |
| frontend | not touched by Tasks 1 to 8, so not run |

`docker compose down` was run afterwards. Only this lane's project was touched.

## 2. Blocking findings

### B1: `fetchLater()` sends a POST over HTTPS that neither the route nor the proxy sees. Filled pages included.
- Where: `dps/layers/scanner/guard.py`. `ISOLATE_JS` wraps `sendBeacon`, `fetch`, `WebSocket` and `Worker`, but does not touch `window.fetchLater`. It exists in this Chromium (Playwright 1.55 / Chromium 140): `typeof fetchLater === 'function'`.
- Probe `p4_fetchlater.py`: an HTTPS counting server reached through the scope proxy with `guard.install`.
  - The page runs `fetchLater('/fl-timer', {method:'POST', body:'v', activateAfter:300})`.
  - The server receives `POST /fl-timer` with its body, on the **unfilled** page and on a **filled, armed** page (`arm` returned True). The guard counted nothing for it.
  - Positive control: an unguarded context also delivers it.
  - Over plain HTTP `fetchLater` throws ("only supported over HTTPS"). That is why the plain-HTTP proof suite cannot see this.
- Impact: page JS can send a non-GET with a body (for example the filled persona values) to an allowed host. This contradicts spec §8 / D4 ("no submissions of any kind; POSTs are intercepted"), the guard's own claim ("Only GET and HEAD leave the browser … There is no switch") and D-C.
- Fix: in `ISOLATE_JS`, always remove `fetchLater` (it is never routed, so not only on locked documents), in every realm, and also from `Window.prototype` if it is defined there. Add an HTTPS counting-server test that shows `typeof fetchLater === 'undefined'` and that 0 POSTs are received, with a positive control in an unguarded context.

### B2: `WebSocketStream` opens a `wss://` socket from a FILLED page. Neither the lock, `route_web_socket` nor the proxy stops it.
- Where: `ISOLATE_JS` locks `WebSocket` only. Playwright's `route_web_socket` replaces only `WebSocket`. `typeof WebSocketStream === 'function'` in this Chromium.
- Probe `p5_prerender.py filled`: an armed page over HTTPS runs `new WebSocketStream('wss://…/wss-stream')`. The counting server receives the `GET` with `Upgrade: websocket`. The guard counted nothing. The same happens on unfilled pages.
- Impact: this is the M4-G2-B2 class (a socket no route sees) and it reopens it. A filled page can open a socket to an allowed host and stream the fill values. Programme plan §2.3 requires "WebSocket → 0 received by the counting server", and D-C requires removing the socket APIs on filled pages. Over `ws://` the proxy refuses the plaintext upgrade, so again only HTTPS is exposed.
- Fix: wrap `WebSocketStream` like `WebSocket` (refuse and report `websocket` once locked). Better, delete it outright, because it is never routed even on unfilled pages, where page-level `WebSocket`s are closed by the route. Add an HTTPS proof test (filled page: 0 upgrades received, counted `api_removed`).

### B3: A date of birth written with a month name survives `redact_url` and `template`, and so is persisted.
- Where: `dps/layers/scanner/urls.py` `_value_like`. `_DATE_SHAPE` only knows numeric groups. `12May90`, `12-may-90` and `May-12-90` have under 6 digits, no 4-digit run, no 8+ alphanumeric run and no numeric date shape.
- Probe `p7_urls.py`, with both a None classifier and the real `Detector(DetectorConfig(locale="en_IN"))` (the test's own classifier shape):
  - `https://h.example/dob/12May90` gives `redact=…/dob/12May90` and `tpl=h.example/dob/12May90`. The same holds for `12-may-90` and `May-12-90`.
  - `template_is_value_free` accepts them, so `PageRecord.template` stores them in `dps_scan_page.template`.
- Impact: a customer value leaks into persisted storage and the console. This is the same class as R2-B1 (short-group dates such as `12-05-90`), which round 2 treated as blocking, and it is part of the G1-r3 re-verification this review owes (programme plan §1.3 `M4-G1-r3`).
- Fix: extend `_DATE_SHAPE` (or add a second pattern) for day/month-name/year in any order, with or without separators, using English 3-letter and full month names. Add the cases to `test_redact_url_hides_…`, `test_the_persisted_template_is_value_free` and `test_a_page_record_refuses_a_raw_template`. Consider the ES/HI month names that the structural dictionary covers.

### Test-quality note supporting B1 and B2 (fix together)
Every D-C counting-server proof runs over **plain HTTP**. There the scope proxy refuses any body or upgrade by itself, so the lock path is proven only by the `api_removed` counters. Nothing proves the claim where the proxy is blind, which is HTTPS (the bank's real targets are `https://…`). The redirect test already starts an HTTPS hop, so the suite can host a TLS counting server: a self-signed cert generated in the fixture, and a context with `ignore_https_errors=True` built the way `_new_context` builds it. The HTTPS cases should be: fetchLater, WebSocketStream, a worker's `fetch` POST, `<a ping>`, a CSP `report-uri`, and unload beacon/keepalive through `close_scan_context`.

## 3. What I verified as correct (probes with positive controls)

- **Popups, noopener included** (`p1`, harden on and off): `window.open(…,'noopener')`, `'noreferrer'`, `<a target=_blank rel="noopener noreferrer">`, and native `HTMLFormElement.prototype.submit` of a GET form with `target=_blank rel=noopener`, all from an armed page. Playwright's `opener()` still links them, they count as filled, they are blocked with `filled_navigation`, and the server receives 0.
- **Over HTTPS, unfilled page** (`p2`): a dedicated worker's `fetch` POST and XHR PUT, `<a ping>`, a CSP `report-uri` POST, and a POST answered by a 307. All are routed and blocked with `method`; the server receives no non-GET. The only arrival is a worker's `wss://` upgrade on an unfilled page, which is a documented residual (D-C scopes API removal to filled pages).
- **Fresh realms on an armed page over HTTPS** (`p3`): sandboxed srcdoc, srcdoc, `data:`, `blob:` and `document.write` iframes, the synchronous `iframe.contentWindow.WebSocket`/`Worker`, and an `about:blank` popup. All are locked at birth: 0 upgrades, counted `api_removed`. Positive control: the same payloads on an unfilled page deliver 6 worker `wss` upgrades.
- **Close path over HTTPS** (`p4`): `pagehide` beacon and keepalive on unfilled and filled pages closed through `close_scan_context`. 0 arrive (`closing` and `api_removed` counted).
- **Reporting API over HTTPS** (`p6`): `Reporting-Endpoints` + CSP `report-to` on an armed page, waited 90 s. Nothing arrived. Not conclusive, because Chromium may batch reports; it stays a documented residual.
- **Prerender** (`p5`): speculation-rules prerender did not trigger in headless Chromium (no GET either), so it was not exercised. I recommend a test in G4.
- **The proxy** (`proxy.py`): CONNECT host check on every hop, a plaintext-in-tunnel upgrade/POST refused, one request per connection, `Connection: close` both ways, the body never forwarded, head-size limits, and fail-closed `guard_error`. Numeric or alternative host encodings fail closed: the start URL must literally be a registered host, and Chromium canonicalises numeric IPs before the route or proxy sees them, so `0x7f.1` cannot match an allowed name. Redirect hops to a foreign listener give 0 connections (suite).
- **B4** (`dom.py`): a failed same-origin frame goes to `frames_failed` and `unread_frame_boxes` returns `None` (capture withheld). Unread frames are listed with boxes; a frame whose box is unknown also withholds the capture.
- **Store and migration**: schema name regex-checked and quoted once; every value bound; RLS ENABLE+FORCE+policy on all four tables (tests on the lane DB); navigation log append-only; `count >= 1`. Scanner modules have no logging, and errors carry codes only.
- **Deny rail re-run** (`p7`): `/%61dmin%20/`, `/admin/..%2f`, `/ad%6din;a=b/`, `/;/admin`, `/.;/admin` and `/x/..;/admin` are all `deny`.

## 4. Deviation log judgement (M4-DEVIATIONS.md)
- Tasks 1 to 5 and review rounds 1 and 2: accepted. They are stricter than the plan, and contract §2.4, §3.3, §9 and §10 are updated with them. Deferred N-items are recorded.
- Task 6: accepted (`filled_request`, needles on every page, fail closed, value-free host and method).
- Task 7: accepted. The deferred `storage_state.json` waits on SEC-2, which is the right call.
- Task 8: accepted. Note the `CLICKABLES_JS` call form for Task 14.
- G2-fix (D-C): the design is accepted. The about:blank-before-close variant is better founded than the plan text, and the probes table is reproducible. The **residuals list is incomplete**: it omits `fetchLater` (B1) and `WebSocketStream` (B2), which are not "GET to an allowed host" residuals. The plan amendments for Tasks 12 to 15 (`await guard.arm`, `close_scan_context`, `guard_lock`) are consistent.

## 5. Non-blocking (for later tasks or round 2)
1. Encoded fill values in **passive** resource URLs: on a filled page, `new Image().src='/x?d='+btoa(value)` is allowed because the needle check misses base64. This is inconsistent with the `filled_request` rationale ("encoded beyond recognition"). Values are synthetic persona or tokens, so it is not a submission; either document it as a residual or block script-initiated images. A script-chosen URL is hard to tell apart, though.
2. Cross-document relay: an unfilled page that is open at the same time (with a worker created before the fill) is not locked by `arm`. It could receive values over BroadcastChannel and send them on a worker `wss`. Task 14 should keep one page per context at a time, or lock every page of the context at the first `arm`.
3. Task 12 capture rule: `unread_frame_boxes` does not cover the following, all rendered but unread: **open shadow roots when `shadow_dom` is off**, **closed shadow roots** (always), `PageSnapshot.truncated` (texts after 5000), canvas text, and CSS-generated content. Captures must black out shadow hosts or be withheld in those cases. G3's OCR check should include a shadow-DOM-off scan.
4. Deny rail: trailing `%09`/`%0a`/`%0d` (`/admin%09`) reads as `ok`. Old Spring `AntPathMatcher(trimTokens=true)` routes these as `/admin`. The lenient reading could also strip ASCII control whitespace.
5. Names in path segments (`/u/VENKATESAN`, `/u/meera.venkatesan`) are kept without NER. This is the known N-e, for Task 14.
6. `add_request_counts` is not idempotent on a retried flush (known N2, Task 14).
7. N-c (registered host ignores port; the proxy allows CONNECT to any port of an allowed host) is still open (Task 14).
8. Style: the `dom.py` module docstring has a line over 100 characters (the docstring is exempt from ruff here; cosmetic).

## 6. Contract and interfaces
- No contract name used by other lanes is broken. The `jobs_models`, `config`, `observability` and `db.TENANT_TABLES` changes are additive, and the M2 tests were adjusted alongside them.
- Changed M4-internal signatures are recorded in the plan and in the deviations: `ScanGuard.arm` is async and returns bool, and `new_scan_context` takes `guard=`.
