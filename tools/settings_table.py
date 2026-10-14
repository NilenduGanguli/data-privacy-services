"""Render HANDOFF.md's configuration table from ``Settings.model_fields`` (M5 Task 27).

    python tools/settings_table.py           # rewrite the table in HANDOFF.md
    python tools/settings_table.py --check   # exit 1 when the table is stale (the docs gate)

The table lives between ``<!-- settings-table:start -->`` and ``<!-- settings-table:end -->``:
one row per ``dps.config.Settings`` field — the variable ``DPS_<NAME>``, its default (``—`` for
secret fields and empty values), ``secret (_FILE)`` for the secret fields (``dps.posture.SECRET_ENV``
plus ``DPS_BOOTSTRAP_API_KEY``), the milestone that added it and the one-line description of
:data:`FIELD_DOCS`. Every field must have an entry and every entry a field: a setting added
without documentation fails ``--check`` (and ``tests/test_handoff.py``) with its name.
Prints names only, never a value from the environment: defaults come from the class.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from pydantic.fields import FieldInfo

from dps.config import Settings
from dps.posture import SECRET_ENV

ROOT = Path(__file__).resolve().parents[1]
HANDOFF = ROOT / "HANDOFF.md"
START = "<!-- settings-table:start -->"
END = "<!-- settings-table:end -->"
DASH = "—"
#: The secret fields: mounted files in staging/prod (posture), never a default worth printing.
SECRETS = frozenset({*SECRET_ENV, "DPS_BOOTSTRAP_API_KEY"})

#: field → (milestone, one-line description). Plain text; no value, key or host of any deployment.
FIELD_DOCS: dict[str, tuple[str, str]] = {
    # --- app ---
    "app_name": ("M0", "Declared, not read by the app."),
    "env": (
        "M0",
        "`local`, `dev`, `staging` or `prod`; `staging`/`prod` enforce the posture guard at boot "
        "and serve no `/docs` or OpenAPI document; outside `local`: JSON logs and HSTS.",
    ),
    "log_level": ("M0", "Log level of the allow-listed structured logs."),
    "host": ("M0", "Address the worker's ops server (health, readiness, metrics) binds to."),
    "port": ("M0", "Declared, not read: the image's command fixes `--port 8400`."),
    # --- auth ---
    "auth_enabled": (
        "M0",
        "`false` only on a laptop (every request then runs as a wildcard `local-dev` principal); "
        "refused in staging/prod.",
    ),
    "api_keys": (
        "M0",
        "Static keys: a JSON list of `{name, key or key_sha256, tenant_ids, scopes}` (no implicit "
        "wildcard); staging/prod read it from `DPS_API_KEYS_FILE`. DB-backed keys come from "
        "`/admin/keys` and `python -m dps.keys_cli` (M5).",
    ),
    "bootstrap_api_key": (
        "M0",
        "One wildcard key (every tenant and scope) for local demos only; refused in staging/prod.",
    ),
    # --- limits ---
    "max_payload_mb": ("M0", "JSON request body limit (413 `payload_too_large`)."),
    "max_text_chars": (
        "M0",
        "Text characters per request, a document's extracted text included (413 beyond).",
    ),
    "max_items": ("M0", "JSON leaf values per request or per NDJSON item (413 beyond)."),
    "rate_limit_enabled": ("M0", "Token-bucket rate limit per API key, per process."),
    "rate_limit_rps": (
        "M0",
        "Refill rate of the bucket (requests per second); 429 + `Retry-After`.",
    ),
    "rate_limit_burst": ("M0", "Bucket size (requests)."),
    # --- engine ---
    "default_policy": (
        "M0",
        "Shipped preset used when a request names no policy (strict verification in staging/prod).",
    ),
    "min_score": (
        "M0",
        "Detector score floor for `POST /schema/generator-spec` samples; every other path uses "
        "the policy's `detect.min_score`.",
    ),
    "ner_enabled": (
        "M0",
        "Unset = on when `presidio-analyzer` imports; `true` requires the `ner` extra and a spaCy "
        "model; `false` = patterns and cues only.",
    ),
    "ner_languages": (
        "M0",
        "NER languages; those with an installed model load (the `WITH_NER=1` image ships `en`).",
    ),
    "fpe_enabled": (
        "M0",
        "FF1 format-preserving methods (`fastfpe`); `false` falls back to `det_token` and reports "
        "it. Flip only after the licence review (D8); compose sets `true`.",
    ),
    "default_locale": (
        "M0",
        "Locale for phones, dates and surrogates unless the request or the policy sets one.",
    ),
    # --- LLM gateway ---
    "llm_provider": (
        "M0",
        "`stellar` (production, VDI), `vertex` (laptop), `stub` (tests, demo; refused in "
        "staging/prod) or empty (no LLM). The gateway only ever sees typed placeholders.",
    ),
    "llm_model": ("M0", "Model name sent to the gateway."),
    "llm_timeout_seconds": ("M0", "Timeout of one LLM call."),
    "llm_text_rewrite": (
        "M0",
        "Global switch for the placeholder-only text rewrite; a request or policy can only narrow "
        "it.",
    ),
    "stellar_base_url": ("M0", "Stellar gateway base URL (`https` in staging/prod)."),
    "coin_url": ("M0", "COIN OAuth2 token endpoint (`https` in staging/prod)."),
    "coin_client_id": ("M0", "COIN client id, base64-encoded (org convention)."),
    "coin_client_secret": ("M0", "COIN client secret, base64-encoded."),
    "coin_scope": ("M0", "COIN scope, base64-encoded; sent as `coinscope<scope>`."),
    "coin_token_ttl_seconds": ("M0", "How long a minted COIN token is reused."),
    "ssl_cert_file": (
        "M0",
        "Organisation CA bundle for egress (Stellar, and the COIN mint without "
        "`DPS_COIN_CA_FILE`); required in staging/prod when any egress URL is set.",
    ),
    "coin_ca_file": (
        "M1",
        "CA bundle (file or directory) for the COIN token mint; else `DPS_SSL_CERT_FILE`, else "
        "httpx's default store (`SSL_CERT_FILE`, then `SSL_CERT_DIR` from the environment, else "
        "certifi).",
    ),
    "coin_tls_insecure": (
        "M1",
        "Skip TLS verification on the COIN mint only (the VDI's intercepting proxy); `/readyz` "
        "then reports `coin_tls: insecure`.",
    ),
    "allow_insecure_coin_mint": (
        "M1",
        "The documented VDI exception: staging/prod refuse `DPS_COIN_TLS_INSECURE=true` without it.",
    ),
    "vertex_project": ("M0", "Vertex AI project (laptop path; needs the `vertex` extra and ADC)."),
    "vertex_location": ("M0", "Vertex AI location."),
    # --- audit ---
    "audit_enabled": (
        "M0",
        "One value-free audit event per request and job action; refused off in staging/prod.",
    ),
    # --- documents and schema (M1) ---
    "ocr_provider": (
        "M1",
        "`off`/empty, `azure` (Read v3.2: mock, org gateway, Azure), `tesseract` (local CLI) or "
        "`auto` (azure when endpoint and key are set, else tesseract when on PATH; the image's "
        "default).",
    ),
    "azure_vision_endpoint": (
        "M1",
        "Read v3.2 base URL (compose: the mock; production: the org OCR gateway, `https`).",
    ),
    "azure_vision_key": ("M1", "`Ocp-Apim-Subscription-Key` of the Read v3.2 endpoint."),
    "ocr_timeout_seconds": ("M1", "One OCR call (analyze plus polls)."),
    "ocr_poll_interval_seconds": ("M1", "Spacing of the Read v3.2 result polls."),
    "max_document_mb": (
        "M1",
        "Uploaded document limit (413 `document_too_large`; the body may be 1 MiB larger).",
    ),
    "max_document_pages": ("M1", "Pages per document (413 beyond)."),
    "pdf_render_dpi": (
        "M1",
        "Raster resolution for OCR and redaction (pages over 10,000 px a side are refused).",
    ),
    "schema_max_rows": ("M1", "Rows per `POST /schema/synthesize` call."),
    "ocr_health_path": (
        "M1",
        "Readiness probe path of the OCR endpoint (`/_gw/health` behind the org gateway; empty = "
        "no probe).",
    ),
    "ocr_probe_interval_seconds": ("M1", "OCR readiness probe interval (`0` = once at startup)."),
    "ocr_max_concurrency": (
        "M1",
        "Parallel OCR calls per document; also bounds parallel Tesseract runs per process.",
    ),
    "ocr_min_text_chars": (
        "M1",
        "Under `ocr: auto`, a PDF page with fewer text-layer characters is OCR'd.",
    ),
    "ocr_image_coverage": (
        "M1",
        "Under `ocr: auto`, a PDF page whose images cover this share of it is OCR'd.",
    ),
    "redact_image_format": ("M1", "`jpeg` or `png` page rasters inside a redacted PDF."),
    "redact_jpeg_quality": ("M1", "JPEG quality of redacted page rasters."),
    "redact_ocr_verify": (
        "M1",
        "Re-read every page of a redacted PDF with OCR (when configured) and refuse it (422 "
        "`leakage`) if a redacted original is still readable.",
    ),
    "preview_dpi": ("M1", "Console page-preview resolution."),
    "max_preview_pages": ("M1", "Pages previewed in the console per document."),
    "tesseract_cmd": ("M1", "Tesseract binary (name on PATH or a path)."),
    "tesseract_langs": ("M1", "Tesseract languages (`eng`, `eng+spa`, …; the image ships `eng`)."),
    "tesseract_timeout_seconds": (
        "M1",
        "One page's Tesseract time limit (the process group is killed: `ocr_timeout`).",
    ),
    "llm_label_max_fields": (
        "M1",
        "T3 labelling (opt-in tier `llm_label`): fields per call (names, types and value shapes "
        "only, never values).",
    ),
    "llm_label_min_confidence": ("M1", "T3 labelling: verdicts below this confidence are dropped."),
    "ndjson_max_items": ("M1", "Items per NDJSON stream (413 `ndjson_too_large`)."),
    "ndjson_chunk_items": ("M1", "NDJSON items per worker-thread hop."),
    "max_ndjson_mb": (
        "M1",
        "Body limit of an `application/x-ndjson` request (held in memory while it streams).",
    ),
    "idempotency_ttl_seconds": (
        "M1",
        "`Idempotency-Key` replay window on the Layer 1 routes (replica-local fingerprints only, "
        "never key material — D2); `0` disables the cache.",
    ),
    "idempotency_max_entries": ("M1", "Entries in the Layer 1 replay cache."),
    # --- metadata database (M2) ---
    "pg_host": (
        "M2",
        "Metadata PostgreSQL host; empty = Layer 1 only (no jobs, no keys from the database, no "
        "M2 posture checks).",
    ),
    "pg_port": ("M2", "Metadata PostgreSQL port."),
    "pg_database": ("M2", "Metadata database."),
    "pg_schema": ("M2", "Schema of the DPS tables (`^[a-z_][a-z0-9_]{0,62}$`)."),
    "pg_user": ("M2", "API pool login (`dps_app`)."),
    "pg_password": ("M2", "API pool password."),
    "pg_worker_user": ("M2", "Worker pool login (`dps_worker_login`); empty = `DPS_PG_USER`."),
    "pg_worker_password": ("M2", "Worker pool password."),
    "pg_migration_user": (
        "M2",
        "DDL login for `python -m dps.migrate` (`dps_owner`); empty = `DPS_PG_USER`.",
    ),
    "pg_migration_password": ("M2", "DDL login password (the migrate Job only)."),
    "pg_pool_min": ("M2", "Minimum connections per pool."),
    "pg_pool_max": ("M2", "Maximum connections per pool."),
    "pg_ssl_root_cert": (
        "M2",
        "CA file that turns on verified TLS to the metadata database (required in staging/prod).",
    ),
    "rls_enabled": ("M2", "Tenant row-level security; must stay `true` in staging/prod."),
    "migrations_mode": (
        "M2",
        "`auto` (apply at boot), `verify` (staging/prod: check only — while migrations are "
        "pending or changed the job routes answer 503 `db_unavailable`) or `off` (tests).",
    ),
    "job_kek": (
        "M2",
        "Key-encryption key (base64 of 32 bytes): seals job keys, specs and inline credentials and "
        "keys request fingerprints and the external-token binding; staging/prod read it only from "
        "`DPS_JOB_KEK_FILE`.",
    ),
    "job_kek_previous": (
        "M2",
        "The previous KEK during a rotation; `python -m dps.rekey` re-seals what only it opens "
        "(M5).",
    ),
    # --- queue ---
    "job_lease_seconds": ("M2", "Lease of a claimed job (heartbeats renew it)."),
    "partition_lease_seconds": ("M2", "Lease of a claimed partition."),
    "job_max_attempts": ("M2", "Attempts of a job before it is dead-lettered (its key purged)."),
    "partition_max_attempts": (
        "M2",
        "Attempts of a partition before it is dead-lettered (the run then fails).",
    ),
    "job_retry_base_seconds": (
        "M2",
        "Retry back-off: `min(max, base × 2^attempts)` seconds with ±50 % jitter.",
    ),
    "job_retry_max_seconds": ("M2", "Ceiling of the retry back-off."),
    "job_poll_interval_seconds": ("M2", "Idle worker's queue poll interval."),
    "job_claim_batch": ("M2", "Most queue items a worker claims per poll."),
    "job_reaper_interval_seconds": ("M2", "How often each worker reaps expired leases."),
    "job_drain_timeout_seconds": (
        "M2",
        "On SIGTERM: time to finish claimed work before releasing it without charging an attempt.",
    ),
    "job_tenant_max_running": ("M2", "Running jobs per tenant (a `dps_tenant` row overrides)."),
    "job_tenant_max_queued": ("M2", "Queued jobs per tenant (a `dps_tenant` row overrides)."),
    "worker_concurrency": ("M2", "Concurrent tasks per worker process."),
    "worker_metrics_port": (
        "M2",
        "Worker ops server (`/health`, `/readyz`, `/metrics`); `0` turns it off.",
    ),
    "embedded_worker": (
        "M2",
        "Run a worker inside the app process (local demos only; refused in staging/prod).",
    ),
    # --- connectors ---
    "batch_rows": ("M2", "Rows per transform-and-write batch (a run may set its own)."),
    "partition_rows": ("M2", "Rows per partition (a run may set its own)."),
    "max_partitions": ("M2", "Most partitions per run."),
    "discovery_sample_rows": ("M2", "Rows sampled per object by discovery."),
    "preflight_rows": ("M2", "Rows transformed at prepare, before any write."),
    "connector_connect_timeout_seconds": ("M2", "Connect timeout to a source or target."),
    "connector_statement_timeout_seconds": (
        "M2",
        "Statement timeout of connector reads and writes.",
    ),
    "secret_dir": (
        "M2",
        "Root of the connection `secret_ref` folders, per tenant: `<dir>/<tenant_id>/<ref>/` "
        "(and `<dir>/_shared/<ref>/` for listed tenants).",
    ),
    "inline_secrets_allowed": (
        "M2",
        "Accept inline connection credentials (sealed with the KEK); refused in staging/prod.",
    ),
    "inline_secret_ttl_hours": ("M2", "Inline credentials stop resolving after this."),
    # --- M3 connectors ---
    "oracle_pool_max": ("M3", "Sessions per Oracle connection pool (1–64)."),
    "oracle_call_timeout_ms": (
        "M3",
        "Per round trip to Oracle (1,000–3,600,000; a hung call becomes an error).",
    ),
    "oracle_module": (
        "M3",
        "`v$session.module` of every DPS session, for the DBA's audit (1–48 of `[A-Za-z0-9_.-]`).",
    ),
    "mongo_server_selection_timeout_ms": ("M3", "MongoDB server selection timeout (500–120,000)."),
    # --- retention and audit ---
    "retention_days": (
        "M2",
        "Days a finished job, its artefacts and evidence are kept (a tenant's own setting wins).",
    ),
    "retention_interval_hours": (
        "M2",
        "One retention job per interval across all workers (owner `_system`).",
    ),
    "audit_retention_months": (
        "M2",
        "`python -m dps.migrate --maintain` drops audit partitions older than this.",
    ),
    "audit_strict": (
        "M2",
        "A request whose audit event cannot be written fails (503 `audit_unavailable`); required "
        "in staging/prod.",
    ),
    "audit_queue_max": ("M2", "Bound of the database audit sink's queue."),
    "audit_flush_batch": ("M2", "Audit rows per insert."),
    "audit_flush_ms": ("M2", "Audit flush interval."),
    # --- artefacts and exports ---
    "artefact_store": ("M2", "`local` (a directory or volume) or `s3` (S3, MinIO)."),
    "artefact_dir": ("M2", "Artefact directory of the `local` store."),
    "s3_endpoint_url": ("M2", "S3 endpoint (empty = AWS; `https` in staging/prod)."),
    "s3_region": ("M2", "S3 region."),
    "s3_bucket": ("M2", "Artefact bucket."),
    "s3_access_key_id": ("M2", "S3 access key id."),
    "s3_secret_access_key": ("M2", "S3 secret access key."),
    "s3_force_path_style": ("M2", "Path-style S3 addressing (MinIO)."),
    "export_root": ("M2", "Root of `files` targets on a mounted volume (`pvc` connections)."),
    "image_digest": ("M2", "Image digest stamped into every evidence pack."),
    # --- M5: tabular synthesis ---
    "synth_tabular_engine": (
        "M5",
        "Enabled tabular engines, a comma list of `copula` and `mostly` (the `synth-ml` extra); "
        "empty = tabular synthesis off.",
    ),
    "copula_fit_rows": ("M5", "Rows the copula is fitted on at prepare."),
    "copula_holdout_rows": ("M5", "Rows held out, never fitted, to measure privacy."),
    "copula_metrics_rows": (
        "M5",
        "Preview rows run through the real batch transformer to measure fidelity and privacy.",
    ),
    "copula_rare_floor": ("M5", "Categories below this share are never emitted (0–0.2)."),
    "copula_max_resample": (
        "M5",
        "Redraws per row before a batch fails closed with `exact_row_match`.",
    ),
    "fidelity_column_limit": ("M5", "KS/TVD per column shown as within limit."),
    "fidelity_corr_limit": ("M5", "Mean correlation delta shown as within limit."),
    "privacy_nndr_target": (
        "M5",
        "Nearest-neighbour distance ratio at or above which privacy passes.",
    ),
    "privacy_nndr_enforce": (
        "M5",
        "`true`: a ratio below the target fails prepare (`privacy_gate`).",
    ),
    "mostly_pool_rows": (
        "M5",
        "Rows in the optional MOSTLY AI engine's generated pool per job (repeated when the source "
        "is larger).",
    ),
    "mostly_max_training_minutes": ("M5", "MOSTLY AI training time limit."),
    # --- M5: external tokenization ---
    "tokenizer_provider": (
        "M5",
        "`http` = the bank tokenization service adapter (`external_tokenize`); empty = off.",
    ),
    "tokenizer_url": (
        "M5",
        "Base URL of the tokenization adapter (`/v1/tokenize`, `/v1/detokenize`, `/health`; "
        "`https` in staging/prod).",
    ),
    "tokenizer_token": ("M5", "Bearer token of the tokenization adapter."),
    "tokenizer_ca_file": ("M5", "CA bundle for the adapter; else `DPS_SSL_CERT_FILE`."),
    "tokenizer_timeout_seconds": ("M5", "Timeout of one tokenization call."),
    "tokenizer_batch": ("M5", "Values per tokenize or detokenize call."),
    "tokenizer_retries": ("M5", "Retries on 429, 5xx and transport errors."),
    # --- M5: keys, reports, posture ---
    "key_cache_seconds": (
        "M5",
        "DB-backed API keys are refreshed this often (a disable or rotation reaches other "
        "replicas within it).",
    ),
    "key_max_ttl_days": ("M5", "Longest lifetime of a DB-backed key."),
    "key_rotation_max_age_days": (
        "M5",
        "Keys older than this and never rotated fail the `keys_rotated` posture check.",
    ),
    "key_rotation_max_grace_hours": ("M5", "Longest grace period of a key rotation."),
    "report_pdf_enabled": (
        "M5",
        "Evidence-pack and roll-up PDFs; `false` = 404 `pdf_disabled`.",
    ),
    "rollup_retention_days": (
        "M5",
        "Report roll-up and daily-usage rows older than this are deleted.",
    ),
    "egress_tls_required": (
        "M5",
        "TLS-only egress: every egress URL `https` and every connection TLS (422 `tls_required`); "
        "required in staging/prod.",
    ),
    "cors_origins": (
        "M5",
        "Allowed CORS origins, a comma list (empty = same-origin only); never `*` in staging/prod.",
    ),
    "verify_warn_allowed": (
        "M5",
        "Whether callers may relax the leakage sweep to `warn`; `false` in staging/prod (strict "
        "verification at boot).",
    ),
    "llm_egress_check": (
        "M5",
        "Egress verifier on every LLM prompt: `strict` blocks a raw value (staging/prod), `warn` "
        "counts it.",
    ),
    "utility_max_rows": ("M5", "Train-on-synthetic, test-on-real budget per job."),
    "mia_min_holdout": ("M5", "Holdout rows the membership-inference proxy needs, else not run."),
    "mia_auc_target": ("M5", "Membership-inference AUC at or below which the proxy passes."),
    "mostly_dp_enabled": ("M5", "MOSTLY AI differential-privacy training."),
    "mostly_dp_max_epsilon": ("M5", "Largest epsilon a DP run may spend."),
    "mostly_dp_delta": ("M5", "Delta of a DP run (0 < delta < 1)."),
    "mostly_linked_child_rows": (
        "M5",
        "MOSTLY linked generation: most child rows read for training.",
    ),
}


def _env(name: str) -> str:
    return f"DPS_{name.upper()}"


def _cell(text: str) -> str:
    """A Markdown table cell: pipes escaped, one line."""
    return " ".join(text.replace("|", "\\|").split())


def _default(name: str, field: FieldInfo) -> str:
    value: Any = field.default
    if _env(name) in SECRETS or value is None or value == "":
        return DASH
    if isinstance(value, bool):
        return "`true`" if value else "`false`"
    return f"`{value}`"


def problems() -> list[str]:
    """Fields without an entry and entries without a field (names only)."""
    fields = set(Settings.model_fields)
    out = [
        f"undocumented setting {_env(n)} (add it to FIELD_DOCS)"
        for n in sorted(fields - set(FIELD_DOCS))
    ]
    out += [
        f"FIELD_DOCS names {_env(n)}, which Settings no longer has"
        for n in sorted(set(FIELD_DOCS) - fields)
    ]
    return out


def render() -> str:
    """The generated block, markers included."""
    lines = [
        START,
        "<!-- generated by `python tools/settings_table.py` from dps.config.Settings: do not edit -->",
        "",
        "| Variable | Default | Secret | Since | What it does |",
        "|---|---|---|---|---|",
    ]
    for name, field in Settings.model_fields.items():
        milestone, text = FIELD_DOCS[name]
        secret = "secret (`_FILE`)" if _env(name) in SECRETS else ""
        lines.append(
            f"| `{_env(name)}` | {_default(name, field)} | {secret} | {milestone} | {_cell(text)} |"
        )
    lines.append(END)
    return "\n".join(lines)


def replace_block(document: str, block: str) -> str:
    """``document`` with the text between the markers (markers included) replaced by ``block``."""
    start, end = document.find(START), document.find(END)
    if start < 0 or end < start:
        raise ValueError(f"HANDOFF.md has no {START} … {END} block")
    return document[:start] + block + document[end + len(END) :]


def main(argv: list[str] | None = None) -> int:
    """Rewrite the table, or with ``--check`` exit 1 when it is stale."""
    parser = argparse.ArgumentParser(prog="python tools/settings_table.py", description=__doc__)
    parser.add_argument("--check", action="store_true", help="exit 1 when HANDOFF.md is stale")
    parser.add_argument("--handoff", type=Path, default=HANDOFF, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    issues = problems()
    if issues:
        for issue in issues:
            print(issue)
        return 1
    current = args.handoff.read_text(encoding="utf-8")
    try:
        wanted = replace_block(current, render())
    except ValueError as exc:
        print(exc)
        return 1
    if args.check:
        if wanted != current:
            print("HANDOFF.md's settings table is stale: run python tools/settings_table.py")
            return 1
        print(f"settings table current ({len(Settings.model_fields)} settings)")
        return 0
    if wanted != current:
        args.handoff.write_text(wanted, encoding="utf-8")
        print(f"settings table rewritten ({len(Settings.model_fields)} settings)")
    else:
        print(f"settings table already current ({len(Settings.model_fields)} settings)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
