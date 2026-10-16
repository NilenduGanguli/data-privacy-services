# M4 G3 review, round 2 (Tasks 9 to 13 on branch `m4`, after fix `216bdd6`)

Reviewer: independent review agent, 2026-10-13. Working copy: `/Users/neelu/document_intelligence/dps-wt-m4`
(HEAD `216bdd6`). The tree was clean before and after the review, and I modified no repository file.
In scope: `f3eb65a` (T9), `0e12190` (T10), `89b0935` (T11), `8e5e35b` (T12), `a5ff949` (T13), and the
round-1 fix `216bdd6`, which touched `capture.py`, `dom.py`, `models.py`, the tests and the deviation log.
I did not rely on `M4-G3-impl.md` or `M4-G3-fix-r1.md`. I re-ran every claim myself.
Probes are in `/Users/neelu/.cache/dps-build/reports/probe-m4-G3-r2/`. They print booleans and counts only,
and every value in them is made up.

## Verdict: FAIL. Two blocking findings, both about captures that show a source value

All gates are green. The round-1 findings B1 (the exact second-copy case) and B2 (frames) are fixed: I re-ran
their probes and nothing is readable. However, the B1 guarantee as built ("every other copy … the page
renders is boxed; a copy split across text nodes withholds") still has holes:

* **B1-r2:** a copy is not boxed when it sits in a button, an `<input type=button|submit>` label, a visible
  listbox option, or 1-character text nodes.
* **B3:** text beyond the first 2000 characters of a text node is never read or classified, so it is never
  masked.

In both cases OCR reads the customer value from the masked capture and from both obfuscated captures.

---

## 1. Gates (I ran them)

| Gate | Result |
|---|---|
| `./lane.sh ruff format --check dps tests tools` | 277 files already formatted |
| `./lane.sh ruff check dps tests tools` | All checks passed |
| `./lane.sh mypy dps` | Success: no issues found in 115 source files |
| `./lane.sh pytest -q` (default suite) | **1886 passed, 198 skipped** in 101 s (OCR and DB tests skip by default) |
| G3 browser and OCR suites with `DPS_TEST_REQUIRE_SERVICES=1 DPS_TEST_REQUIRE_TESSERACT=1 DPS_TEST_TESSERACT_CMD=probe-m4-G3/tess.sh` (tesseract 5.5.0 run in the app image with `--network none`): `test_capture_residual, test_capture_ocr, test_capture, test_fill_browser, test_classify, test_risk_flags, test_personas_fill, test_dom_browser, test_guard` | **160 passed, 0 skipped** (87 s) |
| `tests/test_surrogates.py tests/test_service.py` (`DPS_TEST_REQUIRE_SERVICES=1`) | 87 passed |

Run notes:
* Chromium 1187 from playwright 1.55.0 in the lane venv.
* The tests auto-started the demo site on 8420. For the probes I started it myself, plus a static probe
  server on 127.0.0.1:8497. Both were stopped afterwards.
* Tasks 9 to 13 contain no SQL, so no compose service was needed and none was started.
* The frontend was not touched.

Gate outputs are in `probe-m4-G3-r2/gates.txt` and `probe-m4-G3-r2/g3_integration.txt`.

## 2. Round-1 findings re-checked

| r1 finding | Status | Evidence |
|---|---|---|
| B1: second copy of a detected value (greeting) | fixed for text nodes | `r1_masked_copy_probe.py`: masked PNG `family_name_readable False`; obfuscated withheld `leakage`. `test_capture_residual` and `test_capture_ocr` (+2) pass. |
| B2: frame attached after `extract_page` | fixed | `r1_frame_probe.py`: map built after the attach gives `status captured, name_readable False, digits_readable False`; the control is the same. Frames are now matched by identity (`dom.Extraction`, the stamp `window.__dpsSnap`). A disagreeing map, a stale snapshot or a reloaded page withholds (tests pass). |
| B2 related: text rendered after extraction | done (live text census) | Text nodes only. Input values are logged as open for Task 14 (see N8). |
| N2: non-ASCII residual sweep | fixed | per-text `normalise` in `sweep_copies` |
| N1, N3 to N7 | unchanged (not in fix scope) | still listed in §6 |

D4 re-check after the `dom.py` change: `r1_fill_probe.py` → `fill_probe.txt` runs 13 demo forms × 3 modes =
39 fills. `/__received` non_get delta 0 and form_get delta 0. The URL was unchanged, the guard was armed and
closed, and the stored fill records hold 0 demo values.

## 3. Conformance and the deviation log

* **Plan interfaces:** they hold. `capture_screen` gained only an optional keyword `detector`, and
  `PageSnapshot` gained only a private attribute that is never serialised. The `RiskFlag`, `FieldFacts`
  and `PageSnapshot` contract fields are unchanged, so the console lane is unaffected.
* **"G3 review round 1 — fix" deviation entry:** accepted. Its claims match the code:
  * `sweep_copies` exists with `SWEEP_MIN_CHARS = 4`;
  * frames are matched by identity;
  * stale stamps withhold;
  * late text is classified, or withheld when no detector is given;
  * more than 5000 late nodes withholds.
* **Over-withholding by adjacent nodes:** accepted. It fails closed.
* **"Task 14 must pass `detector=`":** accepted, and it must be carried into Task 14. Without it, every page
  with a live clock or counter is withheld.
* **Its open items:** closed shadow roots, and input values changed after extraction. Accepted as open, but
  see N8.
* **The entry's B1 claim** that "every other copy of those values the page renders (text or field)" is boxed
  is **not true as built** (B1-r2).
* **Programme plan §2.3 row G3:** classification ✓, risk flags ✓, persona values ✓, fill records validation ✓
  (re-probed). For "masked captures contain no source value (OCR the PNG)", the demo-site OCR suite passes,
  but it fails under B1-r2 and B3.

## 4. Blocking findings

### B1-r2: copies of a detected value outside "readable text nodes" are neither boxed nor withheld (`capture.py` `sweep_copies` / `dom.py` `_TEXT_HELPERS_JS`)

**Cause.** `sweep_copies` searches only `snapshot.texts` and the fields' values. The text-node predicate
`textOk` drops two kinds of node:
* nodes under `SKIP_TEXT` (`button`, `select`, `option`, `textarea`, …);
* nodes shorter than 2 characters.

`EXTRACT_JS` also excludes `<input type=button|submit|reset|image>` as `NON_FIELD`. These strings are
rendered on screen, but the sweep never sees them, and neither does the split-copy check (`haystack.count >
located`). So a value the scanner itself detected on the page stays readable in the "masked" PNG and in both
obfuscated captures. `verify_output` only checks the extracted document, so the obfuscated path does not
withhold either.

**Probes.** Each page shows a `Full name` / `Email` / `PAN` table that the detectors find, plus a second copy
of the value:

| Page (`site/*.html`) | Second copy | masked `capture_screen` (with `detector`) | `obfuscated_capture` synthesize / obfuscate |
|---|---|---|---|
| `button.html` | `<button>Continue as NAME</button>`, `<button>Send statement to EMAIL</button>` | captured, **name and email readable** | captured, **name and email readable** in both |
| `submitinput.html` | `<input type=button value="Continue as NAME">` | captured, **name readable** | captured, **readable** in both |
| `listbox.html` | `<select size=3>` with NAME as an unselected option | captured, **name readable** | captured, **readable** in both |
| `digits.html` | PAN rendered one character per `<span>` | captured, **PAN readable** | (not run) |
| `textarea.html` (control) | `<textarea>Dear NAME</textarea>` | captured, not readable (boxed) | – |

Sources: `capture_probe.json`, `capture_probe2.json`, `obf_probe.json`. The images (`button_masked.png`,
`digits_masked.png`) show the table cells boxed and the button and span copies in clear. In every case the
unmasked control reads every key, so the OCR is not blind.

**Why it is blocking.** It contradicts the module docstring and deviation entry ("every other copy … is
boxed; a copy split across text nodes withholds"). It contradicts the plan rule that an unmasked image of a
page rendering personal data never leaves memory, and programme acceptance G3. "Continue as <name>",
"Not <name>? Sign out" and per-digit account or PAN displays are ordinary banking UI.

**Fix.**
* Give the capture a census of every rendered string, separate from the classification text list:
  * every visible text node, with no minimum length and no `button`/`option`/`select`/`textarea` exclusion
    (a listbox's options are rendered; a closed select shows only its selected option);
  * the value labels of `input[type=button|submit|reset]`.
* Sweep every original against it: box what is in a measurable node, and withhold when the normalised
  concatenation holds more copies than were located. That covers the 1-character split.
* The obfuscated path needs the same census after the DOM replacement.
* Add browser and OCR tests for a button, an input button, a listbox option and a per-character span copy.

### B3: text past the 2000th character of a text node is rendered but never read, classified or masked (`dom.py` `EXTRACT_JS` / `LIVE_TEXT_JS`, consumed by `capture.py`)

**Cause.** `EXTRACT_JS` stores `nodeValue.slice(0, 2000)`, and the census compares the same truncated slice.
A value in the tail of a long text node is therefore not in any finding, not swept and not counted as late
text. Long text nodes are common: a `<pre>` statement, a JSON or log dump, a long terms paragraph, an address
block in one node.

**Probe.** `long.html` has an email in a table cell (detected and boxed) and a paragraph of 2100 characters of
boilerplate followed by `Contact: <another email>` in one text node. The masked capture and both obfuscated
captures are emitted, and **OCR reads the second email in all three** (`capture_probe.json`,
`obf_probe.json`; image `long_masked.png`). The snapshot's `max_text_len` is 2000.

In obfuscate mode, `REPLACE_TEXT_JS` overwrites a node that had a finding with its transformed first 2000
characters, so that node's tail is silently dropped. Only nodes with findings are rewritten.

**Fix.**
* Either read long nodes in chunks (several `TextFacts` with a base offset that `text_rects` honours), or
  make every capture fail closed when a visible text node is longer than the read cap.
* The census must detect changes on the full node (length plus a hash), not on the slice.
* Add a browser and OCR test with a value past character 2000.

## 5. What I verified and found sound

* **Round-1 behaviour:** the B1 and B2 scenarios now mask, and the six `test_capture_residual` tests assert
  pixel coverage, not just status.
* **Full-page screenshot geometry:**
  * a page with a `height:100vh` hero above the values (`vh.html`) is masked correctly, with nothing
    readable;
  * so is a `position:fixed; bottom:0` bar that repeats the email (`fixed.html`).
* **Textarea copies** are boxed through the field sweep (`textarea.html`).
* **Frames:** `_read_frames` and `_fresh_frames` match frames by identity and box every live unread child
  frame. A detached frame is skipped. Any read frame without its stamp withholds.
* **Two-pass measurement:** measuring twice (with late nodes re-numbered stably at `i >= base`) gives
  identical masks, so the "moved while shooting" check does not misfire.
* **No values in output:** there is no logging in `capture.py`, `classify.py`, `personas_fill.py` or
  `dom.py`. Every `raise` in them has a constant, value-free message, and withheld reasons are codes.
* **No SQL and no tenant state** in Tasks 9 to 13. No contract name used by another lane changed.
* **D4 while filling** still holds (§2).

## 6. Non-blocking findings and nits

* **N1 (r1, open):** `test_capture_ocr.py:144` still asserts only `>= max(2, len(keys)//2)` readable keys in
  the unmasked control. Assert the full set (it holds). The obfuscated OCR test still counts digit runs only
  from 6.
* **N3 to N7 (r1, open):**
  * N3: a select stores `option.text` as `shown`.
  * N4: `capture_json.t` keeps whole node text, and `capture_json.v` keeps the kept pre-filled values.
  * N5: unclassified visible pre-filled inputs are not masked.
  * N6: `age` from `date.today()`.
  * N7: the `rng_choice` and `ATTR_ENTITY` fallback style issue.
* **N8 (open, logged for Task 14):** an input value set by script after extraction is readable in the masked
  capture (`lateinput.html`: captured, value readable). The census covers text nodes only. This is the same
  class as the late-text item r1 asked to fix or log. It was logged, which I accept, but Task 14 must close
  it, for example by re-reading the values of visible text inputs in `measure()` and classifying or
  withholding them.
* **N9:** fields beyond `max_fields` are not stamped or read, and `capture_screen` does not check
  `snapshot.truncated`. A pre-filled value in such a field is neither masked nor withheld. Fold this into
  the B1-r2 census, or withhold on field truncation.
* **N10 (cosmetic):** `_unique_spans` keeps overlapping spans with different labels. In `button.html` the
  email cell carries the label `PERSON_NAME`, because the swept name span lies inside the email span. The
  value is fully covered; only the label is wrong.
* **N11 (theoretical):** for a periodic value (a needle with a border, e.g. `abcab`), an occurrence that
  straddles a boxed in-node copy and the next node can pass `haystack.count == located` (the count is
  non-overlapping), leaving a partial tail unboxed. This is very unlikely for real identifiers.
* **Pre-existing (reported by the fixer, confirmed by reading):** one pytest run that mixes
  `tests/test_service.py` between scanner files loses the `chromium` fixture. Separate runs pass.

## 7. Probe index (`/Users/neelu/.cache/dps-build/reports/probe-m4-G3-r2/`)

| File | Purpose and result |
|---|---|
| `gates.txt`, `g3_integration.txt` | gate outputs (§1) |
| `site/*.html` | static probe pages (made-up values) |
| `capture_probe.py` → `capture_probe.json` | masked vs unmasked OCR: button **leak**, vh ok, fixed ok, long **leak**, lateinput leak (N8), listbox **leak** |
| `capture_probe2.py` → `capture_probe2.json` | per-character PAN **leak**; textarea boxed (control) |
| `obf_probe.py` → `obf_probe.json`, `obf_*.png` | obfuscated captures (synthesize, obfuscate): button, input button, listbox and long all **readable** |
| `r1_frame_probe.py`, `r1_masked_copy_probe.py` | r1 B1 and B2 re-run: fixed |
| `r1_fill_probe.py` → `fill_probe.txt` | 39 fills, `/__received` deltas 0 |
| `*_masked.png`, `*_control.png`, `*_top.png` | images (masked = what would be stored) |
