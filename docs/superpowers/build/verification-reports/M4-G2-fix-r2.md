# M4 G2 fix, round 2 (M4-G2-review-r2 B1, B2)

**Branch:** `m4` in `/Users/neelu/document_intelligence/dps-wt-m4`. **Commit:** `6cfdbdf`
"fix(m4): arm locks and fills every open page of the scan; dates split over path segments or with
connector words are values". Author NilenduGanguli <nilendu.ganguli@gmail.com>, no trailers.
Resume check: the tree was clean at `54a7d5d` and no earlier r2 fix existed, so I started from scratch.

Files: `dps/layers/scanner/guard.py`, `dps/layers/scanner/urls.py`, `tests/scanner/test_guard.py`,
`tests/scanner/test_urls.py`, `docs/superpowers/build/M4-DEVIATIONS.md` (new section "G2 review
round 2 — fix"). No contract name changed. The public signatures of `arm`, `redact_url`, `template`
and `template_is_value_free` are unchanged.

## B1: pre-fill popup relays a value to its worker's `wss://` socket (D4 / D-C)

**Test first.** `test_https_a_popup_opened_before_the_fill_relays_nothing[unguarded|guarded]`:
- It uses the TLS counting server.
- `/relay` opens `/relay-popup` at load. The popup starts a blob dedicated worker that sends whatever it receives on `wss://…/leak-worker?d=<base64>`.
- The popup's own realm also tries `new WebSocket` and `new Worker` on every relay.
- The filled page relays the value by `postMessage` and by `BroadcastChannel`.
- An unrelated tab (`/tls-page`) is also open at the fill. It tries a GET `fetch` with a custom header.

**Before the fix:** the guarded run failed with `assert (2 == 0)`, i.e. 2 upgrades. This reproduces probe q6.

**Fix (`ScanGuard.arm` / `_lock` / new `_open_pages` and `_lock_frame`):**
- `arm` now locks every frame of every open page of the context (`context.pages`), not only `page.frames`. Locking terminates their workers and refuses sockets, workers, beacons and keepalive, each counted `api_removed`.
- `arm` returns False if any live frame cannot be locked.
- A frame that throws while it navigates is retried up to 3 times, 100 ms apart, because its next document is locked at birth by `LOCK_JS`. A detached frame counts as done, as before.
- **Deviation (stricter than asked):** every page open at `arm` is also *marked filled*, so the route rules apply to it: no navigation, no script requests, only passive loads.
  - Locking alone would still let an unrelated tab that gets a value over BroadcastChannel send it in a header of a GET fetch to an allowed host. This closes round-1 §5.2 for pages open at the fill.
  - It does not affect the planned Task 14 crawler, which fills one page at a time and closes popups through the guard.
- I updated the docstrings: filled pages, locked documents, and the residuals. The dedicated-worker `wss://` residual now holds only while no page of the scan is filled.

**After the fix:**
- Guarded run: 0 upgrades, 0 `/leak-*` requests and 0 non-GET. The popup's later `new WebSocket` and `new Worker` are counted `api_removed`. The unrelated tab's fetch is blocked and counted `filled_request`. `locks_failed == 0`.
- Unguarded positive control: ≥2 upgrades, the worker's socket URL carries the base64 value, and `/other-fetch` arrives.
- **Mutation check:** marking only the armed page filled makes the unrelated-tab assertion fail.
- The reviewer's probe `r2/q6_popup_relay.py guarded` now prints `armed True`, `upgrades: []` and `value in an upgrade URL: False`.
- I ran the HTTPS guard tests 3 times in a row: 7 passed each time.

## B2: dates split over path segments, or written with connector words

**Tests first.**
- `test_redact_url_hides_…`: +8 URLs, run with both the None classifier and the real `Detector(en_IN)`. These are the six probe rows plus `/12/de/mayo/de/90/x` and `/12th/of/May/1990`.
- `test_the_persisted_template_is_value_free`: +7, with both classifiers. This includes a hash route `#/dob/12/may/90`.
- `test_a_page_record_refuses_a_raw_template`: +11. These are the raw forms and the half-templated forms that the old code produced (`{id}/may/{id}`, `may/{id}/{id}`, `{id}/May/{id}`, `{id}/de/mayo/de/{id}`, and a hash route).
- `test_value_free_templates_pass_the_record_guard`: +3 forms that must still pass (`calendar/{id}/{id}/{id}`, `news/may/{id}`, `archive/{id}/may`).
- **Before the fix:** 36 failed.

**Fix (`urls.py`):**
1. `_split_date`:
   - It takes every window of up to `_DATE_WINDOW = 5` consecutive non-empty decoded segments, joins it with `-`, and searches it with `_DATE_SHAPE` and `_MONTH_DATE`.
   - Every segment touched by a match that spans 2 or more segments becomes `{id}`.
   - It is used in `_path`, so it applies to both `redact_url` and `template`.
   - **Deviation:** windows are up to 5 segments rather than 2–3, so that connector forms split over segments are covered.
2. `_SEP` between date parts allows up to two whole connector words `de`/`del`/`of`/`the`. `dec` and `december` are not read as `de`.
3. `template_is_value_free` mirrors both:
   - It shares `_MONTH_DATE`.
   - It runs the window check with `{id}` and `<ENTITY>` placeholders read as numbers.
   - It refuses a date match that touches a readable segment.
   - `{id}/{id}/{id}` still passes.

**Probe `r2/q3_urls.py`:** all six rows now give `{id}`-only paths and templates, for both classifiers. Timing on a hostile 1,800-segment path: 0.03 s.

**Over-redaction:** a route shaped like a date (`/page/1/2/10`) becomes `{id}/{id}/{id}`. The reviewer stated this is the accepted failure mode.

## Gates (final, on the committed tree)

| Gate | Result |
|---|---|
| `./lane.sh ruff format dps tests tools` | 266 files left unchanged |
| `./lane.sh ruff check dps tests tools` | All checks passed |
| `./lane.sh mypy dps` | Success: no issues found in 111 source files |
| `./lane.sh pytest -q` (lane `dps-db` up) | **1924 passed, 71 skipped**, 1 warning (starlette httpx deprecation) |
| `DPS_TEST_REQUIRE_SERVICES=1 ./lane.sh pytest -q tests/scanner tests/test_config_m4.py tests/db` | **487 passed**, 0 skipped |
| frontend | not touched, not run |

The 71 skips are the same count as in the review: other milestones' services (source-pg, minio, azure-ocr-mock, app, tesseract, spaCy/ner, presidio). There are no scanner or db skips. Docker: I started only `dps-db` for this lane and took it down afterwards with `docker compose down`. I touched no other project.

## Open issues (non-blocking, unchanged from review §5)
- `12-mei-90` (Dutch or Indonesian month names) stays readable. Only English, Spanish and Hindi month names are in scope.
- The persona-value GET residuals (image URL, speculation-rules prefetch, cookie) are still for the Task 14/15 docs.
- Deny-rail edge readings (§5.3), N-c port binding, N2 retry idempotency, and the style nits (§5.7) are still open as assigned.
- `test_https_nothing_but_get_…` still lacks an unguarded unload control for HTTPS (§5.5).
