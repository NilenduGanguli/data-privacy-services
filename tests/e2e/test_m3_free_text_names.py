"""The first name the CRM notes repeat never reaches a target (S1-M3 post-merge verification r1,
engines B2 — D-A for synthesize, and the same root cause in obfuscate, N1).

The M3 demo seed writes ``NOTES`` / ``notes`` as "KYC call with <first name>; follow up at <e-mail>
or <phone>. Prefers branch in <city>. …" (``tools/seed_model.py``). Without NER no detector finds
a first name, and the row-value spans of the M3 merge matched only a planned column's *whole*
value: 20 000 / 20 000 synthetic notes on Oracle and MongoDB kept their source row's real first
name, linkable through the kept ``LAST_CONTACT_AT`` / balance. Each case runs the demo shape
(600 customers of the shared customer space) through the in-process pipeline — discover, accept
(default policy, no NER), run into a table, finalize — and counts, row by row, target notes that
still hold the row's own source first name (aligned on the kept ``LAST_CONTACT_AT`` on Oracle and
the kept account balance on MongoDB). Counts only; no value is printed.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from dps.auth import LOCAL_PRINCIPAL
from dps.connections import create_connection
from dps.jobs import JobState
from dps.jobs_models import ConnectionParams, ConnectionSpec, ObjectRef
from dps.layers.connectors.evidence import get_evidence
from dps.layers.connectors.submit import submit_run
from dps.worker import HandlerContext
from tests.connectors.helpers import discover_and_accept, drive
from tests.connectors.test_mongo_pipeline import seed_profiles
from tests.connectors.test_run_modes import run_spec
from tests.e2e.test_m3_resume_after_kill import ORACLE_DDL, _oracle_rows, _register_oracle
from tests.fixtures.m3_services import OracleTestSchema, mongo_uri
from tests.fixtures.sources import write_ref

pytestmark = [pytest.mark.integration, pytest.mark.db, pytest.mark.timeout(900)]
TENANT = "t1"
ROWS = 600
MODES = ["synthesize", "obfuscate"]


def _first_name(full_name: str) -> str:
    return full_name.split(" ")[0]


def _keeps(first: str, notes: str | None) -> bool:
    """The note holds ``first`` as a whole word (any case) — the demo name slot."""
    if len(first) < 3 or not notes:
        return False
    return re.search(rf"(?<!\w){re.escape(first)}(?!\w)", notes, re.IGNORECASE) is not None


async def _run(
    ctx: HandlerContext,
    conn_id: str,
    obj: ObjectRef,
    snap_id: str,
    target: dict[str, Any],
    mode: str,
) -> Any:  # noqa: E501
    sub = await submit_run(
        run_spec(conn_id, obj, snap_id, target, mode=mode),
        tenant_id=TENANT,
        principal=LOCAL_PRINCIPAL,
        settings=ctx.settings,
        kek=ctx.kek,
    )
    return await drive(ctx, TENANT, sub.job.id, loops=4000)


async def _verified(job: Any) -> None:
    ev = await get_evidence(TENANT, job.progress.evidence_id or "")
    assert ev is not None and ev.pack.verification["passed"]


# -- Oracle: CRM.CUSTOMERS --------------------------------------------------------------------------


@pytest.fixture
def _oracle_clean(oracle_admin: Any, oracle_schema: OracleTestSchema) -> Iterator[None]:
    from tests.connectors.oracle_fixtures import drop_all

    drop_all(oracle_admin, oracle_schema.owner)
    yield
    drop_all(oracle_admin, oracle_schema.owner)


def _load_customers(conn: Any, owner: str) -> None:
    import oracledb

    from tools.seed_model import seed_customers
    from tools.seed_oracle import customer_row

    rows = [customer_row(c) for c in seed_customers(ROWS, 7)]
    cols = list(rows[0])
    values = ", ".join(
        "TO_TIMESTAMP(:LAST_CONTACT_AT, 'YYYY-MM-DD\"T\"HH24:MI:SS.FF9')"
        if c == "LAST_CONTACT_AT"
        else f":{c}"
        for c in cols
    )
    with conn.cursor() as cur:
        cur.execute(ORACLE_DDL.format(o=owner, t="CUSTOMERS", identity=""))
        cur.setinputsizes(NOTES=oracledb.DB_TYPE_CLOB)
        cur.executemany(
            f'INSERT INTO "{owner}"."CUSTOMERS" ({", ".join(cols)}) VALUES ({values})', rows
        )
    conn.commit()


@pytest.mark.oracle
@pytest.mark.usefixtures("_oracle_clean")
@pytest.mark.parametrize("mode", MODES)
async def test_oracle_notes_keep_no_source_first_name(
    handler_ctx: HandlerContext, oracle_conn: Any, oracle_schema: OracleTestSchema, mode: str
) -> None:
    owner = oracle_schema.owner
    _load_customers(oracle_conn, owner)
    conn = await _register_oracle(handler_ctx, oracle_schema)
    obj = ObjectRef(owner=owner, name="CUSTOMERS")
    snap = await discover_and_accept(handler_ctx, TENANT, conn.id, obj)
    target = {
        "write_mode": "table",
        "connection_id": conn.id,
        "object": {"owner": owner, "name": "CUSTOMERS_OUT"},
    }
    job = await _run(handler_ctx, conn.id, obj, snap.id, target, mode)
    assert job.state == JobState.succeeded, (job.error_code, job.error)
    await _verified(job)
    # column order of the demo table: 1 FULL_NAME, 13 NOTES, 15 LAST_CONTACT_AT (kept, unique)
    source = _oracle_rows(oracle_conn, owner, "CUSTOMERS")
    out = _oracle_rows(oracle_conn, owner, "CUSTOMERS_OUT")
    by_ts = {r[15]: r for r in source}
    pairs = [(by_ts[r[15]], r) for r in out if r[15] in by_ts]
    assert len(pairs) == ROWS
    control = sum(_keeps(_first_name(s[1]), s[13]) for s, _ in pairs)
    assert control >= ROWS * 0.95  # the probe sees the name slot in the source notes
    kept = sum(_keeps(_first_name(s[1]), t[13]) for s, t in pairs)
    assert kept == 0, kept


# -- MongoDB: crm.profiles --------------------------------------------------------------------------


@pytest.mark.mongo
@pytest.mark.parametrize("mode", MODES)
async def test_mongo_notes_keep_no_source_first_name(
    handler_ctx: HandlerContext, mongo_client: Any, mongo_db: str, mode: str
) -> None:
    docs = seed_profiles(mongo_client, mongo_db, "profiles", ROWS)
    write_ref(Path(handler_ctx.settings.secret_dir), "m3/names-mongo", {"uri": mongo_uri()})
    conn = await create_connection(
        TENANT,
        ConnectionSpec(
            name="names-mongo",
            engine="mongo",
            params=ConnectionParams(database=mongo_db),
            secret_ref="m3/names-mongo",
            roles={"source", "target"},
        ),
        settings=handler_ctx.settings,
        kek=handler_ctx.kek,
        created_by="test",
    )
    obj = ObjectRef(name="profiles")
    snap = await discover_and_accept(handler_ctx, TENANT, conn.id, obj)
    target = {"write_mode": "table", "connection_id": conn.id, "object": {"name": "profiles_out"}}
    job = await _run(handler_ctx, conn.id, obj, snap.id, target, mode)
    assert job.state == JobState.succeeded, (job.error_code, job.error)
    await _verified(job)

    def balance(d: dict[str, Any]) -> str:  # kept by every run, unique per demo customer
        return str(d["accounts"][0]["balance"])

    by_balance = {balance(d): d for d in docs}
    out = list(mongo_client[mongo_db]["profiles_out"].find({}))
    pairs = [(by_balance[balance(d)], d) for d in out if balance(d) in by_balance]
    assert len(pairs) == ROWS
    control = sum(_keeps(_first_name(s["profile"]["fullName"]), s["notes"]) for s, _ in pairs)
    assert control >= ROWS * 0.95
    kept = sum(_keeps(_first_name(s["profile"]["fullName"]), t.get("notes")) for s, t in pairs)
    assert kept == 0, kept
