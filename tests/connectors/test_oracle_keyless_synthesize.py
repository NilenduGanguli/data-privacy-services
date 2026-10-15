"""A key-less Oracle table synthesizes (S1-M3 post-merge verification r1, engines B1).

``CRM.ADDRESS_HISTORY`` has no key: the connector pages it on the ``ROWID`` pseudo key, which is
never pseudonymised because it is never written. The D-A prepare guard still counted it among the
keys a synthesize run would copy, so every such run failed ``leakage`` with nothing written. Through
the in-process pipeline (discover → accept → synthesize → finalize), on a copy of the demo table
in the session's test owner: the run succeeds, writes no ``ROWID`` (no column in a table, none in
a file), and the customer key is pseudonymised into ``[10⁹, 2·10⁹)`` with no source key written
(counts only).
"""

from __future__ import annotations

import io
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq
import pytest

from dps.auth import LOCAL_PRINCIPAL
from dps.jobs import JobState
from dps.jobs_models import ObjectRef
from dps.layers.connectors.submit import submit_run
from dps.worker import HandlerContext
from tests.connectors.helpers import discover_and_accept, drive, register_pvc
from tests.connectors.oracle_fixtures import drop_all
from tests.connectors.test_run_modes import run_spec
from tests.e2e.test_m3_resume_after_kill import _register_oracle
from tests.fixtures.m3_services import OracleTestSchema

pytestmark = [pytest.mark.db, pytest.mark.integration, pytest.mark.oracle]
CUSTOMERS = 750  # two address rows each
DDL = """CREATE TABLE "{o}"."ADDRESS_HISTORY" (CUSTOMER_ID NUMBER(12) NOT NULL,
  ADDR_LINE1 VARCHAR2(200 CHAR), CITY VARCHAR2(80 CHAR), PIN_CODE VARCHAR2(10), VALID_FROM DATE)"""
BAND = range(10**9, 2 * 10**9)


@pytest.fixture(autouse=True)
def _clean(oracle_admin: Any, oracle_schema: OracleTestSchema) -> Iterator[None]:
    drop_all(oracle_admin, oracle_schema.owner)
    yield
    drop_all(oracle_admin, oracle_schema.owner)


def _load(conn: Any, owner: str) -> list[dict[str, Any]]:
    from tools.seed_model import seed_customers
    from tools.seed_oracle import address_rows

    rows = [r for c in seed_customers(CUSTOMERS, 7) for r in address_rows(c)]
    cols = list(rows[0])
    with conn.cursor() as cur:
        cur.execute(DDL.format(o=owner))
        cur.executemany(
            f'INSERT INTO "{owner}"."ADDRESS_HISTORY" ({", ".join(cols)}) '
            f"VALUES ({', '.join(':' + c for c in cols)})",
            rows,
        )
    conn.commit()
    return rows


def _columns(conn: Any, owner: str, table: str) -> list[str]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT column_name FROM all_tab_columns WHERE owner = :o AND table_name = :t",
            {"o": owner, "t": table},
        )
        return sorted(r[0] for r in cur.fetchall())


def _check(rows: list[dict[str, Any]], source: list[dict[str, Any]]) -> None:
    """Counts only: every customer key pseudonymised into the band, none a source key, no source
    address line copied."""
    assert len(rows) == len(source)
    keys = [int(r["CUSTOMER_ID"]) for r in rows]
    assert sum(k in BAND for k in keys) == len(rows)
    assert not {int(s["CUSTOMER_ID"]) for s in source} & set(keys)
    assert not {s["ADDR_LINE1"] for s in source} & {r["ADDR_LINE1"] for r in rows}


async def _synthesize(
    ctx: HandlerContext, schema: OracleTestSchema, conn: Any, target: dict[str, Any]
) -> tuple[Any, list[dict[str, Any]], Any]:
    source = _load(conn, schema.owner)
    registered = await _register_oracle(ctx, schema)
    obj = ObjectRef(owner=schema.owner, name="ADDRESS_HISTORY")
    snap = await discover_and_accept(ctx, "t1", registered.id, obj)
    assert snap.shape.key == ["ROWID"] and snap.shape.key_source == "rowid"
    target = {**target}
    if target["write_mode"] == "table":
        target["connection_id"] = registered.id
    sub = await submit_run(
        run_spec(registered.id, obj, snap.id, target, mode="synthesize"),
        tenant_id="t1",
        principal=LOCAL_PRINCIPAL,
        settings=ctx.settings,
        kek=ctx.kek,
    )
    job = await drive(ctx, "t1", sub.job.id, loops=3000)
    return job, source, registered


async def test_a_keyless_oracle_table_synthesizes_into_a_table(
    handler_ctx: HandlerContext,
    oracle_conn: Any,
    oracle_schema: OracleTestSchema,
) -> None:
    owner = oracle_schema.owner
    target = {"write_mode": "table", "object": {"owner": owner, "name": "ADDR_SYN"}}
    job, source, _ = await _synthesize(handler_ctx, oracle_schema, oracle_conn, target)
    assert job.state == JobState.succeeded, (job.error_code, job.error)
    assert job.progress.rows_written == len(source)
    # the target holds the table's own columns: the ROWID locator is never written
    assert _columns(oracle_conn, owner, "ADDR_SYN") == [
        "ADDR_LINE1",
        "CITY",
        "CUSTOMER_ID",
        "PIN_CODE",
        "VALID_FROM",
    ]
    with oracle_conn.cursor() as cur:
        cur.execute(f'SELECT CUSTOMER_ID, ADDR_LINE1 FROM "{owner}"."ADDR_SYN"')
        rows = [{"CUSTOMER_ID": r[0], "ADDR_LINE1": r[1]} for r in cur.fetchall()]
    _check(rows, source)


async def test_a_keyless_oracle_table_synthesizes_into_files_without_its_rowid(
    handler_ctx: HandlerContext,
    oracle_conn: Any,
    oracle_schema: OracleTestSchema,
) -> None:
    pvc = await register_pvc("t1", handler_ctx, base_path="keyless")
    target = {
        "write_mode": "files",
        "connection_id": pvc.id,
        "files": {"format": "parquet", "prefix": "addr"},
    }
    job, source, _ = await _synthesize(handler_ctx, oracle_schema, oracle_conn, target)
    assert job.state == JobState.succeeded, (job.error_code, job.error)
    parts = sorted(Path(handler_ctx.settings.export_root).rglob("part-*.parquet"))
    assert parts
    tables = [pq.read_table(io.BytesIO(p.read_bytes())) for p in parts]
    assert all(
        t.schema.names == ["CUSTOMER_ID", "ADDR_LINE1", "CITY", "PIN_CODE", "VALID_FROM"]
        for t in tables
    )
    rows = [r for t in tables for r in t.to_pylist()]
    _check(rows, source)
