# M5 lane verification fix, round 1

- Worktree `/Users/neelu/document_intelligence/dps-wt-m5`, branch `m5`. Started at HEAD `5694280`. The tree was clean apart from the verifier's untracked `docs/screenshots/m5/`, which I left alone and did not commit. No earlier fix attempt existed.
- **Commit `ad886e8`**: `fix(m5): never model a pseudonymised foreign key and draw integer pseudonyms from bands no real key reaches`.
  - Author: NilenduGanguli <nilendu.ganguli@gmail.com>.
  - No trailers.
  - Explicit pathspec: 12 files.
- Scratch files are in the session scratchpad (`.../scratchpad/`): `l2_synth.py`/`.json`/`.log`, `scan.py`, `pytest-full.log`, `pytest-int.log`, `compose-up.log`. `all-logs.txt` was written with umask 077.

## Finding 1: copula `bank.customers` died in prepare (fixed)

**Root cause.** This confirms the verifier's analysis.
- `tabular_columns` modelled `relationship_manager_id`, an unclassified int that is a declared foreign key.
- `pseudonym_columns` also pseudonymises that column (D-A B2).
- `BatchTransformer` refused the overlap with a `ValueError`, and `prepare` retried it as `internal_error` until the job was dead.

**Changes.**
- `dps/layers/connectors/tabular.py`:
  - `tabular_columns(..., pseudonyms=())` never models a column that the run pseudonymises.
  - `prepare_tabular` passes the run's pseudonym columns.
  - Pseudonymised columns are not listed in `tabular.excluded`, because that list is for columns that keep their source values.
- `dps/engine/batch.py`: new `PlanConflict(ValueError)`. It is raised for the plan contradictions that `BatchTransformer` refuses:
  - a missing column named by a plan or a pseudonym;
  - a sampler that writes a key or a planned column;
  - tabular sampling without a key;
  - a pseudonym that is also planned or sampled.
- `dps/layers/connectors/jobs_run.py`: `prepare` maps `PlanConflict` to a single value-free failed finalize with the new code `plan_conflict`. It is no longer retried as `internal_error`. This was the optional part of the finding.

## Finding 2: a real key in synthetic targets (fixed for integers)

**Root cause.** `KeyPseudonyms.integer` cycled values below 10⁹ through all of `[0, 10⁹)`. That range contains the source's own keys.
- Probe with the old code on 300,000 random 9-digit keys: **85 and 92 real keys hit**, under two seeds.

**Change (`dps/engine/synth/keys.py`).** Integer pseudonyms now lie in bands that no accepted source value reaches:
- `|v| < 10⁹` maps to `[10⁹, 2·10⁹)`. That is 10 digits, and an `integer` column holds it (2·10⁹−1 < 2³¹).
- 10 to 18 digits maps to `[10¹⁸, 2·10¹⁸)`, which a `bigint` holds.
- More than 18 digits is refused, as before.
- The sign is kept.
- Each tier is a plain keyed Feistel permutation onto its band. The result is:
  - injective;
  - deterministic per seed and label;
  - independent of the column type, so keys and foreign keys still agree within a job group;
  - free of fixed points by construction.
- The labels changed to `<entity>|int-band|<tier>|<sign>`.

**Supporting changes.**
- `PseudonymColumn.limit` holds the largest magnitude the column can take. The batch check uses `max_magnitude()`, which falls back to `10**digits − 1`.
- `targets._pseudonym_of` refuses an integer identifier whose column cannot hold 2·10⁹−1, with `synthesize_pseudonym_unsupported`. That covers `smallint` and `numeric(p ≤ 9, 0)`. `integer`, `bigint` and `numeric(≥10)` are accepted.

**Alternative rejected.** I considered the cycle-walk alternative (walking past the group's source key set) and rejected it:
- It is not injective: two source keys on one run of the cycle land on the same value.
- A job group has no shared state, so every table cannot know the same key set.

## Tests (written first; new tests fail on the old code)

- `tests/test_key_pseudonyms.py`:
  - band and int4 assertions;
  - `test_integer_pseudonyms_never_land_on_a_real_key_at_realistic_n`. It runs four key sets under two seeds, with 0 overlap and injectivity asserted: 300k random 9-digit ids, the sequence 1..150k, 50k random 12-digit ids, and 20k random 10–18-digit ids. The old map fails it (85/92 hits);
  - `test_an_int4_key_holds_its_banded_pseudonym`.
- `tests/connectors/test_targets.py`: `integer` and `numeric(10,0)` are accepted; `numeric(9,0)` is refused (422).
- `tests/test_tabular_prepare.py`:
  - a pseudonymised FK is never modelled;
  - `prepare_tabular` succeeds on a table with a declared FK, and its preview holds no real key. On the old code this raises `PlanConflict` (checked by reverting the exclusion).
- `tests/connectors/test_synthesize_copula.py` (integration, `pg_source`):
  - `test_copula_job_on_a_table_with_a_declared_foreign_key`: 9-digit keys plus an FK to `employees`.
  - `test_copula_job_on_the_seeded_demo_customers`: the verifier's reproduction on the seeded `bank.customers`, 20,000 rows. It skips if the bank is unseeded, and fails instead under `DPS_TEST_REQUIRE_SERVICES=1`. With the exclusion reverted through a pytest plugin, it fails with `plan_conflict`.

  Both tests assert:
  - the job succeeds;
  - `exact_row_matches` is 0 and the SQL `INTERSECT` of non-key rows is 0;
  - 0 real customer or employee keys appear in `customer_id` or `relationship_manager_id`;
  - FK pseudonyms equal the pseudonyms of the referenced key, so joins hold;
  - the FK method is `key_pseudonym`;
  - the FK is not among the tabular columns.
  - `test_a_plan_conflict_fails_prepare_without_retrying`: the job is `failed`/`plan_conflict` with `attempts == 1`.

## Gates (all green, before the commit)

| Gate | Result |
|---|---|
| `./lane.sh ruff format dps tests tools` | 301 files unchanged |
| `./lane.sh ruff check dps tests tools` | All checks passed |
| `./lane.sh mypy dps` | Success, 126 source files |
| `./lane.sh pytest -q -rs`, `sources` stack up | **1912 passed, 9 skipped**, 266 s |
| `DPS_TEST_REQUIRE_SERVICES=1 pytest -m "db or pg_source or s3"` | **269 passed**, 0 skipped. Was 266; +3 new |
| Frontend | Not run: no frontend files changed |

The 9 skips in the full run:
- tesseract binary ×2;
- `mostlyai` ×2 (the `ml` extra);
- presidio / `ner` ×2;
- `azure-ocr-mock` ×3. The `docs` profile was not up during that run, and the change touches no docs/OCR path.

## Rebuilt lane stack and live L2 check (HTTP API on `:8415`)

**Stack.** I rebuilt and restarted with:

```
DPS_TOKENIZER_MOCK_PORT=8426 docker compose --env-file .env --env-file docker/compose.sources.env --profile full up --build -d
```

`/readyz` returned `ready: true`. I then reran the verifier's `l2_synth.py`, extended with FK checks, for `bank-pg-ro` → `bank-uat`, as a `table` target. Counts only:

| Case | State | Rows | exact_row_matches (pack / SQL non-key) | Real-key checks | Pack |
|---|---|---|---|---|---|
| copula `bank.customers` (**was dead**) | **succeeded** | 20,000 | 0 / 0 | `customer_id` in source: **0**; `relationship_manager_id` that are real `employee_id`: **0** | fidelity 0.958, NNDR pass, MIA run, verification passed |
| surrogate `bank.customers` | succeeded | 20,000 | – / 0 | `customer_id`: **0**; FK real employee ids: **0** | verification passed |
| copula `bank.accounts` | succeeded | 30,000 | 0 / 0 | `account_id`: **0** (was 1); `customer_id` real: **0** | fidelity 0.980, NNDR pass, MIA run |

Other columns, as target values found anywhere in the same source column:
- `email`, `notes`, `account_number`, `iban`, `tax_id`: 0.
- `full_name`: 847 and 879. This is the known pool overlap.
- `balance`: 20 chance amounts.

**Log and evidence scan.**
- Scope: about 192K distinct demo values of 8 characters or more, including the real `customer_id`, `account_id` and `employee_id`.
- Scanned: all lane container logs (49 KB), plus the 3 jobs and their evidence packs, read through the API.
- Result: **0 hits**. The positive control was found.

**Stack shutdown.** The stack was stopped with `docker compose ... down`, without `-v`.

## Deviations / open issues (non-blocking, for the owner or the verifier)

1. **Integer residual.** A column that holds keys below 10⁹ **and** keys in `[10⁹, 2·10⁹)` can still see a small-tier pseudonym equal a real key in the second range. The `integer` type forces the small band there. This is documented in the code, the contract, HANDOFF §8 and the deviations log.
2. **Text pseudonyms are unchanged.** They are shape-keeping cycles, so a collision with another real key of the same shape has probability of about n²/|shape|:
   - negligible for UUIDs and long mixed keys (`iban`, and `account_number` showed 0);
   - not negligible for short digit-only text keys at very large n.

   This needs an owner decision.
3. **New refusal: `numeric(9,0)` identifiers.** `numeric(9,0)` / Oracle `NUMBER(9)` identifiers are now refused at submit; they were accepted before.
4. **New write failure: long keys in narrow numeric columns.** A `numeric(10..18, 0)` key with values ≥ 10⁹ now fails closed at the write (`incompatible_method`), because the 19-digit band does not fit. Before, such a key kept its digit count.
5. **Earlier pseudonyms are not reproduced.** The labels changed, so the same seed no longer reproduces pseudonyms from earlier runs. Rerun every table of a job group under this build.
6. **Linked generation does not pseudonymise other declared FKs.** `prepare_linked` (MOSTLY) still models a parent's or child's other declared FKs (`tabular_columns` without pseudonyms) and does not pseudonymise them. This is unchanged, and it is not exercised here because the `ml` extra is absent.
7. **Surrogate `national_id` chance match (unrelated to this fix).** In the surrogate run, 1 target `national_id` exists elsewhere in the source. It is a `NINO`: about 1,956 source values in a QQ+6-digit+letter domain of about 4M, so about 1 expected by chance. Surrogates are not keys, and this fix does not change them. A related demo-seed oddity: AADHAAR and SSN each have only 1 distinct `national_id` in the seeded source.
8. **Verifier's port and env notes still apply.** I again needed `DPS_TOKENIZER_MOCK_PORT=8426`, which is not in the lane env.
