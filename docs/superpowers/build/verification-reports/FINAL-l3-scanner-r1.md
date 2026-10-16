# FINAL — v1.0.0 candidate on `main` — L3 SCANNER lens, round 1

**Verdict: PASS. No blocking defect in the L3 scanner lens.** Every check below ran live, on the deployed `browser-worker` of the running `full` stack, at `45be8ac` (`chore(release): v1.0.0`).

- **Live scans.** The demo site was scanned in both modes, on a private and on the shared instance. All 4 scans succeeded: 21 pages, 63 fields, 3 masked captures, and 60 blocked POSTs each.
- **No submissions.** Demo site `/__received` showed **non-GET +0** on both instances. Every other counting site I served also received 0 non-GET. Stack-wide, 0 non-GET or scope-path requests were allowed, in the DB rows and in Prometheus.
- **Parallel view, both modes.** Both sides of the switch ("Synthetic persona" / "Obfuscated tokens") and both views ("Screens" / "Fields") were checked in the console for a synthesize-mode scan and an obfuscate-mode scan: **48/48 states correct**, 0 console errors, 0 HTTP ≥ 400.
- **Masked captures, OCR-checked.** 814 unique stored screens were OCR'd (demo site 268, hostile select site 546), plus 14 console screenshots.
  - The hostile select site covers all 63 closed-select, listbox and customizable-select shapes from the project's tests, scanned in both modes. Result: **0 made-up values read** (0 exact, 0 fuzzy), against a plain-screenshot control that reads 61 of 62 pages.
  - On the demo site, the only reads are the carried `application_ref` on `/apply/review` (N2, not a detected entity).
- **Deny rail.** It held against a hostile site that tries 14 different ways to reach `/deny`: the site received **0 requests under `/deny`**. The allow rail and the SPA (`pushState`) deny case also held.
- **SSRF netguard.** It held at accept time: 60/60 hostile start URLs were refused with 422, with no echo and no job queued. It also held at the scope proxy: 11 registered names that resolve to forbidden addresses gave **0 connections**, refused on both proxy paths.
- **N1, the Cookie/Referer case, is reported as non-blocking** (owner decision pending). It was re-checked live and is still present: 12/12 of the page script's pixel GETs carried a fill value in both `Cookie` and `Referer`.

| | |
|---|---|
| Date | 2026-10-16, 09:55–10:28 IST |
| Tree | `/Users/neelu/document_intelligence/data-privacy-services`, `main` @ **`45be8ac`**. No tracked file was modified, before or after, and HEAD is unchanged. One untracked dir, `docs/screenshots/v1.0.0/` (82 PNGs, created 10:10), **is not mine**: it is another verifier's console screenshots (§9, N4). I wrote nothing into the repository. |
| Stack | The gates verifier's running `full` stack: 11 services healthy and `/readyz` ready at the start and at the end. I did not restart, rebuild or take it down. What I added, all stopped or removed at the end: <ul><li>extra `node` processes inside `demo-site` on private ports 18461 and 18463–18467 (the main 8410 server was not touched);</li><li>my own tesseract container `l3scan-r1-tess` (app image, `--network none`), removed;</li><li>one `--rm` container of the browser image for P6b.</li></ul> |
| Scans I created | 23 scan jobs, all `succeeded`: P1 4, P1b 1, P3 12, P5 1, P5b 2, P8 1, P10 2. They are listed in the probe JSONs. |
| Tools | main's `.venv` (Python 3.12.13; httpx and the repo's own modules). A private Playwright venv `~/.cache/dps-build/pw-l3-scanner` (playwright 1.55.0, Chromium 1187). Tesseract 5.5.0 from `data-privacy-services:local`. |
| Values | Only counts, ids, codes, fixed probe paths and needle **names** are printed or recorded. Hostile pages use the tests' made-up values. I deleted the 5 OCR-cache texts that held a demo-site value (`application_ref` reads) after counting. |
| Probes | `~/.cache/dps-build/reports/FINAL-l3-scanner-r1/` (inventory in §10) |

## 1. Code identity (P0)

The 22 tracked files of the L3 path hash identically in the tree, in `app` and in `browser-worker`. The files are `dps/layers/scanner/*.py`, `netguard.py`, `browser_worker.py`, `routers/scans.py`, `config.py`, `posture.py`, `worker.py`, `secrets.py` and `pyproject.toml`. **0 of 22 differ.** HEAD is `45be8ac`.

## 2. Live scans of the demo onboarding site (P1)

I ran four API scans with `max_pages` 50, `max_depth` 4, deny `["/logout","/admin"]`, auth storage state `dps/ui/demo-onboarding`, 3 personas and locale `en_IN`:
- synthesize and obfuscate on a **private** instance (same files, `demo-site:18461`, counters only this probe touches);
- synthesize and obfuscate on the **shared** instance (`demo-site:8410`).

All four ran concurrently in 165 s and gave identical figures:

| | each of the 4 scans |
|---|---|
| state | `succeeded` |
| pages / forms / fields / personal | 21 / 14 / 63 / 51 |
| pages filled / masked captures | 12 / 3 |
| page artefacts | original 21, annotated 21, filled_synthesize 12, filled_obfuscate 12, masked_capture 3 (+ capture_json 3 in obfuscate mode) |
| skipped | deny 2 (`/logout`, `/admin`), other_origin 1, template 1 |
| requests (DB rows) | allowed GET document 54 / script 67 / stylesheet 54; **blocked** POST fetch 34 (`method`), POST beacon 26 (`api_removed`), GET image 1 (`host`, `tracker.invalid`) |
| `non_get_allowed` (summary, rows, evidence) | **0 / 0 / 0** |
| evidence scan block | `submissions` **0**, `submit_attempts_neutralised` 4, `scope_paths_reached` 0, `scope_routes_reached` 0, `non_get_allowed` 0 |

**Demo `/__received`:**

| Instance | Before → after | Change |
|---|---|---|
| Private (18461) | 0/0/0/0 → 0/0/0/0 for non_get / form_get / logout / admin | `by_path` `{}`; +350 GETs |
| Shared (8410) | 16/4/2/2 → 16/4/2/2 | `by_path` unchanged |

- The shared baseline is the gates' deliberate positive controls (`test_demo_site.py` / `test_no_submit.py`, FINAL-gates §D4).
- At the end of my window (10:23), after every scan of mine and of the other verifiers in that time, the shared instance still read **non_get 16, form_get 4, logout 2, admin 2**, with the same `by_path`.

## 3. Parallel view, both modes

**Through the API (P1).** Every filled page of every scan has both filled screens, `filled_synthesize` and `filled_obfuscate`, next to its `original` / `annotated` screen. The page details hold `fill_synthesize` and `fill_obfuscate` per field. All 4 scans: 12 pages × 2 sides, 0 missing.

**In the console (P4).** I used the private Playwright, the dark theme, 1440 px and the static local key. The checks covered the shared synthesize-mode scan and the shared obfuscate-mode scan, each of the 12 filled pages, and both sides, switched with a **real click**:

| Scan mode × side | states OK | screens loaded | the side's own artefact requested | Fields view values | original side |
|---|---|---|---|---|---|
| synthesize × Synthetic persona | 12/12 | 24/24 | 12/12 | 59 `.f.syn`, 0 tokens | 62 rows, only "(empty)" / "••• masked" |
| synthesize × Obfuscated tokens | 12/12 | 24/24 | 12/12 | 37 `.f.obf` + 22 `.f.kept`, 0 persona | same |
| obfuscate × Synthetic persona | 12/12 | 24/24 | 12/12 | 59 `.f.syn` | same |
| obfuscate × Obfuscated tokens | 12/12 | 24/24 | 12/12 | 37 `.f.obf` + 22 `.f.kept` | same |

- In every state, `aria-pressed` and the URL's `fill=` followed the click.
- **0 demo values in the page text** (17 needle forms) across all 48 states.
- The Captures tab loaded the masked capture of all 6 pages that have one (3 per scan). Each had 0 demo values in its text.
- Totals: 0 console errors, 0 page errors, 0 HTTP ≥ 400.
- **OCR of the 14 console screenshots** (`console_shots/`, both sides × both views of `/apply`, plus the capture tabs): 0 exact and 0 fuzzy demo values.

## 4. Masked captures, OCR-checked (incl. closed selects)

**Method.** Every PNG the API serves, plus the raw objects of the artefact store, was OCR'd by tesseract 5.5.0 (2× Lanczos, psm 11 + psm 3). Each text was matched exactly and with an edit distance of 1–2 against named needles. Every other file was text-scanned: exports, inventory, evidence JSON/HTML/PDF, capture maps, page details and events. The method is the one used by S2 / S1-M4 captures-privacy.

**Demo site (P2, 17 needle forms of the demo customer values):**

| Kind | unique PNGs (4 scans) | exact hits |
|---|---|---|
| original | 76 | 0 |
| annotated | 84 | 8 — all `application_ref` on `/apply/review` |
| filled_synthesize | 48 | 0 (1 fuzzy: N3) |
| filled_obfuscate | 48 | 0 |
| masked_capture | 12 | 8 — all `application_ref` on `/apply/review` |

- The `application_ref` reads are the carried N2 (§9).
- The 84 text files hold no customer value, apart from the 6 copies of one export that hold a synthetic company name (N3, §9).

**Closed selects — hostile select site (P3).**
- **Pages.** All 63 pages of `tests/scanner/test_capture_select_shapes.py` and `test_capture_select_drawn.py`, including their `_statement` variants, served from `demo-site` on private ports. Every value on them is made up. The shapes:
  - an empty `label=""` on the selected option (account, PAN, name, e-mail with and without a value);
  - real values that start like a placeholder (`Pick…`, `Please…`, `-- … --`), disabled and past-300 selections, and their copies in free text;
  - listboxes (`size`, `multiple`, 400 options);
  - empty or blank option values;
  - customizable selects (`appearance: base-select`) without a button, with `<button>` text and with `<selectedcontent>`;
  - a same-origin frame, a whitespace label, and the controls.
- **Scans.** 12 scans, both modes, 162 pages visited.
  - The 50-page cap, the 25-link cap and the 3-per-template budget (segments of 24+ characters template to `/{id}`) meant 25 pages were first reached only through `/{id}`.
  - Those 25 were re-served under short aliases, so **all 63 pages were scanned in both modes**.
  - Stored: annotated 162, original 162, filled_synthesize 150, filled_obfuscate 150, masked_capture 148.
  - Capture status: 49 captured and 5 `not_needed` per mode; **0 withheld**.

| Kind | unique PNGs | exact | fuzzy |
|---|---|---|---|
| original | 82 | 0 | 0 |
| annotated | 126 | 0 | 0 |
| filled_synthesize | 113 | 0 | 0 |
| filled_obfuscate | 113 | 0 | 0 |
| masked_capture | 112 | 0 | 0 |
| text files (exports, inventory, evidence, capture maps…) | 365 | 0 | — |

- **Control (P3d).** The same pages were rendered by plain Chromium (no DPS code) and OCR'd the same way. **61 of the 62 pages that hold a value read it.** The 62nd is `label_whitespace`, which by design draws nothing. A 0 in the stored screens therefore means masked, not unreadable.
- **Counting servers.** non_get 0, form_get 0 and `by_path` `{}` on all three select-site instances (362 + 52 + 204 GETs).

## 5. D4 — nothing submitted (summary)

| Counter | Result |
|---|---|
| Demo `/__received`, private instance (5 scans: P1 ×2, P1b, P5, P8) | non_get / form_get / logout / admin **0 / 0 / 0 / 0**, `by_path` `{}` |
| Demo `/__received`, shared instance | **+0** over my window (§2) |
| Select-site counting servers (12 scans) | non_get 0, form_get 0 |
| Deny-rail site (P5b, 2 scans) | 0 non-GET; every request it got is listed in §6 |
| N1 site (P10, 2 scans) | 0 non-GET |
| **Stack-wide** `dps.dps_scan_request` (all 26 scans with rows, every verifier) | allowed non-GET **0**, allowed scope-path (`deny`/`not_allowed`) **0**, blocked 684 |
| **Stack-wide** browser-worker `dps_scan_requests_total` (since start) | allowed non-GET **0**. Blocked: POST method 330, POST api_removed 258, GET deny 86, GET host 10. Allowed: GET ok 2,266. |

## 6. Deny rail

| Probe | Setup | Result |
|---|---|---|
| Demo scans (P1) | deny `/logout`, `/admin` | both skipped `deny` in all 4 scans; `logout_hits` and `admin_hits` **+0** on both instances |
| P5 allow rail | private demo, allow `/apply`, deny `/logout`, `/admin`, `/apply/card` | 7 navigated paths, all under `/apply` and none denied. Skips: deny `/apply/card`, deny `/logout`; not_allowed `/`, `/app`, `/help`, `/login`, `/profile`. Counters +0. **pass** |
| **P5b hostile deny site** (`demo-site:18464`), both modes, deny `/deny` | The page tries 14 different ways to reach `/deny`: <ul><li>anchors;</li><li>a 302 from an allowed path;</li><li>`<img>`, a redirecting `<img>` and an `<iframe>`;</li><li>`fetch` on load, plus prefetch and stylesheet links;</li><li>a `pushState` link that also fetches, and a `role=link` route that fetches;</li><li>a button that assigns `location`;</li><li>`input` handlers that fetch and load a pixel while the scanner fills;</li><li>a `pagehide` beacon and a meta refresh;</li><li>a denied GET form and a denied POST form.</li></ul> | The site received only `GET /` 4, `/ok/a` 6, `/ok/b` 6, `/ok/spa` 2, and `/r/deny/redirected` 2 + `/r/deny/img-redirect.png` 4. The last two are the allowed redirectors; their 302 hop was refused. **0 requests under `/deny`, 0 non-GET.** Guard rows per scan: blocked `deny` GET image 11, fetch 9, other 9, stylesheet 9, unrouted 3, document 2; POST beacon `api_removed` 3. Evidence `submissions` 0, `scope_paths_reached` 0, `scope_routes_reached` 1 (the handler-only `pushState` route, recorded `spa|reached|deny` by design). **pass** |
| P8 SPA deny | private demo from `/app`, deny `/app/preferences`, `/app/nominee`, `/logout`, `/admin` | 19 navigated, 0 under a denied path. `/app/nominee`, `/logout` and `/admin` skipped `deny`. `/app/preferences` (role=link, no href) recorded `spa|reached|deny` and not processed further. Evidence `scope_routes_reached` 1, `scope_paths_reached` 0, `submissions` 0. Counters +0. **pass** (same as S1-M4) |

## 7. SSRF netguard

**P6a — accept time (deployed `POST /scans`, `env=local`).** I sent 60 hostile start URLs:
- numeric encodings of loopback and of the metadata address (decimal, hex, octal, short forms);
- IPv6 forms: `::1`, IPv4-mapped, NAT64, 6to4, `fe80::`, `fd00:ec2::254`, `::`, `ff02::1`;
- `169.254.169.254`, `169.254.170.2`, `100.100.100.200`, `192.0.0.192`, `metadata.google.internal`, `metadata`, `0.0.0.0`, `0`, multicast and broadcast;
- container names, internal services and the `browser-worker` metrics port;
- `localtest.me`, `*.nip.io`, trailing dots and `%2e` / `%2d` escapes;
- credentials and authority tricks (`@`, `\@`, `#@`, `?@`), a bad port and malformed authorities;
- the `ftp`, `file`, `ws`, `gopher`, `javascript`, `data`, `chrome` and `view-source` schemes.

Result: **60/60 refused 422** (`host_forbidden` 27, `host_not_registered` 33). **0 responses echo the URL** and no scan job was queued (22 → 22).

**P6b — below the page (scope proxy + Chromium), inside a fresh `data-privacy-services-browser:local` container (`--network none`).**
- **Setup.**
  - The real `ScanGuard` and its scope proxy, Chromium with the worker's own `launch_args()`, which include `--host-resolver-rules`.
  - A raw listener on 127.0.0.1 counts connections.
  - 11 registered names resolve, through the guard's resolver, to forbidden addresses: metadata direct, IPv4-mapped, NAT64, 6to4, split with an allowed address, AWS IPv6 IMDS, Alibaba, `fe80::1`, multicast, `0.0.0.0` and broadcast.
  - Each name is loaded by a page on the allowed host as a first-hop `<img>` and through a 302, over http and https: 44 hops.
- **Result.**
  - **0 connections at the listener.** Every name was refused `host_forbidden` on both proxy paths (plain GET 2 + CONNECT 2 each).
  - The `allowed|GET|image|ok|<name>` rows are the page-level route decision, made because the name is registered. The proxy then refuses the dial. These are two layers, by design (`guard.py` `_dial_hop`).
  - Positive control: a registered name that resolves to 127.0.0.1 connects (1), since loopback is allowed in `local`.
  - Outside `local` (`allow_loopback=False`), the same name is refused on the raw proxy: plain 403, CONNECT 403, 0 connections.
  - My first pass flag wrongly counted the route rows; the corrected verdict is in `p6b_netguard_in_image.json` (`pass_corrected: true`).

**P6c — the project's own SSRF proofs, re-run on this tree** (`tests/security/test_netguard.py`, `tests/security/test_scan_ssrf.py`, `tests/scanner/test_scan_ssrf_browser.py`, with `DPS_TEST_REQUIRE_SERVICES=1`): **49 passed**. They include the browser-worker re-checking the start URL before any browser starts, which a live probe cannot reach without editing the queue.

## 8. Key custody on the scanner path and log scan

**P1b — D2.** One obfuscate scan; each field of `key_material` and `synthesis_seed` was searched by field name.
- The create answer is `Cache-Control: no-store`.
- **`key_material.dek` and `synthesis_seed.seed` appear nowhere.** The places searched:
  - every API download of the scan (exports, inventory, evidence JSON/HTML/PDF, artefacts, raw store objects);
  - every row of the `dps_scan_*`, `dps_job`, `dps_evidence`, `dps_artefact`, `dps_audit_log` and `dps_idempotency` tables;
  - the compose logs of app, worker, browser-worker and dps-db;
  - a later `GET /scans/{id}`.
- Only the public fields appear, by design:
  - `kid` in the job, the evidence and the exports;
  - `kcv` (the key check value) and `alg` in the exports.
- P1's first, coarser check counted every string leaf of 16+ characters and so flagged `kid` and `kcv`. P1b replaces it.

**P9 — logs of the window** (since 09:54 IST: app 3,459 lines, browser-worker 716, worker 169, dps-db 57): **0** demo customer values and **0** select-site made-up values, in squashed, digit and raw forms.

## 9. Non-blocking findings and notes

- **N1 — persona fill values leave filled pages in `Cookie` and `Referer` headers of GETs the page's own script makes.**
  - This is S2 captures-privacy r3 N1, carried by S1-M4. It is reported as **non-blocking: owner decision pending**.
  - Live re-check on v1.0.0 (P10, `demo-site:18467`, both modes): the page copies each typed value into a cookie and into its URL (`replaceState`), then loads a same-origin pixel.
    - 12/12 pixel GETs reached the site with a non-empty fill value in `Cookie` and in `Referer`.
    - The URL carried none. There were 0 non-GET requests and evidence `submissions` 0.
  - The values are synthetic persona values or tokens, never customer values. They do, however, reach the target's server.
  - `guard.py` still has no `Cookie`/`Referer` needle check: the only cookie access is a `context.cookies()` round-trip in `_round_trip`.
  - The CHANGELOG "Known limitations" does not mention it. If the owner accepts it, it should be listed there. The fix options are in S2-captures-privacy-r3 §N1: restore the cookies at arm, check the needles in `Cookie`/`Referer`, and strip `Referer` on filled pages.
- **N2 (carried S1-M4 N6 / S2 N8) — `application_ref` is readable on `/apply/review`** in `annotated` (= original) and `masked_capture`, in both modes and on both instances (8 + 8 reads). It is not a detected entity. Unchanged.
- **N3 (info) — a synthetic company name holds the demo customer's surname token.**
  - In one of the four scans (`private_synthesize`), persona 3's `organization` is a Faker `en_IN` company name built on a surname. That surname equals the 8-letter token of the demo customer's name, the one the surrogate docstring cites.
  - It reached the persona export (6 file copies) and the employer field of the filled screen on `/apply/employment` (1 fuzzy OCR read).
  - Scanner personas come only from the scan's seed (`persona_values`: exemplars → surrogates). They never see the page's values, so this is a coincidental draw and no disclosure.
  - Unlike `SurrogateEngine.declare_record` for records, the scanner does not redraw a persona part that equals a value on the page. That is a possible hardening, not a defect.
- **N4 (info) — an untracked `docs/screenshots/v1.0.0/` (82 PNGs, created 10:10) appeared in main's checkout during my run.** It is not from this lens: I wrote nothing into the repository. It is presumably the console lens's screenshots; its owner should commit or remove it.
- **N5 (info) — budgets met by the select site.**
  - A start page follows at most 25 links (`scan_max_spa_clicks_per_page`).
  - Path segments of 24+ characters template to `/{id}` (3 visits per template).
  - Both are expected crawl budgets. I worked around them with short aliases.

## 10. Probe inventory (`~/.cache/dps-build/reports/FINAL-l3-scanner-r1/`)

| Probe | Files | Covers |
|---|---|---|
| P0 | `p0_identity.py/.json` | code identity |
| P1 | `p1_live_scans.py/.json/.out` | live demo scans, counters, digests, downloads (`out/`) |
| P1b | `p1b_secrets.py/.json/.out` | D2 per field (`out_secrets/`) |
| P2 | `p2_ocr.py`, `p2_ocr_out.json`, `p2_ocr_out_sel.json`, `p2_ocr_out_sel2_out_sel3.json`, logs | OCR and text scan |
| P3 | `p3_build_pages.py`, `p3_server.js`, `p3_scan.py`, `p3_scan2.py`, `p3_scan3.py` (+ `.json/.out`), `site*.json`, `site*_indexes/alias.json`, `values.json` | hostile select site, both modes (`out_sel/`, `out_sel2/`, `out_sel3/`) |
| P3d | `p3_control.py/.json`, `control_shots/` | plain-screenshot OCR control |
| P4 | `p4_console_parallel.py/.json/.out`, `p4_console_ocr.json`, `console_shots/` | console parallel view, both modes |
| P5 | `p5_allow_rail.*` | allow rail |
| P5b | `p5b_server.js`, `p5b_deny_hostile.*` | hostile deny site |
| P6a | `p6a_accept_ssrf.*` | accept-time SSRF |
| P6b | `p6b_netguard_in_image.py/.out/.json` | proxy-level netguard in the image |
| P6c | `p6c_ssrf_tests.xml/.out` | the project's SSRF tests |
| P7 | `p7_counters.*` | stack-wide D4 counters |
| P8 | `p8_spa_deny.*` | SPA deny |
| P9 | `p9_logs.*` | log scan |
| P10 | `p10_n1_server.js`, `p10_n1.json/.out` | N1 re-check |
| shared | `common.py`, `needles.py`, `tess.sh` | |
