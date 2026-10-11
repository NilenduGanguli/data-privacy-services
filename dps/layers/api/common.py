"""What every engine route does: limits, tracing, error mapping, audit, metrics.

Error mapping (the engine's messages name rules, never values):

- payload too large (characters, values) → 413; malformed payload or nesting too deep → 422
- :class:`~dps.engine.service.VerificationFailed` → 422 (nothing is returned)
- :class:`~dps.engine.service.EngineError` → 400
- anything else → 500 ``internal error``; only the exception's class name is logged
- a strict audit sink that cannot take the event (:class:`~dps.audit.AuditUnavailable`) → 503
  ``audit unavailable``: nothing the engine produced is returned

Every outcome, rejections included, is counted and audited (value-free). The audited and
measured ``route`` is the route **template** (``/api/v1/jobs/{job_id}``), never the concrete path,
and the audit event carries the HTTP status the request answers with.
"""

from __future__ import annotations

import inspect
import re
import time
import uuid
from collections import Counter
from collections.abc import Awaitable, Callable
from typing import Any, overload

import structlog
from fastapi import HTTPException, Request

from dps.audit import AuditEvent, AuditUnavailable
from dps.engine.policy import preset_names
from dps.engine.service import EngineError, Payload, VerificationFailed
from dps.observability import LATENCY, REQUESTS, get_logger

logger = get_logger(__name__)

TRACE_HEADER = "X-Request-ID"
#: Deepest JSON nesting accepted; the engine walks documents recursively.
MAX_DEPTH = 100

#: A caller's request id is echoed (and logged) only when it is an identifier; anything else —
#: free text, a value, a control character — is replaced by a generated id.
_TRACE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
#: Policy names that may be audited verbatim (preset names, ``<preset>+overrides``, ids).
_POLICY_LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,63}")


def route_template(request: Request) -> str:
    """The matched route's path template (``/api/v1/jobs/{job_id}``), else the request path."""
    path = getattr(request.scope.get("route"), "path", None)
    return path if isinstance(path, str) else request.url.path


def trace_id(request: Request) -> str:
    """The caller's ``X-Request-ID`` when it is a safe identifier, else a fresh 16-hex id."""
    given = request.headers.get(TRACE_HEADER, "")
    return given if _TRACE_ID.fullmatch(given) else uuid.uuid4().hex[:16]


def policy_label(ref: str | dict[str, Any] | None, default: str) -> str:
    """A value-free name for the requested policy (for audit and metrics).

    Preset names pass through; an unknown name becomes ``unknown`` (it could be anything the
    caller typed); an inline policy is ``<preset>+overrides`` or ``inline``.
    """
    if ref is None:
        return default
    if isinstance(ref, str):
        return ref if ref in preset_names() else "unknown"
    preset = ref.get("preset")
    if "name" not in ref and isinstance(preset, str) and preset in preset_names():
        return f"{preset}+overrides"
    return "inline"


def _measure(data: Any, max_items: int, max_depth: int) -> tuple[int, bool]:
    """``(leaf count, too deep)`` without recursion; stops early past either limit."""
    stack: list[tuple[Any, int]] = [(data, 0)]
    leaves = 0
    while stack:
        node, depth = stack.pop()
        if depth > max_depth:
            return leaves, True
        if isinstance(node, dict):
            stack.extend((v, depth + 1) for v in node.values())
        elif isinstance(node, list):
            stack.extend((v, depth + 1) for v in node)
        else:
            leaves += 1
            if leaves > max_items:
                break
    return leaves, False


def payload_from(request: Request, data: Any | None, text: str | None) -> Payload:
    """Validate the body against the configured limits.

    Raises:
        HTTPException: 422 when not exactly one of data / text is given or the document nests
            deeper than :data:`MAX_DEPTH`; 413 when it exceeds the text or value limits.
    """
    settings = request.app.state.settings
    try:
        payload = Payload(data=data, text=text)
    except ValueError:
        raise HTTPException(status_code=422, detail="provide exactly one of data or text") from None
    if text is not None and len(text) > settings.max_text_chars:
        raise HTTPException(
            status_code=413, detail=f"text exceeds {settings.max_text_chars} characters"
        )
    if data is not None:
        leaves, too_deep = _measure(data, settings.max_items, MAX_DEPTH)
        if too_deep:
            raise HTTPException(status_code=422, detail=f"document nests deeper than {MAX_DEPTH}")
        if leaves > settings.max_items:
            raise HTTPException(
                status_code=413, detail=f"document exceeds {settings.max_items} values"
            )
    return payload


def _audit_event(
    request: Request,
    *,
    mode: str,
    policy: str,
    result: Any,
    duration_ms: float,
    outcome: str,
    tid: str,
    status_code: int,
) -> AuditEvent:
    report = getattr(result, "report", None)
    findings = getattr(result, "findings", None) or []
    if report is not None:
        if policy == "inline" and _POLICY_LABEL.fullmatch(report.policy):
            policy = report.policy
        verification = report.verification
        counts: dict[str, Any] = {
            "findings_total": report.findings_total,
            "findings_by_type": dict(report.findings_by_type),
            "methods_used": dict(report.methods_used),
            "verification_passed": verification.passed if verification is not None else None,
            "reversible_values": report.reversible_values,
        }
    else:
        counts = {
            "findings_total": len(findings),
            "findings_by_type": dict(Counter(f.entity_type for f in findings)),
        }
    principal = getattr(request.state, "principal", None)
    return AuditEvent(
        trace_id=tid,
        route=route_template(request),
        tenant_id=getattr(request.state, "tenant_id", "default"),
        key_id=principal.key_id if principal is not None else "anonymous",
        mode=mode,
        policy=policy,
        duration_ms=round(duration_ms, 2),
        outcome=outcome,
        status=status_code,
        **counts,
    )


def _audit(request: Request, **fields: Any) -> None:
    """Write the audit event.

    Raises:
        HTTPException: 503 ``audit unavailable`` when a strict sink refuses the event. Any other
            sink failure is logged (type only) and never fails the request.
    """
    try:
        request.app.state.audit.write(_audit_event(request, **fields))
    except AuditUnavailable as exc:
        raise HTTPException(status_code=503, detail="audit unavailable") from exc
    except Exception as exc:  # noqa: BLE001 - a non-strict sink failure never takes the request down
        logger.warning("audit_write_failed", error=type(exc).__name__)


@overload
async def run_engine[T](
    request: Request, *, mode: str, policy_name: str, call: Callable[[], Awaitable[T]]
) -> tuple[T, str]: ...


@overload
async def run_engine[T](
    request: Request, *, mode: str, policy_name: str, call: Callable[[], T]
) -> tuple[T, str]: ...


async def run_engine[T](
    request: Request,
    *,
    mode: str,
    policy_name: str,
    call: Callable[[], Awaitable[T] | T],
) -> tuple[T, str]:
    """Run an engine operation with tracing, error mapping, metrics and audit.

    Args:
        request: the current request (auth dependencies have set principal and tenant).
        mode: the audited mode (``detect``, ``obfuscate``, ``plan:synthesize``, ...).
        policy_name: the value-free policy label to audit — pass :func:`policy_label`'s
            result (or ``"-"``), never the caller's raw policy.
        call: validates the payload and runs the engine; may raise ``HTTPException``. It may
            return a value or an awaitable (a sync ``call`` runs on the event loop, so routers
            push CPU-bound engine work to the threadpool inside an async ``call``).

    Returns:
        The engine's result and the trace id.
    """
    tid = trace_id(request)
    request.state.trace_id = tid
    route = route_template(request)
    structlog.contextvars.bind_contextvars(trace_id=tid, route=route)
    start = time.perf_counter()
    outcome = "ok"
    status_code = getattr(request.scope.get("route"), "status_code", None) or 200
    result: T | None = None
    try:
        raw = call()
        result = await raw if inspect.isawaitable(raw) else raw
        return result, tid
    except VerificationFailed as exc:
        outcome, status_code = "verification_failed", 422
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except EngineError as exc:
        outcome, status_code = "bad_request", 400
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except HTTPException as exc:
        outcome, status_code = "rejected", exc.status_code
        raise
    except Exception as exc:  # noqa: BLE001 - mapped to a value-free 500
        outcome, status_code = "error", 500
        logger.error("engine_error", error=type(exc).__name__)
        raise HTTPException(status_code=500, detail="internal error") from None
    finally:
        try:
            duration = (time.perf_counter() - start) * 1000
            REQUESTS.labels(route, outcome).inc()
            LATENCY.labels(route).observe(duration / 1000)
            # A strict sink's 503 replaces the result (or the error) being returned.
            _audit(
                request,
                mode=mode,
                policy=policy_name,
                result=result,
                duration_ms=duration,
                outcome=outcome,
                tid=tid,
                status_code=status_code,
            )
        finally:
            structlog.contextvars.unbind_contextvars("trace_id", "route")
