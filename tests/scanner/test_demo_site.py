"""The demo onboarding site (the scan target): pages, session, counters, static files."""

from __future__ import annotations

import asyncio
import socket
from typing import Any
from urllib.parse import urlsplit

import httpx
import pytest

from dps.config import Settings
from tests.scanner.helpers import received

pytestmark = pytest.mark.browser

PAGES = (
    "/",
    "/apply",
    "/apply/identity",
    "/apply/address",
    "/apply/employment",
    "/apply/card",
    "/apply/documents",
    "/apply/us",
    "/apply/review",
    "/login",
    "/help",
    "/help/contact-us",
    "/help/track",
    "/help/faq/3",
    "/app",
    "/app/nominee",
    "/app/preferences",
    "/frames/card-widget",
    "/fixtures/labels",
)


def test_pages_render_and_profile_needs_a_session(demo_site_url: str, demo_data: dict) -> None:
    with httpx.Client(base_url=demo_site_url, follow_redirects=False) as c:
        for path in PAGES:
            assert c.get(path).status_code == 200, path
        assert c.get("/help/faq/9").status_code == 404 and c.get("/nope").status_code == 404
        anon = c.get("/profile")
        assert anon.status_code == 302 and anon.headers["location"].startswith("/login")
        assert c.get("/profile/contact").status_code == 302
        assert 'pattern="\\d{4} \\d{4} \\d{4}"' in c.get("/apply/identity").text
    with httpx.Client(base_url=demo_site_url, cookies={"demo_session": "demo-valid"}) as c:
        page = c.get("/profile").text
        assert demo_data["card_number"] in page and demo_data["aadhaar"] in page


def test_every_demo_value_is_rendered_somewhere(demo_site_url: str, demo_data: dict) -> None:
    """Later tests treat each value as customer data and prove none reaches DPS storage — so each
    one, account numbers and customer ids included, must really be on a page."""
    cookies = {"demo_session": "demo-valid"}
    with httpx.Client(base_url=demo_site_url, cookies=cookies) as c:
        rendered = " ".join(
            c.get(p).text
            for p in ("/profile", "/profile/contact", "/apply/review", "/apply/identity")
        )
    assert {"account_number", "customer_id"} <= set(demo_data)
    for key, value in demo_data.items():
        if key == "aadhaar":
            assert value in rendered and f"XXXX XXXX {value[-4:]}" in rendered
        else:
            assert value in rendered, key


def test_received_counts_non_get_and_form_get_and_logout(demo_site_url: str) -> None:
    before = received(demo_site_url)
    httpx.post(f"{demo_site_url}/api/autosave", json={"x": 1})
    httpx.get(f"{demo_site_url}/help/track/result", params={"email": "x"})
    httpx.get(f"{demo_site_url}/logout")
    httpx.get(f"{demo_site_url}/admin")
    after = received(demo_site_url)
    assert after["non_get"] == before["non_get"] + 1
    assert (
        after["by_path"]["POST /api/autosave"] == before["by_path"].get("POST /api/autosave", 0) + 1
    )
    assert after["form_get"] == before["form_get"] + 1
    assert after["logout_hits"] == before["logout_hits"] + 1
    assert after["admin_hits"] == before["admin_hits"] + 1


def test_a_form_post_is_counted_and_answered_with_a_redirect(demo_site_url: str) -> None:
    before = received(demo_site_url)
    r = httpx.post(f"{demo_site_url}/apply/identity/submit", data={"fullName": "x"})
    after = received(demo_site_url)
    assert r.status_code in (302, 303) and after["non_get"] == before["non_get"] + 1
    assert after["by_path"]["POST /apply/identity/submit"] >= 1


def test_static_files_and_traversal_guard(demo_site_url: str) -> None:
    assert httpx.get(f"{demo_site_url}/static/forms.js").status_code == 200
    assert httpx.get(f"{demo_site_url}/static/../server.mjs").status_code == 404
    parts = urlsplit(demo_site_url)
    for raw in ("/static/../server.mjs", "/static/..%2fserver.mjs", "/static/%2e%2e/data.json"):
        with socket.create_connection((parts.hostname, parts.port or 80), timeout=5) as s:
            s.sendall(f"GET {raw} HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n".encode())
            head = s.recv(64).decode("latin-1")
        assert head.startswith("HTTP/1.1 404"), raw


def test_health(demo_site_url: str) -> None:
    assert httpx.get(f"{demo_site_url}/health").json() == {"status": "ok"}


async def test_a_draft_is_flushed_when_a_page_goes_and_the_guard_holds_it(
    chromium: Any, demo_site_url: str, scan_settings: Settings
) -> None:
    """The demo flushes a draft (beacon + keepalive fetch) on pagehide. A plain ``page.close()``
    lets it out (positive control); ``ScanGuard.close`` of a filled page does not."""
    from dps.layers.scanner.browser import close_scan_context, new_scan_context
    from dps.layers.scanner.guard import ScanGuard

    def drafts() -> int:
        return int(received(demo_site_url)["by_path"].get("POST /api/draft", 0))

    before = drafts()
    plain = await chromium.new_context()
    page = await plain.new_page()
    await page.goto(f"{demo_site_url}/apply/identity")
    await page.locator("#fullName").fill("Sample Person")
    await page.close()
    await asyncio.sleep(0.8)
    await plain.close()
    assert drafts() >= before + 1
    guard = ScanGuard(host_allowed=lambda h: h in scan_settings.scan_registered_hosts_list)
    ctx = await new_scan_context(
        chromium, scan_settings, storage_state=None, locale="en_IN", guard=guard, harden_js=False
    )
    try:
        page = await ctx.new_page()
        await page.goto(f"{demo_site_url}/apply/identity")
        assert await guard.arm(page, ["Sample Person"])
        sent = received(demo_site_url)["non_get"]
        await page.locator("#fullName").fill("Sample Person")
        assert await guard.close(page)
        await asyncio.sleep(0.8)
    finally:
        await close_scan_context(ctx, guard)
    assert received(demo_site_url)["non_get"] == sent
    blocked = {(c.method, c.reason) for c in guard.drain_counts() if c.decision == "blocked"}
    assert ("POST", "api_removed") in blocked  # the beacon and the keepalive of the flush
