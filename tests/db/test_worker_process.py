"""``python -m dps.worker`` as a real process: health server, readiness, metrics, SIGTERM drain."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest

from dps.config import Settings
from tests.fixtures.db import _truncate, worker_env

pytestmark = pytest.mark.db


@pytest.fixture
def worker_db(db_settings: Settings) -> Iterator[Settings]:
    """The session database, truncated afterwards: the worker process schedules a retention job
    and registers itself, and later tests must not find those rows."""
    yield db_settings
    asyncio.run(_truncate(db_settings))


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def test_worker_serves_health_and_drains_on_sigterm(worker_db: Settings, tmp_path: Path) -> None:
    port = _free_port()
    env = {**os.environ, **worker_env(worker_db, tmp_path), "DPS_WORKER_METRICS_PORT": str(port)}
    proc = subprocess.Popen([sys.executable, "-m", "dps.worker"], env=env, cwd=tmp_path)
    try:
        ready: dict[str, object] = {}
        for _ in range(150):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/readyz", timeout=1) as r:
                    if r.status == 200:
                        ready = json.loads(r.read())
                        break
            except (OSError, urllib.error.URLError):
                time.sleep(0.2)
        else:
            pytest.fail("worker never became ready")
        components = ready["components"]
        assert isinstance(components, dict)
        assert {"db", "migrations", "kek", "queue"} <= set(components)
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as r:
            assert json.loads(r.read()) == {"status": "ok"}
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/metrics", timeout=2) as r:
            assert b"dps_jobs_claimed_total" in r.read()
        proc.send_signal(signal.SIGTERM)
        assert proc.wait(timeout=30) == 0
    finally:
        if proc.poll() is None:
            proc.kill()


def test_worker_refuses_to_start_without_a_kek(worker_db: Settings, tmp_path: Path) -> None:
    env = {**os.environ, **worker_env(worker_db, tmp_path), "DPS_JOB_KEK": ""}
    done = subprocess.run(
        [sys.executable, "-m", "dps.worker"],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert done.returncode != 0
    assert "DPS_JOB_KEK" in done.stderr and "Traceback" not in done.stderr
