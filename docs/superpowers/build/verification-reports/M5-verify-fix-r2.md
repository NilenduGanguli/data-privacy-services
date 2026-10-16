# M5 lane verification fix, round 2

- Worktree `/Users/neelu/document_intelligence/dps-wt-m5`, branch `m5`. Started at HEAD `ad886e8`.
  - The tree was clean apart from the verifier's untracked `docs/screenshots/m5/`. I left that directory alone and did not commit it.
  - No earlier r2 fix attempt existed.
- **Commit `0d43420`**: `fix(m5): draw government-id surrogates from never-issued ranges and check every written identifier against the source`.
  - Author: NilenduGanguli <nilendu.ganguli@gmail.com>.
  - No trailers.
  - Explicit pathspec: 17 files.
- Scratch files are in `/private/tmp/claude-501/-Users-neelu-document-intelligence/d1f0660d-411d-4447-9d4e-0331e8df3a77/scratchpad/`:
  - `pytest-full2.log` and `pytest-int.log`;
  - `l2_synth.py` (the verifier's script, extended to record `identifier_check` and `review_columns`);
  - `l2_run{1,2,3}.json`/`.log`;
  - `scan.py` and `all-logs.txt` (written with umask 077);
  - `oldplug/` (the plugin that reverts the fix, used to show the new tests fail).

## The finding: real national IDs in synthetic targets (fixed)

**Root cause, confirmed.** `NATIONAL_ID`, `NINO`, `CURP`, `RFC`, `NHS_NUMBER`, `EIN`, `PASSPORT` and the other government IDs had no generator. They fell back to `Reshaper`, a keyed derangement of the *issued* space that keeps the 2-letter NINO prefix. A derangement maps real IDs onto other real IDs.

I found two more members of the same class while fixing it:
- **SSN surrogates** used 9xx with any group, so one could equal a real ITIN or ATIN.
- **ITIN surrogates** used group 93, which is the ATIN group (issued to people).
- **PAN_IN surrogates** were random valid PANs, which is the issued space.

### 1. Never-issued generators (`dps/engine/synth/unissued.py`, new)

`SurrogateEngine` now routes NINO, CURP, RFC, NHS_NUMBER, EIN and NATIONAL_ID to the generators below, and SSN, ITIN and PAN_IN get the new ranges:

| Type | Never-issued range used | Check digits |
|---|---|---|
| NINO | Prefix with a letter HMRC never uses (DFIQUV first / DFIOQUV second), **excluding QQ** | – |
| CURP | First 4 letters are a RENAPO "inconvenient word" (stdnum list) | Valid |
| Personal RFC | First 4 letters are a SAT "inconvenient word" | – |
| NHS | `999` test range | mod-11 valid |
| EIN | Unassigned campus prefixes | – |
| PAN | Sequence `0000` (stdnum also rejects it) | – |
| SSN | 9xx with groups 01–49, which no ITIN or ATIN uses | – |
| ITIN | Group 89 only | – |
| Generic `NATIONAL_ID` | By recognised sub-shape (see below) | – |

Generic `NATIONAL_ID` sub-shapes:
- 12 digits: leading `1`, Verhoeff-valid.
- 9 digits: `000…`, Luhn-valid. That is neither an SSN area nor a SIN.
- 10 digits: NHS `999`.
- NINO, CURP and RFC shapes: as in the table.

Types and shapes with no such range are still reshaped:
- PASSPORT, DRIVER_LICENSE, VOTER_ID, INE_CLAVE, BN, MRZ document numbers;
- company RFCs;
- other national-ID shapes.

For these, `never_issued()` is always `False`, so the post-write check sends the column to review (fail closed).

**Scope.** These generators apply wherever the `surrogate` method runs, masking included, not only synthesize mode. A real ID mapped onto another real ID is just as wrong there.

**Deviation: no QQ.** Mockup 05 says "QQ prefix". Surrogates never draw `QQ`, because test fixtures use it and the demo seed's NINOs are all `QQ`. A `QQ` surrogate would land on demo source values at about n²/4·10⁶. The predicate still counts `QQ` as never issued.

### 2. Post-write identifier check (`dps/layers/connectors/identifier_check.py`, new; `jobs_run._identifier_check`)

**When it runs.** After a successful synthesize to a database target.

**What it reads.** Every written value of each written government-ID column, plus the financial identifiers (`CARD_PAN`, `IBAN`, `ACCOUNT_NUMBER`, `CRYPTO_ADDRESS`). It reads them through the connector in keyset pages of 5,000, projecting only those columns. It then reads every source value of the same columns.

**What it counts.**
- `in_source`: distinct target values found in the source column. The comparison uses 64-bit BLAKE2b hashes of the leakage sweep's normalised form, under a fresh random key that is never stored. The hashes are sorted with numpy, 8 bytes per value.
- `issuable`: government IDs that are not in a never-issued range.

**Outcome.**
- Either count above 0 puts the column in `review_columns`, sets `review_required: true` and sets `passed: false`. This flows into the existing pack, PDF and console logic.
- An error flags every identifier column (fail closed).
- `in_place` checks the ranges only.
- A same-table append records `not_run: same_table_append`.
- A files target records `not_run: files_target`.

**Value-free.** The result holds column names, entity types and counts only. The contract (evidence `verification`) and HANDOFF §8 are updated.

**Deviation from the suggested SQL join.** I stream through the connector instead, which works across servers and credentials: the source role is read-only and need not see the target schema. The cost is two full scans per run.

### 3. Knock-on changes (needed for the gates)

These tools need *rule-valid* PANs, and a `0000` surrogate no longer validates, so each now draws its own:
- **`tools/seed_sources.py`**: draws customers' and applicants' PANs with its own per-customer generator. No other seeded value moves.
  - Without this change, a reseed would make demo `tax_id` stop being discovered as PAN. It would become a kept column, copied verbatim.
  - `test_seed_sources` now asserts that seeded PANs validate.
- **`dps.evaluation.corpus.rule_valid_pan`**: reproduces the engine's former PAN draw exactly. It is used by the eval corpus and `tools/make_demo_docs.py`.
  - I regenerated the committed `tests/eval/corpus/*.jsonl`: byte-identical.
  - The demo-documents tests (committed PDF/DOCX/TXT equal what the tool writes) pass.
- **Console**: the Policies matrix's synthesize hints for PAN_IN, NINO, CURP, NHS_NUMBER, NATIONAL_ID and PASSPORT now describe the new ranges and the review.

## Tests (written first)

- **`tests/test_unissued.py`** (10 tests):
  - 20,000 demo-style `QQ` NINOs give 0 surrogates among the originals, as NINO and as NATIONAL_ID. The old reshape kept `QQ`; the expected count was about 100.
  - Real-format NINOs map outside the issued space and fail validation.
  - CURP, the sub-shapes, SSN/ITIN groups, NHS/EIN/PAN/RFC.
  - Types with no range are never claimed as unissued.
  - The demo's mixed column gives 0 survivors and 0 issuable.
  - 8 of these 10 failed before the engine change.
- **`tests/test_identifier_check.py`** (4 tests): identifier family; column selection; tally counts (layout-insensitive, value-free); reading every row of both sides; in-place mode.
- **`tests/connectors/test_synthesize_copula.py`**:
  - `test_copula_job_on_the_seeded_demo_customers` (extended) and the new `test_surrogate_job_on_the_seeded_demo_customers`.
  - Both assert, by SQL on the normalised value, that 0 target `national_id`/`tax_id` values are in `bank.customers`.
  - Both assert that the run's own check agrees: `checked` equals the number of target rows with a value, `in_source` is 0, `issuable` is 0, and neither column is a review column.
  - **With the fix reverted for NATIONAL_ID** (`-p old_reshape_plugin`), both demo tests fail in each of two runs:
    - the SQL count found 2 and then 1 real IDs (copula);
    - the check found 18,044 issuable (surrogate).
- **`tests/connectors/test_synthesize_identifiers.py::test_a_real_identifier_in_the_target_needs_review`**: a deliberately leaky generator writes 2 real IDs. The check's `in_source` equals the source-vs-target count (2), and the pack is `review_required` with `passed: false`. The pack JSON holds neither value.
- **`tests/test_surrogates.py`**: the PAN assertion now expects `0000` and `validate(...) is False`. That assertion encoded the old, wrong behaviour.

## Gates (all green before the commit)

| Gate | Result |
|---|---|
| `./lane.sh ruff format dps tests tools` | 305 files unchanged |
| `./lane.sh ruff check dps tests tools` | All checks passed |
| `./lane.sh mypy dps` | Success, 128 source files |
| `./lane.sh pytest -q -rs`, full stack, fresh volumes | **1929 passed, 9 skipped** (255 s) |
| `DPS_TEST_REQUIRE_SERVICES=1 pytest -m "db or pg_source or s3"` | **268 passed**, plus 3 console errors (see below) |
| `pytest tests/console`, after the smoke run | 3 passed |
| `DPS_SMOKE_REQUIRE_OCR=1 tools/smoke_test.py` | all 34 checks passed |
| `cd frontend && npm run build && npm test` | build ok; node --test 42/42; Vitest 190/190 |

The 9 skips in the full run:
- 3 console tests: the demo connections did not exist yet on fresh volumes;
- tesseract binary ×2;
- `mostlyai` ×2 (`ml` extra);
- presidio / `ner` ×2.

The 3 console errors in the integration run had the same cause: the demo connections did not exist until the smoke test ran. They passed after it.

An earlier full run (before the corpus and demo-docs fix) had 11 failures, all from the PAN change. The run above is after that fix.

## Rebuilt lane stack and live L2 check

**Stack.**
- Command: `DPS_TOKENIZER_MOCK_PORT=8426 docker compose --env-file .env --env-file docker/compose.sources.env --profile full down -v`, then `up --build -d`. The reseed used the new seed code.
- `/readyz` returned `ready: true`, and `seed-sources` exited 0.

**L2 runs.** The verifier's `l2_synth.py` through the HTTP API (`bank-pg-ro` → `bank-uat`, `table` target), run 3 times. Counts only:

| Case (×3) | State / rows | `national_id` in source | `tax_id` | Exact rows | Real keys | Identifier check (checked / in_source / issuable) | Pack |
|---|---|---|---|---|---|---|---|
| copula customers | succeeded 20,000 | **0, 0, 0** (was 1, 1, 2) | 0 | 0 | 0 / 0 | national_id 20,000 / 0 / 0; tax_id 12,069 / 0 / 0 | passed, review [] |
| surrogate customers | succeeded 20,000 | **0, 0, 0** (was 0, 2, 3) | 0 | 0 | 0 / 0 | same | passed, review [] |
| copula accounts | succeeded 30,000 | – | – | 0 | 0 / 0 | account_number 30,000 / 0 / 0; iban 2,916 / 0 / 0 | passed, review [] |

**Log scan.**
- Scope: about 204.5K distinct demo values of 8 characters or more, including real `customer_id`, `account_id` and `employee_id`.
- Scanned: all lane container logs (111 KB), `dps_audit_log` (85 rows) and `dps_evidence` (14 rows).
- Result: **0 source values, 0 literals, 0 key-shaped tokens**. The positive control was detected.

**Shutdown.** The stack was stopped with `docker compose ... down`, without `-v`.

## Open issues / notes

1. **Email chance overlap (new observation, non-blocking).** In 2 of 6 customers runs, 1 synthetic `email` equalled a source email. Both were on RFC 2606 domains (`example.com/net/org`).
   - The fresh-reseeded demo source is itself made of the engine's RFC 2606 e-mails, so the pools overlap like the known name-pool overlap (N1).
   - A real source address can never equal one of these.
   - EMAIL is outside the identifier check by design (contact, not identifier). The owner should confirm, together with N1.
2. **Residual collision risk.**
   - NINO, CURP and the others draw from their never-issued space randomly, not injectively. Two originals can share a surrogate with probability about n²/2N (e.g. NINO: 41 prefixes × 4·10⁶ ≈ 1.6·10⁸), as the SSN generator always could.
   - A surrogate equals a *source* value only if that source value is itself in the same never-issued range (test data). The post-write check catches that.
   - Demo Aadhaar and SSN source values sit in the engine's own never-issued ranges, at 1 distinct value each (N5). Once T26 diversifies the seed, about n²/10¹⁰ Aadhaar overlaps per run become possible (≈0.015 at 12k). The check would then flag review.
3. **Files targets** are not read back (`not_run: files_target`). Only the generator guarantee holds there.
4. **Same-table synthesize append** records `not_run`. It is not flagged.
5. **Cost.** The check does two full keyset scans of the identifier columns per synthesize run, which is cheap for the demo. At bank scale the scans dominate finalize time, and memory is 8 bytes per distinct target value. A same-server SQL join could be added later as an optimisation.
6. **No never-issued range** exists for PASSPORT, DRIVER_LICENSE, VOTER_ID, INE_CLAVE, BN, MRZ, company RFC, or other national-ID shapes (e.g. 13-digit). A synthesize that writes them is **always** `review_required`. This is by design (fail closed), but owners will see review on such tables.
7. The verifier's earlier notes N2–N7 are untouched. `DPS_TOKENIZER_MOCK_PORT` is still missing from the lane env; I used 8426 as the verifier did.
