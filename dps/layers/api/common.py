"""What every engine route does: limits, tracing, error mapping, audit, metrics.

Error mapping (the engine's messages name rules, never values):

- payload too large (characters, values) → 413; malformed payload or nesting too deep → 422
- :class:`~dps.engine.service.VerificationFailed` → 422 (nothing is returned)
- :class:`~dps.engine.service.EngineError` → 400
- anything else → 500 ``internal error``; only the exception's class name is logged

Every outcome, rejections included, is counted and audited (value-free).
"""

from __future__ import annotations

import re
import time
import uuid
from collections import Counter
from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from fastapi import HTTPException, Request

from dps.audit import AuditEvent
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
        route=request.url.path,
        tenant_id=getattr(request.state, "tenant_id", "default"),
        key_id=principal.key_id if principal is not None else "anonymous",
        mode=mode,
        policy=policy,
        duration_ms=round(duration_ms, 2),
        outcome=outcome,
        **counts,
    )


def _audit(request: Request, **fields: Any) -> None:
    """Write the audit event; a failure is logged (type only) and never fails the request."""
    try:
        request.app.state.audit.write(_audit_event(request, **fields))
    except Exception as exc:  # noqa: BLE001 - audit must not take the request down in M0 (strict mode arrives with M2)
        logger.warning("audit_write_failed", error=type(exc).__name__)


async def run_engine[T](
    request: Request,
    *,
    mode: str,
    policy: str,
    call: Callable[[], Awaitable[T]],
) -> tuple[T, str]:
    """Run an engine operation with tracing, error mapping, metrics and audit.

    Args:
        request: the current request (auth dependencies have set principal and tenant).
        mode: the audited mode (``detect``, ``obfuscate``, ``plan:synthesize``, ...).
        policy: the value-free policy label (see :func:`policy_label`).
        call: validates the payload and runs the engine; may raise ``HTTPException``.

    Returns:
        The engine's result and the trace id.
    """
    tid = trace_id(request)
    request.state.trace_id = tid
    route = request.url.path
    structlog.contextvars.bind_contextvars(trace_id=tid, route=route)
    start = time.perf_counter()
    outcome = "ok"
    result: T | None = None
    try:
        result = await call()
        return result, tid
    except VerificationFailed as exc:
        outcome = "verification_failed"
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except EngineError as exc:
        outcome = "bad_request"
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except HTTPException:
        outcome = "rejected"
        raise
    except Exception as exc:  # noqa: BLE001 - mapped to a value-free 500
        outcome = "error"
        logger.error("engine_error", error=type(exc).__name__)
        raise HTTPException(status_code=500, detail="internal error") from None
    finally:
        duration = (time.perf_counter() - start) * 1000
        REQUESTS.labels(route, outcome).inc()
        LATENCY.labels(route).observe(duration / 1000)
        _audit(
            request,
            mode=mode,
            policy=policy,
            result=result,
            duration_ms=duration,
            outcome=outcome,
            tid=tid,
        )
        structlog.contextvars.unbind_contextvars("trace_id", "route")
