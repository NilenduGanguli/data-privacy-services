"""Per-key token-bucket rate limiting — a per-process backstop; the bank's gateway owns global limits."""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable

from fastapi import HTTPException, Request

from dps.observability import get_logger

logger = get_logger(__name__)

#: Buckets kept before idle (full) ones are dropped; keys are configured, so this is a backstop.
MAX_BUCKETS = 10_000


class RateLimiter:
    """Token bucket per key id: ``burst`` requests at once, refilled at ``rate`` per second."""

    def __init__(
        self,
        rate: float,
        burst: int,
        *,
        enabled: bool = True,
        clock: Callable[[], float] = time.monotonic,
        max_buckets: int = MAX_BUCKETS,
    ) -> None:
        if enabled and (rate <= 0 or burst < 1):
            raise ValueError("rate limit needs rate > 0 and burst >= 1")
        self.rate, self.burst, self.enabled, self._clock = rate, burst, enabled, clock
        self._max_buckets = max_buckets
        self._buckets: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()

    @property
    def retry_after_seconds(self) -> int:
        """Whole seconds until one token is back in an empty bucket (at least 1)."""
        return max(1, math.ceil(1.0 / self.rate)) if self.rate > 0 else 1

    def check(self, key_id: str) -> bool:
        """Take one token for ``key_id``; ``False`` when its bucket is empty."""
        if not self.enabled:
            return True
        now = self._clock()
        with self._lock:
            if key_id not in self._buckets and len(self._buckets) >= self._max_buckets:
                self._prune(now)
            tokens, last = self._buckets.get(key_id, (float(self.burst), now))
            tokens = min(float(self.burst), tokens + max(0.0, now - last) * self.rate)
            if tokens < 1.0:
                self._buckets[key_id] = (tokens, now)
                return False
            self._buckets[key_id] = (tokens - 1.0, now)
            return True

    def _prune(self, now: float) -> None:
        """Drop buckets that have refilled completely (they hold no state worth keeping)."""
        full_after = self.burst / self.rate
        for key in [k for k, (_, last) in self._buckets.items() if now - last >= full_after]:
            del self._buckets[key]


def enforce_rate_limit(request: Request) -> None:
    """FastAPI dependency (after ``require_scope``): HTTP 429 when the caller's bucket is empty."""
    limiter: RateLimiter = request.app.state.rate_limiter
    principal = getattr(request.state, "principal", None)
    if principal is not None:
        key_id = principal.key_id
    else:
        key_id = f"ip:{request.client.host}" if request.client else "anonymous"
    if not limiter.check(key_id):
        logger.warning(
            "rate_limited",
            key_id=principal.key_id if principal is not None else None,
            route=request.url.path,
        )
        raise HTTPException(
            status_code=429,
            detail="rate limit exceeded",
            headers={"Retry-After": str(limiter.retry_after_seconds)},
        )
