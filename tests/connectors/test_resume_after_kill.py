"""Resume after kill, for every write mode (M2 exit criterion; contract §12.3).

Each case runs one job twice from identical inputs with the same key material or synthesis seed:
a *reference* run with one uninterrupted ``python -m dps.worker`` subprocess, and a *kill* run whose
first worker is ``SIGKILL``ed once five batches are committed; a second, separate worker process
finishes it (after the reaper reclaims the dead worker's leases). The two targets must be equal row
for row, the write ledger must be empty, and the evidence must count a resumed partition — which
proves the fencing, the reaper, the ledger and each mode's resume rule end to end.

Inputs: 6 000 synthetic customers per source table, ``partition_rows`` 1000 and 200-row batches
(6 partitions × 5 batches), parallelism 2.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import sys
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from dps import db, jobs
from dps.auth import LOCAL_PRINCIPAL
from dps.connections import Connection
from dps.engine.policy import MethodSpec
from dps.engine.synth.surrogates import generate_synthesis_seed
from dps.engine.transform import fpe
from dps.engine.transform.crypto import generate_key_material
from dps.ids import new_id
from dps.jobs import TERMINAL_JOB_STATES, Job, JobState
from dps.jobs_models import ClassificationSnapshot, ObjectRef, RunSpec, SnapshotOverride
from dps.layers.connectors.evidence import get_evidence
from dps.layers.connectors.postgres import LEDGER_TABLE
from dps.layers.connectors.submit import submit_run
from dps.snapshots import create_snapshot
from dps.worker import HandlerContext
from tests.connectors.helpers import discover_and_accept, register_pvc
from tests.fixtures.db import worker_env
from tests.fixtures.sources import CUSTOMERS_DDL, SourcePg, customers_records

pytestmark = [
    pytest.mark.db,
    pytest.mark.pg_source,
    pytest.mark.integration,
    pytest.mark.slow,
    pytest.mark.timeout(300),
]
TENANT = "t1"
ROWS = 6000
PURPOSE = {"ticket": "CHG-48213", "legal_basis": "legitimate interest — resume test"}
COLUMNS = [
    "customer_id",
    "full_name",
    "date_of_birth",
    "national_id",
    "id_type",
    "tax_id",
    "email",
    "mobile",
    "country_code",
    "segment",
    "risk_score",
    "kyc_status",
    "relationship_manager_id",
    "notes",
    "created_at",
    "updated_at",
]
#: In place the key is kept and every output must fit its column (see test_run_modes).
IN_PLACE_REVIEW = [
    SnapshotOverride(path="$.customer_id", method=MethodSpec(method="keep")),
    SnapshotOverride(path="$.national_id", method=MethodSpec(method="fpe_digits")),
    SnapshotOverride(path="$.tax_id", method=MethodSpec(method="mask_partial")),
]
#: An existing table target (upsert) also keeps national_id varchar(18) and tax_id char(10).
TABLE_REVIEW = IN_PLACE_REVIEW[1:]
RegisterSource = Callable[..., Awaitable[Connection]]


# -- helpers --------------------------------------------------------------------------------------


def start_worker(env: dict[str, str], cwd: Path, label: str) -> subprocess.Popen[bytes]:
    """A ``python -m dps.worker`` subprocess; its output goes to ``<cwd>/<label>.log``."""
    log = (cwd / f"{label}.log").open("wb")
    return subprocess.Popen(
        [sys.executable, "-m", "dps.worker"],
        env={**os.environ, **env},
        cwd=cwd,
        stdout=log,
        stderr=subprocess.STDOUT,
    )


def stop_worker(proc: subprocess.Popen[bytes], *, sig: int = signal.SIGTERM) -> None:
    """Signal the worker and wait for it (kill after 30 s)."""
    if proc.poll() is None:
        proc.send_signal(sig)
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)


async def batches_done(job_id: str) -> int:
    """Committed batches of the job (sum of the partitions' checkpoints)."""
    async with db.acquire_queue(None) as conn:
        value = await conn.fetchval(
            f"SELECT COALESCE(sum(batch_no), 0) FROM {db.schema_ident()}.dps_job_partition "
            "WHERE job_id = $1",
            job_id,
        )
    return int(value)


async def wait_for_batches(job_id: str, n: int, *, timeout: float = 120.0) -> int:
    """Poll every 50 ms until at least ``n`` batches are committed; returns the count seen."""
    deadline = time.monotonic() + timeout
    while True:
        done = await batches_done(job_id)
        if done >= n:
            return done
        job = await jobs.get_job(TENANT, job_id)
        assert job is not None and job.state not in TERMINAL_JOB_STATES, "finished before the kill"
        assert time.monotonic() < deadline, f"only {done} batches after {timeout:.0f}s"
        await asyncio.sleep(0.05)


async def wait_terminal(job_id: str, timeout: float) -> Job:
    """Poll until the job is terminal."""
    deadline = time.monotonic() + timeout
    while True:
        job = await jobs.get_job(TENANT, job_id)
        assert job is not None
        if job.state in TERMINAL_JOB_STATES:
            return job
        assert time.monotonic() < deadline, f"job still {job.state}/{job.phase}"
        await asyncio.sleep(0.2)


async def table_digest(pg: SourcePg, table: str, *, columns: list[str], where: str = "") -> str:
    """md5 over the table's rows (``columns``), order-independent; the rows must exist."""
    cols = ", ".join(f'"{c}"' for c in columns)
    row = await pg.admin.fetchrow(
        f"SELECT md5(string_agg(r::text, '|' ORDER BY r::text)) AS digest, count(*) AS n FROM "
        f'(SELECT {cols} FROM "{pg.schema}"."{table}" {where}) r'
    )
    assert row is not None and row["n"] >= ROWS, f"{table}: {row['n'] if row else 0} rows"
    return f"{row['n']}:{row['digest']}"


def files_digest(ctx: HandlerContext, base_path: str, prefix: str, job_id: str) -> str:
    """md5 over the sorted JSONL lines of every object the job's manifest lists."""
    import hashlib

    root = Path(ctx.settings.export_root) / base_path
    manifest = json.loads((root / prefix / job_id / "manifest.json").read_text("utf-8"))
    lines: list[str] = []
    for item in manifest["objects"]:
        lines += (root / item["key"]).read_text("utf-8").splitlines()
    assert len(lines) == ROWS
    return hashlib.md5("\n".join(sorted(lines)).encode("utf-8")).hexdigest()


async def ledger_rows(pg: SourcePg, job_id: str) -> int:
    """Write-ledger rows the job left in the target schema (0 once finalize cleared them)."""
    exists = await pg.admin.fetchval(f"SELECT to_regclass('\"{pg.schema}\".{LEDGER_TABLE}')")
    if exists is None:
        return 0
    return int(
        await pg.admin.fetchval(
            f'SELECT count(*) FROM "{pg.schema}".{LEDGER_TABLE} WHERE job_id = $1', job_id
        )
    )


async def clone_accepted(snap: ClassificationSnapshot, obj: ObjectRef) -> ClassificationSnapshot:
    """The same reviewed classification for an identical copy of the table (same columns)."""
    copy = snap.model_copy(
        update={
            "id": new_id("snap"),
            "job_id": None,
            "object_ref": obj.fqn(),
            "shape": snap.shape.model_copy(update={"object": obj}),
            "status": "accepted",
            "accepted_at": datetime.now(UTC),
            "accepted_by": "test",
        }
    )
    await create_snapshot(copy)
    return copy


def run_spec(
    conn_id: str,
    obj: ObjectRef,
    snap_id: str,
    target: dict[str, Any],
    consistency: dict[str, Any],
    mode: str,
) -> RunSpec:
    return RunSpec.model_validate(
        {
            "mode": mode,
            "snapshot_id": snap_id,
            "purpose": PURPOSE,
            "target": target,
            "consistency": consistency,
            "source": {
                "connection_id": conn_id,
                "object": obj.model_dump(),
                "parallelism": 2,
                "partition_rows": 1000,
                "batch_rows": 200,
            },
        }
    )


# -- cases ----------------------------------------------------------------------------------------


@dataclass
class Prepared:
    """One case, set up: the run spec of each run and the digest of each run's target."""

    spec: Callable[[str], RunSpec]  # "ref" | "kill"
    digest: Callable[[str, Job], Awaitable[str]]
    ledger: bool  # in_place / append keep a write ledger


async def _customers(pg: SourcePg, name: str, *, identity: bool = False) -> ObjectRef:
    ddl = CUSTOMERS_DDL
    if identity:
        ddl = ddl.replace(
            "customer_id bigint PRIMARY KEY",
            "customer_id bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY",
        )
    obj = await pg.table(name, ddl, customers_records(ROWS))
    if identity:
        await pg.admin.execute(
            f"SELECT setval(pg_get_serial_sequence('\"{pg.schema}\".{name}', 'customer_id'), "
            f'(SELECT max(customer_id) FROM "{pg.schema}".{name}))'
        )
    return obj


async def case_table(
    ctx: HandlerContext, pg: SourcePg, register: RegisterSource, *, load: str
) -> Prepared:
    src_obj = await _customers(pg, f"t{load[:3]}_src")
    src = await register(TENANT, name=f"src-{load[:3]}")
    uat = await register(TENANT, ref="dps/src/test-pg-rw", roles={"target"}, name=f"uat-{load[:3]}")
    # an existing target keeps its column lengths: det_token cannot fit national_id varchar(18)
    review = TABLE_REVIEW if load == "upsert" else []
    snap = await discover_and_accept(ctx, TENANT, src.id, src_obj, overrides=review)
    key = generate_key_material("key").model_dump(mode="json")
    if load == "upsert":  # targets pre-loaded with 100 unrelated rows (keys 1..100)
        unrelated = [(i + 1, *r[1:]) for i, r in enumerate(customers_records(100))]
        for label in ("ref", "kill"):
            await pg.table(f"tups_out_{label}", CUSTOMERS_DDL, unrelated)

    def spec(label: str) -> RunSpec:
        target = {
            "write_mode": "table",
            "connection_id": uat.id,
            "object": {"owner": pg.schema, "name": f"t{load[:3]}_out_{label}"},
            "table_load": load,
        }
        return run_spec(src.id, src_obj, snap.id, target, {"key_material": key}, "obfuscate")

    async def digest(label: str, job: Job) -> str:
        return await table_digest(pg, f"t{load[:3]}_out_{label}", columns=COLUMNS)

    return Prepared(spec, digest, ledger=False)


async def case_in_place(ctx: HandlerContext, pg: SourcePg, register: RegisterSource) -> Prepared:
    objs = {label: await _customers(pg, f"ip_{label}") for label in ("ref", "kill")}
    rw = await register(TENANT, ref="dps/src/test-pg-rw", roles={"source", "target"}, name="ip-rw")
    first = await discover_and_accept(ctx, TENANT, rw.id, objs["ref"], overrides=IN_PLACE_REVIEW)
    snaps = {"ref": first, "kill": await clone_accepted(first, objs["kill"])}
    key = generate_key_material("key").model_dump(mode="json")

    def spec(label: str) -> RunSpec:
        target = {"write_mode": "in_place", "confirm_write": True}
        return run_spec(
            rw.id, objs[label], snaps[label].id, target, {"key_material": key}, "obfuscate"
        )

    async def digest(label: str, job: Job) -> str:
        return await table_digest(pg, f"ip_{label}", columns=COLUMNS)

    return Prepared(spec, digest, ledger=True)


async def case_append_named(
    ctx: HandlerContext, pg: SourcePg, register: RegisterSource
) -> Prepared:
    src_obj = await _customers(pg, "ao_src")
    for label in ("ref", "kill"):
        await pg.table(f"ao_out_{label}", CUSTOMERS_DDL, [])
    rw = await register(TENANT, ref="dps/src/test-pg-rw", roles={"source", "target"}, name="ao-rw")
    snap = await discover_and_accept(ctx, TENANT, rw.id, src_obj)
    seed = generate_synthesis_seed().model_dump(mode="json")

    def spec(label: str) -> RunSpec:
        target = {
            "write_mode": "append",
            "connection_id": rw.id,
            "object": {"owner": pg.schema, "name": f"ao_out_{label}"},
            "confirm_write": True,
            "pk_strategy": {"kind": "offset", "offset": 90_000_000},
        }
        return run_spec(rw.id, src_obj, snap.id, target, {"synthesis_seed": seed}, "synthesize")

    async def digest(label: str, job: Job) -> str:
        return await table_digest(pg, f"ao_out_{label}", columns=COLUMNS)

    return Prepared(spec, digest, ledger=True)


async def case_append_generate(
    ctx: HandlerContext, pg: SourcePg, register: RegisterSource
) -> Prepared:
    objs = {label: await _customers(pg, f"ag_{label}", identity=True) for label in ("ref", "kill")}
    first_generated = 10_000_001 + ROWS  # the sequence stands at the largest seeded key
    rw = await register(TENANT, ref="dps/src/test-pg-rw", roles={"source", "target"}, name="ag-rw")
    first = await discover_and_accept(ctx, TENANT, rw.id, objs["ref"])
    snaps = {"ref": first, "kill": await clone_accepted(first, objs["kill"])}
    seed = generate_synthesis_seed().model_dump(mode="json")

    def spec(label: str) -> RunSpec:
        target = {
            "write_mode": "append",
            "confirm_write": True,
            "pk_strategy": {"kind": "generate"},
        }
        return run_spec(
            rw.id, objs[label], snaps[label].id, target, {"synthesis_seed": seed}, "synthesize"
        )

    async def digest(label: str, job: Job) -> str:
        # identity values differ between runs: compare the appended rows without their key
        count = await pg.admin.fetchval(f'SELECT count(*) FROM "{pg.schema}".ag_{label}')
        assert count == 2 * ROWS
        return await table_digest(
            pg,
            f"ag_{label}",
            columns=[c for c in COLUMNS if c != "customer_id"],
            where=f"WHERE customer_id >= {first_generated}",
        )

    return Prepared(spec, digest, ledger=True)


async def case_files(ctx: HandlerContext, pg: SourcePg, register: RegisterSource) -> Prepared:
    src_obj = await _customers(pg, "fj_src")
    src = await register(TENANT, name="fj-src")
    pvc = await register_pvc(TENANT, ctx, base_path="exports")
    snap = await discover_and_accept(ctx, TENANT, src.id, src_obj)
    key = generate_key_material("key").model_dump(mode="json")

    def spec(label: str) -> RunSpec:
        target = {
            "write_mode": "files",
            "connection_id": pvc.id,
            "files": {"format": "jsonl", "compression": "none", "prefix": "fj"},
        }
        return run_spec(src.id, src_obj, snap.id, target, {"key_material": key}, "obfuscate")

    async def digest(label: str, job: Job) -> str:
        return files_digest(ctx, "exports", "fj", job.id)

    return Prepared(spec, digest, ledger=False)


CASES: dict[str, Callable[[HandlerContext, SourcePg, RegisterSource], Awaitable[Prepared]]] = {
    "table_truncate_insert": lambda c, p, r: case_table(c, p, r, load="truncate_insert"),
    "table_upsert": lambda c, p, r: case_table(c, p, r, load="upsert"),
    "in_place": case_in_place,
    "append_offset_named": case_append_named,
    "append_generate_same": case_append_generate,
    "files_jsonl_pvc": case_files,
}


# -- the test -------------------------------------------------------------------------------------


@pytest.mark.parametrize("case", list(CASES))
async def test_resume_after_kill(
    case: str,
    handler_ctx: HandlerContext,
    source_pg: SourcePg,
    register_source: RegisterSource,
    tmp_path: Path,
) -> None:
    if not fpe.fpe_available():
        pytest.skip("the [fpe] extra is not installed: integer keys need FF1")
    prepared = await CASES[case](handler_ctx, source_pg, register_source)
    env = {**worker_env(handler_ctx.settings, tmp_path), "DPS_BATCH_ROWS": "200"}
    jobs_by_label: dict[str, Job] = {}

    async def submit(label: str) -> str:
        sub = await submit_run(
            prepared.spec(label),
            tenant_id=TENANT,
            principal=LOCAL_PRINCIPAL,
            settings=handler_ctx.settings,
            kek=handler_ctx.kek,
        )
        return sub.job.id

    # the reference run: one uninterrupted worker
    ref_id = await submit("ref")
    worker = start_worker(env, tmp_path, "ref")
    try:
        jobs_by_label["ref"] = await wait_terminal(ref_id, timeout=180)
    finally:
        stop_worker(worker)
    assert jobs_by_label["ref"].state == JobState.succeeded, jobs_by_label["ref"].error_code

    # the kill run: SIGKILL the first worker mid-run, a second worker finishes the job
    kill_id = await submit("kill")
    first = start_worker(env, tmp_path, "kill-1")
    second: subprocess.Popen[bytes] | None = None
    try:
        await wait_for_batches(kill_id, 5)
        first.send_signal(signal.SIGKILL)
        first.wait(timeout=10)
        second = start_worker(env, tmp_path, "kill-2")
        jobs_by_label["kill"] = await wait_terminal(kill_id, timeout=180)
    finally:
        stop_worker(first, sig=signal.SIGKILL)
        if second is not None:
            stop_worker(second)
    kill_job = jobs_by_label["kill"]
    assert kill_job.state == JobState.succeeded, kill_job.error_code
    assert kill_job.progress.rows_written >= ROWS
    partitions = await jobs.list_partitions(TENANT, kill_job.id)
    assert len(partitions) == 6
    assert any(p.resumed >= 1 or p.attempts >= 2 for p in partitions)
    assert await prepared.digest("kill", kill_job) == await prepared.digest(
        "ref", jobs_by_label["ref"]
    )
    if prepared.ledger:
        assert await ledger_rows(source_pg, kill_job.id) == 0  # cleared at finalize
    assert kill_job.progress.evidence_id is not None
    evidence = await get_evidence(TENANT, kill_job.progress.evidence_id)
    assert evidence is not None and evidence.pack.counts["resumed_partitions"] >= 1
    assert evidence.pack.verification["passed"]
