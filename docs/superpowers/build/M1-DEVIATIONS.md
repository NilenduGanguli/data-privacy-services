# M1 build — deviations from the plan

Plan: `docs/superpowers/plans/2026-10-11-m1-documents-and-l1-complete.md`.
Contract: `docs/superpowers/plans/2026-10-11-m1-m5-contract.md`.
One section per task: file, what changed, why. Public names and signatures that later tasks rely on
are preserved unless a section says otherwise.

## Task 1 — Dependencies, M1 settings, OCR posture, test service discovery

- `.env.example`: **not changed in this commit.** The build session's permission rules deny every
  tool access to `.env*` paths (the file could be read only through `git show HEAD:.env.example`, and
  neither an edit nor an append was allowed), so the M1 block could not be written. Follow-up for
  whoever holds that permission — append, in the lane-safe form of contract §0 (python-dotenv 1.x and
  compose both expand `${VAR:-default}` in a `.env`; checked with python-dotenv 1.0.1):

  ```bash
  # Documents (M1). OCR goes through the Azure Read v3.2 contract: the compose mock locally,
  # the org gateway (azure-gateway-api, health path /_gw/health) in prod. The mock's host port is
  # DPS_OCR_PORT (contract port 5016), so a lane that moves it moves this endpoint too.
  DPS_OCR_PROVIDER=azure
  DPS_AZURE_VISION_ENDPOINT=http://127.0.0.1:${DPS_OCR_PORT:-5016}
  DPS_AZURE_VISION_KEY=mock-key
  DPS_OCR_HEALTH_PATH=/health
  DPS_MAX_DOCUMENT_MB=20
  DPS_MAX_DOCUMENT_PAGES=200
  DPS_PDF_RENDER_DPI=200
  DPS_REDACT_IMAGE_FORMAT=jpeg
  DPS_SCHEMA_MAX_ROWS=10000
  ```

  The plan's block shows the literal `5016` with a comment; the contract's lane-safe amendment says a
  literal host port in a `.env` example stands for its variable form, so the block above uses it.
- `dps/posture.py`: the https rule for `DPS_AZURE_VISION_ENDPOINT` is
  `urlparse(endpoint).scheme != "https"` instead of `not endpoint.startswith("https://")`. Why: the
  M0 Stellar/COIN rule in the same function compares the parsed scheme, which is case-insensitive
  (`HTTPS://…` passes there); one rule for every egress URL. An empty endpoint still fails it.
- `dps/config.py`: the plan's trailing comments are reflowed onto the line above each field so the
  file stays within 100 columns; `ocr_configured` is wrapped by `ruff format`. Fields, defaults,
  bounds, validators and properties are exactly the plan's.
- `tests/conftest.py`: `from tests.services import require_service` sits with the module's other
  imports instead of being appended above the fixture (ruff `E402`, module import not at top).
- `HANDOFF.md` (§4 configuration table, §6 posture guard, §7 secrets) and `README.md` (develop install
  `.[dev,fpe,docs]`, the `docs` and `uitest` extras) are updated in the same commit. Why: contract §0.6
  (the Wega rule) — a change to env or extras updates HANDOFF.md in the same commit; the plan's Task 1
  file list omits both.
- Count: the plan expects 12 new test cases; its own tests are 13 (config 8 — of which 6
  parametrised — posture 1, services 4). Suite 588 → 601 passed, 2 skipped (the `ner` extra).

## Task 2 — Coded errors and value-free policy labels in the API layer

- `dps/layers/api/common.py` `policy_label` (name collision with M0 as built): M0 already had a
  `policy_label(ref, default)` that every M0 router calls (`detect.py`, `transform.py`,
  `policies.py`), so the routers are **unchanged** and the function is replaced in place with the
  plan's signature and label format. Two rules stay stricter than the plan's snippet, because the
  plan's regex passthrough would audit a caller-typed lowercase string (a policy named `ananya`, or
  `{"preset": "ananya"}`) verbatim, weakening M0's "a name that is no preset is `unknown`" rule:
  - a string policy is audited verbatim only when it is a shipped preset (`preset_names()`); any
    other string with a preset name's shape (`^[a-z0-9][a-z0-9-]{0,63}$`) is `unknown` (M0's label),
    and anything else is `invalid` (the plan's label);
  - an inline policy's label base is its `preset` only when that is a shipped preset, else
    `custom`; the suffix is the plan's `+inline:<sha256(canonical JSON)[:12]>`.
- `dps/layers/api/common.py` `_audit_event`: removed M0's substitution of `report.policy` for the
  `inline` label (and the `_POLICY_LABEL` regex it used). Why: it audited an inline policy's
  caller-chosen `name` whenever that looked like an identifier — part of the inline body, which the
  plan's rule says audit never records. Labels are now computed once, by `policy_label`.
- `dps/layers/api/common.py` `run_engine`: the coded exceptions are raised `from None`, as M0 as
  built does, instead of the plan's `from exc` (no chained engine exception in tracebacks; the
  response and audit are the plan's).
- `tests/test_api.py` (M0 tests changed with their module, contract §0.7):
  `test_rejections_and_engine_errors_are_audited_value_free` now expects `invalid` for the policy
  string `"Ananya Krishnan"`, adds `"ananya"` → `unknown`, uses the inline name `"ananya"` (a name
  M0's label would have audited verbatim) → `custom+inline:…`, and `{"preset": "pci-dss"}` →
  `pci-dss+inline:…`; the test's closing "no `ananya` in any audit event" check covers both.
  `test_run_engine_takes_policy_name_and_sync_or_async_calls` expects the additive
  `"code": "bad_request"`.
- `docs/technical/api-m0.md` (errors carry an additive `code`) and `HANDOFF.md` §5 (the audit
  policy label format) are updated in the same commit; not in the plan's file list.
- Count: `tests/test_api_errors.py` 4 passed; suite 601 → 605 passed, 2 skipped.

## Task 3 — Engine output segments (where each replacement landed)

- `dps/engine/synth/llm_text.py` `placeholder_segments` takes the fill's own replacements and
  per-label methods — `placeholder_segments(text, ph, output, replacements, methods)` — instead of
  the plan's `(text, ph, output, surrogates, locale, anchor)`, which recomputed every slot with
  `surrogates.surrogate(...)`. Why: M0 as built fills placeholders through `_resolve`, which applies
  the **plan's** method per finding (`keep` puts the original back, `redact` writes the mask, a
  surrogate otherwise), and the service always passes the plan. Recomputed surrogates would differ
  from the output at every kept or redacted slot, the rebuild check would fail, and every
  plan-driven synthesis (all document synthesis) would get no segments. `_Fill` gains
  `label_methods` (each label's method) so a segment names the method that actually ran. The
  rebuild safety check ("no segments rather than wrong ones") is kept as the plan wrote it.
- `dps/engine/synth/llm_text.py` `synthesize_text`: M0 as built has several early returns of the
  deterministic text (no rewrite, leak guard, rejected or failed rewrite, invented entities,
  verification), so `info["segments"]` is computed once for the deterministic output and set to
  `[]` only when the model's rewrite is used, instead of the plan's single `if not
  info["llm_used"]` before one final return.
- `tests/test_segments.py`: the plan's `test_ambiguous_templates_yield_no_segments` asserted `[]`
  for `"Template {EMAIL_9} then a.b@example-mail.in"`. Impossible as M0 is built: `_substitute`
  fills only this text's own labels and leaves any other placeholder-shaped string untouched (M0's
  `test_text_that_already_looks_like_a_placeholder_is_kept_and_never_reused`), and labels never
  reuse a placeholder-shaped string already in the text, so the deterministic output always
  rebuilds and its segments are exact (observed: one correct `EMAIL` segment). Replaced by
  `test_placeholder_shaped_text_keeps_exact_segments` (the segment is exact and `{EMAIL_9}`
  survives) and `test_placeholder_segments_refuse_an_output_they_cannot_rebuild` (the safety check,
  called directly with an output that differs → `[]`). Added `test_placeholder_segments_follow_the_plan`
  (a `synthetic-realistic` plan: two `keep` and one `surrogate` segment, each slicing the output)
  and `test_a_used_rewrite_has_no_segments`.
- `tests/test_llm_text.py` (M0 test changed with its module, contract §0.7):
  `test_gateway_failures_and_unavailability_fall_back` compared `info` with an exact dict; it now
  pops and checks `segments` first, then compares the rest unchanged.
- `docs/technical/api-m0.md`: documents `options.return_segments` and the additive `segments`
  response field (M0 routes return `"segments": null` unless asked); not in the plan's file list.
- Count: `tests/test_segments.py` 9 passed (the plan's 6, one replaced, three added); suite
  605 → 614 passed, 2 skipped.

## Task 4 — Document models, layout geometry, document metrics and log fields

- `dps/layers/documents/models.py`: `PageBox`, `PageSummary`, `RedactionReport` and `DocumentInfo`
  gained one-line class docstrings (project rule: Google-style docstrings on public names); the
  `PageFinding.out_span` comment sits on one line. Fields, types and defaults are the plan's.
- `dps/layers/documents/errors.py`: the module docstring carries the plan's list of M1 codes and
  statuses, so later tasks find them next to `DocumentError`.
- `dps/observability.py`: the third-party logger caps are a module constant
  (`_THIRD_PARTY_LEVELS`, same loggers and levels as the plan) applied at the end of
  `configure_logging`. Before widening `_SAFE_FIELDS`, an AST scan of every `logger.*(...)` call in
  `dps/` confirmed that no M0 call site passes any of the 18 new field names, so the extension lets
  no M0 value through.
- No `docs`/`ocr`-marked tests exist yet (Tasks 1–4 add none, and compose has no `azure-ocr-mock`
  until Task 19), so the M1 integration gate `pytest -q -m "docs or ocr"` selects nothing at this
  point (630 deselected).
- Count: `tests/documents` 12 passed, `tests/test_observability.py` +2; suite 614 → 628 passed,
  2 skipped.

## Task 5 — PDF normalisation, text layer and serialised rasterisation

- No code deviation: `dps/layers/documents/render.py`, the PDF part of
  `dps/layers/documents/extract.py`, `tests/documents/fixtures.py` and `tests/documents/test_pdf.py`
  are the plan's code (ruff format leaves them unchanged).
- Step 2's expected failure: the run fails with `ModuleNotFoundError: dps.layers.documents.extract`,
  not `…render` as the plan says, because `test_pdf.py` imports `extract` before `render`. Same cause
  (the modules do not exist yet).
- Count: `tests/documents/test_pdf.py` 12 passed (also with `-W error::DeprecationWarning`);
  suite 628 → 640 passed, 2 skipped; `-m "docs or ocr"` selects 12.

## Task 6 — DOCX, TXT, CSV and image extraction; format sniffing

The plan's code is used, with five robustness/security fixes found by probing it with crafted
inputs before committing. Each has a test in `tests/documents/test_extract.py` (12 added to the
plan's 15) that failed against the plan's code for the reason named.

- `dps/layers/documents/extract.py` `extract_csv` — **`csv.Sniffer` replaced by `_sniff_delimiter`.**
  The Sniffer's quote regexes backtrack quadratically on crafted text (cubically on some): a 64 KB
  upload of `", ' "` repeated pinned a worker thread for more than five minutes (killed), 16 KB took
  4.7 s. `_sniff_delimiter` parses the first ≤ 51 non-blank rows of the 64 KB head with the C `csv`
  parser once per candidate (`, ; tab |`), keeps the candidate whose most common row width (≥ 2) is
  the most consistent (ties: wider, then that order), and falls back to `,` — the plan's fallback
  (`csv.excel`) when nothing is detected. Linear: the 64 KB sample now takes 4 ms. Reading uses the
  excel dialect with the detected delimiter, so two Sniffer side effects are gone: a sniffed
  `skipinitialspace` no longer strips the space after a delimiter (cells keep it; header names are
  stripped as before), and `'` is not detected as a quote character. `csv_delimiter` and
  `ExtractedDocument` are unchanged. Test: `test_csv_delimiter_detection_is_linear_on_crafted_input`
  (< 1 s), plus `test_csv_delimiters_are_detected` (all four delimiters, single column → `,`).
- `extract_csv` — **duplicate header names**: the plan's `while name in header: name += "_2"` is
  cubic in the number of duplicates (list scans × growing names; 12 289 duplicate columns from the
  crafted sample took 38 s). Now a set plus a per-name counter: `a, a_2, a_3, …` instead of
  `a, a_2, a_2_2, …` (the plan's first duplicate, `name_2`, and blank → `column_N` are unchanged, so
  the plan's test holds). Test: `test_csv_duplicate_header_names_are_numbered_in_linear_time`.
- `extract_csv` — a `csv.Error` while reading (a field over `csv.field_size_limit()`, 131 072
  characters) escaped as an uncoded exception (a 500). It is now `corrupt_document` (422) naming
  the line and the limit, raised `from None`. Test: `test_csv_parse_errors_are_coded`.
- `_looks_like_text` decoded the 64 KB head strictly, so a UTF-8 text whose multi-byte character
  straddles byte 65 536 (two in three Devanagari texts over 64 KB, when the upload has no `.txt`
  name or `text/*` type) was refused as `unsupported_format`. It now decodes the head with an
  incremental decoder (`final=False`). The head size is the constant `SNIFF_BYTES`. Test:
  `test_sniff_text_with_a_multibyte_character_across_the_sample_end`.
- `extract_docx` — a part python-docx does not model (footnotes, endnotes) that is not well-formed
  XML raised `lxml.etree.XMLSyntaxError` (a 500); now `corrupt_document`. The XXE-safe parser is the
  plan's; `test_docx_parts_never_resolve_external_entities` pins that a `SYSTEM` entity in such a
  part is not resolved. Test: `test_docx_unmodelled_parts_are_read_and_malformed_ones_refused`.
- `extract_image` — `Image.open(..., formats=("PNG", "JPEG"))`: only the two formats
  `sniff_format` admits are decoded, so a caller passing `fmt="image"` cannot reach Pillow's other
  decoders (a GIF is `corrupt_document`). A fuzz run (3 000 mutated PNG/JPEG files) found Pillow
  raising `SyntaxError` for broken PNG chunks (an uncoded 500 whose message held file bytes); the
  caught tuple adds `SyntaxError` and `EOFError`. Tests: `test_only_png_and_jpeg_are_decoded`,
  `test_broken_png_chunks_are_coded`.
- Count: `tests/documents/test_extract.py` 27 passed (the plan says `15`; its tests are 15 cases
  with the parametrisation); `tests/documents` 51 passed (also with `-W error::DeprecationWarning`);
  suite 640 → 667 passed, 2 skipped; `-m "docs or ocr"` selects 39.

## Task 7 — OCR client (Azure Read v3.2 contract), readiness, mock integration test

The plan's `dps/layers/documents/ocr.py` and its two test files are used, with three fixes that keep
every name and signature; 10 tests added to `tests/documents/test_ocr_client.py` (the plan's 14 → 24)
failed against the plan's code for the reason named.

- `parse_read_result` **fails closed**. The plan skipped a word whose `boundingBox` was missing,
  short or non-numeric. Such a word never reaches the engine, so a value in it is neither detected
  nor boxed, and the rasterised page keeps showing it while the redaction report passes (the OCR
  re-read only looks for detected originals). Now a word with text but no usable box (8 finite
  numbers, `_polygon`) raises `OcrError("ocr_protocol")` for the page, and so does a result of an
  unexpected shape (`readResults` not a list, `lines`/`words` not lists of objects, a non-numeric
  `width`…) — the plan let those escape as `AttributeError`/`KeyError`/`ValueError` (a 500).
  Words with blank text are still skipped (nothing to box). A non-finite `confidence` becomes
  `None`. Messages are fixed text (no OCR body). Tests:
  `test_results_of_an_unexpected_shape_are_protocol_errors` (5 shapes),
  `test_words_without_usable_geometry_fail_closed` (4 boxes, incl. NaN — Python's `json` accepts
  `NaN`).
- `AzureReadClient._read`: a poll body that is JSON but not an object (a list) raised
  `AttributeError`; now `ocr_protocol` ("poll response was not a JSON object").
- `_retry_after`: `Retry-After: nan` parsed to NaN and reached `asyncio.sleep(nan)`, whose timer
  handle breaks the event loop's heap ordering for every other task; a non-finite value now falls
  back to the default (`inf` too, previously capped to 30). Test:
  `test_non_finite_retry_after_falls_back_to_the_default`.
- Mock container: compose has no `azure-ocr-mock` until Task 19, so the plan's pre-Task-19 command
  ran it, in the lane-safe form: `docker run -d --rm --name "dps-ocr-${COMPOSE_PROJECT_NAME:-dps}"`
  (here `dps-ocr-dps-wt-m1`) `-p "127.0.0.1:${DPS_OCR_PORT:-5016}:5000"
  document_intelligence-azure-ocr-mock:latest` (the cached image; `${DPS_IMAGE_TAG}` names images
  compose builds), then stopped. The test reads the endpoint from `DPS_TEST_OCR_URL` (`ocr_url`).
- Count: `tests/documents/test_ocr_client.py` 24 passed; `test_ocr_mock.py` 1 passed with
  `DPS_TEST_REQUIRE_SERVICES=1`; `-m "docs or ocr"` 40 passed; suite 667 → 692 passed with the mock
  up (691 passed, 3 skipped without it).

## Fix round 1 for Tasks 5–7 (review `M1-G2-review-r1`)

Each fix has tests that failed before it. Public names and signatures are unchanged; the private
helpers named below are new or changed.

- **B1, `dps/layers/documents/ocr.py` `parse_read_result` fails closed on every unknown shape.** The
  Task 7 version still read a `succeeded` body with no `analyzeResult`, `readResults: {}` or `[{}]`
  (no `lines`), or a v4-style `readResult.blocks` body, as a page with **no words and no error**. It
  also dropped a line whose text had no words. Such a page would reach the engine empty, its values
  would stay visible in a redacted raster, and the report would pass. Now each of these raises
  `ocr_protocol`:
  - the result is not exactly one page: `analyzeResult.readResults` must be a one-element list
    holding an object, because DPS sends one page image per call;
  - that page has no `lines` list (a blank page is `"lines": []`, which the mock sends and which
    was checked live), or its `unit` is not `pixel` or `inch`;
  - a line or a word is not an object, or a line's `text` is not a string, or its `words` is not a
    list;
  - a word's `text` is not a string (a dict used to become its `repr`), or it is missing;
  - a line's text is not carried by its words, compared with all whitespace removed. This covers
    `words` that are missing, empty, only blank, or **partial** (`"SSN 123-45-6789"` with only the
    word `SSN`). The partial case goes beyond the review: it is the same failure, a value that
    could never be detected or boxed. Comparing without whitespace accepts scripts without spaces
    (`株式会社` from `株式` + `会社`). Read v3.2 builds a line's text from its words, and so does
    the mock. If a real deployment ever differs, every OCR'd page fails with `ocr_protocol`, which
    is visible; values are never silently kept;
  - `width`, `height` or `angle` is present but is not a finite JSON number. Strings such as
    `"1700"` and `"nan"`, booleans, infinities and integers too large for a float are all refused.
    A missing field still defaults to 0, and Task 9 (N8) cross-checks the size.

  An integer too large for a float (`10**400`, which `json` accepts) no longer escapes as an uncoded
  `OverflowError`. In a box it means "no usable box", which is `ocr_protocol`. In a page field it is
  `ocr_protocol`. As a `confidence` it becomes `None`, like a non-finite confidence. `OverflowError`
  is also in the parser's catch-all tuple. Tests:
  `test_succeeded_results_without_exactly_one_page_fail_closed` (10 bodies),
  `test_line_text_the_words_do_not_carry_fails_closed` (5),
  `test_lines_and_words_that_agree_are_read`, `test_word_text_must_be_a_string` (4),
  `test_page_fields_of_the_wrong_type_fail_closed` (12) and
  `test_numbers_beyond_a_float_never_escape_uncoded`.
- **B1 (optional part), `AzureReadClient._same_origin`.** An `Operation-Location` that the stdlib
  cannot parse used to escape as an uncoded `ValueError`. Examples: port 99999, `:abc`, `[::1`, or a
  tab that `urlsplit` strips so the port becomes `5000.evil.example`. These are now
  `ocr_protocol: Operation-Location is not a valid URL`. The resolved URL is also parsed by httpx,
  and the key is sent only when **both** parsers place it on the endpoint's origin. The method now
  returns that `httpx.URL`, and the poll sends exactly the URL that was checked, so there is no
  re-parse. The review's 19 location payloads give the same pinning: every request goes to the
  configured origin, and the uncoded cases are now coded. The review's IDN full-width-dot case was
  an artefact of its probe: `httpx.Response(headers=str)` cannot encode the header. Sent as raw
  header bytes, as a server sends it, it is refused by the origin check, and the test sends it that
  way. Test: `test_malformed_operation_locations_are_protocol_errors` (5).
- **N5, `AzureReadClient._read`:** the submit loop no longer sleeps after its last attempt, which
  only delayed the `ocr_throttled` error by up to 30 s. Test:
  `test_throttled_submits_do_not_sleep_after_the_last_attempt`.
- Live check against the lane's mock: a blank 800×600 PNG gives 0 words and `unit=pixel`, and three
  200-dpi KYC renders give 16 words each. The integration test `test_ocr_mock` passes.
- **B2, `dps/layers/documents/extract.py` `normalise_pdf`: every pypdf failure is now
  `corrupt_document` (422).** The plan's `_PDF_ERRORS` tuple let 116 of 3,000 fuzzed PDFs (3.9 %)
  escape as uncoded 500s. They raised `AttributeError`, `NotImplementedError` ("Unsupported
  filter") or `IndexError` at the clone, at `page.rotation`, at `transfer_rotation_to_content` and
  at `add_transformation`. Both pypdf stages now catch `Exception` (after `DocumentError`), as the
  pdfplumber stage already did, and name only the exception type:
  `the PDF could not be parsed (AttributeError)`. The tuple is gone, and so is the `PyPdfError`
  import. Re-running the review's fuzz probe on seeds 7, 1234 and 99 (3,000 PDFs each) gives
  0 uncoded failures. Test: `test_malformed_pdfs_are_corrupt_documents_not_uncoded_errors` (3
  deterministic inputs: an unsupported `/Filter` on a rotated page and on a cropped page, and a
  `/Contents` that is a number). It also asserts that no cause is chained.
- **N4 (same commit): `DocumentError` is raised `from None`** wherever `extract.py` and `render.py`
  used `from exc`: the pypdf, pdfplumber, UTF-8, zip, python-docx, Pillow and PDFium paths. pypdf's
  `PdfReadError`, Pillow's `SyntaxError` and `UnicodeDecodeError` quote file bytes in their
  messages, and a chained cause would carry those bytes into any traceback that a future log
  processor kept. Messages are unchanged apart from the type names added above. `render.py` thus
  differs from the plan in that one `raise`.
- **B3, `extract_pdf` bounds the page size (new private `_check_page_sizes`).** A 433-byte PDF with
  one blank 14,400 pt page used to be accepted with `needs_ocr=True`, because blank pages are
  scan-suspect. At 200 dpi it would be rendered at 40,000 × 40,000 px, about 17 GB. Read v3.2 also
  refuses more than 10,000 px a side, so such a page could never be OCR'd. After normalising,
  `extract_pdf` now refuses any page whose pypdfium2 bitmap, `ceil(points × dpi / 72)`, would
  exceed `MAX_IMAGE_SIDE` (10,000). It refuses with `image_too_large` (413), naming the page
  number, the limit and the dpi:
  `page 1 would render over 10000 px a side at 200 dpi; pages are limited to 3600 pt a side`.
  The dpi used is `max(pdf_render_dpi, preview_dpi)`. This goes beyond the review's formula
  (`pdf_render_dpi`), because previews (Task 10) render the same pages at `preview_dpi`, which the
  settings allow up to 150 while `pdf_render_dpi` may be as low as 72. With the defaults (200/72)
  the bound is the review's: 3,600 pt is accepted and 3,601 pt is refused. The refusal takes under
  1 ms and renders nothing. A 3,600 pt page still renders at the full 10,000 px (the review
  measured about 1.1 GB peak RSS). That is the Read limit, and renders stay serialised by
  `PDFIUM_LOCK`. `MAX_IMAGE_SIDE` moves up to the PDF section of the module, with the same name
  and value. HANDOFF.md §4 (the `DPS_PDF_RENDER_DPI` row) states the bound. Tests:
  `test_pages_too_large_to_render_are_refused` (14,400 pt; 3,600 accepted; page 2 of 2 named; a
  rotated page) and `test_the_page_bound_follows_the_highest_render_resolution`. The fuzz re-run
  (3 × 3,000) still gives 0 uncoded failures with the new size walk.
- **N1, `test_csv_delimiter_detection_is_linear_on_crafted_input` now tests its claim.** Its 16 KB
  input (`", ' "` × 4096) takes 0.07 s with the plan's `csv.Sniffer().sniff(text[:65536],
  delimiters=",;\t|")`, so the test passed against the code it was written to reject. The input is
  now `",'"` × 32,768 (64 KB). The plan's call takes 6.7 s on it, and `_sniff_delimiter` takes about
  6 ms, so the 1 s bound fails against the plan and passes now. A correction to the Task 6 entry
  above: the review measured `", ' "` at 1.0 s for 64 KB and 0.07 s for 16 KB, and could not
  reproduce "more than five minutes". The worst measured cases are about 6.5–7 s at 64 KB
  (quadratic). The reason for replacing the Sniffer still holds.

## Fix round 2 for Tasks 5–7 (review `M1-G2-review-r2`)

- **B1-r2: a forged `/Pages /Count` was an uncoded `PdfiumError` (HTTP 500).** pypdf keeps the
  input's root `/Count` when it writes the normalised PDF. PDFium takes its page count from that
  `/Count` (when it lies in 1..2^20−2), while pdfplumber walks `/Kids`. So a one-page PDF claiming
  `/Count 2` made `_check_page_sizes` load PDFium page 2, which raised
  `PdfiumError: Failed to load page.` uncoded. Three changes:
  1. **`PdfRenderer` codes every PDFium failure** (`render.py`). A private `_page(index)` loads
     each page and turns `PdfiumError` into `corrupt_document` ("a PDF page could not be loaded").
     `page_size`, `render` and `image_coverage` all go through it. `render` also codes a failing
     render ("a PDF page could not be rendered"). `image_coverage` also codes a failing
     `get_bounds` ("the images on a PDF page could not be measured"). All are raised
     `from None`.
  2. **`extract_pdf` refuses a page-count disagreement.** When PDFium's count differs from
     pdfplumber's, it raises `corrupt_document` with the message "the renderer and the text layer
     disagree on the page count (R and T): /Count contradicts the page tree". The message holds
     only the two counts. Before, a `/Count` below the tree was refused only by an incidental
     `IndexError` on `coverage[i]`. The check runs after `_check_page_sizes`, so a `/Count` above
     the tree is still refused there, by the guard in (1).
  3. **Beyond the review: `normalise_pdf` settles the page tree** to pypdf's reading of it. On
     clone, pypdf already lists every page it found directly under the root `/Kids`. Normalisation
     now also does three things:
     - it sets `/Type /Pages` on the root;
     - it sets `/Type /Page` on every page;
     - it deletes any `/Kids` or `/Count` that a page carries.

     **Why.** The three parsers decide what a page is by different rules:
     - pypdf reads `/Type` first, then the absence of `/Kids`;
     - pdfminer reads `/Type` only;
     - PDFium reads the absence of `/Kids` only.

     **The attack this closes.** Build a tree of an untyped page X (holding an SSN) and a page Y
     typed `/Page` but carrying `/Kids []`, with `/Count 1`. Before this fix it was *accepted*:
     PDFium rendered X while the text layer read only Y, so the SSN was in the raster and was
     never scanned. The count check in (2) did not catch it, because both sides counted 1. Now
     every parser sees both pages:
     - with `/Count 1` the PDF is refused by (2);
     - with `/Count 2` it is accepted, and page by page PDFium's own text equals the text layer.

     The root's `/Count` is deliberately *not* rewritten. A count that contradicts the tree is
     refused, as the review asked. Repairing it would be the lenient alternative; it is safe once
     the leaves are settled, if real traffic needs it. `/Count 0`, `-1` or out-of-range values
     make PDFium walk the tree, so they are accepted as before when the tree is consistent.
- **N7 (folded in): a closed renderer refuses every call.** `__len__`, `page_size`,
  `image_coverage` and `render` all raise `corrupt_document` ("the renderer was already
  closed"). Before, the first three reached pypdfium2 with a null handle and raised
  `ctypes.ArgumentError`. `close()` now checks and sets `_closed` under `PDFIUM_LOCK`, so two
  closing threads cannot double-close.
- **Interfaces unchanged:** the `PdfRenderer` methods, `normalise_pdf` and `extract_pdf` keep
  their names and signatures. `_page` and `_require_open` are private.
- **Tests (`tests/documents/test_pdf.py`, +6):**
  - `test_a_page_count_that_contradicts_the_page_tree_is_a_corrupt_document`:
    - `count-above-the-tree` (1 page, `/Count 2`) failed uncoded before;
    - `count-below-the-tree` (3 pages, `/Count 1`, the 3rd 14,400 pt) passed before only through
      the `IndexError`.
  - `test_renderer_page_loads_that_fail_are_corrupt_documents`
  - `test_a_closed_renderer_refuses_every_call`
  - `test_the_renderer_and_the_text_layer_read_the_same_pages` (uncoded `PdfiumError` before)
  - `test_a_page_rendered_but_never_read_is_refused` (accepted before)

  The helper `_one_page` now builds on a general `_raw_pdf(*objects)`.

## Task 8 — True redaction: burned-in boxes, image-only rebuild, output inspection

The plan's `dps/layers/documents/redact.py` and `tests/documents/test_redact.py` are used, with one
strengthening of the inspection; names and signatures are the plan's.

- `inspect_output_pdf` also counts text-showing operators inside **Form XObjects** that a page's
  resources name (new private `_form_text_operators`: recursive through each form's own
  resources, each object read once, depth ≤ 8). Why: the plan scanned only each page's content
  stream, so text drawn by a Form XObject that the page merely calls with `Do` was invisible to
  it, and pypdf's `extract_text` also misses such text when the form carries no font resource
  (checked: a form drawing `(hidden) Tj` passed the plan's inspection as `clean`). DPS's own
  output has image XObjects only, so this costs nothing for it and makes the structural check
  prove what its docstring claims. Test:
  `test_inspection_reads_text_operators_inside_form_xobjects` (fails against the plan's code).
- `PageRedaction`, `RedactedOutput` and `OutputInspection` gained one-line class docstrings
  (project rule: Google-style docstrings on public names).
- Tests added beyond the plan's 8: `test_inspection_flags_a_text_layer_and_annotations` (the
  inspection is not vacuous: a reportlab text PDF has text characters and operators, the annotated
  fixture has one annotation), the Form XObject test above, and
  `test_only_pdf_and_image_documents_are_redacted` (`redaction_unsupported_format` for a DOCX).
- Count: `tests/documents/test_redact.py` 11 passed (the pdftoppm cross-check runs: poppler is
  installed here); suite 741 → 752 passed, 3 skipped.

## Task 9 — The DocumentPipeline

The plan's `dps/layers/documents/__init__.py` and `tests/documents/test_pipeline.py` are used (all
16 plan tests pass, including both reveal round trips, against the real M0 engine). Changes, each
with a test that fails against the plan's module (checked by swapping the plan's module in: 6
failed, 17 passed):

- **Review N8 (owned by this task): the OCR page size is cross-checked against the image sent.**
  New private `_check_ocr_size(result, size, dpi)`: a `pixel` page must be the PNG's size within
  1 px; an `inch` page × the image's dpi within 1 % (≥ 1 px). Otherwise `OcrError("ocr_protocol",
  "the OCR page size differs from the image sent")`, which the pipeline reports as it reports
  every OCR failure: 503 `ocr_unavailable` naming the code (`… (ocr_protocol)`). Why: a gateway
  that rescales images returns words in another pixel space and every box would land in the wrong
  place while the report passed. Both the OCR stage and the OCR re-read check it.
  `_page_png` returns the image size as a third element (private helper; `_png_size` reads a PNG
  header for image uploads). Verified against the lane's mock: it reports exactly the PNG size
  (1700 × 2200 for a 200-dpi letter page). Tests:
  `test_an_ocr_page_size_other_than_the_image_sent_is_refused`,
  `test_the_reread_refuses_a_page_size_other_than_the_image_sent`.
- **Review §5 escalation (small-image PII on a text page) — provisional decision, flagged for
  the owner.** The plan OCRs a text page only when images cover ≥ `ocr_image_coverage` (0.25), so
  an ID photo or stamp on a KYC form page was rasterised verbatim into a "passed" redacted PDF.
  Now, **only when a redacted PDF is requested** with `ocr=auto` and OCR configured, every page
  with any image (`image_coverage > 0`) is OCR'd too (it becomes `hybrid`: text-layer words win,
  OCR adds what lies in the image). When such a page is not OCR'd (OCR unconfigured, or
  `ocr=never`) the response carries the new warning code `images_not_ocr_read` (the redaction is
  still produced, as an unconfigured deployment already gets `redaction_not_ocr_verified`).
  Detect, obfuscate and synthesize without `redacted_pdf` are unchanged (no extra OCR calls).
  Cost: one OCR call per image-bearing page of a redaction request. The stricter alternative
  (refuse `redacted_pdf` when an image-bearing page cannot be OCR'd) is a one-line change in
  `_redact` if the owner prefers it. Tests: `test_a_redacted_pdf_reads_pages_with_small_images`,
  `test_unread_images_in_a_redaction_are_a_warning` (2 cases), and the control
  `test_small_images_are_not_ocr_read_without_a_redaction`. Fixture: `small_image_pdf()`.
- `locate_findings` numbers CSV findings in row order: the plan sorted by the path string, which
  puts `$[10]` before `$[2]` (new private `_document_order`: row index, path, offset). Test:
  `test_csv_findings_are_numbered_in_row_order` (12 rows).
- `_ocr_reread`: the OCR haystack skips words that normalise to nothing (`-`, `|`), so a blank
  token cannot split a multi-word original and hide a hit (strictly more hits, never fewer).
- `__all__` lists the module's public names.
- `tests/documents/fixtures.py`: `FakeOcr.read` reports the size of the PNG it was sent (as
  Read v3.2 does) instead of 0 × 0, which the N8 check refuses; `small_image_pdf()` added.
  `FakeOcr` had no other user.
- Count: `tests/documents/test_pipeline.py` 23 passed (the plan's 16 + 7); suite 752 → 776
  passed, 2 skipped (lane OCR mock up). Live probe against the mock (not committed): scanned PDF,
  PNG, small-image and hybrid PDFs all redact with `ocr_checked: true`, `ocr_hits: 0`; the hybrid
  merge adds no duplicate words (16 = the text layer's 16).

## Task 10 — Documents API, app wiring, OCR readiness loop

The plan's router, tests and `create_app` changes are used, adapted to M0's `app.py` as built.

- `dps/app.py` `_BodyLimit`: **M0's pure-ASGI middleware is extended** instead of the plan's
  `BaseHTTPMiddleware` replacement. The plan's version checked only `Content-Length`, which would
  have dropped M0's streaming check (a chunked body has no length; `test_body_limit_by_header_and_by_stream`
  pins it). The middleware takes `max_bytes` and `document_max_bytes` (the plan's names), picks the
  limit by path (`/api/v1/documents/*` → `max_document_bytes + DOCUMENT_MULTIPART_OVERHEAD`), and
  both refusals now carry `"code": "payload_too_large"`: the `Content-Length` answer adds the code
  to M0's body, and the streaming refusal raises `CodedHTTPException(413, "payload_too_large", …)`
  instead of M0's uncoded `StarletteHTTPException` (FastAPI re-raises it from multipart parsing as
  it does from JSON parsing; checked with a chunked 4 MB multipart upload in the test and with
  `curl -H "Transfer-Encoding: chunked"` against a live uvicorn). The detail text is M0's.
- `dps/app.py`: the startup probe and the loop both go through a private `_refresh_ocr`, which
  catches any exception from a probe, marks the `ocr` component `{"ok": false, "detail": "error"}`
  and logs `ocr_probe_error` with the exception's class name only. Why: the plan wrapped the loop
  but called `refresh_ocr_readiness` bare at startup, so a probe that raised (a client double, an
  unexpected `httpx` error type) would have failed the app's startup although OCR is never
  required. Test: `test_a_probe_that_raises_at_startup_does_not_stop_the_app`.
- `dps/app.py` lifespan: `api-core` is set to `stopping` inside the `finally` that also cancels the
  probe loop and closes the OCR client (the plan's block, with M0's shutdown line moved into it).
- `dps/layers/api/routers/documents.py` `_run`: the `request` JSON is parsed before
  `run_engine` (to label the policy) but its refusal, and the upload read, are raised **inside**
  `run_engine`'s call. Why: M0 audits every outcome, rejections included (`payload_from` runs
  inside the call); the plan's router parsed and read outside it, so an invalid `request` field
  or an oversized file was neither audited nor counted. The audited policy is `-` when the request
  could not be parsed (its policy is unknown). Test: `test_rejections_are_audited_value_free`.
  Response models and route functions gained one-line docstrings.
- `Dockerfile` (not in the plan's Task 10 file list; Task 19 plans the same line): the runtime
  installs `.[fpe,docs]` (`.[fpe,docs,ner]` under `WITH_NER=1`). Why: `dps.app` now imports the
  document pipeline at startup (the plan's code does), so an image without the `docs` extra could
  no longer start — the lane's compose stack would have been broken from this commit until Task 19.
  Checked: `docker compose build app` (image `data-privacy-services:m1`), the container reaches
  `healthy`, `/readyz` shows `ocr: unconfigured`, and inside the container a 2-page text PDF is
  obfuscated with a redacted PDF (`passed`, 0 text characters, no SSN in the PDF or the result,
  2 previews) while a scanned PDF answers 200 with `ocr_unconfigured` on detect and 503
  `ocr_unconfigured` on redaction (compose has no OCR until Task 19).
- `HANDOFF.md` (contract §0.6): §1/§2 the `docs` extra in the image, §4 the per-route body limits
  and the redaction OCR rule of Task 9 (`images_not_ocr_read`), §5 the `ocr` readiness component,
  the M1 metrics and the document audit modes and outcomes.
- Tests added beyond the plan's 9 (+1 `ocr`): `test_body_limits_are_per_route` (JSON route still
  1 MB, a 1.5 MB upload reaches the pipeline, a chunked 4 MB upload is a coded 413),
  `test_rejections_are_audited_value_free`, `test_lifespan_probes_ocr_and_closes_the_client`,
  `test_a_failed_probe_never_stops_the_loop` (a probe that raises, then `down`, then `up`),
  `test_a_probe_that_raises_at_startup_does_not_stop_the_app`. `tests/test_api_documents_ocr.py`
  also `importorskip`s `pdfplumber` (its fixtures need the `docs` extra).
- Mock: compose has no `azure-ocr-mock` until Task 19, so the plan's pre-Task-19 command ran it in
  the lane-safe form (`docker run -d --rm --name dps-ocr-dps-wt-m1 -p
  "127.0.0.1:${DPS_OCR_PORT:-5016}:5000" document_intelligence-azure-ocr-mock:latest`); the test
  reads `DPS_TEST_OCR_URL`.
- Count: `tests/test_api_documents.py` 15 passed, `tests/test_api_documents_ocr.py` 1 passed;
  `DPS_TEST_REQUIRE_SERVICES=1 pytest -q -m ocr` 2 passed; suite 776 → 791 passed, 2 skipped
  (mock up).

## Fix round 1 for Tasks 8–10 (review `M1-G3-review-r1`)

**B1: a redacted PDF could pass, `ocr_checked: true`, while still showing SSNs and card numbers
whenever the PDF's text layer disagreed with what the page draws.** Reproduced first with the
review's probes against the lane mock (real tesseract): a searchable scan with an OCR-error
invisible layer, one with a "Specimen" layer, and a text PDF whose `/ToUnicode` maps digits to `X`
all passed with values visible. The plan's design caused it in two places ("text-layer words win";
the re-read looks only for the originals of detected findings, and only on boxed pages). Both are
changed; interfaces (names and signatures) are unchanged.

- **(a) Verification: `_redact` / `_ocr_reread` (`dps/layers/documents/__init__.py`).**
  - When OCR is configured and `ocr_verify` holds, **every** page of the produced PDF is re-read,
    not only boxed ones. `RedactionReport.ocr_pages` is now the page count.
  - New private `_residual`: the engine runs over each page's re-read text with the request's policy,
    locale and mode, through the public `PrivacyEngine.plan`. Any finding whose method is not `keep`
    fails the request with 422 `leakage`, and nothing is returned. This also catches values that only
    the raster holds (vector or outlined text, broken maps).
  - The needle check stays. It is now **document-wide**: each page's re-read is searched for the
    redacted originals of every page. Before, it searched only for that page's own. Why: the
    customer's name, boxed on page 1, shown on page 2 where the text layer does not say it, has no
    cue there, so only a needle sees it.
  - The `leakage` message now names the count, the entity types and the pages, never a value:
    "an OCR re-read of the redacted PDF still shows N value(s) the policy redacts (CARD_PAN, SSN)
    on page(s) 1; nothing was returned". `RedactionReport.ocr_hits` is 0 whenever a report is
    returned, since any hit raises. Its model comment is updated.
  - **Text the engine checks after the re-read (`_reread_text`).** The page is laid out as an OCR
    page with two changes:
    - words OCR read inside a painted box (half their area or more, the `chars_under_boxes` rule)
      are dropped;
    - each box reads as one `█` (private `_BOX_MARK`) on its OCR line.

    **Why.** It came from a false positive found in the real-mock sweep. Tesseract read the
    redacted SSN box on a text-layer page as `RR`, and with the name boxed, the weak lead-in
    `Customer:` named the next line's `SSN RR` a `PERSON_NAME`. Without the mark, every text-layer
    fixture failed with `leakage`: 70 of the 168 sweep combinations. A box read as nothing has the
    same effect when the next line starts with capitalised words (`Customer:` followed by
    `Premier Banking`). A box is solid black, so nothing under it is readable, and the mark keeps a
    cue from reaching past a redacted value.
- **(b) Hybrid merge: `merge_ocr_words` and `group_lines` (`dps/layers/documents/layout.py`).**
  - **Grouping.** OCR and text-layer words that hold the same place are grouped transitively:
    boxes overlapping `OCR_OVERLAP` of the *smaller* box. Before, the test was coverage of the OCR
    word alone. Transitive grouping makes a word split differently on each side (`Kri`+`shnan,`
    against `KRISHNAN`) one group.
  - **Duplicates.** A group's OCR words are dropped only when both sides read the same text,
    normalised with the engine's `normalise`.
  - **Conflicts.** Otherwise the OCR line holding the conflicting word is kept **whole**, with its
    line hint, as a line of its own. Why whole lines: a card number's four groups next to the text
    layer's misread groups would interleave word by word, and the engine does not see a card number
    without its "Card" cue (`4111 1111 1111 1111 09/28` alone has no finding).
  - **Other OCR words.** OCR words over no text-layer word join the text-layer lines, as before.
  - **`group_lines`.** It now groups hinted words by hint and clusters unhinted words by centre in
    the same call. Before, a mixed list was clustered entirely. Pure OCR and pure text-layer pages
    are unchanged.
  - A text-layer index of 24 pt row bands keeps the merge near-linear on dense pages.
- **Measured against the lane mock (real tesseract), `options.redacted_pdf: true`:**
  - The review's probes: `garbled` and `specimen` now redact SSN, card, e-mail and name. An
    independent OCR of the output reads none of them, and the report passes. The control `none`
    passes unchanged. `probe-m1-G3-tounicode.py` fails closed: 422 `leakage` (CARD_PAN, SSN).
  - A sweep of 12 fixtures × 7 policies (default and six presets) × {obfuscate, synthesize}:
    - 154 of 168 pass, with no false-positive `leakage`. The fixtures are text 1 and 3 pages,
      scanned 1 and 2 pages, hybrid, small image, PNG, rotated, cropped, annotated, garbled and
      specimen.
    - The other 14 are `cropped_pdf`, which crashes **before** the re-read on HEAD too (see the
      next section).
    - The hybrid and small-image fixtures keep exactly the text layer's 16 words, because tesseract
      agrees with them, so no extra lines are added.
  - In the rebuilt image (`data-privacy-services:m1`, lane `dps-wt-m1`), the pipeline was run against
    the mock at `host.docker.internal`. It passed with nothing visible afterwards for text, scan,
    garbled and specimen.
- **Costs and side effects (for the owner):**
  - A redaction request now makes one OCR call per output page, where it used to make one per boxed
    page. It also runs one engine pass per page, on top of the OCR'd input pages.
  - On a hybrid page whose OCR disagrees with the text layer, the canonical text, and so the
    obfuscated output, holds both readings of the disagreeing lines.
  - The re-read's detections increment `dps_findings_total` as any `PrivacyEngine.plan` call does.
- **Open (not measurable here): NER.** With the `ner` extra (`DPS_NER_ENABLED`), spaCy may label
  re-read text differently from the original. A context-dependent label on words the engine already
  judged would then fail closed. That is safe, but it is a false `leakage`. A possible refinement,
  if real traffic shows it: count a residual finding only when its value never appeared in the
  canonical text outside redacted spans.
- **Tests** (each B1 test fails against the pre-fix modules; checked by running them against a HEAD
  export of `dps/`, 8 failed):
  - `tests/documents/test_layout.py`:
    - `test_merge_ocr_words_keeps_what_the_text_layer_reads_otherwise`;
    - `test_merge_ocr_words_keeps_a_line_an_overlay_hides`;
    - `test_merge_ocr_words_drops_duplicates_however_the_words_split` (a guard: different
      segmentation, case or punctuation adds no duplicates).
  - `tests/documents/test_pipeline.py`:
    - `test_a_searchable_scan_is_redacted_by_what_it_shows[garbled|specimen]`;
    - `test_values_the_text_layer_cannot_read_fail_the_reread[obfuscate|synthesize]` (broken
      `/ToUnicode`);
    - `test_every_page_of_a_redacted_pdf_is_reread`;
    - `test_a_redacted_value_another_page_shows_is_a_leak`;
    - the control `test_the_reread_passes_a_redaction_that_hides_every_value`;
    - `test_what_ocr_reads_inside_a_box_is_not_a_leak[2 cases]` (it failed against the
      intermediate state without box marks, in 3 of its 4 sub-cases).
  - Fixtures (`tests/documents/fixtures.py`): `searchable_scan(hidden)`, `GARBLED_LAYER`,
    `SPECIMEN_LAYER`, `broken_tounicode_pdf()`, and `ScreenOcr`. `ScreenOcr` is an OCR double that
    returns the drawn words less those a black box hides in the image it is sent, and optionally
    reads a box as text (`box_reads_as`).
- **Count:** suite 791 → 803 passed, 2 skipped (`ner` extra; lane OCR mock up).
  `DPS_TEST_REQUIRE_SERVICES=1 pytest -q -m "docs or ocr"` gives 109 passed, and `-m ocr` gives 2
  passed.
- Review nits N1, N3–N8 are not addressed in this round. N2 is partly covered by (a): a value that
  a misplaced box leaves whole on the page is found. A value it leaves only in part is still not
  found.

### Beyond the review: text outside a crop box crashed redaction (found by the B1 sweep)

- **Symptom.** `cropped_pdf` keeps its first line in the text layer, above the crop box: it is
  detected but never drawn. Its finding box came out *inverted* (`y1 < y0`), for two reasons:
  - `BBox.padded` clamped each side on its own;
  - the box lay wholly above the page.

  Two failures followed:
  - `/documents/detect` returned that inverted box;
  - with `redacted_pdf`, `paint_boxes` raised an uncoded `ValueError` (HTTP 500).

  This was present on HEAD before this round, with or without OCR (Tasks 4 and 8).
- **Fix.**
  - `BBox.padded` (`models.py`) clamps every coordinate to the page and never lets a far side pass
    its near side. A box off the page becomes an empty box on the page's edge.
  - `paint_boxes` (`redact.py`) skips boxes without area. They cover nothing that is drawn, while
    PIL would have painted a 1-px line.
  - `RedactedOutput.boxes`, and so `RedactionReport.boxes` and `dps_redacted_boxes_total`, counts
    only the boxes actually burned in.
  - The finding keeps its (empty) box, so it is "located" and `redaction_unlocated` does not fire.
    The redacted raster cannot show text outside the crop box.
- **Tests (each fails before the fix):**
  - `test_models.py::test_a_box_off_the_page_pads_to_an_empty_box_on_its_edge`;
  - `test_redact.py::test_empty_boxes_paint_nothing`;
  - `test_pipeline.py::test_text_outside_a_cropped_page_does_not_break_redaction`.
- **Count:** suite 803 → 806 passed, 2 skipped. `-m "docs or ocr"` gives 111 passed. The real-mock
  sweep (12 fixtures × 7 policies × 2 modes) now passes 168 of 168.

## Task 11 — Generator specs and the deterministic executor

- `dps/engine/synth/schema_gen.py`, `ENTITY_TEMPLATES`: **every entity in `ENTITY_GENERATORS` now has
  a template.** The plan's dict left ten without one (`DATE_OF_BIRTH`, `GENDER`, `NATIONALITY`,
  `AGE`, `CARD_EXPIRY`, `IBAN`, `IP_ADDRESS`, `MAC_ADDRESS`, `URL`, `ORG_NAME`) and fell back to `""`.
  The M0 surrogate engine as built returns a blank original unchanged
  (`SurrogateEngine.surrogate`: `if not original.strip(): return original`), so those fields
  came out empty: the plan's own `validate("IBAN", …)` assertion failed on every row. Added:
  `YYYY-MM-DD` (unparseable, so the row persona's birth date in ISO form), `Gender`, `Nationality`,
  `age` (no digits, so the age that agrees with the persona's birth date), `MM/YY`,
  `GB00 AAAA 0000 0000 0000 00` (a fresh mod-97-valid GB IBAN), `0.0.0.0`, `00:00:00:00:00:00`,
  `https://host/path` (not `https://example.com/`: the URL surrogate always lives under
  example.com, so that template would "reveal" itself on every draw and fall through to the
  engine's hex last resort) and `Org`. `_generate` reads `ENTITY_TEMPLATES[entity]` (no `""`
  fallback); a test asserts the dict covers the set.
- `ENTITY_TEMPLATES["PHONE"]` is `+ phone` instead of `+1 201 555 0100`. The plan's template is a
  parseable US number, so every row had a US phone whatever the spec's locale; an unparseable
  template makes the engine use the row persona's country (en_IN → `+91 …`, en_GB → the Ofcom drama
  range) in international format. A caller who wants the US layout passes `format`.
- `tests/test_schema_gen.py::test_entity_values_are_rule_valid_and_coherent`: the Aadhaar check is
  "12 digits, starts with `1`, Verhoeff-valid" instead of `validate("AADHAAR", …) is True`. The M0
  surrogate deliberately draws Aadhaar numbers outside issued space (UIDAI never issues 0/1), and
  stdnum's validator refuses exactly those; M0's `test_surrogates.py` asserts
  `validate("AADHAAR", aad) is False`. The plan's assertion was impossible.
- Validation tightened so a spec that validates cannot fail or emit NaN during execution (the plan
  says "all validated before execution"); each case is in `test_invalid_specs_are_refused`:
  - numbers (`decimal`/`normal`/`lognormal` bounds, `weights`, `p_true`) are finite and
    `|x| ≤ MAX_ABS` (1e100): `NaN`/`Infinity` arrive through JSON (`json.loads` accepts them) and
    `uniform(-1e300, 1e300)` overflows to `inf`, which then fails response serialisation (500);
  - `scale` is an integer 0..10 (the plan's `int(p.get("scale", 2))` raised on `"x"` at execution);
  - `lognormal` needs `|mu| ≤ 100`, `sigma ≤ 20` (`exp` overflow); `normal`/`lognormal` need
    `min ≤ max` when both are given;
  - `choice` values and `constant` values are JSON scalars without NaN/infinity; `RuleCase` and
    `RuleConstraint` use `allow_inf_nan=False`;
  - an `entity` `format` must hold a letter or digit (an all-blank format came back blank).
- Order constraints: the plan grouped `sequence`/`integer`/`decimal`/`normal`/`lognormal` as one
  "number" kind and both sortable date formats as one "date" kind. Sorting moves values between
  fields, so mixing produced wrong output: an integer column received a decimal, a `%Y%m%d` column an
  ISO date (and the string sort ordered `2015-…` before `20150101`), and a `sequence` with a prefix
  (a string) next to an integer raised `TypeError` mid-execution (a 500). Now `_sort_class` replaces
  `_ORDER_KIND`: fields must share one class — integers (incl. a bare `sequence` and scale-0
  `normal`/`lognormal`), numbers of one scale, dates of one sortable format, or datetimes; a
  prefixed or padded `sequence` is not sortable; a field may not be named twice. Datetimes are sorted
  as instants (`_sort_key`), not as strings, so different offsets order correctly
  (`test_ordered_datetimes_compare_as_instants`).
- A constraint may not target a `unique` field (order fields, rule target): uniqueness is enforced
  before constraints run, and a constraint would silently rewrite the value (duplicates in a key).
- Module docstring: `AGE` and `CARD_EXPIRY` are relative to today (M0 surrogate behaviour), so
  replays are byte-identical within a day; every other value is a pure function of
  (spec, seed, row). The contract §0 amendment (call `declare_record` before `surrogate`) has
  nothing to declare here — a generated row has no original values, only templates; a comment says
  so at the call site.
- Count: the plan expects 17 tests; the file has 37 (17 plan cases + 5 locales of
  `test_every_entity_generator_produces_a_value`, `test_ordered_datetimes_compare_as_instants`, 14
  new invalid-spec cases). Against the plan's code, 21 of them fail. Throughput on this Mac:
  ≈ 1 875 rows/s for the 16-field test spec. Suite 804 → 841 passed, 4 skipped.

## Task 12 — Structure profiles and spec proposers (heuristic and LLM, P1-safe)

`dps/engine/shapes.py` is the plan's. `dps/engine/synth/schema_profile.py` is the plan's module plus the
fixes below; public names and signatures are unchanged. The plan's 11 tests pass on the plan's code, but a
probe found 14 inputs on which it raised a raw `ValidationError`/`OverflowError`/`TypeError` (an uncoded 500
at the Task 13 routes) and one quadratic path; each fix has a test that fails on the plan's code (25 of the
27 new cases fail there; the two that pass guard behaviour the plan already had).

- **Field names always validate** (`_Namer`, used by all three profilers). The plan built names with
  `_ident` per segment, so `a b` and `a_b` (or JSON Schema `a-b` / `a_b`) became duplicate fields, a sample
  key that is a scalar in one row and an object in another (`a` and `a.b`) made a field nest under a
  scalar, and a dotted key with more than four segments broke `FieldSpec`'s name rule — each a
  `GeneratorSpec` `ValidationError`. A name that cannot be used as is (too deep, colliding, or aliased as
  sensitive) becomes `field_N` (`field_N_2`, … if that is taken). The JSON Schema walk passes raw path
  segments to the namer instead of pre-joined prefixes, and stops descending past `MAX_SCHEMA_DEPTH` (32)
  levels (as does `_flatten` for samples) instead of recursing until `RecursionError`.
- **At most `MAX_FIELDS` (200) fields** — `GeneratorSpec.fields` holds 200 — else `SchemaError`
  `schema_unsupported` (a coded 422) from every profiler, checked before the per-column work.
- **Numbers that no generator accepts are not numbers.** `_finite` (a `TypeGuard`): finite and
  `|x| ≤ MAX_ABS` (Task 11). A sampled `10**400` raised `OverflowError` in `float()`; a JSON `NaN` raised
  `ValueError` in `math.log10`; a JSON Schema `maximum: 1e308` produced a `decimal` the executor refuses.
  `_infer_type` and the sample statistics use it, and so do JSON Schema `minimum`/`maximum`.
- **The heuristic proposer clamps to the executor's limits:** `scale ≤ MAX_SCALE` (DDL `NUMERIC(20,12)`
  gave scale 12); `lognormal` only when `|mu| ≤ 100` and `sigma ≤ 20` (else `decimal`); DDL precision is
  capped at `MAX_DIGITS` = 38 integer digits (`NUMERIC(1000,0)` raised `OverflowError` in `10.0 ** 1000`);
  years are clamped to 1..9999 and zero-padded (`0005-…` became `5-01-01`, not an ISO date). As a last
  resort, `heuristic_spec` turns a field whose proposal still fails validation into short `text` and says
  how many did in `notes` (a count, never a value).
- **JSON Schema keywords of the wrong type are ignored**, not fatal: `minLength: "x"` raised `ValueError`
  in `int()`; `required: [{}]` raised `TypeError` building a set. Only string entries of `required` count;
  `minLength`/`maxLength` must be integers.
- **DDL comments are stripped in one linear pass, quote-aware** (`_strip_comments`, `_body_end`). The
  plan's `re.sub(r"/\*.*?\*/", …, flags=re.DOTALL)` is quadratic on unterminated comments: 60 000
  characters took 2 s, the 100 000-character limit about 6 s, on the event loop (Task 13 also moves DDL
  profiling to a thread). The plan's strip and parenthesis scan also ignored quotes, so
  `DEFAULT '--'` or `DEFAULT '('` made a valid table `ddl_unparsed`.
- **`merge_llm_spec` refuses unusable replies with `ValueError`** (→ `fallback: llm_invalid_spec`, as
  the plan intends): `fields` not a list (`{"fields": 5}` raised `TypeError`, a 500), a reply proposing
  none of the profile's fields (`{}` was labelled `source: llm` while being the heuristic spec), and a reply
  nested deeply enough for `json.loads` to raise `RecursionError`. `notes` that are not a list are ignored
  (`{"notes": 7}` raised `TypeError`).
- **`verify_against_sample` does not look for `GENDER` and `NATIONALITY` values (`SWEEP_EXEMPT`).** The
  `entity` generator draws them from a handful of words, and the M0 surrogate engine already lets them
  equal an original (`surrogates._IDENTITY_OK`: forcing a different value would itself reveal it), so a
  sample with a gender column failed every run with `leakage`. Every other detected type is swept exactly
  as the plan says. **Open:** at volume the sweep still fails healthy runs on other low-entropy types
  (measured on Faker en_IN samples: a 50-row sample with a city column → 100 rows fail 5/5 on `CITY`;
  200 → 1 000 rows fail 5/5 on `CITY`, `DATE`, `DATE_OF_BIRTH`, a few `PERSON_NAME`). That is a policy
  decision (which sample values count as "originals" for a generated dataset) and is left fail-closed;
  see the build report.
- Count: plan 11 tests; the file has 38 (11 + 14 always-valid cases, DDL comments/quotes, linear-time
  comment strip, 3 too-many-fields cases, 6 unusable replies, notes of the wrong type, gender/nationality
  sweep). Suite 841 → 879 passed, 4 skipped.

## Task 13 — Schema API: generator spec and synthesis routes

- `dps/layers/api/common.py` `_audit_event` (not in the task's file list): **the plan's
  `/schema/synthesize` lost its audit event.** `SchemaSynthesizeOut` has a `report` (a `SchemaReport`),
  so `_audit_event` took the engine-report branch and raised `AttributeError` on
  `report.findings_total`; `_audit` swallowed it (`audit_write_failed`), so every successful synthesis
  went unaudited. The engine-report branch now requires `findings_total`; any other report contributes
  only its `verification.passed`, so the audit carries the sample sweep's verdict
  (`test_schema_routes_are_audited`, which fails on the plan's code).
- `dps/layers/api/routers/schema.py`:
  - `ddl` and `sample` carry no pydantic `max_length`. With it, an oversized DDL or sample was a generic
    422 validation error and the plan's own 413 codes `ddl_too_large` / `sample_too_large` were
    unreachable. `parse_ddl` already checks `MAX_DDL_CHARS`; `_profile` checks `MAX_SAMPLE_ROWS` before the
    leaf count (`test_oversized_inputs_are_coded_413`). The body-size guard still bounds both.
  - `seed` is used when it is not `None`, not when it is truthy: `{"seed": {}}` silently drew a fresh
    seed; it is now 422 `invalid_seed` (`test_an_empty_seed_is_invalid_not_ignored`).
  - JSON Schema and DDL profiling run in a worker thread like sample profiling (a 100 000-character
    DDL is parsed on the event loop otherwise), and so does the sample sweep: at the limits (1 000
    sample rows × 5 sensitive fields against 10 000 generated rows) `verify_against_sample` took 7.5 s
    on this Mac.
  - `info` is annotated `dict[str, Any]` (mypy inferred `dict[str, object]` from the caller-spec branch).
- `HANDOFF.md` §5: the schema routes' audit modes, policy label and the `genspec` LLM purpose.
- Extra tests beyond the plan's five: audit events, coded 413s, empty seed, replaying the returned spec
  with the returned seed (same rows, no sweep without a sample), and a fail-closed `leakage` 422 that
  does not echo the leaked values. Three of them fail on the plan's router.
- **Open (see Task 12):** with a sample, the sweep fails healthy runs at volume on low-entropy types —
  on this Mac a 1 000-row sample of account numbers, segments, balances and an opening date gave
  422 `leakage` for 10 000 rows (generated dates coincide with sampled ones), and Faker-like names
  coincide too. Fail-closed, as the plan specifies; a policy decision is needed.
- Count: 10 tests (plan 5). Suite 879 → 889 passed, 4 skipped. Route timings on this Mac: a 10 000-row
  synthesis from a spec answers in 0.36 s (823 KiB of JSON).

## Task 14 — Entities route, recognizer test route, allow-lists, preset summaries

- `dps/engine/detect/custom.py`: each compiled pattern and deny-list matcher carries its recognizer's
  allow-list (`frozenset`), instead of the plan's `self._allow` keyed by recognizer **name**. Nothing
  makes recognizer names unique, so with two recognizers named alike the plan's dict let one's
  allow-list silence (or fail to silence) the other's hits
  (`test_allow_lists_are_per_recognizer_even_with_one_name`). The allow-list is matched exactly
  (case-sensitive) against `m.group(0)`, for pattern and deny-list hits alike, as the plan says; blank
  entries are refused like blank deny-list entries (one validator for both). The module docstring
  documents the field. Additive: an inline policy with recognizers now dumps `allow_list: []`, so its
  `config_hash()` differs from M0's for the same input (no hash is persisted before M2).
- `dps/engine/policy.py` `REVERSIBLE_METHODS` also holds `external_tokenize`, which the method registry
  marks reversible (`Method("external_tokenize", True, …)`); the plan's set omitted it.
  `transform/methods.py` imports `policy.py`, so the set cannot be derived there; a test keeps the two
  equal (`test_reversible_methods_match_the_method_registry`). No shipped preset uses the method, so
  every preset's `reversible` value is the plan's.
- `dps/layers/api/routers/entities.py`:
  - `GET /entities` also has the rate-limit dependency (M0's `GET /policies/presets` has it) and reads
    each entry through `entity_info`, so a built-in entry wins even if the open registry was mutated
    (the taxonomy's own rule); the plan sorted `TAXONOMY.values()` directly.
  - `POST /policies/test-recognizer` also refuses more than 50 patterns (`invalid_recognizer`): the
    plan bounds the text and each pattern because Python's `re` has no timeout, but not how many
    patterns run over the text.
- `dps/layers/api/routers/policies.py`: the presets route's return type is `list[dict[str, Any]]`.
- Extra tests: the allow-list on deny-list hits and blank entries, per-recognizer allow-lists,
  registry parity, the pattern cap with value-free errors, and registered custom types listed by
  `/entities` with `builtin: false`.
- **Open:** the taxonomy registry is process-wide (M0), so `GET /entities` lists custom types (and
  their `custom:<recognizer name>` descriptions) registered by any tenant's policy — names only, never
  values; the plan asks for "the live taxonomy incl. registered custom types". A pathological regex
  (catastrophic backtracking) can still hold a worker thread on this route, as on M0's `/detect` with
  an inline recognizer.
- Count: plan 7 new tests (custom 1, policy 1, entities 5); added 5 more. Suite 889 → 901 passed,
  4 skipped; `tests/test_api.py` (M0) unchanged and green.

## Fix round 1 for Tasks 11–14 (review `M1-G4-review-r1`)

Each blocking finding has a test that fails on the reviewed code (be188d7) and passes after the fix.
Public names and signatures are unchanged.

**B1: the model could overwrite a detected field through a constraint** (`merge_llm_spec`,
`dps/engine/synth/schema_profile.py`). The merge kept DPS's generator for every field with an
entity type, but passed the model's `constraints` through, and the executor applies them after
generation: a `rule` on `ssn` made every row `078-05-1120` (an issued SSN) and one on `card` made every
row `4111111111111111`, with `source: llm`.

- New private `_llm_constraints`: the reply's constraints are parsed one by one
  (`TypeAdapter(list[Constraint])`), and a `rule` whose `field` is detected, or an `order` that names a
  detected field (sorting moves values between its fields), is dropped. The spec's `notes` say how many
  (`"N llm constraint(s) dropped: …"`), never which values. Constraints that do not parse, or that fail
  the spec's own checks, are still all dropped with `llm constraints dropped (invalid)`, as before.
  Rules may still *read* a detected field in `when` (they write a model constant into another field).
- Consequence: "detected" means any entity type, as the merge's existing rule does, and sample date
  columns are usually detected as `DATE` by value, so an LLM `order` over two sampled date columns
  (`opened ≤ closed`) is now dropped. The heuristic proposer makes no constraints, so this is no worse
  than the no-LLM path. Allowing `order` over fields whose generator is not `entity` would bring it back;
  that is a product decision, left as is (fail-safe).
- Side effect, intended: an invalid `order` on a detected field (an `entity` generator is not sortable)
  no longer takes the model's valid constraints on other fields down with it.
- Test: `test_llm_constraints_cannot_write_or_reorder_sensitive_fields` (the review's reply plus an
  `order` over detected dates, a kept `rule` and a kept `order`).

**B2 (P1): sampled values reached the gateway as categorical labels and as field names**
(`profile_from_sample`, `render_prompt`, `merge_llm_spec`, `profile_originals`). A column with ≤ 12
distinct letters-only values, each seen twice and none detected, became `FieldProfile.enum`, and
`render_prompt` sent it. The default image has no NER, so a column of first names or of
relationship managers' names passed every test: the review's probe sent `Ananya` … `Krishnan` with
`llm.raw_pii_sent: 0`. A sample key that is a name went through as a field name too.

- **Labels never reach the model.** `render_prompt` drops each field's `enum` and sends
  `enum_size` (the label count) instead, for every source (JSON Schema `enum` values are treated the
  same way, though they are caller metadata rather than data). `FieldProfile.enum` itself is unchanged:
  the heuristic proposer uses it, and `POST /schema/generator-spec` returns it to the caller who sent
  the sample. `SPEC_SYSTEM` says the model gets only the count, and that DPS generates fields with an
  `entity_type` or an `enum_size` itself.
- **An enum field keeps DPS's `choice`** in `merge_llm_spec`, exactly like a detected field (the
  model never saw the labels, so its proposal could only invent them), and B1's constraint filter
  covers enum fields as well (`kept_by_dps` = detected ∪ categorical).
- **Data-like sample keys are aliased in the prompt only.** New `FieldProfile.prompt_alias`
  (`Field(exclude=True)`: never serialised, so neither the prompt nor any response carries it) is set
  by `profile_from_sample` when a key segment is not identifier-like (`_PLAIN_KEY`: a letter or `_`,
  then letters, digits, `_` or `-`, at most 63 characters, and no run of four digits — so
  `Ananya Krishnan`, `José` and account numbers used as keys are aliased; `customer_id`, `first-name`,
  `address_line1` are not). New private `_prompt_names` maps each such field to `field_N` (never equal
  to, or a prefix of, a name sent as is); `merge_llm_spec` maps the reply's field and constraint names
  back, so the generated rows keep the caller's column names. Profile-level aliasing
  (`field_N` in the output, as for keys the detector flags) was not used because it would also rename
  ordinary spaced CSV headers in the output. DDL column names and JSON Schema property names are
  schema metadata and are sent as before.
- `profile_originals` no longer exempts enum labels from the leak guard (the exemption existed only
  because labels were sent). Enum labels themselves are not added to the guard: `normalise` joins the
  whole prompt into one string of letters and digits, so short labels would match inside JSON keys and
  silently push every categorical sample to the heuristic path; the prompt holds no labels by
  construction instead.
- Tests: `test_sample_labels_and_data_keys_never_reach_the_model` (NER off; first names, full names,
  segment labels and a name used as a key; the model's alias maps back and its `choice` for the name
  column is ignored) and `test_json_schema_labels_are_counted_not_sent`. Both fail on be188d7.
- **Open:** a key that is a name *and* identifier-like (`ananya_krishnan`) is still sent; only NER (or a
  key dictionary) could tell. The heuristic `choice` still writes the sampled labels into the generated
  rows, which go back to the caller who sent them; with NER off that includes an undetected
  low-cardinality name column (a product decision: e.g. refuse multi-word labels, or require NER for
  samples).
