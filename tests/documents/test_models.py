from __future__ import annotations

import pytest
from pydantic import ValidationError

from dps.layers.documents.models import (
    PAGE_BREAK,
    BBox,
    DocumentRequest,
    ExtractedDocument,
    PageText,
    validation_message,
)


def test_document_text_and_page_starts() -> None:
    doc = ExtractedDocument(
        format="pdf",
        pages=[
            PageText(page=1, width=1, height=1, source="ocr", text="ab"),
            PageText(page=2, width=1, height=1, source="ocr", text="cde"),
        ],
    )
    assert doc.text() == "ab" + PAGE_BREAK + "cde" and doc.page_starts() == [0, 5]
    assert doc.text()[doc.page_starts()[1] :] == "cde"


def test_bbox_helpers() -> None:
    a, b = BBox(x0=0, y0=0, x1=10, y1=10), BBox(x0=5, y0=5, x1=20, y1=20)
    assert (
        a.area() == 100
        and a.intersection_area(b) == 25
        and a.union(b) == BBox(x0=0, y0=0, x1=20, y1=20)
    )
    assert a.padded(3, 12, 12) == BBox(x0=0, y0=0, x1=12, y1=12)


def test_a_box_off_the_page_pads_to_an_empty_box_on_its_edge() -> None:
    """Text outside a cropped page's crop box keeps its findings but is never drawn: its padded
    box is empty on the page's edge, never inverted (an inverted box failed redaction)."""
    above = BBox(x0=117, y0=-33, x1=225, y1=-17).padded(1.5, 400, 600)
    assert above == BBox(x0=115.5, y0=0, x1=226.5, y1=0) and above.area() == 0
    beside = BBox(x0=410, y0=20, x1=450, y1=30).padded(1.5, 400, 600)
    assert beside.x0 == beside.x1 == 400 and beside.area() == 0


def test_request_validation_messages_never_echo_inputs() -> None:
    with pytest.raises(ValidationError) as exc:
        DocumentRequest.model_validate(
            {"options": {"ocr": "sometimes-SECRET", "consistency": {"key_material": "DEK-SECRET"}}}
        )
    message = validation_message(exc.value)
    assert "options.ocr" in message and "SECRET" not in message
    assert DocumentRequest.model_validate({}).options.ocr == "auto"
    with pytest.raises(ValidationError):
        DocumentRequest.model_validate({"unknown": 1})
