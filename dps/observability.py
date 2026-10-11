"""Structured logging, Prometheus metrics and the readiness registry.

Logging rule (spec §10): no customer value, key or token is ever logged. A processor drops the
field names that could carry one; code elsewhere logs counts, types, hashes and durations only.
"""

from __future__ import annotations

import logging
import sys
import threading
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

import structlog
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from structlog.typing import EventDict

#: Keys that are dropped from every log event, whatever the call site passed.
_VALUE_FIELDS = frozenset(
    {
        "value",
        "values",
        "data",
        "text",
        "token",
        "tokens",
        "dek",
        "key",
        "api_key",
        "password",
        "secret",
        "payload",
        "result",
        "original",
        "originals",
        "record",
        "key_material",
    }
)


def _drop_value_fields(_logger: Any, _method: str, event_dict: EventDict) -> EventDict:
    """structlog processor: remove any field that could carry a sensitive value."""
    for name in list(event_dict):
        if name in _VALUE_FIELDS:
            event_dict.pop(name)
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
