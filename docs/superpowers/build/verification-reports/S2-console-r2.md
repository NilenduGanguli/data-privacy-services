# S2 — branch `m4` after main was merged into it (CONSOLE lens verifier, round 2)

**Verdict: FAIL — 1 blocking finding (small to fix). The three round-1 findings B1–B3 are fixed and verified.**

| # | Finding | Introduced by |
|---|---|---|
| **B1 (new)** | **Reports & audit contradicts itself when a UI scan fails.** The "Verification sweeps" tile reads "236 passed · **2 failed, all failed closed**" and the chart shows a red bar on 14 Oct. The "Failed sweeps" card next to them reads "**0 in 14 days** · No sweep failed in this window." With Layer = UI scanner, the tile reads "27 passed · 2 failed" and the card can never list anything. The Overview posture says "Leakage sweeps passed **100 %**". The 2 "failed sweeps" are 2 failed scans (`secret_unavailable`: the crawl never started). Mockup 09's tile and card agree with each other ("3 failed" / "3 in 14 days"). | **The merge** (M5 Reports × M4 scans): M5's `jobs_report` counts a failed scan as a failed UI sweep, and nothing else does. The bug was already on screen in round 1 ("121 passed · 1 failed" next to "0 in 14 days"), but round 1 did not report it. |

Everything else in the lens is green:

- **Errors.** 0 console errors, 0 page errors, 0 console warnings, 0 HTTP ≥ 400 and 0 failed requests in **280 console views / 805 measurements** (each a width × theme). Also 0 in 84 drawer page×width checks, a 768/1024 px sweep of 52 routes, 17 mockup renders and 23 download buttons. Deliberate refusals are counted separately (N1).
- **Horizontal scroll.** None at 390 px. None at 768, 1024 or 1440 px either. None while the navigation drawer is open, in any dialog state, or in any reduced-scope or rate-limited state.
- **Flows.** Every interactive flow of M1–M5 completed end to end in the browser. Admin states round 1 did not walk also passed: add tenant, register/remove host, rotate key, re-enable/disable.
- **Round-1 findings.** B1 (navigation below 1100 px), B2 ("UI scan of —") and B3 (Admin hosts card) are fixed and match the mockups (§3).

| | |
|---|---|
| Date | 2026-10-15, 01:15–01:45 IST |
| Tree verified | `dps-wt-m4`, branch `m4` @ **`a3cd667`** (S2 fix round 1). Clean at start and end; no repository file modified (`git status` empty). Main's `5c11e49`/`d2fdc3f` are not in this tree (N9). |
| Stack | m4 compose project (app 8414, demo site 8420) as the gates verifier left it: 11 services healthy, `/readyz` ready, browser 1 online. Not restarted, rebuilt or brought down. Other S2 verifiers were active at the same time (tenants `s2r2a`, `s2r2b`, `s2aprobe`). |
| Browser | Private Playwright `playwright==1.55.0`, headless Chromium 1187, venv `~/.cache/dps-build/pw-s2-console`. Never the shared MCP browser. |
| Keys | The per-key rate limit (50 rps, burst 100) is shared by every verifier that uses the static key. To keep the walk out of that shared bucket, it ran under its own all-scope database keys (`s2con-r2-walk`, `-flows-a/b/c`), minted through the Admin API. The raw keys were written only to 0600 files in the session scratchpad, which are now deleted. A full static pass of all 52 routes with the **static key** confirmed the environment-key path: 0 events. **All 10 `s2con-r2-*` keys are disabled** (`logs/cleanup-keys.json`, `scope-api-probe.json`). |
| Probes, logs, shots | `~/.cache/dps-build/reports/S2-console-r2/`. Scripts are at the top level (run through `run.sh` → `./lane.sh`), JSON in `logs/`, 988 screenshots in `shots/`, and pairs, montages and crops in `mock/`. |
| Privacy of the probes | Key material was blurred before any screenshot was taken while it was on screen (Playground, Documents, wizard run result, Admin new or rotated key, group dialog). Downloads were exercised with `accept_downloads=False`: only the event and the file name were checked. Logs hold counts, routes, codes and scrubbed texts. A grep of `logs/` finds no key string. |

## 1. Method

1. **Static walk** (`walk_static.py`). 52 routes, three passes: dark and light with the walk key, and dark with the static key. Each route load was measured at 1440 and 390 px. The routes cover every `App.tsx` route plus real ids:
   - Job details: PostgreSQL obfuscate, synthesize, files and in-place; Oracle; MongoDB; grouped Oracle and MongoDB; canceled run; copula run; PostgreSQL, Oracle and MongoDB discoveries.
   - The scan-id redirect.
   - Scanner setup: new, saved and failed.
   - Scan detail: synthesize and obfuscate scans × parallel/inventory/captures, both fill sides, and the failed scan.
   - Evidence packs: 13, including the copula pack, PostgreSQL synthesize, canceled, grouped and both scan packs.
   - Policies: the landing route, a tenant policy and two presets.
   - Reports with all layers and with the UI-scanner layer; Admin; the 404 page.

   All 12 mockups were rendered at 1440 and 390 px, dark and light, plus wizard mockup steps 1–5. `extra.py` re-swept every route at 768 and 1024 px.
2. **Interactive flows** (`walk_flows.py`, five browsers in parallel, each state measured at 1440 and 390 px, dark and light, with a full-page shot):

| Flow | Walked | Result |
|---|---|---|
| Playground (02) | Detect (12 findings), Obfuscate JSON (key card), Reveal, Synthesize (no key card, no Reveal), Text obfuscate, `analytics-safe`, hand-over to Documents | 8 views, 0 events |
| Documents (08) | Demo KYC scan (2 pages): Redact (14 findings, page 2), Obfuscate + reveal text, Synthesize text, OCR `always`; Download redacted PDF fires `kyc-scanned.redacted.pdf` | 8 views, 0 events |
| Policies (05) | New policy `s2con-r2-5522e7` from `gdpr-pseudonymise`; AADHAAR → `redact`; declare `LOANS.APPLICANTS.EMPLOYER_NAME`; recognizer live test "1 match · 1 on the allow list" and add; plan preview + FF1 toggle; draft diff; publish v2 with ticket; v1/v2 diffs; matrix filters/search; `pci-dss` page | 14 views, 0 events |
| Wizard PostgreSQL (06 → 03 → 10) | `bank-pg-ro` (3/3), `bank.customers WHERE country_code = 'IN'`. Discovery (16-cell heat map), accept, **synthesize** to `bank_uat.s2con2_syn_7f85a7`. The seed card is shown once; after "stored" no JSON remains. Evidence sections 1–8, including 6 "Synthetic fidelity and privacy". A4 print 794/794. | 11 views, 0 events |
| Wizard Oracle | `smoke-ora-src` (2/2), `CRM.CUSTOMERS WHERE SEGMENT = 'retail'`, discovery (16 cells), `CRM_MASKED.S2CON2_5522E7` (run 28 s), evidence 1–5, 7, 8, A4 OK | 11 views, 0 events |
| Wizard MongoDB | `smoke-mdb-src` (2/2), `profiles {"segment":"premium"}`, discovery (17 leaf cells), `crm_uat.s2con2_ca2d84` (18 s), evidence, A4 OK | 11 views, 0 events |
| Job groups (07) | Smoke group card: 3 systems, wires; consistency check **passed, 3 / 3 consistent**, 3 coverage rows. Jobs search and the 5 filters; Export list (CSV) fires. New group `s2con-r2-ca2d84`: Done is disabled until "stored"; `.dpskey`/`.dpsseed` fire; no key material in storage. "Add a job to this group" → wizard preset. | 8 views, 0 events |
| UI scanner (12 → 04 → 10) | Setup; Check session ("✓ Session valid · /apply returned 200 without a redirect"). Crawl `s_01M4XZGJC9PRX011CHKC1CJ67V`: live map, progress and log until "finished · 1:26", 21 pages. JSONL fires. Parallel view on both sides; Screens and Fields; page 3. Inventory 63 rows (risk-only 8). Captures (3). Inventory and persona exports fire. Evidence "UI scan demo-site:8410 → captures and exports", A4 OK. Saved-scan setup. | 15 views, 0 events |
| Reports (09) | 7 days; Connectors/UI scanner/API; OK/Failed (URL keeps the filters); ticket search → 1 pack; chart as table; older rows/packs; audit CSV fires; open a pack and come back | 9 views, 0 events |
| Admin (11) | Create key (detect): shown once, then hidden after "stored" (storage holds only `dps.apiKey`, `dps.tenant`, `dps.theme`). Disable. Invalid host refused in the browser. Tenant dialogs opened. Hosts card text (B3). | 8 views, 0 events |
| **Admin, new in r2** (`admin2_flow.py`) | **Add tenant** `s2con2-9dfa14`. **Register** host `https://UAT-9DFA14.BANK.INTERNAL:443/start` for it: it shows as `https://uat-9dfa14.bank.internal`, "1 registered", and the footer notes the fallback. **Remove** it (dialog): the card returns to the service-default rows. **Rotate** `s2con-r2-detect`: dialog with now / 24 h / 7 d; the new key is shown once, then hidden; the old key reads "rotated · stops 15 Oct". **Re-enable**, then **disable** a key. | 10 views, 0 events |
| Job controls (`extra2.py`) | PostgreSQL run driven from its page: **Pause → paused, Resume → waiting, Cancel dialog → canceled**. Evidence "not verified · job canceled". Failed scan: 3 tabs + setup "Crawl failed …". | 8 views, 0 events |
| Copula pack, no key, theme (`extra.py`) | Section 6 (fidelity, correlation, privacy, utility), A4 OK. Without a key every page explains the missing key (401s, 0 page errors). The theme survives a reload. On a finished scan, Stop crawl is disabled. | 0 events |

3. **Lens probes.**
   - **B1:** `drawer.py`, 14 pages × 6 widths against `vault.js`.
   - **B2:** `b2_rows.py` (every UI pack row against the API), `scan_rows.py` and `brief_probe.py`.
   - **B3:** the Admin flows and `phone_shots.py`.
   - **Downloads:** `downloads_probe.py`.
   - **Inner overflow:** `overflow_probe.py` and `wizard_overflow.py`.
   - **Reduced-scope keys:** `scope_probe.py` (no `scan`; detect only) and `scope_api_probe.py`.
   - **HTTP 429** (gates-r2 N3): `ratelimit_probe.py`.
   - **The new finding:** `sweeps_probe.py` and `newb1_shot.py`.

## 2. Blocking finding

### B1 (new) — Reports says sweeps failed and, in the next card, that none failed

- **Evidence (counts only):**
  - **API**, from `logs/sweeps-probe.json`, for both 14 and 30 days:
    - `GET /reports/jobs` `sweeps`: layer `ui` passed 27, **failed 2** (both on 2026-10-14); api 77/0; conn 132/0.
    - `totals.sweeps_failed` is **2**.
    - **`failed_sweeps` is `[]`**.
  - **The two failures are failed scans:**
    - `s_01M4XZGMTZ942A0CE3Q3Z300SC`, from this walk's deliberate foreign-ref probe;
    - `s_01M4XKAYW988906ETSZKQQCJK2`, the same probe in round 1.

    Both are `failed` with `secret_unavailable`, have no evidence pack and swept nothing.
  - **Console, all layers:** "Verification sweeps 238 · 236 passed · **2 failed, all failed closed**", a red segment on Oct 14, then "Failed sweeps **0 in 14 days** · No sweep failed in this window. A failed sweep stops the batch before anything is written." Shots:
    - `shots/NEW-reports-sweeps-contradiction-all-dark-1440.png`;
    - at 390 px: `el-reports-sweeps-*-390.png` and `el-reports-failed-card-*-390.png`.
  - **Console, Layer = UI scanner:** "29 · 27 passed · 2 failed", with the same empty card (`NEW-…-ui-dark-1440.png`).
  - **Overview:** Posture score "Leakage sweeps passed 100 %".
  - **Round 1** showed the same contradiction: "121 passed · 1 failed, all failed closed" next to "0 in 14 days" (`S2-console-r1/shots/reports--landing-dark-1440.png`, crop `mock/r1-reports-landing-kpi.png`).
  - **Mockup 09** keeps them consistent: "1,454 passed · 3 failed, all failed closed" and "Failed sweeps 3 in 14 days", with each failure listed.
- **Root cause:**
  - `dps/reports.py::jobs_report` (lines 622–627) counts a `job.scan` roll-up row with outcome `failed`/`dead` as a failed UI sweep.
  - `failed_sweeps` (lines 670–689) lists only leakage partitions (`where="job"`) and API `verification_failed` rows (`where="api"`), never a scan.
  - `frontend/src/pages/Reports.tsx:47` maps `where === 'job'` to Connectors and everything else to API. Under Layer = UI scanner the card can therefore never list anything, even if the API returned a scan failure.
  - The Overview posture (`dps/reports.py` lines 532–546) counts sweeps from API batches and partitions only, so it reads 100 %.
  - The result is three different answers to "did a verification sweep fail?" on two pages.
  - Any failed scan triggers it, for example an expired or unreadable stored session, which is an everyday operational event.
- **Fix (small, both sides).** Give a UI sweep one meaning and apply it to the tile, chart, card and Overview posture.
  - **Preferred: a scan that failed before it swept anything is not a failed verification sweep.**
    - Count UI sweeps from scans that ran their checks: succeeded → passed. A scan whose own verification failed (for example non-GET allowed > 0 or a capture withheld) → failed, and listed.
    - Leave operational failures to the scanner and job pages, where the evidence list already says "not verified · scan failed".
  - **Acceptable alternative.** Keep counting a failed scan as a failed sweep, but make every surface agree:
    - list it in `failed_sweeps` (`where: "scan"`, scan id, `error_code`, day);
    - map `where === 'scan'` to Layer = UI scanner (and All) in `Reports.tsx`;
    - word it, linking `/scanner/scans/:id`, e.g. "UI scan s_… stopped (secret_unavailable) before any page was filled; nothing was submitted";
    - include it in the Overview `sweeps_passed_pct`.
  - **Tests:**
    - an API test with one failed scan: the tile's UI failed count equals the number of UI entries in `failed_sweeps`, or both are 0;
    - a Vitest of `ReportsView` with a UI failure under Layer All and UI scanner;
    - an Overview posture assertion.

## 3. Round-1 findings re-checked

**B1: navigation below 1100 px. FIXED.**

`drawer.py` (`logs/drawer.json`) ran the following, with 0 problems and 0 events:

- **14 pages:** the 7 sections plus Documents, a preset policy, the wizard, an Oracle job, a scan detail, an evidence pack and the 404 page.
- **390 and 1024 px, full sequence:**
  - The toggle is visible with `aria-controls="sidebar"`, `aria-expanded="false"` and "Open navigation", and the sidebar is hidden.
  - Open: the drawer is 280 px wide (`min(280px, 86vw)`) at left 0 and fixed. The scrim covers the viewport. All 7 sections are listed and the active one is marked. Focus goes to the active section (Overview on the 404 page). The toggle reads `true` and "Close navigation". No horizontal scroll.
  - Escape closes it and focus returns to the toggle. A scrim click does the same.
  - Clicking the current section closes the drawer.
  - Clicking another section navigates, closes the drawer and marks that section active. Every section was reached this way, `/policies` landing on `/policies/<first tenant policy>`.
- **768 and 1100 px, short sequence:** toggle, open, 7 items, no horizontal scroll, Escape.
- **1101 and 1440 px:** the sidebar is shown and there is no toggle.

The same probe on mockup 01 gives identical aria, focus, width and Escape behaviour. In `mock/drawer-console-vs-mockup-390.png` (light and dark) the console drawer is visually the mockup's.

**B2: "UI scan of —". FIXED.**

- `b2_rows.py`, Reports with Layer = UI scanner:
  - **24/24 pack rows match the API summary exactly**: host `demo-site:8410`, pages, forms, personas and submissions, in the form "21 pages · 14 forms · 20 personas · 0 submissions".
  - There are 0 "UI scan of —".
- All layers: 6 UI rows, 0 dashes.
- Overview: "Recent jobs and scans" names the host on every scan row, and the API recent scans have `source` = `demo-site:8410`.
- `scan_rows.py` (round 1's probe): Overview 3 rows with the host; Reports 8; Reports with the UI layer 27; 0 dashes.
- Shots: `b2-reports-ui-1440.png`, `el-reports-ui-rows-*`, `el-overview-recent-*`.

**B3: Admin › UI-scanner hosts. FIXED.**

- On this local stack the card says:
  - "No tenant has a registered host. This local deployment lets every tenant without one scan the service default below (DPS_SCAN_REGISTERED_HOSTS); staging and production refuse scans until a host is registered."
  - It lists `localhost`, `127.0.0.1` and `demo-site` as "service default", for "every tenant without a host".
  - It says nothing about scans being refused.
- With a host registered for one tenant (`admin2`), the single row shows and the footer adds "(on this local deployment a tenant without a host of its own may scan the service default: localhost, 127.0.0.1 and demo-site)".
- After removal the card returns to the service-default rows.
- At 390 px the stacked card renders cleanly in dark and light (`el-admin-hosts-*-390.png`, `admin2--hosts-card-registered-1440.png`).

## 4. Mockup comparison

- **Side by side:** `mock/pair-<mockup>.png`, mockup on the left and console on the right, both at 1440 px dark.
- **Montages:** `mock/montage-390-{dark,light}-1…7.png`, every 390 px route in both themes.
- **Structural labels** (h1–h3, card titles, KPI labels, table headings, tabs): **210 of 233 found** (round 1: 206; `logs/mockup-compare.json`). The labels still missing are the mockups' sample content or deviations documented in `M4/M5-DEVIATIONS-console.md`, as in round 1:

| Mockup | Found | Missing |
|---|---|---|
| 04 | 13/14 | sample scan heading (`onboarding-uat`) |
| 05 | 23/25 | titles of closed confirm dialogs |
| 06 | 26/29 | "Export files"/"Append new rows" are not heading elements; in-place confirm dialog not opened |
| 07 | 12/17 | three-way token-join sample (counts-only privacy deviation); sample group name |
| 08 | 7/12 | bank-statement sample columns |
| 10 | 31/37 | CSS upper-case / sample sentences; CHECKPOINTS (privacy deviation) |
| 12 | 8/9 | sample heading |

All other mockups are complete.

## 5. Non-blocking notes

- **N1. Deliberate refusals.** These are the only browser console lines, all of them Chromium's own "Failed to load resource" for a deliberate refusal. The app logged nothing else and there were 0 page errors.
  - **Negatives flow** (9 events), each shown as words on the page:
    - 422 `host_not_registered` and `start_url_denied` under the Start URL, cleared on edit;
    - 404 `evidence_not_found`, `job_not_found` and `scan_not_found`.
  - **No-key first visit:** 2–5 lines of 401 per page, each page naming the missing key.
  - **Reduced-scope keys:** 403s on 64 page loads, with 0 page errors and 0 horizontal scroll.
  - **Rate-limit probe:** 19 lines of 429.
- **N2. How the console shows HTTP 429** (answers gates-r2 N3).
  - The rate-limit probe kept a dedicated key's bucket empty while the console loaded six pages.
  - What works:
    - every refusal reads value-free ("429: rate limit exceeded", "Could not load jobs: rate limit exceeded");
    - there were 0 page errors;
    - polling pages (Jobs, scan detail) recover by themselves once the bucket refills;
    - a reload restores every page.
  - Gaps:
    - **one-shot pages keep the message until a reload:** Reports, Admin, and the job detail page, which keeps a skeleton and an h1 of "— · CRM.CUSTOMERS → …" with no message;
    - several cards show their **empty state while their request was refused**: "No job group yet", "No evidence pack matches these filters", "No audit rows match…", "No API key yet. Create the first one below.", "No connection is registered for this tenant".
  - Suggestions (F4):
    - word 429 ("Too many requests from this key — retrying in N s");
    - retry once after `Retry-After`;
    - never render an empty state for a refused list.
  - The console tests' drawer walk (gates-r2 N3) should be paced; it is a test artefact, not user behaviour.
- **N3. Scope consistency (for the adaptations/security lens; outside this lens).**
  - Since fix r1, `GET /jobs` and `/reports/evidence` hide scans from a key without the `scan` scope.
  - The aggregates still include them:
    - `/reports/summary` `recent_jobs`, 3 scans;
    - `layers.ui`: 29 scans plus `last_scan`;
    - `/reports/jobs`: UI sweeps 27/2 and `scan_packs` 27 (`logs/scope-api-probe.json`).
  - So the Overview of such a key links to scan pages that answer 403. The page says "The scan could not be loaded: 403: scope 'scan' required. **Retrying.**" and `usePoll` re-requests the permanent 403 every 4 s for as long as the tab stays open.
  - The scan evidence page and `/data-sources/jobs/<scan id>` refuse cleanly ("…scope 'scan' required…").
- **N4. Reduced-scope empty states.**
  - With a detect-only key, the Jobs page says "Could not load jobs: scope 'jobs' required" correctly. Its Job groups card still says "No job group yet", while `/job-groups` answered 403.
  - This is minor and pre-existing.
- **N5. Overview Layer 3 "Last scan".**
  - When the newest scan failed, the card reads "Last scan: 0 pages, 20 personas, 0 submitted".
  - It does not say the scan failed. The 20 is the configured persona count.
- **N6. Round-1 notes, status.**
  - **Unchanged:**
    - N2: the wizard step-3 review table is 949/710 at 1440 (912/710 Oracle), and all 16 Method selects start outside the visible box;
    - N3: the scan field inventory (956–1216/818) and the job classification tables (694–737/680) scroll inside their cards at 1440;
    - N4: Parallel view "Screens" thumbnails are about 390 px;
    - N5: Overview lists all 31 entity types;
    - N6: the Documents preset selector is 456/356 at 390 px;
    - N7: on a finished scan, stepper step 4 "Parallel view" stays numbered.
  - **New and similar:** the policy version diff box is 1908/810 at 1440 and scrolls.
  - There is no clipped, unreachable content on any route.
- **N7. The 404 page still has the M0 placeholder copy.** It says "This screen arrives with its milestone (see docs/specs §11). The engine behind it is live in the Playground today." Every milestone is now built (F4 polish).
- **N8. Phone topbar.**
  - At ≤ 640 px the topbar holds the tenant field, the API-key field, "Remember key" and the theme button. It wraps to about 100 px and stays sticky.
  - It works, but it takes about 12 % of an 844 px viewport.
  - It is pre-existing (M0 settings live in the topbar; the mockups' topbar holds crumbs).
- **N9. Main has moved** (`5c11e49`, `d2fdc3f`; not in `m4`). The post-merge console walk on main must re-check:
  - Admin, a MongoDB evidence pack and wizard step 1 (round-1 N8);
  - the recognizer builder's ReDoS refusal. On this branch `(a+)+$` gets no "unsafe_pattern" message, and a direct `POST /policies/test-recognizer` with 31 characters ran **36 s**. F1 (`fsec`, S3-fix-r1 B1) already fixes this server-side, and the console already maps `unsafe_pattern`.
- **N10. Log noise this walk caused, for the other lenses' log scans.**
  - **557** `rate_limited` warnings for `key_01M4XZMWF7CJTQD990J0H57HET` (`s2con-r2-429`), 01:28:19–01:30:27 IST, deliberate.
  - **13** for `s2con-r2-walk` at 01:17–01:18 IST, from the id picker script (not the console).
  - One 36-s test-recognizer request at about 01:41 IST.
  - The **227** static-key `rate_limited` warnings of the last hour (00:45, 01:10 and 01:20 IST) are **not** this walk's: its static-key pass ran 01:32–01:37 with 0 HTTP ≥ 400.

## 6. Objects this walk left on the m4 stack

Names are prefixed `s2con-r2`/`s2con2`.

- **Tenant:** `s2con2-9dfa14`, with no host (its host was added and removed).
- **Policy:** `s2con-r2-5522e7` (v2 published, ticket `CHG-S2CON-R2`).
- **Job group:** `s2con-r2-ca2d84`.
- **Targets:**
  - `bank_uat.s2con2_syn_7f85a7` (synthesize);
  - `CRM_MASKED.S2CON2_5522E7`;
  - `crm_uat.s2con2_ca2d84`;
  - `bank_uat.s2con2_ctl_457768` (canceled run, 8,000 rows).
- **Scans:**
  - the console scan `s_01M4XZGJC9PRX011CHKC1CJ67V`;
  - the deliberate failed scan `s_01M4XZGMTZ942A0CE3Q3Z300SC`. It is one of the two failed scans behind B1; deleting it would hide the bug, not fix it;
  - the one-page session-check scans.
- **Keys:** 10 `s2con-r2-*` keys, **all disabled**.
- **Other:** several discoveries and accepted snapshots.
- Nothing was deleted.

## 7. Re-check after the fix

- **New B1:**
  - `run.sh sweeps_probe.py`: the UI failed count equals the number of listed UI failures, or both are 0;
  - `run.sh newb1_shot.py` and `run.sh phone_shots.py`: the tile, chart and card agree, and the Overview posture matches.
- **Regressions:** `walk_static.py routes-dark routes-light`, `drawer.py`, `b2_rows.py` and `aggregate.py`.
- **Keys:** mint fresh walk keys with `mkkey.py <name> <scratch-file> '*' '*'` and point `S2_KEY_FILE` at the file. All of this round's keys are disabled.
