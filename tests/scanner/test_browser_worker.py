"""Task 17: the browser-worker process — ops endpoints, a scan-only consumer with the
``browser-worker`` role, refusals without a database or a KEK."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from dps.config import Settings
from dps.jobs import JobKind, JobPhase, JobState


async def _http(port: int, request: bytes) -> tuple[int, bytes]:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(request)
    await writer.drain()
    raw = await asyncio.wait_for(reader.read(), timeout=5)
    writer.close()
    head, _, body = raw.partition(b"\r\n\r\n")
    return int(head.split()[1]), body


async def test_ops_server_serves_health_readyz_metrics_only() -> None:
    from dps.browser_worker import serve_ops
    from dps.observability import SCAN_REQUESTS

    SCAN_REQUESTS.labels("blocked", "POST", "method").inc(0)  # the series exists in this process
    server = await serve_ops(0, "127.0.0.1")
    port = server.sockets[0].getsockname()[1]
    try:
        status, body = await _http(port, b"GET /health HTTP/1.1\r\nHost: x\r\n\r\n")
        assert (status, json.loads(body)) == (200, {"status": "ok"})
        status, body = await _http(port, b"GET /metrics HTTP/1.1\r\nHost: x\r\n\r\n")
        assert status == 200 and b"dps_scan_requests_total" in body
        status, body = await _http(port, b"GET /readyz?x=1 HTTP/1.1\r\nHost: x\r\n\r\n")
        assert status in (200, 503) and "components" in json.loads(body)
        for request in (
            b"POST /health HTTP/1.1\r\nHost: x\r\nContent-Length: 0\r\n\r\n",
            b"GET /anything HTTP/1.1\r\n\r\n",
            b"GET /../health HTTP/1.1\r\n\r\n",
            b"garbage\r\n\r\n",
        ):
            status, body = await _http(port, request)
            assert status == 404 and json.loads(body) == {"detail": "not found"}
    finally:
        server.close()
        await server.wait_closed()


def test_consumer_claims_scan_jobs_only(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    import dps.browser_worker as bw
    from dps.layers.scanner import scan_finalize_job, scan_job

    captured: dict[str, Any] = {}

    class FakeConsumer:
        def __init__(self, s: Settings, **kw: Any) -> None:
            captured.clear()
            captured.update(kw, settings=s)

    sentinel, sentinel2 = object(), object()
    monkeypatch.setattr(bw, "Consumer", FakeConsumer)
    bw.build_consumer(settings, ctx=sentinel)  # type: ignore[arg-type]
    assert captured["kinds"] == (JobKind.scan,)
    assert captured["handlers"] == {JobPhase.scan: scan_job, JobPhase.finalize: scan_finalize_job}
    assert captured["partition_handler"] is None
    assert captured["concurrency"] == settings.browser_concurrency
    assert captured["ctx"] is sentinel and captured["role"] == "browser-worker"
    monkeypatch.setattr(bw, "build_context", lambda s: sentinel2)
    bw.build_consumer(settings)
    assert captured["ctx"] is sentinel2


async def test_refuses_to_start_without_db_or_kek(monkeypatch: pytest.MonkeyPatch) -> None:
    import dps.browser_worker as bw

    async def never(*_a: Any, **_k: Any) -> None:
        raise AssertionError("the pools must not be opened")

    monkeypatch.setattr(bw, "init_worker_pool", never)
    monkeypatch.setattr(bw, "init_pool", never)
    base = dict(_env_file=None, env="local", llm_provider="stub", browser_metrics_port=0)
    with pytest.raises(SystemExit) as no_db:
        await bw.amain(Settings(**base, pg_host=""))  # type: ignore[arg-type]
    assert "DPS_PG_HOST" in str(no_db.value)
    with pytest.raises(SystemExit) as no_kek:
        await bw.amain(Settings(**base, pg_host="x", job_kek=""))  # type: ignore[arg-type]
    assert "DPS_JOB_KEK" in str(no_kek.value)


@pytest.mark.db
async def test_worker_row_has_browser_role(migrated_db: Settings, handler_ctx: Any) -> None:
    from dps import db
    from dps.browser_worker import build_consumer

    consumer = build_consumer(migrated_db, handler_ctx)
    task = asyncio.create_task(consumer.run())
    sql = f"SELECT role FROM {db.schema_ident()}.dps_worker WHERE worker_id = $1"
    role = None
    try:
        for _ in range(50):
            async with db.acquire_queue(None) as conn:
                role = await conn.fetchval(sql, handler_ctx.worker_id)
            if role is not None:
                break
            await asyncio.sleep(0.1)
        assert role == "browser-worker"
    finally:
        consumer.stop()
        await asyncio.wait_for(task, timeout=30)
    async with db.acquire_queue(None) as conn:
        assert await conn.fetchval(sql, handler_ctx.worker_id) is None  # the drain unregistered it


def _free_port() -> int:
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


async def _get(port: int, path: str) -> tuple[int, bytes]:
    return await _http(port, f"GET {path} HTTP/1.1\r\nHost: x\r\n\r\n".encode())


@pytest.mark.db
@pytest.mark.browser
async def test_process_runs_a_scan_and_drains_on_sigterm(
    migrated_db: Settings, kek: bytes, tmp_path: Any, demo_site_url: str
) -> None:
    """``python -m dps.browser_worker`` as a real process: ready once Chromium is up, claims a
    scan job from the queue and finishes it (evidence, key purged), drains on SIGTERM."""
    import os
    import signal
    import subprocess
    import sys

    from dps import jobs
    from dps.engine.synth.surrogates import generate_synthesis_seed
    from dps.engine.transform.crypto import generate_key_material
    from dps.ids import new_id
    from dps.jobs_models import JobSecret, ScanSpec
    from dps.layers.scanner.models import redact_scan_spec, validate_scan
    from dps.secrets import seal
    from tests.connectors.helpers import db_key_enc
    from tests.fixtures.db import worker_env
    from tests.scanner.helpers import demo_cookie_state, received

    pytest.importorskip("playwright.async_api")
    port = _free_port()
    env = {
        **os.environ,
        **worker_env(migrated_db, tmp_path),
        "DPS_BROWSER_METRICS_PORT": str(port),
        "DPS_SCAN_SETTLE_MS": "150",
        "DPS_SCAN_REGISTERED_HOSTS": "127.0.0.1,localhost",
    }
    spec = ScanSpec.model_validate(
        {
            "url": f"{demo_site_url}/profile",
            "scope": {"max_depth": 0},
            "auth": {"storage_state": demo_cookie_state("127.0.0.1")},
        }
    )
    validated = validate_scan(
        spec, settings=migrated_db, hosts=frozenset({"127.0.0.1"}), tenant_id="t-bw"
    )
    key, seed, job_id = generate_key_material("key"), generate_synthesis_seed(), new_id("s")
    secret = JobSecret(
        key_material=key.model_dump(mode="json"),
        synthesis_seed=seed.model_dump(mode="json"),
        scan_auth=spec.auth.storage_state,
        scan_start_url=validated.start_url,
    )
    before = received(demo_site_url)
    await jobs.create_job(
        job_id=job_id,
        tenant_id="t-bw",
        kind=JobKind.scan,
        phase=JobPhase.scan,
        spec=redact_scan_spec(validated, validated.origin.text() + "/profile"),
        mode="synthesize",
        sealed_secret=seal(kek, secret.model_dump_json().encode(), aad=f"dps_job.key:{job_id}"),
        kid=key.kid,
    )
    proc = subprocess.Popen([sys.executable, "-m", "dps.browser_worker"], env=env, cwd=tmp_path)
    try:
        ready: dict[str, Any] = {}
        for _ in range(300):
            try:
                status, body = await _get(port, "/readyz")
                if status == 200:
                    ready = json.loads(body)
                    break
            except OSError:
                pass
            await asyncio.sleep(0.2)
        else:
            pytest.fail("browser-worker never became ready")
        assert ready["components"]["browser"]["ok"] is True
        job = None
        for _ in range(600):
            job = await jobs.get_job("t-bw", job_id)
            if job is not None and job.state in jobs.TERMINAL_JOB_STATES:
                break
            await asyncio.sleep(0.2)
        assert job is not None and job.state == JobState.succeeded, job and job.error_code
        assert job.progress.evidence_id and await db_key_enc(job_id) is None
        status, body = await _get(port, "/metrics")
        assert status == 200 and b"dps_scan_pages_total" in body
        proc.send_signal(signal.SIGTERM)
        assert await asyncio.to_thread(proc.wait, 60) == 0
    finally:
        if proc.poll() is None:
            proc.kill()
    after = received(demo_site_url)
    assert after["non_get"] == before["non_get"] and after["form_get"] == before["form_get"]
