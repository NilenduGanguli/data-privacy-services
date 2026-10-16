# FINAL — v1.0.0 candidate on `main` — lens "l1-docs" verifier, round 2

**Verdict: PASS. No blocking finding.** Round 1's blocker (B1: a sampled e-mail used as a sample
key came back in every row while the sweep said `passed`) is **fixed live**: the exact round-1
reproductions now return 0 rows, 0 spec fields and 0 CSV headers holding the value. The fix's
seven new key-plant forms all came back clean too. Every check in this lens passed:

| Check | Result |
|---|---|
| Layer 1 round trips (detect, obfuscate, reveal, synthesize; JSON and text) | pass |
| NDJSON (response and request streams, reveal, detect, synthesize, broken line) | pass |
| `Idempotency-Key` (replay, reuse, malformed, D2 fresh key, scopes, documents) | pass |
| Redacted PDFs: extractable characters / independent OCR re-read | **0 / 0 originals** on 3 of 3 documents |
| Schema replay (spec + seed at 25 and 1,000 rows, JSON and CSV; sample + seed) | identical |
| 7-column, 4-row sample at 100 rows | **20/20 × 200** over HTTP (NER off) and **20/20 × 200** in-process with NER on; 0 sensitive copies |
| Planted sample value | **422 `leakage`**, no echo (4 of 4 HTTP forms); 29 of 29 planted-case tests pass on the release code |
| Live Vertex test | **PASS**, exit 0 |
| Log scan | 0 values |

| | |
|---|---|
| Date | 2026-10-16, 12:29–12:45 IST |
| Tree | `/Users/neelu/document_intelligence/data-privacy-services`, `main` @ **`d033dd3`** (round 1 verified `45be8ac`; since then `c7ad14f` fix round and `d033dd3` deck/HANDOFF). HEAD unchanged at the end. |
| Stack | Compose project `data-privacy-services`, default ports. The gates verifier started it at 06:11:10Z. I did not restart, rebuild or stop it: app, worker and browser-worker 0 restarts, `/readyz` ready at start and end. Components: ocr azure-read-v3.2, fpe ff1, ner patterns-only (by design), llm stub. The lens modules in `app` hash the same as the tree: `schema_profile.py`, `schema_gen.py`, `names.py`, `egress.py`, `routers/schema.py`. |
| Probe dir | `~/.cache/dps-build/reports/FINAL-l1-docs-r2/`: scripts (round 1's probes, E3/E4 updated for the fix), logs, `*.results.json`, redacted PDFs, OCR counts, prompts sent, log scan |
| Live-LLM venv | `~/.cache/dps-build/venv-final` (round 1's): Python 3.12.13, `-e <repo>[vertex,ner,fpe,docs,db,connectors,tabular]` + `en_core_web_sm`. `pyproject.toml` is unchanged since it was built (`git diff 45be8ac HEAD -- pyproject.toml` is empty). `dps` 1.0.0 is imported from the checkout. Every extra imports. |
| Repo files modified | **None.** `git status` is the same at start and end: only the foreign untracked `docs/screenshots/v1.0.0/` (not mine, left untouched). |
| Values | Every input is invented: the repo's demo documents, fictional customers, the live-run sample. Logs and this report hold statuses, counts, codes, entity types and booleans only. The SA key was used only as a path: never printed, copied or mounted. |

## A–C. Layer 1, NDJSON, Idempotency-Key (`probe_l1.py`, `probe_l1.log`: 30/31, the 31st is informational)

"Left 0" is an **independent** count: each original is searched for in the output, normalised to
lower-case alphanumerics.

**A. Layer 1**

| # | Check | Result |
|---|---|---|
| A1 | `/detect`, 11-field customer | 200, 11 findings, 10 types |
| A2 | `/obfuscate` gdpr-pseudonymise | 200, **0/10 originals left** outside the span the policy keeps (`$.notes` DATE, N4). Methods: fpe_digits 3, email 2, fpe_text 2, date_shift, phone, det_token, keep |
| A3 | `/reveal` | 200, revealed 10, **result == input** |
| A4 | Reveal with another request's key | 400, value-free |
| A5 | Caller `key_material` | same tokens |
| A6, A7 | `/synthesize` | 0 originals left; seed replay identical |
| A8 | Text obfuscate → reveal | 0/6 originals left, **== original** |
| A9 | Text synthesize | 0 left |

**B. NDJSON**

| # | Check | Result |
|---|---|---|
| B1 | Array → NDJSON | 7 lines (5 items), 0/15 originals left |
| B2 | Each item vs the JSON route | 5/5 equal |
| B3 | NDJSON request → reveal | 5/5 equal |
| B4 | NDJSON obfuscate → JSON reveal | 5/5 equal |
| B5 | Detect stream | 15 findings |
| B6 | Synthesize stream | 0 originals; seed replay identical |
| B7 | Broken line | 400 `ndjson_invalid` |
| B1b (info) | Bare `name` key, NER off | names pass through. By design (N4). |

**C. Idempotency-Key**

| # | Check | Result |
|---|---|---|
| C1 | Same key + same body | replay header, same `trace_id`, equal body (minus `duration_ms`) |
| C2 | Same key + other body | 422 `idempotency_key_reused` |
| C3 | Malformed keys | 4/4 → 422 `idempotency_key_invalid` |
| C4 | Replay without caller key | fresh kid (D2: no key material cached) |
| C5 | Same key on another route | separate scope |
| C6 | Detect, reveal, synthesize + seed, NDJSON obfuscate | all replay identically |

## D. Documents (`probe_l1.py` §D, `ocr-redacted-*.counts.json`, `redacted-*.pdf`)

The OCR re-read is independent of the server: tesseract 5.5 from `data-privacy-services:local`
(`--rm --network none`, image on stdin, 300 dpi). The originals are the 12 values the generator
wrote (`tools/make_demo_docs._values()` + the customer id).

| Input | Server report | Text layer (pypdf / pdfplumber / pdfium / text operators) | OCR positive control on the input | OCR of the redacted output |
|---|---|---|---|---|
| `kyc-text.pdf` | 14 boxes, chars 0, operators 0, `ocr_hits` 0, passed | **0 / 0 / 0 / 0** | 12/12 | **0 originals** (35 words read) |
| `kyc-scanned.pdf` (OCR pages 1–2) | 14 boxes, zeros, passed | **0 / 0 / 0 / 0** | 12/12 | **0** (36 words) |
| `id-card.png` (OCR page 1) | 6 boxes, zeros, passed | **0 / 0 / 0 / 0** | 6/6 on the card | **0** (18 words) |

Other document checks:
- D1 `documents/detect`: 14 boxes, 11 types.
- D3 `kyc.txt` obfuscate → reveal: 0 left, **==** original.
- D4 `kyc.docx` obfuscate → reveal: 0 left, 7 originals restored.
- D5 `customers.csv` synthesize: 20/20 rows, 0 same-column equal values, 0 source values anywhere.
- D6 documents `Idempotency-Key`: replay; another file → 422 `idempotency_key_reused`.

## E. Schema synthesis (`probe_schema.py`, `probe_schema-{http,ner}.log`, `schema-20x100.counts.json`, `pytest-schema*.log`)

**Replay:**
- **E1:** DDL → spec → synthesize. Spec + returned seed at **25 and 1,000 rows** gives byte-identical `rows` and identical CSV text. Only `trace_id` and `duration_ms` differ. A fresh seed differs.
- **E2:** same sample + seed, three times identical.

**E3 (HTTP, stack, NER off):** the 7-column, 4-row sample, 100 rows, `en_IN`, 20 calls.
- **20/20 × 200**, verification passed 20/20, 4 sample redraws in total, 0.03–0.07 s per call.
- Independent comparison over 2,000 rows, **0** in every sensitive category:
  - same-column copies of cust_id, full_name, email, balance or opened;
  - strings holding a sampled full name, e-mail or date;
  - e-mails whose whole local part is a sampled one;
  - sampled IDs as a whole digit run.
- Non-zero counts are by design:
  - same-column city 12 (CITY is `SWEEP_EXEMPT`);
  - 20 strings with a sampled given name or surname (common en_IN vocabulary; whole names 0).
- Round 1's one "local part" count was a substring coincidence. This round it is measured exactly; the substring count was 0 too.

**E5 (in-process, Presidio + `en_core_web_sm`, stub LLM):**
- **20/20 × 200**, passed 20/20, 0.06–0.76 s.
- Same zeros; city 22, by design.
- **E5b:** with NER on, the planted label column is detected as PERSON_NAME and generated, so 0 sampled names are written.

**E4 — planted values (HTTP):**

| Plant | Result |
|---|---|
| Sampled full names as the categorical labels of another column (2 variants × JSON and CSV) | **422 `leakage`, 0 echo** (4/4) |
| Key plants (each × JSON, CSV and `generator-spec`, 24 calls): a sampled name; the dotted e-mail (round 1's B1); the e-mail in upper case; a key with the e-mail inside other text; the e-mail one level down; an undotted e-mail as key and value; a mobile as key and value; a name two levels down | 200 with the key aliased `field_8`. **0 echo** anywhere in the answer (rows, spec, CSV header and body). This is the documented behaviour change of `c7ad14f`. |
| Control: sampled e-mails as the values of another column | 200, detected and generated, 0 sampled e-mails in the answer |
| Information: a 1–9 ID column whose sample fills its range | written back. By design (M1-DEVIATIONS: unswept short IDs that cannot get away keep their values) |

**Round 1 B1 re-check** (`diag_p2b.log`, `diag_p2c.log`, the exact round-1 scripts):
- The dotted e-mail key: rows holding it **0/20** (was 20/20), spec fields 0, body 0.
- Undotted e-mail, phone, CSV header and `generator-spec`: all **False** (all True or 422 before).

**Release-code tests:**
- `tests/test_api_schema.py` + `tests/test_schema_profile.py`: **102 passed**, 1 skipped (spaCy model absent from `.venv`; E5 covers NER on).
- Planted subset: **29/29 passed**. It covers 9 forms forced into the rows, a spec literal, a mobile with a country code, ID-like keys and lists, sampled-value keys (JSON/CSV) and the three-level key path → 422.

## F. Layer 1 stopword surnames (new this round, `probe_surnames.py`, `probe_surnames.log`)

This checks the `c7ad14f` `names.py` change, over HTTP, NER off. The input is five invented
sentences, each with a Mr/Ms/Mrs name whose surname is Date, Best, Holder, Branch or Le.
- Each sentence ran 3 times under synthetic-realistic, synthetic-strict and gdpr-pseudonymise: **45/45 × 200**.
- Surname kept **0**, given name kept **0**.
- Obfuscate → reveal **15/15 equal** to the input.

## G. Live Vertex (`vertex_live.log`, `prompts_sent.this-run.json`)

The command was `venv-final/bin/python -I ~/.cache/dps-build/vertex_live/vertex_live_test.py <key path> ziffy-staging`. The script's sha256 is `03252588…bcc8c8`, the same as round 1. It ran 12:31:45–12:31:58 and **ended `PASS`, exit 0**.

| Section | Result |
|---|---|
| readyz | vertex, `coin_tls: verified` |
| Gateway smoke | gemini-2.5-flash, 3.49 s |
| T3 labelling | 200: 3 `llm_label` findings (PERSON_NAME ×2, STREET_ADDRESS), `raw_pii_sent` 0, no raw value left |
| Text rewrite | 200, `used: True`, `fallback: None`: the rewrite applied this time (round 1: `leak_guard`). No raw value left. |
| Generator spec | 200, `source=llm`, names kept (`cust_id` …); schema/synthesize 200; 0 sample values reproduced |
| **P1** | 4 prompts, **0 raw values** |

My own scan of the prompts found no person-identifying sample token: no name, e-mail, ID, phone or city.
- The rewrite prompt carries only placeholders plus non-personal words: a bank name and an amount, which the AMOUNT keep rule allows.
- The spec prompt aliases `cust_id` as `field_1` and carries shapes only.

## H. Log scan (`log_scan.json`)

- I scanned the app and worker logs since 07:01Z for the 46 invented values my probes and the live run sent: 1,243 app lines and 84 worker lines.
- Result: **0 raw and 0 normalised hits**.
- `vertex_live.log` holds 0 raw input values.

## Non-blocking observations (carried to the owner)

**N1 — still open, systematic, not in the CHANGELOG.** An LLM `pattern` written in the prompt's shape notation is executed literally.
- In this run, as in both of round 1's live calls (3/3), Gemini proposed `segment: pattern {"template": "xxxxxx"}`. Every synthetic row's segment is therefore the literal `xxxxxx`.
- `_invented_labels` (in `dps/engine/synth/schema_profile.py`) ignores shape-valued model output only for `choice`, and `merge_llm_spec` accepts the pattern.
- This is a quality defect only: no sample value is involved.
- `use_llm` defaults to true, so production (Stellar) users of sample synthesis would see it.
- Fix:
  - treat a `pattern` whose template is one of the field's shapes (or any `x`/`X`/`9` shape) like an invented `choice` (keep the heuristic);
  - or convert it with `_template()`.
- Otherwise list it under Known limitations.

**N2 — persists.** `en_core_web_sm` sometimes tags the word "Aadhaar" as PERSON_NAME: 5 of 12 random Aadhaars with the stub (`diag_llm-stub.log`).
- When that happens, the rewrite's leak guard refuses, which is P1-safe: nothing is sent.
- The deterministic text then writes a surrogate name for the word.
- This run's live rewrite did apply. The CHANGELOG line "fell back … in both runs" is now one data point behind.

**N3 — persists.** The NDJSON `report` line aggregates verification as `{passed, checked}` with no `status` or `coverage` (`diag_ndjson_report.log`).
- A stream of undetected bare-`name` values reads `passed: true, checked: 0`, while the JSON route says `partial` with coverage.
- This matches the module's documented line format, so it is a reporting gap, not a contract breach.

**N4 — by design, recorded:**
- The DATE kept in free text.
- A bare `name` key needs value evidence with NER off.
- Short unswept IDs that fill their range are written back.
- CITY is `SWEEP_EXEMPT`.
- New this round: under synthetic-realistic a NER LOCATION (low sensitivity) is kept by `keep_non_sensitive`. So the live rewrite's `{LOCATION_1}` placeholder was refilled with the input's city. The placeholder protected P1; the city survives by policy, and synthetic-strict replaces it.

**N5.** Someone else's untracked `docs/screenshots/v1.0.0/` is still in the main checkout. The release step should commit it or drop it on purpose.

## End state

- The stack is left running, unchanged: 0 restarts, ready.
- My only containers were throwaway `tesseract` runs (`--rm --network none`).
- No `dps_test_*` / `dps_fresh_*` database exists.
- No repository file was modified.
- `venv-final` is kept.
