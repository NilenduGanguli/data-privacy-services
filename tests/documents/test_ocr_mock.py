from __future__ import annotations

import pytest

pytestmark = [pytest.mark.docs, pytest.mark.ocr]

from dps.layers.documents.ocr import AzureReadClient  # noqa: E402
from dps.layers.documents.render import PdfRenderer, encode_png  # noqa: E402
from tests.documents.fixtures import text_pdf  # noqa: E402


async def test_mock_reads_a_rendered_page(ocr_url: str) -> None:
    client = AzureReadClient(endpoint=ocr_url, key="mock-key", timeout_s=60, poll_interval_s=0.2)
    try:
        assert await client.probe() == (True, "up")
        with PdfRenderer(text_pdf()) as renderer:
            png = encode_png(renderer.render(0, 200))
        page = await client.read(png)
    finally:
        await client.aclose()
    text = " ".join(w.text for w in page.words)
    assert page.unit == "pixel" and (page.width, page.height) == (1700.0, 2200.0)
    assert "123-45-6789" in text and "ananya.k@example-mail.in" in text
