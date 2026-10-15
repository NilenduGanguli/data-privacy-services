"""``/api/v1/jobs`` (contract §9, scope ``jobs``; production writes need ``admin``, §5.3).

``POST /jobs/run`` returns the generated key material or synthesis seed **once** (``Cache-Control:
no-store``); an idempotent replay answers 200 with ``Idempotent-Replay: true`` and no key, and the
same ``Idempotency-Key`` with another request is 422 ``idempotency_key_reused`` (Layer 1's code). The
job's stored spec is the redacted one (templates, ids, fingerprints). Controls follow the queue's
rules (contract §3.1): 409 ``job_terminal`` / ``job_finalizing`` / ``job_not_pausable`` /
``job_not_paused`` / ``job_not_retryable`` / ``key_material_required`` / ``key_material_mismatch``.
``POST /jobs/run/validate`` answers what ``POST /jobs/run`` would refuse (same status and code) or
``{"ok": true}``, without creating a job or returning a key: the console's step 5 asks it before it
says "all checks passed".

Every ``/jobs/{id}`` route answers for any job kind, so each checks the job's kind against the key's
scopes as evidence and artefacts do (contract §8.1: ``scan`` covers scans): a scan read or
controlled here needs ``scan`` as well as the route's ``jobs`` (403 ``scope_required``, controls
audited ``rejected``), and ``GET /jobs`` lists no scan for a key without it (S2 adaptations r1 B1).
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Request, Response
from pydantic import BaseModel, ConfigDict

from dps import jobs
from dps.auth import require_scope
from dps.config import Settings
from dps.connections import get_connection
from dps.jobs import Job, JobKind, JobState
from dps.jobs_models import ConsistencySpec, DiscoverSpec, RunSpec, SpecError
from dps.layers.api.deps import (
    audit_action,
    audited,
    guarded,
    idempotency_key,
    kek_of,
    page_args,
    principal_of,
    rejected,
    request_fingerprint,
    require_db,
    tenant_of,
)
from dps.layers.api.errors import DpsHttpError
from dps.layers.api.idempotency import REPLAY_HEADER
from dps.layers.api.policy_refs import resolve
from dps.layers.api.routers.evidence import job_kinds, require_kind_scope
from dps.layers.connectors.submit import (
    check_run,
    seal_retry_secret,
    submit_discover,
    submit_run,
)
from dps.layers.connectors.targets import check_retry_scope
from dps.ratelimit import enforce_rate_limit
from dps.tenants import TenantError, check_daily_quota

router = APIRouter(prefix="/api/v1", tags=["jobs"])
_JOBS = [Depends(require_scope("jobs")), Depends(enforce_rate_limit), Depends(require_db)]


class RetryRequest(BaseModel):
    """Body of ``POST /jobs/{id}/retry``: the key material (or seed) when the job's was purged."""

    model_config = ConfigDict(hide_input_in_errors=True)
    consistency: ConsistencySpec | None = None


def _settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def _job(job: Job) -> dict[str, Any]:
    return job.model_dump(mode="json")


async def _owned(request: Request, job_id: str) -> Job:
    """The tenant's job, readable with this key: 404 ``job_not_found``, or 403 ``scope_required``
    when the key lacks the scope of the job's kind (a scan needs ``scan``)."""
    job = await jobs.get_job(tenant_of(request), job_id)
    if job is None:
        raise DpsHttpError(404, "job_not_found", "job not found")
    require_kind_scope(principal_of(request), job.kind.value)
    return job


async def _check_kind(request: Request, job_id: str) -> None:
    """404 ``job_not_found`` / 403 ``scope_required`` before a control acts on ``job_id``."""
    kind = (await job_kinds(tenant_of(request), [job_id])).get(job_id)
    if kind is None:
        raise DpsHttpError(404, "job_not_found", "job not found")
    require_kind_scope(principal_of(request), kind)


def _replayed(response: Response, job: Job) -> dict[str, Any]:
    response.status_code = 200
    response.headers[REPLAY_HEADER] = "true"
    return {"job": _job(job)}


@router.post("/jobs/discover", status_code=202, dependencies=_JOBS)
async def discover(body: DiscoverSpec, request: Request, response: Response) -> dict[str, Any]:
    """Queue a discover job (one proposed snapshot per object)."""
    counts = {"objects": len(body.objects)}
    try:
        body.policy = await resolve(request, body.policy)  # M5: @name[:vN] at accept time
        tenant, key = tenant_of(request), idempotency_key(request)
        fp = await request_fingerprint(request) if key else None
        earlier = await jobs.find_by_idempotency(tenant, key, fingerprint=fp) if key else None
        if earlier is None:
            job = await submit_discover(
                body,
                tenant_id=tenant,
                principal=principal_of(request),
                settings=_settings(request),
                kek=kek_of(request),
                idempotency_key=key,
                idempotency_fp=fp,
            )
    except Exception as exc:  # noqa: BLE001 - mapped to a value-free {"detail", "code"}
        raise rejected(request, "job.create", exc) from None
    if earlier is not None:
        audit_action(request, action="job.create", status=200, job_id=earlier.id, counts=counts)
        return _replayed(response, earlier)
    audit_action(request, action="job.create", status=202, job_id=job.id, counts=counts)
    return {"job": _job(job)}


@router.post("/jobs/run/validate", dependencies=_JOBS)
async def validate_run_spec(body: RunSpec, request: Request, response: Response) -> dict[str, bool]:
    """Run every check ``POST /jobs/run`` makes before a job exists; ``{"ok": true}`` or the
    run's own refusal. Nothing is queued, sealed, stored or returned."""

    async def body_() -> dict[str, bool]:
        body.policy = await resolve(request, body.policy)  # M5: as POST /jobs/run resolves it
        await check_run(
            body,
            tenant_id=tenant_of(request),
            principal=principal_of(request),
            settings=_settings(request),
            kek=kek_of(request),
        )
        return {"ok": True}

    response.headers["Cache-Control"] = "no-store"
    return await guarded(body_())


@router.post("/jobs/run", status_code=202, dependencies=_JOBS)
async def run(body: RunSpec, request: Request, response: Response) -> dict[str, Any]:
    """Validate, seal and queue a run; the generated key or seed is in this answer only."""
    try:
        key = idempotency_key(request)
        fp = await request_fingerprint(request) if key else None
        earlier = None
        if key:  # a key reused with another request is refused before any validation (as on L1)
            earlier = await jobs.find_by_idempotency(tenant_of(request), key, fingerprint=fp)
        # M5: an @name[:vN] reference becomes the stored body before validation, so the job
        # records exactly the version it ran
        body.policy = await resolve(request, body.policy)
        if earlier is None:  # M5: a replay of an accepted job is never refused for the quota
            try:
                await check_daily_quota(tenant_of(request), _settings(request))
            except TenantError as exc:
                raise DpsHttpError(exc.status, exc.code, exc.detail) from None
        sub = await submit_run(
            body,
            tenant_id=tenant_of(request),
            principal=principal_of(request),
            settings=_settings(request),
            kek=kek_of(request),
            idempotency_key=key,
            idempotency_fp=fp,
        )
    except Exception as exc:  # noqa: BLE001 - mapped to a value-free {"detail", "code"}
        raise rejected(request, "job.create", exc) from None
    audit_action(request, action="job.create", status=200 if sub.replay else 202, job_id=sub.job.id)
    if sub.replay:
        return _replayed(response, sub.job)
    out: dict[str, Any] = {"job": _job(sub.job)}
    if sub.key_material:
        out["key_material"] = sub.key_material
    if sub.synthesis_seed:
        out["synthesis_seed"] = sub.synthesis_seed
    response.headers["Cache-Control"] = "no-store"  # the key is in this body: never cached
    return out


@router.get("/jobs", dependencies=_JOBS)
async def list_(
    request: Request,
    kind: JobKind | None = None,
    state: JobState | None = None,
    group_id: str | None = None,
    limit: int = 50,
    cursor: str | None = None,
) -> dict[str, Any]:
    """The tenant's jobs, newest first (filters: kind, state, group); scans only for a key with
    ``scan`` (``kind=scan`` without it: 403 ``scope_required``)."""

    async def body() -> dict[str, Any]:
        scans = principal_of(request).has_scope("scan")
        if kind == JobKind.scan:
            require_kind_scope(principal_of(request), kind.value)
        items, nxt = await jobs.list_jobs(
            tenant_of(request),
            kind=kind,
            state=state,
            group_id=group_id,
            exclude_kind=None if scans else JobKind.scan,
            **page_args(limit, cursor),
        )
        return {"items": [_job(j) for j in items], "next_cursor": nxt}

    return await guarded(body())


@router.get("/jobs/stats", dependencies=_JOBS)
async def stats(request: Request) -> dict[str, Any]:
    """Workers, waiting partitions, throughput and the tenant's states (Jobs page health strip)."""

    async def body() -> dict[str, Any]:
        return (await jobs.tenant_stats(tenant_of(request))).model_dump(mode="json")

    return await guarded(body())


@router.get("/jobs/{job_id}", dependencies=_JOBS)
async def get(job_id: str, request: Request) -> dict[str, Any]:
    """One job (progress merged from its partitions); the console polls it every 2 s."""

    async def body() -> dict[str, Any]:
        return _job(await _owned(request, job_id))

    return await guarded(body())


@router.get("/jobs/{job_id}/partitions", dependencies=_JOBS)
async def partitions(job_id: str, request: Request) -> dict[str, Any]:
    """Value-free partition rows (the checkpoint is a keyed fingerprint, never a key)."""

    async def body() -> dict[str, Any]:
        await _owned(request, job_id)
        items = await jobs.list_partitions(tenant_of(request), job_id)
        return {"items": [p.model_dump(mode="json") for p in items]}

    return await guarded(body())


async def _control(request: Request, job_id: str, action: str) -> dict[str, Any]:
    idempotency_key(request)  # shape-checked; controls are idempotent by state

    async def body() -> dict[str, Any]:
        tenant = tenant_of(request)
        fn = {"cancel": jobs.cancel, "pause": jobs.pause, "resume": jobs.resume}[action]
        await _check_kind(request, job_id)  # a scan is controlled with the scan scope only
        job = await fn(tenant, job_id)
        if job is None:
            raise DpsHttpError(404, "job_not_found", "job not found")
        return _job(job)

    return await audited(request, f"job.{action}", body(), job_id=job_id)


@router.post("/jobs/{job_id}/cancel", dependencies=_JOBS)
async def cancel(job_id: str, request: Request) -> dict[str, Any]:
    """Cancel: queued/waiting/paused at once; running holders stop at their next fenced write."""
    return await _control(request, job_id, "cancel")


@router.post("/jobs/{job_id}/pause", dependencies=_JOBS)
async def pause(job_id: str, request: Request) -> dict[str, Any]:
    """Pause a run job that is processing partitions (each stops after its current batch)."""
    return await _control(request, job_id, "pause")


@router.post("/jobs/{job_id}/resume", dependencies=_JOBS)
async def resume(job_id: str, request: Request) -> dict[str, Any]:
    """Resume a paused run job."""
    return await _control(request, job_id, "resume")


@router.post("/jobs/{job_id}/retry", dependencies=_JOBS)
async def retry(
    job_id: str, request: Request, body: Annotated[RetryRequest | None, Body()] = None
) -> dict[str, Any]:
    """Requeue a failed/dead run job from its checkpoints; a purged key must be resubmitted."""
    idempotency_key(request)

    async def run_retry() -> dict[str, Any]:
        tenant = tenant_of(request)
        job = await _owned(request, job_id)
        # D3: a retry writes again — into a production-tagged connection it needs admin, as the
        # run did at submission (the job's target connection, else its source)
        written_id = job.target_connection_id or job.connection_id
        written = await get_connection(tenant, written_id) if written_id else None
        check_retry_scope(job, written, principal=principal_of(request))
        sealed: bytes | None = None
        consistency = body.consistency if body is not None else None
        if consistency is not None and (consistency.key_material or consistency.synthesis_seed):
            kek = kek_of(request)
            if kek is None:
                raise SpecError("kek_unconfigured", 503, "DPS_JOB_KEK is not configured")
            if job.kind != JobKind.run:
                raise DpsHttpError(
                    409, "job_not_retryable", "only failed or dead run jobs can be retried"
                )
            sealed = await seal_retry_secret(job, consistency, kek=kek)
        retried = await jobs.retry(tenant, job_id, sealed_secret=sealed)
        if retried is None:
            raise DpsHttpError(404, "job_not_found", "job not found")
        return _job(retried)

    return await audited(request, "job.retry", run_retry(), job_id=job_id)
