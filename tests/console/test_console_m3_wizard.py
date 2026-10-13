"""Console: Oracle and MongoDB in the New-job wizard against the running stack (M3 Task 18b).

Drives the wizard (mockup 06) end to end on the lane's stack (``--profile oracle --profile
sources`` with the Task 15 seed): Oracle ``CRM.CUSTOMERS`` → ``CRM_MASKED.CUSTOMERS_CONSOLE`` and
MongoDB ``crm.profiles`` (filtered by a JSON filter) → ``crm_uat.profiles_console``, each to its
evidence pack. Every page is shot at 1440 and 390 px (dark) and 1440 px (light) beside mockup 06,
into ``docs/superpowers/build/m3-shots/``. No source value is visible on any page (25 seeded
customers' names and e-mails read beforehand are absent from the page text — counts only).
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any

import pytest

from tests.console.m3_console import (
    MOCKUPS,
    SHOTS,
    app_url_for,
    browser_page,
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
    pytest.mark.slow,
    pytest.mark.timeout(900),
]
pytest.importorskip("playwright.sync_api")

NAMES = ("smoke-ora-src", "smoke-ora-tgt", "smoke-mdb-src", "smoke-mdb-tgt")
EVIDENCE_SECTIONS = (
    "1 Summary",
    "2 Configuration hashes",
    "3 Classification snapshot",
    "4 Execution",
    "5 Verification",
)
PAGE_ERRORS: list[str] = []


@pytest.fixture(scope="module")
def app_url() -> str:
    return app_url_for(NAMES)


@pytest.fixture(scope="module")
def page(app_url: str) -> Iterator[Any]:
    yield from browser_page(app_url, PAGE_ERRORS)


def _drop_target(engine: str) -> None:
    """Remove what an earlier run left in the console target of ``engine`` (a fresh create)."""
    if engine == "oracle":
        import oracledb

        from tests.fixtures.m3_services import oracle_dsn

        with oracledb.connect(user="crm_masked", password="crm_masked", dsn=oracle_dsn()) as conn:
            with conn.cursor() as cur:
                try:
                    cur.execute("DROP TABLE CRM_MASKED.CUSTOMERS_CONSOLE PURGE")
                except oracledb.DatabaseError:
                    pass  # ORA-00942: nothing to drop
        return
    import pymongo

    from tests.fixtures.m3_services import mongo_uri

    client: Any = pymongo.MongoClient(mongo_uri())
    try:
        client["crm_uat"].drop_collection("profiles_console")
    finally:
        client.close()


def _oracle_survivors() -> tuple[int, int, dict[str, int]]:
    """(retail source rows, target rows, column → target values equal to any source value)."""
    import oracledb

    from tests.fixtures.m3_services import oracle_dsn

    cols = ("FULL_NAME", "EMAIL", "MOBILE", "NATIONAL_ID", "TAX_ID")
    sel = ", ".join(cols)

    def text_lobs(cursor: Any, meta: Any) -> Any:  # a widened target column may be a CLOB
        if meta.type_code in (oracledb.DB_TYPE_CLOB, oracledb.DB_TYPE_NCLOB):
            return cursor.var(oracledb.DB_TYPE_LONG, arraysize=cursor.arraysize)
        return None

    with oracledb.connect(user="crm", password="crm", dsn=oracle_dsn()) as conn:  # demo login
        with conn.cursor() as cur:
            cur.outputtypehandler = text_lobs
            cur.execute(f"SELECT {sel} FROM CRM.CUSTOMERS WHERE SEGMENT = 'retail'")
            source = cur.fetchall()
    with oracledb.connect(user="crm_masked", password="crm_masked", dsn=oracle_dsn()) as conn:
        with conn.cursor() as cur:
            cur.outputtypehandler = text_lobs
            cur.execute(f"SELECT {sel} FROM CRM_MASKED.CUSTOMERS_CONSOLE")
            target = cur.fetchall()
    out: dict[str, int] = {}
    for i, col in enumerate(cols):
        seen = {str(r[i]).rstrip() for r in source if r[i] is not None}
        out[col] = sum(1 for r in target if r[i] is not None and str(r[i]).rstrip() in seen)
    return len(source), len(target), out


def _mongo_survivors() -> tuple[int, int, dict[str, int]]:
    """(premium source documents, target documents, path → same-document survivors)."""
    import pymongo

    from dps.layers.connectors.docpaths import values_at
    from tests.fixtures.m3_services import mongo_uri

    paths = ("$.profile.fullName", "$.contact.email", "$.contact.mobile", "$.customerId")
    client: Any = pymongo.MongoClient(mongo_uri())
    try:
        source = {d["_id"]: d for d in client["crm"]["profiles"].find({"segment": "premium"})}
        target = list(client["crm_uat"]["profiles_console"].find({}))
    finally:
        client.close()
    out = {
        p: sum(
            1
            for t in target
            for a, b in zip(values_at(source.get(t["_id"], {}), p), values_at(t, p), strict=False)
            if a is not None and a == b
        )
        for p in paths
    }
    return len(source), len(target), out


def _connect(pg: Any, engine_label: str, saved: str, name: str) -> None:
    pg.get_by_label(engine_label, exact=True).check()
    pg.get_by_label("Saved connection").select_option(label=saved)
    pg.get_by_role("button", name="Test connection").click()
    pg.get_by_text(re.compile(r"\d+ of \d+ checks passed")).wait_for(timeout=60_000)
    shoot(pg, name)


def _discover(pg: Any, name: str) -> None:
    pg.get_by_role("button", name="Next: Discover").click()
    pg.get_by_role("button", name="Run discovery").click()
    pg.get_by_text("Column heat-map").wait_for(timeout=180_000)
    assert pg.locator(".hm .cell").count() >= 8
    shoot(pg, name)


def _accept_and_target(pg: Any) -> None:
    pg.get_by_role("button", name="Accept snapshot").click()
    pg.get_by_text(re.compile(r"classification snapshot snap_")).wait_for()
    pg.get_by_role("button", name="Next: Target and write mode").click()
    pg.get_by_label(re.compile("Copy to a table")).check()


def _run_to_evidence(pg: Any, ticket: str, name: str, values: list[str]) -> None:
    pg.get_by_role("button", name="Next: Review and run").click()
    pg.get_by_label("Ticket").fill(ticket)
    shoot(pg, f"{name}-5-review")
    pg.get_by_role("button", name="Run job").click()
    pg.get_by_text(re.compile("shown once")).wait_for(
        timeout=60_000
    )  # no shot: the key is on screen
    pg.get_by_role("link", name="Open job").click()
    pg.get_by_text("Run · partitions").wait_for()
    pg.get_by_role("link", name="Evidence pack").first.wait_for(timeout=300_000)
    shoot(pg, f"{name}-detail")
    no_values(pg, values)
    pg.get_by_role("link", name="Evidence pack").first.click()
    for section in EVIDENCE_SECTIONS:
        pg.get_by_text(section).first.wait_for()
    shoot(pg, f"{name}-evidence")
    no_values(pg, values)


def test_engine_cards_are_enabled(page: Any, app_url: str) -> None:
    page.goto(f"{app_url}/data-sources/jobs/new")
    group = page.get_by_role("radiogroup", name="Database engine")
    for label in ("PostgreSQL", "Oracle", "MongoDB"):
        assert group.get_by_label(label, exact=True).is_enabled(), label
    assert group.get_by_text("M3", exact=True).count() == 0
    page.get_by_label("MongoDB", exact=True).check()
    page.get_by_role("button", name="New connection").click()
    for field in ("Database", "Replica set"):
        page.get_by_role("textbox", name=re.compile(f"^{field}")).wait_for()
    page.get_by_label("Oracle", exact=True).check()
    page.get_by_role("textbox", name=re.compile("^Service name")).wait_for()
    assert page.get_by_role("textbox", name=re.compile("^Database")).count() == 0
    shoot(page, "06-wizard-oracle-1-new-connection")
    assert PAGE_ERRORS == []


def test_oracle_wizard_discovers_and_runs(page: Any, app_url: str) -> None:
    values = oracle_values()
    _drop_target("oracle")
    page.goto(f"{app_url}/data-sources/jobs/new")
    _connect(page, "Oracle", "smoke-ora-src", "06-wizard-oracle-1-connect")
    page.get_by_text(re.compile(r"Oracle Database 23")).first.wait_for()
    # 2 Select data: owners of every non-Oracle-maintained user, a WHERE with a bound literal
    page.get_by_role("button", name="Next: Select data").click()
    page.get_by_label("Owner").select_option("CRM")
    page.get_by_label("Table", exact=True).select_option("CUSTOMERS")
    page.get_by_label(re.compile("^WHERE expression")).fill("SEGMENT = 'retail'")
    page.get_by_role("button", name="Check").click()
    page.get_by_text(re.compile(r"rows match")).wait_for(timeout=60_000)
    shoot(page, "06-wizard-oracle-2-select")
    _discover(page, "06-wizard-oracle-3-discover")
    no_values(page, values)
    _accept_and_target(page)
    page.get_by_label("Target connection").select_option(label="smoke-ora-tgt")
    page.get_by_label("Destination schema").fill("CRM_MASKED")
    page.get_by_label("Destination table").fill("CUSTOMERS_CONSOLE")
    shoot(page, "06-wizard-oracle-4-target")
    _run_to_evidence(page, "CHG-30301", "06-wizard-oracle", values)
    # source vs target, counts only: every retail row written, no protected value survives
    source, target, survivors = _oracle_survivors()
    print("M3_WIZARD_ORACLE", source, target, survivors)  # counts only (pytest -s)
    assert source > 0 and target == source, (source, target)
    assert survivors == dict.fromkeys(survivors, 0), survivors
    assert PAGE_ERRORS == []


def test_mongo_wizard_filters_discovers_and_runs(page: Any, app_url: str) -> None:
    values = mongo_values()
    _drop_target("mongo")
    page.goto(f"{app_url}/data-sources/jobs/new")
    _connect(page, "MongoDB", "smoke-mdb-src", "06-wizard-mongo-1-connect")
    page.get_by_text("transactions: yes").wait_for()
    # 2 Select data: no owner, the connection's database, a collection and a JSON filter
    page.get_by_role("button", name="Next: Select data").click()
    assert page.get_by_label("Owner").count() == 0
    page.get_by_text("Database: crm").wait_for()
    page.get_by_label("Collection").select_option("profiles")
    flt = page.get_by_label("Filter (JSON)")
    flt.fill('{"segment": "premium"}')
    page.get_by_role("button", name="Check").click()
    page.get_by_text(re.compile(r"documents match")).wait_for(timeout=60_000)
    # server-side JavaScript: refused on the page, before any request (the filter is not echoed)
    flt.fill('{"$where": "1"}')
    page.get_by_role("button", name="Check").click()
    page.get_by_text(re.compile(r"\$where runs JavaScript")).first.wait_for()
    assert '"$where": "1"' not in page.inner_text("body")
    # an operator outside the allow-list: refused by the server (422), message shown, no echo
    flt.fill('{"$jsonSchema": {"required": ["kycSentinel42"]}}')
    page.get_by_role("button", name="Check").click()
    page.get_by_text(re.compile("selection_operator_denied")).first.wait_for(timeout=30_000)
    assert "kycSentinel42" not in page.inner_text(
        "body"
    )  # the rail shows <bound>, the callout no text
    shoot(page, "06-wizard-mongo-2-denied")
    flt.fill('{"segment": "premium"}')
    page.get_by_role("button", name="Check").click()
    page.get_by_text(re.compile(r"documents match")).wait_for(timeout=60_000)
    shoot(page, "06-wizard-mongo-2-select")
    _discover(page, "06-wizard-mongo-3-discover")
    cells = page.locator(".hm .cell .cn").all_inner_texts()
    assert any(c.startswith("profile.") for c in cells), "no profile.* path in the heat-map"
    no_values(page, values)
    _accept_and_target(page)
    page.get_by_label("Target connection").select_option(label="smoke-mdb-tgt")
    assert page.get_by_label("Destination schema").count() == 0
    page.get_by_label("Destination collection").fill("profiles_console")
    shoot(page, "06-wizard-mongo-4-target")
    _run_to_evidence(page, "CHG-30302", "06-wizard-mongo", values)
    # the pack states the filter that ran, as its value-free template, never "all rows"
    selection = page.locator(".kv .s2", has_text="Selection").first.inner_text()
    assert '{"segment":"?"}' in selection and "1 bound literal" in selection, selection
    assert "all rows" not in selection and "all documents" not in selection, selection
    assert "premium" not in selection
    page.get_by_text(re.compile(r"DPS read [\d,]+ documents from MongoDB")).wait_for()
    # source vs target, counts only: every premium document written, no protected leaf survives
    source, target, survivors = _mongo_survivors()
    print("M3_WIZARD_MONGO", source, target, survivors)  # counts only (pytest -s)
    assert source > 0 and target == source, (source, target)
    assert survivors == dict.fromkeys(survivors, 0), survivors
    assert PAGE_ERRORS == []
    mockup(page, "06-connect-wizard.html", "06-wizard")
    assert (MOCKUPS / "06-connect-wizard.html").is_file() and SHOTS.is_dir()
