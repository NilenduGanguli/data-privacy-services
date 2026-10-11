from __future__ import annotations

import io
import re
from concurrent.futures import ThreadPoolExecutor

import pytest

pytestmark = pytest.mark.docs
pytest.importorskip("pdfplumber")
pytest.importorskip("pypdfium2")

import pypdfium2 as pdfium  # noqa: E402
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


def _raw_pdf(*objects: bytes) -> bytes:
    """A PDF of the given objects, numbered from 1 (1 is the catalog), with a valid xref."""
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


def _one_page(page: bytes) -> bytes:
    """A minimal one-page PDF whose page dictionary is ``page``."""
    return _raw_pdf(
        b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>", page
    )


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


def _blank(*sizes: tuple[float, float], rotate: int = 0) -> bytes:
    writer = PdfWriter()
    for width, height in sizes:
        page = writer.add_blank_page(width=width, height=height)
        if rotate:
            page.rotation = rotate
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def test_pages_too_large_to_render_are_refused():
    # 433 bytes that would render (blank pages are scan-suspect, so OCR'd) at 200 dpi as a
    # 40,000 x 40,000 px bitmap, about 17 GB; Read v3.2 also refuses more than 10,000 px a side
    huge = _blank((14_400, 14_400))
    assert len(huge) < 1_000
    with pytest.raises(DocumentError) as exc:
        extract_pdf(huge, SET, "auto")
    assert (exc.value.code, exc.value.status) == ("image_too_large", 413)
    assert str(exc.value).startswith("page 1 ")
    # the bound is the rendered side, so 3,600 pt is the limit at the default 200 dpi
    assert extract_pdf(_blank((3_600, 100)), SET, "auto").pages[0].width == 3_600
    with pytest.raises(DocumentError) as second:
        extract_pdf(_blank((612, 792), (100, 3_601)), SET, "auto")
    assert second.value.code == "image_too_large" and str(second.value).startswith("page 2 ")
    with pytest.raises(DocumentError):  # and on a rotated page
        extract_pdf(_blank((3_601, 100), rotate=90), SET, "auto")


def test_the_page_bound_follows_the_highest_render_resolution():
    wide = _blank((7_200, 100))
    assert extract_pdf(wide, Settings(_env_file=None, pdf_render_dpi=100), "auto").pages
    previews_finer = Settings(_env_file=None, pdf_render_dpi=72, preview_dpi=150)
    assert extract_pdf(_blank((4_700, 100)), previews_finer, "auto").pages
    with pytest.raises(DocumentError) as exc:  # a 150-dpi preview would be 15,000 px wide
        extract_pdf(wide, previews_finer, "auto")
    assert exc.value.code == "image_too_large"


def _forged_count(data: bytes, count: int) -> bytes:
    """``data`` with the page tree root's ``/Count`` replaced (same width, so the xref holds)."""
    forged, n = re.subn(rb"/Count \d\b", b"/Count %d" % count, data, count=1)
    assert n == 1 and len(forged) == len(data)
    return forged


@pytest.mark.parametrize(
    "make",
    [
        # PDFium trusts /Count (2) while pdfplumber walks /Kids (1): loading page 2 failed uncoded
        lambda: _forged_count(_blank((612, 792)), 2),
        # PDFium sees 1 page, pdfplumber 3, and the 3rd is too large to render
        lambda: _forged_count(_blank((612, 792), (612, 792), (14_400, 14_400)), 1),
    ],
    ids=["count-above-the-tree", "count-below-the-tree"],
)
def test_a_page_count_that_contradicts_the_page_tree_is_a_corrupt_document(make):
    with pytest.raises(DocumentError) as exc:
        extract_pdf(make(), SET, "auto")
    assert (exc.value.code, exc.value.status) == ("corrupt_document", 422)
    assert exc.value.__cause__ is None  # no PDFium or pdfminer text kept
    assert exc.value.__context__ is None or exc.value.__suppress_context__


def test_renderer_page_loads_that_fail_are_corrupt_documents():
    with PdfRenderer(_forged_count(_blank((612, 792)), 2)) as r:
        assert len(r) == 2  # PDFium believes /Count; page 2 does not exist
        calls = (lambda: r.page_size(1), lambda: r.image_coverage(1), lambda: r.render(1, 72))
        for call in calls:
            with pytest.raises(DocumentError) as exc:
                call()
            assert exc.value.code == "corrupt_document" and exc.value.__cause__ is None


def test_a_closed_renderer_refuses_every_call():
    r = PdfRenderer(_blank((612, 792)))
    r.close()
    r.close()  # idempotent
    calls = (
        lambda: len(r),
        lambda: r.page_size(0),
        lambda: r.image_coverage(0),
        lambda: r.render(0, 72),
    )
    for call in calls:
        with pytest.raises(DocumentError) as exc:
            call()
        assert exc.value.code == "corrupt_document"


_FONT = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"


def _text_page(contents: int, extra: bytes = b"") -> bytes:
    return (
        b"<< %s /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 3 0 R >> >> "
        b"/Contents %d 0 R >>" % (extra, contents)
    )


def _stream(text: bytes) -> bytes:
    body = b"BT /F1 24 Tf 72 700 Td (" + text + b") Tj ET"
    return b"<< /Length %d >>\nstream\n%s\nendstream" % (len(body), body)


def _two_readings(count: int) -> bytes:
    """Object 4 is a page without /Type (pypdf and PDFium: a page; pdfminer: skipped); object 5
    is typed /Page but carries /Kids (pypdf and pdfminer: a page; PDFium: an empty intermediate
    node). Unless normalisation settles the tree, PDFium renders object 4 while the text layer
    reads object 5."""
    return _raw_pdf(
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [4 0 R 5 0 R] /Count %d >>" % count,
        _FONT,
        _text_page(6),
        _text_page(7, b"/Type /Page /Kids []"),
        _stream(b"RASTER 123-45-6789"),
        _stream(b"Only the text layer reads this harmless page"),
    )


def _pdfium_texts(data: bytes) -> list[str]:
    doc = pdfium.PdfDocument(data)
    try:
        texts = []
        for i in range(len(doc)):
            page = doc[i]
            texts.append(page.get_textpage().get_text_range())
            page.close()
        return texts
    finally:
        doc.close()


def test_the_renderer_and_the_text_layer_read_the_same_pages():
    # a tree that each parser reads differently is settled by normalisation: every page is
    # rendered and its text layer scanned, page for page
    doc = extract_pdf(_two_readings(2), SET, "auto")
    assert ["123-45-6789" in p.text for p in doc.pages] == [True, False]
    rendered = _pdfium_texts(doc.pdf)
    assert len(rendered) == 2
    for page, text in zip(doc.pages, rendered, strict=True):
        assert page.text.split() == text.split()


def test_a_page_rendered_but_never_read_is_refused():
    # with /Count 1 this was accepted: PDFium rendered object 4 (the SSN) as the only page, while
    # the text layer read only object 5
    with pytest.raises(DocumentError) as exc:
        extract_pdf(_two_readings(1), SET, "auto")
    assert (exc.value.code, exc.value.status) == ("corrupt_document", 422)
