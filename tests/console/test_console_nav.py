"""The console's navigation below 1100 px (S2 console r1 B1; mockups 01-12, ``vault.js``).

Below 1100 px the sidebar is hidden; the approved mockups put a menu button in the topbar that opens it
as a drawer over a scrim. Before this fix the console rendered no button at all, so at 390 and 1024 px
Playground and Admin reached no other section and the Overview could not reach Policies or Admin. This
test drives the running stack's console (``DPS_TEST_APP_URL``) in Chromium at 390 px and 1024 px: from
every top-level page each of the seven sections is reached through the drawer, the drawer opens with
no horizontal scroll, and Escape, a scrim click and the navigation itself close it. At 1440 px the
sidebar is always shown and the button is not. Nothing here reads or prints a value.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from typing import Any

import pytest

from tests.fixtures.services import require_service

pytestmark = [pytest.mark.browser, pytest.mark.integration]
playwright = pytest.importorskip("playwright.sync_api")

KEY = (
    os.environ.get("DPS_TEST_API_KEY")
    or os.environ.get("DPS_API_KEY")
    or os.environ.get("DPS_BOOTSTRAP_API_KEY")
    or "dps_local_dev_key_change_me"
)
#: The seven sections of the sidebar (Layout.tsx NAV, mockup vault.js) and their routes.
SECTIONS: dict[str, str] = {
    "Overview": "/",
    "Playground": "/playground",
    "Policies": "/policies",
    "Data sources & jobs": "/data-sources",
    "UI scanner": "/scanner",
    "Reports & audit": "/reports",
    "Admin": "/admin",
}
#: Whether the page shows the section of ``path`` and has landed there: the Policies page opens
#: the first policy (``/policies/<name>``) once its list call returns (with or without an error),
#: and that route change closes a drawer opened before it — so the walk waits for it (under the
#: per-key rate limit the list answers late, and the next click found the drawer closed).
IN_SECTION_JS = (
    "(path) => path === '/' ? location.pathname === '/'"
    " : path === '/policies' ? location.pathname.startsWith('/policies/')"
    " : location.pathname === path || location.pathname.startsWith(path + '/')"
)
#: Whether the document scrolls sideways (the console's rule: never at 390 px).
H_SCROLL_JS = "() => document.documentElement.scrollWidth > window.innerWidth + 1"
IS_OPEN = "document.querySelector('.shell').classList.contains('nav-open')"
OPEN_JS = f"() => {IS_OPEN}"
CLOSED_JS = f"() => !{IS_OPEN}"


@pytest.fixture(scope="module")
def browser() -> Iterator[Any]:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        b = p.chromium.launch()
        try:
            yield b
        finally:
            b.close()


def _page(browser: Any, width: int, height: int) -> Any:
    ctx = browser.new_context(viewport={"width": width, "height": height}, color_scheme="dark")
    ctx.add_init_script(
        script="try {"
        f" sessionStorage.setItem('dps.apiKey', {json.dumps(KEY)});"
        " localStorage.setItem('dps.tenant', '');"
        "} catch (e) {}"
    )
    return ctx.new_page()


def _open(pg: Any) -> None:
    toggle = pg.locator("header.topbar button.nav-toggle")
    assert toggle.is_visible()
    assert toggle.get_attribute("aria-expanded") == "false"
    assert toggle.get_attribute("aria-label") == "Open navigation"
    toggle.click()
    pg.wait_for_function(OPEN_JS)
    assert toggle.get_attribute("aria-expanded") == "true"
    assert toggle.get_attribute("aria-label") == "Close navigation"
    assert pg.locator("#sidebar").is_visible() and pg.locator(".nav-scrim").is_visible()
    assert not pg.evaluate(H_SCROLL_JS)  # the open drawer never widens the page


@pytest.mark.parametrize("width", [390, 1024])
def test_every_section_is_reachable_through_the_drawer_from_every_page(
    browser: Any, width: int
) -> None:
    app_url = require_service("app").rstrip("/")
    pg = _page(browser, width, 844)
    errors: list[str] = []
    pg.on("pageerror", lambda exc: errors.append(exc.name))
    try:
        for start, start_path in SECTIONS.items():
            pg.goto(f"{app_url}{start_path}")
            pg.wait_for_selector("header.topbar")
            pg.wait_for_function(IN_SECTION_JS, arg=start_path)  # landed (Policies opens one)
            assert not pg.locator("#sidebar").is_visible(), (width, start)  # hidden below 1100
            assert not pg.evaluate(H_SCROLL_JS), (width, start)
            for name, path in SECTIONS.items():
                _open(pg)
                links = pg.locator("#sidebar a.nav-item")
                assert links.count() == len(SECTIONS)
                links.filter(has_text=name).first.click()
                pg.wait_for_function(IN_SECTION_JS, arg=path)
                assert pg.evaluate(IN_SECTION_JS, path), (width, start, name)
                pg.wait_for_function(CLOSED_JS)  # navigation closes it
                if path != start_path:  # back to the page this round starts from
                    _open(pg)
                    pg.locator("#sidebar a.nav-item").filter(has_text=start).first.click()
                    pg.wait_for_function(IN_SECTION_JS, arg=start_path)
                    pg.wait_for_function(CLOSED_JS)
        _open(pg)
        pg.keyboard.press("Escape")  # Escape closes and gives the focus back to the button
        pg.wait_for_function(CLOSED_JS)
        focused = pg.evaluate("() => document.activeElement.className")
        assert focused == "nav-toggle"
        _open(pg)
        pg.mouse.click(width - 10, 400)  # the scrim beside the drawer
        pg.wait_for_function(CLOSED_JS)
        assert not pg.locator("#sidebar").is_visible()
    finally:
        pg.context.close()
    assert errors == []


def test_the_sidebar_is_always_shown_on_a_wide_screen(browser: Any) -> None:
    app_url = require_service("app").rstrip("/")
    pg = _page(browser, 1440, 900)
    try:
        pg.goto(f"{app_url}/admin")
        pg.wait_for_selector("header.topbar")
        assert pg.locator("#sidebar").is_visible()
        assert pg.locator("#sidebar a.nav-item").count() == len(SECTIONS)
        assert not pg.locator("header.topbar button.nav-toggle").is_visible()
    finally:
        pg.context.close()
