# DPS M2 — Jobs + PostgreSQL — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship milestone M2 of Data Privacy Services: a Postgres-backed job platform that connects to a PostgreSQL source, discovers and classifies its columns, lets an owner review the classification, and runs a partitioned, checkpointed, resumable masking job in any of the four write modes (`in_place`, `append`, `table`, `files`) in obfuscate or synthesize mode, ending every run with a hashed evidence pack — operated from the console's Data sources & jobs pages, demonstrable end to end on a seeded 10-table retail-bank schema, and proven by a resume-after-kill test for every write mode.

**Architecture:** One image, three roles. `app` (FastAPI, the M0 process) gains the metadata database (`dps/db.py`, asyncpg, RLS bound per checkout through the `app.tenant_id` GUC), a durable Postgres job queue (`dps/jobs.py`: `FOR UPDATE SKIP LOCKED` claims, leases, fenced writes, reaper, dead-letter, per-tenant caps — the Document AI Services design), the connection registry, the selection validator (`sqlglot`), and the `/api/v1` jobs surface. `worker` (`python -m dps.worker`) claims job rows (`discover`, run `prepare`/`finalize`, `retention`) and run partitions, reads the source through the `Connector` protocol (`PostgresConnector`: read-only transactions, statement timeout, keyset paging), transforms rows through `BatchTransformer` (the only L2 → engine bridge; the M0 engine stays the only transformer), writes through a `BatchWriter` that commits each batch together with a write-ledger mark in the target, and checkpoints after every committed batch. `migrate` (`python -m dps.migrate`) owns DDL. Artefacts (evidence HTML/JSON, manifests) go through `ArtefactService` to a local directory or MinIO/S3. The console adds the Jobs list (mockup 07), Job detail (03), the five-step New-job wizard (06), the Evidence pack page (10) and the evidence list of Reports (09).

**Tech Stack:** Python 3.12, FastAPI, pydantic v2, asyncpg (`db` extra), sqlglot + pyarrow + boto3 (`connectors` extra), cryptography (AES-256-GCM sealing), the M0 engine; PostgreSQL 16 (metadata `postgres:16-alpine`, demo source `postgres:16`), MinIO; React 18 + TypeScript + Vite (Vitest for console helpers), Playwright 1.55.0 (Python, `e2e` extra) for the console browser check. Tooling: uv, ruff, mypy, pytest (+ pytest-asyncio, pytest-timeout), Docker Compose.

**Spec:** `docs/specs/2026-10-11-data-privacy-services-design.md` — §4 (processes, statelessness), §5.7 (verification and evidence), §7 (Layer 2: lifecycle, selection, write modes D3, discovery, execution, PostgreSQL engine notes, credentials, throughput), §9 (Data sources & jobs page), §10 (cross-tenant access, connector credentials, writing to production, audit, retention) and milestone M2 in §11.

**Contract (binding):** `docs/superpowers/plans/2026-10-11-m1-m5-contract.md` — §1 (M2 paths), §2 (DB, roles, DDL, RLS), §3 (job model, queue, workers), §4 (connector protocol), §5 (models and validation), §6 (engine bridge), §7 (evidence), §8 (scopes, errors, audit), §9 (M2 routes), §10 (M2 settings), §11 (compose), §12 (tests), §13 (artefacts), §14 (console 06/07/03/10/09-list), §15 (M2 owns). Every name, column, route, port and setting below is the contract's. Where this plan **adds** something (a defaulted field, a helper function, an extra index, a stricter rule) it says so in place and lists it in "Open contract questions" at the end. **Contract change: none** — this plan does not edit the contract file; the open questions are for the contract owner.

**Depends on:** M0 complete (all 28 tasks green, tag `v0.1.0-m0`). M2 does not depend on M1. Several files are shared with M1 (`pyproject.toml`, `dps/config.py`, `dps/posture.py`, `dps/app.py`, `docker-compose.yml`, `Dockerfile`, `tests/conftest.py`, `frontend/src/App.tsx`, `frontend/src/lib/{api,types}.ts`, `HANDOFF.md`). Every edit in this plan to a shared file is **additive** (new fields, new list entries, new routes); if M1 has already landed, keep both sets of additions.

---

## Conventions for every task

- Work in `/Users/neelu/document_intelligence/data-privacy-services` (its own git repo, branch `main`). Never run git against the parent `~/document_intelligence` repo.
- Environment: `source .venv/bin/activate && uv pip install -e ".[dev,fpe,db,connectors,e2e]"` (Task 1 adds the extras).
- **Per-task gates** (contract §0.4): `ruff check dps tests tools && ruff format --check dps tests tools && mypy dps && pytest -q`. When `frontend/` changes: `cd frontend && npm run typecheck && npm test && npm run build`.
- **Services for tests.** Unit tests are offline. Tests marked `db` need the metadata Postgres on `127.0.0.1:5441`; `pg_source` needs the demo source Postgres on `127.0.0.1:5442`; `s3` needs MinIO on `127.0.0.1:9010`. Start them with `docker compose up -d dps-db` and `docker compose --profile sources up -d source-pg minio minio-init` (from Task 3/15/11 on), or run `tools/scratch_pg.sh start all` (Homebrew PostgreSQL 16, no Docker). Without the services those tests **skip** with the start command in the reason; with `DPS_TEST_REQUIRE_SERVICES=1` they **fail** instead. A task's "Expected" counts assume the services are up.
- Type hints on every function; Google-style docstrings on public ones; no `Any` unless unavoidable; line length 100.
- **Never log, print, return in an error, store in a job row, an evidence pack, a metric label or an artefact name: a customer value, a key/DEK/seed, a token-to-original mapping, a password, a connection string with credentials, a selection literal.** Keys of source rows are customer data: they live only in `dps_job_partition.{lower_key,upper_key,last_key}` and the target's write ledger. Tests assert this where it matters; Task 37 adds the M2 log-scan test.
- Engine rule: L2 never implements a masking method; it builds `Plan`s and calls `apply_plan` (Task 16).
- **Git** (strict, contract §0.5): commits are authored by the configured global identity. **No `Co-Authored-By` or any other trailer.** Conventional commits. Other agents may work in the same tree: stage and commit only the task's own paths with an explicit pathspec — `git add <paths> && git commit -m "<msg>" -- <paths>`. Never `git add -A`, `git add .`, `git commit -a`, `git stash`, `git reset`, `git checkout --`, `git clean`. If `.git/index.lock` exists, wait 3 s and retry (up to 10 times).
- `HANDOFF.md` is updated in the same commit as any build/run/config change (contract §0.6); Task 38 does the full pass, earlier tasks that change compose or settings add their line to the relevant HANDOFF table.

## M0 touch points (changed together with their tests)

| M0 module | Change | Task |
|---|---|---|
| `dps/config.py`, `dps/posture.py` | M2 settings (contract §10) + posture checks that apply only when `pg_host` is set | 1 |
| `dps/audit.py` | `AuditEvent` gains defaulted `action`, `status`, `job_id`, `counts`; new `DbAuditSink` | 10 |
| `dps/layers/api/common.py` | audit `route` is the route **template**; `AuditUnavailable` → 503 when `audit_strict` | 10 |
| `dps/engine/jsonpath.py` | public `child_path(parent, key)` (wraps `_segment`) | 16 |
| `dps/engine/transform/apply.py` | `ApplyOptions.anchor: str | None = None`; `apply_plan` uses it when set, else `anchor_for` (contract §6.2) | 16 |
| `dps/engine/service.py` | public `PrivacyEngine.detector(policy, locale)` and `PrivacyEngine.surrogate_engine(seed, locale)` | 16 |
| `dps/engine/transform/methods.py` | `_dictionary` without a key uses `sha256`, not Python's per-process-randomised `hash()` — L2 runs one job across several worker processes | 16 |
| `dps/auth.py` | `require_any_scope(*scopes)` dependency factory | 24 |
| `dps/app.py` | DB lifecycle + readiness `db`/`jobs`, M2 routers, `DpsHttpError` handler, CORS methods `PATCH`/`DELETE`, nested SPA routes resolve `…/assets/<file>` and root files (multi-segment console routes, see Task 24) | 24, 25 |
| `dps/observability.py` | `_SAFE_FIELDS` additions (ids, counts, phases) and M2 metrics | 8 |
| `frontend/src/App.tsx`, `frontend/src/lib/{api,types}.ts`, `frontend/src/pages/Overview.tsx`, `frontend/src/styles.css` | routes, typed API groups, recent jobs, M2 styles | 29–35 |

## File structure (what M2 creates or changes)

| Path | Responsibility |
|---|---|
| `pyproject.toml` | extras `db`, `connectors`, `e2e`; markers `db pg_source s3 slow`; `pytest-timeout` |
| `dps/config.py`, `dps/posture.py`, `.env.example`, `.gitignore` | M2 settings and posture (contract §10); `var/` ignored |
| `dps/ids.py` | `new_id(prefix)` → `<prefix>_<26-char Crockford ULID>` |
| `dps/secrets.py` | KEK loading, AES-256-GCM `seal`/`open_sealed`, `resolve_secret_ref`, `seed_fingerprint` |
| `dps/db.py`, `dps/migrate.py`, `dps/migrations/001…009_*.sql` | pools, RLS GUC binding, checksummed advisory-locked runner, RLS posture, audit partitions |
| `docker/dps-db/initdb/01_roles.sql`, `tools/scratch_pg.sh` | local roles and DB; Homebrew PG16 scratch clusters on 5441/5442 |
| `dps/jobs_models.py` | connection/selection/target/run/discover/retention specs, `JobSecret`, `SealedSelection`, `JobProgress`, snapshot models, `SpecError`, `redact_spec` |
| `dps/jobs.py` | the durable queue: jobs + partitions, claims, leases, fencing, finalize transition, reaper, controls, stats |
| `dps/audit.py` (modify) | `DbAuditSink`: synchronous log line, then batched per-tenant inserts into `dps_audit_log` |
| `dps/storage.py` | `ObjectStore` (`LocalObjectStore`, `S3ObjectStore`), `ArtefactService`, `target_store` |
| `dps/connections.py` | the connection registry (CRUD, sealed inline credentials, `secret_ref`, last test) |
| `dps/snapshots.py` | classification snapshots: create, list, override, accept (supersede) |
| `dps/groups.py` | job groups (key/seed returned once; only `kid` + seed fingerprint stored) |
| `dps/layers/connectors/base.py` | `ObjectRef`, `ColumnInfo`, `TableShape`, key and value codecs, `Connector`/`BatchWriter` protocols |
| `dps/layers/connectors/selection.py` | sqlglot validation, identifier rules, literal parameterisation, templates, Mongo filter allow-list |
| `dps/layers/connectors/partitions.py` | key-space partitioning from DB-ordered key samples |
| `dps/layers/connectors/registry.py` | `open_connector(conn, settings, *, action, …)`, `open_target_store` |
| `dps/layers/connectors/postgres.py` | `PostgresConnector` (read side) and `PostgresWriter` (in_place/append/table, ledger, precheck) |
| `dps/engine/batch.py` | `ColumnPlan`, `BatchTransformer`, per-row verification — the L2 → engine bridge |
| `dps/layers/connectors/discovery.py` | per-column classification from a sample; `discover_job` handler |
| `dps/layers/connectors/targets.py` | `validate_run`, `RunPlan`, `ResolvedTarget`, PK strategies, created-table DDL types, length checks |
| `dps/layers/connectors/files.py` | CSV/Parquet/JSONL part files per partition + manifest over `ObjectStore` |
| `dps/layers/connectors/evidence.py` | `EvidencePack` build, hash, printable HTML, store, list |
| `dps/layers/connectors/jobs_run.py` | `prepare`, `run_partition`, `finalize` handlers + per-job runtime cache |
| `dps/worker.py`, `dps/retention.py` | `python -m dps.worker` (claim/heartbeat/reaper/listen/dispatch/drain, health server); retention job |
| `dps/layers/api/{errors,deps}.py`, `dps/layers/api/routers/{connections,jobs,snapshots,groups,evidence,artefacts}.py` | `/api/v1` M2 routes (contract §9) |
| `docker-compose.yml`, `Dockerfile`, `docker/entrypoint.sh`, `docker/source-pg/initdb/01_bank_schema.sql`, `docker/compose.sources.env` | `dps-db migrate worker source-pg minio minio-init` (+ `demo-secrets`, `seed-sources` one-shots) |
| `tools/seed_sources.py`, `tools/bench_connector.py`, `tools/demo_m2.py`, `tools/smoke_test.py` (modify) | demo data, measured rows/s, the 10-table demo, smoke extension |
| `frontend/src/lib/{api,types,jobs}.ts`, `frontend/src/hooks/usePoll.ts`, `frontend/src/components/{StateChip,Sparkline,CheckList,HeatMap,ConfirmWriteModal,KeyMaterialCard}.tsx`, `frontend/src/pages/{Jobs,JobDetail,JobWizard,EvidencePack,Reports}.tsx`, `frontend/src/pages/wizard/*.tsx` | console pages 07, 03, 06, 10 and the 09 evidence list |
| `tests/fixtures/{services,db,sources}.py`, `tests/db/**`, `tests/connectors/**`, `tests/api/**`, `tests/console/**`, `tests/test_*_m2*.py` | unit, db, source, API, conformance, resume-after-kill, browser and log-scan tests |
| `HANDOFF.md`, `README.md`, `docs/technical/api-m2.md` | build/run/config/traps/bench numbers; routes as built |

Shared interfaces are defined once (Tasks 6, 7, 8, 12, 16, 18) and reused by name everywhere after; if a later task needs a change, change the defining task's module and its tests together.

## Task index

| # | Task | Markers it adds |
|---|---|---|
| 1 | Dependencies, markers, M2 settings and posture | — |
| 2 | Ids and sealing (`ids.py`, `secrets.py`) | — |
| 3 | Metadata DB service, roles, scratch clusters, service fixtures | db |
| 4 | Migrations 001–009 and the runner (`db.py`, `migrate.py`) | db |
| 5 | RLS posture and tenant-isolation tests | db |
| 6 | Connector base types and codecs (`base.py`) | — |
| 7 | Job, connection and snapshot models (`jobs_models.py`) | — |
| 8 | Queue, part 1: job rows, claims, leases, workers registry | db |
| 9 | Queue, part 2: partitions, finalize transition, reaper, controls, stats | db |
| 10 | DB-backed audit (`DbAuditSink`) | db |
| 11 | Object storage and artefacts (`storage.py`) + MinIO service | s3 |
| 12 | Selection validation (`selection.py`) and `redact_spec` | — |
| 13 | Partition planning (`partitions.py`) | — |
| 14 | Connection registry (`connections.py`) and connector registry | db |
| 15 | PostgreSQL connector — read side + `source-pg` service | pg_source |
| 16 | Engine bridge (`batch.py`) + M0 touch points | — |
| 17 | Discovery, snapshots and the `discover_job` handler | db, pg_source |
| 18 | Targets: run validation, run plan, PK strategies, created DDL | — |
| 19 | PostgreSQL writer: in_place, append, table, ledger, collision precheck | pg_source |
| 20 | Files writer and manifest | s3 |
| 21 | Evidence pack: build, hash, HTML, store | db |
| 22 | Run handlers (`prepare`, `run_partition`, `finalize`) and job groups | db, pg_source |
| 23 | Worker process and retention | db |
| 24 | API foundation, connections routes, app wiring | db |
| 25 | Jobs, snapshots, groups, evidence and artefact routes | db, pg_source |
| 26 | Connector conformance suite and resume-after-kill tests | slow |
| 27 | Demo schema seeding, compose stack, image, smoke extension | — |
| 28 | Benchmark (`bench_connector.py`) and the 10-table demo (`demo_m2.py`) | — |
| 29 | Console foundation: types, API groups, helpers, Vitest, shared components | — |
| 30 | Jobs page (07) and Overview recent jobs | — |
| 31 | Job detail page (03) | — |
| 32 | New-job wizard, steps 1–2 (connect, select data) | — |
| 33 | New-job wizard, step 3 (discover and review) | — |
| 34 | New-job wizard, steps 4–5 (target, confirmation, run, key card) | — |
| 35 | Evidence pack page (10) and Reports evidence list (09) | — |
| 36 | Console browser check against the mockups | browser |
| 37 | M2 log-scan test | — |
| 38 | Documentation, handoff, milestone gates | — |

---

### Task 1: Dependencies, markers, M2 settings and posture

**Files:**
- Modify: `pyproject.toml`, `dps/config.py`, `dps/posture.py`, `.env.example`, `.gitignore`
- Test: `tests/test_config_m2.py`, `tests/test_posture_m2.py`

- [ ] **Step 1: Extend `pyproject.toml`**

Add to `[project.optional-dependencies]` (keep every existing extra):
```toml
db = ["asyncpg>=0.29"]                                   # metadata DB + PostgreSQL connector
connectors = ["sqlglot>=26", "pyarrow>=16", "boto3>=1.34"]  # M3 appends oracledb, pymongo>=4.13
e2e = ["playwright==1.55.0"]                             # console browser check; same pin as M4's [scanner]
```
Add `"pytest-timeout>=2.3"` to the `dev` extra. Extend `[tool.pytest.ini_options]`:
```toml
asyncio_default_fixture_loop_scope = "function"
markers = [
    "ner: needs the optional [ner] extra and spaCy models",
    "fpe: needs the optional [fpe] extra",
    "network: needs a live LLM gateway",
    "browser: needs Playwright Chromium (M2 console check, M4 scanner)",
    "integration: needs a live source/target container (always combined with a service marker)",
    "db: needs the metadata Postgres (DPS_TEST_PG_DSN, default 127.0.0.1:5441)",
    "pg_source: needs the demo source Postgres (DPS_TEST_SOURCE_PG_DSN, default 127.0.0.1:5442)",
    "s3: needs MinIO (DPS_TEST_S3_ENDPOINT, default http://127.0.0.1:9010)",
    "slow: takes more than 30 s (benchmarks, resume-after-kill)",
]
```
(Keep any marker M1 added, e.g. `docs`, `ocr`.) Then reinstall: `uv pip install -e ".[dev,fpe,db,connectors,e2e]"`.

- [ ] **Step 2: Write the failing tests**

`tests/test_config_m2.py`:
```python
from __future__ import annotations

import base64

import pytest

from dps.config import Settings

KEK = base64.b64encode(bytes(range(32))).decode()


def test_m2_defaults_keep_l1_database_free() -> None:
    s = Settings(_env_file=None)
    assert s.pg_host == "" and s.pg_port == 5432 and s.pg_database == "dps" and s.pg_schema == "dps"
    assert s.pg_user == "dps_app" and s.migrations_mode == "auto" and s.rls_enabled is True
    assert (s.job_lease_seconds, s.partition_lease_seconds) == (300, 120)
    assert (s.job_max_attempts, s.partition_max_attempts) == (3, 5)
    assert (s.batch_rows, s.partition_rows, s.max_partitions, s.discovery_sample_rows) == (1000, 50000, 256, 2000)
    assert s.worker_metrics_port == 9400 and s.embedded_worker is False
    assert s.artefact_store == "local" and s.s3_bucket == "dps-artefacts" and s.s3_force_path_style is True
    assert s.retention_days == 30 and s.audit_retention_months == 13 and s.audit_strict is False


def test_worker_and_migration_logins_fall_back_to_pg_user() -> None:
    s = Settings(_env_file=None, pg_user="u", pg_password="p")
    assert s.pg_worker_login == ("u", "p") and s.pg_migration_login == ("u", "p")
    s2 = Settings(_env_file=None, pg_worker_user="w", pg_worker_password="wp",
                  pg_migration_user="o", pg_migration_password="op")
    assert s2.pg_worker_login == ("w", "wp") and s2.pg_migration_login == ("o", "op")


@pytest.mark.parametrize("field,value", [("pg_schema", "Bad-Schema"), ("pg_schema", "x" * 64),
                                         ("migrations_mode", "always"), ("artefact_store", "gcs")])
def test_invalid_m2_values_are_refused(field: str, value: str) -> None:
    with pytest.raises(ValueError):
        Settings(_env_file=None, **{field: value})


def test_kek_must_decode_to_32_bytes_and_errors_never_echo_it() -> None:
    assert Settings(_env_file=None, job_kek=KEK).job_kek == KEK
    short = base64.b64encode(b"tooshort-secret-material").decode()
    with pytest.raises(ValueError) as err:
        Settings(_env_file=None, job_kek=short)
    assert short not in str(err.value) and "32 bytes" in str(err.value)


def test_kek_from_file(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:  # noqa: ANN001
    path = tmp_path / "kek"
    path.write_text(KEK + "\n", encoding="utf-8")
    monkeypatch.setenv("DPS_JOB_KEK_FILE", str(path))
    assert Settings(_env_file=None).job_kek == KEK
```

`tests/test_posture_m2.py`:
```python
from __future__ import annotations

import base64

import pytest

from dps.config import Settings
from dps.posture import PostureError, assert_production_posture, posture_problems

KEK = base64.b64encode(bytes(range(32))).decode()


def _prod(**kw):  # noqa: ANN003, ANN202 - test helper
    base = dict(_env_file=None, env="prod", auth_enabled=True, api_keys='[{"name":"k","key_sha256":"ab"}]',
                llm_provider="", audit_enabled=True, pg_host="db.internal", job_kek=KEK,
                inline_secrets_allowed=False, rls_enabled=True, migrations_mode="verify",
                audit_strict=True, pg_ssl_root_cert="/etc/ssl/pg-ca.pem", embedded_worker=False)
    base.update(kw)
    return Settings(**base)


def test_good_m2_production_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DPS_JOB_KEK", raising=False)
    assert_production_posture(_prod())


def test_m2_checks_skip_without_a_database() -> None:
    assert posture_problems(_prod(pg_host="", job_kek="", inline_secrets_allowed=True,
                                  migrations_mode="auto", audit_strict=False)) == []


@pytest.mark.parametrize("bad,needle", [
    (dict(job_kek=""), "DPS_JOB_KEK_FILE"), (dict(inline_secrets_allowed=True), "INLINE_SECRETS"),
    (dict(rls_enabled=False), "RLS_ENABLED"), (dict(migrations_mode="auto"), "MIGRATIONS_MODE"),
    (dict(audit_strict=False), "AUDIT_STRICT"), (dict(pg_ssl_root_cert=""), "PG_SSL_ROOT_CERT"),
    (dict(embedded_worker=True), "EMBEDDED_WORKER"),
])
def test_bad_m2_production_is_refused(bad: dict, needle: str) -> None:
    with pytest.raises(PostureError) as err:
        assert_production_posture(_prod(**bad))
    assert needle in str(err.value)


def test_literal_kek_env_var_is_refused_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DPS_JOB_KEK", KEK)
    problems = posture_problems(_prod())
    assert any("DPS_JOB_KEK_FILE" in p for p in problems)
    assert all(KEK not in p for p in problems)
```

- [ ] **Step 3: Run them to confirm they fail**

Run: `pytest -q tests/test_config_m2.py tests/test_posture_m2.py`
Expected: FAIL — `pg_host` and the other fields do not exist yet (`AttributeError` / `ValidationError`).

- [ ] **Step 4: Add the M2 settings to `dps/config.py`**

Add the fields below to `Settings` (after the `audit` block), the validators, and the two properties. Field names are contract §10 verbatim; the four marked *(added)* are this plan's additions.

```python
    # --- metadata database (M2; empty pg_host = Layer 1 only, no database at all) ---
    pg_host: str = ""
    pg_port: int = 5432
    pg_database: str = "dps"
    pg_schema: str = "dps"
    pg_user: str = "dps_app"
    pg_password: str = ""
    pg_worker_user: str = ""          # falls back to pg_user
    pg_worker_password: str = ""
    pg_migration_user: str = ""       # falls back to pg_user
    pg_migration_password: str = ""
    pg_pool_min: int = 2
    pg_pool_max: int = 16
    pg_ssl_root_cert: str = ""
    rls_enabled: bool = True
    migrations_mode: str = "auto"     # auto | verify | off

    # --- job sealing (base64 of 32 bytes; production reads it from DPS_JOB_KEK_FILE) ---
    job_kek: str = ""
    job_kek_previous: str = ""        # M5 rotation

    # --- queue ---
    job_lease_seconds: int = 300
    partition_lease_seconds: int = 120
    job_max_attempts: int = 3
    partition_max_attempts: int = 5
    job_retry_base_seconds: float = 30.0
    job_retry_max_seconds: float = 3600.0
    job_poll_interval_seconds: float = 2.0
    job_claim_batch: int = 4
    job_reaper_interval_seconds: float = 30.0
    job_drain_timeout_seconds: float = 30.0
    job_tenant_max_running: int = 8
    job_tenant_max_queued: int = 1000
    worker_concurrency: int = 4
    worker_metrics_port: int = 9400   # 0 disables the worker's health/metrics server (tests)
    embedded_worker: bool = False     # local only; posture refuses it in staging/prod

    # --- connectors ---
    batch_rows: int = 1000
    partition_rows: int = 50000
    max_partitions: int = 256
    discovery_sample_rows: int = 2000
    preflight_rows: int = 500         # (added) rows transformed at prepare before any write
    connector_connect_timeout_seconds: float = 10.0
    connector_statement_timeout_seconds: float = 300.0
    secret_dir: str = "/var/run/secrets/dps"
    inline_secrets_allowed: bool = True
    inline_secret_ttl_hours: int = 24

    # --- retention and audit ---
    retention_days: int = 30
    retention_interval_hours: int = 6
    audit_retention_months: int = 13
    audit_strict: bool = False
    audit_queue_max: int = 10000      # (added) DbAuditSink queue bound
    audit_flush_batch: int = 200      # (added)
    audit_flush_ms: int = 250         # (added)

    # --- artefacts and exports ---
    artefact_store: str = "local"     # local | s3
    artefact_dir: str = "./var/artefacts"
    s3_endpoint_url: str = ""
    s3_region: str = "us-east-1"
    s3_bucket: str = "dps-artefacts"
    s3_access_key_id: str = ""
    s3_secret_access_key: str = ""
    s3_force_path_style: bool = True
    export_root: str = "./var/exports"
    image_digest: str = ""
```

Validators and properties (add `import base64, binascii, re` at the top):
```python
_SCHEMA_RE = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")

    @field_validator("pg_schema")
    @classmethod
    def _valid_schema(cls, v: str) -> str:
        if not _SCHEMA_RE.fullmatch(v):
            raise ValueError("pg_schema must match ^[a-z_][a-z0-9_]{0,62}$")
        return v

    @field_validator("migrations_mode")
    @classmethod
    def _valid_migrations_mode(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in ("auto", "verify", "off"):
            raise ValueError("migrations_mode must be auto, verify or off")
        return v

    @field_validator("artefact_store")
    @classmethod
    def _valid_store(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in ("local", "s3"):
            raise ValueError("artefact_store must be local or s3")
        return v

    @field_validator("job_kek", "job_kek_previous")
    @classmethod
    def _valid_kek(cls, v: str) -> str:
        v = v.strip()
        if not v:
            return v
        try:
            raw = base64.b64decode(v + "=" * (-len(v) % 4), altchars=b"-_" if "-" in v or "_" in v else None,
                                   validate=True)
        except (binascii.Error, ValueError):
            raise ValueError("job KEK must be base64 of exactly 32 bytes") from None
        if len(raw) != 32:
            raise ValueError("job KEK must be base64 of exactly 32 bytes")
        return v

    @property
    def pg_worker_login(self) -> tuple[str, str]:
        """``(user, password)`` for the worker pool, falling back to the API login."""
        if self.pg_worker_user:
            return self.pg_worker_user, self.pg_worker_password
        return self.pg_user, self.pg_password

    @property
    def pg_migration_login(self) -> tuple[str, str]:
        """``(user, password)`` for DDL, falling back to the API login."""
        if self.pg_migration_user:
            return self.pg_migration_user, self.pg_migration_password
        return self.pg_user, self.pg_password
```
`hide_input_in_errors=True` (M0 deviation) already keeps pydantic from echoing the KEK; the messages above never interpolate it.

- [ ] **Step 5: Extend `dps/posture.py`**

```python
import os

def _m2_problems(settings: Settings) -> list[str]:
    """Database-backed (M2) checks; only when jobs are enabled (``pg_host`` set)."""
    if not settings.pg_host:
        return []
    problems: list[str] = []
    if not settings.job_kek:
        problems.append("DPS_JOB_KEK_FILE must provide the job KEK")
    elif os.environ.get("DPS_JOB_KEK"):
        problems.append("the job KEK must come from DPS_JOB_KEK_FILE, not a literal DPS_JOB_KEK")
    if settings.inline_secrets_allowed:
        problems.append("DPS_INLINE_SECRETS_ALLOWED must be false")
    if not settings.rls_enabled:
        problems.append("DPS_RLS_ENABLED must be true")
    if settings.migrations_mode != "verify":
        problems.append("DPS_MIGRATIONS_MODE must be verify")
    if not settings.audit_strict:
        problems.append("DPS_AUDIT_STRICT must be true")
    if not settings.pg_ssl_root_cert:
        problems.append("DPS_PG_SSL_ROOT_CERT is required when DPS_PG_HOST is set")
    if settings.embedded_worker:
        problems.append("DPS_EMBEDDED_WORKER must be false")
    return problems
```
In `posture_problems`, after the existing checks: `problems.extend(_m2_problems(settings))`.

- [ ] **Step 6: `.env.example` and `.gitignore`**

Append to `.env.example` a commented `# --- M2: jobs + PostgreSQL ---` block listing `DPS_PG_HOST=`, `DPS_PG_USER=dps_app`, `DPS_PG_WORKER_USER=dps_worker_login`, `DPS_PG_MIGRATION_USER=dps_owner`, `DPS_JOB_KEK_FILE=` (with the comment "base64 of 32 random bytes: `python -c 'import os,base64;print(base64.b64encode(os.urandom(32)).decode())'`"), `DPS_MIGRATIONS_MODE=auto`, `DPS_ARTEFACT_STORE=local`, `DPS_SECRET_DIR=./var/secrets`. Append `var/` to `.gitignore` (artefacts, exports, local secret dirs).

- [ ] **Step 7: Run the tests, lint, types**

Run: `pytest -q && ruff check dps tests tools && ruff format --check dps tests tools && mypy dps`
Expected: all green; the two new modules add `8 + 10 = 18` passing tests (parametrised cases counted: `test_invalid_m2_values_are_refused` ×4, `test_bad_m2_production_is_refused` ×7); every M0 test still passes (M2 posture checks are skipped without `pg_host`).

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml dps/config.py dps/posture.py .env.example .gitignore tests/test_config_m2.py tests/test_posture_m2.py
git commit -m "feat: M2 settings, extras and markers; production posture for the metadata database" -- pyproject.toml dps/config.py dps/posture.py .env.example .gitignore tests/test_config_m2.py tests/test_posture_m2.py
```

---

### Task 2: Ids and sealing (`dps/ids.py`, `dps/secrets.py`)

**Files:**
- Create: `dps/ids.py`, `dps/secrets.py`
- Test: `tests/test_ids.py`, `tests/test_secrets.py`

Public interface (contract §1, §6.3; `seal_json`/`open_json`/`seed_fingerprint`/`SecretUnavailable` are additions):
```python
# dps/ids.py
CROCKFORD: str
ID_RE: re.Pattern[str]                                   # ^[a-z][a-z0-9]{0,7}_[0-9A-HJKMNP-TV-Z]{26}$
PREFIX_CONNECTION, PREFIX_JOB, PREFIX_SCAN, PREFIX_SNAPSHOT, PREFIX_GROUP, PREFIX_ARTEFACT, PREFIX_EVIDENCE = (
    "c", "j", "s", "snap", "grp", "art", "ev")
def ulid(now_ms: int | None = None) -> str
def new_id(prefix: str) -> str
def is_id(value: str, prefix: str | None = None) -> bool

# dps/secrets.py
class SealError(Exception)                               # never carries detail
class SecretUnavailable(Exception)                       # .code == "secret_unavailable"
def load_kek(settings: Settings) -> bytes | None
def seal(kek: bytes, plaintext: bytes, *, aad: str) -> bytes
def open_sealed(kek: bytes, sealed: bytes, *, aad: str) -> bytes
def seal_json(kek: bytes, model: BaseModel, *, aad: str) -> bytes
def open_json(kek: bytes, sealed: bytes, model: type[M], *, aad: str) -> M
def resolve_secret_ref(ref: str, settings: Settings) -> dict[str, str]
def seed_fingerprint(seed_b64: str) -> str               # sha256(raw seed bytes) hex [:16]
```

- [ ] **Step 1: Write the failing tests**

`tests/test_ids.py`:
```python
from __future__ import annotations

import pytest

from dps.ids import ID_RE, is_id, new_id, ulid


def test_ulid_shape_and_time_order() -> None:
    a, b = ulid(now_ms=1_700_000_000_000), ulid(now_ms=1_700_000_000_001)
    assert len(a) == 26 and a < b and set(a) <= set("0123456789ABCDEFGHJKMNPQRSTVWXYZ")


def test_new_id_prefix_and_uniqueness() -> None:
    ids = {new_id("j") for _ in range(2000)}
    assert len(ids) == 2000 and all(ID_RE.fullmatch(i) and i.startswith("j_") for i in ids)
    assert is_id(new_id("snap"), "snap") and not is_id(new_id("j"), "snap") and not is_id("j_x")


@pytest.mark.parametrize("prefix", ["", "J", "toolongprefix", "a-b"])
def test_bad_prefix_is_refused(prefix: str) -> None:
    with pytest.raises(ValueError):
        new_id(prefix)
```

`tests/test_secrets.py`:
```python
from __future__ import annotations

import base64
import os

import pytest
from pydantic import BaseModel

from dps.config import Settings
from dps.secrets import (
    SealError, SecretUnavailable, load_kek, open_json, open_sealed, resolve_secret_ref, seal, seal_json,
    seed_fingerprint,
)

KEK = os.urandom(32)


class Box(BaseModel):
    dek: str


def test_seal_round_trip_and_format() -> None:
    sealed = seal(KEK, b"payload", aad="dps_job.key:j_1")
    assert sealed[:1] == b"\x01" and len(sealed) == 1 + 12 + len(b"payload") + 16
    assert open_sealed(KEK, sealed, aad="dps_job.key:j_1") == b"payload"
    assert seal(KEK, b"payload", aad="a") != seal(KEK, b"payload", aad="a")  # fresh nonce


@pytest.mark.parametrize("tamper", ["aad", "kek", "bit", "version", "short"])
def test_open_fails_closed_without_detail(tamper: str) -> None:
    sealed = seal(KEK, b"secret-value", aad="dps_job.key:j_1")
    kek, aad, blob = KEK, "dps_job.key:j_1", bytearray(sealed)
    if tamper == "aad":
        aad = "dps_job.key:j_2"          # a blob moved to another row
    elif tamper == "kek":
        kek = os.urandom(32)
    elif tamper == "bit":
        blob[-1] ^= 1
    elif tamper == "version":
        blob[0] = 2
    else:
        blob = blob[:10]
    with pytest.raises(SealError) as err:
        open_sealed(kek, bytes(blob), aad=aad)
    assert str(err.value) == "sealed blob could not be opened"


def test_seal_json_models() -> None:
    blob = seal_json(KEK, Box(dek="k"), aad="x:1")
    assert open_json(KEK, blob, Box, aad="x:1").dek == "k"


def test_load_kek() -> None:
    assert load_kek(Settings(_env_file=None)) is None
    assert load_kek(Settings(_env_file=None, job_kek=base64.b64encode(KEK).decode())) == KEK


def test_resolve_secret_ref(tmp_path) -> None:  # noqa: ANN001
    d = tmp_path / "dps" / "src" / "bank-pg"
    d.mkdir(parents=True)
    (d / "username").write_text("dps_reader\n")
    (d / "password").write_text("pw\n")
    (d / "notes.txt").write_text("ignored")
    s = Settings(_env_file=None, secret_dir=str(tmp_path))
    assert resolve_secret_ref("dps/src/bank-pg", s) == {"username": "dps_reader", "password": "pw"}
    for bad in ("../etc", "/abs", "dps/../../x", "dps/src/missing"):
        with pytest.raises(SecretUnavailable) as err:
            resolve_secret_ref(bad, s)
        assert err.value.code == "secret_unavailable" and "pw" not in str(err.value)


def test_seed_fingerprint_is_stable_and_short() -> None:
    seed = base64.urlsafe_b64encode(bytes(32)).decode().rstrip("=")
    assert seed_fingerprint(seed) == seed_fingerprint(seed) and len(seed_fingerprint(seed)) == 16
```

- [ ] **Step 2: Run to confirm failure**

Run: `pytest -q tests/test_ids.py tests/test_secrets.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'dps.ids'`.

- [ ] **Step 3: Write `dps/ids.py`**

```python
"""Sortable, prefixed identifiers: ``<prefix>_<26-char Crockford ULID>`` (contract §1).

48 bits of millisecond time + 80 random bits, so ids sort by creation time and never collide in
practice. Ids are opaque handles: they carry no value, so they may appear in logs and URLs.
"""
from __future__ import annotations

import os
import re
import time

CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
ID_RE = re.compile(r"^[a-z][a-z0-9]{0,7}_[0-9A-HJKMNP-TV-Z]{26}$")
_PREFIX_RE = re.compile(r"^[a-z][a-z0-9]{0,7}$")
PREFIX_CONNECTION, PREFIX_JOB, PREFIX_SCAN, PREFIX_SNAPSHOT = "c", "j", "s", "snap"
PREFIX_GROUP, PREFIX_ARTEFACT, PREFIX_EVIDENCE = "grp", "art", "ev"


def ulid(now_ms: int | None = None) -> str:
    """A 26-character Crockford base32 ULID."""
    ms = int(time.time() * 1000) if now_ms is None else now_ms
    value = (ms & ((1 << 48) - 1)) << 80 | int.from_bytes(os.urandom(10), "big")
    return "".join(CROCKFORD[(value >> (5 * i)) & 31] for i in reversed(range(26)))


def new_id(prefix: str) -> str:
    """``<prefix>_<ulid>``; ``ValueError`` for a malformed prefix."""
    if not _PREFIX_RE.fullmatch(prefix):
        raise ValueError("id prefix must match ^[a-z][a-z0-9]{0,7}$")
    return f"{prefix}_{ulid()}"


def is_id(value: str, prefix: str | None = None) -> bool:
    """True when ``value`` is a well-formed id (with ``prefix`` when given)."""
    return bool(ID_RE.fullmatch(value)) and (prefix is None or value.split("_", 1)[0] == prefix)
```

- [ ] **Step 4: Write `dps/secrets.py`**

```python
"""At-rest sealing for job secrets and inline connection credentials (contract §6.3).

``seal`` = ``b"\\x01" + nonce(12) + AES-256-GCM(kek, plaintext, aad)``. The AAD binds a blob to
its row (``"dps_job.key:<job id>"``, ``"dps_job.spec:<job id>"``, ``"dps_connection.secret:<id>"``),
so a sealed blob copied into another row does not open. This module and
``dps/engine/transform/crypto.py`` are the only modules that touch key bytes; neither logs, and
every error here is detail-free.
"""
from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path
from typing import TypeVar

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import BaseModel, ValidationError

from dps.config import Settings

_VERSION = b"\x01"
_NONCE = 12
_TAG = 16
SECRET_FILES = ("username", "password", "dsn", "uri", "access_key_id", "secret_access_key",
                "storage_state.json")
M = TypeVar("M", bound=BaseModel)


class SealError(Exception):
    """A sealed blob could not be opened (wrong KEK, wrong row, tampered). Carries no detail."""

    def __init__(self) -> None:
        super().__init__("sealed blob could not be opened")


class SecretUnavailable(Exception):
    """A ``secret_ref`` does not resolve to a readable secret directory."""

    code = "secret_unavailable"

    def __init__(self) -> None:
        super().__init__("secret reference could not be resolved")


def _b64decode(text: str) -> bytes:
    text = text.strip()
    pad = "=" * (-len(text) % 4)
    alt = b"-_" if ("-" in text or "_" in text) else None
    return base64.b64decode(text + pad, altchars=alt, validate=True)


def load_kek(settings: Settings) -> bytes | None:
    """The 32-byte job KEK, or ``None`` when unset (settings already validated its length)."""
    return _b64decode(settings.job_kek) if settings.job_kek else None


def seal(kek: bytes, plaintext: bytes, *, aad: str) -> bytes:
    """Encrypt and authenticate ``plaintext`` bound to ``aad``."""
    nonce = os.urandom(_NONCE)
    return _VERSION + nonce + AESGCM(kek).encrypt(nonce, plaintext, aad.encode("utf-8"))


def open_sealed(kek: bytes, sealed: bytes, *, aad: str) -> bytes:
    """Decrypt a blob from :func:`seal`; :class:`SealError` on any failure."""
    if len(sealed) < 1 + _NONCE + _TAG or sealed[:1] != _VERSION or len(kek) != 32:
        raise SealError()
    try:
        return AESGCM(kek).decrypt(sealed[1:1 + _NONCE], sealed[1 + _NONCE:], aad.encode("utf-8"))
    except (InvalidTag, ValueError):
        raise SealError() from None


def seal_json(kek: bytes, model: BaseModel, *, aad: str) -> bytes:
    """Seal a pydantic model's JSON (``SecretStr`` fields must be revealed by the caller first)."""
    return seal(kek, model.model_dump_json().encode("utf-8"), aad=aad)


def open_json(kek: bytes, sealed: bytes, model: type[M], *, aad: str) -> M:
    """Open and validate a sealed model; :class:`SealError` on any failure."""
    try:
        return model.model_validate_json(open_sealed(kek, sealed, aad=aad))
    except ValidationError:
        raise SealError() from None


def resolve_secret_ref(ref: str, settings: Settings) -> dict[str, str]:
    """Read the allow-listed files under ``DPS_SECRET_DIR/<ref>/`` (trailing newline stripped)."""
    if not ref or ref.startswith("/") or ".." in ref.split("/"):
        raise SecretUnavailable()
    root = Path(settings.secret_dir).resolve()
    folder = (root / ref).resolve()
    if root not in folder.parents or not folder.is_dir():
        raise SecretUnavailable()
    out: dict[str, str] = {}
    for name in SECRET_FILES:
        path = folder / name
        if path.is_file():
            try:
                out[name] = path.read_text(encoding="utf-8").rstrip("\r\n")
            except OSError:
                raise SecretUnavailable() from None
    if not out:
        raise SecretUnavailable()
    return out


def seed_fingerprint(seed_b64: str) -> str:
    """Identifier of a caller-held synthesis seed: first 16 hex chars of sha256(raw seed)."""
    return hashlib.sha256(_b64decode(seed_b64)).hexdigest()[:16]
```

- [ ] **Step 5: Run tests, lint, types**

Run: `pytest -q tests/test_ids.py tests/test_secrets.py && ruff check dps tests tools && mypy dps`
Expected: `6 + 10 = 16 passed` (parametrised cases counted), clean.

- [ ] **Step 6: Commit**

```bash
git add dps/ids.py dps/secrets.py tests/test_ids.py tests/test_secrets.py
git commit -m "feat: prefixed ULIDs and AES-256-GCM row-bound sealing for job secrets" -- dps/ids.py dps/secrets.py tests/test_ids.py tests/test_secrets.py
```

---

### Task 3: Metadata DB service, roles, scratch clusters, service fixtures

**Files:**
- Create: `docker/dps-db/initdb/01_roles.sql`, `tools/scratch_pg.sh`, `tests/fixtures/__init__.py`, `tests/fixtures/services.py`, `tests/db/__init__.py`, `tests/db/test_service.py`
- Modify: `docker-compose.yml` (add `dps-db`; bind the app port to `127.0.0.1`), `tests/conftest.py` (register the fixture plugins), `HANDOFF.md` (ports table: `dps-db 127.0.0.1:5441`)

- [ ] **Step 1: Write `docker/dps-db/initdb/01_roles.sql`** (contract §2.3; runs once on an empty data directory, as `postgres`)

```sql
-- 01_roles.sql — local-only roles and the metadata database (contract §2.3).
-- Passwords are demo literals equal to the role name; production roles are created by the platform.
CREATE ROLE dps_owner LOGIN PASSWORD 'dps_owner'
    NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOINHERIT;
CREATE ROLE dps_app_rw NOLOGIN NOSUPERUSER NOBYPASSRLS;
CREATE ROLE dps_worker NOLOGIN NOSUPERUSER NOBYPASSRLS;
CREATE ROLE dps_app LOGIN PASSWORD 'dps_app' NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
CREATE ROLE dps_worker_login LOGIN PASSWORD 'dps_worker_login'
    NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
-- Disjoint memberships: dps_app is never a member of dps_worker (the worker_claim policy, 008).
GRANT dps_app_rw TO dps_app;
GRANT dps_app_rw, dps_worker TO dps_worker_login;
CREATE DATABASE dps OWNER dps_owner;
ALTER DATABASE dps SET timezone TO 'UTC';
REVOKE ALL ON DATABASE dps FROM PUBLIC;
GRANT CONNECT ON DATABASE dps TO dps_app_rw, dps_worker, dps_owner;
```

- [ ] **Step 2: Add `dps-db` to `docker-compose.yml`** (contract §11; host port bound to localhost)

```yaml
  dps-db:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: postgres          # local-only demo literal
      POSTGRES_PASSWORD: postgres
      POSTGRES_DB: postgres
      TZ: UTC
    command: ["postgres", "-c", "max_connections=200", "-c", "timezone=UTC"]
    ports:
      - "127.0.0.1:5441:5432"
    volumes:
      - dps-db-data:/var/lib/postgresql/data
      - ./docker/dps-db/initdb:/docker-entrypoint-initdb.d:ro
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres -d dps"]
      interval: 5s
      timeout: 3s
      retries: 30
    mem_limit: 768m
    labels: {app: data-privacy-services, component: metadata-db, env: local}
```
Add the top-level `volumes: {dps-db-data: {}}` and change the `app` port mapping to `"127.0.0.1:${DPS_APP_PORT:-8400}:8400"` (contract §11: host ports bind 127.0.0.1 only).

- [ ] **Step 3: Write `tools/scratch_pg.sh`** (Homebrew PostgreSQL 16; the memory note "set timezone UTC" is applied)

```bash
#!/usr/bin/env bash
# Homebrew PostgreSQL 16 scratch clusters for host-side tests (no Docker).
#   tools/scratch_pg.sh start [meta|source|all]   meta = 5441 (dps roles + DB), source = 5442 (bank)
#   tools/scratch_pg.sh stop  [meta|source|all]
#   tools/scratch_pg.sh status
# Trust auth, superuser "postgres", timezone UTC, no unix sockets (the scratch path can be too long).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BASE="${DPS_SCRATCH_DIR:-$HOME/.cache/dps-scratch-pg}"
BIN="$(brew --prefix postgresql@16 2>/dev/null)/bin"
[ -x "$BIN/pg_ctl" ] || BIN="$(dirname "$(command -v pg_ctl)")"

port_of() { case "$1" in meta) echo 5441;; source) echo 5442;; esac; }

start_one() {
  local name="$1" port; port="$(port_of "$name")"; local data="$BASE/$name"
  if [ ! -f "$data/PG_VERSION" ]; then
    mkdir -p "$data"
    "$BIN/initdb" -D "$data" -U postgres --auth=trust -E UTF8 --locale=C >/dev/null
    echo "timezone = 'UTC'" >> "$data/postgresql.conf"
    fresh=1
  else
    fresh=0
  fi
  if ! "$BIN/pg_ctl" -D "$data" status >/dev/null 2>&1; then
    "$BIN/pg_ctl" -D "$data" -l "$data/server.log" -w start \
      -o "-p $port -c listen_addresses=127.0.0.1 -c unix_socket_directories='' -c max_connections=300"
  fi
  if [ "$fresh" = 1 ]; then
    case "$name" in
      meta)   "$BIN/psql" -h 127.0.0.1 -p "$port" -U postgres -v ON_ERROR_STOP=1 -q \
                -f "$ROOT/docker/dps-db/initdb/01_roles.sql" ;;
      source) for f in "$ROOT"/docker/source-pg/initdb/*.sql; do
                [ -f "$f" ] && "$BIN/psql" -h 127.0.0.1 -p "$port" -U postgres -v ON_ERROR_STOP=1 -q -f "$f"
              done ;;
    esac
  fi
  echo "$name: 127.0.0.1:$port ($data)"
}

stop_one() {
  local data="$BASE/$1"
  [ -f "$data/PG_VERSION" ] && "$BIN/pg_ctl" -D "$data" -m fast stop >/dev/null 2>&1 || true
  echo "$1: stopped"
}

cmd="${1:-status}"; which="${2:-all}"
targets=(); [ "$which" = all ] && targets=(meta source) || targets=("$which")
case "$cmd" in
  start) for t in "${targets[@]}"; do start_one "$t"; done ;;
  stop)  for t in "${targets[@]}"; do stop_one "$t"; done ;;
  status) for t in meta source; do
            if "$BIN/pg_ctl" -D "$BASE/$t" status >/dev/null 2>&1; then echo "$t: running"; else echo "$t: stopped"; fi
          done ;;
  *) echo "usage: $0 start|stop|status [meta|source|all]" >&2; exit 2 ;;
esac
```
`chmod +x tools/scratch_pg.sh`. (The source cluster picks up `docker/source-pg/initdb/*.sql` once Task 15 adds it; re-create with `rm -rf ~/.cache/dps-scratch-pg/source` to re-init.)

- [ ] **Step 4: Write `tests/fixtures/services.py`** (contract §12.2; if M1 already put `service_url` into `tests/conftest.py`, move it here unchanged and delete the copy)

```python
"""Finding test containers by env var; skip (or fail under DPS_TEST_REQUIRE_SERVICES=1) when down."""
from __future__ import annotations

import os
import socket
from urllib.parse import urlparse

import pytest

SERVICES: dict[str, tuple[str, str, str]] = {   # name -> (env var, default, compose profile)
    "dps-db": ("DPS_TEST_PG_DSN", "postgresql://postgres:postgres@127.0.0.1:5441/postgres", ""),
    "source-pg": ("DPS_TEST_SOURCE_PG_DSN", "postgresql://postgres:postgres@127.0.0.1:5442/bank", "sources"),
    "minio": ("DPS_TEST_S3_ENDPOINT", "http://127.0.0.1:9010", "sources"),
    "app": ("DPS_TEST_APP_URL", "http://127.0.0.1:8400", ""),
    "azure-ocr-mock": ("DPS_TEST_OCR_URL", "http://127.0.0.1:5016", "docs"),
    "demo-site": ("DPS_TEST_DEMO_SITE", "http://127.0.0.1:8410", "scanner"),
}


def service_url(name: str) -> str:
    """The URL/DSN of a test service (env var override, else the compose host port)."""
    env, default, _ = SERVICES[name]
    return os.environ.get(env, default)


def _reachable(url: str) -> bool:
    parsed = urlparse(url)
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((parsed.hostname or "127.0.0.1", port), timeout=2.0):
            return True
    except OSError:
        return False


def require_service(name: str) -> str:
    """Return the service URL, or skip/fail with the command that starts it."""
    url = service_url(name)
    if _reachable(url):
        return url
    profile = SERVICES[name][2]
    start = f"docker compose {'--profile ' + profile + ' ' if profile else ''}up -d {name}"
    message = f"{name} unreachable — start: {start} (or tools/scratch_pg.sh start)"
    if os.environ.get("DPS_TEST_REQUIRE_SERVICES") == "1":
        pytest.fail(message)
    pytest.skip(message)
```
`tests/fixtures/__init__.py`, `tests/db/__init__.py`: empty. In `tests/conftest.py` add (top level, after imports):
```python
pytest_plugins = ["tests.fixtures.services", "tests.fixtures.db", "tests.fixtures.sources"]
```
`tests.fixtures.db` and `tests.fixtures.sources` are created in Tasks 4 and 15; create both now as modules containing only a docstring so the plugin list imports.

- [ ] **Step 5: Write the failing test** `tests/db/test_service.py`

```python
from __future__ import annotations

import asyncpg
import pytest

from tests.fixtures.services import require_service

pytestmark = pytest.mark.db
ROLES = {"dps_owner", "dps_app_rw", "dps_worker", "dps_app", "dps_worker_login"}


async def test_metadata_db_has_the_contract_roles() -> None:
    conn = await asyncpg.connect(require_service("dps-db"))
    try:
        rows = await conn.fetch("SELECT rolname, rolsuper, rolbypassrls, rolcanlogin FROM pg_roles "
                                "WHERE rolname = ANY($1::text[])", list(ROLES))
        assert {r["rolname"] for r in rows} == ROLES
        assert not any(r["rolsuper"] or r["rolbypassrls"] for r in rows)
        login = {r["rolname"] for r in rows if r["rolcanlogin"]}
        assert login == {"dps_owner", "dps_app", "dps_worker_login"}
        member = await conn.fetchval("SELECT pg_has_role('dps_app', 'dps_worker', 'MEMBER')")
        assert member is False
        assert await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = 'dps'") == 1
    finally:
        await conn.close()
```

- [ ] **Step 6: Run it**

Run: `docker compose up -d dps-db` (or `tools/scratch_pg.sh start meta`), wait for healthy, then `pytest -q tests/db/test_service.py`
Expected: `1 passed`. Without the service: `1 skipped` with "start: docker compose up -d dps-db".

- [ ] **Step 7: Gates and commit**

Run the per-task gates. Then:
```bash
git add docker/dps-db/initdb/01_roles.sql docker-compose.yml tools/scratch_pg.sh tests/fixtures tests/db/__init__.py tests/db/test_service.py tests/conftest.py HANDOFF.md
git commit -m "feat: metadata Postgres service with contract roles, scratch clusters and service fixtures" -- docker/dps-db/initdb/01_roles.sql docker-compose.yml tools/scratch_pg.sh tests/fixtures tests/db/__init__.py tests/db/test_service.py tests/conftest.py HANDOFF.md
```

---

### Task 4: Migrations 001–009 and the runner (`dps/db.py`, `dps/migrate.py`)

**Files:**
- Create: `dps/migrations/001_schema_and_roles.sql` … `dps/migrations/009_grants.sql`, `dps/db.py`, `dps/migrate.py`
- Modify: `tests/fixtures/db.py` (fixtures), `dps/observability.py` (`_SAFE_FIELDS` += `"migration"`)
- Test: `tests/db/test_migrations.py`

Public interface (contract §2.1; `active_settings`, `schema_ident`, `rowcount`, `migration_files`, `drop_expired_audit_partitions`, `GLOBAL_TABLES`, `ALLOWED_EXTRA_POLICIES` are additions):
```python
TENANT_TABLES: tuple[str, ...]      # dps_connection dps_job_group dps_job dps_job_partition dps_snapshot dps_artefact dps_evidence dps_audit_log
GLOBAL_TABLES: tuple[str, ...]      # dps_tenant dps_api_key dps_worker dps_migration_ledger (no RLS)
class MigrationDrift(RuntimeError)
def db_configured(settings: Settings) -> bool
def active_settings() -> Settings                 # the settings the pools were initialised with
def schema_ident(settings: Settings | None = None) -> str   # validated, double-quoted: '"dps"'
def rowcount(command_tag: str) -> int
def migration_files() -> list[Path]
async def init_pool(settings: Settings | None = None) -> asyncpg.Pool
async def init_worker_pool(settings: Settings | None = None) -> asyncpg.Pool
async def close_pools() -> None
@asynccontextmanager
async def acquire(tenant_id: str | None) -> AsyncIterator[asyncpg.Connection]
@asynccontextmanager
async def acquire_queue(tenant_id: str | None = None) -> AsyncIterator[asyncpg.Connection]
async def open_migration_connection(settings: Settings | None = None) -> asyncpg.Connection
async def run_migrations(settings: Settings | None = None, *, connection: asyncpg.Connection | None = None) -> list[str]
async def verify_migrations(settings: Settings | None = None) -> None
async def ensure_audit_partitions(conn: asyncpg.Connection, settings: Settings, months_ahead: int = 3) -> None
async def drop_expired_audit_partitions(conn: asyncpg.Connection, settings: Settings) -> list[str]
async def assert_rls_posture(settings: Settings | None = None) -> list[str]     # Task 5
def evaluate_rls_posture(...) -> list[str]                                      # Task 5
```

- [ ] **Step 1: Write the migration files** (contract §2.4; `__SCHEMA__` is replaced by the validated, quoted schema; each file runs in one transaction; applied files are never edited)

`dps/migrations/001_schema_and_roles.sql`:
```sql
-- 001_schema_and_roles.sql — schema hygiene and the two NOLOGIN group roles (belt-and-braces).
-- Runs as dps_owner. docker/dps-db/initdb/01_roles.sql (local) or the platform (prod) creates every
-- role first; this block only guarantees the groups exist so a bare database still reaches the 009
-- grant shape. If the groups are missing and dps_owner cannot CREATEROLE, it fails loudly — correct.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dps_app_rw') THEN
        CREATE ROLE dps_app_rw NOLOGIN NOSUPERUSER NOBYPASSRLS;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dps_worker') THEN
        CREATE ROLE dps_worker NOLOGIN NOSUPERUSER NOBYPASSRLS;
    END IF;
END$$;
REVOKE ALL ON SCHEMA __SCHEMA__ FROM PUBLIC;
GRANT USAGE ON SCHEMA __SCHEMA__ TO dps_app_rw, dps_worker;
```

`dps/migrations/002_tenancy_and_keys.sql`:
```sql
-- 002_tenancy_and_keys.sql — per-tenant overrides and the API-key table (M5 uses it).
-- Both tables are GLOBAL (no RLS): caps are read by the cross-tenant claim query (dps/jobs.py) and
-- keys are looked up before a tenant is known. A missing dps_tenant row means "settings defaults";
-- the API inserts an all-NULL row the first time a tenant creates a connection or a job, so the
-- retention job can enumerate tenants.
CREATE TABLE __SCHEMA__.dps_tenant (
    tenant_id      text PRIMARY KEY CHECK (tenant_id ~ '^[a-z0-9_][a-z0-9_-]{0,62}$'),
    display_name   text,
    retention_days int CHECK (retention_days BETWEEN 1 AND 3650),
    max_running    int CHECK (max_running > 0),
    max_queued     int CHECK (max_queued > 0),
    note           text,
    created_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE __SCHEMA__.dps_api_key (
    id           text PRIMARY KEY,
    key_hash     text NOT NULL UNIQUE,
    name         text NOT NULL,
    tenant_ids   text[] NOT NULL DEFAULT '{}',
    scopes       text[] NOT NULL DEFAULT '{}',
    created_by   text,
    created_at   timestamptz NOT NULL DEFAULT now(),
    expires_at   timestamptz,
    last_used_at timestamptz,
    disabled_at  timestamptz,
    rotated_from text
);
```
(The contract's tenant regex is `^[a-z0-9][a-z0-9_-]{0,62}$`; this file also admits a leading `_` so the internal `_system` tenant that owns retention job rows (Task 23) can have a row — listed in "Open contract questions".)

`dps/migrations/003_connections.sql`:
```sql
-- 003_connections.sql — the connection registry. params never holds credentials (ConnectionParams);
-- secret_enc is the sealed InlineCredentials (AAD "dps_connection.secret:<id>"), nulled by retention
-- after secret_expires_at — hence "at most one" here, "exactly one at create" in the API.
CREATE TABLE __SCHEMA__.dps_connection (
    id                text PRIMARY KEY,
    tenant_id         text NOT NULL,
    name              text NOT NULL,
    engine            text NOT NULL CHECK (engine IN ('postgres','oracle','mongo','s3','pvc')),
    params            jsonb NOT NULL,
    secret_ref        text,
    secret_enc        bytea,
    secret_expires_at timestamptz,
    roles             text[] NOT NULL DEFAULT '{source}'
                      CHECK (cardinality(roles) > 0 AND roles <@ ARRAY['source','target']::text[]),
    tags              text[] NOT NULL DEFAULT '{}',
    capabilities      jsonb NOT NULL DEFAULT '{}'::jsonb,
    last_test         jsonb,
    created_by        text,
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, name),
    CONSTRAINT dps_connection_one_secret CHECK (secret_ref IS NULL OR secret_enc IS NULL)
);
CREATE INDEX dps_connection_feed ON __SCHEMA__.dps_connection (tenant_id, created_at DESC, id DESC);
```

`dps/migrations/004_jobs_and_partitions.sql`:
```sql
-- 004_jobs_and_partitions.sql — job groups, jobs, partitions, the worker registry, and two helpers.
-- Additions to the contract DDL (all value-free): dps_job.plan (the RunPlan written at prepare:
-- column plans, resolved target summary), dps_job_partition.meta (files: per-part object keys, rows,
-- sha256), indexes dps_part_job / dps_job_expires / dps_group_feed, and dps_jsonb_add +
-- dps_jsonb_sum for server-side counter maps (values_by_method).
CREATE TABLE __SCHEMA__.dps_job_group (
    id               text PRIMARY KEY,
    tenant_id        text NOT NULL,
    name             text NOT NULL,
    kid              text NOT NULL,
    seed_fingerprint text NOT NULL,
    created_by       text,
    created_at       timestamptz NOT NULL DEFAULT now(),
    closed_at        timestamptz,
    UNIQUE (tenant_id, name)
);
CREATE INDEX dps_group_feed ON __SCHEMA__.dps_job_group (tenant_id, created_at DESC, id DESC);

CREATE TABLE __SCHEMA__.dps_job (
    id                   text PRIMARY KEY,
    tenant_id            text NOT NULL,
    kind                 text NOT NULL CHECK (kind IN ('discover','run','scan','retention')),
    state                text NOT NULL DEFAULT 'queued' CHECK (state IN
                         ('queued','running','waiting','paused','succeeded','failed','dead','canceled')),
    phase                text NOT NULL CHECK (phase IN
                         ('discover','prepare','partitions','finalize','scan','retention')),
    mode                 text CHECK (mode IN ('obfuscate','synthesize')),
    group_id             text REFERENCES __SCHEMA__.dps_job_group(id),
    connection_id        text,
    target_connection_id text,
    object_ref           text,
    snapshot_id          text,
    spec                 jsonb NOT NULL,
    purpose              jsonb NOT NULL DEFAULT '{}'::jsonb,
    key_enc              bytea,
    spec_enc             bytea,
    kid                  text,
    progress             jsonb NOT NULL DEFAULT '{}'::jsonb,
    plan                 jsonb,
    priority             smallint NOT NULL DEFAULT 100,
    attempts             int NOT NULL DEFAULT 0,
    max_attempts         int NOT NULL DEFAULT 3,
    run_after            timestamptz NOT NULL DEFAULT now(),
    lease_expires_at     timestamptz,
    locked_by            text,
    error_code           text,
    error                text,
    idempotency_key      text,
    created_by           text,
    created_at           timestamptz NOT NULL DEFAULT now(),
    updated_at           timestamptz NOT NULL DEFAULT now(),
    started_at           timestamptz,
    finished_at          timestamptz,
    expires_at           timestamptz
) WITH (fillfactor = 70, autovacuum_vacuum_scale_factor = 0.02, autovacuum_analyze_scale_factor = 0.02);
CREATE UNIQUE INDEX dps_job_idem ON __SCHEMA__.dps_job (tenant_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;
CREATE INDEX dps_job_claim ON __SCHEMA__.dps_job (kind, priority, run_after, created_at, id)
    WHERE state = 'queued';
CREATE INDEX dps_job_lease ON __SCHEMA__.dps_job (lease_expires_at) WHERE state = 'running';
CREATE INDEX dps_job_feed ON __SCHEMA__.dps_job (tenant_id, created_at DESC, id DESC);
CREATE INDEX dps_job_expires ON __SCHEMA__.dps_job (expires_at) WHERE expires_at IS NOT NULL;

CREATE TABLE __SCHEMA__.dps_job_partition (
    id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    job_id           text NOT NULL REFERENCES __SCHEMA__.dps_job(id) ON DELETE CASCADE,
    tenant_id        text NOT NULL,
    partition_no     int NOT NULL,
    lower_key        jsonb,
    upper_key        jsonb,
    state            text NOT NULL DEFAULT 'queued'
                     CHECK (state IN ('queued','running','done','failed','dead','canceled')),
    attempts         int NOT NULL DEFAULT 0,
    max_attempts     int NOT NULL DEFAULT 5,
    run_after        timestamptz NOT NULL DEFAULT now(),
    lease_expires_at timestamptz,
    locked_by        text,
    last_key         jsonb,
    batch_no         int NOT NULL DEFAULT 0,
    rows_read        bigint NOT NULL DEFAULT 0,
    rows_written     bigint NOT NULL DEFAULT 0,
    rows_skipped     bigint NOT NULL DEFAULT 0,
    values_by_method jsonb NOT NULL DEFAULT '{}'::jsonb,
    verify_checked   bigint NOT NULL DEFAULT 0,
    resumed          int NOT NULL DEFAULT 0,
    meta             jsonb NOT NULL DEFAULT '{}'::jsonb,
    error_code       text,
    error            text,
    started_at       timestamptz,
    finished_at      timestamptz,
    updated_at       timestamptz NOT NULL DEFAULT now(),
    UNIQUE (job_id, partition_no)
) WITH (fillfactor = 70, autovacuum_vacuum_scale_factor = 0.02, autovacuum_analyze_scale_factor = 0.02);
CREATE INDEX dps_part_claim ON __SCHEMA__.dps_job_partition (run_after, job_id, partition_no)
    WHERE state = 'queued';
CREATE INDEX dps_part_lease ON __SCHEMA__.dps_job_partition (lease_expires_at) WHERE state = 'running';
CREATE INDEX dps_part_job ON __SCHEMA__.dps_job_partition (job_id, state);

CREATE TABLE __SCHEMA__.dps_worker (
    worker_id  text PRIMARY KEY,
    role       text NOT NULL CHECK (role IN ('worker','browser-worker')),
    version    text NOT NULL,
    started_at timestamptz NOT NULL DEFAULT now(),
    last_seen  timestamptz NOT NULL DEFAULT now(),
    draining   boolean NOT NULL DEFAULT false,
    busy       int NOT NULL DEFAULT 0
);

-- Sum two {"name": number} maps key by key (missing keys count as 0).
CREATE OR REPLACE FUNCTION __SCHEMA__.dps_jsonb_add(a jsonb, b jsonb) RETURNS jsonb
LANGUAGE sql IMMUTABLE PARALLEL SAFE SET search_path = pg_catalog AS $$
    SELECT COALESCE(jsonb_object_agg(k, COALESCE((a ->> k)::numeric, 0) + COALESCE((b ->> k)::numeric, 0)),
                    '{}'::jsonb)
    FROM (SELECT jsonb_object_keys(COALESCE(a, '{}'::jsonb)) AS k
          UNION SELECT jsonb_object_keys(COALESCE(b, '{}'::jsonb))) AS keys
$$;
CREATE OR REPLACE AGGREGATE __SCHEMA__.dps_jsonb_sum(jsonb) (
    SFUNC = __SCHEMA__.dps_jsonb_add, STYPE = jsonb, INITCOND = '{}'
);
```

`dps/migrations/005_snapshots.sql`:
```sql
-- 005_snapshots.sql — classification snapshots (contract §5.4). An accepted snapshot is immutable;
-- accepting another for the same (connection, object) supersedes it (dps/snapshots.py).
CREATE TABLE __SCHEMA__.dps_snapshot (
    id            text PRIMARY KEY,
    tenant_id     text NOT NULL,
    job_id        text REFERENCES __SCHEMA__.dps_job(id) ON DELETE SET NULL,
    connection_id text NOT NULL,
    engine        text NOT NULL,
    object_ref    text NOT NULL,
    status        text NOT NULL DEFAULT 'proposed' CHECK (status IN ('proposed','accepted','superseded')),
    policy_name   text NOT NULL,
    policy_hash   text NOT NULL,
    shape         jsonb NOT NULL,
    columns       jsonb NOT NULL,
    sample_rows   int NOT NULL,
    overrides     jsonb NOT NULL DEFAULT '[]'::jsonb,
    content_hash  text NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now(),
    accepted_at   timestamptz,
    accepted_by   text
);
CREATE UNIQUE INDEX dps_snapshot_one_accepted ON __SCHEMA__.dps_snapshot (tenant_id, connection_id, object_ref)
    WHERE status = 'accepted';
CREATE INDEX dps_snapshot_feed ON __SCHEMA__.dps_snapshot (tenant_id, connection_id, object_ref, created_at DESC);
```

`dps/migrations/006_evidence_and_artefacts.sql`:
```sql
-- 006_evidence_and_artefacts.sql — artefact rows (objects live in the ObjectStore) and evidence packs.
-- dps_evidence is append-only except its rendering artefact ids (html now, pdf in M5).
CREATE TABLE __SCHEMA__.dps_artefact (
    id           text PRIMARY KEY,
    tenant_id    text NOT NULL,
    job_id       text REFERENCES __SCHEMA__.dps_job(id) ON DELETE CASCADE,
    kind         text NOT NULL CHECK (kind IN ('evidence_html','evidence_pdf','evidence_json','screenshot',
                                               'capture','export_csv','export_json','manifest','redacted_pdf')),
    store        text NOT NULL CHECK (store IN ('local','s3')),
    object_key   text NOT NULL,
    content_type text NOT NULL,
    bytes        bigint NOT NULL,
    sha256       text NOT NULL,
    meta         jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at   timestamptz NOT NULL DEFAULT now(),
    expires_at   timestamptz
);
CREATE INDEX dps_artefact_job ON __SCHEMA__.dps_artefact (job_id);

CREATE TABLE __SCHEMA__.dps_evidence (
    id               text PRIMARY KEY,
    tenant_id        text NOT NULL,
    job_id           text NOT NULL REFERENCES __SCHEMA__.dps_job(id) ON DELETE CASCADE,
    pack             jsonb NOT NULL,
    pack_sha256      text NOT NULL,
    pack_version     text NOT NULL DEFAULT 'dps.evidence.v1',
    html_artefact_id text,
    pdf_artefact_id  text,
    created_at       timestamptz NOT NULL DEFAULT now(),
    expires_at       timestamptz
);
CREATE INDEX dps_evidence_feed ON __SCHEMA__.dps_evidence (tenant_id, created_at DESC, id DESC);
CREATE INDEX dps_evidence_job ON __SCHEMA__.dps_evidence (job_id);

CREATE OR REPLACE FUNCTION __SCHEMA__.dps_evidence_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
    IF NEW.id IS DISTINCT FROM OLD.id OR NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
       OR NEW.job_id IS DISTINCT FROM OLD.job_id OR NEW.pack IS DISTINCT FROM OLD.pack
       OR NEW.pack_sha256 IS DISTINCT FROM OLD.pack_sha256
       OR NEW.pack_version IS DISTINCT FROM OLD.pack_version
       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION 'dps_evidence is append-only (only artefact ids may change)';
    END IF;
    RETURN NEW;
END$$;
CREATE TRIGGER dps_evidence_append_only BEFORE UPDATE ON __SCHEMA__.dps_evidence
    FOR EACH ROW EXECUTE FUNCTION __SCHEMA__.dps_evidence_guard();
```

`dps/migrations/007_audit_log.sql`:
```sql
-- 007_audit_log.sql — append-only audit log, monthly RANGE partitions created by
-- dps.db.ensure_audit_partitions (owner role, inside the migration advisory lock) and dropped only
-- by `python -m dps.migrate --maintain`. No DEFAULT partition: a missing month fails the writer's
-- batch loudly (readiness "audit") instead of silently landing in a partition that later blocks
-- CREATE TABLE … PARTITION OF.
CREATE TABLE __SCHEMA__.dps_audit_log (
    id        bigint GENERATED ALWAYS AS IDENTITY,
    ts        timestamptz NOT NULL DEFAULT now(),
    tenant_id text NOT NULL,
    key_id    text,
    trace_id  text,
    route     text NOT NULL,
    action    text NOT NULL,
    mode      text,
    policy    text,
    outcome   text NOT NULL,
    status    smallint,
    job_id    text,
    counts    jsonb NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (ts, id)
) PARTITION BY RANGE (ts);
CREATE INDEX dps_audit_tenant_ts ON __SCHEMA__.dps_audit_log (tenant_id, ts DESC);

CREATE OR REPLACE FUNCTION __SCHEMA__.dps_audit_log_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
    RAISE EXCEPTION 'dps_audit_log is append-only';
END$$;
CREATE TRIGGER dps_audit_log_append_only BEFORE UPDATE OR DELETE ON __SCHEMA__.dps_audit_log
    FOR EACH ROW EXECUTE FUNCTION __SCHEMA__.dps_audit_log_guard();
```

`dps/migrations/008_rls_policies.sql`:
```sql
-- 008_rls_policies.sql — FORCE row-level security on every tenant table (contract §2.4), plus the
-- role-targeted worker_claim policy on the two queue tables. Permissive policies OR-combine, so
-- worker_claim widens visibility only for members of dps_worker (dps_worker_login), never dps_app.
DO $$
DECLARE
    t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['dps_connection','dps_job_group','dps_job','dps_job_partition',
                             'dps_snapshot','dps_artefact','dps_evidence','dps_audit_log'] LOOP
        EXECUTE format('ALTER TABLE %s.%I ENABLE ROW LEVEL SECURITY', '__SCHEMA__', t);
        EXECUTE format('ALTER TABLE %s.%I FORCE ROW LEVEL SECURITY', '__SCHEMA__', t);
        EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON %s.%I', '__SCHEMA__', t);
        EXECUTE format('CREATE POLICY tenant_isolation ON %s.%I '
                       'USING (tenant_id = current_setting(''app.tenant_id'', true)) '
                       'WITH CHECK (tenant_id = current_setting(''app.tenant_id'', true))', '__SCHEMA__', t);
    END LOOP;
END$$;
DROP POLICY IF EXISTS worker_claim ON __SCHEMA__.dps_job;
CREATE POLICY worker_claim ON __SCHEMA__.dps_job TO dps_worker USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS worker_claim ON __SCHEMA__.dps_job_partition;
CREATE POLICY worker_claim ON __SCHEMA__.dps_job_partition TO dps_worker USING (true) WITH CHECK (true);
```
(`'__SCHEMA__'` inside the string literals becomes `'"dps"'`; `format('%s', …)` inserts it verbatim.)

`dps/migrations/009_grants.sql`:
```sql
-- 009_grants.sql — explicit per-table grants to the group roles; nothing to PUBLIC, never
-- "ALL TABLES IN SCHEMA" (that would reach audit partitions directly and bypass the parent's RLS).
GRANT SELECT, INSERT, UPDATE ON __SCHEMA__.dps_tenant TO dps_app_rw;
GRANT SELECT, INSERT, UPDATE ON __SCHEMA__.dps_api_key TO dps_app_rw;
GRANT SELECT ON __SCHEMA__.dps_migration_ledger TO dps_app_rw;
GRANT SELECT, INSERT, UPDATE, DELETE ON
    __SCHEMA__.dps_connection, __SCHEMA__.dps_job_group, __SCHEMA__.dps_job,
    __SCHEMA__.dps_job_partition, __SCHEMA__.dps_snapshot, __SCHEMA__.dps_artefact
TO dps_app_rw;
GRANT SELECT, INSERT, UPDATE ON __SCHEMA__.dps_evidence TO dps_app_rw;       -- UPDATE: artefact ids only (trigger)
GRANT SELECT, INSERT ON __SCHEMA__.dps_audit_log TO dps_app_rw;
GRANT SELECT ON __SCHEMA__.dps_worker TO dps_app_rw;                         -- /jobs/stats
GRANT SELECT, INSERT, UPDATE, DELETE ON __SCHEMA__.dps_worker TO dps_worker; -- heartbeats, pruning
GRANT EXECUTE ON FUNCTION __SCHEMA__.dps_jsonb_add(jsonb, jsonb) TO dps_app_rw, dps_worker;
GRANT EXECUTE ON FUNCTION __SCHEMA__.dps_jsonb_sum(jsonb) TO dps_app_rw, dps_worker;
DO $$
DECLARE
    s text;
BEGIN
    FOREACH s IN ARRAY ARRAY[pg_get_serial_sequence('__SCHEMA__.dps_job_partition', 'id'),
                             pg_get_serial_sequence('__SCHEMA__.dps_audit_log', 'id')] LOOP
        EXECUTE format('GRANT USAGE, SELECT ON SEQUENCE %s TO dps_app_rw', s);
    END LOOP;
END$$;
```
(`dps_migration_ledger` is created by the runner before 001, so the grant in 009 always finds it.)

- [ ] **Step 2: Write the fixtures** `tests/fixtures/db.py`

```python
"""Metadata-DB fixtures: one migrated database per test session, pools per test (contract §12.2)."""
from __future__ import annotations

import asyncio
import base64
import os
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from urllib.parse import urlparse

import asyncpg
import pytest
import pytest_asyncio

from dps import db
from dps.config import Settings
from tests.fixtures.services import require_service

TEST_KEK = bytes(range(32))            # test-only constant; never a production value
ROLES_SQL = """
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='dps_owner') THEN
    CREATE ROLE dps_owner LOGIN PASSWORD 'dps_owner' NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOINHERIT; END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='dps_app_rw') THEN CREATE ROLE dps_app_rw NOLOGIN; END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='dps_worker') THEN CREATE ROLE dps_worker NOLOGIN; END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='dps_app') THEN
    CREATE ROLE dps_app LOGIN PASSWORD 'dps_app' NOSUPERUSER NOBYPASSRLS; END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='dps_worker_login') THEN
    CREATE ROLE dps_worker_login LOGIN PASSWORD 'dps_worker_login' NOSUPERUSER NOBYPASSRLS; END IF;
  GRANT dps_app_rw TO dps_app;
  GRANT dps_app_rw, dps_worker TO dps_worker_login;
END$$;"""


def make_db_settings(admin_dsn: str, database: str, **overrides: object) -> Settings:
    """Settings that point every login at ``database`` with the local demo passwords."""
    parsed = urlparse(admin_dsn)
    base: dict[str, object] = dict(
        _env_file=None, env="local", auth_enabled=False, llm_provider="stub",
        pg_host=parsed.hostname or "127.0.0.1", pg_port=parsed.port or 5432, pg_database=database,
        pg_user="dps_app", pg_password="dps_app", pg_worker_user="dps_worker_login",
        pg_worker_password="dps_worker_login", pg_migration_user="dps_owner",
        pg_migration_password="dps_owner", migrations_mode="off", pg_pool_min=1, pg_pool_max=8,
        job_kek=base64.b64encode(TEST_KEK).decode(),
    )
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


async def create_database(admin_dsn: str, name: str) -> None:
    conn = await asyncpg.connect(admin_dsn)
    try:
        await conn.execute(ROLES_SQL)
        await conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await conn.execute(f'CREATE DATABASE "{name}" OWNER dps_owner')
        await conn.execute(f"ALTER DATABASE \"{name}\" SET timezone TO 'UTC'")
        await conn.execute(f'GRANT CONNECT ON DATABASE "{name}" TO dps_app_rw, dps_worker')
    finally:
        await conn.close()


async def drop_database(admin_dsn: str, name: str) -> None:
    conn = await asyncpg.connect(admin_dsn)
    try:
        await conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    finally:
        await conn.close()


async def _migrate(settings: Settings) -> None:
    conn = await db.open_migration_connection(settings)
    try:
        await db.run_migrations(settings, connection=conn)
    finally:
        await conn.close()


async def _truncate(settings: Settings) -> None:
    conn = await db.open_migration_connection(settings)
    schema = db.schema_ident(settings)
    try:
        await conn.execute(
            f"TRUNCATE {schema}.dps_audit_log, {schema}.dps_evidence, {schema}.dps_artefact, "
            f"{schema}.dps_snapshot, {schema}.dps_job_partition, {schema}.dps_job, "
            f"{schema}.dps_job_group, {schema}.dps_connection, {schema}.dps_tenant, "
            f"{schema}.dps_worker, {schema}.dps_api_key RESTART IDENTITY CASCADE")
    finally:
        await conn.close()


@pytest.fixture(scope="session")
def pg_admin_dsn() -> str:
    return require_service("dps-db")


@pytest.fixture(scope="session")
def db_settings(pg_admin_dsn: str) -> Iterator[Settings]:
    name = f"dps_test_{os.getpid()}"
    asyncio.run(create_database(pg_admin_dsn, name))
    settings = make_db_settings(pg_admin_dsn, name)
    asyncio.run(_migrate(settings))
    yield settings
    asyncio.run(drop_database(pg_admin_dsn, name))


@pytest_asyncio.fixture
async def migrated_db(db_settings: Settings) -> AsyncIterator[Settings]:
    """Pools bound to this test's loop; all tables truncated afterwards."""
    await db.init_pool(db_settings)
    await db.init_worker_pool(db_settings)
    try:
        yield db_settings
    finally:
        await db.close_pools()
        await _truncate(db_settings)


@pytest.fixture
def kek() -> bytes:
    return TEST_KEK


@pytest.fixture
def tenant_conn(migrated_db: Settings) -> Callable[..., object]:
    """``async with tenant_conn("t1") as conn:`` — the API pool with the tenant GUC bound."""
    return db.acquire


@pytest.fixture
def queue(migrated_db: Settings):  # noqa: ANN201 - the dps.jobs module (Task 8) bound to the test DB
    import dps.jobs
    return dps.jobs


@pytest_asyncio.fixture
async def fresh_db(pg_admin_dsn: str) -> AsyncIterator[Callable[..., Awaitable[Settings]]]:
    """Factory for empty, unmigrated databases (migration-runner tests); dropped afterwards."""
    made: list[str] = []

    async def make(suffix: str = "x", **overrides: object) -> Settings:
        name = f"dps_fresh_{os.getpid()}_{suffix}"
        await create_database(pg_admin_dsn, name)
        made.append(name)
        return make_db_settings(pg_admin_dsn, name, **overrides)

    yield make
    await db.close_pools()
    for name in made:
        await drop_database(pg_admin_dsn, name)
```

- [ ] **Step 3: Write the failing tests** `tests/db/test_migrations.py`

```python
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from datetime import UTC, datetime

import pytest

from dps import db

pytestmark = pytest.mark.db
FILES = [p.name for p in db.migration_files()]


async def _owner(settings):  # noqa: ANN001, ANN202
    return await db.open_migration_connection(settings)


async def test_files_are_the_contract_set() -> None:
    assert FILES == ["001_schema_and_roles.sql", "002_tenancy_and_keys.sql", "003_connections.sql",
                     "004_jobs_and_partitions.sql", "005_snapshots.sql", "006_evidence_and_artefacts.sql",
                     "007_audit_log.sql", "008_rls_policies.sql", "009_grants.sql"]


async def test_fresh_database_applies_every_file_once(fresh_db) -> None:  # noqa: ANN001
    s = await fresh_db("once")
    assert await db.run_migrations(s) == FILES
    assert await db.run_migrations(s) == []
    conn = await _owner(s)
    try:
        assert await conn.fetchval('SELECT count(*) FROM "dps".dps_migration_ledger') == len(FILES)
        tables = {r["tablename"] for r in await conn.fetch("SELECT tablename FROM pg_tables WHERE schemaname='dps'")}
        assert set(db.TENANT_TABLES) | {"dps_tenant", "dps_api_key", "dps_worker", "dps_migration_ledger"} <= tables
    finally:
        await conn.close()


async def test_concurrent_runners_apply_each_file_exactly_once(fresh_db) -> None:  # noqa: ANN001
    s = await fresh_db("race")
    a, b = await asyncio.gather(db.run_migrations(s), db.run_migrations(s))
    assert sorted(a + b) == FILES and not set(a) & set(b)


async def test_drift_is_refused_and_never_replayed(fresh_db) -> None:  # noqa: ANN001
    s = await fresh_db("drift")
    await db.run_migrations(s)
    conn = await _owner(s)
    try:
        await conn.execute("UPDATE \"dps\".dps_migration_ledger SET checksum = 'x' WHERE filename = '003_connections.sql'")
    finally:
        await conn.close()
    with pytest.raises(db.MigrationDrift) as err:
        await db.run_migrations(s)
    assert "003_connections.sql" in str(err.value) and "CREATE TABLE" not in str(err.value)
    await db.init_pool(s)
    with pytest.raises(db.MigrationDrift):
        await db.verify_migrations(s)


async def test_verify_names_pending_files(fresh_db) -> None:  # noqa: ANN001
    s = await fresh_db("pending")
    await db.run_migrations(s)
    conn = await _owner(s)
    try:
        await conn.execute("DELETE FROM \"dps\".dps_migration_ledger WHERE filename = '009_grants.sql'")
    finally:
        await conn.close()
    await db.init_pool(s)
    with pytest.raises(db.MigrationDrift) as err:
        await db.verify_migrations(s)
    assert "009_grants.sql: pending" in str(err.value)


async def test_alternate_schema_name(fresh_db) -> None:  # noqa: ANN001
    s = await fresh_db("alt", pg_schema="dps_alt")
    await db.run_migrations(s)
    conn = await _owner(s)
    try:
        assert await conn.fetchval("SELECT to_regclass('\"dps_alt\".dps_job') IS NOT NULL")
    finally:
        await conn.close()


async def test_audit_partitions_ahead_and_maintenance(fresh_db) -> None:  # noqa: ANN001
    s = await fresh_db("audit", audit_retention_months=13)
    await db.run_migrations(s)
    conn = await _owner(s)
    try:
        names = {r["relname"] for r in await conn.fetch(
            "SELECT c.relname FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid "
            "WHERE i.inhparent = '\"dps\".dps_audit_log'::regclass")}
        now = datetime.now(UTC)
        assert f"dps_audit_log_{now:%Y_%m}" in names and len(names) == 4
        await conn.execute("CREATE TABLE \"dps\".dps_audit_log_2020_01 PARTITION OF \"dps\".dps_audit_log "
                           "FOR VALUES FROM ('2020-01-01') TO ('2020-02-01')")
        assert await db.drop_expired_audit_partitions(conn, s) == ["dps_audit_log_2020_01"]
    finally:
        await conn.close()


async def test_migrate_cli_exit_codes(fresh_db) -> None:  # noqa: ANN001
    s = await fresh_db("cli")
    env = {**os.environ, "DPS_PG_HOST": s.pg_host, "DPS_PG_PORT": str(s.pg_port),
           "DPS_PG_DATABASE": s.pg_database, "DPS_PG_USER": "dps_app", "DPS_PG_PASSWORD": "dps_app",
           "DPS_PG_MIGRATION_USER": "dps_owner", "DPS_PG_MIGRATION_PASSWORD": "dps_owner"}

    def run(*args: str) -> int:
        return subprocess.run([sys.executable, "-m", "dps.migrate", *args], env=env,
                              capture_output=True, timeout=120).returncode

    assert run("--verify") == 1
    assert run() == 0 and run("--verify") == 0 and run("--maintain") == 0
```

- [ ] **Step 4: Run to confirm failure**

Run: `pytest -q tests/db/test_migrations.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'dps.db'` (or errors importing the fixtures).

- [ ] **Step 5: Write `dps/db.py`** (adapted from `di/db.py`; RLS posture functions land in Task 5)

```python
"""Metadata database: asyncpg pools, the RLS tenant GUC, migrations and audit partitions.

Mirrors ``di/db.py``: an API pool logged in as ``dps_app`` and a worker pool logged in as
``dps_worker_login`` (the only login in the ``dps_worker`` group, which the ``worker_claim``
policies on the queue tables target). Every tenant-scoped statement runs inside
``acquire(tenant_id)``, which binds ``app.tenant_id`` for the checkout and resets it on release;
``search_path`` is a startup parameter so asyncpg's ``RESET ALL`` on release keeps it. All SQL in
DPS is schema-qualified anyway. DDL only ever runs on the migration connection (``dps_owner``)
under ``pg_advisory_lock(hashtext('dps.migrate.' || schema))``; applied files are checksummed in
``dps_migration_ledger`` and never edited.
"""
from __future__ import annotations

import hashlib
import json
import re
import ssl
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from pathlib import Path

import asyncpg

from dps.config import Settings, get_settings
from dps.observability import get_logger

logger = get_logger(__name__)

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
TENANT_GUC = "app.tenant_id"
TENANT_TABLES: tuple[str, ...] = (
    "dps_connection", "dps_job_group", "dps_job", "dps_job_partition", "dps_snapshot",
    "dps_artefact", "dps_evidence", "dps_audit_log",
)
GLOBAL_TABLES: tuple[str, ...] = ("dps_tenant", "dps_api_key", "dps_worker", "dps_migration_ledger")
ALLOWED_EXTRA_POLICIES: dict[str, str] = {"dps_job": "worker_claim", "dps_job_partition": "worker_claim"}
_SCHEMA_RE = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
_AUDIT_PART_RE = re.compile(r"^dps_audit_log_(\d{4})_(\d{2})$")

_pool: asyncpg.Pool | None = None
_worker_pool: asyncpg.Pool | None = None
_settings: Settings | None = None


class MigrationDrift(RuntimeError):
    """Shipped migration files and the ledger disagree (pending or changed files, by name only)."""


def db_configured(settings: Settings) -> bool:
    """True when a metadata database is configured (Layer 1 runs without one)."""
    return bool(settings.pg_host)


def active_settings() -> Settings:
    """The settings the pools were initialised with (falls back to the process settings)."""
    return _settings or get_settings()


def schema_ident(settings: Settings | None = None) -> str:
    """The validated, double-quoted schema identifier, e.g. ``'"dps"'``."""
    name = (settings or active_settings()).pg_schema
    if not _SCHEMA_RE.fullmatch(name):
        raise ValueError("pg_schema must match ^[a-z_][a-z0-9_]{0,62}$")
    return f'"{name}"'


def rowcount(command_tag: str) -> int:
    """Rows affected from an asyncpg command tag (``"UPDATE 3"`` → 3)."""
    parts = command_tag.split()
    return int(parts[-1]) if parts and parts[-1].isdigit() else 0


def migration_files() -> list[Path]:
    """Shipped ``NNN_*.sql`` files in apply order."""
    return sorted(MIGRATIONS_DIR.glob("[0-9][0-9][0-9]_*.sql"))


def _checksum(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _ssl_context(settings: Settings) -> ssl.SSLContext | None:
    if not settings.pg_ssl_root_cert:
        return None
    ctx = ssl.create_default_context(cafile=settings.pg_ssl_root_cert)
    ctx.check_hostname = True
    ctx.verify_mode = ssl.CERT_REQUIRED
    return ctx


async def _init_conn(conn: asyncpg.Connection) -> None:
    for typ in ("json", "jsonb"):
        await conn.set_type_codec(typ, encoder=json.dumps, decoder=json.loads, schema="pg_catalog")


def _server_settings(settings: Settings, app_name: str) -> dict[str, str]:
    return {"application_name": app_name, "search_path": f"{schema_ident(settings)}, public",
            "timezone": "UTC"}


async def init_pool(settings: Settings | None = None) -> asyncpg.Pool:
    """The API pool (``dps_app``), created once per process."""
    global _pool, _settings
    if _pool is not None:
        return _pool
    settings = settings or active_settings()
    _settings = settings
    _pool = await asyncpg.create_pool(
        host=settings.pg_host, port=settings.pg_port, user=settings.pg_user,
        password=settings.pg_password or None, database=settings.pg_database,
        ssl=_ssl_context(settings), min_size=settings.pg_pool_min, max_size=settings.pg_pool_max,
        command_timeout=60, max_inactive_connection_lifetime=300, init=_init_conn,
        server_settings=_server_settings(settings, "dps-app"),
    )
    return _pool


async def init_worker_pool(settings: Settings | None = None) -> asyncpg.Pool:
    """The worker pool (``dps_worker_login``): cross-tenant queue access via ``worker_claim``."""
    global _worker_pool, _settings
    if _worker_pool is not None:
        return _worker_pool
    settings = settings or active_settings()
    _settings = _settings or settings
    user, password = settings.pg_worker_login
    _worker_pool = await asyncpg.create_pool(
        host=settings.pg_host, port=settings.pg_port, user=user, password=password or None,
        database=settings.pg_database, ssl=_ssl_context(settings), min_size=1,
        max_size=max(4, settings.worker_concurrency * 2 + 4), command_timeout=60,
        max_inactive_connection_lifetime=300, init=_init_conn,
        server_settings=_server_settings(settings, "dps-worker"),
    )
    return _worker_pool


async def close_pools() -> None:
    """Close both pools (tests call this between event loops)."""
    global _pool, _worker_pool, _settings
    for pool in (_pool, _worker_pool):
        if pool is not None:
            await pool.close()
    _pool = _worker_pool = None
    _settings = None


async def _bind(conn: asyncpg.Connection, tenant_id: str | None) -> bool:
    if tenant_id is None:
        return False
    await conn.execute("SELECT set_config($1, $2, false)", TENANT_GUC, tenant_id)
    return True


@asynccontextmanager
async def acquire(tenant_id: str | None) -> AsyncIterator[asyncpg.Connection]:
    """An API-pool connection with ``app.tenant_id`` bound (``None`` binds nothing → RLS sees no rows)."""
    pool = await init_pool(_settings)
    async with pool.acquire() as conn:
        bound = await _bind(conn, tenant_id)
        try:
            yield conn
        finally:
            if bound:
                await conn.execute("SELECT set_config($1, '', false)", TENANT_GUC)


@asynccontextmanager
async def acquire_queue(tenant_id: str | None = None) -> AsyncIterator[asyncpg.Connection]:
    """A worker-pool connection; ``None`` = cross-tenant queue access, else the tenant GUC is bound."""
    pool = await init_worker_pool(_settings)
    async with pool.acquire() as conn:
        bound = await _bind(conn, tenant_id)
        try:
            yield conn
        finally:
            if bound:
                await conn.execute("SELECT set_config($1, '', false)", TENANT_GUC)


async def open_migration_connection(settings: Settings | None = None) -> asyncpg.Connection:
    """One dedicated connection as the migration role; the caller closes it."""
    settings = settings or active_settings()
    user, password = settings.pg_migration_login
    conn = await asyncpg.connect(
        host=settings.pg_host, port=settings.pg_port, user=user, password=password or None,
        database=settings.pg_database, ssl=_ssl_context(settings),
        server_settings={"application_name": "dps-migrate", "timezone": "UTC"},
    )
    await _init_conn(conn)
    return conn


async def _ensure_ledger(conn: asyncpg.Connection, schema: str) -> None:
    await conn.execute(
        f"CREATE TABLE IF NOT EXISTS {schema}.dps_migration_ledger ("
        " filename text PRIMARY KEY, checksum text NOT NULL,"
        " applied_at timestamptz NOT NULL DEFAULT now())"
    )


async def _ledger(conn: asyncpg.Connection, schema: str) -> dict[str, str]:
    exists = await conn.fetchval("SELECT to_regclass($1)", f"{schema}.dps_migration_ledger")
    if exists is None:
        return {}
    rows = await conn.fetch(f"SELECT filename, checksum FROM {schema}.dps_migration_ledger")
    return {r["filename"]: r["checksum"] for r in rows}


def _drift(ledger: dict[str, str], *, include_pending: bool) -> list[str]:
    shipped = {p.name: _checksum(p) for p in migration_files()}
    problems = [f"{name}: checksum drift" for name, cs in ledger.items()
                if name in shipped and shipped[name] != cs]
    problems += [f"{name}: applied but no longer shipped" for name in ledger if name not in shipped]
    if include_pending:
        problems += [f"{name}: pending" for name in shipped if name not in ledger]
    return sorted(problems)


async def run_migrations(settings: Settings | None = None, *,
                         connection: asyncpg.Connection | None = None) -> list[str]:
    """Apply pending files in order under the advisory lock; return the names applied now."""
    settings = settings or active_settings()
    schema = schema_ident(settings)
    conn = connection or await open_migration_connection(settings)
    applied: list[str] = []
    try:
        await conn.execute("SELECT pg_advisory_lock(hashtext($1))", f"dps.migrate.{settings.pg_schema}")
        try:
            await conn.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
            await _ensure_ledger(conn, schema)
            ledger = await _ledger(conn, schema)
            problems = _drift(ledger, include_pending=False)
            if problems:
                raise MigrationDrift("refusing to migrate: " + "; ".join(problems))
            for path in migration_files():
                checksum = _checksum(path)
                if ledger.get(path.name) == checksum:
                    continue
                sql = path.read_text(encoding="utf-8").replace("__SCHEMA__", schema)
                async with conn.transaction():
                    await conn.execute(sql)
                    await conn.execute(
                        f"INSERT INTO {schema}.dps_migration_ledger (filename, checksum) VALUES ($1, $2)",
                        path.name, checksum)
                applied.append(path.name)
                logger.info("migration_applied", migration=path.name)
            await ensure_audit_partitions(conn, settings)
        finally:
            await conn.execute("SELECT pg_advisory_unlock(hashtext($1))", f"dps.migrate.{settings.pg_schema}")
    finally:
        if connection is None:
            await conn.close()
    return applied


async def verify_migrations(settings: Settings | None = None) -> None:
    """Raise :class:`MigrationDrift` naming every pending or changed file (runtime role, no DDL)."""
    settings = settings or active_settings()
    pool = await init_pool(settings)
    async with pool.acquire() as conn:
        ledger = await _ledger(conn, schema_ident(settings))
    problems = _drift(ledger, include_pending=True)
    if problems:
        raise MigrationDrift("migrations_mode=verify: " + "; ".join(problems))


def _add_months(d: date, n: int) -> date:
    m0 = d.month - 1 + n
    return date(d.year + m0 // 12, m0 % 12 + 1, 1)


async def ensure_audit_partitions(conn: asyncpg.Connection, settings: Settings,
                                  months_ahead: int = 3) -> None:
    """Create ``dps_audit_log_YYYY_MM`` for this month (UTC) and ``months_ahead`` more (owner only)."""
    schema = schema_ident(settings)
    if await conn.fetchval("SELECT to_regclass($1)", f"{schema}.dps_audit_log") is None:
        return
    first = datetime.now(UTC).date().replace(day=1)
    for i in range(months_ahead + 1):
        month = _add_months(first, i)
        nxt = _add_months(month, 1)
        # Bounds are computed here, never user input; DDL takes no bind parameters.
        await conn.execute(
            f"CREATE TABLE IF NOT EXISTS {schema}.\"dps_audit_log_{month:%Y_%m}\" "
            f"PARTITION OF {schema}.dps_audit_log FOR VALUES FROM ('{month.isoformat()}') "
            f"TO ('{nxt.isoformat()}')")


async def drop_expired_audit_partitions(conn: asyncpg.Connection, settings: Settings) -> list[str]:
    """Drop audit partitions whose whole month is older than ``audit_retention_months`` (owner only)."""
    schema = schema_ident(settings)
    cutoff = _add_months(datetime.now(UTC).date().replace(day=1), -settings.audit_retention_months)
    rows = await conn.fetch(
        "SELECT c.relname FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid "
        "WHERE i.inhparent = to_regclass($1)", f"{schema}.dps_audit_log")
    dropped: list[str] = []
    for name in sorted(r["relname"] for r in rows):
        match = _AUDIT_PART_RE.fullmatch(name)
        if match and date(int(match[1]), int(match[2]), 1) < cutoff:
            await conn.execute(f'DROP TABLE {schema}."{name}"')
            dropped.append(name)
    return dropped
```

- [ ] **Step 6: Write `dps/migrate.py`**

```python
"""``python -m dps.migrate [--verify] [--maintain] [--months-ahead N]`` (contract §2.1).

Exit codes: 0 ok · 1 drift/pending (verify) or a failed migration · 2 no database configured.
Prints file names and counts only.
"""
from __future__ import annotations

import argparse
import asyncio
import sys

from dps import db
from dps.config import get_settings
from dps.observability import configure_logging


async def _run(args: argparse.Namespace) -> int:
    settings = get_settings()
    try:
        if args.verify:
            await db.verify_migrations(settings)
            print("migrations: verified")
            return 0
        conn = await db.open_migration_connection(settings)
        try:
            applied = await db.run_migrations(settings, connection=conn)
            print(f"migrations: applied {len(applied)} ({', '.join(applied) or 'none pending'})")
            if args.maintain:
                await db.ensure_audit_partitions(conn, settings, months_ahead=args.months_ahead)
                dropped = await db.drop_expired_audit_partitions(conn, settings)
                print(f"audit partitions: ensured {args.months_ahead} months ahead, dropped {len(dropped)}")
        finally:
            await conn.close()
        return 0
    except db.MigrationDrift as exc:
        print(str(exc), file=sys.stderr)
        return 1
    finally:
        await db.close_pools()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m dps.migrate")
    parser.add_argument("--verify", action="store_true", help="check only; never runs DDL")
    parser.add_argument("--maintain", action="store_true", help="audit partitions ahead + drop expired")
    parser.add_argument("--months-ahead", type=int, default=3)
    args = parser.parse_args(argv)
    settings = get_settings()
    configure_logging(settings.log_level, json_output=settings.env != "local")
    if not db.db_configured(settings):
        print("DPS_PG_HOST is not set: no metadata database configured", file=sys.stderr)
        return 2
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 7: Run tests, lint, types**

Run: `pytest -q tests/db/test_migrations.py && ruff check dps tests tools && mypy dps`
Expected: `8 passed`, clean. If `test_concurrent_runners_apply_each_file_exactly_once` deadlocks, check the lock is taken **before** `CREATE SCHEMA` (two concurrent `CREATE SCHEMA IF NOT EXISTS` race on `pg_namespace`).

- [ ] **Step 8: Commit**

```bash
git add dps/migrations dps/db.py dps/migrate.py dps/observability.py tests/fixtures/db.py tests/db/test_migrations.py
git commit -m "feat(db): metadata schema 001-009 with checksummed advisory-locked migration runner" -- dps/migrations dps/db.py dps/migrate.py dps/observability.py tests/fixtures/db.py tests/db/test_migrations.py
```

---

### Task 5: RLS posture and tenant-isolation tests

**Files:**
- Modify: `dps/db.py` (add `evaluate_rls_posture`, `assert_rls_posture`)
- Test: `tests/test_rls_posture_unit.py`, `tests/db/test_rls.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_rls_posture_unit.py` (offline):
```python
from __future__ import annotations

from dps.db import ALLOWED_EXTRA_POLICIES, TENANT_TABLES, evaluate_rls_posture

GOOD_POLICIES = {t: ["tenant_isolation"] + ([ALLOWED_EXTRA_POLICIES[t]] if t in ALLOWED_EXTRA_POLICIES else [])
                 for t in TENANT_TABLES}


def _eval(**kw):  # noqa: ANN003, ANN202
    base = dict(rls_enabled=True, rolsuper=False, rolbypassrls=False, policies_by_table=GOOD_POLICIES,
                predicate_ok={t: True for t in TENANT_TABLES},
                forced={t: True for t in TENANT_TABLES}, partition_leaks=[])
    base.update(kw)
    return evaluate_rls_posture(**base)


def test_sound_posture_has_no_problems() -> None:
    assert _eval() == []


def test_each_violation_is_named() -> None:
    assert any("superuser" in p for p in _eval(rolsuper=True))
    assert any("BYPASSRLS" in p for p in _eval(rolbypassrls=True))
    assert any("RLS_ENABLED" in p for p in _eval(rls_enabled=False))
    missing = dict(GOOD_POLICIES, dps_connection=[])
    assert any("dps_connection: missing tenant_isolation" in p for p in _eval(policies_by_table=missing))
    extra = dict(GOOD_POLICIES, dps_snapshot=["tenant_isolation", "open_door"])
    assert any("dps_snapshot: unexpected" in p for p in _eval(policies_by_table=extra))
    assert any("not FORCE" in p for p in _eval(forced={**{t: True for t in TENANT_TABLES}, "dps_job": False}))
    assert any("app.tenant_id" in p for p in _eval(predicate_ok={**{t: True for t in TENANT_TABLES}, "dps_job": False}))
    assert any("dps_audit_log_2026_10" in p for p in _eval(partition_leaks=["dps.dps_audit_log_2026_10"]))
```

`tests/db/test_rls.py`:
```python
from __future__ import annotations

import asyncpg
import pytest

from dps import db

pytestmark = pytest.mark.db

# Minimal valid row per tenant table, keyed by tenant (dps_audit_log and dps_job_partition need parents).
async def _seed(conn: asyncpg.Connection, tenant: str) -> None:
    await conn.execute("INSERT INTO dps.dps_connection (id, tenant_id, name, engine, params) "
                       "VALUES ($1, $2, 'c1', 'postgres', '{}')", f"c_{tenant}", tenant)
    await conn.execute("INSERT INTO dps.dps_job_group (id, tenant_id, name, kid, seed_fingerprint) "
                       "VALUES ($1, $2, 'g', 'k_1', 'f')", f"grp_{tenant}", tenant)
    await conn.execute("INSERT INTO dps.dps_job (id, tenant_id, kind, phase, spec) "
                       "VALUES ($1, $2, 'run', 'prepare', '{}')", f"j_{tenant}", tenant)
    await conn.execute("INSERT INTO dps.dps_job_partition (job_id, tenant_id, partition_no) VALUES ($1, $2, 0)",
                       f"j_{tenant}", tenant)
    await conn.execute("INSERT INTO dps.dps_snapshot (id, tenant_id, connection_id, engine, object_ref, policy_name,"
                       " policy_hash, shape, columns, sample_rows, content_hash) "
                       "VALUES ($1, $2, 'c', 'postgres', 'bank.x', 'p', 'h', '{}', '[]', 0, 'h')", f"snap_{tenant}", tenant)
    await conn.execute("INSERT INTO dps.dps_artefact (id, tenant_id, job_id, kind, store, object_key, content_type,"
                       " bytes, sha256) VALUES ($1, $2, $3, 'manifest', 'local', 'k', 'application/json', 1, 'h')",
                       f"art_{tenant}", tenant, f"j_{tenant}")
    await conn.execute("INSERT INTO dps.dps_evidence (id, tenant_id, job_id, pack, pack_sha256) "
                       "VALUES ($1, $2, $3, '{}', 'h')", f"ev_{tenant}", tenant, f"j_{tenant}")
    await conn.execute("INSERT INTO dps.dps_audit_log (tenant_id, route, action, outcome) "
                       "VALUES ($1, '/api/v1/jobs', 'job.create', 'ok')", tenant)


async def test_posture_is_clean_for_the_api_role(migrated_db) -> None:  # noqa: ANN001
    assert await db.assert_rls_posture(migrated_db) == []


@pytest.mark.parametrize("table", db.TENANT_TABLES)
async def test_each_tenant_table_is_isolated(migrated_db, table: str) -> None:  # noqa: ANN001
    for tenant in ("t1", "t2"):
        async with db.acquire(tenant) as conn:
            await _seed(conn, tenant)
    async with db.acquire("t2") as conn:
        assert await conn.fetchval(f"SELECT count(*) FROM dps.{table} WHERE tenant_id = 't1'") == 0
        assert await conn.fetchval(f"SELECT count(*) FROM dps.{table}") >= 1
        if table not in ("dps_audit_log",):
            assert db.rowcount(await conn.execute(f"DELETE FROM dps.{table} WHERE tenant_id = 't1'")) == 0
    async with db.acquire(None) as conn:
        assert await conn.fetchval(f"SELECT count(*) FROM dps.{table}") == 0      # unbound GUC sees nothing


async def test_with_check_blocks_writing_for_another_tenant(migrated_db) -> None:  # noqa: ANN001
    async with db.acquire("t2") as conn:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.execute("INSERT INTO dps.dps_connection (id, tenant_id, name, engine, params) "
                               "VALUES ('c_x', 't1', 'x', 'postgres', '{}')")


async def test_worker_role_sees_queue_tables_across_tenants_only(migrated_db) -> None:  # noqa: ANN001
    for tenant in ("t1", "t2"):
        async with db.acquire(tenant) as conn:
            await _seed(conn, tenant)
    async with db.acquire_queue(None) as conn:
        assert await conn.fetchval("SELECT count(*) FROM dps.dps_job") == 2
        assert await conn.fetchval("SELECT count(*) FROM dps.dps_job_partition") == 2
        assert await conn.fetchval("SELECT count(*) FROM dps.dps_connection") == 0
    async with db.acquire(None) as conn:                                          # dps_app is not a worker
        assert await conn.fetchval("SELECT count(*) FROM dps.dps_job") == 0


async def test_audit_log_and_evidence_are_append_only(migrated_db) -> None:  # noqa: ANN001
    async with db.acquire("t1") as conn:
        await _seed(conn, "t1")
        with pytest.raises(asyncpg.RaiseError, match="append-only"):
            await conn.execute("UPDATE dps.dps_audit_log SET outcome = 'x'")
        with pytest.raises(asyncpg.RaiseError, match="append-only"):
            await conn.execute("DELETE FROM dps.dps_audit_log")
        with pytest.raises(asyncpg.RaiseError, match="append-only"):
            await conn.execute("UPDATE dps.dps_evidence SET pack = '{\"x\":1}'")
        assert db.rowcount(await conn.execute("UPDATE dps.dps_evidence SET html_artefact_id = 'art_1'")) == 1


async def test_no_direct_privilege_on_audit_partitions(migrated_db) -> None:  # noqa: ANN001
    async with db.acquire(None) as conn:
        parts = [r["relname"] for r in await conn.fetch(
            "SELECT c.relname FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid "
            "WHERE i.inhparent = 'dps.dps_audit_log'::regclass")]
        assert parts
        for p in parts:
            assert await conn.fetchval("SELECT has_table_privilege(current_user, $1, 'SELECT')", f"dps.{p}") is False
```

- [ ] **Step 2: Run to confirm failure**

Run: `pytest -q tests/test_rls_posture_unit.py tests/db/test_rls.py`
Expected: FAIL — `evaluate_rls_posture` / `assert_rls_posture` are not defined.

- [ ] **Step 3: Add the posture functions to `dps/db.py`**

```python
def evaluate_rls_posture(*, rls_enabled: bool, rolsuper: bool, rolbypassrls: bool,
                         policies_by_table: dict[str, list[str]], predicate_ok: dict[str, bool],
                         forced: dict[str, bool], partition_leaks: list[str]) -> list[str]:
    """Pure: observed RLS facts → violations (empty = sound)."""
    problems: list[str] = []
    if not rls_enabled:
        problems.append("DPS_RLS_ENABLED is false")
    if rolsuper:
        problems.append("runtime role is a superuser: RLS is bypassed")
    if rolbypassrls:
        problems.append("runtime role has BYPASSRLS: RLS is bypassed")
    for table in TENANT_TABLES:
        names = policies_by_table.get(table, [])
        if "tenant_isolation" not in names:
            problems.append(f"{table}: missing tenant_isolation policy")
        elif not predicate_ok.get(table, False):
            problems.append(f"{table}: tenant_isolation does not reference app.tenant_id")
        if not forced.get(table, False):
            problems.append(f"{table}: row level security is not FORCE-enabled")
        extra = sorted(n for n in names if n not in ("tenant_isolation", ALLOWED_EXTRA_POLICIES.get(table)))
        if extra:
            problems.append(f"{table}: unexpected policies {extra}")
    if partition_leaks:
        problems.append("runtime role has direct privileges on audit partitions: " + ", ".join(sorted(partition_leaks)))
    return problems


async def assert_rls_posture(settings: Settings | None = None) -> list[str]:
    """Observe the runtime role, policies, FORCE flags and partition grants; evaluate them."""
    settings = settings or active_settings()
    schema = settings.pg_schema
    pool = await init_pool(settings)
    async with pool.acquire() as conn:
        role = await conn.fetchrow("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
        rows = await conn.fetch("SELECT tablename, policyname, qual FROM pg_policies WHERE schemaname = $1", schema)
        flags = await conn.fetch(
            "SELECT c.relname, c.relforcerowsecurity AND c.relrowsecurity AS forced FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = $1 AND c.relname = ANY($2::text[])",
            schema, list(TENANT_TABLES))
        parts = await conn.fetch(
            "SELECT c.relname FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid "
            "WHERE i.inhparent = to_regclass($1)", f'"{schema}".dps_audit_log')
        leaks = [f"{schema}.{p['relname']}" for p in parts
                 if await conn.fetchval("SELECT has_table_privilege(current_user, $1, 'SELECT')",
                                        f'"{schema}"."{p["relname"]}"')]
    policies: dict[str, list[str]] = {}
    predicate_ok: dict[str, bool] = {}
    for r in rows:
        policies.setdefault(r["tablename"], []).append(r["policyname"])
        if r["policyname"] == "tenant_isolation":
            predicate_ok[r["tablename"]] = TENANT_GUC in (r["qual"] or "")
    return evaluate_rls_posture(
        rls_enabled=settings.rls_enabled,
        rolsuper=bool(role["rolsuper"]) if role else True, rolbypassrls=bool(role["rolbypassrls"]) if role else True,
        policies_by_table=policies, predicate_ok=predicate_ok,
        forced={r["relname"]: bool(r["forced"]) for r in flags}, partition_leaks=leaks)
```

- [ ] **Step 4: Run tests, lint, types**

Run: `pytest -q tests/test_rls_posture_unit.py tests/db/test_rls.py && ruff check dps tests tools && mypy dps`
Expected: `2 + 13 = 15 passed` (`test_each_tenant_table_is_isolated` ×8), clean.

- [ ] **Step 5: Commit**

```bash
git add dps/db.py tests/test_rls_posture_unit.py tests/db/test_rls.py
git commit -m "feat(db): RLS posture evaluation and tenant-isolation tests for every tenant table" -- dps/db.py tests/test_rls_posture_unit.py tests/db/test_rls.py
```

---

### Task 6: Connector base types and codecs (`dps/layers/connectors/base.py`)

**Files:**
- Create: `dps/layers/connectors/__init__.py` (docstring only), `dps/layers/connectors/base.py`
- Test: `tests/connectors/__init__.py`, `tests/connectors/test_base.py`

This task defines the data types of contract §4.1 plus the write-side models that `jobs_models.py` re-exports (`WriteMode`, `PkGenerate`, `PkOffset`, `PkToken`, `PkStrategy`, `FilesSpec` live here so `base.py` never imports `jobs_models.py` at runtime). The `Connector`/`BatchWriter` protocols and `ResolvedTarget` are appended in Task 7, once `Selection` exists.

Public interface:
```python
Engine = Literal["postgres", "oracle", "mongo"]
ConnEngine = Literal["postgres", "oracle", "mongo", "s3", "pvc"]
WriteMode = Literal["in_place", "append", "table", "files"]
ColumnKind = Literal["int", "decimal", "float", "text", "char", "date", "timestamp", "bool", "binary",
                     "json", "lob_text", "lob_binary", "uuid", "object_id", "other"]
JsonScalar = str | int | float | bool | None
Row = dict[str, Any]
EncodedKey = list[dict[str, str]]          # [{"t": "int|dec|str|char|date|ts|bytes|uuid|oid", "v": "<text>"}]
NEVER_TRANSFORMED_KINDS: frozenset[str]    # binary, lob_binary, object_id
KEYABLE_KINDS: frozenset[str]              # int, decimal, text, char, date, timestamp, uuid, binary, object_id
class ObjectRef(BaseModel):                # frozen; owner/name validated with ^[A-Za-z_][A-Za-z0-9_$#]{0,127}$
    owner: str | None = None; name: str
    def fqn(self) -> str
    @classmethod
    def parse(cls, text: str) -> ObjectRef
class ColumnInfo(BaseModel): ...           # contract §4.1, verbatim
class TableShape(BaseModel): ...           # contract §4.1 + column(name) / key_columns() helpers
class KeyRange(BaseModel): lower: EncodedKey | None = None; upper: EncodedKey | None = None
class PkGenerate / PkOffset / PkToken; PkStrategy (discriminated on "kind"); class FilesSpec  # contract §5.3
class IncompatibleValue(ValueError): code: Literal["incompatible_method", "length_overflow"]
class UnsupportedKey(ValueError)
class ConnectorError(Exception): code: str; retryable: bool
class ConnectionTestResult(BaseModel)      # contract §4.2
class PrepareReport(BaseModel): created: bool = False; truncated: bool = False; ledger: bool = False
                                existing_rows: int | None = None; checks: list[dict[str, Any]] = []
                                append_upper: int | None = None   # (added) same-table append: max key + 1
class LedgerMark(BaseModel): job_id: str; partition_no: int; batch_no: int; last_key: EncodedKey; rows: int = 0
def encode_key(values: tuple[Any, ...], columns: list[ColumnInfo]) -> EncodedKey
def decode_key(encoded: EncodedKey, columns: list[ColumnInfo]) -> tuple[Any, ...]
def key_tuple(row: Row, shape: TableShape) -> tuple[Any, ...]
def key_fingerprint(encoded: EncodedKey | None) -> str | None      # 8 hex chars, for the console
def to_engine_text(value: Any, column: ColumnInfo) -> str | None
def from_engine_text(text: str | None, column: ColumnInfo) -> Any
```
(`LedgerMark.rows` — cumulative rows the partition has written, so a resume can reconcile the partition's counters with the ledger — and `key_fingerprint` are additions.)

- [ ] **Step 1: Write the failing tests** `tests/connectors/test_base.py`

```python
from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from dps.layers.connectors.base import (
    ColumnInfo, IncompatibleValue, ObjectRef, PkOffset, TableShape, UnsupportedKey, decode_key, encode_key,
    from_engine_text, key_fingerprint, key_tuple, to_engine_text,
)


def col(name: str, kind: str, **kw) -> ColumnInfo:  # noqa: ANN003
    return ColumnInfo(path=f"$.{name}", name=name, data_type=kw.pop("data_type", kind), kind=kind, **kw)


def test_object_ref_parse_and_fqn() -> None:
    assert ObjectRef.parse("bank.customers") == ObjectRef(owner="bank", name="customers")
    assert ObjectRef.parse("profiles").fqn() == "profiles"
    for bad in ("bank.cust omers", 'a"b', "a.b.c", ""):
        with pytest.raises(ValueError):
            ObjectRef.parse(bad)


@pytest.mark.parametrize("kind,value,text", [
    ("int", 12345678, "12345678"), ("decimal", Decimal("1E+2"), "100"), ("decimal", Decimal("12.50"), "12.50"),
    ("char", "AB12      ", "AB12"), ("date", date(1988, 3, 14), "1988-03-14"),
    ("timestamp", datetime(2025, 1, 2, 3, 4, 5, 123456, tzinfo=UTC), "2025-01-02T03:04:05.123456+00:00"),
    ("bool", True, "true"), ("uuid", uuid.UUID(int=1), "00000000-0000-0000-0000-000000000001"),
    ("text", "Ananya Krishnan", "Ananya Krishnan"), ("text", None, None),
])
def test_value_codec_round_trip(kind: str, value: object, text: str | None) -> None:
    c = col("c", kind, length=10 if kind == "char" else None, precision=12 if kind == "decimal" else None,
            scale=2 if kind == "decimal" else None)
    assert to_engine_text(value, c) == text
    expected = value.ljust(10) if kind == "char" and isinstance(value, str) else value
    assert from_engine_text(text, c) == expected        # Decimal compares numerically: 100.00 == 1E+2


def test_incompatible_and_overflow_errors_carry_no_value() -> None:
    with pytest.raises(IncompatibleValue) as err:
        from_engine_text("k7fq2wxb3n", col("n", "int"))
    assert err.value.code == "incompatible_method" and "k7fq" not in str(err.value)
    with pytest.raises(IncompatibleValue) as err2:
        from_engine_text("x" * 13, col("s", "text", length=12))
    assert err2.value.code == "length_overflow" and "xxxx" not in str(err2.value)
    with pytest.raises(IncompatibleValue):
        from_engine_text("123456.78", col("d", "decimal", precision=6, scale=2))
    with pytest.raises(IncompatibleValue):
        from_engine_text("abc", col("b", "binary"))


def test_key_codec_round_trip_and_padding() -> None:
    cols = [col("id", "int"), col("code", "char", length=6), col("at", "timestamp")]
    values = (90000001, "AB    ", datetime(2025, 5, 6, 7, 8, 9, 1, tzinfo=UTC))
    enc = encode_key(values, cols)
    assert enc[0] == {"t": "int", "v": "90000001"} and enc[1] == {"t": "char", "v": "AB    "}
    assert decode_key(enc, cols) == values
    with pytest.raises(UnsupportedKey):
        encode_key((1.5,), [col("f", "float")])


def test_key_tuple_and_fingerprint() -> None:
    shape = TableShape(object=ObjectRef(owner="bank", name="t"), columns=[col("a", "int"), col("b", "text")],
                       key=["a", "b"], key_source="primary_key")
    assert key_tuple({"b": "x", "a": 1, "z": 0}, shape) == (1, "x")
    fp = key_fingerprint(encode_key((10000001,), [col("a", "int")]))
    assert fp is not None and len(fp) == 8 and "10000001" not in fp and key_fingerprint(None) is None


def test_pk_offset_exactly_one() -> None:
    assert PkOffset(offset=90_000_000).offset == 90_000_000 and PkOffset(prefix="X-").prefix == "X-"
    for bad in ({}, {"offset": 5, "prefix": "X"}, {"offset": 0}):
        with pytest.raises(ValueError):
            PkOffset(**bad)
```

- [ ] **Step 2: Run to confirm failure**

Run: `pytest -q tests/connectors/test_base.py`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write `dps/layers/connectors/base.py`** (types and codecs)

```python
"""Connector types, key codec and value codec (contract §4.1).

Keys and values of source rows are customer data. Encoded keys are stored only in partition rows
and the target's write ledger; nothing here logs, and every error message names a kind or a code,
never a value.
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Engine = Literal["postgres", "oracle", "mongo"]
ConnEngine = Literal["postgres", "oracle", "mongo", "s3", "pvc"]
WriteMode = Literal["in_place", "append", "table", "files"]
ColumnKind = Literal["int", "decimal", "float", "text", "char", "date", "timestamp", "bool", "binary",
                     "json", "lob_text", "lob_binary", "uuid", "object_id", "other"]
JsonScalar = str | int | float | bool | None
Row = dict[str, Any]
EncodedKey = list[dict[str, str]]

NEVER_TRANSFORMED_KINDS = frozenset({"binary", "lob_binary", "object_id"})
KEYABLE_KINDS = frozenset({"int", "decimal", "text", "char", "date", "timestamp", "uuid", "binary", "object_id"})
_KEY_TAG = {"int": "int", "decimal": "dec", "text": "str", "char": "char", "date": "date",
            "timestamp": "ts", "uuid": "uuid", "binary": "bytes", "object_id": "oid"}
_REF_PART = re.compile(r"^[A-Za-z_][A-Za-z0-9_$#]{0,127}$")
_INT = re.compile(r"-?\d+")
_DEC = re.compile(r"-?\d+(\.\d+)?")


class ObjectRef(BaseModel):
    """A table or collection: ``owner`` is the PG schema / Oracle owner, ``None`` for Mongo."""

    model_config = ConfigDict(frozen=True)
    owner: str | None = None
    name: str

    @model_validator(mode="after")
    def _parts(self) -> ObjectRef:
        for part in (self.owner, self.name):
            if part is not None and not _REF_PART.fullmatch(part):
                raise ValueError("object names must match ^[A-Za-z_][A-Za-z0-9_$#]{0,127}$")
        return self

    def fqn(self) -> str:
        """``OWNER.NAME`` / ``schema.name`` / ``name`` — the ``object_ref`` text."""
        return f"{self.owner}.{self.name}" if self.owner else self.name

    @classmethod
    def parse(cls, text: str) -> ObjectRef:
        """Inverse of :meth:`fqn`."""
        parts = text.split(".")
        if len(parts) == 1:
            return cls(name=parts[0])
        if len(parts) == 2:
            return cls(owner=parts[0], name=parts[1])
        raise ValueError("object reference must be NAME or OWNER.NAME")


class ColumnInfo(BaseModel):
    path: str
    name: str
    data_type: str
    kind: ColumnKind
    length: int | None = None
    precision: int | None = None
    scale: int | None = None
    ts_precision: int | None = None
    nullable: bool = True
    is_key: bool = False
    has_default: bool = False
    is_identity: bool = False


class TableShape(BaseModel):
    object: ObjectRef
    columns: list[ColumnInfo]
    key: list[str]
    key_source: Literal["primary_key", "unique_index", "rowid", "none"]
    row_estimate: int | None = None

    def column(self, name: str) -> ColumnInfo:
        """The column called ``name`` (``KeyError`` when absent)."""
        for c in self.columns:
            if c.name == name:
                return c
        raise KeyError(name)

    def key_columns(self) -> list[ColumnInfo]:
        """Key columns in key order."""
        return [self.column(k) for k in self.key]


class KeyRange(BaseModel):
    lower: EncodedKey | None = None    # inclusive; None = open
    upper: EncodedKey | None = None    # exclusive; None = open


class PkGenerate(BaseModel):
    kind: Literal["generate"] = "generate"
    sequence: str | None = None        # Oracle sequence name (M3)


class PkOffset(BaseModel):
    kind: Literal["offset"] = "offset"
    offset: int | None = Field(None, gt=0)
    prefix: str | None = Field(None, min_length=1, max_length=16, pattern=r"^[A-Za-z0-9_-]+$")

    @model_validator(mode="after")
    def _exactly_one(self) -> PkOffset:
        if (self.offset is None) == (self.prefix is None):
            raise ValueError("pk_strategy offset needs exactly one of offset or prefix")
        return self


class PkToken(BaseModel):
    kind: Literal["token"] = "token"


PkStrategy = Annotated[PkGenerate | PkOffset | PkToken, Field(discriminator="kind")]


class FilesSpec(BaseModel):
    format: Literal["csv", "parquet", "jsonl"] = "parquet"
    prefix: str = Field("", max_length=200, pattern=r"^[A-Za-z0-9/_.-]*$")
    compression: Literal["none", "gzip", "snappy", "zstd"] = "snappy"
    rows_per_file: int = Field(1_000_000, ge=1000)


class IncompatibleValue(ValueError):
    """A transformed value cannot be stored in its column (never carries the value)."""

    def __init__(self, kind: str, code: Literal["incompatible_method", "length_overflow"] = "incompatible_method") -> None:
        super().__init__(f"{code}: output does not fit a {kind} column")
        self.code = code


class UnsupportedKey(ValueError):
    """A key column kind that keyset paging cannot handle."""


class ConnectorError(Exception):
    """A source/target failure with a stable code (``source_error``, ``target_error``, …)."""

    def __init__(self, code: str, detail: str = "", *, retryable: bool = False) -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code, self.retryable = code, retryable


class ConnectionTestResult(BaseModel):
    ok: bool
    engine: Literal["postgres", "oracle", "mongo", "s3", "pvc"]
    server_version: str | None = None
    checks: list[dict[str, Any]] = Field(default_factory=list)   # {name, ok, severity, detail?} value-free
    capabilities: dict[str, bool] = Field(default_factory=dict)
    error_code: str | None = None


class PrepareReport(BaseModel):
    created: bool = False
    truncated: bool = False
    ledger: bool = False
    existing_rows: int | None = None
    checks: list[dict[str, Any]] = Field(default_factory=list)
    append_upper: int | None = None    # same-table append: exclusive upper bound = max(key) + 1 at prepare


class LedgerMark(BaseModel):
    job_id: str
    partition_no: int
    batch_no: int
    last_key: EncodedKey
    rows: int = 0


def encode_key(values: tuple[Any, ...], columns: list[ColumnInfo]) -> EncodedKey:
    """Tagged, lossless text form of a key (ts keeps full precision, char keeps padding)."""
    out: EncodedKey = []
    for value, column in zip(values, columns, strict=True):
        tag = _KEY_TAG.get(column.kind)
        if tag is None:
            raise UnsupportedKey(f"key columns of kind {column.kind} cannot be paged")
        if isinstance(value, bytes):
            text = value.hex()
        elif isinstance(value, Decimal):
            text = format(value, "f")
        elif isinstance(value, (date, datetime)):
            text = value.isoformat()
        else:
            text = str(value)
        out.append({"t": tag, "v": text})
    return out


def decode_key(encoded: EncodedKey, columns: list[ColumnInfo]) -> tuple[Any, ...]:
    """Inverse of :func:`encode_key`: driver values for bind parameters."""
    values: list[Any] = []
    for item, column in zip(encoded, columns, strict=True):
        tag, text = item["t"], item["v"]
        if tag == "int":
            values.append(int(text))
        elif tag == "dec":
            values.append(Decimal(text))
        elif tag in ("str", "char", "oid"):
            values.append(text)
        elif tag == "date":
            values.append(date.fromisoformat(text))
        elif tag == "ts":
            # Oracle TIMESTAMP(7-9) keys travel as text (contract §4.2); PG keys are microseconds.
            values.append(text if (column.ts_precision or 6) > 6 else datetime.fromisoformat(text))
        elif tag == "uuid":
            values.append(uuid.UUID(text))
        elif tag == "bytes":
            values.append(bytes.fromhex(text))
        else:
            raise UnsupportedKey(f"unknown key tag {tag}")
    return tuple(values)


def key_tuple(row: Row, shape: TableShape) -> tuple[Any, ...]:
    """The key values of ``row`` in key order."""
    return tuple(row[k] for k in shape.key)


def key_fingerprint(encoded: EncodedKey | None) -> str | None:
    """A short, non-reversible handle for a checkpoint (shown in the console instead of the key)."""
    if encoded is None:
        return None
    return hashlib.sha256(json.dumps(encoded, sort_keys=True).encode("utf-8")).hexdigest()[:8]


def to_engine_text(value: Any, column: ColumnInfo) -> str | None:
    """Canonical text the engine sees (contract §4.1)."""
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).hex()
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True, ensure_ascii=False)
    if column.kind == "char":
        return str(value).rstrip(" ")
    return str(value)


def _check_length(text: str, column: ColumnInfo) -> None:
    if column.length is not None and len(text) > column.length:
        raise IncompatibleValue(column.kind, "length_overflow")


def from_engine_text(text: str | None, column: ColumnInfo) -> Any:
    """Driver value for ``text`` in ``column``; :class:`IncompatibleValue` when it cannot fit."""
    if text is None:
        return None
    kind = column.kind
    if kind in NEVER_TRANSFORMED_KINDS:
        raise IncompatibleValue(kind)
    if kind == "int":
        if not _INT.fullmatch(text):
            raise IncompatibleValue(kind)
        return int(text)
    if kind == "decimal":
        if not _DEC.fullmatch(text):
            raise IncompatibleValue(kind)
        try:
            number = Decimal(text)
        except InvalidOperation:
            raise IncompatibleValue(kind) from None
        if column.precision is not None:
            scale = column.scale or 0
            whole = len(text.lstrip("-").split(".")[0].lstrip("0"))
            if whole > column.precision - scale:
                raise IncompatibleValue(kind, "length_overflow")
            number = number.quantize(Decimal(1).scaleb(-scale)) if scale else number.to_integral_value()
        return number
    if kind == "float":
        try:
            return float(text)
        except ValueError:
            raise IncompatibleValue(kind) from None
    if kind == "bool":
        if text.lower() not in ("true", "false"):
            raise IncompatibleValue(kind)
        return text.lower() == "true"
    if kind == "date":
        try:
            return date.fromisoformat(text)
        except ValueError:
            raise IncompatibleValue(kind) from None
    if kind == "timestamp":
        try:
            return datetime.fromisoformat(text)
        except ValueError:
            raise IncompatibleValue(kind) from None
    if kind == "uuid":
        try:
            return uuid.UUID(text)
        except ValueError:
            raise IncompatibleValue(kind) from None
    if kind == "json":
        try:
            json.loads(text)
        except ValueError:
            raise IncompatibleValue(kind) from None
        return text
    if kind == "char":
        _check_length(text, column)
        return text.ljust(column.length) if column.length else text
    _check_length(text, column)
    return text
```

- [ ] **Step 4: Run tests, lint, types**

Run: `pytest -q tests/connectors/test_base.py && ruff check dps tests tools && mypy dps`
Expected: `15 passed` (`test_value_codec_round_trip` ×10 + 5 others), clean.

- [ ] **Step 5: Commit**

```bash
git add dps/layers/connectors/__init__.py dps/layers/connectors/base.py tests/connectors/__init__.py tests/connectors/test_base.py
git commit -m "feat(connectors): object/column/shape types, tagged key codec and engine value codec" -- dps/layers/connectors/__init__.py dps/layers/connectors/base.py tests/connectors/__init__.py tests/connectors/test_base.py
```

---

### Task 7: Job, connection and snapshot models (`dps/jobs_models.py`) and the connector protocols

**Files:**
- Create: `dps/jobs_models.py`
- Modify: `dps/layers/connectors/base.py` (append `ResolvedTarget`, `Connector`, `BatchWriter`)
- Test: `tests/test_jobs_models.py`

Public interface (contract §3.3, §5.1–§5.4 verbatim, with the additions marked):
```python
# re-exported from base: WriteMode, PkGenerate, PkOffset, PkToken, PkStrategy, FilesSpec, ObjectRef
OPTION_KEYS = frozenset({"sslmode", "sslrootcert_ref", "connect_timeout", "authSource", "replicaSet",
                         "directConnection", "tls"})
class ConnectionParams(BaseModel)          # §5.1
class InlineCredentials(BaseModel)         # username, password: SecretStr, extra: dict[str, SecretStr]
class ConnectionSpec(BaseModel)            # §5.1 + rules below
class SampleSpec(BaseModel); class Selection(BaseModel)          # §5.2
class TargetSpec(BaseModel); class SourceSpec(BaseModel); class ConsistencySpec(BaseModel)
class Purpose(BaseModel); class RunOptions(BaseModel); class RunSpec(BaseModel)
class DiscoverSpec(BaseModel)              # + mode: Literal["obfuscate","synthesize"] = "obfuscate" (added; hint only)
class RetentionSpec(BaseModel)
class JobSecret(BaseModel); class SealedSelection(BaseModel)
class JobProgress(BaseModel)               # §3.3 + stage: str = "" + throughput_mark: list[Any] | None = None (added)
class ColumnClassification(BaseModel)      # §5.4 + synthesize_suggested / synthesize_method: MethodSpec | None = None,
                                           #   type_note: str | None = None (added)
class SnapshotOverride(BaseModel)          # §5.4 + synthesize_method: MethodSpec | None = None (added)
class ClassificationSnapshot(BaseModel)    # §5.4
class SpecError(Exception): code: str; status: int        # value-free detail
def redact_spec(spec: RunSpec | DiscoverSpec, *, engine: Engine) -> dict[str, Any]   # Task 12 adds the body
```
`DiscoverSpec.mode` is only a hint for the console; every snapshot stores both the obfuscate method (`suggested`/`method`) and the synthesize method (`synthesize_suggested`/`synthesize_method`) per column, so the run's mode can be chosen after review (wizard step 5). See "Open contract questions".

Appended to `base.py`:
```python
class ResolvedTarget(BaseModel):
    job_id: str
    write_mode: WriteMode
    connection_id: str
    engine: ConnEngine
    object: ObjectRef | None = None            # None for files
    same_as_source: bool = False
    create_if_missing: bool = True
    table_load: Literal["truncate_insert", "upsert"] = "truncate_insert"
    pk_strategy: PkStrategy | None = None
    files: FilesSpec | None = None
    ledger_owner: str | None = None
    key: list[str] = []                         # key column names (source = target names)
    write_columns: list[str] = []               # in_place: SET list; append/table/files: written columns
    generated_key: bool = False                 # append+generate: key columns omitted from INSERT
    target_shape: TableShape | None = None      # existing table, or the table prepare will create
    column_types: dict[str, str] = {}           # DDL type per column for create_if_missing

class Connector(Protocol):                      # contract §4.2 + key_at_offset (added)
    engine: Engine
    async def open(self) -> None
    async def close(self) -> None
    async def test(self, *, role: Literal["source", "target"], obj: ObjectRef | None = None,
                   write_mode: WriteMode | None = None) -> ConnectionTestResult
    async def list_objects(self, owner: str | None = None, *, limit: int = 500) -> list[ObjectRef]
    async def describe(self, obj: ObjectRef) -> TableShape
    async def count(self, shape: TableShape, selection: Selection, params: dict[str, JsonScalar]) -> int
    async def sample(self, shape: TableShape, selection: Selection, params: dict[str, JsonScalar], n: int) -> list[Row]
    async def sample_keys(self, shape: TableShape, selection: Selection, params: dict[str, JsonScalar],
                          n: int) -> list[tuple[Any, ...]]
    async def key_at_offset(self, shape: TableShape, selection: Selection, params: dict[str, JsonScalar],
                            offset: int) -> tuple[Any, ...] | None
    def read_partition(self, shape: TableShape, selection: Selection, params: dict[str, JsonScalar],
                       key_range: KeyRange, *, after_key: tuple[Any, ...] | None,
                       batch: int) -> AsyncIterator[list[Row]]
    async def writer(self, target: ResolvedTarget, source_shape: TableShape) -> BatchWriter

class BatchWriter(Protocol):                    # contract §4.2 + clear_ledger (added)
    async def prepare(self, *, dry_run: bool) -> PrepareReport
    async def precheck_collisions(self, keys: AsyncIterator[list[tuple[Any, ...]]]) -> int
    async def read_ledger(self, job_id: str, partition_no: int) -> LedgerMark | None
    async def write_batch(self, rows: list[Row], *, mark: LedgerMark) -> int
    async def finish_partition(self, job_id: str, partition_no: int) -> dict[str, Any]   # files: part metadata
    async def clear_ledger(self, job_id: str) -> int
    async def close(self) -> None
```
(`finish_partition` returns the partition's file metadata for `files` targets and `{}` otherwise; the run handler stores it in `dps_job_partition.meta`.) `base.py` imports `Selection` only under `TYPE_CHECKING`.

- [ ] **Step 1: Write the failing tests** `tests/test_jobs_models.py`

```python
from __future__ import annotations

import pytest
from pydantic import ValidationError

from dps.jobs_models import (
    ClassificationSnapshot, ColumnClassification, ConnectionSpec, DiscoverSpec, JobProgress, JobSecret,
    ObjectRef, PkOffset, Purpose, RunSpec, Selection, SnapshotOverride, SpecError, TargetSpec,
)

PG = {"host": "source-pg", "port": 5432, "database": "bank"}


def test_connection_spec_rules() -> None:
    ok = ConnectionSpec(name="bank-pg-ro", engine="postgres", params=PG, secret_ref="dps/src/bank-pg-ro")
    assert ok.roles == {"source"} and ok.tags == []
    inline = ConnectionSpec(name="dev-pg", engine="postgres", params=PG,
                            credentials={"username": "u", "password": "p"})
    assert inline.credentials is not None and "p" not in repr(inline)
    bad = [
        dict(name="x", engine="postgres", params=PG),                                        # no secret
        dict(name="x", engine="postgres", params=PG, secret_ref="a", credentials={"username": "u", "password": "p"}),
        dict(name="Bad Name", engine="postgres", params=PG, secret_ref="a"),
        dict(name="x", engine="postgres", params=PG, secret_ref="../etc"),
        dict(name="x", engine="postgres", params={"database": "bank"}, secret_ref="a"),      # no host
        dict(name="x", engine="s3", params={}, secret_ref="a", roles={"target"}),             # no bucket
        dict(name="x", engine="pvc", params={"base_path": "x"}, secret_ref="a", roles={"target"}),
        dict(name="x", engine="s3", params={"bucket": "b"}, secret_ref="a", roles={"source"}),
        dict(name="x", engine="postgres", params={**PG, "options": {"evil": "1"}}, secret_ref="a"),
        dict(name="x", engine="postgres", params=PG, credentials={"username": "u", "password": "p"},
             tags=["production"]),
    ]
    for kw in bad:
        with pytest.raises(ValidationError):
            ConnectionSpec(**kw)


def test_selection_exclusivity_and_param_names() -> None:
    assert Selection(where="created_at >= :since", params={"since": "2025-01-01"}).params["since"] == "2025-01-01"
    for kw in (dict(where="a = 1", query="SELECT 1"), dict(where="a = 1", mongo_filter={"a": 1}),
               dict(params={"Bad-Name": 1}), dict(columns=[]), dict(max_rows=0)):
        with pytest.raises(ValidationError):
            Selection(**kw)


def test_target_and_pk_strategy_parsing() -> None:
    t = TargetSpec.model_validate({"write_mode": "append", "pk_strategy": {"kind": "offset", "offset": 90000000},
                                   "confirm_write": True})
    assert isinstance(t.pk_strategy, PkOffset) and t.table_load == "truncate_insert" and t.create_if_missing
    with pytest.raises(ValidationError):
        TargetSpec.model_validate({"write_mode": "append", "pk_strategy": {"kind": "random"}})


def test_run_and_discover_specs() -> None:
    run = RunSpec.model_validate({
        "mode": "obfuscate", "snapshot_id": "snap_1",
        "source": {"connection_id": "c_1", "object": {"owner": "bank", "name": "customers"}},
        "target": {"write_mode": "table", "connection_id": "c_2", "object": {"owner": "bank_uat", "name": "customers"}},
        "purpose": {"ticket": "CHG-48213", "legal_basis": "legitimate interest — UAT provisioning"}})
    assert run.source.parallelism == 4 and run.options.verify == "strict" and run.consistency.scope == "key"
    with pytest.raises(ValidationError):
        Purpose(ticket="ab", legal_basis="x")
    with pytest.raises(ValidationError):   # selection only with a single object
        DiscoverSpec(connection_id="c_1", objects=[ObjectRef(name="a"), ObjectRef(name="b")],
                     selection=Selection(where="a = 1"))
    assert DiscoverSpec(connection_id="c_1", objects=[ObjectRef(name="a")]).mode == "obfuscate"


def test_progress_secret_and_snapshot_models_round_trip() -> None:
    p = JobProgress(rows_read=10, throughput=[("2026-10-11T10:00:00Z", 1200.0)])
    assert JobProgress.model_validate_json(p.model_dump_json()) == p and p.outcome == ""
    assert JobSecret().key_material is None
    col = ColumnClassification(path="$.email", name="email", data_type="varchar(254)", kind="text", is_key=False,
                               entity_type="EMAIL", confidence=0.99, sensitivity="MEDIUM", detectors=["structural"],
                               value_hit_rate=0.98, null_rate=0.0, distinct_ratio=1.0, shapes=["xxxx@xxxx.xx"],
                               text_column=False, anchor=False, suggested={"method": "email"},
                               method={"method": "email"})
    snap = ClassificationSnapshot(id="snap_1", tenant_id="t", job_id=None, connection_id="c", engine="postgres",
                                  object_ref="bank.customers", status="proposed", policy_name="p", policy_hash="h",
                                  shape={"object": {"owner": "bank", "name": "customers"}, "columns": [],
                                         "key": [], "key_source": "none"},
                                  columns=[col], sample_rows=10, overrides=[], content_hash="h",
                                  created_at="2026-10-11T00:00:00Z", accepted_at=None, accepted_by=None)
    assert ClassificationSnapshot.model_validate_json(snap.model_dump_json()).columns[0].method.method == "email"
    assert SnapshotOverride(path="$.email", method={"method": "det_token"}).method.method == "det_token"


def test_spec_error_carries_code_and_status() -> None:
    err = SpecError("confirm_write_required", 422, "in_place and append need confirm_write: true")
    assert (err.code, err.status) == ("confirm_write_required", 422) and "confirm_write" in str(err)
```

- [ ] **Step 2: Run to confirm failure**

Run: `pytest -q tests/test_jobs_models.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'dps.jobs_models'`.

- [ ] **Step 3: Write `dps/jobs_models.py`**

```python
"""Pydantic specs for connections, discovery, runs and retention (contract §3.3, §5)."""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator

from dps.engine.policy import MethodSpec
from dps.engine.taxonomy import Sensitivity
from dps.layers.connectors.base import (  # noqa: F401 - re-exported (contract §1)
    Engine, FilesSpec, JsonScalar, ObjectRef, PkGenerate, PkOffset, PkStrategy, PkToken, TableShape, WriteMode,
)

OPTION_KEYS = frozenset({"sslmode", "sslrootcert_ref", "connect_timeout", "authSource", "replicaSet",
                         "directConnection", "tls"})
_TAG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
_PARAM_RE = re.compile(r"^[a-z_][a-z0-9_]{0,30}$")


class SpecError(Exception):
    """A spec the service will not run; ``code`` and ``status`` map to ``{"detail", "code"}``."""

    def __init__(self, code: str, status: int, detail: str) -> None:
        super().__init__(detail)
        self.code, self.status, self.detail = code, status, detail


class ConnectionParams(BaseModel):
    host: str | None = Field(None, max_length=253)
    port: int | None = Field(None, ge=1, le=65535)
    database: str | None = Field(None, max_length=128)
    service_name: str | None = Field(None, max_length=128)
    options: dict[str, str] = Field(default_factory=dict)
    bucket: str | None = Field(None, pattern=r"^[a-z0-9][a-z0-9.-]{1,62}$")
    prefix: str = Field("", max_length=200, pattern=r"^[A-Za-z0-9/_.-]*$")
    region: str | None = None
    endpoint_url: str | None = None
    base_path: str | None = Field(None, pattern=r"^[A-Za-z0-9_][A-Za-z0-9/_.-]{0,200}$")

    @field_validator("options")
    @classmethod
    def _allowed_options(cls, value: dict[str, str]) -> dict[str, str]:
        unknown = set(value) - OPTION_KEYS
        if unknown:
            raise ValueError(f"unsupported connection options: {sorted(unknown)}")
        return value


class InlineCredentials(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: SecretStr
    extra: dict[str, SecretStr] = Field(default_factory=dict)


class ConnectionSpec(BaseModel):
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,62}$")
    engine: Literal["postgres", "oracle", "mongo", "s3", "pvc"]
    params: ConnectionParams
    secret_ref: str | None = Field(None, pattern=r"^[a-z0-9][a-z0-9/_.-]{0,200}$")
    credentials: InlineCredentials | None = None
    roles: set[Literal["source", "target"]] = Field(default_factory=lambda: {"source"})
    tags: list[str] = Field(default_factory=list, max_length=16)

    @model_validator(mode="after")
    def _rules(self) -> ConnectionSpec:
        if self.secret_ref is not None and ".." in self.secret_ref.split("/"):
            raise ValueError("secret_ref must not contain '..'")
        for tag in self.tags:
            if not _TAG_RE.fullmatch(tag):
                raise ValueError("tags must match ^[a-z0-9][a-z0-9-]{0,31}$")
        if self.engine == "pvc":
            if self.secret_ref or self.credentials:
                raise ValueError("pvc connections take no credentials")
            if not self.params.base_path:
                raise ValueError("pvc connections need params.base_path")
        elif (self.secret_ref is None) == (self.credentials is None):
            raise ValueError("exactly one of secret_ref or credentials is required")
        if self.engine in ("s3", "pvc") and self.roles != {"target"}:
            raise ValueError("file stores are target-only connections")
        if self.engine == "s3" and not self.params.bucket:
            raise ValueError("s3 connections need params.bucket")
        if self.engine in ("postgres", "oracle") and not self.params.host:
            raise ValueError("database connections need params.host")
        if self.engine == "postgres" and not self.params.database:
            raise ValueError("postgres connections need params.database")
        if "production" in self.tags and self.credentials is not None:
            raise ValueError("production-tagged connections must use secret_ref")
        return self


class SampleSpec(BaseModel):
    percent: float = Field(gt=0, le=100)
    seed: int | None = None


class Selection(BaseModel):
    where: str | None = Field(None, max_length=8000)
    params: dict[str, JsonScalar] = Field(default_factory=dict)
    query: str | None = Field(None, max_length=20000)
    columns: list[str] | None = Field(None, min_length=1, max_length=2000)
    max_rows: int | None = Field(None, ge=1)
    sample: SampleSpec | None = None
    mongo_filter: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _exclusive(self) -> Selection:
        if self.where is not None and self.query is not None:
            raise ValueError("where and query are mutually exclusive")
        if self.mongo_filter is not None and (self.where is not None or self.query is not None):
            raise ValueError("mongo_filter excludes where and query")
        for name in self.params:
            if not _PARAM_RE.fullmatch(name):
                raise ValueError("bind names must match ^[a-z_][a-z0-9_]{0,30}$")
        return self


class TargetSpec(BaseModel):
    write_mode: WriteMode
    connection_id: str | None = None
    object: ObjectRef | None = None
    create_if_missing: bool = True
    table_load: Literal["truncate_insert", "upsert"] = "truncate_insert"
    pk_strategy: PkStrategy | None = None
    files: FilesSpec | None = None
    ledger_owner: str | None = Field(None, pattern=r"^[A-Za-z_][A-Za-z0-9_$#]{0,62}$")
    confirm_write: bool = False
    expected_rows: int | None = Field(None, ge=0)


class SourceSpec(BaseModel):
    connection_id: str
    object: ObjectRef
    selection: Selection = Field(default_factory=Selection)
    parallelism: int = Field(4, ge=1, le=64)
    batch_rows: int | None = Field(None, ge=10, le=50_000)
    partition_rows: int | None = Field(None, ge=1_000)


class ConsistencySpec(BaseModel):
    scope: Literal["request", "key", "entity"] = "key"
    field_scoped: bool = False
    key_material: dict[str, Any] | None = None
    synthesis_seed: dict[str, Any] | None = None
    wrap_key: dict[str, str] | None = None


class Purpose(BaseModel):
    ticket: str = Field(min_length=3, max_length=64)
    legal_basis: str = Field(min_length=3, max_length=256)


class RunOptions(BaseModel):
    dry_run: bool = False
    verify: Literal["strict", "warn"] = "strict"
    unclassified: Literal["detect", "keep", "redact"] = "detect"
    tabular: Literal["none", "copula", "mostly"] = "none"


class RunSpec(BaseModel):
    mode: Literal["obfuscate", "synthesize"]
    source: SourceSpec
    target: TargetSpec
    snapshot_id: str
    policy: str | dict[str, Any] | None = None
    consistency: ConsistencySpec = Field(default_factory=ConsistencySpec)
    group_id: str | None = None
    purpose: Purpose
    options: RunOptions = Field(default_factory=RunOptions)


class DiscoverSpec(BaseModel):
    connection_id: str
    objects: list[ObjectRef] = Field(min_length=1, max_length=50)
    selection: Selection = Field(default_factory=Selection)
    sample_rows: int | None = Field(None, ge=50, le=50_000)
    policy: str | dict[str, Any] | None = None
    mode: Literal["obfuscate", "synthesize"] = "obfuscate"

    @model_validator(mode="after")
    def _selection_single_object(self) -> DiscoverSpec:
        if self.selection != Selection() and len(self.objects) != 1:
            raise ValueError("a selection is allowed only when discovering one object")
        return self


class RetentionSpec(BaseModel):
    dry_run: bool = False


class JobSecret(BaseModel):
    key_material: dict[str, Any] | None = None
    synthesis_seed: dict[str, Any] | None = None
    scan_auth: dict[str, Any] | None = None
    tabular_model: str | None = None


class SealedSelection(BaseModel):
    where: str | None = None
    query: str | None = None
    params: dict[str, JsonScalar] = Field(default_factory=dict)
    mongo_filter: dict[str, Any] | None = None


class JobProgress(BaseModel):
    outcome: Literal["", "succeeded", "failed", "canceled", "dry_run"] = ""
    stage: str = ""
    rows_estimated: int | None = None
    rows_read: int = 0
    rows_written: int = 0
    rows_skipped: int = 0
    partitions_total: int = 0
    partitions_done: int = 0
    partitions_failed: int = 0
    rows_per_second: float | None = None
    throughput: list[tuple[str, float]] = Field(default_factory=list)
    throughput_mark: list[Any] | None = None          # [iso ts, rows_written] of the last sample
    values_by_method: dict[str, int] = Field(default_factory=dict)
    verification: dict[str, int] = Field(default_factory=dict)
    snapshot_ids: list[str] = Field(default_factory=list)
    evidence_id: str | None = None
    pages: int | None = None
    fields: int | None = None
    blocked_requests: int | None = None


class ColumnClassification(BaseModel):
    path: str
    name: str
    data_type: str
    kind: str
    is_key: bool
    entity_type: str | None
    confidence: float
    sensitivity: Sensitivity | None
    detectors: list[str]
    value_hit_rate: float
    null_rate: float
    distinct_ratio: float
    shapes: list[str]
    text_column: bool
    anchor: bool
    suggested: MethodSpec
    method: MethodSpec
    override: bool = False
    synthesize_suggested: MethodSpec | None = None
    synthesize_method: MethodSpec | None = None
    type_note: str | None = None


class SnapshotOverride(BaseModel):
    path: str
    entity_type: str | None = None
    method: MethodSpec | None = None
    synthesize_method: MethodSpec | None = None
    text_column: bool | None = None
    anchor: bool | None = None


class ClassificationSnapshot(BaseModel):
    id: str
    tenant_id: str
    job_id: str | None
    connection_id: str
    engine: str
    object_ref: str
    status: Literal["proposed", "accepted", "superseded"]
    policy_name: str
    policy_hash: str
    shape: TableShape
    columns: list[ColumnClassification]
    sample_rows: int
    overrides: list[SnapshotOverride]
    content_hash: str
    created_at: datetime
    accepted_at: datetime | None
    accepted_by: str | None


def redact_spec(spec: RunSpec | DiscoverSpec, *, engine: Engine) -> dict[str, Any]:
    """Value-free copy of ``spec`` for ``dps_job.spec`` — implemented in Task 12."""
    raise NotImplementedError
```

- [ ] **Step 4: Append the protocols and `ResolvedTarget` to `base.py`** exactly as in the interface block above (with `from collections.abc import AsyncIterator`, `from typing import TYPE_CHECKING, Protocol` and `if TYPE_CHECKING: from dps.jobs_models import Selection`). The protocols carry docstrings stating the contract §4.2 rules (read-only sources, keyset order, one transaction per `write_batch` including the ledger mark).

- [ ] **Step 5: Run tests, lint, types**

Run: `pytest -q tests/test_jobs_models.py && ruff check dps tests tools && mypy dps`
Expected: `6 passed`, clean.

- [ ] **Step 6: Commit**

```bash
git add dps/jobs_models.py dps/layers/connectors/base.py tests/test_jobs_models.py
git commit -m "feat(jobs): connection, selection, target, run and snapshot specs; connector protocols" -- dps/jobs_models.py dps/layers/connectors/base.py tests/test_jobs_models.py
```

---

### Task 8: Queue, part 1 — job rows, claims, leases, workers registry (`dps/jobs.py`)

**Files:**
- Create: `dps/jobs.py`
- Modify: `dps/observability.py` (safe fields, metrics), `tests/fixtures/db.py` (`queue_settings` fixture)
- Test: `tests/db/test_jobs_queue.py`

The design mirrors `di/jobs.py` (read it before starting): FIFO by `(priority, run_after, created_at, id)`, a cap-aware candidate CTE feeding an inner `FOR UPDATE SKIP LOCKED` with a state re-check, leases renewed at `lease/4`, and every worker-side write **fenced** on `locked_by = $worker AND state = 'running'` — a zombie whose lease was reaped writes zero rows and must stop. Claims, heartbeats, reaping and stats run on the worker pool with no tenant GUC (the `worker_claim` policy grants cross-tenant visibility of the two queue tables only). Admin operations (`cancel`, `pause`, `resume`, `retry`) run on the API pool under the tenant GUC.

Public interface (contract §3.1–§3.2; additions marked):
```python
class JobKind(StrEnum)          # discover run scan retention
class JobState(StrEnum)         # queued running waiting paused succeeded failed dead canceled
class JobPhase(StrEnum)         # discover prepare partitions finalize scan retention
class PartitionState(StrEnum)   # queued running done failed dead canceled
TERMINAL_JOB_STATES: frozenset[JobState]; ACTIVE_JOB_STATES: tuple[JobState, ...]
SYSTEM_TENANT = "_system"; NOTIFY_CHANNEL = "dps_job_new"
class QueueFull(Exception)                      # code "queue_full" (429)
class KeyMaterialRequired(Exception)            # code "key_material_required" (409)
class JobStateError(Exception)                  # .code: job_terminal | job_finalizing | job_not_pausable |
                                                #        job_not_paused | job_not_retryable ; .status 409
class Job(BaseModel)                            # contract §3.2 + plan: dict[str, Any] | None = None (added)
class JobInternal(Job)                          # (added) + attempts, max_attempts, key_enc, spec_enc, locked_by,
                                                #   lease_expires_at: datetime | None — what workers load
class ClaimedJob(JobInternal)                   # lease_expires_at: datetime (contract §3.2)
class ClaimedPartition(BaseModel)               # contract §3.2 + rows_written: int, resumed: int (added)
class PartitionView(BaseModel)                  # partition_no state rows_read rows_written rows_skipped batch_no attempts
                                                # resumed checkpoint_fp started_at finished_at updated_at error_code rows_per_second
class ReapResult(BaseModel)                     # jobs_requeued jobs_dead partitions_requeued partitions_dead partitions_released workers_pruned
class JobStats(BaseModel)                       # workers_online workers_busy workers_draining partitions_waiting oldest_waiting_s
                                                # rows_per_s by_state running tenant_max_running lease_reclaims_24h
async def create_job(*, tenant_id: str, kind: JobKind, phase: JobPhase, spec: dict[str, Any], mode: str | None = None,
                     sealed_secret: bytes | None = None, sealed_selection: bytes | None = None, kid: str | None = None,
                     group_id: str | None = None, connection_id: str | None = None,
                     target_connection_id: str | None = None, object_ref: str | None = None,
                     snapshot_id: str | None = None, purpose: dict[str, str] | None = None,
                     created_by: str | None = None, idempotency_key: str | None = None, priority: int = 100,
                     job_id: str | None = None) -> Job     # job_id (added): callers seal with the id first
async def get_job(tenant_id: str, job_id: str) -> Job | None
async def find_by_idempotency(tenant_id: str, key: str) -> Job | None
async def list_jobs(tenant_id: str, *, kind: JobKind | None = None, state: JobState | None = None,
                    group_id: str | None = None, limit: int = 50, cursor: str | None = None) -> tuple[list[Job], str | None]
async def load_job_internal(job_id: str) -> JobInternal | None                       # (added) worker pool
async def claim_jobs(worker_id: str, *, kinds: tuple[JobKind, ...], batch: int) -> list[ClaimedJob]
async def heartbeat(worker_id: str, *, job_ids: list[str], partition_ids: list[int]) -> tuple[list[str], list[int]]
async def advance_job(worker_id: str, job_id: str, *, phase: JobPhase, state: JobState,
                      progress: dict[str, Any] | None = None, error_code: str | None = None, error: str | None = None,
                      purge_secret: bool = False, plan: dict[str, Any] | None = None,
                      delay_seconds: float = 0.0) -> bool             # plan, delay_seconds (added)
async def retry_job(worker_id: str, job_id: str, *, error_code: str, error: str, backoff_base: float,
                    backoff_cap: float) -> JobState | None             # (added) queued with backoff, or dead
async def release(worker_id: str, *, job_ids: list[str], partition_ids: list[int]) -> None
async def register_worker(worker_id: str, *, role: str, version: str) -> None           # (added)
async def worker_heartbeat(worker_id: str, *, busy: int, draining: bool) -> None         # (added)
async def unregister_worker(worker_id: str) -> None                                      # (added)
async def ensure_retention_job(interval_hours: int) -> bool                              # (added)
def worker_id() -> str                                                                    # "<hostname>:<pid>:<6 hex>"
```
Task 9 adds the partition functions, `reap`, `cancel`/`pause`/`resume`/`retry`, `queue_stats`, `tenant_stats`, `sample_throughput`.

- [ ] **Step 1: Observability additions** — in `dps/observability.py` extend `_SAFE_FIELDS` with
`"job_id", "partition_id", "partition_no", "phase", "state", "kind", "worker_id", "rows", "batch_no", "attempts", "engine", "write_mode", "connection_id", "snapshot_id", "evidence_id", "group_id", "partitions", "elapsed_s", "rows_per_s", "object_ref", "code", "checks", "lease_lost"` (ids, counts and metadata names only; `object_ref` is a table name), and add the metrics:
```python
JOBS_CLAIMED = Counter("dps_jobs_claimed_total", "Job rows claimed", ["kind", "phase"])
PARTITIONS_FINISHED = Counter("dps_partitions_finished_total", "Partitions reaching a state", ["state"])
LEASES_LOST = Counter("dps_leases_lost_total", "Fenced writes that found the lease gone", ["what"])
ROWS_WRITTEN = Counter("dps_rows_written_total", "Rows written by L2 jobs", ["mode", "write_mode"])
QUEUE_DEPTH = Gauge("dps_queue_depth", "Queue rows by table and state", ["table", "state"])
AUDIT_DROPPED = Counter("dps_audit_dropped_total", "Audit events dropped on a saturated queue")
```
(`from prometheus_client import Gauge`.)

- [ ] **Step 2: Add the `queue_settings` fixture** to `tests/fixtures/db.py`

```python
@pytest.fixture
def queue_settings(migrated_db: Settings, monkeypatch: pytest.MonkeyPatch) -> Callable[..., Settings]:
    """Override queue timings for one test: ``queue_settings(job_lease_seconds=1)``."""
    def apply(**overrides: object) -> Settings:
        updated = db.active_settings().model_copy(update=overrides)
        monkeypatch.setattr(db, "_settings", updated)
        return updated
    return apply
```

- [ ] **Step 3: Write the failing tests** `tests/db/test_jobs_queue.py` (part 1; Task 9 appends)

```python
from __future__ import annotations

import asyncio

import pytest

from dps import db, jobs
from dps.jobs import JobKind, JobPhase, JobState

pytestmark = pytest.mark.db


async def _job(tenant: str = "t1", kind: JobKind = JobKind.run, phase: JobPhase = JobPhase.prepare, **kw):  # noqa: ANN003, ANN202
    return await jobs.create_job(tenant_id=tenant, kind=kind, phase=phase, spec={"source": {"parallelism": 2}}, **kw)


async def test_create_get_list_with_cursor_and_filters(migrated_db) -> None:  # noqa: ANN001
    made = [await _job() for _ in range(3)] + [await _job(kind=JobKind.discover, phase=JobPhase.discover)]
    page1, cursor = await jobs.list_jobs("t1", limit=2)
    page2, cursor2 = await jobs.list_jobs("t1", limit=2, cursor=cursor)
    page3, _ = await jobs.list_jobs("t1", limit=2, cursor=cursor2)
    assert [j.id for j in page1 + page2 + page3] == [j.id for j in reversed(made)]
    assert len((await jobs.list_jobs("t1", kind=JobKind.discover))[0]) == 1
    assert (await jobs.get_job("t1", made[0].id)).state == JobState.queued
    assert await jobs.get_job("t2", made[0].id) is None                         # RLS
    with pytest.raises(ValueError):
        await jobs.list_jobs("t1", cursor="not-a-cursor")


async def test_idempotency_key_returns_the_first_job(migrated_db) -> None:  # noqa: ANN001
    first = await _job(idempotency_key="req-1")
    again = await _job(idempotency_key="req-1")
    assert again.id == first.id


async def test_queue_full_per_tenant(migrated_db) -> None:  # noqa: ANN001
    async with db.acquire_queue(None) as conn:
        await conn.execute("INSERT INTO dps.dps_tenant (tenant_id, max_queued) VALUES ('t1', 2)")
    await _job(); await _job()
    with pytest.raises(jobs.QueueFull):
        await _job()
    await _job("t2")


async def test_claim_order_kinds_and_lease(migrated_db) -> None:  # noqa: ANN001
    low = await _job(priority=200)
    high = await _job(priority=10)
    disc = await _job(kind=JobKind.discover, phase=JobPhase.discover)
    claimed = await jobs.claim_jobs("w1", kinds=(JobKind.run,), batch=5)
    assert [c.id for c in claimed] == [high.id, low.id]
    assert all(c.state == JobState.running and c.locked_by == "w1" and c.attempts == 1 for c in claimed)
    assert [c.id for c in await jobs.claim_jobs("w2", kinds=(JobKind.discover,), batch=5)] == [disc.id]


async def test_concurrent_claimers_never_double_claim(migrated_db) -> None:  # noqa: ANN001
    async with db.acquire_queue(None) as conn:
        await conn.execute("INSERT INTO dps.dps_tenant (tenant_id, max_running) VALUES ('t1', 100)")
    ids = {(await _job()).id for _ in range(40)}

    async def claimer(name: str) -> list[str]:
        got: list[str] = []
        while batch := await jobs.claim_jobs(name, kinds=(JobKind.run,), batch=3):
            got += [c.id for c in batch]
        return got

    results = await asyncio.gather(*(claimer(f"w{i}") for i in range(8)))
    flat = [i for r in results for i in r]
    assert len(flat) == len(set(flat)) == 40 and set(flat) == ids


async def test_tenant_running_cap(migrated_db) -> None:  # noqa: ANN001
    async with db.acquire_queue(None) as conn:
        await conn.execute("INSERT INTO dps.dps_tenant (tenant_id, max_running) VALUES ('t1', 2)")
    for _ in range(5):
        await _job()
    other = await _job("t2")
    claimed = await jobs.claim_jobs("w1", kinds=(JobKind.run,), batch=10)
    assert sum(c.tenant_id == "t1" for c in claimed) == 2 and other.id in {c.id for c in claimed}
    assert await jobs.claim_jobs("w2", kinds=(JobKind.run,), batch=10) == []


async def test_fencing_after_lease_expiry(migrated_db, queue_settings) -> None:  # noqa: ANN001
    queue_settings(job_lease_seconds=1)
    job = await _job()
    [claimed] = await jobs.claim_jobs("w1", kinds=(JobKind.run,), batch=1)
    assert (await jobs.heartbeat("w1", job_ids=[job.id], partition_ids=[]))[0] == [job.id]
    await asyncio.sleep(1.3)
    await jobs.reap(backoff_base=0.0, backoff_cap=0.0)                           # Task 9; requeues
    assert await jobs.heartbeat("w1", job_ids=[job.id], partition_ids=[]) == ([], [])
    assert await jobs.advance_job("w1", job.id, phase=JobPhase.finalize, state=JobState.queued) is False
    assert claimed.attempts == 1


async def test_advance_job_terminal_purges_key_and_sets_expiry(migrated_db) -> None:  # noqa: ANN001
    job = await _job(sealed_secret=b"\x01sealed")
    await jobs.claim_jobs("w1", kinds=(JobKind.run,), batch=1)
    assert await jobs.advance_job("w1", job.id, phase=JobPhase.finalize, state=JobState.succeeded,
                                  progress={"outcome": "succeeded"}, purge_secret=True)
    internal = await jobs.load_job_internal(job.id)
    assert internal is not None and internal.key_enc is None and internal.finished_at is not None
    assert 29 <= (internal.expires_at - internal.finished_at).days <= 30 and internal.progress.outcome == "succeeded"


async def test_phase_change_resets_attempts_and_requeues(migrated_db) -> None:  # noqa: ANN001
    job = await _job()
    await jobs.claim_jobs("w1", kinds=(JobKind.run,), batch=1)
    assert await jobs.advance_job("w1", job.id, phase=JobPhase.finalize, state=JobState.queued, delay_seconds=0)
    [again] = await jobs.claim_jobs("w2", kinds=(JobKind.run,), batch=1)
    assert again.phase == JobPhase.finalize and again.attempts == 1


async def test_retry_job_backoff_then_dead(migrated_db) -> None:  # noqa: ANN001
    job = await jobs.create_job(tenant_id="t1", kind=JobKind.discover, phase=JobPhase.discover, spec={})
    for expected in (JobState.queued, JobState.queued, JobState.dead):
        async with db.acquire_queue(None) as conn:
            await conn.execute("UPDATE dps.dps_job SET run_after = now() WHERE id = $1", job.id)
        [c] = await jobs.claim_jobs("w1", kinds=(JobKind.discover,), batch=1)
        assert await jobs.retry_job("w1", c.id, error_code="source_error", error="connection refused",
                                    backoff_base=0.0, backoff_cap=0.0) == expected


async def test_release_does_not_charge_an_attempt(migrated_db) -> None:  # noqa: ANN001
    job = await _job()
    await jobs.claim_jobs("w1", kinds=(JobKind.run,), batch=1)
    await jobs.release("w1", job_ids=[job.id], partition_ids=[])
    internal = await jobs.load_job_internal(job.id)
    assert internal.state == JobState.queued and internal.attempts == 0 and internal.locked_by is None


async def test_worker_registry_and_retention_scheduling(migrated_db) -> None:  # noqa: ANN001
    await jobs.register_worker("w1", role="worker", version="0.1.0")
    await jobs.worker_heartbeat("w1", busy=2, draining=False)
    assert await jobs.ensure_retention_job(6) is True and await jobs.ensure_retention_job(6) is False
    [r] = await jobs.claim_jobs("w1", kinds=(JobKind.retention,), batch=5)
    assert r.tenant_id == jobs.SYSTEM_TENANT and r.phase == JobPhase.retention
    await jobs.unregister_worker("w1")


def test_worker_id_shape() -> None:
    host, pid, suffix = jobs.worker_id().rsplit(":", 2)
    assert host and pid.isdigit() and len(suffix) == 6
```

- [ ] **Step 4: Run to confirm failure**

Run: `pytest -q tests/db/test_jobs_queue.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'dps.jobs'`.

- [ ] **Step 5: Write `dps/jobs.py`** (part 1). `S` below stands for `db.schema_ident()`, interpolated with an f-string; every statement is schema-qualified.

```python
"""Durable Postgres job queue (contract §3) — jobs and partitions in the metadata database.

Mirrors ``di/jobs.py``. Claims are two-level (a cap-aware, lock-free candidate CTE feeding an inner
``FOR UPDATE SKIP LOCKED`` with a state re-check), leases are renewed by heartbeats, and every
worker-side write is fenced on ``locked_by`` + ``state = 'running'`` so a reaped zombie writes zero
rows. Per-tenant caps count running job rows plus running partitions and are enforced within one
claim call (cross-call soft, exactly as in Document AI Services). The public :class:`Job` never
carries ``key_enc``/``spec_enc``.
"""
from __future__ import annotations

import base64
import os
import random
import socket
import time
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

import asyncpg
from pydantic import BaseModel, Field

from dps import db
from dps.ids import new_id
from dps.jobs_models import JobProgress
from dps.layers.connectors.base import EncodedKey, KeyRange, key_fingerprint
from dps.observability import JOBS_CLAIMED, get_logger

logger = get_logger(__name__)


class JobKind(StrEnum):
    discover = "discover"
    run = "run"
    scan = "scan"
    retention = "retention"


class JobState(StrEnum):
    queued = "queued"
    running = "running"
    waiting = "waiting"
    paused = "paused"
    succeeded = "succeeded"
    failed = "failed"
    dead = "dead"
    canceled = "canceled"


class JobPhase(StrEnum):
    discover = "discover"
    prepare = "prepare"
    partitions = "partitions"
    finalize = "finalize"
    scan = "scan"
    retention = "retention"


class PartitionState(StrEnum):
    queued = "queued"
    running = "running"
    done = "done"
    failed = "failed"
    dead = "dead"
    canceled = "canceled"


TERMINAL_JOB_STATES = frozenset({JobState.succeeded, JobState.failed, JobState.dead, JobState.canceled})
ACTIVE_JOB_STATES = (JobState.queued, JobState.running, JobState.waiting, JobState.paused)
SYSTEM_TENANT = "_system"
NOTIFY_CHANNEL = "dps_job_new"
_MIN_LIMIT, _MAX_LIMIT = 1, 200

_PUBLIC = ("id, tenant_id, kind, state, phase, mode, group_id, connection_id, target_connection_id, "
           "object_ref, snapshot_id, spec, purpose, kid, progress, plan, error_code, error, created_by, "
           "created_at, updated_at, started_at, finished_at, expires_at")
_INTERNAL = _PUBLIC + ", attempts, max_attempts, key_enc, spec_enc, locked_by, lease_expires_at"


class QueueFull(Exception):
    code = "queue_full"


class KeyMaterialRequired(Exception):
    code = "key_material_required"


class JobStateError(Exception):
    status = 409

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code


class Job(BaseModel):
    id: str
    tenant_id: str
    kind: JobKind
    state: JobState
    phase: JobPhase
    mode: str | None = None
    group_id: str | None = None
    connection_id: str | None = None
    target_connection_id: str | None = None
    object_ref: str | None = None
    snapshot_id: str | None = None
    spec: dict[str, Any] = Field(default_factory=dict)
    purpose: dict[str, str] = Field(default_factory=dict)
    kid: str | None = None
    progress: JobProgress = Field(default_factory=JobProgress)
    plan: dict[str, Any] | None = None
    error_code: str | None = None
    error: str | None = None
    created_by: str | None = None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    expires_at: datetime | None = None


class JobInternal(Job):
    attempts: int
    max_attempts: int
    key_enc: bytes | None = None
    spec_enc: bytes | None = None
    locked_by: str | None = None
    lease_expires_at: datetime | None = None


class ClaimedJob(JobInternal):
    lease_expires_at: datetime


class ClaimedPartition(BaseModel):
    id: int
    job_id: str
    tenant_id: str
    partition_no: int
    lower_key: EncodedKey | None = None
    upper_key: EncodedKey | None = None
    last_key: EncodedKey | None = None
    batch_no: int
    attempts: int
    max_attempts: int
    lease_expires_at: datetime
    rows_written: int = 0
    resumed: int = 0


class PartitionView(BaseModel):
    partition_no: int
    state: PartitionState
    rows_read: int
    rows_written: int
    rows_skipped: int
    batch_no: int
    attempts: int
    resumed: int
    checkpoint_fp: str | None
    started_at: datetime | None
    finished_at: datetime | None
    updated_at: datetime
    error_code: str | None
    rows_per_second: float | None


class ReapResult(BaseModel):
    jobs_requeued: int = 0
    jobs_dead: int = 0
    partitions_requeued: int = 0
    partitions_dead: int = 0
    partitions_released: int = 0
    workers_pruned: int = 0


class JobStats(BaseModel):
    workers_online: int
    workers_busy: int
    workers_draining: int
    partitions_waiting: int
    oldest_waiting_s: float | None
    rows_per_s: float
    by_state: dict[str, int]
    running: int
    tenant_max_running: int
    lease_reclaims_24h: int


def _clamp(limit: int) -> int:
    return max(_MIN_LIMIT, min(_MAX_LIMIT, limit))


def _encode_cursor(created_at: datetime, job_id: str) -> str:
    return base64.urlsafe_b64encode(f"{created_at.isoformat()}|{job_id}".encode()).decode().rstrip("=")


def _decode_cursor(cursor: str) -> tuple[datetime, str]:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode("utf-8")
        ts, sep, job_id = raw.partition("|")
        if not sep or not job_id:
            raise ValueError
        return datetime.fromisoformat(ts), job_id
    except (ValueError, UnicodeDecodeError):
        raise ValueError("malformed cursor") from None


def _row_job(row: asyncpg.Record, model: type[Job] = Job) -> Any:
    data = dict(row)
    progress = JobProgress.model_validate(data.pop("progress") or {})
    if data.get("p_total"):
        progress.partitions_total = int(data["p_total"])
        progress.partitions_done = int(data["p_done"])
        progress.partitions_failed = int(data["p_failed"])
        progress.rows_read = int(data["rows_read"])
        progress.rows_written = int(data["rows_written"])
        progress.rows_skipped = int(data["rows_skipped"])
        progress.values_by_method = {k: int(v) for k, v in (data.get("vbm") or {}).items()}
        progress.verification = {**progress.verification, "checked": int(data["checked"])}
    for extra in ("p_total", "p_done", "p_failed", "rows_read", "rows_written", "rows_skipped", "vbm", "checked"):
        data.pop(extra, None)
    return model.model_validate({**data, "progress": progress, "spec": data.get("spec") or {},
                                 "purpose": data.get("purpose") or {}})


def _select(cols: str, where: str) -> str:
    """SELECT with the live partition aggregate merged into ``progress`` (contract §3.3)."""
    s = db.schema_ident()
    qcols = ", ".join(f"j.{c.strip()}" for c in cols.split(","))
    return (
        f"SELECT {qcols}, agg.p_total, agg.p_done, agg.p_failed, agg.rows_read, agg.rows_written, "
        f"agg.rows_skipped, agg.vbm, agg.checked FROM {s}.dps_job j LEFT JOIN LATERAL ("
        "SELECT count(*) AS p_total, count(*) FILTER (WHERE p.state = 'done') AS p_done, "
        "count(*) FILTER (WHERE p.state IN ('failed','dead')) AS p_failed, "
        "COALESCE(sum(p.rows_read), 0)::bigint AS rows_read, "
        "COALESCE(sum(p.rows_written), 0)::bigint AS rows_written, "
        "COALESCE(sum(p.rows_skipped), 0)::bigint AS rows_skipped, "
        f"{s}.dps_jsonb_sum(p.values_by_method) AS vbm, "
        "COALESCE(sum(p.verify_checked), 0)::bigint AS checked "
        f"FROM {s}.dps_job_partition p WHERE p.job_id = j.id) agg ON true WHERE {where}")


async def _touch_tenant(conn: asyncpg.Connection, tenant_id: str) -> None:
    await conn.execute(f"INSERT INTO {db.schema_ident()}.dps_tenant (tenant_id) VALUES ($1) "
                       "ON CONFLICT (tenant_id) DO NOTHING", tenant_id)


async def create_job(*, tenant_id: str, kind: JobKind, phase: JobPhase, spec: dict[str, Any],
                     mode: str | None = None, sealed_secret: bytes | None = None,
                     sealed_selection: bytes | None = None, kid: str | None = None,
                     group_id: str | None = None, connection_id: str | None = None,
                     target_connection_id: str | None = None, object_ref: str | None = None,
                     snapshot_id: str | None = None, purpose: dict[str, str] | None = None,
                     created_by: str | None = None, idempotency_key: str | None = None,
                     priority: int = 100, job_id: str | None = None) -> Job:
    """Insert a queued job (cap-checked) and notify workers; an idempotent replay returns the original."""
    settings = db.active_settings()
    s = db.schema_ident()
    job_id = job_id or new_id("s" if kind == JobKind.scan else "j")
    max_attempts = settings.job_max_attempts
    try:
        async with db.acquire(tenant_id) as conn, conn.transaction():
            await _touch_tenant(conn, tenant_id)
            cap = await conn.fetchval(f"SELECT COALESCE((SELECT max_queued FROM {s}.dps_tenant "
                                      "WHERE tenant_id = $1), $2)", tenant_id, settings.job_tenant_max_queued)
            active = await conn.fetchval(f"SELECT count(*) FROM {s}.dps_job WHERE tenant_id = $1 "
                                         "AND state = ANY($2::text[])", tenant_id, [x.value for x in ACTIVE_JOB_STATES])
            if active >= cap:
                raise QueueFull()
            await conn.execute(
                f"INSERT INTO {s}.dps_job (id, tenant_id, kind, phase, mode, group_id, connection_id, "
                "target_connection_id, object_ref, snapshot_id, spec, purpose, key_enc, spec_enc, kid, "
                "priority, max_attempts, idempotency_key, created_by) VALUES "
                "($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11::jsonb,$12::jsonb,$13,$14,$15,$16,$17,$18,$19)",
                job_id, tenant_id, kind.value, phase.value, mode, group_id, connection_id,
                target_connection_id, object_ref, snapshot_id, spec, purpose or {}, sealed_secret,
                sealed_selection, kid, priority, max_attempts, idempotency_key, created_by)
            await conn.execute("SELECT pg_notify($1, $2)", NOTIFY_CHANNEL, kind.value)
    except asyncpg.UniqueViolationError:
        if idempotency_key is None:
            raise
        existing = await find_by_idempotency(tenant_id, idempotency_key)
        if existing is None:
            raise
        return existing
    job = await get_job(tenant_id, job_id)
    assert job is not None
    return job


async def get_job(tenant_id: str, job_id: str) -> Job | None:
    async with db.acquire(tenant_id) as conn:
        row = await conn.fetchrow(_select(_PUBLIC, "j.tenant_id = $1 AND j.id = $2"), tenant_id, job_id)
    return _row_job(row) if row else None


async def find_by_idempotency(tenant_id: str, key: str) -> Job | None:
    async with db.acquire(tenant_id) as conn:
        row = await conn.fetchrow(_select(_PUBLIC, "j.tenant_id = $1 AND j.idempotency_key = $2"), tenant_id, key)
    return _row_job(row) if row else None


async def list_jobs(tenant_id: str, *, kind: JobKind | None = None, state: JobState | None = None,
                    group_id: str | None = None, limit: int = 50,
                    cursor: str | None = None) -> tuple[list[Job], str | None]:
    """Newest first; keyset pagination on ``(created_at, id)``. ``ValueError`` on a bad cursor."""
    conds, params = ["j.tenant_id = $1"], [tenant_id]
    for column, value in (("kind", kind), ("state", state), ("group_id", group_id)):
        if value is not None:
            params.append(str(value))
            conds.append(f"j.{column} = ${len(params)}")
    if cursor:
        ts, cid = _decode_cursor(cursor)
        params += [ts, cid]
        conds.append(f"(j.created_at, j.id) < (${len(params) - 1}::timestamptz, ${len(params)})")
    page = _clamp(limit)
    sql = _select(_PUBLIC, " AND ".join(conds)) + f" ORDER BY j.created_at DESC, j.id DESC LIMIT {page + 1}"
    async with db.acquire(tenant_id) as conn:
        rows = await conn.fetch(sql, *params)
    items = [_row_job(r) for r in rows[:page]]
    nxt = _encode_cursor(items[-1].created_at, items[-1].id) if len(rows) > page and items else None
    return items, nxt


async def load_job_internal(job_id: str) -> JobInternal | None:
    """Worker-side full row (sealed blobs included); never returned by the API."""
    async with db.acquire_queue(None) as conn:
        row = await conn.fetchrow(_select(_INTERNAL, "j.id = $1"), job_id)
    return _row_job(row, JobInternal) if row else None


async def claim_jobs(worker_id: str, *, kinds: tuple[JobKind, ...], batch: int) -> list[ClaimedJob]:
    """Claim up to ``batch`` due job rows across tenants (FIFO, per-tenant running cap)."""
    if batch <= 0:
        return []
    settings = db.active_settings()
    s = db.schema_ident()
    sql = f"""
WITH running AS (
    SELECT tenant_id, count(*) AS n FROM (
        SELECT tenant_id FROM {s}.dps_job WHERE state = 'running'
        UNION ALL
        SELECT tenant_id FROM {s}.dps_job_partition WHERE state = 'running') r
    GROUP BY tenant_id),
ranked AS (
    SELECT j.id, j.tenant_id, j.priority, j.run_after, j.created_at,
           row_number() OVER (PARTITION BY j.tenant_id
                              ORDER BY j.priority, j.run_after, j.created_at, j.id) AS rn
    FROM {s}.dps_job j
    WHERE j.state = 'queued' AND j.run_after <= now() AND j.kind = ANY($3::text[])),
eligible AS (
    SELECT ranked.id FROM ranked
    LEFT JOIN running rc ON rc.tenant_id = ranked.tenant_id
    LEFT JOIN {s}.dps_tenant t ON t.tenant_id = ranked.tenant_id
    WHERE ranked.rn <= GREATEST(COALESCE(t.max_running, $4) - COALESCE(rc.n, 0), 0)
    ORDER BY ranked.priority, ranked.run_after, ranked.created_at, ranked.id
    LIMIT $5)
UPDATE {s}.dps_job j
SET state = 'running', locked_by = $1, attempts = j.attempts + 1,
    lease_expires_at = now() + make_interval(secs => $2),
    started_at = COALESCE(j.started_at, now()), updated_at = now()
FROM (SELECT id FROM {s}.dps_job
      WHERE id IN (SELECT id FROM eligible) AND state = 'queued'
      FOR UPDATE SKIP LOCKED LIMIT $6) c
WHERE j.id = c.id
RETURNING j.id"""
    async with db.acquire_queue(None) as conn:
        async with conn.transaction():
            rows = await conn.fetch(sql, worker_id, float(settings.job_lease_seconds), [k.value for k in kinds],
                                    settings.job_tenant_max_running, max(batch * 4, 50), batch)
        ids = [r["id"] for r in rows]
        full = await conn.fetch(_select(_INTERNAL, "j.id = ANY($1::text[])")
                                + " ORDER BY j.priority, j.run_after, j.created_at, j.id", ids) if ids else []
    claimed = [_row_job(r, ClaimedJob) for r in full]
    for job in claimed:
        JOBS_CLAIMED.labels(job.kind.value, job.phase.value).inc()
    return claimed


async def heartbeat(worker_id: str, *, job_ids: list[str], partition_ids: list[int]) -> tuple[list[str], list[int]]:
    """Renew leases; returns what this worker still owns (anything missing was reclaimed)."""
    settings = db.active_settings()
    s = db.schema_ident()
    owned_jobs: list[str] = []
    owned_parts: list[int] = []
    async with db.acquire_queue(None) as conn:
        if job_ids:
            rows = await conn.fetch(
                f"UPDATE {s}.dps_job SET lease_expires_at = now() + make_interval(secs => $1), updated_at = now() "
                "WHERE id = ANY($2::text[]) AND locked_by = $3 AND state = 'running' RETURNING id",
                float(settings.job_lease_seconds), job_ids, worker_id)
            owned_jobs = [r["id"] for r in rows]
        if partition_ids:
            rows = await conn.fetch(
                f"UPDATE {s}.dps_job_partition SET lease_expires_at = now() + make_interval(secs => $1), "
                "updated_at = now() WHERE id = ANY($2::bigint[]) AND locked_by = $3 AND state = 'running' "
                "RETURNING id", float(settings.partition_lease_seconds), partition_ids, worker_id)
            owned_parts = [r["id"] for r in rows]
    return owned_jobs, owned_parts


async def advance_job(worker_id: str, job_id: str, *, phase: JobPhase, state: JobState,
                      progress: dict[str, Any] | None = None, error_code: str | None = None,
                      error: str | None = None, purge_secret: bool = False,
                      plan: dict[str, Any] | None = None, delay_seconds: float = 0.0) -> bool:
    """Fenced phase/state transition of a claimed job row. ``False`` = lease lost: stop now."""
    settings = db.active_settings()
    s = db.schema_ident()
    terminal = state in TERMINAL_JOB_STATES
    release_lock = state != JobState.running
    async with db.acquire_queue(None) as conn, conn.transaction():
        tag = await conn.execute(
            f"""UPDATE {s}.dps_job j SET
                phase = $3, state = $4, progress = j.progress || $5::jsonb,
                error_code = COALESCE($6, j.error_code), error = COALESCE($7, j.error),
                key_enc = CASE WHEN $8 THEN NULL ELSE j.key_enc END,
                plan = COALESCE($9::jsonb, j.plan),
                attempts = CASE WHEN j.phase <> $3 THEN 0 ELSE j.attempts END,
                locked_by = CASE WHEN $10 THEN NULL ELSE j.locked_by END,
                lease_expires_at = CASE WHEN $10 THEN NULL ELSE j.lease_expires_at END,
                run_after = CASE WHEN $4 = 'queued' THEN now() + make_interval(secs => $11) ELSE j.run_after END,
                finished_at = CASE WHEN $12 THEN now() ELSE NULL END,
                expires_at = CASE WHEN $12 THEN now() + make_interval(days => COALESCE(
                    (SELECT t.retention_days FROM {s}.dps_tenant t WHERE t.tenant_id = j.tenant_id), $13))
                    ELSE j.expires_at END,
                updated_at = now()
            WHERE j.id = $1 AND j.locked_by = $2 AND j.state = 'running'""",
            job_id, worker_id, phase.value, state.value, progress or {}, error_code, error, purge_secret,
            plan, release_lock, float(delay_seconds), terminal, settings.retention_days)
        applied = db.rowcount(tag) > 0
        if applied and state == JobState.queued:
            await conn.execute("SELECT pg_notify($1, $2)", NOTIFY_CHANNEL, phase.value)
    return applied


async def retry_job(worker_id: str, job_id: str, *, error_code: str, error: str, backoff_base: float,
                    backoff_cap: float) -> JobState | None:
    """Requeue after a transient failure (backoff + jitter), or dead-letter when attempts are spent."""
    settings = db.active_settings()
    s = db.schema_ident()
    async with db.acquire_queue(None) as conn:
        row = await conn.fetchrow(
            f"""UPDATE {s}.dps_job j SET
                state = CASE WHEN j.attempts >= j.max_attempts THEN 'dead' ELSE 'queued' END,
                error_code = $3, error = $4, locked_by = NULL, lease_expires_at = NULL,
                key_enc = CASE WHEN j.attempts >= j.max_attempts THEN NULL ELSE j.key_enc END,
                run_after = now() + least($5, $6 * 2 ^ j.attempts) * (0.5 + random()) * interval '1 second',
                finished_at = CASE WHEN j.attempts >= j.max_attempts THEN now() END,
                expires_at = CASE WHEN j.attempts >= j.max_attempts THEN now() + make_interval(days => COALESCE(
                    (SELECT t.retention_days FROM {s}.dps_tenant t WHERE t.tenant_id = j.tenant_id), $7)) END,
                updated_at = now()
            WHERE j.id = $1 AND j.locked_by = $2 AND j.state = 'running' RETURNING j.state""",
            job_id, worker_id, error_code, error[:500], float(backoff_cap), float(backoff_base),
            settings.retention_days)
    return JobState(row["state"]) if row else None


async def release(worker_id: str, *, job_ids: list[str], partition_ids: list[int]) -> None:
    """Voluntary requeue on drain/pause: no attempt is charged."""
    s = db.schema_ident()
    async with db.acquire_queue(None) as conn:
        if job_ids:
            await conn.execute(
                f"UPDATE {s}.dps_job SET state = 'queued', locked_by = NULL, lease_expires_at = NULL, "
                "run_after = now(), attempts = GREATEST(attempts - 1, 0), updated_at = now() "
                "WHERE id = ANY($1::text[]) AND locked_by = $2 AND state = 'running'", job_ids, worker_id)
        if partition_ids:
            await conn.execute(
                f"UPDATE {s}.dps_job_partition SET state = 'queued', locked_by = NULL, lease_expires_at = NULL, "
                "run_after = now(), attempts = GREATEST(attempts - 1, 0), updated_at = now() "
                "WHERE id = ANY($1::bigint[]) AND locked_by = $2 AND state = 'running'", partition_ids, worker_id)
        await conn.execute("SELECT pg_notify($1, 'released')", NOTIFY_CHANNEL)


async def register_worker(worker_id: str, *, role: str, version: str) -> None:
    async with db.acquire_queue(None) as conn:
        await conn.execute(
            f"INSERT INTO {db.schema_ident()}.dps_worker (worker_id, role, version) VALUES ($1, $2, $3) "
            "ON CONFLICT (worker_id) DO UPDATE SET last_seen = now(), draining = false", worker_id, role, version)


async def worker_heartbeat(worker_id: str, *, busy: int, draining: bool) -> None:
    async with db.acquire_queue(None) as conn:
        await conn.execute(
            f"UPDATE {db.schema_ident()}.dps_worker SET last_seen = now(), busy = $2, draining = $3 "
            "WHERE worker_id = $1", worker_id, busy, draining)


async def unregister_worker(worker_id: str) -> None:
    async with db.acquire_queue(None) as conn:
        await conn.execute(f"DELETE FROM {db.schema_ident()}.dps_worker WHERE worker_id = $1", worker_id)


async def ensure_retention_job(interval_hours: int) -> bool:
    """Insert at most one retention job per interval bucket (idempotency key); True when inserted."""
    s = db.schema_ident()
    bucket = int(time.time() // (max(1, interval_hours) * 3600))
    async with db.acquire_queue(None) as conn, conn.transaction():
        await conn.execute(f"INSERT INTO {s}.dps_tenant (tenant_id) VALUES ($1) ON CONFLICT DO NOTHING", SYSTEM_TENANT)
        tag = await conn.execute(
            f"INSERT INTO {s}.dps_job (id, tenant_id, kind, phase, spec, idempotency_key, priority) "
            "VALUES ($1, $2, 'retention', 'retention', '{}'::jsonb, $3, 200) "
            "ON CONFLICT (tenant_id, idempotency_key) WHERE idempotency_key IS NOT NULL DO NOTHING",
            new_id("j"), SYSTEM_TENANT, f"retention:{bucket}")
        inserted = db.rowcount(tag) > 0
        if inserted:
            await conn.execute("SELECT pg_notify($1, 'retention')", NOTIFY_CHANNEL)
    return inserted


def worker_id() -> str:
    """Unique per process start: ``<hostname>:<pid>:<6 hex>`` — the fencing identity."""
    return f"{socket.gethostname()}:{os.getpid()}:{random.randint(0, 0xFFFFFF):06x}"
```

- [ ] **Step 6: Run part-1 tests** (the fencing test also needs `reap` from Task 9 — run everything except it now)

Run: `pytest -q tests/db/test_jobs_queue.py -k "not fencing" && ruff check dps tests tools && mypy dps`
Expected: `12 passed, 1 deselected`, clean.

- [ ] **Step 7: Commit**

```bash
git add dps/jobs.py dps/observability.py tests/fixtures/db.py tests/db/test_jobs_queue.py
git commit -m "feat(jobs): durable job rows - capped FIFO claims, fenced leases, retries, worker registry" -- dps/jobs.py dps/observability.py tests/fixtures/db.py tests/db/test_jobs_queue.py
```

---

### Task 9: Queue, part 2 — partitions, finalize transition, reaper, controls, stats

**Files:**
- Modify: `dps/jobs.py`
- Test: `tests/db/test_jobs_queue.py` (append), `tests/db/test_partitions_queue.py`

Public interface (contract §3.1–§3.2; additions marked):
```python
async def add_partitions(worker_id: str, job_id: str, ranges: list[KeyRange], *,
                         plan: dict[str, Any] | None = None, progress: dict[str, Any] | None = None) -> int
async def claim_partitions(worker_id: str, *, batch: int) -> list[ClaimedPartition]
async def checkpoint(worker_id: str, partition_id: int, *, last_key: EncodedKey, batch_no: int, rows_read: int,
                     rows_written: int, rows_skipped: int, values_by_method: dict[str, int],
                     verify_checked: int) -> bool
async def finish_partition(worker_id: str, partition_id: int, state: PartitionState, *,
                           error_code: str | None = None, error: str | None = None,
                           meta: dict[str, Any] | None = None) -> bool               # meta (added)
async def retry_partition(worker_id: str, partition_id: int, *, error_code: str, error: str,
                          backoff_base: float, backoff_cap: float) -> PartitionState | None   # (added)
async def reset_partition_progress(worker_id: str, partition_id: int) -> bool          # (added) files restart
async def abandon_partition(worker_id: str, partition_id: int) -> None                 # (added) after cancel
async def partition_control(worker_id: str, partition_id: int) -> Literal["continue", "pause", "cancel", "lost"]  # (added)
async def list_partitions(tenant_id: str, job_id: str) -> list[PartitionView]
async def reap(*, backoff_base: float, backoff_cap: float) -> ReapResult
async def cancel(tenant_id: str, job_id: str) -> Job | None
async def pause(tenant_id: str, job_id: str) -> Job | None
async def resume(tenant_id: str, job_id: str) -> Job | None
async def retry(tenant_id: str, job_id: str, *, sealed_secret: bytes | None = None) -> Job | None
async def queue_stats() -> list[dict[str, Any]]
async def tenant_stats(tenant_id: str) -> JobStats                                      # (added) GET /jobs/stats
async def sample_throughput(*, min_interval_s: float = 10.0) -> int                     # (added) reaper loop
```

**The finalize transition, exactly once.** Every path that can make a partition terminal (`finish_partition`, `retry_partition` → dead, `reap` → dead) runs, in one transaction, `SELECT … FROM dps_job WHERE id = $job FOR UPDATE` **first**, then its partition update, then `_MAYBE_FINALIZE`. The job-row lock serialises those transactions per job, and under READ COMMITTED each statement takes a fresh snapshot, so the last finisher's `NOT EXISTS` sees every earlier finisher's committed state: exactly one transaction moves the job to `finalize/queued`. The reaper updates partitions and runs `_MAYBE_FINALIZE` in **separate** transactions (it never holds a partition lock while waiting for a job lock), so it cannot deadlock with a finisher.

- [ ] **Step 1: Write the failing tests** `tests/db/test_partitions_queue.py`

```python
from __future__ import annotations

import asyncio

import pytest

from dps import db, jobs
from dps.jobs import JobKind, JobPhase, JobState, PartitionState
from dps.layers.connectors.base import KeyRange

pytestmark = pytest.mark.db


def _ranges(n: int) -> list[KeyRange]:
    keys = [[{"t": "int", "v": str(10_000_000 + i * 1000)}] for i in range(n + 1)]
    return [KeyRange(lower=None if i == 0 else keys[i], upper=None if i == n - 1 else keys[i + 1]) for i in range(n)]


async def _prepared(n: int, parallelism: int = 8, tenant: str = "t1") -> str:
    job = await jobs.create_job(tenant_id=tenant, kind=JobKind.run, phase=JobPhase.prepare,
                                spec={"source": {"parallelism": parallelism}}, sealed_secret=b"\x01k")
    [c] = await jobs.claim_jobs("prep", kinds=(JobKind.run,), batch=1)
    assert await jobs.add_partitions("prep", c.id, _ranges(n), plan={"columns": []}) == n
    return job.id


async def test_add_partitions_is_atomic_and_fenced(migrated_db) -> None:  # noqa: ANN001
    job_id = await _prepared(4)
    job = await jobs.get_job("t1", job_id)
    assert (job.phase, job.state, job.progress.partitions_total, job.plan) == (
        JobPhase.partitions, JobState.waiting, 4, {"columns": []})
    assert await jobs.add_partitions("someone-else", job_id, _ranges(2)) == 0


async def test_claim_partitions_respects_parallelism_and_pause(migrated_db) -> None:  # noqa: ANN001
    job_id = await _prepared(6, parallelism=2)
    first = await jobs.claim_partitions("w1", batch=10)
    assert len(first) == 2 and [p.partition_no for p in first] == [0, 1] and first[0].lower_key is None
    assert await jobs.claim_partitions("w2", batch=10) == []
    await jobs.pause("t1", job_id)
    await jobs.finish_partition("w1", first[0].id, PartitionState.done)
    assert await jobs.claim_partitions("w2", batch=10) == []                      # paused: no new claims
    await jobs.resume("t1", job_id)
    assert len(await jobs.claim_partitions("w2", batch=10)) == 1


async def test_checkpoint_deltas_merge_and_fencing(migrated_db) -> None:  # noqa: ANN001
    await _prepared(1)
    [p] = await jobs.claim_partitions("w1", batch=1)
    key = [{"t": "int", "v": "10000500"}]
    for _ in range(2):
        assert await jobs.checkpoint("w1", p.id, last_key=key, batch_no=1, rows_read=10, rows_written=9,
                                     rows_skipped=1, values_by_method={"det_token": 9, "fallback:fpe_disabled": 2},
                                     verify_checked=9)
    assert not await jobs.checkpoint("w2", p.id, last_key=key, batch_no=3, rows_read=1, rows_written=1,
                                     rows_skipped=0, values_by_method={}, verify_checked=0)
    [view] = await jobs.list_partitions("t1", p.job_id)
    assert (view.rows_read, view.rows_written, view.rows_skipped) == (20, 18, 2)
    assert view.checkpoint_fp is not None and "10000500" not in view.checkpoint_fp
    job = await jobs.get_job("t1", p.job_id)
    assert job.progress.values_by_method == {"det_token": 18, "fallback:fpe_disabled": 4}


async def test_finalize_transition_happens_exactly_once(migrated_db, queue_settings) -> None:  # noqa: ANN001
    queue_settings(job_tenant_max_running=100)
    job_id = await _prepared(20, parallelism=20)
    parts = await jobs.claim_partitions("w1", batch=20)
    assert len(parts) == 20
    results = await asyncio.gather(*(jobs.finish_partition("w1", p.id, PartitionState.done) for p in parts))
    assert all(results)
    job = await jobs.get_job("t1", job_id)
    assert (job.phase, job.state) == (JobPhase.finalize, JobState.queued)
    [fin] = await jobs.claim_jobs("w9", kinds=(JobKind.run,), batch=5)
    assert fin.id == job_id and fin.phase == JobPhase.finalize


async def test_dead_partition_still_finalizes(migrated_db, queue_settings) -> None:  # noqa: ANN001
    queue_settings(partition_lease_seconds=1, partition_max_attempts=1)
    job_id = await _prepared(1)
    async with db.acquire_queue(None) as conn:
        await conn.execute("UPDATE dps.dps_job_partition SET max_attempts = 1 WHERE job_id = $1", job_id)
    await jobs.claim_partitions("w1", batch=1)
    await asyncio.sleep(1.3)
    result = await jobs.reap(backoff_base=0.0, backoff_cap=0.0)
    assert result.partitions_dead == 1
    job = await jobs.get_job("t1", job_id)
    assert job.phase == JobPhase.finalize and job.progress.partitions_failed == 1


async def test_lease_reclaim_requeues_and_counts_resume(migrated_db, queue_settings) -> None:  # noqa: ANN001
    queue_settings(partition_lease_seconds=1)
    await _prepared(1)
    [p] = await jobs.claim_partitions("w1", batch=1)
    await asyncio.sleep(1.3)
    assert (await jobs.reap(backoff_base=0.0, backoff_cap=0.0)).partitions_requeued == 1
    assert not await jobs.checkpoint("w1", p.id, last_key=[{"t": "int", "v": "1"}], batch_no=1, rows_read=1,
                                     rows_written=1, rows_skipped=0, values_by_method={}, verify_checked=0)
    [again] = await jobs.claim_partitions("w2", batch=1)
    assert again.id == p.id and again.resumed == 1 and again.attempts == 2


async def test_retry_partition_backoff_then_dead(migrated_db) -> None:  # noqa: ANN001
    job_id = await _prepared(1)
    async with db.acquire_queue(None) as conn:
        await conn.execute("UPDATE dps.dps_job_partition SET max_attempts = 2 WHERE job_id = $1", job_id)
    for expected in (PartitionState.queued, PartitionState.dead):
        [p] = await jobs.claim_partitions("w1", batch=1)
        assert await jobs.retry_partition("w1", p.id, error_code="target_error", error="timeout",
                                          backoff_base=0.0, backoff_cap=0.0) == expected
        async with db.acquire_queue(None) as conn:
            await conn.execute("UPDATE dps.dps_job_partition SET run_after = now() WHERE job_id = $1", job_id)
    assert (await jobs.get_job("t1", job_id)).phase == JobPhase.finalize


async def test_partition_control_and_cancel(migrated_db) -> None:  # noqa: ANN001
    job_id = await _prepared(3)
    [a, b] = await jobs.claim_partitions("w1", batch=2)
    assert await jobs.partition_control("w1", a.id) == "continue"
    assert await jobs.partition_control("w2", a.id) == "lost"
    await jobs.pause("t1", job_id)
    assert await jobs.partition_control("w1", a.id) == "pause"
    await jobs.resume("t1", job_id)
    canceled = await jobs.cancel("t1", job_id)
    assert canceled.phase == JobPhase.finalize and canceled.state == JobState.queued
    assert canceled.progress.outcome == "canceled"
    assert await jobs.partition_control("w1", a.id) == "cancel"
    assert not await jobs.checkpoint("w1", a.id, last_key=[{"t": "int", "v": "1"}], batch_no=1, rows_read=1,
                                     rows_written=1, rows_skipped=0, values_by_method={}, verify_checked=0)
    await jobs.abandon_partition("w1", a.id)
    states = {v.partition_no: v.state for v in await jobs.list_partitions("t1", job_id)}
    assert set(states.values()) == {PartitionState.canceled}
    with pytest.raises(jobs.JobStateError) as err:
        await jobs.cancel("t1", job_id)
    assert err.value.code == "job_finalizing"
    assert b.id != a.id


async def test_retry_requires_key_material_and_requeues(migrated_db) -> None:  # noqa: ANN001
    job_id = await _prepared(2)
    parts = await jobs.claim_partitions("w1", batch=2)
    await jobs.finish_partition("w1", parts[0].id, PartitionState.done)
    await jobs.finish_partition("w1", parts[1].id, PartitionState.failed, error_code="leakage", error="leak")
    [fin] = await jobs.claim_jobs("w1", kinds=(JobKind.run,), batch=1)
    await jobs.advance_job("w1", fin.id, phase=JobPhase.finalize, state=JobState.failed,
                           progress={"outcome": "failed"}, purge_secret=True)
    with pytest.raises(jobs.KeyMaterialRequired):
        await jobs.retry("t1", job_id)
    job = await jobs.retry("t1", job_id, sealed_secret=b"\x01new")
    assert (job.phase, job.state, job.progress.outcome) == (JobPhase.partitions, JobState.waiting, "")
    [p] = await jobs.claim_partitions("w2", batch=5)
    assert p.partition_no == 1 and p.attempts == 1


async def test_reset_progress_and_throughput_and_stats(migrated_db) -> None:  # noqa: ANN001
    job_id = await _prepared(1)
    [p] = await jobs.claim_partitions("w1", batch=1)
    await jobs.checkpoint("w1", p.id, last_key=[{"t": "int", "v": "1"}], batch_no=1, rows_read=5, rows_written=5,
                          rows_skipped=0, values_by_method={"keep": 5}, verify_checked=5)
    assert await jobs.sample_throughput(min_interval_s=0) == 1
    assert len((await jobs.get_job("t1", job_id)).progress.throughput) == 1
    assert await jobs.reset_partition_progress("w1", p.id)
    [view] = await jobs.list_partitions("t1", job_id)
    assert (view.rows_written, view.batch_no, view.checkpoint_fp) == (0, 0, None)
    await jobs.register_worker("w1", role="worker", version="t")
    stats = await jobs.tenant_stats("t1")
    assert stats.workers_online == 1 and stats.running == 1 and stats.tenant_max_running == 8
    assert {r["table"] for r in await jobs.queue_stats()} == {"dps_job", "dps_job_partition"}
```

- [ ] **Step 2: Run to confirm failure**

Run: `pytest -q tests/db/test_partitions_queue.py`
Expected: FAIL — `add_partitions` and the other functions are not defined.

- [ ] **Step 3: Add the partition functions to `dps/jobs.py`**

```python
_MAYBE_FINALIZE = """UPDATE {s}.dps_job j
SET phase = 'finalize', state = 'queued', run_after = now(), attempts = 0, updated_at = now()
WHERE j.id = $1 AND j.phase = 'partitions' AND j.state IN ('waiting', 'paused')
  AND NOT EXISTS (SELECT 1 FROM {s}.dps_job_partition p
                  WHERE p.job_id = j.id AND p.state IN ('queued', 'running'))
RETURNING j.id"""


async def _lock_job(conn: asyncpg.Connection, job_id: str) -> None:
    await conn.execute(f"SELECT 1 FROM {db.schema_ident()}.dps_job WHERE id = $1 FOR UPDATE", job_id)


async def _maybe_finalize(conn: asyncpg.Connection, job_id: str) -> bool:
    """Move a partitions-phase job to finalize when no partition is queued/running (job row locked)."""
    row = await conn.fetchrow(_MAYBE_FINALIZE.format(s=db.schema_ident()), job_id)
    if row is not None:
        await conn.execute("SELECT pg_notify($1, 'finalize')", NOTIFY_CHANNEL)
    return row is not None


async def add_partitions(worker_id: str, job_id: str, ranges: list[KeyRange], *,
                         plan: dict[str, Any] | None = None, progress: dict[str, Any] | None = None) -> int:
    """Atomically replace the job's partitions and move it to ``partitions/waiting`` (fenced).

    Returns ``len(ranges)``, or 0 when the fence failed (the caller treats that as a lost lease).
    Partitions left by an earlier, crashed prepare attempt are deleted first.
    """
    settings = db.active_settings()
    s = db.schema_ident()
    async with db.acquire_queue(None) as conn, conn.transaction():
        tenant = await conn.fetchval(f"SELECT tenant_id FROM {s}.dps_job WHERE id = $1 AND locked_by = $2 "
                                     "AND state = 'running' FOR UPDATE", job_id, worker_id)
        if tenant is None:
            return 0
        await conn.execute(f"DELETE FROM {s}.dps_job_partition WHERE job_id = $1", job_id)
        await conn.executemany(
            f"INSERT INTO {s}.dps_job_partition (job_id, tenant_id, partition_no, lower_key, upper_key, max_attempts) "
            "VALUES ($1, $2, $3, $4::jsonb, $5::jsonb, $6)",
            [(job_id, tenant, i, r.lower, r.upper, settings.partition_max_attempts) for i, r in enumerate(ranges)])
        await conn.execute(
            f"UPDATE {s}.dps_job SET phase = 'partitions', state = 'waiting', locked_by = NULL, "
            "lease_expires_at = NULL, attempts = 0, plan = COALESCE($2::jsonb, plan), "
            "progress = progress || $3::jsonb, updated_at = now() WHERE id = $1",
            job_id, plan, {**(progress or {}), "partitions_total": len(ranges)})
        await conn.execute("SELECT pg_notify($1, 'partition')", NOTIFY_CHANNEL)
    return len(ranges)


async def claim_partitions(worker_id: str, *, batch: int) -> list[ClaimedPartition]:
    """Claim due partitions of ``waiting`` run jobs: per-job ``parallelism`` and per-tenant caps."""
    if batch <= 0:
        return []
    settings = db.active_settings()
    s = db.schema_ident()
    sql = f"""
WITH running AS (
    SELECT tenant_id, count(*) AS n FROM (
        SELECT tenant_id FROM {s}.dps_job WHERE state = 'running'
        UNION ALL
        SELECT tenant_id FROM {s}.dps_job_partition WHERE state = 'running') r
    GROUP BY tenant_id),
job_running AS (
    SELECT job_id, count(*) AS n FROM {s}.dps_job_partition WHERE state = 'running' GROUP BY job_id),
ranked AS (
    SELECT p.id, p.tenant_id, p.job_id, p.partition_no, j.priority, j.created_at AS job_created,
           COALESCE((j.spec #>> '{{source,parallelism}}')::int, 4) AS par,
           row_number() OVER (PARTITION BY p.job_id ORDER BY p.run_after, p.partition_no) AS rn_job,
           row_number() OVER (PARTITION BY p.tenant_id
                              ORDER BY j.priority, j.created_at, p.job_id, p.partition_no) AS rn_tenant
    FROM {s}.dps_job_partition p JOIN {s}.dps_job j ON j.id = p.job_id
    WHERE p.state = 'queued' AND p.run_after <= now() AND j.state = 'waiting' AND j.phase = 'partitions'),
eligible AS (
    SELECT r.id FROM ranked r
    LEFT JOIN job_running jr ON jr.job_id = r.job_id
    LEFT JOIN running tr ON tr.tenant_id = r.tenant_id
    LEFT JOIN {s}.dps_tenant t ON t.tenant_id = r.tenant_id
    WHERE r.rn_job <= GREATEST(r.par - COALESCE(jr.n, 0), 0)
      AND r.rn_tenant <= GREATEST(COALESCE(t.max_running, $3) - COALESCE(tr.n, 0), 0)
    ORDER BY r.priority, r.job_created, r.job_id, r.partition_no
    LIMIT $4)
UPDATE {s}.dps_job_partition p
SET state = 'running', locked_by = $1, attempts = p.attempts + 1,
    lease_expires_at = now() + make_interval(secs => $2),
    started_at = COALESCE(p.started_at, now()), updated_at = now()
FROM (SELECT id FROM {s}.dps_job_partition
      WHERE id IN (SELECT id FROM eligible) AND state = 'queued'
      FOR UPDATE SKIP LOCKED LIMIT $5) c
WHERE p.id = c.id
RETURNING p.id, p.job_id, p.tenant_id, p.partition_no, p.lower_key, p.upper_key, p.last_key, p.batch_no,
          p.attempts, p.max_attempts, p.lease_expires_at, p.rows_written, p.resumed"""
    async with db.acquire_queue(None) as conn, conn.transaction():
        rows = await conn.fetch(sql, worker_id, float(settings.partition_lease_seconds),
                                settings.job_tenant_max_running, max(batch * 4, 50), batch)
    parts = [ClaimedPartition.model_validate(dict(r)) for r in rows]
    return sorted(parts, key=lambda p: (p.job_id, p.partition_no))


async def checkpoint(worker_id: str, partition_id: int, *, last_key: EncodedKey, batch_no: int,
                     rows_read: int, rows_written: int, rows_skipped: int,
                     values_by_method: dict[str, int], verify_checked: int) -> bool:
    """Record one committed batch (deltas) and renew the lease; fenced. ``False`` = stop now."""
    settings = db.active_settings()
    s = db.schema_ident()
    async with db.acquire_queue(None) as conn:
        tag = await conn.execute(
            f"UPDATE {s}.dps_job_partition SET last_key = $3::jsonb, batch_no = $4, "
            "rows_read = rows_read + $5, rows_written = rows_written + $6, rows_skipped = rows_skipped + $7, "
            f"values_by_method = {s}.dps_jsonb_add(values_by_method, $8::jsonb), "
            "verify_checked = verify_checked + $9, lease_expires_at = now() + make_interval(secs => $10), "
            "updated_at = now() WHERE id = $1 AND locked_by = $2 AND state = 'running'",
            partition_id, worker_id, last_key, batch_no, rows_read, rows_written, rows_skipped,
            values_by_method, verify_checked, float(settings.partition_lease_seconds))
    return db.rowcount(tag) > 0


async def finish_partition(worker_id: str, partition_id: int, state: PartitionState, *,
                           error_code: str | None = None, error: str | None = None,
                           meta: dict[str, Any] | None = None) -> bool:
    """Terminal partition state (fenced) + the exactly-once finalize transition of its job."""
    s = db.schema_ident()
    async with db.acquire_queue(None) as conn, conn.transaction():
        job_id = await conn.fetchval(f"SELECT job_id FROM {s}.dps_job_partition WHERE id = $1", partition_id)
        if job_id is None:
            return False
        await _lock_job(conn, job_id)
        tag = await conn.execute(
            f"UPDATE {s}.dps_job_partition SET state = $3, error_code = COALESCE($4, error_code), "
            "error = COALESCE($5, error), meta = meta || COALESCE($6::jsonb, '{}'::jsonb), locked_by = NULL, "
            "lease_expires_at = NULL, finished_at = now(), updated_at = now() "
            "WHERE id = $1 AND locked_by = $2 AND state = 'running'",
            partition_id, worker_id, state.value, error_code, (error or "")[:500] or None, meta)
        if db.rowcount(tag) == 0:
            return False
        await _maybe_finalize(conn, job_id)
    return True


async def retry_partition(worker_id: str, partition_id: int, *, error_code: str, error: str,
                          backoff_base: float, backoff_cap: float) -> PartitionState | None:
    """Requeue with backoff (counts as a resume), or dead-letter at ``max_attempts`` (fenced)."""
    s = db.schema_ident()
    async with db.acquire_queue(None) as conn, conn.transaction():
        job_id = await conn.fetchval(f"SELECT job_id FROM {s}.dps_job_partition WHERE id = $1", partition_id)
        if job_id is None:
            return None
        await _lock_job(conn, job_id)
        row = await conn.fetchrow(
            f"""UPDATE {s}.dps_job_partition SET
                state = CASE WHEN attempts >= max_attempts THEN 'dead' ELSE 'queued' END,
                resumed = resumed + CASE WHEN attempts >= max_attempts THEN 0 ELSE 1 END,
                error_code = $3, error = $4, locked_by = NULL, lease_expires_at = NULL,
                run_after = now() + least($5, $6 * 2 ^ attempts) * (0.5 + random()) * interval '1 second',
                finished_at = CASE WHEN attempts >= max_attempts THEN now() END, updated_at = now()
            WHERE id = $1 AND locked_by = $2 AND state = 'running' RETURNING state""",
            partition_id, worker_id, error_code, error[:500], float(backoff_cap), float(backoff_base))
        if row is None:
            return None
        if row["state"] == "dead":
            await _maybe_finalize(conn, job_id)
        else:
            await conn.execute("SELECT pg_notify($1, 'partition')", NOTIFY_CHANNEL)
    return PartitionState(row["state"])


async def reset_partition_progress(worker_id: str, partition_id: int) -> bool:
    """Zero a partition's checkpoint (``files`` targets restart a partition from its lower bound)."""
    async with db.acquire_queue(None) as conn:
        tag = await conn.execute(
            f"UPDATE {db.schema_ident()}.dps_job_partition SET last_key = NULL, batch_no = 0, rows_read = 0, "
            "rows_written = 0, rows_skipped = 0, values_by_method = '{}'::jsonb, verify_checked = 0, "
            "meta = '{}'::jsonb, updated_at = now() WHERE id = $1 AND locked_by = $2 AND state = 'running'",
            partition_id, worker_id)
    return db.rowcount(tag) > 0


async def abandon_partition(worker_id: str, partition_id: int) -> None:
    """Drop this worker's lock on a partition that was canceled under it (unblocks cancel-finalize)."""
    async with db.acquire_queue(None) as conn:
        await conn.execute(
            f"UPDATE {db.schema_ident()}.dps_job_partition SET locked_by = NULL, lease_expires_at = NULL "
            "WHERE id = $1 AND locked_by = $2 AND state = 'canceled'", partition_id, worker_id)


async def partition_control(worker_id: str, partition_id: int) -> Literal["continue", "pause", "cancel", "lost"]:
    """What a partition holder should do after a committed batch."""
    async with db.acquire_queue(None) as conn:
        row = await conn.fetchrow(
            f"SELECT p.state AS pstate, p.locked_by, j.state AS jstate FROM {db.schema_ident()}.dps_job_partition p "
            f"JOIN {db.schema_ident()}.dps_job j ON j.id = p.job_id WHERE p.id = $1", partition_id)
    if row is None:
        return "lost"
    if row["pstate"] == "canceled" or row["jstate"] == "canceled":
        return "cancel"
    if row["locked_by"] != worker_id or row["pstate"] != "running":
        return "lost"
    return "pause" if row["jstate"] == "paused" else "continue"


async def list_partitions(tenant_id: str, job_id: str) -> list[PartitionView]:
    """Value-free partition rows for the console (checkpoint shown as a fingerprint only)."""
    async with db.acquire(tenant_id) as conn:
        rows = await conn.fetch(
            f"SELECT partition_no, state, rows_read, rows_written, rows_skipped, batch_no, attempts, resumed, "
            "last_key, started_at, finished_at, updated_at, error_code, "
            "CASE WHEN started_at IS NOT NULL AND rows_written > 0 THEN rows_written / GREATEST(extract(epoch FROM "
            "COALESCE(finished_at, updated_at) - started_at), 0.001) END AS rps "
            f"FROM {db.schema_ident()}.dps_job_partition WHERE job_id = $1 ORDER BY partition_no", job_id)
    return [PartitionView(partition_no=r["partition_no"], state=PartitionState(r["state"]), rows_read=r["rows_read"],
                          rows_written=r["rows_written"], rows_skipped=r["rows_skipped"], batch_no=r["batch_no"],
                          attempts=r["attempts"], resumed=r["resumed"], checkpoint_fp=key_fingerprint(r["last_key"]),
                          started_at=r["started_at"], finished_at=r["finished_at"], updated_at=r["updated_at"],
                          error_code=r["error_code"], rows_per_second=float(r["rps"]) if r["rps"] is not None else None)
            for r in rows]
```

- [ ] **Step 4: Add the reaper, controls and stats**

```python
async def reap(*, backoff_base: float, backoff_cap: float) -> ReapResult:
    """Requeue expired leases with backoff, dead-letter exhausted ones, prune offline workers.

    Each statement is its own transaction; finalize transitions for jobs whose last partition was
    dead-lettered run afterwards, one short transaction per job (job lock first).
    """
    settings = db.active_settings()
    s = db.schema_ident()
    backoff = "now() + least($1, $2 * 2 ^ attempts) * (0.5 + random()) * interval '1 second'"
    result = ReapResult()
    async with db.acquire_queue(None) as conn:
        rows = await conn.fetch(
            f"UPDATE {s}.dps_job SET state = 'queued', locked_by = NULL, lease_expires_at = NULL, "
            f"run_after = {backoff}, updated_at = now() WHERE state = 'running' AND lease_expires_at < now() "
            "AND attempts < max_attempts RETURNING id", float(backoff_cap), float(backoff_base))
        result.jobs_requeued = len(rows)
        rows = await conn.fetch(
            f"UPDATE {s}.dps_job j SET state = 'dead', key_enc = NULL, error_code = COALESCE(error_code, 'lease_lost'), "
            "error = COALESCE(error, 'lease expired; max attempts reached'), locked_by = NULL, "
            "lease_expires_at = NULL, finished_at = now(), expires_at = now() + make_interval(days => COALESCE("
            f"(SELECT t.retention_days FROM {s}.dps_tenant t WHERE t.tenant_id = j.tenant_id), $1)), updated_at = now() "
            "WHERE state = 'running' AND lease_expires_at < now() AND attempts >= max_attempts RETURNING id",
            settings.retention_days)
        result.jobs_dead = len(rows)
        rows = await conn.fetch(
            f"UPDATE {s}.dps_job_partition SET state = 'queued', locked_by = NULL, lease_expires_at = NULL, "
            f"resumed = resumed + 1, run_after = {backoff}, updated_at = now() WHERE state = 'running' "
            "AND lease_expires_at < now() AND attempts < max_attempts RETURNING job_id",
            float(backoff_cap), float(backoff_base))
        result.partitions_requeued = len(rows)
        dead_jobs = await conn.fetch(
            f"UPDATE {s}.dps_job_partition SET state = 'dead', error_code = COALESCE(error_code, 'lease_lost'), "
            "locked_by = NULL, lease_expires_at = NULL, finished_at = now(), updated_at = now() "
            "WHERE state = 'running' AND lease_expires_at < now() AND attempts >= max_attempts RETURNING job_id")
        result.partitions_dead = len(dead_jobs)
        result.partitions_released = db.rowcount(await conn.execute(
            f"UPDATE {s}.dps_job_partition SET locked_by = NULL, lease_expires_at = NULL "
            "WHERE state = 'canceled' AND locked_by IS NOT NULL AND lease_expires_at < now()"))
        for job_id in sorted({r["job_id"] for r in dead_jobs}):
            async with conn.transaction():
                await _lock_job(conn, job_id)
                await _maybe_finalize(conn, job_id)
        heartbeat_s = max(1.0, settings.partition_lease_seconds / 4)
        result.workers_pruned = db.rowcount(await conn.execute(
            f"DELETE FROM {s}.dps_worker WHERE last_seen < now() - make_interval(secs => $1)", 3 * heartbeat_s))
        if result.jobs_requeued or result.partitions_requeued:
            await conn.execute("SELECT pg_notify($1, 'reaped')", NOTIFY_CHANNEL)
    return result


async def cancel(tenant_id: str, job_id: str) -> Job | None:
    """Cancel (contract §3.1): run jobs get a cancel-finalize; other kinds become ``canceled`` now."""
    settings = db.active_settings()
    s = db.schema_ident()
    async with db.acquire(tenant_id) as conn, conn.transaction():
        row = await conn.fetchrow(f"SELECT kind, state, phase FROM {s}.dps_job WHERE id = $1 FOR UPDATE", job_id)
        if row is None:
            return None
        if JobState(row["state"]) in TERMINAL_JOB_STATES:
            raise JobStateError("job_terminal", "the job has already finished")
        if row["phase"] == JobPhase.finalize.value:
            raise JobStateError("job_finalizing", "the job is finalizing and can no longer be canceled")
        # Running partitions keep locked_by: their holders' next fenced write fails and they abandon.
        await conn.execute(f"UPDATE {s}.dps_job_partition SET state = 'canceled', finished_at = now(), "
                           "updated_at = now() WHERE job_id = $1 AND state IN ('queued', 'running')", job_id)
        if row["kind"] == JobKind.run.value:
            await conn.execute(
                f"UPDATE {s}.dps_job SET phase = 'finalize', state = 'queued', run_after = now(), attempts = 0, "
                "locked_by = NULL, lease_expires_at = NULL, progress = progress || '{\"outcome\": \"canceled\"}'::jsonb, "
                "updated_at = now() WHERE id = $1", job_id)
            await conn.execute("SELECT pg_notify($1, 'finalize')", NOTIFY_CHANNEL)
        else:
            await conn.execute(
                f"UPDATE {s}.dps_job j SET state = 'canceled', locked_by = NULL, lease_expires_at = NULL, "
                "key_enc = NULL, finished_at = now(), progress = progress || '{\"outcome\": \"canceled\"}'::jsonb, "
                "expires_at = now() + make_interval(days => COALESCE((SELECT t.retention_days FROM "
                f"{s}.dps_tenant t WHERE t.tenant_id = j.tenant_id), $2)), updated_at = now() WHERE id = $1",
                job_id, settings.retention_days)
    return await get_job(tenant_id, job_id)


async def pause(tenant_id: str, job_id: str) -> Job | None:
    """``waiting`` run job → ``paused`` (running partitions stop after their current batch)."""
    return await _flip(tenant_id, job_id, "waiting", "paused", "job_not_pausable",
                       "only a run job that is processing partitions can be paused")


async def resume(tenant_id: str, job_id: str) -> Job | None:
    """``paused`` → ``waiting`` (and finalize when every partition already finished)."""
    return await _flip(tenant_id, job_id, "paused", "waiting", "job_not_paused", "the job is not paused")


async def _flip(tenant_id: str, job_id: str, src: str, dst: str, code: str, detail: str) -> Job | None:
    s = db.schema_ident()
    async with db.acquire(tenant_id) as conn, conn.transaction():
        row = await conn.fetchrow(f"SELECT state, phase FROM {s}.dps_job WHERE id = $1 FOR UPDATE", job_id)
        if row is None:
            return None
        if row["state"] != src or row["phase"] != JobPhase.partitions.value:
            raise JobStateError(code, detail)
        await conn.execute(f"UPDATE {s}.dps_job SET state = $2, updated_at = now() WHERE id = $1", job_id, dst)
        if dst == "waiting":
            await _maybe_finalize(conn, job_id)
            await conn.execute("SELECT pg_notify($1, 'partition')", NOTIFY_CHANNEL)
    return await get_job(tenant_id, job_id)


async def retry(tenant_id: str, job_id: str, *, sealed_secret: bytes | None = None) -> Job | None:
    """Requeue a ``failed``/``dead`` run job from its checkpoints (contract §3.1)."""
    s = db.schema_ident()
    async with db.acquire(tenant_id) as conn, conn.transaction():
        row = await conn.fetchrow(f"SELECT kind, state, key_enc IS NOT NULL AS has_key FROM {s}.dps_job "
                                  "WHERE id = $1 FOR UPDATE", job_id)
        if row is None:
            return None
        if row["kind"] != JobKind.run.value or row["state"] not in ("failed", "dead"):
            raise JobStateError("job_not_retryable", "only failed or dead run jobs can be retried")
        if not row["has_key"] and sealed_secret is None:
            raise KeyMaterialRequired()
        parts = await conn.fetchval(f"SELECT count(*) FROM {s}.dps_job_partition WHERE job_id = $1", job_id)
        if parts:
            await conn.execute(
                f"UPDATE {s}.dps_job_partition SET state = 'queued', attempts = 0, run_after = now(), "
                "error_code = NULL, error = NULL, locked_by = NULL, lease_expires_at = NULL, finished_at = NULL, "
                "updated_at = now() WHERE job_id = $1 AND state IN ('failed', 'dead', 'canceled')", job_id)
        phase, state = ("partitions", "waiting") if parts else ("prepare", "queued")
        await conn.execute(
            f"UPDATE {s}.dps_job SET phase = $2, state = $3, attempts = 0, run_after = now(), error_code = NULL, "
            "error = NULL, finished_at = NULL, expires_at = NULL, key_enc = COALESCE($4, key_enc), "
            "progress = progress || '{\"outcome\": \"\"}'::jsonb, locked_by = NULL, lease_expires_at = NULL, "
            "updated_at = now() WHERE id = $1", job_id, phase, state, sealed_secret)
        if parts:
            await _maybe_finalize(conn, job_id)         # every partition was already done: go straight to finalize
        await conn.execute("SELECT pg_notify($1, 'retry')", NOTIFY_CHANNEL)
    return await get_job(tenant_id, job_id)


async def queue_stats() -> list[dict[str, Any]]:
    """Per-table, per-state counts and oldest age — the queue gauges (worker pool, cross-tenant)."""
    s = db.schema_ident()
    async with db.acquire_queue(None) as conn:
        rows = await conn.fetch(
            f"SELECT 'dps_job' AS table, state, count(*) AS n, extract(epoch FROM now() - min(created_at)) AS oldest_s "
            f"FROM {s}.dps_job GROUP BY state UNION ALL "
            f"SELECT 'dps_job_partition', state, count(*), extract(epoch FROM now() - min(run_after)) "
            f"FROM {s}.dps_job_partition GROUP BY state")
    return [dict(r) for r in rows]


async def tenant_stats(tenant_id: str) -> JobStats:
    """The Jobs page health strip (mockup 07) for one tenant."""
    settings = db.active_settings()
    s = db.schema_ident()
    heartbeat_s = max(1.0, settings.partition_lease_seconds / 4)
    async with db.acquire(tenant_id) as conn:
        w = await conn.fetchrow(
            "SELECT count(*) AS online, count(*) FILTER (WHERE busy > 0) AS busy, "
            f"count(*) FILTER (WHERE draining) AS draining FROM {s}.dps_worker "
            "WHERE role = 'worker' AND last_seen > now() - make_interval(secs => $1)", 3 * heartbeat_s)
        waiting = await conn.fetchrow(
            f"SELECT count(*) AS n, extract(epoch FROM now() - min(p.run_after)) AS oldest FROM {s}.dps_job_partition p "
            f"JOIN {s}.dps_job j ON j.id = p.job_id WHERE p.state = 'queued' AND j.state = 'waiting'")
        running = await conn.fetchval(
            f"SELECT (SELECT count(*) FROM {s}.dps_job WHERE state = 'running') + "
            f"(SELECT count(*) FROM {s}.dps_job_partition WHERE state = 'running')")
        rps = await conn.fetchval(
            f"SELECT COALESCE(sum((progress->>'rows_per_second')::float), 0) FROM {s}.dps_job "
            "WHERE state IN ('waiting', 'paused') AND phase = 'partitions'")
        by_state = {r["state"]: r["n"] for r in await conn.fetch(
            f"SELECT state, count(*) AS n FROM {s}.dps_job WHERE created_at > now() - interval '7 days' GROUP BY state")}
        cap = await conn.fetchval(f"SELECT max_running FROM {s}.dps_tenant WHERE tenant_id = $1", tenant_id)
        reclaims = await conn.fetchval(
            f"SELECT COALESCE(sum(resumed), 0) FROM {s}.dps_job_partition WHERE updated_at > now() - interval '24 hours'")
    return JobStats(workers_online=w["online"], workers_busy=w["busy"], workers_draining=w["draining"],
                    partitions_waiting=waiting["n"], oldest_waiting_s=float(waiting["oldest"]) if waiting["oldest"] else None,
                    rows_per_s=float(rps or 0), by_state=by_state, running=int(running),
                    tenant_max_running=int(cap or settings.job_tenant_max_running), lease_reclaims_24h=int(reclaims))


async def sample_throughput(*, min_interval_s: float = 10.0) -> int:
    """Append a ``(ts, rows/s)`` point (≤ 60 kept) to each processing run job; returns jobs sampled."""
    s = db.schema_ident()
    sampled = 0
    async with db.acquire_queue(None) as conn, conn.transaction():
        rows = await conn.fetch(
            f"SELECT j.id, j.progress, j.started_at, now() AS now, (SELECT COALESCE(sum(p.rows_written), 0) "
            f"FROM {s}.dps_job_partition p WHERE p.job_id = j.id) AS written FROM {s}.dps_job j "
            "WHERE j.kind = 'run' AND j.phase = 'partitions' AND j.state IN ('waiting', 'paused') "
            "FOR UPDATE OF j SKIP LOCKED")
        for r in rows:
            progress = r["progress"] or {}
            mark = progress.get("throughput_mark")
            last_ts = datetime.fromisoformat(mark[0]) if mark else (r["started_at"] or r["now"])
            last_rows = int(mark[1]) if mark else 0
            elapsed = (r["now"] - last_ts).total_seconds()
            if elapsed < min_interval_s or elapsed <= 0:
                continue
            rate = round(max(0, int(r["written"]) - last_rows) / elapsed, 1)
            points = (progress.get("throughput") or [])[-59:] + [[r["now"].isoformat(), rate]]
            await conn.execute(
                f"UPDATE {s}.dps_job SET progress = progress || $2::jsonb, updated_at = now() WHERE id = $1",
                r["id"], {"throughput": points, "throughput_mark": [r["now"].isoformat(), int(r["written"])],
                          "rows_per_second": rate})
            sampled += 1
    return sampled
```

- [ ] **Step 5: Run all queue tests, lint, types**

Run: `pytest -q tests/db/test_jobs_queue.py tests/db/test_partitions_queue.py && ruff check dps tests tools && mypy dps`
Expected: `13 + 10 = 23 passed`, clean. If `test_finalize_transition_happens_exactly_once` ever leaves the job in `waiting`, check that `_lock_job` runs **before** the partition `UPDATE` in `finish_partition`.

- [ ] **Step 6: Commit**

```bash
git add dps/jobs.py tests/db/test_jobs_queue.py tests/db/test_partitions_queue.py
git commit -m "feat(jobs): partitions with fenced checkpoints, exactly-once finalize, reaper, pause/cancel/retry, stats" -- dps/jobs.py tests/db/test_jobs_queue.py tests/db/test_partitions_queue.py
```

---

### Task 10: DB-backed audit (`DbAuditSink`)

**Files:**
- Modify: `dps/audit.py`, `dps/layers/api/common.py`
- Test: `tests/test_audit_m2.py`, `tests/db/test_audit_sink.py`

Contract §8.3: a structured log line is written **synchronously first** (the crash-safe record), then the event is queued for a batched background writer into `dps_audit_log`; `DPS_AUDIT_STRICT=true` turns a saturated queue into HTTP 503. The table has FORCE RLS, so each flush groups events by tenant and inserts each group under that tenant's GUC.

Public interface (additions to M0's `dps/audit.py`):
```python
class AuditEvent(BaseModel):                       # M0 fields unchanged, plus (all defaulted):
    action: str = ""                               # detect|obfuscate|…|job.create|snapshot.accept|connection.test|…
    status: int | None = None
    job_id: str | None = None
    counts: dict[str, int] = {}                    # extra value-free counts (rows, partitions, columns)
class AuditUnavailable(Exception)                  # strict mode, saturated queue
AcquireFn = Callable[[str | None], AbstractAsyncContextManager[asyncpg.Connection]]
class DbAuditSink:
    def __init__(self, acquire: AcquireFn, settings: Settings) -> None
    def start(self) -> None                        # inside a running loop
    async def stop(self, timeout: float = 10.0) -> None   # flushes what is queued
    def write(self, event: AuditEvent) -> None     # sync log line, then enqueue; AuditUnavailable when strict + full
    @property
    def healthy(self) -> bool
    @property
    def last_error(self) -> str | None             # an error class name, never a message
def audit_row(event: AuditEvent) -> tuple[Any, ...]
```

- [ ] **Step 1: Write the failing tests**

`tests/test_audit_m2.py` (offline):
```python
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from dps.app import create_app
from dps.audit import AuditEvent, AuditUnavailable, DbAuditSink, audit_row
from dps.config import Settings


def _event(**kw) -> AuditEvent:  # noqa: ANN003
    base = dict(trace_id="t", route="/api/v1/jobs/{job_id}/cancel", tenant_id="t1", key_id="k", mode="",
                policy="-", action="job.cancel", status=200, job_id="j_1", counts={"partitions": 4})
    base.update(kw)
    return AuditEvent(**base)


def test_audit_row_maps_fields_and_counts() -> None:
    row = audit_row(_event(findings_total=3, methods_used={"det_token": 3}))
    ts, tenant, key, trace, route, action, mode, policy, outcome, status, job_id, counts = row
    assert (tenant, route, action, status, job_id, outcome) == ("t1", "/api/v1/jobs/{job_id}/cancel", "job.cancel", 200, "j_1", "ok")
    assert counts["partitions"] == 4 and counts["findings_total"] == 3 and counts["methods_used"] == {"det_token": 3}
    assert audit_row(_event(action="", mode="obfuscate"))[5] == "obfuscate"


async def test_strict_sink_refuses_when_full_and_lenient_drops() -> None:
    async def never(_t):  # noqa: ANN001, ANN202 - never called (writer not started)
        raise AssertionError

    strict = DbAuditSink(never, Settings(_env_file=None, audit_queue_max=1, audit_strict=True))  # type: ignore[arg-type]
    strict.start()
    strict.write(_event())
    with pytest.raises(AuditUnavailable):
        strict.write(_event())
    lenient = DbAuditSink(never, Settings(_env_file=None, audit_queue_max=1, audit_strict=False))  # type: ignore[arg-type]
    lenient.start()
    lenient.write(_event())
    lenient.write(_event())                         # dropped, counted, no exception
    await strict.stop(timeout=0.1)
    await lenient.stop(timeout=0.1)


class _Raising:
    def write(self, event: AuditEvent) -> None:
        raise AuditUnavailable()


def test_api_returns_503_when_strict_audit_is_unavailable() -> None:
    settings = Settings(_env_file=None, auth_enabled=False, llm_provider="stub", audit_strict=True,
                        rate_limit_enabled=False)
    with TestClient(create_app(settings, audit_sink=_Raising())) as client:
        r = client.post("/api/v1/detect", json={"data": {"email": "a@b.co"}})
    assert r.status_code == 503 and json.loads(r.text)["detail"] == "audit unavailable"
```

`tests/db/test_audit_sink.py`:
```python
from __future__ import annotations

import pytest

from dps import db
from dps.audit import AuditEvent, DbAuditSink

pytestmark = pytest.mark.db


async def test_rows_land_per_tenant_under_rls(migrated_db) -> None:  # noqa: ANN001
    sink = DbAuditSink(db.acquire, migrated_db)
    sink.start()
    for tenant in ("t1", "t2", "t1"):
        sink.write(AuditEvent(trace_id="x", route="/api/v1/connections", tenant_id=tenant, key_id="k",
                              mode="", policy="-", action="connection.create", status=201))
    await sink.stop()
    assert sink.healthy
    async with db.acquire("t1") as conn:
        assert await conn.fetchval("SELECT count(*) FROM dps.dps_audit_log") == 2
    async with db.acquire("t2") as conn:
        assert await conn.fetchval("SELECT count(*) FROM dps.dps_audit_log") == 1


async def test_flush_failure_marks_unhealthy_without_detail(migrated_db) -> None:  # noqa: ANN001
    class Boom(Exception):
        pass

    def broken(_tenant):  # noqa: ANN001, ANN202
        raise Boom("customer value 123-45-6789")

    sink = DbAuditSink(broken, migrated_db)  # type: ignore[arg-type]
    sink.start()
    sink.write(AuditEvent(trace_id="x", route="/r", tenant_id="t1", key_id="k", mode="", policy="-"))
    await sink.stop()
    assert sink.healthy is False and sink.last_error == "Boom"
```

- [ ] **Step 2: Run to confirm failure**

Run: `pytest -q tests/test_audit_m2.py tests/db/test_audit_sink.py`
Expected: FAIL — `AuditUnavailable`, `DbAuditSink`, `audit_row` do not exist.

- [ ] **Step 3: Extend `dps/audit.py`**

Add the four defaulted fields to `AuditEvent`, then:
```python
import asyncio
from collections import defaultdict
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Any

from dps.config import Settings
from dps.observability import AUDIT_DROPPED

AcquireFn = Callable[[str | None], AbstractAsyncContextManager[Any]]


class AuditUnavailable(Exception):
    """Strict mode: the audit queue is saturated, so the request must not proceed (503)."""


def audit_row(event: AuditEvent) -> tuple[Any, ...]:
    """The ``dps_audit_log`` row for an event (route template, action, value-free counts)."""
    counts: dict[str, Any] = {"findings_total": event.findings_total, "reversible_values": event.reversible_values,
                              "duration_ms": int(event.duration_ms), **event.counts}
    if event.findings_by_type:
        counts["findings_by_type"] = event.findings_by_type
    if event.methods_used:
        counts["methods_used"] = event.methods_used
    if event.verification_passed is not None:
        counts["verification_passed"] = event.verification_passed
    action = event.action or (event.mode.split(":")[0] if event.mode else "request")
    return (event.ts, event.tenant_id, event.key_id, event.trace_id, event.route, action,
            event.mode or None, event.policy or None, event.outcome, event.status, event.job_id, counts)


class DbAuditSink:
    """Synchronous log line + batched, per-tenant inserts into ``dps_audit_log``."""

    def __init__(self, acquire: AcquireFn, settings: Settings) -> None:
        self._acquire = acquire
        self._settings = settings
        self._log = LogAuditSink()
        self._queue: asyncio.Queue[AuditEvent] | None = None
        self._task: asyncio.Task[None] | None = None
        self._healthy = True
        self._last_error: str | None = None

    @property
    def healthy(self) -> bool:
        return self._healthy

    @property
    def last_error(self) -> str | None:
        return self._last_error

    def start(self) -> None:
        self._queue = asyncio.Queue(maxsize=self._settings.audit_queue_max)
        self._task = asyncio.create_task(self._run(), name="audit-writer")

    async def stop(self, timeout: float = 10.0) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await asyncio.wait_for(self._task, timeout=timeout)
        except (asyncio.CancelledError, TimeoutError):
            pass
        self._task = None

    def write(self, event: AuditEvent) -> None:
        self._log.write(event)                       # the crash-safe record, first
        if self._queue is None:
            return
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            AUDIT_DROPPED.inc()
            if self._settings.audit_strict:
                raise AuditUnavailable() from None

    async def _run(self) -> None:
        assert self._queue is not None
        batch: list[AuditEvent] = []
        try:
            while True:
                try:
                    batch.append(await asyncio.wait_for(self._queue.get(), self._settings.audit_flush_ms / 1000))
                except TimeoutError:
                    pass
                while len(batch) < self._settings.audit_flush_batch and not self._queue.empty():
                    batch.append(self._queue.get_nowait())
                if batch:
                    await self._flush(batch)
                    batch = []
        except asyncio.CancelledError:
            while not self._queue.empty():
                batch.append(self._queue.get_nowait())
            if batch:
                await self._flush(batch)
            raise

    async def _flush(self, batch: list[AuditEvent]) -> None:
        by_tenant: dict[str, list[AuditEvent]] = defaultdict(list)
        for event in batch:
            by_tenant[event.tenant_id or "-"].append(event)
        try:
            for tenant, events in by_tenant.items():
                async with self._acquire(tenant) as conn:
                    await conn.executemany(
                        "INSERT INTO dps_audit_log (ts, tenant_id, key_id, trace_id, route, action, mode, policy, "
                        "outcome, status, job_id, counts) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12::jsonb)",
                        [audit_row(e) for e in events])
            self._healthy, self._last_error = True, None
        except Exception as exc:  # noqa: BLE001 - the writer must survive a bad batch; never echo the message
            self._healthy, self._last_error = False, type(exc).__name__
            logger.warning("audit_flush_failed", error=type(exc).__name__, count=len(batch))
```
(`dps_audit_log` resolves through the pool's `search_path`; the schema is the only one on it.)

- [ ] **Step 4: Touch `dps/layers/api/common.py`** — in `_audit`, use the route template and honour strict mode:
```python
    route_obj = request.scope.get("route")
    route = getattr(route_obj, "path", request.url.path)       # template: /api/v1/jobs/{job_id}
    event = AuditEvent(trace_id=tid, route=route, …, status=status_code)   # other fields as in M0
    try:
        request.app.state.audit.write(event)
    except AuditUnavailable as exc:
        raise HTTPException(status_code=503, detail="audit unavailable") from exc
    except Exception:  # noqa: BLE001 - a non-strict sink failure never takes the request down
        structlog.get_logger(__name__).warning("audit_write_failed")
```
`run_engine` passes the response status it is about to return (200, or the mapped error status) as `status_code`.

- [ ] **Step 5: Run tests, lint, types**

Run: `pytest -q tests/test_audit_m2.py tests/db/test_audit_sink.py tests/test_api.py && ruff check dps tests tools && mypy dps`
Expected: `3 + 2 = 5` new tests pass and M0's `tests/test_api.py` stays green.

- [ ] **Step 6: Commit**

```bash
git add dps/audit.py dps/layers/api/common.py tests/test_audit_m2.py tests/db/test_audit_sink.py
git commit -m "feat(audit): DB-backed append-only audit sink with strict-mode 503 and route templates" -- dps/audit.py dps/layers/api/common.py tests/test_audit_m2.py tests/db/test_audit_sink.py
```

---

### Task 11: Object storage and artefacts (`dps/storage.py`) + MinIO service

**Files:**
- Create: `dps/storage.py`
- Modify: `docker-compose.yml` (add `minio`, `minio-init`), `HANDOFF.md` (ports 9010/9011)
- Test: `tests/test_storage_local.py`, `tests/connectors/test_storage_s3.py`, `tests/db/test_artefacts.py`, `tests/fixtures/sources.py` (`object_store`, `s3_store` fixtures)

Public interface (contract §13; `StorageError`, `ArtefactService.get_row` are additions; `target_store` arrives in Task 14 because it needs `Connection`):
```python
class StorageError(Exception)              # .code: storage_key_invalid | storage_not_found | storage_error
class StoredObject(BaseModel): store: Literal["local", "s3"]; key: str; bytes: int; sha256: str; content_type: str
class ObjectStore(Protocol): ...           # contract §13 verbatim
class LocalObjectStore:                    # __init__(root: Path); refuses keys escaping root; atomic writes
class S3ObjectStore:                       # __init__(*, bucket, endpoint_url, region, access_key_id,
                                           #   secret_access_key, force_path_style); boto3 in to_thread; multipart > 16 MiB
def artefact_store(settings: Settings) -> ObjectStore
class Artefact(BaseModel): id, tenant_id, job_id, kind, store, object_key, content_type, bytes, sha256, meta, created_at, expires_at
class ArtefactService:
    def __init__(self, store: ObjectStore, *, acquire: AcquireFn) -> None
    async def save(self, *, tenant_id: str, job_id: str, kind: str, data: bytes, content_type: str,
                   meta: dict[str, Any] | None = None) -> str
    async def get_row(self, tenant_id: str, artefact_id: str) -> Artefact | None
    async def open(self, tenant_id: str, artefact_id: str) -> tuple[Artefact, AsyncIterator[bytes]]
    async def delete_for_job(self, tenant_id: str, job_id: str) -> int
ARTEFACT_EXT: dict[str, str]               # evidence_html→html, evidence_json→json, manifest→json, …
```
Object keys: `t/<tenant_id>/<job_id>/<artefact_id>.<ext>` (artefacts). Keys never contain values.

- [ ] **Step 1: Add MinIO to `docker-compose.yml`** (contract §11)

```yaml
  minio:
    image: minio/minio:latest
    command: ["server", "/data", "--console-address", ":9001"]
    environment:
      MINIO_ROOT_USER: minioadmin          # local-only demo literal
      MINIO_ROOT_PASSWORD: minioadmin
    ports: ["127.0.0.1:9010:9000", "127.0.0.1:9011:9001"]
    volumes: ["minio-data:/data"]
    healthcheck:
      test: ["CMD", "curl", "-fsS", "http://127.0.0.1:9000/minio/health/live"]
      interval: 5s
      timeout: 3s
      retries: 30
    mem_limit: 512m
    profiles: ["sources", "full"]
  minio-init:
    image: minio/mc:latest
    depends_on: {minio: {condition: service_healthy}}
    entrypoint: ["/bin/sh", "-c"]
    command: ["mc alias set local http://minio:9000 minioadmin minioadmin && mc mb --ignore-existing local/dps-artefacts && mc mb --ignore-existing local/dps-exports"]
    profiles: ["sources", "full"]
```
Add `minio-data: {}` to `volumes`. Before relying on the healthcheck, confirm the cached image ships curl: `docker run --rm --entrypoint sh minio/minio:latest -c 'command -v curl || echo missing'`. If it prints `missing`, use `test: ["CMD", "mc", "ready", "local"]` (the minio image bundles `mc`) and record the deviation in HANDOFF and the build report.

- [ ] **Step 2: Write the failing tests**

`tests/test_storage_local.py`:
```python
from __future__ import annotations

import hashlib

import pytest

from dps.storage import LocalObjectStore, StorageError


async def _chunks():  # noqa: ANN202
    for part in (b"a" * 10, b"b" * 5):
        yield part


async def test_put_get_stream_delete(tmp_path) -> None:  # noqa: ANN001
    store = LocalObjectStore(tmp_path)
    obj = await store.put("t/t1/j_1/art_1.json", b'{"ok":true}', content_type="application/json")
    assert obj.store == "local" and obj.bytes == 11 and obj.sha256 == hashlib.sha256(b'{"ok":true}').hexdigest()
    assert await store.get("t/t1/j_1/art_1.json") == b'{"ok":true}' and await store.exists("t/t1/j_1/art_1.json")
    streamed = await store.put("x/y.bin", _chunks(), content_type="application/octet-stream")
    assert streamed.bytes == 15 and b"".join([c async for c in store.stream("x/y.bin", chunk=4)]) == b"a" * 10 + b"b" * 5
    await store.delete("x/y.bin")
    await store.delete("x/y.bin")                                     # idempotent
    assert not await store.exists("x/y.bin")


@pytest.mark.parametrize("key", ["../escape", "/abs", "a/../../b", "", "a\\b", "a b"])
async def test_keys_cannot_escape_or_carry_odd_characters(tmp_path, key: str) -> None:  # noqa: ANN001
    with pytest.raises(StorageError) as err:
        await LocalObjectStore(tmp_path).put(key, b"x", content_type="text/plain")
    assert err.value.code == "storage_key_invalid"


async def test_missing_object(tmp_path) -> None:  # noqa: ANN001
    with pytest.raises(StorageError) as err:
        await LocalObjectStore(tmp_path).get("nope.json")
    assert err.value.code == "storage_not_found"
```

`tests/connectors/test_storage_s3.py`:
```python
from __future__ import annotations

import os

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.s3]


async def test_round_trip_and_multipart(s3_store) -> None:  # noqa: ANN001
    big = os.urandom(17 * 1024 * 1024)
    obj = await s3_store.put("tests/big.bin", big, content_type="application/octet-stream")
    assert obj.store == "s3" and obj.bytes == len(big)
    assert await s3_store.get("tests/big.bin") == big
    assert b"".join([c async for c in s3_store.stream("tests/big.bin")]) == big
    await s3_store.delete("tests/big.bin")
    assert not await s3_store.exists("tests/big.bin")
```

`tests/db/test_artefacts.py`:
```python
from __future__ import annotations

import pytest

from dps import db, jobs
from dps.jobs import JobKind, JobPhase
from dps.storage import ArtefactService, LocalObjectStore

pytestmark = pytest.mark.db


async def test_save_open_isolate_and_delete(migrated_db, tmp_path) -> None:  # noqa: ANN001
    job = await jobs.create_job(tenant_id="t1", kind=JobKind.run, phase=JobPhase.prepare, spec={})
    svc = ArtefactService(LocalObjectStore(tmp_path), acquire=db.acquire)
    art_id = await svc.save(tenant_id="t1", job_id=job.id, kind="evidence_json", data=b"{}", content_type="application/json")
    row, chunks = await svc.open("t1", art_id)
    assert row.object_key == f"t/t1/{job.id}/{art_id}.json" and b"".join([c async for c in chunks]) == b"{}"
    assert await svc.get_row("t2", art_id) is None
    assert await svc.delete_for_job("t1", job.id) == 1
    assert await svc.get_row("t1", art_id) is None and not (tmp_path / row.object_key).exists()
```

`tests/fixtures/sources.py` (append):
```python
@pytest.fixture
def object_store(tmp_path):  # noqa: ANN001, ANN201
    from dps.storage import LocalObjectStore
    return LocalObjectStore(tmp_path / "objects")


@pytest.fixture
def s3_store():  # noqa: ANN201
    from dps.storage import S3ObjectStore
    endpoint = require_service("minio")
    return S3ObjectStore(bucket="dps-artefacts", endpoint_url=endpoint, region="us-east-1",
                         access_key_id="minioadmin", secret_access_key="minioadmin", force_path_style=True)
```

- [ ] **Step 3: Run to confirm failure**

Run: `pytest -q tests/test_storage_local.py tests/db/test_artefacts.py tests/connectors/test_storage_s3.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'dps.storage'`.

- [ ] **Step 4: Write `dps/storage.py`**

```python
"""Object storage for artefacts and file exports (contract §13).

Keys are generated by DPS (ids, partition numbers) and validated here; they never contain values.
Local writes are atomic (temp file + rename). S3 runs boto3 in a thread; objects over 16 MiB use
multipart upload. Errors carry a code and the exception class name, never object contents.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import re
import tempfile
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

from dps.config import Settings
from dps.ids import new_id

_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9/_.=-]{0,1000}$")
MULTIPART_THRESHOLD = 16 * 1024 * 1024
ARTEFACT_EXT = {"evidence_html": "html", "evidence_json": "json", "evidence_pdf": "pdf", "manifest": "json",
                "export_csv": "csv", "export_json": "json", "screenshot": "png", "capture": "png",
                "redacted_pdf": "pdf"}
AcquireFn = Callable[[str | None], AbstractAsyncContextManager[Any]]


class StorageError(Exception):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


class StoredObject(BaseModel):
    store: Literal["local", "s3"]
    key: str
    bytes: int
    sha256: str
    content_type: str


class ObjectStore(Protocol):
    name: Literal["local", "s3"]

    async def put(self, key: str, data: bytes | AsyncIterator[bytes], *, content_type: str) -> StoredObject: ...
    async def get(self, key: str) -> bytes: ...
    def stream(self, key: str, *, chunk: int = 1 << 20) -> AsyncIterator[bytes]: ...
    async def delete(self, key: str) -> None: ...
    async def exists(self, key: str) -> bool: ...


def _check_key(key: str) -> str:
    if not _KEY_RE.fullmatch(key) or ".." in key.split("/"):
        raise StorageError("storage_key_invalid", "object keys are DPS-generated paths")
    return key


async def _spool(data: bytes | AsyncIterator[bytes]) -> tuple[Path, int, str]:
    """Write ``data`` to a temp file; returns (path, bytes, sha256)."""
    digest = hashlib.sha256()
    fd, name = tempfile.mkstemp(prefix="dps-spool-")
    size = 0
    with os.fdopen(fd, "wb") as fh:
        if isinstance(data, bytes):
            fh.write(data)
            digest.update(data)
            size = len(data)
        else:
            async for part in data:
                fh.write(part)
                digest.update(part)
                size += len(part)
    return Path(name), size, digest.hexdigest()


class LocalObjectStore:
    name: Literal["local", "s3"] = "local"

    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()

    def _path(self, key: str) -> Path:
        path = (self.root / _check_key(key)).resolve()
        if self.root not in path.parents:
            raise StorageError("storage_key_invalid", "key escapes the store root")
        return path

    async def put(self, key: str, data: bytes | AsyncIterator[bytes], *, content_type: str) -> StoredObject:
        path = self._path(key)
        tmp, size, sha = await _spool(data)
        await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(os.replace, tmp, path)
        return StoredObject(store="local", key=key, bytes=size, sha256=sha, content_type=content_type)

    async def get(self, key: str) -> bytes:
        path = self._path(key)
        if not path.is_file():
            raise StorageError("storage_not_found")
        return await asyncio.to_thread(path.read_bytes)

    async def stream(self, key: str, *, chunk: int = 1 << 20) -> AsyncIterator[bytes]:
        path = self._path(key)
        if not path.is_file():
            raise StorageError("storage_not_found")
        fh = await asyncio.to_thread(path.open, "rb")
        try:
            while part := await asyncio.to_thread(fh.read, chunk):
                yield part
        finally:
            fh.close()

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self._path(key).unlink, True)

    async def exists(self, key: str) -> bool:
        return self._path(key).is_file()


class S3ObjectStore:
    name: Literal["local", "s3"] = "s3"

    def __init__(self, *, bucket: str, endpoint_url: str | None, region: str, access_key_id: str,
                 secret_access_key: str, force_path_style: bool) -> None:
        import boto3
        from botocore.config import Config

        self.bucket = bucket
        self._client = boto3.client(
            "s3", endpoint_url=endpoint_url or None, region_name=region,
            aws_access_key_id=access_key_id or None, aws_secret_access_key=secret_access_key or None,
            config=Config(signature_version="s3v4", retries={"max_attempts": 3, "mode": "standard"},
                          s3={"addressing_style": "path" if force_path_style else "auto"}))

    async def put(self, key: str, data: bytes | AsyncIterator[bytes], *, content_type: str) -> StoredObject:
        from boto3.s3.transfer import TransferConfig

        _check_key(key)
        tmp, size, sha = await _spool(data)
        try:
            config = TransferConfig(multipart_threshold=MULTIPART_THRESHOLD, multipart_chunksize=MULTIPART_THRESHOLD)
            await asyncio.to_thread(self._client.upload_file, str(tmp), self.bucket, key,
                                    ExtraArgs={"ContentType": content_type}, Config=config)
        except Exception as exc:  # noqa: BLE001 - botocore errors can quote request details
            raise StorageError("storage_error", type(exc).__name__) from None
        finally:
            tmp.unlink(missing_ok=True)
        return StoredObject(store="s3", key=key, bytes=size, sha256=sha, content_type=content_type)

    async def get(self, key: str) -> bytes:
        return b"".join([part async for part in self.stream(key)])

    async def stream(self, key: str, *, chunk: int = 1 << 20) -> AsyncIterator[bytes]:
        _check_key(key)
        try:
            obj = await asyncio.to_thread(self._client.get_object, Bucket=self.bucket, Key=key)
        except self._client.exceptions.NoSuchKey:
            raise StorageError("storage_not_found") from None
        body = obj["Body"]
        try:
            while part := await asyncio.to_thread(body.read, chunk):
                yield part
        finally:
            body.close()

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self._client.delete_object, Bucket=self.bucket, Key=_check_key(key))

    async def exists(self, key: str) -> bool:
        from botocore.exceptions import ClientError

        try:
            await asyncio.to_thread(self._client.head_object, Bucket=self.bucket, Key=_check_key(key))
            return True
        except ClientError:
            return False


def artefact_store(settings: Settings) -> ObjectStore:
    """The artefact store selected by ``DPS_ARTEFACT_STORE``."""
    if settings.artefact_store == "s3":
        return S3ObjectStore(bucket=settings.s3_bucket, endpoint_url=settings.s3_endpoint_url or None,
                             region=settings.s3_region, access_key_id=settings.s3_access_key_id,
                             secret_access_key=settings.s3_secret_access_key,
                             force_path_style=settings.s3_force_path_style)
    return LocalObjectStore(Path(settings.artefact_dir))


class Artefact(BaseModel):
    id: str
    tenant_id: str
    job_id: str | None
    kind: str
    store: Literal["local", "s3"]
    object_key: str
    content_type: str
    bytes: int
    sha256: str
    meta: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    expires_at: datetime | None = None


class ArtefactService:
    """Artefact rows in ``dps_artefact`` + objects in an :class:`ObjectStore`."""

    def __init__(self, store: ObjectStore, *, acquire: AcquireFn) -> None:
        self.store = store
        self._acquire = acquire

    async def save(self, *, tenant_id: str, job_id: str, kind: str, data: bytes, content_type: str,
                   meta: dict[str, Any] | None = None) -> str:
        artefact_id = new_id("art")
        key = f"t/{tenant_id}/{job_id}/{artefact_id}.{ARTEFACT_EXT.get(kind, 'bin')}"
        obj = await self.store.put(key, data, content_type=content_type)
        async with self._acquire(tenant_id) as conn:
            await conn.execute(
                "INSERT INTO dps_artefact (id, tenant_id, job_id, kind, store, object_key, content_type, bytes, "
                "sha256, meta) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10::jsonb)",
                artefact_id, tenant_id, job_id, kind, obj.store, key, content_type, obj.bytes, obj.sha256, meta or {})
        return artefact_id

    async def get_row(self, tenant_id: str, artefact_id: str) -> Artefact | None:
        async with self._acquire(tenant_id) as conn:
            row = await conn.fetchrow("SELECT * FROM dps_artefact WHERE id = $1", artefact_id)
        return Artefact.model_validate(dict(row)) if row else None

    async def open(self, tenant_id: str, artefact_id: str) -> tuple[Artefact, AsyncIterator[bytes]]:
        row = await self.get_row(tenant_id, artefact_id)
        if row is None:
            raise StorageError("storage_not_found")
        return row, self.store.stream(row.object_key)

    async def delete_for_job(self, tenant_id: str, job_id: str) -> int:
        async with self._acquire(tenant_id) as conn:
            rows = await conn.fetch("SELECT id, object_key FROM dps_artefact WHERE job_id = $1", job_id)
            for r in rows:
                await self.store.delete(r["object_key"])
            await conn.execute("DELETE FROM dps_artefact WHERE job_id = $1", job_id)
        return len(rows)
```

- [ ] **Step 5: Run tests, lint, types**

Run: `docker compose --profile sources up -d minio minio-init`, then `pytest -q tests/test_storage_local.py tests/db/test_artefacts.py tests/connectors/test_storage_s3.py && ruff check dps tests tools && mypy dps`
Expected: `8 + 1 + 1 = 10 passed` (`test_keys_cannot_escape…` ×6), clean.

- [ ] **Step 6: Commit**

```bash
git add dps/storage.py docker-compose.yml HANDOFF.md tests/test_storage_local.py tests/connectors/test_storage_s3.py tests/db/test_artefacts.py tests/fixtures/sources.py
git commit -m "feat(storage): local and S3 object stores with atomic writes and multipart; artefact service; MinIO" -- dps/storage.py docker-compose.yml HANDOFF.md tests/test_storage_local.py tests/connectors/test_storage_s3.py tests/db/test_artefacts.py tests/fixtures/sources.py
```

---

### Task 12: Selection validation (`dps/layers/connectors/selection.py`) and `redact_spec`

**Files:**
- Create: `dps/layers/connectors/selection.py`
- Modify: `dps/jobs_models.py` (implement `redact_spec`)
- Test: `tests/connectors/test_selection.py`, `tests/test_redact_spec.py`

Public interface (contract §5.2; the functions marked are additions):
```python
from dps.jobs_models import SampleSpec, Selection   # re-exported (contract §5.2 places them here too)
ORACLE_IDENT, PG_IDENT, MONGO_PATH: re.Pattern[str]
MONGO_OPERATORS: frozenset[str]
class SelectionError(ValueError): code: str          # value-free message
def quote_ident(name: str, engine: Engine) -> str
def validate_selection(sel: Selection, engine: Engine, shape: TableShape | None) -> None
def selection_checks(sel: Selection, engine: Engine, shape: TableShape | None) -> list[dict[str, Any]]   # (added) console checklist
def parameterize_literals(sel: Selection, engine: Engine) -> Selection                                    # (added)
def compile_where(sel: Selection, engine: Engine) -> tuple[str, list[str]]
def compile_query(sel: Selection, engine: Engine) -> tuple[str, list[str]]                                # (added)
def where_template(text: str, engine: Engine) -> str                                                     # (added)
def query_template(text: str, engine: Engine) -> str                                                     # (added)
def validate_mongo_filter(flt: dict[str, Any]) -> None                                                   # (added)
def mongo_filter_template(flt: dict[str, Any]) -> dict[str, Any]                                         # (added)
def referenced_columns(sel: Selection, engine: Engine) -> set[str]                                        # (added)
def query_projects_keys(sel: Selection, engine: Engine, key: list[str]) -> bool                           # (added)
```

**How text becomes SQL.** Parse with `sqlglot` (`read="postgres"` or `"oracle"`; a `where` is parsed as `SELECT * FROM __dps_t WHERE <where>` — `*` rather than the contract's `1`, so the wrapper adds no literal that the binding pass would number); reject on the first violation; fold unquoted identifiers (lower for PG, upper for Oracle) and mark every identifier quoted; replace every literal with a bind (`:l1`, `:l2`, …; typed literals `DATE '…'` become `CAST(CAST(:l1 AS TEXT) AS DATE)` and intervals `CAST(CAST(:l2 AS TEXT) AS INTERVAL)`, so the driver only ever binds text for them; literals under `ORDER BY`/`GROUP BY` stay, they are positions); render with sentinel columns `__dps_bind_<name>` and swap them for `$n` (PG execution), `:name` (Oracle execution) or `:name` (templates). The API stores the **parameterised** selection (literal values in `params`, sealed into `spec_enc`) and only templates in `dps_job.spec`; `compile_where`/`compile_query` refuse a selection that still has literals (`selection_not_parameterized`). Comments are stripped. Error messages name the rule, never the SQL text.

Rejection codes: `selection_parse_error`, `selection_multiple_statements`, `selection_not_select`, `selection_dml`, `selection_into`, `selection_for_update`, `selection_function_denied`, `selection_identifier_invalid`, `selection_positional_param`, `selection_param_missing`, `selection_unknown_column`, `selection_query_missing_key`, `selection_not_parameterized`, `selection_operator_denied`, `selection_engine_mismatch`. The function deny-list is the contract's plus `set_config`, `pg_terminate_backend`, `pg_cancel_backend`, `pg_advisory*`, `pg_reload_conf`, `pg_rotate_logfile`, `pg_promote`, `pg_ls_dir`, `pg_stat_file`, `lo_import`, `lo_export` (`set_config` would let a `where` lift the statement timeout inside the read-only transaction).

- [ ] **Step 1: Write the failing tests** `tests/connectors/test_selection.py`

```python
from __future__ import annotations

import pytest

from dps.jobs_models import ObjectRef, Selection
from dps.layers.connectors.base import ColumnInfo, TableShape
from dps.layers.connectors.selection import (
    SelectionError, compile_query, compile_where, mongo_filter_template, parameterize_literals,
    query_projects_keys, query_template, quote_ident, referenced_columns, selection_checks,
    validate_mongo_filter, validate_selection, where_template,
)

SHAPE = TableShape(object=ObjectRef(owner="bank", name="customers"), key=["customer_id"], key_source="primary_key",
                   columns=[ColumnInfo(path=f"$.{n}", name=n, data_type="text", kind="text")
                            for n in ("customer_id", "created_at", "country_code", "full_name", "risk_score")])


def test_quote_ident_rules() -> None:
    assert quote_ident("created_at", "postgres") == '"created_at"'
    assert quote_ident("Created_At", "oracle") == '"CREATED_AT"'
    assert quote_ident("profile.contact.email", "mongo") == "profile.contact.email"
    for name, engine in (("bad name", "postgres"), ("1abc", "oracle"), ('x"y', "postgres"), ("a..b", "mongo")):
        with pytest.raises(SelectionError) as err:
            quote_ident(name, engine)  # type: ignore[arg-type]
        assert err.value.code == "selection_identifier_invalid"


def test_parameterize_then_compile_postgres() -> None:
    sel = parameterize_literals(Selection(
        where="CREATED_AT >= DATE '2025-01-01' AND country_code IN ('IN', 'US') AND risk_score > :min_risk "
              "AND created_at > now() - INTERVAL '30 days'", params={"min_risk": 500}), "postgres")
    assert {k: sel.params[k] for k in ("min_risk", "l1", "l2", "l3")} == {
        "min_risk": 500, "l1": "2025-01-01", "l2": "IN", "l3": "US"}
    assert str(sel.params["l4"]).lower() == "30 days"   # sqlglot may split the unit; PG accepts both
    assert "2025" not in sel.where and "'IN'" not in sel.where and ":l1" in sel.where
    fragment, names = compile_where(sel, "postgres")
    assert names == ["l1", "l2", "l3", "min_risk", "l4"]
    assert '"created_at" >= CAST(CAST($1 AS TEXT) AS DATE)' in fragment
    assert '"country_code" IN ($2, $3)' in fragment and '"risk_score" > $4' in fragment
    assert "CAST(CAST($5 AS TEXT) AS INTERVAL)" in fragment


def test_repeated_bind_reuses_its_position() -> None:
    fragment, names = compile_where(Selection(where="a = :x OR b = :x", params={"x": 1}), "postgres")
    assert names == ["x"] and fragment.count("$1") == 2


def test_oracle_rendering_uses_named_binds_and_upper_identifiers() -> None:
    sel = parameterize_literals(Selection(where="created_at >= DATE '2025-01-01'"), "oracle")
    fragment, names = compile_where(sel, "oracle")
    assert names == ["l1"] and '"CREATED_AT"' in fragment and ":l1" in fragment


def test_unparameterized_literals_are_refused_at_compile() -> None:
    with pytest.raises(SelectionError) as err:
        compile_where(Selection(where="full_name = 'Ananya'"), "postgres")
    assert err.value.code == "selection_not_parameterized" and "Ananya" not in str(err.value)


@pytest.mark.parametrize("sel,code", [
    (Selection(where="1 = 1; DROP TABLE x"), "selection_multiple_statements"),
    (Selection(query="INSERT INTO t VALUES (1)"), "selection_not_select"),
    (Selection(query="SELECT * INTO new_t FROM bank.customers"), "selection_into"),
    (Selection(query="SELECT * FROM bank.customers FOR UPDATE"), "selection_for_update"),
    (Selection(query="WITH x AS (DELETE FROM t RETURNING *) SELECT * FROM x"), "selection_dml"),
    (Selection(where="pg_sleep(10) IS NOT NULL"), "selection_function_denied"),
    (Selection(where="set_config('statement_timeout', '0', false) = '0'"), "selection_function_denied"),
    (Selection(where="a = $1"), "selection_positional_param"),
    (Selection(where="full_name = :who"), "selection_param_missing"),
    (Selection(where="nonexistent = 1"), "selection_unknown_column"),
    (Selection(where="full_name = (SELECT"), "selection_parse_error"),
    (Selection(query="SELECT full_name FROM bank.customers"), "selection_query_missing_key"),
])
def test_rejections(sel: Selection, code: str) -> None:
    with pytest.raises(SelectionError) as err:
        validate_selection(sel, "postgres", SHAPE)
    assert err.value.code == code


def test_oracle_package_calls_are_denied() -> None:
    with pytest.raises(SelectionError) as err:
        validate_selection(Selection(where="dbms_lock.sleep(5) = 1"), "oracle", None)
    assert err.value.code == "selection_function_denied"


def test_query_compile_template_and_keys() -> None:
    sel = parameterize_literals(Selection(
        query="SELECT c.customer_id, c.full_name FROM bank.customers c WHERE c.country_code = 'IN'"), "postgres")
    sql, names = compile_query(sel, "postgres")
    assert names == ["l1"] and "$1" in sql and "'IN'" not in sql
    assert query_projects_keys(sel, "postgres", ["customer_id"])
    assert "'IN'" not in query_template("SELECT customer_id FROM t WHERE x = 'IN'", "postgres")


def test_templates_and_referenced_columns_are_value_free() -> None:
    template = where_template("full_name = 'Ananya Krishnan' AND risk_score > 700", "postgres")
    assert "Ananya" not in template and "700" not in template and ":l1" in template and ":l2" in template
    assert referenced_columns(Selection(where="FULL_NAME = :a AND risk_score > :b", params={"a": 1, "b": 2}),
                              "postgres") == {"full_name", "risk_score"}


def test_selection_checks_for_the_console() -> None:
    sel = parameterize_literals(Selection(where="created_at >= DATE '2025-01-01' AND country_code IN ('IN','US')"),
                                "postgres")
    checks = {c["name"]: c for c in selection_checks(sel, "postgres", SHAPE)}
    assert all(c["ok"] for c in checks.values())
    assert checks["literals_bound"]["count"] == 3 and checks["identifiers_validated"]["count"] == 2
    bad = {c["name"]: c for c in selection_checks(Selection(query="SELECT * FROM t FOR UPDATE"), "postgres", SHAPE)}
    assert bad["no_into_lock_procs"]["ok"] is False


def test_mongo_filter_allow_list_and_template() -> None:
    flt = {"$and": [{"kyc.status": {"$in": ["PENDING", "REVIEW"]}}, {"age": {"$gte": 18}},
                    {"accounts": {"$elemMatch": {"iban": {"$exists": True}}}}]}
    validate_mongo_filter(flt)
    assert mongo_filter_template(flt) == {"$and": [{"kyc.status": {"$in": ["?", "?"]}}, {"age": {"$gte": "?"}},
                                                   {"accounts": {"$elemMatch": {"iban": {"$exists": "?"}}}}]}
    for bad in ({"$where": "sleep(1)"}, {"a": {"$expr": {}}}, {"a": {"$function": {}}}, {"bad key": 1}):
        with pytest.raises(SelectionError) as err:
            validate_mongo_filter(bad)
        assert err.value.code in ("selection_operator_denied", "selection_identifier_invalid")
```

`tests/test_redact_spec.py`:
```python
from __future__ import annotations

import json

from dps.engine.transform.crypto import generate_key_material
from dps.jobs_models import DiscoverSpec, ObjectRef, RunSpec, Selection, redact_spec
from dps.layers.connectors.selection import parameterize_literals


def test_run_spec_redaction_keeps_no_values_or_keys() -> None:
    km = generate_key_material("key").model_dump(mode="json")
    sel = parameterize_literals(Selection(where="full_name = 'Ananya Krishnan'"), "postgres")
    spec = RunSpec.model_validate({
        "mode": "obfuscate", "snapshot_id": "snap_1",
        "source": {"connection_id": "c_1", "object": {"owner": "bank", "name": "customers"},
                   "selection": sel.model_dump()},
        "target": {"write_mode": "table", "connection_id": "c_2", "object": {"owner": "bank_uat", "name": "customers"}},
        "consistency": {"key_material": km, "synthesis_seed": {"seed": "AAAA" * 11}},
        "purpose": {"ticket": "CHG-1", "legal_basis": "legitimate interest"}})
    red = redact_spec(spec, engine="postgres")
    text = json.dumps(red)
    assert "Ananya" not in text and km["dek"] not in text and "AAAAAAAA" not in text
    assert red["source"]["selection"]["params"] == {"l1": "<bound>"}
    assert red["consistency"]["kid"] == km["kid"] and red["consistency"]["caller_key"] is True
    assert len(red["consistency"]["seed_fingerprint"]) == 16


def test_discover_spec_redaction() -> None:
    sel = parameterize_literals(Selection(where="country_code = 'MX'"), "postgres")
    red = redact_spec(DiscoverSpec(connection_id="c", objects=[ObjectRef(owner="bank", name="t")], selection=sel),
                      engine="postgres")
    assert "MX" not in json.dumps(red) and red["selection"]["where"] == '"country_code" = :l1'
```

- [ ] **Step 2: Run to confirm failure**

Run: `pytest -q tests/connectors/test_selection.py tests/test_redact_spec.py`
Expected: FAIL with `ModuleNotFoundError` / `NotImplementedError`.

- [ ] **Step 3: Write `dps/layers/connectors/selection.py`**

```python
"""Selection ("control statement") validation and compilation with sqlglot (contract §5.2)."""
from __future__ import annotations

import re
from typing import Any

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError, TokenError
from sqlglot.tokens import TokenType

from dps.jobs_models import SampleSpec, Selection  # noqa: F401 - re-exported
from dps.layers.connectors.base import Engine, JsonScalar, TableShape

ORACLE_IDENT = re.compile(r"^[A-Za-z][A-Za-z0-9_$#]{0,127}$")
PG_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]{0,62}$")
MONGO_PATH = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")
MONGO_OPERATORS = frozenset({"$eq", "$ne", "$gt", "$gte", "$lt", "$lte", "$in", "$nin", "$and", "$or", "$nor",
                             "$not", "$exists", "$type", "$size", "$all", "$elemMatch", "$regex"})
_BIND_NAME = re.compile(r"^[a-z_][a-z0-9_]{0,30}$")
_DENY_PREFIXES = ("pg_sleep", "pg_read_file", "pg_read_binary_file", "pg_ls_dir", "pg_stat_file", "lo_",
                  "dblink", "dbms_", "utl_", "xmltype", "httpuritype", "set_config", "pg_terminate_backend",
                  "pg_cancel_backend", "pg_advisory", "pg_reload_conf", "pg_rotate_logfile", "pg_promote")
_DENY_SCHEMAS = frozenset({"sys", "dbms_lock", "dbms_pipe", "dbms_scheduler", "utl_http", "utl_file"})
_SENTINEL = "__dps_bind_"
_SENTINEL_RE = re.compile(r'"?__dps_bind_([A-Za-z0-9_]+)"?', re.IGNORECASE)
_DIALECT = {"postgres": "postgres", "oracle": "oracle"}


def _node_types(*names: str) -> tuple[type[exp.Expression], ...]:
    found = [getattr(exp, n, None) for n in names]
    return tuple(t for t in found if isinstance(t, type))


_DML = _node_types("Insert", "Update", "Delete", "Merge", "Create", "Drop", "Alter", "AlterTable",
                   "TruncateTable", "Command", "Copy", "Set", "Use", "Transaction", "Commit", "Rollback",
                   "Grant", "Revoke", "LoadData", "Pragma", "Analyze", "Describe")
_INTO = _node_types("Into")
_LOCK = _node_types("Lock")


class SelectionError(ValueError):
    """A selection that DPS will not run; ``code`` is stable, the message never quotes the SQL."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code


def quote_ident(name: str, engine: Engine) -> str:
    """Validate and quote one identifier (Oracle upper-cases, the Wega rule; PG keeps case)."""
    if engine == "oracle":
        if not ORACLE_IDENT.fullmatch(name):
            raise SelectionError("selection_identifier_invalid", "not a valid Oracle identifier")
        return f'"{name.upper()}"'
    if engine == "postgres":
        if not PG_IDENT.fullmatch(name):
            raise SelectionError("selection_identifier_invalid", "not a valid PostgreSQL identifier")
        return f'"{name}"'
    if not MONGO_PATH.fullmatch(name):
        raise SelectionError("selection_identifier_invalid", "not a valid MongoDB field path")
    return name


def _walk(node: exp.Expression) -> list[exp.Expression]:
    out: list[exp.Expression] = []
    for item in node.walk():
        out.append(item[0] if isinstance(item, tuple) else item)   # older sqlglot yields tuples
    return out


def _parse(text: str, engine: Engine, *, wrap_where: bool) -> exp.Select:
    if engine not in _DIALECT:
        raise SelectionError("selection_engine_mismatch", "SQL selections apply to SQL engines only")
    # SELECT * (not the contract's "SELECT 1"): the wrapper must contain no literal of its own
    sql = f"SELECT * FROM __dps_t WHERE {text}" if wrap_where else text
    try:
        tokens = sqlglot.tokenize(sql, read=_DIALECT[engine])
    except TokenError:
        raise SelectionError("selection_parse_error", "the statement could not be tokenised") from None
    if any(t.token_type == TokenType.SEMICOLON for t in tokens):
        raise SelectionError("selection_multiple_statements", "only one statement is allowed; remove ';'")
    try:
        parsed = [s for s in sqlglot.parse(sql, read=_DIALECT[engine]) if s is not None]
    except ParseError:
        raise SelectionError("selection_parse_error", "the statement could not be parsed") from None
    if len(parsed) != 1:
        raise SelectionError("selection_multiple_statements", "only one statement is allowed")
    stmt = parsed[0]
    if not isinstance(stmt, exp.Select):
        raise SelectionError("selection_not_select", "the statement must be a single SELECT")
    for node in _walk(stmt):
        node.comments = None
        if _DML and isinstance(node, _DML):          # e.g. a data-modifying CTE under the SELECT
            raise SelectionError("selection_dml", "only SELECT is allowed (no DML, DDL or commands)")
    return stmt


def _validate_tree(stmt: exp.Expression, engine: Engine, params: dict[str, JsonScalar]) -> dict[str, int]:
    counts = {"identifiers": 0, "literals": 0, "placeholders": 0}
    ident_re = ORACLE_IDENT if engine == "oracle" else PG_IDENT
    for node in _walk(stmt):
        if _INTO and isinstance(node, _INTO):
            raise SelectionError("selection_into", "SELECT … INTO is not allowed")
        if _LOCK and isinstance(node, _LOCK):
            raise SelectionError("selection_for_update", "FOR UPDATE/SHARE is not allowed: DPS never locks source rows")
        if isinstance(node, exp.Parameter):
            raise SelectionError("selection_positional_param", "use named binds (:name), not positional ones")
        if isinstance(node, exp.Func):
            name = (node.name if isinstance(node, exp.Anonymous) else node.sql_name()).lower()
            if name.startswith(_DENY_PREFIXES):
                raise SelectionError("selection_function_denied", "a denied function is used")
        if isinstance(node, exp.Placeholder):
            name = str(node.name or "")
            if not _BIND_NAME.fullmatch(name):
                raise SelectionError("selection_positional_param", "binds must be named :name")
            if name not in params:
                raise SelectionError("selection_param_missing", "a named bind has no value in params")
            counts["placeholders"] += 1
        if isinstance(node, exp.Literal):
            counts["literals"] += 1
        if isinstance(node, exp.Identifier):
            text = node.this if isinstance(node.this, str) else str(node.this)
            if text.lower().startswith(_SENTINEL) or text == "__dps_t":
                continue
            if text.lower() in _DENY_SCHEMAS or text.lower().startswith(("dbms_", "utl_")):
                raise SelectionError("selection_function_denied", "a denied package or schema is referenced")
            if not ident_re.fullmatch(text):
                raise SelectionError("selection_identifier_invalid", "an identifier is not valid")
            if isinstance(node.parent, exp.Column):
                counts["identifiers"] += 1
    return counts


def _fold(stmt: exp.Expression, engine: Engine) -> None:
    for node in _walk(stmt):
        if isinstance(node, exp.Identifier):
            text = str(node.this)
            if text.lower().startswith(_SENTINEL) or text == "__dps_t":
                continue
            if not node.args.get("quoted"):
                node.set("this", text.upper() if engine == "oracle" else text.lower())
            node.set("quoted", True)


def _sentinel(name: str) -> exp.Column:
    return exp.column(f"{_SENTINEL}{name}")


def _number(text: str) -> JsonScalar:
    return int(text) if re.fullmatch(r"-?\d+", text) else text      # decimals stay text; the driver casts


def _bind_literals(stmt: exp.Expression, taken: set[str]) -> tuple[exp.Expression, dict[str, JsonScalar]]:
    values: dict[str, JsonScalar] = {}
    counter = 0

    def fresh() -> str:
        nonlocal counter
        while True:
            counter += 1
            name = f"l{counter}"
            if name not in taken:
                return name

    def transform(node: exp.Expression) -> exp.Expression:
        if isinstance(node, exp.Interval) and isinstance(node.this, exp.Literal):
            name = fresh()
            unit = node.args.get("unit")
            values[name] = f"{node.this.this} {unit.name if unit is not None else ''}".strip()
            return exp.Cast(this=exp.Cast(this=_sentinel(name), to=exp.DataType.build("text")),
                            to=exp.DataType.build("interval"))
        if isinstance(node, exp.Literal):
            if isinstance(node.parent, (exp.Ordered, exp.Group)):
                return node
            name = fresh()
            values[name] = node.this if node.is_string else _number(str(node.this))
            if isinstance(node.parent, exp.Cast) and node.parent.this is node:
                return exp.Cast(this=_sentinel(name), to=exp.DataType.build("text"))
            return _sentinel(name)
        if isinstance(node, exp.Placeholder):
            return _sentinel(str(node.name))
        return node

    return stmt.transform(transform), values


def _render(node: exp.Expression, engine: Engine, *, style: str, order: list[str]) -> str:
    sql = node.sql(dialect=_DIALECT[engine])

    def repl(match: re.Match[str]) -> str:
        name = match.group(1).lower()
        if style == "template" or engine == "oracle":
            return f":{name}"
        if name not in order:
            order.append(name)
        return f"${order.index(name) + 1}"

    return _SENTINEL_RE.sub(repl, sql)


def _where_expr(stmt: exp.Select) -> exp.Expression:
    where = stmt.args.get("where")
    assert where is not None
    return where.this


def parameterize_literals(sel: Selection, engine: Engine) -> Selection:
    """Move every literal into ``params`` (``l1``, ``l2``…); the text keeps only ``:name`` binds."""
    if engine == "mongo":
        return sel
    taken = set(sel.params)
    update: dict[str, Any] = {}
    params = dict(sel.params)
    for field, wrap in (("where", True), ("query", False)):
        text = getattr(sel, field)
        if text is None:
            continue
        stmt = _parse(text, engine, wrap_where=wrap)
        _validate_tree(stmt, engine, params)
        _fold(stmt, engine)
        new, values = _bind_literals(stmt, taken)
        params.update(values)
        target = _where_expr(new) if wrap else new   # type: ignore[arg-type]
        update[field] = _render(target, engine, style="template", order=[])
    update["params"] = params
    return sel.model_copy(update=update)


def _compile(text: str, engine: Engine, params: dict[str, JsonScalar], *, wrap: bool) -> tuple[str, list[str]]:
    stmt = _parse(text, engine, wrap_where=wrap)
    counts = _validate_tree(stmt, engine, params)
    if counts["literals"] and any(not isinstance(n.parent, (exp.Ordered, exp.Group))
                                  for n in _walk(stmt) if isinstance(n, exp.Literal)):
        raise SelectionError("selection_not_parameterized", "literals must be bound before compiling")
    _fold(stmt, engine)
    new, _ = _bind_literals(stmt, set(params))
    order: list[str] = []
    target = _where_expr(new) if wrap else new   # type: ignore[arg-type]
    sql = _render(target, engine, style="exec", order=order)
    if engine == "oracle":
        order = sorted({m.group(1).lower() for m in _SENTINEL_RE.finditer(target.sql(dialect="oracle"))},
                       key=lambda n: target.sql(dialect="oracle").lower().find(f"{_SENTINEL}{n}"))
    return sql, order


def compile_where(sel: Selection, engine: Engine) -> tuple[str, list[str]]:
    """``(fragment, ordered bind names)``; ``("", [])`` without a where."""
    return ("", []) if sel.where is None else _compile(sel.where, engine, sel.params, wrap=True)


def compile_query(sel: Selection, engine: Engine) -> tuple[str, list[str]]:
    """Full read-only SELECT with binds; ``("", [])`` without a query."""
    return ("", []) if sel.query is None else _compile(sel.query, engine, sel.params, wrap=False)


def where_template(text: str, engine: Engine) -> str:
    """Value-free form of a where clause (literals → ``:lN``) for ``dps_job.spec`` and evidence."""
    stmt = _parse(text, engine, wrap_where=True)
    _fold(stmt, engine)
    new, _ = _bind_literals(stmt, set())
    return _render(_where_expr(new), engine, style="template", order=[])  # type: ignore[arg-type]


def query_template(text: str, engine: Engine) -> str:
    """Value-free form of a query."""
    stmt = _parse(text, engine, wrap_where=False)
    _fold(stmt, engine)
    new, _ = _bind_literals(stmt, set())
    return _render(new, engine, style="template", order=[])


def referenced_columns(sel: Selection, engine: Engine) -> set[str]:
    """Folded column names a where clause references (sentinels excluded)."""
    if sel.where is None:
        return set()
    stmt = _parse(sel.where, engine, wrap_where=True)
    _fold(stmt, engine)
    return {c.name for c in _walk(_where_expr(stmt)) if isinstance(c, exp.Column)
            and not c.name.lower().startswith(_SENTINEL)}


def query_projects_keys(sel: Selection, engine: Engine, key: list[str]) -> bool:
    """True when the query's projection includes ``*`` or every key column."""
    if sel.query is None:
        return True
    stmt = _parse(sel.query, engine, wrap_where=False)
    _fold(stmt, engine)
    names: set[str] = set()
    for proj in stmt.expressions:
        if isinstance(proj, exp.Star) or (isinstance(proj, exp.Column) and isinstance(proj.this, exp.Star)):
            return True
        names.add(proj.alias_or_name)
    folded = {k.upper() if engine == "oracle" else k for k in key}
    return folded <= names


def validate_selection(sel: Selection, engine: Engine, shape: TableShape | None) -> None:
    """Raise the first :class:`SelectionError`; ``shape`` (when given) enables column/key checks."""
    if engine == "mongo":
        if sel.where is not None or sel.query is not None:
            raise SelectionError("selection_engine_mismatch", "MongoDB selections use mongo_filter")
        if sel.mongo_filter is not None:
            validate_mongo_filter(sel.mongo_filter)
        return
    if sel.mongo_filter is not None:
        raise SelectionError("selection_engine_mismatch", "mongo_filter applies to MongoDB only")
    for field, wrap in (("where", True), ("query", False)):
        text = getattr(sel, field)
        if text is not None:
            _validate_tree(_parse(text, engine, wrap_where=wrap), engine, sel.params)
    if shape is not None:
        known = {c.name.upper() if engine == "oracle" else c.name for c in shape.columns}
        if referenced_columns(sel, engine) - known:
            raise SelectionError("selection_unknown_column", "the where clause names a column the object does not have")
        for name in sel.columns or []:
            if name not in known:
                raise SelectionError("selection_unknown_column", "a projected column does not exist")
        if not query_projects_keys(sel, engine, shape.key):
            raise SelectionError("selection_query_missing_key", "the query must project every key column")


_CHECKS = (("one_statement", ("selection_multiple_statements", "selection_parse_error")),
           ("select_only", ("selection_not_select", "selection_dml")),
           ("no_into_lock_procs", ("selection_into", "selection_for_update", "selection_function_denied",
                                   "selection_positional_param")),
           ("identifiers_validated", ("selection_identifier_invalid", "selection_unknown_column")),
           ("literals_bound", ("selection_param_missing", "selection_not_parameterized")),
           ("keyset_able", ("selection_query_missing_key",)))


def selection_checks(sel: Selection, engine: Engine, shape: TableShape | None) -> list[dict[str, Any]]:
    """The wizard's checklist (mockup 06 step 2): every check ok, or the failing one named."""
    failed: str | None = None
    try:
        validate_selection(sel, engine, shape)
    except SelectionError as exc:
        failed = exc.code
    counts = {"identifiers": 0, "literals": 0, "placeholders": 0}
    if failed is None and engine != "mongo":
        for field, wrap in (("where", True), ("query", False)):
            text = getattr(sel, field)
            if text is not None:
                c = _validate_tree(_parse(text, engine, wrap_where=wrap), engine, sel.params)
                counts = {k: counts[k] + c[k] for k in counts}
    out: list[dict[str, Any]] = []
    for name, codes in _CHECKS:
        entry: dict[str, Any] = {"name": name, "ok": failed not in codes}
        if not entry["ok"]:
            entry["code"] = failed
        if name == "identifiers_validated":
            entry["count"] = counts["identifiers"]
        if name == "literals_bound":
            entry["count"] = counts["placeholders"]
        out.append(entry)
    return out


def validate_mongo_filter(flt: dict[str, Any], _depth: int = 0) -> None:
    """Operator allow-list (``$where``, ``$expr``, ``$function`` … refused) and field-path rules."""
    if _depth > 20:
        raise SelectionError("selection_operator_denied", "filter nesting is too deep")
    for key, value in flt.items():
        if key.startswith("$"):
            if key not in MONGO_OPERATORS:
                raise SelectionError("selection_operator_denied", "a MongoDB operator outside the allow-list is used")
        elif not MONGO_PATH.fullmatch(key):
            raise SelectionError("selection_identifier_invalid", "not a valid MongoDB field path")
        for item in value if isinstance(value, list) else [value]:
            if isinstance(item, dict):
                validate_mongo_filter(item, _depth + 1)


def mongo_filter_template(flt: dict[str, Any]) -> dict[str, Any]:
    """Every scalar value replaced by ``"?"`` (structure and operators kept)."""
    def walk(value: Any) -> Any:
        if isinstance(value, dict):
            return {k: walk(v) for k, v in value.items()}
        if isinstance(value, list):
            return [walk(v) for v in value]
        return "?"
    return {k: walk(v) for k, v in flt.items()}
```

Note on the Oracle bind order: `oracledb` binds by name, so `order` for Oracle is simply "names in first-appearance order" (used only for documentation and tests); PostgreSQL's `$n` positions come from the render pass.

- [ ] **Step 4: Implement `redact_spec`** in `dps/jobs_models.py` (replace the stub)

```python
def redact_spec(spec: RunSpec | DiscoverSpec, *, engine: Engine) -> dict[str, Any]:
    """Value-free copy for ``dps_job.spec`` (contract §3.3): selection templates, bound-param markers,
    and consistency identifiers only (``kid``, seed fingerprint) — never key material or seeds."""
    from dps.layers.connectors.selection import mongo_filter_template, query_template, where_template
    from dps.secrets import seed_fingerprint

    data = spec.model_dump(mode="json")
    sel = (data["source"] if isinstance(spec, RunSpec) else data)["selection"]
    if sel.get("where"):
        sel["where"] = where_template(sel["where"], engine)
    if sel.get("query"):
        sel["query"] = query_template(sel["query"], engine)
    if sel.get("mongo_filter") is not None:
        sel["mongo_filter"] = mongo_filter_template(sel["mongo_filter"])
    sel["params"] = {name: "<bound>" for name in sel.get("params") or {}}
    if isinstance(spec, RunSpec):
        cons = data["consistency"]
        km = cons.pop("key_material", None)
        seed = cons.pop("synthesis_seed", None)
        wrap = cons.pop("wrap_key", None)
        cons["kid"] = km.get("kid") if km else None
        cons["caller_key"] = km is not None
        cons["seed_fingerprint"] = seed_fingerprint(seed["seed"]) if seed and seed.get("seed") else None
        cons["wrapped"] = wrap is not None
    return data
```

- [ ] **Step 5: Run tests, lint, types**

Run: `pytest -q tests/connectors/test_selection.py tests/test_redact_spec.py && ruff check dps tests tools && mypy dps`
Expected: `22 + 2 = 24 passed` (`test_rejections` ×12), clean. sqlglot node class names drift between versions: if a rejection test fails because a node class was renamed (for example `AlterTable` → `Alter`), add the new name to `_node_types(...)` — never weaken the test. If the installed sqlglot's postgres dialect does not parse `:name` placeholders (the parse-error tests then fail on valid selections), parse with `read="postgres"` after rewriting `:name` to the sentinel column text yourself (a regex over the token stream outside string literals, using `sqlglot.tokenize`) — the rest of the pipeline is unchanged. Record the installed sqlglot version in HANDOFF.

- [ ] **Step 6: Commit**

```bash
git add dps/layers/connectors/selection.py dps/jobs_models.py tests/connectors/test_selection.py tests/test_redact_spec.py
git commit -m "feat(connectors): sqlglot selection validation, literal binding, templates and value-free spec redaction" -- dps/layers/connectors/selection.py dps/jobs_models.py tests/connectors/test_selection.py tests/test_redact_spec.py
```

---

### Task 13: Partition planning (`dps/layers/connectors/partitions.py`)

**Files:**
- Create: `dps/layers/connectors/partitions.py`
- Test: `tests/connectors/test_partitions.py`

Partition boundaries are chosen **by position in a key sample that the database ordered** — Python never compares two keys, so the ranges always agree with the database collation that `ORDER BY` and the keyset predicates use. The connector's `sample_keys(…, upper=…)` (Task 15) only returns keys below the global upper bound, so every boundary is below it.

Public interface:
```python
def partition_count(rows: int, *, partition_rows: int, max_partitions: int) -> int   # 0 rows → 0
def plan_partitions(sample: list[tuple[Any, ...]], *, count: int, columns: list[ColumnInfo],
                    upper: tuple[Any, ...] | None = None) -> list[KeyRange]
```

- [ ] **Step 1: Write the failing tests**

```python
from __future__ import annotations

from dps.layers.connectors.base import ColumnInfo, decode_key
from dps.layers.connectors.partitions import partition_count, plan_partitions

INT = [ColumnInfo(path="$.id", name="id", data_type="bigint", kind="int")]
PAIR = INT + [ColumnInfo(path="$.n", name="n", data_type="int", kind="int")]


def test_partition_count_bounds() -> None:
    assert partition_count(0, partition_rows=50_000, max_partitions=256) == 0
    assert partition_count(1, partition_rows=50_000, max_partitions=256) == 1
    assert partition_count(1_948_210, partition_rows=50_000, max_partitions=256) == 39
    assert partition_count(10**9, partition_rows=50_000, max_partitions=256) == 256


def test_ranges_are_contiguous_open_ended_and_ordered_by_sample_position() -> None:
    sample = [(10_000_000 + i,) for i in range(0, 1000, 10)]          # already DB-ordered
    ranges = plan_partitions(sample, count=4, columns=INT)
    assert len(ranges) == 4 and ranges[0].lower is None and ranges[-1].upper is None
    for a, b in zip(ranges, ranges[1:], strict=False):
        assert a.upper == b.lower
    assert [decode_key(r.lower, INT)[0] for r in ranges[1:]] == [10_000_250, 10_000_500, 10_000_750]


def test_duplicate_boundaries_collapse_and_upper_applies_to_last_range() -> None:
    sample = [(1,)] * 50 + [(2,)] * 50
    ranges = plan_partitions(sample, count=4, columns=INT, upper=(3,))
    assert len(ranges) == 2 and decode_key(ranges[-1].upper, INT) == (3,)


def test_composite_keys_and_tiny_samples() -> None:
    sample = [(1, 1), (1, 2), (2, 1), (2, 2)]
    ranges = plan_partitions(sample, count=2, columns=PAIR)
    assert decode_key(ranges[1].lower, PAIR) == (2, 1)
    assert len(plan_partitions([(5,)], count=8, columns=INT)) == 1
    assert len(plan_partitions([], count=1, columns=INT, upper=(9,))) == 1
```

- [ ] **Step 2: Run to confirm failure**, then **Step 3: write the module**

```python
"""Key-space partitioning (spec §7 "Execution", contract §4).

Boundaries are taken by position from a key sample the database returned in ``ORDER BY key`` order;
equal neighbouring boundaries collapse (comparison by equality only). Ranges are ``[lower, upper)``,
the first open below, the last bounded by the global ``upper`` (max_rows / same-table append) or open.
"""
from __future__ import annotations

import math
from typing import Any

from dps.layers.connectors.base import ColumnInfo, KeyRange, encode_key


def partition_count(rows: int, *, partition_rows: int, max_partitions: int) -> int:
    """Partitions for ``rows`` selected rows: ceil(rows / partition_rows) clamped to [1, max]; 0 for none."""
    if rows <= 0:
        return 0
    return max(1, min(max_partitions, math.ceil(rows / max(1, partition_rows))))


def plan_partitions(sample: list[tuple[Any, ...]], *, count: int, columns: list[ColumnInfo],
                    upper: tuple[Any, ...] | None = None) -> list[KeyRange]:
    """Contiguous ranges from a DB-ordered key sample (all sample keys are below ``upper``)."""
    top = encode_key(upper, columns) if upper is not None else None
    if count <= 1 or len(sample) < 2:
        return [KeyRange(lower=None, upper=top)]
    bounds: list[tuple[Any, ...]] = []
    for i in range(1, count):
        candidate = sample[(i * len(sample)) // count]
        if candidate != sample[0] and (not bounds or candidate != bounds[-1]):
            bounds.append(candidate)
    encoded = [encode_key(b, columns) for b in bounds]
    lowers: list[list[dict[str, str]] | None] = [None, *encoded]
    uppers: list[list[dict[str, str]] | None] = [*encoded, top]
    return [KeyRange(lower=lo, upper=hi) for lo, hi in zip(lowers, uppers, strict=True)]
```

- [ ] **Step 4: Run tests, lint, types**

Run: `pytest -q tests/connectors/test_partitions.py && ruff check dps tests tools && mypy dps`
Expected: `4 passed`, clean. (Coverage against a real table — union of ranges = every row, no overlap — is in the conformance suite, Task 26.)

- [ ] **Step 5: Commit**

```bash
git add dps/layers/connectors/partitions.py tests/connectors/test_partitions.py
git commit -m "feat(connectors): partition planning from DB-ordered key samples" -- dps/layers/connectors/partitions.py tests/connectors/test_partitions.py
```

---

### Task 14: Connection registry (`dps/connections.py`) and connector registry (`registry.py`)

**Files:**
- Create: `dps/connections.py`, `dps/layers/connectors/registry.py`
- Modify: `dps/storage.py` (add `target_store`), `dps/secrets.py` (`SECRET_FILES` += `"ca.pem"`)
- Test: `tests/db/test_connections.py`, `tests/test_registry.py`

Public interface (contract §5.1, §1; the module `dps/connections.py`, `ConnectionPatch`, `Credentials`, `ConnectionStoreError`, `load_credentials`, `record_test`, `touch_tenant`, `open_target_store` are additions — the contract fixes the table and the spec but not where the repository lives):
```python
class Credentials(BaseModel): username: str = ""; password: SecretStr = SecretStr(""); extra: dict[str, SecretStr] = {}
class Connection(BaseModel):
    id: str; tenant_id: str; name: str; engine: ConnEngine; params: ConnectionParams
    secret_ref: str | None; has_secret: bool; secret_expires_at: datetime | None
    roles: list[Literal["source", "target"]]; tags: list[str]; capabilities: dict[str, bool]
    last_test: ConnectionTestResult | None; created_by: str | None; created_at: datetime; updated_at: datetime
    @property
    def production(self) -> bool
class ConnectionPatch(BaseModel): name, params, secret_ref, credentials, roles, tags — all optional
class ConnectionStoreError(Exception): code: str; status: int
async def create_connection(tenant_id: str, spec: ConnectionSpec, *, settings: Settings, kek: bytes | None,
                            created_by: str | None) -> Connection
async def get_connection(tenant_id: str, connection_id: str, *, conn: asyncpg.Connection | None = None) -> Connection | None
async def list_connections(tenant_id: str, *, limit: int = 50, cursor: str | None = None) -> tuple[list[Connection], str | None]
async def update_connection(tenant_id: str, connection_id: str, patch: ConnectionPatch, *, settings: Settings,
                            kek: bytes | None) -> Connection | None
async def delete_connection(tenant_id: str, connection_id: str) -> bool
async def record_test(tenant_id: str, connection_id: str, result: ConnectionTestResult) -> None
async def load_credentials(c: Connection, *, settings: Settings, kek: bytes | None,
                           conn: asyncpg.Connection | None = None) -> Credentials
async def touch_tenant(tenant_id: str, *, conn: asyncpg.Connection | None = None) -> None
# dps/storage.py
def target_store(conn: Connection, settings: Settings, credentials: Credentials | None = None) -> ObjectStore
# dps/layers/connectors/registry.py
async def open_connector(conn: Connection, settings: Settings, *, action: str, kek: bytes | None,
                         job_id: str | None = None, credentials: Credentials | None = None) -> Connector
async def open_target_store(conn: Connection, settings: Settings, *, kek: bytes | None) -> ObjectStore
async def test_store(store: ObjectStore, conn: Connection) -> ConnectionTestResult
```
Rules: inline credentials are refused unless `inline_secrets_allowed` (`inline_secrets_disabled`, 422) and need the KEK (`kek_unconfigured`, 503); they are sealed into `secret_enc` with AAD `dps_connection.secret:<id>` and `secret_expires_at = now() + inline_secret_ttl_hours`; a duplicate name is `connection_name_taken` (409); deleting a connection referenced by a non-terminal job is `connection_in_use` (409); an expired/purged inline secret raises `SecretUnavailable`; `pvc` base paths must resolve under `DPS_EXPORT_ROOT` (`target_invalid`). Every repository function takes an optional `conn` so workers can call it on `acquire_queue(tenant_id)`; without one it uses `db.acquire(tenant_id)`. Creating a connection inserts the tenant's `dps_tenant` row if missing (so retention can enumerate tenants). `open_connector` returns the PostgreSQL connector (Task 15) and raises `ConnectorError("engine_unavailable", retryable=False)` for `oracle`/`mongo` (M3 replaces that branch) and `ConnectorError("engine_not_tabular")` for `s3`/`pvc`.

- [ ] **Step 1: Write the failing tests**

`tests/db/test_connections.py`:
```python
from __future__ import annotations

import pytest

from dps import db, jobs
from dps.connections import (
    ConnectionPatch, ConnectionStoreError, create_connection, delete_connection, get_connection,
    list_connections, load_credentials, record_test, update_connection,
)
from dps.jobs import JobKind, JobPhase
from dps.jobs_models import ConnectionSpec
from dps.layers.connectors.base import ConnectionTestResult
from dps.secrets import SecretUnavailable

pytestmark = pytest.mark.db
PG = {"host": "source-pg", "port": 5432, "database": "bank"}


async def test_secret_ref_and_inline_round_trip(migrated_db, kek, tmp_path) -> None:  # noqa: ANN001
    d = tmp_path / "dps" / "src" / "bank-pg-ro"
    d.mkdir(parents=True)
    (d / "username").write_text("dps_reader")
    (d / "password").write_text("dps_reader")
    settings = migrated_db.model_copy(update={"secret_dir": str(tmp_path)})
    ref = await create_connection("t1", ConnectionSpec(name="bank-pg-ro", engine="postgres", params=PG,
                                                       secret_ref="dps/src/bank-pg-ro", tags=["production"]),
                                  settings=settings, kek=kek, created_by="k1")
    assert ref.production and ref.has_secret is False and ref.secret_ref == "dps/src/bank-pg-ro"
    assert (await load_credentials(ref, settings=settings, kek=kek)).username == "dps_reader"
    inline = await create_connection("t1", ConnectionSpec(name="dev-pg", engine="postgres", params=PG,
                                                          credentials={"username": "u", "password": "s3cret-pw"}),
                                     settings=settings, kek=kek, created_by="k1")
    assert inline.has_secret and inline.secret_expires_at is not None
    async with db.acquire("t1") as conn:
        blob = await conn.fetchval("SELECT secret_enc FROM dps.dps_connection WHERE id = $1", inline.id)
        params = await conn.fetchval("SELECT params::text FROM dps.dps_connection WHERE id = $1", inline.id)
    assert b"s3cret-pw" not in blob and "s3cret" not in params
    creds = await load_credentials(inline, settings=settings, kek=kek)
    assert creds.password.get_secret_value() == "s3cret-pw"
    async with db.acquire("t1") as conn:
        await conn.execute("UPDATE dps.dps_connection SET secret_enc = NULL WHERE id = $1", inline.id)
    with pytest.raises(SecretUnavailable):
        await load_credentials(inline, settings=settings, kek=kek)


async def test_rules_listing_isolation_and_tests(migrated_db, kek) -> None:  # noqa: ANN001
    spec = ConnectionSpec(name="dev-pg", engine="postgres", params=PG, credentials={"username": "u", "password": "p"})
    with pytest.raises(ConnectionStoreError) as err:
        await create_connection("t1", spec, settings=migrated_db.model_copy(update={"inline_secrets_allowed": False}),
                                kek=kek, created_by=None)
    assert (err.value.code, err.value.status) == ("inline_secrets_disabled", 422)
    with pytest.raises(ConnectionStoreError) as err2:
        await create_connection("t1", spec, settings=migrated_db, kek=None, created_by=None)
    assert err2.value.code == "kek_unconfigured"
    made = [await create_connection("t1", spec.model_copy(update={"name": f"pg-{i}"}), settings=migrated_db,
                                    kek=kek, created_by=None) for i in range(3)]
    with pytest.raises(ConnectionStoreError) as dup:
        await create_connection("t1", spec.model_copy(update={"name": "pg-0"}), settings=migrated_db, kek=kek,
                                created_by=None)
    assert dup.value.code == "connection_name_taken"
    first, cursor = await list_connections("t1", limit=2)
    second, _ = await list_connections("t1", limit=2, cursor=cursor)
    assert [c.id for c in first + second] == [c.id for c in reversed(made)]
    assert await get_connection("t2", made[0].id) is None
    updated = await update_connection("t1", made[0].id, ConnectionPatch(tags=["uat"], roles={"source", "target"}),
                                      settings=migrated_db, kek=kek)
    assert updated.tags == ["uat"] and set(updated.roles) == {"source", "target"}
    await record_test("t1", made[0].id, ConnectionTestResult(ok=True, engine="postgres", checks=[],
                                                             capabilities={"copy": True}))
    assert (await get_connection("t1", made[0].id)).last_test.ok


async def test_delete_refused_while_a_job_is_active(migrated_db, kek) -> None:  # noqa: ANN001
    c = await create_connection("t1", ConnectionSpec(name="pg", engine="postgres", params=PG, secret_ref="dps/x"),
                                settings=migrated_db, kek=kek, created_by=None)
    await jobs.create_job(tenant_id="t1", kind=JobKind.discover, phase=JobPhase.discover, spec={}, connection_id=c.id)
    with pytest.raises(ConnectionStoreError) as err:
        await delete_connection("t1", c.id)
    assert err.value.code == "connection_in_use"
```

`tests/test_registry.py` (offline):
```python
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from dps.config import Settings
from dps.connections import Connection, Credentials
from dps.jobs_models import ConnectionParams
from dps.layers.connectors.base import ConnectorError
from dps.layers.connectors.registry import open_connector, test_store
from dps.storage import LocalObjectStore, target_store


def _conn(engine: str, **params) -> Connection:  # noqa: ANN003
    now = datetime.now(UTC)
    return Connection(id="c_1", tenant_id="t1", name="x", engine=engine, params=params, secret_ref=None,
                      has_secret=False, secret_expires_at=None, roles=["target"], tags=[], capabilities={},
                      last_test=None, created_by=None, created_at=now, updated_at=now)


@pytest.mark.parametrize("engine,code", [("oracle", "engine_unavailable"), ("mongo", "engine_unavailable"),
                                         ("s3", "engine_not_tabular"), ("pvc", "engine_not_tabular")])
async def test_engines_without_a_tabular_connector(engine: str, code: str) -> None:
    with pytest.raises(ConnectorError) as err:
        await open_connector(_conn(engine), Settings(_env_file=None), action="test", kek=None,
                             credentials=Credentials())
    assert err.value.code == code


async def test_pvc_store_is_confined_and_testable(tmp_path) -> None:  # noqa: ANN001
    settings = Settings(_env_file=None, export_root=str(tmp_path))
    store = target_store(_conn("pvc", base_path="exports/crm"), settings)
    assert isinstance(store, LocalObjectStore) and store.root == (tmp_path / "exports" / "crm").resolve()
    result = await test_store(store, _conn("pvc", base_path="exports/crm"))
    assert result.ok and [c["name"] for c in result.checks] == ["put_object", "delete_object"]
    # the spec model already refuses "..", so build the row as a corrupted stored one would look
    sneaky = Connection.model_construct(**{**dict(_conn("pvc", base_path="x")),
                                           "params": ConnectionParams.model_construct(base_path="../../etc")})
    with pytest.raises(ValueError):
        target_store(sneaky, settings)
```

- [ ] **Step 2: Run to confirm failure**

Run: `pytest -q tests/db/test_connections.py tests/test_registry.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'dps.connections'`.

- [ ] **Step 3: Write `dps/connections.py`** (glue; the non-obvious parts in full)

- `_COLS = "id, tenant_id, name, engine, params, secret_ref, secret_enc IS NOT NULL AS has_secret, secret_expires_at, roles, tags, capabilities, last_test, created_by, created_at, updated_at"` — `secret_enc` itself is selected only inside `load_credentials`.
- `create_connection`:
```python
    if spec.credentials is not None:
        if not settings.inline_secrets_allowed:
            raise ConnectionStoreError("inline_secrets_disabled", 422, "inline credentials are disabled; use secret_ref")
        if kek is None:
            raise ConnectionStoreError("kek_unconfigured", 503, "DPS_JOB_KEK is not configured")
    conn_id = new_id("c")
    sealed = expires = None
    if spec.credentials is not None:
        plain = _SealedCredentials(username=spec.credentials.username,
                                   password=spec.credentials.password.get_secret_value(),
                                   extra={k: v.get_secret_value() for k, v in spec.credentials.extra.items()})
        sealed = seal_json(kek, plain, aad=f"dps_connection.secret:{conn_id}")
        expires = datetime.now(UTC) + timedelta(hours=settings.inline_secret_ttl_hours)
    async with db.acquire(tenant_id) as conn, conn.transaction():
        await touch_tenant(tenant_id, conn=conn)
        try:
            await conn.execute(
                "INSERT INTO dps_connection (id, tenant_id, name, engine, params, secret_ref, secret_enc, "
                "secret_expires_at, roles, tags, created_by) VALUES ($1,$2,$3,$4,$5::jsonb,$6,$7,$8,$9,$10,$11)",
                conn_id, tenant_id, spec.name, spec.engine, spec.params.model_dump(mode="json"), spec.secret_ref,
                sealed, expires, sorted(spec.roles), spec.tags, created_by)
        except asyncpg.UniqueViolationError:
            raise ConnectionStoreError("connection_name_taken", 409, "a connection with this name exists") from None
```
  (`_SealedCredentials(BaseModel)`: `username: str`, `password: str`, `extra: dict[str, str]` — exists only inside the sealed blob and in memory.)
- `load_credentials`: `pvc` → `Credentials()`; `secret_ref` → `resolve_secret_ref` → `Credentials(username=files.pop("username", ""), password=SecretStr(files.pop("password", "")), extra={k: SecretStr(v) for k, v in files.items()})`; inline → `SELECT secret_enc FROM dps_connection WHERE id = $1` (tenant GUC) → `None` raises `SecretUnavailable()`; `kek is None` raises `ConnectionStoreError("kek_unconfigured", 503, …)`; `open_json(kek, blob, _SealedCredentials, aad=f"dps_connection.secret:{c.id}")`.
- `update_connection`: load the row, merge the patch into a `ConnectionSpec` built from the stored row (secret: keep the existing `secret_ref`, or keep the existing sealed blob when neither `secret_ref` nor `credentials` is in the patch — validate with `model_construct` for that one case), re-seal when `credentials` changes, `UPDATE … SET …, updated_at = now()`.
- `delete_connection`: in one transaction, `SELECT 1 FROM dps_job WHERE (connection_id = $1 OR target_connection_id = $1) AND state = ANY($2) LIMIT 1` with the active states → `connection_in_use`; else `DELETE`.
- `record_test`: `UPDATE dps_connection SET last_test = $2::jsonb, capabilities = $3::jsonb, updated_at = now() WHERE id = $1`.
- `list_connections`: keyset on `(created_at, id)` exactly as `jobs.list_jobs` (reuse `jobs._encode_cursor`/`_decode_cursor` by importing them as `dps.jobs.encode_cursor`/`decode_cursor` — rename the two helpers to public names in `dps/jobs.py` in this task and update their call sites).

- [ ] **Step 4: Write `target_store` (append to `dps/storage.py`) and `registry.py`**

```python
def target_store(conn: Connection, settings: Settings, credentials: Credentials | None = None) -> ObjectStore:
    """The object store behind a ``files`` target connection (``s3`` or ``pvc``)."""
    if conn.engine == "pvc":
        root = Path(settings.export_root).resolve()
        path = (root / (conn.params.base_path or "")).resolve()
        if path != root and root not in path.parents:
            raise ValueError("pvc base_path must stay under DPS_EXPORT_ROOT")
        return LocalObjectStore(path)
    if conn.engine == "s3":
        creds = credentials or Credentials()
        key_id = creds.extra.get("access_key_id") or SecretStr(creds.username)
        secret = creds.extra.get("secret_access_key") or creds.password
        return S3ObjectStore(bucket=conn.params.bucket or "", endpoint_url=conn.params.endpoint_url or settings.s3_endpoint_url or None,
                             region=conn.params.region or settings.s3_region, access_key_id=key_id.get_secret_value(),
                             secret_access_key=secret.get_secret_value(), force_path_style=settings.s3_force_path_style)
    raise ValueError("only s3 and pvc connections are object stores")
```
(`Connection`/`Credentials` imported under `TYPE_CHECKING` plus a local import inside the function body to avoid the cycle.)

`dps/layers/connectors/registry.py`:
```python
"""Connection → connector / object store (contract §1). M3 adds the oracle and mongo branches."""
from __future__ import annotations

from dps.config import Settings
from dps.connections import Connection, Credentials, load_credentials
from dps.layers.connectors.base import ConnectionTestResult, Connector, ConnectorError
from dps.storage import ObjectStore, StorageError, target_store


async def open_connector(conn: Connection, settings: Settings, *, action: str, kek: bytes | None,
                         job_id: str | None = None, credentials: Credentials | None = None) -> Connector:
    """An opened connector for a database connection (caller closes it)."""
    if conn.engine in ("s3", "pvc"):
        raise ConnectorError("engine_not_tabular", "file stores are targets for the files write mode")
    if conn.engine == "postgres":
        from dps.layers.connectors.postgres import PostgresConnector

        creds = credentials or await load_credentials(conn, settings=settings, kek=kek)
        connector = PostgresConnector(conn, creds, settings, action=action, job_id=job_id)
        await connector.open()
        return connector
    raise ConnectorError("engine_unavailable", f"the {conn.engine} connector arrives with milestone M3")


async def open_target_store(conn: Connection, settings: Settings, *, kek: bytes | None) -> ObjectStore:
    creds = None if conn.engine == "pvc" else await load_credentials(conn, settings=settings, kek=kek)
    return target_store(conn, settings, creds)


async def test_store(store: ObjectStore, conn: Connection) -> ConnectionTestResult:
    """Least-privilege check for a files target: put and delete a probe object (no customer data)."""
    key = f"dps-probe/{conn.id}.txt"                 # object keys must start with a letter or digit
    checks = []
    try:
        await store.put(key, b"dps connection test", content_type="text/plain")
        checks.append({"name": "put_object", "ok": True, "severity": "required"})
        await store.delete(key)
        checks.append({"name": "delete_object", "ok": True, "severity": "required"})
    except StorageError as exc:
        checks.append({"name": "put_object" if not checks else "delete_object", "ok": False,
                       "severity": "required", "code": exc.code})
    ok = all(c["ok"] for c in checks)
    return ConnectionTestResult(ok=ok, engine=conn.engine, checks=checks,
                                capabilities={"objects": ok}, error_code=None if ok else "storage_error")
```

- [ ] **Step 5: Run tests, lint, types**

Run: `pytest -q tests/db/test_connections.py tests/test_registry.py && ruff check dps tests tools && mypy dps`
Expected: `3 + 5 = 8 passed`, clean.

- [ ] **Step 6: Commit**

```bash
git add dps/connections.py dps/layers/connectors/registry.py dps/storage.py dps/secrets.py dps/jobs.py tests/db/test_connections.py tests/test_registry.py
git commit -m "feat(connectors): connection registry with sealed inline credentials, secret refs and store targets" -- dps/connections.py dps/layers/connectors/registry.py dps/storage.py dps/secrets.py dps/jobs.py tests/db/test_connections.py tests/test_registry.py
```

---

### Task 15: PostgreSQL connector — read side (`postgres.py`) + `source-pg` service

**Files:**
- Create: `dps/layers/connectors/postgres.py`, `docker/source-pg/initdb/01_bank_schema.sql`
- Modify: `docker-compose.yml` (`source-pg`), `tests/fixtures/sources.py` (`source_pg`, `pg_conn_factory`), `HANDOFF.md` (port 5442)
- Test: `tests/connectors/test_pg_sql.py` (offline), `tests/connectors/test_postgres_read.py` (`pg_source`)

Public interface:
```python
class PostgresConnector:                                    # implements Connector (contract §4.2)
    engine: Engine = "postgres"
    def __init__(self, conn: Connection, credentials: Credentials, settings: Settings, *, action: str,
                 job_id: str | None = None) -> None
    # + every Connector method; sample_keys(..., *, upper: tuple | None = None) (added kwarg);
    #   key_at_offset (added); writer() lands in Task 19
def coerce_bind(value: Any, pg_type: str) -> Any            # JSON scalar → asyncpg type for a prepared parameter
def select_sql(shape: TableShape, sel: Selection, *, projection: list[ColumnInfo], body_sql: str,
               body_args: list[Any], lower: tuple | None = None, upper: tuple | None = None,
               after: tuple | None = None, limit: int | None = None, count: bool = False,
               order: bool = True, tablesample: float | None = None) -> tuple[str, list[Any]]
PG_KINDS: dict[str, ColumnKind]                             # pg_type.typname → ColumnInfo.kind
```

**Read rules (contract §4.2, spec §7 engine notes).** The read pool's sessions start with `default_transaction_read_only=on`, `statement_timeout = connector_statement_timeout_seconds`, `lock_timeout=5s`, `idle_in_transaction_session_timeout = 2 × statement timeout`, and `application_name = dps:<action>:<job id>` (the PG analogue of Oracle's module/action/client_identifier, so the DBA's `pg_stat_activity` shows DPS). Every read runs inside `transaction(readonly=True)` (`BEGIN READ ONLY`). Keyset paging uses PostgreSQL row-value comparison, `key >= lower AND key < upper AND key > after ORDER BY key LIMIT batch`, one short read-only transaction per page (no long snapshot). Bind parameters are coerced with the **prepared statement's parameter types** (`stmt.get_parameters()`), because asyncpg will not coerce a JSON string into a `date`/`timestamptz`/`numeric` parameter on its own. `selection.sample` is a deterministic, update-stable filter on the key hash (`abs(hashtextextended(concat_ws('|', key::text…), seed)) % 1000000 < percent × 10000`) — TABLESAMPLE is used only for discovery and key sampling, never for runs (an in-place update moves tuples, which would change a TABLESAMPLE). `max_rows` is turned into a global upper bound by the run handler via `key_at_offset`. Connector errors carry a code and the exception class name only.

- [ ] **Step 1: Add `source-pg` and the bank schema**

`docker-compose.yml`:
```yaml
  source-pg:
    image: postgres:16
    environment: {POSTGRES_USER: postgres, POSTGRES_PASSWORD: postgres, POSTGRES_DB: postgres, TZ: UTC}
    command: ["postgres", "-c", "max_connections=200", "-c", "timezone=UTC", "-c", "shared_buffers=256MB"]
    ports: ["127.0.0.1:5442:5432"]
    volumes: ["source-pg-data:/var/lib/postgresql/data", "./docker/source-pg/initdb:/docker-entrypoint-initdb.d:ro"]
    healthcheck: {test: ["CMD-SHELL", "pg_isready -U postgres -d bank"], interval: 5s, timeout: 3s, retries: 30}
    mem_limit: 1g
    profiles: ["sources", "full"]
```
(+ `source-pg-data: {}` volume.)

`docker/source-pg/initdb/01_bank_schema.sql` — the demo source: database `bank`, schemas `bank` (the "production" source, 10 tables), `bank_uat` (table-mode targets), `bank_restore` (a restored copy for in-place demos, filled by `tools/seed_sources.py --restore-copy`), `bank_perf` (append demos), and two local-only roles. Ids start at 8+ digits so `fpe_digits` (domain ≥ 10⁶) applies to every key.
```sql
-- 01_bank_schema.sql — demo retail-bank source for DPS (local only; rows come from tools/seed_sources.py,
-- generated by the surrogate engine, so no real PII ever lands here).
CREATE DATABASE bank;
\connect bank
CREATE ROLE dps_reader LOGIN PASSWORD 'dps_reader' NOSUPERUSER NOBYPASSRLS;   -- read-only source login
CREATE ROLE dps_writer LOGIN PASSWORD 'dps_writer' NOSUPERUSER NOBYPASSRLS;   -- UAT/restore/perf writer
CREATE SCHEMA bank;
CREATE SCHEMA bank_uat AUTHORIZATION dps_writer;
CREATE SCHEMA bank_restore AUTHORIZATION dps_writer;
CREATE SCHEMA bank_perf AUTHORIZATION dps_writer;
CREATE TABLE bank._dps_seed_info (seed int NOT NULL, rows int NOT NULL, seeded_at timestamptz NOT NULL DEFAULT now());

CREATE TABLE bank.employees (
    employee_id   bigint PRIMARY KEY,                 -- 8 digits
    full_name     varchar(120) NOT NULL,
    email         varchar(254) NOT NULL,
    phone         varchar(20),
    branch_code   varchar(11) NOT NULL,
    role          varchar(32) NOT NULL,
    hired_on      date NOT NULL);
CREATE TABLE bank.customers (
    customer_id   bigint PRIMARY KEY,                 -- 10000001…
    full_name     varchar(120) NOT NULL,
    date_of_birth date,
    national_id   varchar(18),
    id_type       varchar(12) NOT NULL,               -- AADHAAR | SSN | NINO | SIN | CURP
    tax_id        char(10),
    email         varchar(254),
    mobile        varchar(20),
    country_code  char(2) NOT NULL,
    segment       varchar(16) NOT NULL,
    risk_score    smallint NOT NULL,
    kyc_status    varchar(12) NOT NULL,
    relationship_manager_id bigint REFERENCES bank.employees,
    notes         text,
    created_at    timestamptz NOT NULL,
    updated_at    timestamptz NOT NULL);
CREATE TABLE bank.addresses (
    address_id    bigint PRIMARY KEY,
    customer_id   bigint NOT NULL REFERENCES bank.customers,
    kind          varchar(12) NOT NULL,
    line1         varchar(200) NOT NULL,
    line2         varchar(200),
    city          varchar(80) NOT NULL,
    state_code    varchar(8),
    postal_code   varchar(10) NOT NULL,
    country_code  char(2) NOT NULL);
CREATE TABLE bank.contacts (
    contact_id    bigint PRIMARY KEY,
    customer_id   bigint NOT NULL REFERENCES bank.customers,
    channel       varchar(12) NOT NULL,               -- email | mobile | landline
    value         varchar(254) NOT NULL,
    is_primary    boolean NOT NULL DEFAULT false,
    verified_at   timestamptz);
CREATE TABLE bank.accounts (
    account_id    bigint PRIMARY KEY,                 -- 9 digits
    customer_id   bigint NOT NULL REFERENCES bank.customers,
    account_number varchar(34) NOT NULL UNIQUE,
    iban          varchar(34),
    ifsc          varchar(11),
    product       varchar(24) NOT NULL,
    currency      char(3) NOT NULL,
    balance       numeric(14,2) NOT NULL,
    opened_on     date NOT NULL,
    status        varchar(10) NOT NULL);
CREATE TABLE bank.cards (
    card_id       bigint PRIMARY KEY,
    account_id    bigint NOT NULL REFERENCES bank.accounts,
    pan           varchar(19) NOT NULL,
    expiry        char(5) NOT NULL,                   -- MM/YY
    cardholder_name varchar(120) NOT NULL,
    network       varchar(12) NOT NULL,
    status        varchar(10) NOT NULL,
    issued_on     date NOT NULL);
CREATE TABLE bank.transactions (
    txn_id        bigint PRIMARY KEY,                 -- 12 digits
    account_id    bigint NOT NULL REFERENCES bank.accounts,
    booked_at     timestamptz NOT NULL,
    amount        numeric(14,2) NOT NULL,
    currency      char(3) NOT NULL,
    direction     char(1) NOT NULL,
    counterparty_name varchar(120),
    counterparty_iban varchar(34),
    channel       varchar(12) NOT NULL,
    description   varchar(200),
    merchant_category char(4));
CREATE TABLE bank.loans (
    loan_id       bigint PRIMARY KEY,
    customer_id   bigint NOT NULL REFERENCES bank.customers,
    product       varchar(24) NOT NULL,
    principal     numeric(14,2) NOT NULL,
    rate          numeric(5,3) NOT NULL,
    term_months   smallint NOT NULL,
    status        varchar(12) NOT NULL,
    originated_on date NOT NULL,
    internal_score smallint NOT NULL);
CREATE TABLE bank.loan_applicants (
    applicant_id  bigint PRIMARY KEY,
    loan_id       bigint NOT NULL REFERENCES bank.loans,
    customer_id   bigint REFERENCES bank.customers,
    role          varchar(12) NOT NULL,               -- primary | co_applicant | guarantor
    full_name     varchar(120) NOT NULL,
    date_of_birth date,
    employer_name varchar(120),
    annual_income numeric(14,2),
    pan_or_ssn    varchar(18));
CREATE TABLE bank.kyc_documents (
    doc_ref       varchar(40) PRIMARY KEY,            -- a text key (token-strategy demos)
    customer_id   bigint NOT NULL REFERENCES bank.customers,
    doc_type      varchar(16) NOT NULL,
    doc_number    varchar(32) NOT NULL,
    issued_on     date,
    expires_on    date,
    issuing_country char(2) NOT NULL,
    verified_by   bigint REFERENCES bank.employees,
    notes         text);

CREATE TABLE bank_perf.transactions (                  -- append(generate) demo: identity keys
    txn_id        bigint GENERATED BY DEFAULT AS IDENTITY (START WITH 900000000001) PRIMARY KEY,
    account_id    bigint NOT NULL, booked_at timestamptz NOT NULL, amount numeric(14,2) NOT NULL,
    currency char(3) NOT NULL, direction char(1) NOT NULL, counterparty_name varchar(120),
    counterparty_iban varchar(34), channel varchar(12) NOT NULL, description varchar(200), merchant_category char(4));
ALTER TABLE bank_perf.transactions OWNER TO dps_writer;

GRANT USAGE ON SCHEMA bank TO dps_reader, dps_writer;
GRANT SELECT ON ALL TABLES IN SCHEMA bank TO dps_reader, dps_writer;
ALTER DEFAULT PRIVILEGES IN SCHEMA bank GRANT SELECT ON TABLES TO dps_reader, dps_writer;
GRANT USAGE ON SCHEMA bank_uat, bank_restore, bank_perf TO dps_reader;
```

- [ ] **Step 2: Fixtures** — append to `tests/fixtures/sources.py`:
```python
@pytest_asyncio.fixture
async def source_pg() -> AsyncIterator[SourcePg]:
    """A fresh schema ``t_<hex>`` in the ``bank`` database (superuser), readable by dps_reader; dropped after."""
    dsn = require_service("source-pg")
    schema = f"t_{secrets.token_hex(4)}"
    conn = await asyncpg.connect(dsn)
    await conn.execute(f'CREATE SCHEMA "{schema}"')
    await conn.execute(f'GRANT USAGE ON SCHEMA "{schema}" TO dps_reader, dps_writer')
    await conn.execute(f'ALTER DEFAULT PRIVILEGES IN SCHEMA "{schema}" GRANT SELECT ON TABLES TO dps_reader')
    await conn.execute(f'ALTER DEFAULT PRIVILEGES IN SCHEMA "{schema}" GRANT SELECT, INSERT, UPDATE, DELETE, TRUNCATE ON TABLES TO dps_writer')
    await conn.execute(f'ALTER DEFAULT PRIVILEGES IN SCHEMA "{schema}" GRANT USAGE, SELECT ON SEQUENCES TO dps_writer')
    await conn.execute(f'GRANT CREATE ON SCHEMA "{schema}" TO dps_writer')
    try:
        yield SourcePg(dsn=dsn, schema=schema, admin=conn)
    finally:
        await conn.execute(f'DROP SCHEMA "{schema}" CASCADE')
        await conn.close()
```
`SourcePg` is a small dataclass with `dsn`, `schema`, `admin: asyncpg.Connection`, and helpers `async def table(self, name, ddl_columns: str, rows: list[tuple]) -> ObjectRef` (CREATE TABLE + `copy_records_to_table`) and `def connection(self, *, user="dps_reader", roles=("source",)) -> Connection` (an in-memory `Connection` pointing at host/port/`bank` of the DSN), plus `def credentials(self, user="dps_reader") -> Credentials` (password = user name; `postgres` → `postgres`).

- [ ] **Step 3: Write the failing tests**

`tests/connectors/test_pg_sql.py` (offline):
```python
from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from dps.jobs_models import ObjectRef, SampleSpec, Selection
from dps.layers.connectors.base import ColumnInfo, TableShape
from dps.layers.connectors.postgres import coerce_bind, select_sql

COLS = [ColumnInfo(path="$.a", name="a", data_type="bigint", kind="int", is_key=True),
        ColumnInfo(path="$.b", name="b", data_type="text", kind="text", is_key=True),
        ColumnInfo(path="$.c", name="c", data_type="text", kind="text")]
SHAPE = TableShape(object=ObjectRef(owner="s", name="t"), columns=COLS, key=["a", "b"], key_source="primary_key")


def test_keyset_sql_uses_row_values_and_continues_bind_numbers() -> None:
    sql, args = select_sql(SHAPE, Selection(), projection=COLS, body_sql='"c" = $1', body_args=["x"],
                           lower=(1, "a"), upper=(9, "z"), after=(5, "m"), limit=100)
    assert sql == ('SELECT t."a", t."b", t."c" FROM "s"."t" AS t WHERE ("c" = $1) AND (t."a", t."b") >= ($2, $3) '
                   'AND (t."a", t."b") < ($4, $5) AND (t."a", t."b") > ($6, $7) ORDER BY t."a", t."b" LIMIT 100')
    assert args == ["x", 1, "a", 9, "z", 5, "m"]


def test_query_wrapping_count_and_sample_filter() -> None:
    sel = Selection(query="SELECT a, b, c FROM s.t", sample=SampleSpec(percent=12.5, seed=7))
    sql, args = select_sql(SHAPE, sel, projection=COLS, body_sql='SELECT "a", "b", "c" FROM "s"."t"', body_args=[],
                           count=True)
    assert sql.startswith('SELECT count(*) FROM (SELECT "a", "b", "c" FROM "s"."t") AS q WHERE ')
    assert "hashtextextended(concat_ws('|', q.\"a\"::text, q.\"b\"::text), 7)" in sql and "< 125000" in sql
    assert args == []


@pytest.mark.parametrize("value,typ,expected", [
    ("2025-01-01", "date", date(2025, 1, 1)),
    ("2025-01-01T10:00:00", "timestamptz", datetime(2025, 1, 1, 10, tzinfo=UTC)),
    ("12.50", "numeric", Decimal("12.50")), (5, "numeric", Decimal(5)), ("42", "int8", 42),
    ("x", "text", "x"), (3, "text", "3"), ("true", "bool", True), (None, "date", None),
    (date(2025, 1, 1), "date", date(2025, 1, 1)),
])
def test_coerce_bind(value: object, typ: str, expected: object) -> None:
    assert coerce_bind(value, typ) == expected


def test_coerce_bind_errors_are_value_free() -> None:
    with pytest.raises(ValueError) as err:
        coerce_bind("not-a-date-123-45-6789", "date")
    assert "6789" not in str(err.value)
```

`tests/connectors/test_postgres_read.py` (`pg_source`):
```python
from __future__ import annotations

from datetime import date

import asyncpg
import pytest

from dps.jobs_models import Selection
from dps.layers.connectors.base import ConnectorError, KeyRange, encode_key
from dps.layers.connectors.postgres import PostgresConnector
from dps.layers.connectors.selection import parameterize_literals

pytestmark = [pytest.mark.integration, pytest.mark.pg_source]


async def _connector(source_pg, settings, user: str = "dps_reader", **overrides) -> PostgresConnector:  # noqa: ANN001
    c = PostgresConnector(source_pg.connection(user=user), source_pg.credentials(user),
                          settings.model_copy(update=overrides), action="test", job_id="j_test")
    await c.open()
    return c


async def test_describe_types_and_key_detection(source_pg, settings) -> None:  # noqa: ANN001
    obj = await source_pg.table("typed", "id bigint PRIMARY KEY, name varchar(120) NOT NULL, amt numeric(14,2), "
                                "code char(10), at timestamptz, u uuid, doc jsonb, raw bytea", [])
    await source_pg.table("uniq", "a int NOT NULL, b text NOT NULL, c text, UNIQUE (a, b)", [])
    await source_pg.table("nokey", "a int, b text", [])
    c = await _connector(source_pg, settings)
    try:
        shape = await c.describe(obj)
        kinds = {col.name: col.kind for col in shape.columns}
        assert kinds == {"id": "int", "name": "text", "amt": "decimal", "code": "char", "at": "timestamp",
                         "u": "uuid", "doc": "json", "raw": "binary"}
        assert shape.key == ["id"] and shape.key_source == "primary_key"
        assert shape.column("name").length == 120 and (shape.column("amt").precision, shape.column("amt").scale) == (14, 2)
        assert shape.column("code").length == 10 and shape.column("at").ts_precision == 6
        uniq = await c.describe(obj.model_copy(update={"name": "uniq"}))
        assert (uniq.key, uniq.key_source) == (["a", "b"], "unique_index")
        assert (await c.describe(obj.model_copy(update={"name": "nokey"}))).key_source == "none"
        with pytest.raises(ConnectorError) as err:
            await c.describe(obj.model_copy(update={"name": "missing"}))
        assert err.value.code == "object_not_found"
    finally:
        await c.close()


async def test_keyset_paging_visits_every_row_once_in_db_order(source_pg, settings) -> None:  # noqa: ANN001
    names = ["b", "B", "a", "ä", "Z", "aa", "ab", "a b", "_x", "10", "9"] * 40
    obj = await source_pg.table("strkeys", "k text PRIMARY KEY, n int",
                                [(f"{v}-{i:04d}", i) for i, v in enumerate(names)])
    c = await _connector(source_pg, settings)
    try:
        shape = await c.describe(obj)
        got = [r["k"] async for batch in c.read_partition(shape, Selection(), {}, KeyRange(), after_key=None, batch=37)
               for r in batch]
        expected = [r["k"] for r in await source_pg.admin.fetch(f'SELECT k FROM "{source_pg.schema}".strkeys ORDER BY k')]
        assert got == expected and len(got) == len(names)
    finally:
        await c.close()


async def test_ranges_after_key_and_composite_keys(source_pg, settings) -> None:  # noqa: ANN001
    obj = await source_pg.table("pairs", "a int, b int, v text, PRIMARY KEY (a, b)",
                                [(a, b, f"v{a}{b}") for a in range(10) for b in range(10)])
    c = await _connector(source_pg, settings)
    try:
        shape = await c.describe(obj)
        cols = shape.key_columns()
        rng = KeyRange(lower=encode_key((2, 5), cols), upper=encode_key((4, 0), cols))
        rows = [(r["a"], r["b"]) async for batch in c.read_partition(shape, Selection(), {}, rng, after_key=(3, 1),
                                                                    batch=4) for r in batch]
        assert rows == [(3, b) for b in range(2, 10)]
    finally:
        await c.close()


async def test_selection_binds_count_sample_and_offsets(source_pg, settings) -> None:  # noqa: ANN001
    obj = await source_pg.table("cust", "id bigint PRIMARY KEY, country char(2), created date, risk int",
                                [(10_000_000 + i, ["IN", "US", "GB", "MX"][i % 4], date(2024, (i % 12) + 1, 1), i % 900)
                                 for i in range(2000)])
    c = await _connector(source_pg, settings)
    try:
        shape = await c.describe(obj)
        sel = parameterize_literals(Selection(where="created >= DATE '2024-07-01' AND country IN ('IN', 'US') "
                                              "AND risk > :min_risk", params={"min_risk": 100}), "postgres")
        expected = await source_pg.admin.fetchval(
            f"SELECT count(*) FROM \"{source_pg.schema}\".cust WHERE created >= '2024-07-01' "
            "AND country IN ('IN','US') AND risk > 100")
        assert await c.count(shape, sel, sel.params) == expected
        keys = await c.sample_keys(shape, Selection(), {}, 50, upper=(10_001_000,))
        assert len(keys) <= 50 and keys == sorted(keys) and all(k[0] < 10_001_000 for k in keys)
        assert await c.key_at_offset(shape, Selection(), {}, 10) == (10_000_010,)
        assert await c.key_at_offset(shape, Selection(), {}, 5000) is None
        sample = await c.sample(shape, Selection(), {}, 100)
        assert 1 <= len(sample) <= 100 and set(sample[0]) == {"id", "country", "created", "risk"}
    finally:
        await c.close()


async def test_sessions_are_read_only_tagged_and_time_limited(source_pg, settings) -> None:  # noqa: ANN001
    obj = await source_pg.table("ro", "id int PRIMARY KEY", [(1,)])
    c = await _connector(source_pg, settings, user="postgres", connector_statement_timeout_seconds=1)
    try:
        async with c.read_transaction() as conn:
            assert await conn.fetchval("SHOW transaction_read_only") == "on"
            assert await conn.fetchval("SHOW statement_timeout") == "1s"
            assert (await conn.fetchval("SELECT current_setting('application_name')")).startswith("dps:test:j_test")
            with pytest.raises(asyncpg.ReadOnlySQLTransactionError):
                await conn.execute(f'UPDATE "{source_pg.schema}".ro SET id = 2')
        with pytest.raises(ConnectorError) as err:
            await c.fetch_internal("SELECT pg_sleep(2)", [])
        assert err.value.code == "statement_timeout"
        assert obj.name == "ro"
    finally:
        await c.close()


async def test_source_test_checks_and_value_free_failures(source_pg, settings) -> None:  # noqa: ANN001
    obj = await source_pg.table("chk", "id int PRIMARY KEY", [])
    reader = await _connector(source_pg, settings)
    try:
        result = await reader.test(role="source", obj=obj)
        checks = {c["name"]: c for c in result.checks}
        assert result.ok and checks["select_source"]["ok"] and checks["no_dml_on_source"]["ok"]
        assert result.capabilities["copy"] and result.server_version
    finally:
        await reader.close()
    owner = await _connector(source_pg, settings, user="postgres")
    try:
        result = await owner.test(role="source", obj=obj)
        assert result.ok and {c["name"]: c for c in result.checks}["no_dml_on_source"]["ok"] is False
    finally:
        await owner.close()
    bad = PostgresConnector(source_pg.connection(), source_pg.credentials("dps_reader").model_copy(
        update={"password": "wrong-secret-pw"}), settings, action="test")
    with pytest.raises(ConnectorError) as err:
        await bad.open()
    assert err.value.code == "connection_failed" and "wrong-secret-pw" not in str(err.value)
```
(`read_transaction()` and `fetch_internal()` are small public helpers on the connector used by tests and by the writer's privilege checks; `fetch_internal` maps `asyncpg.QueryCanceledError` to `statement_timeout`.)

- [ ] **Step 4: Run to confirm failure**

Run: `docker compose --profile sources up -d source-pg && pytest -q tests/connectors/test_pg_sql.py tests/connectors/test_postgres_read.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'dps.layers.connectors.postgres'`.

- [ ] **Step 5: Write `dps/layers/connectors/postgres.py`** (read side; the writer class is added in Task 19)

```python
"""PostgreSQL connector (asyncpg) — read side (contract §4, spec §7 engine notes)."""
from __future__ import annotations

import json
import math
import ssl
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, time
from decimal import Decimal, InvalidOperation
from typing import Any, Literal
from uuid import UUID

import asyncpg

from dps.config import Settings
from dps.connections import Connection, Credentials
from dps.jobs_models import Selection
from dps.layers.connectors.base import (
    ColumnInfo, ColumnKind, ConnectionTestResult, ConnectorError, Engine, JsonScalar, KeyRange, ObjectRef, Row,
    TableShape, WriteMode, decode_key,
)
from dps.layers.connectors.selection import compile_query, compile_where, quote_ident
from dps.secrets import resolve_secret_ref

PG_KINDS: dict[str, ColumnKind] = {
    "int2": "int", "int4": "int", "int8": "int", "numeric": "decimal", "float4": "float", "float8": "float",
    "text": "text", "varchar": "text", "citext": "text", "name": "text", "bpchar": "char", "date": "date",
    "timestamp": "timestamp", "timestamptz": "timestamp", "bool": "bool", "bytea": "binary", "json": "json",
    "jsonb": "json", "uuid": "uuid"}
_RETRYABLE = (asyncpg.ConnectionDoesNotExistError, asyncpg.TooManyConnectionsError,
              asyncpg.CannotConnectNowError, ConnectionError, OSError, TimeoutError)


def _q(name: str) -> str:
    return quote_ident(name, "postgres")


def coerce_bind(value: Any, pg_type: str) -> Any:
    """Turn a JSON scalar into the Python type asyncpg needs for a parameter of ``pg_type``."""
    if value is None or not isinstance(value, (str, int, float, bool)):
        return value                              # already a driver value (decoded keys, row values)
    try:
        if pg_type in ("int2", "int4", "int8"):
            return int(value)
        if pg_type == "numeric":
            return Decimal(str(value))
        if pg_type in ("float4", "float8"):
            return float(value)
        if pg_type == "bool":
            return value if isinstance(value, bool) else str(value).lower() in ("true", "t", "1", "yes")
        if pg_type == "date":
            return date.fromisoformat(str(value))
        if pg_type in ("timestamp", "timestamptz"):
            dt = datetime.fromisoformat(str(value))
            if pg_type == "timestamptz" and dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            return dt
        if pg_type == "time":
            return time.fromisoformat(str(value))
        if pg_type == "uuid":
            return UUID(str(value))
        if pg_type == "bytea":
            return bytes.fromhex(str(value))
    except (ValueError, InvalidOperation):
        raise ValueError(f"a bound value is not a valid {pg_type}") from None
    return str(value) if not isinstance(value, str) else value


class _Binds:
    def __init__(self, start: list[Any]) -> None:
        self.args = list(start)

    def row(self, values: tuple[Any, ...]) -> str:
        marks = []
        for v in values:
            self.args.append(v)
            marks.append(f"${len(self.args)}")
        return marks[0] if len(marks) == 1 else "(" + ", ".join(marks) + ")"


def select_sql(shape: TableShape, sel: Selection, *, projection: list[ColumnInfo], body_sql: str,
               body_args: list[Any], lower: tuple[Any, ...] | None = None, upper: tuple[Any, ...] | None = None,
               after: tuple[Any, ...] | None = None, limit: int | None = None, count: bool = False,
               order: bool = True, tablesample: float | None = None) -> tuple[str, list[Any]]:
    """Read/count SQL over a table (``body_sql`` = compiled where) or a wrapped query (``body_sql`` = query)."""
    alias = "q" if sel.query else "t"
    binds = _Binds(body_args)
    if sel.query:
        source = f"({body_sql}) AS q"
        conds: list[str] = []
    else:
        sample = f" TABLESAMPLE SYSTEM ({tablesample:.4f})" if tablesample else ""
        source = f"{_q(shape.object.owner or 'public')}.{_q(shape.object.name)} AS t{sample}"
        conds = [f"({body_sql})"] if body_sql else []
    keys = [f"{alias}.{_q(k)}" for k in shape.key]
    key_expr = keys[0] if len(keys) == 1 else "(" + ", ".join(keys) + ")"
    if lower is not None:
        conds.append(f"{key_expr} >= {binds.row(lower)}")
    if upper is not None:
        conds.append(f"{key_expr} < {binds.row(upper)}")
    if after is not None:
        conds.append(f"{key_expr} > {binds.row(after)}")
    if sel.sample is not None:
        texts = ", ".join(f"{k}::text" for k in keys)
        conds.append(f"(abs(hashtextextended(concat_ws('|', {texts}), {int(sel.sample.seed or 0)})) % 1000000) "
                     f"< {int(round(sel.sample.percent * 10000))}")
    cols = "count(*)" if count else ", ".join(f"{alias}.{_q(c.name)}" for c in projection)
    sql = f"SELECT {cols} FROM {source}" + (" WHERE " + " AND ".join(conds) if conds else "")
    if order and not count:
        sql += " ORDER BY " + ", ".join(keys)
    if limit is not None:
        sql += f" LIMIT {int(limit)}"
    return sql, binds.args


def _typmod_fields(typname: str, typmod: int) -> dict[str, int | None]:
    if typname in ("varchar", "bpchar") and typmod > 4:
        return {"length": typmod - 4}
    if typname == "numeric" and typmod >= 4:
        return {"precision": ((typmod - 4) >> 16) & 0xFFFF, "scale": (typmod - 4) & 0xFFFF}
    if typname in ("timestamp", "timestamptz"):
        return {"ts_precision": typmod if typmod >= 0 else 6}
    return {}


class PostgresConnector:
    engine: Engine = "postgres"

    def __init__(self, conn: Connection, credentials: Credentials, settings: Settings, *, action: str,
                 job_id: str | None = None) -> None:
        self.conn, self._creds, self.settings = conn, credentials, settings
        self.action, self.job_id = action, job_id
        self._read: asyncpg.Pool | None = None
        self._write: asyncpg.Pool | None = None
        self._param_types: dict[str, list[str]] = {}

    # -- sessions -----------------------------------------------------------------------------
    def _ssl(self) -> ssl.SSLContext | bool:
        mode = self.conn.params.options.get("sslmode", "prefer")
        if mode == "disable":
            return False
        if mode in ("verify-ca", "verify-full"):
            ref = self.conn.params.options.get("sslrootcert_ref")
            cadata = resolve_secret_ref(ref, self.settings).get("ca.pem") if ref else None
            ctx = ssl.create_default_context(cadata=cadata) if cadata else ssl.create_default_context()
            ctx.check_hostname = mode == "verify-full"
            return ctx
        if mode == "require":
            ctx = ssl.create_default_context()
            ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
            return ctx
        return False

    def _server_settings(self, *, read_only: bool) -> dict[str, str]:
        ms = int(self.settings.connector_statement_timeout_seconds * 1000)
        out = {"application_name": f"dps:{self.action}:{self.job_id or '-'}"[:63], "statement_timeout": str(ms),
               "lock_timeout": "5000", "idle_in_transaction_session_timeout": str(ms * 2), "timezone": "UTC"}
        if read_only:
            out["default_transaction_read_only"] = "on"
        return out

    async def _pool(self, *, read_only: bool) -> asyncpg.Pool:
        p = self.conn.params
        try:
            return await asyncpg.create_pool(
                host=p.host, port=p.port or 5432, database=p.database, user=self._creds.username,
                password=self._creds.password.get_secret_value() or None, ssl=self._ssl(), min_size=1,
                max_size=self.settings.worker_concurrency + 1, timeout=self.settings.connector_connect_timeout_seconds,
                command_timeout=self.settings.connector_statement_timeout_seconds + 5,
                server_settings=self._server_settings(read_only=read_only))
        except (asyncpg.PostgresError, *_RETRYABLE) as exc:
            raise ConnectorError("connection_failed", type(exc).__name__, retryable=True) from None

    async def open(self) -> None:
        if self._read is None:
            self._read = await self._pool(read_only=True)

    async def write_pool(self) -> asyncpg.Pool:
        """A separate read-write pool (writers only); never used for source reads."""
        if self._write is None:
            self._write = await self._pool(read_only=False)
        return self._write

    async def close(self) -> None:
        for pool in (self._read, self._write):
            if pool is not None:
                await pool.close()
        self._read = self._write = None

    @asynccontextmanager
    async def read_transaction(self) -> AsyncIterator[asyncpg.Connection]:
        """``BEGIN READ ONLY`` on a read-only, time-limited session."""
        assert self._read is not None, "open() first"
        async with self._read.acquire() as conn, conn.transaction(readonly=True):
            yield conn

    async def _fetch(self, conn: asyncpg.Connection, sql: str, args: list[Any]) -> list[asyncpg.Record]:
        try:
            stmt = await conn.prepare(sql)
            types = self._param_types.setdefault(sql, [t.name for t in stmt.get_parameters()])
            coerced = [coerce_bind(v, t) for v, t in zip(args, types, strict=True)]
            return await stmt.fetch(*coerced)
        except asyncpg.QueryCanceledError:
            raise ConnectorError("statement_timeout", "the statement exceeded the connector timeout") from None
        except asyncpg.PostgresError as exc:
            raise ConnectorError("source_error", type(exc).__name__) from None
        except _RETRYABLE as exc:
            raise ConnectorError("source_error", type(exc).__name__, retryable=True) from None

    async def fetch_internal(self, sql: str, args: list[Any]) -> list[asyncpg.Record]:
        async with self.read_transaction() as conn:
            return await self._fetch(conn, sql, args)

    # -- catalogue ------------------------------------------------------------------------------
    async def list_objects(self, owner: str | None = None, *, limit: int = 500) -> list[ObjectRef]:
        rows = await self.fetch_internal(
            "SELECT n.nspname AS owner, c.relname AS name FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE c.relkind IN ('r', 'p') AND n.nspname NOT IN ('pg_catalog', 'information_schema') "
            "AND n.nspname NOT LIKE 'pg\\_%' AND c.relname NOT IN ('dps_write_ledger', '_dps_seed_info') "
            "AND has_table_privilege(c.oid, 'SELECT') AND ($1::text IS NULL OR n.nspname = $1) "
            "ORDER BY 1, 2 LIMIT $2", [owner, limit])
        out = []
        for r in rows:
            try:
                out.append(ObjectRef(owner=r["owner"], name=r["name"]))
            except ValueError:
                continue                                   # names DPS cannot quote safely are not offered
        return out

    async def describe(self, obj: ObjectRef) -> TableShape:
        owner = obj.owner or "public"
        cols = await self.fetch_internal(
            "SELECT a.attname, format_type(a.atttypid, a.atttypmod) AS data_type, t.typname, a.atttypmod, "
            "a.attnotnull, a.atthasdef, a.attidentity <> '' AS is_identity "
            "FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid JOIN pg_namespace n ON n.oid = c.relnamespace "
            "JOIN pg_type t ON t.oid = a.atttypid WHERE n.nspname = $1 AND c.relname = $2 AND a.attnum > 0 "
            "AND NOT a.attisdropped ORDER BY a.attnum", [owner, obj.name])
        if not cols:
            raise ConnectorError("object_not_found", "the table does not exist or is not readable")
        idx = await self.fetch_internal(
            "SELECT i.indisprimary, array_agg(a.attname ORDER BY k.ord) AS cols, bool_and(a.attnotnull) AS not_null "
            "FROM pg_index i JOIN pg_class c ON c.oid = i.indrelid JOIN pg_namespace n ON n.oid = c.relnamespace "
            "JOIN pg_class ic ON ic.oid = i.indexrelid "
            "JOIN LATERAL unnest(i.indkey::int2[]) WITH ORDINALITY AS k(attnum, ord) ON k.ord <= i.indnkeyatts "
            "JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = k.attnum "
            "WHERE n.nspname = $1 AND c.relname = $2 AND (i.indisprimary OR i.indisunique) AND i.indexprs IS NULL "
            "AND i.indpred IS NULL AND i.indisvalid GROUP BY i.indisprimary, ic.relname, i.indnkeyatts "
            "ORDER BY i.indisprimary DESC, i.indnkeyatts, ic.relname", [owner, obj.name])
        key: list[str] = []
        source: Literal["primary_key", "unique_index", "rowid", "none"] = "none"
        for r in idx:
            if r["indisprimary"] or r["not_null"]:
                key, source = list(r["cols"]), "primary_key" if r["indisprimary"] else "unique_index"
                break
        estimate = await self.fetch_internal(
            "SELECT c.reltuples::bigint AS n FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = $1 AND c.relname = $2", [owner, obj.name])
        columns = [ColumnInfo(path=f"$.{r['attname']}", name=r["attname"], data_type=r["data_type"],
                              kind=PG_KINDS.get(r["typname"], "other"), nullable=not r["attnotnull"],
                              is_key=r["attname"] in key, has_default=r["atthasdef"], is_identity=r["is_identity"],
                              **_typmod_fields(r["typname"], r["atttypmod"])) for r in cols]
        n = estimate[0]["n"] if estimate else -1
        return TableShape(object=ObjectRef(owner=owner, name=obj.name), columns=columns, key=key, key_source=source,
                          row_estimate=int(n) if n is not None and n >= 0 else None)
```
(`ColumnInfo.path` uses `dps.engine.jsonpath.child_path("$", name)` once Task 16 adds it; until then `f"$.{name}"` — Task 16 switches it.)

Reads (append to the class):
```python
    def _body(self, sel: Selection) -> tuple[str, list[str]]:
        return compile_query(sel, "postgres") if sel.query else compile_where(sel, "postgres")

    def _projection(self, shape: TableShape, sel: Selection) -> list[ColumnInfo]:
        if not sel.columns:
            return shape.columns
        wanted = set(sel.columns) | set(shape.key)
        return [c for c in shape.columns if c.name in wanted]

    async def count(self, shape: TableShape, selection: Selection, params: dict[str, JsonScalar]) -> int:
        body, names = self._body(selection)
        sql, args = select_sql(shape, selection, projection=[], body_sql=body, body_args=[params[n] for n in names],
                               count=True)
        rows = await self.fetch_internal(sql, args)
        total = int(rows[0][0])
        return min(total, selection.max_rows) if selection.max_rows else total

    async def sample(self, shape: TableShape, selection: Selection, params: dict[str, JsonScalar], n: int) -> list[Row]:
        body, names = self._body(selection)
        args = [params[k] for k in names]
        proj = self._projection(shape, selection)
        estimate = shape.row_estimate or 0
        if not selection.query and estimate > n * 20:
            pct = min(100.0, max(0.01, 100.0 * n * 4 / estimate))
            sql, a = select_sql(shape, selection, projection=proj, body_sql=body, body_args=args, limit=n,
                                order=False, tablesample=pct)
            rows = await self.fetch_internal(sql, a)
            if len(rows) >= min(n, 50):
                return [dict(r) for r in rows]
        sql, a = select_sql(shape, selection, projection=proj, body_sql=body, body_args=args, order=False)
        rows = await self.fetch_internal(sql + f" ORDER BY random() LIMIT {int(n)}", a)
        return [dict(r) for r in rows]

    async def sample_keys(self, shape: TableShape, selection: Selection, params: dict[str, JsonScalar], n: int, *,
                          upper: tuple[Any, ...] | None = None) -> list[tuple[Any, ...]]:
        """Up to ``n`` keys spread over the selection, returned in the database's key order."""
        body, names = self._body(selection)
        args = [params[k] for k in names]
        keys = shape.key_columns()
        total = await self.count(shape, selection.model_copy(update={"max_rows": None}), params)
        step = max(1, math.ceil(total / max(1, n)))
        estimate = shape.row_estimate or 0
        sampled = None if selection.query or estimate <= 2_000_000 else min(100.0, 100.0 * 20 * n / estimate)
        inner, a = select_sql(shape, selection, projection=keys, body_sql=body, body_args=args, upper=upper,
                              order=False, tablesample=sampled)
        if sampled is not None:
            step = max(1, math.ceil(step * sampled / 100))
        cols = ", ".join(_q(k) for k in shape.key)
        sql = (f"SELECT {cols} FROM (SELECT {cols}, row_number() OVER (ORDER BY {cols}) AS rn FROM ({inner}) s0) s "
               f"WHERE s.rn % {step} = 0 ORDER BY s.rn")
        rows = await self.fetch_internal(sql, a)
        return [tuple(r[k] for k in shape.key) for r in rows][:n]

    async def key_at_offset(self, shape: TableShape, selection: Selection, params: dict[str, JsonScalar],
                            offset: int) -> tuple[Any, ...] | None:
        body, names = self._body(selection)
        sql, a = select_sql(shape, selection, projection=shape.key_columns(), body_sql=body,
                            body_args=[params[k] for k in names])
        rows = await self.fetch_internal(sql + f" OFFSET {int(offset)} LIMIT 1", a)
        return tuple(rows[0][k] for k in shape.key) if rows else None

    async def read_partition(self, shape: TableShape, selection: Selection, params: dict[str, JsonScalar],
                             key_range: KeyRange, *, after_key: tuple[Any, ...] | None,
                             batch: int) -> AsyncIterator[list[Row]]:
        """Keyset pages within ``[lower, upper)`` after ``after_key``; one read-only transaction per page."""
        body, names = self._body(selection)
        args = [params[k] for k in names]
        cols = shape.key_columns()
        lower = decode_key(key_range.lower, cols) if key_range.lower else None
        upper = decode_key(key_range.upper, cols) if key_range.upper else None
        proj = self._projection(shape, selection)
        cursor = after_key
        while True:
            sql, a = select_sql(shape, selection, projection=proj, body_sql=body, body_args=args, lower=lower,
                                upper=upper, after=cursor, limit=batch)
            rows = await self.fetch_internal(sql, a)
            if not rows:
                return
            yield [dict(r) for r in rows]
            cursor = tuple(rows[-1][k] for k in shape.key)
            if len(rows) < batch:
                return
```
(`row_number() OVER (ORDER BY key)` ranks in the database collation, so every `step`-th key is a quantile boundary candidate in the database's own order. Above two million estimated rows the inner select reads a `TABLESAMPLE SYSTEM` of about `20·n` rows instead of ranking the whole selection; the boundaries are then approximate quantiles, which only affects partition balance, never coverage — the first range is open below and the last is bounded only by `upper`.)

`test()` (append): `SHOW server_version` and `current_setting('server_version_num')`; checks as dicts `{name, ok, severity, detail?}`:
- always `connect` (required; detail `"tls"`/`"plain"`), `session_tagged` (advisory: `application_name` starts with `dps:`);
- `role="source"` with `obj`: `select_source` (required: `has_table_privilege(current_user, '<owner>.<name>', 'SELECT')`, detail = `row_estimate` rows by statistics), `no_dml_on_source` (advisory: ok when none of `INSERT/UPDATE/DELETE/TRUNCATE` is held);
- `role="target"`: `write_mode="table"` → if the object exists: `insert_target`, `select_target` (required), `truncate_target` (advisory); else `create_target` = `has_schema_privilege(current_user, owner, 'CREATE')` (required); `in_place` → `update_target` + `select_target` + `ledger_schema` (`CREATE` on `ledger_owner or owner`) (required); `append` → `insert_target` + `ledger_schema` (required);
- `capabilities = {"transactions": True, "merge": version_num >= 150000, "identity": True, "sample": True, "rowid": False, "copy": True}`;
- `ok = all(c["ok"] for c in checks if c["severity"] == "required")`; a connection failure returns `ok=False, error_code="connection_failed"` without raising.

- [ ] **Step 6: Run tests, lint, types**

Run: `pytest -q tests/connectors/test_pg_sql.py tests/connectors/test_postgres_read.py && ruff check dps tests tools && mypy dps`
Expected: `13 + 6 = 19 passed` (`test_coerce_bind` ×10), clean. If `test_keyset_paging_visits_every_row_once_in_db_order` fails only on the `ä`/`a b` ordering, the read path is sorting somewhere in Python — every ordering must come from `ORDER BY`.

- [ ] **Step 7: Commit**

```bash
git add dps/layers/connectors/postgres.py docker/source-pg/initdb/01_bank_schema.sql docker-compose.yml HANDOFF.md tests/fixtures/sources.py tests/connectors/test_pg_sql.py tests/connectors/test_postgres_read.py
git commit -m "feat(connectors): PostgreSQL read side - read-only keyset paging, typed binds, sampling; demo bank schema" -- dps/layers/connectors/postgres.py docker/source-pg/initdb/01_bank_schema.sql docker-compose.yml HANDOFF.md tests/fixtures/sources.py tests/connectors/test_pg_sql.py tests/connectors/test_postgres_read.py
```

---

### Task 16: Engine bridge (`dps/engine/batch.py`) + M0 touch points

**Files:**
- Create: `dps/engine/batch.py`
- Modify: `dps/engine/jsonpath.py` (`child_path`), `dps/engine/transform/apply.py` (`ApplyOptions.anchor`), `dps/engine/service.py` (`detector`, `surrogate_engine`), `dps/engine/transform/methods.py` (`_dictionary` determinism), `dps/layers/connectors/postgres.py` (`child_path` in `describe`)
- Test: `tests/test_batch.py`, `tests/test_engine_touch_m2.py`

Public interface (contract §6.4; additions marked):
```python
class ColumnPlan(BaseModel):
    path: str; entity_type: str; method: MethodSpec; text_column: bool; reversible: bool
    kind: str; length: int | None                      # of the OUTPUT (target) column
    name: str                                          # (added) physical column
    precision: int | None = None; scale: int | None = None   # (added) decimal outputs
class BatchResult(BaseModel):
    rows: list[Row]; values_by_method: dict[str, int]; fallbacks: dict[str, int]; verification: Verification; skipped: int
    values_by_column: dict[str, int] = {}             # (added) evidence per-column counts
class LeakageError(Exception)                          # .hits: count; message value-free
class IncompatibleMethod(Exception)                    # .path, .code ("incompatible_method" | "length_overflow")
KEYLESS_METHODS: frozenset[str]                        # usable in synthesize mode (no key material)
LOCALE_BY_COUNTRY: dict[str, str]                      # US→en_US, IN→en_IN, GB/UK→en_GB, CA→en_CA, MX→es_MX
class BatchTransformer:
    def __init__(self, engine: PrivacyEngine, *, policy: Policy, mode: Literal["obfuscate", "synthesize"],
                 columns: list[ColumnPlan], shape: TableShape, key: KeyMaterial | None,
                 seed: SynthesisSeed | None, locale: str, unclassified: str = "detect",
                 tabular: TabularSampler | None = None, anchor: tuple[str, str] | None = None,
                 verify: Literal["strict", "warn"] = "strict") -> None   # anchor, verify (added)
    def transform(self, rows: list[Row]) -> BatchResult
def row_anchor(row: Row, shape: TableShape, anchor: tuple[str, str] | None) -> str
def verify_row(originals: list[tuple[ColumnPlan, str, bool]], outputs: dict[str, str | None]) -> list[dict[str, str]]
```
(`TabularSampler` is M5's protocol (contract §6.6); M2 declares it in `batch.py` as `class TabularSampler(Protocol): def sample_row(self, anchor: str) -> dict[str, Any]: ...` and always passes `None`.)

**Verification per row (deviation from contract §6.4's "per batch").** M0's `verify_output` searches a normalised haystack for every original of length ≥ 4. Over a whole batch that produces false positives at scale (a 4-letter first name inside another row's realistic surrogate; a 4-digit original inside another row's 12-digit FPE output). L2 therefore verifies **each row against its own output**: a hit is (a) an exact fixed point — the normalised output of a column equals the normalised original, for originals of ≥ 4 characters — or (b) containment of a normalised original of ≥ 8 characters anywhere in the row's transformed outputs (cross-field leakage). Span originals (free-text columns) use rule (b). Kept columns are never in the haystack. See "Open contract questions".

- [ ] **Step 1: Write the failing tests**

`tests/test_engine_touch_m2.py`:
```python
from __future__ import annotations

import subprocess
import sys

from dps.config import Settings
from dps.engine.findings import Finding, Locator, Plan, PlanEntry
from dps.engine.jsonpath import child_path
from dps.engine.policy import load_preset
from dps.engine.service import PrivacyEngine
from dps.engine.synth.surrogates import generate_synthesis_seed
from dps.engine.transform.apply import ApplyOptions, apply_plan
from dps.engine.transform.crypto import generate_key_material


def test_child_path() -> None:
    assert child_path("$", "full_name") == "$.full_name" and child_path("$", "odd key") == "$['odd key']"


def test_apply_plan_uses_the_given_anchor() -> None:
    policy = load_preset("gdpr-pseudonymise")
    finding = Finding(locator=Locator(path="$.dob"), entity_type="DATE_OF_BIRTH", score=1.0, detectors=["snapshot"],
                      sensitivity="HIGH")
    plan = Plan(mode="obfuscate", record_sensitivity="HIGH",
                entries=[PlanEntry(finding=finding, method="date_shift", params={"window_days": 365},
                                   reason="t", reversible=True)])
    key = generate_key_material("key")
    base = ApplyOptions(policy=policy, key=key, surrogates=None, fpe_enabled=False)
    a = apply_plan(plan=plan, options=base, data={"dob": "1988-03-14"})
    b = apply_plan(plan=plan, options=ApplyOptions(policy=policy, key=key, surrogates=None, fpe_enabled=False,
                                                   anchor="CUSTOMER_ID:abc"), data={"dob": "1988-03-14"})
    assert b.anchor == "CUSTOMER_ID:abc" and a.anchor != b.anchor


def test_engine_exposes_detector_and_surrogates() -> None:
    engine = PrivacyEngine(Settings(_env_file=None, llm_provider="stub", ner_enabled=False))
    policy = load_preset("gdpr-pseudonymise")
    assert engine.detector(policy, "en_IN").detect(text="mail ann.k@example-mail.in").findings
    seed = generate_synthesis_seed()
    one = engine.surrogate_engine(seed, "en_US").surrogate("EMAIL", "a@b.co", "en_US", "row:1")
    assert one == engine.surrogate_engine(seed, "en_US").surrogate("EMAIL", "a@b.co", "en_US", "row:1")


def test_dictionary_without_key_is_stable_across_processes() -> None:
    code = ("from dps.engine.transform.methods import TransformContext, run_method;"
            "print(run_method('dictionary', 'alpha', TransformContext(entity_type='X', path='$.x', mode='synthesize',"
            " params={'values': ['p','q','r','s','t','u','v']})).value)")
    outs = {subprocess.run([sys.executable, "-c", code], env={"PYTHONHASHSEED": seed}, capture_output=True,
                           text=True, check=True).stdout.strip() for seed in ("1", "2", "3")}
    assert len(outs) == 1
```

`tests/test_batch.py`:
```python
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from dps.config import Settings
from dps.engine.batch import BatchTransformer, ColumnPlan, IncompatibleMethod, LeakageError, row_anchor
from dps.engine.policy import MethodSpec, load_preset
from dps.engine.service import Payload, PrivacyEngine, RequestOptions
from dps.engine.synth.surrogates import generate_synthesis_seed
from dps.engine.transform.apply import reveal
from dps.engine.transform.crypto import generate_key_material
from dps.jobs_models import ObjectRef
from dps.layers.connectors.base import ColumnInfo, TableShape


class RotCipher:
    def encrypt(self, alphabet: str, plaintext: str) -> str:
        return "".join(alphabet[(alphabet.index(c) + 3) % len(alphabet)] for c in plaintext)

    def decrypt(self, alphabet: str, ciphertext: str) -> str:
        return "".join(alphabet[(alphabet.index(c) - 3) % len(alphabet)] for c in ciphertext)


ENGINE = PrivacyEngine(Settings(_env_file=None, llm_provider="stub", fpe_enabled=True, ner_enabled=False,
                                default_locale="en_IN"), cipher_factory=lambda k, t: RotCipher())
COLS = [ColumnInfo(path="$.customer_id", name="customer_id", data_type="bigint", kind="int", is_key=True),
        ColumnInfo(path="$.full_name", name="full_name", data_type="varchar(120)", kind="text", length=120),
        ColumnInfo(path="$.email", name="email", data_type="varchar(254)", kind="text", length=254),
        ColumnInfo(path="$.dob", name="dob", data_type="date", kind="date"),
        ColumnInfo(path="$.balance", name="balance", data_type="numeric(14,2)", kind="decimal", precision=14, scale=2),
        ColumnInfo(path="$.country", name="country", data_type="char(2)", kind="char", length=2),
        ColumnInfo(path="$.notes", name="notes", data_type="text", kind="text")]
SHAPE = TableShape(object=ObjectRef(owner="bank", name="customers"), columns=COLS, key=["customer_id"],
                   key_source="primary_key")
ROWS = [{"customer_id": 10_000_001, "full_name": "Ananya Krishnan", "email": "ananya.k@example-mail.in",
         "dob": date(1988, 3, 14), "balance": Decimal("1200.50"), "country": "IN",
         "notes": "Call ann.k@example-mail.in about the loan"},
        {"customer_id": 10_000_002, "full_name": None, "email": "", "dob": None, "balance": Decimal("1.00"),
         "country": "US", "notes": None}]


def plan(name: str, entity: str, method: str, kind: str, text: bool = False, **params) -> ColumnPlan:  # noqa: ANN003
    col = SHAPE.column(name)
    return ColumnPlan(path=col.path, name=name, entity_type=entity, method=MethodSpec(method=method, **params),
                      text_column=text, reversible=method not in ("surrogate", "keep", "generalize"), kind=kind,
                      length=col.length, precision=col.precision, scale=col.scale)


OBF = [plan("customer_id", "CUSTOMER_ID", "fpe_digits", "int"), plan("full_name", "PERSON_NAME", "fpe_text", "text"),
       plan("email", "EMAIL", "email", "text"), plan("dob", "DATE_OF_BIRTH", "date_shift", "date", window_days=365),
       plan("notes", "FREE_TEXT", "det_token", "text", text=True)]


def _obf(key=None, cols=OBF, **kw):  # noqa: ANN001, ANN003, ANN202
    return BatchTransformer(ENGINE, policy=load_preset("gdpr-pseudonymise"), mode="obfuscate", columns=cols,
                            shape=SHAPE, key=key or generate_key_material("key"), seed=None, locale="en_IN",
                            anchor=("customer_id", "CUSTOMER_ID"), **kw)


def test_obfuscate_keeps_types_nulls_and_untouched_columns() -> None:
    result = _obf().transform([dict(r) for r in ROWS])
    a, b = result.rows
    assert isinstance(a["customer_id"], int) and a["customer_id"] != ROWS[0]["customer_id"]
    assert len(str(a["customer_id"])) == 8 and isinstance(a["dob"], date) and a["dob"] != ROWS[0]["dob"]
    assert a["email"].endswith("@example-mail.in") and a["full_name"] != ROWS[0]["full_name"]
    assert "[[PII:EMAIL:" in a["notes"] and "ann.k@" not in a["notes"]
    assert a["balance"] is ROWS[0]["balance"] or a["balance"] == ROWS[0]["balance"]       # not planned: unchanged
    assert b["full_name"] is None and b["email"] == "" and b["notes"] is None
    assert result.verification.passed and result.values_by_method["fpe_digits"] == 2


def test_determinism_and_key_sensitivity() -> None:
    key = generate_key_material("key")
    one, two = _obf(key).transform([dict(ROWS[0])]), _obf(key).transform([dict(ROWS[0])])
    assert one.rows == two.rows and _obf().transform([dict(ROWS[0])]).rows != one.rows


def test_l1_and_l2_agree_on_the_anchor_driven_date_shift() -> None:
    key = generate_key_material("key")
    l2 = _obf(key).transform([dict(ROWS[0])]).rows[0]["dob"]
    l1 = ENGINE.obfuscate(Payload(data={"customer_id": "10000001", "dob": "1988-03-14"}), "gdpr-pseudonymise",
                          RequestOptions(consistency={"key_material": key.model_dump(mode="json")})).result
    assert l2.isoformat() == l1["dob"]


def test_text_envelopes_reveal_without_hints() -> None:
    key = generate_key_material("key")
    out = _obf(key).transform([dict(ROWS[0])]).rows[0]["notes"]
    from dps.engine.transform.apply import ApplyOptions

    opts = ApplyOptions(policy=load_preset("gdpr-pseudonymise"), key=key, surrogates=None, fpe_enabled=True,
                        cipher_factory=lambda k, t: RotCipher())
    assert reveal(output=out, key=key, hints=[], options=opts) == ROWS[0]["notes"]


def test_synthesize_mode_needs_no_key_and_is_seeded() -> None:
    seed = generate_synthesis_seed()
    cols = [plan("full_name", "PERSON_NAME", "surrogate", "text"), plan("email", "EMAIL", "surrogate", "text")]

    def run(s):  # noqa: ANN001, ANN202
        return BatchTransformer(ENGINE, policy=load_preset("synthetic-realistic"), mode="synthesize", columns=cols,
                                shape=SHAPE, key=None, seed=s, locale="en_IN").transform([dict(ROWS[0])])

    first = run(seed)
    assert first.rows == run(seed).rows and first.rows[0]["full_name"] != ROWS[0]["full_name"]
    assert first.values_by_method == {"surrogate": 2} and run(generate_synthesis_seed()).rows != first.rows


def test_incompatible_and_overflow_fail_with_codes() -> None:
    with pytest.raises(IncompatibleMethod) as err:
        _obf(cols=[plan("customer_id", "CUSTOMER_ID", "det_token", "int")]).transform([dict(ROWS[0])])
    assert err.value.code == "incompatible_method" and err.value.path == "$.customer_id"
    short = plan("full_name", "PERSON_NAME", "det_token", "text").model_copy(update={"length": 12})
    with pytest.raises(IncompatibleMethod) as err2:
        _obf(cols=[short]).transform([dict(ROWS[0])])
    assert err2.value.code == "length_overflow" and "Ananya" not in str(err2.value)


def test_leakage_strict_fails_closed_and_warn_counts() -> None:
    leaky = [plan("full_name", "PERSON_NAME", "dictionary", "text", values=["Ananya Krishnan"])]
    with pytest.raises(LeakageError) as err:
        _obf(cols=leaky).transform([dict(ROWS[0])])
    assert err.value.hits == 1 and "Ananya" not in str(err.value)
    warned = _obf(cols=leaky, verify="warn").transform([dict(ROWS[0])])
    assert warned.verification.passed is False and len(warned.verification.hits) == 1


def test_short_originals_inside_other_outputs_are_not_leaks() -> None:
    rows = [{**ROWS[0], "full_name": "Anna", "email": "hannah.anna@x.in"}]
    cols = [plan("full_name", "PERSON_NAME", "fpe_text", "text"),
            plan("email", "EMAIL", "dictionary", "text", values=["hannah@x.in"])]   # output contains "anna"
    result = _obf(cols=cols).transform(rows)
    assert result.verification.passed and result.verification.checked == 2


def test_row_anchor_matches_the_l1_formula() -> None:
    from dps.engine.findings import hash_value

    assert row_anchor(ROWS[0], SHAPE, ("customer_id", "CUSTOMER_ID")) == f"CUSTOMER_ID:{hash_value('10000001')}"
    assert row_anchor(ROWS[0], SHAPE, None) == f"row:{hash_value('10000001')}"
```

- [ ] **Step 2: Run to confirm failure**

Run: `pytest -q tests/test_engine_touch_m2.py tests/test_batch.py`
Expected: FAIL — `child_path`, `ApplyOptions.anchor`, `PrivacyEngine.detector` and `dps.engine.batch` do not exist.

- [ ] **Step 3: M0 touch points**

- `dps/engine/jsonpath.py`: `def child_path(parent: str, key: str) -> str: """Path of ``key`` under ``parent`` (quoted when needed)."""; return parent + _segment(key)`.
- `dps/engine/transform/apply.py`: add `anchor: str | None = None` as the **last** field of `ApplyOptions` (dataclass default keeps M0 call sites valid); in `apply_plan` replace `anchor = anchor_for(document, plan)` with `anchor = options.anchor or anchor_for(document, plan)`.
- `dps/engine/service.py`: rename nothing; add
```python
    def detector(self, policy: Policy, locale: str) -> Detector:
        """The detector this engine would use for ``policy`` (L2 runs it over free-text columns)."""
        return self._detector(policy, locale)

    def surrogate_engine(self, seed: SynthesisSeed, locale: str) -> SurrogateEngine:
        """A fresh, seeded surrogate engine (bounded persona cache: create one per batch)."""
        return SurrogateEngine(seed_bytes(seed), default_locale=locale)
```
- `dps/engine/transform/methods.py` `_dictionary`: replace `hash(value) % len(values)` with `int.from_bytes(hashlib.sha256(value.encode("utf-8")).digest()[:8], "big") % len(values)` (keyed path unchanged).
- `dps/layers/connectors/postgres.py` `describe`: `path=child_path("$", r["attname"])`.

- [ ] **Step 4: Write `dps/engine/batch.py`**

```python
"""The L2/L3 → engine bridge (contract §6.4): rows in, transformed rows out, through ``apply_plan``.

Pure CPU: callers run :meth:`BatchTransformer.transform` in ``asyncio.to_thread``. Whole-value
findings come from the accepted snapshot (detector ``"snapshot"``); free-text columns are scanned
per row and planned with ``build_plan``. Text-column spans use envelope tokens, so a reveal needs no
stored hints. A new surrogate engine is made per batch, which bounds its persona cache.
"""
from __future__ import annotations

import dataclasses
import json
from collections import Counter
from typing import Any, Literal, Protocol

from pydantic import BaseModel

from dps.engine.findings import Finding, Locator, Plan, PlanEntry, hash_value
from dps.engine.planner import build_plan
from dps.engine.policy import MethodSpec, Policy
from dps.engine.service import PrivacyEngine
from dps.engine.synth.surrogates import SynthesisSeed
from dps.engine.taxonomy import entity_info, record_sensitivity
from dps.engine.transform.apply import ApplyOptions, apply_plan
from dps.engine.transform.crypto import KeyMaterial
from dps.engine.transform.methods import METHODS
from dps.engine.verify import Verification, normalise
from dps.layers.connectors.base import (
    ColumnInfo, IncompatibleValue, Row, TableShape, from_engine_text, to_engine_text,
)
from dps.observability import TRANSFORMS

KEYLESS_METHODS = frozenset({"surrogate", "keep", "null", "redact", "bucket", "generalize", "mask_partial",
                             "dictionary", "noise"})
LOCALE_BY_COUNTRY = {"US": "en_US", "IN": "en_IN", "GB": "en_GB", "UK": "en_GB", "CA": "en_CA", "MX": "es_MX"}
_FPE = frozenset({"fpe_digits", "fpe_text"})
EXACT_MIN, CONTAIN_MIN = 4, 8


class TabularSampler(Protocol):
    def sample_row(self, anchor: str) -> dict[str, Any]: ...


class ColumnPlan(BaseModel):
    path: str
    name: str
    entity_type: str
    method: MethodSpec
    text_column: bool
    reversible: bool
    kind: str
    length: int | None
    precision: int | None = None
    scale: int | None = None

    def output_column(self) -> ColumnInfo:
        return ColumnInfo(path=self.path, name=self.name, data_type="", kind=self.kind,  # type: ignore[arg-type]
                          length=self.length, precision=self.precision, scale=self.scale)


class BatchResult(BaseModel):
    rows: list[Row]
    values_by_method: dict[str, int]
    fallbacks: dict[str, int]
    verification: Verification
    skipped: int = 0
    values_by_column: dict[str, int] = {}


class LeakageError(Exception):
    def __init__(self, hits: int) -> None:
        super().__init__(f"leakage: {hits} original value(s) found in the transformed batch; nothing was written")
        self.hits = hits


class IncompatibleMethod(Exception):
    def __init__(self, path: str, code: str) -> None:
        super().__init__(f"{code}: the method output for {path} does not fit the target column")
        self.path, self.code = path, code


def row_anchor(row: Row, shape: TableShape, anchor: tuple[str, str] | None) -> str:
    """Contract §6.2: ``<ENTITY>:<hash>`` of the anchor column (the L1 formula), else ``row:<hash(key)>``."""
    if anchor is not None:
        name, entity = anchor
        value = to_engine_text(row.get(name), shape.column(name))
        if value:
            return f"{entity}:{hash_value(value)}"
    key_text = "|".join(to_engine_text(row.get(k), shape.column(k)) or "" for k in shape.key)
    return f"row:{hash_value(key_text)}"


def verify_row(originals: list[tuple[ColumnPlan, str, bool]], outputs: dict[str, str | None]) -> list[dict[str, str]]:
    """Per-row leakage sweep: exact fixed points (≥ 4 chars) and cross-field containment (≥ 8 chars).

    ``originals`` holds (column, original text, whole-value?) for every transformed value of the row;
    ``outputs`` the transformed texts of the planned columns only (kept columns are never searched).
    """
    haystack = normalise(" ".join(v for v in outputs.values() if v))
    hits: list[dict[str, str]] = []
    for column, original, whole in originals:
        needle = normalise(original)
        if whole and len(needle) >= EXACT_MIN and normalise(outputs.get(column.name) or "") == needle:
            hits.append({"path": column.path, "entity_type": column.entity_type, "rule": "fixed_point"})
        elif len(needle) >= CONTAIN_MIN and needle in haystack:
            hits.append({"path": column.path, "entity_type": column.entity_type, "rule": "contained"})
    return hits


class BatchTransformer:
    def __init__(self, engine: PrivacyEngine, *, policy: Policy, mode: Literal["obfuscate", "synthesize"],
                 columns: list[ColumnPlan], shape: TableShape, key: KeyMaterial | None,
                 seed: SynthesisSeed | None, locale: str, unclassified: str = "detect",
                 tabular: TabularSampler | None = None, anchor: tuple[str, str] | None = None,
                 verify: Literal["strict", "warn"] = "strict") -> None:
        if mode == "synthesize":
            if seed is None:
                raise ValueError("synthesize mode needs a synthesis seed")
            bad = [c.path for c in columns if c.method.method not in KEYLESS_METHODS and not c.text_column]
            if bad:
                raise IncompatibleMethod(bad[0], "incompatible_method")
        elif key is None:
            raise ValueError("obfuscate mode needs key material")
        self.engine, self.policy, self.mode = engine, policy, mode
        self.shape, self.key, self.seed, self.locale = shape, key, seed, locale
        self.anchor, self.verify_mode, self.unclassified, self.tabular = anchor, verify, unclassified, tabular
        self.whole = [c for c in columns if not c.text_column]
        self.text = [c for c in columns if c.text_column]
        self.detector = engine.detector(policy, locale) if self.text else None
        self.out_cols = {c.name: c.output_column() for c in columns}

    def _entry(self, finding: Finding, spec: MethodSpec) -> PlanEntry:
        method, params, reason = spec.method, dict(spec.params), "snapshot"
        if method in _FPE and not self.engine.fpe_enabled:
            method, params, reason = "det_token", {}, "snapshot|fallback:fpe_disabled"
        return PlanEntry(finding=finding, method=method, params=params, reason=reason,
                         reversible=METHODS[method].reversible)

    def _locale(self, row: Row) -> str:
        field = self.policy.locale.by_country_field
        name = field.split(".")[-1] if field else None
        value = str(row.get(name) or "").strip().upper() if name else ""
        return LOCALE_BY_COUNTRY.get(value, self.locale)

    def transform(self, rows: list[Row]) -> BatchResult:
        surrogates = self.engine.surrogate_engine(self.seed, self.locale) if self.seed is not None else None
        base = ApplyOptions(policy=self.policy, key=self.key, surrogates=surrogates,
                            fpe_enabled=self.engine.fpe_enabled, cipher_factory=self.engine.cipher_factory,
                            token_style="envelope", locale=self.locale)
        out_rows: list[Row] = []
        methods: Counter[str] = Counter()
        notes: Counter[str] = Counter()
        per_column: Counter[str] = Counter()
        hits: list[dict[str, str]] = []
        checked = 0
        for row in rows:
            doc: dict[str, str] = {}
            for col in self.whole + self.text:
                text = to_engine_text(row.get(col.name), self.shape.column(col.name))
                if text:
                    doc[col.name] = text
            entries: list[PlanEntry] = []
            originals: list[tuple[ColumnPlan, str, bool]] = []
            for col in self.whole:
                if col.name in doc and col.method.method != "keep":
                    finding = Finding(locator=Locator(path=col.path), entity_type=col.entity_type, score=1.0,
                                      detectors=["snapshot"], sensitivity=entity_info(col.entity_type).sensitivity,
                                      value_hash=hash_value(doc[col.name]))
                    entries.append(self._entry(finding, col.method))
                    originals.append((col, doc[col.name], True))
                    per_column[col.name] += 1
            for col in self.text:
                if col.name not in doc or self.detector is None:
                    continue
                found = [f.model_copy(update={"locator": Locator(path=col.path, start=f.locator.start,
                                                                  end=f.locator.end)})
                         for f in self.detector.detect(text=doc[col.name]).findings]
                for f in found:
                    originals.append((col, doc[col.name][f.locator.start or 0:f.locator.end or 0], False))
                per_column[col.name] += len(found)
                if self.mode == "obfuscate":
                    # every span uses the column's method (det_token): envelopes reveal without stored hints
                    entries += [self._entry(f, col.method) for f in found]
                elif found:
                    entries += build_plan(found, self.policy, "synthesize",
                                          fpe_enabled=self.engine.fpe_enabled).entries
            out = dict(row)
            if entries:
                plan = Plan(mode=self.mode, record_sensitivity=record_sensitivity(e.finding.entity_type for e in entries),
                            entries=entries)
                options = dataclasses.replace(base, anchor=row_anchor(row, self.shape, self.anchor),
                                              locale=self._locale(row))
                applied = apply_plan(plan=plan, options=options, data=doc)
                methods.update(applied.methods_used)
                notes.update(applied.notes)
                outputs: dict[str, str | None] = {}
                for name in doc:
                    value = applied.output.get(name)
                    outputs[name] = None if value is None else str(value)
                    try:
                        out[name] = from_engine_text(outputs[name], self.out_cols[name])
                    except IncompatibleValue as exc:
                        raise IncompatibleMethod(self.out_cols[name].path, exc.code) from None
                checked += len(originals)
                hits += verify_row(originals, outputs)
            out_rows.append(out)
        for method, count in methods.items():
            TRANSFORMS.labels(self.mode, method).inc(count)
        verification = Verification(passed=not hits, checked=checked, hits=hits[:50])
        if hits and self.verify_mode == "strict":
            raise LeakageError(len(hits))
        return BatchResult(rows=out_rows, values_by_method=dict(methods), fallbacks=dict(notes),
                           verification=verification, values_by_column=dict(per_column))


def plans_digest(columns: list[ColumnPlan]) -> str:
    """Stable JSON of the column plans (for the run plan / config hash)."""
    return json.dumps([c.model_dump(mode="json") for c in columns], sort_keys=True, separators=(",", ":"))
```
`Verification.hits` is typed `list[dict[str, str]]` in M0, so the extra `"rule"` key fits.

- [ ] **Step 5: Run tests, lint, types**

Run: `pytest -q tests/test_engine_touch_m2.py tests/test_batch.py tests/test_apply.py tests/test_service.py tests/test_methods.py && ruff check dps tests tools && mypy dps`
Expected: `4 + 9 = 13` new tests pass; M0's apply/service/methods tests stay green.

- [ ] **Step 6: Commit**

```bash
git add dps/engine/batch.py dps/engine/jsonpath.py dps/engine/transform/apply.py dps/engine/service.py dps/engine/transform/methods.py dps/layers/connectors/postgres.py tests/test_batch.py tests/test_engine_touch_m2.py
git commit -m "feat(engine): BatchTransformer bridge with row anchors and per-row leakage verification" -- dps/engine/batch.py dps/engine/jsonpath.py dps/engine/transform/apply.py dps/engine/service.py dps/engine/transform/methods.py dps/layers/connectors/postgres.py tests/test_batch.py tests/test_engine_touch_m2.py
```

---

### Task 17: Discovery, snapshots and the `discover_job` handler

**Files:**
- Create: `dps/layers/connectors/discovery.py`, `dps/snapshots.py`, `dps/worker.py` (only `HandlerContext`, `Handler`, `PartitionHandler` for now — Task 23 adds the consumer)
- Modify: `tests/fixtures/db.py` (`handler_ctx`, `engine`), `tests/fixtures/sources.py` (`source_secrets`, `register_source`)
- Test: `tests/test_discovery_classify.py` (offline), `tests/db/test_snapshots.py`, `tests/connectors/test_discover_job.py` (`db`, `pg_source`)

Public interface:
```python
# dps/worker.py (contract §3.5)
@dataclass
class HandlerContext:
    settings: Settings; engine: PrivacyEngine; worker_id: str; kek: bytes
    artefacts: ArtefactService; audit: AuditSink
Handler = Callable[[HandlerContext, ClaimedJob], Awaitable[None]]
PartitionHandler = Callable[[HandlerContext, JobInternal, ClaimedPartition], Awaitable[None]]

# dps/layers/connectors/discovery.py
JOIN_ENTITIES: frozenset[str]          # CUSTOMER_ID, EMPLOYEE_ID, CASE_ID — keys of joins, kept in synthesize mode
SHAPE_PRESERVING_METHODS: frozenset[str]; TEXT_OUTPUT_METHODS: frozenset[str]; NUMERIC_SAFE_METHODS: frozenset[str]
def value_shape(text: str) -> str      # letters→x/X, digits→9, other chars kept, ≤ 24 chars (+ "…")
def suggest_methods(entity: str | None, column: ColumnInfo, *, policy: Policy, fpe_enabled: bool,
                    min_digits: int | None, text_column: bool) -> tuple[MethodSpec, MethodSpec, str | None]
def classify_columns(shape: TableShape, rows: list[Row], *, policy: Policy, detector: Detector,
                     fpe_enabled: bool) -> list[ColumnClassification]
async def discover_job(ctx: HandlerContext, job: ClaimedJob) -> None

# dps/snapshots.py
class SnapshotStateError(Exception): code: str; status: int
def content_hash(columns: list[ColumnClassification], overrides: list[SnapshotOverride]) -> str
def apply_overrides(columns: list[ColumnClassification], overrides: list[SnapshotOverride], *,
                    policy: Policy | None, fpe_enabled: bool) -> list[ColumnClassification]
async def create_snapshot(snapshot: ClassificationSnapshot, *, conn: asyncpg.Connection | None = None) -> None
async def get_snapshot(tenant_id: str, snapshot_id: str, *, conn: asyncpg.Connection | None = None) -> ClassificationSnapshot | None
async def list_snapshots(tenant_id: str, *, connection_id: str | None = None, object_ref: str | None = None,
                         status: str | None = None, limit: int = 50, cursor: str | None = None) -> tuple[list[ClassificationSnapshot], str | None]
async def set_overrides(tenant_id: str, snapshot_id: str, overrides: list[SnapshotOverride], *,
                        fpe_enabled: bool) -> ClassificationSnapshot
async def accept_snapshot(tenant_id: str, snapshot_id: str, *, accepted_by: str) -> ClassificationSnapshot
```

**Classification (spec §7 "Discovery", contract §5.4).** For every column, each sampled non-null value is run through the M0 detector as a one-field document `{name: text}` (structural T0 on the column name, T1 patterns + checksums, T2 NER when enabled, T4 declared fields/custom recognizers). Per candidate entity `E`: `name_score(E)` = the structural score when the column name suggests `E` (else 0); `whole_rate(E)` = share of non-null values whose **whole-value** finding is `E`; `score(E) = noisy_or(name_score(E), whole_rate(E) × mean_score(E))`. The best `E` with `score ≥ policy.detect.min_score` is the column's entity. Otherwise, a `text`/`lob_text` column whose values carry **span** findings in ≥ 5 % of rows is a free-text column (`entity_type="FREE_TEXT"`, `text_column=true`, spans planned per row at run time). Binary/LOB/ObjectId columns are classified by name but forced `keep` (`type_note="binary_kept"`). The anchor is the first column (by M0's `ANCHOR_ENTITIES` priority, ties → key column, then column order) whose entity is an anchor entity. Only **shapes** of values are stored (`Xxxxx Xxxxxxx`, `9999 9999 9999`), never values; the sample is dropped when the snapshot is written.

**Method suggestion.** `suggest_methods` returns `(obfuscate, synthesize, type_note)`:
- no entity → `keep`, `keep`; binary → `keep`, `keep`, `binary_kept`; free text → `det_token` (envelopes), `surrogate`, `text_pipeline`;
- else `policy.method_for(E, "obfuscate")` / `policy.method_for(E, "synthesize")` with the planner's rules (FPE disabled → `det_token`, note `fpe_disabled`; synthesize keeps LOW-sensitivity entities when `keep_non_sensitive`);
- `fpe_digits` when the sample's shortest digit run is < 6 → `det_token`, note `fpe_domain_too_small`;
- type compatibility for the **source** kind: `int`/`decimal`/`float` columns accept only `fpe_digits`, `keep`, `null`, `noise`; otherwise obfuscate becomes `keep` for key columns (note `numeric_key_kept`), `null` when nullable, else `keep` (note `numeric_column`); `date`/`timestamp` columns accept `date_shift`, `keep`, `null`, `surrogate`, otherwise `date_shift` (note `date_column`); `uuid`/`bool` → `keep` (note `type_kept`);
- synthesize mode: key columns and `JOIN_ENTITIES` keep their values (referential integrity of synthetic copies; note `synthesize_key_kept`); numeric sensitive columns become `null` when nullable else `keep` (`synthesize_numeric`); every synthesize method must be keyless (`KEYLESS_METHODS` from Task 16), else `surrogate`.

- [ ] **Step 1: Write the failing tests**

`tests/test_discovery_classify.py`:
```python
from __future__ import annotations

from datetime import date

from dps.config import Settings
from dps.engine.policy import load_preset
from dps.engine.service import PrivacyEngine
from dps.jobs_models import ObjectRef
from dps.layers.connectors.base import ColumnInfo, TableShape
from dps.layers.connectors.discovery import classify_columns, suggest_methods, value_shape

ENGINE = PrivacyEngine(Settings(_env_file=None, llm_provider="stub", ner_enabled=False, fpe_enabled=False))
POLICY = load_preset("gdpr-pseudonymise")


def c(name: str, kind: str, **kw) -> ColumnInfo:  # noqa: ANN003
    return ColumnInfo(path=f"$.{name}", name=name, data_type=kw.pop("data_type", kind), kind=kind, **kw)


SHAPE = TableShape(object=ObjectRef(owner="bank", name="customers"), key=["customer_id"], key_source="primary_key",
                   columns=[c("customer_id", "int", is_key=True, nullable=False), c("full_name", "text", length=120),
                            c("email", "text", length=254), c("ssn", "text", length=11), c("dob", "date"),
                            c("segment", "text", length=16), c("risk_score", "int"), c("notes", "text"),
                            c("photo", "binary")])
ROWS = [{"customer_id": 10_000_000 + i, "full_name": f"Ananya Krishnan{i}", "email": f"user{i}@example-mail.in",
         "ssn": f"{900 + i % 9:03d}-45-{1000 + i:04d}", "dob": date(1980, 1 + i % 12, 1 + i % 28),
         "segment": ["retail", "premium"][i % 2], "risk_score": i % 900,
         "notes": f"Spoke to the client; reach them at user{i}@example-mail.in" if i % 3 == 0 else "Routine review",
         "photo": b"\x89PNG"} for i in range(60)]


def by_name(cols):  # noqa: ANN001, ANN202
    return {col.name: col for col in cols}


def test_columns_are_classified_with_shapes_not_values() -> None:
    cols = by_name(classify_columns(SHAPE, ROWS, policy=POLICY, detector=ENGINE.detector(POLICY, "en_IN"),
                                    fpe_enabled=False))
    assert cols["full_name"].entity_type == "PERSON_NAME" and "structural" in cols["full_name"].detectors
    assert cols["email"].entity_type == "EMAIL" and cols["email"].value_hit_rate > 0.9
    assert cols["dob"].entity_type == "DATE_OF_BIRTH" and cols["dob"].method.method == "date_shift"
    assert cols["notes"].text_column and cols["notes"].entity_type == "FREE_TEXT"
    assert cols["notes"].method.method == "det_token" and cols["notes"].synthesize_method.method == "surrogate"
    assert cols["segment"].entity_type is None and cols["segment"].method.method == "keep"
    assert cols["customer_id"].anchor and sum(col.anchor for col in cols.values()) == 1
    assert cols["photo"].method.method == "keep"
    assert all("Ananya" not in s and "example-mail" not in s for col in cols.values() for s in col.shapes)
    assert cols["email"].null_rate == 0.0 and cols["full_name"].distinct_ratio == 1.0


def test_numeric_and_key_rules() -> None:
    key = c("customer_id", "int", is_key=True, nullable=False)
    obf, syn, note = suggest_methods("CUSTOMER_ID", key, policy=POLICY, fpe_enabled=True, min_digits=8, text_column=False)
    assert obf.method in ("fpe_digits", "det_token", "keep") and syn.method == "keep" and note in (None, "synthesize_key_kept", "numeric_key_kept")
    small, _, note2 = suggest_methods("CUSTOMER_ID", key, policy=POLICY, fpe_enabled=True, min_digits=4,
                                      text_column=False)
    assert small.method == "keep" and note2 in ("fpe_domain_too_small", "numeric_key_kept")
    dob, _, _ = suggest_methods("DATE_OF_BIRTH", c("dob", "date"), policy=POLICY, fpe_enabled=False, min_digits=8,
                                text_column=False)
    assert dob.method == "date_shift"


def test_value_shape() -> None:
    assert value_shape("Ananya K-12") == "Xxxxxx X-99"
    assert value_shape("4111 1111 1111 1111") == "9999 9999 9999 9999"
    assert value_shape("x" * 40) == "x" * 24 + "…"
```

`tests/db/test_snapshots.py`:
```python
from __future__ import annotations

import pytest

from dps import snapshots
from dps.engine.policy import MethodSpec
from dps.jobs_models import SnapshotOverride
from tests.connectors.factories import make_snapshot

pytestmark = pytest.mark.db


async def test_create_list_override_accept_supersede(migrated_db) -> None:  # noqa: ANN001
    first = make_snapshot("t1", connection_id="c_1", object_ref="bank.customers")
    await snapshots.create_snapshot(first)
    patched = await snapshots.set_overrides("t1", first.id, [
        SnapshotOverride(path="$.email", method=MethodSpec(method="det_token")),
        SnapshotOverride(path="$.full_name", anchor=True)], fpe_enabled=False)
    email = next(col for col in patched.columns if col.name == "email")
    assert email.method.method == "det_token" and email.override and patched.content_hash != first.content_hash
    assert [col.name for col in patched.columns if col.anchor] == ["full_name"]
    accepted = await snapshots.accept_snapshot("t1", first.id, accepted_by="k1")
    assert accepted.status == "accepted" and accepted.accepted_by == "k1"
    with pytest.raises(snapshots.SnapshotStateError) as err:
        await snapshots.set_overrides("t1", first.id, [], fpe_enabled=False)
    assert (err.value.code, err.value.status) == ("snapshot_not_proposed", 409)
    second = make_snapshot("t1", connection_id="c_1", object_ref="bank.customers")
    await snapshots.create_snapshot(second)
    await snapshots.accept_snapshot("t1", second.id, accepted_by="k1")
    assert (await snapshots.get_snapshot("t1", first.id)).status == "superseded"
    items, _ = await snapshots.list_snapshots("t1", connection_id="c_1", status="accepted")
    assert [s.id for s in items] == [second.id] and await snapshots.get_snapshot("t2", second.id) is None
    with pytest.raises(snapshots.SnapshotStateError):
        await snapshots.set_overrides("t1", second.id, [SnapshotOverride(path="$.nope")], fpe_enabled=False)
```
`tests/connectors/factories.py` (test helper module): `make_snapshot(tenant, *, connection_id, object_ref, columns=None)` builds a `ClassificationSnapshot` for the customers shape of `tests/test_discovery_classify.py` with `id=new_id("snap")`, status `proposed`, the policy `gdpr-pseudonymise`, `content_hash=content_hash(columns, [])`; and `customers_shape()`.

`tests/connectors/test_discover_job.py`:
```python
from __future__ import annotations

import json

import pytest

from dps import db, jobs, snapshots
from dps.jobs import JobKind, JobPhase, JobState
from dps.jobs_models import DiscoverSpec, SealedSelection, Selection, redact_spec
from dps.layers.connectors.discovery import discover_job
from dps.layers.connectors.selection import parameterize_literals
from dps.secrets import seal_json

pytestmark = [pytest.mark.db, pytest.mark.integration, pytest.mark.pg_source]


async def test_discover_job_writes_a_value_free_proposed_snapshot(migrated_db, handler_ctx, source_pg,  # noqa: ANN001
                                                                  register_source, kek) -> None:
    obj = await source_pg.customers_table(rows=300)            # SourcePg helper: the demo customers DDL + rows
    conn = await register_source("t1")
    sel = parameterize_literals(Selection(where="country_code = 'IN'"), "postgres")
    spec = DiscoverSpec(connection_id=conn.id, objects=[obj], selection=sel, sample_rows=200)
    job_id = "j_" + "0" * 26
    job = await jobs.create_job(tenant_id="t1", kind=JobKind.discover, phase=JobPhase.discover, job_id=job_id,
                                spec=redact_spec(spec, engine="postgres"), connection_id=conn.id,
                                object_ref=obj.fqn(),
                                sealed_selection=seal_json(kek, SealedSelection(where=sel.where, params=sel.params),
                                                           aad=f"dps_job.spec:{job_id}"))
    [claimed] = await jobs.claim_jobs(handler_ctx.worker_id, kinds=(JobKind.discover,), batch=1)
    await discover_job(handler_ctx, claimed)
    done = await jobs.get_job("t1", job.id)
    assert done.state == JobState.succeeded and len(done.progress.snapshot_ids) == 1
    snap = await snapshots.get_snapshot("t1", done.progress.snapshot_ids[0])
    assert snap.status == "proposed" and snap.sample_rows > 0 and snap.object_ref == obj.fqn()
    names = {col.name: col for col in snap.columns}
    assert names["email"].entity_type == "EMAIL" and names["full_name"].entity_type == "PERSON_NAME"
    stored = json.dumps(snap.model_dump(mode="json"))
    sample_names = [r["full_name"] for r in await source_pg.admin.fetch(
        f'SELECT full_name FROM "{source_pg.schema}".customers LIMIT 20')]
    assert not any(n in stored for n in sample_names)
    async with db.acquire("t1") as c:
        spec_text = await c.fetchval("SELECT spec::text FROM dps.dps_job WHERE id = $1", job.id)
    assert "'IN'" not in spec_text and ":l1" in spec_text
```
Fixtures added (contract §12.2 names): `engine` (a `PrivacyEngine` with `fpe_enabled=True`; uses the real FF1 when `fastfpe` imports, else a rotation cipher via `cipher_factory`; a module-level `engine` fixture in an M0 test module still overrides it there), `handler_ctx` (`HandlerContext(settings=migrated_db with secret_dir=<tmp>, engine=engine, worker_id="test-worker:1:abcdef", kek=TEST_KEK, artefacts=ArtefactService(LocalObjectStore(tmp/"artefacts"), acquire=db.acquire_queue), audit=MemoryAuditSink())`), `source_secrets` (writes `dps/src/test-pg/{username,password}` = `dps_reader`, `dps/src/test-pg-rw/…` = `dps_writer`, `dps/src/test-pg-admin/…` = `postgres` under the same tmp secret dir), and `register_source(tenant, *, ref="dps/src/test-pg", roles={"source"}, name=None, tags=())` → a stored `Connection` pointing at the source-pg host/port, database `bank`. `SourcePg.customers_table(rows)` creates `customers` with the demo DDL (Task 15) and deterministic synthetic rows (the surrogate engine with a fixed seed — no real PII).

- [ ] **Step 2: Run to confirm failure**

Run: `pytest -q tests/test_discovery_classify.py tests/db/test_snapshots.py tests/connectors/test_discover_job.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'dps.layers.connectors.discovery'`.

- [ ] **Step 3: Write `dps/worker.py` (context only)** — the dataclass and the two type aliases exactly as in the interface block, with a module docstring stating that Task 23 adds `Consumer` and `main`.

- [ ] **Step 4: Write `dps/layers/connectors/discovery.py`**

```python
"""Discovery: sample → detect → per-column classification snapshot (spec §7, contract §5.4)."""
from __future__ import annotations

import asyncio
import re
from collections import Counter, defaultdict
from datetime import UTC, datetime

from dps import db, jobs
from dps.connections import get_connection, load_credentials
from dps.engine.batch import KEYLESS_METHODS
from dps.engine.detect.ensemble import Detector
from dps.engine.detect.structural import classify_key
from dps.engine.policy import MethodSpec, Policy
from dps.engine.taxonomy import Sensitivity, entity_info
from dps.engine.transform.apply import ANCHOR_ENTITIES
from dps.ids import new_id
from dps.jobs import ClaimedJob, JobPhase, JobState
from dps.jobs_models import ClassificationSnapshot, ColumnClassification, DiscoverSpec, SealedSelection, Selection
from dps.layers.connectors.base import NEVER_TRANSFORMED_KINDS, ColumnInfo, ConnectorError, Row, TableShape, to_engine_text
from dps.layers.connectors.registry import open_connector
from dps.layers.connectors.selection import SelectionError, validate_selection
from dps.observability import get_logger
from dps.secrets import SealError, open_json
from dps.snapshots import content_hash, create_snapshot
from dps.worker import HandlerContext

logger = get_logger(__name__)
JOIN_ENTITIES = frozenset({"CUSTOMER_ID", "EMPLOYEE_ID", "CASE_ID"})
SHAPE_PRESERVING_METHODS = frozenset({"fpe_digits", "fpe_text", "phone", "email", "date_shift", "mask_partial",
                                      "noise", "keep", "null", "surrogate"})
TEXT_OUTPUT_METHODS = frozenset({"det_token", "hmac_pseudonym", "redact", "bucket", "dictionary",
                                 "external_tokenize", "generalize"})
NUMERIC_SAFE_METHODS = frozenset({"fpe_digits", "keep", "null", "noise"})
DATE_SAFE_METHODS = frozenset({"date_shift", "keep", "null", "surrogate"})
TEXT_SPAN_MIN_RATE = 0.05
_DIGITS = re.compile(r"\d")


def value_shape(text: str) -> str:
    """Letters → x/X, digits → 9, other characters kept; at most 24 characters."""
    shaped = "".join("9" if ch.isdigit() else ("X" if ch.isupper() else "x") if ch.isalpha() else ch for ch in text)
    return shaped if len(shaped) <= 24 else shaped[:24] + "…"


def _noisy_or(*scores: float) -> float:
    acc = 1.0
    for s in scores:
        acc *= 1.0 - s
    return round(1.0 - acc, 3)


def suggest_methods(entity: str | None, column: ColumnInfo, *, policy: Policy, fpe_enabled: bool,
                    min_digits: int | None, text_column: bool) -> tuple[MethodSpec, MethodSpec, str | None]:
    """``(obfuscate, synthesize, type_note)`` for one column (rules in the task text)."""
    keep = MethodSpec(method="keep")
    if entity is None:
        return keep, keep, None
    if column.kind in NEVER_TRANSFORMED_KINDS:
        return keep, keep, "binary_kept"
    if text_column:
        return MethodSpec(method="det_token"), MethodSpec(method="surrogate"), "text_pipeline"
    obf, syn, note = policy.method_for(entity, "obfuscate"), policy.method_for(entity, "synthesize"), None
    if (policy.synthesize.keep_non_sensitive and entity not in policy.synthesize.overrides
            and entity_info(entity).sensitivity is Sensitivity.LOW):
        syn = keep
    if obf.method in ("fpe_digits", "fpe_text") and not fpe_enabled:
        obf, note = MethodSpec(method="det_token"), "fpe_disabled"
    if obf.method == "fpe_digits" and min_digits is not None and min_digits < 6:
        obf, note = MethodSpec(method="det_token"), "fpe_domain_too_small"
    if column.kind in ("int", "decimal", "float") and obf.method not in NUMERIC_SAFE_METHODS:
        if column.is_key:
            obf, note = keep, note or "numeric_key_kept"
        else:
            obf, note = (MethodSpec(method="null") if column.nullable else keep), note or "numeric_column"
    if column.kind in ("date", "timestamp") and obf.method not in DATE_SAFE_METHODS:
        obf, note = MethodSpec(method="date_shift", params={"window_days": 365}), "date_column"
    if column.kind in ("uuid", "bool") and obf.method != "keep":
        obf, note = keep, "type_kept"
    if column.is_key or entity in JOIN_ENTITIES:
        syn, note = keep, note or "synthesize_key_kept"
    elif column.kind in ("int", "decimal", "float") and syn.method not in ("keep", "null"):
        syn, note = (MethodSpec(method="null") if column.nullable else keep), note or "synthesize_numeric"
    if syn.method not in KEYLESS_METHODS:
        syn = MethodSpec(method="surrogate")
    return obf, syn, note


def classify_columns(shape: TableShape, rows: list[Row], *, policy: Policy, detector: Detector,
                     fpe_enabled: bool) -> list[ColumnClassification]:
    """One :class:`ColumnClassification` per column of ``shape`` (no values retained)."""
    out: list[ColumnClassification] = []
    for column in shape.columns:
        texts = [to_engine_text(r.get(column.name), column) for r in rows]
        values = [t for t in texts if t not in (None, "")]
        whole: dict[str, list[float]] = defaultdict(list)
        detectors: dict[str, set[str]] = defaultdict(set)
        span_rows, span_detectors = 0, set()
        for text in values:
            assert text is not None
            result = detector.detect(data={column.name: text})
            spans = [f for f in result.findings if not f.locator.whole]
            for f in result.findings:
                if f.locator.whole:
                    whole[f.entity_type].append(f.score)
                    detectors[f.entity_type].update(d.split(":")[0] for d in f.detectors)
            if spans:
                span_rows += 1
                span_detectors.update(d.split(":")[0] for f in spans for d in f.detectors)
        names = {k.entity_type: k.score for k in classify_key(column.path)}
        scores: dict[str, float] = {}
        for entity in set(whole) | set(names):
            hits = whole.get(entity, [])
            rate = len(hits) / len(values) if values else 0.0
            mean = sum(hits) / len(hits) if hits else 0.0
            scores[entity] = _noisy_or(names.get(entity, 0.0), rate * mean)
        best = max(scores.items(), key=lambda kv: kv[1], default=(None, 0.0))
        entity: str | None = best[0] if best[1] >= policy.detect.min_score else None
        text_column = False
        span_rate = span_rows / len(values) if values else 0.0
        if entity is None and column.kind in ("text", "lob_text") and span_rate >= TEXT_SPAN_MIN_RATE:
            entity, text_column = "FREE_TEXT", True
        hit_rate = span_rate if text_column else (len(whole.get(entity or "", [])) / len(values) if values else 0.0)
        used = span_detectors if text_column else (detectors.get(entity or "", set())
                                                   | ({"structural"} if entity in names else set()))
        digit_runs = [len(_DIGITS.findall(v or "")) for v in values]
        obf, syn, note = suggest_methods(entity, column, policy=policy, fpe_enabled=fpe_enabled,
                                         min_digits=min(digit_runs) if digit_runs else None, text_column=text_column)
        out.append(ColumnClassification(
            path=column.path, name=column.name, data_type=column.data_type, kind=column.kind, is_key=column.is_key,
            entity_type=entity, confidence=round(min(1.0, 0.5 + span_rate) if text_column else best[1], 3),
            sensitivity=entity_info(entity).sensitivity if entity else None, detectors=sorted(used),
            value_hit_rate=round(hit_rate, 3), null_rate=round(1 - len(values) / len(rows), 3) if rows else 0.0,
            distinct_ratio=round(len(set(values)) / len(values), 3) if values else 0.0,
            shapes=[s for s, _ in Counter(value_shape(v or "") for v in values).most_common(3)],
            text_column=text_column, anchor=False, suggested=obf, method=obf, synthesize_suggested=syn,
            synthesize_method=syn, type_note=note))
    for anchor_entity in ANCHOR_ENTITIES:
        candidates = [c for c in out if c.entity_type == anchor_entity]
        if candidates:
            chosen = sorted(candidates, key=lambda c: (not c.is_key, out.index(c)))[0]
            chosen.anchor = True
            break
    return out
```
`discover_job` (same module):
```python
async def discover_job(ctx: HandlerContext, job: ClaimedJob) -> None:
    """Sample, classify and store one proposed snapshot per object (values never leave the worker)."""
    spec = DiscoverSpec.model_validate(job.spec)
    try:
        sealed = (open_json(ctx.kek, job.spec_enc, SealedSelection, aad=f"dps_job.spec:{job.id}")
                  if job.spec_enc else SealedSelection())
    except SealError:
        await jobs.advance_job(ctx.worker_id, job.id, phase=JobPhase.discover, state=JobState.failed,
                               error_code="secret_unavailable", error="the sealed selection could not be opened")
        return
    selection = spec.selection.model_copy(update=sealed.model_dump())
    async with db.acquire_queue(job.tenant_id) as conn:
        connection = await get_connection(job.tenant_id, spec.connection_id, conn=conn)
        creds = await load_credentials(connection, settings=ctx.settings, kek=ctx.kek, conn=conn) if connection else None
    if connection is None or creds is None:
        await jobs.advance_job(ctx.worker_id, job.id, phase=JobPhase.discover, state=JobState.failed,
                               error_code="connection_missing", error="the connection no longer exists")
        return
    policy = ctx.engine.policy(spec.policy)
    locale = policy.locale.default or ctx.settings.default_locale
    connector = await open_connector(connection, ctx.settings, action="discover", kek=ctx.kek, job_id=job.id,
                                     credentials=creds)
    snapshot_ids: list[str] = []
    try:
        for index, obj in enumerate(spec.objects):
            shape = await connector.describe(obj)
            sel = selection if len(spec.objects) == 1 else Selection()
            validate_selection(sel, connection.engine, shape)   # type: ignore[arg-type]
            rows = await connector.sample(shape, sel, sel.params, spec.sample_rows or ctx.settings.discovery_sample_rows)
            columns = await asyncio.to_thread(classify_columns, shape, rows, policy=policy,
                                              detector=ctx.engine.detector(policy, locale),
                                              fpe_enabled=ctx.engine.fpe_enabled)
            sampled = len(rows)
            del rows
            snapshot = ClassificationSnapshot(
                id=new_id("snap"), tenant_id=job.tenant_id, job_id=job.id, connection_id=connection.id,
                engine=connection.engine, object_ref=shape.object.fqn(), status="proposed", policy_name=policy.name,
                policy_hash=policy.config_hash(), shape=shape, columns=columns, sample_rows=sampled, overrides=[],
                content_hash=content_hash(columns, []), created_at=datetime.now(UTC), accepted_at=None,
                accepted_by=None)
            async with db.acquire_queue(job.tenant_id) as conn:
                await create_snapshot(snapshot, conn=conn)
            snapshot_ids.append(snapshot.id)
            if not await jobs.advance_job(ctx.worker_id, job.id, phase=JobPhase.discover, state=JobState.running,
                                          progress={"snapshot_ids": snapshot_ids,
                                                    "stage": f"{index + 1}/{len(spec.objects)} objects"}):
                return                                       # canceled or reclaimed: stop quietly
        await jobs.advance_job(ctx.worker_id, job.id, phase=JobPhase.discover, state=JobState.succeeded,
                               progress={"outcome": "succeeded", "snapshot_ids": snapshot_ids}, purge_secret=True)
        logger.info("discover_done", job_id=job.id, count=len(snapshot_ids))
    except SelectionError as exc:
        await jobs.advance_job(ctx.worker_id, job.id, phase=JobPhase.discover, state=JobState.failed,
                               error_code=exc.code, error=str(exc))
    except ConnectorError as exc:
        if exc.retryable:
            await jobs.retry_job(ctx.worker_id, job.id, error_code=exc.code, error=str(exc),
                                 backoff_base=ctx.settings.job_retry_base_seconds,
                                 backoff_cap=ctx.settings.job_retry_max_seconds)
        else:
            await jobs.advance_job(ctx.worker_id, job.id, phase=JobPhase.discover, state=JobState.failed,
                                   error_code=exc.code, error=str(exc))
    finally:
        await connector.close()
```
(`advance_job(..., state=running)` keeps the lease and only records progress; `retry_job` dead-letters at `max_attempts`.)

- [ ] **Step 5: Write `dps/snapshots.py`**

Glue over `dps_snapshot` (`jsonb` codec on the pools does the JSON):
- `content_hash`: `sha256(json.dumps({"columns": [c.model_dump(mode="json") for c in columns], "overrides": [o.model_dump(mode="json") for o in overrides]}, sort_keys=True, separators=(",", ":")))`.
- `apply_overrides(columns, overrides, *, policy, fpe_enabled)`: start every column from its suggestions (`method = suggested`, `synthesize_method = synthesize_suggested`, `override = False`); an override path not in the snapshot → `SnapshotStateError("override_unknown_path", 422)`; `entity_type` changes re-run `suggest_methods` for the new entity (requires `policy`; a snapshot whose `policy_name` is not a preset needs an explicit `method` → `SnapshotStateError("override_method_required", 422)`); `method`/`synthesize_method` replace the column's; `text_column` sets the flag (and `entity_type="FREE_TEXT"` when set without an entity); an override with `anchor=true` clears `anchor` on every other column; every touched column gets `override=True`.
- `create_snapshot`: `INSERT … (shape, columns, overrides as jsonb)`.
- `set_overrides`: one transaction, `SELECT … FOR UPDATE`; status must be `proposed` (`snapshot_not_proposed`, 409); policy = `load_preset(policy_name)` when it exists else `None`; `UPDATE … SET columns, overrides, content_hash`.
- `accept_snapshot`: one transaction, row lock, status must be `proposed`; `UPDATE dps_snapshot SET status = 'superseded' WHERE tenant_id = $1 AND connection_id = $2 AND object_ref = $3 AND status = 'accepted'` then accept this one (`accepted_at = now()`, `accepted_by`). The partial unique index `dps_snapshot_one_accepted` makes a lost race fail loudly (`UniqueViolationError` → `SnapshotStateError("snapshot_conflict", 409)`).
- `list_snapshots`: keyset `(created_at, id)` newest first, optional filters.

- [ ] **Step 6: Run tests, lint, types**

Run: `pytest -q tests/test_discovery_classify.py tests/db/test_snapshots.py tests/connectors/test_discover_job.py && ruff check dps tests tools && mypy dps`
Expected: `3 + 1 + 1 = 5 passed`, clean. If `full_name` is not classified, check M0's `classify_key("$.full_name")` (structural score 0.9) — discovery relies on it.

- [ ] **Step 7: Commit**

```bash
git add dps/layers/connectors/discovery.py dps/snapshots.py dps/worker.py tests/test_discovery_classify.py tests/db/test_snapshots.py tests/connectors/test_discover_job.py tests/connectors/factories.py tests/fixtures/db.py tests/fixtures/sources.py
git commit -m "feat(discovery): per-column classification snapshots with shape-only examples; snapshot review and acceptance" -- dps/layers/connectors/discovery.py dps/snapshots.py dps/worker.py tests/test_discovery_classify.py tests/db/test_snapshots.py tests/connectors/test_discover_job.py tests/connectors/factories.py tests/fixtures/db.py tests/fixtures/sources.py
```

---

### Task 18: Targets — run validation, run plan, PK strategies, created DDL (`targets.py`)

**Files:**
- Create: `dps/layers/connectors/targets.py`
- Test: `tests/connectors/test_targets.py` (offline)

Public interface (contract §5.3; the run plan, PK helpers and DDL helpers are additions):
```python
class PrepareFailure(Exception): code: str; detail: str          # prepare-time failures (contract §5.3 list + snapshot_stale)
GroupKeys = tuple[str, str]                                       # (kid, seed_fingerprint) of a job group
class RunPlan(BaseModel):                                         # stored in dps_job.plan at prepare; value-free
    columns: list[ColumnPlan]; anchor: list[str] | None           # [column, entity]
    key: list[str]; projection: list[str]; target: ResolvedTarget
    source_object: str; policy_name: str; policy_hash: str; snapshot_id: str; snapshot_hash: str
    locale: str; batch_rows: int; same_table_append: bool = False
    prepare: dict[str, Any] = {}                                  # created / truncated / collisions / existing_rows
def validate_run(spec: RunSpec, *, principal: Principal, source_conn: Connection, target_conn: Connection | None,
                 snapshot: ClassificationSnapshot | None, settings: Settings, kek: bytes | None = None,
                 group: GroupKeys | None = None) -> None                       # raises SpecError(code, status)
def method_for_column(col: ColumnClassification, mode: str) -> MethodSpec
def build_column_plans(snapshot: ClassificationSnapshot, *, mode: str, write_mode: WriteMode,
                       pk_strategy: PkStrategy | None, projection: list[str]) -> list[ColumnPlan]
def created_column_type(column: ColumnInfo, plan: ColumnPlan | None) -> str
def worst_case_length(plan: ColumnPlan, source: ColumnInfo) -> int | None
def resolve_target(spec: RunSpec, *, job_id: str, source_conn: Connection, target_conn: Connection | None,
                   source_shape: TableShape, target_shape: TableShape | None, plans: list[ColumnPlan],
                   projection: list[str]) -> tuple[ResolvedTarget, list[ColumnPlan]]
def apply_pk_strategy(rows: list[Row], *, strategy: PkStrategy, key: list[str]) -> list[Row]
def is_same_table(spec: RunSpec) -> bool
TYPE_RE: re.Pattern[str]                                           # catalogue type strings allowed in created DDL
```

**Validation table** — `validate_run` checks, in this order, and raises `SpecError(code, status, value-free detail)`:

| # | Rule | Code · status |
|---|---|---|
| 1 | `DPS_JOB_KEK` unset | `kek_unconfigured` · 503 |
| 2 | source engine other than `postgres` (M2) | `engine_unavailable` · 422 |
| 3 | snapshot missing, of another connection/object, or not `accepted` | `snapshot_not_accepted` · 409 |
| 4 | source connection lacks role `source`; written connection lacks `target` (in_place/append write the source connection, which then needs both) | `connection_role` · 422 |
| 5 | `in_place`/`append` without `confirm_write: true` | `confirm_write_required` · 422 |
| 6 | `in_place`/`append` on a `production`-tagged written connection without the `admin` scope | `admin_scope_required` · 403 |
| 7 | `in_place`: `target.connection_id` set and ≠ source, `target.object` set and ≠ source object, or `selection.query` set | `in_place_invalid` · 422 |
| 8 | `in_place`: a key column's method in this mode is not `keep` | `pk_transform_in_place` · 422 |
| 9 | `append`: no `pk_strategy` | `pk_strategy_required` · 422 |
| 10 | `append` + `token` with a key method that is not reversible (always in synthesize) | `pk_strategy_invalid` · 422 |
| 11 | `append` to the **same** table with `token` or `prefix`, or with a key that is not one integer column | `pk_strategy_invalid` · 422 |
| 12 | `table`: no `connection_id`/`object`, a target connection that is not a database (`s3`/`pvc`), target = source object on the same connection, or `pk_strategy` set | `target_invalid` · 422 |
| 13 | `files`: target engine not `s3`/`pvc`, or `files` missing | `target_invalid` · 422 |
| 14 | `table`/`files`/`append+token`: a key column's method is neither `keep` nor reversible | `key_method_not_injective` · 422 |
| 15 | synthesize mode: a column method that needs key material (anything outside `KEYLESS_METHODS`) | `method_needs_key` · 422 |
| 16 | obfuscate mode: a free-text column whose method is not `det_token` | `text_method_invalid` · 422 |
| 17 | `group_id` with `field_scoped: true`; group set and the key material's `kid` ≠ group kid, its seed fingerprint ≠ group fingerprint, or obfuscate without `key_material` | `group_key_mismatch` · 409 |
| 18 | `options.tabular != "none"` (copula/MOSTLY arrive in M5; with obfuscate or in_place/append always invalid) | `tabular_invalid` · 422 |

Rules 11, 14–16 are additions (see "Open contract questions"). Rule 11 exists because a same-table append re-reads its own new rows unless every new key sorts above every selected key: only one integer key with `generate` (sequence ahead of the table, checked at prepare) or `offset` (the prepare clips the read range to `max(key) + 1`) guarantees that.

**Created tables.** `created_column_type`: kept column → the source's `data_type` (from `format_type`, validated against `TYPE_RE = ^[a-z][a-z0-9 _(),\[\]".]*$`, else `PrepareFailure("target_invalid")`); planned `text`/`char`/`lob_text`/`json`/`other` column → `text` (FPE/e-mail/phone outputs can fall back to `det_token`, which is longer); planned numeric/date column → the source type (preflight catches incompatible outputs). Key columns are `NOT NULL` and the table gets `PRIMARY KEY (key…)`; no FKs, indexes or defaults are copied (HANDOFF trap). **Existing targets.** Every projected column must exist with a compatible kind (`target_invalid`), the target must have a primary key or unique index on exactly the key columns for `table` upserts (`target_invalid`), and `worst_case_length(plan, source)` (for `det_token`: `ceil((4·L + 16)·8/5)` — UTF-8 worst case plus the AES-SIV tag in base32; for `hmac_pseudonym`: `params.length` or 16; others: `None`, measured by the preflight instead) must not exceed a bounded target length (`length_overflow`).

- [ ] **Step 1: Write the failing tests** `tests/connectors/test_targets.py`

```python
from __future__ import annotations

import pytest

from dps.auth import Principal
from dps.engine.policy import MethodSpec
from dps.jobs_models import RunSpec, SpecError
from dps.layers.connectors.base import ColumnInfo, PkOffset
from dps.layers.connectors.targets import (
    apply_pk_strategy, build_column_plans, created_column_type, resolve_target, validate_run, worst_case_length,
)
from tests.connectors.factories import accepted_snapshot, make_connection

ADMIN = Principal(key_id="k1", name="a", tenant_ids=["*"], scopes=["*"])
JOBS = Principal(key_id="k2", name="j", tenant_ids=["*"], scopes=["jobs"])
SRC = make_connection("c_src", roles=["source", "target"], tags=["production"])
UAT = make_connection("c_uat", roles=["target"])
S3 = make_connection("c_s3", engine="s3", roles=["target"], params={"bucket": "dps-exports"})
KEK = bytes(32)


def spec(**target) -> RunSpec:  # noqa: ANN003
    return RunSpec.model_validate({
        "mode": target.pop("mode", "obfuscate"), "snapshot_id": "snap_1",
        "source": {"connection_id": "c_src", "object": {"owner": "bank", "name": "customers"},
                   "selection": target.pop("selection", {})},
        "target": target, "purpose": {"ticket": "CHG-1", "legal_basis": "legitimate interest"}})


CASES = [
    (dict(write_mode="in_place"), JOBS, SRC, None, "confirm_write_required", 422),
    (dict(write_mode="in_place", confirm_write=True), JOBS, SRC, None, "admin_scope_required", 403),
    (dict(write_mode="in_place", confirm_write=True, connection_id="c_uat"), ADMIN, SRC, UAT, "in_place_invalid", 422),
    (dict(write_mode="in_place", confirm_write=True, selection={"query": "SELECT * FROM bank.customers"}),
     ADMIN, SRC, None, "in_place_invalid", 422),
    (dict(write_mode="append", confirm_write=True), ADMIN, SRC, None, "pk_strategy_required", 422),
    (dict(write_mode="append", confirm_write=True, pk_strategy={"kind": "token"}), ADMIN, SRC, None,
     "pk_strategy_invalid", 422),
    (dict(write_mode="append", confirm_write=True, pk_strategy={"kind": "offset", "prefix": "X"}), ADMIN, SRC, None,
     "pk_strategy_invalid", 422),
    (dict(write_mode="table"), ADMIN, SRC, None, "target_invalid", 422),
    (dict(write_mode="table", connection_id="c_src", object={"owner": "bank", "name": "customers"}), ADMIN, SRC, SRC,
     "target_invalid", 422),
    (dict(write_mode="table", connection_id="c_uat", object={"owner": "bank_uat", "name": "customers"},
          pk_strategy={"kind": "generate"}), ADMIN, SRC, UAT, "target_invalid", 422),
    (dict(write_mode="files", connection_id="c_uat"), ADMIN, SRC, UAT, "target_invalid", 422),
    (dict(write_mode="table", connection_id="c_s3", object={"owner": "x", "name": "y"}), ADMIN, SRC, S3,
     "target_invalid", 422),
    (dict(write_mode="table", connection_id="c_uat", object={"owner": "bank_uat", "name": "customers"},
          mode="synthesize"), ADMIN, SRC, UAT, None, None),
]


@pytest.mark.parametrize("target,principal,src,tgt,code,status", CASES)
def test_validation_table(target, principal, src, tgt, code, status, settings) -> None:  # noqa: ANN001
    snap = accepted_snapshot("c_src", "bank.customers")
    if code is None:
        validate_run(spec(**target), principal=principal, source_conn=src, target_conn=tgt, snapshot=snap,
                     settings=settings, kek=KEK)
        return
    with pytest.raises(SpecError) as err:
        validate_run(spec(**target), principal=principal, source_conn=src, target_conn=tgt, snapshot=snap,
                     settings=settings, kek=KEK)
    assert (err.value.code, err.value.status) == (code, status)


def test_kek_snapshot_key_and_text_rules(settings) -> None:  # noqa: ANN001
    target = dict(write_mode="table", connection_id="c_uat", object={"owner": "bank_uat", "name": "customers"})
    snap = accepted_snapshot("c_src", "bank.customers")
    with pytest.raises(SpecError, match="DPS_JOB_KEK") as e1:
        validate_run(spec(**target), principal=ADMIN, source_conn=SRC, target_conn=UAT, snapshot=snap,
                     settings=settings, kek=None)
    assert e1.value.code == "kek_unconfigured"
    with pytest.raises(SpecError) as e2:
        validate_run(spec(**target), principal=ADMIN, source_conn=SRC, target_conn=UAT,
                     snapshot=snap.model_copy(update={"status": "proposed"}), settings=settings, kek=KEK)
    assert e2.value.code == "snapshot_not_accepted"
    hmac_key = accepted_snapshot("c_src", "bank.customers", key_method=MethodSpec(method="hmac_pseudonym"))
    with pytest.raises(SpecError) as e3:
        validate_run(spec(**target), principal=ADMIN, source_conn=SRC, target_conn=UAT, snapshot=hmac_key,
                     settings=settings, kek=KEK)
    assert e3.value.code == "key_method_not_injective"
    bad_text = accepted_snapshot("c_src", "bank.customers", notes_method=MethodSpec(method="fpe_text"))
    with pytest.raises(SpecError) as e4:
        validate_run(spec(**target), principal=ADMIN, source_conn=SRC, target_conn=UAT, snapshot=bad_text,
                     settings=settings, kek=KEK)
    assert e4.value.code == "text_method_invalid"


def test_column_plans_per_write_mode() -> None:
    snap = accepted_snapshot("c_src", "bank.customers", key_method=MethodSpec(method="fpe_digits"))
    every = [c.name for c in snap.shape.columns]
    table = {p.name for p in build_column_plans(snap, mode="obfuscate", write_mode="table", pk_strategy=None,
                                                projection=every)}
    assert "customer_id" in table and "country_code" not in table and "notes" in table
    gen = {p.name for p in build_column_plans(snap, mode="obfuscate", write_mode="append",
                                              pk_strategy={"kind": "generate"}, projection=every)}
    assert "customer_id" not in gen
    narrowed = {p.name for p in build_column_plans(snap, mode="obfuscate", write_mode="table", pk_strategy=None,
                                                   projection=["customer_id", "email"])}
    assert narrowed <= {"customer_id", "email"}


def test_created_types_and_worst_case() -> None:
    name = ColumnInfo(path="$.full_name", name="full_name", data_type="character varying(120)", kind="text", length=120)
    dob = ColumnInfo(path="$.dob", name="dob", data_type="date", kind="date")
    snap = accepted_snapshot("c_src", "bank.customers")
    plans = {p.name: p for p in build_column_plans(snap, mode="obfuscate", write_mode="table", pk_strategy=None,
                                                   projection=[c.name for c in snap.shape.columns])}
    assert created_column_type(name, plans["full_name"]) == "text"
    assert created_column_type(name, None) == "character varying(120)"
    assert created_column_type(dob, plans.get("dob")) == "date"
    token = plans["full_name"].model_copy(update={"method": MethodSpec(method="det_token")})
    assert worst_case_length(token, name) == 794


def test_pk_strategies_and_existing_target_checks() -> None:
    rows = [{"customer_id": 10_000_001, "x": 1}]
    assert apply_pk_strategy([dict(r) for r in rows], strategy=PkOffset(offset=90_000_000),
                             key=["customer_id"])[0]["customer_id"] == 100_000_001
    assert apply_pk_strategy([{"doc_ref": "K-1"}], strategy=PkOffset(prefix="S-"), key=["doc_ref"])[0]["doc_ref"] == "S-K-1"
    snap = accepted_snapshot("c_src", "bank.customers")
    projection = [c.name for c in snap.shape.columns]
    plans = build_column_plans(snap, mode="obfuscate", write_mode="table", pk_strategy=None, projection=projection)
    missing = snap.shape.model_copy(update={"columns": snap.shape.columns[:-2]})
    from dps.layers.connectors.targets import PrepareFailure

    with pytest.raises(PrepareFailure) as err:
        resolve_target(spec(write_mode="table", connection_id="c_uat", object={"owner": "bank_uat", "name": "c"},
                            create_if_missing=False), job_id="j_1", source_conn=SRC, target_conn=UAT,
                       source_shape=snap.shape, target_shape=missing, plans=plans, projection=projection)
    assert err.value.code == "target_invalid"
    resolved, out = resolve_target(spec(write_mode="table", connection_id="c_uat",
                                        object={"owner": "bank_uat", "name": "c"}), job_id="j_1", source_conn=SRC,
                                   target_conn=UAT, source_shape=snap.shape, target_shape=None, plans=plans,
                                   projection=projection)
    assert resolved.column_types["full_name"] == "text" and resolved.key == ["customer_id"]
    assert next(p for p in out if p.name == "full_name").length is None      # created as text: unbounded
```
`tests/connectors/factories.py` gains `make_connection(id, *, engine="postgres", roles, tags=(), params=None)` (an in-memory `Connection`) and `accepted_snapshot(connection_id, object_ref, *, key_method=MethodSpec(method="keep"), notes_method=MethodSpec(method="det_token"))` — the customers snapshot with `customer_id` (key, int), `full_name` (fpe_text), `email` (email), `dob` (date_shift), `country_code` (kept), `notes` (free text) — status `accepted`.

- [ ] **Step 2: Run to confirm failure**, **Step 3: write `targets.py`**

The module is the validation table above, the helpers below, and nothing else (no I/O):
```python
def method_for_column(col: ColumnClassification, mode: str) -> MethodSpec:
    if mode == "synthesize":
        return col.synthesize_method or col.synthesize_suggested or MethodSpec(method="surrogate")
    return col.method


def build_column_plans(snapshot: ClassificationSnapshot, *, mode: str, write_mode: WriteMode,
                       pk_strategy: PkStrategy | dict[str, Any] | None, projection: list[str]) -> list[ColumnPlan]:
    kind = (pk_strategy.get("kind") if isinstance(pk_strategy, dict) else getattr(pk_strategy, "kind", None))
    plans: list[ColumnPlan] = []
    wanted = set(projection)
    for col in snapshot.columns:
        if col.name not in wanted:
            continue
        method = method_for_column(col, mode)
        if col.is_key and (write_mode == "in_place" or (write_mode == "append" and kind in ("generate", "offset"))):
            continue                       # the key is never rewritten in place; append keys come from the strategy
        if not col.text_column and (col.entity_type is None or method.method == "keep"):
            continue
        info = snapshot.shape.column(col.name)
        plans.append(ColumnPlan(path=col.path, name=col.name, entity_type=col.entity_type or "FREE_TEXT",
                                method=method, text_column=col.text_column,
                                reversible=METHODS[method.method].reversible, kind=info.kind, length=info.length,
                                precision=info.precision, scale=info.scale))
    return plans


def worst_case_length(plan: ColumnPlan, source: ColumnInfo) -> int | None:
    if plan.method.method == "det_token" and source.length:
        return math.ceil((4 * source.length + 16) * 8 / 5)
    if plan.method.method == "hmac_pseudonym":
        return int(plan.method.params.get("length", 16))
    return None


def apply_pk_strategy(rows: list[Row], *, strategy: PkStrategy, key: list[str]) -> list[Row]:
    if isinstance(strategy, PkOffset):
        for row in rows:
            for k in key:
                row[k] = row[k] + strategy.offset if strategy.offset is not None else f"{strategy.prefix}{row[k]}"
    elif isinstance(strategy, PkGenerate):
        for row in rows:
            for k in key:
                row.pop(k, None)
    return rows
```
`resolve_target` builds the `ResolvedTarget` per write mode (table: `object`, `table_load`, `create_if_missing`, `column_types` from `created_column_type` when `target_shape is None`; in_place: the source object, `write_columns` = plan names; append: `object = spec.target.object or source object`, `generated_key = strategy is generate`, `write_columns` = projection minus key when generated; files: `object=None`, `files=spec.target.files`), runs the existing-target checks, and returns the plans with `kind`/`length`/`precision`/`scale` replaced by the **target** column's (created text columns → `kind="text", length=None`).

- [ ] **Step 4: Run tests, lint, types**

Run: `pytest -q tests/connectors/test_targets.py && ruff check dps tests tools && mypy dps`
Expected: `13 + 4 = 17 passed` (`test_validation_table` ×13), clean.

- [ ] **Step 5: Commit**

```bash
git add dps/layers/connectors/targets.py tests/connectors/test_targets.py tests/connectors/factories.py
git commit -m "feat(connectors): run validation for all write modes, column plans, PK strategies and created-table types" -- dps/layers/connectors/targets.py tests/connectors/test_targets.py tests/connectors/factories.py
```

---

### Task 19: PostgreSQL writer — in_place, append, table, ledger, collision precheck

**Files:**
- Modify: `dps/layers/connectors/postgres.py` (add `PostgresWriter`, `PostgresConnector.writer`)
- Test: `tests/connectors/test_postgres_write.py` (`pg_source`)

Public interface:
```python
class LedgerConflict(ConnectorError)      # code "ledger_conflict": another holder advanced this partition
class PkCollision(ConnectorError)         # code "pk_collision": a unique violation on append
LEDGER_TABLE = "dps_write_ledger"         # PG folds the contract's DPS_WRITE_LEDGER to lower case
class PostgresWriter:                      # implements BatchWriter (contract §4.2)
    def __init__(self, connector: PostgresConnector, target: ResolvedTarget, source_shape: TableShape) -> None
async def PostgresConnector.writer(self, target: ResolvedTarget, source_shape: TableShape) -> BatchWriter
```

**Atomicity and resume (contract §4.2).** For `in_place` and `append`, every `write_batch` is **one** transaction on the read-write pool: lock the partition's ledger row (`SELECT batch_no … FOR UPDATE`), compare it with the batch number this writer last saw (`read_ledger` sets it at partition start; each successful batch advances it) — a mismatch means another holder wrote meanwhile → `LedgerConflict`, nothing written — then apply the rows, then upsert the ledger mark `(job_id, partition_no) → (batch_no, last_key, rows)`, then commit. The ledger mark is therefore always exactly the last applied batch, so a resumed partition continues from it and never applies a batch twice (re-obfuscating an obfuscated row would corrupt it irreversibly). `table` writes stage rows in a session temp table via binary `COPY` and `INSERT … SELECT … ON CONFLICT (key) DO UPDATE` — idempotent, so `table` needs no ledger. `in_place` updates are one set-based statement: `UPDATE t SET c = u.c … FROM unnest($1::type[], …) AS u(…) WHERE t.key = u.key`; key columns are never in the `SET` list. `append` inserts with binary `COPY` (`copy_records_to_table`); a unique violation is `PkCollision`.

- [ ] **Step 1: Write the failing tests** `tests/connectors/test_postgres_write.py`

```python
from __future__ import annotations

import pytest

from dps.layers.connectors.base import LedgerMark, PkGenerate, PkOffset, ResolvedTarget, encode_key
from dps.layers.connectors.postgres import LedgerConflict, PkCollision, PostgresConnector
from dps.layers.connectors.targets import PrepareFailure

pytestmark = [pytest.mark.integration, pytest.mark.pg_source]


async def _rw(source_pg, settings, user: str = "dps_writer") -> PostgresConnector:  # noqa: ANN001
    c = PostgresConnector(source_pg.connection(user=user, roles=("source", "target")), source_pg.credentials(user),
                          settings, action="run", job_id="j_w")
    await c.open()
    return c


def _target(source_pg, shape, **kw) -> ResolvedTarget:  # noqa: ANN001, ANN003
    base = dict(job_id="j_w", write_mode="table", connection_id="c", engine="postgres",
                object=shape.object.model_copy(update={"name": "copy"}), key=shape.key,
                write_columns=[c.name for c in shape.columns], target_shape=None,
                column_types={c.name: c.data_type for c in shape.columns})
    base.update(kw)
    return ResolvedTarget(**base)


def _mark(p: int, b: int, last: int, shape, rows: int) -> LedgerMark:  # noqa: ANN001
    return LedgerMark(job_id="j_w", partition_no=p, batch_no=b, last_key=encode_key((last,), shape.key_columns()),
                      rows=rows)


async def test_table_create_truncate_and_idempotent_upsert(source_pg, settings) -> None:  # noqa: ANN001
    obj = await source_pg.table("src", "id bigint PRIMARY KEY, name text", [(i, f"n{i}") for i in range(10)])
    c = await _rw(source_pg, settings)
    try:
        shape = await c.describe(obj)
        writer = await c.writer(_target(source_pg, shape), shape)
        report = await writer.prepare(dry_run=False)
        assert report.created
        rows = [{"id": i, "name": f"t{i}"} for i in range(10)]
        assert await writer.write_batch(rows[:5], mark=_mark(0, 1, 4, shape, 5)) == 5
        assert await writer.write_batch(rows[:5], mark=_mark(0, 1, 4, shape, 5)) == 5   # replay: upsert, no dupes
        assert await writer.write_batch(rows[5:], mark=_mark(0, 2, 9, shape, 10)) == 5
        assert await source_pg.admin.fetchval(f'SELECT count(*) FROM "{source_pg.schema}".copy') == 10
        again = await c.writer(_target(source_pg, shape), shape)
        assert (await again.prepare(dry_run=False)).truncated
        assert await source_pg.admin.fetchval(f'SELECT count(*) FROM "{source_pg.schema}".copy') == 0
    finally:
        await c.close()


async def test_in_place_updates_only_planned_columns_with_a_ledger(source_pg, settings) -> None:  # noqa: ANN001
    obj = await source_pg.table("people", "id bigint PRIMARY KEY, name text, city text",
                                [(i, f"n{i}", "Pune") for i in range(6)])
    c = await _rw(source_pg, settings)
    try:
        shape = await c.describe(obj)
        writer = await c.writer(_target(source_pg, shape, write_mode="in_place", object=obj, write_columns=["name"],
                                        target_shape=shape), shape)
        assert (await writer.prepare(dry_run=False)).ledger
        assert await writer.read_ledger("j_w", 0) is None
        n = await writer.write_batch([{"id": i, "name": f"x{i}", "city": "IGNORED"} for i in range(3)],
                                     mark=_mark(0, 1, 2, shape, 3))
        assert n == 3
        rows = await source_pg.admin.fetch(f'SELECT id, name, city FROM "{source_pg.schema}".people ORDER BY id')
        assert [r["name"] for r in rows] == ["x0", "x1", "x2", "n3", "n4", "n5"] and {r["city"] for r in rows} == {"Pune"}
        mark = await writer.read_ledger("j_w", 0)
        assert (mark.batch_no, mark.rows) == (1, 3)
        stale = await c.writer(_target(source_pg, shape, write_mode="in_place", object=obj, write_columns=["name"],
                                       target_shape=shape), shape)
        with pytest.raises(LedgerConflict):                      # a zombie that never saw batch 1
            await stale.write_batch([{"id": 3, "name": "zz"}], mark=_mark(0, 1, 3, shape, 1))
        assert await source_pg.admin.fetchval(f'SELECT name FROM "{source_pg.schema}".people WHERE id = 3') == "n3"
        assert await writer.clear_ledger("j_w") == 1
    finally:
        await c.close()


async def test_append_offset_precheck_and_collision(source_pg, settings) -> None:  # noqa: ANN001
    obj = await source_pg.table("acct", "id bigint PRIMARY KEY, v text", [(i, "a") for i in (1, 2, 1001)])
    c = await _rw(source_pg, settings)
    try:
        shape = await c.describe(obj)
        writer = await c.writer(_target(source_pg, shape, write_mode="append", object=obj, same_as_source=True,
                                        pk_strategy=PkOffset(offset=1000), target_shape=shape), shape)
        report = await writer.prepare(dry_run=False)
        assert report.append_upper == 1002

        async def keys():  # noqa: ANN202
            yield [(1001,), (1002,)]

        assert await writer.precheck_collisions(keys()) == 1
        await writer.read_ledger("j_w", 0)
        assert await writer.write_batch([{"id": 1002, "v": "b"}], mark=_mark(0, 1, 2, shape, 1)) == 1
        with pytest.raises(PkCollision):
            await writer.write_batch([{"id": 1001, "v": "dup"}], mark=_mark(0, 2, 2, shape, 2))
    finally:
        await c.close()


async def test_append_generate_uses_identity_and_refuses_a_lagging_sequence(source_pg, settings) -> None:  # noqa: ANN001
    obj = await source_pg.table("gen", "id bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY, v text", [])
    await source_pg.admin.execute(f'INSERT INTO "{source_pg.schema}".gen (v) VALUES (\'a\'), (\'b\')')
    c = await _rw(source_pg, settings)
    try:
        shape = await c.describe(obj)
        writer = await c.writer(_target(source_pg, shape, write_mode="append", object=obj, pk_strategy=PkGenerate(),
                                        generated_key=True, write_columns=["v"], target_shape=shape), shape)
        await writer.prepare(dry_run=False)
        await writer.read_ledger("j_w", 0)
        await writer.write_batch([{"v": "c"}], mark=_mark(0, 1, 2, shape, 1))
        assert await source_pg.admin.fetchval(f'SELECT max(id) FROM "{source_pg.schema}".gen') == 3
        await source_pg.admin.execute(f'INSERT INTO "{source_pg.schema}".gen (id, v) VALUES (100, \'x\')')
        with pytest.raises(PrepareFailure) as err:
            await (await c.writer(_target(source_pg, shape, write_mode="append", object=obj, pk_strategy=PkGenerate(),
                                          generated_key=True, write_columns=["v"], target_shape=shape),
                                  shape)).prepare(dry_run=False)
        assert err.value.code == "pk_strategy_invalid"
    finally:
        await c.close()


async def test_dry_run_and_privilege_failures(source_pg, settings) -> None:  # noqa: ANN001
    obj = await source_pg.table("dry", "id bigint PRIMARY KEY, v text", [(1, "a")])
    rw = await _rw(source_pg, settings)
    try:
        shape = await rw.describe(obj)
        report = await (await rw.writer(_target(source_pg, shape), shape)).prepare(dry_run=True)
        assert report.created is False
        assert await source_pg.admin.fetchval(f"SELECT to_regclass('\"{source_pg.schema}\".copy')") is None
    finally:
        await rw.close()
    ro = await _rw(source_pg, settings, user="dps_reader")
    try:
        with pytest.raises(PrepareFailure) as err:
            await (await ro.writer(_target(source_pg, shape, write_mode="in_place", object=obj, write_columns=["v"],
                                           target_shape=shape), shape)).prepare(dry_run=False)
        assert err.value.code == "target_invalid"
    finally:
        await ro.close()
```

- [ ] **Step 2: Run to confirm failure**, then **Step 3: write the writer**

```python
LEDGER_TABLE = "dps_write_ledger"
_TYPE_RE = re.compile(r'^[a-z][a-z0-9 _(),\[\]".]*$')


class LedgerConflict(ConnectorError):
    def __init__(self) -> None:
        super().__init__("ledger_conflict", "another worker advanced this partition")


class PkCollision(ConnectorError):
    def __init__(self) -> None:
        super().__init__("pk_collision", "an appended key already exists in the target")


class PostgresWriter:
    def __init__(self, connector: PostgresConnector, target: ResolvedTarget, source_shape: TableShape) -> None:
        self.c, self.t, self.src = connector, target, source_shape
        obj = target.object
        assert obj is not None
        self.owner, self.name = obj.owner or "public", obj.name
        self.fqn = f"{_q(self.owner)}.{_q(self.name)}"
        self.ledger = f"{_q(target.ledger_owner or self.owner)}.{LEDGER_TABLE}"
        self._seen: dict[int, int] = {}
        digest = hashlib.sha256(f"{target.job_id}:{self.fqn}".encode()).hexdigest()[:10]
        self.stage = f"dps_stage_{digest}"
        self.types: dict[str, str] = {}

    def _type(self, column: str) -> str:
        t = self.types.get(column) or (self.t.target_shape.column(column).data_type if self.t.target_shape
                                       else self.t.column_types[column])
        if not _TYPE_RE.fullmatch(t):
            raise PrepareFailure("target_invalid", "a column type cannot be used in generated SQL")
        return t

    async def _privilege(self, conn: asyncpg.Connection, what: str) -> bool:
        return bool(await conn.fetchval("SELECT has_table_privilege(current_user, $1, $2)",
                                        f"{self.fqn}", what))

    async def prepare(self, *, dry_run: bool) -> PrepareReport:
        pool = await self.c.write_pool()
        report = PrepareReport()
        mode = self.t.write_mode
        async with pool.acquire() as conn:
            exists = await conn.fetchval("SELECT to_regclass($1) IS NOT NULL", self.fqn)
            if mode == "table":
                if not exists:
                    if not self.t.create_if_missing:
                        raise PrepareFailure("target_invalid", "the target table does not exist")
                    cols = ", ".join(f"{_q(n)} {self._type(n)}{' NOT NULL' if n in self.t.key else ''}"
                                     for n in self.t.write_columns)
                    keys = ", ".join(_q(k) for k in self.t.key)
                    if not dry_run:
                        await conn.execute(f"CREATE TABLE {self.fqn} ({cols}, PRIMARY KEY ({keys}))")
                        report.created = True
                    report.checks.append({"name": "create_target", "ok": True})
                else:
                    for priv in ("INSERT", "UPDATE", "SELECT"):
                        if not await self._privilege(conn, priv):
                            raise PrepareFailure("target_invalid", f"missing {priv} privilege on the target")
                    report.existing_rows = await conn.fetchval(
                        "SELECT GREATEST(reltuples, 0)::bigint FROM pg_class WHERE oid = $1::regclass", self.fqn)
                    if self.t.table_load == "truncate_insert" and not dry_run:
                        await conn.execute(f"TRUNCATE {self.fqn}")
                        report.truncated = True
            else:
                if not exists:
                    raise PrepareFailure("target_invalid", "the target table does not exist")
                needed = "UPDATE" if mode == "in_place" else "INSERT"
                if not await self._privilege(conn, needed) or not await self._privilege(conn, "SELECT"):
                    raise PrepareFailure("target_invalid", f"missing {needed} privilege on the target")
                if not dry_run:
                    try:
                        await conn.execute(
                            f"CREATE TABLE IF NOT EXISTS {self.ledger} (job_id text NOT NULL, partition_no int NOT NULL, "
                            "batch_no int NOT NULL, last_key jsonb NOT NULL, rows bigint NOT NULL DEFAULT 0, "
                            "updated_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY (job_id, partition_no))")
                    except asyncpg.InsufficientPrivilegeError:
                        raise PrepareFailure("target_invalid", "cannot create the write ledger table") from None
                    report.ledger = True
                if mode == "append" and self.t.same_as_source:
                    k = _q(self.t.key[0])
                    top = await conn.fetchval(f"SELECT max({k}) FROM {self.fqn}")
                    report.append_upper = int(top) + 1 if top is not None else None
                if mode == "append" and self.t.generated_key:
                    await self._check_generated(conn)
        return report

    async def _check_generated(self, conn: asyncpg.Connection) -> None:
        key = self.t.key[0]
        info = self.t.target_shape.column(key) if self.t.target_shape else None
        if info is None or not (info.is_identity or info.has_default):
            raise PrepareFailure("pk_strategy_invalid", "the key has no identity, default or sequence")
        seq = await conn.fetchval("SELECT pg_get_serial_sequence($1, $2)", self.fqn, key)
        if seq is not None:
            try:
                row = await conn.fetchrow(f"SELECT last_value, is_called FROM {seq}")
            except asyncpg.InsufficientPrivilegeError:
                raise PrepareFailure("target_invalid", "cannot read the key sequence (SELECT on it is needed)") from None
            nxt = row["last_value"] + 1 if row["is_called"] else row["last_value"]
            top = await conn.fetchval(f"SELECT max({_q(key)}) FROM {self.fqn}")
            if top is not None and nxt <= top:
                raise PrepareFailure("pk_strategy_invalid", "the key sequence is behind the table's largest key")

    async def precheck_collisions(self, keys: AsyncIterator[list[tuple[Any, ...]]]) -> int:
        pool = await self.c.write_pool()
        total = 0
        async with pool.acquire() as conn:
            async for batch in keys:
                if not batch:
                    continue
                if len(self.t.key) == 1:
                    k = self.t.key[0]
                    total += await conn.fetchval(
                        f"SELECT count(*) FROM {self.fqn} WHERE {_q(k)} = ANY($1::{self._type(k)}[])",
                        [v[0] for v in batch])
                else:
                    arrays = [[v[i] for v in batch] for i in range(len(self.t.key))]
                    unnest = ", ".join(f"${i + 1}::{self._type(k)}[]" for i, k in enumerate(self.t.key))
                    names = ", ".join(_q(k) for k in self.t.key)
                    on = " AND ".join(f"t.{_q(k)} = u.{_q(k)}" for k in self.t.key)
                    total += await conn.fetchval(
                        f"SELECT count(*) FROM {self.fqn} t JOIN unnest({unnest}) AS u({names}) ON {on}", *arrays)
        return int(total)

    async def read_ledger(self, job_id: str, partition_no: int) -> LedgerMark | None:
        pool = await self.c.write_pool()
        async with pool.acquire() as conn:
            if await conn.fetchval("SELECT to_regclass($1)", self.ledger) is None:
                self._seen[partition_no] = 0
                return None
            row = await conn.fetchrow(f"SELECT batch_no, last_key::text AS k, rows FROM {self.ledger} "
                                      "WHERE job_id = $1 AND partition_no = $2", job_id, partition_no)
        self._seen[partition_no] = row["batch_no"] if row else 0
        if row is None:
            return None
        return LedgerMark(job_id=job_id, partition_no=partition_no, batch_no=row["batch_no"],
                          last_key=json.loads(row["k"]), rows=row["rows"])

    async def write_batch(self, rows: list[Row], *, mark: LedgerMark) -> int:
        if not rows:
            return 0
        pool = await self.c.write_pool()
        ledgered = self.t.write_mode in ("in_place", "append")
        try:
            async with pool.acquire() as conn, conn.transaction():
                if ledgered:
                    current = await conn.fetchval(
                        f"SELECT batch_no FROM {self.ledger} WHERE job_id = $1 AND partition_no = $2 FOR UPDATE",
                        mark.job_id, mark.partition_no) or 0
                    if current != self._seen.get(mark.partition_no, 0):
                        raise LedgerConflict()
                if self.t.write_mode == "in_place":
                    written = await self._update(conn, rows)
                elif self.t.write_mode == "append":
                    written = await self._insert(conn, rows)
                else:
                    written = await self._upsert(conn, rows)
                if ledgered:
                    await conn.execute(
                        f"INSERT INTO {self.ledger} (job_id, partition_no, batch_no, last_key, rows, updated_at) "
                        "VALUES ($1, $2, $3, $4::jsonb, $5, now()) ON CONFLICT (job_id, partition_no) DO UPDATE SET "
                        "batch_no = EXCLUDED.batch_no, last_key = EXCLUDED.last_key, rows = EXCLUDED.rows, "
                        "updated_at = now()", mark.job_id, mark.partition_no, mark.batch_no,
                        json.dumps(mark.last_key), mark.rows)
        except asyncpg.UniqueViolationError:
            raise PkCollision() from None
        except (LedgerConflict, PrepareFailure):
            raise
        except asyncpg.PostgresError as exc:
            raise ConnectorError("target_error", type(exc).__name__) from None
        except _RETRYABLE as exc:
            raise ConnectorError("target_error", type(exc).__name__, retryable=True) from None
        if ledgered:
            self._seen[mark.partition_no] = mark.batch_no
        return written

    async def _update(self, conn: asyncpg.Connection, rows: list[Row]) -> int:
        names = self.t.key + [c for c in self.t.write_columns if c not in self.t.key]
        arrays = [[row.get(n) for row in rows] for n in names]
        unnest = ", ".join(f"${i + 1}::{self._type(n)}[]" for i, n in enumerate(names))
        sets = ", ".join(f"{_q(c)} = u.{_q(c)}" for c in names if c not in self.t.key)
        on = " AND ".join(f"t.{_q(k)} = u.{_q(k)}" for k in self.t.key)
        tag = await conn.execute(f"UPDATE {self.fqn} AS t SET {sets} FROM unnest({unnest}) AS "
                                 f"u({', '.join(_q(n) for n in names)}) WHERE {on}", *arrays)
        return rowcount(tag)

    async def _insert(self, conn: asyncpg.Connection, rows: list[Row]) -> int:
        cols = list(self.t.write_columns)
        tag = await conn.copy_records_to_table(self.name, schema_name=self.owner, columns=cols,
                                               records=[tuple(r.get(c) for c in cols) for r in rows])
        return rowcount(tag)

    async def _upsert(self, conn: asyncpg.Connection, rows: list[Row]) -> int:
        cols = list(self.t.write_columns)
        await conn.execute(f"CREATE TEMP TABLE IF NOT EXISTS {self.stage} (LIKE {self.fqn} INCLUDING DEFAULTS) "
                           "ON COMMIT DELETE ROWS")
        await conn.copy_records_to_table(self.stage, schema_name="pg_temp", columns=cols,
                                         records=[tuple(r.get(c) for c in cols) for r in rows])
        listed = ", ".join(_q(c) for c in cols)
        rest = [c for c in cols if c not in self.t.key]
        action = ("DO UPDATE SET " + ", ".join(f"{_q(c)} = EXCLUDED.{_q(c)}" for c in rest)) if rest else "DO NOTHING"
        tag = await conn.execute(f"INSERT INTO {self.fqn} ({listed}) SELECT {listed} FROM pg_temp.{self.stage} "
                                 f"ON CONFLICT ({', '.join(_q(k) for k in self.t.key)}) {action}")
        return rowcount(tag)

    async def finish_partition(self, job_id: str, partition_no: int) -> dict[str, Any]:
        return {}

    async def clear_ledger(self, job_id: str) -> int:
        pool = await self.c.write_pool()
        async with pool.acquire() as conn:
            if await conn.fetchval("SELECT to_regclass($1)", self.ledger) is None:
                return 0
            return rowcount(await conn.execute(f"DELETE FROM {self.ledger} WHERE job_id = $1", job_id))

    async def close(self) -> None:
        return None
```
(`rowcount` is imported from `dps.db`; `PrepareFailure` from `targets.py`.) `PostgresConnector.writer` returns `PostgresWriter(self, target, source_shape)` and refuses `files` targets (`ConnectorError("target_invalid")`).

- [ ] **Step 4: Run tests, lint, types**

Run: `pytest -q tests/connectors/test_postgres_write.py && ruff check dps tests tools && mypy dps`
Expected: `5 passed`, clean.

- [ ] **Step 5: Commit**

```bash
git add dps/layers/connectors/postgres.py tests/connectors/test_postgres_write.py
git commit -m "feat(connectors): PostgreSQL writer - set-based in-place updates, COPY appends and upserts, write ledger" -- dps/layers/connectors/postgres.py tests/connectors/test_postgres_write.py
```

---

### Task 20: Files writer and manifest (`files.py`)

**Files:**
- Create: `dps/layers/connectors/files.py`
- Test: `tests/connectors/test_files.py` (offline, local store), `tests/connectors/test_files_s3.py` (`s3`)

Public interface (contract §4.2 `BatchWriter`, §13 key layout):
```python
EXTENSIONS: dict[str, str]                 # csv→csv, jsonl→jsonl, parquet→parquet ("+.gz" for gzip text formats)
class FilesWriter:                         # implements BatchWriter
    def __init__(self, store: ObjectStore, spec: FilesSpec, *, job_id: str, base_prefix: str,
                 columns: list[ColumnInfo], spool_root: Path | None = None) -> None
    def base_key(self) -> str              # "<base_prefix>/<job_id>" (base_prefix = connection prefix + files.prefix)
    def reset_partition(self, partition_no: int) -> None
def arrow_schema(columns: list[ColumnInfo]) -> pa.Schema
def build_manifest(*, job_id: str, spec: FilesSpec, columns: list[ColumnInfo], parts: list[dict[str, Any]],
                   created_at: datetime) -> dict[str, Any]
async def write_manifest(store: ObjectStore, base_key: str, manifest: dict[str, Any]) -> StoredObject
```
Behaviour: rows of a partition are spooled to local temp files (`rows_per_file` per part file: `part-<partition:05d>-<file:03d>.<ext>`); `finish_partition` uploads the part files and returns `{"files": [{"key", "rows", "bytes", "sha256"}]}` (stored in `dps_job_partition.meta` by the run handler); a restarted partition calls `reset_partition` and rewrites its objects under the same keys (deterministic content). `read_ledger` returns `None`, `precheck_collisions` returns 0, `clear_ledger` 0, `prepare` does a probe put/delete unless `dry_run`. The manifest is written **last** at finalize (`<base>/manifest.json`: job id, format, compression, columns `{name, type}`, every object with rows/bytes/sha256, `rows_total`, `created_at`), so a loader that sees the manifest sees complete files. Value conversion: CSV — header row, `None` → empty, `Decimal` → plain digits, dates ISO-8601, bytes → hex; JSONL — one compact JSON object per row with the same scalar rules; Parquet — `pyarrow` schema from the output column kinds (`int64`, `decimal128(p,s)` when `p ≤ 38` else string, `float64`, `string`, `date32`, `timestamp("us", tz="UTC")` for `timestamp with time zone` else `timestamp("us")`, `bool_`, `binary`), compression `snappy`/`zstd`/`gzip`/`none`.

- [ ] **Step 1: Write the failing tests**

`tests/connectors/test_files.py`:
```python
from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
from datetime import UTC, date, datetime
from decimal import Decimal

import pyarrow.parquet as pq
import pytest

from dps.layers.connectors.base import ColumnInfo, FilesSpec, LedgerMark
from dps.layers.connectors.files import FilesWriter, build_manifest, write_manifest
from dps.storage import LocalObjectStore

COLS = [ColumnInfo(path="$.id", name="id", data_type="bigint", kind="int"),
        ColumnInfo(path="$.amount", name="amount", data_type="numeric(14,2)", kind="decimal", precision=14, scale=2),
        ColumnInfo(path="$.day", name="day", data_type="date", kind="date"),
        ColumnInfo(path="$.at", name="at", data_type="timestamp with time zone", kind="timestamp"),
        ColumnInfo(path="$.name", name="name", data_type="text", kind="text")]
ROWS = [{"id": i, "amount": Decimal(f"{i}.50"), "day": date(2025, 1, 1 + i), "at": datetime(2025, 1, 1, tzinfo=UTC),
         "name": None if i == 2 else f"t{i}"} for i in range(5)]
MARK = LedgerMark(job_id="j_f", partition_no=0, batch_no=1, last_key=[], rows=0)


@pytest.mark.parametrize("fmt,compression", [("csv", "none"), ("csv", "gzip"), ("jsonl", "none"), ("parquet", "snappy")])
async def test_formats_round_trip(tmp_path, fmt: str, compression: str) -> None:  # noqa: ANN001
    store = LocalObjectStore(tmp_path / "out")
    w = FilesWriter(store, FilesSpec(format=fmt, compression=compression, prefix="crm/customers"), job_id="j_f",
                    base_prefix="exports", columns=COLS, spool_root=tmp_path / "spool")
    await w.write_batch(ROWS, mark=MARK)
    meta = await w.finish_partition("j_f", 0)
    [part] = meta["files"]
    assert part["key"].startswith("exports/crm/customers/j_f/part-00000-000.") and part["rows"] == 5
    data = await store.get(part["key"])
    assert part["sha256"] == hashlib.sha256(data).hexdigest() and part["bytes"] == len(data)
    if fmt == "parquet":
        table = pq.read_table(io.BytesIO(data))
        assert table.num_rows == 5 and table.column("amount").to_pylist()[1] == Decimal("1.50")
    else:
        text = (gzip.decompress(data) if compression == "gzip" else data).decode()
        rows = list(csv.DictReader(io.StringIO(text))) if fmt == "csv" else [json.loads(x) for x in text.splitlines()]
        assert len(rows) == 5 and str(rows[1]["amount"]) == "1.50" and rows[2]["name"] in ("", None)
        assert rows[0]["day"] == "2025-01-01"


async def test_rows_per_file_split_reset_and_manifest(tmp_path) -> None:  # noqa: ANN001
    store = LocalObjectStore(tmp_path / "out")
    spec = FilesSpec(format="jsonl", compression="none", rows_per_file=1000)
    w = FilesWriter(store, spec, job_id="j_f", base_prefix="", columns=COLS, spool_root=tmp_path / "spool")
    many = [dict(ROWS[0], id=i) for i in range(2500)]
    await w.write_batch(many, mark=MARK)
    w.reset_partition(0)                                           # a resumed partition starts again
    await w.write_batch(many, mark=MARK)
    meta = await w.finish_partition("j_f", 0)
    assert [f["rows"] for f in meta["files"]] == [1000, 1000, 500]
    manifest = build_manifest(job_id="j_f", spec=spec, columns=COLS, parts=meta["files"],
                              created_at=datetime(2026, 10, 11, tzinfo=UTC))
    assert manifest["rows_total"] == 2500 and [c["name"] for c in manifest["columns"]][0] == "id"
    obj = await write_manifest(store, w.base_key(), manifest)
    assert obj.key == "j_f/manifest.json" and json.loads(await store.get(obj.key))["objects"][2]["rows"] == 500
```

`tests/connectors/test_files_s3.py`: the parquet case of `test_formats_round_trip` against `s3_store` with `base_prefix="tests"`, plus `store.delete` of the part afterwards (marked `integration`, `s3`).

- [ ] **Step 2: Run to confirm failure**, **Step 3: write `files.py`**

The non-obvious part is the per-partition spool with rollover:
```python
class _Spool:
    """Part files of one partition being written (rolled every ``rows_per_file`` rows)."""

    def __init__(self, root: Path, partition: int, spec: FilesSpec, columns: list[ColumnInfo]) -> None:
        self.root, self.partition, self.spec, self.columns = root, partition, spec, columns
        self.files: list[tuple[Path, int]] = []     # (path, rows)
        self._fh: Any = None
        self._writer: Any = None
        self._rows = 0

    def _open(self) -> None:
        path = self.root / f"part-{self.partition:05d}-{len(self.files):03d}.{EXTENSIONS[self.spec.format]}" \
            f"{'.gz' if self.spec.format != 'parquet' and self.spec.compression == 'gzip' else ''}"
        path.parent.mkdir(parents=True, exist_ok=True)
        if self.spec.format == "parquet":
            codec = None if self.spec.compression == "none" else self.spec.compression
            self._writer = pq.ParquetWriter(str(path), arrow_schema(self.columns), compression=codec or "NONE")
        else:
            self._fh = gzip.open(path, "wt", encoding="utf-8", newline="") if path.suffix == ".gz" \
                else path.open("w", encoding="utf-8", newline="")
            if self.spec.format == "csv":
                self._writer = csv.writer(self._fh)
                self._writer.writerow([c.name for c in self.columns])
        self.files.append((path, 0))
        self._rows = 0

    def write(self, rows: list[Row]) -> None:
        index = 0
        while index < len(rows):
            if self._fh is None and self._writer is None:
                self._open()
            room = self.spec.rows_per_file - self._rows
            chunk = rows[index:index + room]
            self._emit(chunk)
            self._rows += len(chunk)
            path, count = self.files[-1]
            self.files[-1] = (path, count + len(chunk))
            index += len(chunk)
            if self._rows >= self.spec.rows_per_file:
                self.close()

    def _emit(self, rows: list[Row]) -> None:
        names = [c.name for c in self.columns]
        if self.spec.format == "parquet":
            self._writer.write_table(pa.Table.from_pylist([{n: _arrow_value(r.get(n)) for n in names} for r in rows],
                                                          schema=arrow_schema(self.columns)))
        elif self.spec.format == "csv":
            self._writer.writerows([[_text_value(r.get(n)) for n in names] for r in rows])
        else:
            for r in rows:
                self._fh.write(json.dumps({n: _json_value(r.get(n)) for n in names}, ensure_ascii=False,
                                          separators=(",", ":")) + "\n")

    def close(self) -> None:
        if self.spec.format == "parquet" and self._writer is not None:
            self._writer.close()
        if self._fh is not None:
            self._fh.close()
        self._fh = self._writer = None
```
`_text_value`: `None`→`""`, `bool`→`"true"/"false"`, `Decimal`→`format(v, "f")`, dates→`isoformat()`, bytes→hex, else `str`; `_json_value`: same but `None` stays `None`, ints/floats stay numbers, `Decimal`→string; `_arrow_value`: `UUID`→`str`, else unchanged. `FilesWriter.finish_partition` closes the spool, uploads each part with `store.put(key, path.read_bytes(), content_type=…)` (`text/csv`, `application/x-ndjson`, `application/vnd.apache.parquet`, gzip variants `application/gzip`), deletes the spool directory, and returns the metadata. `reset_partition` closes and deletes that partition's spool files.

- [ ] **Step 4: Run tests, lint, types**

Run: `pytest -q tests/connectors/test_files.py tests/connectors/test_files_s3.py && ruff check dps tests tools && mypy dps`
Expected: `5 + 1 = 6 passed`, clean.

- [ ] **Step 5: Commit**

```bash
git add dps/layers/connectors/files.py tests/connectors/test_files.py tests/connectors/test_files_s3.py
git commit -m "feat(connectors): CSV/Parquet/JSONL part files per partition with a manifest written last" -- dps/layers/connectors/files.py tests/connectors/test_files.py tests/connectors/test_files_s3.py
```

---

### Task 21: Evidence pack — build, hash, HTML, store (`evidence.py`)

**Files:**
- Create: `dps/layers/connectors/evidence.py`
- Test: `tests/test_evidence.py` (offline), `tests/db/test_evidence_store.py`

Public interface (contract §7; `label`, `EvidenceInputs`, `EvidenceRecord`, `EvidenceSummary` are additions — the contract requires the label but lists no field for it):
```python
LABEL_OBFUSCATE = "pseudonymised — still personal data"
LABEL_SYNTHESIZE = "synthetic — measured, not claimed anonymous"
class EvidencePack(BaseModel): ...          # contract §7.1 verbatim + label: str + mode: str | None (added)
@dataclass
class EvidenceInputs:
    job: JobInternal; spec: dict[str, Any]; plan: RunPlan | None; snapshot: ClassificationSnapshot | None
    source_conn: Connection; target_conn: Connection | None; partitions: list[PartitionView]
    outcome: Literal["succeeded", "failed", "canceled", "dry_run"]; server_version: str | None
    prepare: dict[str, Any]; files: dict[str, Any] | None; verification: dict[str, Any]
    key_custody: dict[str, Any]; errors: list[dict[str, str]]; engine_version: str; image_digest: str | None
class EvidenceRecord(BaseModel): id; job_id; pack: EvidencePack; pack_sha256: str; html_artefact_id: str | None
                                 pdf_artefact_id: str | None; json_artefact_id: str | None; created_at: datetime
class EvidenceSummary(BaseModel): id; job_id; created_at; outcome; mode; label; source_object; target; write_mode
                                  policy; ticket; passed: bool; hits: int; rows_written: int; html_artefact_id
def build_pack(inputs: EvidenceInputs) -> EvidencePack
def pack_sha256(pack: EvidencePack) -> str
def canonical_json(pack: EvidencePack) -> bytes
def render_html(pack: EvidencePack, *, sha: str) -> str
async def save_evidence(pack: EvidencePack, *, artefacts: ArtefactService, acquire: AcquireFn,
                        expires_at: datetime | None) -> EvidenceRecord
async def get_evidence(tenant_id: str, evidence_id: str) -> EvidenceRecord | None
async def list_evidence(tenant_id: str, *, job_id: str | None = None, limit: int = 50,
                        cursor: str | None = None) -> tuple[list[EvidenceSummary], str | None]
```

**Content rules.** The pack holds counts, hashes, column names/paths, entity types, method names and params, connection ids/names, key ids, seed fingerprints and **templates** of the selection — never a value, a key, a seed, a selection literal or a partition key position (the mockup says so: "Checkpoint positions … are not printed here, because a key position is a customer identifier"). `columns[*].values` comes from the `col:<name>` counters (Task 22); `reveal_map` lists every reversible column with `{path, entity_type, method, params, anchor_path, reveal}` where `reveal` is false for `date_shift` when the anchor column is not reversibly transformed (contract §6.2); `config_hash = sha256(policy_hash + snapshot.content_hash + sha256(canonical redacted spec))`; `pack_sha256 = sha256(canonical JSON, sort_keys, compact separators)`. The HTML is a self-contained, escaped f-string page (every interpolation through `html.escape`, inline CSS only: the Vault light tokens and a print stylesheet, no scripts) with the mockup-10 sections M2 can fill: header (job, mode, purpose, sealed time, pack id + sha256, label), 1 Summary (KPIs: rows written = rows read, columns transformed, values transformed, leakage sweep hits, duration, partitions/retries, raw values to LLM 0), 2 Configuration hashes (policy name + hash, snapshot id + hash, config hash, engine version, image digest, key id + custody, source/target/selection template), 3 Classification snapshot (column, type, entity, confidence, method, decision accepted/overridden), 4 Execution (partition table: rows, batches, retries, state — no checkpoint positions), 5 Verification (leakage sweep, post-write sample, row counts, FPE fallbacks), 7 Regulatory note (the label's paragraph), 8 Signatures (system attestation + data owner / DPO / change approver blanks). Section 6 (synthetic fidelity/privacy) renders only when `fidelity`/`privacy` are present (M5).

- [ ] **Step 1: Write the failing tests**

`tests/test_evidence.py`:
```python
from __future__ import annotations

import json

from dps.layers.connectors.evidence import (
    LABEL_OBFUSCATE, LABEL_SYNTHESIZE, build_pack, canonical_json, pack_sha256, render_html,
)
from tests.connectors.factories import evidence_inputs

SECRETS = ["Ananya", "example-mail", "10000001", "2025-01-01", "AAAAAAAAAAAA"]


def test_pack_is_value_free_and_labelled() -> None:
    pack = build_pack(evidence_inputs(mode="obfuscate"))
    text = canonical_json(pack).decode()
    assert pack.label == LABEL_OBFUSCATE and not any(s in text for s in SECRETS)
    assert pack.source["selection"]["where_template"] == '"created_at" >= :l1'
    assert pack.consistency["kid"].startswith("k_") and "dek" not in text
    assert all("checkpoint" not in json.dumps(p) and "last_key" not in json.dumps(p) for p in pack.partitions)
    assert {c["path"] for c in pack.reveal_map} == {"$.full_name", "$.email", "$.dob"}
    assert build_pack(evidence_inputs(mode="synthesize")).label == LABEL_SYNTHESIZE


def test_hash_is_stable_and_sensitive() -> None:
    pack = build_pack(evidence_inputs(mode="obfuscate"))
    assert pack_sha256(pack) == pack_sha256(pack.model_copy(deep=True))
    assert pack_sha256(pack) != pack_sha256(pack.model_copy(update={"outcome": "failed"}))


def test_html_escapes_everything_and_has_no_scripts() -> None:
    inputs = evidence_inputs(mode="obfuscate", connection_name='crm<script>alert(1)</script>')
    pack = build_pack(inputs)
    page = render_html(pack, sha=pack_sha256(pack))
    assert "<script" not in page and "&lt;script&gt;" in page and pack_sha256(pack) in page
    assert "pseudonymised — still personal data" in page and "Signatures" in page
```
`factories.evidence_inputs(mode, connection_name="bank-pg-ro")` builds a finished job (`JobInternal` with a redacted spec whose where template is `"created_at" >= :l1`, `kid="k_…"`), a `RunPlan` with plans for `full_name` (fpe_text), `email` (email), `dob` (date_shift, anchor `customer_id`), the accepted snapshot, two `PartitionView`s, and the verification block.

`tests/db/test_evidence_store.py`:
```python
from __future__ import annotations

import asyncpg
import pytest

from dps import db, jobs
from dps.jobs import JobKind, JobPhase
from dps.layers.connectors.evidence import build_pack, get_evidence, list_evidence, save_evidence
from dps.storage import ArtefactService, LocalObjectStore
from tests.connectors.factories import evidence_inputs

pytestmark = pytest.mark.db


async def test_save_get_list_and_append_only(migrated_db, tmp_path) -> None:  # noqa: ANN001
    inputs = evidence_inputs(mode="obfuscate")
    job = await jobs.create_job(tenant_id=inputs.job.tenant_id, kind=JobKind.run, phase=JobPhase.prepare, spec={},
                                job_id=inputs.job.id)
    pack = build_pack(inputs)
    record = await save_evidence(pack, artefacts=ArtefactService(LocalObjectStore(tmp_path), acquire=db.acquire),
                                 acquire=db.acquire, expires_at=None)
    assert record.html_artefact_id and record.json_artefact_id
    got = await get_evidence(job.tenant_id, record.id)
    assert got.pack_sha256 == record.pack_sha256 and got.pack.job_id == job.id
    items, _ = await list_evidence(job.tenant_id, job_id=job.id)
    assert [i.id for i in items] == [record.id] and items[0].passed and items[0].write_mode == "table"
    async with db.acquire(job.tenant_id) as conn:
        with pytest.raises(asyncpg.RaiseError):
            await conn.execute("UPDATE dps.dps_evidence SET pack_sha256 = 'x' WHERE id = $1", record.id)
```

- [ ] **Step 2: Run to confirm failure**, **Step 3: write `evidence.py`**

`build_pack` assembles the contract §7.1 fields from `EvidenceInputs` (sources: `spec["source"]["selection"]` for the templates and `len(params)` for `binds`; `plan.target` for write mode/load/strategy/created; `inputs.prepare` for `created`/`truncated`/`collisions`; `snapshot` for id/hash/accepted_by/accepted_at/overrides count; `job.progress.values_by_method` split into methods, `fallback:*` and `col:*` counters; partitions → `[{no, rows: rows_written, attempts, resumed, state}]`; `timings` from `job.started_at`/now, `rows_per_s`). `save_evidence`: in one `acquire(tenant)` block insert the `dps_evidence` row; save the JSON artefact (`canonical_json(pack)`, `application/json`) and the HTML artefact (`text/html; charset=utf-8`) through `ArtefactService`; `UPDATE dps_evidence SET html_artefact_id = $2` (the trigger allows exactly this). `json_artefact_id` is found by `meta->>'evidence_id'` on the artefact row (saved with `meta={"evidence_id": id}`). `list_evidence` selects the summary fields straight from `pack` jsonb (`pack->>'outcome'`, `pack->'source'->>'object'`, `pack->'target'->>'write_mode'`, `pack->'policy'->>'name'`, `pack->'purpose'->>'ticket'`, `(pack->'verification'->>'passed')::boolean`, `(pack->'verification'->>'hits')::int`, `(pack->'counts'->>'rows_written')::bigint`, `pack->>'label'`) with keyset paging on `(created_at, id)`.

`render_html` (the full page; `e = html.escape`):
```python
_CSS = """:root{--ink:#141a2b;--ink2:#4d5875;--line:#e0e5f0;--teal:#0f766e;--emerald:#047857;--rose:#be123c;--amber:#b45309}
*{box-sizing:border-box}body{font:13px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif;color:var(--ink);margin:0;background:#fff}
main{max-width:980px;margin:0 auto;padding:32px 28px}h1{font-size:22px;margin:4px 0}h2{font-size:15px;margin:28px 0 10px;border-bottom:1px solid var(--line);padding-bottom:6px}
.muted{color:var(--ink2)}.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px}.chip{display:inline-block;border:1px solid;border-radius:999px;padding:1px 8px;font-size:11px;font-weight:700}
.ok{color:var(--emerald)}.bad{color:var(--rose)}.kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}.kpi{border:1px solid var(--line);border-radius:10px;padding:10px 12px}
.kpi b{display:block;font-size:20px}table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);vertical-align:top}
th{font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:var(--ink2)}.sig{display:grid;grid-template-columns:repeat(2,1fr);gap:16px}.sig div{border:1px solid var(--line);border-radius:10px;padding:12px;min-height:90px}
@media print{main{padding:0}h2{break-after:avoid}table,.kpis,.sig{break-inside:avoid}}"""


def render_html(pack: EvidencePack, *, sha: str) -> str:
    e = html.escape
    v = pack.verification
    rows_cols = "".join(
        f"<tr><td class=mono>{e(c['path'])}</td><td>{e(str(c.get('entity_type') or '—'))}</td>"
        f"<td class=mono>{e(c['method'])}</td><td>{'yes' if c.get('reversible') else 'one-way'}</td>"
        f"<td>{e(str(c.get('values') if c.get('values') is not None else '—'))}</td></tr>" for c in pack.columns)
    rows_parts = "".join(
        f"<tr><td>p{e(str(p['no']))}</td><td>{e(str(p['rows']))}</td><td>{e(str(p['attempts']))}</td>"
        f"<td>{e(str(p['resumed']))}</td><td>{e(str(p['state']))}</td></tr>" for p in pack.partitions)
    status = (f"<span class='chip ok'>verified · {e(str(v.get('hits', 0)))} leaks</span>" if v.get("passed")
              else f"<span class='chip bad'>verification failed · {e(str(v.get('hits', 0)))} hits</span>")
    purpose = pack.purpose
    note = ("Whoever holds the key with id " + e(str(pack.consistency.get("kid") or "—")) +
            " can re-identify every row. Hold this output under the same controls as the source."
            if pack.label.startswith("pseudonymised") else
            "The synthetic output is a measurement, not a claim of anonymity. Treat it as confidential until your "
            "data protection officer decides otherwise.")
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><title>DPS evidence pack {e(pack.id)}</title>
<style>{_CSS}</style></head><body><main>
<div class=muted>Data Privacy Services · evidence pack</div>
<h1>{e(str(pack.source.get('engine', '')))} {e(str(pack.source.get('object', '')))} → {e(str(pack.target.get('object') or pack.target.get('write_mode', '')))}</h1>
<p>Job <span class=mono>{e(pack.job_id)}</span> · {e(pack.mode or '')} · {e(str(pack.target.get('write_mode', '')))} · outcome <b>{e(pack.outcome)}</b> · {status}</p>
<p>Pack <span class=mono>{e(pack.id)}</span> · sha256 <span class=mono>{e(sha)}</span> · sealed {e(pack.created_at.isoformat())}</p>
<p><span class=chip>{e(pack.label)}</span></p>
<p>Purpose {e(purpose.ticket if purpose else '—')} · {e(purpose.legal_basis if purpose else '—')} · requested by key {e(pack.requested_by or '—')}</p>
<h2>1 Summary</h2><div class=kpis>
<div class=kpi>Rows written<b>{e(str(pack.counts.get('rows_written', 0)))}</b><span class=muted>of {e(str(pack.counts.get('rows_read', 0)))} read</span></div>
<div class=kpi>Columns transformed<b>{len(pack.columns)}</b></div>
<div class=kpi>Leakage sweep<b class="{'ok' if v.get('passed') else 'bad'}">{e(str(v.get('hits', 0)))} hits</b><span class=muted>{e(str(v.get('values_checked', 0)))} values checked</span></div>
<div class=kpi>Raw values to LLM<b class=ok>0</b></div></div>
<h2>2 Configuration hashes</h2><table>
<tr><th>Source</th><td>{e(str(pack.source.get('connection_name', '')))} · {e(str(pack.source.get('engine', '')))} {e(str(pack.source.get('server_version') or ''))} · {e(str(pack.source.get('object', '')))}</td></tr>
<tr><th>Target</th><td>{e(str(pack.target.get('connection_name', '')))} · {e(str(pack.target.get('object') or pack.target.get('files') or ''))}</td></tr>
<tr><th>Policy</th><td class=mono>{e(pack.policy.get('name', ''))} · {e(pack.policy.get('hash', ''))}</td></tr>
<tr><th>Snapshot</th><td class=mono>{e(str(pack.snapshot.get('id')))} · {e(str(pack.snapshot.get('content_hash')))}</td></tr>
<tr><th>Configuration</th><td class=mono>{e(pack.config_hash)}</td></tr>
<tr><th>Engine</th><td class=mono>{e(pack.engine_version)} · image {e(pack.image_digest or 'unset')}</td></tr>
<tr><th>Key</th><td class=mono>kid {e(str(pack.consistency.get('kid') or '—'))} · seed {e(str(pack.consistency.get('seed_fingerprint') or '—'))} · DPS stored no key</td></tr>
<tr><th>Selection</th><td class=mono>{e(str(pack.source.get('selection', {}).get('where_template') or 'all rows'))} · {e(str(pack.source.get('selection', {}).get('binds', 0)))} bound parameters</td></tr></table>
<h2>3 Classification snapshot</h2><table><tr><th>Column</th><th>Entity</th><th>Method</th><th>Reversible</th><th>Values</th></tr>{rows_cols}</table>
<h2>4 Execution</h2><table><tr><th>Partition</th><th>Rows</th><th>Attempts</th><th>Resumed</th><th>Result</th></tr>{rows_parts}</table>
<p class=muted>Checkpoint positions stay in DPS's job tables and are not printed here: a key position is a customer identifier.</p>
<h2>5 Verification</h2><table>
<tr><th>Leakage sweep</th><td>{e(str(v.get('hits', 0)))} hits in {e(str(v.get('values_checked', 0)))} values ({e(str(v.get('mode', 'strict')))})</td></tr>
<tr><th>Post-write sample</th><td>{e(json.dumps(v.get('post_sample', {}), sort_keys=True))}</td></tr>
<tr><th>Row counts</th><td>{e(str(pack.counts.get('rows_read', 0)))} read · {e(str(pack.counts.get('rows_written', 0)))} written · {e(str(pack.counts.get('rows_skipped', 0)))} skipped</td></tr></table>
<h2>7 Regulatory note</h2><p>{note} DPS stores no customer values and no keys; this pack holds counts, hashes, names and key ids only.</p>
<h2>8 Signatures</h2><div class=sig><div><b>System attestation</b><br>Data Privacy Services {e(pack.engine_version)}<br><span class=mono>sha256:{e(sha)}</span></div>
<div><b>Data owner</b><br><br>Signature · Date</div><div><b>Data protection officer</b><br><br>Signature · Date</div><div><b>Change approver</b><br><br>Signature · Date</div></div>
</main></body></html>"""
```

- [ ] **Step 4: Run tests, lint, types**

Run: `pytest -q tests/test_evidence.py tests/db/test_evidence_store.py && ruff check dps tests tools && mypy dps`
Expected: `3 + 1 = 4 passed`, clean.

- [ ] **Step 5: Commit**

```bash
git add dps/layers/connectors/evidence.py tests/test_evidence.py tests/db/test_evidence_store.py tests/connectors/factories.py
git commit -m "feat(evidence): value-free hashed evidence packs with a printable HTML rendering" -- dps/layers/connectors/evidence.py tests/test_evidence.py tests/db/test_evidence_store.py tests/connectors/factories.py
```

---

### Task 22: Run handlers (`prepare`, `run_partition`, `finalize`), submission and job groups

**Files:**
- Create: `dps/layers/connectors/jobs_run.py`, `dps/layers/connectors/submit.py`, `dps/groups.py`
- Modify: `dps/engine/batch.py` (add `reveal_row`)
- Test: `tests/connectors/helpers.py` (driver), `tests/connectors/test_run_modes.py`, `tests/connectors/test_run_failures.py`, `tests/db/test_groups.py`, `tests/test_reveal_row.py`

Public interface (contract §3.5 handlers; `submit.py`, `reveal_row`, `RuntimeCache` are additions — the API routers and the tests share the submission code instead of duplicating sealing):
```python
# dps/layers/connectors/jobs_run.py
async def prepare(ctx: HandlerContext, job: ClaimedJob) -> None
async def run_partition(ctx: HandlerContext, job: JobInternal, part: ClaimedPartition) -> None
async def finalize(ctx: HandlerContext, job: ClaimedJob) -> None
class RuntimeCache:                    # per-process: one opened source/target/transformer per running job
    async def get(self, ctx: HandlerContext, job: JobInternal) -> RunRuntime
    def release(self, job_id: str) -> None
    async def close_idle(self, idle_seconds: float = 120.0) -> int
    async def drop(self, job_id: str) -> None
    async def close_all(self) -> None
RUNTIMES: RuntimeCache
# dps/layers/connectors/submit.py
class RunSubmission(BaseModel): job: Job; key_material: dict[str, Any] | None; synthesis_seed: dict[str, Any] | None; replay: bool
async def submit_discover(spec: DiscoverSpec, *, tenant_id: str, principal: Principal, settings: Settings,
                          kek: bytes | None, idempotency_key: str | None = None) -> Job
async def submit_run(spec: RunSpec, *, tenant_id: str, principal: Principal, settings: Settings,
                     kek: bytes | None, idempotency_key: str | None = None) -> RunSubmission
async def seal_retry_secret(job: Job, consistency: ConsistencySpec, *, kek: bytes) -> bytes   # retry body → sealed JobSecret
# dps/groups.py
class JobGroup(BaseModel): id; tenant_id; name; kid; seed_fingerprint; created_by; created_at; closed_at
async def create_group(tenant_id: str, name: str, *, created_by: str | None) -> tuple[JobGroup, KeyMaterial, SynthesisSeed]
async def get_group(tenant_id: str, group_id: str) -> JobGroup | None
async def list_groups(tenant_id: str, *, limit: int = 50, cursor: str | None = None) -> tuple[list[JobGroup], str | None]
# dps/engine/batch.py
def reveal_row(row: Row, *, reveal_map: list[dict[str, Any]], shape: TableShape, key: KeyMaterial,
               engine: PrivacyEngine, policy: Policy, anchor: tuple[str, str] | None) -> Row
```

**`prepare` (one job row, fenced).** In order — and nothing is written to the target before step 9:
1. Open the sealed secret (`JobSecret`, AAD `dps_job.key:<id>`) and selection (`SealedSelection`, AAD `dps_job.spec:<id>`) and merge the selection into the redacted spec.
2. Load the source/target connections, the snapshot and credentials (tenant GUC); open the source connector (`action="prepare"`, `job_id`).
3. `describe` the source; every snapshot column must still exist with the same kind (`snapshot_stale`); the object must have a key (`no_key`); re-validate the selection against the live shape.
4. `count` the selection; `rows > target.expected_rows` → `row_count_mismatch`.
5. Column plans (`build_column_plans`), target connector (the source connector itself for `in_place`/same-connection `append`), target shape (`describe`, `object_not_found` → `None`), `resolve_target`, static worst-case length check (`length_overflow`).
6. `BatchTransformer` with the key (obfuscate) or seed (synthesize), the snapshot's anchor column, the policy locale, `options.verify`.
7. **Preflight**: transform `min(preflight_rows, rows)` sampled rows — `IncompatibleMethod` → `incompatible_method`/`length_overflow`, `LeakageError` (strict) → `leakage`. The sample stays in memory and is dropped.
8. `append` with `offset`/`token`: stream the selection's keys, transform them like the run will, `writer.precheck_collisions` → any clash → `pk_collision` (count only).
9. `writer.prepare(dry_run=options.dry_run)` — creates/truncates the table, or creates the ledger, or probes the store; returns `append_upper` for a same-table append.
10. `dry_run` → `advance_job(phase=finalize, state=queued, progress={outcome: "dry_run", rows_estimated, values_by_method (preflight counters), verification})` and stop.
11. Partitions: global upper bound = the `(max_rows+1)`-th key (`key_at_offset`) and, for a same-table append, `min(that, append_upper)` (integer key); `partition_count` from the (capped) row count; `sample_keys(…, upper=…)`; `plan_partitions`; `add_partitions(ranges, plan=RunPlan(…, prepare={created, truncated, collisions, existing_rows}))` (the atomic `prepare → partitions/waiting` transition). Zero rows → straight to `finalize`.

Failures: `PrepareFailure`, `SpecError`, `SelectionError`, `IncompatibleMethod`, `LeakageError`, `SealError`/`SecretUnavailable` → `advance_job(phase=finalize, state=queued, progress={outcome: "failed"}, error_code, error)` (finalize writes a failed evidence pack and purges the key); a retryable `ConnectorError` → `retry_job` while attempts remain, then the same failed-finalize; any other exception → `retry_job(error_code="internal_error", error=<class name>)`.

**`run_partition` (fenced, resumable).** Get the job runtime from `RUNTIMES` (built once per process: sealed blobs opened, connectors opened with `action="run"`, the transformer and writer built from the stored `RunPlan`). Resume point: `in_place`/`append` read the ledger — if its `batch_no` is ahead of the partition row, the last batch committed but its checkpoint did not, so checkpoint the ledger mark first (the ledger is authoritative); `files` restart a resumed partition from its lower bound (`reset_partition` + `reset_partition_progress`); `table` resumes after `last_key` (idempotent upserts). Then per page of `read_partition`: transform in a thread → `apply_pk_strategy` (append) → `write_batch(rows, mark=LedgerMark(batch_no+1, last source key, cumulative rows))` → `checkpoint` (counters: methods, `fallback:<note>`, `col:<column>`) → `partition_control`: `pause` releases the partition, `cancel` abandons it, `lost` stops. At the end, `writer.finish_partition` (files upload their parts) and `finish_partition(done, meta=…)`. Errors: `LeakageError` → partition `failed` `leakage` (nothing of that batch was written); `IncompatibleMethod` → `failed` with its code; `PkCollision` → `failed` `pk_collision`; `LedgerConflict` or a failed fenced write → stop silently (someone else owns it); `ConnectorError`/other → `retry_partition` (dead at `max_attempts`).

**`finalize` (one job row, fenced).** For a canceled job, wait (re-queue with `delay_seconds=5`) while any canceled partition still holds a live lease. Outcome = `progress.outcome` if set (`dry_run`, `canceled`, `failed` from prepare) else `failed` when any partition is `failed`/`dead`, else `succeeded`. `files` + succeeded: build and write the manifest last (`<base>/manifest.json`) and save it as a `manifest` artefact. `in_place`/`append`: `clear_ledger(job_id)`. Post-write sample (succeeded, database targets): read up to 200 written rows from the target, run the detector over the **kept** columns (`kept_column_hits` — a misclassification signal) and the transformed ones (`detector_hits`, expected: tokens look like e-mails). Build the evidence pack, save it (JSON + HTML artefacts), write the audit row `job.finalize`, and `advance_job(terminal state, progress={outcome, evidence_id, totals}, purge_secret=True)`; drop the runtime. A transient failure → `retry_job` (the reaper dead-letters and purges at `max_attempts`).

- [ ] **Step 1: Write the test driver and the failing tests**

`tests/connectors/helpers.py`:
```python
"""In-process driver: claims and runs job rows and partitions until a job is terminal."""
from __future__ import annotations

import asyncio

from dps import jobs, snapshots
from dps.jobs import TERMINAL_JOB_STATES, Job, JobKind, JobPhase
from dps.layers.connectors.discovery import discover_job
from dps.layers.connectors.jobs_run import finalize, prepare, run_partition
from dps.worker import HandlerContext

PHASES = {JobPhase.discover: discover_job, JobPhase.prepare: prepare, JobPhase.finalize: finalize}


async def drive(ctx: HandlerContext, tenant: str, job_id: str, *, loops: int = 2000) -> Job:
    for _ in range(loops):
        job = await jobs.get_job(tenant, job_id)
        assert job is not None
        if job.state in TERMINAL_JOB_STATES:
            return job
        claimed = await jobs.claim_jobs(ctx.worker_id, kinds=(JobKind.discover, JobKind.run), batch=4)
        for c in claimed:
            await PHASES[c.phase](ctx, c)
        parts = await jobs.claim_partitions(ctx.worker_id, batch=8)
        internal = await jobs.load_job_internal(job_id) if parts else None
        for p in parts:
            await run_partition(ctx, internal, p)
        if not claimed and not parts:
            await asyncio.sleep(0.05)
    raise AssertionError("job did not reach a terminal state")


async def discover_and_accept(ctx: HandlerContext, tenant: str, connection_id: str, obj, **overrides):  # noqa: ANN001, ANN003, ANN201
    from dps.auth import LOCAL_PRINCIPAL
    from dps.jobs_models import DiscoverSpec
    from dps.layers.connectors.submit import submit_discover

    job = await submit_discover(DiscoverSpec(connection_id=connection_id, objects=[obj], sample_rows=500),
                                tenant_id=tenant, principal=LOCAL_PRINCIPAL, settings=ctx.settings, kek=ctx.kek)
    done = await drive(ctx, tenant, job.id)
    snap_id = done.progress.snapshot_ids[0]
    if overrides.get("overrides"):
        await snapshots.set_overrides(tenant, snap_id, overrides["overrides"], fpe_enabled=ctx.engine.fpe_enabled)
    return await snapshots.accept_snapshot(tenant, snap_id, accepted_by="test")
```

`tests/connectors/test_run_modes.py` (`db`, `pg_source`, `integration`):
```python
from __future__ import annotations

import pytest

from dps.auth import LOCAL_PRINCIPAL
from dps.engine.batch import reveal_row
from dps.engine.transform.crypto import key_material_from_caller
from dps.jobs import JobState
from dps.jobs_models import RunSpec
from dps.layers.connectors.evidence import get_evidence
from dps.layers.connectors.submit import submit_run
from tests.connectors.helpers import db_key_enc, discover_and_accept, drive, json_from_export, register_pvc

pytestmark = [pytest.mark.db, pytest.mark.integration, pytest.mark.pg_source]
PURPOSE = {"ticket": "CHG-48213", "legal_basis": "legitimate interest — UAT provisioning"}


def run_spec(conn_id: str, obj, snap_id: str, target: dict, mode: str = "obfuscate", **extra) -> RunSpec:  # noqa: ANN001, ANN003
    return RunSpec.model_validate({"mode": mode, "snapshot_id": snap_id, "purpose": PURPOSE, "target": target,
                                   "source": {"connection_id": conn_id, "object": obj.model_dump(),
                                              "parallelism": 2, "partition_rows": 1000, "batch_rows": 200},
                                   **extra})


async def test_table_mode_obfuscate_into_a_created_uat_table(handler_ctx, source_pg, register_source) -> None:  # noqa: ANN001
    obj = await source_pg.customers_table(rows=2500)
    src = await register_source("t1")
    uat = await register_source("t1", ref="dps/src/test-pg-rw", roles={"target"}, name="uat")
    snap = await discover_and_accept(handler_ctx, "t1", src.id, obj)
    sub = await submit_run(run_spec(src.id, obj, snap.id, {"write_mode": "table", "connection_id": uat.id,
                                                           "object": {"owner": source_pg.schema, "name": "customers_uat"}}),
                           tenant_id="t1", principal=LOCAL_PRINCIPAL, settings=handler_ctx.settings, kek=handler_ctx.kek)
    assert sub.key_material and sub.key_material["dek"]
    job = await drive(handler_ctx, "t1", sub.job.id)
    assert job.state == JobState.succeeded and job.progress.partitions_done == 3 and job.progress.rows_written == 2500
    src_rows = {r["customer_id"]: r for r in await source_pg.admin.fetch(f'SELECT * FROM "{source_pg.schema}".customers')}
    out_rows = await source_pg.admin.fetch(f'SELECT * FROM "{source_pg.schema}".customers_uat')
    assert len(out_rows) == 2500
    assert not {r["email"] for r in out_rows} & {r["email"] for r in src_rows.values()}
    ev = await get_evidence("t1", job.progress.evidence_id)
    assert ev.pack.outcome == "succeeded" and ev.pack.verification["passed"] and ev.pack.counts["rows_written"] == 2500
    assert ev.pack.target["write_mode"] == "table" and ev.pack.target["created"] is True
    internal_key = await db_key_enc(job.id)
    assert internal_key is None                                          # key purged at terminal state


async def test_in_place_round_trips_with_the_returned_key(handler_ctx, source_pg, register_source) -> None:  # noqa: ANN001
    obj = await source_pg.customers_table(rows=600, name="restore_customers")
    before = {r["customer_id"]: dict(r) for r in await source_pg.admin.fetch(
        f'SELECT * FROM "{source_pg.schema}".restore_customers')}
    rw = await register_source("t1", ref="dps/src/test-pg-rw", roles={"source", "target"}, name="restore-rw")
    snap = await discover_and_accept(handler_ctx, "t1", rw.id, obj)
    sub = await submit_run(run_spec(rw.id, obj, snap.id, {"write_mode": "in_place", "confirm_write": True,
                                                          "expected_rows": 600}),
                           tenant_id="t1", principal=LOCAL_PRINCIPAL, settings=handler_ctx.settings, kek=handler_ctx.kek)
    job = await drive(handler_ctx, "t1", sub.job.id)
    assert job.state == JobState.succeeded
    after = {r["customer_id"]: dict(r) for r in await source_pg.admin.fetch(
        f'SELECT * FROM "{source_pg.schema}".restore_customers')}
    assert after.keys() == before.keys()                                 # keys never rewritten in place
    some = next(iter(after))
    assert after[some]["full_name"] != before[some]["full_name"]
    assert await source_pg.admin.fetchval(
        f'SELECT count(*) FROM "{source_pg.schema}".dps_write_ledger WHERE job_id = $1', job.id) == 0
    ev = await get_evidence("t1", job.progress.evidence_id)
    shape = snap.shape
    restored = reveal_row(after[some], reveal_map=ev.pack.reveal_map, shape=shape,
                          key=key_material_from_caller(sub.key_material), engine=handler_ctx.engine,
                          policy=handler_ctx.engine.policy(snap.policy_name),
                          anchor=next(((c.name, c.entity_type) for c in snap.columns if c.anchor), None))
    for entry in ev.pack.reveal_map:
        if entry["reveal"]:
            name = entry["path"][2:]
            assert restored[name] == before[some][name]


async def test_append_offset_to_the_same_table(handler_ctx, source_pg, register_source) -> None:  # noqa: ANN001
    obj = await source_pg.customers_table(rows=400, name="append_customers")
    rw = await register_source("t1", ref="dps/src/test-pg-rw", roles={"source", "target"}, name="append-rw")
    snap = await discover_and_accept(handler_ctx, "t1", rw.id, obj)
    sub = await submit_run(run_spec(rw.id, obj, snap.id, {"write_mode": "append", "confirm_write": True,
                                                          "pk_strategy": {"kind": "offset", "offset": 90_000_000}},
                                    mode="synthesize"),
                           tenant_id="t1", principal=LOCAL_PRINCIPAL, settings=handler_ctx.settings, kek=handler_ctx.kek)
    assert sub.key_material is None and sub.synthesis_seed
    job = await drive(handler_ctx, "t1", sub.job.id)
    assert job.state == JobState.succeeded and job.progress.rows_written == 400
    ids = [r["customer_id"] for r in await source_pg.admin.fetch(
        f'SELECT customer_id FROM "{source_pg.schema}".append_customers ORDER BY 1')]
    assert len(ids) == 800 and ids[400:] == [i + 90_000_000 for i in ids[:400]]   # appended rows read exactly once


async def test_files_parquet_to_a_pvc_path_with_a_manifest(handler_ctx, source_pg, register_source, tmp_path) -> None:  # noqa: ANN001
    obj = await source_pg.customers_table(rows=1500, name="files_customers")
    src = await register_source("t1")
    pvc = await register_pvc("t1", handler_ctx, base_path="exports")
    snap = await discover_and_accept(handler_ctx, "t1", src.id, obj)
    sub = await submit_run(run_spec(src.id, obj, snap.id, {"write_mode": "files", "connection_id": pvc.id,
                                                           "files": {"format": "parquet", "prefix": "crm"}}),
                           tenant_id="t1", principal=LOCAL_PRINCIPAL, settings=handler_ctx.settings, kek=handler_ctx.kek)
    job = await drive(handler_ctx, "t1", sub.job.id)
    assert job.state == JobState.succeeded
    manifest = json_from_export(handler_ctx, f"exports/crm/{job.id}/manifest.json")
    assert manifest["rows_total"] == 1500 and len(manifest["objects"]) == 2
    ev = await get_evidence("t1", job.progress.evidence_id)
    assert ev.pack.target["files"]["objects"] == 2 and ev.pack.target["files"]["manifest_artefact_id"]


async def test_dry_run_writes_nothing(handler_ctx, source_pg, register_source) -> None:  # noqa: ANN001
    obj = await source_pg.customers_table(rows=300, name="dry_customers")
    src = await register_source("t1")
    uat = await register_source("t1", ref="dps/src/test-pg-rw", roles={"target"}, name="uat-dry")
    snap = await discover_and_accept(handler_ctx, "t1", src.id, obj)
    sub = await submit_run(run_spec(src.id, obj, snap.id, {"write_mode": "table", "connection_id": uat.id,
                                                           "object": {"owner": source_pg.schema, "name": "never_made"}},
                                    options={"dry_run": True}),
                           tenant_id="t1", principal=LOCAL_PRINCIPAL, settings=handler_ctx.settings, kek=handler_ctx.kek)
    job = await drive(handler_ctx, "t1", sub.job.id)
    assert job.state == JobState.succeeded and job.progress.outcome == "dry_run"
    assert job.progress.verification["checked"] > 0 and job.progress.partitions_total == 0
    assert await source_pg.admin.fetchval(f"SELECT to_regclass('\"{source_pg.schema}\".never_made')") is None
```
(`db_key_enc(job_id)` reads `key_enc IS NULL` through `db.acquire_queue(None)`; `register_pvc(tenant, ctx, base_path)` creates a `pvc` connection with `settings.export_root = <tmp>`; `json_from_export(ctx, key)` reads the object from `LocalObjectStore(export_root)` — all three live in `tests/connectors/helpers.py`.)

`tests/connectors/test_run_failures.py`:
```python
from __future__ import annotations

import pytest

from dps import jobs
from dps.auth import LOCAL_PRINCIPAL
from dps.engine.policy import MethodSpec
from dps.jobs import JobState, PartitionState
from dps.jobs_models import SnapshotOverride
from dps.layers.connectors.evidence import get_evidence
from dps.layers.connectors.submit import submit_run
from tests.connectors.helpers import discover_and_accept, drive
from tests.connectors.test_run_modes import run_spec

pytestmark = [pytest.mark.db, pytest.mark.integration, pytest.mark.pg_source]


async def _setup(handler_ctx, source_pg, register_source, name: str, **accept):  # noqa: ANN001, ANN003, ANN202
    obj = await source_pg.customers_table(rows=300, name=name)
    src = await register_source("t1", name=f"src-{name}")
    uat = await register_source("t1", ref="dps/src/test-pg-rw", roles={"target"}, name=f"uat-{name}")
    snap = await discover_and_accept(handler_ctx, "t1", src.id, obj, **accept)
    return obj, src, uat, snap


async def test_row_count_mismatch_fails_before_any_write(handler_ctx, source_pg, register_source) -> None:  # noqa: ANN001
    obj, src, uat, snap = await _setup(handler_ctx, source_pg, register_source, "mismatch")
    rw = await register_source("t1", ref="dps/src/test-pg-rw", roles={"source", "target"}, name="mm-rw")
    snap2 = await discover_and_accept(handler_ctx, "t1", rw.id, obj)
    sub = await submit_run(run_spec(rw.id, obj, snap2.id, {"write_mode": "in_place", "confirm_write": True,
                                                           "expected_rows": 10}),
                           tenant_id="t1", principal=LOCAL_PRINCIPAL, settings=handler_ctx.settings, kek=handler_ctx.kek)
    job = await drive(handler_ctx, "t1", sub.job.id)
    assert (job.state, job.error_code) == (JobState.failed, "row_count_mismatch")
    ev = await get_evidence("t1", job.progress.evidence_id)
    assert ev.pack.outcome == "failed" and ev.pack.errors[0]["code"] == "row_count_mismatch"


async def test_preflight_leakage_fails_closed(handler_ctx, source_pg, register_source) -> None:  # noqa: ANN001
    obj = await source_pg.customers_table(rows=300, name="leaky")
    src = await register_source("t1", name="src-leaky")
    uat = await register_source("t1", ref="dps/src/test-pg-rw", roles={"target"}, name="uat-leaky")
    names = [r["full_name"] for r in await source_pg.admin.fetch(f'SELECT full_name FROM "{source_pg.schema}".leaky')]
    # a deliberately broken review: every name becomes the first customer's real name
    snap = await discover_and_accept(handler_ctx, "t1", src.id, obj, overrides=[SnapshotOverride(
        path="$.full_name", method=MethodSpec(method="dictionary", params={"values": names[:1]}))])
    sub = await submit_run(run_spec(src.id, obj, snap.id, {"write_mode": "table", "connection_id": uat.id,
                                                           "object": {"owner": source_pg.schema, "name": "leaky_out"}}),
                           tenant_id="t1", principal=LOCAL_PRINCIPAL, settings=handler_ctx.settings, kek=handler_ctx.kek)
    job = await drive(handler_ctx, "t1", sub.job.id)
    assert (job.state, job.error_code) == (JobState.failed, "leakage")
    assert await source_pg.admin.fetchval(f"SELECT to_regclass('\"{source_pg.schema}\".leaky_out')") is None


async def test_cancel_mid_run_ends_canceled_with_evidence(handler_ctx, source_pg, register_source) -> None:  # noqa: ANN001
    obj, src, uat, snap = await _setup(handler_ctx, source_pg, register_source, "cancel")
    sub = await submit_run(run_spec(src.id, obj, snap.id, {"write_mode": "table", "connection_id": uat.id,
                                                           "object": {"owner": source_pg.schema, "name": "cancel_out"}}),
                           tenant_id="t1", principal=LOCAL_PRINCIPAL, settings=handler_ctx.settings, kek=handler_ctx.kek)
    [c] = await jobs.claim_jobs(handler_ctx.worker_id, kinds=(jobs.JobKind.run,), batch=1)
    from dps.layers.connectors.jobs_run import prepare

    await prepare(handler_ctx, c)
    await jobs.cancel("t1", sub.job.id)
    job = await drive(handler_ctx, "t1", sub.job.id)
    assert job.state == JobState.canceled and job.progress.outcome == "canceled"
    assert {p.state for p in await jobs.list_partitions("t1", job.id)} == {PartitionState.canceled}
```
(The preflight transforms all 300 rows (`preflight_rows` = 500): the first customer's row comes out unchanged — an exact fixed point — so `LeakageError` is raised before the target is touched. Accepted snapshots are immutable, so the override is applied while the snapshot is still proposed.)

`tests/test_reveal_row.py` (offline): obfuscate a customers row with `BatchTransformer` (fpe via the rotation cipher), build the reveal map the evidence would carry (`[{"path", "entity_type", "method", "params", "anchor_path": "$.customer_id", "reveal": True}]` for full_name/email/dob), and assert `reveal_row(...)` returns the original values; and that an entry with `"reveal": False` is left as is.

`tests/db/test_groups.py`: `create_group` returns `(group, key, seed)` with `group.kid == key.kid`, `group.seed_fingerprint == seed_fingerprint(seed.seed)`, the stored row has no `dek`/seed column (`SELECT * … ` keys), duplicate name → `UniqueViolationError` mapped to `SpecError("group_name_taken", 409)`, `get_group("t2", …)` is `None`; and `submit_run` with `group_id` but a different key → `SpecError("group_key_mismatch", 409)`.

- [ ] **Step 2: Run to confirm failure**

Run: `pytest -q tests/test_reveal_row.py tests/db/test_groups.py tests/connectors/test_run_modes.py tests/connectors/test_run_failures.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'dps.layers.connectors.jobs_run'`.

- [ ] **Step 3: Write `dps/groups.py` and `dps/layers/connectors/submit.py`**

`submit_run` (the part the API and tests share; `submit_discover` is the same minus keys: parameterise, validate the selection against the connection engine, seal the selection, `create_job(kind=discover, phase=discover)`):
```python
async def submit_run(spec: RunSpec, *, tenant_id: str, principal: Principal, settings: Settings,
                     kek: bytes | None, idempotency_key: str | None = None) -> RunSubmission:
    if kek is None:
        raise SpecError("kek_unconfigured", 503, "DPS_JOB_KEK is not configured")
    source = await get_connection(tenant_id, spec.source.connection_id)
    if source is None:
        raise SpecError("connection_not_found", 404, "source connection not found")
    target = await get_connection(tenant_id, spec.target.connection_id) if spec.target.connection_id else None
    if spec.target.connection_id and target is None:
        raise SpecError("connection_not_found", 404, "target connection not found")
    snapshot = await get_snapshot(tenant_id, spec.snapshot_id)
    group = await get_group(tenant_id, spec.group_id) if spec.group_id else None
    if spec.group_id and group is None:
        raise SpecError("group_not_found", 404, "job group not found")
    validate_run(spec, principal=principal, source_conn=source, target_conn=target, snapshot=snapshot,
                 settings=settings, kek=kek, group=(group.kid, group.seed_fingerprint) if group else None)
    try:
        selection = parameterize_literals(spec.source.selection, source.engine)        # type: ignore[arg-type]
        validate_selection(selection, source.engine, snapshot.shape if snapshot else None)  # type: ignore[arg-type]
    except SelectionError as exc:
        raise SpecError(exc.code, 422, str(exc)) from None
    try:
        km = (key_material_from_caller(spec.consistency.key_material) if spec.consistency.key_material
              else generate_key_material(spec.consistency.scope)) if spec.mode == "obfuscate" else None
        seed = (seed_from_caller(spec.consistency.synthesis_seed) if spec.consistency.synthesis_seed
                else generate_synthesis_seed()) if spec.mode == "synthesize" else None
    except ValueError:
        raise SpecError("key_material_invalid", 422, "the supplied key material or seed is not valid") from None
    job_id = new_id("j")
    sealed_secret = seal_json(kek, JobSecret(key_material=km.model_dump(mode="json") if km else None,
                                             synthesis_seed=seed.model_dump(mode="json") if seed else None),
                              aad=f"dps_job.key:{job_id}")
    sealed_selection = seal_json(kek, SealedSelection(where=selection.where, query=selection.query,
                                                      params=selection.params, mongo_filter=selection.mongo_filter),
                                 aad=f"dps_job.spec:{job_id}")
    stored = spec.model_copy(update={"source": spec.source.model_copy(update={"selection": selection})})
    redacted = redact_spec(stored, engine=source.engine)                                # type: ignore[arg-type]
    redacted["consistency"]["kid"] = km.kid if km else None
    redacted["consistency"]["seed_fingerprint"] = seed_fingerprint(seed.seed) if seed else None
    job = await create_job(tenant_id=tenant_id, kind=JobKind.run, phase=JobPhase.prepare, spec=redacted,
                           mode=spec.mode, sealed_secret=sealed_secret, sealed_selection=sealed_selection,
                           kid=km.kid if km else None, group_id=spec.group_id, connection_id=source.id,
                           target_connection_id=target.id if target else None, object_ref=spec.source.object.fqn(),
                           snapshot_id=spec.snapshot_id, purpose=spec.purpose.model_dump(), created_by=principal.key_id,
                           idempotency_key=idempotency_key, job_id=job_id)
    replay = job.id != job_id
    key_out: dict[str, Any] | None = None
    if km is not None and not replay and spec.consistency.key_material is None:
        try:
            out = wrap_key_material(km, spec.consistency.wrap_key["public_key_pem"]) if spec.consistency.wrap_key else km
        except (ValueError, KeyError):
            raise SpecError("wrap_key_invalid", 422, "wrap_key must carry an RSA public_key_pem") from None
        key_out = out.model_dump(mode="json")
    seed_out = seed.model_dump(mode="json") if seed is not None and not replay and spec.consistency.synthesis_seed is None else None
    return RunSubmission(job=job, key_material=key_out, synthesis_seed=seed_out, replay=replay)
```
(A wrapped key cannot be unwrapped by DPS, so the worker uses the clear key sealed in `key_enc`; the caller holds the wrapped copy — contract §6.1. Note the wrap is validated **after** `create_job`; to avoid a queued job with an unusable wrap request, validate `wrap_key` with `wrap_key_material` on a throwaway key before `create_job` too.)

`seal_retry_secret`: validates the retry body's key material against the job (`kid` equal to `job.kid`, or seed fingerprint equal to `job.spec["consistency"]["seed_fingerprint"]` → else `SpecError("key_material_mismatch", 409)`) and seals a `JobSecret` with AAD `dps_job.key:<job id>`.

- [ ] **Step 4: Write `dps/layers/connectors/jobs_run.py`**

```python
"""Run job handlers: prepare → partitions → finalize (contract §3.1, spec §7 "Execution")."""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from dps import db, jobs
from dps.connections import Connection, get_connection, load_credentials
from dps.engine.batch import BatchTransformer, IncompatibleMethod, LeakageError
from dps.engine.policy import Policy
from dps.engine.synth.surrogates import seed_from_caller
from dps.engine.transform.crypto import key_material_from_caller
from dps.jobs import ClaimedJob, ClaimedPartition, JobInternal, JobPhase, JobState, PartitionState
from dps.jobs_models import JobSecret, RunSpec, SealedSelection, SpecError
from dps.layers.connectors.base import (
    BatchWriter, Connector, ConnectorError, KeyRange, LedgerMark, TableShape, decode_key, encode_key,
)
from dps.layers.connectors.files import FilesWriter, build_manifest, write_manifest
from dps.layers.connectors.partitions import partition_count, plan_partitions
from dps.layers.connectors.postgres import LedgerConflict, PkCollision
from dps.layers.connectors.registry import open_connector, open_target_store
from dps.layers.connectors.selection import SelectionError, validate_selection
from dps.layers.connectors.targets import (
    PrepareFailure, RunPlan, apply_pk_strategy, build_column_plans, resolve_target, worst_case_length,
)
from dps.observability import LEASES_LOST, ROWS_WRITTEN, get_logger
from dps.secrets import SealError, SecretUnavailable, open_json
from dps.snapshots import get_snapshot
from dps.storage import ObjectStore
from dps.worker import HandlerContext

logger = get_logger(__name__)


class _Lost(Exception):
    """A fenced write found the lease gone: stop without touching the row again."""


@dataclass
class RunRuntime:
    job_id: str
    spec: RunSpec
    plan: RunPlan
    shape: TableShape
    source: Connector
    target: Connector | None
    store: ObjectStore | None
    writer: BatchWriter
    transformer: BatchTransformer
    users: int = 0
    last_used: float = field(default_factory=time.monotonic)

    async def close(self) -> None:
        await self.writer.close()
        if self.target is not None and self.target is not self.source:
            await self.target.close()
        await self.source.close()


def _secret(ctx: HandlerContext, job: JobInternal) -> JobSecret:
    if job.key_enc is None:
        raise SecretUnavailable()
    return open_json(ctx.kek, job.key_enc, JobSecret, aad=f"dps_job.key:{job.id}")


def _spec(ctx: HandlerContext, job: JobInternal) -> RunSpec:
    spec = RunSpec.model_validate(job.spec)
    sealed = (open_json(ctx.kek, job.spec_enc, SealedSelection, aad=f"dps_job.spec:{job.id}")
              if job.spec_enc else SealedSelection())
    selection = spec.source.selection.model_copy(update=sealed.model_dump())
    return spec.model_copy(update={"source": spec.source.model_copy(update={"selection": selection})})


async def _connections(ctx: HandlerContext, job: JobInternal, spec: RunSpec) -> tuple[Connection, Connection | None]:
    async with db.acquire_queue(job.tenant_id) as conn:
        source = await get_connection(job.tenant_id, spec.source.connection_id, conn=conn)
        target = (await get_connection(job.tenant_id, spec.target.connection_id, conn=conn)
                  if spec.target.connection_id else None)
    if source is None or (spec.target.connection_id and target is None):
        raise PrepareFailure("connection_failed", "a connection of this job no longer exists")
    return source, target


async def _open(ctx: HandlerContext, job: JobInternal, conn: Connection, action: str) -> Connector:
    async with db.acquire_queue(job.tenant_id) as c:
        creds = await load_credentials(conn, settings=ctx.settings, kek=ctx.kek, conn=c)
    return await open_connector(conn, ctx.settings, action=action, kek=ctx.kek, job_id=job.id, credentials=creds)


def _transformer(ctx: HandlerContext, spec: RunSpec, secret: JobSecret, plan: RunPlan, policy: Policy,
                 shape: TableShape) -> BatchTransformer:
    key = key_material_from_caller(secret.key_material) if spec.mode == "obfuscate" and secret.key_material else None
    seed = seed_from_caller(secret.synthesis_seed) if spec.mode == "synthesize" and secret.synthesis_seed else None
    return BatchTransformer(ctx.engine, policy=policy, mode=spec.mode, columns=plan.columns, shape=shape, key=key,
                            seed=seed, locale=plan.locale, unclassified=spec.options.unclassified,
                            anchor=tuple(plan.anchor) if plan.anchor else None,  # type: ignore[arg-type]
                            verify=spec.options.verify)


async def _stage(ctx: HandlerContext, job: ClaimedJob, stage: str) -> None:
    if not await jobs.advance_job(ctx.worker_id, job.id, phase=job.phase, state=JobState.running,
                                  progress={"stage": stage}):
        raise _Lost()


async def _fail_to_finalize(ctx: HandlerContext, job: ClaimedJob, code: str, detail: str) -> None:
    await jobs.advance_job(ctx.worker_id, job.id, phase=JobPhase.finalize, state=JobState.queued,
                           progress={"outcome": "failed", "stage": "prepare failed"},
                           error_code=code, error=detail[:500])


async def prepare(ctx: HandlerContext, job: ClaimedJob) -> None:
    """Contract §3.1 ``prepare`` (steps 1–11 in the task text)."""
    try:
        await _prepare(ctx, job)
    except _Lost:
        LEASES_LOST.labels("job").inc()
    except PrepareFailure as exc:
        await _fail_to_finalize(ctx, job, exc.code, exc.detail)
    except (SpecError, SelectionError) as exc:
        await _fail_to_finalize(ctx, job, exc.code, str(exc))
    except IncompatibleMethod as exc:
        await _fail_to_finalize(ctx, job, exc.code, str(exc))
    except LeakageError as exc:
        await _fail_to_finalize(ctx, job, "leakage", str(exc))
    except (SealError, SecretUnavailable):
        await _fail_to_finalize(ctx, job, "secret_unavailable", "the job's sealed material could not be opened")
    except ConnectorError as exc:
        if exc.retryable and job.attempts < job.max_attempts:
            await jobs.retry_job(ctx.worker_id, job.id, error_code=exc.code, error=str(exc),
                                 backoff_base=ctx.settings.job_retry_base_seconds,
                                 backoff_cap=ctx.settings.job_retry_max_seconds)
        else:
            await _fail_to_finalize(ctx, job, exc.code, str(exc))
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - unexpected: retry; never put the message in the row
        logger.error("prepare_failed", job_id=job.id, error=type(exc).__name__)
        await jobs.retry_job(ctx.worker_id, job.id, error_code="internal_error", error=type(exc).__name__,
                             backoff_base=ctx.settings.job_retry_base_seconds,
                             backoff_cap=ctx.settings.job_retry_max_seconds)


async def _prepare(ctx: HandlerContext, job: ClaimedJob) -> None:
    settings = ctx.settings
    spec, secret = _spec(ctx, job), _secret(ctx, job)
    source_conn, target_conn = await _connections(ctx, job, spec)
    async with db.acquire_queue(job.tenant_id) as conn:
        snapshot = await get_snapshot(job.tenant_id, spec.snapshot_id, conn=conn)
    if snapshot is None:
        raise PrepareFailure("snapshot_stale", "the accepted snapshot no longer exists")
    policy = ctx.engine.policy(spec.policy or snapshot.policy_name)
    source = await _open(ctx, job, source_conn, "prepare")
    target: Connector | None = None
    try:
        shape = await source.describe(spec.source.object)
        for col in snapshot.columns:
            try:
                live = shape.column(col.name)
            except KeyError:
                raise PrepareFailure("snapshot_stale", f"column {col.path} is no longer in the table") from None
            if live.kind != col.kind:
                raise PrepareFailure("snapshot_stale", f"column {col.path} changed type since review")
        if not shape.key:
            raise PrepareFailure("no_key", "the table has no primary key or unique not-null index")
        selection = spec.source.selection
        validate_selection(selection, source.engine, shape)
        await _stage(ctx, job, "counting rows")
        rows = await source.count(shape, selection, selection.params)
        if spec.target.expected_rows is not None and rows > spec.target.expected_rows:
            raise PrepareFailure("row_count_mismatch",
                                 f"{rows} rows are selected but {spec.target.expected_rows} were confirmed")
        projection = [c.name for c in shape.columns if not selection.columns
                      or c.name in set(selection.columns) | set(shape.key)]
        plans = build_column_plans(snapshot, mode=spec.mode, write_mode=spec.target.write_mode,
                                   pk_strategy=spec.target.pk_strategy, projection=projection)
        mode = spec.target.write_mode
        store: ObjectStore | None = None
        target_shape: TableShape | None = None
        if mode == "files":
            assert target_conn is not None
            store = await open_target_store(target_conn, settings, kek=ctx.kek)
        else:
            same = mode in ("in_place", "append") and spec.target.connection_id in (None, source_conn.id)
            target = source if same else await _open(ctx, job, target_conn, "prepare")   # type: ignore[arg-type]
            try:
                target_shape = await target.describe(spec.target.object or spec.source.object)
            except ConnectorError as exc:
                if exc.code != "object_not_found":
                    raise
        resolved, plans = resolve_target(spec, job_id=job.id, source_conn=source_conn,
                                         target_conn=target_conn or source_conn, source_shape=shape,
                                         target_shape=target_shape, plans=plans, projection=projection)
        for p in plans:
            worst = worst_case_length(p, shape.column(p.name))
            if worst is not None and p.length is not None and worst > p.length:
                raise PrepareFailure("length_overflow", f"{p.path}: worst-case output {worst} > column length {p.length}")
        anchor = next(([c.name, c.entity_type] for c in snapshot.columns if c.anchor and c.entity_type), None)
        plan = RunPlan(columns=plans, anchor=anchor, key=shape.key, projection=projection, target=resolved,
                       source_object=shape.object.fqn(), policy_name=policy.name, policy_hash=policy.config_hash(),
                       snapshot_id=snapshot.id, snapshot_hash=snapshot.content_hash,
                       locale=policy.locale.default or settings.default_locale,
                       batch_rows=spec.source.batch_rows or settings.batch_rows,
                       same_table_append=mode == "append" and resolved.same_as_source)
        transformer = _transformer(ctx, spec, secret, plan, policy, shape)
        await _stage(ctx, job, "preflight")
        sample = await source.sample(shape, selection, selection.params, max(1, min(settings.preflight_rows, rows)))
        preflight = await asyncio.to_thread(transformer.transform, [dict(r) for r in sample])
        del sample
        if mode == "files":
            assert store is not None and target_conn is not None and spec.target.files is not None
            writer: BatchWriter = FilesWriter(store, spec.target.files, job_id=job.id,
                                              base_prefix=target_conn.params.prefix.strip("/"),
                                              columns=[next(p.output_column() for p in plans if p.name == n)
                                                       if any(p.name == n for p in plans) else shape.column(n)
                                                       for n in projection])
        else:
            assert target is not None
            writer = await target.writer(resolved, shape)
        collisions = 0
        if mode == "append" and spec.target.pk_strategy is not None and spec.target.pk_strategy.kind in ("offset", "token"):
            await _stage(ctx, job, "checking key collisions")
            collisions = await writer.precheck_collisions(
                _transformed_keys(source, shape, selection, spec, transformer))
            if collisions:
                raise PrepareFailure("pk_collision", f"{collisions} transformed keys already exist in the target")
        await _stage(ctx, job, "preparing target")
        report = await writer.prepare(dry_run=spec.options.dry_run)
        plan.prepare = {"created": report.created, "truncated": report.truncated, "collisions": collisions,
                        "existing_rows": report.existing_rows}
        counters = {**preflight.values_by_method, **{f"fallback:{k}": v for k, v in preflight.fallbacks.items()}}
        base = {"rows_estimated": rows, "stage": ""}
        if spec.options.dry_run:
            await jobs.advance_job(ctx.worker_id, job.id, phase=JobPhase.finalize, state=JobState.queued,
                                   plan=plan.model_dump(mode="json"),
                                   progress={**base, "outcome": "dry_run", "values_by_method": counters,
                                             "verification": {"checked": preflight.verification.checked,
                                                              "hits": len(preflight.verification.hits)}})
            return
        max_rows = selection.max_rows
        total = min(rows, max_rows) if max_rows else rows
        if total == 0:
            await jobs.advance_job(ctx.worker_id, job.id, phase=JobPhase.finalize, state=JobState.queued,
                                   plan=plan.model_dump(mode="json"), progress=base)
            return
        upper = await source.key_at_offset(shape, selection, selection.params, max_rows) if max_rows else None
        if plan.same_table_append and report.append_upper is not None:
            upper = (report.append_upper,) if upper is None else (min(int(upper[0]), report.append_upper),)
        count = partition_count(total, partition_rows=spec.source.partition_rows or settings.partition_rows,
                                max_partitions=settings.max_partitions)
        keys = await source.sample_keys(shape, selection, selection.params, min(count * 50, 20_000), upper=upper)
        ranges = plan_partitions(keys, count=count, columns=shape.key_columns(), upper=upper)
        if await jobs.add_partitions(ctx.worker_id, job.id, ranges, plan=plan.model_dump(mode="json"),
                                     progress=base) != len(ranges):
            raise _Lost()
    finally:
        if target is not None and target is not source:
            await target.close()
        await source.close()


async def _transformed_keys(source: Connector, shape: TableShape, selection: Any, spec: RunSpec,
                            transformer: BatchTransformer):  # noqa: ANN202 - async generator
    key_sel = selection.model_copy(update={"columns": list(shape.key)})
    strategy = spec.target.pk_strategy
    async for batch in source.read_partition(shape, key_sel, selection.params, KeyRange(), after_key=None, batch=10_000):
        if strategy is not None and strategy.kind == "token":
            batch = (await asyncio.to_thread(transformer.transform, batch)).rows
        else:
            batch = apply_pk_strategy(batch, strategy=strategy, key=shape.key)   # type: ignore[arg-type]
        yield [tuple(r[k] for k in shape.key) for r in batch]
```

`RuntimeCache.get` builds a `RunRuntime` from the stored `RunPlan` (`RunPlan.model_validate(job.plan)`) under a per-job `asyncio.Lock`: `_spec`, `_secret`, `_connections`, source connector (`action="run"`), target connector (the source for in_place/same-connection append) or target store, `describe` of the source (the `RunPlan` carries no shape; a describe per process is cheap), the writer (`FilesWriter` or `target.writer(plan.target, shape)`), and `_transformer`; `users += 1`. `release` decrements and stamps `last_used`; `close_idle` (called by the worker's reaper loop) closes runtimes unused for `idle_seconds`; `drop` closes and removes one (finalize); `close_all` on drain.

`run_partition`:
```python
async def run_partition(ctx: HandlerContext, job: JobInternal, part: ClaimedPartition) -> None:
    runtime: RunRuntime | None = None
    try:
        runtime = await RUNTIMES.get(ctx, job)
        await _run_partition(ctx, job, runtime, part)
    except _Lost:
        LEASES_LOST.labels("partition").inc()
    except LedgerConflict:
        LEASES_LOST.labels("ledger").inc()
    except LeakageError as exc:
        await jobs.finish_partition(ctx.worker_id, part.id, PartitionState.failed, error_code="leakage", error=str(exc))
    except IncompatibleMethod as exc:
        await jobs.finish_partition(ctx.worker_id, part.id, PartitionState.failed, error_code=exc.code, error=str(exc))
    except PkCollision as exc:
        await jobs.finish_partition(ctx.worker_id, part.id, PartitionState.failed, error_code=exc.code, error=str(exc))
    except (SealError, SecretUnavailable):
        await jobs.finish_partition(ctx.worker_id, part.id, PartitionState.failed, error_code="secret_unavailable",
                                    error="the job's sealed material could not be opened")
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - ConnectorError and the unexpected: retry with backoff
        code = exc.code if isinstance(exc, ConnectorError) else "internal_error"
        await jobs.retry_partition(ctx.worker_id, part.id, error_code=code, error=type(exc).__name__,
                                   backoff_base=ctx.settings.job_retry_base_seconds,
                                   backoff_cap=ctx.settings.job_retry_max_seconds)
    finally:
        if runtime is not None:
            RUNTIMES.release(job.id)


async def _run_partition(ctx: HandlerContext, job: JobInternal, rt: RunRuntime, part: ClaimedPartition) -> None:
    worker, mode = ctx.worker_id, rt.plan.target.write_mode
    cols = rt.shape.key_columns()
    after = decode_key(part.last_key, cols) if part.last_key else None
    batch_no, read_total = part.batch_no, part.rows_written
    if mode in ("in_place", "append"):
        mark = await rt.writer.read_ledger(job.id, part.partition_no)
        if mark is not None and mark.batch_no > part.batch_no:
            delta = max(0, mark.rows - part.rows_written)
            if not await jobs.checkpoint(worker, part.id, last_key=mark.last_key, batch_no=mark.batch_no,
                                         rows_read=delta, rows_written=delta, rows_skipped=0, values_by_method={},
                                         verify_checked=0):
                raise _Lost()
            after, batch_no, read_total = decode_key(mark.last_key, cols), mark.batch_no, mark.rows
    elif mode == "files" and (part.batch_no > 0 or part.attempts > 1):
        assert isinstance(rt.writer, FilesWriter)
        rt.writer.reset_partition(part.partition_no)
        if not await jobs.reset_partition_progress(worker, part.id):
            raise _Lost()
        after, batch_no, read_total = None, 0, 0
    selection = rt.spec.source.selection
    key_range = KeyRange(lower=part.lower_key, upper=part.upper_key)
    async for rows in rt.source.read_partition(rt.shape, selection, selection.params, key_range, after_key=after,
                                               batch=rt.plan.batch_rows):
        last = tuple(rows[-1][k] for k in rt.shape.key)
        result = await asyncio.to_thread(rt.transformer.transform, rows)
        out = result.rows
        if mode == "append" and rt.spec.target.pk_strategy is not None:
            out = apply_pk_strategy(out, strategy=rt.spec.target.pk_strategy, key=rt.shape.key)
        batch_no += 1
        read_total += len(rows)
        encoded = encode_key(last, cols)
        written = await rt.writer.write_batch(out, mark=LedgerMark(job_id=job.id, partition_no=part.partition_no,
                                                                   batch_no=batch_no, last_key=encoded,
                                                                   rows=read_total))
        ROWS_WRITTEN.labels(rt.spec.mode, mode).inc(written)
        counters = {**result.values_by_method, **{f"fallback:{k}": v for k, v in result.fallbacks.items()},
                    **{f"col:{k}": v for k, v in result.values_by_column.items()}}
        if not await jobs.checkpoint(worker, part.id, last_key=encoded, batch_no=batch_no, rows_read=len(rows),
                                     rows_written=written, rows_skipped=max(0, len(rows) - written),
                                     values_by_method=counters, verify_checked=result.verification.checked):
            if await jobs.partition_control(worker, part.id) == "cancel":
                await jobs.abandon_partition(worker, part.id)
            raise _Lost()
        control = await jobs.partition_control(worker, part.id)
        if control == "pause":
            await jobs.release(worker, job_ids=[], partition_ids=[part.id])
            return
        if control == "cancel":
            await jobs.abandon_partition(worker, part.id)
            return
        if control == "lost":
            raise _Lost()
    meta = await rt.writer.finish_partition(job.id, part.partition_no)
    if not await jobs.finish_partition(worker, part.id, PartitionState.done, meta=meta):
        raise _Lost()
```

`finalize` follows the task text; the evidence assembly is `build_pack(EvidenceInputs(...))` + `save_evidence(...)` (Task 21), the audit event is `AuditEvent(trace_id=job.id, route="worker:finalize", tenant_id=job.tenant_id, key_id=job.created_by or "-", mode=job.mode or "", policy=plan.policy_name if plan else "-", action="job.finalize", outcome=outcome, job_id=job.id, counts={"rows_written": …, "partitions": …})`, and the terminal transition is `advance_job(…, state={"succeeded": succeeded, "dry_run": succeeded, "failed": failed, "canceled": canceled}[outcome], progress={"outcome": outcome, "evidence_id": record.id, "stage": ""}, purge_secret=True)`. The live-lease wait for canceled partitions is one query on the worker pool: `SELECT count(*) FROM dps_job_partition WHERE job_id = $1 AND state = 'canceled' AND locked_by IS NOT NULL AND lease_expires_at > now()`.

`reveal_row` (append to `dps/engine/batch.py`): reveals the anchor column first when it is reversible, recomputes the row anchor with `row_anchor`, then calls M0's `reveal()` once over the row document with one `RevealHint` per `reveal: true` entry whose `reverse_params` are derived the way the method produced them: `email` → `{"sub": "fpe_text" if engine.fpe_enabled else "det_token", "domain": params.get("domain", "keep")}`; `phone` → `{"keep_prefix": <country-code length of the token>}` (the code is kept verbatim); `date_shift` → `{"anchor": <recomputed anchor>, "fmt": "iso-datetime" if the column is a timestamp else "%Y-%m-%d"}`; others `{}`. A value that ran a fallback (`det_token` instead of FPE) is retried with `det_token` when the recorded method raises `MethodError`.

- [ ] **Step 5: Run tests, lint, types**

Run: `pytest -q tests/test_reveal_row.py tests/db/test_groups.py tests/connectors/test_run_modes.py tests/connectors/test_run_failures.py && ruff check dps tests tools && mypy dps`
Expected: `2 + 1 + 5 + 3 = 11 passed`, clean.

- [ ] **Step 6: Commit**

```bash
git add dps/layers/connectors/jobs_run.py dps/layers/connectors/submit.py dps/groups.py dps/engine/batch.py tests/connectors/helpers.py tests/connectors/test_run_modes.py tests/connectors/test_run_failures.py tests/db/test_groups.py tests/test_reveal_row.py
git commit -m "feat(jobs): prepare/partition/finalize handlers for every write mode with ledger resume and evidence" -- dps/layers/connectors/jobs_run.py dps/layers/connectors/submit.py dps/groups.py dps/engine/batch.py tests/connectors/helpers.py tests/connectors/test_run_modes.py tests/connectors/test_run_failures.py tests/db/test_groups.py tests/test_reveal_row.py
```

---

### Task 23: Worker process (`python -m dps.worker`) and retention

**Files:**
- Modify: `dps/worker.py` (add `Consumer`, `build_context`, health server, `main`)
- Create: `dps/retention.py`, `dps/__main__`-style entry: `dps/worker.py` ends with `if __name__ == "__main__": main()`
- Test: `tests/db/test_consumer.py`, `tests/db/test_retention.py`, `tests/db/test_worker_process.py`

Public interface (contract §3.5; `build_context`, `serve_health` are additions):
```python
class Consumer:
    def __init__(self, settings: Settings, *, kinds: tuple[JobKind, ...], handlers: dict[JobPhase, Handler],
                 partition_handler: PartitionHandler | None, concurrency: int, ctx: HandlerContext,
                 role: str = "worker", worker_id: str | None = None) -> None
    async def run(self) -> None              # claim / heartbeat / reaper / listen loops until stop(); then drain
    def stop(self) -> None
    @property
    def in_flight(self) -> int
def build_context(settings: Settings) -> HandlerContext       # RuntimeError when DPS_JOB_KEK is missing
async def serve_health(settings: Settings, port: int, consumer: Consumer) -> None   # /health /readyz /metrics
def main() -> None                                            # python -m dps.worker
# dps/retention.py
async def retention_job(ctx: HandlerContext, job: ClaimedJob) -> None
```

**Loops (from `di/worker.py`).** *Claim*: while running, `free = concurrency − in_flight`; claim job rows first (`claim_jobs(kinds, batch=min(free, job_claim_batch))` — prepare/finalize unblock partitions), dispatch each to `handlers[job.phase]`; then, if a partition handler is set, `claim_partitions(batch=remaining)` and dispatch each with the job's `JobInternal` (`load_job_internal`, cached per claim round); with nothing claimed, wait on the LISTEN wake event or `job_poll_interval_seconds`. *Heartbeat*: every `min(job_lease, partition_lease) / 4` seconds call `heartbeat(job_ids, partition_ids)`; any in-flight id not returned was reclaimed → cancel its task at once (`LEASES_LOST`); also `worker_heartbeat(busy=in_flight, draining)`. *Reaper* (every instance; idempotent): every `job_reaper_interval_seconds` × U(0.8, 1.2): `reap`, `sample_throughput`, `RUNTIMES.close_idle()`, `ensure_retention_job(retention_interval_hours)` when `retention` is among the kinds, and `queue_stats()` → `QUEUE_DEPTH` gauges. *Listen*: a dedicated non-pooled connection (worker login) with `add_listener("dps_job_new")` setting the wake event; reconnect after 5 s on failure (poll is the floor). *Drain* (SIGTERM/SIGINT): stop claiming, mark `draining`, wait up to `job_drain_timeout_seconds`, cancel what is left and `release` it (no attempt charged), `RUNTIMES.close_all()`, `unregister_worker`.

**Health server.** A minimal FastAPI app served by `uvicorn.Server` inside the worker's loop on `worker_metrics_port` (skipped when the port is 0): `/health` → `{"status": "ok"}`; `/readyz` → 200 when the DB answers `SELECT 1`, migrations verify and the KEK is loaded (`{"ready", "components": {"db", "migrations", "kek", "queue"}}`), else 503; `/metrics` → the Prometheus exposition.

**Retention (contract §3.6).** For each tenant (`dps_tenant` ∪ distinct `dps_job.tenant_id`), on the worker pool with the tenant GUC bound: delete up to 500 terminal jobs with `expires_at < now()` (their artefact objects first, via `ArtefactService.delete_for_job`; the rows cascade to partitions, evidence and artefact rows; snapshots keep living with `job_id` set null); delete `proposed`/`superseded` snapshots older than the tenant's retention; null `secret_enc` where `secret_expires_at < now()`; null `key_enc` on terminal jobs (defence in depth). Never touches `dps_audit_log` (`migrate --maintain` does) and never touches exported files (they are the customer's output). Counters go to `progress.values_by_method` as `retention:jobs`, `retention:artefacts`, `retention:snapshots`, `retention:secrets`, `retention:keys`; `dry_run` counts only.

- [ ] **Step 1: Write the failing tests**

`tests/db/test_consumer.py`:
```python
from __future__ import annotations

import asyncio

import pytest

from dps import jobs
from dps.jobs import JobKind, JobPhase, JobState, PartitionState
from dps.worker import Consumer

pytestmark = pytest.mark.db


async def _wait(predicate, timeout: float = 10.0) -> None:  # noqa: ANN001
    deadline = asyncio.get_running_loop().time() + timeout
    while not await predicate():
        assert asyncio.get_running_loop().time() < deadline, "timed out"
        await asyncio.sleep(0.05)


async def test_consumer_dispatches_jobs_and_partitions(migrated_db, handler_ctx, queue_settings) -> None:  # noqa: ANN001
    settings = queue_settings(job_poll_interval_seconds=0.1, job_reaper_interval_seconds=0.2)
    seen: list[str] = []

    async def on_job(ctx, job):  # noqa: ANN001, ANN202
        seen.append(f"{job.phase}:{job.id}")
        if job.phase == JobPhase.prepare:
            from dps.layers.connectors.base import KeyRange
            await jobs.add_partitions(ctx.worker_id, job.id, [KeyRange(), KeyRange()])
        else:
            await jobs.advance_job(ctx.worker_id, job.id, phase=job.phase, state=JobState.succeeded)

    async def on_part(ctx, job, part):  # noqa: ANN001, ANN202
        seen.append(f"part:{part.partition_no}")
        await jobs.finish_partition(ctx.worker_id, part.id, PartitionState.done)

    run_job = await jobs.create_job(tenant_id="t1", kind=JobKind.run, phase=JobPhase.prepare, spec={"source": {"parallelism": 2}})
    consumer = Consumer(settings, kinds=(JobKind.run,), handlers={JobPhase.prepare: on_job, JobPhase.finalize: on_job},
                        partition_handler=on_part, concurrency=4, ctx=handler_ctx)
    task = asyncio.create_task(consumer.run())
    await _wait(lambda: _state(run_job.id, JobState.succeeded))
    consumer.stop()
    await task
    assert seen[0] == f"prepare:{run_job.id}" and seen[-1] == f"finalize:{run_job.id}"
    assert sorted(s for s in seen if s.startswith("part:")) == ["part:0", "part:1"]


async def _state(job_id: str, state: JobState) -> bool:
    job = await jobs.get_job("t1", job_id)
    return job is not None and job.state == state


async def test_reclaimed_task_is_canceled_and_drain_releases(migrated_db, handler_ctx, queue_settings) -> None:  # noqa: ANN001
    settings = queue_settings(job_lease_seconds=2, job_poll_interval_seconds=0.1, job_drain_timeout_seconds=0.2)
    started = asyncio.Event()
    canceled = asyncio.Event()

    async def slow(ctx, job):  # noqa: ANN001, ANN202
        started.set()
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            canceled.set()
            raise

    a = await jobs.create_job(tenant_id="t1", kind=JobKind.discover, phase=JobPhase.discover, spec={})
    consumer = Consumer(settings, kinds=(JobKind.discover,), handlers={JobPhase.discover: slow},
                        partition_handler=None, concurrency=1, ctx=handler_ctx)
    task = asyncio.create_task(consumer.run())
    await asyncio.wait_for(started.wait(), 5)
    from dps import db
    async with db.acquire_queue(None) as conn:                       # someone else "owns" it now
        await conn.execute("UPDATE dps.dps_job SET locked_by = 'other' WHERE id = $1", a.id)
    await asyncio.wait_for(canceled.wait(), 5)
    started.clear()
    b = await jobs.create_job(tenant_id="t1", kind=JobKind.discover, phase=JobPhase.discover, spec={})
    await asyncio.wait_for(started.wait(), 5)
    consumer.stop()
    await task
    internal = await jobs.load_job_internal(b.id)
    assert internal.state == JobState.queued and internal.attempts == 0 and internal.locked_by is None
```

`tests/db/test_retention.py`:
```python
from __future__ import annotations

import pytest

from dps import db, jobs
from dps.jobs import JobKind, JobPhase, JobState
from dps.retention import retention_job

pytestmark = pytest.mark.db


async def test_retention_removes_expired_material(migrated_db, handler_ctx, kek) -> None:  # noqa: ANN001
    old = await jobs.create_job(tenant_id="t1", kind=JobKind.run, phase=JobPhase.prepare, spec={}, sealed_secret=b"\x01k")
    art = await handler_ctx.artefacts.save(tenant_id="t1", job_id=old.id, kind="evidence_json", data=b"{}",
                                           content_type="application/json")
    fresh = await jobs.create_job(tenant_id="t1", kind=JobKind.run, phase=JobPhase.prepare, spec={}, sealed_secret=b"\x01k")
    async with db.acquire_queue("t1") as conn:
        await conn.execute("UPDATE dps.dps_job SET state = 'succeeded', expires_at = now() - interval '1 day' "
                           "WHERE id = $1", old.id)
        await conn.execute("UPDATE dps.dps_job SET state = 'failed' WHERE id = $1", fresh.id)
        await conn.execute("INSERT INTO dps.dps_connection (id, tenant_id, name, engine, params, secret_enc, "
                           "secret_expires_at) VALUES ('c_x', 't1', 'x', 'postgres', '{}', '\\x01'::bytea, now() - interval '1 hour')")
        await conn.execute("INSERT INTO dps.dps_snapshot (id, tenant_id, connection_id, engine, object_ref, policy_name, "
                           "policy_hash, shape, columns, sample_rows, content_hash, created_at) VALUES ('snap_old', 't1', "
                           "'c_x', 'postgres', 'a.b', 'p', 'h', '{}', '[]', 0, 'h', now() - interval '90 days')")
    await jobs.ensure_retention_job(6)
    [r] = await jobs.claim_jobs(handler_ctx.worker_id, kinds=(JobKind.retention,), batch=1)
    await retention_job(handler_ctx, r)
    done = await jobs.load_job_internal(r.id)
    counts = done.progress.values_by_method
    assert done.state == JobState.succeeded and counts["retention:jobs"] == 1 and counts["retention:artefacts"] == 1
    assert counts["retention:secrets"] == 1 and counts["retention:snapshots"] == 1 and counts["retention:keys"] == 1
    assert await jobs.get_job("t1", old.id) is None and await handler_ctx.artefacts.get_row("t1", art) is None
    assert (await jobs.load_job_internal(fresh.id)).key_enc is None
```

`tests/db/test_worker_process.py`:
```python
from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request

import pytest

from tests.fixtures.db import worker_env

pytestmark = pytest.mark.db


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_worker_serves_health_and_drains_on_sigterm(db_settings, tmp_path) -> None:  # noqa: ANN001
    port = _free_port()
    env = {**os.environ, **worker_env(db_settings, tmp_path), "DPS_WORKER_METRICS_PORT": str(port)}
    proc = subprocess.Popen([sys.executable, "-m", "dps.worker"], env=env)
    try:
        for _ in range(100):
            try:
                if urllib.request.urlopen(f"http://127.0.0.1:{port}/readyz", timeout=1).status == 200:
                    break
            except OSError:
                time.sleep(0.2)
        else:
            pytest.fail("worker never became ready")
        assert b"dps_jobs_claimed_total" in urllib.request.urlopen(f"http://127.0.0.1:{port}/metrics").read()
        proc.send_signal(signal.SIGTERM)
        assert proc.wait(timeout=30) == 0
    finally:
        if proc.poll() is None:
            proc.kill()
```
`worker_env(settings, tmp_path) -> dict[str, str]` (in `tests/fixtures/db.py`): the `DPS_*` environment of a worker pointed at the test database — PG host/port/database, `dps_app`/`dps_worker_login` logins and passwords, `DPS_MIGRATIONS_MODE=off`, `DPS_JOB_KEK` (base64 of `TEST_KEK`), `DPS_SECRET_DIR` = `settings.secret_dir` (the same directory the test's `source_secrets` wrote, so the subprocess resolves the same `secret_ref`s), `DPS_ARTEFACT_DIR` = `settings.artefact_dir`, `DPS_EXPORT_ROOT` = `settings.export_root` (all under `tmp_path` in the fixtures), `DPS_ENV=local`, `DPS_LLM_PROVIDER=stub`, `DPS_NER_ENABLED=false`, `DPS_FPE_ENABLED=true` when `fastfpe` imports else `false`, and fast queue timings (`DPS_JOB_POLL_INTERVAL_SECONDS=0.2`, `DPS_JOB_REAPER_INTERVAL_SECONDS=1`, `DPS_JOB_RETRY_BASE_SECONDS=0.5`, `DPS_JOB_RETRY_MAX_SECONDS=2`, `DPS_PARTITION_LEASE_SECONDS=6`, `DPS_JOB_LEASE_SECONDS=10`, `DPS_WORKER_CONCURRENCY=2`). Task 26 reuses it.

- [ ] **Step 2: Run to confirm failure**

Run: `pytest -q tests/db/test_consumer.py tests/db/test_retention.py tests/db/test_worker_process.py`
Expected: FAIL — `Consumer` and `dps.retention` do not exist.

- [ ] **Step 3: Write the consumer** (append to `dps/worker.py`)

```python
class Consumer:
    """Claims job rows and partitions and runs their handlers under leases (contract §3.5)."""

    def __init__(self, settings: Settings, *, kinds: tuple[JobKind, ...], handlers: dict[JobPhase, Handler],
                 partition_handler: PartitionHandler | None, concurrency: int, ctx: HandlerContext,
                 role: str = "worker", worker_id: str | None = None) -> None:
        self.settings, self.kinds, self.handlers = settings, kinds, handlers
        self.partition_handler, self.concurrency, self.ctx, self.role = partition_handler, concurrency, ctx, role
        self.worker_id = worker_id or ctx.worker_id
        self._jobs: dict[str, asyncio.Task[None]] = {}
        self._parts: dict[int, asyncio.Task[None]] = {}
        self._stop = asyncio.Event()
        self._wake = asyncio.Event()
        self._draining = False

    @property
    def in_flight(self) -> int:
        return len(self._jobs) + len(self._parts)

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    async def run(self) -> None:
        await jobs.register_worker(self.worker_id, role=self.role, version=__version__)
        loops = [asyncio.create_task(fn(), name=f"worker:{fn.__name__}")
                 for fn in (self._claim_loop, self._heartbeat_loop, self._reaper_loop, self._listen_loop)]
        await self._stop.wait()
        self._draining = True
        for t in loops:
            t.cancel()
        await asyncio.gather(*loops, return_exceptions=True)
        await self._drain()

    def _spawn(self, coro: Awaitable[None], *, job_id: str | None = None, part_id: int | None = None) -> None:
        task = asyncio.create_task(self._guard(coro), name=f"run:{job_id or part_id}")
        if job_id is not None:
            self._jobs[job_id] = task
            task.add_done_callback(lambda _t, k=job_id: self._jobs.pop(k, None))
        else:
            assert part_id is not None
            self._parts[part_id] = task
            task.add_done_callback(lambda _t, k=part_id: self._parts.pop(k, None))

    async def _guard(self, coro: Awaitable[None]) -> None:
        try:
            await coro
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - handlers own their error paths; this is the last net
            logger.error("handler_crashed", error=type(exc).__name__)

    async def _claim_loop(self) -> None:
        while not self._stop.is_set():
            claimed_any = False
            try:
                free = self.concurrency - self.in_flight
                if free > 0:
                    for job in await jobs.claim_jobs(self.worker_id, kinds=self.kinds,
                                                     batch=min(free, self.settings.job_claim_batch)):
                        handler = self.handlers.get(job.phase)
                        if handler is None:
                            await jobs.release(self.worker_id, job_ids=[job.id], partition_ids=[])
                            continue
                        self._spawn(handler(self.ctx, job), job_id=job.id)
                        claimed_any = True
                free = self.concurrency - self.in_flight
                if free > 0 and self.partition_handler is not None:
                    cache: dict[str, JobInternal | None] = {}
                    for part in await jobs.claim_partitions(self.worker_id, batch=free):
                        if part.job_id not in cache:
                            cache[part.job_id] = await jobs.load_job_internal(part.job_id)
                        internal = cache[part.job_id]
                        if internal is None:
                            continue
                        self._spawn(self.partition_handler(self.ctx, internal, part), part_id=part.id)
                        claimed_any = True
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - a DB hiccup must not kill the loop
                logger.warning("claim_failed", error=type(exc).__name__)
            if claimed_any:
                continue
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self.settings.job_poll_interval_seconds)
            except TimeoutError:
                pass
            self._wake.clear()

    async def _heartbeat_loop(self) -> None:
        interval = max(0.5, min(self.settings.job_lease_seconds, self.settings.partition_lease_seconds) / 4)
        while not self._stop.is_set():
            await asyncio.sleep(interval)
            try:
                owned_jobs, owned_parts = await jobs.heartbeat(self.worker_id, job_ids=list(self._jobs),
                                                               partition_ids=list(self._parts))
                for job_id, task in list(self._jobs.items()):
                    if job_id not in owned_jobs and not task.done():
                        LEASES_LOST.labels("job").inc()
                        task.cancel()
                for part_id, task in list(self._parts.items()):
                    if part_id not in owned_parts and not task.done():
                        LEASES_LOST.labels("partition").inc()
                        task.cancel()
                await jobs.worker_heartbeat(self.worker_id, busy=self.in_flight, draining=self._draining)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning("heartbeat_failed", error=type(exc).__name__)

    async def _reaper_loop(self) -> None:
        while not self._stop.is_set():
            await asyncio.sleep(self.settings.job_reaper_interval_seconds * random.uniform(0.8, 1.2))
            try:
                result = await jobs.reap(backoff_base=self.settings.job_retry_base_seconds,
                                         backoff_cap=self.settings.job_retry_max_seconds)
                if result.jobs_requeued or result.partitions_requeued or result.jobs_dead or result.partitions_dead:
                    logger.info("reaped", counts=result.model_dump())
                await jobs.sample_throughput()
                from dps.layers.connectors.jobs_run import RUNTIMES   # lazy: jobs_run imports this module
                await RUNTIMES.close_idle()
                if JobKind.retention in self.kinds:
                    await jobs.ensure_retention_job(self.settings.retention_interval_hours)
                for row in await jobs.queue_stats():
                    QUEUE_DEPTH.labels(row["table"], row["state"]).set(row["n"])
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning("reaper_failed", error=type(exc).__name__)

    async def _listen_loop(self) -> None:
        s = self.settings
        user, password = s.pg_worker_login
        while not self._stop.is_set():
            conn: asyncpg.Connection | None = None
            try:
                conn = await asyncpg.connect(host=s.pg_host, port=s.pg_port, user=user, password=password or None,
                                             database=s.pg_database, ssl=db._ssl_context(s))
                await conn.add_listener(jobs.NOTIFY_CHANNEL, lambda *_a: self._wake.set())
                await self._stop.wait()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - polling is the floor; LISTEN only cuts latency
                logger.warning("listen_lost", error=type(exc).__name__)
                await asyncio.sleep(5)
            finally:
                if conn is not None:
                    await conn.close()

    async def _drain(self) -> None:
        pending = [*self._jobs.values(), *self._parts.values()]
        if pending:
            _, still = await asyncio.wait(pending, timeout=self.settings.job_drain_timeout_seconds)
            job_ids = [k for k, t in self._jobs.items() if t in still]
            part_ids = [k for k, t in self._parts.items() if t in still]
            for t in still:
                t.cancel()
            await asyncio.gather(*still, return_exceptions=True)
            await jobs.release(self.worker_id, job_ids=job_ids, partition_ids=part_ids)
        from dps.layers.connectors.jobs_run import RUNTIMES          # lazy: jobs_run imports this module
        await RUNTIMES.close_all()
        await jobs.unregister_worker(self.worker_id)
```
(`release` runs after the cancelled tasks finish so a handler that was mid-write cannot race its own release; a released partition resumes from its last checkpoint or ledger mark.)

`build_context(settings)`: `kek = load_kek(settings)` (missing → `RuntimeError("DPS_JOB_KEK is required for the worker")`), `PrivacyEngine(settings, gateway=get_gateway(settings))`, `ArtefactService(artefact_store(settings), acquire=db.acquire_queue)`, `DbAuditSink(db.acquire_queue, settings)`, `worker_id=jobs.worker_id()`. `serve_health` builds the three routes on a fresh `FastAPI()` and runs `uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=port, log_level="warning", lifespan="off")).serve()` as a task. `main()`:
```python
def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, json_output=settings.env != "local")
    assert_production_posture(settings)
    if not db.db_configured(settings):
        raise SystemExit("DPS_PG_HOST is not set: the worker needs the metadata database")
    asyncio.run(_amain(settings))


async def _amain(settings: Settings) -> None:
    await db.init_pool(settings)
    await db.init_worker_pool(settings)
    if settings.migrations_mode == "auto":
        conn = await db.open_migration_connection(settings)
        try:
            await db.run_migrations(settings, connection=conn)
        finally:
            await conn.close()
    elif settings.migrations_mode == "verify":
        await db.verify_migrations(settings)
    ctx = build_context(settings)
    ctx.audit.start()                                    # type: ignore[attr-defined]
    from dps.layers.connectors.discovery import discover_job
    from dps.layers.connectors.jobs_run import finalize, prepare, run_partition
    from dps.retention import retention_job
    consumer = Consumer(settings, kinds=(JobKind.discover, JobKind.run, JobKind.retention),
                        handlers={JobPhase.discover: discover_job, JobPhase.prepare: prepare,
                                  JobPhase.finalize: finalize, JobPhase.retention: retention_job},
                        partition_handler=run_partition, concurrency=settings.worker_concurrency, ctx=ctx)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, consumer.stop)
    health = asyncio.create_task(serve_health(settings, settings.worker_metrics_port, consumer)) \
        if settings.worker_metrics_port else None
    logger.info("worker_ready", worker_id=consumer.worker_id)
    await consumer.run()
    if health is not None:
        health.cancel()
    await ctx.audit.stop()                               # type: ignore[attr-defined]
    await db.close_pools()
```

- [ ] **Step 4: Write `dps/retention.py`** per the rules above (SQL as listed in the task text; tenants from `SELECT tenant_id FROM dps_tenant UNION SELECT DISTINCT tenant_id FROM dps_job` on the worker pool; one `acquire_queue(tenant)` block per tenant; a fenced `advance_job(state=running, progress={"stage": "<i>/<n> tenants"})` between tenants; finish with `advance_job(state=succeeded, progress={"outcome": "succeeded", "values_by_method": counters})`).

- [ ] **Step 5: Run tests, lint, types**

Run: `pytest -q tests/db/test_consumer.py tests/db/test_retention.py tests/db/test_worker_process.py && ruff check dps tests tools && mypy dps`
Expected: `2 + 1 + 1 = 4 passed`, clean.

- [ ] **Step 6: Commit**

```bash
git add dps/worker.py dps/retention.py tests/db/test_consumer.py tests/db/test_retention.py tests/db/test_worker_process.py tests/fixtures/db.py
git commit -m "feat(worker): python -m dps.worker - leased claims, heartbeats, reaper, LISTEN wake-ups, drain, retention" -- dps/worker.py dps/retention.py tests/db/test_consumer.py tests/db/test_retention.py tests/db/test_worker_process.py tests/fixtures/db.py
```

---

### Task 24: API foundation, connections routes, app wiring

**Files:**
- Create: `dps/layers/api/errors.py`, `dps/layers/api/deps.py`, `dps/layers/api/routers/connections.py`
- Modify: `dps/auth.py` (`require_any_scope`), `dps/app.py` (DB lifecycle, readiness, routers, error handler, CORS, nested SPA paths, embedded worker), `tests/fixtures/db.py` (`api_client`)
- Test: `tests/api/__init__.py`, `tests/api/test_api_connections.py`, `tests/test_app_m2.py`

Public interface:
```python
# dps/layers/api/errors.py
class DpsHttpError(Exception):
    def __init__(self, status: int, code: str, detail: str, extra: dict[str, Any] | None = None) -> None
def to_http(exc: Exception) -> DpsHttpError            # SpecError, SelectionError, ConnectionStoreError, JobStateError,
                                                       # QueueFull (429), KeyMaterialRequired (409), SnapshotStateError,
                                                       # ConnectorError (502, or 422 for engine_*), StorageError (404/502)
def install_error_handlers(app: FastAPI) -> None       # {"detail", "code", **extra}
# dps/layers/api/deps.py
def require_db(request: Request) -> None               # 503 db_unavailable while the metadata DB is down
def tenant_of(request: Request) -> str
def kek_of(request: Request) -> bytes | None
def idempotency_key(request: Request) -> str | None    # header Idempotency-Key, ^[A-Za-z0-9_.:-]{1,128}$ else 422
def audit_action(request: Request, *, action: str, outcome: str = "ok", status: int = 200,
                 job_id: str | None = None, counts: dict[str, int] | None = None) -> None
# dps/auth.py
def require_any_scope(*scopes: str) -> Callable[[Request], Principal]
# routes (contract §9, scope jobs; production-tag changes need admin)
POST   /api/v1/connections                    ConnectionSpec → 201 Connection
GET    /api/v1/connections?limit&cursor        → {"items": [Connection], "next_cursor"}
GET    /api/v1/connections/{id}                → Connection
PATCH  /api/v1/connections/{id}                ConnectionPatch → Connection
DELETE /api/v1/connections/{id}                → 204
POST   /api/v1/connections/{id}/test           {role, object?, write_mode?} → ConnectionTestResult (stored)
GET    /api/v1/connections/{id}/objects?owner= → {"items": [ObjectRef], "next_cursor": null}
GET    /api/v1/connections/{id}/describe?object=OWNER.NAME → TableShape
POST   /api/v1/connections/{id}/count          {object, selection} → {"rows", "checks", "runs_as", "binds"}
```
`count` validates and parameterises the selection first; a `SelectionError` answers 422 `{"detail", "code", "checks"}` (the wizard's checklist, mockup 06 step 2); `runs_as` is the value-free statement DPS will page with (`select_sql` over the **template**, e.g. `SELECT … FROM "bank"."customers" AS t WHERE ("created_at" >= CAST(CAST(:l1 AS TEXT) AS DATE)) ORDER BY t."customer_id"`); `binds` is the number of bound parameters. Credentials never appear in any response (`has_secret: bool` only).

**App wiring.** When `db_configured(settings)`: the lifespan initialises both pools (the API pool always; the worker pool only for the embedded worker), applies the migrations mode (`auto` → `run_migrations` on the migration connection; `verify` → `verify_migrations`, failure = not ready; `off` → nothing), runs `assert_rls_posture` (problems refuse boot in staging/prod, are logged as `rls_posture_problem` names locally), loads the KEK into `app.state.kek`, starts `DbAuditSink(db.acquire, settings)` unless a sink was injected, sets readiness `db` (`up|down`) and `jobs` (`ok` when db is up, migrations verified and the KEK loaded), and — only when `embedded_worker` (local) — starts a `Consumer` in the app's loop; at shutdown it stops the consumer (drain), stops the audit writer and closes both pools. A DB that is down at boot leaves `api-core` ready, sets `db=down`, and a background task retries every 10 s. Without a DB the M2 routers answer 503 `db_unavailable`. CORS `allow_methods` gains `PATCH`, `DELETE`; `allow_headers` gains `Idempotency-Key`. The SPA fallback resolves nested routes: for `/data-sources/jobs/<id>`, a request for `…/assets/<file>` or a root file (`favicon.svg`) is served from `frontend/dist` by trying each path suffix inside the dist root (the console is built with `base: './'`, so assets are requested relative to the route).

- [ ] **Step 1: Write the failing tests**

`tests/test_app_m2.py` (offline):
```python
from __future__ import annotations

from fastapi.testclient import TestClient

from dps.app import FRONTEND_DIST, create_app
from dps.config import Settings


def test_m2_routes_answer_503_without_a_database() -> None:
    with TestClient(create_app(Settings(_env_file=None, auth_enabled=False, llm_provider="stub"))) as c:
        r = c.get("/api/v1/jobs")
        assert r.status_code == 503 and r.json() == {"detail": "the metadata database is not configured",
                                                     "code": "db_unavailable"}
        assert c.get("/readyz").json()["ready"] is True                     # api-core never depends on the DB


def test_cors_allows_patch_delete_and_idempotency() -> None:
    with TestClient(create_app(Settings(_env_file=None, auth_enabled=False, llm_provider="stub"))) as c:
        r = c.options("/api/v1/connections/c_1", headers={"Origin": "http://x", "Access-Control-Request-Method": "PATCH",
                                                           "Access-Control-Request-Headers": "Idempotency-Key"})
        assert r.status_code == 200 and "PATCH" in r.headers["access-control-allow-methods"]


def test_nested_spa_routes_serve_relative_assets() -> None:
    assets = sorted((FRONTEND_DIST / "assets").glob("*.js"))
    if not assets:
        import pytest
        pytest.skip("frontend not built")
    with TestClient(create_app(Settings(_env_file=None, auth_enabled=False, llm_provider="stub"))) as c:
        r = c.get(f"/data-sources/jobs/assets/{assets[0].name}")
        assert r.status_code == 200 and "javascript" in r.headers["content-type"]
        assert c.get("/data-sources/jobs/j_1").headers["content-type"].startswith("text/html")
```

`tests/api/test_api_connections.py` (`db`):
```python
from __future__ import annotations

import pytest

pytestmark = pytest.mark.db
PG = {"host": "127.0.0.1", "port": 5442, "database": "bank"}
FULL = {"X-API-KEY": "dps_full", "X-DPS-Tenant": "t1"}
JOBS = {"X-API-KEY": "dps_jobs", "X-DPS-Tenant": "t1"}


def test_connection_crud_scopes_and_secrecy(api_client) -> None:  # noqa: ANN001
    c, sink = api_client
    body = {"name": "dev-pg", "engine": "postgres", "params": PG, "credentials": {"username": "u", "password": "pw-123"}}
    r = c.post("/api/v1/connections", json=body, headers=JOBS)
    assert r.status_code == 201 and r.json()["has_secret"] and "pw-123" not in r.text
    conn_id = r.json()["id"]
    assert c.post("/api/v1/connections", json=body, headers=JOBS).json()["code"] == "connection_name_taken"
    prod = {**body, "name": "prod-pg", "credentials": None, "secret_ref": "dps/src/x", "tags": ["production"]}
    assert c.post("/api/v1/connections", json=prod, headers=JOBS).status_code == 403
    assert c.post("/api/v1/connections", json=prod, headers=FULL).status_code == 201
    assert c.patch(f"/api/v1/connections/{conn_id}", json={"tags": ["production"]}, headers=JOBS).status_code == 403
    listed = c.get("/api/v1/connections?limit=1", headers=JOBS).json()
    assert len(listed["items"]) == 1 and listed["next_cursor"]
    assert c.get(f"/api/v1/connections/{conn_id}", headers={**JOBS, "X-DPS-Tenant": "t2"}).status_code == 404
    assert c.delete(f"/api/v1/connections/{conn_id}", headers=JOBS).status_code == 204
    assert [e.action for e in sink.events][:2] == ["connection.create", "connection.create"]


@pytest.mark.integration
@pytest.mark.pg_source
def test_test_describe_objects_and_count(api_client, source_pg_sync) -> None:  # noqa: ANN001
    c, _ = api_client
    conn_id = c.post("/api/v1/connections", json={"name": "src", "engine": "postgres", "params": PG,
                                                  "secret_ref": "dps/src/test-pg"}, headers=JOBS).json()["id"]
    obj = f"{source_pg_sync.schema}.customers"
    tested = c.post(f"/api/v1/connections/{conn_id}/test", json={"role": "source", "object": obj}, headers=JOBS).json()
    assert tested["ok"] and {x["name"] for x in tested["checks"]} >= {"connect", "select_source", "no_dml_on_source"}
    assert c.get(f"/api/v1/connections/{conn_id}", headers=JOBS).json()["last_test"]["ok"]
    objects = c.get(f"/api/v1/connections/{conn_id}/objects?owner={source_pg_sync.schema}", headers=JOBS).json()
    assert {"owner": source_pg_sync.schema, "name": "customers"} in objects["items"]
    shape = c.get(f"/api/v1/connections/{conn_id}/describe?object={obj}", headers=JOBS).json()
    assert shape["key"] == ["customer_id"]
    counted = c.post(f"/api/v1/connections/{conn_id}/count", headers=JOBS, json={
        "object": {"owner": source_pg_sync.schema, "name": "customers"},
        "selection": {"where": "country_code IN ('IN', 'US')"}}).json()
    assert counted["rows"] > 0 and counted["binds"] == 2 and "'IN'" not in counted["runs_as"]
    assert all(ch["ok"] for ch in counted["checks"])
    bad = c.post(f"/api/v1/connections/{conn_id}/count", headers=JOBS, json={
        "object": {"owner": source_pg_sync.schema, "name": "customers"},
        "selection": {"query": "SELECT * FROM bank.customers FOR UPDATE"}})
    assert bad.status_code == 422 and bad.json()["code"] == "selection_for_update"
    assert next(ch for ch in bad.json()["checks"] if ch["name"] == "no_into_lock_procs")["ok"] is False
```
`api_client` (in `tests/fixtures/db.py`): the session's `db_settings` with `auth_enabled=True`, `api_keys` = `[{"name": "full", "key": "dps_full", "tenant_ids": ["*"], "scopes": ["*"]}, {"name": "jobs", "key": "dps_jobs", "tenant_ids": ["t1", "t2"], "scopes": ["jobs", "detect"]}]`, `rate_limit_enabled=False`, `secret_dir`/`artefact_dir`/`export_root` under `tmp_path` (the secret dir gets the `source_secrets` files), a `MemoryAuditSink`; yields `(TestClient, sink)` inside `with TestClient(app)` (the app's lifespan owns the pools — do not combine with `migrated_db`), then truncates with `asyncio.run(_truncate(settings))`. `source_pg_sync` is a synchronous wrapper fixture that runs `SourcePg.customers_table(rows=200)` with `asyncio.run` and drops the schema afterwards.

- [ ] **Step 2: Run to confirm failure**, then **Step 3: write `errors.py`, `deps.py`, `require_any_scope`, the router and the app changes**

`to_http` mapping (the full table; every branch keeps the exception's value-free message):
```python
def to_http(exc: Exception) -> DpsHttpError:
    if isinstance(exc, DpsHttpError):
        return exc
    if isinstance(exc, SpecError):
        return DpsHttpError(exc.status, exc.code, exc.detail)
    if isinstance(exc, SelectionError):
        return DpsHttpError(422, exc.code, str(exc))
    if isinstance(exc, (ConnectionStoreError, SnapshotStateError, JobStateError)):
        return DpsHttpError(exc.status, exc.code, str(exc))
    if isinstance(exc, QueueFull):
        return DpsHttpError(429, "queue_full", "the tenant's job queue is full")
    if isinstance(exc, KeyMaterialRequired):
        return DpsHttpError(409, "key_material_required", "resubmit consistency.key_material (or synthesis_seed)")
    if isinstance(exc, SecretUnavailable):
        return DpsHttpError(409, "secret_unavailable", "the connection's credentials are unavailable or expired")
    if isinstance(exc, ConnectorError):
        status = 422 if exc.code.startswith("engine_") or exc.code == "object_not_found" else 502
        return DpsHttpError(status, exc.code, str(exc))
    if isinstance(exc, StorageError):
        return DpsHttpError(404 if exc.code == "storage_not_found" else 502, exc.code, str(exc))
    return DpsHttpError(500, "internal_error", "internal error")
```
Routers wrap their bodies in `try: … except Exception as exc: raise to_http(exc) from None` (one helper `call(fn)`); the app installs a `DpsHttpError` exception handler rendering `{"detail", "code", **extra}`.

- [ ] **Step 4: Run tests, lint, types**

Run: `pytest -q tests/test_app_m2.py tests/api/test_api_connections.py tests/test_api.py && ruff check dps tests tools && mypy dps`
Expected: `3 + 2 = 5` new tests pass (one may skip when `frontend/dist` is absent); M0's API tests stay green.

- [ ] **Step 5: Commit**

```bash
git add dps/layers/api/errors.py dps/layers/api/deps.py dps/layers/api/routers/connections.py dps/auth.py dps/app.py tests/fixtures/db.py tests/api/__init__.py tests/api/test_api_connections.py tests/test_app_m2.py
git commit -m "feat(api): connections routes with value-free errors; DB lifecycle, readiness and nested console routes" -- dps/layers/api/errors.py dps/layers/api/deps.py dps/layers/api/routers/connections.py dps/auth.py dps/app.py tests/fixtures/db.py tests/api/__init__.py tests/api/test_api_connections.py tests/test_app_m2.py
```

---

### Task 25: Jobs, snapshots, groups, evidence and artefact routes

**Files:**
- Create: `dps/layers/api/routers/{jobs,snapshots,groups,evidence,artefacts}.py`
- Modify: `dps/app.py` (include the routers)
- Test: `tests/api/test_api_jobs.py`, `tests/api/test_api_e2e.py` (`pg_source`, embedded worker)

Routes (contract §9; all `jobs` scope except where noted; mutating job routes honour `Idempotency-Key`):
```
POST /api/v1/jobs/discover          DiscoverSpec → 202 {"job": Job}
POST /api/v1/jobs/run               RunSpec → 202 {"job": Job, "key_material"?: {...}, "synthesis_seed"?: {...}}
                                    idempotent replay → 200 {"job": Job} + header Idempotent-Replay: true (no key again)
GET  /api/v1/jobs?kind=&state=&group_id=&limit=&cursor=   → {"items": [Job], "next_cursor"}
GET  /api/v1/jobs/stats             → JobStats
GET  /api/v1/jobs/{id}              → Job                         (404 job_not_found)
GET  /api/v1/jobs/{id}/partitions   → {"items": [PartitionView]}
POST /api/v1/jobs/{id}/cancel | /pause | /resume                  → Job (409 job_terminal | job_finalizing | job_not_pausable | job_not_paused)
POST /api/v1/jobs/{id}/retry        {"consistency"?: ConsistencySpec} → Job (409 key_material_required | key_material_mismatch | job_not_retryable)
GET  /api/v1/snapshots?connection_id=&object=&status=&limit=&cursor= → {"items": [ClassificationSnapshot], "next_cursor"}
GET  /api/v1/snapshots/{id}         → ClassificationSnapshot
PATCH /api/v1/snapshots/{id}        {"overrides": [SnapshotOverride]} → ClassificationSnapshot (409 snapshot_not_proposed)
POST /api/v1/snapshots/{id}/accept  → ClassificationSnapshot (accepted_by = principal name)
POST /api/v1/job-groups             {"name"} → 201 {"group": JobGroup, "key_material": {...}, "synthesis_seed": {...}} (once)
GET  /api/v1/job-groups             → {"items": [JobGroup], "next_cursor"}
GET  /api/v1/job-groups/{id}        → {"group": JobGroup, "jobs": [Job]}
GET  /api/v1/evidence?job_id=&limit=&cursor=   (jobs or scan) → {"items": [EvidenceSummary], "next_cursor"}
GET  /api/v1/evidence/{id}          (jobs or scan) → {"pack": EvidencePack, "pack_sha256", "artefacts": [{"id","kind","bytes","content_type"}]}
GET  /api/v1/evidence/{id}/html     (jobs or scan) → text/html; CSP default-src 'none'; style-src 'unsafe-inline'
GET  /api/v1/artefacts/{id}         (jobs or scan, by owning job kind) → streamed bytes; Content-Disposition: attachment; filename="<id>.<ext>"
```
Audit actions: `job.create` (counts `{"objects": n}` for discover), `job.cancel`, `job.pause`, `job.resume`, `job.retry`, `snapshot.update`, `snapshot.accept`, `group.create`, `artefact.read`. Scan-kind evidence/artefacts require `scan`, run/discover ones `jobs` (checked after loading the owning job: `require_any_scope("jobs", "scan")` on the route, then the kind check → 403 `scope_required`).

- [ ] **Step 1: Write the failing tests**

`tests/api/test_api_jobs.py` (`db`): seeds a snapshot (`factories.make_snapshot` accepted) and two connections through the API, then asserts:
- `POST /jobs/run` without `confirm_write` for `in_place` → 422 `confirm_write_required`; with confirm on a `production` connection using the `dps_jobs` key → 403 `admin_scope_required`;
- a valid `table` run → 202, `key_material.dek` present, `job.state == "queued"`, `job.spec.source.selection.params == {}` for a selection without literals and `job.spec` never contains `dek`;
- the same body with `Idempotency-Key: abc` twice → second answer 200, header `Idempotent-Replay: true`, same job id, no `key_material`;
- `GET /jobs`, `GET /jobs/{id}`, `GET /jobs/{id}/partitions` (empty), `GET /jobs/stats` (`workers_online == 0`, `tenant_max_running == 8`);
- `POST /jobs/{id}/pause` on a queued (prepare) job → 409 `job_not_pausable`; `cancel` → job `phase == "finalize"`, `progress.outcome == "canceled"`; second `cancel` → 409 `job_finalizing`;
- snapshots: `PATCH` on an accepted snapshot → 409 `snapshot_not_proposed`; on a proposed one → 200 with `override: true`; `accept` → `accepted_by == "jobs"`;
- groups: `POST /job-groups` → 201 with `key_material` and `synthesis_seed`; `GET /job-groups/{id}` has no key fields; a run with that `group_id` and a different key → 409 `group_key_mismatch`; with the group's key → 202;
- `GET /evidence/unknown` → 404 `evidence_not_found`; `GET /artefacts/unknown` → 404.

`tests/api/test_api_e2e.py` (`db`, `pg_source`, `integration`, `slow`): an app with `embedded_worker=True` and `DPS_WORKER_CONCURRENCY=2`; through HTTP only: create the source and UAT connections, `count` the selection, `POST /jobs/discover`, poll `GET /jobs/{id}` until `succeeded`, `GET /snapshots/{snap}`, `POST …/accept`, `POST /jobs/run` (table mode into `<schema>.customers_uat`), poll until terminal (≤ 120 s), then `GET /evidence?job_id=` → one pack with `passed == true`, `GET /evidence/{id}` → `pack_sha256` equals `sha256(canonical json)` recomputed client-side, `GET /evidence/{id}/html` → 200 `text/html` containing the sha, `GET /artefacts/{json_artefact_id}` → bytes whose sha256 equals `pack_sha256`. Asserts no response body anywhere in the flow contains one of the seeded customer names (read from source-pg).

- [ ] **Step 2: Run to confirm failure**, **Step 3: write the routers** (thin: parse → `submit_*`/repository call → `to_http` mapping → audit → response models as listed). The run route:
```python
@router.post("/jobs/run", status_code=202, dependencies=[Depends(require_scope("jobs")), Depends(enforce_rate_limit),
                                                         Depends(require_db)])
async def run(body: RunSpec, request: Request, response: Response) -> dict[str, Any]:
    principal: Principal = request.state.principal
    try:
        sub = await submit_run(body, tenant_id=tenant_of(request), principal=principal,
                               settings=request.app.state.settings, kek=kek_of(request),
                               idempotency_key=idempotency_key(request))
    except Exception as exc:  # noqa: BLE001 - mapped to a value-free {"detail", "code"}
        err = to_http(exc)
        audit_action(request, action="job.create", outcome="rejected", status=err.status)
        raise err from None
    audit_action(request, action="job.create", status=200 if sub.replay else 202, job_id=sub.job.id)
    if sub.replay:
        response.status_code = 200
        response.headers["Idempotent-Replay"] = "true"
        return {"job": sub.job.model_dump(mode="json")}
    out: dict[str, Any] = {"job": sub.job.model_dump(mode="json")}
    if sub.key_material:
        out["key_material"] = sub.key_material
    if sub.synthesis_seed:
        out["synthesis_seed"] = sub.synthesis_seed
    response.headers["Cache-Control"] = "no-store"          # the key is in this body: never cached
    return out
```
The artefact route streams `ArtefactService.open` with `StreamingResponse(chunks, media_type=row.content_type, headers={"Content-Disposition": f'attachment; filename="{row.id}.{ARTEFACT_EXT.get(row.kind, "bin")}"', "Cache-Control": "no-store"})`.

- [ ] **Step 4: Run tests, lint, types**

Run: `pytest -q tests/api && ruff check dps tests tools && mypy dps`
Expected: `tests/api/test_api_jobs.py` (8 tests) and `tests/api/test_api_e2e.py` (1) pass with the services up; clean.

- [ ] **Step 5: Commit**

```bash
git add dps/layers/api/routers/jobs.py dps/layers/api/routers/snapshots.py dps/layers/api/routers/groups.py dps/layers/api/routers/evidence.py dps/layers/api/routers/artefacts.py dps/app.py tests/api/test_api_jobs.py tests/api/test_api_e2e.py
git commit -m "feat(api): jobs, snapshots, job groups, evidence and artefact routes with once-only key return" -- dps/layers/api/routers/jobs.py dps/layers/api/routers/snapshots.py dps/layers/api/routers/groups.py dps/layers/api/routers/evidence.py dps/layers/api/routers/artefacts.py dps/app.py tests/api/test_api_jobs.py tests/api/test_api_e2e.py
```

---

### Task 26: Connector conformance suite and resume-after-kill tests

**Files:**
- Create: `tests/connectors/conformance.py`, `tests/connectors/test_conformance_postgres.py`, `tests/connectors/test_resume_after_kill.py`
- Test: the two test modules above

**Conformance (contract §12.3).** `conformance.py` is an engine-agnostic mix-in; M3 binds it to Oracle and MongoDB **unchanged**. An engine binding provides one fixture, `env`, implementing:
```python
class ConformanceEnv(Protocol):
    engine: str
    source: Connector                              # opened, read-only login
    rw: Connector                                  # opened, read-write login (same database)
    async def make_table(self, name: str, *, key: Literal["int", "composite", "text"], rows: int) -> ObjectRef
    async def read_all(self, obj: ObjectRef) -> list[Row]
    def target(self, obj: ObjectRef, shape: TableShape, write_mode: WriteMode, **kw: Any) -> ResolvedTarget
```
`class ConnectorConformance` (tests are methods; `pytestmark` is set by the binding):
- `test_describe_finds_the_key` — int → `["id"]` `primary_key`; composite → two columns; a table without a key → `key_source == "none"`.
- `test_keyset_paging_visits_each_row_once[int|composite|text]` — `read_partition` over the whole range with `batch=7` equals `read_all` ordered by the database (`sorted` is never used for text keys; the binding's `read_all` orders server-side).
- `test_partitions_cover_every_row_exactly_once` — `sample_keys(n=40)` → `plan_partitions(count=5)` → read each range → union = all keys, no key twice.
- `test_selection_rejections` — `FOR UPDATE`, two statements, a denied function, an unknown column → `SelectionError` with the expected codes, before any query runs.
- `test_table_reload_is_idempotent` — writing the same two batches twice leaves exactly the rows once.
- `test_in_place_crash_between_write_and_checkpoint_never_double_applies` — transform batch 1 with a `BatchTransformer` (`det_token` on a text column), `write_batch(mark=1)`, then **discard the writer** (the "crash": the partition row's checkpoint is still 0); a fresh writer's `read_ledger` returns batch 1, the resume continues after its `last_key`, writes batch 2; the final rows equal "transform once" for every row (no value is a token of a token).
- `test_append_offset_and_precheck` — `precheck_collisions` counts exactly the clashing keys; appended rows carry `key + offset`.
- `test_no_values_in_logs` — structlog configured to a buffer (`configure_logging` + `capsys`), run describe/read/write: no row value appears in the captured output.

`test_conformance_postgres.py` binds it with `source_pg` (read-only `dps_reader`, read-write `dps_writer`): `class TestPostgresConformance(ConnectorConformance)` + `pytestmark = [pytest.mark.integration, pytest.mark.pg_source]`.

**Resume after kill (M2 exit criterion; contract §12.3).** `test_resume_after_kill.py` (`db`, `pg_source`, `integration`, `slow`; `@pytest.mark.timeout(300)`), parametrised over the write modes:

| id | target | source table | key strategy |
|---|---|---|---|
| `table_truncate_insert` | `table` + `truncate_insert` into `<schema>.<id>_out` | 6 000 customers | — |
| `table_upsert` | `table` + `upsert` (target pre-loaded with 100 unrelated rows) | 6 000 | — |
| `in_place` | `in_place`, `confirm_write` | 6 000 (two identical copies, one per run) | — |
| `append_offset_named` | `append` into `<schema>.<id>_out` (pre-created, same DDL) | 6 000 | `offset 90_000_000` |
| `append_generate_same` | `append` to the same table (identity key) | 6 000 | `generate` |
| `files_jsonl_pvc` | `files` (`jsonl`, no compression) to a `pvc` connection | 6 000 | — |

Each case runs the job **twice from identical inputs with the same key material / synthesis seed** (passed as `consistency`): a *reference* run with one uninterrupted `python -m dps.worker` subprocess, and a *kill* run: start a worker subprocess (env from `worker_env`, `DPS_BATCH_ROWS=200`, parallelism 2, `partition_rows=1000` → 6 partitions × 5 batches), poll the metadata DB every 50 ms until `sum(batch_no) >= 5` for the job, `SIGKILL` the worker, start a **second** worker, and wait (≤ 180 s) for a terminal state. Assertions:
```python
assert kill_job.state == JobState.succeeded
assert any(p.resumed >= 1 or p.attempts >= 2 for p in await jobs.list_partitions(TENANT, kill_job.id))
assert await target_digest(case, "kill") == await target_digest(case, "ref")          # row-for-row
if case.write_mode in ("in_place", "append"):
    assert await ledger_rows(case, kill_job.id) == 0                                    # cleared at finalize
assert (await get_evidence(TENANT, kill_job.progress.evidence_id)).pack.counts["resumed_partitions"] >= 1
```
`target_digest`: for tables, `SELECT md5(string_agg(r::text, '|' ORDER BY r::text)) FROM (SELECT <columns> FROM <table>) r`, excluding the generated key column for `append_generate_same` (identity values differ between runs) and restricted to the appended rows (`WHERE <key> >= <first generated value>`) / the transformed copy; for `files`, the md5 of the sorted JSONL lines of every object listed in the manifest. The kill worker and the second worker are separate processes, so a pass proves the fencing, the reaper, the ledger and the per-mode resume rules end to end.

- [ ] **Step 1: Write the suite and both bindings** (the code is the specification above; helper functions `target_digest`, `ledger_rows`, `start_worker(env)`, `wait_for_batches(job_id, n)` and `wait_terminal(job_id, timeout)` live at the top of `test_resume_after_kill.py`).

- [ ] **Step 2: Run them**

Run: `pytest -q tests/connectors/test_conformance_postgres.py && pytest -q -m slow tests/connectors/test_resume_after_kill.py`
Expected: conformance `10 passed` (`test_keyset_paging…` ×3); resume `6 passed` in about 2–4 minutes. A failing digest with `in_place` means a batch was applied twice — check that `write_batch` compares the ledger row with the batch the writer last saw **inside** the transaction and that `run_partition` resumes from the ledger mark.

- [ ] **Step 3: Commit**

```bash
git add tests/connectors/conformance.py tests/connectors/test_conformance_postgres.py tests/connectors/test_resume_after_kill.py
git commit -m "test(connectors): engine-agnostic conformance suite and resume-after-kill for every write mode" -- tests/connectors/conformance.py tests/connectors/test_conformance_postgres.py tests/connectors/test_resume_after_kill.py
```

---

### Task 27: Demo data seeding, compose stack, image, smoke extension

**Files:**
- Create: `tools/seed_sources.py`, `tools/__init__.py` (empty: tests import `tools.seed_sources`), `docker/compose.sources.env`
- Modify: `docker-compose.yml` (`migrate`, `worker`, `demo-secrets`, `seed-sources`, app DB env, shared volumes), `Dockerfile`, `docker/entrypoint.sh`, `.dockerignore` (keep `tools/` in the build context), `tools/smoke_test.py`, `HANDOFF.md` (run commands, ports, services)
- Test: `tests/test_seed_sources.py` (offline generation), `tests/connectors/test_seed_sources_pg.py` (`pg_source`)

**`tools/seed_sources.py`** (contract §11 "Demo data"):
```
python tools/seed_sources.py --target postgres [--dsn postgresql://postgres:postgres@127.0.0.1:5442/bank]
                             [--schema bank] [--rows 20000] [--seed 7] [--restore-copy] [--reset]
```
- `--target oracle|mongo` exits 2 with "arrives with M3" (M3 adds them).
- Idempotent: when `bank._dps_seed_info` already holds `(seed, rows)`, print "already seeded" and exit 0; `--reset` truncates the ten tables (`CASCADE`) and the marker first.
- Every PII value is produced by the M0 surrogate engine (`SurrogateEngine(sha256(b"dps-demo-%d" % seed).digest(), …)`): originals are synthetic shape templates with the row number embedded (for example `f"{i % 1000:03d}-{i % 100:02d}-{i % 10000:04d}"` for an SSN, `f"Person {i}"` for a name), passed with `anchor=f"CUSTOMER_ID:{hash_value(str(customer_id))}"` so a customer's name, e-mail, DOB and address agree; non-PII columns come from `random.Random(seed)`. The surrogate engine's rule-valid fakes (test-BIN Luhn-valid PANs, never-issued SSNs, Verhoeff-valid Aadhaar-shaped numbers, RFC 2606 e-mail domains, fictional phone ranges) mean **no real PII ever exists in the demo source**.
- Row counts for `--rows N` customers: employees `max(50, N // 40)`, addresses `≈1.1 N`, contacts `2 N`, accounts `1.5 N`, cards `≈1.2 N`, transactions `10 N`, loans `N // 4`, loan applicants `≈1.4 × loans`, KYC documents `N`. Country mix IN 60 % / US 20 % / GB 10 % / MX 10 % (locale per row: `en_IN`, `en_US`, `en_GB`, `es_MX`); `national_id`/`id_type` by country (AADHAAR, SSN, NINO, CURP); `tax_id` (a PAN-shaped `char(10)`) for IN rows only; `notes` on 30 % of customers is free text embedding a surrogate e-mail or phone ("Prefers SMS on … ; alt email …") so discovery finds a free-text column; ids are 8–12 digits (`customer_id = 10_000_001 + i`, `account_id = 100_000_001 + j`, `txn_id = 100_000_000_001 + k`, `card_id = 30_000_001 + n`, `loan_id = 50_000_001 + m`), so `fpe_digits` (domain ≥ 10⁶) applies to every key. Loads use `copy_records_to_table`, children after parents.
- `--restore-copy`: `bank_restore.<table>` = `CREATE TABLE … (LIKE bank.<table> INCLUDING ALL)` + `INSERT … SELECT` for the ten tables (owner `dps_writer`) — the "restored production backup" for in-place demos; `bank_perf.transactions` gets 1 000 rows.
- Prints per-table row counts and the elapsed time; never a value.

**Compose** (contract §11; host ports on 127.0.0.1; every service has a healthcheck or is a one-shot):
```yaml
x-dps-env: &dps-env
  DPS_ENV: local
  DPS_LOG_LEVEL: INFO
  DPS_PG_HOST: dps-db
  DPS_PG_PORT: "5432"
  DPS_PG_DATABASE: dps
  DPS_PG_USER: dps_app                      # local-only demo literals (contract §2.3)
  DPS_PG_PASSWORD: dps_app
  DPS_PG_WORKER_USER: dps_worker_login
  DPS_PG_WORKER_PASSWORD: dps_worker_login
  DPS_PG_MIGRATION_USER: dps_owner
  DPS_PG_MIGRATION_PASSWORD: dps_owner
  DPS_MIGRATIONS_MODE: verify                # the migrate one-shot applies; app and worker verify
  DPS_JOB_KEK: ${DPS_JOB_KEK:-ZHBzLWxvY2FsLWRlbW8ta2VrLTMyLWJ5dGVzLW9ubHk=}   # demo literal, local only
  DPS_SECRET_DIR: /var/run/secrets/dps
  DPS_ARTEFACT_STORE: ${DPS_ARTEFACT_STORE:-local}
  DPS_ARTEFACT_DIR: /app/var/artefacts
  DPS_S3_ENDPOINT_URL: ${DPS_S3_ENDPOINT_URL:-}
  DPS_S3_ACCESS_KEY_ID: ${DPS_S3_ACCESS_KEY_ID:-}
  DPS_S3_SECRET_ACCESS_KEY: ${DPS_S3_SECRET_ACCESS_KEY:-}
  DPS_EXPORT_ROOT: /app/var/exports
  DPS_FPE_ENABLED: "true"
  DPS_NER_ENABLED: "false"
  DPS_LLM_PROVIDER: ${DPS_LLM_PROVIDER:-stub}
  DPS_DEFAULT_LOCALE: ${DPS_DEFAULT_LOCALE:-en_IN}
x-dps-volumes: &dps-volumes
  - dps-artefacts:/app/var/artefacts
  - dps-exports:/app/var/exports
  - dps-secrets:/var/run/secrets/dps:ro
```
`app`: `environment: {<<: [*dps-env], DPS_AUTH_ENABLED: "true", DPS_BOOTSTRAP_API_KEY: ${DPS_BOOTSTRAP_API_KEY:-dps_local_dev_key_change_me}, …M0 keys}`, `volumes: *dps-volumes`, `depends_on: {dps-db: {condition: service_healthy}, migrate: {condition: service_completed_successfully}}`, `mem_limit: 1g`.
`migrate`: image `data-privacy-services:local`, `command: ["python", "-m", "dps.migrate"]`, `environment: {<<: [*dps-env], DPS_MIGRATIONS_MODE: auto}`, `depends_on: {dps-db: {condition: service_healthy}}`, `restart: "no"`.
`worker`: image `data-privacy-services:local`, `command: ["python", "-m", "dps.worker"]`, `environment: {<<: [*dps-env], DPS_WORKER_CONCURRENCY: "4", DPS_WORKER_METRICS_PORT: "9400"}`, `volumes: *dps-volumes`, same `depends_on`, `healthcheck: ["CMD", "python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:9400/health', timeout=3).status==200 else 1)"]`, `mem_limit: 1g`, scalable (`docker compose up -d --scale worker=3`).
`demo-secrets` (profiles `sources`, `full`; one-shot): image `data-privacy-services:local`, `user: "0:0"`, writes demo secret files into the `dps-secrets` volume (demo literals; the app and worker mount it read-only):
```yaml
    entrypoint: ["/bin/sh", "-c"]
    command: ["set -e; d=/var/run/secrets/dps/dps;
      w() { mkdir -p \"$$d/$$1\"; printf %s \"$$2\" > \"$$d/$$1/username\"; printf %s \"$$3\" > \"$$d/$$1/password\"; };
      w src/bank-pg-ro dps_reader dps_reader; w tgt/bank-uat dps_writer dps_writer; w src/bank-restore-rw dps_writer dps_writer;
      mkdir -p \"$$d/s3/exports\"; printf minioadmin > \"$$d/s3/exports/access_key_id\"; printf minioadmin > \"$$d/s3/exports/secret_access_key\";
      chmod -R a+rX /var/run/secrets/dps"]
    volumes: ["dps-secrets:/var/run/secrets/dps"]
```
(`$$` is compose's escape for a literal `$`; without it compose would interpolate `$d`, `$1` … from the host environment.)
`seed-sources` (profiles `sources`, `full`; one-shot): image `data-privacy-services:local`, `command: ["python", "tools/seed_sources.py", "--target", "postgres", "--dsn", "postgresql://postgres:postgres@source-pg:5432/bank", "--rows", "20000", "--seed", "7", "--restore-copy"]`, `depends_on: {source-pg: {condition: service_healthy}}`.
Volumes: `dps-artefacts`, `dps-exports`, `dps-secrets` (+ the data volumes of earlier tasks).

`docker/compose.sources.env` (use with `--env-file`): `DPS_ARTEFACT_STORE=s3`, `DPS_S3_ENDPOINT_URL=http://minio:9000`, `DPS_S3_ACCESS_KEY_ID=minioadmin`, `DPS_S3_SECRET_ACCESS_KEY=minioadmin`, `DPS_BOOTSTRAP_API_KEY=dps_local_dev_key_change_me` (contract §11: S3 artefacts when the sources profile is on). The full local stack:
```bash
docker compose --env-file docker/compose.sources.env --profile sources up -d --build
```

**Image.** `Dockerfile`: the console stage becomes `FROM node:22-slim AS frontend` (the cached image); the runtime installs `uv pip install --system -e ".[fpe,docs,db,connectors,tabular]"` (contract §11.1 — `docs`/`tabular` are created by M1/M5; until then uv prints "does not have an extra named …" and continues); `COPY tools ./tools`; `RUN mkdir -p /app/var/artefacts /app/var/exports && chown -R appuser /app/var` before `USER appuser` (named volumes inherit that ownership). `docker/entrypoint.sh`:
```sh
#!/bin/sh
# App containers run pending migrations first when DPS_MIGRATIONS_MODE=auto (single-container runs);
# workers and the compose stack rely on the `migrate` one-shot instead (contract §11.1).
set -e
if [ "${DPS_MIGRATIONS_MODE:-auto}" = "auto" ] && [ -n "${DPS_PG_HOST:-}" ] && [ "$1" = "uvicorn" ]; then
  python -m dps.migrate
fi
exec "$@"
```

**Smoke extension** (`tools/smoke_test.py`; runs after the M0 checks when `/readyz` reports `db` ok; every check prints `PASS/FAIL name — counts`, never a body):
1. `readyz` components `db` and `jobs` ok; 2. create or reuse connections `bank-pg-ro` (`secret_ref dps/src/bank-pg-ro`, tag `production`), `bank-uat` (`dps/tgt/bank-uat`, role target), `bank-restore-rw` (`dps/src/bank-restore-rw`, roles source+target), `exports-s3` (engine `s3`, bucket `dps-exports`, endpoint `http://minio:9000`, `dps/s3/exports`, role target); 3. `test` each → ok; 4. `count` `bank.customers WHERE country_code = 'IN'` → rows > 0 and `binds == 1`; 5. discover `bank.customers` → poll ≤ 60 s → accept; 6. run `table` obfuscate into `bank_uat.customers_smoke` (`truncate_insert`, rerunnable) → poll ≤ 180 s → `succeeded`, evidence `passed`, `GET /evidence/{id}/html` 200; 7. run `files` parquet to `exports-s3` → `succeeded`, manifest artefact present; 8. dry-run `in_place` on `bank_restore.contacts` (`confirm_write`, admin key) → outcome `dry_run`; 9. `GET /jobs/stats` → `workers_online ≥ 1`.

- [ ] **Step 1: Write the failing tests**

`tests/test_seed_sources.py` (offline): `generate_rows(rows=50, seed=7)` (the pure generator inside `tools/seed_sources.py`, importable as `tools.seed_sources`) returns the ten tables; the same seed gives identical rows and another seed different ones; every `customers.national_id` matches its `id_type`'s format and US SSNs have area 900–999/666/000 (never issued); every `email` ends with an RFC 2606 domain or the surrogate engine's reserved domain set; every `cards.pan` passes Luhn and starts with a test IIN; foreign keys line up (every `accounts.customer_id` exists in customers, every `cards.account_id` in accounts); `customer_id` values have 8 digits.

`tests/connectors/test_seed_sources_pg.py` (`pg_source`): `seed(dsn, rows=200, seed=7, schema=<test schema>)` loads all ten tables into a fresh schema (the tool takes `--schema`, default `bank`, so tests never touch the demo data); a second call prints "already seeded" and changes nothing; `--reset` reloads; row counts match the ratios above.

- [ ] **Step 2: Run to confirm failure**, **Step 3: implement** `tools/seed_sources.py` (`generate_rows` pure; `seed()` async loader with `copy_records_to_table`; `main()` CLI), the compose/Dockerfile/entrypoint edits and the smoke extension.

- [ ] **Step 4: Run the tests and bring the stack up**

Run: `pytest -q tests/test_seed_sources.py tests/connectors/test_seed_sources_pg.py`
Expected: `5 + 3 = 8 passed`.
Run: `docker compose --env-file docker/compose.sources.env --profile sources up -d --build`, then poll `docker compose ps` until `dps-db`, `source-pg`, `minio`, `app`, `worker` are `healthy` and `migrate`, `minio-init`, `demo-secrets`, `seed-sources` exited 0 (never use `--wait`: it fails on exited one-shots — the Wega measurement), then `DPS_API_KEY=dps_local_dev_key_change_me python tools/smoke_test.py`.
Expected: every M0 check plus the nine M2 checks `PASS`; final line `all 22 checks passed against http://localhost:8400`.

- [ ] **Step 5: Commit**

```bash
git add tools/seed_sources.py tools/__init__.py docker/compose.sources.env docker-compose.yml Dockerfile docker/entrypoint.sh .dockerignore tools/smoke_test.py HANDOFF.md tests/test_seed_sources.py tests/connectors/test_seed_sources_pg.py
git commit -m "feat: seeded 10-table bank source, metadata DB/migrate/worker/MinIO stack and M2 smoke checks" -- tools/seed_sources.py tools/__init__.py docker/compose.sources.env docker-compose.yml Dockerfile docker/entrypoint.sh .dockerignore tools/smoke_test.py HANDOFF.md tests/test_seed_sources.py tests/connectors/test_seed_sources_pg.py
```

---

### Task 28: Benchmark (`tools/bench_connector.py`), the 10-table demo (`tools/demo_m2.py`) and `tools/reveal_rows.py`

**Files:**
- Create: `tools/bench_connector.py`, `tools/demo_m2.py`, `tools/reveal_rows.py`, `docs/technical/bench/.gitkeep`
- Test: `tests/test_tools_m2.py` (offline argument parsing and result formatting)

**`tools/bench_connector.py`** — the measured rows/s the spec asks for (§7 "Throughput", §11 M2 exit criterion):
```
python tools/bench_connector.py --rows 200000 [--mode obfuscate|synthesize] [--write-mode table|in_place|files]
       [--parallelism 4] [--batch 1000] [--partition-rows 50000] [--base-url http://127.0.0.1:8400]
       [--api-key …] [--source-dsn postgresql://postgres:postgres@127.0.0.1:5442/bank] [--label 1-worker]
```
It (1) creates `bench.customers_bench` in the source (customers DDL; rows generated by recycling a pool of 5 000 surrogate-engine values — throughput does not depend on distinct values) unless it already holds `--rows` rows, (2) registers or reuses the `bank-pg-ro`/`bank-uat`/`exports-s3`/`bank-restore-rw` connections, (3) discovers and accepts, (4) runs the job and polls `GET /jobs/{id}` every second, (5) reads `GET /jobs/{id}/partitions` and `GET /jobs/stats`, and (6) prints one line and writes `docs/technical/bench/m2-<date>-<mode>-<write_mode>-<label>.json`:
```json
{"rows": 200000, "mode": "obfuscate", "write_mode": "table", "partitions": 4, "parallelism": 4, "batch_rows": 1000,
 "workers_online": 1, "wall_seconds": 0.0, "rows_per_second": 0.0, "partition_rows_per_second": {"p50": 0.0, "p95": 0.0},
 "values_by_method": {}, "engine_version": "…", "fpe_enabled": true, "host": {"cpus": 12}}
```
(the numbers are filled by the run; nothing else is printed). In-place benchmarks run against `bench_restore.customers_bench`, a copy the tool recreates before each in-place run.

**`tools/demo_m2.py`** — the milestone's demoable outcome (spec §11 M2): "mask a 10-table Postgres schema into a UAT copy, then mask one table in place and append to another, all with evidence":
1. `POST /job-groups {"name": "bank-uat-refresh"}`; the returned key and seed are written to `./var/demo/bank-uat-refresh.json` (git-ignored, local only) and only the `kid` is printed;
2. for each of the ten `bank` tables: discover → accept → run `table` (obfuscate, `truncate_insert`) into `bank_uat.<table>` with `group_id` and the group's key material; tables run with `parallelism 4`, two jobs at a time;
3. referential check through the source DSN (read-only `SELECT count(*)`s): every `bank_uat.accounts.customer_id` exists in `bank_uat.customers`, every `cards.account_id` in `accounts`, every `transactions.account_id` in `accounts`, every `loan_applicants.loan_id` in `loans` → all orphan counts 0 (keys of one entity type under one key tokenise identically in every table);
4. in place: `bank_restore.contacts` (`in_place`, `confirm_write`, `expected_rows` from `count`, admin key);
5. append: `bank.transactions` → `bank_perf.transactions` (`synthesize`, `append` to a named table, `pk_strategy: generate`);
6. prints a table `step · job · rows · evidence id · verification` and exits non-zero on any failed job.

**`tools/reveal_rows.py`** — run **by the key holder, locally** (contract §6.2; DPS never stores the key):
```
python tools/reveal_rows.py --evidence pack.json --snapshot snapshot.json --key key.json --rows rows.jsonl > revealed.jsonl
```
Reads the evidence pack's `reveal_map`, the snapshot's shape and anchor, the key material and JSONL rows (as exported or selected by the operator), and writes the revealed rows with `dps.engine.batch.reveal_row`. It makes no network call.

- [ ] **Step 1: Write the failing tests** `tests/test_tools_m2.py`: `bench_connector.parse_args([...])` defaults (`mode=obfuscate`, `write_mode=table`, `parallelism=4`); `bench_connector.summarise(job, partitions, started, finished)` computes `rows_per_second = rows_written / (finished − started)` and p50/p95 of partition rates on a fixed fixture; `demo_m2.TABLES` is exactly the contract's ten table names in parent-before-child order; `reveal_rows.main([...])` on files written to `tmp_path` (a row obfuscated in the test with `BatchTransformer`) prints the original row.

- [ ] **Step 2: Run to confirm failure**, **Step 3: implement**, **Step 4: run them**

Run: `pytest -q tests/test_tools_m2.py && ruff check dps tests tools && mypy dps`
Expected: `4 passed`, clean.

- [ ] **Step 5: Measure and record** (stack from Task 27 running)

Run, in order:
```bash
python tools/bench_connector.py --rows 200000 --write-mode table --label 1-worker
python tools/bench_connector.py --rows 200000 --write-mode in_place --label 1-worker
python tools/bench_connector.py --rows 200000 --write-mode files --label 1-worker
python tools/bench_connector.py --rows 200000 --write-mode table --mode synthesize --label 1-worker
docker compose up -d --scale worker=3 worker && python tools/bench_connector.py --rows 200000 --write-mode table --label 3-workers
python tools/demo_m2.py
```
Expected: five JSON result files under `docs/technical/bench/`, each with `rows_per_second > 0`; `demo_m2.py` ends with ten table jobs plus the in-place and append jobs `succeeded`, verification `passed`, and every orphan count 0. Copy the five `rows_per_second` numbers (and the host: 12 CPUs, Docker with ~15 GiB) into the HANDOFF "Measured throughput" table — the number is measured, not promised (spec §7).

- [ ] **Step 6: Commit**

```bash
git add tools/bench_connector.py tools/demo_m2.py tools/reveal_rows.py docs/technical/bench tests/test_tools_m2.py HANDOFF.md
git commit -m "feat(tools): connector benchmark with recorded rows/s, 10-table demo, local reveal tool" -- tools/bench_connector.py tools/demo_m2.py tools/reveal_rows.py docs/technical/bench tests/test_tools_m2.py HANDOFF.md
```

---

### Task 29: Console foundation — types, API groups, helpers, Vitest, shared components

**Files:**
- Modify: `frontend/package.json` (Vitest, `test` script), `frontend/vite.config.ts` (test config), `frontend/src/lib/api.ts`, `frontend/src/lib/types.ts`, `frontend/src/main.tsx` (import the page stylesheets)
- Create: `frontend/src/lib/jobs.ts`, `frontend/src/lib/jobs.test.ts`, `frontend/src/hooks/usePoll.ts`, `frontend/src/components/{StateChip,Sparkline,CheckList,HeatMap,ConfirmWriteModal,KeyMaterialCard,EngineBadge}.tsx`, `frontend/src/styles/{jobs,wizard,job-detail,evidence}.css`

Contract §14: API calls only through `lib/api.ts` (typed groups), types in `lib/types.ts` mirror the pydantic models field for field (snake_case), key material and seeds live only in React state, the wizard draft in `localStorage` holds no credentials, key material or selection binds, and job pages poll every 2 s while non-terminal.

- [ ] **Step 1: Tooling** — `npm install --save-dev vitest@^2.1.9` (Node 22); `package.json` scripts gain `"test": "vitest run"`; `vite.config.ts` gains `/// <reference types="vitest/config" />` at the top and `test: { environment: 'node', include: ['src/**/*.test.ts'] }` inside `defineConfig`.

- [ ] **Step 2: Types** — append to `frontend/src/lib/types.ts` (one interface per model; `| null` where the model is optional):
```ts
export type JobKind = 'discover' | 'run' | 'scan' | 'retention';
export type JobState = 'queued' | 'running' | 'waiting' | 'paused' | 'succeeded' | 'failed' | 'dead' | 'canceled';
export type JobPhase = 'discover' | 'prepare' | 'partitions' | 'finalize' | 'scan' | 'retention';
export type PartitionState = 'queued' | 'running' | 'done' | 'failed' | 'dead' | 'canceled';
export type WriteMode = 'in_place' | 'append' | 'table' | 'files';
export type ConnEngine = 'postgres' | 'oracle' | 'mongo' | 's3' | 'pvc';
export interface Page<T> { items: T[]; next_cursor: string | null }
export interface ObjectRef { owner: string | null; name: string }
export interface ColumnInfo { path: string; name: string; data_type: string; kind: string; length: number | null; precision: number | null; scale: number | null; ts_precision: number | null; nullable: boolean; is_key: boolean; has_default: boolean; is_identity: boolean }
export interface TableShape { object: ObjectRef; columns: ColumnInfo[]; key: string[]; key_source: 'primary_key' | 'unique_index' | 'rowid' | 'none'; row_estimate: number | null }
export interface Check { name: string; ok: boolean; severity?: 'required' | 'advisory'; detail?: string; code?: string; count?: number }
export interface ConnectionTestResult { ok: boolean; engine: ConnEngine; server_version: string | null; checks: Check[]; capabilities: Record<string, boolean>; error_code: string | null }
export interface ConnectionParams { host?: string | null; port?: number | null; database?: string | null; service_name?: string | null; options?: Record<string, string>; bucket?: string | null; prefix?: string; region?: string | null; endpoint_url?: string | null; base_path?: string | null }
export interface Connection { id: string; tenant_id: string; name: string; engine: ConnEngine; params: ConnectionParams; secret_ref: string | null; has_secret: boolean; secret_expires_at: string | null; roles: ('source' | 'target')[]; tags: string[]; capabilities: Record<string, boolean>; last_test: ConnectionTestResult | null; created_by: string | null; created_at: string; updated_at: string }
export interface ConnectionSpec { name: string; engine: ConnEngine; params: ConnectionParams; secret_ref?: string | null; credentials?: { username: string; password: string } | null; roles: ('source' | 'target')[]; tags: string[] }
export interface Selection { where?: string | null; params?: Record<string, string | number | boolean | null>; query?: string | null; columns?: string[] | null; max_rows?: number | null; sample?: { percent: number; seed?: number | null } | null }
export interface CountOut { rows: number; checks: Check[]; runs_as: string; binds: number }
export interface JobProgress { outcome: '' | 'succeeded' | 'failed' | 'canceled' | 'dry_run'; stage: string; rows_estimated: number | null; rows_read: number; rows_written: number; rows_skipped: number; partitions_total: number; partitions_done: number; partitions_failed: number; rows_per_second: number | null; throughput: [string, number][]; values_by_method: Record<string, number>; verification: Record<string, number>; snapshot_ids: string[]; evidence_id: string | null }
export interface Job { id: string; tenant_id: string; kind: JobKind; state: JobState; phase: JobPhase; mode: 'obfuscate' | 'synthesize' | null; group_id: string | null; connection_id: string | null; target_connection_id: string | null; object_ref: string | null; snapshot_id: string | null; spec: Record<string, any>; purpose: { ticket?: string; legal_basis?: string }; kid: string | null; progress: JobProgress; plan: Record<string, any> | null; error_code: string | null; error: string | null; created_by: string | null; created_at: string; updated_at: string; started_at: string | null; finished_at: string | null; expires_at: string | null }
export interface PartitionView { partition_no: number; state: PartitionState; rows_read: number; rows_written: number; rows_skipped: number; batch_no: number; attempts: number; resumed: number; checkpoint_fp: string | null; started_at: string | null; finished_at: string | null; updated_at: string; error_code: string | null; rows_per_second: number | null }
export interface JobStats { workers_online: number; workers_busy: number; workers_draining: number; partitions_waiting: number; oldest_waiting_s: number | null; rows_per_s: number; by_state: Record<string, number>; running: number; tenant_max_running: number; lease_reclaims_24h: number }
export interface MethodSpec { method: string; params: Record<string, unknown> }
export interface ColumnClassification { path: string; name: string; data_type: string; kind: string; is_key: boolean; entity_type: string | null; confidence: number; sensitivity: Sensitivity | null; detectors: string[]; value_hit_rate: number; null_rate: number; distinct_ratio: number; shapes: string[]; text_column: boolean; anchor: boolean; suggested: MethodSpec; method: MethodSpec; override: boolean; synthesize_suggested: MethodSpec | null; synthesize_method: MethodSpec | null; type_note: string | null }
export interface SnapshotOverride { path: string; entity_type?: string | null; method?: MethodSpec | null; synthesize_method?: MethodSpec | null; text_column?: boolean | null; anchor?: boolean | null }
export interface ClassificationSnapshot { id: string; tenant_id: string; job_id: string | null; connection_id: string; engine: string; object_ref: string; status: 'proposed' | 'accepted' | 'superseded'; policy_name: string; policy_hash: string; shape: TableShape; columns: ColumnClassification[]; sample_rows: number; overrides: SnapshotOverride[]; content_hash: string; created_at: string; accepted_at: string | null; accepted_by: string | null }
export interface PkStrategy { kind: 'generate' | 'offset' | 'token'; offset?: number | null; prefix?: string | null; sequence?: string | null }
export interface FilesSpec { format: 'csv' | 'parquet' | 'jsonl'; prefix: string; compression: 'none' | 'gzip' | 'snappy' | 'zstd'; rows_per_file?: number }
export interface TargetSpec { write_mode: WriteMode; connection_id?: string | null; object?: ObjectRef | null; create_if_missing?: boolean; table_load?: 'truncate_insert' | 'upsert'; pk_strategy?: PkStrategy | null; files?: FilesSpec | null; confirm_write?: boolean; expected_rows?: number | null }
export interface RunSpec { mode: 'obfuscate' | 'synthesize'; source: { connection_id: string; object: ObjectRef; selection: Selection; parallelism: number; batch_rows?: number | null }; target: TargetSpec; snapshot_id: string; policy?: string | null; consistency: { scope: 'request' | 'key' | 'entity'; key_material?: Record<string, unknown> | null; synthesis_seed?: Record<string, unknown> | null }; group_id?: string | null; purpose: { ticket: string; legal_basis: string }; options: { dry_run: boolean; verify: 'strict' | 'warn' } }
export interface RunOut { job: Job; key_material?: KeyMaterial; synthesis_seed?: { alg: string; seed: string; created_at: string } }
export interface JobGroup { id: string; tenant_id: string; name: string; kid: string; seed_fingerprint: string; created_by: string | null; created_at: string; closed_at: string | null }
export interface EvidencePack { pack_version: string; id: string; tenant_id: string; job_id: string; job_kind: string; group_id: string | null; outcome: 'succeeded' | 'failed' | 'canceled' | 'dry_run'; mode: string | null; label: string; created_at: string; engine_version: string; image_digest: string | null; purpose: { ticket: string; legal_basis: string } | null; requested_by: string | null; source: Record<string, any>; target: Record<string, any>; policy: Record<string, string>; snapshot: Record<string, any>; consistency: Record<string, any>; columns: Record<string, any>[]; reveal_map: Record<string, any>[]; counts: Record<string, number>; verification: Record<string, any>; fidelity: Record<string, any> | null; privacy: Record<string, any> | null; scan: Record<string, any> | null; timings: Record<string, any>; partitions: Record<string, any>[]; errors: { code: string; where: string }[]; config_hash: string }
export interface EvidenceOut { pack: EvidencePack; pack_sha256: string; artefacts: { id: string; kind: string; bytes: number; content_type: string }[] }
export interface EvidenceSummary { id: string; job_id: string; created_at: string; outcome: string; mode: string | null; label: string; source_object: string | null; target: string | null; write_mode: string | null; policy: string | null; ticket: string | null; passed: boolean; hits: number; rows_written: number; html_artefact_id: string | null }
```

- [ ] **Step 3: API client** — generalise `call()` in `lib/api.ts` (methods, JSON bodies, 204, text and blob responses, `ApiError` with `code` and `extra`), and add the groups:
```ts
export class ApiError extends Error {
  constructor(public status: number, message: string, public code?: string, public extra: Record<string, unknown> = {}) { super(message); }
}

async function request(path: string, auth: Auth, init: RequestInit = {}): Promise<Response> {
  const headers: Record<string, string> = { ...(init.body ? { 'Content-Type': 'application/json' } : {}) };
  if (auth.apiKey) headers['X-API-KEY'] = auth.apiKey;
  if (auth.tenant) headers['X-DPS-Tenant'] = auth.tenant;
  const res = await fetch(path, { ...init, headers: { ...headers, ...(init.headers as Record<string, string> | undefined) } });
  if (!res.ok) {
    let body: Record<string, unknown> = {};
    try { body = await res.json(); } catch { /* non-JSON error */ }
    const { detail, code, ...extra } = body as { detail?: unknown; code?: string };
    throw new ApiError(res.status, typeof detail === 'string' ? detail : res.statusText, code, extra);
  }
  return res;
}
const json = <T,>(path: string, auth: Auth, init?: RequestInit) => request(path, auth, init).then((r) => (r.status === 204 ? (undefined as T) : (r.json() as Promise<T>)));
const post = <T,>(path: string, auth: Auth, body?: unknown, headers?: Record<string, string>) =>
  json<T>(path, auth, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body), headers });
const qs = (p: Record<string, string | number | undefined | null>) => {
  const s = new URLSearchParams(Object.entries(p).filter(([, v]) => v !== undefined && v !== null && v !== '').map(([k, v]) => [k, String(v)]));
  return s.toString() ? `?${s}` : '';
};

export const api = {
  ...m0,                                                    // readyz, metrics, presets, detect, obfuscate, synthesize, reveal (unchanged)
  connections: {
    list: (a: Auth, limit = 100) => json<Page<Connection>>(`/api/v1/connections${qs({ limit })}`, a),
    get: (a: Auth, id: string) => json<Connection>(`/api/v1/connections/${id}`, a),
    create: (a: Auth, spec: ConnectionSpec) => post<Connection>('/api/v1/connections', a, spec),
    update: (a: Auth, id: string, patch: Partial<ConnectionSpec>) => json<Connection>(`/api/v1/connections/${id}`, a, { method: 'PATCH', body: JSON.stringify(patch) }),
    remove: (a: Auth, id: string) => json<void>(`/api/v1/connections/${id}`, a, { method: 'DELETE' }),
    test: (a: Auth, id: string, body: { role: 'source' | 'target'; object?: string; write_mode?: WriteMode }) => post<ConnectionTestResult>(`/api/v1/connections/${id}/test`, a, body),
    objects: (a: Auth, id: string, owner?: string) => json<Page<ObjectRef>>(`/api/v1/connections/${id}/objects${qs({ owner })}`, a),
    describe: (a: Auth, id: string, object: string) => json<TableShape>(`/api/v1/connections/${id}/describe${qs({ object })}`, a),
    count: (a: Auth, id: string, object: ObjectRef, selection: Selection) => post<CountOut>(`/api/v1/connections/${id}/count`, a, { object, selection }),
  },
  jobs: {
    list: (a: Auth, p: { kind?: JobKind; state?: JobState; group_id?: string; limit?: number; cursor?: string } = {}) => json<Page<Job>>(`/api/v1/jobs${qs(p)}`, a),
    get: (a: Auth, id: string) => json<Job>(`/api/v1/jobs/${id}`, a),
    partitions: (a: Auth, id: string) => json<{ items: PartitionView[] }>(`/api/v1/jobs/${id}/partitions`, a),
    stats: (a: Auth) => json<JobStats>('/api/v1/jobs/stats', a),
    discover: (a: Auth, body: { connection_id: string; objects: ObjectRef[]; selection?: Selection; sample_rows?: number; policy?: string }) => post<{ job: Job }>('/api/v1/jobs/discover', a, body),
    run: (a: Auth, spec: RunSpec, idempotencyKey: string) => post<RunOut>('/api/v1/jobs/run', a, spec, { 'Idempotency-Key': idempotencyKey }),
    cancel: (a: Auth, id: string) => post<Job>(`/api/v1/jobs/${id}/cancel`, a),
    pause: (a: Auth, id: string) => post<Job>(`/api/v1/jobs/${id}/pause`, a),
    resume: (a: Auth, id: string) => post<Job>(`/api/v1/jobs/${id}/resume`, a),
    retry: (a: Auth, id: string, consistency?: Record<string, unknown>) => post<Job>(`/api/v1/jobs/${id}/retry`, a, consistency ? { consistency } : {}),
  },
  snapshots: {
    list: (a: Auth, p: { connection_id?: string; object?: string; status?: string; limit?: number } = {}) => json<Page<ClassificationSnapshot>>(`/api/v1/snapshots${qs(p)}`, a),
    get: (a: Auth, id: string) => json<ClassificationSnapshot>(`/api/v1/snapshots/${id}`, a),
    patch: (a: Auth, id: string, overrides: SnapshotOverride[]) => json<ClassificationSnapshot>(`/api/v1/snapshots/${id}`, a, { method: 'PATCH', body: JSON.stringify({ overrides }) }),
    accept: (a: Auth, id: string) => post<ClassificationSnapshot>(`/api/v1/snapshots/${id}/accept`, a),
  },
  groups: {
    list: (a: Auth) => json<Page<JobGroup>>('/api/v1/job-groups', a),
    create: (a: Auth, name: string) => post<{ group: JobGroup; key_material: KeyMaterial; synthesis_seed: Record<string, unknown> }>('/api/v1/job-groups', a, { name }),
  },
  evidence: {
    list: (a: Auth, p: { job_id?: string; limit?: number; cursor?: string } = {}) => json<Page<EvidenceSummary>>(`/api/v1/evidence${qs(p)}`, a),
    get: (a: Auth, id: string) => json<EvidenceOut>(`/api/v1/evidence/${id}`, a),
  },
  artefacts: {
    /** Fetch with the API key (artefacts are not public links), then hand the browser a blob to save. */
    download: async (a: Auth, id: string, filename: string): Promise<void> => {
      const blob = await (await request(`/api/v1/artefacts/${id}`, a)).blob();
      const url = URL.createObjectURL(blob);
      const link = Object.assign(document.createElement('a'), { href: url, download: filename });
      link.click();
      URL.revokeObjectURL(url);
    },
  },
};
```
(`m0` is the existing object, renamed when the file is reorganised; M0 call sites keep `api.detect(...)` etc.)

- [ ] **Step 3b: Helpers** `frontend/src/lib/jobs.ts` — pure functions the pages share (all formatting is locale-stable: `en-US` grouping):
```ts
export type Tone = 'teal' | 'amber' | 'emerald' | 'rose' | 'violet' | 'slate';
export function jobStatus(job: Job, proposedSnapshotIds: Set<string> = new Set()): { label: string; tone: Tone }
//   discover+succeeded with a proposed snapshot → "awaiting review"/amber; running|waiting → "running"/teal; paused → amber;
//   finalize queued → "finalizing"/teal; succeeded → "evidence ready"/emerald (run) or "discovered"; dry_run → "dry run"/violet;
//   failed|dead → "failed"/rose; canceled → slate; queued → "queued"/slate
export function writeModeLabel(mode: WriteMode | undefined, load?: string): string   // "in place" | "append" | "copy to table" (+ " · truncate + insert"|" · upsert") | "export files"
export function engineBadge(engine: string | undefined): 'PG' | 'ORA' | 'MDB' | 'S3' | 'PVC' | '—'
export function progressPct(job: Job): number | null                 // rows_written / rows_estimated, capped 100; null before estimate
export function etaSeconds(job: Job): number | null                  // remaining / rows_per_second
export function fmtCount(n: number | null | undefined): string       // 1948210 → "1,948,210"; null → "—"
export function fmtDuration(seconds: number | null): string          // 472 → "7 min 52 s"; 41 → "41 s"
export function sparkPath(points: [string, number][], width: number, height: number): string   // SVG path "M x y L …"
export function splitCounters(vbm: Record<string, number>): { methods: Record<string, number>; fallbacks: Record<string, number>; columns: Record<string, number> }
export function needsDecision(col: ColumnClassification): boolean    // entity && confidence < 0.8 || type_note in {fpe_domain_too_small, numeric_column, date_column, text_pipeline} || text_column
export function heatTone(col: ColumnClassification): 'crit' | 'sens' | 'decl' | 'kept'
export interface WizardDraft { /* every wizard field (Task 32), including transient secrets */ }
export function draftForStorage(d: WizardDraft): Partial<WizardDraft>   // drops credentials, key material, where/query text and params, confirmation
export function canConfirmWrite(typed: string, objectFqn: string, blocked: boolean): boolean
export function buildRunSpec(d: WizardDraft): RunSpec
export function specPreview(d: WizardDraft): Record<string, unknown>  // the rail's JSON: no credentials, no key, filter as entered but params shown as "<bound>"
export function jobsCsv(jobs: Job[]): string                         // id,created_at,kind,state,source,target,mode,write_mode,rows_written — no values
```
`frontend/src/lib/jobs.test.ts` (Vitest):
```ts
import { describe, expect, it } from 'vitest';
import { buildRunSpec, canConfirmWrite, draftForStorage, fmtCount, fmtDuration, heatTone, jobStatus, jobsCsv, needsDecision, progressPct, sparkPath, splitCounters, writeModeLabel } from './jobs';
import { draft, job, column } from './fixtures';

describe('jobs helpers', () => {
  it('labels statuses like the jobs mockup', () => {
    expect(jobStatus(job({ kind: 'discover', state: 'succeeded', progress: { snapshot_ids: ['snap_1'] } }), new Set(['snap_1']))).toEqual({ label: 'awaiting review', tone: 'amber' });
    expect(jobStatus(job({ state: 'waiting', phase: 'partitions' })).label).toBe('running');
    expect(jobStatus(job({ state: 'succeeded', progress: { outcome: 'dry_run' } })).label).toBe('dry run');
    expect(jobStatus(job({ state: 'failed' })).tone).toBe('rose');
  });
  it('formats counts, durations and write modes', () => {
    expect(fmtCount(1948210)).toBe('1,948,210');
    expect(fmtDuration(472)).toBe('7 min 52 s');
    expect(writeModeLabel('table', 'truncate_insert')).toBe('copy to table · truncate + insert');
    expect(progressPct(job({ progress: { rows_estimated: 200, rows_written: 50 } }))).toBe(25);
  });
  it('splits counters and draws sparklines', () => {
    expect(splitCounters({ det_token: 3, 'fallback:fpe_disabled': 1, 'col:email': 3 })).toEqual({ methods: { det_token: 3 }, fallbacks: { fpe_disabled: 1 }, columns: { email: 3 } });
    expect(sparkPath([['t1', 0], ['t2', 10]], 100, 20)).toBe('M0 20 L100 0');
  });
  it('flags columns that need a decision and colours the heat-map', () => {
    expect(needsDecision(column({ entity_type: 'POSTAL_CODE', confidence: 0.93, type_note: 'fpe_domain_too_small' }))).toBe(true);
    expect(needsDecision(column({ entity_type: 'EMAIL', confidence: 0.99, type_note: null }))).toBe(false);
    expect(heatTone(column({ sensitivity: 'CRITICAL' }))).toBe('crit');
    expect(heatTone(column({ entity_type: null }))).toBe('kept');
  });
  it('never stores secrets, key material or filters in the saved draft', () => {
    const saved = JSON.stringify(draftForStorage(draft({ password: 'pw-123', keyMaterialJson: '{"dek":"x"}', where: "name = 'Ananya'" })));
    expect(saved).not.toContain('pw-123');
    expect(saved).not.toContain('dek');
    expect(saved).not.toContain('Ananya');
  });
  it('builds the run spec and gates the write confirmation', () => {
    const spec = buildRunSpec(draft({ writeMode: 'in_place', typed: 'bank_restore.contacts', rows: 412560 }));
    expect(spec.target).toMatchObject({ write_mode: 'in_place', confirm_write: true, expected_rows: 412560 });
    expect(canConfirmWrite('bank_restore.contacts', 'bank_restore.contacts', false)).toBe(true);
    expect(canConfirmWrite('bank_restore.contact', 'bank_restore.contacts', false)).toBe(false);
    expect(canConfirmWrite('bank_restore.contacts', 'bank_restore.contacts', true)).toBe(false);
  });
  it('exports a value-free CSV', () => {
    const csv = jobsCsv([job({ id: 'j_1', object_ref: 'bank.customers' })]);
    expect(csv.split('\n')[0]).toBe('id,created_at,kind,state,source,target,mode,write_mode,rows_written');
  });
});
```
(`frontend/src/lib/fixtures.ts` holds the `job()`, `column()` and `draft()` factories with defaults.)

- [ ] **Step 4: Hook and components**

`hooks/usePoll.ts`:
```ts
import { useCallback, useEffect, useRef, useState } from 'react';

/** Run `load` now and every `intervalMs` while `active(data)` is true; stops on unmount. */
export function usePoll<T>(load: () => Promise<T>, intervalMs: number, active: (data: T | undefined) => boolean,
                           deps: unknown[] = []): { data: T | undefined; error: Error | undefined; refresh: () => void } {
  const [data, setData] = useState<T>();
  const [error, setError] = useState<Error>();
  const timer = useRef<number>();
  const alive = useRef(true);
  const tick = useCallback(async () => {
    window.clearTimeout(timer.current);
    try {
      const next = await load();
      if (!alive.current) return;
      setData(next);
      setError(undefined);
      if (active(next)) timer.current = window.setTimeout(tick, intervalMs);
    } catch (e) {
      if (!alive.current) return;
      setError(e as Error);
      timer.current = window.setTimeout(tick, intervalMs * 2);
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  useEffect(() => { alive.current = true; void tick(); return () => { alive.current = false; window.clearTimeout(timer.current); }; }, [tick]);
  return { data, error, refresh: () => void tick() };
}
```
Components (each small, typed, styled with the classes ported in Step 5):
- `StateChip({ job })` → `<span className={`chip ${tone}`}>{label}</span>` from `jobStatus`;
- `EngineBadge({ engine })` → the mockup's `.eb` square badge (`PG`, `ORA`, `MDB`, `S3`, `PVC`);
- `Sparkline({ points, width = 160, height = 34, label })` → `<svg role="img" aria-label={label}>` with `sparkPath`;
- `CheckList({ checks, title })` → the mockup's `.checks` list: ✓/✕/! icon per check, name in plain words (`select_source` → "SELECT on the source object"), `detail`/`count`, summary "N of M checks passed";
- `HeatMap({ columns })` → `.hm` grid of `.cell.{crit|sens|decl}` tiles: column name, entity · confidence; kept columns muted;
- `ConfirmWriteModal({ mode, objectFqn, rows, production, adminScope, blockedReason, onConfirm, onCancel })` → a `role="dialog" aria-modal="true"` box with the mode's consequence bullets (texts from mockup 06: in-place — rows updated by key, readers see batches as they commit, originals restorable only with the key returned at submission, one-way columns listed, failed partitions resume from their last committed batch; append — table growth, new key range, rows counted by reports until removed), the production/admin note, a text box "Type <fqn> to confirm", and the primary button `Write <rows> rows in place` / `Append <rows> rows` enabled only when `canConfirmWrite(...)`; Escape cancels; focus is trapped in the dialog;
- `KeyMaterialCard({ keyMaterial, seed, jobId })` → `.keycard` with kid, "shown once — DPS keeps no copy", **Copy JSON** and **Download** (`dps-<job id>.key.json` via a Blob, never `localStorage`), and a "I have stored the key" checkbox that hides the card's JSON once ticked.

- [ ] **Step 5: Page stylesheets** — copy the `<style>` content of mockups `07-jobs.html`, `06-connect-wizard.html`, `03-data-sources.html` and `10-evidence-pack.html` into `frontend/src/styles/jobs.css`, `wizard.css`, `job-detail.css`, `evidence.css`, each wrapped in one nesting block for its page root (`.jobs-page { … }`, `.wizard-page { … }`, `.job-detail-page { … }`, `.evidence-page { … }`) so no rule leaks to other pages; replace the mockups' `body.mode-syn …` selectors with `&.mode-syn …`; delete the job-group rules of 07/03 (`.ghead`, `.keynode`, `.sys3`, `.wires`, `.gsplit`, `.verdict`, `.pair`, `.join` — M3) and the synthetic-companion rules of 10 (M5). Import the four files in `main.tsx` after `styles.css`. The tokens (`--teal`, `--surface`, …) already exist in `styles.css`.

- [ ] **Step 6: Gates and commit**

Run: `cd frontend && npm run typecheck && npm test && npm run build`
Expected: typecheck clean, `7 passed` (Vitest), build writes `dist/`.
```bash
git add frontend/package.json frontend/package-lock.json frontend/vite.config.ts frontend/src/lib frontend/src/hooks/usePoll.ts frontend/src/components frontend/src/styles frontend/src/main.tsx
git commit -m "feat(console): typed M2 API groups, job helpers with Vitest, shared job components and page styles" -- frontend/package.json frontend/package-lock.json frontend/vite.config.ts frontend/src/lib frontend/src/hooks/usePoll.ts frontend/src/components frontend/src/styles frontend/src/main.tsx
```

---

### Task 30: Jobs page (mockup 07) and Overview recent jobs

**Files:**
- Create: `frontend/src/pages/Jobs.tsx`
- Modify: `frontend/src/App.tsx` (routes `/data-sources` and `/data-sources/jobs` → `Jobs`), `frontend/src/pages/Overview.tsx` (recent jobs + jobs running)

**Layout (mockup 07, root `<main className="page jobs-page">`):**
1. **Page head** — "Jobs" / "Batch jobs over PostgreSQL in the last 7 days. Keys go back to the requester when a job is submitted; DPS keeps none." Buttons: **Export list (CSV)** (`jobsCsv` of the filtered rows → Blob download) and **New job** (link to `/data-sources/jobs/new`).
2. **Queue and workers** card (`.hgrid` of six `.ht` tiles from `GET /jobs/stats`, refreshed every 10 s, "updated hh:mm:ss · refreshes every 10 s"): *Workers* — `workers_online` online, `workers_busy` busy, `workers_draining` draining (the `.wk` dots); *Queue depth* — `partitions_waiting` partitions waiting + sparkline of the last 60 samples kept in component state (client-side ring buffer; the API has no history); *Oldest waiting* — `fmtDuration(oldest_waiting_s)`, "Alert at 5 min" (amber when ≥ 300 s); *Throughput* — `rows_per_s` rows protected / s + sparkline ring buffer; *Running jobs* — `running` of `tenant_max_running`, "Tenant cap · N slots free"; *Lease reclaims · 24 h* — `lease_reclaims_24h`, "Both resumed from their checkpoint. No row was written twice." when > 0.
3. **Filters** (`.filters`): search (job id, object, ticket), Status (all / running / awaiting review / completed / failed — counts from the loaded list), Engine, Mode, Write mode, Owner (schema part of `object_ref`), **Clear filters**. Filters are client-side over the loaded page (`GET /jobs?limit=200`).
4. **Jobs table** (`table.jobs`): *Job* — short id (`j_01K7…9Q`, link to detail), ticket · created time; *Source → target* — `EngineBadge`, `object_ref`, → target object / "rewritten in place" / "→ Parquet on S3 · <prefix>" / "append · offset keys"; *Mode · write* — chips `obfuscate|synthesize` and `writeModeLabel`; *Owner*; *Progress* — bar + `rows_written / rows_estimated` (or "not started · N rows sampled" for discover jobs); *Status* — `StateChip` + one line (partitions and ETA while running; "leakage sweep 0 hits" when succeeded; the error code when failed); *Actions* — **Open**, **Review N columns** (awaiting review → `/data-sources/jobs/new?snapshot=<id>`), **Evidence pack** (→ `/reports/evidence/<evidence_id>`), **Show error** (expands an `.errbox` row with `error_code` explained in plain words and **Retry** for failed/dead run jobs).
5. **Empty state** — "No jobs match these filters" + Clear filters / New job.
6. **Footer** — "Times are in your browser's time zone. Jobs and their evidence are kept for 30 days. Paused jobs resume from their last checkpoint."
Polling: the list every 5 s while any job is non-terminal (else every 30 s); stats every 10 s. Job groups (the lower half of mockup 07) render in M3.

**Overview** (`Overview.tsx`): when `/readyz` shows `jobs` ok, add a "Jobs running" KPI (`stats.running`) and a "Recent jobs" card (5 newest from `GET /jobs?limit=5`: id, object, mode, `StateChip`, link) — the M2 part of mockup 01.

- [ ] **Step 1:** implement both; **Step 2:** `cd frontend && npm run typecheck && npm test && npm run build` → clean; **Step 3:** with the compose stack up, open `http://127.0.0.1:8400/data-sources`, paste the bootstrap key, check the strip, filters and table against `docs/design/mockups/07-jobs.html` side by side (Task 36 automates the screenshots).
- [ ] **Step 4: Commit**
```bash
git add frontend/src/pages/Jobs.tsx frontend/src/pages/Overview.tsx frontend/src/App.tsx
git commit -m "feat(console): Jobs page - queue health strip, filters, jobs table; recent jobs on the overview" -- frontend/src/pages/Jobs.tsx frontend/src/pages/Overview.tsx frontend/src/App.tsx
```

---

### Task 31: Job detail page (mockup 03)

**Files:**
- Create: `frontend/src/pages/JobDetail.tsx`
- Modify: `frontend/src/App.tsx` (route `/data-sources/jobs/:jobId`)

**Layout (mockup 03, root `<main className="page job-detail-page">`):**
1. **Head** — `<EngineBadge>` `source object → target object` (or "rewritten in place", "→ files"); line: "Mode obfuscate · policy <name> · purpose <ticket> (<legal basis>)"; group line when `group_id`. Actions: **Pause** (state `waiting`) / **Resume** (`paused`), **Cancel** (non-terminal and not finalizing; a confirm dialog "Cancel job? Rows already written stay written; the evidence pack records the cancellation."), **Retry** (`failed`/`dead` run jobs: opens a panel to load the key JSON file — kept in memory — then `POST /jobs/{id}/retry`; a 409 `key_material_required` explains why), **Evidence pack** (enabled when `progress.evidence_id`).
2. **Stepper** — ✓ Connect · ✓ Discover · *n sampled* · ✓ Review · *columns, overrides* · ✓ Target · *created/truncate_insert|upsert|append|files* · **Run · pct · partitions** · Evidence; current step highlighted by `phase` (prepare → Target, partitions → Run, finalize → Evidence).
3. **Column classification (reviewed snapshot)** — table from `GET /snapshots/{snapshot_id}`: Column, Type, Inferred entity (heat legend tone), Conf., Method (obfuscate or synthesize column per job mode), Evidence (detectors, `override` badge, `type_note` in words). Kept columns collapsed into "+ N more columns kept"; footer "snapshot <id> is part of the evidence pack · examples shown to reviewers were masked".
4. **Run · partitions** — one `.part` row per `PartitionView`: `p<no>`, progress bar (`rows_written / (rows_estimated / partitions_total)`, capped), rows/s, checkpoint fingerprint `#<checkpoint_fp>` (never key digits — see "Open contract questions"), state chip, `resumed` badge ("lease reclaimed, resumed" when `resumed > 0`); right: `Sparkline` of `progress.throughput` ("throughput · 10 min · ~N rows/s total"); explanation line when any partition resumed: "p6: worker lost its lease; the reaper re-queued it from its last checkpoint — no row written twice."
5. **Evidence so far** — six `.ev .box` tiles: Rows protected (`rows_written`), Values transformed (sum of `splitCounters(values_by_method).methods`), Leakage sweep (`verification.hits ?? 0` hits), FPE fallbacks (sum of fallbacks, with the dominant note), Raw PII to LLM **0**, Partitions (`done/total`, failed in rose); the label callout ("Output is pseudonymised — still personal data … The key is held by the requesting team (kid …); DPS keeps no copy." or the synthetic label).
6. **Error card** when failed/dead: `error_code` in words + `error`.
Discover jobs show the snapshot list and **Review and accept** (→ wizard step 3 with `?snapshot=`). Polling: `GET /jobs/{id}` every 2 s while non-terminal; partitions every 2 s while `phase == 'partitions'` or finalize; snapshot once.

- [ ] **Step 1:** implement; **Step 2:** typecheck/test/build clean; **Step 3:** compare with `03-data-sources.html` on a running job of the demo; **Step 4: Commit**
```bash
git add frontend/src/pages/JobDetail.tsx frontend/src/App.tsx
git commit -m "feat(console): job detail - stepper, reviewed snapshot, live partitions and throughput, controls" -- frontend/src/pages/JobDetail.tsx frontend/src/App.tsx
```

---

### Task 32: New-job wizard, steps 1–2 (connect, select data)

**Files:**
- Create: `frontend/src/pages/JobWizard.tsx`, `frontend/src/pages/wizard/StepConnect.tsx`, `frontend/src/pages/wizard/StepSelect.tsx`, `frontend/src/pages/wizard/state.ts`
- Modify: `frontend/src/App.tsx` (route `/data-sources/jobs/new`), `frontend/src/lib/jobs.ts` (fill `WizardDraft`)

**Shell (mockup 06, root `<main className="page wizard-page">`, class `mode-syn` when the mode is synthesize):** crumb "Data sources & jobs / New job · draft d_<8 hex> · Draft saved hh:mm"; head "New job — Protect a table in five steps. DPS reads only the discovery sample until you press Run job, and writes nothing before then."; **Cancel** (back to Jobs) and **Save draft** (`localStorage['dps.jobDraft']` ← `draftForStorage(draft)`; restored on open, with a note "Credentials, keys and filters are not saved: re-enter them"). Left: the five step sections (`.wsteps` header with each step's summary once done — "1 Connect PostgreSQL · bank-pg-ro", "2 Select data 1,948,210 rows · 22 columns", …; only the active section is expanded; completed ones can be reopened). Right rail: **What DPS guarantees** (four lines from the mockup, the third adapted: "Values are transformed inside the worker. Raw PII sent to an LLM: 0.") and **Job specification** (`specPreview(draft)` as highlighted JSON with **Copy JSON**; "Key material is never part of the specification."). State: a `useReducer` over `WizardDraft` in `state.ts` (actions per field; `reset`), never written anywhere but React state except the filtered draft.

**Step 1 · Connect to the source** — *Engine*: three radio cards; **PostgreSQL** enabled; Oracle and MongoDB disabled with chip "M3" and their mockup copy. *Source connection*: **Use a saved connection** (select of `GET /connections` with engine `postgres` and role `source`; production chip) or **New connection** with a segmented control **Secret reference** (field `secret_ref`, help text from the mockup) / **Inline credentials (dev only)** (host, port, database, username, password — password field `autocomplete="off"`; hidden when the production switch is on, with the mockup's warning); **Tagged production** switch ("writes need the admin scope"). *Target connection used by Copy to a table* (optional select; also editable in step 4). **Test connection** → creates the connection when new (`POST /connections`; the password leaves React state right after the create succeeds) → `POST /connections/{id}/test {role: "source", object?}` → `CheckList` with "N of M checks passed", server version, "Tested at hh:mm:ss · no rows were read". **Next: Select data** enabled when the test is ok.

**Step 2 · Select data** — *Owner* select (distinct owners of `GET /connections/{id}/objects`), *Table* select (objects of that owner) → `GET …/describe` → shape line "N rows (estimate) · M columns · primary key <cols> <type>"; *Which rows*: segmented **WHERE expression** / **Read-only SELECT** with a monospace textarea and **Check** → `POST …/count` → `CheckList` of the six selection checks ("One statement", "SELECT only", "No INTO, procedure calls, database links or FOR UPDATE", "Identifiers validated", "Literals bound as parameters", "Keyset-able") with counts, the **Runs as** pane (`runs_as`) and "N rows match · counted at hh:mm in x s"; a 422 shows the failing check and the server's message (never echoes the SQL). *Columns*: checklist of the shape's columns (key columns locked "key · always read"), Select all / Clear, help "Columns you leave out are not read. For In place, columns you leave out keep their current values." *Limits*: Max rows (empty = all), Discovery sample (default 2 000), Parallelism select (2 / 4 / 8 / 16 partitions by key range; "Checkpoint after every committed batch"). **Next: Discover**.

**Accessible names** (Task 36 drives the wizard by them; keep them exact): radio cards labelled `PostgreSQL`, `Oracle`, `MongoDB`; selects labelled `Saved connection`, `Owner`, `Table`, `Target connection`; fields `Destination schema`, `Destination table`, `WHERE expression`, `Ticket`; buttons `Test connection`, `Check`, `Run discovery`, `Accept snapshot`, `Confirm`, `Choose another write mode`, `Dry run (no writes)`, `Run job`, and the step buttons `Next: Select data`, `Next: Discover`, `Next: Target and write mode`, `Next: Review and run`; write-mode cards labelled `In place`, `Append new rows`, `Copy to a table`, `Export files`; the confirmation dialog has `role="dialog"`, a field labelled `Type <object> to confirm` and the button `Write <n> rows in place` / `Append <n> rows`; after submission the links `Open job` and `Open in Jobs`.

- [ ] **Step 1:** implement; **Step 2:** add Vitest cases to `jobs.test.ts` for the reducer: selecting a new table clears the selection, the check result and the discovery state; switching to "Inline credentials" while production is on is refused; `draftForStorage` after a successful test holds the connection id but no password. **Step 3:** typecheck/test/build clean. **Step 4: Commit**
```bash
git add frontend/src/pages/JobWizard.tsx frontend/src/pages/wizard/StepConnect.tsx frontend/src/pages/wizard/StepSelect.tsx frontend/src/pages/wizard/state.ts frontend/src/lib/jobs.ts frontend/src/lib/jobs.test.ts frontend/src/App.tsx
git commit -m "feat(console): new-job wizard - connect and test a source, select and check the rows to read" -- frontend/src/pages/JobWizard.tsx frontend/src/pages/wizard/StepConnect.tsx frontend/src/pages/wizard/StepSelect.tsx frontend/src/pages/wizard/state.ts frontend/src/lib/jobs.ts frontend/src/lib/jobs.test.ts frontend/src/App.tsx
```

---

### Task 33: New-job wizard, step 3 (discover and review)

**Files:**
- Create: `frontend/src/pages/wizard/StepDiscover.tsx`
- Modify: `frontend/src/pages/JobWizard.tsx` (mount step 3; `?snapshot=` deep link)

**Step 3 · Discover** — **Run discovery** → `POST /jobs/discover {connection_id, objects: [object], selection, sample_rows}` → poll `GET /jobs/{id}` every 2 s ("Sampling …", then "Sampled N of M rows in x s · sample complete") → `GET /snapshots/{progress.snapshot_ids[0]}`. Detector line: "Detectors: structural · patterns and checksums · NER <on|off> · LLM labelling off · 0 raw values sent to the LLM". Four count boxes: **Critical** (sensitivity CRITICAL), **Sensitive** (other entities), **Declared** (detector `declared`), **Kept** (no entity). **Column heat-map** (`HeatMap`, legend critical/sensitive/declared/kept). **Needs a decision** list (`needsDecision`): column, why (from `type_note` and confidence: "fewer than 10⁶ possible values, so format-preserving encryption would be easy to reverse", "Free text with personal data in N % of sampled rows", "numeric column: the policy method would not fit"), suggested method, **Accept** (keeps the suggestion and marks it reviewed) / **Change** (opens the row in the review table). **Review table** (all columns): Column, Type, Entity (editable select of `GET /entities` when M1 is present, else free text), Conf., Examples (the stored `shapes` — "examples are masked"), Method (select of the 17 method names; the obfuscate column, plus the synthesize column when the wizard mode is synthesize), Anchor (radio), Text pipeline (checkbox). Each change builds the full overrides list and `PATCH /snapshots/{id}` (debounced 400 ms); the table re-renders from the response. **Accept snapshot** → `POST /snapshots/{id}/accept` → "Accepted choices are saved as classification snapshot snap_… It becomes the run policy and goes into the evidence pack." **Next: Target and write mode** enabled once accepted. With `?snapshot=<id>` the wizard opens here on that snapshot (connection and object restored from it).

- [ ] **Step 1:** implement; **Step 2:** Vitest for the overrides builder (`overridesFor(snapshot, edits)` in `lib/jobs.ts`: only changed fields are sent; setting an anchor clears the others); **Step 3:** typecheck/test/build clean; **Step 4: Commit**
```bash
git add frontend/src/pages/wizard/StepDiscover.tsx frontend/src/pages/JobWizard.tsx frontend/src/lib/jobs.ts frontend/src/lib/jobs.test.ts
git commit -m "feat(console): wizard discovery - heat-map, decisions, review table with overrides, snapshot acceptance" -- frontend/src/pages/wizard/StepDiscover.tsx frontend/src/pages/JobWizard.tsx frontend/src/lib/jobs.ts frontend/src/lib/jobs.test.ts
```

---

### Task 34: New-job wizard, steps 4–5 (target, confirmation, run, key card)

**Files:**
- Create: `frontend/src/pages/wizard/StepTarget.tsx`, `frontend/src/pages/wizard/StepRun.tsx`
- Modify: `frontend/src/pages/JobWizard.tsx`

**Step 4 · Target and write mode** — four radio cards with the mockup's copy and tags: **In place** ("changes source rows"), **Append new rows** ("adds rows to source"), **Copy to a table** ("source unchanged"), **Export files** ("source unchanged"). Panels:
- *Copy to a table*: target connection (select, role target), destination schema and table (defaults: `<owner>_uat`, same name), Load segmented **Truncate and insert** / **Upsert on <key>**, **Create the table if it is missing**; **Check target** → `POST /connections/{id}/test {role: "target", object, write_mode: "table"}` → checks.
- *Export files*: format **CSV / Parquet / JSONL**, destination connection (role target, engine `s3`/`pvc`; "Add a file store" inline mini-form creating an `s3` (bucket, endpoint, secret ref) or `pvc` (base path) connection), prefix, compression; **Check** → store test (put/delete probe) → checks; note "manifest.json lists each file with its row count and SHA-256, written last".
- *In place*: write connection = the source connection (shown read-only; when it lacks the `target` role, an amber callout explains how to add it and **Next** stays disabled — see "Open contract questions" on separate write connections), **Commit every** (batch rows 1 000 / 5 000 / 20 000), checks (`test role target write_mode in_place`: UPDATE privilege, ledger schema); the key columns line "is the update key and is not changed".
- *Append*: **Same table** / **Named table** (schema + name), primary-key strategy radios **Database-generated** / **Offset** (number; for text keys a prefix) / **Token** (disabled for the same table and in synthesize mode, with the reason); checks (`test role target write_mode append`); a note that the collision pre-check runs when the job prepares and stops it before any write.
For **In place** and **Append**, **Confirm** opens `ConfirmWriteModal` with `rows` from step 2's count, the production/admin note (`connection.tags` includes `production`; when the current key lacks `admin`, the modal says so and stays blocked), and the type-to-confirm box; after confirming: "Confirmed at hh:mm. Nothing is written yet. The job writes only after you press Run job in step 5." + **Undo**. **Next: Review and run** requires a confirmation for these two modes.

**Step 5 · Review and run** — *Mode* radios **Obfuscate** ("reversible with the key") / **Synthesize** ("one-way"); *Policy* preset select (`GET /policies/presets`; default = the snapshot's policy) with "N overrides from discovery"; *Plan* — the snapshot's columns grouped by method for the chosen mode (`fpe_digits CUSTOMER_ID · …`, "keep N columns"); *Consistency key* (obfuscate) / *Synthesis seed* (synthesize): **New key for this job** or **Reuse the key of a job group** (select from `GET /job-groups`, then **Load key file** — a `.json` read with `FileReader` into React state only; the `kid` must equal the group's or the panel shows "kid does not match the group"); *Purpose* (required): Ticket, Legal basis select (four options from the mockup), "What the data is for" (appended to the legal basis, ≤ 256 characters in total); **Ready to run** summary rows with **Edit** links (Source, Rows, Discovery, Write, Key, Purpose) and the blocked reason when a confirmation is missing. Buttons **Dry run (no writes)** and **Run job**: `POST /jobs/run` (`buildRunSpec(draft)`, `Idempotency-Key` = a UUID generated once per wizard submission so a double click cannot start two jobs). On success: `KeyMaterialCard` (obfuscate, when DPS generated the key) or the seed card (synthesize) — "shown once; DPS keeps no copy" — and **Open in Jobs** / **Open job**; the draft in `localStorage` is cleared. A dry run navigates to the job detail. API errors show the server's `detail` and `code` next to the button.

- [ ] **Step 1:** implement; **Step 2:** Vitest for `buildRunSpec` per write mode (table: no `pk_strategy`; append same table: `object` omitted; files: `files` set and no `object`; group reuse: `group_id` + `consistency.key_material` from the loaded file; synthesize: no `key_material`); **Step 3:** typecheck/test/build clean; **Step 4: Commit**
```bash
git add frontend/src/pages/wizard/StepTarget.tsx frontend/src/pages/wizard/StepRun.tsx frontend/src/pages/JobWizard.tsx frontend/src/lib/jobs.ts frontend/src/lib/jobs.test.ts
git commit -m "feat(console): wizard target, write confirmation, review and run with a once-only key card" -- frontend/src/pages/wizard/StepTarget.tsx frontend/src/pages/wizard/StepRun.tsx frontend/src/pages/JobWizard.tsx frontend/src/lib/jobs.ts frontend/src/lib/jobs.test.ts
```

---

### Task 35: Evidence pack page (mockup 10) and Reports evidence list (mockup 09)

**Files:**
- Create: `frontend/src/pages/EvidencePack.tsx`, `frontend/src/pages/Reports.tsx`
- Modify: `frontend/src/App.tsx` (routes `/reports/evidence/:evidenceId`, `/reports` → `Reports`), `frontend/src/styles.css` (print rules)

**Evidence pack** (root `<main className="page evidence-page">`, data from `GET /evidence/{id}`): crumb "Reports & audit / Evidence pack j_…" + **Back to reports**; toolbar **Download JSON** (`api.artefacts.download` of the `evidence_json` artefact — the exact bytes whose sha256 is printed), **Open HTML** (download of `evidence_html`), **Print or save as PDF** (`window.print()`). The document: title `<engine> <source> → <target>`, "Job <id> · <mode> · purpose <ticket>", status chip "verified · 0 leaks" (or rose "verification failed"), "Sealed <created_at> · Pack <id> · sha256 <first 8>…<last 4>", the label chip; meta grid (Tenant, Requested by key, Purpose, Legal basis, Job group, Mode · write, Pack kept until <expires>); sections **1 Summary** (sentence + six KPIs), **2 Configuration hashes** (policy, snapshot, config hash, engine version, image digest, key id and custody — "Data key returned at job creation; DPS stored no key. This pack holds the key id only.", source/target/selection template), **3 Classification snapshot** (columns with method and decision), **4 Execution** (partition table + "Checkpoint positions stay in DPS's job tables and are not printed here, because a key position is a customer identifier."), **5 Verification** (leakage sweep, post-write sample incl. `kept_column_hits`, row counts, FPE fallbacks), **7 Regulatory note** (by label), **8 Signatures** (system attestation with the full sha256; data owner, DPO and change approver blanks). Section 6 renders only when `fidelity`/`privacy` exist (M5). Print CSS: `@media print { .sidebar, .topbar, .no-print { display: none } .page { padding: 0 } section { break-inside: avoid } }`.

**Reports** (mockup 09, M2 subset — contract §14: the evidence list is M2, the rest M5): head "Evidence and audit — Every job leaves an evidence pack. Packs hold counts, entity types, method names, hashes and key ids. They never hold values."; KPI tiles **Evidence packs** (count), **Verification** (passed / failed), **Customer values stored 0**; **Evidence packs** table from `GET /evidence?limit=50` (+ "Show older" via `next_cursor`): Job or request (short id · source → target · ticket · write mode · rows), Layer ("Connectors"), Mode, Policy, Verification chip ("passed · 0 leaks" / "failed · N leaks caught"), Created (UTC), Download **JSON** / **Open** (→ the evidence page). A card states "The audit log, verification trends and exports arrive with M5." in place of the M5 sections (no fake data).

- [ ] **Step 1:** implement; **Step 2:** typecheck/test/build clean; **Step 3: Commit**
```bash
git add frontend/src/pages/EvidencePack.tsx frontend/src/pages/Reports.tsx frontend/src/App.tsx frontend/src/styles.css
git commit -m "feat(console): printable evidence pack page and the evidence list of Reports" -- frontend/src/pages/EvidencePack.tsx frontend/src/pages/Reports.tsx frontend/src/App.tsx frontend/src/styles.css
```

---

### Task 36: Console browser check against the mockups

**Files:**
- Create: `tests/console/__init__.py`, `tests/console/test_console_m2.py`
- Test: the module above (markers `browser`, `integration`, `pg_source`, `slow`)

Playwright for Python **1.55.0** (the `e2e` extra; the same pin as the M4 browser image). Before the first run: `python -m playwright install chromium` — a no-op when the browser revision for 1.55.0 is already in `~/Library/Caches/ms-playwright`, otherwise one download (network). The test drives the **compose stack** (Task 27, `DPS_TEST_APP_URL`, default `http://127.0.0.1:8400`) with the bootstrap key (`DPS_TEST_API_KEY`, default `dps_local_dev_key_change_me`) and the demo connections created by the smoke test (`bank-pg-ro`, `bank-uat`, `bank-restore-rw`). It writes screenshots to `docs/superpowers/build/m2-console/` (git-ignored by the executor; the build report links them).

```python
"""Console M2 pages against the running stack and the approved mockups (Task 36)."""
from __future__ import annotations

import os
import re
from pathlib import Path

import asyncpg
import pytest

pytestmark = [pytest.mark.browser, pytest.mark.integration, pytest.mark.pg_source, pytest.mark.slow]
playwright = pytest.importorskip("playwright.sync_api")
ROOT = Path(__file__).resolve().parents[2]
SHOTS = ROOT / "docs" / "superpowers" / "build" / "m2-console"
MOCKUPS = ROOT / "docs" / "design" / "mockups"
APP = os.environ.get("DPS_TEST_APP_URL", "http://127.0.0.1:8400")
KEY = os.environ.get("DPS_TEST_API_KEY", "dps_local_dev_key_change_me")
VIEWPORTS = {"desktop": (1440, 900), "mobile": (390, 844)}


@pytest.fixture(scope="module")
def customer_names() -> list[str]:
    import asyncio

    async def load() -> list[str]:
        conn = await asyncpg.connect(os.environ.get("DPS_TEST_SOURCE_PG_DSN",
                                                    "postgresql://postgres:postgres@127.0.0.1:5442/bank"))
        try:
            return [r["full_name"] for r in await conn.fetch("SELECT full_name FROM bank.customers LIMIT 25")]
        finally:
            await conn.close()
    return asyncio.run(load())


@pytest.fixture(scope="module")
def page():  # noqa: ANN201
    from playwright.sync_api import sync_playwright

    SHOTS.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1440, "height": 900}, color_scheme="dark")
        ctx.add_init_script(f"localStorage.setItem('dps.apiKey', '{KEY}'); localStorage.setItem('dps.tenant', '');")
        pg = ctx.new_page()
        yield pg
        browser.close()


def shoot(pg, name: str) -> None:  # noqa: ANN001
    for label, (w, h) in VIEWPORTS.items():
        pg.set_viewport_size({"width": w, "height": h})
        pg.screenshot(path=str(SHOTS / f"{name}-{label}.png"), full_page=True)
        assert pg.evaluate("document.scrollingElement.scrollWidth <= window.innerWidth + 1"), f"{name}: horizontal scroll at {label}"
    pg.set_viewport_size({"width": 1440, "height": 900})


def mockup(pg, file: str, name: str) -> None:  # noqa: ANN001
    pg.goto((MOCKUPS / file).as_uri())
    pg.screenshot(path=str(SHOTS / f"{name}-mockup.png"), full_page=True)


def no_values(pg, names: list[str]) -> None:  # noqa: ANN001
    text = pg.inner_text("body")
    assert not [n for n in names if n and n in text], "a source customer value is visible"


def test_jobs_page(page, customer_names) -> None:  # noqa: ANN001
    page.goto(f"{APP}/data-sources")
    for label in ("Workers", "Queue depth", "Oldest waiting", "Throughput", "Running jobs", "Lease reclaims"):
        page.get_by_text(label, exact=False).first.wait_for()
    for header in ("Job", "Source → target", "Mode · write", "Owner", "Progress", "Status", "Actions"):
        assert page.locator("table.jobs th", has_text=header).count() >= 1
    shoot(page, "07-jobs")
    no_values(page, customer_names)
    mockup(page, "07-jobs.html", "07-jobs")


def test_wizard_to_evidence(page, customer_names) -> None:  # noqa: ANN001
    page.goto(f"{APP}/data-sources/jobs/new")
    page.get_by_label("PostgreSQL").check()
    page.get_by_label("Saved connection").select_option(label=re.compile("bank-pg-ro"))
    page.get_by_role("button", name="Test connection").click()
    page.get_by_text(re.compile(r"\d+ of \d+ checks passed")).wait_for(timeout=30_000)
    shoot(page, "06-wizard-1-connect")
    page.get_by_role("button", name="Next: Select data").click()
    page.get_by_label("Owner").select_option("bank")
    page.get_by_label("Table").select_option("customers")
    page.get_by_label("WHERE expression").fill("country_code = 'IN'")
    page.get_by_role("button", name="Check").click()
    page.get_by_text(re.compile(r"rows match")).wait_for(timeout=30_000)
    shoot(page, "06-wizard-2-select")
    page.get_by_role("button", name="Next: Discover").click()
    page.get_by_role("button", name="Run discovery").click()
    page.get_by_text("Column heat-map").wait_for(timeout=120_000)
    assert page.locator(".hm .cell").count() >= 10
    shoot(page, "06-wizard-3-discover")
    no_values(page, customer_names)
    page.get_by_role("button", name="Accept snapshot").click()
    page.get_by_text(re.compile(r"classification snapshot snap_")).wait_for()
    page.get_by_role("button", name="Next: Target and write mode").click()
    page.get_by_label(re.compile("In place")).check()
    page.get_by_role("button", name=re.compile("Confirm")).click()
    dialog = page.get_by_role("dialog")
    write = dialog.get_by_role("button", name=re.compile(r"Write .* rows in place"))
    assert write.is_disabled()
    dialog.get_by_label(re.compile("Type .* to confirm")).fill("bank.customers")
    shoot(page, "06-wizard-4-confirm")
    dialog.get_by_role("button", name="Choose another write mode").click()
    page.get_by_label(re.compile("Copy to a table")).check()
    page.get_by_label("Target connection").select_option(label=re.compile("bank-uat"))
    page.get_by_label("Destination schema").fill("bank_uat")
    page.get_by_label("Destination table").fill("customers_console")
    page.get_by_role("button", name="Next: Review and run").click()
    page.get_by_label("Ticket").fill("CHG-12345")
    shoot(page, "06-wizard-5-review")
    page.get_by_role("button", name="Run job").click()
    page.get_by_text(re.compile("shown once")).wait_for(timeout=30_000)
    page.get_by_role("link", name="Open job").click()
    page.get_by_text("Run · partitions").wait_for()
    shoot(page, "03-job-detail")
    page.get_by_role("link", name="Evidence pack").wait_for(timeout=240_000)
    page.get_by_role("link", name="Evidence pack").click()
    for section in ("1 Summary", "2 Configuration hashes", "3 Classification snapshot", "4 Execution",
                    "5 Verification", "7 Regulatory note", "8 Signatures"):
        page.get_by_text(section).first.wait_for()
    shoot(page, "10-evidence")
    no_values(page, customer_names)
    page.goto(f"{APP}/reports")
    page.get_by_text("Evidence packs").first.wait_for()
    shoot(page, "09-reports")
    for file, name in (("06-connect-wizard.html", "06-wizard"), ("03-data-sources.html", "03-job-detail"),
                       ("10-evidence-pack.html", "10-evidence"), ("09-reports.html", "09-reports")):
        mockup(page, file, name)
```

- [ ] **Step 1:** write the module; **Step 2:** with the stack up and the smoke test passed, run `python -m playwright install chromium && DPS_TEST_REQUIRE_SERVICES=1 pytest -q tests/console/test_console_m2.py`
Expected: `2 passed`; `docs/superpowers/build/m2-console/` holds, per page, `<page>-desktop.png`, `<page>-mobile.png` and `<page>-mockup.png`.
- [ ] **Step 3: Visual comparison (agent or reviewer).** Open each `*-desktop.png` beside its `*-mockup.png` and check the list in the Definition of Done (section order, card structure, labels, signal colours, chip shapes, light/dark tokens, mobile layout without horizontal scroll). Differences that come from M3/M5 scope (job groups, Oracle/Mongo content, synthetic companion, audit log) are expected; anything else is fixed in the page and re-shot before the milestone closes. Record the outcome in the build report.
- [ ] **Step 4: Commit**
```bash
git add tests/console/__init__.py tests/console/test_console_m2.py
git commit -m "test(console): browser check of the jobs, wizard, job detail, evidence and reports pages" -- tests/console/__init__.py tests/console/test_console_m2.py
```

---

### Task 37: M2 log-scan test

**Files:**
- Create: `tests/test_no_values_in_logs_m2.py`
- Test: the module above (static part offline; dynamic part `db`, `pg_source`)

Contract §0.1: each milestone adds a log-scan test for its modules.

- [ ] **Step 1: Write the test**

```python
"""No customer value, key, seed, password or selection literal reaches a log line (contract §0.1)."""
from __future__ import annotations

import ast
import io
import json
import logging
from pathlib import Path

import pytest

from dps.observability import _SAFE_FIELDS

ROOT = Path(__file__).resolve().parents[1]
M2_MODULES = sorted([*(ROOT / "dps").glob("jobs*.py"), ROOT / "dps" / "worker.py", ROOT / "dps" / "retention.py",
                     ROOT / "dps" / "db.py", ROOT / "dps" / "connections.py", ROOT / "dps" / "snapshots.py",
                     ROOT / "dps" / "groups.py", ROOT / "dps" / "storage.py", ROOT / "dps" / "secrets.py",
                     ROOT / "dps" / "engine" / "batch.py", *(ROOT / "dps" / "layers" / "connectors").glob("*.py"),
                     *[ROOT / "dps" / "layers" / "api" / "routers" / f"{n}.py"
                       for n in ("connections", "jobs", "snapshots", "groups", "evidence", "artefacts")]])
LOG_METHODS = {"debug", "info", "warning", "error", "exception", "critical"}


def _log_calls(tree: ast.AST):  # noqa: ANN202
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in LOG_METHODS
                and isinstance(node.func.value, ast.Name) and node.func.value.id == "logger"):
            yield node


@pytest.mark.parametrize("path", M2_MODULES, ids=lambda p: p.name)
def test_log_calls_use_snake_case_events_and_safe_fields(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for call in _log_calls(tree):
        assert call.args and isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, str), \
            f"{path.name}:{call.lineno} log event must be a string literal"
        assert len(call.args) == 1, f"{path.name}:{call.lineno} no positional format args"
        unsafe = {kw.arg for kw in call.keywords} - _SAFE_FIELDS
        assert not unsafe, f"{path.name}:{call.lineno} unsafe log fields {unsafe}"
    assert "print(" not in path.read_text(encoding="utf-8"), f"{path.name} prints"


@pytest.mark.db
@pytest.mark.integration
@pytest.mark.pg_source
async def test_a_full_job_logs_no_values(handler_ctx, source_pg, register_source, capsys) -> None:  # noqa: ANN001
    from dps.auth import LOCAL_PRINCIPAL
    from dps.layers.connectors.submit import submit_run
    from dps.observability import configure_logging
    from tests.connectors.helpers import discover_and_accept, drive
    from tests.connectors.test_run_modes import run_spec

    stream = io.StringIO()
    configure_logging("DEBUG", json_output=True)
    root = logging.getLogger()
    handler = logging.StreamHandler(stream)
    root.addHandler(handler)
    try:
        obj = await source_pg.customers_table(rows=500, name="logscan")
        src = await register_source("t1", name="src-logscan")
        uat = await register_source("t1", ref="dps/src/test-pg-rw", roles={"target"}, name="uat-logscan")
        snap = await discover_and_accept(handler_ctx, "t1", src.id, obj)
        sub = await submit_run(run_spec(src.id, obj, snap.id, {"write_mode": "table", "connection_id": uat.id,
                                                               "object": {"owner": source_pg.schema, "name": "logscan_out"}},
                                        source={"selection": {"where": "full_name LIKE 'A%'"}}),
                               tenant_id="t1", principal=LOCAL_PRINCIPAL, settings=handler_ctx.settings,
                               kek=handler_ctx.kek)
        await drive(handler_ctx, "t1", sub.job.id)
    finally:
        root.removeHandler(handler)
    cap = capsys.readouterr()
    captured = stream.getvalue() + cap.out + cap.err
    rows = await source_pg.admin.fetch(f'SELECT full_name, email, national_id, mobile FROM "{source_pg.schema}".logscan LIMIT 50')
    secrets = [v for r in rows for v in r.values() if v] + [sub.key_material["dek"], handler_ctx.settings.job_kek,
                                                           "dps_reader", "dps_writer"]
    assert not [s for s in secrets if s in captured]
    for line in captured.splitlines():
        if line.startswith("{"):
            assert set(json.loads(line)) <= _SAFE_FIELDS
```
(`run_spec(..., source={...})` merges extra source fields — extend the Task 22 helper with a `source` keyword in this task. The connection passwords equal the role names locally, so the role names stand in for "a password" in the scan.)

- [ ] **Step 2: Run it** — `pytest -q tests/test_no_values_in_logs_m2.py` → the static cases pass for every listed module (one per module) and the dynamic case passes with the services up. A failure names the module, line and field: add the field to `_SAFE_FIELDS` only if it carries no value; otherwise change the call site.
- [ ] **Step 3: Commit**
```bash
git add tests/test_no_values_in_logs_m2.py tests/connectors/test_run_modes.py
git commit -m "test: M2 log scan - allow-listed log fields and a full job without values in the logs" -- tests/test_no_values_in_logs_m2.py tests/connectors/test_run_modes.py
```

---

### Task 38: Documentation, handoff, milestone gates

**Files:**
- Modify: `HANDOFF.md`, `README.md`
- Create: `docs/technical/api-m2.md`

- [ ] **Step 1: `HANDOFF.md`** — sections updated: *What this repo ships* (worker and migrate roles, the M2 surface); *Build* (extras `fpe,docs,db,connectors,tabular`; console Vitest); *Run* (the compose commands below, the one-shots, `--scale worker=N`, `tools/scratch_pg.sh`); *Configuration* (every M2 setting of contract §10 with default and meaning, plus `preflight_rows`, `audit_queue_max`, `audit_flush_batch`, `audit_flush_ms`); *Ports* (8400 app, 5441 dps-db, 5442 source-pg, 9010/9011 MinIO; 9400 worker internal); *Secrets (names only)* (`DPS_JOB_KEK(_FILE)`, `DPS_PG_*_PASSWORD(_FILE)`, `DPS_S3_SECRET_ACCESS_KEY(_FILE)`, secret-ref directories `dps/src/*`, `dps/tgt/*`, `dps/s3/*`); *Probes* (app `/readyz` components `db`, `jobs`; worker `:9400/health|readyz|metrics`); *Measured throughput* (the five Task 28 numbers with date, host and commit); *Traps*: asyncpg will not coerce JSON strings into typed parameters (DPS coerces by the prepared statement's types); PostgreSQL folds `DPS_WRITE_LEDGER` to `dps_write_ledger`; the ledger is authoritative over the partition checkpoint on resume; `fpe_digits` on keys with < 6 digits falls back to `det_token` and does not fit an integer column — the snapshot suggests `keep` for such keys; created target tables copy no FKs, indexes or defaults; same-table append needs one integer key (`generate`/`offset`); a superseded snapshot still drives the jobs that were submitted with it; Homebrew clusters need `timezone=UTC`; `docker compose up --wait` fails on the exited one-shots — poll `docker compose ps`; the console uses relative assets, so nested routes rely on the app's suffix resolution; the in-place key is returned once at submission, keep it (the evidence pack holds only its `kid`); `DPS_EMBEDDED_WORKER` is for local demos only. *Last verified* line with the date and commit.
- [ ] **Step 2: `README.md`** — "Run it" gains:
```bash
docker compose --env-file docker/compose.sources.env --profile sources up -d --build   # app, worker, metadata DB, demo source, MinIO
python tools/smoke_test.py                                                               # L1 + M2 checks
python tools/demo_m2.py                                                                  # 10-table UAT copy, in place, append
```
and "Develop" gains `tools/scratch_pg.sh start all`, the markers (`db pg_source s3 slow browser`) and the gate commands.
- [ ] **Step 3: `docs/technical/api-m2.md`** — every M2 route as built (method, path, scope, request and response shapes, error codes), the error-code table (contract §5.3 codes + the additions of this plan), the job/partition state machine, the evidence pack fields, and the measured throughput.
- [ ] **Step 4: Milestone gates** (all must pass; record the summaries in the build report):
```bash
ruff check dps tests tools && ruff format --check dps tests tools && mypy dps && pytest -q
(cd frontend && npm run typecheck && npm test && npm run build)
docker compose --env-file docker/compose.sources.env --profile sources up -d --build
# poll `docker compose ps` until app/worker/dps-db/source-pg/minio are healthy and the one-shots exited 0
DPS_TEST_REQUIRE_SERVICES=1 pytest -q -m "db or pg_source or s3"
DPS_TEST_REQUIRE_SERVICES=1 pytest -q -m slow tests/connectors/test_resume_after_kill.py
python tools/smoke_test.py
python tools/demo_m2.py
DPS_TEST_REQUIRE_SERVICES=1 pytest -q tests/console/test_console_m2.py
```
Expected: every command exits 0; the integration gate reports no skips (`DPS_TEST_REQUIRE_SERVICES=1` turns a missing service into a failure); `smoke_test.py` prints `all 22 checks passed`; the demo prints twelve succeeded jobs with verification passed and zero orphans.
- [ ] **Step 5: Commit and tag**
```bash
git add HANDOFF.md README.md docs/technical/api-m2.md
git commit -m "docs: M2 handoff, run commands, API reference and measured throughput" -- HANDOFF.md README.md docs/technical/api-m2.md
git tag -a m2 -m "M2: jobs + PostgreSQL - discovery, review, four write modes, resume, evidence, console"
```
(The package version is not bumped in M2; M5's release task versions the product.)

---

## Definition of Done (M2)

M2 is done when **all** of the following hold on a clean checkout, with the evidence recorded in the build report:

1. **Code gates** — `ruff check dps tests tools`, `ruff format --check dps tests tools`, `mypy dps`, `pytest -q` (offline suite) all clean; `cd frontend && npm run typecheck && npm test && npm run build` clean.
2. **Stack** — `docker compose --env-file docker/compose.sources.env --profile sources up -d --build` builds the image (console compiled inside) and brings up `app`, `worker`, `dps-db`, `source-pg`, `minio` healthy, with `migrate`, `minio-init`, `demo-secrets`, `seed-sources` exited 0; `/readyz` shows `api-core`, `db`, `jobs` ok; the worker's `/readyz` is 200; plain `docker compose up --build` (no profile) still serves Layer 1 and the jobs API against `dps-db`.
3. **Integration gate** — `DPS_TEST_REQUIRE_SERVICES=1 pytest -q -m "db or pg_source or s3"` green with no skips (migrations, RLS on every tenant table, queue races and fencing, connections, selection against a live server, discovery, every write mode, files on MinIO, evidence, API, conformance suite).
4. **Resume after kill** — `tests/connectors/test_resume_after_kill.py` green for all six cases (table truncate+insert, table upsert, in place, append offset to a named table, append generate to the same table, files): a `SIGKILL`ed worker, a second worker finishing, target contents row-for-row equal to an uninterrupted run, ledger empty, `resumed_partitions ≥ 1` in the evidence.
5. **Smoke** — `python tools/smoke_test.py` → `all 22 checks passed` (13 Layer 1 + 9 M2).
6. **Demo outcome (spec §11)** — `python tools/demo_m2.py`: the ten `bank` tables masked into `bank_uat` under one job group with zero orphaned foreign keys, `bank_restore.contacts` masked in place, `bank.transactions` appended to `bank_perf.transactions` (synthesize, generated keys) — every job `succeeded` with an evidence pack whose verification passed.
7. **Measured throughput** — five benchmark results under `docs/technical/bench/` (table, in place, files, synthesize at 200 000 rows with one worker; table with three workers), copied into HANDOFF and `docs/technical/api-m2.md`.
8. **Browser check** — `tests/console/test_console_m2.py` green, and the screenshots compared with the mockups (07 Jobs, 06 wizard steps 1–5 incl. the confirmation dialog, 03 job detail, 10 evidence pack, 09 reports list): same section order and headings, the health strip's six tiles, the filter row, the table columns, the wizard's five steps with the right rail (guarantees + live job specification), the check lists, heat-map tiles and decision list, the four write-mode cards and the type-to-confirm dialog, the stepper/partitions/throughput/evidence tiles of the job detail, the evidence pack's sections 1–5, 7, 8 and its print layout; Vault tokens in dark and light; no horizontal scroll at 390 px; no customer value visible on any page. Expected scope differences (job groups M3, Oracle/MongoDB content M3, synthetic fidelity/privacy M5, audit log M5) are listed, nothing else differs.
9. **Safety** — the M2 log-scan test green; no customer value, key, seed, password, selection literal or key position in any log line, job row (`spec`, `progress`, `plan`, `error`), evidence pack, artefact name or API error; keys returned once and purged at every terminal state; production-tagged in-place/append writes refused without `admin`; `confirm_write` enforced.
10. **Docs** — HANDOFF, README and `docs/technical/api-m2.md` updated in the same commits as the changes; the tag `m2` on the final commit.

## Self-review against the spec (sections this plan covers)

| Spec | Requirement | Where |
|---|---|---|
| §4 Processes | `app`, `worker`, `migrate` roles from one image; `dps-db` holds tenants, keys (hashed), jobs, checkpoints, reports and the append-only audit log — never customer values | Tasks 4, 23, 27 |
| §4 Statelessness | L1 needs no DB; `/readyz` `api-core` ready with the DB down, only `jobs` degrades; job state = spec (secrets sealed or by reference), snapshot (shapes only), checkpoints, evidence; retention 30 days | Tasks 4, 7, 23, 24 |
| §4/§7 Durable queue | `FOR UPDATE SKIP LOCKED` claims, leases, heartbeats, reaper with backoff, dead-letter, per-tenant caps, LISTEN wake-ups | Tasks 8, 9, 23 |
| §5.7 Verification and evidence | leakage sweep (per row, fail closed), post-write detector sample, fidelity/privacy slots (M5), configuration hash and versions, JSON + printable HTML pack | Tasks 16, 21, 22, 35 |
| §7 Lifecycle | discover → review (accept/override) → run → evidence, through the API and the console | Tasks 17, 22, 25, 32–35 |
| §7 Selection | `where` with bound parameters, read-only `query`, `columns`, `max_rows`, `sample`; sqlglot single `SELECT`, no DML/DDL/INTO/procedures/locks; identifier validation before interpolation; Mongo operator allow-list | Tasks 12, 15 |
| §7 Write modes (D3) | `in_place` (by key, committed batches, keys never rewritten), `append` (same or named table; generate/offset/token; collision pre-check aborts), `table` (create if missing; truncate_insert / upsert), `files` (CSV/Parquet/JSONL to S3 or PVC with a manifest); `confirm_write` + `admin` on production; same checkpointing and no half-applied row | Tasks 18, 19, 20, 22, 26 |
| §7 Discovery | sample n rows (TABLESAMPLE), detectors per column, semantic type from names + values, snapshot with confidence, masked examples (shapes) and suggested method; accepted snapshot becomes the run policy and goes into the evidence | Tasks 17, 21 |
| §7 Execution | key-space partitions as durable queue rows, workers claim, read/transform/write in batches, checkpoint the last key after every committed write, idempotent writes, resume instead of restart | Tasks 13, 9, 22, 26 |
| §7 PostgreSQL notes | asyncpg, `BEGIN READ ONLY`, `statement_timeout`, keyset by PK, `COPY` for loads | Tasks 15, 19 |
| §7 Cross-system consistency | one key material across a job group, value-free group record, key check on every grouped job (three-way check is M3) | Tasks 22, 25, 28 |
| §7 Credentials | `secret_ref` by name; inline credentials sealed with `DPS_JOB_KEK` and expired; never logged; least-privilege `test-connection` (SELECT on source, no DML, CREATE/INSERT/UPDATE on target) | Tasks 2, 14, 15, 24 |
| §7 Throughput | measured with `tools/bench_connector.py`, recorded, not promised | Task 28 |
| §9 Data sources & jobs page | connect → discover → review (heat-map, accept/override) → target → run; job detail with partition progress, throughput sparkline, checkpoints, evidence pack | Tasks 29–35 |
| §10 Security | tenant RLS on every job table, scopes (`jobs`, `admin` for production writes and tags), sealed secrets and purge, posture checks (KEK from file, inline off, RLS, verify mode, strict audit, TLS to PG), append-only partitioned audit, evidence hashes, retention | Tasks 1, 4, 5, 10, 14, 23, 24 |
| §11 M2 | durable queue + workers, discovery, review, run, all four write modes with confirmation, checkpoints, evidence pack, console pages, benchmark; resume-after-kill for every write mode; measured rows/s; the 10-table demo | all tasks; DoD 4, 6, 7 |
| D2 | the DEK is returned (optionally wrapped) once at submission; DPS holds it sealed only while the job runs and purges it at the terminal state; groups store only `kid` + seed fingerprint | Tasks 9, 22, 25 |
| D3 | four write modes; in place and append behind `confirm_write` and `admin` on production-tagged connections | Tasks 18, 25, 34 |

Not in M2 by design: Oracle and MongoDB connectors and their pitfalls (M3, against this plan's conformance suite and Mongo filter validator), the three-way consistency check and the job-group console panels (M3), the copula / MOSTLY AI tabular engines and fidelity/privacy metrics (M5; `options.tabular` is refused), the evidence PDF, the audit log UI, admin and DB-backed API keys (M5), the UI scanner (M4).

## Open contract questions

The plan follows the contract everywhere; these are the points where it **adds** something or where the contract (or a mockup) seems wrong. None changes a contract name, route, column or port.

1. **`dps_tenant` is treated as a global table (no RLS).** The contract gives it `tenant_id` but does not mark it GLOBAL; with FORCE RLS the cross-tenant claim queries could not read `max_running`, and M5's admin routes could not list tenants. 002 also lets a tenant id start with `_` so the internal `_system` tenant (retention jobs) can have a row.
2. **Per-row verification instead of per-batch** (contract §6.4). M0's `verify_output` over a whole batch flags a 4-character original inside another row's realistic surrogate or 12-digit FPE output; at L2 volumes strict mode would fail healthy jobs. The plan verifies each row against its own outputs: exact fixed points for originals ≥ 4 characters, cross-field containment for ≥ 8.
3. **`Key`/`KeyRange` typing.** §4.1 says `Key = list[JsonScalar]` but `encode_key` returns `list[dict[str, str]]` and partitions store the encoded form. The plan uses `EncodedKey = list[dict[str, str]]` for `KeyRange` and the persisted fields, and driver tuples in memory.
4. **In-place through a separate write connection.** Mockup 06 picks a read-write connection (`crm-prod-rw`) next to the read-only source; the contract makes `in_place` with a different target connection `in_place_invalid`. The plan follows the contract (the source connection needs both roles). Suggestion: allow a target connection whose host/port/database equal the source's.
5. **Snapshot methods per mode.** `DiscoverSpec` has no mode, but the wizard chooses obfuscate/synthesize after review (mockup 06 step 5). The plan stores both per column (`suggested`/`method` and the added `synthesize_suggested`/`synthesize_method`, overridable through `SnapshotOverride.synthesize_method`); `DiscoverSpec.mode` is an optional hint.
6. **When the key is shown.** Mockup 06 says the key is "shown once when the job finishes"; contract §6.1 returns it once in the `POST /jobs/run` response. The plan follows the contract; the console copy says "shown once now".
7. **Checkpoint display.** Mockup 03 prints the last six digits of a key (`…884120`); the contract treats keys as customer data. `PartitionView` exposes an 8-hex fingerprint of the checkpoint instead.
8. **Same-table append restrictions** (added validation rule): one integer key with `generate` (sequence ahead of the table) or `offset`, never `token` or `prefix` — otherwise appended rows can land inside unread key ranges and be read and appended again.
9. **Protocol additions** (defaulted, M3 implements them unchanged): `Connector.key_at_offset`, `Connector.sample_keys(..., upper=None)`, `BatchWriter.clear_ledger`, `BatchWriter.finish_partition` returning part metadata, `LedgerMark.rows`, `PrepareReport.append_upper`, `ResolvedTarget` living in `base.py`. `PartitionHandler` takes a `JobInternal` (the partition's job is not leased); `ClaimedJob` is its subclass.
10. **Queue and worker API additions:** `Consumer(..., ctx=HandlerContext, role=…, worker_id=…)` (the contract's constructor has no context argument, but handlers need one), `create_job(job_id=…)` (the AAD of the sealed blobs needs the id first), `advance_job(plan=…, delay_seconds=…)`, `add_partitions(plan=…, progress=…)` performing the whole `prepare → partitions/waiting` transition atomically, `finish_partition(meta=…)`, and the new functions `retry_job`, `retry_partition`, `reset_partition_progress`, `abandon_partition`, `partition_control`, `load_job_internal`, `tenant_stats`, `sample_throughput`, `register_worker`/`worker_heartbeat`/`unregister_worker`, `ensure_retention_job`.
11. **DDL additions** (in M2's own files, not yet applied anywhere): `dps_job.plan jsonb`, `dps_job_partition.meta jsonb`, indexes `dps_part_job`, `dps_job_expires`, `dps_group_feed`, `dps_connection_feed`, `dps_snapshot_feed`, `dps_artefact_job`, `dps_evidence_feed`, `dps_evidence_job`, `dps_audit_tenant_ts`, the helper `dps_jsonb_add` + aggregate `dps_jsonb_sum`, and `dps_connection_one_secret` (at most one of `secret_ref`/`secret_enc`, since retention nulls expired blobs).
12. **Stricter selection deny-list:** `set_config` (could lift the statement timeout inside the read-only transaction), `pg_terminate_backend`, `pg_cancel_backend`, `pg_advisory*`, `pg_reload_conf`, `pg_rotate_logfile`, `pg_promote`, `pg_ls_dir`, `pg_stat_file`, `lo_import`, `lo_export`; positional `$n` binds refused.
13. **Additional run-validation codes:** `key_method_not_injective`, `method_needs_key`, `text_method_invalid`, `engine_unavailable`, `wrap_key_invalid`, `key_material_invalid`, `group_name_taken`, `key_material_mismatch` (retry), and prepare-time `snapshot_stale`, `target_invalid`; API codes `connection_not_found`, `connection_name_taken`, `connection_in_use`, `inline_secrets_disabled`, `job_not_found`, `job_terminal`, `job_finalizing`, `job_not_pausable`, `job_not_paused`, `job_not_retryable`, `snapshot_not_proposed`, `snapshot_conflict`, `override_unknown_path`, `override_method_required`, `evidence_not_found`, `db_unavailable`, `scope_required`, `ledger_conflict`, `statement_timeout`, `object_not_found`, `engine_not_tabular`.
14. **Synthesize-mode key rule.** Surrogates are not injective, so key and join columns (`CUSTOMER_ID`, `EMPLOYEE_ID`, `CASE_ID`) keep their values in synthesize mode by default (referential integrity of synthetic copies); M5's copula adds key uniqueness. Owners can override per column in review.
15. **Counter keys.** `values_by_method` carries methods plus `fallback:<note>` and `col:<column>` (and `retention:*` for retention jobs) so per-column evidence counts and fallbacks need no extra column; `JobProgress` gains `stage` and `throughput_mark`; `EvidencePack` gains `label` (required by §7.2 but not listed in §7.1) and `mode`; `verification.post_sample` gains `kept_column_hits`.
16. **Settings additions:** `preflight_rows`, `audit_queue_max`, `audit_flush_batch`, `audit_flush_ms`; `worker_metrics_port = 0` disables the worker's server; posture refuses `embedded_worker` in staging/prod and a literal `DPS_JOB_KEK` env var (must be `_FILE`); M2 posture checks apply only when `pg_host` is set, so Layer 1-only deployments keep passing.
17. **Compose additions:** one-shots `demo-secrets` (writes the demo secret-ref files into a volume instead of committing password files) and `seed-sources`; `docker/compose.sources.env` selects the S3 artefact store when the `sources` profile runs (compose cannot switch env by profile). If the cached `minio/minio` image has no `curl`, its healthcheck becomes `mc ready local`.
18. **`dps_audit_log` rows for unknown tenants** (401s before a tenant is known) are written with tenant `-`; the contract's NOT NULL `tenant_id` needs some value.
19. **M0 `_anchor_for`** is `anchor_for` (public) since M0 Task 22; the plan uses the public name. M0's `_dictionary` used Python's per-process-randomised `hash()` without a key; M2 makes it deterministic (L2 runs one job across processes).
20. **`tools/reveal_rows.py`** (named in contract §6.2 but not in §1's M2 list) is delivered in M2 with `dps.engine.batch.reveal_row`, so in-place obfuscation is demonstrably reversible by the key holder.

## Execution

Plan complete and saved to `docs/superpowers/plans/2026-10-11-m2-jobs-and-postgres.md`. Two execution options:

1. **Subagent-driven (recommended)** — a fresh subagent per task, review between tasks (`superpowers:subagent-driven-development`). Tasks 1–28 are backend and must run in order; 29–35 (console) can start once Task 25's routes exist and run in parallel with Tasks 26–28; 36–38 close the milestone.
2. **Inline execution** — tasks executed in one session with checkpoints (`superpowers:executing-plans`).
