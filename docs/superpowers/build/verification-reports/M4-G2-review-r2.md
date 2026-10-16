# M4 review: G1 and G2 (Tasks 1 to 8, the G2-fix and fix r1), round 2

**Reviewer:** independent and read-only. **Branch:** `m4` @ `54a7d5d` in `/Users/neelu/document_intelligence/dps-wt-m4`. The working tree was clean before and after the review.
**Verdict: FAIL.** All gates are green. There are 2 blockers: a D4/D-C socket leak, and a value leak in the URL toolkit.

Probes are in `/Users/neelu/.cache/dps-build/reports/probe-m4-G2/r2/` (`q1`..`q6`, `static.txt`, `pytest_full.txt`, `pytest_integ.txt`). I also re-ran the round-1 probes `p1`, `p2` and `p3` from the parent directory. No repository file was modified. Lane `dps-db` was brought up and taken down again (`docker compose down`). No other project was touched.

## 1. Gates (run by me)

| Gate | Result |
|---|---|
| `./lane.sh ruff format --check dps tests tools` | 266 files already formatted (exit 0). I used `--check` so the repo is not modified |
| `./lane.sh ruff check dps tests tools` | All checks passed |
| `./lane.sh mypy dps` | Success: no issues found in 111 source files |
| `./lane.sh pytest -q` (full, lane `dps-db` up) | **1881 passed, 71 skipped**, 1 warning (starlette httpx deprecation), exit 0 |
| Skip reasons (`-rs`) | Only other milestones' services: source-pg, minio, azure-ocr-mock, app, tesseract, spaCy/ner, presidio. There are no scanner, config_m4 or db skips |
| `DPS_TEST_REQUIRE_SERVICES=1 ./lane.sh pytest -q tests/scanner tests/test_config_m4.py tests/db` (lane `dps-db`; the demo site is auto-started by the fixture) | **444 passed**, 0 skipped |
| frontend | not touched by Tasks 1 to 8, so not run |

## 2. Blocking findings

### B1: A fill value leaves on a `wss://` socket through a popup that the guard counts as filled but never locks (D4 / D-C)
- **Where:** `dps/layers/scanner/guard.py`, `ScanGuard.arm` → `_lock`. It locks only `page.frames` of the armed page. `LOCK_JS` locks documents born after the first `arm`. A popup that the page opened **before** the fill is never locked. Yet `_is_filled` treats it as filled, because its opener is filled (module docstring: "A popup opened by a filled page is a filled page"). So its pre-existing dedicated worker keeps an unrouted socket API.
- **Probe:** `r2/q6_popup_relay.py`, over HTTPS, with the guard installed exactly as `new_scan_context` does.
  - `/page` does `window.open('/popup')` at load. The popup starts a worker.
  - `guard.arm(page, ["Meera Venkatesan"])` returns **True**. The value is then set and the page `postMessage`s it, and also sends it over `BroadcastChannel`, to the popup.
  - The popup's worker opens `wss://127.0.0.1/leak?d=<base64 value>` and sends the value.
  - Result: the counting server receives **2 upgrades whose URL carries the fill value**. The guard counted **nothing** (`blocked: []`).
  - Positive control (`unguarded`): identical.
- **Why blocking:**
  - D-C requires the beacon, worker and socket APIs to be removed on filled pages. Programme plan §2.3 requires "WebSocket → 0 received by the counting server". D4/D-C must hold "even when a page is hostile".
  - The page here is filled by the guard's own definition, and `arm` returned True, which the caller reads as "safe to fill".
  - Task 14 cannot avoid it reliably: pages open popups at load, before the crawler acts.
  - Round 1 (§5.2) deferred the general "cross-document relay" to Task 14. This concrete case is a guard contract violation, so it belongs here.
  - Over plain HTTP the proxy refuses the `ws://` upgrade, which is why the plain-HTTP suite cannot see it.
- **Fix (small):** at `arm`, lock every frame of **every open page of the context** (`self._context.pages`), not only `page.frames`. Locking terminates their workers and refuses new sockets and workers. Return False if any live frame fails. This also closes round-1 §5.2 (BroadcastChannel to an unrelated open page).
- **Test needed:** an HTTPS counting-server test of the q6 flow (popup opened pre-fill with a worker, value relayed by `postMessage` and `BroadcastChannel`). Expect 0 upgrades, and the popup's later `new WebSocket` / `new Worker` counted `api_removed`. Keep the unguarded positive control.
- **Docs:** add the case to the residuals and lock text in `guard.py` and in `M4-DEVIATIONS.md`.

### B2: Dates split over path segments, or written with connector words, survive `redact_url` (and some survive `template`, so the persisted template too)
- **Where:** `dps/layers/scanner/urls.py`.
  - `_split_number` joins consecutive segments only for **numeric** segments holding **6+ digits**.
  - The date-shape and month-name rules run per segment only.
  - `_SEP` admits no words.
- **Probe:** `r2/q3_urls.py`, with both a None classifier and the real `Detector(DetectorConfig(locale="en_IN"))` (identical results):

| URL path | `redact_url` (stored as `url_redacted` / `final_url_redacted` / event `target_redacted`) | `template` (stored, passes `template_is_value_free`) |
|---|---|---|
| `/dob/1/5/90` | `/dob/1/5/90` (**full DOB**) | `/dob/{id}/{id}/{id}` |
| `/dob/12/may/90` | `/dob/12/may/90` (**full DOB**) | `/dob/{id}/may/{id}` |
| `/dob/may/12/90` | `/dob/may/12/90` (**full DOB**) | `/dob/may/{id}/{id}` |
| `/dob/12/May/1990` | `/dob/12/May/{id}` (day and month) | `/dob/{id}/May/{id}` |
| `/dob/12-de-mayo-de-90` | unchanged (**full DOB**) | unchanged (**persisted**) |
| `/dob/the-12th-of-may-90` | unchanged (**full DOB**) | unchanged (**persisted**) |

- **Why blocking:**
  - This is the same class as R2-B1 (short digit groups) and round-1 B3 (month-name dates), which both rounds treated as blocking.
  - The module's own rule says a date shape "is a value whatever its count", but it is not applied across segments.
  - The fix r1 claims Spanish month coverage, yet the idiomatic Spanish form (`12 de mayo de 90`) is missed with a 2-digit year.
  - `redact_url` output is persisted (`PageRecord.url_redacted`, which has no structural guard, unlike `template`).
- **Fix (narrow; please keep it to this):**
  1. In `_path`, also test each window of 2 and 3 consecutive segments (decoded) joined by `-` with `_DATE_SHAPE` / `_MONTH_DATE`, and make every segment of a matching window `{id}`.
  2. Allow the connector words `de`, `del`, `of`, `the` (as whole words) between date parts in `_MONTH_DATE`.
  3. Mirror both in `template_is_value_free`.
- **Tests:** add the six rows above to `test_redact_url_hides_…`, `test_the_persisted_template_is_value_free` and `test_a_page_record_refuses_a_raw_template`, with both classifiers. Over-redaction (for example `/calendar/2026/5/1`) is the accepted failure mode.

## 3. What I verified as correct (probes with positive controls)

- **Fix r1, B1/B2 (`fetchLater`, `WebSocketStream`, `WebTransport`).**
  - They are deleted in the main frame, `about:blank`, `srcdoc` and `<object>` realms.
  - They are also deleted in a **same-origin sandboxed iframe without `allow-scripts`**. This was a suspected bypass: the parent reaching `f.contentWindow.fetchLater` in a frame where page script cannot run. The probe (`q1`) shows that Playwright's init scripts still run there: `__dpsGuardLock` is present, `fetchLater` and `WebSocketStream` are undefined, and `WebSocket` is the wrapper.
  - Result: 0 non-GET and 0 upgrades, on unfilled and filled pages. On a filled page the realm's `sendBeacon`, `keepalive` and `WebSocket` are refused and counted `api_removed`.
- **Unload of a cross-site iframe (OOPIF) over HTTPS** (`q2`, page on `127.0.0.1`, frame on `localhost`). Positive control: a plain `page.close()` delivers `POST /oopif-plain-pagehide`. Through `close_scan_context`:
  - Unfilled: 0 arrive, counted `closing` (6 fetch, 3 ping).
  - Filled: 0 arrive. Counted as `api_removed` beacon 3, keepalive 3, and `closing` fetch 5.
  - A worker signalled at `pagehide` sent nothing.
- **Speculation rules over HTTPS** (`q5`). Prerender, either static and immediate or `<link rel=prerender>`, does not run in this headless shell; the prerendered page's POST never arrives. A prefetch GET arrives with `Sec-Purpose: prefetch`, with or without the guard. That is the documented residual.
- **Round-1 probes re-run on `54a7d5d`:**
  - `p1`: noopener popups, harden on and off. 0 hits, `filled_navigation`.
  - `p2`: HTTPS. The unfilled run gives only the documented worker `wss` residual. The filled run gives 0 hits.
  - `p3`: fresh realms on an armed page. 0 upgrades, `api_removed` websocket and worker.
- **The new HTTPS proof tests** (`test_guard.py`) have real positive controls (`POST /fl-timer`, `/worker-post`, `/csp-report` and an upgrade all arrive unguarded) and assert 0 non-GET and 0 upgrades guarded. They are not vacuous.
- **Proxy** (`proxy.py`), re-read:
  - The CONNECT authority is parsed once and the same host is checked and dialled; userinfo is refused.
  - A plaintext tunnel gets the plain-HTTP rules. One request per connection; a body is never forwarded.
  - Folded or space-padded header names give 400. The check and the dial use the same `urlsplit` result, so `http://a@b/` checks `b`.
  - Numeric host encodings are canonicalised by Chromium before the check (round 1).
- **Deny rail** (`q4`): case, `%61`, `..%2f`, `%2e%2e`, `;x`, `//`, `%252f`, `%2561`, `%25%36%31`, `　`, `#/admin`, `#!/Admin`, `#/x/../admin` and `%5c..%5c` are all `deny`.
- **Store:** the schema is regex-checked and quoted once, every value is bound, and limits are clamped. `replace_fields` and `reset_scan` are transactional, `append_events` is idempotent on `seq`, and `insert_evidence` checks tenant and job. Scanner modules contain no logging. Errors carry codes only.

## 4. Deviation log (`M4-DEVIATIONS.md`)
- **Tasks 1 to 8, review rounds G1 r1/r2 and G2 r1 (D-C):** accepted, as judged in round 1. They are stricter than the plan, and the contract is updated where names changed.
- **"G2 review round 1 — fix":**
  - Accepted: deleting rather than wrapping (a deleted API cannot be called, so the proof is the counting server plus `typeof` in every realm); `WebTransport` as an extra; the HTTPS suite.
  - Not accepted as complete: the claim that Spanish month names are covered (B2), and the residuals list, which still omits the pre-fill popup / cross-document relay (B1).

## 5. Non-blocking (for later tasks; I re-confirmed round 1's §5 items, still open as assigned)
1. **Persona values in page-chosen GET URLs (Task 14/15 doc).**
   - A filled page can base64 a value into a passive image URL (round 1 §5.1).
   - Over HTTPS it can also put a **plain** value into a speculation-rules prefetch URL, which is unrouted, so the needle check never sees it.
   - Both are GETs to allowed hosts carrying synthetic persona values. Document them as residuals.
2. **Cookies:** page JS can copy a fill value into `document.cookie`, and a passive image GET then carries it in the `Cookie` header. The needle check looks at the URL only. Same class as item 1.
3. **Deny rail edge readings still `ok`:**
   - `/admin%09` and `/admin%0a` (round 1 §5.4).
   - `/x#%2Fadmin` (an encoded hash route).
   - Fullwidth `/ａdmin` (IIS best-fit).
   - `/admin::$DATA`, `/log%00out` and `/logout%3f`.

   These are scope rails only; D4 does not depend on them.
4. **`redact_origin` keeps a foreign host in full** (data can sit in labels). Task 14 should store other-host event targets with `stored_host`, not with `template(redact_origin(...))` (N-f).
5. **Test gap:** in `test_https_nothing_but_get_leaves_where_the_proxy_is_blind`, the unload pages have no unguarded HTTPS positive control (the plain-HTTP suite has one).
6. Still open, as assigned in round 1 §5: the capture rule for closed shadow roots, shadow-DOM-off, truncated text, canvas and CSS content (Task 12); N-e names in segments; N2 `add_request_counts` retry idempotency; N-c port binding (the proxy allows CONNECT to any port of an allowed host); and the dedicated-worker `wss` residual on unfilled pages.
7. Style: the `dom.py` module docstring has a line over 100 characters, and so does `urls.py` line 19 (docstrings are exempt from ruff here).

## 6. Contract and interfaces
- Fix r1 touched only `guard.py`, `urls.py`, two test files and the deviation log. No contract name changed.
- Round 1's check stands: the changes to `jobs_models`, `config`, `observability` and `TENANT_TABLES` are additive.
- The M4-internal signature changes (`arm` is async and returns bool; `new_scan_context(guard=…)`; `close_scan_context`) are recorded in the plan and in the deviations.
