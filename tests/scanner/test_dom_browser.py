"""Chromium runtime and DOM extraction against the demo site (labels, shadow DOM, iframes, text,
links, clickables), plus the page-side helpers the capture and fill steps use."""

from __future__ import annotations

import contextlib
from typing import Any

import pytest

pytest.importorskip("playwright")
from playwright.async_api import TimeoutError as PlaywrightTimeoutError  # noqa: E402

from dps.config import Settings  # noqa: E402
from dps.layers.scanner.browser import (  # noqa: E402
    close_browser,
    get_browser,
    new_scan_context,
)
from dps.layers.scanner.dom import (  # noqa: E402
    CLICKABLES_JS,
    REPLACE_TEXT_JS,
    SET_VALUES_JS,
    extract_page,
    frame_map,
    text_rects,
    utf16_offset,
)
from dps.layers.scanner.models import PageSnapshot  # noqa: E402
from tests.scanner.helpers import received  # noqa: E402

pytestmark = pytest.mark.browser


def _session(url: str) -> dict[str, Any]:
    host = url.split("://", 1)[1].split(":", 1)[0].split("/", 1)[0]
    return {
        "cookies": [
            {
                "name": "demo_session",
                "value": "demo-valid",
                "domain": host,
                "path": "/",
                "expires": -1,
                "httpOnly": True,
                "secure": False,
                "sameSite": "Lax",
            }
        ],
        "origins": [],
    }


async def _open(chromium: Any, url: str, settings: Settings, *, cookies: bool = False) -> Any:
    ctx = await new_scan_context(
        chromium, settings, storage_state=_session(url) if cookies else None, locale="en_IN"
    )
    page = await ctx.new_page()
    await page.goto(url)
    with contextlib.suppress(PlaywrightTimeoutError):
        await page.wait_for_load_state("networkidle", timeout=3000)
    return ctx, page


async def _snap(
    chromium: Any, url: str, settings: Settings, *, cookies: bool = False, **kw: Any
) -> PageSnapshot:
    ctx, page = await _open(chromium, url, settings, cookies=cookies)
    try:
        opts = {"iframes": True, "shadow_dom": True, "max_fields": 400, **kw}
        return await extract_page(page, **opts)
    finally:
        await ctx.close()


async def test_label_sources_and_non_fields(chromium, demo_site_url, scan_settings) -> None:  # noqa: ANN001
    snap = await _snap(chromium, f"{demo_site_url}/fixtures/labels", scan_settings)
    by_name = {f.name: f for f in snap.fields}
    assert {
        n: (by_name[n].label, by_name[n].label_source)
        for n in ("a1", "a2", "a3", "a4", "a5", "a6", "a7", "a8")
    } == {
        "a1": ("Customer email", "label_for"),
        "a2": ("Mobile number", "label_wrap"),
        "a3": ("Passport number", "aria_labelledby"),
        "a4": ("Date of birth", "aria_label"),
        "a5": ("PIN code", "placeholder"),
        "a6": ("Aadhaar", "nearest_text"),
        "a7": ("Employee ID", "title"),
        "a8": ("", "none"),
    }
    assert by_name["a9"].tag == "select"
    assert [o.text for o in by_name["a9"].options][1:] == ["Salaried", "Self-employed", "Student"]
    assert by_name["csrf"].input_type == "hidden" and by_name["csrf"].visible is False
    assert {f.input_type for f in snap.fields}.isdisjoint({"submit", "button", "reset", "image"})
    assert [f.field_no for f in snap.fields] == list(range(1, len(snap.fields) + 1))


async def test_shadow_dom_field_found_in_tree_order(chromium, demo_site_url, scan_settings) -> None:  # noqa: ANN001
    snap = await _snap(chromium, f"{demo_site_url}/apply/address", scan_settings)
    names = [f.name for f in snap.fields]
    pin = next(f for f in snap.fields if f.name == "pin")
    assert pin.in_shadow and pin.label == "PIN code" and pin.label_source == "label_for"
    assert pin.autocomplete == "postal-code"
    assert names.index("state") < names.index("pin") < names.index("landmark")
    line1 = next(f for f in snap.fields if f.name == "line1")
    assert pin.form_index == line1.form_index  # the host's form
    residence = next(f for f in snap.fields if f.name == "residence")
    assert residence.label_source == "nearest_text"
    flat = await _snap(chromium, f"{demo_site_url}/apply/address", scan_settings, shadow_dom=False)
    assert "pin" not in [f.name for f in flat.fields]


async def test_iframe_fields_with_frame_offsets(chromium, demo_site_url, scan_settings) -> None:  # noqa: ANN001
    snap = await _snap(chromium, f"{demo_site_url}/apply/card", scan_settings)
    card = next(f for f in snap.fields if f.name == "ccNumber")
    frame = next(fr for fr in snap.frames if fr.frame_index == card.frame_index)
    assert card.frame_index == 1 and frame.same_origin and card.autocomplete == "cc-number"
    assert card.rect is not None and card.rect.y >= frame.offset.y  # top-page coordinates
    assert card.key.startswith("1:")
    outer = [f.field_no for f in snap.fields if f.frame_index == 0]
    inner = [f.field_no for f in snap.fields if f.frame_index == 1]
    assert outer and inner and max(outer) < min(inner)  # numbered across frames in order
    no_frames = await _snap(chromium, f"{demo_site_url}/apply/card", scan_settings, iframes=False)
    assert {f.frame_index for f in no_frames.fields} == {0}


async def test_texts_links_clickables_and_values_stay_in_memory(  # noqa: ANN001
    chromium, demo_site_url, scan_settings, demo_data
) -> None:
    profile = await _snap(chromium, f"{demo_site_url}/profile", scan_settings, cookies=True)
    cells = {t.text.strip(): t for t in profile.texts}
    assert cells[demo_data["aadhaar"]].in_table
    assert cells[demo_data["aadhaar"]].label_hint == "Aadhaar"
    assert cells[demo_data["account_number"]].label_hint == "Account number"
    assert cells[demo_data["customer_id"]].label_hint == "Customer ID"
    shown = repr(profile)
    for value in demo_data.values():
        assert value not in shown  # page data never reaches a repr
    home = await _snap(chromium, f"{demo_site_url}/", scan_settings)
    hrefs = {link.href for link in home.links}
    assert f"{demo_site_url}/apply" in hrefs and any(link.download for link in home.links)
    app = await _snap(chromium, f"{demo_site_url}/app", scan_settings)
    assert [c.kind for c in app.clickables] == ["role_link"]
    contact = await _snap(chromium, f"{demo_site_url}/profile/contact", scan_settings, cookies=True)
    assert next(f for f in contact.fields if f.name == "contact1").value == demo_data["email"]
    review = await _snap(chromium, f"{demo_site_url}/apply/review", scan_settings)
    hints = {t.text.strip(): t.label_hint for t in review.texts}
    assert hints[demo_data["pan"]] == "PAN number"


async def test_a_password_value_is_never_read(chromium, demo_site_url, scan_settings) -> None:  # noqa: ANN001
    ctx, page = await _open(chromium, f"{demo_site_url}/login", scan_settings)
    try:
        await page.locator("#password").fill("pw-typed-by-the-test")
        await page.locator("#username").fill("someone")
        snap = await extract_page(page, iframes=True, shadow_dom=True, max_fields=400)
    finally:
        await ctx.close()
    by_name = {f.name: f for f in snap.fields}
    assert by_name["password"].value is None and by_name["username"].value == "someone"


async def test_numbers_are_stable_across_loads_and_truncation_is_reported(  # noqa: ANN001
    chromium, demo_site_url, scan_settings
) -> None:
    url = f"{demo_site_url}/apply/identity"
    first = await _snap(chromium, url, scan_settings)
    second = await _snap(chromium, url, scan_settings)
    assert [(f.key, f.name) for f in first.fields] == [(f.key, f.name) for f in second.fields]
    assert len(first.fields) == 9 and first.truncated is False
    capped = await _snap(chromium, url, scan_settings, max_fields=3)
    assert [f.name for f in capped.fields] == ["fullName", "dob", "aadhaar"] and capped.truncated


async def test_clickables_are_restamped_with_the_same_ordinals(  # noqa: ANN001
    chromium, demo_site_url, scan_settings
) -> None:
    # a role=link that is laid out but not visible must be skipped by both scripts alike
    hidden = (
        "(() => { const s = document.createElement('span'); s.setAttribute('role', 'link'); "
        "s.textContent = 'x'; s.style.visibility = 'hidden'; document.body.prepend(s); })()"
    )
    ctx, page = await _open(chromium, f"{demo_site_url}/app", scan_settings)
    try:
        await page.evaluate(hidden)
        snap = await extract_page(page, iframes=True, shadow_dom=True, max_fields=400)
        stamped = await page.evaluate(
            "Array.from(document.querySelectorAll('[data-dps-click]')).map(e => e.dataset.dpsClick)"
        )
        await page.reload()
        await page.evaluate(hidden)
        assert await page.evaluate(CLICKABLES_JS, {"frameIndex": 0, "shadow": True}) == 1
        assert await page.evaluate(CLICKABLES_JS, 0) == 1  # the plan's call form still works
        again = await page.evaluate(
            "Array.from(document.querySelectorAll('[data-dps-click]')).map(e => e.dataset.dpsClick)"
        )
    finally:
        await ctx.close()
    assert stamped == again == [f"0:{c.ordinal}" for c in snap.clickables]


async def test_text_rects_and_in_page_helpers(  # noqa: ANN001
    chromium, demo_site_url, scan_settings, demo_data
) -> None:
    ctx, page = await _open(chromium, f"{demo_site_url}/profile", scan_settings, cookies=True)
    try:
        snap = await extract_page(page, iframes=True, shadow_dom=True, max_fields=400)
        frames = await frame_map(page, iframes=True)
        cell = next(t for t in snap.texts if t.text.strip() == demo_data["aadhaar"])
        start = cell.text.index(demo_data["aadhaar"])
        end = start + len(demo_data["aadhaar"])
        rects = await text_rects(page, frames, snap, [(0, cell.i, start, end), (0, 10**6, 0, 1)])
        assert len(rects) == 2 and rects[1] == []
        (box,) = rects[0]
        assert box.w > 20 and box.h > 5 and box.y > 0
        assert await page.evaluate(REPLACE_TEXT_JS, [[cell.i, "XXXX XXXX 0000"]]) == 1
        body = await page.evaluate("document.body.innerText")
        assert demo_data["aadhaar"] not in body and "XXXX XXXX 0000" in body
    finally:
        await ctx.close()


async def test_set_values_fires_no_events(chromium, demo_site_url, scan_settings) -> None:  # noqa: ANN001
    before = received(demo_site_url)
    ctx, page = await _open(chromium, f"{demo_site_url}/apply/address", scan_settings)
    try:
        snap = await extract_page(page, iframes=True, shadow_dom=True, max_fields=400)
        await page.evaluate(
            "window.__events = 0; for (const t of ['input', 'change']) "
            "document.addEventListener(t, () => { window.__events += 1; }, true);"
        )
        pairs = [[f.key, "Sample"] for f in snap.fields if f.tag == "input" and f.visible]
        assert await page.evaluate(SET_VALUES_JS, pairs) == len(pairs) >= 3
        pin = next(f for f in snap.fields if f.name == "pin")
        shown = await page.evaluate(
            "document.querySelector('postal-lookup').shadowRoot.querySelector('#pin').value"
        )
        await page.wait_for_timeout(400)  # the demo's autosave debounce is 150 ms
        events = await page.evaluate("window.__events")
    finally:
        await ctx.close()
    assert pin.key in {k for k, _ in pairs} and shown == "Sample" and events == 0
    assert received(demo_site_url)["non_get"] == before["non_get"]


def test_text_rects_convert_code_points_to_utf16() -> None:  # pure helper, no browser
    assert utf16_offset("ab😀cd", 3) == 4 and utf16_offset("abc", 2) == 2
    assert utf16_offset("a\ud83d", 2) == 2  # a lone surrogate (text cut by the page) never raises


async def test_new_scan_context_is_isolated_and_names_dps(chromium, scan_settings) -> None:  # noqa: ANN001
    ctx = await new_scan_context(chromium, scan_settings, storage_state=None, locale="en_IN")
    page = await ctx.new_page()
    ua = await page.evaluate("navigator.userAgent")
    assert "DPS-Scanner/" in ua
    assert await page.evaluate("Intl.DateTimeFormat().resolvedOptions().timeZone") == "UTC"
    assert await page.evaluate("navigator.language") == "en-IN"
    size = await page.evaluate("[window.innerWidth, window.innerHeight]")
    assert size == [scan_settings.scan_viewport_width, scan_settings.scan_viewport_height]
    await ctx.close()


async def test_one_browser_per_process_relaunched_after_close(scan_settings) -> None:  # noqa: ANN001
    try:
        first = await get_browser(scan_settings)
        assert first.is_connected() and await get_browser(scan_settings) is first
        await close_browser()
        second = await get_browser(scan_settings)
        assert second is not first and second.is_connected()
    finally:
        await close_browser()
