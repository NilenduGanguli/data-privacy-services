# M5 G2 review, round 2: Tasks 6, 6B, 7, 7B, 8, plus SEC-4 (D-B) and D-A, after fix `7c97bde`

Reviewer: independent. Worktree `/Users/neelu/document_intelligence/dps-wt-m5`, branch `m5`, HEAD `7c97bde`.
Date: 2026-10-13. The working tree was clean before and after the review, and I changed no repository file.
Probes are in `/Users/neelu/.cache/dps-build/reports/probe-m5-G2/r2/`. Earlier reports read for context only:
`M5-G2-review-r1.md` and `M5-G2-fix-r1.md`. I did not rely on their claims; every claim below was re-checked.

**Verdict: PASS.**
- Every gate is green.
- r1's B1 and B2 are fixed. Independent source-vs-target probes on shapes the fix's own tests do not use confirm
  both.
- No leak into logs, evidence or job rows.
- No broken interface.

Some residual risks fall inside D-A's explicit carve-out ("non-modelled columns are kept when classified
non-personal and the detector finds nothing"). §4 lists them for an owner decision. They are not builder
defects.

## 1. Gates (run by me)

| Gate | Result |
|---|---|
| `./lane.sh ruff format --check dps tests tools` | 272 files already formatted. I used `--check` so that no file would be modified. |
| `./lane.sh ruff check dps tests tools` | All checks passed |
| `./lane.sh mypy dps` | Success: no issues found in 111 source files |
| `./lane.sh pytest -q -rs` with the lane's `dps-db`, `source-pg` and `minio` up | **1743 passed, 12 skipped** in 182 s. Log: `r2/pytest-full.log` |
| Frontend | Not applicable: `m5` changes no frontend file |

The 12 skips:
- console (3): no `m5` app image;
- OCR mock or tesseract (5);
- `mostlyai` (2): the `synth-ml` extra cannot be installed here because it needs torch;
- presidio and spaCy (2): the `ner` extra.

Only the two `mostlyai` skips touch G2 (`test_mostly.py:194`, `test_mostly_linked.py:246`). Every G2 integration
suite ran, including the 3 new D-A tests in `tests/connectors/test_synthesize_identifiers.py`.

Afterwards I ran `docker compose --profile sources down`, which touched only this lane's project. No container
is left.

## 2. Re-check of r1 blockers

### B1: classified LOW attributes copied row-aligned. FIXED.

**Code.**
- `targets.kept_attribute` covers a column that is classified, not free text, has synthesize method `keep`, and is
  not a key, a `LINK_ENTITIES` entity or MEDIUM+.
- `build_column_plans` plans such a column as `surrogate` in synthesize mode.
- `tabular_columns(..., personal=True)` lets the copula model it in single-table runs.
- `_prepare_tabular.make` drops the surrogate plan of each drawn column before the plan is stored. The plan is
  stored by `add_partitions` after `make`, so partitions rebuild the same plan.
- The fail-closed guard `copied_personal_columns` runs before the preflight.

**Probe P1** (`r2/test_probe_r2.py::test_p1_attributes`, output in `p1_copula.json` and `p1_none.json`).
- Shape: a 400-row `people` table with a text key `person_ref` (unclassified), `customer_id` (CUSTOMER_ID),
  city (8 values), postal_code (unique), gender, age, `moved_in` (DATE), amount (unclassified) and nationality.
- Every target row was aligned with its own source row by recomputing the `person_ref` pseudonym with the job's
  seed: 400/400 aligned.

Row-aligned equal counts:

| Column | copula | none | Chance level (source top frequency /400) |
|---|---|---|---|
| city | 63 (drawn) | 0 (surrogate) | 50 |
| postal_code | 0 (surrogate: too many distinct values to model) | 0 | 1 |
| gender | 191 (drawn) | 207 (surrogate) | 200 |
| age | 5 (drawn) | 0 | 6 |
| moved_in | 9 (drawn) | 0 | 12 |
| nationality | 114 (drawn) | 0 | 100 |
| QI (city, postal, gender, age) all equal | 0 | 0 | — |
| `person_ref` / `customer_id` real survivors | 0 / 0 | 0 / 0 | — |

Other results:
- The evidence lists each column as `surrogate` or under `tabular.columns`.
- `verification.passed` is true.
- `exact_row_matches` is 0 for the copula run.

The r1 failure (400/400 on every LOW column) no longer reproduces.

### B2: unclassified key copied next to a pseudonymised identifier. FIXED.

**Code.**
- `_identifiers` now also returns:
  - every unclassified key that a `table`/`files`/`append` run writes (append + `generate` excluded), labelled
    `KEY|owner.table.column`;
  - every unclassified declared FK, labelled by the key it references.
- The PostgreSQL `describe` fills `ColumnInfo.references` from `pg_constraint` (single-column FKs only).
- Any offset or prefix append in synthesize mode is refused with 422 `pk_strategy_invalid`.

**Probes.**
- **P2** (`p2_copula.json`, `p2_none.json`), text key `loan_ref varchar(12)`:
  - `loans_t`: 0 real survivors.
  - `repay_decl` with a declared text FK: 0 real `loan_ref`, 0 real `repayment_id`. The FK joins the synthetic
    parent **900/900**.
  - Evidence: `key_pseudonym` on all of them.
- **P3** (`p3.json`), composite PK `(book_id bigint, trade_ref varchar)`: 0 real survivors in either column, and
  400 distinct composite keys.
- **P3b** (`p3b.json`), `uuid` PK: 0 real survivors and 300 distinct keys, under both copula and none.
- The first uuid shape in P3 failed `leakage` ("every redraw … equalled a real row"). Its non-key columns had only
  150 distinct combinations, so the exact-row gate failed closed, as designed.

## 3. Other checks (no defect found)

**Values in logs, evidence and job rows.**
- Logs: across all probe runs (182 lines), I scanned for 2841 distinct source values of 6+ characters (keys,
  refs, customer ids, postal codes, dates, amounts). There were **0** hits.
- Evidence and job rows: `r2/test_probe_r2_ev.py` dumped the full evidence pack and job row of a copula run and of
  a none run, and scanned them for 1644 source values. The only hit was `1000`. That is a 4-character amount that
  equals the configured `partition_rows`, so it is a coincidence, not a leak. There are 0 hits at 6+ characters.

**The new guard is wired and tested.** `copied_personal_columns` names `customer_id`, `dob`, `email` and
`full_name` for a wrong plan (`test_kept_review.py`). It is empty for a correct plan.

**Verification relaxation (`CHANCE_EQUAL_ENTITIES = {GENDER, NATIONALITY}`).**
- The exemption is narrow: only an equal *whole value* of these two entities is not treated as a fixed point.
- A CITY that equals its source still hits (unit test).
- It matches M0 `_IDENTITY_OK`.
- The probe confirms the gender surrogate is independent of the source: 207/400, against a chance level of 200.
- Accepted. See nit N1.

**Contract.**
- `ColumnInfo.references` and `PseudonymColumn.label` are optional fields with defaults. No existing name changed,
  so the other lanes are not broken.
- Evidence now uses `entity_type: "KEY"` for an unclassified pseudonymised key. That value is not in the taxonomy.
  The console `EntityChip`/`tone()` should tolerate unknown types (see N4).

**SEC-4 / D-B.** Unchanged since r1 (`553ce70`, `cb9aafc`). The counting-server suites
(`tests/test_external_binding.py`, `tests/test_reveal_rows_external.py`) pass in the full run. The r1 PASS stands.

**Linked generation (7B).** Unchanged. `tabular_columns` without `personal` still leaves classified columns out,
as the unit test asserts.

## 4. Residual risks inside D-A's carve-out (owner decision, not blocking)

These follow from the D-A body as written in programme plan §3: "Non-modelled columns are kept only when
discovery classified them non-personal **and** the post-write sample finds no detector hit". They conflict with
the D-A headline ("never carry linkable real identifiers"), so the owner should decide on them before release.

### R1. An undeclared foreign key is copied verbatim and links synthetic rows to real parents

- **Probe P2, `repay_undecl`.** The table has the same data as `repay_decl`, but no `REFERENCES` constraint on
  `loan_ref`. Under both copula and none:
  - `loan_ref` has **900/900 real survivors**;
  - it joins the **real** `loans_t` 900/900;
  - it joins the synthetic parent 0/900;
  - the evidence says `passed: true` with no review.
- **Impact, copula run:** the content is drawn, but each synthetic row is attached to a real loan, which leads to a
  real customer. The per-loan row counts are real.
- **Impact, none run:** the whole row is real data attached to a real customer.
- **Other engines:** the contract fills `references` for PostgreSQL only ("other engines: None"). After
  integration, an Oracle or Mongo source copies even **declared** FKs verbatim, which is exactly the r1 B2 FK case.
- **Recommendation:**
  - (a) WP-I/M3 must fill `references` in the Oracle `describe` (`ALL_CONSTRAINTS` type `R`) before D-A is
    claimed for Oracle.
  - (b) Add a snapshot override that lets the owner declare a reference.
  - (c) The kept review should flag a near-unique kept column (distinct ratio of about 0.9 or more) as
    `review_required`.

### R2. The default synthesize mode (surrogate-only) makes the pseudonym map recoverable

- `options.tabular` defaults to `"none"`. In that mode every unclassified column is written with its real value,
  row-aligned:
  - P1: `amount` 400/400;
  - P3b: `spend` 300/300.
- Anyone who holds the real table can therefore match rows on such a near-unique column. That recovers the full
  map from `person_ref`/`customer_id` pseudonym to the real value, which is the r1 B2 consequence again, reached
  through a column that D-A explicitly permits.
- The evidence for a none run has no `privacy` section (`exact_row_matches: null`). The §6 matrix row "exact-row
  matches 0" is therefore only measured for tabular runs.
- **Recommendation:** pick one of the following.
  - (a) Default synthesize runs into `table`/`files` targets to `copula`.
  - (b) Flag near-unique kept columns as `review_required`, as in R1(c).
  - (c) State in the evidence that a surrogate-only synthesize output is linkable through its kept columns.

### R3. Surrogate-only runs keep dates and ages close to the original

The fixer disclosed this. Probe P1 (none) counts, row-aligned:
- `moved_in` is within ±30 days in **400/400** rows (0 are equal);
- `age` is within ±2 in **307/400** rows.

The values are never equal, so D-A's "not copied" holds. They are still close quasi-identifiers. The copula path
draws both independently (12/400 and 32/400). The privacy review should decide whether coarser M0 generators are
needed for synthesize.

## 5. Non-blocking defects and nits

1. **N1 (`batch.verify_row`).** The `CHANCE_EQUAL_ENTITIES` exemption applies to every method and in both modes. A
   GENDER column planned `redact`/`mask` that came out unchanged by a bug would no longer be caught. Scope the
   exemption to `method == "surrogate"`.
2. **N2. In-place synthesize of a table whose classified column has a declared FK can never succeed.**
   - Probe `r2/test_probe_r2_tb.py`: `loans_ir.customer_id` (CUSTOMER_ID, declared `REFERENCES cust_r`) is
     pseudonymised in place, and every partition hits an FK violation.
   - After **11.7 min** the job was still `waiting` (partition retries).
   - **0 rows changed**, so it fails closed and nothing is half-applied.
   - The same shape without the FK succeeds (300 rows rewritten).
   - `ColumnInfo.references` is now available at prepare, so this should be a coded refusal at prepare or submit.
   - This behaviour dates from `e231483` and was not introduced by r1.
3. **N3 (carried over from r1, still open).**
   - The evidence says `key_pseudonym` `"reversible": False`, but the synthesis seed inverts the map. The pack
     should say "reversible with the job's synthesis seed".
   - The ungated `qi_uniqueness.unique_overlap`.
   - The int4 overflow surfaces mid-run.
   - A missing adapter or KEK fails as `internal_error`.
   - `.env.example` lacks the M5 variables.
   - The lane `.env` has no `DPS_TOKENIZER_MOCK_PORT` (8420 would collide with m4; m5 should use 8425).
4. **N4.** The console tables (m5ui) should render `entity_type: "KEY"` (unclassified pseudonymised keys) without
   a taxonomy lookup error. Check at the m5ui merge.
5. **N5.** An unclassified FK that points to a classified key, or the reverse, gets a different label, so the join
   breaks silently. This is documented. It could be detected at prepare from `references` plus the parent snapshot
   and reported.

## 6. Deviation log judgement (r1 fix section of `M5-DEVIATIONS.md`)

- **Accepted:**
  - B1 approach: draw where the engine models the column, else surrogate;
  - withdrawal of the false "copula models them" sentence;
  - `CHANCE_EQUAL_ENTITIES` (with N1);
  - B2 labels and the declared-FK handling;
  - the offset/prefix refusal for any key;
  - the fail-closed guard;
  - moving the 3 offset-mechanics tests to obfuscate mode (synthesize offset is now refused, and a test asserts
    the 422 before a job exists);
  - limitations (1)–(4), recorded honestly.
- **Escalated to the owner:**
  - limitation (2), the undeclared FK. It becomes a real-key leak on Oracle/Mongo even for declared FKs until
    their `describe` fills `references` (R1);
  - R2, which is not in the log.
- The r1 judgements of the earlier entries (Tasks 6–8, SEC-4, L2 reveal) stand.

## 7. Probe index (`probe-m5-G2/r2/`)

| Probe | Output |
|---|---|
| `test_probe_r2.py` P1 / P2 / P3 / P3b | `p1_*.json`, `p2_*.json`, `p3.json`, `p3b.json` |
| `test_probe_r2_tb.py`, in-place FK | `p4_tb*.json` |
| `test_probe_r2_ev.py`, evidence and job scan | `ev_scan.json` |

Logs: `probe_run*.log`, `pytest-full.log`, `static.log`. `p4b.json` is a probe artefact: I set `partition_rows=500`
after validation, which bypassed the `ge=1000` rule, so finalize's `ValidationError` there is mine. Ignore it.

The probes were run with
`./lane.sh pytest -c pyproject.toml --rootdir=. -p tests.conftest <probe>` from the worktree.
