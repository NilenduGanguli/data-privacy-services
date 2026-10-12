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

## Review G2 r1 fixes (Tasks 6–9)

- **B1, `checkpoint_fp` was reversible.** `key_fingerprint` was the first 8 hex of an unkeyed
  SHA-256 of the encoded key, so enumerating a small key space (integer ids) recovered the key
  from `PartitionView.checkpoint_fp`. It is now keyed:
  `key_fingerprint(encoded, *, kek, job_id)` = first 8 hex of
  `HMAC-SHA256(HKDF-SHA256(kek, salt=job id, info="dps.checkpoint_fp"), canonical JSON)`. The
  HMAC and HKDF live in the new `dps.secrets.keyed_fingerprint` (contract §0.3: only
  `secrets.py`/`crypto.py` handle key bytes; the subkey never leaves it). `list_partitions` reads
  the KEK from the active settings (signature unchanged) and returns `checkpoint_fp = None` when
  none is configured (a run job cannot exist without one: `validate_run` refuses with
  `kek_unconfigured`). The same key shows different fingerprints in two jobs; a KEK rotation (M5)
  changes the fingerprints of running jobs, which only label a checkpoint in the console. Plan
  interface `key_fingerprint(encoded)` is superseded; contract §3.2 amended in the same commit.
  Tests: `test_key_fingerprint_is_keyed_and_salted_by_the_job`,
  `test_keyed_fingerprint_depends_on_kek_salt_info_and_data`,
  `test_checkpoint_fingerprint_is_keyed_per_job` (db; failed before the fix with two equal
  fingerprints).
- **B2, an interrupted reaper stranded a job.** `reap` finalized only the jobs whose partitions
  it dead-lettered in the same pass, after that autocommit statement, so a pass interrupted in
  between left the job in `partitions/waiting` with its `key_enc` forever. Each pass now ends
  with a sweep (`_FINISHABLE_JOBS`): every `partitions`-phase job in `waiting`/`paused` with no
  `queued`/`running` partition gets `_lock_job` + `_maybe_finalize` in its own short
  transaction (exactly-once is unchanged: the finalize `UPDATE` re-checks under the job lock).
  The sweep runs after pruning and the notify, so a failure in one job's finalize does not skip
  the rest of the pass. The dead-letter statement no longer returns job ids (it counts rows).
  The sweep reads `dps_job` without an index on `(phase, state)`; at the 30 s reaper interval
  that is cheap, and a partial index can go into a spare 010–019 migration if `dps_job` grows
  large. Test `test_an_interrupted_reaper_pass_is_finalized_by_the_next` (fails before the fix:
  the job stayed `partitions/waiting`) also covers a paused job and leaves a busy job alone.
- **B3, `max_queued` passed by concurrent submissions.** `create_job` now takes
  `pg_advisory_xact_lock(hashtextextended('dps_job_admit:<schema>:<tenant>', 0))` first in its
  transaction (no grant needed), so a tenant's count-and-insert runs one at a time. Inside the
  lock it re-checks the idempotency key and returns the original job: without that, replays that
  queued behind the lock saw the original counted and got `queue_full`. That replay race existed
  before the lock as well (`test_concurrent_replays_at_the_cap_return_the_original` failed on the
  old code with 4 of 6 replays refused). Test `test_queue_cap_holds_under_concurrent_submissions`
  (two tenants, three rounds; failed before the fix with 9 of 12 accepted against a total cap of 4).

## Task 10 — DB-backed audit (`DbAuditSink`)

- `dps/audit.py` `DbAuditSink.stop` — **the plan's stop could not flush, fixed.** The plan
  cancelled the writer task and drained the queue in its `except CancelledError` branch. A task
  cancelled before its first step never enters its body (verified with a probe script), so the
  plan's own DB test (`start()`, three `write()`s, `stop()` with no await in between) would have
  inserted nothing. `stop()` now sets a closing flag and waits (`wait_for(shield(task))`) for the
  writer to drain the queue and exit; only on `timeout` is the task cancelled, and whatever is
  still queued is counted in `dps_audit_dropped_total` and logged as `audit_stop_timeout`
  (count only). The writer loop is the plan's (wait up to `audit_flush_ms` for the first event,
  then take what is queued up to `audit_flush_batch`).
- `DbAuditSink._flush` inserts each tenant group separately, so one tenant's failure does not
  skip the others; the sink is healthy after a batch only if every group landed. An event with an
  empty `tenant_id` is filed under `-` (contract §8.3), so the row and the GUC agree under RLS.
- The insert is schema-qualified (`<schema>.dps_audit_log`, from `dps.db.schema_ident`, imported
  lazily so an L1-only deployment without the `db` extra can still import `dps.audit`). Plan:
  unqualified name resolved through `search_path`; `dps/db.py` states all DPS SQL is qualified.
- `AuditUnavailable` carries `code = "audit_unavailable"` and a fixed message.
- `dps/layers/api/common.py`: the route template is also used for the `dps_requests_total` /
  latency metric labels and the log context (plan: audit only). Why: a concrete path with ids
  would make the metric labels unbounded once M2 routes use `run_engine`; for every M0 route the
  template equals the path. The audit status is the route's declared `status_code` (default 200)
  or the mapped error status. The context-var unbind runs in an inner `finally`, so a strict
  503 raised by the audit write cannot leave `trace_id`/`route` bound.
- `dps/observability.py`: `action` added to the log allow-list (the M2 `AuditEvent` field; without
  it the synchronous audit line would silently lose it). `tests/test_observability.py` gains
  `test_fields_the_m2_audit_line_logs_are_kept`.
- `tests/test_audit_m2.py`: the strict test's acquire stub is a plain function raising
  `AssertionError` (plan: an `async def` "never called"); with a draining `stop()` the writer does
  reach it, and an un-awaited coroutine would only warn. The test now also asserts the dropped
  counter and that the strict sink reports `last_error == "AssertionError"`. Added:
  `test_sink_without_a_running_writer_only_logs`,
  `test_audited_route_is_the_template_and_status_is_recorded` (a `{kind}` route; the concrete
  path never reaches the event). `tests/db/test_audit_sink.py` also checks the stored columns,
  that the writer flushes while running, and that the failure message never reaches the log.
  5 offline + 3 db tests (plan: 3 + 2).

## Task 11 — Object storage and artefacts + MinIO

- `docker-compose.yml`: `minio` publishes `127.0.0.1:${DPS_MINIO_PORT:-9010}:9000` and
  `127.0.0.1:${DPS_MINIO_CONSOLE_PORT:-9011}:9001` (plan: literal ports; contract §0 amendment
  2026-10-12). `minio-init` gets `mem_limit: 128m` and `restart: "no"`; both carry the stack's
  labels. The cached `minio/minio:latest` (RELEASE.2025-09-07) ships `curl`, so the contract's
  healthcheck is used unchanged. `minio-init`'s command is the plan's, folded over three lines.
- `dps/storage.py` `LocalObjectStore.put` — **the plan's atomic write could fail across
  filesystems, fixed.** The plan spooled into the system temp directory and `os.replace`d the file
  into the store; under compose `/tmp` is a tmpfs and the store a volume/PVC, so the rename would
  fail with `EXDEV`. The spool file is now created in the target directory (`.dps-spool-*`),
  fsynced and renamed; a failing source iterator removes it (test
  `test_overwrite_is_atomic_and_a_failed_stream_leaves_nothing`). Spool writes run in a thread
  (the plan wrote synchronously on the event loop).
- Keys: besides the plan's rules, empty, `.` and trailing-slash segments are refused (`a//b`,
  `a/./b`, `a/`), so a local path and an S3 key always name the same object. The checker is public
  as `check_key` (Task 20's files writer validates its keys with it).
- `S3ObjectStore`: a private `boto3.session.Session()` per store (the default session is not
  thread-safe to create clients from); bytes up to 16 MiB go up with one `put_object` (plan: always
  spooled to disk); `get`/`stream`/`delete`/`exists` map every botocore error to `StorageError`
  (`storage_not_found` for `NoSuchKey`/`404`, else `storage_error` with the S3 error code or the
  class name — never the message). The plan let non-`NoSuchKey` errors through raw and reported
  any `ClientError` (e.g. `AccessDenied`) from `exists` as "absent". `connect_timeout=10`,
  `read_timeout=60`.
- `LocalObjectStore.get/stream` map a missing file to `storage_not_found` on the first read (no
  separate `is_file` race); `exists` runs in a thread.
- `ArtefactService.save` deletes the object again when the row insert fails (no orphan objects;
  test `test_a_refused_row_leaves_no_orphan_object`). `delete_for_job` deletes exactly the rows
  whose objects it removed (`id = ANY(...)`), so a concurrent `save` is never left as an object
  without a row. SQL is schema-qualified; `dps.db` is imported lazily (needs the `db` extra).
- `StorageError` also keeps `.detail`. `tests/fixtures/sources.py`: the `s3_store` fixture skips
  via `importorskip("boto3")` without the `connectors` extra.
- Tests: the plan's 10 plus three more key cases, `test_missing_object` also covers `stream`,
  `test_overwrite_is_atomic_and_a_failed_stream_leaves_nothing`,
  `test_artefact_store_follows_the_settings`, `test_missing_object_and_bad_keys` (s3) and
  `test_a_refused_row_leaves_no_orphan_object` (db): 13 local + 2 s3 + 2 db = 17 (plan: 10).
- `HANDOFF.md` §3: `minio`/`minio-init` rows and the start command for the `s3` tests; §4: the two
  MinIO port variables.

## Task 12 — Selection validation and `redact_spec`

Installed sqlglot: **30.22.0** (recorded in HANDOFF §8). All 24 plan tests passed against the
plan's design; the changes below close gaps the plan's code left open (each has a test).

- **Oracle typed literals could not compile, fixed.** sqlglot parses Oracle `DATE '…'` as
  `DateStrToDate` and `TIMESTAMP '…'` as `StrToTime`, which render as `TO_DATE(:l1,
  'YYYY-MM-DD')` / `TO_TIMESTAMP(…, '…')`; re-parsing that template found the format string and
  `compile_where` refused it as `selection_not_parameterized`. Literals that are SQL structure,
  not values, are now kept and never counted: `ORDER BY`/`GROUP BY` positions (plan), type
  parameters (`NUMERIC(10,2)`, which the plan would have turned into binds and broken), the
  `format` argument of date/time functions, and the unit of an Oracle interval function.
- **Oracle intervals** become `NUMTODSINTERVAL(:lN, 'DAY')` / `NUMTOYMINTERVAL(:lN, 'MONTH')` with
  the number bound (plan: `CAST(CAST(:lN AS TEXT) AS INTERVAL)`, which renders as
  `CAST(… AS CLOB)` and is not valid Oracle). Oracle's inner text cast is `VARCHAR2(4000)` (plan:
  `TEXT` → `CLOB`). PostgreSQL output is exactly the plan's.
- **String forms that are not `exp.Literal` leaked into templates, fixed.** `$$…$$` (RawString)
  and `N'…'` (National) are bound like plain strings; `E'…'`, `B'…'`, `X'…'` and `U&'…'` (whose
  text is not the value) are refused with the new code `selection_literal_unsupported` (mapped
  to the `literals_bound` check). Before, `x = $$Ananya$$` rendered as `x = 'Ananya'` in the
  "value-free" template.
- A `where` must be one boolean expression: trailing `ORDER BY`, `LIMIT`, `GROUP BY` etc. are
  refused (`selection_parse_error`) instead of being silently dropped by `_where_expr`.
- Identifiers starting with `__dps_` are reserved (`selection_identifier_invalid`): a column
  named `__dps_bind_x` would otherwise be rendered as a bind position.
- The `dbms_*`/`utl_*`/`sys` package check applies to qualifiers (schema, package, table
  qualifier), not to a column's own name (`utl_rate` is a valid PostgreSQL column); a package call
  `dbms_lock.sleep(…)` and `sys.x` are still refused.
- Deny-list additions (stricter): `pg_try_advisory*` (the plan's `pg_advisory*` prefix misses
  them), `query_to_xml*` and `cursor_to_xml*` (they execute SQL from a string, which with bound
  literals would run arbitrary statements past this validation, e.g. `set_config`), `pg_notify`,
  `pg_logical_emit_message`, `pg_switch_wal`, `pg_create_restore_point`, `pg_file_*`,
  `pg_stat_reset*`. Exported as `DENIED_FUNCTION_PREFIXES`. Review G3 r1 (B1) and r2 (B1) added
  the known core and contrib members of the SQL-from-a-string class: `ts_stat*` and
  `ts_rewrite*` (core PostgreSQL, executable by any role), `crosstab*` and `connectby*`
  (tablefunc), `xpath_table` (xml2) and `pg_background*` (its session is not read-only). A
  deny-list cannot cover user-defined functions or third-party extensions; see "Review G3 r1
  fixes" and "Review G3 r2 fixes" below for the residual.
- `referenced_columns` ignores columns inside a subquery (they belong to the subquery's tables),
  so `customer_id IN (SELECT a.customer_id FROM bank.accounts a …)` is not refused as an unknown
  column. Projected `columns` are validated with `quote_ident` and folded for Oracle.
- `_walk` casts sqlglot 30's `Expr` to `Expression` (mypy). `RecursionError` from a deeply
  nested expression is a `selection_parse_error`. `selection_checks` maps
  `selection_engine_mismatch` to `select_only` and `selection_operator_denied` to
  `no_into_lock_procs`.
- `redact_spec`: as planned, with type guards on the popped consistency dicts.
- Tests: the plan's 22 + 2 and 17 more (`test_oracle_intervals_and_timestamps_…`,
  `test_type_parameters_and_order_positions_…`, `test_comments_and_dollar_quoted_strings_…`,
  `test_escaped_string_forms_are_refused_…` ×3, `test_more_rejections` ×8,
  `test_column_names_are_not_package_names_…`, `test_projected_columns_…`,
  `test_mongo_selections_refuse_sql_…`): 41.
- Open for Task 15 (not changed here, plan decision): bare string literals are bound as `str`
  and decimals as their text, so where PostgreSQL infers a non-text parameter type (`date_col >
  $1`, `numeric_col > $1`) asyncpg will refuse the `str`; the connector must coerce bound values
  by the prepared statement's parameter types (typed literals are already cast from text).

## Task 13 — Partition planning

- `dps/layers/connectors/partitions.py`: as planned (the `lowers`/`uppers` lists are typed
  `EncodedKey | None`, the contract alias). No behaviour change.
- Tests: the plan's 4 (two extra assertions: a negative row count is 0 partitions,
  `partition_rows=0` cannot divide by zero; `count=0` gives one range) plus
  `test_every_key_falls_in_exactly_one_range` (5,000 keys, a 300-key DB-ordered sample, 16
  ranges: every key in exactly one range) and `test_text_keys_follow_the_sample_order_not_python_order`
  (boundaries come from sample positions even when Python would order the keys differently).
  6 tests (plan: 4).

## Task 14 — Connection registry and connector registry

- `dps/layers/connectors/registry.py` — **the contract name `test_store` would break every test
  module that imports it, fixed.** pytest collects any module-level callable named `test_*`,
  imported ones included, so `from … import test_store` in `tests/test_registry.py` made pytest
  run it as a test with fixtures `store`/`conn` (an error). The function sets
  `test_store.__test__ = False` (name unchanged, contract §1). Test
  `test_the_probe_helper_is_not_collected_as_a_test`.
- `open_connector`'s `postgres` branch imports `dps.layers.connectors.postgres`, which Task 15
  creates; until then that branch raises `ModuleNotFoundError` (nothing calls it before Task 15;
  `mypy` passes through `ignore_missing_imports`). The oracle/mongo/s3/pvc branches are the plan's.
- `dps/connections.py` `update_connection`: the merged connection is validated with
  `ConnectionSpec` itself, a placeholder inline secret standing in when the stored secret is
  inline (kept, re-sealed or already purged), instead of the plan's `model_construct` for that
  case — `model_construct` skips every rule, so a patch could have tagged an inline connection
  `production` (contract §5.1: production must use `secret_ref`), given a `pvc` connection a
  secret, or made a file store a source. Such patches are now `connection_invalid` (422, the
  rule's message). Changing `params` or the secret clears `last_test` and `capabilities` (the old
  test describes another endpoint or login); a rename keeps them. `ConnectionPatch`, the engine
  is immutable. Tests `test_patch_rules_secret_switch_and_stale_test`,
  `test_a_pvc_connection_never_takes_a_secret` (failed before the placeholder covered patched
  credentials).
- `load_credentials` also refuses an **expired** inline secret (`secret_expires_at <= now()`,
  contract §5.1: expired or purged → `SecretUnavailable`) even before retention nulls the blob,
  and maps a blob that does not open with this KEK (`SealError`) to `SecretUnavailable`; it reads
  the blob with `tenant_id = $1 AND id = $2` under the tenant GUC.
- Every repository query also filters `tenant_id = $1` (defence in depth beside RLS); `delete`,
  `record_test` and `get` likewise. `record_test` takes the optional `conn` too (workers).
  `create_connection` returns the row it inserted (read back in the same transaction).
- `_SealedCredentials` fields are `repr=False`; `Credentials` keeps `SecretStr`. Every model hides
  input in validation errors.
- `dps/storage.py`: `target_store` as planned. `LocalObjectStore.put/delete` map `OSError`
  (unwritable root, a file in the way, disk full) to `StorageError("storage_error", <class>)`, so
  `test_store` reports a broken `pvc` target as a failed check instead of raising (the plan's
  `test_store` only catches `StorageError`). Test `test_a_failing_store_is_reported_by_code`.
- `dps/jobs.py`: `_encode_cursor`/`_decode_cursor` renamed to `encode_cursor`/`decode_cursor`
  (with docstrings) as the plan asks; the two call sites updated.
- `dps/secrets.py`: `SECRET_FILES` gains `"ca.pem"`.
- `HANDOFF.md` §7: the `secret_ref` file names and the inline-secret TTL, and the open issue below.
- **Open issue (not changed, contract question):** a `secret_ref` is not scoped to the tenant.
  Any tenant with the `jobs` scope can create a connection naming another tenant's mounted
  reference (`dps/src/<other>`) and read through it. The contract and spec fix the path rule
  (`DPS_SECRET_DIR/<ref>/`) without a tenant prefix and the plan's own test uses a shared
  `dps/src/…` reference, so the fix (e.g. refs under `<tenant_id>/` or a per-tenant allow-list in
  `dps_tenant`) needs a contract decision.
- Tests: the plan's 3 + 5 plus `test_expired_inline_secret_and_missing_kek`,
  `test_patch_rules_secret_switch_and_stale_test`, `test_a_pvc_connection_never_takes_a_secret`,
  `test_creating_a_connection_registers_the_tenant` (db) and
  `test_a_failing_store_is_reported_by_code`, `test_s3_store_takes_keys_from_the_credentials`,
  `test_the_probe_helper_is_not_collected_as_a_test` (offline): 7 db + 8 offline = 15 (plan: 8).

## Review G3 r1 fixes (Tasks 10–14)

- **B1, the selection deny-list could be bypassed through functions that execute a SQL
  string.** `ts_stat(sqlquery text [, weights])` and `ts_rewrite(query tsquery, select text)`
  (core PostgreSQL, `EXECUTE` granted to PUBLIC) and tablefunc's `crosstab(text …)` /
  `connectby(relname, …)` run the text they are given. Validation did not refuse them and
  literal binding turned the SQL text into a parameter, so a selection that passed
  `validate_selection`, `parameterize_literals` and `compile_where`/`compile_query` could run any
  SELECT, including the denied functions (the reviewer lifted `statement_timeout` to 0 with
  `ts_stat('select set_config(…)::tsvector')` inside `BEGIN READ ONLY` on the lane's PG16, and
  ran `pg_sleep`). `DENIED_FUNCTION_PREFIXES` now also holds `ts_stat`, `ts_rewrite`,
  `crosstab` (covers `crosstab2/3/4` and the documented `crosstab_*` wrappers), `connectby`, and
  `pg_background` (stricter than the finding: `pg_background_launch(sql)` runs SQL in a separate
  session that is not read-only). The check matches the lower-cased bare function name, so the
  `pg_catalog.`/`public.`-qualified, double-quoted, upper/mixed-case, named-argument
  (`sqlquery => …`), `ROWS FROM (…)`, `LATERAL`, scalar-subquery and `EXISTS` spellings are all
  refused with `selection_function_denied` and a value-free message; `U&"…"` identifiers were
  already refused (`selection_identifier_invalid`). Oracle `CONNECT BY PRIOR` and
  `SYS_CONNECT_BY_PATH` are syntax/other names and still validate.
  Tests (all failed before the fix with `DID NOT RAISE`, except the Oracle guard):
  `test_functions_that_run_a_sql_string_are_denied_in_where_and_query_form` ×23 spellings (each
  in two where forms and four query forms, through `validate_selection` and
  `parameterize_literals`), `test_a_bound_sql_string_never_compiles_into_a_sql_executing_function`
  ×9 (the bound-parameter attack through `compile_where` and `compile_query`),
  `test_the_sql_string_functions_are_on_the_exported_deny_list`,
  `test_oracle_hierarchical_queries_are_not_mistaken_for_connectby`. No existing test changed.
- Residual (not changed, recorded for the M5 hardening owner): a function deny-list cannot be
  complete. User-defined functions in a source database, or other extensions (pgTAP's
  `lives_ok`/`results_eq`/`throws_ok`, Oracle `CTXSYS`-owned packages), can also execute a SQL
  string. The connector's source role must stay least-privilege (no `EXECUTE` on such functions,
  no `dblink`/`pg_background` installed for it); an allow-list of functions for selections would
  need a contract change (§5.2 specifies a deny-list).

## Review G3 r2 fixes (Tasks 10–14)

- **B1, `xpath_table` (PostgreSQL contrib `xml2`) executed a SQL string past the deny-list.**
  `xpath_table(key, document, relation, xpaths, criteria)` builds
  `SELECT <key>, <document> FROM <relation> WHERE <criteria>` from its text arguments and runs it
  through SPI. xml2 ships in the lane's `postgres:16-alpine` image and, once installed, grants
  `EXECUTE` to PUBLIC. Validation accepted it and literal binding turned the relation text into a
  parameter, so `compile_where`/`compile_query` produced `XPATH_TABLE($1..$5)`; the reviewer lifted
  `statement_timeout` to 0 through `set_config` and ran `pg_sleep(0.5)` as `dps_app` inside
  `BEGIN READ ONLY` on the lane's PG16. Round 1's claim that it "added the rest of the
  SQL-from-a-string class" was wrong; the wording above now says "the known core and contrib
  members". `DENIED_FUNCTION_PREFIXES` now also holds `xpath_table` (xml2's other functions,
  `xpath_string/number/bool/list/nodeset`, `xml_valid`, `xml_encode_special_chars` and
  `xslt_process`, take a document, not SQL, and stay allowed, as do core `xpath`/`xpath_exists`).
  Tests (all failed before the fix with `DID NOT RAISE`, except the document-function guard):
  `test_functions_that_run_a_sql_string_are_denied_in_where_and_query_form` +8 spellings
  (plain, upper-case, mixed-case, `public.`/`PUBLIC.`-qualified, quoted, quoted-qualified; each in
  two where forms and four query forms, through `validate_selection` and `parameterize_literals`),
  `test_a_bound_sql_string_never_compiles_into_a_sql_executing_function` +3 (the bound `:q`
  attack through `compile_where` and `compile_query`), `xpath_table` added to
  `test_the_sql_string_functions_are_on_the_exported_deny_list`, and
  `test_the_xpath_table_relation_attack_is_refused_at_every_stage` (the reviewer's bound-`:rel`
  reproduction through `validate_selection`, `parameterize_literals`, `compile_where` and
  `compile_query`, value-free message), plus the guard
  `test_xml_functions_that_take_a_document_are_not_mistaken_for_xpath_table`. No existing test
  changed or loosened.
- How "known core and contrib members" was checked: in a scratch database on the lane's PG16
  (dropped afterwards), every extension in `pg_available_extensions` that the image can create
  was created (46 of the 60 not installed by default; the other 14 are PL/Perl, PL/Python,
  PL/Tcl and their transforms, whose libraries the alpine image lacks), and every extension
  function taking a `text`/`varchar`/`name`/`regclass` argument that PUBLIC can execute was read
  by hand. The only SQL-string executors are `dblink*`, `crosstab*`, `connectby` and
  `xpath_table`, all now denied. In core PostgreSQL 16 the class is `query_to_xml*` (SQL text),
  `cursor_to_xml*` (fetches from a named cursor), `ts_stat` and `ts_rewrite`, all denied.
- Residual (not changed, recorded for the Task 15 connection test and the contract owner): a
  function deny-list cannot be complete. It does not cover user-defined functions in a source
  database (any PL/pgSQL `EXECUTE`, or a function in an untrusted PL), or third-party and
  managed-service extensions such as `aws_s3.query_export_to_s3(query, …)` (Amazon RDS/Aurora:
  runs a SQL string and writes the result to S3), pgTAP (`lives_ok`/`results_eq`/`throws_ok`)
  or Oracle `CTXSYS` packages. The source role must stay least-privilege. Recommended, not done
  here: the Task 15 PG connection test reports `has_function_privilege` for the source role on
  known SQL-string or exfiltration functions (`dblink_exec`, `xpath_table`, `crosstab`,
  `ts_stat`, `query_to_xml`, `pg_background_launch`, `aws_s3.query_export_to_s3`), and a contract
  amendment could move §5.2 to a function allow-list for `where`/`query`.

## Task 15 — PostgreSQL connector (read side) and `source-pg`

- `docker-compose.yml` `source-pg`: publishes `127.0.0.1:${DPS_SOURCE_PG_PORT:-5442}:5432` (plan:
  literal 5442; contract §0 amendment 2026-10-12) and its healthcheck is `pg_isready -h 127.0.0.1
  -U postgres -d bank` (plan: without `-h`), for the reason recorded at Task 3: the image's
  init-time server listens on the unix socket only, and `pg_isready` answers "accepting" even
  for a database that does not exist yet, so a socket probe turns healthy before
  `01_bank_schema.sql` has created `bank`. Labels as on the other services.
- `dps/layers/connectors/postgres.py`, sessions: `sslmode` `prefer`/`allow` are passed to asyncpg
  as those modes (try TLS, fall back), where the plan's `_ssl` returned `False` (never TLS) for
  the default `prefer`. An unknown `sslmode` is `connection_invalid`; a `sslrootcert_ref` that
  does not resolve is `secret_unavailable` (plan: a raw `SecretUnavailable`).
- Login refusals are not retryable: `InvalidAuthorizationSpecificationError` (wrong password, no
  such role), `InvalidCatalogNameError` (no such database) and `ssl.SSLError` raise
  `connection_failed` with `retryable=False`; network failures stay retryable (plan: every open
  failure retryable, so a wrong password would have been retried until dead-lettered).
- `_fetch`: the retryable classes are matched before `PostgresError` (`TooManyConnectionsError`
  and `CannotConnectNowError` are `PostgresError`s, so the plan's order made them non-retryable);
  `UndefinedTableError` → `object_not_found`; a bind value that does not parse as its parameter
  type → **new code `selection_param_invalid`** (not retryable, value-free; the plan let the
  `ValueError` from `coerce_bind` escape unmapped). A selection whose compiled binds name a
  parameter without a value is `selection_param_missing` (existing code; the plan's
  `params[name]` raised a `KeyError` carrying the bind name).
- `coerce_bind`: booleans accept `true/t/1/yes/y/on` and `false/f/0/no/n/off`, anything else is
  an error (plan: any other text silently became `False`); a non-integral float for an integer
  parameter is an error (plan: truncated). Plan parametrised cases unchanged.
- `sample_keys`: the step comes from the size of the **bounded** set in the same query
  (`count(*) OVER ()` beside `row_number()`), where the plan counted the whole selection without
  `upper` first. With a global upper bound (`max_rows`), the plan returned about
  `n × bounded/total` keys instead of `n` (25 of 50 in its own test); the test now also asserts at
  least 40. One scan instead of two.
- Keyset methods (`read_partition`, `sample_keys`, `key_at_offset`) refuse a shape without a key
  with `no_key` (contract prepare-time code; the plan would have built SQL with an empty key
  list). `select_sql` emits no `ORDER BY` for a keyless shape (`count`/`sample` still work).
- `describe` only sees tables (`relkind IN ('r','p')`) the login can `SELECT`; anything else is
  `object_not_found`, matching the message. `read_transaction` raises `RuntimeError` when the
  connector is not open (plan: `assert`, removed under `python -O`).
- `test()`: opens the connector itself when needed (a refused login returns `ok=False,
  error_code="connection_failed"` as the plan specifies, without raising); a missing source table
  fails `select_source` (`detail="missing"`); target checks run when an object and a write mode
  are given. **Added advisory check `no_sql_string_functions`** (count of executable `dblink*`,
  `crosstab*`, `connectby`, `xpath_table`, `pg_background_launch`, `query_export_to_s3`): the
  recommendation recorded under "Review G3 r2 fixes" — the selection validator refuses these
  functions, but a source login able to run them is not least-privilege.
- `writer()` is a stub raising `ConnectorError("engine_unavailable")` until Task 19, so
  `PostgresConnector` satisfies the `Connector` protocol for `mypy` (the registry returns it as
  one).
- `tests/fixtures/sources.py`: `SourcePg` also exposes `host`/`port`/`database` (from the DSN); the
  fixture drops the schema with `IF EXISTS` in `finally`. Fixture names and helpers as planned.
- Tests: the plan's 13 offline + 6 integration, plus offline
  `test_single_column_keys_compare_scalars`, `test_tablesample_applies_to_tables_only`,
  `test_identifiers_are_validated_before_interpolation`, `test_coerce_bind_more_types`,
  `test_kinds_cover_the_common_types`; integration `test_string_binds_are_coerced_by_parameter_type`,
  `test_query_selection_reads_through_the_wrapped_query`,
  `test_sql_string_functions_are_reported_as_advisory` (creates `tablefunc` in the test schema;
  the schema drop removes it), and extra assertions in the plan's tests (listing, whole-range
  read, `max_rows`/projection, the `dps_writer` target checks, a refused login is not retryable
  and `test()` reports it without raising): 18 offline + 9 integration = 27 (plan: 19).
- `HANDOFF.md`: §3 `source-pg` row, start command and `DPS_TEST_SOURCE_PG_DSN`; §4
  `DPS_SOURCE_PG_PORT`; §8 the read-session rules and the advisory check.

## Task 16 — Engine bridge (`dps/engine/batch.py`) and M0 touch points

- `dps/engine/transform/apply.py` — **plan assertion that cannot hold against M0 as built,
  corrected.** M0 keys the record anchor before use (`_keyed_anchor`: HMAC under the key's
  `anchor` subkey whenever key material is present; it travels in reveal hints). The touch point
  is therefore `anchor = _keyed_anchor(options.anchor or anchor_for(document, plan), options.key)`
  (plan: `options.anchor or anchor_for(...)`, written against the pre-fix M0), and
  `test_apply_plan_uses_the_given_anchor` asserts `b.anchor == _keyed_anchor("CUSTOMER_ID:abc",
  key)` instead of the raw string; the raw anchor must never reach a hint. L1 and L2 still agree
  (`test_l1_and_l2_agree_on_the_anchor_driven_date_shift` passes): both key the same raw anchor.
  **Note for Task 22 (`reveal_row`):** the `date_shift` reverse parameter `anchor` is the keyed
  anchor; recompute it as `_keyed_anchor(row_anchor(...), key)`, not the raw `row_anchor`.
- `dps/engine/transform/methods.py`: **not modified.** M0 as built already draws `_dictionary`
  without a key from `sha256` (its comment: "stable across processes"); the plan's
  cross-process test passes unchanged.
- `dps/engine/batch.py` `verify_row` / per-row verification (plan design kept: fixed points ≥ 4,
  containment ≥ 8), with these corrections, each covered by a test:
  - kept whole columns are not searched (the plan's text says so, but its code put every column
    of the row document, kept ones included, into the haystack) and keep their driver value
    (only transformed columns are converted back with `from_engine_text`);
  - spans a synthesize-mode plan keeps (`keep_non_sensitive`, e.g. a `DATE` in notes) are not
    originals (the plan counted them, so an 8-digit kept date failed the batch:
    `test_synthesize_free_text_keeps_low_sensitivity_spans_without_a_hit`);
  - each output is a haystack of its own (plan: one string joined with spaces, which
    normalisation turns into one run, so a needle could match across two columns' outputs);
  - `outputs` and the new keyword `inputs` are keyed by **path**, hits name the output path where
    the original was found and carry the span's own entity type (plan: the original column's path
    and `FREE_TEXT` for every span), at most one hit per (path, entity type) as in M0's sweep;
  - **added the name-part rule** (contract §0 amendment 2026-10-12 leaves it to §6.4): a part of
    at least 8 characters of a transformed name that appears in an output more often than the same
    column's input held it outside the name is a `name_part` hit
    (`test_a_name_part_that_appears_from_nowhere_is_a_leak`); 8, not M0's 4, by the cross-field
    volume reasoning (a 4-letter part inside random FPE/token text is ~1e-4 per row).
  - `checked` counts the originals a rule could check (plan: every original).
- `BatchTransformer.transform`: a free-text value the detector finds as one **whole** value (the
  notes are only an e-mail) is planned as a span over the whole text, so it still becomes an
  envelope token (plan: the whole finding gave an empty original and no envelope). Free-text
  columns are always searched, findings or not, so an undetected copy of another column's
  original is a leak (`test_an_undetected_copy_in_free_text_is_a_leak`).
- `BatchTransformer.__init__` refuses plans (or an anchor) naming a column the table does not
  have (`ValueError`, value-free; plan: a `KeyError` mid-batch). `FINDINGS` (span findings) and
  `VERIFY_FAIL` are incremented per batch beside `TRANSFORMS` (contract §6.4 names the engine
  counters). `LeakageError.code = "leakage"`. `ColumnPlan`/`BatchResult` hide input in errors;
  `ColumnPlan.output_column` casts `kind` instead of a `type: ignore`. Input rows are never
  mutated (`dict(row)` copies; asserted).
- `dps/layers/connectors/postgres.py` `describe`: `path=child_path("$", name)` as planned.
- Tests: the plan's 4 + 9 plus `test_apply_options_keep_their_m0_positional_order`,
  `test_a_text_column_that_is_one_whole_value_is_tokenized_as_a_span`, `test_mode_prerequisites`,
  `test_an_undetected_copy_in_free_text_is_a_leak`,
  `test_a_name_part_that_appears_from_nowhere_is_a_leak`, `test_kept_columns_are_not_searched`,
  `test_verify_row_rules`, `test_plans_digest_is_stable`,
  `test_synthesize_free_text_keeps_low_sensitivity_spans_without_a_hit` and extra assertions
  (`values_by_column`, hit shape, child paths that need quoting): 5 + 17 = 22 (plan: 13).

## Task 17 — Discovery, snapshots and the `discover_job` handler

- `dps/snapshots.py` — **the plan's override semantics lost information, changed to "overrides
  accumulate per path".** The plan replaced the stored override list on every `set_overrides`
  and re-applied it to the *stored* (already overridden) columns from their method suggestions.
  An entity, free-text or anchor override is not undone by resetting methods, so a later call that
  omitted it left the column reclassified but flagged `override=False` — the evidence pack would
  then under-report what the owner changed. `set_overrides` now merges the new overrides into the
  stored list (per path; fields set later win; a re-specified path moves to the end) and applies
  the merged list; an override stays visible until replaced (`merge_overrides`, public;
  `test_overrides_accumulate_per_path`). An explicit `entity_type: null` marks a column not
  sensitive (`keep`); overrides are stored and hashed with `exclude_unset` so that explicit null
  survives the round trip. Contract §5.3 notes the semantics (same commit).
- `apply_overrides` takes an additional optional `shape` (the snapshot's `TableShape`), so a
  re-suggestion sees the column's real `nullable`; `text_column` overrides follow fixed rules
  (true without an entity → `FREE_TEXT`; false on `FREE_TEXT` → not sensitive); an anchor on a
  column without an entity is refused with **new code `override_anchor_invalid`** (422), and a
  column reclassified to "not sensitive" loses the anchor. The preset is used for re-suggestion
  only when both its name and `config_hash` match the snapshot (an inline policy named like a
  preset is treated as inline). A missing snapshot is **new code `snapshot_not_found`** (404).
  `accept_snapshot` catches the unique-index race outside the transaction (`snapshot_conflict`).
  All queries also filter `tenant_id` beside RLS. Contract §5.3 lists the new codes.
- `dps/layers/connectors/discovery.py` `discover_job`, failures the plan let escape uncoded:
  `open_connector` runs inside the `try` (plan: before it, so a refused or unreachable source
  raised out of the handler and was never retried — `test_an_unreachable_source_is_retried`);
  `SecretUnavailable` → `secret_unavailable`, `ConnectionStoreError` → its code, an invalid inline
  policy → `policy_invalid`, an unparsable stored spec → `spec_invalid`; a target-only connection
  → `connection_role`. A stored spec that carries a selection but no `spec_enc` fails with
  `secret_unavailable` (the plan's code would have replaced the selection with an empty one and
  sampled the whole table). Failed jobs record `progress.outcome = "failed"`.
- `classify_columns`: binary / LOB-binary / ObjectId columns are classified by name only, as the
  plan's text says (its code ran the detector over the hex text of every blob); their
  `null_rate` counts non-null values. The anchor flag is set with `model_copy`.
- `dps/worker.py`: `HandlerContext.__repr__` shows the worker id only (the dataclass default repr
  would print the KEK bytes into any log line or traceback that formats the context).
- Fixtures: `engine` and `handler_ctx` in `tests/fixtures/db.py` (the context's `secret_dir` is a
  per-test tmp directory); `source_secrets` and `register_source` (extra `port` keyword, for the
  unreachable-source test) in `tests/fixtures/sources.py`; `SourcePg.customers_table` uses the
  demo DDL without its foreign key (the test schema has no `employees`) and rows from
  `SurrogateEngine` personas under a fixed test-only seed. `tests/connectors/factories.py` also
  provides `customers_rows()` and `classified_columns()`, which `tests/test_discovery_classify.py`
  uses instead of a local copy.
- Tests: the plan's 3 + 1 + 1 plus offline `test_empty_samples_and_nulls`,
  `test_type_compatibility_rules`, `test_overrides_apply_to_suggestions`, `test_override_errors`,
  `test_free_text_overrides`, `test_content_hash_is_canonical`; db
  `test_overrides_accumulate_per_path`, `test_missing_snapshots_and_paging`; integration
  `test_several_objects_make_one_snapshot_each`, `test_failures_are_coded_and_value_free`,
  `test_an_unreachable_source_is_retried`: 9 + 3 + 4 = 16 (plan: 5). The discover test also
  checks that no sampled name, e-mail or mobile number reaches the stored snapshot.
