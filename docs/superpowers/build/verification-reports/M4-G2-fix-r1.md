# M4 G2 fix, round 1 (review M4-G2-review-r1: B1, B2, B3)

**Branch:** `m4` in `/Users/neelu/document_intelligence/dps-wt-m4`. **Commit:** `54a7d5d fix(m4): delete fetchLater and WebSocketStream in every realm; month-name dates are values` (on top of `97695cd`). Author: NilenduGanguli <nilendu.ganguli@gmail.com>. No trailers. Explicit pathspec. Working tree clean afterwards.

Resume check: the working tree was clean at `97695cd` and there was no earlier fix report, so I started fresh.

## What changed

### B1 `fetchLater` and B2 `WebSocketStream` (`dps/layers/scanner/guard.py`)
- `ISOLATE_JS` deletes `fetchLater`, `WebSocketStream` and `WebTransport` from `window` **and from every object on its prototype chain**. This happens always, on filled and unfilled pages and in every realm (the init script runs in every frame before page script). It sits in the same deletion loop as `SharedWorker` and `RTCPeerConnection`. None of these APIs is routed: the browser sends a `fetchLater` POST itself, and `route_web_socket` replaces only `WebSocket`. Over HTTPS the proxy sees only the tunnel.
- I took the review's preferred option: delete, rather than a lockable wrapper. A deleted API is a `ReferenceError`. That means scan pages that feature-detect it fall back to `WebSocket` or `fetch`, which are routed.
- Module docstring, `ISOLATE_JS` comment and residuals list are updated. One residual remains: a dedicated worker of an *unfilled* page can open a `wss://` `WebSocketStream`, because `ISOLATE_JS` does not run in workers. This is the same class as the documented worker `WebSocket` residual. Filled pages terminate their workers and refuse new ones. The probe confirms `fetchLater` is Window-only (`undefined` in a dedicated worker).

### B3 month-name dates (`dps/layers/scanner/urls.py`)
- New `_MONTH_DATE` is used in `_value_like`, so it also covers `redact_url`, `template`, `_safe_key`, matrix keys and `template_is_value_free`.
- It matches a day (optionally ordinal, e.g. `2nd`) and a year (2 or 4 digits) around a month name, in all six orders. Separators can be absent or up to three of `\s - . / _ , '`. Digit and letter boundaries are enforced.
- Month names covered:
  - English 3/4-letter and full names.
  - Spanish full names and abbreviations (`ene`, `abr`, `ago`, `dic`, `mayo`, `septiembre`/`setiembre`, …).
  - Hindi (Devanagari) with spelling and nukta variants (फ़/फ़ precomposed, सितंबर/सितम्बर, अक्टूबर/अक्तूबर, …).
- Still routes: `may-24`, `12-may`, `mayor`, `march`, `dec2`, `sept-update`, `julio` (tested).

## Tests (each written first and seen failing)
- `tests/scanner/test_urls.py`:
  - +13 cases in `test_redact_url_hides_…`, run with both the None classifier and the real `Detector(en_IN)`. The secrets list gains `May`/`may`/`Sept`/`Jan`/`jan`/`मई`/`%E0%A4%AE`.
  - +7 cases in `test_the_persisted_template_is_value_free`.
  - +11 in `test_a_page_record_refuses_a_raw_template`, plus an error-text check.
  - A negative case in `test_redact_url_keeps_plain_route_segments`.
  - Before the fix, **28 failed** (some of the new cases were already caught by the whitespace, 4-digit and alnum-8 rules and are kept as regressions).
- `tests/scanner/test_guard.py`, new HTTPS proof suite:
  - Fixtures: `tls_cert` (a throwaway EC self-signed certificate for 127.0.0.1, generated with `cryptography` in a temp dir) and `https_counting_server` (`_handler` over TLS, handshake in the handler thread, quiet on aborted handshakes).
  - `_TrustingBrowser` builds `new_scan_context`'s own context, changing only `ignore_https_errors=True`.
  - `/tls-page` is served with `<a ping>` and a CSP `report-uri`.
  - `test_https_positive_control_an_unguarded_page_delivers_what_the_guard_must_stop`: unguarded, the server receives `POST /fl-timer`, `POST /worker-post` and `POST /csp-report`, and ≥1 upgrade from `WebSocketStream`.
  - `test_https_fetch_later_and_websocket_stream_are_gone_in_every_realm[unfilled|filled]`:
    - Positive control: `/tls-page` arrived over TLS.
    - **0 non-GET and 0 upgrades** received.
    - `typeof` checks return `undefined` and `in window` returns false, for `fetchLater`, `WebSocketStream` and `Window.prototype.fetchLater`. Uses throw `ReferenceError`.
    - In a fresh `about:blank` iframe and a `srcdoc` iframe the APIs are `undefined` and refused.
    - A plain `wss://` `WebSocket` is refused and counted `(5, GET, websocket) api_removed` when filled, and closed by the route when unfilled.
    - Before the fix both variants failed: the server received `POST /fl-frame`, `/fl-timer`, ….
    - Mutation check: with only `WebSocketStream` removed from the delete list, the test fails with `upgrades == 1`.
  - `test_https_nothing_but_get_leaves_where_the_proxy_is_blind`:
    - Over TLS: a page POST, an XHR PUT, a worker's `fetch` POST, `<a ping>`, a CSP report, and the unload beacon and keepalive of an unfilled and a filled `/unload` page closed through `close_scan_context`.
    - The server received 0 non-GET and 0 upgrades.
    - Positive controls: `/tls-page`, both `/unload` pages and `/pinged-nav` arrived.
    - Blocked `POST method` and `PUT method`, plus `closing`/`api_removed`, were counted.
  - `test_isolate_js_is_always_installed_…` needles: `'fetchLater'`, `'WebSocketStream'`.
  - The HTTPS tests were run 3× in a row and all passed each time (about 15 s per run).

## Gates
| Gate | Result |
|---|---|
| `./lane.sh ruff format dps tests tools` | 266 files left unchanged |
| `./lane.sh ruff check dps tests tools` | All checks passed |
| `./lane.sh mypy dps` | Success: no issues found in 111 source files |
| `./lane.sh pytest -q` with lane `dps-db` up | **1881 passed, 71 skipped** (review baseline was 1826/71, +55 new). The skips are other milestones' services: source-pg 31, minio 3, azure-ocr-mock 3, app 3, ner/spaCy 1, tesseract 1, presidio 1 |
| `DPS_TEST_REQUIRE_SERVICES=1 ./lane.sh pytest -q tests/scanner tests/test_config_m4.py tests/db` (lane `dps-db`; the demo site is auto-started by the fixture) | **444 passed**, 0 skipped |
| frontend | not touched, not run |

I ran `docker compose up -d dps-db` from the lane, then `docker compose down` afterwards. Only this lane's project was touched.

## Deviations (logged in `docs/superpowers/build/M4-DEVIATIONS.md`, section "G2 review round 1 — fix")
- B2: the review's test wording says "counted `api_removed`". A deleted API cannot be used, so nothing is counted. The proof is the counting server (0 upgrades) plus `typeof` in every realm. The plain `wss://` `WebSocket` is still counted `api_removed` on filled pages over HTTPS.
- `WebTransport` is deleted as well. This goes beyond the review; it is defensive and requires QUIC, which is off anyway.
- No contract name changed. The contract and the M4 plan do not list these rules, so they needed no edit.

## Open issues (non-blocking, unchanged from review §5)
- A `WebSocketStream` or `WebSocket` over `wss://` from a dedicated worker of an *unfilled* page is a documented residual.
- Review §5 items 1–8 (base64 in passive URLs, cross-document relay, capture rule for shadow/canvas, `%09` deny trailing, names in segments, `add_request_counts` idempotency, N-c port, docstring length) are left for later tasks, as the review assigned.
