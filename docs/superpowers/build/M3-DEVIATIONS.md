# M3 build — deviations from the plan

Plan: `docs/superpowers/plans/2026-10-11-m3-oracle-and-mongodb.md`. One section per task: file, what
changed, why. Public names and signatures that later tasks rely on are preserved unless a section says
otherwise. Built in the lane worktree `dps-wt-m3` (branch `m3`), compose project and host ports from the
lane's `.env` (contract §0, amendment 2026-10-12).

## Task 1 — seam audit

Read in the tree as built (M0 + M1 + M2 integrated on `main`, `88eae2e`). One line per row of the
plan's "Seams with M2"; `same` = the name/signature the plan assumes exists as built.

- **connector types** (`dps/layers/connectors/base.py`): `ObjectRef(owner, name)` (+ `fqn`, `parse`;
  part pattern `^[A-Za-z_][A-Za-z0-9_$#]{0,127}$`), `ColumnInfo` (no `pseudo` yet — Task 3 adds it),
  `ColumnKind`, `TableShape` (+ `key_columns()`, `column(name)`), `KeyRange(lower, upper)` encoded,
  `EncodedKey`, `encode_key`, `decode_key` (tag `ts` with `ts_precision > 6` stays text), `to_engine_text`,
  `from_engine_text`, `LedgerMark(job_id, partition_no, batch_no, last_key, rows)`,
  `ConnectionTestResult`, `PrepareReport(created, truncated, ledger, existing_rows, checks,
  append_upper)`, `ConnectorError(code, detail="", *, retryable=False)`, `IncompatibleValue`,
  `UnsupportedKey`: **same**. `ConnectorError` already keeps `.code`, `.detail`, `.retryable`, so the
  plan's one-line M2 touch (Task 4) is not needed. The key codec already has the `oid` tag
  (`object_id` kind, decoded as text) and the TIMESTAMP(7-9) text rule, so the file-structure row
  "`base.py`: `oid` and TIMESTAMP(7-9) key codec rules" is already satisfied by M2. Extra M2 name M3
  uses: `LedgerConflict(ConnectorError)` (`ledger_conflict`, contract §0 amendment "the write guard
  is the caller's mark"), which the Oracle/Mongo writers raise instead of inventing their own.
- **protocols**: `Connector` with `key_at_offset(shape, selection, params, offset)` and
  `sample_keys(shape, selection, params, n, *, upper=None)`: **same**. `BatchWriter`: `prepare(*,
  dry_run)`, `precheck_collisions(keys)`, `read_ledger(job_id, partition_no)`, `write_batch(rows, *,
  mark)`, `finish_partition(job_id, partition_no, *, batch_no=None) -> dict`, `clear_ledger(job_id)`,
  `close()`: **differs** only by `finish_partition`'s keyword `batch_no` (contract amendment; database
  writers ignore it) — M3 writers (Tasks 6, 12) accept and ignore it. `ResolvedTarget(job_id,
  write_mode, connection_id, engine, object, same_as_source, create_if_missing, table_load,
  pk_strategy, files, ledger_owner, key, write_columns, generated_key, target_shape, column_types)`:
  **same**.
- **connections** (`dps/connections.py`): `Connection`, `Credentials(username="", password: SecretStr,
  extra: dict[str, SecretStr])`, `load_credentials(c, *, settings, kek, conn=None)`, `get_connection`:
  **same**; the `dsn`/`uri` files of a `secret_ref` arrive in `Credentials.extra` (`dps.secrets.
  SECRET_FILES` lists both). Note: `resolve_secret_ref(ref, settings)` is not tenant-scoped yet; the
  queued SEC-2 fix resolves under `DPS_SECRET_DIR/<tenant>/<ref>/` at integration, so no M3 code or
  demo relies on one tenant reading another's ref (see Task 2).
- **registry** (`registry.py`): `open_connector(conn, settings, *, action, kek, job_id=None,
  credentials=None)` returns an opened connector and raises `engine_unavailable` for `oracle`/`mongo`:
  **same**. The PG connector's constructor is `PostgresConnector(conn, credentials, settings, *, action,
  job_id=None)`; the M3 connectors take the same arguments (Tasks 5, 11 call sites).
- **selection**: `Selection(where, params, query, columns, max_rows, sample: SampleSpec(percent, seed),
  mongo_filter)` (in `dps/jobs_models.py`), `SelectionError(code, message)`, `quote_ident(name,
  engine)` (Oracle: `^[A-Za-z][A-Za-z0-9_$#]{0,127}$`, upper-cased), `validate_selection(sel, engine,
  shape)`, `compile_where`/`compile_query(sel, engine) -> (sql, ordered bind names)` (Oracle renders
  `:name`), `validate_mongo_filter`, `mongo_filter_template`, `MONGO_OPERATORS`, the codes
  `selection_operator_denied`, `selection_identifier_invalid`: **same**; M2 also has
  `parameterize_literals(sel, engine)` (literals → `:lN` binds) which the Oracle reads reuse.
- **engine bridge** (`dps/engine/batch.py`): `ColumnPlan(path, name, entity_type, method, text_column,
  reversible, kind, length, precision, scale)`: **same**; `BatchTransformer(engine, *, policy, mode,
  columns, shape, key, seed, locale, unclassified="detect", tabular=None, anchor=None,
  verify="strict")`: **same** names (more keywords than the plan lists, all defaulted).
- **discovery** (`discovery.py`): `discover_job(ctx, job)` exists (the plan said "not yet written");
  per-column classification is the shared `classify_columns(shape, rows, *, policy, detector,
  fpe_enabled)`. Lesson (Stage A): `main` is being fixed so that a text column whose sampled values hit
  detectors at a meaningful rate is never auto-kept (`discovery.py`, `jobs_run.py`). Task 8 must route
  Oracle and Mongo discovery through `classify_columns` (no engine-specific classifier) so the fix is
  inherited at merge; only the value read per column (`docpaths.column_values`) and the forced `keep`
  of pseudo/never-transformed columns are M3's.
- **targets / handlers / groups**: `targets.resolve_target(spec, *, job_id, source_conn,
  target_conn, source_shape, target_shape, plans, projection) -> (ResolvedTarget, plans)`;
  `created_column_type` returns **PostgreSQL** DDL types (open question 14: Oracle targets need
  `oracle_sql.oracle_ddl_type`, Task 6 call site). `jobs_run.py` has `prepare`, `run_partition`,
  `finalize` (finalize calls `writer.clear_ledger(job.id)` through `_clear_ledger`; same-table append
  is bounded by `append_upper` in `_prepare`/`_check_same_table_offset`). `POST /job-groups {name}` →
  `{group, key_material, synthesis_seed}`: **same**. **Differs:** the group model is
  `dps.groups.JobGroup(id, tenant_id, name, kid, seed_fingerprint, created_by, created_at, closed_at)`
  in `dps/groups.py` (not `jobs_models.py`); Task 17 adds `last_check`/`last_check_at` there.
- **fixtures**: `db_settings`, `migrated_db`, `api_client`, `kek` (`tests/fixtures/db.py`) and
  `source_pg` (`tests/fixtures/sources.py`): **same**. Endpoint helpers exist twice:
  `tests/services.py` (`service_url("oracle"|"mongo")`, `host_port`, already lane-safe) and
  `tests/fixtures/services.py` (no Oracle/Mongo entries); `tests/fixtures/m3_services.py` uses the
  former, so no host port is hard-coded (deviation below).
- **demo PG data**: `bank.customers(customer_id bigint` from `10000001`, `full_name`, `email`,
  `mobile`, `national_id`, `tax_id char(10)`, …`)`, targets in `bank_uat`, seed marker
  `bank._dps_seed_info`, `tools/seed_sources.py` `CUSTOMER_BASE = 10_000_001`: **same**.
- **conformance** (`tests/connectors/conformance.py`): exists. Interface = `ConformanceEnv` protocol
  (`engine`, `source`, `rw`, `make_table(name, *, key, rows)`, `insert`, `read_all`, `target(obj,
  shape, write_mode, **kw)`, `rejected_selections()`) + the `ConnectorConformance` test class +
  `sql_rejected_selections()`; Task 14's harnesses implement `ConformanceEnv`.
- **console**: `pages/{Jobs,JobDetail,JobWizard}.tsx` and `pages/wizard/{StepConnect,StepSelect,
  StepDiscover,StepTarget,StepRun,RunResult,state}.ts(x)`, `api.connections`, `api.jobs`,
  `api.snapshots`, `api.groups.{list, create}` (no `get` yet), Vitest (`lib/jobs.test.ts`,
  `wizard/state.test.ts`): **same** names; `types.ts` `Selection` has no `mongo_filter` yet (Task 18b).

## Task 1 — dependencies, settings, markers, service fixtures

- `tests/fixtures/m3_services.py`: `oracle_dsn()` / `mongo_uri()` return `tests.services.
  service_url("oracle"|"mongo")` (plan: literal defaults `127.0.0.1:1522/FREEPDB1`,
  `mongodb://127.0.0.1:27018/...`) and the TCP probe uses `host_port()` (plan: string splitting). Why:
  contract §0 amendment 2026-10-12 — tests read endpoints only from `DPS_TEST_*`, else from the
  `DPS_ORACLE_PORT` / `DPS_MONGO_PORT` compose variables; this lane runs Oracle on 1523 and Mongo on
  27019.
- `tests/fixtures/m3_services.py`: the drivers are imported inside the fixtures
  (`pytest.importorskip` per fixture) instead of at module level. Why: the module is a `pytest_plugins`
  entry of `tests/conftest.py`; a module-level `importorskip` in a plugin aborts collection of the
  whole suite on a host without the drivers instead of skipping only the tests that need them.
- `tests/fixtures/m3_services.py`: `mongo_client` tolerates `PyMongoError` while it waits for a
  primary (plan: `hello` once per loop, an exception would escape), closes the client before skipping,
  and `OracleTestSchema.__repr__` names the users only (the passwords are random per session but a
  failing assertion would otherwise print them).
- `pyproject.toml`: the `oracle` / `mongo` marker descriptions name `DPS_TEST_*` and the port variables
  (`${DPS_ORACLE_PORT:-1522}`, `${DPS_MONGO_PORT:-27018}`) like M2's markers; the `connectors` extra is
  written one entry per line. Installed: `oracledb 26.0.0`, `pymongo 4.18.3` (`dnspython 2.9.0`).
- `.env.example`: **not modified.** As in M2 Task 1, the session's permission rules deny every read and
  write of `.env*` files in this worktree. The four `DPS_` settings (and the rule that Oracle/Mongo
  credentials never go in env vars) are documented in `HANDOFF.md` §4 "M3 settings" instead. Open issue:
  append them to `.env.example` from a session that may edit it.
- `HANDOFF.md`: §4 gains the "M3 settings" table (contract §0.6: HANDOFF rides with config changes).
- `dps/config.py`: the module regex `_ORACLE_MODULE` holds the plan's pattern; validator names are
  prefixed (`_oracle_pool_max`, …) so they cannot shadow M0/M2 validators. Messages are the plan's.
- `dps/observability.py`: of the plan's allow-list additions, `engine`, `job_id`, `group_id`,
  `partition_no`, `batch_no`, `rows`, `object_ref` were already present (M1/M2); added `op`,
  `driver_code`, `members`, `pairs`, `coverage`, `passed`, `sampled`. M2 defines no connector-call
  counter, so `CONNECTOR_CALLS` and `CONSISTENCY_CHECKS` are new as planned.

## Task 2 — Local stack: Oracle Free, MongoDB replica set, demo schemas, secret refs

- **Demo secret refs are written by M2's `demo-secrets` one-shot, not committed** (plan:
  `docker/secrets/local/<name>/{username,password,dsn|uri}` committed and bind-mounted at
  `/var/run/secrets/dps/local:ro` into app and worker; refs `local/<name>`). Files: `docker-compose.yml`
  (`demo-secrets` command), no `docker/secrets/local/`. Why: M2 built the demo refs as a one-shot
  that writes into the `dps-secrets` volume ("nothing is committed", contract §11 as revised for M2),
  which is exactly the alternative the plan's open question 11 offers; a second, committed tree
  bind-mounted *inside* the read-only `dps-secrets` mount would split the demo refs over two
  mechanisms; and the queued SEC-2 fix (tenant-scoped `secret_ref`, applied at integration) moves
  every demo ref under the demo tenant in that one-shot, so M3's refs must live where M2's do. Names
  follow M2's `dps/src|tgt/...` scheme with the plan's leaf names: `dps/src/oracle-crm-ro` (`dps_ro`),
  `dps/src/oracle-crm-owner` (`crm`), `dps/tgt/oracle-crm-masked` (`crm_masked`),
  `dps/src/oracle-loans-owner` (`loans`) — each `username`, `password`, `dsn` =
  `oracle:1521/FREEPDB1` — and `dps/src/mongo-crm`, `dps/tgt/mongo-crm-uat` (`uri` =
  `mongodb://mongo:27017/crm|crm_uat?directConnection=true`; no username/password, the local replica
  set has no auth). Later tasks that name `local/oracle-crm-ro` etc. (smoke test, Task 21) use these
  refs; the e2e tests' own `m3/...` refs under a temporary secret dir are unaffected.
- `tests/test_compose_m3.py`: `test_app_and_worker_mount_local_secret_refs_read_only` became
  `test_app_and_worker_read_the_demo_refs_read_only` (the M2 `dps-secrets` mount, `DPS_SECRET_DIR`, and
  no `depends_on` on `oracle`/`mongo`); `test_secret_refs_hold_no_production_shapes` became two tests:
  the one-shot's script names only the in-network hosts `oracle` and `mongo`, and
  `test_demo_secret_refs_resolve_to_credentials` runs the one-shot's shell command against a temp dir
  and reads every M3 ref back through `dps.connections.load_credentials` (username, password, `dsn` /
  `uri` in `Credentials.extra`). The same command was also run once in the app image's `/bin/sh`
  (dash; `data-privacy-services:m2`) into a scratch dir: all 22 files written as expected.
- `docker-compose.yml`: host ports are `127.0.0.1:${DPS_ORACLE_PORT:-1522}:1521` and
  `127.0.0.1:${DPS_MONGO_PORT:-27018}:27017` (plan: literal 1522 / 27018). Why: contract §0 amendment
  2026-10-12; `tests/test_compose.py` requires the variable form. The tests assert those forms.
- `docker-compose.yml`: `labels` written as a block mapping like the M2 services (same keys/values).
  The `app`/`worker` volume lists and `DPS_SECRET_DIR` are unchanged (M2 already mounts `dps-secrets`
  at `/var/run/secrets/dps:ro` and sets `DPS_SECRET_DIR`).
- `docker/oracle/initdb/01_crm.sql`: **bug fix** — the plan put comments after the `;` of two `GRANT`
  statements on the same line. SQL*Plus only ends a statement at a `;` that ends its line, so it kept
  buffering, sent `GRANT … TO CRM_MASKED; -- … GRANT … TO LOANS;` as one statement, Oracle 23ai
  refused it (`ORA-03405: End of query reached; no additional text should follow`) and the
  entrypoint stopped (`WHENEVER SQLERROR EXIT`): the container stayed `unhealthy` with half the users
  created. The comments now sit on their own lines; `test_init_sql_creates_contract_users_and_tables`
  asserts no `;` is followed by a `--` comment, and also that `DPS_RO` gets only `CREATE SESSION` and
  `SELECT`.
- `tests/test_m3_stack.py` (new, markers `integration` + `oracle` / `mongo`): the plan's Step 7 manual
  checks as repeatable tests — `DPS_RO` sees exactly the six demo tables (with `TAX_ID CHAR(10)`,
  `NOTES CLOB`, `LAST_CONTACT_AT TIMESTAMP(9)`) and an `INSERT` is refused; the per-session fixture
  schema is least-privilege (`ORA-00942` before `grant_select`, rows after); the Mongo node is the
  `rs0` writable primary and a transaction aborts and commits. Oracle 23ai reports the refused insert
  as `ORA-41900` (missing INSERT privilege) where 19c says `ORA-01031`; the test accepts both.
- `HANDOFF.md`: §3 services table (`oracle`, `mongo`; `demo-secrets` row lists the M3 refs) and the
  M3-only start command, §4 compose-only variables (`DPS_ORACLE_PORT`, `DPS_MONGO_PORT`) and the M3
  `DPS_TEST_*` variables, §7 the ref names, §8 traps (`up --wait`, a failed Oracle init, the SQL*Plus
  `;` rule, `ORA-41900`). Mongo has no named volume (the image's anonymous volumes; `docker compose
  rm -sfv mongo` resets it), like Oracle's "no volume" reset.
- Contract: no change. §11 already names the `oracle`/`mongo` services, `docker/oracle/initdb/01_crm.sql`
  and the `demo-secrets` one-shot; the plan's §11 mount of `docker/secrets/local` is not made (above).

## Task 3 — Oracle rules: type table, SQL builders, ROWID codec

- **Write ledger statements follow the binding write-guard amendment** (contract §0 amendment
  2026-10-12, "the write guard is the caller's mark", written after this plan). Plan: one
  `ledger_mark_sql` `MERGE … WHEN MATCHED … WHERE s.BATCH_NO > l.BATCH_NO WHEN NOT MATCHED THEN INSERT`
  with positional binds, and a ledger without a row counter. That MERGE applies a *skipped* batch
  (mark 7 over ledger 5) and relies on MERGE re-reading a row another holder changed while it waited.
  Now (`dps/layers/connectors/oracle_sql.py`):
  - `ledger_ensure_sql(owner)` (new name): `MERGE … WHEN NOT MATCHED THEN INSERT (… 0, '[]', 0 …)` —
    creates the partition's row at batch 0 before it is locked; a concurrent first batch waits on the
    other holder's uncommitted insert and then fails with ORA-00001, which the writer (Task 6) maps to
    `ledger_conflict`.
  - `ledger_mark_sql(owner)`: the guarded advance `UPDATE … SET BATCH_NO = :batch, LAST_KEY =
    :last_key, ROWS_WRITTEN = :written … WHERE JOB_ID = :job AND PARTITION_NO = :part AND BATCH_NO =
    :prev` (`:prev` = `mark.batch_no - 1`); row count 1 = apply the batch, 0 = `ledger_conflict`.
    Oracle restarts an UPDATE whose WHERE column another transaction changed while it waited for the
    row lock, so a zombie re-reads and matches 0 rows. No `FOR UPDATE` anywhere (the plan's invariant).
  - `ledger_ddl` gains `ROWS_WRITTEN NUMBER(19) DEFAULT 0 NOT NULL` and `ledger_read_sql` selects it:
    M2's `LedgerMark` carries `rows` (cumulative rows; a resume reconciles the partition counters), so
    the Oracle ledger must store it like PostgreSQL's `rows`. A `BATCH_NO = 0` row means "no mark"
    (contract amendment; Task 6's `read_ledger`).
  - every ledger statement carries `/* dps:ledger */` and named binds (`:job`, `:part`, …).
  - `tests/connectors/test_oracle_sql.py::test_ledger_statements_are_19c_safe_and_fenced` asserts the
    ensure/guard shapes above instead of the plan's `s.BATCH_NO > l.BATCH_NO` (a strictly stronger
    fence, as the contract requires); `ledger_ensure_sql` and `ledger_read_sql` joined the
    "no FOR UPDATE / no FETCH FIRST" loop.
  Writer contract for Task 6: in the batch's transaction run `ledger_ensure_sql`, then
  `ledger_mark_sql` (0 rows → rollback, `LedgerConflict`), then the rows, then commit; ORA-00001 from
  the ensure statement → `LedgerConflict`.
- `tests/connectors/test_oracle_sql_live.py` (new, markers `integration` + `oracle`): the statements
  executed on the lane's Oracle Free — composite TIMESTAMP(9) keyset paging through a burst of 7 rows
  inside one microsecond (every row once, nanoseconds intact); CHAR keys paged with CHAR binds (and the
  untyped VARCHAR2 bind shown re-reading `K17`, the pitfall); a key-less table paged on ROWID with the
  DB order equal to `rowid_to_key` order and `[lower, upper)` ROWID ranges; `count_sql`,
  `sample_sql` (sorted key sample, row sample), a repeatable `SAMPLE … SEED`; `create_table_sql`,
  `merge_sql` twice (idempotent, CLOB of 4.5 KB), `update_sql`, `insert_sql` from a sequence,
  `collision_sql`; the ledger DDL twice (ORA-00955 swallowed), ensure/mark/read/purge (replayed and
  skipped batches fenced, a partition starts at batch 1) and **the fence under a real concurrent
  holder** (a second session blocked on the first batch gets ORA-00001 after the commit; blocked on
  batch 2 it matches 0 rows after the commit). Why: lesson (1) of Stage A — a fence claim must be
  backed by a run, not by statement text.
- `tests/connectors/test_oracle_sql.py`: the plan's tests plus statement checks for the query source,
  the seeded sample, `count_sql`, `sample_sql`, composite `collision_sql` binds, `column_path` for
  `$`/`#` names, `tuple_predicate` arity errors, `qualified()` (owner required, upper-cased) and the
  `pseudo` default (40 tests). Plan rows wrapped to 100 columns; the test oracle's `eval` is commented
  (it evaluates a predicate the code under test built from fixed names).
- `dps/layers/connectors/oracle_sql.py`: `_KIND` is typed `dict[Family, ColumnKind]` and the key
  bound loop is typed (`KeyOp`), so the plan's two `# type: ignore[arg-type]` are gone; the ROWID
  codec names its bit widths. `key_columns(shape)` returns `shape.key_columns()` (M2's method).
- `dps/layers/connectors/base.py`: `ColumnInfo.pseudo: bool = False` as planned; contract §4.1 now lists
  the field in the `ColumnInfo` block (the "M3 adds" comment line is folded into it).
- **M2 touch point** `dps/layers/connectors/postgres.py`: `_typmod_fields` returns a `TypedDict`
  (`_TypmodFields`, `total=False`: `length`, `precision`, `scale`, `ts_precision`) instead of
  `dict[str, int | None]`. Why: with the new `bool` field on `ColumnInfo`, mypy rejects
  `ColumnInfo(..., **dict[str, int | None])` (the unpacked dict could fill `pseudo`). Typing only — the
  same dict at run time; the unit suite is green (the PG integration tests were not run in this lane:
  no `source-pg` was started for an annotation-only change).

## Task 4 — Oracle session pool, value-free driver errors, fetch types

- `dps/layers/connectors/oracle.py` `connector_error`: the plan's one connection-class set (all
  `connection_failed`, retryable) is split by what a retry can fix, matching M2's PostgreSQL
  connector (`_LOGIN_REFUSED` not retryable, network failures retryable):
  - refused logins `ORA-01017`, `ORA-01045`, `ORA-28000`, `ORA-28001`, `ORA-28040` →
    `connection_failed`, **not retryable**. Why: a retried wrong password counts towards the
    profile's `FAILED_LOGIN_ATTEMPTS` (default 10) and locks the bank's service account; a locked or
    expired account cannot be fixed by retrying.
  - unreachable/lost: the plan's `ORA-12505/12514/12541/12170`, `TNS-12541`, `DPY-6000/6001/6005`,
    `DPY-4011` plus `ORA-12537/12547`, `ORA-03113/03114/03135` (lost mid-call), `DPY-1001` (not
    connected), `DPY-6003`, `DPY-4005` (pool wait timed out) → `connection_failed`, retryable.
  - call timeouts `DPY-4024` (thin), `ORA-03156` / `ORA-01013` (thick) → `statement_timeout`
    (contract §5.3 code; M2's PG maps its statement timeout the same way), not retryable.
  The plan's tests are kept (`test_connection_class_codes` checks the code only); new tests pin the
  retry split and the timeout code.
- `_DRIVER_CODE` also accepts `PLS-nnnnn` (PL/SQL compile errors from the ledger DDL block); anything
  else stays `ORA-unknown`.
- `OraclePool.acquire` turns a failure to open or check out a session (`create_pool` is lazy, so
  this is where a login fails) into `connector_error("connection_failed", exc)` raised `from None`
  (plan: the raw driver error escaped `acquire`, carrying its message). `module` and `action` are
  capped at 64 characters like `client_identifier`. `easy_connect` raises `ConnectorError(
  "connection_invalid", "host and service_name must be plain names")` (value-free detail).
- **Measured driver behaviour** (python-oracledb 26.0.0 thin on Oracle 23.26 Free), now in the module
  docstring, HANDOFF §8 and `tests/connectors/test_oracle_session_live.py`:
  - a long SQL call over `call_timeout` fails with `DPY-4024` and the session stays usable; a call
    the server cannot break (PL/SQL `DBMS_SESSION.SLEEP`) ends with the driver closing the connection
    (`DPY-4011`), and **setting `call_timeout` on that closed connection crashes the process with
    SIGBUS** (reproduced in a scratch script). The pool's rollback-on-release raises `DPY-1001` for
    it and the session is dropped, so it never returns to the pool; the live test proves the next
    checkout is fresh and the process survives.
  - an unknown service surfaces as `DPY-6005` in thin mode (the listener's ORA-12514 is wrapped).
  - Oracle 23ai's `ORA-00001` message quotes the duplicate row's value — the live test shows the raw
    driver text containing the sentinel and the `ConnectorError` without it.
- `tests/connectors/test_oracle_session.py`: the plan's tests (formatted to 100 columns) plus: close is
  forced and idempotent, size 0 refused, the block's own exception still rolls back and releases,
  `acquire` failures are value-free, more unknown driver shapes, `easy_connect` default port and
  missing host/service (38 tests).
- `tests/connectors/test_oracle_session_live.py` (new, `integration` + `oracle`): session tags
  (`MODULE`/`ACTION`/`CLIENT_IDENTIFIER`) and `NLS_SORT`/`NLS_COMP = BINARY` read back from the
  server; release rolls back an uncommitted insert; both timeout shapes above; refused login
  (`ORA-01017`, not retryable, password not in the error), closed port (retryable), unknown service;
  value-free `ORA-00001` and `ORA-00942`; fetch types (NUMBER(12) → int, NUMBER(14,2) and NUMBER →
  Decimal exact above 2**53, BINARY_DOUBLE → float, CLOB/NCLOB of > 4000 bytes with astral characters
  → whole str, BLOB → bytes, `IS JSON` VARCHAR2 and CLOB → str, not dict).
- `ConnectorError.detail` already existed (Task 1 audit): no M2 touch in `base.py`.

## Review round 1 fixes (Tasks 1-4) — `M3-G1-review-r1.md` B1, B2

- **B1 — TIMESTAMP(0-6) binds are typed TIMESTAMP** (`dps/layers/connectors/oracle_sql.py`,
  `dps/layers/connectors/oracle.py`). Measured: python-oracledb 26.0.0 thin binds a bare `datetime`
  as `DB_TYPE_DATE` and drops the fractional seconds. A `merge_sql`/`update_sql` `executemany` into
  `TIMESTAMP(6)` lost the microseconds (the demo's `CREATED_AT`, `OCCURRED_AT`, `APPLIED_AT`), and a
  keyset over `(NUMBER, TIMESTAMP(6))` re-read the same rows on every page. Now:
  - `InputType` gains `"timestamp"`; `input_type()` returns it for `TIMESTAMP` / `TIMESTAMP(0-6)`
    without a zone (the same `is_ts9` test as `value_expr`, so a column is either a typed TIMESTAMP
    bind or FF9 text, never both). `DATE` keeps the driver's DATE bind (exact for DATE); TIMESTAMP(7-9)
    and WITH TIME ZONE stay text (`None`).
  - `oracle.INPUT_TYPES["timestamp"] = oracledb.DB_TYPE_TIMESTAMP`; a unit test pins that
    `INPUT_TYPES` covers every `InputType` literal.
  - `read_sql` therefore types the `lo/hi/af` key binds of a TIMESTAMP(0-6) key column. The writers
    (Task 6) pick it up through the plan's `INPUT_TYPES.get(osql.input_type(c) or "")` for SET,
    VALUES and key binds; `lookup_sql` (plan) through `input_type(join)`.
  - Contract §4.2 engine notes record the rule; HANDOFF §8 has the trap.
  - Tests: unit `test_timestamps_up_to_microseconds_bind_as_timestamp`,
    `test_timestamp6_key_bounds_are_typed`, `test_every_input_type_has_a_driver_type`; live
    `test_keyset_pages_timestamp6_keys_with_their_microseconds` (10 microsecond-distinct composite
    keys, none `.000000`, paged with batch 2 and 3, typed from the plan's `bind_types`) and
    `test_timestamp6_writes_keep_their_microseconds` (MERGE twice with named binds, UPDATE by a
    `(NUMBER, TIMESTAMP(6))` key and INSERT with positional binds; the first row at `.000000`).
    The live `_page_all` helper now types its binds from the plan (as the connector will) and fails
    at the first re-read row instead of looping. Mutation check: with `INPUT_TYPES["timestamp"]`
    forced back to `DB_TYPE_DATE`, both live tests fail (re-read after 2 rows; values differ).
- **B2 — `sample_sql(keys_only=True)` returns key quantiles** (`oracle_sql.py`). Plan:
  `… ORDER BY key FETCH FIRST :n`, which returns the lowest `n` keys of the sampled set (measured:
  keys 4..603 of 0..9999), so every partition boundary fell at the bottom of the key space. Now an
  inline view ranks the set (`ROW_NUMBER() OVER (ORDER BY <table-qualified keys>) DPS_RN`,
  `COUNT(*) OVER () DPS_N`) and the outer query keeps `MOD(DPS_RN - 1, CEIL(DPS_N / GREATEST(:n, 1)))
  = 0` ordered by `DPS_RN`: the first key of every run of `ceil(total / n)` keys, at most `n`, in the
  database's order (M2's PostgreSQL `sample_keys` keeps the last key of each run; the first is used
  here so the count is `ceil(total / step)`, closer to `n`; `plan_partitions` never cuts at
  `sample[0]` either way). No lock, no `FETCH FIRST` in this statement; the `DPS_*` aliases name the
  view's columns only (an analytic ORDER BY never sees select-list aliases). The row sample
  (`keys_only=False`) is unchanged.
  - **Additive keyword** `sample_sql(..., upper: bool = False)`: binds `:hi<i>` (typed like
    `read_sql`'s) bound the set *before* ranking, so the quantiles are those of `[min, upper)`. Why:
    with spread keys, the plan's Task 5 Python filter `k < tuple(upper)` would throw away every key
    above the bound and leave a `max_rows` job with one or two boundaries. **Task 5 must pass
    `upper=upper is not None` with `hi<i>` binds (through `_bind_key`) instead of filtering in
    Python** (this also keeps Python from ever ordering keys, review N3).
  - Contract §4.2 `sample_keys` comment: "spread over that bounded set (quantiles), never just its
    lowest n keys" (spec §7 already says partitions come from key quantiles).
  - Tests: unit `test_sample_sql_spreads_keys_over_the_set_or_draws_a_fresh_sample` (exact text),
    `test_key_sample_below_an_upper_bound_is_spread_below_it` (CHAR + TIMESTAMP(6) composite bound,
    typed `hi` binds, ROWID keys); live `test_key_sample_spreads_over_the_whole_key_space` (10,000
    keys: unsampled `n=50` → exactly `0, 200, …, 9800`; `SAMPLE (10)` → max in the top decile and
    min in the bottom one; `upper=5000` → `0, 100, …, 4900`; `n` above the set → every key; `n=3`
    → 3 keys; `n=0` → 1 key).
- Not changed (non-blocking review items, left for the tasks they name): N1 sample percent below
  `0.0000005`, N2 BYTE-semantics VARCHAR2 created targets, N4 dead TSTZ key path, N5 `.env.example`,
  N6 per-cursor output type handler.

## Task 5 — `OracleConnector` reads

- **`selection.sample` is a hash of the key, not `SAMPLE … SEED`** (`oracle_sql.sample_filter`, new;
  `OracleConnector._compile`). Measured on 23ai Free: with `SAMPLE (40) SEED (7)` on 200 rows,
  `COUNT(*)` saw 81 rows and keyset pages of the same clause saw 72 (the sampled set depends on the
  access path), so the count, the partitions and a resumed page would not read one sample. The sample
  is now `ORA_HASH(<key>, 999999, seed mod 2**32) < percent·10**4` (composite keys fold the per-column
  hashes), a function of the row alone like M2's PostgreSQL `hashtextextended` filter; it also works
  for `query` selections (the plan applied nothing there: `_source` ignores the sample under a query).
  `read_sql`/`count_sql` are called with `sample=None`; their `SAMPLE … SEED` path stays (Task 3 tests)
  but no connector code uses it. Unit test `test_selection_sample_is_a_key_hash_filter`; the live test
  checks repeatability, the count, a partition split, another seed, a query and a narrowing where.
- `_compile` binds literals itself through M2's `parameterize_literals` (generated `:lN` binds take
  their values from the bound selection, user binds from `params`), so a where with literals runs
  bound, never interpolated (plan: `compile_where` on the raw selection, which M2 refuses with
  `selection_not_parameterized` when literals are present). The ROWID `selection_query_needs_key` check
  runs before `validate_selection` (M2's key-projection rule would otherwise answer
  `selection_query_missing_key` for a ROWID table).
- `sample_keys` bounds the ranked set with `sample_sql(upper=True)` and typed `hi<i>` binds (review B2;
  plan: filter in Python) and samples with `SAMPLE` sized for `10·n` keys (plan: `n`) so the quantiles
  of large tables come from about `20·n` sampled keys; `sample()` retries without `SAMPLE` when a
  selective where left fewer than `min(n, 50)` sampled rows (M2's PostgreSQL does the same).
- `test()` checks carry `severity` (M2's console `Check` type and PostgreSQL's result): `required`
  `connect`, `select_source`, `select_target`, `insert_target`, `update_target`, `create_target`,
  `create_ledger`; `advisory` `session_tagged`, `no_dml_on_source`. `ok` = every required check; a
  refused login returns `error_code="connection_failed"` with the driver code in the check's
  `detail` (never raises), like PostgreSQL.
- `engine: Engine = "oracle"` (plan: `Final`): a `Final` attribute does not satisfy the `Connector`
  protocol's settable `engine` under mypy. `from_secret` also refuses empty `username`/`password`
  (`secret_unavailable`) and strips the `dsn` file. `_call` forwards keyword arguments and logs the
  mapped error's code (`oracle_call_failed`, fields `op`, `driver_code`). `list_objects` clamps
  `limit` to 1..5000 and skips names `ObjectRef` refuses, like PostgreSQL.
- Tests (`tests/connectors/test_oracle_connector.py`, 20): the plan's 16 plus describe without an owner,
  ROWID-range partition coverage of a key-less table, NVARCHAR2 keys as one partition, and the owner's
  in-place / missing-table connection checks. `TAX_ID` values are 9 characters in `CHAR(10)` so the test
  proves CHAR values come back blank-padded. Every Oracle test module drops the session owner's tables
  before and after each test (`oracle_fixtures.drop_all`): the owner is one schema per session, so
  `KYC` and other names repeat across modules.

## Task 6 — `OracleWriter`

- **The write guard is the caller's mark** (contract §0 amendment 2026-10-12; Task 3's statements).
  Plan: one guarded `MERGE` whose 0 rows raise `ConnectorError("ledger_conflict")`. Now: in the
  batch's transaction `ledger_ensure_sql` (ORA-00001 → `LedgerConflict`: another holder committed
  the partition's first batch while this one waited), then `ledger_mark_sql` from `mark.batch_no - 1`
  (row count ≠ 1 → `LedgerConflict`), then the rows, then commit; `ROWS_WRITTEN` holds `mark.rows`.
  `LedgerConflict` (M2's class) makes M2's partition handler stop quietly; the plan's plain
  `ConnectorError` would have been retried. An empty ledgered batch still records its mark (contract
  amendment; plan: `return 0`). New live test `test_a_concurrent_holder_is_fenced`: a second session
  holds the ledger row in an open transaction (first batch and a later one), the writer blocks, and
  after the commit raises `LedgerConflict` and writes nothing. Mutation check: with the row-count
  guard removed, `test_ledger_fences_a_replayed_batch` and `test_rowid_table_load_is_ledgered` fail.
- **Prepare refusals raise M2's `PrepareFailure(code, detail)`** (plan: `ConnectorError(code)`), the
  type M2's PostgreSQL writer raises and M2's handlers fail without retry (a `ConnectorError` from
  `write_batch` is retried until dead-lettered). Codes are the plan's: `no_key`,
  `pk_strategy_invalid`, `column_unsupported`, `target_missing`, `column_missing`,
  `incompatible_method`, `length_overflow`, plus M2's `target_invalid` for a refused DDL privilege
  (ORA-01031 / ORA-41900) and for a `table` target without a key on the key columns (MERGE would
  raise ORA-30926). Write-time: ORA-00001 on the rows → M2's `PkCollision` (`pk_collision`, as on
  PostgreSQL); ORA-12899 / ORA-01438 → `PrepareFailure("length_overflow")` (that batch can never
  succeed, so it fails the partition instead of retrying). `oracle.py` therefore imports
  `postgres.PkCollision` and `targets.PrepareFailure` (the `db` extra is needed with `connectors`,
  as for every worker).
- **Columns follow M2's `ResolvedTarget` as built.** Written columns are `write_columns` (in_place: the
  SET list) minus pseudo columns (M2 puts `ROWID` in a key-less run's projection), keys are
  `ResolvedTarget.key` minus pseudo columns (plan: the columns present in `rows[0]`). For
  `append` + `generate`, key columns are dropped unless a sequence fills them (`NEXTVAL`). Bind types
  come from the **live target table**, described once per writer (plan: the source shape), so CHAR
  padding, TIMESTAMP typing and FF9 text follow the column actually written — a created `VARCHAR2`
  copy of a `CHAR` source is never blank-padded. Zoned timestamps bind as FF9+offset text.
- **Created tables when M2 passes no target shape.** M2's `resolve_target` creates targets with
  `target_shape=None` and `column_types` in PostgreSQL DDL, where a planned text-like column is
  `text`. The writer keeps the source's Oracle type (`oracle_ddl_type`) and widens a column marked
  `text` to the worst-case `det_token` width `ceil((4·L + 16)·8/5)` (`VARCHAR2(n CHAR)`, CLOB past
  1000 characters or without a length). With a target shape (plan tests), its columns are the
  requirement, compared with the live table (`column_missing`, `incompatible_method`,
  `length_overflow`) — with M2's target shape (the live table) the check is a no-op.
- **Same-table `append` + `generate` needs a generator ahead of the table** (contract §0 amendment,
  binding for M3 writers; plan: identity/default only). The identity's sequence
  (`ALL_TAB_IDENTITY_COLS`) or the named sequence must be ascending and non-cycling, and one probe
  `NEXTVAL` must exceed `MAX(key)`: the dictionary's `LAST_NUMBER` is the cache high-water mark, not
  a bound on the next value, so only a probe proves it (it costs one gap, never a row; also under
  `dry_run`). A `GENERATED BY DEFAULT` identity behind explicitly inserted ids is refused
  (`pk_strategy_invalid`), as is a default without a sequence; another table needs only an
  identity, a default or an existing sequence. `append_upper` is reported for every same-table
  single-key append (M2's PostgreSQL rule), not only for `int`-kind keys.
- `prepare` reports `existing_rows` (`ALL_TABLES.NUM_ROWS`, None without statistics) and value-free
  `checks` (`create_target`, `existing_target`, `write_ledger`) like PostgreSQL; it runs with the
  long statement timeout. `table` + `upsert` does not truncate.
- `finish_partition(…, *, batch_no=None)` (M2 signature) returns `{}`; `close()` releases nothing
  (the connector owns the pool).
- Tests (`tests/connectors/test_oracle_writer.py`, 19): the plan's 13 (XMLTYPE replaced by `INTERVAL
  DAY TO SECOND`, always present in Free; the unsupported family is the same), plus the concurrent
  holder, a key-less `table` load under the ledger (replay fenced, `ROWID` never written), M2's
  created-target widening, `table` + `upsert`, `target_missing`, a missing/behind sequence, and the
  `files` refusal. Tests that write changed values assert that no source value of a changed column
  survived in the target (Stage A lesson 1).

## Task 7 — the Oracle pitfalls as tests

- **Red as planned, one M2 touch:** only `from_engine_text` truncated FF9 text (`datetime.
  fromisoformat` keeps microseconds). `dps/layers/connectors/base.py`: a `timestamp` column with
  `ts_precision > 6` returns the validated text unchanged (still `IncompatibleValue` when it does not
  parse). `decode_key` and `to_engine_text` were already right (Task 1 audit). This also lets M2's
  codec round-trip check (`to_engine_text(from_engine_text(t)) == t`) accept a `date_shift` on a
  TIMESTAMP(9) column instead of failing it with `incompatible_method`.
- **`date_shift` needed no change (M0 not touched):** M0's ISO-datetime rule already shifts the date and
  keeps the time tail verbatim, so `…T10:00:00.123456789` keeps `789` and reveals exactly. The plan's
  test is in `tests/test_methods.py` and pins it; the plan's `_split_submicro` code was not added.
- **New measured pitfall, fixed in `OracleWriter`:** the CLOB round-trip test failed through the writer
  with `ORA-01461`. Measured with a scratch probe (python-oracledb 26.0.0 thin, 23ai): a `MERGE …
  USING (SELECT :v … FROM dual)` refuses a CLOB bind of more than 32,767 UTF-8 bytes (32,767 ASCII
  characters pass, 32,768 fail; 8,191 emoji = 32,764 bytes pass, 8,192 fail), while `INSERT` and
  `UPDATE` take 140,000-character strings; and an `executemany` whose CLOB column mixes temporary LOBs
  with `str` values stored **NULL** for the LOB rows without an error. `_merge_lob_binds` therefore
  binds a MERGE batch's CLOB/NCLOB/BLOB column as temporary LOBs (`conn.createlob`) for every row when
  any value is over the limit (UTF-16 bytes for NCLOB), never for some rows only. The test writes a
  second batch mixing 32,767 / 32,768-byte / NULL / short / 140K values and reads every one back
  exactly. HANDOFF §8 records both traps (and the `SAMPLE … SEED` one from Task 5).
- `test_oracle_char_key_paging` also cuts a partition at an rstripped CHAR bound, and checks the
  in-place count in the table; `test_oracle_timestamp9_key_paging` also updates all 66 burst rows in
  place by their nanosecond keys (`UPDATE … WHERE TS = TO_TIMESTAMP(:k, FF9)`); the CLOB test asserts
  that the e-mail it masked is absent from the target (`DBMS_LOB.INSTR` = 0). The `IS JSON` CLOB case
  is kept as a fourth measured pitfall (the plan's).
- `tests/connectors/test_codec_m3.py` adds a microsecond `TIMESTAMP(6)` case (still a `datetime`).

## Task 8 — Oracle wired into the pipeline (registry, discovery, finalize, wildcard paths)

Resumed from an earlier attempt's uncommitted `registry.py`, `discovery.py`, `docpaths.py` and four
test files (reviewed, kept, extended). An in-process pipeline test (submit → discover → accept → run
→ finalize on the lane's Oracle Free) found four M2 seams the plan did not foresee; each is fixed
test-first below.

- `dps/layers/connectors/registry.py`: the Oracle branch as planned (lazy import → `engine_unavailable`
  without the extra, credentials flattened to `{username, password, dsn?}`, `client_id=job_id`), plus:
  the import is attempted before credentials are loaded (a host without the extra never opens a
  secret), and a failed `open()` closes the pool before re-raising. `mongo` keeps M2's
  `engine_unavailable` until Task 12 (the plan's combined branch would import a module that does
  not exist yet). The docstring lists `secret_unavailable` (from `from_secret`).
- `tests/connectors/test_registry_m3.py`: passes `Credentials` (or replaces `load_credentials`)
  instead of writing a `DPS_SECRET_DIR/<ref>/` tree (plan). Why: the queued SEC-2 fix makes refs
  tenant-scoped at integration; this unit test must not depend on the layout. The real `secret_ref`
  resolution of an Oracle connection is exercised by `test_oracle_pipeline.py` (below). Added: easy
  connect fallback without a `dsn` file, `secret_unavailable` without a password, a failed open
  closes the pool, `engine_unavailable` without the driver, mongo still unavailable.
- **M2 touch point** `tests/test_registry.py`: the `("oracle", "engine_unavailable")` row is removed
  (with the driver installed and empty credentials the Oracle branch now answers
  `secret_unavailable`); Oracle's registry behaviour is pinned in `test_registry_m3.py`.
- `dps/layers/connectors/docpaths.py`: the plan's functions, plus `top_level(column)` (one key of a
  flat row) and `row_locator(column)` (the ROWID pseudo key and real `ROWID`/`UROWID` columns).
  `forced_keep` = `row_locator` or a never-transformed kind (plan: `pseudo`, the three kinds, or
  `data_type == "ROWID"`; `UROWID(n)` was missed). `expand_wildcards`/`dotted` also swallow
  `ValueError` from the path parser (a malformed path addresses nothing; `dotted` → `None`).
- `dps/layers/connectors/discovery.py`: values are read through `docpaths.column_values` and
  `forced_keep` columns skip value detectors (plan). Two refinements: a **row locator is never
  classified, not even by its name** (a stored `ROWID` column named `ACCOUNT_NUMBER` would otherwise
  get a token method that cannot be bound into it), while binary columns and ObjectIds keep M2's
  name-only classification and then `keep` (`binary_kept`) — the plan said every `forced_keep`
  column gets `entity_type None`, which would hide a sensitive BLOB's name from the review; and
  `null_rate` of a wildcard document path counts a document once however many array elements
  matched. PostgreSQL rows read exactly as before (`top_level`: by column name; a missing key
  contributes nothing, as `to_engine_text(None)` did).
- **M2 touch points, the engine gates** (`dps/layers/connectors/submit.py`, `targets.py`, new
  `base.PIPELINE_ENGINES = {"postgres", "oracle"}`): `submit_discover` refused every non-PostgreSQL
  source ("M2 discovers PostgreSQL sources only") and `validate_run` every non-PostgreSQL run source,
  so no Oracle job could reach the registry. Both now admit `PIPELINE_ENGINES`; MongoDB joins it with
  its connector (Task 12). Same code (`engine_unavailable` 422), engine-neutral message. Tests:
  `test_targets.py::test_an_oracle_source_passes_the_engine_gate`, the static
  `test_pipeline_seams_m3.py::test_oracle_sources_pass_the_pipeline_engine_gates`.
- **M2 touch point** `targets.created_column_type(column, plan, *, engine="postgres")`: M2 checked
  every created type with PostgreSQL's lower-case `TYPE_RE`, so a table-mode run into a created
  Oracle table failed `target_invalid` ("`$.CUSTOMER_ID`: the column type cannot be copied",
  `NUMBER(12)`). The check now uses the target engine's pattern (`ORACLE_TYPE_RE =
  ^[A-Z][A-Z0-9 _(),]*$` for Oracle; an unknown engine is refused). The Oracle writer still reads
  only the `text` marker (Task 6). Contract §4 `ResolvedTarget.column_types` comment updated (open
  question 14 resolved this way). Test `test_created_column_types_follow_the_target_engine`.
- **M2 touch point** `jobs_run.py` finalize: M2 cleared the write ledger only for `in_place` /
  `append`; `OracleWriter` also ledgers a `table` load of a key-less source (ROWID pages, Task 6), so
  its rows stayed in `DPS_WRITE_LEDGER` after success (measured: 2 rows). `prepare` now records
  `plan.prepare["ledger"] = report.ledger` and finalize clears when the mode is `in_place`/`append`
  **or** prepare reported a ledger (PostgreSQL's `table` mode reports none: unchanged). Evidence then
  carries `write_ledger_cleared`.
- **M2 touch point** `jobs_run.py` post-write sample: the scan is now the module function
  `post_sample_hits(rows, shape, *, written, planned, detector)` and skips `forced_keep` columns and
  reads values through `docpaths.column_values` (a hit = one row × one column, any element of a
  wildcard path). Why: `in_place` scans every column of the table, and a packed ROWID can pass a Luhn
  check (counted as a kept-column hit → a needless review outcome); Mongo paths need the same read as
  discovery. Mutation check: with `forced_keep` stubbed to `False`, the unit test reports 10 kept hits
  instead of 5. PostgreSQL columns are scanned exactly as before.
- `tests/connectors/test_pipeline_seams_m3.py`: the plan's static checks (`open_connector` calls carry
  `job_id`, discovery calls `column_values`/`forced_keep`, finalize calls `clear_ledger` — matched as
  AST calls, not text) plus the gate, post-sample and ledger checks above. `jobs_run.py` already
  passed `job_id=job.id` and called `clear_ledger` (Task 1 audit).
- `tests/connectors/test_discovery_m3.py` (new, offline): ROWID pseudo key kept with Luhn-valid
  packed values while a real card column is classified; a stored ROWID column with a sensitive name
  kept; a BLOB keeps its name entity with `keep`/`binary_kept`; wildcard e-mail paths classified by
  value with a per-document null rate.
- `tests/connectors/test_oracle_pipeline.py` (new; markers `db`, `integration`, `oracle`): an Oracle
  connection stored with a `secret_ref` holding only username/password (DSN by easy connect from the
  params), then through M2's real handlers (`tests/connectors/helpers.drive`): (1) keyed `CUSTOMERS`
  (1,500 rows) discovered and loaded into a created `CUSTOMERS_UAT` — 0 surviving source values in
  `EMAIL`, `FULL_NAME`, `NOTES` (SQL counts; created token columns may be CLOB, so both sides compare
  as text), kept `SEGMENT` multiset identical, no ledger rows, key purged, evidence verified;
  (2) `in_place` on 900 rows with the key kept — row-by-row 0 equal `EMAIL`/`FULL_NAME`/`NOTES`,
  `SEGMENT`/`INCOME`/`LAST_CONTACT_AT` (TIMESTAMP(9)) unchanged on all 900, ledger cleared and
  `write_ledger_cleared` true; (3) a key-less table: discovery shows `ROWID` as the `rowid` key, kept,
  never classified; the `table` load writes 1,200 rows with 0 surviving e-mails, the created target
  has only the 3 table columns, and finalize clears the ledger.

## Review G2 round 1 fixes (Tasks 5-8) — `M3-G2-review-r1.md` B1, B2

- **B1 (query subset NULL-filled Oracle targets) — fixed by one shared read projection (the review's
  preferred option 1), on both engines.** New `selection.query_columns(sel, engine)` (folded output names
  of a query, `None` for no query or a bare/qualified `*`) and `selection.read_projection(shape, sel,
  engine)`: every column, narrowed by the query's own select list and by `columns` (Oracle folds
  `columns` upper), keys always, in table order. `query_projects_keys` now uses `query_columns`
  (same result). Used by `OracleConnector._projection` (signature lost its compiled-`query` argument),
  `PostgresConnector._projection`, `jobs_run._prepare` (→ `RunPlan.projection` → `ResolvedTarget.
  write_columns`, created DDL, files columns, post-sample) and the `runs_as` preview of the connections
  router. Contract §5.2 amended (2026-10-13).
  - **M2 behaviour change (PostgreSQL):** a `table`/`append`/`files` run whose query names a column
    subset no longer fails `source_error` (it selected `q."<col>"` for every column); it reads, creates
    and writes exactly the projected columns, like Oracle. An upsert into a wider existing target
    MERGEs only those columns, so matched rows keep every other value (as with `columns`).
  - **M2 behaviour change (Oracle `columns` folding):** `jobs_run` did not fold `columns`, so a
    lower-case `columns` list narrowed Oracle's read but not the plan; it is folded once now.
  - **Defence in depth:** `OracleWriter._write_sync` refuses a batch whose rows lack a bound column
    (`PrepareFailure("column_missing", …)`, value-free) instead of binding `None`.
  - Not changed: discovery (it samples through the connector, which now yields the same projection on
    both engines; a column outside the query is classified by name only, as Oracle already did) and
    `targets._projection` (submit-time method checks over `columns` ∪ keys: a superset, so stricter,
    and submit holds only the selection template).
  - Tests (failed before the fix): `test_selection.py::test_read_projection_is_the_query_select_list_
    on_both_engines` (ImportError); `test_run_modes.py::test_table_mode_query_subset_writes_only_the_
    projected_columns` (PG: was `failed source_error`; now target columns exactly `customer_id, email,
    segment`, 300 rows, 0 NULL e-mails, 0 surviving source e-mails, segment multiset equal, evidence
    passed); `test_oracle_pipeline.py::test_a_query_subset_creates_and_writes_only_the_projected_
    columns` (was 10 target columns; now exactly 3, 0 NULLs, 0 surviving `CUSTOMER_ID`/`EMAIL`);
    `…::test_a_query_subset_upsert_never_nulls_the_target_columns_it_does_not_read` (key kept; second
    run upserts a subset query: was 0/300 rows with `FULL_NAME`/`DOB` unchanged; now 300/300 and NOTES
    unchanged on every non-NULL row, 0 surviving source e-mails).
- **B2 (key-less Oracle `table` load stuck after a lost checkpoint) — fixed in `jobs_run`.** New
  `_ledgered(plan)`: `in_place`/`append`, or `plan.prepare["ledger"]` (what prepare reported). Resume
  (`_run_partition`) and finalize (ledger clearing) both use it, so the ledger-behind → `ledger_conflict`
  and ledger-ahead → record and continue rules now hold for key-less Oracle `table` loads. Contract §4.2
  amended ("a `table` load of a key-less Oracle source is ledgered"). Test (failed before: "job did not
  reach a terminal state"): `test_oracle_pipeline.py::test_a_keyless_table_load_resumes_from_its_ledger_
  after_a_lost_checkpoint` — checkpoint #2 raises after its batch committed; the job succeeds with 1,200
  rows written and 1,200 in the target, 0 repeated rows, one resumed partition (evidence
  `resumed_partitions` 1), 0 surviving source e-mails, ledger cleared.

## Review G2 round 2 fixes (Tasks 5-8) — `M3-G2-review-r2.md` B3, B4, B5

- **B3 (a key-less Oracle `upsert` appended on every run) — refused twice.** `validate_run` refuses
  `write_mode: table` + `table_load: upsert` when the snapshot's `shape.key_source` is `rowid`/`none` with
  `no_key` · 422 (placed after the target checks; module docstring and contract §5.3 table amended), and
  `OracleWriter._prepare_sync` refuses the same combination with `PrepareFailure("no_key", …)` before it
  creates, truncates or ledgers anything (defence in depth for a spec that reaches prepare). Contract §4.2
  now says key-less `table` loads are `truncate_insert` only. **Behaviour change:** a PostgreSQL key-less
  `upsert` is also refused at submit now (it already failed `no_key` at prepare, like every key-less PG
  load). Tests (failed before the fix): `test_targets.py::test_a_keyless_upsert_is_refused_at_submit`;
  `test_oracle_writer.py::test_a_keyless_upsert_is_refused_and_writes_nothing` (existing target keeps its 7
  rows, no target created, dry run refused too; `truncate_insert` still prepares);
  `test_oracle_pipeline.py::test_a_keyless_upsert_is_refused_and_truncate_insert_reloads_exactly` (500-row
  `LEADS`: upsert → 422 `no_key`; `truncate_insert` twice → 500 rows each time, 0 duplicated `LEAD_NO`
  groups, 0 surviving source e-mails).
- **B4 (lower-case Oracle `columns` bypassed the submit-time method checks; regression of r1) — one
  projection at submit and run.** `targets._projection` now computes `read_projection(snapshot.shape,
  selection, engine)` (the run's own projection: query select list, `columns` folded per engine, keys);
  a selection that does not parse yields every column (strictest; the selection is refused right after by
  `_bind`). The method rules moved into `targets.check_projected_methods(snapshot, mode, projection)`,
  which `validate_run` calls at submit and `jobs_run._prepare` calls again over the final projection
  (`SpecError` → `PrepareFailure`, same code). This reverses r1's "not changed: `targets._projection`"
  entry. **Behaviour change:** a query that leaves a column out no longer has that column's method
  checked at submit (it is neither read nor written). Tests (failed before the fix):
  `test_targets.py::test_submit_method_checks_cover_what_the_run_reads` (Oracle `columns` `NOTES`/`notes`/
  `Notes` with kept free-text NOTES → `text_method_invalid`; synthesize `full_name` with `det_token` →
  `method_needs_key`; PG query subset with/without `notes`), `…::test_projected_method_checks_are_shared_
  with_prepare`, `test_oracle_pipeline.py::test_lower_case_columns_meet_the_same_method_checks` (all three
  spellings → 422, no target created).
- **B5 (a query alias put a sensitive column under a kept column's name) — refused in `selection.py`, both
  engines.** New `_check_query_outputs`, run by `validate_selection` whenever a shape is given (submit's
  `_bind`, the discovery job, `jobs_run._prepare`, `OracleConnector._compile`, the `runs_as` preview): a
  select-list item whose folded output name is a column of the object must be a plain reference to that
  column (unaliased or aliased to itself; qualified only by the object's own `FROM` alias or unaliased
  table name; unqualified only when the object is the only `FROM` source); `*` needs the object as the sole
  source and `t.*` the object's qualifier. A source counts as the object when it is a plain table with the
  object's name and, if qualified, owner (PG: `public` when the object has none); an unqualified name a
  `WITH` clause defines is the CTE. New code `selection_query_alias` (checklist `identifiers_validated`).
  Contract §5.2 amended (it had codified output names as column identities in r1). **Behaviour change
  (M2 too):** queries such as `SELECT key, email AS channel`, `SELECT key, upper(channel) AS channel`, a
  join that takes the object's column name from another table, `SELECT *` over a join or a derived table,
  and a CTE shadowing the object's name are refused. Tests (failed before the fix):
  `test_selection.py::test_a_query_output_named_like_a_column_must_be_that_column[postgres|oracle]` (15
  refused and 9 allowed shapes per engine; checklist entry; no check without a shape);
  `test_oracle_pipeline.py::test_a_query_alias_cannot_put_a_column_under_a_kept_columns_name` (three
  alias queries → 422, no target created; a discovery job with the alias query fails
  `selection_query_alias`; the plain query writes 400 rows, 0 surviving e-mails, 400/400 CHANNEL kept,
  0 CHANNEL values equal a source e-mail, evidence passed); `test_run_modes.py::test_a_query_alias_cannot_
  put_a_column_under_a_kept_columns_name` (PG: three alias queries → 422, no target created, discovery
  job fails `selection_query_alias`).
  - **Residual (not changed):** an *unqualified* table name in a query is taken to be the object when its
    name matches, although the database resolves it through the PostgreSQL `search_path` or the Oracle
    login's schema and synonyms. A query reading a same-named table of another schema would be read under
    the object's reviewed methods. Qualifying the table (as every test and the console do) avoids it;
    refusing unqualified names would break plain queries. Logged as an open issue.

## Task 9 — MongoDB document rules (`mongo_docs.py`)

- `infer_shape`: `_id` is the range key only when every sampled `_id` has one label the key codec
  turns back into the same BSON value — `int`/`long` (one family), `string`, `objectId`, `date`,
  `uuid` (plan: any one scalar label). A `double`, `Decimal128`, `bool` or `binData` `_id` now reads
  as one partition (`key_source = "none"`): `encode_key` refuses `float`/`bool` kinds, a
  `Decimal128` is not a `decimal.Decimal`, and binary keys decode as subtype-0 `bytes`, which would
  not match a stored `Binary` of another subtype in a range predicate. An empty sample has no key.
- `ColumnInfo.is_key` is set on `$._id` only when the shape is keyed (plan: always), so a key-less
  shape has no column that claims to be a key.
- `coerce_bson` leaves `binData` paths alone (added to the skip labels): a binary leaf is never
  transformed (contract §4.1) and has no text form to restore from.
- `make_bson_safe` returns a converted copy (tuples become lists) and never changes its input; the
  plan's version already did for dicts and lists.
- Tests (`tests/connectors/test_mongo_docs.py`, 9): the plan's 5 plus `top_level` (quoted
  segments), int/long and mixed labels, the unpageable `_id` labels (and that every keyed label
  encodes), and restoration of numeric text in every array element.

## Task 10 — Mongo filter validation and compilation (`selection.py`)

- **Extended-JSON wrappers are checked by `validate_mongo_filter` itself** (plan: the validator accepted
  any wrapper shape and only `compile_mongo_filter` parsed the value). Why: submit, discovery and the
  wizard's checklist call `validate_selection` without compiling, so `{"$oid": {"$where": "1"}}` or an
  unparseable `$date` would pass submit and fail only at run. The check is pure Python (24 hex digits,
  a 64-bit integer, a finite decimal, an ISO date or epoch milliseconds), so the API needs no `bson`;
  `compile_mongo_filter` converts with `bson` as planned. Errors are value-free and raised `from None`.
- **Depth counts nesting levels** (filter documents, `$not`/`$elemMatch` and literal containers), not
  field conditions: eight nested `$and` levels pass, nine are `selection_too_complex` (plan: the field
  condition added a level, so eight already failed; the plan's test uses nine and still passes).
- A `$not` whose argument is a wrapper and an empty `$elemMatch` are `selection_invalid`.
- `_CHECKS` (the wizard checklist) maps the new codes `selection_invalid`, `selection_too_complex` and
  `selection_regex_too_long` to `one_statement` (the filter's structure), so `failed_checks` names an
  entry for every Mongo refusal (`check_for` returned `None` for them).
- The M2 helper `_deny`-style messages: `SelectionError(code, "the MongoDB filter breaks rule <code>")`.
- Contract §5.2: the limits, codes, wrappers and the `params` rule are added after the Mongo allow-list.
- `tests/connectors/test_selection_oracle.py`: the plan's cases pass on M2's code unchanged (no M2 gap);
  the allowed cases carry their `params` (named binds without values are M2's `selection_param_missing`
  only at compile time, but the binds test compiles), plus a case pinning that literals compile only
  after `parameterize_literals`. Mongo tests (`test_selection_mongo.py`, 62): the plan's cases plus more
  denied shapes (`$text`, `$near`, operators inside `$in`/`$eq`/`$all` items and literal arrays,
  `$comment`), malformed wrappers, value-free errors, the node budget, template of wrapped literals.

## Task 11 — `MongoConnector` reads (`mongo.py`)

- **No client-side `timeoutMS`** (plan: `timeoutMS = connector_statement_timeout_seconds·1000`).
  Measured with pymongo 4.18.3: under a client `timeoutMS` an unreachable server fails only after
  `timeoutMS` (3 s with `timeoutMS=3000`), not after `serverSelectionTimeoutMS` (0.5 s), so a lane or
  bank outage would hang every call for the 300 s statement timeout. Now `socketTimeoutMS =
  statement timeout + 5 s` bounds a hung call and every read carries `maxTimeMS` (= the statement
  timeout; property `max_time_ms`) so the server stops the work; `MaxTimeMSExpired` (50) and a
  socket timeout map to `statement_timeout`. The test asserts an unreachable server fails in < 10 s
  and that profiled `find`s carry `maxTimeMS`.
- **`connector_error` mapping** follows the Oracle split (Task 4): server selection / lost
  connection → `connection_failed` retryable; a refused login (18) → `connection_failed` not
  retryable (plan: retryable; a retried password can lock the account); a time limit →
  `statement_timeout`; a bad URI or client option (`ConfigurationError`) → `connection_invalid`;
  `Unauthorized` (13) keeps the caller's code (plan: `connection_failed`). Logged as
  `mongo_call_failed` with `op` and `driver_code` only (Oracle's event shape).
- **Key-less collections are read through one cursor** in `_id` order (plan: the same keyset loop).
  `{_id: {$gt: after}}` matches only documents of `after`'s BSON type, so a keyset over mixed `_id`
  types drops documents as soon as a batch boundary falls between types (the plan's test passed only
  because 3 documents fitted one batch; the new test pages 33 mixed documents with batch 7). The
  cursor is closed when the caller stops. M2's run handler refuses key-less sources (`no_key`), so
  this path serves discovery and `table`-free reads only.
- **`sample_keys`** bounds the set with `$match {_id: {$lt: upper}}` *before* `$sample` and sorts
  the sample in the server (`$sort {_id: 1}`) (plan: Python filter after sampling, unsorted): M2's
  `plan_partitions` needs keys in database order, and the review B2 lesson (quantiles of the bounded
  set) applies here too.
- **`selection.sample` in `count` and `key_at_offset`**: the HMAC sample is a client-side test, so
  both scan the filter's `_id`s (keyset pages of `_id` only, 1,000 per page) and apply it (plan:
  `count_documents` ignored the sample, and `key_at_offset` used `skip`, so the count, the
  `max_rows` bound and the read disagreed). `count` without a sample uses `count_documents(limit=
  max_rows)`.
- `from_secret` raises `connection_invalid` for a URI that does not parse and validates the
  database name (`connection_invalid`), `database_required` when none is given; `engine: Engine =
  "mongo"` (not `Final`, as Oracle: the protocol's attribute is settable). `open()` closes the client
  on failure and tags `hello`/`buildInfo` with the comment too.
- `list_objects` clamps `limit` to 1..5000 and skips views, `system.*`, the ledger, names with `$`
  and names `ObjectRef` refuses (like the SQL engines). `collection_name(obj)` refuses an owner, `$`
  and `system.` names with `object_invalid`.
- A projection drops a path that is under another listed path (MongoDB refuses `profile` together
  with `profile.fullName`: path collision).
- `test()` follows the Oracle/PostgreSQL check shape: every check has a `severity`; `connect`,
  `session_tagged` (advisory), source `select_source` (collection present and `find`) and advisory
  `no_dml_on_source`; targets `create_target` (missing, `table`), `insert_target`/`update_target`,
  `select_target`/`update_target` (in place, collection present), `transactions` and
  `create_ledger` for `in_place`/`append`; `ok` = every required check.
- Small public helpers the writer and later tasks use: `collection_name`, `bson_id`,
  `MongoConnector.client()`, `.db`, `.read_collection(name)`, `.collections()`, `.replica_set`,
  `.database`, `.max_time_ms`. `writer()` arrives with Task 12, `lookup_values()` with Task 15.
- Tests (`tests/connectors/test_mongo_connector.py`, 19): the plan's 14 plus UUID `_id` paging and
  range bounds, `sample_keys` under an upper bound and `key_at_offset` (with a filter and with a
  sample), `from_secret` rules, value-free driver error mapping, and the key tuple of a document.

## Task 12 — `MongoWriter`, the registry branch, the run handler's non-integer append bound

- **The ledger guard is the caller's mark** (contract §4.2, amendment 2026-10-12; plan: `update_one(
  {_id, batch_no: {$lt: n}}, upsert=True)`, which applies a *skipped* batch and treats any lower
  mark as free). In the batch's transaction `_advance` updates `{_id: "<job>:<partition>", batch_no:
  mark.batch_no - 1}`; the first batch upserts on `batch_no: 0`, so a concurrent first batch either
  write-conflicts (the driver's `with_transaction` retries it) or, once the other holder committed,
  hits the duplicate `_id` → `LedgerConflict`. No match → `LedgerConflict` (M2's class, which the
  partition handler stops on quietly; the plan's `ConnectorError("ledger_conflict")` would be
  retried). An empty ledgered batch still records its mark (contract amendment; plan: `return 0`).
  Mongo needs no batch-0 document before locking: document-level write conflicts serialise holders.
  Tests: replayed and skipped batches fenced; two writers on two clients racing batch 1 with
  `asyncio.gather` → exactly one `LedgerConflict`, one holder's values in every document.
  Mutation checks: with the guard removed the replay and concurrency tests fail; with the ledger
  update outside the session the commit-together test fails.
- **Prepare refusals raise M2's `PrepareFailure(code, detail)`** (plan: `ConnectorError(code)`), as
  the Oracle writer does (Task 6): `transactions_unsupported`, `no_key`, `target_missing`,
  `pk_strategy_invalid`; write-time `path_unsupported` is a `PrepareFailure` too (the batch can
  never succeed, so the partition fails instead of retrying).
- **A duplicate key in the rows is M2's `PkCollision`** (`pk_collision`; plan: `ConnectorError(
  "target_error", "mongo:11000")`), like PostgreSQL and Oracle, for append collisions and a unique
  secondary index in a `table` load alike. `BulkWriteError.details` / messages are never read; the
  test asserts the sentinel is absent from `str`, `repr`, `args` and that the context is suppressed.
- **Key rules** (`_check_append`; plan: offset/prefix/token checks only):
  - `generate` needs an ObjectId `_id` **and a target holding only ObjectIds** (new ObjectIds next
    to int ids would make the collection key-less: mixed `_id` types);
  - same-collection `generate`: the bound is the first ObjectId of a second after the newest `_id`
    (`append_upper_key`), and `prepare` returns only once the clock is **one second past** it (plan:
    returns as soon as the second turned), so a worker whose clock lags by up to a second still
    generates ids above the bound; newest ids more than 5 s ahead (a skewed writer) →
    `pk_strategy_invalid`. Residual: a worker clock skewed by more than a second could generate ids
    below the bound (NTP assumed; logged as an open issue);
  - same-collection `prefix` (text `_id`): the prefix must sort above every existing `_id` and is
    itself the bound (`append_upper_key`); otherwise prefixed ids could fall inside the read range and
    be read and appended again (plan: no bound for text ids);
  - `token` refuses ObjectIds (plan) and also a same-collection target (tokens land anywhere in the
    read range).
- **`table` loads replace whole documents** (`ReplaceOne(upsert=True)`, as planned) also for
  `table_load: upsert`; a projected read therefore writes projected documents. PostgreSQL's
  "upsert leaves the other columns of matched rows untouched" is not mirrored: a leaf `$set` upsert
  would turn an array path (`accounts.0.iban`) into an object with a `"0"` field on insert. Open
  question for Tasks 18b/19 (whether the wizard offers `upsert` for Mongo table targets).
- **Write concerns**: `table` loads write with `majority` on a replica set; transactions commit with
  `majority` (`with_transaction(write_concern=…)`); operations inside a transaction use the default
  handle (a transaction carries its own concern). Every write carries the `dps:` comment.
- `prepare` reports `existing_rows` (estimated count) and value-free `checks` (`create_target`,
  `existing_target`, `write_ledger`, `indexes_not_copied`) like PostgreSQL/Oracle; dry runs create
  and delete nothing (test).
- Documents are made BSON-safe on a copy before types are restored (`make_bson_safe` then
  `coerce_bson`), so the transformer's rows are never mutated (plan: `coerce_bson` on a shallow copy
  changed nested documents of the caller's rows).
- `MongoWriter.target_collection()` is public (the commit-together test swaps it).
- **Registry** (`registry.py`): the `mongo` branch (plan: "no registry change" — M2's registry had
  no branch to reuse): lazy import (`engine_unavailable` without the driver), credentials flattened
  by the shared `_flat_secret` (Oracle now uses it too), `client_id = job_id` (the comment), a
  failed `open()` closes the client. **M2 touch point** `tests/test_registry.py`: the `("mongo",
  "engine_unavailable")` row is removed (pinned in `test_registry_m3.py`, as Oracle's was in Task 8).
- **`PIPELINE_ENGINES` stays `{postgres, oracle}`** (Task 8's log said MongoDB joins with Task 12).
  Why: M2's run handler and discovery plan, project and write by *column name*, and the columns of a
  Mongo shape share names (`$.profile.fullName` and `$.profile.dob` are both `profile`); admitting
  Mongo jobs before Tasks 15/19 adapt the pipeline would accept jobs that cannot run correctly.
  `test_pipeline_seams_m3.py`'s docstring records it.
- **M2 touch point** `jobs_run.py`: the plan assumed the run handler bounds same-table appends with
  `append_upper_key`; M2 read only `append_upper`. New `append_read_bound(upper, report, columns)`
  (used by `_prepare`) clips the read to either bound, comparing ObjectIds as hex text (the codec's
  form); M2's integer behaviour is unchanged (`tests/connectors/test_append_bound_m3.py`; M2's
  `test_run_modes.py`, `test_run_failures.py` and the PostgreSQL conformance suite re-run green on the
  lane's `source-pg`). Contract §4.2 `PrepareReport` documents the field.
- Tests (`tests/connectors/test_mongo_writer.py`, 15): the plan's 13 (the duplicate-key case now
  expects `PkCollision`; the commit-together case swaps the target handle instead of patching the
  driver class) plus the concurrent holder, and "targets must exist / dry runs change nothing";
  writes of changed values assert 0 surviving source values (`$in` counts).

## Task 13 — engine bridge on real Oracle and MongoDB rows (`dps/engine/batch.py`)

Red as the plan foresaw for documents, plus one Oracle gap; each fix landed with M2's batch tests
(`tests/test_batch.py`) and the whole suite green.

- **Behaviour 1/2 (documents) were red**: M2's `transform` read and wrote values by column *name*
  (`row.get(col.name)`, `out[col.name] = …`). A Mongo plan `$.profile.fullName` (name `profile`)
  serialised the whole `profile` sub-document to JSON, transformed it, and wrote a *string* over it,
  and `[*]` paths were not expanded. Now a plan whose path is not one key of a flat row
  (`docpaths.top_level`) is read and written at every concrete path `expand_wildcards` finds; the
  engine sees those leaves at their concrete paths (the paths L1 uses for the same document); the
  output row is the input row with each container on a written path shallow-copied once
  (`_set_copied`), so the input is never changed and untouched values leave **by reference** (plan:
  "copied by reference") — `Int64`, `Decimal128`, `ObjectId`, `datetime` and sub-documents keep their
  identity. Flat rows (PostgreSQL, Oracle) take exactly the old path (`doc[name]`, `out[name]`).
  Verification and `values_by_column` key document values by concrete path (the column *path* for
  the counters), so two elements of one array are checked as two values. A plan naming a document
  path the shape does not have is a `ValueError` (like a plan naming an unknown column).
- **Behaviour 3 was red** (`unclassified` was stored but unused). Now, for leaves whose generalised
  path the shape does not list (top-level keys that are flat shape columns are never searched, so SQL
  rows are untouched): `detect` runs the detector over string leaves and plans the spans with
  `det_token` (obfuscate) or `build_plan` (synthesize), like a free-text column; `redact` replaces
  every such scalar (not booleans, ObjectIds or binary) with the `redact` label (`[REDACTED]`) as a
  whole-value entry of entity `UNCLASSIFIED`; `keep` leaves them. Leaves the shape lists are the
  reviewed snapshot's decision and are never re-detected (a kept `notes` stays). The detector is
  created lazily when the first unclassified leaf needs it.
- **Oracle gap (new): `ColumnPlan.ts_precision`.** `ColumnPlan.output_column()` had no fractional
  precision, so the codec took a TIMESTAMP(9) output for microseconds: a `date_shift` on an Oracle
  `TIMESTAMP(9)` column failed every batch with `incompatible_method` (the reversible round-trip
  check saw the nanoseconds go). `ColumnPlan` gains `ts_precision: int | None = None` (contract §6.4,
  additive), `output_column()` passes it, and **M2 touch point** `targets.build_column_plans` copies
  it from the shape (`test_targets.py::test_column_plans_carry_the_timestamp_precision`;
  `tests/test_batch_documents_m3.py::test_a_timestamp9_date_shift_keeps_its_nanoseconds` shows the
  refusal without it). Task 7 had pinned the method and the codec, not the bridge.
- `tests/connectors/test_engine_bridge_m3.py` (6): per-test markers (`oracle`, `mongo`, both for the
  cross-engine case) instead of all three on the module, so each runs with its own container. The
  untouched-values cases also transform one column and check every *other* column/leaf keeps value and
  type (the plan's empty-plan case passed trivially); the CHAR case uses letter values (`fpe_text`
  falls back to `det_token` on digit runs too short for FF1 — `fpe_domain_too_small` — which a
  `CHAR(10)` cannot hold) and checks 0 surviving values after the in-place write; FF9 values survive
  the write. The cross-engine case: `fpe_digits` digits equal as integers (Oracle `NUMBER` vs Mongo
  `"CUST-…"`), `fpe_text` names and `email` (`domain: keep`) byte-identical, a second key differs.
- `tests/test_batch_documents_m3.py` (8, offline): nested leaf only, identity of untouched values,
  `[*]` over 3/0/absent arrays, a flat document field, unclassified detect/keep/redact (and an unknown
  field inside an array element), unknown plan paths refused, leakage in an array element fails
  closed, and the TIMESTAMP(9) case.

## Task 14 — the conformance suite on Oracle and MongoDB

- **Harness interface as built** (plan: `create/fetch/ledger_rows/drop/connector` and an engine
  registration list in `conformance.py`). M2 built the suite as a `ConformanceEnv` protocol
  (`engine`, `source`, `rw`, `make_table`, `insert`, `read_all`, `target`, `rejected_selections`) bound
  per engine by a test module that subclasses `ConnectorConformance` and provides an `env` fixture
  (`test_conformance_postgres.py`). M3 follows that: `harness_oracle.OracleEnv` +
  `test_conformance_oracle.py`, `harness_mongo.MongoEnv` + `test_conformance_mongo.py`; there is no
  registration list to edit.
- **The suite gained defaulted engine hooks; no assertion changed** (plan: "the suite's tests are not
  edited", with a `composite_keys` capability flag the plan assumed the suite had). The suite as built
  hard-coded PostgreSQL's names (`key == ["id"]`, `r["name"]`, `{"id": 1005, …}`), a key-less
  `key_source == "none"` and a SQL `where` in the log test, which no Oracle (upper-case names, `ROWID`
  paging) or MongoDB (`_id`, filters) binding can satisfy. `conformance.py` now reads optional env
  members with PostgreSQL defaults — `names` (logical → physical column), `keyless` (Oracle
  `(["ROWID"], "rowid")`), `composite_keys` (MongoDB: `False` → the composite paging case skips with
  a reason and the partition-coverage case uses the text key; the describe case checks the composite
  key only when it exists) and `equals_selection` (MongoDB: `{"name": value}`) — so the PostgreSQL
  binding is unchanged and every assertion is the same for all three engines. One assertion was
  *added*: the log test's bound equality count must be exactly 1 (it was not checked). Contract §12.3
  records the members.
- Oracle binding: tables in the session's test owner created and read back by SYSTEM (session
  `NLS_SORT/NLS_COMP = BINARY` so `read_all` orders like the connector), `SELECT` granted to the
  read-only login per table (the source connector reads as it, the writer writes as the owner),
  quoted upper-case names, every table dropped before and after each test (`drop_all`).
- MongoDB binding: one database per test; `id`/`code` map to `_id`; a key-less collection has
  document `_id`s; rejected selections are `$where`, `$function`, SQL text (engine mismatch), a bad
  field name and an empty `$and`.
- Run on the lane's containers: `tests/connectors/test_conformance_{postgres,oracle,mongo}.py` —
  29 passed, 1 skipped (MongoDB composite paging, by its capability flag); with
  `DPS_TEST_REQUIRE_SERVICES=1 pytest -m "oracle or mongo"`: 132 passed, 1 skipped.

## Review G3 round 1 fixes (Tasks 9-14) — `M3-G3-review-r1.md` B1, B2, B3

- **B1 (the Mongo writer retyped untouched values) — fixed by restoring types only where the engine
  wrote, from the original leaf (the review's first option).** `MongoWriter._documents` no longer runs
  `coerce_bson` (removed with its tests): it only makes a copy BSON-safe (`make_bson_safe`:
  `decimal.Decimal` → `Decimal128`, `datetime.date` → `datetime`, which pymongo cannot encode and never
  decodes), so every value no plan covers reaches the target as the driver decoded it. Restoration moved
  into `BatchTransformer`, which holds each written leaf's input value: new additive constructor argument
  `leaf_restore: Callable[[Any, Any], Any] | None = None` (contract §6.4 amended), called once per value a
  plan *wrote* with `(original, codec value)`; MongoDB passes `mongo_docs.restore_leaf`, which returns the
  value in the original leaf's BSON type (`Int64` stays `Int64`, int32 stays int32 unless it needs 64
  bits, `Decimal128`, double, date, UUID) and returns a value whose type already matches unchanged. With a
  hook, a leaf that held **text** keeps the engine's text instead of the plan column's codec (an
  out-of-sample `"0042"` in a field sampled as `long` is not turned into `42`). `jobs_run.leaf_restorer(
  engine)` picks the hook (`None` for PostgreSQL/Oracle: SQL rows unchanged) and `_transformer` now takes
  the source `engine` (both call sites pass `source.engine`; MongoDB is still outside `PIPELINE_ENGINES`
  until T15/T19, the wiring is in place so it cannot be forgotten).
  - Test change: `test_mongo_writer.py::_mask` hands the writer `Int64` risk scores (what the transformer
    now emits) instead of plain ints, since the writer no longer converts them; the restoration it used to
    prove is proven end to end in `test_mongo_write_fidelity.py::test_planned_leaves_keep_their_original_
    bson_type` (transformer + writer: 21/21 written `acct` leaves keep int32 or Int64 as in the source,
    0 equal to the source).
  - Tests (failed before the fix): `test_mongo_write_fidelity.py::test_untouched_leaves_keep_their_raw_
    bson_on_full_documents[table|in_place]` — 40 sampled documents (int32/Int64 mixed `score`/`acct`,
    nested int/date/ObjectId/UUID, arrays with `Decimal128`/int/double) plus one drift document written
    after discovery with text in typed fields, read **unprojected**, `$.name` transformed, written whole:
    every other top-level field compared as raw BSON element bytes, **0 changed** (was 44 in both modes),
    0 surviving names; `test_planned_leaves_keep_their_original_bson_type` (was 10/21 same type);
    offline `test_batch_documents_m3.py::test_leaf_restore_sees_only_written_leaves_and_text_leaves_stay_
    text`, `test_mongo_docs.py::test_restore_leaf_*`, `test_pipeline_seams_m3.py::test_the_run_
    transformer_restores_leaf_types_by_source_engine`. Reviewer probe `probe_untouched.py`: 24 → 0
    retyped leaves in `table` and `in_place`; drift values stay strings.
- **B2 (a minority `_id` type the sample missed was dropped from range reads) — fixed exactly at
  describe, plus a fail-closed read guard.** `MongoConnector.describe` reads the smallest and largest
  `_id` (`find({}, {_id: 1}).sort(_id, ±1).limit(1)`, two `_id` index lookups) and passes them to
  `infer_shape(…, id_bounds=…)`; the collection is keyed only when the sample *and* both bounds share one
  range-pageable family (`mongo_docs.id_label`: int32/int64 one family). BSON sorts by type bracket
  first, so both ends in the bracket ⇒ every `_id` in it; otherwise the shape is key-less (`none`) and
  M2's run handler refuses it with `no_key`, as the plan intends. The run's `prepare` re-runs `describe`
  (M2 `_prepare`), so the check is repeated before every run. Inside a bracket a foreign type (a double
  among ints; a non-UUID binary sorts outside the UUIDs and is caught by the bounds) is not dropped by
  ranges but its key would not round-trip through the codec: a keyed `read_partition` now raises
  `ConnectorError("key_type_mixed", …)` (value-free) when a page holds an `_id` of another family.
  Contract §4.2 amended. Tests (failed before): `test_mongo_write_fidelity.py::test_a_foreign_id_type_the_
  sample_missed_makes_the_collection_keyless` (20,000 ObjectIds + an int and a string `_id`, sample 10:
  was keyed; now key-less 3/3, count 20,002 = read 20,002), `…::test_a_foreign_id_inside_the_key_bracket_
  fails_the_read_closed` (no error before; now `key_type_mixed`, value absent from the message), and the
  control `…::test_a_uniform_id_collection_stays_keyed_and_reads_every_partition` (3,000 over 4
  partitions); offline `test_mongo_docs.py::test_id_bounds_of_another_type_make_the_shape_keyless`.
  Reviewer probe `probe_idtypes.py`: keyed 5/5 → 0/5.
- **B3 (a projected `table` upsert wiped matched documents' other fields) — fixed with `$set`
  upserts (the review's first option).** `table_load: upsert` now writes `UpdateOne({_id}, {$set:
  mongo_docs.upsert_fields(doc)}, upsert=True)`: every field except `_id` at its dotted path **through
  sub-documents** (so a `profile.fullName` projection updates that leaf and keeps `profile.dob`), arrays,
  scalars and empty sub-documents set whole (array elements cannot be merged by position — the reason the
  Task 12 log gave for not doing a leaf `$set`), a field name with `.`/leading `$` → `PrepareFailure(
  "path_unsupported")`. `truncate_insert` and created targets keep `ReplaceOne(upsert=True)` (the target
  is empty, a replay stays idempotent). The Task 12 "open question" is closed; contract §4.2/§5.2
  amended (a projected array is overwritten whole on matched documents). Test (failed before: `KeyError:
  'segment'`): `test_mongo_write_fidelity.py::test_a_projected_upsert_keeps_the_other_fields_of_matched_
  documents` — 12 source documents, 10 already in a wider target with a target-only field: 10/10 matched
  keep `segment`, `accounts`, `profile.dob` and the target-only field, 0 surviving source names, the 2
  inserted documents hold the projection only; offline `test_upsert_fields_set_through_sub_documents_
  only`. Reviewer probe `probe_upsert.py`: 10/10 → 0/10 documents lost fields.
- Open (not in this round's findings): a **key-less** collection counted with `selection.sample` still
  pages `_id`s with `$gt` (`_scan_ids`), which skips documents of other BSON types; M2's run handler
  refuses key-less sources, so only a discovery count is affected (review N-notes otherwise unchanged).

## Review G3 round 2 fixes (Tasks 9-14) — `M3-G3-review-r2.md` R2-B1, R2-B2

- **R2-B1 (a projected `in_place` write through an array landed on the wrong element and left a source
  value in clear) — fixed by reading top-level fields (the review's first option, applied to every
  mode).** `mongo._projection` now projects the **top-level field** of each `columns` entry
  (`accounts.iban` → `accounts`, `_id` always). A dotted MongoDB inclusion projection drops the
  non-document elements of an array it crosses, so positions read were not the stored positions; with
  whole top-level fields every positional leaf path `MongoWriter._in_place_ops` `$set`s is the stored one,
  and untouched leaves are rewritten with identical BSON (already proven, r1 B1). Applying it to every
  mode (not only `in_place`) keeps the connector stateless (a read does not know its write mode) and
  makes the read equal `selection.read_projection`, which already folded MongoDB `columns` by top-level
  name (contract §5.2: every connector reads exactly it); `read_projection` now folds a dotted MongoDB
  `columns` entry to its top-level field (`_fold_column`), so the plan covers every leaf of the field
  read (closes review R2-N1's "dotted columns plan nothing"), and `jobs_run._prepare` de-duplicates the
  projection names (one name per top-level field; SQL unchanged). **Behaviour change:** a dotted
  `columns` entry no longer narrows *within* a top-level field — `profile.fullName` reads, plans and
  writes all of `profile` (every leaf of it is planned, so nothing unreviewed passes in clear). This is
  also what closes the sparse-sub-document read (`profile: {}` for a document without `fullName`).
  Contract §4.2/§5.2 amended. Tests: `test_mongo_write_fidelity.py::test_a_projected_in_place_write_
  keeps_array_positions` (failed before with `target_error: mongo:28` — the shifted `$set` path ran into a
  scalar element; the accounts-only shape leaked silently in the reviewer's probe) — 20 documents with
  `accounts: [{iban}, "note", {bal}, {iban}]` and `matrix: [[{iban}, "x"], "y", [7, {iban}]]`,
  `columns=["accounts.iban", "matrix.iban"]`, `det_token` on `$.accounts[*].iban` and
  `$.matrix[*][*].iban`: 0/80 source IBANs survive, 0/20 documents change structure (element counts,
  element kinds, keys and every non-IBAN scalar), 4 tokens per document; offline
  `test_selection.py::test_read_projection_folds_mongo_paths_to_top_level_fields` (HEAD returned
  `['$._id']` only). Updated to the new rule (no assertion of masking or kept fields removed):
  `test_mongo_connector.py::test_projection_reads_the_top_level_fields_of_selected_paths` (was
  `…_reads_selected_paths`, expected `profile == {fullName}`), `test_mongo_writer.py::test_in_place_sets_
  leaves_only` (the read holds `profile.dob` too; the masked row keeps it), and the r1 B3 fidelity test
  (inserted documents hold `profile.fullName` and `profile.dob`).
- **R2-B2 (a projected upsert `$set` an empty sub-document over a matched document) — fixed with
  `$setOnInsert`.** New `mongo_docs.upsert_update(doc)` returns the whole update document: `$set` through
  sub-documents as before, but an **empty** sub-document goes to `$setOnInsert` (omitted when there is
  none), so a matched document keeps its sub-document and an inserted one still gets the source's `{}`.
  `upsert_fields` stays (the `$set` part, empty sub-documents no longer in it); `MongoWriter._table_ops`
  sends `upsert_update`. Paths in `$set` and `$setOnInsert` are disjoint leaves, so they never conflict.
  Contract §4.2 amended. Tests: `test_mongo_write_fidelity.py::test_a_projected_upsert_keeps_matched_
  fields_a_sparse_row_lacks` (failed before: `KeyError: 'fullName'`) — a sparse source
  `{profile: {dob}}` and an empty `{profile: {}}` against targets holding `fullName`, `dob` and a nested
  sub-document, `columns=["name", "profile.fullName"]`: the sparse match keeps `fullName` and the nested
  field, the empty match keeps its whole `profile`, the inserted document holds `profile: {}`; offline
  `test_mongo_docs.py::test_upsert_update_never_sets_an_empty_sub_document_over_a_match` and the updated
  `test_upsert_fields_set_through_sub_documents_only`.
- Not changed (non-blocking notes): R2-N2 (a matched document's array is overwritten whole by a `table`
  upsert, documented), R2-N3 (`InvalidBSON` for out-of-range dates not mapped to `source_error`), R2-N4
  (engine date span), R2-N5 (fail-closed `fpe_digits` fallback) stay open for T15/T19 and the engine
  owners.
