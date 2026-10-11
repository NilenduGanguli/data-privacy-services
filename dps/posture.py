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
