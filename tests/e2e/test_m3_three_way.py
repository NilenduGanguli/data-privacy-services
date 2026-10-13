"""Three systems, one key, one customer: the M3 demo as a test (spec §7 cross-system consistency).

Oracle ``CRM.CUSTOMERS``, MongoDB ``crm.profiles`` and a PostgreSQL copy of the M2 bank customers
(schema ``m3e2e``) are seeded from the one customer space (``tools/seed_model.py``); three runs of
one job group (one caller-held key) mask them into ``CRM_MASKED.CUSTOMERS_E2E``,
``crm_uat.profiles_e2e`` and ``m3e2e_uat.customers_e2e`` through the API and a worker subprocess,
and the group's consistency check joins the three masked targets: 3/3 pairs at coverage 1.0, the
masked names and e-mails agreeing for every joined customer. A fourth member that protects the
join column differently fails the check (``method_mismatch``). Every masking claim is a
source-vs-target comparison per sensitive column (counts only), and the worker logs hold no seeded
value.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from tests.e2e.conftest import JOBS, MONGO_REF, Workers, oracle_params, wait_job
from tests.fixtures.m3_services import mongo_uri, oracle_dsn
from tests.services import require_service

pytestmark = [
    pytest.mark.integration,
    pytest.mark.slow,
    pytest.mark.db,
    pytest.mark.pg_source,
    pytest.mark.oracle,
    pytest.mark.mongo,
    pytest.mark.timeout(1800),
]
ROOT = Path(__file__).resolve().parents[2]
ROWS = 2400
PG_SCHEMA, PG_TARGET = "m3e2e", "m3e2e_uat"
PURPOSE = {"ticket": "CHG-48213", "legal_basis": "legitimate interest - UAT provisioning"}


def _connection(c: Any, name: str, engine: str, params: dict[str, Any], ref: str, roles: list[str]) -> str:  # fmt: skip
    body = {"name": name, "engine": engine, "params": params, "secret_ref": ref, "roles": roles}
    r = c.post("/api/v1/connections", json=body, headers=JOBS)
    assert r.status_code == 201, r.json().get("code")
    cid = str(r.json()["id"])
    test = c.post(f"/api/v1/connections/{cid}/test", json={"role": roles[-1]}, headers=JOBS)
    assert test.status_code == 200, test.json().get("code")
    failed = [k["name"] for k in test.json()["checks"] if not k["ok"] and k.get("severity") != "advisory"]  # fmt: skip
    assert test.json()["ok"], failed
    return cid


def _discover_override_accept(
    c: Any, conn_id: str, obj: dict[str, Any], overrides: list[dict[str, Any]]
) -> dict[str, Any]:
    """discover → PATCH overrides while the snapshot is proposed → accept; the accepted snapshot."""
    r = c.post("/api/v1/jobs/discover", json={"connection_id": conn_id, "objects": [obj]}, headers=JOBS)  # fmt: skip
    assert r.status_code == 202, r.json().get("code")
    job = wait_job(c, r.json()["job"]["id"], timeout=300)
    [snap_id] = job["progress"]["snapshot_ids"]
    if overrides:
        p = c.patch(f"/api/v1/snapshots/{snap_id}", json={"overrides": overrides}, headers=JOBS)
        assert p.status_code == 200, p.json().get("code")
    a = c.post(f"/api/v1/snapshots/{snap_id}/accept", headers=JOBS)
    assert a.status_code == 200, a.json().get("code")
    return dict(a.json())


def _sensitive(snapshot: dict[str, Any]) -> list[str]:
    """Paths the accepted snapshot transforms (an entity and a method other than keep)."""
    return [
        col["path"]
        for col in snapshot["columns"]
        if col["text_column"] or (col["entity_type"] and col["method"]["method"] != "keep")
    ]


# -- seeding and source-vs-target reads (counts only) ---------------------------------------------


def _seed_postgres(dsn: str) -> None:
    subprocess.run(
        [sys.executable, "tools/seed_sources.py", "--target", "postgres", "--schema", PG_SCHEMA,
         "--rows", str(ROWS), "--seed", "7", "--reset", "--dsn", dsn],
        cwd=ROOT, check=True, capture_output=True, timeout=900,
    )  # fmt: skip


async def _pg(dsn: str, sql: str, *args: Any) -> list[dict[str, Any]]:
    import asyncpg

    conn = await asyncpg.connect(dsn)
    try:
        return [dict(r) for r in await conn.fetch(sql, *args)]
    finally:
        await conn.close()


def _pg_rows(dsn: str, side: str) -> list[dict[str, Any]]:
    table = f'"{PG_SCHEMA}".customers' if side == "source" else f'"{PG_TARGET}".customers_e2e'
    return asyncio.run(_pg(dsn, f"SELECT * FROM {table}"))


def _oracle_rows(side: str) -> list[dict[str, Any]]:
    import oracledb

    user, table = (
        ("crm", "CRM.CUSTOMERS") if side == "source" else ("crm_masked", "CRM_MASKED.CUSTOMERS_E2E")
    )

    def text_lobs(cursor: Any, meta: Any) -> Any:
        if meta.type_code in (oracledb.DB_TYPE_CLOB, oracledb.DB_TYPE_NCLOB):
            return cursor.var(oracledb.DB_TYPE_LONG, arraysize=cursor.arraysize)
        return None

    with oracledb.connect(user=user, password=user, dsn=oracle_dsn()) as conn:  # demo login
        with conn.cursor() as cur:
            cur.outputtypehandler = text_lobs
            cur.execute(f"SELECT * FROM {table}")
            names = [d[0] for d in cur.description]
            return [dict(zip(names, r, strict=True)) for r in cur.fetchall()]


def _check_documents(snapshot: dict[str, Any]) -> dict[str, tuple[int, int, int]]:
    """MongoDB: a ``table`` load keeps ``_id``, so documents line up by it."""
    import pymongo

    from dps.layers.connectors.docpaths import values_at

    client: pymongo.MongoClient[dict[str, Any]] = pymongo.MongoClient(mongo_uri())
    try:
        source = list(client["crm"]["profiles"].find({}))
        target = {d["_id"]: d for d in client["crm_uat"]["profiles_e2e"].find({})}
    finally:
        client.close()
    report: dict[str, tuple[int, int, int]] = {}
    for col in snapshot["columns"]:
        if not (col["text_column"] or (col["entity_type"] and col["method"]["method"] != "keep")):
            continue
        path = col["path"]
        pairs = [
            (a, b)
            for s in source
            if s["_id"] in target
            for a, b in zip(values_at(s, path), values_at(target[s["_id"]], path), strict=False)
        ]
        anywhere = 0
        if col["kind"] == "text":
            anywhere = _survivors(
                [v for d in source for v in values_at(d, path)],
                [v for d in target.values() for v in values_at(d, path)],
            )
        report[path] = (len(target), _same_row(pairs), anywhere)
    return report


def _norm(value: Any) -> Any:
    return value.rstrip() if isinstance(value, str) else value


def _survivors(source: list[Any], target: list[Any]) -> int:
    """Target values equal to *any* source value of the column (text columns)."""
    seen = {_norm(v) for v in source if v is not None}
    return sum(1 for v in target if v is not None and _norm(v) in seen)


def _same_row(pairs: list[tuple[Any, Any]]) -> int:
    """Rows whose target value equals the same customer's source value."""
    return sum(1 for s, t in pairs if s is not None and _norm(s) == _norm(t))


def masked_ids(snapshot: dict[str, Any], key: dict[str, Any], ids: list[Any]) -> dict[Any, Any]:
    """source join value → the value the run wrote (the engine's deterministic method under the
    group key), to line up target rows with source rows without reading any key from DPS."""
    from dps.config import Settings
    from dps.engine.batch import BatchTransformer, ColumnPlan
    from dps.engine.policy import MethodSpec
    from dps.engine.service import PrivacyEngine
    from dps.engine.transform.crypto import key_material_from_caller
    from dps.layers.connectors.base import TableShape

    shape = TableShape.model_validate(snapshot["shape"])
    col = next(c for c in snapshot["columns"] if c["is_key"] or c["anchor"])
    engine = PrivacyEngine(
        Settings(_env_file=None, llm_provider="stub", ner_enabled=False, fpe_enabled=True)
    )
    plan = ColumnPlan(
        path=col["path"], name=col["name"], entity_type=col["entity_type"],
        method=MethodSpec.model_validate(col["method"]), text_column=False, reversible=True,
        kind=col["kind"], length=None,
    )  # fmt: skip
    out = BatchTransformer(
        engine, policy=engine.policy(snapshot["policy_name"]), mode="obfuscate", columns=[plan],
        shape=shape, key=key_material_from_caller(key), seed=None, locale="en_IN",
        unclassified="keep",
    ).transform([{col["name"]: v} for v in ids])  # fmt: skip
    return {v: r[col["name"]] for v, r in zip(ids, out.rows, strict=True)}


def _check_table(
    snapshot: dict[str, Any],
    key: dict[str, Any],
    read: Any,
    *,
    join: str,
) -> dict[str, tuple[int, int, int]]:
    """Per sensitive column: (rows, same-row survivors, any-row survivors for text) — counts."""
    source, target = read("source"), read("target")
    mapping = masked_ids(snapshot, key, [r[join] for r in source])
    by_masked = {_norm(t[join]): t for t in target}
    aligned = [(s, by_masked.get(_norm(mapping[s[join]]))) for s in source]
    found = sum(1 for _, t in aligned if t is not None)
    report: dict[str, tuple[int, int, int]] = {}
    for col in snapshot["columns"]:
        if not (col["text_column"] or (col["entity_type"] and col["method"]["method"] != "keep")):
            continue
        name = col["name"]
        pairs = [(s[name], t[name]) for s, t in aligned if t is not None]
        textual = col["kind"] in ("text", "char", "lob_text")
        anywhere = (
            _survivors([s[name] for s in source], [t[name] for t in target]) if textual else 0
        )
        report[col["path"]] = (found, _same_row(pairs), anywhere)
    return report


def _prepare_targets(pg_dsn: str) -> None:
    """Drop what an earlier run left in the three targets (the source data is reseeded)."""
    import oracledb
    import pymongo

    with oracledb.connect(user="crm_masked", password="crm_masked", dsn=oracle_dsn()) as conn:
        with conn.cursor() as cur:
            for table in ("CUSTOMERS_E2E", "DPS_WRITE_LEDGER"):
                try:
                    cur.execute(f"DROP TABLE CRM_MASKED.{table} PURGE")
                except oracledb.DatabaseError:
                    pass  # ORA-00942: nothing to drop
    client: pymongo.MongoClient[dict[str, Any]] = pymongo.MongoClient(mongo_uri())
    try:
        for name in ("profiles_e2e", "profiles_drift"):
            client["crm_uat"].drop_collection(name)
    finally:
        client.close()
    asyncio.run(_pg(pg_dsn, f'DROP SCHEMA IF EXISTS "{PG_TARGET}" CASCADE'))
    asyncio.run(_pg(pg_dsn, f'CREATE SCHEMA "{PG_TARGET}"'))


def test_three_way_consistency_join(m3_app: Any, m3_workers: Workers) -> None:
    from tools.seed_model import seed_customers
    from tools.seed_mongo import seed_mongo
    from tools.seed_oracle import seed_oracle

    c = m3_app
    pg_dsn = require_service("source_pg")
    require_service("oracle")
    require_service("mongo")
    seed_oracle(oracle_dsn(), ROWS, 7)
    seed_mongo(mongo_uri(), ROWS, 7)
    _seed_postgres(pg_dsn)
    _prepare_targets(pg_dsn)
    m3_workers.start()

    from tests.api.test_api_connections import PG

    ora_src = _connection(c, "e2e-ora-src", "oracle", oracle_params(), "m3/oracle-crm-ro", ["source"])  # fmt: skip
    ora_tgt = _connection(c, "e2e-ora-tgt", "oracle", oracle_params(), "m3/oracle-crm-masked", ["target"])  # fmt: skip
    mdb_src = _connection(c, "e2e-mdb-src", "mongo", {"database": "crm"}, MONGO_REF, ["source"])
    mdb_tgt = _connection(c, "e2e-mdb-tgt", "mongo", {"database": "crm_uat"}, MONGO_REF, ["target"])  # fmt: skip
    pg = _connection(c, "e2e-pg", "postgres", PG, "dps/src/test-pg-admin", ["source", "target"])
    members = [
        (
            ora_src,
            {"owner": "CRM", "name": "CUSTOMERS"},
            ora_tgt,
            {"owner": "CRM_MASKED", "name": "CUSTOMERS_E2E"},
        ),  # fmt: skip
        (mdb_src, {"name": "profiles"}, mdb_tgt, {"name": "profiles_e2e"}),
        (
            pg,
            {"owner": PG_SCHEMA, "name": "customers"},
            pg,
            {"owner": PG_TARGET, "name": "customers_e2e"},
        ),  # fmt: skip
    ]
    snaps = [_discover_override_accept(c, s, o, []) for s, o, _, _ in members]
    created = c.post("/api/v1/job-groups", json={"name": "crm-uat-refresh-e2e"}, headers=JOBS)
    assert created.status_code == 201
    group, key = created.json()["group"], created.json()["key_material"]

    def run(src: str, obj: dict[str, Any], tgt: str, tobj: dict[str, Any], snap_id: str) -> str:
        body = {
            "mode": "obfuscate", "snapshot_id": snap_id, "group_id": group["id"],
            "purpose": PURPOSE,
            "source": {"connection_id": src, "object": obj, "parallelism": 3,
                       "partition_rows": 1000, "batch_rows": 250},
            "target": {"write_mode": "table", "connection_id": tgt, "object": tobj,
                       "create_if_missing": True},
            "consistency": {"scope": "key", "key_material": key},
        }  # fmt: skip
        r = c.post("/api/v1/jobs/run", json=body, headers=JOBS)
        assert r.status_code == 202, r.json().get("code")
        return str(r.json()["job"]["id"])

    run_ids = [run(*m, s["id"]) for m, s in zip(members, snaps, strict=True)]
    for jid in run_ids:
        job = wait_job(c, jid)
        assert job["progress"]["rows_written"] == ROWS and job["progress"]["partitions_total"] >= 2

    # source vs target, per sensitive column of every target (counts only): 0 survivors — rows
    # lined up through the masked key; text columns also checked against every source value
    ora_snap, mdb_snap, pg_snap = snaps
    reports = {
        "oracle": _check_table(ora_snap, key, lambda side: _oracle_rows(side), join="CUSTOMER_ID"),
        "postgres": _check_table(
            pg_snap, key, lambda side: _pg_rows(pg_dsn, side), join="customer_id"
        ),
        "mongo": _check_documents(mdb_snap),
    }
    for engine_name, report in reports.items():
        assert report, engine_name
        for path, (rows, same_row, anywhere) in report.items():
            assert (engine_name, path, rows, same_row, anywhere) == (engine_name, path, ROWS, 0, 0)

    res = c.post(
        f"/api/v1/job-groups/{group['id']}/consistency-check", json={"sample": 500}, headers=JOBS
    )
    assert res.status_code == 200, res.json().get("code")
    check = res.json()
    assert check["problems"] == [] and check["methods_agree"], check["problems"]
    assert check["passed"] and check["consistent_pairs"] == check["total_pairs"] == 3
    assert all(p["coverage"] == 1.0 and p["matched"] == 500 for p in check["pairs"])
    assert check["all_members_matched"] == 500
    forms = {m["engine"]: m["form"] for m in check["members"]}
    assert forms == {"oracle": "numeric", "mongo": "prefixed_digits", "postgres": "numeric"}
    assert {m["entity_type"] for m in check["members"]} == {"CUSTOMER_ID"}
    counted = {a["entity_type"]: a for a in check["attributes"] if a["counts_toward_pass"]}
    for entity in ("PERSON_NAME", "EMAIL"):
        assert counted[entity]["agreed"] == counted[entity]["compared"] > 0, entity
        assert counted[entity]["members"] == [0, 1, 2], entity
    stored = c.get(f"/api/v1/job-groups/{group['id']}", headers=JOBS).json()["group"]
    assert stored["last_check"] == check and stored["last_check_at"]
    for jid in run_ids:
        listed = c.get("/api/v1/evidence", params={"job_id": jid}, headers=JOBS).json()["items"]
        pack = c.get(f"/api/v1/evidence/{listed[0]['id']}", headers=JOBS).json()["pack"]
        assert pack["verification"]["passed"] and pack["consistency"]["kid"] == key["kid"]

    # drift: a fourth member protecting the join column differently must fail the check
    drift_snap = _discover_override_accept(
        c, mdb_src, {"name": "profiles"},
        [{"path": "$.customerId", "method": {"method": "det_token"}}],
    )  # fmt: skip
    drift = run(
        mdb_src, {"name": "profiles"}, mdb_tgt, {"name": "profiles_drift"}, drift_snap["id"]
    )
    wait_job(c, drift)
    pairs = [
        {"job_id": run_ids[0], "path": "$.CUSTOMER_ID"},
        {"job_id": drift, "path": "$.customerId"},
    ]
    bad = c.post(
        f"/api/v1/job-groups/{group['id']}/consistency-check",
        json={"pairs": pairs, "sample": 200},
        headers=JOBS,
    ).json()
    assert bad["passed"] is False and any(p["code"] == "method_mismatch" for p in bad["problems"])
    assert bad["pairs"][0]["matched"] == 0

    # the value-free evidence of this run (counts, codes, paths; pytest -s shows it)
    print(
        "M3_THREE_WAY",
        json.dumps({"check": check, "drift_problems": bad["problems"], "columns": reports}),
    )

    # no seeded value reached a worker log
    logs = m3_workers.logs()
    assert logs  # the workers did log
    for cust in seed_customers(200, 7):
        for value in (cust.email, cust.full_name, cust.mobile, cust.national_id):
            assert value not in logs
        assert cust.mongo_customer_id not in logs
