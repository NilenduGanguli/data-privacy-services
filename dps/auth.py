"""API-key authentication with tenant grants and scopes (sibling pattern, stateless for M0).

Keys come from ``DPS_API_KEYS`` (JSON) or ``DPS_API_KEYS_FILE``; each record holds either the raw
``key`` or its ``key_sha256``, plus explicit ``tenant_ids`` and ``scopes`` (no implicit wildcard:
a record that forgets them is refused, it is not granted everything). Only hashes are kept in
memory. ``DPS_BOOTSTRAP_API_KEY`` seeds one wildcard key for local demos (production posture
refuses it). A DB-backed store with rotation arrives with jobs (M2) behind the same ``KeyStore``
protocol.

No error message, log line or key id ever carries a raw key: validation errors name the record
and the rule, and key ids are a short prefix of the key's SHA-256.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
from collections.abc import Callable
from typing import Any, Protocol

from fastapi import HTTPException, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from starlette.exceptions import HTTPException as StarletteHTTPException

from dps.config import Settings
from dps.observability import get_logger

logger = get_logger(__name__)

API_KEY_HEADER = "X-API-KEY"
TENANT_HEADER = "X-DPS-Tenant"
KEY_PREFIX = "dps_"
WILDCARD = "*"
DEFAULT_TENANT = "default"
SCOPES = ("detect", "obfuscate", "synthesize", "reveal", "jobs", "scan", "admin")
WWW_AUTHENTICATE = f'ApiKey realm="data-privacy-services", header="{API_KEY_HEADER}"'

#: Tenant ids are identifiers (they reach the audit log and, from M2, row-level security), so a
#: header that is not one is refused rather than logged.
TENANT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
_SHA256_HEX = re.compile(r"[0-9a-f]{64}")


#: Tenant ids starting with this are DPS pseudo-tenants (``_system``, contract §8.1): never
#: selectable through the API, not even with a wildcard key.
RESERVED_TENANT_PREFIX = "_"


class AuthError(Exception):
    """An authentication or authorisation failure with its HTTP status (value-free detail).

    ``code`` (M2) is a stable machine-readable code; when set, the HTTP glue answers
    ``{"detail", "code"}`` instead of M0's ``{"detail"}``.
    """

    def __init__(self, status: int, detail: str, code: str | None = None) -> None:
        super().__init__(detail)
        self.status, self.detail, self.code = status, detail, code


class CodedAuthHTTPException(HTTPException):
    """An ``HTTPException`` that also carries a ``code``: rendered as ``{"detail", "code"}`` by
    the M2 error handlers (``dps.layers.api.errors.install_error_handlers``)."""

    def __init__(
        self, status_code: int, detail: str, code: str, headers: dict[str, str] | None = None
    ) -> None:
        super().__init__(status_code=status_code, detail=detail, headers=headers)
        self.code = code


class Principal(BaseModel):
    """Who is calling: a key id (never the key), its name, tenant grants and scopes."""

    key_id: str
    name: str
    tenant_ids: list[str] = Field(default_factory=list)
    scopes: list[str] = Field(default_factory=list)

    def can_access(self, tenant_id: str) -> bool:
        """True when the key is granted ``tenant_id`` (or every tenant)."""
        return WILDCARD in self.tenant_ids or tenant_id in self.tenant_ids

    def has_scope(self, scope: str) -> bool:
        """True when the key holds ``scope`` (or every scope)."""
        return WILDCARD in self.scopes or scope in self.scopes


class KeyRecord(BaseModel):
    """One configured key: exactly one of ``key`` / ``key_sha256``, explicit grants and scopes."""

    # The record carries a raw key: validation errors must never echo the input, and an unknown
    # field (``scope`` for ``scopes``) is an error, not a silently ignored typo.
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    name: str = Field(min_length=1, max_length=64)
    key: str | None = Field(default=None, repr=False)
    key_sha256: str | None = Field(default=None, repr=False)
    tenant_ids: list[str] = Field(min_length=1)
    scopes: list[str] = Field(min_length=1)

    @field_validator("key_sha256")
    @classmethod
    def _hex_digest(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip().lower()
        if not _SHA256_HEX.fullmatch(value):
            raise ValueError("key_sha256 must be 64 hexadecimal characters")
        return value

    @field_validator("tenant_ids")
    @classmethod
    def _tenants(cls, value: list[str]) -> list[str]:
        bad = [i for i, t in enumerate(value) if t != WILDCARD and not TENANT_ID.fullmatch(t)]
        if bad:
            raise ValueError(f"tenant_ids at positions {bad} are not tenant identifiers")
        return value

    @field_validator("scopes")
    @classmethod
    def _scopes(cls, value: list[str]) -> list[str]:
        unknown = sorted({s for s in value if s != WILDCARD and s not in SCOPES})
        if unknown:
            # Scope names are vocabulary, not secrets; naming them makes a typo obvious.
            raise ValueError(f"unknown scopes {unknown}; known: {', '.join(SCOPES)}")
        return value

    @model_validator(mode="after")
    def _one_secret(self) -> KeyRecord:
        if bool(self.key) == bool(self.key_sha256):
            raise ValueError(f"key record {self.name!r} needs exactly one of key / key_sha256")
        return self


def hash_key(raw: str) -> str:
    """Hex SHA-256 of a raw API key (what ``key_sha256`` holds)."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def new_api_key() -> str:
    """A fresh random API key: ``dps_`` + 256 bits, URL-safe."""
    return KEY_PREFIX + secrets.token_urlsafe(32)


class KeyStore(Protocol):
    """Resolves a raw key to its principal (``None`` when unknown)."""

    def lookup(self, raw_key: str) -> Principal | None: ...


class StaticKeyStore:
    """Keys from configuration, held as SHA-256 digests only."""

    def __init__(self, records: list[KeyRecord]) -> None:
        self._by_hash: dict[str, Principal] = {}
        for rec in records:
            digest = rec.key_sha256 or hash_key(rec.key or "")
            if digest in self._by_hash:
                first = self._by_hash[digest].name
                raise ValueError(f"key records {first!r} and {rec.name!r} hold the same key")
            self._by_hash[digest] = Principal(
                key_id=f"static-{digest[:12]}",
                name=rec.name,
                tenant_ids=list(rec.tenant_ids),
                scopes=list(rec.scopes),
            )

    @classmethod
    def from_settings(cls, settings: Settings) -> StaticKeyStore:
        """Parse ``DPS_API_KEYS`` and add the bootstrap key when set.

        Raises:
            ValueError: the setting is not a JSON list of valid, distinct key records. The message
                names the setting, the record position and the rule, never a key.
        """
        raw: Any = []
        if settings.api_keys.strip():
            try:
                raw = json.loads(settings.api_keys)
            except json.JSONDecodeError as exc:
                where = f"line {exc.lineno} column {exc.colno}"
                raise ValueError(f"DPS_API_KEYS is not valid JSON ({exc.msg} at {where})") from None
        if not isinstance(raw, list):
            raise ValueError("DPS_API_KEYS must be a JSON list of key records")
        records: list[KeyRecord] = []
        for i, item in enumerate(raw):
            try:
                records.append(KeyRecord.model_validate(item))
            except ValidationError as exc:
                problems = "; ".join(
                    f"{'.'.join(str(p) for p in e['loc']) or 'record'}: {e['msg']}"
                    for e in exc.errors(include_input=False, include_url=False)
                )
                raise ValueError(f"DPS_API_KEYS record {i}: {problems}") from None
        if settings.bootstrap_api_key:
            records.append(
                KeyRecord(
                    name="bootstrap",
                    key=settings.bootstrap_api_key,
                    tenant_ids=[WILDCARD],
                    scopes=[WILDCARD],
                )
            )
        return cls(records)

    def lookup(self, raw_key: str) -> Principal | None:
        """The principal for ``raw_key``, or ``None``."""
        return self._by_hash.get(hash_key(raw_key))

    def __len__(self) -> int:
        return len(self._by_hash)


LOCAL_PRINCIPAL = Principal(
    key_id="local-dev", name="local-dev", tenant_ids=[WILDCARD], scopes=[WILDCARD]
)


def authenticate(store: KeyStore, *, enabled: bool, raw_key: str | None) -> Principal:
    """The caller's principal; ``LOCAL_PRINCIPAL`` when auth is disabled (local only).

    Raises:
        AuthError: 401 when the key is missing or unknown.
    """
    if not enabled:
        return LOCAL_PRINCIPAL
    if not raw_key:
        raise AuthError(401, "missing API key")
    principal = store.lookup(raw_key)
    if principal is None:
        raise AuthError(401, "invalid API key")
    return principal


def resolve_tenant(principal: Principal, requested: str | None) -> str:
    """The tenant this call acts for: the header when given (and granted), else the only grant.

    Raises:
        AuthError: 400 when the header is not a tenant identifier, 403 when the tenant is not
            granted or the key holds several tenants and none was chosen, 403
            ``tenant_reserved`` for a DPS pseudo-tenant (``_system``), wildcard keys included.
    """
    if requested:
        if requested.startswith(RESERVED_TENANT_PREFIX):  # before the grant test: wildcards too
            raise AuthError(403, "tenant id is reserved", code="tenant_reserved")
        if not TENANT_ID.fullmatch(requested):
            raise AuthError(400, f"{TENANT_HEADER} is not a tenant identifier")
        if not principal.can_access(requested):
            raise AuthError(403, "tenant not granted to this key")
        return requested
    if WILDCARD in principal.tenant_ids:
        return DEFAULT_TENANT
    if len(principal.tenant_ids) == 1:
        return principal.tenant_ids[0]
    raise AuthError(403, f"{TENANT_HEADER} header required: key is granted several tenants")


# --- FastAPI glue --------------------------------------------------------------------------------
def _reject(request: Request, exc: AuthError, reason: str) -> HTTPException:
    logger.warning("auth_rejected", status_code=exc.status, reason=reason, route=request.url.path)
    headers = {"WWW-Authenticate": WWW_AUTHENTICATE} if exc.status == 401 else None
    if exc.code is not None:
        return CodedAuthHTTPException(exc.status, exc.detail, exc.code, headers=headers)
    return HTTPException(status_code=exc.status, detail=exc.detail, headers=headers)


def _authorize(request: Request, scopes: tuple[str, ...]) -> Principal:
    principal = current_principal(request)
    if not any(principal.has_scope(s) for s in scopes):
        wanted = " or ".join(repr(s) for s in scopes)
        raise _reject(request, AuthError(403, f"scope {wanted} required"), "scope")
    try:
        request.state.tenant_id = resolve_tenant(principal, request.headers.get(TENANT_HEADER))
    except AuthError as exc:
        raise _reject(request, exc, "tenant") from None
    request.state.principal = principal
    return principal


def current_principal(request: Request) -> Principal:
    """Authenticate the request from ``X-API-KEY`` (HTTP 401 on failure)."""
    auth = request.app.state.auth
    try:
        return authenticate(
            auth["store"], enabled=auth["enabled"], raw_key=request.headers.get(API_KEY_HEADER)
        )
    except AuthError as exc:
        raise _reject(request, exc, "key") from None


def require_scope(scope: str) -> Callable[[Request], Principal]:
    """FastAPI dependency: authenticate, check ``scope``, resolve the tenant.

    Sets ``request.state.principal`` and ``request.state.tenant_id`` for the route, the rate
    limiter and the audit record.
    """
    if scope not in SCOPES:
        raise ValueError(f"unknown scope {scope!r}")

    def dependency(request: Request) -> Principal:
        return _authorize(request, (scope,))

    return dependency


def require_any_scope(*scopes: str) -> Callable[[Request], Principal]:
    """FastAPI dependency like :func:`require_scope`, satisfied by any one of ``scopes``
    (evidence and artefacts: ``jobs`` or ``scan``; the route then checks the owning job's kind)."""
    unknown = [s for s in scopes if s not in SCOPES]
    if not scopes or unknown:
        raise ValueError(f"unknown scopes {unknown!r}")

    def dependency(request: Request) -> Principal:
        return _authorize(request, tuple(scopes))

    return dependency


async def coded_http_exception_handler(request: Request, exc: Exception) -> Response:
    """Exception handler: an ``HTTPException`` with a ``code`` answers ``{"detail", "code"}``;
    any other keeps FastAPI's ``{"detail"}`` (M0's wire shape)."""
    if not isinstance(exc, StarletteHTTPException):  # pragma: no cover - registered for it only
        raise exc
    code = getattr(exc, "code", None)
    if isinstance(code, str):
        return JSONResponse(
            {"detail": exc.detail, "code": code}, status_code=exc.status_code, headers=exc.headers
        )
    return await http_exception_handler(request, exc)
