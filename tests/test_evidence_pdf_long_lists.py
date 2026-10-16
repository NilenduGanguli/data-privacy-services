"""An evidence pack with very long lists still renders as a PDF (M5 G3 review r2 N10).

reportlab cannot split one table row across pages: a pack whose ``verification.review_columns``
(or ``consistency.key_custody`` / ``verification.fallbacks``) joined into a single cell taller than
a page made ``GET /evidence/{id}/pdf`` answer 500. Such lists are capped in the PDF — the first
entries and "and N more (see the JSON pack)" — while the JSON pack keeps every entry.
"""

from __future__ import annotations

import io
from typing import Any

import pytest

pytest.importorskip("reportlab")
pypdf = pytest.importorskip("pypdf")

from dps.layers.reports.pdf import render_evidence_pdf  # noqa: E402
from tests.test_evidence_pdf import BASE, SHA  # noqa: E402


def _text(pdf: bytes) -> str:
    """All pages' text with whitespace collapsed (table cells wrap lines anywhere)."""
    pages = (page.extract_text() for page in pypdf.PdfReader(io.BytesIO(pdf)).pages)
    return " ".join(" ".join(pages).split())


def test_a_thousand_review_columns_render() -> None:
    columns = [f"$.SCHEMA_OWNER_{i:04d}.SOME_LONG_COLUMN_NAME" for i in range(1000)]
    pack: dict[str, Any] = {
        **BASE,
        "verification": {
            **BASE["verification"],
            "review_required": True,
            "review_columns": columns,
        },
    }
    pdf = render_evidence_pdf(pack, pack_sha256=SHA)
    assert pdf.startswith(b"%PDF-")
    text = _text(pdf)
    assert "and 980 more (see the JSON pack)" in text
    assert "SCHEMA_OWNER_0000" in text and "SCHEMA_OWNER_0999" not in text


def test_long_custody_and_fallback_lists_render() -> None:
    custody = {f"$.COLUMN_WITH_A_LONG_NAME_{i:04d}": "caller" for i in range(400)}
    fallbacks = {f"fallback:note_with_a_long_name_{i:04d}": i for i in range(400)}
    pack: dict[str, Any] = {
        **BASE,
        "consistency": {**BASE["consistency"], "key_custody": custody},
        "verification": {**BASE["verification"], "fallbacks": fallbacks},
    }
    pdf = render_evidence_pdf(pack, pack_sha256=SHA)
    assert pdf.startswith(b"%PDF-")
    assert _text(pdf).count("and 380 more (see the JSON pack)") == 2


def test_short_lists_are_unchanged() -> None:
    pack: dict[str, Any] = {
        **BASE,
        "verification": {
            **BASE["verification"],
            "review_required": True,
            "review_columns": ["$.A", "$.B"],
            "fallbacks": {"fallback:fpe_disabled": 3},
        },
    }
    text = _text(render_evidence_pdf(pack, pack_sha256=SHA))
    assert "$.A, $.B" in text and "fallback:fpe_disabled 3" in text and "more (see" not in text
