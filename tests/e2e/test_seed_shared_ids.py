"""One customer space in three engines: the seeded PG, Oracle and MongoDB ids and names line up.

Runs ``tools/seed_sources.py`` against the lane's containers (the demo logins). PostgreSQL is
seeded into its own schema (``m3_seed_ids``) so the demo ``bank`` schema is never touched; Oracle
and MongoDB use the demo tables (``CRM.CUSTOMERS``, ``crm.profiles``) the tool owns.
"""

from __future__ import annotations

import asyncio
import random
import subprocess
import sys
from pathlib import Path

import pytest

from tests.fixtures.m3_services import mongo_uri, oracle_dsn
from tests.services import require_service

pytestmark = [pytest.mark.integration, pytest.mark.pg_source, pytest.mark.oracle, pytest.mark.mongo]

ROOT = Path(__file__).resolve().parents[2]
ROWS = 200
SCHEMA = "m3_seed_ids"


def _seed(*extra: str) -> str:
    out = subprocess.run(
        [sys.executable, "tools/seed_sources.py", "--rows", str(ROWS), "--seed", "7", *extra],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=600,
    )
    return out.stdout


async def _pg_customers(dsn: str) -> dict[int, str]:
    import asyncpg

    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch(f'SELECT customer_id, full_name FROM "{SCHEMA}".customers')
        return {int(r["customer_id"]): str(r["full_name"]) for r in rows}
    finally:
        await conn.close()


async def _drop_schema(dsn: str) -> None:
    import asyncpg

    conn = await asyncpg.connect(dsn)
    try:
        for schema in (SCHEMA, f"{SCHEMA}_restore", f"{SCHEMA}_perf"):
            await conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
    finally:
        await conn.close()


def test_three_engines_share_one_customer_space() -> None:
    oracledb = pytest.importorskip("oracledb")
    pymongo = pytest.importorskip("pymongo")
    pg_dsn = require_service("source_pg")
    require_service("oracle")
    require_service("mongo")
    try:
        _seed("--target", "postgres", "--schema", SCHEMA, "--reset")
        first = _seed("--target", "oracle") + _seed("--target", "mongo")
        assert "error" not in first.lower()
        pg = asyncio.run(_pg_customers(pg_dsn))
        with oracledb.connect(user="crm", password="crm", dsn=oracle_dsn()) as ora:  # demo login
            with ora.cursor() as cur:
                cur.execute("SELECT CUSTOMER_ID, FULL_NAME FROM CRM.CUSTOMERS")
                oracle = {int(i): str(n) for i, n in cur.fetchall()}
        client: pymongo.MongoClient[dict[str, object]] = pymongo.MongoClient(mongo_uri())
        try:
            docs = list(client["crm"]["profiles"].find({}, {"customerId": 1, "profile": 1}))
        finally:
            client.close()
        mongo = {int(str(d["customerId"])[5:]): str(d["profile"]["fullName"]) for d in docs}  # type: ignore[index]
        assert len(pg) == len(oracle) == len(mongo) == ROWS
        assert set(pg) == set(oracle) == set(mongo)
        assert all(str(d["customerId"]).startswith("CUST-") for d in docs)
        for cid in random.Random(7).sample(sorted(pg), 10):
            assert pg[cid] == oracle[cid] == mongo[cid]
        # a second run with the same (rows, seed) changes nothing
        again = _seed("--target", "oracle") + _seed("--target", "mongo")
        lines = [ln for ln in again.splitlines() if ": " in ln and " done in " not in ln]
        assert len(lines) == 7 and all(ln.endswith(": unchanged") for ln in lines)
        assert "already seeded" in _seed("--target", "postgres", "--schema", SCHEMA)
        # S2 gates r1 B1: data of an older demo model (marker version 1) is replaced, once
        with oracledb.connect(user="crm", password="crm", dsn=oracle_dsn()) as ora:  # demo login
            with ora.cursor() as cur:
                cur.execute("UPDATE CRM.DPS_SEED_INFO SET MODEL_VERSION = 1")
            ora.commit()
        client = pymongo.MongoClient(mongo_uri())
        try:
            client["crm"]["dps_seed_info"].update_one({"_id": "seed"}, {"$unset": {"version": 1}})
        finally:
            client.close()
        upgraded = _seed("--target", "oracle") + _seed("--target", "mongo")
        lines = [ln for ln in upgraded.splitlines() if ": " in ln and " done in " not in ln]
        assert len(lines) == 7 and not any(ln.endswith(": unchanged") for ln in lines)
        with oracledb.connect(user="crm", password="crm", dsn=oracle_dsn()) as ora:
            with ora.cursor() as cur:
                cur.execute(
                    "SELECT count(*) FROM CRM.CUSTOMERS WHERE COUNTRY = 'IN' "
                    "AND NATIONAL_ID NOT LIKE '0%'"
                )
                assert cur.fetchone() == (0,)  # model 2: no Aadhaar in a surrogate range
        settled = _seed("--target", "oracle") + _seed("--target", "mongo")
        lines = [ln for ln in settled.splitlines() if ": " in ln and " done in " not in ln]
        assert len(lines) == 7 and all(ln.endswith(": unchanged") for ln in lines)
    finally:
        asyncio.run(_drop_schema(pg_dsn))
