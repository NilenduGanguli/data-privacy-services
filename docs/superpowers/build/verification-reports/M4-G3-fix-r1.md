# M4 G3 review r1: fix report (B1, B2)

Working copy: `/Users/neelu/document_intelligence/dps-wt-m4`, branch `m4`. Commit **`216bdd6`**
`fix(m4): masked captures box every copy of a detected value; frames matched by identity, unread
and late frames boxed, late text classified or withheld` (author NilenduGanguli
<nilendu.ganguli@gmail.com>, no trailer). The tree is clean after the commit.

## Resume state
An earlier attempt had died and left the whole fix uncommitted in `capture.py`, `dom.py`,
`models.py`, `test_capture.py`, `test_capture_ocr.py` and the new `test_capture_residual.py`, plus
reviewer probe copies in `/Users/neelu/.cache/dps-build/reports/probe-m4-G3-fix-r1/`. I did not
discard that work. I reviewed it against both findings, updated the `capture.py` module docstring,
wrote the deviation-log entry, ran every gate and the before/after checks, then committed it.

## B1: every copy of a detected value is boxed (`dps/layers/scanner/capture.py`)
- There is a new public function `sweep_copies(texts, fields, values)`, with `SWEEP_MIN_CHARS = 4`.
  - It looks for each value with at least 4 normalised letters/digits in every text node and in
    every visible field (for a select, the text of the selected option). Values are compared after
    `verify.normalise`, so case, accents, spacing and punctuation do not hide a copy.
  - Each hit becomes a code-point span or a field box.
  - A copy split across text nodes, or one the character mapping cannot reproduce, returns `None`,
    and the capture is withheld.
- `capture_screen` (masked path) sweeps for the original of every rendered finding and every
  pre-filled value. `obfuscated_capture` now runs its residual sweep through the same function.
  Because it normalises each text separately, non-ASCII originals are found, which also fixes nit
  N2.

## B2: frames are matched by identity (`dom.py`, `models.py`, `capture.py`)
- `extract_page` binds a memory-only `dom.Extraction` to the snapshot through
  `PageSnapshot.extraction`, a pydantic private attribute that is never serialised. It holds:
  - the `Frame` objects that were read, under their numbers;
  - a random token stamped into each read frame (`window.__dpsSnap`).
- Captures measure only those frames. The caller's positional map decides nothing:
  - A frame in that map that the snapshot did not read is boxed like any other unread frame.
  - A map that names a read frame under another number withholds the capture.
- Every live child frame in `page.frames` that was not read is boxed (measured now; detached
  frames skipped). If one cannot be measured, the capture is withheld.
- A read frame whose stamp is gone (reloaded, navigated, or extracted again) withholds the capture.
- A snapshot that did not come from `extract_page` is never captured.
- **Related item, done here instead of deferred:**
  - `dom.LIVE_TEXT_JS` and `late_texts()` find text that the read frames render now but the
    extraction did not read (new nodes, or extracted nodes whose text changed).
  - `capture_screen` gained the optional keyword `detector: Detector | None = None`. Late text is
    classified with it, and its hits are boxed and swept.
  - Without a detector, late text withholds the capture. A finding whose node changed also
    withholds it, and so do more than 5000 late nodes.
  - `obfuscated_capture` passes `engine.detector(policy, locale)`.

## Tests: failing before the fix, passing after
I exported HEAD `a5ff949` with `git archive` to the scratchpad and ran the new
`test_capture_residual.py` there with `PYTHONPATH` set to the export (I checked that
`dps.__file__` pointed into it). All 6 tests failed:
- the second copy was 0.0 covered;
- the split copy was captured;
- the late-attached frame was 0.0 covered;
- the swapped map was captured;
- the stale snapshot was captured;
- `detector` was an unknown keyword.

On the fix, all 6 pass. Other tests added:
- `test_capture.py`: +3 unit tests (sweep in any spelling, including astral characters, accents
  and selects; split copies and short values; a snapshot not from `extract_page` is refused).
- `test_capture_ocr.py`: +2 OCR tests (a name rendered twice, ASCII and non-ASCII). The unmasked
  control reads the family name in both places; the masked capture reads no part of 5 or more
  letters.

## Reviewer probes re-run on the fix (outputs are booleans only)
- `frame_probe.py`: frame_map after the attach gives `frames_in_map 2, snapshot_frames 1, status
  captured, name_readable False, digits_readable False`. The control (frame_map before the attach)
  is the same: captured, nothing readable.
- `masked_copy_probe.py` and `residual_probe.py`: in 4 runs each (ASCII and non-ASCII names ×
  synthesize and obfuscate), `family_name_readable_in_masked_png False`, and the obfuscated
  capture is withheld as `leakage`, as before.

## Gates (final tree)
| Gate | Result |
|---|---|
| `./lane.sh ruff format dps tests tools` | 277 files unchanged |
| `./lane.sh ruff check dps tests tools` | All checks passed |
| `./lane.sh mypy dps` | no issues in 115 source files |
| `./lane.sh pytest -q` | 1886 passed, 198 skipped (OCR and DB tests skip by default) |
| G3 scanner suites (`test_capture_residual`, `test_capture_ocr`, `test_capture`, `test_fill_browser`, `test_classify`, `test_risk_flags`, `test_personas_fill`, `test_dom_browser`, `test_guard`) with `DPS_TEST_REQUIRE_SERVICES=1 DPS_TEST_REQUIRE_TESSERACT=1` and tesseract through `docker run --network none data-privacy-services:local` | 160 passed, 0 skipped |
| `tests/test_surrogates.py tests/test_service.py`, same environment | 87 passed |

Frontend: not touched. Compose: no service needed, so none was started. I stopped the lane demo
site (port 8420) that the earlier attempt had left running.

## Privacy
- The new code adds no logging, `print` or `raise` (checked with a grep of the diff).
- Withheld reasons stay as constant codes.
- Tests and probes print only booleans and counts.

## Deviations (logged in `docs/superpowers/build/M4-DEVIATIONS.md`, section "G3 review round 1 — fix")
- New public names: `sweep_copies`, `SWEEP_MIN_CHARS`, `dom.Extraction`, `extraction_of`,
  `stamped`, `late_texts`, `LIVE_TEXT_JS`, `PageSnapshot.extraction` / `bind_extraction`.
- `capture_screen` has a new optional keyword `detector`. Existing contract names and signatures
  are otherwise unchanged.
- **Task 14 must pass `detector=` to `capture_screen`.** The plan's call omits it. That still fails
  closed, but every page whose text changes after extraction would then be withheld.

## Open issues
- **Over-withholding (accepted, fails closed):** the split-copy check concatenates the normalised
  node texts in order, so two adjacent unrelated nodes can spell a swept value between them and
  withhold the capture.
- **For Task 14:**
  - The census covers text nodes only, so input values that a script changes after extraction are
    not re-read.
  - Closed shadow roots are still not read (open item carried from Task 12).
- **Pre-existing, not caused by this fix:** a single pytest run that lists `tests/test_service.py`
  between `tests/scanner/*` files fails with `fixture 'chromium' not found` in
  `test_dom_browser` and `test_guard`. HEAD shows the same in the scratch export. Running the
  scanner and top-level files in separate runs passes.
- Nits N1 and N3–N7 from the review were not in scope for this fix and are unchanged.
