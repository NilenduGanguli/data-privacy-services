"""Value-free audit events. M0 writes them to the structured log; M2 adds the append-only table.

An event holds ids, names, counts, a verdict and a duration — never a customer value, a key, a
token or a seed. ``AuditEvent`` forbids extra fields so nothing value-bearing can be attached
by accident, and every field name is on the logging allow-list (``dps.observability``).
"""

from __future__ import annotations

from collections import deque
from datetime import UTC, datetime
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from dps.observability import get_logger

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
