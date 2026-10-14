"""MongoDB through M2's job pipeline, in process: submit → discover → accept → run → finalize.

The registry opens the MongoDB connector from a stored connection (the ``uri`` of its
``secret_ref``, the database from its parameters); discovery classifies document paths; the run
handlers read keyset pages on ``_id``, transform nested leaves at their concrete paths and write
through ``MongoWriter``; finalize clears the write ledger. The documents are the demo profiles of
``tools/seed_mongo.py`` (the shared customer space). Every masking claim is a source-vs-target
comparison (counts only): for each sensitive leaf path, how many target values equal a source value.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest

from dps.auth import LOCAL_PRINCIPAL
from dps.connections import Connection, create_connection
from dps.engine.policy import MethodSpec
from dps.jobs import JobState
from dps.jobs_models import ConnectionParams, ConnectionSpec, ObjectRef, SnapshotOverride
from dps.layers.connectors.docpaths import values_at
from dps.layers.connectors.evidence import get_evidence
from dps.layers.connectors.submit import submit_run
from dps.worker import HandlerContext
from tests.connectors.helpers import db_key_enc, discover_and_accept, drive
from tests.connectors.test_run_modes import run_spec
from tests.fixtures.m3_services import mongo_uri

pytestmark = [pytest.mark.db, pytest.mark.integration, pytest.mark.mongo]
RegisterMongo = Callable[..., Awaitable[Connection]]
ROWS = 1200
#: Leaf paths that hold personal data in every profile (``kyc.taxId`` only for Indian customers).
SENSITIVE = (
    "$.customerId",
    "$.profile.fullName",
    "$.contact.email",
    "$.contact.mobile",
    "$.contact.addresses[*].line1",
    "$.kyc.taxId",
    "$.accounts[*].iban",
)
#: Without NER the sample does not show these as addresses: the reviewer maps them.
ADDRESS_REVIEW = [
    SnapshotOverride(
        path="$.contact.addresses[*].line1",
        entity_type="STREET_ADDRESS",
        method=MethodSpec(method="det_token"),  # obfuscate mode has no seed for a surrogate
    ),
    SnapshotOverride(
        path="$.contact.addresses[*].pin",
        entity_type="POSTAL_CODE",
        method=MethodSpec(method="det_token"),
    ),
]


def seed_profiles(client: Any, database: str, name: str, rows: int = ROWS) -> list[dict[str, Any]]:
    """Insert the demo profiles of ``tools/seed_mongo.py`` (ObjectId ``_id``); returns them."""
    from dps.engine.synth.surrogates import SurrogateEngine
    from tools.seed_model import demo_seed_bytes, seed_customers
    from tools.seed_mongo import profile_document

    engine = SurrogateEngine(demo_seed_bytes(7), default_locale="en_GB")
    docs = [
        profile_document(
            c,
            engine.surrogate(
                "IBAN", f"GB82WEST1234{c.customer_id:010d}", "en_GB", f"ACCOUNT:{c.customer_id}"
            ),
        )
        for c in seed_customers(rows, 7)
    ]
    client[database][name].insert_many(docs)
    return list(client[database][name].find({}))


@pytest.fixture
def register_mongo(handler_ctx: HandlerContext) -> RegisterMongo:
    """``await register_mongo("t1", database=..., roles=..., name=...)``: a MongoDB connection
    whose ``secret_ref`` holds only the URI (the lane's replica set has no auth)."""
    ref = "m3/test-mongo"
    folder = Path(handler_ctx.settings.secret_dir) / ref
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "uri").write_text(mongo_uri() + "\n", encoding="utf-8")

    async def register(tenant: str, *, database: str, roles: set[str], name: str) -> Connection:
        spec = ConnectionSpec(
            name=name,
            engine="mongo",
            params=ConnectionParams(database=database),
            secret_ref=ref,
            roles=roles,  # type: ignore[arg-type]
        )
        return await create_connection(
            tenant, spec, settings=handler_ctx.settings, kek=handler_ctx.kek, created_by="test"
        )

    return register


def survivors(source: list[dict[str, Any]], target: list[dict[str, Any]], path: str) -> int:
    """Target leaves at ``path`` that equal a source leaf at ``path`` (counts only)."""
    seen = {v for d in source for v in values_at(d, path) if v is not None}
    return sum(1 for d in target for v in values_at(d, path) if v in seen)


def present(docs: list[dict[str, Any]], path: str) -> int:
    return sum(1 for d in docs for v in values_at(d, path) if v is not None)


async def _run(
    ctx: HandlerContext, conn: Connection, obj: ObjectRef, snap_id: str, target: dict[str, Any]
) -> Any:
    sub = await submit_run(
        run_spec(conn.id, obj, snap_id, target, source={"partition_rows": 1000}),
        tenant_id="t1",
        principal=LOCAL_PRINCIPAL,
        settings=ctx.settings,
        kek=ctx.kek,
    )
    return await drive(ctx, "t1", sub.job.id, loops=4000)


async def test_mongo_profiles_are_discovered_and_masked_into_a_created_collection(
    handler_ctx: HandlerContext, mongo_client: Any, mongo_db: str, register_mongo: RegisterMongo
) -> None:
    source = seed_profiles(mongo_client, mongo_db, "profiles")
    conn = await register_mongo("t1", database=mongo_db, roles={"source", "target"}, name="mdb")
    obj = ObjectRef(name="profiles")
    snap = await discover_and_accept(handler_ctx, "t1", conn.id, obj, overrides=ADDRESS_REVIEW)
    cols = {c.path: c for c in snap.columns}
    for path in SENSITIVE:
        assert cols[path].entity_type and cols[path].method.method != "keep", path
    assert cols["$.segment"].method.method == "keep" and cols["$._id"].method.method == "keep"
    target = {"write_mode": "table", "connection_id": conn.id, "object": {"name": "profiles_uat"}}
    job = await _run(handler_ctx, conn, obj, snap.id, target)
    assert job.state == JobState.succeeded, (job.error_code, job.error)
    assert job.progress.rows_written == ROWS and job.progress.partitions_done >= 2
    out = list(mongo_client[mongo_db]["profiles_uat"].find({}))
    assert len(out) == ROWS and {d["_id"] for d in out} == {d["_id"] for d in source}
    for path in SENSITIVE:  # source vs target, per sensitive leaf (counts only)
        assert present(out, path) == present(source, path) > 0, path
        assert survivors(source, out, path) == 0, path
    by_id = {d["_id"]: d for d in source}
    for d in out:  # free-text notes keep no source e-mail; kept fields are byte-identical
        src = by_id[d["_id"]]
        assert src["contact"]["email"] not in d["notes"]
        assert (d["segment"], d["riskScore"], d["tags"]) == (
            src["segment"],
            src["riskScore"],
            src["tags"],
        )
        assert type(d["riskScore"]) is type(src["riskScore"])
    assert await db_key_enc(job.id) is None
    ev = await get_evidence("t1", job.progress.evidence_id or "")
    assert ev is not None and ev.pack.verification["passed"], ev and ev.pack.verification
    assert ev.pack.counts["rows_written"] == ROWS


async def test_mongo_in_place_masks_leaves_and_finalize_clears_the_ledger(
    handler_ctx: HandlerContext, mongo_client: Any, mongo_db: str, register_mongo: RegisterMongo
) -> None:
    source = seed_profiles(mongo_client, mongo_db, "profiles")
    conn = await register_mongo("t1", database=mongo_db, roles={"source", "target"}, name="mdb")
    obj = ObjectRef(name="profiles")
    review = [SnapshotOverride(path="$.customerId", method=MethodSpec(method="keep"))]
    snap = await discover_and_accept(
        handler_ctx, "t1", conn.id, obj, overrides=[*ADDRESS_REVIEW, *review]
    )
    job = await _run(
        handler_ctx, conn, obj, snap.id, {"write_mode": "in_place", "confirm_write": True}
    )
    assert job.state == JobState.succeeded, (job.error_code, job.error)
    assert job.progress.rows_written == ROWS
    out = list(mongo_client[mongo_db]["profiles"].find({}))
    assert len(out) == ROWS
    for path in SENSITIVE[1:]:
        assert survivors(source, out, path) == 0, path
    assert survivors(source, out, "$.customerId") == ROWS  # reviewed to keep
    by_id = {d["_id"]: d for d in source}
    for d in out:  # structure and every untouched leaf unchanged
        src = by_id[d["_id"]]
        assert set(d) == set(src) and set(d["profile"]) == set(src["profile"])
        assert len(d["accounts"]) == len(src["accounts"])
        assert d["accounts"][0]["balance"] == src["accounts"][0]["balance"]
        assert (d["segment"], d["riskScore"], d["kyc"].get("verifiedAt")) == (
            src["segment"],
            src["riskScore"],
            src["kyc"].get("verifiedAt"),
        )
    assert mongo_client[mongo_db]["dps_write_ledger"].count_documents({"job_id": job.id}) == 0
    ev = await get_evidence("t1", job.progress.evidence_id or "")
    assert ev is not None and ev.pack.verification["passed"]


async def test_mongo_append_generate_on_the_same_collection(
    handler_ctx: HandlerContext, mongo_client: Any, mongo_db: str, register_mongo: RegisterMongo
) -> None:
    source = seed_profiles(mongo_client, mongo_db, "profiles")
    conn = await register_mongo("t1", database=mongo_db, roles={"source", "target"}, name="mdb")
    obj = ObjectRef(name="profiles")
    snap = await discover_and_accept(handler_ctx, "t1", conn.id, obj, overrides=ADDRESS_REVIEW)
    target = {"write_mode": "append", "confirm_write": True, "pk_strategy": {"kind": "generate"}}
    job = await _run(handler_ctx, conn, obj, snap.id, target)
    assert job.state == JobState.succeeded, (job.error_code, job.error)
    assert job.progress.rows_written == ROWS
    coll = mongo_client[mongo_db]["profiles"]
    assert coll.count_documents({}) == 2 * ROWS
    old = {d["_id"] for d in source}
    appended = [d for d in coll.find({}) if d["_id"] not in old]
    assert len(appended) == ROWS
    for path in SENSITIVE:
        assert survivors(source, appended, path) == 0, path
    ev = await get_evidence("t1", job.progress.evidence_id or "")
    assert ev is not None and ev.pack.verification["passed"]
