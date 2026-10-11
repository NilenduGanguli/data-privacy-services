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
