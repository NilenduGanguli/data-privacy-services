"""The L2 engine bridge (M2 ``BatchTransformer``) on real Oracle and MongoDB rows (contract §6.4/§6.5).

docker compose --profile oracle --profile sources up -d oracle mongo
DPS_TEST_REQUIRE_SERVICES=1 pytest -q -m "oracle or mongo" tests/connectors/test_engine_bridge_m3.py
"""

from __future__ import annotations

import copy
import datetime as dt
import re
from collections.abc import AsyncIterator, Iterator
from decimal import Decimal
from typing import Any

import pytest

pytest.importorskip("fastfpe")
oracledb = pytest.importorskip("oracledb")
pytest.importorskip("pymongo")
from bson import Decimal128, Int64, ObjectId  # noqa: E402

from dps.config import Settings  # noqa: E402
from dps.engine.batch import BatchTransformer, ColumnPlan  # noqa: E402
from dps.engine.policy import MethodSpec, load_preset  # noqa: E402
from dps.engine.service import PrivacyEngine  # noqa: E402
from dps.engine.transform.crypto import KeyMaterial, generate_key_material  # noqa: E402
from dps.jobs_models import ConnectionParams, Selection  # noqa: E402
from dps.layers.connectors.base import (  # noqa: E402
    KeyRange,
    LedgerMark,
    ObjectRef,
    Row,
    TableShape,
    encode_key,
)
from dps.layers.connectors.mongo import MongoConnector  # noqa: E402
from dps.layers.connectors.oracle import OracleConnector  # noqa: E402
from tests.connectors.mongo_fixtures import connector as mongo_connector  # noqa: E402
from tests.connectors.mongo_fixtures import profile  # noqa: E402
from tests.connectors.oracle_fixtures import drop_all, make_target  # noqa: E402
from tests.fixtures.m3_services import OracleTestSchema, mongo_uri  # noqa: E402

pytestmark = [pytest.mark.integration]
POLICY = load_preset("gdpr-pseudonymise")
ENGINE = PrivacyEngine(
    Settings(_env_file=None, fpe_enabled=True, llm_provider="stub", ner_enabled=False)
)
PROFILES = ObjectRef(name="profiles")


def _plan(
    shape: TableShape, path: str, entity: str, method: str, *, text: bool = False, **params: Any
) -> ColumnPlan:
    col = next(c for c in shape.columns if c.path == path)
    return ColumnPlan(
        path=path,
        name=col.name,
        entity_type=entity,
        method=MethodSpec(method=method, params=dict(params)),
        text_column=text,
        reversible=method not in ("surrogate", "keep", "redact"),
        kind=col.kind,
        length=col.length,
        precision=col.precision,
        scale=col.scale,
        ts_precision=col.ts_precision,
    )


def _transformer(
    shape: TableShape,
    plans: list[ColumnPlan],
    key: KeyMaterial,
    unclassified: str = "keep",
) -> BatchTransformer:
    return BatchTransformer(
        ENGINE,
        policy=POLICY,
        mode="obfuscate",
        columns=plans,
        shape=shape,
        key=key,
        seed=None,
        locale="en_IN",
        unclassified=unclassified,
    )


def _digits(value: object) -> int:
    return int(re.sub(r"\D", "", str(value)))


def _leaves(value: Any, prefix: str = "$") -> dict[str, Any]:
    """Every leaf of a document with its path (arrays by index)."""
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            out.update(_leaves(v, f"{prefix}.{k}"))
        return out
    if isinstance(value, list):
        out = {}
        for i, v in enumerate(value):
            out.update(_leaves(v, f"{prefix}[{i}]"))
        return out
    return {prefix: value}


# --- Oracle --------------------------------------------------------------------------------------

ORACLE_DDL = """CREATE TABLE "{o}"."BRIDGE" (
  ID NUMBER(12) PRIMARY KEY, NAME VARCHAR2(120 CHAR), AMOUNT NUMBER(14,2), RATIO BINARY_DOUBLE,
  SEEN DATE, CREATED TIMESTAMP(6), EXACT TIMESTAMP(9), RAWV RAW(16), PAN CHAR(10), NOTES CLOB)"""


@pytest.fixture
def oracle_owner(oracle_admin: Any, oracle_schema: OracleTestSchema) -> Iterator[str]:
    drop_all(oracle_admin, oracle_schema.owner)
    yield oracle_schema.owner
    drop_all(oracle_admin, oracle_schema.owner)


@pytest.fixture
async def ora(oracle_schema: OracleTestSchema, oracle_owner: str) -> AsyncIterator[OracleConnector]:
    c = OracleConnector.from_secret(
        {
            "username": oracle_schema.owner,
            "password": oracle_schema.owner_password,
            "dsn": oracle_schema.dsn,
        },
        ConnectionParams(),
        Settings(_env_file=None),
        action="test",
        client_id="j_test",
    )
    await c.open()
    yield c
    await c.close()


def _oracle_rows(conn: Any, owner: str, n: int = 6) -> None:
    with conn.cursor() as cur:
        cur.execute(ORACLE_DDL.format(o=owner))
        cur.setinputsizes(
            None, None, None, None, None, oracledb.DB_TYPE_TIMESTAMP, None, None, None,
            oracledb.DB_TYPE_CLOB,
        )  # fmt: skip
        cur.executemany(
            f'INSERT INTO "{owner}"."BRIDGE" VALUES (:1, :2, :3, :4, :5, :6, '
            "TO_TIMESTAMP(:7, 'YYYY-MM-DD\"T\"HH24:MI:SS.FF9'), :8, :9, :10)",
            [
                (
                    10_000_100 + i,
                    f"Person {i:03d} Krishnan",
                    Decimal(f"{1000 + i}.25"),
                    0.1 + i,
                    dt.datetime(2024, 3, 1, 13, 45, 7),
                    dt.datetime(2025, 1, 1, 9, 0, 0, 123_456 + i),
                    f"2025-02-01T10:00:00.12345678{i}",
                    bytes(range(i, i + 16)),
                    f"KYCREFAB{chr(65 + i)}",  # 9 letters in CHAR(10): stored blank-padded
                    f"Note {i} \U0001f600 for person.{i}@example.com",
                )
                for i in range(n)
            ],
        )
    conn.commit()


async def _ora_read(c: OracleConnector, shape: TableShape) -> list[Row]:
    out: list[Row] = []
    async for page in c.read_partition(
        shape, Selection(), {}, KeyRange(), after_key=None, batch=50
    ):
        out += page
    return out


@pytest.mark.oracle
async def test_untouched_oracle_values_are_identical(
    ora: OracleConnector, oracle_conn: Any, oracle_owner: str
) -> None:
    _oracle_rows(oracle_conn, oracle_owner)
    shape = await ora.describe(ObjectRef(owner=oracle_owner, name="BRIDGE"))
    rows = await _ora_read(ora, shape)
    original = copy.deepcopy(rows)
    key = generate_key_material("key")
    for plans in ([], [_plan(shape, "$.NAME", "PERSON_NAME", "det_token")]):
        out = _transformer(shape, plans, key).transform(rows).rows
        assert rows == original  # the input rows are not changed
        for before, after in zip(rows, out, strict=True):
            for column, value in before.items():
                if plans and column == "NAME":
                    assert after[column] != value
                    continue
                assert after[column] == value and type(after[column]) is type(value), column
    sample = rows[0]
    assert isinstance(sample["AMOUNT"], Decimal) and isinstance(sample["RATIO"], float)
    assert isinstance(sample["RAWV"], bytes) and sample["PAN"].endswith(" ")  # CHAR padding
    assert sample["SEEN"] == dt.datetime(2024, 3, 1, 13, 45, 7)
    assert sample["EXACT"] == "2025-02-01T10:00:00.123456780"  # FF9 text


@pytest.mark.oracle
async def test_ts9_and_char_columns_transform_safely(
    ora: OracleConnector, oracle_conn: Any, oracle_owner: str
) -> None:
    _oracle_rows(oracle_conn, oracle_owner)
    shape = await ora.describe(ObjectRef(owner=oracle_owner, name="BRIDGE"))
    rows = await _ora_read(ora, shape)
    plans = [
        _plan(shape, "$.EXACT", "DATE_TIME", "date_shift", window_days=30),
        _plan(shape, "$.PAN", "TAX_ID", "fpe_text"),
    ]
    out = _transformer(shape, plans, generate_key_material("key")).transform(rows).rows
    for before, after in zip(rows, out, strict=True):
        assert after["EXACT"] != before["EXACT"] and after["EXACT"][-10:] == before["EXACT"][-10:]
        assert len(after["PAN"]) == 10 and after["PAN"] != before["PAN"]
    writer = await ora.writer(
        make_target(shape, write_mode="in_place", write_columns=["EXACT", "PAN"]), shape
    )
    await writer.prepare(dry_run=False)
    mark = LedgerMark(
        job_id="j_test",
        partition_no=0,
        batch_no=1,
        last_key=encode_key((out[-1]["ID"],), shape.key_columns()),
        rows=len(out),
    )
    assert await writer.write_batch(out, mark=mark) == len(out)
    again = {r["ID"]: r for r in await _ora_read(ora, shape)}
    for after in out:
        stored = again[after["ID"]]
        assert stored["EXACT"] == after["EXACT"]  # nine fractional digits survive the write
        assert len(stored["PAN"]) == 10 and stored["PAN"].rstrip() == after["PAN"].rstrip()
    originals = {r["PAN"] for r in rows}
    assert not originals & {r["PAN"] for r in again.values()}  # 0 surviving values


# --- MongoDB -------------------------------------------------------------------------------------


@pytest.fixture
async def mdb(mongo_client: Any, mongo_db: str) -> AsyncIterator[MongoConnector]:
    c = mongo_connector(mongo_uri(), mongo_db)
    await c.open()
    yield c
    await c.close()


async def _mongo_read(c: MongoConnector, shape: TableShape) -> list[Row]:
    out: list[Row] = []
    async for page in c.read_partition(
        shape, Selection(), {}, KeyRange(), after_key=None, batch=50
    ):
        out += page
    return out


@pytest.mark.mongo
async def test_untouched_mongo_values_are_identical(
    mdb: MongoConnector, mongo_client: Any, mongo_db: str
) -> None:
    mongo_client[mongo_db]["profiles"].insert_many([profile(i) for i in range(20)])
    shape = await mdb.describe(PROFILES)
    rows = await _mongo_read(mdb, shape)
    original = copy.deepcopy(rows)
    plans = [_plan(shape, "$.profile.fullName", "PERSON_NAME", "fpe_text")]
    result = _transformer(shape, plans, generate_key_material("key")).transform(rows)
    assert rows == original  # nested documents of the input are not changed
    for before, after in zip(rows, result.rows, strict=True):
        old, new = _leaves(before), _leaves(after)
        assert set(old) == set(new)
        for path, value in old.items():
            if path == "$.profile.fullName":
                assert new[path] != value and isinstance(new[path], str)
                continue
            assert new[path] == value and type(new[path]) is type(value), path
    first = result.rows[0]
    assert isinstance(first["_id"], ObjectId) and isinstance(first["riskScore"], Int64)
    assert isinstance(first["accounts"][0]["balance"], Decimal128)
    assert isinstance(first["profile"]["dob"], dt.datetime)
    assert result.verification.passed and sum(result.values_by_method.values()) == 20


@pytest.mark.mongo
async def test_mongo_wildcard_paths_transform_every_element(
    mdb: MongoConnector, mongo_client: Any, mongo_db: str
) -> None:
    docs = []
    for i in range(10):
        doc = profile(i)
        doc["accounts"] = [
            {"iban": f"GB00TEST{i:06d}{j:04d}", "balance": Decimal128(f"{j}.50")} for j in range(3)
        ]
        docs.append(doc)
    mongo_client[mongo_db]["profiles"].insert_many(docs)
    shape = await mdb.describe(PROFILES)
    rows = await _mongo_read(mdb, shape)
    plans = [_plan(shape, "$.accounts[*].iban", "IBAN", "det_token")]
    result = _transformer(shape, plans, generate_key_material("key")).transform(rows)
    ibans = {a["iban"] for r in rows for a in r["accounts"]}
    assert len(ibans) == 30
    for before, after in zip(rows, result.rows, strict=True):
        assert len(after["accounts"]) == 3
        for old, new in zip(before["accounts"], after["accounts"], strict=True):
            assert new["iban"] != old["iban"] and new["iban"] not in ibans
            assert new["balance"] == old["balance"] and isinstance(new["balance"], Decimal128)
    assert result.verification.hits == [] and result.values_by_method == {"det_token": 30}
    assert result.values_by_column == {"$.accounts[*].iban": 30}


@pytest.mark.mongo
async def test_mongo_unclassified_leaves_are_detected(
    mdb: MongoConnector, mongo_client: Any, mongo_db: str
) -> None:
    coll = mongo_client[mongo_db]["profiles"]
    coll.insert_many([profile(i) for i in range(5)])
    shape = await mdb.describe(PROFILES)  # the shape (snapshot) does not know "extra"
    coll.insert_one({**profile(9), "extra": {"note": "mail sentinel.x@example.com now"}})
    rows = await _mongo_read(mdb, shape)
    assert not any(c.path.startswith("$.extra") for c in shape.columns)
    plans = [_plan(shape, "$.profile.fullName", "PERSON_NAME", "fpe_text")]
    key = generate_key_material("key")
    detected = _transformer(shape, plans, key, unclassified="detect").transform(rows)
    assert "sentinel.x@example.com" not in str(detected.rows)
    extra = next(r for r in detected.rows if "extra" in r)["extra"]["note"]
    assert extra.startswith("mail ") and extra.endswith(" now")  # only the span is replaced
    kept = _transformer(shape, plans, key, unclassified="keep").transform(rows)
    assert "sentinel.x@example.com" in str(kept.rows)
    redacted = _transformer(shape, plans, key, unclassified="redact").transform(rows)
    assert next(r for r in redacted.rows if "extra" in r)["extra"]["note"] == "[REDACTED]"
    # leaves the shape lists (reviewed, here kept) are never re-detected
    assert all(r["notes"] == o["notes"] for r, o in zip(detected.rows, rows, strict=True))


@pytest.mark.oracle
@pytest.mark.mongo
async def test_tokens_agree_across_engines(
    ora: OracleConnector,
    oracle_conn: Any,
    oracle_owner: str,
    mdb: MongoConnector,
    mongo_client: Any,
    mongo_db: str,
) -> None:
    with oracle_conn.cursor() as cur:
        cur.execute(
            f'CREATE TABLE "{oracle_owner}"."PEOPLE" (CUSTOMER_ID NUMBER(12) PRIMARY KEY, '
            "FULL_NAME VARCHAR2(120 CHAR), EMAIL VARCHAR2(254))"
        )
        cur.execute(
            f'INSERT INTO "{oracle_owner}"."PEOPLE" VALUES (10000123, :n, :e)',
            {"n": "Ananya Krishnan", "e": "ananya.k@example.com"},
        )
    oracle_conn.commit()
    mongo_client[mongo_db]["profiles"].insert_one(
        {
            "_id": ObjectId(),
            "customerId": "CUST-10000123",
            "profile": {"fullName": "Ananya Krishnan"},
            "contact": {"email": "ananya.k@example.com"},
        }
    )
    o_shape = await ora.describe(ObjectRef(owner=oracle_owner, name="PEOPLE"))
    m_shape = await mdb.describe(PROFILES)
    o_rows, m_rows = await _ora_read(ora, o_shape), await _mongo_read(mdb, m_shape)

    def run(key: KeyMaterial) -> tuple[Row, Row]:
        o_plans = [
            _plan(o_shape, "$.CUSTOMER_ID", "CUSTOMER_ID", "fpe_digits"),
            _plan(o_shape, "$.FULL_NAME", "PERSON_NAME", "fpe_text"),
            _plan(o_shape, "$.EMAIL", "EMAIL", "email", domain="keep"),
        ]
        m_plans = [
            _plan(m_shape, "$.customerId", "CUSTOMER_ID", "fpe_digits"),
            _plan(m_shape, "$.profile.fullName", "PERSON_NAME", "fpe_text"),
            _plan(m_shape, "$.contact.email", "EMAIL", "email", domain="keep"),
        ]
        o_out = _transformer(o_shape, o_plans, key).transform(o_rows).rows[0]
        m_out = _transformer(m_shape, m_plans, key).transform(m_rows).rows[0]
        return o_out, m_out

    key = generate_key_material("key")
    o_out, m_out = run(key)
    assert _digits(o_out["CUSTOMER_ID"]) == _digits(m_out["customerId"])
    assert o_out["CUSTOMER_ID"] != 10000123 and m_out["customerId"].startswith("CUST-")
    assert o_out["FULL_NAME"] == m_out["profile"]["fullName"] != "Ananya Krishnan"
    assert o_out["EMAIL"] == m_out["contact"]["email"] != "ananya.k@example.com"
    other_o, other_m = run(generate_key_material("key"))
    assert other_o["CUSTOMER_ID"] != o_out["CUSTOMER_ID"]
    assert other_m["profile"]["fullName"] != m_out["profile"]["fullName"]
