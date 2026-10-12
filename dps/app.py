"""FastAPI application factory. Run with ``uvicorn dps.app:create_app --factory``.

Middleware order (outermost first): security headers → CORS → body-size guard → value-free
error guard → FastAPI. Auth and rate limiting are per-route dependencies. The compiled console
(``frontend/dist``) is served from the same process with a strict CSP, as in the sibling services.

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
from typing import TYPE_CHECKING

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.datastructures import Headers, MutableHeaders
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from dps import __version__
from dps.audit import AuditSink, DbAuditSink, LogAuditSink, NullAuditSink
from dps.auth import API_KEY_HEADER, TENANT_HEADER, StaticKeyStore, coded_http_exception_handler
from dps.config import Settings, get_settings
from dps.engine.service import PrivacyEngine
from dps.layers.api.common import TRACE_HEADER
from dps.layers.api.routers import detect, ops, policies, transform
from dps.llm.gateway import get_gateway
from dps.observability import READINESS, configure_logging, get_logger
from dps.posture import PostureError, assert_production_posture
from dps.ratelimit import RateLimiter
from dps.secrets import load_kek

if TYPE_CHECKING:
    from dps.worker import Consumer

logger = get_logger(__name__)
FRONTEND_DIST = Path(__file__).resolve().parents[1] / "frontend" / "dist"
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
    "font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'"
)
HSTS = "max-age=31536000; includeSubDomains"
#: Seconds between reconnect attempts while the metadata database is down at boot.
DB_RETRY_SECONDS = 10.0
IDEMPOTENCY_HEADER = "Idempotency-Key"
ASSET_CACHE = "public, max-age=31536000, immutable"


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
        found = _dist_file(dist, full_path)
        if found is None or found == index:
            return _index()
        cached = assets.is_dir() and found.is_relative_to(assets.resolve())
        return FileResponse(found, headers={"cache-control": ASSET_CACHE} if cached else None)


def _dist_file(dist: Path, full_path: str) -> Path | None:
    """The file a console route asks for, if any.

    The console is built with ``base: './'``, so a page at ``/data-sources/jobs/<id>`` requests
    ``/data-sources/jobs/assets/<file>`` and ``/data-sources/jobs/favicon.svg``. Each path suffix
    is tried inside the dist root (``assets/<file>``, then ``<file>``); ``.``/``..`` segments and
    anything resolving outside the root are never served.
    """
    parts = [p for p in full_path.split("/") if p]
    if any(p in (".", "..") or "\\" in p for p in parts):
        return None
    for i in range(len(parts)):
        candidate = (dist / "/".join(parts[i:])).resolve()
        if candidate.is_relative_to(dist) and candidate.is_file():
            return candidate
    return None


class _JobPlatform:
    """The app's side of the job platform (M2): metadata DB, migrations mode, RLS posture, KEK,
    the DB audit sink, readiness ``db``/``jobs`` and — locally only — an embedded worker.

    A database that is down at boot leaves ``api-core`` ready, reports ``db: down`` and retries
    every :data:`DB_RETRY_SECONDS`; the M2 routes answer 503 ``db_unavailable`` meanwhile.
    """

    def __init__(self, app: FastAPI, settings: Settings, *, own_audit: bool) -> None:
        self.app, self.settings, self.own_audit = app, settings, own_audit
        self.sink: DbAuditSink | None = None
        self.retry: asyncio.Task[None] | None = None
        self.consumer: Consumer | None = None
        self.worker: asyncio.Task[None] | None = None

    @property
    def configured(self) -> bool:
        from dps.db import db_configured  # the db extra is needed only with a database

        return bool(self.app.state.m2_routes) and db_configured(self.settings)

    async def start(self) -> None:
        """Connect (or schedule reconnects); refuses boot only for a staging/prod RLS problem."""
        if not self.configured:
            READINESS.set("db", False, "unconfigured")
            READINESS.set("jobs", False, "unconfigured")
            return
        from dps import db

        self.app.state.kek = load_kek(self.settings)
        if self.own_audit and self.settings.audit_enabled:
            self.sink = DbAuditSink(db.acquire, self.settings)
            self.sink.start()
            self.app.state.audit = self.sink
        try:
            await self._connect()
        except PostureError:
            await self.stop()
            raise
        except Exception as exc:  # noqa: BLE001 - reported by class name; retried in background
            self._down(exc)
            self.retry = asyncio.create_task(self._reconnect(), name="dps-db-reconnect")

    def _down(self, exc: BaseException) -> None:
        self.app.state.db_state = "down"
        READINESS.set("db", False, "down", error=type(exc).__name__)
        READINESS.set("jobs", False, "db down")
        logger.warning("db_unavailable", error=type(exc).__name__)

    async def _reconnect(self) -> None:
        while True:
            await asyncio.sleep(DB_RETRY_SECONDS)
            try:
                await self._connect()
                return
            except PostureError:
                logger.error("rls_posture_refused")
                return
            except Exception as exc:  # noqa: BLE001
                self._down(exc)

    async def _connect(self) -> None:
        from dps import db

        settings = self.settings
        await db.init_pool(settings)
        async with db.acquire(None) as conn:
            await conn.fetchval("SELECT 1")
        if settings.migrations_mode == "auto":
            mconn = await db.open_migration_connection(settings)
            try:
                await db.run_migrations(settings, connection=mconn)
            finally:
                await mconn.close()
        try:
            await db.verify_migrations(settings)
            migrations = "verified"
        except db.MigrationDrift:
            migrations = "pending or drifted"
            logger.warning("migrations_not_verified", mode=settings.migrations_mode)
        problems = await db.assert_rls_posture(settings)
        for problem in problems:
            logger.warning("rls_posture_problem", reason=problem)
        if problems and settings.is_production:
            raise PostureError("rls posture refused: " + "; ".join(problems))
        kek = self.app.state.kek is not None
        ready = migrations == "verified"
        self.app.state.db_state = "up" if ready else "down"
        READINESS.set("db", True, "up", migrations=migrations)
        READINESS.set(
            "jobs",
            ready and kek,
            "ready" if ready and kek else ("kek missing" if ready else "migrations " + migrations),
        )
        if ready and settings.embedded_worker:
            await self._start_worker()

    async def _start_worker(self) -> None:
        """Local only (posture refuses it in staging/prod): a ``Consumer`` in the app's loop."""
        from dps import db
        from dps.jobs import JobKind, JobPhase
        from dps.layers.connectors.discovery import discover_job
        from dps.layers.connectors.jobs_run import finalize, prepare, run_partition
        from dps.retention import retention_job
        from dps.worker import Consumer, build_context

        try:
            ctx = build_context(self.settings, audit=self.app.state.audit)
        except RuntimeError:
            logger.warning("embedded_worker_disabled", reason="DPS_JOB_KEK is not set")
            return
        await db.init_worker_pool(self.settings)
        self.consumer = Consumer(
            self.settings,
            kinds=(JobKind.discover, JobKind.run, JobKind.retention),
            handlers={
                JobPhase.discover: discover_job,
                JobPhase.prepare: prepare,
                JobPhase.finalize: finalize,
                JobPhase.retention: retention_job,
            },
            partition_handler=run_partition,
            concurrency=self.settings.worker_concurrency,
            ctx=ctx,
        )
        self.worker = asyncio.create_task(self.consumer.run(), name="dps-embedded-worker")
        logger.info("embedded_worker_started", worker_id=ctx.worker_id)

    async def stop(self) -> None:
        """Drain the embedded worker, flush the audit sink, close the pools."""
        if self.retry is not None:
            self.retry.cancel()
            await asyncio.gather(self.retry, return_exceptions=True)
        if self.consumer is not None and self.worker is not None:
            self.consumer.stop()
            await asyncio.gather(self.worker, return_exceptions=True)
        if self.sink is not None:
            await self.sink.stop()
        if self.configured:
            from dps import db

            with contextlib.suppress(Exception):
                await db.close_pools()


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
        m2 = _JobPlatform(app, settings, own_audit=audit_sink is None)
        await m2.start()
        logger.info("startup", env=settings.env, auth=settings.auth_enabled, provider=provider)
        try:
            yield
        finally:
            READINESS.set("api-core", False, "stopping", version=__version__)
            await m2.stop()

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
    app.add_exception_handler(StarletteHTTPException, coded_http_exception_handler)
    app.state.db_state = "unconfigured"
    app.state.kek = None

    # add_middleware prepends: the last one added is the outermost.
    app.add_middleware(_ValueFreeErrors)
    app.add_middleware(_BodyLimit, max_bytes=settings.max_payload_bytes)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=[
            "Content-Type",
            API_KEY_HEADER,
            TENANT_HEADER,
            TRACE_HEADER,
            IDEMPOTENCY_HEADER,
        ],
    )
    app.add_middleware(_SecurityHeaders, hsts=settings.env != "local")

    app.include_router(ops.router)
    app.include_router(detect.router)
    app.include_router(transform.router)
    app.include_router(policies.router)
    app.state.m2_routes = _include_m2_routers(app)
    _mount_frontend(app)
    return app


def _include_m2_routers(app: FastAPI) -> bool:
    """The job platform's routes (contract §9). They need the ``db`` and ``connectors`` extras; a
    Layer 1 install without them serves the engine routes only."""
    try:
        from dps.layers.api.errors import install_error_handlers
        from dps.layers.api.routers import connections
    except ModuleNotFoundError as exc:
        logger.warning("m2_routes_disabled", reason=f"missing module {exc.name}")
        return False
    install_error_handlers(app)
    app.include_router(connections.router)
    return True
