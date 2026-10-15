"""In-process driver for run tests: claims and runs job rows and partitions until a job is
terminal, plus small helpers (discover-and-accept, a PVC target, sealed-key checks)."""

from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from dps import db, jobs, snapshots
from dps.connections import Connection, create_connection
from dps.jobs import TERMINAL_JOB_STATES, ClaimedJob, Job, JobKind, JobPhase
from dps.jobs_models import (
    ClassificationSnapshot,
    ConnectionParams,
    ConnectionSpec,
    ObjectRef,
    SnapshotOverride,
)
from dps.layers.connectors.discovery import discover_job
from dps.layers.connectors.jobs_run import finalize, prepare, run_partition
from dps.worker import HandlerContext

#: The demo key reviewed as not personal, as an owner would for a technical id. D-A (G2 review
#: r1, B2): even then a synthesize run never copies or shifts it — offsets are obfuscate mechanics.
KEY_NOT_PERSONAL = [SnapshotOverride(path="$.customer_id", entity_type=None, anchor=False)]

PHASES: dict[JobPhase, Callable[[HandlerContext, ClaimedJob], Awaitable[None]]] = {
    JobPhase.discover: discover_job,
    JobPhase.prepare: prepare,
    JobPhase.finalize: finalize,
}


async def drive(ctx: HandlerContext, tenant: str, job_id: str, *, loops: int = 2000) -> Job:
    """Run claimed job phases and partitions in this process until ``job_id`` is terminal."""
    for _ in range(loops):
        job = await jobs.get_job(tenant, job_id)
        assert job is not None
        if job.state in TERMINAL_JOB_STATES:
            return job
        claimed = await jobs.claim_jobs(
            ctx.worker_id, kinds=(JobKind.discover, JobKind.run), batch=4
        )
        for c in claimed:
            await PHASES[c.phase](ctx, c)
        parts = await jobs.claim_partitions(ctx.worker_id, batch=8)
        for p in parts:
            internal = await jobs.load_job_internal(p.job_id)
            assert internal is not None
            await run_partition(ctx, internal, p)
        if not claimed and not parts:
            await asyncio.sleep(0.05)
    raise AssertionError("job did not reach a terminal state")


async def discover_and_accept(
    ctx: HandlerContext, tenant: str, connection_id: str, obj: ObjectRef, **review: Any
) -> ClassificationSnapshot:
    """Discover ``obj``, apply ``overrides=[...]`` while proposed, and accept the snapshot."""
    from dps.auth import LOCAL_PRINCIPAL
    from dps.jobs_models import DiscoverSpec
    from dps.layers.connectors.submit import submit_discover

    job = await submit_discover(
        DiscoverSpec(connection_id=connection_id, objects=[obj], sample_rows=500),
        tenant_id=tenant,
        principal=LOCAL_PRINCIPAL,
        settings=ctx.settings,
        kek=ctx.kek,
    )
    done = await drive(ctx, tenant, job.id)
    snap_id = done.progress.snapshot_ids[0]
    if review.get("overrides"):
        await snapshots.set_overrides(
            tenant, snap_id, review["overrides"], fpe_enabled=ctx.engine.fpe_enabled
        )
    return await snapshots.accept_snapshot(tenant, snap_id, accepted_by="test")


async def db_key_enc(job_id: str) -> bytes | None:
    """The job row's sealed key (``None`` once purged) — read through the worker pool."""
    async with db.acquire_queue(None) as conn:
        value = await conn.fetchval(
            f"SELECT key_enc FROM {db.schema_ident()}.dps_job WHERE id = $1", job_id
        )
    return bytes(value) if value is not None else None


async def register_pvc(tenant: str, ctx: HandlerContext, *, base_path: str) -> Connection:
    """A ``pvc`` target connection; points ``ctx.settings.export_root`` at a per-test directory."""
    root = Path(ctx.settings.secret_dir).parent / "export-root"
    root.mkdir(parents=True, exist_ok=True)
    ctx.settings = ctx.settings.model_copy(update={"export_root": str(root)})
    spec = ConnectionSpec(
        name=f"pvc-{secrets.token_hex(4)}",
        engine="pvc",
        params=ConnectionParams(base_path=base_path),
        roles={"target"},
    )
    return await create_connection(
        tenant, spec, settings=ctx.settings, kek=ctx.kek, created_by="test"
    )


def json_from_export(ctx: HandlerContext, key: str) -> Any:
    """Read a JSON object written under ``DPS_EXPORT_ROOT`` (a PVC target)."""
    return json.loads((Path(ctx.settings.export_root) / key).read_text(encoding="utf-8"))


def derived_key(label: str) -> dict[str, Any]:
    """Caller key material derived from ``label``: a fixed key a test can pin for a property it
    needs (a test fixture, not a secret — the DEK is ``sha256`` of the label)."""
    from dps.engine.transform.crypto import KeyMaterial, b64u, key_check_value

    km = KeyMaterial(
        kid="k_" + hashlib.sha256(f"kid|{label}".encode()).hexdigest()[:16],
        dek=b64u(hashlib.sha256(f"dek|{label}".encode()).digest()),
        consistency_scope="key",
    )
    km.kcv = key_check_value(km)
    return km.model_dump(mode="json")


def masked_values(
    ctx: HandlerContext,
    snapshot: ClassificationSnapshot,
    key_material: dict[str, Any],
    path: str,
    values: list[Any],
) -> dict[Any, Any]:
    """source value → the value an obfuscate run of ``snapshot`` under ``key_material`` writes at
    the top-level ``path``: the run's own column plan (``build_column_plans``) through the
    engine's batch transformer — deterministic methods only (FPE, ``det_token``). It lines target
    rows up with their source rows without reading any key from DPS."""
    from dps.engine.batch import BatchTransformer
    from dps.engine.transform.crypto import key_material_from_caller
    from dps.layers.connectors.targets import build_column_plans

    column = next(c for c in snapshot.columns if c.path == path)
    [plan] = [
        p
        for p in build_column_plans(
            snapshot,
            mode="obfuscate",
            write_mode="table",
            pk_strategy=None,
            projection=[column.name],
        )
        if p.path == path
    ]
    keys = {k: None for k in snapshot.shape.key if k != column.name}
    out = BatchTransformer(
        ctx.engine,
        policy=ctx.engine.policy(snapshot.policy_name),
        mode="obfuscate",
        columns=[plan],
        shape=snapshot.shape,
        key=key_material_from_caller(key_material),
        seed=None,
        locale="en_IN",
        unclassified="keep",
    ).transform([{**keys, column.name: v} for v in values])
    return {v: r[column.name] for v, r in zip(values, out.rows, strict=True)}
