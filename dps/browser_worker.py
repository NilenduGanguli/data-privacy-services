"""Browser worker (contract §3.5): claims ``scan`` jobs only, runs Playwright Chromium, never
submits anything (decision D4: the scanner has no submit path; the request guard blocks every
non-GET request).

    python -m dps.browser_worker

Ops endpoints on ``DPS_BROWSER_METRICS_PORT`` (9401; 0 disables them): ``/health``, ``/readyz``
(ready once Chromium is up and the consumer claims; component ``browser``) and ``/metrics``.
SIGTERM/SIGINT: the M2 ``Consumer`` drains (running scans get the drain timeout, then their
leases are released and the reaper requeues them).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import signal

from dps import __version__
from dps.audit import DbAuditSink
from dps.config import Settings, get_settings
from dps.db import close_pools, db_configured, init_pool, init_worker_pool
from dps.jobs import JobKind, JobPhase
from dps.layers.scanner import scan_finalize_job, scan_job
from dps.layers.scanner.browser import close_browser, get_browser
from dps.observability import READINESS, configure_logging, get_logger, metrics_payload
from dps.posture import BROWSER_WORKER, assert_production_posture
from dps.secrets import load_kek
from dps.worker import Consumer, HandlerContext, build_context

logger = get_logger(__name__)
OPS_PATHS = ("/health", "/readyz", "/metrics")
_HEAD_TIMEOUT_S = 5
_MAX_HEAD_LINES = 100


async def _respond(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """One request, one response, then the connection closes. GET on the three paths only."""
    try:
        line = (await asyncio.wait_for(reader.readline(), timeout=_HEAD_TIMEOUT_S)).decode(
            "latin-1"
        )
        for _ in range(_MAX_HEAD_LINES):
            header = await asyncio.wait_for(reader.readline(), timeout=_HEAD_TIMEOUT_S)
            if header in (b"\r\n", b"\n", b""):
                break
        parts = line.split()
        path = parts[1].split("?")[0] if len(parts) >= 2 else ""
        if parts[:1] != ["GET"] or path not in OPS_PATHS:
            status, ctype, body = "404 Not Found", "application/json", b'{"detail":"not found"}'
        elif path == "/health":
            status, ctype, body = "200 OK", "application/json", b'{"status":"ok"}'
        elif path == "/readyz":
            snap = READINESS.snapshot()
            status = "200 OK" if snap["ready"] else "503 Service Unavailable"
            ctype, body = "application/json", json.dumps(snap).encode("utf-8")
        else:
            body, ctype = metrics_payload()
            status = "200 OK"
        head = (
            f"HTTP/1.1 {status}\r\nContent-Type: {ctype}\r\nContent-Length: {len(body)}\r\n"
            "Connection: close\r\n\r\n"
        )
        writer.write(head.encode("latin-1") + body)
        await writer.drain()
    except (TimeoutError, ConnectionError, UnicodeError):
        pass
    finally:
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()


async def serve_ops(port: int, host: str = "0.0.0.0") -> asyncio.Server:
    """Start the ops server (``/health``, ``/readyz``, ``/metrics``; nothing else)."""
    return await asyncio.start_server(_respond, host=host, port=port)


def build_consumer(settings: Settings, ctx: HandlerContext | None = None) -> Consumer:
    """M2's Consumer for scan jobs (contract §3.5): ``ctx`` is required by M2 (built here when
    none is given), and the role is passed explicitly so ``dps_worker`` records ``browser-worker``
    (the API's ``browser`` readiness counts those rows)."""
    return Consumer(
        settings,
        kinds=(JobKind.scan,),
        handlers={JobPhase.scan: scan_job, JobPhase.finalize: scan_finalize_job},
        partition_handler=None,
        concurrency=settings.browser_concurrency,
        ctx=ctx if ctx is not None else build_context(settings),
        role="browser-worker",
    )


async def amain(settings: Settings | None = None) -> None:
    """Posture, database and KEK checks, Chromium, then claim scans until SIGTERM."""
    settings = settings or get_settings()
    configure_logging(settings.log_level, json_output=settings.env != "local")
    assert_production_posture(settings, role=BROWSER_WORKER)  # M4 + M5 rules of this role
    if not db_configured(settings):
        raise SystemExit("browser-worker needs the metadata database (DPS_PG_HOST)")
    if load_kek(settings) is None:
        raise SystemExit("browser-worker needs DPS_JOB_KEK to open scan secrets")
    READINESS.set("api-core", False, "starting", version=__version__)
    READINESS.set("browser", False, "starting")
    server = (
        await serve_ops(settings.browser_metrics_port, settings.host)
        if settings.browser_metrics_port
        else None
    )
    try:
        await init_pool(settings)
        await init_worker_pool(settings)  # the ArtefactService and the DbAuditSink use it
        ctx = build_context(settings)
        audit = ctx.audit
        if isinstance(audit, DbAuditSink):
            audit.start()
        consumer = build_consumer(settings, ctx)
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, consumer.stop)  # Consumer.run() installs none itself
        try:
            browser = await get_browser(settings)
            READINESS.set("browser", True, "chromium", version=browser.version)
            READINESS.set("api-core", True, "browser-worker", version=__version__)
            logger.info("startup", component="browser-worker", version=__version__)
            await consumer.run()
        finally:
            READINESS.set("api-core", False, "stopped")
            READINESS.set("browser", False, "stopped")
            await close_browser()
            if isinstance(audit, DbAuditSink):
                await audit.stop()
    finally:
        await close_pools()
        if server is not None:
            server.close()
            await server.wait_closed()


def main() -> None:
    """``python -m dps.browser_worker``."""
    asyncio.run(amain())


if __name__ == "__main__":
    # Run the canonical module (``dps.browser_worker``), not this ``__main__`` copy, so handlers
    # and tests see one set of module objects (as ``dps.worker`` does).
    from dps import browser_worker as _browser_worker

    _browser_worker.main()
