# M4 lane verification, round 1 (branch `m4`, HEAD `fabbd81`)

Verifier: an independent agent. I ran every gate, probe and walk below myself on 2026-10-14 in
`/Users/neelu/document_intelligence/dps-wt-m4`. I trusted no earlier report.

- **Tree:** clean at the start. At the end the only change is the untracked folder
  `docs/screenshots/m4/` (44 PNGs), which I did not commit.
- **I changed no code.**
- **Probes and outputs:** `/Users/neelu/.cache/dps-build/reports/probe-m4-verify-r1/`.

## Verdict: FAIL

Every gate, the live stack, the smoke test, the console walk and the privacy scans are green.

All five carried blocking findings are still open on HEAD. No commit after `d987144` or `0cf0140` touches
them, and I re-ran each one's probe on HEAD:

| # | Finding | Re-run on HEAD |
|---|---|---|
| 1 | **D4/D-C race in `ScanGuard.arm`** (`dps/layers/scanner/guard.py` 498-536). `_lock` registers `LOCK_JS` via `add_init_script` *after* `_open_pages` takes its snapshot. | `r/g1_blank_popup_worker.py` over HTTPS through the scope proxy, 3 guarded runs. Each run: `armed True locks_failed 0`. Upgrades carrying the base64 fill value: 1, 0 and **1**. Unguarded control: 61. The fill value left the browser on a wss upgrade in **2 of 3** runs (`g1_out.txt`). |
| 2 | **Short-date shapes survive `redact_url`/`template`** (`dps/layers/scanner/urls.py:58` `_DATE_SHAPE` `[-./ ]`, `:84` `_PUNCT`). | `r/u1_urls.py det`. `/dob/1_5_90`, `12_5_90`, `1,5,90`, `1:5:90`, `1~5~90`, `1+5+90`, `1%2B5%2B90`, `1\|5\|90`, `12:may:90`, `12+may+90` and `१_५_९०` all stay unredacted in both the redacted URL and the template, with `template_is_value_free == True`. `/dob/1/5_90` stays in the redacted URL. Same result with the None classifier and with `Detector(en_IN)` (`u1_out.txt`). |
| 3 | **B4: text under a `display:contents` parent is never classified or boxed** (`dom.py` 64/302 `visible()`/`textOk` require `el.getClientRects().length > 0` on the parent). | `b45/probe_r3.py` with tesseract 5.5.0 in `data-privacy-services:m4`, `--network none`. **contents:** OCR reads 2+2 copies in masked, synthesize and obfuscate (control 4+4). **slotted:** findings 0; both values read in all three captures (`b45/out.txt`). |
| 4 | **B5: `img alt` (broken image) and `optgroup label` are not in `CENSUS_JS`** (`dom.py`). | **alt:** 1+1 values readable in masked, synthesize and obfuscate (control 3+3). **optgroup:** 1+1 readable in all three. |
| 5 | **R3-B1: a click that lands on a denied route is recorded as `skipped/deny`** (`crawler.py` 901-927 `_discover_clicks` → `_consider`). | `clicks/test_probe_r3_clicks.py -k "onclick_hash or pushstate"` (`clicks/out.txt`). **onclick_hash:** hits `/api/session/end` = **2**; rows `(2, '/', 'skipped', 'deny', 'link')`. **pushstate:** `/api/session/end` = **2**; row `(2, '/logout', 'skipped', 'deny', 'link')`. The records say the deny list held for a route the scanner ran. |

Because of these, I did **not** commit the screenshots and did **not** run `down -v`. I ran
`docker compose down`, and no container of this project is left.

## 1. Gates (all run by me)

| Gate | Result |
|---|---|
| Re-install `./lane.sh uv pip install -e ".[fpe,docs,db,connectors,scanner,uitest,dev]"` | ok |
| `./lane.sh ruff format --check dps tests tools` | 293 files already formatted |
| `./lane.sh ruff check dps tests tools` | All checks passed |
| `./lane.sh mypy dps` | Success: no issues found in 119 source files |
| `./lane.sh pytest -q -rs`, with the lane stack up (scanner + sources + docs profiles) and `DPS_TEST_TESSERACT_CMD` set to the docker tesseract wrapper | **2245 passed, 4 skipped**, 638 s (`pytest_full.txt`). The 4 skips are explained below the table. |
| DoD 4 integration: `DPS_TEST_REQUIRE_SERVICES=1 DPS_TEST_REQUIRE_TESSERACT=1 pytest -q -m "browser or db" tests/scanner tests/test_no_values_in_logs_m4.py tests/test_tools_m4.py` | **164 passed, 0 skipped**, 447 s (`integration.txt`). This includes `test_no_submit.py`, `test_crawler.py` and the e2e handler test. |
| `cd frontend && npm ci && npm run typecheck && npm test && npm run build` | ci ok; typecheck ok; **179/179 tests** in 11 files; build ok (`frontend.txt`) |

The 4 skips in the full run:
- 2 × `tests/documents/test_ocr_tesseract.py`, which needs a tesseract binary on the host PATH. No host
  binary is installed; the scanner OCR tests did run, through the wrapper.
- 2 × `[ner]` (presidio and spaCy). That extra is not installed in this lane.

## 2. Stack and smoke

- **Bring-up.** `docker compose --profile scanner --profile sources --profile docs up --build -d` exited 0.
  I then polled the containers (no `--wait`):
  - app, dps-db, worker, browser-worker, demo-site, minio, source-pg and azure-ocr-mock were healthy;
  - migrate, minio-init, seed-sources and demo-secrets exited 0.
- **Readiness.** `/readyz` on 8414 reported `browser: ok (1 online)`, db and jobs ok.
- **Playwright in the browser-worker:** `1.55.0`.
- **Smoke.** `DPS_SMOKE_SCANNER=1 ./lane.sh python tools/smoke_test.py` passed **all 45 checks**: L1, documents,
  M2 and scanner (`smoke.txt`). Scanner results:
  - 21 pages, 63 fields, 51 of them personal, 9 risk flags;
  - site counters `non_get +0, form_get +0, logout_hits +0, admin_hits +0`, `non_get_allowed 0`, blocked `{GET: 1, POST: 68}`;
  - 4/4 parallel screenshots, 3 masked captures, 0 demo values in the persona export, 1 evidence pack.

## 3. Console walk (private Playwright 1.55.0, `pw-m4` venv; `walk.py`, `walk.txt`, `walk_result.json`)

I started two scans from **New scan** (`/scanner/new`, mockup 12) with the defaults (demo onboarding
site, deny `/logout` and `/admin`, demo session): one in synthesize mode and one in obfuscate mode. Both succeeded.

- **Scans:** `s_01M4WCDFY7DM00RFSJT58GFHAC` (synthesize) and `s_01M4WCFYWPR2ZCZJ07V9SHX0KV` (obfuscate).
- **Live crawl.** The key-material card was shown once. The live page showed:
  - crawl map, navigation log and the no-submit banner (72 POST blocked);
  - stepper and request preview.
- **Scan detail** (mockup 04):
  - Parallel view: Synthetic persona and Obfuscated tokens, each with Screens (2 images) and Fields.
  - Inventory: 59 entity chips and a risk-flag list.
  - Captures: masked capture, `obfuscated · reversible map` badge, Download reversible map.
  - Evidence pack: opens `/reports/evidence/…`.
- **Exports downloaded from the console:** inventory CSV and JSON, Export inventory, Export personas, and the reversible map
  (obfuscate scan).
- **Variants covered:** dark and light at 1440; obfuscate dark and synthesize light at 390.
- **Console errors:** 0 across all 8 page sessions.
- **Horizontal scroll:** none on any page at 1440 or 390.
- **No submission.** Demo-site `/__received` delta across the walk:
  `non_get 0, form_get 0, logout_hits 0, admin_hits 0, by_path 0`. The guard counters of both scans show
  `non_get_allowed 0`, blocked `{GET: 1, POST: 72}` and `{GET: 1, POST: 70}`.
- **Mockup comparison.** I compared the app against `mockup-12-…png` and `mockup-04-…png` in the probe directory.
  - Same sections and order: banner, stepper, target/scope/auth/fill/request, crawl live, crawl map, nav log;
    pages-with-forms, rendered-PII, risk flags, parallel view, field inventory, captures.
  - Deviations are the documented ones: the capture lives in the Captures tab rather than under the inventory,
    and the screens are whole-page thumbnails.
  - Cosmetic: the inventory table's last column header is clipped ("FILLED VALU…") inside its card at
    1440. This is not page scroll.
- **Screenshots:** 44 files in `docs/screenshots/m4/` (uncommitted). Mockup renders are in the probe directory.

## 4. Programme plan §6 proofs

- **Masking: OCR of every PNG artefact of both scans** (`ocr_scan.py`, `ocr.txt`, `ocr_result.json`;
  tesseract `--psm 11`). The needles are the 11 demo source values: exact, squashed and digit-only forms, plus name tokens.
  - **Artefacts per scan:** 21 original, 21 annotated, 12 filled_synthesize, 12 filled_obfuscate and 3 masked_capture.
    The obfuscate scan also has 3 capture_json.
  - **Positive control** (`ocr_control.py`): unguarded screenshots read 16 hits on `/profile` (9 entities) and
    9 hits on `/apply/review`.
  - **filled_synthesize / filled_obfuscate:** 0 hits in both scans.
  - **masked_capture of `/profile` and `/profile/contact`:** 0 hits in both scans.
  - **capture_json:** 0 hits.
  - **`/apply/review` masked capture (and its original and annotated):** `application_ref` is readable twice.
    Every PII entity on the page (name, email, mobile, PAN, Aadhaar) is masked or tokenised. This is the known
    open item **N9** (the application reference is not a detected entity). I report it as non-blocking.
- **Exports, source vs target (counts only):** persona export, inventory CSV and JSON, inventory export and
  reversible map, for both scans: **0** demo values.
- **No submission (counting server):** the demo site `/__received` stayed at 0 across the walk (smoke likewise),
  and `test_no_submit.py` passed with services required.
- **Log scan** (`privacy_scan.py`, run in-process from the walk). I scanned every container log of this project
  (12 services, about 800 lines in total) for the demo values and for the 10 key-material and seed strings the
  API returned at scan creation. The strings were held in memory only. **0 value hits, 0 secret hits.**
- **DB tables:**
  - `dps_audit_log` (856 rows), `dps_scan_page`, `dps_scan_field`, `dps_scan_event` and `dps_scan_request`;
  - `dps_job` (42) and `dps_evidence` (31);
  - result: **0** value hits and **0** secret hits. The audit rows are value-free.
- **LLM metric:** `dps_llm_raw_pii_sent_total 0.0`. It is global; the per-tenant metric is M5.

## 5. Plan DoD status

| Item | Status |
|---|---|
| 1. Unit gates | green |
| 2. Console build | green |
| 3. Stack | green |
| 4. Integration | green |
| 5. Smoke | green |
| 6. Browser vs mockups, dark and light, 1440 and 390 | green |
| 7. Demo outcome from the console | green |
| 8. Docs committed | present (accepted in G7 r2) |

The DoD lists pass, but the programme acceptance "masked captures contain no source value" and D4/D-C
("must hold even when a page is hostile") do not hold under findings 1, 3 and 4. Findings 2 and 5 break
the value-free-records and truthful-evidence rules.

## 6. Open items, non-blocking

- N9: `application_ref` is readable in the `/apply/review` captures.
- N12 / N8 from G3 r3: shadow-DOM-off input values, and values set by script after extraction. Not re-probed this round.
- N-r3-1 (slashless hash routes), N-r3-2 (stamp swap), N-r2-1 (HTTPS redirect hops to denied paths are
  counted but sent).
- Inventory header clipped at 1440.

## 7. Cleanup

- `docker compose --profile scanner --profile sources --profile docs down` was run. 0 containers are left,
  and the volumes are kept.
- I touched no other project.
- Nothing was committed. `docs/screenshots/m4/` is untracked, ready for a later "docs: M4 verification
  screenshots" commit once the findings are fixed.
