# M4 lane verification, round 3 (branch `m4`, HEAD under test `9d4bda4`)

- **Verifier:** independent agent, 2026-10-14.
- **Worktree:** `/Users/neelu/document_intelligence/dps-wt-m4`.
- **Method:** I ran everything below myself and trusted no earlier report. I changed no code.
- **Probes and outputs:** `/Users/neelu/.cache/dps-build/reports/probe-m4-verify-r3/`. They are r2's probes, copied and re-run, plus new probes `r/u2_extra.py`, `r/u3_multisep.py`, `r/g5_cross_realm.py`, `b45` `slotted_plain`, `parquet_*.py` and `setup_light.py`. The TLS certificate is a fresh self-signed one for localhost that expires in 3 days.

**Resume check:** no r3 report existed. The tree was clean apart from r2's untracked `docs/screenshots/m4/` (42 PNGs). I moved those to `probe-m4-verify-r3/r2-screens-backup/` and regenerated the screenshots from this round's walk.

## Verdict: PASS

- Every gate, the stack, the smoke, the full and integration suites, the console walk and the privacy proofs are green.
- The 5 carried findings and r2's N1 hold on HEAD.
- I found no new blocking issue. Two new non-blocking observations are listed in section 6.
- **Commit:** `bdbdc0a docs: M4 verification screenshots`. It contains 44 PNGs. The author is NilenduGanguli <nilendu.ganguli@gmail.com>, there is no trailer, and the pathspec was explicit.
- **Cleanup:** I then ran `docker compose ... down -v`.

## 1. Gates

| Gate | Result |
|---|---|
| `./lane.sh uv pip install -e ".[fpe,docs,db,connectors,scanner,uitest,dev]"` | ok |
| `ruff format --check dps tests tools` | 294 files already formatted |
| `ruff check dps tests tools` | All checks passed |
| `mypy dps` | Success: no issues found in 119 source files |
| `pytest -q -rs` (stack up; `DPS_TEST_TESSERACT_CMD` = docker tesseract wrapper `tess.sh`) | **2363 passed, 4 skipped**, 755 s (`pytest_full.txt`). The skips are 2 × host tesseract on PATH (none is installed; the OCR tests ran through the wrapper) and 2 × the `[ner]` extra (presidio and spaCy are not installed in this lane). |
| DoD 4: `DPS_TEST_REQUIRE_SERVICES=1 DPS_TEST_REQUIRE_TESSERACT=1 pytest -m "browser or db" tests/scanner tests/test_no_values_in_logs_m4.py tests/test_tools_m4.py` | **232 passed, 0 skipped**, 545 s (`integration.txt`). This includes `test_no_submit.py`, `test_popup_realms.py` and `test_scope_rail.py`. |
| `cd frontend && npm ci && npm run typecheck && npm test && npm run build` | `ci` rc 0; `tsc` rc 0; **180/180** tests in 11 files; build rc 0; check-bundle ok (`frontend.txt`, `frontend_rc.txt`) |

## 2. Stack and smoke

**Bring-up**
- `docker compose --profile scanner --profile sources --profile docs up --build -d` returned rc 0 (`up.log`).
- Healthy: app, dps-db, worker, browser-worker, demo-site, minio, source-pg and azure-ocr-mock.
- Exited 0: migrate, minio-init, seed-sources and demo-secrets.

**Readiness:** I polled `/readyz` on 8414 without `--wait`. It reported `ready true`, `browser: ok (1 online)`, db `up` with migrations verified, and jobs `ready`.

**Browser-worker**
- `playwright` 1.55.0.
- The running image contains the N1 fix: `__dpsInert` appears 5 times in the image's `guard.py`.

**Smoke:** `DPS_SMOKE_SCANNER=1 ./lane.sh python tools/smoke_test.py` passed **all 45 checks** (`smoke.txt`). Results:
- scan 21 pages, 63 fields, 51 personal, 9 risk flags;
- no submission: `non_get +0, form_get +0, logout +0, admin +0`;
- guard: `non_get_allowed 0`, blocked `{GET: 1, POST: 60}`;
- persona export: 0 demo values;
- evidence: submissions 0;
- M2 checks: all pass.

## 3. Carried findings, re-run on HEAD

| # | Finding | Re-run result |
|---|---|---|
| 1 | D4/D-C race in `ScanGuard.arm` | **Code:** `arm` registers `LOCK_JS` before it calls `_open_pages`, then runs a settle loop with a round trip (`page.evaluate("0")`) until a pass finds no new page. The docstring is updated. **Probe:** `r/g1_blank_popup_worker.py` over HTTPS. 6 guarded runs: each `armed True locks_failed 0`, **0 upgrades**, 0 value in a URL, 0 non-GET. Unguarded control: **58 upgrades** carrying the value (`g1_out.txt`). **Extra:** `g2_post_arm_realms` gave 0 upgrades in 2 of 2 guarded runs, against 2 in the control. |
| 2 | Short dates with `_ , : + ~ \|`, Devanagari digits | `r/u1_urls.py det` output is byte-identical to r2's (`u1_out.txt`). Every listed row gives `/dob/{id}` (or `/dob/{id}/{id}`) in both the redacted URL and the template, with the `None` and the `Detector(en_IN)` classifiers. Rows covered: `1_5_90`, `12_5_90`, `1,5,90`, `1:5:90`, `1+5+90`, `1/5_90`, `12:may:90`, `१_५_९०`. My extra shapes (`u2_out.txt`) are also all `{id}`: ISO order with `_ . : +`, `%20`, `%2F`, `12_may_1990`, `may_12_1990`, `19900512`, `12051990`, and German `12. 5. 1990`. Doubled separators are covered in section 6 (O1). |
| 3 | B4: `display:contents` and slotted text | `b45/probe_r3.py` with docker tesseract (`b45/out.txt`). **contents:** control 4+4; masked 0+0; both obfuscated captures withheld (`leakage`, fail closed). **slotted_email:** control 4+4; masked, synthesize and obfuscate all 0+0. **slotted** (bare unlabelled name): read once in all three captures. My new control `slotted_plain` (the same page with plain `<b>` instead of the web component) gives exactly the same counts. So this is detector recall for an unlabelled name, not a census gap. |
| 4 | B5: `img alt`, optgroup and other attribute strings | **alt:** control 3+3, then 0+0 in masked, synthesize and obfuscate. **optgroup:** control 3+3, then 0+0 in all three. **inputimg** (`input type=image` alt, `option label`, placeholder): control 5+5, then 0+0 in all three. |
| 5 | R3-B1: click handler runs a denied route | `clicks/test_probe_r3_clicks.py`: 6 passed, 4 skipped (`clicks/out.txt`). **onclick_hash:** pages `/` done ×2, no skipped/deny row; 2 `spa/reached/deny` events; `scope_routes_reached` 2. **pushstate:** the same, with target `/logout`. The counter is in the evidence block and HTML (`evidence.py:485`), the console (`ScanEvidence.tsx`), `api-m4.md` and the tests. |
| r2 N1 | POSTs from an about:blank popup realm over HTTPS | **ScanRunner probe** `runner/test_probe_runner_https_popup.py`, 3 runs × 2 tests (`runner/out.txt`). Every run: non-GET received `[]`, guard `non_get_allowed 0`, page `done`. Blocked rows: `POST beacon api_removed`, `POST fetch method`, `GET fetch api_removed`, `POST/PUT xhr api_removed`. **Guard-level probes over HTTPS** (`g34_out.txt`; each with an unguarded control and 2 guarded runs): in `g4` and `g4` crawlerclose, the controls receive 6 non-GET and the guarded runs **0**; in `g3b` and `g3b` crawlerclose, the controls receive 12 leak requests (11 carrying the value) and the guarded runs **0**. |

### New adversarial probe of the N1 fix: `r/g5_cross_realm.py` (HTTPS)

The probe tries to get around the per-realm method rule by mixing realms (`g5_out.txt`). The 13 attempts are:
- a popup XHR opened in the popup realm and sent with the opener's `send`, which holds no state for it;
- a popup XHR opened and sent with the opener's wrappers;
- `fetch.call(popupWindow, …)` and `w.fetch.call(window, …)`;
- a popup `Request` passed to the opener's `fetch`;
- `w.eval` of a `fetch`;
- a `javascript:` popup, a `blob:` HTML popup and a popup rewritten with `document.open`/`write`;
- a form POST from the popup, and an XHR from an iframe born inside the popup;
- a routed same-realm control, and a cross-realm method-override header.

Results:
- **Unguarded:** 11 POSTs, the override GET and the image arrive.
- **Guarded, 2 of 2 runs: 0 of all of them arrive.** The cross-realm XHRs fail through the inert document's CSP (`connect-src 'none'`), and the opener-side tricks fail through the method rule.

## 4. Console walk (private Playwright 1.55.0, `pw-m4`)

The walk scripts are `walk.py` and `setup_light.py`; their output is in `walk.txt`, `walk_result.json` and `setup_light.txt`.

- **Scans started from `/scanner/new`** (mockup 12):
  - synthesize: `s_01M4WQX7JVRVDTGN0E0V0NSWHE`;
  - obfuscate: `s_01M4WQZPEJKP08WRR4TP76DFWC`.

  Both succeeded with 21 pages, 63 fields, 51 personal fields, 9 risk flags and 3 captures. Guard: `non_get_allowed 0`, blocked `{GET: 1, POST: 60}`. The key card was shown once, with the key id and a seed hash prefix only.
- **Live crawl:** the crawl map shows `/admin` and `/logout` as deny list, the off-origin asset host as other origin, and `/help/faq/4` as template limit. The navigation log is shown, and the "60 POST requests blocked in this scan · 0 forms submitted" tiles appear.
- **Scan detail (mockup 04):**
  - Parallel view: Synthetic persona and Obfuscated tokens, each with Screens (2 blob images) and Fields.
  - Inventory: 59 entity chips, the risk flags A1-A9 and the Rendered personal data list.
  - Captures tab: 3 masked captures (`/profile`, `/profile/contact`, `/apply/review`), each with a "Download reversible map" button.
  - Evidence pack opens at `/reports/evidence/…`.
- **Exports downloaded from the console:** inventory CSV and JSON, Export inventory, Export personas, and the reversible map, for both scans (`downloads/`).
- **Variants:** dark and light at 1440 and 390, including the setup page in light at 1440.
  - Console errors: **0** across 10 page sessions.
  - Horizontal scroll: **none** at 1440 or 390.
- **No submission:**
  - Demo-site `/__received` delta across the walk was **non_get 0, form_get 0, logout 0, admin 0, by_path 0**; the smoke delta was also 0.
  - The absolute counters are `non_get 16, form_get 4, logout 2, admin 2`. By path: `POST /api/autosave 2`, `POST /apply/identity/submit 4`, `POST /api/telemetry/form-start 2`, `POST /api/draft 8`. These are exactly 2 × the unguarded positive controls of one pytest run: the direct `httpx` POSTs and GETs in `test_demo_site.py` and `test_no_submit.py`, and the plain-context draft-flush control. I ran pytest twice (full suite, then DoD 4). The demo site started from zero at this round's `up`.
  - Every guarded path asserts its counters are unchanged.
- **Mockups:** I compared against `mockup-12/04-*.png`, rendered into the probe directory. The sections and their order match. The deviations are the documented ones:
  - open contract questions 8 (the capture lives in the Captures tab; whole-page screen thumbnails), 10 (HAR switch disabled), 14 and 15;
  - the inventory's last header is clipped ("FILLED VALU…") inside its card at 1440.

  The full-page screenshots of the live page show the fixed sidebar and top bar mid-page. That is an artefact of full-page capture.
- **Screenshots:** 44 PNGs in `docs/screenshots/m4/`, committed in `bdbdc0a`.

## 5. Programme plan §6 proofs

### OCR of every PNG artefact of both scans

Script `ocr_scan.py`, output `ocr.txt` and `ocr_result.json`. Tesseract runs with `--psm 11`. The needles are the demo values in exact, squashed and digit forms, plus name tokens.

- **Per scan:** 21 original, 21 annotated, 12 filled_synthesize, 12 filled_obfuscate and 3 masked_capture. The obfuscate scan also has 3 capture_json.
- **filled_\* and capture_json:** **0** hits.
- **masked_capture:** **0** on `/profile` and `/profile/contact`. `application_ref` is read 2× on `/apply/review`. This is the known open item N9: it is not a detected entity, and every PII entity on that page is masked (screenshot `scan-detail-captures-obfuscate-dark-1440.png`).
- **Positive control** (`ocr_control.py`, unguarded): `/profile` gives 16 hits across 9 entities, and `/apply/review` gives 9.

### Exports vs source (counts only)

- Inventory CSV, JSON and export, plus the reversible map: **0** demo values in all 7 files.
- Persona exports: 0 full names, 0 first-name tokens. **The 8-letter surname token** appears 21× (obfuscate) and 6× (synthesize), in fields such as `family_name`, `email`, `organization` and `address_line1` of synthetic personas. See O2: this is a dictionary collision, not a leak.

### L2 targets written by the smoke, per column (counts only)

**`bank_uat.customers_smoke` vs `bank.customers`** (`src_vs_tgt.txt`):
- Set survivors are **0** for `full_name`, `national_id`, `tax_id`, `email`, `mobile` and `notes`.
- Non-null counts are preserved: 20000 / 20000 / 12069 / 20000 / 20000 / 5980.
- `customer_id` is transformed: only 4 accidental joins, and on those rows 0 personal columns are equal.

**Parquet exports in MinIO `dps-exports/smoke/*`** (`parquet_vs_source.txt`; 6 objects including this round's `j_01M4WQWR3F0739H09PCYTDDT5Y`, 20 000 rows each):
- **0** survivors for `full_name`, `national_id`, `tax_id`, `email`, `mobile` and `notes`;
- `customer_id` has 2-8 accidental collisions;
- `date_of_birth` cannot be measured by set membership (small domain).

The hashing was done on the host. The MinIO credentials were read from the minio container's environment into env vars and never printed.

**Product verification** on the evidence (`job_evidence.txt`): strict, **0 hits**, 143 455 values checked for the table and parquet runs, and 500 for the dry run.

**Dry run in place** (`dryrun_inplace.txt`): `bank_restore.contacts` equals `bank.contacts` (40000 / 40000 rows, 0 / 0 rows in `EXCEPT`), so nothing was written.

### No submission (counting servers)

- `/__received` delta was 0 across the smoke and the walk.
- `test_no_submit.py` and `test_popup_realms.py` passed with services required.
- The g1, g4, g3b, g5 and runner probes received 0 over HTTPS, against positive controls that receive everything.

### Logs, database and metrics

- **Log scan** of all 12 container logs of this project, run twice:
  - after the walk, with the 10 key-material and seed strings captured at scan creation;
  - after all probes.

  **0 value hits, 0 secret hits** (`walk.txt`, `privacy_final.txt`).
- **DB tables:** **0** value hits and **0** secret hits in all of these, so the audit rows are value-free:

  | Table | Rows |
  |---|---|
  | `dps_audit_log` | 1775 |
  | `dps_scan_page` | 694 |
  | `dps_scan_field` | 1716 |
  | `dps_scan_event` | 3346 |
  | `dps_scan_request` | 2241 |
  | `dps_job` | 91 |
  | `dps_evidence` | 59 |
- **LLM metric:** `dps_llm_raw_pii_sent_total 0.0`. This metric is global; the per-tenant version is M5's.

## 6. Open items (non-blocking)

- **O1 (new): URL dates with doubled separators survive redaction.**
  - Affected: `/dob/12__5__90`, `/dob/12--5--90`, `/dob/12..5..90` and `/dob/12_-_5_-_90`. They stay as they are in the redacted URL and the template, and `template_is_value_free` returns True, under both classifiers (`u3_out.txt`).
  - `_DATE_SHAPE` allows exactly one separator character between parts. That is what the finding specified, and the single-separator class is fully fixed. Spaced forms such as `12. 5. 1990` and `12 - 5 - 90` are caught.
  - I judge it non-blocking because no date formatter emits doubled separators.
  - Suggested hardening: `[\W_]{1,3}` between the parts of `_DATE_SHAPE`.
- **O2 (new): the demo surname appears in persona exports.**
  - The demo customer's surname is in the surrogate name dictionaries (`dps/engine/synth/personas.py`, `surrogates.py`).
  - `persona_values()` depends only on the seed, persona number, locale and policy, never on page input. So persona exports can contain that surname by coincidence: 21 and 6 token hits this round, 0 in r2 with other seeds. Full name and first name: 0.
  - Not a leak. A future verifier needle set should exclude dictionary tokens, or the demo data could use a surname outside the dictionary.
- **O3 (new): CSP-refused requests from inert documents are not counted.** Requests the CSP of an inert popup document refuses (for example the g5 cross-realm XHRs) do not appear in the guard's blocked counts. Nothing leaves the browser; the evidence simply under-counts these blocked attempts.
- **Carried from r2:**
  - N9: `application_ref` readable in the `/apply/review` captures.
  - Bare unlabelled names are not detected (detector recall).
  - N-r3-1: slashless `#logout` runs a click handler with no reached event.
  - N-r3-2: a stamp swap with `harden_js=False` submits a GET form. Hardening is on by default.
  - N-r2-1: HTTPS redirect hops to denied paths (counted).
  - N8/N12.
  - B5 audit residuals: CSS generated content, `li value`, closed-select option label, `<track>`, canvas, video poster.
  - Passive images carrying an encoded fill value from routed filled pages (accepted residual).
  - URL controls `/dob/12/5`, `/dob/1/x/5/90` and `/dob;d=1/5/90`.
  - The inventory header is clipped at 1440.

## 7. DoD status

All 8 items are green:
1. Unit gates.
2. Console build (typecheck, tests, build).
3. Stack: services healthy, migrate 0, `browser: ok (1 online)`, `playwright` 1.55.0.
4. Integration: 232/232, none skipped.
5. Smoke: 45/45.
6. Browser walk against mockups 12 and 04, dark and light, 1440 and 390.
7. Demo outcome entirely from the console: scan, parallel view in both modes, persona and inventory exports, evidence pack.
8. Docs: HANDOFF, README, `api-m4.md` and `M4-DEVIATIONS.md` (r1 and r2 fix sections) are present.

Programme acceptance D4/D-C holds on every probe I ran, hostile HTTPS pages included.

## 8. Cleanup

- `docker compose --profile scanner --profile sources --profile docs down -v` returned rc 0. No `dps-wt-m4` containers or volumes are left.
- No other compose project was touched. The main `data-privacy-services-*` containers are still running.
- I stopped the probe's static server on 8498.
- `git status`: clean after `bdbdc0a`. Nothing was pushed.
