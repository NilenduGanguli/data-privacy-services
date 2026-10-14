"""Resume after kill on Oracle and MongoDB, ``in_place`` and ``append`` (M3 Task 19; contract §12.3).

Each case builds two identical copies (A, B) of 3,000 demo customers from the shared customer space,
runs B to completion on one ``python -m dps.worker`` subprocess (the reference), then runs A with the
same key on a first worker that is ``SIGKILL``ed once five batches are committed; a second worker
finishes A after the reaper reclaims the dead worker's leases. A must equal B row for row
(``in_place``) or hold the same multiset of appended rows (``append``; generated keys differ), the
job's write-ledger rows must be gone after finalize, and the evidence must count a resumed
partition. Counts and digests only — no value is printed.
"""

from __future__ import annotations

import hashlib
import signal
import subprocess
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from dps import jobs
from dps.auth import LOCAL_PRINCIPAL
from dps.connections import Connection, create_connection
from dps.engine.policy import MethodSpec
from dps.engine.transform import fpe
from dps.engine.transform.crypto import generate_key_material
from dps.jobs import Job, JobState
from dps.jobs_models import ConnectionParams, ConnectionSpec, ObjectRef, SnapshotOverride
from dps.layers.connectors.evidence import get_evidence
from dps.layers.connectors.submit import submit_run
from dps.worker import HandlerContext
from tests.connectors.helpers import discover_and_accept
from tests.connectors.test_mongo_pipeline import ADDRESS_REVIEW, seed_profiles
from tests.connectors.test_resume_after_kill import (
    clone_accepted,
    run_spec,
    start_worker,
    stop_worker,
    wait_for_batches,
    wait_terminal,
)
from tests.fixtures.db import worker_env
from tests.fixtures.m3_services import OracleTestSchema, mongo_uri
from tests.fixtures.sources import write_ref
from tests.services import host_port

pytestmark = [
    pytest.mark.integration,
    pytest.mark.slow,
    pytest.mark.db,
    pytest.mark.timeout(900),
]
TENANT = "t1"
ROWS = 3000
CASES = [
    pytest.param(name, mode, id=f"{name}-{mode}", marks=getattr(pytest.mark, name))
    for name in ("oracle", "mongo")
    for mode in ("in_place", "append")
]

#: ``CRM.CUSTOMERS`` of the demo schema, in the test owner (``{t}`` = the table).
ORACLE_DDL = """CREATE TABLE "{o}"."{t}" (
  CUSTOMER_ID NUMBER(12) {identity} NOT NULL, FULL_NAME VARCHAR2(120 CHAR) NOT NULL,
  NATIONAL_ID VARCHAR2(18), TAX_ID CHAR(10), EMAIL VARCHAR2(254), MOBILE VARCHAR2(20), DOB DATE,
  ADDR_LINE1 VARCHAR2(200 CHAR), CITY VARCHAR2(80 CHAR), PIN_CODE VARCHAR2(10),
  COUNTRY CHAR(2) NOT NULL, SEGMENT VARCHAR2(16) NOT NULL, RISK_SCORE NUMBER(4), NOTES CLOB,
  CREATED_AT TIMESTAMP(6) NOT NULL, LAST_CONTACT_AT TIMESTAMP(9),
  CONSTRAINT "{o}_{t}_PK" PRIMARY KEY (CUSTOMER_ID))"""
#: In place every output must fit its column (and keys are never rewritten): the reviewer keeps
#: the key and picks length-preserving methods for the narrow identifier columns.
ORACLE_REVIEW = [
    SnapshotOverride(path="$.NATIONAL_ID", method=MethodSpec(method="fpe_digits")),
    SnapshotOverride(path="$.PIN_CODE", method=MethodSpec(method="mask_partial")),
    # a PAN's four digits are too few for FF1: its det_token fallback cannot fit CHAR(10)
    SnapshotOverride(path="$.TAX_ID", method=MethodSpec(method="mask_partial")),
    SnapshotOverride(
        path="$.ADDR_LINE1", entity_type="STREET_ADDRESS", method=MethodSpec(method="fpe_text")
    ),
]
KEEP_KEY = [SnapshotOverride(path="$.CUSTOMER_ID", method=MethodSpec(method="keep"))]


@dataclass
class Case:
    """One case, set up: the two objects, their snapshots, a digest reader, a ledger counter."""

    conn: Connection
    objects: dict[str, ObjectRef]  # "a" (killed) | "b" (reference)
    snapshots: dict[str, str]
    digest: Callable[[str], Awaitable[tuple[int, str]]]
    ledger_rows: Callable[[str], int]
    #: written rows whose e-mail or name is still a seeded (source) value (counts only)
    survivors: Callable[[str], int]


def _seeded() -> set[str]:
    from tools.seed_model import seed_customers

    return {v for c in seed_customers(ROWS, 7) for v in (c.email, c.full_name)}


def _digest(rows: list[tuple[Any, ...]]) -> tuple[int, str]:
    """``(rows, sha256 of the sorted row reprs)`` — order-independent, value-free."""
    lines = sorted(repr(r) for r in rows)
    return len(lines), hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


# -- Oracle ---------------------------------------------------------------------------------------


async def _register_oracle(ctx: HandlerContext, schema: OracleTestSchema) -> Connection:
    ref = "m3/resume-oracle-owner"
    write_ref(
        Path(ctx.settings.secret_dir),
        ref,
        {"username": schema.owner, "password": schema.owner_password},
    )
    host, port = host_port(schema.dsn)
    spec = ConnectionSpec(
        name="resume-oracle",
        engine="oracle",
        params=ConnectionParams(host=host, port=port, service_name=schema.dsn.rsplit("/", 1)[-1]),
        secret_ref=ref,
        roles={"source", "target"},
    )
    return await create_connection(
        TENANT, spec, settings=ctx.settings, kek=ctx.kek, created_by="test"
    )


def _oracle_load(conn: Any, owner: str, table: str, *, identity: bool) -> None:
    import oracledb

    from tools.seed_model import seed_customers
    from tools.seed_oracle import customer_row

    ident = "GENERATED BY DEFAULT AS IDENTITY" if identity else ""
    rows = [customer_row(c) for c in seed_customers(ROWS, 7)]
    cols = list(rows[0])
    values = ", ".join(
        "TO_TIMESTAMP(:LAST_CONTACT_AT, 'YYYY-MM-DD\"T\"HH24:MI:SS.FF9')"
        if c == "LAST_CONTACT_AT"
        else f":{c}"
        for c in cols
    )
    with conn.cursor() as cur:
        cur.execute(ORACLE_DDL.format(o=owner, t=table, identity=ident))
        cur.setinputsizes(NOTES=oracledb.DB_TYPE_CLOB)
        cur.executemany(f'INSERT INTO "{owner}"."{table}" ({", ".join(cols)}) VALUES ({values})', rows)  # fmt: skip
        if identity:  # the identity continues above the loaded keys
            cur.execute(
                f'ALTER TABLE "{owner}"."{table}" MODIFY CUSTOMER_ID GENERATED BY DEFAULT AS '
                "IDENTITY (START WITH LIMIT VALUE)"
            )
    conn.commit()


def _oracle_rows(conn: Any, owner: str, table: str, where: str = "") -> list[tuple[Any, ...]]:
    import oracledb

    def text_lobs(cursor: Any, meta: Any) -> Any:
        if meta.type_code in (oracledb.DB_TYPE_CLOB, oracledb.DB_TYPE_NCLOB):
            return cursor.var(oracledb.DB_TYPE_LONG, arraysize=cursor.arraysize)
        return None

    with conn.cursor() as cur:
        cur.outputtypehandler = text_lobs
        cur.execute(f'SELECT * FROM "{owner}"."{table}" {where}')
        return [tuple(r) for r in cur.fetchall()]


async def _oracle_case(
    ctx: HandlerContext, mode: str, schema: OracleTestSchema, admin: Any, oracle_conn: Any
) -> Case:
    from tests.connectors.oracle_fixtures import drop_all

    owner = schema.owner
    drop_all(admin, owner)
    tables = {"a": "CUST_A", "b": "CUST_B"}
    for table in tables.values():
        _oracle_load(oracle_conn, owner, table, identity=mode == "append")
    conn = await _register_oracle(ctx, schema)
    objects = {k: ObjectRef(owner=owner, name=t) for k, t in tables.items()}
    review = [*ORACLE_REVIEW, *(KEEP_KEY if mode == "in_place" else [])]
    first = await discover_and_accept(ctx, TENANT, conn.id, objects["b"], overrides=review)
    second = await clone_accepted(first, objects["a"])
    top = 10_000_000 + ROWS

    async def digest(label: str) -> tuple[int, str]:
        table = tables[label]
        assert len(_oracle_rows(oracle_conn, owner, table)) == (2 * ROWS if mode == "append" else ROWS)  # fmt: skip
        if mode == "in_place":
            return _digest(_oracle_rows(oracle_conn, owner, table))
        appended = _oracle_rows(oracle_conn, owner, table, f"WHERE CUSTOMER_ID > {top}")
        return _digest([r[1:] for r in appended])  # generated keys differ between runs

    def ledger_rows(job_id: str) -> int:
        with oracle_conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM all_tables WHERE owner = :o AND table_name = "
                "'DPS_WRITE_LEDGER'",
                {"o": owner},
            )
            if not cur.fetchone()[0]:
                return 0
            cur.execute(
                f'SELECT COUNT(*) FROM "{owner}".DPS_WRITE_LEDGER WHERE JOB_ID = :j', {"j": job_id}
            )
            return int(cur.fetchone()[0])

    def survivors(label: str) -> int:
        where = f"WHERE CUSTOMER_ID > {top}" if mode == "append" else ""
        with oracle_conn.cursor() as cur:
            cur.execute(f'SELECT EMAIL, FULL_NAME FROM "{owner}"."{tables[label]}" {where}')
            seeded = _seeded()
            return sum(1 for row in cur.fetchall() for v in row if v in seeded)

    return Case(conn, objects, {"a": second.id, "b": first.id}, digest, ledger_rows, survivors)


# -- MongoDB --------------------------------------------------------------------------------------


async def _mongo_case(ctx: HandlerContext, mode: str, client: Any, database: str) -> Case:
    import bson

    ref = "m3/resume-mongo"
    write_ref(Path(ctx.settings.secret_dir), ref, {"uri": mongo_uri()})
    spec = ConnectionSpec(
        name="resume-mongo",
        engine="mongo",
        params=ConnectionParams(database=database),
        secret_ref=ref,
        roles={"source", "target"},
    )
    conn = await create_connection(
        TENANT, spec, settings=ctx.settings, kek=ctx.kek, created_by="test"
    )
    docs = seed_profiles(client, database, "cust_b", ROWS)
    client[database]["cust_a"].insert_many(docs)  # an identical copy, same _ids
    original = {d["_id"] for d in docs}
    objects = {"a": ObjectRef(name="cust_a"), "b": ObjectRef(name="cust_b")}
    first = await discover_and_accept(ctx, TENANT, conn.id, objects["b"], overrides=ADDRESS_REVIEW)
    second = await clone_accepted(first, objects["a"])

    async def digest(label: str) -> tuple[int, str]:
        found = list(client[database][f"cust_{label}"].find({}))
        assert len(found) == (2 * ROWS if mode == "append" else ROWS)
        if mode == "in_place":
            return _digest([(bson.encode(d),) for d in found])
        appended = [{k: v for k, v in d.items() if k != "_id"} for d in found if d["_id"] not in original]  # fmt: skip
        return _digest([(bson.encode(d),) for d in appended])

    def ledger_rows(job_id: str) -> int:
        return int(client[database]["dps_write_ledger"].count_documents({"job_id": job_id}))

    def survivors(label: str) -> int:
        seeded = _seeded()
        written = [
            d
            for d in client[database][f"cust_{label}"].find({})
            if mode == "in_place" or d["_id"] not in original
        ]
        return sum(
            1
            for d in written
            for v in (d["contact"]["email"], d["profile"]["fullName"])
            if v in seeded
        )

    return Case(conn, objects, {"a": second.id, "b": first.id}, digest, ledger_rows, survivors)


# -- the test -------------------------------------------------------------------------------------


@pytest.mark.parametrize(("store", "mode"), CASES)
async def test_resume_after_kill(
    store: str, mode: str, handler_ctx: HandlerContext, tmp_path: Path, request: Any
) -> None:
    if not fpe.fpe_available():
        pytest.skip("the [fpe] extra is not installed: integer keys need FF1")
    if store == "oracle":  # (``engine`` is the PrivacyEngine fixture handler_ctx uses)
        schema = request.getfixturevalue("oracle_schema")
        case = await _oracle_case(
            handler_ctx, mode, schema, request.getfixturevalue("oracle_admin"),
            request.getfixturevalue("oracle_conn"),
        )  # fmt: skip
    else:
        case = await _mongo_case(
            handler_ctx, mode, request.getfixturevalue("mongo_client"),
            request.getfixturevalue("mongo_db"),
        )  # fmt: skip
    key = generate_key_material("key").model_dump(mode="json")
    if mode == "in_place":
        target: dict[str, Any] = {"write_mode": "in_place", "confirm_write": True}
    else:
        target = {
            "write_mode": "append",
            "confirm_write": True,
            "pk_strategy": {"kind": "generate"},
        }
    env = {**worker_env(handler_ctx.settings, tmp_path), "DPS_WORKER_CONCURRENCY": "4"}

    async def submit(label: str) -> str:
        spec = run_spec(
            case.conn.id, case.objects[label], case.snapshots[label],
            {**target, "expected_rows": ROWS}, {"key_material": key}, "obfuscate",
        )  # fmt: skip
        spec = spec.model_copy(
            update={
                "source": spec.source.model_copy(
                    update={"parallelism": 3, "partition_rows": 1000, "batch_rows": 50}
                )
            }
        )
        sub = await submit_run(
            spec, tenant_id=TENANT, principal=LOCAL_PRINCIPAL, settings=handler_ctx.settings,
            kek=handler_ctx.kek,
        )  # fmt: skip
        return sub.job.id

    # the reference: B on one uninterrupted worker
    ref_id = await submit("b")
    worker = start_worker(env, tmp_path, "ref")
    try:
        ref = await wait_terminal(ref_id, timeout=300)
    finally:
        stop_worker(worker)
    assert ref.state == JobState.succeeded, (ref.error_code, ref.error)

    # the kill run: A on a worker SIGKILLed mid-run; a second worker finishes it
    kill_id = await submit("a")
    first = start_worker(env, tmp_path, "kill-1")
    second: subprocess.Popen[bytes] | None = None
    try:
        await wait_for_batches(kill_id, 5)
        first.send_signal(signal.SIGKILL)
        first.wait(timeout=10)
        second = start_worker(env, tmp_path, "kill-2")
        killed: Job = await wait_terminal(kill_id, timeout=300)
    finally:
        stop_worker(first, sig=signal.SIGKILL)
        if second is not None:
            stop_worker(second)
    assert killed.state == JobState.succeeded, killed.error_code
    assert killed.progress.rows_written >= ROWS
    partitions = await jobs.list_partitions(TENANT, killed.id)
    assert len(partitions) >= 3 and any(p.resumed >= 1 or p.attempts >= 2 for p in partitions)
    assert await case.digest("a") == await case.digest("b")
    assert case.survivors("a") == 0 and case.survivors("b") == 0  # source vs target
    assert case.ledger_rows(killed.id) == 0 and case.ledger_rows(ref.id) == 0
    evidence = await get_evidence(TENANT, killed.progress.evidence_id or "")
    assert evidence is not None and evidence.pack.counts["resumed_partitions"] >= 1
    assert evidence.pack.verification["passed"]
    logs = "".join(p.read_text(errors="replace") for p in tmp_path.glob("*.log"))
    from tools.seed_model import seed_customers

    leaked = sum(
        1 for c in seed_customers(100, 7) for v in (c.email, c.full_name, c.mobile) if v in logs
    )
    assert leaked == 0
