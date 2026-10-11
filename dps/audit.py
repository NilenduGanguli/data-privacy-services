"""Value-free audit events. M0 writes them to the structured log; M2 adds the append-only table.

An event holds ids, names, counts, a verdict and a duration — never a customer value, a key, a
token or a seed. ``AuditEvent`` forbids extra fields so nothing value-bearing can be attached
by accident, and every field name is on the logging allow-list (``dps.observability``).

:class:`DbAuditSink` (contract §8.3) writes the structured log line **synchronously first** — the
crash-safe record — and then queues the event for a background writer that inserts batches into
``dps_audit_log``. The table has FORCE RLS, so each flush groups events by tenant and inserts each
group under that tenant's GUC. ``DPS_AUDIT_STRICT=true`` turns a saturated queue into
:class:`AuditUnavailable` (HTTP 503); otherwise the event is dropped and counted
(``dps_audit_dropped_total``) — its log line already exists.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections import defaultdict, deque
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from dps.config import Settings
from dps.observability import AUDIT_DROPPED, get_logger

logger = get_logger("dps.audit")


class AuditEvent(BaseModel):
    """One audited API call (counts and names only)."""

    model_config = ConfigDict(extra="forbid")

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
    # M2 (all defaulted, so M0 call sites are unchanged)
    action: str = ""  # detect|obfuscate|…|job.create|snapshot.accept|connection.test|…
    status: int | None = None  # the HTTP status the request answered with
    job_id: str | None = None
    counts: dict[str, int] = Field(default_factory=dict)  # extra value-free counts


class AuditSink(Protocol):
    """Where audit events go (log in M0; the append-only table from M2)."""

    def write(self, event: AuditEvent) -> None: ...


class LogAuditSink:
    """Writes each event as one structured ``audit`` log line."""

    def write(self, event: AuditEvent) -> None:
        """Log ``event`` (every field is allow-listed and value-free)."""
        logger.info("audit", **event.model_dump(mode="json"))


class MemoryAuditSink:
    """Keeps the last ``maxlen`` events in memory (tests and the local console)."""

    def __init__(self, maxlen: int = 1000) -> None:
        self.events: deque[AuditEvent] = deque(maxlen=maxlen)

    def write(self, event: AuditEvent) -> None:
        """Append ``event``."""
        self.events.append(event)


class NullAuditSink:
    """Drops events: used only when ``DPS_AUDIT_ENABLED=false`` (refused in staging/prod)."""

    def write(self, event: AuditEvent) -> None:
        """Discard ``event``."""


#: ``acquire(tenant_id)`` → an async context manager yielding a connection with the tenant GUC
#: bound (``dps.db.acquire`` in the API, ``dps.db.acquire_queue`` in workers).
AcquireFn = Callable[[str | None], AbstractAsyncContextManager[Any]]


class AuditUnavailable(Exception):
    """Strict mode: the audit queue is saturated, so the request must not proceed (503)."""

    code = "audit_unavailable"

    def __init__(self) -> None:
        super().__init__("audit unavailable")


def audit_row(event: AuditEvent) -> tuple[Any, ...]:
    """The ``dps_audit_log`` row of an event (route template, action, value-free counts).

    Column order: ``ts, tenant_id, key_id, trace_id, route, action, mode, policy, outcome,
    status, job_id, counts``. ``action`` falls back to the mode's first segment (``obfuscate``,
    ``plan``), else ``request``.
    """
    counts: dict[str, Any] = {
        "findings_total": event.findings_total,
        "reversible_values": event.reversible_values,
        "duration_ms": int(event.duration_ms),
        **event.counts,
    }
    if event.findings_by_type:
        counts["findings_by_type"] = event.findings_by_type
    if event.methods_used:
        counts["methods_used"] = event.methods_used
    if event.verification_passed is not None:
        counts["verification_passed"] = event.verification_passed
    action = event.action or (event.mode.split(":")[0] if event.mode else "request")
    return (
        event.ts,
        event.tenant_id,
        event.key_id,
        event.trace_id,
        event.route,
        action,
        event.mode or None,
        event.policy or None,
        event.outcome,
        event.status,
        event.job_id,
        counts,
    )


class DbAuditSink:
    """Synchronous log line, then batched per-tenant inserts into ``dps_audit_log``.

    ``start()`` runs inside the event loop (the app's lifespan, a worker's main); ``stop()``
    lets the writer drain the queue and returns within ``timeout`` (what is still queued then is
    counted as dropped). Without a running writer, ``write`` only logs. A failed flush keeps the
    writer alive and marks the sink unhealthy with the exception's class name — never its
    message, which may quote a value.
    """

    def __init__(self, acquire: AcquireFn, settings: Settings) -> None:
        from dps.db import schema_ident  # the db extra is needed only when this sink is used

        self._acquire = acquire
        self._settings = settings
        self._insert = (
            f"INSERT INTO {schema_ident(settings)}.dps_audit_log (ts, tenant_id, key_id, trace_id, "
            "route, action, mode, policy, outcome, status, job_id, counts) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12::jsonb)"
        )
        self._log = LogAuditSink()
        self._queue: asyncio.Queue[AuditEvent] | None = None
        self._task: asyncio.Task[None] | None = None
        self._closing = False
        self._healthy = True
        self._last_error: str | None = None

    @property
    def healthy(self) -> bool:
        """False after a failed flush, until the next successful one."""
        return self._healthy

    @property
    def last_error(self) -> str | None:
        """The class name of the last flush failure (never its message)."""
        return self._last_error

    def start(self) -> None:
        """Start the background writer (call inside a running event loop)."""
        if self._task is not None:
            return
        self._queue = asyncio.Queue(maxsize=max(1, self._settings.audit_queue_max))
        self._closing = False
        self._task = asyncio.get_running_loop().create_task(self._run(), name="dps-audit-writer")

    async def stop(self, timeout: float = 10.0) -> None:
        """Flush what is queued and stop the writer (at most ``timeout`` seconds)."""
        task, self._task = self._task, None
        if task is None:
            return
        self._closing = True
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout)
        except TimeoutError:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            left = self._queue.qsize() if self._queue is not None else 0
            if left:
                AUDIT_DROPPED.inc(left)
                logger.warning("audit_stop_timeout", count=left)
        self._queue = None

    def write(self, event: AuditEvent) -> None:
        """Log ``event`` (the crash-safe record), then queue it for the table.

        Raises:
            AuditUnavailable: strict mode and the queue is full.
        """
        self._log.write(event)
        if self._queue is None:
            return
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            AUDIT_DROPPED.inc()
            if self._settings.audit_strict:
                raise AuditUnavailable() from None

    async def _run(self) -> None:
        queue = self._queue
        if queue is None:  # pragma: no cover - start() sets it first
            return
        wait_s = max(0.001, self._settings.audit_flush_ms / 1000)
        max_batch = max(1, self._settings.audit_flush_batch)
        while True:
            if self._closing and queue.empty():
                return
            try:
                batch = [await asyncio.wait_for(queue.get(), wait_s)]
            except TimeoutError:
                continue
            while len(batch) < max_batch and not queue.empty():
                batch.append(queue.get_nowait())
            await self._flush(batch)

    async def _flush(self, batch: list[AuditEvent]) -> None:
        by_tenant: dict[str, list[AuditEvent]] = defaultdict(list)
        for event in batch:
            if not event.tenant_id:  # refused before a tenant was known (contract §8.3)
                event = event.model_copy(update={"tenant_id": "-"})
            by_tenant[event.tenant_id].append(event)
        failed: str | None = None
        for tenant, events in by_tenant.items():
            try:
                async with self._acquire(tenant) as conn:
                    await conn.executemany(self._insert, [audit_row(e) for e in events])
            except Exception as exc:  # noqa: BLE001 - the writer survives a bad batch; never echo
                failed = type(exc).__name__
                logger.warning("audit_flush_failed", error=failed, count=len(events))
        self._healthy, self._last_error = failed is None, failed
