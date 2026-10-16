"""Chromium for the browser-worker: one browser per process, one isolated context per scan.

The browser is launched lazily and relaunched when it has disconnected (a crashed Chromium) or
when it belongs to another event loop (a Playwright connection is bound to the loop that started
it; the old objects are dropped, not used). Each scan gets its own ``BrowserContext`` — cookies,
storage and cache never cross scans — created with the options the request guard relies on
(``service_workers="block"``: Playwright cannot route what a service worker intercepts; the
guard's scope proxy: Playwright routes only the first hop of a request) and with the guard
installed before the first page exists.

``LAUNCH_ARGS`` is part of the guard (``guard.py``): ``--disable-shared-workers`` removes the
``SharedWorker`` API, whose requests and WebSockets no route ever sees; ``--disable-quic`` and
``--force-webrtc-ip-handling-policy=disable_non_proxied_udp`` keep every connection on TCP through
the scope proxy (no UDP around it). A scan context is closed with ``close_scan_context``, never
with ``context.close()`` alone after closing pages with ``page.close()``.

S5 (security review): the browser-worker's Chromium is launched with :func:`launch_args` —
``LAUNCH_ARGS`` plus ``--host-resolver-rules`` (:data:`RESOLVER_RULES`) that resolve nothing but
the scope proxy's own address (``MAP * ~NOTFOUND, EXCLUDE 127.0.0.1``; ``MAP *`` covers IP
literals too, so a connection that bypassed the proxy would reach no name and no address). Every
lookup of a scan is the scope proxy's, which checks each address it dials (``guard._dial_hop``).
The plan named the scan's registered hosts for the exclusions; with every connection on the proxy
the browser needs to resolve none of them, and ``MAP *`` without the proxy's address would fail
every connection (``ERR_PROXY_CONNECTION_FAILED``, measured).
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

from playwright.async_api import Browser, BrowserContext, Playwright, async_playwright
from playwright.async_api import Error as PlaywrightError

from dps import __version__
from dps.config import Settings
from dps.layers.scanner.guard import ScanGuard
from dps.layers.scanner.proxy import PROXY_HOST, ProxySettings
from dps.netguard import chromium_resolver_rules

LAUNCH_ARGS = (
    "--disable-dev-shm-usage",
    "--disable-background-networking",
    "--disable-sync",
    "--disable-component-update",
    "--no-first-run",
    # the request guard depends on these (see guard.py)
    "--disable-shared-workers",
    "--disable-quic",
    "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
    "--dns-prefetch-disable",
)
#: S5: the scan browser resolves no name itself — only the scope proxy's address
RESOLVER_RULES = chromium_resolver_rules([PROXY_HOST])


def launch_args() -> list[str]:
    """The browser-worker's Chromium flags: ``LAUNCH_ARGS`` and the resolver rules (S5)."""
    return [*LAUNCH_ARGS, f"--host-resolver-rules={RESOLVER_RULES}"]


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
                headless=settings.browser_headless, args=launch_args()
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
    guard: ScanGuard,
    harden_js: bool = True,
) -> BrowserContext:
    """Isolated context: no service workers (Playwright cannot route their requests), no downloads,
    no permissions, UTC, a user agent that names DPS so the target's logs show who visited, every
    connection through ``guard``'s scope proxy, and ``guard`` installed before any page.

    Close it with ``close_scan_context(context, guard)``.
    """
    ua = (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
        f"Chrome/{browser.version} Safari/537.36 DPS-Scanner/{__version__}"
    )
    proxy = await guard.start_proxy()
    try:
        context = await _new_context(browser, settings, storage_state, locale, ua, proxy)
    except BaseException:
        await guard.stop_proxy()
        raise
    try:
        await guard.install(context, harden_js=harden_js)
    except BaseException:
        await context.close()
        await guard.stop_proxy()
        raise
    return context


async def close_scan_context(context: BrowserContext, guard: ScanGuard) -> None:
    """Close a scan context so that nothing leaves while it goes (idempotent).

    Every page is closed with ``guard.close`` (its unload handlers run while the route aborts
    everything); a page that could not be navigated away goes with the context, and closing a
    context runs no unload handlers. Then the guard's scope proxy stops.
    """
    try:
        for page in list(context.pages):
            await guard.close(page)
        with contextlib.suppress(PlaywrightError):
            await context.close()
    finally:
        await guard.stop_proxy()


async def _new_context(
    browser: Browser,
    settings: Settings,
    storage_state: dict[str, Any] | None,
    locale: str,
    ua: str,
    proxy: ProxySettings,
) -> BrowserContext:
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
        proxy={"server": proxy["server"], "bypass": proxy["bypass"]},
    )
