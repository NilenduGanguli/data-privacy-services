"""Shared harness of the M3 console browser checks (Tasks 18 and 18b).

Same harness as M2's ``test_console_m2.py``: Playwright 1.55 Chromium against the lane's app
(``DPS_TEST_APP_URL``, else ``http://127.0.0.1:${DPS_APP_PORT:-8400}``) with the stack's local demo
key, screenshots into ``docs/superpowers/build/m3-shots/`` (synthetic seed data only; committed by
Task 21's docs commit). The demo connections are registered through the API when missing — the
Oracle and MongoDB ones under the names Task 21's ``smoke --m3`` uses, against the compose
``demo-secrets`` refs — so the checks run on a stack that only has ``--profile oracle --profile
sources`` up and the Task 15 seed. Nothing here prints or asserts on a value: leak checks count.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from tests.services import require_service

ROOT = Path(__file__).resolve().parents[2]
SHOTS = ROOT / "docs" / "superpowers" / "build" / "m3-shots"
MOCKUPS = ROOT / "docs" / "design" / "mockups"
#: The compose stack's local demo key unless the shell names another (test-only literal).
KEY = (
    os.environ.get("DPS_TEST_API_KEY")
    or os.environ.get("DPS_API_KEY")
    or os.environ.get("DPS_BOOTSTRAP_API_KEY")
    or "dps_local_dev_key_change_me"
)
VIEWPORTS = {"desktop": (1440, 900), "mobile": (390, 844)}
_ORACLE = {"host": "oracle", "port": 1521, "service_name": "FREEPDB1"}
#: name → (engine, params, secret_ref, roles): the compose network's hosts and demo-secrets refs.
CONNECTIONS: dict[str, tuple[str, dict[str, Any], str, list[str]]] = {
    "smoke-ora-src": ("oracle", _ORACLE, "dps/src/oracle-crm-ro", ["source"]),
    "smoke-ora-tgt": ("oracle", _ORACLE, "dps/tgt/oracle-crm-masked", ["target"]),
    "smoke-mdb-src": ("mongo", {"database": "crm"}, "dps/src/mongo-crm", ["source"]),
    "smoke-mdb-tgt": ("mongo", {"database": "crm_uat"}, "dps/tgt/mongo-crm-uat", ["target"]),
    "bank-pg-ro": (
        "postgres",
        {"host": "source-pg", "port": 5432, "database": "bank"},
        "dps/src/bank-pg-ro",
        ["source"],
    ),
    "bank-uat": (
        "postgres",
        {"host": "source-pg", "port": 5432, "database": "bank"},
        "dps/tgt/bank-uat",
        ["target"],
    ),
}


def api(url: str) -> httpx.Client:
    """An API client with the demo key."""
    return httpx.Client(base_url=url, headers={"X-API-KEY": KEY}, timeout=60.0)


def ensure_connections(url: str, names: tuple[str, ...]) -> dict[str, str]:
    """name → connection id; each missing connection is created and must pass its test."""
    with api(url) as c:
        listed = c.get("/api/v1/connections", params={"limit": 200})
        assert listed.status_code == 200, f"GET /connections answered {listed.status_code}"
        ids = {x["name"]: x["id"] for x in listed.json()["items"]}
        for name in names:
            if name in ids:
                continue
            engine, params, ref, roles = CONNECTIONS[name]
            body = {"name": name, "engine": engine, "params": params, "secret_ref": ref, "roles": roles}  # fmt: skip
            made = c.post("/api/v1/connections", json=body)
            assert made.status_code == 201, made.json().get("code")
            ids[name] = made.json()["id"]
            test = c.post(f"/api/v1/connections/{ids[name]}/test", json={"role": roles[-1]})
            assert test.status_code == 200 and test.json()["ok"], test.json().get("error_code")
    return {n: ids[n] for n in names}


def app_url_for(names: tuple[str, ...]) -> str:
    """The lane's app with ``names`` registered (skips/fails like every integration test)."""
    url = require_service("app").rstrip("/")
    for svc in ("oracle", "mongo", "source_pg"):
        require_service(svc)
    ensure_connections(url, names)
    return url


def oracle_values(n: int = 25) -> list[str]:
    """``FULL_NAME`` and ``EMAIL`` of ``n`` seeded Oracle customers (compared, never printed)."""
    import oracledb

    from tests.fixtures.m3_services import oracle_dsn

    with oracledb.connect(user="crm", password="crm", dsn=oracle_dsn()) as conn:  # demo login
        with conn.cursor() as cur:
            cur.execute(
                "SELECT FULL_NAME, EMAIL FROM CRM.CUSTOMERS ORDER BY CUSTOMER_ID "
                "FETCH FIRST :n ROWS ONLY",
                n=n,
            )
            values = [str(v) for row in cur.fetchall() for v in row if v]
    assert len(values) >= n, "the Oracle demo table holds too few customers (run the Task 15 seed)"
    return values


def mongo_values(n: int = 25) -> list[str]:
    """Names and e-mails of ``n`` seeded MongoDB profiles (compared, never printed)."""
    import pymongo

    from tests.fixtures.m3_services import mongo_uri

    client: pymongo.MongoClient[dict[str, Any]] = pymongo.MongoClient(mongo_uri())
    try:
        docs = list(client["crm"]["profiles"].find({}, limit=n).sort("_id", 1))
    finally:
        client.close()
    values = [
        str(v)
        for d in docs
        for v in ((d.get("profile") or {}).get("fullName"), (d.get("contact") or {}).get("email"))
        if v
    ]
    assert len(values) >= n, "the MongoDB demo collection holds too few profiles"
    return values


def browser_page(url: str, errors: list[str]) -> Iterator[Any]:
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
        pg.on("pageerror", lambda exc: errors.append(exc.name) if pg.url.startswith(url) else None)
        yield pg
        browser.close()


def shoot(pg: Any, name: str) -> None:
    """Desktop and phone (dark) and desktop (light) shots; no horizontal scroll at either width."""
    for label, (w, h) in VIEWPORTS.items():
        pg.set_viewport_size({"width": w, "height": h})
        pg.wait_for_timeout(300)
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


def no_values(pg: Any, values: list[str]) -> None:
    """No source value is visible on the page (text of the body; count only, never a value)."""
    text = pg.inner_text("body")
    leaked = sum(1 for v in values if v in text)
    assert leaked == 0, f"{leaked} source values are visible"


def require_browser() -> None:
    """Skip without Playwright (the ``e2e`` extra)."""
    pytest.importorskip("playwright.sync_api")
