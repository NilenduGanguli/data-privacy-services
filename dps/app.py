"""FastAPI application factory. Run with ``uvicorn dps.app:create_app --factory``.

Middleware order (outermost first): security headers → CORS → body-size guard → value-free
error guard → idempotent-replay header → FastAPI. Auth and rate limiting are per-route dependencies. The compiled console
(``frontend/dist``) is served from the same process with a strict CSP, as in the sibling services.
The body-size guard is per route: ``max_payload_mb`` for the JSON routes, ``max_document_mb`` plus
1 MiB of multipart overhead for ``/api/v1/documents/*``, ``max_ndjson_mb`` for an NDJSON body on
the four streaming routes. OCR readiness is probed at startup and,
when OCR is configured, re-probed every ``ocr_probe_interval_seconds`` (never required).

Nothing on the error paths quotes a request: 422 bodies list the field, the rule and the error
type (never the input), and an unexpected exception becomes a 500 ``internal error`` with only its
class name logged — it never reaches the server's traceback log, which could quote values.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from dps import __version__
from dps.audit import AuditSink, LogAuditSink, NullAuditSink
from dps.auth import API_KEY_HEADER, TENANT_HEADER, StaticKeyStore
from dps.config import Settings, get_settings
from dps.engine.service import PrivacyEngine
from dps.layers.api.common import (
    TRACE_HEADER,
    CodedHTTPException,
    coded_http_exception_handler,
)
from dps.layers.api.idempotency import IDEMPOTENCY_HEADER, REPLAY_HEADER, IdempotencyCache
from dps.layers.api.ndjson import NDJSON, STREAM_ROUTES
from dps.layers.api.routers import (
    detect,
    documents,
    entities,
    ops,
    policies,
    schema,
    transform,
)
from dps.layers.documents import DocumentPipeline
from dps.layers.documents.ocr import OcrClient, build_ocr_client, refresh_ocr_readiness
from dps.llm.gateway import get_gateway
from dps.observability import READINESS, configure_logging, get_logger
from dps.posture import assert_production_posture
from dps.ratelimit import RateLimiter

logger = get_logger(__name__)
FRONTEND_DIST = Path(__file__).resolve().parents[1] / "frontend" / "dist"
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
    "font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'"
)
HSTS = "max-age=31536000; includeSubDomains"
#: Room for the multipart envelope (boundaries, part headers, the ``request`` field) on top of
#: ``max_document_mb``; the documents router re-checks the file itself while reading it.
DOCUMENT_MULTIPART_OVERHEAD = 1 << 20
DOCUMENTS_PREFIX = "/api/v1/documents/"


class _SecurityHeaders:
    """Adds nosniff / DENY / no-referrer (and HSTS outside local) to every response."""

    def __init__(self, app: ASGIApp, *, hsts: bool) -> None:
        self.app, self.hsts = app, hsts

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers.setdefault("X-Content-Type-Options", "nosniff")
                headers.setdefault("X-Frame-Options", "DENY")
                headers.setdefault("Referrer-Policy", "no-referrer")
                if self.hsts:
                    headers.setdefault("Strict-Transport-Security", HSTS)
            await send(message)

        await self.app(scope, receive, send_with_headers)


class _BodyLimit:
    """413 ``payload_too_large`` for bodies over the route's limit — by ``Content-Length`` up
    front, or while streaming.

    The limit is ``max_bytes`` for the JSON routes, ``document_max_bytes`` for
    ``/api/v1/documents/*`` (multipart uploads) and ``ndjson_max_bytes`` for an
    ``application/x-ndjson`` body on the four streaming routes. A chunked body has no length
    header, so the receive channel counts bytes and raises a coded 413 once the limit is passed
    (FastAPI re-raises an ``HTTPException`` from body parsing, JSON and multipart alike).
    """

    def __init__(
        self, app: ASGIApp, *, max_bytes: int, document_max_bytes: int, ndjson_max_bytes: int
    ) -> None:
        self.app, self.max_bytes, self.document_max_bytes = app, max_bytes, document_max_bytes
        self.ndjson_max_bytes = ndjson_max_bytes

    def _limit(self, scope: Scope) -> int:
        path = str(scope.get("path", ""))
        if path.startswith(DOCUMENTS_PREFIX):
            return self.document_max_bytes
        if path in STREAM_ROUTES:
            media = Headers(scope=scope).get("content-type", "").split(";", 1)[0]
            if media.strip().lower() == NDJSON:
                return self.ndjson_max_bytes
        return self.max_bytes

    @staticmethod
    def _too_large(limit: int) -> str:
        return f"payload exceeds {limit} bytes"

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        limit = self._limit(scope)
        length = Headers(scope=scope).get("content-length")
        if length and length.isdigit() and int(length) > limit:
            response = JSONResponse(
                {"detail": self._too_large(limit), "code": "payload_too_large"}, status_code=413
            )
            await response(scope, receive, send)
            return
        received = 0

        async def counting_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise CodedHTTPException(413, "payload_too_large", self._too_large(limit))
            return message

        await self.app(scope, counting_receive, send)


class _ReplayHeader:
    """``Idempotent-Replay: true`` on the response of a replayed call (``with_idempotency`` marks
    the request state before the response starts)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        state = scope.setdefault("state", {})  # the dict ``request.state`` writes into

        async def send_with_flag(message: Message) -> None:
            if message["type"] == "http.response.start" and state.get("idempotent_replay"):
                MutableHeaders(scope=message)[REPLAY_HEADER] = "true"
            await send(message)

        await self.app(scope, receive, send_with_flag)


class _ValueFreeErrors:
    """Turns any exception that escapes the app into a value-free 500.

    Starlette's own error middleware re-raises after answering, and the server then logs the
    full traceback — whose messages can quote customer values (a response that failed
    validation, an engine error). Here only the exception's class name is logged.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = False

        async def tracking_send(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, receive, tracking_send)
        except Exception as exc:  # noqa: BLE001 - the point: nothing escapes with its message
            route = getattr(scope.get("route"), "path", "unmatched")
            logger.error("unhandled_error", error=type(exc).__name__, route=route)
            if not started:
                response = JSONResponse({"detail": "internal error"}, status_code=500)
                await response(scope, receive, send)


async def _validation_error(_: Request, exc: Exception) -> JSONResponse:
    """422 listing each error's type, location and rule — never the input or its context."""
    errors = exc.errors() if isinstance(exc, RequestValidationError) else []
    detail = [
        {"type": e.get("type", ""), "loc": list(e.get("loc", ())), "msg": e.get("msg", "")}
        for e in errors
    ]
    return JSONResponse({"detail": detail}, status_code=422)


async def _refresh_ocr(ocr: OcrClient, settings: Settings) -> None:
    """Probe OCR once. A probe that raises marks the component down (``error``) and logs the
    exception's class name only; it never takes the app or the probe loop down."""
    try:
        await refresh_ocr_readiness(ocr, settings)
    except Exception as exc:  # noqa: BLE001 - readiness is best effort; the name is enough
        READINESS.set("ocr", False, "error", provider=settings.ocr_provider, engine=ocr.engine)
        logger.warning("ocr_probe_error", engine=ocr.engine, error=type(exc).__name__)


async def _ocr_probe_loop(ocr: OcrClient, settings: Settings) -> None:
    """Re-probe OCR readiness forever; one failed probe never stops the loop."""
    while True:
        await asyncio.sleep(settings.ocr_probe_interval_seconds)
        await _refresh_ocr(ocr, settings)


class _HashedStatic(StaticFiles):
    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        if response.status_code == 200:
            response.headers["cache-control"] = "public, max-age=31536000, immutable"
        return response


def _mount_frontend(app: FastAPI) -> None:
    dist = FRONTEND_DIST.resolve()
    index, assets = dist / "index.html", dist / "assets"
    if not index.is_file():
        logger.info("frontend_disabled", reason="no dist")
        return
    if assets.is_dir():
        app.mount("/assets", _HashedStatic(directory=str(assets)), name="assets")

    def _index() -> FileResponse:
        return FileResponse(
            index,
            media_type="text/html",
            headers={"cache-control": "no-cache", "content-security-policy": CSP},
        )

    @app.get("/", include_in_schema=False)
    async def _root() -> FileResponse:
        return _index()

    @app.get("/{full_path:path}", include_in_schema=False, response_model=None)
    async def _spa(full_path: str) -> FileResponse | JSONResponse:
        if full_path == "api" or full_path.startswith("api/"):
            return JSONResponse({"detail": "API route not found"}, status_code=404)
        candidate = (dist / full_path).resolve()
        if not candidate.is_relative_to(dist):
            return _index()
        return FileResponse(candidate) if candidate.is_file() else _index()


def create_app(
    settings: Settings | None = None,
    *,
    engine: PrivacyEngine | None = None,
    audit_sink: AuditSink | None = None,
    ocr_client: OcrClient | None = None,
) -> FastAPI:
    """Build the app: posture check, key store, engine, OCR client, middleware, routers, console.

    ``ocr_client`` replaces the client built from the settings (tests inject a double); the app
    closes whichever client it uses when it stops.

    Raises:
        PostureError: staging/prod configuration violates the production posture.
        ValueError: ``DPS_API_KEYS`` is invalid (the message never quotes a key).
    """
    settings = settings or get_settings()
    configure_logging(settings.log_level, json_output=settings.env != "local")
    assert_production_posture(settings)
    key_store = StaticKeyStore.from_settings(settings)
    if engine is None:
        engine = PrivacyEngine(settings, gateway=get_gateway(settings))
    gateway = engine.gateway
    ocr = ocr_client or build_ocr_client(settings)
    if settings.auth_enabled and not len(key_store):
        logger.warning("auth_no_keys", reason="DPS_API_KEYS and DPS_BOOTSTRAP_API_KEY are unset")

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        llm_ok = gateway is not None and gateway.available()
        provider = (gateway.provider if gateway is not None else "") or "none"
        coin_tls = (
            gateway.coin_tls
            if gateway is not None
            else ("insecure" if settings.coin_tls_insecure else "verified")
        )
        READINESS.set("api-core", True, "up", version=__version__)
        READINESS.set(
            "llm-gateway",
            llm_ok,
            "configured" if llm_ok else "unconfigured",
            provider=provider,
            coin_tls=coin_tls,
        )
        if coin_tls == "insecure":
            # SEC-1: say so once per process, naming the settings only (posture.py gates prod).
            logger.warning(
                "coin_tls_insecure",
                component="llm-gateway",
                provider=provider,
                env=settings.env,
                reason="DPS_COIN_TLS_INSECURE=true: the COIN token mint skips TLS verification",
            )
        READINESS.set(
            "ner", engine.ner is not None, "presidio" if engine.ner is not None else "patterns-only"
        )
        READINESS.set(
            "fpe", engine.fpe_enabled, "ff1" if engine.fpe_enabled else "det_token fallback"
        )
        await _refresh_ocr(ocr, settings)
        logger.info(
            "startup",
            env=settings.env,
            auth=settings.auth_enabled,
            provider=provider,
            engine=ocr.engine,
        )
        probe = (
            asyncio.create_task(_ocr_probe_loop(ocr, settings))
            if ocr.configured() and settings.ocr_probe_interval_seconds > 0
            else None
        )
        try:
            yield
        finally:
            READINESS.set("api-core", False, "stopping", version=__version__)
            if probe is not None:
                probe.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await probe
            await ocr.aclose()

    app = FastAPI(
        title="Data Privacy Services",
        version=__version__,
        lifespan=lifespan,
        openapi_url="/api/v1/openapi.json",
        docs_url="/docs",
        redoc_url=None,
    )
    app.state.settings = settings
    app.state.engine = engine
    app.state.auth = {"store": key_store, "enabled": settings.auth_enabled}
    app.state.rate_limiter = RateLimiter(
        settings.rate_limit_rps, settings.rate_limit_burst, enabled=settings.rate_limit_enabled
    )
    app.state.audit = audit_sink or (LogAuditSink() if settings.audit_enabled else NullAuditSink())
    app.state.ocr = ocr
    app.state.documents = DocumentPipeline(engine, settings, ocr)
    # Fingerprints only (contract §9, decision D2): never key material, a seed or an output.
    app.state.idempotency = IdempotencyCache(
        ttl_seconds=settings.idempotency_ttl_seconds,
        max_entries=settings.idempotency_max_entries,
    )
    app.add_exception_handler(CodedHTTPException, coded_http_exception_handler)
    app.add_exception_handler(RequestValidationError, _validation_error)

    # add_middleware prepends: the last one added is the outermost.
    app.add_middleware(_ReplayHeader)
    app.add_middleware(_ValueFreeErrors)
    app.add_middleware(
        _BodyLimit,
        max_bytes=settings.max_payload_bytes,
        document_max_bytes=settings.max_document_bytes + DOCUMENT_MULTIPART_OVERHEAD,
        ndjson_max_bytes=settings.max_ndjson_bytes,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=[
            "Content-Type",
            API_KEY_HEADER,
            TENANT_HEADER,
            TRACE_HEADER,
            IDEMPOTENCY_HEADER,
        ],
        expose_headers=[REPLAY_HEADER],
    )
    app.add_middleware(_SecurityHeaders, hsts=settings.env != "local")

    app.include_router(ops.router)
    app.include_router(detect.router)
    app.include_router(transform.router)
    app.include_router(policies.router)
    app.include_router(documents.router)
    app.include_router(schema.router)
    app.include_router(entities.router)
    _mount_frontend(app)
    return app
