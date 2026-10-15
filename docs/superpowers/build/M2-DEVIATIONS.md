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
- **Open issue (fixed after M2 on main, security fix SEC-2, 2026-10-12: refs resolve under
  `DPS_SECRET_DIR/<tenant_id>/`, shared refs need an allow-list; see HANDOFF §7):** a `secret_ref` is not scoped to the tenant.
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
- **Defence in depth beside the selection deny-list** (recorded at verify r1, see "Verify r1
  fixes"). The deny-list is the control; the read side limits what a function that slips past it
  can do. Every read runs in `BEGIN READ ONLY` on a session with `statement_timeout`
  (`DPS_CONNECTOR_STATEMENT_TIMEOUT_SECONDS`), `lock_timeout=5s` and
  `idle_in_transaction_session_timeout`, so a sleep ends at the timeout and nothing is written.
  Sessions come from an asyncpg pool, which resets a session on release with
  `SELECT pg_advisory_unlock_all(); CLOSE ALL; UNLISTEN *; RESET ALL;`: a session-level advisory
  lock or a `set_config(…, false)` taken inside one read never reaches the next acquirer (`RESET
  ALL` restores the startup `statement_timeout` and `default_transaction_read_only`). Pinned by the
  integration test `test_a_released_session_keeps_no_advisory_lock_or_session_setting` (one pooled
  session: lock and `statement_timeout=0` taken, released, the lock gone and `7s` back). It does
  not stop a transaction-level effect inside the read itself (signalling another backend, reading
  a large object), which is why the deny-list must cover every invocation path.

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

## Review G4 r1 fixes (Tasks 15–17)

Review: `M2-G4-review-r1.md`, one blocking finding (B1). Contract change: §0 amendment 2026-10-12
"`fpe_digits` keeps the leading-zero class", §6.4 (`ColumnPlan` comment), §6.5 (same commit).

- **B1 — `fpe_digits` on `int`/`decimal` columns lost the token's leading zero (~1 value in 10), so
  the stored token revealed to a different number or not at all.** Decided at contract level, for
  every layer, so that the same digits keep one token in a document, a text column and a numeric
  column (P3, §6.5): `fpe_digits` gains the param `leading_zero: any | preserve`; `preserve`
  cycle-walks FF1 (when `keep_prefix` is 0) until the token's first digit is `0` exactly when the
  value's is. That is a bijection on each class, keeps the length, and leaves every plain-FF1 token
  that already started with a non-zero digit unchanged (about 90 % of ids). Forcing
  `keep_prefix >= 1` on numeric columns (the review's second option) was rejected: it changes every
  numeric token relative to the same digits in a text column or a document (breaking §6.5), leaks the
  first digit, and pushes 6-digit values under the FF1 domain minimum (`det_token` fallback, which an
  integer column cannot hold).
- **Where the default lives.** The method's own default stays `any` (plain FF1), so a reveal hint or
  reveal map without the param keeps revealing tokens issued by v0.1.0-m0. Every *new plan* gets
  `preserve` explicitly: `dps/engine/findings.py` `PLAN_PARAM_DEFAULTS` / `with_plan_defaults`,
  applied by a `PlanEntry` validator (L1 plans and hints) and by a `ColumnPlan` validator
  (`dps/engine/batch.py`; a new `MethodSpec`, never the snapshot's or policy's object), so the evidence
  pack's `reveal_map` will carry it. The policy model and its `config_hash` are untouched (the pinned
  `dpdp-pseudonymise` hash test still passes).
- **Fail closed instead of silent loss** (`dps/engine/batch.py`): `BatchTransformer` refuses
  `fpe_digits` without `leading_zero: preserve` on an `int`/`decimal`/`float` output column
  (`IncompatibleMethod`, `incompatible_method`) and a column plan whose params lack their plan
  defaults (`ValueError`; a `model_copy` around the validator would run with params the reveal map
  does not show). Per row, any **reversible** output that the target column's codec would store
  differently (`to_engine_text(from_engine_text(text)) != text`) raises `incompatible_method` and the
  batch is not written: this also catches `phone` on an integer column (no `+`, so no kept country
  code) and a decimal value whose scale differs from the target column's (re-scaling changes the
  digits FF1 reverses). Non-reversible outputs (surrogates, noise) are converted as before.
- **M0 touch points** (contract §0.7, module and tests together): `dps/engine/transform/fpe.py`
  (`preserve_leading_zero` keyword, `cycle_walk_leading_zero`, `CycleWalkError`, `MAX_WALK = 1000`),
  `dps/engine/transform/methods.py` (`LEADING_ZERO_MODES`, the `leading_zero` choice param on encrypt
  and reverse; the cipher is now built outside the `try`, so a missing key on reverse reports itself
  instead of "domain too small"), `dps/engine/findings.py` (plan defaults). M0 test expectations
  changed with them: `tests/test_planner.py` (SSN params now include `leading_zero: preserve`) and
  `tests/test_apply.py::test_int_tokens_that_would_lose_a_leading_zero_stay_strings` (pins the
  v0.1.0 safety net with an explicit `leading_zero: any` plan; the default now walks on to an int
  token, pinned by the new `test_plans_preserve_the_leading_zero_so_int_tokens_stay_ints`).
- L1 behaviour change, documented: an L1 `fpe_digits` token now differs from the v0.1.0 token for the
  same value and key in about 1 case in 10 (values without a leading zero whose plain token started
  with `0`) and about 9 in 10 for values that start with `0`; old tokens still reveal with their old
  hints. Cost: ~1.1 FF1 calls per value without a leading zero, ~10 per value with one (FF1 ≈ 7 µs).
- Tests (each failed before the fix; run against a `git archive HEAD` copy with the new tests):
  `tests/test_fpe.py` +5 (incl. an exhaustive bijection check of the walk over a 3-digit domain, the
  non-permutation bound, and real FF1 on 400 eight-digit ids), `tests/test_methods.py` +2,
  `tests/test_planner.py` +1, `tests/test_apply.py` +1, `tests/test_batch.py` +5 (incl. real FF1:
  400 `bigint` ids and 400 `numeric(14,2)` amounts, digit lengths kept, revealed with per-path hints
  built from the column plans as Task 22's `reveal_row` will), and integration
  `tests/connectors/test_fpe_numeric_pg.py` (300 rows through source-pg: read as `dps_reader`,
  transformed with real FF1, written to a `bigint`/`varchar`/`numeric(14,2)` table, read back and
  revealed; the `bigint` and text tokens of the same digits are equal; the legacy setting on the text
  column shows ≥ 10 zero-led tokens, so the data set exercises the defect).

## Task 18 — Targets: run validation, run plan, PK strategies, created DDL

- `dps/layers/connectors/targets.py`: the plan's validation table, in its order, with these
  additions (each covered by a test in `tests/connectors/test_targets.py`):
  - **Snapshot object match normalises the PostgreSQL owner** (`None` → `public`), because
    `describe` stores `public.<name>` as the snapshot's `object_ref`; otherwise a run naming an
    object without an owner could never use its own accepted snapshot.
  - `pk_strategy_invalid` also for an `offset` on a non-integer key and a `prefix` on a non-text
    key (the plan's `apply_pk_strategy` would raise a `TypeError` mid-run), and an `append` to
    another connection that is missing or a file store is `target_invalid` (no rule covered it
    when `validate_run` is called without `submit_run`'s lookups).
  - Job groups: a `group_id` with no group passed in is **`group_not_found` (404)** — the code the
    plan's `submit_run` already raises; `validate_run` fails closed instead of skipping rule 17.
    Contract §5.3 lists the code (same commit). In synthesize mode a group run must carry the
    group's seed (fingerprint equal), as obfuscate must carry the group's key (the plan's text
    named "obfuscate without key_material" only).
  - Rules 15/16 (`method_needs_key`, `text_method_invalid`) apply to the columns the run reads
    (`selection.columns` ∪ key, else all), not to columns the run never touches.
- `resolve_target`: the static worst-case length check (`length_overflow`) runs inside
  `resolve_target` for existing targets (the plan described it both there and as a separate loop
  in `prepare` step 5; `prepare` now relies on this one). An `in_place` run with no transformed
  column is `target_invalid` (the `UPDATE` would have an empty `SET` list). Existing-target
  compatibility: same kind, both textual (`text`/`char`/`lob_text`), a planned column into a
  `text` column, or `int` into `decimal`/`float`; a `table` target's key must equal the source
  key as a set.
- `created_column_type` decides "text-like" by the **source column's** kind (the plan's code read
  the plan's kind, which is the same value before `resolve_target`).
- `tests/connectors/factories.py`: `make_connection` also takes `name` and `tenant_id`;
  `accepted_shape` is a helper; `accepted_snapshot` takes `tenant` and marks `customer_id` as the
  anchor, with synthesize methods `keep` (key, `country_code`) and `surrogate` (the rest).
- Tests: the plan's 13 + 4 (typed, without `noqa` comments) plus
  `test_engine_roles_and_in_place_key_rules`, `test_append_rules`,
  `test_files_synthesize_group_and_tabular_rules`, `test_resolve_existing_targets_per_write_mode`,
  `test_same_table_and_run_plan_round_trip` and extra assertions (synthesize plans, plan
  defaults, `TYPE_RE`, `hmac_pseudonym` length): 22 tests (plan: 17).

## Task 19 — PostgreSQL writer (in_place, append, table, ledger, collision precheck)

- `dps/layers/connectors/postgres.py` `PostgresWriter._check_generated` — **no catalogue text is
  interpolated into SQL.** The plan read the identity sequence with
  `SELECT last_value, is_called FROM {seq}` (the sequence name from `pg_get_serial_sequence`
  pasted into the statement); it now uses `pg_sequence_last_value($1::regclass)` with
  `pg_sequence.seqstart`/`seqincrement` (next value = last + increment, or the start value when
  the sequence was never called). A key default that is not a sequence (e.g.
  `gen_random_uuid()`) passes; a composite key is `pk_strategy_invalid` for `generate`.
- `prepare` maps driver failures instead of letting them escape raw: a missing privilege
  (`InsufficientPrivilegeError`, e.g. `CREATE` on the schema) or schema is `target_invalid`,
  other server errors `target_error` (retryable for network/server classes) or
  `statement_timeout`. `truncate_insert` on an existing table also requires `TRUNCATE` (the plan
  checked INSERT/UPDATE/SELECT only, so a missing TRUNCATE failed with a raw driver error after
  the checks passed). The ledger table's own privileges (SELECT/INSERT/UPDATE/DELETE) are checked
  after `CREATE TABLE IF NOT EXISTS` (it may have been created by another login), and a
  concurrent create (`UniqueViolationError` on the catalogue) is tolerated.
- `precheck_collisions`, `read_ledger` and `clear_ledger` map driver errors the same way
  (value-free `target_error`); `write_batch` matches the retryable classes before
  `PostgresError` (Task 15's rule: `TooManyConnectionsError` is a `PostgresError`).
- The table-load staging table name is quoted (`_q`) and the writer keeps no unused `types`
  cache. `PostgresConnector.writer` refuses a `files` target or one without an object
  (`target_invalid`), as planned; `PostgresWriter.__init__` checks the object too (no `assert`).
- Tests (`tests/connectors/test_postgres_write.py`): the plan's 5 plus
  `test_upsert_load_keeps_rows_and_existing_target_privileges` (upsert into an existing table
  keeps other rows), `test_composite_keys_update_and_precheck` (row-value `unnest` join, empty
  key batches) and `test_write_errors_are_coded_and_value_free` (a too-long value: `target_error`
  without the value), and extra assertions (no ledger for `table`, a refused batch leaves the
  ledger mark, two partitions' marks, a created table's primary key, dry-run creates no ledger,
  `create_if_missing: false`, a key without identity/default): 8 tests (plan: 5).

## Task 20 — Files writer and manifest

- `dps/layers/connectors/files.py`: as planned, with these changes (each has a test):
  - **Deterministic gzip.** Text formats are gzipped through `GzipFile(mtime=0)`; the plan's
    `gzip.open` stamps the current time into the header, so a resumed partition would rewrite
    its object with different bytes and a different sha256 (the plan promises deterministic
    content). `test_partitions_are_independent_and_deterministic` compares the hashes.
  - `snappy`/`zstd` are Parquet codecs: a CSV/JSONL target with them (the `FilesSpec` default is
    `snappy`) is written uncompressed, and the manifest records the **effective** compression
    (`effective_compression`); the plan did not say what text formats do with them.
  - Uploads stream each part file in 1 MiB chunks (`ObjectStore.put` takes an async iterator;
    S3 goes multipart above 16 MiB) instead of `read_bytes()`, which held a whole part (up to a
    million rows) in memory.
  - Parquet: `decimal128(p, s)` only for a declared precision ≤ 38, else exact text (the plan's
    rule; an unconstrained `numeric` has no precision, so it is text too); string columns convert
    any non-string driver value (UUID, JSON, other types) to its text form; batches go through
    `Table.from_pydict` with the schema. JSONL keeps non-finite floats as strings (JSON has no
    NaN).
  - `prepare` turns a refused probe into `PrepareFailure("target_invalid")` with the storage
    code; the probe object is `<base>/dps-probe.txt`. `close()` discards unfinished spools.
    The manifest carries `manifest_version: dps.files.v1` and objects sorted by key; a column's
    `type` is its declared type (else its kind). An empty partition returns `{"files": []}`.
- Tests: the plan's 4 + 1 (`test_formats_round_trip` also covers JSONL+gzip and checks the
  extension, the date column and the timestamp type; the split test writes the resumed
  partition in two batches) plus `test_partitions_are_independent_and_deterministic`,
  `test_writer_protocol_no_ops_probe_and_value_conversion`, `test_a_failing_store_fails_the_probe`;
  the s3 test uses `zstd` and checks the probe: 9 offline + 1 s3 = 10 (plan: 6).

## Task 21 — Evidence pack (build, hash, HTML, store)

- `dps/layers/connectors/evidence.py`: the contract §7.1 model plus `label` and `mode` (as
  planned); `fidelity`, `privacy`, `utility` and `scan` default to `None` (M4/M5 fill them).
  Additions and decisions, each covered by a test:
  - **Column params are printed only for reversible methods.** `columns[*].params` and
    `reveal_map[*].params` carry the parameters reveal needs (`keep_suffix`, `domain`,
    `window_days`, `leading_zero` …); a one-way method's params are left out because a policy or
    review may put literal values there (`dictionary` `values`, a `redact` label) — the leakage
    test in Task 22 deliberately puts a customer's name into a `dictionary` override.
  - `reveal_map` entries also carry `text_column` (free-text columns reveal through their
    envelopes, not a whole-value hint), and `reveal` is computed by the public
    `anchor_recoverable(plan)`: false for `date_shift` when the anchor column (or, without one,
    a key column) is transformed one-way or replaced by an append `generate`/`offset`.
  - Counter keys are split as contract §3.3 defines (`method`, `fallback:<note>`, `col:<column>`)
    plus `verify:hits` (warn-mode hits; Task 22 records them). `counts` adds
    `values_transformed` (method counters except `keep`); `verification` gets `fallbacks`
    (per-note totals; per-column fallbacks are not tracked, so `columns[*].fallbacks` is `None`).
  - `source.selection` also carries `query_template` / `mongo_filter_template` (templates from the
    redacted spec); `target` carries `connection_name`, `truncated`, `collisions`,
    `existing_rows` and, for files, `{format, compression, prefix, objects, rows,
    manifest_artefact_id}`. A pack can be built without a plan or snapshot (a prepare that failed
    early).
  - HTML: the plan's page plus an errors table, the files row, a viewport meta tag and a narrow
    screen rule; every interpolation goes through one escaping helper (`None` prints as "—").
- `save_evidence` returns the row's `created_at`; `get_evidence` finds the JSON artefact by
  `meta->>'evidence_id'` (as planned) within the same tenant and job; `list_evidence` returns
  `target` as the target object or, for files, the prefix. Both read through the API pool.
- `tests/connectors/factories.py`: `evidence_inputs(mode, *, connection_name, outcome, tenant)`.
- Tests: the plan's 3 + 1 plus `test_pack_sections_follow_the_run`,
  `test_reveal_map_marks_date_shift_without_a_recoverable_anchor`,
  `test_a_failed_prepare_still_makes_a_pack` (offline) and `test_list_pages_newest_first` (db);
  the db test also reads both artefacts back and checks another tenant sees nothing:
  6 offline + 2 db = 8 (plan: 4).

## Task 22 — Run handlers, submission and job groups

- **Plan tests that could never pass, corrected (intent kept).** The in-place tests submitted a
  discovered customers snapshot unchanged, but discovery suggests `fpe_digits` for `customer_id`
  (`CUSTOMER_ID`), `det_token` for `national_id varchar(18)` and `fpe_text` for `tax_id char(10)`.
  Contract §5.3 refuses a transformed key in place (`pk_transform_in_place`, rule 8 of Task 18 —
  not weakened), a `det_token` cannot fit `varchar(18)` (`length_overflow`), and a PAN has 4 digits,
  below the FPE domain, so `fpe_text` falls back to `det_token` and fails the preflight on
  `char(10)`. `tests/connectors/test_run_modes.py` therefore reviews the snapshot first
  (`IN_PLACE_REVIEW`: key `keep`, `national_id` → `fpe_digits`, `tax_id` → `mask_partial`), as an
  owner must; `test_row_count_mismatch_fails_before_any_write` uses the same review (it would
  otherwise stop at submit). The round-trip test now compares **whole rows** (every revealed and
  kept column equals the source; the one-way `tax_id` keeps its last four characters) instead of
  only the reveal-map columns of one row.
- `dps/engine/batch.py` `reveal_row` — **a reveal can never return a wrong value silently.** The
  plan retried `det_token` only when the recorded method raised `MethodError`, but FPE inverses
  (and `phone` with a wrong kept prefix) decrypt anything without an error. Each whole value is now
  tried as `det_token` first (AES-SIV authenticates: it settles whether the value ran a fallback),
  then as its method (email: `sub` det_token, then fpe_text; phone: the token's country-code
  length, then 0–3), and a candidate is accepted only when the forward plan (`apply_plan`, same
  key, params and row anchor) maps it back to the stored token. The anchor's inputs (anchor
  column, else the key columns) are revealed first; the `date_shift` anchor is
  `_keyed_anchor(row_anchor(...), key)` (Task 16 note); free-text columns reveal their envelopes.
- `dps/layers/connectors/jobs_run.py`, finalize:
  - **A failed in_place/append job keeps its write ledger** (the plan cleared it at every
    finalize). A retry resumes from the ledger mark; with the ledger gone a resumed partition would
    restart from a checkpoint that can be one batch behind and re-obfuscate that batch. Succeeded
    and canceled jobs (not retryable) clear it. HANDOFF §8 says so.
  - The post-write sample and the ledger cleanup are best effort: a target or secret failure is
    recorded in the evidence (`post_sample: {"error": code}`, `write_ledger_cleared: false`)
    instead of retrying finalize until the job is dead-lettered without an evidence pack. The
    files manifest is not best effort (loaders need it). In place, the sample scans every column
    of the table (kept columns are output too).
  - A finalize that is retried after storing the pack reuses it (packs are append-only, one per
    job). A failed partition's code becomes the job's `error_code` when prepare set none.
- `prepare`: projected columns that are not in the accepted snapshot (added since review) are
  `snapshot_stale` unless `options.unclassified` is `keep` (the plan copied them unreviewed into
  table/append/files targets; in place they are not written). A missing connection is
  `connection_missing`, an invalid inline policy `policy_invalid`, a registry error its own code, a
  PVC path outside `DPS_EXPORT_ROOT` `target_invalid`, a storage error at prepare fails the job.
  `_spec` refuses a stored selection with binds but no `spec_enc` (`secret_unavailable`, as
  discovery does). The length check lives in `resolve_target` (Task 18).
- **Files targets** (`targets.resolve_target`): planned text-like columns are unbounded `text`
  (a file has no column length; the plan kept the source length, so a `det_token` output on
  `varchar(18)` failed the preflight with `length_overflow` — found by the parquet run test), and
  `column_types` records the written columns' types for the manifest. The `FilesWriter` columns are
  the source `ColumnInfo`s (their declared types decide Parquet `timestamp` with/without zone);
  the plan's `ColumnPlan.output_column()` carries no declared type.
- `dps/layers/connectors/submit.py`: the wrap key is validated before the job exists (the plan
  noted the gap); the redacted spec also records `consistency.caller_seed` (the evidence's
  `key_custody.returned`); `submit_discover` refuses a target-only or file connection
  (`connection_role`) and non-PostgreSQL sources in M2 (`engine_unavailable`); run selections are
  always sealed. `seal_retry_secret` answers `key_material_required` when the body has no material
  for the job's mode.
- `dps/groups.py`: names follow the connection-name pattern (`ValueError` otherwise; the API
  validates first); creating a group inserts the tenant's `dps_tenant` row like connections do.
- Counters: warn-mode verification hits are checkpointed as `verify:hits` (evidence `hits`).
  `RuntimeCache.job_ids()` added (ops/tests); `RunRuntime.close` is best effort.
- `dps/layers/connectors/evidence.py`: the HTML "Files" row is printed for files targets only
  (with the row count); found when checking the rendered page.
- Tests: `tests/connectors/helpers.py` loads each claimed partition's own job (the plan ran every
  claimed partition with the driven job), `register_pvc` points `export_root` at a per-test
  directory (keyword `base_path`), `json_from_export` is synchronous, `db_key_enc` returns the
  sealed bytes or `None`; `SourcePg.customers_table` takes `name`. Added
  `test_in_place_resume_never_applies_a_batch_twice` (the checkpoint of batch 2 is lost after its
  write: the resumed partition takes the ledger mark, and every row still reveals to its source),
  `test_in_place_refuses_a_transformed_key_at_submit`, submission tests in `tests/db/test_groups.py`
  (group key mismatch / not found, idempotent replay returns no key, a bad wrap key leaves no job,
  RSA wrapping, `connection_not_found`, `kek_unconfigured`, `key_material_invalid`, retry secrets)
  and reveal tests for fallbacks, a reversibly transformed anchor and a wrong key:
  4 offline + 4 db + 10 integration = 18 (plan: 11).
- `dps/layers/connectors/files.py` gains `manifest_bytes` (the serialisation `write_manifest`
  uses), so finalize stores the same bytes as the `manifest` artefact without reading the object
  back. HANDOFF §8 gains the PostgreSQL-target trap (ledger, `CREATE` on the target schema, in-place
  review rules).

## Review G5 r1 fixes (Tasks 18–22)

Review: `M2-G5-review-r1.md`, three blocking findings (B1–B3). Contract change: §0 amendment 2026-10-12
"the write guard is the caller's mark; same-table append keys", §4.2 (`BatchWriter.finish_partition`
signature, write-atomicity rule) and §5.3 (same-table append row), same commit.

- **B1 — a same-table `append` with an `offset` re-read and re-appended its own rows.** Prepare only
  clipped the read range to `max(key) + 1`; a shifted key in a key gap below that bound sat inside a
  partition range (probe: 41 rows, offset 100, one key 1000 above the rest → `succeeded`, 442 rows).
  `jobs_run._check_same_table_offset` (after `writer.prepare`, before the dry-run return, so a dry
  run reports it too) now requires `min(selected key) + offset >= append_upper`, i.e. every new key
  sorts above every existing key, else `pk_strategy_invalid`. `min(selected key)` is
  `Connector.key_at_offset(…, 0)` (a contract method, so M3 connectors inherit it). The review's
  looser alternative (`offset > max − min` of the table) is the same bound for the whole table; the
  bound is checked against `append_upper` rather than the `max_rows`-clipped upper, which is stricter
  but simpler to state and also rules out any collision with existing rows by construction.
  `PostgresWriter._check_generated` refuses a same-table `generate` unless the key has an owned
  sequence (`pg_get_serial_sequence`, identity included) that ascends (`seqincrement > 0`) and does
  not cycle (`seqcycle`, which would wrap into the read range — not in the review, same defect class);
  a different-table `generate` keeps the old rules (any default; the lagging-sequence check).
- **B2 — the ledger guard compared with writer-instance state (`_seen`), which a zombie and the
  re-claimed holder share through `RuntimeCache`.** `_seen` is gone. `write_batch` requires the locked
  ledger row to hold `mark.batch_no - 1` (the caller's own resume point), so a zombie's replayed batch
  and a skipped batch are both `LedgerConflict`. `LedgerConflict` moved to `base.py` (re-exported by
  `postgres.py`, name unchanged) so the files writer can raise it.
  - Consequence: an empty ledgered batch (`rows == []`) now still runs the transaction and records
    its mark (it used to return 0 without a mark, which the new guard would turn into a conflict on
    the next batch).
  - Consequence: `_run_partition` fails a partition whose ledger is *behind* its checkpoint (ledger
    rows removed mid-run) with `ledger_conflict` instead of writing; the old guard silently trusted the
    checkpoint, the new one would loop on conflicts. Not reachable in normal operation (the ledger
    commits before the checkpoint).
  - **Files writer** (the review's "spool is shared the same way"): a spool takes each batch number
    exactly once, in order, under a per-spool `asyncio.Lock` (`mark.batch_no` must follow the spool's
    last batch, else `LedgerConflict`, nothing spooled; a batch 1 starts a spool, any other number
    without a spool conflicts); every spool gets its own folder (`p<partition>-<n>`), so one holder's
    discard or `rmtree` never deletes another's files; `reset_partition` is now `async` and waits for a
    batch in flight; `finish_partition` gains the keyword `batch_no` (the caller's last batch, `0` for an
    empty partition) and uploads only a spool that ends with it, else `LedgerConflict` (a zombie can no
    longer upload — and then mark done — a spool the re-claimed holder reset and restarted). Batch `n`
    is the same rows whoever reads it, so a contiguous spool `1..n` is complete whichever holder wrote
    each batch. `BatchWriter.finish_partition` gained the optional keyword (contract §4.2);
    `PostgresWriter` ignores it.
- **B3 — a partition's first batch had no ledger row, so `FOR UPDATE` locked nothing and two holders
  both applied batch 1.** `PostgresWriter._lock_ledger` inserts `(job_id, partition_no, 0, '[]', 0)`
  `ON CONFLICT DO NOTHING` before the `SELECT … FOR UPDATE`, in the batch's transaction: a second
  holder's insert waits on the first holder's uncommitted row (unique-index wait), then finds batch 1
  and conflicts. The mark is then an `UPDATE`. `read_ledger` treats a `batch_no = 0` row as no mark (such
  a row exists only inside an open or rolled-back transaction). The insert is inside the batch's
  transaction rather than in `read_ledger`, so a holder that never read the ledger is serialised too.
- Tests (each failed before the fix; run against a `git archive HEAD` copy with the new tests, the
  files test adapted to HEAD's import location and synchronous `reset_partition`):
  `tests/connectors/test_postgres_write.py` +3 —
  `test_two_holders_sharing_one_writer_never_apply_a_batch_twice` (one writer, two `read_ledger`
  holders, in place: the zombie's batch 1 and a skipped batch 3 are `LedgerConflict`; HEAD: DID NOT
  RAISE), `test_a_first_batch_waits_for_an_open_holder_and_conflicts` (two connectors, holder A's
  batch-1 transaction held open inside `_insert`: B waits, then conflicts, 3 rows; HEAD: B finished at
  once and both applied), `test_same_table_generate_needs_an_owned_ascending_sequence` (plain default,
  descending and cycling sequences refused on the same table, an owned ascending one accepted;
  another table still takes a plain default; HEAD: DID NOT RAISE);
  `tests/connectors/test_files.py` +1 — `test_two_holders_sharing_one_writer_cannot_interleave_a_spool`
  (zombie batches after a reset, a replayed batch, the zombie's finish of a restarted spool and writes
  after the upload are `LedgerConflict`; the uploaded part holds ids 0..4 once, in order; HEAD: DID NOT
  RAISE); `tests/connectors/test_run_failures.py` +1 —
  `test_same_table_append_refuses_an_offset_inside_the_key_span` (the review's probe as a run: offset
  100 fails prepare with `pk_strategy_invalid` and leaves the 41 rows; offset 1 000 000 succeeds and
  appends each row exactly once, 82 rows; HEAD: `succeeded`). Existing files tests now number their
  batches (one wrote batch 1 twice) and await `reset_partition`.
- The review's probes against the fixed code: `probe-m2-G5_ledger.py` and `probe-m2-G5_firstbatch.py`
  pass (`conflict`, 3 rows); `probe-m2-G5_append_gap.py` now ends `failed pk_strategy_invalid` with
  the 41 rows untouched (the probe asserts the alternative outcome, success with 82 rows; refusing at
  prepare is the fix the review recommended).
- Open (not in this round's scope): the queue fences a partition by `locked_by` only, so a zombie and
  a re-claimed holder **on the same worker** both pass `jobs.checkpoint` / `finish_partition`. With
  the guards above every batch is still applied or spooled once, but a files zombie whose last batch
  went into the spool just before the re-claim reset it can still checkpoint that batch's counters
  after the re-claim's `reset_partition_progress` (an overcount; no data effect). A claim token (e.g.
  the claim's `attempts`) in the fence would close it; Task 23 (worker loop, lost-task cancellation)
  is the natural place.

## Task 23 — Worker process and retention

- `dps/worker.py` `Consumer` — **closes the open G5 item (a zombie and a re-claimed holder on the
  same worker).** The queue fences by `locked_by` only, so when a partition's lease expired and this
  same worker claimed it again while the old task still ran, both tasks passed every fenced write
  and the plan's `_parts[part_id] = task` silently replaced the old task (its done-callback then
  removed the *new* task from the map). A claim of a row whose earlier task is still running now
  cancels that task and the new task waits for it to end before its handler starts
  (`_guard(coro, predecessor)`); done-callbacks only remove their own task. Test
  `test_a_partition_reclaimed_by_the_same_worker_cancels_the_zombie_first` (events
  `first:start, first:canceled, second:start`). Same rule for job rows.
- `Consumer.run` does not cancel the claim loop mid-round (plan: all four loops cancelled before the
  drain). A cancel landing between `claim_jobs` committing and the task being spawned left the row
  leased until its lease expired (an attempt charged by the reaper); found by
  `test_a_job_without_a_handler_is_released_untouched`. The claim loop now finishes its round and
  exits (cancelled after `CLAIM_STOP_SECONDS = 10` only if the DB hangs). The **heartbeat keeps
  running during the drain** (plan: cancelled before it), so the leases of tasks that are finishing
  within `job_drain_timeout_seconds` do not expire under them; it stops before the release.
- A claimed job row whose phase has no handler is released (no attempt charged, as planned) and the
  loop then waits out the poll interval instead of being woken by its own release notification
  (otherwise a misconfigured worker spins claim/release). Logged as `no_handler` (ids, kind, phase).
- A claimed partition whose job row has vanished is released (plan: skipped, leaving it leased).
- LISTEN connection: checked every 5 s (`is_closed()`), reconnect after 5 s; the plan waited on the
  stop event only and never noticed a dropped connection (polling still covered it).
- Reaper: `QUEUE_DEPTH` gauges are set for every known state (0 when absent), so a state that
  empties does not keep its last value.
- `build_context(settings, *, audit=None)` — optional keyword (additive) so the embedded worker
  (Task 24) shares the app's sink; contract §3.5 updated in this commit. `health_app(settings,
  consumer)` is public (the ops app `serve_health` serves). `serve_health` binds `settings.host`
  (`DPS_HOST`, default `0.0.0.0`; the tests use 127.0.0.1) instead of a literal `0.0.0.0`, runs a
  `uvicorn.Server` subclass that installs no signal handlers (uvicorn 0.54's `capture_signals` would
  replace the worker's SIGTERM handler and re-raise the signal on exit), and shuts the server down
  gracefully when cancelled. `/readyz` components: `db` (`SELECT 1` on the worker pool),
  `migrations` (`verify_migrations`, every mode), `kek` (32 bytes loaded), `queue` (`claiming` /
  `draining` / `stopped`, in-flight count, role).
- `main()` exits with a one-line message (exit 1, no traceback) when `DPS_PG_HOST` or `DPS_JOB_KEK`
  is missing; the `if __name__ == "__main__"` block runs `dps.worker.main` from the canonical module
  (`python -m` loads the file a second time as `__main__`, which would give handlers a second
  `HandlerContext` class). `_amain` closes the pools in `finally` and stops the audit sink and the
  health server even when the consumer raises. The audit sink is started/stopped through an
  `isinstance(DbAuditSink)` check instead of `type: ignore`.
- `dps/retention.py`: as specified, plus: a `proposed`/`superseded` snapshot that a live
  (queued/running/waiting/paused) job still names is kept (a retried prepare re-reads it); a dry run
  ends with `outcome: "dry_run"` and counts keys only on terminal jobs it would not delete (a real
  run deletes those jobs before purging keys, so the two counts agree); `updated_at` is bumped on
  purged connections and jobs; a transient failure (DB, storage) requeues the job with backoff
  (`retention_error` or the storage code; every step is idempotent); an invalid stored spec fails it
  with `spec_invalid`. Ids are ordered by `expires_at`, and the 500-per-tenant cap is `JOBS_PER_TENANT`.
- `tests/fixtures/db.py` `worker_env`: besides the plan's variables, `DPS_HOST=127.0.0.1`,
  `DPS_PG_SCHEMA`, an empty `DPS_PG_SSL_ROOT_CERT`, `DPS_ARTEFACT_STORE=local`,
  `DPS_AUDIT_STRICT=false`, `DPS_EMBEDDED_WORKER=false`, `DPS_WORKER_METRICS_PORT=0` (tests that
  want the ops server set a free port) and `DPS_JOB_DRAIN_TIMEOUT_SECONDS=5`; a relative
  `secret_dir`/`artefact_dir`/`export_root` becomes a folder under `tmp_path`. Subprocess workers
  run with `cwd=tmp_path` so the lane's `.env` in the worktree cannot change their settings.
- `tests/db/test_worker_process.py` truncates the session database afterwards (`worker_db`
  fixture): the worker process registers itself and schedules a `_system` retention job, which
  later tests (e.g. `ensure_retention_job` in the same interval bucket) would otherwise find.
- Tests: consumer 4 (plan 2: + no-handler release, + same-worker re-claim), retention 3 (plan 1:
  + dry run, + live material kept), worker process 2 (plan 1: + refuses to start without a KEK):
  9 (plan 4).
- `HANDOFF.md` §4/§5: the worker command, its ops server, drain and retention; `DPS_HOST` is now read
  by the worker's ops server.

## Task 24 — API foundation, connections routes, app wiring

- `tests/test_app_m2.py` — **plan assertion that cannot pass at this task, adapted.**
  `test_m2_routes_answer_503_without_a_database` called `GET /api/v1/jobs`, a Task 25 route; at
  Task 24 it falls through to the SPA's 404. The test checks the connections routes here (same body:
  `{"detail": "the metadata database is not configured", "code": "db_unavailable"}`) and Task 25
  adds `/api/v1/jobs` to it. It also asserts the new readiness components (`db: unconfigured`).
- `dps/auth.py`: `resolve_tenant` checks the reserved prefix **before** the identifier shape (the
  plan: before the grant test) — `_system` does not match M0's `TENANT_ID` pattern (it must start
  with a letter or digit), so a shape check first would have answered 400 instead of 403
  `tenant_reserved`. `AuthError(status, detail, code=None)`; the glue raises
  `CodedAuthHTTPException` (an `HTTPException` with `.code`) when a code is set, and
  `coded_http_exception_handler` (registered by the app for every `HTTPException`) renders it as
  `{"detail", "code"}` and leaves every other `HTTPException` to FastAPI's handler (M0's
  `{"detail"}`, `WWW-Authenticate` kept). The handler lives in `dps.auth` (not `errors.py`) so a
  Layer 1 install without the `db`/`connectors` extras still renders the reserved-tenant refusal.
  `require_scope` and the new `require_any_scope(*scopes)` share one `_authorize` helper.
- `dps/layers/api/deps.py`: `tenant_of` also refuses (400 `tenant_invalid`) a tenant id the job
  platform cannot store: `dps_tenant.tenant_id` has `CHECK (~ '^[a-z0-9_][a-z0-9_-]{0,62}$')`
  (migration 002) while M0's header pattern admits upper case and dots, so `X-DPS-Tenant: Retail`
  would have failed inside the first insert as a 500. Additions: `principal_of`, `audited` (runs a
  body, maps its error with `to_http`, audits `ok` / `rejected` (4xx) / `error` (5xx) with the
  status, raises without the exception chain), `guarded` (the same for read-only routes, not
  audited), `page_args` (limit clamped to 1..200). `audit_action` also counts
  `dps_requests_total{route,outcome}` with the route template, and the event's tenant is `-` when
  none was resolved.
- `dps/layers/api/errors.py` `to_http`: as planned, plus `SealError` → 409 `secret_unavailable`,
  `AuditUnavailable` → 503 `audit_unavailable`, `decode_cursor`'s `ValueError("malformed cursor")`
  → 400 **`cursor_invalid`** (new code; every M2 list pages with that decoder), `ConnectorError`
  codes `no_key`, `connection_invalid` and `selection_*` → 422 (plan: 502), the `ConnectorError`'s
  `detail` (its value-free phrase) instead of `str(exc)` (`"<code>: <detail>"`), and fixed phrases for
  `StorageError` (its detail is an S3 error code). The unexpected case logs `api_error` with the class
  name. `DpsHttpError.body()` builds the wire shape; `.headers` for responses that need them.
- `dps/layers/api/routers/connections.py`:
  - the `production` tag check needs `admin` when **adding or removing** the tag (plan: adding only);
    code `admin_scope_required` (the code Task 18 uses for production writes);
  - `test`: the body's `object` takes `OWNER.NAME` or `{owner, name}`; a role the connection does not
    have is 422 `connection_role`; an unresolvable `secret_ref` or purged inline secret is a failed
    test (`error_code: secret_unavailable`) and a refused login a failed test
    (`connection_failed`), both stored as `last_test`, never a 5xx; `s3`/`pvc` targets run
    `test_store` (put + delete of a probe object);
  - `objects`/`describe`/`count` on a file store are 422 `engine_not_tabular`; an unparsable object
    name is 422 **`object_invalid`** (new code);
  - `count` binds and validates the selection **before opening the connector** (statically), then
    again against the described shape; a refusal answers `{"detail", "code", "checks"}` where the
    failing entry is the one the code belongs to (new public helpers
    `selection.check_for(code)` / `failed_checks(sel, engine, shape, code)`: re-validating the
    *unbound* selection does not always fail the same entry, e.g. a binding refusal); a non-source
    connection is 422 `connection_role`. `runs_as` is rendered by the PostgreSQL `select_sql` over the
    bound template (`None` for engines without SQL; M3 adds Oracle's); it lists the projected
    columns rather than `…`. Audit actions: `connection.create|update|delete|test|count`.
- `dps/app.py` (`_JobPlatform`): as planned, plus: readiness `db` is `unconfigured` without a
  database (and `jobs` likewise) so the components always exist; migrations are **verified in every
  mode** after `auto` applied them (a database with pending files is `db_state = down` for the
  routes and `jobs` not ready, `api-core` unaffected); the DB audit sink is created whenever the
  database is configured and auditing is on, even when the DB is down at boot (its log line is
  synchronous; flushes retry); an RLS posture problem in staging/prod raises `PostureError` (boot
  refused), locally each is logged as `rls_posture_problem`; the embedded worker shares the app's
  audit sink (`build_context(settings, audit=…)`, Task 23) and is skipped with a warning when the
  KEK is missing. The M2 routers are included only when their modules import
  (`_include_m2_routers`): a Layer 1 install without the `db`/`connectors` extras keeps serving
  the engine routes. SPA: `_dist_file` tries each path suffix inside `frontend/dist`, refuses `.`/
  `..` segments, serves `index.html` only through `_index()` (with its CSP, also for
  `/a/b/index.html`), and gives nested `assets/` files the same immutable cache header as `/assets`.
- `tests/fixtures`: `api_client` builds its settings with `api_settings(db_settings, tmp_path,
  **overrides)` (public: Task 25's e2e app reuses it) and writes the source `secret_ref` files with
  `write_source_secrets(root)` (factored out of `source_secrets`, which needs `handler_ctx` and
  therefore `migrated_db`). A third key `dps_detect` (scope `detect`) tests the scope refusal.
  `source_pg_sync` returns `SourcePgSync(dsn, schema)` and shares the schema/grant setup with
  `source_pg` (`_create_schema`).
- `tests/api/test_api_connections.py`: the PG params come from `service_url("source-pg")` (plan:
  literal port 5442; contract §0 amendment). The connection used for the target test has both roles.
  Added `test_reserved_and_upper_case_tenants_never_reach_the_database` and
  `test_a_refused_login_is_a_failed_test_not_an_error` (wrong password, unresolvable `secret_ref`,
  `count` with an unresolvable secret → 409); `tests/test_app_m2.py` adds
  `test_a_database_that_is_down_at_boot_leaves_api_core_ready`. 5 offline + 4 db = 9 (plan 4 + 2).
- `HANDOFF.md` §5: readiness `db`/`jobs`, the DB-down behaviour, coded errors, reserved and
  lower-case tenants, CORS, the embedded worker.

## Task 25 — Jobs, snapshots, groups, evidence and artefact routes

- `dps/layers/api/routers/jobs.py`: the run route is the plan's. Additions: `POST /jobs/discover`
  honours `Idempotency-Key` the same way (a key already admitted answers 200 + `Idempotent-Replay:
  true` with the original job; `submit_discover` cannot tell a replay from a new job, so the route
  looks the key up first); every control route shape-checks `Idempotency-Key` (422
  `idempotency_key_invalid`) — controls are idempotent by state and `dps_job_idem` only covers job
  creation, so they have no durable replay; `GET /jobs/{id}/partitions` answers 404 `job_not_found`
  for another tenant's job (the queue function would have returned an empty list); retry with
  `consistency` seals the resubmitted key/seed only for run jobs and needs the KEK (503
  `kek_unconfigured`). Audit: `job.create` (with `counts.objects` for discover, the job id and 200
  for a replay), `job.cancel|pause|resume|retry` with the job id.
- `dps/layers/api/routers/snapshots.py`: `PATCH` body `{"overrides": [...]}` with 1–2000 entries
  (audited with `counts.overrides`); `object=` filters `object_ref` (`OWNER.NAME`); `status` is
  validated (`proposed|accepted|superseded`, else FastAPI's 422).
- `dps/layers/api/routers/groups.py`: the name is validated by `GROUP_NAME_RE` in the body model
  (FastAPI 422) before `create_group`; `Cache-Control: no-store` on the create answer (it carries
  the key and seed); `GET /job-groups/{id}` lists at most 200 member jobs.
- `dps/layers/api/routers/evidence.py`: `GET /evidence/{id}` also answers `id`, `job_id` and
  `created_at`; the evidence list drops the packs of job kinds the key may not read (a key with
  `jobs` only does not see scan packs) after paging, so a page can hold fewer than `limit` items;
  `/html` serves the **stored** `evidence_html` artefact (the attested rendering) and re-renders
  from the pack only if that artefact is gone, with `Cache-Control: no-store` beside the plan's CSP.
  Helpers `scope_for_kind`, `require_kind_scope`, `job_kinds` and `artefacts_of` are shared with the
  artefact route; the app builds `app.state.artefacts = ArtefactService(artefact_store(settings),
  acquire=db.acquire)` at startup.
- `dps/layers/api/routers/artefacts.py`: the first chunk is read before the response starts (a
  missing object is a 404 `storage_not_found`, not a truncated 200); `Content-Length` is the
  stored size; an unknown id is 404 **`artefact_not_found`** (new code).
- `tests/test_app_m2.py`: the 503 test now covers every M2 route group (Task 24 deviation).
- `tests/api/test_api_jobs.py` seeds snapshots on its own asyncpg connection (the app's pools
  belong to the TestClient's event loop) and accepts them through the API. 7 tests (plan: 8 bullet
  groups in one module; the controls, retry with a purged key, review, groups, discover replay and
  audit are separate tests), plus `test_api_e2e.py` (1). The e2e app enables FF1 (`fpe_enabled`:
  integer keys need it; skipped when the `fpe` extra is missing) and disables NER for determinism;
  it also asserts the partitions, `GET /jobs/stats` (`workers_online == 1`, the embedded worker),
  the artefact's `Content-Disposition`, and that the returned key never appears in a later answer.

## Task 26 — Connector conformance suite and resume-after-kill tests

- `tests/connectors/conformance.py` — the env protocol gains three members the engine-agnostic
  suite needs: `make_table(…, key="none")` (the plan's "a table without a key" case had no key
  kind to ask for), `insert(obj, rows)` (the append test needs keys that clash after the offset)
  and `rejected_selections()` (the refused selections are engine syntax: SQL cases for PostgreSQL
  and Oracle via `sql_rejected_selections()`, Mongo filters for M3's Mongo binding). Tables hold
  `name`/`note` text columns with synthetic values; `make_table` keys are `id` = 1..rows,
  `(a, b)` or a mixed-case `code` (a collation order that differs from Python's).
- Tests, as specified, with these additions: keyset paging also resumes after a middle key
  (`after_key`) and checks each batch's size; selection rejections also go through the
  connector's `count` (refused before a query runs; the unknown-column case is a shape check only,
  the connector has no shape-free way to know it); the in-place crash test checks the untouched
  `note` column too and closes the partial reader explicitly; the no-values test also covers a
  refused selection carrying a literal and a bound parameter; the append test checks the exact
  ids after the write. 10 tests (plan: 10).
- `tests/connectors/test_resume_after_kill.py`, per case (the plan left these open):
  - **modes:** the table, in-place and files cases run obfuscate with one caller key; the two
    append cases run **synthesize** with one caller seed (a same-table or offset append in
    obfuscate would transform the key column before the offset, which `test_run_modes` does not do
    either; synthesize keeps the key);
  - **`table_upsert`:** the pre-created targets keep the source DDL, so the snapshot is reviewed
    (`national_id` → `fpe_digits`, `tax_id` → `mask_partial`): a `det_token` cannot fit
    `national_id varchar(18)` in an existing table (`length_overflow`, the Task 22 finding);
  - **two copies per run** for `in_place` and `append_generate_same` (both change their source);
    the kill copy gets a **clone** of the reference copy's reviewed snapshot (same columns, object
    adjusted, inserted as accepted) instead of a second discovery, whose random sample could
    classify a borderline column differently and make the digests differ for a reason unrelated
    to resuming;
  - `append_generate_same` uses an identity key whose sequence is set to the largest seeded key;
    the digest compares the appended rows (`customer_id >= 10 006 001`) without the key;
  - digests carry the row count and must cover at least 6 000 rows (an empty target would
    otherwise compare equal); `files` digests every JSONL line listed by the job's manifest
    (6 000 lines);
  - worker output goes to `<tmp>/<label>.log` (ref, kill-1, kill-2) for diagnosis; workers run with
    `cwd=tmp_path`; the env is `worker_env(...)` + `DPS_BATCH_ROWS=200` computed after the case is
    set up (the PVC case moves `export_root`).
  - extra assertions: six partitions, `rows_written >= 6000`, the evidence's verification passed.
  Measured: 6 passed in 91 s (three consecutive runs green). The tests skip without the `fpe`
  extra (integer keys need FF1 in the worker subprocess).
- `pytest -q` (the unit gate) now includes these `slow` tests (≈ 90 s): no default deselection was
  added, so every gate run exercises resume-after-kill.

## Task 27 — Demo data seeding, compose stack, image, smoke extension

- **Finding (not fixed here, open item for the detector owner): UK mobiles in the Ofcom drama range
  are not detected in free text.** The surrogate engine gives GB rows `+44 7700 900xxx`; the
  detector does not take those for phones inside text (a column of them is classified by the
  column, so `mobile` itself is transformed). With the plan's notes ("Prefers SMS on <mobile>; alt
  email …") the smoke's table run failed closed with `leakage` (17 hits at prepare: `$.notes`,
  `PHONE`, `contained`) — the per-row sweep doing its job. Measured on 400 generated customers:
  every IN/US/MX phone in notes is detected, none of the GB ones. Real numbers are valid and
  detected, so the seed keeps GB phones out of the notes (GB notes carry the alternative e-mail
  only). Nothing in detection or verification was changed. HANDOFF §8 records the trap.
- `tools/seed_sources.py`:
  - the ten tables' DDL is embedded (`TABLE_DDL`, schema-parameterised) so `--schema <test schema>`
    can create them; `tests/test_seed_sources.py::test_table_ddl_matches_the_initdb_schema` pins it
    to `docker/source-pg/initdb/01_bank_schema.sql` (comments and whitespace ignored);
  - a schema seeded with **other** `(seed, rows)` is refused (exit 3, `SeedConflict`) unless
    `--reset` (the plan only defined the matching case); tables with rows but no marker likewise;
  - `--restore-copy` (re)creates a `bank_restore` table only when it is missing or its row count
    differs from the source (or with `--reset`), so the compose one-shot does not undo in-place demo
    results on every `up`; another `--schema` uses `<schema>_restore` / `<schema>_perf` (created,
    owned by `dps_writer` when that role exists); `bank_perf.transactions` gets 1 000 rows when empty;
  - identifiers: SSNs from the surrogate engine are in the **9xx area only** (never issued as SSNs;
    the plan also named 666/000, which the engine does not produce), Aadhaar numbers start with 1
    (UIDAI never issues them) and are Verhoeff-valid, NINOs use HMRC's example prefix `QQ` (never
    issued; the surrogate engine has no NINO generator and a reshaped NINO could be a real one),
    CURPs are built from the synthetic persona with the stdnum check digit; e-mails are
    `<persona local><n>@example.{com,net,org}` (RFC 2606); PANs use the test IIN `411111` + Luhn;
    `kyc_documents.notes` carry no name (without NER a name in free text has no cue to detect);
  - the default DSN comes from `DPS_TEST_SOURCE_PG_DSN`, else `DPS_SOURCE_PG_PORT` (plan: literal
    5442; contract §0 amendment); failures print the exception class only, never the DSN;
  - measured: 20 000 customers → 368 529 rows generated in 7.4 s and loaded in 9.5 s in total.
- `docker-compose.yml`: as planned, with the lane-safe image tag on every service that runs the app
  image and `pull_policy: never` on the image-only services (they use the image `app` builds; a
  missing image fails fast instead of a registry pull); `DPS_FPE_ENABLED` in the shared env
  follows `${DPS_FPE_ENABLED:-true}` (the app and the worker must agree; plan: literal `"true"` in
  the anchor but a variable on the app); the worker keeps the app's hardening (read-only root,
  `cap_drop`, `no-new-privileges`) with a 512 MB `/tmp` tmpfs for files spools; `migrate`,
  `demo-secrets` and `seed-sources` are read-only too; app and worker do not depend on
  `demo-secrets` (a dependency on a profile service breaks the default-profile stack; secrets are
  read at job time). Header comments document `--env-file` (it replaces `.env`: a lane passes
  `--env-file .env --env-file docker/compose.sources.env`).
- `Dockerfile`: as planned; uv 0.11.21 warns "does not have an extra named `docs`/`tabular`" and
  installs the rest (verified). Image 687 MB (arm64; M0: 407 MB).
- `tools/smoke_test.py`: the nine M2 checks as planned (connections "created or reused" is its
  own check); the M2 block runs when `/readyz` lists a configured database (a database that is
  down then fails the first M2 check instead of being skipped). The connection test of
  `bank-uat` uses `bank_uat.customers_smoke` + `table`, `exports-s3` the `files` role check;
  the dry run reviews `bank_restore.contacts`' key to `keep` first (in place never rewrites keys).
  Result against the lane's stack: **all 24 checks passed** (the M0 part is 15 checks today, not
  the plan's 13), twice in a row (rerunnable).
- `HANDOFF.md`: §1 artefacts, §2 image, §3 the full stack, services rows (`migrate`, `worker`,
  `demo-secrets`, `seed-sources`), the shared compose environment and the seeding CLI, §8 traps
  (the GB phone detection gap, `--env-file` replacing `.env`).
- Tests: `tests/test_seed_sources.py` 6 (plan: 5; + the DDL drift check, the CLI) and
  `tests/connectors/test_seed_sources_pg.py` 1 covering the plan's three cases (fresh load with the
  ratios, "already seeded" unchanged, `--reset`) plus the restore copy, the CLI and the conflict exit.

## Task 28 — Benchmark, the 10-table demo and `reveal_rows`

- **Defect found and fixed: `reveal_row` revealed wrong values silently under another key.** Its
  docstring (Task 22) promised that a wrong key fails ("a candidate is accepted only when the
  forward method maps it back to the stored token"), but FF1 and the shape-keeping methods decrypt
  any token to a well-formed value under any key and the forward check maps it back under the same
  wrong key. `tests/test_tools_m2.py` found it: a row of `fpe_digits`/`fpe_text`/`email`/
  `date_shift` columns "revealed" to other plausible values with a fresh key and exit 0 (the
  existing `test_a_wrong_key_reveals_nothing` passes only because its plans include an
  authenticated `det_token` column). Fix, additive: `reveal_row(…, kid: str | None = None)` refuses
  a key whose id is not `kid` (`MethodError`, value-free) before anything is decrypted; the
  docstring now states the limits; `tools/reveal_rows.py` passes the evidence pack's
  `consistency.kid` and warns when a pack names none. A mistyped DEK with the right id is refused
  by its key check value (`key_material_from_caller`, M0), as L1's reveal does with hints. New test
  `tests/test_reveal_row.py::test_another_key_is_refused_by_the_pack_key_id`.
- `tools/m2_api.py` (new, not in the plan's file list): the small API client the benchmark and
  the demo share (connections created or reused, discover/review/accept, run, poll, count);
  `ApiError` carries the HTTP status and the API's code only. The tools put the repository root on
  `sys.path` so `python tools/<tool>.py` and `import tools.<tool>` both work (`# noqa: E402`).
- `tools/bench_connector.py`: as planned, plus `--timeout` and `--out`; the source table is filled
  from a pool of 5 000 generated customers (`tools.seed_sources.generate_rows`, seed 11) with new
  keys and no relationship manager (the bench table has no foreign key); in place runs review the
  snapshot like the run tests (key kept, `national_id` → `fpe_digits`, `tax_id` → `mask_partial`);
  synthesize/in-place is refused by the CLI (in place obfuscates). The result JSON also records the
  job id, the terminal state and `host.machine`; `values_by_method` leaves out the `col:<column>`
  counters. Default endpoints follow `DPS_TEST_*` / the port variables (contract §0 amendment).
- Measured (stack of Task 27; Apple-silicon Mac, Docker VM 12 CPUs): obfuscate table 3 004 rows/s,
  in place 3 196, files (parquet to MinIO) 2 912, synthesize table 2 021 (1 worker); obfuscate table
  4 128 with 3 workers, 4 828 with 3 workers and 12 partitions (an extra run beyond the plan's five,
  showing that throughput scales with worker replicas, not partitions per worker). Six result files
  in `docs/technical/bench/`; HANDOFF §3 "Measured throughput".
- `tools/demo_m2.py`: as planned; the group's key and seed are written with mode 0600 and a rerun
  reuses `var/demo/<group>.json` (the key is returned once; a group name that exists without its
  file gets a numbered suffix). Measured: all ten table jobs, the in-place job and the append job
  succeeded with verification passed; every orphan count 0 (eleven foreign keys checked). Customer
  ids are tokenised (FF1 under the group key) and line up in every table; account, loan and
  employee ids are not personal data for discovery and are kept, so their checks hold trivially.
  The append synthesizes `bank.transactions` into `bank_perf.transactions` through `bank-restore-rw`
  (the `dps_writer` login that owns `bank_perf`).
- `tools/reveal_rows.py`: as planned, plus `--locale` (the run's locale is not in the evidence pack;
  the compose stack uses `en_IN`) and `--policy` (default: the snapshot's policy); accepts either
  `GET /evidence/{id}`'s answer or the bare pack, and either the bare key material or a job group's
  saved file. Verified beyond the test: 25 rows of the demo's `bank_uat.customers` revealed with the
  group key equal their `bank.customers` rows column for column.
- Observed, not changed (outside this task): fallback counters are stored as
  `fallback:fallback:fpe_domain_too_small` (the engine's reason already carries `fallback:`), so the
  evidence's `fallbacks` lists the note with one `fallback:` prefix. Cosmetic; no value involved.
- Process: the tool modules were scaffolded before `tests/test_tools_m2.py` (TDD order reversed for
  the scripts); the test then failed for a real reason (the reveal defect above) before the fix.
- Tests: `tests/test_tools_m2.py` 4 (as planned) + `tests/test_reveal_row.py` +1.

## Task 29 — Console foundation (types, API groups, helpers, Vitest, shared components)

- `frontend/vite.config.ts`: besides the Vitest reference and `test` block, `build.cssTarget` lists
  browsers (`chrome105 edge105 firefox104 safari15`) so esbuild flattens the page stylesheets' CSS
  nesting (with `es2020` alone it ships native nesting, which needs Chrome 112 / Safari 16.5); the dev
  proxy follows `DPS_APP_PORT` (contract §0 lane-safe amendment; default 8400); the comment that
  routes must be one segment deep is replaced by the Task 24 suffix resolution it no longer matches.
- `frontend/src/styles/components.css` (new, flat): the shared components (EngineBadge, Sparkline,
  CheckList, HeatMap, ConfirmWriteModal, KeyMaterialCard) appear on several pages, so their rules
  cannot live inside one page's nesting block; it also carries the vault.css utilities the M2 pages
  use and `styles.css` lacks (`[hidden]`, `.sr`, `.linkbtn`, `.btn-danger`, `.btn[disabled]`,
  `.head-actions`, `.tbl-scroll`, `.tbl .s2`, `.callout.emerald|rose`, `:focus-visible`). M0's
  `select.input { appearance: auto }` is left alone. Imported in `main.tsx` with the four page files.
- Page stylesheets: nested type selectors are written `& h3`, `& dl.kv …` (valid in every nesting
  version). `&.mode-syn .only-obf` and `&:not(.mode-syn) .only-syn` are two rules: as one selector
  list esbuild folded them into `.wizard-page:is(.mode-syn .only-obf, …)`, which matches nothing
  intended. The `.eb` badge moved to components.css; the synthetic-companion rules of mockup 10 that
  were dropped also include `.two` and `.dist` (used only by the M5 article); the print rules that
  reach outside the page (chrome, tokens, `@page`) go to `styles.css` in Task 35.
- `frontend/src/lib/types.ts`: fields added so the interfaces mirror the pydantic models field for
  field: `JobProgress.throughput_mark|pages|fields|blocked_requests`, `TargetSpec.ledger_owner`,
  `RunSpec.source.partition_rows`, `consistency.field_scoped`, `options.unclassified`,
  `EvidencePack.utility`, `EvidenceOut.id|job_id|created_at` (the route answers them);
  `CountOut.runs_as` is `string | null` (`None` for engines without SQL). No exported name beyond the
  contract §14 list.
- `frontend/src/lib/api.ts`: M0's `call` is kept for the L1 call sites and now runs through `json`
  (same JSON content type and errors); errors carry `code` and `extra`; a FastAPI validation list
  becomes "field: message" (M0 showed the raw JSON); path ids are URL-encoded; `saveBlob` is
  exported (artefact download and KeyMaterialCard share it); `jobs.discover` accepts the
  `DiscoverSpec.mode` hint.
- `frontend/src/lib/jobs.ts`: `WizardDraft` is complete here (the plan fills it in Task 32) because
  `buildRunSpec`, `draftForStorage` and `specPreview`, tested in this task, read its fields. `typed`
  holds the object name the operator confirmed in the modal ('' = not confirmed), so `confirm_write`
  is `canConfirmWrite(typed, written object)`. `splitCounters` strips a doubled `fallback:` prefix
  (the Task 28 observation `fallback:fallback:fpe_domain_too_small`) and adds up duplicates. Extra
  pure helpers the pages share: `isTerminal`, `fmtCompact|Clock|When|Utc`, `shortId`, `total`,
  `writeModeTone`, `engineName`, `decisionReason`, `typeNoteText`, `checkLabel`, `errorText`,
  `methodFor|Text`, `METHOD_NAMES`, `emptyDraft`, `newDraftId`, `fqn`, `sourceObject`,
  `writtenObject`, `needsConfirmation`, `selectionOf`, `parseCount`, `legalBasisText`,
  `parseKeyFile`, `jobWriteMode`, `jobTargetText`, `LEGAL_BASES`. Test factories in `lib/fixtures.ts`.
- `frontend/src/hooks/usePoll.ts`: the contract signature; a generation counter replaces the plan's
  `alive` flag (with it, a slow answer from before a `deps` change could overwrite the new data), and
  the latest `load`/`active`/interval are read through a ref.
- `ConfirmWriteModal`: `adminScope` is `boolean | null`. No route tells the console which scopes its
  key holds (contract §9), so the page passes `null` and the modal says DPS checks the scope at Run
  job (the server answers 403 `admin_scope_required`); `false` blocks as planned. The copy says the
  key is "returned once" — "shown once" is reserved for the key card, which the Task 36 check waits
  for by that text. `KeyMaterialCard` also renders the synthesize seed (`dps-<job>.seed.json`);
  `HeatLegend` is exported beside `HeatMap`.
- Tooling: `vitest@2.1.9` brings `npm audit` advisories that concern the dev server only (vite,
  esbuild, @vitest/mocker); nothing of it ships in `dist/`.

## Task 30 — Jobs page (mockup 07) and Overview recent jobs

- `frontend/src/lib/jobs.ts`, `jobs.test.ts` (added to the commit): the page's filter logic is pure
  and tested first — `statusBucket`, `JobFilters`/`NO_FILTERS`, `ownerOf`, `filterJobs`,
  `pushSample` (3 Vitest cases, 10 in all).
- `frontend/src/hooks/useAuth.ts` (new): a stable `Auth` from `useSettings()`, shared by the pages.
- `frontend/src/styles/jobs.css` (added to the commit): `.cols-5` for the overview KPI row with the
  extra "Jobs running" tile; the Owner cell wraps long schema names.
- `frontend/vite.config.ts` (added to the commit): the dev proxy targets `127.0.0.1` (compose
  publishes on 127.0.0.1 only; Node 22 may resolve `localhost` to `::1`).
- Status filter: five buckets (running, awaiting review, completed, failed, canceled) — a canceled
  job is not a failure, the mockup has four. Engine filter and badges come from `GET /connections`
  (a job row carries no engine). "Review N columns" and "N rows sampled" come from
  `GET /snapshots?status=proposed&limit=200`, polled with the list (N = `needsDecision` columns; a
  proposed snapshot without such columns says "Review snapshot").
- Rows keep **Open** (or **Review N columns**) and add **Evidence pack** when the pack exists, so a
  finished job can be opened too (the mockup shows one action). The error box shows `errorText(code)`
  plus the job's value-free `error`; **Retry** (failed/dead run jobs) answers a 409
  `key_material_required` with a pointer to the job page, where the key file can be loaded.
- Overview: the subtitle names M2; **New job** beside **Open playground** when `jobs` is ready; the
  readiness table scrolls inside its card on phones.
- Browser check (private Playwright 1.55.0, Vite dev server proxied to this lane's stack, 57 jobs of
  the smoke/demo/bench runs): `/data-sources` and `/` render with live stats, no console errors, no
  horizontal scroll at 1440 and 390 px.

## Task 31 — Job detail page (mockup 03)

- `frontend/src/lib/jobs.ts`, `jobs.test.ts` (added to the commit): `stepperState`/`DetailStep`,
  `partitionPct`, `evidenceSoFar`, `shortType` (`character varying(120)` → `varchar(120)`, full type
  in the title) and `typeNoteShort`, tested first (4 Vitest cases, 14 in all); `methodText` shows a
  `true` param by its name (`phone · keep_country_code`).
- `frontend/src/styles/components.css`, `jobs.css`, `job-detail.css` (added to the commit): scroll
  containers (`.tbl-scroll`, `.twrap`) are positioned — an absolutely positioned `.sr` header label
  inside a scrolled table otherwise escaped it and widened the page to 512 px on a phone; the stepper
  wraps and its separators shrink; the evidence column keeps 180 px.
- Head: the title adds the engine name (`PostgreSQL · bank.customers → bank_uat.customers`); the
  line shows the write mode, the policy (`plan.policy_name`, else the spec's, else the snapshot's),
  purpose and group; a third line has the full job id and times. **Jobs** goes back to the list.
  **Evidence pack** is a link only once `progress.evidence_id` exists (before that a disabled button
  "Evidence pack (when finished)"), so the page has exactly one link with that name.
- Retry: one panel for both cases — **Retry job** without a file first; a 409
  `key_material_required` keeps the panel open with the explanation and **Load key file** (read
  with `File.text()` into component state, sent only inside the retry body as `consistency`).
- Partitions: the right column shows `batch N` while running, else a state chip, plus "lease
  reclaimed, resumed" and `#<checkpoint_fp>` (never a key); rows/s from `PartitionView`. The card is
  rendered from the start with "Partitions appear when the job has prepared its run plan." so the
  title "Run · partitions" is there while the job prepares (the Task 36 check waits for it).
- Evidence so far: a job failed with `leakage` shows "leakage caught · failed closed" even when the
  counters hold no hit (a preflight failure records the code, not a counter). The "Cross-system
  joins" tile of the mockup is M3 (job groups); its place shows Partitions done/total as planned.
- Snapshot table: kept columns collapse behind "+ N more columns kept" (a toggle); the footer names
  the snapshot as plain text, not a link (one "Evidence pack" link per page). Discover jobs show the
  snapshot list (**Review and accept** for proposed ones) and the first snapshot's table.
- `synthesize_*` type notes are hidden on obfuscate jobs (they describe the synthesize method).

## Task 32 — New-job wizard, steps 1–2 (connect, select data)

- `WizardDraft` was completed in Task 29; this task adds the reducer (`pages/wizard/state.ts`:
  `wizardReducer`, `WizardAction`, `canLeave`, `reachable`, `targetReady`, `useWizard`,
  `loadSavedDraft`, `clearSavedDraft`, `DRAFT_KEY`) and its 4 Vitest cases in `jobs.test.ts` (18 in
  all; the plan's three plus one for step gating). `lib/jobs.ts` is therefore not in this commit.
- Saved connections: the option text is the connection's name alone (production / role shown as
  chips after the choice). Observed for Task 36: Playwright for Python 1.55 `select_option(label=…)`
  takes strings only (a compiled regex raises `TypeError: 're.Pattern' object is not iterable`), so
  the plan's `select_option(label=re.compile("bank-pg-ro"))` needs the plain name; with exact option
  texts `label="bank-pg-ro"` works.
- New connection: PostgreSQL needs `host` and `database` in `params` whichever credential mode is
  used (`ConnectionSpec` rule), so both modes show host, port and database; a **Writable (In place
  and Append)** switch adds the `target` role (otherwise In place cannot be chosen later). After the
  create the fields are replaced by a note (the password is dropped from React state by the
  `created` action); an edit means a new connection.
- The step 1 target select is labelled "Target connection for Copy to a table (optional)" and the
  WHERE box "WHERE expression on <object>" (no "table" before a table is chosen): Playwright's
  `get_by_label` matches substrings, and "Table" / "Target connection" must resolve to one element
  in step 2 and step 4.
- Step 2 requires a **Check** only when a filter was typed (no filter = every row, nothing to check);
  the count's `runs_as` statement is shown as returned (it lists the projected columns, Task 24).
  *Narrowed by Review G7 r1:* this holds for Copy to a table and Export files only; In place and
  Append always count the selection (step 4 counts it when step 2 did not).
  The console has no UI for named bind parameters: literals are bound by the server
  (`parameterize_literals`), and `params` stays empty.
- `CheckList` shows the `connect` check's detail `tls`/`plain` as "over TLS"/"without TLS".
- Shell: the crumb and "Draft saved hh:mm" are in the page (the M0 topbar holds the key fields); a
  restored draft opens on the farthest step it can still show and offers "Start a new draft"; steps
  3–5 render a placeholder until Tasks 33–34.
- Browser check (private Playwright, lane stack): steps 1–2 driven by the accessible names of the
  plan (PostgreSQL, Saved connection, Test connection, Next: Select data, Owner, Table, WHERE
  expression, Check): "3 of 3 checks passed", a `FOR UPDATE` filter refused with the failing check
  and the server's message (no SQL echoed), `country_code = 'IN'` → "12,069 rows match"; no
  horizontal scroll at 1440/390 px.

## Task 33 — New-job wizard, step 3 (discover and review)

- `lib/jobs.ts`: `overridesFor(snapshot, edits)` and `ColumnEdit`; 3 Vitest cases (21 in all): only
  changed fields are sent, an anchor clears the others (including an anchor kept by an earlier
  override, which the server's per-path merge would otherwise re-apply after the new one), and a
  kept column can be reclassified / switched to the text pipeline.
- Review edits accumulate per column and are PATCHed after 400 ms of quiet; the answer replaces the
  snapshot and the edits are dropped (the server merges overrides per path). **Accept snapshot** is
  disabled while a change is unsaved. An accepted or superseded snapshot is read-only.
- Entity column: free text with a datalist of the snapshot's entity types — `GET /entities` is M1
  and this lane has no M1 (the plan's fallback). Method selects list the 17 engine methods; picking
  the snapshot's or the suggestion's method keeps its params, any other starts with no params (the
  run's `ColumnPlan` completes the defaults). The text-pipeline box is enabled for text/LOB/JSON
  columns, the anchor radio for classified columns only (`override_anchor_invalid` otherwise).
- Examples show the first stored value shape (all shapes in the title), never values.
- "Needs a decision" **Accept** marks the column reviewed in the page only (it keeps the suggestion,
  so there is nothing to send); **Change** scrolls to the review table and marks the row.
- Detector line: NER on/off comes from `/readyz` (`ner` component). Discovery sends
  `DiscoverSpec.mode` (the wizard's mode) and the sample size clamped to 50–50,000.
- `?snapshot=<id>`: the shell loads the snapshot and its connection, uses the connection's stored
  `last_test` when it passed (else tests the source again, value-free), restores owner, table and
  shape from the snapshot and opens step 3. A saved draft is not restored on a deep link.
- Browser check: discovery of `bank.customers` (sample 2,000, 16 heat-map cells, 1 decision: the
  free-text `notes`), acceptance shows "classification snapshot snap_…"; a method change on
  `bank_restore.contacts` sent exactly one override (`{"path": "$.customer_id", "method":
  {"method": "redact", "params": {}}}`), and the deep link reopened that proposed snapshot with
  "1 overrides".

## Task 34 — New-job wizard, steps 4–5 (target, confirmation, run, key card)

- `lib/jobs.ts`: `REVERSIBLE_METHODS` (the engine's reversible set), `oneWayColumns`, `planGroups`,
  `seedFingerprint` (WebCrypto; the server's first 16 hex of sha256 of the raw seed, URL-safe or
  padded base64). 7 Vitest cases (28 in all): the plan's `buildRunSpec` cases per write mode (they
  pass on the Task 29 builder: tests written for this task, red only for the new helpers), the
  one-way / plan helpers and a fingerprint value checked against `dps.secrets.seed_fingerprint`.
- The wizard shell loads the accepted snapshot once for steps 4 and 5 (key columns, one-way columns,
  rewritten-column count, plan by method). The step placeholder of Task 32 is gone.
- Step 4: In place without the `target` role shows the amber callout and keeps **Next** disabled;
  **Confirm** still opens the dialog, which explains the block (no target role) and keeps the write
  button disabled (the Task 36 check opens the dialog on the read-only `bank-pg-ro`). The modal's
  production note says DPS checks the admin scope at Run job (`adminScope = null`, Task 29). **Check
  target** runs `test {role: target, object, write_mode}` (a file store: the put/delete probe); it is
  not required to continue. Append: a text key offers a key **prefix**, an integer key an **offset**;
  Token is disabled for the same table and in synthesize mode with the reason. "Add a file store"
  creates an `s3` (bucket, endpoint, secret name) or `pvc` (base path) target-only connection.
  In-place **Commit every** sets `source.batch_rows` (empty = service default).
- Step 5: the policy select defaults to the snapshot's policy (sent only when another preset is
  chosen); the group key/seed file is read with `File.text()` into React state and checked against
  the group (`kid`, or the seed fingerprint); the Idempotency-Key is one `crypto.randomUUID()` per
  submission (kept across retries of the same kind of submission, dropped after an answer), so a
  double click cannot start two jobs. After **Run job** the form is replaced by the queued callout
  with **Open in Jobs** / **Open job** and the once-only key card; the saved draft is cleared. A dry
  run opens the job detail.
- `styles/wizard.css`: a choice card's radio now covers the card (invisible, `inset: 0`): with the
  mockup's `pointer-events: none` input, Playwright's `check()` on a card label timed out ("<span
  class=ct> intercepts pointer events"). `styles/components.css`: page-head actions take the full
  width on phones (the live job page's four buttons overflowed to 461 px).
- Browser check (private Playwright, lane stack): the plan's Task 36 wizard path end to end — In
  place on `bank-pg-ro` opens the dialog with the write button disabled, Copy to a table to
  `bank_uat.customers_console_t34` via `bank-uat`, ticket, **Run job** → "shown once" key card →
  **Open job** → "Run · partitions" live → evidence link after the run; Pause / Resume / the Cancel
  dialog on a 200,000-row bench job (4 live partitions with rows/s and checkpoint fingerprints). No
  horizontal scroll at 1440/390 px. Screenshots holding the once-only test key were deleted.

## Task 35 — Evidence pack page (mockup 10) and Reports evidence list (mockup 09)

- `lib/jobs.ts`, `jobs.test.ts` (added to the commit): `shortSha` and `packTargetText`, tested
  first (1 Vitest case, 29 in all). `styles/evidence.css` (added): print layout of the head (the
  title takes the free width, the seal at most 45 %).
- Section headings render "1 Summary" with a space between number and title (the Task 36 check
  finds them by that text). Numbering as planned: section 6 only when `fidelity`/`privacy` exist
  (M5), so an M2 pack shows 1–5, 7, 8.
- Data: besides `GET /evidence/{id}` the page reads the job (`expires_at` → "Pack kept until") and
  the pack's snapshot (column types, confidence, which columns were overridden, the kept columns);
  both are optional — without them the pack's own columns are shown. Requested by shows the key id
  the pack holds (`requested_by`); the data owner signature box names the snapshot's `accepted_by`;
  DPO and change approver are blanks.
- Verification table: the post-write sample counts findings in **kept** columns (pass when 0,
  "review" otherwise) and reports the findings in transformed columns as expected (format-preserving
  tokens still match their detectors) — `detector_hits` counts findings, not source values.
- **Download JSON** saves the `evidence_json` artefact (`dps-evidence-<pack>.json`; its sha256 is the
  printed one — checked in the browser), **Open HTML** saves the stored `evidence_html` artefact (as
  planned: a download, since artefacts are fetched with the API key), **Print or save as PDF** calls
  `window.print()`.
- `styles.css` print rules: the plan's (`.sidebar, .topbar, .no-print` hidden, `.page` unpadded,
  `section { break-inside: avoid }`) plus the mockup's black-on-white token override, `@page A4`
  margins, the shell un-gridded and scroll containers unclipped. The mockup's `@page` margin boxes
  with the job id are left out (CSS cannot take the id from the page).
- Reports: KPI tiles count the loaded packs ("+" while older pages exist); **JSON** fetches the pack
  for its artefact id, then downloads; **Open** goes to the evidence page (the mockup's PDF button
  is M5). The M5 sections are one card saying so, with no placeholder data.
- Browser check: the Task 36 path end to end (wizard → job → evidence pack sections 1–5, 7, 8 →
  Reports "Evidence packs"), the JSON download hashed to the printed sha256, the HTML download, a
  print-media PDF (A4, console chrome hidden), the Reports JSON download; no horizontal scroll at
  1440/390 px, no page errors.

## Review G7 r1 fixes (Tasks 29–35)

Both blocking findings were in the D3 write confirmation of the wizard. Each fix has Vitest cases
that failed first (`frontend/src/pages/wizard/state.test.ts`, 17 cases; 15 failed against the old
reducer with the new helpers stubbed, among them the reviewer's cases A–D on their assertions).

- **B1 — In place / Append are confirmed with a row count.** The Task 32 deviation ("a Check only
  when a filter was typed") is rejected for these two modes. Step 4 counts the selection
  (`POST /connections/{id}/count`, read-only) as soon as In place or Append is chosen and `rows` is
  unknown (an empty filter), shows "N rows match the selection" with the time, and offers **Count
  again** with the server's message when the count fails. **Confirm** stays disabled until the count
  is known; `ConfirmWriteModal` also refuses to confirm with `rows = null` (defence in depth), and
  the reducer's `confirm` action refuses a draft without `rows`. A standing confirmation therefore
  always sends `confirm_write: true` with `expected_rows`, so the server's `row_count_mismatch` guard
  is always on for these writes.
- **B2 — a confirmation is bound to what it confirmed.** `lib/jobs.ts` adds `confirmationKey(d)`
  (connection, source object, selection including filter, binds, column subset and row cap, row
  count, snapshot, mode, write mode, written object, append destination, primary-key strategy, In
  place batch size, partitions, production) and `isWriteConfirmed(d)`. The `confirm` action stores
  the key of the draft the dialog was drawn for (`WizardDraft.confirmedKey`) and refuses a key that is
  not the current draft's. After **every** action the reducer withdraws a confirmation whose key no
  longer matches (`bind` in `state.ts`), whatever the action — so `set` patches of `mode`, `pk*`,
  `batchRows`, `parallelism`, `maxRows` or the destination are covered without listing them.
  `canLeave(4)`, StepRun's blocked list, its summary and run bar, the stepper and `targetOf`'s
  `confirm_write` all use `isWriteConfirmed`. The step 5 preset is deliberately not in the key: the
  reviewed snapshot decides each column's method (`build_column_plans` takes no policy) and the
  dialog does not name the preset, so changing it would only bounce the operator back to step 4.
- Case C in the UI: choosing the other mode in step 5 withdraws the confirmation, and the wizard's
  existing "farthest reachable step" rule returns to step 4, which shows "Your earlier confirmation
  no longer applies …" (`WizardDraft.confirmLapsed`, set when an action other than Undo, a write-mode
  change, restore or reset withdraws a standing confirmation). The dialog then shows the Synthesize
  wording ("no path back"). Step 4 and step 5 say which mode the confirmation is for.
- **A row count is bound to its selection.** `countKey(d)` (connection, object, filter, binds, row
  cap; not the column subset) is stored with the count (`WizardDraft.countedKey`); the `counted`
  action carries the key of the selection that was counted and is ignored when the selection changed
  while the count ran, and any action that changes the selection drops the count. New consequence:
  changing **Max rows** in step 2 drops the count (the server caps the count at `max_rows`), so a
  typed filter needs **Check** again.
- `WizardAction` changes (no contract name; contract §14 does not list the reducer's actions):
  `counted` and `confirm` now carry `key`. New transient `WizardDraft` fields `countedKey`,
  `confirmedKey`, `confirmLapsed`. `draftForStorage` additionally drops `rows`, `countedAt`,
  `countSeconds` (a count without its filter is meaningless; a restored draft counts again) and the
  three new fields. `pkStrategyOf(d)` is extracted from `targetOf` (shared with the key).
- Two Task 34 Vitest cases built a "confirmed" draft by setting `typed` directly; that is no longer a
  confirmation by design, so they now count and confirm through the reducer (and the named-append
  case asserts `expected_rows`). 46 Vitest cases in all.
- Browser check (private Playwright 1.55 against a Vite dev server proxying to the lane app on 8400;
  read-only — the only POST was the count): `?snapshot=` deep link to the accepted
  `bank_restore.contacts` snapshot → step 4 → In place counted "40,000 rows match the selection" with
  no filter → dialog "Confirm: overwrite 40,000 rows in bank_restore.contacts" / "Write 40,000 rows
  in place" → step 5 spec target `{in_place, confirm_write: true, expected_rows: 40000}` →
  Synthesize → back on step 4 with the lapse note, **Next** disabled, spec `confirm_write: false`,
  and the dialog in its one-way wording. No page errors, no horizontal scroll at 1440 and 390 px.
- Not changed (review §5 non-blocking items, left open): `username` in the saved draft, the replay
  wording after an idempotent retry, `email` with `domain: generalize` counted as reversible,
  `targetReady('append')` ignoring the target role, StepDiscover dropping edits during a PATCH, and
  the dev-only `npm audit` advisories.

## Review G7 r2 fixes (Tasks 29–35)

- **B3 — a submitted run closes the wizard.** Before, the answer of **Run job** (the job and its
  once-only key) lived only in `StepRun`'s local state, the stepper stayed open and the confirmation
  stayed standing: one click on step 4 unmounted the key card, and step 5 re-armed **Run job** with
  the old confirmation and a new Idempotency-Key (a second in-place/append write with no new
  confirmation; `row_count_mismatch` cannot catch it because the count is unchanged). Now:
  - `state.ts` adds the `submitted` action (`{ type: 'submitted'; jobId }`) and `isSubmitted(d)`.
    `submitted` withdraws the confirmation (`NO_CONFIRMATION`, without the lapse note), drops the
    loaded key file and any password, and sets `WizardDraft.submittedJobId` (new transient field,
    also dropped by `draftForStorage`; `restore` never brings it back). While it is set the reducer
    refuses **every** action except `reset`, and `canLeave(step)` is false for every step, so no step
    change, re-confirmation, recount or unlock is possible; a second write starts from a new draft
    and a new confirmation. This holds for every write mode, not only In place / Append: the
    once-only key of a Copy to a table or Export files run is as easy to lose.
  - `JobWizard` owns the submission (moved out of `StepRun`): the Idempotency-Key, a synchronous
    in-flight guard (a double click before the re-render sends one request; the answer holding the
    key is never replaced), the request state (the stepper, **Back**, the **Edit** links and **Save
    draft** are disabled while a run is in flight) and the answer, in memory only. Once submitted,
    the new `wizard/RunResult.tsx` replaces the steps: the queued callout with **Open in Jobs** /
    **Open job** (unchanged accessible names), the key or seed card, and **Start a new job** (the
    only way back to an open draft: `reset`). The stepper shows steps 1–4 done and every step
    disabled; the head's **Cancel** / **Save draft** and the restored-draft callout are hidden; the
    rail shows the specification as submitted ("As submitted for job …", `confirm_write: true`)
    instead of the closed draft's.
  - The optional guard is implemented: while a returned key or seed is not marked "I have stored
    the key", **Start a new job** is disabled with the reason, and closing or reloading the tab asks
    first (`beforeunload`). `KeyMaterialCard` gains an optional `onStoredChange` prop for this. The
    links **Open job** / **Open in Jobs** are deliberately not held: they are how the operator
    follows the job (Task 36 clicks **Open job** right after the key card), and a `BrowserRouter`
    app cannot block in-app navigation without a data router.
  - `StepRun` takes `run: RunSubmission` (`busy`, `error`, `submit`) instead of submitting itself; a
    dry run still opens the job detail.
- Vitest: 5 new cases in `state.test.ts` (22 there, 51 in all). With only the new field and
  `isSubmitted` stubbed, 4 failed (confirmation still standing after `submitted`, the Copy to a table
  draft still open, actions still applied to a submitted draft, `submittedJobId` in the saved draft);
  the fifth (a reset draft needs a new count and confirmation) guards the way back.
- Browser check (private Playwright 1.55, a private Vite dev server proxying to the lane app on 8400;
  `POST /jobs/run` answered locally with a fake key, so nothing was written): In place on
  `bank_restore.contacts`, 40,000 rows counted and confirmed, **Run job** double-clicked → one request
  (`confirm_write: true`, `expected_rows: 40000`); the key card stays after a forced click on step 4
  (every step button disabled, no step 4 heading); no **Run job** button; a reload is held by the
  `beforeunload` prompt; **Start a new job** is disabled until "I have stored the key", then opens an
  empty step 1. The reviewer's `probe-m2-G7-resubmit-r2.py` now stops at "element is not enabled" on
  the step 4 button. **Open job** right after the key card navigates with no prompt; **Dry run**
  still opens the job page. 1440 and 390 px, no page errors, no horizontal scroll.

## Task 36 — Console browser check against the mockups

- `tests/console/test_console_m2.py`, adapted from the plan's module:
  - **endpoints** follow the lane-safe rule (contract §0 amendment): the app is
    `require_service("app")` (`DPS_TEST_APP_URL`, else `DPS_APP_PORT`), the source DSN
    `require_service("source-pg")`; both skip, or fail under `DPS_TEST_REQUIRE_SERVICES=1`. The key is
    `DPS_TEST_API_KEY` → `DPS_API_KEY` → `DPS_BOOTSTRAP_API_KEY` → the compose demo literal (as the
    tools). A module fixture checks `GET /connections` for `bank-pg-ro` and `bank-uat` and skips/fails
    with "run python tools/smoke_test.py first" instead of a 30 s select timeout.
  - **storage:** the init script puts the key in `sessionStorage` (the post-M0 O5 rule; the plan's
    `localStorage` copy is migrated by the console anyway), sets `dps.theme=dark`, removes a saved
    `dps.jobDraft` (a restored draft would open another step) and is wrapped in `try` (it also runs
    on the `file://` mockups).
  - **customer values:** names, e-mails, national ids and mobiles (values ≥ 6 characters) of the
    first 200 `country_code = 'IN'` customers — the rows the wizard selects — instead of 25 names of
    any country; read through `asyncpg` in a worker thread (Playwright's sync API keeps an event loop
    running in the test thread, so `asyncio.run` there raises). The assertion message carries a
    count only. Checked on the jobs page, step 3, the finished job page, the evidence pack and
    Reports (plan: three of them).
  - **Playwright 1.55:** `select_option(label=…)` takes strings only (Task 32 finding), so
    `label="bank-pg-ro"` / `label="bank-uat"` (option texts are the exact names).
  - **extra assertions:** the in-place write button stays disabled *after* the typed confirmation
    (`bank-pg-ro` has no target role), the evidence pack shows "Verification passed", and no
    uncaught page exception on any console page (class names only; the mockups are not counted).
  - **screenshots:** besides `<page>-desktop.png` / `-mobile.png` (dark), each page gets
    `<page>-desktop-light.png` (the DoD asks for Vault tokens in dark and light; the theme is the
    `data-theme` attribute, as in the mockups), mockups get `-mockup.png` and `-mockup-light.png`
    (`?light`), mockup 06 is shot per step through its own stepper (`06-wizard-<n>-mockup.png`, plus
    `06-wizard-4-confirm-mockup.png` with In place chosen), the job page twice (live and finished:
    `03-job-detail`, `03-job-detail-done`) and step 4 after the target is set
    (`06-wizard-4-target`). No shot is taken while the once-only key card is on screen.
  - `docs/superpowers/build/m2-console/` is git-ignored (`.gitignore`, as the plan says the executor
    does).
- Visual comparison (Step 3) — fixed in the pages, re-shot after an image rebuild:
  - Jobs: the "→" of a copy/export target sat alone on a line above a long target name
    (`frontend/src/pages/Jobs.tsx`: a no-break space joins it to the name, as the mockup shows it).
  - Job detail: the reviewed-snapshot table was wider than its card at 1440 px and clipped its
    Evidence column (`styles/job-detail.css`: the column's minimum width 180 → 136 px; it now wraps
    inside the card like the mockup).
  - Wizard step 3: the review table's entity inputs cut entity names (`CUSTOMER_I`, `DATE_OF_BIF`)
    (`StepDiscover.tsx` class `ent`, `styles/wizard.css`: `min-width: calc(18ch + 30px)` — room for
    18 characters beside Chromium's datalist arrow).
  - Reports and the evidence pack: a failed pack whose counters hold no hit (a preflight leak, for
    instance the Task 27 smoke failure) read "failed · 0 leaks caught" / "The leakage sweep caught 0
    source values". New `verificationText(passed, hits)` in `lib/jobs.ts` ("failed closed" when no
    hit was counted; Vitest case first, red on the missing helper; 52 cases in all), used by both
    pages; the evidence pack's sentence says the run failed closed before its counters held a hit.
- Visual comparison — differences kept (by scope or by design), recorded in the build report:
  M0's topbar (tenant/key fields instead of the crumb and worker pill), native `select` chevrons
  (M0), job groups / consistency panels / cross-system joins tile (M3), Oracle and MongoDB content
  (M3), the synthetic companion, fidelity/privacy and PDF (M5), the Reports filters, charts and
  audit log (M5), the in-place confirmation as a modal dialog (plan/contract §14 `ConfirmWriteModal`)
  instead of the mockup's inline card, target checks in step 4 instead of step 1 (the target is
  chosen in step 4), the step 3 review table (plan Task 33; not in the mockup) which scrolls inside
  its card at 1440 px because of its eight columns, and the FPE-fallback tile counting values (the
  progress counters hold no per-column fallback).
- Measured: `DPS_TEST_REQUIRE_SERVICES=1 pytest -q tests/console/test_console_m2.py` → 2 passed in
  ≈ 25 s (three runs, before and after the fixes); with the app unreachable the module errors
  (`app unreachable — start: docker compose up -d app`).

## Task 37 — M2 log-scan test

- `tests/test_no_values_in_logs_m2.py`, adapted from the plan's module:
  - **`print` detection is an AST check** (a call of the name `print`). The plan's substring test
    `"print(" not in source` fails on seven modules that call `seed_fingerprint(` /
    `key_fingerprint(` / `keyed_fingerprint(` (`jobs_models.py`, `jobs.py`, `groups.py`,
    `secrets.py`, `base.py`, `submit.py`, `targets.py`), none of which prints.
  - **modules:** the plan's list plus `dps/layers/api/errors.py` and `deps.py` (M2's API foundation,
    Task 24, both log) and `dps/audit.py` (M2's `DbAuditSink`); 32 modules, one case each, and a
    case that pins the list (≥ 30 modules, the core ones present). The audit line's `**event` splat
    is the one allowed splat, with `set(AuditEvent.model_fields) <= _SAFE_FIELDS` as its own case.
  - **stricter static rules:** the event literal must match the runtime's `_EVENT_NAME` (snake_case,
    no run of 4+ digits), and no stdlib `logging.getLogger(…)` / `logging.<level>(…)` call (it would
    bypass the allow-list, HANDOFF §8's access-log trap). A self-test feeds the scanner a snippet with
    each refused form (format argument, f-string event, unsafe field, non-snake event, splat,
    `print`, stdlib logger) and the two `*_fingerprint(` calls the plan's check misread.
  - **dynamic case:** the logging set-up is a fixture that restores structlog's previous
    configuration and the root level afterwards (the plan's version left JSON/DEBUG configured for
    every later test). The selection carries a distinctive literal
    (`email <> 'logscan-literal@example.invalid'`) that is scanned for — the plan's `'A%'` is too
    short to scan. Scanned values: every value ≥ 6 characters of the per-customer columns of
    **every** row of the table (corrected in the G8 r1 fix below; this entry first said the
    **selected** rows, which missed rows discovery reads), the job's DEK, the KEK setting, the literal
    and the two local passwords.
    Added: the job must succeed, write exactly the selected rows, and log `discover_done`,
    `prepare_done` and `finalize_done` (so the scan is not vacuous); the failure message gives a
    count, never a value.
- `tests/connectors/test_run_modes.py`: `run_spec(..., *, source=None, **extra)` merges `source`
  into the default source fields (as the plan asks).
- Mutation probe (scratch, not committed): a source row value logged through an allow-listed field
  (`reason=`) or through a stdlib logger, injected into `PostgresConnector.read_partition`, makes the
  dynamic case fail with "N values reached the logs" — 2 of 2 probes caught.
- Measured: `pytest -q tests/test_no_values_in_logs_m2.py` → 36 passed (32 module cases, the list,
  the scanner self-test, the audit fields, the full job in ≈ 0.8 s). No module needed a change and no
  field was added to `_SAFE_FIELDS`.

## Task 38 — Documentation, handoff, milestone gates

- **Finding fixed: the documented first run failed on the plain stack.** Since Task 27 the default
  compose profile includes `dps-db`, `migrate` and `worker`, so `/readyz` reports a configured
  database and `tools/smoke_test.py` runs its nine job checks — which need the `sources` profile's demo
  source and secrets. On a plain `docker compose up -d --build` (verified under a separate project and
  ports) the smoke stopped at "m2 connections tested — 0/4 ok" (17/18), although Layer 1 and the jobs
  API work (DoD 2: `/readyz` `db`/`jobs` ok, `GET /api/v1/jobs` 200). `tools/smoke_test.py` gains
  `DPS_SMOKE_M2=auto|on|off` (`m2_wanted(components, setting)`; default `auto` = the old behaviour,
  `off` skips the job checks with a note, `on` forces them, anything else exits with a message);
  `tests/test_tools_m2.py::test_smoke_runs_the_job_checks_where_the_stack_has_them` was written first
  (red on the missing function). The plain stack then passes `DPS_SMOKE_M2=off` 15/15. README, HANDOFF
  §1/§3 and the lane example use `DPS_SMOKE_M2=off` for the plain stack (and move `DPS_DB_PORT`
  together with `DPS_APP_PORT`, since the plain stack now publishes the metadata database too).
- `HANDOFF.md`: the plan's sections, mapped onto the existing layout — *Last verified* (date, the
  `m2` tag on top of `2b38deb`, every gate with its count); §1 the three roles of the image and the
  M2 surface; §2 the M2 image size, the development extras (`dev,fpe,db,connectors,e2e` +
  `python -m playwright install chromium`) and the console Vitest suite; §3 a *Ports* paragraph (the
  plan's 8400/5441/5442/9010/9011, worker 9400 inside the network, all lane variables), the demo,
  reveal and test-gate commands, the console check's prerequisites; the measured-throughput heading
  names the image and tools commits; §6 CORS now lists `PATCH DELETE` and `Idempotency-Key` (it still
  said `GET POST OPTIONS`); §7 the `_FILE` forms, `DPS_S3_ACCESS_KEY_ID` and the `dps/src/*`,
  `dps/tgt/*`, `dps/s3/*` secret-ref directories; §8 the stale "routes are one segment deep" trap is
  replaced by the suffix-resolution trap, and an *M2 traps* block holds the plan's list (asyncpg
  coercion, `dps_write_ledger` folding, ledger over checkpoint, `fpe_digits` on short keys, created
  tables, superseded snapshots, Homebrew `timezone=UTC`, the once-only key, `DPS_EMBEDDED_WORKER`)
  plus the review table's sideways scroll; §9 marks M2 done. *Configuration* and *Probes* already held
  every contract §10 setting (incl. `preflight_rows` and the `audit_*` ones) and the worker probes
  (Tasks 1, 23, 24), so they are unchanged; `up --wait` and `--scale worker=N` were already there.
- `README.md`: status (M2), the API reference link, the plan's three "Run it" lines with the
  `--env-file .env` note for lanes, the plain stack's smoke with `DPS_SMOKE_M2=off` (the old line said
  13 checks; it is 15), "Develop" with the M2 extras, all gates, the services/`tools/scratch_pg.sh`,
  the markers (`db pg_source s3 slow browser`) and the integration gate commands, the extras list, and
  a one-paragraph job flow.
- `docs/technical/api-m2.md`: every M2 route as built (method, path, scope, request → response,
  error codes), request bodies, the run-validation and job error codes, the job/partition state
  machine, the evidence pack fields and the measured throughput (the six results). Built from the
  routers and models, not from the plan: `POST /connections/{id}/test` answers 200 with `ok: false`
  and an `error_code` for unavailable secrets or a failed connect; ids are `grp_…` / `art_…` for
  groups and artefacts.
- Milestone gates: as planned, with the lane's `--env-file .env` before `docker/compose.sources.env`
  (HANDOFF trap) and `DPS_TEST_*` from `.env.lane`; plus the plain-stack check (DoD 2) under
  `COMPOSE_PROJECT_NAME=dps-wt-m2-plain` on ports 8470/5471, removed afterwards with
  `docker compose -p dps-wt-m2-plain down -v` (that project only). The smoke test reports **24**
  checks (15 Layer 1 + 9 M2), not the plan's 22 (Task 27 deviation).
- **Tag name `v0.1.0-m2`, not the plan's `m2`.** The lane's branch is `m2`; a tag of the same name makes
  `m2` ambiguous (`warning: refname 'm2' is ambiguous`, and `git merge m2` / `git log m2` resolve to
  the tag, which would silently pin an older commit once review fixes land on the branch). The tag
  follows M0's `v0.1.0-m0` pattern (the package version stays 0.1.0 in M2); HANDOFF and README name
  it. The `m2` tag created first was deleted at once (it was never pushed or used).

## Review G8 r1 fixes (Tasks 36–38)

- **B1 — the dynamic log scan covers every row discovery reads.** `test_a_full_job_logs_no_values`
  built its scan list from the run's selected rows only (`full_name LIKE 'A%'`, 213 values in the
  review's run), while discovery samples the whole 500-row table with no selection, so a value
  discovery logged from an unselected row passed unseen (the reviewer's probe B). The Task 37 entry's
  claim that this narrowing was stricter than the plan's `LIMIT 50` rows of any name was wrong: the
  plan's rows did reach outside the selection. Now `_scan_list` takes every row of the table, and the
  separate `written == selected` assertion stays as it was.
  - Columns scanned: the per-customer ones of the `customers` fixture (`customer_id`, `full_name`,
    `date_of_birth`, `national_id`, `tax_id`, `email`, `mobile`, `notes`), values of 6 characters or
    more. The review allowed "the five columns or all columns"; this takes the five plus the other
    per-row values (the key, which partition planning reads, the birth date and the notes). Left out,
    with the reason in the module: the shared labels `id_type`, `country_code`, `segment`,
    `kyc_status` (a few words such as `pending` that a log line may legitimately name as a state, so
    a scan for them would fail on vocabulary, not on a leak), `risk_score` (under 6 characters),
    `relationship_manager_id` (always NULL) and the two load timestamps (one value for every row).
    The reviewer's probe A scanned all columns of all rows and found 0 hits, so the product is clean
    under either choice.
  - New self-test `test_the_scan_catches_a_value_discovery_logs_from_an_unselected_row` (`db`,
    `pg_source`): it monkeypatches `PostgresConnector.sample` (discovery's read) to log the e-mail of
    the first sampled row outside the selection through the allow-listed `reason=` field, runs the
    same job, and requires the planted line to be present, the scan list to catch it, and the caught
    value to belong to an unselected row. Written first and red against the old selected-rows scan
    list ("the scan missed a value discovery logged from an unselected row"), green after the fix.
  - The job flow is shared by both cases (`_run_logscan_job`), as are `_scan_list`, `_captured` and
    `_leaked` (positions only, never values). The self-test's table and connection names have no
    underscore (`logscanleak`), because a connection name must match `^[a-z0-9][a-z0-9-]{1,62}$`.
- The tag `v0.1.0-m2` and HANDOFF's *Last verified* line (1098 passed) still describe `438cf86`; with
  this fix `pytest -q` counts one more case (1099 passed, 2 skipped).

## Review G8 r2 fix (Tasks 36–38)

- **B1 — the release tag and HANDOFF's *Last verified* line now describe the final commit.** The r1
  entry above left both on `438cf86`, so `v0.1.0-m2` lacked the r1 log-scan fix (`62cf9e3`) that is
  the DoD 9 safety evidence, against DoD 10 (the tag on the final commit) and Task 38 Step 1.
  - Every gate was re-run at `62cf9e3` on a freshly rebuilt lane stack (`up -d --build`, `sources`
    profile): `ruff format`/`ruff check`/`mypy dps` (82 files) clean; `pytest -q` 1099 passed,
    2 skipped (the `ner` extra); `DPS_TEST_REQUIRE_SERVICES=1 pytest -q -m "db or pg_source or s3"`
    159 passed; resume-after-kill 6 passed; log scan + console check 39 passed; smoke 24/24;
    `tools/demo_m2.py` 12 jobs succeeded with verification passed and 0 orphans; console typecheck,
    52 Vitest cases and build. The plain-stack check (DoD 2) was not re-run: product code, compose
    files and tools have not changed since `6ea0738`, where it passed; HANDOFF says so.
  - HANDOFF's *Last verified* paragraph names `62cf9e3` + the HANDOFF update, the new counts and the
    tag's move. A HANDOFF commit cannot carry its own hash, so it names the tagged commit by the tag
    and its parent, as the earlier line did (`2b38deb` + the documentation pass).
  - The annotated tag `v0.1.0-m2` was re-pointed with `git tag -f -a` to the commit that carries this
    entry. It had never been pushed (`git ls-remote --tags origin` lists only `v0.1.0-m0`), so
    nothing outside this repository saw the old target.
  - No repository test was added for this finding. Where a tag points is repository metadata, not
    code: a pytest that pins the tag to a commit or reads local tags would fail in a clone without
    tags and on every commit after the release. The red/green evidence is a scratch check
    (`scratchpad/build/g8fix2/check_release_tag.py`, verdicts only): it requires the annotated tag on
    HEAD, `62cf9e3` an ancestor of the tagged commit, and the tagged HANDOFF's *Last verified*
    paragraph to name `62cf9e3`, 1099 passed / 2 skipped and 159 passed. It failed 6 checks before
    the fix and passes after it.
  - While re-running the gates, a parallel `pytest -q -rs` started by the fixer collided with the
    integration gate on MinIO's `tests/big.bin` key (`test_round_trip_and_multipart` read the other
    run's random bytes). It is a harness collision, not a product defect: both runs share one bucket
    and one key by design of the test. The gate was re-run alone: 159 passed. HANDOFF's *M2 traps*
    gains "run the integration gates one at a time against a lane's stack".

## Verify r1 fixes

- **B1 — denied functions ran through PostgreSQL attribute notation** (review G3 r3 B1, carried
  until now and reproduced again at verify r1). In PostgreSQL `x.f` is the call `f(x)` when `x` is
  a range variable with no column `f`, and `(expr).f` is `f(expr)`; with `unnest(ARRAY[:q]) AS t`
  in `FROM`, `t` is a scalar of the bound type, so `t.pg_sleep`, `t.ts_stat` (a bound SQL string),
  `t.pg_advisory_lock` or `t.pg_terminate_backend` call the function with no call syntax. sqlglot
  parses these as `exp.Column` (`t.f`, `"t"."f"`, `s.t.f`) or `exp.Dot` (`(t).f`, `(t.*).f`,
  `((t)).f`, `(t).x.f`, `(CAST(… ) ).f`, `(ARRAY[…])[1].f`), never as `exp.Func`, and
  `_validate_tree` applied `DENIED_FUNCTION_PREFIXES` to `exp.Func` only. The verifier's
  `SELECT (SELECT t.pg_sleep::text FROM unnest(ARRAY[1.0::float8]) AS t) IS NULL` slept 1012 ms on
  the lane's source-pg while `validate_selection` accepted the same where clause.
  - Fix (`dps/layers/connectors/selection.py`): `_validate_tree` also refuses, with
    `selection_function_denied` and the value-free message "a denied function is called through
    attribute notation (name.function)", the `this` identifier of a **qualified** `exp.Column`
    (any of `table`/`db`/`catalog` set) and the `expression` identifier of an `exp.Dot` whose
    lower-cased name starts with a `DENIED_ATTRIBUTE_PREFIXES` entry. It runs on every node of the
    tree, so `validate_selection`, `parameterize_literals`, `compile_where`, `compile_query` and
    the console checklist (`selection_checks` → `no_into_lock_procs`) all refuse it. An
    unqualified name stays allowed: PostgreSQL never reads it as a call (`column "pg_sleep" does
    not exist`), and `pg_sleep > :a` on a real column `pg_sleep` is a column.
  - `DENIED_ATTRIBUTE_PREFIXES` is `DENIED_FUNCTION_PREFIXES` with the contract's `lo_*` narrowed
    to the real large-object functions (`LARGE_OBJECT_FUNCTIONS`: `lo_close`, `lo_creat`,
    `lo_create`, `lo_export`, `lo_from_bytea`, `lo_get`, `lo_import`, `lo_lseek*`, `lo_manage`,
    `lo_open`, `lo_put`, `lo_tell*`, `lo_truncate*`, `lo_unlink`, `loread`, `lowrite`; read from
    `pg_proc` on the lane's PG16 plus the `lo` extension), so a qualified column such as
    `a.lo_limit` is not refused. Calls keep the contract's `lo_*` prefix unchanged. Every other
    prefix applies in full, so a qualified column whose name starts with a denied prefix
    (`a.utl_rate`, `c.ts_status`, `x.dbms_flag`) is now refused (stricter); reference such a
    column unqualified. HANDOFF §8 says so.
  - Live check on the lane's source-pg (PG16.15), each with `unnest(ARRAY[0.3::float8]) AS t`:
    `t.pg_sleep`, `(t).pg_sleep`, `(t.*).pg_sleep`, `"t"."pg_sleep"`, `T.PG_SLEEP` and
    `((t)).pg_sleep` each slept ~305 ms; `s.t.pg_sleep` and `(t).x.pg_sleep` are errors in
    PostgreSQL. The validator now refuses all eight.
  - Tests (`tests/connectors/test_selection.py`, written first; 50 failed with `DID NOT RAISE`
    before the fix): `test_denied_functions_called_through_attribute_notation_are_refused_at_every_stage`
    ×48 (`pg_sleep`, `ts_stat`, `crosstab`, `pg_advisory_lock`, `dblink_exec`,
    `pg_terminate_backend` × `t.f`, `(t).f`, `(t.*).f`, `"t"."f"`, `s.t.f`, `T.F`, `((t)).f`,
    `(t).x.f`; each in three where forms — scalar subquery, `EXISTS … WHERE`, `IN (SELECT …)` — and
    three query forms — projection, scalar subquery in the where, `JOIN … ON`; each through
    `validate_selection` without and with a shape, `parameterize_literals` and
    `compile_where`/`compile_query`, asserting the code and that the message holds neither the
    function name, the bound SQL nor the selection's table),
    `test_attribute_notation_on_expressions_and_long_paths_is_refused` (field selection on a cast,
    `ROW`, an array element, a five-part name, a subscript, a `::` cast, `ORDER BY`, a window, a
    CTE, `coalesce`, and the other SQL-string functions), and
    `test_large_object_functions_are_denied_by_name_in_attribute_notation` (17 names in three
    forms; `lo_*` calls still refused; `a.lo_limit`, `a.loan_ref`, `a.lower_band` allowed); guard
    `test_unqualified_columns_named_like_denied_functions_are_not_calls` (passes before and after).
    No existing test changed. 137 tests in the file (was 86).
  - Defence in depth: see the Task 15 entry above (read-only transaction, statement timeout, pool
    reset with `pg_advisory_unlock_all()` and `RESET ALL`, pinned by a new integration test). It
    is not a substitute for the fix.
  - Residual (unchanged, for the contract owner): a name deny-list cannot cover user-defined
    functions, operators backed by functions, casts or domain checks in a source database; the
    §5.2 allow-list recommendation of the G3 reviews stands.
- **B2 — the console's default In place on a table whose key the snapshot transforms could not
  run** (`frontend/src/pages/wizard/StepTarget.tsx`). Discovery maps the entity key
  `bank_restore.customers.customer_id` to `fpe_digits`; step 4 still said "customer_id is the
  update key and is not changed", step 5 said "Ready to run · all checks passed", and Run job got
  422 `pk_transform_in_place` (contract §5.3: in place refuses a transformed key column). The run
  spec carries no per-column methods (the accepted snapshot decides them) and an accepted snapshot
  takes no overrides, so the console cannot send a key forced to keep; it now says so and leads the
  operator to a snapshot that keeps the key. Contract and server rule unchanged.
  - `lib/jobs.ts`: `runMethodFor(col, mode)` decides a column's run method as the server's
    `targets.method_for_column` does (synthesize: the accepted synthesize method, else its
    suggestion, else `surrogate`; the display helper `methodFor` falls back to keep and is
    unchanged), `keyMethodsOf(snapshot)` lists the `shape.key` columns with their run method per
    mode, and `inPlaceKeyRewrites(d)` the key columns an In place run would rewrite in the draft's
    mode (`null` while unknown). The draft gains `keyMethods` (names and method names only), set by
    the `snapshot` action, cleared with the selection, by a new discovery and by `restore`, and
    never saved (`TRANSIENT`): it always comes from the server's snapshot.
  - `state.ts`: `targetReady` for `in_place` also needs `keysKeptInPlace(d)` (known and every key
    kept), so Next, the stepper's step 5 and Run job stay closed; the `confirm` action refuses an
    in-place confirmation while a key is rewritten. Switching the mode at step 5 to one that
    rewrites the key closes step 5 and the shell returns to step 4, as a withdrawn confirmation
    already did.
  - Step 4: the key check states what the spec will do — a failed check "customer_id is the update
    key, but the accepted snapshot rewrites it (fpe_digits in Obfuscate mode)" with the server's
    code and a **Change it in step 3** link, a pending check while the snapshot is read, and the
    previous "is not changed" statement only when every key is kept. The confirmation dialog shows
    the reason and keeps its write button disabled; the footer hint says "In place needs the key
    kept (step 3)". Step 5 lists the same reason among the items to resolve.
  - Step 3: **Run again** is enabled for an accepted or superseded snapshot too (it was off unless
    the snapshot was proposed, although the superseded callout already asked for a new run), the
    accepted callout says how to change a method, and a key column whose method is not keep in the
    current mode carries the note "keep it to write in place". An accepted snapshot stays read-only.
  - `JobWizard.tsx`: the accepted snapshot that steps 4–5 read is also dispatched as `snapshot`, so
    the key methods are known on a restored draft or a `?snapshot=` deep link, and a snapshot found
    superseded closes step 4 instead of being fetched again in a loop.
  - Tests (written first): Vitest `in place keeps the key columns (verify r1)` ×5 in
    `state.test.ts` (a rewritten key keeps In place closed and refuses the confirmation; other write
    modes are not gated; a new discovery whose accepted snapshot keeps the key confirms and sends
    `confirm_write`; the synthesize fallback to `surrogate` closes step 5 after a mode switch; an
    unknown key method keeps In place closed and is neither saved nor restored) — 4 failed before
    the change — and `jobs.test.ts` `reads the key columns' run methods as the server does`.
    `fixtures.ts`' default draft now carries an accepted snapshot that keeps its key, and
    `a second run starts from a new draft…` re-reads the key methods before its final confirmation
    (the shell does that on a rebuilt draft); no assertion was loosened. 58 Vitest cases (was 52).
    Browser case `test_in_place_on_a_table_with_an_entity_typed_key` in
    `tests/console/test_console_m2.py` (lane stack, `bank-restore-rw`): discovery of
    `bank_restore.customers`, In place shows the failed key check, Next disabled, the dialog's
    write button disabled with the typed name; **Change it in step 3** → **Run again** → the key row's
    note → method `keep` → saved override → accept; step 4 shows "customer_id is the update key and
    is not changed", the confirmation is accepted, step 5 shows "all checks passed", and
    `POST /jobs/run` (a dry run, so the restored copy is not written) answers below 400 and opens
    the job. Screenshots `06-wizard-4-in-place-key-*` (git-ignored); no horizontal scroll at 390 px.
- Release (DoD 10): every gate was re-run at `fca6cff` on the rebuilt lane stack (`sources`
  profile): `ruff format`/`ruff check`/`mypy dps` (82 files) clean; `pytest -q` 1152 passed,
  2 skipped (the `ner` extra); `DPS_TEST_REQUIRE_SERVICES=1 pytest -q -m "db or pg_source or s3"`
  161 passed; resume-after-kill 6 passed; log scan + console check 40 passed; smoke 24/24;
  `tools/demo_m2.py` 12 jobs succeeded with verification passed and 0 orphans; console typecheck,
  58 Vitest cases and build; app and worker `/readyz` 200. HANDOFF's *Last verified* paragraph
  names `fca6cff`, and the annotated tag `v0.1.0-m2` was re-pointed (`git tag -f -a`, never pushed)
  from `99ec8c0` to the commit carrying this entry. The plain-stack check (DoD 2) is carried from
  `6ea0738`: compose files, the image build and tools have not changed since.

## S1-M4 post-merge verification r1 — a canceled run counts every batch its target holds (regression r1 B1, 2026-10-15)

M2 code, found on main after the M4 merge (`jobs_run.py` byte-identical to `d2fdc3f`): `jobs.cancel` flips
every running partition to `canceled` at once, and `checkpoint()` was fenced on `state = 'running'`. A holder
that had already committed a batch to the target (with its ledger mark, for a ledgered plan) when the cancel
landed found its checkpoint matching nothing and abandoned: the batch never reached the partition's
`rows_written` / `batch_no`, the job's progress, the evidence pack, Reports or `dps_tenant_usage`, and the cancel
finalize of a ledgered plan (`in_place`, `append`, key-less Oracle `table`) then cleared the ledger mark that
recorded it. Live, 3 of 3: packs 1 000 / 3 000 / 4 000 rows, targets 2 000 / 4 000 / 5 000.

| File | Change | Why (as built) |
|---|---|---|
| `dps/jobs.py` `checkpoint` | fenced on the holder (`locked_by = worker`) and `state IN ('running', 'canceled')`: the holder records the batch it committed under its lease (usage charged in the same transaction), then `partition_control` answers `cancel` and it abandons, as before; another worker and an abandoned partition (`locked_by` NULL) still match nothing | option (a) of the finding: the evidence, the job and Reports count what reached the target; `_finalize` already waits for canceled partitions with live leases (`_live_canceled`) |
| `dps/jobs.py` `record_ledger_batches` (new), `dps/layers/connectors/jobs_run.py` `_clear_ledger` / `_finalize` | before a cancel (or success) finalize clears a ledgered plan's write ledger, every **canceled** partition's ledger mark is read; a mark ahead of the partition's checkpoint (a holder that died or lost its lease between its write and its checkpoint) is recorded first — the ledger is authoritative, as on a resume — only for a partition no live holder owns, with its usage; the finalize then re-reads the partitions and the job's progress for the pack | clearing deleted the only record of such a batch |
| `tests/db/test_partitions_queue.py` `test_partition_control_and_cancel` | the holder's checkpoint after cancel now records (another worker's does not; after `abandon_partition` nobody's does) | the old assertion encoded the defect |
| contract §3.1 (amendment 2026-10-15), §3.2 signature list; `docs/technical/api-m2.md` (Cancel) | the rule above | contract kept in step |

Tests first (`tests/connectors/test_run_cancel_counts.py`, the writer patched to cancel the job right after it
commits a batch, two partitions running): a PG `table` load, a ledgered `in_place` run, and a ledgered run
whose holder dies before its checkpoint (lease expired): pack `counts.rows_written` = job progress = sum of
partitions = rows in (or changed in) the target, ledger cleared, `dps_tenant_usage` charged by exactly those
rows. On `8ea220a`: 3 failed (0 vs 400, 200 vs 600, 0 vs 400).

