# S2 — branch `m4` after main was merged into it — captures-privacy lens, round 2

**Verdict: FAIL — three blocking findings (B1, B2, B3).**

Round 1's B1 (frames) is **fixed**. Text in a same-origin frame is now masked where the frame draws it, with
the frame bordered, padded, scrolled or nested, and when the fill scrolls the frame. I verified this with
direct captures, with the crawler and inside the deployed browser-worker image. The live demo scans are clean,
and so are their inventory, exports, evidence, reports, logs and database rows. Key custody holds.

A hostile-page sweep found three new masking defects. All three are in M4 code that is about to be merged.
None is a regression of fix round 1, and the demo site triggers none of them.

| | Defect | What it exposes |
|---|---|---|
| **B1** | **Root scroll snapping.** `scroll-snap-type` (`mandatory` or `proximity`) on the document keeps the page off scroll 0 after the capture's `scrollTo(0,0)`. Every box Playwright measures is then taken as if the page were at 0,0. | `original`, `annotated` and both `filled_*` show pre-filled fields and same-origin frame text. A pre-selected select and a cross-origin frame are also readable in the **obfuscated `masked_capture`**. |
| **B2** | **RTL or `vertical-rl` documents wider than the viewport.** `scrollX` 0 is the right edge, but the full-page shot starts at the left edge of the overflow. | **Every** mask (text, fields, frames, selects) is shifted left by the overflow width. |
| **B3** | **A closed `<select>` draws its selected option's `label` attribute.** The census, the sweep and the shown-value classification read only the option's text. | The drawn value is never classified or masked, in any capture, `masked_capture` included. |

Two non-blocking gaps sit outside the masking itself:
- **N1:** persona fill values do leave the page when a page script encodes them into a passive resource URL (base64, hex or reversed). Plain values are blocked.
- **N2:** the persona OTP is visible in the stored filled screens, although the docs say it is never stored.

| | |
|---|---|
| Date | 2026-10-15, 01:15–01:55 IST |
| Tree | `dps-wt-m4`, branch `m4` @ **`a3cd667`**. `git status` was clean at the start and is clean at the end. **No repository file was modified.** |
| Stack | The gates verifier's running lane stack (profiles docs, sources, oracle, scanner, tokenizer). I did not restart or rebuild it and never ran `down`. It is still healthy, `/readyz` reports ready, and the browser worker is online. |
| Code identity | All 157 tracked `dps/**` files and `pyproject.toml` have the same sha256 in the worktree and in the `app`, `worker` and `browser-worker` containers (`logs/00-code-identity.txt`). Playwright is 1.55.0 in both the lane venv and `browser-worker`, with Chromium 1187. |
| Probes / outputs | All under `~/.cache/dps-build/reports/S2-captures-privacy-r2/`: `live/` (API scans, downloads, OCR, logs/DB/reports, key and fill custody), `hostile/` (pages in `site/`, `make_r2_site.py`, direct, crawler and container probes, shots, summaries), `control/` (OCR positive control) and `evidence/` (key images). |
| OCR | The app image's tesseract 5.5 (`docker run --network none`). Each image is upscaled 2× (LANCZOS) and read in two passes (`--psm 11` and `--psm 3`). A count of 2 means one readable copy, seen once per pass. For the live scans there is also a fuzzy pass (edit distance ≤ 1, or ≤ 2 for 12+ characters). |
| Values | Only counts and needle names were printed. Hostile pages use made-up values (`zebulon.quixotic@example.org`, `AQXPZ4821K`). No customer value, key, seed or cookie was printed or logged. |

## 1. Round 1's B1 (frame masks): fixed

**Round 1 cases on HEAD** (`hostile/probe_captures_all.log`, 46 cases, masked plus both obfuscated captures).
Every frame case reads **0/0**, against controls that read 2–4. These include:
- the fragment-scrolled iframe and the scrolled-only one;
- the 40 px border and the 50/60 px padding;
- `transform: scale(2)`, which is now painted over whole;
- the scrolled iframe form.

The rows that still read values are exactly round 1's documented N2 classes and N1 (§6).

**Geometry** (`hostile/geometry_check.txt`). For plain, bordered, padded and scrolled frames (`scrollY` 730), the mask now
sits at a constant +4 px from the text's element box. In round 1 it was off by −36, −46 and +734.

**New frame cases** (`hostile/all_r2_direct_summary.txt`). Each was run masked and in both obfuscated modes, against an unmasked control.
**All 0**:

| Group | Cases |
|---|---|
| Combined geometry | bordered + padded + fragment-scrolled iframe; the same as `<object>` and as `<embed>`; nested scrolled and bordered frames two levels deep; an iframe in a scrolled `overflow:auto` panel |
| Framesets | `rows`, `cols` with `border`/`marginwidth`, plain |
| Transforms and zoom | `transform: translate()`; the `translate:` property; `perspective` + `translateZ` and SVG `foreignObject` with `viewBox` scaling (both **painted over whole**: `flat=False`); CSS `zoom` on an ancestor (painted whole); `zoom` or `transform` on the **child's** root |
| Scroll and position | an RTL child scrolled to its far end; `position:fixed` text in a scrolled child; `content-visibility:auto` ancestor; `loading=lazy`; fixed, sticky and modal-`<dialog>` iframes |
| Height | iframe at `100vh` and at `100%`. Their controls show that the full-page shot does not resize the viewport, so cutting masks to what a frame shows drops nothing that is drawn. |
| Drawn strings inside a bordered, padded, scrolled frame | `alt` of a broken image; an input-button label; a placeholder; a listbox option `label`; an `<optgroup>` label; `display:contents` text; SVG text; open-shadow text |

**Crawler** (`ScanRunner` + `MemoryScanSink`, both modes, `hostile/all_r2_runner_summary.txt`).
Every stored screen reads **0** — `original`, `annotated`, both `filled_*` and `masked_capture` — in these cases:
- `iframe_fill_scroll`; the fill scrolls a bordered and padded iframe (`r2_fill_scroll_border`); and a nested one (`r2_fill_scroll_nested`);
- `iframe_scrolled_form`, `iframe_scrolled_only`, `iframe_padding`, `iframe_border`, `iframe_scaled`, `r2_iframe_combo` and `r2_nested_scrolled`.

The unmasked controls read 2–4. `evidence/r1B1_fixed_fill_scroll_border_filled_synthesize.png` shows the EMAIL and PAN_IN boxes
exactly over the values in a frame the fill scrolled. A root with `scroll-behavior: smooth`, where the fill scrolls the page, is also 0.

**Deployed image** (`hostile/container_probe.py`). This ran in `data-privacy-services-browser:m4`: Linux, its own Chromium, `--network none`.
`iframe_plain` and `r2_iframe_combo` read **0** in the masked capture, both obfuscated captures and every stored crawler screen. The controls read 2–4 (`hostile/ocr_container_shots.txt`).

## 2. Live scans of the demo site through the API (deployed browser-worker)

| Scan | Mode / start | Outcome |
|---|---|---|
| `s_01M4XZBVAABQM6QACPNFJY2TZQ` | synthesize, `/apply`, 25 pages, depth 3, 3 personas, demo session ref | succeeded. 21 pages, 63 fields (51 personal). 3 captured / 0 withheld. Blocked POST 60 + GET 1; `non_get_allowed` 0. |
| `s_01M4XZBVAHNTQM60RZ8GABR0NP` | obfuscate, same spec | identical counts, plus 3 `capture_json` |
| `s_01M4XZNMTF1RNZCY3ST3Z0N1CD` | synthesize with a caller-supplied seed (fill custody) | succeeded. 21 pages. Blocked POST 60 + GET 1; `non_get_allowed` 0. |
| `s_01M4XZR84FZXDMJ1MTEKDF5G8D` / `…R84PH1TD18372DNRRA0E` | obfuscate / synthesize, `/profile`, 6 pages (key custody) | succeeded |

**Downloaded:** everything the API serves for the main pair:
- scan, pages, page details and events;
- inventory JSON/CSV and export JSON/CSV;
- evidence JSON/HTML/PDF;
- every page artefact;
- the raw store objects (`docker compose cp`, read-only).

All 73 + 76 store objects are **byte-identical** to a served download.

**OCR** (`live/analyze_live.{log,json}`): 67 + 67 unique PNGs (138 + 138 files), in both modes.

| Kind | Exact hits | Fuzzy |
|---|---|---|
| `original` (19) | **0** | none |
| `filled_synthesize` (12) | **0** | none |
| `filled_obfuscate` (12) | **0** | none |
| `annotated` (21) / `masked_capture` (3) | only `application_ref` on `/apply/review` (known N9, not a detected entity) | none |

**Positive control** (`control/ocr_control.txt`): plain full-page shots at 1280×900, read with the same OCR and needles.

| Page | Needles found | Total reads |
|---|---|---|
| `/profile` | 15/17 | 34 |
| `/profile/contact` | 7/17 | 14 |
| `/apply/review` | 8/17 | 20 |

**Text artefacts: 24 + 18 files, 0 hits.** They are inventory JSON/CSV, export JSON/CSV, evidence JSON/HTML and the PDF text
(2588 and 2623 characters extracted), 3 `capture_json` reveal maps, page details, events and the scan detail.

**Hostile inventory check** (`hostile/meta_runner.txt`). The made-up values sat in these places on one page:
- the page title and the query of the scanned URL;
- a form action and a link;
- a label, a placeholder, a `title` and an `aria-label`;
- select options;
- an `aria-describedby` error text.

Every stored row (pages, fields, requests, events, non-PNG artefacts) had **0** hits in both modes. The page's
greeting name ("Welcome Zebulon Quixotic") is not detected with the stack's patterns-only NER. That is a detection limit, not
a masking one (N7).

## 3. Logs, database, reports, metrics, key custody

- **Values** (`live/values_logs_db.txt`): line by line and row by row, after all my runs.
  - All 15 container logs: **0** demo values and **0** session-cookie (`demo-valid`) hits.
  - `dps_audit_log` (2895 rows), `dps_scan_page` 587, `dps_scan_field` 1392, `dps_scan_event` 2465, `dps_scan_request` 1633,
    `dps_job` 360, `dps_evidence` 181 and `dps_artefact` 1923: **0**.
  - `dps_llm_raw_pii_sent_total 0.0`.
- **Reports** (`live/reports/`): `/reports/summary`, `/reports/jobs`, `/reports/evidence` and `/evidence` had **0** value hits and 0 cookie hits.
  The new scan rows name only `host:port` (`reports.scan_host`).
- **D2 key custody** (`live/secret_check.log`): the DEK and the seed of both scans had **0 hits** in 15 logs, 9 tables
  (`dps_job_secret` included) and every download and store object.
  - `kid` appears in the job and evidence rows and in the downloads; `kcv` in the reveal hints. Both are by design.

## 4. Persona fill values

- **Network (D4):** the demo site's `/__received` stayed at **+0** for `non_get`, `form_get`, `logout_hits` and `admin_hits`, and `by_path` did not change.
  - This held over the main pair (01:18:55–01:20:14) and over the fill scan (01:24:16–01:25:35).
  - The guard refused 60 POST and 1 GET; `non_get_allowed` 0.
  - `/logout` and `/admin` are `skipped` in all my scans.
- **Storage** (`live/fill_check.log`). The seed was caller-supplied, so I computed every persona attribute locally.
  - Persona values appear **only** in `dps_scan_field`, the export JSON/CSV with their store objects, and page details. That is by design.
  - They appear **0** times in the other 32 of 38 corpora: all logs, requests, events, pages, audit, job, evidence, artefact rows, the inventory, events and the evidence JSON/HTML/PDF.
  - `credential`: 0 anywhere. `otp`: 0 as a stored field value.
- **Two gaps (non-blocking, §6):**
  - **N1:** encoded values leave through passive resources.
  - **N2:** the OTP is visible in the filled screens.

## 5. Blocking findings

### B1 — Root scroll snapping: masks measured by Playwright are shifted by the scroll the page keeps

**What happens.** `measure()` and `_full_page_png()` run `SCROLL_JS` (`window.scrollTo(0,0)`) and **ignore its result**
(`capture.py:560`, `capture.py:677`). The page is then assumed to be at 0,0:
- `_element_box` (`capture.py:220`, "the page is scrolled to 0,0") takes field, select and swept-field boxes;
- `frame_geometry(frame, (0.0, 0.0))` (`capture.py:325`) takes the content box of read frames and the border box of opaque frames;
- `_frame_box` (`capture.py:235`) takes the FRAME box of unread frames;
- `field_boxes` (`capture.py:700`) places the annotated overlays.

All of these are Playwright viewport coordinates. The full-page screenshot, however, is in document coordinates.

With **CSS scroll snapping on the root**, `scrollTo(0,0)` snaps back to the first snap position (`html { scroll-snap-type: y mandatory | proximity }`, plus any header, banner or notice above the first `scroll-snap-align` area). Every one of those boxes then lands that many pixels too high. Main-frame text is not affected, because it adds `window.scrollY`.

**Measured** (`hostile/snap_geometry.txt`): after `SCROLL_JS` the scroll is `[0, 201]` (mandatory and proximity, 150 px header), or `[0, 99]` (a 48 px "UAT environment" bar).
- Field mask − document box: **−201 / −99**.
- Frame content box: −201.
- Unread FRAME box: −201.

**Proof (OCR):**

| Case | Direct capture: masked / synthesize / obfuscate | Crawler, both modes |
|---|---|---|
| `r2_snap` (prefilled PAN field + same-origin frame) | **e2 p4** (= control) / 0 / 0 | `original` and `annotated` e2 p4; `filled_*` e2 p2; `masked_capture` 0 |
| `r2_snap_prox` (`proximity`) | **e2 p4** / 0 / 0 | same as `r2_snap` |
| `r2_snap_sections` (realistic: notice bar + `min-height:100vh` sections) | **p2** / 0 / 0 | `original` and `annotated` p2 |
| `r2_snap_select` (pre-selected sensitive select) | **p2 / p2 / p2** | **every screen p2, `masked_capture` included** |
| `r2_snap_cross` (cross-origin frame) | **e2 p2 / e2 p2 / e2 p2** | **every screen e2 p2, `masked_capture` included** |

The same results appear in the deployed browser-worker image (`hostile/ocr_container_shots.txt`). The controls without snapping
(`r2_select_pan`, `iframe_plain`) are 0. Images: `evidence/B1_snap_original_masks_201px_high.png` (all three masks 201 px above the
values) and `evidence/B1_snap_cross_origin_masked_capture.png` (the FRAME box above the cross-origin frame's e-mail and PAN in a stored obfuscated capture).

**Why it is realistic.** `html { scroll-snap-type: y mandatory }` with full-height sections is the canonical CSS Scroll Snap pattern.
It is used for step-by-step onboarding and "full page" layouts, nearly always below a site header. `proximity`, the softer variant,
behaves the same. The defect predates round 1. `_element_box` and `_frame_box` are unchanged by `a3cd667`.

**Fix (do not weaken any check):**
1. In `_NO_MOTION_CSS`, which every capture injects before measuring, add `scroll-snap-type: none !important` (covering `html` and `body`).
   Apply the same before `field_boxes`.
2. Read `SCROLL_JS`'s `[x, y]` in `measure()` and in `_full_page_png()`. Either map every Playwright box with the actual scroll:
   `_element_box` + (x, y), `frame_geometry(frame, (x, y))` and `_frame_box` + (x, y), as `extract_page` already does. Or **withhold**
   when the page is not at 0,0. The screenshot is document-based, so the two coordinate spaces must be joined explicitly.
3. Tests, failing first:
   - pixel and OCR tests with root `mandatory` and `proximity` snapping under a header, covering a pre-filled sensitive input, a pre-selected sensitive select, same-origin frame text and a cross-origin frame. Assert masked + both obfuscated captures read 0, against an unmasked control.
   - a crawler test (`r2_snap*.html` in `hostile/site/` can be copied; its values are made up). Assert `original`, `annotated`, `filled_*` and `masked_capture` read 0.

### B2 — RTL / `vertical-rl` documents wider than the viewport: every mask is shifted left by the overflow width

**What happens.** In a document whose root is `dir=rtl` (or `writing-mode: vertical-rl`) and wider than the viewport:
- the scroll origin is the **right** edge (`scrollX` 0, minimum `−(scrollWidth − clientWidth)`);
- Playwright's full-page shot starts at the **left** edge of the overflow.

All mask coordinates come from client rects + `scrollX` (`TEXT_RECTS_JS`, `CENSUS_RECTS_JS`) or from Playwright boxes. All of them are therefore
`minScrollX` pixels left of what the shot draws.

**Measured** (`hostile/rtl_geometry.txt`): mask.x − drawn.x is **−1144**, **−344** and **−586** for:
- an RTL page with a 2400 px table;
- an Arabic statement page with a 1600 px table (`r2_rtl_table`);
- a `vertical-rl` page.

An LTR page with overflow measures 0.

**Proof (OCR):**

| Case | Direct capture: masked / synthesize / obfuscate | Crawler |
|---|---|---|
| `r2_rtl_wide` | **e2 p3** (control e2 p4) / 0 / 0 | `original` and `annotated` e2 p3; `filled_*` e2 p2 |
| `r2_rtl_table` | **p2** (prefilled PAN) / 0 / 0 | `original` and `annotated` p2 |
| `r2_rtl_select` | **p1 / p1 / p1** | `masked_capture` p1, others p1–p2 |
| `r2_rtl_wide_frame` | **e2 p2** / 0 / 0 | — |
| `r2_vertical_rl` | **e1 p1** (= control) / 0 / 0 | — |

The deployed image reproduces them (`hostile/ocr_container_shots_rtl.txt`). In `r2_rtl_cross` the FRAME box is shifted 544 px, and the frame's
right part ("…c@example.org") shows in the control (`shots_r2/r2_rtl_cross_control.png`).
Image: `evidence/B2_rtl_wide_original_half_scale.png`.

**Why it matters.** Arabic, Hebrew and Urdu UIs set `dir=rtl` on `<html>` or `<body>`. A wide table that makes the document scroll
sideways is ordinary in back-office and statement screens. Nothing fails closed: the before/after measurements agree.

**Fix:** measure the scroll origin, for example `scrollTo(-1e9, -1e9)` then read `scrollX`/`scrollY` (both 0 for LTR/top-down) before `scrollTo(0,0)`.
Map every rect to shot coordinates as `client + scroll − min`: text ranges, census, fields, frames and FRAME boxes. As an alternative, withhold
when the root's scroll origin is not top-left. Tests: an RTL page with a 2400 px table, a field, a select and a cross-origin frame, and a
`vertical-rl` page. OCR reads 0 in every capture and every crawler screen, with a positive control.

### B3 — A closed `<select>` draws its selected option's `label`, which nothing reads

**What happens.** A closed select draws the selected option's **`label` attribute** when it has one.
- `EXTRACT_JS` keeps only `text` per option (`dom.py:216`).
- `classify.shown_text` (`classify.py:371`) and `capture._shown` (`capture.py:391`) use `o.text`.
- `CENSUS_JS` skips `select` subtrees (`dom.py:412`). It reads option labels only for listboxes.

So a value drawn only as a closed select's label is never classified, swept or masked.

**Proof:**
- `r2_select_label_only`: "Branch" select, selected `<option label="AQXPZ4821K">x</option>`. The PAN is readable in the masked capture **and in both
  obfuscated captures** (p2 / p2 / p2, `evidence/B3_closed_select_option_label.png`). The crawler's `original`, `annotated` and `filled_*`
  read p1–p2; it stores no `masked_capture` here (`not_needed`). The deployed image reproduces this.
- Round 1's `select_label` stays 0, because that select is classified personal.

This is the attribute-drawn-strings class (M4 verify r1 B5) that the lens bar names. The census docstring claims a closed select's shown option "is
swept as a field", but it sweeps the wrong string. The likelihood is low: `label` on `<option>` is uncommon. The fix is small:
- extract `label: clean(o.label)`;
- make `shown_text` and `_shown` return what a closed select draws (`option.label`);
- add tests: a direct OCR test and a classification test.

## 6. Non-blocking findings

- **N1 — Persona fill values leave the page encoded in passive-resource URLs.** `hostile/fill_exfil.txt`, `ScanRunner`, both modes.
  - The page's own scripts set `img.src`, `<link href>` and `background-image` with the typed value.
  - The plain `/px?e=<value>` requests were **all refused** (`filled_value`).
  - The **base64** (`/bg`), **reversed** (`/css`) and **hex** (`/hex`) forms **arrived**: 6 + 6 + 6 requests, each carrying a value. There were 0 non-GET.
  - The guard allows passive types on filled pages (`PASSIVE_TYPES`, `guard.py:160`). `_readings` (`guard.py:467`) decodes only percent-encoding.
    Yet the Task 6 deviation blocks script requests precisely because values may travel "encoded (base64, hashed)".
  - Only DPS-generated values can leave this way, and only to in-scope hosts. A realistic route is a first-party, same-origin
    analytics collector that sends form-field values as base64 in GET image beacons (Snowplow-style `ue_px`).
  - Suggested fix: decode base64/base64url and hex runs in `_readings`. Optionally refuse post-`arm` passive requests whose query string was not seen before
    `arm`. **Owner / D4 lens decision.**
- **N2 — The persona OTP is stored in the filled screens.** The live `/login` `filled_synthesize` shows the one-time code in clear
  (`evidence/N_live_login_filled_synthesize_otp.png`); the password shows as dots.
  - `api-m4.md` (lines 245–247) and the `personas_fill.py` docstring say `credential`/`otp` "are never stored or exported".
  - The value is synthetic. Either box credential and OTP fields in the filled screens, or reword the claim.
- **N3 — CSS-drawn copies.** These copies are readable in `original` and `annotated`; the obfuscated captures are clean.
  - A far-offset `text-shadow`: `r2_text_shadow`, masked e2, `evidence/N_text_shadow_copy.png`.
  - `filter: drop-shadow` on a transparent iframe: `r2_drop_shadow`, masked e2 p2.
  - `-webkit-box-reflect` draws a mirrored copy, which OCR does not read.
  - Suggested fix: add `text-shadow: none !important; -webkit-box-reflect: none !important` to `_NO_MOTION_CSS`, and list drop-shadow under the known limits.
- **N4 (round 1 N1, carried) — late text in the obfuscated capture.** In `react_rewrite`, the crawler's `masked_capture` shows the e-mail and PAN that the
  page's MutationObserver adds, in both modes; `original` and `annotated` are clean. This limit is not in `api-m4.md`'s known limits.
- **N5 (round 1 N2) — now documented.** These still read in all captures, as expected, and are now listed in `api-m4.md` as "Not yet masked (known limits)":
  - closed shadow roots (script and declarative);
  - CSS generated content (`attr()` and literals);
  - canvas text;
  - an input value set by script after extraction.
- **N6 (round 1 N3, the known N9) — unchanged.** `application_ref` is readable on `/apply/review` in `annotated` and `masked_capture`, in both modes.
- **N7 — expected.** A name in free text ("Welcome Zebulon Quixotic") is not detected with the stack's patterns-only NER. This is detection recall, not masking.
- **N8 — record notes.**
  - My unguarded OCR control (GET page loads, no guard) made the demo flush drafts on page hide: +4 `POST /api/draft`, 52 → 56.
    The "+4 later" that round 1 attributed to other verifiers came from round 1's identical control.
  - Another verifier's scan `s_01M4Y00N76J25XY37YMJRCAXQB` (01:30, `deny: []`) fetched `/logout`: `logout_hits` 6 → 7, page status `redirected`.
    That was outside my windows; it belongs to the D4/scope lens.
  - Final counters: 56/12/7/6.

## 7. Probe inventory (all under `S2-captures-privacy-r2/`)

- **`live/`**
  - `run_scans.py` and its log, `analyze_live.py` and its JSON;
  - `fill_check.py`, `secret_check.py`, `values_logs_db.py`;
  - `reports/`, `out*/`.
- **`hostile/`**
  - `make_r2_site.py` and `site/`: round 1's 51 pages plus 70 new `r2_*` pages (cases and their inner frames);
  - `probe_captures.py` (round 1 cases) and `probe_captures_r2.py`, which adds frame diagnostics;
  - `probe_runner*.py` (crawler);
  - `container_probe.py` (deployed image, `--network none`);
  - `snap_geometry.py`, `rtl_geometry.py`, `geometry_check.py`;
  - `fill_exfil_runner.py`, `meta_runner.py`;
  - `ocr_dir.py`, `summ.py`, `summ_runner.py`;
  - `all_r2_direct_summary.txt`, `all_r2_runner_summary.txt`;
  - `shots_r2/`, `runner_shots_r2/`, `container_shots*/`.
- **`control/ocr_control.*`**
- **`evidence/`**
- **`logs/00-code-identity.txt`, `logs/received_final.json`**
