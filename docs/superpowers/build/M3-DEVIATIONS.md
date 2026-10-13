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
