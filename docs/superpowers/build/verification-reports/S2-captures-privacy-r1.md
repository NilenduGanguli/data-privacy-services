# S2 — branch `m4` after main was merged into it — captures-privacy lens, round 1

**Verdict: FAIL — one blocking finding (B1).** The live demo-site scans are clean, and so are their
inventory, exports, evidence, logs and database rows. Key custody holds, and persona fill values
stay on the page. But a hostile-page sweep through the product's own capture code and crawler found
one real masking defect. **Text rendered inside a same-origin iframe is masked in the wrong place**
when the iframe's document is scrolled (a `#fragment`, or **the scanner's own fill scrolling an
iframe form**), when the iframe has a border wider than 2 px or padding, or when it is CSS-transformed.
The stored `original`, `annotated`, `filled_synthesize` and `filled_obfuscate` screenshots then show
those values in clear (OCR and visual proof below). The defect predates the merge: `dom.py` and
`capture.py` are byte-identical to lane verification r3. It is in the M4 code that is about to be
merged into main.

| | |
|---|---|
| Date | 2026-10-14, 21:40–22:20 IST |
| Tree | `dps-wt-m4`, branch `m4` @ `a0e8d26`. `git status` was clean at the start and is clean at the end. **No repository file was modified.** |
| Stack | The gates verifier's running lane stack (profiles docs, sources, oracle, scanner, tokenizer). It was not restarted, rebuilt or taken down. |
| Code identity | The sha256 of `capture.py`, `dom.py`, `classify.py`, `crawler.py`, `guard.py`, `personas_fill.py`, `inventory.py`, `engine/verify.py`, `detect/patterns.py` and `observability.py` is identical in the worktree, the `app` container and the `browser-worker` container. So the in-process probes run exactly the deployed code. |
| Probes / outputs | `~/.cache/dps-build/reports/S2-captures-privacy-r1/`: `live/` (API scans, downloads, OCR, log/DB/secret/fill checks), `hostile/` (probe site, direct-capture and crawler probes, shots, geometry), `control/` (OCR positive control), `tess.sh` (app-image tesseract, `--network none`), `needles.py`. |
| OCR | 2× LANCZOS upscale, then two passes (`--psm 11` and `--psm 3`). Needles are normalised (letters/digits/@). A count of 2 = one readable copy (once per pass). There is also a fuzzy pass (edit distance ≤ 1, or ≤ 2 for 12+ chars) for misreads. |
| Values | Only counts and needle **names** were printed. No value, key, seed or cookie was printed or logged. |

## 1. Live scans of the demo site through the API (deployed browser-worker)

| Scan | Mode / start | Outcome |
|---|---|---|
| `s_01M4XK0MEN7YNN6KJAYYEC8F64` | synthesize, `/apply`, 25 pages, depth 3, 3 personas, demo session ref | succeeded; 21 pages, 63 fields, 51 personal; 3 captured / 0 withheld; blocked POST 60 + GET 1; `non_get_allowed` 0; evidence `submissions` 0; policy `gdpr-pseudonymise` v1 |
| `s_01M4XK0MEW50R127HMAC7JAEXS` | obfuscate, same spec | identical counts, plus 3 `capture_json` |
| `s_01M4XK5YFBQZQZ6SN4PKF7P211` / `s_01M4XK5YFKTV9Y411C65246R0V` | obfuscate / synthesize, `/profile`, 6 pages (key-custody run) | succeeded |
| `s_01M4XKKMDXAP0VQJ8NVY38R9GA` | synthesize with a caller-supplied seed (fill-custody run) | succeeded; 21 pages; blocked POST 60 + GET 1; `non_get_allowed` 0 |

**Downloaded:** for each main scan, everything the API serves:
- scan, pages and page details (fields and requests), events;
- inventory JSON/CSV and export JSON/CSV;
- evidence JSON/HTML/PDF (`/evidence/{id}/pdf` rendered it once);
- every page artefact;
- the raw objects of the artefact store for each job (`docker compose cp`, read-only).

**Every stored object (73 and 76) is byte-identical to an artefact the API serves.** No hidden or unmasked image is stored.

**OCR of every PNG** (`live/analyze_live.{py,log,json}`):

| Scan | Unique PNGs | original+annotated | filled_synthesize | filled_obfuscate | masked_capture | Fuzzy |
|---|---|---|---|---|---|---|
| synthesize (main) | 67 (138 files) | 0, except `application_ref` on `/apply/review` | **0** | **0** | 0, except `application_ref` on `/apply/review` | none |
| obfuscate (main) | 67 (138 files) | same | **0** | **0** | same | none |
| both key-custody scans | 16 + 16 | 0 | 0 | 0 | 0 | none |

The `application_ref` read is the known **N9**: it is not a detected entity, and it is unchanged by the merge.
Every other needle (name, name tokens, DOB in two digit orders, Aadhaar, PAN, card, e-mail and
local part, mobile, address and its parts, account, customer id) read **0**.

**Positive control** (`control/ocr_control.txt`): plain, unmasked full-page shots of the same pages at
the scanner viewport (1280×900) were read with the same OCR and needles.

| Page | Needles found | Total reads |
|---|---|---|
| `/profile` | **15/17** | 34 |
| `/profile/contact` | 7/17 | 14 |
| `/apply/review` | 8/17 | 20 |

**Text artefacts:** 24 + 18 files had **0** hits. They are:
- inventory JSON/CSV;
- export JSON/CSV;
- evidence JSON/HTML and the PDF text (2588 and 2623 chars extracted);
- 3 `capture_json` reveal maps;
- page details, events and the scan detail.

The demo session cookie has 0 hits in all downloads.

## 2. Logs, database, metrics and key custody

- **Values** (`live/values_logs_db.txt`). I scanned line by line and row by row, after all my runs, so values could not be joined across lines.
  - All 15 container logs of the project: **0** demo values and **0** session-cookie hits.
  - Tables, all with **0** hits: `dps_audit_log` 1450 rows, `dps_scan_page` 359, `dps_scan_field` 895, `dps_scan_event` 1588, `dps_scan_request` 1038, `dps_job` 177, `dps_evidence` 93, `dps_artefact` 1185.
  - `dps_llm_raw_pii_sent_total 0.0`.
- **D2 key custody** (`live/secret_check.{log,json}`). The DEK and the seed of both runs were kept in process memory only.
  - **0 hits** in the 15 logs, 9 tables (including `dps_job_secret`, which is empty after finalize), every download and every store object.
  - `kid` appears in the job and evidence rows, the exports and the capture maps. `kcv` appears in the reveal hints. Both are by design: identifiers, not secrets.

## 3. Persona fill values never leave the page (`live/fill_check.{log,json}`)

The fill-custody scan used a caller-supplied seed, so I computed every persona attribute locally,
including `credential` and `otp`. The computed values match the scan's export, so the check is valid.

**Network:**
- Demo-site `/__received` was **+0** for `non_get`, `form_get`, `logout_hits` and `admin_hits`, and `by_path` did not change. This held across the fill scan and across the main pair's window (16/4/2/2 before and after).
- The counter later moved to 20 non-GET. That happened after my windows and comes from other verifiers' positive controls.
- The guard refused 60 POST and 1 GET; `non_get_allowed` 0.

**Storage:** persona values appear **only** where they belong by design:
- `dps_scan_field` (the fill record);
- the export JSON/CSV and their stored objects;
- page details.

They appear **0** times in all 15 logs, `dps_scan_request`, `dps_scan_event`, `dps_scan_page`, `dps_audit_log`, `dps_job`, `dps_evidence`, `dps_artefact`, the inventory JSON/CSV, the events and the evidence JSON/HTML/PDF.

**Credentials:** `credential` has **0** hits anywhere. `otp` appears **0** times as a stored field value.

## 4. Hostile pages through the product's capture code

**`hostile/probe_captures.py`** follows the crawler's pipeline:
- `extract_page` with iframes and open shadow DOM;
- `classify_fields`, `sensitive_prefilled` and `classify_rendered`;
- `capture_screen` masked, and `obfuscated_capture` in both modes, in a guarded context at 1280×900.

All values are made up. Each cell is "e-mail/name reads / PAN reads": 2 = one readable copy, 4 = two copies.

| Case | Control | masked | synthesize | obfuscate |
|---|---|---|---|---|
| **iframe_scrolled** (`src=…#target`) | 4/4 | **2/2** | 0/0 | 0/0 |
| **iframe_scrolled_only** | 2/2 | **2/2** | 0/0 | 0/0 |
| **iframe_border** (40 px) | 4/4 | **0/2** | 0/0 | 0/0 |
| **iframe_padding** (50/60 px) | 4/4 | **0/2** | 0/0 | 0/0 |
| **iframe_scaled** (`transform: scale(2)`) | 4/4 | **2/2** | 0/0 | 0/0 |
| iframe_plain / default (2 px inset) / srcdoc / nested same-origin / object / embed / late iframe | 2–4 | 0/0 | 0/0 | 0/0 |
| iframe_cross / nested cross-origin (boxed `FRAME`) | 2/2 | 0/0 | 0/0 | 0/0 |
| contents (`display:contents`) / alt / placeholder / input button / select `label` / `::marker attr()` / svg text (with and without anchor) | 2–4 | 0/0 | 0/0 | 0/0 |
| fixed footer / sticky header / 100vh hero / vh shell / content-visibility / CSS zoom / transform on text | 2–4 | 0/0 | 0/0 | 0/0 |
| prefilled MIXED_PII (contact value next to a channel) / FREE_TEXT textarea (the merge's `classify_fields`) | 2/0 | 0/0 | 0/0 | 0/0 |
| css_attr (`::before{content:attr()}` copy) | 4/2 | 2/0 | 2/0 | 2/0 |
| css_attr_only / css_literal_only | 2/2 · 2/0 | 2/2 · 2/0 | same | same |
| closed shadow (script) / closed declarative shadow root | 2–4 | 2/2 | 2/2 | 2/2 |
| canvas text | 2/0 | 2/0 | 2/0 | 2/0 |
| late_input (value set by script after extraction, N8) | 2/0 | 2/0 | 2/0 | 2/0 |
| late_text (text added between extraction and capture) | 2/2 | **0/0** | 2/2 | 2/2 |

**Crawler-level confirmation (`hostile/probe_runner.py`).** This runs `ScanRunner` with `MemoryScanSink` (the browser-worker's code), both modes, plus an unmasked control. Every PNG it stores was OCR'd.

| Case | Values readable in the stored PNGs |
|---|---|
| iframe_scrolled_only | e-mail and PAN in `original` and `annotated`, both modes |
| iframe_padding / iframe_border | `original` and `annotated`: the PAN in both; the e-mail token in the padding case. In the border case the e-mail is fully visible in the image (`hostile/shots/iframe_border_masked.png`), but OCR missed it next to the misplaced box |
| iframe_scaled | e-mail and PAN in `original` and `annotated` |
| iframe_scrolled_form | e-mail and PAN in `original`, `annotated`, **`filled_synthesize` and `filled_obfuscate`**, both modes |
| **iframe_fill_scroll** | an ordinary same-origin iframe card form whose field is below the iframe fold, with the page's own e-mail and PAN rendered above that field. **The scanner's own fill scrolls the iframe**, so both parallel-view screens show both values (`runner_shots/iframe_fill_scroll_synthesize_product_filled_synthesize.png`) |
| react_rewrite | the obfuscated `masked_capture` shows a new e-mail and PAN; `original` and `annotated` are clean. See N1 |

In every row the obfuscated `masked_capture` is clean, except react_rewrite. The probe server received 0 non-GET requests.

**Geometry** (`hostile/geometry_check.txt`). For text in the child frame I compared the mask the capture
code places (`dom.text_rects`) with the browser's own box of that text's element:

| Iframe | Mask offset (y) | Expected | Notes |
|---|---|---|---|
| plain | +4 | +4 | aligned |
| 40 px border | −36 | +4 | mask moved 40 px up and left |
| 50/60 px padding | −46 | +4 | mask moved up 50 px and left 60 px |
| scrolled (`scrollY` 730) | +734 | +4 | mask 730 px below the text |
| scaled ×2 | — | — | mask height 30 against a text height of 78 |

## 5. Blocking finding

### B1 — masks of text inside same-origin child frames are misplaced (scrolled, bordered/padded or transformed iframes): the stored screenshots show the values

**What happens.** For every read child frame, the capture maps the frame's rects into the page incorrectly, in three ways:

| Problem | Code | Effect |
|---|---|---|
| The frame's own scroll is added | `dom.py:361` (`TEXT_RECTS_JS`) and `dom.py:483` (`CENSUS_RECTS_JS`) add the evaluated frame's `window.scrollX/scrollY`. For a child frame that is its own document scroll. But the frame offset already maps the child's **viewport**. | Masks move by the iframe's scroll. |
| The offset is the border-box | `dom.py:680` (`_frame_offset`) and `capture.py:227` / `capture.py:281` (`_frame_box` / `_fresh_frames`) use `frame_element().bounding_box()`, the **border-box**. A child viewport starts at the content box. | Masks move up and left by border + padding. Chromium's default 2 px inset border is absorbed by the 2 px mask pad, which is why the existing tests pass. |
| Transforms are ignored | No transform on the frame element is taken into account. | A scaled iframe's masks are unscaled and misplaced. |

**Where it shows.** `capture_screen` is used for the `original`, `annotated` and both `filled_*` screens.
`masked_capture` is spared for the text it rewrites, but not for the copies its census boxes in frames.

**Why it is realistic.**
- Same-origin iframes are read by default (`scope.iframes = true`).
- A fragment-scrolled document viewer, or an iframe form taller than its frame, is ordinary bank UAT.
- `iframe_fill_scroll` needs no special page behaviour at all: the scanner's own fill scrolls the iframe.

The demo site is unaffected: its card-widget iframe renders no personal text, which is why every gate and the live scans pass.

This breaks `api-m4.md`'s "original: … personal data it renders masked" and the module rule that boxes are measured "child-frame offsets included".

**Fix (do not weaken any check):**
1. Measure child-frame rects in the child's **viewport** coordinates: no `window.scrollX/Y` for child frames. Alternatively, scroll every read child frame to 0,0 before measuring and shooting, as the main frame is.
2. Map them through the iframe's **content-box origin**: border-box plus `clientLeft + paddingLeft` and `clientTop + paddingTop`, composed for nested frames. Keep the border-box for boxing unread frames.
3. For a read frame whose element has a non-identity transform, or whose content box differs from the child's `innerWidth/innerHeight`, **fail closed**: withhold, or box the whole frame as `FRAME`.
4. Failing tests first:
   - OCR and pixel tests in `test_capture_census.py` / `test_capture_ocr.py` for: a fragment-scrolled iframe, a 40 px border, 50/60 px padding, and `transform: scale(2)`;
   - a crawler-level test, like `test_every_screen_the_crawler_stores_holds_no_readable_value`, with an iframe form below the fold, so that the fill scrolls the iframe. Assert 0 reads in `original`, `annotated` and both `filled_*`, with an unmasked positive control.

  The probe pages in `hostile/site/` (`iframe_*.html`, `inner_*.html`) can be copied as fixtures; their values are made up.

## 6. Non-blocking findings

- **N1 — `obfuscated_capture` never classifies text that appears between its snapshot and its own re-extraction.**
  - **Repro:** a page whose `MutationObserver` reacts to DPS rewriting a text node by adding a new e-mail and PAN (`react_rewrite.html`).
    - Crawler, both modes: `masked_capture` reads the new e-mail and PAN (2/2 and 2/2), while `original` and `annotated` are clean.
    - Direct probe with a 1.8 s window (`late_text`): masked capture 0/0, but both obfuscated captures 2/2.
  - **Cause:** `after = extract_page(...)` (`capture.py:787`) becomes the reference, and the census classifies only nodes it does not know (`capture.py:554`). New or changed text in `after` counts as classified even though it never was. `capture_screen` handles the same case through `late_texts`.
  - **Severity:** the natural timing window in the crawler is tens to hundreds of ms. A timer-driven feed did not leak (`late_feed`: `quixotic` 0), so I rate it non-blocking. It does contradict the documented "fails closed when … a page that changed under the capture".
  - **Fix (about 10 lines):** after `REPLACE_TEXT_JS`/`SET_VALUES_JS`, call `late_texts(ext)` on the original extraction. `REPLACE_TEXT_JS` keeps `__dpsTextValues` in sync, so only genuinely new or changed text comes back. Run `classify_rendered` on it and add the hits to `values` (swept and boxed), or withhold. Add a test with the MutationObserver page. Worth folding into the B1 fix round.
- **N2 — known open census gaps, reproduced on HEAD.** All are readable in the masked capture and in both obfuscated captures:
  - closed shadow roots, script-created and declarative;
  - CSS generated content, `content: attr()` and literal strings: a copy of a detected value, and the only copy;
  - canvas text;
  - an input value set by script after extraction (N8).

  They are documented only in `M4-DEVIATIONS.md` (Task 12, G3 r2, B5 audit). The user-facing `docs/technical/api-m4.md` ("original … personal data it renders masked", "a capture fails closed"), HANDOFF and README do not list them. Add them to `api-m4.md` and the CHANGELOG "Known limitations" (F4).

  Cheap mitigations, all in `CENSUS_JS`/init script:
  - pseudo-element `content` strings (`getComputedStyle(el, '::before'|'::after'|'::marker').content`) as box items, which the census sweeps and classifies;
  - an init script that records closed shadow roots (patching `attachShadow`), so extraction and census can walk them, or withhold when one renders text;
  - boxing visible canvases on pages with findings;
  - re-reading input values at measure time (N8).
- **N3 — N9 carried.** `application_ref` is readable on `/apply/review` in `original`/`annotated` and in `masked_capture`, in both modes. It is not a detected entity, and main's new label-only patterns do not cover "Application reference". It is unchanged from M4 verification r3.
- **N4 — by design, recorded for the record.** The store's object key ends in `.png` for `capture`-kind JSON reveal maps (`ARTEFACT_EXT` by kind). The served `content_type` is correct. This is cosmetic.

## 7. What the merge changed in this lens (checked)

- `classify_fields`, main's mixed-PII rules, now decides `sensitive_prefilled`. MIXED_PII and FREE_TEXT pre-filled fields are masked in all three captures (0/0).
- The detectors main changed (`patterns`, `names`, `structural`) did not lower recall on any demo page: 0 reads apart from N9, against the control's 15/17.
- `verify.py` changed only its coverage statements. `normalise`, `collect_originals` and `verify_output`, which the captures use, are unchanged in behaviour (live leakage sweeps 0; no capture withheld).
- SEC-2/SEC-5: the demo storage state resolves under the tenant folder, and the cookie value appears in no log, row or artefact.
- The M5 evidence PDF of a scan is value-free (text extracted and scanned). The evidence names `policy.version` "1".
