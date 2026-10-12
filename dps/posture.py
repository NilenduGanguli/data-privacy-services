"""Production posture guard — refuse to boot with an unsafe configuration.

Called before the FastAPI app is constructed (see ``dps/app.py``) so a bad staging/prod
configuration fails the process instead of serving traffic.
"""

from __future__ import annotations

import os
from urllib.parse import urlparse

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
    # SEC-1: the COIN mint verifies TLS unless DPS_COIN_TLS_INSECURE opts out; in staging/prod
    # that opt-out is the documented VDI exception and must be acknowledged explicitly.
    if settings.coin_tls_insecure and not settings.allow_insecure_coin_mint:
        problems.append(
            "DPS_COIN_TLS_INSECURE=true requires DPS_ALLOW_INSECURE_COIN_MINT=true in staging/prod "
            "(the documented VDI exception)"
        )
    # Spec §10: TLS-only egress. COIN receives the client id/secret and Stellar the bearer token,
    # so a plain-http endpoint would put credentials on the wire in cleartext. Checked whenever
    # the URL is set, whatever the provider. The message names the setting, never the URL
    # (it may carry userinfo credentials).
    for env_name, url in (
        ("DPS_STELLAR_BASE_URL", settings.stellar_base_url),
        ("DPS_COIN_URL", settings.coin_url),
    ):
        if url and urlparse(url).scheme != "https":
            problems.append(f"{env_name} must be https in staging/prod")
    if not settings.audit_enabled:
        problems.append("DPS_AUDIT_ENABLED must be true")
    # M4 (contract §10): an unmasked original screenshot holds the page's personal data, and a
    # headed browser needs a display server the production images never have.
    if settings.scan_store_unmasked_originals:
        problems.append("DPS_SCAN_STORE_UNMASKED_ORIGINALS must be false")
    if not settings.browser_headless:
        problems.append("DPS_BROWSER_HEADLESS must be true")
    # M1 (contract §10): the OCR key travels in a header and the pages are customer documents,
    # so azure OCR needs a key and TLS. The message names the setting, never the URL.
    # ``auto`` resolving to azure sends the key and the pages just the same.
    engine = settings.ocr_engine
    if settings.ocr_provider == "azure" or engine == "azure":
        if not settings.azure_vision_key:
            problems.append(
                "DPS_AZURE_VISION_KEY (or DPS_AZURE_VISION_KEY_FILE) is required for azure OCR"
            )
        if urlparse(settings.azure_vision_endpoint).scheme != "https":
            problems.append("DPS_AZURE_VISION_ENDPOINT must be an https URL")
    # Local Tesseract (provider tesseract, or auto without Azure) has no egress and is allowed;
    # auto that resolves to nothing would silently leave scanned pages unread.
    if settings.ocr_provider == "auto" and engine == "none":
        problems.append(
            "OCR is enabled (auto) but neither an Azure endpoint nor tesseract is available"
        )
    problems.extend(_m2_problems(settings))
    return problems


def _m2_problems(settings: Settings) -> list[str]:
    """Database-backed (M2) checks; only when jobs are enabled (``pg_host`` set).

    An L1-only deployment (no metadata database) boots without a KEK or any database setting.
    Messages name settings only, never a value (the KEK in particular).
    """
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


def assert_production_posture(settings: Settings) -> None:
    """Raise :class:`PostureError` naming every violation."""
    problems = posture_problems(settings)
    if problems:
        raise PostureError("production posture violated: " + "; ".join(problems))
