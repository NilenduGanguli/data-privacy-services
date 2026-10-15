"""The D-A post-write identifier check reads every row on PostgreSQL, Oracle and MongoDB
(S1-M3 post-merge verification r3, engines B1).

The check read one ``CHECK_BATCH`` page (5 000 rows) of each side on Oracle and MongoDB yet said
``scope: whole_table``: it emptied every page it had counted, and those connectors read the
emptied list again to decide whether to page on. A real identifier past row 5 000 of the target,
or whose source row lay past row 5 000 of the source, was never flagged, and the evidence said
``passed``. Here each engine holds a scratch target and source of 20 000 generated rows (no demo
or customer value). Exactly one target account number equals a source one (target row 15 000,
source row 18 000), and exactly one target national id equals a source one and is issuable
(target row 17 500, source row 19 000); every other target national id lies in a never-issued
range. The check flags both columns with counts of exactly 1, and its counts say what it read:
``checked`` = ``rows_read`` = 20 000 on both sides. ``key_floor`` reads every page of the key, and
an append check after a floor past the first page reads exactly the rows after it. Key-less
targets too: an Oracle table without a key (paged on ``ROWID``) and a MongoDB collection whose
document ``_id``s cannot be range-paged (one pass, held to its count).

docker compose --profile oracle --profile sources up -d oracle mongo source-pg
DPS_TEST_REQUIRE_SERVICES=1 pytest -q -m "oracle or mongo or pg_source" \
    tests/connectors/test_identifier_check_engines.py
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
import pytest_asyncio

from dps.config import Settings
from dps.jobs_models import ConnectionParams
from dps.layers.connectors.base import Connector, ObjectRef
from dps.layers.connectors.identifier_check import CHECK_BATCH, check_identifiers, key_floor
from tests.fixtures.m3_services import OracleTestSchema
from tests.fixtures.sources import SourcePg

pytestmark = [pytest.mark.integration]

N = 20_000
ACCOUNT_AT, ACCOUNT_SOURCE_AT = 15_000, 18_000  # 1-based rows (= integer keys)
NATIONAL_AT, NATIONAL_SOURCE_AT = 17_500, 19_000
FLOOR = 5_000  # an append's pre-run key: the run "wrote" rows 5 001-20 000


def _rows() -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """``(account number, national id)`` of rows 1..N: target and source (generated values).

    Target national ids are 12 digits with a leading 1 (an Aadhaar shape no one is issued);
    source ones lead with 4 (issuable). Two target values are planted from the source."""
    target = [(f"TGT{i:09d}", f"1{i:011d}") for i in range(1, N + 1)]
    source = [(f"SRC{i:09d}", f"4{i:011d}") for i in range(1, N + 1)]
    target[ACCOUNT_AT - 1] = (source[ACCOUNT_SOURCE_AT - 1][0], target[ACCOUNT_AT - 1][1])
    target[NATIONAL_AT - 1] = (target[NATIONAL_AT - 1][0], source[NATIONAL_SOURCE_AT - 1][1])
    return target, source


def _assert_whole(out: dict[str, Any], account: str, national: str) -> None:
    """Every row of both sides read; both planted values flagged, nothing else."""
    assert N > 3 * CHECK_BATCH  # the hits sit pages past the first one
    assert out["scope"] == "whole_table" and "scope_reason" not in out
    assert out["source_compared"] is True
    assert out["columns"][account] == {
        "entity_type": "ACCOUNT_NUMBER",
        "checked": N,
        "in_source": 1,
        "issuable": 0,
    }
    assert out["columns"][national] == {
        "entity_type": "NATIONAL_ID",
        "checked": N,
        "in_source": 1,
        "issuable": 1,
    }
    assert sorted(out["review_columns"]) == sorted([account, national])
    assert out["rows_read"] == {"target": N, "source": N}


def _assert_after_floor(out: dict[str, Any], account: str, national: str) -> None:
    written = N - FLOOR
    assert out["scope"] == "written_rows"
    assert out["columns"][account]["checked"] == written
    assert out["columns"][account]["in_source"] == 1  # row 15 000 is after the floor
    assert out["columns"][national]["checked"] == written
    assert (out["columns"][national]["in_source"], out["columns"][national]["issuable"]) == (1, 1)
    assert out["rows_read"] == {"target": written, "source": N}


# -- PostgreSQL (the control: it always yielded a fresh list) -----------------------------------


@pytest.mark.pg_source
async def test_postgres_checks_every_row(source_pg: SourcePg, settings: Settings) -> None:
    from dps.layers.connectors.postgres import PostgresConnector

    target, source = _rows()
    ddl = "id bigint PRIMARY KEY, account_number varchar(20), national_id varchar(12)"
    tgt = await source_pg.table("idc_target", ddl, [(i, *v) for i, v in enumerate(target, 1)])
    src = await source_pg.table("idc_source", ddl, [(i, *v) for i, v in enumerate(source, 1)])
    c: Connector = PostgresConnector(
        source_pg.connection(),
        source_pg.credentials("dps_reader"),
        settings,
        action="test",
        job_id="j_idcheck",
    )
    await c.open()
    try:
        tshape, sshape = await c.describe(tgt), await c.describe(src)
        columns = {"account_number": "ACCOUNT_NUMBER", "national_id": "NATIONAL_ID"}
        out = await check_identifiers(
            columns, target=c, target_shape=tshape, source=c, source_shape=sshape
        )
        _assert_whole(out, "account_number", "national_id")
        assert await key_floor(c, tshape) == (N,)
        appended = await check_identifiers(
            columns,
            target=c,
            target_shape=tshape,
            source=c,
            source_shape=sshape,
            after_key=(FLOOR,),
            expected_rows=N - FLOOR,
        )
        _assert_after_floor(appended, "account_number", "national_id")
    finally:
        await c.close()


# -- Oracle ---------------------------------------------------------------------------------------


@pytest.fixture
def _ora_clean(oracle_admin: Any, oracle_schema: OracleTestSchema) -> Iterator[None]:
    from tests.connectors.oracle_fixtures import drop_all

    drop_all(oracle_admin, oracle_schema.owner)
    yield
    drop_all(oracle_admin, oracle_schema.owner)


@pytest_asyncio.fixture
async def _ora(oracle_schema: OracleTestSchema) -> AsyncIterator[Connector]:
    """The read-only login (``SELECT`` granted per table), as a run's finalize reads."""
    from dps.layers.connectors.oracle import OracleConnector

    c = OracleConnector.from_secret(
        {
            "username": oracle_schema.ro_user,
            "password": oracle_schema.ro_password,
            "dsn": oracle_schema.dsn,
        },
        ConnectionParams(),
        Settings(_env_file=None),
        action="test",
        client_id="j_idcheck",
    )
    await c.open()
    try:
        yield c
    finally:
        await c.close()


def _ora_table(
    admin: Any, schema: OracleTestSchema, name: str, rows: list[Any], *, key: bool = True
) -> ObjectRef:
    owner = schema.owner
    with admin.cursor() as cur:
        cur.execute(
            f'CREATE TABLE "{owner}"."{name}" (ID NUMBER(12){" PRIMARY KEY" if key else ""}, '
            "ACCOUNT_NUMBER VARCHAR2(20), NATIONAL_ID VARCHAR2(12))"
        )
        cur.executemany(
            f'INSERT INTO "{owner}"."{name}" (ID, ACCOUNT_NUMBER, NATIONAL_ID) VALUES (:1, :2, :3)',
            [(i, *v) for i, v in enumerate(rows, 1)],
        )
    admin.commit()
    schema.grant_select(admin, name)
    return ObjectRef(owner=owner, name=name)


@pytest.mark.oracle
@pytest.mark.usefixtures("_ora_clean")
async def test_oracle_checks_every_row(
    oracle_admin: Any, oracle_schema: OracleTestSchema, _ora: Connector
) -> None:
    target, source = _rows()
    tgt = _ora_table(oracle_admin, oracle_schema, "IDC_TARGET", target)
    src = _ora_table(oracle_admin, oracle_schema, "IDC_SOURCE", source)
    tshape, sshape = await _ora.describe(tgt), await _ora.describe(src)
    columns = {"ACCOUNT_NUMBER": "ACCOUNT_NUMBER", "NATIONAL_ID": "NATIONAL_ID"}
    out = await check_identifiers(
        columns, target=_ora, target_shape=tshape, source=_ora, source_shape=sshape
    )
    _assert_whole(out, "ACCOUNT_NUMBER", "NATIONAL_ID")
    assert await key_floor(_ora, tshape) == (N,)  # every page of the key, not the first one
    appended = await check_identifiers(
        columns,
        target=_ora,
        target_shape=tshape,
        source=_ora,
        source_shape=sshape,
        after_key=(FLOOR,),
        expected_rows=N - FLOOR,
    )
    _assert_after_floor(appended, "ACCOUNT_NUMBER", "NATIONAL_ID")
    # a key-less target (a table load of a key-less source creates one) is paged on ROWID
    bare = _ora_table(oracle_admin, oracle_schema, "IDC_TARGET_ROWID", target, key=False)
    bshape = await _ora.describe(bare)
    assert bshape.key_source == "rowid"
    keyless = await check_identifiers(
        columns, target=_ora, target_shape=bshape, source=_ora, source_shape=sshape
    )
    _assert_whole(keyless, "ACCOUNT_NUMBER", "NATIONAL_ID")


def _ora_spelt(
    admin: Any, schema: OracleTestSchema, name: str, rows: list[Any], names: tuple[str, ...]
) -> ObjectRef:
    """A table whose key and columns are quoted names the catalogue holds as written."""
    owner = schema.owner
    key, account, national = names
    with admin.cursor() as cur:
        cur.execute(
            f'CREATE TABLE "{owner}"."{name}" ("{key}" NUMBER(12) PRIMARY KEY, '
            f'"{account}" VARCHAR2(20), "{national}" VARCHAR2(12))'
        )
        cur.executemany(
            f'INSERT INTO "{owner}"."{name}" ("{key}", "{account}", "{national}") '
            "VALUES (:1, :2, :3)",
            [(i, *v) for i, v in enumerate(rows, 1)],
        )
    admin.commit()
    schema.grant_select(admin, name)
    return ObjectRef(owner=owner, name=name)


@pytest.mark.oracle
@pytest.mark.usefixtures("_ora_clean")
async def test_oracle_reads_columns_as_the_catalogue_spells_them(
    oracle_admin: Any, oracle_schema: OracleTestSchema, _ora: Connector
) -> None:
    """S8-idcheck review r1: a column list is a person's selection, which Oracle folds to upper
    case, so a mixed-case column was never read (``checked`` 0, ``whole_table``, passed on a
    target holding a real identifier) and a name with a space was refused (the run's finalize
    failed until the job was dead). Quoted names are read as the catalogue spells them — a
    mixed-case key too, every page of it."""
    target, source = _rows()
    names = ("RowKey", "acctNo", "National Id")
    tgt = _ora_spelt(oracle_admin, oracle_schema, "IDC_SPELT_TARGET", target, names)
    src = _ora_spelt(oracle_admin, oracle_schema, "IDC_SPELT_SOURCE", source, names)
    tshape, sshape = await _ora.describe(tgt), await _ora.describe(src)
    assert [c.name for c in tshape.columns] == list(names) and tshape.key == ["RowKey"]
    columns = {"acctNo": "ACCOUNT_NUMBER", "National Id": "NATIONAL_ID"}
    out = await check_identifiers(
        columns, target=_ora, target_shape=tshape, source=_ora, source_shape=sshape
    )
    _assert_whole(out, "acctNo", "National Id")
    assert await key_floor(_ora, tshape) == (N,)
    appended = await check_identifiers(
        columns,
        target=_ora,
        target_shape=tshape,
        source=_ora,
        source_shape=sshape,
        after_key=(FLOOR,),
        expected_rows=N - FLOOR,
    )
    _assert_after_floor(appended, "acctNo", "National Id")


# -- MongoDB --------------------------------------------------------------------------------------


@pytest.mark.mongo
async def test_mongo_checks_every_document(mongo_client: Any, mongo_db: str) -> None:
    """Document leaves keyed by path; the target's ``_id``s are ObjectIds (what a synthesize
    writes), the source's integers (``key_floor`` reads every page of them)."""
    from bson import ObjectId

    from tests.connectors.mongo_fixtures import connector
    from tests.fixtures.m3_services import mongo_uri

    target, source = _rows()

    def doc(_id: Any, account: str, national: str) -> dict[str, Any]:
        return {"_id": _id, "kyc": {"nationalId": national}, "accounts": [{"number": account}]}

    db = mongo_client[mongo_db]
    db["target"].insert_many([doc(ObjectId(f"{i:024x}"), *v) for i, v in enumerate(target, 1)])
    db["source"].insert_many([doc(i, *v) for i, v in enumerate(source, 1)])
    c = connector(mongo_uri(), mongo_db)
    await c.open()
    try:
        tshape = await c.describe(ObjectRef(name="target"))
        sshape = await c.describe(ObjectRef(name="source"))
        assert tshape.key == ["_id"] and sshape.key == ["_id"]
        account, national = "$.accounts[*].number", "$.kyc.nationalId"
        columns = {account: "ACCOUNT_NUMBER", national: "NATIONAL_ID"}
        out = await check_identifiers(
            columns, target=c, target_shape=tshape, source=c, source_shape=sshape
        )
        _assert_whole(out, account, national)
        assert await key_floor(c, sshape) == (N,)
        # a key-less collection (document _ids cannot be range-paged): one pass, held to its count
        db["target_keyless"].insert_many([doc({"n": i}, *v) for i, v in enumerate(target, 1)])
        kshape = await c.describe(ObjectRef(name="target_keyless"))
        assert kshape.key == []
        keyless = await check_identifiers(
            columns, target=c, target_shape=kshape, source=c, source_shape=sshape
        )
        _assert_whole(keyless, account, national)
    finally:
        await c.close()


@pytest.mark.mongo
async def test_mongo_reads_fields_a_projection_cannot_name(
    mongo_client: Any, mongo_db: str
) -> None:
    """S8-idcheck review r1: a read projects the top-level field of each listed path, so a field
    name with a hyphen was refused as a selection (the run's finalize failed until the job was
    dead) and one holding a dot was projected as the field before the dot (``checked`` 0,
    passed). Such fields are read from whole documents."""
    from bson import ObjectId

    from tests.connectors.mongo_fixtures import connector
    from tests.fixtures.m3_services import mongo_uri

    target, source = _rows()

    def doc(_id: Any, account: str, national: str) -> dict[str, Any]:
        return {"_id": _id, "acct.no": account, "tax-id": national}

    db = mongo_client[mongo_db]
    db["target"].insert_many([doc(ObjectId(f"{i:024x}"), *v) for i, v in enumerate(target, 1)])
    db["source"].insert_many([doc(i, *v) for i, v in enumerate(source, 1)])
    c = connector(mongo_uri(), mongo_db)
    await c.open()
    try:
        tshape = await c.describe(ObjectRef(name="target"))
        sshape = await c.describe(ObjectRef(name="source"))
        assert {col.name for col in tshape.columns} == {"_id", "acct.no", "tax-id"}
        columns = {"acct.no": "ACCOUNT_NUMBER", "tax-id": "NATIONAL_ID"}
        out = await check_identifiers(
            columns, target=c, target_shape=tshape, source=c, source_shape=sshape
        )
        _assert_whole(out, "acct.no", "tax-id")
    finally:
        await c.close()
