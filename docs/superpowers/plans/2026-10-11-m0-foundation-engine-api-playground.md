# DPS M0 — Foundation, Privacy Engine core, Layer 1 API, Playground — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship milestone M0 of Data Privacy Services: a runnable, tested service that detects sensitive data in JSON and text, obfuscates it reversibly (caller-held key) or replaces it with consistent synthetic surrogates, reveals with the key, and exposes all of it through `/api/v1` and a console Playground — offline, in one container.

**Architecture:** One Python package `dps` (FastAPI) with a pure, dependency-injected Privacy Engine (`dps/engine`: detect → plan → transform → verify → report) that the Layer 1 routers call; an LLM gateway client (`dps/llm`) in the org's Stellar/COIN + Vertex + stub pattern that only ever receives placeholder text; a Vite + React console compiled into `frontend/dist` and served by the same process. No database in M0 — Layer 1 is stateless; API keys come from the environment.

**Tech Stack:** Python 3.12, FastAPI, pydantic v2 + pydantic-settings, httpx, structlog, prometheus-client, cryptography (HKDF, AES-SIV, RSA-OAEP), python-stdnum, Faker, phonenumbers, PyYAML; optional extras: presidio-analyzer + spaCy (`ner`), fastfpe (`fpe`, FF1, MIT), google-genai (`vertex`). Frontend: React 18, react-router-dom 6, TypeScript strict, Vite. Tooling: uv, ruff, mypy, pytest (+ pytest-asyncio).

**Spec:** `docs/specs/2026-10-11-data-privacy-services-design.md` (§3–§6, §9, §10 and milestone M0 in §11). Later milestones (M1 documents, M2 jobs + PostgreSQL, M3 Oracle + MongoDB, M4 UI scanner, M5 hardening + showcase) get their own plans derived from the same spec once M0 is green.

---

## Conventions for every task

- Work in `/Users/neelu/document_intelligence/data-privacy-services` (its own git repo; commits are authored as NilenduGanguli, no trailers).
- Environment: `uv venv --python 3.12 .venv && source .venv/bin/activate && uv pip install -e ".[dev]"` (Task 1 creates it; later tasks assume it is active).
- Run tests with `pytest -q`; lint with `ruff check dps tests && ruff format --check dps tests`; types with `mypy dps`. Every task ends green on all three before its commit.
- Type hints on every function; Google-style docstrings on public ones; no `Any` unless unavoidable; line length 100.
- **Never log, print, or put in an exception message a customer value, a key, or a token.** Tests assert this where it matters.
- Commit after each task with a conventional-commit message (`feat:`, `test:`, `chore:`, `docs:`).

## File structure (what M0 creates)

| Path | Responsibility |
|---|---|
| `pyproject.toml`, `.env.example`, `dps/__init__.py` | package, dependencies, tooling config, version |
| `dps/config.py`, `dps/posture.py` | `DPS_*` settings with `_FILE` secret indirection; production posture guard |
| `dps/observability.py` | structlog JSON logging, Prometheus metrics, readiness registry |
| `dps/engine/taxonomy.py` | entity families, sensitivity tiers, the entity registry |
| `dps/engine/findings.py` | `Locator`, `Finding`, `PlanEntry`, `Plan` models |
| `dps/engine/jsonpath.py` | flatten JSON to `(path, leaf)` pairs; get/set by path |
| `dps/engine/detect/checksums.py` | stdnum / Luhn / Verhoeff / mod-97 validators |
| `dps/engine/detect/patterns.py` | the regex catalogue with validators and context words; `Hit` |
| `dps/engine/detect/structural.py` | key-name dictionary (tier T0) |
| `dps/engine/detect/ner_presidio.py` | optional Presidio adapter (tier T2) with a no-op stub |
| `dps/engine/detect/custom.py` | custom recognizers (Presidio YAML shape) and declared fields (tier T4) |
| `dps/engine/detect/ensemble.py` | runs the tiers over JSON/text, merges scores, record sensitivity |
| `dps/engine/policy.py`, `dps/engine/presets/*.yaml` | policy model, presets, `method_for()` |
| `dps/engine/transform/crypto.py` | key material, HKDF subkeys, AES-SIV, HMAC, base32 tokens, RSA wrap |
| `dps/engine/transform/tokens.py` | the `[[PII:TYPE:token]]` envelope |
| `dps/engine/transform/fpe.py` | optional FF1 adapter (digits and character classes) with domain guard |
| `dps/engine/transform/methods.py` | the method registry (reversible and irreversible) |
| `dps/engine/planner.py` | findings + policy → plan, with fallbacks and reasons |
| `dps/engine/transform/apply.py` | apply a plan to JSON/text; reveal hints; reveal |
| `dps/engine/verify.py`, `dps/engine/report.py` | leakage sweep; report and config hash |
| `dps/engine/synth/surrogates.py`, `dps/engine/synth/personas.py` | seeded, locale-aware, rule-valid surrogates; coherent personas |
| `dps/llm/gateway.py` | Stellar (COIN OAuth2) / Vertex / stub completions, placeholder-only |
| `dps/engine/synth/llm_text.py` | placeholder pipeline: findings → `{PERSON_1}` → optional LLM rewrite → fill → verify |
| `dps/engine/service.py` | `PrivacyEngine`: detect / plan / obfuscate / synthesize / reveal |
| `dps/auth.py`, `dps/ratelimit.py`, `dps/audit.py` | static API keys with tenant grants and scopes; token bucket; value-free audit events |
| `dps/layers/api/schemas.py`, `dps/layers/api/routers/{ops,detect,transform,policies}.py`, `dps/app.py` | the HTTP surface and app factory (SPA mount, CSP, middleware) |
| `frontend/**` | Vite + React console: Vault tokens, Layout, Overview-lite, Playground |
| `Dockerfile`, `docker/entrypoint.sh`, `docker-compose.yml`, `tools/smoke_test.py` | one image, local stack, end-to-end smoke |
| `HANDOFF.md`, `README.md`, `docs/technical/api-m0.md` | build/run/deploy facts and the API as built |
| `tests/**` | one test module per source module, plus `tests/test_api.py` and `tests/test_e2e_sample.py` |

Shared interfaces are defined once (Tasks 4, 11, 12, 15, 17, 19, 20) and reused by name everywhere after; if a later task needs a change, change the defining task's module and its tests together.

---

### Task 1: Repository scaffold and toolchain

**Files:**
- Create: `pyproject.toml`, `dps/__init__.py`, `dps/py.typed`, `.env.example`, `tests/__init__.py`, `tests/conftest.py`, `tests/test_package.py`

- [ ] **Step 1: Write the package and tooling files**

`pyproject.toml`:
```toml
[project]
name = "data-privacy-services"
version = "0.1.0"
description = "Data Privacy Services: obfuscation and synthetic data for JSON, text, documents, databases and UIs."
requires-python = ">=3.12"
readme = "README.md"
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.30",
    "pydantic>=2.7",
    "pydantic-settings>=2.3",
    "httpx>=0.27",
    "structlog>=24.1",
    "prometheus-client>=0.20",
    "python-stdnum>=1.20",      # SSN/SIN/CURP/RFC/Aadhaar/PAN/IBAN/BIC/NHS checksums
    "cryptography>=42",         # HKDF, AES-SIV, RSA-OAEP
    "faker>=26",                # locale-aware surrogates
    "phonenumbers>=8.13",       # phone validation + valid synthetic numbers
    "pyyaml>=6.0",              # policy presets and custom recognizers
    "python-multipart>=0.0.9",  # multipart uploads (documents, M1)
]

[project.optional-dependencies]
ner = ["presidio-analyzer>=2.2.355", "spacy>=3.7"]
fpe = ["fastfpe>=0.2.1"]        # FF1 (NIST SP 800-38G); enabled only with DPS_FPE_ENABLED=true
vertex = ["google-genai>=1.0"]  # laptop path to Gemini; production goes through Stellar
dev = ["pytest>=8.2", "pytest-asyncio>=0.23", "ruff>=0.5", "mypy>=1.10", "types-PyYAML"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["dps"]

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B"]
ignore = ["E501"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
markers = [
    "ner: needs the optional [ner] extra and spaCy models",
    "fpe: needs the optional [fpe] extra",
    "network: needs a live LLM gateway",
    "browser: needs Playwright (M4)",
    "integration: needs live databases (M2+)",
]

[tool.mypy]
python_version = "3.12"
ignore_missing_imports = true
```

`dps/__init__.py`:
```python
"""Data Privacy Services — obfuscation and synthetic data for the enterprise."""

__version__ = "0.1.0"
```

`dps/py.typed`: empty file.

`.env.example`:
```bash
# Data Privacy Services — local configuration (copy to .env). Every key is optional.
DPS_ENV=local
DPS_LOG_LEVEL=INFO
DPS_PORT=8400
# Auth: a bootstrap wildcard key for local demos ONLY (production posture refuses it).
DPS_BOOTSTRAP_API_KEY=dps_local_dev_key_change_me
# Or explicit keys (JSON): [{"name":"demo","key":"dps_...","tenant_ids":["*"],"scopes":["*"]}]
DPS_API_KEYS=
# Engine
DPS_DEFAULT_POLICY=gdpr-pseudonymise
DPS_MIN_SCORE=0.6
DPS_FPE_ENABLED=false          # FF1 stays a fallback until the license review approves it
DPS_DEFAULT_LOCALE=en_US
# LLM gateway: "" (off) | stellar | vertex | stub
DPS_LLM_PROVIDER=stub
DPS_LLM_MODEL=gemini-2.5-flash
DPS_LLM_TEXT_REWRITE=false
# Stellar (VDI only): base64-encoded COIN credentials as distributed, corporate CA bundle for API calls
DPS_STELLAR_BASE_URL=
DPS_COIN_URL=
DPS_COIN_CLIENT_ID=
DPS_COIN_CLIENT_SECRET=
DPS_COIN_SCOPE=
DPS_SSL_CERT_FILE=
# Vertex (laptop): GOOGLE_APPLICATION_CREDENTIALS + project
DPS_VERTEX_PROJECT=
DPS_VERTEX_LOCATION=global
```

`tests/__init__.py`: empty. `tests/conftest.py`:
```python
"""Shared fixtures: settings isolated from any .env on disk."""
from __future__ import annotations

import pytest

from dps.config import Settings


@pytest.fixture
def settings() -> Settings:
    """Deterministic local settings; never reads a .env file."""
    return Settings(_env_file=None, env="local", auth_enabled=False, llm_provider="stub")
```

`tests/test_package.py`:
```python
from dps import __version__


def test_version_is_semver() -> None:
    assert __version__.count(".") == 2
```

- [ ] **Step 2: Create the environment and install**

Run:
```bash
cd /Users/neelu/document_intelligence/data-privacy-services
uv venv --python 3.12 .venv && source .venv/bin/activate && uv pip install -e ".[dev]"
```
Expected: resolves and installs without errors (`python -c "import fastapi, stdnum, faker, cryptography"` prints nothing).

- [ ] **Step 3: Run the first test and the linters**

Run: `pytest -q && ruff check dps tests && mypy dps`
Expected: `1 passed` (conftest imports `dps.config`, which does not exist yet — so this run FAILS with `ModuleNotFoundError: dps.config`; that is the expected red for Task 2. Temporarily run `pytest -q -p no:cacheprovider tests/test_package.py --noconftest` to see `1 passed`.)

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml dps/__init__.py dps/py.typed .env.example tests/
git commit -m "chore: package scaffold, toolchain config, first test"
```

---

### Task 2: Settings and production posture

**Files:**
- Create: `dps/config.py`, `dps/posture.py`
- Test: `tests/test_config.py`, `tests/test_posture.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_config.py`:
```python
from __future__ import annotations

import pytest

from dps.config import Settings


def test_defaults_are_local_and_safe() -> None:
    s = Settings(_env_file=None)
    assert s.env == "local" and s.port == 8400 and s.auth_enabled is True
    assert s.fpe_enabled is False and s.llm_provider == ""
    assert s.is_production is False


def test_env_prefix_and_file_indirection(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    secret = tmp_path / "coin_secret"
    secret.write_text("c2VjcmV0\n", encoding="utf-8")
    monkeypatch.setenv("DPS_ENV", "dev")
    monkeypatch.setenv("DPS_COIN_CLIENT_SECRET_FILE", str(secret))
    s = Settings(_env_file=None)
    assert s.env == "dev"
    assert s.coin_client_secret == "c2VjcmV0"  # stripped, from the file


def test_explicit_env_var_beats_file(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    secret = tmp_path / "k"
    secret.write_text("from-file", encoding="utf-8")
    monkeypatch.setenv("DPS_COIN_SCOPE_FILE", str(secret))
    monkeypatch.setenv("DPS_COIN_SCOPE", "from-env")
    assert Settings(_env_file=None).coin_scope == "from-env"


@pytest.mark.parametrize("field,value", [("env", "qa"), ("llm_provider", "openai"), ("stellar_base_url", "not a url")])
def test_invalid_values_are_refused(field: str, value: str) -> None:
    with pytest.raises(ValueError):
        Settings(_env_file=None, **{field: value})


def test_derived_properties() -> None:
    s = Settings(_env_file=None, env="prod", max_payload_mb=2, ner_languages="en, es")
    assert s.is_production and s.max_payload_bytes == 2 * 1024 * 1024
    assert s.ner_languages_list == ["en", "es"]
```

`tests/test_posture.py`:
```python
from __future__ import annotations

import pytest

from dps.config import Settings
from dps.posture import PostureError, assert_production_posture


def _prod(**kw):  # noqa: ANN003 - test helper
    base = dict(_env_file=None, env="prod", auth_enabled=True, api_keys='[{"name":"k","key_sha256":"ab"}]',
                llm_provider="", audit_enabled=True)
    base.update(kw)
    return Settings(**base)


def test_local_is_never_checked() -> None:
    assert_production_posture(Settings(_env_file=None, env="local", auth_enabled=False))


def test_good_production_passes() -> None:
    assert_production_posture(_prod())


@pytest.mark.parametrize(
    "bad",
    [dict(auth_enabled=False), dict(bootstrap_api_key="x"), dict(api_keys=""), dict(llm_provider="stub"),
     dict(llm_provider="stellar", stellar_base_url="https://s", coin_url="https://c", ssl_cert_file=""),
     dict(audit_enabled=False)],
)
def test_bad_production_is_refused(bad: dict) -> None:
    with pytest.raises(PostureError):
        assert_production_posture(_prod(**bad))
```

- [ ] **Step 2: Run them to confirm they fail**

Run: `pytest -q tests/test_config.py tests/test_posture.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'dps.config'`.

- [ ] **Step 3: Write `dps/config.py`**

```python
"""Application settings — env-driven via pydantic-settings, prefix ``DPS_``.

Single source of truth for configuration, read once via :func:`get_settings`. Secrets are never
hard-coded; ``DPS_<NAME>_FILE`` reads a value from a mounted file (Kubernetes secret volumes, Vault
sinks), with a literal ``DPS_<NAME>`` env var taking precedence.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from pydantic import field_validator
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
        env_prefix=ENV_PREFIX, env_file=".env", env_file_encoding="utf-8",
        case_sensitive=False, extra="ignore",
    )

    @classmethod
    def settings_customise_sources(  # type: ignore[override]
        cls, settings_cls, init_settings, env_settings, dotenv_settings, file_secret_settings,
    ):
        # init kwargs > env vars > _FILE indirection > .env file > pydantic secrets dir
        return (init_settings, env_settings, _FileSecretSource(settings_cls), dotenv_settings,
                file_secret_settings)

    # --- app ---
    app_name: str = "data-privacy-services"
    env: str = "local"
    log_level: str = "INFO"
    host: str = "0.0.0.0"
    port: int = 8400

    # --- auth (static key store in M0; a DB-backed store arrives with jobs in M2) ---
    auth_enabled: bool = True
    api_keys: str = ""              # JSON list of {name, key | key_sha256, tenant_ids, scopes}
    bootstrap_api_key: str = ""     # one wildcard key for local demos; refused in production

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
    fpe_enabled: bool = False        # FF1 is a fallback-only method until a license review flips this
    default_locale: str = "en_US"

    # --- LLM gateway (placeholder text only — see spec P1) ---
    llm_provider: str = ""
    llm_model: str = "gemini-2.5-flash"
    llm_timeout_seconds: float = 30.0
    llm_text_rewrite: bool = False
    stellar_base_url: str = ""
    coin_url: str = ""
    coin_client_id: str = ""         # base64-encoded, decoded at use (org convention)
    coin_client_secret: str = ""
    coin_scope: str = ""
    coin_token_ttl_seconds: int = 840
    ssl_cert_file: str = ""
    vertex_project: str = ""
    vertex_location: str = "global"

    # --- audit ---
    audit_enabled: bool = True

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

    @field_validator("stellar_base_url", "coin_url")
    @classmethod
    def _sane_url(cls, v: str) -> str:
        if not v:
            return v
        parsed = urlparse(v)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError(f"must be an http(s) URL with a host, got {v!r}")
        return v

    @property
    def is_production(self) -> bool:
        return self.env in ("staging", "prod")

    @property
    def max_payload_bytes(self) -> int:
        return self.max_payload_mb * 1024 * 1024

    @property
    def ner_languages_list(self) -> list[str]:
        return [p.strip() for p in self.ner_languages.split(",") if p.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide cached settings."""
    return Settings()
```

- [ ] **Step 4: Write `dps/posture.py`**

```python
"""Production posture guard — refuse to boot with an unsafe configuration.

Called before the FastAPI app is constructed (see ``dps/app.py``) so a bad staging/prod
configuration fails the process instead of serving traffic.
"""
from __future__ import annotations

from dps.config import Settings


class PostureError(RuntimeError):
    """The configuration violates the production posture."""


def posture_problems(settings: Settings) -> list[str]:
    """Return every violation for ``settings`` (empty outside staging/prod)."""
    if not settings.is_production:
        return []
    problems: list[str] = []
    if not settings.auth_enabled:
        problems.append("DPS_AUTH_ENABLED must be true")
    if settings.bootstrap_api_key:
        problems.append("DPS_BOOTSTRAP_API_KEY must be unset")
    if not settings.api_keys:
        problems.append("DPS_API_KEYS (or DPS_API_KEYS_FILE) must be set")
    if settings.llm_provider == "stub":
        problems.append("DPS_LLM_PROVIDER=stub is not allowed")
    if settings.llm_provider == "stellar":
        if not settings.ssl_cert_file:
            problems.append("DPS_SSL_CERT_FILE is required for the stellar provider")
        if not (settings.stellar_base_url and settings.coin_url):
            problems.append("DPS_STELLAR_BASE_URL and DPS_COIN_URL are required for stellar")
    if not settings.audit_enabled:
        problems.append("DPS_AUDIT_ENABLED must be true")
    return problems


def assert_production_posture(settings: Settings) -> None:
    """Raise :class:`PostureError` naming every violation."""
    problems = posture_problems(settings)
    if problems:
        raise PostureError("production posture violated: " + "; ".join(problems))
```

- [ ] **Step 5: Run the tests, lint, types**

Run: `pytest -q && ruff check dps tests && mypy dps`
Expected: all tests pass (`1 + 5 + 8 = 14 passed`), ruff clean, mypy clean.

- [ ] **Step 6: Commit**

```bash
git add dps/config.py dps/posture.py tests/test_config.py tests/test_posture.py
git commit -m "feat: DPS_ settings with _FILE secret indirection and production posture guard"
```

---

### Task 3: Observability — value-free logging, metrics, readiness

**Files:**
- Create: `dps/observability.py`
- Test: `tests/test_observability.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_observability.py`:
```python
from __future__ import annotations

from dps.observability import (
    LLM_RAW_PII,
    READINESS,
    Readiness,
    _drop_value_fields,
    configure_logging,
    metrics_payload,
)


def test_value_fields_never_reach_the_log() -> None:
    event = {"event": "obfuscate", "entity_type": "SSN", "value": "123-45-6789", "dek": "x", "count": 3}
    out = _drop_value_fields(None, "info", event)
    assert out == {"event": "obfuscate", "entity_type": "SSN", "count": 3}


def test_readiness_snapshot_requires_required_components() -> None:
    r = Readiness(required=("api-core",))
    assert r.snapshot()["ready"] is False
    r.set("api-core", True, "up")
    r.set("llm-gateway", False, "unconfigured", provider="")
    snap = r.snapshot()
    assert snap["ready"] is True
    assert snap["components"]["llm-gateway"] == {"ok": False, "detail": "unconfigured", "provider": ""}


def test_metrics_payload_exposes_the_zero_counter() -> None:
    configure_logging("INFO", json_output=True)
    body, content_type = metrics_payload()
    assert b"dps_llm_raw_pii_sent_total" in body
    assert content_type.startswith("text/plain")
    assert LLM_RAW_PII._value.get() == 0.0  # noqa: SLF001 - the whole point of the metric
    assert READINESS.snapshot()["components"] is not None
```

- [ ] **Step 2: Run to confirm failure**

Run: `pytest -q tests/test_observability.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'dps.observability'`.

- [ ] **Step 3: Write `dps/observability.py`**

```python
"""Structured logging, Prometheus metrics and the readiness registry.

Logging rule (spec §10): no customer value, key or token is ever logged. A processor drops the
field names that could carry one; code elsewhere logs counts, types, hashes and durations only.
"""
from __future__ import annotations

import logging
import sys
import threading
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

import structlog
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

#: Keys that are dropped from every log event, whatever the call site passed.
_VALUE_FIELDS = frozenset(
    {"value", "values", "data", "text", "token", "tokens", "dek", "key", "api_key", "password",
     "secret", "payload", "result", "original", "originals", "record", "key_material"}
)


def _drop_value_fields(_logger: Any, _method: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    """structlog processor: remove any field that could carry a sensitive value."""
    for name in list(event_dict):
        if name in _VALUE_FIELDS:
            event_dict.pop(name)
    return event_dict


def configure_logging(level: str = "INFO", *, json_output: bool = True) -> None:
    """Configure structlog (JSON to stdout in containers, console locally) and stdlib logging."""
    numeric = logging.getLevelNamesMapping().get(level.upper(), logging.INFO)
    logging.basicConfig(level=numeric, stream=sys.stderr, format="%(message)s")
    renderer: Any = structlog.processors.JSONRenderer() if json_output else structlog.dev.ConsoleRenderer()
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            _drop_value_fields,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(numeric),
        cache_logger_on_first_use=False,
    )


def get_logger(name: str) -> Any:
    """A bound structlog logger named after the module."""
    return structlog.get_logger(name)


# --- metrics -------------------------------------------------------------------------------
REQUESTS = Counter("dps_requests_total", "API requests by route and outcome", ["route", "outcome"])
FINDINGS = Counter("dps_findings_total", "Findings by entity type and detector", ["entity_type", "detector"])
TRANSFORMS = Counter("dps_transforms_total", "Values transformed by mode and method", ["mode", "method"])
LLM_CALLS = Counter("dps_llm_calls_total", "LLM gateway calls", ["provider", "purpose", "outcome"])
LLM_RAW_PII = Counter(
    "dps_llm_raw_pii_sent", "Raw sensitive values sent to the LLM gateway. Must stay 0 forever."
)
VERIFY_FAIL = Counter("dps_verification_failures_total", "Leakage sweeps that failed", ["mode"])
LATENCY = Histogram(
    "dps_request_seconds", "Request latency by route", ["route"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)


def metrics_payload() -> tuple[bytes, str]:
    """The Prometheus exposition body and its content type."""
    return generate_latest(), CONTENT_TYPE_LATEST


# --- readiness ----------------------------------------------------------------------------
@dataclass
class _Component:
    ok: bool
    detail: str
    meta: dict[str, Any] = field(default_factory=dict)


class Readiness:
    """Thread-safe registry of per-dependency readiness for ``/readyz``."""

    def __init__(self, required: Iterable[str] = ("api-core",)) -> None:
        self.required = tuple(required)
        self._lock = threading.Lock()
        self._components: dict[str, _Component] = {}

    def set(self, name: str, ok: bool, detail: str = "", **meta: Any) -> None:
        """Record the state of one component (meta must hold no values or secrets)."""
        with self._lock:
            self._components[name] = _Component(ok, detail, dict(meta))

    def snapshot(self) -> dict[str, Any]:
        """``{"ready": bool, "components": {name: {ok, detail, ...meta}}}``."""
        with self._lock:
            comps = {n: {"ok": c.ok, "detail": c.detail, **c.meta} for n, c in self._components.items()}
        ready = all(comps.get(n, {}).get("ok") is True for n in self.required)
        return {"ready": ready, "components": comps}


READINESS = Readiness()
```

Note: prometheus_client appends `_total` to a `Counter` name, so `LLM_RAW_PII` is declared as
`dps_llm_raw_pii_sent` and exposed as `dps_llm_raw_pii_sent_total` (the test checks the exposed name).

- [ ] **Step 4: Run tests, lint, types**

Run: `pytest -q tests/test_observability.py && ruff check dps tests && mypy dps`
Expected: `3 passed`, clean.

- [ ] **Step 5: Commit**

```bash
git add dps/observability.py tests/test_observability.py
git commit -m "feat: value-free structured logging, Prometheus metrics, readiness registry"
```

---

### Task 4: Taxonomy, findings model, JSON path helpers

**Files:**
- Create: `dps/engine/__init__.py`, `dps/engine/taxonomy.py`, `dps/engine/findings.py`, `dps/engine/jsonpath.py`
- Test: `tests/test_taxonomy.py`, `tests/test_findings.py`, `tests/test_jsonpath.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_taxonomy.py`:
```python
from __future__ import annotations

from dps.engine.taxonomy import (
    TAXONOMY,
    EntityInfo,
    Family,
    Sensitivity,
    entity_info,
    record_sensitivity,
    register_entity,
)


def test_core_entities_have_expected_tiers() -> None:
    assert entity_info("SSN").sensitivity is Sensitivity.CRITICAL
    assert entity_info("CARD_PAN").family is Family.FINANCIAL
    assert entity_info("EMAIL").sensitivity is Sensitivity.MEDIUM
    assert entity_info("API_KEY").sensitivity is Sensitivity.CRITICAL
    assert "AADHAAR" in TAXONOMY and "IN" in TAXONOMY["AADHAAR"].jurisdictions


def test_unknown_entities_default_to_medium_other() -> None:
    info = entity_info("EMPLOYEE_BADGE")
    assert info.family is Family.OTHER and info.sensitivity is Sensitivity.MEDIUM


def test_register_custom_entity() -> None:
    register_entity(EntityInfo("BUSINESS_SENSITIVE", Family.ORGANISATION, Sensitivity.HIGH, "owner-declared"))
    assert entity_info("BUSINESS_SENSITIVE").sensitivity is Sensitivity.HIGH


def test_record_sensitivity_uses_max_and_cooccurrence() -> None:
    assert record_sensitivity([]) is Sensitivity.LOW
    assert record_sensitivity(["EMAIL"]) is Sensitivity.MEDIUM
    assert record_sensitivity(["PERSON_NAME", "DATE_OF_BIRTH", "POSTAL_CODE"]) is Sensitivity.HIGH
    assert record_sensitivity(["PERSON_NAME", "POSTAL_CODE"]) is Sensitivity.MEDIUM
    assert record_sensitivity(["URL", "SSN"]) is Sensitivity.CRITICAL
    assert Sensitivity.CRITICAL.rank > Sensitivity.HIGH.rank > Sensitivity.MEDIUM.rank > Sensitivity.LOW.rank
```

`tests/test_findings.py`:
```python
from __future__ import annotations

from dps.engine.findings import Finding, Locator, Plan, PlanEntry, hash_value
from dps.engine.taxonomy import Sensitivity


def test_locator_whole_vs_span() -> None:
    assert Locator(path="$.ssn").whole is True
    assert Locator(path="$.notes", start=3, end=9).whole is False


def test_finding_defaults_and_hash_is_not_the_value() -> None:
    f = Finding(locator=Locator(path="$.ssn"), entity_type="SSN", score=0.99, detectors=["pattern:us_ssn"],
                sensitivity=Sensitivity.CRITICAL, value_hash=hash_value("123-45-6789"))
    assert f.evidence == [] and len(f.value_hash or "") == 16
    assert "123" not in (f.value_hash or "")
    assert hash_value(" 123-45-6789 ") == hash_value("123-45-6789")


def test_plan_counts_reversible_entries() -> None:
    f = Finding(locator=Locator(path="$.a"), entity_type="EMAIL", score=0.9, detectors=["x"],
                sensitivity=Sensitivity.MEDIUM)
    plan = Plan(mode="obfuscate", record_sensitivity=Sensitivity.MEDIUM, entries=[
        PlanEntry(finding=f, method="email", params={}, reason="policy:EMAIL", reversible=True),
        PlanEntry(finding=f, method="redact", params={}, reason="policy:EMAIL", reversible=False),
    ])
    assert plan.reversible_count == 1 and plan.methods_used() == {"email": 1, "redact": 1}
```

`tests/test_jsonpath.py`:
```python
from __future__ import annotations

import copy

import pytest

from dps.engine.jsonpath import flatten, get_path, parse_path, set_path

DOC = {"customer_id": "C1", "name": "Ann", "card": {"pan": "4111", "tags": ["a", {"k.v": 1}]}, "n": None}


def test_flatten_yields_every_scalar_leaf_with_a_path() -> None:
    paths = dict(flatten(DOC))
    assert paths["$.customer_id"] == "C1"
    assert paths["$.card.pan"] == "4111"
    assert paths["$.card.tags[0]"] == "a"
    assert paths["$.card.tags[1]['k.v']"] == 1
    assert paths["$.n"] is None
    assert flatten("plain text") == [("$", "plain text")]


def test_parse_get_set_round_trip() -> None:
    assert parse_path("$.card.tags[1]['k.v']") == ["card", "tags", 1, "k.v"]
    assert get_path(DOC, "$.card.tags[1]['k.v']") == 1
    doc = copy.deepcopy(DOC)
    set_path(doc, "$.card.pan", "tok")
    set_path(doc, "$.card.tags[0]", "b")
    assert doc["card"]["pan"] == "tok" and doc["card"]["tags"][0] == "b"
    assert DOC["card"]["pan"] == "4111"  # original untouched


def test_root_path_on_scalar_document() -> None:
    assert get_path("hello", "$") == "hello"
    with pytest.raises(KeyError):
        get_path(DOC, "$.missing")
```

- [ ] **Step 2: Run to confirm failure**

Run: `pytest -q tests/test_taxonomy.py tests/test_findings.py tests/test_jsonpath.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'dps.engine'`.

- [ ] **Step 3: Write `dps/engine/__init__.py` and `dps/engine/taxonomy.py`**

`dps/engine/__init__.py`:
```python
"""The Privacy Engine: detect → plan → transform → verify → report (pure, no I/O)."""
```

`dps/engine/taxonomy.py`:
```python
"""Entity taxonomy (spec §5.2): families, sensitivity tiers and the registry.

The registry is open: custom recognizers and declared fields may introduce entity names that are
not listed here; they default to ``Family.OTHER`` / ``Sensitivity.MEDIUM`` unless registered.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum


class Sensitivity(StrEnum):
    """Sensitivity ladder, LOW < MEDIUM < HIGH < CRITICAL."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"

    @property
    def rank(self) -> int:
        return _RANK[self]

    @classmethod
    def max_of(cls, items: Iterable[Sensitivity]) -> Sensitivity:
        best = cls.LOW
        for item in items:
            if item.rank > best.rank:
                best = item
        return best


_RANK = {Sensitivity.LOW: 0, Sensitivity.MEDIUM: 1, Sensitivity.HIGH: 2, Sensitivity.CRITICAL: 3}


class Family(StrEnum):
    IDENTITY = "identity"
    GOVERNMENT_ID = "government_id"
    FINANCIAL = "financial"
    CONTACT = "contact"
    TEMPORAL = "temporal"
    ONLINE = "online"
    SECRET = "secret"
    HEALTH = "health"
    ORGANISATION = "organisation"
    LOCATION = "location"
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class EntityInfo:
    """One entity type."""

    name: str
    family: Family
    sensitivity: Sensitivity
    description: str = ""
    jurisdictions: tuple[str, ...] = ()


_C, _H, _M, _L = Sensitivity.CRITICAL, Sensitivity.HIGH, Sensitivity.MEDIUM, Sensitivity.LOW
_ENTRIES: tuple[EntityInfo, ...] = (
    # identity
    EntityInfo("PERSON_NAME", Family.IDENTITY, _M, "a natural person's name"),
    EntityInfo("DATE_OF_BIRTH", Family.IDENTITY, _M, "birth date"),
    EntityInfo("GENDER", Family.IDENTITY, _L),
    EntityInfo("NATIONALITY", Family.IDENTITY, _L),
    EntityInfo("AGE", Family.IDENTITY, _L),
    # government identifiers
    EntityInfo("SSN", Family.GOVERNMENT_ID, _C, "US Social Security Number", ("US",)),
    EntityInfo("ITIN", Family.GOVERNMENT_ID, _C, "US taxpayer identification number", ("US",)),
    EntityInfo("EIN", Family.GOVERNMENT_ID, _H, "US employer identification number", ("US",)),
    EntityInfo("PASSPORT", Family.GOVERNMENT_ID, _C, "passport number"),
    EntityInfo("DRIVER_LICENSE", Family.GOVERNMENT_ID, _H),
    EntityInfo("SIN", Family.GOVERNMENT_ID, _C, "Canadian Social Insurance Number", ("CA",)),
    EntityInfo("BN", Family.GOVERNMENT_ID, _H, "Canadian business number", ("CA",)),
    EntityInfo("CURP", Family.GOVERNMENT_ID, _C, "Mexican CURP", ("MX",)),
    EntityInfo("RFC", Family.GOVERNMENT_ID, _C, "Mexican RFC", ("MX",)),
    EntityInfo("INE_CLAVE", Family.GOVERNMENT_ID, _C, "Mexican INE clave de elector", ("MX",)),
    EntityInfo("AADHAAR", Family.GOVERNMENT_ID, _C, "Indian Aadhaar number", ("IN",)),
    EntityInfo("PAN_IN", Family.GOVERNMENT_ID, _C, "Indian permanent account number", ("IN",)),
    EntityInfo("VOTER_ID", Family.GOVERNMENT_ID, _H, "Indian voter id (EPIC)", ("IN",)),
    EntityInfo("NINO", Family.GOVERNMENT_ID, _C, "UK national insurance number", ("GB",)),
    EntityInfo("NHS_NUMBER", Family.GOVERNMENT_ID, _C, "UK NHS number", ("GB",)),
    EntityInfo("NATIONAL_ID", Family.GOVERNMENT_ID, _C, "other national identifier"),
    EntityInfo("MRZ", Family.GOVERNMENT_ID, _C, "machine-readable zone"),
    # financial
    EntityInfo("CARD_PAN", Family.FINANCIAL, _C, "payment card number"),
    EntityInfo("CARD_EXPIRY", Family.FINANCIAL, _H),
    EntityInfo("CVV", Family.FINANCIAL, _C),
    EntityInfo("IBAN", Family.FINANCIAL, _H),
    EntityInfo("SWIFT_BIC", Family.FINANCIAL, _L),
    EntityInfo("ACCOUNT_NUMBER", Family.FINANCIAL, _H),
    EntityInfo("ROUTING_ABA", Family.FINANCIAL, _L, "", ("US",)),
    EntityInfo("IFSC", Family.FINANCIAL, _L, "", ("IN",)),
    EntityInfo("SORT_CODE", Family.FINANCIAL, _L, "", ("GB",)),
    EntityInfo("CRYPTO_ADDRESS", Family.FINANCIAL, _H),
    EntityInfo("AMOUNT", Family.FINANCIAL, _L),
    # contact
    EntityInfo("EMAIL", Family.CONTACT, _M),
    EntityInfo("PHONE", Family.CONTACT, _M),
    EntityInfo("STREET_ADDRESS", Family.CONTACT, _M),
    EntityInfo("POSTAL_CODE", Family.CONTACT, _L),
    EntityInfo("CITY", Family.CONTACT, _L),
    EntityInfo("GEO_COORDINATE", Family.CONTACT, _M),
    # temporal / online
    EntityInfo("DATE", Family.TEMPORAL, _L, "a date that is not known to be a birth date"),
    EntityInfo("IP_ADDRESS", Family.ONLINE, _M),
    EntityInfo("MAC_ADDRESS", Family.ONLINE, _M),
    EntityInfo("URL", Family.ONLINE, _L),
    EntityInfo("USERNAME", Family.ONLINE, _M),
    EntityInfo("DEVICE_ID", Family.ONLINE, _M),
    EntityInfo("COOKIE_ID", Family.ONLINE, _M),
    # secrets
    EntityInfo("API_KEY", Family.SECRET, _C),
    EntityInfo("PASSWORD", Family.SECRET, _C),
    EntityInfo("JWT", Family.SECRET, _C),
    EntityInfo("PRIVATE_KEY", Family.SECRET, _C),
    # health
    EntityInfo("MEDICAL_RECORD_NUMBER", Family.HEALTH, _H),
    EntityInfo("DIAGNOSIS_CODE", Family.HEALTH, _H),
    EntityInfo("MEDICATION", Family.HEALTH, _H),
    # organisation / location
    EntityInfo("ORG_NAME", Family.ORGANISATION, _L),
    EntityInfo("EMPLOYEE_ID", Family.ORGANISATION, _M),
    EntityInfo("CUSTOMER_ID", Family.ORGANISATION, _M),
    EntityInfo("CASE_ID", Family.ORGANISATION, _L),
    EntityInfo("LOCATION", Family.LOCATION, _L, "a place name from NER"),
)

TAXONOMY: dict[str, EntityInfo] = {e.name: e for e in _ENTRIES}

#: Entities whose co-occurrence raises a record's sensitivity (spec §5.2).
QUASI_IDENTIFIERS = frozenset({"DATE_OF_BIRTH", "STREET_ADDRESS", "POSTAL_CODE", "CITY", "GEO_COORDINATE"})


def entity_info(name: str) -> EntityInfo:
    """Look up an entity type; unknown names are MEDIUM / OTHER."""
    return TAXONOMY.get(name) or EntityInfo(name, Family.OTHER, Sensitivity.MEDIUM, "custom or declared")


def register_entity(info: EntityInfo) -> None:
    """Register (or replace) a custom entity type."""
    TAXONOMY[info.name] = info


def record_sensitivity(entity_types: Iterable[str]) -> Sensitivity:
    """Max tier across the record, raised for quasi-identifier co-occurrence."""
    types = set(entity_types)
    tier = Sensitivity.max_of(entity_info(t).sensitivity for t in types)
    quasi = types & QUASI_IDENTIFIERS
    if "PERSON_NAME" in types and "DATE_OF_BIRTH" in types and (quasi - {"DATE_OF_BIRTH"}):
        tier = Sensitivity.max_of([tier, Sensitivity.HIGH])
    elif "PERSON_NAME" in types and quasi:
        tier = Sensitivity.max_of([tier, Sensitivity.MEDIUM])
    return tier
```

- [ ] **Step 4: Write `dps/engine/findings.py`**

```python
"""Findings and plans — the data the detectors produce and the planner consumes."""
from __future__ import annotations

import hashlib
from collections import Counter
from typing import Any, Literal

from pydantic import BaseModel, Field

from dps.engine.taxonomy import Sensitivity


def hash_value(value: Any) -> str:
    """A short, salt-free content hash for dedupe and reports. Never reversible to the value in
    practice for high-entropy data; it is a correlation id, not a secret, and is never logged with
    the value."""
    canon = str(value).strip().lower()
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()[:16]


class Locator(BaseModel):
    """Where a finding lives: a JSON path, optionally narrowed to a span within a string."""

    path: str = "$"
    start: int | None = None
    end: int | None = None

    @property
    def whole(self) -> bool:
        return self.start is None


class Finding(BaseModel):
    """One detected sensitive value (never the value itself)."""

    locator: Locator
    entity_type: str
    score: float = Field(ge=0.0, le=1.0)
    detectors: list[str]
    sensitivity: Sensitivity
    evidence: list[str] = Field(default_factory=list)
    value_hash: str | None = None


class PlanEntry(BaseModel):
    """One finding and the method the policy resolved for it."""

    finding: Finding
    method: str
    params: dict[str, Any] = Field(default_factory=dict)
    reason: str
    reversible: bool


class Plan(BaseModel):
    """The resolved plan for one request."""

    mode: Literal["obfuscate", "synthesize"]
    record_sensitivity: Sensitivity
    entries: list[PlanEntry] = Field(default_factory=list)

    @property
    def reversible_count(self) -> int:
        return sum(1 for e in self.entries if e.reversible)

    def methods_used(self) -> dict[str, int]:
        return dict(Counter(e.method for e in self.entries))
```

- [ ] **Step 5: Write `dps/engine/jsonpath.py`**

```python
"""Minimal JSON path helpers: flatten to ``(path, leaf)`` pairs, get and set by path.

Paths look like ``$.customer.card.pan``, ``$.tags[0]`` and ``$['odd key']`` — enough for records,
never a query language.
"""
from __future__ import annotations

import re
from typing import Any

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_TOKEN = re.compile(r"\.([A-Za-z_][A-Za-z0-9_]*)|\[(\d+)\]|\['((?:[^'\\]|\\.)*)'\]")

Scalar = str | int | float | bool | None


def _segment(key: str) -> str:
    if _IDENT.match(key):
        return f".{key}"
    escaped = key.replace("\\", "\\\\").replace("'", "\\'")
    return f"['{escaped}']"


def flatten(obj: Any, prefix: str = "$") -> list[tuple[str, Scalar]]:
    """Every scalar leaf of ``obj`` with its path, in document order."""
    out: list[tuple[str, Scalar]] = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            out.extend(flatten(value, prefix + _segment(str(key))))
    elif isinstance(obj, list):
        for index, value in enumerate(obj):
            out.extend(flatten(value, f"{prefix}[{index}]"))
    else:
        out.append((prefix, obj))
    return out


def parse_path(path: str) -> list[str | int]:
    """``"$.a.b[1]['k']"`` → ``["a", "b", 1, "k"]``. Raises ``ValueError`` on malformed paths."""
    if not path.startswith("$"):
        raise ValueError(f"path must start with '$': {path!r}")
    rest, out, pos = path[1:], [], 0
    out_list: list[str | int] = out
    while pos < len(rest):
        match = _TOKEN.match(rest, pos)
        if not match:
            raise ValueError(f"malformed path at offset {pos + 1}: {path!r}")
        ident, index, quoted = match.groups()
        if ident is not None:
            out_list.append(ident)
        elif index is not None:
            out_list.append(int(index))
        else:
            out_list.append(quoted.replace("\\'", "'").replace("\\\\", "\\"))
        pos = match.end()
    return out_list


def get_path(obj: Any, path: str) -> Any:
    """Return the value at ``path``; ``KeyError``/``IndexError`` when absent."""
    node = obj
    for part in parse_path(path):
        node = node[part]
    return node


def set_path(obj: Any, path: str, value: Any) -> None:
    """Replace the value at ``path`` in place. The root (``$``) of a scalar document cannot be
    reassigned in place; callers handle ``path == "$"`` themselves."""
    parts = parse_path(path)
    if not parts:
        raise ValueError("cannot set the root path in place")
    node = obj
    for part in parts[:-1]:
        node = node[part]
    node[parts[-1]] = value
```

- [ ] **Step 6: Run tests, lint, types**

Run: `pytest -q && ruff check dps tests && mypy dps`
Expected: all green (`27 passed`).

- [ ] **Step 7: Commit**

```bash
git add dps/engine tests/test_taxonomy.py tests/test_findings.py tests/test_jsonpath.py
git commit -m "feat(engine): entity taxonomy, findings/plan models, JSON path helpers"
```

---

### Task 5: Checksum validators

**Files:**
- Create: `dps/engine/detect/__init__.py`, `dps/engine/detect/checksums.py`
- Test: `tests/test_checksums.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_checksums.py`:
```python
from __future__ import annotations

import pytest

from dps.engine.detect.checksums import (
    VALIDATORS,
    luhn_check_digit,
    luhn_valid,
    valid_aba,
    validate,
    verhoeff_check_digit,
)


@pytest.mark.parametrize(
    "entity,good,bad",
    [
        ("SSN", "123-45-6789", "666-12-3456"),
        ("ITIN", "912-70-1234", "123-45-6789"),
        ("EIN", "12-3456789", "07-1234567"),
        ("SIN", "046 454 286", "046 454 287"),
        ("CURP", "BOXW310820HNERXN09", "BOXW310820HNERXN00"),
        ("RFC", "GODE561231GR8", "GODE561231GR0"),
        ("AADHAAR", "2341 2341 2346", "2341 2341 2345"),
        ("PAN_IN", "ACUPA7085R", "ACUP17085R"),
        ("NHS_NUMBER", "943 476 5919", "943 476 5910"),
        ("IBAN", "GB82 WEST 1234 5698 7654 32", "GB82 WEST 1234 5698 7654 33"),
        ("SWIFT_BIC", "DEUTDEFF", "DEUTDEF"),
        ("CARD_PAN", "4111 1111 1111 1111", "4111 1111 1111 1112"),
        ("NINO", "AB123456C", "BG123456C"),
        ("ROUTING_ABA", "021000021", "021000022"),
    ],
)
def test_validators_accept_known_good_and_reject_bad(entity: str, good: str, bad: str) -> None:
    assert entity in VALIDATORS
    assert validate(entity, good) is True
    assert validate(entity, bad) is False


def test_unknown_entity_has_no_validator() -> None:
    assert validate("PERSON_NAME", "Ann") is None


def test_luhn_and_verhoeff_helpers() -> None:
    assert luhn_valid("4111111111111111") and not luhn_valid("4111111111111112")
    assert luhn_check_digit("411111111111111") == "1"
    assert verhoeff_check_digit("23412341234") == "6"
    assert valid_aba("021000021") and not valid_aba("123456789")
```

- [ ] **Step 2: Run to confirm failure**

Run: `pytest -q tests/test_checksums.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'dps.engine.detect'`.

- [ ] **Step 3: Write the module**

`dps/engine/detect/__init__.py`:
```python
"""Detectors: structural (T0), patterns + checksums (T1), NER (T2), custom (T4), ensemble."""
```

`dps/engine/detect/checksums.py`:
```python
"""Checksum validators — python-stdnum wrappers plus Luhn, Verhoeff and ABA helpers.

Every function takes the raw matched string (spaces and dashes allowed) and returns a bool; a
validator never raises. ``validate`` returns ``None`` for entity types without a checksum.
"""
from __future__ import annotations

import importlib
import re
from collections.abc import Callable

_DIGITS = re.compile(r"\D")


def _stdnum(module: str, value: str) -> bool:
    try:
        mod = importlib.import_module(module)
        return bool(mod.is_valid(value))
    except Exception:  # noqa: BLE001 - any parse/import problem means "not validated"
        return False


def luhn_valid(digits: str) -> bool:
    """Luhn mod-10 over a digit string (non-digits ignored)."""
    d = _DIGITS.sub("", digits)
    if len(d) < 2:
        return False
    total = 0
    for i, ch in enumerate(reversed(d)):
        n = ord(ch) - 48
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def luhn_check_digit(partial: str) -> str:
    """The digit that makes ``partial + digit`` Luhn-valid."""
    for candidate in "0123456789":
        if luhn_valid(partial + candidate):
            return candidate
    raise ValueError("unreachable: one digit always satisfies Luhn")


def verhoeff_check_digit(partial: str) -> str:
    """Verhoeff check digit (used by Aadhaar), via stdnum."""
    from stdnum import verhoeff

    return str(verhoeff.calc_check_digit(_DIGITS.sub("", partial)))


def valid_card_pan(value: str) -> bool:
    d = _DIGITS.sub("", value)
    return 13 <= len(d) <= 19 and len(set(d)) > 1 and luhn_valid(d)


def valid_aba(value: str) -> bool:
    """US ABA routing number checksum."""
    d = _DIGITS.sub("", value)
    if len(d) != 9:
        return False
    n = [int(c) for c in d]
    return (3 * (n[0] + n[3] + n[6]) + 7 * (n[1] + n[4] + n[7]) + (n[2] + n[5] + n[8])) % 10 == 0


_NINO = re.compile(r"^(?!BG|GB|NK|KN|TN|NT|ZZ)[A-CEGHJ-PR-TW-Z][A-CEGHJ-NPR-TW-Z]\d{6}[A-D]$")


def valid_nino(value: str) -> bool:
    return bool(_NINO.match(value.replace(" ", "").upper()))


VALIDATORS: dict[str, Callable[[str], bool]] = {
    "SSN": lambda v: _stdnum("stdnum.us.ssn", v),
    "ITIN": lambda v: _stdnum("stdnum.us.itin", v),
    "EIN": lambda v: _stdnum("stdnum.us.ein", v),
    "SIN": lambda v: _stdnum("stdnum.ca.sin", v),
    "CURP": lambda v: _stdnum("stdnum.mx.curp", v),
    "RFC": lambda v: _stdnum("stdnum.mx.rfc", v),
    "AADHAAR": lambda v: _stdnum("stdnum.in_.aadhaar", v),
    "PAN_IN": lambda v: _stdnum("stdnum.in_.pan", v),
    "NHS_NUMBER": lambda v: _stdnum("stdnum.gb.nhs", v),
    "IBAN": lambda v: _stdnum("stdnum.iban", v),
    "SWIFT_BIC": lambda v: _stdnum("stdnum.bic", v),
    "CARD_PAN": valid_card_pan,
    "NINO": valid_nino,
    "ROUTING_ABA": valid_aba,
}


def validate(entity_type: str, value: str) -> bool | None:
    """True/False when ``entity_type`` has a checksum, ``None`` otherwise."""
    fn = VALIDATORS.get(entity_type)
    return None if fn is None else fn(value)
```

- [ ] **Step 4: Run tests, lint, types**

Run: `pytest -q tests/test_checksums.py && ruff check dps tests && mypy dps`
Expected: `16 passed`, clean. If one stdnum example fails on your stdnum version, check `python -c "from stdnum.mx import curp; print(curp.validate('BOXW310820HNERXN09'))"` and replace the sample with the one in that module's docstring — never weaken the validator.

- [ ] **Step 5: Commit**

```bash
git add dps/engine/detect tests/test_checksums.py
git commit -m "feat(detect): checksum validators for government, financial and card identifiers"
```

---

### Task 6: Pattern catalogue (tier T1) and phone matching

**Files:**
- Create: `dps/engine/detect/patterns.py`
- Test: `tests/test_patterns.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_patterns.py`:
```python
from __future__ import annotations

import pytest

from dps.engine.detect.patterns import Hit, resolve_overlaps, scan_phones, scan_text


def _types(text: str, **kw: object) -> dict[str, Hit]:
    return {h.entity_type: h for h in scan_text(text, **kw)}  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "text,entity,validated",
    [
        ("ssn 123-45-6789", "SSN", True),
        ("ITIN 912-70-1234 on file", "ITIN", True),
        ("EIN: 12-3456789", "EIN", True),
        ("CURP BOXW310820HNERXN09", "CURP", True),
        ("Aadhaar 2341 2341 2346", "AADHAAR", True),
        ("PAN ACUPA7085R", "PAN_IN", True),
        ("card 4111 1111 1111 1111", "CARD_PAN", True),
        ("IBAN GB82 WEST 1234 5698 7654 32", "IBAN", True),
        ("mail me at ann.k@example-mail.in", "EMAIL", False),
        ("server 10.20.30.40 responded", "IP_ADDRESS", False),
        ("token eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk", "JWT", False),
        ("key AKIAIOSFODNN7EXAMPLE", "API_KEY", False),
        ("NI number AB 12 34 56 C", "NINO", True),
        ("NHS 943 476 5919", "NHS_NUMBER", True),
    ],
)
def test_catalogue_finds_and_validates(text: str, entity: str, validated: bool) -> None:
    hits = _types(text)
    assert entity in hits, hits
    assert hits[entity].validated is validated
    assert hits[entity].score >= 0.6


def test_itin_wins_over_ssn_shape_and_invalid_card_is_low() -> None:
    hits = scan_text("912-70-1234")
    assert [h.entity_type for h in hits] == ["ITIN"]
    assert all(h.entity_type != "CARD_PAN" or h.score < 0.3 for h in scan_text("4111 1111 1111 1112"))


def test_context_promotes_date_to_date_of_birth() -> None:
    assert _types("DOB: 1988-03-14")["DATE_OF_BIRTH"].score >= 0.8
    assert "DATE" in _types("shipped 2024-01-02")
    assert "DATE_OF_BIRTH" in _types("1988-03-14", key_hint="birth_date")


def test_needs_context_patterns_stay_silent_without_it() -> None:
    assert "POSTAL_CODE" not in _types("ref 56001 approved")
    assert "POSTAL_CODE" in _types("PIN code 560001")
    assert "ROUTING_ABA" not in _types("021000021")
    assert "ROUTING_ABA" in _types("routing 021000021")


def test_phone_matcher_uses_region() -> None:
    hits = scan_phones("call +91 98451 20377 or (415) 555-0132 today", region="US")
    assert len(hits) == 2 and all(h.entity_type == "PHONE" for h in hits)
    assert scan_phones("order 98765 shipped", region="US") == []


def test_resolve_overlaps_prefers_validated_then_score_then_length() -> None:
    a = Hit("SSN", 0, 11, 0.5, "pattern:x", "", False)
    b = Hit("ITIN", 0, 11, 0.98, "pattern:y", "", True)
    c = Hit("DATE", 20, 30, 0.6, "pattern:z", "", False)
    assert resolve_overlaps([a, b, c]) == [b, c]
```

- [ ] **Step 2: Run to confirm failure**

Run: `pytest -q tests/test_patterns.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'dps.engine.detect.patterns'`.

- [ ] **Step 3: Write `dps/engine/detect/patterns.py`**

```python
"""Tier T1 — the regex catalogue with checksum validation and context words.

Each :class:`PatternSpec` names an entity, a compiled regex (group 1 = the value), a base score,
an optional validator (by entity key in :mod:`.checksums`), context words that raise the score,
and whether the pattern is too generic to fire without context. ``scan_text`` returns
non-overlapping :class:`Hit` objects; phones are matched by ``phonenumbers`` in ``scan_phones``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import phonenumbers

from dps.engine.detect.checksums import validate

CONTEXT_WINDOW = 40
CONTEXT_BONUS = 0.1
PROMOTION_BONUS = 0.25
INVALID_FACTOR = 0.45


@dataclass(frozen=True, slots=True)
class Hit:
    """A detector hit: span, type, score and evidence (never the value)."""

    entity_type: str
    start: int
    end: int
    score: float
    detector: str
    evidence: str
    validated: bool = False


@dataclass(frozen=True, slots=True)
class PatternSpec:
    entity_type: str
    name: str
    regex: re.Pattern[str]
    base_score: float
    validator: str | None = None
    validated_score: float = 0.98
    context: tuple[str, ...] = ()
    needs_context: bool = False
    promote_to: str | None = None
    promote_context: tuple[str, ...] = ()


def _p(entity: str, name: str, pattern: str, base: float, **kw: object) -> PatternSpec:
    return PatternSpec(entity, name, re.compile(pattern), base, **kw)  # type: ignore[arg-type]


_DOB_CTX = ("dob", "birth", "born", "nacimiento", "fecha de nac", "birthday", "janm")

CATALOGUE: tuple[PatternSpec, ...] = (
    _p("SSN", "us_ssn", r"(?<![\d-])(\d{3}-\d{2}-\d{4})(?![\d-])", 0.7, validator="SSN",
       context=("ssn", "social security", "seguro social")),
    _p("SSN", "us_ssn_bare", r"(?<!\d)(\d{9})(?!\d)", 0.4, validator="SSN", needs_context=True,
       context=("ssn", "social security")),
    _p("ITIN", "us_itin", r"(?<![\d-])(9\d{2}-\d{2}-\d{4})(?![\d-])", 0.6, validator="ITIN",
       context=("itin", "taxpayer")),
    _p("EIN", "us_ein", r"(?<![\d-])(\d{2}-\d{7})(?![\d-])", 0.5, validator="EIN",
       context=("ein", "employer id", "tax id")),
    _p("SIN", "ca_sin", r"(?<![\d-])(\d{3}[- ]\d{3}[- ]\d{3})(?![\d-])", 0.5, validator="SIN",
       context=("sin", "social insurance", "nas")),
    _p("CURP", "mx_curp", r"\b([A-Z][AEIOUX][A-Z]{2}\d{6}[HM][A-Z]{5}[0-9A-Z]\d)\b", 0.8,
       validator="CURP", context=("curp", "renapo")),
    _p("RFC", "mx_rfc", r"\b([A-ZÑ&]{3,4}\d{6}[0-9A-Z]{3})\b", 0.5, validator="RFC",
       context=("rfc", "registro federal", "sat")),
    _p("INE_CLAVE", "mx_ine", r"\b([A-Z]{6}\d{8}[HM]\d{3})\b", 0.55,
       context=("clave de elector", "ine", "credencial")),
    _p("AADHAAR", "in_aadhaar", r"(?<!\d)([2-9]\d{3}[ -]?\d{4}[ -]?\d{4})(?!\d)", 0.6,
       validator="AADHAAR", context=("aadhaar", "aadhar", "uidai", "uid")),
    _p("PAN_IN", "in_pan", r"\b([A-Z]{3}[ABCFGHLJPT][A-Z]\d{4}[A-Z])\b", 0.6, validator="PAN_IN",
       context=("pan", "permanent account", "income tax")),
    _p("NINO", "gb_nino", r"\b([A-CEGHJ-PR-TW-Z]{2} ?\d{2} ?\d{2} ?\d{2} ?[A-D])\b", 0.6,
       validator="NINO", context=("national insurance", "nino", "ni number")),
    _p("NHS_NUMBER", "gb_nhs", r"(?<!\d)(\d{3} ?\d{3} ?\d{4})(?!\d)", 0.3, validator="NHS_NUMBER",
       needs_context=True, context=("nhs",)),
    _p("CARD_PAN", "card_pan", r"(?<![\d-])((?:\d[ -]?){12,18}\d)(?![\d-])", 0.45,
       validator="CARD_PAN", validated_score=0.95,
       context=("card", "credit", "debit", "visa", "mastercard", "tarjeta", "pan")),
    _p("CARD_EXPIRY", "card_expiry", r"\b((?:0[1-9]|1[0-2])/(?:\d{2}|\d{4}))\b", 0.5,
       needs_context=True, context=("exp", "expiry", "valid thru", "vencimiento")),
    _p("IBAN", "iban", r"\b([A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,4})?)\b", 0.5,
       validator="IBAN", context=("iban",)),
    _p("SWIFT_BIC", "bic", r"\b([A-Z]{6}[A-Z2-9][A-NP-Z0-9](?:[A-Z0-9]{3})?)\b", 0.3,
       validator="SWIFT_BIC", needs_context=True, context=("swift", "bic", "bank identifier")),
    _p("ROUTING_ABA", "aba", r"(?<!\d)(\d{9})(?!\d)", 0.4, validator="ROUTING_ABA",
       needs_context=True, context=("routing", "aba")),
    _p("IFSC", "ifsc", r"\b([A-Z]{4}0[A-Z0-9]{6})\b", 0.6, needs_context=True, context=("ifsc",)),
    _p("SORT_CODE", "sort_code", r"\b(\d{2}-\d{2}-\d{2})\b", 0.5, needs_context=True,
       context=("sort code",)),
    _p("EMAIL", "email", r"(?<![A-Za-z0-9._%+\-])([A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,24})", 0.9,
       context=("email", "e-mail", "correo", "mail")),
    _p("IP_ADDRESS", "ipv4",
       r"(?<![\d.])((?:25[0-5]|2[0-4]\d|1?\d?\d)(?:\.(?:25[0-5]|2[0-4]\d|1?\d?\d)){3})(?![\d.])", 0.85),
    _p("IP_ADDRESS", "ipv6", r"\b((?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4})\b", 0.8),
    _p("MAC_ADDRESS", "mac", r"\b([0-9A-Fa-f]{2}(?:[:-][0-9A-Fa-f]{2}){5})\b", 0.8),
    _p("URL", "url", r"\b(https?://[^\s<>\"']+)", 0.7),
    _p("DATE", "date_iso", r"(?<!\d)(\d{4}-\d{2}-\d{2})(?!\d)", 0.6,
       promote_to="DATE_OF_BIRTH", promote_context=_DOB_CTX),
    _p("DATE", "date_slash", r"(?<!\d)(\d{1,2}/\d{1,2}/\d{4})(?!\d)", 0.55,
       promote_to="DATE_OF_BIRTH", promote_context=_DOB_CTX),
    _p("DATE", "date_text",
       r"\b(\d{1,2} (?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*,? \d{4})\b", 0.6,
       promote_to="DATE_OF_BIRTH", promote_context=_DOB_CTX),
    _p("POSTAL_CODE", "us_zip", r"\b(\d{5}(?:-\d{4})?)\b", 0.5, needs_context=True,
       context=("zip", "postal", "pin code", "pincode", "pin")),
    _p("POSTAL_CODE", "in_pin", r"\b([1-9]\d{5})\b", 0.5, needs_context=True,
       context=("pin code", "pincode", "pin", "postal")),
    _p("POSTAL_CODE", "gb_postcode", r"\b([A-Z]{1,2}\d[A-Z\d]? ?\d[A-Z]{2})\b", 0.6,
       context=("postcode", "postal")),
    _p("POSTAL_CODE", "ca_postal", r"\b([A-Z]\d[A-Z] ?\d[A-Z]\d)\b", 0.6, context=("postal",)),
    _p("API_KEY", "aws_access_key", r"\b(AKIA[0-9A-Z]{16})\b", 0.95),
    _p("API_KEY", "generic_sk", r"\b((?:sk|pk|rk)_(?:live|test)_[A-Za-z0-9]{16,})\b", 0.9),
    _p("API_KEY", "github_token", r"\b(gh[pousr]_[A-Za-z0-9]{36,})\b", 0.95),
    _p("API_KEY", "google_api_key", r"\b(AIza[0-9A-Za-z_\-]{35})\b", 0.95),
    _p("API_KEY", "slack_token", r"\b(xox[baprs]-[A-Za-z0-9-]{10,})\b", 0.95),
    _p("JWT", "jwt", r"\b(eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,})\b", 0.95),
    _p("PRIVATE_KEY", "pem_private_key", r"(-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----)", 0.99),
    _p("PASSWORD", "password_assignment", r"(?i)password\s*[:=]\s*(\S{4,})", 0.8),
    _p("CRYPTO_ADDRESS", "btc", r"\b([13][a-km-zA-HJ-NP-Z1-9]{25,34}|bc1[a-z0-9]{39,59})\b", 0.6,
       context=("btc", "bitcoin", "wallet")),
    _p("CRYPTO_ADDRESS", "eth", r"\b(0x[a-fA-F0-9]{40})\b", 0.7),
    _p("GEO_COORDINATE", "latlon", r"(-?\d{1,2}\.\d{3,},\s*-?\d{1,3}\.\d{3,})", 0.6),
    _p("PASSPORT", "passport_generic", r"\b([A-Z]{1,2}\d{6,8})\b", 0.5, needs_context=True,
       context=("passport", "pasaporte")),
    _p("DRIVER_LICENSE", "dl_generic", r"\b([A-Z]{1,2}\d{5,8})\b", 0.45, needs_context=True,
       context=("driver", "licence", "license", "dl no")),
    _p("USERNAME", "handle", r"(?<!\w)(@[A-Za-z0-9_]{3,30})\b", 0.5, context=("user", "handle")),
)

_REGION_BY_LOCALE = {"US": "US", "IN": "IN", "MX": "MX", "CA": "CA", "GB": "GB"}


def region_for_locale(locale: str) -> str:
    """``en_IN`` → ``IN``; unknown → ``US``."""
    tail = locale.replace("-", "_").split("_")[-1].upper()
    return _REGION_BY_LOCALE.get(tail, "US")


def _context(text: str, start: int, end: int, key_hint: str | None) -> str:
    before = text[max(0, start - CONTEXT_WINDOW):start]
    after = text[end:end + CONTEXT_WINDOW]
    return f"{before} {after} {key_hint or ''}".lower()


def resolve_overlaps(hits: list[Hit]) -> list[Hit]:
    """Keep the best hit per overlapping region: validated, then score, then length."""
    ranked = sorted(hits, key=lambda h: (not h.validated, -h.score, -(h.end - h.start), h.start))
    kept: list[Hit] = []
    for hit in ranked:
        if all(hit.end <= k.start or hit.start >= k.end for k in kept):
            kept.append(hit)
    return sorted(kept, key=lambda h: h.start)


def scan_text(text: str, *, key_hint: str | None = None) -> list[Hit]:
    """Run the catalogue over ``text``; ``key_hint`` (a JSON key or column name) counts as context."""
    hits: list[Hit] = []
    for spec in CATALOGUE:
        for match in spec.regex.finditer(text):
            start, end = match.span(1)
            value = match.group(1)
            ctx = _context(text, start, end, key_hint)
            has_ctx = any(word in ctx for word in spec.context)
            if spec.needs_context and not has_ctx:
                continue
            score, validated, evidence = spec.base_score, False, f"pattern:{spec.name}"
            if spec.validator:
                ok = validate(spec.validator, value)
                if ok:
                    score, validated, evidence = spec.validated_score, True, f"checksum:{spec.validator}"
                elif ok is False:
                    score *= INVALID_FACTOR
            entity = spec.entity_type
            if spec.promote_to and any(word in ctx for word in spec.promote_context):
                entity, score = spec.promote_to, min(1.0, score + PROMOTION_BONUS)
            if has_ctx:
                score = min(1.0, score + CONTEXT_BONUS)
            hits.append(Hit(entity, start, end, round(score, 3), f"pattern:{spec.name}", evidence, validated))
    return resolve_overlaps(hits)


def scan_phones(text: str, *, region: str = "US") -> list[Hit]:
    """Phone numbers via ``phonenumbers`` (valid numbers only, national or international)."""
    hits: list[Hit] = []
    try:
        matcher = phonenumbers.PhoneNumberMatcher(text, region, leniency=phonenumbers.Leniency.VALID)
        for m in matcher:
            hits.append(Hit("PHONE", m.start, m.end, 0.85, "pattern:phone", f"phone:{region}", True))
    except Exception:  # noqa: BLE001 - a matcher error must never fail detection
        return []
    return hits
```

- [ ] **Step 4: Run tests, lint, types**

Run: `pytest -q tests/test_patterns.py && ruff check dps tests && mypy dps`
Expected: `22 passed`, clean.

- [ ] **Step 5: Commit**

```bash
git add dps/engine/detect/patterns.py tests/test_patterns.py
git commit -m "feat(detect): pattern catalogue with checksum validation, context promotion, phone matching"
```

---

### Task 7: Structural detector (tier T0) — key and column names

**Files:**
- Create: `dps/engine/detect/structural.py`
- Test: `tests/test_structural.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_structural.py`:
```python
from __future__ import annotations

import pytest

from dps.engine.detect.structural import classify_key, normalize_key


@pytest.mark.parametrize(
    "key,expected",
    [
        ("customerSSN", "customer ssn"),
        ("date_of_birth", "date of birth"),
        ("$.card.tags[1]['cc-number']", "cc number"),
        ("EMAIL_ADDRESS", "email address"),
    ],
)
def test_normalize_key(key: str, expected: str) -> None:
    assert normalize_key(key) == expected


@pytest.mark.parametrize(
    "key,entity,min_score",
    [
        ("ssn", "SSN", 0.9),
        ("$.applicant.dateOfBirth", "DATE_OF_BIRTH", 0.9),
        ("aadhaar_no", "AADHAAR", 0.9),
        ("cc_number", "CARD_PAN", 0.9),
        ("correo_electronico", "EMAIL", 0.8),
        ("mobile_phone", "PHONE", 0.8),
        ("addr_line1", "STREET_ADDRESS", 0.7),
        ("api_key", "API_KEY", 0.9),
        ("employeeId", "EMPLOYEE_ID", 0.85),
    ],
)
def test_classify_key_top_candidate(key: str, entity: str, min_score: float) -> None:
    top = classify_key(key)[0]
    assert top.entity_type == entity and top.score >= min_score
    assert top.evidence.startswith("key:")


def test_ambiguous_and_unknown_keys() -> None:
    assert classify_key("pan")[0].entity_type == "PAN_IN" and classify_key("pan")[0].score == 0.5
    assert classify_key("name")[0].score < 0.6  # needs value evidence to pass the threshold
    assert classify_key("widget_count") == []
```

- [ ] **Step 2: Run to confirm failure**

Run: `pytest -q tests/test_structural.py`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write `dps/engine/detect/structural.py`**

```python
"""Tier T0 — what a key, column or field name says about its value.

``classify_key`` returns candidates sorted by score. Scores are deliberately below the default
threshold for ambiguous words (``name``, ``pan``, ``ip``) so they only pass with value evidence
from the other tiers, while unambiguous names (``ssn``, ``aadhaar``, ``cvv``) stand on their own.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_SPLIT = re.compile(r"[^a-z0-9]+")
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_INDEX = re.compile(r"\[\d+\]$")
_QUOTED = re.compile(r"^\['(.*)'\]$")


@dataclass(frozen=True, slots=True)
class KeyCandidate:
    entity_type: str
    score: float
    evidence: str


def normalize_key(key: str) -> str:
    """Last path segment, camelCase split, lower-cased, non-alphanumerics → single spaces."""
    last = key
    if last.startswith("$"):
        last = last[1:]
    last = _INDEX.sub("", last)
    if "." in last or "[" in last:
        # take the last dotted or bracketed segment
        parts = re.split(r"\.(?![^\[]*\])", last)
        last = parts[-1] if parts[-1] else (parts[-2] if len(parts) > 1 else last)
        quoted = _QUOTED.match(last)
        if quoted:
            last = quoted.group(1)
    spaced = _CAMEL.sub(" ", last)
    return " ".join(t for t in _SPLIT.split(spaced.lower()) if t)


# (phrase, entity, score). Phrases match as whole words inside the normalized key.
_RULES: tuple[tuple[str, str, float], ...] = (
    ("date of birth", "DATE_OF_BIRTH", 0.9), ("birth date", "DATE_OF_BIRTH", 0.9),
    ("birthdate", "DATE_OF_BIRTH", 0.9), ("dob", "DATE_OF_BIRTH", 0.85), ("birthday", "DATE_OF_BIRTH", 0.8),
    ("fecha nacimiento", "DATE_OF_BIRTH", 0.9), ("fecha nac", "DATE_OF_BIRTH", 0.85),
    ("social security number", "SSN", 0.95), ("social security", "SSN", 0.9), ("ssn", "SSN", 0.9),
    ("nss", "SSN", 0.6), ("itin", "ITIN", 0.9), ("ein", "EIN", 0.8),
    ("social insurance", "SIN", 0.9), ("sin", "SIN", 0.7),
    ("curp", "CURP", 0.95), ("rfc", "RFC", 0.9), ("clave elector", "INE_CLAVE", 0.9), ("ine", "INE_CLAVE", 0.6),
    ("aadhaar", "AADHAAR", 0.95), ("aadhar", "AADHAAR", 0.95), ("uid", "AADHAAR", 0.5),
    ("pan number", "PAN_IN", 0.7), ("pan", "PAN_IN", 0.5), ("voter id", "VOTER_ID", 0.85), ("epic", "VOTER_ID", 0.5),
    ("national insurance", "NINO", 0.9), ("nino", "NINO", 0.9), ("nhs number", "NHS_NUMBER", 0.9), ("nhs", "NHS_NUMBER", 0.7),
    ("passport", "PASSPORT", 0.85), ("pasaporte", "PASSPORT", 0.85),
    ("driver license", "DRIVER_LICENSE", 0.85), ("drivers license", "DRIVER_LICENSE", 0.85),
    ("driving licence", "DRIVER_LICENSE", 0.85), ("dl number", "DRIVER_LICENSE", 0.7), ("licencia", "DRIVER_LICENSE", 0.6),
    ("national id", "NATIONAL_ID", 0.8), ("identity number", "NATIONAL_ID", 0.6), ("id number", "NATIONAL_ID", 0.5),
    ("mrz", "MRZ", 0.9),
    ("card number", "CARD_PAN", 0.9), ("credit card", "CARD_PAN", 0.9), ("debit card", "CARD_PAN", 0.9),
    ("cc number", "CARD_PAN", 0.9), ("ccnum", "CARD_PAN", 0.9), ("card no", "CARD_PAN", 0.85), ("card pan", "CARD_PAN", 0.9),
    ("expiry", "CARD_EXPIRY", 0.7), ("exp date", "CARD_EXPIRY", 0.7), ("expiration", "CARD_EXPIRY", 0.6),
    ("valid thru", "CARD_EXPIRY", 0.7),
    ("cvv", "CVV", 0.95), ("cvc", "CVV", 0.95), ("cvv2", "CVV", 0.95), ("security code", "CVV", 0.7),
    ("iban", "IBAN", 0.9), ("swift", "SWIFT_BIC", 0.9), ("bic", "SWIFT_BIC", 0.8),
    ("account number", "ACCOUNT_NUMBER", 0.85), ("account no", "ACCOUNT_NUMBER", 0.85), ("acct", "ACCOUNT_NUMBER", 0.6),
    ("routing", "ROUTING_ABA", 0.85), ("aba", "ROUTING_ABA", 0.7), ("ifsc", "IFSC", 0.9), ("sort code", "SORT_CODE", 0.9),
    ("wallet address", "CRYPTO_ADDRESS", 0.8), ("wallet", "CRYPTO_ADDRESS", 0.5),
    ("email", "EMAIL", 0.9), ("e mail", "EMAIL", 0.9), ("correo", "EMAIL", 0.85), ("mail", "EMAIL", 0.6),
    ("phone", "PHONE", 0.85), ("mobile", "PHONE", 0.85), ("telephone", "PHONE", 0.85), ("tel", "PHONE", 0.7),
    ("telefono", "PHONE", 0.85), ("cell", "PHONE", 0.7), ("msisdn", "PHONE", 0.9), ("whatsapp", "PHONE", 0.8),
    ("street address", "STREET_ADDRESS", 0.9), ("address line", "STREET_ADDRESS", 0.85), ("address", "STREET_ADDRESS", 0.75),
    ("addr", "STREET_ADDRESS", 0.7), ("direccion", "STREET_ADDRESS", 0.85), ("street", "STREET_ADDRESS", 0.6),
    ("postal code", "POSTAL_CODE", 0.9), ("postcode", "POSTAL_CODE", 0.9), ("zip", "POSTAL_CODE", 0.85),
    ("pin code", "POSTAL_CODE", 0.9), ("pincode", "POSTAL_CODE", 0.9), ("codigo postal", "POSTAL_CODE", 0.9),
    ("city", "CITY", 0.7), ("ciudad", "CITY", 0.7),
    ("latitude", "GEO_COORDINATE", 0.8), ("longitude", "GEO_COORDINATE", 0.8), ("lat", "GEO_COORDINATE", 0.5),
    ("lng", "GEO_COORDINATE", 0.5), ("geo", "GEO_COORDINATE", 0.5),
    ("full name", "PERSON_NAME", 0.9), ("first name", "PERSON_NAME", 0.9), ("last name", "PERSON_NAME", 0.9),
    ("surname", "PERSON_NAME", 0.9), ("given name", "PERSON_NAME", 0.9), ("family name", "PERSON_NAME", 0.9),
    ("middle name", "PERSON_NAME", 0.85), ("nombre", "PERSON_NAME", 0.85), ("apellido", "PERSON_NAME", 0.85),
    ("name", "PERSON_NAME", 0.55),
    ("gender", "GENDER", 0.8), ("sex", "GENDER", 0.8), ("genero", "GENDER", 0.8),
    ("nationality", "NATIONALITY", 0.8), ("citizenship", "NATIONALITY", 0.75), ("age", "AGE", 0.6),
    ("ip address", "IP_ADDRESS", 0.9), ("ip", "IP_ADDRESS", 0.6), ("mac address", "MAC_ADDRESS", 0.9),
    ("device id", "DEVICE_ID", 0.85), ("imei", "DEVICE_ID", 0.9), ("user agent", "DEVICE_ID", 0.5),
    ("cookie", "COOKIE_ID", 0.7), ("session id", "COOKIE_ID", 0.6),
    ("username", "USERNAME", 0.9), ("user name", "USERNAME", 0.9), ("login", "USERNAME", 0.6), ("handle", "USERNAME", 0.5),
    ("url", "URL", 0.7), ("website", "URL", 0.6),
    ("api key", "API_KEY", 0.95), ("apikey", "API_KEY", 0.95), ("access key", "API_KEY", 0.9),
    ("secret", "API_KEY", 0.8), ("token", "API_KEY", 0.7),
    ("password", "PASSWORD", 0.95), ("passwd", "PASSWORD", 0.95), ("pwd", "PASSWORD", 0.8),
    ("private key", "PRIVATE_KEY", 0.95), ("jwt", "JWT", 0.9), ("bearer", "JWT", 0.7),
    ("medical record", "MEDICAL_RECORD_NUMBER", 0.9), ("mrn", "MEDICAL_RECORD_NUMBER", 0.85),
    ("diagnosis", "DIAGNOSIS_CODE", 0.8), ("icd", "DIAGNOSIS_CODE", 0.8),
    ("medication", "MEDICATION", 0.8), ("prescription", "MEDICATION", 0.7),
    ("employee id", "EMPLOYEE_ID", 0.9), ("emp id", "EMPLOYEE_ID", 0.85), ("staff id", "EMPLOYEE_ID", 0.85),
    ("customer id", "CUSTOMER_ID", 0.85), ("cust id", "CUSTOMER_ID", 0.85), ("client id", "CUSTOMER_ID", 0.8),
    ("case id", "CASE_ID", 0.8), ("ticket", "CASE_ID", 0.5),
    ("organization", "ORG_NAME", 0.7), ("employer", "ORG_NAME", 0.7), ("empresa", "ORG_NAME", 0.7),
    ("company", "ORG_NAME", 0.6),
)

_COMPILED: tuple[tuple[re.Pattern[str], str, float, str], ...] = tuple(
    (re.compile(rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])"), entity, score, phrase)
    for phrase, entity, score in _RULES
)


def classify_key(key: str) -> list[KeyCandidate]:
    """Candidates for ``key`` (a JSON path, column or field name), best first, one per entity."""
    norm = normalize_key(key)
    if not norm:
        return []
    best: dict[str, KeyCandidate] = {}
    for regex, entity, score, phrase in _COMPILED:
        if regex.search(norm):
            current = best.get(entity)
            if current is None or score > current.score:
                best[entity] = KeyCandidate(entity, score, f"key:{phrase}")
    return sorted(best.values(), key=lambda c: -c.score)
```

- [ ] **Step 4: Run tests, lint, types**

Run: `pytest -q tests/test_structural.py && ruff check dps tests && mypy dps`
Expected: `14 passed`, clean.

- [ ] **Step 5: Commit**

```bash
git add dps/engine/detect/structural.py tests/test_structural.py
git commit -m "feat(detect): structural key-name classifier"
```

---

### Task 8: Detector ensemble over JSON and text

**Files:**
- Create: `dps/engine/detect/base.py`, `dps/engine/detect/ensemble.py`
- Test: `tests/test_ensemble.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_ensemble.py`:
```python
from __future__ import annotations

from dps.engine.detect.base import NerAdapter
from dps.engine.detect.ensemble import Detector, DetectorConfig
from dps.engine.detect.patterns import Hit
from dps.engine.taxonomy import Sensitivity

SAMPLE = {
    "customer_id": "CUST-884120",
    "full_name": "Ananya Krishnan",
    "dob": "1988-03-14",
    "aadhaar": "2341 2341 2346",
    "pan": "4532 7811 2290 4417",
    "email": "ananya.k@example-mail.in",
    "phone": "+91 98451 20377",
    "active": True,
    "risk_score": 712,
    "notes": "Called on 14 Mar about card ending 4417; mail ann.k@example-mail.in",
}


def _by_path(result):  # noqa: ANN001, ANN202 - test helper
    out: dict[str, list] = {}
    for f in result.findings:
        out.setdefault(f.locator.path, []).append(f)
    return out


def test_structured_record_findings() -> None:
    det = Detector(DetectorConfig(locale="en_IN"))
    res = det.detect(data=SAMPLE)
    by = _by_path(res)
    aadhaar = by["$.aadhaar"][0]
    assert aadhaar.entity_type == "AADHAAR" and aadhaar.locator.whole and aadhaar.score >= 0.98
    assert any(d.startswith("checksum") for d in aadhaar.evidence) or "checksum:AADHAAR" in aadhaar.evidence
    assert by["$.pan"][0].entity_type == "CARD_PAN"  # value shape beats the ambiguous key
    assert by["$.email"][0].entity_type == "EMAIL" and by["$.phone"][0].entity_type == "PHONE"
    assert by["$.dob"][0].entity_type == "DATE_OF_BIRTH"
    assert by["$.full_name"][0].entity_type == "PERSON_NAME"  # structural alone, 0.9 >= floor
    assert "$.active" not in by and "$.risk_score" not in by
    spans = by["$.notes"]
    assert any(f.entity_type == "EMAIL" and not f.locator.whole for f in spans)
    assert res.record_sensitivity is Sensitivity.CRITICAL


def test_numbers_under_sensitive_keys_and_threshold() -> None:
    det = Detector(DetectorConfig())
    assert det.detect(data={"ssn": 123456789}).findings[0].entity_type == "SSN"
    assert det.detect(data={"name": "Widget 3000"}).findings == []
    assert det.detect(data={"customer_id": "CUST-1"}).findings[0].entity_type == "CUSTOMER_ID"


def test_text_mode_yields_spans_on_root() -> None:
    res = Detector(DetectorConfig()).detect(text="Call (415) 555-0132 or mail a@b.co today")
    types = {f.entity_type: f for f in res.findings}
    assert {"PHONE", "EMAIL"} <= set(types)
    assert types["EMAIL"].locator.path == "$" and types["EMAIL"].locator.start is not None


class FakeNer(NerAdapter):
    languages = ("en",)

    def analyze(self, text: str, language: str) -> list[Hit]:
        i = text.find("Ann Smith")
        return [Hit("PERSON_NAME", i, i + 9, 0.85, "ner:fake", "ner:PERSON")] if i >= 0 else []


def test_ner_adapter_is_used_when_enabled() -> None:
    det = Detector(DetectorConfig(ner_enabled=True), ner=FakeNer())
    res = det.detect(text="Ann Smith lives here")
    assert res.findings[0].entity_type == "PERSON_NAME" and "ner:fake" in res.findings[0].detectors
    assert Detector(DetectorConfig(ner_enabled=False), ner=FakeNer()).detect(text="Ann Smith").findings == []
```

- [ ] **Step 2: Run to confirm failure**

Run: `pytest -q tests/test_ensemble.py`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write `dps/engine/detect/base.py`**

```python
"""Protocols the ensemble composes: NER (T2) and custom detectors (T4)."""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from dps.engine.detect.patterns import Hit
from dps.engine.detect.structural import KeyCandidate


@runtime_checkable
class NerAdapter(Protocol):
    """Named-entity recognition over free text, returning catalogue-style hits."""

    languages: tuple[str, ...]

    def analyze(self, text: str, language: str) -> list[Hit]: ...


@runtime_checkable
class CustomDetector(Protocol):
    """Client-declared fields and custom recognizers from the policy."""

    def declared(self, path: str) -> KeyCandidate | None: ...

    def scan(self, text: str, key_hint: str | None) -> list[Hit]: ...
```

- [ ] **Step 4: Write `dps/engine/detect/ensemble.py`**

```python
"""The detector ensemble: run the tiers over a JSON document or a text and merge the evidence.

Merging rules (spec §5.3):
* a hit covering the whole value and a structural candidate for the same entity combine by
  noisy-OR; a whole-value hit for a different entity wins over the key's suggestion;
* a structural candidate alone passes only at or above ``structural_only_floor``;
* partial hits inside a longer string become span findings unless a whole-value finding exists;
* findings below ``min_score`` are dropped; one finding per locator.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from dps.engine.detect.base import CustomDetector, NerAdapter
from dps.engine.detect.patterns import Hit, region_for_locale, resolve_overlaps, scan_phones, scan_text
from dps.engine.detect.structural import KeyCandidate, classify_key, normalize_key
from dps.engine.findings import Finding, Locator, hash_value
from dps.engine.jsonpath import flatten
from dps.engine.taxonomy import Sensitivity, entity_info, record_sensitivity


@dataclass(frozen=True)
class DetectorConfig:
    min_score: float = 0.6
    locale: str = "en_US"
    ner_enabled: bool = False
    ner_min_chars: int = 3
    structural_only_floor: float = 0.7


class DetectionResult(BaseModel):
    findings: list[Finding]
    record_sensitivity: Sensitivity


def _noisy_or(*scores: float) -> float:
    acc = 1.0
    for s in scores:
        acc *= 1.0 - s
    return round(1.0 - acc, 3)


def _language(locale: str) -> str:
    return locale.split("_")[0].split("-")[0].lower() or "en"


class Detector:
    """Composes T0 (structural), T1 (patterns + phones), T2 (NER) and T4 (custom)."""

    def __init__(
        self, config: DetectorConfig, ner: NerAdapter | None = None, custom: CustomDetector | None = None
    ) -> None:
        self.config = config
        self.ner = ner
        self.custom = custom
        self.region = region_for_locale(config.locale)
        self.language = _language(config.locale)

    # -- public -------------------------------------------------------------------------------
    def detect(self, *, data: Any | None = None, text: str | None = None) -> DetectionResult:
        """Detect over a JSON document (``data``) or a text; exactly one must be given."""
        if (data is None) == (text is None):
            raise ValueError("pass exactly one of data or text")
        findings: list[Finding] = []
        if text is not None:
            findings.extend(self._text_findings("$", text, key_hint=None, structural=[]))
        else:
            for path, value in flatten(data):
                findings.extend(self._leaf_findings(path, value))
        findings = self._dedupe(f for f in findings if f.score >= self.config.min_score)
        return DetectionResult(
            findings=findings, record_sensitivity=record_sensitivity(f.entity_type for f in findings)
        )

    # -- internals ----------------------------------------------------------------------------
    def _value_hits(self, text: str, key_hint: str | None) -> list[Hit]:
        hits = scan_text(text, key_hint=key_hint) + scan_phones(text, region=self.region)
        if self.ner is not None and self.config.ner_enabled and len(text) >= self.config.ner_min_chars:
            hits += self.ner.analyze(text, self.language)
        if self.custom is not None:
            hits += self.custom.scan(text, key_hint)
        return resolve_overlaps(hits)

    def _leaf_findings(self, path: str, value: Any) -> list[Finding]:
        if value is None or isinstance(value, bool):
            return []
        key_hint = normalize_key(path)
        structural = classify_key(path)
        if self.custom is not None:
            declared = self.custom.declared(path)
            if declared is not None:
                structural = [declared, *structural]
        return self._text_findings(path, str(value), key_hint=key_hint, structural=structural)

    def _text_findings(
        self, path: str, text: str, *, key_hint: str | None, structural: list[KeyCandidate]
    ) -> list[Finding]:
        hits = self._value_hits(text, key_hint)
        stripped_start = len(text) - len(text.lstrip())
        stripped_end = len(text.rstrip())
        whole_hits = [h for h in hits if h.start <= stripped_start and h.end >= stripped_end]
        partial_hits = [h for h in hits if h not in whole_hits]
        top_key = structural[0] if structural else None
        out: list[Finding] = []

        if whole_hits:
            hit = max(whole_hits, key=lambda h: (h.validated, h.score))
            same_key = next((c for c in structural if c.entity_type == hit.entity_type), None)
            if same_key is not None:
                score, detectors = _noisy_or(hit.score, same_key.score), [hit.detector, "structural"]
                evidence = [hit.evidence, same_key.evidence]
            elif top_key is not None and top_key.score > hit.score and top_key.score >= self.config.structural_only_floor:
                hit, score, detectors = None, top_key.score, ["structural"]  # type: ignore[assignment]
                evidence = [top_key.evidence]
            else:
                score, detectors, evidence = hit.score, [hit.detector], [hit.evidence]
                if top_key is not None:
                    evidence.append(f"key-suggested:{top_key.entity_type}")
            entity = hit.entity_type if hit is not None else top_key.entity_type  # type: ignore[union-attr]
            out.append(self._finding(path, None, None, entity, score, detectors, evidence, text))
            return out

        if top_key is not None and top_key.score >= self.config.structural_only_floor and text.strip():
            out.append(self._finding(path, None, None, top_key.entity_type, top_key.score, ["structural"],
                                     [top_key.evidence], text))
            return out

        for hit in partial_hits:
            out.append(self._finding(path, hit.start, hit.end, hit.entity_type, hit.score, [hit.detector],
                                     [hit.evidence], text[hit.start:hit.end]))
        return out

    @staticmethod
    def _finding(path: str, start: int | None, end: int | None, entity: str, score: float,
                 detectors: list[str], evidence: list[str], value: str) -> Finding:
        return Finding(
            locator=Locator(path=path, start=start, end=end), entity_type=entity, score=min(1.0, score),
            detectors=detectors, sensitivity=entity_info(entity).sensitivity, evidence=evidence,
            value_hash=hash_value(value),
        )

    @staticmethod
    def _dedupe(findings: Any) -> list[Finding]:
        best: dict[tuple[str, int | None, int | None], Finding] = {}
        for f in findings:
            key = (f.locator.path, f.locator.start, f.locator.end)
            if key not in best or f.score > best[key].score:
                best[key] = f
        return sorted(best.values(), key=lambda f: (f.locator.path, f.locator.start or -1))
```

- [ ] **Step 5: Run tests, lint, types**

Run: `pytest -q tests/test_ensemble.py && ruff check dps tests && mypy dps`
Expected: `4 passed`, clean. If `$.full_name` is missing, check that `classify_key("$.full_name")` returns 0.9 (Task 7) — the structural floor is 0.7.

- [ ] **Step 6: Commit**

```bash
git add dps/engine/detect/base.py dps/engine/detect/ensemble.py tests/test_ensemble.py
git commit -m "feat(detect): ensemble over JSON and text with noisy-OR merging and record sensitivity"
```

---

### Task 9: Optional Presidio NER adapter (tier T2)

**Files:**
- Create: `dps/engine/detect/ner_presidio.py`
- Test: `tests/test_ner_presidio.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_ner_presidio.py`:
```python
from __future__ import annotations

import pytest

from dps.engine.detect.ner_presidio import (
    PRESIDIO_TO_DPS,
    PresidioNer,
    build_ner,
    presidio_available,
)


def test_mapping_covers_the_entities_we_request() -> None:
    for name in ("PERSON", "LOCATION", "NRP", "DATE_TIME"):
        assert name in PRESIDIO_TO_DPS
    assert PRESIDIO_TO_DPS["PERSON"] == "PERSON_NAME"


def test_build_ner_respects_the_switch() -> None:
    assert build_ner(("en",), enabled=False) is None
    auto = build_ner(("en",), enabled=None)
    assert (auto is None) is (not presidio_available())
    if not presidio_available():
        with pytest.raises(RuntimeError):
            build_ner(("en",), enabled=True)


def test_engine_errors_never_propagate(monkeypatch: pytest.MonkeyPatch) -> None:
    ner = PresidioNer(languages=("en",))
    monkeypatch.setattr(ner, "_engine", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    assert ner.analyze("Ann Smith", "en") == []


@pytest.mark.ner
def test_real_presidio_finds_a_person() -> None:
    pytest.importorskip("presidio_analyzer")
    spacy = pytest.importorskip("spacy")
    if not spacy.util.is_package("en_core_web_lg") and not spacy.util.is_package("en_core_web_sm"):
        pytest.skip("no spaCy English model installed")
    hits = PresidioNer(languages=("en",)).analyze("My name is John Smith and I live in Seattle.", "en")
    assert any(h.entity_type == "PERSON_NAME" for h in hits)
```

- [ ] **Step 2: Run to confirm failure**

Run: `pytest -q tests/test_ner_presidio.py`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write `dps/engine/detect/ner_presidio.py`**

```python
"""Tier T2 — Presidio NER (spaCy ``en``/``es``) mapped onto the DPS taxonomy.

Optional: the ``[ner]`` extra and a spaCy model. Everything is lazy and guarded so the engine
imports cleanly without them; a missing or failing engine yields no hits, never an exception.
Presidio is only asked for the entities the pattern catalogue cannot find (names, places,
nationalities, free-form dates); identifiers with checksums stay with tier T1.
"""
from __future__ import annotations

import importlib.util
import threading
from typing import Any

from dps.engine.detect.patterns import Hit
from dps.observability import get_logger

logger = get_logger(__name__)

PRESIDIO_TO_DPS: dict[str, str] = {
    "PERSON": "PERSON_NAME", "LOCATION": "LOCATION", "NRP": "NATIONALITY", "DATE_TIME": "DATE",
    "EMAIL_ADDRESS": "EMAIL", "PHONE_NUMBER": "PHONE", "CREDIT_CARD": "CARD_PAN", "IBAN_CODE": "IBAN",
    "US_SSN": "SSN", "US_ITIN": "ITIN", "US_PASSPORT": "PASSPORT", "US_DRIVER_LICENSE": "DRIVER_LICENSE",
    "IP_ADDRESS": "IP_ADDRESS", "URL": "URL", "CRYPTO": "CRYPTO_ADDRESS", "UK_NHS": "NHS_NUMBER",
    "IN_AADHAAR": "AADHAAR", "IN_PAN": "PAN_IN", "IN_PASSPORT": "PASSPORT", "IN_VOTER": "VOTER_ID",
    "ORGANIZATION": "ORG_NAME",
}
#: What we ask Presidio for — the recall-only entities.
NER_ENTITIES: tuple[str, ...] = ("PERSON", "LOCATION", "NRP", "DATE_TIME")
_MODEL_CANDIDATES = {
    "en": ("en_core_web_lg", "en_core_web_md", "en_core_web_sm"),
    "es": ("es_core_news_lg", "es_core_news_md", "es_core_news_sm"),
}


def presidio_available() -> bool:
    return importlib.util.find_spec("presidio_analyzer") is not None


def _installed_model(lang: str) -> str | None:
    import spacy.util  # lazy; only reached when presidio is importable

    for name in _MODEL_CANDIDATES.get(lang, ()):
        if spacy.util.is_package(name):
            return name
    return None


class PresidioNer:
    """Lazily built, thread-safe Presidio analyzer."""

    def __init__(self, languages: tuple[str, ...] = ("en", "es"), score_threshold: float = 0.4) -> None:
        self.languages = languages
        self.score_threshold = score_threshold
        self._lock = threading.Lock()
        self._analyzer: Any | None = None
        self._warned = False

    def _engine(self) -> Any:
        with self._lock:
            if self._analyzer is None:
                from presidio_analyzer import AnalyzerEngine
                from presidio_analyzer.nlp_engine import NlpEngineProvider

                models = []
                for lang in self.languages:
                    model = _installed_model(lang)
                    if model:
                        models.append({"lang_code": lang, "model_name": model})
                if not models:
                    raise RuntimeError("no spaCy model installed for " + ",".join(self.languages))
                provider = NlpEngineProvider(nlp_configuration={"nlp_engine_name": "spacy", "models": models})
                self._analyzer = AnalyzerEngine(
                    nlp_engine=provider.create_engine(), supported_languages=[m["lang_code"] for m in models]
                )
            return self._analyzer

    def analyze(self, text: str, language: str) -> list[Hit]:
        """Catalogue-style hits for ``text``; empty on any engine problem."""
        lang = language if language in self.languages else self.languages[0]
        try:
            results = self._engine().analyze(
                text=text, language=lang, entities=list(NER_ENTITIES), score_threshold=self.score_threshold
            )
        except Exception as exc:  # noqa: BLE001 - NER is recall-only, never load-bearing
            if not self._warned:
                logger.warning("ner_unavailable", error=type(exc).__name__)
                self._warned = True
            return []
        hits: list[Hit] = []
        for r in results:
            entity = PRESIDIO_TO_DPS.get(r.entity_type)
            if entity is None:
                continue
            hits.append(Hit(entity, r.start, r.end, round(float(r.score), 3), "ner:presidio",
                            f"ner:{r.entity_type}:{float(r.score):.2f}"))
        return hits


def build_ner(languages: tuple[str, ...], enabled: bool | None) -> PresidioNer | None:
    """``enabled``: False → none; True → Presidio or raise; None → Presidio when importable."""
    if enabled is False:
        return None
    if presidio_available():
        return PresidioNer(languages=languages)
    if enabled is True:
        raise RuntimeError("DPS_NER_ENABLED=true but presidio-analyzer is not installed (pip install '.[ner]')")
    return None
```

- [ ] **Step 4: Run tests, lint, types**

Run: `pytest -q tests/test_ner_presidio.py && ruff check dps tests && mypy dps`
Expected: `3 passed, 1 skipped` (the `ner` test skips without the extra), clean.

- [ ] **Step 5: Commit**

```bash
git add dps/engine/detect/ner_presidio.py tests/test_ner_presidio.py
git commit -m "feat(detect): optional Presidio NER adapter mapped to the DPS taxonomy"
```

---

### Task 10: Custom recognizers and declared fields (tier T4)

**Files:**
- Create: `dps/engine/detect/custom.py`
- Test: `tests/test_custom.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_custom.py`:
```python
from __future__ import annotations

import pytest

from dps.engine.detect.custom import (
    DeclaredField,
    PolicyCustomDetector,
    RecognizerSpec,
    load_recognizers_yaml,
)
from dps.engine.taxonomy import Sensitivity, entity_info

EMP = RecognizerSpec(name="employee-id", supported_entity="EMPLOYEE_ID",
                     patterns=[{"name": "emp", "regex": r"\bEMP-\d{6}\b", "score": 0.8}], context=["employee"])
CODES = RecognizerSpec(name="project-codes", supported_entity="PROJECT_CODE", deny_list=["Blue Falcon", "Nightjar"],
                       sensitivity=Sensitivity.HIGH)


def test_declared_fields_match_exact_and_glob() -> None:
    det = PolicyCustomDetector([], [DeclaredField(path="$.loan.internal_score", entity_type="BUSINESS_SENSITIVE"),
                                    DeclaredField(path="$.accounts[*].iban", entity_type="IBAN", score=0.9)])
    assert det.declared("$.loan.internal_score").entity_type == "BUSINESS_SENSITIVE"  # type: ignore[union-attr]
    assert det.declared("$.accounts[3].iban").score == 0.9  # type: ignore[union-attr]
    assert det.declared("$.other") is None


def test_custom_pattern_with_context_boost_and_deny_list() -> None:
    det = PolicyCustomDetector([EMP, CODES], [])
    plain = det.scan("id EMP-123456", None)
    boosted = det.scan("employee EMP-123456", None)
    assert plain[0].entity_type == "EMPLOYEE_ID" and plain[0].score == 0.8
    assert boosted[0].score == pytest.approx(0.9)
    hits = det.scan("codename blue falcon shipped", None)
    assert hits[0].entity_type == "PROJECT_CODE" and hits[0].score == 0.9
    assert entity_info("PROJECT_CODE").sensitivity is Sensitivity.HIGH


def test_specs_are_validated() -> None:
    with pytest.raises(ValueError):
        RecognizerSpec(name="bad", supported_entity="EMPLOYEE_ID", patterns=[{"name": "x", "regex": "(", "score": 0.5}])
    with pytest.raises(ValueError):
        RecognizerSpec(name="bad", supported_entity="lowercase", deny_list=["x"])
    with pytest.raises(ValueError):
        RecognizerSpec(name="empty", supported_entity="X")


def test_yaml_loader(tmp_path) -> None:
    f = tmp_path / "rec.yaml"
    f.write_text("recognizers:\n  - name: badge\n    supported_entity: BADGE_ID\n    patterns:\n"
                 "      - {name: b, regex: 'B-\\d{4}', score: 0.7}\n", encoding="utf-8")
    specs = load_recognizers_yaml(f)
    assert specs[0].supported_entity == "BADGE_ID" and specs[0].patterns[0].score == 0.7
```

- [ ] **Step 2: Run to confirm failure**

Run: `pytest -q tests/test_custom.py`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write `dps/engine/detect/custom.py`**

```python
"""Tier T4 — what the client declares: fields by path and custom recognizers.

Recognizers use Presidio's YAML recognizer shape (``name``, ``supported_entity``, ``patterns``,
``deny_list``, ``context``) so the files DES already maintains load unchanged; an optional
``sensitivity`` registers the custom entity's tier in the taxonomy.
"""
from __future__ import annotations

import fnmatch
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from dps.engine.detect.patterns import CONTEXT_BONUS, CONTEXT_WINDOW, Hit
from dps.engine.detect.structural import KeyCandidate
from dps.engine.taxonomy import EntityInfo, Family, Sensitivity, register_entity

_ENTITY = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")


class PatternSpecModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str
    regex: str
    score: float = Field(ge=0.0, le=1.0)

    @field_validator("regex")
    @classmethod
    def _compiles(cls, value: str) -> str:
        try:
            re.compile(value)
        except re.error as exc:
            raise ValueError(f"invalid regex {value!r}: {exc}") from exc
        return value


class RecognizerSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str
    supported_entity: str
    supported_language: str = "en"
    patterns: tuple[PatternSpecModel, ...] = ()
    deny_list: tuple[str, ...] = ()
    context: tuple[str, ...] = ()
    sensitivity: Sensitivity | None = None

    @field_validator("supported_entity")
    @classmethod
    def _entity_name(cls, value: str) -> str:
        if not _ENTITY.match(value):
            raise ValueError(f"entity type {value!r} must match {_ENTITY.pattern}")
        return value

    @model_validator(mode="after")
    def _has_matcher(self) -> RecognizerSpec:
        if not self.patterns and not self.deny_list:
            raise ValueError(f"recognizer {self.name!r} needs patterns or a deny_list")
        return self


class DeclaredField(BaseModel):
    """A field the owner names explicitly; ``path`` is an exact JSON path or an fnmatch glob."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    path: str
    entity_type: str
    score: float = Field(default=0.99, ge=0.0, le=1.0)
    sensitivity: Sensitivity | None = None

    @field_validator("entity_type")
    @classmethod
    def _entity_name(cls, value: str) -> str:
        if not _ENTITY.match(value):
            raise ValueError(f"entity type {value!r} must match {_ENTITY.pattern}")
        return value


def load_recognizers_yaml(source: str | Path) -> list[RecognizerSpec]:
    """Read ``recognizers: [...]`` (Presidio's shape) from a YAML file."""
    data = yaml.safe_load(Path(source).read_text(encoding="utf-8")) or {}
    entries = data.get("recognizers") if isinstance(data, dict) else data
    if not isinstance(entries, list):
        raise ValueError(f"{source}: expected a 'recognizers' list")
    return [RecognizerSpec.model_validate(e) for e in entries]


class PolicyCustomDetector:
    """Implements :class:`dps.engine.detect.base.CustomDetector` from policy declarations."""

    def __init__(self, recognizers: list[RecognizerSpec], declared: list[DeclaredField]) -> None:
        self._declared = list(declared)
        self._patterns: list[tuple[RecognizerSpec, PatternSpecModel, re.Pattern[str]]] = []
        self._deny: list[tuple[RecognizerSpec, re.Pattern[str]]] = []
        for rec in recognizers:
            if rec.sensitivity is not None:
                register_entity(EntityInfo(rec.supported_entity, Family.OTHER, rec.sensitivity, f"custom:{rec.name}"))
            for pat in rec.patterns:
                self._patterns.append((rec, pat, re.compile(pat.regex)))
            if rec.deny_list:
                alternation = "|".join(re.escape(w) for w in sorted(rec.deny_list, key=len, reverse=True))
                self._deny.append((rec, re.compile(rf"(?<!\w)(?:{alternation})(?!\w)", re.IGNORECASE)))
        for d in declared:
            if d.sensitivity is not None:
                register_entity(EntityInfo(d.entity_type, Family.OTHER, d.sensitivity, "declared"))

    def declared(self, path: str) -> KeyCandidate | None:
        for spec in self._declared:
            if path == spec.path or fnmatch.fnmatchcase(path, spec.path):
                return KeyCandidate(spec.entity_type, spec.score, f"declared:{spec.path}")
        return None

    def scan(self, text: str, key_hint: str | None) -> list[Hit]:
        hits: list[Hit] = []
        for rec, pat, regex in self._patterns:
            for m in regex.finditer(text):
                score = pat.score
                window = (text[max(0, m.start() - CONTEXT_WINDOW):m.start()] + " " + (key_hint or "")).lower()
                if any(w.lower() in window for w in rec.context):
                    score = min(1.0, score + CONTEXT_BONUS)
                hits.append(Hit(rec.supported_entity, m.start(), m.end(), round(score, 3), f"custom:{rec.name}",
                                f"custom-pattern:{pat.name}"))
        for rec, regex in self._deny:
            for m in regex.finditer(text):
                hits.append(Hit(rec.supported_entity, m.start(), m.end(), 0.9, f"custom:{rec.name}", "deny-list"))
        return hits


def custom_detector_from(recognizers: list[Any], declared: list[Any]) -> PolicyCustomDetector | None:
    """``None`` when the policy declares nothing (so the ensemble skips the tier)."""
    if not recognizers and not declared:
        return None
    return PolicyCustomDetector(
        [RecognizerSpec.model_validate(r) if not isinstance(r, RecognizerSpec) else r for r in recognizers],
        [DeclaredField.model_validate(d) if not isinstance(d, DeclaredField) else d for d in declared],
    )
```

- [ ] **Step 4: Run tests, lint, types**

Run: `pytest -q tests/test_custom.py && ruff check dps tests && mypy dps`
Expected: `4 passed`, clean.

- [ ] **Step 5: Commit**

```bash
git add dps/engine/detect/custom.py tests/test_custom.py
git commit -m "feat(detect): declared fields and custom recognizers in Presidio's YAML shape"
```

---

### Task 11: Policy model and presets

**Files:**
- Create: `dps/engine/policy.py`, `dps/engine/presets/{gdpr-pseudonymise,pci-dss,dpdp-pseudonymise,analytics-safe,synthetic-realistic,synthetic-strict}.yaml`
- Test: `tests/test_policy.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_policy.py`:
```python
from __future__ import annotations

import pytest

from dps.engine.policy import METHOD_NAMES, MethodSpec, Policy, list_presets, load_preset, resolve_policy


def test_all_presets_load_and_validate() -> None:
    names = {p["name"] for p in list_presets()}
    assert names == {"gdpr-pseudonymise", "pci-dss", "dpdp-pseudonymise", "analytics-safe",
                     "synthetic-realistic", "synthetic-strict"}
    for name in names:
        policy = load_preset(name)
        assert isinstance(policy, Policy) and policy.name == name
        for spec in policy.treat.values():
            assert spec.method in METHOD_NAMES


def test_method_spec_flattens_extra_keys() -> None:
    spec = MethodSpec.model_validate({"method": "fpe_digits", "keep_suffix": 4})
    assert spec.method == "fpe_digits" and spec.params == {"keep_suffix": 4}
    with pytest.raises(ValueError):
        MethodSpec.model_validate({"method": "teleport"})


def test_method_for_both_modes() -> None:
    p = load_preset("gdpr-pseudonymise")
    assert p.method_for("SSN", "obfuscate").method == "fpe_digits"
    assert p.method_for("UNKNOWN_THING", "obfuscate").method == "det_token"   # default_sensitive
    assert p.method_for("SSN", "synthesize").method == "surrogate"
    strict = load_preset("synthetic-strict")
    assert strict.consistency.scope == "request" and strict.detect.min_score == 0.5


def test_resolve_policy_merges_overrides_and_hashes_stably() -> None:
    base = resolve_policy(None, default_name="gdpr-pseudonymise")
    merged = resolve_policy({"preset": "gdpr-pseudonymise", "treat": {"EMAIL": {"method": "redact"}},
                             "output": {"token_style": "envelope"}}, default_name="gdpr-pseudonymise")
    assert merged.method_for("EMAIL", "obfuscate").method == "redact"
    assert merged.method_for("SSN", "obfuscate").method == "fpe_digits"  # untouched
    assert merged.output.token_style == "envelope"
    assert base.config_hash() == load_preset("gdpr-pseudonymise").config_hash() != merged.config_hash()
    assert resolve_policy("pci-dss", default_name="x").name == "pci-dss"
    with pytest.raises(KeyError):
        resolve_policy("no-such-preset", default_name="x")
```

- [ ] **Step 2: Run to confirm failure**

Run: `pytest -q tests/test_policy.py`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write `dps/engine/policy.py`**

```python
"""The policy model (spec §5.4): what to detect, how sure to be, what to do, how to stay consistent.

Presets live as YAML next to this module. ``resolve_policy`` accepts a preset name, ``None`` (the
configured default) or a dict that may name a ``preset`` and override any section.
"""
from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from dps.engine.detect.custom import DeclaredField, RecognizerSpec

PRESET_DIR = Path(__file__).parent / "presets"

#: Every method the transform registry (Task 15) implements. Kept here so policies validate
#: without importing the registry; ``methods.py`` asserts it implements exactly this set.
METHOD_NAMES: tuple[str, ...] = (
    "det_token", "fpe_digits", "fpe_text", "email", "phone", "date_shift", "hmac_pseudonym",
    "mask_partial", "redact", "null", "bucket", "generalize", "dictionary", "noise", "keep",
    "surrogate", "external_tokenize",
)


class MethodSpec(BaseModel):
    """``{"method": "fpe_digits", "keep_suffix": 4}`` → ``method="fpe_digits", params={...}``."""

    model_config = ConfigDict(extra="forbid")
    method: str
    params: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _flatten(cls, data: Any) -> Any:
        if isinstance(data, dict) and "params" not in data:
            data = dict(data)
            method = data.pop("method", None)
            return {"method": method, "params": data}
        return data

    @field_validator("method")
    @classmethod
    def _known(cls, value: str) -> str:
        if value not in METHOD_NAMES:
            raise ValueError(f"unknown method {value!r}; known: {', '.join(METHOD_NAMES)}")
        return value


class LocaleConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    default: str = "en_US"
    by_country_field: str | None = None


class DetectConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tiers: list[str] = Field(default_factory=lambda: ["structural", "patterns", "ner"])
    min_score: float = Field(default=0.6, ge=0.0, le=1.0)
    custom_recognizers: list[RecognizerSpec] = Field(default_factory=list)
    declared_fields: list[DeclaredField] = Field(default_factory=list)


class SynthesizeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    default: MethodSpec = Field(default_factory=lambda: MethodSpec(method="surrogate"))
    overrides: dict[str, MethodSpec] = Field(default_factory=dict)
    keep_non_sensitive: bool = True
    llm_rewrite_text: bool = False


class ConsistencyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope: Literal["request", "key", "entity"] = "key"
    field_scoped: bool = False


class OutputConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token_style: Literal["envelope", "natural"] = "natural"


class VerifyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    leakage_check: Literal["strict", "warn"] = "strict"


class Policy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = "custom"
    version: int = 1
    description: str = ""
    locale: LocaleConfig = Field(default_factory=LocaleConfig)
    detect: DetectConfig = Field(default_factory=DetectConfig)
    treat: dict[str, MethodSpec] = Field(default_factory=dict)
    synthesize: SynthesizeConfig = Field(default_factory=SynthesizeConfig)
    consistency: ConsistencyConfig = Field(default_factory=ConsistencyConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)
    verify: VerifyConfig = Field(default_factory=VerifyConfig)

    def method_for(self, entity_type: str, mode: str) -> MethodSpec:
        """The method for ``entity_type`` in ``mode`` (``obfuscate`` | ``synthesize``)."""
        if mode == "synthesize":
            return self.synthesize.overrides.get(entity_type, self.synthesize.default)
        return self.treat.get(entity_type) or self.treat.get("default_sensitive") or MethodSpec(method="det_token")

    def config_hash(self) -> str:
        """Stable sha256 of the policy for reports and evidence packs."""
        canon = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


@lru_cache(maxsize=None)
def load_preset(name: str) -> Policy:
    """Load and validate ``presets/<name>.yaml``; ``KeyError`` when absent."""
    path = PRESET_DIR / f"{name}.yaml"
    if not path.is_file():
        raise KeyError(f"unknown policy preset {name!r}")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return Policy.model_validate(data)


def list_presets() -> list[dict[str, str]]:
    """``[{"name", "description"}]`` for every preset file."""
    out = []
    for path in sorted(PRESET_DIR.glob("*.yaml")):
        policy = load_preset(path.stem)
        out.append({"name": policy.name, "description": policy.description})
    return out


def resolve_policy(ref: str | dict[str, Any] | None, *, default_name: str) -> Policy:
    """Preset name, inline dict (optionally ``{"preset": name, ...overrides}``) or the default."""
    if ref is None:
        return load_preset(default_name)
    if isinstance(ref, str):
        return load_preset(ref)
    override = dict(ref)
    preset = override.pop("preset", None)
    if preset is None:
        return Policy.model_validate(override)
    base = load_preset(str(preset)).model_dump(mode="json")
    merged = _deep_merge(base, override)
    merged.setdefault("name", f"{preset}+overrides")
    return Policy.model_validate(merged)
```

- [ ] **Step 4: Write the six presets**

`dps/engine/presets/gdpr-pseudonymise.yaml`:
```yaml
name: gdpr-pseudonymise
version: 1
description: Reversible pseudonymisation that keeps formats and joins. Output remains personal data (GDPR Art. 4(5)).
locale: { default: en_US }
detect: { tiers: [structural, patterns, ner], min_score: 0.6 }
treat:
  default_sensitive: { method: det_token }
  SSN: { method: fpe_digits, keep_suffix: 4 }
  ITIN: { method: fpe_digits }
  EIN: { method: fpe_digits }
  SIN: { method: fpe_digits }
  AADHAAR: { method: fpe_digits, keep_suffix: 4 }
  PAN_IN: { method: fpe_text }
  CURP: { method: fpe_text }
  RFC: { method: fpe_text }
  NINO: { method: fpe_text }
  NHS_NUMBER: { method: fpe_digits }
  PASSPORT: { method: fpe_text }
  DRIVER_LICENSE: { method: fpe_text }
  CARD_PAN: { method: fpe_digits, keep_prefix: 6, keep_suffix: 4 }
  CARD_EXPIRY: { method: keep }
  CVV: { method: redact }
  IBAN: { method: fpe_text }
  ACCOUNT_NUMBER: { method: fpe_digits }
  PHONE: { method: phone, keep_country_code: true }
  EMAIL: { method: email, domain: keep }
  PERSON_NAME: { method: fpe_text }
  USERNAME: { method: fpe_text }
  DATE_OF_BIRTH: { method: date_shift, window_days: 365 }
  DATE: { method: keep }
  STREET_ADDRESS: { method: det_token }
  POSTAL_CODE: { method: generalize, keep_chars: 3 }
  CITY: { method: keep }
  LOCATION: { method: keep }
  GEO_COORDINATE: { method: det_token }
  IP_ADDRESS: { method: det_token }
  MAC_ADDRESS: { method: det_token }
  URL: { method: keep }
  DEVICE_ID: { method: det_token }
  COOKIE_ID: { method: det_token }
  CUSTOMER_ID: { method: fpe_digits }
  EMPLOYEE_ID: { method: fpe_text }
  CASE_ID: { method: keep }
  ORG_NAME: { method: keep }
  GENDER: { method: keep }
  NATIONALITY: { method: keep }
  AGE: { method: keep }
  AMOUNT: { method: keep }
  API_KEY: { method: redact }
  PASSWORD: { method: redact }
  JWT: { method: redact }
  PRIVATE_KEY: { method: redact }
synthesize:
  default: { method: surrogate }
consistency: { scope: key }
output: { token_style: natural }
verify: { leakage_check: strict }
```

`dps/engine/presets/pci-dss.yaml`:
```yaml
name: pci-dss
version: 1
description: Cardholder-data focus. PAN format-preserving with first-6/last-4 kept, CVV never retained, everything else tokenised.
locale: { default: en_US }
detect: { tiers: [structural, patterns, ner], min_score: 0.6 }
treat:
  default_sensitive: { method: det_token }
  CARD_PAN: { method: fpe_digits, keep_prefix: 6, keep_suffix: 4 }
  CARD_EXPIRY: { method: keep }
  CVV: { method: redact }
  PERSON_NAME: { method: fpe_text }
  EMAIL: { method: email, domain: keep }
  PHONE: { method: phone, keep_country_code: true }
  STREET_ADDRESS: { method: det_token }
  POSTAL_CODE: { method: generalize, keep_chars: 3 }
  DATE_OF_BIRTH: { method: date_shift, window_days: 365 }
  DATE: { method: keep }
  AMOUNT: { method: keep }
  CUSTOMER_ID: { method: fpe_digits }
  API_KEY: { method: redact }
  PASSWORD: { method: redact }
  JWT: { method: redact }
  PRIVATE_KEY: { method: redact }
synthesize:
  default: { method: surrogate }
consistency: { scope: key }
output: { token_style: natural }
verify: { leakage_check: strict }
```

`dps/engine/presets/dpdp-pseudonymise.yaml`:
```yaml
name: dpdp-pseudonymise
version: 1
description: India DPDP pseudonymisation. Aadhaar masked to last 4 (UIDAI style), PAN/voter id format-preserving, Indian locale.
locale: { default: en_IN }
detect: { tiers: [structural, patterns, ner], min_score: 0.6 }
treat:
  default_sensitive: { method: det_token }
  AADHAAR: { method: fpe_digits, keep_suffix: 4 }
  PAN_IN: { method: fpe_text }
  VOTER_ID: { method: fpe_text }
  PASSPORT: { method: fpe_text }
  CARD_PAN: { method: fpe_digits, keep_prefix: 6, keep_suffix: 4 }
  CARD_EXPIRY: { method: keep }
  CVV: { method: redact }
  IFSC: { method: keep }
  ACCOUNT_NUMBER: { method: fpe_digits }
  PHONE: { method: phone, keep_country_code: true }
  EMAIL: { method: email, domain: keep }
  PERSON_NAME: { method: fpe_text }
  DATE_OF_BIRTH: { method: date_shift, window_days: 365 }
  DATE: { method: keep }
  STREET_ADDRESS: { method: det_token }
  POSTAL_CODE: { method: generalize, keep_chars: 3 }
  CITY: { method: keep }
  CUSTOMER_ID: { method: fpe_digits }
  AMOUNT: { method: keep }
  API_KEY: { method: redact }
  PASSWORD: { method: redact }
  JWT: { method: redact }
  PRIVATE_KEY: { method: redact }
synthesize:
  default: { method: surrogate }
consistency: { scope: key }
output: { token_style: natural }
verify: { leakage_check: strict }
```

`dps/engine/presets/analytics-safe.yaml`:
```yaml
name: analytics-safe
version: 1
description: Irreversible. Keyed pseudonyms keep joins, dates generalised to year, addresses removed, amounts lightly perturbed.
locale: { default: en_US }
detect: { tiers: [structural, patterns, ner], min_score: 0.55 }
treat:
  default_sensitive: { method: hmac_pseudonym }
  PERSON_NAME: { method: hmac_pseudonym }
  EMAIL: { method: hmac_pseudonym }
  PHONE: { method: hmac_pseudonym }
  DATE_OF_BIRTH: { method: generalize, granularity: year }
  DATE: { method: keep }
  STREET_ADDRESS: { method: redact }
  POSTAL_CODE: { method: generalize, keep_chars: 3 }
  CITY: { method: keep }
  CARD_PAN: { method: mask_partial, keep_last: 4 }
  CARD_EXPIRY: { method: redact }
  CVV: { method: redact }
  AMOUNT: { method: noise, percent: 5 }
  CUSTOMER_ID: { method: hmac_pseudonym }
  GENDER: { method: keep }
  NATIONALITY: { method: keep }
  AGE: { method: bucket, ranges: [0, 18, 25, 35, 45, 55, 65, 120] }
  API_KEY: { method: redact }
  PASSWORD: { method: redact }
  JWT: { method: redact }
  PRIVATE_KEY: { method: redact }
synthesize:
  default: { method: surrogate }
consistency: { scope: key }
output: { token_style: envelope }
verify: { leakage_check: strict }
```

`dps/engine/presets/synthetic-realistic.yaml`:
```yaml
name: synthetic-realistic
version: 1
description: Synthetic surrogates that look real, agree with each other (one persona per record) and keep non-sensitive values.
locale: { default: en_US }
detect: { tiers: [structural, patterns, ner], min_score: 0.6 }
treat:
  default_sensitive: { method: det_token }
synthesize:
  default: { method: surrogate }
  overrides:
    DATE: { method: keep }
    AMOUNT: { method: keep }
    URL: { method: keep }
  keep_non_sensitive: true
  llm_rewrite_text: false
consistency: { scope: key }
output: { token_style: natural }
verify: { leakage_check: strict }
```

`dps/engine/presets/synthetic-strict.yaml`:
```yaml
name: synthetic-strict
version: 1
description: Synthetic with maximum privacy. Lower detection threshold, every date and location replaced, no cross-request linkage.
locale: { default: en_US }
detect: { tiers: [structural, patterns, ner], min_score: 0.5 }
treat:
  default_sensitive: { method: det_token }
synthesize:
  default: { method: surrogate }
  overrides:
    DATE: { method: surrogate }
    LOCATION: { method: surrogate }
    CITY: { method: surrogate }
    URL: { method: redact }
  keep_non_sensitive: true
  llm_rewrite_text: false
consistency: { scope: request }
output: { token_style: natural }
verify: { leakage_check: strict }
```

- [ ] **Step 5: Run tests, lint, types**

Run: `pytest -q tests/test_policy.py && ruff check dps tests && mypy dps`
Expected: `4 passed`, clean.

- [ ] **Step 6: Commit**

```bash
git add dps/engine/policy.py dps/engine/presets tests/test_policy.py
git commit -m "feat(engine): policy model with presets, overrides and a stable config hash"
```

---

### Task 12: Crypto core — key material, HKDF subkeys, AES-SIV, HMAC, RSA wrap

**Files:**
- Create: `dps/engine/transform/__init__.py`, `dps/engine/transform/crypto.py`
- Test: `tests/test_crypto.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_crypto.py`:
```python
from __future__ import annotations

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from dps.engine.transform.crypto import (
    KeyMaterial,
    b32,
    dek_bytes,
    derive_int,
    det_decrypt,
    det_encrypt,
    generate_key_material,
    key_material_from_caller,
    subkey,
    unb32,
    unwrap_dek,
    wrap_key_material,
)


def test_key_material_shape() -> None:
    km = generate_key_material("key")
    assert km.alg == "dps.v1" and km.kid.startswith("k_") and km.consistency_scope == "key"
    assert len(dek_bytes(km)) == 32 and km.has_dek()
    assert generate_key_material().kid != km.kid


def test_subkeys_are_deterministic_and_separated() -> None:
    km = generate_key_material()
    assert subkey(km, "det_token|EMAIL") == subkey(km, "det_token|EMAIL")
    assert subkey(km, "det_token|EMAIL") != subkey(km, "det_token|SSN")
    other = KeyMaterial(kid="k_other", dek=km.dek, created_at=km.created_at)
    assert subkey(other, "det_token|EMAIL") != subkey(km, "det_token|EMAIL")  # kid is the salt


def test_deterministic_encryption_round_trip_and_aad_binding() -> None:
    key = subkey(generate_key_material(), "det_token|EMAIL")
    ct1 = det_encrypt(key, b"ann@example.com", "EMAIL")
    assert ct1 == det_encrypt(key, b"ann@example.com", "EMAIL")       # deterministic
    assert ct1 != det_encrypt(key, b"ann@example.com", "PERSON_NAME")  # bound to the type
    assert det_decrypt(key, ct1, "EMAIL") == b"ann@example.com"
    with pytest.raises(Exception):  # noqa: B017 - cryptography raises InvalidTag
        det_decrypt(key, ct1, "PERSON_NAME")


def test_base32_tokens_round_trip_and_are_url_safe() -> None:
    raw = bytes(range(0, 37))
    tok = b32(raw)
    assert tok == tok.lower() and "=" not in tok and unb32(tok) == raw


def test_derive_int_is_in_range_and_stable() -> None:
    key = subkey(generate_key_material(), "date_shift|DATE_OF_BIRTH")
    values = {derive_int(key, "anchor-1", 731) for _ in range(3)}
    assert len(values) == 1 and 0 <= values.pop() < 731


def test_wrap_and_unwrap_with_rsa() -> None:
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_pem = private.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    private_pem = private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                        serialization.NoEncryption()).decode()
    km = generate_key_material()
    wrapped = wrap_key_material(km, public_pem)
    assert wrapped.dek is None and wrapped.wrapped_dek and wrapped.wrap["type"] == "rsa-oaep-256"  # type: ignore[index]
    assert not wrapped.has_dek()
    with pytest.raises(KeyError):
        dek_bytes(wrapped)
    assert unwrap_dek(wrapped.wrapped_dek, private_pem) == dek_bytes(km)  # type: ignore[arg-type]


def test_caller_supplied_key_material_is_validated() -> None:
    km = generate_key_material()
    again = key_material_from_caller(km.model_dump(mode="json"))
    assert dek_bytes(again) == dek_bytes(km)
    with pytest.raises(ValueError):
        key_material_from_caller({"alg": "dps.v1", "kid": "k_x", "dek": "c2hvcnQ", "created_at": "2026-01-01T00:00:00Z"})
```

- [ ] **Step 2: Run to confirm failure**

Run: `pytest -q tests/test_crypto.py`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write the modules**

`dps/engine/transform/__init__.py`:
```python
"""Transformation: crypto primitives, tokens, FPE, the method registry, plan application."""
```

`dps/engine/transform/crypto.py`:
```python
"""Key material and the primitives every reversible method is built from (spec §5.5).

* A **DEK** (256-bit) is generated per request and returned to the caller; DPS never stores it.
* **Subkeys** come from HKDF-SHA256 with the key id as salt and ``dps.v1|<method>|<entity>`` as
  info, so a leaked subkey compromises one method/type pair only.
* **Deterministic encryption** is AES-SIV (RFC 5297) with the entity type as associated data —
  the same value under the same key always yields the same token, which is what gives joins and
  cross-system consistency.
* The DEK may be **wrapped** with the caller's RSA public key (OAEP-SHA256) so it never crosses the
  wire in clear.

This module logs nothing and has no debug paths. Keep it that way.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from datetime import UTC, datetime
from typing import Any

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESSIV
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from pydantic import BaseModel, Field

ALG = "dps.v1"
DEK_BYTES = 32
SUBKEY_BYTES = 32


def b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def unb64u(text: str) -> bytes:
    padding_len = (-len(text)) % 4
    return base64.urlsafe_b64decode(text + "=" * padding_len)


def b32(data: bytes) -> str:
    """Lower-case, unpadded base32 — the token alphabet (safe in URLs, SQL and file names)."""
    return base64.b32encode(data).decode("ascii").lower().rstrip("=")


def unb32(text: str) -> bytes:
    padded = text.upper() + "=" * ((-len(text)) % 8)
    return base64.b32decode(padded)


class KeyMaterial(BaseModel):
    """What the caller receives (and later presents) to reveal or to stay consistent."""

    alg: str = ALG
    kid: str
    dek: str | None = None
    wrapped_dek: str | None = None
    wrap: dict[str, str] | None = None
    consistency_scope: str = "request"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def has_dek(self) -> bool:
        return self.dek is not None


def new_kid() -> str:
    return "k_" + secrets.token_hex(8)


def generate_key_material(scope: str = "request") -> KeyMaterial:
    """A fresh 256-bit DEK with a random key id."""
    return KeyMaterial(kid=new_kid(), dek=b64u(secrets.token_bytes(DEK_BYTES)), consistency_scope=scope)


def dek_bytes(km: KeyMaterial) -> bytes:
    """The raw DEK; ``KeyError`` when the material is wrapped-only."""
    if km.dek is None:
        raise KeyError("key material carries no plaintext dek (wrapped only)")
    raw = unb64u(km.dek)
    if len(raw) != DEK_BYTES:
        raise ValueError("dek must be 32 bytes")
    return raw


def key_material_from_caller(payload: dict[str, Any]) -> KeyMaterial:
    """Validate caller-presented key material (reveal, or consistency scope ``key``)."""
    km = KeyMaterial.model_validate(payload)
    if km.alg != ALG:
        raise ValueError(f"unsupported key material alg {km.alg!r}")
    if not km.has_dek():
        raise ValueError("key material must carry the plaintext dek; unwrap it client-side first")
    dek_bytes(km)  # length check
    return km


def subkey(km: KeyMaterial, info: str) -> bytes:
    """HKDF-SHA256 subkey for ``info`` (e.g. ``"det_token|EMAIL"``), salted by the key id."""
    hkdf = HKDF(algorithm=hashes.SHA256(), length=SUBKEY_BYTES, salt=km.kid.encode("utf-8"),
                info=f"{ALG}|{info}".encode("utf-8"))
    return hkdf.derive(dek_bytes(km))


def det_encrypt(key: bytes, plaintext: bytes, aad: str) -> bytes:
    """AES-SIV deterministic authenticated encryption bound to ``aad``."""
    return AESSIV(key).encrypt(plaintext, [aad.encode("utf-8")])


def det_decrypt(key: bytes, ciphertext: bytes, aad: str) -> bytes:
    return AESSIV(key).decrypt(ciphertext, [aad.encode("utf-8")])


def hmac_bytes(key: bytes, data: str) -> bytes:
    return hmac.new(key, data.encode("utf-8"), hashlib.sha256).digest()


def derive_int(key: bytes, data: str, modulus: int) -> int:
    """A stable integer in ``[0, modulus)`` from ``HMAC(key, data)``."""
    if modulus <= 0:
        raise ValueError("modulus must be positive")
    return int.from_bytes(hmac_bytes(key, data)[:8], "big") % modulus


def _oaep() -> padding.OAEP:
    return padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()), algorithm=hashes.SHA256(), label=None)


def wrap_key_material(km: KeyMaterial, public_key_pem: str) -> KeyMaterial:
    """Return a copy whose DEK is RSA-OAEP-256 wrapped with the caller's public key."""
    public = serialization.load_pem_public_key(public_key_pem.encode("utf-8"))
    if not isinstance(public, rsa.RSAPublicKey):
        raise ValueError("wrap_key requires an RSA public key (PEM, SubjectPublicKeyInfo)")
    spki = public.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    wrapped = public.encrypt(dek_bytes(km), _oaep())
    return km.model_copy(update={
        "dek": None,
        "wrapped_dek": base64.b64encode(wrapped).decode("ascii"),
        "wrap": {"type": "rsa-oaep-256", "fingerprint": hashlib.sha256(spki).hexdigest()},
    })


def unwrap_dek(wrapped_dek_b64: str, private_key_pem: str, password: bytes | None = None) -> bytes:
    """Client-side helper (and test oracle): recover the DEK with the matching private key."""
    private = serialization.load_pem_private_key(private_key_pem.encode("utf-8"), password=password)
    if not isinstance(private, rsa.RSAPrivateKey):
        raise ValueError("unwrap requires an RSA private key")
    return private.decrypt(base64.b64decode(wrapped_dek_b64), _oaep())
```

- [ ] **Step 4: Run tests, lint, types**

Run: `pytest -q tests/test_crypto.py && ruff check dps tests && mypy dps`
Expected: `7 passed`, clean.

- [ ] **Step 5: Commit**

```bash
git add dps/engine/transform tests/test_crypto.py
git commit -m "feat(transform): key material, HKDF subkeys, AES-SIV deterministic encryption, RSA wrap"
```

---

### Task 13: Token envelope

**Files:**
- Create: `dps/engine/transform/tokens.py`
- Test: `tests/test_tokens.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_tokens.py`:
```python
from __future__ import annotations

from dps.engine.transform.tokens import is_envelope, make_envelope, parse_envelopes


def test_make_and_parse_envelopes() -> None:
    text = f"Contact {make_envelope('EMAIL', 'k7fq2wxb3n')} or {make_envelope('PERSON_NAME', 'abcd')}."
    found = parse_envelopes(text)
    assert [(e, t) for _, _, e, t in found] == [("EMAIL", "k7fq2wxb3n"), ("PERSON_NAME", "abcd")]
    start, end, _, _ = found[0]
    assert text[start:end] == "[[PII:EMAIL:k7fq2wxb3n]]"


def test_is_envelope_is_strict() -> None:
    assert is_envelope("[[PII:SSN:abc234]]")
    assert not is_envelope("[[PII:ssn:abc]]") and not is_envelope("x[[PII:SSN:abc234]]")
    assert not is_envelope("[[PII:SSN:ABC1]]")  # upper case / digits 0,1,8,9 are not base32 here
```

- [ ] **Step 2: Run to confirm failure**

Run: `pytest -q tests/test_tokens.py`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write `dps/engine/transform/tokens.py`**

```python
"""The envelope token style: ``[[PII:ENTITY_TYPE:base32token]]`` (spec §5.5).

Self-describing, so ``reveal`` needs no hints for text; the inner token is lower-case base32
(``a-z2-7``) and the entity name is upper snake case.
"""
from __future__ import annotations

import re

ENVELOPE_RE = re.compile(r"\[\[PII:([A-Z][A-Z0-9_]{0,63}):([a-z2-7]{2,})\]\]")


def make_envelope(entity_type: str, token: str) -> str:
    return f"[[PII:{entity_type}:{token}]]"


def parse_envelopes(text: str) -> list[tuple[int, int, str, str]]:
    """``(start, end, entity_type, token)`` for every envelope in ``text``, in order."""
    return [(m.start(), m.end(), m.group(1), m.group(2)) for m in ENVELOPE_RE.finditer(text)]


def is_envelope(value: str) -> bool:
    return ENVELOPE_RE.fullmatch(value) is not None
```

- [ ] **Step 4: Run tests, lint, types; commit**

Run: `pytest -q tests/test_tokens.py && ruff check dps tests && mypy dps` → `2 passed`.
```bash
git add dps/engine/transform/tokens.py tests/test_tokens.py
git commit -m "feat(transform): self-describing token envelope"
```

---

### Task 14: Optional FF1 adapter with the minimum-domain guard

**Files:**
- Create: `dps/engine/transform/fpe.py`
- Test: `tests/test_fpe.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_fpe.py`:
```python
from __future__ import annotations

import pytest

from dps.engine.transform.fpe import (
    DIGITS,
    LOWER,
    Ff1,
    domain_ok,
    fpe_available,
    fpe_digits_decrypt,
    fpe_digits_encrypt,
    fpe_text_decrypt,
    fpe_text_encrypt,
)


class RotCipher:
    """Stand-in for FF1 in tests: a fixed rotation within the alphabet (a permutation)."""

    def encrypt(self, alphabet: str, plaintext: str) -> str:
        return "".join(alphabet[(alphabet.index(c) + 3) % len(alphabet)] for c in plaintext)

    def decrypt(self, alphabet: str, ciphertext: str) -> str:
        return "".join(alphabet[(alphabet.index(c) - 3) % len(alphabet)] for c in ciphertext)


def test_domain_guard() -> None:
    assert domain_ok(10, 6) and not domain_ok(10, 5)
    assert domain_ok(26, 5) and not domain_ok(26, 4)


def test_digits_keep_prefix_suffix_and_format() -> None:
    out = fpe_digits_encrypt(RotCipher(), "4532 7811 2290 4417", keep_prefix=6, keep_suffix=4)
    assert out is not None and out.startswith("4532 78") and out.endswith("4417") and out[7] == "1"
    assert out.count(" ") == 3 and out != "4532 7811 2290 4417"
    assert fpe_digits_decrypt(RotCipher(), out, keep_prefix=6, keep_suffix=4) == "4532 7811 2290 4417"
    assert fpe_digits_encrypt(RotCipher(), "12-34", keep_prefix=0, keep_suffix=0) is None  # 4 < 6 digits


def test_text_preserves_case_pattern_and_separators() -> None:
    out = fpe_text_encrypt(RotCipher(), "Ananya Krishnan-Rao")
    assert out is not None and out != "Ananya Krishnan-Rao"
    assert out[0].isupper() and out[1].islower() and out[6] == " " and out[15] == "-"
    assert fpe_text_decrypt(RotCipher(), out) == "Ananya Krishnan-Rao"
    assert fpe_text_encrypt(RotCipher(), "Li") is None            # 2 letters: domain too small
    assert fpe_text_encrypt(RotCipher(), "Ann 42") is None        # 3 letters, 2 digits: too small


@pytest.mark.fpe
def test_real_ff1_round_trip() -> None:
    pytest.importorskip("fastfpe")
    assert fpe_available()
    cipher = Ff1(bytes(range(32)), tweak="SSN")
    ct = cipher.encrypt(DIGITS, "123456789")
    assert ct != "123456789" and len(ct) == 9 and cipher.decrypt(DIGITS, ct) == "123456789"
    assert cipher.decrypt(LOWER, cipher.encrypt(LOWER, "ananyak")) == "ananyak"
```

- [ ] **Step 2: Run to confirm failure**

Run: `pytest -q tests/test_fpe.py`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write `dps/engine/transform/fpe.py`**

```python
"""Format-preserving encryption (FF1, NIST SP 800-38G) behind an optional extra.

``fastfpe`` (MIT) provides FF1; it is installed only with the ``[fpe]`` extra and used only when
``DPS_FPE_ENABLED=true`` (spec D8: license review first). Everything else here is pure Python:
the **domain guard** (``radix**length >= 1_000_000``, NIST's revised minimum) and the
split/rebuild logic that encrypts only the digit or letter positions of a value while keeping
separators, case pattern and kept prefixes/suffixes. When the guard fails, callers fall back to
``det_token`` and record the reason in the plan.

Non-ASCII letters (``Ñ``, accented characters) pass through unchanged — a documented v1 limit;
names dominated by them should be routed to ``det_token`` by policy.
"""
from __future__ import annotations

import importlib.util
from string import ascii_lowercase, ascii_uppercase
from typing import Protocol

MIN_DOMAIN = 1_000_000
DIGITS = "0123456789"
LOWER = ascii_lowercase
UPPER = ascii_uppercase


class Cipher(Protocol):
    def encrypt(self, alphabet: str, plaintext: str) -> str: ...

    def decrypt(self, alphabet: str, ciphertext: str) -> str: ...


def fpe_available() -> bool:
    return importlib.util.find_spec("fastfpe") is not None


def domain_ok(radix: int, length: int) -> bool:
    return length > 0 and radix**length >= MIN_DOMAIN


class Ff1:
    """FF1 over an arbitrary alphabet via ``fastfpe`` (key: 32 bytes → AES-256)."""

    def __init__(self, key: bytes, tweak: str) -> None:
        if len(key) not in (16, 24, 32):
            raise ValueError("FF1 key must be 16, 24 or 32 bytes")
        from fastfpe import ff1  # lazy: optional extra

        self._ff1 = ff1
        self._key_hex = key.hex()
        self._tweak = tweak

    def encrypt(self, alphabet: str, plaintext: str) -> str:
        return str(self._ff1.encrypt(self._key_hex, self._tweak, alphabet, plaintext))

    def decrypt(self, alphabet: str, ciphertext: str) -> str:
        return str(self._ff1.decrypt(self._key_hex, self._tweak, alphabet, ciphertext))


# --- digits -----------------------------------------------------------------------------------
def _digit_positions(text: str) -> list[int]:
    return [i for i, ch in enumerate(text) if ch in DIGITS]


def _rebuild_digits(text: str, positions: list[int], digits: str) -> str:
    chars = list(text)
    for pos, digit in zip(positions, digits, strict=True):
        chars[pos] = digit
    return "".join(chars)


def _fpe_digits(cipher: Cipher, text: str, keep_prefix: int, keep_suffix: int, *, decrypt: bool) -> str | None:
    positions = _digit_positions(text)
    digits = "".join(text[i] for i in positions)
    end = len(digits) - keep_suffix
    middle = digits[keep_prefix:end]
    if not domain_ok(10, len(middle)):
        return None
    coded = cipher.decrypt(DIGITS, middle) if decrypt else cipher.encrypt(DIGITS, middle)
    return _rebuild_digits(text, positions, digits[:keep_prefix] + coded + digits[end:])


def fpe_digits_encrypt(cipher: Cipher, text: str, *, keep_prefix: int = 0, keep_suffix: int = 0) -> str | None:
    """Encrypt the digit positions between a kept prefix and suffix; ``None`` when the domain is too small."""
    return _fpe_digits(cipher, text, keep_prefix, keep_suffix, decrypt=False)


def fpe_digits_decrypt(cipher: Cipher, text: str, *, keep_prefix: int = 0, keep_suffix: int = 0) -> str:
    out = _fpe_digits(cipher, text, keep_prefix, keep_suffix, decrypt=True)
    if out is None:
        raise ValueError("ciphertext digit domain too small — not produced by fpe_digits")
    return out


# --- text (letters + digits, case pattern kept) ----------------------------------------------
def _fpe_text(cipher: Cipher, text: str, *, decrypt: bool) -> str | None:
    letter_pos = [i for i, ch in enumerate(text) if ch in LOWER or ch in UPPER]
    digit_pos = _digit_positions(text)
    letters = "".join(text[i].lower() for i in letter_pos)
    digits = "".join(text[i] for i in digit_pos)
    if not domain_ok(26, len(letters)):
        return None
    if digits and not domain_ok(10, len(digits)):
        return None
    op = cipher.decrypt if decrypt else cipher.encrypt
    coded_letters = op(LOWER, letters)
    coded_digits = op(DIGITS, digits) if digits else ""
    chars = list(text)
    for pos, ch in zip(letter_pos, coded_letters, strict=True):
        chars[pos] = ch.upper() if text[pos].isupper() else ch
    for pos, ch in zip(digit_pos, coded_digits, strict=True):
        chars[pos] = ch
    return "".join(chars)


def fpe_text_encrypt(cipher: Cipher, text: str) -> str | None:
    """Encrypt letters (radix 26, case pattern kept) and digits (radix 10) in place; ``None`` if too short."""
    return _fpe_text(cipher, text, decrypt=False)


def fpe_text_decrypt(cipher: Cipher, text: str) -> str:
    out = _fpe_text(cipher, text, decrypt=True)
    if out is None:
        raise ValueError("ciphertext domain too small — not produced by fpe_text")
    return out
```

- [ ] **Step 4: Run tests, lint, types**

Run: `pytest -q tests/test_fpe.py && ruff check dps tests && mypy dps`
Expected: `3 passed, 1 skipped` without the extra (`4 passed` with `uv pip install -e ".[fpe]"`), clean.

- [ ] **Step 5: Commit**

```bash
git add dps/engine/transform/fpe.py tests/test_fpe.py
git commit -m "feat(transform): optional FF1 adapter with NIST domain guard and format-keeping split/rebuild"
```

---

### Task 15: The method registry

**Files:**
- Create: `dps/engine/transform/methods.py`
- Test: `tests/test_methods.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_methods.py`:
```python
from __future__ import annotations

import pytest

from dps.engine.policy import METHOD_NAMES
from dps.engine.transform.crypto import generate_key_material
from dps.engine.transform.methods import (
    METHODS,
    MethodError,
    TransformContext,
    reverse_method,
    run_method,
)


class RotCipher:
    def encrypt(self, alphabet: str, plaintext: str) -> str:
        return "".join(alphabet[(alphabet.index(c) + 3) % len(alphabet)] for c in plaintext)

    def decrypt(self, alphabet: str, ciphertext: str) -> str:
        return "".join(alphabet[(alphabet.index(c) - 3) % len(alphabet)] for c in ciphertext)


class FakeSynth:
    def surrogate(self, entity_type: str, original: str, locale: str, anchor: str) -> str:
        return f"<{entity_type}:{anchor}>"


KM = generate_key_material("key")


def ctx(entity: str, method_params: dict | None = None, **kw) -> TransformContext:  # noqa: ANN003
    base = dict(entity_type=entity, path=f"$.{entity.lower()}", mode="obfuscate", params=method_params or {},
                key=KM, anchor="rec-1", fpe_enabled=True, cipher_factory=lambda key, tweak: RotCipher())
    base.update(kw)
    return TransformContext(**base)  # type: ignore[arg-type]


def test_registry_matches_policy_method_names() -> None:
    assert set(METHODS) == set(METHOD_NAMES)


def test_det_token_round_trip_and_determinism() -> None:
    c = ctx("EMAIL")
    out = run_method("det_token", "ann@example.com", c)
    assert out.value and out.value != "ann@example.com" and out.reversible and out.method == "det_token"
    assert out.value == run_method("det_token", "ann@example.com", c).value
    assert out.value != run_method("det_token", "ann@example.com", ctx("PERSON_NAME")).value
    assert reverse_method("det_token", out.value, c, out.reverse_params) == "ann@example.com"


def test_fpe_digits_and_text_with_fallbacks() -> None:
    out = run_method("fpe_digits", "4532 7811 2290 4417", ctx("CARD_PAN", {"keep_prefix": 6, "keep_suffix": 4}))
    assert out.method == "fpe_digits" and out.value and out.value.startswith("4532 78") and out.value.endswith("4417")
    assert reverse_method("fpe_digits", out.value, ctx("CARD_PAN", {"keep_prefix": 6, "keep_suffix": 4}),
                          out.reverse_params) == "4532 7811 2290 4417"
    small = run_method("fpe_digits", "12-34", ctx("SSN"))
    assert small.method == "det_token" and small.note == "fallback:fpe_domain_too_small"
    disabled = run_method("fpe_text", "Ananya Krishnan", ctx("PERSON_NAME", fpe_enabled=False))
    assert disabled.method == "det_token" and disabled.note == "fallback:fpe_unavailable"
    name = run_method("fpe_text", "Ananya Krishnan", ctx("PERSON_NAME"))
    assert name.value and name.value[0].isupper() and " " in name.value
    assert reverse_method("fpe_text", name.value, ctx("PERSON_NAME"), name.reverse_params) == "Ananya Krishnan"


def test_email_and_phone_keep_shape() -> None:
    out = run_method("email", "ananya.k@example-mail.in", ctx("EMAIL", {"domain": "keep"}))
    assert out.value and out.value.endswith("@example-mail.in") and not out.value.startswith("ananya.k")
    assert reverse_method("email", out.value, ctx("EMAIL", {"domain": "keep"}), out.reverse_params) == "ananya.k@example-mail.in"
    gen = run_method("email", "ananya.k@example-mail.in", ctx("EMAIL", {"domain": "generalize"}))
    assert gen.value and gen.value.endswith("@example.com") and gen.reversible is False
    phone = run_method("phone", "+91 98451 20377", ctx("PHONE", {"keep_country_code": True}))
    assert phone.value and phone.value.startswith("+91 ") and phone.value != "+91 98451 20377"
    assert reverse_method("phone", phone.value, ctx("PHONE", {"keep_country_code": True}), phone.reverse_params) == "+91 98451 20377"


def test_date_shift_preserves_format_and_intervals() -> None:
    c = ctx("DATE_OF_BIRTH", {"window_days": 365})
    a = run_method("date_shift", "1988-03-14", c)
    b = run_method("date_shift", "1988-03-20", c)
    assert a.value != "1988-03-14" and len(a.value or "") == 10
    from datetime import date
    assert (date.fromisoformat(b.value) - date.fromisoformat(a.value)).days == 6  # type: ignore[arg-type]
    assert reverse_method("date_shift", a.value, c, a.reverse_params) == "1988-03-14"
    slash = run_method("date_shift", "03/14/1988", ctx("DATE_OF_BIRTH", locale="en_US"))
    assert slash.value and slash.value.count("/") == 2
    assert run_method("date_shift", "not a date", c).method == "det_token"


def test_irreversible_methods() -> None:
    assert run_method("hmac_pseudonym", "ann", ctx("PERSON_NAME")).value == run_method("hmac_pseudonym", "ann", ctx("PERSON_NAME")).value
    assert len(run_method("hmac_pseudonym", "ann", ctx("PERSON_NAME", {"length": 12})).value or "") == 12
    assert run_method("mask_partial", "4532-7811-2290-4417", ctx("CARD_PAN", {"keep_last": 4})).value == "****-****-****-4417"
    assert run_method("redact", "x", ctx("SSN")).value == "[REDACTED]"
    assert run_method("null", "x", ctx("SSN")).value is None
    assert run_method("bucket", "33", ctx("AGE", {"ranges": [0, 18, 25, 35, 45]})).value == "25-34"
    assert run_method("generalize", "1988-03-14", ctx("DATE_OF_BIRTH", {"granularity": "year"})).value == "1988"
    assert run_method("generalize", "560001", ctx("POSTAL_CODE", {"keep_chars": 3})).value == "560xxx"
    assert run_method("dictionary", "ann", ctx("PERSON_NAME", {"values": ["Alex", "Sam"]})).value in {"Alex", "Sam"}
    noisy = run_method("noise", "1000", ctx("AMOUNT", {"percent": 5}))
    assert noisy.value and 950 <= int(noisy.value) <= 1050
    assert run_method("keep", "x", ctx("DATE")).value == "x"
    assert run_method("surrogate", "Ann", ctx("PERSON_NAME", synth=FakeSynth(), mode="synthesize")).value == "<PERSON_NAME:rec-1>"


def test_errors_are_explicit() -> None:
    with pytest.raises(MethodError):
        run_method("det_token", "x", ctx("SSN", key=None))
    with pytest.raises(MethodError):
        run_method("surrogate", "x", ctx("SSN", synth=None))
    with pytest.raises(MethodError):
        run_method("external_tokenize", "x", ctx("SSN"))
    with pytest.raises(MethodError):
        reverse_method("hmac_pseudonym", "abc", ctx("SSN"), {})
```

- [ ] **Step 2: Run to confirm failure**

Run: `pytest -q tests/test_methods.py`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write `dps/engine/transform/methods.py`**

```python
"""The method registry (spec §5.5): what each transformation does, and how it is reversed.

``run_method`` applies a method by name with the fallbacks the spec requires (FPE unavailable or
domain too small → ``det_token``, unparseable date → ``det_token``) and returns an
:class:`Outcome` that names the method actually used and the parameters ``reverse_method``
needs later. Methods never log and never raise on bad values — only on missing prerequisites
(no key, no surrogate engine), which are programming or configuration errors.
"""
from __future__ import annotations

import math
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Protocol

import phonenumbers

from dps.engine.policy import METHOD_NAMES
from dps.engine.transform import crypto, fpe
from dps.engine.transform.crypto import KeyMaterial


class SurrogateProvider(Protocol):
    def surrogate(self, entity_type: str, original: str, locale: str, anchor: str) -> str: ...


CipherFactory = Callable[[bytes, str], fpe.Cipher]


class MethodError(ValueError):
    """A method cannot run in this context (no key, no engine, not reversible)."""


@dataclass
class TransformContext:
    entity_type: str
    path: str
    mode: str
    params: dict[str, Any] = field(default_factory=dict)
    key: KeyMaterial | None = None
    anchor: str = "record"
    field_scoped: bool = False
    locale: str = "en_US"
    fpe_enabled: bool = False
    synth: SurrogateProvider | None = None
    cipher_factory: CipherFactory | None = None

    def subkey(self, method: str) -> bytes:
        if self.key is None:
            raise MethodError(f"method {method!r} needs key material")
        info = f"{method}|{self.entity_type}" + (f"|{self.path}" if self.field_scoped else "")
        return crypto.subkey(self.key, info)

    def cipher(self, method: str) -> fpe.Cipher:
        tweak = self.entity_type + (f"|{self.path}" if self.field_scoped else "")
        key = self.subkey(method)
        if self.cipher_factory is not None:
            return self.cipher_factory(key, tweak)
        return fpe.Ff1(key, tweak)

    def fpe_ready(self) -> bool:
        return self.fpe_enabled and (self.cipher_factory is not None or fpe.fpe_available())


@dataclass(frozen=True)
class Outcome:
    value: str | None
    method: str
    reversible: bool
    note: str | None = None
    reverse_params: dict[str, Any] = field(default_factory=dict)


_TOO_SMALL = object()


# --- reversible --------------------------------------------------------------------------------
def _det_token(value: str, ctx: TransformContext) -> str:
    return crypto.b32(crypto.det_encrypt(ctx.subkey("det_token"), value.encode("utf-8"), ctx.entity_type))


def _det_reverse(token: str, ctx: TransformContext) -> str:
    try:
        return crypto.det_decrypt(ctx.subkey("det_token"), crypto.unb32(token), ctx.entity_type).decode("utf-8")
    except Exception as exc:  # noqa: BLE001 - wrong key/type: say so without echoing anything
        raise MethodError("det_token could not be reversed with this key material") from exc


def _fpe_digits(value: str, ctx: TransformContext) -> str | object:
    out = fpe.fpe_digits_encrypt(ctx.cipher("fpe_digits"), value, keep_prefix=int(ctx.params.get("keep_prefix", 0)),
                                 keep_suffix=int(ctx.params.get("keep_suffix", 0)))
    return _TOO_SMALL if out is None else out


def _fpe_digits_reverse(token: str, ctx: TransformContext) -> str:
    return fpe.fpe_digits_decrypt(ctx.cipher("fpe_digits"), token, keep_prefix=int(ctx.params.get("keep_prefix", 0)),
                                  keep_suffix=int(ctx.params.get("keep_suffix", 0)))


def _fpe_text(value: str, ctx: TransformContext) -> str | object:
    out = fpe.fpe_text_encrypt(ctx.cipher("fpe_text"), value)
    return _TOO_SMALL if out is None else out


def _fpe_text_reverse(token: str, ctx: TransformContext) -> str:
    return fpe.fpe_text_decrypt(ctx.cipher("fpe_text"), token)


def _email(value: str, ctx: TransformContext) -> Outcome:
    local, sep, domain = value.rpartition("@")
    if not sep:
        return _as_outcome("det_token", _det_token(value, ctx), ctx, note="email:no_at_sign")
    sub = "det_token"
    coded: str | object = _TOO_SMALL
    if ctx.fpe_ready():
        coded = _fpe_text(local, ctx)
        if coded is not _TOO_SMALL:
            sub = "fpe_text"
    if coded is _TOO_SMALL:
        coded = _det_token(local, ctx)
    mode = str(ctx.params.get("domain", "keep"))
    if mode == "generalize":
        return Outcome(f"{coded}@example.com", "email", False, None, {"sub": sub, "domain": "generalize"})
    return Outcome(f"{coded}@{domain}", "email", True, None, {"sub": sub, "domain": "keep"})


def _email_reverse(token: str, ctx: TransformContext, rp: dict[str, Any]) -> str:
    if rp.get("domain") == "generalize":
        raise MethodError("email with domain=generalize is not reversible")
    local, _, domain = token.rpartition("@")
    plain = _fpe_text_reverse(local, ctx) if rp.get("sub") == "fpe_text" else _det_reverse(local, ctx)
    return f"{plain}@{domain}"


def _country_code_len(value: str, ctx: TransformContext) -> int:
    if not value.strip().startswith("+") or not ctx.params.get("keep_country_code", True):
        return 0
    try:
        parsed = phonenumbers.parse(value, None)
        return len(str(parsed.country_code))
    except phonenumbers.NumberParseException:
        return 0


def _phone(value: str, ctx: TransformContext) -> Outcome:
    keep = _country_code_len(value, ctx)
    if ctx.fpe_ready():
        out = fpe.fpe_digits_encrypt(ctx.cipher("phone"), value, keep_prefix=keep)
        if out is not None:
            return Outcome(out, "phone", True, None, {"sub": "fpe_digits", "keep_prefix": keep})
        note = "fallback:fpe_domain_too_small"
    else:
        note = "fallback:fpe_unavailable"
    return _as_outcome("det_token", _det_token(value, ctx), ctx, note=note)


def _phone_reverse(token: str, ctx: TransformContext, rp: dict[str, Any]) -> str:
    return fpe.fpe_digits_decrypt(ctx.cipher("phone"), token, keep_prefix=int(rp.get("keep_prefix", 0)))


_DATE_FORMATS = ("%Y-%m-%d", "%d %b %Y", "%d %B %Y", "%d %b, %Y")


def _parse_date(value: str, locale: str) -> tuple[date | datetime, str] | None:
    text = value.strip()
    try:
        dt = datetime.fromisoformat(text)
        if "T" in text or " " in text:
            return dt, "iso-datetime"
        return dt.date(), "%Y-%m-%d"
    except ValueError:
        pass
    slash = "%m/%d/%Y" if locale.endswith("US") else "%d/%m/%Y"
    for fmt in (slash, *_DATE_FORMATS):
        try:
            return datetime.strptime(text, fmt).date(), fmt
        except ValueError:
            continue
    return None


def _format_date(d: date | datetime, fmt: str) -> str:
    return d.isoformat() if fmt == "iso-datetime" else d.strftime(fmt)


def _offset_days(ctx: TransformContext) -> int:
    window = int(ctx.params.get("window_days", 365))
    offset = crypto.derive_int(ctx.subkey("date_shift"), ctx.anchor, 2 * window + 1) - window
    return offset if offset != 0 else 1


def _date_shift(value: str, ctx: TransformContext) -> Outcome:
    parsed = _parse_date(value, ctx.locale)
    if parsed is None:
        return _as_outcome("det_token", _det_token(value, ctx), ctx, note="fallback:unparseable_date")
    d, fmt = parsed
    shifted = d + timedelta(days=_offset_days(ctx))
    return Outcome(_format_date(shifted, fmt), "date_shift", True, None, {"anchor": ctx.anchor, "fmt": fmt})


def _date_shift_reverse(token: str, ctx: TransformContext, rp: dict[str, Any]) -> str:
    parsed = _parse_date(token, ctx.locale)
    if parsed is None:
        raise MethodError("date_shift token is not a date")
    d, fmt = parsed
    anchor_ctx = TransformContext(**{**ctx.__dict__, "anchor": str(rp.get("anchor", ctx.anchor))})
    return _format_date(d - timedelta(days=_offset_days(anchor_ctx)), str(rp.get("fmt", fmt)))


# --- irreversible ------------------------------------------------------------------------------
def _hmac_pseudonym(value: str, ctx: TransformContext) -> str:
    length = int(ctx.params.get("length", 16))
    return crypto.b32(crypto.hmac_bytes(ctx.subkey("hmac_pseudonym"), value))[:length]


def _mask_partial(value: str, ctx: TransformContext) -> str:
    keep_last, keep_first = int(ctx.params.get("keep_last", 4)), int(ctx.params.get("keep_first", 0))
    char = str(ctx.params.get("char", "*"))[:1] or "*"
    positions = [i for i, ch in enumerate(value) if ch.isalnum()]
    to_mask = set(positions[keep_first:len(positions) - keep_last] if keep_last else positions[keep_first:])
    return "".join(char if i in to_mask else ch for i, ch in enumerate(value))


def _bucket(value: str, ctx: TransformContext) -> str:
    try:
        number = float(value.replace(",", ""))
    except ValueError:
        return "[REDACTED]"
    edges = [float(x) for x in ctx.params.get("ranges", [])]
    for lo, hi in zip(edges, edges[1:], strict=False):
        if lo <= number < hi:
            return f"{int(lo)}-{int(hi) - 1}"
    return f"{int(edges[-1])}+" if edges and number >= edges[-1] else f"<{int(edges[0])}" if edges else "[REDACTED]"


def _generalize(value: str, ctx: TransformContext) -> str:
    parsed = _parse_date(value, ctx.locale)
    if parsed is not None:
        d, _ = parsed
        return f"{d.year}" if ctx.params.get("granularity", "year") == "year" else f"{d.year}-{d.month:02d}"
    keep = int(ctx.params.get("keep_chars", 3))
    return value[:keep] + "x" * max(0, len(value) - keep)


def _dictionary(value: str, ctx: TransformContext) -> str:
    values = [str(v) for v in ctx.params.get("values", [])]
    if not values:
        return "[REDACTED]"
    index = crypto.derive_int(ctx.subkey("dictionary"), value, len(values)) if ctx.key else hash(value) % len(values)
    return values[index]


def _noise(value: str, ctx: TransformContext) -> str:
    try:
        number = float(value)
    except ValueError:
        return value
    percent = float(ctx.params.get("percent", 5))
    unit = (crypto.derive_int(ctx.subkey("noise"), value, 20001) - 10000) / 10000 if ctx.key else 0.0
    noisy = number * (1 + unit * percent / 100)
    return str(int(round(noisy))) if re.fullmatch(r"-?\d+", value) else f"{noisy:.{max(0, len(value.split('.')[-1]) if '.' in value else 0)}f}"


def _surrogate(value: str, ctx: TransformContext) -> str:
    if ctx.synth is None:
        raise MethodError("surrogate needs a surrogate engine in the context")
    return ctx.synth.surrogate(ctx.entity_type, value, ctx.locale, ctx.anchor)


def _external(value: str, ctx: TransformContext) -> str:
    raise MethodError("external_tokenize: no tokenization adapter is configured (M5)")


# --- registry ----------------------------------------------------------------------------------
@dataclass(frozen=True)
class Method:
    name: str
    reversible: bool
    apply: Callable[[str, TransformContext], Any]
    reverse: Callable[[str, TransformContext, dict[str, Any]], str] | None = None


METHODS: dict[str, Method] = {
    "det_token": Method("det_token", True, _det_token, lambda t, c, _rp: _det_reverse(t, c)),
    "fpe_digits": Method("fpe_digits", True, _fpe_digits, lambda t, c, _rp: _fpe_digits_reverse(t, c)),
    "fpe_text": Method("fpe_text", True, _fpe_text, lambda t, c, _rp: _fpe_text_reverse(t, c)),
    "email": Method("email", True, _email, _email_reverse),
    "phone": Method("phone", True, _phone, _phone_reverse),
    "date_shift": Method("date_shift", True, _date_shift, _date_shift_reverse),
    "hmac_pseudonym": Method("hmac_pseudonym", False, _hmac_pseudonym),
    "mask_partial": Method("mask_partial", False, _mask_partial),
    "redact": Method("redact", False, lambda v, c: str(c.params.get("label", "[REDACTED]"))),
    "null": Method("null", False, lambda v, c: None),
    "bucket": Method("bucket", False, _bucket),
    "generalize": Method("generalize", False, _generalize),
    "dictionary": Method("dictionary", False, _dictionary),
    "noise": Method("noise", False, _noise),
    "keep": Method("keep", False, lambda v, c: v),
    "surrogate": Method("surrogate", False, _surrogate),
    "external_tokenize": Method("external_tokenize", True, _external),
}
assert set(METHODS) == set(METHOD_NAMES), "methods.py and policy.METHOD_NAMES disagree"

_FPE_METHODS = {"fpe_digits", "fpe_text"}


def _as_outcome(method: str, value: str | None, ctx: TransformContext, note: str | None = None) -> Outcome:
    return Outcome(value, method, METHODS[method].reversible, note, {})


def run_method(name: str, value: str, ctx: TransformContext) -> Outcome:
    """Apply ``name`` to ``value`` with the spec's fallbacks; raises :class:`MethodError` only for
    missing prerequisites."""
    method = METHODS[name]
    if name in _FPE_METHODS and not ctx.fpe_ready():
        return _as_outcome("det_token", _det_token(value, ctx), ctx, note="fallback:fpe_unavailable")
    result = method.apply(value, ctx)
    if isinstance(result, Outcome):
        return result
    if result is _TOO_SMALL:
        return _as_outcome("det_token", _det_token(value, ctx), ctx, note="fallback:fpe_domain_too_small")
    if result is not None and not isinstance(result, str):
        raise MethodError(f"method {name!r} returned {type(result).__name__}")
    return Outcome(result, name, method.reversible, None, {})


def reverse_method(name: str, token: str, ctx: TransformContext, reverse_params: dict[str, Any]) -> str:
    """Invert a reversible method; ``MethodError`` for irreversible ones or wrong key material."""
    method = METHODS[name]
    if not method.reversible or method.reverse is None:
        raise MethodError(f"method {name!r} is not reversible")
    if name in _FPE_METHODS and not ctx.fpe_ready():
        raise MethodError(f"method {name!r} needs FPE enabled to reverse")
    return method.reverse(token, ctx, reverse_params)
```

- [ ] **Step 4: Run tests, lint, types**

Run: `pytest -q tests/test_methods.py && ruff check dps tests && mypy dps`
Expected: `8 passed`, clean. (`mypy` may need `# type: ignore[arg-type]` on the two lambdas in `METHODS` that return `None`/`str` — add them only where mypy points.)

- [ ] **Step 5: Commit**

```bash
git add dps/engine/transform/methods.py tests/test_methods.py
git commit -m "feat(transform): method registry with reversible and irreversible methods and spec fallbacks"
```

---

### Task 16: Planner — findings + policy → plan

**Files:**
- Create: `dps/engine/planner.py`
- Test: `tests/test_planner.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_planner.py`:
```python
from __future__ import annotations

from dps.engine.findings import Finding, Locator
from dps.engine.planner import build_plan, explain_plan
from dps.engine.policy import load_preset
from dps.engine.taxonomy import Sensitivity


def _f(entity: str, path: str, score: float = 0.95) -> Finding:
    from dps.engine.taxonomy import entity_info
    return Finding(locator=Locator(path=path), entity_type=entity, score=score, detectors=["t"],
                   sensitivity=entity_info(entity).sensitivity)


FINDINGS = [_f("SSN", "$.ssn"), _f("EMAIL", "$.email"), _f("CITY", "$.city"), _f("GADGET_ID", "$.g")]


def test_obfuscate_plan_follows_policy_and_falls_back_without_fpe() -> None:
    plan = build_plan(FINDINGS, load_preset("gdpr-pseudonymise"), "obfuscate", fpe_enabled=True)
    by = {e.finding.entity_type: e for e in plan.entries}
    assert by["SSN"].method == "fpe_digits" and by["SSN"].params == {"keep_suffix": 4} and by["SSN"].reversible
    assert by["EMAIL"].method == "email" and by["GADGET_ID"].method == "det_token"
    assert by["GADGET_ID"].reason == "policy:default_sensitive" and by["SSN"].reason == "policy:SSN"
    assert plan.record_sensitivity is Sensitivity.CRITICAL
    nofpe = build_plan(FINDINGS, load_preset("gdpr-pseudonymise"), "obfuscate", fpe_enabled=False)
    ssn = next(e for e in nofpe.entries if e.finding.entity_type == "SSN")
    assert ssn.method == "det_token" and ssn.reason.endswith("fallback:fpe_disabled") and ssn.params == {}


def test_synthesize_plan_keeps_non_sensitive_unless_overridden() -> None:
    plan = build_plan(FINDINGS, load_preset("synthetic-realistic"), "synthesize", fpe_enabled=False)
    by = {e.finding.entity_type: e for e in plan.entries}
    assert by["SSN"].method == "surrogate" and by["EMAIL"].method == "surrogate"
    assert by["CITY"].method == "keep" and by["CITY"].reason == "policy:keep_non_sensitive"
    strict = build_plan([_f("CITY", "$.city")], load_preset("synthetic-strict"), "synthesize", fpe_enabled=False)
    assert strict.entries[0].method == "surrogate"


def test_explain_plan_is_value_free() -> None:
    plan = build_plan(FINDINGS, load_preset("pci-dss"), "obfuscate", fpe_enabled=True)
    rows = explain_plan(plan)
    assert rows[0].keys() == {"path", "span", "entity_type", "score", "method", "params", "reason", "reversible"}
    assert all("ssn" not in str(r["params"]).lower() for r in rows)
```

- [ ] **Step 2: Run to confirm failure**

Run: `pytest -q tests/test_planner.py`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write `dps/engine/planner.py`**

```python
"""The planner: resolve one method per finding from the policy, with reasons (spec §5.4–5.5).

Static fallbacks (FPE disabled) happen here so the plan shown to a reviewer is what will run;
value-dependent fallbacks (domain too small, unparseable date) happen in ``run_method`` and are
reported through the outcome notes.
"""
from __future__ import annotations

from typing import Any

from dps.engine.findings import Finding, Plan, PlanEntry
from dps.engine.policy import Policy
from dps.engine.taxonomy import Sensitivity, entity_info, record_sensitivity
from dps.engine.transform.methods import METHODS

_FPE_METHODS = {"fpe_digits", "fpe_text"}


def build_plan(findings: list[Finding], policy: Policy, mode: str, *, fpe_enabled: bool) -> Plan:
    """One :class:`PlanEntry` per finding, in finding order."""
    if mode not in ("obfuscate", "synthesize"):
        raise ValueError(f"mode must be obfuscate or synthesize, got {mode!r}")
    entries: list[PlanEntry] = []
    for finding in findings:
        spec = policy.method_for(finding.entity_type, mode)
        method, params = spec.method, dict(spec.params)
        if mode == "synthesize":
            explicit = finding.entity_type in policy.synthesize.overrides
            reason = f"policy:{finding.entity_type}" if explicit else "policy:synthesize_default"
            if (not explicit and policy.synthesize.keep_non_sensitive
                    and entity_info(finding.entity_type).sensitivity is Sensitivity.LOW):
                method, params, reason = "keep", {}, "policy:keep_non_sensitive"
        else:
            reason = f"policy:{finding.entity_type}" if finding.entity_type in policy.treat else "policy:default_sensitive"
        if method in _FPE_METHODS and not fpe_enabled:
            method, params, reason = "det_token", {}, f"{reason}|fallback:fpe_disabled"
        entries.append(PlanEntry(finding=finding, method=method, params=params, reason=reason,
                                 reversible=METHODS[method].reversible))
    return Plan(mode=mode, record_sensitivity=record_sensitivity(f.entity_type for f in findings), entries=entries)  # type: ignore[arg-type]


def explain_plan(plan: Plan) -> list[dict[str, Any]]:
    """Reviewer-facing rows: locator, type, score, method, params, reason. No values."""
    rows = []
    for e in plan.entries:
        loc = e.finding.locator
        rows.append({
            "path": loc.path, "span": None if loc.whole else [loc.start, loc.end],
            "entity_type": e.finding.entity_type, "score": e.finding.score, "method": e.method,
            "params": e.params, "reason": e.reason, "reversible": e.reversible,
        })
    return rows
```

- [ ] **Step 4: Run tests, lint, types; commit**

Run: `pytest -q tests/test_planner.py && ruff check dps tests && mypy dps` → `3 passed`.
```bash
git add dps/engine/planner.py tests/test_planner.py
git commit -m "feat(engine): planner resolves methods per finding with reasons and static fallbacks"
```

---

### Task 17: Apply a plan to JSON or text; reveal hints; reveal

**Files:**
- Create: `dps/engine/transform/apply.py`
- Test: `tests/test_apply.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_apply.py`:
```python
from __future__ import annotations

import copy

from dps.engine.detect.ensemble import Detector, DetectorConfig
from dps.engine.planner import build_plan
from dps.engine.policy import load_preset
from dps.engine.transform.apply import ApplyOptions, apply_plan, reveal
from dps.engine.transform.crypto import generate_key_material

SAMPLE = {
    "customer_id": "CUST-884120",
    "full_name": "Ananya Krishnan",
    "dob": "1988-03-14",
    "aadhaar": "2341 2341 2346",
    "email": "ananya.k@example-mail.in",
    "phone": "+91 98451 20377",
    "risk_score": 712,
    "ssn_int": 123456789,
    "notes": "Mail ann.k@example-mail.in about 1988-03-14 soon",
}


class RotCipher:
    def encrypt(self, alphabet: str, plaintext: str) -> str:
        return "".join(alphabet[(alphabet.index(c) + 3) % len(alphabet)] for c in plaintext)

    def decrypt(self, alphabet: str, ciphertext: str) -> str:
        return "".join(alphabet[(alphabet.index(c) - 3) % len(alphabet)] for c in ciphertext)


class FakeSynth:
    def surrogate(self, entity_type: str, original: str, locale: str, anchor: str) -> str:
        return {"PERSON_NAME": "Meera Venkatesan", "EMAIL": "meera@example.com"}.get(entity_type, f"<{entity_type}>")


def _plan(data, policy_name: str, mode: str):  # noqa: ANN001, ANN202
    policy = load_preset(policy_name)
    findings = Detector(DetectorConfig(locale="en_IN", min_score=policy.detect.min_score)).detect(data=data).findings
    return policy, build_plan(findings, policy, mode, fpe_enabled=True)


def _opts(policy, key=None, **kw):  # noqa: ANN001, ANN003, ANN202
    base = dict(policy=policy, key=key, surrogates=None, fpe_enabled=True, cipher_factory=lambda k, t: RotCipher(),
                token_style=policy.output.token_style, locale="en_IN")
    base.update(kw)
    return ApplyOptions(**base)  # type: ignore[arg-type]


def test_obfuscate_then_reveal_round_trips_natural_style() -> None:
    policy, plan = _plan(SAMPLE, "gdpr-pseudonymise", "obfuscate")
    key = generate_key_material("key")
    res = apply_plan(data=SAMPLE, plan=plan, options=_opts(policy, key))
    out = res.output
    assert out["risk_score"] == 712 and out["customer_id"] != "CUST-884120" and out["customer_id"].startswith("CUST-")
    assert out["email"].endswith("@example-mail.in") and not out["email"].startswith("ananya.k")
    assert isinstance(out["ssn_int"], int) and out["ssn_int"] != 123456789
    assert out["notes"].startswith("Mail ") and "ann.k@example-mail.in" not in out["notes"]
    assert res.methods_used["fpe_digits"] >= 2 and res.reveal_hints
    assert reveal(output=out, key=key, hints=res.reveal_hints, options=_opts(policy, key)) == SAMPLE
    assert SAMPLE["notes"].startswith("Mail ann.k")  # input untouched


def test_envelope_style_text_round_trips_without_hints() -> None:
    policy = load_preset("analytics-safe").model_copy(update={"treat": {"default_sensitive": {"method": "det_token"}}})
    policy = type(policy).model_validate({**policy.model_dump(mode="json"), "treat": {"default_sensitive": {"method": "det_token"},
                                                                                       "EMAIL": {"method": "det_token"}},
                                          "output": {"token_style": "envelope"}})
    text = "Reach ann.k@example-mail.in or 10.20.30.40 today"
    findings = Detector(DetectorConfig()).detect(text=text).findings
    plan = build_plan(findings, policy, "obfuscate", fpe_enabled=False)
    key = generate_key_material()
    res = apply_plan(text=text, plan=plan, options=_opts(policy, key, fpe_enabled=False, token_style="envelope"))
    assert res.output.startswith("Reach [[PII:EMAIL:") and "[[PII:IP_ADDRESS:" in res.output
    assert reveal(output=res.output, key=key, hints=[], options=_opts(policy, key, fpe_enabled=False)) == text


def test_synthesize_replaces_without_hints_and_keeps_shape() -> None:
    policy, plan = _plan(SAMPLE, "synthetic-realistic", "synthesize")
    res = apply_plan(data=SAMPLE, plan=plan, options=_opts(policy, None, surrogates=FakeSynth()))
    assert res.output["full_name"] == "Meera Venkatesan" and res.output["email"] == "meera@example.com"
    assert res.output["risk_score"] == 712 and res.reveal_hints == []
    assert "ann.k@example-mail.in" not in res.output["notes"]
    assert copy.deepcopy(SAMPLE) == SAMPLE
```

- [ ] **Step 2: Run to confirm failure**

Run: `pytest -q tests/test_apply.py`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write `dps/engine/transform/apply.py`**

```python
"""Apply a :class:`Plan` to a JSON document or a text, and reverse it with the key (spec §5.5).

* Whole-value entries replace the leaf (numbers stay numbers when the output is numeric).
* Span entries rewrite slices of a string left to right, tracking the shift so the emitted
  :class:`RevealHint` spans describe the *output*; in ``envelope`` style, ``det_token`` spans are
  wrapped as ``[[PII:TYPE:token]]`` so text can be revealed without hints.
* The record's **anchor** (a hash of its business key when one is found, else of the record) seeds
  date shifting and persona choice, so every field of one record moves together.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field

from dps.engine.findings import Plan, PlanEntry
from dps.engine.jsonpath import get_path, set_path
from dps.engine.policy import Policy
from dps.engine.transform.crypto import KeyMaterial
from dps.engine.transform.methods import (
    CipherFactory,
    SurrogateProvider,
    TransformContext,
    reverse_method,
    run_method,
)
from dps.engine.transform.tokens import ENVELOPE_RE, make_envelope, parse_envelopes

ANCHOR_ENTITIES = ("CUSTOMER_ID", "ACCOUNT_NUMBER", "EMPLOYEE_ID", "CASE_ID", "USERNAME", "EMAIL")
_INT = re.compile(r"-?\d+")
_FLOAT = re.compile(r"-?\d+\.\d+")


class RevealHint(BaseModel):
    path: str
    start: int | None = None
    end: int | None = None
    entity_type: str
    method: str
    params: dict[str, Any] = Field(default_factory=dict)
    reverse_params: dict[str, Any] = Field(default_factory=dict)
    original_type: str = "str"
    anchor: str = "record"


class ApplyResult(BaseModel):
    output: Any
    reveal_hints: list[RevealHint]
    methods_used: dict[str, int]
    notes: dict[str, int]
    anchor: str


@dataclass
class ApplyOptions:
    policy: Policy
    key: KeyMaterial | None
    surrogates: SurrogateProvider | None
    fpe_enabled: bool
    cipher_factory: CipherFactory | None = None
    token_style: str = "natural"
    locale: str = "en_US"


def _anchor_for(document: Any, plan: Plan) -> str:
    for entity in ANCHOR_ENTITIES:
        for entry in plan.entries:
            if entry.finding.entity_type == entity and entry.finding.locator.whole and entry.finding.value_hash:
                return f"{entity}:{entry.finding.value_hash}"
    canon = document if isinstance(document, str) else json.dumps(document, sort_keys=True, default=str)
    return "record:" + hashlib.sha256(canon.encode("utf-8")).hexdigest()[:16]


def _ctx(entry: PlanEntry, options: ApplyOptions, anchor: str, mode: str) -> TransformContext:
    return TransformContext(
        entity_type=entry.finding.entity_type, path=entry.finding.locator.path, mode=mode, params=entry.params,
        key=options.key, anchor=anchor, field_scoped=options.policy.consistency.field_scoped,
        locale=options.locale, fpe_enabled=options.fpe_enabled, synth=options.surrogates,
        cipher_factory=options.cipher_factory,
    )


def _typed(value: str | None, original_type: str) -> Any:
    if value is None:
        return None
    if original_type == "int" and _INT.fullmatch(value):
        return int(value)
    if original_type == "float" and (_FLOAT.fullmatch(value) or _INT.fullmatch(value)):
        return float(value)
    return value


def _type_name(value: Any) -> str:
    if isinstance(value, bool):
        return "str"
    return "int" if isinstance(value, int) else "float" if isinstance(value, float) else "str"


def apply_plan(*, plan: Plan, options: ApplyOptions, data: Any | None = None, text: str | None = None) -> ApplyResult:
    """Apply ``plan`` to a deep copy of ``data`` (or to ``text``) and return the output with hints."""
    if (data is None) == (text is None):
        raise ValueError("pass exactly one of data or text")
    document: Any = copy.deepcopy(data) if text is None else text
    anchor = _anchor_for(document, plan)
    hints: list[RevealHint] = []
    used: Counter[str] = Counter()
    notes: Counter[str] = Counter()
    by_path: dict[str, list[PlanEntry]] = {}
    for entry in plan.entries:
        by_path.setdefault(entry.finding.locator.path, []).append(entry)

    for path, entries in by_path.items():
        whole = [e for e in entries if e.finding.locator.whole]
        spans = sorted((e for e in entries if not e.finding.locator.whole), key=lambda e: e.finding.locator.start or 0)
        current = document if path == "$" else get_path(document, path)
        if whole:
            entry = whole[0]
            original_type = _type_name(current)
            outcome = run_method(entry.method, str(current), _ctx(entry, options, anchor, plan.mode))
            used[outcome.method] += 1
            if outcome.note:
                notes[outcome.note] += 1
            new_value = _typed(outcome.value, original_type)
            if outcome.reversible:
                hints.append(RevealHint(path=path, entity_type=entry.finding.entity_type, method=outcome.method,
                                        params=entry.params, reverse_params=outcome.reverse_params,
                                        original_type=original_type, anchor=anchor))
        elif spans and isinstance(current, str):
            pieces: list[str] = []
            cursor, delta = 0, 0
            for entry in spans:
                start, end = entry.finding.locator.start or 0, entry.finding.locator.end or 0
                if start < cursor:
                    continue  # overlapping span already consumed
                outcome = run_method(entry.method, current[start:end], _ctx(entry, options, anchor, plan.mode))
                used[outcome.method] += 1
                if outcome.note:
                    notes[outcome.note] += 1
                replacement = outcome.value if outcome.value is not None else ""
                if options.token_style == "envelope" and outcome.reversible and outcome.method == "det_token":
                    replacement = make_envelope(entry.finding.entity_type, replacement)
                pieces.append(current[cursor:start])
                out_start = start + delta
                pieces.append(replacement)
                if outcome.reversible:
                    hints.append(RevealHint(path=path, start=out_start, end=out_start + len(replacement),
                                            entity_type=entry.finding.entity_type, method=outcome.method,
                                            params=entry.params, reverse_params=outcome.reverse_params, anchor=anchor))
                delta += len(replacement) - (end - start)
                cursor = end
            pieces.append(current[cursor:])
            new_value = "".join(pieces)
        else:
            continue
        if path == "$":
            document = new_value
        else:
            set_path(document, path, new_value)
    return ApplyResult(output=document, reveal_hints=hints, methods_used=dict(used), notes=dict(notes), anchor=anchor)


def _reveal_ctx(hint: RevealHint, options: ApplyOptions) -> TransformContext:
    return TransformContext(
        entity_type=hint.entity_type, path=hint.path, mode="reveal", params=hint.params, key=options.key,
        anchor=hint.anchor, field_scoped=options.policy.consistency.field_scoped, locale=options.locale,
        fpe_enabled=options.fpe_enabled, cipher_factory=options.cipher_factory,
    )


def _reveal_envelopes(text: str, options: ApplyOptions) -> str:
    out, cursor = [], 0
    for start, end, entity, token in parse_envelopes(text):
        ctx = TransformContext(entity_type=entity, path="$", mode="reveal", key=options.key,
                               field_scoped=options.policy.consistency.field_scoped, locale=options.locale)
        out.append(text[cursor:start])
        out.append(reverse_method("det_token", token, ctx, {}))
        cursor = end
    out.append(text[cursor:])
    return "".join(out)


def reveal(*, output: Any, key: KeyMaterial, hints: list[RevealHint], options: ApplyOptions) -> Any:
    """Undo ``apply_plan``: hints first (whole values, then spans right to left), then any
    remaining envelopes anywhere in the document."""
    options.key = key
    document = copy.deepcopy(output)
    whole = [h for h in hints if h.start is None]
    spans = sorted((h for h in hints if h.start is not None), key=lambda h: (h.path, -(h.start or 0)))
    for hint in whole:
        token = document if hint.path == "$" else get_path(document, hint.path)
        plain = reverse_method(hint.method, str(token), _reveal_ctx(hint, options), hint.reverse_params)
        value = _typed(plain, hint.original_type)
        if hint.path == "$":
            document = value
        else:
            set_path(document, hint.path, value)
    for hint in spans:
        text = document if hint.path == "$" else get_path(document, hint.path)
        segment = text[hint.start:hint.end]
        match = ENVELOPE_RE.fullmatch(segment)
        token = match.group(2) if match else segment
        plain = reverse_method(hint.method, token, _reveal_ctx(hint, options), hint.reverse_params)
        new_text = text[:hint.start] + plain + text[hint.end:]
        if hint.path == "$":
            document = new_text
        else:
            set_path(document, hint.path, new_text)
    return _walk_envelopes(document, options)


def _walk_envelopes(node: Any, options: ApplyOptions) -> Any:
    if isinstance(node, str):
        return _reveal_envelopes(node, options) if "[[PII:" in node else node
    if isinstance(node, dict):
        return {k: _walk_envelopes(v, options) for k, v in node.items()}
    if isinstance(node, list):
        return [_walk_envelopes(v, options) for v in node]
    return node
```

- [ ] **Step 4: Run tests, lint, types**

Run: `pytest -q tests/test_apply.py && ruff check dps tests && mypy dps`
Expected: `3 passed`, clean. If the first test's `customer_id` assertion fails, confirm the detector classified `$.customer_id` as `CUSTOMER_ID` (Task 7 rule `customer id` = 0.85 ≥ floor 0.7) and that `fpe_digits` saw 6 digits (`884120`).

- [ ] **Step 5: Commit**

```bash
git add dps/engine/transform/apply.py tests/test_apply.py
git commit -m "feat(transform): apply plans to JSON and text with reveal hints; reveal by key"
```

---

### Task 18: Verifier (leakage sweep) and report

**Files:**
- Create: `dps/engine/verify.py`, `dps/engine/report.py`
- Test: `tests/test_verify_report.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_verify_report.py`:
```python
from __future__ import annotations

from dps.engine.findings import Finding, Locator, Plan, PlanEntry
from dps.engine.report import Report, build_report
from dps.engine.taxonomy import Sensitivity
from dps.engine.verify import Verification, collect_originals, verify_output


def _finding(path: str, entity: str, start: int | None = None, end: int | None = None) -> Finding:
    return Finding(locator=Locator(path=path, start=start, end=end), entity_type=entity, score=0.9,
                   detectors=["t"], sensitivity=Sensitivity.MEDIUM)


DATA = {"email": "ann.k@example-mail.in", "ssn": "123-45-6789", "notes": "mail ann.k@example-mail.in", "n": 42}
FINDINGS = [_finding("$.email", "EMAIL"), _finding("$.ssn", "SSN"), _finding("$.notes", "EMAIL", 5, 26)]


def test_collect_originals_reads_whole_values_and_spans() -> None:
    originals = collect_originals(FINDINGS, data=DATA)
    assert [o for _, o in originals] == ["ann.k@example-mail.in", "123-45-6789", "ann.k@example-mail.in"]


def test_verify_passes_clean_output_and_catches_leaks() -> None:
    originals = collect_originals(FINDINGS, data=DATA)
    clean = {"email": "x@example-mail.in", "ssn": "987-65-4321", "notes": "mail x@example-mail.in", "n": 42}
    ok = verify_output(originals, clean)
    assert ok.passed and ok.checked == 3 and ok.hits == []
    leaky = {**clean, "notes": "mail ann.k@example-mail.in"}
    bad = verify_output(originals, leaky)
    assert not bad.passed and bad.hits == [{"path": "$.notes", "entity_type": "EMAIL"}]
    # formatting changes do not hide a leak
    assert not verify_output(originals, {**clean, "ssn": "123 45 6789"}).passed
    # deliberately kept locators and short values are skipped
    assert verify_output(originals, leaky, kept={("$.notes", 5, 26)}).passed
    assert verify_output([(_finding("$.x", "AGE"), "42")], {"x": "42"}).passed


def test_report_summarises_without_values() -> None:
    plan = Plan(mode="obfuscate", record_sensitivity=Sensitivity.CRITICAL, entries=[
        PlanEntry(finding=FINDINGS[1], method="fpe_digits", params={}, reason="policy:SSN", reversible=True)])
    rep = build_report(mode="obfuscate", policy_name="gdpr-pseudonymise", policy_hash="abc", findings=FINDINGS,
                       plan=plan, methods_used={"fpe_digits": 1}, notes={}, reversible_values=1,
                       verification=Verification(passed=True, checked=3, hits=[]), llm_calls=0, duration_ms=3.2)
    assert isinstance(rep, Report) and rep.findings_total == 3 and rep.findings_by_type == {"EMAIL": 2, "SSN": 1}
    assert rep.llm == {"calls": 0, "raw_pii_sent": 0} and rep.verification.passed  # type: ignore[union-attr]
    assert "6789" not in rep.model_dump_json()
```

- [ ] **Step 2: Run to confirm failure**

Run: `pytest -q tests/test_verify_report.py`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write `dps/engine/verify.py`**

```python
"""The leakage sweep (spec §5.7): no original sensitive value may survive in the output.

Originals and output are compared after normalisation (lower case, alphanumerics only), so a
re-formatted SSN or a re-cased e-mail still counts as a leak. Values whose normalised form is
shorter than ``min_len`` are skipped (they would match by accident), as are locators the plan
deliberately kept.
"""
from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel, Field

from dps.engine.findings import Finding
from dps.engine.jsonpath import get_path

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


class Verification(BaseModel):
    passed: bool
    checked: int
    hits: list[dict[str, str]] = Field(default_factory=list)


def normalise(text: str) -> str:
    return _NON_ALNUM.sub("", text.lower())


def collect_originals(findings: list[Finding], *, data: Any | None = None, text: str | None = None) -> list[tuple[Finding, str]]:
    """The original string for each finding (whole value or span), read from the input."""
    out: list[tuple[Finding, str]] = []
    for f in findings:
        loc = f.locator
        source: Any = text if loc.path == "$" and text is not None else get_path(data, loc.path)
        value = str(source)
        out.append((f, value if loc.whole else value[loc.start:loc.end]))
    return out


def verify_output(
    originals: list[tuple[Finding, str]], output: Any, *, kept: set[tuple[str, int | None, int | None]] | None = None,
    min_len: int = 4,
) -> Verification:
    """Search the whole output (serialised, normalised) for every original."""
    haystack = normalise(output if isinstance(output, str) else json.dumps(output, ensure_ascii=False, default=str))
    kept = kept or set()
    hits: list[dict[str, str]] = []
    checked = 0
    for finding, original in originals:
        loc = finding.locator
        if (loc.path, loc.start, loc.end) in kept:
            continue
        needle = normalise(original)
        if len(needle) < min_len:
            continue
        checked += 1
        if needle in haystack:
            hits.append({"path": loc.path, "entity_type": finding.entity_type})
    return Verification(passed=not hits, checked=checked, hits=hits)
```

- [ ] **Step 4: Write `dps/engine/report.py`**

```python
"""The per-request report (spec §5.7): counts, methods, verification, configuration hash. No values."""
from __future__ import annotations

from collections import Counter
from typing import Any

from pydantic import BaseModel, Field

from dps import __version__
from dps.engine.findings import Finding, Plan
from dps.engine.verify import Verification


class Report(BaseModel):
    mode: str
    policy: str
    policy_hash: str
    engine_version: str = __version__
    findings_total: int
    findings_by_type: dict[str, int]
    findings_by_detector: dict[str, int]
    record_sensitivity: str
    methods_used: dict[str, int]
    fallbacks: dict[str, int] = Field(default_factory=dict)
    reversible_values: int
    verification: Verification | None = None
    llm: dict[str, Any] = Field(default_factory=lambda: {"calls": 0, "raw_pii_sent": 0})
    duration_ms: float


def build_report(*, mode: str, policy_name: str, policy_hash: str, findings: list[Finding], plan: Plan | None,
                 methods_used: dict[str, int], notes: dict[str, int], reversible_values: int,
                 verification: Verification | None, llm_calls: int, duration_ms: float) -> Report:
    by_type = Counter(f.entity_type for f in findings)
    by_detector = Counter(d.split(":")[0] for f in findings for d in f.detectors)
    return Report(
        mode=mode, policy=policy_name, policy_hash=policy_hash, findings_total=len(findings),
        findings_by_type=dict(by_type), findings_by_detector=dict(by_detector),
        record_sensitivity=str(plan.record_sensitivity) if plan else "LOW",
        methods_used=methods_used, fallbacks=notes, reversible_values=reversible_values,
        verification=verification, llm={"calls": llm_calls, "raw_pii_sent": 0}, duration_ms=round(duration_ms, 2),
    )
```

- [ ] **Step 5: Run tests, lint, types; commit**

Run: `pytest -q tests/test_verify_report.py && ruff check dps tests && mypy dps` → `3 passed`.
```bash
git add dps/engine/verify.py dps/engine/report.py tests/test_verify_report.py
git commit -m "feat(engine): leakage verifier and value-free report"
```

---

### Task 19: Surrogates and personas (synthesis engine)

**Files:**
- Create: `dps/engine/synth/__init__.py`, `dps/engine/synth/personas.py`, `dps/engine/synth/surrogates.py`
- Test: `tests/test_surrogates.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_surrogates.py`:
```python
from __future__ import annotations

import phonenumbers
import pytest

from dps.engine.detect.checksums import luhn_valid, valid_aba, validate
from dps.engine.synth.personas import PersonaFactory
from dps.engine.synth.surrogates import SurrogateEngine, generate_synthesis_seed, seed_bytes, seed_from_caller

SEED = seed_bytes(generate_synthesis_seed())


def test_seed_round_trip_and_validation() -> None:
    s = generate_synthesis_seed()
    assert seed_from_caller(s.model_dump(mode="json")).seed == s.seed and len(seed_bytes(s)) == 32
    with pytest.raises(ValueError):
        seed_from_caller({"alg": "dps.synth.v1", "seed": "c2hvcnQ", "created_at": "2026-01-01T00:00:00Z"})


def test_persona_is_coherent_and_deterministic() -> None:
    f = PersonaFactory(SEED)
    a = f.for_anchor("CUSTOMER_ID:abc", "en_IN")
    assert a == f.for_anchor("CUSTOMER_ID:abc", "en_IN") and a != f.for_anchor("CUSTOMER_ID:xyz", "en_IN")
    assert a.first_name.lower() in a.email_local and a.last_name.lower() in a.email_local
    assert a.country_code == "IN" and a.gender in {"female", "male"}


def test_surrogates_are_rule_valid_and_consistent() -> None:
    eng = SurrogateEngine(SEED, default_locale="en_IN")
    anchor = "CUSTOMER_ID:abc"
    card = eng.surrogate("CARD_PAN", "4532 7811 2290 4417", "en_IN", anchor)
    assert card.startswith("4111 11") and card.count(" ") == 3 and luhn_valid(card) and card != "4532 7811 2290 4417"
    aad = eng.surrogate("AADHAAR", "2341 2341 2346", "en_IN", anchor)
    assert validate("AADHAAR", aad) and aad.count(" ") == 2 and aad != "2341 2341 2346"
    ssn = eng.surrogate("SSN", "123-45-6789", "en_US", anchor)
    assert ssn[0] == "9" and ssn.count("-") == 2 and validate("SSN", ssn) is False  # never-issued range
    assert valid_aba(eng.surrogate("ROUTING_ABA", "021000021", "en_US", anchor))
    email = eng.surrogate("EMAIL", "ananya.k@example-mail.in", "en_IN", anchor)
    assert email.split("@")[1] in {"example.com", "example.net", "example.org"}
    phone = eng.surrogate("PHONE", "+91 98451 20377", "en_IN", anchor)
    assert phone.startswith("+91") and phonenumbers.is_valid_number(phonenumbers.parse(phone, None))
    dob = eng.surrogate("DATE_OF_BIRTH", "1988-03-14", "en_IN", anchor)
    assert dob != "1988-03-14" and len(dob) == 10 and 1985 <= int(dob[:4]) <= 1989  # same 5-year band
    name = eng.surrogate("PERSON_NAME", "Ananya Krishnan", "en_IN", anchor)
    assert " " in name and name != "Ananya Krishnan"
    assert name == eng.surrogate("PERSON_NAME", "Ananya Krishnan", "en_IN", anchor)         # same persona
    assert eng.surrogate("PERSON_NAME", "Ananya", "en_IN", anchor) == name.split(" ")[0]  # single token → first name
    assert eng.surrogate("API_KEY", "AKIAIOSFODNN7EXAMPLE", "en_US", anchor) == "[REDACTED]"
    cust = eng.surrogate("CUSTOMER_ID", "CUST-884120", "en_IN", anchor)
    assert cust.startswith("CUST-") and cust != "CUST-884120" and cust[5:].isdigit()


def test_value_consistency_and_never_identity() -> None:
    eng = SurrogateEngine(SEED)
    a = eng.surrogate("EMAIL", "x@y.co", "en_US", "r1")
    assert a == eng.surrogate("EMAIL", "x@y.co", "en_US", "r1") != eng.surrogate("EMAIL", "z@y.co", "en_US", "r1")
    for original in ("12", "7", "ab"):
        assert eng.surrogate("ACCOUNT_NUMBER", original, "en_US", "r1") != original
    assert eng.surrogate("CITY", "Bengaluru", "xx_ZZ", "r1")  # unknown locale falls back, no crash
```

- [ ] **Step 2: Run to confirm failure**

Run: `pytest -q tests/test_surrogates.py`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write `dps/engine/synth/__init__.py` and `dps/engine/synth/personas.py`**

`dps/engine/synth/__init__.py`:
```python
"""Synthesis: seeded personas and rule-valid surrogates; LLM text rewriting over placeholders."""
```

`dps/engine/synth/personas.py`:
```python
"""One coherent persona per record anchor (spec §5.6): name, gender, e-mail, DOB, address agree."""
from __future__ import annotations

import hashlib
import hmac
import re
from dataclasses import dataclass
from datetime import date

from faker import Faker

from dps.engine.detect.patterns import region_for_locale

SUPPORTED_LOCALES = ("en_US", "en_IN", "en_GB", "en_CA", "es_MX")
_NOT_EMAIL = re.compile(r"[^a-z0-9.]")


def seed_int(seed: bytes, label: str) -> int:
    """A stable 64-bit integer from ``HMAC(seed, label)``."""
    return int.from_bytes(hmac.new(seed, label.encode("utf-8"), hashlib.sha256).digest()[:8], "big")


def normalise_locale(locale: str | None) -> str:
    if not locale:
        return "en_US"
    candidate = locale.replace("-", "_")
    if candidate in SUPPORTED_LOCALES:
        return candidate
    for supported in SUPPORTED_LOCALES:
        if supported.endswith("_" + candidate.split("_")[-1].upper()):
            return supported
    return "en_US"


@dataclass(frozen=True)
class Persona:
    locale: str
    country_code: str
    gender: str
    first_name: str
    last_name: str
    email_local: str
    dob: date
    street: str
    city: str
    postal_code: str

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}"


class PersonaFactory:
    """Deterministic personas: same seed + anchor + locale → same persona."""

    def __init__(self, seed: bytes) -> None:
        self._seed = seed

    def for_anchor(self, anchor: str, locale: str, *, original_dob: date | None = None,
                   gender_hint: str | None = None) -> Persona:
        loc = normalise_locale(locale)
        fake = Faker(loc)
        fake.seed_instance(seed_int(self._seed, f"persona|{anchor}|{loc}"))
        gender = gender_hint or ("female" if fake.random.random() < 0.5 else "male")
        first = fake.first_name_female() if gender == "female" else fake.first_name_male()
        last = fake.last_name()
        if original_dob is not None:
            band = original_dob.year - original_dob.year % 5
            year = band + fake.random_int(0, 4)
            dob = date(year, fake.random_int(1, 12), fake.random_int(1, 28))
            if dob == original_dob:
                dob = date(year, dob.month, dob.day % 28 + 1)
        else:
            dob = fake.date_of_birth(minimum_age=18, maximum_age=80)
        email_local = _NOT_EMAIL.sub("", f"{first}.{last}".lower()) or "user"
        return Persona(
            locale=loc, country_code=region_for_locale(loc), gender=gender, first_name=first, last_name=last,
            email_local=email_local, dob=dob, street=fake.street_address().replace("\n", ", "),
            city=fake.city(), postal_code=fake.postcode(),
        )
```

- [ ] **Step 4: Write `dps/engine/synth/surrogates.py`**

```python
"""Rule-valid, locale-aware, seeded surrogates (spec §5.6).

``SurrogateEngine.surrogate(entity_type, original, locale, anchor)`` is deterministic in
(seed, entity type, original value) for value-level consistency, and persona fields come from
(seed, anchor) so one record stays coherent. Generated identifiers are *valid in format* and
*never issued*: SSNs in the 900 area, cards on a test IIN, e-mails on RFC 2606 domains, private
IP ranges, locally administered MACs. Credentials are never synthesised — they are redacted.
"""
from __future__ import annotations

import random
import re
import secrets
from datetime import UTC, date, datetime, timedelta
from typing import Any

import phonenumbers
from faker import Faker
from pydantic import BaseModel, Field

from dps.engine.detect.checksums import luhn_check_digit, verhoeff_check_digit
from dps.engine.synth.personas import Persona, PersonaFactory, normalise_locale, seed_int
from dps.engine.transform.crypto import b64u, unb64u

SEED_ALG = "dps.synth.v1"
RFC2606_DOMAINS = ("example.com", "example.net", "example.org")
TEST_CARD_IIN = "411111"
_DIGIT = re.compile(r"\d")
_NATIONALITY = {"US": "American", "IN": "Indian", "MX": "Mexican", "CA": "Canadian", "GB": "British"}
_DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d %b %Y", "%d %B %Y")


class SynthesisSeed(BaseModel):
    alg: str = SEED_ALG
    seed: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


def generate_synthesis_seed() -> SynthesisSeed:
    return SynthesisSeed(seed=b64u(secrets.token_bytes(32)))


def seed_bytes(seed: SynthesisSeed) -> bytes:
    raw = unb64u(seed.seed)
    if len(raw) != 32:
        raise ValueError("synthesis seed must be 32 bytes")
    return raw


def seed_from_caller(payload: dict[str, Any]) -> SynthesisSeed:
    seed = SynthesisSeed.model_validate(payload)
    if seed.alg != SEED_ALG:
        raise ValueError(f"unsupported synthesis seed alg {seed.alg!r}")
    seed_bytes(seed)
    return seed


def _digits(rng: random.Random, n: int) -> str:
    return "".join(str(rng.randint(0, 9)) for _ in range(n))


def _reshape_digits(original: str, digits: str) -> str:
    """Replace the digit positions of ``original`` in order with ``digits`` (same separators)."""
    it = iter(digits)
    return "".join(next(it, ch) if ch.isdigit() else ch for ch in original)


def _parse_date(text: str) -> tuple[date, str] | None:
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text.strip(), fmt).date(), fmt
        except ValueError:
            continue
    return None


class SurrogateEngine:
    """Implements ``SurrogateProvider`` for the ``surrogate`` method."""

    def __init__(self, seed: bytes, default_locale: str = "en_US") -> None:
        self._seed = seed
        self._default_locale = normalise_locale(default_locale)
        self._personas = PersonaFactory(seed)
        self._cache: dict[tuple[str, str], Persona] = {}

    def persona(self, anchor: str, locale: str, *, original_dob: date | None = None) -> Persona:
        key = (anchor, normalise_locale(locale))
        if key not in self._cache:
            self._cache[key] = self._personas.for_anchor(anchor, key[1], original_dob=original_dob)
        return self._cache[key]

    def _rng(self, entity_type: str, original: str) -> random.Random:
        return random.Random(seed_int(self._seed, f"{entity_type}|{original.strip().lower()}"))

    def surrogate(self, entity_type: str, original: str, locale: str, anchor: str) -> str:
        loc = normalise_locale(locale or self._default_locale)
        value = self._generate(entity_type, original, loc, anchor, self._rng(entity_type, original))
        if value == original:  # a surrogate must never equal its original
            value = self._generate(entity_type, original, loc, anchor, self._rng(entity_type, original + "|retry"))
            if value == original:
                value = value + "1" if not value[-1:].isdigit() else value[:-1] + str((int(value[-1]) + 1) % 10)
        return value

    # -- generators -----------------------------------------------------------------------------
    def _generate(self, entity: str, original: str, loc: str, anchor: str, rng: random.Random) -> str:
        if entity in ("API_KEY", "PASSWORD", "JWT", "PRIVATE_KEY"):
            return "[REDACTED]"
        if entity == "PERSON_NAME":
            p = self.persona(anchor, loc)
            name = p.first_name if " " not in original.strip() else p.full_name
            return name.upper() if original.isupper() else name
        if entity == "DATE_OF_BIRTH":
            parsed = _parse_date(original)
            p = self.persona(anchor, loc, original_dob=parsed[0] if parsed else None)
            return p.dob.strftime(parsed[1]) if parsed else p.dob.isoformat()
        if entity == "DATE":
            parsed = _parse_date(original)
            if parsed is None:
                return original
            shifted = parsed[0] + timedelta(days=rng.choice([d for d in range(-30, 31) if d != 0]))
            return shifted.strftime(parsed[1])
        if entity == "EMAIL":
            return f"{self.persona(anchor, loc).email_local}@{rng.choice(RFC2606_DOMAINS)}"
        if entity == "PHONE":
            return self._phone(original, self.persona(anchor, loc).country_code, rng)
        if entity == "SSN":
            return f"9{rng.randint(0, 99):02d}-{rng.randint(1, 99):02d}-{rng.randint(1, 9999):04d}"
        if entity == "ITIN":
            return f"9{rng.randint(0, 99):02d}-{rng.randint(70, 88):02d}-{rng.randint(1, 9999):04d}"
        if entity == "AADHAAR":
            body = str(rng.randint(2, 9)) + _digits(rng, 10)
            return _reshape_digits(original, body + verhoeff_check_digit(body)) if _DIGIT.search(original) else body
        if entity == "PAN_IN":
            letters = "".join(rng.choice("ABCDEFGHJKLMNPQRSTUVWXYZ") for _ in range(3))
            return f"{letters}P{self.persona(anchor, loc).last_name[:1].upper() or 'K'}{_digits(rng, 4)}{rng.choice('ABCDEFGHJKLMNPQRSTUVWXYZ')}"
        if entity == "CARD_PAN":
            count = max(13, min(19, len(_DIGIT.findall(original)) or 16))
            body = TEST_CARD_IIN + _digits(rng, count - len(TEST_CARD_IIN) - 1)
            return _reshape_digits(original, body + luhn_check_digit(body))
        if entity == "CARD_EXPIRY":
            return f"{rng.randint(1, 12):02d}/{(datetime.now(UTC).year + rng.randint(1, 4)) % 100:02d}"
        if entity == "CVV":
            return _digits(rng, 3)
        if entity == "IBAN":
            fake = Faker("en_GB" if loc == "en_IN" else loc)
            fake.seed_instance(rng.getrandbits(64))
            return str(fake.iban())
        if entity == "ROUTING_ABA":
            while True:
                candidate = _digits(rng, 9)
                n = [int(c) for c in candidate]
                if (3 * (n[0] + n[3] + n[6]) + 7 * (n[1] + n[4] + n[7]) + (n[2] + n[5] + n[8])) % 10 == 0:
                    return candidate
        if entity == "SIN":
            body = _digits(rng, 8)
            return _reshape_digits(original, body + luhn_check_digit(body))
        if entity in ("STREET_ADDRESS",):
            p = self.persona(anchor, loc)
            return f"{p.street}, {p.city} {p.postal_code}" if "," in original or len(original) > 40 else p.street
        if entity in ("CITY", "LOCATION"):
            return self.persona(anchor, loc).city
        if entity == "POSTAL_CODE":
            return self.persona(anchor, loc).postal_code
        if entity == "IP_ADDRESS":
            return f"10.{rng.randint(0, 255)}.{rng.randint(0, 255)}.{rng.randint(1, 254)}"
        if entity == "MAC_ADDRESS":
            return "02:" + ":".join(f"{rng.randint(0, 255):02x}" for _ in range(5))
        if entity == "URL":
            return f"https://example.com/{rng.getrandbits(32):08x}"
        if entity == "USERNAME":
            return self.persona(anchor, loc).email_local.replace(".", "_") + _digits(rng, 2)
        if entity in ("DEVICE_ID", "COOKIE_ID"):
            return "".join(rng.choice("0123456789abcdef") for _ in range(max(8, len(original))))
        if entity == "GENDER":
            return self.persona(anchor, loc).gender.title()
        if entity == "NATIONALITY":
            return _NATIONALITY.get(self.persona(anchor, loc).country_code, "Unknown")
        if entity == "AGE":
            p = self.persona(anchor, loc)
            return str((date.today() - p.dob).days // 365)
        if entity == "ORG_NAME":
            fake = Faker(loc)
            fake.seed_instance(rng.getrandbits(64))
            return str(fake.company())
        if entity in ("DIAGNOSIS_CODE", "MEDICATION", "AMOUNT", "SWIFT_BIC", "IFSC", "SORT_CODE"):
            return original if entity in ("DIAGNOSIS_CODE", "MEDICATION") else _reshape_digits(original, _digits(rng, 12))
        # identifiers of any other kind: keep letters and separators, replace the digits
        count = len(_DIGIT.findall(original))
        if count == 0:
            return "".join(rng.choice("ABCDEFGHJKLMNPQRSTUVWXYZ") if ch.isalpha() else ch for ch in original)
        return _reshape_digits(original, _digits(rng, count))

    @staticmethod
    def _phone(original: str, region: str, rng: random.Random) -> str:
        example = phonenumbers.example_number_for_type(region, phonenumbers.PhoneNumberType.MOBILE)
        if example is None:
            example = phonenumbers.example_number(region)
        base = str(example.national_number) if example else "2015550100"
        for _ in range(50):
            candidate = base[:-4] + f"{rng.randint(0, 9999):04d}"
            try:
                number = phonenumbers.parse(f"+{example.country_code if example else 1}{candidate}", None)
            except phonenumbers.NumberParseException:
                continue
            if phonenumbers.is_valid_number(number):
                fmt = (phonenumbers.PhoneNumberFormat.INTERNATIONAL if original.strip().startswith("+")
                       else phonenumbers.PhoneNumberFormat.NATIONAL)
                return phonenumbers.format_number(number, fmt)
        return f"+1 201-555-{rng.randint(100, 199):04d}"
```

- [ ] **Step 5: Run tests, lint, types**

Run: `pytest -q tests/test_surrogates.py && ruff check dps tests && mypy dps`
Expected: `4 passed`, clean.

- [ ] **Step 6: Commit**

```bash
git add dps/engine/synth tests/test_surrogates.py
git commit -m "feat(synth): seeded personas and rule-valid locale-aware surrogates"
```

---

### Task 20: LLM gateway client (Stellar / Vertex / stub)

**Files:**
- Create: `dps/llm/__init__.py`, `dps/llm/gateway.py`
- Test: `tests/test_gateway.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_gateway.py`:
```python
from __future__ import annotations

import base64
import json

import httpx
import pytest

from dps.config import Settings
from dps.llm.gateway import LlmCallError, LlmGateway, LlmUnavailable

B64 = lambda s: base64.b64encode(s.encode()).decode()  # noqa: E731


def _stellar_settings() -> Settings:
    return Settings(_env_file=None, llm_provider="stellar", stellar_base_url="https://stellar.test/v1",
                    coin_url="https://coin.test/token", coin_client_id=B64("cid"), coin_client_secret=B64("sec"),
                    coin_scope=B64("abc"), coin_token_ttl_seconds=100, llm_model="gemini-2.5-flash")


async def test_stub_provider_echoes_and_counts() -> None:
    gw = LlmGateway(Settings(_env_file=None, llm_provider="stub"))
    assert gw.available()
    result = await gw.complete([{"role": "user", "content": "Rewrite {PERSON_1} please"}], purpose="rewrite")
    assert "{PERSON_1}" in result.text and result.provider == "stub"


async def test_unconfigured_provider_is_unavailable() -> None:
    gw = LlmGateway(Settings(_env_file=None, llm_provider=""))
    assert not gw.available()
    with pytest.raises(LlmUnavailable):
        await gw.complete([{"role": "user", "content": "x"}])


async def test_stellar_mints_a_token_and_calls_chat_completions() -> None:
    calls: list[httpx.Request] = []
    mints = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal mints
        calls.append(request)
        if request.url.host == "coin.test":
            mints += 1
            form = dict(p.split("=") for p in request.content.decode().split("&"))
            assert form["client_id"] == "cid" and form["scope"] == "coinscopeabc"
            return httpx.Response(200, json={"access_token": f"tok{mints}"})
        assert request.url.path.endswith("/chat/completions")
        assert request.headers["authorization"] == f"Bearer tok{mints}"
        body = json.loads(request.content)
        assert body["model"] == "gemini-2.5-flash" and body["messages"][0]["content"] == "hello {X_1}"
        return httpx.Response(200, json={"choices": [{"message": {"content": "hi {X_1}"}}],
                                         "usage": {"prompt_tokens": 3, "completion_tokens": 2}})

    now = [1000.0]
    gw = LlmGateway(_stellar_settings(), transport=httpx.MockTransport(handler), clock=lambda: now[0])
    r1 = await gw.complete([{"role": "user", "content": "hello {X_1}"}])
    r2 = await gw.complete([{"role": "user", "content": "hello {X_1}"}])
    assert r1.text == "hi {X_1}" and r1.usage == {"prompt": 3, "completion": 2} and mints == 1 and r2.text
    now[0] += 101  # TTL elapsed on the monotonic clock → re-mint
    await gw.complete([{"role": "user", "content": "hello {X_1}"}])
    assert mints == 2


async def test_stellar_errors_carry_no_content() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"access_token": "t"}) if request.url.host == "coin.test" else httpx.Response(503)

    gw = LlmGateway(_stellar_settings(), transport=httpx.MockTransport(handler))
    with pytest.raises(LlmCallError) as err:
        await gw.complete([{"role": "user", "content": "SECRET-CONTENT"}])
    assert "SECRET-CONTENT" not in str(err.value)
```

- [ ] **Step 2: Run to confirm failure**

Run: `pytest -q tests/test_gateway.py`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write `dps/llm/__init__.py` and `dps/llm/gateway.py`**

`dps/llm/__init__.py`:
```python
"""LLM gateway client. Only placeholder text ever travels through here (spec P1)."""
```

`dps/llm/gateway.py`:
```python
"""Async LLM gateway in the org's pattern (DPC ``ArrangeLlmClient``, retrieval ``get_llm.py``).

Providers:
* ``stellar`` — the OpenAI-compatible gateway behind COIN OAuth2 client-credentials. Client id,
  secret and scope arrive base64-encoded and are decoded at use; the effective scope is
  ``"coinscope" + scope``; the token is re-minted after the TTL on a **monotonic** clock. The
  token mint deliberately skips TLS verification (VDI intercepting proxy, per the org handoff);
  API calls verify against ``DPS_SSL_CERT_FILE`` when set.
* ``vertex`` — Gemini via ``google-genai`` (lazy import), the laptop path.
* ``stub`` — deterministic echo for tests and offline demos.

No payload or response content is ever logged; errors name the provider and the exception type.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import time
from collections.abc import Callable
from typing import Any

import httpx
from pydantic import BaseModel, Field

from dps.config import Settings
from dps.observability import LLM_CALLS, get_logger

logger = get_logger(__name__)


class LlmUnavailable(RuntimeError):
    """No provider is configured, or its prerequisites are missing."""


class LlmCallError(RuntimeError):
    """A call failed; the message carries provider, host and error type only."""


class LlmResult(BaseModel):
    text: str
    provider: str
    model: str
    usage: dict[str, int] = Field(default_factory=dict)


def _b64(value: str) -> str:
    try:
        return base64.b64decode(value.strip(), validate=True).decode("utf-8")
    except (binascii.Error, ValueError, UnicodeDecodeError) as exc:
        raise LlmCallError(f"COIN credential is not valid base64 ({type(exc).__name__})") from exc


class LlmGateway:
    def __init__(self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._s = settings
        self._transport = transport
        self._clock = clock
        self._token: str | None = None
        self._minted = 0.0
        self._lock = asyncio.Lock()

    @property
    def provider(self) -> str:
        return self._s.llm_provider

    @property
    def model(self) -> str:
        return self._s.llm_model

    def available(self) -> bool:
        if self.provider == "stub":
            return True
        if self.provider == "stellar":
            return bool(self._s.stellar_base_url and self._s.coin_url)
        if self.provider == "vertex":
            try:
                import google.genai  # noqa: F401 - availability probe only
            except ImportError:
                return False
            return bool(self._s.vertex_project)
        return False

    async def complete(self, messages: list[dict[str, str]], *, temperature: float = 0.2,
                       max_tokens: int = 512, purpose: str = "rewrite") -> LlmResult:
        """One chat completion. ``messages`` must already be free of raw sensitive values."""
        if not self.available():
            LLM_CALLS.labels(self.provider or "none", purpose, "unavailable").inc()
            raise LlmUnavailable(f"llm provider {self.provider or 'none'!r} is not configured")
        try:
            if self.provider == "stub":
                result = self._stub(messages)
            elif self.provider == "stellar":
                result = await self._stellar(messages, temperature, max_tokens)
            else:
                result = await asyncio.to_thread(self._vertex, messages, temperature, max_tokens)
        except LlmCallError:
            LLM_CALLS.labels(self.provider, purpose, "error").inc()
            raise
        LLM_CALLS.labels(self.provider, purpose, "ok").inc()
        return result

    # -- stub ---------------------------------------------------------------------------------
    def _stub(self, messages: list[dict[str, str]]) -> LlmResult:
        last = next((m["content"] for m in reversed(messages) if m.get("role") == "user"), "")
        return LlmResult(text=last, provider="stub", model="stub", usage={"prompt": 0, "completion": 0})

    # -- stellar ------------------------------------------------------------------------------
    async def _coin_token(self) -> str:
        async with self._lock:
            if self._token and self._clock() - self._minted < self._s.coin_token_ttl_seconds:
                return self._token
            data = {
                "grant_type": "client_credentials",
                "client_id": _b64(self._s.coin_client_id),
                "client_secret": _b64(self._s.coin_client_secret),
                "scope": "coinscope" + _b64(self._s.coin_scope),
            }
            try:
                # verify=False is intentional for the mint only (org handoff: VDI TLS interception).
                async with httpx.AsyncClient(timeout=20.0, verify=False, transport=self._transport) as client:
                    response = await client.post(self._s.coin_url, data=data)
                    response.raise_for_status()
                    token = response.json().get("access_token")
            except httpx.HTTPError as exc:
                raise LlmCallError(f"coin mint failed: {type(exc).__name__}") from exc
            if not isinstance(token, str) or not token:
                raise LlmCallError("coin mint returned no access_token")
            self._token, self._minted = token, self._clock()
            return token

    async def _stellar(self, messages: list[dict[str, str]], temperature: float, max_tokens: int) -> LlmResult:
        token = await self._coin_token()
        verify: Any = self._s.ssl_cert_file or True
        url = self._s.stellar_base_url.rstrip("/") + "/chat/completions"
        try:
            async with httpx.AsyncClient(timeout=self._s.llm_timeout_seconds, verify=verify,
                                         transport=self._transport) as client:
                response = await client.post(url, headers={"Authorization": f"Bearer {token}"}, json={
                    "model": self._s.llm_model, "temperature": temperature, "max_tokens": max_tokens,
                    "messages": messages,
                })
                response.raise_for_status()
                body = response.json()
        except httpx.HTTPError as exc:
            raise LlmCallError(f"stellar call to {httpx.URL(url).host!r} failed: {type(exc).__name__}") from exc
        try:
            text = str(body["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError) as exc:
            raise LlmCallError("stellar response missing choices content") from exc
        usage = body.get("usage") or {}
        return LlmResult(text=text, provider="stellar", model=self._s.llm_model,
                         usage={"prompt": int(usage.get("prompt_tokens", 0)), "completion": int(usage.get("completion_tokens", 0))})

    # -- vertex -------------------------------------------------------------------------------
    def _vertex(self, messages: list[dict[str, str]], temperature: float, max_tokens: int) -> LlmResult:
        from google import genai
        from google.genai import types

        system = "\n".join(m["content"] for m in messages if m.get("role") == "system") or None
        contents = [m["content"] for m in messages if m.get("role") != "system"]
        try:
            client = genai.Client(vertexai=True, project=self._s.vertex_project, location=self._s.vertex_location,
                                  http_options=types.HttpOptions(timeout=int(self._s.llm_timeout_seconds * 1000)))
            response = client.models.generate_content(
                model=self._s.llm_model, contents=contents,
                config=types.GenerateContentConfig(temperature=temperature, max_output_tokens=max_tokens,
                                                   system_instruction=system,
                                                   thinking_config=types.ThinkingConfig(thinking_budget=0)),
            )
        except Exception as exc:  # noqa: BLE001 - name only, never content
            raise LlmCallError(f"vertex call failed: {type(exc).__name__}") from exc
        return LlmResult(text=str(response.text or ""), provider="vertex", model=self._s.llm_model)


def get_gateway(settings: Settings) -> LlmGateway:
    return LlmGateway(settings)
```

- [ ] **Step 4: Run tests, lint, types; commit**

Run: `pytest -q tests/test_gateway.py && ruff check dps tests && mypy dps` → `4 passed`.
```bash
git add dps/llm tests/test_gateway.py
git commit -m "feat(llm): async gateway client — Stellar via COIN OAuth2, Vertex, stub; no content in logs"
```

---

### Task 21: LLM text synthesis over placeholders

**Files:**
- Create: `dps/engine/synth/llm_text.py`
- Test: `tests/test_llm_text.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_llm_text.py`:
```python
from __future__ import annotations

import pytest

from dps.config import Settings
from dps.engine.detect.ensemble import Detector, DetectorConfig
from dps.engine.synth.llm_text import LeakToModelError, assert_placeholder_safe, synthesize_text, to_placeholders
from dps.llm.gateway import LlmGateway, LlmResult
from dps.observability import LLM_RAW_PII

TEXT = "Ann Smith (ann.k@example-mail.in) called; reply to ann.k@example-mail.in by 2026-01-05."


class FakeSynth:
    def surrogate(self, entity_type: str, original: str, locale: str, anchor: str) -> str:
        return {"EMAIL": "meera@example.com", "DATE": "2026-02-01", "PERSON_NAME": "Meera V"}[entity_type]


def _findings():  # noqa: ANN202
    return Detector(DetectorConfig()).detect(text=TEXT).findings


def test_placeholders_are_typed_and_consistent() -> None:
    ph = to_placeholders(TEXT, _findings())
    assert ph.text.count("{EMAIL_1}") == 2 and "{DATE_1}" in ph.text
    assert "example-mail" not in ph.text and len(ph.mapping) == 2
    assert_placeholder_safe(ph.text, [o for _, o in ph.mapping.values()])
    with pytest.raises(LeakToModelError):
        assert_placeholder_safe(ph.text + " ann.k@example-mail.in", ["ann.k@example-mail.in"])


async def test_deterministic_fill_without_llm() -> None:
    out, info = await synthesize_text(TEXT, _findings(), surrogates=FakeSynth(), locale="en_US", anchor="r1",
                                      gateway=None, llm_rewrite=False)
    assert "meera@example.com" in out and "example-mail" not in out and info["llm_calls"] == 0


class RewritingGateway(LlmGateway):
    def __init__(self, text: str) -> None:
        super().__init__(Settings(_env_file=None, llm_provider="stub"))
        self._text = text

    async def complete(self, messages, **kw):  # noqa: ANN001, ANN003, ANN201
        return LlmResult(text=self._text, provider="stub", model="stub")


async def test_llm_rewrite_keeps_placeholders_or_falls_back() -> None:
    before = LLM_RAW_PII._value.get()  # noqa: SLF001
    good = RewritingGateway("{EMAIL_1} wrote and {EMAIL_1} asked for a reply before {DATE_1}.")
    out, info = await synthesize_text(TEXT, _findings(), surrogates=FakeSynth(), locale="en_US", anchor="r1",
                                      gateway=good, llm_rewrite=True)
    assert out.startswith("meera@example.com wrote") and info["llm_calls"] == 1 and info["llm_used"]
    bad = RewritingGateway("I dropped every placeholder.")
    out2, info2 = await synthesize_text(TEXT, _findings(), surrogates=FakeSynth(), locale="en_US", anchor="r1",
                                        gateway=bad, llm_rewrite=True)
    assert "meera@example.com" in out2 and info2["llm_used"] is False and info2["fallback"] == "placeholders_missing"
    assert LLM_RAW_PII._value.get() == before == 0.0  # noqa: SLF001
```

- [ ] **Step 2: Run to confirm failure**

Run: `pytest -q tests/test_llm_text.py`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write `dps/engine/synth/llm_text.py`**

```python
"""Free-text synthesis (spec §5.6): findings → typed placeholders → optional LLM rewrite → fill → verify.

The model only ever sees ``{PERSON_NAME_1}``-style placeholders; ``assert_placeholder_safe`` proves
it before any call, and the rewrite is accepted only if every placeholder survives. DPS chooses the
surrogates itself, so consistency with the rest of the record holds whether or not the model ran.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from dps.engine.findings import Finding
from dps.engine.transform.methods import SurrogateProvider
from dps.engine.verify import normalise
from dps.llm.gateway import LlmCallError, LlmGateway, LlmUnavailable

PLACEHOLDER_RE = re.compile(r"\{([A-Z][A-Z0-9_]*?)_(\d+)\}")
REWRITE_SYSTEM = (
    "You rewrite short business texts for test data. The text contains placeholders like "
    "{PERSON_NAME_1} or {DATE_2}. Produce a different but equally plausible text with the same "
    "structure, tone and approximate length. Keep EVERY placeholder exactly as written, do not add "
    "new placeholders, and do not invent names, numbers, addresses or identifiers. Reply with the "
    "rewritten text only."
)


class LeakToModelError(RuntimeError):
    """A raw sensitive value was about to be sent to the model."""


class LlmRewriteRejected(RuntimeError):
    """The model's rewrite dropped or altered placeholders."""


@dataclass
class PlaceholderText:
    text: str
    mapping: dict[str, tuple[Finding, str]] = field(default_factory=dict)


def to_placeholders(text: str, findings: list[Finding]) -> PlaceholderText:
    """Replace every root-level span finding with a typed placeholder; equal values share one."""
    spans = sorted((f for f in findings if f.locator.path == "$" and not f.locator.whole),
                   key=lambda f: f.locator.start or 0)
    counters: dict[str, int] = {}
    by_value: dict[tuple[str, str], str] = {}
    mapping: dict[str, tuple[Finding, str]] = {}
    pieces: list[str] = []
    cursor = 0
    for f in spans:
        start, end = f.locator.start or 0, f.locator.end or 0
        if start < cursor:
            continue
        original = text[start:end]
        key = (f.entity_type, normalise(original))
        label = by_value.get(key)
        if label is None:
            counters[f.entity_type] = counters.get(f.entity_type, 0) + 1
            label = f"{{{f.entity_type}_{counters[f.entity_type]}}}"
            by_value[key] = label
            mapping[label] = (f, original)
        pieces.extend([text[cursor:start], label])
        cursor = end
    pieces.append(text[cursor:])
    return PlaceholderText("".join(pieces), mapping)


def assert_placeholder_safe(templated: str, originals: list[str], *, min_len: int = 4) -> None:
    """Raise if any original (normalised, len ≥ min_len) still appears in the text bound for the model."""
    hay = normalise(templated)
    for original in originals:
        needle = normalise(original)
        if len(needle) >= min_len and needle in hay:
            raise LeakToModelError("refusing to send text that still contains an original value")


def fill_placeholders(templated: str, mapping: dict[str, tuple[Finding, str]], surrogates: SurrogateProvider,
                      locale: str, anchor: str) -> str:
    def replace(match: re.Match[str]) -> str:
        label = match.group(0)
        if label not in mapping:
            return ""
        finding, original = mapping[label]
        return surrogates.surrogate(finding.entity_type, original, locale, anchor)

    return PLACEHOLDER_RE.sub(replace, templated)


async def rewrite_placeholders(templated: str, gateway: LlmGateway, *, temperature: float = 0.7) -> str:
    result = await gateway.complete([{"role": "system", "content": REWRITE_SYSTEM},
                                     {"role": "user", "content": templated}],
                                    temperature=temperature, max_tokens=max(256, len(templated)), purpose="rewrite")
    wanted = set(PLACEHOLDER_RE.findall(templated))
    got = set(PLACEHOLDER_RE.findall(result.text))
    if wanted != got:
        raise LlmRewriteRejected("placeholders_missing")
    return result.text


async def synthesize_text(text: str, findings: list[Finding], *, surrogates: SurrogateProvider, locale: str,
                          anchor: str, gateway: LlmGateway | None, llm_rewrite: bool) -> tuple[str, dict[str, Any]]:
    """The full pipeline; returns ``(output_text, info)`` with ``info`` safe to log."""
    ph = to_placeholders(text, findings)
    assert_placeholder_safe(ph.text, [o for _, o in ph.mapping.values()])
    info: dict[str, Any] = {"llm_calls": 0, "llm_used": False, "fallback": None, "placeholders": len(ph.mapping)}
    templated = ph.text
    if llm_rewrite and gateway is not None and gateway.available() and ph.mapping:
        info["llm_calls"] = 1
        try:
            templated = await rewrite_placeholders(ph.text, gateway)
            info["llm_used"] = True
        except LlmRewriteRejected as exc:
            info["fallback"] = str(exc)
        except (LlmCallError, LlmUnavailable) as exc:
            info["fallback"] = type(exc).__name__
    return fill_placeholders(templated, ph.mapping, surrogates, locale, anchor), info
```

- [ ] **Step 4: Run tests, lint, types; commit**

Run: `pytest -q tests/test_llm_text.py && ruff check dps tests && mypy dps` → `3 passed`.
```bash
git add dps/engine/synth/llm_text.py tests/test_llm_text.py
git commit -m "feat(synth): placeholder-only LLM text rewriting with leak guard and fallback"
```

---

### Task 22: The engine façade — detect / plan / obfuscate / synthesize / reveal

**Files:**
- Create: `dps/engine/service.py`
- Modify: `dps/engine/transform/apply.py` (rename `_anchor_for` → public `anchor_for`, update its one call site)
- Test: `tests/test_service.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_service.py`:
```python
from __future__ import annotations

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from dps.config import Settings
from dps.engine.service import EngineError, Payload, PrivacyEngine, RequestOptions, VerificationFailed
from dps.engine.transform.crypto import unwrap_dek
from dps.llm.gateway import LlmGateway

SAMPLE = {
    "customer_id": "CUST-884120", "full_name": "Ananya Krishnan", "dob": "1988-03-14",
    "aadhaar": "2341 2341 2346", "email": "ananya.k@example-mail.in", "phone": "+91 98451 20377",
    "risk_score": 712, "notes": "Mail ann.k@example-mail.in about 1988-03-14 soon",
}


class RotCipher:
    def encrypt(self, alphabet: str, plaintext: str) -> str:
        return "".join(alphabet[(alphabet.index(c) + 3) % len(alphabet)] for c in plaintext)

    def decrypt(self, alphabet: str, ciphertext: str) -> str:
        return "".join(alphabet[(alphabet.index(c) - 3) % len(alphabet)] for c in ciphertext)


@pytest.fixture
def engine() -> PrivacyEngine:
    s = Settings(_env_file=None, llm_provider="stub", fpe_enabled=True, ner_enabled=False, default_locale="en_IN")
    return PrivacyEngine(s, gateway=LlmGateway(s), cipher_factory=lambda k, t: RotCipher())


def test_detect_and_plan(engine: PrivacyEngine) -> None:
    det = engine.detect(Payload(data=SAMPLE), None, RequestOptions())
    assert det.record_sensitivity == "CRITICAL" and det.report.findings_total >= 6
    plan = engine.plan(Payload(data=SAMPLE), "pci-dss", RequestOptions(), mode="obfuscate")
    assert any(r["entity_type"] == "AADHAAR" and r["method"] == "det_token" for r in plan.plan)


def test_obfuscate_reveal_round_trip_and_consistency(engine: PrivacyEngine) -> None:
    first = engine.obfuscate(Payload(data=SAMPLE), None, RequestOptions())
    assert first.key_material and first.key_material.has_dek() and first.result["risk_score"] == 712
    assert first.result["email"] != SAMPLE["email"] and first.report.verification.passed  # type: ignore[union-attr]
    back = engine.reveal(Payload(data=first.result), first.key_material.model_dump(mode="json"),
                         [h.model_dump(mode="json") for h in first.reveal_hints])
    assert back.result == SAMPLE and back.revealed >= 6
    again = engine.obfuscate(Payload(data=SAMPLE), None, RequestOptions(
        consistency={"scope": "key", "key_material": first.key_material.model_dump(mode="json")}))
    assert again.result["aadhaar"] == first.result["aadhaar"] and again.key_material.kid == first.key_material.kid  # type: ignore[union-attr]


def test_wrap_key_with_rsa(engine: PrivacyEngine) -> None:
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    out = engine.obfuscate(Payload(data=SAMPLE), None, RequestOptions(wrap_key={"type": "rsa-oaep-256", "public_key_pem": pem}))
    assert out.key_material and out.key_material.dek is None and out.key_material.wrapped_dek
    private_pem = private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                        serialization.NoEncryption()).decode()
    assert len(unwrap_dek(out.key_material.wrapped_dek, private_pem)) == 32


async def test_synthesize_json_and_text(engine: PrivacyEngine) -> None:
    out = await engine.synthesize(Payload(data=SAMPLE), "synthetic-realistic", RequestOptions())
    assert out.result["full_name"] != SAMPLE["full_name"] and out.result["risk_score"] == 712
    assert out.synthesis_seed is not None and out.reveal_hints == [] and out.report.verification.passed  # type: ignore[union-attr]
    assert "ananya" not in str(out.result).lower()
    text = await engine.synthesize(Payload(text="Call Ann at ann.k@example-mail.in"), "synthetic-realistic", RequestOptions())
    assert "example-mail" not in text.result and text.report.llm["raw_pii_sent"] == 0
    strict = await engine.synthesize(Payload(data=SAMPLE), "synthetic-strict", RequestOptions())
    assert strict.synthesis_seed is None  # scope=request: no linkage echoed


def test_errors(engine: PrivacyEngine, monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(EngineError):
        engine.detect(Payload(data=SAMPLE), "no-such-preset", RequestOptions())
    with pytest.raises(ValueError):
        Payload(data={"a": 1}, text="both")
    import dps.engine.service as svc
    from dps.engine.verify import Verification
    monkeypatch.setattr(svc, "verify_output", lambda *a, **k: Verification(passed=False, checked=1, hits=[{"path": "$.x", "entity_type": "SSN"}]))
    with pytest.raises(VerificationFailed):
        engine.obfuscate(Payload(data=SAMPLE), None, RequestOptions())
```

- [ ] **Step 2: Run to confirm failure**

Run: `pytest -q tests/test_service.py`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Rename the anchor helper in `dps/engine/transform/apply.py`**

Change `def _anchor_for(` to `def anchor_for(` and its call `anchor = _anchor_for(document, plan)` to `anchor = anchor_for(document, plan)`. Run `pytest -q tests/test_apply.py` → still `3 passed`.

- [ ] **Step 4: Write `dps/engine/service.py`**

```python
"""``PrivacyEngine`` — the façade the Layer 1 routers (and later the job workers) call.

Request flow: resolve policy → detect → plan → transform → verify → report. Obfuscation is CPU
work and synchronous; synthesis is async because the optional LLM rewrite is.
"""
from __future__ import annotations

import time
from typing import Any

from pydantic import BaseModel, Field, model_validator

from dps.config import Settings
from dps.engine.detect.custom import custom_detector_from
from dps.engine.detect.ensemble import Detector, DetectorConfig
from dps.engine.detect.ner_presidio import build_ner
from dps.engine.findings import Finding, Plan
from dps.engine.planner import build_plan, explain_plan
from dps.engine.policy import Policy, resolve_policy
from dps.engine.report import Report, build_report
from dps.engine.synth.llm_text import LeakToModelError, synthesize_text
from dps.engine.synth.surrogates import (
    SurrogateEngine,
    SynthesisSeed,
    generate_synthesis_seed,
    seed_bytes,
    seed_from_caller,
)
from dps.engine.taxonomy import Sensitivity
from dps.engine.transform import fpe
from dps.engine.transform.apply import ApplyOptions, RevealHint, anchor_for, apply_plan, reveal
from dps.engine.transform.crypto import (
    KeyMaterial,
    generate_key_material,
    key_material_from_caller,
    wrap_key_material,
)
from dps.engine.transform.methods import CipherFactory, MethodError
from dps.engine.verify import Verification, collect_originals, verify_output
from dps.llm.gateway import LlmGateway
from dps.observability import FINDINGS, TRANSFORMS, VERIFY_FAIL, get_logger

logger = get_logger(__name__)


class EngineError(ValueError):
    """A request the engine cannot honour (bad policy, bad key material, bad hints)."""


class VerificationFailed(EngineError):
    """The leakage sweep found an original value in the output under a strict policy."""


class Payload(BaseModel):
    data: Any | None = None
    text: str | None = None

    @model_validator(mode="after")
    def _exactly_one(self) -> Payload:
        if (self.data is None) == (self.text is None):
            raise ValueError("provide exactly one of data or text")
        return self


class ConsistencyOptions(BaseModel):
    scope: str | None = None
    key_material: dict[str, Any] | None = None
    synthesis_seed: dict[str, Any] | None = None


class RequestOptions(BaseModel):
    locale: str | None = None
    token_style: str | None = None
    wrap_key: dict[str, str] | None = None
    consistency: ConsistencyOptions = Field(default_factory=ConsistencyOptions)
    llm_rewrite: bool | None = None


class DetectResponse(BaseModel):
    findings: list[Finding]
    record_sensitivity: Sensitivity
    report: Report


class PlanResponse(BaseModel):
    findings: list[Finding]
    plan: list[dict[str, Any]]
    record_sensitivity: Sensitivity


class TransformResponse(BaseModel):
    result: Any
    findings: list[Finding]
    plan: list[dict[str, Any]]
    key_material: KeyMaterial | None = None
    reveal_hints: list[RevealHint] = Field(default_factory=list)
    synthesis_seed: SynthesisSeed | None = None
    report: Report


class RevealResponse(BaseModel):
    result: Any
    revealed: int
    report: Report


def _kept(plan: Plan) -> set[tuple[str, int | None, int | None]]:
    return {(e.finding.locator.path, e.finding.locator.start, e.finding.locator.end)
            for e in plan.entries if e.method == "keep"}


class PrivacyEngine:
    def __init__(self, settings: Settings, *, gateway: LlmGateway | None = None,
                 cipher_factory: CipherFactory | None = None) -> None:
        self.settings = settings
        self.gateway = gateway
        self.cipher_factory = cipher_factory
        self.ner = build_ner(tuple(settings.ner_languages_list), settings.ner_enabled)
        self.fpe_enabled = settings.fpe_enabled and (cipher_factory is not None or fpe.fpe_available())

    # -- helpers ------------------------------------------------------------------------------
    def policy(self, ref: str | dict[str, Any] | None) -> Policy:
        try:
            return resolve_policy(ref, default_name=self.settings.default_policy)
        except (KeyError, ValueError) as exc:
            raise EngineError(f"invalid policy: {exc}") from exc

    def _locale(self, policy: Policy, options: RequestOptions) -> str:
        return options.locale or policy.locale.default or self.settings.default_locale

    def _detector(self, policy: Policy, locale: str) -> Detector:
        config = DetectorConfig(min_score=policy.detect.min_score, locale=locale,
                                ner_enabled="ner" in policy.detect.tiers and self.ner is not None)
        custom = custom_detector_from(policy.detect.custom_recognizers, policy.detect.declared_fields)
        return Detector(config, ner=self.ner, custom=custom)

    def _detect(self, payload: Payload, policy: Policy, locale: str) -> tuple[list[Finding], Sensitivity]:
        result = self._detector(policy, locale).detect(data=payload.data, text=payload.text)
        for f in result.findings:
            for d in f.detectors:
                FINDINGS.labels(f.entity_type, d.split(":")[0]).inc()
        return result.findings, result.record_sensitivity

    def _apply_options(self, policy: Policy, key: KeyMaterial | None, surrogates: SurrogateEngine | None,
                       options: RequestOptions, locale: str) -> ApplyOptions:
        return ApplyOptions(policy=policy, key=key, surrogates=surrogates, fpe_enabled=self.fpe_enabled,
                            cipher_factory=self.cipher_factory,
                            token_style=options.token_style or policy.output.token_style, locale=locale)

    def _verify(self, findings: list[Finding], payload: Payload, output: Any, plan: Plan, policy: Policy,
                mode: str) -> Verification:
        originals = collect_originals(findings, data=payload.data, text=payload.text)
        verification = verify_output(originals, output, kept=_kept(plan))
        if not verification.passed:
            VERIFY_FAIL.labels(mode).inc()
            logger.warning("verification_failed", mode=mode, hits=len(verification.hits))
            if policy.verify.leakage_check == "strict":
                raise VerificationFailed("output failed the leakage sweep; nothing was returned")
        return verification

    # -- operations ---------------------------------------------------------------------------
    def detect(self, payload: Payload, policy_ref: str | dict[str, Any] | None, options: RequestOptions) -> DetectResponse:
        t0 = time.perf_counter()
        policy = self.policy(policy_ref)
        findings, sensitivity = self._detect(payload, policy, self._locale(policy, options))
        report = build_report(mode="detect", policy_name=policy.name, policy_hash=policy.config_hash(),
                              findings=findings, plan=None, methods_used={}, notes={}, reversible_values=0,
                              verification=None, llm_calls=0, duration_ms=(time.perf_counter() - t0) * 1000)
        report.record_sensitivity = str(sensitivity)
        return DetectResponse(findings=findings, record_sensitivity=sensitivity, report=report)

    def plan(self, payload: Payload, policy_ref: str | dict[str, Any] | None, options: RequestOptions,
             *, mode: str) -> PlanResponse:
        policy = self.policy(policy_ref)
        findings, sensitivity = self._detect(payload, policy, self._locale(policy, options))
        plan = build_plan(findings, policy, mode, fpe_enabled=self.fpe_enabled)
        return PlanResponse(findings=findings, plan=explain_plan(plan), record_sensitivity=sensitivity)

    def obfuscate(self, payload: Payload, policy_ref: str | dict[str, Any] | None, options: RequestOptions) -> TransformResponse:
        t0 = time.perf_counter()
        policy = self.policy(policy_ref)
        locale = self._locale(policy, options)
        findings, _ = self._detect(payload, policy, locale)
        plan = build_plan(findings, policy, "obfuscate", fpe_enabled=self.fpe_enabled)
        scope = options.consistency.scope or policy.consistency.scope
        try:
            key = (key_material_from_caller(options.consistency.key_material)
                   if options.consistency.key_material else generate_key_material(scope))
        except ValueError as exc:
            raise EngineError(f"invalid key material: {exc}") from exc
        try:
            applied = apply_plan(plan=plan, options=self._apply_options(policy, key, None, options, locale),
                                 data=payload.data, text=payload.text)
        except MethodError as exc:
            raise EngineError(str(exc)) from exc
        for method, count in applied.methods_used.items():
            TRANSFORMS.labels("obfuscate", method).inc(count)
        verification = self._verify(findings, payload, applied.output, plan, policy, "obfuscate")
        key_out = key
        if options.wrap_key:
            try:
                key_out = wrap_key_material(key, options.wrap_key.get("public_key_pem", ""))
            except ValueError as exc:
                raise EngineError(f"wrap_key: {exc}") from exc
        report = build_report(mode="obfuscate", policy_name=policy.name, policy_hash=policy.config_hash(),
                              findings=findings, plan=plan, methods_used=applied.methods_used, notes=applied.notes,
                              reversible_values=len(applied.reveal_hints), verification=verification, llm_calls=0,
                              duration_ms=(time.perf_counter() - t0) * 1000)
        return TransformResponse(result=applied.output, findings=findings, plan=explain_plan(plan),
                                 key_material=key_out, reveal_hints=applied.reveal_hints, report=report)

    async def synthesize(self, payload: Payload, policy_ref: str | dict[str, Any] | None, options: RequestOptions) -> TransformResponse:
        t0 = time.perf_counter()
        policy = self.policy(policy_ref)
        locale = self._locale(policy, options)
        findings, _ = self._detect(payload, policy, locale)
        plan = build_plan(findings, policy, "synthesize", fpe_enabled=self.fpe_enabled)
        scope = options.consistency.scope or policy.consistency.scope
        try:
            seed = (seed_from_caller(options.consistency.synthesis_seed)
                    if options.consistency.synthesis_seed else generate_synthesis_seed())
        except ValueError as exc:
            raise EngineError(f"invalid synthesis seed: {exc}") from exc
        surrogates = SurrogateEngine(seed_bytes(seed), default_locale=locale)
        llm_rewrite = (options.llm_rewrite if options.llm_rewrite is not None else policy.synthesize.llm_rewrite_text)
        llm_rewrite = llm_rewrite and self.settings.llm_text_rewrite
        llm_calls = 0
        if payload.text is not None:
            anchor = anchor_for(payload.text, plan)
            try:
                output, info = await synthesize_text(payload.text, findings, surrogates=surrogates, locale=locale,
                                                     anchor=anchor, gateway=self.gateway, llm_rewrite=llm_rewrite)
            except LeakToModelError as exc:
                raise EngineError(str(exc)) from exc
            llm_calls = int(info["llm_calls"])
            methods_used, notes = {"surrogate": len(findings)}, ({} if not info["fallback"] else {f"llm:{info['fallback']}": 1})
        else:
            try:
                applied = apply_plan(plan=plan, options=self._apply_options(policy, None, surrogates, options, locale),
                                     data=payload.data)
            except MethodError as exc:
                raise EngineError(str(exc)) from exc
            output, methods_used, notes = applied.output, applied.methods_used, applied.notes
        for method, count in methods_used.items():
            TRANSFORMS.labels("synthesize", method).inc(count)
        verification = self._verify(findings, payload, output, plan, policy, "synthesize")
        report = build_report(mode="synthesize", policy_name=policy.name, policy_hash=policy.config_hash(),
                              findings=findings, plan=plan, methods_used=methods_used, notes=notes,
                              reversible_values=0, verification=verification, llm_calls=llm_calls,
                              duration_ms=(time.perf_counter() - t0) * 1000)
        return TransformResponse(result=output, findings=findings, plan=explain_plan(plan),
                                 synthesis_seed=seed if scope != "request" else None, report=report)

    def reveal(self, payload: Payload, key_material: dict[str, Any], hints: list[dict[str, Any]]) -> RevealResponse:
        t0 = time.perf_counter()
        try:
            key = key_material_from_caller(key_material)
            parsed = [RevealHint.model_validate(h) for h in hints]
        except ValueError as exc:
            raise EngineError(f"invalid reveal request: {exc}") from exc
        policy = self.policy(None)
        options = ApplyOptions(policy=policy, key=key, surrogates=None, fpe_enabled=self.fpe_enabled,
                               cipher_factory=self.cipher_factory, locale=self.settings.default_locale)
        output: Any = payload.data if payload.data is not None else payload.text
        try:
            result = reveal(output=output, key=key, hints=parsed, options=options)
        except (MethodError, KeyError, IndexError, ValueError) as exc:
            raise EngineError(f"reveal failed: {type(exc).__name__}") from exc
        report = build_report(mode="reveal", policy_name=policy.name, policy_hash=policy.config_hash(), findings=[],
                              plan=None, methods_used={}, notes={}, reversible_values=len(parsed), verification=None,
                              llm_calls=0, duration_ms=(time.perf_counter() - t0) * 1000)
        return RevealResponse(result=result, revealed=len(parsed), report=report)
```

- [ ] **Step 5: Run tests, lint, types**

Run: `pytest -q && ruff check dps tests && mypy dps`
Expected: everything green (the suite is now ~95 tests), clean. If `test_synthesize_json_and_text` fails on the text case, check that the `synthetic-realistic` preset's `min_score` lets the EMAIL span through (it does: 0.9 + context).

- [ ] **Step 6: Commit**

```bash
git add dps/engine/service.py dps/engine/transform/apply.py tests/test_service.py
git commit -m "feat(engine): PrivacyEngine facade — detect, plan, obfuscate, synthesize, reveal"
```

---

### Task 23: Auth, rate limiting and audit (stateless)

**Files:**
- Create: `dps/auth.py`, `dps/ratelimit.py`, `dps/audit.py`
- Test: `tests/test_auth.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_auth.py`:
```python
from __future__ import annotations

import json

import pytest

from dps.audit import AuditEvent, MemoryAuditSink
from dps.auth import AuthError, StaticKeyStore, authenticate, hash_key, new_api_key, resolve_tenant
from dps.config import Settings
from dps.ratelimit import RateLimiter


def _store(**kw) -> StaticKeyStore:  # noqa: ANN003
    keys = [{"name": "ops", "key": "dps_plain", "tenant_ids": ["retail", "cards"], "scopes": ["detect", "obfuscate"]},
            {"name": "hashed", "key_sha256": hash_key("dps_hashed"), "tenant_ids": ["*"], "scopes": ["*"]}]
    return StaticKeyStore.from_settings(Settings(_env_file=None, api_keys=json.dumps(keys), **kw))


def test_store_resolves_plain_and_hashed_keys_and_bootstrap() -> None:
    store = _store(bootstrap_api_key="dps_boot")
    assert store.lookup("dps_plain").name == "ops" and store.lookup("dps_hashed").scopes == ["*"]  # type: ignore[union-attr]
    assert store.lookup("dps_boot").name == "bootstrap" and store.lookup("nope") is None  # type: ignore[union-attr]
    assert new_api_key().startswith("dps_") and len(new_api_key()) > 30


def test_authenticate_and_scopes() -> None:
    store = _store()
    p = authenticate(store, enabled=True, raw_key="dps_plain")
    assert p.has_scope("detect") and not p.has_scope("reveal") and p.can_access("retail") and not p.can_access("x")
    with pytest.raises(AuthError) as missing:
        authenticate(store, enabled=True, raw_key=None)
    assert missing.value.status == 401
    with pytest.raises(AuthError) as bad:
        authenticate(store, enabled=True, raw_key="wrong")
    assert bad.value.status == 401
    local = authenticate(store, enabled=False, raw_key=None)
    assert local.key_id == "local-dev" and local.has_scope("admin")


def test_resolve_tenant_rules() -> None:
    store = _store()
    ops = authenticate(store, enabled=True, raw_key="dps_plain")
    assert resolve_tenant(ops, "cards") == "cards"
    with pytest.raises(AuthError) as denied:
        resolve_tenant(ops, "wealth")
    assert denied.value.status == 403
    with pytest.raises(AuthError):
        resolve_tenant(ops, None)  # two tenants granted, none chosen
    wild = authenticate(store, enabled=True, raw_key="dps_hashed")
    assert resolve_tenant(wild, None) == "default" and resolve_tenant(wild, "anything") == "anything"


def test_rate_limiter_burst_and_refill() -> None:
    now = [0.0]
    limiter = RateLimiter(rate=10.0, burst=2, enabled=True, clock=lambda: now[0])
    assert limiter.check("k") and limiter.check("k") and not limiter.check("k")
    now[0] += 0.1
    assert limiter.check("k")
    assert RateLimiter(rate=1.0, burst=1, enabled=False).check("k") and RateLimiter(1.0, 1, enabled=False).check("k")


def test_audit_event_is_value_free_and_sink_records() -> None:
    sink = MemoryAuditSink()
    event = AuditEvent(trace_id="t1", route="/api/v1/obfuscate", tenant_id="retail", key_id="k1", mode="obfuscate",
                       policy="gdpr-pseudonymise", findings_total=3, findings_by_type={"SSN": 1}, methods_used={"fpe_digits": 1},
                       verification_passed=True, reversible_values=1, duration_ms=4.2, outcome="ok")
    sink.write(event)
    assert sink.events[-1].route == "/api/v1/obfuscate"
    assert not ({"value", "data", "text", "result", "dek"} & set(AuditEvent.model_fields))
```

- [ ] **Step 2: Run to confirm failure**

Run: `pytest -q tests/test_auth.py`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write `dps/auth.py`**

```python
"""API-key authentication with tenant grants and scopes (sibling pattern, stateless for M0).

Keys come from ``DPS_API_KEYS`` (JSON) or ``DPS_API_KEYS_FILE``; each record holds either the raw
``key`` or its ``key_sha256``. Only hashes are kept in memory. ``DPS_BOOTSTRAP_API_KEY`` seeds one
wildcard key for local demos (production posture refuses it). A DB-backed store with rotation
arrives with jobs (M2) behind the same ``KeyStore`` protocol.
"""
from __future__ import annotations

import hashlib
import json
import secrets
from typing import Protocol

from fastapi import HTTPException, Request
from pydantic import BaseModel, Field, model_validator

from dps.config import Settings

API_KEY_HEADER = "X-API-KEY"
TENANT_HEADER = "X-DPS-Tenant"
KEY_PREFIX = "dps_"
WILDCARD = "*"
SCOPES = ("detect", "obfuscate", "synthesize", "reveal", "jobs", "scan", "admin")
WWW_AUTHENTICATE = f'ApiKey realm="data-privacy-services", header="{API_KEY_HEADER}"'


class AuthError(Exception):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status, self.detail = status, detail


class Principal(BaseModel):
    key_id: str
    name: str
    tenant_ids: list[str] = Field(default_factory=list)
    scopes: list[str] = Field(default_factory=list)

    def can_access(self, tenant_id: str) -> bool:
        return WILDCARD in self.tenant_ids or tenant_id in self.tenant_ids

    def has_scope(self, scope: str) -> bool:
        return WILDCARD in self.scopes or scope in self.scopes


class KeyRecord(BaseModel):
    name: str
    key: str | None = None
    key_sha256: str | None = None
    tenant_ids: list[str] = Field(default_factory=lambda: [WILDCARD])
    scopes: list[str] = Field(default_factory=lambda: [WILDCARD])

    @model_validator(mode="after")
    def _one_secret(self) -> KeyRecord:
        if bool(self.key) == bool(self.key_sha256):
            raise ValueError(f"key record {self.name!r} needs exactly one of key / key_sha256")
        return self


def hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def new_api_key() -> str:
    return KEY_PREFIX + secrets.token_urlsafe(32)


class KeyStore(Protocol):
    def lookup(self, raw_key: str) -> Principal | None: ...


class StaticKeyStore:
    def __init__(self, records: list[KeyRecord]) -> None:
        self._by_hash: dict[str, Principal] = {}
        for i, rec in enumerate(records):
            digest = rec.key_sha256 or hash_key(rec.key or "")
            self._by_hash[digest] = Principal(key_id=f"static-{i}-{digest[:8]}", name=rec.name,
                                              tenant_ids=rec.tenant_ids, scopes=rec.scopes)

    @classmethod
    def from_settings(cls, settings: Settings) -> StaticKeyStore:
        records = [KeyRecord.model_validate(r) for r in (json.loads(settings.api_keys) if settings.api_keys else [])]
        if settings.bootstrap_api_key:
            records.append(KeyRecord(name="bootstrap", key=settings.bootstrap_api_key))
        return cls(records)

    def lookup(self, raw_key: str) -> Principal | None:
        return self._by_hash.get(hash_key(raw_key))

    def __len__(self) -> int:
        return len(self._by_hash)


LOCAL_PRINCIPAL = Principal(key_id="local-dev", name="local-dev", tenant_ids=[WILDCARD], scopes=[WILDCARD])


def authenticate(store: KeyStore, *, enabled: bool, raw_key: str | None) -> Principal:
    if not enabled:
        return LOCAL_PRINCIPAL
    if not raw_key:
        raise AuthError(401, "missing API key")
    principal = store.lookup(raw_key)
    if principal is None:
        raise AuthError(401, "invalid API key")
    return principal


def resolve_tenant(principal: Principal, requested: str | None) -> str:
    """The tenant this call acts for: the header when given (and granted), else the only grant."""
    if requested:
        if not principal.can_access(requested):
            raise AuthError(403, "tenant not granted to this key")
        return requested
    if WILDCARD in principal.tenant_ids:
        return "default"
    if len(principal.tenant_ids) == 1:
        return principal.tenant_ids[0]
    raise AuthError(403, f"{TENANT_HEADER} header required: key is granted several tenants")


# --- FastAPI glue --------------------------------------------------------------------------------
def current_principal(request: Request) -> Principal:
    auth = request.app.state.auth
    try:
        return authenticate(auth["store"], enabled=auth["enabled"], raw_key=request.headers.get(API_KEY_HEADER))
    except AuthError as exc:
        headers = {"WWW-Authenticate": WWW_AUTHENTICATE} if exc.status == 401 else None
        raise HTTPException(status_code=exc.status, detail=exc.detail, headers=headers) from exc


def require_scope(scope: str):  # noqa: ANN201 - FastAPI dependency factory
    def dependency(request: Request) -> Principal:
        principal = current_principal(request)
        if not principal.has_scope(scope):
            raise HTTPException(status_code=403, detail=f"scope {scope!r} required")
        try:
            request.state.tenant_id = resolve_tenant(principal, request.headers.get(TENANT_HEADER))
        except AuthError as exc:
            raise HTTPException(status_code=exc.status, detail=exc.detail) from exc
        request.state.principal = principal
        return principal

    return dependency
```

- [ ] **Step 4: Write `dps/ratelimit.py`**

```python
"""Per-key token-bucket rate limiting — a per-process backstop; the bank's gateway owns global limits."""
from __future__ import annotations

import threading
import time
from collections.abc import Callable

from fastapi import HTTPException, Request


class RateLimiter:
    def __init__(self, rate: float, burst: int, *, enabled: bool = True,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.rate, self.burst, self.enabled, self._clock = rate, burst, enabled, clock
        self._buckets: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()

    def check(self, key_id: str) -> bool:
        if not self.enabled:
            return True
        now = self._clock()
        with self._lock:
            tokens, last = self._buckets.get(key_id, (float(self.burst), now))
            tokens = min(float(self.burst), tokens + (now - last) * self.rate)
            if tokens < 1.0:
                self._buckets[key_id] = (tokens, now)
                return False
            self._buckets[key_id] = (tokens - 1.0, now)
            return True


def enforce_rate_limit(request: Request) -> None:
    limiter: RateLimiter = request.app.state.rate_limiter
    principal = getattr(request.state, "principal", None)
    key_id = principal.key_id if principal else request.client.host if request.client else "anonymous"
    if not limiter.check(key_id):
        raise HTTPException(status_code=429, detail="rate limit exceeded", headers={"Retry-After": "1"})
```

- [ ] **Step 5: Write `dps/audit.py`**

```python
"""Value-free audit events. M0 writes them to the structured log; M2 adds the append-only table."""
from __future__ import annotations

from collections import deque
from datetime import UTC, datetime
from typing import Protocol

from pydantic import BaseModel, Field

from dps.observability import get_logger

logger = get_logger("dps.audit")


class AuditEvent(BaseModel):
    ts: datetime = Field(default_factory=lambda: datetime.now(UTC))
    trace_id: str
    route: str
    tenant_id: str
    key_id: str
    mode: str
    policy: str
    findings_total: int = 0
    findings_by_type: dict[str, int] = Field(default_factory=dict)
    methods_used: dict[str, int] = Field(default_factory=dict)
    verification_passed: bool | None = None
    reversible_values: int = 0
    duration_ms: float = 0.0
    outcome: str = "ok"


class AuditSink(Protocol):
    def write(self, event: AuditEvent) -> None: ...


class LogAuditSink:
    def write(self, event: AuditEvent) -> None:
        logger.info("audit", **event.model_dump(mode="json"))


class MemoryAuditSink:
    def __init__(self, maxlen: int = 1000) -> None:
        self.events: deque[AuditEvent] = deque(maxlen=maxlen)

    def write(self, event: AuditEvent) -> None:
        self.events.append(event)
```

- [ ] **Step 6: Run tests, lint, types; commit**

Run: `pytest -q tests/test_auth.py && ruff check dps tests && mypy dps` → `5 passed`.
```bash
git add dps/auth.py dps/ratelimit.py dps/audit.py tests/test_auth.py
git commit -m "feat: static API-key auth with tenant grants and scopes, token-bucket rate limit, audit events"
```

---

### Task 24: Layer 1 API — schemas, routers, app factory

**Files:**
- Create: `dps/layers/__init__.py`, `dps/layers/api/__init__.py`, `dps/layers/api/schemas.py`, `dps/layers/api/common.py`, `dps/layers/api/routers/__init__.py`, `dps/layers/api/routers/ops.py`, `dps/layers/api/routers/detect.py`, `dps/layers/api/routers/transform.py`, `dps/layers/api/routers/policies.py`, `dps/app.py`
- Test: `tests/test_api.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_api.py`:
```python
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from dps.app import create_app
from dps.audit import MemoryAuditSink
from dps.config import Settings

KEYS = [{"name": "full", "key": "dps_full", "tenant_ids": ["*"], "scopes": ["*"]},
        {"name": "ro", "key": "dps_ro", "tenant_ids": ["retail"], "scopes": ["detect"]}]
SAMPLE = {"customer_id": "CUST-884120", "full_name": "Ananya Krishnan", "email": "ananya.k@example-mail.in",
          "aadhaar": "2341 2341 2346", "risk_score": 712, "notes": "mail ann.k@example-mail.in"}
FULL = {"X-API-KEY": "dps_full"}
RO = {"X-API-KEY": "dps_ro"}


@pytest.fixture
def client() -> tuple[TestClient, MemoryAuditSink]:
    settings = Settings(_env_file=None, env="local", auth_enabled=True, api_keys=json.dumps(KEYS), llm_provider="stub",
                        fpe_enabled=False, ner_enabled=False, max_text_chars=200, max_items=50, rate_limit_enabled=False)
    sink = MemoryAuditSink()
    return TestClient(create_app(settings, audit_sink=sink)), sink


def test_ops_endpoints(client) -> None:  # noqa: ANN001
    c, _ = client
    assert c.get("/health").json() == {"status": "ok"}
    ready = c.get("/readyz")
    assert ready.status_code == 200 and ready.json()["ready"] is True and "llm-gateway" in ready.json()["components"]
    assert b"dps_llm_raw_pii_sent_total 0.0" in c.get("/metrics").content
    headers = c.get("/health").headers
    assert headers["x-content-type-options"] == "nosniff" and headers["x-frame-options"] == "DENY"


def test_auth_and_scopes(client) -> None:  # noqa: ANN001
    c, _ = client
    assert c.post("/api/v1/detect", json={"data": SAMPLE}).status_code == 401
    assert c.post("/api/v1/detect", json={"data": SAMPLE}, headers={"X-API-KEY": "nope"}).status_code == 401
    assert c.post("/api/v1/obfuscate", json={"data": SAMPLE}, headers=RO).status_code == 403
    ok = c.post("/api/v1/detect", json={"data": SAMPLE}, headers=RO)
    assert ok.status_code == 200 and ok.json()["record_sensitivity"] == "CRITICAL" and ok.json()["trace_id"]


def test_obfuscate_reveal_round_trip_over_http(client) -> None:  # noqa: ANN001
    c, sink = client
    r = c.post("/api/v1/obfuscate", json={"data": SAMPLE, "policy": "gdpr-pseudonymise"}, headers=FULL)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["result"]["risk_score"] == 712 and body["key_material"]["dek"] and body["report"]["verification"]["passed"]
    assert body["result"]["email"] != SAMPLE["email"]
    back = c.post("/api/v1/reveal", json={"data": body["result"], "key_material": body["key_material"],
                                          "reveal_hints": body["reveal_hints"]}, headers=FULL)
    assert back.status_code == 200 and back.json()["result"] == SAMPLE
    assert sink.events[-2].mode == "obfuscate" and sink.events[-1].mode == "reveal" and sink.events[-1].outcome == "ok"
    assert "ananya" not in json.dumps([e.model_dump(mode="json") for e in sink.events]).lower()


def test_synthesize_presets_plan_validate(client) -> None:  # noqa: ANN001
    c, _ = client
    s = c.post("/api/v1/synthesize", json={"data": SAMPLE, "policy": "synthetic-realistic"}, headers=FULL)
    assert s.status_code == 200 and s.json()["result"]["full_name"] != SAMPLE["full_name"] and s.json()["synthesis_seed"]
    presets = c.get("/api/v1/policies/presets", headers=RO).json()
    assert {p["name"] for p in presets} >= {"gdpr-pseudonymise", "pci-dss"}
    plan = c.post("/api/v1/policies/plan", json={"data": SAMPLE, "policy": "pci-dss", "mode": "obfuscate"}, headers=RO)
    assert plan.status_code == 200 and any(row["entity_type"] == "AADHAAR" for row in plan.json()["plan"])
    bad = c.post("/api/v1/policies/validate", json={"policy": {"treat": {"SSN": {"method": "teleport"}}}}, headers=RO)
    assert bad.status_code == 400 and "teleport" in bad.json()["detail"]
    good = c.post("/api/v1/policies/validate", json={"policy": {"preset": "pci-dss", "output": {"token_style": "envelope"}}}, headers=RO)
    assert good.status_code == 200 and good.json()["output"]["token_style"] == "envelope"


def test_limits_and_errors(client) -> None:  # noqa: ANN001
    c, _ = client
    assert c.post("/api/v1/detect", json={"text": "x" * 201}, headers=RO).status_code == 413
    assert c.post("/api/v1/detect", json={"data": {f"k{i}": i for i in range(51)}}, headers=RO).status_code == 413
    assert c.post("/api/v1/detect", json={"data": {"a": 1}, "text": "both"}, headers=RO).status_code == 422
    assert c.post("/api/v1/detect", json={"data": SAMPLE, "policy": "nope"}, headers=RO).status_code == 400
    assert c.post("/api/v1/reveal", json={"data": {"a": "b"}, "key_material": {"alg": "x", "kid": "k", "dek": "c2hvcnQ",
                                                                             "created_at": "2026-01-01T00:00:00Z"}},
                  headers=FULL).status_code == 400
    assert c.get("/api/v1/nothing", headers=FULL).status_code == 404
```

- [ ] **Step 2: Run to confirm failure**

Run: `pytest -q tests/test_api.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'dps.app'`.

- [ ] **Step 3: Write the schemas and the shared request runner**

`dps/layers/__init__.py`: `"""Entry points: Layer 1 API (M0), documents (M1), connectors (M2–M3), scanner (M4)."""`
`dps/layers/api/__init__.py`: `"""Layer 1 — the stateless HTTP API."""`
`dps/layers/api/routers/__init__.py`: empty.

`dps/layers/api/schemas.py`:
```python
"""Request and response bodies for /api/v1 (responses reuse the engine's models plus a trace id)."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from dps.engine.policy import Policy
from dps.engine.service import (
    DetectResponse,
    PlanResponse,
    RequestOptions,
    RevealResponse,
    TransformResponse,
)


class EngineRequest(BaseModel):
    data: Any | None = None
    text: str | None = None
    policy: str | dict[str, Any] | None = None
    options: RequestOptions = Field(default_factory=RequestOptions)


class PlanRequest(EngineRequest):
    mode: Literal["obfuscate", "synthesize"] = "obfuscate"


class RevealRequest(BaseModel):
    data: Any | None = None
    text: str | None = None
    key_material: dict[str, Any]
    reveal_hints: list[dict[str, Any]] = Field(default_factory=list)


class ValidatePolicyRequest(BaseModel):
    policy: str | dict[str, Any]


class DetectOut(DetectResponse):
    trace_id: str


class PlanOut(PlanResponse):
    trace_id: str


class TransformOut(TransformResponse):
    trace_id: str


class RevealOut(RevealResponse):
    trace_id: str


class PolicyOut(Policy):
    pass
```

`dps/layers/api/common.py`:
```python
"""What every engine route does: limits, tracing, error mapping, audit, metrics."""
from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

import structlog
from fastapi import HTTPException, Request

from dps.audit import AuditEvent
from dps.engine.jsonpath import flatten
from dps.engine.service import EngineError, Payload, VerificationFailed
from dps.observability import LATENCY, REQUESTS

T = TypeVar("T")
TRACE_HEADER = "X-Request-ID"


def trace_id(request: Request) -> str:
    return request.headers.get(TRACE_HEADER) or uuid.uuid4().hex[:16]


def payload_from(request: Request, data: Any | None, text: str | None) -> Payload:
    """Validate the body against the configured limits; 413 when it is too large."""
    settings = request.app.state.settings
    try:
        payload = Payload(data=data, text=text)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if text is not None and len(text) > settings.max_text_chars:
        raise HTTPException(status_code=413, detail=f"text exceeds {settings.max_text_chars} characters")
    if data is not None and len(flatten(data)) > settings.max_items:
        raise HTTPException(status_code=413, detail=f"document exceeds {settings.max_items} values")
    return payload


def _audit(request: Request, *, mode: str, policy: str, report: Any, duration_ms: float, outcome: str, tid: str) -> None:
    principal = getattr(request.state, "principal", None)
    event = AuditEvent(
        trace_id=tid, route=request.url.path, tenant_id=getattr(request.state, "tenant_id", "default"),
        key_id=principal.key_id if principal else "anonymous", mode=mode, policy=policy,
        findings_total=getattr(report, "findings_total", 0), findings_by_type=getattr(report, "findings_by_type", {}),
        methods_used=getattr(report, "methods_used", {}),
        verification_passed=getattr(getattr(report, "verification", None), "passed", None),
        reversible_values=getattr(report, "reversible_values", 0), duration_ms=round(duration_ms, 2), outcome=outcome,
    )
    try:
        request.app.state.audit.write(event)
    except Exception:  # noqa: BLE001 - audit must not take the request down in M0 (strict mode arrives with M2)
        structlog.get_logger(__name__).warning("audit_write_failed")


async def run_engine(request: Request, *, mode: str, policy_name: str,
                     call: Callable[[], Awaitable[T] | T]) -> tuple[T, str]:
    """Run an engine operation with tracing, error mapping, metrics and audit."""
    tid = trace_id(request)
    route = request.url.path
    structlog.contextvars.bind_contextvars(trace_id=tid, route=route)
    start = time.perf_counter()
    outcome = "ok"
    try:
        result = call()
        if isinstance(result, Awaitable):
            result = await result
        return result, tid
    except VerificationFailed as exc:
        outcome = "verification_failed"
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except EngineError as exc:
        outcome = "bad_request"
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except HTTPException:
        outcome = "rejected"
        raise
    except Exception as exc:
        outcome = "error"
        structlog.get_logger(__name__).exception("engine_error", error=type(exc).__name__)
        raise HTTPException(status_code=500, detail="internal error") from exc
    finally:
        duration = (time.perf_counter() - start) * 1000
        REQUESTS.labels(route, outcome).inc()
        LATENCY.labels(route).observe(duration / 1000)
        report = locals().get("result")
        _audit(request, mode=mode, policy=policy_name, report=getattr(report, "report", None),
               duration_ms=duration, outcome=outcome, tid=tid)
        structlog.contextvars.unbind_contextvars("trace_id", "route")
```

- [ ] **Step 4: Write the routers**

`dps/layers/api/routers/ops.py`:
```python
"""Liveness, readiness and metrics."""
from __future__ import annotations

from fastapi import APIRouter, Response
from fastapi.responses import JSONResponse

from dps.observability import READINESS, metrics_payload

router = APIRouter()


@router.get("/health", include_in_schema=False)
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz", include_in_schema=False)
def readyz() -> JSONResponse:
    snap = READINESS.snapshot()
    return JSONResponse(snap, status_code=200 if snap["ready"] else 503)


@router.get("/metrics", include_in_schema=False)
def metrics() -> Response:
    body, content_type = metrics_payload()
    return Response(content=body, media_type=content_type)
```

`dps/layers/api/routers/detect.py`:
```python
"""POST /api/v1/detect — findings only."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from dps.auth import require_scope
from dps.layers.api.common import payload_from, run_engine
from dps.layers.api.schemas import DetectOut, EngineRequest
from dps.ratelimit import enforce_rate_limit

router = APIRouter(prefix="/api/v1", tags=["engine"])


@router.post("/detect", response_model=DetectOut, dependencies=[Depends(require_scope("detect")), Depends(enforce_rate_limit)])
async def detect(body: EngineRequest, request: Request) -> DetectOut:
    engine = request.app.state.engine
    payload = payload_from(request, body.data, body.text)
    result, tid = await run_engine(request, mode="detect", policy_name=str(body.policy or engine.settings.default_policy),
                                   call=lambda: engine.detect(payload, body.policy, body.options))
    return DetectOut(**result.model_dump(), trace_id=tid)
```

`dps/layers/api/routers/transform.py`:
```python
"""POST /api/v1/obfuscate, /synthesize, /reveal."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from dps.auth import require_scope
from dps.layers.api.common import payload_from, run_engine
from dps.layers.api.schemas import EngineRequest, RevealOut, RevealRequest, TransformOut
from dps.ratelimit import enforce_rate_limit

router = APIRouter(prefix="/api/v1", tags=["engine"])


@router.post("/obfuscate", response_model=TransformOut,
             dependencies=[Depends(require_scope("obfuscate")), Depends(enforce_rate_limit)])
async def obfuscate(body: EngineRequest, request: Request) -> TransformOut:
    engine = request.app.state.engine
    payload = payload_from(request, body.data, body.text)
    result, tid = await run_engine(request, mode="obfuscate", policy_name=str(body.policy or engine.settings.default_policy),
                                   call=lambda: engine.obfuscate(payload, body.policy, body.options))
    return TransformOut(**result.model_dump(), trace_id=tid)


@router.post("/synthesize", response_model=TransformOut,
             dependencies=[Depends(require_scope("synthesize")), Depends(enforce_rate_limit)])
async def synthesize(body: EngineRequest, request: Request) -> TransformOut:
    engine = request.app.state.engine
    payload = payload_from(request, body.data, body.text)
    result, tid = await run_engine(request, mode="synthesize", policy_name=str(body.policy or engine.settings.default_policy),
                                   call=lambda: engine.synthesize(payload, body.policy, body.options))
    return TransformOut(**result.model_dump(), trace_id=tid)


@router.post("/reveal", response_model=RevealOut,
             dependencies=[Depends(require_scope("reveal")), Depends(enforce_rate_limit)])
async def reveal(body: RevealRequest, request: Request) -> RevealOut:
    engine = request.app.state.engine
    payload = payload_from(request, body.data, body.text)
    result, tid = await run_engine(request, mode="reveal", policy_name="-",
                                   call=lambda: engine.reveal(payload, body.key_material, body.reveal_hints))
    return RevealOut(**result.model_dump(), trace_id=tid)
```

`dps/layers/api/routers/policies.py`:
```python
"""Policy tooling: presets, validate, plan preview."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from dps.auth import require_scope
from dps.engine.policy import list_presets
from dps.engine.service import EngineError
from dps.layers.api.common import payload_from, run_engine
from dps.layers.api.schemas import PlanOut, PlanRequest, PolicyOut, ValidatePolicyRequest
from dps.ratelimit import enforce_rate_limit

router = APIRouter(prefix="/api/v1/policies", tags=["policies"])


@router.get("/presets", dependencies=[Depends(require_scope("detect"))])
def presets() -> list[dict[str, str]]:
    return list_presets()


@router.post("/validate", response_model=PolicyOut, dependencies=[Depends(require_scope("detect"))])
def validate(body: ValidatePolicyRequest, request: Request) -> PolicyOut:
    try:
        policy = request.app.state.engine.policy(body.policy)
    except EngineError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return PolicyOut(**policy.model_dump())


@router.post("/plan", response_model=PlanOut, dependencies=[Depends(require_scope("detect")), Depends(enforce_rate_limit)])
async def plan(body: PlanRequest, request: Request) -> PlanOut:
    engine = request.app.state.engine
    payload = payload_from(request, body.data, body.text)
    result, tid = await run_engine(request, mode=f"plan:{body.mode}", policy_name=str(body.policy or engine.settings.default_policy),
                                   call=lambda: engine.plan(payload, body.policy, body.options, mode=body.mode))
    return PlanOut(**result.model_dump(), trace_id=tid)
```

- [ ] **Step 5: Write `dps/app.py`**

```python
"""FastAPI application factory. Run with ``uvicorn dps.app:create_app --factory``.

Middleware order (outermost first): security headers → CORS → body-size guard → request context.
Auth and rate limiting are per-route dependencies. The compiled console (``frontend/dist``) is
served from the same process with a strict CSP, as in the sibling services.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from dps import __version__
from dps.audit import AuditSink, LogAuditSink
from dps.auth import StaticKeyStore
from dps.config import Settings, get_settings
from dps.engine.service import PrivacyEngine
from dps.layers.api.routers import detect, ops, policies, transform
from dps.llm.gateway import get_gateway
from dps.observability import READINESS, configure_logging, get_logger
from dps.posture import assert_production_posture
from dps.ratelimit import RateLimiter

logger = get_logger(__name__)
FRONTEND_DIST = Path(__file__).resolve().parents[1] / "frontend" / "dist"
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
       "font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'")


class _SecurityHeaders(BaseHTTPMiddleware):
    def __init__(self, app: Any, *, hsts: bool) -> None:
        super().__init__(app)
        self.hsts = hsts

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        if self.hsts:
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return response


class _BodyLimit(BaseHTTPMiddleware):
    def __init__(self, app: Any, *, max_bytes: int) -> None:
        super().__init__(app)
        self.max_bytes = max_bytes

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        length = request.headers.get("content-length")
        if length and length.isdigit() and int(length) > self.max_bytes:
            return JSONResponse({"detail": f"payload exceeds {self.max_bytes} bytes"}, status_code=413)
        return await call_next(request)


class _HashedStatic(StaticFiles):
    async def get_response(self, path: str, scope: Any) -> Response:
        response = await super().get_response(path, scope)
        if response.status_code == 200:
            response.headers["cache-control"] = "public, max-age=31536000, immutable"
        return response


def _mount_frontend(app: FastAPI) -> None:
    index, assets = FRONTEND_DIST / "index.html", FRONTEND_DIST / "assets"
    if not index.is_file():
        logger.info("frontend_disabled", reason="no dist")
        return
    if assets.is_dir():
        app.mount("/assets", _HashedStatic(directory=str(assets)), name="assets")

    def _index() -> FileResponse:
        return FileResponse(index, media_type="text/html",
                            headers={"cache-control": "no-cache", "content-security-policy": CSP})

    @app.get("/", include_in_schema=False)
    async def _root() -> FileResponse:
        return _index()

    @app.get("/{full_path:path}", include_in_schema=False, response_model=None)
    async def _spa(full_path: str):  # noqa: ANN202
        if full_path == "api" or full_path.startswith("api/"):
            return JSONResponse({"detail": "API route not found"}, status_code=404)
        candidate = (FRONTEND_DIST / full_path).resolve()
        try:
            candidate.relative_to(FRONTEND_DIST.resolve())
        except ValueError:
            return _index()
        return FileResponse(candidate) if candidate.is_file() else _index()


def create_app(settings: Settings | None = None, *, engine: PrivacyEngine | None = None,
               audit_sink: AuditSink | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, json_output=settings.env != "local")
    assert_production_posture(settings)
    gateway = get_gateway(settings)
    engine = engine or PrivacyEngine(settings, gateway=gateway)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        READINESS.set("api-core", True, "up", version=__version__)
        READINESS.set("llm-gateway", gateway.available(), "configured" if gateway.available() else "unconfigured",
                      provider=gateway.provider or "none")
        READINESS.set("ner", engine.ner is not None, "presidio" if engine.ner is not None else "patterns-only")
        READINESS.set("fpe", engine.fpe_enabled, "ff1" if engine.fpe_enabled else "det_token fallback")
        logger.info("startup", env=settings.env, auth=settings.auth_enabled, provider=gateway.provider or "none")
        yield

    app = FastAPI(title="Data Privacy Services", version=__version__, lifespan=lifespan,
                  openapi_url="/api/v1/openapi.json", docs_url="/docs")
    app.state.settings = settings
    app.state.engine = engine
    app.state.auth = {"store": StaticKeyStore.from_settings(settings), "enabled": settings.auth_enabled}
    app.state.rate_limiter = RateLimiter(settings.rate_limit_rps, settings.rate_limit_burst,
                                         enabled=settings.rate_limit_enabled)
    app.state.audit = audit_sink or LogAuditSink()

    app.add_middleware(_BodyLimit, max_bytes=settings.max_payload_bytes)
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET", "POST", "OPTIONS"],
                       allow_headers=["Content-Type", "X-API-KEY", "X-DPS-Tenant", "X-Request-ID"])
    app.add_middleware(_SecurityHeaders, hsts=settings.env != "local")

    app.include_router(ops.router)
    app.include_router(detect.router)
    app.include_router(transform.router)
    app.include_router(policies.router)
    _mount_frontend(app)
    return app
```

- [ ] **Step 6: Run tests, lint, types**

Run: `pytest -q && ruff check dps tests && mypy dps`
Expected: all green (`test_api.py`: 5 passed), clean. The lifespan runs inside `TestClient` only as a context manager; `/readyz` is read after startup because `TestClient(app)` calls the lifespan on first request in recent Starlette — if `ready` is `False`, wrap the fixture's client in `with TestClient(app) as c:` and yield it.

- [ ] **Step 7: Commit**

```bash
git add dps/layers dps/app.py tests/test_api.py
git commit -m "feat(api): /api/v1 detect, obfuscate, synthesize, reveal, policies; app factory with limits, audit, SPA mount"
```

---

### Task 25: Console scaffold — Vite + React + TypeScript, Vault tokens, Layout, Overview

**Files:**
- Create: `frontend/package.json`, `frontend/tsconfig.json`, `frontend/vite.config.ts`, `frontend/index.html`, `frontend/public/theme-init.js`, `frontend/src/main.tsx`, `frontend/src/App.tsx`, `frontend/src/styles.css`, `frontend/src/lib/types.ts`, `frontend/src/lib/api.ts`, `frontend/src/hooks/useSettings.tsx`, `frontend/src/components/Layout.tsx`, `frontend/src/components/Chip.tsx`, `frontend/src/pages/Overview.tsx`, `frontend/src/pages/Playground.tsx` (stub; replaced in Task 26)

- [ ] **Step 1: Write the tooling files**

`frontend/package.json`:
```json
{
  "name": "dps-privacy-console",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "description": "Data Privacy Services — Privacy Console (Vite + React SPA served by FastAPI from frontend/dist).",
  "scripts": {
    "dev": "vite",
    "build": "tsc --noEmit && vite build",
    "preview": "vite preview",
    "typecheck": "tsc --noEmit"
  },
  "dependencies": {
    "react": "^18.3.1",
    "react-dom": "^18.3.1",
    "react-router-dom": "^6.26.2"
  },
  "devDependencies": {
    "@types/react": "^18.3.11",
    "@types/react-dom": "^18.3.1",
    "@vitejs/plugin-react": "^4.3.2",
    "typescript": "^5.6.3",
    "vite": "^5.4.8"
  }
}
```

`frontend/tsconfig.json`:
```json
{
  "compilerOptions": {
    "target": "ES2020",
    "lib": ["ES2020", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "moduleResolution": "Bundler",
    "jsx": "react-jsx",
    "strict": true,
    "noUnusedLocals": true,
    "noUnusedParameters": true,
    "noFallthroughCasesInSwitch": true,
    "skipLibCheck": true,
    "isolatedModules": true,
    "noEmit": true
  },
  "include": ["src"]
}
```

`frontend/vite.config.ts`:
```ts
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Served by FastAPI (dps/app.py::_mount_frontend) from frontend/dist. `base: './'` keeps assets
// relative, so every route must be ONE segment deep (/playground, never /jobs/123) — see the
// sibling console's vite.config.ts for the full explanation.
export default defineConfig({
  base: './',
  plugins: [react()],
  build: { outDir: 'dist', emptyOutDir: true, sourcemap: false, target: 'es2020' },
  server: {
    port: 5174,
    proxy: {
      '/api': { target: 'http://localhost:8400', changeOrigin: true },
      '/readyz': { target: 'http://localhost:8400', changeOrigin: true },
      '/health': { target: 'http://localhost:8400', changeOrigin: true },
      '/metrics': { target: 'http://localhost:8400', changeOrigin: true },
    },
  },
});
```

`frontend/index.html`:
```html
<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <meta name="color-scheme" content="light dark" />
    <meta name="description" content="Data Privacy Services — Privacy Console: detect, obfuscate, synthesize, reveal." />
    <title>Data Privacy Services — Privacy Console</title>
    <link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'%3E%3Crect width='100' height='100' rx='22' fill='%230b0f1a'/%3E%3Cpath d='M50 18 L78 32 V58 C78 72 64 82 50 86 C36 82 22 72 22 58 V32 Z' fill='none' stroke='%232dd4bf' stroke-width='7' stroke-linejoin='round'/%3E%3Ccircle cx='50' cy='54' r='9' fill='%232dd4bf'/%3E%3C/svg%3E" />
    <script src="./theme-init.js"></script>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

`frontend/public/theme-init.js`:
```js
// Runs before first paint (same-origin, so the strict CSP stays script-src 'self').
(function () {
  var theme = 'dark';
  try { theme = localStorage.getItem('dps.theme') || (matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark'); } catch (e) {}
  document.documentElement.setAttribute('data-theme', theme);
})();
```

- [ ] **Step 2: Create the stylesheet from the Vault mockup tokens**

Run:
```bash
cp docs/design/mockups/vault.css frontend/src/styles.css
sed -i '' '/^\.mock-banner/,/^}/d' frontend/src/styles.css   # remove the mockup-only banner rules (two blocks)
sed -i '' '/^\.mock-banner a/d' frontend/src/styles.css
```
Then append to `frontend/src/styles.css`:
```css
/* --- console additions (not in the mockups) --- */
textarea.input { font-family: var(--mono); font-size: 12.5px; line-height: 1.5; min-height: 320px; resize: vertical; }
select.input { appearance: auto; }
.row { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
.spacer { flex: 1; }
.err { color: var(--rose); background: var(--rose-soft); border: 1px solid var(--rose-border); border-radius: var(--r-md); padding: 8px 12px; font-size: 12.5px; }
.finding { display: grid; grid-template-columns: 1fr auto; gap: 4px 10px; padding: 10px 0; border-bottom: 1px solid var(--border); font-size: 12.5px; }
.finding:last-child { border-bottom: 0; }
.finding .path { font-family: var(--mono); color: var(--ink-2); font-size: 11.5px; }
.finding .why { color: var(--ink-3); font-size: 11.5px; grid-column: 1 / -1; }
.keycard { background: linear-gradient(160deg, var(--surface), color-mix(in srgb, var(--teal) 10%, var(--surface))); border: 1px solid var(--teal-border); }
.kv { display: grid; grid-template-columns: 140px 1fr; gap: 6px 10px; font-size: 12.5px; }
.kv .k { color: var(--ink-3); }
.play { display: grid; grid-template-columns: 1fr 300px 1fr; gap: 16px; align-items: start; }
@media (max-width: 1200px) { .play { grid-template-columns: 1fr; } }
.skeleton { height: 14px; border-radius: 6px; background: linear-gradient(90deg, var(--surface-2), var(--surface-3), var(--surface-2)); background-size: 200% 100%; animation: shimmer 1.2s infinite; }
@keyframes shimmer { to { background-position: -200% 0; } }
.key-wrap { position: relative; display: inline-flex; align-items: center; }
.key-wrap input { padding-right: 34px; }
.key-peek { position: absolute; right: 6px; background: transparent; border: 0; color: var(--ink-3); cursor: pointer; font-size: 11px; }
.field { display: flex; flex-direction: column; gap: 2px; }
.field-label { font-size: 10.5px; letter-spacing: .08em; text-transform: uppercase; color: var(--ink-3); font-weight: 700; }
.input-inline { padding: 6px 10px; font-size: 12.5px; width: 220px; }
```

- [ ] **Step 3: Write the app shell**

`frontend/src/main.tsx`:
```tsx
import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import { App } from './App';
import { SettingsProvider } from './hooks/useSettings';
import './styles.css';

ReactDOM.createRoot(document.getElementById('root') as HTMLElement).render(
  <React.StrictMode>
    <SettingsProvider>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </SettingsProvider>
  </React.StrictMode>,
);
```

`frontend/src/App.tsx`:
```tsx
import { Route, Routes } from 'react-router-dom';
import { Layout } from './components/Layout';
import { Overview } from './pages/Overview';
import { Playground } from './pages/Playground';

function Soon({ title }: { title: string }): JSX.Element {
  return (
    <main className="page">
      <div className="card">
        <div className="card-title">{title}</div>
        <p className="muted">This screen arrives with its milestone (see docs/specs §11). The engine behind it is live in the Playground today.</p>
      </div>
    </main>
  );
}

export function App(): JSX.Element {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route path="/" element={<Overview />} />
        <Route path="/playground" element={<Playground />} />
        <Route path="/policies" element={<Soon title="Policies" />} />
        <Route path="/data-sources" element={<Soon title="Data sources & jobs (M2)" />} />
        <Route path="/scanner" element={<Soon title="UI scanner (M4)" />} />
        <Route path="/reports" element={<Soon title="Reports & audit (M5)" />} />
        <Route path="/admin" element={<Soon title="Admin (M5)" />} />
        <Route path="*" element={<Soon title="Page not found" />} />
      </Route>
    </Routes>
  );
}
```

`frontend/src/lib/types.ts`:
```ts
export type Sensitivity = 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL';

export interface Locator { path: string; start: number | null; end: number | null }
export interface Finding {
  locator: Locator; entity_type: string; score: number; detectors: string[];
  sensitivity: Sensitivity; evidence: string[]; value_hash: string | null;
}
export interface Verification { passed: boolean; checked: number; hits: { path: string; entity_type: string }[] }
export interface Report {
  mode: string; policy: string; policy_hash: string; engine_version: string; findings_total: number;
  findings_by_type: Record<string, number>; findings_by_detector: Record<string, number>; record_sensitivity: string;
  methods_used: Record<string, number>; fallbacks: Record<string, number>; reversible_values: number;
  verification: Verification | null; llm: { calls: number; raw_pii_sent: number }; duration_ms: number;
}
export interface KeyMaterial { alg: string; kid: string; dek: string | null; wrapped_dek: string | null; wrap: Record<string, string> | null; consistency_scope: string; created_at: string }
export interface PlanRow { path: string; span: [number, number] | null; entity_type: string; score: number; method: string; params: Record<string, unknown>; reason: string; reversible: boolean }
export interface DetectOut { findings: Finding[]; record_sensitivity: Sensitivity; report: Report; trace_id: string }
export interface TransformOut {
  result: unknown; findings: Finding[]; plan: PlanRow[]; key_material: KeyMaterial | null;
  reveal_hints: Record<string, unknown>[]; synthesis_seed: { seed: string } | null; report: Report; trace_id: string;
}
export interface RevealOut { result: unknown; revealed: number; report: Report; trace_id: string }
export interface Preset { name: string; description: string }
export interface Readiness { ready: boolean; components: Record<string, { ok: boolean; detail: string } & Record<string, unknown>> }
```

`frontend/src/lib/api.ts`:
```ts
import type { DetectOut, Preset, Readiness, RevealOut, TransformOut } from './types';

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

export interface Auth { apiKey: string; tenant: string }

async function call<T>(path: string, auth: Auth, init?: RequestInit): Promise<T> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  if (auth.apiKey) headers['X-API-KEY'] = auth.apiKey;
  if (auth.tenant) headers['X-DPS-Tenant'] = auth.tenant;
  const res = await fetch(path, { ...init, headers: { ...headers, ...(init?.headers as Record<string, string> | undefined) } });
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail ?? detail; } catch { /* non-JSON error body */ }
    throw new ApiError(res.status, typeof detail === 'string' ? detail : JSON.stringify(detail));
  }
  return (await res.json()) as T;
}

export type Policy = string | Record<string, unknown> | null;
export interface Body { data?: unknown; text?: string; policy?: Policy; options?: Record<string, unknown> }

export const api = {
  readyz: () => fetch('/readyz').then((r) => r.json() as Promise<Readiness>),
  metrics: () => fetch('/metrics').then((r) => r.text()),
  presets: (auth: Auth) => call<Preset[]>('/api/v1/policies/presets', auth),
  detect: (auth: Auth, body: Body) => call<DetectOut>('/api/v1/detect', auth, { method: 'POST', body: JSON.stringify(body) }),
  obfuscate: (auth: Auth, body: Body) => call<TransformOut>('/api/v1/obfuscate', auth, { method: 'POST', body: JSON.stringify(body) }),
  synthesize: (auth: Auth, body: Body) => call<TransformOut>('/api/v1/synthesize', auth, { method: 'POST', body: JSON.stringify(body) }),
  reveal: (auth: Auth, body: { data?: unknown; text?: string; key_material: unknown; reveal_hints: unknown[] }) =>
    call<RevealOut>('/api/v1/reveal', auth, { method: 'POST', body: JSON.stringify(body) }),
};

/** Sum a Prometheus counter family from the exposition text (good enough for the overview tiles). */
export function metricTotal(text: string, name: string): number {
  let total = 0;
  for (const line of text.split('\n')) {
    if (line.startsWith(name) && !line.startsWith('#')) {
      const value = Number(line.trim().split(/\s+/).pop());
      if (!Number.isNaN(value)) total += value;
    }
  }
  return total;
}
```

`frontend/src/hooks/useSettings.tsx`:
```tsx
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';

interface Settings {
  apiKey: string; setApiKey: (v: string) => void;
  tenant: string; setTenant: (v: string) => void;
  theme: 'dark' | 'light'; toggleTheme: () => void;
}

const Ctx = createContext<Settings | null>(null);

function read(key: string, fallback: string): string {
  try { return localStorage.getItem(key) ?? fallback; } catch { return fallback; }
}
function write(key: string, value: string): void {
  try { localStorage.setItem(key, value); } catch { /* private mode */ }
}

export function SettingsProvider({ children }: { children: ReactNode }): JSX.Element {
  const [apiKey, setApiKeyState] = useState(() => read('dps.apiKey', ''));
  const [tenant, setTenantState] = useState(() => read('dps.tenant', ''));
  const [theme, setTheme] = useState<'dark' | 'light'>(() => (read('dps.theme', document.documentElement.getAttribute('data-theme') || 'dark') === 'light' ? 'light' : 'dark'));

  useEffect(() => { document.documentElement.setAttribute('data-theme', theme); write('dps.theme', theme); }, [theme]);
  const setApiKey = useCallback((v: string) => { setApiKeyState(v); write('dps.apiKey', v); }, []);
  const setTenant = useCallback((v: string) => { setTenantState(v); write('dps.tenant', v); }, []);
  const toggleTheme = useCallback(() => setTheme((t) => (t === 'dark' ? 'light' : 'dark')), []);

  const value = useMemo(() => ({ apiKey, setApiKey, tenant, setTenant, theme, toggleTheme }), [apiKey, setApiKey, tenant, setTenant, theme, toggleTheme]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useSettings(): Settings {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error('useSettings outside SettingsProvider');
  return ctx;
}
```

`frontend/src/components/Chip.tsx`:
```tsx
import type { Sensitivity } from '../lib/types';

const TONE: Record<Sensitivity, string> = { CRITICAL: 'rose', HIGH: 'amber', MEDIUM: 'amber', LOW: 'slate' };

export function EntityChip({ type, sensitivity }: { type: string; sensitivity: Sensitivity }): JSX.Element {
  return <span className={`chip ${TONE[sensitivity] ?? 'slate'}`}>{type}</span>;
}

export function Tone({ tone, children }: { tone: 'teal' | 'amber' | 'emerald' | 'rose' | 'violet' | 'slate'; children: React.ReactNode }): JSX.Element {
  return <span className={`chip ${tone}`}>{children}</span>;
}
```

`frontend/src/components/Layout.tsx`:
```tsx
import { useState } from 'react';
import { NavLink, Outlet } from 'react-router-dom';
import { useSettings } from '../hooks/useSettings';

const NAV: { label: string; items: { to: string; text: string }[] }[] = [
  { label: 'Operate', items: [{ to: '/', text: 'Overview' }, { to: '/playground', text: 'Playground' }, { to: '/policies', text: 'Policies' }] },
  { label: 'Layers', items: [{ to: '/data-sources', text: 'Data sources & jobs' }, { to: '/scanner', text: 'UI scanner' }] },
  { label: 'Govern', items: [{ to: '/reports', text: 'Reports & audit' }, { to: '/admin', text: 'Admin' }] },
];

export function Layout(): JSX.Element {
  const { apiKey, setApiKey, tenant, setTenant, theme, toggleTheme } = useSettings();
  const [show, setShow] = useState(false);
  return (
    <div className="shell">
      <nav className="sidebar" aria-label="Primary">
        <div className="brand">
          <div className="brand-mark">D</div>
          <div><div className="brand-title">Data Privacy Services</div><div className="brand-sub">Privacy Console</div></div>
        </div>
        <div>
          {NAV.map((g) => (
            <div key={g.label}>
              <div className="nav-label">{g.label}</div>
              {g.items.map((i) => (
                <NavLink key={i.to} to={i.to} end={i.to === '/'} className={({ isActive }) => `nav-item${isActive ? ' active' : ''}`}>{i.text}</NavLink>
              ))}
            </div>
          ))}
        </div>
        <div className="sidebar-foot"><span>v0.1.0 · M0</span><span className="mono">/api/v1</span></div>
      </nav>
      <div className="main">
        <header className="topbar">
          <label className="field"><span className="field-label">Tenant</span>
            <input className="input input-inline" value={tenant} onChange={(e) => setTenant(e.target.value)} placeholder="default" spellCheck={false} /></label>
          <label className="field"><span className="field-label">API key</span>
            <span className="key-wrap">
              <input className="input input-inline" type={show ? 'text' : 'password'} value={apiKey} onChange={(e) => setApiKey(e.target.value)} placeholder="X-API-KEY" spellCheck={false} autoComplete="off" />
              <button type="button" className="key-peek" onClick={() => setShow((v) => !v)} aria-label={show ? 'Hide key' : 'Show key'}>{show ? 'hide' : 'show'}</button>
            </span></label>
          <span className="topbar-spacer" />
          <button type="button" className="btn btn-sm" onClick={toggleTheme}>{theme === 'dark' ? 'Light' : 'Dark'} theme</button>
        </header>
        <Outlet />
      </div>
    </div>
  );
}
```

`frontend/src/pages/Overview.tsx`:
```tsx
import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { Tone } from '../components/Chip';
import { api, metricTotal } from '../lib/api';
import type { Readiness } from '../lib/types';

export function Overview(): JSX.Element {
  const [ready, setReady] = useState<Readiness | null>(null);
  const [metrics, setMetrics] = useState<string>('');
  useEffect(() => {
    api.readyz().then(setReady).catch(() => setReady(null));
    api.metrics().then(setMetrics).catch(() => setMetrics(''));
  }, []);
  const requests = metricTotal(metrics, 'dps_requests_total');
  const findings = metricTotal(metrics, 'dps_findings_total');
  const rawToLlm = metricTotal(metrics, 'dps_llm_raw_pii_sent_total');
  return (
    <main className="page">
      <div className="page-head">
        <div><h1>Protection posture</h1><p>Milestone M0 · Layer 1 is live; connectors and the UI scanner arrive with M2–M4.</p></div>
        <Link className="btn btn-primary" to="/playground">Open playground</Link>
      </div>
      <div className="grid cols-4">
        <div className="card kpi hero"><div className="label">Requests served</div><div className="value">{requests.toLocaleString()}</div><div className="delta">since process start</div></div>
        <div className="card kpi"><div className="label">Findings</div><div className="value">{findings.toLocaleString()}</div><div className="delta">by entity type and detector</div></div>
        <div className="card kpi"><div className="label">Service</div><div className="value">{ready ? (ready.ready ? 'Ready' : 'Degraded') : '—'}</div><div className="delta">{ready ? Object.keys(ready.components).length : 0} components</div></div>
        <div className={`card kpi ${rawToLlm === 0 ? 'zero' : ''}`}><div className="label">Raw PII sent to LLM</div><div className="value">{rawToLlm}</div><div className="delta">placeholders only · must stay 0</div></div>
      </div>
      <div className="card">
        <div className="card-head"><div className="card-title">Readiness</div>{ready && <Tone tone={ready.ready ? 'emerald' : 'rose'}>{ready.ready ? 'all required up' : 'degraded'}</Tone>}</div>
        <table className="tbl"><thead><tr><th>Component</th><th>State</th><th>Detail</th></tr></thead>
          <tbody>{ready ? Object.entries(ready.components).map(([name, c]) => (
            <tr key={name}><td className="mono">{name}</td><td><Tone tone={c.ok ? 'emerald' : 'amber'}>{c.ok ? 'ok' : 'off'}</Tone></td><td className="muted">{c.detail}</td></tr>
          )) : <tr><td colSpan={3}><div className="skeleton" /></td></tr>}</tbody></table>
      </div>
    </main>
  );
}
```

`frontend/src/pages/Playground.tsx` (temporary stub, replaced in Task 26):
```tsx
export function Playground(): JSX.Element {
  return <main className="page"><div className="card"><div className="card-title">Playground</div></div></main>;
}
```

- [ ] **Step 4: Install and build**

Run:
```bash
cd frontend && npm install --no-audit --no-fund && npm run build && cd ..
```
Expected: `tsc` passes, Vite writes `frontend/dist/index.html` and `frontend/dist/assets/*`. (`frontend/dist` and `node_modules` are git-ignored; `package-lock.json` is committed.)

- [ ] **Step 5: Commit**

```bash
git add frontend/package.json frontend/package-lock.json frontend/tsconfig.json frontend/vite.config.ts frontend/index.html frontend/public frontend/src
git commit -m "feat(console): Vite + React shell with Vault design tokens, settings, overview"
```

---

### Task 26: Playground page — the Layer 1 demo

**Files:**
- Modify: `frontend/src/pages/Playground.tsx` (replace the stub)

- [ ] **Step 1: Write the page**

```tsx
import { useEffect, useMemo, useState } from 'react';
import { EntityChip, Tone } from '../components/Chip';
import { useSettings } from '../hooks/useSettings';
import { api, ApiError, type Policy } from '../lib/api';
import type { DetectOut, Finding, Preset, Report, TransformOut } from '../lib/types';

const SAMPLE_JSON = JSON.stringify({
  customer_id: 'CUST-884120', full_name: 'Ananya Krishnan', dob: '1988-03-14', aadhaar: '2341 2341 2346',
  pan: 'ACUPA7085R', email: 'ananya.k@example-mail.in', phone: '+91 98451 20377',
  card: { pan: '4111 1111 1111 1111', expiry: '09/28' }, address: '14 Lavelle Road, Bengaluru 560001',
  risk_score: 712, notes: 'Called on 2026-03-14 about card ending 1111; mail ananya.k@example-mail.in.',
}, null, 2);
const SAMPLE_TEXT = 'Ananya Krishnan (ananya.k@example-mail.in, +91 98451 20377) asked to update her Aadhaar 2341 2341 2346 before 2026-03-14.';

type Mode = 'detect' | 'obfuscate' | 'synthesize';
type Kind = 'json' | 'text';

function pretty(v: unknown): string { return typeof v === 'string' ? v : JSON.stringify(v, null, 2); }

function ReportLine({ r }: { r: Report }): JSX.Element {
  return (
    <div className="kv" style={{ marginTop: 10 }}>
      <span className="k">findings</span><span>{r.findings_total} · {Object.entries(r.findings_by_type).map(([k, v]) => `${k} ${v}`).join(' · ')}</span>
      <span className="k">methods</span><span>{Object.entries(r.methods_used).map(([k, v]) => `${k} ×${v}`).join(' · ') || '—'}</span>
      {Object.keys(r.fallbacks).length > 0 && <><span className="k">fallbacks</span><span>{Object.entries(r.fallbacks).map(([k, v]) => `${k} ×${v}`).join(' · ')}</span></>}
      <span className="k">verification</span><span>{r.verification ? (r.verification.passed ? <Tone tone="emerald">leakage sweep passed · 0 originals in output</Tone> : <Tone tone="rose">failed</Tone>) : '—'}</span>
      <span className="k">LLM</span><span>{r.llm.calls} calls · raw PII sent: <b style={{ color: 'var(--emerald)' }}>{r.llm.raw_pii_sent}</b></span>
      <span className="k">timing</span><span>{r.duration_ms} ms · policy {r.policy} · {r.policy_hash.slice(0, 12)}</span>
    </div>
  );
}

function Findings({ items }: { items: Finding[] }): JSX.Element {
  if (items.length === 0) return <p className="muted">No sensitive values found above the policy threshold.</p>;
  return (
    <>
      {items.map((f, i) => (
        <div className="finding" key={i}>
          <EntityChip type={f.entity_type} sensitivity={f.sensitivity} />
          <span className="mono muted">{f.score.toFixed(2)}</span>
          <span className="path">{f.locator.path}{f.locator.start !== null ? `[${f.locator.start}:${f.locator.end}]` : ''}</span>
          <span className="why">{f.evidence.join(' · ')}</span>
        </div>
      ))}
    </>
  );
}

export function Playground(): JSX.Element {
  const { apiKey, tenant } = useSettings();
  const auth = useMemo(() => ({ apiKey, tenant }), [apiKey, tenant]);
  const [kind, setKind] = useState<Kind>('json');
  const [input, setInput] = useState(SAMPLE_JSON);
  const [presets, setPresets] = useState<Preset[]>([]);
  const [policy, setPolicy] = useState<string>('gdpr-pseudonymise');
  const [busy, setBusy] = useState<Mode | 'reveal' | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [detect, setDetect] = useState<DetectOut | null>(null);
  const [out, setOut] = useState<{ mode: Mode; res: TransformOut } | null>(null);
  const [revealed, setRevealed] = useState<unknown>(null);

  useEffect(() => { api.presets(auth).then(setPresets).catch(() => setPresets([])); }, [auth]);
  useEffect(() => { setInput(kind === 'json' ? SAMPLE_JSON : SAMPLE_TEXT); setOut(null); setDetect(null); setRevealed(null); }, [kind]);

  function body(): { data?: unknown; text?: string; policy: Policy } {
    if (kind === 'text') return { text: input, policy };
    return { data: JSON.parse(input) as unknown, policy };
  }

  async function run(mode: Mode): Promise<void> {
    setBusy(mode); setError(null); setRevealed(null);
    try {
      const b = body();
      if (mode === 'detect') { setDetect(await api.detect(auth, b)); setOut(null); }
      else {
        const res = mode === 'obfuscate' ? await api.obfuscate(auth, b) : await api.synthesize(auth, b);
        setOut({ mode, res });
        setDetect({ findings: res.findings, record_sensitivity: res.report.record_sensitivity as DetectOut['record_sensitivity'], report: res.report, trace_id: res.trace_id });
      }
    } catch (e) {
      setError(e instanceof ApiError ? `${e.status}: ${e.message}` : e instanceof SyntaxError ? `Input is not valid JSON: ${e.message}` : String(e));
    } finally { setBusy(null); }
  }

  async function doReveal(): Promise<void> {
    if (!out || !out.res.key_material) return;
    setBusy('reveal'); setError(null);
    try {
      const payload = kind === 'text' ? { text: out.res.result as string } : { data: out.res.result };
      const r = await api.reveal(auth, { ...payload, key_material: out.res.key_material, reveal_hints: out.res.reveal_hints });
      setRevealed(r.result);
    } catch (e) { setError(e instanceof ApiError ? `${e.status}: ${e.message}` : String(e)); }
    finally { setBusy(null); }
  }

  const findings = detect?.findings ?? [];
  return (
    <main className="page">
      <div className="page-head">
        <div><h1>Try the engine</h1><p>Paste JSON or text. Detection and transformation run locally; the LLM only ever sees placeholders.</p></div>
        <div className="row"><span className="muted" style={{ fontSize: 12.5 }}>Policy</span>
          <select className="input" style={{ width: 240 }} value={policy} onChange={(e) => setPolicy(e.target.value)}>
            {(presets.length ? presets : [{ name: policy, description: '' }]).map((p) => <option key={p.name} value={p.name}>{p.name}</option>)}
          </select></div>
      </div>
      {error && <div className="err">{error}</div>}
      <div className="play">
        <div className="card">
          <div className="card-head"><div className="card-title">Input</div>
            <div className="seg"><button className={kind === 'json' ? 'on' : ''} onClick={() => setKind('json')}>JSON</button><button className={kind === 'text' ? 'on' : ''} onClick={() => setKind('text')}>Text</button></div></div>
          <textarea className="input" value={input} onChange={(e) => setInput(e.target.value)} spellCheck={false} />
          <div className="row" style={{ marginTop: 12 }}>
            <span className="faint" style={{ fontSize: 12 }}>{input.length.toLocaleString()} chars</span><span className="spacer" />
            <button className="btn" disabled={busy !== null} onClick={() => run('detect')}>Detect only</button>
            <button className="btn btn-primary" disabled={busy !== null} onClick={() => run('obfuscate')}>{busy === 'obfuscate' ? 'Working…' : 'Obfuscate'}</button>
            <button className="btn" disabled={busy !== null} onClick={() => run('synthesize')}>{busy === 'synthesize' ? 'Working…' : 'Synthesize'}</button>
          </div>
        </div>
        <div className="card">
          <div className="card-head"><div className="card-title">Findings <span className="faint">· {findings.length}</span></div>
            {detect && <EntityChip type={detect.record_sensitivity} sensitivity={detect.record_sensitivity} />}</div>
          {detect ? <Findings items={findings} /> : <p className="muted">Run Detect, Obfuscate or Synthesize to see what the engine found and why.</p>}
        </div>
        <div className="card">
          <div className="card-head">
            <div className="card-title">Output{out && <> · {out.mode === 'obfuscate' ? <Tone tone="teal">reversible</Tone> : <Tone tone="violet">synthetic · no path back</Tone>}</>}</div>
            {out && out.mode === 'obfuscate' && out.res.key_material && (
              <button className="btn btn-sm btn-primary" disabled={busy !== null} onClick={doReveal}>{busy === 'reveal' ? 'Revealing…' : 'Reveal with key'}</button>)}
          </div>
          {out ? (
            <>
              <pre className="pane" style={{ minHeight: 260 }}>{pretty(revealed ?? out.res.result)}</pre>
              {revealed !== null && <div className="callout" style={{ marginTop: 10, fontSize: 12 }}>Original restored via <span className="mono">POST /api/v1/reveal</span> with the key you hold. DPS kept no copy.</div>}
              {out.res.key_material && (
                <div className="card keycard" style={{ marginTop: 12, padding: '12px 14px' }}>
                  <div className="card-head" style={{ marginBottom: 6 }}><div className="card-title">Key material · returned once, never stored</div><Tone tone="teal">{out.res.key_material.alg}</Tone></div>
                  <div className="mono" style={{ fontSize: 12, wordBreak: 'break-all' }}>kid {out.res.key_material.kid} · dek {out.res.key_material.dek ? `${out.res.key_material.dek.slice(0, 6)}…${out.res.key_material.dek.slice(-4)}` : 'wrapped'} · scope {out.res.key_material.consistency_scope}</div>
                  <div className="row" style={{ marginTop: 8 }}>
                    <button className="btn btn-sm" onClick={() => navigator.clipboard?.writeText(JSON.stringify(out.res.key_material))}>Copy key material</button>
                    <button className="btn btn-sm" onClick={() => navigator.clipboard?.writeText(JSON.stringify(out.res.reveal_hints))}>Copy reveal hints</button>
                  </div>
                </div>)}
              {out.res.synthesis_seed && <div className="callout amber" style={{ marginTop: 12, fontSize: 12 }}>Synthesis seed echoed for cross-call consistency. It is not a reveal key — generation is one-way.</div>}
              <ReportLine r={out.res.report} />
            </>
          ) : detect ? <ReportLine r={detect.report} /> : <p className="muted">The transformed document, the plan and the evidence appear here.</p>}
        </div>
      </div>
    </main>
  );
}
```

- [ ] **Step 2: Typecheck and build**

Run: `cd frontend && npm run build && cd ..`
Expected: clean `tsc`, Vite build succeeds.

- [ ] **Step 3: Verify in the browser against the live API**

Run the API in one terminal: `DPS_AUTH_ENABLED=false DPS_LLM_PROVIDER=stub DPS_FPE_ENABLED=false uvicorn dps.app:create_app --factory --port 8400`
and the console in another: `cd frontend && npm run dev` (port 5174, proxied to 8400). In the browser: Overview shows readiness and the zero tile; Playground → Obfuscate shows tokens, Reveal restores the sample, Synthesize shows a coherent fake record, Text mode round-trips. Capture a screenshot of the Playground for the handoff.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/pages/Playground.tsx
git commit -m "feat(console): Playground — detect, obfuscate, synthesize, reveal against the live API"
```

---

### Task 27: Container image, local stack, smoke test

**Files:**
- Create: `Dockerfile`, `.dockerignore`, `docker/entrypoint.sh`, `docker-compose.yml`, `tools/smoke_test.py`

- [ ] **Step 1: Write the container files**

`.dockerignore`:
```
.git
.venv
**/__pycache__
**/node_modules
frontend/dist
docs
tests
.pytest_cache
.mypy_cache
.ruff_cache
.env
.env.*
```

`docker/entrypoint.sh`:
```bash
#!/bin/sh
# M0: nothing to prepare (no database). Later milestones add `python -m dps.migrate` here under
# MIGRATIONS_MODE=auto, exactly like the sibling services.
set -e
exec "$@"
```

`Dockerfile`:
```dockerfile
# Data Privacy Services — application image (multi-stage).
# Stage 1 compiles the console; stage 2 is the lean Python runtime with only the built dist.

# ---------------------------------------------------------------- stage 1: console
FROM node:20-slim AS frontend
WORKDIR /ui
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci --no-audit --no-fund 2>/dev/null || npm install --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---------------------------------------------------------------- stage 2: runtime
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml README.md ./
COPY dps ./dps
# [fpe] is MIT-licensed fastfpe; FF1 is still gated at runtime by DPS_FPE_ENABLED (spec D8).
# The [ner] extra (Presidio + spaCy models) is deliberately not baked in: pass --build-arg WITH_NER=1.
ARG WITH_NER=0
RUN if [ "$WITH_NER" = "1" ]; then uv pip install --system -e ".[fpe,ner]" && python -m spacy download en_core_web_sm; \
    else uv pip install --system -e ".[fpe]"; fi
COPY --from=frontend /ui/dist ./frontend/dist
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh \
    && useradd --create-home --uid 10001 appuser
USER appuser
EXPOSE 8400
HEALTHCHECK --interval=15s --timeout=5s --start-period=15s --retries=5 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8400/health', timeout=3).status==200 else 1)"
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["uvicorn", "dps.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8400"]
```

`docker-compose.yml`:
```yaml
# Local stack for M0: the app alone (Layer 1 is stateless). M2 adds Postgres (job metadata),
# a worker, and source databases; M3 adds Oracle Free and MongoDB; M4 adds the browser worker.
#
#   docker compose up --build          # console + API on http://localhost:8400
#   python tools/smoke_test.py         # end-to-end checks against it
services:
  app:
    build: .
    image: data-privacy-services:local
    ports:
      - "${DPS_APP_PORT:-8400}:8400"
    environment:
      DPS_ENV: local
      DPS_LOG_LEVEL: INFO
      DPS_AUTH_ENABLED: "true"
      DPS_BOOTSTRAP_API_KEY: ${DPS_BOOTSTRAP_API_KEY:-dps_local_dev_key_change_me}
      DPS_DEFAULT_POLICY: gdpr-pseudonymise
      DPS_DEFAULT_LOCALE: ${DPS_DEFAULT_LOCALE:-en_IN}
      DPS_FPE_ENABLED: "true"          # the image ships fastfpe; flip to false to demo the det_token fallback
      DPS_LLM_PROVIDER: ${DPS_LLM_PROVIDER:-stub}
      DPS_LLM_MODEL: ${DPS_LLM_MODEL:-gemini-2.5-flash}
      DPS_LLM_TEXT_REWRITE: ${DPS_LLM_TEXT_REWRITE:-false}
      # Stellar (VDI only) — leave empty elsewhere; the provider then reports unavailable.
      DPS_STELLAR_BASE_URL: ${DPS_STELLAR_BASE_URL:-}
      DPS_COIN_URL: ${DPS_COIN_URL:-}
      DPS_COIN_CLIENT_ID: ${DPS_COIN_CLIENT_ID:-}
      DPS_COIN_CLIENT_SECRET: ${DPS_COIN_CLIENT_SECRET:-}
      DPS_COIN_SCOPE: ${DPS_COIN_SCOPE:-}
      DPS_SSL_CERT_FILE: ${DPS_SSL_CERT_FILE:-}
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8400/health', timeout=3).status==200 else 1)"]
      interval: 15s
      timeout: 5s
      retries: 5
    labels:
      app: data-privacy-services
      component: api
      env: local
```

- [ ] **Step 2: Write `tools/smoke_test.py`**

```python
"""End-to-end smoke test against a running DPS (default http://localhost:8400).

    DPS_BASE_URL=http://localhost:8400 DPS_API_KEY=dps_local_dev_key_change_me python tools/smoke_test.py

Exits non-zero on the first failed check and prints a one-line verdict per check. Never prints
request or response bodies that could carry values.
"""
from __future__ import annotations

import os
import sys

import httpx

BASE = os.environ.get("DPS_BASE_URL", "http://localhost:8400").rstrip("/")
KEY = os.environ.get("DPS_API_KEY", "dps_local_dev_key_change_me")
H = {"X-API-KEY": KEY}
SAMPLE = {"customer_id": "CUST-884120", "full_name": "Ananya Krishnan", "dob": "1988-03-14",
          "aadhaar": "2341 2341 2346", "email": "ananya.k@example-mail.in", "phone": "+91 98451 20377",
          "risk_score": 712, "notes": "Mail ann.k@example-mail.in about 1988-03-14"}

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}{(' — ' + detail) if detail else ''}")
    if not ok:
        print(f"\n{sum(1 for _, o, _ in results if o)}/{len(results)} passed; stopping at first failure")
        sys.exit(1)


def main() -> None:
    with httpx.Client(base_url=BASE, timeout=30.0) as c:
        check("health", c.get("/health").json().get("status") == "ok")
        ready = c.get("/readyz")
        check("readyz", ready.status_code == 200 and ready.json()["ready"] is True,
              ", ".join(f"{k}={'ok' if v['ok'] else 'off'}" for k, v in ready.json()["components"].items()))
        check("metrics zero counter", b"dps_llm_raw_pii_sent_total 0.0" in c.get("/metrics").content)
        check("auth required", c.post("/api/v1/detect", json={"data": SAMPLE}).status_code == 401)
        det = c.post("/api/v1/detect", json={"data": SAMPLE}, headers=H)
        check("detect", det.status_code == 200 and det.json()["record_sensitivity"] == "CRITICAL",
              f"{det.json().get('report', {}).get('findings_total')} findings")
        obf = c.post("/api/v1/obfuscate", json={"data": SAMPLE, "policy": "gdpr-pseudonymise"}, headers=H)
        body = obf.json() if obf.status_code == 200 else {}
        check("obfuscate", obf.status_code == 200 and body["report"]["verification"]["passed"]
              and body["result"]["email"] != SAMPLE["email"] and body["result"]["risk_score"] == 712,
              f"methods={body.get('report', {}).get('methods_used')} fallbacks={body.get('report', {}).get('fallbacks')}")
        rev = c.post("/api/v1/reveal", json={"data": body["result"], "key_material": body["key_material"],
                                             "reveal_hints": body["reveal_hints"]}, headers=H)
        check("reveal restores original", rev.status_code == 200 and rev.json()["result"] == SAMPLE)
        again = c.post("/api/v1/obfuscate", headers=H, json={"data": SAMPLE, "options": {
            "consistency": {"scope": "key", "key_material": body["key_material"]}}}).json()
        check("consistency under the same key", again["result"]["aadhaar"] == body["result"]["aadhaar"])
        syn = c.post("/api/v1/synthesize", json={"data": SAMPLE, "policy": "synthetic-realistic"}, headers=H)
        sbody = syn.json() if syn.status_code == 200 else {}
        check("synthesize", syn.status_code == 200 and sbody["report"]["verification"]["passed"]
              and "ananya" not in str(sbody["result"]).lower() and sbody["report"]["llm"]["raw_pii_sent"] == 0)
        txt = c.post("/api/v1/synthesize", json={"text": SAMPLE["notes"]}, headers=H)
        check("synthesize text", txt.status_code == 200 and "example-mail" not in txt.json()["result"])
        presets = c.get("/api/v1/policies/presets", headers=H).json()
        check("presets", {p["name"] for p in presets} >= {"gdpr-pseudonymise", "pci-dss", "synthetic-realistic"})
        plan = c.post("/api/v1/policies/plan", json={"data": SAMPLE, "policy": "pci-dss", "mode": "obfuscate"}, headers=H)
        check("plan preview", plan.status_code == 200 and len(plan.json()["plan"]) >= 5)
        check("console served", c.get("/").status_code == 200 and b"<div id=\"root\">" in c.get("/").content)
    print(f"\nall {len(results)} checks passed against {BASE}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Build and run the stack, then smoke it**

Run:
```bash
docker compose up --build -d && sleep 20 && python tools/smoke_test.py
```
Expected: image builds (console compiled inside), `all 13 checks passed against http://localhost:8400`. Also open `http://localhost:8400` and paste the bootstrap key into the console header; the Playground round trip works with FPE on (format-preserving tokens). Tear down with `docker compose down`.

- [ ] **Step 4: Commit**

```bash
git add Dockerfile .dockerignore docker docker-compose.yml tools/smoke_test.py
git commit -m "feat: container image with compiled console, local compose stack, end-to-end smoke test"
```

---

### Task 28: Documentation, handoff, tag

**Files:**
- Modify: `README.md`
- Create: `HANDOFF.md`, `docs/technical/api-m0.md`

- [ ] **Step 1: Rewrite `README.md`**

```markdown
# Data Privacy Services (DPS)

Data obfuscation and synthetic data generation for the enterprise — one Privacy Engine, three entry
points (API, database connectors, UI scanner), two outcomes (reversible obfuscation, synthetic data),
measurable privacy, and a hard rule that **no LLM ever sees raw PII**.

**Status:** M0 shipped — Layer 1 API (`/api/v1/detect`, `/obfuscate`, `/synthesize`, `/reveal`, policy
tooling) and the Privacy Console Playground, offline, in one container. Design:
[`docs/specs/2026-10-11-data-privacy-services-design.md`](docs/specs/2026-10-11-data-privacy-services-design.md).
Build and deploy facts: [`HANDOFF.md`](HANDOFF.md). API: [`docs/technical/api-m0.md`](docs/technical/api-m0.md).

## Run it

```bash
docker compose up --build            # console + API on http://localhost:8400 (key: dps_local_dev_key_change_me)
python tools/smoke_test.py           # 13 end-to-end checks
```

## Develop

```bash
uv venv --python 3.12 .venv && source .venv/bin/activate && uv pip install -e ".[dev,fpe]"
pytest -q && ruff check dps tests && mypy dps
DPS_AUTH_ENABLED=false DPS_LLM_PROVIDER=stub uvicorn dps.app:create_app --factory --port 8400
cd frontend && npm install && npm run dev        # console on :5174, proxied to :8400
```

Optional extras: `ner` (Presidio + spaCy, higher recall on names and places), `fpe` (FF1 format-preserving
encryption, gated by `DPS_FPE_ENABLED`), `vertex` (laptop path to Gemini). Production goes through the
Stellar gateway — see `HANDOFF.md`.

## How it works

detect (structural + patterns/checksums + optional NER + client declarations) → plan (policy → one method
per finding, with reasons) → transform (AES-SIV tokens, FF1, keyed date shift, surrogates…) → verify
(no original value survives) → report. Keys are returned to the caller and never stored. Synthetic text
goes through the LLM as typed placeholders only.
```

- [ ] **Step 2: Write `HANDOFF.md`**

```markdown
# HANDOFF — data-privacy-services (DPS)

Last verified: <date of your run> at <commit> · gates: `pytest -q`, `ruff check dps tests`, `mypy dps`,
`cd frontend && npm run build`, `docker compose up --build` + `python tools/smoke_test.py`.

**Update rule (the Wega rule):** update this file in the same change as anything that alters build or
deploy facts — image, ports, env, extras, probes, labels.

## 1. What this repo ships

| Artefact | Kind | Notes |
|---|---|---|
| `data-privacy-services` image | container | FastAPI app + compiled console; one process, port **8400**; non-root uid 10001 |
| `dps` package | Python 3.12 | Layer 1 engine + API; extras `fpe` (baked in), `ner` (build-arg `WITH_NER=1`), `vertex` |
| `frontend/dist` | built inside the image | never committed |

## 2. Build

`docker build -t <registry>/data-privacy-services:<semver> .` (amd64 on a Mac: `--platform linux/amd64`).
Tag semver + git sha. No database, no volumes in M0.

## 3. Configuration (all `DPS_*`; `_FILE` suffix reads a mounted file)

| Variable | Default | Notes |
|---|---|---|
| `DPS_ENV` | `local` | `staging`/`prod` enable the posture guard |
| `DPS_AUTH_ENABLED` | `true` | never false outside a laptop |
| `DPS_API_KEYS` | — | JSON list of `{name, key|key_sha256, tenant_ids, scopes}`; prefer `DPS_API_KEYS_FILE` |
| `DPS_BOOTSTRAP_API_KEY` | — | local demos only; refused in production |
| `DPS_DEFAULT_POLICY` / `DPS_DEFAULT_LOCALE` | `gdpr-pseudonymise` / `en_US` | |
| `DPS_FPE_ENABLED` | `false` | FF1 methods fall back to `det_token` until set; flip only after the license review (spec D8) |
| `DPS_NER_ENABLED` | auto | `true` requires the `ner` extra and a spaCy model |
| `DPS_LLM_PROVIDER` | `""` | `stellar` (prod, VDI), `vertex` (laptop), `stub` (tests/demo; refused in prod) |
| `DPS_STELLAR_BASE_URL`, `DPS_COIN_URL`, `DPS_COIN_CLIENT_ID/SECRET/SCOPE` (base64), `DPS_SSL_CERT_FILE` | — | the org's COIN OAuth2 convention; secrets by name only |
| `DPS_LLM_TEXT_REWRITE` | `false` | global switch for the placeholder rewrite |
| `DPS_MAX_PAYLOAD_MB` / `DPS_MAX_TEXT_CHARS` / `DPS_MAX_ITEMS` | 8 / 1,000,000 / 10,000 | 413 beyond |
| `DPS_RATE_LIMIT_RPS` / `_BURST` | 50 / 100 | per key, per process |

## 4. Probes and ops surfaces

`GET /health` (liveness), `GET /readyz` (components: `api-core`, `llm-gateway`, `ner`, `fpe`; 503 when a
required one is down — only `api-core` is required in M0), `GET /metrics` (Prometheus; watch
`dps_llm_raw_pii_sent_total`, which must stay 0, and `dps_verification_failures_total`).

## 5. Secrets (names only)

`DPS_API_KEYS`, `DPS_COIN_CLIENT_ID`, `DPS_COIN_CLIENT_SECRET`, `DPS_COIN_SCOPE`. Nothing else. DPS never
stores customer data or reveal keys.

## 6. Traps

- `uvicorn dps.app:create_app --factory` — there is no module-level `app`.
- Routes in the console are one segment deep (`base: './'`).
- `fpe_*` silently degrade to `det_token` when `DPS_FPE_ENABLED=false`; the plan says `fallback:fpe_disabled`.
- The COIN token mint runs with TLS verification off on purpose (org handoff); API calls verify against `DPS_SSL_CERT_FILE`.
- Presidio is recall-only; identifiers with checksums come from the pattern tier regardless.

## 7. Next milestones

M1 documents → M2 jobs + PostgreSQL (adds Postgres, worker, migrations) → M3 Oracle + MongoDB → M4 UI
scanner (browser worker image) → M5 hardening + showcase. Each adds a section here.
```

- [ ] **Step 3: Write `docs/technical/api-m0.md`**

```markdown
# Layer 1 API (M0)

Base path `/api/v1`. Auth: `X-API-KEY`; optional `X-DPS-Tenant` when a key is granted several tenants;
optional `X-Request-ID` echoed as `trace_id`. Errors: `{"detail": "..."}` with 400 (bad policy/key
material), 401/403 (auth), 413 (limits), 422 (invalid body, or the leakage sweep failed under a strict
policy), 429 (rate limit).

| Method | Path | Scope | Purpose |
|---|---|---|---|
| POST | `/detect` | detect | findings + record sensitivity, no transformation |
| POST | `/obfuscate` | obfuscate | reversible/irreversible per policy; returns `key_material`, `reveal_hints` |
| POST | `/synthesize` | synthesize | surrogates; `synthesis_seed` echoed unless consistency scope is `request` |
| POST | `/reveal` | reveal | de-obfuscate with the caller's key material (+ hints for natural-style tokens) |
| GET | `/policies/presets` | detect | the six presets |
| POST | `/policies/validate` | detect | resolve a preset/inline policy, return the effective policy |
| POST | `/policies/plan` | detect | the plan for a sample without transforming |

## Request body (detect / obfuscate / synthesize)

```json
{
  "data": { "...": "JSON document" },          "text": "or a text — exactly one of the two",
  "policy": "gdpr-pseudonymise",                 "// or": { "preset": "gdpr-pseudonymise", "treat": { "EMAIL": { "method": "redact" } } },
  "options": {
    "locale": "en_IN",
    "token_style": "natural | envelope",
    "wrap_key": { "type": "rsa-oaep-256", "public_key_pem": "-----BEGIN PUBLIC KEY-----..." },
    "consistency": { "scope": "request | key | entity", "key_material": { "...": "from a previous response" },
                     "synthesis_seed": { "...": "from a previous response" } },
    "llm_rewrite": false
  }
}
```

## Response (obfuscate)

`result`, `findings[]` (locator, entity_type, score, detectors, sensitivity, evidence, value_hash),
`plan[]` (path, span, entity_type, method, params, reason, reversible), `key_material`
(`alg`, `kid`, `dek` or `wrapped_dek` + `wrap`, `consistency_scope`, `created_at`), `reveal_hints[]`,
`report` (counts, methods, fallbacks, verification, llm, timing, policy hash), `trace_id`.

## Reveal

```json
{ "data": { "...": "the obfuscated document" }, "key_material": { "...": "as returned" }, "reveal_hints": [ "...as returned" ] }
```
Envelope-style text (`[[PII:EMAIL:...]]`) reveals without hints; natural-style tokens need them.

## curl

```bash
curl -s localhost:8400/api/v1/obfuscate -H 'X-API-KEY: dps_local_dev_key_change_me' -H 'Content-Type: application/json' \
  -d '{"data":{"ssn":"123-45-6789","email":"ann@example.com"},"policy":"gdpr-pseudonymise"}' | jq .
```
```

- [ ] **Step 4: Final gates, commit, tag**

Run: `pytest -q && ruff check dps tests && mypy dps && (cd frontend && npm run build) && python tools/smoke_test.py`
(smoke test against the compose stack from Task 27, still running or restarted with `docker compose up -d`).
Expected: all green; fill the "Last verified" line in `HANDOFF.md` with the date and commit.

```bash
git add README.md HANDOFF.md docs/technical/api-m0.md
git commit -m "docs: README, HANDOFF and Layer 1 API reference for M0"
git tag -a v0.1.0-m0 -m "M0: Privacy Engine core, Layer 1 API, Playground"
```

---

## Plan self-review (done while writing; re-check before executing)

- **Spec coverage, M0 scope (§11):** scaffold mirroring the siblings (T1–T3), gateway client (T20), taxonomy + detectors T0–T2 + custom T4 (T4–T10), policy model + presets (T11), crypto core + reversible and irreversible methods (T12–T15), surrogate synthesis (T19, T21), planner/apply/verify/report (T16–T18), L1 `detect`/`obfuscate`/`synthesize`/`reveal` + policy tooling (T22–T24), console shell + Playground (T25–T26), compose stack + smoke (T27), docs/handoff (T28). Not in M0 by design: documents and redacted PDF (M1), `external_tokenize` adapter and DB-backed keys/audit (M2/M5), Vertex live test (needs credentials; the provider code is covered by `available()` only).
- **Interfaces used consistently:** `Hit(entity_type, start, end, score, detector, evidence, validated)`; `Finding/Locator/Plan/PlanEntry`; `Policy.method_for(entity, mode)` and `METHOD_NAMES`; `TransformContext`, `Outcome`, `run_method`, `reverse_method`; `ApplyOptions`, `apply_plan`, `reveal`, `RevealHint`, `anchor_for`; `SurrogateEngine.surrogate(entity_type, original, locale, anchor)`; `LlmGateway.complete(messages, temperature, max_tokens, purpose)`; `PrivacyEngine.detect/plan/obfuscate/synthesize/reveal`; `require_scope`, `enforce_rate_limit`, `AuditEvent`.
- **Known soft spots to watch during execution:** (1) `run_engine` reads `locals().get("result")` in its `finally` for the audit report — if a linter objects, hoist `result: Any = None` above the `try`; (2) stdnum sample numbers in `test_checksums.py` follow stdnum's own docstrings — swap a sample, never the validator, if a version differs; (3) Presidio and fastfpe tests skip when the extras are absent; run once with `uv pip install -e ".[dev,fpe,ner]"` and `python -m spacy download en_core_web_sm` before tagging; (4) Faker locale `en_IN` gendered names: if `first_name_female` yields non-Indian names, add `fake.first_name()` as the fallback for that locale and keep the determinism test.

## Execution

Plan complete and saved to `docs/superpowers/plans/2026-10-11-m0-foundation-engine-api-playground.md`. Two execution options:

1. **Subagent-driven (recommended)** — a fresh subagent per task, review between tasks, fast iteration (`superpowers:subagent-driven-development`).
2. **Inline execution** — tasks executed in this session with checkpoints (`superpowers:executing-plans`).
