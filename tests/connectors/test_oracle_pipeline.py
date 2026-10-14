"""Oracle through M2's job pipeline, in process: submit → discover → accept → run → finalize.

The registry opens the Oracle connector from a stored connection (``secret_ref`` resolved from the
handler's secret dir, easy-connect DSN from the connection's parameters), discovery classifies its
columns through the shared ``classify_columns`` (the ROWID pseudo key of a key-less table is kept,
never classified), the run handlers write through ``OracleWriter``, and finalize clears the write
ledger. Every masking claim is a source-vs-target comparison in SQL (counts only).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from dps import jobs
from dps.auth import LOCAL_PRINCIPAL
from dps.connections import Connection, create_connection
from dps.engine.policy import MethodSpec
from dps.jobs import JobState
from dps.jobs_models import (
    ConnectionParams,
    ConnectionSpec,
    DiscoverSpec,
    ObjectRef,
    SnapshotOverride,
    SpecError,
)
from dps.layers.connectors.evidence import get_evidence
from dps.layers.connectors.submit import submit_discover, submit_run
from dps.worker import HandlerContext
from tests.connectors.helpers import db_key_enc, discover_and_accept, drive
from tests.connectors.oracle_fixtures import create_customers, drop_all
from tests.connectors.test_run_modes import run_spec
from tests.fixtures.m3_services import OracleTestSchema
from tests.services import host_port

pytestmark = [pytest.mark.db, pytest.mark.integration, pytest.mark.oracle]
RegisterOracle = Callable[..., Awaitable[Connection]]
LEDGER = "DPS_WRITE_LEDGER"


@pytest.fixture(autouse=True)
def _clean(oracle_admin: Any, oracle_schema: OracleTestSchema) -> Iterator[None]:
    drop_all(oracle_admin, oracle_schema.owner)
    yield
    drop_all(oracle_admin, oracle_schema.owner)


@pytest.fixture
def register_oracle(handler_ctx: HandlerContext, oracle_schema: OracleTestSchema) -> RegisterOracle:
    """``await register_oracle("t1", roles=...)`` stores an Oracle connection as the test owner
    whose ``secret_ref`` holds only a username and password (the DSN comes from the params)."""
    ref = "m3/test-oracle-owner"
    folder = Path(handler_ctx.settings.secret_dir) / ref
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "username").write_text(oracle_schema.owner + "\n", encoding="utf-8")
    (folder / "password").write_text(oracle_schema.owner_password + "\n", encoding="utf-8")
    host, port = host_port(oracle_schema.dsn)
    service = oracle_schema.dsn.rsplit("/", 1)[-1]

    async def register(tenant: str, *, roles: set[str], name: str) -> Connection:
        spec = ConnectionSpec(
            name=name,
            engine="oracle",
            params=ConnectionParams(host=host, port=port, service_name=service),
            secret_ref=ref,
            roles=roles,  # type: ignore[arg-type]
        )
        return await create_connection(
            tenant, spec, settings=handler_ctx.settings, kek=handler_ctx.kek, created_by="test"
        )

    return register


def _count(conn: Any, sql: str, binds: dict[str, Any] | None = None) -> int:
    with conn.cursor() as cur:
        cur.execute(sql, binds or {})
        (n,) = cur.fetchone()
    return int(n)


def _survivors(conn: Any, src: str, out: str, column: str) -> int:
    """Target values of ``column`` that equal a source value of it (counts only). A created
    target may hold the column as CLOB (widened for tokens), so both sides compare as text."""
    text = f"DBMS_LOB.SUBSTR(TO_CLOB({column}), 4000, 1)"
    return _count(
        conn,
        f"SELECT COUNT(*) FROM (SELECT {text} v FROM {out}) t "
        f"JOIN (SELECT DISTINCT {text} v FROM {src}) s ON t.v = s.v",
    )


def _ledger_rows(conn: Any, owner: str, job_id: str) -> int:
    """The job's rows in the owner's write ledger (0 when the ledger table does not exist)."""
    exists = _count(
        conn,
        "SELECT COUNT(*) FROM all_tables WHERE owner = :o AND table_name = :t",
        {"o": owner, "t": LEDGER},
    )
    if not exists:
        return 0
    return _count(
        conn, f'SELECT COUNT(*) FROM "{owner}"."{LEDGER}" WHERE JOB_ID = :j', {"j": job_id}
    )


async def _run(
    ctx: HandlerContext,
    conn: Connection,
    obj: ObjectRef,
    snap_id: str,
    target: dict[str, Any],
    *,
    source: dict[str, Any] | None = None,
    loops: int = 2000,
) -> Any:
    sub = await submit_run(
        run_spec(conn.id, obj, snap_id, target, source=source),
        tenant_id="t1",
        principal=LOCAL_PRINCIPAL,
        settings=ctx.settings,
        kek=ctx.kek,
    )
    job = await drive(ctx, "t1", sub.job.id, loops=loops)
    return sub, job


def _target_columns(conn: Any, owner: str, table: str) -> list[str]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT column_name FROM all_tab_columns WHERE owner = :o AND table_name = :t "
            "ORDER BY column_id",
            {"o": owner, "t": table},
        )
        return [r[0] for r in cur.fetchall()]


async def test_oracle_table_is_discovered_and_masked_into_a_created_table(
    handler_ctx: HandlerContext,
    oracle_conn: Any,
    oracle_schema: OracleTestSchema,
    register_oracle: RegisterOracle,
) -> None:
    owner = oracle_schema.owner
    create_customers(oracle_conn, owner, 1500)
    conn = await register_oracle("t1", roles={"source", "target"}, name="ora-owner")
    obj = ObjectRef(owner=owner, name="CUSTOMERS")
    snap = await discover_and_accept(handler_ctx, "t1", conn.id, obj)
    cols = {c.name: c for c in snap.columns}
    assert snap.shape.key == ["CUSTOMER_ID"] and cols["CUSTOMER_ID"].is_key
    assert cols["EMAIL"].entity_type == "EMAIL" and cols["EMAIL"].method.method != "keep"
    assert cols["FULL_NAME"].method.method != "keep"
    assert cols["SEGMENT"].method.method == "keep"

    target = {
        "write_mode": "table",
        "connection_id": conn.id,
        "object": {"owner": owner, "name": "CUSTOMERS_UAT"},
    }
    sub, job = await _run(handler_ctx, conn, obj, snap.id, target)
    assert job.state == JobState.succeeded, (job.error_code, job.error)
    assert job.progress.rows_written == 1500 and job.progress.partitions_done >= 2
    src, out = f'"{owner}"."CUSTOMERS"', f'"{owner}"."CUSTOMERS_UAT"'
    assert _count(oracle_conn, f"SELECT COUNT(*) FROM {out}") == 1500
    # source vs target, per sensitive column (counts only): no source value survived
    for column in ("EMAIL", "FULL_NAME", "NOTES"):
        assert _survivors(oracle_conn, src, out, column) == 0, column
    assert _count(oracle_conn, f"SELECT COUNT(*) FROM {out} WHERE EMAIL IS NULL") == 0
    # a kept column is copied unchanged (same multiset of values)
    assert (
        _count(
            oracle_conn,
            f"SELECT COUNT(*) FROM (SELECT SEGMENT, COUNT(*) n FROM {src} GROUP BY SEGMENT "
            f"MINUS SELECT SEGMENT, COUNT(*) n FROM {out} GROUP BY SEGMENT)",
        )
        == 0
    )
    # finalize cleared the job's write ledger; the key is purged; the evidence verified
    assert _ledger_rows(oracle_conn, owner, job.id) == 0
    assert await db_key_enc(job.id) is None
    assert job.progress.evidence_id is not None
    ev = await get_evidence("t1", job.progress.evidence_id)
    assert ev is not None and ev.pack.verification["passed"], ev and ev.pack.verification
    assert ev.pack.counts["rows_written"] == 1500
    # finalize re-read the created Oracle table (the kept timestamps carry low-score DATE hits)
    assert ev.pack.verification["post_sample"]["rows"] == 200
    assert "write_ledger_cleared" not in ev.pack.verification  # a keyed load MERGEs, no ledger


async def test_oracle_in_place_masks_the_table_and_finalize_clears_the_ledger(
    handler_ctx: HandlerContext,
    oracle_conn: Any,
    oracle_schema: OracleTestSchema,
    register_oracle: RegisterOracle,
) -> None:
    owner = oracle_schema.owner
    create_customers(oracle_conn, owner, 900)
    tbl, orig = f'"{owner}"."CUSTOMERS"', f'"{owner}"."CUSTOMERS_ORIG"'
    with oracle_conn.cursor() as cur:  # the comparison copy (never read by DPS)
        cur.execute(f"CREATE TABLE {orig} AS SELECT * FROM {tbl}")
    conn = await register_oracle("t1", roles={"source", "target"}, name="ora-in-place")
    obj = ObjectRef(owner=owner, name="CUSTOMERS")
    review = [SnapshotOverride(path="$.CUSTOMER_ID", method=MethodSpec(method="keep"))]
    snap = await discover_and_accept(handler_ctx, "t1", conn.id, obj, overrides=review)
    target = {"write_mode": "in_place", "confirm_write": True}
    _, job = await _run(handler_ctx, conn, obj, snap.id, target)
    assert job.state == JobState.succeeded, (job.error_code, job.error)
    assert job.progress.rows_written == 900
    assert _count(oracle_conn, f"SELECT COUNT(*) FROM {tbl}") == 900
    same = f"SELECT COUNT(*) FROM {tbl} t JOIN {orig} o ON t.CUSTOMER_ID = o.CUSTOMER_ID WHERE "
    for column in ("EMAIL", "FULL_NAME"):  # source vs target, row by row (counts only)
        assert _count(oracle_conn, same + f"t.{column} = o.{column}") == 0, column
    assert _count(oracle_conn, same + "DBMS_LOB.COMPARE(t.NOTES, o.NOTES) = 0") == 0
    assert _count(oracle_conn, same + "t.SEGMENT = o.SEGMENT AND t.INCOME = o.INCOME") == 900
    assert _count(oracle_conn, same + "t.LAST_CONTACT_AT = o.LAST_CONTACT_AT") == 900  # FF9 kept
    assert _ledger_rows(oracle_conn, owner, job.id) == 0
    ev = await get_evidence("t1", job.progress.evidence_id or "")
    assert ev is not None and ev.pack.verification["passed"]
    assert ev.pack.verification["write_ledger_cleared"] is True
    assert ev.pack.verification["post_sample"]["rows"] == 200


async def test_a_keyless_oracle_table_keeps_its_rowid_and_loads_under_the_ledger(
    handler_ctx: HandlerContext,
    oracle_conn: Any,
    oracle_schema: OracleTestSchema,
    register_oracle: RegisterOracle,
) -> None:
    owner = oracle_schema.owner
    with oracle_conn.cursor() as cur:
        cur.execute(
            f'CREATE TABLE "{owner}"."LEADS" (LEAD_NO NUMBER(8), CONTACT_EMAIL VARCHAR2(400), '
            "SOURCE VARCHAR2(20))"
        )
        cur.executemany(
            f'INSERT INTO "{owner}"."LEADS" VALUES (:1, :2, :3)',
            [(i, f"lead.{i}@example.com", "web" if i % 2 else "branch") for i in range(1200)],
        )
    oracle_conn.commit()
    conn = await register_oracle("t1", roles={"source", "target"}, name="ora-leads")
    obj = ObjectRef(owner=owner, name="LEADS")
    snap = await discover_and_accept(handler_ctx, "t1", conn.id, obj)
    assert snap.shape.key == ["ROWID"] and snap.shape.key_source == "rowid"
    rowid = next(c for c in snap.columns if c.name == "ROWID")
    assert rowid.entity_type is None and rowid.method.method == "keep" and rowid.detectors == []
    email = next(c for c in snap.columns if c.name == "CONTACT_EMAIL")
    assert email.entity_type == "EMAIL" and email.method.method != "keep"

    target = {
        "write_mode": "table",
        "connection_id": conn.id,
        "object": {"owner": owner, "name": "LEADS_UAT"},
    }
    _, job = await _run(handler_ctx, conn, obj, snap.id, target)
    assert job.state == JobState.succeeded, (job.error_code, job.error)
    assert job.progress.rows_written == 1200
    src, out = f'"{owner}"."LEADS"', f'"{owner}"."LEADS_UAT"'
    assert _count(oracle_conn, f"SELECT COUNT(*) FROM {out}") == 1200
    assert _survivors(oracle_conn, src, out, "CONTACT_EMAIL") == 0
    # the pseudo key is never written: the created target has only the table's columns
    assert (
        _count(
            oracle_conn,
            "SELECT COUNT(*) FROM all_tab_columns WHERE owner = :o AND table_name = 'LEADS_UAT'",
            {"o": owner},
        )
        == 3
    )
    # the key-less load paged on ROWID under the ledger; finalize cleared it (M2 cleared only
    # in_place/append ledgers)
    assert _ledger_rows(oracle_conn, owner, job.id) == 0
    ev = await get_evidence("t1", job.progress.evidence_id or "")
    assert ev is not None and ev.pack.verification["passed"]
    assert ev.pack.verification["write_ledger_cleared"] is True


async def test_a_query_subset_creates_and_writes_only_the_projected_columns(
    handler_ctx: HandlerContext,
    oracle_conn: Any,
    oracle_schema: OracleTestSchema,
    register_oracle: RegisterOracle,
) -> None:
    """M3 G2 B1: the read, the created DDL and the written columns are the query's own select
    list (keys always), as on PostgreSQL: no column outside it exists in the target."""
    owner = oracle_schema.owner
    create_customers(oracle_conn, owner, 300)
    conn = await register_oracle("t1", roles={"source", "target"}, name="ora-q")
    obj = ObjectRef(owner=owner, name="CUSTOMERS")
    snap = await discover_and_accept(handler_ctx, "t1", conn.id, obj)
    target = {
        "write_mode": "table",
        "connection_id": conn.id,
        "object": {"owner": owner, "name": "CUST_Q"},
    }
    sel = {"query": f'SELECT customer_id, email, segment FROM "{owner}".customers'}
    _, job = await _run(handler_ctx, conn, obj, snap.id, target, source={"selection": sel})
    assert job.state == JobState.succeeded, (job.error_code, job.error)
    assert job.progress.rows_written == 300
    assert _target_columns(oracle_conn, owner, "CUST_Q") == ["CUSTOMER_ID", "EMAIL", "SEGMENT"]
    src, out = f'"{owner}"."CUSTOMERS"', f'"{owner}"."CUST_Q"'
    assert _count(oracle_conn, f"SELECT COUNT(*) FROM {out}") == 300
    for column in ("CUSTOMER_ID", "EMAIL", "SEGMENT"):  # nothing NULL-filled
        assert _count(oracle_conn, f"SELECT COUNT(*) FROM {out} WHERE {column} IS NULL") == 0
    for column in ("CUSTOMER_ID", "EMAIL"):  # source vs target (counts only)
        assert _survivors(oracle_conn, src, out, column) == 0, column
    ev = await get_evidence("t1", job.progress.evidence_id or "")
    assert ev is not None and ev.pack.verification["passed"]


async def test_a_query_subset_upsert_never_nulls_the_target_columns_it_does_not_read(
    handler_ctx: HandlerContext,
    oracle_conn: Any,
    oracle_schema: OracleTestSchema,
    register_oracle: RegisterOracle,
) -> None:
    """M3 G2 B1: an upsert whose query reads a subset MERGEs only those columns; matched rows
    keep every other target value (the probe saw 300/300 FULL_NAME overwritten or NULL)."""
    owner = oracle_schema.owner
    create_customers(oracle_conn, owner, 300)
    conn = await register_oracle("t1", roles={"source", "target"}, name="ora-q-upsert")
    obj = ObjectRef(owner=owner, name="CUSTOMERS")
    keep_key = [SnapshotOverride(path="$.CUSTOMER_ID", method=MethodSpec(method="keep"))]
    snap = await discover_and_accept(handler_ctx, "t1", conn.id, obj, overrides=keep_key)
    target = {
        "write_mode": "table",
        "connection_id": conn.id,
        "object": {"owner": owner, "name": "CUST_U"},
    }
    _, first = await _run(handler_ctx, conn, obj, snap.id, target)
    assert first.state == JobState.succeeded, (first.error_code, first.error)
    out = f'"{owner}"."CUST_U"'
    copy = f'"{owner}"."CUST_U_BEFORE"'
    with oracle_conn.cursor() as cur:  # the comparison copy (never read by DPS)
        cur.execute(f"CREATE TABLE {copy} AS SELECT * FROM {out}")
    sel = {"query": f'SELECT customer_id, email, segment FROM "{owner}".customers'}
    _, job = await _run(
        handler_ctx,
        conn,
        obj,
        snap.id,
        {**target, "table_load": "upsert"},
        source={"selection": sel},
    )
    assert job.state == JobState.succeeded, (job.error_code, job.error)
    assert _count(oracle_conn, f"SELECT COUNT(*) FROM {out}") == 300  # every key matched
    same = f"SELECT COUNT(*) FROM {out} t JOIN {copy} b ON t.CUSTOMER_ID = b.CUSTOMER_ID WHERE "
    # unread columns: unchanged row by row, none NULL-filled
    assert _count(oracle_conn, same + "t.FULL_NAME = b.FULL_NAME AND t.DOB = b.DOB") == 300
    assert _count(oracle_conn, same + "DBMS_LOB.COMPARE(t.NOTES, b.NOTES) = 0") == _count(
        oracle_conn, f"SELECT COUNT(*) FROM {copy} WHERE NOTES IS NOT NULL"
    )
    assert _count(oracle_conn, f"SELECT COUNT(*) FROM {out} WHERE FULL_NAME IS NULL") == 0
    # the read column was rewritten under the second job's key; no source e-mail survived
    assert _survivors(oracle_conn, f'"{owner}"."CUSTOMERS"', out, "EMAIL") == 0


async def test_a_keyless_table_load_resumes_from_its_ledger_after_a_lost_checkpoint(
    handler_ctx: HandlerContext,
    oracle_conn: Any,
    oracle_schema: OracleTestSchema,
    register_oracle: RegisterOracle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """M3 G2 B2: a key-less ``table`` load is ledgered, so a batch whose rows and ledger mark
    committed but whose checkpoint was lost is taken from the ledger on resume: the job
    finishes with every row exactly once (the probe stalled at 1,000 of 1,200)."""
    owner = oracle_schema.owner
    with oracle_conn.cursor() as cur:
        cur.execute(
            f'CREATE TABLE "{owner}"."LEADS" (LEAD_NO NUMBER(8), CONTACT_EMAIL VARCHAR2(400), '
            "SOURCE VARCHAR2(20))"
        )
        cur.executemany(
            f'INSERT INTO "{owner}"."LEADS" VALUES (:1, :2, :3)',
            [(i, f"lead.{i}@example.com", "web" if i % 2 else "branch") for i in range(1200)],
        )
    oracle_conn.commit()
    conn = await register_oracle("t1", roles={"source", "target"}, name="ora-leads-resume")
    obj = ObjectRef(owner=owner, name="LEADS")
    snap = await discover_and_accept(handler_ctx, "t1", conn.id, obj)
    assert snap.shape.key_source == "rowid"
    handler_ctx.settings = handler_ctx.settings.model_copy(update={"job_retry_base_seconds": 0.0})
    real_checkpoint = jobs.checkpoint
    calls = {"n": 0}

    async def crash_once(*args: Any, **kwargs: Any) -> bool:
        calls["n"] += 1
        if calls["n"] == 2:  # batch 2 committed with its ledger mark; its checkpoint is lost
            raise ConnectionError("worker died between the write and the checkpoint")
        return bool(await real_checkpoint(*args, **kwargs))

    monkeypatch.setattr(jobs, "checkpoint", crash_once)
    target = {
        "write_mode": "table",
        "connection_id": conn.id,
        "object": {"owner": owner, "name": "LEADS_UAT"},
    }
    _, job = await _run(handler_ctx, conn, obj, snap.id, target, loops=600)
    assert job.state == JobState.succeeded, (job.error_code, job.error)
    assert job.progress.rows_written == 1200 and calls["n"] >= 4
    assert sum(p.resumed for p in await jobs.list_partitions("t1", job.id)) == 1
    src, out = f'"{owner}"."LEADS"', f'"{owner}"."LEADS_UAT"'
    assert _count(oracle_conn, f"SELECT COUNT(*) FROM {out}") == 1200
    # no batch applied twice: no repeated row, and every source row is represented once
    assert (
        _count(
            oracle_conn,
            f"SELECT COUNT(*) FROM (SELECT LEAD_NO, SOURCE, DBMS_LOB.SUBSTR(TO_CLOB("
            f"CONTACT_EMAIL), 4000, 1) e, COUNT(*) n FROM {out} GROUP BY LEAD_NO, SOURCE, "
            "DBMS_LOB.SUBSTR(TO_CLOB(CONTACT_EMAIL), 4000, 1) HAVING COUNT(*) > 1)",
        )
        == 0
    )
    assert _survivors(oracle_conn, src, out, "CONTACT_EMAIL") == 0
    assert _ledger_rows(oracle_conn, owner, job.id) == 0
    ev = await get_evidence("t1", job.progress.evidence_id or "")
    assert ev is not None and ev.pack.verification["passed"]
    assert ev.pack.counts["resumed_partitions"] == 1


def _create_leads(conn: Any, owner: str, n: int) -> None:
    """A key-less table (no primary key, no unique index): DPS pages it on ROWID."""
    with conn.cursor() as cur:
        cur.execute(
            f'CREATE TABLE "{owner}"."LEADS" (LEAD_NO NUMBER(8), CONTACT_EMAIL VARCHAR2(400), '
            "SOURCE VARCHAR2(20))"
        )
        cur.executemany(
            f'INSERT INTO "{owner}"."LEADS" VALUES (:1, :2, :3)',
            [(i, f"lead.{i}@example.com", "web" if i % 2 else "branch") for i in range(n)],
        )
    conn.commit()


async def test_a_keyless_upsert_is_refused_and_truncate_insert_reloads_exactly(
    handler_ctx: HandlerContext,
    oracle_conn: Any,
    oracle_schema: OracleTestSchema,
    register_oracle: RegisterOracle,
) -> None:
    """M3 G2 B3: a key-less source cannot upsert (the probe doubled the target to 1000 rows on
    a second run); it is refused before a job exists, and ``truncate_insert`` run twice leaves
    exactly the source's row count."""
    owner = oracle_schema.owner
    _create_leads(oracle_conn, owner, 500)
    conn = await register_oracle("t1", roles={"source", "target"}, name="ora-leads-upsert")
    obj = ObjectRef(owner=owner, name="LEADS")
    snap = await discover_and_accept(handler_ctx, "t1", conn.id, obj)
    assert snap.shape.key_source == "rowid"
    target = {
        "write_mode": "table",
        "connection_id": conn.id,
        "object": {"owner": owner, "name": "LEADS_UP"},
    }
    with pytest.raises(SpecError) as err:
        await _run(handler_ctx, conn, obj, snap.id, {**target, "table_load": "upsert"})
    assert (err.value.code, err.value.status) == ("no_key", 422)
    out = f'"{owner}"."LEADS_UP"'
    for _ in range(2):
        _, job = await _run(handler_ctx, conn, obj, snap.id, target)
        assert job.state == JobState.succeeded, (job.error_code, job.error)
        assert _count(oracle_conn, f"SELECT COUNT(*) FROM {out}") == 500
    dupes = f"SELECT COUNT(*) FROM (SELECT LEAD_NO FROM {out} GROUP BY LEAD_NO HAVING COUNT(*) > 1)"
    assert _count(oracle_conn, dupes) == 0
    assert _survivors(oracle_conn, f'"{owner}"."LEADS"', out, "CONTACT_EMAIL") == 0


async def test_lower_case_columns_meet_the_same_method_checks(
    handler_ctx: HandlerContext,
    oracle_conn: Any,
    oracle_schema: OracleTestSchema,
    register_oracle: RegisterOracle,
) -> None:
    """M3 G2 B4: ``columns`` folds to upper case at submit exactly as the run folds it, so a
    kept free-text NOTES is refused however it is spelt (the probe copied 200/200 raw notes
    with ``columns: ["notes"]``). Nothing is written."""
    owner = oracle_schema.owner
    create_customers(oracle_conn, owner, 200)
    conn = await register_oracle("t1", roles={"source", "target"}, name="ora-lower")
    obj = ObjectRef(owner=owner, name="CUSTOMERS")
    keep_notes = [SnapshotOverride(path="$.NOTES", method=MethodSpec(method="keep"))]
    snap = await discover_and_accept(handler_ctx, "t1", conn.id, obj, overrides=keep_notes)
    notes = next(c for c in snap.columns if c.name == "NOTES")
    assert notes.text_column and notes.method.method == "keep"
    target = {
        "write_mode": "table",
        "connection_id": conn.id,
        "object": {"owner": owner, "name": "CUST_LOWER"},
    }
    for columns in (["NOTES"], ["notes"], ["Notes"]):
        with pytest.raises(SpecError) as err:
            await _run(
                handler_ctx, conn, obj, snap.id, target, source={"selection": {"columns": columns}}
            )
        assert (err.value.code, err.value.status) == ("text_method_invalid", 422), columns
    assert (
        _count(
            oracle_conn,
            "SELECT COUNT(*) FROM all_tables WHERE owner = :o AND table_name = 'CUST_LOWER'",
            {"o": owner},
        )
        == 0
    )


async def test_a_query_alias_cannot_put_a_column_under_a_kept_columns_name(
    handler_ctx: HandlerContext,
    oracle_conn: Any,
    oracle_schema: OracleTestSchema,
    register_oracle: RegisterOracle,
) -> None:
    """M3 G2 B5: ``SELECT contact_id, email AS channel`` would give e-mail addresses CHANNEL's
    reviewed method (``keep``); it is refused at submit and in discovery. The plain query over
    the same columns still runs and masks what it reads."""
    owner = oracle_schema.owner
    with oracle_conn.cursor() as cur:
        cur.execute(
            f'CREATE TABLE "{owner}"."CONTACTS" (CONTACT_ID NUMBER(10) PRIMARY KEY, '
            "EMAIL VARCHAR2(400), CHANNEL VARCHAR2(400))"
        )
        cur.executemany(
            f'INSERT INTO "{owner}"."CONTACTS" VALUES (:1, :2, :3)',
            [
                (i, f"contact.{i}@example.com", ("web", "branch", "phone")[i % 3])
                for i in range(400)
            ],
        )
    oracle_conn.commit()
    conn = await register_oracle("t1", roles={"source", "target"}, name="ora-alias")
    obj = ObjectRef(owner=owner, name="CONTACTS")
    keep_key = [SnapshotOverride(path="$.CONTACT_ID", method=MethodSpec(method="keep"))]
    snap = await discover_and_accept(handler_ctx, "t1", conn.id, obj, overrides=keep_key)
    methods = {c.name: c.method.method for c in snap.columns}
    assert methods["CHANNEL"] == "keep" and methods["EMAIL"] != "keep"
    target = {
        "write_mode": "table",
        "connection_id": conn.id,
        "object": {"owner": owner, "name": "CONTACTS_Q"},
    }
    table = f'"{owner}".contacts'
    for query in (
        f"SELECT contact_id, email AS channel FROM {table}",
        f"SELECT c.contact_id, c.email channel FROM {table} c",
        f"SELECT * FROM (SELECT contact_id, email AS channel FROM {table})",
    ):
        with pytest.raises(SpecError) as err:
            await _run(
                handler_ctx, conn, obj, snap.id, target, source={"selection": {"query": query}}
            )
        assert (err.value.code, err.value.status) == ("selection_query_alias", 422), query
    assert (
        _count(
            oracle_conn,
            "SELECT COUNT(*) FROM all_tables WHERE owner = :o AND table_name = 'CONTACTS_Q'",
            {"o": owner},
        )
        == 0
    )
    # discovery binds the same rule (its submit has no shape; the job checks with the shape)
    disc = await submit_discover(
        DiscoverSpec(
            connection_id=conn.id,
            objects=[obj],
            selection={"query": f"SELECT contact_id, email AS channel FROM {table}"},
        ),
        tenant_id="t1",
        principal=LOCAL_PRINCIPAL,
        settings=handler_ctx.settings,
        kek=handler_ctx.kek,
    )
    failed = await drive(handler_ctx, "t1", disc.id)
    assert (failed.state, failed.error_code) == (JobState.failed, "selection_query_alias")
    # the plain query runs; CHANNEL is kept as reviewed and no source e-mail reaches the target
    plain = {"query": f"SELECT contact_id, email, channel FROM {table}"}
    _, job = await _run(handler_ctx, conn, obj, snap.id, target, source={"selection": plain})
    assert job.state == JobState.succeeded, (job.error_code, job.error)
    src, out = f'"{owner}"."CONTACTS"', f'"{owner}"."CONTACTS_Q"'
    assert _count(oracle_conn, f"SELECT COUNT(*) FROM {out}") == 400
    assert _survivors(oracle_conn, src, out, "EMAIL") == 0
    same_channel = (
        f"SELECT COUNT(*) FROM {out} t JOIN {src} s ON t.CONTACT_ID = s.CONTACT_ID "
        "WHERE t.CHANNEL = s.CHANNEL"
    )
    assert _count(oracle_conn, same_channel) == 400
    email_in_channel = (
        f"SELECT COUNT(*) FROM {out} t JOIN {src} s ON DBMS_LOB.SUBSTR(TO_CLOB(t.CHANNEL), "
        "4000, 1) = s.EMAIL"
    )
    assert _count(oracle_conn, email_in_channel) == 0
    ev = await get_evidence("t1", job.progress.evidence_id or "")
    assert ev is not None and ev.pack.verification["passed"]


async def test_unpivot_pivot_or_match_recognize_cannot_remap_a_column(
    handler_ctx: HandlerContext,
    oracle_conn: Any,
    oracle_schema: OracleTestSchema,
    register_oracle: RegisterOracle,
) -> None:
    """M3 verify r1 B6: ``UNPIVOT (channel FOR src IN (email, channel)) WHERE src = :s`` puts
    EMAIL values under CHANNEL, whose reviewed method is ``keep`` (the verifier's lane run wrote
    20000/20000 source e-mails and its evidence passed). UNPIVOT, PIVOT, MATCH_RECOGNIZE and
    column-list aliases are refused at submit (422, nothing created) and by a discovery job."""
    owner = oracle_schema.owner
    with oracle_conn.cursor() as cur:
        cur.execute(
            f'CREATE TABLE "{owner}"."CONTACTS_P" (CONTACT_ID NUMBER(10) PRIMARY KEY, '
            "EMAIL VARCHAR2(400), CHANNEL VARCHAR2(400))"
        )
        cur.executemany(
            f'INSERT INTO "{owner}"."CONTACTS_P" VALUES (:1, :2, :3)',
            [
                (i, f"contact.{i}@example.com", ("web", "branch", "phone")[i % 3])
                for i in range(200)
            ],
        )
    oracle_conn.commit()
    conn = await register_oracle("t1", roles={"source", "target"}, name="ora-pivot")
    obj = ObjectRef(owner=owner, name="CONTACTS_P")
    keep_key = [SnapshotOverride(path="$.CONTACT_ID", method=MethodSpec(method="keep"))]
    snap = await discover_and_accept(handler_ctx, "t1", conn.id, obj, overrides=keep_key)
    methods = {c.name: c.method.method for c in snap.columns}
    assert methods["CHANNEL"] == "keep" and methods["EMAIL"] != "keep"
    target = {
        "write_mode": "table",
        "connection_id": conn.id,
        "object": {"owner": owner, "name": "CONTACTS_PQ"},
    }
    table = f'"{owner}".contacts_p'
    unpivot = (
        f"SELECT contact_id, channel FROM {table} "
        "UNPIVOT (channel FOR src IN (email, channel)) WHERE src = :s"
    )
    for selection in (
        {"query": unpivot, "params": {"s": "EMAIL"}},
        {"query": f"SELECT * FROM {table} UNPIVOT (channel FOR src IN (email, channel))"},
        {
            "query": f"SELECT contact_id, channel FROM {table} MATCH_RECOGNIZE (PARTITION BY "
            "contact_id ORDER BY contact_id MEASURES email AS channel ONE ROW PER MATCH "
            "PATTERN (a) DEFINE a AS 1 = 1)"
        },
        {
            "query": f"SELECT contact_id, channel FROM {table} "
            "PIVOT (max(email) FOR channel IN ('web' AS channel))"
        },
        {"query": f"SELECT c.contact_id, c.channel FROM {table} c(contact_id, channel, email)"},
    ):
        with pytest.raises(SpecError) as err:
            await _run(handler_ctx, conn, obj, snap.id, target, source={"selection": selection})
        assert (err.value.code, err.value.status) == ("selection_query_alias", 422), selection
    assert (
        _count(
            oracle_conn,
            "SELECT COUNT(*) FROM all_tables WHERE owner = :o AND table_name = 'CONTACTS_PQ'",
            {"o": owner},
        )
        == 0
    )
    disc = await submit_discover(
        DiscoverSpec(
            connection_id=conn.id,
            objects=[obj],
            selection={"query": unpivot, "params": {"s": "EMAIL"}},
        ),
        tenant_id="t1",
        principal=LOCAL_PRINCIPAL,
        settings=handler_ctx.settings,
        kek=handler_ctx.kek,
    )
    failed = await drive(handler_ctx, "t1", disc.id)
    assert (failed.state, failed.error_code) == (JobState.failed, "selection_query_alias")
