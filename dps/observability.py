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
from contextvars import ContextVar
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
        # documents, OCR and schema synthesis (M1): counts, names, hashes and infrastructure
        # hosts — never a value, a page's text or an OCR response body
        "format",
        "pages",
        "ocr_pages",
        "unreadable_pages",
        "boxes",
        "words",
        "rows",
        "fields",
        "generator",
        "source",
        "engine",
        "phase",
        "http_status",
        "host",
        "fallback",
        "spec_hash",
        "bytes",
        "warnings",
        # T3 metadata labelling (M1): how many fields were sent and labelled — counts only
        "verdicts",
        # metadata database (M2): migration file names (dps/db.py)
        "migration",
        # job queue and connectors (M2): ids, counts, phases, states and metadata names only;
        # object_ref is a table name, never a row value
        "job_id",
        "partition_id",
        "partition_no",
        "state",
        "kind",
        "worker_id",
        "batch_no",
        "attempts",
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
        # UI scanner (M4): page numbers, crawl depth, guard decisions and resource types, counts,
        # step names and persona ordinals — never a URL, a label, a page value or a storage state
        "page_no",
        "depth",
        "decision",
        "resource_type",
        "blocked",
        "step",
        "persona",
        # M5: tabular synthesis, adapters, retention and keys — counts, ids, engine and gate
        # names only (a fitted model, a seed or a token never has a field of its own)
        "columns",
        "resamples",
        "adapter",
        "deleted",
        "tenants",
        "artefact_id",
        "gate",
        "key_count",
        "grace_hours",
        # Oracle and MongoDB connectors and cross-system checks (M3): an operation name, a driver
        # error *code* (ORA-00942, mongo:11000 — never the driver message, which can quote a
        # value), member/pair counts, a coverage ratio and a pass flag
        "op",
        "driver_code",
        "members",
        "pairs",
        "coverage",
        "passed",
        "sampled",
    }
)

#: Third-party loggers bypass the structlog allow-list (they log through stdlib ``logging``), and
#: pdfminer's debug output quotes page content, so they are capped whatever ``DPS_LOG_LEVEL`` is.
_THIRD_PARTY_LEVELS = (
    ("pypdf", logging.ERROR),
    ("pdfminer", logging.ERROR),
    ("pdfplumber", logging.ERROR),
    ("PIL", logging.ERROR),
    ("reportlab", logging.ERROR),
    ("faker", logging.ERROR),
    ("httpx", logging.WARNING),
    ("httpcore", logging.WARNING),
    # Driver debug logs quote whole commands: pymongo's command log carries every document of a
    # write (and E11000 replies quote the duplicate key); botocore logs request bodies.
    ("pymongo", logging.WARNING),
    ("botocore", logging.WARNING),
    ("boto3", logging.WARNING),
    ("s3transfer", logging.WARNING),
    ("urllib3", logging.WARNING),
    # sqlglot logs the statement it falls back to parsing as a Command ("EXPLAIN PLAN FOR … WHERE
    # n = '…'") at WARNING, and parser.check_errors logs str(error) — which quotes the SQL — at
    # ERROR, so a refused selection's literal would reach stderr: silenced entirely.
    ("sqlglot", logging.CRITICAL),
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
    for name, cap in _THIRD_PARTY_LEVELS:
        logging.getLogger(name).setLevel(cap)


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
LLM_LABEL_FIELDS = Counter(
    "dps_llm_label_fields_total",
    "Fields offered to T3 metadata labelling, by outcome "
    "(labelled, unlabelled, skipped, blocked by the leak guard, failed)",
    ["outcome"],
)
VERIFY_FAIL = Counter("dps_verification_failures_total", "Leakage sweeps that failed", ["mode"])
LATENCY = Histogram(
    "dps_request_seconds",
    "Request latency by route",
    ["route"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)
# documents and OCR (M1)
DOCUMENT_PAGES = Counter(
    "dps_document_pages_total", "Document pages processed", ["format", "source"]
)
REDACTED_BOXES = Counter("dps_redacted_boxes_total", "Boxes burned into redacted PDFs")
OCR_CALLS = Counter("dps_ocr_calls_total", "OCR calls by engine and outcome", ["engine", "outcome"])
OCR_SECONDS = Histogram(
    "dps_ocr_seconds",
    "OCR call latency (analyze + polls)",
    buckets=(0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0, 120.0),
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
CONNECTOR_CALLS = Counter(
    "dps_connector_calls_total",
    "Connector driver calls by engine, operation and outcome (M3)",
    ["engine", "op", "outcome"],
)
CONSISTENCY_CHECKS = Counter(
    "dps_consistency_checks_total", "Cross-system consistency checks by outcome (M3)", ["outcome"]
)

# M5 tabular synthesis, external tokenization and DB-backed API keys
TABULAR_RESAMPLES = Counter(
    "dps_tabular_resamples_total", "Synthetic rows resampled to avoid an exact real row"
)
EXTERNAL_CALLS = Counter(
    "dps_external_token_calls_total", "Calls to the tokenization service", ["op", "outcome"]
)
KEYS_LOADED = Gauge("dps_auth_keys_loaded", "Active DB-backed API keys in this process's snapshot")

# M5 per-tenant LLM exposure (spec P1, §10; contract §8.3). M0's LLM_RAW_PII stays the process
# counter; these add the tenant dimension. Labels are tenant ids and purposes, never a value.
LLM_CALLS_TENANT = Counter(
    "dps_llm_calls_by_tenant", "LLM gateway calls per tenant", ["tenant_id", "purpose"]
)
LLM_RAW_PII_TENANT = Counter(
    "dps_llm_raw_pii_sent_by_tenant",
    "Raw sensitive values sent to the LLM per tenant. Must stay 0.",
    ["tenant_id"],
)
LLM_BLOCKED_TENANT = Counter(
    "dps_llm_exposure_blocked_by_tenant",
    "Prompts the LLM egress verifier blocked per tenant",
    ["tenant_id"],
)
#: The tenant the current request (``run_engine``/``begin_call``) or job handler
#: (``Consumer._guard``) acts for; ``None`` outside both (counted as tenant ``-``).
CURRENT_TENANT: ContextVar[str | None] = ContextVar("dps_tenant", default=None)


@dataclass
class LlmStats:
    """LLM figures of one request or job handler (counts only): calls sent, raw values found
    in a sent prompt (``warn`` mode), prompts blocked by the egress verifier."""

    calls: int = 0
    raw_values: int = 0
    blocked: int = 0

    def counts(self) -> dict[str, int]:
        """``{"llm_calls", "llm_raw_values", "llm_blocked"}`` (audit counts)."""
        return {
            "llm_calls": self.calls,
            "llm_raw_values": self.raw_values,
            "llm_blocked": self.blocked,
        }


#: The current request's / job handler's :class:`LlmStats` (``None`` when nothing is bound).
LLM_STATS: ContextVar[LlmStats | None] = ContextVar("dps_llm_stats", default=None)


def record_llm(*, purpose: str, raw_values: int = 0, blocked: bool = False) -> None:
    """Count one LLM egress decision for the bound tenant (``-`` when unbound).

    ``blocked``: the verifier stopped the prompt (nothing sent). Otherwise the call was sent and
    ``raw_values`` is the number of raw sensitive values it carried (``warn`` mode only; any
    increase fires the ``DpsRawPiiSentToLlm`` alert). Never takes a value.
    """
    tenant = CURRENT_TENANT.get() or "-"
    stats = LLM_STATS.get()
    if blocked:
        LLM_BLOCKED_TENANT.labels(tenant).inc()
        if stats is not None:
            stats.blocked += 1
        return
    LLM_CALLS_TENANT.labels(tenant, purpose).inc()
    if raw_values > 0:
        LLM_RAW_PII_TENANT.labels(tenant).inc(raw_values)
        LLM_RAW_PII.inc(raw_values)
    if stats is not None:
        stats.calls += 1
        stats.raw_values += raw_values


# M4 UI scanner (labels are outcomes, guard decisions, HTTP methods, reason codes and entity
# types — never a URL, a host's path or a value)
SCAN_PAGES = Counter("dps_scan_pages_total", "UI-scanner pages by outcome", ["outcome"])
SCAN_REQUESTS = Counter(
    "dps_scan_requests_total", "Requests seen by the scan guard", ["decision", "method", "reason"]
)
SCAN_FIELDS = Counter(
    "dps_scan_fields_total", "Form fields inventoried by entity type", ["entity_type"]
)
SCAN_FILLS = Counter("dps_scan_fills_total", "Field fills by mode and outcome", ["mode", "outcome"])


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
