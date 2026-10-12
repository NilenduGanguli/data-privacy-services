"""Scanner fixtures: Chromium (Playwright 1.55.0 drives chromium-1187)."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from typing import Any

import pytest


def _require() -> bool:
    return os.environ.get("DPS_TEST_REQUIRE_SERVICES") == "1"


def skip_or_fail(hint: str) -> None:
    """Skip with ``hint`` — or fail under ``DPS_TEST_REQUIRE_SERVICES=1`` (milestone gates)."""
    if _require():
        pytest.fail(f"required service unavailable — {hint}")
    pytest.skip(hint)


@pytest.fixture
async def chromium() -> AsyncIterator[Any]:
    """A headless Chromium for one test."""
    pw = pytest.importorskip("playwright.async_api")
    async with pw.async_playwright() as p:
        try:
            browser = await p.chromium.launch(headless=True)
        except pw.Error:
            skip_or_fail("run: python -m playwright install chromium (playwright==1.55.0 → 1187)")
        try:
            yield browser
        finally:
            await browser.close()
