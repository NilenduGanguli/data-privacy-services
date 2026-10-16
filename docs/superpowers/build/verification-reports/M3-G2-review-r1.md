# M3 G2 review, round 1 (Tasks 5–8: OracleConnector, OracleWriter, pitfalls, pipeline wiring)

Reviewer: independent agent · Date: 2026-10-13 · Worktree `/Users/neelu/document_intelligence/dps-wt-m3`,
branch `m3`, HEAD `f795666` (T8), with T5 `6366422`, T6 `2b8eb25`, T7 `e6272d4`. Tree clean before and after
the review (no repository file modified). Probes: `/Users/neelu/.cache/dps-build/reports/probe-m3-G2/`.
The implementer's report (`M3-G2-impl.md`) was not relied on; everything below was read in the code or run.

**Verdict: FAIL — 2 blocking findings (B1, B2).** All gates are green; the three pitfalls, read-never-locks
and ledger fencing under `kill -9` are proven on the lane's Oracle Free. Both blockers are pipeline-seam
defects introduced or exposed by Task 8 that make an Oracle job end differently from PostgreSQL in a way
that silently loses data or never finishes.

---

## 1. Gates (run by the reviewer, lane wrapper, lane containers)

| Gate | Result |
|---|---|
| `./lane.sh ruff format --check dps tests tools` | 268 files already formatted |
| `./lane.sh ruff check dps tests tools` | All checks passed |
| `./lane.sh mypy dps` | Success: no issues found in 106 source files |
| `./lane.sh pytest -q` | **1560 passed, 235 skipped** (skips = service-marked tests without `DPS_TEST_REQUIRE_SERVICES`) |
| Oracle + T8 tests, `DPS_TEST_REQUIRE_SERVICES=1 -m "oracle or db"` over `test_oracle_*.py`, `test_registry_m3.py`, `test_docpaths.py`, `test_pipeline_seams_m3.py`, `test_discovery_m3.py`, `test_targets.py`, `test_codec_m3.py`, `test_methods.py` | **63 passed** (incl. `test_oracle_pipeline.py` 3/3, `test_oracle_pitfalls.py` 4/4, `test_oracle_writer.py` 19/19) |
| Whole non-Mongo integration suite (M2 regressions for the touched M2 files `jobs_run.py`, `discovery.py`, `submit.py`, `targets.py`), `-m "(db or pg_source or s3 or oracle) and not slow and not browser and not mongo"` | **219 passed** |
| Slow integration (`-m "slow and not browser and not mongo"`), incl. M2 `test_resume_after_kill` ×6 modes and the HTTP e2e | **7 passed** |
| Frontend | not touched by T5–T8 (not run) |

Stack: `docker compose up -d dps-db oracle`, later `--profile sources up -d source-pg minio minio-init`;
`docker compose --profile sources --profile oracle down` at the end. No other project touched. No extras skipped
(Mongo tests are G3, out of scope).

## 2. Blocking findings

### B1 — An Oracle `query` selection silently writes NULL into every column the query does not project (PostgreSQL fails closed)

- **Where:** `dps/layers/connectors/oracle.py` `OracleConnector._projection` (T5) + `jobs_run._prepare`
  projection / `targets.resolve_target` `write_columns` (M2, wired by T8); writer `_write_sync`
  `r.get(c.name)` → `None`.
- **What:** M2's run handler takes `projection` = every shape column (narrowed only by `selection.columns`) and
  makes it the target's `write_columns`. `OracleConnector.read_partition` reads only the query's own select
  list (`named_selects`), so the rows lack the other columns and `OracleWriter` binds `None` for them.
  PostgreSQL selects `q."<col>"` for every projected column from the wrapped query and fails on a missing one.
- **Proof (probes `p3_report.json`, `p3b_report.json`, `p3_pg_report.json`):**
  - Oracle, `table` mode into a created `CUST_Q`, query `SELECT customer_id, email, segment FROM <owner>.customers`
    over 300 rows → job **succeeded**, target has all 10 columns, `FULL_NAME` NULL in **300/300** rows (also
    NOTES, DOB, …). Evidence says verification passed.
  - Oracle, second run with `table_load: upsert` into an existing populated target → succeeded, **300 rows
    with NULL `FULL_NAME`/`NOTES`** added; with a group key (same token for the key) the MERGE `WHEN MATCHED
    UPDATE SET` would overwrite existing target values with NULL.
  - PostgreSQL, same query shape, `table` mode → job **failed `source_error`** (fail closed).
- **Why blocking:** silent wrong output with a passing evidence pack; Oracle diverges from PG behaviour on the
  same spec (contract §4: one behaviour per the conformance suite; §5.2 query wrapped as
  `SELECT * FROM (<query>) q`); can destroy target data on upsert.
- **Fix (test-first):** make the read fail closed or make the projection agree. Preferred: in the M2 run handler
  (`_prepare`) and discovery, intersect the projection with the query's projected names for query selections
  (keys always present — `query_projects_keys` already enforces it), so `write_columns`, created DDL and the
  post-sample use exactly the columns the query yields, on both engines; alternatively have
  `OracleConnector._projection` return the caller's full projection and select `t."COL"` from the wrapped
  query so ORA-00904 surfaces like PG. Test: an Oracle (and PG) `table` run with a subset query → either the
  target holds only the projected columns with 0 NULL-filled columns, or the job fails before writing.

### B2 — A key-less Oracle `table` load (ledgered since T6/T8) cannot resume after a crash between commit and checkpoint: the partition replays its last batch, hits `LedgerConflict`, and stops forever

- **Where:** `dps/layers/connectors/jobs_run.py` `_run_partition` — the ledger is consulted only
  `if mode in ("in_place", "append")`. T8 made finalize aware of ledgered `table` loads
  (`plan.prepare["ledger"]`) but not the resume path. `OracleWriter._uses_ledger` is true for
  `table` + `key_source == "rowid"`.
- **Scenario / proof (probe `test_probe_keyless_table_resume_after_crash_before_checkpoint`, `p6_report.json`):**
  key-less `LEADS` (1,200 rows) → `table` load; the 2nd `jobs.checkpoint` call raises after its batch committed
  (rows + ledger mark N in one Oracle transaction). The retry starts from the partition checkpoint (N-1),
  re-reads batch N, `write_batch(mark=N)` → ledger holds N, guarded UPDATE from N-1 matches 0 rows →
  `LedgerConflict` → handler "stops quietly". Result after ~2 min of driving: job `waiting`, partition 0
  `running`, `batch_no 1`, attempts 2; target **1,000 of 1,200 rows**; it never progresses (each re-claim
  repeats the conflict until dead-letter). Fencing itself held (no duplicate rows).
- **Why blocking:** contradicts programme plan G2-review acceptance "ledger fencing survives kill" as a
  workflow property and contract §4.2 write atomicity/resume ("on resume, the worker takes the ledger mark as
  authoritative"); the contract also still says `table` loads "need no ledger", so the M2 handler was never
  told. A crash at a normal point turns a correct load into a stuck job.
- **Fix (test-first):** in `_run_partition` read the ledger whenever the writer ledgers (e.g.
  `mode in ("in_place","append") or rt.plan.prepare.get("ledger")`, or a `BatchWriter.uses_ledger`
  property), with the same "ledger behind checkpoint → fail" and "ledger ahead → record and continue" rules;
  amend contract §4.2 ("a `table` load of a key-less Oracle source is ledgered"). Add a resume test: Oracle
  key-less `table` load, crash after commit before checkpoint (or kill), job finishes with exactly 1,200 rows
  and 0 duplicates.

## 3. Programme-plan acceptance (§2.2 G2-finish, G2-review)

| Criterion | Result | Evidence |
|---|---|---|
| Oracle flows through registry, discovery and finalize seams | yes, except B1/B2 | `test_oracle_pipeline.py` 3/3; probes P1/P4/P7 |
| Wildcard doc paths | yes | `docpaths.py` + `test_docpaths.py`, `test_discovery_m3.py` (offline; Mongo live use is G3) |
| CLOB UTF-16 offsets proven on Oracle Free | yes | `test_oracle_clob_astral_roundtrip`: `DBMS_LOB.GETLENGTH` = UTF-16 units ≠ `len`, whole-value read, target `DBMS_LOB.INSTR(email)=0`, mixed 32,767/32,768-byte/NULL/short/140K MERGE batch exact |
| CHAR binds proven | yes | `test_oracle_char_key_paging`: VARCHAR2 bind matches 0, CHAR bind 1; rstripped checkpoint and partition cut; in-place 30/30 |
| TIMESTAMP(7–9) paging proven | yes | `test_oracle_timestamp9_key_paging`: 60-row burst inside one µs paged with batch 7, cut inside the burst, 66/66 in-place updates by FF9 key |
| Reads never `FOR UPDATE` | yes | probe P5 (live): during 7 pages of `read_partition` the tagged session had 0 `v$locked_object` rows and 0 `v$transaction`; `v$sql` of the schema's `dps:` statements contains no `FOR UPDATE`; validator refuses `FOR UPDATE` / `NOWAIT` / `SKIP LOCKED` / `OF … WAIT` queries |
| Ledger fencing survives kill | **fencing yes, resume no for key-less `table`** | probe P2 (`p2_report.json`): in-place batch 2 (29,000 rows) SIGKILLed mid-`executemany` (trigger sleep at row 15,000) → after PMON cleanup 0 batch-2 rows, ledger at 1; stale replay of batch 1 → `LedgerConflict`, nothing written; resume of batch 2 → 29,000 rows exactly once, ledger 2. B2 for the key-less `table` path |
| Session tags | yes | P5: `module=dps`, `action=partitions`, `client_identifier=<job id>` on the live session |

## 4. Privacy checks (source vs target, counts only; log scan)

- **P1 created table** (1,500 rows, `CUSTOMERS` → `CUSTOMERS_UAT`): classification `CUSTOMER_ID` fpe_digits,
  `FULL_NAME` fpe_text, `EMAIL` email, `DOB` date_shift, `NOTES` det_token, others keep. Target values equal to
  any source value: `CUSTOMER_ID` 0, `FULL_NAME` 0, `EMAIL` 0; target `NOTES` containing a source e-mail 0;
  kept columns equal (multisets) as expected. `DOB` any-value overlap is uninformative for a dense date range
  (row alignment impossible after FPE of the key); see P7.
- **P7 in place** (900 rows, key kept): row-aligned equal `FULL_NAME` 0, `EMAIL` 0, `DOB` 0, `NOTES` 0;
  `CUSTOMER_ID`, `TAX_ID`, `SEGMENT`, `INCOME`, `CREATED_AT`, `LAST_CONTACT_AT` 900/900 unchanged.
- **P4 append offset** (same table, `synthesize` and `obfuscate`): appended rows equal to an original row's
  `EMAIL` 0, `FULL_NAME` 0, `NOTES` 0; `DOB` row-aligned (key + offset) 0.
- **Log scan P1:** JSON + stdout/stderr of a full discover + run: 0 occurrences of any source `FULL_NAME`,
  `EMAIL` or `NOTES` value, no Oracle password, no key material. Driver errors carry codes only
  (`connector_error`, `raise … from None`; probe child tracebacks show `ConnectorError: connection_failed: DPY-4011`
  and `LedgerConflict` with no driver text). Metric labels are `(engine, op, status)`.

## 5. Deviation log judgements (M3-DEVIATIONS.md, Tasks 5–8)

| Entry | Judgement |
|---|---|
| T5 sample as `ORA_HASH(key)` filter instead of `SAMPLE … SEED` | Accept (measured instability; matches PG's hash filter) |
| T5 `_compile` binds literals via `parameterize_literals` | Accept (never interpolates; plan text would have been refused) |
| T5 `sample_keys` bounded with typed `hi` binds, 10·n sample; `sample()` retry without SAMPLE | Accept |
| T5 `test()` severities, `engine` not `Final`, `from_secret` refusals, list clamp | Accept |
| T5 query projection = the query's select list (plan's own code) | Accept as connector behaviour, but its interaction with M2's projection is **B1** |
| T6 write guard = caller's mark (`ledger_ensure` + guarded UPDATE, `LedgerConflict`) | Accept (contract §0 amendment; proven under concurrency and kill) |
| T6 `PrepareFailure` for prepare refusals; `PkCollision`; `length_overflow` as `PrepareFailure` | Accept (matches M2 PG semantics) |
| T6 columns from `ResolvedTarget`, binds from the live target | Accept |
| T6 created-table widening to worst-case `det_token` width | Accept |
| T6 same-table `append`+`generate` probe `NEXTVAL` | Accept; note it also consumes one sequence value under `dry_run` (N7) |
| T6 key-less `table` loads under the ledger | Accept in principle; contract §4.2 not amended and M2 resume path not adapted → **B2** |
| T7 `from_engine_text` keeps FF9 text (M2 touch) | Accept |
| T7 `date_shift` unchanged (nanosecond tail kept) | Accept (pinned in `tests/test_methods.py`) |
| T7 MERGE LOB binds converted for the whole column | Accept (live test with mixed sizes and NULL) |
| T8 registry branch (lazy import before credentials, close on failed open, mongo still unavailable) | Accept |
| T8 `test_registry_m3.py` without a secret tree | Accept (real `secret_ref` resolution exercised by `test_oracle_pipeline.py`) |
| T8 `tests/test_registry.py` oracle row removed | Accept |
| T8 `docpaths.top_level`, `row_locator`, `UROWID` | Accept |
| T8 discovery: row locators never classified; binaries name-classified then kept; per-document null rate | Accept |
| T8 `PIPELINE_ENGINES` gates in `submit.py` / `targets.py` | Accept (same code, engine-neutral text) |
| T8 `created_column_type(..., engine=)` + contract comment | Accept (defaulted kw, backward compatible); see N5 for cross-engine runs |
| T8 finalize clears a prepare-reported ledger | Accept, but incomplete without the resume side (**B2**) |
| T8 `post_sample_hits` skips `forced_keep` columns, reads by path | Accept (also changes M2 PG: `bytea` columns no longer scanned — N6) |

Open G1 items (programme §1.3 M3-N): N1 (sample percent floor) is handled by `_SAMPLE_PERCENT_MIN`; N4
(TSTZ key path) is now used (`_tz_text`); N6 (per-cursor handler) is how `_rows` works. N2 (BYTE semantics)
is harmless for kept columns; N5 (`.env.example`) could not be checked by this reviewer (file read denied by
local policy). None blocking.

## 6. Non-blocking findings (style / hardening / test gaps)

- **N1 (hardening, M2 validator shared):** the Oracle selection validator accepts `CRM.SEQ1.NEXTVAL`
  (a side effect in a "read-only" read), arbitrary schema functions (e.g. `CTXSYS.DRITHSX.SN`,
  `ORDSYS.*`, user PL/SQL functions that may be `AUTONOMOUS_TRANSACTION`), and `DBURITYPE`/`XDBURITYPE`.
  Reads are not wrapped in `SET TRANSACTION READ ONLY` (PG has `BEGIN READ ONLY`). Least privilege is the
  real bound; consider denying `nextval`/`currval`, `ctxsys`, `ordsys`, `mdsys`, `dburitype`, `xdburitype`
  and setting `SET TRANSACTION READ ONLY` on read sessions (WP-F1 security pass).
- **N2 (test gap):** `test_oracle_pipeline.py` compares only `EMAIL`, `FULL_NAME`, `NOTES`; add `DOB`
  (row-aligned in the in-place test) and the FPE'd key in the created-table test (probes P1/P7 show 0).
- **N3:** `TargetSpec.ledger_owner` pattern allows a leading `_`, Oracle `quote_ident` refuses it; the
  `SelectionError` escapes `OracleWriter.prepare` (not a `PrepareFailure`/`ConnectorError`) and would be retried
  as `internal_error`. Refuse at submit for Oracle targets or map it to `PrepareFailure("target_invalid")`.
- **N4:** `list_objects` offers mixed-case (quoted) table names that `describe` cannot address (identifiers are
  always upper-cased); skip names that are not upper-case in the listing.
- **N5:** cross-engine `table`/`append` runs (PG source → Oracle target or the reverse) are not refused at
  submit; they fail at prepare (`target_invalid` from `created_column_type`) or try to bind PG-typed values into an
  existing Oracle table. Add a submit-time gate or document the support matrix.
- **N6:** `post_sample_hits` now skips binary columns for PostgreSQL as well (`bytea` was scanned as text before).
  Logged; acceptable.
- **N7:** the same-table `append`+`generate` sequence probe consumes a sequence value even under `dry_run`
  (documented as a gap, never a row); the dry-run claim "changes nothing" should say so.
- **N8 (contract-level, all engines):** the ledger's `LAST_KEY` holds the **source** key of each partition's last
  batch in the target database; for `append` into another (e.g. UAT) database a failed job keeps up to one real key
  per partition there until a retry finalizes. Worth a line for the D-A owners.
- **Observation:** while a killed session's rollback was in progress, a 199,000-row resume `executemany` got
  `DPY-4011` and was correctly mapped to retryable `connection_failed`; 50,000-row batches (the `batch_rows`
  maximum) complete normally in ~1 s.

## 7. Probe index (`/Users/neelu/.cache/dps-build/reports/probe-m3-G2/`)

`gates.txt` (unit gates), `test_probe_g2.py` (P1 every column + log scan → `p1_report.json`; P3 query subset →
`p3_report.json`; P3b upsert → `p3b_report.json`; P4 append → `p4_report.json`; P6 key-less resume →
`p6_report.json`; P7 in place → `p7_report.json`), `test_probe_pg_query.py` (`p3_pg_report.json`),
`test_probe_kill.py` + `kill_writer.py` (P2 → `p2_report.json`, `p2b_report.json`), `test_probe_session.py`
(P5 → `p5_report.json`). Run with
`./lane.sh pytest -p no:cacheprovider -c pyproject.toml -p tests.conftest --rootdir=. <probe>` from the worktree.
