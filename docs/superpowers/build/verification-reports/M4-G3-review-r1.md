# M4 G3 review — round 1 (Tasks 9–13 on branch `m4`)

Reviewer: independent review agent · 2026-10-13 · working copy `/Users/neelu/document_intelligence/dps-wt-m4`
(HEAD `a5ff949`, tree clean before and after; no repository file modified).
Commits reviewed: `f3eb65a` (T9), `0e12190` (T10), `89b0935` (T11), `8e5e35b` (T12), `a5ff949` (T13).
Probes: `/Users/neelu/.cache/dps-build/reports/probe-m4-G3/` (scripts, outputs, PNGs).
The implementer's report `M4-G3-impl.md` was not relied on; every claim below was re-run.

## Verdict: FAIL — 2 blocking findings (both: a masked capture shows a source value)

Every gate is green and the demo-site OCR acceptance passes, but two probes produce **masked captures
in which OCR reads a customer value**, contradicting the module's own rules, D-C ("capture fails closed
on unextracted frames") and programme acceptance G3 ("masked captures contain no source value").

---

## 1. Gates (run by me)

| Gate | Result |
|---|---|
| `./lane.sh ruff format --check dps tests tools` | 276 files already formatted |
| `./lane.sh ruff check dps tests tools` | All checks passed |
| `./lane.sh mypy dps` | Success: no issues found in 115 source files |
| `./lane.sh pytest -q` (full default suite) | **1877 passed, 196 skipped** (101.8 s) |
| `./lane.sh pytest -q tests/scanner -rs` | 434 passed, 23 skipped: 7 OCR tests (no tesseract on PATH) + 16 DB tests (dps-db not up; T3/T5, not G3) |
| G3 suites with `DPS_TEST_REQUIRE_SERVICES=1 DPS_TEST_REQUIRE_TESSERACT=1 DPS_TEST_TESSERACT_CMD=<probe>/tess.sh` (wrapper: `docker run --rm -i --network none --entrypoint tesseract data-privacy-services:local`, tesseract 5.5.0): `test_capture_ocr, test_capture, test_fill_browser, test_classify, test_risk_flags, test_personas_fill, tests/test_surrogates.py, tests/test_service.py` | **164 passed, 0 skipped** |
| `test_capture_ocr.py -v` | 7/7 passed (masked ×3 pages, obfuscate/synthesize ×2 pages) |

Browser tests ran against Chromium 1187 (playwright 1.55.0 in the lane venv) and the demo site
auto-started by the fixture on the lane port 8420. No compose service is needed by Tasks 9–13 (no
SQL); none was started. The node demo site I started for probes was stopped afterwards.

## 2. Plan / contract / spec / programme-plan conformance

| Task | Interface vs plan | Notes |
|---|---|---|
| 9 Classification | all names present (`AUTOCOMPLETE_ENTITY`, `PATTERN_PROBES`, `MAXLENGTH_HINTS`, `autocomplete_token`, `compile_html_pattern`, `pattern_entities`, `classify_field`, `classify_rendered`, `redact_text`, `value_classifier`, `placeholder_shape`); `PrivacyEngine.detector` already existed (M2) | evidence strings value-free (structural evidence is the dictionary's phrase `key:<phrase>`, verified in `structural.py:240`) |
| 10 Risk flags | `field_risks`, `url_risks`, `rendered_risks`, `number_flags`, `risk_codes_by_field` | `RiskFlagKind`/`RiskFlag`/`NumberedRiskFlag` unchanged in `models.py` (console lane contract intact) |
| 11 Personas | all plan names; `tokens_for` signature changed (items positional) | justified (see §3) |
| 12 Captures | all plan names; stricter (double measurement, frame boxes) | **B1, B2** |
| 13 Fill | `FillResult`, `fill_page` + new `validation_records`, `validation_counts` | statuses ⊂ contract `fill_status` CHECK set; `ValidationMessage.text ≤ 200` respected |

Programme plan §2.3 G3 row: classification via the shared M0/M2 detectors ✓; risk flags ✓; persona
values ✓; captures ✗ (B1, B2); fill records page validation ✓; "masked captures contain no source
value (OCR the PNG)" ✓ on the demo pages, ✗ under the two probes below.

## 3. Deviation log entries (M4-DEVIATIONS.md §Task 9–13) — judgement

| Entry | Judgement |
|---|---|
| T9 no change to `service.py` (detector exists) | accepted (verified) |
| T9 safe-subset pattern compilation (ReDoS) | accepted, stronger than plan. Probe: `(\d+)+x`, `(?:\d{1,8}){1,8}x`, `\d*`×5 refused; 4-way polynomial patterns compile but answer in ≤ 3 ms on 64-char input (`redos_probe.py`) |
| T9 labels as text (`key_candidates`) | accepted |
| T9 `placeholder_shape` any script | accepted |
| T10 hash-route query, bare key value, `safe_key`, form_index 0 excluded | accepted |
| T11 no IFSC surrogate change (M0 already rule-shaped, keeps bank code) | accepted (test added against code as built) |
| T11 locale phone exemplars, `ATTR_ENTITY`, `tokens_for` items positional | accepted — the plan's keyword-only `items` cannot be called through its own `functools.partial` |
| T11 select/radio `shown` never page text | accepted with nit N3 (matched-by-value stores `option.text`) |
| T11 Aadhaar test checks 12 digits + Verhoeff instead of `validate()` | accepted (M0 draws non-issuable numbers by design) |
| T12 frames measured live, double measurement, rounding up, select boxed | accepted — but the frame rule has the fail-open hole B2 |
| T12 `RotCipher` reverse-then-rotate in scanner conftest | accepted: the plain rotation is degenerate under cycle walking; I re-ran all captures with the **real FF1** cipher (fastfpe) — captured, no value readable (`ocr_probe.json`) |
| T12 `test_capture_ocr.py` positive control "10/10, 3/3, 4/4" | the claim holds in my run (`ocr_probe.json`: unmasked /profile reads all 10 keys), but the test asserts only `≥ max(2, n//2)` (nit N1) |
| T12 open item: closed shadow roots / `shadow_dom=false` | accepted as open for Task 14 |
| T13 check without click (`el.checked = true`), failed-not-filled, `validation_records/counts` | accepted |

## 4. Blocking findings

### B1 — a masked capture shows a value the scanner itself detected on the same page (`capture.py` `capture_screen`)

`capture_screen` paints boxes only over the *spans of the rendered findings*. When the same value is
rendered again in a node where the detectors do not fire (typical: the customer's name found via the
`Full name` row header, and again in a greeting "Welcome back, <name>"), the second copy is left in the
"masked" PNG. The scanner holds the value (it detected it) and does not look for it elsewhere.
`obfuscated_capture` has exactly this knowledge (its `verify_output` over all page texts withholds such a
page as `leakage`), so the two capture paths disagree: the obfuscated one fails closed, the masked
original ships the value.

Probe `probe-m4-G3/masked_copy_probe.py` (and `residual_probe.py`, ASCII and non-ASCII names, en/es):
demo `/help` + an injected `<table><th>Full name</th><td>NAME</td>` and `<p>Welcome back, NAME</p>`;
`classify_rendered` finds the table cell (1 finding), `capture_screen` returns a PNG, tesseract reads
the family name: **readable = True** in all 4 runs; `obfuscated_capture` on the same page: withheld
(`leakage`). Image: `probe-m4-G3/masked_second_copy.png` (table cell boxed `PERSON_NAME`, greeting in
clear).

Fix: in `capture_screen` (masked path) sweep every snapshot text node for every original of a rendered
finding / pre-filled value (≥ 4 normalised chars, the same rule as the residual sweep) and box every
occurrence; withhold when an occurrence cannot be located (cross-node). Add a browser + OCR test with
the value rendered twice.

### B2 — a same-origin frame the snapshot never read is neither boxed nor withheld (`capture.py` `_fresh_frames`) — D-C fail-open

`_fresh_frames` iterates the caller's `frames` map and, for an index the snapshot has no facts for,
does `continue`; that frame is then also excluded from the "unread" loop because it is in `read`
(`read = list(frames.values())`). Indexes are positional (`dom.frame_map` numbers `page.frames` in
order) and `extract_page` builds its own map, so any frame that attaches between `extract_page` and the
caller's `frame_map` — the exact call order of every plan test and of `test_capture_ocr.py` — is
treated as read, gets no finding, no `FRAME` box, and the capture is emitted.

Probe `probe-m4-G3/frame_probe.py`: demo `/help`, `extract_page`, then a same-origin `srcdoc` iframe
showing a name and an Aadhaar-shaped number is attached, then `frame_map`, then `capture_screen`:
`frames_in_map 2, snapshot_frames 1, status captured, name_readable True, digits_readable True`
(image `probe-m4-G3/frame_probe_frame_map_after_attach.png`). Control (frame_map before the attach):
the frame is boxed, nothing readable. The same hole shifts indexes when a frame detaches/attaches, so
`text_rects` can measure node `i` of the wrong frame (most such cases withhold, but none is checked).

Fix: match frames by identity, not by position — e.g. `extract_page` returns (or stores) the `Frame`
objects it read and captures use that map; in `_fresh_frames` any frame in `frames` without read facts,
and any live child frame not read, is boxed or the capture withheld. Related (same root cause, include
in the fix or log as open for Task 14): neither capture checks that the live page still renders only
the text the snapshot read — main-frame text rendered after extraction is not masked; a cheap
re-extraction in `_shoot` that withholds when new text nodes carry detector hits would close it.

## 5. What I verified and found sound

* **OCR acceptance (G3) on the demo site**, with the real FF1 cipher and with det_token
  (`ocr_probe.py` → `ocr_probe.json`, 9 pages × unmasked/masked/obfuscate-ff1/obfuscate-det/synthesize,
  4-digit and 4-letter pieces — stricter than the test's 6-digit rule for tokens): masked captures read
  0 demo values; obfuscate/synthesize captures read only policy-kept pieces (email domain
  `example`/`mail` — also present in the chrome word "Email", test IIN `4111 1111`, Aadhaar last four).
  Positive control: unmasked `/profile` reads all 10 keys, `/profile/contact` 3/3 (+name piece),
  `/apply/review` 4/4. Capture JSON of every obfuscate run: 0 whole demo values.
* **D4 while filling** (`fill_probe.py` → `fill_probe.txt`): 13 demo form pages × synthesize /
  obfuscate-FF1 / obfuscate-det = 39 fills: `/__received` **non_get delta 0, form_get delta 0**, page URL
  unchanged every time, guard armed every time, autosave/beacon blocked where the page has them (1–2
  per page), `guard.close` True. Stored fill records (`shown`, redacted validation texts) hold 0 demo
  values.
* Validation evidence: synthesize mode accepted by the page rules on every en_IN form; det tokens
  rejected by the Aadhaar pattern and recorded (`native` + `aria` + `aria-invalid`).
* No logging in the four G3 modules; exception messages are constants (`"leakage"`, `"unknown fill
  format"`, `"obfuscate mode needs a token function"`); credentials never in `shown`, hidden from `repr`.
* No SQL, no tenant-scoped state, no API in Tasks 9–13 (nothing to isolate); contract model names
  unchanged.
* Fill code holds no `.click(`, `.press(`, `.type(`, `keyboard`, `mouse`, `.check(`, `requestSubmit`,
  `dispatch_event(` (pinned by a source test) — verified by reading `fill_page`.

## 6. Non-blocking findings and nits

* **N1** `test_capture_ocr.py` positive control asserts only `≥ max(2, len(keys)//2)` readable keys; the
  deviation log claims every value is read. Assert the full set (it holds today) so a weaker OCR cannot
  make the masked assertion vacuous. The obfuscated OCR test has no control of its own and counts digit
  runs only from 6 — a partially kept token (e.g. two original 4-digit Aadhaar groups) would pass; a
  4-digit rule with an explicit kept-parts allow-list would be tighter.
* **N2** `obfuscated_capture` residual sweep builds its haystack with `json.dumps(...)` (default
  `ensure_ascii=True`) and then `normalise`s it: a non-ASCII original (`José`, Devanagari) becomes
  `jos00e9…` and is never found, so a surviving non-ASCII original is not boxed. Today the earlier
  whole-document `verify_output` withholds the cases I could build (`residual_probe.py`: withheld
  `leakage`), so this is defence-in-depth, but the documented guarantee is false for non-ASCII. Use
  `ensure_ascii=False` or normalise per text.
* **N3** `prepare_fill` select: when the persona value matches an option's *value* but not its text,
  `shown` stores `option.text` (page text). Store the persona value instead.
* **N4** `capture_json["data"]["t"]` keeps the whole text of a node that held a finding, so undetected
  text in the same node (e.g. a name next to a detected Aadhaar) is copied into the JSON artefact; the
  plan text promises only tokens. Same exposure as the PNG, but a second artefact.
  `capture_json["data"]["v"]` also carries pre-filled values the policy keeps (method `keep`).
* **N5** `sensitive_prefilled`: a visible pre-filled input that is neither classified nor detected
  (e.g. a nominee name in an unlabeled field) is not masked — detection limit; worth an explicit
  open item for Task 14 (e.g. mask every pre-filled visible text input in the `original` capture).
* **N6** `persona_values()["age"]` uses `date.today()`, so a persona is identical across scans only on
  the same day (docstring says "identical … in a later scan").
* **N7** Style: `rng_choice` is a public one-line helper used only by exemplars;
  `ATTR_ENTITY[attr or "name"]` fallback in `prepare_fill` hides a should-not-happen case.

## 7. Probe index (`/Users/neelu/.cache/dps-build/reports/probe-m4-G3/`)

| File | Purpose / result |
|---|---|
| `unit.txt` | full suite tail: 1877 passed, 196 skipped |
| `tess.sh` | tesseract wrapper (app image, `--network none`) |
| `ocr_probe.py`, `ocr_probe.json`, `png/` | 9 pages × 5 variants, FF1 + det, OCR results (key names / piece lengths only) |
| `fill_probe.py`, `fill_probe.txt` | 39 fills, `/__received` deltas 0 |
| `masked_copy_probe.py`, `masked_second_copy.png`, `residual_probe.py` | **B1** |
| `frame_probe.py`, `frame_probe_*.png` | **B2** |
| `redos_probe.py` | pattern guard timing |
