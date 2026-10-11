"""Rasterisation with pypdfium2 (no poppler, no PyMuPDF).

PDFium is not thread-safe and the pipeline runs CPU work in worker threads, so every call into
pypdfium2 — open, render, object walk, close — holds ``PDFIUM_LOCK``. Renders are therefore
serialised per process; scale out with replicas.
"""

from __future__ import annotations

import base64
import io
import threading

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c
from PIL import Image

from dps.layers.documents.errors import DocumentError

PDFIUM_LOCK = threading.RLock()


class PdfRenderer:
    """One open PDFium document; every call is serialised behind ``PDFIUM_LOCK``.

    PDFium failures are ``corrupt_document``: a page that does not load (PDFium takes the page
    count from ``/Count``, so a forged count names pages that do not exist), will not render, or
    whose objects cannot be measured. The cause is never chained. Every call on a closed
    renderer is refused the same way rather than reaching pypdfium2 with a closed handle."""

    def __init__(self, data: bytes) -> None:
        with PDFIUM_LOCK:
            try:
                self._doc = pdfium.PdfDocument(data)
            except pdfium.PdfiumError:
                raise DocumentError("corrupt_document", "the PDF could not be rendered") from None
        self._closed = False

    def __enter__(self) -> PdfRenderer:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def _require_open(self) -> None:
        if self._closed:
            raise DocumentError("corrupt_document", "the renderer was already closed")

    def _page(self, index: int) -> pdfium.PdfPage:
        """Load page ``index``; the caller holds ``PDFIUM_LOCK`` and closes the page."""
        self._require_open()
        try:
            return self._doc[index]
        except pdfium.PdfiumError:
            raise DocumentError("corrupt_document", "a PDF page could not be loaded") from None

    def __len__(self) -> int:
        with PDFIUM_LOCK:
            self._require_open()
            return len(self._doc)

    def page_size(self, index: int) -> tuple[float, float]:
        """Page ``index`` width and height in points."""
        with PDFIUM_LOCK:
            page = self._page(index)
            try:
                width, height = page.get_size()
                return float(width), float(height)
            finally:
                page.close()

    def render(self, index: int, dpi: float) -> Image.Image:
        """Page ``index`` as an RGB image. Forms and annotations are not drawn: their content is
        never scanned, so it must never reach an output."""
        with PDFIUM_LOCK:
            page = self._page(index)
            try:
                bitmap = page.render(scale=dpi / 72.0, may_draw_forms=False, draw_annots=False)
                return bitmap.to_pil().convert("RGB")
            except pdfium.PdfiumError:
                raise DocumentError(
                    "corrupt_document", "a PDF page could not be rendered"
                ) from None
            finally:
                page.close()

    def image_coverage(self, index: int) -> float:
        """Share of page ``index`` covered by image objects (0..1, overlaps not deduplicated)."""
        with PDFIUM_LOCK:
            page = self._page(index)
            try:
                width, height = page.get_size()
                covered = 0.0
                for obj in page.get_objects(filter=[pdfium_c.FPDF_PAGEOBJ_IMAGE], max_depth=3):
                    left, bottom, right, top = obj.get_bounds()
                    covered += max(0.0, min(right, width) - max(left, 0.0)) * max(
                        0.0, min(top, height) - max(bottom, 0.0)
                    )
                return min(1.0, covered / max(width * height, 1.0))
            except pdfium.PdfiumError:
                raise DocumentError(
                    "corrupt_document", "the images on a PDF page could not be measured"
                ) from None
            finally:
                page.close()

    def close(self) -> None:
        """Release the document (idempotent)."""
        with PDFIUM_LOCK:
            if not self._closed:
                self._doc.close()
                self._closed = True


def encode_png(image: Image.Image) -> bytes:
    """PNG bytes of ``image``."""
    buf = io.BytesIO()
    image.save(buf, "PNG")
    return buf.getvalue()


def preview_png(image: Image.Image, *, source_dpi: float, target_dpi: float) -> bytes:
    """A PNG downscaled from ``source_dpi`` to ``target_dpi`` (never upscaled)."""
    factor = min(1.0, target_dpi / source_dpi)
    if factor < 1.0:
        size = (max(1, round(image.width * factor)), max(1, round(image.height * factor)))
        image = image.resize(size, Image.Resampling.LANCZOS)
    return encode_png(image)


def b64(data: bytes) -> str:
    """Base64 (standard alphabet) text of ``data``."""
    return base64.b64encode(data).decode("ascii")
