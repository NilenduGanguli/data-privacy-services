# FINAL — v1.0.0 candidate on `main` — CONSOLE lens verifier, round 1

**Verdict: PASS for the console lens. I found no blocking console defect.**
- I walked every console route and every flow against mockups 01–12: 262 views and 821 measurements at 1440 and 390 px, in dark and light, plus sweeps at 768 and 1024 px.
- **0 console errors, 0 page errors, 0 console warnings and 0 failed requests**, except the deliberate refusals listed in §3.
- **No horizontal scroll** anywhere. The check is strict: `scrollWidth > innerWidth`, `scrollWidth > clientWidth`, or a real programmatic `scrollLeft` > 0.

The release panel is **not** green, because the gates verifier failed (FINAL-gates-r1 **B1**: `finalize` dies for stored or inline policies).
- The console shows B1's dead runs correctly, with 0 console errors (§5). B1 itself belongs to the gates lens.
- So, as the prompt asks, **the 80 v1.0.0 screenshots are in `docs/screenshots/v1.0.0/` and are not committed** (§7).

| | |
|---|---|
| Date | 2026-10-16, 09:55–10:15 IST |
| Tree | `/Users/neelu/document_intelligence/data-privacy-services`, `main` @ **`45be8ac`** (`chore(release): v1.0.0`). HEAD is unchanged at the end. **The only change in the repo is the new untracked `docs/screenshots/v1.0.0/` (80 PNGs, 51 MB).** `git status` shows nothing else. |
| Code identity | The app serves `index-DHVfp0FH.js` and `index-DCSeEcP4.css`. The gates verifier proved these byte-identical to a host build of this HEAD (FINAL-gates-r1 §1). |
| Stack | Main's compose project `data-privacy-services`, default ports, as the gates verifier left it: 11 services healthy, `/readyz` 200 at the start and at the end. I did not restart, rebuild or bring down anything. Other lens verifiers used the stack at the same time: I saw their scans, keys `fsr1-*`, a copula run under `FINAL-SYN-R1` and 4 failed runs. |
| Browser | Private Playwright **1.55.0**, headless Chromium 1187, venv `~/.cache/dps-build/pw-final-console-r1` (with httpx and Pillow). Never the shared MCP browser. |
| Keys | I minted 7 database keys `fcr1-*` (all scopes, all tenants) with the static key, one per parallel walker, each in a 0600 scratchpad file. The admin flow created and disabled one more. **All 8 `fcr1-*` keys are now disabled** (`logs/cleanup-keys.json`), and the key files are deleted. A grep of `logs/` finds 0 key-shaped strings. The log scan found 0 log lines that hold a minted key. |
| Probes | `~/.cache/dps-build/reports/FINAL-console-r1/`. The harness is S1-M4 console r1's, copied and re-pointed, with these changes: <br>• the h-scroll test is stricter (no 1-px tolerance, plus a real `scrollLeft` probe); <br>• routes whose ids are absent are skipped and then walked in a supplementary pass; <br>• `wizard_modes.py` gained the new append key-offset check. <br>New probes: `tenants_probe.py`, `tenant_dbg.py`, `tenant_walk.py`, `cop_run.py`, `evkeys.py`, `scans_now.py`, `shoot_routes.py`, `sweep_detail.py`, `curate_v1.py`, `pair.py`, `strip.py`. Outputs: `logs/` (JSON), `shots/` (926 PNGs), `mock/` (pair images and strips). |
| Values | Logs hold counts, ids, routes, codes and scrubbed fixed texts. Screens show seeded demo rows, synthetic personas and tokens only. Key-shown-once views were blurred before the shot, and none of them is among the curated screenshots. |

## 1. Coverage

| Pass | What | Views | Result |
|---|---|---|---|
| Mockups | `walk_static.py mockups`: all 12 mockups, plus the 5 wizard steps of mockup 06, at 1440/390 × dark/light | 17 | 0 events; the mockups themselves have no h-scroll |
| Static routes | `walk_static.py routes-dark` and `routes-light`. Every console route: overview, playground, documents, policies (tenant, two presets), jobs list/group, wizard, the job page for each kind (PG obfuscate/synthesize/files/in-place, Oracle, Mongo, grouped Oracle/Mongo, canceled, three discovers, scan redirect), scanner (new/saved), scan tabs (synthesize parallel / obfuscate side / inventory / captures, obfuscate parallel and captures), reports and three filters, 13 evidence packs, admin, 404. Each at 1440 and 390. | 50 + 50 | **0 events; 0 h-scroll**; every page has its h1 except the 404 page (none, by design); 0 skeletons and 0 alerts left |
| Supplementary routes | The 6 routes whose data the fresh stack lacked: a measured copula pack and its job; a failed run's page and pack (4 failed runs had appeared from another verifier); the failed scan's tabs and setup. Each in dark and light. | 6 + 6 | 0 events; 0 h-scroll |
| Flows | `walk_flows.py`, each state measured at 1440/390 × dark/light: playground (8 states), documents (8), policies (14: create → treatment editor → draft → recognizer → plan preview → diff → publish → versions → matrix search → preset), wizard PG synthesize / Oracle / Mongo (11 each: connect → select → discover → accept → target → review → run with key shown once → stored → live → done → evidence + print A4 + downloads), groups (8), scanner synthesize (15) and obfuscate (16), reports (9), admin (8: create key, shown once, stored, disable dialog, invalid host, tenant dialogs), layout, negatives (7) | 147 | 0 events outside negatives; 0 h-scroll; 0 flow failures |
| Write modes | `wizard_modes.py`: step 4 Export files / In place / Append, their typed-confirmation dialogs (closed with Escape, nothing confirmed or run), and **the new append key offset** (1.5, then a valid offset) | 7 | 0 events; 0 h-scroll (§4) |
| Job controls | `extra2.py`: Pause → paused, Resume → waiting, Cancel dialog → canceled, all from the console job page | 4 | 0 events; 0 h-scroll |
| Demo tenant | `tenant_walk.py retail-banking-apac`: overview, jobs, reports (+failed), policies (+2 tenant policies), admin, its 3 dead runs (gates B1) and 3 discovers | 14 | 0 events; 0 h-scroll (§5) |
| Extras | `extra.py`: the copula pack and its job (+ print A4 at 794 px), first visit without a key on 5 pages, theme persistence, Stop crawl on a finished scan, and a 768/1024 h-scroll sweep of all routes. Then `sweep_detail.py 1024 768`, a re-sweep that keeps each event's details. | — | Copula: 0 events, sections 1–8, print A4 no h-scroll. Theme persists across reload. Stop crawl is disabled after finish. **0 h-scroll at 768/1024 on all 56 routes; the re-sweep has 0 events.** |
| Drawer | `drawer.py`: the navigation drawer below 1100 px from 14 pages (390/768/1024/1100/1101/1440), compared with mockup 01's `vault.js` | — | `problems: []`, 0 events |
| Downloads | `downloads_probe.py` | — | 16 buttons fire their files, the same set as S1-M4 r1. 2 "Copy as Presidio YAML" buttons copy, by design. |
| Regression repros | `repro_deleted_conn.py`, `repro_wizard_link.py` | — | §4 |

**Totals** (`aggregate.py`, deliberate refusals excluded): **262 views, 821 measurements, `hscroll_390: []`, `hscroll_1440: []`, 0 console errors, 0 page errors, 0 warnings, 0 HTTP ≥ 400, 0 failed requests.**

## 2. Against the mockups

- **Structure.** `compare.py` checks the structural labels (h1–h3, card titles, KPI labels, table headings, tabs) of each mockup against the mapped console views. **210 of 233** labels are found, identical to S1-M4 console r1 (210/233).
  - The 23 that are not found are sample data or states that the mapping does not open:
    - the mockups' sample names ("Scan s_01K7ZR… · onboarding-uat", "Oracle CRM.CUSTOMERS → CRM_SYNTH.CUSTOMERS", "Job group · crm-uat-refresh");
    - the bank-statement column names of mockup 08's sample document;
    - the publish and discard dialog titles;
    - wizard modes that the flow does not select (walked separately in §1, Write modes).
  - Per mockup: 01 17/17, 02 6/6, 03 10/10, 04 13/14, 05 23/25, 06 26/29, 07 12/17, 08 7/12, 09 25/25, 10 31/37, 11 32/32, 12 8/9.
- **Visual.** I compared the pair images (`mock/pair-0{1..6,8,10,11,12}.png`: mockup left, console right) and the 390-px strips (`mock/strip-390-{a,b}.png`). The console follows the approved layouts:
  - KPI rows, the posture ring, layer cards, findings and recent jobs (01);
  - the parallel view with original and filled panes, the risk flags and the field inventory (04);
  - presets, the treatment table and draft chips (05);
  - the stepper, heat-map, decisions and job specification (06);
  - the evidence sheet with its numbered sections and signatures (10).
  The deviations are known and intended: real data instead of the mockups' sample figures, and the M5 additions (tenant/API key bar, posture checks).

## 3. Console errors: only deliberate refusals

- **Negatives flow** (`flow-negatives.json`): 10 console "Failed to load resource" lines, each matched to the refusal it provokes:
  - 422 `host_not_registered` and 422 `start_url_denied` (scanner);
  - 422 `invalid_recognizer` (unsafe regex);
  - 404 `evidence_not_found`, `job_not_found` and `scan_not_found` (unknown ids).
  Each page shows a plain-language message. This is the same set as S1-M4 r1.
- **No key** (`extra.json` › `no_key`): 18 console 401s on Overview, Reports, Admin, Jobs and Scanner. Each page shows its alert, for example "Admin needs a key with the admin scope…" and "Could not load jobs: missing API key". There were 0 page errors.
- **One transient `requestfailed`** in `extra.py`'s 1024 sweep, with no console error. Most likely an in-flight request aborted by the harness navigating away. The detailed re-sweep of all 56 routes at 1024 and 768 (`logs/sweep-detail.json`) found **0 events**. Not a defect.

## 4. Console changes since the last console lens (`8ea220a..45be8ac`, 27 frontend files): re-checked live

| Change | Check | Result |
|---|---|---|
| A job whose connection was deleted (`4f0aac4`, `39405d5`; S1-M4 console B1) | `repro_deleted_conn.py`: create a temporary connection, run discovery, delete the connection (204), open the job at 1440 dark and 390 light | Head reads "PostgreSQL · bank.customers / Discovery · connection deleted (c_01M51F…01)"; **0 console events**, no h-scroll |
| Jobs list counts (S1-M4 console B2, `lib/paging.ts`) | Jobs page vs API | "Showing 78 of 78 jobs". The stack holds fewer than 200 jobs, so the cursor paging was not exercised here. The unit tests cover it. |
| Long names in the evidence summary (S1-M4 console B3) | 13 + 2 packs at 390 px with the strict probe. Then `resume_lead_overflow_u.py` (static key, read-only), run on the 3 packs (of 105) with the longest unbreakable names (36/35/35 characters, other verifiers' synthesize targets) at 390, 375 and 360 px. | **`scrollX` 0 and `scrollWidth` = `clientWidth` at every width.** The bold names wrap and end 49–96 px inside the viewport. In S1-M4 r1, a 38-character name overflowed by 2 px at 390 and 32 px at 360. |
| Wizard step 4 append key offset (`28130d4`, `08949db`) | `wizard_modes.py`: Append → Offset → "1.5" | Inline alert "The key offset must be a whole number above 0 (for example …)". A valid offset clears it. 0 events; 0 h-scroll at 390. |
| Evidence pack states key custody once (`423c716`) | Oracle obfuscate pack | "Key id" row shows the kid and scope only; "Key custody" row shows "Data key returned to the requesting team at job creation; DPS stored no key" once |
| Must-stay-zero figures (`7934d2a`) | Overview "Raw PII sent to LLM" tile; copula pack "Exact-row matches" | 0 → emerald "placeholders only · must stay 0"; Exact-row "0 of 20,000 · enforced". No above-zero instance exists on the stack; the unit tests cover the rose state. |
| Policies quick switch / late answers (`2df5da9`, `7bb202c`) | Policies flow (14 states, preset switches) | 0 events; the right policy shown at every step |

## 5. Gates B1 seen through the console (context, not a console defect)

- The demo tenant `retail-banking-apac` holds 3 runs `dead` / `finalize_error` (Oracle, Mongo and PG; group `crm-uat-refresh`).
- In the console, each job page shows:
  - the head with the engine, objects, policy `retail-kyc`, purpose and shared consistency key;
  - the stepper through Run 100 % · 4 partitions and 6 Evidence;
  - the alert "Error code finalize_error. PrepareFailure finalize_error";
  - **Retry**, and a disabled "Evidence pack (when finished)";
  - "Evidence so far: rows protected 20,000 · leakage sweep 0 hits · verifier passing".
- 0 console events and 0 h-scroll, at 1440/390 × dark/light.
- What the console says matches the API. The fix belongs to B1.
- In the worker log, the 4 `finalize_failed` errors in my window are other verifiers' stored-policy runs, the same B1.

## 6. Logs (`log_scan.py` since 04:25 UTC; `logs/log-scan.json`)

| Service | Lines | Tracebacks | Warnings / errors | Lines with an `fcr1` key |
|---|---|---|---|---|
| app | 2,995 | **0** | 377 `auth_rejected`, 17 `rate_limited` | 0 |
| worker | 113 | **0** | 4 `finalize_failed` (B1, other verifiers' runs) | 0 |
| browser-worker | 569 | **0** | 0 | 0 |

- **`auth_rejected`.** 346 of the 377 fall in 04:29–04:30, on routes such as `/connections/x`, which this walk never calls. They are another verifier's scope and tenant probes. About 17 are this walk's deliberate no-key visits (04:35–04:38).
- **`rate_limited`.** These are on `/api/v1/synthesize` and similar routes. My walkers logged no 429.

## 7. v1.0.0 screenshots: **in place, NOT committed**

`docs/screenshots/v1.0.0/` holds 80 PNGs (51 MB): 20 views × {1440, 390} × {dark, light}, named `<NN>-<page>-<width>-<theme>.png` like `docs/screenshots/m5`.

| Mockup | Views |
|---|---|
| 01 | `01-overview` |
| 02 | `02-playground`, `02-playground-synthesize` |
| 03 | `03-job-oracle`, `03-job-synthetic` |
| 04 | `04-scan-parallel`, `04-scan-captures-obfuscated` |
| 05 | `05-policies-draft` |
| 06 | `06-wizard-discover`, `06-wizard-review` |
| 07 | `07-jobs` |
| 08 | `08-documents` |
| 09 | `09-reports` |
| 10 | `10-evidence-obfuscate`, `10-evidence-synthetic` (the measured copula pack of this lens's run `CHG-FCR1-COP`), `10-evidence-synthetic-surrogates`, `10-evidence-scan` |
| 11 | `11-admin` |
| 12 | `12-scanner-setup`, `12-scanner-live` |

- No key-shown-once view is among them.
- They stay untracked because the panel did not pass (gates B1).
- After B1 is fixed and the panel is green, the release step can commit them with an explicit pathspec, or re-shoot them with `curate_v1.py` after a fresh walk.

## 8. Non-blocking notes

- **N1.** The B1 job page shows the raw exception class: "Error code finalize_error. PrepareFailure finalize_error". Its "Evidence pack (when finished)" label is shown for a `dead` job, which will never finish without Retry. Both are wording only, and B1's fix removes the case.
- **N2.** The negatives step "foreign secret ref" behaves as designed (`dps/secrets.py`: a ref is relative to the tenant's folder, and `secret_ref_foreign_tenant` applies only when the first segment names an existing tenant folder):
  - `other-tenant/dps/ui/demo-onboarding` is accepted;
  - the scan then fails with `secret_unavailable` (`s_01M51FK16Y…`), and no secret is loaded.
  
  This is the same as S1-M4 r1 and S2 r3, and it belongs to the security lens.
- **N3.** The Overview chip reads "1 degraded" because `/readyz` reports NER as patterns-only (by design on this stack). Mockup 01 shows "All layers ready". The chip is truthful.
- **N4.** The `layout` flow's theme toggle reverts on reload in that flow only. The harness writes `dps.theme` on every load. `extra.py` (c), which writes no theme, shows the theme persists.
- **N5 — state this walk left on the shared stack** (test data, like earlier lenses):
  - target tables `bank_uat.fcr1_*` and `CRM_MASKED.FCR1_*`, and Mongo collections `fcr1_*`;
  - tenant policy `fcr1-8e079d` (v2 published) and one job group;
  - 2 scans, plus the failed negatives scan;
  - one canceled run;
  - the temporary connection `fcr1-tmp-994f03`, created and deleted.

## 9. Re-run

From `~/.cache/dps-build/reports/FINAL-console-r1/`:
1. Mint keys: `S2_KEY=static ./run.sh mkkey.py fcr1-<n> <scratch>/<n>.key '*' '*'`.
2. Pick ids: `./run.sh ids.py`.
3. Run the walks in parallel with `./bg.sh <label> <n> <script> …`:
   - `walk_static.py mockups|routes-dark|routes-light|supp-dark|supp-light`;
   - `walk_flows.py playground documents policies admin | wizard-pg wizard-mdb | wizard-ora groups | scanner scanner-obf | layout negatives reports`;
   - `wizard_modes.py`, `extra2.py`, `cop_run.py`, `tenant_walk.py`, `extra.py`, `drawer.py`, `downloads_probe.py`, `repro_deleted_conn.py`, `sweep_detail.py 1024 768`.
4. Then `compare.py`, `aggregate.py`, `log_scan.py <since> <keydir>`, `curate_v1.py` and `cleanup_keys.py`.

About 20 minutes on this stack.
