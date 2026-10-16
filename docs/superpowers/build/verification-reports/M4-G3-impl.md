# M4 G3 implementation report — Tasks 9–13 (worktree `dps-wt-m4`, branch `m4`)

Date: 2026-10-13. Start state: clean tree at `6cfdbdf` (G2 fixes); no earlier G3 work existed
(nothing committed or uncommitted for Tasks 9–13, no report file), so all five tasks were built
from scratch.

## Commits (author NilenduGanguli <nilendu.ganguli@gmail.com>, no trailers)

| Task | Commit | Message |
|---|---|---|
| 9 | `f3eb65a` | feat(scanner): field classification (autocomplete, structural, type, pattern, value) and rendered-PII detection |
| 10 | `0e12190` | feat(scanner): risk flags — autocomplete on sensitive fields, password tokens, GET forms, PII in URLs, rendered PII |
| 11 | `89b0935` | feat(scanner): coherent seeded personas through the surrogate engine, field-rule formatting, tokens of persona values |
| 12 | `8e5e35b` | feat(scanner): masked screenshots, numbered overlays and reversible obfuscated captures (fail closed) |
| 13 | `a5ff949` | feat(scanner): fill pages field by field without any key press or click, recording client-side validation |

Each commit carries its section of `docs/superpowers/build/M4-DEVIATIONS.md`. No contract name
changed, so the contract file was not edited.

## What was built

- `dps/layers/scanner/classify.py` — autocomplete map, structural/type/pattern/value signals with
  noisy-OR, `classify_rendered` (value detectors + row-header/`dt`/`Label:` hints), `redact_text`,
  `value_classifier`, `placeholder_shape`; plus `key_candidates`, `pattern_accepts`,
  `CREDENTIAL_ENTITIES`, `sensitive_prefilled`.
- `dps/layers/scanner/inventory.py` — `field_risks`, `url_risks`, `rendered_risks`,
  `number_flags`, `risk_codes_by_field` (value-free, one flag per kind per page).
- `dps/layers/scanner/personas_fill.py` — seeded personas through M0 `apply_plan` + surrogates,
  `plan_field`, `choose_format`/`apply_format`, `sample_for_pattern`, `choose_option`,
  `tokens_for` (verified, `LeakageError`), `prepare_fill`, `fill_page`, `validation_records`,
  `validation_counts`.
- `dps/layers/scanner/capture.py` — masks/overlays (Pillow), `capture_screen`,
  `obfuscated_capture`, `field_boxes`.
- `dps/layers/scanner/urls.py` — additive public `safe_key`.
- Tests: `tests/scanner/test_classify.py` (30), `test_risk_flags.py` (10), `test_personas_fill.py`
  (15), `test_capture.py` (10), `test_capture_ocr.py` (7, G3), `test_fill_browser.py` (5);
  `tests/test_service.py` (+1 assertion), `tests/test_surrogates.py` (+1 test);
  `tests/scanner/helpers.py` (shared `field_facts`/`classification`, `DEMO_VALUE_KEYS`,
  `demo_cookie_state`, `guarded_context`); `tests/scanner/conftest.py` (`RotCipher` fix).
- Import hygiene checked: importing `classify`, `inventory`, `personas_fill` loads neither
  Playwright nor Pillow.

## Deviations (full text in M4-DEVIATIONS.md, sections Task 9–13)

1. **ReDoS (Task 9).** The plan's nested-quantifier regex missed `((a+))+`, `(?:a{1,9}){1,9}`,
   `(a|a)*`, `(a|aa)?`×30 and long runs of `\d*`, all catastrophic on a hostile page's `pattern`.
   Now a parse-tree safe subset (no look-arounds/back-refs, no variable repeat over a variable
   body, ≤ 4 variable repeats+alternations) and inputs ≤ 64 chars (`pattern_accepts`).
2. Labels are page text: `.[]'"$` become spaces before `classify_key` (it read `Aadhaar No. (e.g.
   1234)` as a JSON path → nothing).
3. `PrivacyEngine.detector` already existed (M2) — no engine change.
4. Risk flags: hash-route queries and bare value keys are read; query keys listed only via
   `urls.safe_key`; page URL + external links merged into one flag per page.
5. IFSC surrogate: **no M0 change** — already rule-shaped and an M0 test pins the kept bank code,
   which the plan's change would break; the plan's test added against the code as built.
6. Personas: phone exemplars carry the locale's country (the plan's random `+12 digits` gave
   foreign numbers); `tokens_for(engine, items, *, …)` — `items` positional so the plan's own
   `functools.partial` call works (keyword-only raised `TypeError`); select/radio fills store
   persona values or `option:<n>`/`selected`, never option text (page data); `ATTR_ENTITY` added.
   Plan test adjusted: M0's Aadhaar surrogates start with `1` (never issued) so
   `validate("AADHAAR")` is False by design → test checks 12 digits + Verhoeff.
7. Captures: unread frames painted on every capture (unmasked too), frame offsets measured live,
   masks measured before and after the screenshot (animations off) and withheld on any movement,
   locator timeouts 2 s, mask far edge rounded up, selects boxed instead of DOM-replaced.
8. Test cipher: the scanner `RotCipher` was degenerate under M0's leading-zero cycle walking
   (`CUST0048213` → itself after 10 rounds; the capture's leakage check correctly withheld). It
   now reverses before rotating.
9. Fill: checkboxes/radios ticked by property + events, not `loc.check()` (a click); unknown action
   → `failed`; `validation_records`/`validation_counts` added for Task 14.

## Gate results (final, after `a5ff949`)

- `./lane.sh ruff format dps tests tools` — clean; `./lane.sh ruff check dps tests tools` — All
  checks passed; `./lane.sh mypy dps` — Success: no issues found in 115 source files.
- `./lane.sh pytest -q` (with `DPS_TEST_TESSERACT_CMD` set) — **1884 passed, 189 skipped**. The 189
  skips are the DB/S3/source-container tests (no lane containers were needed: Tasks 9–13 touch
  no database). Baseline before G3: 1806 passed, 189 skipped. Without the OCR command the 7 OCR
  tests skip (scanner suite: 428 passed, 23 skipped).
- G3 tests with `DPS_TEST_REQUIRE_SERVICES=1 DPS_TEST_REQUIRE_TESSERACT=1` (classify, risk flags,
  personas, capture, OCR, fill, surrogates, service): **164 passed, 0 skipped**.
- Browser tests ran against the demo site auto-started with the host's node on the lane port, and
  Chromium 1187 (Playwright 1.55.0). No docker compose services were started (none needed), so
  there was nothing to `compose down`. Frontend untouched.

## Programme acceptance G3 — OCR of the captures

Tesseract 5.5.0 from `data-privacy-services:local` via
`docker run --rm -i --network none --entrypoint tesseract` (the Mac has no tesseract); images
upscaled 2×, `--psm 11`. Counts only (which demo values the OCR text holds):

| Page | values on page | unmasked (control) | masked original | obfuscate | synthesize |
|---|---|---|---|---|---|
| `/profile` | 10 | 10 readable | **0** | **0** | **0** |
| `/profile/contact` (pre-filled inputs) | 3 | 3 readable | **0** | **0** | **0** |
| `/apply/review` | 4 | 4 readable | **0** | **0** | **0** |

Masked: whole values, 5+-letter runs and 4+-digit runs. Obfuscate/synthesize: whole values,
5+-letter runs, 6+-digit runs (policy-kept parts not counted: email token domain, Aadhaar token
last four, card token IIN/last four, test IIN 411111). The same check is the committed test
`tests/scanner/test_capture_ocr.py` (7 tests, positive control included). Evidence PNGs (no
unmasked image kept) and the wrapper/script: `~/.cache/dps-build/reports/M4-G3-ocr/`.

D4 counting evidence in the fill tests: every fill test reads the demo site's `/__received`
before and after; non-GET count unchanged in all 5, while `guard.blocked_total ≥ 1` on the
identity form (autosave POST and beacon were attempted and stopped).

## Open issues (for Task 14 and later reviews)

- **Shadow DOM residual:** text in a *closed* shadow root, or in an open one when a scan runs with
  `scope.shadow_dom=false`, is rendered but never read, so no mask covers it. Task 14 should
  extract with shadow DOM on for captures (or box open shadow hosts); closed roots need CDP
  (`DOM.getDocument(pierce=true)`).
- Task 14 should use `classify.sensitive_prefilled` for `prefilled`, `validation_records` /
  `validation_counts` for `FieldRecord.validation_messages` / `PageRecord.validation[mode]`, and
  check `page.url` after `fill_page` (an aborted navigation leaves Chromium's error page).
- Policy-kept partial values remain visible in obfuscate captures by design (gdpr-pseudonymise:
  Aadhaar last four, card IIN/last four, email domain, date-shifted year may coincide); a policy
  choice, not a capture defect.
- The OCR test needs a tesseract binary; on the Mac set `DPS_TEST_TESSERACT_CMD` to the docker
  wrapper. The browser-worker image (Task 20) may want tesseract for the milestone gate.
