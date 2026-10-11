# DPS M3 — Oracle + MongoDB connectors, cross-system job groups — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship milestone M3 of Data Privacy Services: an Oracle connector and a MongoDB connector that pass
the M2 connector conformance suite on real containers, the three measured Oracle pitfalls carried in as
tests, Oracle Free and MongoDB in the local stack with demo data whose customer ids are shared with the M2
PostgreSQL bank schema, job groups that protect one customer consistently across the three systems with one
caller-held key, and a cross-system consistency check (a three-way join over the masked outputs) with its
console panel on the Jobs page (mockup 07) and the job detail page (mockup 03).

**Architecture:** Two new `Connector`/`BatchWriter` implementations behind M2's `open_connector()` registry —
nothing else in the job pipeline changes. `dps/layers/connectors/oracle.py` drives python-oracledb in thin
mode through a synchronous pool used only from worker threads (`asyncio.to_thread`), with the Wega pool
rules (`expire_time=1`, `call_timeout`, `tcp_connect_timeout`, `module/action/client_identifier`), keyset
reads that never lock, a closed Oracle type table in `oracle_sql.py` (pure, unit-tested SQL builders),
`MERGE` upserts, `executemany` loads, in-place `UPDATE` by key, ROWID-range partitions for key-less
tables, and an in-target write ledger whose guarded `MERGE` also fences zombie writers.
`dps/layers/connectors/mongo.py` uses pymongo's `AsyncMongoClient` (contract §1: Motor is deprecated),
infers a path shape (`$.contact.addresses[*].line1`) from `$sample`, pages on `_id`, writes with
`bulk_write(ordered=False)`, and runs `in_place`/`append` inside replica-set transactions together with
the ledger. `dps/layers/connectors/consistency.py` samples join values from one member's *target*,
looks them up in the other members' targets (format-aware: `73104482` joins `CUST-73104482`), compares the
other shared entity columns of the joined rows, and returns counts only.

**Tech Stack:** Python 3.12, python-oracledb 26.0.0 (thin; the version Wega measured), pymongo ≥ 4.13 (<5;
4.17 is cached locally) with `bson`, FastAPI, pydantic v2, asyncpg (M2 metadata DB), sqlglot (M2
selection parsing); Oracle Database Free (`gvenzl/oracle-free:23-slim-faststart`, which reports 23.26/26ai),
MongoDB 7 as a single-node replica set; React 18 + TypeScript (console); pytest with the `oracle`, `mongo`,
`integration`, `slow` markers.

**Spec:** `docs/specs/2026-10-11-data-privacy-services-design.md` §7 (connectors, write modes, engine notes,
cross-system consistency, credentials), §9 (console: Data sources & jobs, job groups), §10, §11 (M3 row).
**Binding contract:** `docs/superpowers/plans/2026-10-11-m1-m5-contract.md` (names, DDL, ports, routes,
settings). **Builds on:** M0 (engine, `PrivacyEngine`, `apply_plan`, `hash_value`, surrogates, console shell)
and M2 (metadata DB, queue, worker, `Connector`/`BatchWriter` protocols, `BatchTransformer`, discovery,
partitions, targets, PG connector, job-group routes, console pages 06/07/03/10).

**Contract changes made by this plan (additive only; each is applied to the contract file in the same
commit as the code that needs it, and listed again in "Open contract questions"):**
- §4.1 `ColumnInfo` gains `pseudo: bool = False` (Task 3) — a read-only key column that is not table data
  (Oracle `ROWID` for key-less tables): never classified as sensitive, never written.
- §4.2 `PrepareReport` gains `append_upper_key: EncodedKey | None = None` (Task 12) — the exclusive read
  bound of a same-table append whose key is not an integer (Mongo ObjectId), next to M2's `append_upper`.
- §5.3 prepare-time failure codes gain `column_unsupported`, `target_missing`, `ledger_conflict`
  (run-time), `path_unsupported` (Mongo in-place on a field name that cannot be dot-addressed).
- §9 `POST /job-groups/{id}/consistency-check` body gains optional `min_coverage` and `attributes`, and
  `pairs` becomes optional (derived from the members' accepted snapshots when omitted); the response shape is
  fixed in Task 16. `GET /job-groups/{id}` gains `last_check` and `last_check_at`.
- §2.2 migration `020_job_group_last_check.sql` (M3 range) adds `last_check jsonb`, `last_check_at
  timestamptz` to `dps_job_group`.
- §11 the `oracle` service mounts `docker/oracle/initdb` at `/container-entrypoint-initdb.d`; app and
  worker mount `docker/secrets/local` at `/var/run/secrets/dps/local` (demo secret refs, local only).

---

## Conventions for every task

- Work in `/Users/neelu/document_intelligence/data-privacy-services` (its own git repo, branch `main`). Never
  run git against the parent `~/document_intelligence` repo.
- Environment: `source .venv/bin/activate && uv pip install -e ".[dev,fpe,db,connectors]"` (Task 1 adds
  `oracledb` and `pymongo` to `connectors`).
- **Gates per task (contract §0.4):** `ruff check dps tests tools && ruff format --check dps tests tools &&
  mypy dps && pytest -q`; when `frontend/` changes, `cd frontend && npm run typecheck && npm run build`.
  Integration tests are deselected by default only because their services are absent — they *skip* with a
  start hint; with `DPS_TEST_REQUIRE_SERVICES=1` they *fail* instead. Each task that adds integration tests
  also runs them against the stack: `docker compose --profile oracle --profile sources up -d` then
  `DPS_TEST_REQUIRE_SERVICES=1 pytest -q -m "<markers>" <files>`.
- Type hints everywhere, Google-style docstrings on public callables, no `Any` unless a driver value is
  genuinely untyped, line length 100.
- **Never log, print, put in an exception message, a metric label, an audit row, a job row or an evidence
  pack: a customer value, key, DEK, seed, token, password, DSN with credentials, Mongo URI, or a driver
  error message** (driver messages quote values: Mongo `E11000 … dup key: { … }`). Driver errors become
  `ConnectorError(code, <driver code>)` built from the driver's *code* only and raised `from None`, so the
  driver's message is not even in the traceback context.
- **Git:** the global identity (NilenduGanguli) authors every commit; **no `Co-Authored-By` or any other
  trailer**; conventional commits; stage and commit only the task's paths with an explicit pathspec, exactly
  `git add <paths> && git commit -m "<msg>" -- <paths>`; never `git add -A/.`, `commit -a`, `stash`,
  `reset`, `checkout --`, `clean`. If `.git/index.lock` exists, wait 3 s and retry (up to 10 times).
- `HANDOFF.md` changes ride in the same commit as any build/run/config change (contract §0.6).
- Deviations from this plan are logged per task in `docs/superpowers/build/M3-DEVIATIONS.md` (same format
  as the M0 log: file, what changed, why).

## Seams with M2 (read before Task 1)

M3 is written against the contract's names and against the M2 plan
(`docs/superpowers/plans/2026-10-11-m2-jobs-and-postgres.md`, written in parallel; its Tasks 6, 7, 12, 14,
15 and 16 fix the interfaces below). Where M2 has not fixed a name yet (targets, run handlers, group routes,
conformance harness, console pages), M3 states what it needs; Task 1 records what M2 actually built and M3
adapts **only its own call sites** (never M2 behaviour) when a name differs.

| Need | Name M3 uses (source) | Notes |
|---|---|---|
| connector types | `base.py`: `ObjectRef` (owner/name `^[A-Za-z_][A-Za-z0-9_$#]{0,127}$`), `ColumnInfo`, `ColumnKind`, `TableShape` (+ `key_columns()`), `KeyRange` (**encoded** `lower`/`upper`), `EncodedKey`, `encode_key`, `decode_key` (ts tag with `ts_precision > 6` stays text), `to_engine_text`, `from_engine_text`, `LedgerMark` (+ `rows`), `ConnectionTestResult`, `PrepareReport(created, truncated, ledger, existing_rows, checks, append_upper)`, `ConnectorError(code, detail="", *, retryable=False)`, `IncompatibleValue`, `UnsupportedKey` (M2 Task 6) | M3 puts the driver *code* (`ORA-00001`, `mongo:11000`) in `detail`, stores it as `ConnectorError.detail` (one-line M2 touch if the attribute is missing), and adds `PrepareReport.append_upper_key: EncodedKey \| None` for non-integer same-table append bounds (Task 12) |
| protocols | `Connector` (+ `key_at_offset`, `sample_keys(..., *, upper=None)`), `BatchWriter` (+ `clear_ledger(job_id)`, `finish_partition -> dict`), `ResolvedTarget(job_id, write_mode, connection_id, engine, object, same_as_source, create_if_missing, table_load, pk_strategy, files, ledger_owner, key, write_columns, generated_key, target_shape, column_types)` (M2 Task 7) | rows reach `write_batch` with offset/token keys already rewritten by M2; for `generate` the writer omits the key (`generated_key`) |
| connections | `dps.connections`: `Connection`, `Credentials(username, password: SecretStr, extra: dict[str, SecretStr])`, `load_credentials`, `get_connection` (M2 Task 14) | Oracle `dsn` and Mongo `uri` secret files arrive in `Credentials.extra` |
| registry | `open_connector(conn, settings, *, action, kek, job_id=None, credentials=None)` returns an **opened** connector; M2 raises `engine_unavailable` for `oracle`/`mongo` until M3 replaces that branch (M2 Task 14) | `job_id` becomes Oracle `client_identifier` / Mongo `comment` |
| selection | `selection.py`: `Selection`, `SampleSpec`, `SelectionError(code, message)`, `quote_ident`, `validate_selection`, `compile_where`, `compile_query` (literals already parameterised; binds `:name` for Oracle), `validate_mongo_filter`, `mongo_filter_template`, `MONGO_OPERATORS`, codes `selection_operator_denied`, `selection_identifier_invalid`, `selection_engine_mismatch`, `selection_param_missing`, `selection_query_missing_key` (M2 Task 12) | M3 hardens `validate_mongo_filter` and adds `compile_mongo_filter` (Task 10) |
| engine bridge | `batch.py`: `ColumnPlan(path, entity_type, method, text_column, reversible, kind, length, name, precision, scale)`, `BatchTransformer(..., anchor=None, verify="strict")` (M2 Task 16) | pinned on real Oracle/Mongo rows by Task 13: untransformed values pass through untouched, `[*]` paths expand |
| discovery | `discovery.py` (`discover_job`, M2 Task 17 — not yet written) | M3 routes per-column value reads through `docpaths.column_values()` and forces `keep` for `docpaths.forced_keep()` columns (Task 8) |
| targets / handlers / groups | `targets.py`, `jobs_run.py`, `POST/GET /job-groups` (M2 Tasks 18, 22, 25 — not yet written) | assumed: `POST /job-groups {name}` → `{group, key_material, synthesis_seed}`; the group model `JobGroup` (M3 adds `last_check`, `last_check_at`); finalize calls `writer.clear_ledger(job.id)`; the run handler bounds same-table append reads with `append_upper` (and, from M3, `append_upper_key`) |
| fixtures | `db_settings`, `migrated_db`, `api_client`, `source_pg`, `kek` (contract §12.2; M2 `tests/fixtures/sources.py`) | M3 adds `oracle_admin`, `oracle_schema`, `oracle_conn`, `mongo_client`, `mongo_db` in `tests/fixtures/m3_services.py` |
| demo PG data | `bank.customers` (`customer_id bigint` from 10000001, `full_name`, `email`, `mobile`, `national_id`, `tax_id char(10)`, …); table-mode targets in schema `bank_uat`; seed marker `bank._dps_seed_info` (M2 Task 15) | the M3 seed model produces exactly that id space |
| conformance | `tests/connectors/conformance.py` (M2 Task 26 — not yet written) | M3 registers `oracle` and `mongo` harnesses (Task 14) |
| console | `Jobs.tsx` (07), `JobDetail.tsx` (03), `JobWizard.tsx` (06), `api.groups.*`, `api.jobs.*`, `api.snapshots.*`, `api.connections.*`, Vitest for helpers (M2 Tasks 29–34) | M3 adds `api.groups.consistencyCheck` and Vitest cases for `lib/groups.ts` |

## File structure

| Path | Kind | Responsibility |
|---|---|---|
| `pyproject.toml` | modify | `connectors` extra += `oracledb==26.0.0`, `pymongo>=4.13,<5`; markers `oracle`, `mongo` |
| `dps/config.py`, `.env.example` | modify | `oracle_pool_max`, `oracle_call_timeout_ms`, `oracle_module`, `mongo_server_selection_timeout_ms` |
| `dps/observability.py` | modify | allow-listed log fields for connectors; `dps_connector_calls_total`, `dps_consistency_checks_total` |
| `dps/layers/connectors/oracle_sql.py` | create | pure Oracle rules: type table, select/bind expressions, keyset predicates, statements, DDL, ROWID codec |
| `dps/layers/connectors/oracle.py` | create | `OraclePool` (Wega rules), `OracleConnector`, `OracleWriter` |
| `dps/layers/connectors/docpaths.py` | create | wildcard paths: `generalise`, `expand_wildcards`, `values_at`, `dotted`, `leaf_updates` |
| `dps/layers/connectors/mongo_docs.py` | create | BSON type labels, `infer_shape`, `coerce_bson`, `_id` helpers |
| `dps/layers/connectors/mongo.py` | create | `MongoConnector`, `MongoWriter` (pymongo `AsyncMongoClient`) |
| `dps/layers/connectors/consistency.py` | create | join forms, sampling, lookups, attribute agreement, `run_consistency_check` |
| `dps/layers/connectors/selection.py` | modify (M2) | Mongo filter allow-list validator + compiler (EJSON literals) |
| `dps/layers/connectors/base.py` | modify (M2) | `ColumnInfo.pseudo`; `oid` and TIMESTAMP(7-9) key codec rules |
| `dps/layers/connectors/registry.py` | modify (M2) | `oracle` and `mongo` branches (replacing M2's `engine_unavailable`) |
| `dps/layers/connectors/discovery.py` | modify (M2) | wildcard-aware column values; pseudo columns forced `keep` |
| `dps/layers/connectors/postgres.py` | modify (M2) | `lookup_values()` for the consistency check |
| `dps/migrations/020_job_group_last_check.sql` | create | `last_check`, `last_check_at` on `dps_job_group` |
| `dps/layers/api/routers/groups.py` | modify (M2) | `POST /job-groups/{id}/consistency-check`; `last_check` on reads |
| `docker-compose.yml` | modify | `oracle` (profile `oracle`), `mongo` (profile `sources`); secret mounts |
| `docker/oracle/initdb/01_crm.sql` | create | users `CRM`, `CRM_MASKED`, `LOANS`, `DPS_RO`; `CRM.CUSTOMERS` and friends; grants |
| `docker/secrets/local/{oracle-crm-ro,oracle-crm-owner,oracle-crm-masked,mongo-crm,mongo-crm-uat}/*` | create | demo secret refs (local literals only) |
| `tools/seed_model.py` | create | `SeedCustomer`, `seed_customers(rows, seed)` — the one customer space for all three engines |
| `tools/seed_oracle.py`, `tools/seed_mongo.py` | create | idempotent seeding of Oracle and Mongo demo data |
| `tools/seed_sources.py` | modify (M2) | `--target oracle\|mongo\|all`; PG customers drawn from `seed_model` |
| `tools/bench_connector.py`, `tools/smoke_test.py` | modify | `--engine oracle\|mongo`; M3 smoke checks |
| `frontend/src/lib/{types,api}.ts` | modify | group check types; `api.groups.consistencyCheck` |
| `frontend/src/components/groups/*.tsx`, `frontend/src/hooks/useJobGroup.ts` | create | group card, verdict, coverage, new-group dialog, data hook |
| `frontend/src/pages/{Jobs,JobDetail,JobWizard}.tsx`, `frontend/src/styles.css` | modify (M2) | group section + badge, group header + joins box, `?group=` preselect, Vault styles |
| `tests/fixtures/m3_services.py`, `tests/conftest.py` | create / modify | Oracle per-session schemas, Mongo per-test databases, service probes |
| `tests/connectors/test_oracle_*.py`, `test_mongo_*.py`, `test_docpaths.py`, `test_selection_mongo.py`, `test_selection_oracle.py`, `test_engine_bridge_m3.py`, `test_consistency.py`, `harness_oracle.py`, `harness_mongo.py` | create | unit + integration tests, conformance harnesses |
| `tests/api/test_groups_consistency_api.py`, `tests/e2e/test_m3_three_way.py`, `tests/e2e/test_m3_resume_after_kill.py`, `tests/test_seed_model.py`, `tests/test_no_values_in_logs_m3.py` | create | route, end-to-end, resume-after-kill, seed, log-scan |
| `HANDOFF.md`, `README.md`, `docs/technical/api-m3.md`, `docs/superpowers/build/M3-DEVIATIONS.md` | modify / create | build/run facts, API as built, deviations |

Tasks 3–7 (Oracle) and 9–12 (Mongo) are independent lanes after Task 2; Task 13 joins them.

---
### Task 1: Seam audit, dependencies, settings, markers, service fixtures

**Files:**
- Create: `docs/superpowers/build/M3-DEVIATIONS.md`, `tests/fixtures/__init__.py`, `tests/fixtures/m3_services.py`, `tests/test_config_m3.py`
- Modify: `pyproject.toml`, `dps/config.py`, `.env.example`, `tests/conftest.py`, `dps/observability.py`, `tests/test_observability.py`

**Interfaces introduced:**

```python
# dps/config.py — Settings (contract §10, M3 rows)
oracle_pool_max: int = 4                      # DPS_ORACLE_POOL_MAX; sessions per Oracle connection pool
oracle_call_timeout_ms: int = 30000           # DPS_ORACLE_CALL_TIMEOUT_MS; per round trip (Wega rule)
oracle_module: str = "dps"                    # DPS_ORACLE_MODULE; v$session.module of every DPS session
mongo_server_selection_timeout_ms: int = 5000 # DPS_MONGO_SERVER_SELECTION_TIMEOUT_MS

# tests/fixtures/m3_services.py
@dataclass(frozen=True)
class OracleTestSchema:
    dsn: str                 # host:port/service
    owner: str               # DPS_T_<6 hex>   (CREATE TABLE/SEQUENCE, quota) — writers and fixtures
    owner_password: str
    ro_user: str             # DPS_T_<6 hex>_RO (CREATE SESSION + per-table SELECT grants) — sources
    ro_password: str
    def grant_select(self, admin: oracledb.Connection, table: str) -> None
def oracle_dsn() -> str                                   # DPS_TEST_ORACLE_DSN or 127.0.0.1:1522/FREEPDB1
def mongo_uri() -> str                                    # DPS_TEST_MONGO_URI or mongodb://127.0.0.1:27018/?directConnection=true
@pytest.fixture(scope="session") def oracle_admin() -> Iterator[oracledb.Connection]      # SYSTEM, only to create/drop test users
@pytest.fixture(scope="session") def oracle_schema(oracle_admin) -> Iterator[OracleTestSchema]
@pytest.fixture def oracle_conn(oracle_schema) -> Iterator[oracledb.Connection]           # owner session, autocommit off
@pytest.fixture(scope="session") def mongo_client() -> Iterator[pymongo.MongoClient]     # sync, setup/assertions only
@pytest.fixture def mongo_db(mongo_client) -> Iterator[str]                               # dps_t_<8 hex>, dropped after the test
```

**Behaviours:** the four settings validate their ranges and refuse bad values without echoing them; the
fixtures probe TCP for 2 s, then `pytest.skip("start: docker compose --profile oracle up -d oracle")`
(or `--profile sources up -d mongo`) — or `pytest.fail` when `DPS_TEST_REQUIRE_SERVICES=1`, after waiting
up to 180 s for Oracle to accept a session (Oracle Free opens its PDB after the listener). Tests never run
the code under test as SYSTEM: SYSTEM only creates a per-session owner and a read-only user and drops them
(`DROP USER … CASCADE`) at session end, so parallel runs never share objects (Wega lessons 4 and 5).

- [ ] **Step 1: Seam audit (no code).** Read, in the M2 tree as built: `dps/layers/connectors/{base,selection,
  targets,registry,discovery,partitions,jobs_run,postgres}.py`, `dps/engine/batch.py`,
  `dps/layers/api/routers/groups.py`, `dps/jobs_models.py`, `tests/conftest.py`,
  `tests/connectors/conformance.py`, `frontend/src/lib/{api,types}.ts`, `frontend/src/pages/{Jobs,JobDetail,
  JobWizard}.tsx`. For every row of "Seams with M2" write one line in a new
  `docs/superpowers/build/M3-DEVIATIONS.md` section `## Task 1 — seam audit`: the actual name/signature, and
  `same` or the difference. Where a non-contract assumption does not hold, note which M3 call sites change
  (the M3 code in later tasks is adapted at those call sites only).

- [ ] **Step 2: Write the failing tests**

`tests/test_config_m3.py`:
```python
from __future__ import annotations

import pytest

from dps.config import Settings


def test_m3_defaults() -> None:
    s = Settings(_env_file=None)
    assert (s.oracle_pool_max, s.oracle_call_timeout_ms, s.oracle_module) == (4, 30000, "dps")
    assert s.mongo_server_selection_timeout_ms == 5000


@pytest.mark.parametrize(
    "field,value",
    [("oracle_pool_max", 0), ("oracle_pool_max", 65), ("oracle_call_timeout_ms", 999),
     ("oracle_module", "dps module"), ("oracle_module", "x" * 49),
     ("mongo_server_selection_timeout_ms", 100)],
)
def test_m3_bad_values_are_refused_without_echo(field: str, value: object) -> None:
    with pytest.raises(ValueError) as info:
        Settings(_env_file=None, **{field: value})
    assert repr(value) not in str(info.value)


def test_m3_env_names(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DPS_ORACLE_POOL_MAX", "2")
    monkeypatch.setenv("DPS_ORACLE_MODULE", "dps-uat")
    s = Settings(_env_file=None)
    assert s.oracle_pool_max == 2 and s.oracle_module == "dps-uat"
```

Append to `tests/test_observability.py`:
```python
def test_m3_connector_fields_are_allow_listed() -> None:
    event = {"event": "oracle.read", "engine": "oracle", "op": "read", "job_id": "j_1", "partition_no": 3,
             "batch_no": 4, "rows": 1000, "driver_code": "ORA-00942", "group_id": "g_1",
             "value": "secret", "dsn": "u/p@h", "uri": "mongodb://u:p@h"}
    out = _drop_value_fields(None, "info", dict(event))
    assert "value" not in out and "dsn" not in out and "uri" not in out
    assert out["driver_code"] == "ORA-00942" and out["rows"] == 1000 and out["engine"] == "oracle"
```

- [ ] **Step 3: Run to confirm failure**

Run: `pytest -q tests/test_config_m3.py tests/test_observability.py`
Expected: FAIL — `AttributeError: 'Settings' object has no attribute 'oracle_pool_max'` and the new
observability test fails on `driver_code`/`rows` being dropped (fields not yet allow-listed; any of them
already added by M2 simply pass).

- [ ] **Step 4: Implement**

`pyproject.toml` — in `[project.optional-dependencies]` extend `connectors` (M2 created it with `sqlglot`,
`pyarrow`, `boto3`) and add the markers (keep M0/M2 entries):
```toml
connectors = [
    # ... M2 entries stay ...
    "oracledb==26.0.0",        # thin mode; the version Wega measured the Oracle pitfalls on
    "pymongo>=4.13,<5",        # AsyncMongoClient (Motor is deprecated); bson ships with it
]
# [tool.pytest.ini_options] markers += 
    "oracle: needs the oracle container (docker compose --profile oracle up -d oracle)",
    "mongo: needs the mongo container (docker compose --profile sources up -d mongo)",
```
Then: `uv pip install -e ".[dev,fpe,db,connectors]"` and `python -c "import oracledb, pymongo; print(oracledb.__version__, pymongo.version)"` → `26.0.0 4.17.0` (or another ≥ 4.13).

`dps/config.py` — add after the M2 connector fields:
```python
    # --- M3 connectors: Oracle and MongoDB ------------------------------------------------------
    oracle_pool_max: int = 4
    oracle_call_timeout_ms: int = 30000
    oracle_module: str = "dps"
    mongo_server_selection_timeout_ms: int = 5000

    @field_validator("oracle_pool_max")
    @classmethod
    def _pool_max(cls, v: int) -> int:
        if not 1 <= v <= 64:
            raise ValueError("oracle_pool_max must be between 1 and 64")
        return v

    @field_validator("oracle_call_timeout_ms")
    @classmethod
    def _call_timeout(cls, v: int) -> int:
        if not 1_000 <= v <= 3_600_000:
            raise ValueError("oracle_call_timeout_ms must be between 1000 and 3600000")
        return v

    @field_validator("oracle_module")
    @classmethod
    def _module(cls, v: str) -> str:
        # v$session.module holds 64 bytes; the DBA's audit filters on it, so keep it a plain token.
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,48}", v):
            raise ValueError("oracle_module must be 1-48 characters of [A-Za-z0-9_.-]")
        return v

    @field_validator("mongo_server_selection_timeout_ms")
    @classmethod
    def _mongo_sel(cls, v: int) -> int:
        if not 500 <= v <= 120_000:
            raise ValueError("mongo_server_selection_timeout_ms must be between 500 and 120000")
        return v
```
(add `import re` at the top). `.env.example` gains the four `DPS_` lines with their defaults and a
comment that Oracle/Mongo credentials never go in env vars — they come from `secret_ref` files.

`dps/observability.py` — extend `_SAFE_FIELDS` with each of these that is not already present (M2 may
have added some): `"engine", "op", "job_id", "group_id", "partition_no", "batch_no", "rows",
"driver_code", "object_ref", "members", "pairs", "coverage", "passed", "sampled"` — every one is an id,
a count, a table name or a driver *code*. Add the counters:
```python
CONNECTOR_CALLS = Counter(
    "dps_connector_calls_total", "Connector driver calls by engine, operation and outcome",
    ["engine", "op", "outcome"],
)
CONSISTENCY_CHECKS = Counter(
    "dps_consistency_checks_total", "Cross-system consistency checks by outcome", ["outcome"]
)
```
(skip `CONNECTOR_CALLS` if M2 already defines a counter with the same name and labels; reuse it).

`tests/fixtures/__init__.py`: empty. `tests/fixtures/m3_services.py`:
```python
"""Oracle and MongoDB fixtures for M3 integration tests (contract §12.2 names).

SYSTEM is used only to create and drop a per-session owner and a read-only user. Code under test never
connects as SYSTEM, so a missing grant fails here exactly as it would in a bank's least-privilege setup.
"""
from __future__ import annotations

import os
import secrets
import socket
import time
from collections.abc import Iterator
from dataclasses import dataclass

import pytest

oracledb = pytest.importorskip("oracledb")
pymongo = pytest.importorskip("pymongo")

_REQUIRE = os.environ.get("DPS_TEST_REQUIRE_SERVICES") == "1"


def oracle_dsn() -> str:
    return os.environ.get("DPS_TEST_ORACLE_DSN", "127.0.0.1:1522/FREEPDB1")


def mongo_uri() -> str:
    return os.environ.get("DPS_TEST_MONGO_URI", "mongodb://127.0.0.1:27018/?directConnection=true")


def _tcp_ok(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _unavailable(hint: str) -> None:
    if _REQUIRE:
        pytest.fail(f"service required but unreachable — {hint}")
    pytest.skip(hint)


@dataclass(frozen=True)
class OracleTestSchema:
    dsn: str
    owner: str
    owner_password: str
    ro_user: str
    ro_password: str

    def grant_select(self, admin: "oracledb.Connection", table: str) -> None:
        with admin.cursor() as cur:
            cur.execute(f'GRANT SELECT ON "{self.owner}"."{table.upper()}" TO "{self.ro_user}"')


@pytest.fixture(scope="session")
def oracle_admin() -> Iterator["oracledb.Connection"]:
    dsn = oracle_dsn()
    host, _, rest = dsn.partition(":")
    port = int(rest.split("/")[0])
    if not _tcp_ok(host, port):
        _unavailable("start: docker compose --profile oracle up -d oracle")
    user = os.environ.get("DPS_TEST_ORACLE_USER", "system")
    password = os.environ.get("DPS_TEST_ORACLE_PASSWORD", "dps_local_oracle")
    deadline = time.monotonic() + (180 if _REQUIRE else 5)
    while True:
        try:
            conn = oracledb.connect(user=user, password=password, dsn=dsn, tcp_connect_timeout=10)
            break
        except oracledb.Error:
            if time.monotonic() > deadline:
                _unavailable("oracle listener is up but the PDB does not accept sessions yet")
            time.sleep(3)
    conn.autocommit = True
    yield conn
    conn.close()


@pytest.fixture(scope="session")
def oracle_schema(oracle_admin: "oracledb.Connection") -> Iterator[OracleTestSchema]:
    tag = secrets.token_hex(3).upper()
    schema = OracleTestSchema(
        dsn=oracle_dsn(), owner=f"DPS_T_{tag}", owner_password="p" + secrets.token_hex(12),
        ro_user=f"DPS_T_{tag}_RO", ro_password="p" + secrets.token_hex(12),
    )
    with oracle_admin.cursor() as cur:
        cur.execute(f'CREATE USER "{schema.owner}" IDENTIFIED BY "{schema.owner_password}" '
                    "DEFAULT TABLESPACE USERS QUOTA UNLIMITED ON USERS")
        cur.execute(f'GRANT CREATE SESSION, CREATE TABLE, CREATE SEQUENCE TO "{schema.owner}"')
        cur.execute(f'CREATE USER "{schema.ro_user}" IDENTIFIED BY "{schema.ro_password}"')
        cur.execute(f'GRANT CREATE SESSION TO "{schema.ro_user}"')
    yield schema
    for user in (schema.ro_user, schema.owner):
        for _ in range(10):
            try:
                with oracle_admin.cursor() as cur:
                    cur.execute(f'DROP USER "{user}" CASCADE')
                break
            except oracledb.DatabaseError:   # ORA-01940: a pool still holds a session — retry
                time.sleep(1)


@pytest.fixture
def oracle_conn(oracle_schema: OracleTestSchema) -> Iterator["oracledb.Connection"]:
    conn = oracledb.connect(user=oracle_schema.owner, password=oracle_schema.owner_password,
                            dsn=oracle_schema.dsn)
    yield conn
    conn.close()


@pytest.fixture(scope="session")
def mongo_client() -> Iterator["pymongo.MongoClient"]:
    if not _tcp_ok("127.0.0.1", int(mongo_uri().split(":")[2].split("/")[0])):
        _unavailable("start: docker compose --profile sources up -d mongo")
    client = pymongo.MongoClient(mongo_uri(), serverSelectionTimeoutMS=5000)
    deadline = time.monotonic() + (60 if _REQUIRE else 5)
    while not client.admin.command("hello").get("isWritablePrimary"):
        if time.monotonic() > deadline:
            _unavailable("mongo is up but the replica set has no primary yet")
        time.sleep(1)
    yield client
    client.close()


@pytest.fixture
def mongo_db(mongo_client: "pymongo.MongoClient") -> Iterator[str]:
    name = "dps_t_" + secrets.token_hex(4)
    yield name
    mongo_client.drop_database(name)
```
`tests/conftest.py`: add one line at module level — `pytest_plugins = ["tests.fixtures.m3_services"]`
(append to the list if M2 already declares `pytest_plugins`). The module `importorskip`s the drivers, so a
host without the `connectors` extra still collects the unit suite.

- [ ] **Step 5: Run the gates**

Run: `pytest -q tests/test_config_m3.py tests/test_observability.py && ruff check dps tests tools && ruff format --check dps tests tools && mypy dps && pytest -q`
Expected: the new tests pass; the full suite is green (integration tests skip with start hints).

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml dps/config.py .env.example dps/observability.py tests/test_config_m3.py tests/test_observability.py tests/fixtures/__init__.py tests/fixtures/m3_services.py tests/conftest.py docs/superpowers/build/M3-DEVIATIONS.md && git commit -m "chore(m3): oracle and mongo drivers, settings, markers, service fixtures, seam audit" -- pyproject.toml dps/config.py .env.example dps/observability.py tests/test_config_m3.py tests/test_observability.py tests/fixtures/__init__.py tests/fixtures/m3_services.py tests/conftest.py docs/superpowers/build/M3-DEVIATIONS.md
```

---

### Task 2: Local stack — Oracle Free, MongoDB replica set, demo schemas, secret refs

**Files:**
- Create: `docker/oracle/initdb/01_crm.sql`, `docker/secrets/local/oracle-crm-ro/{username,password,dsn}`, `docker/secrets/local/oracle-crm-owner/{username,password,dsn}`, `docker/secrets/local/oracle-crm-masked/{username,password,dsn}`, `docker/secrets/local/oracle-loans-owner/{username,password,dsn}`, `docker/secrets/local/mongo-crm/uri`, `docker/secrets/local/mongo-crm-uat/uri`, `docker/secrets/local/README.md`
- Modify: `docker-compose.yml`, `HANDOFF.md`
- Test: `tests/test_compose_m3.py`

**Behaviours:** `docker compose --profile oracle --profile sources up -d` starts `oracle` (Free 23ai, host
`127.0.0.1:1522`, healthy once the init script ran — the image's `healthcheck.sh` reports "running setup
scripts" until then) and `mongo` (`mongo:7 --replSet rs0 --bind_ip_all`, host `127.0.0.1:27018`; its
healthcheck initiates the single-member replica set on first run and is healthy only when the node is a
writable primary, which `in_place`/`append` transactions need). Init runs once per fresh container as SYS
in `FREEPDB1`; no Oracle volume, so `docker compose down` resets the demo. App and worker read connection
secrets from `/var/run/secrets/dps/local/<name>/` (contract §5.1 `secret_ref` = `local/<name>`).

- [ ] **Step 1: Write the failing test**

`tests/test_compose_m3.py`:
```python
"""Static checks of the M3 compose services (no Docker needed)."""
from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def _services() -> dict:
    return yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))["services"]


def test_oracle_service_matches_contract() -> None:
    ora = _services()["oracle"]
    assert ora["image"] == "gvenzl/oracle-free:23-slim-faststart"
    assert "oracle" in ora["profiles"] and "full" in ora["profiles"]
    assert ora["ports"] == ["127.0.0.1:1522:1521"] and ora["mem_limit"] == "2700m"
    assert ora["healthcheck"]["test"] == ["CMD", "healthcheck.sh"]
    assert ora["healthcheck"]["start_period"] == "180s"
    assert "./docker/oracle/initdb:/container-entrypoint-initdb.d:ro" in ora["volumes"]


def test_mongo_service_is_a_single_node_replica_set() -> None:
    mongo = _services()["mongo"]
    assert mongo["image"] == "mongo:7" and mongo["command"] == ["--replSet", "rs0", "--bind_ip_all"]
    assert "sources" in mongo["profiles"] and mongo["ports"] == ["127.0.0.1:27018:27017"]
    probe = " ".join(mongo["healthcheck"]["test"])
    assert "rs.initiate" in probe and "isWritablePrimary" in probe


def test_app_and_worker_mount_local_secret_refs_read_only() -> None:
    for name in ("app", "worker"):
        vols = _services()[name].get("volumes", [])
        assert "./docker/secrets/local:/var/run/secrets/dps/local:ro" in vols


def test_init_sql_creates_contract_users_and_tables() -> None:
    sql = (ROOT / "docker/oracle/initdb/01_crm.sql").read_text(encoding="utf-8").upper()
    assert "ALTER SESSION SET CONTAINER = FREEPDB1" in sql
    for user in ("CRM", "CRM_MASKED", "LOANS", "DPS_RO"):
        assert f'CREATE USER {user} IDENTIFIED BY "{user.lower()}"'.upper() in sql
    assert re.search(r"\bTAX_ID\s+CHAR\(10\)", sql) and re.search(r"\bNOTES\s+CLOB\b", sql)
    assert re.search(r"\bLAST_CONTACT_AT\s+TIMESTAMP\(9\)", sql) and "CREATE TABLE LOANS.APPLICANTS" in sql
    assert "FOR UPDATE" not in sql


def test_secret_refs_hold_no_production_shapes() -> None:
    base = ROOT / "docker/secrets/local"
    assert (base / "oracle-crm-ro/dsn").read_text().strip() == "oracle:1521/FREEPDB1"
    assert (base / "mongo-crm/uri").read_text().strip() == "mongodb://mongo:27017/crm?directConnection=true"
```

- [ ] **Step 2: Run to confirm failure**

Run: `pytest -q tests/test_compose_m3.py` → FAIL (`KeyError: 'oracle'`, missing files).

- [ ] **Step 3: Write the init script** `docker/oracle/initdb/01_crm.sql`:
```sql
-- DPS M3 local demo schemas. Runs ONCE, as SYS, on the container's first database start
-- (gvenzl/oracle-free /container-entrypoint-initdb.d). Passwords are demo literals equal to the
-- lower-cased user name (contract §11) and are never used outside docker compose.
-- Tables are created empty; rows come from tools/seed_sources.py --target oracle.
WHENEVER SQLERROR EXIT SQL.SQLCODE
ALTER SESSION SET CONTAINER = FREEPDB1;

CREATE USER CRM IDENTIFIED BY "crm" DEFAULT TABLESPACE USERS QUOTA UNLIMITED ON USERS;
CREATE USER CRM_MASKED IDENTIFIED BY "crm_masked" DEFAULT TABLESPACE USERS QUOTA UNLIMITED ON USERS;
CREATE USER LOANS IDENTIFIED BY "loans" DEFAULT TABLESPACE USERS QUOTA UNLIMITED ON USERS;
CREATE USER DPS_RO IDENTIFIED BY "dps_ro";
GRANT CREATE SESSION, CREATE TABLE, CREATE SEQUENCE TO CRM;
GRANT CREATE SESSION, CREATE TABLE TO CRM_MASKED;   -- target of table loads (creates its own tables)
GRANT CREATE SESSION, CREATE TABLE TO LOANS;
GRANT CREATE SESSION TO DPS_RO;                      -- read-only source login: SELECT grants below only

-- The headline table (mockup 03): a CHAR(10) tax id, a CLOB of notes, a TIMESTAMP(9) column.
CREATE TABLE CRM.CUSTOMERS (
  CUSTOMER_ID     NUMBER(12)          NOT NULL,
  FULL_NAME       VARCHAR2(120 CHAR)  NOT NULL,
  NATIONAL_ID     VARCHAR2(16),
  TAX_ID          CHAR(10),
  EMAIL           VARCHAR2(254),
  MOBILE          VARCHAR2(20),
  DOB             DATE,
  ADDR_LINE1      VARCHAR2(200 CHAR),
  CITY            VARCHAR2(80 CHAR),
  PIN_CODE        VARCHAR2(10),
  COUNTRY         CHAR(2)             NOT NULL,
  SEGMENT         VARCHAR2(16)        NOT NULL,
  RISK_SCORE      NUMBER(4),
  NOTES           CLOB,
  CREATED_AT      TIMESTAMP(6)        DEFAULT SYSTIMESTAMP NOT NULL,
  LAST_CONTACT_AT TIMESTAMP(9),
  CONSTRAINT CUSTOMERS_PK PRIMARY KEY (CUSTOMER_ID)
);

-- Composite key with a TIMESTAMP(9) component; events arrive in bursts inside one microsecond.
-- PAYLOAD carries an IS JSON check: python-oracledb returns such a CLOB as a dict unless told not to.
CREATE TABLE CRM.INTERACTIONS (
  CUSTOMER_ID     NUMBER(12)          NOT NULL,
  EVENT_TS        TIMESTAMP(9)        NOT NULL,
  CHANNEL         VARCHAR2(16)        NOT NULL,
  AGENT_EMAIL     VARCHAR2(254),
  SUMMARY         VARCHAR2(2000 CHAR),
  PAYLOAD         CLOB CONSTRAINT INTERACTIONS_PAYLOAD_JSON CHECK (PAYLOAD IS JSON),
  CONSTRAINT INTERACTIONS_PK PRIMARY KEY (CUSTOMER_ID, EVENT_TS)
);

-- CHAR primary key: short references are blank-padded to 12; a VARCHAR2 bind of 'K17' matches nothing.
CREATE TABLE CRM.KYC_DOCUMENTS (
  DOC_REF         CHAR(12)            NOT NULL,
  CUSTOMER_ID     NUMBER(12)          NOT NULL,
  DOC_TYPE        VARCHAR2(20)        NOT NULL,
  DOC_NUMBER      VARCHAR2(32),
  ISSUED_ON       DATE,
  EXPIRES_ON      DATE,
  CONSTRAINT KYC_DOCUMENTS_PK PRIMARY KEY (DOC_REF)
);

-- No key on purpose: partitions are ROWID ranges.
CREATE TABLE CRM.ADDRESS_HISTORY (
  CUSTOMER_ID     NUMBER(12)          NOT NULL,
  ADDR_LINE1      VARCHAR2(200 CHAR),
  CITY            VARCHAR2(80 CHAR),
  PIN_CODE        VARCHAR2(10),
  VALID_FROM      DATE                NOT NULL
);

-- Identity key: the append(generate) demo.
CREATE TABLE CRM.CUSTOMER_EVENTS (
  EVENT_ID        NUMBER GENERATED BY DEFAULT AS IDENTITY,
  CUSTOMER_ID     NUMBER(12)          NOT NULL,
  EVENT_TYPE      VARCHAR2(24)        NOT NULL,
  CONTACT_EMAIL   VARCHAR2(254),
  OCCURRED_AT     TIMESTAMP(6)        NOT NULL,
  CONSTRAINT CUSTOMER_EVENTS_PK PRIMARY KEY (EVENT_ID)
);

-- String key in a second schema.
CREATE TABLE LOANS.APPLICANTS (
  APPLICATION_ID  VARCHAR2(20)        NOT NULL,
  CUSTOMER_ID     NUMBER(12)          NOT NULL,
  APPLICANT_NAME  VARCHAR2(120 CHAR)  NOT NULL,
  PAN             CHAR(10),
  MOBILE          VARCHAR2(20),
  INCOME          NUMBER(14,2),
  STATUS          VARCHAR2(12)        NOT NULL,
  APPLIED_AT      TIMESTAMP(6)        NOT NULL,
  CONSTRAINT APPLICANTS_PK PRIMARY KEY (APPLICATION_ID)
);

GRANT SELECT ON CRM.CUSTOMERS TO DPS_RO;
GRANT SELECT ON CRM.INTERACTIONS TO DPS_RO;
GRANT SELECT ON CRM.KYC_DOCUMENTS TO DPS_RO;
GRANT SELECT ON CRM.ADDRESS_HISTORY TO DPS_RO;
GRANT SELECT ON CRM.CUSTOMER_EVENTS TO DPS_RO;
GRANT SELECT ON LOANS.APPLICANTS TO DPS_RO;
EXIT;
```

- [ ] **Step 4: Write the secret refs** (each file one line, no trailing secrets elsewhere):

| Ref (`secret_ref`) | `username` | `password` | `dsn` / `uri` |
|---|---|---|---|
| `local/oracle-crm-ro` | `dps_ro` | `dps_ro` | `oracle:1521/FREEPDB1` |
| `local/oracle-crm-owner` | `crm` | `crm` | `oracle:1521/FREEPDB1` |
| `local/oracle-crm-masked` | `crm_masked` | `crm_masked` | `oracle:1521/FREEPDB1` |
| `local/oracle-loans-owner` | `loans` | `loans` | `oracle:1521/FREEPDB1` |
| `local/mongo-crm` | — | — | `uri`: `mongodb://mongo:27017/crm?directConnection=true` |
| `local/mongo-crm-uat` | — | — | `uri`: `mongodb://mongo:27017/crm_uat?directConnection=true` |

`docker/secrets/local/README.md`: "Demo secret refs for docker compose only. The values are the
documented local literals (contract §11). Production mounts real secrets at `DPS_SECRET_DIR/<ref>/` from
the platform secret store; nothing in this directory is ever deployed."

- [ ] **Step 5: Add the services to `docker-compose.yml`** (merge into the M2 file; keep M2 services):
```yaml
  oracle:
    image: gvenzl/oracle-free:23-slim-faststart
    profiles: ["oracle", "full"]
    environment:
      ORACLE_PASSWORD: dps_local_oracle          # SYS/SYSTEM, local demo literal (contract §11)
    volumes:
      - ./docker/oracle/initdb:/container-entrypoint-initdb.d:ro
    ports:
      - "127.0.0.1:1522:1521"
    mem_limit: 2700m                             # idles at ~2.5 GiB (Wega measurement)
    healthcheck:
      test: ["CMD", "healthcheck.sh"]            # exit 4 while init scripts run → unhealthy until done
      interval: 10s
      timeout: 10s
      retries: 30
      start_period: 180s
      start_interval: 2s
    labels: { app: data-privacy-services, component: source-oracle, env: local }

  mongo:
    image: mongo:7
    profiles: ["sources", "full"]
    command: ["--replSet", "rs0", "--bind_ip_all"]
    ports:
      - "127.0.0.1:27018:27017"
    mem_limit: 1g
    healthcheck:
      # Initiates the one-member replica set on first run; healthy only as a writable primary.
      test: ["CMD", "mongosh", "--quiet", "--eval",
             "try { rs.status() } catch (e) { rs.initiate({_id:'rs0',members:[{_id:0,host:'localhost:27017'}]}) } if (!db.hello().isWritablePrimary) { quit(1) } print('ok')"]
      interval: 5s
      timeout: 10s
      retries: 30
      start_period: 20s
    labels: { app: data-privacy-services, component: source-mongo, env: local }
```
Add to both `app` and `worker`: `volumes: ["./docker/secrets/local:/var/run/secrets/dps/local:ro"]`
(merged with M2's volume list) and env `DPS_SECRET_DIR: /var/run/secrets/dps`. No `depends_on` on
`oracle`/`mongo`: they live in optional profiles and the connectors report `connection_failed` cleanly when
absent.

- [ ] **Step 6: HANDOFF.md** — in §1/§3/§6 add: services `oracle` (`--profile oracle`, 1522, 2.7 GB,
first start 1–3 min, init runs once per fresh container) and `mongo` (`--profile sources`, 27018,
single-node replica set, clients use `directConnection=true`); secret refs `local/*` (names only); trap:
"`docker compose up --wait` fails on exited one-shots — start, then poll health (contract §11)".

- [ ] **Step 7: Verify for real**

Run:
```bash
pytest -q tests/test_compose_m3.py
docker compose --profile oracle --profile sources up -d oracle mongo
until [ "$(docker inspect -f '{{.State.Health.Status}}' "$(docker compose ps -q oracle)")" = healthy ]; do sleep 5; done
until [ "$(docker inspect -f '{{.State.Health.Status}}' "$(docker compose ps -q mongo)")" = healthy ]; do sleep 2; done
docker compose exec -T oracle bash -c "echo \"SELECT owner, table_name FROM all_tables WHERE owner IN ('CRM','LOANS') ORDER BY 1,2;\" | sqlplus -s dps_ro/dps_ro@localhost:1521/FREEPDB1"
docker compose exec -T mongo mongosh --quiet --eval "db.hello().setName"
```
Expected: 5 passed; both containers healthy (Oracle within ~3 min); the query lists `CRM.ADDRESS_HISTORY,
CUSTOMERS, CUSTOMER_EVENTS, INTERACTIONS, KYC_DOCUMENTS` and `LOANS.APPLICANTS` (DPS_RO sees them through
its grants); `rs0`.

- [ ] **Step 8: Commit**

```bash
git add docker/oracle docker/secrets/local docker-compose.yml HANDOFF.md tests/test_compose_m3.py && git commit -m "feat(stack): Oracle Free and MongoDB replica set with CRM demo schemas and local secret refs" -- docker/oracle docker/secrets/local docker-compose.yml HANDOFF.md tests/test_compose_m3.py
```

---
### Task 3: Oracle rules — type table, SQL builders, ROWID codec (pure, offline)

**Files:**
- Create: `dps/layers/connectors/oracle_sql.py`
- Modify: `dps/layers/connectors/base.py` (`ColumnInfo.pseudo`; contract §4.1 addition — edit the contract file in this commit)
- Test: `tests/connectors/test_oracle_sql.py`

**Interfaces introduced:**

```python
# dps/layers/connectors/base.py (M2 file) — additive, defaulted
class ColumnInfo(BaseModel):
    ...                                      # M2 fields unchanged
    pseudo: bool = False                     # read-only key column that is not table data (Oracle ROWID)

# dps/layers/connectors/oracle_sql.py
TS9_FORMAT: Final = 'YYYY-MM-DD"T"HH24:MI:SS.FF9'
TSTZ_FORMAT: Final = 'YYYY-MM-DD"T"HH24:MI:SS.FF9TZH:TZM'
LEDGER_TABLE: Final = "DPS_WRITE_LEDGER"
IN_LIST_MAX: Final = 500
Family = Literal["number","float","varchar","nvarchar","char","nchar","date","timestamp","timestamp_tz",
                 "clob","nclob","blob","raw","json","unsupported"]
InputType = Literal["char","nchar","nvarchar","clob","nclob","blob"]
@dataclass(frozen=True, slots=True)
class CatalogColumn: name; data_type; data_length; char_length; char_used; data_precision; data_scale; nullable; has_default; is_identity
@dataclass(frozen=True, slots=True)
class ReadPlan: sql: str; columns: list[ColumnInfo]; bind_types: dict[str, InputType]
def family_of(data_type: str) -> Family
def column_path(name: str) -> str
def column_info(c: CatalogColumn, *, is_key: bool) -> ColumnInfo
def rowid_column() -> ColumnInfo
def qcol(name: str) -> str
def qualified(obj: ObjectRef) -> str
def select_expr(col: ColumnInfo, alias: str = "t") -> str
def sort_expr(col: ColumnInfo, alias: str = "t") -> str
def value_expr(col: ColumnInfo, bind: str) -> str
def key_value_expr(col: ColumnInfo, bind: str) -> str
def input_type(col: ColumnInfo) -> InputType | None
def is_ts9(col: ColumnInfo) -> bool
def tuple_predicate(cols: list[str], values: list[str], op: Literal[">", ">=", "<"]) -> str
def key_columns(shape: TableShape) -> list[ColumnInfo]
def read_sql(shape, *, columns, where, query, sample, lower, upper, after) -> ReadPlan
def count_sql(shape, *, where, query, sample) -> str
def sample_sql(shape, *, columns, where, query, percent, keys_only: bool = False) -> ReadPlan
def merge_sql(target: ObjectRef, cols: list[ColumnInfo], keys: list[str]) -> str
def update_sql(target: ObjectRef, set_cols: list[ColumnInfo], key_cols: list[ColumnInfo]) -> str
def insert_sql(target: ObjectRef, cols: list[ColumnInfo], *, sequence: tuple[str, str] | None = None) -> str
def collision_sql(target: ObjectRef, keys: list[ColumnInfo], n: int) -> str
def ledger_ddl(owner: str) -> str
def ledger_mark_sql(owner: str) -> str
def ledger_read_sql(owner: str) -> str
def ledger_purge_sql(owner: str) -> str
def oracle_ddl_type(target: ColumnInfo, source: ColumnInfo | None) -> str
def create_table_sql(target: ObjectRef, cols: list[tuple[ColumnInfo, ColumnInfo | None]], keys: list[str]) -> str
def rowid_to_key(rowid: str) -> int
def key_to_rowid(key: int) -> str
def pad_char(value: object, length: int | None) -> object
```

- [ ] **Step 1: Write the failing tests** — `tests/connectors/test_oracle_sql.py`:
```python
from __future__ import annotations

import itertools
import random
import re

import pytest

from dps.layers.connectors import oracle_sql as osql
from dps.layers.connectors.base import ColumnInfo, ObjectRef, TableShape


def _cat(name: str, data_type: str, **kw: object) -> osql.CatalogColumn:
    base = dict(data_length=22, char_length=0, char_used=None, data_precision=None, data_scale=None,
                nullable=True, has_default=False, is_identity=False)
    base.update(kw)
    return osql.CatalogColumn(name=name, data_type=data_type, **base)  # type: ignore[arg-type]


def _shape(*cols: ColumnInfo, key: list[str], src: str = "primary_key") -> TableShape:
    return TableShape(object=ObjectRef(owner="CRM", name="INTERACTIONS"), columns=list(cols), key=key,
                      key_source=src)  # type: ignore[arg-type]


CID = osql.column_info(_cat("CUSTOMER_ID", "NUMBER", data_precision=12, data_scale=0, nullable=False), is_key=True)
TS9 = osql.column_info(_cat("EVENT_TS", "TIMESTAMP(9)", data_scale=9, nullable=False), is_key=True)
REF = osql.column_info(_cat("DOC_REF", "CHAR", data_length=12, char_length=12, char_used="B"), is_key=True)
NOTES = osql.column_info(_cat("NOTES", "CLOB", data_length=4000), is_key=False)
TZ = osql.column_info(_cat("SEEN_AT", "TIMESTAMP(6) WITH TIME ZONE", data_scale=6), is_key=False)
NAME = osql.column_info(_cat("FULL_NAME", "VARCHAR2", data_length=480, char_length=120, char_used="C"), is_key=False)


@pytest.mark.parametrize(
    "cat,kind,data_type,ts,length",
    [
        (_cat("A", "NUMBER", data_precision=12, data_scale=0), "int", "NUMBER(12)", None, None),
        (_cat("A", "NUMBER", data_precision=14, data_scale=2), "decimal", "NUMBER(14,2)", None, None),
        (_cat("A", "NUMBER"), "decimal", "NUMBER", None, None),
        (_cat("A", "NUMBER", data_scale=0), "int", "NUMBER(38)", None, None),
        (_cat("A", "FLOAT", data_precision=126), "float", "FLOAT(126)", None, None),
        (_cat("A", "VARCHAR2", data_length=480, char_length=120, char_used="C"), "text", "VARCHAR2(120 CHAR)", None, 120),
        (_cat("A", "VARCHAR2", data_length=16, char_length=16, char_used="B"), "text", "VARCHAR2(16)", None, 16),
        (_cat("A", "CHAR", data_length=10, char_length=10, char_used="B"), "char", "CHAR(10)", None, 10),
        (_cat("A", "DATE"), "timestamp", "DATE", 0, None),
        (_cat("A", "TIMESTAMP(9)", data_scale=9), "timestamp", "TIMESTAMP(9)", 9, None),
        (_cat("A", "TIMESTAMP(6) WITH TIME ZONE", data_scale=6), "timestamp", "TIMESTAMP(6) WITH TIME ZONE", 6, None),
        (_cat("A", "CLOB"), "lob_text", "CLOB", None, None),
        (_cat("A", "BLOB"), "lob_binary", "BLOB", None, None),
        (_cat("A", "RAW", data_length=16), "binary", "RAW(16)", None, 16),
        (_cat("A", "XMLTYPE"), "other", "XMLTYPE", None, None),
        (_cat("A", "TIMESTAMP(6) WITH LOCAL TIME ZONE"), "other", "TIMESTAMP(6) WITH LOCAL TIME ZONE", None, None),
    ],
)
def test_type_table(cat: osql.CatalogColumn, kind: str, data_type: str, ts: int | None, length: int | None) -> None:
    info = osql.column_info(cat, is_key=False)
    assert (info.kind, info.data_type, info.ts_precision, info.length) == (kind, data_type, ts, length)
    assert info.path == "$.A" and info.pseudo is False


def _py(expr: str) -> str:
    return expr.replace(" AND ", " and ").replace(" OR ", " or ").replace(" = ", " == ")


@pytest.mark.parametrize("n", [1, 2, 3])
@pytest.mark.parametrize("op", [">", ">=", "<"])
def test_tuple_predicate_is_lexicographic_over_a_domain(n: int, op: str) -> None:
    cols, vals = ["a", "b", "c"][:n], ["x", "y", "z"][:n]
    expr = _py(osql.tuple_predicate(cols, vals, op))  # type: ignore[arg-type]
    domain = list(itertools.product(range(3), repeat=n))
    for left, right in itertools.product(domain, domain):
        env = dict(zip(cols, left, strict=True)) | dict(zip(vals, right, strict=True))
        expected = {">": left > right, ">=": left >= right, "<": left < right}[op]
        assert eval(expr, {}, env) is expected, (expr, left, right)  # noqa: S307 - test oracle


def test_read_sql_keyset_shape_and_pitfall_rules() -> None:
    shape = _shape(CID, TS9, NOTES, TZ, key=["CUSTOMER_ID", "EVENT_TS"])
    plan = osql.read_sql(shape, columns=shape.columns, where="CHANNEL = :ch", query=None, sample=None,
                         lower=True, upper=True, after=True)
    sql = plan.sql
    assert "FOR UPDATE" not in sql.upper() and sql.endswith("FETCH FIRST :batch ROWS ONLY")
    assert f"TO_CHAR(t.\"EVENT_TS\", '{osql.TS9_FORMAT}')" in sql            # ns survive the driver
    assert f"TO_TIMESTAMP(:af1, '{osql.TS9_FORMAT}')" in sql                  # and go back as text
    assert "ORDER BY t.\"CUSTOMER_ID\", t.\"EVENT_TS\"" in sql                # raw columns, never aliases
    assert not re.search(r'AS\s+"(CUSTOMER_ID|EVENT_TS)"', sql)
    assert f"TO_CHAR(t.\"SEEN_AT\", '{osql.TSTZ_FORMAT}')" in sql
    assert "(CHANNEL = :ch)" in sql and ":lo0" in sql and ":hi1" in sql
    assert [c.name for c in plan.columns] == ["CUSTOMER_ID", "EVENT_TS", "NOTES", "SEEN_AT"]


def test_char_keys_bind_as_char_and_are_padded() -> None:
    shape = _shape(REF, key=["DOC_REF"])
    plan = osql.read_sql(shape, columns=shape.columns, where=None, query=None, sample=None,
                         lower=False, upper=False, after=True)
    assert plan.bind_types == {"af0": "char"}
    assert osql.pad_char("K17", 12) == "K17         " and osql.pad_char(None, 12) is None
    assert osql.pad_char("K17         ", 12) == "K17         "


def test_rowid_tables_page_on_rowid() -> None:
    rid = osql.rowid_column()
    assert rid.pseudo and rid.is_key and rid.name == "ROWID" and rid.kind == "int"
    shape = _shape(NAME, rid, key=["ROWID"], src="rowid")
    sql = osql.read_sql(shape, columns=shape.columns, where=None, query=None, sample=None,
                        lower=True, upper=False, after=False).sql
    assert "ROWIDTOCHAR(t.ROWID)" in sql and "t.ROWID >= CHARTOROWID(:lo0)" in sql
    assert sql.split("ORDER BY")[1].strip().startswith("t.ROWID")


def test_rowid_codec_round_trip_and_order() -> None:
    rng = random.Random(7)
    comps = [(rng.randrange(2**32), rng.randrange(1024), rng.randrange(2**22), rng.randrange(2**16))
             for _ in range(500)]
    rowids = []
    for obj, fno, blk, row in comps:
        rid = osql._enc(obj, 6) + osql._enc(fno, 3) + osql._enc(blk, 6) + osql._enc(row, 3)
        assert osql.key_to_rowid(osql.rowid_to_key(rid)) == rid
        rowids.append(rid)
    by_components = [r for _, r in sorted(zip(comps, rowids, strict=True))]
    assert sorted(rowids, key=osql.rowid_to_key) == by_components
    with pytest.raises(ValueError):
        osql.rowid_to_key("*BAEADAoA")       # logical (IOT) rowids are not range-able


def test_write_statements() -> None:
    target = ObjectRef(owner="CRM_MASKED", name="CUSTOMERS")
    merge = osql.merge_sql(target, [CID, NAME, NOTES], ["CUSTOMER_ID"])
    assert merge.startswith('MERGE /* dps:merge */ INTO "CRM_MASKED"."CUSTOMERS" d USING (SELECT')
    assert 'ON (d."CUSTOMER_ID" = s."CUSTOMER_ID")' in merge and "WHEN MATCHED THEN UPDATE SET" in merge
    assert 'd."CUSTOMER_ID" = s."CUSTOMER_ID",' not in merge.split("UPDATE SET")[1]
    keys_only = osql.merge_sql(target, [CID], ["CUSTOMER_ID"])
    assert "WHEN MATCHED" not in keys_only and "WHEN NOT MATCHED THEN INSERT" in keys_only
    upd = osql.update_sql(ObjectRef(owner="CRM", name="CUSTOMERS"), [NAME, NOTES], [CID])
    assert upd.split(" WHERE ")[0].count('"CUSTOMER_ID"') == 0 and upd.endswith('d."CUSTOMER_ID" = :v2')
    ins = osql.insert_sql(target, [CID, NAME], sequence=("CUSTOMER_ID", '"CRM"."CASE_SEQ"'))
    assert '"CRM"."CASE_SEQ".NEXTVAL' in ins and ":v0" not in ins.split("VALUES")[1].split(",")[0]
    for stmt in (merge, keys_only, upd, ins, osql.collision_sql(target, [CID], 3),
                 osql.ledger_ddl("CRM"), osql.ledger_mark_sql("CRM"), osql.ledger_purge_sql("CRM")):
        assert "FOR UPDATE" not in stmt.upper() and "FETCH FIRST" not in stmt.upper()


def test_ledger_statements_are_19c_safe_and_fenced() -> None:
    assert "SQLCODE != -955" in osql.ledger_ddl("CRM") and '"CRM"."DPS_WRITE_LEDGER"' in osql.ledger_ddl("CRM")
    mark = osql.ledger_mark_sql("CRM")
    assert "WHERE s.BATCH_NO > l.BATCH_NO" in mark and "WHEN NOT MATCHED THEN INSERT" in mark


def test_created_target_types() -> None:
    widened = NAME.model_copy(update={"length": 64})
    assert osql.oracle_ddl_type(widened, NAME) == "VARCHAR2(120 CHAR)"     # fits: keep the source type
    longer = NAME.model_copy(update={"length": 220})
    assert osql.oracle_ddl_type(longer, NAME) == "VARCHAR2(220 CHAR)"
    assert osql.oracle_ddl_type(NAME.model_copy(update={"length": 1500}), NAME) == "CLOB"
    as_text = CID.model_copy(update={"kind": "text", "length": 40})
    assert osql.oracle_ddl_type(as_text, CID) == "VARCHAR2(40 CHAR)"      # det_token on a NUMBER copy
    ddl = osql.create_table_sql(ObjectRef(owner="CRM_MASKED", name="CUSTOMERS"),
                                [(CID, CID), (NAME, NAME)], ["CUSTOMER_ID"])
    assert ddl.startswith('CREATE TABLE "CRM_MASKED"."CUSTOMERS" ("CUSTOMER_ID" NUMBER(12) NOT NULL')
    assert re.search(r'CONSTRAINT "DPS_PK_[0-9A-F]{8}" PRIMARY KEY \("CUSTOMER_ID"\)\)$', ddl)
    with pytest.raises(ValueError):
        osql.oracle_ddl_type(ColumnInfo(path="$.X", name="X", data_type="XMLTYPE", kind="other"), None)


def test_catalog_names_are_quoted_exactly() -> None:
    assert osql.qcol("FULL_NAME") == '"FULL_NAME"' and osql.qcol("firstName") == '"firstName"'
    with pytest.raises(ValueError):
        osql.qcol('bad"name')
```

- [ ] **Step 2: Run to confirm failure** — `pytest -q tests/connectors/test_oracle_sql.py` → FAIL
  (`ModuleNotFoundError: dps.layers.connectors.oracle_sql`).

- [ ] **Step 3: Add `pseudo` to `ColumnInfo`** in `base.py` (`pseudo: bool = False`, docstring line:
  "read-only key column that is not table data, e.g. Oracle ROWID; never classified, never written") and
  update contract §4.1 with the same line.

- [ ] **Step 4: Write `dps/layers/connectors/oracle_sql.py`**
```python
"""Pure Oracle rules for the DPS connector: the closed type table, SQL builders, the ROWID codec.

No driver I/O happens here, so every statement shape is unit-tested offline. The rules are measured,
not chosen (Wega orchestrator on Oracle 23ai Free with python-oracledb 26.0.0 thin):

* Reads never lock. No statement built here contains FOR UPDATE; FETCH FIRST appears only in reads,
  which is legal precisely because they do not lock (Wega L21).
* The select list is positional and real columns are unaliased; ORDER BY names the table-qualified
  column (t."K"). An alias equal to a column name would shadow it in ORDER BY and sort numbers as text
  (Wega T2-ORA-ID-EXACT).
* TIMESTAMP(7-9) crosses the driver as FF9 text both ways: the driver's datetime holds microseconds,
  and a key truncated to microseconds never pages past a burst of rows inside one microsecond.
* CHAR binds are typed CHAR and blank-padded to the declared length; a VARCHAR2 bind compares without
  padding, so 'K17' never matches the stored 'K17         '.
* CLOB/NCLOB are fetched whole as str and never addressed by offset: CLOB offsets count UTF-16 code
  units and Python counts code points, so an emoji shifts every later offset by one.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Final, Literal

from dps.layers.connectors.base import ColumnInfo, ObjectRef, TableShape
from dps.layers.connectors.selection import quote_ident

TS9_FORMAT: Final = 'YYYY-MM-DD"T"HH24:MI:SS.FF9'
TSTZ_FORMAT: Final = 'YYYY-MM-DD"T"HH24:MI:SS.FF9TZH:TZM'
LEDGER_TABLE: Final = "DPS_WRITE_LEDGER"
IN_LIST_MAX: Final = 500                 # Oracle allows 1000 expressions in an IN list
TEXT_VARCHAR_MAX_CHARS: Final = 1000     # created targets: wider text goes to CLOB (4000-byte limit)

Family = Literal["number", "float", "varchar", "nvarchar", "char", "nchar", "date", "timestamp",
                 "timestamp_tz", "clob", "nclob", "blob", "raw", "json", "unsupported"]
InputType = Literal["char", "nchar", "nvarchar", "clob", "nclob", "blob"]

_TS = re.compile(r"^TIMESTAMP(?:\((\d)\))?$")
_TSTZ = re.compile(r"^TIMESTAMP(?:\((\d)\))? WITH TIME ZONE$")
_SIMPLE_PATH = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_SIMPLE: Final[dict[str, Family]] = {
    "NUMBER": "number", "FLOAT": "float", "BINARY_FLOAT": "float", "BINARY_DOUBLE": "float",
    "VARCHAR2": "varchar", "NVARCHAR2": "nvarchar", "CHAR": "char", "NCHAR": "nchar", "DATE": "date",
    "CLOB": "clob", "NCLOB": "nclob", "BLOB": "blob", "RAW": "raw", "JSON": "json",
}
_KIND: Final[dict[Family, str]] = {
    "number": "decimal", "float": "float", "varchar": "text", "nvarchar": "text", "char": "char",
    "nchar": "char", "date": "timestamp", "timestamp": "timestamp", "timestamp_tz": "timestamp",
    "clob": "lob_text", "nclob": "lob_text", "blob": "lob_binary", "raw": "binary", "json": "json",
    "unsupported": "other",
}


@dataclass(frozen=True, slots=True)
class CatalogColumn:
    """One ALL_TAB_COLS row (visible, non-virtual, user-generated), as describe() reads it."""

    name: str
    data_type: str
    data_length: int
    char_length: int
    char_used: str | None
    data_precision: int | None
    data_scale: int | None
    nullable: bool
    has_default: bool
    is_identity: bool


@dataclass(frozen=True, slots=True)
class ReadPlan:
    """A read statement, the columns its select list yields in order, and typed binds."""

    sql: str
    columns: list[ColumnInfo]
    bind_types: dict[str, InputType]


def family_of(data_type: str) -> Family:
    """The closed family of a catalogue type; anything not listed is ``unsupported``."""
    if data_type in _SIMPLE:
        return _SIMPLE[data_type]
    if _TS.match(data_type):
        return "timestamp"
    if _TSTZ.match(data_type):
        return "timestamp_tz"
    return "unsupported"   # LONG, LONG RAW, BFILE, XMLTYPE, (U)ROWID columns, TSLTZ, VECTOR, objects


def column_path(name: str) -> str:
    """``$.NAME`` for plain identifiers, ``$['A$B']`` otherwise (``$``/``#`` are legal in Oracle)."""
    return f"$.{name}" if _SIMPLE_PATH.fullmatch(name) else f"$['{name}']"


def _render_type(c: CatalogColumn, fam: Family) -> str:
    if fam == "number":
        if c.data_precision is None:
            return "NUMBER(38)" if c.data_scale == 0 else "NUMBER"
        return f"NUMBER({c.data_precision})" if not c.data_scale else f"NUMBER({c.data_precision},{c.data_scale})"
    if fam == "float" and c.data_type == "FLOAT":
        return f"FLOAT({c.data_precision or 126})"
    if fam in ("varchar", "char"):
        if c.char_used == "C":
            return f"{c.data_type}({c.char_length} CHAR)"
        return f"{c.data_type}({c.data_length})"
    if fam in ("nvarchar", "nchar"):
        return f"{c.data_type}({c.char_length})"
    if fam == "raw":
        return f"RAW({c.data_length})"
    return c.data_type


def column_info(c: CatalogColumn, *, is_key: bool) -> ColumnInfo:
    """Map one catalogue row to the contract's ColumnInfo (kind, length, precision, ts_precision)."""
    fam = family_of(c.data_type)
    kind = "int" if fam == "number" and c.data_scale == 0 else _KIND[fam]
    ts_precision: int | None = None
    if fam == "date":
        ts_precision = 0
    elif fam in ("timestamp", "timestamp_tz"):
        m = _TS.match(c.data_type) or _TSTZ.match(c.data_type)
        ts_precision = int(m.group(1)) if m and m.group(1) else 6
    length: int | None = None
    if fam in ("varchar", "char"):
        length = c.char_length if c.char_used == "C" else c.data_length
    elif fam in ("nvarchar", "nchar"):
        length = c.char_length
    elif fam == "raw":
        length = c.data_length
    return ColumnInfo(
        path=column_path(c.name), name=c.name, data_type=_render_type(c, fam), kind=kind,  # type: ignore[arg-type]
        length=length, precision=c.data_precision, scale=c.data_scale, ts_precision=ts_precision,
        nullable=c.nullable, is_key=is_key, has_default=c.has_default, is_identity=c.is_identity,
    )


def rowid_column() -> ColumnInfo:
    """The ROWID pseudo key of a key-less table: a packed integer, read-only, never written."""
    return ColumnInfo(path="$.ROWID", name="ROWID", data_type="ROWID", kind="int", nullable=False,
                      is_key=True, pseudo=True)


def qcol(name: str) -> str:
    """Quote a catalogue-derived column name exactly as the catalogue spells it.

    Names typed by a person go through ``selection.quote_ident`` (validated and upper-cased, the Wega
    rule). Names read from ALL_TAB_COLS are already exact, possibly mixed-case, and are quoted verbatim;
    Oracle forbids double quotes and NUL inside identifiers, so neither can appear.
    """
    if not name or len(name) > 128 or '"' in name or "\x00" in name:
        raise ValueError("not a valid catalogue identifier")
    return f'"{name}"'


def qualified(obj: ObjectRef) -> str:
    """``"OWNER"."NAME"`` through the validated, upper-casing identifier rule."""
    if not obj.owner:
        raise ValueError("an Oracle object needs an owner")
    return f"{quote_ident(obj.owner, 'oracle')}.{quote_ident(obj.name, 'oracle')}"


def is_tz(col: ColumnInfo) -> bool:
    return col.kind == "timestamp" and "TIME ZONE" in col.data_type


def is_ts9(col: ColumnInfo) -> bool:
    """TIMESTAMP(7..9) without a zone: carried as FF9 text both ways."""
    return col.kind == "timestamp" and not is_tz(col) and (col.ts_precision or 0) > 6


def select_expr(col: ColumnInfo, alias: str = "t") -> str:
    """Positional, unaliased select expression of one column."""
    if col.pseudo:
        return f"ROWIDTOCHAR({alias}.ROWID)"
    q = f"{alias}.{qcol(col.name)}"
    if is_tz(col):
        return f"TO_CHAR({q}, '{TSTZ_FORMAT}')"
    if is_ts9(col):
        return f"TO_CHAR({q}, '{TS9_FORMAT}')"
    if col.data_type == "JSON":
        return f"JSON_SERIALIZE({q} RETURNING CLOB)"
    return q


def sort_expr(col: ColumnInfo, alias: str = "t") -> str:
    """Column side of a key predicate and of ORDER BY (index-matching, never an alias)."""
    if col.pseudo:
        return f"{alias}.ROWID"
    q = f"{alias}.{qcol(col.name)}"
    return f"SYS_EXTRACT_UTC({q})" if is_tz(col) else q


def value_expr(col: ColumnInfo, bind: str) -> str:
    """Value side of a write or predicate: the bind converted back to the column's type."""
    if col.pseudo:
        return f"CHARTOROWID(:{bind})"
    if is_tz(col):
        return f"TO_TIMESTAMP_TZ(:{bind}, '{TSTZ_FORMAT}')"
    if is_ts9(col):
        return f"TO_TIMESTAMP(:{bind}, '{TS9_FORMAT}')"
    if col.data_type == "JSON":
        return f"JSON(:{bind})"
    return f":{bind}"


def key_value_expr(col: ColumnInfo, bind: str) -> str:
    """Value side of a key predicate; zoned timestamps compare in UTC like ``sort_expr``."""
    return f"SYS_EXTRACT_UTC({value_expr(col, bind)})" if is_tz(col) else value_expr(col, bind)


def input_type(col: ColumnInfo) -> InputType | None:
    """The bind type a value of this column needs (``None`` = the driver's default is right)."""
    dt = col.data_type
    if dt.startswith("CHAR("):
        return "char"
    if dt.startswith("NCHAR("):
        return "nchar"
    if dt.startswith("NVARCHAR2("):
        return "nvarchar"
    if dt in ("CLOB", "JSON"):
        return "clob"
    if dt == "NCLOB":
        return "nclob"
    if dt == "BLOB":
        return "blob"
    return None


def pad_char(value: object, length: int | None) -> object:
    """Blank-pad a CHAR value to its declared length (a rstripped canonical text binds back correctly)."""
    if isinstance(value, str) and length and len(value) < length:
        return value.ljust(length)
    return value


def tuple_predicate(cols: list[str], values: list[str], op: Literal[">", ">=", "<"]) -> str:
    """Lexicographic ``(c1..cn) op (v1..vn)`` in the expanded form Oracle can range-scan.

    ``(a, b) > (x, y)`` becomes ``(a >= x AND (a > x OR (a = x AND b > y)))``: the leading conjunct is
    an index range on the first key column (the Wega keyset form); the rest decides ties.
    """
    if not cols or len(cols) != len(values):
        raise ValueError("a key predicate needs one value per key column")
    first = ">" if op in (">", ">=") else "<"

    def rec(i: int) -> str:
        if i == len(cols) - 1:
            return f"{cols[i]} {op} {values[i]}"
        return f"({cols[i]} {first} {values[i]} OR ({cols[i]} = {values[i]} AND {rec(i + 1)}))"

    body = rec(0)
    if len(cols) == 1:
        return body
    lead = ">=" if first == ">" else "<="
    return f"({cols[0]} {lead} {values[0]} AND {body})"


def key_columns(shape: TableShape) -> list[ColumnInfo]:
    by_name = {c.name: c for c in shape.columns}
    return [by_name[k] for k in shape.key]


def _source(shape: TableShape, query: str | None, sample: tuple[float, int] | None) -> str:
    if query is not None:
        return f"({query}) t"
    if sample is not None and 0 < sample[0] < 100:
        return f"{qualified(shape.object)} SAMPLE ({sample[0]:.6f}) SEED ({int(sample[1])}) t"
    return f"{qualified(shape.object)} t"


def read_sql(shape: TableShape, *, columns: list[ColumnInfo], where: str | None, query: str | None,
             sample: tuple[float, int] | None, lower: bool, upper: bool, after: bool) -> ReadPlan:
    """One keyset page: ``key >= lower AND key < upper AND key > after ORDER BY key FETCH FIRST :batch``."""
    keys = key_columns(shape)
    sorts = [sort_expr(k) for k in keys]
    conds = [f"({where})"] if where else []
    bind_types: dict[str, InputType] = {}
    for prefix, used, op in (("lo", lower, ">="), ("hi", upper, "<"), ("af", after, ">")):
        if not used:
            continue
        conds.append(tuple_predicate(sorts, [key_value_expr(k, f"{prefix}{i}") for i, k in enumerate(keys)],
                                     op))  # type: ignore[arg-type]
        for i, k in enumerate(keys):
            if (t := input_type(k)) is not None:
                bind_types[f"{prefix}{i}"] = t
    select = ", ".join(select_expr(c) for c in columns)
    where_sql = f" WHERE {' AND '.join(conds)}" if conds else ""
    sql = (f"SELECT /* dps:read */ {select} FROM {_source(shape, query, sample)}{where_sql}"  # noqa: S608
           f" ORDER BY {', '.join(sorts)} FETCH FIRST :batch ROWS ONLY")
    return ReadPlan(sql=sql, columns=list(columns), bind_types=bind_types)


def count_sql(shape: TableShape, *, where: str | None, query: str | None,
              sample: tuple[float, int] | None) -> str:
    where_sql = f" WHERE ({where})" if where else ""
    return f"SELECT /* dps:count */ COUNT(*) FROM {_source(shape, query, sample)}{where_sql}"  # noqa: S608


def sample_sql(shape: TableShape, *, columns: list[ColumnInfo], where: str | None, query: str | None,
               percent: float | None, keys_only: bool = False) -> ReadPlan:
    """A random sample for discovery (``keys_only`` for partition boundaries); no locks, no order."""
    cols = key_columns(shape) if keys_only else columns
    if query is None and percent is not None and 0 < percent < 100:
        src = f"{qualified(shape.object)} SAMPLE ({percent:.6f}) t"      # unseeded: a fresh sample
    else:
        src = _source(shape, query, None)                                 # small table or a query
    where_sql = f" WHERE ({where})" if where else ""
    order = f" ORDER BY {', '.join(sort_expr(k) for k in cols)}" if keys_only else ""
    sql = (f"SELECT /* dps:sample */ {', '.join(select_expr(c) for c in cols)} FROM {src}"  # noqa: S608
           f"{where_sql}{order} FETCH FIRST :n ROWS ONLY")
    return ReadPlan(sql=sql, columns=list(cols), bind_types={})


def merge_sql(target: ObjectRef, cols: list[ColumnInfo], keys: list[str]) -> str:
    """Upsert one row per bind set (positional ``:v0..``) on the key — idempotent table loads."""
    src = ", ".join(f"{value_expr(c, f'v{i}')} AS {qcol(c.name)}" for i, c in enumerate(cols))
    on = " AND ".join(f"d.{qcol(k)} = s.{qcol(k)}" for k in keys)
    non_key = [c for c in cols if c.name not in keys]
    update = (" WHEN MATCHED THEN UPDATE SET "
              + ", ".join(f"d.{qcol(c.name)} = s.{qcol(c.name)}" for c in non_key)) if non_key else ""
    names = ", ".join(qcol(c.name) for c in cols)
    vals = ", ".join(f"s.{qcol(c.name)}" for c in cols)
    return (f"MERGE /* dps:merge */ INTO {qualified(target)} d USING (SELECT {src} FROM dual) s "
            f"ON ({on}){update} WHEN NOT MATCHED THEN INSERT ({names}) VALUES ({vals})")


def update_sql(target: ObjectRef, set_cols: list[ColumnInfo], key_cols: list[ColumnInfo]) -> str:
    """In-place update by key; binds ``:v0..`` are the SET values, then the key values."""
    sets = ", ".join(f"d.{qcol(c.name)} = {value_expr(c, f'v{i}')}" for i, c in enumerate(set_cols))
    n = len(set_cols)
    where = " AND ".join(f"d.{qcol(k.name)} = {value_expr(k, f'v{n + i}')}" for i, k in enumerate(key_cols))
    return f"UPDATE /* dps:update */ {qualified(target)} d SET {sets} WHERE {where}"  # noqa: S608


def insert_sql(target: ObjectRef, cols: list[ColumnInfo], *, sequence: tuple[str, str] | None = None) -> str:
    """Append rows; ``sequence = (key column, quoted sequence)`` fills that key from NEXTVAL."""
    names, vals, i = [], [], 0
    for c in cols:
        names.append(qcol(c.name))
        if sequence is not None and c.name == sequence[0]:
            vals.append(f"{sequence[1]}.NEXTVAL")
        else:
            vals.append(value_expr(c, f"v{i}"))
            i += 1
    return (f"INSERT /* dps:insert */ INTO {qualified(target)} ({', '.join(names)}) "
            f"VALUES ({', '.join(vals)})")


def collision_sql(target: ObjectRef, keys: list[ColumnInfo], n: int) -> str:
    """Count how many of ``n`` candidate keys already exist (append offset/token pre-flight)."""
    if len(keys) == 1:
        k = keys[0]
        items = ", ".join(value_expr(k, f"c{j}") for j in range(n))
        cond = f"d.{qcol(k.name)} IN ({items})"
    else:
        cols = ", ".join(f"d.{qcol(k.name)}" for k in keys)
        tuples = ", ".join("(" + ", ".join(value_expr(k, f"c{j}_{i}") for i, k in enumerate(keys)) + ")"
                           for j in range(n))
        cond = f"({cols}) IN ({tuples})"
    return f"SELECT /* dps:collisions */ COUNT(*) FROM {qualified(target)} d WHERE {cond}"  # noqa: S608


def _ledger(owner: str) -> str:
    return f'{quote_ident(owner, "oracle")}."{LEDGER_TABLE}"'


def ledger_ddl(owner: str) -> str:
    """Create the write ledger once; ORA-00955 (exists) is ignored — Oracle 19c has no IF NOT EXISTS."""
    ddl = (f"CREATE TABLE {_ledger(owner)} (JOB_ID VARCHAR2(40) NOT NULL, PARTITION_NO NUMBER(10) NOT NULL, "
           "BATCH_NO NUMBER(10) NOT NULL, LAST_KEY CLOB NOT NULL, "
           "UPDATED_AT TIMESTAMP(6) DEFAULT SYSTIMESTAMP NOT NULL, "
           f'CONSTRAINT "{LEDGER_TABLE}_PK" PRIMARY KEY (JOB_ID, PARTITION_NO))')
    escaped = ddl.replace("'", "''")
    return ("BEGIN EXECUTE IMMEDIATE '" + escaped + "'; "
            "EXCEPTION WHEN OTHERS THEN IF SQLCODE != -955 THEN RAISE; END IF; END;")


def ledger_mark_sql(owner: str) -> str:
    """Record ``(job, partition) -> (batch, last key)``; matches 0 rows unless the batch advances.

    Run first in the batch's transaction: it takes the ledger row lock (serialising a zombie writer that
    lost its lease) and its row count is the in-target fence — 0 means another writer already applied
    this batch, so the caller rolls back.
    """
    return (f"MERGE /* dps:ledger */ INTO {_ledger(owner)} l USING (SELECT :1 AS JOB_ID, :2 AS PARTITION_NO, "
            ":3 AS BATCH_NO, :4 AS LAST_KEY FROM dual) s "
            "ON (l.JOB_ID = s.JOB_ID AND l.PARTITION_NO = s.PARTITION_NO) "
            "WHEN MATCHED THEN UPDATE SET l.BATCH_NO = s.BATCH_NO, l.LAST_KEY = s.LAST_KEY, "
            "l.UPDATED_AT = SYSTIMESTAMP WHERE s.BATCH_NO > l.BATCH_NO "
            "WHEN NOT MATCHED THEN INSERT (JOB_ID, PARTITION_NO, BATCH_NO, LAST_KEY, UPDATED_AT) "
            "VALUES (s.JOB_ID, s.PARTITION_NO, s.BATCH_NO, s.LAST_KEY, SYSTIMESTAMP)")


def ledger_read_sql(owner: str) -> str:
    return (f"SELECT BATCH_NO, LAST_KEY FROM {_ledger(owner)} "  # noqa: S608
            "WHERE JOB_ID = :job AND PARTITION_NO = :part")


def ledger_purge_sql(owner: str) -> str:
    return f"DELETE FROM {_ledger(owner)} WHERE JOB_ID = :job"  # noqa: S608


def oracle_ddl_type(target: ColumnInfo, source: ColumnInfo | None) -> str:
    """The column type of a created target: the source type when the method output still fits it."""
    if (source is not None and family_of(source.data_type.split("(")[0]) != "unsupported"
            and target.kind == source.kind and (target.length or 0) <= (source.length or 0)):
        return source.data_type
    match target.kind:
        case "int":
            return f"NUMBER({target.precision})" if target.precision else "NUMBER(38)"
        case "decimal":
            return f"NUMBER({target.precision},{target.scale or 0})" if target.precision else "NUMBER"
        case "float":
            return "BINARY_DOUBLE"
        case "text" | "char":
            n = target.length or 4000
            return f"VARCHAR2({n} CHAR)" if n <= TEXT_VARCHAR_MAX_CHARS else "CLOB"
        case "timestamp":
            p = 6 if target.ts_precision is None else target.ts_precision
            return "DATE" if p == 0 else f"TIMESTAMP({p})"
        case "date":
            return "DATE"
        case "bool":
            return "NUMBER(1)"
        case "binary" | "uuid":
            return f"RAW({target.length or 2000})"
        case "lob_text" | "json":
            return "CLOB"
        case "lob_binary":
            return "BLOB"
    raise ValueError("column_unsupported")


def create_table_sql(target: ObjectRef, cols: list[tuple[ColumnInfo, ColumnInfo | None]],
                     keys: list[str]) -> str:
    """CREATE TABLE for a missing ``table`` target: types per ``oracle_ddl_type``, PK on the key only."""
    parts = []
    for tgt, src in cols:
        null = " NOT NULL" if tgt.name in keys else ""
        parts.append(f"{qcol(tgt.name)} {oracle_ddl_type(tgt, src)}{null}")
    if keys:
        tag = hashlib.sha256(qualified(target).encode()).hexdigest()[:8].upper()
        parts.append(f'CONSTRAINT "DPS_PK_{tag}" PRIMARY KEY ({", ".join(qcol(k) for k in keys)})')
    return f"CREATE TABLE {qualified(target)} ({', '.join(parts)})"


# --- ROWID codec ----------------------------------------------------------------------------------
_B64: Final = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
_IDX: Final = {ch: i for i, ch in enumerate(_B64)}


def _dec(text: str) -> int:
    n = 0
    for ch in text:
        n = n * 64 + _IDX[ch]
    return n


def _enc(n: int, width: int) -> str:
    out = []
    for _ in range(width):
        n, r = divmod(n, 64)
        out.append(_B64[r])
    if n:
        raise ValueError("component out of range")
    return "".join(reversed(out))


def rowid_to_key(rowid: str) -> int:
    """Pack an extended ROWID (OOOOOOFFFBBBBBBRRR) into an int that sorts like Oracle's ROWID order.

    Oracle orders ROWIDs by (data object, relative file, block, row); the base64 text does not sort
    that way in Python (its alphabet is not ASCII-ordered), so partition boundaries use this integer.
    """
    if len(rowid) != 18 or any(ch not in _IDX for ch in rowid):
        raise ValueError("not an extended rowid")
    obj, fno, blk, row = _dec(rowid[0:6]), _dec(rowid[6:9]), _dec(rowid[9:15]), _dec(rowid[15:18])
    return (((obj << 18 | fno) << 36 | blk) << 18) | row


def key_to_rowid(key: int) -> str:
    row = key & (2**18 - 1)
    key >>= 18
    blk = key & (2**36 - 1)
    key >>= 36
    fno = key & (2**18 - 1)
    obj = key >> 18
    return _enc(obj, 6) + _enc(fno, 3) + _enc(blk, 6) + _enc(row, 3)
```

- [ ] **Step 5: Run tests and gates**

Run: `pytest -q tests/connectors/test_oracle_sql.py && ruff check dps tests tools && ruff format --check dps tests tools && mypy dps && pytest -q`
Expected: all `test_oracle_sql.py` cases pass (16 type-table rows, 9 predicate combinations brute-forced
over a 3-valued domain, statement and codec tests); full suite green.

- [ ] **Step 6: Commit**

```bash
git add dps/layers/connectors/oracle_sql.py dps/layers/connectors/base.py tests/connectors/test_oracle_sql.py docs/superpowers/plans/2026-10-11-m1-m5-contract.md && git commit -m "feat(oracle): closed type table, keyset SQL builders, ledger statements, ROWID codec" -- dps/layers/connectors/oracle_sql.py dps/layers/connectors/base.py tests/connectors/test_oracle_sql.py docs/superpowers/plans/2026-10-11-m1-m5-contract.md
```

---
### Task 4: Oracle session pool with the Wega rules, value-free driver errors, fetch types

**Files:**
- Create: `dps/layers/connectors/oracle.py` (pool, errors, output type handler — the connector and writer join it in Tasks 5–6)
- Modify (M2, only if missing): `dps/layers/connectors/base.py` (`ConnectorError.detail` attribute)
- Test: `tests/connectors/test_oracle_session.py`

**Interfaces introduced:**

```python
def driver_code(exc: BaseException) -> str                      # "ORA-00942" | "DPY-6005" | "TNS-12541" | "ORA-unknown"
def connector_error(code: str, exc: BaseException) -> ConnectorError   # connect-class codes -> "connection_failed"
def output_type_handler(cursor: oracledb.Cursor, metadata: oracledb.FetchInfo) -> oracledb.Var | None
def easy_connect(params: ConnectionParams) -> str                # "host:port/service", validated
class OraclePool:
    def __init__(self, *, user: str, password: str, dsn: str, size: int, call_timeout_ms: int,
                 connect_timeout_s: float, module: str, client_identifier: str | None) -> None
    @contextlib.contextmanager
    def acquire(self, *, action: str, call_timeout_ms: int | None = None) -> Iterator[oracledb.Connection]
    def close(self) -> None
```

**Behaviours:** the pool is created lazily on first acquire with `min=0, max=size, increment=1,
getmode=TIMEDWAIT, wait_timeout=30000, expire_time=1, tcp_connect_timeout=connect_timeout_s` and a
`session_callback` that sets `NLS_SORT = BINARY NLS_COMP = BINARY` once per new session (so Oracle's key
order equals Python's code-point order, which M2's partitioner uses to cut ranges); every checkout sets
`call_timeout`, `module` (= `settings.oracle_module`), `action` (= the job phase / operation) and
`client_identifier` (= the job id), and every release **rolls back** whatever the block left open; a
session whose rollback fails is dropped, not reused. `repr` never shows the password or DSN. Driver errors
become `ConnectorError(code, <driver code>, retryable=…)` from the driver's error *code* only;
connection-class codes map to `connection_failed` (retryable). CLOB/NCLOB/BLOB are fetched whole as `str`/`bytes` (the per-cursor equivalent of
`oracledb.defaults.fetch_lobs = False`, contract §4.2), `IS JSON` VARCHAR2 stays text (python-oracledb
otherwise returns a dict), and NUMBER without scale 0 is fetched as `Decimal` (never float).

- [ ] **Step 1: Write the failing tests** — `tests/connectors/test_oracle_session.py`:
```python
from __future__ import annotations

import decimal
from typing import Any

import pytest

oracledb = pytest.importorskip("oracledb")

from dps.layers.connectors import oracle as ora  # noqa: E402
from dps.layers.connectors.base import ConnectorError  # noqa: E402
from dps.jobs_models import ConnectionParams  # noqa: E402


class FakeConn:
    def __init__(self, broken: bool = False) -> None:
        self.broken, self.rolled_back = broken, 0
        self.call_timeout = self.module = self.action = self.client_identifier = None

    def rollback(self) -> None:
        if self.broken:
            raise oracledb.DatabaseError("connection lost")
        self.rolled_back += 1


class FakePool:
    def __init__(self, conn: FakeConn) -> None:
        self.conn, self.released, self.dropped, self.closed = conn, [], [], False

    def acquire(self) -> FakeConn:
        return self.conn

    def release(self, c: FakeConn) -> None:
        self.released.append(c)

    def drop(self, c: FakeConn) -> None:
        self.dropped.append(c)

    def close(self, force: bool = False) -> None:
        self.closed = force


def _pool(monkeypatch: pytest.MonkeyPatch, conn: FakeConn) -> tuple[ora.OraclePool, dict[str, Any], FakePool]:
    captured: dict[str, Any] = {}
    fake = FakePool(conn)

    def create_pool(**kw: Any) -> FakePool:
        captured.update(kw)
        return fake

    monkeypatch.setattr(oracledb, "create_pool", create_pool)
    pool = ora.OraclePool(user="dps_ro", password="SENTINEL-PW", dsn="db.internal:1521/CRMPRD", size=4,
                          call_timeout_ms=30000, connect_timeout_s=10.0, module="dps", client_identifier="j_01")
    return pool, captured, fake


def test_pool_is_lazy_and_follows_the_wega_rules(monkeypatch: pytest.MonkeyPatch) -> None:
    pool, captured, _ = _pool(monkeypatch, FakeConn())
    assert captured == {}                                   # nothing opened at construction
    with pool.acquire(action="partitions"):
        pass
    assert captured["expire_time"] == 1 and captured["tcp_connect_timeout"] == 10.0
    assert (captured["min"], captured["max"], captured["increment"]) == (0, 4, 1)
    assert captured["getmode"] == oracledb.POOL_GETMODE_TIMEDWAIT and captured["wait_timeout"] == 30000
    assert captured["session_callback"] is ora._init_session


def test_acquire_tags_the_session_and_always_rolls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = FakeConn()
    pool, _, fake = _pool(monkeypatch, conn)
    with pool.acquire(action="partitions") as c:
        assert (c.call_timeout, c.module, c.action, c.client_identifier) == (30000, "dps", "partitions", "j_01")
    assert conn.rolled_back == 1 and fake.released == [conn]
    with pool.acquire(action="count", call_timeout_ms=300000) as c:
        assert c.call_timeout == 300000


def test_broken_sessions_are_dropped_not_reused(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = FakeConn(broken=True)
    pool, _, fake = _pool(monkeypatch, conn)
    with pool.acquire(action="read"):
        pass
    assert fake.dropped == [conn] and fake.released == []


def test_repr_hides_password_and_dsn(monkeypatch: pytest.MonkeyPatch) -> None:
    pool, _, _ = _pool(monkeypatch, FakeConn())
    assert "SENTINEL" not in repr(pool) and "CRMPRD" not in repr(pool)


class _Err:
    def __init__(self, full_code: str) -> None:
        self.full_code = full_code
        self.message = f"{full_code}: unique constraint violated SENTINEL-VALUE-42"


def test_driver_errors_become_value_free() -> None:
    try:
        try:
            raise oracledb.IntegrityError(_Err("ORA-00001"))
        except oracledb.Error as exc:
            raise ora.connector_error("target_error", exc) from None
    except ConnectorError as err:
        assert err.code == "target_error" and err.detail == "ORA-00001"
        assert str(err) == "target_error: ORA-00001" and "SENTINEL" not in repr(err)
        assert err.__cause__ is None and err.__suppress_context__ is True


@pytest.mark.parametrize("code", ["ORA-01017", "ORA-12514", "ORA-12541", "DPY-6005", "ORA-28000"])
def test_connection_class_codes(code: str) -> None:
    assert ora.connector_error("source_error", oracledb.DatabaseError(_Err(code))).code == "connection_failed"


def test_unknown_driver_shapes_never_leak() -> None:
    assert ora.driver_code(oracledb.DatabaseError("free text SENTINEL")) == "ORA-unknown"


class _Meta:
    def __init__(self, type_code: Any, precision: int = 0, scale: int = 0, is_json: bool = False) -> None:
        self.type_code, self.precision, self.scale, self.is_json = type_code, precision, scale, is_json


class _Cur:
    arraysize = 50

    def var(self, typ: Any, arraysize: int) -> tuple[Any, int]:
        return (typ, arraysize)


@pytest.mark.parametrize(
    "meta,expected",
    [
        (_Meta(oracledb.DB_TYPE_CLOB), (oracledb.DB_TYPE_LONG, 50)),
        (_Meta(oracledb.DB_TYPE_CLOB, is_json=True), (oracledb.DB_TYPE_LONG, 50)),
        (_Meta(oracledb.DB_TYPE_NCLOB), (oracledb.DB_TYPE_LONG_NVARCHAR, 50)),
        (_Meta(oracledb.DB_TYPE_BLOB), (oracledb.DB_TYPE_LONG_RAW, 50)),
        (_Meta(oracledb.DB_TYPE_VARCHAR, is_json=True), (oracledb.DB_TYPE_VARCHAR, 50)),
        (_Meta(oracledb.DB_TYPE_NUMBER, precision=14, scale=2), (decimal.Decimal, 50)),
        (_Meta(oracledb.DB_TYPE_NUMBER, precision=0, scale=-127), (decimal.Decimal, 50)),
        (_Meta(oracledb.DB_TYPE_NUMBER, precision=12, scale=0), None),
        (_Meta(oracledb.DB_TYPE_VARCHAR), None),
    ],
)
def test_output_type_handler(meta: _Meta, expected: Any) -> None:
    assert ora.output_type_handler(_Cur(), meta) == expected  # type: ignore[arg-type]


def test_easy_connect_validates_host_and_service() -> None:
    assert ora.easy_connect(ConnectionParams(host="oracle", port=1521, service_name="FREEPDB1")) == "oracle:1521/FREEPDB1"
    for bad in (ConnectionParams(host="h?x=1", port=1521, service_name="S"),
                ConnectionParams(host="h", port=1521, service_name="S)(ADDRESS=")):
        with pytest.raises(ConnectorError):
            ora.easy_connect(bad)
```

- [ ] **Step 2: Run to confirm failure** — `pytest -q tests/connectors/test_oracle_session.py` → FAIL
  (`ModuleNotFoundError: dps.layers.connectors.oracle`).

- [ ] **Step 3: Write `dps/layers/connectors/oracle.py` (part 1)**
```python
"""Oracle connector over python-oracledb thin mode (contract §4; spec §7 engine notes).

The pool is synchronous on purpose and used only from worker threads (``asyncio.to_thread``): the Wega
measurement showed blocking driver work on the event loop producing silent duplicates, and a
synchronous pool used only off the loop cannot do that. Pool options are crash-point decisions, not
tuning: ``expire_time=1`` detects a dead peer within a minute, ``call_timeout`` turns a hung call into
an error, ``tcp_connect_timeout`` bounds a black-holed listener, and module/action/client_identifier
make every DPS session attributable in the DBA's audit.
"""
from __future__ import annotations

import contextlib
import decimal
import re
import threading
from collections.abc import Iterator
from typing import Any, Final

import oracledb

from dps.jobs_models import ConnectionParams
from dps.layers.connectors.base import ConnectorError

_EXPIRE_TIME_MIN: Final = 1
_POOL_WAIT_TIMEOUT_MS: Final = 30_000
_DRIVER_CODE: Final = re.compile(r"(ORA|DPY|TNS)-\d{4,5}")
_HOST: Final = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?|\[[0-9A-Fa-f:]{2,39}\]")
_SERVICE: Final = re.compile(r"[A-Za-z][A-Za-z0-9_.$#-]{0,127}")
#: Codes that mean "cannot reach or log in": invalid credentials, unknown service, no listener,
#: connect timeout, locked account, and the thin driver's connect failures.
_CONNECT_CODES: Final = frozenset({
    "ORA-01017", "ORA-12505", "ORA-12514", "ORA-12541", "ORA-12170", "ORA-28000", "ORA-28001",
    "DPY-6000", "DPY-6001", "DPY-6005", "DPY-4011", "TNS-12541",
})
INPUT_TYPES: Final[dict[str, Any]] = {
    "char": oracledb.DB_TYPE_CHAR, "nchar": oracledb.DB_TYPE_NCHAR, "nvarchar": oracledb.DB_TYPE_NVARCHAR,
    "clob": oracledb.DB_TYPE_CLOB, "nclob": oracledb.DB_TYPE_NCLOB, "blob": oracledb.DB_TYPE_BLOB,
}


def driver_code(exc: BaseException) -> str:
    """The ``ORA-nnnnn`` / ``DPY-nnnn`` code of a driver error — never its message.

    python-oracledb carries an ``_Error`` object as ``args[0]`` whose ``full_code`` is the code; the
    message may quote a value (a constraint violation can echo a key), so it is never read.
    """
    err = exc.args[0] if getattr(exc, "args", None) else None
    full = getattr(err, "full_code", None)
    if isinstance(full, str) and _DRIVER_CODE.fullmatch(full):
        return full
    return "ORA-unknown"


def connector_error(code: str, exc: BaseException) -> ConnectorError:
    """A value-free ConnectorError for ``exc``; raise it ``from None`` so the driver text is dropped."""
    dc = driver_code(exc)
    mapped = "connection_failed" if dc in _CONNECT_CODES else code
    return ConnectorError(mapped, dc, retryable=mapped == "connection_failed")


def output_type_handler(cursor: oracledb.Cursor, metadata: oracledb.FetchInfo) -> Any:
    """Fetch types for every DPS read (contract §4.2 Oracle notes).

    CLOB/NCLOB/BLOB come back whole as ``str``/``bytes`` (never a LOB locator addressed by offsets, which
    count UTF-16 units); a VARCHAR2 with an IS JSON check stays text (python-oracledb would return a dict);
    NUMBER with a non-zero or unknown scale is a Decimal (never a float, which corrupts ids above 2**53).
    """
    code = metadata.type_code
    if code is oracledb.DB_TYPE_CLOB:
        return cursor.var(oracledb.DB_TYPE_LONG, arraysize=cursor.arraysize)
    if code is oracledb.DB_TYPE_NCLOB:
        return cursor.var(oracledb.DB_TYPE_LONG_NVARCHAR, arraysize=cursor.arraysize)
    if code is oracledb.DB_TYPE_BLOB:
        return cursor.var(oracledb.DB_TYPE_LONG_RAW, arraysize=cursor.arraysize)
    if metadata.is_json:
        return cursor.var(code, arraysize=cursor.arraysize)
    if code is oracledb.DB_TYPE_NUMBER and (metadata.scale != 0 or metadata.precision == 0):
        return cursor.var(decimal.Decimal, arraysize=cursor.arraysize)
    return None


def easy_connect(params: ConnectionParams) -> str:
    """``host:port/service`` from validated parameters (a ``dsn`` secret file overrides it)."""
    host, service = params.host or "", params.service_name or ""
    if not _HOST.fullmatch(host) or not _SERVICE.fullmatch(service):
        raise ConnectorError("connection_invalid")
    return f"{host}:{params.port or 1521}/{service}"


def _init_session(conn: oracledb.Connection, requested_tag: str | None) -> None:
    """Once per new pooled session: binary collation, so DB key order == Python code-point order."""
    with conn.cursor() as cur:
        cur.execute("ALTER SESSION SET NLS_SORT = BINARY NLS_COMP = BINARY")


class OraclePool:
    """A fixed-ceiling python-oracledb pool, created on first use; checkouts are tagged and rolled back."""

    def __init__(self, *, user: str, password: str, dsn: str, size: int, call_timeout_ms: int,
                 connect_timeout_s: float, module: str, client_identifier: str | None) -> None:
        if size < 1:
            raise ValueError("Oracle pool size must be at least 1")
        self._user, self._password, self._dsn = user, password, dsn
        self._size, self._call_timeout_ms = size, call_timeout_ms
        self._connect_timeout_s, self._module = connect_timeout_s, module
        self._client_identifier = (client_identifier or "")[:64]
        self._pool: oracledb.ConnectionPool | None = None
        self._lock = threading.Lock()

    def __repr__(self) -> str:
        return f"OraclePool(user={self._user!r}, size={self._size})"

    def _ensure(self) -> oracledb.ConnectionPool:
        with self._lock:
            if self._pool is None:
                self._pool = oracledb.create_pool(
                    user=self._user, password=self._password, dsn=self._dsn, min=0, max=self._size,
                    increment=1, getmode=oracledb.POOL_GETMODE_TIMEDWAIT,
                    wait_timeout=_POOL_WAIT_TIMEOUT_MS, expire_time=_EXPIRE_TIME_MIN,
                    tcp_connect_timeout=self._connect_timeout_s, session_callback=_init_session,
                )
            return self._pool

    @contextlib.contextmanager
    def acquire(self, *, action: str, call_timeout_ms: int | None = None) -> Iterator[oracledb.Connection]:
        """Check a session out, tagged; whatever the block left open is rolled back on release.

        Synchronous: call it only from a worker thread.
        """
        pool = self._ensure()
        conn = pool.acquire()
        try:
            conn.call_timeout = call_timeout_ms or self._call_timeout_ms
            conn.module = self._module
            conn.action = action[:64]
            conn.client_identifier = self._client_identifier
            yield conn
        finally:
            try:
                conn.rollback()
            except oracledb.Error:
                with contextlib.suppress(oracledb.Error):
                    pool.drop(conn)
            else:
                pool.release(conn)

    def close(self) -> None:
        """Close the pool, forcing its sessions closed. Safe to call twice."""
        with self._lock:
            pool, self._pool = self._pool, None
        if pool is not None:
            with contextlib.suppress(oracledb.Error):
                pool.close(force=True)
```
M2's `ConnectorError(code, detail="", *, retryable=False)` renders `"<code>: <detail>"`; M3 always passes
the driver *code* as `detail`. If M2's class does not keep `detail` as an attribute, add `self.detail =
detail` in its `__init__` (one line, M2 touch point, `tests/connectors/test_base.py` extended with
`assert ConnectorError("x", "ORA-1").detail == "ORA-1"`).

- [ ] **Step 4: Run tests and gates**

Run: `pytest -q tests/connectors/test_oracle_session.py && ruff check dps tests tools && ruff format --check dps tests tools && mypy dps && pytest -q`
Expected: all session tests pass (no database needed); suite green.

- [ ] **Step 5: Commit**

```bash
git add dps/layers/connectors/oracle.py tests/connectors/test_oracle_session.py && git commit -m "feat(oracle): thin-mode session pool with the Wega rules, value-free driver errors, fetch types" -- dps/layers/connectors/oracle.py tests/connectors/test_oracle_session.py
```

---
### Task 5: `OracleConnector` — test, list, describe, count, sample, keyset reads

**Files:**
- Modify: `dps/layers/connectors/oracle.py` (append part 2)
- Test: `tests/connectors/test_oracle_connector.py` (markers `integration`, `oracle`), `tests/connectors/oracle_fixtures.py` (DDL + row helpers shared by Oracle tests)

**Interfaces introduced:**

```python
class OracleConnector:                                     # implements base.Connector (contract §4.2)
    engine: Final = "oracle"
    def __init__(self, *, pool: OraclePool, settings: Settings, action: str) -> None
    @classmethod
    def from_secret(cls, secret: Mapping[str, str], params: ConnectionParams, settings: Settings, *,
                    action: str, client_id: str | None) -> OracleConnector
    async def open(self) -> None
    async def close(self) -> None
    async def test(self, *, role: Literal["source", "target"], obj: ObjectRef | None = None,
                   write_mode: WriteMode | None = None) -> ConnectionTestResult
    async def list_objects(self, owner: str | None = None, *, limit: int = 500) -> list[ObjectRef]
    async def describe(self, obj: ObjectRef) -> TableShape
    async def count(self, shape: TableShape, selection: Selection, params: dict[str, JsonScalar]) -> int
    async def sample(self, shape, selection, params, n: int) -> list[Row]
    async def sample_keys(self, shape, selection, params, n: int, *,
                          upper: tuple[Any, ...] | None = None) -> list[tuple[Any, ...]]
    async def key_at_offset(self, shape, selection, params, offset: int) -> tuple[Any, ...] | None   # M2 addition
    def read_partition(self, shape, selection, params, key_range: KeyRange, *,          # KeyRange: encoded keys
                       after_key: tuple[Any, ...] | None, batch: int) -> AsyncIterator[list[Row]]
    async def writer(self, target: ResolvedTarget, source_shape: TableShape) -> OracleWriter   # Task 6
    async def lookup_values(self, shape, join: ColumnInfo, values: list[Any],
                            attributes: list[ColumnInfo]) -> list[Row]                       # Task 15
```

**Behaviours:**
- `describe` reads `ALL_TAB_COLS` (visible, non-virtual, user-generated), the key from the first enabled
  PK, else the first unique constraint or unique index whose columns are all NOT NULL, else **ROWID**
  (`key=["ROWID"]`, `key_source="rowid"`, the pseudo column appended last). An owner of `None` resolves to
  the session user. A table the session cannot see raises `ConnectorError("object_not_found")`.
- Row values: `NUMBER(p,0)` → `int`; other NUMBER → `Decimal`; DATE/TIMESTAMP(≤6) → naive `datetime`;
  TIMESTAMP(7–9) → FF9 text; TIMESTAMP WITH TIME ZONE → FF9+offset text; CHAR → padded `str`; CLOB → whole
  `str`; BLOB/RAW → `bytes`; ROWID pseudo → packed `int`.
- `count`/`sample`/`sample_keys` run with `call_timeout = connector_statement_timeout_seconds`; reads use
  `oracle_call_timeout_ms`. `count` honours `selection.max_rows` (returns the minimum).
- `sample` draws `SAMPLE(p)` (p ≈ 200·n/rows, none for small tables) then `FETCH FIRST :n`; `sample_keys`
  returns `[]` for NVARCHAR2/NCHAR keys (one partition: Oracle's UTF-16 binary order differs from Python's
  code-point order for supplementary characters, so cut points could overlap).
- `read_partition` pages per batch: one statement per page (`FETCH FIRST :batch`), a fresh pooled session per
  page (no long-held cursor, no ORA-01555 exposure), `arraysize = prefetchrows = batch`; the next page starts
  strictly after the last row's key. Binds are named; CHAR key binds are typed CHAR and padded; TIMESTAMP(7–9)
  keys bind as FF9 text; ROWID keys bind as `CHARTOROWID(text)`. `selection.where` binds come from `params`
  (missing → `SelectionError("selection_param_missing")`); `selection.query` binds are the `:name`
  placeholders sqlglot finds in the query; `selection.sample` uses `SAMPLE(p) SEED(seed or 0)` so a resumed
  partition reads the same rows.
- `test()` reports value-free checks: `connect`, `select_source`, `insert_target`, `update_target`,
  `create_target`, `create_ledger` (each `{"name", "ok"}` plus `"code"` on failure) and capabilities
  `{transactions, merge, identity, sample, rowid: true, copy: false}`.

- [ ] **Step 1: Shared test helpers** — `tests/connectors/oracle_fixtures.py`:
```python
"""DDL and rows for Oracle integration tests — synthetic values only (RFC 2606 e-mails, test ranges)."""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

from tests.fixtures.m3_services import OracleTestSchema

CUSTOMERS_DDL = """CREATE TABLE "{o}"."CUSTOMERS" (
  CUSTOMER_ID NUMBER(12) NOT NULL, FULL_NAME VARCHAR2(120 CHAR) NOT NULL, TAX_ID CHAR(10),
  EMAIL VARCHAR2(254), DOB DATE, SEGMENT VARCHAR2(16) NOT NULL, INCOME NUMBER(14,2), NOTES CLOB,
  CREATED_AT TIMESTAMP(6) DEFAULT SYSTIMESTAMP NOT NULL, LAST_CONTACT_AT TIMESTAMP(9),
  CONSTRAINT "{o}_CUST_PK" PRIMARY KEY (CUSTOMER_ID))"""


def customer_rows(n: int, start: int = 10_000_001) -> list[tuple[object, ...]]:
    base = dt.datetime(2025, 1, 1, 9, 0, 0)
    return [(start + i, f"Person {i:05d}", f"ABCDE{i % 10000:04d}F", f"person.{i}@example.com",
             dt.datetime(1980, 1, 1) + dt.timedelta(days=i), "retail" if i % 3 else "premium",
             Decimal(f"{1000 + i}.25"), f"Note {i} for person.{i}@example.com", base + dt.timedelta(seconds=i),
             f"2025-02-01T10:00:00.{i % 1000:06d}123") for i in range(n)]


def create_customers(conn, schema: OracleTestSchema, n: int) -> None:  # noqa: ANN001
    with conn.cursor() as cur:
        cur.execute(CUSTOMERS_DDL.format(o=schema.owner))
        cur.executemany(
            f'INSERT INTO "{schema.owner}"."CUSTOMERS" (CUSTOMER_ID, FULL_NAME, TAX_ID, EMAIL, DOB, SEGMENT, '
            "INCOME, NOTES, CREATED_AT, LAST_CONTACT_AT) VALUES (:1, :2, :3, :4, :5, :6, :7, :8, :9, "
            "TO_TIMESTAMP(:10, 'YYYY-MM-DD\"T\"HH24:MI:SS.FF9'))",
            customer_rows(n),
        )
    conn.commit()


def ranges_from_keys(keys: list[tuple[object, ...]], parts: int, keycols: list) -> list:  # noqa: ANN001
    """``KeyRange``s cut at sampled-key quantiles (encoded, as M2's partitioner stores them)."""
    from dps.layers.connectors.base import KeyRange, encode_key

    cuts = sorted(set(keys))
    step = max(1, len(cuts) // parts)
    bounds = [cuts[i] for i in range(step, len(cuts), step)][: parts - 1]
    edges: list = [None, *bounds, None]
    return [KeyRange(lower=None if lo is None else encode_key(lo, keycols),
                     upper=None if hi is None else encode_key(hi, keycols))
            for lo, hi in zip(edges[:-1], edges[1:], strict=True)]
```

- [ ] **Step 2: Write the failing integration tests** — `tests/connectors/test_oracle_connector.py`
  (module-level `pytestmark = [pytest.mark.integration, pytest.mark.oracle]`; a `connector(schema, user)`
  helper builds `OracleConnector.from_secret({"username", "password", "dsn"}, ConnectionParams(), settings,
  action="test", client_id="j_test")`; every test closes its connector). Test cases and their exact assertions:

| Test | Arrange | Assert |
|---|---|---|
| `test_describe_customers_like_table` | `create_customers(n=5)` | `shape.key == ["CUSTOMER_ID"]`, `key_source == "primary_key"`; kinds `CUSTOMER_ID:int, FULL_NAME:text(len 120), TAX_ID:char(len 10), DOB:timestamp(ts 0), INCOME:decimal, NOTES:lob_text, CREATED_AT:timestamp(ts 6, has_default), LAST_CONTACT_AT:timestamp(ts 9)`; no column is `pseudo` |
| `test_describe_unique_fallback_and_identity` | table `U(A NUMBER NOT NULL, B VARCHAR2(8), CONSTRAINT U_UQ UNIQUE (A))`; table `I(ID NUMBER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY, X VARCHAR2(4))` | `U`: `key == ["A"]`, `key_source == "unique_index"`; `I`: column `ID` has `is_identity is True` |
| `test_describe_keyless_table_uses_rowid` | table `H(A NUMBER, B VARCHAR2(8))` | `key == ["ROWID"]`, `key_source == "rowid"`, last column `pseudo and is_key` |
| `test_describe_missing_table` | — | `ConnectorError` with `code == "object_not_found"` |
| `test_list_objects` | tables `CUSTOMERS`, `H` + ledger created via `osql.ledger_ddl(owner)` | `[o.name for o in await c.list_objects(owner)] == ["CUSTOMERS", "H"]` (ledger excluded, sorted) |
| `test_count_where_binds_and_max_rows` | 30 customers (10 premium) | `count(Selection(where="SEGMENT = :seg", params={"seg": "premium"}))` → 10; with `max_rows=4` → 4; missing param → `SelectionError` `selection_param_missing` |
| `test_sample_returns_python_types` | 50 customers | each row: `CUSTOMER_ID` `int`, `INCOME` `Decimal`, `NOTES` `str`, `DOB` `datetime`, `TAX_ID` length 10, `LAST_CONTACT_AT` `str` matching `^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{9}$`; `len(rows) <= 20` for `n=20` |
| `test_read_partition_pages_all_rows_once` | 250 customers, batch 37 | concatenated keys == sorted keys of all 250; number of yielded batches == 7 |
| `test_read_partition_respects_bounds` | 100 customers, `KeyRange(lower=encode_key((10000021,), keycols), upper=encode_key((10000041,), keycols))` | exactly keys 10000021..10000040 |
| `test_key_at_offset_and_bounded_sample_keys` | 100 customers | `key_at_offset(..., 10) == (10000011,)`; offset 500 → `None`; `sample_keys(n=50, upper=(10000051,))` returns only keys `< 10000051` |
| `test_composite_key_partition_coverage` | table `E(C NUMBER(6), N NUMBER(6), V VARCHAR2(8), PRIMARY KEY (C, N))` 20×15 rows | ranges from `ranges_from_keys(sample_keys(n=40), 5, shape.key_columns())`; union of all partitions == all 300 rows; no row twice |
| `test_string_keys_order_like_python` | `S(K VARCHAR2(20) PRIMARY KEY)` keys `["a", "B", "b", "A1", "_x", "Z", "aa", "0", "~"]` | paging with batch 2 yields `sorted(keys)` (Python order) — proves the session's BINARY collation |
| `test_query_selection_pages` | 40 customers | `Selection(query='SELECT CUSTOMER_ID, FULL_NAME FROM "<o>"."CUSTOMERS" WHERE SEGMENT = :seg', params={"seg": "retail"})` pages all 26/27 retail rows (exact count from data), rows carry only those 2 columns |
| `test_sample_spec_is_repeatable` | 200 customers | `Selection(sample=SampleSpec(percent=40, seed=7))` read twice → identical key lists |
| `test_reads_never_lock_rows` | 20 customers | after the first page of `read_partition` (batch 5) is received and while the generator is suspended, a second session with `call_timeout=2000` runs `UPDATE … SET SEGMENT = 'x' WHERE CUSTOMER_ID = <first key>` and commits — no `DPY-4024`/timeout |
| `test_session_is_tagged_and_binary` | — | through `pool.acquire(action="partitions")`: `SYS_CONTEXT('USERENV','MODULE') == "dps"`, `ACTION == "partitions"`, `CLIENT_IDENTIFIER == "j_test"`; `NLS_SESSION_PARAMETERS` has `NLS_SORT = BINARY`, `NLS_COMP = BINARY` |
| `test_connection_test_as_read_only_user` | grant SELECT on `CUSTOMERS` to the RO user | `test(role="source", obj=…)` → `ok` and `select_source` ok; `test(role="target", obj=…, write_mode="in_place")` → `ok is False`, `update_target` not ok; result JSON contains no password/DSN |
| `test_bad_password_is_connection_failed` | wrong password | `open()` raises `ConnectorError` with `code == "connection_failed"`, `detail == "ORA-01017"`; message has no password |

- [ ] **Step 3: Run to confirm failure** — `docker compose --profile oracle up -d oracle` (healthy), then
  `DPS_TEST_REQUIRE_SERVICES=1 pytest -q -m oracle tests/connectors/test_oracle_connector.py` → FAIL
  (`AttributeError: module 'dps.layers.connectors.oracle' has no attribute 'OracleConnector'`).

- [ ] **Step 4: Append `OracleConnector` to `dps/layers/connectors/oracle.py`**
```python
import asyncio
import json
from collections.abc import AsyncIterator, Callable, Mapping
from datetime import datetime
from typing import Literal, TypeVar

import sqlglot
from sqlglot import exp

from dps.config import Settings
from dps.layers.connectors import oracle_sql as osql
from dps.layers.connectors.base import (
    ColumnInfo, ConnectionTestResult, JsonScalar, KeyRange, ObjectRef, Row, TableShape, decode_key,
)
from dps.layers.connectors.selection import (
    Selection, SelectionError, compile_query, compile_where, quote_ident, validate_selection,
)
from dps.observability import CONNECTOR_CALLS, get_logger

logger = get_logger(__name__)
T = TypeVar("T")
_CAPS: Final = {"transactions": True, "merge": True, "identity": True, "sample": True, "rowid": True,
                "copy": False}

_COLS_SQL: Final = (
    "SELECT column_name, data_type, data_length, char_length, char_used, data_precision, data_scale, "
    "nullable, NVL(default_length, 0), identity_column FROM all_tab_cols "
    "WHERE owner = :o AND table_name = :t AND hidden_column = 'NO' AND virtual_column = 'NO' "
    "AND user_generated = 'YES' ORDER BY column_id")
_KEYS_SQL: Final = (
    "SELECT c.constraint_type, c.constraint_name, cc.column_name FROM all_constraints c "
    "JOIN all_cons_columns cc ON cc.owner = c.owner AND cc.constraint_name = c.constraint_name "
    "AND cc.table_name = c.table_name WHERE c.owner = :o AND c.table_name = :t "
    "AND c.constraint_type IN ('P', 'U') AND c.status = 'ENABLED' "
    "ORDER BY CASE c.constraint_type WHEN 'P' THEN 0 ELSE 1 END, c.constraint_name, cc.position")
_UIDX_SQL: Final = (
    "SELECT i.index_name, ic.column_name FROM all_indexes i JOIN all_ind_columns ic "
    "ON ic.index_owner = i.owner AND ic.index_name = i.index_name "
    "WHERE i.table_owner = :o AND i.table_name = :t AND i.uniqueness = 'UNIQUE' "
    "AND i.index_type = 'NORMAL' ORDER BY i.index_name, ic.column_position")
_ROWS_SQL: Final = "SELECT num_rows FROM all_tables WHERE owner = :o AND table_name = :t"
_LIST_SQL: Final = (
    "SELECT owner, table_name FROM all_tables WHERE {owner_clause} AND nested = 'NO' "
    "AND secondary = 'N' AND table_name NOT LIKE 'BIN$%' AND table_name <> :ledger "
    "ORDER BY owner, table_name FETCH FIRST :lim ROWS ONLY")


def _groups(rows: list[tuple[Any, ...]], name_at: int, col_at: int) -> list[list[str]]:
    out: dict[str, list[str]] = {}
    for r in rows:
        out.setdefault(r[name_at], []).append(r[col_at])
    return list(out.values())


def _ts9_text(value: Any) -> Any:
    """A datetime that reached a TIMESTAMP(7-9) key path (e.g. decoded by older code) → FF9 text."""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%dT%H:%M:%S.%f") + "000"
    return value


def _bind_key(col: ColumnInfo, value: Any) -> Any:
    if col.pseudo:
        return osql.key_to_rowid(int(value))
    if osql.is_ts9(col):
        return _ts9_text(value)
    if osql.input_type(col) in ("char", "nchar"):
        return osql.pad_char(value, col.length)
    return value


class OracleConnector:
    """Contract §4.2 ``Connector`` for Oracle; every driver call runs in a worker thread."""

    engine: Final = "oracle"

    def __init__(self, *, pool: OraclePool, settings: Settings, action: str) -> None:
        self._pool, self._settings, self._action = pool, settings, action
        self._user: str | None = None
        self._version: str | None = None

    @classmethod
    def from_secret(cls, secret: Mapping[str, str], params: ConnectionParams, settings: Settings, *,
                    action: str, client_id: str | None) -> OracleConnector:
        """Build from a resolved secret (``username``, ``password``, optional ``dsn``)."""
        try:
            user, password = secret["username"], secret["password"]
        except KeyError:
            raise ConnectorError("secret_unavailable") from None
        dsn = secret.get("dsn") or easy_connect(params)
        pool = OraclePool(user=user, password=password, dsn=dsn, size=settings.oracle_pool_max,
                          call_timeout_ms=settings.oracle_call_timeout_ms,
                          connect_timeout_s=float(settings.connector_connect_timeout_seconds),
                          module=settings.oracle_module, client_identifier=client_id)
        return cls(pool=pool, settings=settings, action=action)

    # -- plumbing ---------------------------------------------------------------------------------
    @property
    def _long_ms(self) -> int:
        return self._settings.connector_statement_timeout_seconds * 1000

    async def _call(self, op: str, fn: Callable[..., T], *args: Any, code: str = "source_error") -> T:
        try:
            result = await asyncio.to_thread(fn, *args)
        except oracledb.Error as exc:
            CONNECTOR_CALLS.labels("oracle", op, "error").inc()
            logger.warning("oracle.call_failed", op=op, driver_code=driver_code(exc))
            raise connector_error(code, exc) from None
        CONNECTOR_CALLS.labels("oracle", op, "ok").inc()
        return result

    def _query(self, sql: str, binds: dict[str, Any] | None = None, *, action: str | None = None,
               timeout_ms: int | None = None) -> list[tuple[Any, ...]]:
        with self._pool.acquire(action=action or self._action, call_timeout_ms=timeout_ms) as conn:
            with conn.cursor() as cur:
                cur.outputtypehandler = output_type_handler
                cur.execute(sql, binds or {})
                return cur.fetchall()

    def _ping(self) -> tuple[str, str]:
        with self._pool.acquire(action="connect") as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT USER FROM dual")
                (user,) = cur.fetchone()
            return str(user), str(conn.version)

    async def open(self) -> None:
        self._user, self._version = await self._call("connect", self._ping, code="connection_failed")

    async def close(self) -> None:
        await asyncio.to_thread(self._pool.close)

    def _owner(self, obj: ObjectRef) -> ObjectRef:
        if obj.owner:
            return ObjectRef(owner=quote_ident(obj.owner, "oracle").strip('"'),
                             name=quote_ident(obj.name, "oracle").strip('"'))
        if self._user is None:
            raise ConnectorError("connection_failed")
        return ObjectRef(owner=self._user, name=quote_ident(obj.name, "oracle").strip('"'))

    # -- metadata ---------------------------------------------------------------------------------
    async def list_objects(self, owner: str | None = None, *, limit: int = 500) -> list[ObjectRef]:
        if owner:
            clause, binds = "owner = :o", {"o": quote_ident(owner, "oracle").strip('"')}
        else:
            clause, binds = "owner IN (SELECT username FROM all_users WHERE oracle_maintained = 'N')", {}
        sql = _LIST_SQL.format(owner_clause=clause)
        rows = await self._call("list", self._query, sql, {**binds, "ledger": osql.LEDGER_TABLE, "lim": limit})
        return [ObjectRef(owner=o, name=t) for o, t in rows]

    def _describe_sync(self, obj: ObjectRef) -> TableShape:
        binds = {"o": obj.owner, "t": obj.name}
        with self._pool.acquire(action="describe") as conn, conn.cursor() as cur:
            cur.execute(_COLS_SQL, binds)
            cols = cur.fetchall()
            if not cols:
                raise ConnectorError("object_not_found")
            cur.execute(_KEYS_SQL, binds)
            cons = cur.fetchall()
            cur.execute(_UIDX_SQL, binds)
            uidx = cur.fetchall()
            cur.execute(_ROWS_SQL, binds)
            est = cur.fetchone()
        catalog = [osql.CatalogColumn(name=r[0], data_type=r[1], data_length=int(r[2] or 0),
                                      char_length=int(r[3] or 0), char_used=r[4],
                                      data_precision=None if r[5] is None else int(r[5]),
                                      data_scale=None if r[6] is None else int(r[6]), nullable=r[7] == "Y",
                                      has_default=int(r[8] or 0) > 0, is_identity=r[9] == "YES")
                   for r in cols]
        not_null = {c.name for c in catalog if not c.nullable}
        key: list[str] = []
        source: Literal["primary_key", "unique_index", "rowid"] = "rowid"
        pk = [r for r in cons if r[0] == "P"]
        if pk:
            key, source = [r[2] for r in pk], "primary_key"
        else:
            for cand in _groups([r for r in cons if r[0] == "U"], 1, 2) + _groups(uidx, 0, 1):
                if set(cand) <= not_null:
                    key, source = cand, "unique_index"
                    break
        columns = [osql.column_info(c, is_key=c.name in key) for c in catalog]
        if not key:
            columns.append(osql.rowid_column())
            key = ["ROWID"]
        estimate = None if not est or est[0] is None else int(est[0])
        return TableShape(object=obj, columns=columns, key=key, key_source=source, row_estimate=estimate)

    async def describe(self, obj: ObjectRef) -> TableShape:
        if self._user is None:
            await self.open()
        return await self._call("describe", self._describe_sync, self._owner(obj))

    # -- selection --------------------------------------------------------------------------------
    def _compile(self, shape: TableShape, selection: Selection, params: dict[str, JsonScalar]
                 ) -> tuple[str | None, str | None, dict[str, Any], tuple[float, int] | None]:
        validate_selection(selection, "oracle", shape)       # M2: engine mismatch, deny-lists, key projection
        binds: dict[str, Any] = {}
        where = query = None
        names: list[str] = []
        if selection.where:
            where, names = compile_where(selection, "oracle")
        elif selection.query:
            if shape.key_source == "rowid":
                raise SelectionError("selection_query_needs_key", "a query cannot page a key-less table")
            query, names = compile_query(selection, "oracle")
        for name in names:
            if name not in params:
                raise SelectionError("selection_param_missing", "a bound parameter has no value")
            binds[name] = params[name]
        sample = None
        if selection.sample is not None:
            sample = (float(selection.sample.percent), int(selection.sample.seed or 0))
        return where, query, binds, sample

    def _projection(self, shape: TableShape, selection: Selection) -> list[ColumnInfo]:
        """Columns the read yields: a query's own select list, narrowed by ``columns``; keys always."""
        names: set[str] | None = None
        if selection.query:
            parsed = sqlglot.parse_one(selection.query, read="oracle")
            selects = parsed.named_selects if isinstance(parsed, exp.Select) else []
            if "*" not in selects:
                names = {n.upper() for n in selects}
        if selection.columns:
            wanted = {quote_ident(c, "oracle").strip('"') for c in selection.columns}
            names = wanted if names is None else names & wanted
        if names is None:
            return list(shape.columns)
        names |= set(shape.key)
        return [c for c in shape.columns if c.name in names]

    @staticmethod
    def _row(columns: list[ColumnInfo], values: tuple[Any, ...]) -> Row:
        row: Row = {}
        for col, value in zip(columns, values, strict=True):
            row[col.name] = osql.rowid_to_key(value) if col.pseudo and value is not None else value
        return row

    @staticmethod
    def _percent(estimate: int | None, n: int) -> float | None:
        if estimate is None or estimate <= 2 * n:
            return None
        return min(99.0, max(0.000001, 200.0 * n / estimate))

    # -- reads ------------------------------------------------------------------------------------
    async def count(self, shape: TableShape, selection: Selection, params: dict[str, JsonScalar]) -> int:
        where, query, binds, sample = self._compile(shape, selection, params)
        sql = osql.count_sql(shape, where=where, query=query, sample=sample)
        rows = await self._call("count", self._query, sql, binds, action="count", timeout_ms=self._long_ms)
        total = int(rows[0][0])
        return min(total, selection.max_rows) if selection.max_rows else total

    async def sample(self, shape: TableShape, selection: Selection, params: dict[str, JsonScalar],
                     n: int) -> list[Row]:
        where, query, binds, _ = self._compile(shape, selection, params)
        cols = self._projection(shape, selection)
        plan = osql.sample_sql(shape, columns=cols, where=where, query=query,
                               percent=self._percent(shape.row_estimate, n))
        rows = await self._call("sample", self._query, plan.sql, {**binds, "n": n}, action="sample",
                                timeout_ms=self._long_ms)
        return [self._row(plan.columns, r) for r in rows]

    async def sample_keys(self, shape: TableShape, selection: Selection, params: dict[str, JsonScalar],
                          n: int, *, upper: tuple[Any, ...] | None = None) -> list[tuple[Any, ...]]:
        keys = osql.key_columns(shape)
        if any(osql.input_type(k) in ("nchar", "nvarchar") for k in keys):
            return []
        where, query, binds, _ = self._compile(shape, selection, params)
        plan = osql.sample_sql(shape, columns=keys, where=where, query=query,
                               percent=self._percent(shape.row_estimate, n), keys_only=True)
        rows = await self._call("sample_keys", self._query, plan.sql, {**binds, "n": n},
                                action="sample", timeout_ms=self._long_ms)
        out = [tuple(self._row(plan.columns, r)[k.name] for k in keys) for r in rows]
        return [k for k in out if upper is None or k < tuple(upper)]

    async def key_at_offset(self, shape: TableShape, selection: Selection, params: dict[str, JsonScalar],
                            offset: int) -> tuple[Any, ...] | None:
        """The key of the row at ``offset`` in key order (M2 turns ``max_rows`` into an upper bound with it)."""
        where, query, binds, sample = self._compile(shape, selection, params)
        keys = osql.key_columns(shape)
        plan = osql.read_sql(shape, columns=keys, where=where, query=query, sample=sample,
                             lower=False, upper=False, after=False)
        sql = plan.sql.replace("FETCH FIRST :batch ROWS ONLY", "OFFSET :off ROWS FETCH NEXT 1 ROWS ONLY")
        rows = await self._call("key_at_offset", self._query, sql, {**binds, "off": int(offset)},
                                action="count", timeout_ms=self._long_ms)
        return tuple(self._row(plan.columns, rows[0])[k.name] for k in keys) if rows else None

    def _fetch(self, plan: osql.ReadPlan, binds: dict[str, Any], batch: int) -> list[Row]:
        with self._pool.acquire(action=self._action) as conn:
            cur = conn.cursor()
            try:
                cur.arraysize = batch
                cur.prefetchrows = batch
                cur.outputtypehandler = output_type_handler
                sizes = {name: INPUT_TYPES[t] for name, t in plan.bind_types.items()}
                if sizes:
                    cur.setinputsizes(**sizes)
                cur.execute(plan.sql, binds)
                raw = cur.fetchall()
            finally:
                cur.close()
        return [self._row(plan.columns, r) for r in raw]

    async def read_partition(self, shape: TableShape, selection: Selection,
                             params: dict[str, JsonScalar], key_range: KeyRange, *,
                             after_key: tuple[Any, ...] | None, batch: int) -> AsyncIterator[list[Row]]:
        """Keyset pages of one partition, ordered by key; never locks (contract §4.2)."""
        where, query, binds, sample = self._compile(shape, selection, params)
        cols = self._projection(shape, selection)
        keys = osql.key_columns(shape)
        lower = decode_key(key_range.lower, keys) if key_range.lower else None   # KeyRange holds encoded keys
        upper = decode_key(key_range.upper, keys) if key_range.upper else None
        after = after_key
        while True:
            plan = osql.read_sql(shape, columns=cols, where=where, query=query, sample=sample,
                                 lower=lower is not None, upper=upper is not None, after=after is not None)
            b: dict[str, Any] = {**binds, "batch": batch}
            for prefix, values in (("lo", lower), ("hi", upper), ("af", after)):
                if values is not None:
                    for i, (col, value) in enumerate(zip(keys, values, strict=True)):
                        b[f"{prefix}{i}"] = _bind_key(col, value)
            rows = await self._call("read", self._fetch, plan, b, batch)
            if rows:
                yield rows
            if len(rows) < batch:
                return
            after = tuple(rows[-1][k.name] for k in keys)

    # -- connection test --------------------------------------------------------------------------
    def _test_sync(self, role: str, obj: ObjectRef | None, write_mode: str | None) -> list[dict[str, Any]]:
        checks: list[dict[str, Any]] = []

        def check(name: str, fn: Callable[[Any], bool], cur: Any) -> None:
            try:
                ok = fn(cur)
                checks.append({"name": name, "ok": bool(ok)})
            except oracledb.Error as exc:
                checks.append({"name": name, "ok": False, "code": driver_code(exc)})

        with self._pool.acquire(action="test") as conn, conn.cursor() as cur:
            cur.execute("SELECT USER FROM dual")
            user = cur.fetchone()[0]
            cur.execute("SELECT privilege FROM session_privs")
            privs = {r[0] for r in cur.fetchall()}
            if obj is None:
                return checks
            o = self._owner(obj)
            table = osql.qualified(o)

            def can(c: Any, priv: str) -> bool:
                if o.owner == user or f"{priv} ANY TABLE" in privs:
                    return True
                c.execute("SELECT (SELECT COUNT(*) FROM all_tab_privs WHERE table_schema = :o AND table_name = :t "
                          "AND privilege = :p AND grantee IN (USER, 'PUBLIC')) + (SELECT COUNT(*) FROM "
                          "role_tab_privs WHERE owner = :o AND table_name = :t AND privilege = :p) FROM dual",
                          {"o": o.owner, "t": o.name, "p": priv})
                return int(c.fetchone()[0]) > 0

            def exists(c: Any) -> bool:
                c.execute(_ROWS_SQL, {"o": o.owner, "t": o.name})
                return c.fetchone() is not None

            if role == "source":
                check("select_source", lambda c: c.execute(f"SELECT 1 FROM {table} WHERE 1 = 0") or True, cur)
                return checks
            present = exists(cur)
            if write_mode == "table" and not present:
                check("create_target", lambda c: (o.owner == user and "CREATE TABLE" in privs)
                      or "CREATE ANY TABLE" in privs, cur)
            elif write_mode in ("table", "append"):
                check("insert_target", lambda c: can(c, "INSERT"), cur)
                if write_mode == "table":
                    check("update_target", lambda c: can(c, "UPDATE"), cur)
            elif write_mode == "in_place":
                check("update_target", lambda c: can(c, "UPDATE"), cur)
            if write_mode in ("in_place", "append"):
                check("create_ledger", lambda c: (o.owner == user and "CREATE TABLE" in privs)
                      or "CREATE ANY TABLE" in privs, cur)
        return checks

    async def test(self, *, role: Literal["source", "target"], obj: ObjectRef | None = None,
                   write_mode: str | None = None) -> ConnectionTestResult:
        try:
            await self.open()
        except ConnectorError as err:
            return ConnectionTestResult(ok=False, engine="oracle", checks=[
                {"name": "connect", "ok": False, "detail": err.detail}], capabilities=_CAPS,
                error_code=err.code)
        checks = [{"name": "connect", "ok": True}]
        checks += await self._call("test", self._test_sync, role, obj, write_mode)
        return ConnectionTestResult(ok=all(c["ok"] for c in checks), engine="oracle",
                                    server_version=self._version, checks=checks, capabilities=_CAPS)
```

- [ ] **Step 5: Run the integration tests and gates**

Run: `DPS_TEST_REQUIRE_SERVICES=1 pytest -q -m oracle tests/connectors/test_oracle_connector.py && ruff check dps tests tools && ruff format --check dps tests tools && mypy dps && pytest -q`
Expected: 16 passed against the container; the default suite stays green (the module skips without
Oracle).

- [ ] **Step 6: Commit**

```bash
git add dps/layers/connectors/oracle.py tests/connectors/test_oracle_connector.py tests/connectors/oracle_fixtures.py && git commit -m "feat(oracle): connector reads — describe with ROWID fallback, sampling, keyset pages that never lock" -- dps/layers/connectors/oracle.py tests/connectors/test_oracle_connector.py tests/connectors/oracle_fixtures.py
```

---
### Task 6: `OracleWriter` — MERGE upserts, executemany loads, in-place UPDATE, fenced write ledger

**Files:**
- Modify: `dps/layers/connectors/oracle.py` (append part 3), `tests/connectors/oracle_fixtures.py` (`make_target`)
- Test: `tests/connectors/test_oracle_writer.py` (markers `integration`, `oracle`)

**Interfaces introduced:**

```python
class OracleWriter:                                        # implements base.BatchWriter (contract §4.2)
    def __init__(self, connector: OracleConnector, target: ResolvedTarget, source_shape: TableShape) -> None
    async def prepare(self, *, dry_run: bool) -> PrepareReport
    async def precheck_collisions(self, keys: AsyncIterator[list[tuple[Any, ...]]]) -> int
    async def read_ledger(self, job_id: str, partition_no: int) -> LedgerMark | None
    async def write_batch(self, rows: list[Row], *, mark: LedgerMark) -> int
    async def finish_partition(self, job_id: str, partition_no: int) -> dict[str, Any]   # {} for Oracle
    async def clear_ledger(self, job_id: str) -> int
    async def close(self) -> None
# OracleConnector.writer(target, source_shape) -> OracleWriter
```

**Behaviours:**
- `prepare` (once per job, in the job's `prepare` phase — contract §4.2):
  - `in_place` on a `rowid`/`none` key → `no_key`; `append` on `none` → `no_key`; `append` on `rowid`
    with a strategy other than `generate` → `pk_strategy_invalid`; `append`+`generate` needs every key
    column to be an identity or have a default, or `pk_strategy.sequence` naming an existing sequence (single
    key) → else `pk_strategy_invalid`.
  - copies (`table`, `append`) of a column whose type is outside the closed table → `column_unsupported`
    (exclude it with `selection.columns`); a same-table `append` on a key-less (ROWID) table →
    `pk_strategy_invalid` (new rows can land below existing ROWIDs and would be read again).
  - a same-table `append` with a single integer key reports `append_upper = MAX(key) + 1` so M2's run
    handler bounds every partition's reads below the rows the job appends.
  - `table`: missing target → `target_missing` unless `create_if_missing`, then `CREATE TABLE` from
    `ResolvedTarget.target_shape` with Oracle types from `osql.oracle_ddl_type` (source types kept where the
    output fits, widened otherwise; PK on the source key when the source has one; M2's `column_types` are
    PostgreSQL DDL and are not used for Oracle targets); existing target → every required column present (`column_missing`),
    kinds compatible (`incompatible_method`: text into a numeric/date column), lengths sufficient
    (`length_overflow`); `truncate_insert` → `TRUNCATE TABLE` once (falls back to `DELETE` on ORA-01031).
  - `in_place`/`append` check the written columns' lengths/kinds against the table the same way.
  - the ledger table `<ledger_owner or target owner>.DPS_WRITE_LEDGER` is created (19c-safe PL/SQL) for
    `in_place`, `append`, and `table` loads from a `rowid` source (no key to upsert on, so the ledger makes
    them resumable).
  - `dry_run` runs every check and changes nothing (no DDL, no truncate, no ledger).
- `write_batch` runs ONE transaction: (1) the guarded ledger `MERGE` when the mode uses the ledger — 0 rows
  means another writer already applied this batch → `ConnectorError("ledger_conflict")`, rollback; (2)
  `executemany` of the batch — `MERGE` (table with key), `UPDATE … WHERE key = …` of only the transformed
  non-key columns named in `ResolvedTarget.write_columns` (in_place), or `INSERT` (append — key omitted
  when `generated_key`, or filled from the named sequence — and rowid table loads); (3) commit. Returns rows written
  (`rowcount`). CHAR values are padded, TIMESTAMP(7–9)/TSTZ go back from text, CLOB/JSON bind as CLOB.
- `precheck_collisions` counts transformed keys that already exist, `IN` lists of ≤ 500.
- `clear_ledger` deletes the job's ledger rows (0 when the ledger table does not exist).

- [ ] **Step 1: Write the failing tests** — `tests/connectors/test_oracle_writer.py`.
  `make_target(shape, **kw)` in `oracle_fixtures.py` builds M2's `ResolvedTarget` with `job_id="j_test"`,
  `write_mode`, `connection_id="c_test"`, `engine=<shape's engine>`, `object` (default `shape.object`),
  `same_as_source` (default: `object == shape.object`), `table_load` (default `truncate_insert`),
  `pk_strategy`, `create_if_missing` (default `True`), `ledger_owner`, `key` (default `shape.key` minus
  pseudo columns), `write_columns` (default: every non-pseudo column name; for `in_place` tests the SET
  list), `generated_key` (default: `pk_strategy` is `generate`) and `target_shape` (default `shape`); it is
  engine-neutral and reused by the Mongo tests.

| Test | Arrange | Assert |
|---|---|---|
| `test_table_mode_creates_target_and_reloads_idempotently` | 120 customers; target `CUSTOMERS_M` missing; rows read with `read_partition`; `FULL_NAME` replaced by `"T-" + key` in Python | `prepare` → `created`; two `write_batch` calls with the same rows (marks batch 1 and 1 — no ledger in this mode) → `SELECT COUNT(*)` = 120; `TO_CHAR(LAST_CONTACT_AT, FF9)` equals the source text for every key; `TAX_ID` equals the source (padded); no `DPS_WRITE_LEDGER` table exists |
| `test_truncate_insert_truncates_once` | target pre-created with 7 junk rows | `prepare(dry_run=False)` → `truncated`; after writing 50 rows the target has exactly 50 |
| `test_prepare_dry_run_changes_nothing` | missing target, `in_place` on another table | `prepare(dry_run=True)` returns; target still missing; no ledger table |
| `test_in_place_updates_only_transformed_columns` | 40 customers; `write_columns=["FULL_NAME", "EMAIL"]`; rows with new `FULL_NAME`, `EMAIL` **and** a changed `SEGMENT` | after `write_batch(mark=batch 1)`: `FULL_NAME`/`EMAIL` changed for all 40; `SEGMENT` unchanged; keys unchanged; ledger row `(job, 0) → batch 1` |
| `test_ledger_fences_a_replayed_batch` | as above, then the same mark again with different values | second call raises `ConnectorError(code="ledger_conflict")`; table shows the first batch's values |
| `test_ledger_and_rows_commit_together` | monkeypatch the cursor class so `executemany` raises `oracledb.DatabaseError` after the ledger MERGE ran | `write_batch` raises `ConnectorError(code="target_error")`; neither the ledger row nor any update is visible from another session |
| `test_read_ledger_round_trip_and_clear` | write marks for partitions 0 and 1 of job `j_x` | `read_ledger("j_x", 1)` → `LedgerMark(batch_no=…, last_key=…)` equal to what was written; `clear_ledger("j_x")` → 2; then `read_ledger` → `None` |
| `test_append_generate_with_identity` | identity table, 10 rows | `append` + `PkGenerate()`: after writing the 10 read rows the table has 20 rows; the 10 new ids are all > the old max |
| `test_append_generate_with_sequence` | table `CASES(CASE_ID NUMBER(12) PRIMARY KEY, NOTE VARCHAR2(40))`, sequence `CASE_SEQ` | `PkGenerate(sequence="<owner>.CASE_SEQ")` → 2× rows, new ids from the sequence |
| `test_append_offset_and_collision_precheck` | 10 customers; keys offset by 90,000,000 in the rows (M2 rewrites keys before the writer); a second set offset by 5 (colliding) | `precheck_collisions` → 0 for the first set and 5 for the colliding one; after writing the first set the table has 20 rows |
| `test_in_place_refused_on_rowid_tables` | key-less table | `in_place` `prepare` → `ConnectorError(code="no_key")`; same-table `append` → `pk_strategy_invalid` |
| `test_same_table_append_reports_append_upper` | identity table, max id 10 | `append` same table `prepare(dry_run=False)` → `report.append_upper == 11` |
| `test_length_overflow_and_kind_checks` | `target_shape` with `FULL_NAME.length=200` (source 120) and `write_columns=["FULL_NAME"]` for `in_place`; existing target with `CUSTOMER_ID NUMBER` and required kind `text` for `table` | `length_overflow`; `incompatible_method` |
| `test_unsupported_columns_are_refused_for_copies` | table with an `XMLTYPE` column | `table` prepare → `column_unsupported`; with a `target_shape` excluding it (a `Selection.columns` projection) the copy passes |

- [ ] **Step 2: Run to confirm failure** — `DPS_TEST_REQUIRE_SERVICES=1 pytest -q -m oracle tests/connectors/test_oracle_writer.py`
  → FAIL (`AttributeError: … has no attribute 'OracleWriter'`).

- [ ] **Step 3: Append the writer to `dps/layers/connectors/oracle.py`**
```python
from dps.layers.connectors.base import LedgerMark, PrepareReport
from dps.layers.connectors.targets import ResolvedTarget

_TEXT_KINDS: Final = frozenset({"text", "char", "lob_text", "json"})
_NON_TEXT_KINDS: Final = frozenset({"int", "decimal", "float", "timestamp", "date", "bool", "binary"})


class OracleWriter:
    """Contract §4.2 ``BatchWriter`` for Oracle targets (same database family as the source)."""

    def __init__(self, connector: OracleConnector, target: ResolvedTarget, source_shape: TableShape) -> None:
        self._c, self._t, self._src = connector, target, source_shape
        self._obj = connector._owner(target.object)
        self._ledger_owner = (target.ledger_owner or self._obj.owner or "").upper()
        self._keys = [k for k in osql.key_columns(source_shape) if not k.pseudo]
        self._by_name = {c.name: c for c in source_shape.columns}
        self._uses_ledger = target.write_mode in ("in_place", "append") or (
            target.write_mode == "table" and source_shape.key_source == "rowid")

    # -- prepare ----------------------------------------------------------------------------------
    def _required(self) -> list[ColumnInfo]:
        shape = self._t.target_shape or self._src
        return [c for c in shape.columns if not c.pseudo]

    def _check_against(self, existing: TableShape, cols: list[ColumnInfo]) -> None:
        have = {c.name: c for c in existing.columns}
        for req in cols:
            ex = have.get(req.name)
            if ex is None:
                raise ConnectorError("column_missing")
            if req.kind in _TEXT_KINDS and ex.kind in _NON_TEXT_KINDS:
                raise ConnectorError("incompatible_method")
            if req.length and ex.length and ex.kind in ("text", "char") and req.length > ex.length:
                raise ConnectorError("length_overflow")

    def _sequence(self) -> tuple[str, str] | None:
        pk = self._t.pk_strategy
        if pk is None or pk.kind != "generate" or not getattr(pk, "sequence", None):
            return None
        owner, _, name = str(pk.sequence).rpartition(".")
        q = (f"{quote_ident(owner, 'oracle')}." if owner else "") + quote_ident(name, "oracle")
        return self._keys[0].name, q

    def _prepare_sync(self, dry_run: bool) -> PrepareReport:
        mode, src, pk = self._t.write_mode, self._src, self._t.pk_strategy
        if mode == "in_place" and src.key_source in ("none", "rowid"):
            raise ConnectorError("no_key")
        if mode == "append":
            if src.key_source == "none":
                raise ConnectorError("no_key")
            if src.key_source == "rowid" and (pk is None or pk.kind != "generate" or self._t.same_as_source):
                raise ConnectorError("pk_strategy_invalid", "rowid")
        required = self._required()
        if mode in ("table", "append"):
            for col in required:
                source = self._by_name.get(col.name)
                if source is not None and osql.family_of(source.data_type.split("(")[0]) == "unsupported":
                    raise ConnectorError("column_unsupported")
        created = truncated = False
        append_upper: int | None = None
        try:                                     # describe first: never hold two pooled sessions at once
            existing: TableShape | None = self._c._describe_sync(self._obj)
        except ConnectorError as err:
            if err.code != "object_not_found":
                raise
            existing = None
        with self._c._pool.acquire(action="prepare") as conn, conn.cursor() as cur:
            if mode == "table" and existing is None:
                if not self._t.create_if_missing:
                    raise ConnectorError("target_missing")
                if not dry_run:
                    keys = [k.name for k in self._keys] if src.key_source in ("primary_key", "unique_index") else []
                    cur.execute(osql.create_table_sql(
                        self._obj, [(c, self._by_name.get(c.name)) for c in required], keys))
                    created = True
            else:
                if existing is None:
                    raise ConnectorError("target_missing")
                written = required if mode != "in_place" else [
                    c for c in required if c.name in set(self._t.write_columns)]
                self._check_against(existing, written)
                if mode == "append" and pk is not None and pk.kind == "generate":
                    seq = self._sequence()
                    if seq is not None:
                        if len(self._keys) != 1:
                            raise ConnectorError("pk_strategy_invalid")
                        owner, _, name = seq[1].replace('"', "").rpartition(".")
                        cur.execute("SELECT COUNT(*) FROM all_sequences WHERE sequence_owner = :o "
                                    "AND sequence_name = :s", {"o": owner or self._obj.owner, "s": name})
                        if int(cur.fetchone()[0]) == 0:
                            raise ConnectorError("pk_strategy_invalid")
                    elif not all(k.is_identity or k.has_default for k in existing.columns if k.is_key):
                        raise ConnectorError("pk_strategy_invalid")
                if (mode == "append" and self._t.same_as_source and len(self._keys) == 1
                        and self._keys[0].kind == "int"):
                    cur.execute(f"SELECT MAX({osql.qcol(self._keys[0].name)}) FROM {osql.qualified(self._obj)}")  # noqa: S608
                    top = cur.fetchone()[0]
                    append_upper = (int(top) + 1) if top is not None else None
                if (mode == "table" and self._t.table_load == "truncate_insert" and not dry_run):
                    try:
                        cur.execute(f"TRUNCATE TABLE {osql.qualified(self._obj)}")
                    except oracledb.DatabaseError as exc:
                        if driver_code(exc) != "ORA-01031":
                            raise
                        cur.execute(f"DELETE FROM {osql.qualified(self._obj)}")  # noqa: S608
                        conn.commit()
                    truncated = True
            if self._uses_ledger and not dry_run:
                cur.execute(osql.ledger_ddl(self._ledger_owner))
        return PrepareReport(created=created, truncated=truncated, ledger=self._uses_ledger and not dry_run,
                             append_upper=append_upper)

    async def prepare(self, *, dry_run: bool) -> PrepareReport:
        return await self._c._call("prepare", self._prepare_sync, dry_run, code="target_error")

    # -- collisions and ledger --------------------------------------------------------------------
    def _collisions_sync(self, keys: list[tuple[Any, ...]]) -> int:
        total = 0
        with self._c._pool.acquire(action="precheck") as conn, conn.cursor() as cur:
            for start in range(0, len(keys), osql.IN_LIST_MAX):
                chunk = keys[start:start + osql.IN_LIST_MAX]
                binds: dict[str, Any] = {}
                for j, key in enumerate(chunk):
                    for i, (col, value) in enumerate(zip(self._keys, key, strict=True)):
                        binds[f"c{j}" if len(self._keys) == 1 else f"c{j}_{i}"] = _bind_key(col, value)
                cur.execute(osql.collision_sql(self._obj, self._keys, len(chunk)), binds)
                total += int(cur.fetchone()[0])
        return total

    async def precheck_collisions(self, keys: AsyncIterator[list[tuple[Any, ...]]]) -> int:
        total = 0
        async for batch in keys:
            total += await self._c._call("precheck", self._collisions_sync, batch, code="target_error")
        return total

    def _read_ledger_sync(self, job_id: str, partition_no: int) -> LedgerMark | None:
        try:
            rows = self._c._query(osql.ledger_read_sql(self._ledger_owner),
                                  {"job": job_id, "part": partition_no}, action="ledger")
        except oracledb.DatabaseError as exc:
            if driver_code(exc) == "ORA-00942":
                return None
            raise
        if not rows:
            return None
        batch_no, last_key = rows[0]
        return LedgerMark(job_id=job_id, partition_no=partition_no, batch_no=int(batch_no),
                          last_key=json.loads(last_key))

    async def read_ledger(self, job_id: str, partition_no: int) -> LedgerMark | None:
        return await self._c._call("ledger", self._read_ledger_sync, job_id, partition_no, code="target_error")

    def _clear_sync(self, job_id: str) -> int:
        with self._c._pool.acquire(action="finalize") as conn, conn.cursor() as cur:
            try:
                cur.execute(osql.ledger_purge_sql(self._ledger_owner), {"job": job_id})
            except oracledb.DatabaseError as exc:
                if driver_code(exc) == "ORA-00942":
                    return 0
                raise
            n = cur.rowcount
            conn.commit()
            return n

    async def clear_ledger(self, job_id: str) -> int:
        return await self._c._call("ledger", self._clear_sync, job_id, code="target_error")

    # -- writes -----------------------------------------------------------------------------------
    def _statement(self, sample_row: Row) -> tuple[str, list[ColumnInfo]]:
        mode = self._t.write_mode
        present = [c for c in self._src.columns if not c.pseudo and c.name in sample_row]
        if mode == "in_place":
            writable = set(self._t.write_columns)
            set_cols = [c for c in present if c.name in writable and not c.is_key]
            if not set_cols:
                return "", []
            return osql.update_sql(self._obj, set_cols, self._keys), set_cols + self._keys
        if mode == "table" and self._src.key_source in ("primary_key", "unique_index"):
            return osql.merge_sql(self._obj, present, [k.name for k in self._keys]), present
        seq = None
        cols = present
        if mode == "append" and self._t.generated_key:
            seq = self._sequence()
            if seq is None:
                cols = [c for c in present if not c.is_key]
        bound = [c for c in cols if not (seq is not None and c.name == seq[0])]
        return osql.insert_sql(self._obj, cols, sequence=seq), bound

    @staticmethod
    def _value(col: ColumnInfo, value: Any) -> Any:
        if osql.input_type(col) in ("char", "nchar"):
            return osql.pad_char(value, col.length)
        if osql.is_ts9(col):
            return _ts9_text(value)
        return value

    def _write_sync(self, rows: list[Row], mark: LedgerMark) -> int:
        sql, cols = self._statement(rows[0])
        params = [tuple(self._value(c, r.get(c.name)) for c in cols) for r in rows]
        sizes = [INPUT_TYPES.get(osql.input_type(c) or "") for c in cols]
        with self._c._pool.acquire(action="write") as conn:
            if self._uses_ledger:
                with conn.cursor() as led:
                    led.setinputsizes(None, None, None, oracledb.DB_TYPE_CLOB)
                    led.execute(osql.ledger_mark_sql(self._ledger_owner),
                                [mark.job_id, mark.partition_no, mark.batch_no,
                                 json.dumps(mark.last_key, separators=(",", ":"))])
                    if led.rowcount != 1:
                        raise ConnectorError("ledger_conflict")
            written = len(rows)
            if sql:
                with conn.cursor() as cur:
                    if any(s is not None for s in sizes):
                        cur.setinputsizes(*sizes)
                    cur.executemany(sql, params)
                    written = cur.rowcount
            conn.commit()
        return written

    async def write_batch(self, rows: list[Row], *, mark: LedgerMark) -> int:
        if not rows:
            return 0
        return await self._c._call("write", self._write_sync, rows, mark, code="target_error")

    async def finish_partition(self, job_id: str, partition_no: int) -> dict[str, Any]:
        return {}

    async def close(self) -> None:
        return None
```
and on `OracleConnector`:
```python
    async def writer(self, target: ResolvedTarget, source_shape: TableShape) -> OracleWriter:
        if self._user is None:
            await self.open()
        return OracleWriter(self, target, source_shape)
```
Note: `ConnectorError` raised inside `_write_sync` (ledger conflict) propagates through `_call` unchanged
(it only maps `oracledb.Error`), and the `acquire()` finally rolls the transaction back.

- [ ] **Step 4: Run the integration tests and gates**

Run: `DPS_TEST_REQUIRE_SERVICES=1 pytest -q -m oracle tests/connectors/test_oracle_writer.py tests/connectors/test_oracle_connector.py && ruff check dps tests tools && ruff format --check dps tests tools && mypy dps && pytest -q`
Expected: 13 writer tests + 16 connector tests pass; suite green.

- [ ] **Step 5: Commit**

```bash
git add dps/layers/connectors/oracle.py tests/connectors/test_oracle_writer.py tests/connectors/oracle_fixtures.py && git commit -m "feat(oracle): batch writer — MERGE upserts, executemany loads, in-place UPDATE, fenced write ledger" -- dps/layers/connectors/oracle.py tests/connectors/test_oracle_writer.py tests/connectors/oracle_fixtures.py
```

---
### Task 7: The three measured Oracle pitfalls as tests (+ the key codec and date-shift rules they need)

**Files:**
- Create: `tests/connectors/test_oracle_pitfalls.py` (markers `integration`, `oracle`), `tests/connectors/test_codec_m3.py`
- Modify (M2): `dps/layers/connectors/base.py` — TIMESTAMP(7–9) text in the key and value codecs (only if the codec test below is red)
- Modify (M0 touch point): `dps/engine/transform/methods.py` — `date_shift` keeps sub-microsecond digits; `tests/test_methods.py`

**Behaviours:**
- `encode_key`/`decode_key` round-trip a TIMESTAMP(7–9) key as its nine-digit text (`decode_key` returns the
  `str`, never a `datetime`), a CHAR key with its padding, and an Oracle packed ROWID as an `int`.
- `to_engine_text` passes an FF9 `str` through unchanged and `from_engine_text` returns the text unchanged for
  a column with `ts_precision > 6` (`datetime.fromisoformat` would truncate to microseconds — measured).
- `date_shift` on `2025-01-01T10:00:00.123456789` shifts the date part and keeps `789`; reveal restores the
  exact original. Values with ≤ 6 fractional digits produce exactly what M0 produced before (token
  compatibility).

- [ ] **Step 1: Codec and method tests** — `tests/connectors/test_codec_m3.py`:
```python
from __future__ import annotations

from dps.layers.connectors.base import ColumnInfo, decode_key, encode_key, from_engine_text, to_engine_text

TS9 = ColumnInfo(path="$.TS", name="TS", data_type="TIMESTAMP(9)", kind="timestamp", ts_precision=9, is_key=True)
REF = ColumnInfo(path="$.R", name="R", data_type="CHAR(12)", kind="char", length=12, is_key=True)
RID = ColumnInfo(path="$.ROWID", name="ROWID", data_type="ROWID", kind="int", is_key=True, pseudo=True)


def test_ts9_keys_keep_nine_digits() -> None:
    key = ("2025-03-01T10:00:00.123456789",)
    enc = encode_key(key, [TS9])
    assert enc == [{"t": "ts", "v": "2025-03-01T10:00:00.123456789"}]
    assert decode_key(enc, [TS9]) == key


def test_char_and_rowid_keys() -> None:
    assert decode_key(encode_key(("K17         ",), [REF]), [REF]) == ("K17         ",)
    big = 2**100 + 12345
    assert decode_key(encode_key((big,), [RID]), [RID]) == (big,)


def test_ts9_values_pass_through_the_engine_text_codec() -> None:
    text = "2025-03-01T10:00:00.123456789"
    assert to_engine_text(text, TS9) == text
    assert from_engine_text(text, TS9) == text
```
Append to `tests/test_methods.py` (M0):
```python
def test_date_shift_keeps_sub_microsecond_digits() -> None:
    c = ctx("DATE_OF_BIRTH", {"window_days": 365}, anchor="CUSTOMER_ID:abc")
    out = run_method("date_shift", "2025-01-01T10:00:00.123456789", c)
    assert out.value is not None and out.value.endswith(".123456789") and out.value[:10] != "2025-01-01"
    assert reverse_method("date_shift", out.value, c, out.reverse_params) == "2025-01-01T10:00:00.123456789"
    zero = run_method("date_shift", "2025-01-01T10:00:00.000000123", c)
    assert zero.value is not None and zero.value.endswith(".000000123")
    plain = run_method("date_shift", "2025-01-01T10:00:00.123456", c)       # M0 path, unchanged
    assert plain.value is not None and plain.value.endswith(".123456") and len(plain.value) == 26
```
(`ctx` is the M0 test helper in `tests/test_methods.py`; pass `anchor` through its `**kw`.)

- [ ] **Step 2: The pitfall tests** — `tests/connectors/test_oracle_pitfalls.py`:
```python
"""The three Oracle bugs Wega measured on real infrastructure, carried in as tests (contract §4.2).

Each test first demonstrates the pitfall against the raw driver, then proves the connector avoids it.
"""
from __future__ import annotations

import math

import pytest

oracledb = pytest.importorskip("oracledb")

from dps.config import Settings  # noqa: E402
from dps.jobs_models import ConnectionParams  # noqa: E402
from dps.layers.connectors.base import KeyRange, LedgerMark, ObjectRef, decode_key, encode_key  # noqa: E402
from dps.layers.connectors.oracle import OracleConnector  # noqa: E402
from dps.layers.connectors.selection import Selection  # noqa: E402
from tests.connectors.oracle_fixtures import make_target  # noqa: E402

pytestmark = [pytest.mark.integration, pytest.mark.oracle]
FF9 = "'YYYY-MM-DD\"T\"HH24:MI:SS.FF9'"


def _connector(schema) -> OracleConnector:  # noqa: ANN001
    return OracleConnector.from_secret(
        {"username": schema.owner, "password": schema.owner_password, "dsn": schema.dsn},
        ConnectionParams(), Settings(_env_file=None), action="test", client_id="j_pitfalls")


async def _read_all(c: OracleConnector, shape, rng: KeyRange = KeyRange(), after=None, batch=7):  # noqa: ANN001, ANN201, B008
    pages = []
    async for rows in c.read_partition(shape, Selection(), {}, rng, after_key=after, batch=batch):
        pages.append(rows)
    return pages


async def test_oracle_char_key_paging(oracle_conn, oracle_schema) -> None:  # noqa: ANN001
    o = oracle_schema.owner
    with oracle_conn.cursor() as cur:
        cur.execute(f'CREATE TABLE "{o}"."KYC" (DOC_REF CHAR(12) NOT NULL PRIMARY KEY, N NUMBER(6))')
        cur.executemany(f'INSERT INTO "{o}"."KYC" VALUES (:1, :2)', [(f"K{i}", i) for i in range(1, 31)])
        oracle_conn.commit()
        cur.execute(f'SELECT COUNT(*) FROM "{o}"."KYC" WHERE DOC_REF = :r', {"r": "K17"})
        assert cur.fetchone()[0] == 0                       # the pitfall: VARCHAR2 bind, no padding
        cur.setinputsizes(r=oracledb.DB_TYPE_CHAR)
        cur.execute(f'SELECT COUNT(*) FROM "{o}"."KYC" WHERE DOC_REF = :r', {"r": "K17"})
        assert cur.fetchone()[0] == 1                       # CHAR bind: blank-padded comparison
    c = _connector(oracle_schema)
    try:
        shape = await c.describe(ObjectRef(owner=o, name="KYC"))
        seen = [r["DOC_REF"] for p in await _read_all(c, shape) for r in p]
        assert seen == sorted(f"K{i}".ljust(12) for i in range(1, 31))
        rest = [r["DOC_REF"] for p in await _read_all(c, shape, after=("K2",)) for r in p]
        assert rest == [k for k in seen if k > "K2".ljust(12)]   # an rstripped checkpoint still binds
        keycol = [col for col in shape.columns if col.is_key]
        assert decode_key(encode_key((seen[0],), keycol), keycol) == (seen[0],)
        writer = await c.writer(make_target(shape, write_mode="in_place", write_columns=["N"]), shape)
        await writer.prepare(dry_run=False)
        n = await writer.write_batch([{"DOC_REF": k.rstrip(), "N": 0} for k in seen],
                                     mark=LedgerMark(job_id="j_char", partition_no=0, batch_no=1, last_key=[]))
        assert n == 30                                      # every row matched by its CHAR key
    finally:
        await c.close()


async def test_oracle_timestamp9_key_paging(oracle_conn, oracle_schema) -> None:  # noqa: ANN001
    o = oracle_schema.owner
    burst = [(1, f"2025-03-01T10:00:00.123456{ns:03d}", f"v{ns}") for ns in range(60)]  # one microsecond
    tail = [(1, f"2025-03-01T10:00:00.123457{ns:03d}", "t") for ns in range(5)]
    tail += [(2, "2025-03-01T10:00:00.000000001", "c2")]
    with oracle_conn.cursor() as cur:
        cur.execute(f'CREATE TABLE "{o}"."EV" (C NUMBER(6) NOT NULL, TS TIMESTAMP(9) NOT NULL, '
                    "V VARCHAR2(8), PRIMARY KEY (C, TS))")
        cur.executemany(f'INSERT INTO "{o}"."EV" VALUES (:1, TO_TIMESTAMP(:2, {FF9}), :3)', burst + tail)
        oracle_conn.commit()
        cur.execute(f'SELECT TS FROM "{o}"."EV" WHERE C = 1 ORDER BY TS FETCH FIRST 2 ROWS ONLY')
        a, b = cur.fetchall()
        assert a[0] == b[0]                                 # the pitfall: the driver truncates to µs
    c = _connector(oracle_schema)
    try:
        shape = await c.describe(ObjectRef(owner=o, name="EV"))
        assert next(col for col in shape.columns if col.name == "TS").ts_precision == 9
        pages = await _read_all(c, shape)
        keys = [(r["C"], r["TS"]) for p in pages for r in p]
        expected = sorted((cc, ts) for cc, ts, _ in burst + tail)
        assert keys == expected and len(pages) == math.ceil(66 / 7)   # pages straight through the burst
        keycols = [col for col in shape.columns if col.is_key]
        cut = encode_key(expected[30], keycols)             # KeyRange carries encoded keys (M2)
        left = [(r["C"], r["TS"]) for p in await _read_all(c, shape, KeyRange(upper=cut)) for r in p]
        right = [(r["C"], r["TS"]) for p in await _read_all(c, shape, KeyRange(lower=cut)) for r in p]
        assert left + right == expected                     # a cut inside the burst loses nothing
        assert decode_key(encode_key(expected[0], keycols), keycols) == expected[0]
    finally:
        await c.close()


async def test_oracle_clob_astral_roundtrip(oracle_conn, oracle_schema) -> None:  # noqa: ANN001
    o = oracle_schema.owner
    astral, email = "\U0001F600", "sentinel.person@example.net"
    text = ("a" * 32766 + astral + "b" * 32766 + astral + "नमस्ते"
            + f" contact {email} " + astral * 3 + "z" * 70_000 + astral)
    units = len(text.encode("utf-16-le")) // 2
    with oracle_conn.cursor() as cur:
        cur.execute(f'CREATE TABLE "{o}"."DOCS" (ID NUMBER(6) PRIMARY KEY, BODY CLOB)')
        cur.setinputsizes(None, oracledb.DB_TYPE_CLOB)
        cur.execute(f'INSERT INTO "{o}"."DOCS" VALUES (:1, :2)', [1, text])
        oracle_conn.commit()
        cur.execute(f'SELECT DBMS_LOB.GETLENGTH(BODY) FROM "{o}"."DOCS"')
        assert int(cur.fetchone()[0]) == units and units != len(text)   # the pitfall: UTF-16 units
    c = _connector(oracle_schema)
    try:
        shape = await c.describe(ObjectRef(owner=o, name="DOCS"))
        rows = [r for p in await _read_all(c, shape) for r in p]
        assert rows[0]["BODY"] == text                      # whole value, no offset arithmetic
        masked = text.replace(email, "x" * len(email))
        target = make_target(shape, write_mode="table", object=ObjectRef(owner=o, name="DOCS_M"))
        writer = await c.writer(target, shape)
        await writer.prepare(dry_run=False)
        await writer.write_batch([{"ID": 1, "BODY": masked}],
                                 mark=LedgerMark(job_id="j_clob", partition_no=0, batch_no=1, last_key=[]))
        back = [r for p in await _read_all(c, await c.describe(ObjectRef(owner=o, name="DOCS_M"))) for r in p]
        assert back[0]["BODY"] == masked and email not in back[0]["BODY"]
        with oracle_conn.cursor() as cur:
            cur.execute(f'SELECT DBMS_LOB.GETLENGTH(BODY) FROM "{o}"."DOCS_M"')
            assert int(cur.fetchone()[0]) == units
    finally:
        await c.close()


async def test_oracle_is_json_clob_reads_as_text(oracle_conn, oracle_schema) -> None:  # noqa: ANN001
    o = oracle_schema.owner
    doc = '{"channel":"app","e":"\U0001F600\U0001F600abc"}'
    with oracle_conn.cursor() as cur:
        cur.execute(f'CREATE TABLE "{o}"."J" (ID NUMBER(6) PRIMARY KEY, PAYLOAD CLOB CHECK (PAYLOAD IS JSON))')
        cur.execute(f'INSERT INTO "{o}"."J" VALUES (1, :p)', {"p": doc})
        oracle_conn.commit()
        cur.execute(f'SELECT PAYLOAD FROM "{o}"."J"')
        assert isinstance(cur.fetchone()[0], dict)          # the pitfall: python-oracledb parses it
    c = _connector(oracle_schema)
    try:
        shape = await c.describe(ObjectRef(owner=o, name="J"))
        rows = [r for p in await _read_all(c, shape) for r in p]
        assert rows[0]["PAYLOAD"] == doc                    # verbatim text through the connector
    finally:
        await c.close()
```
`make_target(shape, **kw)` (in `oracle_fixtures.py`) defaults `object` to `shape.object` and `columns` to
the shape's non-pseudo columns.

- [ ] **Step 3: Run to see what is red** — `DPS_TEST_REQUIRE_SERVICES=1 pytest -q -m oracle tests/connectors/test_oracle_pitfalls.py tests/connectors/test_codec_m3.py tests/test_methods.py`.
  Expected red: `test_date_shift_keeps_sub_microsecond_digits` (M0 truncates), and the codec tests unless M2
  already implemented the contract's "ts keeps full precision as text" for decode. If
  `test_oracle_is_json_clob_reads_as_text` returns a dict through the connector, move the `is_json` branch
  of `output_type_handler` above the CLOB branch returning `cursor.var(metadata.type_code,
  arraysize=cursor.arraysize)` and read that LOB whole with `lob.read()` (never with offsets) in `_row`.

- [ ] **Step 4: Fix the codecs (M2 `base.py`) when red.** In `decode_key`, for tag `ts`: return the text
  unchanged when the column has `ts_precision > 6`, else `datetime.fromisoformat(text)`. In
  `to_engine_text`: a `str` value of a `timestamp` column is returned unchanged. In `from_engine_text`: for
  `kind == "timestamp"` and `ts_precision > 6`, return the text unchanged.

- [ ] **Step 5: `date_shift` keeps sub-microsecond digits (M0 `methods.py`)**
```python
_SUBMICRO = re.compile(r"^(.*T\d{2}:\d{2}:\d{2}\.\d{6})(\d{1,3})$")


def _split_submicro(value: str) -> tuple[str, str]:
    """``…:SS.ffffff`` + up to three more digits (TIMESTAMP(7-9) text) → (parseable head, tail)."""
    m = _SUBMICRO.match(value.strip())
    return (m.group(1), m.group(2)) if m else (value, "")


def _date_shift(value: str, ctx: TransformContext) -> Outcome:
    head, tail = _split_submicro(value)
    parsed = _parse_date(head, ctx.locale)
    if parsed is None:
        return _as_outcome("det_token", _det_token(value, ctx), ctx, note="fallback:unparseable_date")
    d, fmt = parsed
    shifted = d + timedelta(days=_offset_days(ctx))
    if tail and isinstance(shifted, datetime):
        out = shifted.strftime("%Y-%m-%dT%H:%M:%S.%f") + tail
    else:
        out = _format_date(shifted, fmt)
    return Outcome(out, "date_shift", True, None, {"anchor": ctx.anchor, "fmt": fmt})


def _date_shift_reverse(token: str, ctx: TransformContext, rp: dict[str, Any]) -> str:
    head, tail = _split_submicro(token)
    parsed = _parse_date(head, ctx.locale)
    if parsed is None:
        raise MethodError("date_shift token is not a date")
    d, fmt = parsed
    anchor_ctx = TransformContext(**{**ctx.__dict__, "anchor": str(rp.get("anchor", ctx.anchor))})
    back = d - timedelta(days=_offset_days(anchor_ctx))
    if tail and isinstance(back, datetime):
        return back.strftime("%Y-%m-%dT%H:%M:%S.%f") + tail
    return _format_date(back, str(rp.get("fmt", fmt)))
```
Values without a 7th fractional digit take exactly the M0 path, so existing tokens are unchanged.

- [ ] **Step 6: Run tests and gates**

Run: `DPS_TEST_REQUIRE_SERVICES=1 pytest -q -m oracle tests/connectors/ && pytest -q tests/connectors/test_codec_m3.py tests/test_methods.py && ruff check dps tests tools && ruff format --check dps tests tools && mypy dps && pytest -q`
Expected: the four pitfall tests, three codec tests and the new method test pass; every earlier Oracle and
M0/M2 test still passes.

- [ ] **Step 7: Commit** (list `base.py` only if Step 4 changed it)

```bash
git add tests/connectors/test_oracle_pitfalls.py tests/connectors/test_codec_m3.py tests/connectors/oracle_fixtures.py dps/engine/transform/methods.py tests/test_methods.py dps/layers/connectors/base.py && git commit -m "test(oracle): CLOB UTF-16, CHAR bind and TIMESTAMP(9) paging pitfalls; date_shift keeps nanoseconds" -- tests/connectors/test_oracle_pitfalls.py tests/connectors/test_codec_m3.py tests/connectors/oracle_fixtures.py dps/engine/transform/methods.py tests/test_methods.py dps/layers/connectors/base.py
```

---
### Task 8: Wire Oracle into the pipeline — registry, discovery and finalize seams, wildcard paths

**Files:**
- Create: `dps/layers/connectors/docpaths.py`
- Modify (M2): `dps/layers/connectors/registry.py`, `dps/layers/connectors/discovery.py` (and `jobs_run.py` only if a seam test is red)
- Test: `tests/connectors/test_docpaths.py`, `tests/connectors/test_registry_m3.py`, `tests/connectors/test_pipeline_seams_m3.py`

**Interfaces introduced:**

```python
# dps/layers/connectors/docpaths.py
def generalise(path: str) -> str                                  # "$.a[3].b" -> "$.a[*].b"
def expand_wildcards(doc: Any, path: str) -> list[str]            # concrete paths present in doc
def values_at(doc: Any, path: str) -> list[Any]
def column_values(rows: list[Row], column: ColumnInfo) -> list[Any]   # SQL by name, documents by path
def forced_keep(column: ColumnInfo) -> bool                       # pseudo, binary, lob_binary, object_id
def dotted(path: str) -> str | None                               # "$.a[0].b" -> "a.0.b"; "$.a[*].b" -> "a.b"
def leaf_updates(doc: dict[str, Any]) -> dict[str, Any] | None    # {"a.0.b": v, ...} minus _id; None if not dot-addressable

# dps/layers/connectors/registry.py (M2 signature, unchanged; the oracle/mongo branch is M3's)
async def open_connector(conn: Connection, settings: Settings, *, action: str, kek: bytes | None,
                         job_id: str | None = None, credentials: Credentials | None = None) -> Connector
```

**Behaviours:** `open_connector` replaces M2's `engine_unavailable` branch: for `engine == "oracle"` (and
`"mongo"` once Task 12 lands) it loads the credentials exactly as the postgres branch does
(`credentials or await load_credentials(conn, settings=settings, kek=kek)`), flattens them into the
`{username, password, dsn | uri}` mapping the M3 factories take (`Credentials.extra` carries the `dsn`/`uri`
secret files), builds the connector with `client_id=job_id` (Oracle `client_identifier`, Mongo `comment`),
opens it and returns it; the driver modules are imported lazily, and a missing extra still raises
`ConnectorError("engine_unavailable")`. Discovery reads sampled values through `column_values()` (so Mongo
`[*]` paths work) and classifies `forced_keep` columns as `keep` without running value detectors (a packed
ROWID can pass a Luhn check). M2's handlers already pass `job_id=job.id` and call `writer.clear_ledger` at
finalize; the seam tests below pin both.

- [ ] **Step 1: Write the failing tests**

`tests/connectors/test_docpaths.py`:
```python
from __future__ import annotations

from dps.layers.connectors import docpaths
from dps.layers.connectors.base import ColumnInfo

DOC = {"_id": 1, "customerId": "CUST-10000001",
       "contact": {"email": "a@example.com", "addresses": [{"line1": "1 Main"}, {"line1": "2 High", "pin": "560001"}]},
       "accounts": [], "tags": ["vip", "salary"]}


def test_generalise_and_expand() -> None:
    assert docpaths.generalise("$.contact.addresses[1].line1") == "$.contact.addresses[*].line1"
    assert docpaths.expand_wildcards(DOC, "$.contact.addresses[*].line1") == [
        "$.contact.addresses[0].line1", "$.contact.addresses[1].line1"]
    assert docpaths.expand_wildcards(DOC, "$.contact.addresses[*].pin") == ["$.contact.addresses[1].pin"]
    assert docpaths.expand_wildcards(DOC, "$.accounts[*].iban") == []
    assert docpaths.expand_wildcards(DOC, "$.missing.x") == []
    assert docpaths.values_at(DOC, "$.tags[*]") == ["vip", "salary"]


def test_column_values_for_sql_rows_and_documents() -> None:
    sql = ColumnInfo(path="$.FULL_NAME", name="FULL_NAME", data_type="VARCHAR2(120)", kind="text")
    assert docpaths.column_values([{"FULL_NAME": "A"}, {"FULL_NAME": None}], sql) == ["A", None]
    nested = ColumnInfo(path="$.contact.addresses[*].line1", name="contact", data_type="string", kind="text")
    assert docpaths.column_values([DOC, DOC], nested) == ["1 Main", "2 High", "1 Main", "2 High"]


def test_forced_keep() -> None:
    rid = ColumnInfo(path="$.ROWID", name="ROWID", data_type="ROWID", kind="int", is_key=True, pseudo=True)
    oid = ColumnInfo(path="$._id", name="_id", data_type="objectId", kind="object_id", is_key=True)
    txt = ColumnInfo(path="$.N", name="N", data_type="string", kind="text")
    assert docpaths.forced_keep(rid) and docpaths.forced_keep(oid) and not docpaths.forced_keep(txt)


def test_dotted_and_leaf_updates() -> None:
    assert docpaths.dotted("$.contact.addresses[1].line1") == "contact.addresses.1.line1"
    assert docpaths.dotted("$.accounts[*].iban") == "accounts.iban"
    assert docpaths.dotted("$['a.b']") is None and docpaths.dotted("$['$x']") is None
    upd = docpaths.leaf_updates(DOC)
    assert upd is not None and "_id" not in upd and upd["contact.addresses.1.pin"] == "560001"
    assert upd["tags.0"] == "vip" and "accounts" not in upd
    assert docpaths.leaf_updates({"_id": 1, "a.b": 1}) is None
```

`tests/connectors/test_registry_m3.py` (goes through M2's real `secret_ref` resolution — files under
`DPS_SECRET_DIR/<ref>/`, contract §5.1/§6.3 — so no M2 internal is patched):
```python
from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("oracledb")

from dps.config import Settings  # noqa: E402
from dps.layers.connectors import registry  # noqa: E402
from dps.layers.connectors.oracle import OracleConnector  # noqa: E402


def _secret(root: Path, ref: str, **files: str) -> None:
    d = root / ref
    d.mkdir(parents=True)
    for name, value in files.items():
        (d / name).write_text(value, encoding="utf-8")


async def test_registry_builds_oracle_connectors_with_session_tags(tmp_path: Path, connection_row,  # noqa: ANN001
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    _secret(tmp_path, "local/ora", username="dps_ro", password="SENTINEL-PW", dsn="oracle:1521/FREEPDB1")
    settings = Settings(_env_file=None, secret_dir=str(tmp_path))
    conn = connection_row(engine="oracle", secret_ref="local/ora",
                          params={"host": "oracle", "port": 1521, "service_name": "FREEPDB1"})
    async def no_open(self: OracleConnector) -> None:   # no network in a unit test
        return None

    monkeypatch.setattr(OracleConnector, "open", no_open)
    c = await registry.open_connector(conn, settings, action="partitions", kek=None, job_id="j_01")
    assert isinstance(c, OracleConnector) and c._action == "partitions"
    assert c._pool._client_identifier == "j_01" and "SENTINEL" not in repr(c._pool)
    await c.close()
```
(`connection_row(**fields)` is a local fixture that builds M2's `Connection` row model with defaults for the
remaining fields.)

`tests/connectors/test_pipeline_seams_m3.py`:
```python
"""Static checks that the M2 handlers use the M3 seams (AST, no database)."""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "dps" / "layers" / "connectors"


def _calls(module: str, name: str) -> list[ast.Call]:
    tree = ast.parse((ROOT / module).read_text(encoding="utf-8"))
    return [n for n in ast.walk(tree) if isinstance(n, ast.Call)
            and getattr(n.func, "id", getattr(n.func, "attr", None)) == name]


def test_every_handler_connector_carries_the_job_id() -> None:
    for module in ("jobs_run.py", "discovery.py"):
        calls = _calls(module, "open_connector")
        assert calls, module
        assert all(any(k.arg == "job_id" for k in c.keywords) for c in calls), module


def test_discovery_reads_values_through_column_values_and_forced_keep() -> None:
    text = (ROOT / "discovery.py").read_text(encoding="utf-8")
    assert "column_values(" in text and "forced_keep(" in text


def test_finalize_clears_the_write_ledger() -> None:
    assert "clear_ledger" in (ROOT / "jobs_run.py").read_text(encoding="utf-8")
```

- [ ] **Step 2: Run to confirm failure** — `pytest -q tests/connectors/test_docpaths.py tests/connectors/test_registry_m3.py tests/connectors/test_pipeline_seams_m3.py` → FAIL.

- [ ] **Step 3: Write `dps/layers/connectors/docpaths.py`**
```python
"""Wildcard JSON paths over nested documents, and the discovery/transform seams that use them.

A TableShape column path may carry ``[*]`` (``$.contact.addresses[*].line1``): one column for every
element of an array. These helpers expand such a path against a concrete document, read every value it
addresses, and turn concrete paths into MongoDB dotted notation for ``$set``.
"""
from __future__ import annotations

import re
from typing import Any, Final

from dps.engine.jsonpath import flatten, get_path, parse_path
from dps.layers.connectors.base import ColumnInfo, Row

_INDEX: Final = re.compile(r"\[\d+\]")
_FORCED_KEEP_KINDS: Final = frozenset({"binary", "lob_binary", "object_id"})


def generalise(path: str) -> str:
    """Replace every array index with ``[*]``."""
    return _INDEX.sub("[*]", path)


def expand_wildcards(doc: Any, path: str) -> list[str]:
    """The concrete paths ``path`` addresses in ``doc`` (missing branches yield nothing)."""
    head, sep, tail = path.partition("[*]")
    if not sep:
        try:
            get_path(doc, path)
        except (KeyError, IndexError, TypeError):
            return []
        return [path]
    try:
        node = get_path(doc, head)
    except (KeyError, IndexError, TypeError):
        return []
    if not isinstance(node, list):
        return []
    out: list[str] = []
    for i in range(len(node)):
        out.extend(expand_wildcards(doc, f"{head}[{i}]{tail}"))
    return out


def values_at(doc: Any, path: str) -> list[Any]:
    return [get_path(doc, p) for p in expand_wildcards(doc, path)]


def column_values(rows: list[Row], column: ColumnInfo) -> list[Any]:
    """Every sampled value of ``column``: SQL rows by column name, documents by (wildcard) path."""
    top_level = column.path in (f"$.{column.name}", f"$['{column.name}']")
    out: list[Any] = []
    for row in rows:
        if top_level:
            if column.name in row:
                out.append(row[column.name])
        else:
            out.extend(values_at(row, column.path))
    return out


def forced_keep(column: ColumnInfo) -> bool:
    """Columns classified ``keep`` without value detection: pseudo keys, binary data, ObjectIds."""
    return column.pseudo or column.kind in _FORCED_KEEP_KINDS or column.data_type == "ROWID"


def dotted(path: str) -> str | None:
    """``$.a[0].b`` → ``a.0.b``; ``[*]`` is dropped (Mongo matches array elements implicitly).

    ``None`` when a segment cannot be dot-addressed (contains ``.``, starts with ``$`` or is empty).
    """
    parts = parse_path(path.replace("[*]", ""))
    out: list[str] = []
    for part in parts:
        if isinstance(part, int):
            out.append(str(part))
        elif not part or "." in part or part.startswith("$"):
            return None
        else:
            out.append(part)
    return ".".join(out) if out else None


def leaf_updates(doc: dict[str, Any]) -> dict[str, Any] | None:
    """``$set`` document of every scalar leaf except ``_id``; ``None`` if any leaf is not dot-addressable."""
    updates: dict[str, Any] = {}
    for path, value in flatten(doc):
        if path == "$._id" or path.startswith(("$._id.", "$._id[")):
            continue
        key = dotted(path)
        if key is None:
            return None
        updates[key] = value
    return updates
```

- [ ] **Step 4: Registry branch** (M2 `registry.py`) — replace the `oracle`/`mongo` `engine_unavailable`
  branch:
```python
    if conn.engine in ("oracle", "mongo"):
        creds = credentials or await load_credentials(conn, settings=settings, kek=kek)
        secret = {"username": creds.username, "password": creds.password.get_secret_value(),
                  **{name: value.get_secret_value() for name, value in creds.extra.items()}}
        try:
            if conn.engine == "oracle":
                from dps.layers.connectors.oracle import OracleConnector as factory
            else:
                from dps.layers.connectors.mongo import MongoConnector as factory
        except ImportError:
            raise ConnectorError("engine_unavailable", "driver extra not installed") from None
        connector = factory.from_secret(secret, conn.params, settings, action=action, client_id=job_id)
        await connector.open()
        return connector
```
  (until Task 12 lands, the `mongo` import fails and keeps answering `engine_unavailable`).
- [ ] **Step 5: Discovery seam** (M2 file): in `discovery.py` replace the per-column value read by
  `docpaths.column_values(sample_rows, column)` and, before running value detectors for a column,
  `if docpaths.forced_keep(column): classify as keep (entity_type None, method keep, detectors [])`. If the
  static seam tests show a handler `open_connector` call without `job_id=` or a finalize without
  `clear_ledger`, add them there (M2 touch point, logged).

- [ ] **Step 6: Run tests and gates**

Run: `pytest -q tests/connectors/test_docpaths.py tests/connectors/test_registry_m3.py tests/connectors/test_pipeline_seams_m3.py && ruff check dps tests tools && ruff format --check dps tests tools && mypy dps && pytest -q`
Expected: all pass; the M2 discovery and jobs_run suites still pass (their behaviour for PG rows is unchanged:
`column_values` reads SQL rows by name exactly as before).

- [ ] **Step 7: Commit**

```bash
git add dps/layers/connectors/docpaths.py dps/layers/connectors/registry.py dps/layers/connectors/discovery.py tests/connectors/test_docpaths.py tests/connectors/test_registry_m3.py tests/connectors/test_pipeline_seams_m3.py && git commit -m "feat(connectors): wire Oracle into the registry; wildcard paths and job-id tagging seams" -- dps/layers/connectors/docpaths.py dps/layers/connectors/registry.py dps/layers/connectors/discovery.py tests/connectors/test_docpaths.py tests/connectors/test_registry_m3.py tests/connectors/test_pipeline_seams_m3.py
```

---
### Task 9: MongoDB document rules — BSON labels, shape inference, type restoration (pure, offline)

**Files:**
- Create: `dps/layers/connectors/mongo_docs.py`
- Test: `tests/connectors/test_mongo_docs.py`

**Interfaces introduced:**

```python
KIND_OF: Final[dict[str, str]]          # BSON label -> ColumnInfo.kind
def bson_type(value: Any) -> str        # "string"|"int"|"long"|"double"|"decimal"|"bool"|"date"|"objectId"|"binData"|"uuid"|"null"|"other"
def top_level(path: str) -> str         # "$.contact.addresses[*].line1" -> "contact"
def infer_shape(obj: ObjectRef, docs: list[dict[str, Any]], *, row_estimate: int | None) -> TableShape
def coerce_bson(doc: dict[str, Any], shape: TableShape) -> dict[str, Any]   # in place; returns doc
def make_bson_safe(value: Any) -> Any   # decimal.Decimal -> Decimal128, date -> datetime, recursively
```

**Behaviours:** a shape has one column per generalised leaf path (`[*]` for array elements), `_id` first;
`data_type` is the BSON label (`"long"` when ints and longs mix, `"mixed(a,b)"` otherwise with kind
`other`); `nullable` when any sample had null or lacked the path; `key = ["_id"]`, `key_source =
"primary_key"` when every sampled `_id` has one scalar BSON type (ints and longs count as one), else
`key = []`, `key_source = "none"` (mixed or document `_id`s cannot be range-partitioned: BSON type
bracketing would drop documents of the other types; `in_place`/`append` then fail `no_key`).
`coerce_bson` restores the shape's BSON type at every typed path after the engine converted values to text
and back: `Int64` stays `Int64`, `Decimal128` stays `Decimal128`, a `date` stays a `datetime`, an ObjectId
string becomes an `ObjectId`; a value that cannot be restored (a token in a numeric field) is left as is.
`make_bson_safe` is the last pass before every write so a `decimal.Decimal` or `datetime.date` produced
anywhere (pymongo cannot encode either) never fails a batch.

- [ ] **Step 1: Write the failing tests** — `tests/connectors/test_mongo_docs.py`:
```python
from __future__ import annotations

import datetime as dt
import decimal

import pytest

bson = pytest.importorskip("bson")
from bson import Decimal128, Int64, ObjectId  # noqa: E402

from dps.layers.connectors import mongo_docs as md  # noqa: E402
from dps.layers.connectors.base import ObjectRef  # noqa: E402

OBJ = ObjectRef(name="profiles")


def _doc(i: int, **extra: object) -> dict:
    return {"_id": ObjectId(), "customerId": f"CUST-{10000000 + i}", "riskScore": Int64(700 + i),
            "profile": {"fullName": f"Person {i}", "dob": dt.datetime(1980, 1, 1)},
            "accounts": [{"iban": f"GB00TEST{i:010d}", "balance": Decimal128("10.50")}], **extra}


def test_bson_labels() -> None:
    assert [md.bson_type(v) for v in (None, True, Int64(1), 5, 2**40, 1.5, Decimal128("1"), "x",
                                      dt.datetime(2020, 1, 1), ObjectId(), b"\x00")] == [
        "null", "bool", "long", "int", "long", "double", "decimal", "string", "date", "objectId", "binData"]


def test_infer_shape_paths_kinds_and_key() -> None:
    docs = [_doc(i) for i in range(10)] + [_doc(10, nick=None)]
    shape = md.infer_shape(OBJ, docs, row_estimate=11)
    by = {c.path: c for c in shape.columns}
    assert shape.columns[0].path == "$._id" and shape.key == ["_id"] and shape.key_source == "primary_key"
    assert by["$.accounts[*].iban"].kind == "text" and by["$.accounts[*].iban"].name == "accounts"
    assert by["$.accounts[*].balance"].data_type == "decimal" and by["$.riskScore"].data_type == "long"
    assert by["$.profile.dob"].kind == "timestamp" and by["$._id"].kind == "object_id"
    assert by["$.nick"].nullable is True and by["$.customerId"].nullable is False


def test_mixed_and_document_ids_have_no_range_key() -> None:
    mixed = [_doc(1), {**_doc(2), "_id": "s-2"}]
    assert md.infer_shape(OBJ, mixed, row_estimate=None).key_source == "none"
    ints = [{**_doc(1), "_id": 1}, {**_doc(2), "_id": Int64(2**40)}]
    assert md.infer_shape(OBJ, ints, row_estimate=None).key == ["_id"]
    compound = [{**_doc(1), "_id": {"a": 1}}]
    assert md.infer_shape(OBJ, compound, row_estimate=None).key_source == "none"


def test_coerce_restores_bson_types() -> None:
    shape = md.infer_shape(OBJ, [_doc(1)], row_estimate=1)
    out = {"_id": "0123456789abcdef01234567", "customerId": "CUST-73104482", "riskScore": 731,
           "profile": {"fullName": "Tilqoe Mzvhrpkat", "dob": dt.date(1979, 6, 2)},
           "accounts": [{"iban": "GB00TEST0000000001", "balance": decimal.Decimal("10.50")}]}
    md.coerce_bson(out, shape)
    assert isinstance(out["_id"], ObjectId) and isinstance(out["riskScore"], Int64)
    assert out["profile"]["dob"] == dt.datetime(1979, 6, 2)
    assert out["accounts"][0]["balance"] == Decimal128("10.50")
    token = {"riskScore": "k7fq2wxb3n"}
    md.coerce_bson(token, shape)
    assert token["riskScore"] == "k7fq2wxb3n"          # cannot be restored: left as is


def test_make_bson_safe_is_recursive() -> None:
    v = md.make_bson_safe({"a": [decimal.Decimal("1.2"), {"d": dt.date(2020, 1, 2)}], "t": dt.datetime(2020, 1, 1)})
    assert v == {"a": [Decimal128("1.2"), {"d": dt.datetime(2020, 1, 2)}], "t": dt.datetime(2020, 1, 1)}
    bson.encode(v)                                     # encodes without InvalidDocument
```

- [ ] **Step 2: Run to confirm failure** — `pytest -q tests/connectors/test_mongo_docs.py` → FAIL
  (`ModuleNotFoundError: dps.layers.connectors.mongo_docs`).

- [ ] **Step 3: Write `dps/layers/connectors/mongo_docs.py`**
```python
"""BSON-aware helpers for the MongoDB connector: type labels, shape inference from a $sample, and type
restoration before writes, so a masked copy keeps Int64, Decimal128 and date fields as their BSON types."""
from __future__ import annotations

import datetime as dt
import decimal
import re
import uuid
from collections import Counter
from typing import Any, Final

from bson import Binary, Decimal128, Int64, ObjectId

from dps.engine.jsonpath import flatten, get_path, parse_path, set_path
from dps.layers.connectors.base import ColumnInfo, ObjectRef, TableShape
from dps.layers.connectors.docpaths import expand_wildcards, generalise

KIND_OF: Final[dict[str, str]] = {
    "string": "text", "int": "int", "long": "int", "double": "float", "decimal": "decimal", "bool": "bool",
    "date": "timestamp", "objectId": "object_id", "binData": "binary", "uuid": "uuid", "null": "other",
}
_INT_TEXT: Final = re.compile(r"-?\d{1,19}")
_NUM_TEXT: Final = re.compile(r"-?\d+(\.\d+)?([eE][-+]?\d+)?")
_SKIP_LABELS: Final = frozenset({"string", "bool", "null", "other"})


def bson_type(value: Any) -> str:
    """The BSON type label of a decoded value (pymongo decodes int64 as ``Int64``)."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, Int64):
        return "long"
    if isinstance(value, int):
        return "int" if -(2**31) <= value < 2**31 else "long"
    if isinstance(value, float):
        return "double"
    if isinstance(value, Decimal128):
        return "decimal"
    if isinstance(value, str):
        return "string"
    if isinstance(value, dt.datetime):
        return "date"
    if isinstance(value, ObjectId):
        return "objectId"
    if isinstance(value, uuid.UUID):
        return "uuid"
    if isinstance(value, (bytes, Binary)):
        return "binData"
    return "other"


def top_level(path: str) -> str:
    return str(parse_path(path.replace("[*]", "[0]"))[0])


def _id_type(doc: dict[str, Any]) -> str:
    value = doc.get("_id")
    return "document" if isinstance(value, (dict, list)) else bson_type(value)


def infer_shape(obj: ObjectRef, docs: list[dict[str, Any]], *, row_estimate: int | None) -> TableShape:
    """One column per generalised leaf path across ``docs``; ``_id`` is the key when its type is uniform."""
    types: dict[str, Counter[str]] = {}
    present: Counter[str] = Counter()
    for doc in docs:
        seen: set[str] = set()
        for path, value in flatten(doc):
            g = generalise(path)
            types.setdefault(g, Counter())[bson_type(value)] += 1
            seen.add(g)
        present.update(seen)
    columns: list[ColumnInfo] = []
    for g in sorted(types, key=lambda p: (p != "$._id", p)):
        counts = types[g]
        non_null = [t for t, _ in counts.most_common() if t != "null"]
        if not non_null:
            label, kind = "null", "other"
        elif len(non_null) == 1:
            label, kind = non_null[0], KIND_OF.get(non_null[0], "other")
        elif set(non_null) <= {"int", "long"}:
            label, kind = "long", "int"
        else:
            label, kind = "mixed(" + ",".join(sorted(non_null)) + ")", "other"
        columns.append(ColumnInfo(
            path=g, name=top_level(g), data_type=label, kind=kind,  # type: ignore[arg-type]
            nullable=counts["null"] > 0 or present[g] < len(docs), is_key=g == "$._id"))
    id_types = {_id_type(d) for d in docs}
    uniform = bool(docs) and "document" not in id_types and (len(id_types) == 1 or id_types <= {"int", "long"})
    key, source = (["_id"], "primary_key") if uniform else ([], "none")
    return TableShape(object=obj, columns=columns, key=key, key_source=source,  # type: ignore[arg-type]
                      row_estimate=row_estimate)


def _coerce(value: Any, label: str) -> Any:
    if value is None or isinstance(value, bool):
        return value
    try:
        if label == "long":
            if isinstance(value, int) and not isinstance(value, Int64):
                return Int64(value)
            if isinstance(value, str) and _INT_TEXT.fullmatch(value):
                return Int64(int(value))
        elif label == "int":
            if isinstance(value, str) and _INT_TEXT.fullmatch(value):
                value = int(value)
            if isinstance(value, int):
                return int(value) if -(2**31) <= value < 2**31 else Int64(value)
        elif label == "double":
            if isinstance(value, (int, decimal.Decimal, Decimal128)):
                return float(str(value))
            if isinstance(value, str) and _NUM_TEXT.fullmatch(value):
                return float(value)
        elif label == "decimal":
            if isinstance(value, decimal.Decimal):
                return Decimal128(value)
            if isinstance(value, (int, float)):
                return Decimal128(str(value))
            if isinstance(value, str) and _NUM_TEXT.fullmatch(value):
                return Decimal128(value)
        elif label == "date":
            if isinstance(value, dt.datetime):
                return value
            if isinstance(value, dt.date):
                return dt.datetime(value.year, value.month, value.day)
            if isinstance(value, str):
                return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        elif label == "objectId":
            if isinstance(value, str) and ObjectId.is_valid(value):
                return ObjectId(value)
        elif label == "uuid" and isinstance(value, str):
            return uuid.UUID(value)
    except (ValueError, ArithmeticError, decimal.InvalidOperation):
        return value
    return value


def coerce_bson(doc: dict[str, Any], shape: TableShape) -> dict[str, Any]:
    """Restore the shape's BSON type at every typed path of ``doc`` (in place)."""
    for col in shape.columns:
        if col.data_type in _SKIP_LABELS or col.data_type.startswith("mixed"):
            continue
        for path in expand_wildcards(doc, col.path):
            old = get_path(doc, path)
            new = _coerce(old, col.data_type)
            if new is not old:
                set_path(doc, path, new)
    return doc


def make_bson_safe(value: Any) -> Any:
    """Recursively turn values pymongo cannot encode into their BSON equivalents."""
    if isinstance(value, dict):
        return {k: make_bson_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [make_bson_safe(v) for v in value]
    if isinstance(value, decimal.Decimal):
        return Decimal128(value)
    if isinstance(value, dt.date) and not isinstance(value, dt.datetime):
        return dt.datetime(value.year, value.month, value.day)
    return value
```

- [ ] **Step 4: Run tests and gates** — `pytest -q tests/connectors/test_mongo_docs.py && ruff check dps tests tools && ruff format --check dps tests tools && mypy dps && pytest -q` → 5 passed; suite green.

- [ ] **Step 5: Commit**

```bash
git add dps/layers/connectors/mongo_docs.py tests/connectors/test_mongo_docs.py && git commit -m "feat(mongo): BSON type labels, path shape inference from samples, type restoration before writes" -- dps/layers/connectors/mongo_docs.py tests/connectors/test_mongo_docs.py
```

---

### Task 10: Mongo filter validation (operator allow-list, `$where` rejected) and compilation

**Files:**
- Modify (M2): `dps/layers/connectors/selection.py` (harden M2's `validate_mongo_filter` — same name and signature, stricter body; new `compile_mongo_filter`; `params` rule in `validate_selection`)
- Test: `tests/connectors/test_selection_mongo.py`, `tests/connectors/test_selection_oracle.py`

**Interfaces introduced:**

```python
MONGO_OPERATORS: Final = frozenset({"$eq","$ne","$gt","$gte","$lt","$lte","$in","$nin","$and","$or",
                                    "$nor","$not","$exists","$type","$size","$all","$elemMatch","$regex"})
EJSON_WRAPPERS: Final = frozenset({"$date", "$oid", "$numberLong", "$numberDecimal"})   # value position only
MAX_FILTER_DEPTH: Final = 8; MAX_FILTER_NODES: Final = 500; MAX_REGEX_LEN: Final = 256; MAX_IN_ITEMS: Final = 1000
def validate_mongo_filter(f: dict[str, Any]) -> None            # raises SelectionError(code)
def compile_mongo_filter(f: dict[str, Any] | None) -> dict[str, Any]   # EJSON wrappers -> BSON values
```

**Behaviours (contract §5.2):** any operator outside the allow-list — `$where`, `$expr`, `$function`,
`$accumulator`, `$jsonSchema`, `$text`, `$near`, `$options`, … — anywhere in the tree (top level, inside
`$and`/`$or`/`$nor`, inside a field condition, `$not`, `$elemMatch`, or a literal embedded document) →
`selection_operator_denied`; field names must match `^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$`
→ else `selection_identifier_invalid`; structural errors (`$and` not a non-empty list, `$in` not a list, `$size`
not a non-negative int, `$exists` not a bool, an operator document mixed with plain keys) →
`selection_invalid`; depth > 8 or > 500 nodes → `selection_too_complex`; `$regex` longer than 256 →
`selection_regex_too_long` (case-insensitive matching uses the inline `(?i)` flag; `$options` is not on the
contract's allow-list); `$in`/`$nin`/`$all` over 1000 items → `selection_too_complex`. `where`, `query` or
`params` together with `engine="mongo"` → `selection_engine_mismatch` (M2's code); `mongo_filter` with a SQL
engine → `selection_engine_mismatch`. `compile_mongo_filter` returns a new dict with EJSON wrappers converted
(`{"$date": "2025-01-01T00:00:00Z"}` → `datetime`, `$oid` → `ObjectId`, `$numberLong` → `Int64`,
`$numberDecimal` → `Decimal128`); a malformed wrapper → `selection_invalid`.

- [ ] **Step 1: Write the failing tests** — `tests/connectors/test_selection_mongo.py`:
```python
from __future__ import annotations

import datetime as dt

import pytest

pytest.importorskip("bson")
from bson import Decimal128, Int64, ObjectId  # noqa: E402

from dps.layers.connectors.selection import (  # noqa: E402
    Selection, SelectionError, compile_mongo_filter, validate_mongo_filter, validate_selection,
)

OK = [
    {"segment": "retail"},
    {"riskScore": {"$gte": 600, "$lt": 900}},
    {"$or": [{"contact.email": {"$exists": True}}, {"tags": {"$all": ["vip", "salary"]}}]},
    {"accounts": {"$elemMatch": {"currency": "INR", "balance": {"$gt": 0}}}},
    {"accounts": {"$size": 2}},
    {"profile.fullName": {"$regex": "(?i)^an"}},
    {"riskScore": {"$not": {"$gt": 900}}},
    {"createdAt": {"$gte": {"$date": "2025-01-01T00:00:00Z"}}},
    {"_id": {"$in": [{"$oid": "0123456789abcdef01234567"}]}},
    {"address": {"city": "Pune", "pin": "411001"}},
]
DENIED = [
    {"$where": "this.a == 1"},
    {"$expr": {"$eq": ["$a", "$b"]}},
    {"a": {"$function": {"body": "x", "args": [], "lang": "js"}}},
    {"$and": [{"a": 1}, {"$where": "1"}]},
    {"a": {"$elemMatch": {"$where": "1"}}},
    {"a": {"$not": {"$expr": 1}}},
    {"a": {"$regex": "x", "$options": "i"}},
    {"a": {"$accumulator": {}}},
    {"$jsonSchema": {}},
    {"a": {"b": {"$where": "1"}}},
]


@pytest.mark.parametrize("f", OK)
def test_allowed_filters(f: dict) -> None:
    validate_mongo_filter(f)


@pytest.mark.parametrize("f", DENIED)
def test_denied_operators(f: dict) -> None:
    with pytest.raises(SelectionError) as info:
        validate_mongo_filter(f)
    assert info.value.code == "selection_operator_denied"


@pytest.mark.parametrize(
    "f,code",
    [({"bad-field": 1}, "selection_identifier_invalid"), ({"$and": []}, "selection_invalid"),
     ({"a": {"$in": 5}}, "selection_invalid"), ({"a": {"$size": -1}}, "selection_invalid"),
     ({"a": {"$exists": "yes"}}, "selection_invalid"), ({"a": {"$gt": 1, "b": 2}}, "selection_invalid"),
     ({"a": {"$regex": "x" * 257}}, "selection_regex_too_long"),
     ({"a": {"$in": list(range(1001))}}, "selection_too_complex"),
     ({"a": {"$date": "not-a-date"}}, "selection_invalid")],
)
def test_structural_errors(f: dict, code: str) -> None:
    with pytest.raises(SelectionError) as info:
        validate_mongo_filter(f)
        compile_mongo_filter(f)
    assert info.value.code == code


def test_depth_limit() -> None:
    f: dict = {"a": 1}
    for _ in range(9):
        f = {"$and": [f]}
    with pytest.raises(SelectionError) as info:
        validate_mongo_filter(f)
    assert info.value.code == "selection_too_complex"


def test_compile_converts_ejson_literals_without_mutating() -> None:
    f = {"createdAt": {"$gte": {"$date": "2025-01-01T00:00:00Z"}}, "n": {"$numberLong": "9007199254740993"},
         "amt": {"$lt": {"$numberDecimal": "10.50"}}, "_id": {"$oid": "0123456789abcdef01234567"}}
    out = compile_mongo_filter(f)
    assert out["createdAt"]["$gte"] == dt.datetime(2025, 1, 1, tzinfo=dt.UTC)
    assert out["n"] == Int64(9007199254740993) and out["amt"]["$lt"] == Decimal128("10.50")
    assert out["_id"] == ObjectId("0123456789abcdef01234567") and f["n"] == {"$numberLong": "9007199254740993"}


def test_engine_mismatch() -> None:
    with pytest.raises(SelectionError):
        validate_selection(Selection(where="a = 1"), "mongo", None)
    with pytest.raises(SelectionError):
        validate_selection(Selection(mongo_filter={"a": 1}), "oracle", None)
    with pytest.raises(SelectionError):
        validate_selection(Selection(mongo_filter={"a": 1}, params={"x": 1}), "mongo", None)
```
`tests/connectors/test_selection_oracle.py` (pins M2's sqlglot validation for the Oracle dialect):
```python
from __future__ import annotations

import pytest

pytest.importorskip("sqlglot")
from dps.layers.connectors.selection import Selection, SelectionError, compile_where, quote_ident, validate_selection  # noqa: E402

ALLOWED = ["CREATED_AT >= DATE '2025-01-01'", "SEGMENT = :seg AND RISK_SCORE BETWEEN :lo AND :hi",
           "CREATED_AT >= TO_DATE(:d, 'YYYY-MM-DD')", "UPPER(CITY) IN ('PUNE', 'MUMBAI')"]
REJECTED = ["1 = 1; DROP TABLE CRM.CUSTOMERS", "SEGMENT = 'x' FOR UPDATE",
            "DBMS_PIPE.RECEIVE_MESSAGE('a', 10) = 0", "UTL_HTTP.REQUEST('http://x') IS NOT NULL",
            "SYS.DBMS_LOCK.SLEEP(5) IS NULL", "XMLTYPE('<a/>') IS NOT NULL",
            "HTTPURITYPE('http://x').GETCLOB() IS NOT NULL",
            "CUSTOMER_ID IN (SELECT CUSTOMER_ID FROM CRM.CUSTOMERS FOR UPDATE)"]


@pytest.mark.parametrize("where", ALLOWED)
def test_oracle_where_allowed(where: str) -> None:
    validate_selection(Selection(where=where), "oracle", None)


@pytest.mark.parametrize("where", REJECTED)
def test_oracle_where_rejected(where: str) -> None:
    with pytest.raises(SelectionError):
        validate_selection(Selection(where=where), "oracle", None)


def test_oracle_binds_are_named_and_ordered() -> None:
    fragment, names = compile_where(Selection(where="SEGMENT = :seg AND RISK_SCORE > :lo"), "oracle")
    assert ":seg" in fragment and ":lo" in fragment and names == ["seg", "lo"]


def test_oracle_identifier_rule() -> None:
    assert quote_ident("crm", "oracle") == '"CRM"' and quote_ident("A$B#1", "oracle") == '"A$B#1"'
    for bad in ("1A", "A B", 'A"B', "x" * 129):
        with pytest.raises(SelectionError):
            quote_ident(bad, "oracle")
```

- [ ] **Step 2: Run to confirm failure** — `pytest -q tests/connectors/test_selection_mongo.py tests/connectors/test_selection_oracle.py`
  → `ImportError: cannot import name 'compile_mongo_filter'`; any red Oracle case is an M2 selection gap
  fixed in `selection.py` in this task (same commit, logged in the deviations file).

- [ ] **Step 3: Implement in `selection.py`** (replaces any M2 Mongo branch body; the tests are the spec):
```python
MONGO_OPERATORS: Final = frozenset({"$eq", "$ne", "$gt", "$gte", "$lt", "$lte", "$in", "$nin", "$and",
                                    "$or", "$nor", "$not", "$exists", "$type", "$size", "$all",
                                    "$elemMatch", "$regex"})
EJSON_WRAPPERS: Final = frozenset({"$date", "$oid", "$numberLong", "$numberDecimal"})
MAX_FILTER_DEPTH: Final = 8
MAX_FILTER_NODES: Final = 500
MAX_REGEX_LEN: Final = 256
MAX_IN_ITEMS: Final = 1000
_MONGO_FIELD: Final = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*")
_LOGICAL: Final = frozenset({"$and", "$or", "$nor"})


def _deny(code: str) -> SelectionError:
    """A value-free ``SelectionError`` (M2 signature: code, message); the message names the rule only."""
    return SelectionError(code, f"the MongoDB filter breaks rule {code}")


class _Budget:
    def __init__(self) -> None:
        self.left = MAX_FILTER_NODES

    def take(self) -> None:
        self.left -= 1
        if self.left < 0:
            raise _deny("selection_too_complex")


def _is_operator_doc(value: Any) -> bool:
    return isinstance(value, dict) and bool(value) and all(isinstance(k, str) and k.startswith("$") for k in value)


def _literal(value: Any, budget: _Budget, depth: int) -> None:
    if value is None or isinstance(value, (str, int, float, bool)):
        return                                   # scalars cost nothing: a 1000-item $in stays legal
    if isinstance(value, dict) and len(value) == 1 and next(iter(value)) in EJSON_WRAPPERS:
        return                                   # {"$oid": ...} and friends are literals, not operators
    budget.take()
    if depth > MAX_FILTER_DEPTH:
        raise _deny("selection_too_complex")
    if isinstance(value, dict):
        keys = list(value)
        if keys and all(isinstance(k, str) and k.startswith("$") for k in keys):
            raise _deny("selection_operator_denied")
        for k, v in value.items():
            if not isinstance(k, str) or k.startswith("$"):
                raise _deny("selection_operator_denied")
            _literal(v, budget, depth + 1)
    elif isinstance(value, list):
        if len(value) > MAX_IN_ITEMS:
            raise _deny("selection_too_complex")
        for v in value:
            _literal(v, budget, depth + 1)
    else:
        raise _deny("selection_invalid")


def _condition(value: Any, budget: _Budget, depth: int) -> None:
    if depth > MAX_FILTER_DEPTH:
        raise _deny("selection_too_complex")
    if not _is_operator_doc(value):
        if isinstance(value, dict) and any(isinstance(k, str) and k.startswith("$") for k in value):
            raise _deny("selection_invalid")          # operators mixed with plain keys
        _literal(value, budget, depth)
        return
    if len(value) == 1 and next(iter(value)) in EJSON_WRAPPERS:
        _literal(value, budget, depth)
        return
    for op, arg in value.items():
        budget.take()
        if op not in MONGO_OPERATORS:
            raise _deny("selection_operator_denied")
        if op in _LOGICAL:
            raise _deny("selection_invalid")          # $and/$or belong at document level
        if op in ("$in", "$nin", "$all"):
            if not isinstance(arg, list):
                raise _deny("selection_invalid")
            if len(arg) > MAX_IN_ITEMS:
                raise _deny("selection_too_complex")
            for item in arg:
                _literal(item, budget, depth + 1)
        elif op == "$not":
            if not _is_operator_doc(arg):
                raise _deny("selection_invalid")
            _condition(arg, budget, depth + 1)
        elif op == "$elemMatch":
            if not isinstance(arg, dict) or not arg:
                raise _deny("selection_invalid")
            if _is_operator_doc(arg):
                _condition(arg, budget, depth + 1)
            else:
                _document(arg, budget, depth + 1)
        elif op == "$regex":
            if not isinstance(arg, str):
                raise _deny("selection_invalid")
            if len(arg) > MAX_REGEX_LEN:
                raise _deny("selection_regex_too_long")
        elif op == "$exists":
            if not isinstance(arg, bool):
                raise _deny("selection_invalid")
        elif op == "$size":
            if not isinstance(arg, int) or isinstance(arg, bool) or arg < 0:
                raise _deny("selection_invalid")
        elif op == "$type":
            items = arg if isinstance(arg, list) else [arg]
            if not items or not all(isinstance(t, (str, int)) and not isinstance(t, bool) for t in items):
                raise _deny("selection_invalid")
        else:
            _literal(arg, budget, depth + 1)


def _document(doc: Any, budget: _Budget, depth: int) -> None:
    if not isinstance(doc, dict):
        raise _deny("selection_invalid")
    if depth > MAX_FILTER_DEPTH:
        raise _deny("selection_too_complex")
    for key, value in doc.items():
        budget.take()
        if not isinstance(key, str):
            raise _deny("selection_invalid")
        if key.startswith("$"):
            if key in _LOGICAL:
                if not isinstance(value, list) or not value:
                    raise _deny("selection_invalid")
                for sub in value:
                    _document(sub, budget, depth + 1)
            elif key in MONGO_OPERATORS:
                raise _deny("selection_invalid")        # e.g. a top-level $eq
            else:
                raise _deny("selection_operator_denied")
        else:
            if not _MONGO_FIELD.fullmatch(key):
                raise _deny("selection_identifier_invalid")
            _condition(value, budget, depth + 1)


def validate_mongo_filter(f: dict[str, Any]) -> None:
    """Allow-list validation of a Mongo filter document (contract §5.2); raises ``SelectionError``."""
    _document(f, _Budget(), 0)


def _ejson(value: Any) -> Any:
    from bson import Decimal128, Int64, ObjectId   # local: bson ships with the connectors extra

    if isinstance(value, dict):
        if len(value) == 1 and next(iter(value)) in EJSON_WRAPPERS:
            (tag, raw), = value.items()
            try:
                if tag == "$date":
                    if isinstance(raw, int) and not isinstance(raw, bool):
                        return datetime.fromtimestamp(raw / 1000, tz=UTC)
                    parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
                    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
                if tag == "$oid":
                    if not ObjectId.is_valid(str(raw)):
                        raise ValueError
                    return ObjectId(str(raw))
                if tag == "$numberLong":
                    return Int64(int(str(raw)))
                return Decimal128(str(raw))
            except (ValueError, TypeError, ArithmeticError):
                raise _deny("selection_invalid") from None
        return {k: _ejson(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_ejson(v) for v in value]
    return value


def compile_mongo_filter(f: dict[str, Any] | None) -> dict[str, Any]:
    """A validated filter as a new dict with Extended-JSON literals converted to BSON values."""
    if not f:
        return {}
    validate_mongo_filter(f)
    out = _ejson(f)
    assert isinstance(out, dict)  # noqa: S101 - a dict in, a dict out
    return out
```
M2's `validate_selection(sel, engine, shape)` already routes `engine == "mongo"` to `validate_mongo_filter`
and raises `selection_engine_mismatch` for SQL text on Mongo (and a filter on SQL engines); M3 adds one rule
there: `params` with `engine == "mongo"` → `selection_engine_mismatch` (Mongo literals live in the filter).
M2's `mongo_filter_template` stays as is (EJSON wrappers become `{"$date": "?"}`). Imports:
`from datetime import UTC, datetime`.

Note on the structural-error test for `$date`: `validate_mongo_filter` accepts the wrapper's shape;
`compile_mongo_filter` rejects the unparseable value — the test calls both inside one `raises` block.

- [ ] **Step 4: Run tests and gates** — `pytest -q tests/connectors/test_selection_mongo.py tests/connectors/test_selection_oracle.py && ruff check dps tests tools && ruff format --check dps tests tools && mypy dps && pytest -q`
  → 10 allowed + 10 denied + 9 structural + depth + compile + engine-mismatch, and the Oracle cases, pass; the
  M2 selection suite still passes.

- [ ] **Step 5: Commit**

```bash
git add dps/layers/connectors/selection.py tests/connectors/test_selection_mongo.py tests/connectors/test_selection_oracle.py && git commit -m "feat(selection): Mongo filter operator allow-list with \$where rejected; Extended JSON literals" -- dps/layers/connectors/selection.py tests/connectors/test_selection_mongo.py tests/connectors/test_selection_oracle.py
```

---
### Task 11: `MongoConnector` — open, test, list, describe, count, `$sample`, keyset on `_id`

**Files:**
- Create: `dps/layers/connectors/mongo.py`
- Test: `tests/connectors/test_mongo_connector.py` (markers `integration`, `mongo`), `tests/connectors/mongo_fixtures.py`

**Interfaces introduced:**

```python
LEDGER_COLLECTION: Final = "dps_write_ledger"
def mongo_code(exc: BaseException) -> str                    # "mongo:11000" | "mongo:ServerSelectionTimeoutError" — never a message
def connector_error(code: str, exc: BaseException) -> ConnectorError
class MongoConnector:                                        # implements base.Connector
    engine: Final = "mongo"
    def __init__(self, *, client_kwargs: dict[str, Any], database: str, settings: Settings,
                 action: str, client_id: str | None) -> None
    @classmethod
    def from_secret(cls, secret: Mapping[str, str], params: ConnectionParams, settings: Settings, *,
                    action: str, client_id: str | None) -> MongoConnector
    async def open(self) -> None
    async def close(self) -> None
    async def test(self, *, role, obj=None, write_mode=None) -> ConnectionTestResult
    async def list_objects(self, owner: str | None = None, *, limit: int = 500) -> list[ObjectRef]
    async def describe(self, obj: ObjectRef) -> TableShape
    async def count(self, shape, selection, params) -> int
    async def sample(self, shape, selection, params, n: int) -> list[Row]
    async def sample_keys(self, shape, selection, params, n: int, *, upper=None) -> list[tuple[Any, ...]]
    async def key_at_offset(self, shape, selection, params, offset: int) -> tuple[Any, ...] | None
    def read_partition(self, shape, selection, params, key_range, *, after_key, batch) -> AsyncIterator[list[Row]]
    async def writer(self, target: ResolvedTarget, source_shape: TableShape) -> MongoWriter    # Task 12
    async def lookup_values(self, shape, join: ColumnInfo, values: list[Any],
                            attributes: list[ColumnInfo]) -> list[Row]                    # Task 15
```

**Behaviours:**
- Connection: a secret `uri` (production: credentials + TLS live in the URI file) or `host`/`port` from
  `ConnectionParams` with `username`/`password` from the secret; allowed `options`: `authSource`,
  `replicaSet`, `directConnection`, `tls` (contract §5.1). The database is `params.database`, else the URI's
  path. Client options: `serverSelectionTimeoutMS = mongo_server_selection_timeout_ms`,
  `connectTimeoutMS = connector_connect_timeout_seconds·1000`, `timeoutMS =
  connector_statement_timeout_seconds·1000` (client-side operation timeout), `appname="dps"`,
  `tz_aware=False`, `uuidRepresentation="standard"`. Every operation carries
  `comment="dps:<action>:<job id>"` — the Mongo analogue of Oracle's module/action/client_identifier, visible
  in the profiler and `currentOp`. `repr`, errors and logs never contain the URI, user or password.
- `open` runs `hello` and `buildInfo`; `transactions` capability = replica set member or mongos.
- Reads use `readConcern: majority` on a replica set (contract §4.2). `describe` samples `min(500,
  discovery_sample_rows)` documents with `$sample` and infers the path shape (Task 9); a missing collection
  → `object_not_found`. `count` uses `count_documents(filter)` and honours `max_rows`.
- `read_partition` issues one `find` per batch: `{$and: [filter, {_id: {$gte: lower, $lt: upper, $gt:
  after}}]}`, `sort _id`, `limit = batch_size = batch` — no server cursor survives between batches.
  `selection.sample` keeps a document when `HMAC-SHA256(seed, BSON(_id))[:4] < percent·2³²` (deterministic,
  so a resumed partition reads the same documents); `sample_keys` returns `[]` when the shape has no range key
  (one partition).

- [ ] **Step 1: Test helpers** — `tests/connectors/mongo_fixtures.py`:
```python
"""Synthetic profile documents for Mongo tests (RFC 2606 e-mails, test IBANs, Verhoeff-shaped ids)."""
from __future__ import annotations

import datetime as dt

from bson import Decimal128, Int64, ObjectId


def profile(i: int, *, oid: bool = True) -> dict:
    return {
        "_id": ObjectId() if oid else i,
        "customerId": f"CUST-{10_000_000 + i}",
        "profile": {"fullName": f"Person {i:05d}", "dob": dt.datetime(1980, 1, 1) + dt.timedelta(days=i)},
        "contact": {"email": f"person.{i}@example.com", "mobile": f"+91 98000 {i:05d}",
                    "addresses": [{"type": "home", "line1": f"{i} Test Road", "pin": "560001"}]},
        "kyc": {"taxId": f"ABCDE{i % 10000:04d}F", "verifiedAt": dt.datetime(2024, 5, 1)},
        "accounts": [{"iban": f"GB00TEST{i:010d}", "balance": Decimal128(f"{i}.50")}],
        "riskScore": Int64(500 + i % 400), "segment": "premium" if i % 3 == 0 else "retail",
        "notes": f"Call person.{i}@example.com about the card", "tags": ["salary"] if i % 2 else [],
    }
```
`connector(db, **kw)` helper in the test module: `MongoConnector.from_secret({"uri": mongo_uri()},
ConnectionParams(database=db), Settings(_env_file=None), action="test", client_id="j_test")`.

- [ ] **Step 2: Write the failing integration tests** — `tests/connectors/test_mongo_connector.py`
  (`pytestmark = [pytest.mark.integration, pytest.mark.mongo]`; documents inserted with the sync
  `mongo_client` into `mongo_db`):

| Test | Arrange | Assert |
|---|---|---|
| `test_describe_profiles_shape` | 60 `profile(i)` docs | `key == ["_id"]`, `key_source == "primary_key"`, `$._id` kind `object_id`; paths include `$.contact.addresses[*].line1` (kind text, name `contact`), `$.accounts[*].balance` (`decimal`), `$.riskScore` (`long`), `$.profile.dob` (`timestamp`); `row_estimate == 60` |
| `test_describe_missing_collection` | — | `ConnectorError(code="object_not_found")` |
| `test_list_objects_excludes_internal` | collections `profiles`, `dps_write_ledger`, plus `system.profile` (profiler on) | `[o.name for o in await c.list_objects()] == ["profiles"]`; every `owner is None` |
| `test_count_with_filter_and_max_rows` | 30 docs (10 premium) | `count(Selection(mongo_filter={"segment": "premium"}))` → 10; with `max_rows=3` → 3 |
| `test_denied_filter_never_reaches_the_server` | profiler level 2 on the db | `count(Selection(mongo_filter={"$where": "1"}))` raises `SelectionError(code="selection_operator_denied")`; `system.profile` has no entry with `command.comment` of this connector |
| `test_read_partition_pages_all_docs_once` | 100 docs, batch 13 | concatenated `_id`s == sorted `_id`s; 8 batches; every doc complete (nested arrays intact) |
| `test_int_and_string_ids_page_in_order` | 25 docs `_id = i` (int); 25 docs in a second collection `_id = f"K{i:03d}"` | each pages in ascending order, exactly once |
| `test_partition_coverage_from_sample_keys` | 300 docs | ranges from `ranges_from_keys(sample_keys(n=60), 6, shape.key_columns())` cover all 300 exactly once (encoded ObjectId bounds decode to hex text; the connector turns them back into `ObjectId`s) |
| `test_mixed_id_types_single_partition` | ids `1`, `"a"`, `ObjectId()` | `describe().key_source == "none"`; `sample_keys(...) == []`; an unbounded `read_partition` still returns all 3 |
| `test_projection_reads_selected_paths` | 10 docs | `Selection(columns=["profile.fullName", "segment"])` → every doc has exactly the keys `{"_id", "profile", "segment"}` and `profile` has only `fullName` |
| `test_sample_spec_is_deterministic` | 200 docs | `Selection(sample=SampleSpec(percent=30, seed=11))` read twice → identical id lists, 30–90 docs |
| `test_reads_use_majority_and_carry_a_comment` | profiler level 2 | after a `read_partition`: the collection handle's `read_concern.level == "majority"`; `system.profile` has a `find` whose `command.comment == "dps:test:j_test"` |
| `test_bad_uri_is_connection_failed_and_value_free` | `uri="mongodb://u:SENTINEL-PW@127.0.0.1:1/x"`, selection timeout 500 ms | `open()` → `ConnectorError(code="connection_failed")`; "SENTINEL" not in `str`/`repr` of the error or the connector |
| `test_connection_test_reports_transactions` | — | `test(role="target", obj=ObjectRef(name="profiles"), write_mode="in_place")` → `capabilities["transactions"] is True` and `ok` |

- [ ] **Step 3: Run to confirm failure** — `docker compose --profile sources up -d mongo` (healthy), then
  `DPS_TEST_REQUIRE_SERVICES=1 pytest -q -m mongo tests/connectors/test_mongo_connector.py` → FAIL (`ModuleNotFoundError: dps.layers.connectors.mongo`).

- [ ] **Step 4: Write `dps/layers/connectors/mongo.py` (reader part)**
```python
"""MongoDB connector over pymongo's ``AsyncMongoClient`` (contract §1: Motor is deprecated).

Reads page on ``_id`` with a fresh ``find`` per batch (keyset; no server cursor held across batches) and
``readConcern: majority`` on a replica set. Documents travel whole: the engine transforms nested paths and
arrays through the L1 JSON machinery, and ``coerce_bson`` restores BSON types before writes. ``in_place``
and ``append`` run in multi-document transactions together with the write ledger; a standalone server is
refused with ``transactions_unsupported``. Server error messages can quote a document (``E11000 … dup key:
{ … }``), so only error *codes* ever leave this module.
"""
from __future__ import annotations

import hashlib
import hmac
import re
from collections.abc import AsyncIterator, Callable, Mapping
from typing import Any, Final, Literal

import bson
from pymongo import AsyncMongoClient
from pymongo.errors import BulkWriteError, ConnectionFailure, PyMongoError
from pymongo.read_concern import ReadConcern
from pymongo.uri_parser import parse_uri

from dps.config import Settings
from dps.jobs_models import ConnectionParams
from bson import ObjectId

from dps.layers.connectors.base import (
    ColumnInfo, ConnectionTestResult, ConnectorError, JsonScalar, KeyRange, ObjectRef, Row, TableShape,
    decode_key,
)
from dps.layers.connectors.docpaths import dotted
from dps.layers.connectors.mongo_docs import infer_shape
from dps.layers.connectors.selection import Selection, compile_mongo_filter, validate_selection
from dps.observability import CONNECTOR_CALLS, get_logger

logger = get_logger(__name__)
LEDGER_COLLECTION: Final = "dps_write_ledger"
_NAME: Final = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]{0,119}")
_DB: Final = re.compile(r"[A-Za-z0-9_-]{1,63}")
_HOST: Final = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?")
_AUTH_CODES: Final = frozenset({13, 18})          # Unauthorized, AuthenticationFailed
_INTERNAL: Final = frozenset({LEDGER_COLLECTION})


def mongo_code(exc: BaseException) -> str:
    """``mongo:<server code>`` or ``mongo:<ExceptionClass>`` — never ``str(exc)`` or ``details``."""
    if isinstance(exc, BulkWriteError):
        errors = (exc.details or {}).get("writeErrors") or []
        code = errors[0].get("code") if errors else None
        return f"mongo:{code}" if isinstance(code, int) else "mongo:bulk"
    code = getattr(exc, "code", None)
    return f"mongo:{code}" if isinstance(code, int) else f"mongo:{type(exc).__name__}"


def connector_error(code: str, exc: BaseException) -> ConnectorError:
    """Value-free mapping; raise it ``from None``."""
    if isinstance(exc, ConnectionFailure) or getattr(exc, "code", None) in _AUTH_CODES:
        return ConnectorError("connection_failed", mongo_code(exc), retryable=True)
    return ConnectorError(code, mongo_code(exc))


def _bson_id(value: Any, shape: TableShape) -> Any:
    """A decoded key back to its BSON type (M2's ``decode_key`` returns ObjectIds as hex text)."""
    col = next((c for c in shape.columns if c.path == "$._id"), None)
    if col is not None and col.kind == "object_id" and isinstance(value, str) and ObjectId.is_valid(value):
        return ObjectId(value)
    return value


def _collection_name(obj: ObjectRef) -> str:
    if obj.owner is not None or not _NAME.fullmatch(obj.name) or obj.name.startswith("system."):
        raise ConnectorError("object_invalid")
    return obj.name


class MongoConnector:
    """Contract §4.2 ``Connector`` for MongoDB (whole documents are rows)."""

    engine: Final = "mongo"

    def __init__(self, *, client_kwargs: dict[str, Any], database: str, settings: Settings, action: str,
                 client_id: str | None) -> None:
        self._kwargs, self._db_name, self._settings = client_kwargs, database, settings
        self._action, self._client_id = action, client_id or ""
        self._client: AsyncMongoClient[dict[str, Any]] | None = None
        self._replica = False
        self._version: str | None = None

    def __repr__(self) -> str:
        return f"MongoConnector(database={self._db_name!r})"

    @property
    def comment(self) -> str:
        return f"dps:{self._action}:{self._client_id}"

    @classmethod
    def from_secret(cls, secret: Mapping[str, str], params: ConnectionParams, settings: Settings, *,
                    action: str, client_id: str | None) -> MongoConnector:
        kwargs: dict[str, Any] = {
            "serverSelectionTimeoutMS": settings.mongo_server_selection_timeout_ms,
            "connectTimeoutMS": settings.connector_connect_timeout_seconds * 1000,
            "timeoutMS": settings.connector_statement_timeout_seconds * 1000,
            "appname": "dps", "tz_aware": False, "uuidRepresentation": "standard",
        }
        uri = secret.get("uri")
        database = params.database
        if uri:
            kwargs["host"] = uri
            try:
                database = database or parse_uri(uri).get("database")
            except (PyMongoError, ValueError):
                raise ConnectorError("connection_invalid") from None
        else:
            if not params.host or not _HOST.fullmatch(params.host):
                raise ConnectorError("connection_invalid")
            kwargs.update(host=params.host, port=params.port or 27017)
        if secret.get("username"):
            kwargs.update(username=secret["username"], password=secret.get("password", ""))
        opts = params.options or {}
        if "authSource" in opts:
            if not _DB.fullmatch(opts["authSource"]):
                raise ConnectorError("connection_invalid")
            kwargs["authSource"] = opts["authSource"]
        if "replicaSet" in opts:
            if not _DB.fullmatch(opts["replicaSet"]):
                raise ConnectorError("connection_invalid")
            kwargs["replicaSet"] = opts["replicaSet"]
        for flag in ("directConnection", "tls"):
            if flag in opts:
                kwargs[flag] = opts[flag].lower() == "true"
        if not database or not _DB.fullmatch(database):
            raise ConnectorError("database_required")
        return cls(client_kwargs=kwargs, database=database, settings=settings, action=action,
                   client_id=client_id)

    # -- plumbing ---------------------------------------------------------------------------------
    async def _guard(self, op: str, coro: Any, code: str = "source_error") -> Any:
        try:
            result = await coro
        except PyMongoError as exc:
            CONNECTOR_CALLS.labels("mongo", op, "error").inc()
            logger.warning("mongo.call_failed", op=op, driver_code=mongo_code(exc))
            raise connector_error(code, exc) from None
        CONNECTOR_CALLS.labels("mongo", op, "ok").inc()
        return result

    @property
    def _db(self) -> Any:
        if self._client is None:
            raise ConnectorError("connection_failed")
        return self._client[self._db_name]

    def _coll(self, name: str) -> Any:
        rc = ReadConcern("majority") if self._replica else ReadConcern()
        return self._db.get_collection(name, read_concern=rc)

    async def open(self) -> None:
        if self._client is not None:
            return
        try:
            self._client = AsyncMongoClient(**self._kwargs)
            hello = await self._client.admin.command("hello")
            info = await self._client.admin.command("buildInfo")
        except PyMongoError as exc:
            client, self._client = self._client, None
            if client is not None:
                await client.close()
            raise connector_error("connection_failed", exc) from None
        self._replica = "setName" in hello or hello.get("msg") == "isdbgrid"
        self._version = str(info.get("version", ""))

    async def close(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            await client.close()

    # -- metadata ---------------------------------------------------------------------------------
    async def list_objects(self, owner: str | None = None, *, limit: int = 500) -> list[ObjectRef]:
        await self.open()
        names = await self._guard("list", self._db.list_collection_names(filter={"type": "collection"}))
        keep = sorted(n for n in names if not n.startswith("system.") and n not in _INTERNAL)
        return [ObjectRef(name=n) for n in keep[:limit]]

    async def _sample_docs(self, name: str, flt: dict[str, Any], n: int,
                           project: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        pipeline: list[dict[str, Any]] = ([{"$match": flt}] if flt else []) + [{"$sample": {"size": n}}]
        if project:
            pipeline.append({"$project": project})

        async def run() -> list[dict[str, Any]]:
            cursor = await self._coll(name).aggregate(pipeline, comment=self.comment)
            return await cursor.to_list(length=n)

        return await self._guard("sample", run())

    async def describe(self, obj: ObjectRef) -> TableShape:
        await self.open()
        name = _collection_name(obj)
        found = await self._guard("describe", self._db.list_collection_names(filter={"name": name}))
        if not found:
            raise ConnectorError("object_not_found")
        estimate = await self._guard("describe", self._coll(name).estimated_document_count())
        docs = await self._sample_docs(name, {}, min(500, self._settings.discovery_sample_rows))
        return infer_shape(ObjectRef(name=name), docs, row_estimate=int(estimate))

    # -- selection --------------------------------------------------------------------------------
    @staticmethod
    def _filter(selection: Selection) -> dict[str, Any]:
        validate_selection(selection, "mongo", None)
        return compile_mongo_filter(selection.mongo_filter)

    @staticmethod
    def _projection(selection: Selection) -> dict[str, int] | None:
        if not selection.columns:
            return None
        return {**{c: 1 for c in selection.columns}, "_id": 1}

    @staticmethod
    def _sampler(selection: Selection) -> Callable[[Any], bool] | None:
        if selection.sample is None:
            return None
        threshold = int(selection.sample.percent / 100 * 2**32)
        key = str(selection.sample.seed or 0).encode()

        def keep(_id: Any) -> bool:
            digest = hmac.new(key, bson.encode({"_id": _id}), hashlib.sha256).digest()
            return int.from_bytes(digest[:4], "big") < threshold

        return keep

    # -- reads ------------------------------------------------------------------------------------
    async def count(self, shape: TableShape, selection: Selection, params: dict[str, JsonScalar]) -> int:
        flt = self._filter(selection)
        await self.open()
        n = await self._guard("count", self._coll(shape.object.name).count_documents(flt, comment=self.comment))
        return min(int(n), selection.max_rows) if selection.max_rows else int(n)

    async def sample(self, shape: TableShape, selection: Selection, params: dict[str, JsonScalar],
                     n: int) -> list[Row]:
        flt = self._filter(selection)
        await self.open()
        return await self._sample_docs(shape.object.name, flt, n, self._projection(selection))

    async def sample_keys(self, shape: TableShape, selection: Selection, params: dict[str, JsonScalar],
                          n: int, *, upper: tuple[Any, ...] | None = None) -> list[tuple[Any, ...]]:
        if not shape.key:
            return []
        flt = self._filter(selection)
        await self.open()
        docs = await self._sample_docs(shape.object.name, flt, n, {"_id": 1})
        bound = _bson_id(upper[0], shape) if upper else None
        return [(d["_id"],) for d in docs if bound is None or d["_id"] < bound]

    async def key_at_offset(self, shape: TableShape, selection: Selection, params: dict[str, JsonScalar],
                            offset: int) -> tuple[Any, ...] | None:
        flt = self._filter(selection)
        await self.open()
        cursor = self._coll(shape.object.name).find(flt, projection={"_id": 1}, sort=[("_id", 1)],
                                                    skip=int(offset), limit=1, comment=self.comment)
        docs = await self._guard("key_at_offset", cursor.to_list(length=1))
        return (docs[0]["_id"],) if docs else None

    async def read_partition(self, shape: TableShape, selection: Selection,
                             params: dict[str, JsonScalar], key_range: KeyRange, *,
                             after_key: tuple[Any, ...] | None, batch: int) -> AsyncIterator[list[Row]]:
        """Keyset pages on ``_id`` within ``[lower, upper)``; one ``find`` per batch."""
        flt = self._filter(selection)
        await self.open()
        coll, proj, keep = self._coll(shape.object.name), self._projection(selection), self._sampler(selection)
        keys = shape.key_columns()
        lower = _bson_id(decode_key(key_range.lower, keys)[0], shape) if key_range.lower else None
        upper = _bson_id(decode_key(key_range.upper, keys)[0], shape) if key_range.upper else None
        after = _bson_id(after_key[0], shape) if after_key else None
        while True:
            rng: dict[str, Any] = {}
            if lower is not None:
                rng["$gte"] = lower
            if upper is not None:
                rng["$lt"] = upper
            if after is not None:
                rng["$gt"] = after
            query = {"$and": [flt, {"_id": rng}]} if flt and rng else ({"_id": rng} if rng else flt)
            cursor = coll.find(query, projection=proj, sort=[("_id", 1)], limit=batch, batch_size=batch,
                               comment=self.comment)
            docs: list[dict[str, Any]] = await self._guard("read", cursor.to_list(length=batch))
            if not docs:
                return
            after = docs[-1]["_id"]
            out = [d for d in docs if keep(d["_id"])] if keep else docs
            if out:
                yield out
            if len(docs) < batch:
                return

    # -- connection test --------------------------------------------------------------------------
    async def _privileges(self) -> list[dict[str, Any]] | None:
        status = await self._guard("test", self._client.admin.command(  # type: ignore[union-attr]
            {"connectionStatus": 1, "showPrivileges": True}))
        info = status.get("authInfo", {})
        if not info.get("authenticatedUsers"):
            return None                                  # no auth on this server: nothing to check
        return list(info.get("authenticatedUserPrivileges", []))

    def _allowed(self, privs: list[dict[str, Any]] | None, coll: str, action: str) -> bool:
        if privs is None:
            return True
        for p in privs:
            res = p.get("resource", {})
            hit = res.get("anyResource") or (res.get("db") in ("", self._db_name)
                                             and res.get("collection") in ("", coll))
            if hit and action in p.get("actions", []):
                return True
        return False

    async def test(self, *, role: Literal["source", "target"], obj: ObjectRef | None = None,
                   write_mode: str | None = None) -> ConnectionTestResult:
        caps = {"transactions": False, "merge": True, "identity": False, "sample": True, "rowid": False,
                "copy": False}
        try:
            await self.open()
        except ConnectorError as err:
            return ConnectionTestResult(ok=False, engine="mongo", checks=[
                {"name": "connect", "ok": False, "detail": err.detail}], capabilities=caps,
                error_code=err.code)
        caps["transactions"] = self._replica
        checks: list[dict[str, Any]] = [{"name": "connect", "ok": True}]
        if obj is not None:
            name = _collection_name(obj)
            privs = await self._privileges()
            if role == "source":
                checks.append({"name": "select_source", "ok": self._allowed(privs, name, "find")})
            else:
                need = {"table": ("insert", "update", "remove"), "in_place": ("update",),
                        "append": ("insert",)}.get(write_mode or "", ())
                for action in need:
                    checks.append({"name": f"{action}_target", "ok": self._allowed(privs, name, action)})
                if write_mode in ("in_place", "append"):
                    checks.append({"name": "transactions", "ok": self._replica})
                    checks.append({"name": "create_ledger",
                                   "ok": self._allowed(privs, LEDGER_COLLECTION, "insert")})
        return ConnectionTestResult(ok=all(c["ok"] for c in checks), engine="mongo",
                                    server_version=self._version, checks=checks, capabilities=caps)
```

- [ ] **Step 5: Run the integration tests and gates**

Run: `DPS_TEST_REQUIRE_SERVICES=1 pytest -q -m mongo tests/connectors/test_mongo_connector.py && ruff check dps tests tools && ruff format --check dps tests tools && mypy dps && pytest -q`
Expected: 14 passed; suite green.

- [ ] **Step 6: Commit**

```bash
git add dps/layers/connectors/mongo.py tests/connectors/test_mongo_connector.py tests/connectors/mongo_fixtures.py && git commit -m "feat(mongo): AsyncMongoClient connector — \$sample shapes, majority reads, keyset pages on _id" -- dps/layers/connectors/mongo.py tests/connectors/test_mongo_connector.py tests/connectors/mongo_fixtures.py
```

---

### Task 12: `MongoWriter` — `bulk_write(ordered=False)`, transactional in-place and append with the ledger

**Files:**
- Modify: `dps/layers/connectors/mongo.py` (writer part), (M2) `dps/layers/connectors/base.py` (`PrepareReport.append_upper_key: EncodedKey | None = None`; contract §4.2 addition)
- Test: `tests/connectors/test_mongo_writer.py` (markers `integration`, `mongo`)

**Interfaces introduced:**

```python
class MongoWriter:                                         # implements base.BatchWriter
    def __init__(self, connector: MongoConnector, target: ResolvedTarget, source_shape: TableShape) -> None
    async def prepare(self, *, dry_run: bool) -> PrepareReport
    async def precheck_collisions(self, keys: AsyncIterator[list[tuple[Any, ...]]]) -> int
    async def read_ledger(self, job_id: str, partition_no: int) -> LedgerMark | None
    async def write_batch(self, rows: list[Row], *, mark: LedgerMark) -> int
    async def finish_partition(self, job_id: str, partition_no: int) -> dict[str, Any]   # {}
    async def clear_ledger(self, job_id: str) -> int
    async def close(self) -> None
```

**Behaviours:**
- `prepare`: `in_place`/`append` on a server without transactions → `transactions_unsupported` (contract
  §4.2 — the ledger and the batch must commit together); `in_place` needs a range key (`no_key`);
  `append` with `offset` needs an integer `_id` (`offset`) or a string `_id` (`prefix`), and an ObjectId
  `_id` accepts only `generate` → else `pk_strategy_invalid`. `table`: a missing collection is created when
  `create_if_missing` (secondary indexes are not copied; the report's `checks` carry
  `{"name": "indexes_not_copied", "ok": true, "severity": "info"}`), else `target_missing`; `truncate_insert` → `delete_many({})` once. `in_place`/`append` require the target
  collection (`target_missing`) and create `dps_write_ledger` in the target database. A same-collection
  `append` reports its read bound so M2's run handler never reads back the documents the job appends: an
  integer `_id` → `append_upper = max(_id) + 1`; an ObjectId `_id` with `generate` → `prepare` waits until the
  wall clock is past the second of the largest existing `_id` and reports `append_upper_key` = the ObjectId
  of that new second with a zero tail — every existing `_id` sorts below it and every ObjectId the driver
  generates afterwards sorts above it. `dry_run` changes nothing.
- `write_batch`: every document goes through `coerce_bson(·, source_shape)` and `make_bson_safe` first.
  `table` → `bulk_write([ReplaceOne({_id}, doc, upsert=True)…], ordered=False)` (idempotent by `_id`, no
  ledger). `in_place` and `append` run in `session.with_transaction`: (1) the ledger guard
  `update_one({_id: "<job>:<partition>", batch_no: {$lt: n}}, {$set: …}, upsert=True)` — a duplicate-key
  error means the batch was already applied → `ledger_conflict`; (2) `in_place`: `UpdateOne({_id}, {$set:
  leaf_updates(doc)})` per document (leaf paths, so a projected selection never drops fields; a field name
  that cannot be dot-addressed → `path_unsupported`); `append`: `InsertOne` of the document without `_id`
  (`generate`: the driver assigns a new ObjectId) or with the `_id` M2 already rewrote (offset/token);
  `ordered=False`; (3) commit. Returns `matched_count` (in place), `inserted_count` (append) or
  `upserted + matched` (table).
- Server errors never leak document content: `BulkWriteError.details` and `DuplicateKeyError` messages are
  never read; only `mongo:<code>`.

- [ ] **Step 1: Write the failing tests** — `tests/connectors/test_mongo_writer.py` (target built with the
  same `make_target` helper as Oracle, imported from `oracle_fixtures.py`, which is engine-neutral):

| Test | Arrange | Assert |
|---|---|---|
| `test_table_mode_upserts_idempotently_and_keeps_bson_types` | 40 profiles; read via `read_partition`; set `profile.fullName = "T-" + str(i)`, `riskScore = int(...)` (plain int) and `accounts[0].balance = decimal.Decimal("1.25")` as the engine would | target collection created; two `write_batch` calls → 40 docs; `riskScore` is `Int64`, `balance` is `Decimal128("1.25")`, `profile.dob` a `datetime`, `contact.addresses` intact |
| `test_truncate_insert_once` | target with 5 junk docs | `prepare` → `truncated`; 40 docs after one write |
| `test_in_place_sets_leaves_only` | read with `Selection(columns=["profile.fullName"])`, change the name | after `write_batch`: `profile.fullName` changed, `profile.dob`, `contact`, `accounts` unchanged for all docs |
| `test_in_place_ledger_and_batch_commit_together` | monkeypatch the target collection's `bulk_write` to raise `OperationFailure` after the ledger update | `ConnectorError(code="target_error")`; no ledger doc and no change in the collection |
| `test_ledger_fences_a_replayed_batch` | write mark batch 1, then the same mark with other values | `ledger_conflict`; collection holds the first values |
| `test_append_generate_assigns_new_ids` | 10 docs | after append: 20 docs; the 10 new ones have new ObjectIds and the same masked content |
| `test_append_offset_on_int_ids_and_collision_precheck` | `_id` ints 1..10; rows with `_id + 1000`; a colliding set `_id + 5` | precheck → 0 and 5; after write 20 docs |
| `test_pk_strategy_rules` | ObjectId ids | `PkOffset(offset=10)` → `pk_strategy_invalid`; `PkGenerate()` ok |
| `test_same_collection_append_bounds` | int ids 1..10; ObjectId ids | int: `report.append_upper == 11`; ObjectId: `decode_key(report.append_upper_key, [_id col])[0]` is greater than every existing `_id` and smaller than an `ObjectId()` created after `prepare` returns |
| `test_transactions_unsupported_without_a_replica_set` | set `connector._replica = False` | `in_place` `prepare` → `transactions_unsupported`; `table` `prepare` ok |
| `test_duplicate_key_error_is_value_free` | target with a unique index on `customerId`; two rows sharing `customerId = "SENTINEL-DUP-1"` | `ConnectorError` with `code == "target_error"`, `detail == "mongo:11000"`; "SENTINEL" absent from `str`, `repr`, `args`; `__suppress_context__` |
| `test_path_unsupported_for_dotted_field_names` | a doc with field `"a.b"` | `in_place` write → `path_unsupported` |
| `test_read_ledger_and_clear` | marks for partitions 0, 1 | `read_ledger` equals; `clear_ledger` → 2; then `None` |

- [ ] **Step 2: Run to confirm failure** — `DPS_TEST_REQUIRE_SERVICES=1 pytest -q -m mongo tests/connectors/test_mongo_writer.py` → FAIL (`… has no attribute 'MongoWriter'`).

- [ ] **Step 3: Append the writer to `dps/layers/connectors/mongo.py`**
```python
import asyncio
import datetime as dt
import time

from bson import ObjectId
from pymongo import InsertOne, ReplaceOne, UpdateOne
from pymongo.errors import CollectionInvalid, DuplicateKeyError
from pymongo.write_concern import WriteConcern

from dps.layers.connectors.base import LedgerMark, PrepareReport, encode_key
from dps.layers.connectors.docpaths import leaf_updates
from dps.layers.connectors.mongo_docs import coerce_bson, make_bson_safe
from dps.layers.connectors.targets import ResolvedTarget


class MongoWriter:
    """Contract §4.2 ``BatchWriter`` for MongoDB targets in the connection's database."""

    def __init__(self, connector: MongoConnector, target: ResolvedTarget, source_shape: TableShape) -> None:
        self._c, self._t, self._src = connector, target, source_shape
        self._name = _collection_name(target.object)
        self._uses_ledger = target.write_mode in ("in_place", "append")

    def _target(self) -> Any:
        wc = WriteConcern("majority") if self._c._replica else WriteConcern()
        return self._c._db.get_collection(self._name, write_concern=wc)

    def _ledger(self) -> Any:
        return self._c._db.get_collection(LEDGER_COLLECTION)

    def _id_kind(self) -> str:
        col = next((c for c in self._src.columns if c.path == "$._id"), None)
        return col.kind if col is not None else "other"

    async def prepare(self, *, dry_run: bool) -> PrepareReport:
        await self._c.open()
        mode, pk = self._t.write_mode, self._t.pk_strategy
        if self._uses_ledger and not self._c._replica:
            raise ConnectorError("transactions_unsupported")
        if mode == "in_place" and self._src.key_source != "primary_key":
            raise ConnectorError("no_key")
        if mode == "append" and pk is not None:
            kind = self._id_kind()
            if pk.kind == "offset":
                ok = (kind == "int" and getattr(pk, "offset", None)) or (kind == "text" and getattr(pk, "prefix", None))
                if not ok:
                    raise ConnectorError("pk_strategy_invalid")
            elif pk.kind == "token" and kind == "object_id":
                raise ConnectorError("pk_strategy_invalid")
        names = set(await self._c._guard("prepare", self._c._db.list_collection_names(), "target_error"))
        created = truncated = False
        checks: list[dict[str, Any]] = []
        append_upper: int | None = None
        append_upper_key: list[dict[str, str]] | None = None
        if mode == "table":
            if self._name not in names:
                if not self._t.create_if_missing:
                    raise ConnectorError("target_missing")
                if not dry_run:
                    try:
                        await self._c._db.create_collection(self._name)
                    except CollectionInvalid:
                        pass                                   # created concurrently: fine
                    created = True
                    checks.append({"name": "indexes_not_copied", "ok": True, "severity": "info"})
            elif self._t.table_load == "truncate_insert" and not dry_run:
                await self._c._guard("prepare", self._target().delete_many({}, comment=self._c.comment),
                                     "target_error")
                truncated = True
        elif self._name not in names:
            raise ConnectorError("target_missing")
        if self._uses_ledger and not dry_run and LEDGER_COLLECTION not in names:
            try:
                await self._c._db.create_collection(LEDGER_COLLECTION)
            except CollectionInvalid:
                pass
        if mode == "append" and self._t.same_as_source:
            top = await self._c._guard("prepare", self._target().find_one(
                {}, projection={"_id": 1}, sort=[("_id", -1)]), "target_error")
            id_col = next(c for c in self._src.columns if c.path == "$._id")
            if top is not None and isinstance(top["_id"], int):
                append_upper = int(top["_id"]) + 1
            elif self._t.generated_key and (top is None or isinstance(top["_id"], ObjectId)):
                floor = int(top["_id"].generation_time.timestamp()) if top is not None else 0
                while int(time.time()) <= floor:              # at most ~1 s
                    await asyncio.sleep(0.05)
                bound = ObjectId(int(time.time()).to_bytes(4, "big") + bytes(8))
                append_upper_key = encode_key((bound,), [id_col])
        return PrepareReport(created=created, truncated=truncated, ledger=self._uses_ledger and not dry_run,
                             checks=checks, append_upper=append_upper, append_upper_key=append_upper_key)

    async def precheck_collisions(self, keys: AsyncIterator[list[tuple[Any, ...]]]) -> int:
        total = 0
        async for batch in keys:
            ids = [k[0] for k in batch]
            for start in range(0, len(ids), 1000):
                total += int(await self._c._guard("precheck", self._target().count_documents(
                    {"_id": {"$in": ids[start:start + 1000]}}, comment=self._c.comment), "target_error"))
        return total

    async def read_ledger(self, job_id: str, partition_no: int) -> LedgerMark | None:
        doc = await self._c._guard("ledger", self._ledger().find_one({"_id": f"{job_id}:{partition_no}"}),
                                   "target_error")
        if doc is None:
            return None
        return LedgerMark(job_id=job_id, partition_no=partition_no, batch_no=int(doc["batch_no"]),
                          last_key=doc["last_key"])

    async def clear_ledger(self, job_id: str) -> int:
        res = await self._c._guard("ledger", self._ledger().delete_many({"job_id": job_id}), "target_error")
        return int(res.deleted_count)

    async def _mark(self, session: Any, mark: LedgerMark) -> None:
        try:
            await self._ledger().update_one(
                {"_id": f"{mark.job_id}:{mark.partition_no}", "batch_no": {"$lt": mark.batch_no}},
                {"$set": {"job_id": mark.job_id, "partition_no": mark.partition_no,
                          "batch_no": mark.batch_no, "last_key": mark.last_key,
                          "updated_at": dt.datetime.now(dt.UTC)}},
                upsert=True, session=session)
        except DuplicateKeyError:
            raise ConnectorError("ledger_conflict") from None

    def _append_doc(self, doc: dict[str, Any]) -> dict[str, Any]:
        if self._t.generated_key:
            return {k: v for k, v in doc.items() if k != "_id"}
        return doc

    async def write_batch(self, rows: list[Row], *, mark: LedgerMark) -> int:
        if not rows:
            return 0
        docs = [make_bson_safe(coerce_bson(dict(r), self._src)) for r in rows]
        mode, coll = self._t.write_mode, self._target()
        if mode == "table":
            ops = [ReplaceOne({"_id": d["_id"]}, d, upsert=True) for d in docs]
            res = await self._c._guard("write", coll.bulk_write(ops, ordered=False, comment=self._c.comment),
                                       "target_error")
            return int(res.upserted_count + res.matched_count)

        async def txn(session: Any) -> int:
            await self._mark(session, mark)
            if mode == "in_place":
                ops = []
                writable = set(self._t.write_columns)
                for d in docs:
                    updates = leaf_updates(d)
                    if updates is None:
                        raise ConnectorError("path_unsupported")
                    if writable:
                        updates = {k: v for k, v in updates.items() if k.split(".")[0] in writable}
                    if updates:
                        ops.append(UpdateOne({"_id": d["_id"]}, {"$set": updates}))
                if not ops:
                    return len(docs)
                res = await coll.bulk_write(ops, ordered=False, session=session, comment=self._c.comment)
                return int(res.matched_count)
            res = await coll.bulk_write([InsertOne(self._append_doc(d)) for d in docs], ordered=False,
                                        session=session, comment=self._c.comment)
            return int(res.inserted_count)

        async def run() -> int:
            async with self._c._client.start_session() as session:  # type: ignore[union-attr]
                return int(await session.with_transaction(txn))

        return int(await self._c._guard("write", run(), "target_error"))

    async def finish_partition(self, job_id: str, partition_no: int) -> dict[str, Any]:
        return {}

    async def close(self) -> None:
        return None
```
and on `MongoConnector`:
```python
    async def writer(self, target: ResolvedTarget, source_shape: TableShape) -> MongoWriter:
        await self.open()
        return MongoWriter(self, target, source_shape)
```
The Task 8 registry branch now resolves `MongoConnector` too (no registry change). Add to
`tests/connectors/test_registry_m3.py` a twin test for `engine="mongo"`: secret ref `local/mdb` with a single
`uri` file `mongodb://mongo:27017/crm?directConnection=true`, `MongoConnector.open` monkeypatched to a no-op,
asserting `isinstance(c, MongoConnector)`, `c.comment == "dps:partitions:j_01"` and
`repr(c) == "MongoConnector(database='crm')"`.

- [ ] **Step 4: Run tests and gates**

Run: `DPS_TEST_REQUIRE_SERVICES=1 pytest -q -m mongo tests/connectors/ && pytest -q tests/connectors/test_registry_m3.py && ruff check dps tests tools && ruff format --check dps tests tools && mypy dps && pytest -q`
Expected: 12 writer + 14 reader tests pass; registry tests pass; suite green.

- [ ] **Step 5: Commit**

```bash
git add dps/layers/connectors/mongo.py dps/layers/connectors/base.py tests/connectors/test_mongo_writer.py tests/connectors/test_registry_m3.py docs/superpowers/plans/2026-10-11-m1-m5-contract.md && git commit -m "feat(mongo): batch writer — unordered bulk upserts, transactional in-place and append with the ledger" -- dps/layers/connectors/mongo.py dps/layers/connectors/base.py tests/connectors/test_mongo_writer.py tests/connectors/test_registry_m3.py docs/superpowers/plans/2026-10-11-m1-m5-contract.md
```

---
### Task 13: Engine bridge on real rows — untouched values stay identical, nested arrays transform, tokens agree across engines

**Files:**
- Test: `tests/connectors/test_engine_bridge_m3.py` (markers `integration`, `oracle`, `mongo`)
- Modify (M2, only if a test below is red): `dps/engine/batch.py`

**Behaviours pinned (contract §6.4/§6.5):**
1. A value not covered by any plan entry leaves `BatchTransformer.transform` *as the driver value* — same type,
   same value (Oracle `Decimal`, `float` from BINARY_DOUBLE, `datetime` with seconds, `bytes`, FF9 text, padded
   CHAR; Mongo `Int64`, `Decimal128`, `datetime`, `ObjectId`, nested arrays).
2. `[*]` column paths are expanded against each document; every element is transformed.
3. With `unclassified="detect"`, string leaves of a Mongo document that the snapshot does not list are run
   through the detector and their spans transformed.
4. Under one `KeyMaterial` with `field_scoped=False`, the same entity type, method and canonical digits give the
   same token in Oracle (`NUMBER` 10000123), Mongo (`"CUST-10000123"`) and Postgres (`bigint`): the digits of
   the outputs are equal as integers (FF1 may emit a leading zero that a NUMBER column drops; the join compares
   integers, Task 15). `PERSON_NAME`/`EMAIL` tokens are byte-identical for identical names/e-mails.

- [ ] **Step 1: Write the tests** — `tests/connectors/test_engine_bridge_m3.py`:
```python
"""The L2 engine bridge (M2 BatchTransformer) on real Oracle and Mongo rows."""
from __future__ import annotations

import copy
import datetime as dt
import re
from decimal import Decimal

import pytest

pytest.importorskip("fastfpe")
oracledb = pytest.importorskip("oracledb")
from bson import Decimal128, Int64, ObjectId  # noqa: E402

from dps.config import Settings  # noqa: E402
from dps.engine.batch import BatchTransformer, ColumnPlan  # noqa: E402
from dps.engine.policy import MethodSpec, load_preset  # noqa: E402
from dps.engine.service import PrivacyEngine  # noqa: E402
from dps.engine.transform.crypto import generate_key_material  # noqa: E402
from dps.layers.connectors import mongo_docs  # noqa: E402
from dps.layers.connectors.base import ColumnInfo, ObjectRef, TableShape  # noqa: E402

pytestmark = [pytest.mark.integration, pytest.mark.oracle, pytest.mark.mongo]
POLICY = load_preset("gdpr-pseudonymise")
ENGINE = PrivacyEngine(Settings(_env_file=None, fpe_enabled=True, llm_provider="stub", ner_enabled=False))


def _plan(path: str, entity: str, method: str, kind: str, **params: object) -> ColumnPlan:
    name = path[2:].split(".")[0].split("[")[0]                       # physical column / top-level field
    return ColumnPlan(path=path, entity_type=entity, method=MethodSpec(method=method, params=dict(params)),
                      text_column=False, reversible=True, kind=kind, length=None, name=name)


def _transformer(shape: TableShape, plans: list[ColumnPlan], key, unclassified: str = "keep") -> BatchTransformer:  # noqa: ANN001
    return BatchTransformer(ENGINE, policy=POLICY, mode="obfuscate", columns=plans, shape=shape, key=key,
                            seed=None, locale="en_IN", unclassified=unclassified)


def _digits(v: object) -> int:
    return int(re.sub(r"\D", "", str(v)))
```
Test cases (each builds its rows through the real connectors from Tasks 5 and 11 against fixtures created in
`oracle_schema` / `mongo_db`):

| Test | Assert |
|---|---|
| `test_untouched_oracle_values_are_identical` | table with `NUMBER(14,2)`, `BINARY_DOUBLE`, `DATE` (with 13:45:07), `TIMESTAMP(6)`, `TIMESTAMP(9)`, `RAW(16)`, `CHAR(10)`, `CLOB`; `_transformer(shape, [], key)` → `result.rows == rows` and `type(out[c]) is type(row[c])` for every column |
| `test_untouched_mongo_values_are_identical` | 20 `profile(i)` docs, only `$.profile.fullName` planned → every other leaf equal and same type (`Int64`, `Decimal128`, `datetime`, `ObjectId`); `fullName` changed |
| `test_mongo_wildcard_paths_transform_every_element` | docs with 3 accounts each; plan `$.accounts[*].iban` (`IBAN`, `det_token`) → every `iban` differs from its original, `balance` untouched, `verification.hits == 0` |
| `test_mongo_unclassified_leaves_are_detected` | doc with extra `{"extra": {"note": "mail sentinel.x@example.com now"}}` not in the plan, `unclassified="detect"` → `"sentinel.x@example.com" not in str(out)`; with `"keep"` it stays |
| `test_tokens_agree_across_engines` | one `generate_key_material("key")`; Oracle row `{CUSTOMER_ID: 10000123, FULL_NAME: "Ananya Krishnan", EMAIL: "ananya.k@example.com"}` (plans `fpe_digits`, `fpe_text`, `email` with `domain: keep`); Mongo doc `{customerId: "CUST-10000123", profile: {fullName: same}, contact: {email: same}}` with the same plans on its paths → `_digits(oracle CUSTOMER_ID) == _digits(mongo customerId)`, names equal, e-mails equal; a second key material gives different tokens |
| `test_ts9_and_char_columns_transform_safely` | `date_shift` on a `TIMESTAMP(9)` value keeps its nine digits (Task 7); `fpe_text` on a `CHAR(10)` PAN keeps length 10 after `from_engine_text` and writing back through `OracleWriter` (re-read value has length 10) |

- [ ] **Step 2: Run** — `docker compose --profile oracle --profile sources up -d oracle mongo` and
  `DPS_TEST_REQUIRE_SERVICES=1 pytest -q -m "oracle and mongo" tests/connectors/test_engine_bridge_m3.py`.
  Expected: all pass. If behaviour 1 is red (M2 round-trips every value through text), change
  `BatchTransformer.transform` so only plan-covered paths are converted with `to_engine_text`/`from_engine_text`
  and every other value is copied by reference from the input row; if behaviour 2 is red, expand `[*]` paths
  with `docpaths.expand_wildcards(row, plan.path)` before building the per-row `Plan`; if behaviour 3 is red,
  run the detector over `flatten(row)` leaves whose generalised path is not in the plan set when
  `unclassified == "detect"`. Each fix lands with M2's own batch tests still green and is logged in
  `M3-DEVIATIONS.md`.

- [ ] **Step 3: Gates and commit**

Run: `ruff check dps tests tools && ruff format --check dps tests tools && mypy dps && pytest -q`
```bash
git add tests/connectors/test_engine_bridge_m3.py dps/engine/batch.py && git commit -m "test(connectors): engine bridge on Oracle and Mongo rows; cross-engine token agreement" -- tests/connectors/test_engine_bridge_m3.py dps/engine/batch.py
```

---

### Task 14: Run M2's connector conformance suite against Oracle and MongoDB

**Files:**
- Create: `tests/connectors/harness_oracle.py`, `tests/connectors/harness_mongo.py`
- Modify (M2): the engine registration list used by `tests/connectors/conformance.py` (one line per engine; the suite's tests are not edited — contract §15)

**Behaviours:** the shared suite (contract §12.3: describe/key detection, keyset paging with composite and
string keys, partition coverage, selection rejection, every write mode, ledger-based resume after a crash
between write and checkpoint, idempotent `table` reload, no values in logs) runs unchanged for
`postgres`, `oracle` and `mongo`, each against its real container, marked `pg_source`, `oracle`, `mongo`.

Each harness implements the harness interface the suite defines (recorded in the Task 1 audit) from these
engine-specific building blocks, which this task writes in full:

```python
# tests/connectors/harness_oracle.py
_ORACLE_TYPE = {"int": "NUMBER(12)", "decimal": "NUMBER(14,2)", "text": "VARCHAR2(200 CHAR)",
                "char": "CHAR(12)", "timestamp": "TIMESTAMP(6)", "lob_text": "CLOB"}


class OracleHarness:
    engine = "oracle"
    marker = "oracle"

    def __init__(self, schema: OracleTestSchema, admin: "oracledb.Connection") -> None
    def create(self, name: str, columns: list[tuple[str, str]], key: list[str],
               rows: list[dict[str, Any]], *, identity: str | None = None) -> ObjectRef
        # CREATE TABLE "<owner>"."<NAME>" (<col> <_ORACLE_TYPE[kind]> ..., PRIMARY KEY (...)) — identity key as
        # NUMBER GENERATED BY DEFAULT AS IDENTITY; key=[] creates a key-less (ROWID) table; rows via executemany;
        # SELECT granted to the read-only user
    def fetch(self, obj: ObjectRef) -> list[dict[str, Any]]
        # every row, ordered by the key (or ROWID), values normalised: CHAR rstripped, Decimal -> str
    def ledger_rows(self, job_id: str) -> int
        # COUNT(*) from "<owner>"."DPS_WRITE_LEDGER" WHERE JOB_ID = :j (0 when the table is absent)
    def drop(self, obj: ObjectRef) -> None
    def connector(self, *, role: Literal["source", "target"]) -> OracleConnector
        # source: the read-only user; target: the owner — both with action "conformance"

# tests/connectors/harness_mongo.py
class MongoHarness:
    engine = "mongo"
    marker = "mongo"

    def __init__(self, client: "pymongo.MongoClient", database: str) -> None
    def create(self, name, columns, key, rows, *, identity=None) -> ObjectRef
        # insert_many; the key column becomes _id (identity -> ObjectId generated); "timestamp" -> datetime,
        # "decimal" -> Decimal128, "int" -> Int64
    def fetch(self, obj) -> list[dict[str, Any]]                 # sorted by _id, _id renamed to the key column
    def ledger_rows(self, job_id: str) -> int                    # dps_write_ledger.count_documents({"job_id": …})
    def drop(self, obj) -> None
    def connector(self, *, role) -> MongoConnector
```
Composite keys do not exist in Mongo (`_id` only, contract §4.2): the Mongo harness maps a composite fixture
key to a document `_id` and the suite's composite-key cases are expected to `pytest.skip("mongo: _id only")`
through the suite's own capability flag (`harness.composite_keys = False`). ROWID cases are Oracle-only
(`harness.rowid = True` only on Oracle).

- [ ] **Step 1: Write the harnesses** (the code above, completed against the audited interface) and register
  `("oracle", OracleHarness)` and `("mongo", MongoHarness)` next to M2's postgres entry, each behind
  `pytest.importorskip` of its driver.
- [ ] **Step 2: Run the suite on all three engines**

```bash
docker compose --profile oracle --profile sources up -d dps-db source-pg oracle mongo
DPS_TEST_REQUIRE_SERVICES=1 pytest -q tests/connectors/conformance.py -m "pg_source or oracle or mongo"
```
Expected: every conformance test passes (or skips for a documented capability flag) for `postgres`, `oracle`
and `mongo`; the summary lists all three parametrisations. A red case is fixed in the M3 connector (never by
editing the suite) and logged in the deviations file.

- [ ] **Step 3: Gates and commit**

```bash
git add tests/connectors/harness_oracle.py tests/connectors/harness_mongo.py tests/connectors/conformance.py tests/connectors/conftest.py && git commit -m "test(connectors): run the shared conformance suite on Oracle and MongoDB" -- tests/connectors/harness_oracle.py tests/connectors/harness_mongo.py tests/connectors/conformance.py tests/connectors/conftest.py
```
(list only the registration file M2 actually uses; `conformance.py` itself must show no diff in its tests.)

---
### Task 15: One customer space for all three engines — seed model and Oracle/Mongo seeding

**Files:**
- Create: `tools/seed_model.py`, `tools/seed_oracle.py`, `tools/seed_mongo.py`, `tests/test_seed_model.py`, `tests/e2e/__init__.py`, `tests/e2e/test_seed_shared_ids.py`
- Modify (M2): `tools/seed_sources.py` (`--target oracle|mongo|all`; postgres customers drawn from `seed_model`)

**Interfaces introduced:**

```python
# tools/seed_model.py
CUSTOMER_ID_BASE: Final = 10_000_000
@dataclass(frozen=True)
class SeedCustomer:
    index: int; customer_id: int; locale: str; country: str; full_name: str; first_name: str; last_name: str
    email: str; mobile: str; dob: date; national_id: str; tax_id: str; addr_line1: str; city: str; pin_code: str
    segment: str; risk_score: int; created_at: datetime; last_contact_at: str; notes: str
    @property
    def mongo_customer_id(self) -> str            # "CUST-<customer_id>" (mockup 07: Mongo stores the prefixed form)
def seed_customers(rows: int, seed: int = 7) -> Iterator[SeedCustomer]
# tools/seed_oracle.py
def seed_oracle(dsn: str, rows: int, seed: int) -> dict[str, int]       # table -> rows; idempotent
# tools/seed_mongo.py
def seed_mongo(uri: str, rows: int, seed: int) -> dict[str, int]        # collection -> docs; idempotent
```

**Behaviours:** `seed_customers` is deterministic in `(rows, seed)`: customer `i` has id `10_000_001 + i` (8
digits, so `fpe_digits` has ≥ 6 free digits) and a persona from the M0 `PersonaFactory` anchored on
`CUSTOMER_ID:<id>`, so name, e-mail, date of birth and address agree everywhere; phones, Aadhaar-shaped
national ids, PAN-shaped tax ids and IBANs come from the M0 `SurrogateEngine` with per-customer template
originals (rule-valid, never issued; e-mails on RFC 2606 domains). No value is real. Every 50th customer's
notes exceed 40,000 characters and contain supplementary-plane emoji (CLOB > 32 KiB with astral
characters); every customer's notes contain their e-mail and phone (free-text detection has work to do).
`seed_oracle` connects as `CRM` and `LOANS` (demo literals), fills `CRM.CUSTOMERS`, `INTERACTIONS` (two per
customer, `EVENT_TS` bursts inside one microsecond), `KYC_DOCUMENTS` (`DOC_REF = "K<index>"`, blank-padded by
CHAR(12)), `ADDRESS_HISTORY` (key-less), `CUSTOMER_EVENTS` (identity), `LOANS.APPLICANTS` (every 4th
customer) in `executemany` batches of 1000 (CLOB binds typed), and records `(seed, rows)` in
`CRM.DPS_SEED_INFO`; a repeat run with the same pair is a no-op, a different pair replaces the data.
`seed_mongo` fills `crm.profiles` (shape of `tests/connectors/mongo_fixtures.profile`, values from the
seed model, `customerId = "CUST-<id>"`), creates a unique index on `customerId`, and records the pair in
`crm.dps_seed_info`. M2's postgres seeding takes its `customers` rows (id, names, e-mail, phone, dob,
national id, tax id) from the same generator. The tools print counts only.

- [ ] **Step 1: Write the failing unit tests** — `tests/test_seed_model.py`:
```python
from __future__ import annotations

import re

from stdnum import verhoeff

from tools.seed_model import CUSTOMER_ID_BASE, seed_customers


def test_deterministic_and_dense_ids() -> None:
    a, b = list(seed_customers(120, 7)), list(seed_customers(120, 7))
    assert a == b and [c.customer_id for c in a] == list(range(CUSTOMER_ID_BASE + 1, CUSTOMER_ID_BASE + 121))
    assert list(seed_customers(5, 8))[0].full_name != a[0].full_name or list(seed_customers(5, 8))[0].email != a[0].email


def test_values_are_rule_valid_and_never_real() -> None:
    for c in seed_customers(300, 7):
        assert c.email.split("@")[1] in ("example.com", "example.net", "example.org")
        assert re.fullmatch(r"[A-Z]{5}\d{4}[A-Z]", c.tax_id)
        digits = re.sub(r"\D", "", c.national_id)
        assert len(digits) == 12 and verhoeff.is_valid(digits)       # M0 rule: Aadhaar-shaped, never issued
        assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{9}", c.last_contact_at)
        assert c.email in c.notes and c.mongo_customer_id == f"CUST-{c.customer_id}"


def test_large_astral_notes_every_fiftieth_customer() -> None:
    big = [c for c in seed_customers(120, 7) if c.index % 50 == 0]
    assert big and all(len(c.notes) > 40_000 and "\U0001F600" in c.notes for c in big)
```
`tests/e2e/test_seed_shared_ids.py` (markers `integration`, `pg_source`, `oracle`, `mongo`): runs
`seed_sources.py` for all three targets with `--rows 200 --seed 7` against the containers (the default demo
users), then asserts: `{customer_id}` of the PG `customers` table == `{CUSTOMER_ID}` of `CRM.CUSTOMERS` ==
`{int(customerId[5:])}` of `crm.profiles` (200 each); for 10 sampled ids `FULL_NAME` (Oracle),
`profile.fullName` (Mongo) and the PG customer's name agree; a second run prints `unchanged` for every table.

- [ ] **Step 2: Run to confirm failure** — `pytest -q tests/test_seed_model.py` → FAIL (`ModuleNotFoundError: tools.seed_model`).

- [ ] **Step 3: Write `tools/seed_model.py`**
```python
"""One synthetic customer space shared by the PostgreSQL, Oracle and MongoDB demo data.

Every value is produced by the M0 surrogate engine under a fixed demo seed, so it is rule-valid and never
real (RFC 2606 e-mails, never-issued Aadhaar-shaped numbers, test-range phones). The same (rows, seed)
yields the same customers for every target: that is what lets the three-way consistency join line up.
"""
from __future__ import annotations

import hashlib
import random
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Final

from dps.engine.synth.personas import PersonaFactory
from dps.engine.synth.surrogates import SurrogateEngine

CUSTOMER_ID_BASE: Final = 10_000_000
_LOCALES: Final = ("en_IN", "en_IN", "en_IN", "en_US", "en_GB", "es_MX", "en_CA")
_COUNTRY: Final = {"en_IN": "IN", "en_US": "US", "en_GB": "GB", "es_MX": "MX", "en_CA": "CA"}
_DOMAINS: Final = ("example.com", "example.net", "example.org")
_SEGMENTS: Final = ("retail", "retail", "retail", "premium", "private", "sme")
_ASTRAL: Final = "\U0001F600"


@dataclass(frozen=True)
class SeedCustomer:
    index: int
    customer_id: int
    locale: str
    country: str
    full_name: str
    first_name: str
    last_name: str
    email: str
    mobile: str
    dob: date
    national_id: str
    tax_id: str
    addr_line1: str
    city: str
    pin_code: str
    segment: str
    risk_score: int
    created_at: datetime
    last_contact_at: str
    notes: str

    @property
    def mongo_customer_id(self) -> str:
        return f"CUST-{self.customer_id}"


def _seed_bytes(seed: int) -> bytes:
    return hashlib.sha256(f"dps.demo.seed.v1|{seed}".encode()).digest()


def seed_customers(rows: int, seed: int = 7) -> Iterator[SeedCustomer]:
    """``rows`` customers with ids ``10_000_001 …``; deterministic in ``(rows, seed)``."""
    sb = _seed_bytes(seed)
    personas, surrogates = PersonaFactory(sb), SurrogateEngine(sb, default_locale="en_IN")
    for i in range(rows):
        cid = CUSTOMER_ID_BASE + i + 1
        anchor = f"CUSTOMER_ID:{cid}"
        rng = random.Random(hashlib.sha256(sb + cid.to_bytes(8, "big")).digest())
        locale = _LOCALES[rng.randrange(len(_LOCALES))]
        p = personas.for_anchor(anchor, locale)
        email = f"{p.email_local}.{cid % 100000}@{_DOMAINS[cid % 3]}"
        mobile = surrogates.surrogate("PHONE", f"+91 9{cid % 10**9:09d}", "en_IN", anchor)
        national_id = surrogates.surrogate("AADHAAR", f"{2 + cid % 8}{cid % 10**11:011d}", "en_IN", anchor)
        national_id = " ".join(national_id[k:k + 4] for k in (0, 4, 8)) if len(national_id) == 12 else national_id
        tax_id = surrogates.surrogate("PAN_IN", f"ABCPK{cid % 10000:04d}F", "en_IN", anchor)
        created = datetime(2019, 1, 1) + timedelta(minutes=rng.randrange(3 * 365 * 24 * 60))
        last = created + timedelta(days=rng.randrange(600), seconds=rng.randrange(86400))
        note = (f"KYC call with {p.first_name}; follow up at {email} or {mobile}. "
                f"Prefers branch in {p.city}. \u2705 \u0928\u092e\u0938\u094d\u0924\u0947")
        if i % 50 == 0:
            note = note + (" " + "x" * 997 + _ASTRAL) * 41
        yield SeedCustomer(
            index=i, customer_id=cid, locale=locale, country=_COUNTRY[locale], full_name=p.full_name,
            first_name=p.first_name, last_name=p.last_name, email=email, mobile=mobile, dob=p.dob,
            national_id=national_id, tax_id=tax_id.upper(), addr_line1=p.street[:200], city=p.city[:80],
            pin_code=p.postal_code[:10], segment=_SEGMENTS[rng.randrange(len(_SEGMENTS))],
            risk_score=rng.randint(300, 900), created_at=created,
            last_contact_at=last.strftime("%Y-%m-%dT%H:%M:%S") + f".{rng.randrange(10**9):09d}", notes=note,
        )
```
If the M0 surrogate for `AADHAAR`/`PAN_IN` returns a shape the unit test rejects (it is M0's rule), fix the
template original here (never the assertion) and log it.

- [ ] **Step 4: Write `tools/seed_oracle.py`** — module docstring as above; `seed_oracle(dsn, rows, seed)`:
  connect `oracledb.connect(user="crm", password="crm", dsn=dsn)` and the `loans` twin; create
  `CRM.DPS_SEED_INFO (SEED NUMBER(10), ROWS_N NUMBER(10), SEEDED_AT TIMESTAMP(6))` through
  `osql.ledger_ddl`-style PL/SQL that ignores ORA-00955; if a row `(seed, rows)` exists return
  `{t: "unchanged"}`; else `DELETE` (children first) from the six tables and insert, per 1000 customers:
  `CUSTOMERS` (`setinputsizes(..., NOTES=oracledb.DB_TYPE_CLOB)`, `LAST_CONTACT_AT` through
  `TO_TIMESTAMP(:x, 'YYYY-MM-DD"T"HH24:MI:SS.FF9')`), `INTERACTIONS` (two rows: `EVENT_TS` =
  `last_contact_at` and the same microsecond `+ 1 ns`; `PAYLOAD` = `json.dumps({"channel": …, "agent":
  "agent.<n>@example.org"})`), `KYC_DOCUMENTS` (`f"K{index}"`, doc type `aadhaar`, number = national id),
  `ADDRESS_HISTORY` (current address + one older), `CUSTOMER_EVENTS` (two, no `EVENT_ID` — identity),
  `LOANS.APPLICANTS` (`f"APP-{index:07d}"`, `PAN = tax_id`, income `Decimal`, status), then the marker
  row; one commit per table batch; returns counts. `tools/seed_mongo.py`: `pymongo.MongoClient(uri)` →
  database `crm`; marker in `dps_seed_info`; `profiles.delete_many({})`, `insert_many` per 1000 docs, unique
  index `customerId`. In `tools/seed_sources.py` add the `oracle`/`mongo`/`all` targets with
  `--oracle-dsn` (default `127.0.0.1:1522/FREEPDB1`) and `--mongo-uri` (default
  `mongodb://127.0.0.1:27018/?directConnection=true`), and change M2's postgres customers to come from
  `seed_customers(rows, seed)`; print `"<table>: <n> rows"` or `"<table>: unchanged"` only.

- [ ] **Step 5: Run**

```bash
pytest -q tests/test_seed_model.py
docker compose --profile oracle --profile sources up -d source-pg oracle mongo
python tools/seed_sources.py --target all --rows 200 --seed 7
DPS_TEST_REQUIRE_SERVICES=1 pytest -q tests/e2e/test_seed_shared_ids.py
```
Expected: 3 unit tests pass; the tool prints counts (`CRM.CUSTOMERS: 200 rows`, `CRM.INTERACTIONS: 400 rows`,
`crm.profiles: 200 rows`, … and M2's postgres tables); the shared-id test passes.

- [ ] **Step 6: Gates and commit**

```bash
git add tools/seed_model.py tools/seed_oracle.py tools/seed_mongo.py tools/seed_sources.py tests/test_seed_model.py tests/e2e/__init__.py tests/e2e/test_seed_shared_ids.py && git commit -m "feat(tools): one synthetic customer space seeded into PostgreSQL, Oracle and MongoDB" -- tools/seed_model.py tools/seed_oracle.py tools/seed_mongo.py tools/seed_sources.py tests/test_seed_model.py tests/e2e/__init__.py tests/e2e/test_seed_shared_ids.py
```

---
### Task 16: The cross-system consistency check — join forms, lookups, attribute agreement (counts only)

**Files:**
- Create: `dps/layers/connectors/consistency.py`
- Modify: `dps/layers/connectors/oracle_sql.py` (`lookup_sql`), `dps/layers/connectors/oracle.py` and `mongo.py` (`lookup_values`), (M2) `dps/layers/connectors/postgres.py` (`lookup_values`)
- Test: `tests/connectors/test_consistency.py` (offline, fake connectors), `tests/connectors/test_lookup_values_m3.py` (markers `integration`, `oracle`, `mongo`, `pg_source`)

**Interfaces introduced:**

```python
JOIN_PREFERENCE: Final = ("CUSTOMER_ID", "ACCOUNT_NUMBER", "EMPLOYEE_ID", "CASE_ID", "USERNAME", "EMAIL")
VALUE_DETERMINISTIC: Final = frozenset({"det_token", "fpe_digits", "fpe_text", "email", "phone", "hmac_pseudonym"})
READY_STATES: Final = frozenset({"running", "waiting", "paused", "succeeded"})

class CheckMember(BaseModel):            job_id: str; path: str
class ConsistencyCheckRequest(BaseModel):
    pairs: list[CheckMember] | None = Field(None, min_length=2, max_length=8)   # None: derived from snapshots
    sample: int = Field(1000, ge=10, le=10_000)
    min_coverage: float = Field(1.0, gt=0.0, le=1.0)
    attributes: bool = True
class MemberResult(BaseModel):
    job_id: str; engine: str; connection_id: str; object: str; path: str
    entity_type: str | None; method: str | None; params_hash: str | None
    form: Literal["numeric", "prefixed_digits", "exact", "unknown"]; width: int | None; prefix_len: int | None
    found: int; anchor: bool
class PairResult(BaseModel):             left: int; right: int; checked: int; matched: int; coverage: float; consistent: bool
class AttributeResult(BaseModel):
    entity_type: str; method: str; members: list[int]; counts_toward_pass: bool
    compared: int; agreed: int; ambiguous: int; consistent: bool
class CheckProblem(BaseModel):           code: str; member: int | None = None; pair: list[int] | None = None
class ConsistencyCheckResult(BaseModel):
    group_id: str; checked_at: datetime; sample_requested: int; sampled: int; anchor: int
    members: list[MemberResult]; pairs: list[PairResult]; all_members_matched: int
    attributes: list[AttributeResult]; methods_agree: bool; problems: list[CheckProblem]
    passed: bool; consistent_pairs: int; total_pairs: int; min_coverage: float; duration_ms: float
class ConsistencyError(Exception):       status: int; code: str
@runtime_checkable
class ValueLookup(Protocol):
    async def lookup_values(self, shape: TableShape, join: ColumnInfo, values: list[Any],
                            attributes: list[ColumnInfo]) -> list[Row]: ...
@dataclass(frozen=True)
class JoinForm:                          kind: Literal["numeric","prefixed_digits","exact","unknown"]; prefix: str = ""; width: int | None = None
def detect_form(column: ColumnInfo, values: Sequence[Any]) -> JoinForm
def canonical(value: Any, form: JoinForm) -> int | str | None
def comparable(a: JoinForm, b: JoinForm) -> bool
def candidates(c: int | str, form: JoinForm, column: ColumnInfo) -> list[Any]
def attribute_text(value: Any, method: str) -> str | None
def derive_members(jobs: list[Job], snapshots: Mapping[str, ClassificationSnapshot]) -> list[CheckMember]
Opener = Callable[..., Awaitable[Connector]]
async def run_consistency_check(*, group: JobGroup, jobs: list[Job], snapshots: Mapping[str, ClassificationSnapshot],
                                connections: Mapping[str, Connection], request: ConsistencyCheckRequest,
                                settings: Settings, kek: bytes | None,
                                opener: Opener = open_connector) -> ConsistencyCheckResult
# oracle_sql.py
def lookup_sql(shape: TableShape, join: ColumnInfo, columns: list[ColumnInfo], n: int) -> ReadPlan
# OracleConnector / MongoConnector / PostgresConnector
async def lookup_values(self, shape, join, values, attributes) -> list[Row]
```

**Behaviours:**
- Members are run jobs of the group (`group_id` match) in a ready state with `rows_written > 0` and not a dry
  run (`409 member_not_ready`), naming a column path of their **target** (`422 path_unknown`); `files`
  targets → `422 member_unsupported`; a job outside the group → `422 member_not_in_group`. With `pairs`
  omitted, the join entity is the first of `JOIN_PREFERENCE` present (method ≠ `keep`) in every member's
  accepted snapshot, else the alphabetically first shared entity (`422 no_shared_entity`); per member the
  column with that entity, preferring key, then anchor, then the shortest path.
- Join forms are detected from values: numeric column or all-integer values → `numeric`; strings that are a
  constant non-digit prefix + digits → `prefixed_digits` (width when constant); otherwise `exact`. Numeric
  and prefixed-digit forms compare as integers, so `73104482` (Oracle NUMBER), `"CUST-73104482"` (Mongo)
  and `73104482` (PG bigint) join — and an FF1 output with a leading zero (`"CUST-03104482"`, which a NUMBER
  stores as `3104482`) still joins, because lookups regenerate the zero-padded candidate from the member's
  width. `exact` joins only `exact` (`join_form_mismatch`).
- The anchor (first member) contributes up to `sample` distinct canonical values from a random sample of its
  target (`connector.sample`); every member (the anchor too) is probed with `lookup_values` in `IN`/`$in`
  chunks of 500, concurrently across members; per pair `checked = |S|`, `matched = |found_i ∩ found_j|`,
  `coverage = matched / checked`, `consistent = coverage ≥ min_coverage` and both forms comparable and the
  join methods agree.
- Attributes: every other entity type mapped (method ≠ `keep`) in ≥ 2 members is compared on the joined
  values: canonical text per method (digits for `fpe_digits`/`phone`, lower-case for `email`, date part for
  `date_shift`, exact otherwise); `agreed` when every member holds one distinct value and all are equal,
  `ambiguous` when a member holds several (one-to-many tables). Only value-deterministic methods
  (`VALUE_DETERMINISTIC`, same method and params everywhere) count toward `passed`; anchor-dependent ones
  (`date_shift`, `surrogate`) are reported informationally (their agreement needs identical anchor text,
  contract §6.2).
- `methods_agree`: the join columns have the same entity type, method and params hash in every member; a
  member whose job used `field_scoped` tweaks → problem `field_scoped`; a member job whose `kid` differs
  from the group's → problem `kid_mismatch` (both block `passed`).
- `passed = sampled > 0 and every pair consistent and methods_agree and every counted attribute consistent
  and no blocking problem`.
- **Value-free:** the result holds ids, object names, paths, entity types, method names, a 12-hex params
  hash, forms, widths, the *length* of a prefix and counts. No sampled value, token or prefix text. Every
  connector is closed in a `finally`.

- [ ] **Step 1: Write the failing unit tests** — `tests/connectors/test_consistency.py` (offline). A
  `FakeConnector` holds `rows: list[dict]` and a `TableShape`; `sample()` returns the first `n` rows,
  `lookup_values()` filters rows whose join value is in `values` and records the largest `len(values)` seen;
  `close()` sets a flag. `make_job(id, engine, path, kid="k_1", state="succeeded", write_mode="table",
  rows_written=100)` builds M2's `Job` with a redacted spec; `make_snapshot(path → (entity, method,
  params))` builds an accepted `ClassificationSnapshot`.

```python
from __future__ import annotations

import json
import random

import pytest

from dps.layers.connectors.base import ColumnInfo
from dps.layers.connectors.consistency import (
    ConsistencyCheckRequest, ConsistencyError, JoinForm, attribute_text, candidates, canonical,
    comparable, derive_members, detect_form, run_consistency_check,
)

INT = ColumnInfo(path="$.CUSTOMER_ID", name="CUSTOMER_ID", data_type="NUMBER(12)", kind="int", is_key=True)
TXT = ColumnInfo(path="$.customerId", name="customerId", data_type="string", kind="text")


def test_detect_form() -> None:
    assert detect_form(INT, [3104482, 73104482]).kind == "numeric"
    f = detect_form(TXT, ["CUST-03104482", "CUST-73104482"])
    assert (f.kind, f.prefix, f.width) == ("prefixed_digits", "CUST-", 8)
    assert detect_form(TXT, ["CUST-1", "CUST-22"]).width is None
    assert detect_form(TXT, ["A-1", "B-2"]).kind == "exact"
    assert detect_form(TXT, ["00123", "04567"]) == JoinForm("prefixed_digits", "", 5)
    assert detect_form(TXT, [None]).kind == "unknown"


def test_leading_zeros_join_numbers_and_prefixed_digits() -> None:
    num, pre = JoinForm("numeric"), JoinForm("prefixed_digits", "CUST-", 8)
    assert canonical(3104482, num) == canonical("CUST-03104482", pre) == 3104482
    assert candidates(3104482, pre, TXT) == ["CUST-03104482"]
    assert candidates(123456789, pre, TXT) == []                       # wider than the member's ids
    assert comparable(num, pre) and not comparable(num, JoinForm("exact"))
    assert canonical("OTHER-03104482", pre) is None


def test_attribute_text() -> None:
    assert attribute_text("+91 07311 0448", "phone") == attribute_text("9107311 0448", "phone")
    assert attribute_text("A.B@Example.COM", "email") == "a.b@example.com"
    assert attribute_text("1987-03-14T00:00:00", "date_shift") == "1987-03-14"
    assert attribute_text("ABCDE1234F  ", "fpe_text") == "ABCDE1234F" and attribute_text(None, "email") is None
```
Further cases (each asserting exactly what is stated):

| Test | Arrange (fakes) | Assert |
|---|---|---|
| `test_derive_members_prefers_customer_id_key_columns` | Oracle snapshot with `CUSTOMER_ID` on `$.CUSTOMER_ID` (key) and `$.REFERRER_ID`; Mongo with `$.customerId`; PG with `$.customer_id` | `[m.path for m in derive_members(...)] == ["$.CUSTOMER_ID", "$.customerId", "$.customer_id"]`; snapshots sharing nothing → `ConsistencyError` 422 `no_shared_entity` |
| `test_three_way_check_passes` | 500 customers; tokens = a fixed random permutation; Oracle ints (leading zeros dropped), Mongo `"CUST-" + zfill(8)`, PG ints; same masked `FULL_NAME`/`fullName`/`payer_name` per customer; PG holds 3 rows per customer with the same name | `passed`; 3 pairs each `matched == checked == 500`, `coverage == 1.0`; `all_members_matched == 500`; attribute `PERSON_NAME` `compared == agreed == 500`, `ambiguous == 0` |
| `test_token_drift_fails_the_pair` | Mongo member has 10 % different tokens | the two Mongo pairs `coverage == 0.9`, `consistent is False`; `passed is False`; Oracle↔PG still consistent |
| `test_min_coverage_allows_partial_tables` | PG member lacks 20 % of customers | default request → `passed is False`; `min_coverage=0.75` → `passed` |
| `test_method_mismatch` | Mongo snapshot maps the join column to `det_token` | `methods_agree is False`; problem `method_mismatch`; `passed is False` |
| `test_join_form_mismatch` | Mongo values are free strings | problem `join_form_mismatch` for member 1; its pairs inconsistent |
| `test_anchor_dependent_attributes_are_informational` | `DATE_OF_BIRTH` (`date_shift`) disagrees for every customer | its `AttributeResult.counts_toward_pass is False`; `passed` stays true |
| `test_member_validation` | job not in the group; a `files` target; a `dry_run` job; a `failed` job; a path absent from the target shape | `ConsistencyError` (422 `member_not_in_group`), (422 `member_unsupported`), (409 `member_not_ready`) ×2, (422 `path_unknown`) |
| `test_field_scoped_and_kid_problems_block` | one member job with `spec.consistency.field_scoped = True`; another with `kid = "k_other"` | problems `field_scoped` and `kid_mismatch`; `passed is False` |
| `test_lookups_are_chunked_and_connectors_closed` | `sample=2000` | every fake saw `max(len(values)) <= 500`; all fakes `closed`, also when one `lookup_values` raises (the error propagates) |
| `test_result_is_value_free` | tokens/names prefixed `SENTINEL-` | `"SENTINEL" not in json.dumps(result.model_dump(mode="json"))`; `"CUST-" not in` it; `members[1].prefix_len == 5` |

- [ ] **Step 2: Run to confirm failure** — `pytest -q tests/connectors/test_consistency.py` → FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Write `dps/layers/connectors/consistency.py`**
```python
"""Cross-system consistency check for a job group (contract §6.5): a three-way join over masked outputs.

The check reads only the members' targets — the masked copies — needs no key, and returns counts. Sampled
values exist in memory for the duration of the call and never reach a log, a metric, the response or the
database. Formats may differ across systems (Oracle NUMBER 73104482, Mongo "CUST-73104482"): numeric and
prefixed-digit forms compare as integers, and lookups regenerate each member's own representation,
including FF1 outputs whose leading zero a NUMBER column dropped.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Final, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from dps.config import Settings
from dps.layers.connectors.base import ColumnInfo, Connector, ObjectRef, Row, TableShape
from dps.layers.connectors.docpaths import column_values, dotted
from dps.layers.connectors.registry import open_connector
from dps.layers.connectors.selection import Selection
from dps.observability import CONSISTENCY_CHECKS, get_logger

logger = get_logger(__name__)
JOIN_PREFERENCE: Final = ("CUSTOMER_ID", "ACCOUNT_NUMBER", "EMPLOYEE_ID", "CASE_ID", "USERNAME", "EMAIL")
VALUE_DETERMINISTIC: Final = frozenset({"det_token", "fpe_digits", "fpe_text", "email", "phone", "hmac_pseudonym"})
READY_STATES: Final = frozenset({"running", "waiting", "paused", "succeeded"})
BLOCKING: Final = frozenset({"field_scoped", "kid_mismatch", "method_mismatch"})
LOOKUP_CHUNK: Final = 500
FORM_SAMPLE: Final = 200
_PREFIXED: Final = re.compile(r"^(\D*?)(\d+)$")
_DIGIT_METHODS: Final = frozenset({"fpe_digits", "phone"})


class CheckMember(BaseModel):
    job_id: str = Field(min_length=3, max_length=64)
    path: str = Field(min_length=3, max_length=300)


class ConsistencyCheckRequest(BaseModel):
    pairs: list[CheckMember] | None = Field(None, min_length=2, max_length=8)
    sample: int = Field(1000, ge=10, le=10_000)
    min_coverage: float = Field(1.0, gt=0.0, le=1.0)
    attributes: bool = True


class MemberResult(BaseModel):
    job_id: str
    engine: str
    connection_id: str
    object: str
    path: str
    entity_type: str | None
    method: str | None
    params_hash: str | None
    form: Literal["numeric", "prefixed_digits", "exact", "unknown"]
    width: int | None
    prefix_len: int | None
    found: int
    anchor: bool


class PairResult(BaseModel):
    left: int
    right: int
    checked: int
    matched: int
    coverage: float
    consistent: bool


class AttributeResult(BaseModel):
    entity_type: str
    method: str
    members: list[int]
    counts_toward_pass: bool
    compared: int
    agreed: int
    ambiguous: int
    consistent: bool


class CheckProblem(BaseModel):
    code: str
    member: int | None = None
    pair: list[int] | None = None


class ConsistencyCheckResult(BaseModel):
    group_id: str
    checked_at: datetime
    sample_requested: int
    sampled: int
    anchor: int
    members: list[MemberResult]
    pairs: list[PairResult]
    all_members_matched: int
    attributes: list[AttributeResult]
    methods_agree: bool
    problems: list[CheckProblem]
    passed: bool
    consistent_pairs: int
    total_pairs: int
    min_coverage: float
    duration_ms: float


class ConsistencyError(Exception):
    """A request the check cannot run; ``status``/``code`` map to ``DpsHttpError``."""

    def __init__(self, status: int, code: str) -> None:
        super().__init__(code)
        self.status, self.code = status, code


@runtime_checkable
class ValueLookup(Protocol):
    async def lookup_values(self, shape: TableShape, join: ColumnInfo, values: list[Any],
                            attributes: list[ColumnInfo]) -> list[Row]: ...


@dataclass(frozen=True)
class JoinForm:
    kind: Literal["numeric", "prefixed_digits", "exact", "unknown"]
    prefix: str = ""                 # memory only; results carry its length
    width: int | None = None


def _text(value: Any) -> str:
    return value.rstrip() if isinstance(value, str) else str(value)


def _integral(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    try:
        d = Decimal(str(value))
    except InvalidOperation:
        return False
    return d == d.to_integral_value()


def detect_form(column: ColumnInfo, values: Sequence[Any]) -> JoinForm:
    present = [v for v in values if v is not None]
    if not present:
        return JoinForm("unknown")
    if column.kind in ("int", "decimal") and all(_integral(v) for v in present):
        return JoinForm("numeric")
    if all(isinstance(v, int) and not isinstance(v, bool) for v in present):
        return JoinForm("numeric")
    matches = [_PREFIXED.match(_text(v)) for v in present]
    if all(m is not None for m in matches):
        prefixes = {m.group(1) for m in matches if m}
        widths = {len(m.group(2)) for m in matches if m}
        if len(prefixes) == 1:
            return JoinForm("prefixed_digits", prefixes.pop(), widths.pop() if len(widths) == 1 else None)
    return JoinForm("exact")


def canonical(value: Any, form: JoinForm) -> int | str | None:
    if value is None:
        return None
    if form.kind == "numeric":
        return int(Decimal(str(value))) if _integral(value) else None
    text = _text(value)
    if form.kind == "prefixed_digits":
        m = _PREFIXED.match(text)
        return int(m.group(2)) if m and m.group(1) == form.prefix else None
    return text


def comparable(a: JoinForm, b: JoinForm) -> bool:
    numeric = {"numeric", "prefixed_digits"}
    return (a.kind in numeric and b.kind in numeric) or (a.kind == b.kind == "exact")


def candidates(c: int | str, form: JoinForm, column: ColumnInfo) -> list[Any]:
    """The member's own stored representations of canonical value ``c``."""
    if form.kind == "numeric":
        return [c] if isinstance(c, int) else []
    if form.kind == "prefixed_digits":
        if not isinstance(c, int):
            return []
        digits = str(c)
        if form.width is None:
            return [form.prefix + digits]
        return [form.prefix + digits.zfill(form.width)] if len(digits) <= form.width else []
    if isinstance(c, str):
        return [c.ljust(column.length) if column.kind == "char" and column.length else c]
    return []


def attribute_text(value: Any, method: str) -> str | None:
    if value is None:
        return None
    text = value.isoformat() if isinstance(value, datetime) else _text(value)
    if method in _DIGIT_METHODS:
        return re.sub(r"\D", "", text).lstrip("0") or "0"
    if method == "email":
        return text.lower()
    if method == "date_shift":
        return text[:10]
    return text


def _params_hash(params: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps(dict(params), sort_keys=True, default=str).encode()).hexdigest()[:12]


def _mapped(snapshot: Any) -> dict[str, Any]:
    """entity type -> preferred mapped column (key, then anchor, then shortest path)."""
    out: dict[str, Any] = {}
    for col in sorted(snapshot.columns, key=lambda c: (not c.is_key, not c.anchor, len(c.path), c.path)):
        if col.entity_type and col.method.method != "keep":
            out.setdefault(col.entity_type, col)
    return out


def derive_members(jobs: list[Any], snapshots: Mapping[str, Any]) -> list[CheckMember]:
    mapped = {j.id: _mapped(snapshots[j.snapshot_id]) for j in jobs}
    shared = set.intersection(*(set(m) for m in mapped.values())) if mapped else set()
    join = next((e for e in JOIN_PREFERENCE if e in shared), None) or (min(shared) if shared else None)
    if join is None:
        raise ConsistencyError(422, "no_shared_entity")
    return [CheckMember(job_id=j.id, path=mapped[j.id][join].path) for j in jobs]


@dataclass
class _Member:
    index: int
    job: Any
    connection_id: str
    obj: ObjectRef
    connector: Connector
    shape: TableShape
    join: ColumnInfo
    entity: str | None
    method: str | None
    params_hash: str | None
    attributes: dict[str, tuple[ColumnInfo, str, str]] = field(default_factory=dict)  # entity -> (col, method, hash)
    form: JoinForm = JoinForm("unknown")


Opener = Callable[..., Awaitable[Connector]]


def _select_column(column: ColumnInfo, engine: str) -> str:
    if engine != "mongo":
        return column.name
    name = dotted(column.path)
    if name is None:
        raise ConsistencyError(422, "member_unsupported")
    return name


async def run_consistency_check(*, group: Any, jobs: list[Any], snapshots: Mapping[str, Any],
                                connections: Mapping[str, Any], request: ConsistencyCheckRequest,
                                settings: Settings, kek: bytes | None,
                                opener: Opener = open_connector) -> ConsistencyCheckResult:
    """Run the three-way (n-way) join over the members' masked targets; counts only."""
    t0 = time.perf_counter()
    in_group = {j.id: j for j in jobs if j.kind == "run" and j.group_id == group.id}
    ready = [j for j in in_group.values() if j.state in READY_STATES]
    wanted = request.pairs or derive_members(ready, snapshots)
    problems: list[CheckProblem] = []
    members: list[_Member] = []
    try:
        for i, m in enumerate(wanted):
            job = in_group.get(m.job_id)
            if job is None:
                raise ConsistencyError(422, "member_not_in_group")
            if (job.state not in READY_STATES or job.progress.rows_written <= 0
                    or job.progress.outcome == "dry_run"):
                raise ConsistencyError(409, "member_not_ready")
            target, source = job.spec.get("target", {}), job.spec.get("source", {})
            if target.get("write_mode") == "files":
                raise ConsistencyError(422, "member_unsupported")
            if job.spec.get("consistency", {}).get("field_scoped"):
                problems.append(CheckProblem(code="field_scoped", member=i))
            if job.kid and job.kid != group.kid:
                problems.append(CheckProblem(code="kid_mismatch", member=i))
            conn_id = target.get("connection_id") or source["connection_id"]
            obj = ObjectRef.model_validate(target.get("object") or source["object"])
            connector = await opener(connections[conn_id], settings, action="consistency", kek=kek,
                                     job_id=job.id)
            try:
                shape = await connector.describe(obj)
            except BaseException:
                await connector.close()
                raise
            join = next((c for c in shape.columns if c.path == m.path), None)
            if join is None:
                await connector.close()
                raise ConsistencyError(422, "path_unknown")
            sc = _mapped_by_path(snapshots[job.snapshot_id]).get(m.path)
            member = _Member(i, job, conn_id, obj, connector, shape, join,
                             sc.entity_type if sc else None, sc.method.method if sc else None,
                             _params_hash(sc.method.params) if sc else None)
            if request.attributes:
                by_path = {c.path: c for c in shape.columns}
                for entity, col in _mapped(snapshots[job.snapshot_id]).items():
                    if entity != member.entity and col.path in by_path:
                        member.attributes[entity] = (by_path[col.path], col.method.method,
                                                     _params_hash(col.method.params))
            members.append(member)
        return await _check(group, members, request, problems, t0)
    finally:
        await asyncio.gather(*(m.connector.close() for m in members), return_exceptions=True)


def _mapped_by_path(snapshot: Any) -> dict[str, Any]:
    return {c.path: c for c in snapshot.columns}


async def _check(group: Any, members: list[_Member], request: ConsistencyCheckRequest,
                 problems: list[CheckProblem], t0: float) -> ConsistencyCheckResult:
    anchor = members[0]
    sel = Selection(columns=[_select_column(anchor.join, anchor.connector.engine)])
    rows = await anchor.connector.sample(anchor.shape, sel, {}, request.sample)
    values = column_values(rows, anchor.join)
    anchor.form = detect_form(anchor.join, values)
    sample = list(dict.fromkeys(c for v in values if (c := canonical(v, anchor.form)) is not None))
    sample = sample[: request.sample]
    wanted = set(sample)
    for m in members[1:]:
        sel_m = Selection(columns=[_select_column(m.join, m.connector.engine)])
        m.form = detect_form(m.join, column_values(await m.connector.sample(m.shape, sel_m, {}, FORM_SAMPLE), m.join))

    async def probe(m: _Member) -> dict[Any, dict[str, set[str]]]:
        if not isinstance(m.connector, ValueLookup):
            raise ConsistencyError(422, "member_unsupported")
        if not comparable(anchor.form, m.form):
            problems.append(CheckProblem(code="join_form_mismatch", member=m.index))
            return {}
        by_candidate: dict[Any, Any] = {}
        for c in sample:
            for cand in candidates(c, m.form, m.join):
                by_candidate[cand] = c
        found: dict[Any, dict[str, set[str]]] = {}
        items = list(by_candidate)
        attrs = [(e, col, meth) for e, (col, meth, _) in m.attributes.items()]
        for start in range(0, len(items), LOOKUP_CHUNK):
            chunk = items[start:start + LOOKUP_CHUNK]
            for row in await m.connector.lookup_values(m.shape, m.join, chunk, [a[1] for a in attrs]):
                for v in column_values([row], m.join):
                    c = canonical(v, m.form)
                    if c not in wanted:
                        continue
                    rec = found.setdefault(c, {})
                    for entity, col, meth in attrs:
                        texts = {t for x in column_values([row], col) if (t := attribute_text(x, meth))}
                        rec.setdefault(entity, set()).update(texts)
        return found

    results = await asyncio.gather(*(probe(m) for m in members))
    sets = [set(r) for r in results]
    join_methods = {(m.entity, m.method, m.params_hash) for m in members}
    methods_agree = len(join_methods) == 1
    if not methods_agree:
        problems.append(CheckProblem(code="method_mismatch"))
    blocked_members = {p.member for p in problems if p.code == "join_form_mismatch"}
    pairs: list[PairResult] = []
    for i in range(len(members)):
        for j in range(i + 1, len(members)):
            checked = len(sample)
            matched = len(sets[i] & sets[j])
            coverage = round(matched / checked, 4) if checked else 0.0
            ok = (checked > 0 and coverage >= request.min_coverage and methods_agree
                  and i not in blocked_members and j not in blocked_members)
            pairs.append(PairResult(left=i, right=j, checked=checked, matched=matched, coverage=coverage,
                                    consistent=ok))
    attributes: list[AttributeResult] = []
    entities = sorted({e for m in members for e in m.attributes})
    for entity in entities:
        holders = [m for m in members if entity in m.attributes]
        if len(holders) < 2:
            continue
        methods = {(m.attributes[entity][1], m.attributes[entity][2]) for m in holders}
        method = sorted(methods)[0][0]
        counts = len(methods) == 1 and method in VALUE_DETERMINISTIC
        common = set.intersection(*(sets[m.index] for m in holders))
        compared = agreed = ambiguous = 0
        for c in common:
            texts = [results[m.index].get(c, {}).get(entity, set()) for m in holders]
            if any(not t for t in texts):
                continue
            compared += 1
            if any(len(t) > 1 for t in texts):
                ambiguous += 1
            elif len({next(iter(t)) for t in texts}) == 1:
                agreed += 1
        attributes.append(AttributeResult(
            entity_type=entity, method=method, members=[m.index for m in holders], counts_toward_pass=counts,
            compared=compared, agreed=agreed, ambiguous=ambiguous, consistent=agreed == compared - ambiguous))
    passed = (bool(sample) and all(p.consistent for p in pairs) and methods_agree
              and all(a.consistent for a in attributes if a.counts_toward_pass)
              and not any(p.code in BLOCKING for p in problems))
    result = ConsistencyCheckResult(
        group_id=group.id, checked_at=datetime.now(UTC), sample_requested=request.sample, sampled=len(sample),
        anchor=0,
        members=[MemberResult(
            job_id=m.job.id, engine=m.connector.engine, connection_id=m.connection_id,
            object=m.obj.fqn(), path=m.join.path, entity_type=m.entity, method=m.method,
            params_hash=m.params_hash, form=m.form.kind, width=m.form.width,
            prefix_len=len(m.form.prefix) if m.form.kind == "prefixed_digits" else None,
            found=len(sets[m.index]), anchor=m.index == 0) for m in members],
        pairs=pairs, all_members_matched=len(set.intersection(*sets)) if sets else 0, attributes=attributes,
        methods_agree=methods_agree, problems=problems, passed=passed,
        consistent_pairs=sum(1 for p in pairs if p.consistent), total_pairs=len(pairs),
        min_coverage=request.min_coverage, duration_ms=round((time.perf_counter() - t0) * 1000, 1))
    CONSISTENCY_CHECKS.labels("passed" if passed else "failed").inc()
    logger.info("consistency.checked", group_id=group.id, members=len(members), pairs=len(pairs),
                sampled=len(sample), passed=passed)
    return result
```

- [ ] **Step 4: `lookup_values` per engine** (with `tests/connectors/test_lookup_values_m3.py` integration
  tests: Oracle CHAR join column found by unpadded candidates through `pad_char`, NUMBER join with an
  attribute CLOB; Mongo dotted join `$.kyc.taxId` with attribute `$.profile.fullName`; PG `bigint` join with
  three rows per value returned):
```python
# oracle_sql.py
def lookup_sql(shape: TableShape, join: ColumnInfo, columns: list[ColumnInfo], n: int) -> ReadPlan:
    items = ", ".join(key_value_expr(join, f"v{i}") for i in range(n))
    sql = (f"SELECT /* dps:lookup */ {', '.join(select_expr(c) for c in columns)} "  # noqa: S608
           f"FROM {qualified(shape.object)} t WHERE {sort_expr(join)} IN ({items})")
    t = input_type(join)
    return ReadPlan(sql=sql, columns=list(columns), bind_types={f"v{i}": t for i in range(n)} if t else {})

# OracleConnector
    async def lookup_values(self, shape: TableShape, join: ColumnInfo, values: list[Any],
                            attributes: list[ColumnInfo]) -> list[Row]:
        cols = [join, *[a for a in attributes if a.name != join.name]]
        plan = osql.lookup_sql(shape, join, cols, len(values))
        binds = {f"v{i}": _bind_key(join, v) for i, v in enumerate(values)}
        return await self._call("lookup", self._fetch, plan, binds, 1000)

# MongoConnector
    async def lookup_values(self, shape: TableShape, join: ColumnInfo, values: list[Any],
                            attributes: list[ColumnInfo]) -> list[Row]:
        field_ = dotted(join.path)
        paths = [p for p in (dotted(a.path) for a in attributes) if p]
        if field_ is None:
            raise ConnectorError("path_unsupported")
        keep = [p for p in {field_, *paths} if not any(o != p and p.startswith(o + ".") for o in {field_, *paths})]
        await self.open()
        cursor = self._coll(shape.object.name).find({field_: {"$in": values}}, projection={p: 1 for p in keep},
                                                    comment=self.comment)
        return await self._guard("lookup", cursor.to_list(length=None))
```
PostgresConnector (M2 file): the same signature; `SELECT <join>, <attrs> FROM <schema.table> WHERE <join> =
ANY($1)` inside M2's read-only transaction with its statement timeout, identifiers through `quote_ident(…,
"postgres")`, returning dict rows.

- [ ] **Step 5: Run tests and gates**

Run: `pytest -q tests/connectors/test_consistency.py && DPS_TEST_REQUIRE_SERVICES=1 pytest -q -m "oracle or mongo or pg_source" tests/connectors/test_lookup_values_m3.py && ruff check dps tests tools && ruff format --check dps tests tools && mypy dps && pytest -q`
Expected: 14 unit tests and the lookup integration tests pass; suite green.

- [ ] **Step 6: Commit**

```bash
git add dps/layers/connectors/consistency.py dps/layers/connectors/oracle_sql.py dps/layers/connectors/oracle.py dps/layers/connectors/mongo.py dps/layers/connectors/postgres.py tests/connectors/test_consistency.py tests/connectors/test_lookup_values_m3.py && git commit -m "feat(connectors): cross-system consistency check — format-aware joins over masked targets, counts only" -- dps/layers/connectors/consistency.py dps/layers/connectors/oracle_sql.py dps/layers/connectors/oracle.py dps/layers/connectors/mongo.py dps/layers/connectors/postgres.py tests/connectors/test_consistency.py tests/connectors/test_lookup_values_m3.py
```

---
### Task 17: The consistency-check API — route, last result on the group, audit (migration 020)

**Files:**
- Create: `dps/migrations/020_job_group_last_check.sql`, `tests/api/test_groups_consistency_api.py`
- Modify (M2): `dps/layers/api/routers/groups.py`, the `JobGroup` model (`dps/jobs_models.py`), the group repository functions it uses, `dps/observability.py` (nothing new beyond Task 1), `docs/superpowers/plans/2026-10-11-m1-m5-contract.md` (§2.2 row 020, §9 M3 row — additive)

**Interfaces introduced:**

```sql
-- dps/migrations/020_job_group_last_check.sql
-- M3: the latest cross-system consistency check per job group. Value-free by construction:
-- ConsistencyCheckResult carries ids, object names, paths, entity types, method names, forms and counts.
ALTER TABLE __SCHEMA__.dps_job_group ADD COLUMN IF NOT EXISTS last_check jsonb;
ALTER TABLE __SCHEMA__.dps_job_group ADD COLUMN IF NOT EXISTS last_check_at timestamptz;
COMMENT ON COLUMN __SCHEMA__.dps_job_group.last_check IS
  'ConsistencyCheckResult (counts, codes, ids, paths) - never a customer value, token or key';
```
```python
# JobGroup (M2 model) — additive
last_check: dict[str, Any] | None = None          # ConsistencyCheckResult.model_dump(mode="json")
last_check_at: datetime | None = None

# dps/layers/api/routers/groups.py
@router.post("/job-groups/{group_id}/consistency-check", response_model=ConsistencyCheckResult,
             dependencies=[Depends(require_scope("jobs")), Depends(enforce_rate_limit)])
async def consistency_check(group_id: str, body: ConsistencyCheckRequest, request: Request) -> ConsistencyCheckResult
async def save_last_check(tenant_id: str, group_id: str, result: ConsistencyCheckResult) -> None
```

| Request | Response |
|---|---|
| `POST /api/v1/job-groups/{id}/consistency-check` body `{"pairs"?: [{"job_id", "path"}…≥2], "sample": 10..10000 (1000), "min_coverage": (0,1] (1.0), "attributes": bool (true)}`; scope `jobs` | `200 ConsistencyCheckResult` (Task 16 model); `404 group_not_found`; `409 member_not_ready`; `422 member_not_in_group \| member_unsupported \| path_unknown \| no_shared_entity`; `503 connection_failed \| secret_unavailable \| engine_unavailable`; `502 source_error`; `504 check_timeout` — errors are `{"detail", "code"}` via `DpsHttpError` |
| `GET /api/v1/job-groups/{id}` (M2) | gains `last_check` (the stored result) and `last_check_at` |

**Behaviours:** the handler loads the group (`404` when absent or another tenant's — RLS), the group's run
jobs (`list_jobs(tenant, kind=run, group_id=…, limit=200)`), their accepted snapshots and their target
connections, then runs `run_consistency_check` under `asyncio.wait_for(…,
connector_statement_timeout_seconds)`; it stores the result in `dps_job_group.last_check` (one `UPDATE`
inside `acquire(tenant_id)`), writes the audit event `action="group.consistency_check"`,
`outcome="passed"|"failed"`, `counts={"members", "pairs", "consistent_pairs", "sampled"}`, and returns the
result. A `ConnectorError` maps to its code (status 503 for connection-class codes, else 502) with a fixed,
value-free `detail`.

- [ ] **Step 1: Write the failing tests** — `tests/api/test_groups_consistency_api.py` (marker `db`; uses
  M2's `api_client`, `migrated_db` and a `make_group(tenant, jobs=…)` helper that inserts a group and run
  jobs through M2's repository functions):

| Test | Assert |
|---|---|
| `test_migration_020_adds_the_columns` | `information_schema.columns` lists `last_check` (`jsonb`) and `last_check_at` (`timestamp with time zone`) on `dps.dps_job_group` |
| `test_requires_jobs_scope` | a key without `jobs` → 403; no key → 401 |
| `test_unknown_or_foreign_group_is_404` | random id → 404 `group_not_found`; tenant B asking for tenant A's group → 404 |
| `test_result_is_stored_returned_and_audited` | monkeypatch `groups.run_consistency_check` with a coroutine returning a canned passed result → 200 and body equals it; `GET /job-groups/{id}` returns the same `last_check` and a `last_check_at`; the `MemoryAuditSink` holds one event `group.consistency_check` / `passed` with `counts == {"members": 3, "pairs": 3, "consistent_pairs": 3, "sampled": 500}` |
| `test_check_errors_map_to_codes` | `ConsistencyError(409, "member_not_ready")` → 409 `{"code": "member_not_ready"}`; `ConnectorError("connection_failed")` → 503; `ConnectorError("source_error")` → 502; a check slower than a 1-second `connector_statement_timeout_seconds` → 504 `check_timeout`; none of the bodies contain the exception text |
| `test_request_validation` | `sample=5` → 422; `pairs` of one member → 422; `min_coverage=0` → 422 |

- [ ] **Step 2: Run to confirm failure** — `DPS_TEST_REQUIRE_SERVICES=1 pytest -q -m db tests/api/test_groups_consistency_api.py` → FAIL (404 for the new route; columns missing).

- [ ] **Step 3: Implement** the migration file above; the two `JobGroup` fields; in the group repository the
  `SELECT` gains the two columns and `save_last_check` runs
  `UPDATE __SCHEMA__.dps_job_group SET last_check = $2::jsonb, last_check_at = now() WHERE id = $1`
  (tenant bound by `acquire(tenant_id)`); the route:
```python
@router.post("/job-groups/{group_id}/consistency-check", response_model=ConsistencyCheckResult,
             dependencies=[Depends(require_scope("jobs")), Depends(enforce_rate_limit)])
async def consistency_check(group_id: str, body: ConsistencyCheckRequest, request: Request) -> ConsistencyCheckResult:
    """Three-way (n-way) join over the members' masked targets; counts only (contract §6.5)."""
    tenant, settings = request.state.tenant_id, request.app.state.settings
    group = await get_group(tenant, group_id)
    if group is None:
        raise DpsHttpError(404, "group_not_found", "job group not found")
    jobs_, _ = await jobs.list_jobs(tenant, kind=JobKind.run, group_id=group_id, limit=200)
    snapshots = {j.snapshot_id: s for j in jobs_ if j.snapshot_id
                 and (s := await get_snapshot(tenant, j.snapshot_id)) is not None}
    conn_ids = {(j.spec.get("target") or {}).get("connection_id") or j.spec["source"]["connection_id"]
                for j in jobs_}
    connections = {cid: c for cid in conn_ids if (c := await get_connection(tenant, cid)) is not None}
    try:
        result = await asyncio.wait_for(
            run_consistency_check(group=group, jobs=jobs_, snapshots=snapshots, connections=connections,
                                  request=body, settings=settings, kek=load_kek(settings)),
            timeout=settings.connector_statement_timeout_seconds)
    except ConsistencyError as exc:
        raise DpsHttpError(exc.status, exc.code, "the consistency check cannot run for this group") from None
    except ConnectorError as exc:
        status = 503 if exc.code in ("connection_failed", "secret_unavailable", "engine_unavailable") else 502
        raise DpsHttpError(status, exc.code, "a member target could not be read") from None
    except TimeoutError:
        raise DpsHttpError(504, "check_timeout", "the consistency check timed out") from None
    await save_last_check(tenant, group_id, result)
    request.app.state.audit.write(AuditEvent(
        tenant_id=tenant, key_id=request.state.principal.key_id, trace_id=trace_id(request),
        route="/api/v1/job-groups/{id}/consistency-check", action="group.consistency_check",
        outcome="passed" if result.passed else "failed", status=200,
        counts={"members": len(result.members), "pairs": result.total_pairs,
                "consistent_pairs": result.consistent_pairs, "sampled": result.sampled}))
    return result
```
(`get_connection` is `dps.connections.get_connection` and `load_kek` is `dps.secrets.load_kek` (M2 Tasks 2,
14); `get_group`, `get_snapshot` and the `AuditEvent` field names per the Task 1 audit of M2 Tasks 17/25 —
adapt the call shapes, not the behaviour.)

- [ ] **Step 4: Run tests and gates**

Run: `DPS_TEST_REQUIRE_SERVICES=1 pytest -q -m db tests/api/test_groups_consistency_api.py && python -m dps.migrate --verify && ruff check dps tests tools && ruff format --check dps tests tools && mypy dps && pytest -q`
(the migrate command against the scratch metadata DB from M2's `tools/scratch_pg.sh` or the compose
`dps-db`). Expected: 6 tests pass; `--verify` reports no drift after `python -m dps.migrate` applied 020.

- [ ] **Step 5: Commit**

```bash
git add dps/migrations/020_job_group_last_check.sql dps/layers/api/routers/groups.py dps/jobs_models.py tests/api/test_groups_consistency_api.py docs/superpowers/plans/2026-10-11-m1-m5-contract.md && git commit -m "feat(api): job-group consistency check route; last result stored on the group (migration 020)" -- dps/migrations/020_job_group_last_check.sql dps/layers/api/routers/groups.py dps/jobs_models.py tests/api/test_groups_consistency_api.py docs/superpowers/plans/2026-10-11-m1-m5-contract.md
```
(add the group repository module to both lists if it is a separate file in M2.)

---
### Task 18: Console — job groups and the consistency panel on Jobs (mockup 07) and Job detail (mockup 03)

**Files:**
- Create: `frontend/src/lib/groups.ts`, `frontend/src/lib/groups.test.ts` (Vitest, set up by M2 Task 29), `frontend/src/hooks/useJobGroup.ts`, `frontend/src/components/groups/JobGroupCard.tsx`, `frontend/src/components/groups/ConsistencyVerdict.tsx`, `frontend/src/components/groups/CoverageTable.tsx`, `frontend/src/components/groups/NewGroupDialog.tsx`, `frontend/src/components/groups/JobGroupsSection.tsx`
- Modify (M2 pages): `frontend/src/lib/types.ts`, `frontend/src/lib/api.ts`, `frontend/src/pages/Jobs.tsx`, `frontend/src/pages/JobDetail.tsx`, `frontend/src/pages/JobWizard.tsx`, `frontend/src/styles.css`

**Interfaces introduced (TypeScript, snake_case mirrors of the pydantic models):**

```ts
// types.ts
export interface CheckMember { job_id: string; path: string }
export interface ConsistencyCheckRequest { pairs?: CheckMember[]; sample?: number; min_coverage?: number; attributes?: boolean }
export type JoinFormKind = 'numeric' | 'prefixed_digits' | 'exact' | 'unknown';
export interface MemberResult { job_id: string; engine: 'postgres' | 'oracle' | 'mongo'; connection_id: string; object: string; path: string;
  entity_type: string | null; method: string | null; params_hash: string | null; form: JoinFormKind; width: number | null;
  prefix_len: number | null; found: number; anchor: boolean }
export interface PairResult { left: number; right: number; checked: number; matched: number; coverage: number; consistent: boolean }
export interface AttributeResult { entity_type: string; method: string; members: number[]; counts_toward_pass: boolean;
  compared: number; agreed: number; ambiguous: number; consistent: boolean }
export interface CheckProblem { code: string; member: number | null; pair: number[] | null }
export interface ConsistencyCheckResult { group_id: string; checked_at: string; sample_requested: number; sampled: number; anchor: number;
  members: MemberResult[]; pairs: PairResult[]; all_members_matched: number; attributes: AttributeResult[]; methods_agree: boolean;
  problems: CheckProblem[]; passed: boolean; consistent_pairs: number; total_pairs: number; min_coverage: number; duration_ms: number }
// JobGroup (M2 type) gains: last_check: ConsistencyCheckResult | null; last_check_at: string | null

// api.ts — inside api.groups
consistencyCheck: (auth: Auth, groupId: string, body: ConsistencyCheckRequest) =>
  call<ConsistencyCheckResult>(`/api/v1/job-groups/${encodeURIComponent(groupId)}/consistency-check`, auth,
                               { method: 'POST', body: JSON.stringify(body) }),

// lib/groups.ts (pure helpers)
export const JOIN_PREFERENCE = ['CUSTOMER_ID', 'ACCOUNT_NUMBER', 'EMPLOYEE_ID', 'CASE_ID', 'USERNAME', 'EMAIL'];
export const ENGINE_BADGE: Record<string, string> = { oracle: 'ORA', mongo: 'MDB', postgres: 'PG' };
export function sharedEntities(snapshots: (ClassificationSnapshot | null)[]): string[]
export function memberFields(snapshot: ClassificationSnapshot | null, shared: string[]): string[]  // column names in display order
export function formLabel(m: MemberResult): string            // 'number' | 'prefixed · 5-char prefix · 8 digits' | 'text'
export function problemText(p: CheckProblem, members: MemberResult[]): string
export function targetOf(job: Job): { connectionId: string; object: string }

// hooks/useJobGroup.ts
export interface GroupMember { job: Job; connection: Connection | null; snapshot: ClassificationSnapshot | null }
export interface GroupView { group: JobGroup; members: GroupMember[]; shared: string[] }
export function useJobGroup(auth: Auth, groupId: string | null): {
  view: GroupView | null; loading: boolean; error: string | null; checking: boolean;
  reload: () => void; runCheck: () => Promise<void>;
}
```

**Behaviours (built to mockup 07 "JOB GROUP" section and mockup 03 header/evidence):**
- Jobs page: a **Job groups** card under the jobs table. A group selector (`?group=<id>`, else the most
  recent group with members) and **New job group** (dialog). The selected group renders as in mockup 07:
  header "Job group · `<name>`" with the purpose of its first member ("Purpose CHG-48213 · UAT
  provisioning"), **Add a job to this group** (→ `/data-sources/jobs/new?group=<id>`) and **Run
  consistency check** (busy state while running; errors shown as a `callout` with the API `code`); the key
  node ("Key `k_…`" — "Held by the requesting team since <date>. DPS keeps no copy." — and the chip
  "entity-scoped: CUSTOMER_ID · PERSON_NAME · EMAIL · …" from the shared entities); connector wires (three
  members only); one system card per member (engine badge, connection name, state chip "running · 72 %" /
  "completed" / "failed", source object, "→ target", job id + rows written, field chips of its shared-entity
  columns); the verdict panel (chip passed/failed, "3 / 3 consistent", "500 values sampled from
  `<anchor object>` · checked <relative time>", one row per pair "Oracle ↔ MongoDB / `CUSTOMER_ID =
  customerId` / ✓ 500 / 500", problems in plain words, the sentence "The check reads the masked copies only.
  It needs no key and sees no original value."); and — in place of the mockup's sample of masked rows, which
  the contract's counts-only rule does not allow (Open contract question 3) — a **join coverage** table:
  per member the object, join column, join form ("number" / "prefixed · 5-char prefix · 8 digits"), found /
  sampled; per compared entity the method, agreed / compared, "informational" for anchor-dependent methods,
  and the callout "MongoDB stores the id with a 5-character prefix. Format-preserving encryption changes only
  the digits, so the token still joins to the numeric columns" whenever a `prefixed_digits` member joins a
  `numeric` one.
- Jobs table rows: a member of a group shows the key-icon `.grp` badge with the group name under its object.
- New group dialog: name (`^[a-z0-9][a-z0-9-]{1,62}$`), **Create** → shows the key card once: kid, seed
  fingerprint, **Download key (.dpskey)** and **Download seed (.dpsseed)** (JSON blobs built in memory with
  `URL.createObjectURL`, revoked after click), a required "I have stored the key and the seed" checkbox before
  **Done**; closing clears the material from React state. Nothing is ever written to `localStorage`.
- Job detail page of a grouped job: crumb "Data sources & jobs / Job group `<name>`"; subtitle appends
  "consistency key shared with `<other members' target objects>`"; header button **Open consistency check**
  (→ `/data-sources?group=<id>#group`); the evidence grid gains a "Cross-system joins" box: "3 / 3
  consistent" from `last_check`, "not checked" otherwise.
- Wizard: `?group=<id>` preselects that group in the consistency step (M2's control that sets
  `RunSpec.group_id`), so "Add a job to this group" lands on step 5 with the group chosen (the caller still
  supplies the group key — DPS keeps none).
- Polling: the group card re-reads member jobs every 2 s while any member is non-terminal (contract §14).
- Phone width (390 px): system cards stack, wires hidden, tables scroll inside their wrapper, no horizontal
  page scroll (mockup 07 media queries).

- [ ] **Step 1: Types and API** — add the interfaces above to `types.ts` and `consistencyCheck` to `api.groups`.

- [ ] **Step 2: `frontend/src/lib/groups.ts`**
```ts
import type { CheckProblem, ClassificationSnapshot, Job, MemberResult } from './types';

export const JOIN_PREFERENCE = ['CUSTOMER_ID', 'ACCOUNT_NUMBER', 'EMPLOYEE_ID', 'CASE_ID', 'USERNAME', 'EMAIL'];
export const ENGINE_BADGE: Record<string, string> = { oracle: 'ORA', mongo: 'MDB', postgres: 'PG' };
const ENGINE_NAME: Record<string, string> = { oracle: 'Oracle', mongo: 'MongoDB', postgres: 'PostgreSQL' };

function mapped(s: ClassificationSnapshot | null): Map<string, string> {
  const out = new Map<string, string>();
  if (!s) return out;
  const cols = [...s.columns].sort((a, b) => Number(b.is_key) - Number(a.is_key) || Number(b.anchor) - Number(a.anchor)
    || a.path.length - b.path.length || a.path.localeCompare(b.path));
  for (const c of cols) if (c.entity_type && c.method.method !== 'keep' && !out.has(c.entity_type)) out.set(c.entity_type, c.name);
  return out;
}

/** Entity types mapped (method ≠ keep) in at least two members, join-preference first. */
export function sharedEntities(snapshots: (ClassificationSnapshot | null)[]): string[] {
  const counts = new Map<string, number>();
  for (const s of snapshots) for (const e of mapped(s).keys()) counts.set(e, (counts.get(e) ?? 0) + 1);
  const shared = [...counts].filter(([, n]) => n >= 2).map(([e]) => e);
  const rank = (e: string): number => { const i = JOIN_PREFERENCE.indexOf(e); return i < 0 ? 99 : i; };
  return shared.sort((a, b) => rank(a) - rank(b) || a.localeCompare(b));
}

export function memberFields(snapshot: ClassificationSnapshot | null, shared: string[]): string[] {
  const m = mapped(snapshot);
  return shared.filter((e) => m.has(e)).map((e) => {
    const path = snapshot!.columns.find((c) => c.name === m.get(e))?.path ?? '';
    return path.replace(/^\$\./, '').replace(/\[\*\]/g, '[]');
  });
}

export function formLabel(m: MemberResult): string {
  if (m.form === 'numeric') return 'number';
  if (m.form === 'prefixed_digits') return `prefixed · ${m.prefix_len}-char prefix${m.width ? ` · ${m.width} digits` : ''}`;
  return m.form === 'exact' ? 'text' : 'unknown';
}

export function pairLabel(a: MemberResult, b: MemberResult): string {
  return `${ENGINE_NAME[a.engine] ?? a.engine} ↔ ${ENGINE_NAME[b.engine] ?? b.engine}`;
}

const PROBLEM: Record<string, string> = {
  method_mismatch: 'The join columns are not protected with the same method and parameters in every member.',
  join_form_mismatch: 'A member stores the join value in a form that cannot be compared (text against numbers).',
  field_scoped: 'A member used field-scoped tweaks, so its tokens cannot match the other systems.',
  kid_mismatch: 'A member was run with a different key than the group key.',
};

export function problemText(p: CheckProblem, members: MemberResult[]): string {
  const who = p.member !== null && members[p.member] ? `${members[p.member].object}: ` : '';
  return who + (PROBLEM[p.code] ?? p.code);
}

export function targetOf(job: Job): { connectionId: string; object: string } {
  const spec = job.spec as { source: { connection_id: string; object: { owner: string | null; name: string } };
    target: { connection_id: string | null; object: { owner: string | null; name: string } | null } };
  const obj = spec.target.object ?? spec.source.object;
  return { connectionId: spec.target.connection_id ?? spec.source.connection_id,
           object: obj.owner ? `${obj.owner}.${obj.name}` : obj.name };
}
```

- [ ] **Step 2b: Vitest cases** — `frontend/src/lib/groups.test.ts`: `sharedEntities` returns
  `['CUSTOMER_ID', 'EMAIL', 'PERSON_NAME']` for three snapshots that map those (and `[]` when only one member
  maps an entity); `keep` columns never count; `memberFields` returns `['customerId', 'profile.fullName']` for
  the Mongo snapshot (paths without `$.`, `[*]` shown as `[]`); `formLabel` gives `'number'`,
  `'prefixed · 5-char prefix · 8 digits'`, `'text'`; `problemText` maps `method_mismatch` to its sentence and
  prefixes the member's object; `targetOf` falls back to the source object for `in_place` specs.

- [ ] **Step 3: `frontend/src/hooks/useJobGroup.ts`** — loads `api.groups.get`, then
  `api.jobs.list({group_id, kind: 'run'})`, then (deduplicated, in parallel) `api.connections.get` for each
  member's source and target connection ids and `api.snapshots.get` for each `snapshot_id`; computes
  `shared = sharedEntities(...)`; `runCheck()` posts `{ sample: 500 }`, stores the result into
  `view.group.last_check`/`last_check_at` (no reload needed) and surfaces `ApiError` text + code in `error`;
  a 2-second interval re-reads `api.jobs.list` while any member state is non-terminal and clears itself on
  unmount or when all are terminal.

- [ ] **Step 4: Components** — `ConsistencyVerdict.tsx`:
```tsx
import type { ConsistencyCheckResult } from '../../lib/types';
import { pairLabel, problemText } from '../../lib/groups';

function ago(iso: string): string {
  const s = Math.max(0, Math.round((Date.now() - Date.parse(iso)) / 1000));
  return s < 60 ? `${s} s ago` : s < 3600 ? `${Math.round(s / 60)} min ago` : new Date(iso).toLocaleString();
}

export function ConsistencyVerdict({ result }: { result: ConsistencyCheckResult | null }): JSX.Element {
  if (!result) {
    return (<div className="verdict muted-verdict" aria-live="polite">
      <div className="faint sm vlabel">Cross-system consistency</div>
      <div className="big">—<span className="muted">not checked yet</span></div>
      <div className="faint sm">Run the check once every member has written rows.</div></div>);
  }
  const anchor = result.members[result.anchor];
  return (
    <div className={`verdict${result.passed ? '' : ' failed'}`} aria-live="polite">
      <div className="rowx" style={{ justifyContent: 'space-between' }}>
        <span className="faint sm vlabel">Cross-system consistency</span>
        <span className={`chip ${result.passed ? 'emerald' : 'rose'}`}>{result.passed ? 'passed' : 'failed'}</span>
      </div>
      <div className="big">{result.consistent_pairs} / {result.total_pairs}<span>consistent</span></div>
      <div className="faint sm">{result.sampled.toLocaleString()} values sampled from <span className="mono">{anchor?.object}</span> · checked {ago(result.checked_at)}</div>
      {result.pairs.map((p) => {
        const a = result.members[p.left]; const b = result.members[p.right];
        return (
          <div className="pair" key={`${p.left}-${p.right}`}>
            <span>{pairLabel(a, b)}</span>
            <span className="on">{a.path.replace(/^\$\./, '')} = {b.path.replace(/^\$\./, '')}</span>
            <span className={p.consistent ? 'ok' : 'ok bad'}><i>{p.consistent ? '✓' : '✗'}</i>{p.matched.toLocaleString()} / {p.checked.toLocaleString()}</span>
          </div>);
      })}
      {result.problems.map((p, i) => <div key={i} className="callout warn sm">{problemText(p, result.members)}</div>)}
      <div className="faint sm" style={{ marginTop: 8 }}>The check reads the masked copies only. It needs no key and sees no original value.</div>
    </div>);
}
```
`CoverageTable.tsx` renders two tables inside `.jwrap` (members: system badge + object, join column, form via
`formLabel`, "found / sampled"; attributes: entity chip, method (mono), "agreed / compared" with ✓/✗,
"informational" slate chip when `!counts_toward_pass`, "+N with several values" when `ambiguous > 0`) and the
prefixed-digits callout. `JobGroupCard.tsx` composes `.ghead`, `.keynode`, the `.wires` SVG (path
`M150 0 V13 M49 26 V13 H251 V26 M150 13 V26`, only for exactly three members), `.sys3` member cards and
`.gsplit` (verdict + coverage) exactly in mockup 07's structure and class names. `NewGroupDialog.tsx` is a
modal using the M0/M2 dialog styles with the behaviour above. `JobGroupsSection.tsx` owns the selector, the
dialog and the `id="group"` anchor.

- [ ] **Step 5: Pages** — `Jobs.tsx`: render `<JobGroupsSection auth={auth} />` after the jobs table and the
  `.grp` badge in rows with `job.group_id` (group names from one `api.groups.list` call). `JobDetail.tsx`:
  when `job.group_id`, load the group (`api.groups.get`), add the crumb, subtitle suffix, the header button
  and the "Cross-system joins" evidence box. `JobWizard.tsx`: `const [params] = useSearchParams();` and
  initialise the group selection from `params.get('group')`.

- [ ] **Step 6: Styles** — append to `styles.css` (Vault tokens only, no raw palette), porting from mockup 07
  each selector M2 did not already port: `.eb .src .obj .to .grp` and the group set `.ghead .keynode .ki .kt
  .ks .wires .sys3 .sys .sys .hd .flds .fl .gsplit .verdict .verdict.failed (rose border/gradient) .big .pair
  .pair .on .pair .ok .pair .ok.bad .jwrap .yes .vlabel`, plus the mockup's `@media (max-width: 1200px)`
  (`.gsplit` one column) and `@media (max-width: 980px)` (`.sys3` one column, `.wires` hidden) rules.

- [ ] **Step 7: Build gates**

Run: `cd frontend && npm run typecheck && npm test && npm run build` (`npm test` is M2's Vitest script)
Expected: no type errors; the `groups.test.ts` cases pass with M2's; `dist/` builds. (`frontend/dist` is not
committed.)

- [ ] **Step 8: Browser check against the mockups** — with the stack and demo data from Task 19 running (or
  after it), open `http://localhost:8400/data-sources?group=<id>` and `/data-sources/jobs/<oracle job id>`
  next to `docs/design/mockups/07-jobs.html` and `03-data-sources.html` at 1440 × 900 and 390 × 844, light
  and dark. Check: section order and class structure match; the three system cards and wires; verdict with 3
  pairs; coverage table in place of the masked sample; key chip text; no horizontal page scroll at 390 px; the
  `.grp` badge in the jobs table; the job-detail crumb, subtitle, button and joins box. Save screenshots under
  `docs/superpowers/build/m3-shots/` (synthetic seed data only) and note any intentional differences in
  `M3-DEVIATIONS.md`.

- [ ] **Step 9: Commit**

```bash
git add frontend/src/lib/groups.ts frontend/src/lib/groups.test.ts frontend/src/lib/types.ts frontend/src/lib/api.ts frontend/src/hooks/useJobGroup.ts frontend/src/components/groups frontend/src/pages/Jobs.tsx frontend/src/pages/JobDetail.tsx frontend/src/pages/JobWizard.tsx frontend/src/styles.css && git commit -m "feat(console): job groups, key node, member systems and the consistency verdict on Jobs and Job detail" -- frontend/src/lib/groups.ts frontend/src/lib/groups.test.ts frontend/src/lib/types.ts frontend/src/lib/api.ts frontend/src/hooks/useJobGroup.ts frontend/src/components/groups frontend/src/pages/Jobs.tsx frontend/src/pages/JobDetail.tsx frontend/src/pages/JobWizard.tsx frontend/src/styles.css
```
(screenshots are committed with Task 21's docs commit.)

---
### Task 19: End to end — the three-way consistency join through the API and worker; resume after kill on Oracle and Mongo

**Files:**
- Create: `tests/e2e/conftest.py` (`m3_secret_dir`, `m3_app`, `m3_worker`), `tests/e2e/test_m3_three_way.py`, `tests/e2e/test_m3_resume_after_kill.py`

**Behaviours proven (markers `integration`, `slow`, `db`, `pg_source`, `oracle`, `mongo`):**
1. The full M2 lifecycle works unchanged on Oracle and Mongo sources and targets: connection create + test →
   discover → snapshot accept → run in a job group → evidence.
2. Three jobs in one group (Oracle `CRM.CUSTOMERS` → `CRM_MASKED.CUSTOMERS_E2E`, Mongo `crm.profiles` →
   `crm_uat.profiles_e2e`, PG `bank.customers` → `bank_uat.customers_e2e`), all `table` mode with `create_if_missing`, the
   group key passed back, produce a consistency check that **passes 3 / 3** with coverage 1.0, and
   `PERSON_NAME`/`EMAIL` attributes agreeing for every compared customer.
3. A fourth group member whose snapshot override protects `CUSTOMER_ID` with `det_token` makes the check fail
   with `method_mismatch` — the check detects real drift.
4. A worker `SIGKILL`ed mid-run on Oracle and on Mongo (`in_place` and `append`) is finished by a second
   worker; the target equals a clean run row for row; the job's ledger rows are gone after finalize; the
   evidence pack records ≥ 1 resumed partition.
5. Worker logs never contain a seeded value.

- [ ] **Step 1: Fixtures** — `tests/e2e/conftest.py`:
```python
"""In-process API + subprocess workers for M3 end-to-end tests (real containers only)."""
from __future__ import annotations

import base64
import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dps.app import create_app
from tests.fixtures.m3_services import mongo_uri, oracle_dsn

SECRETS = {
    "m3/oracle-crm-ro": {"username": "dps_ro", "password": "dps_ro"},
    "m3/oracle-crm-masked": {"username": "crm_masked", "password": "crm_masked"},
    "m3/oracle-crm-owner": {"username": "crm", "password": "crm"},
    "m3/pg-bank": {"username": "postgres", "password": "postgres"},
}


@pytest.fixture(scope="session")
def m3_secret_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("secrets")
    for ref, files in SECRETS.items():
        d = root / ref
        d.mkdir(parents=True)
        for name, value in files.items():
            (d / name).write_text(value, encoding="utf-8")
        if ref.startswith("m3/oracle"):
            (d / "dsn").write_text(oracle_dsn(), encoding="utf-8")
    base = mongo_uri().split("/?")[0]
    for ref, db in (("m3/mongo-crm", "crm"), ("m3/mongo-crm-uat", "crm_uat")):
        (root / ref).mkdir(parents=True)
        (root / ref / "uri").write_text(f"{base}/{db}?directConnection=true", encoding="utf-8")
    return root


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture
def m3_settings(db_settings, m3_secret_dir: Path, kek: bytes):  # noqa: ANN001, ANN201
    return db_settings.model_copy(update={
        "secret_dir": str(m3_secret_dir), "job_kek": base64.b64encode(kek).decode(), "fpe_enabled": True,
        "inline_secrets_allowed": True, "auth_enabled": False, "llm_provider": "stub",
        "partition_lease_seconds": 6, "job_lease_seconds": 10, "job_reaper_interval_seconds": 2,
        "job_poll_interval_seconds": 1})


@pytest.fixture
def m3_app(m3_settings) -> Iterator[TestClient]:  # noqa: ANN001
    with TestClient(create_app(m3_settings)) as client:
        yield client


def worker_env(settings) -> dict[str, str]:  # noqa: ANN001
    env = {k: v for k, v in os.environ.items() if not k.startswith("DPS_")}
    for name in ("pg_host", "pg_port", "pg_database", "pg_schema", "pg_user", "pg_password", "pg_worker_user",
                 "pg_worker_password", "secret_dir", "job_kek", "partition_lease_seconds", "job_lease_seconds",
                 "job_reaper_interval_seconds", "job_poll_interval_seconds"):
        value = getattr(settings, name)
        env[f"DPS_{name.upper()}"] = value.get_secret_value() if hasattr(value, "get_secret_value") else str(value)
    env.update({"DPS_ENV": "local", "DPS_MIGRATIONS_MODE": "off", "DPS_FPE_ENABLED": "true",
                "DPS_INLINE_SECRETS_ALLOWED": "true", "DPS_LLM_PROVIDER": "stub", "DPS_WORKER_CONCURRENCY": "4",
                "DPS_WORKER_METRICS_PORT": str(_free_port()), "DPS_LOG_LEVEL": "INFO"})
    return env


@pytest.fixture
def m3_worker(m3_settings, tmp_path: Path):  # noqa: ANN001, ANN201
    """``start()`` launches ``python -m dps.worker``; every process is killed at teardown; logs kept."""
    procs: list[tuple[subprocess.Popen[bytes], Path]] = []

    class Workers:
        def start(self) -> subprocess.Popen[bytes]:
            log = tmp_path / f"worker-{len(procs)}.log"
            proc = subprocess.Popen([sys.executable, "-m", "dps.worker"], env=worker_env(m3_settings),
                                    stdout=log.open("wb"), stderr=subprocess.STDOUT)
            procs.append((proc, log))
            return proc

        def logs(self) -> str:
            return "".join(path.read_text(errors="replace") for _, path in procs)

    yield Workers()
    for proc, _ in procs:
        if proc.poll() is None:
            proc.kill()
            proc.wait(10)


def wait_job(client: TestClient, job_id: str, *, timeout: float = 600, until=("succeeded",)) -> dict:  # noqa: ANN001
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = client.get(f"/api/v1/jobs/{job_id}").json()
        if job["state"] in until:
            return job
        if job["state"] in ("failed", "dead", "canceled") and job["state"] not in until:
            raise AssertionError(f"job {job_id} ended {job['state']} code={job.get('error_code')}")
        time.sleep(1)
    raise AssertionError(f"job {job_id} did not reach {until} in {timeout}s")
```

- [ ] **Step 2: The three-way test** — `tests/e2e/test_m3_three_way.py`:
```python
"""Three systems, one key, one customer: the M3 demo as a test (spec §7 cross-system consistency)."""
from __future__ import annotations

import subprocess
import sys

import pytest

from tests.e2e.conftest import wait_job
from tools.seed_model import seed_customers
from tools.seed_mongo import seed_mongo
from tools.seed_oracle import seed_oracle
from tests.fixtures.m3_services import mongo_uri, oracle_dsn

pytestmark = [pytest.mark.integration, pytest.mark.slow, pytest.mark.db, pytest.mark.pg_source,
              pytest.mark.oracle, pytest.mark.mongo]
ROWS = 2400
PURPOSE = {"ticket": "CHG-48213", "legal_basis": "legitimate interest - UAT provisioning"}


def _connection(c, name: str, engine: str, *, ref: str | None = None, roles=("source",), **params) -> str:  # noqa: ANN001
    body = {"name": name, "engine": engine, "params": params, "roles": list(roles)}
    if ref:
        body["secret_ref"] = ref
    else:
        body["credentials"] = {"username": "postgres", "password": "postgres"}
    r = c.post("/api/v1/connections", json=body)
    assert r.status_code == 201, r.json().get("code")
    cid = r.json()["id"]
    test = c.post(f"/api/v1/connections/{cid}/test", json={"role": roles[-1]}).json()
    assert test["ok"], [k for k in test["checks"] if not k["ok"]]
    return cid


def _discover_override_accept(c, conn_id: str, obj: dict, overrides: list[dict]) -> str:  # noqa: ANN001
    """discover → PATCH overrides while the snapshot is proposed → accept; returns the snapshot id."""
    job = c.post("/api/v1/jobs/discover", json={"connection_id": conn_id, "objects": [obj]}).json()["job"]
    wait_job(c, job["id"])
    snaps = c.get("/api/v1/snapshots", params={"connection_id": conn_id, "status": "proposed"}).json()["items"]
    snap = next(s for s in snaps if s["job_id"] == job["id"])
    if overrides:
        assert c.patch(f"/api/v1/snapshots/{snap['id']}", json={"overrides": overrides}).status_code == 200
    assert c.post(f"/api/v1/snapshots/{snap['id']}/accept").status_code == 200
    return snap["id"]


def _discover_and_accept(c, conn_id: str, obj: dict) -> str:  # noqa: ANN001
    return _discover_override_accept(c, conn_id, obj, [])


def test_three_way_consistency_join(m3_app, m3_worker, source_pg) -> None:  # noqa: ANN001
    c = m3_app
    seed_oracle(oracle_dsn(), ROWS, 7)
    seed_mongo(mongo_uri(), ROWS, 7)
    subprocess.run([sys.executable, "tools/seed_sources.py", "--target", "postgres", "--rows", str(ROWS),
                    "--seed", "7"], check=True, capture_output=True)       # M2 seeding, same customer space
    m3_worker.start()
    ora_src = _connection(c, "e2e-ora-src", "oracle", ref="m3/oracle-crm-ro")
    ora_tgt = _connection(c, "e2e-ora-tgt", "oracle", ref="m3/oracle-crm-masked", roles=("target",))
    mdb_src = _connection(c, "e2e-mdb-src", "mongo", ref="m3/mongo-crm")
    mdb_tgt = _connection(c, "e2e-mdb-tgt", "mongo", ref="m3/mongo-crm-uat", roles=("target",))
    pg = _connection(c, "e2e-pg", "postgres", host="127.0.0.1", port=5442, database="bank",
                     roles=("source", "target"))
    sources = [(ora_src, {"owner": "CRM", "name": "CUSTOMERS"}, ora_tgt, {"owner": "CRM_MASKED", "name": "CUSTOMERS_E2E"}),
               (mdb_src, {"owner": None, "name": "profiles"}, mdb_tgt, {"owner": None, "name": "profiles_e2e"}),
               (pg, {"owner": "bank", "name": "customers"}, pg, {"owner": "bank_uat", "name": "customers_e2e"})]
    snaps = [_discover_and_accept(c, s, o) for s, o, _, _ in sources]
    created = c.post("/api/v1/job-groups", json={"name": "crm-uat-refresh-e2e"}).json()
    group, key = created["group"], created["key_material"]
    run_ids = []
    for (src, obj, tgt, tobj), snap in zip(sources, snaps, strict=True):
        body = {"mode": "obfuscate", "snapshot_id": snap, "group_id": group["id"], "purpose": PURPOSE,
                "source": {"connection_id": src, "object": obj, "parallelism": 3, "partition_rows": 1000,
                           "batch_rows": 250},
                "target": {"write_mode": "table", "connection_id": tgt, "object": tobj, "create_if_missing": True},
                "consistency": {"scope": "key", "key_material": key}}
        r = c.post("/api/v1/jobs/run", json=body)
        assert r.status_code == 202, r.json().get("code")
        run_ids.append(r.json()["job"]["id"])
    for jid in run_ids:
        job = wait_job(c, jid)
        assert job["progress"]["rows_written"] == ROWS and job["progress"]["partitions_total"] >= 2
    res = c.post(f"/api/v1/job-groups/{group['id']}/consistency-check", json={"sample": 500})
    assert res.status_code == 200
    check = res.json()
    assert check["passed"] and check["consistent_pairs"] == check["total_pairs"] == 3
    assert all(p["coverage"] == 1.0 and p["matched"] == 500 for p in check["pairs"])
    forms = {m["engine"]: m["form"] for m in check["members"]}
    assert forms == {"oracle": "numeric", "mongo": "prefixed_digits", "postgres": "numeric"}
    counted = {a["entity_type"]: a for a in check["attributes"] if a["counts_toward_pass"]}
    assert counted["PERSON_NAME"]["agreed"] == counted["PERSON_NAME"]["compared"] > 0
    assert counted["EMAIL"]["agreed"] == counted["EMAIL"]["compared"] > 0
    for jid in run_ids:
        pack = c.get("/api/v1/evidence", params={"job_id": jid}).json()["items"][0]
        assert pack["pack"]["verification"]["passed"] and pack["pack"]["consistency"]["kid"] == key["kid"]
    # drift: a fourth member protecting CUSTOMER_ID differently must fail the check
    drift_snap = _discover_override_accept(c, ora_src, {"owner": "CRM", "name": "CUSTOMERS"},
                                           [{"path": "$.CUSTOMER_ID", "method": {"method": "det_token"}}])
    drift_body = {"mode": "obfuscate", "snapshot_id": drift_snap, "group_id": group["id"], "purpose": PURPOSE,
                  "source": {"connection_id": ora_src, "object": {"owner": "CRM", "name": "CUSTOMERS"},
                             "parallelism": 2, "batch_rows": 250},
                  "target": {"write_mode": "table", "connection_id": ora_tgt, "create_if_missing": True,
                             "object": {"owner": "CRM_MASKED", "name": "CUSTOMERS_DRIFT"}},
                  "consistency": {"scope": "key", "key_material": key}}
    drift = c.post("/api/v1/jobs/run", json=drift_body).json()["job"]["id"]
    wait_job(c, drift)
    pairs = [{"job_id": run_ids[0], "path": "$.CUSTOMER_ID"}, {"job_id": drift, "path": "$.CUSTOMER_ID"}]
    bad = c.post(f"/api/v1/job-groups/{group['id']}/consistency-check", json={"pairs": pairs, "sample": 200}).json()
    assert bad["passed"] is False and any(p["code"] == "method_mismatch" for p in bad["problems"])
    logs = m3_worker.logs()
    for cust in list(seed_customers(30, 7)):
        assert cust.email not in logs and cust.full_name not in logs and str(cust.customer_id) not in logs
```
The det_token CUSTOMER_ID column in `CUSTOMERS_DRIFT` is created as text (M2 widens the created target),
so the drift run succeeds and only the check fails. The PG member is M2's `bank.customers` (target schema
`bank_uat`); its rows come from M2's seeding, which Task 15 points at `seed_model`.

- [ ] **Step 3: Resume after kill** — `tests/e2e/test_m3_resume_after_kill.py`, parametrised over
  `("oracle", "in_place")`, `("oracle", "append")`, `("mongo", "in_place")`, `("mongo", "append")`:
  1. create two identical scratch copies A and B of 3,000 seed customers (Oracle: tables `CUST_A`, `CUST_B`
     in the `oracle_schema` owner, `append` variants with an identity key; Mongo: collections `cust_a`,
     `cust_b` in `mongo_db`) and a connection over each owner (secret refs written into the test's secret
     dir);
  2. discover + accept both; create one group; start worker W1; `POST /jobs/run` for A (`batch_rows=50`,
     `partition_rows=1000`, `parallelism=3`, `confirm_write: true`, `expected_rows: 3000`, append →
     `pk_strategy: {"kind": "generate"}`);
  3. poll `GET /jobs/{id}/partitions` until some partition has `batch_no >= 3` while the job is not terminal,
     then `os.kill(W1.pid, signal.SIGKILL)`;
  4. start W2 (leases are 6 s, reaper 2 s); `wait_job(A)`; then run B to completion on W2 with the same key;
  5. assert: `in_place` — every row of A equals the same-key row of B (all columns); `append` — both hold
     6,000 rows and the multiset of non-key column tuples of the appended rows is equal; ledger rows for both
     job ids are 0 (`DPS_WRITE_LEDGER` / `dps_write_ledger`); A's evidence `counts.resumed_partitions >= 1`
     and `verification.passed`.

- [ ] **Step 4: Run** (stack from Task 15 plus the metadata DB)

```bash
docker compose --profile oracle --profile sources up -d dps-db source-pg oracle mongo
python tools/seed_sources.py --target all --rows 2400 --seed 7
DPS_TEST_REQUIRE_SERVICES=1 pytest -q -m slow tests/e2e/test_m3_three_way.py tests/e2e/test_m3_resume_after_kill.py
```
Expected: 1 + 4 passed (several minutes). A failure here is fixed in the M3 code it points to — never by
loosening an assertion — and logged.

- [ ] **Step 5: Commit**

```bash
git add tests/e2e/conftest.py tests/e2e/test_m3_three_way.py tests/e2e/test_m3_resume_after_kill.py && git commit -m "test(e2e): three-way consistency join through the API; resume after kill on Oracle and Mongo" -- tests/e2e/conftest.py tests/e2e/test_m3_three_way.py tests/e2e/test_m3_resume_after_kill.py
```

---

### Task 20: No values in logs — M3 log-scan test

**Files:**
- Create: `tests/test_no_values_in_logs_m3.py`

**Behaviours:** (contract §0.1) every logging call in the M3 modules uses a literal snake_case event and only
allow-listed fields; no M3 module formats an exception's text; `ConnectorError` is always built from a literal
code; and at run time, with sentinel values flowing through failing and succeeding Oracle, Mongo and
consistency-check paths, nothing sentinel-shaped reaches captured logs or raised errors.

- [ ] **Step 1: Write the test**
```python
"""M3 log scan: static AST checks plus a dynamic sentinel run (contract §0.1)."""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from dps.observability import _EVENT_NAME, _SAFE_FIELDS

ROOT = Path(__file__).resolve().parents[1]
M3 = ["dps/layers/connectors/oracle.py", "dps/layers/connectors/oracle_sql.py", "dps/layers/connectors/mongo.py",
      "dps/layers/connectors/mongo_docs.py", "dps/layers/connectors/docpaths.py",
      "dps/layers/connectors/consistency.py", "tools/seed_oracle.py", "tools/seed_mongo.py"]
LEVELS = {"debug", "info", "warning", "error", "exception", "critical"}


def _tree(rel: str) -> ast.Module:
    return ast.parse((ROOT / rel).read_text(encoding="utf-8"))


@pytest.mark.parametrize("rel", M3)
def test_log_calls_are_allow_listed(rel: str) -> None:
    for node in ast.walk(_tree(rel)):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in LEVELS
                and isinstance(node.func.value, ast.Name) and node.func.value.id == "logger"):
            assert node.args and isinstance(node.args[0], ast.Constant), f"{rel}:{node.lineno} event not literal"
            assert _EVENT_NAME.fullmatch(node.args[0].value), f"{rel}:{node.lineno}"
            bad = [k.arg for k in node.keywords if k.arg not in _SAFE_FIELDS]
            assert not bad, f"{rel}:{node.lineno} fields {bad}"


@pytest.mark.parametrize("rel", M3)
def test_no_exception_text_and_literal_error_codes(rel: str) -> None:
    src = (ROOT / rel).read_text(encoding="utf-8")
    assert not re.search(r"\b(str|repr)\(\s*exc\b", src) and "exc.message" not in src
    assert ".details" not in src or rel.endswith("mongo.py")          # mongo_code reads only the int code
    for node in ast.walk(_tree(rel)):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "ConnectorError":
            assert node.args and isinstance(node.args[0], (ast.Constant, ast.Name)), f"{rel}:{node.lineno}"
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "print":
            assert rel.startswith("tools/"), f"{rel}:{node.lineno} print in a service module"
```
plus `test_runtime_sentinels_never_reach_logs` (markers `integration`, `oracle`, `mongo`): configure
`configure_logging("DEBUG", json_output=True)`, then with `capsys`: (a) an `OracleConnector` whose password is
`SENTINEL-PW-1` fails to open; (b) an Oracle `CHAR` key table holding `SENTINEL-K1` is paged and an
`in_place` write with a stale ledger mark raises `ledger_conflict`; (c) a Mongo duplicate-key write of
`customerId = "SENTINEL-DUP"` raises; (d) the consistency check runs over fakes holding `SENTINEL-TOKEN-*`
values. Assert `"SENTINEL" not in capsys.readouterr().out + err` and not in any raised error's `str`/`repr`.

- [ ] **Step 2: Run** — `pytest -q tests/test_no_values_in_logs_m3.py && DPS_TEST_REQUIRE_SERVICES=1 pytest -q -m "oracle and mongo" tests/test_no_values_in_logs_m3.py` → pass.

- [ ] **Step 3: Commit**

```bash
git add tests/test_no_values_in_logs_m3.py && git commit -m "test: M3 log scan — allow-listed fields, no exception text, runtime sentinels" -- tests/test_no_values_in_logs_m3.py
```

---

### Task 21: Smoke test, benchmark, HANDOFF, README, API reference, final gates

**Files:**
- Modify: `tools/smoke_test.py` (`--m3`), `tools/bench_connector.py` (`--engine oracle|mongo`), `HANDOFF.md`, `README.md`, `docs/superpowers/build/M3-DEVIATIONS.md`
- Create: `docs/technical/api-m3.md`, `docs/superpowers/build/m3-shots/*.png` (from Task 18 Step 8)

- [ ] **Step 1: Smoke extension** — `python tools/smoke_test.py --m3` runs after the M0–M2 checks and
  requires the `oracle` and `sources` profiles plus seeded data. Checks, one verdict line each (counts only):
  `m3 connections` (create-or-reuse `smoke-ora-src` = `local/oracle-crm-ro`, `smoke-ora-tgt` =
  `local/oracle-crm-masked`, `smoke-mdb-src` = `local/mongo-crm`, `smoke-mdb-tgt` = `local/mongo-crm-uat`,
  `smoke-pg` = the connection M2's smoke already creates for `source-pg:5432/bank`; every `/test` ok), `m3 discover` (three discover jobs succeed;
  snapshots accepted), `m3 group` (group created; key returned once), `m3 runs` (three `table` runs into
  `CRM_MASKED.CUSTOMERS_SMOKE`, `crm_uat.profiles_smoke`, `bank_uat.customers_smoke` succeed within 600 s), `m3 consistency
  3/3` (`passed`, `consistent_pairs == 3`), `m3 evidence` (each pack verified), `m3 raw pii to llm`
  (`dps_llm_raw_pii_sent_total 0.0`). The check names, never values, are printed.
- [ ] **Step 2: Benchmark** — `python tools/bench_connector.py --engine oracle --rows 20000` and
  `--engine mongo --rows 20000` (read → `BatchTransformer` with the gdpr preset → `table` write into a scratch
  target; `batch_rows` 1000; one worker) print rows/s; record both numbers with the machine (12 CPUs, Docker
  29.5, Oracle Free 2.7 GB) in HANDOFF's "Measured throughput" table next to M2's PG figure.
- [ ] **Step 3: Docs** — `docs/technical/api-m3.md`: Oracle and Mongo `ConnectionSpec` examples (secret
  files `username`/`password`/`dsn`, Mongo `uri`; allowed options), the selection rules per engine (Oracle
  binds `:name`, Mongo operator allow-list and EJSON literals), engine-specific prepare failures
  (`transactions_unsupported`, `no_key` for ROWID tables in place, `column_unsupported`, `ledger_conflict`,
  `path_unsupported`), and the consistency-check route with request/response JSON. `HANDOFF.md`: services and
  ports, secret refs, the Oracle rules (pool options, NLS binary collation, FF9, CHAR binds, CLOB whole
  reads, ROWID partitions, `DPS_WRITE_LEDGER`), the Mongo rules (replica set + `directConnection`,
  transactions, `dps_write_ledger`, `comment` tagging, indexes not copied), FPE needed for cross-format id
  joins (`DPS_FPE_ENABLED=true` is the compose default), traps (Oracle first start 1–3 min; `docker compose
  down` resets Oracle; NVARCHAR2 keys run as one partition; `date_shift` keeps TIMESTAMP(9) digits),
  measured throughput. `README.md`: the M3 run commands (stack with profiles, seeding, smoke `--m3`).
- [ ] **Step 4: Final gates (the Definition of Done below)**, then commit and tag:

```bash
git add tools/smoke_test.py tools/bench_connector.py HANDOFF.md README.md docs/technical/api-m3.md docs/superpowers/build/M3-DEVIATIONS.md docs/superpowers/build/m3-shots && git commit -m "docs: M3 handoff, API reference, smoke and benchmark for Oracle and MongoDB" -- tools/smoke_test.py tools/bench_connector.py HANDOFF.md README.md docs/technical/api-m3.md docs/superpowers/build/M3-DEVIATIONS.md docs/superpowers/build/m3-shots
git tag -a v0.4.0-m3 -m "M3: Oracle and MongoDB connectors, cross-system job groups, consistency check"
```

---
## M0 / M2 touch points (contract §0.7 — each changed together with its tests)

| Module (owner) | Change | Task |
|---|---|---|
| `dps/config.py`, `dps/observability.py` (M0) | four M3 settings; allow-listed connector fields; two counters | 1 |
| `dps/engine/transform/methods.py` (M0) | `date_shift` keeps sub-microsecond digits (values with ≤ 6 digits unchanged) | 7 |
| `dps/layers/connectors/base.py` (M2) | `ColumnInfo.pseudo`; `ConnectorError.detail` attribute (if missing); `PrepareReport.append_upper_key`; `from_engine_text` keeps TIMESTAMP(7–9) text | 3, 4, 7, 12 |
| `dps/layers/connectors/selection.py` (M2) | Mongo allow-list validator + EJSON compiler; any Oracle-dialect gap the pinned tests expose | 10 |
| `dps/layers/connectors/registry.py` (M2) | the `oracle`/`mongo` branch replaces `engine_unavailable` | 8 |
| `dps/layers/connectors/discovery.py` (M2) | `column_values`/`forced_keep` | 8 |
| `dps/layers/connectors/jobs_run.py` (M2) | only if red: `job_id=` on `open_connector`, `clear_ledger` at finalize; same-table append reads bounded by `append_upper_key` as well as `append_upper` | 8, 12 |
| `dps/engine/batch.py` (M2) | only if Task 13's pins are red: untransformed values by reference, `[*]` expansion, unclassified detection | 13 |
| `dps/layers/connectors/postgres.py` (M2) | `lookup_values` | 16 |
| `dps/layers/api/routers/groups.py`, `JobGroup`, group repository (M2) | consistency-check route; `last_check`, `last_check_at` | 17 |
| `tools/seed_sources.py`, `tools/bench_connector.py`, `tools/smoke_test.py` (M2/M0) | oracle/mongo targets, PG customers from `seed_model`; `--engine`; `--m3` | 15, 21 |
| `frontend/src/pages/{Jobs,JobDetail,JobWizard}.tsx`, `lib/{api,types}.ts`, `styles.css` (M2) | group section, badge, header, joins box, `?group=` | 18 |
| `tests/connectors/conformance.py` registration (M2) | `oracle`, `mongo` harnesses (suite tests untouched) | 14 |
| `docs/superpowers/plans/2026-10-11-m1-m5-contract.md` | the additive changes listed in this plan's header | 3, 17, 21 |

## Definition of Done (M3)

All of the following, run on this Mac (Docker 29.5, Python 3.12 venv, Node 22), with output summaries pasted
into `docs/superpowers/build/M3-DEVIATIONS.md` under "Gates":

1. **Static and unit gates:** `ruff check dps tests tools && ruff format --check dps tests tools && mypy dps &&
   pytest -q` — green; `cd frontend && npm run typecheck && npm run build` — green.
2. **Stack:** `docker compose --profile oracle --profile sources up --build -d`, then poll until healthy (never
   `--wait`, contract §11): `app`, `worker`, `dps-db`, `source-pg`, `minio`, `oracle` (≤ 3 min), `mongo` healthy;
   `migrate` and `minio-init` exited 0; `curl -s localhost:8400/readyz` → `ready: true` with `db` and `jobs`
   ok.
3. **Seed:** `python tools/seed_sources.py --target all --rows 20000 --seed 7` → counts for the PG bank
   tables, `CRM.*`, `LOANS.APPLICANTS`, `crm.profiles`; a second run prints `unchanged`.
4. **Milestone integration gate (contract §12.4):** `DPS_TEST_REQUIRE_SERVICES=1 pytest -q -m "oracle or
   mongo"` — every test passes, including `test_oracle_clob_astral_roundtrip`, `test_oracle_char_key_paging`,
   `test_oracle_timestamp9_key_paging`, the engine bridge, the lookups, the three-way e2e and the four
   resume-after-kill cases.
5. **Conformance on all three engines:** `DPS_TEST_REQUIRE_SERVICES=1 pytest -q tests/connectors/conformance.py
   -m "pg_source or oracle or mongo"` — green for `postgres`, `oracle` and `mongo` (capability skips only:
   Mongo composite keys, ROWID cases off Oracle).
6. **M2 gate still green:** `DPS_TEST_REQUIRE_SERVICES=1 pytest -q -m "db or pg_source or s3"`.
7. **Smoke:** `python tools/smoke_test.py` (M0–M2 checks) and `python tools/smoke_test.py --m3` — all pass;
   `m3 consistency 3/3` among them; `/metrics` shows `dps_llm_raw_pii_sent_total 0.0`.
8. **Browser check against the mockups:** Task 18 Step 8 — `/data-sources?group=<id>` vs
   `docs/design/mockups/07-jobs.html` (job-group section) and `/data-sources/jobs/<id>` vs
   `03-data-sources.html` (crumb, subtitle, "Open consistency check", "Cross-system joins" box), at 1440 px and
   390 px, light and dark; the verdict shows **3 / 3 consistent** for the demo group; screenshots committed
   under `docs/superpowers/build/m3-shots/`; intentional differences (coverage table instead of masked sample
   rows; `NOTES` instead of `RM_NOTES`) logged.
9. **Benchmarks recorded:** Oracle and Mongo rows/s in `HANDOFF.md`.
10. **Docs:** `HANDOFF.md` (services, ports, secret ref names, traps, throughput), `README.md`,
    `docs/technical/api-m3.md`, the contract file's additive changes, `M3-DEVIATIONS.md` complete; tag
    `v0.4.0-m3`.

## Self-review against the spec

| Spec | Requirement | Where |
|---|---|---|
| §7 engine notes — Oracle | thin mode; Wega pool rules (`expire_time=1`, `call_timeout`, `tcp_connect_timeout`, module/action/client_identifier) | Task 4 |
| | raw cursor `arraysize = prefetchrows = batch` | Task 5 `_fetch` |
| | CLOB offsets are UTF-16 units; CHAR needs fixed-length binds; TIMESTAMP beyond µs paged carefully — measured bugs carried in as tests | Task 7 (three named tests) + Task 3 rules |
| | never `FOR UPDATE`; `FETCH FIRST` only on non-locking reads | Task 3 tests over every statement; Task 5 no-lock test |
| | keyset by PK or ROWID ranges | Task 3 codec + Task 5 describe/read |
| | `MERGE` for upsert, `executemany` loads, in-place `UPDATE` by PK | Task 6 |
| §7 engine notes — Mongo | keyset on `_id`; documents through the L1 JSON engine (nested paths, arrays); `bulk_write(ordered=False)`; in place by `_id`; append with new `_id` | Tasks 9, 11, 12, 13 (motor → pymongo async per contract §1) |
| §7 selection | Mongo filters JSON-validated against an operator allow-list, `$where` rejected; Oracle statements parsed, single read-only SELECT, identifiers validated | Task 10 |
| §7 discovery | Oracle `SAMPLE`, Mongo `$sample`; snapshot per column/path | Tasks 5, 8, 11 |
| §7 execution | partitions from key quantiles, ROWID chunking, checkpoints, resume, idempotent writes | Tasks 6, 12, 14, 19 |
| §7 write modes (D3) | `in_place`, `append` (generate/offset/token + collision pre-check), `table`, (`files` stays M2's, engine-agnostic) on both engines; confirm/admin rules are M2's engine-agnostic validation | Tasks 6, 12, 19 |
| §7 cross-system consistency | one key across a job group, entity-scoped tweaks (`field_scoped=false`), the three-way join demonstrated | Tasks 13, 16, 17, 18, 19 |
| §7 credentials | `secret_ref` files in production, inline only in dev; least-privilege `test-connection` checks | Tasks 2, 5, 11 |
| §7 throughput | measured, not promised | Task 21 |
| §9 console | job groups in Data sources & jobs; consistency check visible | Task 18 |
| §10 | no values in logs/metrics/errors; connector credentials never echoed; Mongo/Oracle error messages never read | Tasks 4, 11, 20 |
| §11 M3 | local Oracle Free + Mongo in compose; cross-system job groups; three Oracle pitfalls as tests; **exit: conformance suite green on real containers** | Tasks 2, 7, 14, DoD 4–5 |

Gaps, stated plainly: Mongo secondary indexes are not copied to created targets (the spec marks index copy
optional; the prepare report says `indexes_not_copied`); NVARCHAR2/NCHAR keys run as a single partition;
TIMESTAMP WITH LOCAL TIME ZONE, XMLTYPE, LONG, BFILE and object columns are refused for copies
(`column_unsupported`) and must be projected out; `query` selections are refused on key-less (ROWID) tables;
anchor-dependent methods (`date_shift`, persona surrogates) agree across systems only when the anchor id is
stored in the same form everywhere (reported as informational attributes).

## Open contract questions

The plan follows the contract as written; these are the places where the author believes it is wrong,
incomplete or in tension with the mockups:

1. **Motor vs pymongo async.** The spec says `motor`; the contract (§1) says pymongo ≥ 4.13
   `AsyncMongoClient`. Followed the contract (Motor is deprecated upstream). The spec text should be updated.
2. **`oracledb.defaults.fetch_lobs = False` (§4.2).** Implemented per cursor through an output type handler
   with the same effect, plus `IS JSON` columns kept as text (python-oracledb otherwise returns a dict — a
   fourth Wega-measured pitfall the contract does not list). A process-global default would also change
   every other module's Oracle reads. Suggest rewording §4.2 to "CLOB/NCLOB fetched as str (per-cursor type
   handler)".
3. **Counts-only check vs mockup 07.** §6.5 says the check "returns overlap counts only"; mockup 07 shows five
   masked join rows (tokens and masked names). Followed the contract: the console shows a counts-only join
   coverage table. If the owner wants examples, add `examples: ≤ 5` returning masked target values only
   (data the caller can already read from the target) — a contract change.
4. **Anchors across formats (§6.2).** The anchor text is `ENTITY:hash(to_engine_text(value))`, so Oracle
   `10000123` and Mongo `"CUST-10000123"` produce different anchors: `date_shift` and persona surrogates then
   differ across systems for the same customer. Proposal: snapshots record the join form of an anchor column
   and the anchor canonicalises numeric/prefixed-digit ids to their integer text. M3 reports such attributes
   as informational instead of failing the check.
5. **`fpe_digits` on integer columns.** FF1 can emit a leading zero (`03104482`) that a NUMBER/bigint column
   stores as `3104482`. The join still works (M3 compares integers), but reveal of that stored value runs FF1
   over 7 digits and returns the wrong original. Proposal (M0/M2): cycle-walk FF1 for integer-kind columns so
   a leading zero never appears when the input had none, or zero-pad to the column precision before reveal;
   meanwhile the evidence `reveal_map` should mark such columns `reveal: false`.
6. **Mongo transactions.** §4.2 says `in_place`/`append(generate)` need transactions; every `append`
   strategy uses the write ledger (same §4.2 rule), so M3 requires a replica set for all `append`
   strategies. Suggest "in_place and append need transactions".
7. **`$options` is not on the Mongo allow-list (§5.2).** Case-insensitive matches must use the inline `(?i)`
   flag. Suggest allowing `$options` restricted to `^[imsx]{0,4}$` next to `$regex`.
8. **Non-contract M2 internals M3 depends on.** The M2 plan (written in parallel) fixes `ResolvedTarget`
   (`write_columns`, `generated_key`, `target_shape`, `column_types`, …), `PrepareReport` (`checks`,
   `append_upper`), `ConnectorError(code, detail, *, retryable)`, `KeyRange` holding *encoded* keys (the
   contract's §4.1 says `Key`), `Connector.key_at_offset`, `sample_keys(..., upper=)`,
   `BatchWriter.clear_ledger`, `open_connector(..., kek, job_id, credentials)` and `Credentials`. M3 follows
   them; they should be folded into contract §4/§5 so M4/M5 do not rediscover them. Still unfixed and assumed
   by M3: `POST /job-groups` → `{group, key_material, synthesis_seed}`, the conformance harness interface,
   and how the run handler applies `append_upper`.
9. **New error codes** used by M3 and not in §5.3: `column_unsupported`, `column_missing`, `target_missing`,
   `ledger_conflict`, `path_unsupported`, `engine_unavailable`, `connection_invalid`, `database_required`,
   `object_invalid`, `object_not_found`, and selection codes `selection_param_missing`,
   `selection_query_needs_key`, `selection_invalid`, `selection_too_complex`,
   `selection_regex_too_long`, `selection_operator_denied` (the last is in the contract). They are added to
   the contract's tables in Task 21.
10. **Job groups do not pin a policy.** Cross-system agreement needs the same method and parameters per entity
    in every member; nothing enforces it at run time, the check detects drift afterwards (`method_mismatch`).
    Proposal: optional `policy` on `POST /job-groups` (stored as name + hash in a 02x migration) with
    `validate_run` refusing a member whose resolved policy hash differs (`group_policy_mismatch`).
11. **Demo secret files.** Local secret refs live in `docker/secrets/local/` as committed demo literals (the
    same literals contract §11 already puts in compose). Confirm that committing them is acceptable; the
    alternative is generating them in a one-shot init container.
12. **Mongo `ObjectRef.owner = None`** means "the connection's database", so a target in another database
    needs its own connection (the demo uses `mongo-crm-uat`). Consider allowing `owner` = database for Mongo.
13. **Same-table append bounds for non-integer keys.** M2's `PrepareReport.append_upper` is an `int`; a Mongo
    collection keyed by ObjectId needs a key-typed bound, so M3 adds `append_upper_key: EncodedKey | None`
    and asks M2's run handler to apply it like `append_upper`. Same-table `append` on a key-less Oracle table
    is refused (new rows can take ROWIDs below existing ones and would be read again).
14. **`ResolvedTarget.column_types` is engine-specific.** M2 computes PostgreSQL DDL types; Oracle targets
    need Oracle types (M3's `osql.oracle_ddl_type`) and Mongo has none. Suggest the contract say
    `column_types` is computed by the target engine's connector (or keyed by engine).
15. **Identity keys and same-table append.** `GENERATED BY DEFAULT AS IDENTITY` lets earlier explicit inserts
    run ahead of the identity sequence; appended rows could then collide (`ORA-00001`) or fall below
    `append_upper`. M3 documents it in HANDOFF; a DBA-facing pre-check (`identity next value > MAX(key)`)
    could be added to `prepare`.

## Execution

Plan complete and saved to `docs/superpowers/plans/2026-10-11-m3-oracle-and-mongodb.md`. Two execution options:

1. **Subagent-driven (recommended)** — a fresh subagent per task, review between tasks; the Oracle lane
   (Tasks 3–8) and the Mongo lane (Tasks 9–12) can run in parallel after Task 2, joined by Task 13
   (`superpowers:subagent-driven-development`).
2. **Inline execution** — tasks in order with checkpoints (`superpowers:executing-plans`).
