# M4 G3 review, round 3 (Tasks 9 to 13 on branch `m4`, after fix `d987144`)

Reviewer: independent review agent, 2026-10-13.

* **Working copy:** `/Users/neelu/document_intelligence/dps-wt-m4`, HEAD `d987144`.
* **Tree:** clean before and after the review. I modified no repository file.
* **In scope:**
  * the task commits `f3eb65a` (T9), `0e12190` (T10), `89b0935` (T11), `8e5e35b` (T12) and `a5ff949` (T13);
  * the fixes `216bdd6` (r1) and `d987144` (r2).
* **Independence:** I did not rely on `M4-G3-impl.md` or `M4-G3-fix-r2.md`. Every claim below comes from code I read or something I ran.
* **Probes:** in `/Users/neelu/.cache/dps-build/reports/probe-m4-G3-r3/`. They print counts only, and every value in the probe pages is made up.

## Verdict: FAIL. Two blocking findings, both captures that show a source value

All gates are green, and the r2 findings are fixed:
* B1-r2: buttons, input button labels, listbox options and 1-character nodes;
* B3: long text nodes.

The census still misses two ordinary ways a page renders a string, and OCR reads the customer value from the masked capture and from both obfuscated captures:

* **B4:** text whose parent element has `display: contents`. The commonest case is web-component slotted text with `:host{display:contents}`. `slot` fallback text is the same case, because `<slot>` is `display:contents` by default. This text is also never classified, so Tasks 9 and 10 miss it too.
* **B5:** strings the browser draws from attributes:
  * the `alt` text of a broken image, which is ordinary in a scan because the scope proxy blocks off-scope image hosts;
  * an `<optgroup label>` in a listbox.

---

## 1. Gates (I ran them)

| Gate | Result |
|---|---|
| `./lane.sh ruff format --check dps tests tools` | 278 files already formatted |
| `./lane.sh ruff check dps tests tools` | All checks passed |
| `./lane.sh mypy dps` | Success: no issues found in 115 source files |
| `./lane.sh pytest -q` (default suite) | **1904 passed, 203 skipped**, 1 warning (111 s). The browser, OCR and DB tests skip by default. |
| G3 browser and OCR suites (see below) | **183 passed, 0 skipped** (109 s) |

The G3 suites ran with `DPS_TEST_REQUIRE_SERVICES=1 DPS_TEST_REQUIRE_TESSERACT=1 DPS_TEST_TESSERACT_CMD=probe-m4-G3/tess.sh`, which runs tesseract 5.5.0 in `data-privacy-services:local` with `--network none`. The files were:
* `test_capture_census`
* `test_capture_residual`
* `test_capture_ocr`
* `test_capture`
* `test_fill_browser`
* `test_classify`
* `test_risk_flags`
* `test_personas_fill`
* `test_dom_browser`
* `test_guard`

Gate notes:
* Tasks 9 to 13 contain no SQL, so no compose service was needed. None was started, and no container of this lane is running.
* The demo site (node, port 8420) and my static probe server (127.0.0.1:8498) were stopped afterwards.
* The frontend was not touched, so the npm gates do not apply.
* The `test_ndjson` flake the fixer reported did not appear in my run.
* Outputs: `gates.txt`, `g3_integration.txt`.

**D4 re-check.** I ran `r1_fill_probe.py` (→ `fill_probe.txt`): 13 demo forms × 3 modes, 39 fills.
* Every fill: the guard was armed and then closed, the URL was unchanged, and the stored records hold 0 demo values.
* Totals: `non_get_delta 0`, `form_get_delta 0`.

## 2. Round-2 findings re-checked

| r2 finding | Status | Evidence |
|---|---|---|
| B1-r2: button, input button, listbox option and per-character copies | **fixed** | `test_capture_census` (pixel coverage, in the masked capture and both obfuscated ones) and `test_capture_ocr` (+5 OCR cases) pass. I read `CENSUS_JS`, `_sweep` (coverage rule with overlapping matches) and `_classify_census`, and they do what the deviation entry says. |
| B3: tail past 2000 characters | **fixed** | `TEXT_READ_CAP` 20000 with `truncated` set past it. `__dpsTextValues` holds the whole node. `REPLACE_TEXT_JS` rewrites a node only when it is unchanged and returns a count, and the caller withholds unless every rewrite happened. Tests pass. |
| N9: fields past `max_fields` | fixed (truncated → withheld) | `capture.py:644` and `capture.py:692`/`783` |
| N11: periodic straddle | fixed | `_sweep` coverage with overlapping matches. The test passes. |
| N8: input value set after extraction | still open, logged for Task 14 | accepted as logged |

**Other checks that pass.**
* `cv.html`: `content-visibility:auto` sections above the values. Masked, synthesize and obfuscate are all captured, and 0 values are readable.
* `svgtext.html`: an SVG `<text>` copy. The masked capture is captured with 0 readable. Both obfuscated captures are withheld as `leakage`, which fails closed (the SVG copy is in the text list but was not detected).
* `summary.html`: a copy inside a closed `<details>`. The masked capture is captured with 0 readable. Both obfuscated captures are withheld as `leakage`, which fails closed.

## 3. Conformance and the deviation log

* **Plan and contract interfaces hold.**
  * `capture_screen`, `obfuscated_capture`, `sweep_copies`, `extract_page`, `PageSnapshot` and `TextFacts` keep their names, signatures and fields.
  * `truncated` keeps its field and type, with a wider meaning. No other module reads it (I checked with grep).
  * The withheld reason codes are value-free.
  * The console lane is unaffected.
* **"G3 review round 2 — fix" entry: accepted, except one claim.** It says the census lists "every string rendered now". That is not true as built (B4, B5).
* **Accepted parts of that entry:**
  * the over-withholding from the wider concatenation (it fails closed);
  * the open items: N8, closed shadow roots, CSS `content`, `placeholder` and canvas.
* **Task 12 entry.** It already logs that text or values inside an open shadow root are not read when `scope.shadow_dom=false`, and it hands this to Task 14 ("extract with shadow DOM on for captures, or box open shadow hosts"). My probe confirms the input or select part of that is still open (N12), so I keep it non-blocking as logged. Task 14 must close it.
* **Task 9, 10, 11 and 13 entries:** I re-read them against the code and the r1 and r2 reviews. They are accepted unchanged: the ReDoS-safe pattern subset, labels as page text, persona exemplars, no page-derived fill values stored, and fills without any click or key press (re-probed in §1).
* **Programme plan §2.3 row G3:**
  * classification ✓
  * risk flags ✓
  * persona values ✓
  * fill and validation records ✓ (D4 re-probed)
  * "OCR the masked PNGs: no source value readable": the demo-site OCR suite passes, but **it fails under B4 and B5.**

## 4. Blocking findings

### B4: text under a `display: contents` parent is neither classified nor in the census (`dom.py` `_TEXT_HELPERS_JS` `visible`/`textOk`, `CENSUS_JS` `visible`)

**Cause.**
* Both predicates decide whether text is rendered with `visible(node.parentElement)`, and that test requires `el.getClientRects().length > 0`.
* An element with `display: contents` has no box of its own, so `getClientRects()` is empty, but its child text **is rendered**.
* So the extraction skips such text (no classification, no rendered-PII risk flag), and the census skips it too.
* No copy is located, and there is no split in the haystack, so nothing withholds.

**Where this happens.**
* A web component whose host is `:host { display: contents }` and slots light-DOM text. The text node's parent is the host. This is common in design-system chips, badges and name tags (Lit, Stencil).
* `<slot>` fallback text, because `slot` is `display: contents` by default.
* Framework wrapper elements styled `display: contents`.

**Probes (`probe_r3.py`).** In each page below the unmasked control reads every value. The counts are OCR occurrences of the made-up name or email.

| Page | Setup | masked `capture_screen` (with `detector`) | synthesize | obfuscate |
|---|---|---|---|---|
| `contents.html` | Name and email in a table (detected and boxed), plus a copy of each under `display:contents` | captured, **2 copies readable** | captured, **2 readable** | captured, **2 readable** |
| `slotted.html` | `<user-chip>` with `:host{display:contents}` slotting the name and the email | captured, **both readable**; `findings: 0`, so classification never saw them | captured, **readable** | captured, **readable** |

The results are in `probe_r3_all.json` and `probe_r3_slotted.json`, and the images are `contents_masked.png` and `slotted_masked.png`. In those images the table cells are boxed, and "Welcome back, <name>" and the email are in clear.

**Why it is blocking.**
* It contradicts the module docstring and the r2 deviation entry: "every string the read frames render now … every visible text node".
* It contradicts the plan rule that an unmasked image of a page rendering personal data never leaves memory.
* It contradicts programme acceptance G3.
* It also contradicts Task 9 and Task 10: rendered PII in such a component is never classified or flagged.

**Fix.**
* Decide whether a *text node* is rendered from the node itself, not from its parent's box. One way is a `Range` over the node with non-empty `getClientRects()`. Another is to climb to the nearest ancestor that is not `display:contents` and test that (`checkVisibility()`, visibility, display).
* Use the same rule in `_TEXT_HELPERS_JS.textOk` and in `CENSUS_JS`.
* Add browser and OCR tests for a `display:contents` parent, a slotted web component and `<slot>` fallback text. Each test should check that the text is classified, boxed in the masked capture and boxed in both obfuscated captures.

### B5: strings the browser draws from attributes are not in the census (`CENSUS_JS`)

**Cause.** The census lists:
* text nodes;
* visible `<option>` elements;
* `input[type=button|submit|reset]` labels.

It does not list attribute text that the browser renders:
* **`<img alt>` of a broken image.** Chromium draws the alt text in the image box. In a scan, broken images are routine, because the scope proxy and the guard refuse off-scope image hosts (CDNs). An avatar's alt is very often the customer's name.
* **`<input type=image alt>`**, the same case.
* **`<optgroup label>`** in a listbox, which is drawn as a group header.

**Probes.**

| Page | Setup | masked | synthesize | obfuscate |
|---|---|---|---|---|
| `alt.html` | Name and email in a table, plus `<img src=/no-such-avatar.png alt="<name>">` | captured, **name readable** (`alt_masked.png`) | captured, **readable** | captured, **readable** (`alt_obfuscate.png` shows the table tokenised and the alt name in clear) |
| `optgroup.html` | `<select size=4><optgroup label="<name>">` | captured, **readable** | captured, **readable** | captured, **readable** |

The optgroup case is low-likelihood. The alt case is not.

**Fix.**
* Add these to the census as element-box items (`box: true`), sweep them, and classify them when there is a detector:
  * the `alt` of every visible `img` or `input[type=image]` that renders its alt (`!img.complete || img.naturalWidth === 0`, or simply always);
  * the `label` of every `optgroup` in a visible listbox.
* Add browser and OCR tests.
* Audit for any other attribute-rendered string, and add it to the census or log it with the existing open items (`placeholder`, CSS `content`).

## 5. What I verified and found sound

* **Coverage rule in `_sweep`.**
  * Overlapping occurrences in the normalised concatenation must lie inside located copies.
  * A whole-box item covers its whole span.
  * If the character-wise `_flat` mapping disagrees with `normalise(piece)`, it fails closed.
  * The census is taken twice, before and after the shot, and the two mask sets must agree (`_same`).
* **Census-only classification** (`_classify_census`):
  * it classifies only the items that are not `known`;
  * the values it appends are swept and also drive `_swept_fields`;
  * `values` is rebuilt for each measurement, so the before and after measurements do not accumulate.
* **Obfuscated path.**
  * `REPLACE_TEXT_JS` counts rewrites.
  * The re-extraction is checked for `truncated`.
  * A detector is passed, so census-only strings and late text are classified.
  * `verify_output` withholds as `leakage` when the extracted text list holds an undetected copy (svgtext, summary).
* **Privacy hygiene.**
  * `capture.py` and `dom.py` contain no logging and no `raise`.
  * `CensusItem.text` and `TextFacts.text` are `repr=False`.
  * Withheld reasons are codes.
  * The capture JSON holds tokens of nodes with findings only. N4, kept values, is unchanged.
* **Scope of the code.** There is no SQL and no tenant state in Tasks 9 to 13, and no contract name that another lane uses changed.
* **Test quality.** The new census tests assert pixel coverage at the measured copy rectangles, and the OCR tests use a positive control that reads every value. They test the claim. They are not status-only checks.

## 6. Non-blocking findings and nits

* **N12 (logged in Task 12 for Task 14; confirmed open).** The page `shadowoff.html` is scanned with `shadow_dom=false`. An `<input value>` and a closed `<select>` inside an open shadow root show copies of the detected email and name, and those copies are readable in the masked capture and in both obfuscated captures.
  * The census descends shadow roots for text, but it does not read the values shown by fields it did not extract.
  * Task 14 must close this. Options: extract with shadow DOM on for captures; or put the shown values of every visible input, textarea or select into the census (that also closes N8); or box open shadow hosts when `shadow_dom` is off.
  * `shadow_dom` is a user switch (`jobs_models.py:278`, mockup 12), so this setting is reachable.
* **N8 (open, Task 14).** An input value set by script after extraction is not re-read. The same census-of-field-values fix covers it.
* **N1, N3 to N7, N10 (from r1 and r2, unchanged, not in scope):**
  * N1: the OCR control threshold, and the 6+ digit-run count in the obfuscated test;
  * N3: `option.text` is stored as `shown`;
  * N4: `capture_json` keeps kept values;
  * N5: unclassified pre-filled inputs are not masked;
  * N6: `age` comes from `date.today()`;
  * N7: style;
  * N10: overlapping span labels.
* **N13 (theoretical).** A JS-driven text change that flips A→B→A within the shot window, faster than about 150 ms, is unseen by the two measurements. CSS motion is stopped, but timers are not. This is unlikely for text that carries a value.
* **Open items** (listed in the deviation log, accepted): closed shadow roots, CSS `content`, `placeholder`, canvas, and the `detector=` obligation for Task 14.

## 7. Probe index (`/Users/neelu/.cache/dps-build/reports/probe-m4-G3-r3/`)

| File | Purpose and result |
|---|---|
| `gates.txt`, `g3_integration.txt` | gate outputs (§1) |
| `site/*.html` | static probe pages (made-up values) |
| `probe_r3.py` → `probe_r3_all.json`, `probe_r3_slotted.json` | OCR counts for the unmasked control, masked, synthesize and obfuscate captures: contents **leak**, slotted **leak**, alt **leak**, optgroup **leak**, shadowoff leak (N12, logged), svgtext ok or withheld, cv ok, summary ok or withheld |
| `*_control.png`, `*_masked.png`, `*_synthesize.png`, `*_obfuscate.png` | the captures (made-up values) |
| `r1_fill_probe.py` → `fill_probe.txt` | D4: 39 fills, non-GET delta 0, GET-form delta 0, 0 demo values stored |
