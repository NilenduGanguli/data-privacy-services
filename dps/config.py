"""Application settings — env-driven via pydantic-settings, prefix ``DPS_``.

Single source of truth for configuration, read once via :func:`get_settings`. Secrets are never
hard-coded; ``DPS_<NAME>_FILE`` reads a value from a mounted file (Kubernetes secret volumes, Vault
sinks), with a literal ``DPS_<NAME>`` env var taking precedence.
"""

from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from pydantic import Field, field_validator
from pydantic.fields import FieldInfo
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

ENV_PREFIX = "DPS_"
ENVIRONMENTS = ("local", "dev", "staging", "prod")
LLM_PROVIDERS = ("", "stellar", "vertex", "stub")


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
    # auto (azure if configured, else tesseract) — tesseract and auto arrive in M1 Task 19C and
    # behave as off until then.
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
    def ocr_configured(self) -> bool:
        """True when the azure provider has both an endpoint and a key."""
        return self.ocr_provider == "azure" and bool(
            self.azure_vision_endpoint and self.azure_vision_key
        )

    @property
    def ner_languages_list(self) -> list[str]:
        """``ner_languages`` split on commas, trimmed, empties dropped."""
        return [p.strip() for p in self.ner_languages.split(",") if p.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide cached settings."""
    return Settings()
