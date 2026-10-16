# M4 lane verification, round 2 (branch `m4`, HEAD `6ccecae`)

Verifier: independent agent, 2026-10-14, worktree `/Users/neelu/document_intelligence/dps-wt-m4`. Everything
below was run by me; I trusted no earlier report. I changed no code.
Probes and outputs: `/Users/neelu/.cache/dps-build/reports/probe-m4-verify-r2/`.

Resume check: no earlier r2 report existed; tree clean except r1's untracked `docs/screenshots/m4/`
(42 PNGs). I moved those to `probe-m4-verify-r2/r1-screens-backup/` and regenerated 42 screenshots
from this round's walk into `docs/screenshots/m4/` (left uncommitted, see verdict).

## Verdict: FAIL (1 new blocking finding)

All gates, the stack, the smoke, the integration suite, the console walk and the privacy proofs are green,
and all five carried findings are fixed on HEAD. But a new D4/D-C hole reproduces through the
production `ScanRunner` over HTTPS (finding N1 below). So, per instructions, I did **not** commit the
screenshots and did **not** run `down -v`; I ran `docker compose ... down` (0 containers left, volumes kept).

## Blocking finding N1 - POSTs from an about:blank popup's realm leave the browser over HTTPS, uncounted

- **Where:** `dps/layers/scanner/guard.py` (`ISOLATE_JS`, route attribution); `crawler.py:356` `_on_page`.
- **What:** a page's script opens a popup with `window.open('')` and uses the popup's realm synchronously
  (`w.navigator.sendBeacon(...)`, `w.fetch(url, {method:'POST', keepalive:true})`). Playwright never routes
  these requests (the guard counts nothing), and over HTTPS the scope proxy only sees the CONNECT.
  `ScanRunner._on_page` closes the popup through `guard.close`. That stops plain fetch/XHR, but the beacon and
  the keepalive POST survive the close and are delivered.
- **Proof (production path):** `runner/test_probe_runner_https_popup.py`. It runs a full `ScanRunner` scan
  (`new_scan_context`, harden on) of an HTTPS counting server whose `/apply` page does the above at load.
  - Result: in 5 of 5 runs, the server received `POST /hostile-beacon-load` and `POST /hostile-keepalive-load`.
  - The scan's own records disagree: guard `non_get_allowed` = 0, and the page row is `done`
    (`runner/out_beacon.txt`, `out_beacon2.txt`).
  - Controls in the same page, all blocked as designed:
    - the main page's own `sendBeacon`;
    - an about:blank iframe's beacon and keepalive;
    - a `srcdoc` iframe's beacon;
    - the same popup's fetch POST and XHR PUT.
  - Over plain HTTP the proxy refuses all of it.
- **Guard-level variant** (`r/g4_unfilled_popup_methods.py`, `new_scan_context`, no `_on_page` handler; HTTPS):
  - The unfilled page's popup sends `w.fetch` POST, `w` XHR PUT and `w.fetch` DELETE, and all three are
    **received** (2 of 2 runs). The guard counts none of them.
  - With a crawler-style close handler added (`*_crawlerclose.py`), `POST` (beacon) is still received in
    3 of 3 runs (`g34_close_out.txt`).
- **Related, mitigated in production:** `r/g3b_post_fill_popup_fetch_scanctx.py`.
  - Without a popup-close handler, a popup that a **filled** page opens after `arm` sends GET fetch/XHR
    carrying the base64 fill value. 2-5 requests were received per run, and the guard counted none
    (`g3b_out.txt`). This contradicts the docstring: "A popup opened by a filled page is a filled page …
    every request a script makes is aborted".
  - With `_on_page`-style closing, 0 were received (`g34_close_out.txt`). In the full runner with a 3 ms popup
    race, 0 were received (`runner/out_race.txt`).
  - Not reproduced through `ScanRunner`, but the property rests on the crawler's close racing the page, not
    on the guard.
- **Why blocking:** this is D4 ("only GET and HEAD leave the browser … whatever page issued it"). D-C requires
  it to hold "even when a page is hostile". The evidence pack would claim 0 non-GET while POSTs left the
  browser. It is the same class as r1 F1: a popup realm that the route cannot see.
- **Suggested fix:** do not rely on routing in realms that Playwright cannot route. Change `ISOLATE_JS` in
  every realm, whether locked or not:
  - make `sendBeacon` always refuse and count (it is always a POST, which the guard would block anyway);
  - always drop `keepalive`;
  - make the `fetch`/`XMLHttpRequest.open` wrappers refuse any method outside GET/HEAD (and method
    overrides), and count them `api_removed`.
  - Optionally, refuse every script request in a document whose URL is `about:blank` with an opener.

  Add an HTTPS counting-server test through `ScanRunner`, with an unguarded positive control:
  - at load: popup beacon, keepalive, fetch POST, XHR PUT and DELETE;
  - after the fill: popup GET fetch carrying the value;
  - assert 0 received and the guard counts > 0.

  Update the guard docstring and `M4-DEVIATIONS.md`.

## Carried findings: all fixed (re-run on HEAD)

| # | Finding | Re-run result |
|---|---|---|
| 1 | D4/D-C race in `ScanGuard.arm` | `r/g1_blank_popup_worker.py` over HTTPS + proxy. 8 guarded runs: each `armed True locks_failed 0`, **0 upgrades**, 0 value in URL, 0 non-GET. Unguarded control: 53 upgrades carrying the value (`g1_out.txt`). Extra post-arm sync-realm probe (`r/g2_post_arm_realms.py`): workers and sockets in post-fill popups and iframes are refused (`SecurityError`), 0 upgrades in 3 of 3 runs (`g2_out.txt`). |
| 2 | Short dates with `_ , : + ~ \|` and Devanagari digits | `r/u1_urls.py det`. Every r1 row, including `1_5_90`, `12_5_90`, `1,5,90`, `1:5:90`, `1~5~90`, `1+5+90`, `1%2B5%2B90`, `1\|5\|90`, `12:may:90`, `12+may+90` and `१_५_९०`, now gives `/dob/{id}` in both the redacted URL and the template, with the None and the `Detector(en_IN)` classifiers alike. `/dob/1/5_90` gives `/dob/{id}/{id}`. The non-date control rows are unchanged from r1 (see the open items). (`u1_out.txt`) |
| 3 | B4: `display:contents` and slotted text | `b45/probe_r3.py`, docker tesseract, `--psm 11`. **contents:** control 4+4; masked 0+0; both obfuscated captures withheld (`leakage`, fail closed). New page **slotted_email** (slotted email, slot fallback email, nested `display:contents`, labelled name): control 4+4; masked, synthesize and obfuscate all 0+0. **slotted** (bare unlabelled name): the email is masked, and the name is read once in all three captures. That is detector recall for an unlabelled name, not a census gap: the fix report's plain-span control behaves the same. Non-blocking (N9 class). |
| 4 | B5: `img alt`, optgroup and other attribute strings | **alt:** control 3+3, then 0+0 in masked, synthesize and obfuscate. **optgroup:** control 3+3, then 0+0 in all three. New page **inputimg** (`input type=image` alt, `option label`, placeholder): control 5+5, then 0+0 in all three (`b45/out.txt`). |
| 5 | R3-B1: click handler runs a denied route | `clicks/test_probe_r3_clicks.py`. **onclick_hash:** no skipped/deny row; 2 `spa/reached/deny` events; `scope_routes_reached` 2. **pushstate:** the same, with target `/logout`. (`/api/session/end` still runs twice: this is now recorded truthfully, as designed.) (`clicks/out.txt`) |

## 1. Gates

| Gate | Result |
|---|---|
| `./lane.sh uv pip install -e ".[fpe,docs,db,connectors,scanner,uitest,dev]"` | ok |
| `ruff format --check dps tests tools` | 293 files already formatted |
| `ruff check dps tests tools` | All checks passed |
| `mypy dps` | Success: no issues found in 119 source files |
| `pytest -q -rs` (stack up; `DPS_TEST_TESSERACT_CMD` = docker tesseract wrapper) | **2356 passed, 4 skipped**, 736 s (`pytest_full.txt`). Skips: 2 × host tesseract on PATH (none installed; the scanner OCR tests ran via the wrapper); `[ner]` presidio and spaCy (the extra is not installed in this lane). |
| DoD 4: `DPS_TEST_REQUIRE_SERVICES=1 DPS_TEST_REQUIRE_TESSERACT=1 pytest -m "browser or db" tests/scanner tests/test_no_values_in_logs_m4.py tests/test_tools_m4.py` | **226 passed, 0 skipped**, 534 s (`integration.txt`) |
| `cd frontend && npm ci && npm run typecheck && npm test && npm run build` | ci ok; tsc ok; **180/180** tests (11 files); build ok; check-bundle ok (`frontend.txt`) |

## 2. Stack and smoke

- **Bring-up:** `docker compose --profile scanner --profile sources --profile docs up --build -d` returned rc 0.
  - Healthy: app, dps-db, worker, browser-worker, demo-site, minio, source-pg, azure-ocr-mock.
  - Exited 0: migrate, minio-init, seed-sources, demo-secrets.
- **Readiness:** polled `/readyz` on 8414 (no `--wait`). `ready true`, `browser: ok (1 online)`, db and jobs ok.
- **Browser-worker:** Playwright `1.55.0`. It runs the new code: `_SETTLE_PASSES` 50, `_PUNCT` `[\W_]{0,3}`,
  `scope_routes_reached` present. The app has the same `_PUNCT`.
- **Smoke:** `DPS_SMOKE_SCANNER=1 ./lane.sh python tools/smoke_test.py` → **all 45 checks passed** (`smoke.txt`).
  This includes persona export with 0 demo values and evidence submissions 0.

## 3. Console walk (private Playwright 1.55.0 in `pw-m4`)

Walk script: `walk.py`, output in `walk.txt` and `walk_result.json`.

- **Scans:** two scans started from `/scanner/new` (mockup 12):
  - synthesize: `s_01M4WHGFZYSC68RZMXSRNG16GM`;
  - obfuscate: `s_01M4WHJYWVXDRFGP42MMRR8PKP`.

  Both succeeded with 21 pages, 63 fields, 51 personal fields, 9 risk flags and 3 captures. Guard
  `non_get_allowed 0`, blocked `{GET: 1, POST: 71}`. The key card was shown once; it shows only the key id
  and a seed hash prefix.
- **Scan detail (mockup 04):**
  - Parallel view: Synthetic persona and Obfuscated tokens, each with Screens (2 blob images) and Fields.
  - Inventory: 59 entity chips and the risk flags.
  - Captures tab: reversible-map button (obfuscate scan).
  - Evidence pack opens at `/reports/evidence/…`.
- **Exports downloaded from the console:** inventory CSV and JSON, Export inventory, Export personas, and the
  reversible map.
- **Variants:** dark and light at 1440; obfuscate dark and synthesize light at 390.
- **Console errors:** 0 across 8 page sessions. **Horizontal scroll:** none at 1440 or 390.
- **No submission:** demo-site `/__received` delta across the walk was `non_get 0, form_get 0, logout 0, admin 0`.
  The absolute counters (16/4/2/2) come from the direct `httpx` positive controls in
  `test_no_submit.py` and `test_demo_site.py` during the two pytest runs. The walk does not create them.
- **Mockups:** I compared against `mockup-12/04-…png` (rendered into the probe directory). The sections and
  their order match. The deviations are the documented ones:
  - the capture lives in the Captures tab;
  - the screens are whole-page thumbnails;
  - the inventory's last header is clipped ("FILLED VALU…") inside its card at 1440. This is cosmetic, not page scroll.

  The full-page screenshots show the fixed sidebar mid-page. That is an artefact of full-page capture.
- **Screenshots:** 42 PNGs in `docs/screenshots/m4/`, not committed (FAIL).

## 4. Programme plan §6 proofs

- **OCR of every PNG artefact of both scans** (`ocr_scan.py` → `ocr.txt`; tesseract `--psm 11`; needles are the
  11 demo values in exact, squashed and digit forms, plus name tokens):
  - Artefacts per scan: 21 original, 21 annotated, 12 filled_synthesize, 12 filled_obfuscate and 3
    masked_capture (obfuscate also has 3 capture_json).
  - filled_* and capture_json: **0**.
  - masked_capture: **0** on `/profile` and `/profile/contact`. `application_ref` is read 2× on
    `/apply/review` (known open item N9: not a detected entity; every PII entity on that page is masked).
  - Positive control (`ocr_control.py`): unguarded `/profile` gives 16 hits (9 entities); `/apply/review` gives 9.
- **Exports, source vs target (counts only):** 9 downloaded files (persona exports, inventory CSV/JSON/export,
  reversible map): **0** demo values.
- **Targets written by the smoke (L2), per column** (`src_vs_tgt.txt`, `parquet_vs_source.txt`):
  - **`bank_uat.customers_smoke` vs `bank.customers`** (20 000 rows):
    - Set survivors: 0 for `full_name`, `national_id`, `tax_id`, `email`, `mobile` and `notes`.
    - Non-null counts are preserved.
    - `customer_id` is transformed: only 4 accidental collisions. On those 4 rows, 0 personal columns are equal.
  - **The 4 parquet exports in MinIO `dps-exports/smoke/…`** (20 000 rows each):
    - 0 survivors for name, national_id, tax_id, email, mobile and notes;
    - `customer_id` has 2-8 accidental collisions;
    - `date_of_birth` cannot be measured by set membership (small domain).
  - **Product verification on the evidence:** strict, 143 455 values checked, 0 hits on both runs.
  - **Dry run in place:** `bank_restore.contacts` is identical to `bank.contacts` (40 000/40 000 rows), so
    nothing was written.
- **No submission (counting servers):**
  - `/__received` stayed at 0 across the walk.
  - `test_no_submit.py` passed with services required.
  - N1 above is the exception for hostile HTTPS pages.
- **Log scan** of all 12 container logs of this project (about 730 lines) for the demo values and the 10
  key-material and seed strings captured in memory at scan creation: **0 value hits, 0 secret hits**.
- **DB tables:** `dps_audit_log` (1245 rows), `scan_page` (594), `scan_field` (1464), `scan_event` (2890),
  `scan_request` (1945), `dps_job` (67) and `dps_evidence` (45): **0** value hits and **0** secret hits. The
  audit rows are value-free.
- **LLM metric:** `dps_llm_raw_pii_sent_total 0.0`. The metric is global; the per-tenant metric is M5.

## 5. DoD status

All 8 items are green: unit gates, console build, stack, integration, smoke, browser vs mockups, demo outcome,
docs (HANDOFF, README, `api-m4.md` present; `scope_routes_reached` documented). The programme acceptance D4/D-C
("must hold even when a page is hostile") fails under N1.

## 6. Open items (non-blocking)

- **N9:** `application_ref` is readable in the `/apply/review` captures.
- **Bare unlabelled name not detected** (slotted or plain span): detector recall.
- **N-r3-1:** slashless `#logout`. The `noslash` click case runs `/api/session/end` twice and records no
  reached event (`scope_routes_reached` 0).
- **N-r3-2:** stamp swap with `harden_js=False` submits a GET form (`/api/formget` hit twice). Hardening is on
  by default.
- **N-r2-1:** HTTPS redirect hops to denied paths (counted).
- **N8/N12** from G3 r3.
- **B5 audit residuals:** CSS generated content, `li value`, closed-select option label, `<track>`, canvas,
  video poster.
- **Passive image carrying a base64 fill value** from a filled page or its popup: accepted residual since
  G2-r1. Fill values are synthetic or tokens.
- **URL controls unchanged from r1:**
  - `/dob/12/5` and `/dob/1/x/5/90` stay in the redacted URL (not date shapes);
  - `/dob;d=1/5/90` gives `/dob;d=<v>/5/90` in the redacted URL, with the template value-free.
- **Inventory header clipped** at 1440.

## 7. Cleanup

- `docker compose --profile scanner --profile sources --profile docs down`: rc 0, 0 `dps-wt-m4` containers,
  volumes kept.
- No other project touched.
- Nothing committed.
- `git status`: only `?? docs/screenshots/m4/` (this round's 42 PNGs).
