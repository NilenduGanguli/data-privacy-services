# M4 G3 fix, round 2: B1-r2 and B3 (Tasks 9 to 13, branch `m4`)

Worktree: `/Users/neelu/document_intelligence/dps-wt-m4`. Starting point: HEAD `216bdd6` with a clean tree, so no earlier attempt was left over.
Commit: **`d987144`**, `fix(m4): captures census every rendered string (buttons, listbox options, input labels, 1-character nodes); text nodes read whole up to a cap, truncated pages withheld`.
* Author: NilenduGanguli <nilendu.ganguli@gmail.com>.
* No trailer.
* Committed with an explicit pathspec. No push and no tag.

## What changed

### B1-r2: the capture's own census of rendered strings (`dps/layers/scanner/dom.py`, `capture.py`)

**New pieces in `dom.py`:**
* `CENSUS_JS`, `render_census(ext)` and `census_rects(frames, offsets, items, spans)`.
* `CensusItem`, which is memory only and keeps the text out of `repr`.

**When the census runs.** It runs at measure time, before the shot and again after it, on every read frame. The frame's stamp is checked first, and open shadow roots are always descended.

**What the census lists.** Everything rendered at that moment, kept separate from the classification text list:
* every text node under a visible parent:
  * there is no minimum length and no `button` exclusion;
  * only `script`, `style`, `noscript`, `template` and `title` are left out, plus the source text of `textarea` and `select`, because their shown values are swept as fields;
* every visible `<option>`. A listbox renders its options. A closed select's options have no layout, and its shown option is swept as a field.
* the label of every visible `input[type=button|submit|reset]`.

**How copies are measured.**
* Text nodes are boxed by text range.
* Options and input labels are boxed by their whole element box. I checked that Chromium returns 0 range rects for listbox option text.
* A span that cannot be measured withholds the capture.
* The census fails closed when a single string is over `TEXT_READ_CAP`, the total is over `TEXT_READ_BUDGET`, there are more than 50000 strings, or the stamp does not match.

**`capture._sweep` (shared, coverage-based).**
* Every swept value (`SWEEP_MIN_CHARS`+, normalised) is searched with overlapping matches over the normalised concatenation of the census strings.
* Each occurrence must lie wholly inside copies that were located in single strings, or inside a whole-box string that holds a copy. Otherwise the capture is withheld.
* So a value rendered one character per `<span>` now withholds: those 1-character nodes are in the census.
* This also closes **N11**: a periodic value that straddles a located copy now withholds.
* `sweep_copies(texts, fields, values)` keeps its name, signature and return shape, and now uses `_sweep`.

**Census-only strings are classified when a detector is given.** These are button text, input button labels, listbox options and nodes under 2 characters. Each hit is boxed and its value is swept for. This covers "Continue as NAME" when NAME is not detected anywhere else on the page. Without a detector these strings are only swept.

**Classification and risk flags (Tasks 9 and 10) are unchanged.** They still read the extraction's text list, and `SKIP_TEXT` is untouched.

**Where the census is used.**
* `capture_screen` (masked) sweeps the census instead of `snapshot.texts`.
* `obfuscated_capture` sweeps the census after the DOM replacement, through `_measurer` on the re-extracted page.

### B3: text nodes read whole up to a cap (`dom.py`, `capture.py`, `models.py`)

* **Read cap.** `EXTRACT_JS` reads a text node whole up to `dom.TEXT_READ_CAP = 20_000` UTF-16 units; it used to keep only `slice(0, 2000)`.
  * A longer node is read up to the cap for classification, and the snapshot is marked `truncated`.
  * More than `TEXT_READ_BUDGET = 2_000_000` units in one frame also sets `truncated`.
* **Change detection on the whole node.** `window.__dpsTextValues` now keeps the whole node text, so `LIVE_TEXT_JS` compares whole nodes: a change anywhere in a node counts as late text. A node that grows past the cap after extraction makes the census return `null`, so the capture is withheld.
* **`REPLACE_TEXT_JS`** rewrites a node only while it still holds exactly the text that was read, and returns how many nodes it rewrote. `obfuscated_capture` withholds (`residual`) unless every rewrite happened. A tail can no longer be silently dropped.
* **`PageSnapshot.truncated`** (the contract field is unchanged) now means "the extraction did not read everything the page renders". When it is set:
  * `capture_screen` returns `None`;
  * `obfuscated_capture` returns `withheld` with the new reason code **`truncated`**, both up front and when the re-extraction after the replacement is truncated.

  This also closes **N9** (fields beyond `max_fields`). Unmasked local captures are not affected. No other code read `truncated`; I checked with grep.

### Deviation log

`docs/superpowers/build/M4-DEVIATIONS.md` has a new section, "G3 review round 2 — fix". It records:
* the census;
* the coverage rule;
* classification of census-only strings;
* the cap and budget;
* the wider meaning of `truncated` and the new `truncated` reason code;
* more over-withholding (accepted, because it fails closed);
* the items still open.

No contract name changed: `sweep_copies`, `capture_screen`, `obfuscated_capture`, `extract_page`, `PageSnapshot` and `TextFacts` keep their signatures and fields.

## Tests: each one fails before the fix

New tests:
* **`tests/scanner/test_capture_census.py`: 17 browser tests.**
  * A copy in a `<button>`, an `<input type=button>` label or a listbox option is boxed. This is checked by pixel coverage in the masked capture and in both obfuscated captures (9 tests).
  * One character per node withholds.
  * A value after character 2000 is read, classified and boxed.
  * A change after character 2000 is seen as late text: withheld without a detector, boxed with one.
  * A node longer than the cap withholds the masked capture and the synthesize capture.
  * A node that grows past the cap after extraction withholds.
  * A truncated field extraction withholds (N9).
  * Obfuscation rewrites a long node whole (both modes): the value is gone and the text before and after it is kept.
* **`tests/scanner/test_capture_ocr.py`: +5 OCR cases** (button, input_button, listbox, digits, long).
  * The unmasked control reads every value, and reads the copied value at least twice.
  * The masked, synthesize and obfuscate captures read none of them.
  * For digits, all three captures are withheld.
* **`tests/scanner/test_capture.py`: +1** for the periodic value (N11).
* The probe pages are `CENSUS_PAGES` in `tests/scanner/helpers.py`. Every value in them is made up.

**Proof that they fail before the fix.** I exported HEAD `216bdd6` with `git archive` and copied the new test files over it. Run with that export first on `PYTHONPATH`, the result was **23 failed, 22 passed**, and the 23 failures are exactly the new tests. With the fix, all of them pass.

## Gates (worktree, after the fix)

| Gate | Result |
|---|---|
| `./lane.sh ruff format --check dps tests tools` | 278 files already formatted |
| `./lane.sh ruff check dps tests tools` | All checks passed |
| `./lane.sh mypy dps` | Success: no issues in 115 source files |
| `./lane.sh pytest -q`, run 1 | 1903 passed, 203 skipped, **1 failed**: `tests/test_ndjson.py::test_one_audit_event_per_stream` |
| `./lane.sh pytest -q`, run 2 | **1904 passed, 203 skipped** |
| G3 browser and OCR suites (see below) | **183 passed, 0 skipped** (107 s) |

About the `test_ndjson` failure in run 1: that test is M0 NDJSON audit code, which I did not touch. It passed 3 times out of 3 on its own and passed in the second full run, so it is an unrelated flake.

The G3 suites were run with `DPS_TEST_REQUIRE_SERVICES=1 DPS_TEST_REQUIRE_TESSERACT=1 DPS_TEST_TESSERACT_CMD=probe-m4-G3/tess.sh`, which runs tesseract in the app image with `--network none`. The files were:
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

Other gate notes:
* Tasks 9 to 13 contain no SQL, so no compose service was needed and none was started. No containers of this lane are running.
* The frontend was not touched, so the npm gates do not apply.
* No extras were skipped.

## Reviewer probes re-run against the fix

The probes are copied into `/Users/neelu/.cache/dps-build/reports/probe-m4-G3-fix-r2/`. They print booleans only. The static server on 127.0.0.1:8497 and the demo site on 8420 were stopped afterwards.

**Masked captures** (`capture_probe.py`, `capture_probe2.py`):

| Page | Result |
|---|---|
| button | captured, name and email not readable |
| listbox | captured, name not readable |
| long | captured, both emails not readable; `max_text_len` 2137, so the whole node was read |
| digits | **withheld** |
| vh, fixed, textarea | still captured, nothing readable |
| lateinput | still readable. This is N8, explicitly left for Task 14. |

**Obfuscated captures** (`obf_probe.py`):
* button, submitinput, long and listbox, in synthesize and obfuscate: all captured, and **nothing readable**.
* The capture JSON holds no original.
* The submitinput masked capture is not readable either.

**D4 fill probe** (`r1_fill_probe.py` → `fill_probe.txt`): 39 fills (13 forms × 3 modes). In every fill the guard was armed and then closed, and the URL was unchanged. The stored fill records hold no demo values. Totals: `non_get_delta 0`, `form_get_delta 0`.

**Privacy.**
* There is no logging or printing in `capture.py` or `dom.py`, and `raise` is never used in them.
* Withheld reasons are codes only: `frame`, `truncated`, `residual`, `leakage`, `guard_lock`, `mask`.
* Census items and `TextFacts` keep their text out of `repr`.

## Open issues (not in this fix's scope, also logged)

* **N8:** an input value set by script after extraction is still not re-read. Task 14 must close this, for example by putting the values of visible text inputs in the census.
* **Not in the census:** closed shadow roots, and strings rendered from CSS `content`, `placeholder` attributes or canvas.
* **More over-withholding:** the concatenation now also holds button text, labels and 1-character nodes. Unrelated adjacent strings that together spell a swept value withhold the capture. This is accepted because it fails closed.
* **Task 14 must pass `detector=`** to `capture_screen`. This is unchanged from r1, and it now also drives the classification of census-only strings.
* **N1, N3 to N7 and N10** from the review are unchanged (not in scope).
* **Flake:** `tests/test_ndjson.py::test_one_audit_event_per_stream` failed once in a full run, outside M4 scope. It needs a separate look.
