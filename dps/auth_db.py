"""DB-backed API keys (contract §8.3): hashed at rest, rotation with a grace window, expiry, disable.

``DbKeyStore`` keeps M0's synchronous ``KeyStore.lookup`` by serving an in-memory snapshot of the
active keys, refreshed every ``DPS_KEY_CACHE_SECONDS`` by a background task. Expiry is checked on
every lookup against the snapshot's ``expires_at``, so a key stops at its expiry even between
refreshes. A disable or rotation on this replica invalidates the entry at once; other replicas
see it within one refresh. If refreshes fail for ten periods the snapshot is dropped — DB keys fail
closed while static keys keep working. Raw keys exist only in the create/rotate response and the
operator CLI's stdout; the table holds sha256 hashes and 8-character prefixes.

No privilege escalation (:func:`check_grant`): a key may only carry scopes and tenants its creator
holds; the wildcard (``*``) is granted only by a key that holds the wildcard itself, and every
operation on an existing key (rotate, disable, enable) needs the caller to cover all its tenants.
"""

from __future__ import annotations

import asyncio
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import asyncpg
from pydantic import BaseModel

from dps import db
from dps.auth import (
    KEY_PREFIX,
    RESERVED_TENANT_PREFIX,
    SCOPES,
    WILDCARD,
    KeyStore,
    Principal,
    StaticKeyStore,
    hash_key,
    new_api_key,
)
from dps.config import Settings
from dps.ids import new_id
from dps.observability import KEYS_LOADED, get_logger

logger = get_logger(__name__)
KEY_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")
#: Tenant ids a DB key may name: the job platform's ids (``dps_tenant``'s CHECK without the
#: reserved ``_`` start) — a pseudo-tenant such as ``_system`` is never grantable.
KEY_TENANT = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")
#: Lifetime of a rotated key's successor when the old key had no expiry.
DEFAULT_ROTATED_DAYS = 90
_COLS = (
    "id, name, key_prefix, tenant_ids, scopes, created_by, created_at, expires_at, last_used_at, "
    "disabled_at, rotated_from, rotated_at"
)


class GrantError(Exception):
    """A key operation is refused (status + code + value-free detail)."""

    def __init__(self, status: int, code: str, detail: str) -> None:
        super().__init__(detail)
        self.status, self.code, self.detail = status, code, detail


class ApiKeyView(BaseModel):
    """One key as the Admin page shows it: names, grants, dates — never the key or its hash."""

    id: str
    name: str
    key_prefix: str
    tenant_ids: list[str]
    scopes: list[str]
    source: Literal["db", "static"] = "db"
    status: Literal["active", "expired", "disabled"]
    created_by: str | None = None
    created_at: datetime | None = None
    expires_at: datetime | None = None
    last_used_at: datetime | None = None
    disabled_at: datetime | None = None
    rotated_from: str | None = None
    rotated_at: datetime | None = None
    age_days: int | None = None


class CreatedKey(BaseModel):
    """A new key: the raw ``key`` is returned once and never stored."""

    key: str
    record: ApiKeyView


@dataclass(frozen=True)
class _Entry:
    principal: Principal
    expires_at: datetime | None


def key_prefix(raw: str) -> str:
    """The stored, displayable prefix of a raw key (``dps_`` + 4 characters)."""
    return raw[:8]


def _table(settings: Settings) -> str:
    return f"{db.schema_ident(settings)}.dps_api_key"


def _view(row: Any, now: datetime | None = None) -> ApiKeyView:
    now = now or datetime.now(UTC)
    status: Literal["active", "expired", "disabled"] = "active"
    if row["disabled_at"] is not None:
        status = "disabled"
    elif row["expires_at"] is not None and row["expires_at"] <= now:
        status = "expired"
    created = row["created_at"]
    return ApiKeyView(
        id=row["id"],
        name=row["name"],
        key_prefix=row["key_prefix"],
        tenant_ids=list(row["tenant_ids"]),
        scopes=list(row["scopes"]),
        status=status,
        created_by=row["created_by"],
        created_at=created,
        expires_at=row["expires_at"],
        last_used_at=row["last_used_at"],
        disabled_at=row["disabled_at"],
        rotated_from=row["rotated_from"],
        rotated_at=row["rotated_at"],
        age_days=(now - created).days if created else None,
    )


def check_grant(actor: Principal, tenant_ids: list[str], scopes: list[str]) -> None:
    """No escalation: a key may only carry scopes and tenants its creator holds.

    Raises:
        GrantError: 422 ``invalid_scope``; 403 ``scope_escalation`` / ``tenant_escalation``.
    """
    for scope in scopes:
        if scope != WILDCARD and scope not in SCOPES:
            raise GrantError(422, "invalid_scope", "unknown scope; known: " + ", ".join(SCOPES))
        if scope == WILDCARD and WILDCARD not in actor.scopes:
            raise GrantError(
                403, "scope_escalation", "only a key holding all scopes may grant all scopes"
            )
        if not actor.has_scope(scope):
            raise GrantError(
                403, "scope_escalation", f"cannot grant scope {scope} the caller does not hold"
            )
    for tenant in tenant_ids:
        if tenant == WILDCARD and WILDCARD not in actor.tenant_ids:
            raise GrantError(
                403, "tenant_escalation", "only a key holding all tenants may grant all tenants"
            )
        if not actor.can_access(tenant):
            raise GrantError(
                403, "tenant_escalation", "cannot grant a tenant the caller cannot access"
            )


def covers(actor: Principal, tenant_ids: list[str]) -> bool:
    """True when ``actor`` may see or manage a key granted ``tenant_ids``."""
    if WILDCARD in actor.tenant_ids:
        return True
    return WILDCARD not in tenant_ids and all(actor.can_access(t) for t in tenant_ids)


class DbKeyStore:
    """See the module docstring."""

    def __init__(
        self,
        settings: Settings,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._settings, self._now, self._monotonic = settings, now, monotonic
        self._lock = threading.Lock()
        self._by_hash: dict[str, _Entry] = {}
        self._by_id: dict[str, str] = {}
        self._used: set[str] = set()
        self._loaded_at: float | None = None
        self._failures = 0

    @property
    def loaded(self) -> bool:
        """True while the snapshot is fresh enough to serve lookups."""
        return not self._stale()

    def _stale(self) -> bool:
        return (
            self._loaded_at is None
            or self._monotonic() - self._loaded_at > 10 * self._settings.key_cache_seconds
        )

    def lookup(self, raw_key: str) -> Principal | None:
        """The principal of an active, unexpired DB key; ``None`` otherwise or when stale."""
        if not raw_key.startswith(KEY_PREFIX):
            return None
        digest = hash_key(raw_key)
        with self._lock:
            if self._stale():
                return None
            entry = self._by_hash.get(digest)
            if entry is None or (entry.expires_at is not None and entry.expires_at <= self._now()):
                return None
            self._used.add(entry.principal.key_id)
            return entry.principal

    def invalidate(self, key_id: str) -> None:
        """Drop one key from this replica's snapshot at once (disable / rotate)."""
        with self._lock:
            digest = self._by_id.pop(key_id, None)
            if digest is not None:
                self._by_hash.pop(digest, None)

    async def refresh(self) -> int:
        """Flush ``last_used_at`` and reload the active keys; returns how many are loaded."""
        with self._lock:
            used, self._used = self._used, set()
        table = _table(self._settings)
        try:
            await db.init_pool(self._settings)  # the app's pool (never the process defaults)
            async with db.acquire(None) as conn:
                if used:
                    await conn.execute(
                        f"UPDATE {table} SET last_used_at = now() WHERE id = ANY($1::text[])",
                        sorted(used),
                    )
                rows = await conn.fetch(
                    f"SELECT id, name, key_hash, tenant_ids, scopes, expires_at FROM {table} "
                    "WHERE disabled_at IS NULL AND (expires_at IS NULL OR expires_at > now())"
                )
        except BaseException:
            with self._lock:
                self._used |= used  # flushed next time
            raise
        snapshot = {
            r["key_hash"]: _Entry(
                Principal(
                    key_id=r["id"],
                    name=r["name"],
                    tenant_ids=list(r["tenant_ids"]),
                    scopes=list(r["scopes"]),
                ),
                r["expires_at"],
            )
            for r in rows
        }
        with self._lock:
            self._by_hash = snapshot
            self._by_id = {e.principal.key_id: h for h, e in snapshot.items()}
            self._loaded_at = self._monotonic()
        KEYS_LOADED.set(len(snapshot))
        return len(snapshot)

    async def run(self, stop: asyncio.Event) -> None:
        """Refresh every ``key_cache_seconds`` until ``stop`` is set; failures are counted."""
        while not stop.is_set():
            try:
                await self.refresh()
                self._failures = 0
            except Exception as exc:  # noqa: BLE001 - retried; ten failures make lookups fail closed
                self._failures += 1
                logger.warning(
                    "key_refresh_failed", error=type(exc).__name__, attempt=self._failures
                )
            try:
                await asyncio.wait_for(stop.wait(), timeout=self._settings.key_cache_seconds)
            except TimeoutError:
                pass


class ChainKeyStore:
    """The first store that recognises a key wins (static keys first, then the database)."""

    def __init__(self, *stores: KeyStore) -> None:
        self._stores = stores

    def lookup(self, raw_key: str) -> Principal | None:
        """The first principal any store resolves ``raw_key`` to, or ``None``."""
        for store in self._stores:
            principal = store.lookup(raw_key)
            if principal is not None:
                return principal
        return None


def _validate(
    name: str, tenant_ids: list[str], scopes: list[str], days: int, settings: Settings
) -> None:
    if not KEY_NAME.fullmatch(name):
        raise GrantError(422, "invalid_name", "name must match ^[a-z0-9][a-z0-9-]{1,62}$")
    if not 1 <= days <= settings.key_max_ttl_days:
        raise GrantError(
            422, "invalid_expiry", f"expires_in_days must be within 1..{settings.key_max_ttl_days}"
        )
    if not tenant_ids or not scopes:
        raise GrantError(422, "invalid_grant", "a key needs at least one tenant and one scope")
    for tenant in tenant_ids:
        if tenant != WILDCARD and (
            tenant.startswith(RESERVED_TENANT_PREFIX) or not KEY_TENANT.fullmatch(tenant)
        ):
            raise GrantError(
                422, "invalid_tenant", "tenant ids must match ^[a-z0-9][a-z0-9_-]{0,62}$ or be *"
            )


async def create_key(
    *,
    name: str,
    tenant_ids: list[str],
    scopes: list[str],
    expires_in_days: int,
    actor: Principal,
    settings: Settings,
) -> CreatedKey:
    """Mint a key (raw key returned once). Raises :class:`GrantError`."""
    _validate(name, tenant_ids, scopes, expires_in_days, settings)
    check_grant(actor, tenant_ids, scopes)
    raw = new_api_key()
    async with db.acquire(None) as conn:
        row = await conn.fetchrow(
            f"INSERT INTO {_table(settings)} (id, key_hash, name, key_prefix, tenant_ids, scopes, "
            "created_by, expires_at) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, now() + make_interval(days => $8)) "
            f"RETURNING {_COLS}",
            new_id("key"),
            hash_key(raw),
            name,
            key_prefix(raw),
            sorted(set(tenant_ids)),
            sorted(set(scopes)),
            actor.key_id,
            expires_in_days,
        )
    logger.info("api_key_created", key_id=row["id"], count=len(scopes))
    return CreatedKey(key=raw, record=_view(row))


async def list_keys(
    *, actor: Principal, static: StaticKeyStore | None, settings: Settings
) -> list[ApiKeyView]:
    """DB keys (newest first) and static keys whose tenants the actor covers."""
    async with db.acquire(None) as conn:
        rows = await conn.fetch(f"SELECT {_COLS} FROM {_table(settings)} ORDER BY created_at DESC")
    views = [_view(r) for r in rows if covers(actor, list(r["tenant_ids"]))]
    for p in static.principals() if static is not None else []:
        if covers(actor, p.tenant_ids):
            views.append(
                ApiKeyView(
                    id=p.key_id,
                    name=p.name,
                    key_prefix="static",
                    tenant_ids=p.tenant_ids,
                    scopes=p.scopes,
                    source="static",
                    status="active",
                )
            )
    return views


async def _locked(conn: asyncpg.Connection, key_id: str, settings: Settings) -> Any:
    row = await conn.fetchrow(
        f"SELECT {_COLS} FROM {_table(settings)} WHERE id = $1 FOR UPDATE", key_id
    )
    if row is None:
        raise GrantError(404, "key_not_found", "key not found")
    return row


async def rotate_key(
    key_id: str, *, grace_hours: int, actor: Principal, settings: Settings
) -> tuple[CreatedKey, ApiKeyView]:
    """Mint the successor (same name, grants, lifetime); the old key lives ``grace_hours`` more
    (0 = disabled now). A key is rotated at most once (409 ``key_rotated``)."""
    grace = max(0, min(grace_hours, settings.key_rotation_max_grace_hours))
    raw = new_api_key()
    table = _table(settings)
    try:
        async with db.acquire(None) as conn, conn.transaction():
            old = await _locked(conn, key_id, settings)
            check_grant(actor, list(old["tenant_ids"]), list(old["scopes"]))
            if old["rotated_at"] is not None:
                raise GrantError(409, "key_rotated", "key was already rotated")
            if _view(old).status != "active":
                raise GrantError(404, "key_not_found", "key not found, disabled or expired")
            lifetime = (
                (old["expires_at"] - old["created_at"]).days
                if old["expires_at"]
                else DEFAULT_ROTATED_DAYS
            )
            days = max(1, min(lifetime, settings.key_max_ttl_days))
            new = await conn.fetchrow(
                f"INSERT INTO {table} (id, key_hash, name, key_prefix, tenant_ids, scopes, "
                "created_by, expires_at, rotated_from) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, now() + make_interval(days => $8), $9) "
                f"RETURNING {_COLS}",
                new_id("key"),
                hash_key(raw),
                old["name"],
                key_prefix(raw),
                list(old["tenant_ids"]),
                list(old["scopes"]),
                actor.key_id,
                days,
                key_id,
            )
            if grace == 0:
                old = await conn.fetchrow(
                    f"UPDATE {table} SET disabled_at = now(), disabled_by = $2, rotated_at = now() "
                    f"WHERE id = $1 RETURNING {_COLS}",
                    key_id,
                    actor.key_id,
                )
            else:
                old = await conn.fetchrow(
                    f"UPDATE {table} SET rotated_at = now(), expires_at = LEAST("
                    "coalesce(expires_at, 'infinity'::timestamptz), "
                    "now() + make_interval(hours => $2)) "
                    f"WHERE id = $1 RETURNING {_COLS}",
                    key_id,
                    grace,
                )
    except asyncpg.UniqueViolationError as exc:
        raise GrantError(409, "key_rotated", "key was already rotated") from exc
    logger.info("api_key_rotated", key_id=key_id, grace_hours=grace)
    return CreatedKey(key=raw, record=_view(new)), _view(old)


async def disable_key(key_id: str, *, actor: Principal, settings: Settings) -> ApiKeyView:
    """Disable a key now (idempotent; the first disabler is kept)."""
    async with db.acquire(None) as conn, conn.transaction():
        row = await _locked(conn, key_id, settings)
        check_grant(actor, list(row["tenant_ids"]), [])
        row = await conn.fetchrow(
            f"UPDATE {_table(settings)} SET disabled_at = coalesce(disabled_at, now()), "
            f"disabled_by = coalesce(disabled_by, $2) WHERE id = $1 RETURNING {_COLS}",
            key_id,
            actor.key_id,
        )
    logger.info("api_key_disabled", key_id=key_id)
    return _view(row)


async def enable_key(key_id: str, *, actor: Principal, settings: Settings) -> ApiKeyView:
    """Re-enable a disabled key — never a rotated or an expired one (409)."""
    async with db.acquire(None) as conn, conn.transaction():
        row = await _locked(conn, key_id, settings)
        check_grant(actor, list(row["tenant_ids"]), list(row["scopes"]))
        if row["rotated_at"] is not None:
            raise GrantError(409, "key_rotated", "a rotated key cannot be re-enabled")
        if row["expires_at"] is not None and row["expires_at"] <= datetime.now(UTC) + timedelta(
            seconds=1
        ):
            raise GrantError(409, "key_expired", "an expired key cannot be re-enabled")
        row = await conn.fetchrow(
            f"UPDATE {_table(settings)} SET disabled_at = NULL, disabled_by = NULL "
            f"WHERE id = $1 RETURNING {_COLS}",
            key_id,
        )
    logger.info("api_key_enabled", key_id=key_id)
    return _view(row)
