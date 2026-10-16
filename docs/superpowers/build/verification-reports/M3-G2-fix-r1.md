# M3 G2 fix, round 1 (review `M3-G2-review-r1.md`: B1, B2)

Worktree `/Users/neelu/document_intelligence/dps-wt-m3`, branch `m3`. Start: HEAD `f795666` (T8), tree clean,
no earlier fix attempt (nothing to resume). Commit: **`bd18b15`** `fix(m3): a query subset reads and writes only
its own columns; key-less Oracle table loads resume from the ledger` (author NilenduGanguli
<nilendu.ganguli@gmail.com>, no trailer, explicit pathspec). Tree clean afterwards.

## B1: a query subset NULL-filled Oracle targets

**Fix (the review's preferred option 1): one shared read projection on both engines.**

- `dps/layers/connectors/selection.py`: new `query_columns(sel, engine)`, which returns the folded output names of a query, or
  `None` when there is no query or it uses a bare or qualified `*`. New `read_projection(shape, sel, engine) -> list[ColumnInfo]`
  starts from every column, narrows it to the query's own select list and then to `columns` (Oracle folds these
  upper), always keeps the keys, and keeps table order. Names that are not columns (expressions, aliases) are dropped.
  `query_projects_keys` now uses `query_columns`, with the same result.
- Used by `OracleConnector._projection` (it no longer takes the compiled-query argument),
  `PostgresConnector._projection`, `jobs_run._prepare`, and the PostgreSQL `runs_as` preview in
  `dps/layers/api/routers/connections.py`. In `jobs_run._prepare`, the projection becomes `RunPlan.projection`,
  then `ResolvedTarget.write_columns`, the created DDL, the files columns and the post-sample. As a result, what a
  connector reads is exactly what is planned, created and written.
- Defence in depth: `OracleWriter._write_sync` now refuses a batch whose rows lack a bound column. It raises
  `PrepareFailure("column_missing", "a written column is not in the batch")`, which carries no values, instead of
  binding `None`.
- Contract §5.2 amended (2026-10-13). It defines the read projection and says an upsert into a wider existing
  target leaves the unread columns of matched rows untouched.

**Tests written first. Each failed before the fix:**

| Test | Before | After |
|---|---|---|
| `tests/connectors/test_selection.py::test_read_projection_is_the_query_select_list_on_both_engines` | ImportError | pass |
| `tests/connectors/test_run_modes.py::test_table_mode_query_subset_writes_only_the_projected_columns` (PG) | job `failed source_error` | pass: target columns exactly `customer_id, email, segment`, 300 rows, 0 NULL e-mails, **0 source e-mails survive** (SQL join count), segment multiset equal, evidence passed |
| `tests/connectors/test_oracle_pipeline.py::test_a_query_subset_creates_and_writes_only_the_projected_columns` | target had 10 columns (FULL_NAME etc. NULL) | pass: exactly `CUSTOMER_ID, EMAIL, SEGMENT`, 300 rows, 0 NULLs in any of them, **0 surviving `CUSTOMER_ID` and `EMAIL`** source values, evidence passed |
| `…::test_a_query_subset_upsert_never_nulls_the_target_columns_it_does_not_read` (key kept, full load, then subset-query upsert) | 0/300 rows had FULL_NAME and DOB unchanged | pass: 300 rows (all matched), 300/300 FULL_NAME and DOB unchanged row by row, NOTES unchanged on every non-NULL row, 0 NULL FULL_NAME, **0 surviving source e-mails** |

## B2: key-less Oracle `table` load stuck after a lost checkpoint

**Fix.** `jobs_run._ledgered(plan)` is true for `in_place` and `append`, or when `plan.prepare["ledger"]` is set (what
prepare reported). Both resume (`_run_partition`) and finalize (ledger clearing) now use it. As a result:
- a ledger that is behind the checkpoint fails with `ledger_conflict`;
- a ledger that is ahead is recorded and the run continues after it.

Both rules now hold for key-less Oracle `table` loads. PostgreSQL `table` mode reports no ledger, so its behaviour is
unchanged. The module docstring was updated, and contract §4.2 was amended to say that a key-less Oracle `table` load
is ledgered.

**Test written first:** `test_oracle_pipeline.py::test_a_keyless_table_load_resumes_from_its_ledger_after_a_lost_checkpoint`.
The second `jobs.checkpoint` call raises after its batch committed with its ledger mark.
- Before the fix: "job did not reach a terminal state" (the review's stall).
- After the fix it succeeds:
  - `rows_written` 1200, and 1200 rows in the target;
  - **0 repeated rows** (GROUP BY over all columns, HAVING > 1);
  - exactly one resumed partition, and evidence shows `resumed_partitions` 1;
  - **0 surviving source e-mails**;
  - ledger rows cleared, and evidence verification passed.

I did not repeat a separate `kill -9` probe. The reviewer's P2 already showed that fencing holds under a kill. What
this test exercises is the resume path that failed, at the same commit-then-crash point.

## Gates (lane wrapper; lane containers dps-db, oracle, source-pg, minio and minio-init; brought down afterwards)

| Gate | Result |
|---|---|
| `./lane.sh ruff format --check dps tests tools` | 268 files already formatted |
| `./lane.sh ruff check dps tests tools` | All checks passed |
| `./lane.sh mypy dps` | Success: no issues found in 106 source files |
| `./lane.sh pytest -q` (services up, so the service tests ran) | **1789 passed, 11 skipped** |
| `DPS_TEST_REQUIRE_SERVICES=1 pytest -m "(db or pg_source or s3 or oracle) and not browser and not mongo"` | **230 passed** |
| `DPS_TEST_REQUIRE_SERVICES=1 pytest -m "slow and not browser and not mongo"` (incl. M2 `test_resume_after_kill` ×6, HTTP e2e) | **7 passed** |
| Frontend | not touched (not run) |

The 11 skips:
- 3 console tests (app container not started);
- 3 OCR-mock tests (docs profile);
- 2 Tesseract tests (no binary on PATH);
- 1 Mongo stack test (Mongo is G3);
- 2 `[ner]`/presidio tests (extra not installed in this lane, unchanged from before).

None of them cover code that was touched.

Log scan: I ran the 4 new integration tests at DEBUG with JSON logs and stdout captured (140 lines). It found 0
e-mail-shaped strings and 0 `lead.<n>` source values. No new log statements were added, and the one new error
message carries no values.

## Deviations (logged in `docs/superpowers/build/M3-DEVIATIONS.md`, "Review G2 round 1 fixes")

- **M2 behaviour change (PostgreSQL):**
  - **What changed:** a run whose query names only some of the columns no longer fails with `source_error`. It now reads, creates
    and writes exactly the columns the query projects, the same as Oracle.
  - **Upserts:** an upsert MERGEs only those columns. Rows it inserts into a wider existing target get the target's
    defaults or NULL for the other columns, the same as `columns` narrowing already did.
- **M2 behaviour change:** `jobs_run` now folds Oracle `columns` to upper case. Before, a lower-case list narrowed the
  connector's read but not the plan.
- **Not changed:**
  - **Discovery:** it samples through the connector, which now gives the same projection on both engines. Columns
    outside the query are classified by name only, as Oracle already did.
  - **`targets._projection` (submit-time method checks over `columns` ∪ keys):** this is a superset, so it is the
    stricter side, and at submit time only the selection template is available.

## Open issues

- The review's non-blocking N1–N8 are not addressed. They were out of scope for this round.
- I did not re-run the reviewer's probe files, to keep their before-state evidence. The new repository tests cover
  their scenarios (P3, P3b, P6).
