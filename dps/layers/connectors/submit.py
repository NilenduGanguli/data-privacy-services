"""Run and discover submission (contract §3.3, §6.1): shared by the API routes and the tests.

``submit_run`` validates the spec (:func:`~dps.layers.connectors.targets.validate_run`), binds
every selection literal (``parameterize_literals``), creates or adopts the key material / seed,
seals the clear key and seed into ``key_enc`` (AAD ``dps_job.key:<id>``) and the full selection
into ``spec_enc`` (AAD ``dps_job.spec:<id>``), stores the redacted spec (templates, ids,
fingerprints) and returns the generated key or seed **once** — wrapped with the caller's RSA key
when ``wrap_key`` is given (DPS cannot unwrap it; the worker uses the clear key it sealed, for the
job's lifetime only). An idempotent replay returns the original job and no key. Errors are
:class:`~dps.jobs_models.SpecError` (code, status, value-free detail).
"""

from __future__ import annotations

from typing import Any, cast

from pydantic import BaseModel, ConfigDict

from dps.auth import Principal
from dps.config import Settings
from dps.connections import Connection, get_connection
from dps.engine.synth.surrogates import (
    SynthesisSeed,
    generate_synthesis_seed,
    seed_from_caller,
)
from dps.engine.transform.crypto import (
    KeyMaterial,
    generate_key_material,
    key_material_from_caller,
    wrap_key_material,
)
from dps.groups import get_group
from dps.ids import PREFIX_JOB, new_id
from dps.jobs import Job, JobKind, JobPhase, create_job
from dps.jobs_models import (
    ConsistencySpec,
    DiscoverSpec,
    JobSecret,
    RunSpec,
    SealedSelection,
    Selection,
    SpecError,
    redact_spec,
)
from dps.layers.connectors.base import Engine
from dps.layers.connectors.selection import (
    SelectionError,
    parameterize_literals,
    validate_selection,
)
from dps.layers.connectors.targets import validate_run
from dps.secrets import seal_json, seed_fingerprint
from dps.snapshots import get_snapshot

_TABULAR_ENGINES = ("postgres", "oracle", "mongo")


class RunSubmission(BaseModel):
    """What ``POST /jobs/run`` answers: the job and, once, the generated key or seed."""

    model_config = ConfigDict(hide_input_in_errors=True)
    job: Job
    key_material: dict[str, Any] | None = None
    synthesis_seed: dict[str, Any] | None = None
    replay: bool = False


def _has_selection(sel: Selection) -> bool:
    return bool(sel.where or sel.query or sel.mongo_filter is not None or sel.params)


def _bind(sel: Selection, engine: Engine, shape: Any) -> Selection:
    try:
        bound = parameterize_literals(sel, engine)
        validate_selection(bound, engine, shape)
    except SelectionError as exc:
        raise SpecError(exc.code, 422, str(exc)) from None
    return bound


def _sealed_selection(sel: Selection) -> SealedSelection:
    return SealedSelection(
        where=sel.where, query=sel.query, params=sel.params, mongo_filter=sel.mongo_filter
    )


async def _connection(tenant_id: str, connection_id: str, what: str) -> Connection:
    conn = await get_connection(tenant_id, connection_id)
    if conn is None:
        raise SpecError("connection_not_found", 404, f"{what} connection not found")
    return conn


async def submit_discover(
    spec: DiscoverSpec,
    *,
    tenant_id: str,
    principal: Principal,
    settings: Settings,
    kek: bytes | None,
    idempotency_key: str | None = None,
    idempotency_fp: str | None = None,
) -> Job:
    """Queue a discover job: bind and validate the selection, seal it, store the template.

    Raises:
        SpecError: ``connection_not_found`` 404, ``connection_role`` / ``engine_unavailable`` /
            ``selection_*`` 422, ``kek_unconfigured`` 503 (a selection needs sealing).
    """
    source = await _connection(tenant_id, spec.connection_id, "source")
    if "source" not in source.roles or source.engine not in _TABULAR_ENGINES:
        raise SpecError("connection_role", 422, "discovery reads a source database connection")
    if source.engine != "postgres":
        raise SpecError("engine_unavailable", 422, "M2 discovers PostgreSQL sources only")
    engine = cast(Engine, source.engine)
    selection = _bind(spec.selection, engine, None)
    job_id = new_id(PREFIX_JOB)
    sealed: bytes | None = None
    if _has_selection(selection):
        if kek is None:
            raise SpecError("kek_unconfigured", 503, "DPS_JOB_KEK is not configured")
        sealed = seal_json(kek, _sealed_selection(selection), aad=f"dps_job.spec:{job_id}")
    stored = spec.model_copy(update={"selection": selection})
    return await create_job(
        tenant_id=tenant_id,
        kind=JobKind.discover,
        phase=JobPhase.discover,
        spec=redact_spec(stored, engine=engine),
        sealed_selection=sealed,
        connection_id=source.id,
        object_ref=spec.objects[0].fqn(),
        created_by=principal.key_id,
        idempotency_key=idempotency_key,
        idempotency_fp=idempotency_fp,
        job_id=job_id,
    )


def _material(spec: RunSpec) -> tuple[KeyMaterial | None, SynthesisSeed | None]:
    consistency = spec.consistency
    try:
        if spec.mode == "obfuscate":
            km = (
                key_material_from_caller(consistency.key_material)
                if consistency.key_material
                else generate_key_material(consistency.scope)
            )
            return km, None
        seed = (
            seed_from_caller(consistency.synthesis_seed)
            if consistency.synthesis_seed
            else generate_synthesis_seed()
        )
        return None, seed
    except ValueError:
        raise SpecError(
            "key_material_invalid", 422, "the supplied key material or seed is not valid"
        ) from None


def _wrap(km: KeyMaterial, wrap_key: dict[str, str]) -> KeyMaterial:
    pem = wrap_key.get("public_key_pem")
    if wrap_key.get("type", "rsa-oaep-256") != "rsa-oaep-256" or not pem:
        raise SpecError(
            "wrap_key_invalid", 422, "wrap_key must be rsa-oaep-256 with a public_key_pem"
        )
    try:
        return wrap_key_material(km, pem)
    except ValueError:
        raise SpecError(
            "wrap_key_invalid", 422, "wrap_key must carry a readable RSA public key"
        ) from None


async def submit_run(
    spec: RunSpec,
    *,
    tenant_id: str,
    principal: Principal,
    settings: Settings,
    kek: bytes | None,
    idempotency_key: str | None = None,
    idempotency_fp: str | None = None,
) -> RunSubmission:
    """Validate, seal and queue a run job (module docstring).

    Raises:
        SpecError: every :func:`validate_run` code, ``connection_not_found`` / ``group_not_found``
            404, ``selection_*`` / ``key_material_invalid`` / ``wrap_key_invalid`` 422.
        QueueFull: the tenant's queue is full (429).
    """
    if kek is None:
        raise SpecError("kek_unconfigured", 503, "DPS_JOB_KEK is not configured")
    source = await _connection(tenant_id, spec.source.connection_id, "source")
    target = (
        await _connection(tenant_id, spec.target.connection_id, "target")
        if spec.target.connection_id and spec.target.connection_id != source.id
        else (source if spec.target.connection_id else None)
    )
    snapshot = await get_snapshot(tenant_id, spec.snapshot_id)
    linked = spec.options.linked
    child_snapshot = await get_snapshot(tenant_id, linked.child_snapshot_id) if linked else None
    group = await get_group(tenant_id, spec.group_id) if spec.group_id else None
    if spec.group_id and group is None:
        raise SpecError("group_not_found", 404, "job group not found")
    validate_run(
        spec,
        principal=principal,
        source_conn=source,
        target_conn=target,
        snapshot=snapshot,
        settings=settings,
        kek=kek,
        group=(group.kid, group.seed_fingerprint) if group else None,
        child_snapshot=child_snapshot,
    )
    engine = cast(Engine, source.engine)
    selection = _bind(spec.source.selection, engine, snapshot.shape if snapshot else None)
    km, seed = _material(spec)
    wrap = spec.consistency.wrap_key
    returned_key: KeyMaterial | None = None
    if km is not None and spec.consistency.key_material is None:
        # validated before the job exists, so a bad wrap key never leaves a queued job behind
        returned_key = _wrap(km, wrap) if wrap else km
    job_id = new_id(PREFIX_JOB)
    secret = JobSecret(
        key_material=km.model_dump(mode="json") if km else None,
        synthesis_seed=seed.model_dump(mode="json") if seed else None,
    )
    sealed_secret = seal_json(kek, secret, aad=f"dps_job.key:{job_id}")
    sealed_selection = seal_json(kek, _sealed_selection(selection), aad=f"dps_job.spec:{job_id}")
    stored = spec.model_copy(
        update={"source": spec.source.model_copy(update={"selection": selection})}
    )
    redacted = redact_spec(stored, engine=engine)
    redacted["consistency"]["kid"] = km.kid if km else None
    redacted["consistency"]["seed_fingerprint"] = seed_fingerprint(seed.seed) if seed else None
    redacted["consistency"]["caller_seed"] = spec.consistency.synthesis_seed is not None
    job = await create_job(
        tenant_id=tenant_id,
        kind=JobKind.run,
        phase=JobPhase.prepare,
        spec=redacted,
        mode=spec.mode,
        sealed_secret=sealed_secret,
        sealed_selection=sealed_selection,
        kid=km.kid if km else None,
        group_id=spec.group_id,
        connection_id=source.id,
        target_connection_id=target.id if target else None,
        object_ref=spec.source.object.fqn(),
        snapshot_id=spec.snapshot_id,
        purpose=spec.purpose.model_dump(),
        created_by=principal.key_id,
        idempotency_key=idempotency_key,
        idempotency_fp=idempotency_fp,
        job_id=job_id,
    )
    replay = job.id != job_id
    key_out = returned_key.model_dump(mode="json") if returned_key and not replay else None
    seed_out = (
        seed.model_dump(mode="json")
        if seed is not None and not replay and spec.consistency.synthesis_seed is None
        else None
    )
    return RunSubmission(job=job, key_material=key_out, synthesis_seed=seed_out, replay=replay)


async def seal_retry_secret(job: Job, consistency: ConsistencySpec, *, kek: bytes) -> bytes:
    """Seal the key material (or seed) a retry body brings back for a job whose key was purged.

    Raises:
        SpecError: ``key_material_required`` 409 (nothing for the job's mode),
            ``key_material_invalid`` 422, ``key_material_mismatch`` 409 (another key or seed).
    """
    try:
        if job.mode == "obfuscate":
            if not consistency.key_material:
                raise SpecError("key_material_required", 409, "the retry needs the job's key")
            km = key_material_from_caller(consistency.key_material)
            if km.kid != job.kid:
                raise SpecError("key_material_mismatch", 409, "the key is not the job's key")
            secret = JobSecret(key_material=km.model_dump(mode="json"))
        else:
            if not consistency.synthesis_seed:
                raise SpecError("key_material_required", 409, "the retry needs the job's seed")
            seed = seed_from_caller(consistency.synthesis_seed)
            expected = (job.spec.get("consistency") or {}).get("seed_fingerprint")
            if seed_fingerprint(seed.seed) != expected:
                raise SpecError("key_material_mismatch", 409, "the seed is not the job's seed")
            secret = JobSecret(synthesis_seed=seed.model_dump(mode="json"))
    except ValueError:
        raise SpecError(
            "key_material_invalid", 422, "the supplied key material or seed is not valid"
        ) from None
    return seal_json(kek, secret, aad=f"dps_job.key:{job.id}")
