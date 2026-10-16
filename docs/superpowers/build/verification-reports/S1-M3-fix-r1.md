# S1-M3 fix round 1 — post-merge verification of `main` after the M3 merge

**Verdict: all 7 blocking findings fixed** (engines B1–B4, security B1, privacy-console B1–B2). Each fix
has a test that fails on the code before it (33 failures + 1 collection error on the reverse-applied
fix) and passes after it. One more defect, found by the smoke `--m3` run on the first rebuilt stack,
was introduced by this round's B2 fix and is fixed too (a name word matched inside a kept city word made
3 of 20 000 demo rows fail closed). Gates, smoke and live source-vs-target proofs are green on the
rebuilt stack. Commit `5c11e49` on `main`, pushed by `tools/dev/autopush.sh` (gitleaks clean).

| | |
|---|---|
| Date | 2026-10-14, 18:40–20:50 IST |
| Working copy | `/Users/neelu/document_intelligence/data-privacy-services`, branch `main`, base `144cc65` (clean at start) |
| Stack | compose project `data-privacy-services`, default ports (app 8400, dps-db 5441, source-pg 5442, MinIO 9010/9011, Oracle 1522, Mongo 27018, OCR mock 5016, tokenizer-mock 8430). Rebuilt three times with `DPS_TOKENIZER_MOCK_PORT=8430 docker compose --profile docs --profile sources --profile oracle --profile tokenizer up --build -d`; the final image (`0f590d70d250`) carries the final code. |
| Python | `.venv` of the main checkout; contract §12.2 env in `~/.cache/dps-build/s1-m3-fix-r1/env.sh` |
| Logs | `~/.cache/dps-build/s1-m3-fix-r1/logs/` (`before-fix.log`, `before-fix-vitest.log`, `after-fix*.log`, `gate-*.log`, `gates-summary.log`, `smoke*.log`, `seed.log`, `compose-up*.log`) |
| Probes | `~/.cache/dps-build/reports/S1-M3-fix-r1/` (`live_engines.py` → `live_engines_fix1.json`, `live_security.py` → `live_security_*.json`, `live_console.py` → `live_console.json` + `shots/`, `logscan.py` → `logscan.json`) |
| Values printed or stored | none: every probe output is counts, codes, ids, paths or column names |

## 1. Findings, fixes and tests

| Finding | Fix (code) | Failing-first tests |
|---|---|---|
| **engines B1** — key-less Oracle synthesize failed `leakage` (ROWID counted as a copied key) | `jobs_run.copied_personal_columns` leaves pseudo keys (`ColumnInfo.pseudo`, from the snapshot's and the live shape) out of the written and key sets; the guard still names real keys and classified columns copied as source values. **Beyond the finding:** a `files` target wrote the ROWID pseudo key as a column (a locator back to the source row in a synthetic file): `files.file_columns` drops pseudo columns, `resolve_target` (files) leaves them out of `write_columns`/`column_types`, the manifest lists `plan.target.write_columns`. | `tests/test_da_m3_engines.py::test_the_prepare_guard_never_counts_a_rowid_pseudo_key_as_copied` (table, files); `tests/connectors/test_oracle_keyless_synthesize.py` (integration: into a table — 5 table columns, no ROWID; into parquet — no ROWID column; CUSTOMER_ID 100 % in [10⁹, 2·10⁹), 0 source keys, 0 source address lines) |
| **engines B2** — synthetic (and obfuscated, N1) CRM notes kept the source first name | `batch._row_value_spans` also searches each word (≥ 3 letters) of a planned `PERSON_NAME` value, in **both** modes and in `unclassified: detect` leaves; whole words, caseless (Unicode case folding) via `_whole_word_spans` — a substring test on the folded text, no regex compiled per row. Name-word spans carry detector `row_value_word`: transformed, but not originals of the per-row sweep (§3). Address words were left out on purpose (common words). | `tests/test_da_free_text_spans.py` (Oracle flat + MongoDB nested shapes, both modes, 120 demo customers: 0 first names kept; caseless/whole-word rules; property test vs a brute-force caseless reference and, on ASCII, vs the former regex; the kept-city regression); `tests/e2e/test_m3_free_text_names.py` (Oracle `CRM.CUSTOMERS` and MongoDB `crm.profiles` demo shapes, 600 customers, default policy, no NER, both modes: 0/600 row-aligned notes keep the first name, control ≥ 95 %, evidence passed) |
| **engines B3** — MongoDB → `files` failed in every format | `files._plain` converts nested values recursively (datetime → ISO-8601, Decimal/Decimal128 → digits, ObjectId → hex, Int64 → int, bytes → hex, non-finite float → text) for JSONL and the JSON text of CSV/Parquet; `docpaths.field_columns`: a field holding a sub-document or an array (or mixed) is one column, kind `json`, type `document` (Parquet `string`); manifest type `document`. | `tests/connectors/test_files_documents.py` (offline: demo document shape in jsonl/csv/parquet, BSON leaf rules, manifest types); `tests/connectors/test_files_m3_engines.py::test_mongo_documents_write_to_files` (integration: obfuscate jsonl/csv/parquet + synthesize jsonl, 600 docs, row-aligned 0 survivors per sensitive leaf) |
| **engines B4** — Oracle `TIMESTAMP(7-9)` → parquet failed | `files._arrow_type`: a timestamp the connector carries as text (`ts_precision > 6`, or Oracle `… WITH TIME ZONE`) is a Parquet `string` (exact FF9 / offset); PostgreSQL `timestamp with time zone` stays a UTC timestamp. | offline FF9/offset tests in `test_files_documents.py`; `test_files_m3_engines.py::test_oracle_timestamp9_writes_to_parquet_as_exact_text` (obfuscate + synthesize, 1 200 rows, kept column equals the source FF9 text row for row) |
| **security B1** — `table`/`files` writes into a production-tagged connection needed no admin | `targets.validate_run`: admin for **any** write mode into a production-tagged written connection (`confirm_write` stays `in_place`/`append` only, checked first); `targets.check_retry_scope` + `POST /jobs/{id}/retry`: the job's target connection (else its source), as tagged now, checked before the key checks. Console copy (wizard step 1 help, Admin callout) follows the rule. | 8 new cases in `tests/connectors/test_targets.py::test_validation_table`; `tests/api/test_production_write_gate.py` (PostgreSQL, Oracle, MongoDB through the API: table truncate_insert / upsert / files into production → 403 on validate and run, no job created; admin `{"ok": true}`; non-production target allowed; failed production `in_place` and `table` runs: retry 403 for `jobs`, 200 for admin) |
| **privacy-console B1** — Evidence page labelled MongoDB leaves by top-level field | `EvidencePack.tsx`: `columnLabel()` in section 3 (column cell, both kept lists) | `frontend/src/pages/EvidencePack.test.tsx` "EvidenceSheet for a MongoDB pack" (2 tests) |
| **privacy-console B2** — MongoDB packs had no per-leaf counts | `evidence.build_pack` reads counts with `docpaths.column_key(path, name)` (the transformer's key) | `tests/test_evidence.py::test_a_mongo_pack_counts_each_nested_leaf_under_its_path` |

**Before/after proof** (`logs/before-fix.log`, fixed code reverse-applied over the new tests, then
re-applied byte-identical): **33 failed, 1 collection error** (`test_files_documents.py` imports the new
helpers), 57 passed (the existing tests in those modules and admin controls), 23 min. Vitest before:
2 failed / 10 passed. After: 98 passed (`after-fix.log`); final tree: 135 passed incl. the adapted
modules (`after-fix-final.log`).

## 2. Contract, HANDOFF, deviation log

- Contract §5.3 (gate row, FilesSpec written columns, pseudo keys in the guard, free-text spans), §7.1
  (`values` key), §8.1 (admin for every production write and its retry), route map (`/retry`).
- HANDOFF: MongoDB `files` supported; "Who may write a production connection"; names in free text; the
  post-merge fix summary in the M3 merge notes.
- `docs/superpowers/build/M3-DEVIATIONS.md`: new section "M3 post-merge verification round 1 fixes on
  main" (each finding, the found-while-fixing ROWID file column, the smoke-found false positive, the
  two adapted sweep tests, performance, and the fsec coordination note).

## 3. Found while fixing

1. **ROWID in files** (above): no ROWID column in any file or table now.
2. **Smoke `--m3` failure on the first rebuilt stack** (`logs/smoke-m3.log` history; 37/38 then):
   MongoDB and Oracle runs failed `leakage` at 9 000 rows. Offline repro of the run's own plan on all
   20 000 demo customers: 3 hits `($.NOTES, PERSON_NAME, contained)` — an 8+-letter first name that
   begins the row's kept city word. The sweep's `contained`/`name_part` rules are substring rules, so a
   name word must not be a sweep original; the whole name still is. Repro after the fix: 0 hits; regression
   test in both modes (strict).
3. **Two existing sweep tests** built their "undetected copy" from the row's own name as words; that copy
   is now replaced, so each glues the name into one word (still undetected → still fails closed) and
   asserts the spaced copy is replaced (`tests/test_batch.py`, `tests/test_batch_documents_m3.py`).
4. **Performance**: the first B2 version compiled one regex per needle and row (21 % of an obfuscate
   transform under the profiler); `_whole_word_spans` brings it to 4 %. 4 000 demo rows, 6 planned
   columns: obfuscate 1 265 → 1 205 rows/s, synthesize 875 → 961 rows/s (noisy: suites ran alongside).

## 4. Gates

The marker subsets (M1, M2, M3, browser, conformance) ran on the tree before the last `batch.py` change
(§3.2); the final whole-suite run, a superset with every service required, ran on the committed tree.
Smoke and live proofs ran on the final image.

| Gate | Result |
|---|---|
| `ruff format --check dps tests tools` / `ruff check` / `mypy dps` | 380 files formatted / all checks passed / no issues in 134 files |
| `pytest -q` whole suite, services required | **2665 passed, 8 skipped, 0 failed** (10:09; `gate-full-final.log`, on the committed tree; same 8 environmental/by-design skips as the gates verifier) (first run on the tree before the last two changes: 2661 passed, 2 failed — the two adapted sweep tests, §3.3) |
| M1 `-m "docs or ocr"` | 139 passed, 2 skipped |
| M2 `-m "db or pg_source or s3"` | 362 passed (was 341; +21 new) |
| M3 `-m "oracle or mongo"` | 174 passed, 1 skipped (capability skip) |
| browser (console on main) | 9 passed (the 54 tracked `m3-shots` PNGs they rewrite were copied to `~/.cache/dps-build/s1-m3-fix-r1/m3-shots-from-run/` and restored to HEAD) |
| conformance (3 engines) | 29 passed, 1 skipped |
| frontend `npm ci && npm test && npm run build` | node 42/42; vitest 16 files / 234 passed (was 232); build OK (chunk-size warning pre-existing) |
| `seed_sources.py --target all --rows 20000 --seed 7` ×2 | run 1 restored demo size (the suites reseed at test size), run 2 all `unchanged` |
| smoke M0–M2 (`DPS_SMOKE_REQUIRE_OCR=1`) | all 34 checks passed |
| smoke `--m3` | all 41 checks passed (3/3 runs 20 000 rows each, consistency 3/3, evidence 3/3 verified, raw PII to LLM 0) |

## 5. Live proofs on the rebuilt stack (demo data, one job group, counts only)

`live_engines.py fix1` — 15 runs, **all succeeded, all evidence `passed`, `review_required: false`,
0 null `values` in any pack**:

| Run | Result |
|---|---|
| Oracle `CRM.ADDRESS_HISTORY` (key-less, ROWID) synthesize → table, copula → table, synthesize → parquet | 40 000 rows each; tables hold the 5 table columns, **no ROWID**; parquet columns and manifest without ROWID; CUSTOMER_ID **40 000/40 000 in the pseudonym band, 0 source ids**; ADDR_LINE1: 5 values equal *another* customer's address (surrogate-pool collisions: 0 share VALID_FROM or CITY with that address's source owner, none in "Flat …" form) |
| Oracle `CRM.CUSTOMERS` synthesize / obfuscate → table | 20 000 aligned on the kept FF9 `LAST_CONTACT_AT`; **first name kept in notes 0/20 000** (control: 19 972 source notes hold it); FULL_NAME and EMAIL row_same 0; source e-mail in notes 0 |
| MongoDB `crm.profiles` synthesize / obfuscate → collection | 20 000 aligned on the kept balance; **first name kept in notes 0/20 000** (control 19 972); fullName and email row_same 0 |
| MongoDB → files jsonl / csv / parquet, obfuscate and synthesize | 6/6 succeeded, 20 000 rows each; manifest document columns `accounts, contact, kyc, profile, tags`; source e-mails in the written bytes 0 (text formats), in parquet `contact` 0 |
| Oracle `CRM.CUSTOMERS` → parquet, obfuscate and synthesize | 20 000 rows; 20 000/20 000 aligned on the **exact FF9 text**; EMAIL / FULL_NAME row_same 0; first name kept in notes 0 |

`live_security.py` — **21/21**: a `jobs`-only key against a production-tagged MongoDB connection (target
role) and a production-tagged pvc: table truncate_insert into an existing collection, table upsert, table
create and files → **403 `admin_scope_required` on validate and run**; admin validate `{"ok": true}`; no
job created; the 25 pre-existing documents untouched; an admin's failed production run: `jobs` retry 403
(bare and with the job's key), job still failed; admin retry passes the gate (409 `key_material_required`
bare, 200 queued with the key). Scratch database dropped, probe connections deleted, probe keys disabled.

`live_console.py` (private Playwright `~/.cache/dps-build/pw-main`, 1440 dark/light, 390 dark) — MongoDB
obfuscate pack: section 3 labels `accounts[].iban, contact.addresses[].line1, contact.addresses[].pin,
contact.email, contact.mobile, customerId, kyc.taxId, notes, profile.dob, profile.fullName`; every
VALUES cell a count; kept note "7 columns kept, no entity: _id, accounts[].balance,
contact.addresses[].type, kyc.verifiedAt, riskScore, segment, tags[]". MongoDB synthesize and Oracle
packs correct too. 0 console errors, no horizontal scroll at 390 px. Shots in `shots/`.

`logscan.py` (since the final restart): app/worker/tokenizer **0 hits** of 159 732 source values and
**0 whole-word hits** of 865 CRM first names; 0 tracebacks, 0 error lines; 2 warnings = the smoke's
`auth_rejected` checks; planted control found. `dps_llm_raw_pii_sent_total 0.0`.

## 6. Notes for other streams

- **fsec (`f112044`, production bound to the credentials)** edits the same lines of `validate_run`, the
  retry route and adds `check_retry_scope(job, written, principal=…, production_refs=…)`. At its merge keep
  both rules: the admin check **outside** the `in_place`/`append` block for every write mode, with
  `holds_production_credentials(written, production_refs)`; `check_retry_scope` for every write mode with
  `production_refs` (main's signature lacks only that keyword).
- `POST /jobs/{id}/resume` of a paused production run is not re-gated (it continues an authorised run);
  noted for the fsec redesign.
- Packs sealed before this fix keep their empty MongoDB leaf counts (sealed and hashed by design).

## 7. Commit and state

- Commit `5c11e49` on `main`: `fix(integrate): M3 post-merge r1 — key-less Oracle synthesize, names in notes,
  document and FF9 files, production write gate, MongoDB evidence` (author NilenduGanguli
  <nilendu.ganguli@gmail.com>, no trailer), explicit pathspec of 25 files (7 backend, 4 console, 3 docs,
  11 test files of which 5 new). Pushed to `origin/main` by `tools/dev/autopush.sh` at 20:49 (held 0).
- Stack left running (final image), demo sources at demo size (seed 7, 20 000). Probe residue (value-free):
  the probe jobs, job group `s1m3-fix-r1-fix1-*`, targets `CRM_MASKED.*_FIX1`, `crm_uat.profiles_*_fix1`,
  MinIO `dps-exports/s1m3-fix-r1/fix1/…`, two disabled probe keys. Leftover test databases/users from
  interrupted runs were dropped (`dps_test_22407` on dps-db and four `DPS_T_*` Oracle users, left by the stopped first gates run and
  an earlier session; no MongoDB leftovers).
