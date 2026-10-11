"""Upload → ``ExtractedDocument``: format sniffing and PDF / DOCX / TXT / CSV / image extraction.

PDFs are first normalised (crop box at the origin, page rotation moved into the content) so that
pdfplumber word boxes and pypdfium2 rasters share one coordinate system: points, top-left origin.
pypdf handles structure (decryption check, normalisation); pdfplumber reads positioned words —
pypdf's own text extraction has no reliable word positions.
"""

from __future__ import annotations

import io
from typing import Any

import pdfplumber
from pypdf import PdfReader, PdfWriter, Transformation
from pypdf.errors import PyPdfError
from pypdf.generic import RectangleObject

from dps.config import Settings
from dps.layers.documents.errors import DocumentError
from dps.layers.documents.layout import build_page_text, group_lines
from dps.layers.documents.models import BBox, ExtractedDocument, OcrMode, PageText, Word
from dps.layers.documents.render import PdfRenderer

_PDF_ERRORS = (PyPdfError, ValueError, KeyError, TypeError, OSError, AssertionError)


def normalise_pdf(content: bytes, *, max_pages: int) -> tuple[bytes, int]:
    """Rewrite a PDF so every page's crop box starts at the origin and its rotation lives in the
    content. Afterwards pdfplumber coordinates (points, top-left) and pypdfium2 rasters agree
    exactly, which the redaction boxes rely on. Returns the bytes and the annotation count."""
    try:
        reader = PdfReader(io.BytesIO(content))
        if reader.is_encrypted and not reader.decrypt(""):
            raise DocumentError("encrypted_pdf", "password-protected PDFs are not supported")
        writer = PdfWriter(clone_from=reader)
        count = len(writer.pages)
    except DocumentError:
        raise
    except _PDF_ERRORS as exc:
        raise DocumentError("corrupt_document", "the PDF could not be parsed") from exc
    if count == 0:
        raise DocumentError("corrupt_document", "the PDF has no pages")
    if count > max_pages:
        raise DocumentError(
            "too_many_pages", f"the PDF has {count} pages; the limit is {max_pages}", status=413
        )
    annotations = 0
    try:
        for page in writer.pages:
            annots = page.get("/Annots")
            annotations += len(annots.get_object()) if annots is not None else 0
            if page.rotation % 360:
                page.transfer_rotation_to_content()
            box = page.cropbox
            llx, lly, urx, ury = (
                float(box.left),
                float(box.bottom),
                float(box.right),
                float(box.top),
            )
            if urx - llx <= 0 or ury - lly <= 0:
                raise DocumentError("corrupt_document", "a page has an empty crop box")
            if llx or lly:
                page.add_transformation(Transformation().translate(-llx, -lly))
            rect = RectangleObject((0, 0, urx - llx, ury - lly))
            page.mediabox = rect
            page.cropbox = rect
            page.trimbox = rect
            page.bleedbox = rect
            page.artbox = rect
        out = io.BytesIO()
        writer.write(out)
    except DocumentError:
        raise
    except _PDF_ERRORS as exc:
        raise DocumentError("corrupt_document", "the PDF could not be normalised") from exc
    return out.getvalue(), annotations


def _text_layer_words(plumber_page: Any) -> list[Word]:
    words: list[Word] = []
    deduped = plumber_page.dedupe_chars()
    for raw in deduped.extract_words(
        keep_blank_chars=False, use_text_flow=False, return_chars=True
    ):
        chars = raw.get("chars") or []
        text = "".join(c["text"] for c in chars) or raw["text"]
        if not text.strip():
            continue
        boxes: list[BBox] = []
        for c in chars:
            box = BBox(
                x0=float(c["x0"]), y0=float(c["top"]), x1=float(c["x1"]), y1=float(c["bottom"])
            )
            boxes.extend([box] * len(c["text"]))
        words.append(
            Word(
                text=text,
                bbox=BBox(
                    x0=float(raw["x0"]),
                    y0=float(raw["top"]),
                    x1=float(raw["x1"]),
                    y1=float(raw["bottom"]),
                ),
                source="text_layer",
                char_boxes=boxes if len(boxes) == len(text) else None,
            )
        )
    return words


def extract_pdf(content: bytes, settings: Settings, ocr_mode: OcrMode) -> ExtractedDocument:
    """Normalise, read the text layer, and flag pages that need OCR."""
    normalised, annotations = normalise_pdf(content, max_pages=settings.max_document_pages)
    with PdfRenderer(normalised) as renderer:
        coverage = [renderer.image_coverage(i) for i in range(len(renderer))]
    pages: list[PageText] = []
    try:
        with pdfplumber.open(io.BytesIO(normalised)) as pdf:
            for i, plumber_page in enumerate(pdf.pages):
                words = [] if ocr_mode == "always" else _text_layer_words(plumber_page)
                chars = sum(len(w.text) for w in words)
                page = PageText(
                    page=i + 1,
                    width=float(plumber_page.width),
                    height=float(plumber_page.height),
                    source="text_layer",
                    image_coverage=round(coverage[i], 4),
                )
                page.scan_suspect = (
                    chars < settings.ocr_min_text_chars
                    or coverage[i] >= settings.ocr_image_coverage
                )
                page.needs_ocr = ocr_mode == "always" or (ocr_mode == "auto" and page.scan_suspect)
                if words:
                    build_page_text(page, group_lines(words))
                else:
                    page.source = "unreadable" if page.scan_suspect else "empty"
                pages.append(page)
    except DocumentError:
        raise
    except Exception as exc:  # noqa: BLE001 - pdfminer raises many types; report the name only
        raise DocumentError(
            "corrupt_document", f"the PDF text layer could not be read ({type(exc).__name__})"
        ) from exc
    doc = ExtractedDocument(format="pdf", pages=pages, pdf=normalised)
    if annotations:
        doc.warnings.append("annotations_not_scanned_or_drawn")
    return doc
