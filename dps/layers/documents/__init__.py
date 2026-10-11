"""Layer 1 documents (spec §6): the ``DocumentPipeline`` façade.

extract → OCR (pages that need it) → the unchanged M0 engine over one canonical text (pages
joined by ``PAGE_BREAK``; CSV as records) → findings and output segments mapped back to pages,
lines and boxes → optional true redaction, verified twice (structure, OCR re-read) → optional
previews. Nothing persists: the upload, rasters and texts live for the request only.
"""

from __future__ import annotations

import asyncio
import contextlib
import csv
import hashlib
import io
import re
import time
from collections.abc import Iterator
from typing import Any, Literal

from PIL import Image
from pydantic import BaseModel, ConfigDict

from dps.config import Settings
from dps.engine.detect.checksums import validate
from dps.engine.findings import Finding
from dps.engine.report import Report
from dps.engine.service import (
    DetectResponse,
    Payload,
    PrivacyEngine,
    RequestOptions,
    TransformResponse,
)
from dps.engine.transform.apply import OutputSegment
from dps.engine.verify import normalise
from dps.layers.documents.errors import DocumentError
from dps.layers.documents.extract import extract_document, sniff_format
from dps.layers.documents.layout import (
    build_page_text,
    group_lines,
    line_of_offset,
    merge_ocr_words,
    span_boxes,
    words_in_span,
)
from dps.layers.documents.models import (
    BBox,
    DocumentInfo,
    DocumentOptions,
    DocumentRequest,
    ExtractedDocument,
    PageBox,
    PageFinding,
    PageSummary,
    PageText,
    RedactionReport,
)
from dps.layers.documents.ocr import OcrClient, OcrError, OcrPage, ocr_words_to_points
from dps.layers.documents.redact import PageRedaction, inspect_output_pdf, redact_document
from dps.layers.documents.render import PdfRenderer, b64, encode_png, preview_png
from dps.observability import DOCUMENT_PAGES, REDACTED_BOXES, get_logger

__all__ = [
    "MIN_NEEDLE_CHARS",
    "REVIEW_OCR_CONFIDENCE",
    "RULE_VALID_SURROGATES",
    "DocumentOutcome",
    "DocumentPipeline",
    "locate_findings",
    "records_to_csv",
    "synthetic_validity",
]

logger = get_logger(__name__)
Mode = Literal["detect", "obfuscate", "synthesize"]
REVIEW_OCR_CONFIDENCE = 0.8
#: Surrogate types the synthesis engine generates rule-valid (checksums pass by design).
RULE_VALID_SURROGATES = frozenset({"CARD_PAN", "AADHAAR", "SIN", "IBAN", "ROUTING_ABA", "PAN_IN"})
MIN_NEEDLE_CHARS = 4
#: How far an OCR page size may differ from the image sent: 1 px (``pixel``), or 1 % of it
#: (``inch``, which the service derives from a resolution).
OCR_SIZE_TOLERANCE_PX = 1.0
OCR_SIZE_TOLERANCE_INCH = 0.01
_CSV_ROW = re.compile(r"^\$\[(\d+)\]")


class DocumentOutcome(BaseModel):
    """What the pipeline hands the router (the redacted PDF stays bytes until it is encoded)."""

    model_config = ConfigDict(arbitrary_types_allowed=True)
    mode: str
    detect: DetectResponse | None = None
    transform: TransformResponse | None = None
    document: DocumentInfo
    pages: list[PageSummary]
    redacted_pdf: bytes | None = None
    result_csv: str | None = None

    @property
    def report(self) -> Report:
        """The engine report (``run_engine`` reads it for the audit event)."""
        source = self.detect if self.detect is not None else self.transform
        assert source is not None
        return source.report


@contextlib.contextmanager
def _timed(timings: dict[str, float], name: str) -> Iterator[None]:
    started = time.perf_counter()
    try:
        yield
    finally:
        timings[name] = round(timings.get(name, 0.0) + (time.perf_counter() - started) * 1000, 1)


def _page_list(pages: list[int]) -> str:
    shown = ", ".join(str(p) for p in pages[:10])
    return shown + (", …" if len(pages) > 10 else "")


def _document_order(f: Finding) -> tuple[int, str, int]:
    """Sort key: CSV records by row number (``$[10]`` after ``$[9]``), then path, then offset."""
    match = _CSV_ROW.match(f.locator.path)
    return (
        int(match.group(1)) if match else -1,
        f.locator.path,
        -1 if f.locator.start is None else f.locator.start,
    )


def locate_findings(
    doc: ExtractedDocument,
    findings: list[Finding],
    *,
    plan_rows: list[dict[str, Any]],
    segments: list[OutputSegment],
    mode: str,
) -> list[PageSummary]:
    """Map engine findings (and their plan rows and output segments) onto pages, lines and boxes.

    Findings are numbered in document order; one spanning two pages appears on both with the
    same ``n``. ``redacted`` marks findings whose method transforms the value (not ``keep``).
    """
    rows = {(r["path"], tuple(r["span"]) if r.get("span") else None): r for r in plan_rows}
    segs = {(s.path, s.start, s.end): s for s in segments}
    summaries = [
        PageSummary(
            page=p.page,
            width=p.width,
            height=p.height,
            source=p.source,
            words=len(p.words),
            lines=(p.word_lines[-1] if p.word_lines else (p.text.count("\n") + 1 if p.text else 0)),
            ocr_confidence=p.ocr_confidence,
        )
        for p in doc.pages
    ]
    text = "" if doc.format == "csv" else doc.text()
    starts = doc.page_starts()
    ordered = sorted(findings, key=_document_order)
    for n, f in enumerate(ordered, start=1):
        loc = f.locator
        row = rows.get((loc.path, None if loc.whole else (loc.start, loc.end)))
        seg = segs.get((loc.path, loc.start, loc.end))
        method = str(row["method"]) if row else None
        common: dict[str, Any] = {
            "n": n,
            "entity_type": f.entity_type,
            "score": f.score,
            "sensitivity": f.sensitivity,
            "detectors": f.detectors,
            "evidence": f.evidence,
            "value_hash": f.value_hash,
            "path": loc.path,
            "method": method,
            "reversible": bool(row["reversible"]) if row else None,
            "redacted": mode != "detect" and method not in (None, "keep"),
            "replacement": seg.replacement if seg is not None and method != "keep" else None,
        }
        if doc.format == "csv":
            match = _CSV_ROW.match(loc.path)
            summaries[0].findings.append(
                PageFinding(
                    **common,
                    page=1,
                    line=int(match.group(1)) + 2 if match else None,
                    span=None if loc.whole else [loc.start or 0, loc.end or 0],
                )
            )
            continue
        start, end = (0, len(text)) if loc.whole else (loc.start or 0, loc.end or 0)
        out_span: list[int] | None = None
        if seg is not None and method != "keep":
            out_span = (
                [seg.out_start, seg.out_end]
                if seg.out_start is not None and seg.out_end is not None
                else [0, len(seg.replacement)]
            )
        for page, page_start in zip(doc.pages, starts, strict=True):
            page_end = page_start + len(page.text)
            if end <= page_start or start >= page_end:
                continue
            local_start, local_end = (
                max(start, page_start) - page_start,
                min(end, page_end) - page_start,
            )
            covered = [w for _, w in words_in_span(page, local_start, local_end)]
            confidences = [
                w.confidence for w in covered if w.source == "ocr" and w.confidence is not None
            ]
            confidence = round(min(confidences), 3) if confidences else None
            boxes = span_boxes(page, local_start, local_end) if page.words else []
            summaries[page.page - 1].findings.append(
                PageFinding(
                    **common,
                    page=page.page,
                    line=line_of_offset(page, local_start),
                    span=[start, end],
                    boxes=[
                        PageBox(page=page.page, x0=b.x0, y0=b.y0, x1=b.x1, y1=b.y1) for b in boxes
                    ],
                    out_span=out_span,
                    ocr_confidence=confidence,
                    review=confidence is not None and confidence < REVIEW_OCR_CONFIDENCE,
                )
            )
    return summaries


def records_to_csv(rows: list[dict[str, Any]], header: list[str], delimiter: str) -> str:
    """Re-serialise transformed CSV records with the source header and delimiter."""
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=delimiter, lineterminator="\n")
    writer.writerow(header)
    for row in rows:
        writer.writerow(["" if row.get(h) is None else str(row.get(h)) for h in header])
    return buf.getvalue()


def synthetic_validity(pages: list[PageSummary]) -> dict[str, int]:
    """How many surrogates of rule-valid types pass their checksum (should be all)."""
    seen: set[int] = set()
    checked = valid = 0
    for summary in pages:
        for f in summary.findings:
            if f.n in seen or f.replacement is None or f.entity_type not in RULE_VALID_SURROGATES:
                continue
            seen.add(f.n)
            checked += 1
            valid += validate(f.entity_type, f.replacement) is True
    return {"checked": checked, "valid": valid}


def _needles(summary: PageSummary, text: str) -> set[str]:
    """Normalised originals of a page's redacted findings, token by token (≥ 4 characters)."""
    out: set[str] = set()
    for f in summary.findings:
        if f.redacted and f.span:
            tokens = (normalise(t) for t in text[f.span[0] : f.span[1]].split())
            needle = " ".join(t for t in tokens if t)
            if len(needle.replace(" ", "")) >= MIN_NEEDLE_CHARS:
                out.add(needle)
    return out


def _png_size(png: bytes) -> tuple[int, int]:
    with Image.open(io.BytesIO(png)) as image:  # reads the header only
        return image.size


def _check_ocr_size(result: OcrPage, size: tuple[int, int], dpi: float) -> None:
    """Refuse an OCR page whose size is not the image's (review N8): words in another pixel
    space (a gateway that rescales) would put every box in the wrong place."""
    if result.unit == "inch":
        got = (result.width * dpi, result.height * dpi)
        tolerance = [max(OCR_SIZE_TOLERANCE_PX, OCR_SIZE_TOLERANCE_INCH * side) for side in size]
    else:
        got = (result.width, result.height)
        tolerance = [OCR_SIZE_TOLERANCE_PX, OCR_SIZE_TOLERANCE_PX]
    if any(abs(g - s) > t for g, s, t in zip(got, size, tolerance, strict=True)):
        raise OcrError("ocr_protocol", "the OCR page size differs from the image sent")


class DocumentPipeline:
    """Documents in, findings / transformed text / redacted PDF out (contract §1, spec §6)."""

    def __init__(self, engine: PrivacyEngine, settings: Settings, ocr: OcrClient) -> None:
        self.engine = engine
        self.settings = settings
        self.ocr = ocr

    async def run(
        self,
        *,
        mode: Mode,
        content: bytes,
        filename: str | None,
        content_type: str | None,
        request: DocumentRequest,
    ) -> DocumentOutcome:
        """Process one upload. Raises :class:`DocumentError` (coded) or engine errors."""
        timings: dict[str, float] = {}
        started = time.perf_counter()
        options = request.options
        if len(content) > self.settings.max_document_bytes:
            raise DocumentError(
                "document_too_large",
                f"documents are limited to {self.settings.max_document_mb} MB",
                status=413,
            )
        fmt = sniff_format(content, filename, content_type)
        self._check_options(fmt, mode, options)
        with _timed(timings, "extract"):
            doc = await asyncio.to_thread(
                extract_document, content, fmt, self.settings, options.ocr
            )
        with _timed(timings, "ocr"):
            await self._ocr_pages(doc, options)
        payload = self._payload(doc)
        engine_options = self._engine_options(options, doc)
        detect: DetectResponse | None = None
        transform: TransformResponse | None = None
        with _timed(timings, "engine"):
            if mode == "detect":
                detect = await asyncio.to_thread(
                    self.engine.detect, payload, request.policy, engine_options
                )
            elif mode == "obfuscate":
                transform = await asyncio.to_thread(
                    self.engine.obfuscate, payload, request.policy, engine_options
                )
            else:
                transform = await self.engine.synthesize(payload, request.policy, engine_options)
        findings = (
            detect.findings if detect is not None else transform.findings if transform else []
        )
        pages = locate_findings(
            doc,
            findings,
            plan_rows=transform.plan if transform else [],
            segments=(transform.segments or []) if transform else [],
            mode=mode,
        )
        redacted: bytes | None = None
        redaction: RedactionReport | None = None
        if options.redacted_pdf:
            with _timed(timings, "redact"):
                redacted, redaction = await self._redact(
                    doc, pages, payload.text or "", options, timings
                )
        if options.previews:
            with _timed(timings, "preview"):
                await asyncio.to_thread(self._attach_previews, doc, pages, redacted)
        result_csv = None
        if transform is not None and doc.format == "csv":
            result_csv = records_to_csv(transform.result, doc.csv_header or [], doc.csv_delimiter)
        timings["total"] = round((time.perf_counter() - started) * 1000, 1)
        info = DocumentInfo(
            format=doc.format,
            pages=len(doc.pages),
            ocr_pages=[p.page for p in doc.pages if p.ocr_done],
            unreadable_pages=[p.page for p in doc.pages if p.source == "unreadable"],
            words=sum(len(p.words) for p in doc.pages),
            boxes=sum(
                len(f.boxes) for s in pages for f in s.findings if mode == "detect" or f.redacted
            ),
            ocr_engine=self.ocr.engine if any(p.ocr_done for p in doc.pages) else None,
            bytes=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
            warnings=sorted(set(doc.warnings)),
            timings_ms=timings,
            redaction=redaction,
            synthetic_validity=synthetic_validity(pages) if mode == "synthesize" else None,
        )
        for page in doc.pages:
            DOCUMENT_PAGES.labels(doc.format, page.source).inc()
        logger.info(
            "document_processed",
            mode=mode,
            format=doc.format,
            pages=info.pages,
            ocr_pages=len(info.ocr_pages),
            boxes=info.boxes,
            duration_ms=timings["total"],
        )
        return DocumentOutcome(
            mode=mode,
            detect=detect,
            transform=transform,
            document=info,
            pages=pages,
            redacted_pdf=redacted,
            result_csv=result_csv,
        )

    # -- stages ----------------------------------------------------------------------------------
    @staticmethod
    def _check_options(fmt: str, mode: str, options: DocumentOptions) -> None:
        if options.redacted_pdf and mode == "detect":
            raise DocumentError(
                "redacted_pdf_not_allowed",
                "a redacted PDF needs /documents/obfuscate or /documents/synthesize",
            )
        if options.redacted_pdf and fmt not in ("pdf", "image"):
            raise DocumentError(
                "redaction_unsupported_format",
                "redacted PDFs are produced for PDF and image inputs",
            )
        if fmt == "image" and options.ocr == "never":
            raise DocumentError(
                "ocr_required", "images are read with OCR; ocr=never cannot process them"
            )

    def _payload(self, doc: ExtractedDocument) -> Payload:
        if doc.format == "csv":
            return Payload(data=doc.records or [])
        text = doc.text()
        if len(text) > self.settings.max_text_chars:
            raise DocumentError(
                "text_too_long",
                f"the document text exceeds {self.settings.max_text_chars} characters",
                status=413,
            )
        return Payload(text=text)

    @staticmethod
    def _engine_options(options: DocumentOptions, doc: ExtractedDocument) -> RequestOptions:
        if options.llm_rewrite:
            doc.warnings.append("llm_rewrite_not_applied_to_documents")
        base = options.model_dump(include=set(RequestOptions.model_fields))
        base.update(return_segments=True, llm_rewrite=False)
        return RequestOptions.model_validate(base)

    @staticmethod
    def _page_png(
        doc: ExtractedDocument, renderer: PdfRenderer | None, index: int, dpi: int
    ) -> tuple[bytes, float, tuple[int, int]]:
        """The page image OCR reads: PNG bytes, its resolution and its size in pixels."""
        if renderer is not None:
            image = renderer.render(index, dpi)
            return encode_png(image), float(dpi), image.size
        assert doc.image_png is not None and doc.image_dpi is not None
        return doc.image_png, doc.image_dpi, _png_size(doc.image_png)

    async def _ocr_pages(self, doc: ExtractedDocument, options: DocumentOptions) -> None:
        if options.redacted_pdf and options.ocr == "auto" and self.ocr.configured():
            # A redacted PDF rasterises every image on a page verbatim, and only OCR can read
            # what an image prints; below ``ocr_image_coverage`` a text page is not scan-suspect,
            # so a small image (an ID photo, a stamp) would be burned in unread (review §5).
            for p in doc.pages:
                if p.image_coverage > 0 and not p.needs_ocr:
                    p.needs_ocr = True
        targets = [p for p in doc.pages if p.needs_ocr]
        if not targets:
            return
        if not self.ocr.configured():
            if doc.format == "image" or options.ocr == "always" or options.redacted_pdf:
                raise DocumentError(
                    "ocr_unconfigured",
                    f"{len(targets)} page(s) need OCR and no OCR provider is configured",
                    status=503,
                )
            doc.warnings.append("ocr_unconfigured")
            return
        dpi = self.settings.pdf_render_dpi
        renderer = PdfRenderer(doc.pdf) if doc.format == "pdf" and doc.pdf is not None else None
        gate = asyncio.Semaphore(self.settings.ocr_max_concurrency)

        async def one(page: PageText) -> tuple[PageText, OcrPage, float]:
            async with gate:
                png, px_dpi, size = await asyncio.to_thread(
                    self._page_png, doc, renderer, page.page - 1, dpi
                )
                result = await self.ocr.read(png)
                _check_ocr_size(result, size, px_dpi)
                return page, result, px_dpi

        try:
            # return_exceptions: every page finishes before the renderer closes
            results = await asyncio.gather(*(one(p) for p in targets), return_exceptions=True)
        finally:
            if renderer is not None:
                renderer.close()
        failures = [r for r in results if isinstance(r, BaseException)]
        if failures:
            first = failures[0]
            if isinstance(first, OcrError):
                raise DocumentError(
                    "ocr_unavailable",
                    f"OCR failed for {len(failures)} of {len(targets)} page(s) ({first.code})",
                    status=503,
                ) from None
            raise first
        for item in results:
            assert not isinstance(item, BaseException)
            page, result, px_dpi = item
            words = ocr_words_to_points(result, points_per_px=72.0 / px_dpi)
            if page.words:
                build_page_text(page, group_lines(merge_ocr_words(page.words, words)))
                page.source = "hybrid"
            else:
                build_page_text(page, group_lines(words))
                page.source = "ocr"
            confidences = [
                w.confidence for w in page.words if w.source == "ocr" and w.confidence is not None
            ]
            page.ocr_confidence = (
                round(sum(confidences) / len(confidences), 3) if confidences else None
            )
            page.ocr_done = True

    async def _redact(
        self,
        doc: ExtractedDocument,
        pages: list[PageSummary],
        text: str,
        options: DocumentOptions,
        timings: dict[str, float],
    ) -> tuple[bytes, RedactionReport]:
        suspects = [p.page for p in doc.pages if p.scan_suspect and not p.ocr_done]
        if suspects:
            raise DocumentError(
                "redaction_needs_ocr",
                f"page(s) {_page_list(suspects)} look scanned and were not read with OCR; "
                "a redacted PDF would expose them",
            )
        if any(p.image_coverage > 0 and not p.ocr_done for p in doc.pages):
            # OCR unconfigured or ocr=never: the images are burned in unread (see _ocr_pages)
            doc.warnings.append("images_not_ocr_read")
        plan = [
            PageRedaction(
                index=p.page - 1,
                width=p.width,
                height=p.height,
                boxes=[
                    BBox(x0=b.x0, y0=b.y0, x1=b.x1, y1=b.y1)
                    for f in s.findings
                    if f.redacted
                    for b in f.boxes
                ],
            )
            for p, s in zip(doc.pages, pages, strict=True)
        ]
        unlocated = len({f.n for s in pages for f in s.findings if f.redacted and not f.boxes})
        if unlocated:
            raise DocumentError(
                "redaction_unlocated", f"{unlocated} finding(s) have no position on the page"
            )
        s = self.settings
        output = await asyncio.to_thread(
            redact_document,
            doc,
            plan,
            dpi=s.pdf_render_dpi,
            image_format=s.redact_image_format,
            jpeg_quality=s.redact_jpeg_quality,
        )
        checked, ocr_pages, hits, under = False, 0, 0, 0
        with _timed(timings, "verify"):
            inspection = await asyncio.to_thread(inspect_output_pdf, output.pdf)
            if not inspection.clean or inspection.pages != len(doc.pages):
                raise DocumentError(
                    "redaction_verify_failed",
                    "the produced PDF failed its structural check",
                    status=500,
                )
            verify = options.ocr_verify if options.ocr_verify is not None else s.redact_ocr_verify
            if verify and self.ocr.configured():
                checked = True
                ocr_pages, hits, under = await self._ocr_reread(output.pdf, plan, pages, text)
                if hits:
                    raise DocumentError(
                        "leakage",
                        f"an OCR re-read of the redacted PDF found {hits} original "
                        "value(s); nothing was returned",
                    )
            elif verify:
                doc.warnings.append("redaction_not_ocr_verified")
        REDACTED_BOXES.inc(output.boxes)
        return output.pdf, RedactionReport(
            boxes=output.boxes,
            pages=len(plan),
            dpi=s.pdf_render_dpi,
            image_format=s.redact_image_format,
            bytes=len(output.pdf),
            sha256=hashlib.sha256(output.pdf).hexdigest(),
            text_layer_chars=inspection.text_layer_chars,
            text_operators=inspection.text_operators,
            annotations=inspection.annotations,
            ocr_checked=checked,
            ocr_pages=ocr_pages,
            ocr_hits=hits,
            chars_under_boxes=under,
            passed=True,
        )

    async def _ocr_reread(
        self, pdf: bytes, plan: list[PageRedaction], pages: list[PageSummary], text: str
    ) -> tuple[int, int, int]:
        """OCR every boxed page of the produced PDF: originals still readable (hits) and characters
        read inside painted boxes."""
        dpi = self.settings.pdf_render_dpi
        targets = [r for r in plan if r.boxes]
        renderer = PdfRenderer(pdf)
        gate = asyncio.Semaphore(self.settings.ocr_max_concurrency)

        def render(index: int) -> tuple[bytes, tuple[int, int]]:
            image = renderer.render(index, dpi)
            return encode_png(image), image.size

        async def one(r: PageRedaction) -> tuple[PageRedaction, OcrPage]:
            async with gate:
                png, size = await asyncio.to_thread(render, r.index)
                result = await self.ocr.read(png)
                _check_ocr_size(result, size, dpi)
                return r, result

        try:
            results = await asyncio.gather(*(one(r) for r in targets), return_exceptions=True)
        finally:
            renderer.close()
        hits = under = 0
        for item in results:
            if isinstance(item, OcrError):
                raise DocumentError(
                    "ocr_unavailable",
                    f"the OCR re-read of the redacted PDF failed ({item.code})",
                    status=503,
                )
            if isinstance(item, BaseException):
                raise item
            r, result = item
            words = ocr_words_to_points(result, points_per_px=72.0 / dpi)
            tokens = (normalise(w.text) for w in words)
            haystack = " " + " ".join(t for t in tokens if t) + " "
            hits += sum(1 for needle in _needles(pages[r.index], text) if f" {needle} " in haystack)
            under += sum(
                len(w.text)
                for w in words
                if any(
                    w.bbox.intersection_area(b) >= 0.5 * max(w.bbox.area(), 1e-6) for b in r.boxes
                )
            )
        return len(targets), hits, under

    def _attach_previews(
        self, doc: ExtractedDocument, pages: list[PageSummary], redacted: bytes | None
    ) -> None:
        limit, dpi = self.settings.max_preview_pages, self.settings.preview_dpi
        if doc.format == "pdf" and doc.pdf is not None:
            with PdfRenderer(doc.pdf) as renderer:
                for summary in pages[:limit]:
                    summary.preview_png_b64 = b64(
                        encode_png(renderer.render(summary.page - 1, dpi))
                    )
        elif doc.format == "image" and doc.image_png is not None and doc.image_dpi:
            with Image.open(io.BytesIO(doc.image_png)) as opened:
                pages[0].preview_png_b64 = b64(
                    preview_png(opened.convert("RGB"), source_dpi=doc.image_dpi, target_dpi=dpi)
                )
        if redacted is not None:
            with PdfRenderer(redacted) as renderer:
                for summary in pages[:limit]:
                    summary.redacted_preview_png_b64 = b64(
                        encode_png(renderer.render(summary.page - 1, dpi))
                    )
