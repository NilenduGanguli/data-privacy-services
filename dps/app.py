"""FastAPI application factory. Run with ``uvicorn dps.app:create_app --factory``.

Middleware order (outermost first): security headers → CORS → body-size guard → value-free
error guard → FastAPI. Auth and rate limiting are per-route dependencies. The compiled console
(``frontend/dist``) is served from the same process with a strict CSP, as in the sibling services.

Nothing on the error paths quotes a request: 422 bodies list the field, the rule and the error
type (never the input), and an unexpected exception becomes a 500 ``internal error`` with only its
class name logged — it never reaches the server's traceback log, which could quote values.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.datastructures import Headers, MutableHeaders
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from dps import __version__
from dps.audit import AuditSink, LogAuditSink, NullAuditSink
from dps.auth import API_KEY_HEADER, TENANT_HEADER, StaticKeyStore
from dps.config import Settings, get_settings
from dps.engine.service import PrivacyEngine
from dps.layers.api.common import TRACE_HEADER
from dps.layers.api.routers import detect, ops, policies, transform
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
    """413 for bodies over ``max_bytes`` — by ``Content-Length`` up front, or while streaming.

    A chunked body has no length header, so the receive channel counts bytes and raises an
    ``HTTPException(413)`` once the limit is passed (FastAPI re-raises it from body parsing).
    """

    def __init__(self, app: ASGIApp, *, max_bytes: int) -> None:
        self.app, self.max_bytes = app, max_bytes

    def _too_large(self) -> str:
        return f"payload exceeds {self.max_bytes} bytes"

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        length = Headers(scope=scope).get("content-length")
        if length and length.isdigit() and int(length) > self.max_bytes:
            await JSONResponse({"detail": self._too_large()}, status_code=413)(scope, receive, send)
            return
        received = 0

        async def counting_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise StarletteHTTPException(status_code=413, detail=self._too_large())
            return message

        await self.app(scope, counting_receive, send)


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
) -> FastAPI:
    """Build the app: posture check, key store, engine, middleware, routers, console.

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
        logger.info("startup", env=settings.env, auth=settings.auth_enabled, provider=provider)
        yield
        READINESS.set("api-core", False, "stopping", version=__version__)

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
    app.add_exception_handler(RequestValidationError, _validation_error)

    # add_middleware prepends: the last one added is the outermost.
    app.add_middleware(_ValueFreeErrors)
    app.add_middleware(_BodyLimit, max_bytes=settings.max_payload_bytes)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", API_KEY_HEADER, TENANT_HEADER, TRACE_HEADER],
    )
    app.add_middleware(_SecurityHeaders, hsts=settings.env != "local")

    app.include_router(ops.router)
    app.include_router(detect.router)
    app.include_router(transform.router)
    app.include_router(policies.router)
    _mount_frontend(app)
    return app
