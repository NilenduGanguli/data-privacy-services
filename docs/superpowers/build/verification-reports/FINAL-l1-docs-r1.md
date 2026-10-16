# FINAL — v1.0.0 candidate on `main` — lens "l1-docs" verifier, round 1

**Verdict: FAIL — one blocking defect (B1).** Almost every check in this lens passed:

| Check | Result |
|---|---|
| Layer 1 round trips | pass |
| NDJSON | pass |
| `Idempotency-Key` | pass |
| Documents: 0 extractable characters, independent OCR re-read finds 0 originals | pass |
| Schema replay | identical |
| 20 of 20 runs (100 rows, 7-column 4-row sample) | 200, with NER off on the stack and with NER on in-process |
| Canonical planted sample values | still 422 |
| Live Vertex test | ends **PASS** |
| Log scan | 0 values |

**B1.** A *planted* sample value of one form is not refused. When a sampled e-mail address is also used as a sample key, `POST /schema/synthesize` answers **200** and reports verification `passed`. Every generated row, the returned spec and the CSV header carry that e-mail. This is a regression from fspec `e1f3a3a`: the same call on the code before it gave 0/20 rows.

| | |
|---|---|
| Date | 2026-10-16, 09:55–10:45 IST |
| Tree | `/Users/neelu/document_intelligence/data-privacy-services`, `main` @ **`45be8ac`** (`chore(release): v1.0.0`). HEAD was unchanged at the end. |
| Stack | Compose project `data-privacy-services`, default ports. The gates verifier started it. I did not restart, rebuild or stop it. The app has run since 02:48:57Z with 0 restarts. `/readyz` ready, version 1.0.0. Components: ocr azure-read-v3.2 (pytesseract mock), fpe ff1, ner patterns-only (by design), llm stub. |
| Probe dir | `~/.cache/dps-build/reports/FINAL-l1-docs-r1/`. Scripts, logs, `*.results*.json`, the redacted PDFs, OCR counts, the log scan, and the pre-fspec code extract `old-e1f3a3a-parent/`. |
| Live-LLM venv | `~/.cache/dps-build/venv-final`: uv, Python 3.12.13. Installed `-e <repo>[vertex,ner,fpe,docs,db,connectors,tabular]` + `en_core_web_sm` 3.8.0 (spaCy 3.8.16); `dps` 1.0.0 is imported from the checkout. Log: `venv-final-install.log`. |
| Repo files modified | **None.** `git status` showed only a foreign untracked `docs/screenshots/v1.0.0/` (80 PNGs, created 10:10 IST by another agent; not mine, left untouched). The editable imports refreshed git-ignored `__pycache__/*.pyc` only. `.git/FETCH_HEAD` was also touched by someone else; I ran no git fetch or push. |
| Values | Every input is invented: the repo's demo documents, fictional customers and the live-run sample. Logs and this report hold statuses, counts, codes, entity types and booleans only. The SA key was used only as a path; it was never printed, copied or mounted. |

## A. Layer 1 JSON round trips (`probe_l1.py` sections A–C, `probe_l1-ABC-rerun.log`)

Each "left 0" result is an **independent** count: every original is searched for in the output, after normalising to alphanumerics and lower case.

| # | Check | Result |
|---|---|---|
| A1 | `/detect` on an 11-field customer | 200, 11 findings, 10 types (AADHAAR, CARD_PAN, CUSTOMER_ID, DATE, DATE_OF_BIRTH, EMAIL, IBAN, PAN_IN, PERSON_NAME, PHONE) |
| A2 | `/obfuscate` gdpr-pseudonymise | 200. Methods: fpe_digits 3, fpe_text 2, email 2, date_shift 1, phone 1, det_token 1, keep 1. **0/10 originals left** outside the span the policy keeps (`$.notes` DATE, see N4). |
| A3 | `/reveal` | 200, revealed 10, **result == input** |
| A4 | reveal with another request's key | 400, value-free |
| A5 | caller `key_material` | same tokens, identical result |
| A6/A7 | `/synthesize` synthetic-realistic | 200, 0 originals left outside the kept DATE span. Replay with the returned `synthesis_seed` is identical. |
| A8 | text obfuscate → reveal | 0/6 originals left; revealed text **== original** |
| A9 | text synthesize | 0 originals left |

The first full run (`probe_l1.log`, 26/30) failed A2, A6, B1 and B6. All four were expectation errors in my probe, now corrected:
- The kept generic DATE (N4).
- A bare `name` key, which with NER off needs value evidence by design (N4).

## B. NDJSON

| # | Check | Result |
|---|---|---|
| B1 | JSON array + `Accept: application/x-ndjson` (obfuscate, 5 items) | Lines are header (with key), 5 items with indexes 0–4, then report (`items: 5`). 0/15 originals left. |
| B2 | Each item line vs the JSON route for `{"data": item}` under the stream key | 5/5 identical |
| B3 | NDJSON *request* to `/reveal` (envelope `key_material` + the item lines) | 5/5 items equal the originals; report line last |
| B4 | NDJSON request stream obfuscate → JSON reveal per item | 5/5 equal |
| B5 | NDJSON detect stream | 15 findings |
| B6 | NDJSON synthesize stream | 0 originals; seed replay identical |
| B7 | Broken item line | 400 `ndjson_invalid`, value-free |

## C. `Idempotency-Key`

| # | Check | Result |
|---|---|---|
| C1 | Same key + same body, caller key material | 200/200, `Idempotent-Replay: true` on the replay only, same `trace_id`. Bodies are equal except `report.duration_ms`. |
| C2 | Same key + other body | 422 `idempotency_key_reused`, value-free |
| C3 | `has space`, 129 chars, `semi;colon`, empty | 422 `idempotency_key_invalid` (4/4) |
| C4 | Replay without caller key | Replay header and original trace, **fresh kid** (D2: no key material cached) |
| C5 | Same key on another route | Separate scope, no replay |
| C6 | Replays on `/detect`, `/reveal`, `/synthesize`+seed, NDJSON obfuscate | All replay with identical results |
| D6 | `documents/detect` | Replay; another file under the same key → 422 `idempotency_key_reused` |

## D. Documents (`probe_l1.py` section D, `probe_l1.log`, `ocr-redacted-*.counts.json`, `redacted-*.pdf`)

For the OCR re-read, my tesseract 5.5.0 runs from `data-privacy-services:local` (`--network none`, image on stdin) at 300 dpi. It is independent of the server's Azure-mock OCR. The originals are the 12 values the generator wrote into the demo documents (`tools/make_demo_docs._values()` + `CUST-884120`).

| # | Input | Server redaction report | Independent text layer (pypdf / pdfplumber / pdfium / text operators) | OCR positive control on the input | OCR of the redacted output |
|---|---|---|---|---|---|
| D2 | `kyc-text.pdf` | 14 boxes, `text_layer_chars` 0, `text_operators` 0, `ocr_checked`, `ocr_hits` 0, passed | **0 / 0 / 0 / 0** | 12/12 originals read | **0 originals**; 35 words still read |
| D2 | `kyc-scanned.pdf` (OCR pages [1, 2]) | 14 boxes, same zeros, passed | **0 / 0 / 0 / 0** | 12/12 | **0**; 36 words read |
| D2 | `id-card.png` (OCR page [1]) | 6 boxes, same zeros, passed | **0 / 0 / 0 / 0** | 6/6 (page 1 holds 6 of the 12) | **0**; 18 words read |

Other document checks:
- **D1** `documents/detect`: 14 boxes, 11 types.
- **D3** `kyc.txt` obfuscate → reveal: 0 originals left; revealed **==** original.
- **D4** `kyc.docx` obfuscate → reveal: 0 originals left; the revealed text holds the 7 originals again.
- **D5** `customers.csv` synthesize:
  - header kept, 20/20 rows;
  - sensitive columns customer_id, full_name, email, phone and card_number;
  - 0 same-column equal values, and 0 source values anywhere in the output.

## E. Schema synthesis (`probe_schema.py`, `probe_schema-{http,ner}.log`, `schema-20x100.counts.json`, `pytest-schema-planted.log`)

**Replay checks:**
- **E1** DDL → spec → `/schema/synthesize`. Replaying spec + returned seed at **25 and 1,000 rows** gives byte-identical `rows`, and identical `csv` with `format: csv`. Only `trace_id` and `duration_ms` differ.
- **E2** Same sample + seed: identical twice (`sample_redraws` 1). A sample-free replay of the returned spec differs in 1/100 rows, which is the documented steering effect.

**E3 (HTTP, stack, NER off):** the 7-column, 4-row live sample, 100 rows, `en_IN`, 20 calls.
- **20/20 × 200**, verification passed 20/20, `sample_redraws` 2–4 per 20 calls, 0.03–0.08 s each.
- Independent comparison over 2,000 rows found 0 in every sensitive category:
  - same-column copies of cust_id, full_name, email, opened or balance;
  - strings holding a sampled full name, e-mail or date;
  - strings holding a sampled ID as a whole digit run;
  - strings whose whole e-mail local part is a sampled one.
- The non-zero counts are by design or coincidence:
  - same-column city 10–28 (CITY is `SWEEP_EXEMPT` by design);
  - 40–48 strings containing a sampled given name or surname (common en_IN vocabulary; whole names 0);
  - one e-mail starting with a sampled 7-character local part (`<given>.<initial>` + more), not a copy.

**E5 (in-process, Presidio + en_core_web_sm on, stub LLM):**
- **20/20 × 200**, passed 20/20, 0.06–0.82 s.
- The same zeros (city 21–34 by design).

**E4 — planted sample values:**

| Plant | NER off (stack, HTTP) | NER on (in-process) |
|---|---|---|
| A sampled name used as a column name | **422 `leakage`**, no echo | — |
| A sampled name repeated as categorical labels of another column | **422 `leakage`**, no echo | Column detected as PERSON_NAME and generated; 0 sampled names written |
| A sampled phone (also a value) used as a key | **422 `leakage`** | — |
| 14 forms forced into the rows after execution; spec literal; sampled mobile with a country code; ID-like keys; dense ID; comma lists; drawn again; replays (`tests/test_api_schema.py`, release code) | **23 passed** | — |
| **A sampled e-mail (also a value) used as a key** | **200, verification `passed`: B1** | — |

## F. Live Vertex (`vertex_live.log`, `prompts_sent.this-run.json`)

`venv-final/bin/python -I ~/.cache/dps-build/vertex_live/vertex_live_test.py <key path> ziffy-staging`. The script's sha256 is `03252588…bcc8c8`. It ran 09:57:46–09:58:15 and **ended `PASS`, exit 0**.

| Section | Result |
|---|---|
| readyz | llm-gateway vertex, `coin_tls: verified` |
| Gateway smoke | `gemini-2.5-flash`, 3.67 s |
| T3 labelling | 200: 3 `llm_label` findings (PERSON_NAME ×2, STREET_ADDRESS), `raw_pii_sent` 0, no raw value left |
| Text rewrite | 200, no raw value left. Fallback `leak_guard`: not sent, so the rewrite was not used (N2). |
| Generator spec | 200, `source=llm`, names kept (`cust_id` …), schema/synthesize 200, 0 sample values reproduced |
| **P1** | 3 prompts sent, **0 raw values** |

Diagnostics:
- One extra live generator-spec call (`diag_llm-vertex.log`) confirmed N1.
- `diag_llm-stub.log` measured N2.

## G. Log scan (`log_scan.json`)

- I scanned the app and worker logs since 04:25Z for all 44 invented values my probes and the live run sent: 3,072 app lines and 139 worker lines.
- Result: **0 raw hits and 0 normalised hits**.
- `vertex_live.log` holds 0 raw values.

## Blocking

### B1 — a sampled e-mail used as a sample key reaches every synthetic row, and the sweep reports `passed`

**Reproduction** (HTTP, port 8400, `diag_p2b.py` / `diag_p2c.py`):
- Request: `POST /api/v1/schema/synthesize` with `{"sample": <the 4 live rows, each with one extra key equal to row 4's sampled e-mail>, "rows": 20}`.
- Answer: **200**, `report.verification` = `{passed: true, status: passed, checked: 16, hits: []}`.
- **20/20 rows** carry the e-mail as a 3-level nested key path: field names nest at their dots, so `<local>.<part>@<domain>.<tld>` becomes three nested keys.
- The returned `spec` field name holds it, and so does the `format: csv` header.
- An undotted local part (`<local>@<domain>.<tld>`) gives the same result.
- `/schema/generator-spec` returns it in the spec.

**Controls:**
- The same plant with a sampled name (no dot) → 422 `leakage`.
- A sampled phone (no dot) → 422 `leakage`.

**Regression** (`diag_old.py`, in-process, same call):

| Code | Answer | Rows holding the e-mail | Spec fields holding it |
|---|---|---|---|
| `git archive e1f3a3a^ dps` | 200 | 0/20 | 0 (the flagged key was aliased `field_N`) |
| Release | 200 | **20/20** | **1** |

**Root cause:**
- fspec `e1f3a3a` keeps the caller's identifier-cleaned key in the spec and the rows.
- It relies on the key sweep for safety: per M1-DEVIATIONS, "the sweep still searches output keys for sample values".
- But `verify_against_sample` walks `output_strings()`, which matches each nesting level's key on its own.
- Every e-mail contains a dot, so no single key holds the whole value. The invariant in the `schema_profile` docstring ("Synthesis from a sample never returns a sample's sensitive value") breaks behind a "passed" label.

**Fix** (narrow): in `dps/engine/synth/schema_profile.py`, do one of:
- also match each leaf's joined key path (the spec field name) against the sweep originals → 422 `leakage`, as the name and phone cases already do; or
- alias such a key in the output, as before `e1f3a3a`.

Add the e-mail-as-key form to the planted cases in `tests/test_api_schema.py`.

## Non-blocking observations

**N1 — an LLM `pattern` in the profile's shape notation is executed literally.**
- Both live calls had Gemini propose `segment: pattern {"template": "xxxxxx"}`, so every row's segment is the literal `xxxxxx`.
- Cause: shapes use `x`/`X`/`9`; templates use `a`/`A`/`9`/`X`. The heuristic converts with `_template()`; `merge_llm_spec` does not, and `_invented_labels` filters only `choice`.
- S8-integrate saw this and called it "as documented" (the enum rule); the actual cause is this notation mismatch.
- Also from the model: `cust_id integer 100–999` for sampled 4-digit IDs. This is model quality.
- Fix: convert a model template written in shape notation, or refuse one equal to the field's shape.

**N2 — the text rewrite loses to a false-positive leak guard.**
- `en_core_web_sm` sometimes tags the word "Aadhaar" as PERSON_NAME: 3/12 random Aadhaars with the stub, and the live run.
- `assert_placeholder_safe` then finds that "original" inside the placeholder label `{AADHAAR_1}` and refuses.
- This is P1-safe: nothing is sent. But the rewrite is skipped, and the deterministic text writes a surrogate first name for the word "Aadhaar".
- Fix: exclude placeholder labels from the guard's haystack.

**N3 — the NDJSON report line hides verification gaps.** It aggregates verification as `{"passed": true, "checked": 0}`, with no `status` or `coverage`. A stream of undetected values (a bare `name` key with NER off) therefore reads "passed", while the JSON route says `partial` and names the NER-off gap.

**N4 — by design, recorded:**
- A DOB value repeated as a generic DATE in free text is kept (`DATE: keep`; the leaf-scoped kept rule of M0 Task 18; status `partial`).
- A bare `name` key needs value evidence with NER off (the names pass through, and the JSON route reports it as a gap).
- An unswept 1–9 ID column whose sample fills the value space is written back.
- CITY is exempt from the sweep.

**N5 — someone else's untracked `docs/screenshots/v1.0.0/` is in the main checkout** (10:10 IST). The release step should commit it or drop it on purpose.

## End state

- The stack is left running, as asked.
- My only containers were throwaway `tesseract` runs (`--rm`, `--network none`).
- No `dps_test_*` databases were created.
- `venv-final` is kept for the next round.
