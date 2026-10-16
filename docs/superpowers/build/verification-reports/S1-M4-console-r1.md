# S1 — main after the M4 merge — CONSOLE lens verifier, round 1

**Verdict: FAIL — 3 blocking findings** (B1, B2 from the first pass at 21:07–21:45, both re-confirmed at 22:06–22:08; **B3 found by the resume re-check**, §0). None is caused by the M4 merge, and every fix is small:

| # | Finding | Introduced by |
|---|---|---|
| **B1** | **A finished job whose connection was deleted logs browser console errors on its page, and its head loses engine and name.** `DELETE /connections/{id}` is a documented jobs-scope operation. Afterwards, every visit to a job that used the connection logs `Failed to load resource: 404` (`GET /api/v1/connections/{id}`, `connection_not_found`), and the head reads `— · bank.customers`. Its **Review and accept** link opens the wizard on an error, which logs one more console error. 35 default-tenant jobs on main's stack were in this state before the walk. The walk's failed-run route logged 2 errors at every width in every pass. I reproduced it end to end through the API. | M2 `da9be83` (JobDetail), `JobWizard.tsx:109` |
| **B2** | **The Jobs page shows the newest 200 jobs as if they were all the jobs.** It reads "Showing 178 of 178 jobs" and Status "Failed (0)". The API holds 993 jobs (27 failed), and the Overview on the same console says "960 completed · 28 failed". No failed run can be found from the Jobs list, by filter or by search. That includes both leakage-stopped runs that wrote 9,000 rows, which Reports › Failed sweeps links to. | M2 `c2aa77f` (Jobs; M2 plan Task 30 "client-side over `GET /jobs?limit=200`", not stated anywhere a user can see) |
| **B3** | **The evidence pack's Summary sentence does not wrap a long object, policy or target name, so the page scrolls sideways at 390 px.** `EvidencePack.tsx:155` prints the source object, policy and target in `<b>` inside `p.lead`; `evidence.css:21` gives `.lead` no wrapping rule (unlike `.meta .v` and `.doc-head h1`, which have `overflow-wrap: anywhere`). A name without a break opportunity (`_` and `.` are not one) runs past the sheet's edge and is clipped at the viewport. On main's stack, pack `ev_01M4XXJ38001JCX9B250VRQVDF` (Oracle target, 38 characters) scrolls **2 px at 390**, 17 px at 375 and 32 px at 360. Its siblings scroll 1/16/31 px. The overflow grows about 9.4 px per extra character, so a routine 40+ character Oracle name scrolls at 390 by 20 px or more. The job page and Reports wrap the same name (0 px). | M2 `b69cce6` (`.lead` rule, `v0.3.0-m2`); only the copula/synthesize packs on this stack have a long enough name |

**Both S2 r3 open findings are fixed:**
- **Console B1 (`932075a`), live on main.** It now covers a *canceled* run that wrote rows too, which the gates verifier had no instance of. At 1440 dark and 390 light, with 0 console events:
  - Reports reads correctly for 23 of 23 stopped runs;
  - the evidence packs and job pages read correctly for the 3 runs that wrote rows (2 leakage-stopped, 9,000 rows each; 1 canceled, 2,000 rows).
- **Captures-privacy B1a–c (`40d8b34`).** Verified by the gates verifier (S1-M4 gates r1 §4.2: OCR 0 in every masked or obfuscated screen, controls readable). It is outside this lens's method. I did not OCR it again; the console renders the stored masked captures of both fill modes with 0 events.

The rest of the lens is green:
- **Horizontal scroll:** none at 390, 768, 1024 or 1440 px, on every route, flow state, dialog and drawer, **except B3**. The first pass's harness allows 1 px for sub-pixel rounding, so it read the walked copula pack (391 at 390, a real 1-px `scrollX`) as clean. The resume re-check measured the real `scrollX` and found B3 on a sibling pack.
- **Errors:** 0 page errors and 0 console warnings. The only console errors that were not deliberate are B1's.
- **Flows:** every M1–M5 flow completed with the same outcomes as S2 r3.
- **Mockups:** structural labels 210 of 233, identical to S2 r3.
- **D4:** counters did not move.
- **Logs:** 0 tracebacks, 0 error lines, 0 key hits.

| | |
|---|---|
| Date | 2026-10-15, 21:07–21:45 IST (first pass); resume re-check 22:03–22:14 IST (§0) |
| Tree verified | `/Users/neelu/document_intelligence/data-privacy-services`, `main` @ **`8ea220a`**. Clean at the start (21:09); HEAD unchanged at the end. **This lens modified no repository file:** it ran no pytest, and its probes write only under `~/.cache/dps-build/reports/S1-M4-console-r1/`. At 21:31:44–21:32:18 a concurrent process rewrote the 54 tracked PNGs in `docs/superpowers/build/m3-shots/` (the known A-N2 side effect of the console tests; the regression and captures-privacy lenses saw the same). I left them as found (N9). |
| Code identity | The served console bundle `index-_9Of_JHk.js` is byte-identical to `frontend/dist` built from this HEAD (sha256 `76aed5af…`). It contains `932075a`'s new wording, and none of the old wording ("nothing reached the target", "The partitions were discarded before writing"). The gates verifier checked that the containers run the tree's `dps/`. |
| Stack | Main's compose project, as the gates verifier left it: 5 profiles, tokenizer-mock on 8430, 11 services healthy. Not restarted, rebuilt or brought down. `/readyz` reported ready with browser 1 online at the start and at the end. Other verifiers used the same stack at the same time (I saw their jobs `*_s1m4reg1b` and their scans of `demo-site:18441`). |
| Browser | Private Playwright `playwright==1.55.0`, headless Chromium 1187, venv `~/.cache/dps-build/pw-s1-m4-console`. Never the shared MCP browser. |
| Keys | I minted 18 database keys through the Admin API with the static key: walk keys, nine flow keys, probe keys, a no-`scan` key, a detect-only key and a rotation victim. Each parallel process had its own rate-limit bucket. Raw keys lived only in 0600 scratchpad files; those files are now **deleted**. **All 20 `s1m4c1-*` keys are disabled** (the 18, the rotated replacement and the admin flow's key; `logs/cleanup-keys.json`). One static-key pass covered the environment-key path. |
| Probes | `~/.cache/dps-build/reports/S1-M4-console-r1/`. The harness is S2 console r3's, copied and pointed at main (8400/8410), plus new probes: `b1_main.py`, `repro_deleted_conn.py`, `repro_wizard_link.py`, `jobs_rows.py`, `jobs_counts_probe.py`, `b2_jobs_evidence.py`, `n5_probe.py`, `sweep_attr.py`, `log_scan.py` and `composite.py`. Also `logs/` (JSON), `shots/` (1,124 PNGs), `mock/` (montages and composites) and `crops/`. Run with `run.sh <script>` or `bg.sh <label> <key> <script>`. |
| Privacy of the probes | Logs hold counts, ids, routes, codes and scrubbed fixed sentences. Key cards were blurred before every shot (checked on the Admin one-time key). Downloads used `accept_downloads=False`, so only the event and the file name were seen. No customer value was printed. The only values in the shots are seeded demo rows, synthetic personas and tokens. A grep of `logs/` finds no key-shaped string. |

## 0. Resume re-check (22:03–22:14 IST)

The orchestration dispatched this job again after the first pass had written this report (21:44). I continued from that state. I did not redo the walk; I re-confirmed it on the same tree and stack.

- **State.**
  - `main` @ `8ea220a`, clean (`git status` empty: the 54 `m3-shots` PNGs of N9 are no longer modified).
  - The served bundle is still `index-_9Of_JHk.js`.
  - `/readyz` reported ready with browser 1 online.
  - The stack was not restarted, rebuilt or brought down.
  - No other pytest or Playwright process was running.
- **Keys.** I minted three keys `s1m4c1-rs-{dark,light,b2}` with the static key, each in a 0600 scratchpad file. After the re-check, **all 23 `s1m4c1-*` keys are disabled** (`logs/cleanup-keys-resume.json`) and the key files are deleted.
- **Route re-walk** (`resume_recheck.py routes-dark-1440` and `routes-light-390`, run in parallel; `logs/resume-routes-*.json`). It covered all 56 `walk_static.py` routes, plus the B1 reproduction job page and its **Review and accept** deep link: 57 pages per pass.
  - Every page rendered its heading, except the 404 page, which has none by design. There were 0 skeletons, 0 alerts and 0 navigation errors.
  - No page h-scroll by the harness rule.
  - 0 page errors, 0 warnings and 0 failed requests.
  - **Console errors: 4 per pass, all B1's.** The failed-leakage run had 2 × 404 `connection_not_found`. The repro job page had 1, and its wizard deep link had 1 ("Could not open the snapshot: connection not found (connection_not_found)").
  - Both heads still read `— · …`.
- **B2 re-confirmed** (`resume_recheck.py b2`, `logs/resume-b2.json`, `shots/resume-b2-search-light-390.png`), at 1440 dark and 390 light with 0 events:
  - The API holds 993 jobs, 970 of them table jobs, 27 failed; `/jobs/stats` gives failed 28.
  - The first page holds 200 jobs (178 table jobs, 0 failed) back to 05:03 UTC.
  - The Jobs page says "Showing 178 of 178 jobs" and "Failed (0)", and the Failed filter is empty.
  - A search for the leakage-stopped run `j_01M4XD46RY4DE9TGKM2RE97D52` reads "Showing 0 of 178 jobs".
  - The Overview tile says "960 completed · 28 failed".
- **B3 found.**
  1. The re-walk showed `10-evidence-copula` at scrollWidth 391 for a 390 viewport. The first pass had the same reading, under the harness's 1-px tolerance.
  2. `resume_copula_1px.py` and `resume_copula_1px_b.py` show the 1 px is real, not rounding. `window.scrollX` reaches 1. The overflowing element is the target `<b>` of `p.lead` (38 characters, no break opportunity), and it overflows at every narrower width.
  3. `resume_lead_overflow_u.py` ranked all 537 packs by their longest unbreakable name and measured the top three at 390/375/360 (`logs/resume-lead-overflow-unbreakable.json`):
     - `ev_01M4XXJ38001JCX9B250VRQVDF`: **scrollWidth 392 at 390, `scrollX` 2**; 17 at 375 and 32 at 360 (`shots/resume-lead-overflow-u-VRQVDF-light-390.png` shows the name cut at the viewport edge);
     - `ev_01M4XXJGDRDJC47GC0ZNT5E4ZF`: 1 / 16 / 31;
     - `ev_01M4XRXRFCKKWYG1KVFAV538T1`: 0 / 9 / 24.
  4. Names with `-` or `/` (file targets of 40–44 characters) wrap and do not overflow (`logs/resume-lead-overflow.json`).
  5. `resume_long_name_pages.py`: the same job's page `j_01M4XX6N3YZWW0X5ZSFHYMZ4Q3` and its Reports search do not scroll at 390, 375 or 360.
- **D4.** `/__received` still reads 16/4/2/2.
- **Logs.** `logs/log-scan-resume.json`, since 16:33Z: app 42 lines, 0 tracebacks, 0 warning or error events, 0 lines holding a resume key.
  - **Evidence repair:** my first resume scan wrote over `logs/log-scan.json`. I regenerated that file with `log_scan_resume.py` for the first pass's exact window (15:38Z–16:13:30Z). It reads the same numbers: app 984, worker 100, browser-worker 565, 0 tracebacks, 116 `auth_rejected` warnings and nothing else.
  - The regenerated file's key check uses the three resume keys. The first pass's check of its 18 keys is still in `logs/log-scan-with-keys-2130.json`.

## 1. Method

1. **Static walk** (`walk_static.py`) of **56 routes**: every `App.tsx` route; job, evidence and scan pages of every engine, write mode and state (failed, canceled, copula, grouped, discover, scan redirect); Reports filters; the 404.
   - **Passes:** 3 passes (dark walk key, light walk key, dark static key) × 1440 and 390 px, with full-page shots.
   - **Wider widths:** a 768 and 1024 px sweep of all 56 routes (`extra.py`, attributed per route by `sweep_attr.py`).
   - **Mockups:** the 12 mockups at 1440 and 390 px in both themes, plus wizard steps 1–5 (17 renders).
2. **Interactive flows** (`walk_flows.py` and others). Each state was measured at 1440 and 390 px in dark and light.

| Flow | Walked | Result |
|---|---|---|
| Playground (02) | Detect (12 findings), Obfuscate JSON + key card, Reveal, Synthesize (no key card, no Reveal), Text, `analytics-safe`, hand-over to Documents | 8 views, 0 events |
| Documents (08) | Demo KYC scan (2 pages): Redact (14 findings, page 2), Obfuscate + reveal, Synthesize, OCR `always`. The redacted PDF download fires. | 8 views, 0 events |
| Policies (05) | New policy `s1m4c1-70fcbf` from `gdpr-pseudonymise`; AADHAAR → `redact`; declared field; recognizer "1 match · 1 on the allow list"; plan + FF1; draft diff; publish v2 (`CHG-S1M4C1`); v1/v2; matrix filters/search; `pci-dss` | 14 views, 0 events |
| Wizard PostgreSQL (06 → 03 → 10) | `bank-pg-ro` 3/3, 16 cells, **synthesize** to `bank_uat.s1m4c1_syn_f02deb` (14 s); the seed card is shown once, then no JSON after "stored"; evidence sections 1–8 incl. 6; A4 794/794 | 11 views, 0 events |
| Wizard Oracle | `smoke-ora-src` 2/2, 16 cells, `CRM_MASKED.S1M4C1_3121F7` (43 s), evidence, A4 OK | 11 views, 0 events |
| Wizard MongoDB | `smoke-mdb-src` 2/2, 17 cells, `s1m4c1_75d79f` (18 s), evidence, A4 OK | 11 views, 0 events |
| Wizard step 4 (`wizard_modes.py`) | Export files, In place, Append panels. Typed-confirmation dialogs "Confirm: overwrite 11,989 rows in bank.customers" (blocked: no target role) and "Confirm: append 11,989 rows…". Primary disabled; Escape closes them; nothing confirmed. | 5 views, 0 events |
| Job groups (07) | Smoke group: 3 systems, wires, consistency **passed 3 / 3**, 3 coverage rows. Search and 5 filters; CSV fires. New group `s1m4c1-410042`: Done is disabled until "stored"; `.dpskey`/`.dpsseed` fire; no key material in storage; "Add a job to this group" opens the wizard preset. | 8 views, 0 events |
| UI scanner, synthesize (12 → 04 → 10) | Session check "✓ Session valid · /apply returned 200 without a redirect". Crawl `s_01M503J403…DF` until "finished · 1:20", 21 pages. JSONL fires. Parallel view both sides, Screens/Fields, page 3. Inventory 63 rows (risk-only 8). Captures (3). Exports fire. Evidence "UI scan demo-site:8410 → captures and exports", A4 OK. Saved-scan setup. | 15 views, 0 events |
| **UI scanner, obfuscate (new)** | The same with Fill mode "Obfuscate captures" (chip "reversible tokens"). Key card blurred. `s_01M503J71E…QS` "finished · 1:19", 21 pages, 63 fields, 3 captures with 3 "Download reversible map" buttons. Evidence and A4 OK. | 16 views, 0 events |
| Reports (09) | 7 days; Connectors/UI/API; OK/Failed (URL keeps the filters); search `CHG-S1M4C1-ORA` → 1 pack; chart as table; older rows/packs; audit CSV fires; open a pack and back | 9 views, 0 events |
| Admin (11) + states (`admin2_flow.py`) | Create key (shown once, blurred, then hidden; storage holds only `dps.apiKey`/`dps.tenant`/`dps.theme`). Disable. Wildcard host refused. Add tenant `s1m4c1-a95d71`. Register host `uat-a95d71.bank.internal` (row + fallback footer), then remove it. Rotate `s1m4c1-rot` (now / 24 h / 7 d; shown once). Re-enable, then disable. | 18 views, 0 events |
| Job controls (`extra2.py`, `extra3.py`) | PG run `bank_uat.s1m4c1_ctl_72cc49`, driven from its page: **Pause → paused, Resume → waiting, Cancel → canceled**, with **2,000 rows written**. This is the canceled run of §2. | 6 views, 0 events |
| Copula pack, no key, theme (`extra.py`) | Section 6; A4 794. Without a key every page names the missing key (401s, 0 page errors). Theme survives a reload. Stop crawl is disabled on a finished scan. | 0 events (the 401s are deliberate) |
| Layout and drawer (`drawer.py`) | 14 pages × 6 widths = **84 checks: 0 problems.** Aria, focus, Escape, scrim, same-section and navigate all behave as on mockup 01. | 0 events |

3. **Lens probes.**
   - `b1_main.py`: the S2 r3 B1 surfaces against the API truth.
   - `b1r3_probe.py`: the S2 r2 UI-sweep rule.
   - `b2_rows.py`, `scan_rows.py`: scan rows against the API.
   - `downloads_probe.py`: 16 buttons.
   - `scope_probe.py`: a no-`scan` key and a detect-only key.
   - `overflow_probe.py`, `wizard_overflow.py`: inner overflow.
   - `phone_shots.py`, `final_checks.py`, `compare.py` (mockup labels), `aggregate.py` (totals).
   - New: `n5_probe.py`, `jobs_rows.py`, `jobs_counts_probe.py`, `repro_*.py`, `log_scan.py`.

## 2. The S2 r3 open findings

### Console B1 (`932075a`): a stopped run is worded by its batch — **FIXED**

**Truth from the API** (counts only, `logs/b1-main.json`):
- 23 runs ended failed or canceled; 20 of them wrote 0 rows.
- 3 wrote rows:
  - `j_01M4XD46S7VEDXE10YTMM0YHJX`: Oracle `CUSTOMERS_SMOKE`, leakage, 9,000 of 20,000.
  - `j_01M4XD46RY4DE9TGKM2RE97D52`: Mongo `profiles_smoke`, leakage, 9,000 of 20,000.
  - **`j_01M503K052WBTV49Y903N0FXBY`**: PG, **canceled** from its console page by this walk, 2,000 of 20,000.
- Each pack's `summary.rows_written` equals its job's `progress.rows_written` (0 mismatches).
- `/reports/jobs` lists the 2 leakage runs as failed sweeps with `paths: ['partition 0']`.

**What the console says, at 1440 dark and 390 light** (composite `mock/B1r3-fixed-on-main.png`):

| Surface | Result |
|---|---|
| Reports › Evidence packs (Connectors, Failed): **23 rows** | **23/23 correct.** The 2 leakage runs read "stopped · 9,000 rows written before it" with the "failed closed" chip. The canceled run reads "table · 2,000 rows · canceled" with "not verified · job canceled". The 20 that wrote nothing read "stopped, nothing written". |
| Reports searched by each job id (all outcomes) | The same rows. No "nothing written" for any run that wrote rows. |
| Reports › Failed sweeps | Both entries: "The leakage sweep found a source value in a batch of partition 0. That batch was not written (fail closed); batches committed before it may already be in the target. Open the job to see what was written." The fix line is "Open the job to see what was written, then re-run it."; "Review the columns named above" appears nowhere, because no column is named. "nothing reached the target" and "The partitions were discarded before writing" appear on no page. |
| Evidence pack › 5 Verification › Post-write sample | Leakage runs: "not run · The job ended failed before the post-write sample; the 9,000 written rows were not re-read. · skipped". **Canceled run:** "The job ended canceled before the post-write sample; the 2,000 written rows were not re-read.". Row counts match: 9,000 / 9,000 and 2,000 / 2,000. |
| Job pages | Leakage runs: "The leakage sweep found a source value in the output, so the batch was not written (fail closed)"; Evidence so far "leakage caught · failed closed", Rows protected 9,000. Jobs list for the canceled run: "10% 2,000 / 20,000 canceled · written rows stay". |
| Events / h-scroll | 0 / none |

**Regression of S2 r2 B1** (one meaning of a UI sweep, `b1r3_probe.py`). Still fixed at 7, 14 and 30 days:
- UI passed 8 equals the 8 succeeded scans. This walk's deliberately failed scan (`secret_unavailable`) counts as no sweep.
- Tile "624 · 622 passed · 2 failed, all failed closed", and the chart table sums to the tile under every layer.
- Overview posture 99.7 (displayed 100 %, N4).

**Regression of S2 r1 B1–B3:**
- Drawer: 84/84.
- Scan rows: 17/17 UI packs match the API, 0 "UI scan of —".
- Hosts card: fallback rows and footer, with the registered row and after removal.

### Captures-privacy B1a–c (`40d8b34`): closed selects — **FIXED (verified by the gates verifier)**

S1-M4 gates r1 §4.2 re-ran S2 r3's own hostile select pages three ways: in process, through the crawler and in the deployed browser-worker image. Every masked or obfuscated screen read 0, every unmasked control read its value, and the 36 tests of `test_capture_select_drawn.py` passed.

OCR of hostile pages is the captures-privacy lens's method, so I did not repeat it. From the console side, the Captures tab of both of this walk's crawls renders the stored masked captures: 3 per scan, chips "masked capture · exported", obfuscate tokens in place, 0 console events.

### S2 r3 N5: main's newer console text after the merge — present (`n5_probe.py`)

- **Wizard step 1, New connection + production:** "Any write to a production-tagged connection (in place, append, a table load or files) needs the admin scope…", with the chip "production · writes need the admin scope".
- **Admin › Registered connections:** "…as does every other write there (a table load, files)…".
- **MongoDB and Oracle evidence packs:** section 3 column labels via `columnLabel`, e.g. `accounts[].iban`, `contact.addresses[].line1`, `customerId`. No `$.` in labels or notes.

All at 1440 dark and 390 light, with 0 events and no h-scroll.

## 3. Blocking findings

### B1 — a job whose connection was deleted logs console errors, and its head loses engine and name

**What happens.** `JobDetail.tsx:238-239` fetches the job's source and target with `GET /api/v1/connections/{id}`. It catches the error, but Chromium still logs each 404 as a console error. The deletion that causes this is a documented operation:
- `api-m2.md`: `DELETE /connections/{id}` with the jobs scope;
- it is refused (`connection_in_use`) only while a non-terminal job uses the connection;
- so a finished job keeps its history while its connection is gone.

The page then shows:
- the engine badge `—`;
- the head `— · people → people_idem_x4b5d19` or `— · bank.customers`;
- "Discovery · connection c_01M504…CG".

For a proposed snapshot of such a job it still offers **Review and accept**. That opens `/data-sources/jobs/new?snapshot=…` (`JobWizard.tsx:109`), which logs another 404 console error and shows "Could not open the snapshot: connection not found (connection_not_found)".

**Evidence:**
- **In the walk.** The walk's failed-run route `j_01M4XKWTTXT9AMM41Y3G6SQFWP` (an S1-M3 security test run whose two connections were deleted) logged 2 × "Failed to load resource: … 404" at **every width in every pass**: 3 static passes × 1440/390, plus 768 and 1024. That is 10 errors (`logs/static-routes-*.json`, `logs/sweep-attribution.json`). No other route logged a console error that was not deliberate.
- **On the stack.** Before the walk, the default tenant had **35 jobs whose source connection was deleted, 10 of them also their target**; 12 were runs (dps-db ids and counts only). The reproduction below (a discover job) brought it to 36.
- **Reproduction** (`repro_deleted_conn.py`, `logs/repro-deleted-conn.json`, `shots/repro-deleted-conn-*.png`, `mock/N-deleted-connection.png`):
  1. Create `s1m4c1-tmp-f0e8c4` with `bank-pg-ro`'s non-secret params and the same secret reference (201).
  2. Discover `bank.customers` (202, then succeeded).
  3. `DELETE` the connection (204; a later GET is 404).
  4. Open the job page at 1440 dark and 390 light: a console error per visit, and the head reads "— · bank.customers".
  5. `repro_wizard_link.py`: its Review and accept link logs a second console error and shows the wizard error.

**Why it blocks.**
- The console gate is "0 console errors on every page", and the repo's own `tools/console_check.py` counts every console `error`.
- This is an ordinary history page after a supported operation. It is not a deliberate refusal of invalid input (the precedent for N-level console errors in S1-M3 privacy-console N1). The page also loses which system the job read.
- Privacy impact: none.
- It is M2 code (`da9be83`, in `v0.3.0-m2`), not caused by the merge. It became visible because verifiers delete connections on main's stack.

**Fix** (do not change the delete rule):
1. Do not fetch by id a connection that can be gone. Give the job page data that survives deletion. Either:
   - `GET /jobs/{id}` carries a value-free snapshot of each connection (engine, name) taken when the job was created (the evidence pack already records "MongoDB · connection smoke-mdb-src"); or
   - the page resolves both ids from `GET /connections` (a list: no 404) and words a missing one as "connection deleted (c_…)", with the engine from the job.
2. Hide or explain **Review and accept** when the snapshot's connection no longer exists. The wizard deep link should say so without a failing request.
3. **Tests, failing first:**
   - a static-render test of the job head for a deleted connection (engine/name or "deleted" shown, never `—`);
   - the browser reproduction: `run.sh repro_deleted_conn.py` and `run.sh repro_wizard_link.py` → 0 console errors.

### B2 — the Jobs page shows the newest 200 jobs as if they were all the jobs

**What happens.** `Jobs.tsx:197` loads `api.jobs.list(auth, { limit: 200 })` once and never follows `next_cursor`. Everything on the page is computed over that page, and nothing tells the user so:
- the scans are dropped (`tableJobs`);
- the head promises "Batch jobs over PostgreSQL, Oracle and MongoDB in the last 7 days";
- the card says "Showing N of N jobs";
- the Status counts and the search use only that page.

**Evidence** (`logs/jobs-counts.json`, `logs/b2-jobs.json`, `logs/jobs-rows.json`, composite `mock/B2-jobs-cap.png`):

| Source | What it says |
|---|---|
| API (`/jobs`, all pages) | **993 jobs: 970 table jobs, 27 failed table jobs (22 runs), 488 runs succeeded.** All were created in the last 32 hours (14 Oct 08:18 – 15 Oct 16:02 UTC). The page's single load reaches back only to 15 Oct 05:03 UTC, about 11 hours. |
| `/jobs/stats` `by_state` | succeeded 960 · failed 28 · canceled 5 |
| **Overview › Jobs tile** | "0 running · **960 completed · 28 failed** · 8 awaiting review" |
| **Jobs › All jobs** (1440 dark and 390 light) | "**Showing 178 of 178 jobs**"; Status "Completed (165)", "**Failed (0)**", "Canceled (5)". With Failed selected: "No jobs match these filters". |
| Jobs › search for `j_01M4XD46RY4DE9TGKM2RE97D52` | "No jobs match these filters". This is the leakage-stopped run that wrote 9,000 rows, which Reports › Failed sweeps links to. |

**Why it blocks.**
- The console states something false on a core page (mockup 07), and it contradicts the Overview on the same console (28 failed vs Failed (0)). That is the class of S2 r1 B3 and S2 r2 B1.
- An operator triaging failures from the Jobs page sees none. On a bank deployment, 200 jobs (discoveries + runs) arrive well within the stated 7 days.
- The cap comes from the M2 plan (Task 30: "Filters are client-side over the loaded page (`GET /jobs?limit=200`)"). It is not in any deviation log, HANDOFF, API doc or the page itself.
- M2 code (`c2aa77f`), not caused by the merge.

**Fix:**
1. Make the list complete for the window it names. Either:
   - page through `next_cursor` until jobs are older than 7 days (with a hard cap, then "Load older jobs"); or
   - offer "Load older jobs" and say "Showing N of the newest M" whenever the list is capped.
2. Take the Status counts from `GET /jobs/stats` `by_state`, or label them "in the loaded list".
3. When a Status filter or a search is set, ask the server (`GET /jobs?state=…`, cursor) rather than filtering only the loaded page.
4. **Tests, failing first:**
   - Vitest for the loader and counts with a two-page fixture: failed jobs on page 2 appear and are counted.
   - The browser check on main's stack: Status Failed lists the 14 Oct leakage-stopped runs (27 failed table jobs), and a job-id search finds them (`run.sh jobs_counts_probe.py`, `run.sh b2_jobs_evidence.py`).

### B3 — the evidence pack's Summary sentence pushes a long name past the page edge at 390 px

**What happens.**
- `EvidencePack.tsx:155` writes the Summary sentence into `<p className="lead">`. That sentence puts three names in `<b>`: `text(src.object)`, `p.policy?.name` and `targetText`.
- `evidence.css:21` (`.evidence-page .lead`) sets a font, line height and `max-width: 74ch`, but no `overflow-wrap`.
- A name with no break opportunity therefore keeps its full width. `_` and `.` are not break opportunities; spaces, `-` and `/` are. Oracle and PostgreSQL names such as `SCHEMA.TABLE_NAME_PART` have none.
- Such a name crosses the sheet's right padding and border, and then the viewport, so the document scrolls sideways.
- The neighbours already handle this: `.doc-head h1`, `.doc-head .jid`, `.meta .v` and `.stats .v` set `overflow-wrap: anywhere`, and `dl.kv dd .mono` sets `word-break: break-all`.

**Evidence** (counts, ids and shapes only; `logs/resume-*.json`):

| Pack | Name shape (letters→a, digits→9) | 390 px | 375 px | 360 px |
|---|---|---|---|---|
| `ev_01M4XXJ38001JCX9B250VRQVDF` (Oracle synthesize) | `aaa_aaaaaa.aaaa_aaa_aaaaaa_aaa9_999999` (38) | **scrollWidth 392, scrollX 2** | 17 | 32 |
| `ev_01M4XXJGDRDJC47GC0ZNT5E4ZF` (the walk's copula pick) | same shape (38) | 391, scrollX 1 | 16 | 31 |
| `ev_01M4XRXRFCKKWYG1KVFAV538T1` | same shape (38) | 0 | 9 | 24 |
| File targets with `-` or `/` (40–44 characters) | wrap at `-` and `/` | 0 | 0 | 0 |
| Job page and Reports search of the first pack's job | same name | 0 | 0 | 0 |

- `shots/resume-lead-overflow-u-VRQVDF-light-390.png` shows the target name running past the white sheet and cut at the viewport edge.
- Each extra character adds about 9.4 px (357.94 px for 38 characters in the bold 14 px face). A 45-character name would scroll by about 66 px at 390.

**Why it blocks.**
- The lens gate is "no horizontal scroll at 390 px". This pack fails it at 2 px under the harness's own 1-px rule, and its target name is visibly clipped.
- The trigger is ordinary for the bank. Schema-qualified Oracle names of 40 characters or more are routine (identifiers may reach 128 bytes). The evidence pack is the artefact that approvers and auditors open.
- The rest of the console wraps the same name, so only this page fails.
- Privacy impact: none.
- It is not caused by the merge. It was missed before because only the copula/synthesize packs on these stacks have a 38-character name, and the first pass's 1-px tolerance read the walked one as clean.

**Fix:**
1. Add `overflow-wrap: anywhere` to `.evidence-page .lead` in `evidence.css:21`, the rule the page header and meta already use.
   - Optionally also wrap the `<b>` names in `dl.kv dd` (Policy, consistency scope) the same way. They sit in a `minmax(0, 1fr)` column and would overflow the same way with a long policy name.
   - Keep the print rule (`evidence.css:110`) consistent.
2. **Tests, failing first:**
   - A static-render or Vitest layout test of the Summary with a 60-character `SCHEMA.TABLE_NAME` target and policy name: the lead's CSS wraps.
   - The browser check: `run.sh resume_lead_overflow_u.py` → `scrollX` 0 at 390, 375 and 360 for the three packs.
   - The repo's console check at 390 px on a pack with a long name.

## 4. Results by area

**Errors** (`logs/aggregate.out.json` and the per-probe logs)

| Measure | Result |
|---|---|
| Views and measurements | **309 views / 897 measurements** (each a width × theme), excluding deliberate refusals. Also the 84 drawer checks, the 112-route 768/1024 sweep and the 17 mockup renders. |
| Page errors | 0 |
| Console warnings | 0 |
| Failed requests | 0 |
| Console errors that were not deliberate | **10, all B1's**, on one job page: 404 `connection_not_found` |

Deliberate refusals, each worded on the page:
- **Negatives flow:** 9 lines for 422 `host_not_registered` / `start_url_denied` and 404 `evidence_not_found` / `job_not_found` / `scan_not_found`.
- **No-key visit:** 17 × 401 on 5 pages.
- **Reduced-scope walks:** 66 × 403. 64 page loads with a no-`scan` key and a detect-only key, with 0 page errors and 0 non-HTTP console errors.

**Horizontal scroll: none, except B3** (found by the resume re-check). This list is the first pass's reading with the harness's 1-px tolerance. That reading includes `10-evidence-copula` at 391 for a 390 viewport, which is B3.
- At 390 px: every static route (3 passes), every flow state, every dialog (publish, new group, key once, tenant, rotate, cancel job, typed confirmations), the open drawer, the reduced-scope and no-key states, and the B1/B2 probes.
- None at 768, 1024 or 1440 either.

**Inner scroll boxes.**
- The same kinds as S2 r3: wide tables inside `tbl-scroll` and ellipsized ids and paths.
- The widths differ only with the data, e.g. Reports at 1440 has a 4-px inner scroll from a longer row.
- The wizard step-3 review table is still 912 px wide in its box (known, N6).

**Mockup comparison.**
- Structural labels: **210 of 233** found (`logs/mockup-compare.json`). That is exactly S2 r3's count, with the same missing sample-content and documented-deviation labels.
- Montages `mock/m390-{dark,light}-*.png` and pairs `mock/pair-*.png`. Visual review found no broken layout.

**Downloads.**
- 16 download buttons fire their expected files: evidence JSON/PDF for PG, copula and scan packs; API roll-up; policy YAML; config; jobs CSV; scan exports; inventory CSV/JSON; reversible map.
- 2 "Copy as Presidio YAML" buttons copy, as before.
- The flows' downloads also fired: JSONL, personas, redacted PDF, audit CSV, `.dpskey`/`.dpsseed`.

**D4.**
- The demo site's `/__received` read **16/4/2/2** (`non_get`/`form_get`/`logout_hits`/`admin_hits`) before (21:09) and after (21:43) the walk.
- `by_path` was identical.
- Meanwhile this walk ran 2 crawls (both modes), 2 session checks and the deliberately failed scan. Each crawl recorded 60 POST + 1 GET blocked and `non_get_allowed` 0.

**Logs** (`log_scan.py`, app/worker/browser-worker since 15:38Z = 21:08 IST)
- Lines read through 21:43: app 984, worker 100, browser-worker 565.
- 0 tracebacks and 0 error or critical lines.
- 116 `auth_rejected` warnings, all deliberate:
  - this walk's 403 scope probes and 401 no-key visit;
  - another verifier's `reason=tenant` probes on `/scans`.
- **0 lines contained any of the 18 keys this lens minted.** I checked this at 21:30, before the keys were disabled and their files deleted (`logs/log-scan-with-keys-2130.json`).

## 5. Non-blocking notes

- **N1. Terminal failures read as progress or success in the step indicators and on the job card** (M2/M4 code).
  - A scan that failed at setup (`secret_unavailable`, 0 pages) shows **Setup ✓ Crawl ✓ Classify ✓** (`ScanChrome.tsx:31-32`: `done: terminal`).
  - The failed `row_count_mismatch` job shows **Run ✓ · — · 0 partitions**.
  - "Evidence so far · **verifier passing**" (emerald) shows on failed and canceled jobs whose pack says "not verified · job failed/canceled".
  - Suggestion: mark a step done only on success, and show the pack's verdict on terminal jobs.
- **N2. Leakage-stopped packs say "0 hits".** The pack's Summary tile reads "Leakage sweep **0 hits**" (rose) and its Verification row "0 hits · fail". The job page and Reports say "failed closed". The pack's Summary sentence explains the 0 (the refused batch's hit is not counted), but "failed closed" in the tile and the row would read consistently.
- **N3. The Reports "Evidence packs" tile total leaves out the roll-ups it lists.** It shows "521 · 504 jobs · 17 scans · 22 API roll-ups". The mockup's numbers add up (62 = 48 + 9 + 5), and the table below lists the roll-ups as packs. Include them, or reword "…; plus 22 API roll-ups".
- **N4. Carried from S2 r3, unchanged:**
  - Overview "Leakage sweeps passed 100 %" at 99.7 (N1).
  - Append dialog "customer_id = original + —" without an offset (N2).
  - No-`scan` key: the Overview offers 6 scan links that open "Retrying." forever.
  - The 404 page still says "This screen arrives with its milestone…", although every milestone is now merged.
  - The finished-scan stepper leaves step 4 numbered.
  - No `unsafe_pattern` message for `(a+)+$` (F1/`fsec`).
- **N5. Overview Layer 3 wording.**
  - "Last scan: **1 pages**, 2 personas" is not pluralized (`Overview.tsx:219`).
  - "0 submitted" is the window total, not that scan's.
  - A running newest scan is described as "Last scan".
- **N6. Wizard step-3 review table** is 912 px wide inside its box at 1440; the 16 Method selects start outside the visible part (S2 r2 N6.N2).
- **N7. Long ids wrap mid-token** in the evidence pack meta at 1440 ("key_01M503EXEZPA6P44TJ5BA9CYN / 0", "grp_…9SQT / 9") and in Admin's secret-reference column ("dps/s3/expor / ts"). Cosmetic.
- **N8. Other verifiers' traffic was visible during the walk** (jobs `*_s1m4reg1b`, scans of `demo-site:18441`). It caused none of this lens's events. B1's errors were attributed per route.
- **N9. The main checkout is dirty: 54 tracked `m3-shots/*.png` were rewritten at 21:31:44–21:32:18 by a concurrent pytest run** (A-N2). Not by this lens. The run's owner, or the orchestrator, should restore them by explicit pathspec once no test is running:
  ```bash
  git -C /Users/neelu/document_intelligence/data-privacy-services checkout -- docs/superpowers/build/m3-shots
  ```
  A-N2 itself (console tests rewrite tracked screenshots) remains an F4 polish item.
  **Update 22:03 (resume):** `git status` of the main checkout is empty again; the PNGs are no longer modified. I did not restore them.

## 6. Objects this walk left on main's stack

Names are prefixed `s1m4c1`. Nothing was deleted except my own temporary connection (B1 reproduction).

- **Tenant:** `s1m4c1-a95d71`, with no host (its host was added and removed).
- **Policy:** `s1m4c1-70fcbf` (v2 published, `CHG-S1M4C1`).
- **Job group:** `s1m4c1-410042`.
- **Targets:**
  - `bank_uat.s1m4c1_syn_f02deb` (synthesize);
  - `CRM_MASKED.S1M4C1_3121F7`;
  - Mongo `s1m4c1_75d79f`;
  - `bank_uat.s1m4c1_ctl_72cc49` (canceled run, 2,000 rows).
- **Scans:**
  - `s_01M503J403ZN78PAKMS0A7SMDF` (synthesize) and `s_01M503J71EVAM2WW68MGR8VJQS` (obfuscate);
  - session checks `s_01M503HZ5PPKCCB6Z5TSZFX91S` and `s_01M503HZ4AJ04ZKADA77V654V9`;
  - the deliberately failed `s_01M503J5FF6G0Y0BJCV0ZGFD3N` (`secret_unavailable`).
- **Jobs:** the wizard runs, the canceled control run `j_01M503K052WBTV49Y903N0FXBY`, and the reproduction's discover job `j_01M50433D1QPEPCB464WA6F48K` (its connection `s1m4c1-tmp-f0e8c4` deleted, its snapshot proposed).
- **Discoveries and snapshots:** those of the wizard flows and of the step-4 walk.
- **Keys:** 20 `s1m4c1-*` keys, all **disabled**.

## 7. Re-check after the fix

Everything runs from `~/.cache/dps-build/reports/S1-M4-console-r1/` with `run.sh <script>`. First mint fresh keys with `S2_KEY=static run.sh mkkey.py s1m4c1-<n> <scratch>/<n>.key '*' '*'`.

- **B1:**
  - `run.sh repro_deleted_conn.py` and `run.sh repro_wizard_link.py` → 0 console errors, and the head names the engine and connection.
  - `walk_static.py routes-dark`: `03-job-failed-leakage` → 0 events. Re-run `ids.py` first; its failed-run pick is `j_01M4XKWTTXT9AMM41Y3G6SQFWP`, whose connections are gone.
- **B2:** `run.sh jobs_counts_probe.py` and `run.sh b2_jobs_evidence.py` → the Jobs counts agree with `/jobs/stats`, Failed lists the 14 Oct leakage runs, and a job-id search finds them.
- **Regressions:**
  - `b1_main.py` (S2 r3 B1, 23 stopped runs);
  - `b1r3_probe.py`, `drawer.py`, `b2_rows.py`;
  - `walk_flows.py` (all flows, including `scanner-obf`);
  - `aggregate.py`.
- **B3:**
  - `run.sh resume_lead_overflow_u.py` → `scrollX` 0 at 390, 375 and 360 on the three packs with 38-character names.
  - `run.sh resume_recheck.py routes-light-390` → no `scrollWidth > innerWidth` on any route (read the JSON, not only the 1-px-tolerant `hscroll` flag).
- **Quick whole-lens re-check:**
  - `resume_recheck.py routes-dark-1440`, `routes-light-390` and `b2`, run in parallel with one key each through `bg.sh`. About 2.5 minutes.
  - After the fix, all of them should read 0 console errors.
- **Finish:** `cleanup_keys.py` or `cleanup_keys_resume.py` (static key), then delete the scratch key files.
