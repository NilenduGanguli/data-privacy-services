"""Layer 3 — the UI scanner (spec §8, decision D4): the ``scan`` job handlers.

``scan_job`` (phase ``scan``) opens the job's sealed secret into a :class:`ScanRuntime`, crawls
with :class:`~dps.layers.scanner.crawler.ScanRunner` into a :class:`DbScanSink` and finalizes:
exports (synthetic personas and tokens of them), a ``dps.evidence.v1`` pack with its ``scan``
block, and the key purge. ``scan_finalize_job`` (phase ``finalize``) is the cancel path: a canceled
(or failed) scan still gets its evidence and exports, and its key purged.

Import hygiene: the API imports this package, so nothing here imports Playwright or Pillow at
module level (the crawler and the browser are imported inside :func:`scan_job`).
"""

from __future__ import annotations

import dataclasses
import json
import re
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal

from dps import db, jobs
from dps.audit import AuditEvent
from dps.engine.service import EngineError
from dps.engine.synth.surrogates import seed_from_caller
from dps.engine.transform.crypto import key_material_from_caller
from dps.ids import PREFIX_EVIDENCE, new_id
from dps.jobs import ClaimedJob, JobPhase, JobState
from dps.jobs_models import JobProgress, JobSecret, ScanSpec
from dps.layers.connectors.evidence import pack_sha256, render_html
from dps.layers.scanner.inventory import build_export, build_scan_evidence
from dps.layers.scanner.personas_fill import persona_values
from dps.layers.scanner.runtime import (
    LeaseLost,
    MemoryScanSink,
    RunOutcome,
    ScanRuntime,
    ScanSink,
)
from dps.layers.scanner.store import ScanStore, StoredEvidence
from dps.layers.scanner.urls import CrawlScope, Origin, UrlError, canonical
from dps.observability import get_logger
from dps.secrets import SealError, SecretUnavailable, open_sealed, resolve_secret_ref

if TYPE_CHECKING:
    from dps.layers.scanner.models import FieldRecord, PageRecord, RequestCount, ScanEvent
    from dps.worker import HandlerContext

__all__ = [
    "DbScanSink",
    "LeaseLost",
    "MemoryScanSink",
    "RunOutcome",
    "ScanRuntime",
    "ScanSetupError",
    "ScanSink",
    "finalize_scan",
    "open_runtime",
    "scan_finalize_job",
    "scan_job",
]

logger = get_logger(__name__)
#: Tenant folder names under ``DPS_SECRET_DIR`` (SEC-2's rule: lower-case, never ``_``-first).
_TENANT_FOLDER = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")
#: Keys DPS adds to the stored spec's ``consistency`` that ``ScanSpec`` does not know.
_ADDED_CONSISTENCY = ("key_wrapped", "wrapped", "caller_key", "caller_seed")


class ScanSetupError(Exception):
    """A scan that cannot start; ``str()`` is the code (never a value)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _storage_state(tenant_id: str, ref: str, ctx: HandlerContext) -> dict[str, Any]:
    """The scan's storage state from ``DPS_SECRET_DIR/<tenant_id>/<ref>/storage_state.json``.

    Tenant-scoped (security review SEC-2): a scan resolves its reference under its own tenant's
    folder only, never in the global namespace. (Integration onto main replaces this with SEC-2's
    ``resolve_secret_ref(tenant_id, ref, settings)``, which applies the same layout.)
    """
    if not _TENANT_FOLDER.fullmatch(tenant_id) or ref.startswith(("/", "_")):
        raise ScanSetupError("secret_unavailable")
    try:
        files = resolve_secret_ref(f"{tenant_id}/{ref}", ctx.settings)
        state = json.loads(files["storage_state.json"])
    except (SecretUnavailable, KeyError, ValueError):
        raise ScanSetupError("secret_unavailable") from None
    if not isinstance(state, dict):
        raise ScanSetupError("secret_unavailable")
    return state


async def open_runtime(ctx: HandlerContext, job: ClaimedJob, store: ScanStore) -> ScanRuntime:
    """The :class:`ScanRuntime` of a claimed scan job, from its sealed secret and value-free spec.

    Raises :class:`ScanSetupError` with ``key_unavailable`` (purged), ``secret_unavailable`` (the
    seal or the storage state cannot be opened), ``spec_invalid``, ``policy_invalid`` or
    ``host_not_registered`` (the tenant removed the start host after accept). Nothing sealed is
    ever chained into an exception.
    """
    if job.key_enc is None:
        raise ScanSetupError("key_unavailable")
    try:
        raw = open_sealed(ctx.kek, job.key_enc, aad=f"dps_job.key:{job.id}")
        secret = JobSecret.model_validate_json(raw)
    except (SealError, ValueError):
        raise ScanSetupError("secret_unavailable") from None
    if not secret.scan_start_url or secret.key_material is None or secret.synthesis_seed is None:
        raise ScanSetupError("secret_unavailable")
    data = json.loads(json.dumps(job.spec))
    data.pop("url_origin", None)
    data["url"] = secret.scan_start_url
    ref = (data.get("auth") or {}).get("storage_state_ref")
    data["auth"] = {"storage_state_ref": ref} if ref else {}
    for added in _ADDED_CONSISTENCY:
        (data.get("consistency") or {}).pop(added, None)
    try:
        spec = ScanSpec.model_validate(data)
    except ValueError:
        raise ScanSetupError("spec_invalid") from None
    if ref:
        storage_state: dict[str, Any] | None = _storage_state(job.tenant_id, ref, ctx)
    else:
        storage_state = secret.scan_auth
    try:
        key = key_material_from_caller(secret.key_material)
        seed = seed_from_caller(secret.synthesis_seed)
    except ValueError:
        raise ScanSetupError("secret_unavailable") from None
    try:
        policy = ctx.engine.policy(spec.policy)
    except EngineError:
        raise ScanSetupError("policy_invalid") from None
    async with db.acquire_queue(job.tenant_id) as conn:
        hosts = await store.registered_hosts(conn, job.tenant_id)
    if not hosts and ctx.settings.env in ("local", "dev"):
        hosts = ctx.settings.scan_registered_hosts_list
    start = canonical(spec.url)
    try:
        origin = Origin.of(start or "")
    except UrlError:
        raise ScanSetupError("spec_invalid") from None
    registered = frozenset(h.strip().lower() for h in hosts if h and h.strip())
    if start is None or origin.host not in registered:
        raise ScanSetupError("host_not_registered")
    max_pages = spec.scope.max_pages or ctx.settings.scan_max_pages
    max_depth = (
        spec.scope.max_depth if spec.scope.max_depth is not None else ctx.settings.scan_max_depth
    )
    return ScanRuntime(
        spec=spec,
        start_url=start,
        key=key,
        seed=seed,
        storage_state=storage_state,
        policy=policy,
        locale=spec.fill.locale,
        max_pages=min(max_pages, ctx.settings.scan_max_pages),
        max_depth=max_depth,
        scope=CrawlScope(
            start=origin,
            same_origin=spec.scope.same_origin,
            registered_hosts=registered,
            allow=tuple(spec.scope.allow),
            deny=tuple(spec.scope.deny),
        ),
    )


class DbScanSink:
    """:class:`ScanSink` over the metadata DB (one transaction per batch), the artefact service
    and the audit sink (every navigation is audited, without its URL)."""

    def __init__(self, ctx: HandlerContext, job: ClaimedJob, store: ScanStore) -> None:
        self.ctx, self.job, self.store = ctx, job, store
        spec_policy = (job.spec or {}).get("policy")
        self._policy = spec_policy if isinstance(spec_policy, str) else ""

    def __repr__(self) -> str:
        return f"DbScanSink(job_id={self.job.id!r})"

    async def save_batch(
        self,
        *,
        pages: list[PageRecord],
        fields: dict[int, list[FieldRecord]],
        requests: list[RequestCount],
        events: list[ScanEvent],
    ) -> None:
        """Upsert pages, replace fields, add request counts and append events together."""
        tenant, job_id = self.job.tenant_id, self.job.id
        async with db.acquire_queue(tenant) as conn, conn.transaction():
            for page in pages:
                await self.store.upsert_page(conn, tenant, job_id, page)
            for page_no, records in fields.items():
                await self.store.replace_fields(conn, tenant, job_id, page_no, records)
            await self.store.add_request_counts(conn, tenant, job_id, requests)
            await self.store.append_events(conn, tenant, job_id, events)

    async def save_artefact(
        self, *, kind: str, data: bytes, content_type: str, meta: dict[str, Any]
    ) -> str:
        """Store through the artefact service (tenant- and job-scoped)."""
        return await self.ctx.artefacts.save(
            tenant_id=self.job.tenant_id,
            job_id=self.job.id,
            kind=kind,
            data=data,
            content_type=content_type,
            meta=meta,
        )

    async def progress(self, progress: dict[str, Any]) -> bool:
        """A fenced progress write; ``False`` = the lease is gone (canceled or reaped)."""
        return await jobs.advance_job(
            self.ctx.worker_id,
            self.job.id,
            phase=JobPhase.scan,
            state=JobState.running,
            progress=JobProgress(
                pages=int(progress.get("pages", 0)),
                fields=int(progress.get("fields", 0)),
                blocked_requests=int(progress.get("blocked_requests", 0)),
            ).model_dump(mode="json", include={"pages", "fields", "blocked_requests"}),
        )

    async def audit_navigation(self, *, page_no: int, outcome: str) -> None:
        """One audit row per navigation (no URL: ``page_no`` is in the scan's own tables)."""
        self.ctx.audit.write(
            AuditEvent(
                trace_id=self.job.id,
                route="browser:navigate",
                tenant_id=self.job.tenant_id,
                key_id=self.job.created_by or "-",
                mode=self.job.mode or "",
                policy=self._policy,
                outcome=outcome,
                action="scan.navigate",
                job_id=self.job.id,
                counts={"page_no": page_no},
            )
        )


async def scan_job(ctx: HandlerContext, job: ClaimedJob) -> None:
    """Phase ``scan``: open the runtime, crawl, finalize (``succeeded``). A setup error fails the
    job with its code and purges the key; a lost lease returns (the new owner finalizes)."""
    from dps.layers.scanner.browser import get_browser
    from dps.layers.scanner.crawler import ScanRunner

    store = ScanStore(ctx.settings.pg_schema)
    try:
        runtime = await open_runtime(ctx, job, store)
    except ScanSetupError as exc:
        logger.info("scan_setup_failed", job_id=job.id, error_code=exc.code)
        await jobs.advance_job(
            ctx.worker_id,
            job.id,
            phase=JobPhase.scan,
            state=JobState.failed,
            progress={"outcome": "failed"},
            error_code=exc.code,
            error=exc.code,
            purge_secret=True,
        )
        return
    async with db.acquire_queue(job.tenant_id) as conn:
        stored = await store.existing_evidence(conn, job.id)
    if stored is not None:
        # a pack exists (written before the finalize fence existed): its artefacts are kept and
        # the job ends with it; crawling again would only add a second, contradicting pack
        logger.info("scan_evidence_exists", job_id=job.id)
        await finalize_scan(ctx, job, runtime, store=store, outcome=None, result="succeeded")
        return
    async with db.acquire_queue(job.tenant_id) as conn:  # a reaped-and-retried attempt starts clean
        await store.reset_scan(conn, job.id)
    await ctx.artefacts.delete_for_job(job.tenant_id, job.id)
    runner = ScanRunner(
        settings=ctx.settings,
        engine=ctx.engine,
        runtime=runtime,
        sink=DbScanSink(ctx, job, store),
        browser=await get_browser(ctx.settings),
    )
    try:
        outcome = await runner.run()
    except LeaseLost:
        logger.info("scan_lease_lost", job_id=job.id)  # canceled or reaped: the new owner finalizes
        return
    await finalize_scan(ctx, job, runtime, store=store, outcome=outcome, result="succeeded")


async def scan_finalize_job(ctx: HandlerContext, job: ClaimedJob) -> None:
    """Phase ``finalize``: the cancel path (contract §3.1), and the retry of a finalize that a
    crash or a lost lease interrupted. Evidence, exports, key purge."""
    store = ScanStore(ctx.settings.pg_schema)
    runtime: ScanRuntime | None
    try:
        runtime = await open_runtime(ctx, job, store)
    except ScanSetupError:
        runtime = None  # key already purged (or unusable): evidence without exports
    crawl = (job.plan or {}).get(_PLAN_KEY) if isinstance(job.plan, dict) else None
    result: Literal["succeeded", "failed", "canceled"]
    outcome: RunOutcome | None = None
    if job.progress.outcome == "canceled":
        result = "canceled"
    elif isinstance(crawl, dict) and crawl.get("result") in ("succeeded", "failed"):
        result = crawl["result"]  # the crawl finished; its finalize is being retried
        outcome = _run_outcome(crawl.get("outcome"))
    else:
        result = "failed"
    await finalize_scan(ctx, job, runtime, store=store, outcome=outcome, result=result)


#: ``dps_job.plan`` key under which a scan keeps its crawl's result and counters (value-free) when
#: it enters the finalize phase, so a retried finalize builds the same pack.
_PLAN_KEY = "scan_finalize"
#: the artefact kinds a finalize writes (an interrupted attempt's are replaced, never doubled)
_FINAL_KINDS = ("export_json", "export_csv", "evidence_html")


def _run_outcome(data: object) -> RunOutcome | None:
    """A :class:`RunOutcome` back from the job row's plan (unknown keys dropped)."""
    if not isinstance(data, dict):
        return None
    names = {f.name for f in dataclasses.fields(RunOutcome)}
    try:
        return RunOutcome(**{k: v for k, v in data.items() if k in names})
    except TypeError:
        return None


async def finalize_scan(
    ctx: HandlerContext,
    job: ClaimedJob,
    runtime: ScanRuntime | None,
    *,
    store: ScanStore,
    outcome: RunOutcome | None,
    result: Literal["succeeded", "failed", "canceled"],
) -> str | None:
    """Build the exports and the evidence pack, purge the key, set the terminal state; returns the
    evidence id, or ``None`` when the lease is gone (a cancel or the reaper won: the new owner
    finalizes, and nothing was written here).

    Fenced and idempotent (contract §3.1, one finalize per job). A job still in phase ``scan``
    first moves to ``finalize/running`` through the lease fence, so a cancel either wins before
    anything is written or is refused (``job_finalizing``). A pack an earlier attempt stored is
    reused (with its outcome); otherwise an interrupted attempt's exports and HTML are replaced and
    the pack is inserted under the job-row lock while the lease holds. The ``scan.finalize`` audit
    row is written only after the terminal transition applied. A non-GET request the guard allowed
    (unreachable by construction, measured anyway) fails the scan with ``submission_detected``.
    """
    tenant = job.tenant_id
    if job.phase != JobPhase.finalize:
        crawl = {
            "result": result,
            "outcome": dataclasses.asdict(outcome) if outcome is not None else None,
        }
        if not await jobs.advance_job(
            ctx.worker_id,
            job.id,
            phase=JobPhase.finalize,
            state=JobState.running,
            progress={"stage": "finalize"},
            plan={_PLAN_KEY: crawl},
        ):
            logger.info("scan_finalize_fenced", job_id=job.id)
            return None
    async with db.acquire_queue(tenant) as conn:
        pages = await store.all_pages(conn, job.id)
        fields = await store.list_fields(conn, job.id)
        counts = await store.summary_counts(conn, job.id)
        stored = await store.existing_evidence(conn, job.id)
    errors: list[dict[str, str]] = []
    if counts["non_get_allowed"]:
        result = "failed"
        errors.append({"code": "submission_detected", "where": "guard"})
    if stored is not None:
        evidence_id, policy_name = stored.id, None
        if stored.outcome in ("succeeded", "failed", "canceled"):
            result = stored.outcome  # type: ignore[assignment]
        await ctx.artefacts.delete_for_job(
            tenant, job.id, kinds=_FINAL_KINDS, keep=stored.artefact_ids
        )
    else:
        written = await _write_pack(
            ctx,
            job,
            runtime,
            store=store,
            pages=pages,
            fields=fields,
            counts=counts,
            outcome=outcome,
            result=result,
            errors=errors,
        )
        if written is None:
            return None
        evidence_id, policy_name, stored = written
        if stored is not None and stored.outcome in ("succeeded", "failed", "canceled"):
            result = stored.outcome  # type: ignore[assignment]
    progress = JobProgress(
        outcome=result,
        pages=int(counts["pages_visited"]),
        fields=int(counts["fields"]),
        blocked_requests=sum((counts["blocked"] or {}).values()),
        evidence_id=evidence_id,
    ).model_dump(
        mode="json",
        include={"outcome", "stage", "pages", "fields", "blocked_requests", "evidence_id"},
    )
    code = errors[0]["code"] if errors and result == "failed" else None
    if not await jobs.advance_job(
        ctx.worker_id,
        job.id,
        phase=JobPhase.finalize,
        state=JobState(result),
        progress=progress,
        error_code=code,
        error=code,
        purge_secret=True,
    ):
        logger.info("scan_finalize_fenced", job_id=job.id)
        return None
    if policy_name is None:
        policy_name = _policy_name(ctx, job, runtime)
    ctx.audit.write(
        AuditEvent(
            trace_id=job.id,
            route="browser:finalize",
            tenant_id=tenant,
            key_id=job.created_by or "-",
            mode=job.mode or "",
            policy=policy_name,
            outcome=result,
            action="scan.finalize",
            job_id=job.id,
        )
    )
    logger.info("scan_finalized", job_id=job.id, outcome=result, pages=progress["pages"])
    return evidence_id


def _policy_name(ctx: HandlerContext, job: ClaimedJob, runtime: ScanRuntime | None) -> str:
    if runtime is not None:
        return runtime.policy.name
    try:
        return ctx.engine.policy((job.spec or {}).get("policy")).name
    except EngineError:
        return ""


async def _write_pack(
    ctx: HandlerContext,
    job: ClaimedJob,
    runtime: ScanRuntime | None,
    *,
    store: ScanStore,
    pages: list[Any],
    fields: list[Any],
    counts: dict[str, Any],
    outcome: RunOutcome | None,
    result: Literal["succeeded", "failed", "canceled"],
    errors: list[dict[str, str]],
) -> tuple[str, str, StoredEvidence | None] | None:
    """Write the exports, the HTML and the pack (inserted under the job-row lock while the lease
    holds), then remove an interrupted attempt's exports and HTML. Returns ``(evidence id, policy name, None)``; ``(id, name, stored)`` when an earlier
    owner's pack turned up under the lock (this attempt's artefacts are removed and that pack is
    used); ``None`` when the lease is gone (this attempt's artefacts are removed)."""
    tenant = job.tenant_id
    written: list[str] = []
    exports: dict[str, str] = {}
    if runtime is not None:
        personas = [
            persona_values(
                ctx.engine,
                seed=runtime.seed,
                persona_no=n,
                locale=runtime.locale,
                policy=runtime.policy,
            )
            for n in range(1, runtime.spec.fill.personas + 1)
        ]
        export_json, export_csv = build_export(
            job_id=job.id,
            runtime=runtime,
            pages=pages,
            fields=fields,
            personas=personas,
            engine=ctx.engine,
        )
        meta = {"personas": len(personas)}
        exports["json"] = await ctx.artefacts.save(
            tenant_id=tenant,
            job_id=job.id,
            kind="export_json",
            data=export_json,
            content_type="application/json",
            meta=meta,
        )
        written.append(exports["json"])
        exports["csv"] = await ctx.artefacts.save(
            tenant_id=tenant,
            job_id=job.id,
            kind="export_csv",
            data=export_csv,
            content_type="text/csv; charset=utf-8",
            meta=meta,
        )
        written.append(exports["csv"])
    policy: dict[str, str] | None = None
    if runtime is None:
        try:
            p = ctx.engine.policy((job.spec or {}).get("policy"))
            policy = {"name": p.name, "hash": p.config_hash()}
        except EngineError:
            policy = None
    evidence_id = new_id(PREFIX_EVIDENCE)
    pack = build_scan_evidence(
        evidence_id=evidence_id,
        job=job,
        runtime=runtime,
        pages=pages,
        fields=fields,
        counts=counts,
        outcome=outcome,
        result=result,
        errors=errors,
        settings=ctx.settings,
        exports=exports,
        policy=policy,
    )
    sha = pack_sha256(pack)
    html_id = await ctx.artefacts.save(
        tenant_id=tenant,
        job_id=job.id,
        kind="evidence_html",
        data=render_html(pack, sha=sha).encode("utf-8"),
        content_type="text/html; charset=utf-8",
        meta={"evidence_id": evidence_id},
    )
    written.append(html_id)
    expires = datetime.now(UTC) + timedelta(days=ctx.settings.retention_days)
    earlier: StoredEvidence | None = None
    async with db.acquire_queue(tenant) as conn, conn.transaction():
        leased = await store.lock_leased_job(conn, job.id, ctx.worker_id)
        if leased:
            earlier = await store.existing_evidence(conn, job.id)
            if earlier is None:
                await store.insert_evidence(
                    conn,
                    tenant,
                    job.id,
                    pack,
                    pack_sha256=sha,
                    html_artefact_id=html_id,
                    expires_at=expires,
                )
    if not leased or earlier is not None:
        await ctx.artefacts.delete_for_job(tenant, job.id, ids=written)
    if not leased:
        logger.info("scan_finalize_fenced", job_id=job.id)
        return None
    name = pack.policy.get("name", "")
    if earlier is not None:
        return earlier.id, name, earlier
    # the pack is in: an interrupted attempt's exports and HTML (no pack references them) go.
    # Only the owner that inserted the pack deletes; a stale attempt only ever deletes its own.
    await ctx.artefacts.delete_for_job(tenant, job.id, kinds=_FINAL_KINDS, keep=written)
    return evidence_id, name, None
