"""Production posture guard — refuse to boot with an unsafe configuration.

Called before the FastAPI app is constructed (see ``dps/app.py``) so a bad staging/prod
configuration fails the process instead of serving traffic. M5 adds secrets-from-files, TLS-only
egress, explicit CORS origins and **strict verification at boot** (spec §10), plus
:func:`posture_report` — the runtime view the Admin page shows (evaluated "as if production" in
every environment; enforced only in staging/prod).

Every message names a setting, a policy or a key **name** — never a value, a URL or a secret.
"""

from __future__ import annotations

import base64
import binascii
import os
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from pydantic import BaseModel

from dps.config import Settings
from dps.engine.policy import preset_names, resolve_policy
from dps.observability import get_logger

if TYPE_CHECKING:
    from dps.auth import Principal

logger = get_logger(__name__)

#: Secrets that must be mounted files in staging/prod: a literal env var lands in ``env`` dumps,
#: crash reports and ``oc describe``. Checked against the process environment, because Settings
#: cannot tell where a value came from.
SECRET_ENV = (
    "DPS_JOB_KEK",
    "DPS_JOB_KEK_PREVIOUS",
    "DPS_API_KEYS",
    "DPS_COIN_CLIENT_ID",
    "DPS_COIN_CLIENT_SECRET",
    "DPS_PG_PASSWORD",
    "DPS_PG_WORKER_PASSWORD",
    "DPS_PG_MIGRATION_PASSWORD",
    "DPS_TOKENIZER_TOKEN",
    "DPS_AZURE_VISION_KEY",
    "DPS_S3_ACCESS_KEY_ID",
    "DPS_S3_SECRET_ACCESS_KEY",
)
#: Settings that name an outbound endpoint (TLS-only egress, spec §10).
EGRESS_URLS = (
    "stellar_base_url",
    "coin_url",
    "azure_vision_endpoint",
    "s3_endpoint_url",
    "tokenizer_url",
)


#: The process roles (contract §3.5) the guard knows. ``app`` (the default) and ``worker`` get every
#: rule; the ``browser-worker`` (M4) runs no HTTP API, no documents OCR and no external tokenizer (a
#: scan makes its tokens with the scan key only), so it is never refused for their settings or the
#: secrets it does not mount: API keys, CORS origins, the OCR key and the tokenizer token.
ROLES = ("app", "worker", "browser-worker")
BROWSER_WORKER = "browser-worker"


class PostureError(RuntimeError):
    """The configuration violates the production posture."""


def posture_problems(settings: Settings, *, role: str = "app") -> list[str]:
    """Return every violation for ``settings`` in ``role`` (empty outside staging/prod).

    Raises:
        ValueError: ``role`` is not one of :data:`ROLES`.
    """
    if role not in ROLES:
        raise ValueError(f"unknown role {role!r}")
    if not settings.is_production:
        return []
    serves = role != BROWSER_WORKER  # the API, OCR and the tokenizer
    problems: list[str] = []
    if not settings.auth_enabled:
        problems.append("DPS_AUTH_ENABLED must be true")
    if settings.bootstrap_api_key:
        problems.append("DPS_BOOTSTRAP_API_KEY must be unset")
    if serves and not settings.api_keys:
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
    if serves and (settings.ocr_provider == "azure" or engine == "azure"):
        if not settings.azure_vision_key:
            problems.append(
                "DPS_AZURE_VISION_KEY (or DPS_AZURE_VISION_KEY_FILE) is required for azure OCR"
            )
        if urlparse(settings.azure_vision_endpoint).scheme != "https":
            problems.append("DPS_AZURE_VISION_ENDPOINT must be an https URL")
    # Local Tesseract (provider tesseract, or auto without Azure) has no egress and is allowed;
    # auto that resolves to nothing would silently leave scanned pages unread.
    if serves and settings.ocr_provider == "auto" and engine == "none":
        problems.append(
            "OCR is enabled (auto) but neither an Azure endpoint nor tesseract is available"
        )
    problems.extend(_m2_problems(settings))
    problems.extend(_m5_problems(settings, os.environ, serves=serves))
    return list(dict.fromkeys(problems))


def _kek_bytes(value: str) -> int:
    """Decoded length of a base64 KEK (urlsafe or standard); ``-1`` when it does not decode."""
    altchars = b"-_" if ("-" in value or "_" in value) else None
    try:
        return len(
            base64.b64decode(value + "=" * (-len(value) % 4), altchars=altchars, validate=True)
        )
    except (binascii.Error, ValueError):
        return -1


def non_strict_policies(settings: Settings) -> list[str]:
    """Shipped presets and the default policy whose leakage check is not strict (names only)."""
    names = dict.fromkeys([*sorted(preset_names()), settings.default_policy])
    out: list[str] = []
    for name in names:
        try:
            policy = resolve_policy(name, default_name=settings.default_policy)
        except KeyError:  # an unknown default policy fails every request; not a posture item
            continue
        if policy.verify.leakage_check != "strict":
            out.append(name)
    return out


def _egress_problems(settings: Settings) -> list[str]:
    problems: list[str] = []
    if not settings.egress_tls_required:
        problems.append("DPS_EGRESS_TLS_REQUIRED must be true")
    for field in EGRESS_URLS:
        value = getattr(settings, field, "")
        if value and urlparse(value).scheme != "https":
            problems.append(f"DPS_{field.upper()} must be an https URL")
    if any(getattr(settings, f, "") for f in EGRESS_URLS) and not settings.ssl_cert_file:
        problems.append("DPS_SSL_CERT_FILE (the organisation CA bundle) must be set for TLS egress")
    return problems


def _strict_verification_problems(settings: Settings) -> list[str]:
    """Spec §10: "refuses to boot in staging/prod without ... strict verification"."""
    problems: list[str] = []
    if settings.verify_warn_allowed:
        problems.append(
            "DPS_VERIFY_WARN_ALLOWED must be false (callers may not relax verification)"
        )
    if settings.llm_egress_check != "strict":
        problems.append("DPS_LLM_EGRESS_CHECK must be strict")
    problems += [
        f"policy {name} must use verify.leakage_check=strict"
        for name in non_strict_policies(settings)
    ]
    return problems


def _m5_problems(
    settings: Settings, environ: Mapping[str, str], *, serves: bool = True
) -> list[str]:
    """M5 (spec §10): secrets from files, TLS-only egress, explicit CORS, strict verification.

    The KEK rule applies wherever a worker runs (``pg_host`` set, contract §10); the audit and
    embedded-worker rules are M2's (:func:`_m2_problems`), for the same reason. ``serves=False``
    (the browser-worker) skips the rules of components that role does not run: the tokenizer
    token and the CORS origins.
    """
    problems = [
        f"{name} must be provided as {name}_FILE (a mounted secret), not a literal "
        "environment variable"
        for name in SECRET_ENV
        if environ.get(name)
    ]
    if settings.pg_host:
        if not settings.job_kek:
            problems.append("DPS_JOB_KEK (DPS_JOB_KEK_FILE) must be set")
        elif _kek_bytes(settings.job_kek) != 32:  # the settings validator already refuses it
            problems.append("DPS_JOB_KEK must be base64 of exactly 32 bytes")
    problems += _egress_problems(settings)
    if serves and settings.tokenizer_provider == "http" and not settings.tokenizer_token:
        problems.append("DPS_TOKENIZER_TOKEN_FILE must be set when the tokenizer is enabled")
    if serves and "*" in settings.cors_origin_list:
        problems.append(
            "DPS_CORS_ORIGINS must list origins explicitly (or be empty for same-origin only)"
        )
    problems += _strict_verification_problems(settings)
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


def assert_production_posture(settings: Settings, *, role: str = "app") -> None:
    """Raise :class:`PostureError` naming every violation of ``role`` (see :data:`ROLES`)."""
    problems = posture_problems(settings, role=role)
    if problems:
        raise PostureError("production posture violated: " + "; ".join(problems))


# --- runtime report (Admin page) ------------------------------------------------------------

#: The checks of :func:`posture_report`, in display order: ``(id, name, enforced at boot)``.
CHECKS: tuple[tuple[str, str, bool], ...] = (
    ("auth_enabled", "API authentication is on", True),
    ("bootstrap_key_absent", "No bootstrap API key", True),
    ("api_keys_from_file", "API keys from the database or a mounted file", True),
    ("tls_egress", "TLS-only egress with the organisation CA bundle", True),
    ("audit_strict", "Audit on and strict", True),
    ("leakage_strict", "Strict leakage verification everywhere", True),
    ("llm_not_stub", "A real (or no) LLM provider", True),
    ("kek_from_secret", "Job KEK from a mounted secret (32 bytes)", True),
    ("keys_rotated", "API keys rotated within the maximum age", False),
    ("rls_posture", "Row-level security on every tenant table", True),
    ("migrations_verified", "Database migrations verified", False),
)
DB_DOWN = "database unavailable"


class PostureCheck(BaseModel):
    """One posture check: a fixed id, a label, the result and a value-free detail."""

    id: str
    name: str
    ok: bool
    detail: str
    enforced_at_boot: bool


class PostureReport(BaseModel):
    """The runtime posture: every check evaluated as if production; ``enforced`` in staging/prod."""

    env: str
    enforced: bool
    passed: int
    total: int
    checked_at: datetime
    checks: list[PostureCheck]


async def _tenant_warn_policies(principal: Principal | None) -> list[str]:
    """``<tenant>/<name>`` of every visible tenant's active policy whose leakage check is not strict.

    ``principal=None`` (internal callers) or a wildcard key sees every directory tenant; any other
    key sees only the tenants it is granted (contract §9: admin views are the caller's tenants).
    """
    from dps import db
    from dps.tenants import tenant_directory

    out: list[str] = []
    s = db.schema_ident()
    for tenant in await tenant_directory():
        if principal is not None and not principal.can_access(tenant):
            continue
        async with db.acquire(tenant) as conn:
            rows = await conn.fetch(
                f"SELECT p.name FROM {s}.dps_policy p JOIN {s}.dps_policy_version v "
                "ON v.tenant_id = p.tenant_id AND v.name = p.name AND v.version = p.active_version "
                "WHERE p.tenant_id = $1 AND v.body->'verify'->>'leakage_check' IS DISTINCT FROM "
                "'strict'",
                tenant,
            )
        out += [f"{tenant}/{r['name']}" for r in rows]
    return out


async def _stale_keys(settings: Settings, principal: Principal | None) -> list[str]:
    """``<name> is <n> days old`` of active keys older than the rotation age, never rotated.

    Only keys the caller may see (:func:`dps.auth_db.covers`, as ``GET /admin/keys``) are listed;
    ``principal=None`` (internal callers) sees every key.
    """
    from dps import db
    from dps.auth_db import covers

    async with db.acquire(None) as conn:
        rows = await conn.fetch(
            "SELECT name, tenant_ids, "
            "floor(extract(epoch FROM now() - created_at) / 86400)::int AS days "
            f"FROM {db.schema_ident()}.dps_api_key WHERE disabled_at IS NULL "
            "AND (expires_at IS NULL OR expires_at > now()) AND rotated_at IS NULL "
            "AND created_at < now() - make_interval(days => $1) ORDER BY created_at, name",
            settings.key_rotation_max_age_days,
        )
    return [
        f"{r['name']} is {r['days']} days old"
        for r in rows
        if principal is None or covers(principal, list(r["tenant_ids"]))
    ]


def _check(check_id: str, ok: bool, detail: str) -> PostureCheck:
    name, boot = next((n, b) for i, n, b in CHECKS if i == check_id)
    return PostureCheck(id=check_id, name=name, ok=ok, detail=detail, enforced_at_boot=boot)


def _joined(items: list[str], ok_text: str) -> str:
    return "; ".join(items) if items else ok_text


async def posture_report(
    settings: Settings,
    *,
    environ: Mapping[str, str] | None = None,
    principal: Principal | None = None,
) -> PostureReport:
    """The eleven posture checks (ids fixed, see :data:`CHECKS`), value-free.

    Static checks reuse the boot rules; database checks (tenant policies, key ages, RLS,
    migrations) report ``ok=False, detail="database unavailable"`` without a reachable database.
    The two checks that name tenant objects (``leakage_strict`` tenant policies, ``keys_rotated``)
    are scoped to ``principal``: a key granted some tenants sees, and is judged on, only those
    tenants' policies and the keys it covers; a wildcard key (or ``None``) sees the whole platform.
    """
    env = os.environ if environ is None else environ
    checks: list[PostureCheck] = [
        _check("auth_enabled", settings.auth_enabled, "DPS_AUTH_ENABLED"),
        _check(
            "bootstrap_key_absent",
            not settings.bootstrap_api_key,
            "DPS_BOOTSTRAP_API_KEY is set" if settings.bootstrap_api_key else "unset",
        ),
    ]
    from_file = bool(env.get("DPS_API_KEYS_FILE")) and not env.get("DPS_API_KEYS")
    db_keys = bool(settings.pg_host)
    checks.append(
        _check(
            "api_keys_from_file",
            (from_file or db_keys) and not env.get("DPS_API_KEYS"),
            "a literal DPS_API_KEYS environment variable is set"
            if env.get("DPS_API_KEYS")
            else ("database keys" if db_keys else "DPS_API_KEYS_FILE")
            if (from_file or db_keys)
            else "neither database keys nor DPS_API_KEYS_FILE",
        )
    )
    egress = _egress_problems(settings)
    checks.append(_check("tls_egress", not egress, _joined(egress, "https only, CA bundle set")))
    audit_ok = settings.audit_enabled and settings.audit_strict
    checks.append(
        _check("audit_strict", audit_ok, "on, strict" if audit_ok else "DPS_AUDIT_STRICT/ENABLED")
    )
    leakage = _strict_verification_problems(settings)
    db_ok = False
    if settings.pg_host:
        try:
            leakage += [
                f"tenant policy {p} must use verify.leakage_check=strict"
                for p in await _tenant_warn_policies(principal)
            ]
            db_ok = True
        except Exception as exc:  # noqa: BLE001 - reported as "database unavailable"
            logger.warning("posture_db_unavailable", error=type(exc).__name__)
    if not db_ok:
        leakage.append(f"tenant policies: {DB_DOWN}")
    checks.append(_check("leakage_strict", not leakage, _joined(leakage, "strict everywhere")))
    checks.append(
        _check(
            "llm_not_stub",
            settings.llm_provider != "stub",
            f"provider {settings.llm_provider or 'none'}",
        )
    )
    kek_ok = (
        bool(env.get("DPS_JOB_KEK_FILE"))
        and not env.get("DPS_JOB_KEK")
        and _kek_bytes(settings.job_kek) == 32
    )
    checks.append(
        _check("kek_from_secret", kek_ok, "DPS_JOB_KEK_FILE" if kek_ok else "no KEK file")
    )
    checks += await _db_checks(settings, db_ok, principal)
    passed = sum(c.ok for c in checks)
    return PostureReport(
        env=settings.env,
        enforced=settings.is_production,
        passed=passed,
        total=len(checks),
        checked_at=datetime.now(UTC),
        checks=checks,
    )


async def _db_checks(
    settings: Settings, reachable: bool, principal: Principal | None
) -> list[PostureCheck]:
    if not (settings.pg_host and reachable):
        return [_check(i, False, DB_DOWN) for i in ("keys_rotated", "rls_posture")] + [
            _check("migrations_verified", False, DB_DOWN)
        ]
    from dps import db

    out: list[PostureCheck] = []
    try:
        stale = await _stale_keys(settings, principal)
        out.append(_check("keys_rotated", not stale, _joined(stale, "all within the rotation age")))
    except Exception as exc:  # noqa: BLE001
        logger.warning("posture_db_unavailable", error=type(exc).__name__)
        out.append(_check("keys_rotated", False, DB_DOWN))
    try:
        rls = await db.assert_rls_posture(settings)
        out.append(_check("rls_posture", not rls, _joined(rls, "FORCE + tenant_isolation")))
    except Exception as exc:  # noqa: BLE001
        logger.warning("posture_db_unavailable", error=type(exc).__name__)
        out.append(_check("rls_posture", False, DB_DOWN))
    try:
        await db.verify_migrations(settings)
        out.append(_check("migrations_verified", True, "ledger matches the image"))
    except db.MigrationDrift:
        out.append(_check("migrations_verified", False, "pending or drifted migrations"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("posture_db_unavailable", error=type(exc).__name__)
        out.append(_check("migrations_verified", False, DB_DOWN))
    return out
