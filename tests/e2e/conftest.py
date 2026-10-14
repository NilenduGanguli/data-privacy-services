"""In-process API + subprocess workers for the M3 end-to-end tests (real containers only).

The app runs in this process on the session's test metadata database (``db_settings``) with auth on
(``tests.fixtures.db.API_KEYS``); workers are ``python -m dps.worker`` subprocesses on the same
database (``tests.fixtures.db.worker_env``: fast leases and reaper). Oracle and MongoDB connections
use ``secret_ref`` folders written into the app's secret dir — demo logins of the lane's containers
(``docker/oracle/initdb/01_crm.sql``; the replica set has no auth, so a MongoDB ref holds a URI).
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from dps.config import Settings
from tests.fixtures.db import _truncate, api_settings, worker_env
from tests.fixtures.m3_services import mongo_uri, oracle_dsn

#: Oracle refs: demo logins (local literals; the password equals the user name).
ORACLE_REFS = {
    "m3/oracle-crm-ro": "dps_ro",
    "m3/oracle-crm-owner": "crm",
    "m3/oracle-crm-masked": "crm_masked",
}
MONGO_REF = "m3/mongo"
JOBS = {"X-API-KEY": "dps_jobs", "X-DPS-Tenant": "t1"}
FULL = {"X-API-KEY": "dps_full", "X-DPS-Tenant": "t1"}


def write_m3_refs(secret_dir: Path) -> None:
    """The Oracle and MongoDB ``secret_ref`` folders under ``secret_dir``."""
    from tests.fixtures.sources import write_ref

    # SEC-2: under each test tenant's folder (refs are tenant-scoped)
    for ref, user in ORACLE_REFS.items():
        write_ref(secret_dir, ref, {"username": user, "password": user})
    write_ref(secret_dir, MONGO_REF, {"uri": mongo_uri()})


def oracle_params() -> dict[str, Any]:
    """``host``/``port``/``service_name`` of the lane's Oracle (an easy-connect DSN)."""
    from tests.services import host_port

    dsn = oracle_dsn()
    host, port = host_port(dsn)
    return {"host": host, "port": port, "service_name": dsn.rsplit("/", 1)[-1]}


@pytest.fixture
def m3_settings(db_settings: Settings, tmp_path: Path) -> Settings:
    settings = api_settings(
        db_settings,
        tmp_path,
        fpe_enabled=True,
        ner_enabled=False,
        llm_provider="stub",
        embedded_worker=False,
    )
    write_m3_refs(Path(settings.secret_dir))
    return settings


@pytest.fixture
def m3_app(m3_settings: Settings, db_settings: Settings) -> Iterator[Any]:
    from fastapi.testclient import TestClient

    from dps.app import create_app
    from dps.audit import MemoryAuditSink

    try:
        with TestClient(create_app(m3_settings, audit_sink=MemoryAuditSink())) as client:
            yield client
    finally:
        asyncio.run(_truncate(db_settings))


class Workers:
    """``start()`` launches ``python -m dps.worker``; every process is killed at teardown."""

    def __init__(self, env: dict[str, str], cwd: Path) -> None:
        self.env, self.cwd = env, cwd
        self.procs: list[tuple[subprocess.Popen[bytes], Path]] = []

    def start(self) -> subprocess.Popen[bytes]:
        log = self.cwd / f"worker-{len(self.procs)}.log"
        proc = subprocess.Popen(
            [sys.executable, "-m", "dps.worker"],
            env={**os.environ, **self.env},
            cwd=self.cwd,
            stdout=log.open("wb"),
            stderr=subprocess.STDOUT,
        )
        self.procs.append((proc, log))
        return proc

    def logs(self) -> str:
        return "".join(path.read_text(errors="replace") for _, path in self.procs)

    def stop(self) -> None:
        for proc, _ in self.procs:
            if proc.poll() is None:
                proc.kill()
                proc.wait(10)


@pytest.fixture
def m3_workers(m3_settings: Settings, tmp_path: Path) -> Iterator[Workers]:
    env = {**worker_env(m3_settings, tmp_path), "DPS_WORKER_CONCURRENCY": "4"}
    workers = Workers(env, tmp_path)
    try:
        yield workers
    finally:
        workers.stop()


def wait_job(
    client: Any, job_id: str, *, timeout: float = 900, until: tuple[str, ...] = ("succeeded",)
) -> dict[str, Any]:
    """Poll ``GET /jobs/{id}`` until the job reaches ``until`` (a failure names its code only)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job: dict[str, Any] = client.get(f"/api/v1/jobs/{job_id}", headers=JOBS).json()
        if job["state"] in until:
            return job
        if job["state"] in ("failed", "dead", "canceled"):
            raise AssertionError(f"job {job_id} ended {job['state']} code={job.get('error_code')}")
        time.sleep(1)
    raise AssertionError(f"job {job_id} did not reach {until} in {timeout:.0f}s")
