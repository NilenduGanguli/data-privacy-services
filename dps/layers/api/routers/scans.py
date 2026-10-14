"""``/api/v1/scans`` (contract §9, scope ``scan``): the UI scanner's routes.

``POST /scans`` validates the spec against the tenant's registered hosts, seals the start URL, the
key material, the synthesis seed and any inline storage state into ``key_enc`` and queues a
``scan`` job; the key and the seed are in that answer **only** (``Cache-Control: no-store``), and
an idempotent replay returns neither. Every read is value-free (redacted URLs, counts, codes) or
synthetic (persona values and tokens of them). There is no route that submits a form, writes to a
scanned site, or returns stored key material (decision D4, the contract §9 closing rule).
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import StreamingResponse
from fastapi.routing import APIRoute

from dps import db, jobs
from dps.auth import require_scope
from dps.config import Settings
from dps.engine.service import EngineError, PrivacyEngine
from dps.engine.synth.personas import normalise_locale
from dps.engine.synth.surrogates import generate_synthesis_seed, seed_from_caller
from dps.engine.transform.crypto import (
    KeyMaterial,
    generate_key_material,
    key_material_from_caller,
    wrap_key_material,
)
from dps.ids import new_id
from dps.jobs import Job, JobKind, JobPhase, JobState, JobStateError
from dps.jobs_models import JobSecret, ScanSpec
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
from dps.layers.scanner.classify import value_classifier
from dps.layers.scanner.inventory import (
    inventory_csv,
    inventory_rows,
    number_flags,
    validation_totals,
)
from dps.layers.scanner.models import (
    PersonaPreview,
    PersonaPreviewRequest,
    ScanCreated,
    ScanDetail,
    ScanPageDetail,
    ScanSpecError,
    ScanSummary,
    redact_scan_spec,
    spec_refusal_code,
    validate_scan,
)
from dps.layers.scanner.personas_fill import persona_values, preview_entities
from dps.layers.scanner.store import ScanStore
from dps.layers.scanner.urls import redact_url
from dps.ratelimit import enforce_rate_limit
from dps.secrets import seal

router = APIRouter(prefix="/api/v1/scans", tags=["scans"])
_SCAN = [Depends(require_scope("scan")), Depends(enforce_rate_limit), Depends(require_db)]
_MAX_PAGE = 200
#: What the persona generators guarantee, per locale (the New-scan page shows these).
NOTES: dict[str, list[str]] = {
    "en_IN": [
        "Aadhaar numbers are Verhoeff-valid and generated, not issued",
        "PAN values are format-valid",
        "one persona per record: name, gender, e-mail and birth date agree",
    ],
    "en_US": [
        "SSNs use the never-issued 9xx area",
        "phone numbers are format-valid for the locale",
        "one persona per record: name, gender, e-mail and birth date agree",
    ],
    "en_GB": [
        "National Insurance numbers are format-valid and generated, not issued",
        "phone numbers are format-valid for the locale",
        "one persona per record: name, gender, e-mail and birth date agree",
    ],
    "en_CA": [
        "SINs are Luhn-valid and generated, not issued",
        "phone numbers are format-valid for the locale",
        "one persona per record: name, gender, e-mail and birth date agree",
    ],
    "es_MX": [
        "CURP and RFC values are format-valid and generated, not issued",
        "phone numbers are format-valid for the locale",
        "one persona per record: name, gender, e-mail and birth date agree",
    ],
}


def _settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def _engine(request: Request) -> PrivacyEngine:
    engine: PrivacyEngine = request.app.state.engine
    return engine


def _store(request: Request) -> ScanStore:
    return ScanStore(_settings(request).pg_schema)


async def _scan(request: Request, scan_id: str) -> Job:
    """The caller's scan, or 404 ``scan_not_found`` (another tenant's scan, or another kind)."""
    job = await jobs.get_job(tenant_of(request), scan_id)
    if job is None or job.kind != JobKind.scan:
        raise DpsHttpError(404, "scan_not_found", "scan not found")
    return job


async def _hosts(request: Request, tenant: str) -> frozenset[str]:
    async with db.acquire(tenant) as conn:
        hosts = await _store(request).registered_hosts(conn, tenant)
    settings = _settings(request)
    if not hosts and settings.env in ("local", "dev"):
        hosts = settings.scan_registered_hosts_list
    return frozenset(hosts)


def _material(spec: ScanSpec) -> tuple[KeyMaterial, Any]:
    """The scan's key and seed: the caller's, or fresh ones (both are always made, D2)."""
    cons = spec.consistency
    try:
        key = (
            key_material_from_caller(cons.key_material)
            if cons.key_material
            else generate_key_material(cons.scope)
        )
        seed = (
            seed_from_caller(cons.synthesis_seed)
            if cons.synthesis_seed
            else generate_synthesis_seed()
        )
    except ValueError:
        raise DpsHttpError(
            422, "key_material_invalid", "the supplied key material or seed is not valid"
        ) from None
    return key, seed


def _wrapped(key: KeyMaterial, wrap_key: dict[str, str] | None) -> KeyMaterial:
    if wrap_key is None:
        return key
    pem = wrap_key.get("public_key_pem")
    if wrap_key.get("type", "rsa-oaep-256") != "rsa-oaep-256" or not pem:
        raise DpsHttpError(
            422, "wrap_key_invalid", "wrap_key must be rsa-oaep-256 with a public_key_pem"
        )
    try:
        return wrap_key_material(key, pem)
    except ValueError:
        raise DpsHttpError(
            422, "wrap_key_invalid", "wrap_key must carry a readable RSA public key"
        ) from None


class _CreateScanRoute(APIRoute):
    """``POST /scans``: a body the ``ScanSpec`` model refuses answers the contract §9 code.

    The model checks most bounds and cross-field rules before :func:`validate_scan` runs, so a
    ``RequestValidationError`` is mapped by location with :func:`spec_refusal_code` to a value-free
    ``{"detail", "code"}`` 422 and audited as a refusal, like every other accept-time refusal. An
    error with no code (an unknown key, decision D4; another section's field) keeps FastAPI's
    ``[{type, loc, msg}]`` 422. The route's dependencies (scope, rate limit, database) run first.
    """

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        async def refusing(request: Request) -> Response:
            try:
                return await handler(request)
            except RequestValidationError as exc:
                code = spec_refusal_code(exc.errors())
                if code is None:
                    raise
                err = DpsHttpError(422, code, f"the scan was refused: {code}")
                raise rejected(request, "scan.create", err) from None

        return refusing


async def create_scan(body: ScanSpec, request: Request, response: Response) -> dict[str, Any]:
    """Validate, seal and queue a scan; the key and the seed are in this answer only."""
    settings, engine = _settings(request), _engine(request)
    try:
        tenant = tenant_of(request)
        key_header = idempotency_key(request)
        fp = await request_fingerprint(request) if key_header else None
        earlier = (
            await jobs.find_by_idempotency(tenant, key_header, fingerprint=fp)
            if key_header
            else None
        )
        if earlier is not None:
            if earlier.kind != JobKind.scan:
                raise jobs.IdempotencyKeyReused()
            audit_action(request, action="scan.create", status=200, job_id=earlier.id)
            response.status_code = 200
            response.headers[REPLAY_HEADER] = "true"
            return ScanCreated(scan=earlier, replay=True).model_dump(mode="json")
        kek = kek_of(request)
        if kek is None:
            raise DpsHttpError(503, "kek_unconfigured", "scans need DPS_JOB_KEK to seal the scan")
        try:
            validated = validate_scan(
                body, settings=settings, hosts=await _hosts(request, tenant), tenant_id=tenant
            )
        except ScanSpecError as exc:
            raise DpsHttpError(exc.status, exc.code, f"the scan was refused: {exc.code}") from None
        try:
            policy = engine.policy(body.policy)
        except EngineError:
            raise DpsHttpError(422, "policy_invalid", "the policy does not resolve") from None
        key, seed = _material(body)
        key_out = _wrapped(key, body.consistency.wrap_key)
        job_id = new_id("s")
        secret = JobSecret(
            key_material=key.model_dump(mode="json"),
            synthesis_seed=seed.model_dump(mode="json"),
            scan_auth=body.auth.storage_state,
            scan_start_url=validated.start_url,
        )
        sealed = seal(kek, secret.model_dump_json().encode("utf-8"), aad=f"dps_job.key:{job_id}")
        classify = value_classifier(engine.detector(policy, body.fill.locale))
        job = await jobs.create_job(
            job_id=job_id,
            tenant_id=tenant,
            kind=JobKind.scan,
            phase=JobPhase.scan,
            spec=redact_scan_spec(validated, redact_url(validated.start_url, classify)),
            mode=body.mode,
            sealed_secret=sealed,
            kid=key.kid,
            purpose=body.purpose.model_dump() if body.purpose else None,
            created_by=principal_of(request).key_id,
            idempotency_key=key_header,
            idempotency_fp=fp,
        )
    except Exception as exc:  # noqa: BLE001 - mapped to a value-free {"detail", "code"}
        raise rejected(request, "scan.create", exc) from None
    replay = job.id != job_id
    audit_action(request, action="scan.create", status=200 if replay else 202, job_id=job.id)
    if replay:  # a concurrent replay that waited behind the admission lock
        response.status_code = 200
        response.headers[REPLAY_HEADER] = "true"
        return ScanCreated(scan=job, replay=True).model_dump(mode="json")
    response.headers["Cache-Control"] = "no-store"  # the key and the seed are in this body
    return ScanCreated(scan=job, key_material=key_out, synthesis_seed=seed).model_dump(mode="json")


router.add_api_route(
    "",
    create_scan,
    methods=["POST"],
    status_code=202,
    dependencies=_SCAN,
    route_class_override=_CreateScanRoute,
)


@router.post(
    "/personas/preview",
    dependencies=[Depends(require_scope("scan")), Depends(enforce_rate_limit)],
)
async def personas_preview(body: PersonaPreviewRequest, request: Request) -> dict[str, Any]:
    """Synthetic personas for the New-scan page (stateless; the same generator the worker
    uses: the returned seed passed back as ``consistency.synthesis_seed`` makes persona 1 of the
    scan exactly persona 1 of this preview)."""
    engine = _engine(request)
    try:
        seed = (
            seed_from_caller(body.synthesis_seed)
            if body.synthesis_seed
            else generate_synthesis_seed()
        )
    except ValueError:
        raise DpsHttpError(422, "seed_invalid", "the synthesis seed is not valid") from None
    policy = engine.policy(None)
    personas = []
    for n in range(1, body.count + 1):
        values = persona_values(engine, seed=seed, persona_no=n, locale=body.locale, policy=policy)
        shown = {label: values[attr] for label, attr in preview_entities(body.locale)}
        personas.append({"persona": n, "values": shown})
    notes = NOTES.get(normalise_locale(body.locale), NOTES["en_US"])
    response = PersonaPreview(synthesis_seed=seed, personas=personas, notes=list(notes))
    return response.model_dump(mode="json")


@router.get("", dependencies=_SCAN)
async def list_scans(
    request: Request, state: JobState | None = None, limit: int = 50, cursor: str | None = None
) -> dict[str, Any]:
    """The tenant's scans, newest first."""

    async def body() -> dict[str, Any]:
        items, nxt = await jobs.list_jobs(
            tenant_of(request), kind=JobKind.scan, state=state, **page_args(limit, cursor)
        )
        return {"items": [j.model_dump(mode="json") for j in items], "next_cursor": nxt}

    return await guarded(body())


def _elapsed(job: Job) -> float | None:
    if job.started_at is None:
        return None
    end = job.finished_at or datetime.now(UTC)
    return round(max(0.0, (end - job.started_at).total_seconds()), 1)


@router.get("/{scan_id}", dependencies=_SCAN)
async def get_scan(scan_id: str, request: Request) -> dict[str, Any]:
    """The scan and its counters (the console polls it every 2 s while it runs)."""

    async def body() -> dict[str, Any]:
        job = await _scan(request, scan_id)
        store = _store(request)
        async with db.acquire(job.tenant_id) as conn:
            counts = await store.summary_counts(conn, job.id)
            pages = await store.all_pages(conn, job.id)
            exports = await store.export_artefacts(conn, job.id)
        fill = (job.spec or {}).get("fill") or {}
        by_status = dict(counts["by_status"])
        summary = ScanSummary(
            pages_by_status=by_status,
            pages_visited=counts["pages_visited"],
            pages_discovered=counts["pages_discovered"] - int(by_status.get("skipped", 0)),
            pages_queued=counts["pages_queued"],
            pages_classified=counts["classified"],
            pages_filled=counts["filled"],
            forms=counts["forms"],
            fields=counts["fields"],
            personal_fields=counts["personal"],
            critical_fields=counts["critical"],
            risk_flags=number_flags([(p.page_no, p.url_redacted, p.risk_flags) for p in pages]),
            blocked_requests=counts["blocked"],
            non_get_allowed=counts["non_get_allowed"],
            navigations=counts["navigations"],
            captures=counts["captures"],
            validation=validation_totals(pages),
            personas=int(fill.get("personas", 1)),
            display_persona=int(fill.get("display_persona", 1)),
            mode=job.mode or "",
            locale=str(fill.get("locale", "")),
            export_artefacts=exports,
            evidence_id=job.progress.evidence_id,
            elapsed_s=_elapsed(job),
        )
        return ScanDetail(scan=job, summary=summary).model_dump(mode="json")

    return await guarded(body())


def _limit(limit: int) -> int:
    return max(1, min(_MAX_PAGE, limit))


def _after(value: str | None) -> int:
    if value is None or value == "":
        return 0
    if not value.isdigit() or len(value) > 9:
        raise DpsHttpError(400, "cursor_invalid", "the cursor is not one this API issued")
    return int(value)


@router.get("/{scan_id}/pages", dependencies=_SCAN)
async def list_pages(
    scan_id: str, request: Request, after: str | None = None, limit: int = 50
) -> dict[str, Any]:
    """Pages in ``page_no`` order; ``next_cursor`` is the last ``page_no`` of a full page."""

    async def body() -> dict[str, Any]:
        job = await _scan(request, scan_id)
        n = _limit(limit)
        async with db.acquire(job.tenant_id) as conn:
            items = await _store(request).list_pages(
                conn, job.id, after_page_no=_after(after), limit=n
            )
        nxt = str(items[-1].page_no) if len(items) == n else None
        return {"items": [p.model_dump(mode="json") for p in items], "next_cursor": nxt}

    return await guarded(body())


@router.get("/{scan_id}/pages/{page_no}", dependencies=_SCAN)
async def get_page(scan_id: str, page_no: int, request: Request) -> dict[str, Any]:
    """One page with its fields (overlay order) and its request counts."""

    async def body() -> dict[str, Any]:
        job = await _scan(request, scan_id)
        store = _store(request)
        async with db.acquire(job.tenant_id) as conn:
            page = await store.get_page(conn, job.id, page_no)
            if page is None:
                raise DpsHttpError(404, "page_not_found", "page not found")
            fields = await store.list_fields(conn, job.id, page_no)
            requests = await store.list_requests(conn, job.id, page_no)
        return ScanPageDetail(page=page, fields=fields, requests=requests).model_dump(mode="json")

    return await guarded(body())


@router.get("/{scan_id}/events", dependencies=_SCAN)
async def list_events(
    scan_id: str, request: Request, after: str | None = None, limit: int = 100
) -> dict[str, Any]:
    """The navigation log (value-free) in ``seq`` order; ``next_cursor`` = the last ``seq``."""

    async def body() -> dict[str, Any]:
        job = await _scan(request, scan_id)
        n = _limit(limit)
        async with db.acquire(job.tenant_id) as conn:
            items = await _store(request).list_events(
                conn, job.id, after_seq=_after(after), limit=n
            )
        nxt = str(items[-1].seq) if len(items) == n else None
        return {"items": [e.model_dump(mode="json") for e in items], "next_cursor": nxt}

    return await guarded(body())


def _attachment(name: str) -> dict[str, str]:
    return {"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"}


@router.get("/{scan_id}/inventory", dependencies=_SCAN, response_model=None)
async def inventory(
    scan_id: str, request: Request, format: Literal["json", "csv"] = "json"
) -> dict[str, Any] | Response:
    """The field inventory, live from the tables (value-free): JSON or CSV."""

    async def body() -> dict[str, Any] | Response:
        job = await _scan(request, scan_id)
        store = _store(request)
        async with db.acquire(job.tenant_id) as conn:
            pages = await store.all_pages(conn, job.id)
            fields = await store.list_fields(conn, job.id)
        rows = inventory_rows(pages, fields)
        if format == "csv":
            return Response(
                content=inventory_csv(rows),
                media_type="text/csv; charset=utf-8",
                headers=_attachment(f"{job.id}-inventory.csv"),
            )
        flags = number_flags([(p.page_no, p.url_redacted, p.risk_flags) for p in pages])
        return {
            "items": [r.model_dump(mode="json") for r in rows],
            "risk_flags": [f.model_dump(mode="json") for f in flags],
        }

    return await guarded(body())


@router.get("/{scan_id}/export", dependencies=_SCAN, response_model=None)
async def export(
    scan_id: str, request: Request, format: Literal["json", "csv"] = "json"
) -> StreamingResponse:
    """The stored export (personas and the field→entity→value mapping, synthetic values and
    tokens of them only); 409 ``export_not_ready`` until the scan is finalized."""

    async def body() -> StreamingResponse:
        job = await _scan(request, scan_id)
        async with db.acquire(job.tenant_id) as conn:
            exports = await _store(request).export_artefacts(conn, job.id)
        artefact_id = exports.get(format)
        if artefact_id is None:
            raise DpsHttpError(409, "export_not_ready", "the scan has not been finalized yet")
        row, chunks = await request.app.state.artefacts.open(job.tenant_id, artefact_id)
        return StreamingResponse(
            chunks,
            media_type=row.content_type,
            headers=_attachment(f"{job.id}-export.{format}"),
        )

    return await guarded(body())


@router.post("/{scan_id}/cancel", dependencies=_SCAN)
async def cancel(scan_id: str, request: Request) -> dict[str, Any]:
    """Cancel: a queued scan at once; a running one stops at its next page and is finalized
    (evidence and exports, key purged). 409 ``not_cancelable`` once it is finishing or done."""
    idempotency_key(request)

    async def body() -> dict[str, Any]:
        job = await _scan(request, scan_id)
        try:
            canceled = await jobs.cancel(job.tenant_id, job.id)
        except JobStateError:
            raise DpsHttpError(
                409, "not_cancelable", "the scan is finishing or has finished"
            ) from None
        if canceled is None:
            raise DpsHttpError(404, "scan_not_found", "scan not found")
        return canceled.model_dump(mode="json")

    return await audited(request, "scan.cancel", body(), job_id=scan_id)
