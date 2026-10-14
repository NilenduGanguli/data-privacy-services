# Layer 1 API — M1 additions (documents, schema synthesis, taxonomy)

M1 adds documents, schema synthesis, the entity taxonomy and the recognizer test to the M0 API
(`docs/technical/api-m0.md`, which stays authoritative for `/detect`, `/obfuscate`, `/synthesize`,
`/reveal` and the policy routes). It also adds NDJSON streaming, `Idempotency-Key`, local Tesseract
OCR and the opt-in T3 labelling tier. This page describes the routers as built (v0.2.0); the live
schema is at `/api/v1/openapi.json`.

Headers, authentication, tenants and rate limits are M0's. Every error body is
`{"detail": "...", "code": "..."}` (the `code` is new in M1 and stable; the `detail` names counts,
pages, limits and rules, never a value). The examples use the compose stack:
`http://127.0.0.1:${DPS_APP_PORT:-8400}` and `export DPS_API_KEY=dps_local_dev_key_change_me`.

## Routes

| Method | Path | Scope | Purpose |
|---|---|---|---|
| POST | `/api/v1/documents/detect` | detect | findings per page, with boxes on PDF and image pages |
| POST | `/api/v1/documents/obfuscate` | obfuscate | obfuscated text (revealable with the returned key) and, optionally, a redacted PDF |
| POST | `/api/v1/documents/synthesize` | synthesize | synthetic surrogates in the text (CSV: per record) and, optionally, a redacted PDF |
| POST | `/api/v1/schema/generator-spec` | synthesize | a generator spec from a JSON Schema, DDL or a sample (proposed by the LLM from a value-free profile, or by the heuristic) |
| POST | `/api/v1/schema/synthesize` | synthesize | rows from a spec (or from a schema, DDL or sample through a spec), deterministic under a seed |
| GET | `/api/v1/entities` | detect | the built-in entity taxonomy (`items`, `families`) |
| POST | `/api/v1/policies/test-recognizer` | detect | run a custom recognizer over a test text; offsets only |
| GET | `/api/v1/policies/presets` | detect | M0's `{name, description}` plus `version`, `hash`, `mode` (`obfuscate` \| `synthesize`), `reversible` |
| GET | `/readyz` | — | gains the `ocr` component (below) |

`Idempotency-Key` is honoured on `/detect`, `/obfuscate`, `/synthesize`, `/reveal`,
`/documents/*` and `/schema/synthesize` (not on `/schema/generator-spec` or the policy routes).

**`/readyz` → `components.ocr`**: `{ok, detail, provider, engine}`. `detail` is `up`, `down`,
`down:<status>`, `error`, `unconfigured` or `tesseract_missing`; `engine` is `azure-read-v3.2` or
`tesseract-<major.minor>`. Example (compose `--profile docs`):
`{"ok": true, "detail": "up", "provider": "azure", "engine": "azure-read-v3.2"}`. It is probed at
startup and every `DPS_OCR_PROBE_INTERVAL_SECONDS`, and it is never *required*: JSON and text need
no OCR, so `/readyz` stays 200 when OCR is down.

## Documents

### Request (multipart)

| Part | Required | Content |
|---|---|---|
| `file` | yes | the document: PDF, PNG, JPEG, DOCX, TXT or CSV (sniffed from the bytes; TXT/CSV also from the name or `text/*` type) |
| `request` | no | a JSON string: `{"policy": <preset name or inline policy>, "options": {...}}` (unknown top-level keys are refused) |

`options` is M0's `RequestOptions` (`locale`, `token_style`, `wrap_key`, `consistency`,
`llm_rewrite`, `return_segments`) plus:

| Option | Default | Meaning |
|---|---|---|
| `redacted_pdf` | `false` | also return a true redacted PDF (`obfuscate`/`synthesize` only; PDF and image inputs only) |
| `ocr` | `auto` | `auto`: OCR pages with fewer than `DPS_OCR_MIN_TEXT_CHARS` text-layer characters or images covering `DPS_OCR_IMAGE_COVERAGE` of the page (and, for a redacted PDF, every page carrying an image); `always`: every page; `never`: none (an image upload is then refused) |
| `previews` | `false` | page PNGs (`preview_png_b64`, and `redacted_preview_png_b64` with a redaction) at `DPS_PREVIEW_DPI`, up to `DPS_MAX_PREVIEW_PAGES` pages, for the console |
| `ocr_verify` | `null` | `null` = `DPS_REDACT_OCR_VERIFY` (default `true`): re-read every page of the produced PDF with OCR; `false` = the structural checks only |

`llm_rewrite` is ignored for documents (warning `llm_rewrite_not_applied_to_documents`):
replacements are mapped back onto pages and boxes, which a free rewrite would break.

Limits: `DPS_MAX_DOCUMENT_MB` (20) for the file and the same plus 1 MiB of multipart overhead for the
body; `DPS_MAX_DOCUMENT_PAGES` (200); a PDF page that would render over 10,000 px a side at the
higher of `DPS_PDF_RENDER_DPI` (200) and `DPS_PREVIEW_DPI` (3,600 pt at the defaults); M0's
`DPS_MAX_TEXT_CHARS` for the canonical text and `DPS_MAX_ITEMS` for CSV values.

### Response

`/documents/detect` returns M0's detect body (`findings`, `record_sensitivity`, `report`,
`trace_id`) plus `document` and `pages`. `/documents/obfuscate` and `/documents/synthesize` return
M0's transform body (`result`, `findings`, `plan`, `key_material` / `reveal_hints` for obfuscate,
`synthesis_seed` for synthesize, `segments`, `report`, `trace_id`) plus `document`, `pages`,
`redacted_pdf_b64` and `result_csv`.

- **`result`** is one canonical text: the pages joined by `"\n\f\n"` (PDF, image, DOCX, TXT). For a
  CSV it is the list of records (`[{"column": "value"}, …]`) and `result_csv` is the same output as
  CSV text, header and delimiter kept. `findings[].locator` addresses that text (or `$[row].column`).
- **`document`**: `{format, pages, ocr_pages, unreadable_pages, words, boxes, ocr_engine, bytes,
  sha256, warnings, timings_ms, redaction, synthetic_validity}`. `ocr_pages` lists the pages read
  with OCR; `unreadable_pages` the pages that needed OCR when none was configured (warning
  `ocr_unconfigured`). `sha256` is the upload's digest. `synthetic_validity` (synthesize) is
  `{checked, valid}` for rule-checked surrogate types.
- **`pages[]`**: `{page, width, height, unit: "pt", source, words, lines, ocr_confidence, findings,
  preview_png_b64, redacted_preview_png_b64}`. `source` is `text_layer`, `ocr`, `hybrid` (text layer
  plus OCR), `docx`, `text`, `csv`, `empty` or `unreadable` (needed OCR, none configured); DOCX, TXT
  and CSV pages have no geometry (`width = height = 0`).
- **`pages[].findings[]`**: `{n, entity_type, score, sensitivity, detectors, evidence, value_hash,
  page, line, path, span, boxes, method, reversible, redacted, replacement, out_span,
  ocr_confidence, review}`. `n` numbers findings in document order (the console's box labels).
  `boxes` are `{page, x0, y0, x1, y1}` in **PDF points with a top-left origin**, on the normalised
  page (crop box at the origin, rotation applied), character-precise on a text layer and
  word-precise on OCR. `span` is `[start, end)` in the canonical text (CSV: within the cell).
  `method` / `reversible` / `replacement` / `out_span` are set on transform routes: `replacement` is
  the token or surrogate written in the output (never for `keep`), `out_span` where it sits in
  `result`. `redacted` is `true` when the method is not `keep` (it is then boxed in a redacted PDF).
  `review` flags an OCR confidence below 0.8.
- **`redacted_pdf_b64`**: the redacted PDF, base64, when `redacted_pdf` was asked.
  `document.redaction` reports it: `{boxes, pages, dpi, image_format, bytes, sha256,
  text_layer_chars, text_operators, annotations, ocr_checked, ocr_pages, ocr_hits,
  chars_under_boxes, passed}`.
- **`segments`**: M0's output segments (the document pipeline always computes them; `pages`
  carries the same information per finding).
- **Warnings** (`document.warnings`, codes only): `ocr_unconfigured`, `images_not_ocr_read`,
  `redaction_not_ocr_verified`, `annotations_not_scanned_or_drawn`,
  `llm_rewrite_not_applied_to_documents`.

### Redaction and its verification

A redacted PDF is **image-only**: each normalised page is rasterised (pypdfium2, at
`DPS_PDF_RENDER_DPI`), every box of a transformed finding is burned in as a solid rectangle, and a
new PDF is built from the page images (reportlab; JPEG or PNG per `DPS_REDACT_IMAGE_FORMAT`).
Nothing of the source survives: there is no text layer (the output is not searchable), metadata is
empty, and annotations and form fields are not drawn. It is not reversible, by design; the
`key_material` applies to the text output only.

Before it is returned, the output is verified:

1. **Structure**: pypdf extracts 0 characters, the page streams (and any Form XObjects) hold no
   text-showing operator, there are no annotations, forms, outlines or attachments, and the page
   count matches (else 500 `redaction_verify_failed`).
2. **OCR re-read** (when OCR is configured and `ocr_verify` holds): every page of the produced PDF
   is read again with OCR. The request fails with 422 `leakage` and nothing is returned if a
   redacted original is still readable on any page, or if the engine, run over what OCR read with
   the request's policy, finds a value it would transform. A box reads as one `█` mark, so a cue
   never reaches past a redacted value. With OCR configured but unreachable the request is refused
   (503 `ocr_unavailable`); with no OCR configured the PDF is returned with the warning
   `redaction_not_ocr_verified`.

A page that looks scanned but could not be OCR'd is refused for redaction (422
`redaction_needs_ocr`), as is a finding without a position on the page (422
`redaction_unlocated`).

## Schema synthesis

**`POST /schema/generator-spec`** takes exactly one of `schema` (a JSON Schema object), `ddl` (one
`CREATE TABLE`, ≤ 100,000 characters) or `sample` (≤ 1,000 rows of JSON objects, ≤ `DPS_MAX_ITEMS`
values), plus `name`, `locale` and `use_llm` (default `true`). It returns `{spec, spec_hash,
profile, source, fallback, llm: {calls, raw_pii_sent}, trace_id}`. `source` is `llm` or `heuristic`;
`fallback` says why a model proposal was asked for but not used: `leak_guard` (the prompt would have
carried a sampled value, so no call was made), `llm_invalid_spec` (the reply did not validate), or
the gateway's error class (`LlmUnavailable`, `LlmCallError`). It is `null` when no model was asked
(`use_llm: false`, or no real provider: the stub always uses the heuristic proposer).

**`POST /schema/synthesize`** takes exactly one of `spec`, `schema`, `ddl` or `sample`, plus `rows`
(default 100, ≤ `DPS_SCHEMA_MAX_ROWS`), `seed` (a `synthesis_seed` from an earlier response),
`format` (`json` | `csv`), `name`, `locale` and `use_llm`. It returns `{rows | csv, spec, spec_hash,
synthesis_seed, report, trace_id}`. The same spec, seed and row count give the same rows, byte for
byte (`AGE` and `CARD_EXPIRY` are relative to today, so within a day). With a `sample`, the
generated rows are swept against the sample's sensitive values and the request fails closed
(422 `leakage`) on a match.

A spec (`dps.genspec.v1`) is a reviewable artefact: `fields[]` of `{name, generator, params,
null_rate, unique, entity_type}` with generators `sequence uuid integer decimal normal lognormal
choice boolean date datetime pattern text entity constant`, and `constraints[]` (`order` across
sortable fields, `rule` = `field` set from `when` cases). Every parameter is validated before
anything runs.

**P1 (no raw values reach the LLM).** The model receives a *structure profile* only: field names (a
name that looks like data is sent as `field_<n>` and mapped back), types, nullability, null rate,
distinct ratio, lengths, numeric magnitude, year range, primary key, value **shapes** (`AB-1234` →
`XX-9999`, ≤ 24 characters), the detected entity type, and for categorical columns the **number**
of labels, never the labels. The prompt
passes the leak guard against every sampled value or no call is made. DPS keeps its own generator
for every detected or categorical field and drops any model constraint that would write or reorder
one.

## Taxonomy and recognizer test

**`GET /entities`** → `{items: [{name, family, sensitivity, description, jurisdictions, builtin}],
families}`: the built-in types only, sorted by family then name. Custom types are never listed: a
policy's custom tiers belong to the request (or job) that uses it (S3 review round 1).

**`POST /policies/test-recognizer`** `{recognizer, text}` → `{matches: [{start, end, entity_type,
score, evidence, context}], count, yaml}`. `recognizer` is a custom-recognizer spec (Presidio
fields `name`, `supported_entity`, `patterns[{name, regex, score}]`, `context`, `deny_list`,
plus the DPS extension `allow_list`: exact values that never match). Limits: text 20,000
characters, 50 patterns of ≤ 500 characters (422 `invalid_recognizer`, also for a regex that does
not compile or that the static ReDoS filter refuses). The text is neither stored nor echoed;
nothing is registered. Patterns run under the request's CPU budget like every policy pattern
(below): one that runs past it answers 422 `custom_pattern_too_slow`.

## Error codes

| Code | Status | Where |
|---|---|---|
| `bad_request` | 400 | engine errors on every engine route (bad policy, key material, hints, parameters) — M0 routes too |
| `leakage` | 422 | the leakage sweep failed under a `strict` policy (M0 routes too); a redacted PDF's OCR re-read still found a value; generated schema rows matched a sample value |
| `payload_too_large` | 413 | body over its route limit (JSON `DPS_MAX_PAYLOAD_MB`; documents `DPS_MAX_DOCUMENT_MB` + 1 MiB; NDJSON `DPS_MAX_NDJSON_MB`), by `Content-Length` or while streaming |
| `invalid_request` | 422 | the documents `request` field is not valid JSON of the right shape |
| `unsupported_format` | 415 | not a PDF, PNG, JPEG, DOCX, TXT or CSV |
| `empty_document` · `corrupt_document` · `encrypted_pdf` · `unsupported_encoding` · `csv_ragged_rows` | 422 | the file cannot be read as its format (encrypted PDFs are refused, never decrypted) |
| `document_too_large` · `too_many_pages` · `image_too_large` · `text_too_long` · `too_many_values` | 413 | document limits (above) |
| `redacted_pdf_not_allowed` | 422 | `redacted_pdf` on `/documents/detect` |
| `redaction_unsupported_format` | 422 | `redacted_pdf` for DOCX, TXT or CSV |
| `ocr_required` | 422 | an image with `ocr: never` |
| `redaction_needs_ocr` · `redaction_unlocated` | 422 | see "Redaction and its verification" |
| `redaction_verify_failed` | 500 | the produced PDF failed its structural check (nothing returned) |
| `ocr_unconfigured` | 503 | pages need OCR (an image, `ocr: always`, or a redaction) and no OCR provider is configured |
| `ocr_unavailable` | 503 | the OCR call or re-read failed; the detail names the client code (`ocr_timeout`, `ocr_throttled`, `ocr_protocol`, `ocr_failed`, …) |
| `exactly_one_input` | 422 | schema routes: not exactly one of `spec` / `schema` / `ddl` / `sample` |
| `schema_unsupported` · `ddl_unparsed` · `sample_empty` | 422 | the schema, DDL or sample cannot be profiled (more than 200 fields included) |
| `ddl_too_large` · `sample_too_large` · `too_many_rows` | 413 | schema limits (above) |
| `invalid_seed` · `invalid_rows` · `unique_exhausted` | 422 | a bad `seed`, `rows < 1`, or a `unique` field that ran out of values |
| `invalid_recognizer` | 422 | the recognizer test (above) |
| `custom_pattern_too_slow` | 422 | a policy pattern (custom recognizer or deny list) ran past the call's CPU budget — 1 s plus 50 µs per pattern run plus 1 µs per character per pattern scanned (S3 review round 1): every engine route, NDJSON item, document and the recognizer test; an L2 job (or partition) fails with this code, without retries |
| `ndjson_invalid` | 400 / 422 | an NDJSON envelope or line that is not valid (400), or nesting deeper than 100 (422) |
| `ndjson_too_large` | 413 | more than `DPS_NDJSON_MAX_ITEMS` items |
| `idempotency_key_invalid` | 422 | the header does not match `[A-Za-z0-9_.:-]{1,128}` |
| `idempotency_key_reused` | 422 | the same key with a different body within the TTL |

An invalid JSON body (FastAPI validation) is still a 422 whose `detail` lists `{type, loc, msg}`
without the input, as in M0.

## Worked examples

```bash
B="http://127.0.0.1:${DPS_APP_PORT:-8400}"; export DPS_API_KEY=dps_local_dev_key_change_me

# Redact a scanned KYC PDF and save the redacted copy (needs OCR: docker compose --profile docs up -d)
curl -sS -X POST "$B/api/v1/documents/obfuscate" -H "X-API-KEY: $DPS_API_KEY" \
  -F file=@demo/documents/kyc-scanned.pdf -F 'request={"options":{"redacted_pdf":true}}' \
  | python -c 'import sys,json,base64; b=json.load(sys.stdin); open("kyc.redacted.pdf","wb").write(base64.b64decode(b["redacted_pdf_b64"])); print(b["document"]["redaction"])'
# → {'boxes': 10, 'pages': 2, ..., 'text_layer_chars': 0, 'text_operators': 0, 'ocr_checked': True,
#    'ocr_pages': 2, 'ocr_hits': 0, ..., 'passed': True}

# Findings and boxes per page of a text PDF
curl -sS -X POST "$B/api/v1/documents/detect" -H "X-API-KEY: $DPS_API_KEY" -F file=@demo/documents/kyc-text.pdf \
  | python -c 'import sys,json; b=json.load(sys.stdin); [print(p["page"], f["n"], f["entity_type"], f["boxes"][:1]) for p in b["pages"] for f in p["findings"]]'

# Synthetic CSV, same header and delimiter
curl -sS -X POST "$B/api/v1/documents/synthesize" -H "X-API-KEY: $DPS_API_KEY" \
  -F file=@demo/documents/customers.csv -F 'request={"policy":"synthetic-realistic"}' \
  | python -c 'import sys,json; print(json.load(sys.stdin)["result_csv"])'

# A generator spec from DDL, then 1 000 reproducible rows (replay with the returned seed)
curl -sS -X POST "$B/api/v1/schema/generator-spec" -H "X-API-KEY: $DPS_API_KEY" -H 'Content-Type: application/json' \
  -d '{"ddl":"CREATE TABLE customers (customer_id INTEGER PRIMARY KEY, full_name VARCHAR(80), email VARCHAR(120))"}' > spec.json
python -c 'import json; json.dump({"spec": json.load(open("spec.json"))["spec"], "rows": 1000}, open("req.json","w"))'
curl -sS -X POST "$B/api/v1/schema/synthesize" -H "X-API-KEY: $DPS_API_KEY" -H 'Content-Type: application/json' -d @req.json > rows.json
python -c 'import json; r=json.load(open("rows.json")); json.dump({"spec": r["spec"], "rows": 1000, "seed": r["synthesis_seed"]}, open("again.json","w"))'
curl -sS -X POST "$B/api/v1/schema/synthesize" -H "X-API-KEY: $DPS_API_KEY" -H 'Content-Type: application/json' -d @again.json \
  | python -c 'import sys,json; print(json.load(sys.stdin)["rows"] == json.load(open("rows.json"))["rows"])'   # True

# Or in one call, as CSV
curl -sS -X POST "$B/api/v1/schema/synthesize" -H "X-API-KEY: $DPS_API_KEY" -H 'Content-Type: application/json' \
  -d '{"ddl":"CREATE TABLE customers (customer_id INTEGER PRIMARY KEY, full_name VARCHAR(80))","rows":1000,"format":"csv"}'
```

## NDJSON streaming (`/detect`, `/obfuscate`, `/synthesize`, `/reveal`)

Opt-in per request. **Response streaming**: a JSON body whose `data` is an array, sent with
`Accept: application/x-ndjson`. **Request streaming**: `Content-Type: application/x-ndjson`, where
line 1 is the envelope (`{"policy", "options"}`; for `/reveal` `{"key_material"}`) and every further
line is one item (for `/reveal`, an item line of an earlier obfuscate stream, `{"result",
"reveal_hints"}`). An NDJSON request always gets an NDJSON response:

```text
{"type": "header", "trace_id", "mode", "policy": {"name", "hash"}, "key_material"?, "synthesis_seed"?}
{"type": "item", "index", "result"?, "findings"?, "reveal_hints"?, "segments"?, "record_sensitivity"?, "revealed"?}
{"type": "report", "items", "report": {"findings_total", "findings_by_type", "methods_used", "verification", "llm_calls", …}}
```

- **One key per stream**: the caller's `consistency.key_material` / `synthesis_seed`, or one
  generated up front (and wrapped once under `options.wrap_key`), used for every item. Each item
  line equals what the JSON route returns for `{"data": item}` under that key.
- **Fail closed**: an item that fails verification under `strict`, or any engine error, writes
  `{"type": "error", "index", "code", "detail"}` and ends the stream; no later item runs. Request
  errors (envelope, limits, policy, key material) are coded HTTP errors before anything streams.
- **Limits**: `DPS_NDJSON_MAX_ITEMS` items (413 `ndjson_too_large`), `DPS_MAX_NDJSON_MB` for an
  NDJSON body, and `DPS_MAX_ITEMS` values per item (413 `payload_too_large`, naming the item).
- One audit event per stream (counts summed; outcome `aborted` when the client goes away). A
  complete stream is audited before its `report` line: with `DPS_AUDIT_STRICT=true` (M2) a sink
  that cannot take the event ends the stream with `{"type": "error", "index": null, "code":
  "audit_unavailable", "detail": "audit unavailable"}` instead of the report (the JSON routes
  answer 503 `audit_unavailable`), and the replay cache does not record it. A stream without a
  `report` line is never complete.
- A JSON array sent to `/reveal` with `Accept: application/x-ndjson` is revealed as one document
  (its hints address the whole array) and streamed one line per element.

```bash
curl -sS -X POST "$B/api/v1/obfuscate" -H "X-API-KEY: $DPS_API_KEY" -H 'Content-Type: application/json' \
  -H 'Accept: application/x-ndjson' \
  -d '{"data":[{"email":"ananya.k@example-mail.in"},{"email":"rohan.d@example-mail.in"},{"email":"priya.n@example-mail.in"}]}'
# 5 lines: header, 3 items, report
```

## `Idempotency-Key`

Accepted on the Layer 1 routes listed above; the key matches `[A-Za-z0-9_.:-]{1,128}` (else 422
`idempotency_key_invalid`). The cache is **replica-local** and holds, per
`sha256(tenant, key id, route, key)`, only the request body's SHA-256 (for documents: the file's
digest plus the `request` field), the original `trace_id` and a timestamp — **never key material, a
seed or an output** (decision D2).

- The same key and body within `DPS_IDEMPOTENCY_TTL_SECONDS` (600) **re-runs the engine** and
  answers with `Idempotent-Replay: true` and the original `trace_id` (audit outcome `replayed`).
  The output is identical only when the caller sent its own `options.consistency.key_material` /
  `synthesis_seed` (or `seed` on `/schema/synthesize`); otherwise the replay carries fresh key
  material, which is safe because a Layer 1 call has no side effects.
- The same key with another body → 422 `idempotency_key_reused`.
- An entry is recorded only after a successful call (for a stream, one that ended with its report
  line). A retry on another replica, after the TTL or after an LRU eviction
  (`DPS_IDEMPOTENCY_MAX_ENTRIES`) runs as a first call. `DPS_IDEMPOTENCY_TTL_SECONDS=0` disables the
  cache (a well-formed header is accepted and ignored).
- CORS allows the `Idempotency-Key` header and exposes `Idempotent-Replay`.

## OCR providers

| `DPS_OCR_PROVIDER` | Engine | Notes |
|---|---|---|
| `""` / `off` | none | scanned pages are `unreadable_pages` (warning `ocr_unconfigured`); images, `ocr: always` and redactions that need OCR are 503 `ocr_unconfigured` |
| `azure` | Azure Computer Vision Read v3.2 (`azure-read-v3.2`) | needs `DPS_AZURE_VISION_ENDPOINT` and `DPS_AZURE_VISION_KEY`; the compose mock locally, the org gateway (health path `/_gw/health`) in prod; staging/prod need `https` |
| `tesseract` | the local `tesseract` CLI (`tesseract-<version>`) | no network; `/readyz` says `tesseract_missing` without the binary |
| `auto` (the image's default) | azure when an endpoint and key are set, else tesseract when the binary is on `PATH`, else none | compose sets `azure` (the mock under `--profile docs`) |

DPS sends the OCR engine **page images only** (PNG, rendered at `DPS_PDF_RENDER_DPI`), never a PDF.
The Read client pins polling to the configured endpoint's origin (an `Operation-Location` on another
host is refused) and fails closed on a result of an unknown shape (`ocr_protocol`). Tesseract's
stderr is never read.

## T3 metadata labelling (`llm_label`, opt-in)

A policy opts in by listing `llm_label` in `detect.tiers`, and it only runs with a real provider
(`stellar` or `vertex`; never `stub`). After T0–T2 (structural keys, patterns and checksums, NER),
the JSON leaves left **without** a finding are grouped by path template (`$.rows[*].code`) and sent
as field metadata only: the field name, its JSON type and up to three value **shapes**
(`AB-1234-77` → `XX-9999-99`) — never a value. A name or path that looks like data (or holds a
quoted key) is sent as `field_<n>` and mapped back. The prompt passes the leak guard against the
leaf values, or no call is made. At most `DPS_LLM_LABEL_MAX_FIELDS` (40) fields per call; verdicts
below `DPS_LLM_LABEL_MIN_CONFIDENCE` (0.7) are dropped, and kept verdicts are built-in entity types
with a score capped at 0.85 (detector `llm_label`). From there a labelled leaf is planned,
transformed and verified like any other finding, and the call counts in `report.llm.calls`. Text
payloads and documents are not labelled. There is one labelling call per request, and one per NDJSON
stream (from its first chunk).

## Evaluation report

The detector evaluation is committed as `docs/technical/detector-eval-m1.md` (and `.json`): a
900-record synthetic labelled corpus (`tests/eval/corpus/`), with precision, recall and F1 per
entity type, an *In free text* table (spans inside text records and JSON `notes` only — what a
document, an OCR page or a notes column is swept for, including `ACCOUNT_NUMBER` and `CUSTOMER_ID`
found by their label), a confusion table and a threshold sweep. A type in the main table that is
missing from the free-text table is found by its field name alone; the verification's coverage
gaps name every such type. Regenerate it with:

```bash
python tools/eval_detectors.py run --corpus tests/eval/corpus \
  --json docs/technical/detector-eval-m1.json --markdown docs/technical/detector-eval-m1.md
```

`tests/test_eval_report.py` fails when the detectors change until the report is regenerated. The
bank's own labelled samples go through the same command (`--corpus /secure/…`) and are never
committed (`tests/eval/private/` is git-ignored).
