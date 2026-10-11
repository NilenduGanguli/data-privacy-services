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
