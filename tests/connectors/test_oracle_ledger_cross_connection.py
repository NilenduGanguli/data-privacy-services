"""A key-less Oracle ``table`` load on a separate target connection clears its write ledger
(S1-M3 post-merge verification r2, engines B2; contract §4.2: the ledger is empty after finalize).

A key-less source (paged on ``ROWID``) makes a ``table`` load ledgered. When ``prepare`` *creates*
the target table the plan has no ``target_shape``, and finalize built the writer that clears the
ledger from the *source* object described on the *target* connection. The demo's ``CRM_MASKED``
login cannot see ``CRM.ADDRESS_HISTORY``: ``describe`` raised, the best-effort step swallowed it,
and one ledger row per job (the last source ROWID in ``LAST_KEY``) stayed in the target schema
while the evidence said ``write_ledger_cleared: false`` next to ``passed: true``. The earlier tests
used one connection as source and target, so they never took that path. Here the source is the
session's test owner and the target a second owner holding no grant on it (``CRM`` →
``CRM_MASKED``), through the in-process pipeline: discover → accept → run → finalize. Counts only.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from dps.auth import LOCAL_PRINCIPAL
from dps.jobs import JobState
from dps.jobs_models import ObjectRef
from dps.layers.connectors.evidence import get_evidence
from dps.layers.connectors.submit import submit_run
from dps.worker import HandlerContext
from tests.connectors.helpers import discover_and_accept, drive
from tests.connectors.oracle_fixtures import drop_all
from tests.connectors.test_oracle_keyless_synthesize import DDL
from tests.connectors.test_oracle_keyless_synthesize import _load as load_addresses
from tests.connectors.test_run_modes import run_spec
from tests.e2e.test_m3_resume_after_kill import _register_oracle
from tests.fixtures.m3_services import OracleTestSchema

pytestmark = [pytest.mark.db, pytest.mark.integration, pytest.mark.oracle]
LEDGER = "DPS_WRITE_LEDGER"


@pytest.fixture(autouse=True)
def _clean(
    oracle_admin: Any, oracle_schema: OracleTestSchema, oracle_target_schema: OracleTestSchema
) -> Iterator[None]:
    for owner in (oracle_schema.owner, oracle_target_schema.owner):
        drop_all(oracle_admin, owner)
    yield
    for owner in (oracle_schema.owner, oracle_target_schema.owner):
        drop_all(oracle_admin, owner)


def _count(admin: Any, sql: str, binds: dict[str, Any]) -> int:
    with admin.cursor() as cur:
        cur.execute(sql, binds)
        return int(cur.fetchone()[0])


def ledger_state(admin: Any, owner: str, job_id: str) -> tuple[bool, int]:
    """``(the owner's ledger table exists, the job's rows in it)`` — read as SYSTEM."""
    exists = _count(
        admin,
        "SELECT COUNT(*) FROM all_tables WHERE owner = :o AND table_name = :t",
        {"o": owner, "t": LEDGER},
    )
    if not exists:
        return False, 0
    rows = _count(
        admin, f'SELECT COUNT(*) FROM "{owner}"."{LEDGER}" WHERE JOB_ID = :j', {"j": job_id}
    )
    return True, rows


@pytest.mark.parametrize(
    ("mode", "existing"),
    [("obfuscate", False), ("synthesize", False), ("obfuscate", True)],
    ids=["obfuscate-created", "synthesize-created", "obfuscate-existing"],
)
async def test_a_keyless_load_on_another_login_clears_its_write_ledger(
    handler_ctx: HandlerContext,
    oracle_admin: Any,
    oracle_conn: Any,
    oracle_schema: OracleTestSchema,
    oracle_target_schema: OracleTestSchema,
    mode: str,
    existing: bool,
) -> None:
    source = load_addresses(oracle_conn, oracle_schema.owner)
    target_owner = oracle_target_schema.owner
    # the premise of the finding: the target login holds no grant on the source object
    grants = _count(
        oracle_admin,
        "SELECT COUNT(*) FROM dba_tab_privs WHERE grantee = :g AND owner = :o",
        {"g": target_owner, "o": oracle_schema.owner},
    )
    assert grants == 0
    if existing:  # the control: an existing target table has a target shape at plan time
        ddl = DDL.format(o=target_owner).replace("ADDRESS_HISTORY", "ADDR_OUT")
        with oracle_admin.cursor() as cur:  # as wide as a created table: tokens outgrow 200
            cur.execute(ddl.replace("ADDR_LINE1 VARCHAR2(200 CHAR)", "ADDR_LINE1 CLOB"))
    src = await _register_oracle(handler_ctx, oracle_schema)
    tgt = await _register_oracle(  # a target-only login without any grant on the source
        handler_ctx,
        oracle_target_schema,
        ref="m3/ledger-target-owner",
        name="ledger-target",
        roles=frozenset({"target"}),
    )
    obj = ObjectRef(owner=oracle_schema.owner, name="ADDRESS_HISTORY")
    snap = await discover_and_accept(handler_ctx, "t1", src.id, obj)
    assert snap.shape.key_source == "rowid"  # key-less: paged on ROWID, a ledgered table load
    target = {
        "write_mode": "table",
        "connection_id": tgt.id,
        "object": {"owner": target_owner, "name": "ADDR_OUT"},
        "create_if_missing": True,
    }
    sub = await submit_run(
        run_spec(src.id, obj, snap.id, target, mode=mode),
        tenant_id="t1",
        principal=LOCAL_PRINCIPAL,
        settings=handler_ctx.settings,
        kek=handler_ctx.kek,
    )
    job = await drive(handler_ctx, "t1", sub.job.id, loops=3000)
    assert job.state == JobState.succeeded, (job.error_code, job.error)
    written = _count(oracle_admin, f'SELECT COUNT(*) FROM "{target_owner}"."ADDR_OUT"', {})
    assert written == len(source)
    exists, left = ledger_state(oracle_admin, target_owner, job.id)
    assert exists  # the load was ledgered in the target owner's schema ...
    assert left == 0  # ... and finalize emptied the job's rows
    ev = await get_evidence("t1", job.progress.evidence_id or "")
    assert ev is not None
    verification = ev.pack.verification  # counts, statuses and column names only
    assert verification["write_ledger_cleared"] is True
    assert verification["passed"], {k: v for k, v in verification.items() if k != "hits"}
