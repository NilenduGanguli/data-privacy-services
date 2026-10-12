"""The pure Oracle statements of ``oracle_sql`` executed on the lane's Oracle Free.

The offline tests pin each statement's text; these run them, so a statement that Oracle refuses or
that pages wrongly fails here before a connector is built on it. They also measure the write
ledger's fence under real concurrency (contract §0 amendment 2026-10-12): a second holder that waited
for the row lock re-reads and matches 0 rows instead of applying a batch twice.

    docker compose --profile oracle up -d oracle
    DPS_TEST_REQUIRE_SERVICES=1 pytest -q -m oracle tests/connectors/test_oracle_sql_live.py
"""

from __future__ import annotations

import json
import threading
from typing import Any

import pytest

from dps.layers.connectors import oracle_sql as osql
from dps.layers.connectors.base import ObjectRef, TableShape
from tests.fixtures.m3_services import OracleTestSchema

oracledb = pytest.importorskip("oracledb")

pytestmark = [pytest.mark.integration, pytest.mark.oracle]

TS9 = osql.TS9_FORMAT


def _cat(name: str, data_type: str, **kw: Any) -> osql.CatalogColumn:
    base: dict[str, Any] = dict(
        data_length=22,
        char_length=0,
        char_used=None,
        data_precision=None,
        data_scale=None,
        nullable=True,
        has_default=False,
        is_identity=False,
    )
    base.update(kw)
    return osql.CatalogColumn(name=name, data_type=data_type, **base)


CID = osql.column_info(_cat("CUSTOMER_ID", "NUMBER", data_precision=12, data_scale=0), is_key=True)
EVENT_TS = osql.column_info(_cat("EVENT_TS", "TIMESTAMP(9)", data_scale=9), is_key=True)
NOTE = osql.column_info(
    _cat("NOTE", "VARCHAR2", data_length=20, char_length=20, char_used="B"), is_key=False
)
DOC_REF = osql.column_info(
    _cat("DOC_REF", "CHAR", data_length=12, char_length=12, char_used="B"), is_key=True
)
NAME = osql.column_info(
    _cat("FULL_NAME", "VARCHAR2", data_length=480, char_length=120, char_used="C"), is_key=False
)
NOTES = osql.column_info(_cat("NOTES", "CLOB", data_length=4000), is_key=False)


def _code(exc: BaseException) -> str:
    return str(getattr(exc.args[0], "full_code", "")) if exc.args else ""


def _page_all(
    conn: Any, shape: TableShape, batch: int, bind_types: dict[str, Any] | None = None
) -> list[tuple[Any, ...]]:
    """Every row through keyset pages of ``batch`` rows (``after`` = the last row's key)."""
    first = osql.read_sql(
        shape,
        columns=shape.columns,
        where=None,
        query=None,
        sample=None,
        lower=False,
        upper=False,
        after=False,
    )
    nxt = osql.read_sql(
        shape,
        columns=shape.columns,
        where=None,
        query=None,
        sample=None,
        lower=False,
        upper=False,
        after=True,
    )
    key_pos = [shape.columns.index(k) for k in shape.key_columns()]
    out: list[tuple[Any, ...]] = []
    after: tuple[Any, ...] | None = None
    while True:
        with conn.cursor() as cur:
            if after is None:
                cur.execute(first.sql, {"batch": batch})
            else:
                binds: dict[str, Any] = {"batch": batch}
                for i, value in enumerate(after):
                    binds[f"af{i}"] = value
                if bind_types:
                    cur.setinputsizes(**bind_types)
                cur.execute(nxt.sql, binds)
            page = cur.fetchall()
        if not page:
            return out
        out.extend(page)
        after = tuple(page[-1][p] for p in key_pos)


def test_keyset_pages_through_a_burst_inside_one_microsecond(
    oracle_conn: Any, oracle_schema: OracleTestSchema
) -> None:
    with oracle_conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE BURST (CUSTOMER_ID NUMBER(12) NOT NULL, EVENT_TS TIMESTAMP(9) NOT NULL, "
            "NOTE VARCHAR2(20), CONSTRAINT BURST_PK PRIMARY KEY (CUSTOMER_ID, EVENT_TS))"
        )
        rows = [(1, f"2025-01-01T10:00:00.123456{n:03d}", f"n{n}") for n in range(7)]
        rows += [
            (2, "2025-01-01T10:00:00.000000001", "x"),
            (0, "2024-12-31T23:59:59.999999999", "y"),
        ]
        cur.executemany(f"INSERT INTO BURST VALUES (:1, TO_TIMESTAMP(:2, '{TS9}'), :3)", rows)
    oracle_conn.commit()
    shape = TableShape(
        object=ObjectRef(owner=oracle_schema.owner, name="BURST"),
        columns=[CID, EVENT_TS, NOTE],
        key=["CUSTOMER_ID", "EVENT_TS"],
        key_source="primary_key",
    )
    got = _page_all(oracle_conn, shape, batch=2)
    # every row exactly once, in key order, with its nanoseconds intact
    assert got == sorted(rows)


def test_char_keys_page_with_char_binds(oracle_conn: Any, oracle_schema: OracleTestSchema) -> None:
    with oracle_conn.cursor() as cur:
        cur.execute("CREATE TABLE KYC (DOC_REF CHAR(12) PRIMARY KEY)")
        cur.executemany("INSERT INTO KYC VALUES (:1)", [("K17",), ("K2",), ("K100",), ("K17A",)])
    oracle_conn.commit()
    shape = TableShape(
        object=ObjectRef(owner=oracle_schema.owner, name="KYC"),
        columns=[DOC_REF],
        key=["DOC_REF"],
        key_source="primary_key",
    )
    plan = osql.read_sql(
        shape,
        columns=shape.columns,
        where=None,
        query=None,
        sample=None,
        lower=False,
        upper=False,
        after=True,
    )
    after = osql.pad_char("K17", 12)
    with oracle_conn.cursor() as cur:
        cur.setinputsizes(af0=oracledb.DB_TYPE_CHAR)
        cur.execute(plan.sql, {"batch": 10, "af0": after})
        typed = [r[0].rstrip() for r in cur.fetchall()]
    assert typed == ["K17A", "K2"]  # blank-padded comparison: K17 itself is not read again
    with oracle_conn.cursor() as cur:
        cur.execute(plan.sql, {"batch": 10, "af0": "K17"})  # an untyped, unpadded VARCHAR2 bind
        untyped = [r[0].rstrip() for r in cur.fetchall()]
    assert untyped == ["K17", "K17A", "K2"]  # the pitfall the CHAR bind avoids: K17 again
    got = _page_all(oracle_conn, shape, batch=1, bind_types={"af0": oracledb.DB_TYPE_CHAR})
    assert [r[0].rstrip() for r in got] == ["K100", "K17", "K17A", "K2"]


def test_rowid_tables_page_in_rowid_key_order(
    oracle_conn: Any, oracle_schema: OracleTestSchema
) -> None:
    with oracle_conn.cursor() as cur:
        cur.execute("CREATE TABLE NOKEY (NOTE VARCHAR2(20))")
        cur.executemany("INSERT INTO NOKEY VALUES (:1)", [(f"r{i}",) for i in range(25)])
    oracle_conn.commit()
    rid = osql.rowid_column()
    shape = TableShape(
        object=ObjectRef(owner=oracle_schema.owner, name="NOKEY"),
        columns=[NOTE, rid],
        key=["ROWID"],
        key_source="rowid",
    )
    got = _page_all(oracle_conn, shape, batch=4)
    assert sorted(r[0] for r in got) == sorted(f"r{i}" for i in range(25))
    rowids = [r[1] for r in got]
    assert len(set(rowids)) == 25
    assert sorted(rowids, key=osql.rowid_to_key) == rowids  # DB order == the codec's order
    assert all(osql.key_to_rowid(osql.rowid_to_key(r)) == r for r in rowids)
    lower = osql.read_sql(
        shape,
        columns=shape.columns,
        where=None,
        query=None,
        sample=None,
        lower=True,
        upper=True,
        after=False,
    )
    with oracle_conn.cursor() as cur:
        cur.execute(lower.sql, {"batch": 100, "lo0": rowids[5], "hi0": rowids[10]})
        assert [r[1] for r in cur.fetchall()] == rowids[5:10]  # [lower, upper)


def test_count_and_sample_statements_run(oracle_conn: Any, oracle_schema: OracleTestSchema) -> None:
    with oracle_conn.cursor() as cur:
        cur.execute("CREATE TABLE SAMPLED (CUSTOMER_ID NUMBER(12) PRIMARY KEY, NOTE VARCHAR2(20))")
        cur.executemany(
            "INSERT INTO SAMPLED VALUES (:1, :2)",
            [(i, "even" if i % 2 else "odd") for i in range(200)],
        )
    oracle_conn.commit()
    shape = TableShape(
        object=ObjectRef(owner=oracle_schema.owner, name="SAMPLED"),
        columns=[CID, NOTE],
        key=["CUSTOMER_ID"],
        key_source="primary_key",
    )
    with oracle_conn.cursor() as cur:
        cur.execute(osql.count_sql(shape, where="NOTE = :n", query=None, sample=None), {"n": "odd"})
        assert cur.fetchone() == (100,)
        keys = osql.sample_sql(
            shape, columns=shape.columns, where=None, query=None, percent=50.0, keys_only=True
        )
        cur.execute(keys.sql, {"n": 1000})
        sampled = [r[0] for r in cur.fetchall()]
        assert sampled == sorted(sampled) and 0 < len(sampled) < 200
        rows = osql.sample_sql(shape, columns=shape.columns, where=None, query=None, percent=None)
        cur.execute(rows.sql, {"n": 7})
        assert len(cur.fetchall()) == 7
        seeded = osql.read_sql(
            shape,
            columns=shape.columns,
            where=None,
            query=None,
            sample=(30.0, 7),
            lower=False,
            upper=False,
            after=False,
        )
        cur.execute(seeded.sql, {"batch": 1000})
        once = cur.fetchall()
        cur.execute(seeded.sql, {"batch": 1000})
        assert cur.fetchall() == once  # SEED makes the sample repeatable


def test_write_statements_run_and_are_idempotent(
    oracle_conn: Any, oracle_schema: OracleTestSchema
) -> None:
    target = ObjectRef(owner=oracle_schema.owner, name="CUSTOMERS_M")
    ddl = osql.create_table_sql(target, [(CID, CID), (NAME, NAME), (NOTES, NOTES)], ["CUSTOMER_ID"])
    with oracle_conn.cursor() as cur:
        cur.execute(ddl)
        cur.execute(f'CREATE SEQUENCE "{oracle_schema.owner}"."CASE_SEQ" START WITH 1000')
        merge = osql.merge_sql(target, [CID, NAME, NOTES], ["CUSTOMER_ID"])
        rows = [{"v0": i, "v1": f"name {i}", "v2": "note " * 900 + str(i)} for i in range(5)]
        for _ in range(2):  # a table reload twice is the same table
            cur.setinputsizes(v2=oracledb.DB_TYPE_CLOB)
            cur.executemany(merge, rows)
        cur.execute(f'SELECT COUNT(*) FROM "{oracle_schema.owner}"."CUSTOMERS_M"')
        assert cur.fetchone() == (5,)
        upd = osql.update_sql(target, [NAME], [CID])
        cur.executemany(upd, [{"v0": "renamed", "v1": 3}])
        assert cur.rowcount == 1
        ins = osql.insert_sql(
            target, [CID, NAME], sequence=("CUSTOMER_ID", f'"{oracle_schema.owner}"."CASE_SEQ"')
        )
        cur.executemany(ins, [{"v0": "appended"}])
        coll = osql.collision_sql(target, [CID], 3)
        cur.execute(coll, {"c0": 3, "c1": 1000, "c2": 99})
        assert cur.fetchone() == (2,)
        cur.execute(
            f'SELECT CUSTOMER_ID, FULL_NAME, DBMS_LOB.GETLENGTH(NOTES) FROM "{oracle_schema.owner}"'
            '."CUSTOMERS_M" WHERE CUSTOMER_ID IN (3, 1000) ORDER BY 1'
        )
        assert cur.fetchall() == [
            (3, "renamed", len("note " * 900 + "3")),
            (1000, "appended", None),
        ]
    oracle_conn.rollback()


def _ledger_binds(job: str, part: int, batch: int, written: int) -> dict[str, Any]:
    return {
        "job": job,
        "part": part,
        "batch": batch,
        "prev": batch - 1,
        "last_key": json.dumps([{"t": "int", "v": str(batch)}]),
        "written": written,
    }


def _advance(conn: Any, owner: str, job: str, part: int, batch: int) -> int:
    """ensure + guarded mark in the caller's transaction; returns the mark's row count."""
    with conn.cursor() as cur:
        cur.execute(osql.ledger_ensure_sql(owner), {"job": job, "part": part})
        cur.execute(osql.ledger_mark_sql(owner), _ledger_binds(job, part, batch, batch * 10))
        return int(cur.rowcount)


def test_ledger_ddl_ensure_mark_read_purge(
    oracle_conn: Any, oracle_schema: OracleTestSchema
) -> None:
    owner = oracle_schema.owner
    with oracle_conn.cursor() as cur:
        cur.execute(osql.ledger_ddl(owner))
        cur.execute(osql.ledger_ddl(owner))  # ORA-00955 swallowed: idempotent
    assert _advance(oracle_conn, owner, "j_a", 0, 1) == 1
    assert _advance(oracle_conn, owner, "j_a", 0, 2) == 1
    assert _advance(oracle_conn, owner, "j_a", 0, 2) == 0  # a replayed batch is fenced
    assert _advance(oracle_conn, owner, "j_a", 0, 4) == 0  # a skipped batch is fenced
    assert _advance(oracle_conn, owner, "j_a", 1, 2) == 0  # a partition starts at batch 1
    oracle_conn.commit()
    with oracle_conn.cursor() as cur:
        cur.execute(osql.ledger_read_sql(owner), {"job": "j_a", "part": 0})
        batch, last_key, written = cur.fetchone()
        assert (batch, written) == (2, 20)
        assert last_key.read() == '[{"t": "int", "v": "2"}]'  # a CLOB (no type handler here)
        cur.execute(osql.ledger_read_sql(owner), {"job": "j_a", "part": 1})
        assert cur.fetchone()[0] == 0  # ensured, never advanced: read_ledger treats it as no mark
        cur.execute(osql.ledger_purge_sql(owner), {"job": "j_a"})
        assert cur.rowcount == 2
    oracle_conn.commit()


def _other_session(schema: OracleTestSchema) -> Any:
    return oracledb.connect(user=schema.owner, password=schema.owner_password, dsn=schema.dsn)


def test_ledger_fence_holds_under_a_concurrent_holder(
    oracle_conn: Any, oracle_schema: OracleTestSchema
) -> None:
    owner = oracle_schema.owner
    with oracle_conn.cursor() as cur:
        cur.execute(osql.ledger_ddl(owner))
    zombie = _other_session(oracle_schema)
    try:
        # First batch: the zombie's ensure waits on our uncommitted insert, then gets ORA-00001.
        assert _advance(oracle_conn, owner, "j_c", 0, 1) == 1
        outcome: dict[str, Any] = {}

        def first_batch() -> None:
            try:
                outcome["rows"] = _advance(zombie, owner, "j_c", 0, 1)
            except oracledb.DatabaseError as exc:
                outcome["code"] = _code(exc)

        t = threading.Thread(target=first_batch)
        t.start()
        t.join(1.5)
        assert t.is_alive()  # blocked on our open transaction
        oracle_conn.commit()
        t.join(30)
        assert outcome == {"code": "ORA-00001"}
        zombie.rollback()

        # Next batch: both hold mark 2; the zombie waits for the row lock, Oracle restarts its
        # UPDATE after our commit, and the re-read BATCH_NO = 2 no longer matches :prev = 1.
        assert _advance(oracle_conn, owner, "j_c", 0, 2) == 1
        later: dict[str, Any] = {}
        t2 = threading.Thread(
            target=lambda: later.update(rows=_advance(zombie, owner, "j_c", 0, 2))
        )
        t2.start()
        t2.join(1.5)
        assert t2.is_alive()
        oracle_conn.commit()
        t2.join(30)
        assert later == {"rows": 0}
        zombie.rollback()
    finally:
        zombie.close()
