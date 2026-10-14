"""``lookup_values`` (consistency-check probes) on the lane's Oracle, MongoDB and PostgreSQL.

docker compose --profile oracle --profile sources up -d oracle mongo source-pg
DPS_TEST_REQUIRE_SERVICES=1 pytest -q -m "oracle or mongo or pg_source" tests/connectors/test_lookup_values_m3.py
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from dps.config import Settings
from dps.jobs_models import ConnectionParams
from dps.layers.connectors.base import ObjectRef
from dps.layers.connectors.consistency import ValueLookup
from tests.fixtures.m3_services import OracleTestSchema
from tests.fixtures.sources import SourcePg

pytestmark = [pytest.mark.integration]


# -- Oracle ---------------------------------------------------------------------------------------


@pytest.fixture
def _ora_clean(oracle_admin: Any, oracle_schema: OracleTestSchema) -> Iterator[None]:
    from tests.connectors.oracle_fixtures import drop_all

    drop_all(oracle_admin, oracle_schema.owner)
    yield
    drop_all(oracle_admin, oracle_schema.owner)


def _ora(schema: OracleTestSchema, *, read_only: bool = True) -> Any:
    from dps.layers.connectors.oracle import OracleConnector

    return OracleConnector.from_secret(
        {
            "username": schema.ro_user if read_only else schema.owner,
            "password": schema.ro_password if read_only else schema.owner_password,
            "dsn": schema.dsn,
        },
        ConnectionParams(),
        Settings(_env_file=None),
        action="consistency",
        client_id="j_lookup",
    )


@pytest.mark.oracle
@pytest.mark.usefixtures("_ora_clean")
async def test_oracle_char_join_found_by_unpadded_candidates(
    oracle_admin: Any, oracle_schema: OracleTestSchema
) -> None:
    owner = oracle_schema.owner
    with oracle_admin.cursor() as cur:
        cur.execute(
            f'CREATE TABLE "{owner}"."KYC" (DOC_REF CHAR(12) NOT NULL, CUSTOMER_ID NUMBER(12), '
            f'DOC_NUMBER VARCHAR2(32), CONSTRAINT "{owner}_KYC_PK" PRIMARY KEY (DOC_REF))'
        )
        cur.executemany(
            f'INSERT INTO "{owner}"."KYC" VALUES (:1, :2, :3)',
            [(f"K{i}", 10_000_001 + i, f"DOC-{i:05d}") for i in range(1200)],
        )
    oracle_admin.commit()
    oracle_schema.grant_select(oracle_admin, "KYC")
    c = _ora(oracle_schema)
    await c.open()
    try:
        assert isinstance(c, ValueLookup)
        shape = await c.describe(ObjectRef(owner=owner, name="KYC"))
        join, attr = shape.column("DOC_REF"), shape.column("DOC_NUMBER")
        wanted = [f"K{i}" for i in range(0, 1200, 2)] + ["K99999"]  # 600 found + 1 absent
        rows = await c.lookup_values(shape, join, wanted, [attr])  # > IN_LIST_MAX: chunked
        assert len(rows) == 600 and set(rows[0]) == {"DOC_REF", "DOC_NUMBER"}
        assert {r["DOC_REF"].rstrip() for r in rows} == set(wanted[:-1])
        assert all(r["DOC_NUMBER"] == f"DOC-{int(r['DOC_REF'].strip()[1:]):05d}" for r in rows)
        assert await c.lookup_values(shape, join, [], [attr]) == []
    finally:
        await c.close()


@pytest.mark.oracle
@pytest.mark.usefixtures("_ora_clean")
async def test_oracle_number_join_reads_a_clob_attribute(
    oracle_admin: Any, oracle_schema: OracleTestSchema
) -> None:
    from tests.connectors.oracle_fixtures import create_customers

    create_customers(oracle_admin, oracle_schema.owner, 300)
    oracle_schema.grant_select(oracle_admin, "CUSTOMERS")
    c = _ora(oracle_schema)
    await c.open()
    try:
        shape = await c.describe(ObjectRef(owner=oracle_schema.owner, name="CUSTOMERS"))
        join = shape.column("CUSTOMER_ID")
        rows = await c.lookup_values(
            shape, join, [10_000_001, 10_000_101, 99], [shape.column("NOTES"), join]
        )
        assert sorted(int(r["CUSTOMER_ID"]) for r in rows) == [10_000_001, 10_000_101]
        assert all(isinstance(r["NOTES"], str) and "@example.com" in r["NOTES"] for r in rows)
        assert set(rows[0]) == {"CUSTOMER_ID", "NOTES"}
    finally:
        await c.close()


# -- MongoDB --------------------------------------------------------------------------------------


@pytest.mark.mongo
async def test_mongo_dotted_join_with_a_nested_attribute(mongo_client: Any, mongo_db: str) -> None:
    from tests.connectors.mongo_fixtures import connector, profile
    from tests.fixtures.m3_services import mongo_uri

    mongo_client[mongo_db]["profiles"].insert_many([profile(i) for i in range(1, 801)])
    c = connector(mongo_uri(), mongo_db)
    await c.open()
    try:
        shape = await c.describe(ObjectRef(name="profiles"))
        by_path = {col.path: col for col in shape.columns}
        join, name = by_path["$.kyc.taxId"], by_path["$.profile.fullName"]
        wanted = [f"ABCDE{i:04d}F" for i in (1, 2, 3, 500)] + ["ZZZZZ0000Z"]
        rows = await c.lookup_values(shape, join, wanted, [name])
        assert len(rows) == 4
        assert {r["kyc"]["taxId"] for r in rows} == set(wanted[:-1])
        assert all(r["profile"]["fullName"].startswith("Person ") for r in rows)
        assert all(set(r) == {"_id", "kyc", "profile"} for r in rows)  # top-level projection
    finally:
        await c.close()


# -- PostgreSQL -----------------------------------------------------------------------------------


@pytest.mark.pg_source
async def test_pg_bigint_join_returns_every_matching_row(source_pg: SourcePg) -> None:
    from dps.layers.connectors.postgres import PostgresConnector

    rows = [(k, 10_000_001 + k // 3, f"Payer {k // 3}") for k in range(600)]
    obj = await source_pg.table(
        "payments", "payment_id bigint PRIMARY KEY, customer_id bigint, payer_name text", rows
    )
    c = PostgresConnector(
        source_pg.connection(user="dps_reader", roles=("source",)),
        source_pg.credentials("dps_reader"),
        Settings(_env_file=None),
        action="consistency",
        job_id="j_lookup",
    )
    await c.open()
    try:
        shape = await c.describe(obj)
        join = shape.column("customer_id")
        found = await c.lookup_values(
            shape, join, [10_000_001, 10_000_050, 1], [shape.column("payer_name")]
        )
        assert len(found) == 6 and {r["customer_id"] for r in found} == {10_000_001, 10_000_050}
        assert {r["payer_name"] for r in found} == {"Payer 0", "Payer 49"}
        assert set(found[0]) == {"customer_id", "payer_name"}
    finally:
        await c.close()
