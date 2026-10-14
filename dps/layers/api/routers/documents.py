"""POST /api/v1/documents/{detect,obfuscate,synthesize} — multipart ``file`` + JSON form field
``request`` (contract §9, M1).

Every outcome is audited (mode ``document.<mode>``), refusals included: an invalid ``request``
field or an oversized upload is raised inside ``run_engine``, so it is counted and audited like an
engine error (policy ``-`` when the request could not be parsed). ``Idempotency-Key`` is honoured
(contract §9); the fingerprint is the file's digest plus the ``request`` field.
"""

from __future__ import annotations

import base64
import hashlib
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from pydantic import ValidationError

from dps.auth import require_scope
from dps.layers.api.common import (
    CodedHTTPException,
    run_engine,
    with_idempotency,
)
from dps.layers.api.policy_refs import policy_label, resolve
from dps.layers.api.schemas import DetectOut, TransformOut
from dps.layers.documents import DocumentOutcome, DocumentPipeline
from dps.layers.documents.models import (
    DocumentInfo,
    DocumentRequest,
    PageSummary,
    validation_message,
)
from dps.ratelimit import enforce_rate_limit

router = APIRouter(prefix="/api/v1/documents", tags=["documents"])
READ_CHUNK = 1 << 20


class DocumentDetectOut(DetectOut):
    """M0 ``DetectOut`` plus the document block and the per-page findings."""

    document: DocumentInfo
    pages: list[PageSummary]


class DocumentTransformOut(TransformOut):
    """M0 ``TransformOut`` plus the document block, pages, redacted PDF and CSV output."""

    document: DocumentInfo
    pages: list[PageSummary]
    redacted_pdf_b64: str | None = None
    result_csv: str | None = None


def _parse(raw: str | None) -> DocumentRequest:
    if not raw:
        return DocumentRequest()
    try:
        return DocumentRequest.model_validate_json(raw)
    except ValidationError as exc:
        raise CodedHTTPException(422, "invalid_request", validation_message(exc)) from None


async def _read(file: UploadFile, limit: int) -> bytes:
    """Read the upload in chunks; a chunked upload without Content-Length is bounded here."""
    data = bytearray()
    while chunk := await file.read(READ_CHUNK):
        data += chunk
        if len(data) > limit:
            raise CodedHTTPException(
                413, "document_too_large", f"documents are limited to {limit} bytes"
            )
    return bytes(data)


async def _run(
    request: Request,
    mode: Literal["detect", "obfuscate", "synthesize"],
    file: UploadFile,
    raw: str | None,
) -> tuple[DocumentOutcome, str]:
    settings = request.app.state.settings
    pipeline: DocumentPipeline = request.app.state.documents
    refusal: CodedHTTPException | None = None
    content = b""
    try:
        body = _parse(raw)
        # Read before the idempotency check (its fingerprint covers the file); a refusal is
        # still raised inside run_engine, so it is audited like any other.
        content = await _read(file, settings.max_document_bytes)
    except CodedHTTPException as exc:
        body, refusal = DocumentRequest(), exc
    if refusal is None:
        try:  # M5: an @name[:vN] reference resolves to the stored version (refusals audited)
            body.policy = await resolve(request, body.policy)
        except CodedHTTPException as exc:
            refusal = exc
    policy = "-" if refusal else policy_label(body.policy, settings.default_policy)

    async def call() -> DocumentOutcome:
        if refusal is not None:
            raise refusal
        return await pipeline.run(
            mode=mode,
            content=content,
            filename=file.filename,
            content_type=file.content_type,
            request=body,
        )

    async def run() -> tuple[DocumentOutcome, str]:
        return await run_engine(request, mode=f"document.{mode}", policy_name=policy, call=call)

    # The multipart envelope (boundaries) differs between clients' retries, so the fingerprint
    # is the file's digest plus the ``request`` field, not the raw body.
    fingerprint = hashlib.sha256(content).digest() + (raw or "").encode("utf-8")
    return await with_idempotency(request, fingerprint, run, mode=f"document.{mode}", policy=policy)


def _transform_out(outcome: DocumentOutcome, trace_id: str) -> DocumentTransformOut:
    assert outcome.transform is not None
    pdf = outcome.redacted_pdf
    return DocumentTransformOut(
        **outcome.transform.model_dump(),
        trace_id=trace_id,
        document=outcome.document,
        pages=outcome.pages,
        result_csv=outcome.result_csv,
        redacted_pdf_b64=base64.b64encode(pdf).decode("ascii") if pdf else None,
    )


@router.post(
    "/detect",
    response_model=DocumentDetectOut,
    dependencies=[Depends(require_scope("detect")), Depends(enforce_rate_limit)],
)
async def documents_detect(
    request: Request,
    file: Annotated[UploadFile, File()],
    request_json: Annotated[str | None, Form(alias="request")] = None,
) -> DocumentDetectOut:
    """Findings per page, with boxes on PDF and image pages (never values)."""
    outcome, trace_id = await _run(request, "detect", file, request_json)
    assert outcome.detect is not None
    return DocumentDetectOut(
        **outcome.detect.model_dump(),
        trace_id=trace_id,
        document=outcome.document,
        pages=outcome.pages,
    )


@router.post(
    "/obfuscate",
    response_model=DocumentTransformOut,
    dependencies=[Depends(require_scope("obfuscate")), Depends(enforce_rate_limit)],
)
async def documents_obfuscate(
    request: Request,
    file: Annotated[UploadFile, File()],
    request_json: Annotated[str | None, Form(alias="request")] = None,
) -> DocumentTransformOut:
    """Obfuscated text (revealable with the returned key) and, optionally, a redacted PDF."""
    outcome, trace_id = await _run(request, "obfuscate", file, request_json)
    return _transform_out(outcome, trace_id)


@router.post(
    "/synthesize",
    response_model=DocumentTransformOut,
    dependencies=[Depends(require_scope("synthesize")), Depends(enforce_rate_limit)],
)
async def documents_synthesize(
    request: Request,
    file: Annotated[UploadFile, File()],
    request_json: Annotated[str | None, Form(alias="request")] = None,
) -> DocumentTransformOut:
    """Synthetic surrogates in the document text and, optionally, a redacted PDF."""
    outcome, trace_id = await _run(request, "synthesize", file, request_json)
    return _transform_out(outcome, trace_id)
