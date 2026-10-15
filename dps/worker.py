"""Worker roles (contract §3.5): the handler context, the ``Consumer`` and ``python -m dps.worker``.

The :class:`Consumer` runs four loops in one event loop (from ``di/worker.py``):

- **claim** — job rows first (prepare/finalize unblock partitions), then partitions, up to
  ``concurrency`` tasks in flight; with nothing claimed it waits for a LISTEN wake-up or the poll
  interval. A row this worker claims again while its earlier task still runs (the lease expired
  and the reaper requeued it) first cancels that zombie task and waits for it to end, so one
  worker never runs the same partition twice at once.
- **heartbeat** — every ``min(job lease, partition lease) / 4`` seconds renews the leases of the
  tasks in flight; any id the database no longer returns was reclaimed, and its task is
  cancelled at once (``dps_leases_lost_total``).
- **reaper** — every instance, idempotent: requeue expired leases, sample throughput, close idle
  runtimes, schedule retention, refresh the queue-depth gauges.
- **listen** — a dedicated, non-pooled connection LISTENs on ``dps_job_new``; polling is the floor.

``stop()`` (wired to SIGTERM/SIGINT by ``main``) stops claiming, waits up to
``job_drain_timeout_seconds`` for the tasks in flight, cancels what is left and releases its rows
without charging an attempt; a released partition resumes from its checkpoint or ledger mark.

Handlers live next to their layer: ``dps.layers.connectors.discovery.discover_job``,
``dps.layers.connectors.jobs_run`` (``prepare``, ``run_partition``, ``finalize``) and
``dps.retention.retention_job``. Nothing here logs a value: errors are exception class names.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import inspect
import random
import signal
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass
from typing import Any

import asyncpg
import uvicorn
from fastapi import FastAPI, Response
from fastapi.responses import JSONResponse

from dps import __version__, db, jobs
from dps.audit import AuditSink, DbAuditSink
from dps.config import Settings, get_settings
from dps.engine.call_scope import bind_call_scope
from dps.engine.detect.pattern_process import configure_pattern_processes
from dps.engine.service import PrivacyEngine
from dps.jobs import ClaimedJob, ClaimedPartition, JobInternal, JobKind, JobPhase
from dps.observability import (
    CURRENT_TENANT,
    LEASES_LOST,
    LLM_STATS,
    QUEUE_DEPTH,
    LlmStats,
    configure_logging,
    get_logger,
    metrics_payload,
)
from dps.posture import assert_production_posture
from dps.secrets import load_kek
from dps.storage import ArtefactService, artefact_store

logger = get_logger(__name__)

#: Seconds between reconnect attempts of the LISTEN connection (polling covers the gap).
LISTEN_RETRY_SECONDS = 5.0
#: Seconds between liveness checks of an open LISTEN connection.
LISTEN_CHECK_SECONDS = 5.0
#: Seconds a stopping consumer lets the claim loop finish its round before canceling it.
CLAIM_STOP_SECONDS = 10.0
_QUEUE_TABLES = {
    "dps_job": [s.value for s in jobs.JobState],
    "dps_job_partition": [s.value for s in jobs.PartitionState],
}


@dataclass
class HandlerContext:
    """What every handler gets: settings, the engine, this worker's id, the job KEK, artefacts
    and the audit sink. ``kek`` is key material: it is never logged or put in an error."""

    settings: Settings
    engine: PrivacyEngine
    worker_id: str
    kek: bytes
    artefacts: ArtefactService
    audit: AuditSink

    def __repr__(self) -> str:  # never show the KEK
        return f"HandlerContext(worker_id={self.worker_id!r})"


#: A job-row handler (discover, run prepare/finalize, retention, scan); the job is leased.
Handler = Callable[[HandlerContext, ClaimedJob], Awaitable[None]]
#: A partition handler; the partition is leased, its job row is not.
PartitionHandler = Callable[[HandlerContext, JobInternal, ClaimedPartition], Awaitable[None]]


class Consumer:
    """Claims job rows and partitions and runs their handlers under leases (contract §3.5)."""

    def __init__(
        self,
        settings: Settings,
        *,
        kinds: tuple[JobKind, ...],
        handlers: dict[JobPhase, Handler],
        partition_handler: PartitionHandler | None,
        concurrency: int,
        ctx: HandlerContext,
        role: str = "worker",
        worker_id: str | None = None,
    ) -> None:
        self.settings, self.kinds, self.handlers = settings, kinds, handlers
        self.partition_handler, self.ctx, self.role = partition_handler, ctx, role
        self.concurrency = max(1, concurrency)
        self.worker_id = worker_id or ctx.worker_id
        self._jobs: dict[str, asyncio.Task[None]] = {}
        self._parts: dict[int, asyncio.Task[None]] = {}
        self._stop = asyncio.Event()
        self._wake = asyncio.Event()
        self._draining = False
        self._running = False
        self._released_unhandled = False

    @property
    def in_flight(self) -> int:
        """Handler tasks currently running (job rows + partitions)."""
        return len(self._jobs) + len(self._parts)

    @property
    def running(self) -> bool:
        """True between the start of :meth:`run` and the start of the drain."""
        return self._running and not self._draining

    @property
    def draining(self) -> bool:
        """True once :meth:`stop` was called and the drain began."""
        return self._draining

    def stop(self) -> None:
        """Stop claiming and drain (safe to call from a signal handler or twice)."""
        self._stop.set()
        self._wake.set()

    async def run(self) -> None:
        """Run the claim, heartbeat, reaper and listen loops until :meth:`stop`; then drain.

        The claim loop is not canceled mid-round (a row claimed but not yet dispatched would stay
        leased until its lease expired): it finishes its round and exits; the heartbeat keeps the
        leases of the draining tasks alive until they end or are canceled and released.
        """
        await jobs.register_worker(self.worker_id, role=self.role, version=__version__)
        self._running = True
        claim = asyncio.create_task(self._claim_loop(), name="worker:claim")
        heartbeat = asyncio.create_task(self._heartbeat_loop(), name="worker:heartbeat")
        background = [
            asyncio.create_task(self._reaper_loop(), name="worker:reaper"),
            asyncio.create_task(self._listen_loop(), name="worker:listen"),
        ]
        try:
            await self._stop.wait()
        finally:
            self._draining = True
            for t in background:
                t.cancel()
            _, late = await asyncio.wait({claim}, timeout=CLAIM_STOP_SECONDS)
            for t in late:
                t.cancel()
            await asyncio.gather(claim, *background, return_exceptions=True)
            try:
                await self._drain(heartbeat)
            finally:
                heartbeat.cancel()
                await asyncio.gather(heartbeat, return_exceptions=True)
                self._running = False

    # -- tasks ------------------------------------------------------------------------------------

    def _spawn_job(self, job: ClaimedJob, handler: Handler) -> None:
        old = self._jobs.get(job.id)
        if old is not None and not old.done():
            LEASES_LOST.labels("job").inc()
            old.cancel()
        task = asyncio.create_task(
            self._guard(handler(self.ctx, job), old, tenant_id=job.tenant_id), name=f"job:{job.id}"
        )
        self._jobs[job.id] = task
        task.add_done_callback(functools.partial(self._forget, self._jobs, job.id))

    def _spawn_partition(self, internal: JobInternal, part: ClaimedPartition) -> None:
        assert self.partition_handler is not None
        old = self._parts.get(part.id)
        if old is not None and not old.done():
            LEASES_LOST.labels("partition").inc()
            old.cancel()
        coro = self.partition_handler(self.ctx, internal, part)
        task = asyncio.create_task(
            self._guard(coro, old, tenant_id=part.tenant_id), name=f"partition:{part.id}"
        )
        self._parts[part.id] = task
        task.add_done_callback(functools.partial(self._forget, self._parts, part.id))

    @staticmethod
    def _forget(tasks: dict[Any, asyncio.Task[None]], key: Any, task: asyncio.Task[None]) -> None:
        if tasks.get(key) is task:  # a re-claim may already hold the slot
            del tasks[key]

    async def _guard(
        self,
        coro: Awaitable[None],
        predecessor: asyncio.Task[None] | None = None,
        *,
        tenant_id: str | None = None,
    ) -> None:
        """Run one handler; an earlier task of the same row must have ended first.

        M5 (spec P1): the handler's task binds its job's tenant (``CURRENT_TENANT``) and a fresh
        ``LlmStats``, so LLM calls it makes count for that tenant (a task copies the context at
        creation: the binding is per task). Handlers that call the LLM copy the stats into
        ``progress.metrics.llm`` (:func:`dps.observability.LlmStats.counts`). S3 review round 1:
        it also binds a fresh engine call scope (:mod:`dps.engine.call_scope`) — the job's custom
        entity tiers and the CPU budget of its policy patterns are its own (round 2: no single
        pattern run takes more than ``DPS_PATTERN_CPU_SECONDS``).
        """
        CURRENT_TENANT.set(tenant_id)
        LLM_STATS.set(LlmStats())
        bind_call_scope(pattern_run_cap=self.settings.pattern_cpu_seconds)
        try:
            if predecessor is not None and not predecessor.done():
                await asyncio.wait({predecessor})
            await coro
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - handlers own their error paths; this is the last net
            logger.error("handler_crashed", error=type(exc).__name__)
        finally:
            if inspect.iscoroutine(coro):
                coro.close()  # a handler canceled before it started must not warn "never awaited"

    # -- loops ------------------------------------------------------------------------------------

    async def _claim_loop(self) -> None:
        while not self._stop.is_set():
            claimed_any = False
            try:
                claimed_any = await self._claim_round()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - a DB hiccup must not kill the loop
                logger.warning("claim_failed", error=type(exc).__name__)
            if claimed_any:
                continue
            # after giving back a row this worker has no handler for, its own release notification
            # must not wake it straight into claiming the row again: wait out the poll interval
            wake = self._stop if self._released_unhandled else self._wake
            self._released_unhandled = False
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(wake.wait(), timeout=self.settings.job_poll_interval_seconds)
            self._wake.clear()

    async def _claim_round(self) -> bool:
        claimed_any = False
        free = self.concurrency - self.in_flight
        if free > 0:
            batch = min(free, self.settings.job_claim_batch)
            for job in await jobs.claim_jobs(self.worker_id, kinds=self.kinds, batch=batch):
                handler = self.handlers.get(job.phase)
                if handler is None:
                    logger.warning("no_handler", job_id=job.id, kind=job.kind, phase=job.phase)
                    await jobs.release(self.worker_id, job_ids=[job.id], partition_ids=[])
                    self._released_unhandled = True
                    continue
                self._spawn_job(job, handler)
                claimed_any = True
        free = self.concurrency - self.in_flight
        if free > 0 and self.partition_handler is not None and not self._stop.is_set():
            cache: dict[str, JobInternal | None] = {}
            for part in await jobs.claim_partitions(self.worker_id, batch=free):
                if part.job_id not in cache:
                    cache[part.job_id] = await jobs.load_job_internal(part.job_id)
                internal = cache[part.job_id]
                if internal is None:  # deleted under us: give the row back
                    await jobs.release(self.worker_id, job_ids=[], partition_ids=[part.id])
                    continue
                self._spawn_partition(internal, part)
                claimed_any = True
        return claimed_any

    async def _heartbeat_loop(self) -> None:
        lease = min(self.settings.job_lease_seconds, self.settings.partition_lease_seconds)
        interval = max(0.5, lease / 4)
        while not self._stop.is_set():
            await asyncio.sleep(interval)
            try:
                job_ids = [k for k, t in self._jobs.items() if not t.done()]
                part_ids = [k for k, t in self._parts.items() if not t.done()]
                owned_jobs, owned_parts = await jobs.heartbeat(
                    self.worker_id, job_ids=job_ids, partition_ids=part_ids
                )
                self._cancel_lost(self._jobs, job_ids, set(owned_jobs), "job")
                self._cancel_lost(self._parts, part_ids, set(owned_parts), "partition")
                await jobs.worker_heartbeat(
                    self.worker_id, busy=self.in_flight, draining=self._draining
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - the next beat retries; leases cover the gap
                logger.warning("heartbeat_failed", error=type(exc).__name__)

    @staticmethod
    def _cancel_lost(
        tasks: dict[Any, asyncio.Task[None]], asked: list[Any], owned: set[Any], what: str
    ) -> None:
        for key in asked:
            task = tasks.get(key)
            if key not in owned and task is not None and not task.done():
                LEASES_LOST.labels(what).inc()
                task.cancel()

    async def _reaper_loop(self) -> None:
        while not self._stop.is_set():
            await asyncio.sleep(
                self.settings.job_reaper_interval_seconds * random.uniform(0.8, 1.2)
            )
            try:
                await self._reap_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - the next pass retries
                logger.warning("reaper_failed", error=type(exc).__name__)

    async def _reap_once(self) -> None:
        result = await jobs.reap(
            backoff_base=self.settings.job_retry_base_seconds,
            backoff_cap=self.settings.job_retry_max_seconds,
        )
        if (
            result.jobs_requeued
            or result.partitions_requeued
            or result.jobs_dead
            or result.partitions_dead
        ):
            logger.info("reaped", counts=result.model_dump())
        await jobs.sample_throughput()
        from dps.layers.connectors.jobs_run import RUNTIMES  # lazy: jobs_run imports this module

        await RUNTIMES.close_idle()
        if JobKind.retention in self.kinds:
            await jobs.ensure_retention_job(self.settings.retention_interval_hours)
        depth = {(t, s): 0 for t, states in _QUEUE_TABLES.items() for s in states}
        for row in await jobs.queue_stats():
            depth[(str(row["table"]), str(row["state"]))] = int(row["n"])
        for (table, state), n in depth.items():
            QUEUE_DEPTH.labels(table, state).set(n)

    async def _listen_loop(self) -> None:
        s = self.settings
        user, password = s.pg_worker_login
        while not self._stop.is_set():
            conn: asyncpg.Connection | None = None
            try:
                conn = await asyncpg.connect(
                    host=s.pg_host,
                    port=s.pg_port,
                    user=user,
                    password=password or None,
                    database=s.pg_database,
                    ssl=db._ssl_context(s),
                    timeout=s.connector_connect_timeout_seconds,
                    server_settings={"application_name": "dps-worker-listen"},
                )
                await conn.add_listener(jobs.NOTIFY_CHANNEL, self._on_notify)
                while not self._stop.is_set() and not conn.is_closed():
                    with contextlib.suppress(TimeoutError):
                        await asyncio.wait_for(self._stop.wait(), timeout=LISTEN_CHECK_SECONDS)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - polling is the floor; LISTEN only cuts latency
                logger.warning("listen_lost", error=type(exc).__name__)
            finally:
                if conn is not None and not conn.is_closed():
                    with contextlib.suppress(Exception):
                        await conn.close(timeout=2)
            if not self._stop.is_set():
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self._stop.wait(), timeout=LISTEN_RETRY_SECONDS)

    def _on_notify(self, *_args: object) -> None:
        self._wake.set()

    async def _drain(self, heartbeat: asyncio.Task[None]) -> None:
        pending = {t for t in (*self._jobs.values(), *self._parts.values()) if not t.done()}
        if pending:
            _, still = await asyncio.wait(pending, timeout=self.settings.job_drain_timeout_seconds)
            heartbeat.cancel()  # nothing left to renew: what is still running is released below
            job_ids = [k for k, t in self._jobs.items() if t in still]
            part_ids = [k for k, t in self._parts.items() if t in still]
            for t in still:
                t.cancel()
            await asyncio.gather(*still, return_exceptions=True)
            if job_ids or part_ids:
                # after the canceled handlers ended, so none can race its own release
                try:
                    await jobs.release(self.worker_id, job_ids=job_ids, partition_ids=part_ids)
                except Exception as exc:  # noqa: BLE001 - the reaper requeues them at lease expiry
                    logger.warning("release_failed", error=type(exc).__name__)
        heartbeat.cancel()
        from dps.layers.connectors.jobs_run import RUNTIMES  # lazy: jobs_run imports this module

        await RUNTIMES.close_all()
        try:
            await jobs.unregister_worker(self.worker_id)
        except Exception as exc:  # noqa: BLE001 - the reaper prunes a silent worker row
            logger.warning("unregister_failed", error=type(exc).__name__)


# -- process wiring -------------------------------------------------------------------------------


def build_context(settings: Settings, *, audit: AuditSink | None = None) -> HandlerContext:
    """The worker's :class:`HandlerContext` (contract §3.5).

    The engine gets the LLM gateway, artefacts go to :func:`~dps.storage.artefact_store` through
    the worker pool, and the audit sink is a :class:`~dps.audit.DbAuditSink` on the worker pool
    unless ``audit`` is given (the embedded worker shares the app's sink). The caller starts and
    stops a ``DbAuditSink``.

    Raises:
        RuntimeError: ``DPS_JOB_KEK`` is missing (nothing could be sealed or opened).
    """
    kek = load_kek(settings)
    if kek is None:
        raise RuntimeError("DPS_JOB_KEK is required for the worker")
    from dps.llm.gateway import get_gateway

    return HandlerContext(
        settings=settings,
        engine=PrivacyEngine(settings, gateway=get_gateway(settings)),
        worker_id=jobs.worker_id(),
        kek=kek,
        artefacts=ArtefactService(artefact_store(settings), acquire=db.acquire_queue),
        audit=audit if audit is not None else DbAuditSink(db.acquire_queue, settings),
    )


class _QuietServer(uvicorn.Server):
    """A ``uvicorn.Server`` that leaves the process's signal handlers alone (the worker owns them)."""

    @contextlib.contextmanager
    def capture_signals(self) -> Iterator[None]:
        yield


async def _readiness(settings: Settings, consumer: Consumer) -> tuple[bool, dict[str, Any]]:
    """``/readyz`` components: db (``SELECT 1``), migrations (verified), kek, queue (claiming)."""
    components: dict[str, Any] = {}
    try:
        async with db.acquire_queue(None) as conn:
            await conn.fetchval("SELECT 1")
        components["db"] = {"ok": True, "detail": "up"}
    except Exception as exc:  # noqa: BLE001 - reported by class name only
        components["db"] = {"ok": False, "detail": type(exc).__name__}
    try:
        await db.verify_migrations(settings)
        components["migrations"] = {"ok": True, "detail": "verified"}
    except db.MigrationDrift:
        components["migrations"] = {"ok": False, "detail": "drift or pending"}
    except Exception as exc:  # noqa: BLE001
        components["migrations"] = {"ok": False, "detail": type(exc).__name__}
    kek_ok = consumer.ctx.kek is not None and len(consumer.ctx.kek) == 32
    components["kek"] = {"ok": kek_ok, "detail": "loaded" if kek_ok else "missing"}
    detail = "claiming" if consumer.running else ("draining" if consumer.draining else "stopped")
    components["queue"] = {
        "ok": consumer.running,
        "detail": detail,
        "in_flight": consumer.in_flight,
        "role": consumer.role,
    }
    return all(c["ok"] for c in components.values()), components


def health_app(settings: Settings, consumer: Consumer) -> FastAPI:
    """The worker's ops app: ``/health``, ``/readyz``, ``/metrics`` (no other route)."""
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz() -> JSONResponse:
        ready, components = await _readiness(settings, consumer)
        return JSONResponse(
            {"ready": ready, "components": components}, status_code=200 if ready else 503
        )

    @app.get("/metrics")
    async def metrics() -> Response:
        body, content_type = metrics_payload()
        return Response(content=body, media_type=content_type)

    return app


async def serve_health(settings: Settings, port: int, consumer: Consumer) -> None:
    """Serve :func:`health_app` on ``settings.host:port`` inside this event loop until canceled."""
    server = _QuietServer(
        uvicorn.Config(
            health_app(settings, consumer),
            host=settings.host,
            port=port,
            log_level="warning",
            lifespan="off",
            access_log=False,
        )
    )
    task = asyncio.create_task(server.serve(), name="worker:health")
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError:
        server.should_exit = True
        with contextlib.suppress(Exception):
            await asyncio.wait_for(task, timeout=5)
        raise


async def _amain(settings: Settings) -> None:
    await db.init_pool(settings)
    await db.init_worker_pool(settings)
    try:
        if settings.migrations_mode == "auto":
            conn = await db.open_migration_connection(settings)
            try:
                await db.run_migrations(settings, connection=conn)
            finally:
                await conn.close()
        elif settings.migrations_mode == "verify":
            await db.verify_migrations(settings)
        ctx = build_context(settings)
        audit = ctx.audit
        if isinstance(audit, DbAuditSink):
            audit.start()
        from dps.layers.connectors.discovery import discover_job
        from dps.layers.connectors.jobs_run import finalize, prepare, run_partition
        from dps.retention import retention_job

        consumer = Consumer(
            settings,
            kinds=(JobKind.discover, JobKind.run, JobKind.retention),
            handlers={
                JobPhase.discover: discover_job,
                JobPhase.prepare: prepare,
                JobPhase.finalize: finalize,
                JobPhase.retention: retention_job,
            },
            partition_handler=run_partition,
            concurrency=settings.worker_concurrency,
            ctx=ctx,
        )
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, consumer.stop)
        health = (
            asyncio.create_task(serve_health(settings, settings.worker_metrics_port, consumer))
            if settings.worker_metrics_port
            else None
        )
        logger.info("worker_ready", worker_id=consumer.worker_id)
        try:
            await consumer.run()
        finally:
            if health is not None:
                health.cancel()
                await asyncio.gather(health, return_exceptions=True)
            if isinstance(audit, DbAuditSink):
                await audit.stop()
        logger.info("worker_stopped", worker_id=consumer.worker_id)
    finally:
        await db.close_pools()


def main() -> None:
    """``python -m dps.worker``: posture, database and KEK checks, then claim until SIGTERM."""
    settings = get_settings()
    configure_logging(settings.log_level, json_output=settings.env != "local")
    assert_production_posture(settings)
    configure_pattern_processes(settings.pattern_processes)  # S3 review round 2 (R2-B1)
    if not db.db_configured(settings):
        raise SystemExit("DPS_PG_HOST is not set: the worker needs the metadata database")
    if load_kek(settings) is None:
        raise SystemExit("DPS_JOB_KEK is required for the worker")
    asyncio.run(_amain(settings))


if __name__ == "__main__":
    # Run the canonical module (``dps.worker``), not this ``__main__`` copy, so handlers and tests
    # see one ``HandlerContext`` class.
    from dps import worker as _worker

    _worker.main()
