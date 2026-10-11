"""Upload → ``ExtractedDocument``: format sniffing and PDF / DOCX / TXT / CSV / image extraction.

PDFs are first normalised (crop box at the origin, page rotation moved into the content) so that
pdfplumber word boxes and pypdfium2 rasters share one coordinate system: points, top-left origin.
pypdf handles structure (decryption check, normalisation); pdfplumber reads positioned words —
pypdf's own text extraction has no reliable word positions.
"""

from __future__ import annotations

import codecs
import csv
import io
import math
import re
import zipfile
from typing import Any

import pdfplumber
from PIL import Image, ImageOps, UnidentifiedImageError
from pypdf import PdfReader, PdfWriter, Transformation
from pypdf.generic import RectangleObject

from dps.config import Settings
from dps.layers.documents.errors import DocumentError
from dps.layers.documents.layout import build_page_text, group_lines
from dps.layers.documents.models import (
    BBox,
    DocFormat,
    ExtractedDocument,
    OcrMode,
    PageText,
    Word,
)
from dps.layers.documents.render import PdfRenderer

MAX_IMAGE_SIDE = 10_000  # Read v3.2 accepts at most 10000 x 10000 (images scale, PDF pages refuse)


def normalise_pdf(content: bytes, *, max_pages: int) -> tuple[bytes, int]:
    """Rewrite a PDF so every page's crop box starts at the origin and its rotation lives in the
    content. Afterwards pdfplumber coordinates (points, top-left) and pypdfium2 rasters agree
    exactly, which the redaction boxes rely on. Returns the bytes and the annotation count.

    Any failure inside pypdf is ``corrupt_document`` naming only the exception type: on malformed
    input pypdf raises far more than its own ``PyPdfError`` (``AttributeError``,
    ``NotImplementedError`` for an unknown filter, ``IndexError``, ``RecursionError``, …), and its
    messages can quote the file's bytes, so the cause is never chained."""
    try:
        reader = PdfReader(io.BytesIO(content))
        if reader.is_encrypted and not reader.decrypt(""):
            raise DocumentError("encrypted_pdf", "password-protected PDFs are not supported")
        writer = PdfWriter(clone_from=reader)
        count = len(writer.pages)
    except DocumentError:
        raise
    except Exception as exc:  # noqa: BLE001 - pypdf raises many types on garbage; name only
        raise DocumentError(
            "corrupt_document", f"the PDF could not be parsed ({type(exc).__name__})"
        ) from None
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
    except Exception as exc:  # noqa: BLE001 - as above
        raise DocumentError(
            "corrupt_document", f"the PDF could not be normalised ({type(exc).__name__})"
        ) from None
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


def _check_page_sizes(renderer: PdfRenderer, settings: Settings) -> None:
    """Refuse a page that any DPS render (OCR and redaction at ``pdf_render_dpi``, previews at
    ``preview_dpi``) would make more than ``MAX_IMAGE_SIDE`` pixels a side: Read v3.2 cannot read
    it, and the bitmap is unbounded (a 433-byte, 14,400 pt page is 40,000 px at 200 dpi, ~17 GB).
    Pixels are counted as pypdfium2 sizes its bitmap: ``ceil(points * dpi / 72)``."""
    dpi = max(settings.pdf_render_dpi, settings.preview_dpi)
    scale = dpi / 72.0
    for index in range(len(renderer)):
        if max(math.ceil(side * scale) for side in renderer.page_size(index)) > MAX_IMAGE_SIDE:
            raise DocumentError(
                "image_too_large",
                f"page {index + 1} would render over {MAX_IMAGE_SIDE} px a side at {dpi} dpi; "
                f"pages are limited to {math.floor(MAX_IMAGE_SIDE / scale)} pt a side",
                status=413,
            )


def extract_pdf(content: bytes, settings: Settings, ocr_mode: OcrMode) -> ExtractedDocument:
    """Normalise, bound the page size, read the text layer, and flag pages that need OCR."""
    normalised, annotations = normalise_pdf(content, max_pages=settings.max_document_pages)
    with PdfRenderer(normalised) as renderer:
        _check_page_sizes(renderer, settings)
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
        ) from None
    doc = ExtractedDocument(format="pdf", pages=pages, pdf=normalised)
    if annotations:
        doc.warnings.append("annotations_not_scanned_or_drawn")
    return doc


# --- other formats ----------------------------------------------------------------------
MAX_IMAGE_PIXELS = 40_000_000  # refuse before decoding (Pillow's own bomb guard is 89M)
DEFAULT_IMAGE_DPI = 200.0
ZIP_RATIO_LIMIT = 10.0  # a DOCX may inflate to at most 10x the upload limit
ZIP_MAX_ENTRIES = 5_000
SNIFF_BYTES = 65_536  # the head of an upload that format and delimiter detection look at
CSV_DELIMITERS = (",", ";", "\t", "|")
CSV_SNIFF_ROWS = 50

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_DOCX_PART = re.compile(r"^/word/(header\d*|document|footnotes|endnotes|comments|footer\d*)\.xml$")
_PART_ORDER = ("header", "document", "footnotes", "endnotes", "comments", "footer")


def sniff_format(content: bytes, filename: str | None, content_type: str | None) -> DocFormat:
    """Magic bytes first; the name and declared type only decide between CSV and plain text."""
    if not content:
        raise DocumentError("empty_document", "the uploaded file is empty")
    if content.startswith(b"%PDF-"):
        return "pdf"
    if content.startswith(b"\x89PNG\r\n\x1a\n") or content.startswith(b"\xff\xd8\xff"):
        return "image"
    if content.startswith(b"PK\x03\x04"):
        if _is_docx(content):
            return "docx"
        raise DocumentError(
            "unsupported_format", "this zip container is not a .docx document", status=415
        )
    name = (filename or "").lower()
    ctype = (content_type or "").split(";")[0].strip().lower()
    if name.endswith(".csv") or ctype in ("text/csv", "application/csv"):
        return "csv"
    if name.endswith((".txt", ".text")) or ctype.startswith("text/") or _looks_like_text(content):
        return "text"
    raise DocumentError(
        "unsupported_format", "supported formats: PDF, DOCX, PNG, JPEG, TXT, CSV", status=415
    )


def _is_docx(content: bytes) -> bool:
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            names = set(archive.namelist())
    except (zipfile.BadZipFile, OSError):
        return False
    return "word/document.xml" in names and "[Content_Types].xml" in names


def _looks_like_text(content: bytes) -> bool:
    head = content[:SNIFF_BYTES]
    if b"\x00" in head:
        return False
    try:
        # final=False: a multi-byte character cut by the sample's end is not an encoding error
        sample = codecs.getincrementaldecoder("utf-8")().decode(head, final=False)
    except UnicodeDecodeError:
        return False
    printable = sum(ch.isprintable() or ch in "\t\r\n\f" for ch in sample)
    return printable / max(len(sample), 1) >= 0.95


def _decode_text(content: bytes) -> str:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise DocumentError("unsupported_encoding", "text documents must be UTF-8") from None
    return text.replace("\r\n", "\n").replace("\r", "\n")


def extract_text_file(content: bytes) -> ExtractedDocument:
    page = PageText(page=1, width=0.0, height=0.0, source="text", text=_decode_text(content))
    return ExtractedDocument(format="text", pages=[page])


def _check_zip(content: bytes, limit_bytes: int) -> None:
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            infos = archive.infolist()
    except (zipfile.BadZipFile, OSError):
        raise DocumentError("corrupt_document", "the .docx container could not be read") from None
    if (
        len(infos) > ZIP_MAX_ENTRIES
        or sum(i.file_size for i in infos) > ZIP_RATIO_LIMIT * limit_bytes
    ):
        raise DocumentError(
            "document_too_large", "the .docx expands beyond the size limit", status=413
        )


def _paragraph_text(paragraph: Any) -> str:
    out: list[str] = []
    for node in paragraph.iter():
        tag = node.tag
        if tag == _W + "t":
            out.append(node.text or "")
        elif tag == _W + "tab":
            out.append("\t")
        elif tag in (_W + "br", _W + "cr"):
            out.append("\n")
        elif tag == _W + "noBreakHyphen":
            out.append("-")
    return "".join(out)


def _block_lines(element: Any, lines: list[str]) -> None:
    for child in element.iterchildren():
        if child.tag == _W + "p":
            text = _paragraph_text(child)
            if text.strip():
                lines.append(text)
        elif child.tag == _W + "tbl":
            for row in child.iter(_W + "tr"):
                cells = [
                    " ".join(
                        t for t in (_paragraph_text(p) for p in cell.iter(_W + "p")) if t.strip()
                    )
                    for cell in row.iterchildren(_W + "tc")
                ]
                if any(cells):
                    lines.append("\t".join(cells))
        else:
            _block_lines(child, lines)


def extract_docx(content: bytes, settings: Settings) -> ExtractedDocument:
    """Headers, body (paragraphs, table rows as tab-joined cells, text boxes inline), footnotes,
    endnotes, comments and footers, in that order. DOCX has no page geometry: one page, no boxes."""
    import docx
    from lxml import etree

    _check_zip(content, settings.max_document_bytes)
    try:
        document = docx.Document(io.BytesIO(content))
    except Exception as exc:  # noqa: BLE001 - python-docx raises several types; name only
        raise DocumentError(
            "corrupt_document", f"the .docx could not be opened ({type(exc).__name__})"
        ) from None
    parser = etree.XMLParser(
        resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False
    )
    parts: list[tuple[int, str, Any]] = []
    for part in document.part.package.iter_parts():
        name = str(part.partname)
        match = _DOCX_PART.match(name)
        if not match:
            continue
        kind = re.sub(r"\d+$", "", match.group(1))
        element = getattr(part, "element", None)
        if element is None:
            try:
                element = etree.fromstring(part.blob, parser=parser)
            except etree.XMLSyntaxError:
                raise DocumentError(
                    "corrupt_document", "a .docx part is not well-formed XML"
                ) from None
        parts.append((_PART_ORDER.index(kind), name, element))
    lines: list[str] = []
    for _, _, element in sorted(parts, key=lambda p: (p[0], p[1])):
        body = element.find(_W + "body")
        _block_lines(body if body is not None else element, lines)
    page = PageText(page=1, width=0.0, height=0.0, source="docx", text="\n".join(lines))
    return ExtractedDocument(format="docx", pages=[page])


def _sniff_delimiter(text: str) -> str:
    """The delimiter (``, ; tab |``) that splits the first rows into the most consistent shape
    with at least two columns (ties: more columns, then the order above); ``,`` when none does.

    Each candidate is tried with the C ``csv`` parser, so quoting is honoured and the cost is
    linear. ``csv.Sniffer`` is not used: its quote regexes backtrack quadratically (cubically on
    some inputs) on crafted uploads, minutes for 64 KB.
    """
    sample = text[:SNIFF_BYTES]
    truncated = len(text) > len(sample)
    best, best_score = ",", (0.0, 0)
    for delimiter in CSV_DELIMITERS:
        widths: list[int] = []
        try:
            for row in csv.reader(io.StringIO(sample), delimiter=delimiter):
                if any(cell.strip() for cell in row):
                    widths.append(len(row))
                if len(widths) > CSV_SNIFF_ROWS:
                    break
        except csv.Error:
            continue
        if truncated and len(widths) > 1:
            widths.pop()  # the sample may end inside the last row
        if not widths:
            continue
        mode = max(set(widths), key=lambda w: (widths.count(w), w))
        score = (widths.count(mode) / len(widths), mode)
        if mode >= 2 and score > best_score:
            best, best_score = delimiter, score
    return best


def extract_csv(content: bytes, settings: Settings) -> ExtractedDocument:
    """CSV as records: header row → keys, every cell a string, delimiter detected (, ; tab |)."""
    text = _decode_text(content)
    delimiter = _sniff_delimiter(text)
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    try:
        rows = [r for r in reader if any(cell.strip() for cell in r)]
    except csv.Error:
        raise DocumentError(
            "corrupt_document",
            f"the CSV could not be parsed near line {reader.line_num} "
            f"(a field may exceed {csv.field_size_limit()} characters)",
        ) from None
    if not rows:
        raise DocumentError("empty_document", "the CSV has no header row")
    header: list[str] = []
    seen: set[str] = set()
    last_suffix: dict[str, int] = {}
    for i, raw in enumerate(rows[0]):
        base = name = raw.strip() or f"column_{i + 1}"
        suffix = last_suffix.get(base, 1)
        while name in seen:  # a, a_2, a_3, … (amortised linear: each base resumes its count)
            suffix += 1
            name = f"{base}_{suffix}"
        last_suffix[base] = suffix
        seen.add(name)
        header.append(name)
    records: list[dict[str, str]] = []
    for line_no, row in enumerate(rows[1:], start=2):
        if len(row) > len(header):
            raise DocumentError(
                "csv_ragged_rows", f"CSV line {line_no} has more cells than the header"
            )
        records.append({h: (row[i] if i < len(row) else "") for i, h in enumerate(header)})
    if len(records) * len(header) > settings.max_items:
        raise DocumentError(
            "too_many_values", f"the CSV holds more than {settings.max_items} values", status=413
        )
    page = PageText(page=1, width=0.0, height=0.0, source="csv")
    return ExtractedDocument(
        format="csv",
        pages=[page],
        records=records,
        csv_header=header,
        csv_delimiter=delimiter,
    )


def _image_dpi(info: dict[Any, Any]) -> float:
    dpi = info.get("dpi")
    try:
        value = (
            float(dpi[0]) if isinstance(dpi, tuple) else float(dpi) if dpi else DEFAULT_IMAGE_DPI
        )
    except (TypeError, ValueError):
        value = DEFAULT_IMAGE_DPI
    return value if 72.0 <= value <= 1200.0 else DEFAULT_IMAGE_DPI


def extract_image(content: bytes) -> ExtractedDocument:
    """PNG/JPEG → one page: EXIF-transposed, RGB, at most 10000 px a side, re-encoded as PNG.
    Page size in points = pixels × 72 / dpi (dpi from the file, else 200)."""
    try:
        with Image.open(io.BytesIO(content), formats=("PNG", "JPEG")) as opened:
            if opened.width * opened.height > MAX_IMAGE_PIXELS:
                raise DocumentError(
                    "image_too_large",
                    f"images are limited to {MAX_IMAGE_PIXELS} pixels",
                    status=413,
                )
            dpi = _image_dpi(opened.info)
            image = ImageOps.exif_transpose(opened).convert("RGB")
    except DocumentError:
        raise
    except (
        UnidentifiedImageError,
        OSError,
        ValueError,
        SyntaxError,  # Pillow's PNG chunk reader raises it for broken chunks
        EOFError,
        Image.DecompressionBombError,
    ):
        raise DocumentError("corrupt_document", "the image could not be decoded") from None
    longest = max(image.size)
    if longest > MAX_IMAGE_SIDE:
        factor = MAX_IMAGE_SIDE / longest
        image = image.resize(
            (max(1, int(image.width * factor)), max(1, int(image.height * factor))),
            Image.Resampling.LANCZOS,
        )
        dpi *= factor
    buf = io.BytesIO()
    image.save(buf, "PNG", dpi=(dpi, dpi))
    page = PageText(
        page=1,
        width=image.width * 72.0 / dpi,
        height=image.height * 72.0 / dpi,
        source="unreadable",
        scan_suspect=True,
        needs_ocr=True,
        image_coverage=1.0,
    )
    return ExtractedDocument(format="image", pages=[page], image_png=buf.getvalue(), image_dpi=dpi)


def extract_document(
    content: bytes, fmt: DocFormat, settings: Settings, ocr_mode: OcrMode
) -> ExtractedDocument:
    """Dispatch by format (``sniff_format`` decides ``fmt``)."""
    if fmt == "pdf":
        return extract_pdf(content, settings, ocr_mode)
    if fmt == "docx":
        return extract_docx(content, settings)
    if fmt == "csv":
        return extract_csv(content, settings)
    if fmt == "image":
        return extract_image(content)
    return extract_text_file(content)
