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
