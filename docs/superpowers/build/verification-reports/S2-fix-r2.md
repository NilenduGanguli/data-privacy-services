# S2 — fix round 2 on branch `m4` (main merged into it)

**Verdict: all four blocking findings are fixed, test-first. Every gate is green. Committed and pushed.**

While fixing them I also fixed two more things. One is a leak of the same class: a personal `<select>` the fill chose showed the page's own option in clear in both filled screens. The other is a race in round 1's console drawer test.

| | |
|---|---|
| Date | 2026-10-15, 02:05 – 06:50 IST |
| Tree | `dps-wt-m4`, branch `m4`, `a3cd667` → **`cbbac38`** `fix(m4): S2 fix round 2 — masks follow the page wherever it is scrolled, a select is masked by what it draws, one meaning of a UI sweep`. 20 files, +1135/−133. Author `NilenduGanguli <nilendu.ganguli@gmail.com>`, no trailer. Pushed by `tools/dev/autopush.sh` (log line `06:49:39 pushed m4 cbbac38`, gitleaks clean). The worktree is clean at the end. |
| Findings fixed | `S2-captures-privacy-r2` B1 (root scroll snapping), B2 (RTL and `vertical-rl` overflow), B3 (closed select `label`) · `S2-console-r2` B1 (UI sweeps) |
| Also fixed | (1) A personal select the fill chose was readable in both filled screens. (2) The round-1 drawer walk `test_console_nav.py` failed at both widths under the per-key rate limit. |
| Stack | Lane `m4` (app 8414, demo site 8420, …). The `data-privacy-services:m4` and `data-privacy-services-browser:m4` images were rebuilt from the final tree. The stack was restarted with profiles `docs sources oracle scanner tokenizer` and **left up and healthy**: 11 services, `/readyz` ready, browser 1 online. The 6 committed `dps/` files are byte-identical in `app`, `worker` and `browser-worker` (0 mismatches). |
| Logs and probes | `~/.cache/dps-build/s2-fix-r2/`: `logs/` (numbered in run order; `first-run/` holds the misconfigured first gate run), `probes/`, `console/`, `run_gates.sh`, `run_old.sh`, `post_gates.sh`, `tess.sh`, `log_scan.py` |
| Docs | Sections "S2 fix round 2" in `M4-DEVIATIONS.md` and `M4-DEVIATIONS-console.md`; `HANDOFF.md` §16.2; contract §9 (the M5 `/reports` row); `docs/technical/api-m4.md` (captures, filled screens) |

## 1. Test-first proof

I ran the new tests against the code of `a3cd667`: a `git archive` of that commit with the new test files on top (`run_old.sh`, log `logs/05-new-tests-on-old-code.log`). **All 25 backend tests failed:**

- **`tests/scanner/test_capture_geometry.py`:**
  - the pixel test, ×6: mandatory snapping, proximity snapping, snapping set by an inline `!important`, an RTL page with a 2400 px table, `vertical-rl`, and the closed-select label;
  - the OCR test, ×6;
  - the crawler test, ×8: snapping, inline snapping, RTL and select label, each in both modes;
  - the fill-chosen select test, ×2;
  - the classification test and the extraction test.
- **`tests/test_reports.py::test_a_ui_sweep_means_the_same_on_every_reports_surface`:** the tile counted 2 failed UI sweeps and the card listed 0, because the scan that failed at setup was counted. The posture read 80 % where it should read 71.4 %.
- **Vitest** (run before the frontend change, `logs/01-vitest-new-on-old.log`): both new cases failed. The old page said "1 failed, all failed closed" next to "Failed sweeps 0 in 14 days".
- **Drawer race** (`logs/06-nav-race-old-vs-new.txt`): with the Policies list answering 1.5 s late, the round-1 walk failed exactly as in this round's first whole-suite run (`Locator.click: Timeout 30000ms`). The new walk passed.

**What the old code showed on the new pages** (OCR, `logs/04-probe-old-code.txt`, names only):

| Page | Masked capture showed | Both obfuscated captures showed |
|---|---|---|
| Both snapping pages | the same-origin frame's e-mail and PAN | the cross-origin frame |
| RTL | every value | the selected account number and the cross-origin frame |
| `vertical-rl` | every value | the selected account number |
| Select label | the label | the label |

## 2. The fixes

### 2.1 Masks are placed where the screenshot draws the values (captures-privacy B1, B2)

**Cause.** Masks were placed in "document coordinates" built in two ways:
- a client rect plus `window.scrollX`/`scrollY` (text ranges, census items);
- Playwright's viewport box, assumed to be taken at scroll 0,0 (fields, selects, swept fields, read and opaque frames, unread `FRAME` boxes, the annotated outlines).

The full-page screenshot starts at the top-left corner of the document's scrollable area. The two only agree when the page sits at that corner. Two things move it:
- **Root snapping** keeps the page at its first snap area after `scrollTo(0, 0)`. I measured `[0, 174]` on the new pages and `[0, 201]` on the verifier's.
- **RTL and `vertical-rl`**: in a document wider than the viewport, `scrollX` counts from the right edge. For a 2424 px document, the browser reported the viewport at `pageX` 1144 while `scrollX` was 0.

**Fix** (`dom.py`, `capture.py`, `models.py`):
- **Page scripts return client rects in their own frame's viewport.** This covers `TEXT_RECTS_JS`, `CENSUS_RECTS_JS` and the `EXTRACT_JS` field rects; their `top` argument is gone.
- **Python places every rect and box from where the browser itself reports the top page's layout viewport.** `dom.viewport_box` reads CDP `Page.getLayoutMetrics` → `cssLayoutViewport`: `pageX`/`pageY` within the scrollable area, plus its size.
  - A child frame's content box is its Playwright box plus that origin.
  - `FrameFacts.offset` of the main frame is the viewport box (it was zero).
  - I measured it in Chromium 1187: client + `pageX`/`pageY` is exactly where the shot draws a box. That holds for in-flow, `position:fixed` and `position:sticky` boxes, on LTR, RTL, `vertical-rl`, snapped and CSS-zoomed pages.
- **Withhold if the page moved.** Each capture reads the origin with its masks, then again just before and just after the screenshot. It withholds unless the page stayed in the same place for both measurements and the shot, because a fixed or sticky box is drawn wherever the page is when shot.
- **Defence in depth.** The capture stylesheet `_NO_MOTION_CSS` adds `scroll-snap-type: none !important` and is now idempotent. A snapping root therefore goes back to its top.
  - An inline `!important` declaration still beats it. The masks then follow the snapped page, and a test covers that case.
- **`field_boxes`** stops motion and places the outlines the same way.
- **`extract_page`** raises `PlaywrightError` when the viewport cannot be measured, as for any main-frame failure: the page is failed, and a capture withholds.
- **Not taken:** the finding's `scrollTo(-1e9, -1e9)` to find the scroll origin. Under snapping that also lands on a snap area (measured), and a page can override `scrollTo`. The browser's own metric needs neither.

### 2.2 A closed select is masked by what it draws (captures-privacy B3)

- `EXTRACT_JS` keeps `label: clean(o.label)` for each option. This is the IDL attribute: the label attribute, or the text when there is none.
- `OptionFacts.label` (default `""`) and `OptionFacts.drawn` (the label, else the text) are new.
- `classify.shown_text` and `capture._shown` now return what the select draws. The shown-value rule of `classify_fields`, `sensitive_prefilled` and the sweep therefore all see the drawn string.
- Result: a "Branch" select whose selected option is labelled with a PAN is classified `PAN_IN` and masked.

### 2.3 Also fixed: a personal select the fill chooses (same class as B3)

A fill cannot put a persona value into a select; it picks one of the page's own options. The filled screens kept every filled field readable. Take a "PAN of the payee" select whose placeholder is selected at load, so the original shows nothing: both `filled_synthesize` and `filled_obfuscate` showed the PAN the fill picked, in clear (`probes/dbg/fill_select_filled_synthesize.png`, before the fix).

`crawler._filled_screen` now keeps a select masked whoever chose its option, when `sensitive_prefilled` reports it (classified personal, or a detected value in what it draws). Inputs and textareas the fill typed into stay readable.

### 2.4 One meaning of a UI sweep (console B1, the finding's preferred option)

**Cause.** Four surfaces each counted something different:
- `jobs_report` counted every `failed`/`dead` scan (`job.scan` roll-up rows) as a failed UI sweep;
- `failed_sweeps` listed only leakage partitions and API failures;
- `Reports.tsx` mapped every failure that was not a `job` to the API layer;
- the Overview posture left scans out.

**Server** (`dps/reports.py`): one fresh query of the job and evidence tables (`_UI_SWEEPS`) now feeds the tile, the chart, the Failed sweeps card and the Overview posture. No roll-up change and no migration were needed.

| A scan that… | Counts as |
|---|---|
| succeeded | a passed sweep |
| has an evidence pack recording a verification error (today `submission_detected`: the request log held a non-GET request the guard allowed) | a failed sweep, listed in `failed_sweeps` as `{where: "scan", job_id, error_code, day}` |
| failed before it swept anything (`secret_unavailable` or a crash: no pack, or a pack without an error) | no sweep |
| was canceled | no sweep |

`FailedSweep.where` gains `"scan"`, and the posture's `sweeps_passed_pct` now counts UI sweeps.

**Console** (`Reports.tsx`, `lib/reports.ts`, `lib/types.ts`):
- A `scan` failure is listed under Layer = UI scanner and under All. It reads "UI scan s_…", links to `/scanner/scans/:id`, says what failed and carries a rose "review" chip.
- The tile says "all failed closed" only when no scan failed. Otherwise it reads "N failed, M failed closed, K scans to review", because a submission that went out did not fail closed.
- The chart's subtitle reads "completed scan" instead of "scan page", because a UI sweep is one per scan.

Contract §9 now states these rules.

### 2.5 Gate flake: the console drawer walk (round-1 test)

**What failed.** In this round's first whole-suite run, `test_every_section_is_reachable_through_the_drawer_from_every_page` failed at both widths with `Locator.click: Timeout 30000ms`: the drawer link was not visible.

**Cause.** `/policies` opens the first policy once its list call returns. That route change closes a drawer the walk has just reopened. Under the per-key rate limit (S2 gates-r2 N3), the list answered late.

**Fix.** The walk now waits until each page has landed (`/policies/<name>`), including the page each round starts from. The product is unchanged, and no check is weaker.

## 3. Gates (all on the final code; sequential, nothing else running)

| # | Gate | Result |
|---|---|---|
| 1 | `ruff format --check` / `ruff check` / `mypy dps` | 427 files formatted / all checks passed / no issues in 150 files |
| 2 | Whole suite, `DPS_TEST_REQUIRE_SERVICES=1`, tesseract wrapper | **3502 passed, 8 skipped, 0 failed** (22:17). Round 1 had 3477; this round adds 25 tests. |
| 3 | `-m "docs or ocr"` | **139 passed**, 2 skipped (host tesseract) |
| 4 | `-m "db or pg_source or s3"` | **409 passed, 0 failed** (8:51) |
| 5 | `-m "oracle or mongo"` | **162 passed**, 1 skipped (MongoDB has no composite keys) |
| 6 | `-m browser`, tesseract wrapper | **238 passed, 0 skipped** (14:18). Round 1 had 215; this round adds 23. |
| 7 | Frontend: `npm ci && npm test && npm run build` | node 44/44; Vitest 27 files, **366 passed**; build and `check-bundle` OK |
| 8 | `DPS_SMOKE_SCANNER=1 DPS_SMOKE_REQUIRE_OCR=1 tools/smoke_test.py --m3`, on the final images | **all 52 checks passed** (details below) |

Smoke details:
- the scan covered 21 pages and 63 fields (51 personal);
- 4/4 parallel-view screenshots;
- **3 masked captures, 0 withheld**;
- 60 POST and 1 GET blocked; `non_get_allowed` 0;
- M3 consistency 3/3.

The 8 whole-suite skips are all environmental or by design: 1 MongoDB capability, 2 that need a tesseract binary on the host PATH, pandas and mostlyai ×2, presidio, and the spaCy model.

My first whole-suite run was misconfigured. I set `DPS_TEST_REQUIRE_TESSERACT=1`, which turns the 2 host-tesseract skips into failures. That run also hit the drawer race. It is kept in `logs/first-run/` and is superseded by the run above.

## 4. Proofs (counts only; every value is made up or is the demo site's synthetic applicant)

### 4.1 The deployed browser-worker image, with no network

`probes/container/probe.py` ran inside `data-privacy-services-browser:m4` with `--network none`, its own Chromium 1187 and its own `dps/`. OCR ran on the host with the app image's tesseract, page segmentation modes 11 and 3.

**The round-2 pages: 75 PNGs.**
- **9 controls read every value of their page.** The fill-select controls read each payee PAN once it is selected.
- **All 66 captures read 0.** They are:
  - the masked capture and both obfuscated captures of 6 pages;
  - every screen the crawler stored (`original`, `annotated`, both `filled_*`, `masked_capture`), in both modes, for snapping, inline snapping, RTL, the select label and the fill-chosen select.
- Every capture was `captured`; 0 were withheld and 0 non-GET requests were made.

**The verifier's own round-2 pages**, copied unchanged and re-run through the verifier's own `container_probe.py` (`probes/verifier-r2/`):
- 17 pages: `r2_snap`, `_prox`, `_sections`, `_select`, `_cross`, `_field_only`, `_text`, `r2_rtl_wide`, `_table`, `_select`, `_wide_frame`, `_cross`, `r2_vertical_rl`, `r2_select_label_only`, `r2_select_pan`, `iframe_plain` and `r2_iframe_combo`. Nine of them also went through the crawler in both modes.
- **141 captures, all `captured`; every one reads e0 p0** (e-mail and PAN needles). The 17 controls read the needles, for example `r2_snap` e2 p4 and `r2_rtl_wide` e2 p4.
- In `r2_rtl_cross`, even the unmasked control now reads 0: its `FRAME` box sits on the frame, where it was 544 px off before.
- 0 non-GET requests.

**Edge probes** (`probes/zoom_probe.py`):
- cases: a CSS-zoomed root (`zoom:1.5`) with snapping kept on, an RTL page with `zoom:1.25`, a zoomed `body` with snapping, and a fixed footer on a snapped page;
- result: 0 pixels of any value's background are left, and the footer's PAN is masked where the shot draws it.

### 4.2 Live console (`console/sweeps_check.{py,json}`, private Playwright `pw-m4`, after the smoke)

**API:**
- for both 14 and 30 days: UI sweeps **28 passed, 0 failed, 0 listed**;
- the two `secret_unavailable` scans count nowhere;
- the posture reports `sweeps_passed_pct` 100.0.

**Pages:** Reports with All and with UI scanner, and the Overview, each at 1440 and 390 px.
- The tile reads "28 passed · 0 failed" and the card "0 in 14 days".
- The page errors, console errors and warnings, and horizontal scroll checks all came out clean (0, 0, none).
- Before the fix, this same stack showed "2 failed, all failed closed" next to "0 in 14 days".

### 4.3 D4 (demo site `/__received`)

- **Over the gates:** 56/12/7/6 → 72/16/9/8 (`non_get`, `form_get`, `logout_hits`, `admin_hits`). The delta, 16/4/2/2, is exactly the deliberate positive controls run twice (whole suite and browser gate), the same as in round 1.
- **Over the smoke:** every delta is **0**, and `by_path` did not change.

### 4.4 LLM and logs

**LLM:** `dps_llm_raw_pii_sent_total 0.0` before and after the smoke.

**Logs** (`logs/43-log-scan.txt`, all 15 containers):
- **0 tracebacks**; **0** demo values and **0** session-cookie hits.
- The app's warnings are 242 `rate_limited` (the console tests of the two runs, known as gates-r2 N3) and 1 deliberate `auth_rejected` from the smoke.
- The database error lines come from the negative tests: append-only and immutability triggers, RLS, permission checks, and unique and check constraints.

## 5. Notes

- **N1 — Chromium paints a cross-origin frame only where it is in view.** A full-page shot of such a frame in the left overflow of a `vertical-rl` page is blank. Its `FRAME` box is painted anyway. The test excludes that place from the control.
- **N2 — Unchanged non-blocking findings of the captures lens.** These are its N1 to N4: values sent encoded in passive-resource URLs, the persona OTP visible in the filled screens, CSS-drawn copies (`text-shadow`, `drop-shadow`, `box-reflect`), and late text in the obfuscated capture. They are outside this round.
- **N3 — The console's rate-limit noise (gates-r2 N3) remains.** 242 `rate_limited` warnings came from the console tests. The drawer test no longer fails from it, but the walk is still not paced.
- **N4 — Tracked screenshots (known A-N2).** The console tests rewrote the 54 tracked PNGs in `docs/superpowers/build/m3-shots/`. They were copied to `s2-fix-r2/m3-shots-from-run/` and restored to HEAD by explicit pathspec (`restored-shots.txt`).
- **N5 — Main has moved.** This round covers `m4` only. The m4 → main merge must rerun the §2.5 gates on the combined tree. The console's Overview label still reads "Leakage sweeps passed", as in mockup 01; it now also counts UI sweeps (deviation log).
- **N6 — State left for the verifiers.**
  - The demo-site counters are at 72/16/9/8; take a fresh baseline.
  - The smoke left one more succeeded scan (UI sweeps 28) and a smoke group `smoke-m3-<epoch>`.
  - Nothing was deleted.
