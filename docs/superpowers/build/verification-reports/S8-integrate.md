# S8-integrate: live-Vertex fixes integrated on `fllm`

**Verdict: PASS.** `fpersona`, `fspec` and `fidcheck` are merged into `fllm` (head `bdbfa16`, pushed by the owner's
autopush at 02:58). All three merges were clean. The two findings the fspec review r2 left open (B1, B2) were already
fixed test-first on `fspec` in `a0e2e59` (fix round 2). I re-verified them on the merged tree with the reviewer's own
probes: the outcomes are identical, so no new `fix(fllm)` commit was needed.

Gates:
- ruff and mypy are clean.
- Whole suite under `DPS_TEST_REQUIRE_SERVICES=1` on the lane stack (docs + sources + oracle): **3 900 passed,
  81 skipped, 7 failed** of 3 988.
  - 2 failures came from the environment and pass once it is set right (§3).
  - 5 are the known baseline failures that fail the same way on the pre-merge base.
- The live Vertex run ends **PASS twice**.

No customer value, key or secret was printed. The SA key was only passed by path to the live script.

Evidence: `/Users/neelu/.cache/dps-build/reports/S8-integrate/`.

## 1. Lane setup (worktree `/Users/neelu/document_intelligence/dps-wt-fllm`)

- **Lane files.** `.env`, `.env.lane` and `lane.sh` were created; all are git-ignored through the shared
  `info/exclude`, which already listed them.
  - `.env`: `DPS_IMAGE_TAG=fllm`, ports 8631/5631/5632/9631/9632/1631/27631/5633/8632/8633/9633. All of them were
    free (checked with lsof).
  - `.env.lane`: the m3 template format, with only the ports changed.
  - `lane.sh`: copied from m3.
- **Venv.** It already held `[vertex,ner]` and `en_core_web_sm`. I added the missing test extras with
  `uv pip install -e ".[ner,fpe,vertex,docs,db,connectors,uitest,e2e,scanner,tabular,dev]"`; this added
  playwright 1.55.0.
  - `[synth-ml]` is not installed; it is never in an image.
- **Images and stack.**
  - `docker compose --profile docs --profile sources --profile oracle build` was built after the merges, so the
    images hold the merged code.
  - Then `up -d`. Compose project `dps-wt-fllm`; nothing of another project was touched.
  - The stack was stopped at the end with `docker compose ... stop`.

## 2. Merges (`git merge --no-ff`, author NilenduGanguli, no trailers)

| Commit | Merge | Conflicts | Content |
|---|---|---|---|
| `aa0eddf` | `fpersona` (`b5a9b75`, 4 commits) | none | one persona per person, name-part keys (16 files) |
| `8cf4199` | `fspec` (`a0e2e59`, 7 commits, based on `0fd76d0`) | none | schema-from-sample sweep, steering and bounds (9 files) |
| `bdbfa16` | `fidcheck` (`762418d`, 3 commits) | none | D-A identifier check on every engine (13 files) |

- No file is touched by two of the branches.
- One semantic coupling: `schema_gen` uses `SurrogateEngine`, which `5cef802` and `fpersona` changed. It is covered
  by the full suite and by the probes in §4.

## 3. Gates (all under `./lane.sh` and `DPS_TEST_REQUIRE_SERVICES=1`)

| Gate | Result |
|---|---|
| `ruff format --check dps tests tools` / `ruff check` | 438 files already formatted / all checks passed |
| `mypy dps` | no issues in 150 files |
| `pytest -m "oracle or mongo"` (M3) | **190 passed, 1 skipped** (Mongo has no composite keys, by design), 0 failed, 6:03 (`pytest-m3-oracle-mongo.log`, `junit-m3.xml`) |
| `pytest -m "not (oracle or mongo)"` (everything else) | **3 710 passed, 80 skipped, 7 failed**, 18:15 (`pytest-rest-require.log`, `junit-rest.xml`) |
| Per gate, from the junit files: M1 `docs or ocr` | 141: 139 passed, 2 skipped (tesseract) |
| Per gate: M2 `db or pg_source or s3` | 441: 439 passed, 1 skipped (key-less PG table, by design), 1 failed (precondition, see below) |
| Per gate: M3 `oracle or mongo` | 191: 190 passed, 1 skipped |
| Per gate: M4 `browser` (no scanner profile needed; the demo site runs on the lane port through node) | 271: 193 passed, 73 skipped (tesseract), 5 failed (below) |
| `tools/smoke_test.py` with `DPS_SMOKE_REQUIRE_OCR=1` (M1 + M2 checks against the lane app) | **all 34 checks passed** (`smoke-m1-m2.log`) |

The 7 failures:
1. **`tests/console/test_console_m2.py::test_in_place_on_a_table_with_an_entity_typed_key`** is an environment
   precondition. On a fresh stack the M2 demo connection `bank-restore-rw` does not exist yet; the smoke registers
   it. FSEC-merge-m4 and M3-G5 hit the same thing. After the smoke, `test_console_m2.py` gives **3 passed**
   (`pytest-console-m2-after-smoke.log`).
2. **`tests/test_config_m2.py::test_m2_defaults_keep_l1_database_free`** was caused by my lane file: the first
   `.env.lane` also exported `DPS_WORKER_METRICS_PORT=9633`, and `Settings()` reads it.
   - I removed that export and `DPS_TOKENIZER_MOCK_PORT` from `.env.lane`; they stay in `.env` for compose. The
     file now matches the m3 template exactly.
   - Re-run of all `test_config*` files plus `tests/db/test_worker_process.py`: **68 passed**
     (`pytest-config-rerun.log`).
3. **Known baseline, 5 failures:**
   - `scanner/test_capture.py::test_obfuscated_capture_tokens_the_dom_and_reveals_with_the_key`
   - `scanner/test_capture_residual.py::test_masked_capture_boxes_every_copy_of_a_detected_value`
   - `scanner/test_crawler.py::test_full_demo_crawl_in_memory`
   - `scanner/test_crawler.py::test_dialogs_dismissed_and_popups_closed`
   - `test_playground_samples.py::test_text_sample_with_ner_on_still_never_returns_the_name`

   These are the same five tests that fail on the pre-merge bases `5cef802` (persona review r1/r2) and `e1f3a3a`
   (spec review r1). No feeder branch touches the scanner or the playground.
   - Correction to the earlier explanation: the playground test does not fail because the OCR mock is down. The OCR
     mock was up here and the test still fails. With NER on, verification is `partial` because coverage lists two
     gaps by design (types recognised only by a field name; types found only next to their label; main
     `4286620`). The test still expects `passed`. The test file is identical on `main`, so this is a stale
     expectation for the owner to decide, not a regression.

Skips (81 in total):
- 75 need tesseract, which is not on the Mac; the image has it.
- 2 are by design (key-less PG table, Mongo composite keys).
- 1 is "frontend not built"; no frontend file changed.
- 3 need the `mostlyai`/`pandas` extra (`[synth-ml]`, never installed).

## 4. Open findings from the feeder reviews

`fpersona` (review r2) and `fidcheck` (review r1) were PASS with nothing open.

`fspec` review r2 left B1 and B2 open. Both were fixed in `a0e2e59`, test-first: on the unchanged code `c5b5fc2`,
6 tests failed and 90 passed, per `S8-spec-fix-r2/tests-before-fix-at-c5b5fc2.log`. The merged tree has exactly
what the review asked for.
- **B1.** `_BREAK = ((?:[^\w\-/.:,]|_|,(?!\d{2,3}(?!\d)))+)`. The fix uses `\d` where the review wrote `[0-9]`,
  so digits of any script count; this is stricter and documented. The whole-number pattern
  `_INTEGRAL_TEXT = \s*([+-]?)(\d+(?:,\d{2,3})*)\.0+\s*` uses the same grouping.
  - Tests: `1001,1002`, `[1001,1002]`, `1001,1002.00` and the Devanagari form are in LEAKS; `10,01,002`,
    `1001,25` and `48,250.50` are in JOINED.
  - `_naive_found` / `_groups_digits` apply the comma rule.
  - The new route test `test_comma_lists_of_ids_do_not_repeat_a_sampled_id` was added.
  - Docs updated: module docstring, `_SampleValues`, `verify_against_sample`, `api-m1.md`, M1-DEVIATIONS
    "Fix round 2".
- **B2.** `_words` applies NFKC first. `１００４．００` is in LEAKS, and the property test skips strings whose NFKC form
  is a whole number.

Re-verified on the merged `fllm` tree (`spec-probes/`). These are the review r2/r1 probes, unchanged, run through
the real route; output is counts only.

| Probe | Merged `fllm` |
|---|---|
| `r2_planted` NER off / NER on | 0 unexpected outcomes. NER on: 43×422 `leakage` with no echo (39 real-copy forms + 4 model literals) and 10×200 (joined forms and accidents); **identical** to the fix-r2 output |
| `r2_wide` 600→1 000 ×3, 200→1 000 ×3, 600→10 000 ×2 | 200 in 8/8 runs; **0** list values holding a sampled ID; 0 same-column equality; 0.7-0.9 s (1 000 rows), 7.9-8.2 s (10 000 rows) |
| `r2_accept` stub/model × NER off/on, 20×100 each | 80/80 200; 0 sampled-ID tokens; 0 sample values in the prompt |
| `p13` separators | identical to fix-r2; `json_list_int` and `comma_nospace` refused; guard = sweep |
| `p14` code columns ×3 / `p16` dense id ×3 / `p10` doomed | 200 3/3, 0 tokens / 200 3/3, 0 equal to a sampled `cust_id` / 422 `leakage`, no echo (0.04 s, 0.31 s) |
| Log scan of the raw probe output (5 model + NER calls, 53 planted calls) | **0 of 24** sampled values; positive control 24/24 |

## 5. Live Vertex run

Command: `.venv/bin/python -I ~/.cache/dps-build/vertex_live/vertex_live_test.py <SA key> ziffy-staging`. The
script ran unchanged; the key is referenced by path only.

| Check | Run 1 | Run 2 |
|---|---|---|
| Smoke (direct gateway) | vertex / gemini-2.5-flash, `READY` | same |
| T3 labelling via `llm_label` | 200; 3 findings via `llm_label` (`beneficiary_nm`, `kin_contact_line` PERSON_NAME; `residence_ln1` STREET_ADDRESS); `raw_pii_sent` 0; 0 raw values in the output | same |
| Text rewrite | 200; 0 raw values in the output; `fallback: placeholders_missing` (fail-closed) | same |
| Generator spec | 200, `source=llm`; field names `cust_id, full_name, email, city, segment, balance, opened` (the caller's) | same |
| `schema/synthesize` | **200**; sample values reproduced: none | same |
| P1: raw values in the prompts sent to Vertex | **0 in each of 4 prompts** | **0 in each of 4** |
| Result | **PASS** (exit 0) | **PASS** (exit 0) |

Logs: `vertex-live-run1.log`, `vertex-live-run2.log`.

## 6. Other proofs (counts only)

- **Source vs target.** The smoke's table run `bank.customers → bank_uat.customers_smoke` (20 000 rows): rows were
  aligned on the 8 kept columns and 20 000/20 000 aligned. **0 same-row survivors** in `full_name`,
  `national_id`, `tax_id`, `email`, `mobile`, `date_of_birth`, `customer_id` and `notes`.
  - Cross-row: 0 equal to any source value for names, national ids, tax ids, e-mails and mobiles.
  - `customer_id` has 2 cross-row FF1 crossings and `date_of_birth` 12 549 (date_shift over a small domain). Both
    are the known classes from earlier verifications (3-7 and about 12 500).
- **Container logs.** All 12 lane containers were scanned (`logscan/lane_logscan.txt`) for 119 763 seeded values
  (names, national ids, tax ids, e-mails, mobiles, account numbers, IBANs, contact values ≥ 8 characters), for the
  local bootstrap key and the job KEK literal, and for tracebacks: **0** of each. Positive control: 25/25.

## 7. Observations for the owner (no change made)

- **LLM rewrite is fail-closed and stochastic.** Both live runs fell back with `placeholders_missing`.
  - A diagnostic with the same text shape (placeholder sets only, `scratchpad/rwdiag`) had the rewrite used in 2 of
    3 calls. The rejected call had the model invent placeholder types (`EMPLOYEE_ID_1` and others) and drop
    `AADHAAR_1`/`CARD_PAN_1`/`LOCATION_1`.
  - A text whose Aadhaar fails Verhoeff fell back 3/3 with `llm_introduced_entities`.
  - The safety checks behave as designed. How often the rewrite applies is a product question.
- **Generator spec on the 4-row live sample.** `segment` came out as a pattern (`xxxxxx`), not a choice. The 4 rows
  hold too few repeats to make it an enum (it needs each value at least twice), as documented.
- **Console tests overwrite tracked screenshots** (`docs/superpowers/build/m3-shots/*.png`, 54 files): the known
  "console_check overwriting tracked shots" polish item. I copied them to `m3-shots-from-run/` and restored the
  tracked files only (worktree clean).
- **Still open from the feeder reviews, all non-blocking** and listed as owner decisions there:
  - flaky `tests/test_ndjson.py::test_one_audit_event_per_stream` (persona r2: lengthen the test's e-mails); it
    passed in this run;
  - spec N-1..N-4;
  - persona's over-matching keys (`PRI_LST_NAME`, `MID_NAME`, `PRODUCT_NAME_FAMILY`) and SAP `NAME_LAST2`.
- **`fllm` is based on `40d8b34`**, and `main` has moved 6 commits ahead (`1ce701d`). `main` was not merged here, as
  the brief asked.

## 8. Commits and deviations

- New commits: the three merge commits only (`aa0eddf`, `8cf4199`, `bdbfa16`).
- No `fix(fllm)` commit and no new deviation entry: B1/B2 had already been fixed and logged in M1-DEVIATIONS
  ("Fix round 2 for S8-spec").
- The two non-baseline failures were environment issues, fixed outside the repository (smoke precondition; the
  git-ignored `.env.lane`).
- Pushed by the owner's autopush (`origin/fllm` = `bdbfa16`; the LaunchAgent at 02:58, and my manual run confirmed
  it).
