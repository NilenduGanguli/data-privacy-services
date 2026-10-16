# M3 G2 review, round 2 (Tasks 5–8 after fix round 1)

Reviewer: independent agent · Date: 2026-10-13 · Worktree `/Users/neelu/document_intelligence/dps-wt-m3`,
branch `m3`, HEAD `bd18b15` (fix r1), on top of T5 `6366422`, T6 `2b8eb25`, T7 `e6272d4` and T8 `f795666`.
The tree was clean before and after the review, and I modified no repository file. Probes are in
`/Users/neelu/.cache/dps-build/reports/probe-m3-G2/r2/`. I did not rely on `M3-G2-impl.md` or `M3-G2-fix-r1.md`;
everything below was read in the code or run.

**Verdict: FAIL, 3 blocking findings (B3, B4, B5).**
- The round-1 blockers B1 and B2 are fixed and independently verified.
- All gates are green.
- The pitfalls, read-never-locks and kill-fencing still hold at HEAD.
- One new blocker is a regression introduced by the B1 fix (B4: a submit-time guard bypass).
- One is a T6 behaviour gap (B3: a key-less `upsert` doubles the target).
- One is a leak that the B1 fix's contract amendment now codifies on both engines (B5: query aliases). It already
  existed in M2.

---

## 1. Gates (run by the reviewer: lane wrapper, lane containers dps-db, oracle, source-pg, minio and minio-init)

| Gate | Result |
|---|---|
| `./lane.sh ruff format --check dps tests tools` | 268 files already formatted |
| `./lane.sh ruff check dps tests tools` | All checks passed |
| `./lane.sh mypy dps` | Success: no issues found in 106 source files |
| `./lane.sh pytest -q` (services up) | **1789 passed, 11 skipped** in 178 s |
| `DPS_TEST_REQUIRE_SERVICES=1 pytest -m "(db or pg_source or s3 or oracle) and not browser and not mongo"` | **230 passed** |
| `DPS_TEST_REQUIRE_SERVICES=1 pytest -m "slow and not browser and not mongo"` (incl. M2 `test_resume_after_kill` ×6, HTTP e2e) | **7 passed** |
| Oracle/T8 files explicitly: `test_oracle_{pitfalls,pipeline,writer,connector}.py`, `test_registry_m3.py`, `test_docpaths.py`, `test_pipeline_seams_m3.py`, `test_discovery_m3.py`, `test_selection.py` | **207 passed** |
| Frontend | not touched by T5–T8 or fix r1 (not run) |

The 11 skips:
- console tests (the app container is not started);
- OCR-mock and Tesseract tests;
- one Mongo stack test (Mongo is G3);
- `[ner]`/presidio tests (extra not installed in this lane).

None of them cover code that T5–T8 or the fix touched. Afterwards I ran
`docker compose --profile sources --profile oracle down`, and touched no other compose project.

## 2. Round-1 blockers: verification

### B1 (a query subset NULL-filled Oracle targets): fixed

**Code.** `selection.read_projection(shape, sel, engine)` is now the single projection:
- The read side uses it: `OracleConnector._projection` and `PostgresConnector._projection`.
- The plan side uses it: `jobs_run._prepare` → `RunPlan.projection` → `ResolvedTarget.write_columns`, created DDL,
  files columns and post-sample.
- `OracleWriter._write_sync` also refuses a batch that lacks a bound column (`PrepareFailure("column_missing")`,
  value-free) instead of binding `None`.

**Probe `r2_p3_subset.json`** (Oracle, 300 rows, `table` mode into a created target):

| Query | Target columns | Rows | NULLs | Source values surviving |
|---|---|---|---|---|
| `SELECT customer_id, email, segment …` | exactly `CUSTOMER_ID, EMAIL, SEGMENT` | 300 | 0 in each column | `CUSTOMER_ID` 0, `EMAIL` 0 |
| table-alias-qualified `SELECT c.customer_id, c.full_name, c.email … c WHERE c.segment IS NOT NULL` | exactly those 3 | 300 | 0 | `CUSTOMER_ID` 0, `FULL_NAME` 0, `EMAIL` 0 |

**Repository tests read and run:**
- PG subset test;
- Oracle subset test;
- Oracle subset upsert test: 300/300 `FULL_NAME`/`DOB` unchanged row by row, 0 surviving e-mails;
- unit `read_projection` test.

### B2 (a key-less Oracle `table` load stuck after a lost checkpoint): fixed

**Code.** `jobs_run._ledgered(plan)` is true for `in_place`/`append`, or when `plan.prepare["ledger"]` is set. It
drives both resume (`_run_partition`) and finalize. `plan.prepare` is persisted in `dps_job.plan`, and a retry with
partitions skips `prepare`, so a key-less `truncate_insert` is not re-truncated under an existing ledger.

**Probe `r2_p6_keyless_resume.json`** (stricter than r1 and than the repository test: two lost checkpoints, calls #2
and #5, in different batches):
- the job succeeded;
- `rows_written` 1200, target rows 1200, distinct `LEAD_NO` 1200;
- 0 duplicate groups;
- 0 surviving source e-mails;
- 0 ledger rows left;
- 2 resumed partitions.

**Kill-fencing re-proved at HEAD (`r2/p2_report.json`):**
- in-place batch 2 (29,000 rows) was SIGKILLed mid-`executemany`;
- 0 batch-2 rows remained after the kill, with the ledger at 1;
- a stale replay of batch 1 hit `LedgerConflict` and wrote nothing;
- the resume wrote 29,000 rows exactly once, and the ledger ended at 2.

(`test_max_batch` once hit ORA-00054 in its `drop_all` right after the kill test, while PMON still held the killed
session's locks. That is a probe artifact: it passed when re-run alone.)

## 3. Programme-plan acceptance (§2.2 G2-finish, G2-review)

| Criterion | Result | Evidence |
|---|---|---|
| Oracle flows through the registry, discovery and finalize seams | yes (B3–B5 aside) | `test_oracle_pipeline.py` 6/6; probes above |
| Wildcard doc paths | yes | `test_docpaths.py`, `test_discovery_m3.py` |
| CLOB UTF-16 offsets, CHAR binds, TIMESTAMP(7–9) paging proven on Oracle Free | yes | `test_oracle_pitfalls.py` green at HEAD (unchanged since r1, where each was read and judged) |
| Reads never `FOR UPDATE` | yes | `r2/p5_report.json`: during 7 `read_partition` pages, 0 locks and 0 transactions on the tagged session (`module=dps`, `action=partitions`); 0 `FOR UPDATE` among the `dps:` statements in `v$sql` |
| Ledger fencing survives kill | yes | `r2/p2_report.json` (above); key-less `table` resume now holds (`r2_p6`) |

## 4. Blocking findings

### B3: `table_load: upsert` on a key-less Oracle source silently appends, so every re-run doubles the target (T6)

- **Where:** `dps/layers/connectors/oracle.py`.
  - `OracleWriter.__init__` sets `_merge` only for `primary_key`/`unique_index` sources. A `rowid` source gets
    plain `INSERT`.
  - `_prepare_sync` neither refuses nor truncates for `table` + `upsert` with `key_source == "rowid"`.
  - Nothing at submit (`targets.validate_run`) refuses it either.
- **Proof (probe `r2_keyless_upsert.json`):** I loaded the key-less `LEADS` table (500 rows) twice with
  `{"write_mode": "table", "table_load": "upsert"}` into `LEADS_UP`.
  - Both jobs `succeeded`.
  - There were 500 rows after the first job and **1000 after the second**.
  - **500 `LEAD_NO` groups are duplicated.**
- **Why blocking:**
  - The spec's write-mode table defines `table` as "`truncate_insert` or `upsert` on the primary key". An upsert
    without a key cannot be idempotent.
  - PostgreSQL refuses key-less tables outright (`no_key`).
  - Oracle instead double-applies writes with a passing evidence pack.
- **Fix (test-first):** refuse `upsert` for a key-less source. In `OracleWriter._prepare_sync`, add
  `if mode == "table" and t.table_load == "upsert" and src.key_source == "rowid": raise PrepareFailure("no_key",
  "upsert needs a key: a key-less table loads with truncate_insert")`. Ideally also add a submit-time 422 once the
  snapshot shape is known (`snapshot.shape.key_source == "rowid"`).
- **Tests:**
  - key-less `table` + `upsert` → job `failed no_key`, with 0 rows written and the target untouched;
  - key-less `truncate_insert` run twice → still exactly N rows.

### B4: lower-case Oracle `columns` bypass the submit-time method guards (regression introduced by fix r1)

- **Where:**
  - `dps/layers/connectors/targets.py` `_projection(spec, snapshot)`: `set(columns) | set(snapshot.shape.key)` with
    **no case folding**. It feeds the `text_method_invalid` and `method_needs_key` checks in `validate_run`.
  - Fix r1 made `jobs_run._prepare` fold Oracle `columns` to upper case (`read_projection`), so the run now reads and
    writes a column that submit never checked.
  - `validate_selection` accepts the lower-case name because it folds when it checks for existence.
- **Proof (probe `r2_lowercase_columns.json`):** I set up Oracle `CUSTOMERS` with a snapshot override
  `NOTES → keep`. `NOTES` is a free-text column (`text_column` true).
  - `columns: ["NOTES"]` → refused at submit with **`text_method_invalid`** (correct).
  - `columns: ["notes"]` → **accepted**, and the job `succeeded`.
  - The target holds `CUSTOMER_ID, NOTES`, and **200/200 rows have `NOTES` identical to a source row's raw notes**
    (`DBMS_LOB.COMPARE = 0`).
  - Before fix r1, the run handler did not fold, so `NOTES` was neither planned nor written. The leak is new in
    `bd18b15`.
- **Why blocking:** a leak of raw free text into a target, with a validator effectively weakened by case.
- **Fix (test-first):**
  - Make the submit-time projection agree with the run's: fold `columns` per engine in `targets._projection`.
    Better, compute it with `read_projection(snapshot.shape, spec.source.selection, engine)`, so query subsets
    agree as well.
  - Alternatively, re-run the same method checks in `jobs_run._prepare` over the final projection (defence in
    depth: `build_column_plans` is the last point every column passes).
- **Test:** an Oracle run with `columns: ["notes"]` over a snapshot with free-text `NOTES` → keep must be 422
  `text_method_invalid`. Add the same for `method_needs_key` in synthesize mode.

### B5: a query alias can put a sensitive column under the name of a column the snapshot keeps. Raw values reach the target, and the evidence verification passes (both engines; codified by the r1 contract amendment)

- **Where:**
  - `selection.query_columns` / `read_projection` map a query's **output names** (`alias_or_name`) to the object's
    columns. The connectors then read `q."<name>"` from the wrapped query.
  - The method applied is the snapshot's method for that *name*. Nothing checks that the output named `CHANNEL` is
    the object's `CHANNEL` column: it could be another column, an expression or another table's column.
  - The snapshot is bound to `(connection, object)`, not to the run's query, and the run can use any query.
  - Contract §5.2 (amended in `bd18b15`) now defines the projection this way: "narrowed by a query's own select
    list … names that are not columns of the object are dropped".
- **Proof (counts only):**
  - Oracle (`r2_alias_fitting.json`): `CONTACTS(CONTACT_ID pk, EMAIL VARCHAR2(400), CHANNEL VARCHAR2(400))`, 400
    rows. The discovered snapshot has `EMAIL → email` and `CHANNEL → keep`. The run query
    `SELECT contact_id, email AS channel FROM contacts` (`table` mode) → `succeeded`. **400/400 target `CHANNEL`
    values equal a source e-mail**, and evidence `verification.passed = true`.
  - PostgreSQL (`r2_alias_pg.json`): the same table and query → `succeeded`, and **400/400 target `channel` values
    equal a source e-mail**.
  - (In `r2_alias.json`, the same swap on `CUSTOMERS` failed `length_overflow` only because `SEGMENT` is
    `VARCHAR2(16)`. That is chance, not a guard.)
- **Scope note:** this already existed in M2 for PostgreSQL. Before fix r1, PG selected `q."channel"` the same way,
  and T5's Oracle projection used `named_selects`. A `jobs`-scope caller can also accept a snapshot that keeps a
  column, so it is not a privilege escalation. It is still a silent leak with a passing evidence pack: the reviewed
  snapshot says the column is non-sensitive, and the masking claim of the pack is false. The round-1 amendment
  made it the contract.
- **Fix (test-first, shared `selection.py`, both engines):** in `validate_selection` (when `shape` is given), refuse
  any select-list item whose output name is a column of the object unless it is a plain column reference to that
  same column. Concretely, it must be an `exp.Column` with the same folded name, unaliased or aliased to itself,
  and qualified, if at all, by the object or its own table alias. A new code would be, for example,
  `selection_query_alias` (checklist entry `identifiers_validated`). Expressions under a column's name
  (`UPPER(email) AS email`) are refused too, or kept only when the expression references that column alone. If
  this is preferred instead, bind the accepted snapshot to the discovery's query and refuse a run whose query
  differs.
- **Amend contract §5.2** accordingly.
- **Tests:** on Oracle and PG, `SELECT key, email AS channel` → 422 at submit (and in discovery). A plain
  `SELECT key, channel` still runs.

## 5. Deviation-log judgements (round-1 fix entries; T5–T8 entries were judged in r1 and are unchanged)

| Entry | Judgement |
|---|---|
| B1: one shared read projection (`query_columns`, `read_projection`) used by both connectors, the run handler and `runs_as` | Accept as the mechanism. It is incomplete: the submit-side projection was not aligned (**B4**), and output names are trusted as column identities (**B5**) |
| M2 behaviour change: a PG subset query now runs instead of failing `source_error` | Accept (one behaviour on both engines, contract amended) |
| M2 behaviour change: `jobs_run` folds Oracle `columns` | Accept the fold, but **B4**: submit must fold identically |
| Defence in depth: `_write_sync` refuses a batch missing a bound column | Accept. It checks `rows[0]` only, which is fine because the transformer emits uniform rows |
| "Not changed: `targets._projection` … a superset, so stricter" | **Reject.** It is a superset only when the names match exactly. For Oracle lower-case `columns` it is disjoint from the run's projection (**B4**) |
| B2: `_ledgered(plan)` for resume and finalize; contract §4.2 amended | Accept (verified with two lost checkpoints) |
| "Did not repeat a kill -9 probe" | Accepted. The reviewer re-ran the r1 kill probe at HEAD: fencing and resume hold |

## 6. Privacy checks (counts only)

- **Source vs target:** see §2 and §4. These counts come from SQL joins in the probes; I printed or stored no value.
- **Log scan:** I ran `test_oracle_pipeline.py` (6 tests) and the PG subset test with `--log-cli-level=DEBUG -s`
  (161 lines, `r2/logscan.out`). The scan found:
  - 0 e-mail-shaped `@example.(com|org)` strings;
  - 0 `Person NNNNN` / `lead.N` source values;
  - 0 password or key strings.
- The new error text (`column_missing`) carries no values.
- I added no new log statements.

## 7. Non-blocking findings (carry forward; none addressed in fix r1)

- **r1 N1–N8 still open:**
  - N1: the Oracle validator accepts `SEQ.NEXTVAL`, `CTXSYS`/`ORDSYS` functions and `DBURITYPE`/`XDBURITYPE`, and
    reads are not `SET TRANSACTION READ ONLY`.
  - N2: pipeline tests do not compare `DOB` or the FPE'd key.
  - N3: a `ledger_owner` with a leading `_` raises a `SelectionError` from prepare.
  - N4: mixed-case table names are listed but cannot be described.
  - N5: cross-engine `table`/`append` is not refused at submit.
  - N6: PG `bytea` columns are no longer post-sampled.
  - N7: the same-table `append`+`generate` sequence probe consumes a value under `dry_run`.
  - N8: the ledger `LAST_KEY` holds a source key in the target database.
- **N9 (new):** a subset query or `columns` that leaves out the snapshot's **anchor column** silently falls back to
  the row-key anchor (`batch.row_anchor`). As a result, `date_shift` and persona methods for the same person differ
  between a full run and a subset run. Either refuse a subset without the anchor when an anchor-dependent method is
  projected, or report it in the evidence (`anchor: "row"`). This consistency gap existed with M2's `columns` and
  is now reachable via queries too.
- **N10 (new):** a query output that is not a column of the object (for example `UPPER(email) AS shout`) is silently
  dropped from the read. Consider reporting dropped outputs in the selection checklist (`runs_as` preview), so a
  user sees that their expression is not copied.
- **N11 (new):** an `append` (including same-table) with a subset query now inserts rows whose other columns take
  the target's defaults or NULL. That is documented in the amendment. The dry-run and console confirmation step
  should say so, because a "copy" with NULLs may surprise an owner.

## 8. Probe index (`/Users/neelu/.cache/dps-build/reports/probe-m3-G2/r2/`)

**Probe files:**
- `test_probe_r2.py`:
  - `r2_p3_subset.json` (subset and qualified queries);
  - `r2_alias.json` (swap on `CUSTOMERS`, `length_overflow`);
  - `r2_alias_fitting.json` (**B5** Oracle);
  - `r2_p6_keyless_resume.json` (B2 with two lost checkpoints);
  - `r2_keyless_upsert.json` (**B3**);
  - `r2_lowercase_columns.json` (**B4**).
- `test_probe_r2_pg.py` → `r2_alias_pg.json` (**B5** PG).
- `test_probe_session.py` → `p5_report.json` (no locks, no `FOR UPDATE`).
- `test_probe_kill.py` + `kill_writer.py` → `p2_report.json`, `p2b_report.json`.
- `logscan.out`.

Run them from the worktree with
`DPS_TEST_REQUIRE_SERVICES=1 ./lane.sh pytest -p no:cacheprovider -c pyproject.toml -p tests.conftest --rootdir=. <probe>`
(needs dps-db and oracle, plus source-pg for the PG probe).
