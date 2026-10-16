# S2 — branch `m4` after main was merged into it (CONSOLE lens verifier, round 3)

**Verdict: FAIL, with 1 blocking finding.** The finding is new in this round. It was already on main and is not caused by M4 or by the merge. Round 2's blocking finding (one meaning of a UI sweep) is **fixed and verified**.

| # | Finding | Introduced by |
|---|---|---|
| **B1 (new)** | **The console says a stopped run wrote nothing when it wrote rows.** Smoke run `j_01M4YPD09E5MX92DEKJS9MC8TA` failed closed on `leakage` after writing **9,000 of 20,000 rows** into `bank_uat.customers_smoke`, which was truncated before loading. Three surfaces say otherwise: (1) Reports › Failed sweeps: "The partitions were discarded before writing; nothing reached the target." (2) Reports › Evidence packs row: "SMOKE-0001 · table · **stopped, nothing written**". (3) Evidence pack › Verification › Post-write sample: "not run · **No written rows to sample**", next to "Row counts 9,000 read · 9,000 written". Two pages say it correctly: the job page ("Rows protected 9,000"; "the batch was not written") and the pack's own summary ("wrote 9,000 rows … truncated before loading"). | **main**, not M4 or the merge: M5 Reports `5d984b5` for (1) and (2), the M2 evidence page `132dca9` for (3). It shows only now because gates-r3 smoke 2 left this stack's first leakage-failed run with committed batches. Under the orchestration this is S2's last round, so the merge of `m4` into main must fix it test-first. |

The rest of the lens is green:

- **Errors.** 297 console views and 849 measurements (each a width × theme). There were 0 console errors, 0 console warnings, 0 page errors, 0 HTTP ≥ 400 and 0 failed requests. The same holds for:
  - 84 drawer page×width checks;
  - a 768/1024 px sweep of all 56 routes;
  - 17 mockup renders;
  - 24 downloads: 16 buttons in the downloads probe and 8 inside the flows, each firing its expected file name. The two "Copy as Presidio YAML" buttons copy rather than download, as in round 2;
  - 38 page reads of the B1-fix probe (Reports and Overview).
  
  Deliberate refusals are counted separately (§5 N7).
- **Horizontal scroll.** None at 390 px. None at 768, 1024 or 1440 px either. The same holds in every dialog, drawer, reduced-scope, no-key and injected-failure state.
- **Flows.** Every interactive flow of M1–M5 completed end to end. Three states round 2 never opened were added: the wizard's **Export files**, **In place** and **Append** panels, and the typed-confirmation dialogs (mockup 06).
- **Round-2 B1 (UI sweeps).** Fixed. The tile, the chart, the Failed sweeps card and the Overview posture now count the same sweeps (§3).

| | |
|---|---|
| Date | 2026-10-15, 08:25–08:50 IST |
| Tree verified | `dps-wt-m4`, branch `m4` @ **`cbbac38`** (S2 fix round 2). Clean at start and end (`git status` empty); no repository file modified. Main's `5c11e49`/`d2fdc3f` are still not in this tree (N5). |
| Stack | m4 compose project as the gates verifier left it (app 8414, demo site 8420). 11 services healthy, `/readyz` ready, browser 1 online, at start and at end. Not restarted, rebuilt or brought down. The code in the containers is the tree's: sha256 of `dps/reports.py`, `scanner/capture.py` and `scanner/dom.py` match in app and browser-worker, and the served bundle `index-BpQsOVd-.js` contains fix round 2's new Reports text. |
| Browser | Private Playwright `playwright==1.55.0`, headless Chromium 1187, venv `~/.cache/dps-build/pw-s2-console`. Never the shared MCP browser. |
| Keys | Each parallel process used its own all-scope database key (`s2con-r3-walk`, `-walk-light`, `-flows-a`…`-i`, `-probe`), minted through the Admin API with the static key. That keeps the per-key rate-limit buckets apart. Two reduced-scope keys were minted too (`-noscan`, `-detectonly`). Raw keys lived only in 0600 files in the session scratchpad, which are now **deleted**. A full static pass with the **static key** covered the environment-key path. **All 16 `s2con-r3-*` keys are disabled** (`logs/cleanup-keys.json`). A grep of `logs/` finds no key string. |
| Probes, logs, shots | `~/.cache/dps-build/reports/S2-console-r3/`. Scripts are at the top level and run through `run.sh`/`bg.sh` → `./lane.sh`. JSON is in `logs/`, 1,041 screenshots in `shots/`, and pairs, montages and the B1 composite in `mock/`. |
| Privacy of the probes | Key material was blurred before any screenshot taken while it was on screen. Downloads ran with `accept_downloads=False`, so only the event and the file name were seen. Logs hold counts, ids, routes, codes and scrubbed fixed texts. The only values in shots are the demo site's synthetic applicant and seeded demo rows. |

## 1. Method

Round 2's harness was reused unchanged except for the `r3` names, and run in parallel: 11 processes at a time, each with its own key.

1. **Static walk** (`walk_static.py`, three passes):
   - **Passes:** dark and light with the walk keys, and dark with the static key.
   - **Routes:** 56. Round 2's 52, plus:
     - the leakage-failed job;
     - its evidence pack;
     - `/reports?layer=conn&outcome=failed`;
     - `/reports?layer=ui&outcome=failed`.
   - **Measurement:** each route at 1440 and 390 px, with a full-page shot.
   - **Mockups:** the 12 mockups at 1440 and 390 px in both themes, plus wizard steps 1–5.
   - **Wider widths:** `extra.py` swept every route at 768 and 1024 px.
2. **Interactive flows** (`walk_flows.py`). Each state was measured at 1440 and 390 px in dark and light.

| Flow | Walked | Result |
|---|---|---|
| Playground (02) | Detect (12 findings), Obfuscate JSON (key card), Reveal, Synthesize (no key card, no Reveal), Text, `analytics-safe`, hand-over to Documents | 8 views, 0 events |
| Documents (08) | Demo KYC scan (2 pages): Redact (14 findings, page 2), Obfuscate + reveal text, Synthesize text, OCR `always`; Download redacted PDF fires `kyc-scanned.redacted.pdf` | 8 views, 0 events |
| Policies (05) | New policy `s2con-r3-087c03` from `gdpr-pseudonymise`; AADHAAR → `redact`; declared field; recognizer live test "1 match · 1 on the allow list" and add; plan + FF1; draft diff; publish v2 (`CHG-S2CON-R3`); v1/v2; matrix filters/search; `pci-dss` | 14 views, 0 events |
| Wizard PostgreSQL (06 → 03 → 10) | `bank-pg-ro` 3/3; discovery (16 cells); **synthesize** to `bank_uat.s2con3_syn_d8f1bf` (6 s); the seed card is shown once, then no JSON after "stored"; evidence sections 1–8 incl. 6; A4 794/794 | 11 views, 0 events |
| Wizard Oracle | `smoke-ora-src` 2/2; 16 cells; `CRM_MASKED.S2CON3_69E1A0` (22 s); evidence 1–5, 7, 8; A4 OK | 11 views, 0 events |
| Wizard MongoDB | `smoke-mdb-src` 2/2; 17 cells; `crm_uat.s2con3_09a007` (12 s); evidence; A4 OK | 11 views, 0 events |
| **Wizard step 4, new in r3** (`wizard_modes.py`) | Export files, In place, Append panels. **Typed-confirmation dialogs** "Confirm: overwrite 11,989 rows in bank.customers" (blocked: `bank-pg-ro` has no target role) and "Confirm: append 11,989 rows to bank.customers". Primary disabled; Escape closes them; nothing confirmed or written. | 5 views (20 measurements), 0 events |
| Job groups (07) | Smoke group: 3 systems, wires, consistency **passed 3 / 3**, 3 coverage rows. Search and the 5 filters; Export list (CSV) fires. New group `s2con-r3-09a007`: Done is disabled until "stored"; `.dpskey`/`.dpsseed` fire; no key material in storage; "Add a job to this group" opens the wizard preset. | 8 views, 0 events |
| UI scanner (12 → 04 → 10) | Check session ("✓ Session valid · /apply returned 200 without a redirect"). Crawl `s_01M4YQZ5Q7NXBDH8M6CDKW0ARF`: live map until "finished · 1:17", 21 pages. JSONL fires. Parallel view on both sides; Screens and Fields; page 3. Inventory 63 rows (risk-only 8). Captures (3 masked, rendered). Inventory and persona exports fire. Evidence "UI scan demo-site:8410 → captures and exports", A4 OK. Saved-scan setup. Same counts as rounds 1 and 2. | 15 views, 0 events |
| Reports (09) | 7 days; Connectors/UI/API; OK/Failed (URL keeps the filters); search `CHG-S2CON3-ORA` → 1 pack; chart as table; older rows/packs; audit CSV fires; open a pack and back | 9 views, 0 events |
| Admin (11) | Create key (detect): shown once, then hidden; storage holds only `dps.apiKey`/`dps.tenant`/`dps.theme`. Disable. Wildcard host refused. Tenant dialogs. Hosts card (§3). | 8 views, 0 events |
| Admin states (`admin2_flow.py`) | Add tenant `s2con3-4dd639`. Register host `uat-4dd639.bank.internal` ("1 registered", footer notes the fallback), then remove it. Rotate a key (now / 24 h / 7 d; shown once, then hidden). Re-enable, then disable. | 10 views, 0 events |
| Job controls (`extra2.py`, `extra3.py`) | PG run `bank_uat.s2con3_ctl_f30c04` driven from its page: **Pause → paused, Resume → waiting, Cancel → canceled** (14,000 rows). Evidence "not verified · job canceled"; Reports row "14,000 rows · canceled". Failed-scan tabs read "Crawl failed …". | 10 views, 0 events |
| Copula pack, no key, theme (`extra.py`) | Section 6; A4 794. Without a key every page names the missing key (401s, 0 page errors). The theme survives a reload. Stop crawl is disabled on a finished scan. 768/1024 sweep: no h-scroll, 0 events. | 0 events |

3. **Lens probes.**
   - **Round-2 B1 fix:** `b1r3_probe.py` (API, live console and injected UI failures), `sweeps_probe.py` (before and after).
   - **The new finding:** `truth_probe.py`, `leak_job.py`, `evsum.py` and `leak_row.py`, plus the composite `mock/B1-r3-stopped-run-wording.png`.
   - **Round-1 regressions:**
     - `drawer.py`: 14 pages × 6 widths, against `vault.js`;
     - `b2_rows.py` and `scan_rows.py`;
     - `phone_shots.py`.
   - **Downloads:** `downloads_probe.py`.
   - **Inner overflow:** `overflow_probe.py` and `wizard_overflow.py`.
   - **Reduced scope:** `scope_probe.py` with a no-`scan` key and a detect-only key.
   - **Final checks:** `final_checks.py`.
   - **Mockup structure:** `compare.py`, `montage.py`.
   - **Totals:** `aggregate.py`.

## 2. Blocking finding

### B1 (new, already on main): a stopped run "wrote nothing" on Reports and in its evidence pack, but wrote 9,000 rows

**The run.** `j_01M4YPD09E5MX92DEKJS9MC8TA` is gates-r3's smoke 2: PostgreSQL `bank.customers` → `bank_uat.customers_smoke`, obfuscate, copy to a table · truncate + insert.

**What happened, from the records** (counts only; `logs/leak-job.json`, `logs/evidence-summaries.json`, `logs/truth-probe.json`):

- **Job API:**
  - `progress.rows_written` 9,000 of `rows_estimated` 20,000;
  - partition `p0` `rows_written` 9,000, `error_code` `leakage`, state `failed`.
- **Evidence list API** (`/reports/evidence`): `rows_written: 9000`, `verification_status: failed`.
- **The evidence pack itself** (`ev_01M4YPDBREXBJNANPE92V6DKGM`):
  - Summary: "… and wrote 9,000 rows to bank_uat.customers_smoke … nothing of the failed unit was written";
  - Target: "truncate_insert · truncated before loading";
  - Execution: "Last batch committed … 9 batches";
  - Row counts: "9,000 read · 9,000 written · pass".
- **Job page:** "The leakage sweep found a source value in the output, so the batch was not written (fail closed)". Evidence so far: "Rows protected 9,000".
- **The wizard's own In-place dialog:** "If a partition fails it resumes from its last committed batch."
- **Contract:** §0 rule 2 says strict verification "writes nothing for that unit (request, batch, page)". §6.4 `BatchTransformer` says "the **batch** is not written (the unit that fails closed stays the batch)".
- **Gates-r3:** "the 9,000 rows that were written have 0 same-row survivors".

**What the console says instead** (the composite `mock/B1-r3-stopped-run-wording.png` puts all of them on one image):

1. **Reports › Failed sweeps** (`frontend/src/lib/reports.ts:89-92`, `failedSweepText`):
   - It says: "The leakage sweep found source values in partition 0. **The partitions were discarded before writing; nothing reached the target.**"
   - The fix line says "**Review the columns named above**", but no column is named. `paths` holds the pack error's `where` ("partition 0"), and the sentence uses it as a column list.
   - Shots: `b1r3-live-all-dark-1440.png`, `b1r3-live-conn-*`, `el-reports-failed-card-*-390.png`.
2. **Reports › Evidence packs row** (`frontend/src/components/EvidenceTable.tsx:27`, `packLine`):
   - It says "SMOKE-0001 · table · **stopped, nothing written**" for every failed pack. It never looks at `summary.rows_written`, which the API sends as 9000.
   - The same row's chip says "not verified · job failed". The pack's seal says "verification failed closed", and the job says "leakage caught · failed closed". This comes from `verificationChip`: a leakage hit discards its batch's counters, so `hits` is 0.
   - Shots: `leakrow-dark-1440.png`, `leakrow-light-390.png`.
3. **Evidence pack › 5 Verification** (`frontend/src/pages/EvidencePack.tsx:223`):
   - "Post-write sample · not run · **No written rows to sample** · skipped" appears whenever the sample did not run.
   - Here it sits next to "Row counts 9,000 read · 9,000 written".
   - The same sentence is on both canceled runs: 8,000 rows (`j_01M4XZNN…`) and 14,000 rows (`j_01M4YR4R…`, this walk).
   - Shot: `10-evidence-failed-leakage-dark-1440.png`.

**Why it matters.** Reports is where an operator goes after a failure. It tells them nothing reached the target. In fact the target table was truncated and now holds 9,000 pseudonymised rows of a 20,000-row selection. The regulatory evidence pack contradicts itself in its own Verification table ("No written rows" vs "9,000 written").

There is **no privacy loss.** Every committed batch passed its sweep, and the failing batch was not written.

**Origin.**
- Mockup 09's sentences belong to a **files** job: "Both partitions were discarded before upload; nothing reached the bucket" and "stopped after p3 and p4, nothing uploaded".
- M5's Reports (`5d984b5`) applied them to every write mode. Table, in-place and append jobs commit per batch.
- The post-write row dates from the M2 evidence page (`132dca9`).
- Main has the same code. Main's newer `5c11e49` does not touch these lines.

**Fix** (frontend only, small; do it on main while merging `m4`, test-first):

- **`failedSweepText`** (job, leakage):
  - Say what the contract guarantees, e.g. "The leakage sweep found a source value in a batch of partition 0. That batch was not written (fail closed); rows committed before it stay in the target. Open the job to see what was written."
  - Say "Review the columns named above" only when `paths` names columns, not `partition N`.
- **`packLine`:**
  - For a failed (or canceled) pack with `rows_written > 0`: "stopped · 9,000 rows written before it".
  - Keep "stopped, nothing written" only for 0/null.
  - *Optional:* in `verificationChip`, map `summary.verification_status === 'failed'` on a failed pack to "failed closed" (the pack's own seal).
- **`EvidencePack` post-write row:**
  - When rows were written but the job did not succeed: "not run · The job ended failed/canceled before the post-write sample; the 9,000 written rows were not re-read".
  - "No written rows to sample" only when 0 rows were written.
- **Tests (Vitest):**
  - `failedSweepText` with `paths: ['partition 0']`;
  - `packLine`/`EvidenceTable` for a failed pack with `rows_written: 9000` and for a canceled one;
  - `EvidencePack` for a failed and a canceled pack with rows written.

  Each must fail on the current text.

**Re-check:**
- `run.sh truth_probe.py` and `run.sh leak_row.py`: no surface says nothing was written while `rows_written > 0`.
- `walk_static.py routes-dark`: `03-job-failed-leakage` and `10-evidence-failed-leakage`.

## 3. Round-2 findings re-checked

**B1 (r2): one meaning of a UI sweep. FIXED.** Evidence: `logs/b1r3.json`, `logs/sweeps-probe*.json`.

- **API, for 7, 14 and 30 days:**
  - UI sweeps passed 33, which equals the 33 succeeded scans finished in the window. UI failed 0, which equals the 0 `where: "scan"` entries.
  - The 3 `failed` scans count nowhere. All three are `secret_unavailable`. One is fresh: `s_01M4YQZ77NH3H7MN252MV72BAQ`, created by this walk's deliberate foreign-ref probe exactly to test the rule.
  - Connectors failed 1, which equals the 1 listed partition, the leakage job above.
  - The posture's `sweeps_passed_pct` is **99.7**, which equals 360 / 361 from the jobs report.
  - After the walk: UI 38/0, connectors 206/1, API 121/0, with the failed scans still not counted.
- **Live console, 1440 dark and 390 light:**
  - All: "361 · 360 passed · 1 failed, all failed closed". The card reads "1 in 14 days" and lists `j_01M4YP…TA · p0` with "failed closed" and a link to the job.
  - API: 121/0. Connectors: 207 · 206/1, with the same row. UI scanner: "33 · 33 passed · 0 failed", card "0 in 14 days".
  - The chart, read as a table, sums to the tile under every layer. No horizontal scroll.
  - Overview: "Leakage sweeps passed 100%" (99.7 rounded; see N1).
- **Injected UI failure.** `/reports/jobs` was answered with the real response plus one `where: "scan"` failure. The live stack cannot produce one without breaking the guard, and nothing was written. Variants: `submission_detected`, `verification_failed`, and UI-only.
  - Tile with both kinds: "362 · 360 passed · 2 failed, 1 failed closed, 1 scan to review". UI-only: "1 failed, 1 scan to review", with no "failed closed".
  - Card: "2 in 14 days". It lists the job (amber "failed closed") and "UI scan s_01M4YQ…RF" (rose "review", text for `submission_detected` or the generic code).
  - Layers: under UI scanner only the scan, under Connectors only the job, under API nothing.
  - The link lands on `/scanner/scans/s_01M4YQZ5…` ("Scan s_01M4YQ…RF · demo-site:8410").
  - Every layer at 1440 dark, 390 light and 390 dark: no horizontal scroll, 0 events.
  - Shots: `b1r3-inj-*`.

**Round-1 B1–B3 (regression), all still FIXED:**
- **Drawer:** 84 page×width checks, 0 problems, 0 events. Aria, focus, Escape, scrim and navigation all pass, as on mockup 01.
- **Scan rows:** 33/33 UI-scanner pack rows match the API (host, pages, forms, personas, submissions). There are 0 "UI scan of —", and the Overview and Reports name the host.
- **Hosts card:** states the local fallback. With a registered host, its row and footer note appear; after removal, the service-default rows return. Clean at 390 px in both themes.

## 4. Mockup comparison

- **Structural labels:** **210 of 233 found**, the same as round 2 (`logs/mockup-compare.json`). The missing labels are still the mockups' sample content and the deviations documented in `M4/M5-DEVIATIONS-console.md`:
  - 04 and 12: sample scan heading;
  - 05: closed confirm dialogs;
  - 06: "Export files"/"Append new rows" are choice titles, not headings, and the in-place dialog;
  - 07: three-way sample (counts-only deviation);
  - 08: bank-statement sample columns;
  - 10: CSS upper-case, sample sentences, CHECKPOINTS.
- **Wizard dialogs:** both now walked. The in-place dialog renders "Confirm: overwrite 11,989 rows in bank.customers", the mockup's "Confirm: overwrite 642,318 rows in LOANS.APPLICANTS" with live counts.
- **Images:**
  - Side by side: `mock/pair-<mockup>.png`.
  - Every 390 px route in both themes: `mock/montage-390-{dark,light}-1…7.png`.
  - Visual review found no broken layout. A full-page shot paints the sticky sidebar at the scroll offset, which is a shot artefact.

## 5. Non-blocking notes

- **N1. The Overview rounds a failed sweep away.** With 1 failed sweep in 361, the API says 99.7 %. The Overview shows "Leakage sweeps passed **100%**" and "Posture score **100** of 100" (`postureBars` uses `Math.round`).
  - The mockups do the same: 01 shows 100 % while 09 shows 3 failed of 1,457. So this follows the approved design.
  - Suggestion: floor, or one decimal below 100, so that 100 % means nothing failed.
- **N2. Append dialog with an empty key offset.**
  - The step-4 field shows the placeholder `900000000`, and the spec preview has `"offset": null`. The dialog reads "New rows get customer_id = original + —."
  - The server refuses a same-table append on an integer key without an offset, so nothing is written.
  - The console should require the offset before Confirm, or state it. This is M2 code, already on main.
- **N3. Round-2 notes, unchanged** (the code behind each was not touched by fix round 2; `git diff a3cd667 cbbac38 -- frontend` changes only the Reports files):
  - **429 handling** (r2 N2): not re-run, to avoid log noise. `lib/api.ts` and the hooks are unchanged.
  - **No-`scan` key:** the Overview still offers 3 scan links. Each opens "The scan could not be loaded: 403: scope 'scan' required. **Retrying.**", which polls forever (r2 N3).
  - **Reduced-scope empty states** (r2 N4).
  - **Overview Layer 3** for a failed newest scan: "Last scan: 0 pages, 20 personas, 0 submitted (s_01M4YQ…AQ)" without saying it failed (r2 N5).
  - **Wizard step-3 review table:** 949/710; all 16 Method selects start outside the visible box (r2 N6.N2).
  - **Inner scroll boxes:** identical to round 2, except that the new leakage job page has the usual classification-table scroll.
  - **Parallel-view thumbnails** about 390 px.
  - **Overview** lists all 31 entity types.
  - **Documents** preset selector 456/356 at 390 px.
  - **Finished-scan stepper:** step 4 stays numbered.
  - **404 page:** still has the M0 placeholder copy (r2 N7).
  - **Phone topbar:** about 100 px (r2 N8).
- **N4. ReDoS.** On this branch `(a+)+$` in the recognizer builder still gets no `unsafe_pattern` message. F1 (`fsec`) fixes this server-side, and the console already maps the code (r2 N9).
- **N5. Main has moved.** `5c11e49`/`d2fdc3f` are not in `m4`.
  - They change `Admin.tsx`, `EvidencePack.tsx` (column labels) and `wizard/StepConnect.tsx`.
  - The S1-M4 console lens must re-check, after the merge: Admin, a MongoDB evidence pack, wizard step 1, and the B1 fix above.
- **N6. D4 during this walk.** The demo site's `/__received` read 88 / 20 / 11 / 10 (`non_get`, `form_get`, `logout_hits`, `admin_hits`) before and after the walk. The walk included a 21-page crawl, a session check and the deliberate failed scan.
- **N7. Deliberate refusals and log footprint.**
  - **Negatives flow:** 9 Chromium "Failed to load resource" lines plus 9 HTTP events, each shown as words on the page:
    - 422 `host_not_registered` and `start_url_denied`, cleared on edit;
    - 404 `evidence_not_found`, `job_not_found` and `scan_not_found`.
  - **No-key visit:** 401s on 5 pages, each naming the missing key.
  - **Reduced-scope walks:** 64 page loads (16 routes × 2 widths × 2 keys). 32 of them got 403s where the scope is missing, each worded on the page. There were 0 page errors and 0 horizontal scroll.
  - **App logs since 08:25 IST:**
    - 0 tracebacks and 0 error lines in app, worker and browser-worker;
    - 2 `rate_limited` lines for `s2con-r3-probe` at 08:27, from the paced id picker, not the console;
    - 1 static-key line at 08:39 that is not this walk's: its static-key pass had 0 HTTP ≥ 400.

## 6. Objects this walk left on the m4 stack

Names are prefixed `s2con-r3`/`s2con3`. Nothing was deleted.

- **Tenant:** `s2con3-4dd639`, with no host (its host was added and removed).
- **Policy:** `s2con-r3-087c03` (v2 published, `CHG-S2CON-R3`).
- **Job group:** `s2con-r3-09a007`.
- **Targets:**
  - `bank_uat.s2con3_syn_d8f1bf` (synthesize);
  - `CRM_MASKED.S2CON3_69E1A0`;
  - `crm_uat.s2con3_09a007`;
  - `bank_uat.s2con3_ctl_f30c04` (canceled run, 14,000 rows).
- **Scans:**
  - the console crawl `s_01M4YQZ5Q7NXBDH8M6CDKW0ARF`;
  - the session check `s_01M4YQZ0TZD0F8QDDBX2SCCFJ9`;
  - the deliberate failed scan `s_01M4YQZ77NH3H7MN252MV72BAQ` (`secret_unavailable`, counted as no sweep).
- **Discoveries and accepted snapshots,** including the step-4 walk's discovery, which was never run.
- **Keys:** 16 `s2con-r3-*` keys, all **disabled**.

## 7. Re-check after the fix (on main, after the merge)

- **B1:** `run.sh truth_probe.py` and `run.sh leak_row.py`. They need a failed or canceled run with `rows_written > 0`; cancelling a run from its page, as `extra2.py` does, creates one. Also `walk_static.py routes-dark`.
- **Regressions:**
  - `b1r3_probe.py` (UI-sweep rule);
  - `drawer.py` and `b2_rows.py`;
  - `walk_flows.py` for every flow, and `wizard_modes.py`;
  - `aggregate.py`.
- **Keys:** mint fresh keys with `S2_KEY=static run.sh mkkey.py <name> <scratch-file> '*' '*'`, run probes with `bg.sh <label> <keyname> <script>`, and finish with `cleanup_keys.py`. The scripts point at the m4 lane (8414); on main, set `DPS_TEST_APP_URL`.
