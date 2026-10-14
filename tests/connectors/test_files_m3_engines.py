"""``files`` targets from the M3 engines, end to end in process (S1-M3 post-merge verification r1).

* engines B3 — MongoDB → ``files`` failed in every format: a sub-document or an array reached
  ``json.dumps`` without a converter for its nested ``datetime`` / ``Decimal128`` leaves, and the
  Parquet schema typed a document field from its first leaf. The API accepted the run (202), the
  partitions went ``dead`` after five attempts and the job ended ``internal_error``.
* engines B4 — Oracle → ``files`` in ``parquet`` (the default format) failed on ``TIMESTAMP(7-9)``:
  the connector delivers FF9 text by design, the writer typed it a microsecond timestamp.

Each run goes discover → accept → run → finalize into a ``pvc`` target; every masking claim is a
source-vs-target comparison over the written files (counts only, no value printed).
"""

from __future__ import annotations

import csv
import gzip
import io
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from dps.auth import LOCAL_PRINCIPAL
from dps.jobs import JobState
from dps.jobs_models import ObjectRef
from dps.layers.connectors.docpaths import values_at
from dps.layers.connectors.submit import submit_run
from dps.worker import HandlerContext
from tests.connectors.helpers import discover_and_accept, drive, register_pvc
from tests.connectors.oracle_fixtures import create_customers, drop_all
from tests.connectors.test_mongo_pipeline import ADDRESS_REVIEW, SENSITIVE, seed_profiles
from tests.connectors.test_run_modes import run_spec
from tests.e2e.test_m3_resume_after_kill import _register_oracle
from tests.fixtures.m3_services import OracleTestSchema

pytestmark = [pytest.mark.db, pytest.mark.integration]
MONGO_ROWS = 600


def _parts(ctx: HandlerContext, fmt: str) -> list[Path]:
    root = Path(ctx.settings.export_root)
    return sorted(p for p in root.rglob("part-*") if f".{fmt}" in p.name)


def _rows(path: Path, fmt: str) -> list[dict[str, Any]]:
    data = path.read_bytes()
    if path.name.endswith(".gz"):
        data = gzip.decompress(data)
    if fmt == "parquet":
        return pq.read_table(io.BytesIO(data)).to_pylist()
    if fmt == "csv":
        return list(csv.DictReader(io.StringIO(data.decode("utf-8"))))
    return [json.loads(line) for line in data.decode("utf-8").splitlines()]


def _balance(doc: dict[str, Any]) -> str:
    """The first account's balance as text (unique per demo customer, kept by every run)."""
    return str(doc["accounts"][0]["balance"])


def _manifest(ctx: HandlerContext) -> dict[str, Any]:
    [path] = list(Path(ctx.settings.export_root).rglob("manifest.json"))
    return dict(json.loads(path.read_text(encoding="utf-8")))


async def _run(
    ctx: HandlerContext,
    conn_id: str,
    obj: ObjectRef,
    snap_id: str,
    target: dict[str, Any],
    mode: str,
) -> Any:
    sub = await submit_run(
        run_spec(conn_id, obj, snap_id, target, mode=mode),
        tenant_id="t1",
        principal=LOCAL_PRINCIPAL,
        settings=ctx.settings,
        kek=ctx.kek,
    )
    return await drive(ctx, "t1", sub.job.id, loops=4000)


# -- MongoDB (B3) -----------------------------------------------------------------------------------


@pytest.fixture
def _mongo_profiles(mongo_client: Any, mongo_db: str) -> list[dict[str, Any]]:
    return seed_profiles(mongo_client, mongo_db, "profiles", MONGO_ROWS)


@pytest.mark.mongo
@pytest.mark.parametrize(
    ("mode", "fmt"),
    [
        ("obfuscate", "jsonl"),
        ("obfuscate", "csv"),
        ("obfuscate", "parquet"),
        ("synthesize", "jsonl"),
    ],
)
async def test_mongo_documents_write_to_files(
    handler_ctx: HandlerContext,
    mongo_db: str,
    _mongo_profiles: list[dict[str, Any]],
    mode: str,
    fmt: str,
) -> None:
    from dps.connections import create_connection
    from dps.jobs_models import ConnectionParams, ConnectionSpec
    from tests.fixtures.m3_services import mongo_uri
    from tests.fixtures.sources import write_ref

    source = _mongo_profiles
    write_ref(Path(handler_ctx.settings.secret_dir), "m3/files-mongo", {"uri": mongo_uri()})
    conn = await create_connection(
        "t1",
        ConnectionSpec(
            name="files-mongo",
            engine="mongo",
            params=ConnectionParams(database=mongo_db),
            secret_ref="m3/files-mongo",
            roles={"source"},
        ),
        settings=handler_ctx.settings,
        kek=handler_ctx.kek,
        created_by="test",
    )
    obj = ObjectRef(name="profiles")
    snap = await discover_and_accept(handler_ctx, "t1", conn.id, obj, overrides=ADDRESS_REVIEW)
    pvc = await register_pvc("t1", handler_ctx, base_path="mongo-files")
    target = {
        "write_mode": "files",
        "connection_id": pvc.id,
        "files": {"format": fmt, "compression": "snappy" if fmt == "parquet" else "gzip"},
    }
    job = await _run(handler_ctx, conn.id, obj, snap.id, target, mode)
    assert job.state == JobState.succeeded, (job.error_code, job.error)
    assert job.progress.rows_written == MONGO_ROWS
    rows = [r for p in _parts(handler_ctx, fmt) for r in _rows(p, fmt)]
    assert len(rows) == MONGO_ROWS
    manifest = _manifest(handler_ctx)
    types = {c["name"]: c["type"] for c in manifest["columns"]}
    assert types["profile"] == types["contact"] == types["accounts"] == "document"
    if fmt == "parquet":
        [first] = _parts(handler_ctx, fmt)[:1]
        schema = pq.read_schema(io.BytesIO(first.read_bytes()))
        assert schema.field("profile").type == pa.string()
    docs = [
        {**r, **{f: json.loads(r[f]) for f in ("profile", "contact", "kyc", "accounts")}}
        if fmt != "jsonl"
        else r
        for r in rows
    ]
    # nested leaves are plain JSON: a datetime is ISO-8601 text, a Decimal128 its digits
    assert all(isinstance(d["profile"]["dob"], str) for d in docs if d["profile"].get("dob"))
    assert all(isinstance(a["balance"], str) for d in docs for a in d["accounts"])
    # source vs target per sensitive leaf, row by row (counts only): rows align on the kept,
    # per-customer unique account balance; no leaf kept its source value
    by_balance = {_balance(d): d for d in source}
    pairs = [(by_balance[_balance(d)], d) for d in docs]
    assert len(pairs) == MONGO_ROWS
    for path in SENSITIVE:
        same = sum(
            1
            for src, out in pairs
            for a, b in zip(values_at(src, path), values_at(out, path), strict=False)
            if a is not None and str(a) == str(b)
        )
        assert same == 0, (path, same)
    if mode == "obfuscate":  # deterministic tokens never meet another source value either
        for path in SENSITIVE[1:]:  # an fpe customerId may land on another real id (documented)
            seen = {str(v) for d in source for v in values_at(d, path) if v is not None}
            hits = sum(str(v) in seen for d in docs for v in values_at(d, path) if v is not None)
            assert hits == 0, (path, hits)
    emails = {d["contact"]["email"] for d in source}
    in_notes = sum(e in str(d.get("notes") or "") for d in docs for e in emails)
    assert in_notes == 0


# -- Oracle (B4) ------------------------------------------------------------------------------------


@pytest.fixture
def _oracle_clean(oracle_admin: Any, oracle_schema: OracleTestSchema) -> Iterator[None]:
    drop_all(oracle_admin, oracle_schema.owner)
    yield
    drop_all(oracle_admin, oracle_schema.owner)


@pytest.mark.oracle
@pytest.mark.usefixtures("_oracle_clean")
@pytest.mark.parametrize("mode", ["obfuscate", "synthesize"])
async def test_oracle_timestamp9_writes_to_parquet_as_exact_text(
    handler_ctx: HandlerContext,
    oracle_conn: Any,
    oracle_schema: OracleTestSchema,
    mode: str,
) -> None:
    owner = oracle_schema.owner
    create_customers(oracle_conn, owner, 1200)
    with oracle_conn.cursor() as cur:
        cur.execute(
            f"SELECT TO_CHAR(LAST_CONTACT_AT, 'YYYY-MM-DD\"T\"HH24:MI:SS.FF9'), EMAIL, FULL_NAME "
            f'FROM "{owner}"."CUSTOMERS"'
        )
        source = cur.fetchall()
    conn = await _register_oracle(handler_ctx, oracle_schema)
    obj = ObjectRef(owner=owner, name="CUSTOMERS")
    snap = await discover_and_accept(handler_ctx, "t1", conn.id, obj)
    pvc = await register_pvc("t1", handler_ctx, base_path="oracle-files")
    target = {"write_mode": "files", "connection_id": pvc.id, "files": {"format": "parquet"}}
    job = await _run(handler_ctx, conn.id, obj, snap.id, target, mode)
    assert job.state == JobState.succeeded, (job.error_code, job.error)
    parts = _parts(handler_ctx, "parquet")
    tables = [pq.read_table(io.BytesIO(p.read_bytes())) for p in parts]
    assert all(t.schema.field("LAST_CONTACT_AT").type == pa.string() for t in tables)
    rows = [r for t in tables for r in t.to_pylist()]
    assert len(rows) == 1200
    # the kept TIMESTAMP(9) column is the source's exact FF9 text, nanoseconds included
    assert sorted(r["LAST_CONTACT_AT"] for r in rows) == sorted(s[0] for s in source)
    # source vs target, row by row on the kept FF9 timestamp (unique per row; counts only)
    by_ts = {s[0]: s for s in source}
    for i, column in ((1, "EMAIL"), (2, "FULL_NAME")):
        same = sum(by_ts[r["LAST_CONTACT_AT"]][i] == r[column] for r in rows)
        assert same == 0, (column, same)
    if mode == "obfuscate":
        emails = {s[1] for s in source}
        assert sum(r["EMAIL"] in emails for r in rows) == 0
