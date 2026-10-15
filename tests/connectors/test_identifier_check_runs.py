"""A synthesize run's identifier check covers every written row on Oracle and MongoDB (S1-M3
post-merge verification r3, engines B1), end to end in process: discover → accept → synthesize
into a created table → finalize.

The check's read page (``CHECK_BATCH``) is made small, so each side spans many pages, as 20 000
rows span four real pages. The evidence pack's counts must be what the target holds: ``checked``
= the column's values in the target (non-null cells, or leaves of a wildcard path), ``rows_read``
= the target's and the source's rows, ``scope`` ``whole_table``. Before the fix each column said
``checked`` = one page. Counts only.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from dps.auth import LOCAL_PRINCIPAL
from dps.jobs import JobState
from dps.jobs_models import ObjectRef
from dps.layers.connectors import identifier_check
from dps.layers.connectors.docpaths import values_at
from dps.layers.connectors.evidence import get_evidence
from dps.layers.connectors.submit import submit_run
from dps.worker import HandlerContext
from tests.connectors.helpers import discover_and_accept, drive
from tests.connectors.oracle_fixtures import drop_all
from tests.connectors.test_run_modes import run_spec
from tests.fixtures.m3_services import OracleTestSchema

pytestmark = [pytest.mark.db, pytest.mark.integration]
PAGE = 128


async def _synthesize(
    ctx: HandlerContext, conn_id: str, obj: ObjectRef, snap_id: str, target: dict[str, Any]
) -> dict[str, Any]:
    """Run, assert success, return the pack's identifier check."""
    sub = await submit_run(
        run_spec(conn_id, obj, snap_id, target, mode="synthesize"),
        tenant_id="t1",
        principal=LOCAL_PRINCIPAL,
        settings=ctx.settings,
        kek=ctx.kek,
    )
    job = await drive(ctx, "t1", sub.job.id, loops=4000)
    assert job.state == JobState.succeeded, (job.error_code, job.error)
    ev = await get_evidence("t1", job.progress.evidence_id or "")
    assert ev is not None
    check: dict[str, Any] = ev.pack.verification["post_sample"]["identifier_check"]
    return check


@pytest.fixture
def _ora_clean(oracle_admin: Any, oracle_schema: OracleTestSchema) -> Iterator[None]:
    drop_all(oracle_admin, oracle_schema.owner)
    yield
    drop_all(oracle_admin, oracle_schema.owner)


@pytest.mark.oracle
@pytest.mark.usefixtures("_ora_clean")
async def test_an_oracle_synthesize_checks_every_written_row(
    handler_ctx: HandlerContext,
    oracle_admin: Any,
    oracle_conn: Any,
    oracle_schema: OracleTestSchema,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.e2e.test_m3_resume_after_kill import ROWS, _oracle_load, _register_oracle

    monkeypatch.setattr(identifier_check, "CHECK_BATCH", PAGE)
    owner = oracle_schema.owner
    _oracle_load(oracle_conn, owner, "CUSTOMERS", identity=False)  # the demo's CRM.CUSTOMERS
    conn = await _register_oracle(
        handler_ctx, oracle_schema, ref="m3/idcheck-oracle-owner", name="idcheck-oracle"
    )
    obj = ObjectRef(owner=owner, name="CUSTOMERS")
    snap = await discover_and_accept(handler_ctx, "t1", conn.id, obj)
    target = {
        "write_mode": "table",
        "connection_id": conn.id,
        "object": {"owner": owner, "name": "CUSTOMERS_SYN"},
        "create_if_missing": True,
    }
    check = await _synthesize(handler_ctx, conn.id, obj, snap.id, target)
    assert ROWS > 3 * PAGE
    assert {"NATIONAL_ID", "TAX_ID"} <= set(check["columns"])
    for column, counts in check["columns"].items():
        with oracle_admin.cursor() as cur:
            cur.execute(f'SELECT COUNT("{column}") FROM "{owner}"."CUSTOMERS_SYN"')
            held = int(cur.fetchone()[0])
        assert counts["checked"] == held > 0, (column, counts["checked"], held)
    assert check["scope"] == "whole_table"
    assert check["rows_read"] == {"target": ROWS, "source": ROWS}


@pytest.mark.mongo
async def test_a_mongo_synthesize_checks_every_written_document(
    handler_ctx: HandlerContext,
    mongo_client: Any,
    mongo_db: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dps.connections import create_connection
    from dps.jobs_models import ConnectionParams, ConnectionSpec
    from tests.connectors.test_mongo_pipeline import ADDRESS_REVIEW, seed_profiles
    from tests.fixtures.m3_services import mongo_uri
    from tests.fixtures.sources import write_ref

    rows = 600
    monkeypatch.setattr(identifier_check, "CHECK_BATCH", PAGE)
    seed_profiles(mongo_client, mongo_db, "profiles", rows)
    write_ref(Path(handler_ctx.settings.secret_dir), "m3/idcheck-mongo", {"uri": mongo_uri()})
    conn = await create_connection(
        "t1",
        ConnectionSpec(
            name="idcheck-mongo",
            engine="mongo",
            params=ConnectionParams(database=mongo_db),
            secret_ref="m3/idcheck-mongo",
            roles={"source", "target"},
        ),
        settings=handler_ctx.settings,
        kek=handler_ctx.kek,
        created_by="test",
    )
    obj = ObjectRef(name="profiles")
    snap = await discover_and_accept(handler_ctx, "t1", conn.id, obj, overrides=ADDRESS_REVIEW)
    target = {"write_mode": "table", "connection_id": conn.id, "object": {"name": "profiles_syn"}}
    check = await _synthesize(handler_ctx, conn.id, obj, snap.id, target)
    assert rows > 3 * PAGE
    written = list(mongo_client[mongo_db]["profiles_syn"].find({}))
    assert len(written) == rows
    assert {"$.kyc.taxId", "$.accounts[*].iban"} <= set(check["columns"])
    for path, counts in check["columns"].items():
        held = sum(1 for d in written for v in values_at(d, path) if v is not None)
        assert counts["checked"] == held > 0, (path, counts["checked"], held)
    assert check["scope"] == "whole_table"
    assert check["rows_read"] == {"target": rows, "source": rows}
