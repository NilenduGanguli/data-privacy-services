# M3 G5 fix, round 1: Tasks 18 and 18b (console), review `M3-G5-review-r1.md`

**Worktree:** `/Users/neelu/document_intelligence/dps-wt-m3` (branch `m3`) · **Date:** 2026-10-14
**Commit:** `4c1fd3f fix(m3): evidence pack page states a MongoDB job's filter template, bound literals and documents`. Author: NilenduGanguli <nilendu.ganguli@gmail.com>. No trailer.
**Resume state:** HEAD was `436ae69`, the commit the review covered. The tree was clean except for the untracked `docs/superpowers/build/m3-shots/`, which was left untouched (Task 21 commits it). No earlier fix attempt existed.

## B1: the evidence pack page ignored `selection.mongo_filter_template`

### Test written first, red before the fix

- **Vitest** `frontend/src/lib/jobs.test.ts`, case "states the selection a pack read: SQL template and binds, or the MongoDB filter template and its literals":
  - It failed before the fix because `recordNoun` and `packSelection` did not exist.
  - SQL cases:
    - a where template with 1 bind, max_rows and 2 columns;
    - a query template with 2 binds;
    - no selection, shown as "all rows · 0 bound parameters".
  - MongoDB cases:
    - `{segment:'?'}` becomes `{"segment":"?"}` · 1 bound literal, and never "all rows";
    - a nested `$or`/`$in`/`$gte` template becomes compact JSON · 3 bound literals · at most 50 documents · 1 field;
    - a null or `{}` template becomes "all documents · 0 bound literals".
  - `recordNoun` returns "document" or "documents" for `mongo`, and "row" or "rows" for the other engines.
- **Browser assertion** in `tests/console/test_console_m3_wizard.py::test_mongo_wizard_filters_discovers_and_runs`, made after `_run_to_evidence` and checked against the evidence page's selection line (`.kv .s2` containing "Selection"):
  - the line contains `{"segment":"?"}` and "1 bound literal";
  - it does not contain "all rows" or "all documents";
  - it does not contain the literal `premium`;
  - the summary matches `DPS read [\d,]+ documents from MongoDB`.
  - Run against the lane image built from the pre-fix HEAD, it failed with `AssertionError: Selection all rows · 0 bound parameters`, which is the review's exact symptom.

### Fix

- **`frontend/src/lib/jobs.ts`.** Two new helpers:
  - `recordNoun(engine, n)`.
  - `packSelection(sel, engine) -> {scope, detail}`. For MongoDB, `scope` is `JSON.stringify(mongo_filter_template)`, which is compact and keeps the `?` placeholders; with no filter or an empty filter it is "all documents". `detail` is "· N bound literal(s)", where each `?` leaf counts as one literal, followed by "· at most N documents" and "· N fields". The SQL engines keep the previous wording: template or "all rows", `binds` bound parameter(s), rows, columns.
- **`frontend/src/pages/EvidencePack.tsx`.** The selection line renders `Selection <mono>{scope}</mono>{detail}`. The summary sentence uses `recordNoun` ("DPS read N documents from MongoDB …, and wrote N documents to …").

### Deviations (logged in `docs/superpowers/build/M3-DEVIATIONS.md`, section "M3 G5 fix round 1")

- **Bound-literal count is computed on the page.** For MongoDB, the page counts the `?` leaves of the template. The server's `selection.binds` is `len(params)`, which is 0 for a Mongo filter whose literals are inline. Changing the server would change the sealed pack content and its hash, so the server, the evidence schema and the contract are unchanged. No field was renamed or added.
- **"documents" is used in two places only:** the summary sentence and the selection line. The stat tiles ("Rows written") and the "Row counts" check row keep the mockup's "rows" wording.
- **No new CSS class.** A `.sel` class would have collided with existing `.sel` rules in `wizard.css` and `jobs.css`, so the browser test locates the line by `.kv .s2` and its text instead.

## Gates (after the fix, before the commit)

| Gate | Result |
|---|---|
| `./lane.sh ruff format dps tests tools` | 301 files left unchanged |
| `./lane.sh ruff check dps tests tools` | All checks passed |
| `./lane.sh mypy dps` | Success: no issues found in 109 source files |
| `cd frontend && npm run build` | tsc + vite build clean |
| `cd frontend && npm test` | node tests: pass 42, fail 0. Vitest: **91 passed** (4 files; was 90, plus the new case) |
| `tests/console/test_console_m3_wizard.py` (stack `--profile oracle --profile sources`, image rebuilt with `up --build`) | **3 passed** |
| `./lane.sh pytest -q` (full suite, same stack), third run | **2056 passed, 8 skipped**, 0 failed (428 s) |
| Stack | `docker compose --profile oracle --profile sources down` afterwards. No other project was touched. |

The stack came up with an unseeded MongoDB after the Docker restart. Before the first browser run I seeded Oracle and MongoDB exactly as `test_console_m3_groups.py` does: `seed_oracle`/`seed_mongo`, 2,400 rows, seed 7, counts only.

## Open issue (pre-existing, not caused by this change): `test_three_way_consistency_join` is statistically flaky

- **What happened:** two of my three full-suite runs failed at `tests/e2e/test_m3_three_way.py:344` with `('mongo', '$.customerId', 0, 1)`.
  - Same-document survivors were 0. One masked `customerId` equalled a *different* customer's source id.
  - The test passed on its own, as part of `tests/e2e` (6 passed), and in the third full run.
- **Cause:** a chance collision of the FF1 permutation, not a leak.
  - The ids are `CUST-` followed by 8 digits with no leading zero. `fpe_digits` with leading-zero preservation therefore maps them into a domain of 9×10^7.
  - Masking 2,400 ids gives an expected 2400²/9e7 ≈ 0.064 collisions with the source set.
  - A simulation with the real cipher (`dps.engine.transform.fpe.Ff1`, random 32-byte keys, the 2,400 seeded ids of seed 7) produced **at least one collision in 20 of 300 runs (6.7 %)**.
- **Scope:** my change touches only the console page, its helpers and a console browser test, none of which are on that path.
- **Suggested follow-up (not done here, outside B1's scope):** the "anywhere" assertion for the key column `$.customerId` should be replaced. Options:
  - assert only same-document survivors, as the test already does for non-text fields;
  - bound "anywhere" by the birthday expectation;
  - mark `$.customerId` as not textual in `SENSITIVE["mongo"]`.

  The same applies to the Oracle `CUSTOMER_ID`, which is already marked not textual.

## Privacy

- No customer value, key or secret was printed or logged.
- The diagnostics printed counts and digit-length histograms only.
- The new UI text is value-free: the template's literals are `?` placeholders, and the browser test asserts that the filter literal is absent.
