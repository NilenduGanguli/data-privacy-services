"""Console M2 pages against the running stack and the approved mockups (Task 36).

Drives the compose stack (``DPS_TEST_APP_URL``, else ``http://127.0.0.1:${DPS_APP_PORT:-8400}``)
in Chromium (Playwright 1.55, the ``e2e`` extra) with the stack's local demo key and the demo
connections the smoke test creates (``bank-pg-ro``, ``bank-uat``). Every page is shot at 1440 and
390 px (dark) and at 1440 px (light) beside the matching mockup, into ``reports/console/m2-console/``
(git-ignored; ``DPS_SHOTS_DIR`` moves it, ``tools/shots.py``); the comparison is recorded in the build
report.
No screenshot is taken while the once-only key card is on screen.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import httpx
import pytest

from tests.fixtures.services import require_service
from tools.shots import shots_dir

pytestmark = [pytest.mark.browser, pytest.mark.integration, pytest.mark.pg_source, pytest.mark.slow]
playwright = pytest.importorskip("playwright.sync_api")

ROOT = Path(__file__).resolve().parents[2]
SHOTS = shots_dir("m2-console")
MOCKUPS = ROOT / "docs" / "design" / "mockups"
#: The compose stack's local demo key unless the shell names another (test-only literal).
KEY = (
    os.environ.get("DPS_TEST_API_KEY")
    or os.environ.get("DPS_API_KEY")
    or os.environ.get("DPS_BOOTSTRAP_API_KEY")
    or "dps_local_dev_key_change_me"
)
VIEWPORTS = {"desktop": (1440, 900), "mobile": (390, 844)}
DEMO_CONNECTIONS = ("bank-pg-ro", "bank-uat")
#: Uncaught exceptions on console pages (class names only; the mockups are not counted).
PAGE_ERRORS: list[str] = []
SECTIONS = (
    "1 Summary",
    "2 Configuration hashes",
    "3 Classification snapshot",
    "4 Execution",
    "5 Verification",
    "7 Regulatory note",
    "8 Signatures",
)


@pytest.fixture(scope="module")
def app_url() -> str:
    """The lane's app, with the demo connections the smoke test creates."""
    url = require_service("app").rstrip("/")
    names: set[str] = set()
    cursor: str | None = None
    with httpx.Client(base_url=url, headers={"X-API-KEY": KEY}, timeout=15.0) as client:
        while True:
            params: dict[str, Any] = {"limit": 200, **({"cursor": cursor} if cursor else {})}
            answer = client.get("/api/v1/connections", params=params)
            assert answer.status_code == 200, f"GET /connections answered {answer.status_code}"
            body = answer.json()
            names.update(c["name"] for c in body["items"])
            cursor = body.get("next_cursor")
            if not cursor:
                break
    missing = [n for n in DEMO_CONNECTIONS if n not in names]
    if missing:
        message = f"demo connections {missing} missing: run python tools/smoke_test.py first"
        if os.environ.get("DPS_TEST_REQUIRE_SERVICES") == "1":
            pytest.fail(message)
        pytest.skip(message)
    return url


@pytest.fixture(scope="module")
def customer_values() -> list[str]:
    """Source values of the customers the wizard selects (names, e-mails, ids, mobiles)."""
    import asyncpg

    dsn = require_service("source-pg")

    async def load() -> list[str]:
        conn = await asyncpg.connect(dsn)
        try:
            rows = await conn.fetch(
                "SELECT full_name, email, national_id, mobile FROM bank.customers "
                "WHERE country_code = 'IN' ORDER BY customer_id LIMIT 200"
            )
        finally:
            await conn.close()
        return [str(v) for r in rows for v in r.values() if v is not None and len(str(v)) >= 6]

    # a thread of its own: Playwright's sync API keeps an event loop running in this one
    with ThreadPoolExecutor(max_workers=1) as pool:
        values = pool.submit(asyncio.run, load()).result()
    assert len(values) >= 400, "the demo source holds too few IN customers to check against"
    return values


@pytest.fixture(scope="module")
def page(app_url: str) -> Iterator[Any]:
    """One Chromium page (dark scheme) holding the demo key in the tab's session."""
    from playwright.sync_api import sync_playwright

    SHOTS.mkdir(parents=True, exist_ok=True)
    init = (
        "try {"
        f" sessionStorage.setItem('dps.apiKey', {json.dumps(KEY)});"
        " localStorage.setItem('dps.tenant', '');"
        " localStorage.setItem('dps.theme', 'dark');"
        " localStorage.removeItem('dps.jobDraft');"
        "} catch (e) {}"
    )
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1440, "height": 900}, color_scheme="dark")
        ctx.add_init_script(script=init)
        pg = ctx.new_page()
        pg.on(
            "pageerror",
            lambda exc: PAGE_ERRORS.append(exc.name) if pg.url.startswith(app_url) else None,
        )
        yield pg
        browser.close()


def shoot(pg: Any, name: str) -> None:
    """Desktop and phone (dark) and desktop (light) shots; no horizontal scroll at either width."""
    for label, (w, h) in VIEWPORTS.items():
        pg.set_viewport_size({"width": w, "height": h})
        pg.evaluate("window.scrollTo(0, 0)")
        pg.wait_for_timeout(250)
        pg.screenshot(path=str(SHOTS / f"{name}-{label}.png"), full_page=True)
        width = pg.evaluate("document.scrollingElement.scrollWidth")
        assert width <= w + 1, f"{name}: horizontal scroll at {label} ({width} px > {w} px)"
    pg.set_viewport_size({"width": 1440, "height": 900})
    pg.evaluate("document.documentElement.setAttribute('data-theme', 'light')")
    pg.wait_for_timeout(150)
    pg.screenshot(path=str(SHOTS / f"{name}-desktop-light.png"), full_page=True)
    pg.evaluate("document.documentElement.setAttribute('data-theme', 'dark')")


def mockup(pg: Any, file: str, name: str) -> None:
    """The approved mockup at desktop width, dark and light (its ``?light`` switch)."""
    pg.set_viewport_size({"width": 1440, "height": 900})
    pg.goto((MOCKUPS / file).as_uri())
    pg.screenshot(path=str(SHOTS / f"{name}-mockup.png"), full_page=True)
    pg.goto((MOCKUPS / file).as_uri() + "?light")
    pg.screenshot(path=str(SHOTS / f"{name}-mockup-light.png"), full_page=True)


def wizard_mockups(pg: Any) -> None:
    """Mockup 06 step by step through its own stepper, plus step 4 with In place chosen."""
    pg.set_viewport_size({"width": 1440, "height": 900})
    pg.goto((MOCKUPS / "06-connect-wizard.html").as_uri())
    for step in range(1, 6):
        pg.locator(f'.wsteps [data-go="{step}"]').click()
        pg.wait_for_timeout(700)  # the mockup scrolls to the top smoothly; let it finish
        pg.evaluate("window.scrollTo({top: 0, behavior: 'instant'})")
        pg.screenshot(path=str(SHOTS / f"06-wizard-{step}-mockup.png"), full_page=True)
        if step == 4:
            pg.evaluate("document.querySelector('input[name=wm][value=in_place]').click()")
            pg.wait_for_timeout(700)  # the mockup scrolls its confirmation into view
            pg.evaluate("window.scrollTo({top: 0, behavior: 'instant'})")
            pg.screenshot(path=str(SHOTS / "06-wizard-4-confirm-mockup.png"), full_page=True)
            pg.evaluate("document.querySelector('input[name=wm][value=table]').click()")


def no_values(pg: Any, values: list[str]) -> None:
    """No source value of the selected customers is visible on the page."""
    text = pg.inner_text("body")
    leaked = sum(1 for v in values if v in text)
    assert leaked == 0, f"{leaked} source customer values are visible"  # count only, no value


def no_page_errors(pg: Any) -> None:
    """No uncaught exception on the console pages visited so far (class names only)."""
    assert PAGE_ERRORS == [], f"page errors: {PAGE_ERRORS}"


def test_jobs_page(page: Any, app_url: str, customer_values: list[str]) -> None:
    page.goto(f"{app_url}/data-sources")
    for label in (
        "Workers",
        "Queue depth",
        "Oldest waiting",
        "Throughput",
        "Running jobs",
        "Lease reclaims",
    ):
        page.get_by_text(label, exact=False).first.wait_for()
    for header in (
        "Job",
        "Source → target",
        "Mode · write",
        "Owner",
        "Progress",
        "Status",
        "Actions",
    ):
        assert page.locator("table.jobs th", has_text=header).count() >= 1, header
    shoot(page, "07-jobs")
    no_values(page, customer_values)
    no_page_errors(page)
    mockup(page, "07-jobs.html", "07-jobs")


def test_wizard_to_evidence(page: Any, app_url: str, customer_values: list[str]) -> None:
    page.goto(f"{app_url}/data-sources/jobs/new")
    # 1 Connect
    page.get_by_label("PostgreSQL").check()
    # Playwright 1.55 select_option(label=...) takes strings only; option texts are exact names
    page.get_by_label("Saved connection").select_option(label="bank-pg-ro")
    page.get_by_role("button", name="Test connection").click()
    page.get_by_text(re.compile(r"\d+ of \d+ checks passed")).wait_for(timeout=30_000)
    shoot(page, "06-wizard-1-connect")
    # 2 Select data
    page.get_by_role("button", name="Next: Select data").click()
    page.get_by_label("Owner").select_option("bank")
    page.get_by_label("Table").select_option("customers")
    page.get_by_label("WHERE expression").fill("country_code = 'IN'")
    page.get_by_role("button", name="Check").click()
    page.get_by_text(re.compile(r"rows match")).wait_for(timeout=30_000)
    shoot(page, "06-wizard-2-select")
    # 3 Discover and review
    page.get_by_role("button", name="Next: Discover").click()
    page.get_by_role("button", name="Run discovery").click()
    page.get_by_text("Column heat-map").wait_for(timeout=120_000)
    assert page.locator(".hm .cell").count() >= 10
    shoot(page, "06-wizard-3-discover")
    no_values(page, customer_values)
    page.get_by_role("button", name="Accept snapshot").click()
    page.get_by_text(re.compile(r"classification snapshot snap_")).wait_for()
    # 4 Target: the in-place dialog on the read-only source, then a copy to a UAT table
    page.get_by_role("button", name="Next: Target and write mode").click()
    page.get_by_label(re.compile("In place")).check()
    page.get_by_role("button", name=re.compile("Confirm")).click()
    dialog = page.get_by_role("dialog")
    write = dialog.get_by_role("button", name=re.compile(r"Write .* rows in place"))
    assert write.is_disabled()
    dialog.get_by_label(re.compile("Type .* to confirm")).fill("bank.customers")
    assert write.is_disabled(), "in place on a connection without the target role stays blocked"
    shoot(page, "06-wizard-4-confirm")
    dialog.get_by_role("button", name="Choose another write mode").click()
    # M2 verify r2 issue 1: an append through the read-only source, confirmed in step 4, is
    # refused in step 5 by the server's own check, never "all checks passed"
    page.get_by_label(re.compile("Append new rows")).check()
    page.get_by_label("Key offset").fill("900000000")
    page.get_by_role("button", name="Confirm", exact=True).click()
    dialog = page.get_by_role("dialog")
    dialog.get_by_label(re.compile("Type .* to confirm")).fill("bank.customers")
    dialog.get_by_role("button", name=re.compile(r"Append .* rows")).click()
    page.get_by_text(re.compile(r"Confirmed at")).wait_for()
    page.get_by_role("button", name="Next: Review and run").click()
    page.get_by_label("Ticket").fill("CHG-12345")
    readiness = page.get_by_test_id("run-readiness")
    page.get_by_text(re.compile(r"DPS refuses this run: .*connection_role")).wait_for(
        timeout=15_000
    )
    assert readiness.inner_text() != "all checks passed"
    assert page.get_by_role("button", name="Run job").is_disabled()
    page.get_by_role("button", name="Back", exact=True).click()
    page.get_by_label(re.compile("Copy to a table")).check()
    page.get_by_label("Target connection").select_option(label="bank-uat")
    page.get_by_label("Destination schema").fill("bank_uat")
    page.get_by_label("Destination table").fill("customers_console")
    shoot(page, "06-wizard-4-target")
    # 5 Review and run (no shot while the once-only key card is on screen)
    page.get_by_role("button", name="Next: Review and run").click()
    page.get_by_label("Ticket").fill("CHG-12345")
    shoot(page, "06-wizard-5-review")
    page.get_by_role("button", name="Run job").click()
    page.get_by_text(re.compile("shown once")).wait_for(timeout=30_000)
    page.get_by_role("link", name="Open job").click()
    # Job detail (mockup 03), live and finished
    page.get_by_text("Run · partitions").wait_for()
    shoot(page, "03-job-detail")
    page.get_by_role("link", name="Evidence pack").wait_for(timeout=240_000)
    shoot(page, "03-job-detail-done")
    # M1's page rules (the Policies tables' 760 px minimum width) must not reach the job pages
    # (integration of M1 and M2): the reviewed-snapshot table sizes to its card's content.
    min_width = page.locator("#cls").evaluate("t => getComputedStyle(t).minWidth")
    assert min_width in ("0px", "auto"), (
        f"job detail: the classification table is held at {min_width}"
    )
    no_values(page, customer_values)
    # Evidence pack (mockup 10)
    page.get_by_role("link", name="Evidence pack").click()
    for section in SECTIONS:
        page.get_by_text(section).first.wait_for()
    page.get_by_text("Verification passed").first.wait_for()
    shoot(page, "10-evidence")
    no_values(page, customer_values)
    # Reports (mockup 09, the evidence list)
    page.goto(f"{app_url}/reports")
    page.get_by_text("Evidence packs").first.wait_for()
    shoot(page, "09-reports")
    no_values(page, customer_values)
    no_page_errors(page)
    wizard_mockups(page)
    for file, name in (
        ("03-data-sources.html", "03-job-detail"),
        ("10-evidence-pack.html", "10-evidence"),
        ("09-reports.html", "09-reports"),
    ):
        mockup(page, file, name)


def _discover_and_accept(pg: Any) -> None:
    """Step 3: run discovery, wait for the heat-map and accept the proposed snapshot."""
    pg.get_by_role("button", name="Run discovery").click()
    pg.get_by_text("Column heat-map").wait_for(timeout=120_000)
    pg.get_by_role("button", name="Accept snapshot").click()
    pg.get_by_text(re.compile(r"classification snapshot snap_")).wait_for()


def _confirm_in_place(pg: Any, name: str) -> Any:
    """Open the in-place dialog once the rows are counted and type ``name``; returns the dialog."""
    pg.get_by_text(re.compile(r"rows match the selection")).wait_for(timeout=30_000)
    pg.get_by_role("button", name="Confirm", exact=True).click()
    dialog = pg.get_by_role("dialog")
    dialog.get_by_label(re.compile("Type .* to confirm")).fill(name)
    return dialog


def test_in_place_on_a_table_with_an_entity_typed_key(
    page: Any, app_url: str, customer_values: list[str]
) -> None:
    """Verify r1: discovery maps ``bank_restore.customers.customer_id`` (CUSTOMER_ID) to a method
    other than keep, which the server refuses in place (``pk_transform_in_place``). Step 4 says so,
    keeps In place closed and leads back to step 3; keeping the key there opens it, and the run is
    accepted. It is a dry run, so the restored copy stays as seeded."""
    page.goto(f"{app_url}/data-sources/jobs/new")
    page.get_by_label("PostgreSQL").check()
    page.get_by_label("Saved connection").select_option(label="bank-restore-rw")
    page.get_by_role("button", name="Test connection").click()
    page.get_by_text(re.compile(r"\d+ of \d+ checks passed")).wait_for(timeout=30_000)
    page.get_by_role("button", name="Next: Select data").click()
    page.get_by_label("Owner").select_option("bank_restore")
    page.get_by_label("Table").select_option("customers")
    page.get_by_role("button", name="Next: Discover").click()
    _discover_and_accept(page)
    # 4: the accepted snapshot rewrites the key, so In place is a failed check and stays closed
    page.get_by_role("button", name="Next: Target and write mode").click()
    page.get_by_label(re.compile("In place")).check()
    key_check = page.get_by_test_id("in-place-key")
    key_check.get_by_text(
        re.compile(r"customer_id is the update key, but the accepted snapshot rewrites it")
    ).wait_for(timeout=15_000)
    assert key_check.get_by_text("is not changed").count() == 0
    page.get_by_text("In place needs the key kept (step 3)").wait_for()
    assert page.get_by_role("button", name="Next: Review and run").is_disabled()
    no_values(page, customer_values)
    shoot(page, "06-wizard-4-in-place-key")
    dialog = _confirm_in_place(page, "bank_restore.customers")
    dialog.get_by_text(re.compile("rewrites the update key")).wait_for()
    assert dialog.get_by_role("button", name=re.compile(r"Write .* rows in place")).is_disabled()
    dialog.get_by_role("button", name="Choose another write mode").click()
    # 3: a new discovery, the key set to keep, the new snapshot accepted
    key_check.get_by_role("button", name="Change it in step 3").click()
    page.get_by_role("button", name="Run again").click()
    page.get_by_role("button", name="Accept snapshot").wait_for(timeout=120_000)
    method = page.get_by_label("Method of customer_id")
    row = page.locator("table.review tbody tr").filter(has=method)
    row.get_by_text("keep it to write in place").wait_for()
    method.select_option("keep")
    page.get_by_text(re.compile(r"saved · \d+ overrides")).wait_for(timeout=15_000)
    assert row.get_by_text("keep it to write in place").count() == 0
    # M2 verify r2 issue 2: a token cannot fit national_id varchar(18), and PAN's fpe_text runs as
    # a token on these values: both are decisions with the reason, and both are changed to
    # methods that keep the length before writing in place
    decisions = page.locator(".decide")
    decisions.filter(has_text="national_id").get_by_text(
        re.compile(r"longer than character varying\(18\)")
    ).wait_for()
    decisions.filter(has_text="tax_id").get_by_text(
        re.compile(r"longer than character\(10\)")
    ).wait_for()
    page.get_by_label("Method of national_id").select_option("fpe_digits")
    page.get_by_label("Method of tax_id").select_option("mask_partial")
    page.get_by_text(re.compile(r"saved · 3 overrides")).wait_for(timeout=15_000)
    page.get_by_role("button", name="Accept snapshot").click()
    page.get_by_text(re.compile(r"classification snapshot snap_")).wait_for()
    # 4 again: the key is kept, the write is confirmed for the counted rows
    page.get_by_role("button", name="Next: Target and write mode").click()
    key_check.get_by_text("customer_id is the update key and is not changed").wait_for(
        timeout=15_000
    )
    dialog = _confirm_in_place(page, "bank_restore.customers")
    write = dialog.get_by_role("button", name=re.compile(r"Write .* rows in place"))
    assert write.is_enabled()
    write.click()
    page.get_by_text(re.compile(r"Confirmed at")).wait_for()
    # 5: ready, and the server accepts the run (a dry run: nothing is written)
    page.get_by_role("button", name="Next: Review and run").click()
    page.get_by_label("Ticket").fill("CHG-12345")
    page.get_by_text("all checks passed").wait_for()
    with page.expect_response(
        lambda r: r.url.endswith("/api/v1/jobs/run") and r.request.method == "POST"
    ) as answer:
        page.get_by_role("button", name="Dry run (no writes)").click()
    assert answer.value.status < 400, f"POST /jobs/run answered {answer.value.status}"
    page.wait_for_url(re.compile(r"/data-sources/jobs/j_"), timeout=30_000)
    # the dry run itself must succeed (verify r2: the old test only saw POST /jobs/run < 400)
    found = re.search(r"/data-sources/jobs/(j_[0-9A-Za-z]+)", page.url)
    assert found, page.url
    deadline = time.monotonic() + 180
    with httpx.Client(base_url=app_url, headers={"X-API-KEY": KEY}, timeout=15.0) as client:
        while True:
            job = client.get(f"/api/v1/jobs/{found.group(1)}").json()
            if job["state"] in ("succeeded", "failed", "dead", "canceled"):
                break
            assert time.monotonic() < deadline, f"dry run still {job['state']}"
            time.sleep(1)
    assert job["state"] == "succeeded", job.get("error_code")
    assert job["progress"]["outcome"] == "dry_run"
    no_values(page, customer_values)
    no_page_errors(page)
