# M3 G2-finish — Task 8 implementation report

Worktree `/Users/neelu/document_intelligence/dps-wt-m3`, branch `m3`.
Commit: **`f795666`** `feat(connectors): wire Oracle into the registry; wildcard paths and job-id tagging seams`
(author NilenduGanguli <nilendu.ganguli@gmail.com>, no trailer). Working tree clean after the commit.

## Starting state (resume)

T5 `6366422`, T6 `2b8eb25` and T7 `e6272d4` were committed. A previous attempt had left uncommitted
`registry.py` and `discovery.py` changes, a new `docpaths.py`, and four test files. I reviewed them,
kept them and extended them. Their 15 tests passed as found. Running the full suite then showed one red
M2 test (`tests/test_registry.py`, `oracle → engine_unavailable`), which the new Oracle branch makes
obsolete.

## What was done

### As planned (Task 8 steps 3-5)
- `docpaths.py` provides `generalise`, `expand_wildcards`, `values_at`, `column_values`, `forced_keep`,
  `dotted` and `leaf_updates`, plus `top_level` and `row_locator`.
- `registry.py` adds the Oracle branch:
  - the driver is imported lazily, and without the extra the branch raises `engine_unavailable`;
  - credentials are flattened to `{username, password, dsn?}`;
  - the session's `client_identifier` is set to `job_id`;
  - the connector is opened before it is returned, and a failed open closes the pool.
  - `mongo` still raises `engine_unavailable` until Task 12.
- `discovery.py` reads values through `column_values` and keeps `forced_keep` columns without
  running value detectors.

### Found by a new in-process pipeline test against the lane's Oracle Free, fixed test-first (M2 touch points)
1. **Engine gates.** `submit_discover` and `validate_run` refused every source that was not
   PostgreSQL, so no Oracle job could ever reach the registry. A new
   `base.PIPELINE_ENGINES = {"postgres", "oracle"}` is now used by `submit.py` and `targets.py`.
   MongoDB is added to it with Task 12.
2. **`targets.created_column_type`.** It checked every type against PostgreSQL's lower-case
   `TYPE_RE`, so a table-mode run into a created Oracle table failed `target_invalid`
   (`NUMBER(12)` "cannot be copied"). The function now takes `engine=` and uses `ORACLE_TYPE_RE`
   for Oracle. This resolves open question 14, and the contract §4 comment on `column_types` is updated.
3. **Finalize left the ledger behind.** Finalize cleared the write ledger only for
   `in_place`/`append`. The Oracle writer also keeps a ledger for a `table` load of a key-less (ROWID)
   source, and 2 ledger rows were left after a successful run. `prepare` now stores
   `plan.prepare["ledger"]`, and finalize also clears when that is set. PostgreSQL behaviour is unchanged.
4. **Post-write sample.** The scan is extracted as `jobs_run.post_sample_hits`. It skips
   `forced_keep` columns (a packed ROWID can pass Luhn and was counted as a kept hit) and reads
   values through `docpaths.column_values` (needed for Mongo paths). Mutation check: with
   `forced_keep` stubbed to `False`, the unit test goes red (10 kept hits instead of 5).

### Refinements to the uncommitted draft
- **Row locators are never classified, not even by name.** A row locator is the ROWID pseudo key or a
  real `ROWID`/`UROWID` column. Without this, a stored ROWID column named `ACCOUNT_NUMBER` would get a
  token method.
- Binary columns and ObjectIds keep M2's name-only classification and are then kept (`binary_kept`).
- `UROWID(n)` is now covered (the plan missed it).

## Tests added / changed
- `tests/connectors/test_docpaths.py`: 5 tests, offline.
- `tests/connectors/test_discovery_m3.py`: 3 tests, offline:
  - ROWID with Luhn-valid values is kept while a real card column is classified;
  - a stored ROWID column is kept;
  - a BLOB is kept but keeps its name entity;
  - wildcard e-mail paths are classified by value.
- `tests/connectors/test_pipeline_seams_m3.py`: 6 tests:
  - AST checks that every `open_connector` call carries `job_id`, discovery calls `column_values` and
    `forced_keep`, and finalize calls `clear_ledger`;
  - the engine gates;
  - `post_sample_hits`;
  - the ledger clear.
- `tests/connectors/test_registry_m3.py`: 6 tests.
- `tests/connectors/test_targets.py`: 2 new tests (Oracle source passes the gate; engine-aware created types).
- `tests/test_registry.py`: the Oracle row is removed (now covered by `test_registry_m3.py`).
- **`tests/connectors/test_oracle_pipeline.py`** (new; markers `db`, `integration`, `oracle`). It runs
  through M2's real handlers (submit → discover → accept → run → finalize), using a `secret_ref` for
  the connection and an easy-connect DSN built from its params:
  1. **Keyed table → created `CUSTOMERS_UAT`, 1,500 rows.**
     - 0 source values survive in `EMAIL`, `FULL_NAME` or `NOTES` (SQL counts).
     - The kept `SEGMENT` column has the same multiset of values.
     - The ledger is empty, the key is purged and the evidence verification passed.
  2. **`in_place` on 900 rows, key kept.**
     - Row by row, 0 rows keep their source `EMAIL`, `FULL_NAME` or `NOTES` value.
     - `SEGMENT`, `INCOME` and `LAST_CONTACT_AT` (TIMESTAMP(9)) are unchanged on all 900 rows.
     - The ledger is cleared and the evidence has `write_ledger_cleared=true`.
  3. **Key-less table.**
     - Discovery reports the ROWID key as kept and unclassified.
     - The `table` load writes 1,200 rows with 0 surviving e-mails.
     - The created target has only the 3 real columns.
     - The ledger is cleared.

## Gate results (all on the final tree, through `./lane.sh`)
- `ruff format --check dps tests tools`: 268 files already formatted.
- `ruff check dps tests tools`: all checks passed.
- `mypy dps`: no issues in 106 source files.
- `pytest -q` was run with the lane's dps-db, Oracle, source-pg and MinIO up, under
  `-m "not mongo and not browser and not network and not ocr"`:
  - **1784 passed, 4 skipped, 7 deselected.**
  - Skips: tesseract binary not on PATH (2), the `presidio_analyzer` / `[ner]` extra with spaCy models (2).
  - This run includes every M2 PostgreSQL integration test (run modes, discover, failures,
    resume-after-kill, conformance) and the T3-T7 Oracle live tests.
- `DPS_TEST_REQUIRE_SERVICES=1 pytest tests/connectors/test_oracle_pipeline.py`: 3 passed.
- Before the containers were up, plain `pytest -q`: 1718 passed, 72 skipped.
- Frontend: not touched.
- Docker: `docker compose --profile oracle --profile sources down` was run afterwards (this lane's
  containers only).

## Deviations
All are recorded in `docs/superpowers/build/M3-DEVIATIONS.md` under "Task 8", and committed with the task.

## Open issues / notes for G2-review and later tasks
- A created Oracle target widens token columns to the worst-case `det_token` width, so `EMAIL
  VARCHAR2(254)` becomes CLOB (Task 6 rule). This works, but CLOB e-mails in UAT are unusual. The
  "length-safe method suggestion" fix on main (`b471266`) may interact with this at integration;
  worth a look in the G2 review.
- On the keyed Oracle run the post-write sample reports `kept_column_hits=400` per 200 rows: the kept
  TIMESTAMP columns get low-score `DATE` findings. This is M2's post-sample semantics, not Oracle's,
  and PostgreSQL behaves the same. Verification still passes.
- `RunPlan.prepare["ledger"]` is a new key in the stored plan dict. It is additive; older plans without
  it behave as before.
- MongoDB: `PIPELINE_ENGINES` must gain `"mongo"`, and the registry needs its branch, in Task 12. The
  seam test asserts that `"mongo"` is absent today and will need flipping then.
- The M3-N items from the G1 review (N1, N2, N4-N6) were not touched here (the G2 review decides).
- Session tagging: `client_identifier=job_id` is proven by a unit test and by the AST seam test (every
  handler's `open_connector` call passes `job_id`). It is not read back from `V$SESSION` during a
  pipeline run; T4's live test already reads the session tags from the server.
