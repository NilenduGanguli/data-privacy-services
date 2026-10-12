"""What every engine route does: limits, tracing, error mapping, audit, metrics.

Error mapping (the engine's messages name rules, never values):

- payload too large (characters, values) → 413; malformed payload or nesting too deep → 422
- :class:`~dps.engine.service.VerificationFailed` → 422 ``{"detail", "code": "leakage"}``
  (nothing is returned)
- :class:`~dps.engine.service.EngineError` → 400 ``{"detail", "code": "bad_request"}``
- :class:`~dps.errors.CodedError` → its own status and ``{"detail", "code"}`` (M1)
- anything else → 500 ``internal error``; only the exception's class name is logged
- a strict audit sink that cannot take the event (:class:`~dps.audit.AuditUnavailable`) → 503
  ``audit unavailable``: nothing the engine produced is returned

Every outcome, rejections included, is counted and audited (value-free). The audited and
measured ``route`` is the route **template** (``/api/v1/jobs/{job_id}``), never the concrete path,
and the audit event carries the HTTP status the request answers with.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import re
import time
import uuid
from collections import Counter
from collections.abc import AsyncIterable, AsyncIterator, Awaitable, Callable
from typing import Any, overload

import structlog
from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse

from dps.audit import AuditEvent, AuditUnavailable
from dps.auth import coded_http_exception_handler as coded_http_exception_handler  # one handler
from dps.engine.policy import preset_names
from dps.engine.service import EngineError, Payload, VerificationFailed
from dps.errors import CodedError
from dps.observability import LATENCY, REQUESTS, get_logger

logger = get_logger(__name__)

TRACE_HEADER = "X-Request-ID"
#: Deepest JSON nesting accepted; the engine walks documents recursively.
MAX_DEPTH = 100

#: A caller's request id is echoed (and logged) only when it is an identifier; anything else —
#: free text, a value, a control character — is replaced by a generated id.
_TRACE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
#: The shape of a preset name; a string policy of this shape that names no preset is ``unknown``.
_PRESET_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


def route_template(request: Request) -> str:
    """The matched route's path template (``/api/v1/jobs/{job_id}``), else the request path."""
    path = getattr(request.scope.get("route"), "path", None)
    return path if isinstance(path, str) else request.url.path


def trace_id(request: Request) -> str:
    """The caller's ``X-Request-ID`` when it is a safe identifier, else a fresh 16-hex id."""
    given = request.headers.get(TRACE_HEADER, "")
    return given if _TRACE_ID.fullmatch(given) else uuid.uuid4().hex[:16]


class CodedHTTPException(HTTPException):
    """An HTTPException rendered as ``{"detail", "code"}`` (contract §8.2).

    One wire format, several raise sites: this class (M1 routes), ``dps.auth``'s
    ``CodedAuthHTTPException`` and M2's ``DpsHttpError`` (``{"detail", "code", **extra}``). Every
    coded ``HTTPException`` is rendered by the one handler :func:`coded_http_exception_handler`,
    which ``create_app`` registers for all ``HTTPException``s.
    """

    def __init__(
        self,
        status_code: int,
        code: str,
        detail: str,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(status_code=status_code, detail=detail, headers=headers)
        self.code = code


def policy_label(policy: str | dict[str, Any] | None, default: str) -> str:
    """What audit and logs record for a policy: a preset name or ``<preset>+inline:<hash12>``.

    Never the inline body (an inline policy may hold deny-list values or declared paths) and
    never a name the caller typed: a string that names no preset is ``unknown`` when it has a
    preset name's shape and ``invalid`` otherwise; an inline policy is labelled by its preset
    when that is a shipped preset, else ``custom``, plus a 12-hex hash of its canonical JSON so
    two calls with the same inline policy correlate.
    """
    if policy is None:
        return default
    if isinstance(policy, str):
        if policy in preset_names():
            return policy
        return "unknown" if _PRESET_NAME.fullmatch(policy) else "invalid"
    canon = json.dumps(policy, sort_keys=True, separators=(",", ":"), default=str)
    preset = policy.get("preset")
    base = preset if isinstance(preset, str) and preset in preset_names() else "custom"
    return f"{base}+inline:{hashlib.sha256(canon.encode('utf-8')).hexdigest()[:12]}"


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


def result_counts(result: Any) -> dict[str, Any]:
    """The audit counts of an engine result (value-free: counts, type and method names)."""
    report = getattr(result, "report", None)
    findings = getattr(result, "findings", None) or []
    if report is not None and hasattr(report, "findings_total"):
        verification = report.verification
        return {
            "findings_total": report.findings_total,
            "findings_by_type": dict(report.findings_by_type),
            "methods_used": dict(report.methods_used),
            "verification_passed": verification.passed if verification is not None else None,
            "reversible_values": report.reversible_values,
        }
    # detect results, and reports that are not engine reports (schema synthesis)
    counts: dict[str, Any] = {
        "findings_total": len(findings),
        "findings_by_type": dict(Counter(f.entity_type for f in findings)),
    }
    verification = getattr(report, "verification", None)
    if verification is not None:
        counts["verification_passed"] = verification.passed
    return counts


def _audit_event(
    request: Request,
    *,
    mode: str,
    policy: str,
    counts: dict[str, Any],
    duration_ms: float,
    outcome: str,
    tid: str,
    status_code: int,
) -> AuditEvent:
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
        CodedHTTPException: 503 ``audit_unavailable`` when a strict sink refuses the event. Any
            other sink failure is logged (type only) and never fails the request.
    """
    try:
        request.app.state.audit.write(_audit_event(request, **fields))
    except AuditUnavailable as exc:
        raise CodedHTTPException(503, "audit_unavailable", "audit unavailable") from exc
    except Exception as exc:  # noqa: BLE001 - a non-strict sink failure never takes the request down
        logger.warning("audit_write_failed", error=type(exc).__name__)


def begin_call(request: Request) -> str:
    """Start an audited call: the trace id (set on ``request.state``, bound to the log context).

    A replay (:func:`with_idempotency`) keeps the original call's trace id. The bound ``route``
    is the route template (:func:`route_template`), never the concrete path.
    """
    tid = getattr(request.state, "replay_trace_id", None) or trace_id(request)
    request.state.trace_id = tid
    structlog.contextvars.bind_contextvars(trace_id=tid, route=route_template(request))
    return tid


def end_call(
    request: Request,
    *,
    mode: str,
    policy: str,
    tid: str,
    started: float,
    outcome: str,
    counts: dict[str, Any] | None = None,
    status_code: int | None = None,
) -> None:
    """Finish an audited call: request metrics and exactly one value-free audit event.

    ``started`` is the call's ``time.perf_counter()`` at :func:`begin_call`; ``counts`` are
    :func:`result_counts` (or a stream's sums), ``None`` for a call that produced nothing;
    ``status_code`` is the HTTP status the request answers with (default: the route's success
    status). A successful replay is audited with outcome ``replayed``.

    Raises:
        CodedHTTPException: 503 ``audit_unavailable`` when a strict audit sink refuses the event
            (the caller returns nothing the engine produced).
    """
    if outcome == "ok" and getattr(request.state, "replay_trace_id", None):
        outcome = "replayed"
    if status_code is None:
        status_code = getattr(request.scope.get("route"), "status_code", None) or 200
    route = route_template(request)
    duration = (time.perf_counter() - started) * 1000
    REQUESTS.labels(route, outcome).inc()
    LATENCY.labels(route).observe(duration / 1000)
    _audit(
        request,
        mode=mode,
        policy=policy,
        counts=counts or {},
        duration_ms=duration,
        outcome=outcome,
        tid=tid,
        status_code=status_code,
    )


def map_engine_error(exc: Exception) -> tuple[str, HTTPException]:
    """The audit outcome and the HTTP error for an exception raised by an engine call.

    The engine's messages name rules, never values; an unexpected exception is logged by its
    class name only and becomes a value-free 500.
    """
    if isinstance(exc, VerificationFailed):  # before EngineError, which it subclasses
        return "verification_failed", CodedHTTPException(422, "leakage", str(exc))
    if isinstance(exc, EngineError):
        return "bad_request", CodedHTTPException(400, "bad_request", str(exc))
    if isinstance(exc, CodedError):
        return exc.code, CodedHTTPException(exc.status, exc.code, exc.detail)
    if isinstance(exc, HTTPException):
        return "rejected", exc
    logger.error("engine_error", error=type(exc).__name__)
    return "error", HTTPException(status_code=500, detail="internal error")


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
    tid = begin_call(request)
    start = time.perf_counter()
    outcome = "ok"
    status_code: int | None = None  # the route's success status unless an error says otherwise
    result: T | None = None
    try:
        raw = call()
        result = await raw if inspect.isawaitable(raw) else raw
        return result, tid
    except Exception as exc:  # noqa: BLE001 - mapped to a coded, value-free HTTP error
        outcome, error = map_engine_error(exc)
        status_code = error.status_code
        if error is exc:
            raise
        raise error from None
    finally:
        try:
            # A strict sink's 503 replaces the result (or the error) being returned.
            end_call(
                request,
                mode=mode,
                policy=policy_name,
                tid=tid,
                started=start,
                outcome=outcome,
                counts=result_counts(result),
                status_code=status_code,
            )
        finally:
            structlog.contextvars.unbind_contextvars("trace_id", "route")


async def with_idempotency[T](
    request: Request,
    body: bytes,
    run: Callable[[], Awaitable[T]],
    *,
    mode: str = "-",
    policy: str = "-",
) -> T:
    """Honour ``Idempotency-Key`` around a Layer 1 call (contract §9, decision D2).

    ``body`` is what the call's fingerprint covers: the raw request body (multipart: the file's
    digest and the ``request`` field). A retry with the same key and body within the TTL re-runs
    ``run`` as a replay — the original trace id, audit outcome ``replayed``, header
    ``Idempotent-Replay: true``; nothing from the response is cached. A first call is recorded
    after it succeeds (a stream, when it ends with its report line). ``mode`` and ``policy`` name
    the audit event of a refused header.

    Raises:
        CodedHTTPException: 422 ``idempotency_key_invalid`` (malformed header) or
            ``idempotency_key_reused`` (same key, another body) — audited, nothing run.
    """
    # Imported here: dps.layers.api.idempotency builds on this module's CodedHTTPException.
    from dps.layers.api.idempotency import (
        IdemEntry,
        IdempotencyCache,
        IdempotencyConflict,
        parse_idempotency_key,
        scope_of,
    )

    cache: IdempotencyCache = request.app.state.idempotency
    key: str | None = None
    entry: IdemEntry | None = None
    scope = digest = ""
    try:
        key = parse_idempotency_key(request)
        if key is not None and cache.enabled:
            scope = scope_of(request, key)
            digest = hashlib.sha256(body).hexdigest()
            try:
                entry = cache.get(scope, digest)
            except IdempotencyConflict:
                raise CodedHTTPException(
                    422,
                    "idempotency_key_reused",
                    "this Idempotency-Key was already used with a different request body",
                ) from None
    except CodedHTTPException as exc:
        tid = begin_call(request)
        try:
            end_call(
                request,
                mode=mode,
                policy=policy,
                tid=tid,
                started=time.perf_counter(),
                outcome=exc.code,
                status_code=exc.status_code,
            )
        finally:
            structlog.contextvars.unbind_contextvars("trace_id", "route")
        raise
    if key is None or not cache.enabled:
        return await run()
    if entry is not None:
        request.state.replay_trace_id = entry.trace_id
    result = await run()
    if entry is not None:
        request.state.idempotent_replay = True  # the app's middleware sets the response header
        return result

    def record() -> None:
        cache.put(scope, IdemEntry(digest, request.state.trace_id, cache.clock()))

    if isinstance(result, StreamingResponse):
        result.body_iterator = _record_when_complete(request, result.body_iterator, record)
    else:
        record()
    return result


async def _record_when_complete(
    request: Request, lines: AsyncIterable[Any], record: Callable[[], None]
) -> AsyncIterator[Any]:
    """Pass a stream through; record its idempotency entry only when it ended ``ok``."""
    async for line in lines:
        yield line
    if getattr(request.state, "stream_outcome", None) == "ok":
        record()
