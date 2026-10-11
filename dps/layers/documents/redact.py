"""True redaction: rasterise, burn boxes in, rebuild an image-only PDF (no PyMuPDF).

Each normalised page is rendered with pypdfium2 at ``pdf_render_dpi`` (forms and annotations not
drawn), every box is painted solid black on the raster, and reportlab writes a new PDF whose pages
are just those images (JPEG passed through as DCTDecode, or PNG/Flate). Nothing from the source
survives: no text layer, fonts, annotations, forms, outlines, attachments or metadata. The
original values under the boxes are gone from the pixels, so no key brings them back.
"""

from __future__ import annotations

import io
import math
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import Any

from PIL import Image, ImageDraw
from pypdf import PdfReader
from pypdf.generic import DictionaryObject, StreamObject
from reportlab import rl_config
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from dps.layers.documents.errors import DocumentError
from dps.layers.documents.models import BBox, ExtractedDocument
from dps.layers.documents.render import PdfRenderer

rl_config.useA85 = (
    0  # write image streams in binary (ASCII85 would add 25%); process-wide, set once
)
REDACT_FILL = (0, 0, 0)
#: A text-*showing* operator after a string or array operand. reportlab writes an empty
#: ``BT /F1 12 Tf 14.4 TL ET`` on every page, so ``BT`` alone proves nothing.
_SHOW_TEXT = re.compile(rb"[)\]>]\s*(?:Tj|TJ|'|\")")
#: How deep the inspection follows Form XObjects inside Form XObjects (DPS writes none).
_MAX_FORM_DEPTH = 8


@dataclass(frozen=True)
class PageRedaction:
    """One output page: its 0-based index in the source, its size in points, its boxes."""

    index: int  # 0-based page index
    width: float  # points
    height: float
    boxes: list[BBox]


@dataclass(frozen=True)
class RedactedOutput:
    """The image-only PDF and how many boxes were burned into it."""

    pdf: bytes
    boxes: int


@dataclass(frozen=True)
class OutputInspection:
    """What a produced PDF holds besides images (every count must be 0 for ``clean``)."""

    pages: int
    text_layer_chars: int
    text_operators: int
    annotations: int
    has_forms: bool
    has_outlines: bool
    has_attachments: bool

    @property
    def clean(self) -> bool:
        """True when nothing but images can be in the file."""
        return (
            self.text_layer_chars == 0
            and self.text_operators == 0
            and self.annotations == 0
            and not (self.has_forms or self.has_outlines or self.has_attachments)
        )


def paint_boxes(
    image: Image.Image, boxes: Iterable[BBox], *, width_pt: float, height_pt: float
) -> None:
    """Fill every box (points) on ``image`` (pixels), rounding outward. A box without area (a
    finding outside the page) covers nothing that is drawn, so it is skipped."""
    sx, sy = image.width / width_pt, image.height / height_pt
    draw = ImageDraw.Draw(image)
    for b in boxes:
        if b.area() <= 0:
            continue
        draw.rectangle(
            (
                math.floor(b.x0 * sx),
                math.floor(b.y0 * sy),
                math.ceil(b.x1 * sx),
                math.ceil(b.y1 * sy),
            ),
            fill=REDACT_FILL,
        )


def build_image_pdf(
    pages: Iterable[tuple[Image.Image, float, float]], *, image_format: str, jpeg_quality: int
) -> bytes:
    """An image-only PDF: one image per page at the given size in points; metadata emptied."""
    buf = io.BytesIO()
    pdf = canvas.Canvas(buf, invariant=1, pageCompression=1)
    pdf.setTitle("")
    pdf.setAuthor("")
    pdf.setSubject("")
    pdf.setKeywords("")
    pdf.setCreator("Data Privacy Services")
    for image, width, height in pages:
        encoded = io.BytesIO()
        if image_format == "jpeg":
            image.save(encoded, "JPEG", quality=jpeg_quality, optimize=True)
        else:
            image.save(encoded, "PNG")
        encoded.seek(0)
        pdf.setPageSize((width, height))
        pdf.drawImage(ImageReader(encoded), 0, 0, width=width, height=height)
        pdf.showPage()
    pdf.save()
    return buf.getvalue()


def redact_document(
    doc: ExtractedDocument,
    plan: list[PageRedaction],
    *,
    dpi: int,
    image_format: str,
    jpeg_quality: int,
) -> RedactedOutput:
    """Rasterise every page in ``plan`` (all pages, boxed or not), burn its boxes in, rebuild."""
    if doc.format == "pdf" and doc.pdf is not None:
        renderer = PdfRenderer(doc.pdf)

        def pdf_pages() -> Iterator[tuple[Image.Image, float, float]]:
            for page in plan:
                image = renderer.render(page.index, dpi)
                paint_boxes(image, page.boxes, width_pt=page.width, height_pt=page.height)
                yield image, page.width, page.height

        try:
            data = build_image_pdf(
                pdf_pages(), image_format=image_format, jpeg_quality=jpeg_quality
            )
        finally:
            renderer.close()
    elif doc.format == "image" and doc.image_png is not None:
        with Image.open(io.BytesIO(doc.image_png)) as opened:
            image = opened.convert("RGB")
        page = plan[0]
        paint_boxes(image, page.boxes, width_pt=page.width, height_pt=page.height)
        data = build_image_pdf(
            [(image, page.width, page.height)], image_format=image_format, jpeg_quality=jpeg_quality
        )
    else:
        raise DocumentError(
            "redaction_unsupported_format", "redacted PDFs are produced for PDF and image inputs"
        )
    return RedactedOutput(pdf=data, boxes=sum(1 for p in plan for b in p.boxes if b.area() > 0))


def _form_text_operators(resources: Any, seen: set[int], depth: int = 0) -> int:
    """Text-showing operators inside the Form XObjects a resource dictionary names (recursive).

    DPS's own output has image XObjects only; a Form XObject could draw text that the page's
    content stream merely calls with ``Do``, so the inspection reads those streams too."""
    resources = resources.get_object() if resources is not None else None
    if not isinstance(resources, DictionaryObject) or depth > _MAX_FORM_DEPTH:
        return 0
    xobjects = resources.get("/XObject")
    xobjects = xobjects.get_object() if xobjects is not None else None
    if not isinstance(xobjects, DictionaryObject):
        return 0
    count = 0
    for ref in xobjects.values():
        key = getattr(ref, "idnum", None)
        if key is not None:
            if key in seen:
                continue
            seen.add(key)
        obj = ref.get_object()
        if not isinstance(obj, StreamObject) or obj.get("/Subtype") != "/Form":
            continue
        count += len(_SHOW_TEXT.findall(obj.get_data()))
        count += _form_text_operators(obj.get("/Resources"), seen, depth + 1)
    return count


def inspect_output_pdf(data: bytes) -> OutputInspection:
    """Prove a produced PDF carries no text: pypdf extraction, text-showing operators (in page
    content and in any Form XObject it calls), annotations and document structure."""
    reader = PdfReader(io.BytesIO(data))
    chars = operators = annotations = 0
    seen: set[int] = set()
    for page in reader.pages:
        chars += len((page.extract_text() or "").strip())
        contents = page.get_contents()
        if contents is not None:
            operators += len(_SHOW_TEXT.findall(contents.get_data()))
        operators += _form_text_operators(page.get("/Resources"), seen)
        annots = page.get("/Annots")
        annotations += len(annots.get_object()) if annots is not None else 0
    root = reader.root_object
    return OutputInspection(
        pages=len(reader.pages),
        text_layer_chars=chars,
        text_operators=operators,
        annotations=annotations,
        has_forms="/AcroForm" in root,
        has_outlines="/Outlines" in root,
        has_attachments="/Names" in root,
    )
