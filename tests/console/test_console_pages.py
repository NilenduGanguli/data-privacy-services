"""The ten-minute demo's nine console pages render on the running stack (M5 Task 26).

Drives the stack's console (``DPS_TEST_APP_URL``) in Chromium as the demo seed's ``demo-console`` key
(``var/demo/demo-keys.json``, tenant ``retail-banking-apac``): every route of ``tools/e2e_demo.py``'s
console walk shows its own heading (never just the sidebar's section name) within 15 s, logs no console error, and makes no request to another
origin (CSP sanity: the console loads nothing from outside the app). The job, scan and evidence
pages open the tenant's newest succeeded run, scan and evidence pack — run ``tools/demo_seed.py``
and ``tools/e2e_demo.py`` first; without the key file the test is skipped. Nothing is printed.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest

from tests.fixtures.services import require_service
from tools.demo_client import DemoError, DpsClient, load_keys
from tools.e2e_demo import DEFAULT_KEYS, DEFAULT_TENANT, KEY_NAME, PAGES

pytestmark = [pytest.mark.browser, pytest.mark.integration]
playwright = pytest.importorskip("playwright.sync_api")

TIMEOUT_MS = 15_000


@pytest.fixture(scope="module")
def app_url() -> str:
    return require_service("app").rstrip("/")


@pytest.fixture(scope="module")
def demo_key() -> str:
    if not Path(DEFAULT_KEYS).is_file():
        pytest.skip("no var/demo/demo-keys.json: run python tools/demo_seed.py first")
    return load_keys(Path(DEFAULT_KEYS))[KEY_NAME]


@pytest.fixture(scope="module")
def ids(app_url: str, demo_key: str) -> dict[str, Any]:
    """The newest succeeded run, scan (and its first page with both filled screens) and the
    newest evidence pack of the demo tenant."""
    c = DpsClient(app_url, demo_key, tenant=DEFAULT_TENANT)
    try:
        runs = c.get("/jobs", kind="run", state="succeeded", limit=1).get("items") or []
        scans = c.get("/jobs", kind="scan", state="succeeded", limit=1).get("items") or []
        packs = c.get("/evidence", limit=1).get("items") or []
        page = 1
        if scans:
            pages = c.get(f"/scans/{scans[0]['id']}/pages", limit=200).get("items") or []
            page = next(
                (
                    p["page_no"]
                    for p in pages
                    if {"filled_synthesize", "filled_obfuscate"} <= set(p.get("artefacts") or {})
                ),
                1,
            )
    except DemoError as exc:
        pytest.fail(f"reading the demo tenant's jobs: {exc}")
    finally:
        c.close()
    if not (runs and scans and packs):
        pytest.skip("the demo tenant has no finished run, scan or pack: run tools/e2e_demo.py")
    return {"job": runs[0]["id"], "scan": scans[0]["id"], "page": page, "evidence": packs[0]["id"]}


@pytest.fixture(scope="module")
def browser() -> Iterator[Any]:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        b = p.chromium.launch()
        try:
            yield b
        finally:
            b.close()


@pytest.mark.parametrize(("name", "route", "selector", "text"), PAGES, ids=[p[0] for p in PAGES])
def test_the_demo_page_renders_without_errors_or_foreign_requests(
    browser: Any,
    app_url: str,
    demo_key: str,
    ids: dict[str, Any],
    name: str,
    route: str,
    selector: str,
    text: str,
) -> None:
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    ctx.add_init_script(
        script="try {"
        f" sessionStorage.setItem('dps.apiKey', {json.dumps(demo_key)});"
        f" localStorage.setItem('dps.tenant', {json.dumps(DEFAULT_TENANT)});"
        " localStorage.setItem('dps.theme', 'light');"
        "} catch (e) {}"
    )
    page = ctx.new_page()
    errors: list[str] = []
    foreign: list[str] = []
    origin = urlsplit(app_url).netloc
    page.on("console", lambda m: errors.append(m.type) if m.type == "error" else None)
    page.on("pageerror", lambda exc: errors.append("pageerror"))
    page.on(
        "request",
        lambda r: (
            foreign.append(urlsplit(r.url).netloc)
            if urlsplit(r.url).scheme in ("http", "https") and urlsplit(r.url).netloc != origin
            else None
        ),
    )
    try:
        page.goto(app_url + route.format(**ids), wait_until="networkidle")
        page.locator(selector, has_text=text).first.wait_for(timeout=TIMEOUT_MS)
        page.wait_for_load_state("networkidle")
        assert page.locator("main").first.is_visible(), name
    finally:
        ctx.close()
    assert errors == [], name
    assert foreign == [], name
