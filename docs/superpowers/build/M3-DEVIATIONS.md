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
