# M2 build — deviations from the plan

Plan: `docs/superpowers/plans/2026-10-11-m2-jobs-and-postgres.md`. Contract:
`docs/superpowers/plans/2026-10-11-m1-m5-contract.md` (incl. the 2026-10-12 lane-safe amendment).
One section per task: file, what changed, why. Contract names are preserved unless a section says
otherwise.

## Task 1 — Dependencies, markers, M2 settings and posture

- `pyproject.toml`: the `db`, `pg_source` and `s3` marker descriptions name the port variables
  (`127.0.0.1:${DPS_DB_PORT:-5441}` …) instead of literal host ports. Why: contract §0 amendment
  2026-10-12 (lane-safe compose) supersedes literal host ports anywhere in a plan. The `dev` extra
  is written one entry per line (formatting only).
- `.env.example`: **not modified.** The session's permission rules deny every read and write of
  `.env*` files in this worktree (`cat`, `Read` and `tail` on `.env.example` were refused), so the
  plan's commented M2 block could not be appended safely. The same variables (with the KEK
  generation command and the compose role names) are documented in `HANDOFF.md` §4 "M2 settings"
  instead. Open issue: append the plan's block to `.env.example` from a session that may edit it.
- `HANDOFF.md`: §4 gains the M2 settings table, §6 the M2 posture checks and §7 the M2 secret names.
  Why: contract §0.6 (HANDOFF changes in the same commit as any config change); the plan leaves the
  full pass to Task 38.
- `.dockerignore`: `var` added beside `data`. Why: `var/` holds local artefacts, exports and secret
  directories (`DPS_SECRET_DIR=./var/secrets`); keeping it out of the build context means a local
  secret can never reach an image layer. Same reason as the plan's `.gitignore` entry.
- `dps/config.py`: the allowed values are module constants (`MIGRATIONS_MODES`, `ARTEFACT_STORES`)
  and the KEK error text is one constant; behaviour, names and messages are the plan's.
- `tests/test_config_m2.py`, `tests/test_posture_m2.py`: `test_kek_from_file` and
  `test_bad_m2_production_is_refused` clear `DPS_JOB_KEK` first (`monkeypatch.delenv`), so a KEK
  exported in the developer's shell cannot flip their result; lines are wrapped to 100 characters.
  Assertions unchanged.

## Task 2 — Ids and sealing

- `dps/secrets.py`: `seal` refuses a KEK that is not 32 bytes (`ValueError("the job KEK must be 32
  bytes")`, no detail). Why: `AESGCM` also accepts 16- and 24-byte keys, so a short key would have
  sealed silently with AES-128/192 while `open_sealed` (which already checks 32 bytes) could never
  open it. Test added: `test_seal_refuses_a_kek_that_is_not_32_bytes` (17 tests, plan: 16).
- `dps/secrets.py`: `open_json` uses PEP 695 type parameters (`def open_json[M: BaseModel](…)`) instead of a
  module-level `TypeVar` (ruff UP047 under `target-version = "py312"`); same signature for callers.
- `dps/secrets.py`: `SECRET_FILES` is documented and laid out one name per line; slices formatted by
  `ruff format`. Behaviour as planned.

## Task 3 — Metadata DB service, roles, scratch clusters, service fixtures

- `docker-compose.yml`: `dps-db` publishes `127.0.0.1:${DPS_DB_PORT:-5441}:5432` (plan: literal
  `5441`). Why: contract §0 amendment 2026-10-12. The `app` mapping was already lane-safe (M0 fix).
- `docker-compose.yml`: the `dps-db` healthcheck is `pg_isready -h 127.0.0.1 -U postgres -d dps`
  (plan/contract table: without `-h`). Why: the postgres image runs `docker-entrypoint-initdb.d`
  against a temporary server that listens on the unix socket only; a socket probe reports healthy
  before `01_roles.sql` has created the roles and the `dps` database, a TCP probe does not.
  Same command and same intent, closes a first-boot race.
- `tools/scratch_pg.sh`: ports come from `${DPS_DB_PORT:-5441}` / `${DPS_SOURCE_PG_PORT:-5442}` and
  each cluster's data directory is `$BASE/<name>-<port>` (plan: literal ports, `$BASE/<name>`). Why:
  lane-safe amendment; two lanes on one host would otherwise share (and re-init) one data
  directory. `psql` gets `-d postgres` explicitly; an unknown cluster name exits 2; `shellcheck`
  clean. Verified on private ports (55441) with `tests/db/test_service.py` green, then stopped and
  removed.
- `tests/fixtures/services.py`: `SERVICES` entries carry `(port variable, contract port)` and a
  `{port}` template; `service_url` returns `DPS_TEST_*` when set (non-empty), else builds the
  default from the `DPS_*_PORT` variable, else the contract port (plan: literal defaults). Why:
  contract §12.2 + amendment. The skip/fail message names the service and start command, never the
  URL (it may carry a password). New unit test `tests/test_service_urls.py` (8 tests) pins the
  precedence; contract ports are asserted only with both variables unset.
- `HANDOFF.md` §3: services table with `dps-db`, the scratch-cluster command and the `db` test
  variables; §4 compose-only variables gain `DPS_DB_PORT`.

## Task 4 — Migrations 001–009 and the runner

- `dps/migrate.py`: driver failures (`asyncpg.PostgresError`, `asyncpg.InterfaceError`, `OSError`)
  are caught and printed as `migrations: failed (<ExceptionClass> sqlstate=<code>)`, exit 1. Why:
  the plan's docstring promises exit 1 for a failed migration and output limited to file names and
  counts, but its `_run` only caught `MigrationDrift`, so a wrong password or a failing statement
  ended in a Python traceback (source lines plus the server message). New test
  `test_migrate_cli_failures_exit_cleanly_without_secrets`: a wrong migration password exits 1 with
  neither the password nor a traceback in the output; an empty `DPS_PG_HOST` exits 2.
- `tests/db/test_migrations.py`: the CLI tests run the subprocess with `cwd=tmp_path`, so a `.env`
  in the working directory cannot change the settings the CLI reads (the lane's `.env` sits in the
  worktree root). `test_fresh_database_applies_every_file_once` also pins
  `set(db.GLOBAL_TABLES)` to the four contract names. 9 tests (plan: 8).
- `tests/fixtures/db.py`: the role bootstrap creates the NOLOGIN groups `NOSUPERUSER NOBYPASSRLS`
  (as `01_roles.sql` does) and runs the two `GRANT`s after the `DO` block; the `queue` fixture is
  annotated `ModuleType`; docstrings added. Same roles, memberships and fixtures as planned.
- `dps/db.py`: as planned, plus docstrings and a `lock_key` local. `dps/migrations/009_grants.sql`
  carries the plan's note about the ledger in its header comment.
- `HANDOFF.md` §3: the migration CLI (apply, `--verify`, `--maintain`, exit codes, fix-forward rule).
- Verified beyond the tests: against the lane's compose `dps` database (roles from
  `01_roles.sql`), `--verify` exits 1 naming all nine files pending, the plain run applies 9, then
  `--verify` and `--maintain` exit 0.

## Task 5 — RLS posture and tenant-isolation tests

- `tests/db/test_rls.py` — **plan assertions that contradict the contract's grants, corrected.**
  Contract §2.4 / `009_grants.sql` give `dps_app_rw` only `SELECT, INSERT` on `dps_audit_log` and
  `SELECT, INSERT, UPDATE` on `dps_evidence` (no DELETE on either; checked with
  `has_table_privilege` against the migrated compose database). So:
  - `test_each_tenant_table_is_isolated`: the plan skipped the cross-tenant `DELETE` only for
    `dps_audit_log`; for `dps_evidence` it would raise `InsufficientPrivilegeError`, not return 0.
    Both append-only tables now assert that the API role's `DELETE` is refused outright
    (`InsufficientPrivilegeError`); the other six still assert 0 rows deleted. The test also
    re-reads as `t1` to show t2's attempts left t1's row in place.
  - `test_audit_log_and_evidence_are_append_only`: the plan expected the trigger's `RaiseError` for
    the API role's `UPDATE`/`DELETE` on `dps_audit_log`, but the privilege check fires first
    (`InsufficientPrivilegeError`). The test now proves both layers: the API role has no
    `UPDATE`/`DELETE` privilege, and the owner (`dps_owner`, every privilege, `app.tenant_id`
    bound because FORCE RLS applies to the owner too) is refused by the append-only trigger and
    the row survives. The `dps_evidence` assertions are the plan's. No grant was widened.
- `dps/db.py` `evaluate_rls_posture`: new keyword `worker_member: bool = False` (additive; existing
  callers unaffected) → problem "runtime role is a member of dps_worker: worker_claim opens the
  queue tables". `assert_rls_posture` observes it with `pg_has_role(current_user, 'dps_worker',
  'MEMBER')`. Why: contract §2.3 "dps_app is never a member of dps_worker"; an API pool configured
  with the worker login (an easy env copy-paste) would otherwise pass the posture while every
  tenant's jobs became visible to API requests.
- `dps/db.py` `assert_rls_posture`: `predicate_ok` requires `app.tenant_id` in the policy's
  `WITH CHECK` as well as its `USING` (absent `WITH CHECK` = `USING` applies), and a partition leak
  is any of `SELECT/INSERT/UPDATE/DELETE` (plan: `USING` and `SELECT` only). Why: a
  `tenant_isolation` policy with `WITH CHECK (true)` lets a tenant write rows for another tenant,
  and a direct `INSERT` grant on a partition bypasses the parent's RLS just as `SELECT` does.
  Stricter, never weaker.
- `tests/test_rls_posture_unit.py`: asserts `worker_member=True` is named (2 tests, as planned).
- `tests/db/test_rls.py`: new `test_posture_names_each_tampered_fact` (fresh database, API pool
  logged in as `dps_worker_login`): an open `USING`, an open `WITH CHECK`, `NO FORCE`, an extra
  policy and a direct partition grant are each named. Role memberships are never altered (they are
  cluster-wide). 14 db tests (plan: 13); with the unit file 16 (plan: 15).

## Task 6 — Connector base types and codecs

- `dps/layers/connectors/base.py`: every model sets `hide_input_in_errors=True` (M0's `_HIDDEN`
  pattern; `ObjectRef` keeps `frozen=True` as well). Why: `KeyRange`, `LedgerMark` and
  `PrepareReport` carry encoded keys, which are customer data; pydantic's default error text quotes
  the offending input (`input_value=…`), so a malformed key range would have echoed a key into an
  exception message. New test `test_key_bearing_models_never_echo_keys_in_errors` (verified to fail
  without the setting).
- `encode_key` refuses a `None` key value with `UnsupportedKey("key columns cannot be NULL")`. Why:
  the plan's code would have encoded it as the text `"None"`, which `decode_key` cannot read back
  (an `int("None")` error at resume time). Key columns are NOT NULL by construction (PK or unique
  NOT NULL index), so this only turns a late, confusing failure into an early, value-free one.
  Byte keys accept `bytearray`/`memoryview` as well as `bytes` (same hex text).
- `from_engine_text` for `decimal`: the quantize step runs in a `decimal.Context` whose precision
  covers the declared column precision (`max(28, precision + 2)`), and a failing quantize raises
  `IncompatibleValue(kind, "length_overflow")`. Why: the plan quantized in the default context
  (28 digits), so a value that fits a `numeric(60,2)` column raised a bare
  `decimal.InvalidOperation`. Covered by `test_null_key_and_huge_decimal_fail_without_values`.
- `ConnectorError` also stores `.detail` (contract §4.1 names `.code .detail .retryable`; the
  plan's code stored only `.code` and `.retryable`). `decode_key`'s unknown-tag error no longer
  names the tag (it comes from a stored row). `Key = tuple[Any, ...]` is exported (contract §4.1).
- Tests: the plan's 15 plus the two above (17).

## Task 7 — Job, connection and snapshot models and the connector protocols

- `tests/test_jobs_models.py` — **plan assertion that could never pass, corrected.**
  `test_connection_spec_rules` asserted `"p" not in repr(inline)` with the password `"p"`, but the
  repr legitimately contains the letter `p` (`dev-pg`, `postgres`, `params`, `source-pg`). The test
  now uses a distinctive password and asserts that string is absent from the repr — the intent
  (inline credentials never appear in a repr). Other plan assertions unchanged.
- `dps/jobs_models.py`: every model sets `hide_input_in_errors=True`. Why: `Selection.params`
  holds bound customer values, `InlineCredentials` a password and `ConsistencySpec` caller key
  material; pydantic's default error text quotes the offending input. New test
  `test_spec_validation_errors_never_echo_values` (a card number in a bad bind value, an integer
  password) — both values were echoed before the setting.
- `dps/jobs_models.py` / `base.py`: field defaults are written `Field(default=…, …)` instead of
  `Field(None, …)`, and `ConnectionSpec.roles` uses a typed factory (`_source_only`) instead of a
  lambda. Why: mypy (no pydantic plugin) reads pydantic's `dataclass_transform` and only honours
  the `default=` keyword, so `Selection()` (used by `DiscoverSpec`'s validator and as a
  `default_factory`) failed `mypy dps` with "missing named argument". Same defaults at runtime.
  New alias `ConnRole = Literal["source", "target"]`.
- `ConnectionSpec` rejects an empty `roles` set ("a connection needs at least one role"). Why:
  migration 003 has `CHECK (cardinality(roles) > 0)`; the plan's model accepted `roles=set()` and
  the insert would have failed later with a database error.
- `dps/layers/connectors/base.py` `Connector.sample_keys` takes `*, upper: tuple[Any, ...] | None =
  None` (contract §4.2: "only keys < upper (the global read bound)"). The plan's interface block
  omitted the keyword; the contract is binding. Protocol methods carry docstrings stating the §4.2
  rules (read-only sources, keyset order, one transaction per `write_batch` including the ledger
  mark), as Step 4 asks. `ResolvedTarget` list/dict defaults use `default_factory`.
- Tests added: `test_file_stores_are_target_only` (the plan's s3/`source` case used the bucket
  `"b"`, which already fails the bucket pattern, so the role rule itself was untested),
  `test_spec_validation_errors_never_echo_values`, `test_resolved_target_defaults`. 9 tests
  (plan: 6).

## Task 8 — Queue, part 1 (job rows, claims, leases, workers registry)

- `tests/db/test_jobs_queue.py` — **plan assertion that could never pass, corrected.**
  `test_create_get_list_with_cursor_and_filters` listed four jobs in pages of two and then asked
  for a third page with `cursor=cursor2`; but the second page is the last one, so `cursor2` is
  `None` and the "third page" call restarted from the newest job (six ids against four). The test
  now asserts `cursor is not None and cursor2 is None` and that the two pages hold all four jobs
  newest first — the intent (keyset paging covers everything once and then stops).
- `tests/db/test_jobs_queue.py`: `test_fencing_after_lease_expiry` calls `jobs.reap` (Task 9), so
  it is committed with Task 9 (the plan already lists this file as "append" there) instead of
  being deselected here — every commit runs a green `pytest -q`.
- `dps/jobs.py` `claim_jobs`: the inner locking select joins the candidate CTE and takes rows
  `ORDER BY` the candidate's FIFO ordinal (`FOR UPDATE OF q SKIP LOCKED LIMIT batch`). Why: the
  plan's `WHERE id IN (SELECT id FROM eligible) … FOR UPDATE SKIP LOCKED LIMIT batch` has no
  `ORDER BY`, so when the eligible window (≥ 50 rows) is larger than the batch, which rows are
  locked is up to the plan chosen by Postgres, not FIFO (contract §3.2 "FIFO by (priority,
  run_after, created_at, id)"). New regression test
  `test_claims_are_fifo_when_the_batch_is_smaller_than_the_eligible_set` (it passes with either
  form on today's planner; the `ORDER BY` makes the order a guarantee rather than an accident).
- `create_job` looks the idempotency key up **before** the queue cap check, so a replay of an
  accepted request returns the original job instead of `queue_full` (the unique-violation path
  is kept for concurrent duplicates). New test `test_idempotent_replay_is_not_refused_by_a_full_queue`.
  A job that is not visible after insert raises `RuntimeError` (value-free) instead of `assert`.
- `QueueFull` / `KeyMaterialRequired` carry `status` (429 / 409) beside `code`, and
  `JobStateError` keeps `.detail` (the API mapping in Task 24 uses both); additive.
- `advance_job` truncates `error` to 500 characters like `retry_job` does; `retry_job` sends a
  `pg_notify` when it requeues (workers otherwise wait for the poll interval).
- `dps/observability.py`: as planned (24 safe fields, six metrics). `tests/test_observability.py`
  gains `test_fields_the_m2_queue_logs_are_kept` (the module asks for the test to be kept in step).
- New test `test_public_job_never_carries_sealed_blobs`. 15 tests in this commit (plan: 12 +
  the deferred fencing test).

## Task 9 — Queue, part 2 (partitions, finalize transition, reaper, controls, stats)

- `dps/jobs.py` `claim_partitions` — **plan SQL starved newer jobs under the tenant cap, fixed.**
  The plan ranked every queued partition of a tenant (`rn_tenant`) before applying each job's
  `parallelism`, so an older job's queued backlog occupied all of the tenant's ranks: with
  `max_running = 3`, an older job at `parallelism = 1` with five queued partitions and a newer job
  at `parallelism = 2`, one claim started 1 partition (the older job's) and the newer job got
  none, although two tenant slots were free. The query now filters each job's startable
  partitions first (`job_ok`: `rn_job <= parallelism - running`) and ranks only those per tenant
  (contract §3.2 caps unchanged: per tenant running ≤ `max_running`, per job ≤ `parallelism`).
  New test `test_tenant_cap_counts_only_partitions_a_job_may_run` (fails on the plan's SQL with
  `{newer: 0}`, verified; passes now with 1 + 2). The inner locking select takes rows in FIFO
  order, as in Task 8.
- `tests/db/test_jobs_queue.py`: `test_fencing_after_lease_expiry` (from Task 8's listing) lands
  here, with `reap`.
- `finish_partition`, `retry_partition` (dead) and `reap` (dead) increment
  `dps_partitions_finished_total{state}` (the metric Task 8 defines but the plan never
  incremented). `JobStateError` carries `.detail`. `tenant_stats` keeps `oldest_waiting_s = 0.0`
  instead of turning it into `None` (the plan's truthiness test dropped a zero age).
- Docstrings state the lock order (job row first) and why the reaper's separate transactions
  cannot deadlock a finisher; behaviour otherwise as planned.
- New tests: `test_controls_refuse_states_they_do_not_apply_to` (pause/resume/retry codes and
  409 status on a discover job, immediate cancel of a non-run job with key purge and expiry,
  `job_terminal`, another tenant gets `None`) and
  `test_canceling_a_paused_job_finalizes_and_releases_held_partitions` (the reaper's
  `partitions_released` path and the cancel-finalize claim). 13 partition tests + 16 job-row
  tests = 29 (plan: 10 + 13 = 23).
- Concurrency-sensitive tests (`exactly_once`, concurrent claimers, FIFO, caps) were run five
  times in a row: green every time.
