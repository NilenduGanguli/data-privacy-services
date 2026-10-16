# M4 lane verification round 2: fix for N1 (branch `m4`)

- **Worktree:** `/Users/neelu/document_intelligence/dps-wt-m4`
- **Commit:** `9d4bda4 fix(m4): the method rule and inert popup documents hold in realms no route sees`
- **Base:** `6ccecae`
- **Author:** NilenduGanguli <nilendu.ganguli@gmail.com>. No trailer.
- **Probes and outputs:** `/Users/neelu/.cache/dps-build/reports/probe-m4-verify-fix-r2/`

Resume check: no earlier fix-r2 work existed. The tree was clean apart from the verifier's
untracked `docs/screenshots/m4/`, which I left untouched and did not commit.

## Root cause (established by experiment)

1. **`ISOLATE_JS` is present in the popup's realm when the opener scripts it.** `window.open('')`
   returns a window that already has `__dpsGuardLock`, the wrapped `sendBeacon`, and so on. So the
   leak was not a missing script. It came from the old wrappers:
   - they refused a beacon or `keepalive` **only once the document was locked**;
   - `fetch`/XHR checked no method at all and relied on the route;
   - Playwright never routes this realm.
2. **Chromium swaps the popup's first `about:blank` document for a second one in the same window,**
   and init scripts do not run again. Measured: `doc0 !== w.document`, and the stored first
   document has `defaultView === null`. Two more facts follow:
   - `document.open()` gives that document the opener's URL;
   - the same-origin page that a popup then loads **reuses the window** too, again without
     re-running init scripts.

   So "inert" cannot be a property of the realm (that broke the relay-popup tests, whose page loads
   into the reused window). It cannot be fixed at birth by document identity either. It has to be
   decided per document.

## Fix (`dps/layers/scanner/guard.py`, `ISOLATE_JS` and `_report`)

### Every realm, locked or not, filled or not

- `sendBeacon` always refuses (returns `false`) and is counted `POST`/`beacon`/`api_removed`.
- `fetch` builds its Request once:
  - `keepalive` is always dropped, counted `api_removed`/`keepalive`;
  - a method other than GET/HEAD, an override header (`x-http-method-override`, `x-http-method`,
    `x-method-override`) or a `_method` query key is refused before any request exists. The refusal
    is a rejected `TypeError`, as a route abort looks to the page.
- `XMLHttpRequest.open` / `setRequestHeader` / `send` are wrapped with the same rule. A refused XHR
  fires `error` and `loadend` asynchronously.

### Inert documents

A document is **inert** when its window was born at `about:blank` and the document is still
`about:blank`. Every frame born inside one is inert too.

- **How it is marked:** an own fixed property `__dpsInertDocument` plus a realm-local WeakSet. A
  document is marked when it is first seen at `about:blank`, in any of these places:
  - on the return of the wrapped `window.open`;
  - in the wrapped `Document.prototype.open` / `write` / `writeln`, before the URL changes;
  - on any request.
- **What it refuses:** `fetch`, XHR, `EventSource`, `WebSocket` and `Worker` refuse **every**
  request, GET included, counted `api_removed`.
- **What it loads:** a meta CSP is inserted:
  `default-src 'none'; … form-action 'none'; base-uri 'none'`. It blocks images, scripts, styles,
  frames, `<a ping>` and form POSTs. The mark and the CSP survive `document.open()` (tested).
- **What stays as it was:** the popup's own navigation is routed (its own target), and the
  same-origin page it loads is not inert.

### Counting

- When the popup has no binding of its own yet, reports fall back to the binding of its opener
  (or parent) captured at birth.
- `_report` takes an optional `why`, checked against an allow-list:
  `REPORTED_REASONS = {api_removed, method}`. Anything else is counted as `api_removed`.
- `REPORTED_KINDS` adds `fetch`, `xhr` and `eventsource`.

### Docs

- Module docstring updated: the Methods paragraph, Locked documents, a new **Inert documents**
  paragraph, and the isolation-script comment.
- `docs/technical/api-m4.md` updated: guard items 1 and 5.
- `docs/superpowers/build/M4-DEVIATIONS.md` has a new section, "M4 lane verification round 2 — fix
  for N1".

## Deviation from the finding's wording

The finding asked for refused fetch/XHR to be counted as `api_removed`. A fetch/XHR refused **for its
method** is counted with reason **`method`** and resource type `fetch`/`xhr` instead. That is exactly
the row the route used to write. The existing semantics of `test_crawler`, `test_no_submit` and the
evidence pack are unchanged: a row still means "a POST was refused because of its method".

Beacons, dropped `keepalive` flags and everything refused in an inert document are counted
`api_removed`.

Visible consequence: a page's own POST autosave and unload flush are now refused in JS rather than by
the route. In `test_guard.py` the `closing` rows for POSTs therefore became `method`/`api_removed`
rows. I added a GET `keepalive` to the unload flush, so the route's `closing` abort is still proven
(the positive control now also asserts that the GET arrives unguarded).

## Tests

### New file: `tests/scanner/test_popup_realms.py` (TLS counting server, paths only)

1. **Positive control, unguarded.** The server receives everything the popup realms send:
   - at load: POST beacon, keepalive POST, fetch POST, XHR PUT and fetch DELETE;
   - after the fill: the base64 GET fetch and GET XHR carrying the value, the image with the plain
     value, and the XHR POST.
2. **Three full `ScanRunner` scans** (`new_scan_context`, `harden_js=True`) of the hostile page.
   - Result: **0 hostile requests** reach the server.
   - The load-time and post-fill popups demonstrably opened (`/diag-load-win`, `/diag-fill-win`).
   - Page 1 is `done`, and every allowed row is GET/HEAD.
   - Guard rows: `POST beacon api_removed`, `GET fetch api_removed` (the post-fill value GET),
     `GET xhr api_removed`, and POST fetch / PUT xhr / DELETE fetch refused.
3. **Inert documents, guarded and unguarded.** The popups send subresources (img, script, css,
   iframe src), a form POST in its own popup, an `<a ping>`, a child frame's POST and GET, a
   `wss` socket and an EventSource. The same is tried after `document.open()` + `write`, and from a
   popup with `opener = null`.
   - Unguarded: all of it arrives.
   - Guarded: none of it arrives. The popup's own navigation `/popup-nav` is routed and allowed.
     Counted: POST fetch, GET fetch, websocket and eventsource as `api_removed`.
4. **The binding's allow-lists** (fakes only):
   - an unknown kind is dropped;
   - an unknown reason is counted as `api_removed`;
   - a custom method is counted as `OTHER`;
   - the host is stored value-free (`*.tracker.example`);
   - the URL is not in any `repr`.

### Changes to `tests/scanner/test_guard.py`

- The closing test now expects `POST api_removed`, `POST method` and `GET closing`, and that no
  `/ka-get-*` arrives.
- The filled-page test now expects the keepalive POST as `POST fetch method`, and 1 keepalive drop
  instead of 2.
- More substrings are checked in `ISOLATE_JS`.

### Test-first and negative checks

- On the HEAD `guard.py`, 4 new tests fail: the scan test (the server received
  `POST /hostile-beacon`, `POST /hostile-keepalive`, …, 5 hostile requests) and the guarded inert
  test.
- With the CSP disabled, the guarded inert test leaks `/hostile-frame`, the ping and other
  subresources. So the CSP is load-bearing.

### Flakiness found and fixed in my own test

- In the first DoD-4 run, the unguarded inert control missed `/hostile-img`. The form POST
  navigated the same popup away before the image had loaded.
- Fix: the form now has its own popup, and both positive controls poll for their paths (up to 8 s)
  instead of waiting a fixed 1.5 s.
- Since then the file has passed 3/3 serial runs, 3 parallel runs, and the full DoD-4 rerun.

## Gates (all green before the commit)

| Gate | Result |
|---|---|
| `./lane.sh ruff format dps tests tools` | 294 files unchanged |
| `./lane.sh ruff check dps tests tools` | All checks passed |
| `./lane.sh mypy dps` | Success: no issues found in 119 source files |
| `./lane.sh pytest -q -rs` (stack up, `DPS_TEST_TESSERACT_CMD` = docker tesseract wrapper) | **2363 passed, 4 skipped**, 738 s (`pytest_full.txt`) |
| DoD 4: `DPS_TEST_REQUIRE_SERVICES=1 DPS_TEST_REQUIRE_TESSERACT=1 pytest -m "browser or db" tests/scanner tests/test_no_values_in_logs_m4.py tests/test_tools_m4.py` | **232 passed, 0 skipped**, 548 s (`integration.txt`; rerun after the control-polling fix) |
| `test_popup_realms.py` + `test_guard.py` after the final edits | 71 passed |
| Frontend | not run: no frontend files changed |

- **Full-suite skips:** 2 × host tesseract on PATH (none installed; the OCR tests ran through the
  wrapper), and the `[ner]` presidio/spaCy extra, which is not installed in this lane. These are the
  same as the verifier's.
- **Timing:** the full suite ran before the last test-only edits (control polling, the form in its
  own popup, the report test's host expectation) and the final docstring wording. All of those were
  re-run afterwards (DoD-4, plus 71 tests in the two files).

## Stack, smoke and probes

- **Bring-up:** `docker compose --profile scanner --profile sources --profile docs up --build -d`
  returned rc 0, with every service healthy.
- **Browser-worker:** the image contains the new `ISOLATE_JS` (`__dpsInert` present).
  - The final commit only rewords a docstring in `_report`. The image was not rebuilt for that
    change, so the running code is functionally identical.
- **Smoke:** `DPS_SMOKE_SCANNER=1 ./lane.sh python tools/smoke_test.py` passed **all 45 checks**
  (`smoke.txt`).
  - No submission reached the site: `non_get +0, form_get +0, logout +0, admin +0`.
  - `non_get_allowed 0`; blocked `{GET: 1, POST: 60}`.
  - Evidence pack: submissions 0.
- **The verifier's own ScanRunner probe** (`runner/test_probe_runner_https_popup.py`, copied):
  6 of 6 runs.
  - non-GET received `[]`, and no `/hostile*` GET received.
  - guard `non_get_allowed 0`, page `done`.
  - Blocked: `GET api_removed`, `POST api_removed`, `POST method`, `PUT api_removed`
    (`runner/out.txt`).
  - Before the fix it received `POST /hostile-beacon-load` and `POST /hostile-keepalive-load` in 5
    of 5 runs.
- **The verifier's guard-level probes, without the crawler's close** (`g34_out.txt`). Each guarded
  probe ran twice.

  | Probe | Unguarded control | Guarded |
  |---|---|---|
  | `g4` (no close handler) | 6 non-GET received | **0** received |
  | `g4` crawler-close variant | 6 non-GET received | **0** received |
  | `g3b` (post-fill popup GET fetches, no close handler) | 12 leak requests, 11 carrying the value | **0** leak requests, 0 carrying the value |

  - In `g4` guarded, the popup's DELETE, beacon, fetch POST and XHR PUT are counted `api_removed`,
    and the main page's and iframe's POSTs are counted `method`.
  - In `g3b` guarded, the popup's GET fetch and XHR are counted `api_removed`.
- **Shutdown:** `docker compose ... down` returned rc 0. No containers are left; the volumes are
  kept.

## Open issues and notes

- **Images from a filled routed page.** A filled page may still load passive resources. An image
  whose URL carries a value that is **encoded** (base64) is not caught by the needle check, in a
  routed realm too. This is the pre-existing documented design ("only passive resources still
  load"), unchanged by this fix. Inert popup documents are now stricter: they load nothing at all.
- **Popup row attribution.** Rows from a popup realm that reports through its own binding carry
  `page_no None`, because popups are never bound to a crawl page. Through the opener's binding they
  carry the opener's page. Both are counted. This is documented in `_report` and in the deviations
  log.
- **Window reuse.** When a popup's window is reused for its same-origin page, `ISOLATE_JS` and
  `HARDEN_JS` do not run again for that document. The wrappers installed at the window's birth still
  apply, which was already the behaviour before this fix.
- **Screenshots.** The verifier's untracked `docs/screenshots/m4/` is still uncommitted. It is not
  mine to commit.
