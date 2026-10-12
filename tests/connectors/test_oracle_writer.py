"""OracleWriter on the lane's Oracle Free: MERGE loads, in-place UPDATE, appends, the write ledger.

    docker compose --profile oracle up -d oracle
    DPS_TEST_REQUIRE_SERVICES=1 pytest -q -m oracle tests/connectors/test_oracle_writer.py

Every test that writes changed values also checks that no source value of a changed column
survived in the target (Stage A lesson 1).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest

from dps.config import Settings
from dps.jobs_models import ConnectionParams, Selection
from dps.layers.connectors.base import (
    ConnectorError,
    KeyRange,
    LedgerConflict,
    LedgerMark,
    ObjectRef,
    PkGenerate,
    PkOffset,
    Row,
    TableShape,
    encode_key,
)
from dps.layers.connectors.postgres import PkCollision
from dps.layers.connectors.targets import PrepareFailure
from tests.connectors.oracle_fixtures import (
    create_customers,
    customer_rows,
    drop_all,
    make_target,
)
from tests.fixtures.m3_services import OracleTestSchema

oracledb = pytest.importorskip("oracledb")

from dps.layers.connectors.oracle import OracleConnector  # noqa: E402

pytestmark = [pytest.mark.integration, pytest.mark.oracle]
FF9 = "'YYYY-MM-DD\"T\"HH24:MI:SS.FF9'"


@pytest.fixture(autouse=True)
def _clean(oracle_admin: Any, oracle_schema: OracleTestSchema) -> Iterator[None]:
    drop_all(oracle_admin, oracle_schema.owner)
    yield
    drop_all(oracle_admin, oracle_schema.owner)


@pytest.fixture
async def c(oracle_schema: OracleTestSchema) -> AsyncIterator[OracleConnector]:
    connector = OracleConnector.from_secret(
        {
            "username": oracle_schema.owner,
            "password": oracle_schema.owner_password,
            "dsn": oracle_schema.dsn,
        },
        ConnectionParams(),
        Settings(_env_file=None),
        action="write",
        client_id="j_test",
    )
    await connector.open()
    yield connector
    await connector.close()


def _obj(schema: OracleTestSchema, name: str) -> ObjectRef:
    return ObjectRef(owner=schema.owner, name=name)


def _mark(job: str, part: int, batch: int, last: Row, shape: TableShape, rows: int) -> LedgerMark:
    keys = shape.key_columns()
    return LedgerMark(
        job_id=job,
        partition_no=part,
        batch_no=batch,
        last_key=encode_key(tuple(last[k.name] for k in keys), keys),
        rows=rows,
    )


async def _all(c: OracleConnector, shape: TableShape) -> list[Row]:
    rows: list[Row] = []
    async for page in c.read_partition(
        shape, Selection(), {}, KeyRange(), after_key=None, batch=100
    ):
        rows += page
    return rows


def _select(conn: Any, sql: str, binds: dict[str, Any] | None = None) -> list[tuple[Any, ...]]:
    with conn.cursor() as cur:
        cur.execute(sql, binds or {})
        return list(cur.fetchall())


def _table_exists(conn: Any, owner: str, name: str) -> bool:
    return bool(
        _select(
            conn,
            "SELECT COUNT(*) FROM all_tables WHERE owner = :o AND table_name = :t",
            {"o": owner, "t": name},
        )[0][0]
    )


async def test_table_mode_creates_target_and_reloads_idempotently(
    c: OracleConnector, oracle_conn: Any, oracle_schema: OracleTestSchema
) -> None:
    o = oracle_schema.owner
    create_customers(oracle_conn, o, 120)
    shape = await c.describe(_obj(oracle_schema, "CUSTOMERS"))
    rows = await _all(c, shape)
    originals = {r["CUSTOMER_ID"]: r["FULL_NAME"] for r in rows}
    out = [{**r, "FULL_NAME": f"T-{r['CUSTOMER_ID']}"} for r in rows]
    writer = await c.writer(
        make_target(shape, write_mode="table", object=_obj(oracle_schema, "CUSTOMERS_M")), shape
    )
    report = await writer.prepare(dry_run=False)
    assert report.created and not report.ledger
    for _ in range(2):  # a resumed table load: the same batch twice upserts
        assert await writer.write_batch(out, mark=_mark("j_test", 0, 1, out[-1], shape, 120)) == 120
    await writer.close()
    got = _select(
        oracle_conn,
        f"SELECT m.CUSTOMER_ID, m.FULL_NAME, TO_CHAR(m.LAST_CONTACT_AT, {FF9}), m.TAX_ID, "
        f"TO_CHAR(s.LAST_CONTACT_AT, {FF9}), m.CREATED_AT, s.CREATED_AT, s.TAX_ID "
        f'FROM "{o}"."CUSTOMERS_M" m JOIN "{o}"."CUSTOMERS" s ON s.CUSTOMER_ID = m.CUSTOMER_ID '
        "ORDER BY 1",
    )
    assert len(got) == 120
    for cid, name, ts_m, tax, ts_s, created_m, created_s, tax_s in got:
        assert name == f"T-{cid}" and name != originals[cid]
        assert ts_m == ts_s and len(ts_m.rsplit(".", 1)[1]) == 9  # nanoseconds intact
        assert tax == tax_s and len(tax) == 10  # CHAR kept padded
        assert created_m == created_s  # TIMESTAMP(6) keeps its microseconds
    target_names = {r[1] for r in got}
    assert not target_names & set(originals.values())  # no source name survived
    assert not _table_exists(oracle_conn, o, "DPS_WRITE_LEDGER")


async def test_truncate_insert_truncates_once(
    c: OracleConnector, oracle_conn: Any, oracle_schema: OracleTestSchema
) -> None:
    o = oracle_schema.owner
    create_customers(oracle_conn, o, 50)
    with oracle_conn.cursor() as cur:
        cur.execute(f'CREATE TABLE "{o}"."CUST_T" AS SELECT * FROM "{o}"."CUSTOMERS" WHERE 1 = 0')
        cur.execute(f'ALTER TABLE "{o}"."CUST_T" ADD PRIMARY KEY (CUSTOMER_ID)')
        cur.executemany(
            f'INSERT INTO "{o}"."CUST_T" (CUSTOMER_ID, FULL_NAME, SEGMENT, CREATED_AT) '
            "VALUES (:1, 'junk', 'x', SYSTIMESTAMP)",
            [(i,) for i in range(1, 8)],
        )
    oracle_conn.commit()
    shape = await c.describe(_obj(oracle_schema, "CUSTOMERS"))
    rows = await _all(c, shape)
    writer = await c.writer(
        make_target(shape, write_mode="table", object=_obj(oracle_schema, "CUST_T")), shape
    )
    report = await writer.prepare(dry_run=False)
    assert report.truncated and not report.created and not report.ledger
    assert await writer.write_batch(rows, mark=_mark("j_t", 0, 1, rows[-1], shape, 50)) == 50
    got = _select(oracle_conn, f'SELECT MIN(CUSTOMER_ID), COUNT(*) FROM "{o}"."CUST_T"')
    assert got == [(10_000_001, 50)]
    upsert = await c.writer(
        make_target(
            shape, write_mode="table", object=_obj(oracle_schema, "CUST_T"), table_load="upsert"
        ),
        shape,
    )
    assert not (await upsert.prepare(dry_run=False)).truncated  # upsert keeps existing rows
    assert _select(oracle_conn, f'SELECT COUNT(*) FROM "{o}"."CUST_T"')[0][0] == 50


async def test_prepare_dry_run_changes_nothing(
    c: OracleConnector, oracle_conn: Any, oracle_schema: OracleTestSchema
) -> None:
    o = oracle_schema.owner
    create_customers(oracle_conn, o, 5)
    shape = await c.describe(_obj(oracle_schema, "CUSTOMERS"))
    table = await c.writer(
        make_target(shape, write_mode="table", object=_obj(oracle_schema, "NEW_T")), shape
    )
    report = await table.prepare(dry_run=True)
    assert not report.created and not report.ledger
    assert not _table_exists(oracle_conn, o, "NEW_T")
    in_place = await c.writer(
        make_target(shape, write_mode="in_place", write_columns=["FULL_NAME"]), shape
    )
    report = await in_place.prepare(dry_run=True)
    assert not report.ledger
    assert not _table_exists(oracle_conn, o, "DPS_WRITE_LEDGER")


async def test_in_place_updates_only_transformed_columns(
    c: OracleConnector, oracle_conn: Any, oracle_schema: OracleTestSchema
) -> None:
    o = oracle_schema.owner
    create_customers(oracle_conn, o, 40)
    shape = await c.describe(_obj(oracle_schema, "CUSTOMERS"))
    before = await _all(c, shape)
    target = make_target(shape, write_mode="in_place", write_columns=["FULL_NAME", "EMAIL"])
    writer = await c.writer(target, shape)
    report = await writer.prepare(dry_run=False)
    assert report.ledger and _table_exists(oracle_conn, o, "DPS_WRITE_LEDGER")
    out = [
        {
            **r,
            "FULL_NAME": f"Masked {i}",
            "EMAIL": f"m{i}@example.net",
            "SEGMENT": "changed",
        }
        for i, r in enumerate(before)
    ]
    mark = _mark("j_test", 0, 1, before[-1], shape, 40)
    assert await writer.write_batch(out, mark=mark) == 40
    after = await _all(c, shape)
    assert [r["CUSTOMER_ID"] for r in after] == [r["CUSTOMER_ID"] for r in before]
    for i, (b, a) in enumerate(zip(before, after, strict=True)):
        assert a["FULL_NAME"] == f"Masked {i}" and a["EMAIL"] == f"m{i}@example.net"
        assert a["SEGMENT"] == b["SEGMENT"] and a["NOTES"] == b["NOTES"]
        assert a["LAST_CONTACT_AT"] == b["LAST_CONTACT_AT"]
    names = {r["FULL_NAME"] for r in after} | {r["EMAIL"] for r in after}
    assert not names & ({r["FULL_NAME"] for r in before} | {r["EMAIL"] for r in before})
    assert await writer.read_ledger("j_test", 0) == mark


async def test_ledger_fences_a_replayed_batch(
    c: OracleConnector, oracle_conn: Any, oracle_schema: OracleTestSchema
) -> None:
    create_customers(oracle_conn, oracle_schema.owner, 10)
    shape = await c.describe(_obj(oracle_schema, "CUSTOMERS"))
    rows = await _all(c, shape)
    writer = await c.writer(
        make_target(shape, write_mode="in_place", write_columns=["FULL_NAME"]), shape
    )
    await writer.prepare(dry_run=False)
    first = [{**r, "FULL_NAME": "first"} for r in rows[:5]]
    await writer.write_batch(first, mark=_mark("j_f", 0, 1, rows[4], shape, 5))
    again = [{**r, "FULL_NAME": "again"} for r in rows[:5]]
    with pytest.raises(LedgerConflict) as err:  # a zombie replaying batch 1
        await writer.write_batch(again, mark=_mark("j_f", 0, 1, rows[4], shape, 5))
    assert err.value.code == "ledger_conflict"
    with pytest.raises(LedgerConflict):  # a skipped batch is refused as well
        await writer.write_batch(again, mark=_mark("j_f", 0, 3, rows[4], shape, 5))
    names = {r["FULL_NAME"] for r in (await _all(c, shape))[:5]}
    assert names == {"first"}
    second = [{**r, "FULL_NAME": "second"} for r in rows[5:]]
    assert await writer.write_batch(second, mark=_mark("j_f", 0, 2, rows[9], shape, 10)) == 5
    assert await writer.write_batch([], mark=_mark("j_f", 0, 3, rows[9], shape, 10)) == 0
    mark = await writer.read_ledger("j_f", 0)
    assert mark is not None and mark.batch_no == 3 and mark.rows == 10


async def test_a_concurrent_holder_is_fenced(
    c: OracleConnector, oracle_conn: Any, oracle_schema: OracleTestSchema
) -> None:
    """Another holder's open transaction on the partition: the writer waits, then writes nothing."""
    from dps.layers.connectors import oracle_sql as osql

    o = oracle_schema.owner
    create_customers(oracle_conn, o, 6)
    shape = await c.describe(_obj(oracle_schema, "CUSTOMERS"))
    rows = await _all(c, shape)
    writer = await c.writer(
        make_target(shape, write_mode="in_place", write_columns=["FULL_NAME"]), shape
    )
    await writer.prepare(dry_run=False)
    zombie = [{**r, "FULL_NAME": "zombie"} for r in rows[:3]]

    def hold(batch: int) -> None:  # the other holder: ledger advanced, not yet committed
        with oracle_conn.cursor() as cur:
            cur.execute(osql.ledger_ensure_sql(o), {"job": "j_c", "part": 0})
            cur.execute(
                osql.ledger_mark_sql(o),
                {"job": "j_c", "part": 0, "batch": batch, "prev": batch - 1,
                 "last_key": "[]", "written": 3 * batch},
            )  # fmt: skip
            assert cur.rowcount == 1

    for batch in (1, 2):  # the first batch (no ledger row yet) and a later one
        hold(batch)
        task = asyncio.create_task(
            writer.write_batch(zombie, mark=_mark("j_c", 0, batch, rows[2], shape, 3))
        )
        await asyncio.sleep(1.0)
        assert not task.done()  # blocked on the other holder's row
        oracle_conn.commit()
        with pytest.raises(LedgerConflict):
            await task
    assert "zombie" not in {r["FULL_NAME"] for r in await _all(c, shape)}
    mark = await writer.read_ledger("j_c", 0)
    assert mark is not None and mark.batch_no == 2 and mark.rows == 6


async def test_ledger_and_rows_commit_together(
    c: OracleConnector,
    oracle_conn: Any,
    oracle_schema: OracleTestSchema,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    create_customers(oracle_conn, oracle_schema.owner, 6)
    shape = await c.describe(_obj(oracle_schema, "CUSTOMERS"))
    rows = await _all(c, shape)
    writer = await c.writer(
        make_target(shape, write_mode="in_place", write_columns=["FULL_NAME"]), shape
    )
    await writer.prepare(dry_run=False)

    def boom(self: Any, statement: str, parameters: Any, **kw: Any) -> None:
        raise oracledb.DatabaseError("injected failure after the ledger statements")

    monkeypatch.setattr(oracledb.Cursor, "executemany", boom)
    out = [{**r, "FULL_NAME": "never"} for r in rows]
    with pytest.raises(ConnectorError) as err:
        await writer.write_batch(out, mark=_mark("j_atomic", 0, 1, rows[-1], shape, 6))
    assert err.value.code == "target_error" and "injected" not in str(err.value)
    monkeypatch.undo()
    assert await writer.read_ledger("j_atomic", 0) is None  # the ledger rolled back with the rows
    assert "never" not in {r["FULL_NAME"] for r in await _all(c, shape)}
    assert await writer.write_batch(out, mark=_mark("j_atomic", 0, 1, rows[-1], shape, 6)) == 6


async def test_read_ledger_round_trip_and_clear(
    c: OracleConnector, oracle_conn: Any, oracle_schema: OracleTestSchema
) -> None:
    create_customers(oracle_conn, oracle_schema.owner, 4)
    shape = await c.describe(_obj(oracle_schema, "CUSTOMERS"))
    rows = await _all(c, shape)
    writer = await c.writer(
        make_target(shape, write_mode="in_place", write_columns=["EMAIL"]), shape
    )
    assert await writer.read_ledger("j_x", 0) is None  # no ledger table yet
    assert await writer.clear_ledger("j_x") == 0
    await writer.prepare(dry_run=False)
    out = [{**r, "EMAIL": f"x{i}@example.org"} for i, r in enumerate(rows)]
    m0 = _mark("j_x", 0, 1, rows[1], shape, 2)
    m1 = _mark("j_x", 1, 1, rows[3], shape, 2)
    await writer.write_batch(out[:2], mark=m0)
    await writer.write_batch(out[2:], mark=m1)
    assert await writer.read_ledger("j_x", 1) == m1
    assert await writer.read_ledger("j_other", 1) is None
    assert await writer.clear_ledger("j_x") == 2
    assert await writer.read_ledger("j_x", 1) is None
    assert await writer.finish_partition("j_x", 1, batch_no=1) == {}


async def test_append_generate_with_identity(
    c: OracleConnector, oracle_conn: Any, oracle_schema: OracleTestSchema
) -> None:
    o = oracle_schema.owner
    with oracle_conn.cursor() as cur:
        cur.execute(
            f'CREATE TABLE "{o}"."I" (ID NUMBER(12) GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY, '
            "X VARCHAR2(20))"
        )
        cur.executemany(
            f'INSERT INTO "{o}"."I" (X) VALUES (:1)', [(f"orig {i}",) for i in range(10)]
        )
    oracle_conn.commit()
    shape = await c.describe(_obj(oracle_schema, "I"))
    rows = await _all(c, shape)
    old_max = max(r["ID"] for r in rows)
    writer = await c.writer(
        make_target(shape, write_mode="append", pk_strategy=PkGenerate()), shape
    )
    report = await writer.prepare(dry_run=False)
    assert report.ledger and report.append_upper == old_max + 1
    out = [{"X": f"copy {i}"} for i in range(len(rows))]  # M2 drops generated keys from the rows
    assert await writer.write_batch(out, mark=_mark("j_id", 0, 1, rows[-1], shape, 10)) == 10
    after = _select(oracle_conn, f'SELECT ID, X FROM "{o}"."I" ORDER BY ID')
    assert len(after) == 20
    assert all(i > old_max for i, x in after if x.startswith("copy"))


async def test_append_generate_with_sequence(
    c: OracleConnector, oracle_conn: Any, oracle_schema: OracleTestSchema
) -> None:
    o = oracle_schema.owner
    with oracle_conn.cursor() as cur:
        cur.execute(
            f'CREATE TABLE "{o}"."CASES" (CASE_ID NUMBER(12) PRIMARY KEY, NOTE VARCHAR2(40))'
        )
        cur.execute(f'CREATE SEQUENCE "{o}"."CASE_SEQ" START WITH 1000')
        cur.execute(f'CREATE SEQUENCE "{o}"."LATE_SEQ" START WITH 1')
        cur.executemany(
            f'INSERT INTO "{o}"."CASES" VALUES (:1, :2)', [(i, f"case {i}") for i in range(1, 8)]
        )
    oracle_conn.commit()
    shape = await c.describe(_obj(oracle_schema, "CASES"))
    rows = await _all(c, shape)
    behind = await c.writer(
        make_target(shape, write_mode="append", pk_strategy=PkGenerate(sequence=f"{o}.LATE_SEQ")),
        shape,
    )
    with pytest.raises(PrepareFailure) as err:  # a sequence behind the table could re-read rows
        await behind.prepare(dry_run=False)
    assert err.value.code == "pk_strategy_invalid"
    missing = await c.writer(
        make_target(shape, write_mode="append", pk_strategy=PkGenerate(sequence="NO_SUCH_SEQ")),
        shape,
    )
    with pytest.raises(PrepareFailure):
        await missing.prepare(dry_run=False)
    writer = await c.writer(
        make_target(shape, write_mode="append", pk_strategy=PkGenerate(sequence=f"{o}.CASE_SEQ")),
        shape,
    )
    await writer.prepare(dry_run=False)
    out = [{"CASE_ID": r["CASE_ID"], "NOTE": f"copy {r['CASE_ID']}"} for r in rows]
    assert await writer.write_batch(out, mark=_mark("j_seq", 0, 1, rows[-1], shape, 7)) == 7
    after = _select(oracle_conn, f'SELECT CASE_ID, NOTE FROM "{o}"."CASES" ORDER BY CASE_ID')
    assert len(after) == 14
    assert all(cid >= 1000 for cid, note in after if note.startswith("copy"))


async def test_append_offset_and_collision_precheck(
    c: OracleConnector, oracle_conn: Any, oracle_schema: OracleTestSchema
) -> None:
    o = oracle_schema.owner
    create_customers(oracle_conn, o, 10)
    shape = await c.describe(_obj(oracle_schema, "CUSTOMERS"))
    rows = await _all(c, shape)
    target = make_target(shape, write_mode="append", pk_strategy=PkOffset(offset=90_000_000))
    writer = await c.writer(target, shape)

    async def keys(delta: int) -> AsyncIterator[list[tuple[Any, ...]]]:
        yield [(r["CUSTOMER_ID"] + delta,) for r in rows[:6]]
        yield []
        yield [(r["CUSTOMER_ID"] + delta,) for r in rows[6:]]

    assert await writer.precheck_collisions(keys(90_000_000)) == 0
    assert await writer.precheck_collisions(keys(5)) == 5  # 10000006..10000010 exist
    report = await writer.prepare(dry_run=False)
    assert report.append_upper == rows[-1]["CUSTOMER_ID"] + 1
    shifted = [{**r, "CUSTOMER_ID": r["CUSTOMER_ID"] + 90_000_000} for r in rows]
    assert await writer.write_batch(shifted, mark=_mark("j_off", 0, 1, rows[-1], shape, 10)) == 10
    assert _select(oracle_conn, f'SELECT COUNT(*) FROM "{o}"."CUSTOMERS"')[0][0] == 20
    clash = [{**r, "CUSTOMER_ID": r["CUSTOMER_ID"] + 5} for r in rows]
    with pytest.raises(PkCollision) as err:
        await writer.write_batch(clash, mark=_mark("j_off", 1, 1, rows[-1], shape, 10))
    assert err.value.code == "pk_collision"
    assert await writer.read_ledger("j_off", 1) is None  # nothing of that batch committed
    assert _select(oracle_conn, f'SELECT COUNT(*) FROM "{o}"."CUSTOMERS"')[0][0] == 20


async def test_in_place_refused_on_rowid_tables(
    c: OracleConnector, oracle_conn: Any, oracle_schema: OracleTestSchema
) -> None:
    o = oracle_schema.owner
    with oracle_conn.cursor() as cur:
        cur.execute(f'CREATE TABLE "{o}"."H" (A NUMBER, B VARCHAR2(8))')
    shape = await c.describe(_obj(oracle_schema, "H"))
    cases = [
        ("no_key", make_target(shape, write_mode="in_place", write_columns=["B"])),
        ("pk_strategy_invalid", make_target(shape, write_mode="append", pk_strategy=PkGenerate())),
        (
            "pk_strategy_invalid",
            make_target(shape, write_mode="append", pk_strategy=PkOffset(offset=5)),
        ),
    ]
    for code, target in cases:
        writer = await c.writer(target, shape)
        with pytest.raises(PrepareFailure) as err:
            await writer.prepare(dry_run=True)
        assert err.value.code == code


async def test_rowid_table_load_is_ledgered(
    c: OracleConnector, oracle_conn: Any, oracle_schema: OracleTestSchema
) -> None:
    o = oracle_schema.owner
    with oracle_conn.cursor() as cur:
        cur.execute(f'CREATE TABLE "{o}"."H" (A NUMBER, B VARCHAR2(8))')
        cur.executemany(f'INSERT INTO "{o}"."H" VALUES (:1, :2)', [(i, f"b{i}") for i in range(12)])
    oracle_conn.commit()
    shape = await c.describe(_obj(oracle_schema, "H"))
    rows = await _all(c, shape)
    writer = await c.writer(
        make_target(shape, write_mode="table", object=_obj(oracle_schema, "H_M")), shape
    )
    report = await writer.prepare(dry_run=False)
    assert report.created and report.ledger  # no key to upsert on: the ledger makes it resumable
    out = [{**r, "B": f"m{r['A']}"} for r in rows]
    assert await writer.write_batch(out, mark=_mark("j_h", 0, 1, rows[-1], shape, 12)) == 12
    with pytest.raises(LedgerConflict):
        await writer.write_batch(out, mark=_mark("j_h", 0, 1, rows[-1], shape, 12))
    got = _select(oracle_conn, f'SELECT A, B FROM "{o}"."H_M" ORDER BY A')
    assert got == [(i, f"m{i}") for i in range(12)]
    columns = _select(
        oracle_conn,
        "SELECT column_name FROM all_tab_cols WHERE owner = :o AND table_name = 'H_M'",
        {"o": o},
    )
    assert {n for (n,) in columns} == {"A", "B"}  # the ROWID pseudo key is never written


async def test_same_table_append_reports_append_upper(
    c: OracleConnector, oracle_conn: Any, oracle_schema: OracleTestSchema
) -> None:
    o = oracle_schema.owner
    with oracle_conn.cursor() as cur:
        cur.execute(
            f'CREATE TABLE "{o}"."G" (ID NUMBER(12) GENERATED ALWAYS AS IDENTITY PRIMARY KEY, '
            "X VARCHAR2(8))"
        )
        cur.executemany(f'INSERT INTO "{o}"."G" (X) VALUES (:1)', [(f"x{i}",) for i in range(10)])
        cur.execute(
            f'CREATE TABLE "{o}"."D" (ID NUMBER(12) GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY, '
            "X VARCHAR2(8))"
        )
        cur.executemany(
            f'INSERT INTO "{o}"."D" VALUES (:1, :2)', [(i * 10, "x") for i in range(1, 6)]
        )
    oracle_conn.commit()
    shape = await c.describe(_obj(oracle_schema, "G"))
    writer = await c.writer(
        make_target(shape, write_mode="append", pk_strategy=PkGenerate()), shape
    )
    assert (await writer.prepare(dry_run=False)).append_upper == 11
    behind = await c.describe(_obj(oracle_schema, "D"))  # explicit ids ran ahead of the identity
    writer = await c.writer(
        make_target(behind, write_mode="append", pk_strategy=PkGenerate()), behind
    )
    with pytest.raises(PrepareFailure) as err:
        await writer.prepare(dry_run=False)
    assert err.value.code == "pk_strategy_invalid"
    other = await c.writer(  # into another table the identity's position does not matter
        make_target(
            behind,
            write_mode="append",
            pk_strategy=PkGenerate(),
            object=_obj(oracle_schema, "D"),
            same_as_source=False,
        ),
        behind,
    )
    assert (await other.prepare(dry_run=True)).append_upper is None


async def test_length_overflow_and_kind_checks(
    c: OracleConnector, oracle_conn: Any, oracle_schema: OracleTestSchema
) -> None:
    o = oracle_schema.owner
    create_customers(oracle_conn, o, 2)
    shape = await c.describe(_obj(oracle_schema, "CUSTOMERS"))
    wider = shape.model_copy(
        update={
            "columns": [
                col.model_copy(update={"length": 200}) if col.name == "FULL_NAME" else col
                for col in shape.columns
            ]
        }
    )
    writer = await c.writer(
        make_target(shape, write_mode="in_place", write_columns=["FULL_NAME"], target_shape=wider),
        shape,
    )
    with pytest.raises(PrepareFailure) as err:
        await writer.prepare(dry_run=True)
    assert err.value.code == "length_overflow"
    with oracle_conn.cursor() as cur:
        cur.execute(
            f'CREATE TABLE "{o}"."NUM_T" (CUSTOMER_ID NUMBER PRIMARY KEY, FULL_NAME VARCHAR2(400))'
        )
    as_text = shape.model_copy(
        update={
            "columns": [
                col.model_copy(update={"kind": "text", "length": 40})
                if col.name == "CUSTOMER_ID"
                else col
                for col in shape.columns
            ]
        }
    )
    writer = await c.writer(
        make_target(
            shape,
            write_mode="table",
            object=_obj(oracle_schema, "NUM_T"),
            write_columns=["CUSTOMER_ID", "FULL_NAME"],
            target_shape=as_text,
        ),
        shape,
    )
    with pytest.raises(PrepareFailure) as err:
        await writer.prepare(dry_run=True)
    assert err.value.code == "incompatible_method"
    writer = await c.writer(
        make_target(
            shape,
            write_mode="table",
            object=_obj(oracle_schema, "NUM_T"),
            write_columns=["CUSTOMER_ID", "FULL_NAME", "EMAIL"],
        ),
        shape,
    )
    with pytest.raises(PrepareFailure) as err:
        await writer.prepare(dry_run=True)
    assert err.value.code == "column_missing"
    absent = await c.writer(
        make_target(
            shape, write_mode="table", object=_obj(oracle_schema, "NOPE"), create_if_missing=False
        ),
        shape,
    )
    with pytest.raises(PrepareFailure) as err:
        await absent.prepare(dry_run=True)
    assert err.value.code == "target_missing"


async def test_unsupported_columns_are_refused_for_copies(
    c: OracleConnector, oracle_conn: Any, oracle_schema: OracleTestSchema
) -> None:
    o = oracle_schema.owner
    with oracle_conn.cursor() as cur:
        cur.execute(
            f'CREATE TABLE "{o}"."X" (ID NUMBER(6) PRIMARY KEY, NAME VARCHAR2(20), '
            "WAIT INTERVAL DAY(2) TO SECOND(6))"
        )
    shape = await c.describe(_obj(oracle_schema, "X"))
    assert shape.column("WAIT").kind == "other"
    copy = await c.writer(
        make_target(shape, write_mode="table", object=_obj(oracle_schema, "X_M")), shape
    )
    with pytest.raises(PrepareFailure) as err:
        await copy.prepare(dry_run=True)
    assert err.value.code == "column_unsupported"
    projected = await c.writer(  # Selection.columns projected it out: M2's write_columns
        make_target(
            shape,
            write_mode="table",
            object=_obj(oracle_schema, "X_M"),
            write_columns=["ID", "NAME"],
        ),
        shape,
    )
    assert (await projected.prepare(dry_run=False)).created


async def test_created_targets_widen_planned_text_columns(
    c: OracleConnector, oracle_conn: Any, oracle_schema: OracleTestSchema
) -> None:
    """M2 creates targets without a target shape and marks planned text output ``text``."""
    o = oracle_schema.owner
    create_customers(oracle_conn, o, 3)
    shape = await c.describe(_obj(oracle_schema, "CUSTOMERS"))
    rows = await _all(c, shape)
    target = make_target(
        shape,
        write_mode="table",
        object=_obj(oracle_schema, "WIDE"),
        write_columns=["CUSTOMER_ID", "TAX_ID", "FULL_NAME", "NOTES"],
        target_shape=None,
        column_types={"TAX_ID": "text", "FULL_NAME": "text", "NOTES": "text"},
    )
    writer = await c.writer(target, shape)
    assert (await writer.prepare(dry_run=False)).created
    types = dict(
        _select(
            oracle_conn,
            "SELECT column_name, data_type || '(' || char_length || ')' FROM all_tab_cols "
            "WHERE owner = :o AND table_name = 'WIDE'",
            {"o": o},
        )
    )
    assert types["TAX_ID"] == "VARCHAR2(90)" and types["FULL_NAME"] == "VARCHAR2(794)"
    assert types["NOTES"].startswith("CLOB")
    token = "tok_" + "z" * 80  # longer than the CHAR(10) source; never blank-padded
    out = [{**r, "TAX_ID": token, "FULL_NAME": "n" * 700} for r in rows]
    assert await writer.write_batch(out, mark=_mark("j_w", 0, 1, rows[-1], shape, 3)) == 3
    got = _select(oracle_conn, f'SELECT TAX_ID, LENGTH(FULL_NAME) FROM "{o}"."WIDE"')
    assert got == [(token, 700)] * 3
    sources = {r["TAX_ID"] for r in rows}
    assert not {t for t, _ in got} & sources


async def test_writer_needs_a_table_target(
    c: OracleConnector, oracle_conn: Any, oracle_schema: OracleTestSchema
) -> None:
    create_customers(oracle_conn, oracle_schema.owner, 1)
    shape = await c.describe(_obj(oracle_schema, "CUSTOMERS"))
    with pytest.raises(ConnectorError) as err:
        await c.writer(make_target(shape, write_mode="files", object=None), shape)
    assert err.value.code == "target_invalid"


def test_customer_rows_are_synthetic() -> None:
    rows = customer_rows(3)
    assert all(str(r[3]).endswith("@example.com") for r in rows)
