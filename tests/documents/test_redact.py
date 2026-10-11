from __future__ import annotations

import io
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.docs
pytest.importorskip("reportlab")

from PIL import Image  # noqa: E402
from pypdf import PdfReader, PdfWriter  # noqa: E402
from pypdf.generic import (  # noqa: E402
    ArrayObject,
    DecodedStreamObject,
    DictionaryObject,
    NameObject,
    NumberObject,
)

from dps.config import Settings  # noqa: E402
from dps.layers.documents.errors import DocumentError  # noqa: E402
from dps.layers.documents.extract import extract_document  # noqa: E402
from dps.layers.documents.layout import span_boxes  # noqa: E402
from dps.layers.documents.models import BBox, ExtractedDocument  # noqa: E402
from dps.layers.documents.redact import (  # noqa: E402
    PageRedaction,
    inspect_output_pdf,
    redact_document,
)
from dps.layers.documents.render import PdfRenderer  # noqa: E402
from tests.documents.fixtures import (  # noqa: E402
    annotated_pdf,
    docx_document,
    png_document,
    text_pdf,
)

SET = Settings(_env_file=None)


def _plan(doc: ExtractedDocument, needles: tuple[str, ...]) -> list[PageRedaction]:
    plan = []
    for p in doc.pages:
        boxes = []
        for needle in needles:
            at = p.text.find(needle)
            if at >= 0:
                boxes += span_boxes(p, at, at + len(needle))
        plan.append(PageRedaction(index=p.page - 1, width=p.width, height=p.height, boxes=boxes))
    return plan


def _ratio(image: Image.Image, box: tuple[int, int, int, int], *, dark: bool) -> float:
    crop = image.convert("L").crop(box)
    hist = crop.histogram()
    return sum(hist[:64] if dark else hist[:128]) / max(crop.width * crop.height, 1)


def _px(b, scale: float) -> tuple[int, int, int, int]:  # noqa: ANN001
    return (
        int(b.x0 * scale) + 1,
        int(b.y0 * scale) + 1,
        int(b.x1 * scale) - 1,
        int(b.y1 * scale) - 1,
    )


def _redact(doc: ExtractedDocument, plan: list[PageRedaction], fmt: str = "jpeg") -> bytes:
    return redact_document(doc, plan, dpi=200, image_format=fmt, jpeg_quality=85).pdf


def test_redacted_pdf_has_no_text_and_boxes_are_black() -> None:
    doc = extract_document(text_pdf(), "pdf", SET, "auto")
    plan = _plan(doc, ("Ananya Krishnan", "123-45-6789"))
    out = _redact(doc, plan)
    inspection = inspect_output_pdf(out)
    assert inspection.clean and inspection.pages == 1 and inspection.text_layer_chars == 0
    assert b"123-45-6789" not in out and b"Ananya" not in out
    with PdfRenderer(out) as r:
        image = r.render(0, 100)
    scale = 100 / 72
    for box in plan[0].boxes:
        assert _ratio(image, _px(box, scale), dark=True) > 0.98
    label = next(w for w in doc.pages[0].words if w.text == "SSN").bbox
    assert (
        0.02 < _ratio(image, _px(label, scale), dark=False) < 0.6
    )  # "SSN" still legible, not painted


def test_page_count_and_sizes_are_preserved() -> None:
    doc = extract_document(text_pdf(pages=3), "pdf", SET, "auto")
    reader = PdfReader(io.BytesIO(_redact(doc, _plan(doc, ("123-45-6789",)))))
    assert [(float(p.mediabox.width), float(p.mediabox.height)) for p in reader.pages] == [
        (612.0, 792.0)
    ] * 3


def test_metadata_annotations_and_forms_do_not_survive() -> None:
    doc = extract_document(annotated_pdf(), "pdf", SET, "auto")
    out = _redact(doc, _plan(doc, ("123-45-6789",)))
    inspection = inspect_output_pdf(out)
    assert inspection.clean and inspection.annotations == 0
    assert b"Ananya" not in out and b"123-45-6789" not in out
    assert (PdfReader(io.BytesIO(out)).metadata or {}).get("/Author") in (None, "")


@pytest.mark.parametrize("fmt,filter_name", [("jpeg", "/DCTDecode"), ("png", "/FlateDecode")])
def test_image_formats(fmt: str, filter_name: str) -> None:
    doc = extract_document(text_pdf(), "pdf", SET, "auto")
    page = PdfReader(io.BytesIO(_redact(doc, _plan(doc, ("123-45-6789",)), fmt))).pages[0]
    xobjects = page["/Resources"]["/XObject"]
    filters = {str(xobjects[k].get_object()["/Filter"]) for k in xobjects}
    assert filter_name in "".join(filters)


def test_image_documents_become_one_page_pdfs() -> None:
    doc = extract_document(png_document(dpi=200), "image", SET, "auto")
    page = doc.pages[0]
    plan = [
        PageRedaction(
            index=0,
            width=page.width,
            height=page.height,
            boxes=[BBox(x0=72, y0=100, x1=300, y1=120)],
        )
    ]
    reader = PdfReader(io.BytesIO(_redact(doc, plan)))
    assert len(reader.pages) == 1 and float(reader.pages[0].mediabox.width) == pytest.approx(
        page.width, abs=0.5
    )


def test_output_is_deterministic() -> None:
    doc = extract_document(text_pdf(), "pdf", SET, "auto")
    plan = _plan(doc, ("123-45-6789",))
    assert _redact(doc, plan) == _redact(doc, plan)


@pytest.mark.skipif(shutil.which("pdftoppm") is None, reason="poppler pdftoppm not installed")
def test_pdftoppm_cross_check(tmp_path: Path) -> None:
    doc = extract_document(text_pdf(), "pdf", SET, "auto")
    plan = _plan(doc, ("123-45-6789",))
    (tmp_path / "out.pdf").write_bytes(_redact(doc, plan))
    subprocess.run(
        [
            "pdftoppm",
            "-r",
            "72",
            "-png",
            "-singlefile",
            str(tmp_path / "out.pdf"),
            str(tmp_path / "page"),
        ],
        check=True,
        capture_output=True,
        timeout=60,
    )
    with Image.open(tmp_path / "page.png") as image:
        assert _ratio(image, _px(plan[0].boxes[0], 1.0), dark=True) > 0.95


# --- beyond the plan: the inspection must see what it is there to refuse ---------------------


def test_inspection_flags_a_text_layer_and_annotations() -> None:
    plain = inspect_output_pdf(text_pdf())
    assert not plain.clean and plain.text_layer_chars > 0 and plain.text_operators > 0
    annotated = inspect_output_pdf(annotated_pdf())
    assert not annotated.clean and annotated.annotations == 1


def _with_form_xobject(data: bytes, stream: bytes) -> bytes:
    """``data`` with a Form XObject drawing ``stream`` added to page 1 (the page itself only
    calls it with ``Do``)."""
    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(data)))
    page = writer.pages[0]
    form = DecodedStreamObject()
    form.set_data(stream)
    form.update(
        {
            NameObject("/Type"): NameObject("/XObject"),
            NameObject("/Subtype"): NameObject("/Form"),
            NameObject("/BBox"): ArrayObject([NumberObject(n) for n in (0, 0, 612, 792)]),
        }
    )
    resources = page["/Resources"].get_object()
    xobjects = resources.get("/XObject")
    xobjects = xobjects.get_object() if xobjects is not None else DictionaryObject()
    xobjects[NameObject("/Fx1")] = writer._add_object(form)
    resources[NameObject("/XObject")] = xobjects
    contents = page.get_contents()
    assert contents is not None
    contents.set_data(contents.get_data() + b"\nq /Fx1 Do Q\n")
    page.replace_contents(contents)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def test_inspection_reads_text_operators_inside_form_xobjects() -> None:
    doc = extract_document(text_pdf(), "pdf", SET, "auto")
    clean = _redact(doc, _plan(doc, ("123-45-6789",)))
    assert inspect_output_pdf(clean).clean
    hidden = _with_form_xobject(clean, b"BT /F1 12 Tf 72 72 Td (hidden) Tj ET")
    inspection = inspect_output_pdf(hidden)
    assert not inspection.clean and inspection.text_operators >= 1


def test_only_pdf_and_image_documents_are_redacted() -> None:
    doc = extract_document(docx_document(), "docx", SET, "auto")
    plan = [PageRedaction(index=0, width=0.0, height=0.0, boxes=[])]
    with pytest.raises(DocumentError) as exc:
        _redact(doc, plan)
    assert exc.value.code == "redaction_unsupported_format"


def test_empty_boxes_paint_nothing() -> None:
    """A box with no area (a finding outside the page) has nothing on the raster to cover."""
    from dps.layers.documents.redact import paint_boxes

    image = Image.new("RGB", (100, 100), "white")
    paint_boxes(image, [BBox(x0=10, y0=0, x1=50, y1=0)], width_pt=100, height_pt=100)
    assert image.getextrema() == ((255, 255),) * 3
