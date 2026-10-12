"""The three Oracle bugs Wega measured on real infrastructure, carried in as tests (contract §4.2).

Each test first demonstrates the pitfall against the raw driver, then proves the connector avoids it.
A fourth (``IS JSON`` columns parsed into dicts) is measured the same way.

    docker compose --profile oracle up -d oracle
    DPS_TEST_REQUIRE_SERVICES=1 pytest -q -m oracle tests/connectors/test_oracle_pitfalls.py
"""

from __future__ import annotations

import math
from collections.abc import Iterator
from typing import Any

import pytest

from dps.config import Settings
from dps.jobs_models import ConnectionParams, Selection
from dps.layers.connectors.base import (
    KeyRange,
    LedgerMark,
    ObjectRef,
    Row,
    TableShape,
    decode_key,
    encode_key,
)
from tests.connectors.oracle_fixtures import drop_all, make_target
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


def _connector(schema: OracleTestSchema) -> OracleConnector:
    return OracleConnector.from_secret(
        {"username": schema.owner, "password": schema.owner_password, "dsn": schema.dsn},
        ConnectionParams(),
        Settings(_env_file=None),
        action="test",
        client_id="j_pitfalls",
    )


async def _read_all(
    c: OracleConnector,
    shape: TableShape,
    rng: KeyRange | None = None,
    after: tuple[Any, ...] | None = None,
    batch: int = 7,
) -> list[list[Row]]:
    return [
        rows
        async for rows in c.read_partition(
            shape, Selection(), {}, rng or KeyRange(), after_key=after, batch=batch
        )
    ]


async def test_oracle_char_key_paging(oracle_conn: Any, oracle_schema: OracleTestSchema) -> None:
    o = oracle_schema.owner
    with oracle_conn.cursor() as cur:
        cur.execute(
            f'CREATE TABLE "{o}"."KYC" (DOC_REF CHAR(12) NOT NULL PRIMARY KEY, N NUMBER(6))'
        )
        cur.executemany(
            f'INSERT INTO "{o}"."KYC" VALUES (:1, :2)', [(f"K{i}", i) for i in range(1, 31)]
        )
        oracle_conn.commit()
        cur.execute(f'SELECT COUNT(*) FROM "{o}"."KYC" WHERE DOC_REF = :r', {"r": "K17"})
        assert cur.fetchone()[0] == 0  # the pitfall: a VARCHAR2 bind compares without padding
        cur.setinputsizes(r=oracledb.DB_TYPE_CHAR)
        cur.execute(f'SELECT COUNT(*) FROM "{o}"."KYC" WHERE DOC_REF = :r', {"r": "K17"})
        assert cur.fetchone()[0] == 1  # a CHAR bind: blank-padded comparison
    c = _connector(oracle_schema)
    try:
        shape = await c.describe(ObjectRef(owner=o, name="KYC"))
        seen = [r["DOC_REF"] for p in await _read_all(c, shape) for r in p]
        assert seen == sorted(f"K{i}".ljust(12) for i in range(1, 31))
        rest = [r["DOC_REF"] for p in await _read_all(c, shape, after=("K2",)) for r in p]
        assert rest == [k for k in seen if k > "K2".ljust(12)]  # an rstripped checkpoint binds
        keycol = [col for col in shape.columns if col.is_key]
        assert decode_key(encode_key((seen[0],), keycol), keycol) == (seen[0],)
        cut = encode_key(("K2",), keycol)  # an rstripped partition bound binds the same way
        left = [r["DOC_REF"] for p in await _read_all(c, shape, KeyRange(upper=cut)) for r in p]
        right = [r["DOC_REF"] for p in await _read_all(c, shape, KeyRange(lower=cut)) for r in p]
        assert left + right == seen
        writer = await c.writer(
            make_target(shape, write_mode="in_place", write_columns=["N"]), shape
        )
        await writer.prepare(dry_run=False)
        n = await writer.write_batch(
            [{"DOC_REF": k.rstrip(), "N": 0} for k in seen],
            mark=LedgerMark(job_id="j_char", partition_no=0, batch_no=1, last_key=[]),
        )
        assert n == 30  # every row matched by its CHAR key
        with oracle_conn.cursor() as cur:
            cur.execute(f'SELECT COUNT(*) FROM "{o}"."KYC" WHERE N = 0')
            assert cur.fetchone()[0] == 30
    finally:
        await c.close()


async def test_oracle_timestamp9_key_paging(
    oracle_conn: Any, oracle_schema: OracleTestSchema
) -> None:
    o = oracle_schema.owner
    burst = [(1, f"2025-03-01T10:00:00.123456{ns:03d}", f"v{ns}") for ns in range(60)]  # one µs
    tail = [(1, f"2025-03-01T10:00:00.123457{ns:03d}", "t") for ns in range(5)]
    tail += [(2, "2025-03-01T10:00:00.000000001", "c2")]
    with oracle_conn.cursor() as cur:
        cur.execute(
            f'CREATE TABLE "{o}"."EV" (C NUMBER(6) NOT NULL, TS TIMESTAMP(9) NOT NULL, '
            "V VARCHAR2(8), PRIMARY KEY (C, TS))"
        )
        cur.executemany(
            f'INSERT INTO "{o}"."EV" VALUES (:1, TO_TIMESTAMP(:2, {FF9}), :3)', burst + tail
        )
        oracle_conn.commit()
        cur.execute(f'SELECT TS FROM "{o}"."EV" WHERE C = 1 ORDER BY TS FETCH FIRST 2 ROWS ONLY')
        a, b = cur.fetchall()
        assert a[0] == b[0]  # the pitfall: the driver's datetime truncates to microseconds
    c = _connector(oracle_schema)
    try:
        shape = await c.describe(ObjectRef(owner=o, name="EV"))
        assert next(col for col in shape.columns if col.name == "TS").ts_precision == 9
        pages = await _read_all(c, shape)
        keys = [(r["C"], r["TS"]) for p in pages for r in p]
        expected = sorted((cc, ts) for cc, ts, _ in burst + tail)
        assert keys == expected and len(pages) == math.ceil(66 / 7)  # straight through the burst
        keycols = [col for col in shape.columns if col.is_key]
        cut = encode_key(expected[30], keycols)  # KeyRange carries encoded keys (M2)
        left = [
            (r["C"], r["TS"]) for p in await _read_all(c, shape, KeyRange(upper=cut)) for r in p
        ]
        right = [
            (r["C"], r["TS"]) for p in await _read_all(c, shape, KeyRange(lower=cut)) for r in p
        ]
        assert left + right == expected  # a cut inside the burst loses nothing
        assert decode_key(encode_key(expected[0], keycols), keycols) == expected[0]
        writer = await c.writer(  # an in-place write finds every row by its nanosecond key
            make_target(shape, write_mode="in_place", write_columns=["V"]), shape
        )
        await writer.prepare(dry_run=False)
        rows = [{"C": cc, "TS": ts, "V": "masked"} for cc, ts in expected]
        mark = LedgerMark(job_id="j_ts9", partition_no=0, batch_no=1, last_key=[])
        assert await writer.write_batch(rows, mark=mark) == 66
        with oracle_conn.cursor() as cur:
            cur.execute(f'SELECT COUNT(*) FROM "{o}"."EV" WHERE V = \'masked\'')
            assert cur.fetchone()[0] == 66
    finally:
        await c.close()


async def test_oracle_clob_astral_roundtrip(
    oracle_conn: Any, oracle_schema: OracleTestSchema
) -> None:
    o = oracle_schema.owner
    astral, email = "\U0001f600", "sentinel.person@example.net"
    text = (
        "a" * 32766
        + astral
        + "b" * 32766
        + astral
        + "नमस्ते"
        + f" contact {email} "
        + astral * 3
        + "z" * 70_000
        + astral
    )
    units = len(text.encode("utf-16-le")) // 2
    with oracle_conn.cursor() as cur:
        cur.execute(f'CREATE TABLE "{o}"."DOCS" (ID NUMBER(6) PRIMARY KEY, BODY CLOB)')
        cur.setinputsizes(None, oracledb.DB_TYPE_CLOB)
        cur.execute(f'INSERT INTO "{o}"."DOCS" VALUES (:1, :2)', [1, text])
        oracle_conn.commit()
        cur.execute(f'SELECT DBMS_LOB.GETLENGTH(BODY) FROM "{o}"."DOCS"')
        assert int(cur.fetchone()[0]) == units and units != len(text)  # the pitfall: UTF-16 units
    c = _connector(oracle_schema)
    try:
        shape = await c.describe(ObjectRef(owner=o, name="DOCS"))
        rows = [r for p in await _read_all(c, shape) for r in p]
        assert rows[0]["BODY"] == text  # whole value, no offset arithmetic
        masked = text.replace(email, "x" * len(email))
        target = make_target(shape, write_mode="table", object=ObjectRef(owner=o, name="DOCS_M"))
        writer = await c.writer(target, shape)
        await writer.prepare(dry_run=False)
        await writer.write_batch(
            [{"ID": 1, "BODY": masked}],
            mark=LedgerMark(job_id="j_clob", partition_no=0, batch_no=1, last_key=[]),
        )
        back_shape = await c.describe(ObjectRef(owner=o, name="DOCS_M"))
        back = [r for p in await _read_all(c, back_shape) for r in p]
        assert back[0]["BODY"] == masked and email not in back[0]["BODY"]
        with oracle_conn.cursor() as cur:
            cur.execute(f'SELECT DBMS_LOB.GETLENGTH(BODY) FROM "{o}"."DOCS_M"')
            assert int(cur.fetchone()[0]) == units
            cur.execute(f'SELECT DBMS_LOB.INSTR(BODY, :e) FROM "{o}"."DOCS_M"', {"e": email})
            assert int(cur.fetchone()[0]) == 0  # the source value did not survive in the target
        # MERGE refuses a CLOB bind over 32767 bytes (ORA-01461), and a batch that mixes
        # temporary LOBs with str values stores NULL for the LOBs (both measured): one batch of
        # mixed sizes upserts every value exactly
        mixed = [
            {"ID": 2, "BODY": "y" * 32_767},
            {"ID": 3, "BODY": "\U0001f600" * 8_192},  # 32768 UTF-8 bytes
            {"ID": 4, "BODY": None},
            {"ID": 5, "BODY": "short"},
            {"ID": 1, "BODY": masked + "!"},
        ]
        await writer.write_batch(
            mixed, mark=LedgerMark(job_id="j_clob", partition_no=0, batch_no=2, last_key=[])
        )
        stored = {r["ID"]: r["BODY"] for p in await _read_all(c, back_shape) for r in p}
        assert stored == {r["ID"]: r["BODY"] for r in mixed}
    finally:
        await c.close()


async def test_oracle_is_json_clob_reads_as_text(
    oracle_conn: Any, oracle_schema: OracleTestSchema
) -> None:
    o = oracle_schema.owner
    doc = '{"channel":"app","e":"\U0001f600\U0001f600abc"}'
    with oracle_conn.cursor() as cur:
        cur.execute(
            f'CREATE TABLE "{o}"."J" (ID NUMBER(6) PRIMARY KEY, PAYLOAD CLOB CHECK (PAYLOAD IS JSON))'
        )
        cur.execute(f'INSERT INTO "{o}"."J" VALUES (1, :p)', {"p": doc})
        oracle_conn.commit()
        cur.execute(f'SELECT PAYLOAD FROM "{o}"."J"')
        assert isinstance(cur.fetchone()[0], dict)  # the pitfall: python-oracledb parses it
    c = _connector(oracle_schema)
    try:
        shape = await c.describe(ObjectRef(owner=o, name="J"))
        rows = [r for p in await _read_all(c, shape) for r in p]
        assert rows[0]["PAYLOAD"] == doc  # verbatim text through the connector
    finally:
        await c.close()
