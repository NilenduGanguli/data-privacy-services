# S2 — branch `m4` after main was merged into it (CONSOLE lens verifier, round 1)

**Verdict: FAIL — 3 blocking findings, all small to fix.**

| # | Finding | Introduced by |
|---|---|---|
| **B1** | Below 1100 px the console has **no navigation at all**. The sidebar is hidden and there is no menu button or drawer, although mockup 01–12 (`vault.js`/`vault.css`) define one. At 390 px and 1024 px, Playground and Admin are dead ends: no other section can be reached from them. From the Overview, Policies and Admin cannot be reached. | Pre-existing since M0. The console stylesheet was copied from the mockups five hours before the drawer was added to them. |
| **B2** | Every UI-scan row on the **Overview** ("Recent jobs and scans") and in **Reports** ("Evidence packs") reads **`UI scan of —`**. Mockups 01 and 09 show the host, e.g. "UI scan of onboarding-uat.bank.internal · 38 pages · 12 forms · 20 personas · 0 submissions". Counts: Overview 2/2 scan rows, Reports 13/13 (17/17 with the UI-scanner filter); 0 rows name a host, 0 show forms or personas. | This merge. The M4 console wiring (`scanEvidenceRow`) was lost, and M5's summary code reads a field that scan jobs and scan packs never have. |
| **B3** | Admin › UI-scanner hosts says "**No host is registered. Scans are refused until one is.**" On this stack scans of `demo-site` are accepted and run, through the local/dev fallback `DPS_SCAN_REGISTERED_HOSTS`. The scanner setup page then says "demo-site:8410 is registered for default". Two pages contradict each other, and the Admin one is false on local/dev. | This merge (M5 Admin card vs M4 fallback rule). |

Everything else in the lens is green:

- **Walk size.** 190 console views and 605 measurements (each a width × theme), plus 53 mockup renders.
- **Errors.** 0 console errors, 0 page errors, 0 console warnings, 0 HTTP ≥ 400 and 0 failed requests, excluding the deliberate refusals described in N1.
- **Horizontal scroll.** None at 390 px. None at 1440 px either, and none on any route at 768 or 1024 px.
- **Flows.** Every interactive flow of M1–M5 completed end to end in the browser.
- **Evidence print view.** Every evidence pack (PostgreSQL, Oracle, MongoDB, copula, scan) prints at A4 width (794 px) with no horizontal scroll.

| | |
|---|---|
| Date | 2026-10-14, 21:35–22:16 IST |
| Tree verified | `dps-wt-m4`, branch `m4` @ `a0e8d26`. Clean at start and end; no repository file modified. Main's `5c11e49` is **not** in this tree (see N8). |
| Stack | m4 compose project (app 8414, demo site 8420) as left running by the gates verifier. Not restarted, rebuilt or brought down. Healthy at start and end. Other S2 lens verifiers were active at the same time (tenant `s2aprobe`, `*_s2reg1` targets). |
| Browser | Private Playwright `playwright==1.55.0`, headless Chromium 1187. venv: `~/.cache/dps-build/pw-s2-console`. Never the shared MCP browser. |
| Probes, logs, shots | `~/.cache/dps-build/reports/S2-console-r1/`. Scripts are at the top level, JSON in `logs/`, 701 screenshots in `shots/`, and pairs, crops and montages in `mock/`. |
| Privacy of the probes | Before any screenshot was taken while key material was on screen (Playground, Documents, wizard run result, Admin new key, scan key card, group dialog), the key text was blurred. Downloads were exercised with `accept_downloads=False`: the download event and file name were checked and nothing was saved. Logs hold counts, routes, codes and scrubbed texts only. |

## 1. Method

1. **Static walk** (`walk_static.py`). The 38 routes cover every `App.tsx` route plus the real ids:
   - Job details: PostgreSQL table, files and in-place; Oracle; MongoDB; a grouped Oracle job; PostgreSQL and MongoDB discoveries.
   - The scan-id redirect.
   - Scan detail: synthesize and obfuscate scans × parallel/inventory/captures tabs, both fill sides.
   - Evidence packs: 8 kinds, including both scan packs.
   - The saved-scan setup and the 404 page.

   Each route was loaded twice: once in dark at 1440 and once in light at 390. Each load was measured at both widths. Every one of the 12 mockups was rendered at 1440/390 × dark/light, and wizard mockup steps 1–5 as well.
2. **Interactive flows** (`walk_flows.py`, eight browsers in parallel). Each state was measured at 1440/390 × dark/light, with a full-page shot.

| Flow | States walked | Result |
|---|---|---|
| Playground (02) | Detect (12 findings), Obfuscate JSON (key card shown), Reveal, Synthesize (no key card, no Reveal), Text obfuscate, another preset (`analytics-safe`), hand-over to Documents | 8 views, 0 events |
| Documents (08) | Load demo KYC scan (2 pages). Redact (redacted PDF, 14 findings), page 2, Obfuscate text + reveal, Synthesize text, OCR `always`. Download redacted PDF fires `kyc-scanned.redacted.pdf`. | 8 views, 0 events |
| Policies (05) | New policy `s2con-r1-98b963` from `gdpr-pseudonymise`. AADHAAR → `redact` saved to draft. Declare `LOANS.APPLICANTS.EMPLOYER_NAME`. Recognizer live test "1 match · 1 on the allow list"; add it. Plan preview + FF1 toggle. Draft diff, then publish v2 with ticket. Version v1 and the v2 diff. YAML button. Matrix filters and search. Preset `pci-dss` page. | 14 views, 0 events |
| Wizard PostgreSQL (06 → 03 → 10) | `bank-pg-ro` (3/3 checks), `bank.customers WHERE country_code = 'IN'` (11,989 rows). Discovery (16-cell heat-map) and accept. Copy to `bank_uat.s2con_syn_3a557f`. **Synthesize** mode, review "all checks passed", Run. Seed card shown once; after "stored" no JSON stays. Live job → done → evidence, sections 1–8 incl. 6 "Synthetic fidelity and privacy". A4 print OK. | 11 views, 0 events |
| Wizard **Oracle** | `smoke-ora-src` (2/2), `CRM.CUSTOMERS WHERE SEGMENT = 'retail'`. Discovery (16 cells), accept, `CRM_MASKED.S2CON_330088`, obfuscate run (29 s). Key card once, then hidden. Job → evidence, sections 1–5, 7, 8. A4 print OK. | 11 views, 0 events |
| Wizard **MongoDB** | `smoke-mdb-src` (2/2), `profiles` with filter `{"segment":"premium"}` (3,469 documents). Discovery (17 leaf cells), accept, `crm_uat.s2con_3b5ffb`, run (13 s). Job → evidence. A4 print OK. | 11 views, 0 events |
| **Job groups** (07) | Smoke group card (3 systems, wires). Run consistency check: **passed, 3 / 3 consistent**, 3 coverage rows. Jobs search and the five filters. Export list (CSV) fires. New job group `s2con-r1-d6b639`: Done disabled until "stored", `.dpskey`/`.dpsseed` downloads fire, no key material in storage, card shown. "Add a job to this group" opens the wizard preset. | 8 views, 0 events |
| **UI scanner** (12 → 04 → 10) | Setup, Check session ("✓ Session valid · /apply returned 200 without a redirect"). Start crawl (synthesize), live map/progress/log at start, pages-ready and finish ("finished · 1:36", 21 pages, 114 log entries). JSONL download fires. Parallel view: synthetic and obfuscated sides, Screens and Fields views, page 3. Inventory (63 rows; risk-only 8). Captures (3 masked captures). Inventory and persona exports fire. Evidence: "UI scan demo-site:8410 → captures and exports", sections 1–5, 7, 8. A4 print OK. Saved-scan setup page. | 15 views, 0 events |
| **Reports** (09) | 7-day range; layers Connectors/UI scanner/API; outcomes OK/Failed (URL keeps the filters). Exact ticket search `CHG-S2CON-ORA` → 1 pack. Chart "View as table". Load 50 older rows, older packs. Audit CSV fires (`dps-audit-2026-09-30-2026-10-14.csv`). Open a pack and come back. | 9 views, 0 events |
| **Admin** (11) | Create key `s2con-r1-16a6fa` (detect): shown once, then "stored" hides it (raw key not in the DOM, and storage holds only `dps.apiKey`, `dps.tenant`, `dps.theme`). Disable dialog → disabled. Invalid host refused in the browser ("A registered host cannot be a wildcard…"). Add-tenant and edit-tenant dialogs opened and cancelled. | 8 views, 0 events |
| Job controls (extra2) | A PostgreSQL run (one partition) driven from its console page: **Pause → paused, Resume → waiting, Cancel dialog → canceled**. The evidence page and Reports row read "not verified · job canceled". Failed-scan detail (3 tabs) and setup render "Crawl failed …". | 10 views, 0 events |
| Copula pack (extra) | A measured pack from another verifier's job. Section 6 renders the fidelity table (COLUMN/METRIC/DISTANCE/VS 0.05 LIMIT/NULL RATE Δ), the correlation delta grid and the privacy cards (exact rows 0 of 20,000, NNDR 1.48, QI 0 %, MIA 0.48, utility "not run"). A4 print OK. | 2 views, 0 events |

3. **Extra probes.**
   - Inner-overflow probe (`overflow_probe.py`, `wizard_overflow.py`).
   - Navigation reachability by width (`nav_reach.py`, `flow-layout.json`).
   - No-key first visit.
   - Theme persistence across a reload: dark → light, still light after the reload.
   - A finished scan's "Stop crawl" is disabled.
   - 768/1024 px sweep of all routes: 0 horizontal scroll, 0 events.
   - Summary stability: 6 repeated calls returned identical totals.

## 2. Blocking findings

### B1 — no navigation below 1100 px (every page at 390 px)

- **What:** `styles.css:127` hides `.sidebar` at `max-width: 1100px`, and `Layout.tsx` renders no replacement.

  `nav_reach.py` counts the sections reachable through any visible link or control. At 1440 px every page reaches all 7. At **390 px and 1024 px**:

  | Page | Sections reachable | Menu buttons |
  |---|---|---|
  | `/` | 4 (Policies and Admin unreachable) | 0 |
  | `/playground` | **0** | 0 |
  | `/policies` | 1 | 0 |
  | `/data-sources` | 2 | 0 |
  | `/scanner` | 1 | 0 |
  | `/reports` | 1 | 0 |
  | `/admin` | **0** | 0 |
- **Mockups:** `vault.js` `renderSidebar` injects a `.nav-toggle` button into the topbar below 1100 px. It opens the sidebar as a drawer with a scrim, closes on Escape, and `?nav` opens it for screenshots. The CSS is in `vault.css` lines 170–179.

  The same probe on mockup 01 found 1 toggle at every width ≤ 1100 px and 7 nav items once the drawer is open. Images:
  - `mock/top-mock-01-dashboard-dark-390.png`: the mockup, with the menu button.
  - `mock/top-01-overview-dark-390.png`: the console, without it.
  - `shots/layout--mockup-01-drawer-open-390.png`: the mockup drawer open.
- **History:** the console stylesheet was created from `vault.css` @ `2462e0b` (2026-10-11 12:42), in `2994e71` at 14:51. The drawer was added to the mockups in `24e5c09` at 17:01 and was never ported.
  - The horizontal-scroll checks all earlier verifiers ran do not detect missing navigation.
  - The verification matrix requires the console to work at 390 px, and it does not.
- **Fix (small):**
  1. Port the drawer into `Layout.tsx`: a topbar `button.nav-toggle` with `aria-controls="sidebar"`, `aria-expanded` and the "Open/Close navigation" label; `.shell.nav-open`; a `.nav-scrim`. It closes on Escape, on a scrim click and on route change.
  2. Port the `vault.css` rules into `styles.css`.
  3. Add a 390 px browser test: from every top-level page, each of the 7 sections is reachable through the drawer, with no horizontal scroll while it is open.
  4. Add a Vitest render test for the toggle.

### B2 — every scan is "UI scan of —" on Overview and Reports (merge regression)

- **Evidence:**
  - `scan_rows.py`: Overview 2 scan rows, 2 with "UI scan of —". Reports 13/13, and 17/17 with `?layer=ui`. 0 rows name a host, and 0 rows show forms or personas. Mockup 09 shows "· 12 forms · 20 personas" in these rows.
  - `brief_probe.py`: `/reports/summary` `recent_jobs` scan entries have `source: null` (run entries have one).
  - Images: `mock/B2-overview-recent.png`, `mock/B2-reports-rows.png`.
- **Root cause:**
  - `dps/reports.py::_brief` sets `source=job.object_ref`, which is always `None` for a scan.
  - `_evidence_summary` reads `pack.source.object`. A scan pack carries `source.origin`/`source.url` instead (that is what `ScanEvidence.tsx` reads for the pack title), and the summary has no `forms` or `personas`.
  - The M4 console's documented Reports wiring (`M4-DEVIATIONS-console.md`, "Merge into m4": `isScanJobId`, `scanEvidenceRow` → "UI scan → captures and exports · nothing written to the scanned site") is now **dead code**. Only `ScanEvidence.test.tsx` uses it, because the M5 `EvidenceTable.packWhat` replaced it in the merge.
- **Fix (small):**
  - `_brief`: for `kind == 'scan'`, set `source` to the host of `spec.url_origin` (the redacted origin the console already shows).
  - `_evidence_summary`: `source = source.object or host(source.origin)`, plus `forms` and `personas` from `pack.scan`.
  - Console: `EvidenceTable` ui rows show "… pages · N forms · N personas · N submissions" as in mockup 09.
  - Delete or re-wire `scanEvidenceRow`.
  - Tests: an API test for both summaries on a scan, and a Vitest for the row text.

### B3 — Admin says scans are refused while the stack accepts them (local/dev)

- **Evidence:**
  - `11-admin-*`: the UI-scanner hosts card reads "0 registered · No host is registered. Scans are refused until one is." (`mock/B3-admin-hosts.png`).
  - On the same stack and tenant, the console scan of `http://demo-site:8410/apply` was accepted and finished (`s_01M4XKAXCG9FST356HW594ZJTN`).
  - The setup page then says "demo-site:8410 is registered for default". Before starting, it already hints "locally: localhost, 127.0.0.1 and demo-site" (`mock/B3-scanner-registered.png`).
- **Root cause:** `dps/layers/api/routers/scans.py::_hosts` falls back to `settings.scan_registered_hosts_list` (default `localhost,127.0.0.1,demo-site`) when the tenant has none and `env in ("local", "dev")`. The M5 Admin card (`HostsCard.tsx`) knows only `dps_tenant.registered_hosts`. The statement is right in staging/prod and false on local/dev, which is where the F3 demo runs.
- **Fix (small):**
  - When the deployment is local/dev (the Admin crumb already shows `local`), the empty row should say: "No host is registered for this tenant. A local or dev stack falls back to `DPS_SCAN_REGISTERED_HOSTS` (by default localhost, 127.0.0.1 and demo-site); staging and production refuse scans until a host is registered."
  - Better: list the effective fallback hosts, marked "service default".
  - Add a Vitest for both environments.

## 3. Mockup comparison

- **Side by side:** `mock/pair-<mockup>.png`, mockup on the left and console on the right, both 1440 dark.
- **Montages:** `mock/montage-390-{a,b,c}.png` and `mock/montage-light-1440.png`.
- **Structural labels** (h1–h3, card titles, KPI labels, table headings, tabs) of each mockup were looked up in the console views mapped to it (`compare.py` → `logs/mockup-compare.json`): **206 of 233 found**.

| Mockup | Found | Not found, and why |
|---|---|---|
| 01 dashboard | 17/17 | — (but see B2 for the scan row content) |
| 02 playground | 6/6 | — |
| 03 job detail | 10/10 | — |
| 04 scan detail | 13/14 | the mockup's sample heading (`onboarding-uat`) |
| 05 policies | 23/25 | titles of closed confirm dialogs (the console's publish dialog was walked) |
| 06 wizard | 26/29 | choice-card titles "Append new rows" and "Export files" exist in `StepTarget.tsx` but are not heading elements; the in-place confirm dialog is not opened by a copy-to-table walk |
| 07 jobs | 12/17 | the mockup's three-way token-join sample was replaced by counts-only join coverage (documented M3 privacy deviation); the sample group name |
| 08 documents | 7/12 | the mockup's bank-statement table columns (content of its sample document) |
| 09 reports | 25/25 | — (but see B2) |
| 10 evidence | 27/37 | The synthetic-variant headings are all present on the copula pack (checked on screen; `innerText` returns the CSS upper-case). "CHECKPOINTS" was replaced by a privacy-preferring line ("Checkpoint positions … are not printed here, because a key position is a customer identifier"). Other entries are the mockup's own sample sentences. |
| 11 admin | 32/32 | — (but see B3) |
| 12 scanner setup | 8/9 | the mockup's sample heading |

On visual inspection, Overview, scanner setup/live crawl, scan detail, jobs/groups, wizard, evidence, Admin and Policies follow their mockups closely in dark and light. The deviation logs (`M4-DEVIATIONS-console.md`, `M5-DEVIATIONS-console.md`) explain the remaining differences: tabs on the scan detail, value-free original side, crumb bars, UTC labels.

## 4. Non-blocking notes

- **N1.** The only browser console errors came from Chromium's own "Failed to load resource" line on responses from deliberate refusals (`flow-negatives.json`, 9):
  - 422 `host_not_registered` and `start_url_denied`, shown under the Start URL and cleared on edit;
  - 404 `evidence_not_found`, `job_not_found` and `scan_not_found`, each shown as a page message.

  A first visit with no API key behaves the same way: 2–5 such 401 lines per page and 0 page errors, with the page explaining the missing key (`extra.json`). The app itself logged nothing.
- **N2.** Wizard step 3 "Review · every column" (not in mockup 06) is 949 px wide in a 710 px box at 1440 px (Oracle: 912/710). All 16 **Method** selects start beyond the visible part of the box, so a reviewer must scroll the table sideways to change a method (`shots/wizov--pg-step3-review-1440.png`). This predates the merge (same in `m3-shots` @ `ba60834`).
- **N3.** Other tables that scroll inside their card at 1440 px: the scan Field inventory (963/818 synthetic side, 1223/818 obfuscated side; "Filled value" column cut) and the job-detail classification tables (694–737/680). At 390 px all wide tables scroll inside `.tbl-scroll`, as designed.
- **N4.** Parallel view "Screens" shows each whole-page capture about 390 px wide, which makes the form unreadable. Mockup 04 shows the form legibly. The Fields view and the Captures tab (about 900 px captures) carry the information. Suggestion: crop to the form or add click-to-zoom.
- **N5.** Overview "Findings by entity type" lists all 31 types (mockup: top 7). This makes a very tall card next to a short "Recent jobs" card.
- **N6.** The Documents page's preset selector (`.seg.sm`, 456/356 px) scrolls sideways at 390 px. The selected preset can sit out of view.
- **N7.** On a finished scan the stepper shows step 4 "Parallel view" numbered, i.e. not done, while step 5 Export is ✓. This is probably meant as "your next step". Confirm the intent.
- **N8.** Main has moved: `5c11e49` (M3 post-merge r1) changes `Admin.tsx`, `EvidencePack.tsx` (MongoDB leaf labels) and `wizard/StepConnect.tsx`, and is not in `m4`. The post-merge console walk on main must re-check Admin, a MongoDB evidence pack and wizard step 1. None of B1–B3 is touched by it.
- **N9 (outside this lens, for F1 security, not verified).** The `dev` environment (not only `local`) uses the host fallback, and it includes `localhost`/`127.0.0.1`. On a dev cluster, a tenant with no registered hosts could point the browser-worker at its own loopback. Worth a look in the netguard review.
- **Screenshot artefact (not a defect).** Full-page shots of a scrolled page draw the sticky sidebar/topbar at the scroll offset (e.g. `negatives--scan-foreign-ref-*`). `lib.check_view` scrolls to the top before later shots.

## 5. Objects this walk left on the m4 stack (all tenant `default`, names prefixed `s2con`)

- **Policy:** `s2con-r1-98b963` (v2 published).
- **API key:** `s2con-r1-16a6fa` (**disabled**).
- **Job group:** `s2con-r1-d6b639`.
- **Targets:**
  - `bank_uat.s2con_syn_3a557f` (synthesize, 11,989 rows);
  - `CRM_MASKED.S2CON_330088`;
  - `crm_uat.s2con_3b5ffb`;
  - `bank_uat.s2con_ctl_e5cb48` (canceled run, 11,000 rows).
- **Scans:**
  - the console scan `s_01M4XKAXCG9FST356HW594ZJTN`;
  - the deliberate failed scan `s_01M4XKAYW988906ETSZKQQCJK2` (`secret_unavailable`; a foreign-looking ref resolved under the caller's tenant and failed closed at load);
  - the one-page "Check session" probe scans.
- **Other:** several discover jobs and accepted snapshots (wizard and overflow probes), and wizard drafts.
- Nothing was deleted. The demo-site counters were not read here (D4 is another lens).

## 6. Re-check after the fix round

Run `walk_flows.py layout` and `nav_reach.py` (B1), `scan_rows.py` and `brief_probe.py` (B2), and the Admin static route (B3). Then re-run `walk_static.py routes-dark routes-light` and `aggregate.py` to confirm 0 errors and 0 horizontal scroll still hold.
