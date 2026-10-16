# M5 G2 fix, round 1: D-A B1 (classified attributes) and B2 (unclassified keys)

Worktree `/Users/neelu/document_intelligence/dps-wt-m5`, branch `m5`. Start HEAD `e231483`; fix commit **`7c97bde`**
`fix(m5): synthesize never copies classified attributes or real keys — ...`. Author NilenduGanguli
<nilendu.ganguli@gmail.com>, no trailer. Explicit pathspec, 17 paths. Working tree clean afterwards.
Review: `M5-G2-review-r1.md`.

## 1. Test-first evidence (seen failing before the fix)

| Test | Failure before the fix |
|---|---|
| `test_targets::test_synthesize_pseudonymises_every_written_key_and_its_foreign_keys` | `pseudonym_columns` returned `[]` for an unclassified key |
| `test_targets::test_synthesize_never_shifts_or_copies_any_written_key` | offset append of an unclassified key accepted (DID NOT RAISE) |
| `test_targets::test_synthesize_never_keeps_a_classified_attribute` | `KeyError: 'country_code'` (no plan for a kept CITY) |
| `test_tabular_prepare::test_classified_attributes_kept_by_policy_are_modelled_not_copied` | `tabular_columns()` had no `personal` |
| `test_synthesize_identifiers::test_classified_attributes_are_never_copied_row_aligned[copula]` | row-aligned equality city 400 (bound 22), postal_code 400 (9), gender 400 (253), age 400 (22). On another seed the job failed `leakage` because every row was real. |
| `...[none]` (surrogates only) | city, postal_code, gender, age 400/400 |
| `test_synthesize_identifiers::test_unclassified_keys_and_their_foreign_keys_are_pseudonymised` | real survivors: `loans_synth.loan_id` 300, `repayments_synth.loan_id` 300, `repayment_id` 900 |
| After the B1 code change, `[none]` | failed `leakage: 206 original value(s)`. These were strict-sweep `fixed_point` hits for persona-drawn GENDER that equalled the original by chance. That led to the verification change below. |

## 2. What changed

**B1: classified attributes are never copied** (`targets.py`, `tabular.py`, `jobs_run.py`, `batch.py`)
- `targets.kept_attribute(c)` covers a column that:
  - is classified and not free text;
  - has synthesize method `keep`;
  - is not an identifier (not a key, not in `LINK_ENTITIES`, not MEDIUM+).
  These are the LOW entities that `keep_non_sensitive` keeps.
- In synthesize mode, `build_column_plans` plans every such column as `surrogate`. This applies to every write mode, in place included.
- Single-table tabular runs model the column instead where the engine can.
  - `tabular_columns(..., personal=True)` is used by `prepare_tabular` and `runtime_sampler`.
  - `_prepare_tabular.make` drops the surrogate plan of each kept attribute the fitted sampler draws. The stored plan, which partitions rebuild from, is the plan without those columns.
  - A column the engine declines keeps its surrogate. Reasons for declining include high cardinality (e.g. postal_code), an unsupported kind, and exclusion at fit time.
- Linked generation is unchanged: it still leaves classified columns out entirely.
- This is review option (a) where the engine can model the column, and (b) otherwise.
- **Verification:**
  - `batch.CHANCE_EQUAL_ENTITIES = {GENDER, NATIONALITY}`: an equal whole value of these two entities is not a `fixed_point`.
  - The basis is M0 `surrogates._IDENTITY_OK` and the schema sweep's `SWEEP_EXEMPT`: the value is drawn from the persona and may equal the original by chance.
  - Every other entity still fails on an unchanged value. A unit test asserts that CITY still hits.

**B2: unclassified keys and their foreign keys** (`targets.py`, `base.py`, `postgres.py`, `batch.py`)
- `_identifiers` now also returns two kinds of column, each labelled:
  - every unclassified key that a `table`/`files`/`append` run writes (append + `generate` excluded), labelled `KEY|owner.table.column`;
  - every unclassified declared foreign key, labelled by the key it references.
- New optional field `PseudonymColumn.label` keys the pseudonym. `entity_type` is `KEY` for these columns.
- New optional field `ColumnInfo.references` holds the referenced `owner.table.column`. PostgreSQL `describe` fills it from `pg_constraint` (single-column FKs only).
- `pseudonym_columns(..., shape=)` takes the live shape at prepare.
- In place, unclassified keys and FKs are not pseudonymised. In place the key is the row being rewritten, and the parent rows are real.
- `validate_run` now refuses **any** offset/prefix append in synthesize mode with `pk_strategy_invalid`, because `real key + offset` links back.

**Guard that fails closed.** `jobs_run.copied_personal_columns` lists written classified columns or keys that are not planned, pseudonymised or drawn. If any exist, prepare fails with `leakage` before the preflight. The list is empty by construction. A unit test shows the guard names the columns of a wrong plan.

**Contract** §4.1 (`ColumnInfo.references`) and §5.3 (the key rule, labels, B1 rule, guard, offset refusal) are updated in the same commit. The contract names that already existed are unchanged; the new fields are optional.

**Deviation log.** In `M5-DEVIATIONS.md`, the false sentence "the copula models them when they qualify" is withdrawn, and a new section "G2 review r1 fix" is added. The programme plan §3 needs no owner override, because the code now complies with D-A as written.

**Tests changed.** Three offset-append tests now run in **obfuscate** mode with `IN_PLACE_REVIEW`. They test offset mechanics; the default obfuscate review overflows the same table's varchar columns.
- `test_resume_after_kill[append_offset_named]`
- `test_run_failures::test_same_table_append_refuses_an_offset_inside_the_key_span`
- `test_run_modes::test_append_offset_to_the_same_table`. This test first asserts that a synthesize offset append of a key reviewed as not personal is refused with 422 before a job exists.

New tests:

| File | New tests |
|---|---|
| `test_targets.py` | 3 |
| `test_tabular_prepare.py` | 1 |
| `test_batch.py` | 1 |
| `test_kept_review.py` | 1 |
| `test_synthesize_identifiers.py` | 3 integration |

## 3. Source-vs-target evidence (counts only, this lane's containers)

**B1 integration test**
- Setup: a 400-row addresses table (the demo shape plus gender, age and a date), with the copula and with surrogates only.
- Every target row is aligned with its own source row through the key pseudonym, and **400/400 rows align**.
- For each LOW-classified written column, the number of row-aligned equal values is at most a chance bound: n·p_max + 5σ + 3. A copied column would reach 400.
- Rows equal on the whole quasi-identifier set (city, postal_code, gender, age): at most 2.
- 0 real keys in the target.
- The evidence shows `surrogate` for each such column, or lists it in `tabular.columns`. In the copula run, gender and age are drawn. `verification.passed` is true.

**B2 integration test**
- Setup: loans (`loan_id` unclassified, `customer_id` CUSTOMER_ID) and repayments, which declare an FK to loans. One seed, so one job group.
- **0 real survivors** for each of `loans_synth.loan_id`, `loans_synth.customer_id`, `repayments_synth.loan_id` and `repayments_synth.repayment_id`.
- The values are still keys: 300 and 900 distinct.
- The FK join holds for **900/900** rows.
- Both labels are `KEY|<schema>.loans.loan_id`.
- The evidence shows `key_pseudonym` for all three key/FK columns.

**Reviewer probes re-run.** I ran copies with their output redirected to my scratchpad; the reviewer's files are untouched.

| Probe | Before | After |
|---|---|---|
| `test_probe_da_kept` | 400/400 on city, postal_code, gender, age | succeeded. city and postal_code are `surrogate`; gender and age are copula-drawn. Equal values: gender 197/400, age 11/400. QI rows equal: 0. |
| `test_probe_da_loans` | `loan_id` real 400/400 | `loan_id` is `key_pseudonym`. 0 target rows align by real `loan_id`, i.e. 0 survivors. `originated_on` is copula-drawn. |

The probe's `line1`, `state_code` and `country_code` are equal 400/400 for a different reason. These columns are constant in the probe's data (the probe passed the unknown entity type "ADDRESS"), so the copula draws the single value: that is chance = 1, not copying.

**Log scan.** I ran `test_synthesize_identifiers.py` with DEBUG live logs (158 lines). The log contains 0 of the source loan ids, repayment ids, address ids, postal codes and customer ids.

## 4. Gates (final, before the commit)

| Gate | Result |
|---|---|
| `./lane.sh ruff format dps tests tools` | 272 files left unchanged |
| `./lane.sh ruff check dps tests tools` | All checks passed |
| `./lane.sh mypy dps` | Success: no issues found in 111 source files |
| `./lane.sh pytest -q -rs` with `dps-db`, `source-pg` and `minio` up | **1743 passed, 12 skipped** in 186 s |
| Frontend | Not run: no frontend file changed |

The 12 skips:
- console (3): no `m5` app image;
- OCR mock / tesseract (5);
- `mostlyai` (2): the `synth-ml` extra cannot be installed in this lane (torch);
- presidio / spaCy (2): the `ner` extra.

None of the skips touches the changed code, except that the MOSTLY tests also run through `tabular_columns`. `personal=True` is passed only on the single-table paths, and linked generation is unchanged. I ran `docker compose --profile sources down` afterwards, which touched only this lane's project.

## 5. Open issues (non-blocking; recorded in M5-DEVIATIONS)

1. **Surrogates stay close to the original.** A surrogate DATE is the original shifted by ±30 days, and a surrogate AGE stays in its 5-year band (M0 generators). They are not equal, but they are close. The copula path draws these columns independently whenever it models them. The privacy review should decide whether surrogate-only synthesize needs coarser generators for LOW dates and ages.
2. **Foreign-key label mismatches.**
   - An unclassified FK that references a *classified* key, or the reverse, gets a different label from the key. It is pseudonymised, so nothing leaks, but it no longer joins.
   - A reference without a declared FK constraint is unknown to DPS. It is copied unless discovery classified it, and the kept review then covers it.
   - Multi-column FKs are not named.
3. **Refusal for types no pseudonym fits.** A written key or declared FK whose type no pseudonym fits (smallint, date, float) now refuses the run with `synthesize_pseudonym_unsupported`. An FK can be left out of `selection.columns`, or the run can use append + `generate`.
4. **Snapshot vs. what the run does.** Discovery and the console still show `keep` for LOW attributes in synthesize mode. The run overrides that, and the evidence shows what actually ran. The console may want to display it (T21).
5. **Earlier non-blocking review notes are untouched**, apart from the B1/B2 overlap. Untouched: seed-holder reversibility wording, pseudonym collisions with other real ids, int4 overflow at submit, the `internal_error` for a missing adapter, the ungated `unique_overlap`, partial reproducibility, `.env.example`, and the lane `.env` port 8420 → 8425.
