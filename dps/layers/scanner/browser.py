"""Chromium for the browser-worker: one browser per process, one isolated context per scan.

The browser is launched lazily and relaunched when it has disconnected (a crashed Chromium) or
when it belongs to another event loop (a Playwright connection is bound to the loop that started
it; the old objects are dropped, not used). Each scan gets its own ``BrowserContext`` — cookies,
storage and cache never cross scans — created with the options the request guard relies on
(``service_workers="block"``: Playwright cannot route what a service worker intercepts).
"""

from __future__ import annotations

import asyncio
from typing import Any

from playwright.async_api import Browser, BrowserContext, Playwright, async_playwright

from dps import __version__
from dps.config import Settings

LAUNCH_ARGS = (
    "--disable-dev-shm-usage",
    "--disable-background-networking",
    "--disable-sync",
    "--disable-component-update",
    "--no-first-run",
)

_playwright: Playwright | None = None
_browser: Browser | None = None
_loop: asyncio.AbstractEventLoop | None = None
_lock: asyncio.Lock | None = None


def _state_lock() -> asyncio.Lock:
    """The lock of the running loop (a lock is bound to the loop it first waits on)."""
    global _lock, _loop, _playwright, _browser
    loop = asyncio.get_running_loop()
    if _loop is not loop:
        _loop, _lock, _playwright, _browser = loop, asyncio.Lock(), None, None
    assert _lock is not None
    return _lock


async def get_browser(settings: Settings) -> Browser:
    """The process's Chromium, launched (or relaunched after a disconnect) on first use."""
    global _playwright, _browser
    async with _state_lock():
        if _browser is None or not _browser.is_connected():
            if _playwright is None:
                _playwright = await async_playwright().start()
            _browser = await _playwright.chromium.launch(
                headless=settings.browser_headless, args=list(LAUNCH_ARGS)
            )
        return _browser


async def close_browser() -> None:
    """Close the browser and stop Playwright (idempotent)."""
    global _playwright, _browser
    async with _state_lock():
        browser, pw = _browser, _playwright
        _browser, _playwright = None, None
        try:
            if browser is not None and browser.is_connected():
                await browser.close()
        finally:
            if pw is not None:
                await pw.stop()


async def new_scan_context(
    browser: Browser,
    settings: Settings,
    *,
    storage_state: dict[str, Any] | None,
    locale: str,
) -> BrowserContext:
    """Isolated context: no service workers (Playwright cannot route their requests), no downloads,
    no permissions, UTC, a user agent that names DPS so the target's logs show who visited."""
    ua = (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
        f"Chrome/{browser.version} Safari/537.36 DPS-Scanner/{__version__}"
    )
    return await browser.new_context(
        viewport={"width": settings.scan_viewport_width, "height": settings.scan_viewport_height},
        device_scale_factor=1,
        storage_state=storage_state,  # type: ignore[arg-type]
        service_workers="block",
        accept_downloads=False,
        locale=locale.replace("_", "-"),
        timezone_id="UTC",
        user_agent=ua,
        permissions=[],
        java_script_enabled=True,
        ignore_https_errors=False,
    )
