"""Application settings — env-driven via pydantic-settings, prefix ``DPS_``.

Single source of truth for configuration, read once via :func:`get_settings`. Secrets are never
hard-coded; ``DPS_<NAME>_FILE`` reads a value from a mounted file (Kubernetes secret volumes, Vault
sinks), with a literal ``DPS_<NAME>`` env var taking precedence.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import re
import shutil
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import Field, field_validator
from pydantic.fields import FieldInfo
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

ENV_PREFIX = "DPS_"
ENVIRONMENTS = ("local", "dev", "staging", "prod")
LLM_PROVIDERS = ("", "stellar", "vertex", "stub")
MIGRATIONS_MODES = ("auto", "verify", "off")
ARTEFACT_STORES = ("local", "s3")
TABULAR_ENGINES = ("copula", "mostly")
_SCHEMA_RE = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
_KEK_MESSAGE = "job KEK must be base64 of exactly 32 bytes"
_ORACLE_MODULE = re.compile(r"[A-Za-z0-9_.-]{1,48}")


class _FileSecretSource(PydanticBaseSettingsSource):
    """Read a field from the file named by ``DPS_<FIELD>_FILE``."""

    def get_field_value(self, field: FieldInfo, field_name: str) -> tuple[Any, str, bool]:
        env_name = f"{ENV_PREFIX}{field_name.upper()}_FILE"
        path = os.environ.get(env_name)
        if not path:
            return None, field_name, False
        try:
            return Path(path).read_text(encoding="utf-8").strip(), field_name, False
        except OSError as exc:
            # The path and the OS reason only — never the file's content.
            raise ValueError(f"{env_name}={path!r} could not be read: {exc}") from exc

    def __call__(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for field_name, field in self.settings_cls.model_fields.items():
            value, key, _ = self.get_field_value(field, field_name)
            if value is not None:
                result[key] = value
        return result


_PROFILE_ENTRY = re.compile(r"[A-Z][A-Z0-9_]{0,63}(:[A-Za-z0-9][A-Za-z0-9_.-]{0,63})?")
_PROFILE_TENANT = re.compile(r"\*|[a-z0-9][a-z0-9_-]{0,62}")


def _parse_tokenizer_profiles(raw: str) -> dict[str, frozenset[str]]:
    """``DPS_TOKENIZER_PROFILES`` as ``{tenant or "*": {"ENTITY:profile" | "ENTITY"}}``.

    Raises:
        ValueError: not a JSON object of tenant ids to lists of ``ENTITY[:profile]`` entries (the
            message names the rule, never an entry).
    """
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        raise ValueError("DPS_TOKENIZER_PROFILES is not valid JSON") from None
    if not isinstance(data, dict):
        raise ValueError("DPS_TOKENIZER_PROFILES must be a JSON object of tenant ids to lists")
    out: dict[str, frozenset[str]] = {}
    for tenant, entries in data.items():
        if not isinstance(tenant, str) or not _PROFILE_TENANT.fullmatch(tenant):
            raise ValueError("DPS_TOKENIZER_PROFILES keys must be tenant ids or *")
        if not isinstance(entries, list) or not all(
            isinstance(e, str) and _PROFILE_ENTRY.fullmatch(e) for e in entries
        ):
            raise ValueError(
                "DPS_TOKENIZER_PROFILES values must be lists of ENTITY or ENTITY:profile "
                "(profile [A-Za-z0-9][A-Za-z0-9_.-]{0,63})"
            )
        out[tenant] = frozenset(entries)
    return out


class Settings(BaseSettings):
    """All runtime configuration. Field names map to ``DPS_<UPPER_NAME>``."""

    model_config = SettingsConfigDict(
        env_prefix=ENV_PREFIX,
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        # Settings carry secrets (COIN credentials, API keys): validation errors must never echo
        # the offending input.
        hide_input_in_errors=True,
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Source precedence: init kwargs > env vars > ``_FILE`` indirection > .env > secrets dir."""
        return (
            init_settings,
            env_settings,
            _FileSecretSource(settings_cls),
            dotenv_settings,
            file_secret_settings,
        )

    # --- app ---
    app_name: str = "data-privacy-services"
    env: str = "local"
    log_level: str = "INFO"
    host: str = "0.0.0.0"
    port: int = 8400

    # --- auth (static key store in M0; a DB-backed store arrives with jobs in M2) ---
    auth_enabled: bool = True
    api_keys: str = Field(
        default="", repr=False
    )  # JSON list of {name, key | key_sha256, tenant_ids, scopes}
    bootstrap_api_key: str = Field(
        default="", repr=False
    )  # one wildcard key for local demos; refused in production

    # --- limits ---
    max_payload_mb: int = 8
    max_text_chars: int = 1_000_000
    max_items: int = 10_000
    rate_limit_enabled: bool = True
    rate_limit_rps: float = 50.0
    rate_limit_burst: int = 100

    # --- engine ---
    default_policy: str = "gdpr-pseudonymise"
    min_score: float = 0.6
    ner_enabled: bool | None = None  # None = auto: on when presidio-analyzer imports
    ner_languages: str = "en,es"
    fpe_enabled: bool = False  # FF1 is a fallback-only method until a license review flips this
    default_locale: str = "en_US"

    # --- LLM gateway (placeholder text only — see spec P1) ---
    llm_provider: str = ""
    llm_model: str = "gemini-2.5-flash"
    llm_timeout_seconds: float = 30.0
    llm_text_rewrite: bool = False
    stellar_base_url: str = ""
    coin_url: str = ""
    coin_client_id: str = Field(
        default="", repr=False
    )  # base64-encoded, decoded at use (org convention)
    coin_client_secret: str = Field(default="", repr=False)
    coin_scope: str = ""
    coin_token_ttl_seconds: int = 840
    ssl_cert_file: str = ""  # CA bundle for Stellar calls (and the COIN mint without coin_ca_file)
    coin_ca_file: str = ""  # CA bundle for the COIN token mint; falls back to ssl_cert_file
    # Skip TLS verification on the COIN token mint only (the VDI's intercepting proxy). Off by
    # default; staging/prod also need allow_insecure_coin_mint (the posture guard, dps/posture.py).
    coin_tls_insecure: bool = False
    allow_insecure_coin_mint: bool = False
    vertex_project: str = ""
    vertex_location: str = "global"

    # --- audit ---
    audit_enabled: bool = True

    # --- documents and schema (M1, contract §10) ---
    # "" | off (no OCR) | azure (Read v3.2: mock, org gateway, Azure) | tesseract (local CLI) |
    # auto (azure when an endpoint and key are set, else tesseract when the binary is on PATH;
    # the image's default) — see ``ocr_engine``.
    ocr_provider: str = ""
    azure_vision_endpoint: str = ""  # http://azure-ocr-mock:5000 locally; the org gateway in prod
    azure_vision_key: str = Field(
        default="", repr=False
    )  # DPS_AZURE_VISION_KEY_FILE in prod; Ocp-Apim-Subscription-Key
    ocr_timeout_seconds: float = Field(default=60.0, gt=0, le=600)
    ocr_poll_interval_seconds: float = Field(default=0.5, gt=0, le=10)
    max_document_mb: int = Field(default=20, ge=1, le=512)
    max_document_pages: int = Field(default=200, ge=1, le=5000)
    pdf_render_dpi: int = Field(default=200, ge=72, le=600)
    schema_max_rows: int = Field(default=10_000, ge=1, le=1_000_000)
    # --- documents (M1, additive) ---
    ocr_health_path: str = "/health"  # mock: /health · azure-gateway-api: /_gw/health · "" = none
    ocr_probe_interval_seconds: float = Field(default=30.0, ge=0, le=3600)  # 0 = once at startup
    ocr_max_concurrency: int = Field(default=4, ge=1, le=32)
    ocr_min_text_chars: int = Field(default=20, ge=0, le=10_000)  # fewer text-layer chars → OCR
    ocr_image_coverage: float = Field(default=0.25, ge=0.0, le=1.0)  # images this share → OCR
    redact_image_format: str = "jpeg"  # jpeg | png — rasters inside the redacted PDF
    redact_jpeg_quality: int = Field(default=85, ge=50, le=95)
    redact_ocr_verify: bool = True  # re-read redacted pages with OCR when OCR is configured
    preview_dpi: int = Field(default=72, ge=36, le=150)
    max_preview_pages: int = Field(default=50, ge=0, le=500)
    # --- local Tesseract OCR (M1 Task 19C, contract §10): the CLI, its languages, one page's
    # time limit; used for ocr_provider=tesseract, or auto without an Azure endpoint ---
    tesseract_cmd: str = Field(default="tesseract", min_length=1)
    tesseract_langs: str = Field(default="eng", pattern=r"^[A-Za-z0-9_]+(\+[A-Za-z0-9_]+)*$")
    tesseract_timeout_seconds: float = Field(default=60.0, gt=0, le=600)
    # --- T3 LLM metadata labelling (M1 Task 19D, contract §10): opt-in per policy with
    # detect.tiers: [..., llm_label]; field names, types and value shapes only, never values ---
    llm_label_max_fields: int = Field(default=40, ge=1, le=500)  # fields per labelling call
    llm_label_min_confidence: float = Field(default=0.7, ge=0.0, le=1.0)  # verdicts below: dropped
    # --- NDJSON streaming on /detect, /obfuscate, /synthesize, /reveal (M1, contract §10) ---
    ndjson_max_items: int = Field(default=1_000_000, ge=1, le=100_000_000)  # items per stream
    ndjson_chunk_items: int = Field(default=500, ge=1, le=100_000)  # items per worker-thread hop
    max_ndjson_mb: int = Field(default=256, ge=1, le=4096)  # body limit for application/x-ndjson
    # --- Idempotency-Key on the L1 routes (M1, contract §9/§10): a replica-local replay cache of
    # fingerprints (scope hash, body hash, trace id, time) — never key material; 0 disables it ---
    idempotency_ttl_seconds: int = Field(default=600, ge=0, le=86_400)
    idempotency_max_entries: int = Field(default=10_000, ge=1, le=1_000_000)

    # --- metadata database (M2; empty pg_host = Layer 1 only, no database at all) ---
    pg_host: str = ""
    pg_port: int = 5432
    pg_database: str = "dps"
    pg_schema: str = "dps"
    pg_user: str = "dps_app"
    pg_password: str = Field(default="", repr=False)
    pg_worker_user: str = ""  # falls back to pg_user
    pg_worker_password: str = Field(default="", repr=False)
    pg_migration_user: str = ""  # falls back to pg_user
    pg_migration_password: str = Field(default="", repr=False)
    pg_pool_min: int = 2
    pg_pool_max: int = 16
    pg_ssl_root_cert: str = ""
    rls_enabled: bool = True
    migrations_mode: str = "auto"  # auto | verify | off

    # --- job sealing (base64 of 32 bytes; production reads it from DPS_JOB_KEK_FILE) ---
    job_kek: str = Field(default="", repr=False)
    job_kek_previous: str = Field(default="", repr=False)  # M5 rotation

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
    worker_metrics_port: int = 9400  # 0 disables the worker's health/metrics server (tests)
    embedded_worker: bool = False  # local only; posture refuses it in staging/prod

    # --- connectors ---
    batch_rows: int = 1000
    partition_rows: int = 50000
    max_partitions: int = 256
    discovery_sample_rows: int = 2000
    preflight_rows: int = 500  # rows transformed at prepare before any write
    connector_connect_timeout_seconds: float = 10.0
    connector_statement_timeout_seconds: float = 300.0
    secret_dir: str = "/var/run/secrets/dps"
    inline_secrets_allowed: bool = True
    inline_secret_ttl_hours: int = 24

    # --- M3 connectors: Oracle and MongoDB (contract §10). Their credentials never come from
    # env vars: they come from the connection's secret_ref files (username, password, dsn, uri).
    oracle_pool_max: int = 4  # sessions per Oracle connection pool
    oracle_call_timeout_ms: int = 30000  # per round trip (the Wega rule)
    oracle_module: str = "dps"  # v$session.module of every DPS session
    mongo_server_selection_timeout_ms: int = 5000

    # --- retention and audit ---
    retention_days: int = 30
    retention_interval_hours: int = 6
    audit_retention_months: int = 13
    audit_strict: bool = False
    audit_queue_max: int = 10000  # DbAuditSink queue bound
    audit_flush_batch: int = 200
    audit_flush_ms: int = 250

    # --- artefacts and exports ---
    artefact_store: str = "local"  # local | s3
    artefact_dir: str = "./var/artefacts"
    s3_endpoint_url: str = ""
    s3_region: str = "us-east-1"
    s3_bucket: str = "dps-artefacts"
    s3_access_key_id: str = Field(default="", repr=False)
    s3_secret_access_key: str = Field(default="", repr=False)
    s3_force_path_style: bool = True
    export_root: str = "./var/exports"
    image_digest: str = ""

    # --- M5: tabular synthesis (contract §6.6, §10) ---
    synth_tabular_engine: str = "copula"  # comma list of enabled engines: copula | mostly; "" = off
    copula_fit_rows: int = Field(default=100_000, ge=1)  # rows fitted at prepare
    copula_holdout_rows: int = Field(default=20_000, ge=0)  # held out to measure privacy, unfitted
    copula_metrics_rows: int = Field(default=5_000, ge=1)  # preview rows run through the batch
    copula_rare_floor: float = 0.01  # categories below this share are folded away
    copula_max_resample: int = 8  # exact-row rejections per row before failing closed
    fidelity_column_limit: float = Field(default=0.05, ge=0.0, le=1.0)  # KS/TVD "within limit"
    fidelity_corr_limit: float = Field(default=0.10, ge=0.0, le=2.0)  # correlation-delta limit
    privacy_nndr_target: float = Field(default=0.95, gt=0.0)  # NNDR at/above which privacy passes
    privacy_nndr_enforce: bool = False  # true: a ratio below target fails prepare (privacy_gate)
    mostly_pool_rows: int = Field(default=20_000, ge=1)  # rows the MOSTLY engine draws per job
    mostly_max_training_minutes: int = Field(default=10, ge=1)
    # --- M5: external tokenization (spec §5.5) ---
    tokenizer_provider: str = ""  # "" (off) | http
    tokenizer_url: str = ""
    tokenizer_token: str = Field(default="", repr=False)  # bearer token; _FILE in production
    tokenizer_ca_file: str = ""
    tokenizer_timeout_seconds: float = Field(default=10.0, gt=0, le=300)
    tokenizer_batch: int = 500
    tokenizer_retries: int = Field(default=2, ge=0, le=10)
    #: S3 review F-11: which entity types and data elements (profiles) each tenant may tokenize —
    #: JSON ``{"<tenant>|*": ["ENTITY:profile", "ENTITY", …]}`` (``ENTITY`` alone = no profile).
    #: ``""`` = unrestricted (local/dev); staging/prod with a tokenizer must set it (posture).
    tokenizer_profiles: str = ""
    # --- M5: keys, reports, posture ---
    key_cache_seconds: int = 30
    key_max_ttl_days: int = Field(default=365, ge=1)
    key_rotation_max_age_days: int = Field(default=90, ge=1)
    key_rotation_max_grace_hours: int = Field(default=168, ge=0)
    report_pdf_enabled: bool = True
    rollup_retention_days: int = Field(default=400, ge=1)
    egress_tls_required: bool = False  # posture: true in staging/prod
    cors_origins: str = "*"  # posture: never "*" in staging/prod; "" = same-origin only
    # --- M5: strict verification and LLM exposure (spec §10, P1) ---
    verify_warn_allowed: bool = True  # posture: false in staging/prod (callers cannot ask "warn")
    llm_egress_check: Literal["strict", "warn"] = "strict"  # strict blocks, warn counts
    # --- M5: utility and membership inference (spec §5.7) ---
    utility_max_rows: int = Field(default=5_000, ge=1)  # TSTR train/test budget per job
    mia_min_holdout: int = Field(default=50, ge=1)  # fewer holdout rows: the MIA proxy is "not run"
    mia_auc_target: float = Field(default=0.60, gt=0.0, le=1.0)  # AUC at/below which it passes
    # --- M5: MOSTLY differential privacy (spec §5.6) ---
    mostly_dp_enabled: bool = False
    mostly_dp_max_epsilon: float = Field(default=5.0, gt=0)
    mostly_dp_delta: float = Field(default=1e-5, gt=0, lt=1)
    mostly_linked_child_rows: int = Field(default=200_000, ge=1)  # child rows read for linked fit

    @field_validator("env")
    @classmethod
    def _valid_env(cls, v: str) -> str:
        v = v.lower()
        if v not in ENVIRONMENTS:
            raise ValueError(f"env must be one of {ENVIRONMENTS}, got {v!r}")
        return v

    @field_validator("llm_provider")
    @classmethod
    def _valid_provider(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in LLM_PROVIDERS:
            raise ValueError(f"llm_provider must be one of {LLM_PROVIDERS}, got {v!r}")
        return v

    @field_validator("stellar_base_url", "coin_url", "azure_vision_endpoint", "tokenizer_url")
    @classmethod
    def _sane_url(cls, v: str) -> str:
        if not v:
            return v
        parsed = urlparse(v)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            # Do not echo the URL: it may carry userinfo credentials.
            raise ValueError("must be an http(s) URL with a host")
        return v

    @field_validator("ocr_provider")
    @classmethod
    def _valid_ocr_provider(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in ("", "off", "azure", "tesseract", "auto"):
            raise ValueError("ocr_provider must be one of '', off, azure, tesseract, auto")
        return v

    @field_validator("redact_image_format")
    @classmethod
    def _valid_image_format(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in ("jpeg", "png"):
            raise ValueError("redact_image_format must be 'jpeg' or 'png'")
        return v

    @field_validator("ocr_health_path")
    @classmethod
    def _valid_health_path(cls, v: str) -> str:
        if v and not re.fullmatch(r"/[A-Za-z0-9_./-]*", v):
            raise ValueError("ocr_health_path must be empty or an absolute path")
        return v

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
        if v not in MIGRATIONS_MODES:
            raise ValueError("migrations_mode must be auto, verify or off")
        return v

    @field_validator("artefact_store")
    @classmethod
    def _valid_store(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in ARTEFACT_STORES:
            raise ValueError("artefact_store must be local or s3")
        return v

    @field_validator("job_kek", "job_kek_previous")
    @classmethod
    def _valid_kek(cls, v: str) -> str:
        # Key material: the messages below never interpolate the value.
        v = v.strip()
        if not v:
            return v
        altchars = b"-_" if ("-" in v or "_" in v) else None
        try:
            raw = base64.b64decode(v + "=" * (-len(v) % 4), altchars=altchars, validate=True)
        except (binascii.Error, ValueError):
            raise ValueError(_KEK_MESSAGE) from None
        if len(raw) != 32:
            raise ValueError(_KEK_MESSAGE)
        return v

    @field_validator("synth_tabular_engine")
    @classmethod
    def _valid_tabular(cls, v: str) -> str:
        engines = [p.strip().lower() for p in v.split(",") if p.strip()]
        if any(e not in TABULAR_ENGINES for e in engines):
            raise ValueError("synth_tabular_engine accepts copula and mostly only")
        return ",".join(dict.fromkeys(engines))

    @field_validator("tokenizer_profiles")
    @classmethod
    def _tokenizer_profiles(cls, v: str) -> str:
        if v.strip():
            _parse_tokenizer_profiles(v)  # raises a value-free ValueError
        return v

    def tokenizer_profile_allowed(
        self, tenant: str | None, entity: str, profile: str | None
    ) -> bool:
        """F-11: may ``tenant`` tokenize ``entity`` under ``profile``? Always, when
        ``tokenizer_profiles`` is unset; else the pair must be listed for the tenant or ``*``."""
        if not self.tokenizer_profiles.strip():
            return True
        allowed = _parse_tokenizer_profiles(self.tokenizer_profiles)
        wanted = entity if profile is None else f"{entity}:{profile}"
        return wanted in allowed.get(tenant or "", frozenset()) | allowed.get("*", frozenset())

    @property
    def tokenizer_profiles_restricted(self) -> bool:
        """True when an allow-list is configured (profiles are then admin-chosen labels)."""
        return bool(self.tokenizer_profiles.strip())

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
        """The enabled tabular synthesis engines (``synth_tabular_engine`` as a list)."""
        return [p for p in self.synth_tabular_engine.split(",") if p]

    @property
    def cors_origin_list(self) -> list[str]:
        """``cors_origins`` split on commas, trimmed, empties dropped (``[]`` = same-origin)."""
        return [p.strip() for p in self.cors_origins.split(",") if p.strip()]

    @field_validator("oracle_pool_max")
    @classmethod
    def _oracle_pool_max(cls, v: int) -> int:
        if not 1 <= v <= 64:
            raise ValueError("oracle_pool_max must be between 1 and 64")
        return v

    @field_validator("oracle_call_timeout_ms")
    @classmethod
    def _oracle_call_timeout(cls, v: int) -> int:
        if not 1_000 <= v <= 3_600_000:
            raise ValueError("oracle_call_timeout_ms must be between 1000 and 3600000")
        return v

    @field_validator("oracle_module")
    @classmethod
    def _oracle_module(cls, v: str) -> str:
        # v$session.module holds 64 bytes; the DBA's audit filters on it, so keep a plain token.
        if not _ORACLE_MODULE.fullmatch(v):
            raise ValueError("oracle_module must be 1-48 characters of [A-Za-z0-9_.-]")
        return v

    @field_validator("mongo_server_selection_timeout_ms")
    @classmethod
    def _mongo_selection_timeout(cls, v: int) -> int:
        if not 500 <= v <= 120_000:
            raise ValueError("mongo_server_selection_timeout_ms must be between 500 and 120000")
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

    @property
    def is_production(self) -> bool:
        """True for the staging and prod environments (the production posture applies)."""
        return self.env in ("staging", "prod")

    @property
    def max_payload_bytes(self) -> int:
        """``max_payload_mb`` in bytes."""
        return self.max_payload_mb * 1024 * 1024

    @property
    def max_document_bytes(self) -> int:
        """``max_document_mb`` in bytes."""
        return self.max_document_mb * 1024 * 1024

    @property
    def max_ndjson_bytes(self) -> int:
        """``max_ndjson_mb`` in bytes."""
        return self.max_ndjson_mb * 1024 * 1024

    @property
    def ocr_configured(self) -> bool:
        """True when the azure provider has both an endpoint and a key."""
        return self.ocr_provider == "azure" and bool(
            self.azure_vision_endpoint and self.azure_vision_key
        )

    @property
    def ocr_engine(self) -> Literal["azure", "tesseract", "none"]:
        """The OCR engine ``ocr_provider`` resolves to (contract §10).

        ``""``/``off`` → none; ``azure`` → azure with an endpoint and a key, else none;
        ``tesseract`` → tesseract (readiness reports a missing binary); ``auto`` → azure with an
        endpoint and a key, else tesseract when ``tesseract_cmd`` is on ``PATH``, else none.
        """
        azure = bool(self.azure_vision_endpoint and self.azure_vision_key)
        if self.ocr_provider == "azure":
            return "azure" if azure else "none"
        if self.ocr_provider == "tesseract":
            return "tesseract"
        if self.ocr_provider == "auto":
            if azure:
                return "azure"
            return "tesseract" if shutil.which(self.tesseract_cmd) else "none"
        return "none"

    @property
    def ner_languages_list(self) -> list[str]:
        """``ner_languages`` split on commas, trimmed, empties dropped."""
        return [p.strip() for p in self.ner_languages.split(",") if p.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide cached settings."""
    return Settings()
