# S3 setup — fsec lane worktree

Date: 2026-10-14. Result: **READY**.

## Worktree
- `/Users/neelu/document_intelligence/dps-wt-fsec`, new branch `fsec` from `main` at `64c46e3`
  (`git worktree add ... -b fsec main`, first try). No commits made; `git status` clean.

## Git-ignored lane files
The shared exclude file (`git rev-parse --git-path info/exclude` = `data-privacy-services/.git/info/exclude`)
already contained `lane.sh`, `.env`, `.env.lane`, `.venv/`; nothing added. `.gitignore` untouched.

- `.env` (compose interpolation; also read by `dps.config.Settings` via `env_file=".env"`):
  DPS_IMAGE_TAG=fsec, DPS_APP_PORT=8416, DPS_DB_PORT=5483, DPS_SOURCE_PG_PORT=5484, DPS_MINIO_PORT=9070,
  DPS_MINIO_CONSOLE_PORT=9071, DPS_ORACLE_PORT=1526, DPS_MONGO_PORT=27022, DPS_OCR_PORT=5066,
  DPS_DEMO_SITE_PORT=8426, DPS_TOKENIZER_MOCK_PORT=8436, DPS_WORKER_METRICS_PORT=9416.
  All 11 host ports were free (lsof LISTEN check) and not used by any other lane `.env`.
  Every other `${...}` in main's docker-compose.yml is non-port config with a safe default; left at defaults.
  `docker compose config --quiet` succeeds.
- `.env.lane`: m3 format, exports the same ports (except DPS_WORKER_METRICS_PORT) plus the contract §12.2
  DPS_TEST_* variables (APP_URL :8416, PG_DSN :5483/postgres, SOURCE_PG_DSN :5484/bank, S3 :9070,
  ORACLE 127.0.0.1:1526/FREEPDB1 system, MONGO :27022 directConnection, OCR :5066, DEMO_SITE :8426).
- `lane.sh`: copied from dps-wt-m3, chmod +x.

### Deviations from the task text (deliberate)
- **DPS_PG_PORT and DPS_EXPORT_ROOT not set.** Neither is interpolated by main's docker-compose.yml.
  DPS_PG_PORT is the *app's* metadata-DB port setting (`Settings.pg_port`); exporting it broke
  `tests/test_config_m2.py::test_m2_defaults_keep_l1_database_free` (5483 != default 5432). Removed.
- **DPS_WORKER_METRICS_PORT is in `.env` but not exported by `.env.lane`**: it is an app setting
  (`Settings.worker_metrics_port`), not a compose variable on main; exporting it fails the same defaults
  test (9416 != 9400). The test uses `_env_file=None`, so the `.env` entry is harmless.
  If a later branch's compose interpolates it, `.env` already carries the lane value.

## Python env
- `uv venv --python 3.12 .venv`; `./lane.sh uv pip install -e ".[ner,fpe,vertex,docs,db,connectors,uitest,e2e,tabular,dev]"`.
  Every extra except `synth-ml` (mostlyai[local]: heavy torch stack, "never in an image"; its tests
  `importorskip("mostlyai")`, and the main checkout's venv does not have it either). Import check of
  dps, scipy, presidio_analyzer, fastfpe, oracledb, pymongo, asyncpg, playwright: OK.

## Frontend
- `cd frontend && npm ci` (real node_modules, no symlink) then `npm run build`: built OK
  (only the usual >500 kB chunk-size warning).

## Verification
- `./lane.sh pytest -q -m "not integration"`: **2136 passed, 230 skipped, 259 deselected** in 46 s
  (log tail: `/Users/neelu/.cache/dps-build/reports/S3-setup-pytest.log`).
- Docker: `docker info` returned "Error reading remote info: EOF" at setup time (daemon still coming up);
  not needed for the non-integration suite. No containers started; no other project touched.
