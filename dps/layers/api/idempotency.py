"""``Idempotency-Key`` on the Layer 1 routes: a replica-local replay cache of fingerprints.

Spec §6 ("``Idempotency-Key`` is honoured"), contract §9 (L1 replay custody, amendment
2026-10-12), decision D2 (stateless key custody), M1 plan Task 19B.

Layer 1 keeps no state and DPS stores no key, so the cache holds, per scope hash
(``sha256(tenant, key id, route template, Idempotency-Key)``), exactly an :class:`IdemEntry`: the
SHA-256 of the request body, the original call's trace id and when it completed — **never key
material, a DEK, a synthesis seed, an output or a value**. A retry with the same key and body within
``idempotency_ttl_seconds`` re-runs the engine and is answered as a replay (``Idempotent-Replay:
true``, the original ``trace_id``, audit outcome ``replayed``). The result is byte-identical only
when the caller supplied its own ``consistency.key_material`` / ``synthesis_seed``; otherwise the
replay returns fresh material, which is safe because a Layer 1 call has no side effects. The same
key with another body is refused (422 ``idempotency_key_reused``). An entry is recorded only after
a successful call (for a stream: one that ended with its report line), so a failed call's retry
runs as a first call. Entries leave at the TTL or by LRU eviction at
``idempotency_max_entries``; ``idempotency_ttl_seconds = 0`` disables the cache (a well-formed
header is then accepted and ignored).
"""

from __future__ import annotations

import hashlib
import re
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass

from fastapi import Request

from dps.layers.api.common import CodedHTTPException

#: The header the caller sends, and the one a replay carries (M2's job routes send the same one).
IDEMPOTENCY_HEADER = "Idempotency-Key"
REPLAY_HEADER = "Idempotent-Replay"
#: Contract §9: the key is an identifier, never free text (it is hashed, but also bounded).
KEY_RE: re.Pattern[str] = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")


class IdempotencyConflict(Exception):
    """The scope is cached with another body hash (the key was reused for another request)."""


def parse_idempotency_key(request: Request) -> str | None:
    """The request's ``Idempotency-Key`` (``None`` when absent).

    Raises:
        CodedHTTPException: 422 ``idempotency_key_invalid`` when the header does not match
            :data:`KEY_RE` (the detail never quotes the header).
    """
    raw = request.headers.get(IDEMPOTENCY_HEADER)
    if raw is None:
        return None
    if not KEY_RE.fullmatch(raw):
        raise CodedHTTPException(
            422,
            "idempotency_key_invalid",
            "Idempotency-Key must be 1-128 characters of letters, digits and _ . : -",
        )
    return raw


@dataclass(frozen=True, slots=True)
class IdemEntry:
    """Every field the replay cache keeps for a scope (decision D2: nothing else)."""

    body_sha256: str  # hex SHA-256 of the raw request body
    trace_id: str  # the original call's trace id
    created: float  # clock() when the first call completed


class IdempotencyCache:
    """Replica-local, TTL- and size-bounded map of scope hash → :class:`IdemEntry`.

    Thread-safe (one lock); ``clock`` is injectable for tests.
    """

    def __init__(
        self,
        *,
        ttl_seconds: int,
        max_entries: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self.clock = clock
        self._entries: OrderedDict[str, IdemEntry] = OrderedDict()
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        """False when ``ttl_seconds`` is 0: nothing is cached or replayed."""
        return self.ttl_seconds > 0

    def _expired(self, entry: IdemEntry) -> bool:
        return self.clock() - entry.created > self.ttl_seconds

    def get(self, scope: str, body_sha256: str) -> IdemEntry | None:
        """The live entry for ``scope`` (``None`` when absent or expired).

        Raises:
            IdempotencyConflict: the live entry was recorded for another body.
        """
        if not self.enabled:
            return None
        with self._lock:
            entry = self._entries.get(scope)
            if entry is None:
                return None
            if self._expired(entry):
                del self._entries[scope]
                return None
            if entry.body_sha256 != body_sha256:
                raise IdempotencyConflict
            self._entries.move_to_end(scope)
            return entry

    def put(self, scope: str, entry: IdemEntry) -> None:
        """Record a completed first call (after a successful response only).

        A live entry for the same scope is kept: of two concurrent first calls, the first to
        finish is the one a retry replays.
        """
        if not self.enabled:
            return
        with self._lock:
            current = self._entries.get(scope)
            if current is not None and not self._expired(current):
                return
            self._entries[scope] = entry
            self._entries.move_to_end(scope)
            while len(self._entries) > self.max_entries:
                self._entries.popitem(last=False)

    def entries(self) -> dict[str, IdemEntry]:
        """A copy of the live entries (tests and diagnostics)."""
        with self._lock:
            return {s: e for s, e in self._entries.items() if not self._expired(e)}


def route_template(request: Request) -> str:
    """The matched route's path template (the request path when no route is recorded)."""
    return str(getattr(request.scope.get("route"), "path", "") or request.url.path)


def scope_of(request: Request, key: str) -> str:
    """``sha256(tenant, key id, route template, key)`` — per caller, per route, per key."""
    principal = getattr(request.state, "principal", None)
    parts = (
        str(getattr(request.state, "tenant_id", "default")),
        principal.key_id if principal is not None else "anonymous",
        route_template(request),
        key,
    )
    return hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()
