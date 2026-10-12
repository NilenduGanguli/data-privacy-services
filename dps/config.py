"""Application settings — env-driven via pydantic-settings, prefix ``DPS_``.

Single source of truth for configuration, read once via :func:`get_settings`. Secrets are never
hard-coded; ``DPS_<NAME>_FILE`` reads a value from a mounted file (Kubernetes secret volumes, Vault
sinks), with a literal ``DPS_<NAME>`` env var taking precedence.
"""

from __future__ import annotations

import base64
import binascii
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
_SCHEMA_RE = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
_KEK_MESSAGE = "job KEK must be base64 of exactly 32 bytes"


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
    api_keys: str = ""  # JSON list of {name, key | key_sha256, tenant_ids, scopes}
    bootstrap_api_key: str = ""  # one wildcard key for local demos; refused in production

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
    coin_client_id: str = ""  # base64-encoded, decoded at use (org convention)
    coin_client_secret: str = ""
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
    azure_vision_key: str = ""  # DPS_AZURE_VISION_KEY_FILE in prod; Ocp-Apim-Subscription-Key
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
    pg_password: str = ""
    pg_worker_user: str = ""  # falls back to pg_user
    pg_worker_password: str = ""
    pg_migration_user: str = ""  # falls back to pg_user
    pg_migration_password: str = ""
    pg_pool_min: int = 2
    pg_pool_max: int = 16
    pg_ssl_root_cert: str = ""
    rls_enabled: bool = True
    migrations_mode: str = "auto"  # auto | verify | off

    # --- job sealing (base64 of 32 bytes; production reads it from DPS_JOB_KEK_FILE) ---
    job_kek: str = ""
    job_kek_previous: str = ""  # M5 rotation

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
    s3_access_key_id: str = ""
    s3_secret_access_key: str = ""
    s3_force_path_style: bool = True
    export_root: str = "./var/exports"
    image_digest: str = ""

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

    @field_validator("stellar_base_url", "coin_url", "azure_vision_endpoint")
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
