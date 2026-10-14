"""The M3 local stack for real: Oracle Free's demo schemas and logins, the per-session test schema
fixtures, and the MongoDB replica set (transactions need a writable primary).

    docker compose --profile oracle --profile sources up -d oracle mongo
    DPS_TEST_REQUIRE_SERVICES=1 pytest -q -m "oracle or mongo" tests/test_m3_stack.py
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.fixtures.m3_services import OracleTestSchema, oracle_dsn

oracledb = pytest.importorskip("oracledb")
pymongo = pytest.importorskip("pymongo")

DEMO_TABLES = {
    ("CRM", "ADDRESS_HISTORY"),
    ("CRM", "CUSTOMERS"),
    ("CRM", "CUSTOMER_EVENTS"),
    ("CRM", "INTERACTIONS"),
    ("CRM", "KYC_DOCUMENTS"),
    ("LOANS", "APPLICANTS"),
}


def _code(exc: BaseException) -> str:
    return str(getattr(exc.args[0], "full_code", "")) if exc.args else ""


@pytest.mark.integration
@pytest.mark.oracle
def test_dps_ro_sees_the_demo_tables_and_cannot_write(oracle_admin: Any) -> None:
    # oracle_admin waits for the PDB (and skips/fails with the start hint when it is down).
    conn = oracledb.connect(user="dps_ro", password="dps_ro", dsn=oracle_dsn())
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT owner, table_name FROM all_tables WHERE owner IN ('CRM', 'LOANS')")
            assert set(cur.fetchall()) == DEMO_TABLES
            cur.execute(
                "SELECT column_name, data_type, data_length FROM all_tab_cols "
                "WHERE owner = 'CRM' AND table_name = 'CUSTOMERS' "
                "AND column_name IN ('TAX_ID', 'NOTES', 'LAST_CONTACT_AT')"
            )
            cols = {name: (dtype, length) for name, dtype, length in cur.fetchall()}
            assert cols["TAX_ID"] == ("CHAR", 10) and cols["NOTES"][0] == "CLOB"
            assert cols["LAST_CONTACT_AT"][0] == "TIMESTAMP(9)"
            with pytest.raises(oracledb.DatabaseError) as denied:
                cur.execute(
                    "INSERT INTO CRM.ADDRESS_HISTORY (CUSTOMER_ID, VALID_FROM) "
                    "VALUES (1, DATE '2024-01-01')"
                )
            # insufficient privileges: 19c says ORA-01031, 23ai the finer ORA-41900
            assert _code(denied.value) in ("ORA-01031", "ORA-41900")
    finally:
        conn.close()


@pytest.mark.integration
@pytest.mark.oracle
def test_test_schema_is_least_privilege(
    oracle_admin: Any, oracle_schema: OracleTestSchema, oracle_conn: Any
) -> None:
    with oracle_conn.cursor() as cur:
        cur.execute("CREATE TABLE STACK_PROBE (ID NUMBER(10) PRIMARY KEY)")
        cur.execute("INSERT INTO STACK_PROBE (ID) VALUES (1)")
    oracle_conn.commit()
    ro = oracledb.connect(
        user=oracle_schema.ro_user, password=oracle_schema.ro_password, dsn=oracle_schema.dsn
    )
    try:
        sql = f'SELECT COUNT(*) FROM "{oracle_schema.owner}".STACK_PROBE'
        with ro.cursor() as cur, pytest.raises(oracledb.DatabaseError) as missing:
            cur.execute(sql)
        assert _code(missing.value) == "ORA-00942"  # no grant yet: the table is invisible
        oracle_schema.grant_select(oracle_admin, "stack_probe")
        with ro.cursor() as cur:
            cur.execute(sql)
            assert cur.fetchone() == (1,)
    finally:
        ro.close()
    assert "password" not in repr(oracle_schema).lower()


@pytest.mark.integration
@pytest.mark.mongo
def test_mongo_is_a_writable_primary_with_transactions(mongo_client: Any, mongo_db: str) -> None:
    hello = mongo_client.admin.command("hello")
    assert hello["setName"] == "rs0" and hello["isWritablePrimary"] is True
    coll = mongo_client[mongo_db]["probe"]
    coll.insert_one({"_id": 0})  # collections are created outside the transaction
    with mongo_client.start_session() as session:
        with session.start_transaction():
            coll.insert_one({"_id": 1}, session=session)
            coll.insert_one({"_id": 2}, session=session)
            session.abort_transaction()
    assert coll.count_documents({}) == 1
    with mongo_client.start_session() as session:
        session.with_transaction(lambda s: coll.insert_one({"_id": 3}, session=s))
    assert sorted(d["_id"] for d in coll.find({})) == [0, 3]
