"""Structured logging, Prometheus metrics and the readiness registry.

Logging rule (spec §10): no customer value, key or token is ever logged. Logging uses an
allow-list: a processor keeps only the field names in ``_SAFE_FIELDS`` and drops every other key,
whatever the call site passed. Code logs counts, types, ids, hashes and durations only.
"""

from __future__ import annotations

import logging
import re
import sys
import threading
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

import structlog
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest
from structlog.typing import EventDict

#: The only keys a log event may carry; every other key is dropped (spec §10 allow-list).
#: Each key holds a count, a duration, an id, an entity type / method / route name, a flag or an
#: exception class name — never a customer value, key, token or secret. Extend it (never bypass
#: it) when a new call site needs a field, and keep ``tests/test_observability.py`` in step.
_SAFE_FIELDS = frozenset(
    {
        # structlog's own keys
        "event",
        "level",
        "timestamp",
        "logger",
        # correlation and identity (ids, never credentials)
        "trace_id",
        "request_id",
        "tenant_id",
        "key_id",
        "route",
        "component",
        # outcome
        "outcome",
        "status",
        "status_code",
        "duration_ms",
        "attempt",
        "reason",
        "error",
        "error_code",
        "version",
        # engine vocabulary: modes, methods, entity types and counts
        "mode",
        "method",
        "policy",
        "entity_type",
        "detector",
        "count",
        "counts",
        "hits",
        "findings_total",
        "findings_by_type",
        "methods_used",
        "reversible_values",
        "verification_passed",
        # LLM gateway and startup
        "provider",
        "purpose",
        "model",
        "env",
        "auth",
        # audit events (dps.audit.AuditEvent); action is a name such as job.cancel
        "ts",
        "action",
        # metadata database (M2): migration file names (dps/db.py)
        "migration",
        # job queue and connectors (M2): ids, counts, phases, states and metadata names only;
        # object_ref is a table name, never a row value
        "job_id",
        "partition_id",
        "partition_no",
        "phase",
        "state",
        "kind",
        "worker_id",
        "rows",
        "batch_no",
        "attempts",
        "engine",
        "write_mode",
        "connection_id",
        "snapshot_id",
        "evidence_id",
        "group_id",
        "partitions",
        "elapsed_s",
        "rows_per_s",
        "object_ref",
        "code",
        "checks",
        "lease_lost",
    }
)


#: Event names are snake_case identifiers (``verification_failed``, ``llm.call``). Anything else —
#: free text, an f-string with a value in it, a run of 4+ digits — is replaced wholesale.
_EVENT_NAME = re.compile(r"(?![a-z0-9_.]*[0-9]{4})[a-z][a-z0-9_.]{0,63}")
_REDACTED_EVENT = "event_redacted"


def _drop_value_fields(_logger: Any, _method: str, event_dict: EventDict) -> EventDict:
    """structlog processor: keep only allow-listed fields (``_SAFE_FIELDS``), drop the rest.

    An exception passed in an allowed field is reduced to its class name, because its message
    may quote a value. ``exc_info``/``stack_info`` are not allow-listed, so tracebacks (which can
    quote values too) never reach the log. An ``event`` that is not a snake_case name is replaced
    by ``"event_redacted"``, since a free-text message may carry an interpolated value.
    """
    for name in list(event_dict):
        if name not in _SAFE_FIELDS:
            event_dict.pop(name)
        elif isinstance(event_dict[name], BaseException):
            event_dict[name] = type(event_dict[name]).__name__
    event = event_dict.get("event")
    if "event" in event_dict and not (isinstance(event, str) and _EVENT_NAME.fullmatch(event)):
        event_dict["event"] = _REDACTED_EVENT
    return event_dict


def configure_logging(level: str = "INFO", *, json_output: bool = True) -> None:
    """Configure structlog (JSON to stdout in containers, console locally) and stdlib logging."""
    numeric = logging.getLevelNamesMapping().get(level.upper(), logging.INFO)
    logging.basicConfig(level=numeric, stream=sys.stderr, format="%(message)s")
    renderer: Any = (
        structlog.processors.JSONRenderer() if json_output else structlog.dev.ConsoleRenderer()
    )
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
FINDINGS = Counter(
    "dps_findings_total", "Findings by entity type and detector", ["entity_type", "detector"]
)
TRANSFORMS = Counter(
    "dps_transforms_total", "Values transformed by mode and method", ["mode", "method"]
)
LLM_CALLS = Counter("dps_llm_calls_total", "LLM gateway calls", ["provider", "purpose", "outcome"])
LLM_RAW_PII = Counter(
    "dps_llm_raw_pii_sent", "Raw sensitive values sent to the LLM gateway. Must stay 0 forever."
)
VERIFY_FAIL = Counter("dps_verification_failures_total", "Leakage sweeps that failed", ["mode"])
LATENCY = Histogram(
    "dps_request_seconds",
    "Request latency by route",
    ["route"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)

# M2 job queue and connectors
JOBS_CLAIMED = Counter("dps_jobs_claimed_total", "Job rows claimed", ["kind", "phase"])
PARTITIONS_FINISHED = Counter(
    "dps_partitions_finished_total", "Partitions reaching a state", ["state"]
)
LEASES_LOST = Counter("dps_leases_lost_total", "Fenced writes that found the lease gone", ["what"])
ROWS_WRITTEN = Counter("dps_rows_written_total", "Rows written by L2 jobs", ["mode", "write_mode"])
QUEUE_DEPTH = Gauge("dps_queue_depth", "Queue rows by table and state", ["table", "state"])
AUDIT_DROPPED = Counter("dps_audit_dropped_total", "Audit events dropped on a saturated queue")


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
            comps = {
                n: {"ok": c.ok, "detail": c.detail, **c.meta} for n, c in self._components.items()
            }
        ready = all(comps.get(n, {}).get("ok") is True for n in self.required)
        return {"ready": ready, "components": comps}


READINESS = Readiness()
