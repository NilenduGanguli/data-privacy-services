"""The M0–M4 interfaces M5 builds on (contract §1–§13). A failure lists every missing name.

Adapted to the merged code (M5 plan Task 0, finish stage): a name that moved is listed where it
lives now, never dropped — ``validate_run`` lives in ``dps.layers.connectors.targets`` (M2 as built,
contract §5.3). M4 (the UI scanner) merged into ``fsec`` after this test was written (2026-10-15):
its names, once a strict ``xfail`` that waited for that merge, are part of :data:`SURFACE` and
:data:`ROUTES` now (contract §3.5 roles and modules, §9 scan routes).
"""

from __future__ import annotations

import importlib
import inspect

from fastapi.routing import iter_route_contexts

SURFACE: dict[str, tuple[str, ...]] = {
    "dps.config": ("Settings", "get_settings"),
    "dps.posture": ("posture_problems", "assert_production_posture", "PostureError"),
    "dps.auth": (
        "Principal",
        "StaticKeyStore",
        "KeyStore",
        "hash_key",
        "new_api_key",
        "require_scope",
        "SCOPES",
        "LOCAL_PRINCIPAL",
        "authenticate",
        "resolve_tenant",
    ),
    "dps.audit": ("AuditEvent", "MemoryAuditSink", "LogAuditSink", "DbAuditSink"),
    "dps.engine.policy": (
        "Policy",
        "MethodSpec",
        "SynthesizeConfig",
        "resolve_policy",
        "load_preset",
        "list_presets",
        "METHOD_NAMES",
    ),
    "dps.engine.transform.methods": (
        "TransformContext",
        "run_method",
        "reverse_method",
        "MethodError",
        "METHODS",
    ),
    "dps.engine.transform.apply": (
        "ApplyOptions",
        "apply_plan",
        "reveal",
        "RevealHint",
        "anchor_for",
    ),
    "dps.engine.planner": ("build_plan", "explain_plan"),
    "dps.engine.service": (
        "PrivacyEngine",
        "EngineError",
        "VerificationFailed",
        "Payload",
        "RequestOptions",
    ),
    "dps.engine.synth.surrogates": (
        "SynthesisSeed",
        "seed_bytes",
        "seed_from_caller",
        "generate_synthesis_seed",
    ),
    "dps.engine.synth.personas": ("seed_int",),
    "dps.engine.findings": ("hash_value",),
    "dps.layers.api.common": ("run_engine",),
    "dps.db": (
        "init_pool",
        "init_worker_pool",
        "close_pools",
        "acquire",
        "acquire_queue",
        "run_migrations",
        "verify_migrations",
        "assert_rls_posture",
        "db_configured",
        "TENANT_TABLES",
        "GLOBAL_TABLES",
        "ALLOWED_EXTRA_POLICIES",
    ),
    "dps.ids": ("new_id",),
    "dps.secrets": ("load_kek", "seal", "open_sealed", "resolve_secret_ref"),
    "dps.storage": ("ObjectStore", "ArtefactService", "artefact_store"),
    "dps.jobs": (
        "JobKind",
        "JobState",
        "JobPhase",
        "PartitionState",
        "Job",
        "JobInternal",
        "ClaimedJob",
        "ClaimedPartition",
        "create_job",
        "get_job",
        "list_jobs",
        "claim_jobs",
        "claim_partitions",
        "advance_job",
        "checkpoint",
        "finish_partition",
        "queue_stats",
        "worker_id",
        "SYSTEM_TENANT",
        "ensure_retention_job",
        "load_job_internal",
    ),
    "dps.worker": ("Consumer", "HandlerContext", "build_context"),
    "dps.retention": ("retention_job",),
    "dps.engine.detect.llm_label": ("FieldMeta", "LabelVerdict", "label_fields"),
    "dps.jobs_models": (
        "RunSpec",
        "RunOptions",
        "JobSecret",
        "JobProgress",
        "RetentionSpec",
        "ConnectionSpec",
        "TargetSpec",
        "SpecError",
        "ScanSpec",  # M4 (contract §9)
    ),
    # moved (M2 as built): validation of a run lives beside the write-mode rules it applies
    "dps.layers.connectors.targets": ("validate_run",),
    "dps.engine.batch": (
        "BatchTransformer",
        "ColumnPlan",
        "BatchResult",
        "TabularSampler",
        "LeakageError",
    ),
    "dps.layers.connectors.base": (
        "ObjectRef",
        "ColumnInfo",
        "TableShape",
        "Connector",
        "Row",
        "to_engine_text",
        "from_engine_text",
    ),
    "dps.layers.connectors.selection": (
        "validate_selection",
        "compile_where",
        "quote_ident",
        "SelectionError",
    ),
    "dps.layers.connectors.evidence": ("EvidencePack", "render_html", "pack_sha256"),
    "dps.layers.connectors.jobs_run": ("prepare", "run_partition", "finalize"),
    "dps.layers.connectors.registry": ("open_connector",),
    "dps.layers.api.errors": ("DpsHttpError",),
    # M4 (contract §3.5, §9): the browser-worker role, the scan handlers and the scan spec
    "dps.browser_worker": ("build_consumer", "amain", "main"),
    "dps.layers.scanner": ("scan_job", "scan_finalize_job", "open_runtime"),
    "dps.layers.scanner.crawler": ("ScanRunner",),
    "dps.layers.scanner.classify": ("classify_field",),
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
    ("dps.engine.batch", "BatchTransformer"): (
        "engine",
        "policy",
        "mode",
        "columns",
        "shape",
        "key",
        "seed",
        "locale",
        "unclassified",
        "tabular",
    ),
    ("dps.jobs", "create_job"): (
        "tenant_id",
        "kind",
        "phase",
        "spec",
        "sealed_secret",
        "purpose",
        "created_by",
    ),
    ("dps.jobs", "advance_job"): (
        "worker_id",
        "job_id",
        "phase",
        "state",
        "progress",
        "error_code",
    ),
    ("dps.worker", "Consumer"): (
        "settings",
        "kinds",
        "handlers",
        "partition_handler",
        "concurrency",
        "ctx",
        "role",
    ),
}

#: Routes M5 extends (contract §9). The evidence route's parameter is ``evidence_id``.
ROUTES: tuple[str, ...] = (
    "/api/v1/evidence/{evidence_id}",
    "/api/v1/jobs/run",
    "/api/v1/connections",
    "/api/v1/policies/test-recognizer",
    "/api/v1/entities",
    # M4 (contract §9): the scans API
    "/api/v1/scans",
    "/api/v1/scans/{scan_id}",
    "/api/v1/scans/{scan_id}/pages",
    "/api/v1/scans/{scan_id}/pages/{page_no}",
    "/api/v1/scans/{scan_id}/events",
    "/api/v1/scans/{scan_id}/inventory",
    "/api/v1/scans/{scan_id}/export",
    "/api/v1/scans/{scan_id}/cancel",
    "/api/v1/scans/personas/preview",
)


def _missing(surface: dict[str, tuple[str, ...]]) -> list[str]:
    missing: list[str] = []
    for module_name, names in surface.items():
        try:
            module = importlib.import_module(module_name)
        except ImportError:
            missing.append(f"{module_name} (module)")
            continue
        missing += [f"{module_name}.{n}" for n in names if not hasattr(module, n)]
    return missing


def _missing_either_and_params() -> list[str]:
    missing: list[str] = []
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
            missing.append(f"{module_name}.{name}")
            continue
        sig = inspect.signature(target)
        missing += [f"{module_name}.{name}({p}=)" for p in params if p not in sig.parameters]
    return missing


def _route_paths() -> set[str]:
    from dps.app import create_app
    from dps.config import Settings

    app = create_app(Settings(_env_file=None, env="local", auth_enabled=False, llm_provider="stub"))
    # FastAPI keeps included routers as nested route containers: walk the effective routes
    return {ctx.path for ctx in iter_route_contexts(app.routes) if ctx.path}


def test_m0_to_m4_surface_exists() -> None:
    missing = _missing(SURFACE) + _missing_either_and_params()
    assert missing == [], "contract surface missing: " + ", ".join(missing)


def test_system_tenant_and_global_tenant_table() -> None:
    jobs = importlib.import_module("dps.jobs")
    db = importlib.import_module("dps.db")
    assert jobs.SYSTEM_TENANT == "_system"  # contract §3.6: the only system tenant
    assert "dps_tenant" in db.GLOBAL_TABLES and "dps_tenant" not in db.TENANT_TABLES  # §2.4


def test_job_models_carry_the_m5_hooks() -> None:
    models = importlib.import_module("dps.jobs_models")
    assert "tabular" in models.RunOptions.model_fields
    assert models.RunOptions.model_fields["tabular"].default == "none"
    assert "tabular_model" in models.JobSecret.model_fields


def test_routes_m5_extends_exist() -> None:
    paths = _route_paths()
    missing = [path for path in ROUTES if path not in paths]
    assert missing == [], f"routes missing (path parameter names may differ): {missing}"
