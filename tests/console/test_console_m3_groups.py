"""Console: the job-group card and the consistency panel against the running stack (M3 Task 18).

Builds the spec §9 demo through the API on the lane's stack — one job group, three runs under its
key (PostgreSQL ``bank.customers``, MongoDB ``crm.profiles``, Oracle ``CRM.CUSTOMERS``, one
customer space from the Task 15 seed) into ``bank_uat`` / ``crm_uat`` / ``CRM_MASKED`` — then drives
the console: the Jobs page's group card (mockup 07: key node, wires, three system cards, **Run
consistency check** → verdict 3 / 3 and join coverage), the ``.grp`` badges, and the Oracle job's
detail page (mockup 03: crumb, subtitle, **Open consistency check**, the "Cross-system joins"
tile). Shots at 1440 / 390 px (dark) and 1440 px (light) into ``reports/console/m3-shots/``
(``tests/console/m3_console.py``); no seeded value is visible on any page (counts only).
"""

from __future__ import annotations

import re
import secrets
from collections.abc import Iterator
from typing import Any

import pytest

from tests.console.m3_console import (
    api,
    app_url_for,
    browser_page,
    ensure_connections,
    mockup,
    mongo_values,
    no_values,
    oracle_values,
    shoot,
)

pytestmark = [
    pytest.mark.browser,
    pytest.mark.integration,
    pytest.mark.oracle,
    pytest.mark.mongo,
    pytest.mark.pg_source,
    pytest.mark.slow,
    pytest.mark.timeout(1500),
]
pytest.importorskip("playwright.sync_api")

NAMES = (
    "smoke-ora-src",
    "smoke-ora-tgt",
    "smoke-mdb-src",
    "smoke-mdb-tgt",
    "bank-pg-ro",
    "bank-uat",
)
ROWS = 2400  # Oracle and MongoDB; PostgreSQL's bank holds the same ids as a prefix (Task 15)
PURPOSE = {"ticket": "CHG-48213", "legal_basis": "Legitimate interest — UAT provisioning"}
PAGE_ERRORS: list[str] = []


@pytest.fixture(scope="module")
def app_url() -> str:
    return app_url_for(NAMES)


@pytest.fixture(scope="module")
def page(app_url: str) -> Iterator[Any]:
    yield from browser_page(app_url, PAGE_ERRORS)


def _wait(c: Any, job_id: str, timeout: float = 900.0) -> dict[str, Any]:
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = c.get(f"/api/v1/jobs/{job_id}").json()
        if job["state"] in ("succeeded", "failed", "dead", "canceled"):
            return dict(job)
        time.sleep(2)
    raise AssertionError(f"job {job_id} did not finish in {timeout} s")


def _discover_accept(c: Any, conn_id: str, obj: dict[str, Any]) -> str:
    r = c.post("/api/v1/jobs/discover", json={"connection_id": conn_id, "objects": [obj]})
    assert r.status_code == 202, r.json().get("code")
    job = _wait(c, r.json()["job"]["id"], 300)
    assert job["state"] == "succeeded", job.get("error_code")
    [snap] = job["progress"]["snapshot_ids"]
    a = c.post(f"/api/v1/snapshots/{snap}/accept")
    assert a.status_code == 200, a.json().get("code")
    return str(snap)


def _drop_targets(suffix: str) -> None:
    """Fresh targets for this run (earlier runs used other suffixes; nothing else is touched)."""
    import oracledb

    from tests.fixtures.m3_services import oracle_dsn

    with oracledb.connect(user="crm_masked", password="crm_masked", dsn=oracle_dsn()) as conn:
        with conn.cursor() as cur:
            try:
                cur.execute(f"DROP TABLE CRM_MASKED.CUSTOMERS_GRP_{suffix.upper()} PURGE")
            except oracledb.DatabaseError:
                pass  # ORA-00942: nothing to drop


@pytest.fixture(scope="module")
def group(app_url: str) -> dict[str, Any]:
    """A three-member group whose runs all succeeded: {group, jobs: {engine: job id}}."""
    from tests.fixtures.m3_services import mongo_uri, oracle_dsn
    from tools.seed_mongo import seed_mongo
    from tools.seed_oracle import seed_oracle

    seed_oracle(oracle_dsn(), ROWS, 7)
    seed_mongo(mongo_uri(), ROWS, 7)
    suffix = secrets.token_hex(3)
    _drop_targets(suffix)
    ids = ensure_connections(app_url, NAMES)
    with api(app_url) as c:
        made = c.post("/api/v1/job-groups", json={"name": f"crm-uat-refresh-{suffix}"})
        assert made.status_code == 201, made.json().get("code")
        grp, key = made.json()["group"], made.json()["key_material"]  # memory only
        members = [  # submitted in this order: the newest (Oracle, ROWS rows) anchors the check
            ("postgres", "bank-pg-ro", {"owner": "bank", "name": "customers"},
             "bank-uat", {"owner": "bank_uat", "name": f"customers_grp_{suffix}"}),
            ("mongo", "smoke-mdb-src", {"owner": None, "name": "profiles"},
             "smoke-mdb-tgt", {"owner": None, "name": f"profiles_grp_{suffix}"}),
            ("oracle", "smoke-ora-src", {"owner": "CRM", "name": "CUSTOMERS"},
             "smoke-ora-tgt", {"owner": "CRM_MASKED", "name": f"CUSTOMERS_GRP_{suffix.upper()}"}),
        ]  # fmt: skip
        jobs: dict[str, str] = {}
        for engine, src, obj, tgt, tobj in members:
            snap = _discover_accept(c, ids[src], obj)
            body = {
                "mode": "obfuscate", "snapshot_id": snap, "group_id": grp["id"], "purpose": PURPOSE,
                "source": {"connection_id": ids[src], "object": obj, "parallelism": 2},
                "target": {"write_mode": "table", "connection_id": ids[tgt], "object": tobj,
                           "create_if_missing": True, "table_load": "truncate_insert"},
                "consistency": {"scope": "key", "key_material": key},
            }  # fmt: skip
            r = c.post(
                "/api/v1/jobs/run", json=body, headers={"Idempotency-Key": secrets.token_hex(16)}
            )
            assert r.status_code == 202, r.json().get("code")
            jobs[engine] = r.json()["job"]["id"]
        for engine, jid in jobs.items():
            job = _wait(c, jid)
            assert job["state"] == "succeeded", (engine, job.get("error_code"))
    return {"group": grp, "jobs": jobs}


def test_group_card_and_consistency_check(page: Any, app_url: str, group: dict[str, Any]) -> None:
    values = oracle_values() + mongo_values()
    gid, name = group["group"]["id"], group["group"]["name"]
    page.goto(f"{app_url}/data-sources?group={gid}#group")
    card = page.locator("#group")
    card.get_by_text(f"Job group · {name}").wait_for(timeout=30_000)
    assert card.locator(".sys").count() == 3
    assert card.locator("svg.wires").count() == 1
    chip = card.locator(".keynode .chip").inner_text()
    assert chip.startswith("entity-scoped: CUSTOMER_ID"), chip
    for engine in ("ORA", "MDB", "PG"):
        assert card.locator(".sys .eb", has_text=engine).count() == 1, engine
    card.get_by_text("not checked yet").wait_for()
    card.get_by_role("button", name="Run consistency check").click()
    verdict = card.get_by_test_id("verdict")
    verdict.wait_for(timeout=120_000)
    assert verdict.locator(".chip").inner_text() == "passed"
    assert re.search(r"3 / 3\s*consistent", verdict.locator(".big").inner_text())
    assert verdict.locator(".pair").count() == 3
    assert verdict.locator(".pair .ok.bad").count() == 0
    coverage = card.get_by_test_id("coverage")
    assert coverage.locator("table").first.locator("tbody tr").count() == 3
    coverage.get_by_text(re.compile(r"prefixed · \d+-char prefix")).first.wait_for()
    coverage.get_by_text(re.compile(r"stores the id with a \d+-character prefix")).wait_for()
    no_values(page, values)
    shoot(page, "07-jobs-group")
    # the stored verdict survives a reload (last_check on the group)
    page.reload()
    page.locator("#group").get_by_test_id("verdict").wait_for(timeout=30_000)
    # jobs table: every member carries the key-icon badge with the group name
    assert page.locator("table.jobs .grp", has_text=name).count() >= 3
    # "Add a job to this group" opens the wizard with step 5's key choice on this group
    page.locator("#group").get_by_role("link", name="Add a job to this group").click()
    page.get_by_test_id("group-preset").get_by_text(name).wait_for(timeout=30_000)
    assert PAGE_ERRORS == []
    mockup(page, "07-jobs.html", "07-jobs")


def test_job_detail_of_a_grouped_job(page: Any, app_url: str, group: dict[str, Any]) -> None:
    values = oracle_values()
    name = group["group"]["name"]
    page.goto(f"{app_url}/data-sources/jobs/{group['jobs']['oracle']}")
    page.get_by_role("link", name=re.compile(f"Job group {name}")).wait_for(timeout=30_000)
    page.get_by_text(re.compile(r"consistency key shared with")).wait_for()
    head = page.locator(".page-head p").first.inner_text()
    assert "profiles_grp_" in head and "customers_grp_" in head, "subtitle names the other targets"
    tile = page.get_by_test_id("joins-box")
    tile.wait_for()
    assert re.search(r"3 / 3\s*consistent", tile.inner_text()), "joins tile reads the last check"
    no_values(page, values)
    shoot(page, "03-job-detail-group")
    page.get_by_role("link", name="Open consistency check").click()
    page.locator("#group").get_by_text(f"Job group · {name}").wait_for(timeout=30_000)
    assert page.url.endswith("#group")
    assert PAGE_ERRORS == []
    mockup(page, "03-data-sources.html", "03-job-detail")


def test_new_group_dialog_hands_out_the_key_once(page: Any, app_url: str) -> None:
    """New job group: the key and seed downloads hold the group's material, Done waits for the
    "stored" box, and nothing reaches the browser's storage."""
    import json

    name = f"m3-dialog-{secrets.token_hex(3)}"
    page.goto(f"{app_url}/data-sources")
    page.get_by_role("button", name="New job group").click()
    dialog = page.get_by_role("dialog")
    dialog.get_by_label("Group name").fill(name)
    dialog.get_by_role("button", name="Create").click()
    dialog.get_by_text("Key id").wait_for(timeout=30_000)
    done = dialog.get_by_role("button", name="Done")
    assert done.is_disabled()
    kid = dialog.locator("dd").first.inner_text().strip()
    with page.expect_download() as got:
        dialog.get_by_role("button", name="Download key (.dpskey)").click()
    key_file = json.loads(open(got.value.path(), encoding="utf-8").read())  # noqa: SIM115
    assert got.value.suggested_filename == f"{name}.dpskey"
    assert key_file["key_material"]["kid"] == kid == key_file["group"]["kid"]
    assert "synthesis_seed" not in key_file
    with page.expect_download() as got_seed:
        dialog.get_by_role("button", name="Download seed (.dpsseed)").click()
    seed_file = json.loads(open(got_seed.value.path(), encoding="utf-8").read())  # noqa: SIM115
    assert set(seed_file) == {"group", "synthesis_seed"}
    stored = page.evaluate("JSON.stringify(Object.assign({}, localStorage, sessionStorage))")
    assert "key_material" not in stored and "synthesis_seed" not in stored and kid not in stored
    dialog.get_by_label("I have stored the key and the seed").check()
    done.click()
    page.locator("#group").get_by_text(f"Job group · {name}").wait_for(timeout=30_000)
    assert page.get_by_role("dialog").count() == 0
    assert PAGE_ERRORS == []
