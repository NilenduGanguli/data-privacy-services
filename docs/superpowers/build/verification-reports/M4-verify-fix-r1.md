# M4 lane verification fix, round 1 (branch `m4`, worktree `dps-wt-m4`)

Input: `/Users/neelu/.cache/dps-build/reports/M4-verify-r1.md` (FAIL on HEAD `fabbd81`, five blocking findings).
Probes and outputs: `/Users/neelu/.cache/dps-build/reports/probe-m4-verify-fix-r1/`.

## Result

All five findings are fixed. Each fix was written test first: the new tests failed on `fabbd81` and pass now. All gates are green. There are four commits on `m4`, with author NilenduGanguli <nilendu.ganguli@gmail.com> and no trailers:

| Commit | Finding |
|---|---|
| `2a167eb` fix(m4): arm registers the lock-at-birth script before listing pages and locks late popups until none appear | F1 (D4/D-C race) |
| `80782c1` fix(m4): short dates with any separator (_ , : + ~ \|, Devanagari digits) are redacted in URLs and templates | F2 |
| `ccf3ba1` fix(m4): text under display:contents parents and slots is read and censused; alt, optgroup, option label and placeholder strings are boxed | B4, B5 |
| `6ccecae` fix(m4): a click whose own handler ran a refused route is recorded as reached and counted, never as skipped | R3-B1 |

Resume check: at the start, the tree was clean apart from the verifier's untracked `docs/screenshots/m4/`, and there was no earlier fix report. I left `docs/screenshots/m4/` untouched and uncommitted.

## Fixes

### F1: `ScanGuard.arm` race (`dps/layers/scanner/guard.py`)

On the first arm, `arm` now registers `LOCK_JS` with `add_init_script` before it does anything else. Only then does it list the open pages, mark them filled and lock them. After that it runs a bounded loop:
1. One round trip: `page.evaluate("0")`, or `context.cookies()` if the page cannot evaluate. This makes sure every page event Chromium sent earlier has reached `context.pages`.
2. Mark and lock every page that is not locked yet.
3. Stop when a pass finds no new page.

The loop runs at most `_SETTLE_PASSES = 50` times. If pages are still appearing after that, `arm` returns False and `locks_failed` is incremented, so the caller does not fill (fail closed). The `arm` docstring and the module docstring ("Locked documents") are updated.

Tests (`tests/scanner/test_guard.py`):
- `test_https_popups_opened_while_arm_runs_relay_nothing` runs on an HTTPS counting server with the verifier's race page:
  - Unguarded control: at least 10 upgrades carrying the base64 value.
  - Guarded, run 3 times: armed, 0 upgrades, 0 `/leak-*` paths, workers refused and counted `api_removed`.
  - Before the fix: guarded runs failed, for example with "upgrades 1 == 0".
- Two tests with fake pages and contexts, no browser:
  - The init script is registered before the page list is first read, and every late page is marked and locked (limits 1, 5 and 40).
  - A context whose page list never stops growing makes `arm` return False.
- Stability: 5 runs of the 4 cases, 15 of 15 guarded runs clean.
- The verifier's own probe `g1_blank_popup_worker.py`: 5 guarded runs, each `armed True locks_failed 0` with 0 upgrades. The unguarded control had 59 upgrades (`g1_out.txt`).

### F2: short dates (`dps/layers/scanner/urls.py`)

- `_DATE_SHAPE` is now `(?<!\d)\d{1,2}[\W_]\d{1,2}[\W_]\d{2,4}(?!\d)`.
- `_PUNCT` is now `[\W_]{0,3}`.

Tests (`tests/scanner/test_urls.py`) cover 17 date forms × 2 classifiers (None and `Detector(en_IN)`):
- The redacted URL, the template, hash routes, query keys and the `PageRecord` guard all hide them.
- `template_is_value_free` refuses the raw forms.
- Dates split over segments are fully redacted: `/dob/1/5_90`, `/1_5/90`, `/1:5/90`, `/12/may_90`, `/12_may/90`.
- Routes and versions stay readable: `v1_2`, `x86_64`, `1_2_3`, `c++/1:2`.

### B4: text whose parent has no box of its own (`dps/layers/scanner/dom.py`)

A shared rule, `_RENDERED_JS`, decides whether a text node is rendered. It is true when:
- the parent element is visible, or
- the parent is not `display:none` or hidden but has no client rects of its own (`display:contents`, a `<slot>`, a component host), and a Range over the text node itself has client rects.

The same rule is used by `textOk` (extraction and late text) and by `CENSUS_JS`.

### B5: strings the browser draws from attributes (`CENSUS_JS`)

These are now box items: they are swept, and classified when the capture has a detector.
- the alt text of a visible `<img>`;
- the alt text of `<input type=image>`;
- an `<optgroup>` label (its box covers the whole group, options included);
- an option's `label` attribute, in addition to its text;
- the placeholder of an empty `<input>` or `<textarea>`.

The `capture.py` module docstring is updated.

Tests for B4 and B5:
- 11 new `CENSUS_PAGES` cases: contents, slotted, slot_fallback, contents_only, slotted_only, alt, alt_only, input_image, optgroup, option_label, placeholder. `INJECT_JS` now uses `setHTMLUnsafe`, so declarative shadow roots attach, and it waits for images to load or fail.
- Pixel tests on the masked capture and on both obfuscated captures (`test_capture_census.py`).
- OCR tests with tesseract (`test_capture_ocr.py`):
  - The unmasked control reads every value, and the second copy too.
  - The masked capture and both obfuscated captures read none.
- Extraction and census tests (`test_dom_browser.py`). These include hidden twins that must stay out: `display:none`, `visibility:hidden`, and fallback text of a slot that has assigned content.

### R3-B1: click handler reaching a denied route (`dps/layers/scanner/crawler.py`)

The discovery click and the replay click (in `_navigate`) first mark the main frame's document with a random token in a non-enumerable property. After the settle, `_reached_refused_route` checks the URL the tab landed on. If its scope verdict is not ok and the tab still shows the same document, the click ran a same-document navigation: a hash route or `pushState`. In that case the crawler:
- writes a value-free `spa`/`reached` event (redacted target, note = the verdict) and does not call `_consider` or `_skip`;
- counts it in `RunOutcome.scope_routes_reached`;
- stops processing that tab:
  - discovery moves on to the next clickable;
  - a replay raises `_NavFailed("scope_reached")`, so the route's row is `failed`/`scope_reached`.

The counter appears in:
- the evidence `scan` block (`scope_routes_reached`);
- the printable HTML;
- the console evidence page, as a "Never-visit routes" check;
- the navigation log, worded by result: `reached` is shown in rose, and refused clicks read "not clicked · …".

The `_click` docstring is corrected. The module docstring and `docs/technical/api-m4.md` describe the event and the counter.

Tests (`test_scope_rail.py`) use counting servers:
- The onclick-hash page and the pushState page: the route ran (`/api/session/end` ≥ 1), `/logout` 0, no deny row, no skip event, and the number of `reached` events equals `scope_routes_reached`.
- A replay whose handler switches to `#/logout`: the row is `failed`/`scope_reached`.
- Evidence counter tests: `test_inventory_export.py`, `test_evidence_html_scan.py`, `ScanEvidence.test.tsx`, `format.test.ts`.

## Deviations (also logged in `docs/superpowers/build/M4-DEVIATIONS.md`)

1. **R3-B1 wording.** The finding says "if it is not ok, do not call `_consider` or `_skip`". I apply that only when the tab is still in the document that was clicked. When the tab shows a new document, the navigation was a request the guard saw. Either the guard refused it, counted `blocked` with reason deny, as in the existing `test_the_deny_list_holds_for_clicks_redirects_and_frames` (0 hits on `/logout`). Or it was an HTTPS redirect hop, counted in `scope_paths_reached`. The existing skipped row is truthful there and is kept. Writing `reached` for a navigation the guard refused would be the opposite false claim.
2. **B4: some obfuscated captures are now withheld.** In the `contents`, `slotted` and `slot_fallback` cases, the second copy of the name is now read as a text node, but no detector flags a bare name. The obfuscated capture therefore finds the original still in its text and is withheld (`leakage`), which is fail closed. This is the same behaviour as for any undetected copy in an ordinary text node. The tests assert exactly this, via `CENSUS_OBFUSCATED_WITHHELD`.
3. **B5 audit scope.** These attribute-drawn strings are not handled and are logged as open items:
   - CSS generated content (`content: attr()` or literal strings on `::before`, `::after`, `::marker`);
   - `<li value>` list markers;
   - a closed `<select>` whose selected option has a `label` attribute (the field sweep compares the option's text);
   - `<track>` captions;
   - `<canvas>` text;
   - `<video poster>`.

## Gates (all run by me on the final tree)

| Gate | Result |
|---|---|
| `./lane.sh ruff format dps tests tools` | 293 files unchanged |
| `./lane.sh ruff check dps tests tools` | All checks passed |
| `./lane.sh mypy dps` | Success: no issues found in 119 source files |
| Full `./lane.sh pytest -q -rs`, lane stack up (scanner + sources + docs, rebuilt with `--build`), `DPS_TEST_TESSERACT_CMD` = docker tesseract wrapper | **2356 passed, 4 skipped**, 730 s (`pytest_full.txt`) |
| DoD-4 integration: `DPS_TEST_REQUIRE_SERVICES=1 DPS_TEST_REQUIRE_TESSERACT=1 pytest -m "browser or db" tests/scanner tests/test_no_values_in_logs_m4.py tests/test_tools_m4.py` | **226 passed, 0 skipped**, 533 s (`integration.txt`) |
| `cd frontend && npm run typecheck && npm test && npm run build` | tsc ok; **180/180** tests; build ok, check-bundle ok |
| `DPS_SMOKE_SCANNER=1 ./lane.sh python tools/smoke_test.py` | **all 45 checks passed** (`smoke.txt`) |
| Container log scan (all 12 services, 520 lines) for the 11 demo values | 0 raw hits, 0 normalised hits (counts only) |

The 4 skips are the same as in the verifier's run:
- 2 × `tests/documents/test_ocr_tesseract.py`, which needs tesseract on the host PATH. The scanner OCR tests ran through the wrapper.
- `[ner]` presidio and spaCy, which are not installed in this lane.

Container check: the browser-worker and app containers ran the new code (`_SETTLE_PASSES` 50, `_PUNCT` `[\W_]{0,3}`, `RunOutcome.scope_routes_reached`). Afterwards `docker compose … down` was run, and 0 `dps-wt-m4` containers remain. No other project was touched.

## Verifier probes re-run on the fixed code

- **`g1_blank_popup_worker.py`:** described under F1.
- **`probe_r3.py`** (copied to `probe-m4-verify-fix-r1/b45`, so the verifier's artefacts are not overwritten):
  - contents: masked 0+0; both obfuscated captures withheld (leakage).
  - alt: 0+0 in masked, synthesize and obfuscate.
  - optgroup: 0+0 in masked, synthesize and obfuscate.
  - slotted: the email is now found and masked (findings 0 → 1). The bare name "Customer: Zebulon Quixotic" is still read once. A control page `plainspan.html` with the same name in a plain `<b>` gives exactly the same result (1+1 in all three captures). So this is the detector's recall for an unlabelled name, like N9, not a rendering or census gap.

## Open items, non-blocking

- Detector recall for a bare, unlabelled name. It is the same in light DOM and in slots, and it is the same class as N9 (`application_ref`). One possible improvement: `labelHint` could use a preceding text sibling ending in ":" (`<p>Customer: <b>Name</b></p>`).
- The B5 audit items listed under deviation 3.
- From the verifier, not touched here: N9, N12/N8, N-r3-1 (slashless hash routes such as `#logout` are dropped by `canonical`, so a reached `#logout` is not detected as refused either), N-r3-2, N-r2-1, and the clipped inventory header.
- `docs/screenshots/m4/` (the verifier's 44 PNGs) is still uncommitted, as the verifier left it.
