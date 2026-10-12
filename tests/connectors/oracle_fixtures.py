"""DDL and rows for Oracle integration tests — synthetic values only (RFC 2606 e-mails, test ranges).

The Oracle test owner is one schema per pytest session (``tests/fixtures/m3_services.py``), so every
module that creates tables calls :func:`drop_all` before and after each test: table names can then
repeat across modules without colliding, and listings see only the test's own tables.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from dps.layers.connectors.base import ColumnInfo, KeyRange, ResolvedTarget, TableShape

CUSTOMERS_DDL = """CREATE TABLE "{o}"."CUSTOMERS" (
  CUSTOMER_ID NUMBER(12) NOT NULL, FULL_NAME VARCHAR2(120 CHAR) NOT NULL, TAX_ID CHAR(10),
  EMAIL VARCHAR2(254), DOB DATE, SEGMENT VARCHAR2(16) NOT NULL, INCOME NUMBER(14,2), NOTES CLOB,
  CREATED_AT TIMESTAMP(6) DEFAULT SYSTIMESTAMP NOT NULL, LAST_CONTACT_AT TIMESTAMP(9),
  CONSTRAINT "{o}_CUST_PK" PRIMARY KEY (CUSTOMER_ID))"""
FF9 = "'YYYY-MM-DD\"T\"HH24:MI:SS.FF9'"


def customer_rows(n: int, start: int = 10_000_001) -> list[tuple[object, ...]]:
    """``n`` synthetic customers; every third one (``i % 3 == 0``) is ``premium``."""
    base = dt.datetime(2025, 1, 1, 9, 0, 0, 250_000)
    return [
        (
            start + i,
            f"Person {i:05d}",
            f"ABCDE{i % 10000:04d}F"[:9],  # 9 characters in CHAR(10): stored blank-padded
            f"person.{i}@example.com",
            dt.datetime(1980, 1, 1) + dt.timedelta(days=i),
            "retail" if i % 3 else "premium",
            Decimal(f"{1000 + i}.25"),
            f"Note {i} for person.{i}@example.com",
            base + dt.timedelta(seconds=i, microseconds=i),
            f"2025-02-01T10:00:00.{i % 1000:06d}123",
        )
        for i in range(n)
    ]


def create_customers(conn: Any, owner: str, n: int, start: int = 10_000_001) -> None:
    """Create ``"<owner>"."CUSTOMERS"`` and load ``n`` rows (timestamps typed, TIMESTAMP(9) as FF9)."""
    import oracledb

    with conn.cursor() as cur:
        cur.execute(CUSTOMERS_DDL.format(o=owner))
        cur.setinputsizes(
            None, None, None, None, None, None, None, oracledb.DB_TYPE_CLOB,
            oracledb.DB_TYPE_TIMESTAMP, None,
        )  # fmt: skip
        cur.executemany(
            f'INSERT INTO "{owner}"."CUSTOMERS" (CUSTOMER_ID, FULL_NAME, TAX_ID, EMAIL, DOB, '
            "SEGMENT, INCOME, NOTES, CREATED_AT, LAST_CONTACT_AT) VALUES (:1, :2, :3, :4, :5, :6, "
            f":7, :8, :9, TO_TIMESTAMP(:10, {FF9}))",
            customer_rows(n, start),
        )
    conn.commit()


def drop_all(admin: Any, owner: str) -> None:
    """Drop every table and sequence of the test owner (SYSTEM session, autocommit)."""
    with admin.cursor() as cur:
        cur.execute("SELECT table_name FROM all_tables WHERE owner = :o", {"o": owner})
        for (name,) in cur.fetchall():
            cur.execute(f'DROP TABLE "{owner}"."{name}" CASCADE CONSTRAINTS PURGE')
        cur.execute(
            "SELECT sequence_name FROM all_sequences WHERE sequence_owner = :o "
            "AND sequence_name NOT LIKE 'ISEQ$$%'",
            {"o": owner},
        )
        for (name,) in cur.fetchall():
            cur.execute(f'DROP SEQUENCE "{owner}"."{name}"')


def ranges_from_keys(
    keys: list[tuple[Any, ...]], parts: int, keycols: list[ColumnInfo]
) -> list[KeyRange]:
    """``KeyRange``s cut at sampled-key quantiles (encoded, as M2's partitioner stores them)."""
    from dps.layers.connectors.base import KeyRange, encode_key

    cuts = sorted(set(keys))
    step = max(1, len(cuts) // parts)
    bounds = [cuts[i] for i in range(step, len(cuts), step)][: parts - 1]
    edges: list[tuple[Any, ...] | None] = [None, *bounds, None]
    return [
        KeyRange(
            lower=None if lo is None else encode_key(lo, keycols),
            upper=None if hi is None else encode_key(hi, keycols),
        )
        for lo, hi in zip(edges[:-1], edges[1:], strict=True)
    ]


def make_target(shape: TableShape, **kw: Any) -> ResolvedTarget:
    """M2's ``ResolvedTarget`` for ``shape`` with test defaults (engine-neutral; Mongo reuses it).

    Defaults: ``job_id="j_test"``, ``connection_id="c_test"``, ``engine="oracle"``, ``object`` =
    the shape's, ``same_as_source`` = whether ``object`` is the shape's, ``table_load=
    "truncate_insert"``, ``create_if_missing=True``, ``key`` = the shape's key minus pseudo columns,
    ``write_columns`` = every non-pseudo column (pass the SET list for ``in_place``),
    ``generated_key`` = ``pk_strategy`` is ``generate``, ``target_shape`` = ``shape``.
    """
    from dps.layers.connectors.base import PkGenerate, ResolvedTarget

    obj = kw.pop("object", shape.object)
    strategy = kw.pop("pk_strategy", None)
    pseudo = {c.name for c in shape.columns if c.pseudo}
    fields: dict[str, Any] = {
        "job_id": "j_test",
        "write_mode": kw.pop("write_mode"),
        "connection_id": "c_test",
        "engine": kw.pop("engine", "oracle"),
        "object": obj,
        "same_as_source": kw.pop("same_as_source", obj == shape.object),
        "table_load": kw.pop("table_load", "truncate_insert"),
        "pk_strategy": strategy,
        "create_if_missing": kw.pop("create_if_missing", True),
        "ledger_owner": kw.pop("ledger_owner", None),
        "key": kw.pop("key", [k for k in shape.key if k not in pseudo]),
        "write_columns": kw.pop("write_columns", [c.name for c in shape.columns if not c.pseudo]),
        "generated_key": kw.pop("generated_key", isinstance(strategy, PkGenerate)),
        "target_shape": kw.pop("target_shape", shape),
    }
    fields.update(kw)
    return ResolvedTarget(**fields)
