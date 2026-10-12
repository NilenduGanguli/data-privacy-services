"""NDJSON streaming of large arrays on ``/detect``, ``/obfuscate``, ``/synthesize`` and
``/reveal``.

Spec §6 ("arrays stream as NDJSON when large"), contract §9, M1 plan Task 19A. The JSON routes
stay as M0 built them; NDJSON is opt-in per request:

- **Response streaming** — a JSON request whose ``data`` is an array, sent with
  ``Accept: application/x-ndjson``.
- **Request streaming** — a body with ``Content-Type: application/x-ndjson``: line 1 is the
  envelope (``{"policy", "options"}``; for ``/reveal`` ``{"key_material"}``), every further line is
  one array item (for ``/reveal``: an item line of an earlier obfuscate stream,
  ``{"result", "reveal_hints"}``). An NDJSON request always gets an NDJSON response.

Each item is processed as its own document: its line equals what the JSON route returns for
``{"data": item, ...}`` with the stream's key material or seed, which is resolved once per stream
(the caller's, or generated up front and passed to every item through
``options.consistency``). Items run in chunks of ``ndjson_chunk_items`` on a worker thread, so the
event loop keeps serving. Response lines (``?`` = by mode)::

    {"type": "header", "trace_id", "mode", "policy": {"name", "hash"},
     "key_material"?, "synthesis_seed"?}
    {"type": "item", "index", "result"?, "findings"?, "reveal_hints"?,
     "record_sensitivity"?, "revealed"?}
    {"type": "report", "items",
     "report": {"findings_total", "findings_by_type", "methods_used", "verification"}}

**Fail closed:** an item that fails verification under ``strict`` (or any engine error) produces
``{"type": "error", "index", "code", "detail"}`` and ends the stream; no later item is processed.
Request errors (envelope, limits, policy, key material) are answered with a coded HTTP error
before anything is streamed. One audit event per stream (counts summed). Error details name a
line or item number and a rule, never the content of a line.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections import Counter
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, TypeGuard, overload

import structlog
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.datastructures import Headers
from starlette.routing import Match
from starlette.types import Scope

from dps.auth import require_scope
from dps.config import Settings
from dps.engine.policy import Policy
from dps.engine.service import (
    DetectResponse,
    EngineError,
    Payload,
    PrivacyEngine,
    RequestOptions,
    RevealResponse,
    TransformResponse,
    VerificationFailed,
)
from dps.engine.synth.surrogates import generate_synthesis_seed, seed_from_caller
from dps.engine.transform.crypto import (
    KeyMaterial,
    generate_key_material,
    key_material_from_caller,
    wrap_key_material,
)
from dps.errors import CodedError
from dps.layers.api.common import (
    MAX_DEPTH,
    CodedHTTPException,
    _measure,
    begin_call,
    end_call,
    map_engine_error,
    payload_from,
    policy_label,
    with_idempotency,
)
from dps.observability import get_logger
from dps.ratelimit import enforce_rate_limit

logger = get_logger(__name__)

NDJSON = "application/x-ndjson"
StreamMode = Literal["detect", "obfuscate", "synthesize", "reveal"]
#: The routes that accept an NDJSON body (and its larger body limit, ``max_ndjson_mb``).
STREAM_ROUTES = frozenset(
    {"/api/v1/detect", "/api/v1/obfuscate", "/api/v1/synthesize", "/api/v1/reveal"}
)
#: Documents the NDJSON alternative on the JSON routes (merged into FastAPI's operation).
OPENAPI_NDJSON: dict[str, Any] = {
    "requestBody": {
        "content": {
            NDJSON: {
                "schema": {
                    "type": "string",
                    "description": (
                        "Line 1: the envelope {policy, options} (/reveal: {key_material}); then "
                        "one array item per line (/reveal: an obfuscate stream's item lines)."
                    ),
                }
            }
        }
    },
    "responses": {
        "200": {
            "content": {
                NDJSON: {
                    "schema": {
                        "type": "string",
                        "description": (
                            "Sent for an NDJSON body, or a JSON array with Accept: "
                            "application/x-ndjson: a header line, one item line per element, "
                            "then a report line (or an error line that ends the stream)."
                        ),
                    }
                }
            }
        }
    },
}


class NdjsonEnvelope(BaseModel):
    """Line 1 of an NDJSON request: everything of the JSON request except ``data``."""

    model_config = ConfigDict(hide_input_in_errors=True, extra="forbid")

    policy: str | dict[str, Any] | None = None
    options: RequestOptions = Field(default_factory=RequestOptions)
    key_material: dict[str, Any] | None = None  # /reveal only


def _media_type(value: str) -> str:
    return value.split(";", 1)[0].strip().lower()


def is_ndjson_request(request: Request) -> bool:
    """True when the body's ``Content-Type`` is ``application/x-ndjson``."""
    return _media_type(request.headers.get("content-type", "")) == NDJSON


def wants_ndjson(request: Request) -> bool:
    """True when ``Accept`` lists ``application/x-ndjson`` with a non-zero quality."""
    for part in request.headers.get("accept", "").split(","):
        media, *params = (p.strip() for p in part.split(";"))
        if media.lower() != NDJSON:
            continue
        quality = 1.0
        for param in params:
            name, _, value = param.partition("=")
            if name.strip().lower() == "q":
                try:
                    quality = float(value)
                except ValueError:
                    quality = 0.0
        if quality > 0:
            return True
    return False


def streams_array(data: Any, text: str | None, request: Request) -> TypeGuard[list[Any]]:
    """True for a JSON request whose ``data`` is an array, sent with an NDJSON ``Accept``."""
    return isinstance(data, list) and text is None and wants_ndjson(request)


class NdjsonBodyRoute(APIRoute):
    """A route that matches only requests whose body is NDJSON.

    It is registered before the JSON route of the same path, so an NDJSON body never reaches
    FastAPI's JSON body parsing, and a JSON body never reaches the NDJSON handler.
    """

    def matches(self, scope: Scope) -> tuple[Match, Scope]:
        """The usual path and method match, narrowed to ``Content-Type: application/x-ndjson``."""
        match, child = super().matches(scope)
        if match is Match.NONE:
            return match, child
        if _media_type(Headers(scope=scope).get("content-type", "")) != NDJSON:
            return Match.NONE, {}
        return match, child


def _invalid(detail: str) -> CodedHTTPException:
    return CodedHTTPException(400, "ndjson_invalid", detail)


def _too_many(limit: int) -> CodedHTTPException:
    return CodedHTTPException(413, "ndjson_too_large", f"the stream exceeds {limit} items")


class NdjsonItems(Sequence[Any]):
    """The item lines of an NDJSON body, parsed on access.

    The body is held once, as the bytes the server received, with the span of each item line; an
    item is decoded when a chunk needs it, so a stream never holds every parsed item at once (a
    256 MB body would otherwise expand several-fold in memory).
    """

    def __init__(self, body: bytes, spans: list[tuple[int, int]]) -> None:
        self._body = body
        self._spans = spans

    def __len__(self) -> int:
        return len(self._spans)

    @overload
    def __getitem__(self, index: int) -> Any: ...

    @overload
    def __getitem__(self, index: slice) -> list[Any]: ...

    def __getitem__(self, index: int | slice) -> Any:
        if isinstance(index, slice):
            return [self._parse(span) for span in self._spans[index]]
        return self._parse(self._spans[index])

    def _parse(self, span: tuple[int, int]) -> Any:
        return json.loads(self._body[span[0] : span[1]].decode("utf-8"))


async def read_ndjson(request: Request, settings: Settings) -> tuple[NdjsonEnvelope, Sequence[Any]]:
    """Parse an NDJSON body: the envelope line, then one JSON item per line.

    Blank lines are skipped; line numbers in errors count every line (1-based). Every item line is
    checked here (UTF-8, JSON, not ``null``) and parsed again, chunk by chunk, while it streams
    (:class:`NdjsonItems`).

    Raises:
        CodedHTTPException: 400 ``ndjson_invalid`` (no envelope, a line that is not UTF-8 JSON, a
            ``null`` item — the detail names the line number only, never its content);
            413 ``ndjson_too_large`` above ``ndjson_max_items`` items.
    """
    raw = await request.body()
    lines: list[tuple[int, int, int]] = []  # (line number, start, end) of each non-blank line
    start, number = 0, 1
    while start <= len(raw):
        end = raw.find(b"\n", start)
        end = len(raw) if end < 0 else end
        if raw[start:end].strip():
            lines.append((number, start, end))
        start, number = end + 1, number + 1
    if not lines:
        raise _invalid("line 1: the envelope is missing")
    if len(lines) - 1 > settings.ndjson_max_items:
        raise _too_many(settings.ndjson_max_items)
    first_no, first_start, first_end = lines[0]
    try:
        envelope = NdjsonEnvelope.model_validate_json(raw[first_start:first_end])
    except ValidationError:
        raise _invalid(
            f"line {first_no}: the envelope must be a JSON object with policy and options "
            "(/reveal: key_material)"
        ) from None
    items = NdjsonItems(raw, [(a, b) for _, a, b in lines[1:]])
    for n, (line_no, _, _) in enumerate(lines[1:]):
        try:
            item = items[n]
        except (ValueError, RecursionError):  # UnicodeDecodeError and JSONDecodeError included
            raise _invalid(f"line {line_no} is not valid UTF-8 JSON") from None
        if item is None:
            raise _invalid(f"line {line_no}: an item must not be null")
    return envelope, items


def resolve_stream_material(
    engine: PrivacyEngine,
    mode: str,
    policy_ref: str | dict[str, Any] | None,
    options: RequestOptions,
) -> RequestOptions:
    """Options whose ``consistency`` carries the stream's key material (obfuscate) or seed
    (synthesize): the caller's, else generated once — so every item shares one key and one seed.

    ``wrap_key`` is cleared in the returned options: the stream wraps its key once, in the header.

    Raises:
        EngineError: the policy, the caller's key material or seed, or ``wrap_key`` is invalid.
    """
    if mode not in ("obfuscate", "synthesize"):
        return options
    policy = engine.policy(policy_ref)
    consistency = options.consistency
    if mode == "obfuscate":
        # An unusable wrap_key fails before any work, as in the JSON route.
        engine._wrap_pem(options)
        scope = consistency.scope or policy.consistency.scope
        try:
            key = (
                key_material_from_caller(consistency.key_material)
                if consistency.key_material
                else generate_key_material(scope)
            )
        except ValueError as exc:
            raise EngineError(f"invalid key material: {exc}") from None
        material = {"key_material": key.model_dump(mode="json")}
    else:
        try:
            seed = (
                seed_from_caller(consistency.synthesis_seed)
                if consistency.synthesis_seed
                else generate_synthesis_seed()
            )
        except ValueError as exc:
            raise EngineError(f"invalid synthesis seed: {exc}") from None
        material = {"synthesis_seed": seed.model_dump(mode="json")}
    return options.model_copy(
        update={"consistency": consistency.model_copy(update=material), "wrap_key": None}
    )


def _dumps(obj: dict[str, Any]) -> bytes:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"


@dataclass
class _Totals:
    """Counts summed over a stream's items (value-free)."""

    items: int = 0
    findings_total: int = 0
    findings_by_type: Counter[str] = field(default_factory=Counter)
    methods_used: Counter[str] = field(default_factory=Counter)
    reversible_values: int = 0
    revealed: int = 0
    verified: bool | None = None
    checked: int = 0

    def add(self, result: DetectResponse | TransformResponse | RevealResponse) -> None:
        self.items += 1
        if isinstance(result, RevealResponse):
            self.revealed += result.revealed
            return
        report = result.report
        self.findings_total += report.findings_total
        self.findings_by_type.update(report.findings_by_type)
        self.methods_used.update(report.methods_used)
        self.reversible_values += report.reversible_values
        if report.verification is not None:
            self.verified = report.verification.passed and self.verified is not False
            self.checked += report.verification.checked

    def merge(self, other: _Totals) -> None:
        self.items += other.items
        self.findings_total += other.findings_total
        self.findings_by_type.update(other.findings_by_type)
        self.methods_used.update(other.methods_used)
        self.reversible_values += other.reversible_values
        self.revealed += other.revealed
        self.checked += other.checked
        if other.verified is not None:
            self.verified = other.verified and self.verified is not False

    def report(self, mode: str) -> dict[str, Any]:
        out: dict[str, Any] = {
            "findings_total": self.findings_total,
            "findings_by_type": dict(sorted(self.findings_by_type.items())),
            "methods_used": dict(sorted(self.methods_used.items())),
            "verification": (
                None
                if self.verified is None
                else {"passed": self.verified, "checked": self.checked}
            ),
        }
        if mode == "obfuscate":
            out["reversible_values"] = self.reversible_values
        if mode == "reveal":
            out["revealed"] = self.revealed
        return out

    def audit_counts(self) -> dict[str, Any]:
        return {
            "findings_total": self.findings_total,
            "findings_by_type": dict(self.findings_by_type),
            "methods_used": dict(self.methods_used),
            "verification_passed": self.verified,
            "reversible_values": self.reversible_values,
        }


@dataclass
class _Failure:
    index: int
    code: str
    detail: str
    outcome: str


def _failure(index: int, exc: Exception) -> _Failure:
    """The value-free error line for an item (engine messages name rules, never values)."""
    if isinstance(exc, VerificationFailed):
        return _Failure(index, "leakage", str(exc), "verification_failed")
    if isinstance(exc, EngineError):
        return _Failure(index, "bad_request", str(exc), "bad_request")
    if isinstance(exc, CodedError):
        return _Failure(index, exc.code, exc.detail, exc.code)
    logger.error("stream_item_error", error=type(exc).__name__)
    return _Failure(index, "internal_error", "internal error", "error")


#: Runs one chunk on a worker thread: ``(first index, all items, chunk size) -> (lines, totals,
#: failure)``; the chunk's items are read (for an NDJSON body: parsed) on that thread.
ChunkRunner = Callable[[int, Sequence[Any], int], tuple[list[bytes], _Totals, _Failure | None]]


@dataclass
class _Stream:
    """A validated stream, ready to run: its header, its items and how a chunk is processed."""

    mode: str
    header: dict[str, Any]
    items: Sequence[Any]
    chunk_items: int
    run_chunk: ChunkRunner


def _item_line(index: int, result: Any, mode: str, options: RequestOptions) -> bytes:
    line: dict[str, Any] = {"type": "item", "index": index}
    if isinstance(result, DetectResponse):
        line["findings"] = [f.model_dump(mode="json") for f in result.findings]
        line["record_sensitivity"] = str(result.record_sensitivity)
    elif isinstance(result, TransformResponse):
        line["result"] = result.result
        line["findings"] = [f.model_dump(mode="json") for f in result.findings]
        if mode == "obfuscate":
            line["reveal_hints"] = [h.model_dump(mode="json") for h in result.reveal_hints]
        if options.return_segments and result.segments is not None:
            line["segments"] = [s.model_dump(mode="json") for s in result.segments]
    elif isinstance(result, RevealResponse):
        line["result"] = result.result
        line["revealed"] = result.revealed
    return _dumps(line)


def _engine_runner(
    engine: PrivacyEngine,
    mode: str,
    policy_ref: str | dict[str, Any] | None,
    options: RequestOptions,
    key_material: dict[str, Any] | None,
) -> ChunkRunner:
    """Each item through the engine operation of ``mode``, in order, stopping at a failure."""

    def run_chunk(
        offset: int, everything: Sequence[Any], size: int
    ) -> tuple[list[bytes], _Totals, _Failure | None]:
        items = everything[offset : offset + size]
        lines: list[bytes] = []
        totals = _Totals()
        loop = asyncio.new_event_loop() if mode == "synthesize" else None
        try:
            for n, item in enumerate(items):
                index = offset + n
                try:
                    result: DetectResponse | TransformResponse | RevealResponse
                    if mode == "detect":
                        result = engine.detect(Payload(data=item), policy_ref, options)
                    elif mode == "obfuscate":
                        result = engine.obfuscate(Payload(data=item), policy_ref, options)
                    elif mode == "synthesize":
                        assert loop is not None
                        result = loop.run_until_complete(
                            engine.synthesize(Payload(data=item), policy_ref, options)
                        )
                    else:
                        assert key_material is not None
                        result = engine.reveal(
                            Payload(data=item["result"]),
                            key_material,
                            item.get("reveal_hints") or [],
                        )
                except Exception as exc:  # noqa: BLE001 - every item failure ends the stream
                    return lines, totals, _failure(index, exc)
                totals.add(result)
                lines.append(_item_line(index, result, mode, options))
        finally:
            if loop is not None:
                loop.close()
        return lines, totals, None

    return run_chunk


def _check_items(items: Sequence[Any], settings: Settings, mode: str) -> None:
    """Every item obeys M0's per-document limits (values, nesting); reveal items are
    ``{"result", "reveal_hints"}`` objects."""
    if len(items) > settings.ndjson_max_items:
        raise _too_many(settings.ndjson_max_items)
    for index, item in enumerate(items):
        document = item
        if mode == "reveal":
            if not isinstance(item, dict) or item.get("result") is None:
                raise _invalid(f"item {index}: a reveal item is an object with a result")
            if not isinstance(item.get("reveal_hints") or [], list):
                raise _invalid(f"item {index}: reveal_hints must be a list")
            document = item["result"]
        if document is None:
            raise _invalid(f"item {index} must not be null")
        leaves, too_deep = _measure(document, settings.max_items, MAX_DEPTH)
        if too_deep:
            raise CodedHTTPException(
                422, "ndjson_invalid", f"item {index} nests deeper than {MAX_DEPTH}"
            )
        if leaves > settings.max_items:
            raise CodedHTTPException(
                413, "payload_too_large", f"item {index} exceeds {settings.max_items} values"
            )


def _header(
    engine: PrivacyEngine,
    mode: str,
    tid: str,
    envelope: NdjsonEnvelope,
    item_options: RequestOptions,
) -> dict[str, Any]:
    header: dict[str, Any] = {"type": "header", "trace_id": tid, "mode": mode}
    if mode == "reveal":
        return header
    policy: Policy = engine.policy(envelope.policy)
    header["policy"] = {"name": policy.name, "hash": policy.config_hash()}
    consistency = item_options.consistency
    if mode == "obfuscate":
        assert consistency.key_material is not None
        key = KeyMaterial.model_validate(consistency.key_material)
        pem = engine._wrap_pem(envelope.options)
        if pem is not None:
            try:
                key = wrap_key_material(key, pem)
            except ValueError as exc:
                raise EngineError(f"wrap_key: {exc}") from None
        header["key_material"] = key.model_dump(mode="json")
    elif mode == "synthesize":
        scope = envelope.options.consistency.scope or policy.consistency.scope
        if scope != "request":
            header["synthesis_seed"] = consistency.synthesis_seed
    return header


def _prepare(
    request: Request, mode: StreamMode, tid: str, envelope: NdjsonEnvelope, items: Sequence[Any]
) -> _Stream:
    engine: PrivacyEngine = request.app.state.engine
    settings: Settings = engine.settings
    _check_items(items, settings, mode)
    key_material: dict[str, Any] | None = None
    if mode == "reveal":
        if not envelope.key_material:
            raise _invalid("line 1: the reveal envelope must carry key_material")
        try:
            key_material_from_caller(envelope.key_material)
        except ValueError as exc:
            raise EngineError(f"invalid reveal request: {exc}") from None
        key_material = envelope.key_material
        options = envelope.options
    else:
        options = resolve_stream_material(engine, mode, envelope.policy, envelope.options)
        engine.policy(envelope.policy)  # an unknown or invalid policy fails before streaming
    return _Stream(
        mode=mode,
        header=_header(engine, mode, tid, envelope, options),
        items=items,
        chunk_items=settings.ndjson_chunk_items,
        run_chunk=_engine_runner(engine, mode, envelope.policy, options, key_material),
    )


async def _lines(request: Request, stream: _Stream, policy: str, tid: str) -> AsyncIterator[bytes]:
    """Header, item lines chunk by chunk (each chunk on a worker thread), then the report — or
    the error line that ends the stream. Writes the stream's one audit event when it ends."""
    started = time.perf_counter()
    structlog.contextvars.bind_contextvars(trace_id=tid, route=request.url.path)
    totals = _Totals()
    outcome = "aborted"  # the client went away before the stream finished
    try:
        yield _dumps(stream.header)
        for offset in range(0, len(stream.items), stream.chunk_items):
            lines, counts, failure = await asyncio.to_thread(
                stream.run_chunk, offset, stream.items, stream.chunk_items
            )
            totals.merge(counts)
            for line in lines:
                yield line
            if failure is not None:
                outcome = failure.outcome
                yield _dumps(
                    {
                        "type": "error",
                        "index": failure.index,
                        "code": failure.code,
                        "detail": failure.detail,
                    }
                )
                return
        outcome = "ok"
        yield _dumps(
            {"type": "report", "items": totals.items, "report": totals.report(stream.mode)}
        )
    finally:
        request.state.stream_outcome = outcome  # with_idempotency records only an ``ok`` stream
        end_call(
            request,
            mode=stream.mode,
            policy=policy,
            tid=tid,
            started=started,
            outcome=outcome,
            counts=totals.audit_counts(),
        )
        structlog.contextvars.unbind_contextvars("trace_id", "route")


@dataclass
class _Audit:
    """What the stream's audit event names before the stream exists (set as soon as known)."""

    policy: str = "-"


async def _start(
    request: Request,
    *,
    mode: StreamMode,
    prepare: Callable[[str, _Audit], Awaitable[_Stream]],
) -> StreamingResponse:
    """Load and validate the stream inside an audited region; a request error is answered as
    an HTTP error (and audited) before anything is streamed."""
    tid = begin_call(request)
    started = time.perf_counter()
    audit = _Audit()
    try:
        stream = await prepare(tid, audit)
    except Exception as exc:  # noqa: BLE001 - mapped to a coded, value-free HTTP error
        outcome, error = map_engine_error(exc)
        end_call(request, mode=mode, policy=audit.policy, tid=tid, started=started, outcome=outcome)
        structlog.contextvars.unbind_contextvars("trace_id", "route")
        if error is exc:
            raise
        raise error from None
    structlog.contextvars.unbind_contextvars("trace_id", "route")
    return StreamingResponse(_lines(request, stream, audit.policy, tid), media_type=NDJSON)


def _label(request: Request, mode: str, envelope: NdjsonEnvelope) -> str:
    if mode == "reveal":
        return "-"
    return policy_label(envelope.policy, request.app.state.settings.default_policy)


async def stream_lines(
    request: Request,
    *,
    mode: StreamMode,
    envelope: NdjsonEnvelope,
    items: Sequence[Any],
) -> StreamingResponse:
    """Stream ``items`` (a JSON request's ``data`` array) as NDJSON lines."""

    async def prepare(tid: str, audit: _Audit) -> _Stream:
        audit.policy = _label(request, mode, envelope)
        return _prepare(request, mode, tid, envelope, items)

    return await _start(request, mode=mode, prepare=prepare)


async def stream_request(request: Request, *, mode: StreamMode) -> StreamingResponse:
    """Stream an NDJSON request body (envelope line + item lines)."""

    async def prepare(tid: str, audit: _Audit) -> _Stream:
        envelope, items = await read_ndjson(request, request.app.state.settings)
        audit.policy = _label(request, mode, envelope)
        return _prepare(request, mode, tid, envelope, items)

    return await _start(request, mode=mode, prepare=prepare)


_ELEMENT = re.compile(r"^\$\[(\d+)\]")


async def stream_reveal_array(
    request: Request,
    *,
    data: list[Any],
    key_material: dict[str, Any],
    reveal_hints: list[dict[str, Any]],
) -> StreamingResponse:
    """A JSON ``/reveal`` of an array, streamed.

    The hints of a JSON obfuscation address the whole array (``$[i]…``, possibly field-scoped to
    that path), so the array is revealed as one document — under M0's limits, as the JSON route
    does — and each element becomes an item line.
    """
    engine: PrivacyEngine = request.app.state.engine

    def run_chunk(
        offset: int, everything: Sequence[Any], size: int
    ) -> tuple[list[bytes], _Totals, _Failure | None]:
        totals = _Totals()
        try:
            result = engine.reveal(Payload(data=data), key_material, reveal_hints)
        except Exception as exc:  # noqa: BLE001 - one document: a failure ends the stream at 0
            return [], totals, _failure(offset, exc)
        per_item: Counter[int] = Counter()
        for hint in reveal_hints:
            match = _ELEMENT.match(str(hint.get("path", "")))
            if match:
                per_item[int(match.group(1))] += 1
        lines = []
        for index, element in enumerate(result.result):
            totals.items += 1
            totals.revealed += per_item[index]
            line = {"type": "item", "index": index, "result": element, "revealed": per_item[index]}
            lines.append(_dumps(line))
        return lines, totals, None

    async def prepare(tid: str, audit: _Audit) -> _Stream:
        if len(data) > engine.settings.ndjson_max_items:
            raise _too_many(engine.settings.ndjson_max_items)
        payload_from(request, data, None)  # M0's whole-document limits, as the JSON route
        try:
            key_material_from_caller(key_material)
        except ValueError as exc:
            raise EngineError(f"invalid reveal request: {exc}") from None
        return _Stream(
            mode="reveal",
            header={"type": "header", "trace_id": tid, "mode": "reveal"},
            items=data,
            chunk_items=max(1, len(data)),
            run_chunk=run_chunk,
        )

    return await _start(request, mode="reveal", prepare=prepare)


def add_ndjson_route(router: APIRouter, path: str, *, mode: StreamMode, scope: str) -> None:
    """Register the NDJSON-body twin of a JSON route (call it *before* the JSON route).

    Same scope and rate limit as the JSON route; hidden from OpenAPI, where the JSON route
    documents the ``application/x-ndjson`` alternative (:data:`OPENAPI_NDJSON`).
    """

    async def handler(request: Request) -> StreamingResponse:
        async def run() -> StreamingResponse:
            return await stream_request(request, mode=mode)

        return await with_idempotency(request, await request.body(), run, mode=mode)

    handler.__name__ = f"{mode}_ndjson"
    router.add_api_route(
        path,
        handler,
        methods=["POST"],
        dependencies=[Depends(require_scope(scope)), Depends(enforce_rate_limit)],
        include_in_schema=False,
        route_class_override=NdjsonBodyRoute,
        name=f"{mode}_ndjson",
    )
