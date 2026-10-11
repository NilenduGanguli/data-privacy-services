from __future__ import annotations

import io
from concurrent.futures import ThreadPoolExecutor

import pytest

pytestmark = pytest.mark.docs
pytest.importorskip("pdfplumber")
pytest.importorskip("pypdfium2")

from pypdf import PdfReader, PdfWriter  # noqa: E402
from pypdf.generic import NameObject  # noqa: E402

from dps.config import Settings  # noqa: E402
from dps.layers.documents.errors import DocumentError  # noqa: E402
from dps.layers.documents.extract import extract_pdf  # noqa: E402
from dps.layers.documents.models import ExtractedDocument, Word  # noqa: E402
from dps.layers.documents.render import PdfRenderer  # noqa: E402
from tests.documents.fixtures import (  # noqa: E402
    KYC_LINES,
    annotated_pdf,
    cropped_pdf,
    encrypted_pdf,
    hybrid_pdf,
    rewrite,
    rotated_pdf,
    scanned_pdf,
    text_pdf,
)

SET = Settings(_env_file=None)


def _dark(image, box) -> float:
    crop = image.convert("L").crop(box)
    return sum(crop.histogram()[:128]) / max(crop.width * crop.height, 1)


def _word(doc: ExtractedDocument, text: str) -> Word:
    return next(w for p in doc.pages for w in p.words if w.text == text)


def _ink(doc: ExtractedDocument, text: str) -> tuple[float, float]:
    word, s = _word(doc, text), 200 / 72
    with PdfRenderer(doc.pdf) as r:
        image = r.render(0, 200)
    on = (
        int(word.bbox.x0 * s),
        int(word.bbox.y0 * s),
        int(word.bbox.x1 * s),
        int(word.bbox.y1 * s),
    )
    off = (on[0], on[1] + int(200 * s), on[2], on[3] + int(200 * s))
    return _dark(image, on), _dark(image, off)


def test_text_layer_pages_read_exactly():
    page = extract_pdf(text_pdf(), SET, "auto").pages[0]
    assert page.source == "text_layer" and not page.needs_ocr and not page.scan_suspect
    assert page.text.splitlines() == list(KYC_LINES)
    assert all(w.char_boxes is not None and len(w.char_boxes) == len(w.text) for w in page.words)


def test_boxes_land_on_the_raster_for_cropped_pages():
    doc = extract_pdf(cropped_pdf(), SET, "auto")
    on, off = _ink(doc, "123-45-6789")
    assert on > 0.05 and off < 0.01
    assert (doc.pages[0].width, doc.pages[0].height) == (400.0, 600.0)


def test_rotated_pages_are_upright():
    doc = extract_pdf(rotated_pdf(), SET, "auto")
    assert (
        doc.pages[0].width > doc.pages[0].height and "SSN 123-45-6789 on file" in doc.pages[0].text
    )
    on, off = _ink(doc, "123-45-6789")
    assert on > 0.05 and off < 0.01


def test_scanned_pages_are_flagged_for_ocr():
    auto = extract_pdf(scanned_pdf(), SET, "auto").pages[0]
    assert (
        auto.needs_ocr
        and auto.scan_suspect
        and auto.source == "unreadable"
        and auto.image_coverage > 0.95
    )
    never = extract_pdf(scanned_pdf(), SET, "never").pages[0]
    assert never.scan_suspect and not never.needs_ocr


def test_hybrid_candidates_keep_their_text_layer():
    page = extract_pdf(hybrid_pdf(), SET, "auto").pages[0]
    assert page.needs_ocr and page.source == "text_layer" and "123-45-6789" in page.text
    assert page.image_coverage >= 0.25


def test_ocr_always_ignores_the_text_layer():
    page = extract_pdf(text_pdf(), SET, "always").pages[0]
    assert page.needs_ocr and page.text == "" and page.source == "unreadable"


@pytest.mark.parametrize(
    "make,code",
    [(encrypted_pdf, "encrypted_pdf"), (lambda: b"%PDF-1.7 garbage", "corrupt_document")],
)
def test_refusals(make, code):
    with pytest.raises(DocumentError) as exc:
        extract_pdf(make(), SET, "auto")
    assert (exc.value.code, exc.value.status) == (code, 422)


def test_empty_user_password_pdfs_are_read():
    assert "123-45-6789" in extract_pdf(rewrite(text_pdf(), encrypt=""), SET, "auto").pages[0].text


def test_page_limit():
    with pytest.raises(DocumentError) as exc:
        extract_pdf(text_pdf(pages=3), Settings(_env_file=None, max_document_pages=2), "auto")
    assert (exc.value.code, exc.value.status) == ("too_many_pages", 413)


def test_annotations_are_reported():
    assert "annotations_not_scanned_or_drawn" in extract_pdf(annotated_pdf(), SET, "auto").warnings


def test_renderer_serialises_concurrent_renders():
    data = text_pdf(pages=2)
    with PdfRenderer(data) as r, ThreadPoolExecutor(8) as pool:
        sizes = list(pool.map(lambda i: r.render(i % 2, 100).size, range(16)))
    assert len(set(sizes)) == 1 and sizes[0] == (850, 1100)


def _bogus_filter(data: bytes) -> bytes:
    """Every page's content stream claims a filter pypdf does not implement."""
    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(data)))
    for page in writer.pages:
        page["/Contents"].get_object()[NameObject("/Filter")] = NameObject("/BogusDecode")
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def _one_page(page: bytes) -> bytes:
    """A minimal one-page PDF whose page dictionary is ``page`` (a valid xref table)."""
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>"]
    objects.append(page)
    out, offsets = b"%PDF-1.7\n", []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n%s\nendobj\n" % (number, body)
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        xref,
    )
    return out


@pytest.mark.parametrize(
    "make",
    [
        # NotImplementedError from pypdf while moving /Rotate into the content
        lambda: _bogus_filter(rewrite(text_pdf(), rotate=90)),
        # NotImplementedError while translating a crop box to the origin
        lambda: _bogus_filter(rewrite(text_pdf(), crop=(50, 50, 450, 650))),
        # AttributeError: /Contents is a number, not a stream
        lambda: _one_page(b"<< /Type /Page /Parent 2 0 R /MediaBox [10 10 100 100] /Contents 7 >>"),
    ],
    ids=["unsupported-filter-rotated", "unsupported-filter-cropped", "contents-not-a-stream"],
)
def test_malformed_pdfs_are_corrupt_documents_not_uncoded_errors(make):
    with pytest.raises(DocumentError) as exc:
        extract_pdf(make(), SET, "auto")
    assert (exc.value.code, exc.value.status) == ("corrupt_document", 422)
    assert exc.value.__cause__ is None and exc.value.__suppress_context__  # no pypdf text kept
