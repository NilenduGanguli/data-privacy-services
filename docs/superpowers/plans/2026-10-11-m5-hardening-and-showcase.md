# DPS M5 — Hardening + showcase — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship milestone M5 of Data Privacy Services: the Gaussian-copula tabular engine (with an optional MOSTLY AI engine) behind L2 synthesize jobs, measured fidelity and privacy in every synthetic evidence pack, the `external_tokenize` adapter to a bank tokenization service, DB-backed API keys with rotation and expiry, tenants with quotas and retention, the retention job, the Reports & audit, evidence-PDF, Admin and Policies console pages, a tightened production posture, OpenShift manifests for every role, a complete HANDOFF, a ten-minute demo that runs itself end to end, a security review pass and the `v1.0.0` release tag.

**Architecture:** M5 adds no new process. The copula engine (`dps/engine/synth/copula.py`, NumPy/SciPy, ours) is fitted by the worker in the run job's `prepare` phase on a bounded sample, previewed through the real `BatchTransformer` on that sample, measured (`dps/engine/metrics.py`) and gated *before* anything is written; the fitted model is sealed into `JobSecret.tabular_model` and purged with the job key. Partitions draw one synthetic tabular row per source row from a keyed BLAKE2b stream over the row anchor, so output is deterministic and resumable; exact-row matches are rejected in flight. API keys, tenants, quotas, policy versions and report roll-ups live in four new migrations (040–043) under the M2 RLS model; housekeeping (retention, roll-ups) runs as the contract's `retention` job kind. The console gains four pages built to mockups 01, 05, 09, 10 and 11. Deployment is the Wega kustomize layout: one image, four roles, an external metadata database.

**Tech Stack:** Python 3.12, FastAPI, pydantic v2, asyncpg, NumPy ≥ 2.0, SciPy ≥ 1.13 (`tabular` extra), reportlab (M1 `docs` extra), httpx, cryptography; optional `mostlyai[local]` + pandas (`synth-ml` extra, never installed by default); React 18 + TypeScript + Vite (no new npm dependency); kustomize 5.8, kubeconform, Playwright 1.55.0 (Python) for the console check; Docker compose for the local stack.

**Spec:** `docs/specs/2026-10-11-data-privacy-services-design.md` — §5.6 (tabular shape), §5.5 (`external_tokenize`), §5.7 (fidelity/privacy), §9 (console, demo), §10 (posture), §11 M5 row, D2, D7, D9, D11, D12. **Contract (binding):** `docs/superpowers/plans/2026-10-11-m1-m5-contract.md`. **M0 plan (interfaces):** `docs/superpowers/plans/2026-10-11-m0-foundation-engine-api-playground.md`.

**Contract change (made by the tasks named, each in the same commit as its code, contract rule "a plan may add"):**
§1 new M5 paths and the `tabular`/`synth-ml` extras' exact pins (Task 1) · §2.2 the contents of 040–042 and a new `043_policies.sql` (Tasks 9, 10, 12, 15) · §3.3 `JobProgress.metrics` and `jobs.reseal_secret` (Task 6) · §5.1 `ConnectionParams.options` allow-list gains `protocol` (Task 16) · §5.3 new codes `daily_row_quota_exceeded`, `privacy_gate`, `tls_required`, `host_forbidden`, `policy_not_found` (Tasks 6, 10, 15, 16, 17) · §6.6 `TabularSampler` members and two defaulted `BatchTransformer` kwargs (Task 5) · §7.1/§7.3 additive evidence keys, the `holdout` kwarg of `nndr` and five helper metrics (Tasks 2, 6) · §9 new routes and query parameters (Tasks 11–15) · §10 settings additions (Task 1) · §11 `tokenizer-mock` service (Task 8) · §12.1 markers `deploy` and `ml` (Task 1) · policy references `@name[:vN]` accepted wherever a policy is (Task 15). Nothing in the contract is contradicted; every addition is listed verbatim in the task that makes it.

---

## Conventions for every task

- Work in `/Users/neelu/document_intelligence/data-privacy-services` (its own git repo; never run git against the parent `~/document_intelligence`). Venv: `source .venv/bin/activate`; Task 1 installs `.[dev,fpe,docs,db,connectors,tabular,scanner]`.
- **Unit gate after every task:** `ruff check dps tests tools && ruff format --check dps tests tools && mypy dps && pytest -q` (unit tests are offline; `db`, `pg_source`, `s3`, `oracle`, `mongo`, `browser`, `ocr`, `deploy`, `ml` tests skip unless their service/tool is reachable, contract §12.2). When `frontend/` changes: `cd frontend && npm run typecheck && npm run build`.
- **DB tests on the host without Docker:** `tools/scratch_pg.sh start` (M2) gives Homebrew PG16 on 5441; then `pytest -q -m db`.
- Type hints on every function, Google-style docstrings on public ones, line length 100, no `Any` unless unavoidable.
- **Never log, print, return in an error, store in a job row, evidence pack, audit row, metric label or artefact name: a customer value, a key/DEK/seed, a raw API key (except the one-time create/rotate response and the operator CLI's stdout), a token-to-original mapping, a fitted model.** The structlog allow-list in `dps/observability.py` is extended, never bypassed (Task 1 adds the M5 names).
- **Git:** `git add <paths> && git commit -m "<conventional message>" -- <paths>` with explicit paths only; never `git add -A/.`, `commit -a`, `stash`, `reset`, `checkout --`, `clean`. If `.git/index.lock` exists, wait 3 s and retry (≤ 10 times). No `Co-Authored-By` or any other trailer. Author = the configured global identity.
- **HANDOFF.md** changes in the same commit as any build/run/deploy/config change (contract §0.6). Tasks that change config append their rows; Task 27 finalises the file.
- Every new tenant table: `tenant_id text NOT NULL`, RLS `ENABLE` + `FORCE` + policy `tenant_isolation`, explicit `GRANT` to `dps_app_rw`, an entry in `dps/db.py::TENANT_TABLES` (contract §2.2).
- **Interface drift rule:** this plan builds on M0 (as planned) and on M1–M4 *as fixed by the contract*. Task 0 turns that surface into a test. If a name the contract fixes is missing, stop and report; never re-invent it in M5. Where this plan says "M2 touch point" it names the contract function to edit and gives the exact code to add.

## M0 touch points (changed with their tests, contract rule 7)

| Module | Change | Task |
|---|---|---|
| `dps/config.py` | M5 settings; secret fields `repr=False` | 1, 17 |
| `dps/observability.py` | `_SAFE_FIELDS` += M5 names; counters `dps_tabular_resamples_total`, `dps_external_token_calls_total`, `dps_auth_keys_loaded` | 1 |
| `dps/posture.py` | static production checks (KEK/API keys/tokens from `_FILE`, TLS egress, CORS, embedded worker, docs off) + `posture_report()` for the Admin page | 16 |
| `dps/engine/policy.py` | `TabularConstraint`, `WhenRule`, `WhenCondition`; `SynthesizeConfig.constraints`, `.quasi_identifiers` (preset `config_hash()` values change once — new defaulted fields; nothing pins a literal hash) | 4 |
| `dps/engine/transform/methods.py` | `TransformContext.external`; `external_tokenize` apply/reverse read the batch cache | 8 |
| `dps/engine/transform/apply.py` | `ApplyOptions.tokenizer`; tokenize/detokenize pre-pass | 8 |
| `dps/engine/planner.py` | `build_plan(..., external_enabled=True)` marks `external:unconfigured` | 8 |
| `dps/engine/service.py` | `PrivacyEngine(tokenizer=...)`; `ExternalServiceError` | 8 |
| `dps/engine/detect/custom.py` | `PatternSpecModel` runs `regex_guard.check_pattern` | 17 |
| `dps/auth.py` | `StaticKeyStore.principals()`; `require_scope` tags its dependency (`__dps_scope__`) | 9, 17 |
| `dps/layers/api/common.py` | `ExternalServiceError` → 502 | 8 |
| `dps/layers/api/routers/{detect,transform,policies}.py` | engine calls via `asyncio.to_thread`; `@name` policy refs | 8, 15 |
| `dps/app.py` | `ChainKeyStore`, key refresher, CORS origins from settings, `/docs` off in prod, scrubbed 422 handler, `no-store` on `/api/`, `tokenizer` readiness, routers `admin`, `reports`, `policy_store` | 8, 9, 11, 12, 15, 16, 17 |
| `Dockerfile`, `docker-compose.yml`, `tools/smoke_test.py` | group-0 permissions for arbitrary UIDs; `tokenizer-mock`; M5 smoke checks | 24 |
| `frontend/src/{App.tsx,components/Layout.tsx,lib/api.ts,lib/types.ts,pages/Overview.tsx}` | routes, version, API groups, Overview to mockup 01 | 18, 19 |

## M1–M4 touch points (contract names; code given in the task)

| Module (owner) | Change | Task |
|---|---|---|
| `dps/db.py` (M2) | `TENANT_TABLES` += `dps_tenant_usage`, `dps_report_daily`, `dps_report_entity_daily`, `dps_report_refresh`, `dps_policy`, `dps_policy_version` | 10, 12, 15 |
| `dps/jobs.py` (M2) | `reseal_secret()`; usage upsert inside `checkpoint()`; daily-quota predicate in `claim_partitions` | 6, 10 |
| `dps/jobs_models.py` (M2) | `JobProgress.metrics`; `validate_run` checks the enabled tabular engines | 6 |
| `dps/engine/batch.py` (M2) | tabular sampling, constraints, exact-row rejection | 5 |
| `dps/layers/connectors/jobs_run.py` (M2) | `prepare` fits/gates/seals; `run_partition` loads the sampler | 6 |
| `dps/layers/connectors/evidence.py` (M2) | additive pack keys (columns detail, fidelity/privacy/tabular, policy version) | 6 |
| `dps/layers/api/routers/{jobs,connections}.py` (M2) | daily quota at accept; `@name` refs; TLS-only connections | 10, 15, 16 |
| `dps/worker.py` (M2) | `retention` handler + `RetentionScheduler` | 13 |
| `dps/layers/api/routers/{documents,schema}.py` (M1) | `@name` refs | 15 |
| `frontend/src/pages/Policies.tsx` (M1) | completed to mockup 05 incl. versions | 23 |
| `frontend/src/pages/EvidencePack.tsx` (M2) | synthetic section, PDF download | 21 |
| `dps/layers/api/routers/scans.py`, `dps/layers/scanner/crawler.py` (M4) | `netguard` URL checks; Chromium resolver allow-list | 17 |

## File structure (what M5 creates)

| Path | Responsibility |
|---|---|
| `tests/test_contract_surface.py` | M0–M4 names this plan builds on exist (Task 0) |
| `dps/engine/metrics.py` | KS, TVD, correlation deltas, null-rate delta, row hashes/digests, exact-row matches, feature encoding, NNDR, QI uniqueness, fidelity report |
| `dps/engine/synth/copula.py` | `GaussianCopula`: marginals, copula fit, keyed sampling, reject set, sealed-safe serialisation |
| `dps/engine/synth/constraints.py` | `ConstraintSet`: unique (keyed Feistel), ranges, positivity, dependent-field rules |
| `dps/engine/synth/mostly.py` | optional MOSTLY AI pool sampler (`synth-ml` extra) |
| `dps/layers/connectors/tabular.py` | column eligibility, holdout split, fit + preview + measure + gate for `prepare`; sampler loader for partitions |
| `dps/engine/transform/external.py` | `ExternalTokenizer` protocol, `HttpTokenizer`, `build_tokenizer` |
| `docker/tokenizer-mock/server.py` | stateless (vaultless) mock of a bank tokenization service, stdlib + cryptography |
| `dps/auth_db.py` | `DbKeyStore`, `ChainKeyStore`, create/list/rotate/disable/enable, no-escalation grant check |
| `dps/keys_cli.py` | `python -m dps.keys_cli create|list|rotate|disable` (operator bootstrap) |
| `dps/tenants.py` | tenant CRUD, effective settings, usage, daily quota check, tenant directory |
| `dps/audit_query.py` | audit search across granted tenants, layer mapping (shared with roll-ups), CSV export with formula guard |
| `dps/retention.py` | `retention_job` handler, `RetentionScheduler` |
| `dps/reports.py` | roll-up refresh (audit + jobs + partitions + scans), summary/jobs/evidence/api-roll-up queries |
| `dps/layers/reports/__init__.py`, `dps/layers/reports/pdf.py` | evidence-pack and API-roll-up PDFs (reportlab, escaped, ASCII-safe) |
| `dps/policy_store.py` | tenant policies, drafts, immutable versions, diffs, `@name[:vN]` resolution |
| `dps/netguard.py` | URL/host checks (SSRF), origin normalisation, TLS-only connection rules, Chromium resolver rules |
| `dps/rekey.py` | `python -m dps.rekey` — re-seal every sealed blob with the current KEK |
| `dps/engine/detect/regex_guard.py` | static ReDoS guard for policy-supplied patterns |
| `dps/layers/api/validation.py` | 422 handler that never echoes request input |
| `dps/layers/api/policy_refs.py` | `@name[:vN]` resolution for routers; audit policy labels |
| `dps/layers/api/routers/{admin,reports,policy_store}.py` | §9 M5 routes |
| `dps/migrations/040_tenant_quotas.sql`, `041_report_rollups.sql`, `042_key_rotation.sql`, `043_policies.sql` | DDL |
| `frontend/src/components/{BarChart,ConfirmDialog,KeyOnceCard,DownloadButton,Kpi}.tsx` | shared console pieces |
| `frontend/src/pages/{Reports,Admin}.tsx` (new), `Overview.tsx`, `EvidencePack.tsx`, `Policies.tsx` (completed) | mockups 09, 11, 01, 10, 05 |
| `deploy/openshift/{base,components/{network-policies,monitoring,dev-mocks},overlays/{dev,prod}}/**` | kustomize, Wega layout |
| `tools/demo_client.py`, `tools/demo_seed.py`, `tools/e2e_demo.py`, `tools/flow_report.py`, `tools/settings_table.py` | shared HTTP client, demo seed, automated demo, route-coverage report, HANDOFF config table |
| `docs/demo/DEMO-SCRIPT.md`, `docs/security/2026-10-11-m5-security-review.md`, `docs/technical/api-m5.md`, `HANDOFF.md`, `README.md`, `CHANGELOG.md` | documentation |
| `tests/test_{contract_surface,config_m5,metrics,copula,constraints,batch_tabular,tabular_prepare,mostly,external_tokenize,auth_db,keys_cli,tenants,quota,admin_api,reports,retention,evidence_pdf,policy_diff,policy_store,posture_m5,egress_tls,rekey,handoff}.py`, `tests/security/*.py`, `tests/test_no_values_in_logs_m5.py`, `tests/integration/test_synthesize_copula.py`, `tests/deploy/test_openshift.py`, `tests/console/test_console_pages.py` | tests |

---

### Task 0: Preflight — the M0–M4 surface this plan builds on

**Files:**
- Create: `tests/test_contract_surface.py`

The test imports every name M5 calls from earlier milestones (as fixed by the contract) and fails with a list of what is missing. It is permanent: it guards the contract against drift.

- [ ] **Step 1: Write the test**

`tests/test_contract_surface.py`:
```python
"""The M0–M4 interfaces M5 builds on (contract §1–§13). A failure lists every missing name."""
from __future__ import annotations

import importlib
import inspect

import pytest

SURFACE: dict[str, tuple[str, ...]] = {
    "dps.config": ("Settings", "get_settings"),
    "dps.posture": ("posture_problems", "assert_production_posture", "PostureError"),
    "dps.auth": ("Principal", "StaticKeyStore", "KeyStore", "hash_key", "new_api_key", "require_scope",
                 "SCOPES", "LOCAL_PRINCIPAL", "authenticate", "resolve_tenant"),
    "dps.audit": ("AuditEvent", "MemoryAuditSink", "LogAuditSink", "DbAuditSink"),
    "dps.engine.policy": ("Policy", "MethodSpec", "SynthesizeConfig", "resolve_policy", "load_preset",
                          "list_presets", "METHOD_NAMES"),
    "dps.engine.transform.methods": ("TransformContext", "run_method", "reverse_method", "MethodError", "METHODS"),
    "dps.engine.transform.apply": ("ApplyOptions", "apply_plan", "reveal", "RevealHint", "anchor_for"),
    "dps.engine.planner": ("build_plan", "explain_plan"),
    "dps.engine.service": ("PrivacyEngine", "EngineError", "VerificationFailed", "Payload", "RequestOptions"),
    "dps.engine.synth.surrogates": ("SynthesisSeed", "seed_bytes", "seed_from_caller", "generate_synthesis_seed"),
    "dps.engine.synth.personas": ("seed_int",),
    "dps.engine.findings": ("hash_value",),
    "dps.layers.api.common": ("run_engine",),
    "dps.db": ("init_pool", "init_worker_pool", "close_pools", "acquire", "acquire_queue",
               "run_migrations", "verify_migrations", "assert_rls_posture", "db_configured", "TENANT_TABLES"),
    "dps.ids": ("new_id",),
    "dps.secrets": ("load_kek", "seal", "open_sealed", "resolve_secret_ref"),
    "dps.storage": ("ObjectStore", "ArtefactService", "artefact_store"),
    "dps.jobs": ("JobKind", "JobState", "JobPhase", "PartitionState", "Job", "ClaimedJob", "ClaimedPartition",
                 "create_job", "get_job", "list_jobs", "claim_jobs", "claim_partitions", "advance_job",
                 "checkpoint", "finish_partition", "queue_stats", "worker_id"),
    "dps.worker": ("Consumer", "HandlerContext"),
    "dps.jobs_models": ("RunSpec", "RunOptions", "JobSecret", "JobProgress", "RetentionSpec", "ConnectionSpec",
                        "TargetSpec", "SpecError", "validate_run"),
    "dps.engine.batch": ("BatchTransformer", "ColumnPlan", "BatchResult", "TabularSampler", "LeakageError"),
    "dps.layers.connectors.base": ("ObjectRef", "ColumnInfo", "TableShape", "Connector", "Row", "to_engine_text",
                                   "from_engine_text"),
    "dps.layers.connectors.selection": ("validate_selection", "compile_where", "quote_ident", "SelectionError"),
    "dps.layers.connectors.evidence": ("EvidencePack",),
    "dps.layers.connectors.jobs_run": ("prepare", "run_partition", "finalize"),
    "dps.layers.connectors.registry": ("open_connector",),
    "dps.layers.api.errors": ("DpsHttpError",),
    "dps.layers.scanner": ("scan_job",),
}

# Names the contract places in a section rather than a file: they must exist in ONE of these
# modules. M5 imports them from the first module listed; if Task 0 finds them only in the second,
# change the M5 import lines that name them (Tasks 6, 17) to the second module.
EITHER: dict[str, tuple[str, str]] = {
    "ClassificationSnapshot": ("dps.jobs_models", "dps.layers.connectors.discovery"),
    "ColumnClassification": ("dps.jobs_models", "dps.layers.connectors.discovery"),
    "Selection": ("dps.jobs_models", "dps.layers.connectors.selection"),
}

# Signature facts M5 relies on (contract §6.4, §3.2, §2.1).
PARAMS: dict[tuple[str, str], tuple[str, ...]] = {
    ("dps.engine.batch", "BatchTransformer"): ("engine", "policy", "mode", "columns", "shape", "key", "seed",
                                               "locale", "unclassified", "tabular"),
    ("dps.jobs", "create_job"): ("tenant_id", "kind", "phase", "spec", "sealed_secret", "purpose", "created_by"),
    ("dps.jobs", "advance_job"): ("worker_id", "job_id", "phase", "state", "progress", "error_code"),
}


def _missing() -> list[str]:
    missing: list[str] = []
    for module_name, names in SURFACE.items():
        try:
            module = importlib.import_module(module_name)
        except ImportError:
            missing.append(f"{module_name} (module)")
            continue
        missing += [f"{module_name}.{n}" for n in names if not hasattr(module, n)]
    for name, modules in EITHER.items():
        found = False
        for module_name in modules:
            try:
                found = found or hasattr(importlib.import_module(module_name), name)
            except ImportError:
                continue
        if not found:
            missing.append(f"{name} (in {' or '.join(modules)})")
    for (module_name, name), params in PARAMS.items():
        try:
            target = getattr(importlib.import_module(module_name), name)
        except (ImportError, AttributeError):
            continue
        sig = inspect.signature(target)
        missing += [f"{module_name}.{name}({p}=)" for p in params if p not in sig.parameters]
    return missing


def test_m0_to_m4_surface_exists() -> None:
    missing = _missing()
    assert missing == [], "contract surface missing: " + ", ".join(missing)


def test_job_models_carry_the_m5_hooks() -> None:
    models = importlib.import_module("dps.jobs_models")
    assert "tabular" in models.RunOptions.model_fields
    assert models.RunOptions.model_fields["tabular"].default == "none"
    assert "tabular_model" in models.JobSecret.model_fields


def test_routes_m5_extends_exist() -> None:
    from dps.app import create_app
    from dps.config import Settings

    app = create_app(Settings(_env_file=None, env="local", auth_enabled=False, llm_provider="stub"))
    paths = {getattr(r, "path", "") for r in app.routes}
    for path in ("/api/v1/evidence/{evidence_id}", "/api/v1/jobs/run", "/api/v1/connections",
                 "/api/v1/policies/test-recognizer", "/api/v1/entities", "/api/v1/scans"):
        if path not in paths:
            pytest.fail(f"route {path} missing (path parameter names may differ — read the router)")
```

- [ ] **Step 2: Run it**

Run: `pytest -q tests/test_contract_surface.py`
Expected: `3 passed`. If the evidence route's parameter is named differently (e.g. `{id}`), read `dps/layers/api/routers/evidence.py` and correct only the literal in the test. Any other failure means an earlier milestone is incomplete: stop and report the printed names; do not implement M1–M4 surface here.

- [ ] **Step 3: Commit**

```bash
git add tests/test_contract_surface.py && git commit -m "test: guard the M0-M4 contract surface M5 builds on" -- tests/test_contract_surface.py
```

---

### Task 1: M5 settings, extras, markers, log fields

**Files:**
- Modify: `pyproject.toml`, `dps/config.py`, `dps/observability.py`, `.env.example`, `HANDOFF.md`, `docs/superpowers/plans/2026-10-11-m1-m5-contract.md` (§1 extras, §10, §12.1)
- Test: `tests/test_config_m5.py`, `tests/test_observability.py` (extend)

- [ ] **Step 1: Write the failing tests**

`tests/test_config_m5.py`:
```python
from __future__ import annotations

import pytest

from dps.config import Settings


def test_m5_defaults() -> None:
    s = Settings(_env_file=None)
    assert s.synth_tabular_engine == "copula" and s.tabular_engines == ["copula"]
    assert (s.copula_fit_rows, s.copula_holdout_rows, s.copula_metrics_rows) == (100_000, 20_000, 5_000)
    assert s.copula_rare_floor == 0.01 and s.copula_max_resample == 8
    assert s.privacy_nndr_target == 0.95 and s.privacy_nndr_enforce is False
    assert s.fidelity_column_limit == 0.05 and s.fidelity_corr_limit == 0.10
    assert s.key_cache_seconds == 30 and s.report_pdf_enabled is True
    assert s.key_max_ttl_days == 365 and s.key_rotation_max_age_days == 90
    assert s.tokenizer_provider == "" and s.tokenizer_batch == 500
    assert s.egress_tls_required is False and s.cors_origins == "*"
    assert s.mostly_pool_rows == 20_000 and s.rollup_retention_days == 400


@pytest.mark.parametrize("value,expected", [("copula", ["copula"]), ("copula,mostly", ["copula", "mostly"]),
                                            ("", [])])
def test_tabular_engine_list(value: str, expected: list[str]) -> None:
    assert Settings(_env_file=None, synth_tabular_engine=value).tabular_engines == expected


@pytest.mark.parametrize("field,value", [("synth_tabular_engine", "ctgan"), ("tokenizer_provider", "voltage"),
                                         ("tokenizer_url", "ftp://x"), ("copula_rare_floor", 0.5),
                                         ("copula_max_resample", 0)])
def test_m5_settings_are_validated(field: str, value: object) -> None:
    with pytest.raises(ValueError):
        Settings(_env_file=None, **{field: value})


def test_cors_origin_list() -> None:
    assert Settings(_env_file=None, cors_origins="").cors_origin_list == []
    assert Settings(_env_file=None, cors_origins="https://a.example, https://b.example").cors_origin_list == [
        "https://a.example", "https://b.example"]
```

Append to `tests/test_observability.py`:
```python
def test_m5_log_fields_are_allowed() -> None:
    from dps.observability import _SAFE_FIELDS

    assert {"job_id", "engine", "columns", "rows", "resamples", "adapter", "deleted", "tenants",
            "artefact_id", "bytes", "gate", "version"} <= _SAFE_FIELDS
    assert not ({"value", "token", "dek", "seed", "model", "raw_key", "password"} & _SAFE_FIELDS)
```

- [ ] **Step 2: Run to confirm failure**

Run: `pytest -q tests/test_config_m5.py tests/test_observability.py`
Expected: FAIL with `AttributeError`/`ValidationError` on the unknown M5 fields.

- [ ] **Step 3: Add the extras and markers to `pyproject.toml`**

In `[project.optional-dependencies]` add (exact pins are floors; the lock is the image):
```toml
tabular = ["numpy>=2.0", "scipy>=1.13"]
synth-ml = ["mostlyai[local]>=4.5", "pandas>=2.2"]   # optional engine; never in the default image
```
In `[tool.pytest.ini_options].markers` add:
```toml
    "deploy: needs the kustomize binary (renders deploy/openshift)",
    "ml: needs the optional [synth-ml] extra (MOSTLY AI SDK)",
```
Install: `uv pip install -e ".[dev,fpe,docs,db,connectors,tabular,scanner]"` → resolves; `python -c "import numpy, scipy"` prints nothing.

- [ ] **Step 4: Add the settings to `dps/config.py`**

Inside `class Settings`, after the M4 block (field names are the contract §10 M5 row plus the additions listed in Step 6):
```python
    # --- M5: tabular synthesis -------------------------------------------------------------
    synth_tabular_engine: str = "copula"     # comma list of enabled engines: copula | mostly; "" = off
    copula_fit_rows: int = 100_000           # rows fitted at prepare (contract §6.6)
    copula_holdout_rows: int = 20_000        # extra rows held out to measure privacy (never fitted)
    copula_metrics_rows: int = 5_000         # preview rows run through the real transformer
    copula_rare_floor: float = 0.01          # categories below this share are folded away
    copula_max_resample: int = 8             # exact-row rejections per row before failing closed
    fidelity_column_limit: float = 0.05      # KS/TVD per column shown as "within limit"
    fidelity_corr_limit: float = 0.10        # correlation-delta limit shown in the evidence
    privacy_nndr_target: float = 0.95        # NNDR ratio at or above which privacy reads "pass"
    privacy_nndr_enforce: bool = False       # true: a ratio below target fails prepare (privacy_gate)
    mostly_pool_rows: int = 20_000           # rows the MOSTLY engine generates once per job
    mostly_max_training_minutes: int = 10
    # --- M5: external tokenization (spec §5.5) ---------------------------------------------
    tokenizer_provider: str = ""             # "" (off) | http
    tokenizer_url: str = ""
    tokenizer_token: str = ""                # bearer token; _FILE in production
    tokenizer_ca_file: str = ""
    tokenizer_timeout_seconds: float = 10.0
    tokenizer_batch: int = 500
    tokenizer_retries: int = 2
    # --- M5: keys, reports, posture -------------------------------------------------------
    key_cache_seconds: int = 30
    key_max_ttl_days: int = 365
    key_rotation_max_age_days: int = 90
    key_rotation_max_grace_hours: int = 168
    report_pdf_enabled: bool = True
    rollup_retention_days: int = 400
    egress_tls_required: bool = False        # posture: true in staging/prod
    cors_origins: str = "*"                  # posture: never "*" in staging/prod; "" = same-origin only
```
Validators and properties (add next to the M0 validators):
```python
    @field_validator("synth_tabular_engine")
    @classmethod
    def _valid_tabular(cls, v: str) -> str:
        engines = [p.strip().lower() for p in v.split(",") if p.strip()]
        unknown = [e for e in engines if e not in ("copula", "mostly")]
        if unknown:
            raise ValueError("synth_tabular_engine accepts copula and mostly only")
        return ",".join(engines)

    @field_validator("tokenizer_provider")
    @classmethod
    def _valid_tokenizer(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in ("", "http"):
            raise ValueError("tokenizer_provider must be '' or 'http'")
        return v

    @field_validator("copula_rare_floor")
    @classmethod
    def _rare_floor(cls, v: float) -> float:
        if not 0.0 <= v <= 0.2:
            raise ValueError("copula_rare_floor must be within [0, 0.2]")
        return v

    @field_validator("copula_max_resample", "tokenizer_batch", "key_cache_seconds")
    @classmethod
    def _positive(cls, v: int) -> int:
        if v < 1:
            raise ValueError("must be at least 1")
        return v

    @property
    def tabular_engines(self) -> list[str]:
        return [p for p in self.synth_tabular_engine.split(",") if p]

    @property
    def cors_origin_list(self) -> list[str]:
        return [p.strip() for p in self.cors_origins.split(",") if p.strip()]
```
`tokenizer_url` reuses M0's URL validator: change its decorator from `@field_validator("stellar_base_url", "coin_url")` to `@field_validator("stellar_base_url", "coin_url", "tokenizer_url")` (http/https with a host; the message never echoes the URL — M0 deviation log, Task 2).

- [ ] **Step 5: Extend the log allow-list in `dps/observability.py`**

Add to `_SAFE_FIELDS` (each is a count, an id, a name or a flag):
```python
        # M5: jobs, tabular synthesis, adapters, retention, keys
        "job_id", "partition_no", "engine", "columns", "rows", "resamples", "adapter", "deleted",
        "tenants", "artefact_id", "bytes", "gate", "kind", "phase", "key_count", "grace_hours",
```
(`"version"` is already present.) Add metrics after `VERIFY_FAIL`:
```python
TABULAR_RESAMPLES = Counter("dps_tabular_resamples_total", "Synthetic rows resampled to avoid an exact real row")
EXTERNAL_CALLS = Counter("dps_external_token_calls_total", "Calls to the tokenization service", ["op", "outcome"])
KEYS_LOADED = Gauge("dps_auth_keys_loaded", "Active DB-backed API keys in this process's snapshot")
```
(import `Gauge` from `prometheus_client`).

- [ ] **Step 6: Contract amendments (same commit)**

In the contract: §1 extras sentence becomes "`tabular` (M5: `numpy>=2.0`, `scipy>=1.13`), `synth-ml` (M5: `mostlyai[local]>=4.5`, `pandas>=2.2`; never in an image)"; §1 table gains the M5 rows of this plan's file-structure table (paths only); §10 gains one M5 row per field added in Step 4 that is not already listed (`copula_holdout_rows`, `copula_metrics_rows`, `copula_max_resample`, `fidelity_column_limit`, `fidelity_corr_limit`, `privacy_nndr_target`, `privacy_nndr_enforce`, `mostly_pool_rows`, `mostly_max_training_minutes`, `tokenizer_*`, `key_max_ttl_days`, `key_rotation_max_age_days`, `key_rotation_max_grace_hours`, `rollup_retention_days`, `egress_tls_required`, `cors_origins`) with the defaults above, and "`synth_tabular_engine` = comma list of enabled engines"; §12.1 table gains `deploy` (kustomize binary, M5) and `ml` (`synth-ml` extra, M5).

Append to `.env.example`:
```bash
# M5 — tabular synthesis engines enabled here (copula | copula,mostly | "" off)
DPS_SYNTH_TABULAR_ENGINE=copula
# M5 — bank tokenization service (external_tokenize); the local mock runs with --profile full
DPS_TOKENIZER_PROVIDER=
DPS_TOKENIZER_URL=
DPS_TOKENIZER_TOKEN_FILE=
```
Append the same variables to `HANDOFF.md` §3 (configuration table) with their defaults.

- [ ] **Step 7: Run tests, lint, types**

Run: `pytest -q && ruff check dps tests tools && ruff format --check dps tests tools && mypy dps`
Expected: all green; `tests/test_config_m5.py` → `10 passed`.

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml dps/config.py dps/observability.py .env.example HANDOFF.md tests/test_config_m5.py tests/test_observability.py docs/superpowers/plans/2026-10-11-m1-m5-contract.md
git commit -m "feat: M5 settings, tabular and synth-ml extras, deploy/ml markers, log allow-list" -- pyproject.toml dps/config.py dps/observability.py .env.example HANDOFF.md tests/test_config_m5.py tests/test_observability.py docs/superpowers/plans/2026-10-11-m1-m5-contract.md
```

---

### Task 2: Fidelity and privacy metrics

**Files:**
- Create: `dps/engine/metrics.py`
- Test: `tests/test_metrics.py`

Public interface (contract §7.3, plus the additive helpers and the `holdout` kwarg):
```python
def to_float(value: Any) -> float | None
def canonical(value: Any) -> str | bool | None
def ks_statistic(real: Sequence[Any], synth: Sequence[Any]) -> float
def total_variation(real: Sequence[str | None], synth: Sequence[str | None]) -> float
def correlation_delta(real: Mapping[str, Sequence[Any]], synth: Mapping[str, Sequence[Any]]) -> float
def correlation_matrix_delta(real, synth) -> tuple[list[str], list[list[float]]]
def null_rate_delta(real: Sequence[Any], synth: Sequence[Any]) -> float
def row_hash(row: Mapping[str, Any], cols: Sequence[str]) -> str                       # in-memory only
def row_digest(row: Mapping[str, Any], cols: Sequence[str], key: bytes) -> int        # keyed, sealable
def exact_row_matches(real_hashes: set[str], synth_rows: Iterable[Mapping[str, Any]], cols: list[str]) -> int
def encode_features(columns, kinds, reference=None) -> np.ndarray
def nndr(real: np.ndarray, synth: np.ndarray, *, sample: int = 2000, seed: int = 0,
         holdout: np.ndarray | None = None) -> dict[str, float]
def qi_uniqueness(rows: Iterable[Mapping[str, Any]], quasi_identifiers: list[str]) -> float
def qi_unique_overlap(real_rows, synth_rows, quasi_identifiers: list[str]) -> int
def generalize_qi(value: Any, how: str) -> str | None       # how: decade | band10 | prefix3 | exact
def fidelity_report(real, synth, kinds, *, column_limit: float, corr_limit: float) -> dict[str, Any]
```

- [ ] **Step 1: Write the failing tests**

`tests/test_metrics.py`:
```python
from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal

import numpy as np
import pytest

pytest.importorskip("scipy")

from dps.engine.metrics import (  # noqa: E402
    canonical,
    correlation_delta,
    correlation_matrix_delta,
    encode_features,
    exact_row_matches,
    fidelity_report,
    generalize_qi,
    ks_statistic,
    nndr,
    null_rate_delta,
    qi_unique_overlap,
    qi_uniqueness,
    row_digest,
    row_hash,
    to_float,
    total_variation,
)

RNG = np.random.default_rng(7)


def test_to_float_and_canonical_agree_across_types() -> None:
    assert to_float(True) == 1.0 and to_float(Decimal("2.50")) == 2.5 and to_float(None) is None
    assert to_float(date(2026, 1, 2)) == float(date(2026, 1, 2).toordinal())
    assert to_float(datetime(1970, 1, 1, 0, 0, 10)) == 10.0
    assert canonical(1) == canonical(1.0) == canonical(Decimal("1.00")) == "1"
    assert canonical(Decimal("100")) == "100" and canonical(None) is None and canonical(True) is True


def test_ks_is_zero_for_identical_and_one_for_disjoint() -> None:
    a = RNG.normal(size=500).tolist()
    assert ks_statistic(a, a) == 0.0
    assert ks_statistic([1, 2, 3], [10, 11, 12]) == 1.0
    assert 0.0 < ks_statistic(a, (RNG.normal(size=500) + 0.3).tolist()) < 0.5
    assert ks_statistic([], []) == 0.0 and ks_statistic([1.0], []) == 1.0


def test_tvd_and_null_rate_delta() -> None:
    assert total_variation(["a", "b"], ["b", "a"]) == 0.0
    assert total_variation(["a", "a"], ["b", "b"]) == 1.0
    assert total_variation(["a", "b", None], ["a", "a"]) == pytest.approx(0.5)
    assert null_rate_delta([1, None, None, 4], [1, 2, 3, None]) == pytest.approx(-0.25)


def test_correlation_deltas() -> None:
    x = RNG.normal(size=400)
    real = {"$.A": x.tolist(), "$.B": (x * 2 + RNG.normal(scale=0.1, size=400)).tolist()}
    same = {"$.A": real["$.A"], "$.B": real["$.B"]}
    broken = {"$.A": real["$.A"], "$.B": RNG.permutation(real["$.B"]).tolist()}
    assert correlation_delta(real, same) == pytest.approx(0.0)
    assert correlation_delta(real, broken) > 0.8
    names, matrix = correlation_matrix_delta(real, broken)
    assert names == ["$.A", "$.B"] and matrix[0][0] == 0.0 and matrix[1][0] == matrix[0][1] > 0.8


def test_row_hash_digest_and_exact_matches() -> None:
    cols = ["A", "B"]
    real = [{"A": 1, "B": "x"}, {"A": 2, "B": "y"}]
    hashes = {row_hash(r, cols) for r in real}
    assert exact_row_matches(hashes, [{"A": Decimal("1.0"), "B": "x"}, {"A": 3, "B": "x"}], cols) == 1
    key = b"k" * 32
    assert row_digest(real[0], cols, key) == row_digest({"A": 1.0, "B": "x"}, cols, key)
    assert row_digest(real[0], cols, key) != row_digest(real[0], cols, b"j" * 32)
    assert 0 <= row_digest(real[0], cols, key) < 2**64


def test_encode_features_scales_and_one_hots() -> None:
    cols = {"$.N": [0, 50, 100, None], "$.C": ["a", "b", "a", None]}
    x = encode_features(cols, {"$.N": "int", "$.C": "categorical"})
    # columns sorted: $.C block (a, b, other, null) / √2, then $.N (scaled, null indicator)
    assert x.shape == (4, 6) and np.all((x >= 0) & (x <= 1))
    assert x[3, 3] == pytest.approx(2 ** -0.5) and x[3, 5] == 1.0 and x[2, 4] == 1.0


def test_nndr_detects_copies_and_accepts_fresh_draws() -> None:
    real = RNG.normal(size=(2000, 3))
    holdout = RNG.normal(size=(500, 3))
    fresh = RNG.normal(size=(500, 3))
    copies = real[:500].copy()
    good = nndr(real, fresh, seed=1, holdout=holdout)
    bad = nndr(real, copies, seed=1, holdout=holdout)
    assert 0.8 < good["ratio"] < 1.25 and good["holdout"] == 1.0
    assert bad["ratio"] < 0.05 and bad["zero_distance_share"] == 1.0
    loo = nndr(real, fresh, seed=1)
    assert loo["holdout"] == 0.0 and loo["ratio"] > 0.5
    assert nndr(real[:1], fresh, seed=1)["measured"] == 0.0


def test_qi_uniqueness_overlap_and_generalisation() -> None:
    rows = [{"G": "F", "Z": "560"}, {"G": "F", "Z": "560"}, {"G": "M", "Z": "400"}]
    assert qi_uniqueness(rows, ["G", "Z"]) == pytest.approx(1 / 3)
    assert qi_uniqueness(rows, []) == 0.0
    synth = [{"G": "M", "Z": "400"}, {"G": "X", "Z": "999"}]
    assert qi_unique_overlap(rows, synth, ["G", "Z"]) == 1
    assert generalize_qi(date(1987, 5, 1), "decade") == "1980s"
    assert generalize_qi(43, "band10") == "40-49" and generalize_qi("560034", "prefix3") == "560"
    assert generalize_qi(None, "exact") is None


def test_fidelity_report_shape_and_score() -> None:
    x = RNG.normal(size=300)
    real = {"$.N": x.tolist(), "$.M": (x + 1).tolist(), "$.C": ["alpha", "beta", "gamma"] * 100}
    rep = fidelity_report(real, real, {"$.N": "float", "$.M": "float", "$.C": "categorical"},
                          column_limit=0.05, corr_limit=0.1)
    assert rep["score"] == 1.0 and rep["corr_delta"] == 0.0
    assert set(rep["ks"]) == {"$.N", "$.M"} and set(rep["tvd"]) == {"$.C"}
    assert all(c["within_limit"] for c in rep["columns"]) and rep["corr_matrix"]["columns"] == ["$.M", "$.N"]
    assert "alpha" not in json.dumps(rep)  # numbers and paths only
```

- [ ] **Step 2: Run to confirm failure**

Run: `pytest -q tests/test_metrics.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'dps.engine.metrics'`.

- [ ] **Step 3: Write `dps/engine/metrics.py`**

```python
"""Synthetic-data fidelity and privacy metrics (spec §5.7, contract §7.3).

Inputs are bounded in-memory samples held by the worker during ``prepare``; every function
returns numbers (and column paths) only. Nothing here logs, persists or returns a value.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import math
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import numpy as np
from scipy import stats

NUMERIC_KINDS = frozenset({"int", "decimal", "float", "date", "timestamp"})
_REFERENCE_MAX = 10_000
_CHUNK = 512
_TOP_CATEGORIES = 20


def to_float(value: Any) -> float | None:
    """Numeric view: numbers as-is, bools 0/1, dates as ordinal days, timestamps as epoch seconds
    (a naive timestamp is read as UTC). Anything else is ``None``."""
    if value is None:
        return None
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, int | float | Decimal):
        out = float(value)
        return out if math.isfinite(out) else None
    if isinstance(value, datetime):
        return (value if value.tzinfo else value.replace(tzinfo=UTC)).timestamp()
    if isinstance(value, date):
        return float(value.toordinal())
    return None


def canonical(value: Any) -> str | bool | None:
    """A comparable form: 1 == 1.0 == Decimal("1.00") → "1"; dates ISO; bytes hex."""
    if value is None or isinstance(value, bool | str):
        return value
    if isinstance(value, int | float | Decimal):
        try:
            number = value if isinstance(value, Decimal) else Decimal(str(value))
            return format(number.normalize(), "f")
        except InvalidOperation:
            return str(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, bytes | bytearray | memoryview):
        return bytes(value).hex()
    return str(value)


def _numbers(values: Sequence[Any]) -> np.ndarray:
    return np.asarray([x for v in values if (x := to_float(v)) is not None], dtype=float)


def ks_statistic(real: Sequence[Any], synth: Sequence[Any]) -> float:
    """Two-sample Kolmogorov–Smirnov statistic on the non-null numeric views."""
    r, s = _numbers(real), _numbers(synth)
    if r.size == 0 or s.size == 0:
        return 0.0 if r.size == s.size == 0 else 1.0
    return float(stats.ks_2samp(r, s).statistic)


def total_variation(real: Sequence[str | None], synth: Sequence[str | None]) -> float:
    """½ Σ |p_real − p_synth| over the non-null categories."""
    rc = Counter(v for v in real if v is not None)
    sc = Counter(v for v in synth if v is not None)
    rn, sn = sum(rc.values()), sum(sc.values())
    if rn == 0 or sn == 0:
        return 0.0 if rn == sn == 0 else 1.0
    return 0.5 * sum(abs(rc[k] / rn - sc[k] / sn) for k in set(rc) | set(sc))


def null_rate_delta(real: Sequence[Any], synth: Sequence[Any]) -> float:
    """Synthetic null rate minus real null rate (signed)."""
    def rate(values: Sequence[Any]) -> float:
        return sum(1 for v in values if v is None) / len(values) if values else 0.0
    return rate(synth) - rate(real)


def _spearman(data: Mapping[str, Sequence[Any]], names: list[str]) -> np.ndarray:
    cols = [np.asarray([np.nan if (x := to_float(v)) is None else x for v in data[n]], float)
            for n in names]
    out = np.eye(len(names))
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            both = np.isfinite(cols[i]) & np.isfinite(cols[j])
            a, b = cols[i][both], cols[j][both]
            rho = 0.0
            if a.size >= 3 and np.ptp(a) > 0 and np.ptp(b) > 0:
                rho = float(stats.spearmanr(a, b).statistic)
            out[i, j] = out[j, i] = 0.0 if math.isnan(rho) else rho
    return out


def correlation_matrix_delta(real: Mapping[str, Sequence[Any]],
                             synth: Mapping[str, Sequence[Any]]) -> tuple[list[str], list[list[float]]]:
    """|Spearman(real) − Spearman(synth)| over the columns both share, rounded to 4 places."""
    names = sorted(set(real) & set(synth))
    if len(names) < 2:
        return names, [[0.0] * len(names) for _ in names]
    delta = np.abs(_spearman(real, names) - _spearman(synth, names))
    return names, [[round(float(v), 4) for v in row] for row in delta]


def correlation_delta(real: Mapping[str, Sequence[Any]], synth: Mapping[str, Sequence[Any]]) -> float:
    """Mean absolute Spearman-correlation difference over distinct column pairs."""
    names, matrix = correlation_matrix_delta(real, synth)
    pairs = [matrix[i][j] for i in range(len(names)) for j in range(i)]
    return float(np.mean(pairs)) if pairs else 0.0


def _row_payload(row: Mapping[str, Any], cols: Sequence[str]) -> bytes:
    return json.dumps([canonical(row.get(c)) for c in cols], separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def row_hash(row: Mapping[str, Any], cols: Sequence[str]) -> str:
    """Unkeyed sha256 of the canonical row — in-memory comparisons only, never persisted."""
    return hashlib.sha256(_row_payload(row, cols)).hexdigest()


def row_digest(row: Mapping[str, Any], cols: Sequence[str], key: bytes) -> int:
    """Keyed 64-bit digest (HMAC-SHA256) — the form the sealed copula reject set stores."""
    return int.from_bytes(hmac.new(key, _row_payload(row, cols), hashlib.sha256).digest()[:8], "big")


def exact_row_matches(real_hashes: set[str], synth_rows: Iterable[Mapping[str, Any]], cols: list[str]) -> int:
    """How many synthetic rows equal a real row on every column in ``cols``."""
    return sum(1 for row in synth_rows if row_hash(row, cols) in real_hashes)


def encode_features(columns: Mapping[str, Sequence[Any]], kinds: Mapping[str, str],
                    reference: Mapping[str, Sequence[Any]] | None = None) -> np.ndarray:
    """Rows → features in [0, 1]. Numbers: min–max on the reference's 1st–99th percentiles plus a
    null indicator. Categories: one-hot over the reference's 20 most frequent (+ other, + null),
    scaled by 1/√2 so a mismatch costs 1 — the same as a full-range numeric difference."""
    ref = reference if reference is not None else columns
    n = len(next(iter(columns.values()))) if columns else 0
    blocks: list[np.ndarray] = []
    for path in sorted(kinds):
        values = columns[path]
        if kinds[path] in NUMERIC_KINDS or kinds[path] == "bool":
            ref_x = _numbers(ref[path])
            lo, hi = (float(np.percentile(ref_x, 1)), float(np.percentile(ref_x, 99))) if ref_x.size else (0.0, 1.0)
            span = (hi - lo) or 1.0
            x = np.asarray([np.nan if (f := to_float(v)) is None else f for v in values], float)
            null = np.isnan(x)
            blocks.append(np.clip((np.where(null, lo, x) - lo) / span, 0.0, 1.0)[:, None])
            blocks.append(null.astype(float)[:, None])
        else:
            counts = Counter(canonical(v) for v in ref[path] if v is not None)
            top = [c for c, _ in sorted(counts.items(), key=lambda kv: (-kv[1], str(kv[0])))[:_TOP_CATEGORIES]]
            index = {c: i for i, c in enumerate(top)}
            onehot = np.zeros((len(values), len(top) + 2))
            for r, v in enumerate(values):
                onehot[r, len(top) + 1 if v is None else index.get(canonical(v), len(top))] = 1.0
            blocks.append(onehot / math.sqrt(2.0))
    return np.hstack(blocks) if blocks else np.zeros((n, 0))


def _nearest(queries: np.ndarray, reference: np.ndarray, exclude: np.ndarray | None = None) -> np.ndarray:
    """Euclidean distance from each query row to its nearest reference row (chunked brute force).
    ``exclude[i]`` is the reference index query ``i`` must skip (leave-one-out)."""
    ref_sq = np.einsum("ij,ij->i", reference, reference)
    out = np.empty(len(queries))
    for start in range(0, len(queries), _CHUNK):
        q = queries[start:start + _CHUNK]
        d2 = np.einsum("ij,ij->i", q, q)[:, None] + ref_sq[None, :] - 2.0 * (q @ reference.T)
        np.maximum(d2, 0.0, out=d2)
        if exclude is not None:
            d2[np.arange(len(q)), exclude[start:start + len(q)]] = np.inf
        out[start:start + len(q)] = np.sqrt(d2.min(axis=1))
    return out


def nndr(real: np.ndarray, synth: np.ndarray, *, sample: int = 2000, seed: int = 0,
         holdout: np.ndarray | None = None) -> dict[str, float]:
    """Nearest-neighbour distance ratio: median distance synthetic → real over median distance
    holdout → real (``holdout`` given) or real → other real (leave-one-out). ≈ 1 means synthetic
    rows sit no closer to real rows than unseen real rows do; ≪ 1 means copying."""
    empty = {"ratio": 0.0, "synth_to_real_median": 0.0, "real_to_real_median": 0.0, "synth_rows": 0.0,
             "real_rows": float(len(real)), "holdout": 0.0, "zero_distance_share": 0.0, "measured": 0.0}
    if len(real) < 2 or len(synth) == 0 or real.shape[1] == 0:
        return empty
    rng = np.random.default_rng(seed)
    ref = real if len(real) <= _REFERENCE_MAX else real[rng.choice(len(real), _REFERENCE_MAX, replace=False)]
    q_s = synth if len(synth) <= sample else synth[rng.choice(len(synth), sample, replace=False)]
    d_s = _nearest(q_s, ref)
    if holdout is not None and len(holdout) > 0:
        q_h = holdout if len(holdout) <= sample else holdout[rng.choice(len(holdout), sample, replace=False)]
        d_r = _nearest(q_h, ref)
    else:
        idx = rng.choice(len(ref), min(sample, len(ref)), replace=False)
        d_r = _nearest(ref[idx], ref, exclude=idx)
    med_s, med_r = float(np.median(d_s)), float(np.median(d_r))
    return {"ratio": round(med_s / med_r, 4) if med_r > 0 else (1.0 if med_s > 0 else 0.0),
            "synth_to_real_median": round(med_s, 6), "real_to_real_median": round(med_r, 6),
            "synth_rows": float(len(q_s)), "real_rows": float(len(ref)),
            "holdout": 1.0 if holdout is not None and len(holdout) > 0 else 0.0,
            "zero_distance_share": round(float(np.mean(d_s < 1e-9)), 4), "measured": 1.0}


def _qi_keys(rows: Iterable[Mapping[str, Any]], qis: list[str]) -> list[tuple[Any, ...]]:
    return [tuple(canonical(r.get(q)) for q in qis) for r in rows]


def qi_uniqueness(rows: Iterable[Mapping[str, Any]], quasi_identifiers: list[str]) -> float:
    """Share of rows whose quasi-identifier combination occurs exactly once."""
    if not quasi_identifiers:
        return 0.0
    keys = _qi_keys(rows, quasi_identifiers)
    if not keys:
        return 0.0
    counts = Counter(keys)
    return sum(1 for k in keys if counts[k] == 1) / len(keys)


def qi_unique_overlap(real_rows: Iterable[Mapping[str, Any]], synth_rows: Iterable[Mapping[str, Any]],
                      quasi_identifiers: list[str]) -> int:
    """How many QI-unique synthetic combinations equal a QI-unique real combination."""
    real, synth = Counter(_qi_keys(real_rows, quasi_identifiers)), Counter(_qi_keys(synth_rows, quasi_identifiers))
    unique_real = {k for k, c in real.items() if c == 1}
    return sum(1 for k, c in synth.items() if c == 1 and k in unique_real)


def generalize_qi(value: Any, how: str) -> str | None:
    """Coarsen a quasi-identifier before uniqueness is counted."""
    if value is None:
        return None
    if how == "decade" and isinstance(value, date):
        return f"{value.year // 10 * 10}s"
    if how == "band10" and (x := to_float(value)) is not None:
        lo = int(x // 10 * 10)
        return f"{lo}-{lo + 9}"
    if how == "prefix3":
        return str(canonical(value))[:3]
    return str(canonical(value)).lower()


def fidelity_report(real: Mapping[str, Sequence[Any]], synth: Mapping[str, Sequence[Any]],
                    kinds: Mapping[str, str], *, column_limit: float, corr_limit: float) -> dict[str, Any]:
    """Per-column KS/TVD + null-rate delta, Spearman matrix delta, and the summary score
    1 − mean column distance − mean correlation delta (mockup 10)."""
    columns: list[dict[str, Any]] = []
    ks: dict[str, float] = {}
    tvd: dict[str, float] = {}
    nulls: dict[str, float] = {}
    for path in sorted(kinds):
        if kinds[path] in NUMERIC_KINDS:
            distance, metric = ks_statistic(real[path], synth[path]), "ks"
            ks[path] = round(distance, 4)
        else:
            as_text = [[None if v is None else str(canonical(v)) for v in side[path]] for side in (real, synth)]
            distance, metric = total_variation(as_text[0], as_text[1]), "tvd"
            tvd[path] = round(distance, 4)
        delta = round(null_rate_delta(real[path], synth[path]), 4)
        nulls[path] = delta
        columns.append({"path": path, "kind": kinds[path], "metric": metric, "distance": round(distance, 4),
                        "null_rate_delta": delta, "within_limit": distance <= column_limit})
    numeric = [p for p in kinds if kinds[p] in NUMERIC_KINDS]
    names, matrix = correlation_matrix_delta({p: real[p] for p in numeric}, {p: synth[p] for p in numeric})
    pairs = [matrix[i][j] for i in range(len(names)) for j in range(i)]
    corr = round(float(np.mean(pairs)), 4) if pairs else 0.0
    mean_col = float(np.mean([c["distance"] for c in columns])) if columns else 0.0
    return {"ks": ks, "tvd": tvd, "null_rate_delta": nulls, "columns": columns,
            "corr_delta": corr, "corr_max": round(max(pairs, default=0.0), 4),
            "corr_matrix": {"columns": names, "delta": matrix},
            "limits": {"column": column_limit, "corr": corr_limit},
            "score": round(max(0.0, 1.0 - mean_col - corr), 4)}
```

- [ ] **Step 4: Run tests, lint, types**

Run: `pytest -q tests/test_metrics.py && ruff check dps tests && mypy dps`
Expected: `9 passed`, clean. If `test_nndr_detects_copies_and_accepts_fresh_draws` lands just outside `(0.8, 1.25)`, the seeds changed — keep the bounds and re-check `_nearest` (a ratio near 0.7 for fresh draws means the holdout is being measured against itself).

- [ ] **Step 5: Commit**

```bash
git add dps/engine/metrics.py tests/test_metrics.py && git commit -m "feat(engine): synthetic fidelity and privacy metrics (KS, TVD, correlation, NNDR, QI uniqueness)" -- dps/engine/metrics.py tests/test_metrics.py
```

---

### Task 3: Gaussian-copula engine

**Files:**
- Create: `dps/engine/synth/copula.py`
- Test: `tests/test_copula.py`

Public interface (contract §6.6 plus additive keyword arguments and members):
```python
class CopulaError(ValueError)
def uniforms(key: bytes, anchor: str, count: int) -> np.ndarray
def nearest_correlation(matrix: np.ndarray, floor: float = 1e-6) -> np.ndarray
class GaussianCopula:
    engine: ClassVar[str] = "copula"
    @classmethod
    def fit(cls, columns: dict[str, list[Any]], kinds: dict[str, str], *, rare_floor: float, seed: bytes,
            scales: Mapping[str, int] | None = None, reject_rows: Sequence[Mapping[str, Any]] = (),
            reject_columns: Sequence[str] = (), min_rows: int = 50, min_category_count: int = 5) -> GaussianCopula
    def sample_row(self, anchor: str) -> dict[str, Any]
    def sample_rows(self, anchors: Sequence[str], *, attempt: int = 0) -> list[dict[str, Any]]
    def row_digest(self, row: Mapping[str, Any]) -> int
    def is_rejected(self, row: Mapping[str, Any], *, extra: AbstractSet[int] = frozenset()) -> bool
    columns: list[str]            # column NAMES the sampler writes
    excluded: dict[str, str]      # path → reason ("all_null", "no_frequent_category")
    fit_rows: int; reject_size: int; correlation: np.ndarray
    def to_bytes(self) -> bytes
    @classmethod
    def from_bytes(cls, data: bytes, seed: bytes) -> GaussianCopula
```
`columns` passed to `fit` are keyed by column path `$.NAME`; rows produced are keyed by column **name** (the connector `Row` shape). Kinds are `int | decimal | float | date | timestamp | bool | categorical`.

- [ ] **Step 1: Write the failing tests**

`tests/test_copula.py`:
```python
from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

import numpy as np
import pytest

pytest.importorskip("scipy")

from dps.engine.metrics import correlation_delta, ks_statistic, total_variation  # noqa: E402
from dps.engine.synth.copula import CopulaError, GaussianCopula, nearest_correlation, uniforms  # noqa: E402

SEED = bytes(range(32))


def _data(n: int = 3000) -> tuple[dict[str, list], dict[str, str]]:
    rng = np.random.default_rng(11)
    balance = rng.lognormal(mean=9, sigma=1.0, size=n)
    limit = balance * 1.5 + rng.normal(scale=2000, size=n)
    seg = np.where(balance > np.median(balance), "AFFLUENT", "MASS")
    opened = [date(2015, 1, 1) + timedelta(days=int(d)) for d in rng.integers(0, 3000, size=n)]
    rare = ["RARE-" + str(i) if i < 3 else "COMMON" for i in range(n)]
    cols = {
        "$.BALANCE": [Decimal(f"{b:.2f}") for b in balance],
        "$.CREDIT_LIMIT": [None if i % 20 == 0 else int(v) for i, v in enumerate(limit)],
        "$.SEGMENT": seg.tolist(),
        "$.OPENED": opened,
        "$.ACTIVE": [bool(b > 5000) for b in balance],
        "$.UPDATED_AT": [datetime(2026, 1, 1) + timedelta(seconds=int(s)) for s in rng.integers(0, 10**7, n)],
        "$.TAG": rare,
    }
    kinds = {"$.BALANCE": "decimal", "$.CREDIT_LIMIT": "int", "$.SEGMENT": "categorical", "$.OPENED": "date",
             "$.ACTIVE": "bool", "$.UPDATED_AT": "timestamp", "$.TAG": "categorical"}
    return cols, kinds


@pytest.fixture(scope="module")
def model() -> GaussianCopula:
    cols, kinds = _data()
    return GaussianCopula.fit(cols, kinds, rare_floor=0.01, seed=SEED, scales={"$.BALANCE": 2})


def test_uniform_stream_is_keyed_and_in_open_interval() -> None:
    u = uniforms(SEED, "row:a", 20)
    assert u.shape == (20,) and np.all((u > 0) & (u < 1))
    assert np.array_equal(u, uniforms(SEED, "row:a", 20))
    assert not np.array_equal(u, uniforms(SEED, "row:b", 20))
    assert not np.array_equal(u, uniforms(bytes(32), "row:a", 20))


def test_nearest_correlation_repairs_an_indefinite_matrix() -> None:
    bad = np.array([[1.0, 0.9, -0.9], [0.9, 1.0, 0.9], [-0.9, 0.9, 1.0]])
    fixed = nearest_correlation(bad)
    assert np.allclose(np.diag(fixed), 1.0) and np.linalg.eigvalsh(fixed).min() > 0
    np.linalg.cholesky(fixed)


def test_rows_are_typed_deterministic_and_cover_modelled_columns(model: GaussianCopula) -> None:
    rows = model.sample_rows([f"row:{i}" for i in range(5)])
    assert rows == model.sample_rows([f"row:{i}" for i in range(5)])
    assert rows[0] == model.sample_row("row:0")
    assert set(model.columns) == {"BALANCE", "CREDIT_LIMIT", "SEGMENT", "OPENED", "ACTIVE", "UPDATED_AT", "TAG"}
    r = rows[0]
    assert isinstance(r["BALANCE"], Decimal) and r["BALANCE"].as_tuple().exponent == -2
    assert r["CREDIT_LIMIT"] is None or isinstance(r["CREDIT_LIMIT"], int)
    assert isinstance(r["OPENED"], date) and isinstance(r["ACTIVE"], bool)
    assert isinstance(r["UPDATED_AT"], datetime) and r["UPDATED_AT"].tzinfo is None
    assert model.sample_rows(["row:0"], attempt=1)[0] != r


def test_rare_categories_are_never_emitted(model: GaussianCopula) -> None:
    tags = {r["TAG"] for r in model.sample_rows([f"t:{i}" for i in range(2000)])}
    assert tags == {"COMMON"}


def test_marginals_correlations_and_nulls_are_preserved(model: GaussianCopula) -> None:
    cols, _ = _data()
    synth = model.sample_rows([f"s:{i}" for i in range(5000)])
    s = {k: [r[k[2:]] for r in synth] for k in ("$.BALANCE", "$.CREDIT_LIMIT", "$.SEGMENT")}
    assert ks_statistic(cols["$.BALANCE"], s["$.BALANCE"]) < 0.06
    assert total_variation(cols["$.SEGMENT"], s["$.SEGMENT"]) < 0.05
    assert correlation_delta({k: cols[k] for k in ("$.BALANCE", "$.CREDIT_LIMIT")},
                             {k: s[k] for k in ("$.BALANCE", "$.CREDIT_LIMIT")}) < 0.05
    null_rate = sum(v is None for v in s["$.CREDIT_LIMIT"]) / 5000
    assert abs(null_rate - 0.05) < 0.02


def test_tails_are_clamped(model: GaussianCopula) -> None:
    cols, _ = _data()
    real_max = max(cols["$.BALANCE"])
    synth_max = max(r["BALANCE"] for r in model.sample_rows([f"m:{i}" for i in range(5000)]))
    assert synth_max < real_max


def test_reject_set_and_digest() -> None:
    cols, kinds = _data(200)
    real_rows = [{k[2:]: cols[k][i] for k in cols} for i in range(200)]
    m = GaussianCopula.fit(cols, kinds, rare_floor=0.01, seed=SEED, reject_rows=real_rows,
                           reject_columns=sorted(real_rows[0]))
    assert m.reject_size == 200 and m.is_rejected(real_rows[7])
    synthetic = m.sample_row("x")
    assert not m.is_rejected(synthetic)
    assert m.is_rejected(synthetic, extra=frozenset({m.row_digest(synthetic)}))


def test_serialisation_round_trip_needs_the_seed(model: GaussianCopula) -> None:
    blob = model.to_bytes()
    again = GaussianCopula.from_bytes(blob, SEED)
    assert again.sample_rows(["row:1", "row:2"]) == model.sample_rows(["row:1", "row:2"])
    assert again.sample_rows(["row:1"]) != GaussianCopula.from_bytes(blob, bytes(32)).sample_rows(["row:1"])
    assert again.excluded == model.excluded and len(blob) < 200_000  # sealed by the caller (Task 6)
    with pytest.raises(CopulaError):
        GaussianCopula.from_bytes(b"not a model", SEED)


def test_fit_errors_name_columns_not_values() -> None:
    with pytest.raises(CopulaError, match="at least 50 rows"):
        GaussianCopula.fit({"$.A": [1, 2]}, {"$.A": "int"}, rare_floor=0.01, seed=SEED)
    only_rare = {"$.CODE": [f"C{i}" for i in range(100)]}
    with pytest.raises(CopulaError, match="no column") as err:
        GaussianCopula.fit(only_rare, {"$.CODE": "categorical"}, rare_floor=0.01, seed=SEED)
    assert "C1" not in str(err.value)
    m = GaussianCopula.fit({**only_rare, "$.N": list(range(100))}, {"$.CODE": "categorical", "$.N": "int"},
                           rare_floor=0.01, seed=SEED)
    assert m.excluded == {"$.CODE": "no_frequent_category"} and m.columns == ["N"]
```

- [ ] **Step 2: Run to confirm failure**

Run: `pytest -q tests/test_copula.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'dps.engine.synth.copula'`.

- [ ] **Step 3: Write `dps/engine/synth/copula.py`**

```python
"""Gaussian-copula tabular synthesizer (spec §5.6, contract §6.6).

**Fit.** Per column: empirical quantiles on a 201-point grid for numbers, dates (ordinal days) and
timestamps (epoch seconds), with the two extreme grid points replaced by the 0.1 % / 99.9 %
quantiles so no single outlier's exact value is stored or emitted; frequency tables for
categories and booleans, where a category is kept only if it has at least
``max(min_category_count, ⌈rare_floor · n⌉)`` rows and the folded mass is redistributed over the
kept ones (rare values are never emitted); null rates. The copula is the Pearson correlation of
normal scores — ranks for numbers, a jittered point inside the category's probability interval
for categories — over pairwise-complete rows, repaired to the nearest positive-definite
correlation matrix.

**Sample.** For a row anchor, 2·d uniforms come from a keyed BLAKE2b stream (same seed + anchor →
same row on every machine and numpy version); the first d become correlated normals through the
Cholesky factor and are mapped back through each marginal's inverse CDF; the second d decide
nulls. ``attempt`` > 0 salts the stream so a rejected row can be redrawn deterministically.

**Reject set.** Keyed 64-bit digests of real rows (fit sample + holdout); a synthetic row equal
to any of them on every non-key column is rejected by the caller (``is_rejected``).

The model embeds quantiles and category labels of real data. It is sealed with the job KEK,
never logged, never stored unsealed and purged with the job key. Serialisation is a compressed
``npz`` read back with ``allow_pickle=False``.
"""
from __future__ import annotations

import hashlib
import io
import json
import math
import zipfile
from collections import Counter
from collections.abc import Mapping, Sequence, Set
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, ClassVar

import numpy as np
from scipy import special, stats

from dps.engine.metrics import canonical, row_digest, to_float

FORMAT = "dps.copula.v1"
QUANTILE_POINTS = 201
TAIL = 0.001
MIN_ROWS = 50
MIN_CATEGORY_COUNT = 5
MAX_CATEGORIES = 500
MIN_PAIR_ROWS = 10
NUMERIC_KINDS = frozenset({"int", "decimal", "float", "date", "timestamp"})
KINDS = NUMERIC_KINDS | {"bool", "categorical"}
_TWO_53 = 9007199254740992.0


class CopulaError(ValueError):
    """The sample cannot be modelled. Messages name columns and counts, never values."""


def _subkey(seed: bytes, label: str) -> bytes:
    return hashlib.blake2b(seed, digest_size=32, person=b"dps.copula.key", salt=label.encode()[:16]).digest()


def uniforms(key: bytes, anchor: str, count: int) -> np.ndarray:
    """``count`` uniforms in (0, 1) from a keyed BLAKE2b stream over ``anchor``."""
    blocks = -(-count // 8)
    raw = b"".join(hashlib.blake2b(f"{anchor}\x1f{i}".encode(), key=key, digest_size=64,
                                   person=b"dps.copula.v1").digest() for i in range(blocks))
    ints = np.frombuffer(raw, dtype=">u8")[:count].astype(np.uint64)
    return ((ints >> np.uint64(11)).astype(np.float64) + 0.5) / _TWO_53


def nearest_correlation(matrix: np.ndarray, floor: float = 1e-6) -> np.ndarray:
    """Clip eigenvalues to ``floor`` and rescale to a unit diagonal (Higham's one-step repair)."""
    sym = (matrix + matrix.T) / 2.0
    values, vectors = np.linalg.eigh(sym)
    fixed = (vectors * np.clip(values, floor, None)) @ vectors.T
    scale = np.sqrt(np.clip(np.diag(fixed), floor, None))
    fixed = fixed / np.outer(scale, scale)
    np.fill_diagonal(fixed, 1.0)
    return fixed


def _cholesky(matrix: np.ndarray) -> np.ndarray:
    try:
        return np.linalg.cholesky(matrix)
    except np.linalg.LinAlgError:
        return np.linalg.cholesky(nearest_correlation(matrix + 1e-6 * np.eye(len(matrix)), floor=1e-4))


def _label(value: Any, kind: str) -> str:
    if kind == "bool":
        return "true" if bool(value) else "false"
    return value if isinstance(value, str) else str(canonical(value))


@dataclass
class Marginal:
    """One column's marginal distribution."""

    path: str
    kind: str
    null_rate: float
    quantiles: np.ndarray | None = None
    categories: list[str] | None = None
    cum: np.ndarray | None = None
    scale: int = 0
    tz_aware: bool = False

    @property
    def name(self) -> str:
        return self.path[2:]

    def inverse(self, p: np.ndarray) -> list[Any]:
        """Map uniforms in (0, 1) to typed values of this column."""
        if self.kind in NUMERIC_KINDS:
            assert self.quantiles is not None
            grid = np.linspace(0.0, 1.0, len(self.quantiles))
            return [self._typed(float(x)) for x in np.interp(p, grid, self.quantiles)]
        assert self.categories is not None and self.cum is not None
        idx = np.minimum(np.searchsorted(self.cum, p, side="right"), len(self.categories) - 1)
        labels = [self.categories[int(i)] for i in idx]
        return [lab == "true" for lab in labels] if self.kind == "bool" else labels

    def _typed(self, x: float) -> Any:
        if self.kind == "int":
            return int(round(x))
        if self.kind == "decimal":
            return Decimal(f"{x:.{self.scale}f}")
        if self.kind == "date":
            return date.fromordinal(max(1, int(round(x))))
        if self.kind == "timestamp":
            moment = datetime.fromtimestamp(round(x, 6), tz=UTC)
            return moment if self.tz_aware else moment.replace(tzinfo=None)
        return x


def _fit_numeric(path: str, kind: str, values: Sequence[Any], scale: int) -> tuple[Marginal, np.ndarray] | str:
    raw = [to_float(v) for v in values]
    mask = np.array([x is not None for x in raw])
    x = np.array([v for v in raw if v is not None], dtype=float)
    if x.size == 0:
        return "all_null"
    q = np.quantile(x, np.linspace(0.0, 1.0, QUANTILE_POINTS))
    q[0], q[-1] = np.quantile(x, [TAIL, 1.0 - TAIL])
    q = np.maximum.accumulate(q)
    z = np.full(len(values), np.nan)
    z[mask] = special.ndtri(stats.rankdata(x, method="average") / (x.size + 1))
    tz_aware = kind == "timestamp" and any(isinstance(v, datetime) and v.tzinfo is not None for v in values)
    return Marginal(path, kind, float(1.0 - mask.mean()), quantiles=q, scale=scale, tz_aware=tz_aware), z


def _fit_categorical(path: str, kind: str, values: Sequence[Any], *, rare_floor: float, min_count: int,
                     jitter: np.ndarray) -> tuple[Marginal, np.ndarray] | str:
    labels = [None if v is None else _label(v, kind) for v in values]
    counts = Counter(lab for lab in labels if lab is not None)
    nonnull = sum(counts.values())
    if nonnull == 0:
        return "all_null"
    threshold = max(min_count, math.ceil(rare_floor * nonnull))
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:MAX_CATEGORIES]
    kept = [(c, k) for c, k in ranked if k >= threshold]
    if not kept:
        return "no_frequent_category"
    total = float(sum(k for _, k in kept))
    cum = np.cumsum([k / total for _, k in kept])
    cum[-1] = 1.0
    lo = np.concatenate([[0.0], cum[:-1]])
    pos = {c: i for i, (c, _) in enumerate(kept)}
    z = np.full(len(values), np.nan)
    for r, lab in enumerate(labels):
        if lab is not None and lab in pos:
            i = pos[lab]
            z[r] = special.ndtri(min(max(lo[i] + (cum[i] - lo[i]) * jitter[r], 1e-12), 1.0 - 1e-12))
    marginal = Marginal(path, kind, float(sum(lab is None for lab in labels) / len(labels)),
                        categories=[c for c, _ in kept], cum=cum)
    return marginal, z


class GaussianCopula:
    """See the module docstring. Construct with :meth:`fit` or :meth:`from_bytes`."""

    engine: ClassVar[str] = "copula"

    def __init__(self, marginals: list[Marginal], chol: np.ndarray, reject: set[int], *, seed: bytes,
                 excluded: dict[str, str], fit_rows: int, reject_columns: Sequence[str]) -> None:
        self._marginals = marginals
        self._chol = chol
        self._reject = reject
        self._sample_key = _subkey(seed, "sample")
        self._reject_key = _subkey(seed, "reject")
        self._null_rates = np.array([m.null_rate for m in marginals])
        self.excluded = dict(excluded)
        self.fit_rows = fit_rows
        self.reject_columns = list(reject_columns)

    # -- fit --------------------------------------------------------------------------------
    @classmethod
    def fit(cls, columns: dict[str, list[Any]], kinds: dict[str, str], *, rare_floor: float, seed: bytes,
            scales: Mapping[str, int] | None = None, reject_rows: Sequence[Mapping[str, Any]] = (),
            reject_columns: Sequence[str] = (), min_rows: int = MIN_ROWS,
            min_category_count: int = MIN_CATEGORY_COUNT) -> GaussianCopula:
        """Fit marginals and the copula on ``columns`` (path → values, equal lengths)."""
        unknown = sorted(p for p, k in kinds.items() if k not in KINDS)
        if unknown:
            raise CopulaError(f"unsupported column kinds for {', '.join(unknown)}")
        n = len(next(iter(columns.values()))) if columns else 0
        if n < min_rows:
            raise CopulaError(f"tabular synthesis needs at least {min_rows} rows, got {n}")
        rng = np.random.default_rng(int.from_bytes(_subkey(seed, "fit")[:8], "big"))
        marginals: list[Marginal] = []
        scores: list[np.ndarray] = []
        excluded: dict[str, str] = {}
        for path in sorted(columns):
            kind = kinds[path]
            if kind in NUMERIC_KINDS:
                fitted = _fit_numeric(path, kind, columns[path], (scales or {}).get(path, 0))
            else:
                fitted = _fit_categorical(path, kind, columns[path], rare_floor=rare_floor,
                                          min_count=min_category_count, jitter=rng.uniform(0.05, 0.95, n))
            if isinstance(fitted, str):
                excluded[path] = fitted
                continue
            marginals.append(fitted[0])
            scores.append(fitted[1])
        if not marginals:
            raise CopulaError("no column can be modelled (all null or no frequent category)")
        z = np.column_stack(scores)
        finite = np.isfinite(z)
        corr = np.eye(len(marginals))
        for i in range(len(marginals)):
            for j in range(i + 1, len(marginals)):
                both = finite[:, i] & finite[:, j]
                a, b = z[both, i], z[both, j]
                if both.sum() >= MIN_PAIR_ROWS and a.std() > 0 and b.std() > 0:
                    corr[i, j] = corr[j, i] = float(np.corrcoef(a, b)[0, 1])
        model = cls(marginals, _cholesky(nearest_correlation(corr)), set(), seed=seed, excluded=excluded,
                    fit_rows=n, reject_columns=reject_columns)
        model._reject = {model.row_digest(r) for r in reject_rows} if reject_columns else set()
        return model

    # -- sample -------------------------------------------------------------------------------
    @property
    def columns(self) -> list[str]:
        return [m.name for m in self._marginals]

    @property
    def correlation(self) -> np.ndarray:
        return self._chol @ self._chol.T

    @property
    def reject_size(self) -> int:
        return len(self._reject)

    def sample_rows(self, anchors: Sequence[str], *, attempt: int = 0) -> list[dict[str, Any]]:
        """One synthetic row per anchor (column name → typed value); deterministic."""
        if not anchors:
            return []
        d = len(self._marginals)
        salt = "" if attempt == 0 else f"\x1eretry{attempt}"
        u = np.vstack([uniforms(self._sample_key, a + salt, 2 * d) for a in anchors])
        p = np.clip(special.ndtr(special.ndtri(u[:, :d]) @ self._chol.T), 1e-12, 1.0 - 1e-12)
        nulls = u[:, d:] < self._null_rates[None, :]
        rows: list[dict[str, Any]] = [{} for _ in anchors]
        for j, marginal in enumerate(self._marginals):
            for r, value in enumerate(marginal.inverse(p[:, j])):
                rows[r][marginal.name] = None if nulls[r, j] else value
        return rows

    def sample_row(self, anchor: str) -> dict[str, Any]:
        """Contract §6.6 single-row form of :meth:`sample_rows`."""
        return self.sample_rows([anchor])[0]

    def row_digest(self, row: Mapping[str, Any]) -> int:
        """Keyed digest of ``row`` over the reject columns (non-key output columns)."""
        return row_digest(row, self.reject_columns, self._reject_key)

    def is_rejected(self, row: Mapping[str, Any], *, extra: Set[int] = frozenset()) -> bool:
        """True when ``row`` equals a real row of the fit sample/holdout (or one in ``extra``)."""
        if not self.reject_columns:
            return False
        digest = self.row_digest(row)
        return digest in self._reject or digest in extra

    # -- (de)serialise --------------------------------------------------------------------------
    def to_bytes(self) -> bytes:
        """Compressed npz; the seed is NOT included (it lives in the JobSecret)."""
        meta = {"format": FORMAT, "fit_rows": self.fit_rows, "excluded": self.excluded,
                "reject_columns": self.reject_columns,
                "marginals": [{"path": m.path, "kind": m.kind, "null_rate": m.null_rate,
                               "categories": m.categories, "scale": m.scale, "tz_aware": m.tz_aware}
                              for m in self._marginals]}
        arrays: dict[str, np.ndarray] = {
            "meta": np.frombuffer(json.dumps(meta, sort_keys=True).encode("utf-8"), dtype=np.uint8),
            "chol": self._chol,
            "reject": np.array(sorted(self._reject), dtype=np.uint64),
        }
        for i, m in enumerate(self._marginals):
            if m.quantiles is not None:
                arrays[f"q{i}"] = m.quantiles
            if m.cum is not None:
                arrays[f"c{i}"] = m.cum
        buffer = io.BytesIO()
        np.savez_compressed(buffer, **arrays)  # type: ignore[arg-type]
        return buffer.getvalue()

    @classmethod
    def from_bytes(cls, data: bytes, seed: bytes) -> GaussianCopula:
        """Rebuild a model; ``seed`` is the job's synthesis seed bytes."""
        try:
            with np.load(io.BytesIO(data), allow_pickle=False) as archive:
                meta = json.loads(archive["meta"].tobytes().decode("utf-8"))
                if meta.get("format") != FORMAT:
                    raise CopulaError("unsupported model format")
                marginals = [Marginal(path=m["path"], kind=m["kind"], null_rate=float(m["null_rate"]),
                                      quantiles=archive[f"q{i}"] if f"q{i}" in archive.files else None,
                                      categories=m["categories"],
                                      cum=archive[f"c{i}"] if f"c{i}" in archive.files else None,
                                      scale=int(m["scale"]), tz_aware=bool(m["tz_aware"]))
                             for i, m in enumerate(meta["marginals"])]
                return cls(marginals, archive["chol"], {int(x) for x in archive["reject"]}, seed=seed,
                           excluded=meta["excluded"], fit_rows=int(meta["fit_rows"]),
                           reject_columns=meta["reject_columns"])
        except (OSError, ValueError, KeyError, zipfile.BadZipFile) as exc:
            if isinstance(exc, CopulaError):
                raise
            raise CopulaError("model bytes could not be read") from exc
```

- [ ] **Step 4: Run tests, lint, types**

Run: `pytest -q tests/test_copula.py && ruff check dps tests && mypy dps`
Expected: `9 passed`, clean. `test_marginals_correlations_and_nulls_are_preserved` takes ~2 s (3000 rows). If the correlation assertion fails, check that `sample_rows` multiplies by `chol.T` (row vectors), not `chol`.

- [ ] **Step 5: Commit**

```bash
git add dps/engine/synth/copula.py tests/test_copula.py && git commit -m "feat(synth): Gaussian-copula tabular engine with keyed deterministic sampling and reject set" -- dps/engine/synth/copula.py tests/test_copula.py
```

---
### Task 4: Tabular constraints — unique, ranges, positivity, dependent-field rules

**Files:**
- Modify: `dps/engine/policy.py` (M0 touch point)
- Create: `dps/engine/synth/constraints.py`
- Test: `tests/test_constraints.py`, `tests/test_policy.py` (unchanged assertions must still pass)

Policy additions (in `dps/engine/policy.py`, above `SynthesizeConfig`):
```python
JsonScalar = str | int | float | bool | None
_COLUMN_PATH = r"^\$\.[A-Za-z_][A-Za-z0-9_$#]{0,127}$"


class WhenCondition(BaseModel):
    """``{"path": "$.BALANCE", "op": "<", "value": 0}`` — compared against the synthetic row."""

    model_config = ConfigDict(extra="forbid")
    path: str = Field(pattern=_COLUMN_PATH)
    op: Literal["<", "<=", ">", ">=", "==", "!=", "in", "not_in", "is_null", "not_null"]
    value: JsonScalar | list[JsonScalar] = None


class WhenRule(BaseModel):
    """``{"if": {...}, "then": "OVERDRAWN"}``; the first matching rule of a constraint wins."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    if_: WhenCondition = Field(alias="if")
    then: JsonScalar = None


class TabularConstraint(BaseModel):
    """A rule the tabular engine enforces on every synthetic row (spec §5.6). ``object`` limits it
    to one table (``OWNER.NAME``, case-insensitive); ``None`` applies it wherever the column exists."""

    model_config = ConfigDict(extra="forbid")
    path: str = Field(pattern=_COLUMN_PATH)
    object: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_$#.]{1,257}$")
    min: float | str | None = None
    max: float | str | None = None
    positive: bool = False
    unique: bool = False
    when: list[WhenRule] = Field(default_factory=list)

    @model_validator(mode="after")
    def _coherent(self) -> TabularConstraint:
        ranged = self.min is not None or self.max is not None or self.positive
        if self.unique and (ranged or self.when):
            raise ValueError(f"constraint on {self.path}: unique cannot be combined with other rules")
        if not (self.unique or ranged or self.when):
            raise ValueError(f"constraint on {self.path} sets no rule")
        return self
```
`SynthesizeConfig` gains two fields (after `llm_rewrite_text`):
```python
    constraints: list[TabularConstraint] = Field(default_factory=list)
    quasi_identifiers: list[str] = Field(default_factory=list)   # column paths "$.NAME" for the QI metric
```

- [ ] **Step 1: Write the failing tests**

`tests/test_constraints.py`:
```python
from __future__ import annotations

import re
from datetime import date
from decimal import Decimal

import pytest

from dps.engine.policy import SynthesizeConfig, TabularConstraint, load_preset
from dps.engine.synth.constraints import ColumnSpec, ConstraintError, ConstraintSet, feistel_permute

SEED = bytes(range(32))
KEYS = [b"k1" * 16, b"k2" * 16, b"k3" * 16, b"k4" * 16]
COLS = {
    "ACCOUNT_REF": ColumnSpec("ACCOUNT_REF", "int", precision=10),
    "CARD_REF": ColumnSpec("CARD_REF", "categorical", shape="XXX-99999999"),
    "BALANCE": ColumnSpec("BALANCE", "decimal", scale=2),
    "OPENED": ColumnSpec("OPENED", "date"),
    "STATUS": ColumnSpec("STATUS", "categorical"),
    "TENURE": ColumnSpec("TENURE", "int"),
}


def _set(*constraints: dict, fqn: str = "BANK.ACCOUNTS") -> ConstraintSet:
    return ConstraintSet([TabularConstraint.model_validate(c) for c in constraints], COLS, seed=SEED,
                         object_fqn=fqn)


def test_feistel_is_a_bijection_and_keyed() -> None:
    assert sorted(feistel_permute(x, 1000, KEYS) for x in range(1000)) == list(range(1000))
    sample = {feistel_permute(x, 999_983, KEYS) for x in range(5000)}
    assert len(sample) == 5000 and max(sample) < 999_983
    assert feistel_permute(7, 1000, KEYS) == feistel_permute(7, 1000, KEYS)
    assert [feistel_permute(x, 1000, KEYS) for x in range(20)] != [feistel_permute(x, 1000, KEYS[::-1]) for x in range(20)]
    with pytest.raises(ConstraintError):
        feistel_permute(1000, 1000, KEYS)


def test_unique_integer_column() -> None:
    cs = _set({"path": "$.ACCOUNT_REF", "unique": True})
    assert cs.needs_key_int
    values = []
    for k in range(10_000):
        row = {"ACCOUNT_REF": 5}
        cs.apply(row, key_int=k)
        values.append(row["ACCOUNT_REF"])
    assert len(set(values)) == 10_000 and all(0 <= v < 10**10 for v in values)
    again = {"ACCOUNT_REF": 0}
    cs.apply(again, key_int=42)
    assert again["ACCOUNT_REF"] == values[42]


def test_unique_text_column_follows_the_shape() -> None:
    cs = _set({"path": "$.CARD_REF", "unique": True})
    seen = set()
    for k in range(5000):
        row: dict = {"CARD_REF": "x"}
        cs.apply(row, key_int=k)
        assert re.fullmatch(r"[A-Z]{3}-\d{8}", row["CARD_REF"])
        seen.add(row["CARD_REF"])
    assert len(seen) == 5000 and len({v[:3] for v in seen}) == 1


def test_unique_errors_are_value_free() -> None:
    with pytest.raises(ConstraintError, match="at least 6"):
        ConstraintSet([TabularConstraint(path="$.CARD_REF", unique=True)],
                      {"CARD_REF": ColumnSpec("CARD_REF", "categorical", shape="XX-999")}, seed=SEED, object_fqn="T")
    cs = _set({"path": "$.ACCOUNT_REF", "unique": True})
    with pytest.raises(ConstraintError, match="single integer key"):
        cs.apply({"ACCOUNT_REF": 1}, key_int=None)
    with pytest.raises(ConstraintError) as err:
        cs.apply({"ACCOUNT_REF": 1}, key_int=10**10 + 5)
    assert str(10**10 + 5) not in str(err.value)


def test_ranges_and_positivity() -> None:
    cs = _set({"path": "$.BALANCE", "min": 0, "max": 1000}, {"path": "$.OPENED", "min": "2020-01-01"},
              {"path": "$.TENURE", "positive": True})
    row = {"BALANCE": Decimal("-5.10"), "OPENED": date(2019, 5, 1), "TENURE": 0}
    cs.apply(row, key_int=None)
    assert row == {"BALANCE": Decimal("0.00"), "OPENED": date(2020, 1, 1), "TENURE": 1}
    high = {"BALANCE": Decimal("1500.00"), "OPENED": None, "TENURE": 3}
    cs.apply(high, key_int=None)
    assert high == {"BALANCE": Decimal("1000.00"), "OPENED": None, "TENURE": 3}


def test_dependent_rules_first_match_wins() -> None:
    cs = _set({"path": "$.STATUS", "when": [
        {"if": {"path": "$.BALANCE", "op": "<", "value": 0}, "then": "OVERDRAWN"},
        {"if": {"path": "$.BALANCE", "op": ">=", "value": 100000}, "then": "PREMIUM"},
        {"if": {"path": "$.OPENED", "op": "is_null"}, "then": None},
        {"if": {"path": "$.TENURE", "op": "in", "value": [1, 2]}, "then": "NEW"}]})
    cases = [({"BALANCE": Decimal("-1"), "OPENED": date(2021, 1, 1), "TENURE": 1, "STATUS": "OK"}, "OVERDRAWN"),
             ({"BALANCE": Decimal("250000"), "OPENED": None, "TENURE": 9, "STATUS": "OK"}, "PREMIUM"),
             ({"BALANCE": Decimal("10"), "OPENED": None, "TENURE": 9, "STATUS": "OK"}, None),
             ({"BALANCE": Decimal("10"), "OPENED": date(2021, 1, 1), "TENURE": 2, "STATUS": "OK"}, "NEW"),
             ({"BALANCE": Decimal("10"), "OPENED": date(2021, 1, 1), "TENURE": 9, "STATUS": "OK"}, "OK")]
    for row, expected in cases:
        cs.apply(row, key_int=None)
        assert row["STATUS"] == expected


def test_object_filter_missing_columns_and_describe() -> None:
    cs = _set({"path": "$.BALANCE", "min": 0, "object": "OTHER.TABLE"}, {"path": "$.NOT_HERE", "positive": True},
              {"path": "$.TENURE", "positive": True}, fqn="bank.accounts")
    assert cs.describe() == [{"path": "$.TENURE", "rule": "positive"}]
    assert cs.skipped == [{"path": "$.BALANCE", "reason": "other_object"}, {"path": "$.NOT_HERE", "reason": "no_column"}]


def test_policy_validates_constraints_and_presets_still_load() -> None:
    with pytest.raises(ValueError, match="unique cannot be combined"):
        TabularConstraint(path="$.A", unique=True, min=1)
    with pytest.raises(ValueError, match="sets no rule"):
        TabularConstraint(path="$.A")
    with pytest.raises(ValueError):
        TabularConstraint(path="A", positive=True)
    cfg = SynthesizeConfig.model_validate({"constraints": [{"path": "$.S", "when": [
        {"if": {"path": "$.B", "op": "<", "value": 0}, "then": "X"}]}], "quasi_identifiers": ["$.GENDER"]})
    assert cfg.constraints[0].when[0].if_.op == "<" and cfg.quasi_identifiers == ["$.GENDER"]
    assert load_preset("synthetic-realistic").synthesize.constraints == []
```

- [ ] **Step 2: Run to confirm failure**

Run: `pytest -q tests/test_constraints.py`
Expected: FAIL with `ImportError: cannot import name 'TabularConstraint'`.

- [ ] **Step 3: Add the policy models** (code above) and run `pytest -q tests/test_policy.py` → still `4 passed` (hashes are compared relative to each other, never to literals).

- [ ] **Step 4: Write `dps/engine/synth/constraints.py`**

```python
"""Constraints applied to synthetic tabular rows after sampling (spec §5.6).

Order per row: ``unique`` (a keyed Feistel permutation of the row's integer key — unique by
construction, deterministic, resumable), then ``min``/``max``/``positive`` (clip), then ``when``
rules (first match wins). Rules come from ``policy.synthesize.constraints``; their literals are
configuration, not customer data. Errors name columns and rules only.
"""
from __future__ import annotations

import hashlib
import hmac
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from dps.engine.metrics import canonical, to_float
from dps.engine.policy import TabularConstraint, WhenCondition

MIN_UNIQUE_DIGITS = 6
_NUMERIC = frozenset({"int", "decimal", "float"})
_ORDERED = _NUMERIC | {"date", "timestamp"}


class ConstraintError(ValueError):
    """A constraint cannot be applied (incompatible column, key or shape)."""


@dataclass(frozen=True)
class ColumnSpec:
    """What a constraint needs to know about a column (no values)."""

    name: str
    kind: str
    precision: int | None = None
    scale: int | None = None
    shape: str | None = None


def feistel_permute(x: int, domain: int, keys: Sequence[bytes]) -> int:
    """A keyed bijection on ``range(domain)``: a balanced Feistel network over the smallest even bit
    width covering ``domain``, cycle-walked back into range (fewer than 4 walks on average)."""
    if domain < 2 or not 0 <= x < domain:
        raise ConstraintError("value outside the permutation domain")
    bits = max(2, (domain - 1).bit_length())
    bits += bits % 2
    half = bits // 2
    mask = (1 << half) - 1
    y = x
    while True:
        left, right = y >> half, y & mask
        for key in keys:
            mixed = hmac.new(key, right.to_bytes(32, "big"), hashlib.sha256).digest()
            left, right = right, left ^ (int.from_bytes(mixed[:16], "big") & mask)
        y = (left << half) | right
        if y < domain:
            return y


def _coerce(value: Any, spec: ColumnSpec) -> Any:
    if value is None:
        return None
    if spec.kind == "int":
        return int(value)
    if spec.kind == "decimal":
        number = Decimal(str(value))
        return number.quantize(Decimal(1).scaleb(-spec.scale)) if spec.scale is not None else number
    if spec.kind == "float":
        return float(value)
    if spec.kind == "date":
        return value if isinstance(value, date) and not isinstance(value, datetime) else date.fromisoformat(str(value))
    if spec.kind == "timestamp":
        return value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    if spec.kind == "bool":
        return value if isinstance(value, bool) else str(value).lower() in ("true", "1", "yes")
    return str(value)


def _comparable(actual: Any, expected: Any) -> tuple[float, float] | None:
    if isinstance(expected, str) and isinstance(actual, date):
        expected = datetime.fromisoformat(expected) if isinstance(actual, datetime) else date.fromisoformat(expected)
    a, b = to_float(actual), to_float(expected)
    return None if a is None or b is None else (a, b)


def _matches(cond: WhenCondition, row: Mapping[str, Any]) -> bool:
    actual = row.get(cond.path[2:])
    if cond.op == "is_null":
        return actual is None
    if cond.op == "not_null":
        return actual is not None
    if actual is None:
        return False
    if cond.op in ("in", "not_in"):
        options = cond.value if isinstance(cond.value, list) else [cond.value]
        hit = any(canonical(actual) == canonical(o) for o in options)
        return hit if cond.op == "in" else not hit
    if cond.op == "==":
        return canonical(actual) == canonical(cond.value)
    if cond.op == "!=":
        return canonical(actual) != canonical(cond.value)
    pair = _comparable(actual, cond.value)
    if pair is None:
        return False
    a, b = pair
    return {"<": a < b, "<=": a <= b, ">": a > b, ">=": a >= b}[cond.op]


@dataclass
class _Unique:
    spec: ColumnSpec
    domain: int
    keys: list[bytes]
    template: str | None    # text shape with letters fixed, '9' = digit slot


class ConstraintSet:
    """The constraints of one policy that apply to one table, ready to run per row."""

    def __init__(self, constraints: Sequence[TabularConstraint], columns: Mapping[str, ColumnSpec], *,
                 seed: bytes, object_fqn: str) -> None:
        self._unique: list[_Unique] = []
        self._ranges: list[tuple[ColumnSpec, Any, Any, bool]] = []
        self._rules: list[tuple[ColumnSpec, TabularConstraint]] = []
        self._described: list[dict[str, str]] = []
        self.skipped: list[dict[str, str]] = []
        for c in constraints:
            name = c.path[2:]
            if c.object and c.object.upper() != object_fqn.upper():
                self.skipped.append({"path": c.path, "reason": "other_object"})
                continue
            spec = columns.get(name)
            if spec is None:
                self.skipped.append({"path": c.path, "reason": "no_column"})
                continue
            if c.unique:
                self._unique.append(self._build_unique(spec, seed))
                self._described.append({"path": c.path, "rule": "unique"})
            elif c.min is not None or c.max is not None or c.positive:
                if spec.kind not in _ORDERED or (c.positive and spec.kind not in _NUMERIC):
                    raise ConstraintError(f"range rule on {c.path} needs a numeric or date column")
                self._ranges.append((spec, _coerce(c.min, spec), _coerce(c.max, spec), c.positive))
                self._described.append({"path": c.path, "rule": "positive" if c.positive and c.min is None
                                        and c.max is None else "range"})
            if c.when:
                self._rules.append((spec, c))
                self._described.append({"path": c.path, "rule": "when"})

    @staticmethod
    def _build_unique(spec: ColumnSpec, seed: bytes) -> _Unique:
        keys = [hmac.new(seed, f"dps.unique|{spec.name}|{r}".encode(), hashlib.sha256).digest() for r in range(4)]
        if spec.kind in ("int",) or (spec.kind == "decimal" and not spec.scale):
            digits = max(MIN_UNIQUE_DIGITS, spec.precision or 15)
            return _Unique(spec, 10**digits, keys, None)
        if spec.kind == "categorical":
            shape = spec.shape or ""
            slots = shape.count("9")
            if slots < MIN_UNIQUE_DIGITS:
                raise ConstraintError(f"unique rule on $.{spec.name} needs a value shape with at least "
                                      f"{MIN_UNIQUE_DIGITS} digit positions")
            letters = hmac.new(seed, f"dps.unique.letters|{spec.name}".encode(), hashlib.sha256).digest()
            out, i = [], 0
            for ch in shape:
                if ch in "Xx":
                    letter = chr(ord("A") + letters[i % len(letters)] % 26)
                    out.append(letter if ch == "X" else letter.lower())
                    i += 1
                else:
                    out.append(ch)
            return _Unique(spec, 10**slots, keys, "".join(out))
        raise ConstraintError(f"unique rule on $.{spec.name} needs an integer or text column")

    @property
    def needs_key_int(self) -> bool:
        return bool(self._unique)

    def describe(self) -> list[dict[str, str]]:
        """Value-free summary for the evidence pack."""
        return list(self._described)

    def apply(self, row: dict[str, Any], *, key_int: int | None) -> None:
        """Enforce every constraint on ``row`` (column name → value), in place."""
        for u in self._unique:
            if key_int is None:
                raise ConstraintError(f"unique rule on $.{u.spec.name} needs a single integer key column")
            if not 0 <= key_int < u.domain:
                raise ConstraintError(f"unique rule on $.{u.spec.name}: the key is outside its domain")
            n = feistel_permute(key_int, u.domain, u.keys)
            if u.template is None:
                row[u.spec.name] = _coerce(n, u.spec)
            else:
                digits = iter(str(n).zfill(u.template.count("9")))
                row[u.spec.name] = "".join(next(digits) if ch == "9" else ch for ch in u.template)
        for spec, lo, hi, positive in self._ranges:
            value = row.get(spec.name)
            if value is None:
                continue
            x = to_float(value)
            if lo is not None and x is not None and x < (to_float(lo) or 0.0):
                row[spec.name] = lo
            elif hi is not None and x is not None and x > (to_float(hi) or 0.0):
                row[spec.name] = hi
            if positive and (to_float(row[spec.name]) or 0.0) <= 0:
                smallest = 1 if spec.kind == "int" else Decimal(1).scaleb(-(spec.scale or 0)) \
                    if spec.kind == "decimal" else 1e-9
                row[spec.name] = smallest
            if spec.kind == "decimal" and row[spec.name] is not None:
                row[spec.name] = _coerce(row[spec.name], spec)
        for spec, constraint in self._rules:
            for rule in constraint.when:
                if _matches(rule.if_, row):
                    row[spec.name] = _coerce(rule.then, spec)
                    break
```
Note the `x < (to_float(lo) or 0.0)` guard: `to_float(lo)` is never `None` for a coerced numeric/date bound; the `or 0.0` only satisfies mypy. If ruff flags the nested conditional for `smallest`, split it into an `if/elif/else` (same values).

- [ ] **Step 5: Run tests, lint, types**

Run: `pytest -q tests/test_constraints.py tests/test_policy.py && ruff check dps tests && mypy dps`
Expected: `8 passed` + `4 passed`, clean.

- [ ] **Step 6: Commit**

```bash
git add dps/engine/policy.py dps/engine/synth/constraints.py tests/test_constraints.py
git commit -m "feat(synth): tabular constraints — keyed unique, ranges, positivity, dependent-field rules" -- dps/engine/policy.py dps/engine/synth/constraints.py tests/test_constraints.py
```

---

### Task 5: Tabular sampling and exact-row rejection inside `BatchTransformer`

**Files:**
- Modify: `dps/engine/batch.py` (M2 touch point), `docs/superpowers/plans/2026-10-11-m1-m5-contract.md` (§6.4, §6.6)
- Test: `tests/test_batch_tabular.py`

The contract's `TabularSampler` protocol (§6.6) is extended — every member is additive:
```python
class TabularSampler(Protocol):
    engine: str                                                     # "copula" | "mostly"
    @property
    def columns(self) -> list[str]: ...                             # column NAMES the sampler writes
    def sample_row(self, anchor: str) -> dict[str, Any]: ...        # contract §6.6
    def sample_rows(self, anchors: Sequence[str], *, attempt: int = 0) -> list[dict[str, Any]]: ...
    def row_digest(self, row: Mapping[str, Any]) -> int: ...
    def is_rejected(self, row: Mapping[str, Any], *, extra: AbstractSet[int] = frozenset()) -> bool: ...
    def to_bytes(self) -> bytes: ...
```
`BatchTransformer.__init__` gains two defaulted keyword arguments: `constraints: ConstraintSet | None = None`, `max_resample: int = 8`. Behaviour when `tabular` is set (synthesize mode only; `validate_run` already forbids it elsewhere):

1. In the existing per-row loop, keep the row anchor M2 computes for `ApplyOptions(anchor=...)` (contract §6.2) in a list `anchors`, and the integer key in `key_ints` (`int(row[key])` when `shape.key` is one column of kind `int`, or `decimal` with scale 0; else `None`).
2. After the output rows are converted back with `from_engine_text` and **before** `verify_output`, call `self._apply_tabular(rows, out_rows, anchors, key_ints)`.
3. `values_by_method["copula"]` (or `"mostly"`) += non-null sampled values; `values_by_method[f"{engine}_resample"]` += rejections; `TABULAR_RESAMPLES.inc(rejections)`.

```python
    def _apply_tabular(self, sources: list[Row], outputs: list[Row], anchors: list[str],
                       key_ints: list[int | None]) -> tuple[int, int]:
        """Overwrite the sampler's columns with synthetic values, enforce constraints, and redraw any
        row equal to a real row (fit sample, holdout, or a source row of this batch) on every
        non-key column. More than ``max_resample`` redraws fails the batch closed."""
        sampler = self._tabular
        assert sampler is not None
        sampled = sampler.sample_rows(anchors)
        batch_digests = frozenset(sampler.row_digest(src) for src in sources)
        written = rejections = 0
        for i, out in enumerate(outputs):
            candidate, attempt = sampled[i], 0
            while True:
                out.update(candidate)
                if self._constraints is not None:
                    self._constraints.apply(out, key_int=key_ints[i])
                if not sampler.is_rejected(out, extra=batch_digests):
                    break
                attempt += 1
                if attempt > self._max_resample:
                    raise LeakageError("exact_row_match")
                rejections += 1
                candidate = sampler.sample_rows([anchors[i]], attempt=attempt)[0]
            written += sum(1 for v in candidate.values() if v is not None)
        return written, rejections
```
`LeakageError` is M2's batch error (contract §6.4: "any hit raises `LeakageError`"); its message is the code only, and the run fails with `error_code="leakage"`.

- [ ] **Step 1: Write the failing tests**

`tests/test_batch_tabular.py`:
```python
from __future__ import annotations

from collections.abc import Mapping, Sequence, Set
from decimal import Decimal
from typing import Any

import pytest

pytest.importorskip("scipy")

from dps.config import Settings  # noqa: E402
from dps.engine.batch import BatchTransformer, ColumnPlan, LeakageError  # noqa: E402
from dps.engine.findings import hash_value  # noqa: E402
from dps.engine.metrics import row_digest  # noqa: E402
from dps.engine.policy import MethodSpec, TabularConstraint, load_preset  # noqa: E402
from dps.engine.service import PrivacyEngine  # noqa: E402
from dps.engine.synth.constraints import ColumnSpec, ConstraintSet  # noqa: E402
from dps.engine.synth.surrogates import generate_synthesis_seed  # noqa: E402
from dps.layers.connectors.base import ColumnInfo, ObjectRef, TableShape  # noqa: E402

KEY = b"d" * 32


def _anchor(i: int) -> str:
    """Contract §6.2: no anchor column → ``row:<hash_value(canonical key text)>``."""
    return f"row:{hash_value(str(i))}"


class FakeSampler:
    engine = "copula"

    def __init__(self, *, echo: Sequence[dict[str, Any]] = (), always_echo: bool = False,
                 reject_rows: Sequence[Mapping[str, Any]] = ()) -> None:
        self._echo, self._always = {_anchor(i): r for i, r in enumerate(echo)}, always_echo
        self.reject_columns = ["BALANCE", "SEGMENT"]
        self._reject = {row_digest(r, self.reject_columns, KEY) for r in reject_rows}

    @property
    def columns(self) -> list[str]:
        return ["BALANCE", "SEGMENT"]

    def sample_rows(self, anchors: Sequence[str], *, attempt: int = 0) -> list[dict[str, Any]]:
        out = []
        for a in anchors:
            if a in self._echo and (attempt == 0 or self._always):
                out.append({k: self._echo[a][k] for k in self.columns})
            else:
                out.append({"BALANCE": Decimal(len(a) * 10 + attempt), "SEGMENT": "SYN"})
        return out

    def sample_row(self, anchor: str) -> dict[str, Any]:
        return self.sample_rows([anchor])[0]

    def row_digest(self, row: Mapping[str, Any]) -> int:
        return row_digest(row, self.reject_columns, KEY)

    def is_rejected(self, row: Mapping[str, Any], *, extra: Set[int] = frozenset()) -> bool:
        d = self.row_digest(row)
        return d in self._reject or d in extra

    def to_bytes(self) -> bytes:
        return b""


def _shape(with_name: bool) -> TableShape:
    cols = [ColumnInfo(path="$.ID", name="ID", data_type="integer", kind="int", nullable=False, is_key=True),
            ColumnInfo(path="$.BALANCE", name="BALANCE", data_type="numeric(12,2)", kind="decimal", scale=2),
            ColumnInfo(path="$.SEGMENT", name="SEGMENT", data_type="text", kind="text")]
    if with_name:
        cols.insert(1, ColumnInfo(path="$.FULL_NAME", name="FULL_NAME", data_type="text", kind="text", length=120))
    return TableShape(object=ObjectRef(owner="bank", name="customers"), columns=cols, key=["ID"],
                      key_source="primary_key")


def _transformer(sampler: FakeSampler | None, *, with_name: bool = True, constraints: ConstraintSet | None = None,
                 max_resample: int = 8) -> BatchTransformer:
    settings = Settings(_env_file=None, llm_provider="stub", ner_enabled=False)
    plans = [ColumnPlan(path="$.FULL_NAME", entity_type="PERSON_NAME", method=MethodSpec(method="surrogate"),
                        text_column=False, reversible=False, kind="text", length=120)] if with_name else []
    return BatchTransformer(PrivacyEngine(settings), policy=load_preset("synthetic-realistic"), mode="synthesize",
                            columns=plans, shape=_shape(with_name), key=None, seed=generate_synthesis_seed(),
                            locale="en_US", tabular=sampler, constraints=constraints, max_resample=max_resample)


def _rows(n: int, with_name: bool = True) -> list[dict[str, Any]]:
    rows = [{"ID": i, "BALANCE": Decimal(f"{100 + i}.50"), "SEGMENT": "MASS"} for i in range(n)]
    if with_name:
        for r in rows:
            r["FULL_NAME"] = "Ananya Krishnan"
    return rows


def test_sampler_writes_tabular_columns_and_surrogates_the_rest() -> None:
    result = _transformer(FakeSampler()).transform(_rows(50))
    assert all(r["SEGMENT"] == "SYN" and r["FULL_NAME"] != "Ananya Krishnan" for r in result.rows)
    assert [r["ID"] for r in result.rows] == list(range(50))
    assert result.values_by_method["copula"] == 100 and result.values_by_method.get("copula_resample", 0) == 0


def test_output_is_deterministic_across_batch_boundaries() -> None:
    t = _transformer(FakeSampler())
    whole = t.transform(_rows(20)).rows
    split = t.transform(_rows(20)[:7]).rows + t.transform(_rows(20)[7:]).rows
    assert [(r["BALANCE"], r["SEGMENT"]) for r in whole] == [(r["BALANCE"], r["SEGMENT"]) for r in split]


def test_a_row_equal_to_its_source_is_redrawn() -> None:
    source = _rows(3, with_name=False)
    result = _transformer(FakeSampler(echo=source), with_name=False).transform(source)
    assert all(not (r["BALANCE"] == s["BALANCE"] and r["SEGMENT"] == s["SEGMENT"]) for r, s in zip(result.rows, source, strict=True))
    assert result.values_by_method["copula_resample"] == 3


def test_fails_closed_after_max_resample() -> None:
    source = _rows(3, with_name=False)
    with pytest.raises(LeakageError) as err:
        _transformer(FakeSampler(echo=source, always_echo=True), with_name=False, max_resample=2).transform(source)
    assert str(err.value) == "exact_row_match"


def test_constraints_run_after_sampling() -> None:
    cs = ConstraintSet([TabularConstraint.model_validate({"path": "$.SEGMENT", "when": [
        {"if": {"path": "$.BALANCE", "op": ">=", "value": 0}, "then": "RULED"}]})],
        {"SEGMENT": ColumnSpec("SEGMENT", "categorical"), "BALANCE": ColumnSpec("BALANCE", "decimal", scale=2)},
        seed=bytes(32), object_fqn="bank.customers")
    result = _transformer(FakeSampler(), constraints=cs).transform(_rows(5))
    assert {r["SEGMENT"] for r in result.rows} == {"RULED"}


def test_without_a_sampler_nothing_changes() -> None:
    result = _transformer(None).transform(_rows(5))
    assert [r["BALANCE"] for r in result.rows] == [r["BALANCE"] for r in _rows(5)]
    assert "copula" not in result.values_by_method
```
The echo tests rely on the contract's anchor form for a table without an anchor column (§6.2): `row:<hash_value("|".join(to_engine_text(v) for v in key))>`, which for an integer key `i` is `row:<hash_value(str(i))>`.

- [ ] **Step 2: Run to confirm failure**

Run: `pytest -q tests/test_batch_tabular.py`
Expected: FAIL with `TypeError: BatchTransformer.__init__() got an unexpected keyword argument 'constraints'`.

- [ ] **Step 3: Implement** the two kwargs, `_apply_tabular` (code above), the anchor/key-int collection and the counters in `dps/engine/batch.py`; extend the `TabularSampler` protocol there. Contract §6.6: replace the protocol block with the extended one above and add the sentence "`BatchTransformer` takes `constraints: ConstraintSet | None = None` and `max_resample: int = 8`; with a sampler it overwrites the sampler's columns, applies constraints, and redraws (salted by `attempt`) any row equal on every non-key column to a fit/holdout row or a source row of the batch; more than `max_resample` redraws raises `LeakageError("exact_row_match")`."

- [ ] **Step 4: Run tests, lint, types**

Run: `pytest -q tests/test_batch_tabular.py && pytest -q && ruff check dps tests && mypy dps`
Expected: `6 passed`; the M2 batch suite still green.

- [ ] **Step 5: Commit**

```bash
git add dps/engine/batch.py tests/test_batch_tabular.py docs/superpowers/plans/2026-10-11-m1-m5-contract.md
git commit -m "feat(batch): tabular sampling with constraints and in-flight exact-row rejection" -- dps/engine/batch.py tests/test_batch_tabular.py docs/superpowers/plans/2026-10-11-m1-m5-contract.md
```

---

### Task 6: L2 synthesize jobs — fit, preview, measure, gate, seal; evidence

**Files:**
- Create: `dps/layers/connectors/tabular.py`
- Modify (M2 touch points): `dps/jobs.py` (`reseal_secret`), `dps/jobs_models.py` (`JobProgress.metrics`, `validate_run`), `dps/layers/connectors/jobs_run.py` (`prepare`, `run_partition`), `dps/layers/connectors/evidence.py`, `docs/superpowers/plans/2026-10-11-m1-m5-contract.md` (§3.3, §5.3, §7.1)
- Test: `tests/test_tabular_prepare.py` (offline), `tests/integration/test_synthesize_copula.py` (`db`, `pg_source`, `integration`)

Public interface of `dps/layers/connectors/tabular.py`:
```python
MIN_ROWS = 50
KIND_MAP: dict[str, str]   # connector kind → tabular kind: int, decimal, float, date, timestamp, bool; text/char → categorical
QI_ENTITIES: dict[str, str]  # DATE_OF_BIRTH→decade, AGE→band10, GENDER/CITY/NATIONALITY→exact, POSTAL_CODE→prefix3

class TabularColumn(BaseModel):
    path: str; name: str; kind: str; scale: int | None = None; precision: int | None = None; shape: str | None = None
class TabularGateError(Exception):
    code: str; detail: str           # code ∈ tabular_invalid | leakage | privacy_gate | incompatible_method
class TabularPrepared(BaseModel):
    engine: Literal["copula", "mostly"]; model_b64: str; metrics: dict[str, Any]

def tabular_columns(shape: TableShape, snapshot: ClassificationSnapshot, *, max_distinct_ratio: float = 0.5
                    ) -> tuple[list[TabularColumn], list[dict[str, str]]]
def split_holdout(rows: list[Row], anchors: list[str], seed: bytes, *, max_holdout: int) -> tuple[list[Row], list[Row]]
def build_constraints(policy: Policy, columns: list[TabularColumn], *, seed: bytes, object_fqn: str) -> ConstraintSet | None
def fit_sampler(engine: str, train: list[Row], columns: list[TabularColumn], *, seed: bytes, settings: Settings,
                reject_rows: list[Row], reject_columns: list[str]) -> TabularSampler
def load_sampler(engine: str, model_b64: str, seed: bytes) -> TabularSampler        # LRU (8), keyed by digests
def measure(*, real_rows, train_rows, holdout_rows, preview_source, synth_rows, columns, qi, reject_columns,
            settings, engine, preview_resamples) -> dict[str, Any]
async def prepare_tabular(*, settings, engine_name, connector, shape, selection, params, snapshot, policy,
                          seed: bytes, object_fqn: str,
                          make_transformer: Callable[[TabularSampler, ConstraintSet | None], BatchTransformer],
                          anchor_of: Callable[[Row], str]) -> TabularPrepared
```

Eligibility (`tabular_columns`): a column is modelled when its path is top-level (`$.NAME`), it is not a key column, the snapshot classifies it non-sensitive (`entity_type is None`) and not `text_column`, its kind is in `KIND_MAP`, and — for categorical kinds — `distinct_ratio <= max_distinct_ratio`. Non-sensitive columns that fail are returned in `excluded` with `reason` ∈ `kind:<kind>`, `high_cardinality`, `nested`; they keep their source values (the evidence lists them). Sensitive columns are not listed: they get surrogates as before.

`measure` returns (value-free: numbers, column paths, counts):
```python
{"fidelity": {**fidelity_report(...), "engine": engine,
              "measured_on": {"real_rows": n_real, "synthetic_rows": n_preview, "holdout_rows": n_holdout,
                              "basis": "deterministic preview of the rows this job writes"}},
 "privacy": {"enforced": True, "exact_row_matches": int, "exact_row_resamples": preview_resamples,
             "nndr": {**nndr(...), "target": settings.privacy_nndr_target, "passed": bool},
             "qi_uniqueness": {"columns": [paths], "source": float, "synthetic": float, "unique_overlap": int},
             "membership_inference": {"run": False, "reason": "not available for the built-in copula engine"}},
 "tabular": {"engine": engine, "columns": [paths], "excluded": [...], "constraints": [...],
             "constraints_skipped": [...], "fit_rows": int, "holdout_rows": int, "preview_rows": int}}
```

- [ ] **Step 1: Write the failing offline tests**

`tests/test_tabular_prepare.py`:
```python
from __future__ import annotations

import base64
import json
from decimal import Decimal
from typing import Any

import numpy as np
import pytest

pytest.importorskip("scipy")

from dps.config import Settings  # noqa: E402
from dps.engine.batch import BatchTransformer, ColumnPlan  # noqa: E402
from dps.engine.findings import hash_value  # noqa: E402
from dps.engine.policy import MethodSpec, load_preset  # noqa: E402
from dps.engine.service import PrivacyEngine  # noqa: E402
from dps.engine.synth.surrogates import generate_synthesis_seed, seed_bytes  # noqa: E402
from dps.jobs_models import ClassificationSnapshot, ColumnClassification  # noqa: E402  (Task 0 EITHER)
from dps.layers.connectors.base import ColumnInfo, ObjectRef, TableShape  # noqa: E402
from dps.layers.connectors.tabular import (  # noqa: E402
    TabularGateError,
    load_sampler,
    prepare_tabular,
    split_holdout,
    tabular_columns,
)

SENTINEL_NAMES = ["Zebulon Quixote", "Ottoline Varga"]


def _col(name: str, kind: str, **kw: Any) -> ColumnInfo:
    return ColumnInfo(path=f"$.{name}", name=name, data_type=kind, kind=kind, **kw)  # type: ignore[arg-type]


SHAPE = TableShape(object=ObjectRef(owner="bank", name="customers"), key=["ID"], key_source="primary_key",
                   columns=[_col("ID", "int", is_key=True, nullable=False), _col("FULL_NAME", "text"),
                            _col("BALANCE", "decimal", scale=2), _col("SEGMENT", "text"), _col("NOTE", "text"),
                            _col("PHOTO", "binary")])


def _cc(name: str, kind: str, entity: str | None, distinct: float = 0.01, text: bool = False) -> ColumnClassification:
    method = MethodSpec(method="surrogate" if entity else "keep")
    return ColumnClassification(path=f"$.{name}", name=name, data_type=kind, kind=kind, is_key=name == "ID",
                                entity_type=entity, confidence=0.9, sensitivity=None, detectors=[], value_hit_rate=0.0,
                                null_rate=0.0, distinct_ratio=distinct, shapes=[], text_column=text, anchor=False,
                                suggested=method, method=method)


SNAPSHOT = ClassificationSnapshot.model_construct(
    id="snap_x", tenant_id="t", job_id=None, connection_id="c", engine="postgres", object_ref="bank.customers",
    status="accepted", policy_name="synthetic-realistic", policy_hash="h", shape=SHAPE, sample_rows=600,
    overrides=[], content_hash="c", created_at=None, accepted_at=None, accepted_by=None,
    columns=[_cc("ID", "int", None, 1.0), _cc("FULL_NAME", "text", "PERSON_NAME", 0.9),
             _cc("BALANCE", "decimal", None, 0.95), _cc("SEGMENT", "text", None, 0.003),
             _cc("NOTE", "text", None, 0.9), _cc("PHOTO", "binary", None, 1.0)])


def _rows(n: int) -> list[dict[str, Any]]:
    rng = np.random.default_rng(3)
    bal = rng.lognormal(8, 1, n)
    return [{"ID": i, "FULL_NAME": SENTINEL_NAMES[i % 2], "BALANCE": Decimal(f"{b:.2f}"),
             "SEGMENT": "AFFLUENT" if b > 3000 else "MASS", "NOTE": f"n{i}", "PHOTO": b"\x00"} for i, b in enumerate(bal)]


class FakeConnector:
    engine = "postgres"

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    async def sample(self, shape: TableShape, selection: Any, params: Any, n: int) -> list[dict[str, Any]]:
        return self.rows[:n]


def _settings(**kw: Any) -> Settings:
    base = dict(_env_file=None, llm_provider="stub", ner_enabled=False, copula_fit_rows=400, copula_holdout_rows=100,
                copula_metrics_rows=300)
    base.update(kw)
    return Settings(**base)  # type: ignore[arg-type]


def _anchor(row: dict[str, Any]) -> str:
    return f"row:{hash_value(str(row['ID']))}"


async def _prepare(rows: list[dict[str, Any]], **kw: Any):  # noqa: ANN202
    settings = _settings(**kw)
    seed = generate_synthesis_seed()
    plans = [ColumnPlan(path="$.FULL_NAME", entity_type="PERSON_NAME", method=MethodSpec(method="surrogate"),
                        text_column=False, reversible=False, kind="text", length=120)]

    def make(sampler, constraints):  # noqa: ANN001, ANN202
        return BatchTransformer(PrivacyEngine(settings), policy=load_preset("synthetic-realistic"), mode="synthesize",
                                columns=plans, shape=SHAPE, key=None, seed=seed, locale="en_US", tabular=sampler,
                                constraints=constraints)

    out = await prepare_tabular(settings=settings, engine_name="copula", connector=FakeConnector(rows), shape=SHAPE,
                                selection=None, params={}, snapshot=SNAPSHOT, policy=load_preset("synthetic-realistic"),
                                seed=seed_bytes(seed), object_fqn="bank.customers", make_transformer=make,
                                anchor_of=_anchor)
    return out, seed


def test_column_eligibility() -> None:
    cols, excluded = tabular_columns(SHAPE, SNAPSHOT)
    assert [c.path for c in cols] == ["$.BALANCE", "$.SEGMENT"] and cols[0].kind == "decimal" and cols[0].scale == 2
    assert excluded == [{"path": "$.NOTE", "reason": "high_cardinality"}, {"path": "$.PHOTO", "reason": "kind:binary"}]


def test_holdout_split_is_deterministic_and_bounded() -> None:
    rows = _rows(1000)
    anchors = [_anchor(r) for r in rows]
    train, holdout = split_holdout(rows, anchors, b"s" * 32, max_holdout=150)
    assert len(holdout) <= 150 and len(train) + len(holdout) == 1000
    assert split_holdout(rows, anchors, b"s" * 32, max_holdout=150)[1] == holdout
    assert {r["ID"] for r in train}.isdisjoint({r["ID"] for r in holdout})


async def test_prepare_returns_a_sealable_model_and_value_free_metrics() -> None:
    prepared, seed = await _prepare(_rows(600))
    m = prepared.metrics
    assert m["privacy"]["exact_row_matches"] == 0 and m["privacy"]["nndr"]["measured"] == 1.0
    assert m["fidelity"]["score"] > 0.8 and set(m["fidelity"]["ks"]) == {"$.BALANCE"}
    assert m["tabular"]["columns"] == ["$.BALANCE", "$.SEGMENT"] and m["tabular"]["preview_rows"] == 300
    text = json.dumps(m)
    assert not any(s in text for s in [*SENTINEL_NAMES, "AFFLUENT", "MASS"])
    sampler = load_sampler("copula", prepared.model_b64, seed_bytes(seed))
    assert sampler.columns == ["BALANCE", "SEGMENT"] and base64.b64decode(prepared.model_b64)
    assert load_sampler("copula", prepared.model_b64, seed_bytes(seed)) is sampler


@pytest.mark.parametrize("rows,kw,code", [(40, {}, "tabular_invalid"),
                                          (600, {"synth_tabular_engine": ""}, "tabular_invalid"),
                                          (600, {"privacy_nndr_enforce": True, "privacy_nndr_target": 10.0},
                                           "privacy_gate")])
async def test_prepare_gates(rows: int, kw: dict[str, Any], code: str) -> None:
    with pytest.raises(TabularGateError) as err:
        await _prepare(_rows(rows), **kw)
    assert err.value.code == code and not any(s in err.value.detail for s in SENTINEL_NAMES)
```

- [ ] **Step 2: Run to confirm failure**

Run: `pytest -q tests/test_tabular_prepare.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'dps.layers.connectors.tabular'`.

- [ ] **Step 3: Write `dps/layers/connectors/tabular.py`**

```python
"""Tabular synthesis for L2 synthesize jobs (contract §6.6, §7.3).

At ``prepare``: sample ≤ fit + holdout rows from the selection, split a deterministic holdout,
fit the engine on the rest, run a deterministic *preview* of the first rows through the real
``BatchTransformer`` (the exact rows the job will write for those sources), measure fidelity and
privacy on it, and gate before anything is written. The fitted model is returned base64-encoded
for the caller to seal into ``JobSecret.tabular_model``; it is never stored unsealed.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
from collections import OrderedDict
from collections.abc import Callable
from typing import Any, Literal

from pydantic import BaseModel

from dps.config import Settings
from dps.engine.batch import BatchTransformer, LeakageError, TabularSampler
from dps.engine.metrics import (
    encode_features,
    exact_row_matches,
    fidelity_report,
    generalize_qi,
    nndr,
    qi_unique_overlap,
    qi_uniqueness,
    row_hash,
)
from dps.engine.policy import Policy
from dps.engine.synth.constraints import ColumnSpec, ConstraintError, ConstraintSet
from dps.engine.synth.copula import CopulaError, GaussianCopula
from dps.engine.synth.personas import seed_int
from dps.jobs_models import ClassificationSnapshot   # Task 0 EITHER: or dps.layers.connectors.discovery
from dps.layers.connectors.base import Connector, Row, TableShape

MIN_ROWS = 50
KIND_MAP = {"int": "int", "decimal": "decimal", "float": "float", "date": "date", "timestamp": "timestamp",
            "bool": "bool", "text": "categorical", "char": "categorical"}
QI_ENTITIES = {"DATE_OF_BIRTH": "decade", "AGE": "band10", "GENDER": "exact", "POSTAL_CODE": "prefix3",
               "CITY": "exact", "NATIONALITY": "exact"}
_CACHE: OrderedDict[tuple[str, str, str], TabularSampler] = OrderedDict()
_CACHE_MAX = 8


class TabularColumn(BaseModel):
    path: str
    name: str
    kind: str
    scale: int | None = None
    precision: int | None = None
    shape: str | None = None


class TabularGateError(Exception):
    """``prepare`` must fail the job with ``code``; ``detail`` is value-free."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code, self.detail = code, detail


class TabularPrepared(BaseModel):
    engine: Literal["copula", "mostly"]
    model_b64: str
    metrics: dict[str, Any]


def _top_level(path: str) -> bool:
    return path.startswith("$.") and "." not in path[2:] and "[" not in path


def tabular_columns(shape: TableShape, snapshot: ClassificationSnapshot, *,
                    max_distinct_ratio: float = 0.5) -> tuple[list[TabularColumn], list[dict[str, str]]]:
    """Modellable columns and the non-sensitive columns left as source values (with reasons)."""
    info = {c.path: c for c in shape.columns}
    keys = set(shape.key)
    out: list[TabularColumn] = []
    excluded: list[dict[str, str]] = []
    for cc in sorted(snapshot.columns, key=lambda c: c.path):
        if cc.is_key or cc.name in keys or cc.entity_type is not None or cc.text_column:
            continue
        column = info.get(cc.path)
        if column is None or not _top_level(cc.path):
            excluded.append({"path": cc.path, "reason": "nested"})
            continue
        kind = KIND_MAP.get(column.kind)
        if kind is None:
            excluded.append({"path": cc.path, "reason": f"kind:{column.kind}"})
            continue
        if kind == "categorical" and cc.distinct_ratio > max_distinct_ratio:
            excluded.append({"path": cc.path, "reason": "high_cardinality"})
            continue
        out.append(TabularColumn(path=cc.path, name=column.name, kind=kind, scale=column.scale,
                                 precision=column.precision, shape=cc.shapes[0] if cc.shapes else None))
    return out, excluded


def split_holdout(rows: list[Row], anchors: list[str], seed: bytes, *, max_holdout: int) -> tuple[list[Row], list[Row]]:
    """~20 % of rows (by a keyed hash of the anchor), capped at ``max_holdout``, never fitted."""
    train: list[Row] = []
    holdout: list[Row] = []
    for row, anchor in zip(rows, anchors, strict=True):
        if len(holdout) < max_holdout and seed_int(seed, f"holdout|{anchor}") % 100 < 20:
            holdout.append(row)
        else:
            train.append(row)
    return train, holdout


def build_constraints(policy: Policy, columns: list[TabularColumn], *, seed: bytes,
                      object_fqn: str) -> ConstraintSet | None:
    if not policy.synthesize.constraints:
        return None
    specs = {c.name: ColumnSpec(c.name, c.kind, c.precision, c.scale, c.shape) for c in columns}
    return ConstraintSet(policy.synthesize.constraints, specs, seed=seed, object_fqn=object_fqn)


def fit_sampler(engine: str, train: list[Row], columns: list[TabularColumn], *, seed: bytes, settings: Settings,
                reject_rows: list[Row], reject_columns: list[str]) -> TabularSampler:
    data = {c.path: [r.get(c.name) for r in train] for c in columns}
    kinds = {c.path: c.kind for c in columns}
    if engine == "mostly":
        from dps.engine.synth.mostly import MostlySampler  # optional extra

        return MostlySampler.fit(data, kinds, seed=seed, pool_rows=settings.mostly_pool_rows,
                                 max_training_minutes=settings.mostly_max_training_minutes,
                                 scales={c.path: c.scale or 0 for c in columns},
                                 reject_rows=reject_rows, reject_columns=reject_columns)
    return GaussianCopula.fit(data, kinds, rare_floor=settings.copula_rare_floor, seed=seed,
                              scales={c.path: c.scale or 0 for c in columns if c.kind == "decimal"},
                              reject_rows=reject_rows, reject_columns=reject_columns)


def load_sampler(engine: str, model_b64: str, seed: bytes) -> TabularSampler:
    """Deserialise (cached per process; the cache key holds digests, never the seed itself)."""
    key = (engine, hashlib.sha256(model_b64.encode()).hexdigest(), hashlib.sha256(seed).hexdigest())
    if key in _CACHE:
        _CACHE.move_to_end(key)
        return _CACHE[key]
    data = base64.b64decode(model_b64)
    if engine == "mostly":
        from dps.engine.synth.mostly import MostlySampler

        sampler: TabularSampler = MostlySampler.from_bytes(data, seed)
    else:
        sampler = GaussianCopula.from_bytes(data, seed)
    _CACHE[key] = sampler
    while len(_CACHE) > _CACHE_MAX:
        _CACHE.popitem(last=False)
    return sampler


def _qi_columns(policy: Policy, snapshot: ClassificationSnapshot,
                columns: list[TabularColumn]) -> list[tuple[str, str, str]]:
    """(path, name, generalisation) for the quasi-identifiers of this table."""
    by_path = {c.path: c for c in snapshot.columns}
    kinds = {c.path: c.kind for c in columns}
    chosen: list[tuple[str, str, str]] = []
    paths = policy.synthesize.quasi_identifiers or [c.path for c in snapshot.columns if c.entity_type in QI_ENTITIES]
    for path in paths:
        cc = by_path.get(path)
        if cc is None or not _top_level(path):
            continue
        how = QI_ENTITIES.get(cc.entity_type or "", "")
        if not how:
            how = "decade" if kinds.get(path) in ("date", "timestamp") else \
                "band10" if kinds.get(path) in ("int", "decimal", "float") else "exact"
        chosen.append((path, cc.name, how))
    return chosen


def _generalised(rows: list[Row], qi: list[tuple[str, str, str]]) -> list[dict[str, Any]]:
    return [{name: generalize_qi(r.get(name), how) for _, name, how in qi} for r in rows]


def measure(*, real_rows: list[Row], train_rows: list[Row], holdout_rows: list[Row], preview_source: list[Row],
            synth_rows: list[Row], columns: list[TabularColumn], qi: list[tuple[str, str, str]],
            reject_columns: list[str], settings: Settings, engine: str, preview_resamples: int) -> dict[str, Any]:
    kinds = {c.path: c.kind for c in columns}

    def by_path(rows: list[Row]) -> dict[str, list[Any]]:
        return {c.path: [r.get(c.name) for r in rows] for c in columns}

    fidelity = fidelity_report(by_path(real_rows), by_path(synth_rows), kinds,
                               column_limit=settings.fidelity_column_limit, corr_limit=settings.fidelity_corr_limit)
    reference = by_path(train_rows)
    train_x = encode_features(reference, kinds)
    synth_x = encode_features(by_path(synth_rows), kinds, reference)
    holdout_x = encode_features(by_path(holdout_rows), kinds, reference) if len(holdout_rows) >= 10 else None
    nn = nndr(train_x, synth_x, sample=2000, seed=0, holdout=holdout_x)
    real_hashes = {row_hash(r, reject_columns) for r in real_rows}
    names = [name for _, name, _ in qi]
    real_qi, synth_qi = _generalised(preview_source, qi), _generalised(synth_rows, qi)
    return {
        "fidelity": {**fidelity, "engine": engine,
                     "measured_on": {"real_rows": len(real_rows), "synthetic_rows": len(synth_rows),
                                     "holdout_rows": len(holdout_rows),
                                     "basis": "deterministic preview of the rows this job writes"}},
        "privacy": {"enforced": True,
                    "exact_row_matches": exact_row_matches(real_hashes, synth_rows, reject_columns),
                    "exact_row_resamples": preview_resamples,
                    "nndr": {**nn, "target": settings.privacy_nndr_target,
                             "passed": bool(nn["measured"]) and nn["ratio"] >= settings.privacy_nndr_target},
                    "qi_uniqueness": {"columns": [p for p, _, _ in qi],
                                      "source": round(qi_uniqueness(real_qi, names), 4),
                                      "synthetic": round(qi_uniqueness(synth_qi, names), 4),
                                      "unique_overlap": qi_unique_overlap(real_qi, synth_qi, names)},
                    "membership_inference": {"run": False, "reason": f"not available for the {engine} engine"}},
    }


async def prepare_tabular(*, settings: Settings, engine_name: str, connector: Connector, shape: TableShape,
                          selection: Any, params: dict[str, Any], snapshot: ClassificationSnapshot, policy: Policy,
                          seed: bytes, object_fqn: str,
                          make_transformer: Callable[[TabularSampler, ConstraintSet | None], BatchTransformer],
                          anchor_of: Callable[[Row], str]) -> TabularPrepared:
    """Fit, preview, measure and gate. Raises :class:`TabularGateError`; never writes."""
    if engine_name not in settings.tabular_engines:
        raise TabularGateError("tabular_invalid", f"tabular engine {engine_name} is not enabled on this deployment")
    columns, excluded = tabular_columns(shape, snapshot)
    if not columns:
        raise TabularGateError("tabular_invalid", "no column qualifies for tabular synthesis")
    rows = await connector.sample(shape, selection, params, settings.copula_fit_rows + settings.copula_holdout_rows)
    if len(rows) < MIN_ROWS:
        raise TabularGateError("tabular_invalid",
                               f"tabular synthesis needs at least {MIN_ROWS} sampled rows, got {len(rows)}")
    anchors = [anchor_of(r) for r in rows]
    train, holdout = split_holdout(rows, anchors, seed, max_holdout=settings.copula_holdout_rows)
    key_names = set(shape.key)
    reject_columns = sorted(c.name for c in shape.columns if c.name not in key_names and _top_level(c.path))
    try:
        sampler = await asyncio.to_thread(fit_sampler, engine_name, train, columns, seed=seed, settings=settings,
                                          reject_rows=rows, reject_columns=reject_columns)
    except CopulaError as exc:
        raise TabularGateError("tabular_invalid", str(exc)) from exc
    try:
        constraints = build_constraints(policy, columns, seed=seed, object_fqn=object_fqn)
        preview = train[: settings.copula_metrics_rows]
        result = await asyncio.to_thread(make_transformer(sampler, constraints).transform, preview)
    except ConstraintError as exc:
        raise TabularGateError("incompatible_method", str(exc)) from exc
    except LeakageError as exc:
        raise TabularGateError("leakage", "the synthetic preview failed the leakage sweep; nothing was written") from exc
    metrics = await asyncio.to_thread(
        measure, real_rows=rows, train_rows=train, holdout_rows=holdout, preview_source=preview,
        synth_rows=result.rows, columns=columns, qi=_qi_columns(policy, snapshot, columns),
        reject_columns=reject_columns, settings=settings, engine=engine_name,
        preview_resamples=int(result.values_by_method.get(f"{engine_name}_resample", 0)))
    metrics["tabular"] = {"engine": engine_name, "columns": [c.path for c in columns], "excluded": excluded,
                          "constraints": constraints.describe() if constraints else [],
                          "constraints_skipped": constraints.skipped if constraints else [],
                          "fit_rows": len(train), "holdout_rows": len(holdout), "preview_rows": len(preview)}
    if metrics["privacy"]["exact_row_matches"]:
        raise TabularGateError("leakage", "synthetic preview rows equal real rows")
    if settings.privacy_nndr_enforce and not metrics["privacy"]["nndr"]["passed"]:
        raise TabularGateError("privacy_gate", "nearest-neighbour distance ratio is below the configured target")
    engine_literal: Literal["copula", "mostly"] = "mostly" if engine_name == "mostly" else "copula"
    return TabularPrepared(engine=engine_literal, model_b64=base64.b64encode(sampler.to_bytes()).decode("ascii"),
                           metrics=metrics)
```

- [ ] **Step 4: M2 touch points**

`dps/jobs.py` — add (fenced exactly like `advance_job`):
```python
async def reseal_secret(worker_id: str, job_id: str, sealed_secret: bytes) -> bool:
    """Replace ``key_enc`` while this worker holds the job in ``prepare`` (adds the tabular model)."""
    async with acquire_queue() as conn:
        status = await conn.execute(
            f"UPDATE {_schema()}.dps_job SET key_enc = $3, updated_at = now() "
            "WHERE id = $1 AND locked_by = $2 AND state = 'running' AND phase = 'prepare'",
            job_id, worker_id, sealed_secret)
    return status.endswith(" 1")
```
(`_schema()` is whatever M2's module uses to render the validated schema name; reuse it.)

`dps/jobs_models.py`:
- `JobProgress` gains `metrics: dict[str, Any] = {}` (value-free; written by `prepare`, read by `finalize` and the console).
- `validate_run` gains, after the existing `tabular_invalid` rule:
```python
    if spec.options.tabular != "none":
        if spec.options.tabular not in settings.tabular_engines:
            raise SpecError("tabular_invalid", 422, f"tabular engine {spec.options.tabular} is not enabled")
        if spec.options.tabular == "mostly":
            from dps.engine.synth.mostly import mostly_available
            if not mostly_available():
                raise SpecError("tabular_invalid", 422, "the synth-ml extra is not installed on this deployment")
```
(match M2's `SpecError` constructor; the contract fixes `(code, status)`, the detail argument is M2's.)

`dps/layers/connectors/jobs_run.py`:
- Factor the transformer construction `run_partition` already does into `_make_transformer(ctx, job, spec, policy, shape, columns, key, seed, *, tabular=None, constraints=None) -> BatchTransformer` (pass `max_resample=ctx.settings.copula_max_resample`).
- In `prepare`, after the snapshot/shape/policy are loaded and validated and before partitions are computed:
```python
        if spec.mode == "synthesize" and spec.options.tabular != "none":
            seed = seed_bytes(seed_from_caller(secret.synthesis_seed or {}))
            try:
                prepared = await prepare_tabular(
                    settings=ctx.settings, engine_name=spec.options.tabular, connector=source, shape=shape,
                    selection=spec.source.selection, params=selection_params, snapshot=snapshot, policy=policy,
                    seed=seed, object_fqn=spec.source.object.fqn(),
                    make_transformer=lambda sampler, constraints: _make_transformer(
                        ctx, job, spec, policy, shape, column_plans, None, secret_seed, tabular=sampler,
                        constraints=constraints),
                    anchor_of=row_anchor)
            except TabularGateError as exc:
                await _fail_prepare(ctx, job, exc.code, exc.detail)   # the helper M2 uses for row_count_mismatch
                return
            secret = secret.model_copy(update={"tabular_model": prepared.model_b64})
            sealed = seal(ctx.kek, secret.model_dump_json().encode("utf-8"), aad=f"dps_job.key:{job.id}")
            if not await jobs.reseal_secret(ctx.worker_id, job.id, sealed):
                return  # lease lost: the reaper re-queues prepare, which refits deterministically
            progress_update["metrics"] = prepared.metrics
            logger.info("tabular_prepared", job_id=job.id, engine=prepared.engine,
                        columns=len(prepared.metrics["tabular"]["columns"]))
```
  `source`, `shape`, `snapshot`, `policy`, `column_plans`, `secret`, `secret_seed`, `selection_params`, `row_anchor`, `progress_update` and `_fail_prepare` are the objects M2's `prepare` already holds under whatever names it uses; bind to them, do not re-open connections.
- In `run_partition`, when building the transformer:
```python
        tabular = constraints = None
        if secret.tabular_model:
            seed = seed_bytes(seed_from_caller(secret.synthesis_seed or {}))
            tabular = load_sampler(spec.options.tabular, secret.tabular_model, seed)
            constraints = build_constraints(policy, tabular_columns(shape, snapshot)[0], seed=seed,
                                            object_fqn=spec.source.object.fqn())
```
  and pass both to `_make_transformer`. A `ConstraintError` raised by `transform` fails the partition with `error_code="incompatible_method"`.

`dps/layers/connectors/evidence.py` (additive keys; the contract field types already allow dicts):
- `columns[*]` gains `data_type`, `confidence`, `override`, `params` from the accepted snapshot's `ColumnClassification` for that path.
- `policy` gains `"version": str(policy.version)`.
- synthesize packs: `fidelity = progress.metrics.get("fidelity")`; `privacy = progress.metrics.get("privacy")`; when `privacy` is set add `privacy["exact_row_resamples_run"] = sum of values_by_method["copula_resample"|"mostly_resample"]` over partitions; `EvidencePack` gains `tabular: dict[str, Any] | None = None` = `progress.metrics.get("tabular")`. When `options.tabular == "none"` set `privacy = {"engine": "none", "measured": False, "reason": "options.tabular=none: non-sensitive columns were copied from the source"}` and `fidelity = None`.

Contract edits (same commit): §3.3 `JobProgress` gains `metrics: dict[str, Any] = {}`; §3.2 adds `async def reseal_secret(worker_id: str, job_id: str, sealed_secret: bytes) -> bool  # prepare only, fenced`; §5.3 prepare-time codes gain `privacy_gate` and `tabular_invalid` (prepare-time use); §7.1 `EvidencePack` gains `tabular: dict[str, Any] | None`, `columns[*]` gains `data_type, confidence, override, params`, `policy` gains `version`, and the `fidelity`/`privacy` comments become "M5: see Task 6 of the M5 plan for the full shape (superset of the keys listed)"; §7.3 adds `nndr(..., holdout: np.ndarray | None = None)` and the helpers `row_hash`, `row_digest`, `encode_features`, `qi_unique_overlap`, `generalize_qi`, `fidelity_report`.

- [ ] **Step 5: Write the integration test**

`tests/integration/test_synthesize_copula.py` (markers `db`, `pg_source`, `integration`; fixtures from contract §12.2: `api_client`, `source_pg`, `migrated_db`). Flow, all through the API with `options.tabular="copula"`:
1. `POST /connections` for `source-pg` (`bank` database, inline demo credentials, roles `source,target`); `POST /jobs/discover` for `public.customers`; run the in-process worker (the M2 suites' helper; if none is exported, `asyncio.create_task(Consumer(settings, kinds=(JobKind.discover, JobKind.run), handlers=HANDLERS, partition_handler=run_partition, concurrency=2).run())`, cancelled at teardown) until the discover job succeeds; `POST /snapshots/{id}/accept`.
2. Create a scratch schema `synth_<8 hex>` in `bank`; `POST /jobs/run` with `mode="synthesize"`, `target={"write_mode": "table", "connection_id": <same>, "object": {"owner": "synth_x", "name": "customers"}}`, `options={"tabular": "copula"}`, `purpose`; keep the returned `synthesis_seed`.
3. Assertions — `test_copula_job_writes_measured_synthetic_rows`:
   - job `succeeded`; `GET /evidence?job_id=` pack has `fidelity.score >= 0.85`, `privacy.exact_row_matches == 0`, `privacy.nndr.measured == 1.0`, `tabular.engine == "copula"`, `label == "synthetic — measured, not claimed anonymous"`;
   - `SELECT count(*)` of target == source selection count;
   - `SELECT count(*) FROM (SELECT <non-key cols> FROM public.customers INTERSECT SELECT <non-key cols> FROM synth_x.customers) q` == 0;
   - `dps_job.key_enc IS NULL` after finalize;
   - the pack JSON contains none of 5 source values read beforehand (names, segment labels).
4. `test_same_seed_reproduces_the_same_target` — re-run into `synth_y` with `consistency.synthesis_seed` = the returned seed: `md5(string_agg(row::text, '|' ORDER BY id))` equal for both targets.
5. Drop both scratch schemas at teardown.

- [ ] **Step 6: Run tests**

Run: `pytest -q tests/test_tabular_prepare.py` → `6 passed`. With `docker compose --profile sources up -d dps-db source-pg` (or `tools/scratch_pg.sh start` for `db` plus the `source-pg` container): `DPS_TEST_REQUIRE_SERVICES=1 pytest -q tests/integration/test_synthesize_copula.py` → `2 passed` (~40 s). Then the full unit gate.

- [ ] **Step 7: Commit**

```bash
git add dps/layers/connectors/tabular.py dps/jobs.py dps/jobs_models.py dps/layers/connectors/jobs_run.py dps/layers/connectors/evidence.py tests/test_tabular_prepare.py tests/integration/test_synthesize_copula.py docs/superpowers/plans/2026-10-11-m1-m5-contract.md
git commit -m "feat(connectors): copula-backed synthesize jobs — fit, preview, measure and gate at prepare; evidence fidelity and privacy" -- dps/layers/connectors/tabular.py dps/jobs.py dps/jobs_models.py dps/layers/connectors/jobs_run.py dps/layers/connectors/evidence.py tests/test_tabular_prepare.py tests/integration/test_synthesize_copula.py docs/superpowers/plans/2026-10-11-m1-m5-contract.md
```

---

### Task 7: Optional MOSTLY AI engine (`synth-ml` extra)

**Files:**
- Create: `dps/engine/synth/mostly.py`
- Test: `tests/test_mostly.py`

The SDK trains locally (TabularARGN) and is not deterministic per anchor, so the adapter trains once in `prepare`, generates a pool of `mostly_pool_rows` synthetic rows, seals the pool as the model, and maps each row anchor to a pool row with a keyed hash. Limitation recorded in the evidence (`tabular.pool_rows`): with more source rows than pool rows, synthetic rows repeat; fidelity and privacy are measured on the preview exactly as for the copula. The adapter never imports `mostlyai` at module import time.

Interface:
```python
class TabularUnavailable(RuntimeError)
def mostly_available() -> bool
Trainer = Callable[[dict[str, list[Any]], int, int], list[dict[str, Any]]]     # (columns by name, size, minutes) → rows
class MostlySampler:                                     # implements TabularSampler
    engine: ClassVar[str] = "mostly"
    @classmethod
    def fit(cls, columns: dict[str, list[Any]], kinds: dict[str, str], *, seed: bytes, pool_rows: int,
            max_training_minutes: int, scales: Mapping[str, int] | None = None,
            reject_rows: Sequence[Mapping[str, Any]] = (), reject_columns: Sequence[str] = (),
            trainer: Trainer | None = None) -> MostlySampler
    columns: list[str]; pool_rows: int
    def sample_rows(self, anchors, *, attempt=0) -> list[dict[str, Any]]; def sample_row(self, anchor) -> dict
    def row_digest(self, row) -> int; def is_rejected(self, row, *, extra=frozenset()) -> bool
    def to_bytes(self) -> bytes; @classmethod def from_bytes(cls, data: bytes, seed: bytes) -> MostlySampler
```

- [ ] **Step 1: Write the failing tests**

`tests/test_mostly.py`:
```python
from __future__ import annotations

import builtins
from datetime import date
from decimal import Decimal
from typing import Any

import pytest

import dps.engine.synth.mostly as mostly_module
from dps.engine.synth.mostly import MostlySampler, TabularUnavailable, _sdk_trainer, mostly_available

SEED = bytes(range(32))
COLS = {"$.BALANCE": [Decimal(f"{i}.25") for i in range(300)], "$.SEGMENT": ["A", "B", "C"] * 100,
        "$.OPENED": [date(2020, 1, 1 + i % 28) for i in range(300)]}
KINDS = {"$.BALANCE": "decimal", "$.SEGMENT": "categorical", "$.OPENED": "date"}


def fake_trainer(data: dict[str, list[Any]], size: int, minutes: int) -> list[dict[str, Any]]:
    # what pandas hands back: floats for decimals, ISO strings/Timestamps for dates, NaN for nulls
    return [{"BALANCE": float(i) + 0.125, "SEGMENT": "B", "OPENED": "2021-06-0%d" % (1 + i % 9)} for i in range(size)]


def test_pool_sampler_is_deterministic_and_typed() -> None:
    s = MostlySampler.fit(COLS, KINDS, seed=SEED, pool_rows=50, max_training_minutes=1,
                          scales={"$.BALANCE": 2}, trainer=fake_trainer)
    rows = s.sample_rows(["a", "b", "c"])
    assert rows == s.sample_rows(["a", "b", "c"]) and s.columns == ["BALANCE", "OPENED", "SEGMENT"]
    assert isinstance(rows[0]["BALANCE"], Decimal) and rows[0]["BALANCE"].as_tuple().exponent == -2
    assert isinstance(rows[0]["OPENED"], date) and rows[0]["SEGMENT"] == "B" and s.pool_rows == 50
    assert len({r["BALANCE"] for r in s.sample_rows([f"k{i}" for i in range(200)])}) > 10  # the pool is spread


def test_round_trip_and_reject_set() -> None:
    real = [{"BALANCE": COLS["$.BALANCE"][i], "SEGMENT": COLS["$.SEGMENT"][i], "OPENED": COLS["$.OPENED"][i]}
            for i in range(300)]
    s = MostlySampler.fit(COLS, KINDS, seed=SEED, pool_rows=20, max_training_minutes=1, trainer=fake_trainer,
                          reject_rows=real, reject_columns=["BALANCE", "OPENED", "SEGMENT"])
    again = MostlySampler.from_bytes(s.to_bytes(), SEED)
    assert again.sample_rows(["x", "y"]) == s.sample_rows(["x", "y"]) and again.is_rejected(real[4])


def test_missing_sdk_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    real_import = builtins.__import__

    def deny(name: str, *a: Any, **k: Any) -> Any:
        if name.startswith("mostlyai"):
            raise ImportError(name)
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", deny)
    monkeypatch.setattr(mostly_module, "_find_spec", lambda name: None)
    assert mostly_available() is False
    with pytest.raises(TabularUnavailable):
        _sdk_trainer({"A": [1, 2]}, 10, 1)


@pytest.mark.ml
def test_real_sdk_trains_and_generates() -> None:
    pytest.importorskip("mostlyai")
    pytest.importorskip("pandas")
    data = {"BALANCE": [float(i % 97) for i in range(400)], "SEGMENT": ["A", "B"] * 200}
    rows = _sdk_trainer(data, 25, 1)
    assert len(rows) == 25 and set(rows[0]) == {"BALANCE", "SEGMENT"}
```

- [ ] **Step 2: Run to confirm failure**

Run: `pytest -q tests/test_mostly.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'dps.engine.synth.mostly'`.

- [ ] **Step 3: Write `dps/engine/synth/mostly.py`**

```python
"""Optional MOSTLY AI engine (spec §5.6, D7; ``synth-ml`` extra, Apache-2.0 SDK, local mode).

Trains once per job on the fit sample, generates a pool, and serves rows from the pool by a keyed
hash of the row anchor (deterministic and resumable like the copula). The pool is synthetic but is
derived from real data, so it is sealed with the job KEK exactly like the copula model.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import tempfile
import zlib
from collections.abc import Callable, Mapping, Sequence, Set
from datetime import date, datetime
from decimal import Decimal
from typing import Any, ClassVar

from dps.engine.metrics import row_digest

FORMAT = "dps.mostly.v1"
Trainer = Callable[[dict[str, list[Any]], int, int], list[dict[str, Any]]]
_find_spec = importlib.util.find_spec   # module attribute so tests can simulate a missing extra


class TabularUnavailable(RuntimeError):
    """The synth-ml extra is not installed."""


def mostly_available() -> bool:
    """True when the synth-ml extra is importable (checked without importing torch)."""
    return _find_spec("mostlyai") is not None and _find_spec("pandas") is not None


def _sdk_trainer(data: dict[str, list[Any]], size: int, minutes: int) -> list[dict[str, Any]]:
    """Train a local generator and draw ``size`` rows. The only function that touches the SDK —
    if an SDK release renames a parameter, this is the one place to change (``help(MostlyAI)``)."""
    try:
        import pandas as pd
        from mostlyai.sdk import MostlyAI
    except ImportError as exc:
        raise TabularUnavailable("the synth-ml extra (mostlyai, pandas) is not installed") from exc
    with tempfile.TemporaryDirectory(prefix="dps-mostly-") as workdir:
        mostly = MostlyAI(local=True, local_dir=workdir)
        generator = mostly.train(config={"name": "dps-tabular", "tables": [{
            "name": "rows", "data": pd.DataFrame(data),
            "tabular_model_configuration": {"max_training_time": minutes}}]}, start=True, wait=True)
        frame = mostly.generate(generator, size=size, start=True, wait=True).data()
        return [dict(r) for r in frame.to_dict(orient="records")]


def _coerce(value: Any, kind: str, scale: int) -> Any:
    if value is None or (isinstance(value, float) and math.isnan(value)) or str(value) in ("NaT", "nan", "<NA>"):
        return None
    if kind == "int":
        return int(round(float(value)))
    if kind == "decimal":
        return Decimal(f"{float(value):.{scale}f}")
    if kind == "float":
        return float(value)
    if kind == "bool":
        return value if isinstance(value, bool) else str(value).lower() in ("true", "1")
    if kind in ("date", "timestamp"):
        text = value.isoformat() if hasattr(value, "isoformat") else str(value)
        moment = datetime.fromisoformat(text)
        return moment.date() if kind == "date" else moment
    return str(value)


def _encode(value: Any) -> Any:
    if isinstance(value, Decimal):
        return {"d": str(value)}
    if isinstance(value, datetime):
        return {"t": value.isoformat()}
    if isinstance(value, date):
        return {"D": value.isoformat()}
    return value


def _decode(value: Any) -> Any:
    if isinstance(value, dict):
        if "d" in value:
            return Decimal(value["d"])
        if "t" in value:
            return datetime.fromisoformat(value["t"])
        if "D" in value:
            return date.fromisoformat(value["D"])
    return value


class MostlySampler:
    engine: ClassVar[str] = "mostly"

    def __init__(self, names: list[str], pool: list[list[Any]], *, seed: bytes, reject: set[int],
                 reject_columns: Sequence[str]) -> None:
        self._names, self._pool = names, pool
        self._pick_key = hashlib.blake2b(seed, digest_size=32, person=b"dps.mostly.pick").digest()
        self._reject_key = hashlib.blake2b(seed, digest_size=32, person=b"dps.mostly.rej").digest()
        self._reject = reject
        self.reject_columns = list(reject_columns)

    @classmethod
    def fit(cls, columns: dict[str, list[Any]], kinds: dict[str, str], *, seed: bytes, pool_rows: int,
            max_training_minutes: int, scales: Mapping[str, int] | None = None,
            reject_rows: Sequence[Mapping[str, Any]] = (), reject_columns: Sequence[str] = (),
            trainer: Trainer | None = None) -> MostlySampler:
        names = sorted(p[2:] for p in columns)
        data = {p[2:]: v for p, v in columns.items()}
        raw = (trainer or _sdk_trainer)(data, pool_rows, max_training_minutes)
        pool = [[_coerce(r.get(n), kinds[f"$.{n}"], (scales or {}).get(f"$.{n}", 0)) for n in names] for r in raw]
        if not pool:
            raise TabularUnavailable("the MOSTLY AI generator returned no rows")
        sampler = cls(names, pool, seed=seed, reject=set(), reject_columns=reject_columns)
        sampler._reject = {sampler.row_digest(r) for r in reject_rows} if reject_columns else set()
        return sampler

    @property
    def columns(self) -> list[str]:
        return list(self._names)

    @property
    def pool_rows(self) -> int:
        return len(self._pool)

    def sample_rows(self, anchors: Sequence[str], *, attempt: int = 0) -> list[dict[str, Any]]:
        out = []
        for anchor in anchors:
            digest = hashlib.blake2b(f"{anchor}\x1f{attempt}".encode(), key=self._pick_key, digest_size=8).digest()
            out.append(dict(zip(self._names, self._pool[int.from_bytes(digest, "big") % len(self._pool)], strict=True)))
        return out

    def sample_row(self, anchor: str) -> dict[str, Any]:
        return self.sample_rows([anchor])[0]

    def row_digest(self, row: Mapping[str, Any]) -> int:
        return row_digest(row, self.reject_columns, self._reject_key)

    def is_rejected(self, row: Mapping[str, Any], *, extra: Set[int] = frozenset()) -> bool:
        if not self.reject_columns:
            return False
        d = self.row_digest(row)
        return d in self._reject or d in extra

    def to_bytes(self) -> bytes:
        body = {"format": FORMAT, "names": self._names, "pool": [[_encode(v) for v in r] for r in self._pool],
                "reject": sorted(self._reject), "reject_columns": self.reject_columns}
        return zlib.compress(json.dumps(body, separators=(",", ":")).encode("utf-8"), 6)

    @classmethod
    def from_bytes(cls, data: bytes, seed: bytes) -> MostlySampler:
        body = json.loads(zlib.decompress(data).decode("utf-8"))
        if body.get("format") != FORMAT:
            raise TabularUnavailable("unsupported pool format")
        return cls(body["names"], [[_decode(v) for v in r] for r in body["pool"]], seed=seed,
                   reject=set(body["reject"]), reject_columns=body["reject_columns"])
```
`fit_sampler` (Task 6) passes `scales`; `prepare_tabular` adds `metrics["tabular"]["pool_rows"] = sampler.pool_rows` when the engine is `mostly` (add the line in Task 6's function now). The Dockerfile never installs `synth-ml`; HANDOFF §2 documents `docker build --build-arg EXTRAS=synth-ml` as a bank-side opt-in after the licence and image-size review (torch ≈ 2 GB).

- [ ] **Step 4: Run tests, lint, types**

Run: `pytest -q tests/test_mostly.py && ruff check dps tests && mypy dps`
Expected: `3 passed, 1 skipped` (the `ml` test skips without the extra), clean. Optional, on a machine with the extra: `uv pip install -e ".[synth-ml]" && pytest -q -m ml tests/test_mostly.py` → `1 passed` (≈ 1–2 min).

- [ ] **Step 5: Commit**

```bash
git add dps/engine/synth/mostly.py dps/layers/connectors/tabular.py tests/test_mostly.py
git commit -m "feat(synth): optional MOSTLY AI pool engine behind the synth-ml extra" -- dps/engine/synth/mostly.py dps/layers/connectors/tabular.py tests/test_mostly.py
```

---
### Task 8: `external_tokenize` — the bank tokenization adapter, a mock, and batch pre-passes

**Files:**
- Create: `dps/engine/transform/external.py`, `docker/tokenizer-mock/server.py`
- Modify (M0): `dps/engine/transform/methods.py`, `dps/engine/transform/apply.py`, `dps/engine/planner.py`, `dps/engine/service.py`, `dps/layers/api/common.py`, `dps/layers/api/routers/{detect,transform,policies}.py`, `dps/app.py`; (M2) `dps/engine/batch.py`; `docker-compose.yml`, `HANDOFF.md`, contract §11
- Test: `tests/test_external_tokenize.py`; `tests/test_methods.py` (unchanged assertions must pass)

**Wire contract** (what `HttpTokenizer` speaks; the bank's Voltage/Protegrity/Thales gateway is fronted by an adapter service that exposes it — HANDOFF §8 documents it for the bank's integration team):
```
POST {base}/v1/tokenize    {"profile": str|null, "entity_type": "SSN", "values": [str, ...]}  → 200 {"tokens": [str, ...]}
POST {base}/v1/detokenize  {"profile": str|null, "entity_type": "SSN", "tokens": [str, ...]}  → 200 {"values": [str, ...]}
GET  {base}/health → 200
Auth: "Authorization: Bearer <DPS_TOKENIZER_TOKEN>"; TLS verified with DPS_TOKENIZER_CA_FILE (else DPS_SSL_CERT_FILE, else system CAs).
Order-preserving, same length; ≤ DPS_TOKENIZER_BATCH items per call; deterministic for (profile, entity_type, value).
```
Policy usage: `treat: { SSN: { method: external_tokenize, profile: ssn-fpe } }` — `profile` is passed through; everything else about the token's format is the bank service's business.

Interfaces (`dps/engine/transform/external.py`):
```python
class ExternalServiceError(RuntimeError)          # value-free message
class ExternalTokenizer(Protocol):
    name: str
    def tokenize(self, entity_type: str, values: Sequence[str], *, profile: str | None) -> list[str]: ...
    def detokenize(self, entity_type: str, tokens: Sequence[str], *, profile: str | None) -> list[str]: ...
    def health(self) -> bool: ...
class HttpTokenizer:                               # implements ExternalTokenizer
    def __init__(self, base_url: str, *, token: str = "", verify: bool | str = True, timeout: float = 10.0,
                 batch: int = 500, retries: int = 2, transport: httpx.BaseTransport | None = None) -> None
def build_tokenizer(settings: Settings) -> ExternalTokenizer | None
ExternalCache = dict[tuple[str, str], str]        # (entity_type, plaintext|token) → token|plaintext
```
M0 changes:
- `TransformContext.external: Mapping[tuple[str, str], str] | None = None`; `external_tokenize` gets a reverse function; both read the cache (code below).
- `ApplyOptions.tokenizer: ExternalTokenizer | None = None` and `ApplyOptions.external_cache: ExternalCache | None = None`; `apply_plan` runs a tokenize pre-pass (one call per entity type/profile), `reveal` a detokenize pre-pass, before their loops.
- `build_plan(..., external_enabled: bool = True)`: an `external_tokenize` entry gets `reason += "|external:unconfigured"` when no adapter is configured (the plan preview shows why the request will fail).
- `PrivacyEngine.__init__(..., tokenizer: ExternalTokenizer | None = None)` (default `build_tokenizer(settings)`), passes it in `_apply_options` and `reveal`; wraps `ExternalServiceError` as `TokenizationUnavailable(EngineError)`. `run_engine` maps `TokenizationUnavailable` → **502** `{"detail": "tokenization service unavailable"}` (before the `EngineError` branch).
- Routers run engine calls off the event loop: `call=lambda: asyncio.to_thread(engine.obfuscate, payload, body.policy, body.options)` (same for `detect`, `plan`, `reveal`; `synthesize` stays async).
- M2 `BatchTransformer` pre-tokenizes the batch: for every `ColumnPlan` with `method.method == "external_tokenize"`, collect the batch's non-null engine texts, call `engine.tokenizer.tokenize(...)` once, and pass the result as `ApplyOptions(external_cache=..., tokenizer=engine.tokenizer)` to every row (span values in text columns still go through the per-row pre-pass).

- [ ] **Step 1: Write the failing tests**

`tests/test_external_tokenize.py`:
```python
from __future__ import annotations

import importlib.util
import json
import threading
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from dps.app import create_app
from dps.config import Settings
from dps.engine.service import EngineError, Payload, PrivacyEngine, RequestOptions
from dps.engine.transform.external import ExternalServiceError, HttpTokenizer

MOCK = Path(__file__).resolve().parents[1] / "docker" / "tokenizer-mock" / "server.py"
TOKEN = "test-token"
POLICY = {"preset": "gdpr-pseudonymise", "treat": {"SSN": {"method": "external_tokenize", "profile": "ssn"}}}
RECORD = {"customer_id": "CUST-884120", "ssn": "123-45-6789", "email": "ann.k@example-mail.in"}


@pytest.fixture(scope="module")
def mock_url() -> Iterator[str]:
    spec = importlib.util.spec_from_file_location("tokenizer_mock", MOCK)
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    server = module.make_server("127.0.0.1", 0, token=TOKEN, key="unit-test-key")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def _settings(**kw: object) -> Settings:
    base: dict[str, object] = dict(_env_file=None, llm_provider="stub", ner_enabled=False, fpe_enabled=False)
    base.update(kw)
    return Settings(**base)  # type: ignore[arg-type]


def test_round_trip_against_the_mock_service(mock_url: str) -> None:
    t = HttpTokenizer(mock_url, token=TOKEN)
    tokens = t.tokenize("SSN", ["123-45-6789", "987-65-4321"], profile="ssn")
    assert all(x.startswith("tk_") for x in tokens) and tokens == t.tokenize("SSN", ["123-45-6789", "987-65-4321"], profile="ssn")
    assert t.tokenize("ITIN", ["123-45-6789"], profile="ssn") != tokens[:1]
    assert t.detokenize("SSN", tokens, profile="ssn") == ["123-45-6789", "987-65-4321"] and t.health()


def test_batching_retries_and_response_validation() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(len(body["values"]))
        if len(calls) == 1:
            return httpx.Response(503)
        return httpx.Response(200, json={"tokens": [f"t{v[::-1]}" for v in body["values"]]})

    t = HttpTokenizer("http://svc", batch=2, retries=2, transport=httpx.MockTransport(handler))
    assert len(t.tokenize("SSN", ["a1", "b2", "c3", "d4", "e5"], profile=None)) == 5
    assert calls == [2, 2, 2, 1]  # first call retried once after a 503

    def echo(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"tokens": json.loads(request.content)["values"]})

    with pytest.raises(ExternalServiceError, match="untransformed"):
        HttpTokenizer("http://svc", transport=httpx.MockTransport(echo)).tokenize("SSN", ["123-45-6789"], profile=None)
    short = httpx.MockTransport(lambda r: httpx.Response(200, json={"tokens": []}))
    with pytest.raises(ExternalServiceError, match="malformed"):
        HttpTokenizer("http://svc", transport=short).tokenize("SSN", ["x1"], profile=None)
    refused = httpx.MockTransport(lambda r: httpx.Response(400, text="bad value 123-45-6789"))
    with pytest.raises(ExternalServiceError) as err:
        HttpTokenizer("http://svc", transport=refused).tokenize("SSN", ["123-45-6789"], profile=None)
    assert "HTTP 400" in str(err.value) and "123-45-6789" not in str(err.value)


def test_the_mock_requires_its_bearer_token(mock_url: str) -> None:
    with pytest.raises(ExternalServiceError, match="HTTP 401"):
        HttpTokenizer(mock_url, token="wrong", retries=0).tokenize("SSN", ["123-45-6789"], profile=None)


def test_engine_obfuscates_and_reveals_through_the_service(mock_url: str) -> None:
    engine = PrivacyEngine(_settings(), tokenizer=HttpTokenizer(mock_url, token=TOKEN))
    out = engine.obfuscate(Payload(data=RECORD), POLICY, RequestOptions())
    assert out.result["ssn"].startswith("tk_") and out.report.methods_used["external_tokenize"] == 1
    back = engine.reveal(Payload(data=out.result), out.key_material.model_dump(mode="json"),  # type: ignore[union-attr]
                         [h.model_dump(mode="json") for h in out.reveal_hints])
    assert back.result == RECORD


def test_without_an_adapter_the_request_fails_closed() -> None:
    engine = PrivacyEngine(_settings(), tokenizer=None)
    plan = engine.plan(Payload(data=RECORD), POLICY, RequestOptions(), mode="obfuscate")
    assert any(r["method"] == "external_tokenize" and r["reason"].endswith("external:unconfigured") for r in plan.plan)
    with pytest.raises(EngineError, match="no tokenization adapter"):
        engine.obfuscate(Payload(data=RECORD), POLICY, RequestOptions())


def test_api_maps_an_outage_to_502() -> None:
    settings = _settings(auth_enabled=False, rate_limit_enabled=False)
    engine = PrivacyEngine(settings, tokenizer=HttpTokenizer("http://127.0.0.1:9", retries=0, timeout=0.2))
    with TestClient(create_app(settings, engine=engine)) as client:
        r = client.post("/api/v1/obfuscate", json={"data": RECORD, "policy": POLICY})
    assert r.status_code == 502 and r.json() == {"detail": "tokenization service unavailable"}


def test_batch_transformer_calls_the_service_once_per_batch() -> None:
    from dps.engine.batch import BatchTransformer, ColumnPlan
    from dps.engine.policy import MethodSpec, resolve_policy
    from dps.engine.transform.crypto import generate_key_material
    from dps.layers.connectors.base import ColumnInfo, ObjectRef, TableShape

    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        values = json.loads(request.content)["values"]
        calls.append(len(values))
        return httpx.Response(200, json={"tokens": ["tk_" + v[::-1] for v in values]})

    settings = _settings()
    engine = PrivacyEngine(settings, tokenizer=HttpTokenizer("http://svc", transport=httpx.MockTransport(handler)))
    shape = TableShape(object=ObjectRef(owner="bank", name="people"), key=["ID"], key_source="primary_key",
                       columns=[ColumnInfo(path="$.ID", name="ID", data_type="int", kind="int", is_key=True),
                                ColumnInfo(path="$.SSN", name="SSN", data_type="text", kind="text")])
    plans = [ColumnPlan(path="$.SSN", entity_type="SSN", method=MethodSpec(method="external_tokenize", params={"profile": "ssn"}),
                        text_column=False, reversible=True, kind="text", length=None)]
    t = BatchTransformer(engine, policy=resolve_policy(POLICY, default_name="gdpr-pseudonymise"), mode="obfuscate",
                         columns=plans, shape=shape, key=generate_key_material("key"), seed=None, locale="en_US")
    result = t.transform([{"ID": i, "SSN": f"123-45-{6700 + i}"} for i in range(10)])
    assert calls == [10] and all(r["SSN"].startswith("tk_") for r in result.rows)
```

- [ ] **Step 2: Run to confirm failure**

Run: `pytest -q tests/test_external_tokenize.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'dps.engine.transform.external'`.

- [ ] **Step 3: Write `dps/engine/transform/external.py`**

```python
"""Adapter to a bank-owned tokenization service (spec §5.5 ``external_tokenize``, D8).

DPS does not hold these keys or vaults; the bank's service (Voltage, Protegrity, Thales, or an
in-house vaultless tokenizer) does, which keeps DPS out of that system's PCI scope. Calls are
batched (one per entity type and profile per request or batch), retried on 429/5xx/transport
errors, and their responses validated: same length, strings only, and never a token equal to its
input. Nothing about a value — request or response body — reaches a log or an error message.
"""
from __future__ import annotations

import time
from collections.abc import Sequence
from typing import Any, Literal, Protocol

import httpx

from dps.config import Settings
from dps.observability import EXTERNAL_CALLS, get_logger

logger = get_logger(__name__)
MAX_TOKEN_CHARS = 512
_RETRY_STATUS = frozenset({429, 500, 502, 503, 504})
ExternalCache = dict[tuple[str, str], str]


class ExternalServiceError(RuntimeError):
    """The tokenization service failed or answered out of contract (value-free message)."""


class ExternalTokenizer(Protocol):
    name: str

    def tokenize(self, entity_type: str, values: Sequence[str], *, profile: str | None) -> list[str]: ...

    def detokenize(self, entity_type: str, tokens: Sequence[str], *, profile: str | None) -> list[str]: ...

    def health(self) -> bool: ...


class HttpTokenizer:
    """``ExternalTokenizer`` over the HTTP wire contract in HANDOFF §8."""

    name = "http"

    def __init__(self, base_url: str, *, token: str = "", verify: bool | str = True, timeout: float = 10.0,
                 batch: int = 500, retries: int = 2, transport: httpx.BaseTransport | None = None) -> None:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        self.batch, self.retries = max(1, batch), max(0, retries)
        self._client = httpx.Client(base_url=base_url.rstrip("/"), headers=headers, verify=verify,
                                    timeout=timeout, transport=transport)

    def tokenize(self, entity_type: str, values: Sequence[str], *, profile: str | None) -> list[str]:
        return self._call("tokenize", entity_type, values, profile)

    def detokenize(self, entity_type: str, tokens: Sequence[str], *, profile: str | None) -> list[str]:
        return self._call("detokenize", entity_type, tokens, profile)

    def health(self) -> bool:
        try:
            return self._client.get("/health").status_code == 200
        except httpx.HTTPError:
            return False

    def close(self) -> None:
        self._client.close()

    def _call(self, op: Literal["tokenize", "detokenize"], entity_type: str, items: Sequence[str],
              profile: str | None) -> list[str]:
        out: list[str] = []
        field_in, field_out = ("values", "tokens") if op == "tokenize" else ("tokens", "values")
        for start in range(0, len(items), self.batch):
            chunk = [str(x) for x in items[start:start + self.batch]]
            payload = self._post(f"/v1/{op}", {"profile": profile, "entity_type": entity_type, field_in: chunk}, op)
            result = payload.get(field_out) if isinstance(payload, dict) else None
            if not isinstance(result, list) or len(result) != len(chunk) or not all(isinstance(x, str) for x in result):
                EXTERNAL_CALLS.labels(op, "malformed").inc()
                raise ExternalServiceError("tokenization service returned a malformed response")
            if op == "tokenize" and any(t == v or not t or len(t) > MAX_TOKEN_CHARS for t, v in zip(result, chunk, strict=True)):
                EXTERNAL_CALLS.labels(op, "malformed").inc()
                raise ExternalServiceError("tokenization service returned an untransformed or oversized token")
            out.extend(result)
        return out

    def _post(self, path: str, body: dict[str, Any], op: str) -> Any:
        status: int | None = None
        for attempt in range(self.retries + 1):
            try:
                response = self._client.post(path, json=body)
            except httpx.HTTPError:
                status = None
            else:
                status = response.status_code
                if status == 200:
                    EXTERNAL_CALLS.labels(op, "ok").inc()
                    try:
                        return response.json()
                    except ValueError as exc:
                        raise ExternalServiceError("tokenization service returned a malformed response") from exc
                if status not in _RETRY_STATUS:
                    EXTERNAL_CALLS.labels(op, "refused").inc()
                    raise ExternalServiceError(f"tokenization service refused the request (HTTP {status})")
            if attempt < self.retries:
                time.sleep(0.2 * 2**attempt)
        EXTERNAL_CALLS.labels(op, "unavailable").inc()
        logger.warning("tokenizer_unavailable", status=status, attempt=self.retries + 1)
        raise ExternalServiceError("tokenization service unavailable")


def build_tokenizer(settings: Settings) -> ExternalTokenizer | None:
    """The configured adapter, or ``None`` when ``DPS_TOKENIZER_PROVIDER`` is empty."""
    if settings.tokenizer_provider != "http" or not settings.tokenizer_url:
        return None
    if settings.egress_tls_required and not settings.tokenizer_url.startswith("https://"):
        raise ValueError("DPS_TOKENIZER_URL must be https when egress TLS is required")
    verify: bool | str = settings.tokenizer_ca_file or settings.ssl_cert_file or True
    return HttpTokenizer(settings.tokenizer_url, token=settings.tokenizer_token, verify=verify,
                         timeout=settings.tokenizer_timeout_seconds, batch=settings.tokenizer_batch,
                         retries=settings.tokenizer_retries)
```

- [ ] **Step 4: Wire it into the engine (M0 touch points)**

`dps/engine/transform/methods.py` — add the field to `TransformContext`:
```python
    external: Mapping[tuple[str, str], str] | None = None   # filled by apply's (de)tokenize pre-pass
```
and replace `_external` / its registry entry:
```python
def _external(value: str, ctx: TransformContext) -> str:
    if ctx.external is None:
        raise MethodError("external_tokenize: no tokenization adapter is configured")
    token = ctx.external.get((ctx.entity_type, value))
    if token is None:
        raise MethodError("external_tokenize: value was not part of the tokenization batch")
    return token


def _external_reverse(token: str, ctx: TransformContext, _rp: dict[str, Any]) -> str:
    if ctx.external is None or (ctx.entity_type, token) not in ctx.external:
        raise MethodError("external_tokenize: token could not be detokenized")
    return ctx.external[(ctx.entity_type, token)]

# in METHODS:
    "external_tokenize": Method("external_tokenize", True, _external, _external_reverse),
```

`dps/engine/transform/apply.py` — `ApplyOptions` gains `tokenizer: ExternalTokenizer | None = None` and `external_cache: dict[tuple[str, str], str] | None = None` (import the protocol under `TYPE_CHECKING`); `_ctx`/`_reveal_ctx` take an `external` argument and pass it to `TransformContext`. Pre-passes:
```python
def _tokenize_prepass(plan: Plan, document: Any, options: ApplyOptions) -> dict[tuple[str, str], str] | None:
    """One tokenization call per (entity type, profile); ``None`` when no adapter is configured."""
    if options.tokenizer is None:
        return None
    cache = dict(options.external_cache or {})
    groups: dict[tuple[str, str | None], set[str]] = {}
    for entry in plan.entries:
        if entry.method != "external_tokenize":
            continue
        loc = entry.finding.locator
        current = document if loc.path == "$" else get_path(document, loc.path)
        value = str(current) if loc.whole else str(current)[loc.start:loc.end]
        if (entry.finding.entity_type, value) not in cache:
            groups.setdefault((entry.finding.entity_type, entry.params.get("profile")), set()).add(value)
    for (entity, profile), values in groups.items():
        ordered = sorted(values)
        tokens = options.tokenizer.tokenize(entity, ordered, profile=profile)
        cache.update({(entity, v): t for v, t in zip(ordered, tokens, strict=True)})
    return cache


def _detokenize_prepass(output: Any, hints: list[RevealHint], options: ApplyOptions) -> dict[tuple[str, str], str] | None:
    if options.tokenizer is None:
        return None
    groups: dict[tuple[str, str | None], set[str]] = {}
    for h in hints:
        if h.method != "external_tokenize":
            continue
        current = output if h.path == "$" else get_path(output, h.path)
        token = str(current) if h.start is None else str(current)[h.start:h.end]
        groups.setdefault((h.entity_type, h.params.get("profile")), set()).add(token)
    cache: dict[tuple[str, str], str] = {}
    for (entity, profile), tokens in groups.items():
        ordered = sorted(tokens)
        cache.update({(entity, t): v for t, v in zip(ordered, options.tokenizer.detokenize(entity, ordered, profile=profile), strict=True)})
    return cache
```
`apply_plan` calls `external = _tokenize_prepass(plan, document, options)` right after `anchor = ...`; `reveal` calls `external = _detokenize_prepass(document, hints, options)` right after the deep copy (before any hint rewrites the document). The reveal hint for an external value carries `params` (with `profile`) from the plan entry, which is what the pre-pass reads.

`dps/engine/planner.py` — signature `build_plan(findings, policy, mode, *, fpe_enabled, external_enabled: bool = True)`; inside the loop, after the FPE fallback: `if method == "external_tokenize" and not external_enabled: reason = f"{reason}|external:unconfigured"`.

`dps/engine/service.py` — constructor `tokenizer: ExternalTokenizer | None | Literal["auto"] = "auto"` → `self.tokenizer = build_tokenizer(settings) if tokenizer == "auto" else tokenizer`; every `build_plan` call passes `external_enabled=self.tokenizer is not None`; `_apply_options` and `reveal`'s `ApplyOptions` pass `tokenizer=self.tokenizer`; `obfuscate`, `reveal` wrap `ExternalServiceError` as:
```python
class TokenizationUnavailable(EngineError):
    """The bank tokenization service failed; mapped to HTTP 502."""
```
`dps/layers/api/common.py` — in `run_engine`, before `except EngineError`: `except TokenizationUnavailable as exc: outcome = "upstream_error"; raise HTTPException(status_code=502, detail="tokenization service unavailable") from exc`.

`dps/app.py` lifespan: `READINESS.set("tokenizer", engine.tokenizer.health() if engine.tokenizer else False, "configured" if engine.tokenizer else "unconfigured", adapter=engine.tokenizer.name if engine.tokenizer else "none")` (not a required component).

- [ ] **Step 5: Write the mock service `docker/tokenizer-mock/server.py`**

```python
"""Stateless (vaultless) mock of a bank tokenization service — the HTTP contract DPS speaks.

Local stack and tests only. Tokens are ``tk_`` + base32(AES-SIV(value, ad = entity|profile)), so the
mock is deterministic and reversible without storing anything. It logs request lines only (method,
path, status) and never a body. Run: ``python server.py --port 8420`` (MOCK_TOKENIZER_TOKEN,
MOCK_TOKENIZER_KEY from the environment; demo literals by default).
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESSIV

MAX_BODY = 1_000_000
MAX_ITEMS = 1000


def _b32(data: bytes) -> str:
    return base64.b32encode(data).decode("ascii").lower().rstrip("=")


def _unb32(text: str) -> bytes:
    return base64.b32decode(text.upper() + "=" * ((-len(text)) % 8))


def make_server(host: str, port: int, *, token: str, key: str) -> ThreadingHTTPServer:
    siv = AESSIV(hashlib.sha512(key.encode("utf-8")).digest())

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: Any) -> None:  # request line only, never bodies
            print(f"tokenizer-mock {self.command} {self.path.split('?')[0]}", flush=True)

        def _send(self, status: int, body: dict[str, Any] | None = None) -> None:
            raw = json.dumps(body or {}).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self) -> None:  # noqa: N802
            self._send(200, {"status": "ok"}) if self.path == "/health" else self._send(404)

        def do_POST(self) -> None:  # noqa: N802
            if not hmac.compare_digest(self.headers.get("Authorization", ""), f"Bearer {token}"):
                return self._send(401)
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                return self._send(413)
            try:
                body = json.loads(self.rfile.read(length))
                ad = [f"{body['entity_type']}|{body.get('profile') or ''}".encode("utf-8")]
                if self.path == "/v1/tokenize":
                    items = body["values"]
                    if len(items) > MAX_ITEMS:
                        return self._send(413)
                    return self._send(200, {"tokens": ["tk_" + _b32(siv.encrypt(str(v).encode("utf-8"), ad)) for v in items]})
                if self.path == "/v1/detokenize":
                    items = body["tokens"]
                    if len(items) > MAX_ITEMS:
                        return self._send(413)
                    return self._send(200, {"values": [siv.decrypt(_unb32(str(t)[3:]), ad).decode("utf-8") for t in items]})
            except Exception:  # noqa: BLE001 — a mock: any malformed input is a 400 without detail
                return self._send(400)
            return self._send(404)

    return ThreadingHTTPServer((host, port), Handler)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8420)
    args = parser.parse_args()
    server = make_server(args.host, args.port, token=os.environ.get("MOCK_TOKENIZER_TOKEN", "dps-local-tokenizer-token"),
                         key=os.environ.get("MOCK_TOKENIZER_KEY", "dps-local-tokenizer-key"))
    server.serve_forever()


if __name__ == "__main__":
    main()
```

Compose (`docker-compose.yml`, contract §11 gains the row `tokenizer-mock | data-privacy-services:local | 8420:8420 | full, tokenizer | urllib /health | M5`):
```yaml
  tokenizer-mock:
    build: .
    image: data-privacy-services:local
    profiles: ["full", "tokenizer"]
    command: ["python", "/mock/server.py", "--port", "8420"]
    volumes: ["./docker/tokenizer-mock:/mock:ro"]
    environment:
      MOCK_TOKENIZER_TOKEN: dps-local-tokenizer-token   # local demo literal
      MOCK_TOKENIZER_KEY: dps-local-tokenizer-key       # local demo literal
    ports: ["127.0.0.1:8420:8420"]
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8420/health', timeout=3).status==200 else 1)"]
      interval: 10s
      timeout: 5s
      retries: 5
    mem_limit: 128m
```
and on `app` and `worker`: `DPS_TOKENIZER_PROVIDER: ${DPS_TOKENIZER_PROVIDER:-http}`, `DPS_TOKENIZER_URL: http://tokenizer-mock:8420`, `DPS_TOKENIZER_TOKEN: dps-local-tokenizer-token`. HANDOFF §3 rows for the three variables, §6 the `tokenizer-mock` service.

- [ ] **Step 6: Run tests, lint, types**

Run: `pytest -q tests/test_external_tokenize.py tests/test_methods.py tests/test_apply.py tests/test_service.py tests/test_api.py && pytest -q && ruff check dps tests tools && mypy dps`
Expected: `7 passed` for the new file; the M0 files unchanged and green.

- [ ] **Step 7: Commit**

```bash
git add dps/engine/transform/external.py dps/engine/transform/methods.py dps/engine/transform/apply.py dps/engine/planner.py dps/engine/service.py dps/layers/api/common.py dps/layers/api/routers/detect.py dps/layers/api/routers/transform.py dps/layers/api/routers/policies.py dps/app.py dps/engine/batch.py docker/tokenizer-mock/server.py docker-compose.yml HANDOFF.md tests/test_external_tokenize.py docs/superpowers/plans/2026-10-11-m1-m5-contract.md
git commit -m "feat(transform): external_tokenize via a batched, validated HTTP adapter to a bank tokenization service, with a vaultless mock" -- dps/engine/transform/external.py dps/engine/transform/methods.py dps/engine/transform/apply.py dps/engine/planner.py dps/engine/service.py dps/layers/api/common.py dps/layers/api/routers/detect.py dps/layers/api/routers/transform.py dps/layers/api/routers/policies.py dps/app.py dps/engine/batch.py docker/tokenizer-mock/server.py docker-compose.yml HANDOFF.md tests/test_external_tokenize.py docs/superpowers/plans/2026-10-11-m1-m5-contract.md
```

---

### Task 9: DB-backed API keys — rotation, expiry, disable; operator CLI

**Files:**
- Create: `dps/migrations/042_key_rotation.sql`, `dps/auth_db.py`, `dps/keys_cli.py`
- Modify: `dps/auth.py` (`StaticKeyStore.principals()`), `dps/app.py`, `HANDOFF.md`, contract §2.2
- Test: `tests/test_auth_db.py` (`db`), `tests/test_keys_cli.py` (`db`)

`dps/migrations/042_key_rotation.sql`:
```sql
-- 042 — DB-backed API keys (M5, contract §8.3). dps_api_key is GLOBAL (no RLS): a key is looked up
-- before any tenant is known. Only the sha256 of a key and its first 8 characters are stored.
ALTER TABLE __SCHEMA__.dps_api_key
  ADD COLUMN key_prefix text NOT NULL DEFAULT '' CHECK (key_prefix ~ '^(dps_[A-Za-z0-9_-]{0,4})?$'),
  ADD COLUMN disabled_by text,
  ADD COLUMN rotated_at timestamptz;
ALTER TABLE __SCHEMA__.dps_api_key
  ADD CONSTRAINT dps_api_key_name_ck CHECK (name ~ '^[a-z0-9][a-z0-9-]{1,62}$'),
  ADD CONSTRAINT dps_api_key_hash_ck CHECK (key_hash ~ '^[0-9a-f]{64}$'),
  ADD CONSTRAINT dps_api_key_scopes_ck CHECK (
    scopes <@ ARRAY['detect','obfuscate','synthesize','reveal','jobs','scan','admin','*']::text[]),
  ADD CONSTRAINT dps_api_key_tenants_ck CHECK (cardinality(tenant_ids) >= 1);
-- one successor per key: a concurrent second rotation fails instead of minting two live keys
CREATE UNIQUE INDEX dps_api_key_one_successor ON __SCHEMA__.dps_api_key (rotated_from)
  WHERE rotated_from IS NOT NULL;
CREATE INDEX dps_api_key_active ON __SCHEMA__.dps_api_key (expires_at) WHERE disabled_at IS NULL;
GRANT SELECT, INSERT, UPDATE ON __SCHEMA__.dps_api_key TO dps_app_rw;
REVOKE DELETE ON __SCHEMA__.dps_api_key FROM dps_app_rw;   -- keys are disabled, never deleted (audit)
```

Interfaces (`dps/auth_db.py`):
```python
KEY_NAME: re.Pattern[str]                                    # ^[a-z0-9][a-z0-9-]{1,62}$
class GrantError(Exception): status: int; code: str; detail: str
class ApiKeyView(BaseModel):
    id: str; name: str; key_prefix: str; tenant_ids: list[str]; scopes: list[str]
    source: Literal["db", "static"]; status: Literal["active", "expired", "disabled"]
    created_by: str | None; created_at: datetime | None; expires_at: datetime | None
    last_used_at: datetime | None; disabled_at: datetime | None; rotated_from: str | None
    rotated_at: datetime | None; age_days: int | None
class CreatedKey(BaseModel): key: str; record: ApiKeyView        # `key` is the raw key — shown once
def key_prefix(raw: str) -> str                                  # raw[:8]
def check_grant(actor: Principal, tenant_ids: list[str], scopes: list[str]) -> None   # no escalation
class DbKeyStore:
    def __init__(self, settings: Settings, *, now: Callable[[], datetime] = lambda: datetime.now(UTC),
                 monotonic: Callable[[], float] = time.monotonic) -> None
    def lookup(self, raw_key: str) -> Principal | None               # sync; snapshot + expiry check
    async def refresh(self) -> int
    def invalidate(self, key_id: str) -> None
    async def run(self, stop: asyncio.Event) -> None
    @property
    def loaded(self) -> bool
class ChainKeyStore:
    def __init__(self, *stores: KeyStore) -> None
    def lookup(self, raw_key: str) -> Principal | None
async def create_key(*, name: str, tenant_ids: list[str], scopes: list[str], expires_in_days: int,
                     actor: Principal, settings: Settings) -> CreatedKey
async def list_keys(*, actor: Principal, static: StaticKeyStore | None, settings: Settings) -> list[ApiKeyView]
async def rotate_key(key_id: str, *, grace_hours: int, actor: Principal, settings: Settings) -> tuple[CreatedKey, ApiKeyView]
async def disable_key(key_id: str, *, actor: Principal, settings: Settings) -> ApiKeyView
async def enable_key(key_id: str, *, actor: Principal, settings: Settings) -> ApiKeyView
```
`dps/auth.py` (M0): `StaticKeyStore.principals() -> list[Principal]` (read-only view of the configured keys, for names in the Admin list and audit).

- [ ] **Step 1: Write the failing tests**

`tests/test_auth_db.py` (all `@pytest.mark.db`; fixtures `migrated_db`, `db_settings`, `api_client` from contract §12.2):
```python
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from dps.auth import Principal, StaticKeyStore, hash_key
from dps.auth_db import (
    ChainKeyStore,
    DbKeyStore,
    GrantError,
    create_key,
    disable_key,
    enable_key,
    list_keys,
    rotate_key,
)
from dps.db import acquire

pytestmark = pytest.mark.db
ROOT = Principal(key_id="root", name="root", tenant_ids=["*"], scopes=["*"])


class Clock:
    def __init__(self) -> None:
        self.t = datetime.now(UTC)

    def __call__(self) -> datetime:
        return self.t


async def _store(db_settings, clock: Clock | None = None) -> DbKeyStore:  # noqa: ANN001
    store = DbKeyStore(db_settings, now=clock or (lambda: datetime.now(UTC)))
    await store.refresh()
    return store


async def test_create_lookup_and_hash_only_storage(migrated_db, db_settings) -> None:  # noqa: ANN001
    created = await create_key(name="kyc-pipeline", tenant_ids=["retail"], scopes=["detect", "obfuscate"],
                               expires_in_days=90, actor=ROOT, settings=db_settings)
    assert created.key.startswith("dps_") and created.record.key_prefix == created.key[:8]
    store = await _store(db_settings)
    p = store.lookup(created.key)
    assert p is not None and p.tenant_ids == ["retail"] and p.has_scope("obfuscate") and not p.has_scope("reveal")
    async with acquire(None) as conn:
        dump = await conn.fetchval(f"SELECT string_agg(t::text, '|') FROM {db_settings.pg_schema}.dps_api_key t")
    assert created.key not in dump and hash_key(created.key) in dump


async def test_expiry_is_enforced_between_refreshes(migrated_db, db_settings) -> None:  # noqa: ANN001
    clock = Clock()
    created = await create_key(name="short", tenant_ids=["retail"], scopes=["detect"], expires_in_days=1,
                               actor=ROOT, settings=db_settings)
    store = await _store(db_settings, clock)
    assert store.lookup(created.key) is not None
    clock.t += timedelta(days=1, seconds=1)
    assert store.lookup(created.key) is None


async def test_rotation_with_grace(migrated_db, db_settings) -> None:  # noqa: ANN001
    clock = Clock()
    old = await create_key(name="ledger-batch", tenant_ids=["retail"], scopes=["jobs"], expires_in_days=90,
                           actor=ROOT, settings=db_settings)
    new, old_view = await rotate_key(old.record.id, grace_hours=24, actor=ROOT, settings=db_settings)
    assert new.record.name == "ledger-batch" and new.record.rotated_from == old.record.id
    assert old_view.expires_at is not None and old_view.expires_at <= datetime.now(UTC) + timedelta(hours=24, minutes=1)
    store = await _store(db_settings, clock)
    assert store.lookup(old.key) and store.lookup(new.key)
    clock.t += timedelta(hours=25)
    assert store.lookup(old.key) is None and store.lookup(new.key) is not None
    with pytest.raises(GrantError) as again:
        await rotate_key(old.record.id, grace_hours=24, actor=ROOT, settings=db_settings)
    assert again.value.status in (404, 409)


async def test_rotate_now_disables_and_invalidate_is_immediate(migrated_db, db_settings) -> None:  # noqa: ANN001
    old = await create_key(name="now-key", tenant_ids=["retail"], scopes=["detect"], expires_in_days=30,
                           actor=ROOT, settings=db_settings)
    store = await _store(db_settings)
    assert store.lookup(old.key) is not None
    _, old_view = await rotate_key(old.record.id, grace_hours=0, actor=ROOT, settings=db_settings)
    assert old_view.status == "disabled"
    store.invalidate(old.record.id)
    assert store.lookup(old.key) is None


async def test_disable_enable_and_rotated_keys_stay_dead(migrated_db, db_settings) -> None:  # noqa: ANN001
    k = await create_key(name="toggle", tenant_ids=["retail"], scopes=["detect"], expires_in_days=30,
                         actor=ROOT, settings=db_settings)
    assert (await disable_key(k.record.id, actor=ROOT, settings=db_settings)).status == "disabled"
    assert (await enable_key(k.record.id, actor=ROOT, settings=db_settings)).status == "active"
    await rotate_key(k.record.id, grace_hours=0, actor=ROOT, settings=db_settings)
    with pytest.raises(GrantError) as err:
        await enable_key(k.record.id, actor=ROOT, settings=db_settings)
    assert err.value.code == "key_rotated"


@pytest.mark.parametrize("tenants,scopes,code", [(["retail"], ["reveal"], "scope_escalation"),
                                                 (["cards"], ["detect"], "tenant_escalation"),
                                                 (["*"], ["detect"], "tenant_escalation"),
                                                 (["retail"], ["*"], "scope_escalation"),
                                                 (["retail"], ["teleport"], "invalid_scope")])
async def test_no_privilege_escalation(migrated_db, db_settings, tenants, scopes, code) -> None:  # noqa: ANN001
    actor = Principal(key_id="tenant-admin", name="tenant-admin", tenant_ids=["retail"], scopes=["admin", "detect"])
    with pytest.raises(GrantError) as err:
        await create_key(name="x-key", tenant_ids=tenants, scopes=scopes, expires_in_days=30, actor=actor,
                         settings=db_settings)
    assert err.value.code == code


@pytest.mark.parametrize("name,days,code", [("Bad Name", 30, "invalid_name"), ("ok-name", 0, "invalid_expiry"),
                                            ("ok-name", 366, "invalid_expiry")])
async def test_create_validation(migrated_db, db_settings, name, days, code) -> None:  # noqa: ANN001
    with pytest.raises(GrantError) as err:
        await create_key(name=name, tenant_ids=["retail"], scopes=["detect"], expires_in_days=days, actor=ROOT,
                         settings=db_settings)
    assert err.value.code == code and err.value.status == 422


async def test_chain_static_first_and_stale_snapshot_fails_closed(migrated_db, db_settings) -> None:  # noqa: ANN001
    import json

    static = StaticKeyStore.from_settings(db_settings.model_copy(update={"api_keys": json.dumps(
        [{"name": "env", "key": "dps_env_key_for_tests", "tenant_ids": ["*"], "scopes": ["detect"]}])}))
    created = await create_key(name="db-key", tenant_ids=["retail"], scopes=["detect"], expires_in_days=30,
                               actor=ROOT, settings=db_settings)
    ticks = [0.0]
    store = DbKeyStore(db_settings, monotonic=lambda: ticks[0])
    await store.refresh()
    chain = ChainKeyStore(static, store)
    assert chain.lookup("dps_env_key_for_tests").name == "env" and chain.lookup(created.key).name == "db-key"  # type: ignore[union-attr]
    ticks[0] += 10 * db_settings.key_cache_seconds + 1
    assert chain.lookup(created.key) is None and chain.lookup("dps_env_key_for_tests") is not None


async def test_last_used_is_flushed_and_list_hides_secrets(migrated_db, db_settings) -> None:  # noqa: ANN001
    created = await create_key(name="used-key", tenant_ids=["retail"], scopes=["detect"], expires_in_days=30,
                               actor=ROOT, settings=db_settings)
    store = await _store(db_settings)
    store.lookup(created.key)
    await store.refresh()
    views = {v.name: v for v in await list_keys(actor=ROOT, static=None, settings=db_settings)}
    assert views["used-key"].last_used_at is not None and views["used-key"].status == "active"
    assert created.key not in str(views) and hash_key(created.key) not in str(views)


def test_api_accepts_a_db_key(migrated_db, db_settings) -> None:  # noqa: ANN001
    import functools

    from fastapi.testclient import TestClient

    from dps.app import create_app

    settings = db_settings.model_copy(update={"auth_enabled": True, "bootstrap_api_key": "", "api_keys": ""})
    with TestClient(create_app(settings)) as client:
        # run on the app's event loop and pool (TestClient's portal), never on the test's loop
        created = client.portal.call(functools.partial(  # type: ignore[union-attr]
            create_key, name="api-key", tenant_ids=["retail"], scopes=["detect"], expires_in_days=30, actor=ROOT,
            settings=settings))
        client.portal.call(client.app.state.db_keys.refresh)  # type: ignore[union-attr,attr-defined]
        ok = client.post("/api/v1/detect", json={"text": "mail ann@example.com"}, headers={"X-API-KEY": created.key})
        assert ok.status_code == 200
        assert client.post("/api/v1/detect", json={"text": "x"}, headers={"X-API-KEY": "dps_nope"}).status_code == 401
```
(`test_no_privilege_escalation` and `test_create_validation` are parametrised: 5 + 3 cases.)

`tests/test_keys_cli.py` (`@pytest.mark.db`):
```python
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

pytestmark = pytest.mark.db


def _cli(db_settings, *args: str) -> dict:  # noqa: ANN001
    env = {**os.environ, "DPS_PG_HOST": db_settings.pg_host, "DPS_PG_PORT": str(db_settings.pg_port),
           "DPS_PG_DATABASE": db_settings.pg_database, "DPS_PG_SCHEMA": db_settings.pg_schema,
           "DPS_PG_USER": db_settings.pg_user, "DPS_PG_PASSWORD": db_settings.pg_password, "DPS_MIGRATIONS_MODE": "off"}
    out = subprocess.run([sys.executable, "-m", "dps.keys_cli", *args], env=env, capture_output=True, text=True,
                         check=True, timeout=60)
    assert out.stderr.count("dps_") == 0  # nothing key-like on stderr
    return json.loads(out.stdout)


def test_create_list_disable(migrated_db, db_settings) -> None:  # noqa: ANN001
    created = _cli(db_settings, "create", "--name", "platform-admin", "--tenants", "*", "--scopes", "*",
                   "--expires-in-days", "30")
    assert created["key"].startswith("dps_") and created["record"]["name"] == "platform-admin"
    listed = _cli(db_settings, "list")
    assert any(k["id"] == created["record"]["id"] for k in listed["items"]) and created["key"] not in json.dumps(listed)
    assert _cli(db_settings, "disable", created["record"]["id"])["status"] == "disabled"


def test_rotate_prints_the_new_key_once(migrated_db, db_settings) -> None:  # noqa: ANN001
    created = _cli(db_settings, "create", "--name", "rot-me", "--tenants", "retail", "--scopes", "detect",
                   "--expires-in-days", "30")
    rotated = _cli(db_settings, "rotate", created["record"]["id"], "--grace-hours", "0")
    assert rotated["new"]["key"].startswith("dps_") and rotated["old"]["status"] == "disabled"
```

- [ ] **Step 2: Run to confirm failure**

Run: `tools/scratch_pg.sh start && pytest -q -m db tests/test_auth_db.py tests/test_keys_cli.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'dps.auth_db'`.

- [ ] **Step 3: Write the migration** (SQL above) and run `python -m dps.migrate` against the scratch cluster → `042_key_rotation.sql applied`.

- [ ] **Step 4: Write `dps/auth_db.py`**

```python
"""DB-backed API keys (contract §8.3): hashed at rest, rotation with a grace window, expiry, disable.

``DbKeyStore`` keeps M0's synchronous ``KeyStore.lookup`` by serving an in-memory snapshot of the
active keys, refreshed every ``DPS_KEY_CACHE_SECONDS`` by a background task. Expiry is checked on
every lookup against the snapshot's ``expires_at``, so a key stops at its expiry even between
refreshes. A disable or rotation on this replica invalidates the entry at once; other replicas
see it within one refresh. If refreshes fail for ten periods the snapshot is dropped — DB keys fail
closed while static keys keep working. Raw keys exist only in the create/rotate response and the
operator CLI's stdout; the table holds sha256 hashes and 8-character prefixes.
"""
from __future__ import annotations

import asyncio
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import asyncpg
from pydantic import BaseModel

from dps.auth import KEY_PREFIX, SCOPES, WILDCARD, KeyStore, Principal, StaticKeyStore, hash_key, new_api_key
from dps.config import Settings
from dps.db import acquire
from dps.ids import new_id
from dps.observability import KEYS_LOADED, get_logger

logger = get_logger(__name__)
KEY_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")
_COLS = ("id, name, key_prefix, tenant_ids, scopes, created_by, created_at, expires_at, last_used_at, "
         "disabled_at, rotated_from, rotated_at")


class GrantError(Exception):
    """A key operation is refused (status + code + value-free detail)."""

    def __init__(self, status: int, code: str, detail: str) -> None:
        super().__init__(detail)
        self.status, self.code, self.detail = status, code, detail


class ApiKeyView(BaseModel):
    id: str
    name: str
    key_prefix: str
    tenant_ids: list[str]
    scopes: list[str]
    source: Literal["db", "static"] = "db"
    status: Literal["active", "expired", "disabled"]
    created_by: str | None = None
    created_at: datetime | None = None
    expires_at: datetime | None = None
    last_used_at: datetime | None = None
    disabled_at: datetime | None = None
    rotated_from: str | None = None
    rotated_at: datetime | None = None
    age_days: int | None = None


class CreatedKey(BaseModel):
    key: str            # the raw key — returned once, never stored
    record: ApiKeyView


@dataclass(frozen=True)
class _Entry:
    principal: Principal
    expires_at: datetime | None


def key_prefix(raw: str) -> str:
    return raw[:8]


def _table(settings: Settings) -> str:
    return f'"{settings.pg_schema}".dps_api_key'


def _view(row: Any, now: datetime | None = None) -> ApiKeyView:
    now = now or datetime.now(UTC)
    status: Literal["active", "expired", "disabled"] = "active"
    if row["disabled_at"] is not None:
        status = "disabled"
    elif row["expires_at"] is not None and row["expires_at"] <= now:
        status = "expired"
    created = row["created_at"]
    return ApiKeyView(id=row["id"], name=row["name"], key_prefix=row["key_prefix"], tenant_ids=list(row["tenant_ids"]),
                      scopes=list(row["scopes"]), status=status, created_by=row["created_by"], created_at=created,
                      expires_at=row["expires_at"], last_used_at=row["last_used_at"], disabled_at=row["disabled_at"],
                      rotated_from=row["rotated_from"], rotated_at=row["rotated_at"],
                      age_days=(now - created).days if created else None)


def check_grant(actor: Principal, tenant_ids: list[str], scopes: list[str]) -> None:
    """No escalation: a key may only carry scopes and tenants its creator holds."""
    for scope in scopes:
        if scope != WILDCARD and scope not in SCOPES:
            raise GrantError(422, "invalid_scope", f"unknown scope {scope}")
        if scope == WILDCARD and WILDCARD not in actor.scopes:
            raise GrantError(403, "scope_escalation", "only a key holding all scopes may grant all scopes")
        if not actor.has_scope(scope):
            raise GrantError(403, "scope_escalation", f"cannot grant scope {scope} the caller does not hold")
    for tenant in tenant_ids:
        if tenant == WILDCARD and WILDCARD not in actor.tenant_ids:
            raise GrantError(403, "tenant_escalation", "only a key holding all tenants may grant all tenants")
        if not actor.can_access(tenant):
            raise GrantError(403, "tenant_escalation", f"cannot grant tenant {tenant} the caller cannot access")


class DbKeyStore:
    """See the module docstring."""

    def __init__(self, settings: Settings, *, now: Callable[[], datetime] = lambda: datetime.now(UTC),
                 monotonic: Callable[[], float] = time.monotonic) -> None:
        self._settings, self._now, self._monotonic = settings, now, monotonic
        self._lock = threading.Lock()
        self._by_hash: dict[str, _Entry] = {}
        self._by_id: dict[str, str] = {}
        self._used: set[str] = set()
        self._loaded_at: float | None = None
        self._failures = 0

    @property
    def loaded(self) -> bool:
        return not self._stale()

    def _stale(self) -> bool:
        return self._loaded_at is None or self._monotonic() - self._loaded_at > 10 * self._settings.key_cache_seconds

    def lookup(self, raw_key: str) -> Principal | None:
        if not raw_key.startswith(KEY_PREFIX):
            return None
        digest = hash_key(raw_key)
        with self._lock:
            if self._stale():
                return None
            entry = self._by_hash.get(digest)
            if entry is None or (entry.expires_at is not None and entry.expires_at <= self._now()):
                return None
            self._used.add(entry.principal.key_id)
            return entry.principal

    def invalidate(self, key_id: str) -> None:
        with self._lock:
            digest = self._by_id.pop(key_id, None)
            if digest is not None:
                self._by_hash.pop(digest, None)

    async def refresh(self) -> int:
        with self._lock:
            used, self._used = self._used, set()
        async with acquire(None) as conn:
            if used:
                await conn.execute(f"UPDATE {_table(self._settings)} SET last_used_at = now() "
                                   "WHERE id = ANY($1::text[])", sorted(used))
            rows = await conn.fetch(f"SELECT id, name, key_hash, tenant_ids, scopes, expires_at "
                                    f"FROM {_table(self._settings)} WHERE disabled_at IS NULL "
                                    "AND (expires_at IS NULL OR expires_at > now())")
        snapshot = {r["key_hash"]: _Entry(Principal(key_id=r["id"], name=r["name"], tenant_ids=list(r["tenant_ids"]),
                                                    scopes=list(r["scopes"])), r["expires_at"]) for r in rows}
        with self._lock:
            self._by_hash = snapshot
            self._by_id = {e.principal.key_id: h for h, e in snapshot.items()}
            self._loaded_at = self._monotonic()
        KEYS_LOADED.set(len(snapshot))
        return len(snapshot)

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                await self.refresh()
                self._failures = 0
            except (asyncpg.PostgresError, OSError) as exc:
                self._failures += 1
                logger.warning("key_refresh_failed", error=type(exc).__name__, attempt=self._failures)
            try:
                await asyncio.wait_for(stop.wait(), timeout=self._settings.key_cache_seconds)
            except TimeoutError:
                pass


class ChainKeyStore:
    """The first store that recognises a key wins (static keys first, then the database)."""

    def __init__(self, *stores: KeyStore) -> None:
        self._stores = stores

    def lookup(self, raw_key: str) -> Principal | None:
        for store in self._stores:
            principal = store.lookup(raw_key)
            if principal is not None:
                return principal
        return None


def _validate(name: str, tenant_ids: list[str], scopes: list[str], days: int, settings: Settings) -> None:
    if not KEY_NAME.match(name):
        raise GrantError(422, "invalid_name", "name must match ^[a-z0-9][a-z0-9-]{1,62}$")
    if not 1 <= days <= settings.key_max_ttl_days:
        raise GrantError(422, "invalid_expiry", f"expires_in_days must be within 1..{settings.key_max_ttl_days}")
    if not tenant_ids or not scopes:
        raise GrantError(422, "invalid_grant", "a key needs at least one tenant and one scope")


async def create_key(*, name: str, tenant_ids: list[str], scopes: list[str], expires_in_days: int,
                     actor: Principal, settings: Settings) -> CreatedKey:
    _validate(name, tenant_ids, scopes, expires_in_days, settings)
    check_grant(actor, tenant_ids, scopes)
    raw = new_api_key()
    async with acquire(None) as conn:
        row = await conn.fetchrow(
            f"INSERT INTO {_table(settings)} (id, key_hash, name, key_prefix, tenant_ids, scopes, created_by, expires_at) "
            f"VALUES ($1, $2, $3, $4, $5, $6, $7, now() + make_interval(days => $8)) RETURNING {_COLS}",
            new_id("key"), hash_key(raw), name, key_prefix(raw), sorted(set(tenant_ids)), sorted(set(scopes)),
            actor.key_id, expires_in_days)
    logger.info("api_key_created", key_id=row["id"], count=len(scopes))
    return CreatedKey(key=raw, record=_view(row))


async def list_keys(*, actor: Principal, static: StaticKeyStore | None, settings: Settings) -> list[ApiKeyView]:
    async with acquire(None) as conn:
        rows = await conn.fetch(f"SELECT {_COLS} FROM {_table(settings)} ORDER BY created_at DESC")
    views = [_view(r) for r in rows if WILDCARD in actor.tenant_ids or all(actor.can_access(t) for t in r["tenant_ids"])]
    for p in static.principals() if static is not None else []:
        if WILDCARD in actor.tenant_ids or all(actor.can_access(t) for t in p.tenant_ids):
            views.append(ApiKeyView(id=p.key_id, name=p.name, key_prefix="static", tenant_ids=p.tenant_ids,
                                    scopes=p.scopes, source="static", status="active"))
    return views


async def _locked(conn: asyncpg.Connection, key_id: str, settings: Settings) -> Any:
    row = await conn.fetchrow(f"SELECT {_COLS} FROM {_table(settings)} WHERE id = $1 FOR UPDATE", key_id)
    if row is None:
        raise GrantError(404, "key_not_found", "key not found")
    return row


async def rotate_key(key_id: str, *, grace_hours: int, actor: Principal,
                     settings: Settings) -> tuple[CreatedKey, ApiKeyView]:
    grace = max(0, min(grace_hours, settings.key_rotation_max_grace_hours))
    raw = new_api_key()
    try:
        async with acquire(None) as conn, conn.transaction():
            old = await _locked(conn, key_id, settings)
            if old["rotated_at"] is not None:
                raise GrantError(409, "key_rotated", "key was already rotated")
            if _view(old).status != "active":
                raise GrantError(404, "key_not_found", "key not found, disabled or expired")
            check_grant(actor, list(old["tenant_ids"]), list(old["scopes"]))
            lifetime = (old["expires_at"] - old["created_at"]).days if old["expires_at"] else 90
            days = max(1, min(lifetime, settings.key_max_ttl_days))
            new = await conn.fetchrow(
                f"INSERT INTO {_table(settings)} (id, key_hash, name, key_prefix, tenant_ids, scopes, created_by, "
                "expires_at, rotated_from) VALUES ($1, $2, $3, $4, $5, $6, $7, now() + make_interval(days => $8), $9) "
                f"RETURNING {_COLS}", new_id("key"), hash_key(raw), old["name"], key_prefix(raw),
                list(old["tenant_ids"]), list(old["scopes"]), actor.key_id, days, key_id)
            if grace == 0:
                old = await conn.fetchrow(f"UPDATE {_table(settings)} SET disabled_at = now(), disabled_by = $2, "
                                          f"rotated_at = now() WHERE id = $1 RETURNING {_COLS}", key_id, actor.key_id)
            else:
                old = await conn.fetchrow(
                    f"UPDATE {_table(settings)} SET rotated_at = now(), expires_at = LEAST("
                    "coalesce(expires_at, 'infinity'::timestamptz), now() + make_interval(hours => $2)) "
                    f"WHERE id = $1 RETURNING {_COLS}", key_id, grace)
    except asyncpg.UniqueViolationError as exc:
        raise GrantError(409, "key_rotated", "key was already rotated") from exc
    logger.info("api_key_rotated", key_id=key_id, grace_hours=grace)
    return CreatedKey(key=raw, record=_view(new)), _view(old)


async def disable_key(key_id: str, *, actor: Principal, settings: Settings) -> ApiKeyView:
    async with acquire(None) as conn, conn.transaction():
        row = await _locked(conn, key_id, settings)
        check_grant(actor, list(row["tenant_ids"]), [])
        row = await conn.fetchrow(f"UPDATE {_table(settings)} SET disabled_at = coalesce(disabled_at, now()), "
                                  f"disabled_by = coalesce(disabled_by, $2) WHERE id = $1 RETURNING {_COLS}",
                                  key_id, actor.key_id)
    return _view(row)


async def enable_key(key_id: str, *, actor: Principal, settings: Settings) -> ApiKeyView:
    async with acquire(None) as conn, conn.transaction():
        row = await _locked(conn, key_id, settings)
        check_grant(actor, list(row["tenant_ids"]), list(row["scopes"]))
        if row["rotated_at"] is not None:
            raise GrantError(409, "key_rotated", "a rotated key cannot be re-enabled")
        if row["expires_at"] is not None and row["expires_at"] <= datetime.now(UTC) + timedelta(seconds=1):
            raise GrantError(409, "key_expired", "an expired key cannot be re-enabled")
        row = await conn.fetchrow(f"UPDATE {_table(settings)} SET disabled_at = NULL, disabled_by = NULL "
                                  f"WHERE id = $1 RETURNING {_COLS}", key_id)
    return _view(row)
```

- [ ] **Step 5: Write `dps/keys_cli.py`**

`python -m dps.keys_cli {create --name N --tenants a,b|* --scopes s1,s2|* --expires-in-days D | list | rotate ID [--grace-hours H] | disable ID | enable ID}`. Configures logging at `WARNING` with `structlog.configure(logger_factory=structlog.PrintLoggerFactory(file=sys.stderr))` so stdout carries only the JSON document. Builds `Settings()`, `await init_pool(settings)`, acts as `Principal(key_id="cli", name="operator-cli", tenant_ids=["*"], scopes=["*"])` (the operator already holds DB credentials), prints exactly one JSON document to stdout (`CreatedKey` for create, `{"new": CreatedKey, "old": ApiKeyView}` for rotate, `{"items": [...]}` for list, `ApiKeyView` otherwise), exits 2 with `{"error": code, "detail": detail}` on `GrantError`. It inserts one audit row per mutating command — `INSERT INTO dps_audit_log (tenant_id, key_id, route, action, outcome, counts) VALUES ('_dps', 'cli', 'cli', 'admin.key.<cmd>', 'ok', '{}')` under `acquire("_dps")` — and logs `api_key_<cmd>` with the key id only. Always `await close_pools()` in `finally`.

- [ ] **Step 6: Wire the store into `dps/app.py`**

```python
    static_store = StaticKeyStore.from_settings(settings)
    db_store = DbKeyStore(settings) if db_configured(settings) else None
    store: KeyStore = ChainKeyStore(static_store, db_store) if db_store else static_store
    app.state.auth = {"store": store, "enabled": settings.auth_enabled, "static": static_store}
    app.state.db_keys = db_store
```
In the lifespan, after M2 initialises the pools: `stop = asyncio.Event(); refresher = asyncio.create_task(db_store.run(stop))` when `db_store`; `READINESS.set("keys", True, "db+static" if db_store else "static", count=len(static_store))`; on shutdown `stop.set(); await refresher`. Contract §2.2: the 042 file content above replaces "M5 uses it". HANDOFF §5: the bootstrap path (`python -m dps.keys_cli create --name platform-admin --tenants '*' --scopes '*' --expires-in-days 90`, store the printed key in the secret manager, unset `DPS_BOOTSTRAP_API_KEY`).

- [ ] **Step 7: Run tests, lint, types**

Run: `pytest -q -m db tests/test_auth_db.py tests/test_keys_cli.py && pytest -q && ruff check dps tests tools && mypy dps`
Expected: `16 passed` (`test_auth_db.py`: 10 functions, 16 cases) + `2 passed`; unit suite green.

- [ ] **Step 8: Commit**

```bash
git add dps/migrations/042_key_rotation.sql dps/auth_db.py dps/keys_cli.py dps/auth.py dps/app.py HANDOFF.md tests/test_auth_db.py tests/test_keys_cli.py docs/superpowers/plans/2026-10-11-m1-m5-contract.md
git commit -m "feat(auth): DB-backed API keys with rotation grace, expiry, no-escalation grants and an operator CLI" -- dps/migrations/042_key_rotation.sql dps/auth_db.py dps/keys_cli.py dps/auth.py dps/app.py HANDOFF.md tests/test_auth_db.py tests/test_keys_cli.py docs/superpowers/plans/2026-10-11-m1-m5-contract.md
```

---

### Task 10: Tenants, quotas and the tenant directory

**Files:**
- Create: `dps/migrations/040_tenant_quotas.sql`, `dps/tenants.py`, `dps/netguard.py` (host normalisation part; the SSRF part lands in Task 17)
- Modify (M2): `dps/db.py` (`TENANT_TABLES`), `dps/jobs.py` (`checkpoint`, `claim_partitions`), `dps/layers/api/routers/jobs.py` (`POST /jobs/run`); contract §2.2, §5.3
- Test: `tests/test_tenants.py` (`db`), `tests/test_quota.py` (`db`)

`dps/migrations/040_tenant_quotas.sql`:
```sql
-- 040 — tenant quotas, daily usage, and the tenant directory (M5).
ALTER TABLE __SCHEMA__.dps_tenant
  ADD COLUMN daily_row_quota bigint CHECK (daily_row_quota IS NULL OR daily_row_quota >= 0);

CREATE TABLE __SCHEMA__.dps_tenant_usage (
  tenant_id text NOT NULL,
  day date NOT NULL,                              -- UTC day
  rows_written bigint NOT NULL DEFAULT 0 CHECK (rows_written >= 0),
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, day));
ALTER TABLE __SCHEMA__.dps_tenant_usage ENABLE ROW LEVEL SECURITY;
ALTER TABLE __SCHEMA__.dps_tenant_usage FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON __SCHEMA__.dps_tenant_usage
  USING (tenant_id = current_setting('app.tenant_id', true))
  WITH CHECK (tenant_id = current_setting('app.tenant_id', true));
-- the worker counts rows inside checkpoint() and reads usage in the claim CTE (no GUC bound there)
CREATE POLICY worker_usage ON __SCHEMA__.dps_tenant_usage TO dps_worker USING (true) WITH CHECK (true);
CREATE POLICY worker_quota_read ON __SCHEMA__.dps_tenant FOR SELECT TO dps_worker USING (true);
GRANT SELECT, INSERT, UPDATE, DELETE ON __SCHEMA__.dps_tenant_usage TO dps_app_rw;

-- Tenant directory: the ONLY cross-tenant read open to the app role, and it returns ids alone.
-- The function runs as its owner (the migration role); FORCE RLS still applies to the owner, so
-- the owner gets narrow SELECT-only policies on exactly the three tables the function reads.
CREATE POLICY owner_directory ON __SCHEMA__.dps_tenant FOR SELECT TO dps_owner USING (true);
CREATE POLICY owner_directory ON __SCHEMA__.dps_job FOR SELECT TO dps_owner USING (true);
CREATE POLICY owner_directory ON __SCHEMA__.dps_connection FOR SELECT TO dps_owner USING (true);
CREATE FUNCTION __SCHEMA__.dps_tenant_directory() RETURNS TABLE (tenant_id text)
  LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
    SELECT t.tenant_id FROM __SCHEMA__.dps_tenant t
    UNION SELECT j.tenant_id FROM __SCHEMA__.dps_job j
    UNION SELECT c.tenant_id FROM __SCHEMA__.dps_connection c
    ORDER BY 1
  $$;
REVOKE ALL ON FUNCTION __SCHEMA__.dps_tenant_directory() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION __SCHEMA__.dps_tenant_directory() TO dps_app_rw;
```
`dps/db.py`: `TENANT_TABLES` += `"dps_tenant_usage"`. The RLS posture check (`assert_rls_posture`) therefore covers it.

Interfaces (`dps/tenants.py`; `dps/netguard.py` part 1):
```python
# netguard.py
def normalize_host(value: str) -> str          # "https://Host.Example:8443/x" | "host.example" → "host.example"; ValueError otherwise
# tenants.py
SYSTEM_TENANT = "_dps"                         # retention jobs, CLI audit rows; fails the tenant-id CHECK on purpose
class TenantError(Exception): status: int; code: str; detail: str
class TenantSpec(BaseModel):
    tenant_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,62}$")
    display_name: str | None = Field(None, max_length=120); retention_days: int | None = Field(None, ge=1, le=3650)
    max_running: int | None = Field(None, ge=1, le=256); max_queued: int | None = Field(None, ge=1, le=100_000)
    daily_row_quota: int | None = Field(None, ge=0); registered_hosts: list[str] = Field(default_factory=list, max_length=100)
    note: str | None = Field(None, max_length=500)                  # registered_hosts normalised by normalize_host
class TenantPatch(BaseModel): (every TenantSpec field except tenant_id, all optional; only fields set are written)
class TenantUsage(BaseModel): running: int; queued: int; rows_today: int
class EffectiveTenant(BaseModel): tenant_id: str; retention_days: int; max_running: int; max_queued: int
                                  daily_row_quota: int | None; registered_hosts: list[str]
class TenantView(BaseModel): tenant_id; display_name; note; overrides: dict[str, int | None]; effective: EffectiveTenant
                             usage: TenantUsage; keys: dict[str, int]; exists: bool; created_at; updated_at
async def tenant_directory() -> list[str]
async def effective_tenant(tenant_id: str, settings: Settings) -> EffectiveTenant      # 30 s per-process cache
async def list_tenants(actor: Principal, settings: Settings) -> list[TenantView]
async def create_tenant(spec: TenantSpec, actor: Principal, settings: Settings) -> TenantView
async def update_tenant(tenant_id: str, patch: TenantPatch, actor: Principal, settings: Settings) -> TenantView
async def tenant_usage(tenant_id: str) -> TenantUsage
async def check_daily_quota(tenant_id: str, settings: Settings) -> None                # raises TenantError(429, ...)
```

Key SQL (all under `acquire(tenant_id)` unless stated):
```sql
-- usage
SELECT (SELECT count(*) FROM dps_job WHERE tenant_id = $1 AND state IN ('running','waiting'))::int AS running,
       (SELECT count(*) FROM dps_job WHERE tenant_id = $1 AND state = 'queued')::int AS queued,
       coalesce((SELECT rows_written FROM dps_tenant_usage
                 WHERE tenant_id = $1 AND day = (now() AT TIME ZONE 'UTC')::date), 0)::bigint AS rows_today;
-- key counts (global table, acquire(None))
SELECT count(*) FILTER (WHERE disabled_at IS NULL AND (expires_at IS NULL OR expires_at > now())) AS active,
       count(*) FILTER (WHERE disabled_at IS NOT NULL) AS disabled
FROM dps_api_key WHERE $1 = ANY(tenant_ids) OR '*' = ANY(tenant_ids);
-- retention change re-stamps terminal jobs and their artefacts (same transaction as the tenant UPDATE)
UPDATE dps_job SET expires_at = finished_at + make_interval(days => $2), updated_at = now()
 WHERE tenant_id = $1 AND finished_at IS NOT NULL;
UPDATE dps_artefact a SET expires_at = j.expires_at FROM dps_job j
 WHERE a.job_id = j.id AND a.tenant_id = $1 AND j.tenant_id = $1;
-- directory (acquire(None))
SELECT tenant_id FROM dps_tenant_directory();
```
`list_tenants` = directory ∪ tenants of the actor's explicit grants, filtered by `actor.can_access`, each rendered with `effective_tenant` (settings defaults where the row's column is NULL: `retention_days`, `job_tenant_max_running`, `job_tenant_max_queued`) and its usage. `update_tenant` invalidates the `effective_tenant` cache entry and writes `updated_at = now()`.

M2 touch points:
- `dps/jobs.py::checkpoint` — after its fenced partition `UPDATE` returned one row and `rows_written > 0`, in the same transaction:
```sql
INSERT INTO __SCHEMA__.dps_tenant_usage (tenant_id, day, rows_written)
VALUES ($1, (now() AT TIME ZONE 'UTC')::date, $2)
ON CONFLICT (tenant_id, day) DO UPDATE
  SET rows_written = dps_tenant_usage.rows_written + EXCLUDED.rows_written, updated_at = now()
```
- `dps/jobs.py::claim_partitions` — add to the candidate CTE's `WHERE` (alias the partition table as M2 does; `p` below):
```sql
  AND NOT EXISTS (
    SELECT 1 FROM __SCHEMA__.dps_tenant t
    JOIN __SCHEMA__.dps_tenant_usage u ON u.tenant_id = t.tenant_id AND u.day = (now() AT TIME ZONE 'UTC')::date
    WHERE t.tenant_id = p.tenant_id AND t.daily_row_quota IS NOT NULL AND u.rows_written >= t.daily_row_quota)
```
  Partitions of a tenant over quota stay `queued` and are claimed after 00:00 UTC (the running partition finishes its current batch first).
- `POST /jobs/run` — before `create_job`: `await check_daily_quota(tenant_id, settings)` → `DpsHttpError(429, "daily_row_quota_exceeded", "the tenant's daily row quota is used; new jobs are accepted after 00:00 UTC")`.

- [ ] **Step 1: Write the failing tests**

`tests/test_tenants.py` (`@pytest.mark.db`) — test names and exact assertions:
- `test_create_list_update_with_effective_defaults`: `create_tenant(TenantSpec(tenant_id="retail-apac", display_name="Retail", daily_row_quota=50_000_000))` → view `.effective.retention_days == settings.retention_days` (30), `.effective.max_running == settings.job_tenant_max_running` (8), `.overrides["daily_row_quota"] == 50_000_000`; `update_tenant("retail-apac", TenantPatch(retention_days=7, max_running=2))` → `.effective.retention_days == 7 and .effective.max_running == 2`; a second `create_tenant` with the same id → `TenantError` status 409 code `tenant_exists`.
- `test_ids_and_hosts_are_validated`: `TenantSpec(tenant_id="Bad Id")` → `ValidationError`; `TenantSpec(tenant_id="t1", registered_hosts=["https://Onboarding-UAT.bank.internal:443/apply", "demo-site"]).registered_hosts == ["demo-site", "onboarding-uat.bank.internal"]`; `"https://user:pw@host"` and `"*.bank.internal"` → `ValidationError` whose text contains neither `pw` nor `user`.
- `test_directory_crosses_tenants_without_bypassing_rls`: create jobs (`create_job`) for tenants `a` and `b`; `tenant_directory()` ⊇ `{"a", "b"}`; under `acquire("a")`: `SELECT count(*) FROM dps_job` == 1; under `acquire(None)`: `SELECT count(*) FROM dps_tenant` == 0 (no bypass outside the function).
- `test_retention_change_restamps_terminal_jobs`: finished job with `finished_at = now() - 10 days`, default expiry; `update_tenant(..., TenantPatch(retention_days=5))` → that job's `expires_at == finished_at + 5 days` (±1 s); a running job's `expires_at` stays NULL.
- `test_list_is_filtered_by_the_callers_grants`: tenants `a`, `b`; actor granted `["a"]` sees exactly `["a"]`; wildcard actor sees both.

`tests/test_quota.py` (`@pytest.mark.db`):
- `test_checkpoint_counts_rows_per_utc_day`: claim a partition of a run job for tenant `q1` (the `queue` fixture), `checkpoint(..., rows_written=500, ...)` twice → `dps_tenant_usage.rows_written == 1000` for today; a fenced-out checkpoint (wrong worker id) adds nothing.
- `test_run_is_refused_when_the_daily_quota_is_used`: tenant `q2` with `daily_row_quota=100`, usage row 100 → `POST /api/v1/jobs/run` (valid spec) → 429 `{"code": "daily_row_quota_exceeded"}`.
- `test_claim_skips_partitions_of_a_tenant_over_quota`: queued partitions for `q3` (quota 10, usage 10) and `q4` (no quota) → `claim_partitions("w1", batch=10)` returns only `q4` partitions; after deleting `q3`'s usage row they are claimable.

- [ ] **Step 2: Run to confirm failure** → `ModuleNotFoundError: No module named 'dps.tenants'`.

- [ ] **Step 3: Implement** the migration, `normalize_host`, `dps/tenants.py`, and the three M2 touch points.

`normalize_host` (in `dps/netguard.py`):
```python
"""Network guards: host normalisation (M5 Task 10); SSRF checks and TLS rules (Task 17, Task 16)."""
from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlsplit

_HOST = re.compile(r"^(?=.{1,253}$)[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*$")


def normalize_host(value: str) -> str:
    """A registered host as a bare lower-case hostname (or IP literal). Accepts a URL or a host."""
    text = value.strip()
    parts = urlsplit(text if "://" in text else f"//{text}")
    if parts.username is not None or parts.password is not None:
        raise ValueError("a registered host must not carry credentials")
    host = (parts.hostname or "").rstrip(".")
    if not host:
        raise ValueError("not a host")
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        pass
    host = host.encode("idna").decode("ascii").lower()
    if not _HOST.match(host):
        raise ValueError("not a valid hostname (wildcards are not allowed)")
    return host
```
`TenantSpec`/`TenantPatch` validate `registered_hosts` with it (sorted, de-duplicated). The validator wraps errors as `ValueError("registered_hosts: entry N is not a valid host")` so the input never appears.

- [ ] **Step 4: Run tests**: `pytest -q -m db tests/test_tenants.py tests/test_quota.py` → `8 passed`; unit gate green.

- [ ] **Step 5: Commit**

```bash
git add dps/migrations/040_tenant_quotas.sql dps/tenants.py dps/netguard.py dps/db.py dps/jobs.py dps/layers/api/routers/jobs.py tests/test_tenants.py tests/test_quota.py docs/superpowers/plans/2026-10-11-m1-m5-contract.md
git commit -m "feat: tenants with quotas, daily usage counted at checkpoint and enforced at accept and claim, tenant directory" -- dps/migrations/040_tenant_quotas.sql dps/tenants.py dps/netguard.py dps/db.py dps/jobs.py dps/layers/api/routers/jobs.py tests/test_tenants.py tests/test_quota.py docs/superpowers/plans/2026-10-11-m1-m5-contract.md
```

---

### Task 11: Admin API — keys, tenants, audit (with CSV), queue

**Files:**
- Create: `dps/audit_query.py`, `dps/layers/api/routers/admin.py`
- Modify: `dps/app.py` (include router), contract §9
- Test: `tests/test_admin_api.py` (`db`)

Routes (all `require_scope("admin")` + `enforce_rate_limit`; errors via `DpsHttpError` → `{"detail", "code"}`; every mutating route writes an audit event `admin.<object>.<verb>` with value-free counts):

| Method · path | Body / query | Response |
|---|---|---|
| `GET /api/v1/admin/keys` | — | `{"items": [ApiKeyView], "next_cursor": null}` (db + static keys visible to the caller) |
| `POST /api/v1/admin/keys` | `{"name", "tenant_ids": [str], "scopes": [str], "expires_in_days": int}` | 201 `CreatedKey` (raw `key` once; `Cache-Control: no-store`) |
| `POST /api/v1/admin/keys/{key_id}/rotate` | `{"grace_hours": int = 24}` (0–168) | 201 `{"new": CreatedKey, "old": ApiKeyView}` (`no-store`) |
| `POST /api/v1/admin/keys/{key_id}/disable` · `/enable` | — | `ApiKeyView` (both call `db_keys.invalidate`/`refresh`) |
| `GET /api/v1/admin/tenants` | — | `{"items": [TenantView], "next_cursor": null}` |
| `POST /api/v1/admin/tenants` · `PATCH /api/v1/admin/tenants/{tenant_id}` | `TenantSpec` · `TenantPatch` | 201 `TenantView` · `TenantView` |
| `GET /api/v1/admin/audit` | `from`, `to` (ISO 8601; default last 14 days; window ≤ 400 days), `action`, `layer` (`api\|conn\|ui\|admin`), `outcome` (`ok\|failed\|blocked`), `q`, `tenants` (comma list or `*`; default the request tenant), `limit` (1–200, default 50), `cursor`, `format` (`json\|csv`) | json: `{"items": [AuditRow], "next_cursor": str \| null}`; csv: streamed `text/csv`, `Content-Disposition: attachment; filename="dps-audit-<from>-<to>.csv"` |
| `GET /api/v1/admin/queue` | — | `{"queues": queue_stats(), "workers": [{"worker_id", "role", "version", "online", "busy", "draining", "last_seen"}]}` |

Contract §9 additions (same commit): the `enable` route; the extra audit query parameters (`layer`, `outcome`, `q`, `tenants`, `limit`, `cursor`, `format=csv`).

`dps/audit_query.py`:
```python
def layer_case(column: str) -> str:
    """SQL CASE mapping an audit ``action`` to its console layer (shared with the roll-ups)."""
    return (f"CASE WHEN {column} LIKE 'job.%' OR {column} LIKE 'snapshot.%' OR {column} LIKE 'connection.%' "
            f"OR {column} LIKE 'group.%' OR {column} LIKE 'evidence.%' THEN 'conn' "
            f"WHEN {column} LIKE 'scan.%' THEN 'ui' "
            f"WHEN {column} LIKE 'admin.%' OR {column} LIKE 'retention.%' THEN 'admin' ELSE 'api' END")

OUTCOME_GROUPS = {"ok": ["ok"],
                  "failed": ["error", "failed", "verification_failed", "leakage", "dead", "upstream_error"],
                  "blocked": ["rejected", "bad_request", "denied", "blocked", "forbidden", "canceled"]}

class AuditRow(BaseModel):
    id: int; ts: datetime; tenant_id: str; key_id: str | None; key_name: str | None; trace_id: str | None
    route: str; action: str; layer: str; mode: str | None; policy: str | None; outcome: str
    status: int | None; job_id: str | None; findings: int | None; methods: dict[str, int]

def encode_cursor(ts: datetime, row_id: int) -> str          # base64url("<iso>|<id>")
def decode_cursor(cursor: str) -> tuple[datetime, int]       # ValueError → 422 invalid_cursor
def like_escape(text: str) -> str                            # \ → \\, % → \%, _ → \_
async def query_audit(*, tenants: list[str], since: datetime, until: datetime, action: str | None,
                      layer: str | None, outcome: str | None, q: str | None, limit: int,
                      cursor: tuple[datetime, int] | None, key_names: Mapping[str, str]
                      ) -> tuple[list[AuditRow], str | None]
async def stream_audit_csv(**same filters, max_rows: int = 1_000_000) -> AsyncIterator[bytes]
def csv_cell(value: Any) -> str                              # formula-injection guard
```
Per-tenant query (one per tenant under `acquire(tenant)`, merged by `(ts, id)` descending, first `limit` kept — `id` comes from one identity sequence, so `(ts, id)` orders globally):
```sql
SELECT id, ts, tenant_id, key_id, trace_id, route, action, {layer_case('action')} AS layer, mode, policy,
       outcome, status, job_id, counts
FROM __SCHEMA__.dps_audit_log
WHERE ts >= $1 AND ts < $2
  AND ($3::text IS NULL OR action = $3 OR action LIKE $3 || '.%')
  AND ($4::text IS NULL OR {layer_case('action')} = $4)
  AND ($5::text[] IS NULL OR outcome = ANY($5))
  AND ($6::text IS NULL OR job_id = $6 OR policy = $6 OR route ILIKE '%' || $7 || '%' ESCAPE '\'
       OR key_id = ANY($8::text[]))
  AND ($9::timestamptz IS NULL OR (ts, id) < ($9, $10))
ORDER BY ts DESC, id DESC
LIMIT $11
```
(`$7` = `like_escape(q)`, `$8` = ids whose key name contains `q`, resolved in Python from `key_names`.) `findings` = `counts->'findings_total'`, `methods` = `counts->'methods_used'` (non-dicts → `{}`). `key_names` comes from `list_keys` + `StaticKeyStore.principals()`. `tenants="*"` expands to `tenant_directory()` filtered by the caller's grants; an explicit tenant the caller cannot access → 403 `tenant_forbidden`.

CSV: header `ts,tenant_id,key_name,key_id,route,action,layer,mode,policy,findings,methods,outcome,status,job_id`; `methods` rendered `fpe_digits=3;email=1`; every cell goes through:
```python
_FORMULA = ("=", "+", "-", "@", "\t", "\r")

def csv_cell(value: Any) -> str:
    """CSV formula-injection guard: a cell a spreadsheet would evaluate gets a leading quote."""
    text = "" if value is None else str(value)
    return "'" + text if text.startswith(_FORMULA) else text
```
The stream pages through `query_audit` 1000 rows at a time until exhausted or `max_rows`, writing with `csv.writer` into a `StringIO` per page (`StreamingResponse`).

- [ ] **Step 1: Write the failing tests** — `tests/test_admin_api.py` (`@pytest.mark.db`; a fixture `admin_client` builds `create_app(settings)` with auth on and DB configured, enters `TestClient` as a context manager, mints a wildcard admin key and a detect-only key with `client.portal.call(functools.partial(create_key, ..., actor=ROOT, settings=settings))` — so they run on the app's event loop and pool — calls `client.portal.call(client.app.state.db_keys.refresh)`, and yields `(client, admin_headers, detect_headers)`; direct audit-row inserts in these tests also go through `client.portal.call`):
- `test_every_admin_route_requires_admin`: for each of the 9 admin routes (method, path with `key_x`/`t_x` ids), with `detect_headers` → 403; without a key → 401.
- `test_key_lifecycle_over_http`: POST keys → 201, body `key` starts `dps_`, response header `cache-control == "no-store"`; GET keys → the new record present, response text contains neither the raw key nor its sha256; the new key → 200 on `/api/v1/detect`; rotate `{"grace_hours": 0}` → old key 401 (after `refresh`), new key 200; disable new → 401; enable → 200.
- `test_tenant_crud_over_http`: POST `{"tenant_id": "cards-in", "daily_row_quota": 20000000, "registered_hosts": ["https://cards-apply-uat.bank.internal"]}` → 201 `.effective.registered_hosts == ["cards-apply-uat.bank.internal"]`; PATCH `{"retention_days": 45}` → 200 `.effective.retention_days == 45`; GET lists it with `usage.rows_today == 0`; PATCH unknown → 404 `tenant_not_found`.
- `test_audit_filters_paging_and_tenants`: insert 12 audit rows directly (tenants `a`/`b`; actions `obfuscate`, `job.create`, `scan.navigate`, `admin.key.create`; outcomes `ok`, `verification_failed`, `rejected`) → `layer=conn` returns only `job.*`; `outcome=failed` only `verification_failed`; `q=<job id>` exactly that row; `limit=5` then `cursor` → 5 + 5 + 2 rows, no overlap, strictly descending `(ts, id)`; `tenants=*` returns both tenants' rows; `tenants=zzz` for a key limited to `a` → 403.
- `test_audit_csv_is_streamed_and_formula_safe`: a row whose `route` is `=HYPERLINK("x")` → CSV line has `'=HYPERLINK`; header row exact; `content-type` starts with `text/csv`; `content-disposition` contains `dps-audit-`.
- `test_audit_never_contains_values`: obfuscate `{"ssn": "123-45-6789", "name": "Zebulon Quixote"}` through the API, then fetch audit JSON and CSV → neither sentinel present.
- `test_admin_actions_are_audited`: after POST keys, an audit row `action == "admin.key.create"`, `outcome == "ok"`, and its `counts` contain no key material (`"dps_" not in json.dumps(counts)`).
- `test_queue_endpoint_shape`: 200 with keys `queues`, `workers`.

- [ ] **Step 2: Run to confirm failure** → 404 on `/api/v1/admin/keys`.

- [ ] **Step 3: Implement** `dps/audit_query.py`, `dps/layers/api/routers/admin.py`, include the router in `create_app` (after M2's routers). The audit helper used by every M5 router:
```python
def audit_admin(request: Request, action: str, *, outcome: str = "ok", status: int = 200,
                counts: dict[str, Any] | None = None) -> None:
    """Value-free audit event for an admin/M5 action (written through app.state.audit)."""
    principal = getattr(request.state, "principal", None)
    event = AuditEvent(trace_id=trace_id(request), route=request.scope.get("route").path if request.scope.get("route") else request.url.path,
                       tenant_id=getattr(request.state, "tenant_id", "default"),
                       key_id=principal.key_id if principal else "anonymous", mode="admin", policy="-",
                       outcome=outcome, action=action, status=status, counts=counts or {})
    request.app.state.audit.write(event)
```
(`action`, `status`, `counts` are M2's `AuditEvent` extensions for the contract §2.4 columns; Task 0 proves `AuditEvent` accepts them — if M2 named them differently, use M2's names.) Map `GrantError`/`TenantError` to `DpsHttpError(e.status, e.code, e.detail)`. Rotation/disable/enable call `request.app.state.db_keys.invalidate(id)` then `await ...refresh()`.

- [ ] **Step 4: Run tests**: `pytest -q -m db tests/test_admin_api.py` → `8 passed`; unit gate green.

- [ ] **Step 5: Commit**

```bash
git add dps/audit_query.py dps/layers/api/routers/admin.py dps/app.py tests/test_admin_api.py docs/superpowers/plans/2026-10-11-m1-m5-contract.md
git commit -m "feat(api): admin routes for keys, tenants, audit search with CSV export, and queue health" -- dps/audit_query.py dps/layers/api/routers/admin.py dps/app.py tests/test_admin_api.py docs/superpowers/plans/2026-10-11-m1-m5-contract.md
```

---
### Task 12: Report roll-ups and the reports API

**Files:**
- Create: `dps/migrations/041_report_rollups.sql`, `dps/reports.py`, `dps/layers/api/routers/reports.py`
- Modify: `dps/db.py` (`TENANT_TABLES`), `dps/app.py` (router), contract §2.2, §9
- Test: `tests/test_reports.py` (`db`)

`dps/migrations/041_report_rollups.sql`:
```sql
-- 041 — daily, value-free aggregates behind the Overview and Reports pages (M5).
CREATE TABLE __SCHEMA__.dps_report_daily (
  tenant_id text NOT NULL,
  day date NOT NULL,                                          -- UTC
  layer text NOT NULL CHECK (layer IN ('api', 'conn', 'ui', 'admin')),
  action text NOT NULL,                                       -- audit action, or job.<kind> / job.partition
  mode text NOT NULL DEFAULT '-',
  outcome text NOT NULL,                                      -- audit outcome or job/partition state
  calls bigint NOT NULL DEFAULT 0,
  findings bigint NOT NULL DEFAULT 0,
  values_transformed bigint NOT NULL DEFAULT 0,
  verify_passed bigint NOT NULL DEFAULT 0,
  verify_failed bigint NOT NULL DEFAULT 0,
  rows_written bigint NOT NULL DEFAULT 0,
  pages bigint NOT NULL DEFAULT 0,
  fields bigint NOT NULL DEFAULT 0,
  blocked_requests bigint NOT NULL DEFAULT 0,
  refreshed_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, day, layer, action, mode, outcome));
CREATE TABLE __SCHEMA__.dps_report_entity_daily (
  tenant_id text NOT NULL, day date NOT NULL,
  layer text NOT NULL CHECK (layer IN ('api', 'conn', 'ui')),
  entity_type text NOT NULL CHECK (entity_type ~ '^[A-Z][A-Z0-9_]{0,63}$'),
  findings bigint NOT NULL DEFAULT 0,
  PRIMARY KEY (tenant_id, day, layer, entity_type));
CREATE TABLE __SCHEMA__.dps_report_refresh (                 -- which days were recomputed (also empty ones)
  tenant_id text NOT NULL, day date NOT NULL, refreshed_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, day));
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['dps_report_daily', 'dps_report_entity_daily', 'dps_report_refresh'] LOOP
    EXECUTE format('ALTER TABLE __SCHEMA__.%I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE __SCHEMA__.%I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format('CREATE POLICY tenant_isolation ON __SCHEMA__.%I USING (tenant_id = current_setting(''app.tenant_id'', true)) '
                   'WITH CHECK (tenant_id = current_setting(''app.tenant_id'', true))', t);
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON __SCHEMA__.%I TO dps_app_rw', t);
  END LOOP;
END $$;
-- roll-ups scan one tenant's day of audit rows; the partitioned parent index cascades to partitions
CREATE INDEX IF NOT EXISTS dps_audit_tenant_ts ON __SCHEMA__.dps_audit_log (tenant_id, ts);
```
`dps/db.py`: `TENANT_TABLES` += the three tables.

**Roll-up statements** (`dps/reports.py`; tokens `__S__` = quoted schema, `__LAYER__` = `layer_case("a.action")` from Task 11, `__W(col)__` = `col >= ($2::date)::timestamp AT TIME ZONE 'UTC' AND col < (($3::date) + 1)::timestamp AT TIME ZONE 'UTC'`; every statement takes `($1 tenant, $2 first day, $3 last day)`):
```sql
DELETE FROM __S__.dps_report_daily WHERE tenant_id = $1 AND day BETWEEN $2 AND $3;
DELETE FROM __S__.dps_report_entity_daily WHERE tenant_id = $1 AND day BETWEEN $2 AND $3;

-- 1 · API and admin layers from the audit log ('keep' is not a transformation)
INSERT INTO __S__.dps_report_daily (tenant_id, day, layer, action, mode, outcome, calls, findings,
       values_transformed, verify_passed, verify_failed)
SELECT a.tenant_id, (a.ts AT TIME ZONE 'UTC')::date, __LAYER__, a.action, coalesce(a.mode, '-'), a.outcome,
       count(*),
       coalesce(sum(CASE WHEN jsonb_typeof(a.counts->'findings_total') = 'number'
                         THEN (a.counts->>'findings_total')::bigint END), 0),
       coalesce(sum(m.n), 0),
       count(*) FILTER (WHERE a.counts->>'verification_passed' = 'true'),
       count(*) FILTER (WHERE a.counts->>'verification_passed' = 'false')
FROM __S__.dps_audit_log a
LEFT JOIN LATERAL (
  SELECT sum(e.value::bigint) AS n
  FROM jsonb_each_text(CASE WHEN jsonb_typeof(a.counts->'methods_used') = 'object'
                            THEN a.counts->'methods_used' ELSE '{}'::jsonb END) e
  WHERE e.key <> 'keep' AND e.value ~ '^[0-9]{1,18}$') m ON true
WHERE a.tenant_id = $1 AND __W(a.ts)__ AND __LAYER__ IN ('api', 'admin')
GROUP BY 1, 2, 3, 4, 5, 6;

-- 2 · findings by entity type, API layer
INSERT INTO __S__.dps_report_entity_daily (tenant_id, day, layer, entity_type, findings)
SELECT a.tenant_id, (a.ts AT TIME ZONE 'UTC')::date, 'api', e.key, sum(e.value::bigint)
FROM __S__.dps_audit_log a,
     jsonb_each_text(CASE WHEN jsonb_typeof(a.counts->'findings_by_type') = 'object'
                          THEN a.counts->'findings_by_type' ELSE '{}'::jsonb END) e
WHERE a.tenant_id = $1 AND __W(a.ts)__ AND __LAYER__ = 'api'
  AND e.key ~ '^[A-Z][A-Z0-9_]{0,63}$' AND e.value ~ '^[0-9]{1,18}$'
GROUP BY 1, 2, 4;

-- 3 · job-level rows: run/discover → conn, scan → ui (re-run safe: a retried job lands in its final state)
INSERT INTO __S__.dps_report_daily (tenant_id, day, layer, action, mode, outcome, calls, values_transformed,
       rows_written, pages, fields, blocked_requests)
SELECT j.tenant_id, (j.finished_at AT TIME ZONE 'UTC')::date,
       CASE WHEN j.kind = 'scan' THEN 'ui' ELSE 'conn' END, 'job.' || j.kind, coalesce(j.mode, '-'), j.state,
       count(*), coalesce(sum(v.n), 0),
       coalesce(sum(CASE WHEN jsonb_typeof(j.progress->'rows_written') = 'number' THEN (j.progress->>'rows_written')::bigint END), 0),
       coalesce(sum(CASE WHEN jsonb_typeof(j.progress->'pages') = 'number' THEN (j.progress->>'pages')::bigint END), 0),
       coalesce(sum(CASE WHEN jsonb_typeof(j.progress->'fields') = 'number' THEN (j.progress->>'fields')::bigint END), 0),
       coalesce(sum(CASE WHEN jsonb_typeof(j.progress->'blocked_requests') = 'number' THEN (j.progress->>'blocked_requests')::bigint END), 0)
FROM __S__.dps_job j
LEFT JOIN LATERAL (
  SELECT sum(e.value::bigint) AS n
  FROM jsonb_each_text(CASE WHEN jsonb_typeof(j.progress->'values_by_method') = 'object'
                            THEN j.progress->'values_by_method' ELSE '{}'::jsonb END) e
  WHERE e.key <> 'keep' AND e.key NOT LIKE '%\_resample' AND e.value ~ '^[0-9]{1,18}$') v ON true
WHERE j.tenant_id = $1 AND j.kind IN ('run', 'discover', 'scan') AND j.finished_at IS NOT NULL AND __W(j.finished_at)__
GROUP BY 1, 2, 3, 4, 5, 6;

-- 4 · leakage sweeps: one per finished partition (passed = done, failed = failed closed on leakage)
INSERT INTO __S__.dps_report_daily (tenant_id, day, layer, action, mode, outcome, calls, verify_passed,
       verify_failed, rows_written)
SELECT p.tenant_id, (p.finished_at AT TIME ZONE 'UTC')::date, 'conn', 'job.partition', coalesce(j.mode, '-'), p.state,
       count(*), count(*) FILTER (WHERE p.state = 'done'), count(*) FILTER (WHERE p.error_code = 'leakage'),
       coalesce(sum(p.rows_written), 0)
FROM __S__.dps_job_partition p JOIN __S__.dps_job j ON j.id = p.job_id
WHERE p.tenant_id = $1 AND p.state IN ('done', 'failed', 'dead') AND p.finished_at IS NOT NULL AND __W(p.finished_at)__
GROUP BY 1, 2, 3, 4, 5, 6;

-- 5 · findings by entity type from evidence packs (conn: transformed values per column; ui: scan.by_entity)
INSERT INTO __S__.dps_report_entity_daily AS r (tenant_id, day, layer, entity_type, findings)
SELECT e.tenant_id, (e.created_at AT TIME ZONE 'UTC')::date, 'conn', c->>'entity_type',
       sum(CASE WHEN jsonb_typeof(c->'values') = 'number' THEN (c->>'values')::bigint ELSE 0 END)
FROM __S__.dps_evidence e,
     jsonb_array_elements(CASE WHEN jsonb_typeof(e.pack->'columns') = 'array' THEN e.pack->'columns' ELSE '[]'::jsonb END) c
WHERE e.tenant_id = $1 AND __W(e.created_at)__ AND coalesce(e.pack->>'job_kind', 'run') <> 'scan'
  AND (c->>'entity_type') ~ '^[A-Z][A-Z0-9_]{0,63}$'
GROUP BY 1, 2, 4
ON CONFLICT (tenant_id, day, layer, entity_type) DO UPDATE SET findings = r.findings + EXCLUDED.findings;
INSERT INTO __S__.dps_report_entity_daily AS r (tenant_id, day, layer, entity_type, findings)
SELECT e.tenant_id, (e.created_at AT TIME ZONE 'UTC')::date, 'ui', b.key, sum(b.value::bigint)
FROM __S__.dps_evidence e,
     jsonb_each_text(CASE WHEN jsonb_typeof(e.pack->'scan'->'by_entity') = 'object'
                          THEN e.pack->'scan'->'by_entity' ELSE '{}'::jsonb END) b
WHERE e.tenant_id = $1 AND __W(e.created_at)__ AND b.key ~ '^[A-Z][A-Z0-9_]{0,63}$' AND b.value ~ '^[0-9]{1,18}$'
GROUP BY 1, 2, 4
ON CONFLICT (tenant_id, day, layer, entity_type) DO UPDATE SET findings = r.findings + EXCLUDED.findings;

-- 6 · mark every day in the range as refreshed (also empty days)
INSERT INTO __S__.dps_report_refresh (tenant_id, day, refreshed_at)
SELECT $1, d::date, now() FROM generate_series($2::date, $3::date, interval '1 day') d
ON CONFLICT (tenant_id, day) DO UPDATE SET refreshed_at = now();
```
Rows aggregate counts only — an action name, a mode, an outcome, an entity type, numbers.

Interfaces (`dps/reports.py`):
```python
async def refresh_rollups(tenant_id: str, first: date, last: date, *, settings: Settings,
                          worker: bool = False) -> bool          # False: another refresh holds the lock
async def ensure_fresh(tenant_id: str, first: date, last: date, *, settings: Settings) -> None
class TrendPoint(BaseModel): day: date; obfuscated: int; synthesized: int
class EntityCount(BaseModel): entity_type: str; findings: int; sensitivity: str
class JobBrief(BaseModel): id: str; kind: str; mode: str | None; state: str; source: str | None; target: str | None
                           progress_pct: int | None; note: str | None   # value-free ("8 partitions", "fidelity 0.97")
class SummaryOut(BaseModel):
    tenant_id: str; days: int; generated_at: datetime
    tiles: dict[str, int | float]   # values_protected, values_protected_prev, findings, entity_types, jobs_running,
                                    # jobs_completed, jobs_failed, snapshots_awaiting_review, raw_pii_sent_to_llm, llm_calls
    trend: list[TrendPoint]; findings_by_type: list[EntityCount]
    layers: dict[str, dict[str, Any]]   # api{requests, by_mode}, conn{rows, by_engine, groups}, ui{scans, pages, fields, risk_flags, submissions, last_scan}
    posture: dict[str, float]           # sweeps_passed_pct, key_held_by_caller_pct, snapshots_reviewed_pct, policies_latest_pct, score
    recent_jobs: list[JobBrief]
class SweepDay(BaseModel): day: date; layer: str; passed: int; failed: int
class FailedSweep(BaseModel): where: Literal["job", "api"]; day: date; job_id: str | None; partitions: list[int]
                              route: str | None; error_code: str; paths: list[str]
class ReportsJobsOut(BaseModel): days: int; sweeps: list[SweepDay]; totals: dict[str, int]; failed_sweeps: list[FailedSweep]
class EvidenceItem(BaseModel): id: str; job_id: str; job_kind: str; outcome: str; created_at: datetime; pack_sha256: str
                               layer: str; summary: dict[str, Any]   # mode, policy, policy_version, ticket, source, target,
                                                                     # write_mode, rows_written, verification_passed, hits,
                                                                     # fidelity_score, exact_row_matches, pages, submissions
class ApiRollup(BaseModel): day: date; action: str; calls: int; findings: int; values: int; verify_failed: int
async def summary(tenant_id: str, days: int, settings: Settings) -> SummaryOut
async def jobs_report(tenant_id: str, days: int, settings: Settings) -> ReportsJobsOut
async def evidence_list(tenant_id: str, *, days: int, layer: str | None, outcome: str | None, q: str | None,
                        limit: int, cursor: str | None, include_scans: bool) -> tuple[list[EvidenceItem], str | None]
async def api_rollups(tenant_id: str, days: int) -> list[ApiRollup]
async def api_rollup_doc(tenant_id: str, day: date, action: str) -> dict[str, Any]   # JSON body for download/PDF
```
`ensure_fresh`: days in `[first, last]` with no `dps_report_refresh` row, plus today and yesterday when their `refreshed_at` is older than 60 s, are recomputed in one `refresh_rollups(min, max)` call. `refresh_rollups` runs every statement in one transaction after `pg_try_advisory_xact_lock(hashtext('dps.rollup.' || $1))` (under `acquire(tenant)`; `acquire_queue(tenant)` when `worker=True`).

`summary` reads `dps_report_daily`/`dps_report_entity_daily` for `[today − days + 1, today]` and the previous window of equal length (for `values_protected_prev`) and computes:
- `values_protected` = Σ `values_transformed` of rows with action ∈ {obfuscate, synthesize, `document.*`} (API) and `job.run`, `job.scan`; `findings` = Σ entity findings; `entity_types` = distinct entity types; trend = per day, the same sums split by `mode` (`obfuscate` → obfuscated, `synthesize` → synthesized), every day present (zeros filled);
- live counts: `SELECT state, count(*) FROM dps_job WHERE tenant_id = $1 AND kind IN ('run','scan','discover') GROUP BY state` (running = running+waiting), completed/failed from the window's `job.*` rows; `SELECT count(*) FROM dps_snapshot WHERE tenant_id = $1 AND status = 'proposed'`;
- `raw_pii_sent_to_llm` = the process counter `LLM_RAW_PII` (always 0 by construction, spec P1) and `llm_calls` = Σ `LLM_CALLS` samples, both labelled in the UI "since process start";
- layers: api requests and `by_mode` from API rows; conn `rows` = Σ `rows_written` of `job.run`, `by_engine` = `SELECT c.engine, sum((j.progress->>'rows_written')::bigint) FROM dps_job j JOIN dps_connection c ON c.id = j.connection_id WHERE j.tenant_id = $1 AND j.kind = 'run' AND j.finished_at >= <window start> GROUP BY c.engine`, `groups` = open job groups; ui `scans/pages/fields` from `job.scan` rows, `risk_flags` = `SELECT coalesce(sum(jsonb_array_length(risk_flags)), 0) FROM dps_scan_page WHERE tenant_id = $1 AND created_at >= <window start>`, `submissions` = 0 (the evidence of every scan asserts it), `last_scan` = `{id, pages, fields, personas}` of the newest scan job;
- posture (0–100): `sweeps_passed_pct` = passed/(passed+failed) over API rows and `job.partition` rows (100 when none); `key_held_by_caller_pct` = 100 by design (D2; the UI says so); `snapshots_reviewed_pct` = share of run jobs in the window with an accepted `snapshot_id`; `policies_latest_pct` = share of run jobs in the window whose `spec.policy` name+version equals the tenant's active version (presets count as latest); `score` = their mean;
- `recent_jobs` = `list_jobs(tenant, limit=6)` mapped to `JobBrief` (source = `object_ref`, target = `spec.target.object.fqn` or `files`, `progress_pct` = partitions done/total, note = `"<n> partitions"` while running, `"fidelity <score>"` for finished synthesize jobs, `"awaiting review"` for discover jobs whose snapshot is proposed).

`jobs_report`: sweeps per day and layer (API rows: `verify_passed`/`verify_failed`; conn: `job.partition`; ui: `job.scan` succeeded = passed, failed/dead = failed); totals `{packs, run_packs, scan_packs, api_rollups, sweeps_passed, sweeps_failed, audit_rows}` (`audit_rows` = `SELECT count(*) FROM dps_audit_log WHERE tenant_id = $1 AND ts >= <window start>` under `SET LOCAL statement_timeout = '5s'`, falling back to Σ API `calls` on timeout); `failed_sweeps` = partitions with `error_code = 'leakage'` grouped by job (job id, partition numbers, the job's evidence `errors[*].where` paths) and audit rows with `outcome = 'verification_failed'` (route, day).

`evidence_list` reads `dps_evidence e JOIN dps_job j` for the window, newest first, keyset on `(created_at, id)`, and builds `summary` in Python from the pack (only the keys listed; never `reveal_map`, never `partitions`). `layer` = `ui` for scan packs else `conn`; `outcome` filter: `ok` → `succeeded|dry_run`, `failed` → `failed|canceled`; `q` matches job id, ticket, policy name or object (case-insensitive).

Routes (`dps/layers/api/routers/reports.py`; contract §9 rows M5 extended):

| Method · path | Scope | Query | Response |
|---|---|---|---|
| `GET /api/v1/reports/summary` | detect | `days` 1–90 (14) | `SummaryOut` |
| `GET /api/v1/reports/jobs` | detect | `days` 1–90 (14) | `ReportsJobsOut` |
| `GET /api/v1/reports/evidence` | jobs (scan packs need scan too) | `days` 1–90 (30), `layer`, `outcome`, `q`, `limit`, `cursor` | `{"items": [EvidenceItem], "next_cursor"}` |
| `GET /api/v1/reports/api-rollups` | detect | `days` 1–90 (14) | `{"items": [ApiRollup]}` |
| `GET /api/v1/reports/api-rollups/{day}` | detect | `action` (required), `format=json` (`pdf` in Task 14) | roll-up document, `Content-Disposition: attachment; filename="api-rollup-<day>-<action>.json"` |

Each route calls `ensure_fresh` for its window first.

- [ ] **Step 1: Write the failing tests** — `tests/test_reports.py` (`@pytest.mark.db`; audit rows are inserted directly into `dps_audit_log` under `acquire(tenant)` with realistic `counts`):
- `test_refresh_rolls_up_audit_rows_by_layer_and_day`: rows for tenant `r1` today: 2 × `obfuscate` with `counts={"findings_total": 4, "findings_by_type": {"SSN": 2, "EMAIL": 2}, "methods_used": {"fpe_digits": 3, "keep": 5}, "verification_passed": true}`, 1 × `job.create`, 1 × `admin.key.create` → after `refresh_rollups(r1, today, today)`: the `api/obfuscate/ok` row has `calls == 2, findings == 8, values_transformed == 6, verify_passed == 2`; an `admin` row exists; no `api` row for `job.create` (layer `conn` rows come only from job tables); entity rows `SSN == 4`, `EMAIL == 4`; a `dps_report_refresh` row for today.
- `test_refresh_is_idempotent_and_locked`: two refreshes → identical sums; while one transaction holds the advisory lock, a second `refresh_rollups` returns `False`.
- `test_jobs_and_partitions_roll_up`: a finished run job (`progress = {"rows_written": 1000, "values_by_method": {"fpe_digits": 900, "keep": 50, "copula_resample": 3}}`) with 3 partitions (2 `done`, 1 `failed` with `error_code='leakage'`) → `job.run` row `rows_written == 1000, values_transformed == 900`; `job.partition` rows total `verify_passed == 2, verify_failed == 1`.
- `test_summary_over_http`: `GET /api/v1/reports/summary?days=14` → 200; `len(trend) == 14` with today last; `tiles.values_protected == 6 + 900`; `findings_by_type[0].entity_type in {"SSN","EMAIL"}`; `0 <= posture.score <= 100`; `tiles.raw_pii_sent_to_llm == 0`; response text contains no sentinel value inserted anywhere in the fixtures.
- `test_reports_jobs_lists_failed_sweeps`: `GET /api/v1/reports/jobs?days=7` → the leakage partition appears in `failed_sweeps` with `where == "job"` and its partition number; `totals.sweeps_failed == 1`.
- `test_evidence_list_summaries_and_filters`: two evidence rows (an obfuscate pack and a synthesize pack with `fidelity.score = 0.96`, `privacy.exact_row_matches = 0`) → items carry `summary.fidelity_score == 0.96` for the synthetic one; `outcome=failed` filters to none; `q=<ticket>` finds one; the item JSON never contains `reveal_map` or `partitions`.
- `test_api_rollups_list_and_json_document`: `/reports/api-rollups?days=1` lists `obfuscate` with `calls == 2`; `/reports/api-rollups/<today>?action=obfuscate` returns `{day, action, calls, findings, values, verify_passed, verify_failed, by_outcome, entities, generated_at, sha256}` with a `content-disposition` attachment.
- `test_rollups_are_tenant_isolated`: tenant `r2`'s summary sees none of `r1`'s numbers; under `acquire("r2")` the roll-up tables return no `r1` rows.

- [ ] **Step 2: Run to confirm failure** → 404 on `/api/v1/reports/summary`.
- [ ] **Step 3: Implement** the migration, `dps/reports.py` (statements above, assembled by `_statements(schema)` with the token replacements; queries as specified), the router, and register it in `create_app`.
- [ ] **Step 4: Run tests**: `pytest -q -m db tests/test_reports.py` → `8 passed`; unit gate green.
- [ ] **Step 5: Commit**

```bash
git add dps/migrations/041_report_rollups.sql dps/reports.py dps/layers/api/routers/reports.py dps/db.py dps/app.py tests/test_reports.py docs/superpowers/plans/2026-10-11-m1-m5-contract.md
git commit -m "feat(reports): value-free daily roll-ups and the summary, sweeps, evidence and API roll-up endpoints" -- dps/migrations/041_report_rollups.sql dps/reports.py dps/layers/api/routers/reports.py dps/db.py dps/app.py tests/test_reports.py docs/superpowers/plans/2026-10-11-m1-m5-contract.md
```

---

### Task 13: Retention job and scheduler

**Files:**
- Create: `dps/retention.py`
- Modify (M2): `dps/worker.py`; `dps/layers/api/routers/admin.py` (`POST /admin/retention/run`); contract §9
- Test: `tests/test_retention.py` (`db`)

Behaviour (contract §3.6, extended for M5 tables):
- **What is deleted, per tenant** (GUC bound via `acquire_queue(tenant)`; tenants from `tenant_directory()`): run/scan/discover jobs with `expires_at < now()` — objects first (`ArtefactService.delete_for_job`), then `dps_scan_request` rows of those jobs (no FK), then the job rows (cascade: partitions, artefact rows, evidence, scan pages/fields); `proposed`/`superseded` snapshots older than the tenant's retention; `dps_connection.secret_enc` past `secret_expires_at` (nulled); `key_enc` of any terminal job (nulled, defence in depth); roll-up, refresh-marker and usage rows older than `DPS_ROLLUP_RETENTION_DAYS` (400). System tenant `_dps`: retention jobs finished more than `DPS_RETENTION_DAYS` ago.
- **Never touched:** `dps_audit_log` (partitions are dropped only by `python -m dps.migrate --maintain`), accepted snapshots, policies and their versions, API keys.
- After deletion the job refreshes roll-ups for `[today − 2, today]` for every tenant (`refresh_rollups(..., worker=True)`).
- `dry_run` counts without deleting.
- Job bookkeeping: `progress = {"outcome": "succeeded" | "dry_run", "metrics": {"retention": RetentionCounts}}`; one audit event `retention.run` (tenant `_dps`) with the counts.

Interfaces:
```python
SYSTEM_TENANT = "_dps"      # re-exported from dps.tenants
class RetentionCounts(BaseModel):
    tenants: int = 0; jobs: int = 0; objects: int = 0; snapshots: int = 0; secrets_purged: int = 0
    keys_purged: int = 0; scan_requests: int = 0; rollup_rows: int = 0; usage_rows: int = 0; system_jobs: int = 0
async def run_retention(*, settings: Settings, artefacts: ArtefactService, dry_run: bool) -> RetentionCounts
async def retention_job(ctx: HandlerContext, job: ClaimedJob) -> None          # handler for JobPhase.retention
async def enqueue_retention(*, dry_run: bool, created_by: str | None) -> Job
class RetentionScheduler:
    def __init__(self, settings: Settings) -> None
    async def tick(self) -> str | None          # job id when it enqueued one
    async def run(self, stop: asyncio.Event) -> None
```

Per-tenant SQL (all with `$1 = tenant`; `$2 = now`; batches of 200 ids):
```sql
SELECT id FROM dps_job WHERE tenant_id = $1 AND expires_at IS NOT NULL AND expires_at < $2
 ORDER BY expires_at LIMIT 200;
DELETE FROM dps_scan_request WHERE tenant_id = $1 AND job_id = ANY($2::text[]);
DELETE FROM dps_job WHERE tenant_id = $1 AND id = ANY($2::text[]) AND expires_at < $3;   -- re-checked
DELETE FROM dps_snapshot WHERE tenant_id = $1 AND status IN ('proposed', 'superseded')
   AND created_at < $2 - make_interval(days => $3);
UPDATE dps_connection SET secret_enc = NULL, updated_at = now()
 WHERE tenant_id = $1 AND secret_enc IS NOT NULL AND secret_expires_at < $2;
UPDATE dps_job SET key_enc = NULL
 WHERE tenant_id = $1 AND key_enc IS NOT NULL AND state IN ('succeeded', 'failed', 'dead', 'canceled');
DELETE FROM dps_report_daily WHERE tenant_id = $1 AND day < ($2 AT TIME ZONE 'UTC')::date - $3::int;
DELETE FROM dps_report_entity_daily WHERE tenant_id = $1 AND day < ($2 AT TIME ZONE 'UTC')::date - $3::int;
DELETE FROM dps_report_refresh WHERE tenant_id = $1 AND day < ($2 AT TIME ZONE 'UTC')::date - $3::int;
DELETE FROM dps_tenant_usage WHERE tenant_id = $1 AND day < ($2 AT TIME ZONE 'UTC')::date - $3::int;
-- system tenant (acquire_queue('_dps')):
DELETE FROM dps_job WHERE tenant_id = '_dps' AND kind = 'retention' AND finished_at < $1 - make_interval(days => $2);
```
In `dry_run` each `DELETE`/`UPDATE` becomes the matching `SELECT count(*)`.

Scheduler (single enqueue across replicas — a session advisory lock held across the check and the insert):
```python
class RetentionScheduler:
    """Every worker runs one; at most one retention job is enqueued per interval across replicas."""

    LOCK = "dps.retention.schedule"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def tick(self) -> str | None:
        hours = self._settings.retention_interval_hours
        async with acquire_queue(SYSTEM_TENANT) as conn:
            if not await conn.fetchval("SELECT pg_try_advisory_lock(hashtext($1))", self.LOCK):
                return None
            try:
                recent = await conn.fetchval(
                    "SELECT count(*) FROM dps_job WHERE kind = 'retention' "
                    "AND created_at > now() - make_interval(hours => $1)", hours)
                if recent:
                    return None
                job = await enqueue_retention(dry_run=False, created_by="scheduler")
                logger.info("retention_enqueued", job_id=job.id)
                return job.id
            finally:
                await conn.execute("SELECT pg_advisory_unlock(hashtext($1))", self.LOCK)

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                await self.tick()
            except (asyncpg.PostgresError, OSError) as exc:
                logger.warning("retention_schedule_failed", error=type(exc).__name__)
            try:
                await asyncio.wait_for(stop.wait(), timeout=300)   # look every 5 min; the interval gate is in SQL
            except TimeoutError:
                pass
```
(The `SELECT ... FROM dps_job` under the worker login sees every tenant through the `worker_claim` policy; use the schema-qualified table name exactly as M2's module renders it.)

`enqueue_retention` = `create_job(tenant_id=SYSTEM_TENANT, kind=JobKind.retention, phase=JobPhase.retention, spec=RetentionSpec(dry_run=dry_run).model_dump(mode="json"), created_by=created_by, priority=200)`.

M2 touch (`dps/worker.py`): the `handlers` passed to `Consumer` gain `JobPhase.retention: retention_job`; `kinds` contains `JobKind.retention` (contract §3.5); `main()` starts `asyncio.create_task(RetentionScheduler(settings).run(stop))` next to `Consumer.run()` and sets `stop` on SIGTERM.

Admin route (contract §9 addition): `POST /api/v1/admin/retention/run` body `{"dry_run": bool = false}` → 202 `{"job": Job}`; audit `admin.retention.run`.

- [ ] **Step 1: Write the failing tests** — `tests/test_retention.py` (`@pytest.mark.db`; `object_store` fixture = local tmp store; an `ArtefactService` over it):
- `test_dry_run_counts_without_deleting`: one expired job with one artefact → `run_retention(dry_run=True)` → `counts.jobs == 1, counts.objects == 1`; the job row and the object still exist.
- `test_expired_jobs_and_their_objects_are_deleted`: expired job (artefact + evidence + 2 partitions + 1 scan request row) and an unexpired job → after a real run: expired job, its artefact row, its object, its evidence, partitions and scan-request rows are gone; the unexpired job is intact; `counts.jobs == 1`.
- `test_snapshots_proposed_and_superseded_age_out_accepted_stay`: three 40-day-old snapshots (proposed, superseded, accepted) → only the accepted one remains.
- `test_inline_secrets_are_purged_after_expiry`: a connection with `secret_enc` and `secret_expires_at = now() - 1 hour` → `secret_enc IS NULL`; another with a future expiry keeps its blob.
- `test_terminal_jobs_lose_key_enc`: a `succeeded` job that still has `key_enc` → NULL; a `running` job keeps it.
- `test_audit_log_is_never_touched`: audit row count before == after (including rows older than every retention window).
- `test_scheduler_enqueues_once_across_two_replicas`: `asyncio.gather(s1.tick(), s2.tick())` → exactly one job id returned and one `retention` job row in the last interval; a third tick → `None`.
- `test_handler_records_counts_and_refreshes_rollups`: run `retention_job` with a `HandlerContext` built from the test settings on a claimed retention job → job `succeeded`, `progress.metrics.retention.tenants >= 1`, a `dps_report_refresh` row for today exists for a tenant with jobs, one `retention.run` audit row under `_dps`.
- `test_admin_route_enqueues_a_retention_job`: `POST /api/v1/admin/retention/run {"dry_run": true}` with an admin key → 202, `job.kind == "retention"`, `job.tenant_id == "_dps"`.

- [ ] **Step 2: Run to confirm failure** → `ModuleNotFoundError: No module named 'dps.retention'`.
- [ ] **Step 3: Implement** `dps/retention.py` (SQL above; `run_retention` loops tenants, catches `asyncpg.PostgresError` per tenant, logs `retention_tenant_failed` with the exception class only and continues), the worker wiring and the admin route.
- [ ] **Step 4: Run tests**: `pytest -q -m db tests/test_retention.py` → `9 passed`; unit gate green.
- [ ] **Step 5: Commit**

```bash
git add dps/retention.py dps/worker.py dps/layers/api/routers/admin.py tests/test_retention.py docs/superpowers/plans/2026-10-11-m1-m5-contract.md
git commit -m "feat: retention job and single-enqueue scheduler — expire jobs, objects, snapshots, inline secrets; refresh roll-ups" -- dps/retention.py dps/worker.py dps/layers/api/routers/admin.py tests/test_retention.py docs/superpowers/plans/2026-10-11-m1-m5-contract.md
```

---

### Task 14: Evidence PDF and API roll-up PDF

**Files:**
- Create: `dps/layers/reports/__init__.py`, `dps/layers/reports/pdf.py`
- Modify: `dps/layers/api/routers/reports.py` (PDF routes), `HANDOFF.md`
- Test: `tests/test_evidence_pdf.py` (unit; `db` for the route tests)

Routes: `GET /api/v1/evidence/{evidence_id}/pdf` (contract §9: "M5 adds"; scope `jobs`, or `scan` for scan packs — the same rule as `GET /evidence/{id}`), and `format=pdf` on `GET /api/v1/reports/api-rollups/{day}`. The evidence PDF is rendered on first request (`asyncio.to_thread`), stored as an artefact of kind `evidence_pdf` (`meta = {"pack_sha256": ...}`), linked by `UPDATE dps_evidence SET pdf_artefact_id = $2 WHERE id = $1` (allowed by M2's append-only trigger), audited (`evidence.pdf`), and streamed from the artefact afterwards. `Content-Type: application/pdf`, `Content-Disposition: attachment; filename="evidence-<evidence_id>.pdf"`. `DPS_REPORT_PDF_ENABLED=false` → 404 `{"code": "pdf_disabled"}`.

Interface:
```python
def render_evidence_pdf(pack: Mapping[str, Any], *, pack_sha256: str, snapshot: Mapping[str, Any] | None = None) -> bytes
def render_rollup_pdf(doc: Mapping[str, Any]) -> bytes
def pdf_text(value: Any) -> str      # XML-escaped, folded to the Latin-1 subset of the standard fonts
```

Sections (mockup 10, same order and headings): header (eyebrow "Data Privacy Services · evidence pack", title `"<source engine> <source object> -> <target object | files | in place>"`, job line, seal: verification result, sealed time, pack id, short sha256) · meta grid (tenant, requested by key id, purpose ticket, legal basis, job group, mode · write mode, outcome) · **1 Summary** (one sentence built from counts + six stat cells: rows written, columns transformed, values transformed, leakage hits, duration, raw values to LLM = 0) · **2 Configuration** (policy name vN + hash, config hash, engine version + image digest, key id + scope + custody, source, target, job group) · **3 Classification snapshot** (path, type, entity, confidence, method + params, decision = accepted/overridden) · **4 Execution** (partitions table: no, rows, attempts, resumed, state + totals; timings) · **5 Verification** (leakage sweep, detectors re-run on output, FPE fallbacks, row counts) · **6 Synthetic** (only with `fidelity`: per-column table with metric, distance, limit, null-rate delta; correlation summary and the lower-triangle delta matrix; privacy table: exact-row matches, resamples, NNDR ratio/medians/target/pass, QI uniqueness source vs synthetic + overlap, membership inference "not run" with reason; for `tabular == none` the single line "privacy not measured — non-sensitive columns were copied") · **UI scan** (only with `scan`: pages, forms, fields, personas, submissions = 0, blocked requests by method) · **7 Regulatory note** (the pack `label`: obfuscate → pseudonymised wording; synthesize → measurement-not-claim wording) · **8 Signatures** (system attestation filled with version, time, sha256; data owner = snapshot `accepted_by`; DPO; change approver — signature lines) · footer on every page (`DPS evidence pack · job <id> · page n` and the full pack sha256).

Core of `dps/layers/reports/pdf.py` (helpers and two sections in full; the other sections follow the same `_section`/`_table`/`_kv` pattern with the fields listed above):
```python
"""Evidence-pack and API roll-up PDFs (reportlab platypus, built-in Helvetica — no fonts shipped,
no network). Every string from a pack goes through ``pdf_text``: XML-escaped, because Paragraph
parses mini-markup and an unescaped name such as ``<link href=...>`` would become a live link,
and folded to Latin-1 because the standard fonts have no glyphs beyond it. The PDF carries only
what the pack carries: counts, hashes, names, paths, methods.
"""
from __future__ import annotations

import io
from collections.abc import Mapping, Sequence
from typing import Any
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from dps import __version__

_FOLD = {"→": "->", "≥": ">=", "≤": "<=", "−": "-", "–": "-", "—": "-", "…": "...", "×": "x",
         "Δ": "delta", "⁶": "^6", "✓": "ok", "✕": "x", " ": " "}
_STYLES = getSampleStyleSheet()
H1 = ParagraphStyle("h1", parent=_STYLES["Heading1"], fontName="Helvetica-Bold", fontSize=15, spaceAfter=4)
H2 = ParagraphStyle("h2", parent=_STYLES["Heading2"], fontName="Helvetica-Bold", fontSize=11, spaceBefore=10, spaceAfter=4)
BODY = ParagraphStyle("body", parent=_STYLES["BodyText"], fontName="Helvetica", fontSize=8.5, leading=11)
SMALL = ParagraphStyle("small", parent=BODY, fontSize=7.5, textColor=colors.HexColor("#4a4a4a"))
MONO = ParagraphStyle("mono", parent=BODY, fontName="Courier", fontSize=7.5)
_GRID = TableStyle([("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 7.5), ("FONT", (0, 1), (-1, -1), "Helvetica", 7.5),
                    ("LINEBELOW", (0, 0), (-1, 0), 0.75, colors.black), ("LINEBELOW", (0, 1), (-1, -1), 0.25, colors.grey),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 3),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 3)])


def pdf_text(value: Any) -> str:
    text = "" if value is None else str(value)
    for src, dst in _FOLD.items():
        text = text.replace(src, dst)
    return escape(text.encode("latin-1", "replace").decode("latin-1"))


def _p(value: Any, style: ParagraphStyle = BODY) -> Paragraph:
    return Paragraph(pdf_text(value), style)


def _table(header: Sequence[str], rows: Sequence[Sequence[Any]], widths: Sequence[float]) -> Table:
    data = [[_p(h, SMALL) for h in header]] + [[_p(c) for c in row] for row in rows]
    table = Table(data, colWidths=[w * mm for w in widths], repeatRows=1)
    table.setStyle(_GRID)
    return table


def _kv(rows: Sequence[tuple[str, Any]]) -> Table:
    table = Table([[_p(k, SMALL), _p(v)] for k, v in rows], colWidths=[42 * mm, 128 * mm])
    table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.grey)]))
    return table


def _section(n: int, title: str, *flow: Any) -> list[Any]:
    return [KeepTogether([_p(f"{n}  {title}", H2), *flow[:1]]), *flow[1:]]


def _fidelity(pack: Mapping[str, Any]) -> list[Any]:
    fid, priv = pack.get("fidelity") or {}, pack.get("privacy") or {}
    if not fid:
        if priv.get("engine") == "none":
            return _section(6, "Synthetic data", _p("Privacy not measured: non-sensitive columns were copied from the source (options.tabular=none)."))
        return []
    limit = (fid.get("limits") or {}).get("column", 0.05)
    cols = [[c.get("path"), c.get("metric", "").upper(), f"{c.get('distance', 0):.3f}", f"{limit:.2f}",
             f"{100 * c.get('null_rate_delta', 0):+.1f} pp"] for c in fid.get("columns", [])]
    nn = priv.get("nndr") or {}
    qi = priv.get("qi_uniqueness") or {}
    privacy_rows = [
        ["Exact-row matches", f"{priv.get('exact_row_matches', 0)}", "enforced: matching rows are redrawn before writing"],
        ["Rows redrawn", f"{priv.get('exact_row_resamples', 0)} (preview) / {priv.get('exact_row_resamples_run', 0)} (run)", ""],
        ["Nearest-neighbour distance ratio", f"{nn.get('ratio', 0):.2f}",
         f"median synthetic->real {nn.get('synth_to_real_median', 0):.3f} vs real->real {nn.get('real_to_real_median', 0):.3f}; "
         f"target {nn.get('target', 0):.2f}: {'pass' if nn.get('passed') else 'below target'}"],
        ["Quasi-identifier uniqueness", f"{100 * qi.get('synthetic', 0):.1f}% (source {100 * qi.get('source', 0):.1f}%)",
         f"{qi.get('unique_overlap', 0)} unique synthetic rows match a unique source row"],
        ["Membership-inference proxy", "not run", (priv.get("membership_inference") or {}).get("reason", "")]]
    flow: list[Any] = [_p(f"Engine {fid.get('engine', 'copula')}; summary score {fid.get('score', 0):.2f} "
                          f"(1 - mean column distance - mean correlation delta {fid.get('corr_delta', 0):.3f}). "
                          f"Measured on {(fid.get('measured_on') or {}).get('synthetic_rows', 0)} preview rows.")]
    flow += [Spacer(1, 3 * mm), _table(["Column", "Metric", "Distance", "Limit", "Null rate delta"], cols, [60, 18, 22, 18, 30])]
    names = (fid.get("corr_matrix") or {}).get("columns", [])
    delta = (fid.get("corr_matrix") or {}).get("delta", [])
    if len(names) >= 2:
        rows = [[names[i]] + [f"{delta[i][j]:.3f}" if j < i else "" for j in range(len(names) - 1)] for i in range(1, len(names))]
        width = 120 / max(1, len(names) - 1)
        flow += [Spacer(1, 3 * mm), _p("Correlation-matrix delta |synthetic - source|", SMALL),
                 _table([""] + names[:-1], rows, [40] + [width] * (len(names) - 1))]
    flow += [Spacer(1, 3 * mm), _table(["Privacy check", "Result", "What it means"], privacy_rows, [45, 35, 90])]
    return _section(6, "Synthetic data — fidelity and privacy", *flow)


def _footer(job_id: str, sha: str) -> Any:
    def draw(canvas: Any, doc: Any) -> None:
        canvas.saveState()
        canvas.setFont("Helvetica", 6.5)
        canvas.drawString(15 * mm, 10 * mm, pdf_text(f"DPS evidence pack · job {job_id} · page {doc.page}"))
        canvas.drawRightString(195 * mm, 10 * mm, pdf_text(f"pack sha256 {sha}"))
        canvas.restoreState()
    return draw


def render_evidence_pdf(pack: Mapping[str, Any], *, pack_sha256: str, snapshot: Mapping[str, Any] | None = None) -> bytes:
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm, topMargin=15 * mm,
                            bottomMargin=18 * mm, title="DPS evidence pack", author=f"Data Privacy Services {__version__}")
    story: list[Any] = [*_header(pack, pack_sha256), *_meta(pack), *_summary(pack), *_configuration(pack),
                        *_snapshot(pack, snapshot), *_execution(pack), *_verification(pack), *_fidelity(pack),
                        *_scan(pack), *_regulatory(pack), *_signatures(pack, pack_sha256, snapshot)]
    footer = _footer(str(pack.get("job_id", "")), pack_sha256)
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()
```
`_header`, `_meta`, `_summary`, `_configuration`, `_snapshot`, `_execution`, `_verification`, `_scan`, `_regulatory`, `_signatures` each return a list of flowables built only with `_p`, `_table`, `_kv`, `_section` from the pack keys named in the section list above (missing keys render as "—"). `render_rollup_pdf` renders a one-page document: header ("API roll-up · <action> · <day>"), a `_kv` of calls, findings, values, verification passed/failed, a table of outcomes and of entity types, the document sha256 in the footer.

- [ ] **Step 1: Write the failing tests** — `tests/test_evidence_pdf.py`:
```python
from __future__ import annotations

import io

import pytest

pytest.importorskip("reportlab")
pypdf = pytest.importorskip("pypdf")

from dps.layers.reports.pdf import pdf_text, render_evidence_pdf, render_rollup_pdf  # noqa: E402

SHA = "6ec5e6ddf67837f2b91df0fea306b9db0fcd146c58a7d233c79d6bae6ab7d73b"
BASE = {"pack_version": "dps.evidence.v1", "id": "ev_1", "tenant_id": "retail", "job_id": "j_01K7", "job_kind": "run",
        "outcome": "succeeded", "created_at": "2026-10-11T09:49:12Z", "engine_version": "1.0.0",
        "label": "synthetic — measured, not claimed anonymous",
        "purpose": {"ticket": "CHG-48213", "legal_basis": "legitimate interest — UAT provisioning"},
        "source": {"engine": "oracle", "object": "CRM.CUSTOMERS"}, "target": {"object": "CRM_SYNTH.CUSTOMERS", "write_mode": "table"},
        "policy": {"name": "synthetic-realistic", "hash": "7c15", "version": "1"},
        "columns": [{"path": "$.FULL_NAME", "entity_type": "PERSON_NAME", "method": "surrogate", "values": 500, "data_type": "VARCHAR2(120)", "confidence": 0.96, "override": False}],
        "counts": {"rows_read": 500, "rows_written": 500, "partitions": 2},
        "verification": {"mode": "strict", "hits": 0, "passed": True, "values_checked": 500},
        "partitions": [{"no": 0, "rows": 250, "attempts": 1, "resumed": 0, "state": "done"}],
        "timings": {"duration_s": 12.5}, "config_hash": "abc", "consistency": {"kid": None, "scope": "key"}}
FIDELITY = {"engine": "copula", "score": 0.96, "corr_delta": 0.019, "limits": {"column": 0.05, "corr": 0.1},
            "measured_on": {"synthetic_rows": 300},
            "columns": [{"path": "$.BALANCE", "metric": "ks", "distance": 0.021, "null_rate_delta": 0.0}],
            "corr_matrix": {"columns": ["$.BALANCE", "$.LIMIT"], "delta": [[0.0, 0.024], [0.024, 0.0]]}}
PRIVACY = {"exact_row_matches": 0, "exact_row_resamples": 1, "nndr": {"ratio": 1.04, "synth_to_real_median": 0.412,
           "real_to_real_median": 0.398, "target": 0.95, "passed": True},
           "qi_uniqueness": {"source": 0.029, "synthetic": 0.008, "unique_overlap": 0},
           "membership_inference": {"run": False, "reason": "not available for the copula engine"}}


def _text(pdf: bytes) -> str:
    return "\n".join(page.extract_text() for page in pypdf.PdfReader(io.BytesIO(pdf)).pages)


def test_synthetic_pack_renders_every_section() -> None:
    pdf = render_evidence_pdf({**BASE, "fidelity": FIDELITY, "privacy": PRIVACY}, pack_sha256=SHA)
    assert pdf.startswith(b"%PDF-")
    text = _text(pdf)
    for heading in ("Summary", "Configuration", "Classification snapshot", "Execution", "Verification",
                    "fidelity and privacy", "Regulatory note", "Signatures"):
        assert heading in text
    assert "1.04" in text and "Exact-row matches" in text and SHA in text and "CHG-48213" in text


def test_obfuscate_pack_has_no_synthetic_section() -> None:
    text = _text(render_evidence_pdf({**BASE, "label": "pseudonymised — still personal data"}, pack_sha256=SHA))
    assert "fidelity and privacy" not in text and "pseudonymised" in text


def test_markup_is_escaped_and_unicode_folded() -> None:
    evil = {**BASE, "source": {"engine": "postgres", "object": '<link href="https://evil.example">x</link> café ≥ 1'}}
    pdf = render_evidence_pdf(evil, pack_sha256=SHA)
    text = _text(pdf)
    assert b"/URI" not in pdf and b"/Annots" not in pdf          # rendered as text, never as a live link
    assert "evil.example" in text and ">=" in text and pdf_text("<b>") == "&lt;b&gt;"


def test_rollup_pdf_renders() -> None:
    doc = {"day": "2026-10-10", "action": "obfuscate", "calls": 12, "findings": 40, "values": 33,
           "verify_passed": 12, "verify_failed": 0, "by_outcome": {"ok": 12}, "entities": {"SSN": 10}, "sha256": SHA}
    text = _text(render_rollup_pdf(doc))
    assert "obfuscate" in text and "2026-10-10" in text and SHA in text
```
Route tests (`@pytest.mark.db`, same file):
- `test_pdf_route_renders_once_and_then_streams_the_artefact`: insert a job + evidence row for tenant `pdf1`; two `GET /api/v1/evidence/<id>/pdf` → both 200 `application/pdf` with identical bytes; exactly one `dps_artefact` row of kind `evidence_pdf`; `dps_evidence.pdf_artefact_id` set.
- `test_pdf_route_scope_tenancy_and_switch`: a detect-only key → 403; another tenant's id → 404; with `report_pdf_enabled=False` → 404 `pdf_disabled`.

- [ ] **Step 2: Run to confirm failure** → `ModuleNotFoundError: No module named 'dps.layers.reports'`.
- [ ] **Step 3: Implement** the renderer and the two routes; HANDOFF §3 row for `DPS_REPORT_PDF_ENABLED`.
- [ ] **Step 4: Run tests**: `pytest -q tests/test_evidence_pdf.py` → `4 passed` (+ `2 passed` with `-m db`); unit gate green.
- [ ] **Step 5: Commit**

```bash
git add dps/layers/reports/__init__.py dps/layers/reports/pdf.py dps/layers/api/routers/reports.py HANDOFF.md tests/test_evidence_pdf.py
git commit -m "feat(reports): printable evidence-pack and API roll-up PDFs with escaped, Latin-1-safe text" -- dps/layers/reports/__init__.py dps/layers/reports/pdf.py dps/layers/api/routers/reports.py HANDOFF.md tests/test_evidence_pdf.py
```

---

### Task 15: Policy store — tenant policies, drafts, immutable versions, `@name` references

**Files:**
- Create: `dps/migrations/043_policies.sql`, `dps/policy_store.py`, `dps/layers/api/policy_refs.py`, `dps/layers/api/routers/policy_store.py`
- Modify: `dps/db.py` (`TENANT_TABLES`), `dps/app.py`; routers that accept a policy — M0 `detect`, `transform`, `policies`; M1 `documents`, `schema`; M2 `jobs` (discover, run); M4 `scans`; contract §2.2, §9 and a new §5.5 "Policy references"
- Test: `tests/test_policy_diff.py` (unit), `tests/test_policy_store.py` (`db`)

`dps/migrations/043_policies.sql`:
```sql
-- 043 — tenant policies with immutable published versions (M5, mockup 05).
CREATE TABLE __SCHEMA__.dps_policy (
  tenant_id text NOT NULL,
  name text NOT NULL CHECK (name ~ '^[a-z0-9][a-z0-9-]{1,62}$'
                            AND name NOT IN ('presets', 'validate', 'plan', 'test-recognizer')),
  base_preset text,
  active_version int,
  draft jsonb, draft_by text, draft_updated_at timestamptz,
  created_by text, created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, name));
CREATE TABLE __SCHEMA__.dps_policy_version (
  tenant_id text NOT NULL, name text NOT NULL,
  version int NOT NULL CHECK (version >= 1),
  body jsonb NOT NULL,                                     -- the resolved Policy (configuration, never customer data)
  policy_hash text NOT NULL CHECK (policy_hash ~ '^[0-9a-f]{64}$'),
  change_ticket text CHECK (change_ticket IS NULL OR length(change_ticket) BETWEEN 3 AND 64),
  note text CHECK (note IS NULL OR length(note) <= 1000),
  published_by text, published_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, name, version),
  FOREIGN KEY (tenant_id, name) REFERENCES __SCHEMA__.dps_policy (tenant_id, name) ON DELETE RESTRICT);
ALTER TABLE __SCHEMA__.dps_policy ADD CONSTRAINT dps_policy_active_fk
  FOREIGN KEY (tenant_id, name, active_version) REFERENCES __SCHEMA__.dps_policy_version (tenant_id, name, version)
  DEFERRABLE INITIALLY DEFERRED;
CREATE FUNCTION __SCHEMA__.dps_policy_version_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'dps_policy_version is immutable';
END $$;
CREATE TRIGGER dps_policy_version_immutable BEFORE UPDATE OR DELETE ON __SCHEMA__.dps_policy_version
  FOR EACH ROW EXECUTE FUNCTION __SCHEMA__.dps_policy_version_immutable();
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['dps_policy', 'dps_policy_version'] LOOP
    EXECUTE format('ALTER TABLE __SCHEMA__.%I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE __SCHEMA__.%I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format('CREATE POLICY tenant_isolation ON __SCHEMA__.%I USING (tenant_id = current_setting(''app.tenant_id'', true)) '
                   'WITH CHECK (tenant_id = current_setting(''app.tenant_id'', true))', t);
  END LOOP;
END $$;
GRANT SELECT, INSERT, UPDATE ON __SCHEMA__.dps_policy TO dps_app_rw;
GRANT SELECT, INSERT ON __SCHEMA__.dps_policy_version TO dps_app_rw;
```
Policies and versions are never deleted (governance record); retention does not touch them.

**References (contract §5.5, new):** wherever a request or spec takes `policy`, a string `@<name>` means "the tenant's active published version of `<name>`" and `@<name>:v<N>` a pinned version. They resolve **at accept time** to the stored body (an inline policy dict with `name` and `version`), so jobs record exactly what they ran. Plain preset names keep meaning the shipped presets. Errors: malformed → 400 `invalid_policy_ref`; unknown → 404 `policy_not_found`; never published → 409 `policy_not_published`; no database → 400 `policy_store_unavailable`.

Interfaces:
```python
# dps/policy_store.py
REF = re.compile(r"^@([a-z0-9][a-z0-9-]{1,62})(?::v([0-9]{1,6}))?$")
class PolicyStoreError(Exception): status: int; code: str; detail: str
class PolicyChange(BaseModel): op: Literal["add", "remove", "change"]; path: str; before: Any = None; after: Any = None
class PresetView(BaseModel): name: str; version: int; hash: str; description: str
                             mode: Literal["obfuscate", "synthesize"]; reversible: bool; jobs: int
class PolicyVersionView(BaseModel): version: int; hash: str; change_ticket: str | None; note: str | None
                                    published_by: str | None; published_at: datetime; jobs_pinned: int
class TenantPolicyView(BaseModel): name: str; base_preset: str | None; active_version: int | None; active_hash: str | None
                                   draft: bool; draft_changes: int; published_at: datetime | None; published_by: str | None; jobs: int
class TenantPolicyDetail(TenantPolicyView): active_body: dict[str, Any] | None; draft_body: dict[str, Any] | None
                                            draft_by: str | None; draft_updated_at: datetime | None; versions: list[PolicyVersionView]
def flatten_policy(body: Mapping[str, Any]) -> dict[str, Any]
def diff_policies(before: Mapping[str, Any], after: Mapping[str, Any]) -> list[PolicyChange]
def policy_yaml(body: Mapping[str, Any]) -> str               # yaml.safe_dump(sort_keys=True), "if_" written back as "if"
async def list_policies(tenant_id: str) -> tuple[list[PresetView], list[TenantPolicyView]]
async def create_policy(tenant_id: str, name: str, base_preset: str, *, actor: str) -> TenantPolicyDetail   # publishes v1 = preset
async def get_policy(tenant_id: str, name: str) -> TenantPolicyDetail
async def save_draft(tenant_id: str, name: str, body: Mapping[str, Any], *, actor: str) -> TenantPolicyDetail
async def discard_draft(tenant_id: str, name: str) -> TenantPolicyDetail
async def publish(tenant_id: str, name: str, *, change_ticket: str | None, note: str | None, actor: str) -> PolicyVersionView
async def get_version(tenant_id: str, name: str, version: int) -> dict[str, Any]
async def resolve_ref(ref: str | dict[str, Any] | None, tenant_id: str) -> str | dict[str, Any] | None
# dps/layers/api/policy_refs.py
async def resolved_policy(request: Request, ref: str | dict[str, Any] | None) -> str | dict[str, Any] | None   # DpsHttpError on failure
def policy_label(ref: str | dict[str, Any] | None, default: str) -> str     # "name@vN" | preset | "inline" — for audit
```
`policy_label` also fixes the M0 audit line that stringified inline dicts: routers pass `policy_name=policy_label(body.policy, default)`.

Diff (full code; deterministic, keys stable for the console):
```python
def flatten_policy(body: Mapping[str, Any], prefix: str = "") -> dict[str, Any]:
    """Dotted leaves; list items keyed by their ``name`` or ``path`` when they have one."""
    out: dict[str, Any] = {}
    for key in sorted(body):
        value, path = body[key], f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, Mapping):
            out.update(flatten_policy(value, path))
        elif isinstance(value, list) and value and all(isinstance(v, Mapping) for v in value):
            for index, item in enumerate(value):
                label = item.get("name") or item.get("path") or index
                out.update(flatten_policy(item, f"{path}[{label}]"))
        else:
            out[path] = value
    return out


def diff_policies(before: Mapping[str, Any], after: Mapping[str, Any]) -> list[PolicyChange]:
    a, b = flatten_policy(before), flatten_policy(after)
    changes: list[PolicyChange] = []
    for path in sorted(set(a) | set(b)):
        if path in ("version", "name"):
            continue
        if path not in a:
            changes.append(PolicyChange(op="add", path=path, after=b[path]))
        elif path not in b:
            changes.append(PolicyChange(op="remove", path=path, before=a[path]))
        elif a[path] != b[path]:
            changes.append(PolicyChange(op="change", path=path, before=a[path], after=b[path]))
    return changes
```
Publish (one transaction under `acquire(tenant)`):
```sql
SELECT draft, active_version FROM dps_policy WHERE tenant_id = $1 AND name = $2 FOR UPDATE;   -- 404 / 409 no_draft
SELECT coalesce(max(version), 0) + 1 FROM dps_policy_version WHERE tenant_id = $1 AND name = $2;
INSERT INTO dps_policy_version (tenant_id, name, version, body, policy_hash, change_ticket, note, published_by)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8);
UPDATE dps_policy SET active_version = $3, draft = NULL, draft_by = NULL, draft_updated_at = NULL, updated_at = now()
 WHERE tenant_id = $1 AND name = $2;
```
The body is `Policy.model_validate({**draft, "name": name, "version": next}).model_dump(mode="json")`; `policy_hash = Policy(...).config_hash()`. `save_draft` validates with `resolve_policy(dict(body), default_name=...)` (an invalid draft → 400 `invalid_policy` with pydantic's message, which names fields and methods, never data). Job counts: `SELECT spec->'policy'->>'name', spec->'policy'->>'version', count(*) FROM dps_job WHERE tenant_id = $1 AND kind = 'run' AND jsonb_typeof(spec->'policy') = 'object' GROUP BY 1, 2` plus `spec->>'policy'` for preset strings.

Routes (`dps/layers/api/routers/policy_store.py`, registered **after** M0's policies router so `/policies/presets` keeps its meaning; contract §9 additions):

| Method · path | Scope | Body · query | Response |
|---|---|---|---|
| `GET /api/v1/policies` | detect | — | `{"presets": [PresetView], "tenant": [TenantPolicyView]}` |
| `POST /api/v1/policies` | admin | `{"name", "base_preset"}` | 201 `TenantPolicyDetail` (v1 = the preset, note "shipped preset") |
| `GET /api/v1/policies/{name}` | detect | — | `TenantPolicyDetail` |
| `PUT /api/v1/policies/{name}/draft` | admin | `{"body": {...policy}}` | `TenantPolicyDetail` |
| `DELETE /api/v1/policies/{name}/draft` | admin | — | `TenantPolicyDetail` |
| `POST /api/v1/policies/{name}/publish` | admin | `{"change_ticket"?, "note"?}` | 201 `PolicyVersionView` |
| `GET /api/v1/policies/{name}/versions/{version}` | detect | `format=json\|yaml` | body JSON or `text/yaml` attachment |
| `GET /api/v1/policies/{name}/diff` | detect | `from` (int), `to` (int or `draft`) | `{"from", "to", "changes": [PolicyChange]}` |

Every mutating route audits `admin.policy.<verb>` with `{"version": n, "changes": k}`.

Router changes (resolution at accept): M0 `detect`/`obfuscate`/`synthesize`/`plan`/`validate`, M1 `documents/*`/`schema/*`, M2 `jobs/discover`/`jobs/run` (`spec.policy` replaced before `validate_run`), M4 `scans` call `policy = await resolved_policy(request, body.policy)` and pass `policy` on.

- [ ] **Step 1: Write the failing tests**

`tests/test_policy_diff.py` (offline):
```python
from __future__ import annotations

from dps.engine.policy import load_preset
from dps.policy_store import REF, diff_policies, flatten_policy, policy_yaml


def test_flatten_keys_lists_by_name_or_path() -> None:
    flat = flatten_policy({"detect": {"min_score": 0.6, "custom_recognizers": [{"name": "emp", "patterns": [
        {"name": "p", "regex": "x", "score": 0.9}]}], "declared_fields": [{"path": "$.a", "entity_type": "X"}]}})
    assert flat["detect.min_score"] == 0.6
    assert flat["detect.custom_recognizers[emp].patterns[p].score"] == 0.9
    assert flat["detect.declared_fields[$.a].entity_type"] == "X"


def test_diff_reports_add_remove_change_and_ignores_name_version() -> None:
    v1 = load_preset("gdpr-pseudonymise").model_dump(mode="json")
    v2 = {**v1, "name": "other", "version": 2, "detect": {**v1["detect"], "min_score": 0.55},
          "treat": {k: v for k, v in v1["treat"].items() if k != "CVV"}
          | {"BUSINESS_SENSITIVE": {"method": "bucket", "params": {"ranges": [0, 300, 600, 900]}}}}
    changes = {(c.op, c.path) for c in diff_policies(v1, v2)}
    assert ("change", "detect.min_score") in changes
    assert ("remove", "treat.CVV.method") in changes and ("add", "treat.BUSINESS_SENSITIVE.method") in changes
    assert ("add", "treat.BUSINESS_SENSITIVE.params.ranges") in changes
    assert not any(path in ("name", "version") for _, path in changes)


def test_reference_grammar_and_yaml() -> None:
    assert REF.match("@retail-kyc").groups() == ("retail-kyc", None)
    assert REF.match("@retail-kyc:v12").groups() == ("retail-kyc", "12")
    assert REF.match("@Retail") is None and REF.match("retail-kyc") is None and REF.match("@x:v") is None
    text = policy_yaml(load_preset("pci-dss").model_dump(mode="json"))
    assert text.startswith("consistency:") and "CARD_PAN:" in text
```

`tests/test_policy_store.py` (`@pytest.mark.db`) — names and assertions:
- `test_adopting_a_preset_publishes_v1_with_the_preset_hash`: `create_policy("t1", "gdpr-pseudonymise", "gdpr-pseudonymise")` → `active_version == 1`, `active_hash == load_preset("gdpr-pseudonymise").config_hash()`, `versions[0].note == "shipped preset"`.
- `test_draft_publish_and_versions_are_immutable`: `save_draft` with `min_score` 0.55 → `draft is True, draft_changes == 1`; `publish(change_ticket="CHG-47950")` → version 2, draft cleared; `UPDATE dps_policy_version SET note = 'x'` → raises `asyncpg.RaiseError` ("immutable"); `DELETE` likewise.
- `test_invalid_draft_is_refused_with_field_names_only`: draft with `treat.SSN.method = "teleport"` → `PolicyStoreError` 400 `invalid_policy` whose detail contains `teleport` (a method name) and no row is written.
- `test_reserved_and_bad_names`: `create_policy(..., "presets", ...)` → 422 `invalid_name`; `"Bad"` → 422.
- `test_at_reference_resolves_active_and_pinned_versions_over_http`: publish v2; `POST /api/v1/obfuscate` with `policy="@gdpr-pseudonymise"` → `report.policy_hash == v2 hash`; `"@gdpr-pseudonymise:v1"` → v1 hash; `"@nope"` → 404 `policy_not_found`; `"@gdpr-pseudonymise:v9"` → 404; an unpublished policy row → 409 `policy_not_published`; audit row `policy == "gdpr-pseudonymise@v2"`.
- `test_run_spec_records_the_resolved_body`: `POST /jobs/run` with `policy="@gdpr-pseudonymise"` (valid spec) → stored `dps_job.spec->'policy'->>'version' == '2'`.
- `test_diff_and_yaml_routes`: `GET /policies/gdpr-pseudonymise/diff?from=1&to=2` → one `change` at `detect.min_score`; `to=draft` with no draft → 409 `no_draft`; `versions/2?format=yaml` → `text/yaml` attachment containing `min_score: 0.55`.
- `test_policies_are_tenant_isolated`: tenant `t2` → `GET /policies/gdpr-pseudonymise` 404; `GET /policies` lists only presets for `t2`.

- [ ] **Step 2: Run to confirm failure** → `ModuleNotFoundError: No module named 'dps.policy_store'`.
- [ ] **Step 3: Implement** migration, store, refs helper, router, router call sites; `TENANT_TABLES` += `dps_policy`, `dps_policy_version`.
- [ ] **Step 4: Run tests**: `pytest -q tests/test_policy_diff.py` → `3 passed`; `pytest -q -m db tests/test_policy_store.py` → `8 passed`; unit gate green.
- [ ] **Step 5: Commit**

```bash
git add dps/migrations/043_policies.sql dps/policy_store.py dps/layers/api/policy_refs.py dps/layers/api/routers/policy_store.py dps/db.py dps/app.py dps/layers/api/routers/detect.py dps/layers/api/routers/transform.py dps/layers/api/routers/policies.py dps/layers/api/routers/documents.py dps/layers/api/routers/schema.py dps/layers/api/routers/jobs.py dps/layers/api/routers/scans.py tests/test_policy_diff.py tests/test_policy_store.py docs/superpowers/plans/2026-10-11-m1-m5-contract.md
git commit -m "feat(policies): tenant policies with drafts, immutable versions, diffs and @name references resolved at accept" -- dps/migrations/043_policies.sql dps/policy_store.py dps/layers/api/policy_refs.py dps/layers/api/routers/policy_store.py dps/db.py dps/app.py dps/layers/api/routers/detect.py dps/layers/api/routers/transform.py dps/layers/api/routers/policies.py dps/layers/api/routers/documents.py dps/layers/api/routers/schema.py dps/layers/api/routers/jobs.py dps/layers/api/routers/scans.py tests/test_policy_diff.py tests/test_policy_store.py docs/superpowers/plans/2026-10-11-m1-m5-contract.md
```

---
### Task 16: Posture tightening — secrets from files, TLS-only egress, KEK rotation, runtime posture report

**Files:**
- Create: `dps/rekey.py`
- Modify: `dps/posture.py` (M0), `dps/netguard.py`, `dps/app.py` (CORS, `/docs`, posture route), `dps/layers/api/routers/admin.py` (`GET /admin/posture`), M2 `dps/jobs_models.py` (`ConnectionParams.options` allow-list += `protocol`), M2 `dps/layers/api/routers/connections.py` and `dps/layers/connectors/registry.py` (TLS rule), `HANDOFF.md`, contract §5.1, §10
- Test: `tests/test_posture_m5.py`, `tests/test_egress_tls.py`, `tests/test_rekey.py` (`db`)

**Static checks** added to `posture_problems(settings)` (staging/prod only; each message names the variable, never its value):
```python
#: Secrets that must be mounted files in staging/prod: a literal env var lands in `env` dumps, crash
#: reports and `oc describe`. Checked against os.environ, because Settings cannot tell the source.
SECRET_ENV = ("DPS_JOB_KEK", "DPS_JOB_KEK_PREVIOUS", "DPS_API_KEYS", "DPS_COIN_CLIENT_ID", "DPS_COIN_CLIENT_SECRET",
              "DPS_PG_PASSWORD", "DPS_PG_WORKER_PASSWORD", "DPS_PG_MIGRATION_PASSWORD", "DPS_TOKENIZER_TOKEN",
              "DPS_AZURE_VISION_KEY", "DPS_S3_ACCESS_KEY_ID", "DPS_S3_SECRET_ACCESS_KEY")
_EGRESS_URLS = ("stellar_base_url", "coin_url", "azure_vision_endpoint", "s3_endpoint_url", "tokenizer_url")


def _m5_problems(settings: Settings, environ: Mapping[str, str]) -> list[str]:
    problems = [f"{name} must be provided as {name}_FILE (a mounted secret), not a literal environment variable"
                for name in SECRET_ENV if environ.get(name)]
    if settings.job_kek:
        try:
            if len(base64.b64decode(settings.job_kek, validate=True)) != 32:
                problems.append("DPS_JOB_KEK must decode to exactly 32 bytes")
        except ValueError:
            problems.append("DPS_JOB_KEK must be base64")
    else:
        problems.append("DPS_JOB_KEK (DPS_JOB_KEK_FILE) must be set")
    if not settings.egress_tls_required:
        problems.append("DPS_EGRESS_TLS_REQUIRED must be true")
    for field in _EGRESS_URLS:
        value = getattr(settings, field, "")
        if value and not value.startswith("https://"):
            problems.append(f"DPS_{field.upper()} must be an https URL")
    if any(getattr(settings, f, "") for f in _EGRESS_URLS) and not settings.ssl_cert_file:
        problems.append("DPS_SSL_CERT_FILE (the organisation CA bundle) must be set for TLS egress")
    if settings.tokenizer_provider == "http" and not settings.tokenizer_token:
        problems.append("DPS_TOKENIZER_TOKEN_FILE must be set when the tokenizer is enabled")
    if "*" in settings.cors_origin_list:
        problems.append("DPS_CORS_ORIGINS must list origins explicitly (or be empty for same-origin only)")
    if settings.embedded_worker:
        problems.append("DPS_EMBEDDED_WORKER must be false (run dedicated worker deployments)")
    if not (settings.audit_enabled and settings.audit_strict):
        problems.append("DPS_AUDIT_ENABLED and DPS_AUDIT_STRICT must both be true")
    return problems
```
(`dps/posture.py` imports grow to `base64`, `os`, `collections.abc.Mapping`.) `posture_problems` appends `_m5_problems(settings, os.environ)` (keep M0–M4 checks; duplicates of M2 checks are de-duplicated by `dict.fromkeys`).

**Runtime report** for the Admin page — `dps/posture.py`:
```python
class PostureCheck(BaseModel): id: str; name: str; ok: bool; detail: str; enforced_at_boot: bool
class PostureReport(BaseModel): env: str; enforced: bool; passed: int; total: int; checked_at: datetime
                                checks: list[PostureCheck]
async def posture_report(settings: Settings, *, environ: Mapping[str, str] | None = None) -> PostureReport
```
Checks (ids fixed; evaluated "as if production" in every env; `enforced = settings.is_production`): `auth_enabled`, `bootstrap_key_absent`, `api_keys_from_file` (DB keys or `DPS_API_KEYS_FILE`), `tls_egress` (setting + https URLs + CA bundle), `audit_strict`, `leakage_strict` (every preset and every tenant's active policy has `verify.leakage_check == "strict"`; detail names offending policies), `llm_not_stub`, `kek_from_secret` (`DPS_JOB_KEK_FILE` set, 32 bytes), `keys_rotated` (DB: active keys with `created_at < now() - key_rotation_max_age_days` and no `rotated_at`; detail lists their **names** and ages in days — the mockup's "ledger-recon-batch is 113 days old"), `rls_posture` (`assert_rls_posture()` returns no problems), `migrations_verified` (`verify_migrations()` raises nothing). DB-dependent checks report `ok=False, detail="database unavailable"` when the DB is down. `GET /api/v1/admin/posture` (admin) returns the report.

**App changes:** `CORSMiddleware(allow_origins=settings.cors_origin_list, ...)` (empty list → no CORS headers at all); `FastAPI(docs_url=None, redoc_url=None, openapi_url=None)` when `settings.is_production`.

**TLS-only connections** (`dps/netguard.py`; imports `ConnectionSpec` and `SpecError` from `dps.jobs_models` inside the function, so `netguard` stays importable by M2 modules without a cycle):
```python
def require_tls_connection(spec: ConnectionSpec, settings: Settings, *, secret: Mapping[str, str] | None = None) -> None:
    """With DPS_EGRESS_TLS_REQUIRED, refuse connections that would travel in clear (SpecError tls_required, 422):
    postgres sslmode ∈ {verify-full, verify-ca}; mongo tls=true; oracle protocol=tcps or a secret dsn that uses
    tcps; s3 endpoint_url https (or unset = AWS default https); pvc exempt."""
    if not settings.egress_tls_required:
        return
    opts = spec.params.options
    ok = {
        "postgres": opts.get("sslmode") in ("verify-full", "verify-ca"),
        "mongo": opts.get("tls", "").lower() == "true",
        "oracle": opts.get("protocol", "").lower() == "tcps"
                  or "tcps" in (secret or {}).get("dsn", "").lower(),
        "s3": not spec.params.endpoint_url or spec.params.endpoint_url.startswith("https://"),
        "pvc": True,
    }[spec.engine]
    if not ok:
        raise SpecError("tls_required", 422, f"{spec.engine} connections must use TLS on this deployment")
```
Called by M2's `POST /connections`, `PATCH /connections/{id}` and `open_connector` (so a pre-existing clear connection is refused at use). The Oracle connector (M3) maps `options.protocol=tcps` to `oracledb.ConnectParams(protocol="tcps")`.

**KEK rotation** — `python -m dps.rekey [--dry-run]` (`dps/rekey.py`): for every sealed blob it opens with `DPS_JOB_KEK`, else with `DPS_JOB_KEK_PREVIOUS` and re-seals with the current KEK; blobs: `dps_job.key_enc` (AAD `dps_job.key:{id}`), `dps_job.spec_enc` (`dps_job.spec:{id}`) through the worker pool (the `worker_claim` policy sees every tenant), and `dps_connection.secret_enc` (AAD `dps_connection.secret:{id}` — the M2 call-site template; `tests/test_rekey.py` proves it by opening a secret M2 sealed) per tenant from `tenant_directory()` under `acquire(tenant)`. Each update is optimistic: `UPDATE ... SET <col> = $new WHERE id = $1 AND <col> = $old`. Prints one JSON line `{"rows": n, "resealed": n, "already_current": n, "unreadable": n}`; `unreadable > 0` exits 3 (no row is changed for those). Runbook (HANDOFF §9): mount the new KEK as `DPS_JOB_KEK_FILE` and the old as `DPS_JOB_KEK_PREVIOUS_FILE`, roll the deployments, run `dps.rekey`, confirm `unreadable == 0`, remove the previous KEK, roll again.

- [ ] **Step 1: Write the failing tests**

`tests/test_posture_m5.py`:
```python
from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from dps.config import Settings
from dps.posture import PostureError, assert_production_posture, posture_problems

KEK = base64.b64encode(b"k" * 32).decode()


def _files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in {"DPS_JOB_KEK": KEK, "DPS_API_KEYS": '[{"name":"k","key_sha256":"' + "a" * 64 + '"}]',
                        "DPS_TOKENIZER_TOKEN": "tok-sentinel-998877"}.items():
        path = tmp_path / name
        path.write_text(value, encoding="utf-8")
        monkeypatch.setenv(f"{name}_FILE", str(path))


def _prod(**kw: object) -> Settings:
    base: dict[str, object] = dict(
        _env_file=None, env="prod", auth_enabled=True, llm_provider="", audit_enabled=True, audit_strict=True,
        egress_tls_required=True, cors_origins="https://console.bank.example", ssl_cert_file="/etc/ssl/org-ca.pem",
        tokenizer_provider="http", tokenizer_url="https://tokenizer.bank.example", migrations_mode="verify",
        inline_secrets_allowed=False, rls_enabled=True, pg_host="db.bank.example", pg_ssl_root_cert="/etc/ssl/pg.pem")
    base.update(kw)
    return Settings(**base)  # type: ignore[arg-type]


def test_good_m5_production_passes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _files(tmp_path, monkeypatch)
    assert posture_problems(_prod()) == []
    assert_production_posture(_prod())


@pytest.mark.parametrize("change,needle", [
    ({"egress_tls_required": False}, "DPS_EGRESS_TLS_REQUIRED"),
    ({"tokenizer_url": "http://tokenizer.bank.example"}, "DPS_TOKENIZER_URL"),
    ({"cors_origins": "*"}, "DPS_CORS_ORIGINS"),
    ({"embedded_worker": True}, "DPS_EMBEDDED_WORKER"),
    ({"audit_strict": False}, "DPS_AUDIT_STRICT"),
    ({"s3_endpoint_url": "http://minio:9000"}, "DPS_S3_ENDPOINT_URL"),
    ({"ssl_cert_file": ""}, "DPS_SSL_CERT_FILE"),
    ({"job_kek": base64.b64encode(b"short").decode()}, "32 bytes"),
])
def test_bad_m5_production_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: dict, needle: str) -> None:
    _files(tmp_path, monkeypatch)
    with pytest.raises(PostureError) as err:
        assert_production_posture(_prod(**change))
    assert needle in str(err.value) and "tok-sentinel-998877" not in str(err.value)


def test_literal_secret_env_vars_are_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _files(tmp_path, monkeypatch)
    monkeypatch.setenv("DPS_TOKENIZER_TOKEN", "tok-sentinel-998877")
    problems = posture_problems(_prod())
    assert any("DPS_TOKENIZER_TOKEN must be provided as DPS_TOKENIZER_TOKEN_FILE" in p for p in problems)
    assert "tok-sentinel-998877" not in json.dumps(problems)


async def test_posture_report_is_value_free_and_not_enforced_locally() -> None:
    from dps.posture import posture_report

    report = await posture_report(Settings(_env_file=None, env="local", bootstrap_api_key="dps_sentinel_local_key"))
    ids = {c.id for c in report.checks}
    assert ids == {"auth_enabled", "bootstrap_key_absent", "api_keys_from_file", "tls_egress", "audit_strict",
                   "leakage_strict", "llm_not_stub", "kek_from_secret", "keys_rotated", "rls_posture",
                   "migrations_verified"}
    assert report.enforced is False and report.total == 11 and "dps_sentinel_local_key" not in report.model_dump_json()
    assert next(c for c in report.checks if c.id == "bootstrap_key_absent").ok is False


def test_docs_and_cors_follow_the_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi.testclient import TestClient

    from dps.app import create_app

    local = TestClient(create_app(Settings(_env_file=None, env="local", auth_enabled=False, llm_provider="stub")))
    assert local.get("/docs").status_code == 200
    _files(tmp_path, monkeypatch)
    prod = TestClient(create_app(_prod(tokenizer_provider="", tokenizer_url="", pg_host="")))
    assert prod.get("/docs").status_code in (404, 200) and prod.get("/api/v1/openapi.json").status_code == 404
    pre = prod.options("/api/v1/detect", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"})
    assert pre.headers.get("access-control-allow-origin") is None
```
(`prod.get("/docs")` may be served by the SPA fallback when a `frontend/dist` exists — the assertion that matters is the OpenAPI document being gone.)

`tests/test_egress_tls.py` — parametrised `require_tls_connection` cases (`egress_tls_required=True`): postgres without `sslmode` → `SpecError` code `tls_required`; postgres `verify-full` → ok; mongo `tls=true` → ok, absent → refused; oracle `protocol=tcps` → ok, `tcp` → refused, secret `dsn="tcps://h:2484/s"` → ok; s3 `http://minio:9000` → refused, `https://s3.example` → ok, unset → ok; pvc → ok; and with `egress_tls_required=False` every case passes (12 cases total).

`tests/test_rekey.py` (`@pytest.mark.db`):
- `test_rekey_moves_previous_kek_blobs_to_the_current_kek`: seal a job `key_enc` and a connection `secret_enc` (through M2's own create path for the connection) with KEK A; run `rekey` with `job_kek=B, job_kek_previous=A` → `{"resealed": 2}`; both blobs open with B and no longer with A; a second run → `{"already_current": 2, "resealed": 0}`.
- `test_rekey_dry_run_and_unreadable_rows`: a blob sealed with an unknown KEK C → `unreadable == 1`, exit code 3, the row unchanged; `--dry-run` changes nothing.

- [ ] **Step 2: Run to confirm failure** → `tests/test_posture_m5.py` fails on the first parametrised case (no `DPS_EGRESS_TLS_REQUIRED` problem yet).
- [ ] **Step 3: Implement** the posture additions, `posture_report`, the app changes, `require_tls_connection` and its three M2 call sites, the `protocol` option (contract §5.1 allow-list sentence gains `protocol`), `dps/rekey.py`, `GET /admin/posture`; HANDOFF §3 rows (`DPS_EGRESS_TLS_REQUIRED`, `DPS_CORS_ORIGINS`), §9 KEK-rotation runbook.
- [ ] **Step 4: Run tests**: `pytest -q tests/test_posture_m5.py tests/test_egress_tls.py tests/test_posture.py` → `12 passed` + `12 passed` + M0's tests; if a posture rule an earlier milestone added (beyond the contract's list) fires on `_prod()`, add the setting that satisfies it to `_prod()` — never relax the rule; `pytest -q -m db tests/test_rekey.py` → `2 passed`; unit gate green.
- [ ] **Step 5: Commit**

```bash
git add dps/posture.py dps/netguard.py dps/rekey.py dps/app.py dps/layers/api/routers/admin.py dps/jobs_models.py dps/layers/api/routers/connections.py dps/layers/connectors/registry.py HANDOFF.md tests/test_posture_m5.py tests/test_egress_tls.py tests/test_rekey.py docs/superpowers/plans/2026-10-11-m1-m5-contract.md
git commit -m "feat(posture): secrets from files, TLS-only egress, explicit CORS, docs off in prod, KEK re-sealing, runtime posture report" -- dps/posture.py dps/netguard.py dps/rekey.py dps/app.py dps/layers/api/routers/admin.py dps/jobs_models.py dps/layers/api/routers/connections.py dps/layers/connectors/registry.py HANDOFF.md tests/test_posture_m5.py tests/test_egress_tls.py tests/test_rekey.py docs/superpowers/plans/2026-10-11-m1-m5-contract.md
```

---

### Task 17: Security review pass — fixes and the guard tests

**Files:**
- Create: `dps/layers/api/validation.py`, `dps/engine/detect/regex_guard.py`, `docs/security/2026-10-11-m5-security-review.md`, `tests/security/__init__.py`, `tests/security/test_route_auth.py`, `tests/security/test_validation_errors.py`, `tests/security/test_regex_guard.py`, `tests/security/test_netguard.py`, `tests/security/test_selection_injection.py`, `tests/security/test_cross_tenant.py` (`db`), `tests/security/test_secrets_hygiene.py`, `tests/security/test_headers.py`, `tests/test_no_values_in_logs_m5.py`
- Modify: `dps/auth.py` (`require_scope` marks its dependency), `dps/config.py` (secret fields `repr=False`), `dps/engine/detect/custom.py` (pattern guard), `dps/netguard.py` (SSRF checks), `dps/app.py` (422 handler, `Cache-Control: no-store` on `/api/`), M4 `dps/layers/api/routers/scans.py` + `dps/layers/scanner/crawler.py`, contract §5.3 (`host_forbidden`)

The review covers the five areas the milestone names; each finding has a fix and a test, recorded in `docs/security/2026-10-11-m5-security-review.md`:

| # | Area | Finding (as designed before M5) | Fix | Test |
|---|---|---|---|---|
| S1 | logs/errors | FastAPI's default 422 body echoes the submitted `input` (an SSN in a mistyped field comes back and lands in client logs) | `validation.py` handler: `{"detail": [{"loc", "msg", "type"}], "code": "invalid_request"}` — `input`/`ctx`/`url` dropped | `test_validation_errors.py` |
| S2 | secrets | `repr(Settings)` prints every field, secrets included | secret fields declared with `Field(default="", repr=False)` | `test_secrets_hygiene.py::test_settings_repr_hides_secrets` |
| S3 | injection (policy) | policy-supplied regexes run on every request: catastrophic backtracking (ReDoS) | `regex_guard.check_pattern` in `PatternSpecModel` (and so in `/policies/test-recognizer`, drafts, inline policies) | `test_regex_guard.py` |
| S4 | injection (selections) | M2's `validate_selection`/Mongo allow-list are the only barrier before SQL/BSON | adversarial corpus run against both engines' dialects and the Mongo validator | `test_selection_injection.py` |
| S5 | SSRF (scanner) | registered-host check alone misses numeric host encodings, IPv4-mapped IPv6, metadata IPs, DNS that resolves to link-local, and Chromium resolving hosts the guard never saw | `netguard.check_scan_url` at `POST /scans`; Chromium launched with `--host-resolver-rules` that resolve only registered hosts | `test_netguard.py` |
| S6 | auth | a route added without `require_scope` would be public | `require_scope` tags its dependency; a test walks every `/api/v1` route | `test_route_auth.py` |
| S7 | tenancy | object routes could leak existence across tenants | every object route answers 404 for another tenant's id | `test_cross_tenant.py` |
| S8 | key admin | a tenant admin could mint broader keys | no-escalation `check_grant` (Task 9) | `tests/test_auth_db.py::test_no_privilege_escalation` |
| S9 | exports | audit CSV cells starting `= + - @` execute in spreadsheets | `csv_cell` (Task 11) | `tests/test_admin_api.py::test_audit_csv_is_streamed_and_formula_safe` |
| S10 | PDF | Paragraph mini-markup from pack strings (`<link>`) | `pdf_text` escaping (Task 14) | `tests/test_evidence_pdf.py::test_markup_is_escaped_and_unicode_folded` |
| S11 | caching | API responses carrying key material could be cached by a proxy | `Cache-Control: no-store` on every `/api/` response | `test_headers.py` |
| S12 | exposure | `/docs` and OpenAPI public in prod; CORS `*` | Task 16 | `tests/test_posture_m5.py::test_docs_and_cors_follow_the_environment` |
| S13 | logs | M5 modules must stay value-free | log-scan test over copula, metrics, tokenizer, keys, PDF, netguard, regex guard | `test_no_values_in_logs_m5.py` |

`dps/layers/api/validation.py`:
```python
"""422 responses that never echo request input (security review S1)."""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


async def _scrubbed(_: Request, exc: RequestValidationError) -> JSONResponse:
    detail = [{"loc": list(e.get("loc", ())), "msg": str(e.get("msg", "")), "type": str(e.get("type", ""))}
              for e in exc.errors()]
    return JSONResponse({"detail": detail, "code": "invalid_request"}, status_code=422)


def install(app: FastAPI) -> None:
    app.add_exception_handler(RequestValidationError, _scrubbed)  # type: ignore[arg-type]
```
`loc` holds field names and list indexes; for dict-valued payloads (`data`) pydantic does not descend into `Any`, so customer keys never appear either.

`dps/engine/detect/regex_guard.py`:
```python
"""Static ReDoS guard for policy-supplied regular expressions (security review S3).

Python's ``re`` has no timeout, so a pattern with nested unbounded quantifiers — ``(a+)+``,
``(\\w+\\s?)*``, ``(.*x){12}`` — can pin a CPU on a crafted input. Patterns are parsed with the
stdlib parser and refused when: longer than 512 characters; they use backreferences or
conditional groups; a repeat is bounded above 1000; an unbounded repeat sits inside another
repeat whose upper bound exceeds 3 (``(?:\\d+\\.){3}`` stays legal). Error messages name the rule.
"""
from __future__ import annotations

import re
import re._constants as C  # noqa: N812 — stdlib parser constants (Python ≥ 3.11)
import re._parser as P  # noqa: N812
from typing import Any

MAX_PATTERN = 512
MAX_BOUND = 1000
OUTER_LIMIT = 3
_REPEATS = (C.MAX_REPEAT, C.MIN_REPEAT, C.POSSESSIVE_REPEAT)


class UnsafePattern(ValueError):
    """The pattern could backtrack catastrophically or is otherwise refused."""


def check_pattern(pattern: str) -> None:
    if len(pattern) > MAX_PATTERN:
        raise UnsafePattern(f"pattern is longer than {MAX_PATTERN} characters")
    try:
        tree = P.parse(pattern)
    except re.error as exc:
        raise UnsafePattern(f"pattern does not compile: {exc.msg}") from exc
    _walk(tree, outer_bound=1)


def _children(op: Any, av: Any) -> list[Any]:
    if op in _REPEATS:
        return [av[2]]
    if op is C.SUBPATTERN:
        return [av[3]]
    if op is C.BRANCH:
        return list(av[1])
    if op in (C.ASSERT, C.ASSERT_NOT):
        return [av[1]]
    if op is C.ATOMIC_GROUP:
        return [av]
    return []


def _walk(sub: Any, *, outer_bound: int) -> None:
    for op, av in sub:
        if op in (C.GROUPREF, C.GROUPREF_EXISTS):
            raise UnsafePattern("backreferences and conditional groups are not allowed")
        if op in _REPEATS:
            _lo, hi, body = av
            unbounded = hi == C.MAXREPEAT
            if not unbounded and hi > MAX_BOUND:
                raise UnsafePattern(f"repeat bounds above {MAX_BOUND} are not allowed")
            if unbounded and outer_bound > OUTER_LIMIT:
                raise UnsafePattern("nested quantifiers (an unbounded repeat inside a repeat) are not allowed")
            _walk(body, outer_bound=max(outer_bound, C.MAXREPEAT if unbounded else hi))
            continue
        for child in _children(op, av):
            _walk(child, outer_bound=outer_bound)
```
(`outer_bound` is the largest upper bound of the repeats enclosing the current node; any unbounded repeat beneath a repeat bounded above 3 — or beneath an unbounded one — is refused.) `PatternSpecModel._compiles` calls `check_pattern(value)` after `re.compile` and re-raises `UnsafePattern` as `ValueError` (pydantic reports it as a field error naming the rule).

`dps/netguard.py` (part 2; module imports grow to `ipaddress`, `re`, `socket`, `collections.abc.Callable`, `Sequence`, `urllib.parse.urlsplit`, `dps.config.Settings`):
```python
def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


_METADATA = (ipaddress.ip_network("169.254.0.0/16"), ipaddress.ip_network("fd00:ec2::254/128"),
             ipaddress.ip_network("100.100.100.200/32"), ipaddress.ip_network("192.0.0.192/32"))
_NUMERIC_HOST = re.compile(r"^(0x[0-9a-f]+|[0-9]+)(\.(0x[0-9a-f]+|[0-9]+)){0,3}$")
Resolver = Callable[[str], list[str]]


class NetGuardError(ValueError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code, self.detail = code, detail


def _default_resolver(host: str) -> list[str]:
    return sorted({info[4][0] for info in socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)})


def _forbidden_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address, *, allow_loopback: bool) -> str | None:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if any(ip in net for net in _METADATA) or ip.is_link_local:
        return "link-local or cloud metadata address"
    if ip.is_multicast or ip.is_unspecified or ip.is_reserved:
        return "non-routable address"
    if ip.is_loopback and not allow_loopback:
        return "loopback address"
    return None


def check_scan_url(url: str, *, registered_hosts: Sequence[str], settings: Settings,
                   resolver: Resolver = _default_resolver) -> str:
    """Validate a scan target before any browser starts. Returns the normalised host.
    Private (RFC 1918) addresses stay allowed — internal UAT sites are the use case — but
    link-local/metadata, multicast and (outside local) loopback are refused, also after DNS."""
    parts = urlsplit(url)
    allowed_schemes = ("https",) if settings.egress_tls_required else ("http", "https")
    if parts.scheme not in allowed_schemes:
        raise NetGuardError("host_forbidden", f"scan URLs must use {' or '.join(allowed_schemes)}")
    if parts.username is not None or parts.password is not None:
        raise NetGuardError("host_forbidden", "scan URLs must not carry credentials")
    try:
        host = normalize_host(parts.netloc)
    except ValueError as exc:
        raise NetGuardError("host_forbidden", "scan URL has no valid host") from exc
    if _NUMERIC_HOST.match(host) and not _is_ip(host):
        raise NetGuardError("host_forbidden", "numeric host encodings are not allowed")
    if host not in registered_hosts:
        raise NetGuardError("host_not_registered", "the host is not registered for this tenant")
    allow_loopback = settings.env == "local"
    try:
        addresses = [host] if _is_ip(host) else resolver(host)
    except OSError as exc:
        raise NetGuardError("host_forbidden", "the host does not resolve") from exc
    for address in addresses:
        reason = _forbidden_ip(ipaddress.ip_address(address.split("%")[0]), allow_loopback=allow_loopback)
        if reason:
            raise NetGuardError("host_forbidden", f"the host resolves to a {reason}")
    return host


def chromium_resolver_rules(hosts: Sequence[str]) -> str:
    """Chromium flag value: resolve ONLY the registered hosts; every other name fails DNS."""
    return "MAP * ~NOTFOUND, " + ", ".join(f"EXCLUDE {h}" for h in sorted(set(hosts)))
```
(`_is_ip(host)` = `ipaddress.ip_address` succeeds; `normalize_host` from Task 10.) M4 touch points: `POST /scans` calls `check_scan_url(spec.url, registered_hosts=<tenant hosts, or DPS_SCAN_REGISTERED_HOSTS locally>, settings=...)` and maps `NetGuardError` to `DpsHttpError(422, e.code, e.detail)`; the crawler's `chromium.launch(args=[..., f"--host-resolver-rules={chromium_resolver_rules(hosts)}"])`.

`require_scope` (M0 `dps/auth.py`): after defining `dependency`, set `dependency.__dps_scope__ = scope  # type: ignore[attr-defined]` so the route test can see it.

`_SecurityHeaders` (M0 `dps/app.py`): `if request.url.path.startswith("/api/"): response.headers.setdefault("Cache-Control", "no-store")`.

- [ ] **Step 1: Write the tests**

`tests/security/test_route_auth.py`:
```python
from __future__ import annotations

import re

from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from dps.app import create_app
from dps.config import Settings

PUBLIC = {"/health", "/readyz", "/metrics", "/", "/{full_path:path}", "/docs", "/docs/oauth2-redirect",
          "/redoc", "/api/v1/openapi.json"}


def _app():  # noqa: ANN202
    return create_app(Settings(_env_file=None, env="local", auth_enabled=True, api_keys="", bootstrap_api_key="",
                               llm_provider="stub", rate_limit_enabled=False))


def _scoped(route: APIRoute) -> bool:
    return any(getattr(d.call, "__dps_scope__", None) for d in route.dependant.dependencies)


def test_every_api_route_declares_a_scope() -> None:
    routes = [r for r in _app().routes if isinstance(r, APIRoute)]
    unscoped = sorted(f"{sorted(r.methods)} {r.path}" for r in routes if r.path not in PUBLIC and not _scoped(r))
    assert unscoped == []
    assert all(r.path.startswith("/api/v1/") for r in routes if r.path not in PUBLIC)


def test_every_api_route_refuses_an_anonymous_call() -> None:
    client = TestClient(_app())
    for route in [r for r in client.app.routes if isinstance(r, APIRoute) and r.path.startswith("/api/v1/")
                  and r.path != "/api/v1/openapi.json"]:
        path = re.sub(r"\{[^}]+\}", "x", route.path)
        for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
            status = client.request(method, path, json={}).status_code
            assert status == 401, f"{method} {route.path} answered {status} without a key"
```
`tests/security/test_validation_errors.py` — `POST /api/v1/detect` with `{"data": {"x": 1}, "text": 123456789, "options": {"locale": ["123-45-6789"]}}` → 422, `code == "invalid_request"`, body text contains neither `123456789` nor `123-45-6789`, every detail item has exactly the keys `loc`, `msg`, `type`.

`tests/security/test_regex_guard.py`:
```python
from __future__ import annotations

import pytest

from dps.engine.detect.custom import RecognizerSpec
from dps.engine.detect.regex_guard import UnsafePattern, check_pattern


@pytest.mark.parametrize("pattern", [r"\bEMP-\d{6}\b", r"[A-Z]{5}\d{4}[A-Z]", r"(?:\+91[ -]?)?\d{10}",
                                     r"\b\d{3}-\d{2}-\d{4}\b", r"(?:\d{1,3}\.){3}\d{1,3}", r"(?:\d+\.){3}\d+"])
def test_ordinary_patterns_pass(pattern: str) -> None:
    check_pattern(pattern)


@pytest.mark.parametrize("pattern,rule", [(r"(a+)+$", "nested"), (r"(\w+\s?)*$", "nested"), (r"(.*a){12}", "nested"),
                                          (r"(\d+)\1", "backreferences"), (r"a{1,5000}", "bounds"), ("a" * 600, "longer"),
                                          (r"(", "compile")])
def test_dangerous_patterns_are_refused(pattern: str, rule: str) -> None:
    with pytest.raises(UnsafePattern, match=rule):
        check_pattern(pattern)


def test_policy_recognizers_are_guarded() -> None:
    with pytest.raises(ValueError, match="nested"):
        RecognizerSpec(name="bad", supported_entity="EMPLOYEE_ID", patterns=[{"name": "x", "regex": r"(a+)+", "score": 0.5}])
```
`tests/security/test_netguard.py` — with `registered = ["onboarding-uat.bank.internal", "demo-site", "10.20.30.40", "127.0.0.1", "rebind.bank.internal", normalize_host("[::ffff:169.254.169.254]")]` (the last is `"::ffff:a9fe:a9fe"`, the normalised literal), a fake resolver (`{"onboarding-uat.bank.internal": ["10.1.2.3"], "demo-site": ["172.18.0.5"], "rebind.bank.internal": ["169.254.169.254"]}`):
  - allowed: `https://onboarding-uat.bank.internal/apply` → `"onboarding-uat.bank.internal"`; `http://demo-site:8410/` (local) → `"demo-site"`; `http://10.20.30.40/` → `"10.20.30.40"`;
  - refused with `host_forbidden`: `ftp://demo-site/`, `https://user:pw@demo-site/`, `http://0x7f000001/`, `http://2130706433/`, `http://127.1/`, `http://[::ffff:169.254.169.254]/` (registered, IPv4-mapped metadata address), `https://rebind.bank.internal/` (registered, resolves to metadata), `http://127.0.0.1/` with `env="prod"` and `egress_tls_required=False`, `http://demo-site/` with `egress_tls_required=True`;
  - refused with `host_not_registered`: `https://evil.example/`;
  - `chromium_resolver_rules(["b", "a", "a"]) == "MAP * ~NOTFOUND, EXCLUDE a, EXCLUDE b"`;
  - no error detail contains `pw` or `user`.
`tests/security/test_selection_injection.py` — parametrised over `engine ∈ ("postgres", "oracle")` and `where` ∈ `["1=1; DROP TABLE customers", "id = 1 UNION SELECT password FROM users", "pg_sleep(10) IS NULL", "dbms_pipe.receive_message('x', 10) = 1", "id IN (SELECT id FROM dual) FOR UPDATE", "name = 'x' --", "utl_http.request('http://x') IS NOT NULL", "1=1 /* */ ; COMMIT"]` → `validate_selection(Selection(where=w), engine, None)` raises `SelectionError`; `query` cases `"DELETE FROM t"`, `"SELECT * INTO x FROM t"`, `"SELECT 1; SELECT 2"` → `SelectionError`; identifiers `'name"; DROP'`, `"a b"`, `"1abc"` → `quote_ident` raises; Mongo filters `{"$where": "sleep(1000)"}`, `{"a": {"$function": {}}}`, `{"$expr": {"$gt": ["$a", 1]}}`, `{"a": {"$accumulator": {}}}`, `{"$or": [{"a": 1}, {"$where": "1"}]}` → `selection_operator_denied`. (All offline; M2's functions.)
`tests/security/test_cross_tenant.py` (`@pytest.mark.db`) — tenant `ta` creates a connection, a run job with evidence and an artefact, a policy and a snapshot; a key granted only `tb` (all scopes) gets **404** on `GET /connections/{id}`, `/jobs/{id}`, `/jobs/{id}/partitions`, `/evidence/{id}`, `/evidence/{id}/pdf`, `/artefacts/{id}`, `/snapshots/{id}`, `/policies/{name}`, and `X-DPS-Tenant: ta` with that key → 403.
`tests/security/test_secrets_hygiene.py`:
  - `test_no_private_keys_or_cloud_credentials_in_the_tree`: scan tracked files (`git ls-files`) under `dps/ tools/ deploy/ docker/ frontend/src/` for `-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----`, `AKIA[0-9A-Z]{16}`, `ghp_[A-Za-z0-9]{36}`, `xox[baprs]-[A-Za-z0-9-]+`, `dps_[A-Za-z0-9_-]{43}` → no match.
  - `test_env_files_are_not_tracked`: `git ls-files .env '.env.*'` returns only `.env.example`.
  - `test_settings_repr_hides_secrets`: `Settings(coin_client_secret="s3nt1nel-a", api_keys="s3nt1nel-b", job_kek="s3nt1nel-c", tokenizer_token="s3nt1nel-d", pg_password="s3nt1nel-e")` → none of the sentinels in `repr()` or `str()`.
  - `test_no_dangerous_python`: no `pickle` import, no `eval(`/`exec(`, no `yaml.load(` without `SafeLoader`, no `allow_pickle=True`, no `verify=False` outside `dps/llm/gateway.py`'s documented COIN token mint, in `dps/`.
  - `test_console_never_injects_html`: no `dangerouslySetInnerHTML` and no `innerHTML` assignment in `frontend/src/`.
`tests/security/test_headers.py` — `/api/v1/policies/presets` response has `cache-control: no-store`; `/health` does not; the SPA index (when `frontend/dist` exists) carries the M0 CSP; `/api/v1/obfuscate` → `x-content-type-options: nosniff`.
`tests/test_no_values_in_logs_m5.py` — configure the real logging pipeline (`configure_logging("DEBUG", json_output=True)`), capture stdout/stderr (`capsys`), then exercise with sentinel values (`"Zebulon Quixote"`, `"123-45-6789"`, `"AFFLUENT-SENTINEL"`, a raw `dps_` key, a 32-byte seed's base64): `GaussianCopula.fit/sample_rows/to_bytes`, `fidelity_report`, `nndr`, `prepare_tabular` with the Task 6 fakes (including a forced `privacy_gate`), `HttpTokenizer` against a `MockTransport` that fails, `check_grant` refusals, `DbKeyStore.lookup` misses, `render_evidence_pdf`, `diff_policies`, `check_scan_url` refusals, `check_pattern` refusals → captured output contains none of the sentinels.

- [ ] **Step 2: Run them; fix what fails** — before the fixes: `test_validation_errors.py`, `test_settings_repr_hides_secrets`, `test_regex_guard.py` (nested cases), `test_headers.py` and the netguard encodings fail; route-auth fails on any route a milestone forgot to scope (fix the route, never the allow-list).
- [ ] **Step 3: Implement** the fixes above; contract §5.3 adds `host_forbidden` (422).
- [ ] **Step 4: Write `docs/security/2026-10-11-m5-security-review.md`** — scope (the five areas), method (threat-model walk of every route group + the tests above + `bandit -q -r dps` if available, else the grep checks), the S1–S13 table above with a "Status" column filled from the run (`fixed + test` / `accepted`), residual risks (Python `re` has no timeout — the static guard is heuristic, mitigated by `max_text_chars`; private-range targets are allowed by design for internal UAT sites; failed-auth attempts are not audited because they have no tenant — they are counted in `dps_requests_total{outcome="rejected"}`; keys propagate to other replicas within `DPS_KEY_CACHE_SECONDS`), and the commands to re-run the review.
- [ ] **Step 5: Run tests**: `pytest -q tests/security tests/test_no_values_in_logs_m5.py` → all pass (`test_cross_tenant.py` with `-m db`); full unit gate green.
- [ ] **Step 6: Commit**

```bash
git add dps/layers/api/validation.py dps/engine/detect/regex_guard.py dps/engine/detect/custom.py dps/netguard.py dps/auth.py dps/config.py dps/app.py dps/layers/api/routers/scans.py dps/layers/scanner/crawler.py docs/security/2026-10-11-m5-security-review.md tests/security tests/test_no_values_in_logs_m5.py docs/superpowers/plans/2026-10-11-m1-m5-contract.md
git commit -m "fix(security): scrubbed 422s, secret reprs, ReDoS guard, SSRF guard with DNS checks, scoped-route and cross-tenant tests" -- dps/layers/api/validation.py dps/engine/detect/regex_guard.py dps/engine/detect/custom.py dps/netguard.py dps/auth.py dps/config.py dps/app.py dps/layers/api/routers/scans.py dps/layers/scanner/crawler.py docs/security/2026-10-11-m5-security-review.md tests/security tests/test_no_values_in_logs_m5.py docs/superpowers/plans/2026-10-11-m1-m5-contract.md
```

---
### Task 18: Console foundations — types, API groups, shared components, routes, version

**Files:**
- Modify: `frontend/src/lib/types.ts`, `frontend/src/lib/api.ts`, `frontend/src/App.tsx`, `frontend/src/components/Layout.tsx`, `frontend/vite.config.ts`, `frontend/src/styles.css`
- Create: `frontend/src/vite-env.d.ts` (if absent), `frontend/src/components/{BarChart,ConfirmDialog,KeyOnceCard,DownloadButton,Kpi}.tsx`, `frontend/src/lib/format.ts`

Rules (contract §14): API calls only through `lib/api.ts`; types mirror the pydantic models field for field (snake_case); raw keys, key material and seeds live only in React state — never `localStorage`, never the URL; no `dangerouslySetInnerHTML` (Task 17 test); no new npm dependency (charts are hand-written SVG, as in the mockups).

`lib/types.ts` additions (one interface per model, same field names): `ApiKeyView`, `CreatedKey`, `RotateResult {new: CreatedKey; old: ApiKeyView}`, `EffectiveTenant`, `TenantUsage`, `TenantView`, `TenantSpec`, `TenantPatch`, `AuditRow`, `Page<T> {items: T[]; next_cursor: string | null}`, `PostureCheck`, `PostureReport`, `QueueOut {queues: Record<string, unknown>[]; workers: WorkerRow[]}`, `SummaryOut`, `TrendPoint`, `EntityCount`, `JobBrief`, `ReportsJobsOut`, `SweepDay`, `FailedSweep`, `EvidenceItem`, `ApiRollup`, `PresetView`, `TenantPolicyView`, `TenantPolicyDetail`, `PolicyVersionView`, `PolicyChange`, `FidelityBlock`, `PrivacyBlock`, `TabularBlock`; the M2 `EvidencePack` interface gains optional `fidelity?: FidelityBlock | null`, `privacy?: PrivacyBlock | null`, `tabular?: TabularBlock | null`, and the column fields `data_type`, `confidence`, `override`, `params`.

`lib/api.ts` — `ApiError` gains `code?: string` (contract §8.2 bodies `{"detail", "code"}`), plus a blob helper and the M5 groups:
```ts
export class ApiError extends Error {
  constructor(public status: number, message: string, public code?: string) { super(message); }
}

async function toError(res: Response): Promise<ApiError> {
  let detail = res.statusText;
  let code: string | undefined;
  try {
    const body = await res.json();
    detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail ?? detail);
    code = typeof body.code === 'string' ? body.code : undefined;
  } catch { /* non-JSON error body */ }
  return new ApiError(res.status, detail, code);
}

function headers(auth: Auth, json = true): Record<string, string> {
  const h: Record<string, string> = json ? { 'Content-Type': 'application/json' } : {};
  if (auth.apiKey) h['X-API-KEY'] = auth.apiKey;
  if (auth.tenant) h['X-DPS-Tenant'] = auth.tenant;
  return h;
}

/** GET a file with the API key header (a plain <a href> cannot send it) — PDFs, CSVs, JSON packs. */
async function download(path: string, auth: Auth): Promise<{ blob: Blob; filename: string }> {
  const res = await fetch(path, { headers: headers(auth, false) });
  if (!res.ok) throw await toError(res);
  const match = /filename="([^"]+)"/.exec(res.headers.get('content-disposition') ?? '');
  return { blob: await res.blob(), filename: match?.[1] ?? 'download' };
}

export function qs(params: Record<string, string | number | boolean | null | undefined>): string {
  const u = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== '') u.set(k, String(v));
  const s = u.toString();
  return s ? `?${s}` : '';
}
```
`call()` throws `await toError(res)`. Groups (each a thin wrapper over `call`/`download`, paths exactly the §9 routes):
- `api.admin`: `keys`, `createKey(body)`, `rotateKey(id, graceHours)`, `disableKey(id)`, `enableKey(id)`, `tenants`, `createTenant(spec)`, `patchTenant(id, patch)`, `audit(filters)`, `auditCsv(filters)` (download), `queue`, `posture`, `runRetention(dryRun)`;
- `api.reports`: `summary(days)`, `jobs(days)`, `evidence(filters)`, `apiRollups(days)`, `apiRollupFile(day, action, format)` (download);
- `api.evidence.pdf(id)` and `api.evidence.json(id)` (download; added to M2's group);
- `api.policies`: `list`, `create(name, basePreset)`, `get(name)`, `saveDraft(name, body)`, `discardDraft(name)`, `publish(name, ticket, note)`, `versionYaml(name, v)` (download), `diff(name, from, to)`; M1's `testRecognizer` and `entities` stay where M1 put them.

Shared components (complete code for the non-obvious ones):

`components/DownloadButton.tsx`:
```tsx
import { useState } from 'react';
import { ApiError } from '../lib/api';

export function DownloadButton({ label, fetcher, className = 'btn btn-sm', ariaLabel }: {
  label: string; fetcher: () => Promise<{ blob: Blob; filename: string }>; className?: string; ariaLabel?: string;
}): JSX.Element {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function run(): Promise<void> {
    setBusy(true);
    setError(null);
    try {
      const { blob, filename } = await fetcher();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Download failed');
    } finally {
      setBusy(false);
    }
  }
  return (
    <span className="dl">
      <button type="button" className={className} onClick={() => void run()} disabled={busy} aria-label={ariaLabel ?? label}>
        {busy ? 'Preparing…' : label}
      </button>
      {error && <span className="err" role="alert">{error}</span>}
    </span>
  );
}
```

`components/BarChart.tsx` (stacked bars, accessible, with a table view — the "View as table" toggle of mockups 01/09):
```tsx
import { useMemo, useState } from 'react';

export interface Series { key: string; label: string; color: string }   // color: a CSS variable, e.g. 'var(--emerald)'

export function BarChart({ data, series, xKey, label, height = 200 }: {
  data: Record<string, string | number>[]; series: Series[]; xKey: string; label: string; height?: number;
}): JSX.Element {
  const [asTable, setAsTable] = useState(false);
  const width = 640;
  const pad = { l: 36, r: 8, t: 8, b: 22 };
  const max = useMemo(() => Math.max(1, ...data.map((d) => series.reduce((s, x) => s + Number(d[x.key] ?? 0), 0))), [data, series]);
  const bw = (width - pad.l - pad.r) / Math.max(1, data.length);
  const y = (v: number): number => pad.t + (height - pad.t - pad.b) * (1 - v / max);
  return (
    <div className="chart">
      <div className="chart-tools">
        <div className="legend">{series.map((s) => <span key={s.key}><i style={{ background: s.color }} />{s.label}</span>)}</div>
        <button type="button" className="btn btn-sm" aria-pressed={asTable} onClick={() => setAsTable((v) => !v)}>
          {asTable ? 'View as chart' : 'View as table'}
        </button>
      </div>
      {asTable ? (
        <table className="tbl"><thead><tr><th>{xKey}</th>{series.map((s) => <th key={s.key} className="num">{s.label}</th>)}</tr></thead>
          <tbody>{data.map((d) => <tr key={String(d[xKey])}><td>{String(d[xKey])}</td>
            {series.map((s) => <td key={s.key} className="num mono">{Number(d[s.key] ?? 0).toLocaleString()}</td>)}</tr>)}</tbody></table>
      ) : (
        <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={label} width="100%" height={height}>
          {[0, 0.5, 1].map((f) => (
            <g key={f}><line x1={pad.l} x2={width - pad.r} y1={y(max * f)} y2={y(max * f)} stroke="var(--border)" />
              <text x={pad.l - 6} y={y(max * f) + 4} textAnchor="end" fontSize="10" fill="var(--ink-3)">{Math.round(max * f).toLocaleString()}</text></g>
          ))}
          {data.map((d, i) => {
            let acc = 0;
            return (
              <g key={String(d[xKey])}>
                {series.map((s) => {
                  const v = Number(d[s.key] ?? 0);
                  const top = y(acc + v);
                  const h = y(acc) - top;
                  acc += v;
                  return v > 0 ? (
                    <rect key={s.key} x={pad.l + i * bw + bw * 0.15} y={top} width={bw * 0.7} height={Math.max(h, 1)} fill={s.color} rx={2}>
                      <title>{`${String(d[xKey])} · ${s.label}: ${v.toLocaleString()}`}</title>
                    </rect>
                  ) : null;
                })}
                {(i % Math.ceil(data.length / 7) === 0 || i === data.length - 1) && (
                  <text x={pad.l + i * bw + bw / 2} y={height - 6} textAnchor="middle" fontSize="10" fill="var(--ink-3)">{String(d[xKey]).slice(5)}</text>
                )}
              </g>
            );
          })}
        </svg>
      )}
    </div>
  );
}
```
`components/ConfirmDialog.tsx` — a native `<dialog>` opened with `showModal()` from an effect when `open` flips true; props `{open, title, body, confirmLabel, danger?, requireText?, children?, onConfirm, onCancel}`; with `requireText`, the confirm button stays disabled until the typed text equals it (the in-place/append pattern of contract §14); Escape and backdrop call `onCancel`.
`components/KeyOnceCard.tsx` — props `{name, rawKey, onStored}`; shows the key in a `<code>`, "Copy key" (`navigator.clipboard.writeText`), an amber callout "Store this key in your secret manager now, for example as `dps/keys/<name>`. DPS cannot show it again; if it is lost, rotate it."; "I have stored the key" calls `onStored()` after computing `sha256` with `crypto.subtle.digest` and replaces the card with "Key hidden. Calls authenticate against its hash — prefix `dps_xxxx` · sha256 `abcd…wxyz`". The raw key is dropped from state at that moment.
`components/Kpi.tsx` — `{label, value, delta?, hero?, zero?}` → the mockup `.card.kpi` markup.
`lib/format.ts` — `compact(n)` (2.71M / 24.6K), `relative(ts)` ("12 s ago", "yesterday", "3 days ago"), `utc(ts)` ("11 Oct 09:49"), `shortHash(h)` (`sha256:18d24931…3a66`).

`vite.config.ts` gains `define: { __APP_VERSION__: JSON.stringify(process.env.npm_package_version ?? '0.0.0') }`; `src/vite-env.d.ts` declares `declare const __APP_VERSION__: string;`; the sidebar footer shows `v{__APP_VERSION__}`.
`App.tsx` routes: `/reports` → `Reports`, `/reports/evidence/:evidenceId` (M2's `EvidencePack`), `/admin` → `Admin`, `/policies` and `/policies/:name` → `Policies`. The `Soon` placeholder is removed.
`styles.css` gains the mockup classes these pages use that M0–M4 did not define: `.filters`, `.split`, `.chart*`, `.tbl-scroll`, `.card-foot`, `.dist`, `.cmx`, `.ramp`, `.pm`, `.sheet`, `.doc-head`, `.seal`, `.meta`, `.stats`, `.kv`, `.timeline`, `.sig`, `.chk`, `.health .svc`, `.scopes`, `.kname`, `.kpref`, `.q`, `.newkey`, `.keyval`, `.frow`, `.checks`, `.check.priv`, `.pol*`, `.preset`, `.plist`, `.diff .add/.del`, `.dl`, and the evidence `@media print` block — copied from `docs/design/mockups/{09,10,11,05}-*.html` `<style>` blocks, using only the Vault tokens.

- [ ] **Step 1: Implement** the files above.
- [ ] **Step 2: Gate**: `cd frontend && npm run typecheck && npm run build` → no errors; `grep -rn "dangerouslySetInnerHTML\|localStorage.setItem('dps.key" frontend/src` → nothing new (only M0's settings keys).
- [ ] **Step 3: Commit**

```bash
git add frontend/src/lib/types.ts frontend/src/lib/api.ts frontend/src/lib/format.ts frontend/src/App.tsx frontend/src/components/Layout.tsx frontend/src/components/BarChart.tsx frontend/src/components/ConfirmDialog.tsx frontend/src/components/KeyOnceCard.tsx frontend/src/components/DownloadButton.tsx frontend/src/components/Kpi.tsx frontend/src/styles.css frontend/src/vite-env.d.ts frontend/vite.config.ts
git commit -m "feat(console): M5 API groups and types, download/chart/dialog/key-once components, routes" -- frontend/src/lib/types.ts frontend/src/lib/api.ts frontend/src/lib/format.ts frontend/src/App.tsx frontend/src/components/Layout.tsx frontend/src/components/BarChart.tsx frontend/src/components/ConfirmDialog.tsx frontend/src/components/KeyOnceCard.tsx frontend/src/components/DownloadButton.tsx frontend/src/components/Kpi.tsx frontend/src/styles.css frontend/src/vite-env.d.ts frontend/vite.config.ts
```

---

### Task 19: Overview — protection posture (mockup 01, complete)

**Files:**
- Modify: `frontend/src/pages/Overview.tsx`

Built to `docs/design/mockups/01-dashboard.html`, reading `GET /reports/summary?days=14` and `/readyz`:
- Topbar crumb "Overview / Tenant: <tenant>"; pill "All layers ready" (or "N degraded") with the component list from `/readyz`.
- Page head "Protection posture" · "Last 14 days · policy pack <most-used policy>"; buttons **Export evidence** (→ `/reports`) and **Open playground**.
- KPI row (`Kpi`): **Values protected** (hero; `tiles.values_protected`, delta vs `values_protected_prev` as ▲/▼ %), **PII findings** (`findings`, "`entity_types` entity types"), **Jobs** (`jobs_running` running; "completed · failed · `snapshots_awaiting_review` awaiting review"), **Raw PII sent to LLM** (zero tile; `raw_pii_sent_to_llm`; "`llm_calls` gateway calls since start · placeholders only").
- Row: **Protected values per day** (`BarChart`, series obfuscated `var(--teal)` / synthetic `var(--violet)`, x = `trend[].day`) and **Posture score** (SVG ring for `posture.score` + four bars: leakage sweeps passed, reversible with key held by caller, columns reviewed before run, policies on latest version; chip "Verified" when score ≥ 95 else amber "Review").
- Three layer cards: **Layer 1 · API** ("stateless"; requests, by-mode legend), **Layer 2 · Connectors** (rows, by-engine split, "`groups` job groups keep ids consistent across systems"; amber chip "N awaiting review" when snapshots await), **Layer 3 · UI scanner** (chip "never submits"; scans/pages/fields/risk flags; "Last scan: N pages, P personas, 0 submitted").
- Row: **Findings by entity type** (table of `findings_by_type`, entity chip toned by sensitivity, bar, count) and **Recent jobs** (`recent_jobs`: id → `/data-sources/jobs/:id` or `/scanner/scans/:id`, source → target, mode chip, progress bar, status chip with `note`).
- **Degraded mode:** when `/reports/summary` answers 404/503 (an L1-only deployment without a database), render M0's tiles from `/metrics` and a callout "Connect the metadata database to see 14-day trends" — the page never errors.

- [ ] **Step 1: Implement**; **Step 2:** `npm run typecheck && npm run build`; **Step 3:** with the compose stack up and the demo seeded (Task 26), open `http://localhost:8400/` and compare with the mockup side by side in light and dark themes — same sections, order and labels; numbers are live.
- [ ] **Step 4: Commit**

```bash
git add frontend/src/pages/Overview.tsx && git commit -m "feat(console): Overview to mockup 01 — posture, trends, layer cards, findings, recent jobs" -- frontend/src/pages/Overview.tsx
```

---

### Task 20: Reports & audit (mockup 09)

**Files:**
- Create: `frontend/src/pages/Reports.tsx`, `frontend/src/components/AuditTable.tsx`, `frontend/src/components/EvidenceTable.tsx`

Built to `docs/design/mockups/09-reports.html`:
- Topbar "Reports & audit / Tenant: <tenant> + N tenants you can read" (N from `api.admin.tenants` when the key has admin; otherwise omitted); pill "Audit writer healthy · append-only" from the `/readyz` `db` component.
- Head "Evidence and audit" + subtitle from the mockup; action **Export audit rows as CSV** (`DownloadButton` → `api.admin.auditCsv(current filters)`; hidden when the key lacks `admin` — detected by a 403 from `api.admin.audit`). The mockup's "Schedule a weekly export" is not built (no scheduler in v1; recorded in the self-review).
- Filters row (state in the URL query via `useSearchParams` so a view can be shared): range select 7/14/30 days, Layer seg All/API/Connectors/UI scanner (`layer=api|conn|ui`), Outcome seg All/OK/Failed/Blocked, search box (job, ticket or key name), Clear. All sections below respect them.
- KPIs: **Evidence packs** (`totals.packs`: "R jobs · S scans · A API roll-ups"), **Verification sweeps** (passed + failed; "P passed · F failed, all failed closed"), **Audit rows** (`totals.audit_rows`, "append-only · monthly partitions"), **Customer values stored: 0** (zero tile; "counts, hashes and key names only").
- Split: **Verification sweeps per day** (`BarChart` passed `var(--emerald)` / failed `var(--rose)` from `sweeps`, filtered by layer) and **Failed sweeps** (cards from `failed_sweeps`: id + partitions or route, UTC time, what failed in words built from `error_code` and `paths`, link to the job).
- **Evidence packs** table (`EvidenceTable`): rows from `api.reports.evidence` merged with `api.reports.apiRollups` (one row per day and action, labelled "daily roll-up"), newest first; columns Job or request (id; `source → target`; sub-line ticket · write mode · rows or "C calls"), Layer, Mode chip (teal obfuscate / violet synthesize), Policy (`name vN`), Verification chip (`passed · 0 leaks`, `passed · 0 exact rows` for synthesize with fidelity, `passed · 0 POSTs left` for scans, rose `failed · N leaks caught`), Created (UTC), Download **JSON** (`api.evidence.json` / `apiRollupFile(..., 'json')`) and **PDF** (`api.evidence.pdf` / `apiRollupFile(..., 'pdf')`); the id links to `/reports/evidence/:id`; "Show older packs" follows `next_cursor`; empty-state row with the mockup's wording.
- **Audit log** (`AuditTable`, admin keys only; otherwise a callout "The audit log needs a key with the admin scope"): Time (UTC + date), Tenant, Key name, Route, Mode, Policy, Findings, Methods (first method + "+n"), Outcome chip (emerald ok / rose failed / slate-or-amber blocked) with the status as sub-line; scope switch "This tenant | All tenants I can read" (`tenants=*`); "Load 50 older rows" follows the cursor; lock line "Append-only. Rows cannot be edited or deleted; partitions expire under the retention policy."
- Tables stack into labelled cards under 760 px (`data-l` labels as in the mockup).

- [ ] **Step 1: Implement**; **Step 2:** typecheck + build; **Step 3:** browser check against the mockup (light/dark, 1440 px and 390 px widths) with seeded data; **Step 4: Commit**

```bash
git add frontend/src/pages/Reports.tsx frontend/src/components/AuditTable.tsx frontend/src/components/EvidenceTable.tsx
git commit -m "feat(console): Reports & audit to mockup 09 — sweeps, failed sweeps, evidence packs, audit log, CSV export" -- frontend/src/pages/Reports.tsx frontend/src/components/AuditTable.tsx frontend/src/components/EvidenceTable.tsx
```

---

### Task 21: Evidence pack page — synthetic section, PDF download, print (mockup 10)

**Files:**
- Modify: `frontend/src/pages/EvidencePack.tsx` (M2)
- Create: `frontend/src/components/FidelityPanel.tsx`

The M2 page renders sections 1–5 and 7–8 of mockup 10 from `GET /evidence/{id}`. M5 completes it:
- Topbar: **Download JSON** (`api.evidence.json`), **Download PDF** (`api.evidence.pdf` — the server-rendered, archivable copy), **Print** (`window.print()` with the mockup's print CSS).
- Section 2 shows the policy as `name vN` (new `policy.version`) and the key custody line.
- Section 3 (snapshot table) gains Type (`data_type`), Conf. (`confidence`), Decision (`accepted` / **overridden**) from the new column fields.
- Section 6 **Synthetic** (`FidelityPanel`, only when `fidelity` is present): lead sentence (engine, preview rows, the label that the seed is not in the pack); **Fidelity** table — column, metric (KS/TVD), distance, a `.dist` bar against `limits.column`, null-rate delta in pp; the **correlation-matrix delta** grid (`.cmx`, lower triangle, cell shade `color-mix(in srgb, var(--ink-2) <|Δ| / limits.corr × 45>%, var(--surface))`, a `.ramp` legend 0.00 → limit) with the "Largest change … Mean … across N pairs" note; **Privacy** cards (`.pm`) — exact-row matches (chip "enforced", value `0 of N`, resample counts), nearest-neighbour distance ratio (chip pass/below target, medians), quasi-identifier uniqueness (synthetic vs source, overlap), membership-inference proxy (slate "not run" with the reason); excluded tabular columns listed under the table. For `privacy.engine == "none"` the section shows one amber callout "Privacy not measured — non-sensitive columns were copied from the source (tabular synthesis off)".
- Section 7 regulatory callouts chosen by `label` (pseudonymised vs measured-not-claimed), wording as in the mockup.
- `@media print` styles (Task 18 copied them) hide chrome and print exactly the sheet.

- [ ] **Step 1: Implement**; **Step 2:** typecheck + build; **Step 3:** open a synthesize job's pack from the seeded demo, compare with `10-evidence-pack.html#synthetic`, print-preview, and download the PDF (opens in Preview, 3–5 pages, same sections); **Step 4: Commit**

```bash
git add frontend/src/pages/EvidencePack.tsx frontend/src/components/FidelityPanel.tsx
git commit -m "feat(console): evidence pack synthetic section, server PDF download and print to mockup 10" -- frontend/src/pages/EvidencePack.tsx frontend/src/components/FidelityPanel.tsx
```

---

### Task 22: Admin page (mockup 11)

**Files:**
- Create: `frontend/src/pages/Admin.tsx`, `frontend/src/components/{KeysTable,CreateKeyCard,TenantsTable,HostsCard,HealthGrid,PostureList}.tsx`

Built to `docs/design/mockups/11-admin.html`; every action goes through `api.admin` and is audited server-side:
- Topbar "Admin / <env from posture report>"; pill "Posture · P of T checks pass" (amber when any fails); pill with the caller's key name and scopes.
- Head: subtitle; **Export configuration** (client-side JSON of tenants, key metadata, connections metadata and the posture report — `DownloadButton` with a local `Blob`; contains no secret by construction because none of those responses carry one); **Create key** scrolls to the card.
- KPIs: Active API keys ("D disabled · R due for rotation" — R = active keys with `age_days > key_rotation_max_age_days`, taken from the `keys_rotated` posture detail count), Tenants ("J jobs running of M allowed"), Posture checks ("P of T pass", "N to fix: <first failing name>"), **Keys stored in clear: 0** (zero tile, "SHA-256 hashes only · each key shown once").
- **API keys** (`KeysTable`): name + prefix (`dps_9Fq2…`, "· new" when created today, "· disabled <date>"), tenants (`all tenants (*)`), scope chips (amber for `reveal`, `admin` with legend "privileged scope"), created, last used (relative), expires (or "never" + amber chip "rotate · N days" past the rotation age), actions **Rotate** (`ConfirmDialog` with "Old key stops working" seg now / in 24 hours / in 7 days → `rotateKey(id, 0|24|168)` → `KeyOnceCard` for the new key) and **Disable** / **Re-enable**; static (env) keys show "env" and no actions; filter box by name or tenant; footer "N keys · prefixes only; DPS cannot show a full key after creation · scopes: …".
- **Create key** (`CreateKeyCard`): name (pattern help), tenant select (from `tenants`), scope checkboxes (`reveal`, `admin` styled privileged), expiry seg 30/90/180 days/1 year → `createKey` → `KeyOnceCard`; API errors shown inline by `code` (`invalid_name`, `scope_escalation`, `tenant_escalation`, `invalid_expiry`).
- **Tenants and quotas** (`TenantsTable`): tenant + display name, running "x of y" bar, daily rows "x of quota" bar (amber ≥ 80 %, with the mockup's callout naming the tenant and "queue until 00:00 UTC"), retention days, keys (active + "+n off"), **Edit** (dialog: display name, retention days, max running, max queued, daily row quota, note → `patchTenant`); **Add tenant** dialog → `createTenant`.
- **Registered connections** (M2 `api.connections.list` for the current tenant): name + engine · owner, secret reference (`secret_ref`, or "inline · expires <t>"), tag chips (rose `production`), "Who may write in place or append" (production → names of keys holding `admin` for this tenant; otherwise keys holding `jobs`; "Not applicable · files only" for s3/pvc) with "plus a confirmation with row counts", last test (time + first check name from `last_test`); the explanatory callout from the mockup.
- **UI-scanner hosts** (`HostsCard`): the tenant's `registered_hosts` (host, tenant, storage-state reference column shows the scanner connection's `secret_ref` when one exists for that host, else "none · public pages only", added date = tenant `updated_at`), **Remove** (confirm) and **Add host** (input + tenant select → `patchTenant(t, {registered_hosts: [...old, new]})`; the server normalises and validates; `code` errors shown inline); footnote from the mockup.
- **Service health** (`HealthGrid`): one tile per `/readyz` component (`api-core`, `db`, `jobs`, `llm-gateway`, `ocr`, `fpe`, `ner`, `browser`, `tokenizer`, `keys`) with ready/degraded chip and its value-free detail, plus `worker`/`browser-worker` tiles from `api.admin.queue` (online/busy counts). The mockup's 30-day status strips are not rendered (DPS keeps no readiness history; recorded in the self-review).
- **Posture checks** (`PostureList`): each check with ✓/✕, name, pass/fail chip, detail; a failing `keys_rotated` shows **Rotate <key name>** which opens the rotate dialog for that key; footer "Staging and production refuse to boot when … Checked <relative time>."

- [ ] **Step 1: Implement**; **Step 2:** typecheck + build; **Step 3:** browser check against the mockup with the demo data (create a key, see it once, store, rotate with 24 h grace, disable, re-enable; add and remove a host; edit a quota); **Step 4: Commit**

```bash
git add frontend/src/pages/Admin.tsx frontend/src/components/KeysTable.tsx frontend/src/components/CreateKeyCard.tsx frontend/src/components/TenantsTable.tsx frontend/src/components/HostsCard.tsx frontend/src/components/HealthGrid.tsx frontend/src/components/PostureList.tsx
git commit -m "feat(console): Admin to mockup 11 — keys shown once, rotation with grace, tenants and quotas, hosts, health, posture" -- frontend/src/pages/Admin.tsx frontend/src/components/KeysTable.tsx frontend/src/components/CreateKeyCard.tsx frontend/src/components/TenantsTable.tsx frontend/src/components/HostsCard.tsx frontend/src/components/HealthGrid.tsx frontend/src/components/PostureList.tsx
```

---

### Task 23: Policies page — presets, matrix, recognizer builder, plan preview, versions (mockup 05)

**Files:**
- Modify or create (M1 owns the first version per contract §15): `frontend/src/pages/Policies.tsx`
- Create: `frontend/src/components/{PolicyList,TreatmentMatrix,DeclaredFields,RecognizerBuilder,PlanPreview,PolicyVersions}.tsx`, `frontend/src/lib/policyEdit.ts`

End state (whatever M1 shipped is folded into these components; M1's API calls — `/entities`, `/policies/test-recognizer`, `/policies/plan`, `/policies/presets` — are reused unchanged):
- Route `/policies/:name`; `/policies` redirects to the tenant's first policy or `gdpr-pseudonymise`.
- Topbar "Policies / <name>"; pill "Draft vN · k changes not published" (amber) when a draft exists; pill "FF1 on|off" from `/readyz` `fpe`.
- Head actions: **Discard draft** (confirm → `discardDraft`), **Preview plan** (scrolls), **Publish vN** (dialog with change ticket + note → `publish`); hidden/disabled without `admin` (403 from a draft call switches the page to read-only with a callout).
- **Left column** (`PolicyList`): "Presets" card — every shipped preset and every tenant policy: name, active version, short hash, mode chip, "reversible/irreversible/one-way · N jobs"; a preset the tenant has not adopted shows "v1 · shipped" and selecting it offers **Adopt for this tenant** (`create(name, name)`); **New policy from a preset** (dialog: name + base preset); "On this page" anchors with counts (matrix rows, declared fields, recognizers, plan rows, versions).
- **Policy header card**: name, mode, "vN active", "draft vN+1 · k changes"; description; active hash + **Copy hash**; "Published <date> by <key name> · J jobs use it"; the settings grid (locale, detection tiers, minimum score, consistency, token style, leakage check) of the draft (if any) else the active body; the regulatory callout for the mode.
- **Treatment per entity type** (`TreatmentMatrix`): rows from `/entities` (family, jurisdiction, sensitivity chip) × the body's `treat` (obfuscate method + param chips; `default_sensitive → det_token` explained in the footer) and `synthesize` (method + a hint from a static `SYNTH_HINTS` map, e.g. SSN "area 900–999, never issued", CARD_PAN "test IIN, Luhn-valid", EMAIL "RFC 2606 domains"), reversible yes/no (from the method registry list the API exposes in `/entities` or a static `REVERSIBLE` set mirroring `METHODS`); filter seg All / Critical / Changed in draft (rows whose `treat.<ENTITY>` path appears in `diff(name, active, 'draft')`) and a search box; clicking a method opens an inline editor (method select from `METHOD_NAMES`, params as key/value rows) that writes the draft via `policyEdit.setTreatment(body, entity, spec)` → `saveDraft`. The FF1 domain note from the mockup sits under the table.
- **Declared fields** (`DeclaredFields`): `detect.declared_fields` rows — path, system · owner (from the path's prefix when it names an object, else "API"), declared as (entity chip), treatment (from `treat`), declared by (publisher of the first version containing it), since (`vN` or "draft vN"); **Declare a field** form (path + entity) → draft.
- **Custom recognizer** (`RecognizerBuilder`): name, score, entity select (taxonomy + custom), pattern, context words, deny list; **live test**: the test text runs through `POST /policies/test-recognizer` (M1 route; nothing is stored) debounced 400 ms with an `AbortController` so only the latest request renders; matches are highlighted with `<mark>` elements built from the returned spans (never `innerHTML`), each with score, "context +0.05" and "deny list" markers; the server's `UnsafePattern`/compile error appears under the pattern input ("This pattern does not compile…" / "Nested quantifiers are not allowed…"); **Add recognizer to draft** and **Copy as Presidio YAML** (YAML assembled client-side in the mockup's shape).
- **Plan preview** (`PlanPreview`): a JSON sample editor prefilled with the mockup's synthetic KYC sample (synthetic values only); **Re-run plan** → `POST /policies/plan` with `policy` = the draft body (or active body) — the table shows locator, entity · score, method (+params), reason (`policy:SSN`, `declared:<path>`, `fallback:fpe_disabled`, `external:unconfigured`), reversible; summary line (findings, reversible, fallbacks); the FF1 seg shows "On · this deployment" or "Off · this deployment" from readiness and, when the user picks the other value, recomputes the static FPE fallback client-side (`fpe_digits`/`fpe_text` → `det_token`, reason `…|fallback:fpe_disabled`, the planner's documented rule) and labels the view "simulated"; the two explanation cards (`fallback:fpe_domain_too_small`, `fallback:fpe_disabled`) from the mockup.
- **Version history** (`PolicyVersions`): draft + every version as selectable cards (author key name, date, change ticket, hash, "N jobs pinned"); the selected card shows `diff(name, v-1, v)` (or `active → draft`) rendered as YAML-like lines from `PolicyChange` (`-` removed rose, `+` added emerald, path shown as nested keys) with **Download vN YAML** (`versionYaml`); a note when jobs are pinned to an older version than active.

`lib/policyEdit.ts` (pure functions, unit-testable later): `setTreatment(body, entity, spec)`, `removeTreatment(body, entity)`, `addDeclaredField(body, field)`, `upsertRecognizer(body, rec)`, `toPresidioYaml(rec)` — each returns a new object (no mutation).

- [ ] **Step 1: Implement**; **Step 2:** typecheck + build; **Step 3:** browser check against `05-policies.html` with the seeded `retail-kyc` policy (Task 26): adopt a preset, add the `employee-id` recognizer (live test shows 1 match, 1 denied, 1 five-digit miss as in the mockup text), declare `LOANS.APPLICANTS.EMPLOYER_NAME` as `ORG_NAME`, preview the plan, publish v2, view the v1→v2 diff and download its YAML; **Step 4: Commit**

```bash
git add frontend/src/pages/Policies.tsx frontend/src/components/PolicyList.tsx frontend/src/components/TreatmentMatrix.tsx frontend/src/components/DeclaredFields.tsx frontend/src/components/RecognizerBuilder.tsx frontend/src/components/PlanPreview.tsx frontend/src/components/PolicyVersions.tsx frontend/src/lib/policyEdit.ts
git commit -m "feat(console): Policies to mockup 05 — presets, matrix, recognizer builder with live test, plan preview, versions" -- frontend/src/pages/Policies.tsx frontend/src/components/PolicyList.tsx frontend/src/components/TreatmentMatrix.tsx frontend/src/components/DeclaredFields.tsx frontend/src/components/RecognizerBuilder.tsx frontend/src/components/PlanPreview.tsx frontend/src/components/PolicyVersions.tsx frontend/src/lib/policyEdit.ts
```

---
### Task 24: Images, local stack, smoke-test extension, flow report

**Files:**
- Modify: `Dockerfile`, `Dockerfile.browser` (M4), `docker-compose.yml`, `tools/smoke_test.py`, `.gitignore`, `HANDOFF.md`
- Create: `tools/demo_client.py`, `tools/flow_report.py`

**Images.** `Dockerfile` runtime stage: `ARG EXTRAS=""` and `RUN uv pip install --system -e ".[fpe,docs,db,connectors,tabular${EXTRAS:+,$EXTRAS}]"` (contract §11.1 set + the opt-in, e.g. `--build-arg EXTRAS=synth-ml`); after the `COPY` steps: `RUN chgrp -R 0 /app && chmod -R g=u /app` and `ENV HOME=/tmp` so the image runs under OpenShift's arbitrary UID (group 0) with a read-only root filesystem (writes go to `/tmp`, an `emptyDir`). `Dockerfile.browser` gets the same two lines and `chgrp -R 0 /ms-playwright && chmod -R g=u /ms-playwright`. HANDOFF §2 records both.

**Compose** (contract §11 services unchanged except `tokenizer-mock` from Task 8): `app` and `worker` env gain `DPS_SYNTH_TABULAR_ENGINE: copula`, `DPS_KEY_CACHE_SECONDS: "5"` (fast key propagation for the local demo), `DPS_REPORT_PDF_ENABLED: "true"`; `.gitignore` gains `var/` and `reports/` (demo keys, flow reports, screenshots never get committed).

**`tools/demo_client.py`** (imported by `smoke_test.py`, `flow_report.py`, `demo_seed.py`, `e2e_demo.py` via `sys.path.insert(0, str(Path(__file__).parent))`):
```python
class DemoError(RuntimeError): status: int; code: str | None          # message = method + path template + status
class DpsClient:
    def __init__(self, base_url: str, api_key: str, *, tenant: str | None = None, timeout: float = 60.0) -> None
    def get(self, path: str, **params: Any) -> Any
    def post(self, path: str, body: Any | None = None, *, expect: int | tuple[int, ...] = (200, 201, 202)) -> Any
    def patch(self, path: str, body: Any) -> Any
    def delete(self, path: str) -> Any
    def raw(self, method: str, path: str, **kw: Any) -> httpx.Response
    def download(self, path: str, **params: Any) -> bytes
    def wait_job(self, job_id: str, *, timeout: float = 900, kind: str = "jobs") -> dict[str, Any]   # polls every 2 s
    def as_tenant(self, tenant: str) -> DpsClient
def load_keys(path: Path) -> dict[str, str]                             # var/demo/demo-keys.json, refuses world-readable files
```
Errors carry the route template and status only — never a request or response body.

**Smoke test** — append an "M5" block to `tools/smoke_test.py` (8 checks; the M0–M4 checks stay first):
```python
def m5_checks(c: httpx.Client, h: dict[str, str]) -> None:
    pol = c.get("/api/v1/policies", headers=h)
    check("policies with hashes", pol.status_code == 200 and all(len(p["hash"]) == 64 for p in pol.json()["presets"]))
    ready = c.get("/readyz").json()["components"]
    if ready.get("db", {}).get("ok"):
        summ = c.get("/api/v1/reports/summary", params={"days": 14}, headers=h)
        check("reports summary", summ.status_code == 200 and summ.json()["tiles"]["raw_pii_sent_to_llm"] == 0)
        post = c.get("/api/v1/admin/posture", headers=h)
        check("posture report", post.status_code == 200 and post.json()["total"] == 11)
        made = c.post("/api/v1/admin/keys", headers=h, json={"name": "smoke-temp", "tenant_ids": ["default"],
                                                              "scopes": ["detect"], "expires_in_days": 1})
        key = made.json()["key"] if made.status_code == 201 else ""
        time.sleep(6)  # DPS_KEY_CACHE_SECONDS=5 locally
        works = c.post("/api/v1/detect", json={"text": "x"}, headers={"X-API-KEY": key}).status_code == 200
        c.post(f"/api/v1/admin/keys/{made.json()['record']['id']}/disable", headers=h)
        check("db key lifecycle", made.status_code == 201 and works and made.headers.get("cache-control") == "no-store")
    if ready.get("tokenizer", {}).get("ok"):
        policy = {"preset": "gdpr-pseudonymise", "treat": {"SSN": {"method": "external_tokenize", "profile": "ssn"}}}
        out = c.post("/api/v1/obfuscate", headers=h, json={"data": {"ssn": "123-45-6789"}, "policy": policy}).json()
        back = c.post("/api/v1/reveal", headers=h, json={"data": out["result"], "key_material": out["key_material"],
                                                          "reveal_hints": out["reveal_hints"]}).json()
        check("external tokenization round trip", out["result"]["ssn"].startswith("tk_") and back["result"] == {"ssn": "123-45-6789"})
    bad = c.post("/api/v1/detect", headers=h, json={"text": 123456789, "options": {"locale": ["123-45-6789"]}})
    check("422 never echoes input", bad.status_code == 422 and "123-45-6789" not in bad.text and "123456789" not in bad.text)
    check("api responses are not cacheable", c.get("/api/v1/policies/presets", headers=h).headers.get("cache-control") == "no-store")
```
(`check` is the existing helper; skipped checks print `SKIP` lines and do not fail.)

**`tools/flow_report.py`** — exercises every §9 route group once against a running stack and writes `reports/flow-report.md`: for each call the method, the **route template**, status, duration, and the response's top-level key names (never values). It reuses ids it creates (a connection, a discover job and snapshot, a run job and its evidence, a policy, a key; a scan when `/readyz` reports `browser`), runs with the demo keys (`var/demo/demo-keys.json`) or `DPS_API_KEY`, and exits 1 when any call's status differs from the expected one in its table (`EXPECT: list[tuple[str, str, int]]`).

- [ ] **Step 1: Implement**; **Step 2:** `docker compose --profile full up --build -d`, poll `/readyz` until ready (never `--wait`, contract §11); `python tools/smoke_test.py` → all checks pass (`all N checks passed`); `python tools/flow_report.py` → exit 0, report written; **Step 3:** `ruff check tools && mypy dps`; **Step 4: Commit**

```bash
git add Dockerfile Dockerfile.browser docker-compose.yml .gitignore tools/smoke_test.py tools/demo_client.py tools/flow_report.py HANDOFF.md
git commit -m "feat: arbitrary-UID images, M5 compose settings, smoke-test M5 checks, value-free flow report" -- Dockerfile Dockerfile.browser docker-compose.yml .gitignore tools/smoke_test.py tools/demo_client.py tools/flow_report.py HANDOFF.md
```

---

### Task 25: OpenShift manifests — kustomize base, components, dev and prod overlays

**Files:**
- Create: `deploy/openshift/README.md`, `deploy/openshift/base/{kustomization.yaml,configmap-dps.env,serviceaccounts.yaml,deployment-app.yaml,deployment-worker.yaml,deployment-browser-worker.yaml,job-migrate.yaml,service-app.yaml,service-dps-db.yaml,route-app.yaml,pdb.yaml}`, `deploy/openshift/components/network-policies/{kustomization.yaml,networkpolicies.yaml}`, `deploy/openshift/components/monitoring/{kustomization.yaml,podmonitor.yaml,prometheusrule.yaml}`, `deploy/openshift/components/dev-mocks/{kustomization.yaml,tokenizer-mock.yaml,azure-ocr-mock.yaml,server.py}`, `deploy/openshift/overlays/{dev,prod}/{kustomization.yaml,patch-replicas.yaml}`, `tests/deploy/__init__.py`, `tests/deploy/test_openshift.py`
- Modify: `HANDOFF.md` (§7), contract §11.1 (one line: "OpenShift: `deploy/openshift`, Wega layout; roles app, worker, browser-worker, migrate Job; `dps-db` is an ExternalName Service to the platform PostgreSQL")

Layout (Wega pattern, `disableNameSuffixHash: true`, placeholders that the release overlay replaces — zero digests, `.invalid` hosts, `192.0.2.0/24` CIDRs — all listed in `deploy/openshift/README.md`):

`base/kustomization.yaml`:
```yaml
# Base: everything identical in every environment. One image, four roles (spec §4): app, worker,
# browser-worker, migrate. The metadata database is external (dps-db ExternalName); Oracle Free and
# other source-system mocks are never deployed here.
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - serviceaccounts.yaml
  - deployment-app.yaml
  - deployment-worker.yaml
  - deployment-browser-worker.yaml
  - job-migrate.yaml
  - service-app.yaml
  - service-dps-db.yaml
  - route-app.yaml
  - pdb.yaml
generatorOptions:
  disableNameSuffixHash: true
configMapGenerator:
  - name: dps
    envs: [configmap-dps.env]
labels:
  - includeSelectors: false
    pairs:
      app.kubernetes.io/part-of: data-privacy-services
```
`base/configmap-dps.env` (non-secret settings; every secret is a `_FILE` path set in the deployments):
```
DPS_ENV=prod
DPS_LOG_LEVEL=INFO
DPS_AUTH_ENABLED=true
DPS_PG_HOST=dps-db
DPS_PG_PORT=5432
DPS_PG_DATABASE=dps
DPS_PG_SCHEMA=dps
DPS_PG_USER=dps_app
DPS_PG_WORKER_USER=dps_worker_login
DPS_PG_MIGRATION_USER=dps_owner
DPS_PG_SSL_ROOT_CERT=/etc/dps/ca/pg-root.pem
DPS_SSL_CERT_FILE=/etc/dps/ca/org-ca.pem
DPS_MIGRATIONS_MODE=verify
DPS_RLS_ENABLED=true
DPS_AUDIT_ENABLED=true
DPS_AUDIT_STRICT=true
DPS_INLINE_SECRETS_ALLOWED=false
DPS_EGRESS_TLS_REQUIRED=true
DPS_CORS_ORIGINS=
DPS_EMBEDDED_WORKER=false
DPS_ARTEFACT_STORE=s3
DPS_S3_ENDPOINT_URL=https://s3.example.invalid
DPS_S3_REGION=ap-south-1
DPS_S3_BUCKET=dps-artefacts
DPS_SECRET_DIR=/var/run/secrets/dps/refs
DPS_EXPORT_ROOT=/mnt/dps-exports
DPS_LLM_PROVIDER=stellar
DPS_LLM_MODEL=gemini-2.5-flash
DPS_STELLAR_BASE_URL=https://stellar.example.invalid
DPS_COIN_URL=https://coin.example.invalid/oauth2/token
DPS_OCR_PROVIDER=azure
DPS_AZURE_VISION_ENDPOINT=https://ocr-gateway.example.invalid
DPS_TOKENIZER_PROVIDER=http
DPS_TOKENIZER_URL=https://tokenizer.example.invalid
DPS_SYNTH_TABULAR_ENGINE=copula
DPS_FPE_ENABLED=false
DPS_SCAN_STORE_UNMASKED_ORIGINALS=false
DPS_RETENTION_DAYS=30
DPS_KEY_CACHE_SECONDS=30
DPS_REPORT_PDF_ENABLED=true
```
`base/deployment-app.yaml` (the worker and browser-worker files follow it exactly, differing only where stated; all three carry `replicas: 2` in base so the overlays' JSON6902 `test` ops hold):
```yaml
# dps-app: API + console. Stateless for Layer 1; reads the metadata DB for jobs, keys, reports.
apiVersion: apps/v1
kind: Deployment
metadata:
  name: dps-app
  labels: {app.kubernetes.io/name: data-privacy-services, app.kubernetes.io/component: app}
spec:
  replicas: 2
  strategy: {type: RollingUpdate, rollingUpdate: {maxUnavailable: 0, maxSurge: 1}}
  selector:
    matchLabels: {app.kubernetes.io/component: app}
  template:
    metadata:
      labels: {app.kubernetes.io/name: data-privacy-services, app.kubernetes.io/component: app}
    spec:
      serviceAccountName: dps-app
      automountServiceAccountToken: false
      enableServiceLinks: false
      terminationGracePeriodSeconds: 30
      securityContext:
        runAsNonRoot: true
        seccompProfile: {type: RuntimeDefault}
      containers:
        - name: app
          image: data-privacy-services
          args: ["uvicorn", "dps.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8400", "--proxy-headers"]
          ports: [{name: http, containerPort: 8400, protocol: TCP}]
          envFrom: [{configMapRef: {name: dps}}]
          env:
            - {name: DPS_PG_PASSWORD_FILE, value: /var/run/secrets/dps/pg/password}
            - {name: DPS_JOB_KEK_FILE, value: /var/run/secrets/dps/kek/kek}
            - {name: DPS_API_KEYS_FILE, value: /var/run/secrets/dps/api-keys/api_keys.json}
            - {name: DPS_COIN_CLIENT_ID_FILE, value: /var/run/secrets/dps/coin/client_id}
            - {name: DPS_COIN_CLIENT_SECRET_FILE, value: /var/run/secrets/dps/coin/client_secret}
            - {name: DPS_COIN_SCOPE_FILE, value: /var/run/secrets/dps/coin/scope}
            - {name: DPS_TOKENIZER_TOKEN_FILE, value: /var/run/secrets/dps/tokenizer/token}
            - {name: DPS_AZURE_VISION_KEY_FILE, value: /var/run/secrets/dps/ocr/key}
            - {name: DPS_S3_ACCESS_KEY_ID_FILE, value: /var/run/secrets/dps/s3/access_key_id}
            - {name: DPS_S3_SECRET_ACCESS_KEY_FILE, value: /var/run/secrets/dps/s3/secret_access_key}
          startupProbe: {httpGet: {path: /health, port: http}, periodSeconds: 5, failureThreshold: 30}
          readinessProbe: {httpGet: {path: /readyz, port: http}, periodSeconds: 10}
          livenessProbe: {httpGet: {path: /health, port: http}, periodSeconds: 10, failureThreshold: 6}
          resources:
            requests: {cpu: 500m, memory: 768Mi}
            limits: {cpu: "2", memory: 2Gi}
          securityContext:
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            capabilities: {drop: [ALL]}
          volumeMounts:
            - {name: tmp, mountPath: /tmp}
            - {name: ca, mountPath: /etc/dps/ca, readOnly: true}
            - {name: pg, mountPath: /var/run/secrets/dps/pg, readOnly: true}
            - {name: kek, mountPath: /var/run/secrets/dps/kek, readOnly: true}
            - {name: api-keys, mountPath: /var/run/secrets/dps/api-keys, readOnly: true}
            - {name: coin, mountPath: /var/run/secrets/dps/coin, readOnly: true}
            - {name: tokenizer, mountPath: /var/run/secrets/dps/tokenizer, readOnly: true}
            - {name: ocr, mountPath: /var/run/secrets/dps/ocr, readOnly: true}
            - {name: s3, mountPath: /var/run/secrets/dps/s3, readOnly: true}
            - {name: refs, mountPath: /var/run/secrets/dps/refs, readOnly: true}
      volumes:
        - {name: tmp, emptyDir: {sizeLimit: 1Gi}}
        - {name: ca, configMap: {name: dps-ca-bundle}}
        - {name: pg, secret: {secretName: dps-pg-app, defaultMode: 0440}}
        - {name: kek, secret: {secretName: dps-job-kek, defaultMode: 0440}}
        - {name: api-keys, secret: {secretName: dps-api-keys, defaultMode: 0440, optional: true}}
        - {name: coin, secret: {secretName: dps-coin, defaultMode: 0440}}
        - {name: tokenizer, secret: {secretName: dps-tokenizer, defaultMode: 0440, optional: true}}
        - {name: ocr, secret: {secretName: dps-ocr, defaultMode: 0440}}
        - {name: s3, secret: {secretName: dps-s3, defaultMode: 0440}}
        - {name: refs, secret: {secretName: dps-connector-refs, defaultMode: 0440, optional: true}}
```
Differences: **dps-worker** — `args: ["python", "-m", "dps.worker"]`, port `ops: 9400` (probes on it), env `DPS_PG_WORKER_PASSWORD_FILE` (secret `dps-pg-worker`) instead of the app password, no `DPS_API_KEYS_FILE`/COIN mounts are needed for jobs but the LLM and OCR mounts stay (engine), resources `requests {cpu: "1", memory: 2Gi}`, `limits {cpu: "4", memory: 4Gi}` (copula fit of 100k rows + transform batches), an `exports` PVC mount at `/mnt/dps-exports` (`persistentVolumeClaim: {claimName: dps-exports}`, created by the platform; README lists it), `terminationGracePeriodSeconds: 60` (drain). **dps-browser-worker** — image `data-privacy-services-browser`, `args: ["python", "-m", "dps.browser_worker"]`, port `ops: 9401`, worker DB password, `requests {cpu: "1", memory: 2Gi}`, `limits {cpu: "2", memory: 3Gi}`, extra volume `dshm` (`emptyDir: {medium: Memory, sizeLimit: 512Mi}`) at `/dev/shm`, no LLM/OCR/tokenizer mounts.

`base/job-migrate.yaml` — `dps-migrate-0` (the release overlay renames it per release, as Wega), `restartPolicy: Never`, `backoffLimit: 6`, `args: ["python", "-m", "dps.migrate"]`, env `DPS_PG_MIGRATION_PASSWORD_FILE` from secret `dps-pg-owner` (the only workload that mounts it), `DPS_MIGRATIONS_MODE` overridden to `auto` in the container env, same security context, `resources {requests: {cpu: 100m, memory: 256Mi}, limits: {cpu: 500m, memory: 512Mi}}`.
`base/service-dps-db.yaml`:
```yaml
# The platform PostgreSQL 16 instance (never deployed by DPS). Release overlay sets externalName.
apiVersion: v1
kind: Service
metadata: {name: dps-db}
spec:
  type: ExternalName
  externalName: postgres.example.invalid
  ports: [{name: postgres, port: 5432}]
```
`base/service-app.yaml` (ClusterIP 8400 → `http`), `base/route-app.yaml` (`tls: {termination: edge, insecureEdgeTerminationPolicy: Redirect}`, host `dps.apps.example.invalid`), `base/pdb.yaml` (`dps-app` minAvailable 1, `dps-worker` maxUnavailable 1), `base/serviceaccounts.yaml` (`dps-app`, `dps-worker`, `dps-browser-worker`, `dps-migrate`, each `automountServiceAccountToken: false`).

`components/network-policies/networkpolicies.yaml` — `default-deny` (empty `podSelector`, `policyTypes: [Ingress, Egress]`); `allow-dns` (egress to namespace `openshift-dns`, UDP/TCP 53 and 5353); `app-ingress-from-router` (to component `app`, from namespace label `network.openshift.io/policy-group: ingress`, TCP 8400); `metrics-from-monitoring` (to all three components from `openshift-user-workload-monitoring`, TCP 8400/9400/9401); `egress-dps-db` (all components, `ipBlock 192.0.2.10/32` TCP 5432 — parameter); `egress-gateways` (app + worker: LLM gateway, OCR gateway, tokenizer, S3 — `ipBlock 192.0.2.0/27` TCP 443, parameter per endpoint); `egress-source-databases` (worker only: `192.0.2.32/27` TCP 1521, 2484, 5432, 27017 — parameter); `egress-scan-targets` (browser-worker only: `192.0.2.64/27` TCP 443 — the registered UAT hosts' network, parameter).
`components/monitoring/` — `PodMonitor` for the three components (ports `http`/`ops`, path `/metrics`); `PrometheusRule` with alerts `DpsRawPiiSentToLlm` (`dps_llm_raw_pii_sent_total > 0`, critical — must never fire), `DpsVerificationFailures` (`increase(dps_verification_failures_total[15m]) > 0`, warning), `DpsExactRowResamplesHigh` (`increase(dps_tabular_resamples_total[1h]) > 1000`, info), `DpsTokenizerFailing` (`increase(dps_external_token_calls_total{outcome!="ok"}[10m]) > 10`, warning), `DpsNoDbKeysLoaded` (`dps_auth_keys_loaded == 0` for 15 m, warning), `DpsWorkersAbsent` (`absent(up{app_kubernetes_io_component="worker"} == 1)` for 10 m, critical).
`components/dev-mocks/` — `dps-tokenizer-mock` Deployment + Service (app image, `args: ["python", "/mock/server.py", "--port", "8420"]`, the server file from a `configMapGenerator` over `server.py` — a copy of `docker/tokenizer-mock/server.py` kept identical by a test — mounted at `/mock`; `MOCK_TOKENIZER_TOKEN`/`_KEY` from secret `dps-tokenizer-mock`) and `dps-azure-ocr-mock` (image `azure-ocr-mock`, port 5000). No Oracle, Mongo, MinIO or source PostgreSQL — those are bank systems.

`overlays/dev/kustomization.yaml`:
```yaml
# dev: one replica each, the two dev mocks, TLS egress relaxed for the in-namespace tokenizer mock.
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
namespace: dps-dev
resources: [../../base]
components:
  - ../../components/network-policies
  - ../../components/monitoring
  - ../../components/dev-mocks
images:
  - {name: data-privacy-services, newName: image-registry.openshift-image-registry.svc:5000/dps-dev/data-privacy-services, digest: "sha256:0000000000000000000000000000000000000000000000000000000000000000"}
  - {name: data-privacy-services-browser, newName: image-registry.openshift-image-registry.svc:5000/dps-dev/data-privacy-services-browser, digest: "sha256:0000000000000000000000000000000000000000000000000000000000000000"}
  - {name: azure-ocr-mock, newName: image-registry.openshift-image-registry.svc:5000/dps-dev/azure-ocr-mock, digest: "sha256:0000000000000000000000000000000000000000000000000000000000000000"}
generatorOptions:
  disableNameSuffixHash: true
configMapGenerator:
  - name: dps
    behavior: merge
    literals:
      - DPS_ENV=dev
      - DPS_LLM_PROVIDER=stub
      - DPS_TOKENIZER_URL=http://dps-tokenizer-mock:8420
      - DPS_AZURE_VISION_ENDPOINT=http://dps-azure-ocr-mock:5000
      - DPS_EGRESS_TLS_REQUIRED=false
patches:
  - path: patch-replicas.yaml
    target: {kind: Deployment, name: "dps-(app|worker|browser-worker)"}
```
(`patch-replicas.yaml`: JSON6902 `[{op: test, path: /spec/replicas, value: 2}, {op: replace, path: /spec/replicas, value: 1}]` for app; the worker deployments carry `replicas: 2` in base for the test to hold — the Wega "test before replace" rule.)
`overlays/prod/kustomization.yaml` — `namespace: dps-prod`, base + `network-policies` + `monitoring` (no mocks), the two DPS images by zero digest, `configMapGenerator: dps behavior: merge literals: [DPS_ENV=prod]`, `patch-replicas.yaml` setting app 3, worker 2, browser-worker 1.

`deploy/openshift/README.md` — deploy steps (`oc new-project`, create the secrets by name with `oc create secret generic … --from-file=…`, the CA ConfigMap, the `dps-exports` PVC, a release overlay over `overlays/prod` that sets every parameter, `kustomize build | oc apply -f -`, wait for `dps-migrate-<release>` to complete, `oc rollout status`, run `python tools/smoke_test.py` through the route with a bootstrap key from `dps.keys_cli`) and the **parameter table**: every zero digest, every `.invalid` host, every `192.0.2.x` CIDR, `dps-db` `externalName`, the route host — with where each is set.

- [ ] **Step 1: Write the failing tests** — `tests/deploy/test_openshift.py` (`pytestmark = pytest.mark.deploy`; skips when `shutil.which("kustomize")` is `None`; renders with `subprocess.run(["kustomize", "build", path])` once per overlay into `yaml.safe_load_all` documents; helpers `pods(docs)` → `(name, pod_spec)` for Deployments and Jobs):
- `test_overlays_render_every_role[dev|prod]`: Deployments `{dps-app, dps-worker, dps-browser-worker}`, Job `dps-migrate-0`, Services `dps-app` and `dps-db` (`type: ExternalName`), Route `dps-app` with edge TLS, four ServiceAccounts, ConfigMap `dps`, PodMonitor, PrometheusRule, a `default-deny` NetworkPolicy.
- `test_pod_security_everywhere[dev|prod]`: every pod: `runAsNonRoot: true`, `seccompProfile.type == "RuntimeDefault"`, no `runAsUser`, `automountServiceAccountToken: false`, no `hostPath`/`hostNetwork`/`hostPID`; every container: `allowPrivilegeEscalation: false`, `readOnlyRootFilesystem: true`, `capabilities.drop == ["ALL"]`, cpu+memory requests and limits; every Deployment container has liveness and readiness probes.
- `test_images_are_pinned_by_digest[dev|prod]`: every container image contains `@sha256:`.
- `test_no_literal_secrets_and_every_secret_is_a_mounted_file[dev|prod]`: no env entry with a `value` whose name matches `(KEY|SECRET|PASSWORD|TOKEN|KEK)$`; every `*_FILE` value lies under a `volumeMount.mountPath` of that container whose volume is a Secret or ConfigMap.
- `test_prod_configuration_passes_the_posture_guard`: from the prod `dps-app` container, merge the ConfigMap `dps` data and the container env; for each `*_FILE` var write a temp file with a valid dummy (32-byte base64 for the KEK, `[{"name":"ops","key_sha256":"<64 hex>"}]` for API keys, `"x"` otherwise) and point the var at it; `posture_problems(Settings(_env_file=None))` (with `monkeypatch.setenv` for every var) `== []`.
- `test_prod_has_no_mocks_and_no_source_databases`: no resource name in prod matches `mock|oracle|mongo|minio|source-pg`; dev contains `dps-tokenizer-mock` and still no `oracle`.
- `test_only_the_migrate_job_mounts_the_owner_secret`: `dps-pg-owner` appears only in `dps-migrate-0`, whose args are `["python", "-m", "dps.migrate"]` and whose env sets `DPS_MIGRATIONS_MODE=auto`.
- `test_placeholders_are_documented`: every `.invalid` host, every `sha256:000…` image name and every `192.0.2.` CIDR in the rendered prod output appears in `deploy/openshift/README.md`.
- `test_dev_mock_server_matches_the_compose_mock`: `deploy/openshift/components/dev-mocks/server.py` is byte-identical to `docker/tokenizer-mock/server.py` (kustomize cannot load files outside a kustomization root, so the component carries a copy; this test keeps the two from drifting).

- [ ] **Step 2: Run to confirm failure** → the kustomize build fails (no `deploy/openshift`).
- [ ] **Step 3: Write the manifests**; HANDOFF §7 (OpenShift) summarises the README.
- [ ] **Step 4: Run**: `pytest -q -m deploy tests/deploy/test_openshift.py` → `13 passed`; optional (needs network for schemas): `kustomize build deploy/openshift/overlays/prod | kubeconform -strict -summary -ignore-missing-schemas -` → `0 errors`.
- [ ] **Step 5: Commit**

```bash
git add deploy/openshift tests/deploy HANDOFF.md docs/superpowers/plans/2026-10-11-m1-m5-contract.md
git commit -m "feat(deploy): OpenShift kustomize base, network policies, monitoring, dev and prod overlays for every role" -- deploy/openshift tests/deploy HANDOFF.md docs/superpowers/plans/2026-10-11-m1-m5-contract.md
```

---

### Task 26: The ten-minute demo — seed, automated end-to-end run, script, console check

**Files:**
- Create: `tools/demo_seed.py`, `tools/e2e_demo.py`, `docs/demo/DEMO-SCRIPT.md`, `tests/console/__init__.py`, `tests/console/test_console_pages.py`

**`tools/demo_seed.py`** — idempotent; `--base-url` (default `http://localhost:8400`), `--admin-key` (default `DPS_API_KEY`, else the local bootstrap literal), `--tenant retail-banking-apac`, `--rows 2000`, `--skip-sources`, `--skip-oracle`:
1. Wait for `/readyz` (≤ 300 s; Oracle Free needs ~3 min on first start).
2. Tenant `retail-banking-apac` (create or patch): display name "Retail Banking Technology", `daily_row_quota` 50,000,000, `retention_days` 30, `registered_hosts` `["demo-site", "localhost", "127.0.0.1"]`.
3. Keys (via `/admin/keys`, reused when `var/demo/demo-keys.json` holds keys that still authenticate): `demo-console` (tenant, `*`), `crm-uat-provisioner` (detect, obfuscate, jobs), `qa-onboarding-scanner` (scan, synthesize, obfuscate). Raw keys go only to `var/demo/demo-keys.json` (created `0600`; gitignored); stdout prints names and prefixes.
4. Source data: `python tools/seed_sources.py --target postgres|oracle|mongo --rows N --seed 7` (M2/M3 tool; idempotent marker) unless skipped.
5. Connections (by name, created if missing; local demo literals from contract §11): `bank-pg` (`source-pg:5432/bank`, roles source+target), `crm-oracle` (`oracle:1521/FREEPDB1`, user `dps_ro` for reads + `crm_masked` target owner, roles source+target), `profiles-mongo` (`mongo:27017/?directConnection=true`, database `crm`, roles source+target), `exports-minio` (s3, `http://minio:9000`, bucket `dps-exports`, role target).
6. Policies: adopt `gdpr-pseudonymise`; create `retail-kyc` from it, save a draft adding the `employee-id` recognizer (`\bEMP-\d{6}\b`, score 0.9, context `employee, staff id`, deny `EMP-000000`) and the declared field `LOANS.APPLICANTS.EMPLOYER_NAME → ORG_NAME`, publish v2 (`CHG-48213`) — so the Policies page has a version history.
7. Job group `crm-uat-refresh` (create if missing): its key material and seed — returned once — are written to `var/demo/group-crm-uat-refresh.json` (`0600`), the only place DPS's demo keeps them, because `e2e_demo.py` reuses them for the three-way join.
8. Print a value-free summary (ids, names, counts).

**`tools/e2e_demo.py`** — runs the demo story of spec §9 against the stack and asserts every promise; `--console` adds the Playwright walk; `--skip-scanner`, `--skip-oracle`; writes `reports/e2e-demo-report.md` (step, PASS/FAIL, duration, ids and counts only) and exits non-zero on the first failed step (later steps are reported as `NOT RUN`). Steps and their assertions:

| # | Demo beat (spec §9) | API calls | Asserts |
|---|---|---|---|
| S1 | Overview | `/readyz`, `/reports/summary?days=14` | ready; `tiles.raw_pii_sent_to_llm == 0` |
| S2 | Playground: KYC record → findings → obfuscate → reveal → synthetic | `/detect`, `/obfuscate` (`@retail-kyc`), `/reveal`, `/synthesize` | ≥ 8 findings incl. AADHAAR, PAN_IN, EMAIL; reveal == input; synthetic output contains no input value; `report.llm.raw_pii_sent == 0` |
| S3 | Bank tokenization (when `tokenizer` ready) | `/obfuscate` with `SSN → external_tokenize` | `tk_` token; reveal round trip |
| S4 | Scanned PDF → redacted PDF | `/documents/obfuscate` (`redacted_pdf: true`, `ocr: always`) with a PDF rendered from text to an image (reportlab + Pillow, synthetic content) | `document.ocr_pages ≥ 1`; `redacted_pdf_b64` decodes to `%PDF`; its extracted text lacks the sentinel name |
| S5 | Data sources: discover Oracle CRM, Mongo profiles, PG customers; review; accept | `/jobs/discover` ×3, `wait_job`, `/snapshots/{id}`, `/snapshots/{id}/accept` | each snapshot classifies ≥ 5 sensitive columns; heat-map data present (`value_hit_rate`) |
| S6 | Run with the group key; watch partitions; evidence | `/jobs/run` ×3 (`group_id`, `consistency.key_material` from the group file), `/jobs/{id}/partitions`, `/evidence?job_id=` | all `succeeded`; `partitions_done == partitions_total`; `verification.passed`; pack `label` pseudonymised |
| S7 | Three-way consistency join | `/job-groups/{id}/consistency-check` (customer id path per job) | overlap > 0 for every pair |
| S8 | Synthetic companion (copula) | `/jobs/run` synthesize PG customers → `synth_demo` schema, `options.tabular = "copula"` | `fidelity.score ≥ 0.85`, `privacy.exact_row_matches == 0`, `privacy.nndr.measured == 1` |
| S9 | UI scanner: scan onboarding, parallel view, flip, export | `/scans` (synthesize, 3 personas) on `http://demo-site:8410/apply`, `wait_job(kind="scans")`, `/scans/{id}/pages`, `/scans/{id}/export?format=csv`, demo-site `GET /__received` | pages ≥ 2; a page with both `filled_synthesize` and `filled_obfuscate` artefacts; export CSV has a header; **POSTs received == 0** |
| S10 | Reports: evidence PDF, audit trail | `/reports/jobs`, `/reports/evidence`, `/evidence/{id}/pdf`, `/admin/audit?q=<job id>` | the S6/S8/S9 packs listed; PDF starts `%PDF`; audit rows for the jobs; no input value in any of these bodies |
| S11 | Admin | `/admin/posture`, `/admin/keys` create → rotate (grace 0) → disable, `/admin/retention/run` (dry run) | 11 checks; old key 401 after rotation; retention job `succeeded` |
| S12 | `--console` | Playwright (Python, `playwright==1.55.0`, cached Chromium): `add_init_script` sets `localStorage['dps.apiKey']` / `['dps.tenant']`, then `/`, `/playground`, `/policies/retail-kyc`, `/data-sources`, `/data-sources/jobs/<S6 id>`, `/scanner/scans/<S9 id>?tab=parallel`, `/reports`, `/reports/evidence/<S8 pack>`, `/admin` in light and dark | each page renders its mockup's main heading; no console errors; screenshots to `reports/demo-screens/<page>-<theme>.png` |

The report's last line is the total wall time ("demo flow completed in m:ss") — the rehearsal target is under 10 minutes for the API path.

**`docs/demo/DEMO-SCRIPT.md`** — the presenter's script (full text in the file; outline and the lines that matter):
- **Before the room (T−30 min):** `docker compose --profile full up --build -d`; wait for `/readyz`; `python tools/demo_seed.py`; `python tools/e2e_demo.py --console` (dress rehearsal, must end PASS); open the console, paste the `demo-console` key from `var/demo/demo-keys.json`, set tenant `retail-banking-apac`; light theme for projectors; close other tabs. Fallback if Oracle is slow: `--skip-oracle` and present the PG + Mongo join.
- **0:00 Overview (30 s)** — "One engine, three entry points, two outcomes. This tile is the promise: raw PII sent to an LLM — zero, measured, not claimed."
- **0:30 Playground (2:30)** — paste the KYC sample; point at the entity chips and detector evidence; **Obfuscate** → formats kept (Aadhaar keeps last 4, email keeps the domain); copy the key card ("the bank keeps this key; DPS stored nothing"); **Reveal** → identical; switch to **Synthetic** → a coherent fake person; drop the scanned KYC PDF → findings per page → download the redacted PDF ("true redaction: the text is gone, not hidden").
- **3:00 Policies (1:00)** — `retail-kyc`: the matrix, the employee-id recognizer live test, the v1→v2 diff with its change ticket ("every response and evidence pack names the policy hash it ran with").
- **4:00 Data sources (3:00)** — the wizard: Oracle CRM → discover → heat-map review (accept one override) → target UAT schema → run; watch partitions; open the evidence pack; run the **three-way join** across Oracle, Mongo and PostgreSQL ("same key, same tokens, three systems — no lookup table"); open the synthetic companion pack: fidelity 0.9x, exact-row matches 0, NNDR ≥ target ("measured, not claimed anonymous").
- **7:00 UI scanner (2:00)** — the onboarding URL → crawl map → annotated form → **parallel view** with a synthetic persona filled in → flip to obfuscated → export CSV; point at "POSTs left: 0" ("there is no submit button in DPS, and the browser blocks every POST").
- **9:00 Reports & Admin (1:00)** — the evidence PDF, the audit trail filtered by the job, the posture checks ("staging and production refuse to boot when these fail").
- **Q&A crib** — FF1 licensing (D8: AES-SIV by default, FF1 behind a licence review, the bank's tokenizer via `external_tokenize`); key custody (D2); GDPR/DPDP wording (pseudonymised data is still personal data); the LLM boundary (placeholders only, verifier, zero counter); residency (no cross-region calls); OpenShift (four roles, one image, external DB).
- **If something fails** — a table of symptom → recovery (e.g. a job stuck `waiting` → `docker compose restart worker`; scanner timeout → re-run with 1 persona; PDF render slow → use the HTML evidence page and print).
- **Reset between rehearsals** — `python tools/demo_seed.py --reset-jobs` (cancels and lets retention remove demo jobs: `POST /admin/retention/run` after setting the tenant retention to 1 day, then back to 30), or `docker compose down -v` for a clean slate.

`demo_seed.py` therefore also takes `--reset-jobs`: patch the tenant's `retention_days` to 1, re-stamp, `POST /admin/retention/run`, wait, patch back to 30.

**`tests/console/test_console_pages.py`** (`@pytest.mark.browser`, `@pytest.mark.integration`; needs `DPS_TEST_APP_URL` and `var/demo/demo-keys.json`, else skip): parametrised over the nine console routes of S12 → page loads, the expected heading (`Protection posture`, `Playground`, `Policies`, `Data sources & jobs`, the job id, the scan id, `Evidence and audit`, `evidence pack`, `Admin`) is visible within 15 s, `page.on("console")` collected no `error` entries, and no request to a non-`self` origin was made (CSP sanity).

- [ ] **Step 1: Implement** the two tools, the script and the console test.
- [ ] **Step 2: Run** against the full stack: `python tools/demo_seed.py` → summary printed, files under `var/demo/` with mode `600`; `python tools/e2e_demo.py --console` → all steps PASS, report and screenshots written, total under 10 minutes; `DPS_TEST_REQUIRE_SERVICES=1 pytest -q -m browser tests/console` → `9 passed`.
- [ ] **Step 3: Commit**

```bash
git add tools/demo_seed.py tools/e2e_demo.py docs/demo/DEMO-SCRIPT.md tests/console
git commit -m "feat(demo): idempotent demo seed, automated ten-minute demo with console screenshots, presenter script" -- tools/demo_seed.py tools/e2e_demo.py docs/demo/DEMO-SCRIPT.md tests/console
```

---
### Task 27: HANDOFF complete, README, API reference, changelog

**Files:**
- Create: `tools/settings_table.py`, `docs/technical/api-m5.md`, `CHANGELOG.md`, `tests/test_handoff.py`
- Modify: `HANDOFF.md` (rewritten to its final shape), `README.md`

`tools/settings_table.py` renders the configuration table between `<!-- settings-table:start -->` and `<!-- settings-table:end -->` in `HANDOFF.md` from `Settings.model_fields`: one row per field — env var `DPS_<NAME>`, default (`—` for secret fields and empty strings), `secret (_FILE)` marker for the secret fields (the `SECRET_ENV` list of Task 16 plus `DPS_BOOTSTRAP_API_KEY`), milestone, and the description from `FIELD_DOCS: dict[str, tuple[str, str]]` (field → (milestone, one-line description)) defined in the tool for **every** field. `python tools/settings_table.py` rewrites the block; `--check` exits 1 when the block is stale.

`HANDOFF.md` final sections (the Wega rule: build/deploy facts only, current, verified):
1. **What ships** — images `data-privacy-services` (app, worker, migrate roles; port 8400, ops 9400) and `data-privacy-services-browser` (browser-worker; ops 9401); non-root, arbitrary-UID safe; extras baked in (`fpe, docs, db, connectors, tabular`; browser adds `scanner`), opt-in `--build-arg EXTRAS=synth-ml`.
2. **Build** — `docker build --platform linux/amd64 -t <registry>/data-privacy-services:<semver> .` and the browser image; tags semver + git sha; never `latest` alone.
3. **Configuration** — the generated table (every `DPS_*` setting).
4. **Local stack** — compose profiles (`sources`, `oracle`, `docs`, `scanner`, `tokenizer`, `full`), services and `127.0.0.1` ports from contract §11 plus `tokenizer-mock 8420`, demo credentials (local only), `docker compose --profile full up --build -d` then poll `/readyz` (never `--wait`), `tools/demo_seed.py`.
5. **Secrets (names only)** — Kubernetes secrets `dps-pg-app`, `dps-pg-worker`, `dps-pg-owner`, `dps-job-kek`, `dps-api-keys`, `dps-coin`, `dps-tokenizer`, `dps-ocr`, `dps-s3`, `dps-connector-refs`, ConfigMap `dps-ca-bundle`; the env `_FILE` each one feeds; bootstrap of the first admin key with `python -m dps.keys_cli create ...`.
6. **Services and dependencies** — metadata PostgreSQL 16 (external, roles from contract §2.3, `python -m dps.migrate` as `dps_owner`), S3, Stellar gateway, OCR gateway, the bank tokenization service, source databases; which role talks to which.
7. **OpenShift** — the `deploy/openshift/README.md` steps and the parameter table, the migrate Job per release, rollout order (migrate → workers → app).
8. **Tokenization service contract** — the wire contract of Task 8 for the bank's integration team.
9. **Runbooks** — API-key rotation (grace windows, `keys_rotated` posture check), KEK rotation (`dps.rekey`), retention (interval, manual run, what is never deleted), daily quotas (UTC reset), adding scanner hosts, a failed leakage sweep (what the evidence and audit show, how to re-run), DB outage behaviour (L1 keeps serving; DB keys fail closed after 10 refresh periods).
10. **Probes, metrics, alerts** — `/health`, `/readyz` components, Prometheus series and the PrometheusRule alerts of Task 25.
11. **Traps** — `--factory`; `--wait` with one-shot services; Oracle Free first start ≈ 3 min and 2.7 GB; DB keys propagate within `DPS_KEY_CACHE_SECONDS`; roll-ups refresh on demand (60 s) and on each retention run; tabular synthesis needs ≥ 50 sampled rows and only applies to `table`/`files` targets; MOSTLY engine not in the image; PDFs use built-in fonts (Latin-1 only, other characters folded); kustomize generator/component ordering and the copied dev mock; `@name` policy references resolve at accept time.
12. **Verification gates** — the Definition of Done commands below.
13. **Release** — `v1.0.0`, how images are tagged.
"Last verified: <date> at <commit>" stays the first line (filled in Task 28).

`README.md` — status line "v1.0.0 — M0–M5 shipped", the run block (`docker compose --profile full up --build -d`, `python tools/demo_seed.py`, `python tools/e2e_demo.py --console`), develop block, links to the spec, HANDOFF, API references `api-m0` … `api-m5`, the demo script and the security review.

`docs/technical/api-m5.md` — routes as built (admin, reports, policies store, evidence PDF, retention run) with request/response field lists, scopes, error codes (`daily_row_quota_exceeded`, `privacy_gate`, `tabular_invalid`, `tls_required`, `host_forbidden`, `policy_not_found`, `policy_not_published`, `invalid_policy_ref`, `scope_escalation`, `tenant_escalation`, `key_rotated`, `pdf_disabled`), policy references, the `external_tokenize` policy shape and the evidence pack's fidelity/privacy/tabular blocks; `curl` examples use placeholders (`$DPS_KEY`), never real keys.

`CHANGELOG.md` — `## v1.0.0 — <date>` with one paragraph per milestone M0–M5 written from the shipped code (what each delivered and its exit evidence), then "Known limitations" (the self-review gaps below).

`tests/test_handoff.py`:
```python
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import yaml

from dps.config import Settings

ROOT = Path(__file__).resolve().parents[1]
HANDOFF = (ROOT / "HANDOFF.md").read_text(encoding="utf-8")


def test_settings_table_is_current() -> None:
    out = subprocess.run([sys.executable, str(ROOT / "tools" / "settings_table.py"), "--check"], capture_output=True)
    assert out.returncode == 0, out.stdout.decode()


def test_every_setting_is_documented() -> None:
    sys.path.insert(0, str(ROOT / "tools"))
    from settings_table import FIELD_DOCS  # noqa: PLC0415

    assert set(FIELD_DOCS) == set(Settings.model_fields)
    assert all(f"DPS_{name.upper()}" in HANDOFF for name in Settings.model_fields)


def test_handoff_names_every_compose_service_and_openshift_role() -> None:
    services = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))["services"]
    for name in [*services, "dps-app", "dps-worker", "dps-browser-worker", "dps-migrate"]:
        assert name in HANDOFF, name


def test_handoff_lists_every_secret_the_manifests_mount() -> None:
    names = set()
    for path in (ROOT / "deploy" / "openshift" / "base").glob("*.yaml"):
        for doc in yaml.safe_load_all(path.read_text(encoding="utf-8")):
            text = yaml.safe_dump(doc or {})
            names |= {line.split(":", 1)[1].strip() for line in text.splitlines() if "secretName:" in line}
    assert names and all(n in HANDOFF for n in names)
```

- [ ] **Step 1: Write** the tool, the documents, the test; run `python tools/settings_table.py`.
- [ ] **Step 2: Run** `pytest -q tests/test_handoff.py` → `4 passed`; unit gate green.
- [ ] **Step 3: Commit**

```bash
git add tools/settings_table.py HANDOFF.md README.md docs/technical/api-m5.md CHANGELOG.md tests/test_handoff.py
git commit -m "docs: complete HANDOFF with generated settings table, README, M5 API reference, changelog" -- tools/settings_table.py HANDOFF.md README.md docs/technical/api-m5.md CHANGELOG.md tests/test_handoff.py
```

---

### Task 28: Release — full gates, version 1.0.0, tag

**Files:**
- Modify: `pyproject.toml` (`version = "1.0.0"`), `dps/__init__.py` (`__version__ = "1.0.0"`), `frontend/package.json` + `frontend/package-lock.json` (`npm version 1.0.0 --no-git-tag-version`), `HANDOFF.md` (Last verified), `CHANGELOG.md` (date)

- [ ] **Step 1: Bump the version** in the four files; `pytest -q tests/test_package.py` → passes (semver).
- [ ] **Step 2: Run every gate of the Definition of Done below**, in order, from a clean stack (`docker compose --profile full down -v && docker compose --profile full up --build -d`). Record each command's summary line in `HANDOFF.md` "Last verified" (date, commit, gate summaries).
- [ ] **Step 3: Commit and tag**

```bash
git add pyproject.toml dps/__init__.py frontend/package.json frontend/package-lock.json HANDOFF.md CHANGELOG.md
git commit -m "chore(release): v1.0.0" -- pyproject.toml dps/__init__.py frontend/package.json frontend/package-lock.json HANDOFF.md CHANGELOG.md
git tag -a v1.0.0 -m "DPS v1.0.0: M0-M5 — Privacy Engine, L1 API and documents, L2 connectors (PostgreSQL, Oracle, MongoDB), L3 UI scanner, tabular synthesis with measured privacy, hardening, OpenShift, demo"
```
Tag locally only; there is no remote yet (D1). Local images: `docker tag data-privacy-services:local data-privacy-services:1.0.0` and the same for the browser image.

---

## Definition of Done (M5)

All gates run with `DPS_TEST_REQUIRE_SERVICES=1` (a missing service fails, never skips).

1. **Unit + lint + types:** `ruff check dps tests tools && ruff format --check dps tests tools && mypy dps && pytest -q` → green, zero skips other than `ml` (MOSTLY extra) — `pytest -q -rs | grep SKIP` shows only `ml`, `browser`/`integration`-marked tests when run without services.
2. **Console:** `cd frontend && npm run typecheck && npm run build` → clean.
3. **Stack:** `docker compose --profile full up --build -d`; poll `curl -fsS localhost:8400/readyz` until `"ready": true` with components `api-core, db, jobs, llm-gateway, ocr, fpe, browser, tokenizer, keys` all `ok` (never `--wait`; contract §11).
4. **Milestone integration gates (regression for M1–M4, contract §12.4) and M5's own:** `pytest -q -m "docs or ocr"`, `pytest -q -m "db or pg_source or s3"`, `pytest -q -m "oracle or mongo"`, `pytest -q -m browser`, `pytest -q -m deploy` → all green, including `tests/integration/test_synthesize_copula.py`, `tests/security/test_cross_tenant.py`, `tests/console/test_console_pages.py`.
5. **Smoke test extension:** `python tools/smoke_test.py` → `all N checks passed` with the M5 block's 8 checks present and none `SKIP` (tokenizer and DB are up in `full`).
6. **Flow report:** `python tools/flow_report.py` → exit 0; `reports/flow-report.md` lists every §9 route group with expected statuses.
7. **Demo end to end:** `python tools/demo_seed.py && python tools/e2e_demo.py --console` → every step PASS, "demo flow completed in m:ss" under 10:00; screenshots in `reports/demo-screens/`.
8. **Browser check against the mockups:** open each screenshot beside its mockup (`01-dashboard`, `05-policies`, `09-reports`, `10-evidence-pack` incl. `#synthetic`, `11-admin`; and, for regression, `02`, `03`, `04`, `06`, `07`, `08`, `12`) in light and dark at 1440 px and 390 px: same sections, order, labels and states; the only accepted differences are the deviations listed in the self-review. Also print-preview the evidence page and open the downloaded PDF.
9. **Security:** `pytest -q tests/security tests/test_no_values_in_logs_m5.py` green; `docs/security/2026-10-11-m5-security-review.md` complete with every row's status; `git ls-files | xargs grep -nE "AKIA[0-9A-Z]{16}|BEGIN (RSA |EC )?PRIVATE KEY"` → nothing.
10. **OpenShift:** `kustomize build deploy/openshift/overlays/{dev,prod}` succeeds; `tests/deploy` green; optional `kubeconform` 0 errors.
11. **Docs:** `python tools/settings_table.py --check` → 0; HANDOFF, README, `api-m5.md`, DEMO-SCRIPT, CHANGELOG, security review committed; contract amended in the same commits as the code (Tasks 1, 5, 6, 8–17, 25).
12. **Release:** version `1.0.0` in `pyproject.toml`, `dps/__init__.py`, `frontend/package.json`; annotated tag `v1.0.0`; HANDOFF "Last verified" filled.

## Plan self-review (spec coverage, done while writing)

| Spec section | Covered by | Notes / gaps |
|---|---|---|
| §5.6 tabular shape — marginals (quantiles, rare categories folded, null rates), Gaussian copula on rank-transformed data | Tasks 2–3, 6 | tails clamped at 0.1 %/99.9 % so no outlier's exact value is stored or emitted |
| §5.6 constraints — ranges, positivity, uniqueness, dependent-field rules | Task 4, 5 | uniqueness by keyed Feistel permutation of a single integer key (refused with a clear error otherwise); **parent/child cardinality** is preserved by L2's 1:1 row mapping with consistent key methods, not modelled generatively |
| §5.6 optional MOSTLY AI engine (`synth-ml`) | Task 7 | pool engine (single table); multi-table, sequential and the DP option are not wired (open question 12) |
| §5.7 fidelity (KS, TVD, correlation delta, null-rate delta), privacy (exact-row = 0 enforced, NNDR, QI uniqueness) | Tasks 2, 5, 6, 14, 21 | measured on a deterministic preview of the rows the job writes, gated before writing; exact rows also rejected in flight. **Not built:** train-on-synthetic utility and the membership-inference proxy (reported "not run") |
| §5.5 `external_tokenize` adapter | Task 8 | batched, validated, retried; vaultless mock; fails closed when unconfigured |
| §4/§7 job state, retention (D11) | Tasks 10, 13 | retention per tenant, re-stamped on change; audit never touched |
| §9 console — Overview, Policies, Reports & audit, evidence PDF, Admin | Tasks 18–23 | deviations from mockups: no 30-day health strips (no readiness history), no "Schedule a weekly export", API roll-up rows show calls/values/findings (not key and policy counts), FF1 toggle in the plan preview is simulated client-side |
| §9 ten-minute demo | Task 26 | automated (`e2e_demo.py`) and scripted (`DEMO-SCRIPT.md`); the **deck** named in §11's M5 row is not part of this plan (open question 14) |
| §10 security/compliance posture | Tasks 9, 11, 16, 17 | secrets from files, TLS-only egress, CORS, docs off, KEK rotation, audit strict, no-escalation keys, scrubbed errors, ReDoS and SSRF guards |
| §11 M5 row — LLM text synthesis with verifier | M0 Task 21 (unchanged) | L2 text columns keep the M2 span pipeline; no new LLM path in M5 |
| D2 key custody | Tasks 6, 9 | tabular model sealed with the job key and purged with it; API keys hashed; raw keys shown once |
| D7, D9, D12 | Tasks 3/7, 25, 9 | — |

**Interfaces used consistently:** `GaussianCopula.fit/sample_rows/row_digest/is_rejected/to_bytes/from_bytes`; `TabularSampler` (Task 5); `ConstraintSet.apply`; `prepare_tabular`, `load_sampler`, `build_constraints`, `tabular_columns`; `HttpTokenizer`, `ExternalTokenizer`, `TokenizationUnavailable`; `DbKeyStore`, `ChainKeyStore`, `create_key/rotate_key/disable_key/enable_key/list_keys`, `check_grant`; `tenant_directory`, `effective_tenant`, `check_daily_quota`; `layer_case`, `query_audit`, `csv_cell`; `refresh_rollups`, `ensure_fresh`, `summary`, `jobs_report`, `evidence_list`; `run_retention`, `RetentionScheduler`, `enqueue_retention`; `render_evidence_pdf`, `pdf_text`; `resolve_ref`, `resolved_policy`, `policy_label`, `diff_policies`; `posture_report`, `require_tls_connection`, `check_scan_url`, `chromium_resolver_rules`, `check_pattern`.

**Soft spots to watch during execution:** (1) Task 0 is the gate for every M2–M4 name this plan touches — fix the earlier milestone, not this plan, when it fails; (2) M2's internal names inside `prepare`/`run_partition` are bound in Task 6 by role, not by name — read the M2 module first; (3) `re._parser`/`re._constants` are private stdlib modules (stable since 3.11) — pin Python 3.12 as the project does; (4) `test_marginals_correlations_and_nulls_are_preserved` and `test_nndr_detects_copies_and_accepts_fresh_draws` use fixed seeds; if a NumPy major upgrade shifts them, check the maths before touching the bounds; (5) the MOSTLY SDK's `local_dir` parameter name — verify with `help(MostlyAI)` in the `ml` run (only `_sdk_trainer` changes).

## Open contract questions

These follow the contract as written; each is a point the contract owner should decide.

1. **Mockup 05 ownership.** §15 gives the Policies page to M1; the M5 brief asks for it, including versions. Task 23 completes whatever M1 shipped and adds drafts and versions (which need M5's 043 tables). Confirm, or move versions to M1 with a migration number in M1's range.
2. **Tenant enumeration under RLS.** `dps_tenant` is a tenant table (§2.2), so no app query can list tenants. M5 adds a `SECURITY DEFINER` `dps_tenant_directory()` returning ids only, with narrow owner SELECT policies (040). Alternative: make `dps_tenant` global like `dps_api_key`.
3. **`registered_hosts`: hosts or origins?** §9 says hosts; mockup 11 shows `https://` origins. M5 stores bare hostnames and enforces the scheme (https when `DPS_EGRESS_TLS_REQUIRED`) in `netguard`.
4. **`RunOptions.tabular` default.** §5.3 defaults to `"none"`; spec §5.6 implies synthesize jobs model non-PII columns with the copula. M5 keeps `"none"` for API compatibility and defaults the console wizard and demo to `"copula"`. Flip the API default at v1.0.0?
5. **Reports scope.** §9 puts `/reports/*` under `detect`, which lets a detect-only key read tenant-wide job and sweep aggregates. M5 uses `jobs` for `/reports/evidence` (packs) and keeps `detect` for the aggregates as written. Consider `jobs` (or `admin`) for all.
6. **Audit of failed authentication.** `dps_audit_log` is tenant-scoped; a 401 has no tenant, so M5 counts it in metrics only. Option: write such rows under the system tenant `_dps`.
7. **Roll-up sources.** §9 says "from audit rollups"; job, partition (sweep) and scan layers come from job tables because the audit log has no per-partition data.
8. **Exact-row guarantee scope.** Enforcement compares each synthetic row with the fit sample, the holdout and the source rows of its batch, in memory; a whole-table guarantee would need a post-write hash join on the target. The pack states the basis.
9. **Quota day boundary.** M5 resets daily row quotas at 00:00 UTC; mockup 11 says IST. Add `DPS_QUOTA_TIMEZONE`?
10. **Key prefix display.** Showing `dps_` + 4 characters reveals 24 of ~256 bits; acceptable, but record it.
11. **`GET /evidence/{id}/pdf` placement.** The route path is in M2's evidence group; M5 implements it in its reports router (M5-owned code). Fine unless the contract wants routers strictly by path prefix.
12. **MOSTLY engine shape.** The pool approach repeats rows when the source is larger than the pool; per-row generation needs a persisted model (hundreds of MB) shared across workers. Limit `mostly` to `files` targets, or accept repetition?
13. **Spec §5.7 items not in v1:** train-on-synthetic utility and the membership-inference proxy.
14. **Spec §11 M5 "deck":** not in this plan's brief; a separate deliverable.
15. **No `rollup` job kind.** Roll-ups ride on the `retention` job plus on-demand refresh, avoiding a §2.4 CHECK change; confirm.
16. **Evidence `expires_at`.** The append-only trigger on `dps_evidence` blocks re-stamping it when a tenant's retention changes; retention keys off `dps_job.expires_at`, so behaviour is correct but the column can be stale.
17. **Preset hashes.** Adding `SynthesizeConfig.constraints` and `.quasi_identifiers` changes every preset's `config_hash()` once; nothing in the contract pins a literal hash.

## Execution

Plan complete and saved to `docs/superpowers/plans/2026-10-11-m5-hardening-and-showcase.md`. Two execution options:

1. **Subagent-driven (recommended)** — a fresh subagent per task, review between tasks (`superpowers:subagent-driven-development`). Tasks 2–7 (engine), 8, 9–17 (server), 18–23 (console) and 24–26 (stack, deploy, demo) are the natural review checkpoints; console tasks can run in parallel with server tasks once Task 18 lands, because they depend only on the route shapes fixed here.
2. **Inline execution** — tasks executed in one session with checkpoints (`superpowers:executing-plans`).
