"""MongoDB through M2's job pipeline, in process: submit → discover → accept → run → finalize.

The registry opens the MongoDB connector from a stored connection (the ``uri`` of its
``secret_ref``, the database from its parameters); discovery classifies document paths; the run
handlers read keyset pages on ``_id``, transform nested leaves at their concrete paths and write
through ``MongoWriter``; finalize clears the write ledger. The documents are the demo profiles of
``tools/seed_mongo.py`` (the shared customer space). Every masking claim is a source-vs-target
comparison (counts only), document by document — each target document against its **own** source
document (a ``table`` load keeps ``_id``; an append keeps the per-customer account balance) — and,
for text leaves, against every source value too. The ``fpe_digits`` customer id is compared
document by document only: FF1 permutes its eight digits under the run's key, so a masked id can
equal *another* customer's id (about 1.4 % of runs at 1,200 profiles), which the run's strict
per-row verification rightly allows and the former any-value check counted as a survivor (S1-M3
post-merge verification r2, gates B1). The table and append runs use a fixed key under which that
happens, so both cases are met on every run.
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
from tests.connectors.helpers import (
    db_key_enc,
    derived_key,
    discover_and_accept,
    drive,
    masked_values,
)
from tests.connectors.test_run_modes import run_spec
from tests.fixtures.m3_services import mongo_uri
from tests.fixtures.sources import write_ref

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
#: The customer id (``fpe_digits``): compared document by document only (module docstring).
ID_PATH = "$.customerId"
#: The text leaves: compared document by document and against every source value.
TEXT_LEAVES = tuple(p for p in SENSITIVE if p != ID_PATH)
#: ``derived_key`` label of a fixed key under which FF1 maps at least one seeded customer id onto
#: another seeded customer's id and none onto itself (each test re-checks this premise).
COLLIDING_KEY = "mongo-pipeline-collision-16"
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
    write_ref(Path(handler_ctx.settings.secret_dir), ref, {"uri": mongo_uri() + "\n"})

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


Pairs = list[tuple[dict[str, Any], dict[str, Any]]]


def survivors(source: list[dict[str, Any]], target: list[dict[str, Any]], path: str) -> int:
    """Target leaves at ``path`` that equal **any** source leaf at ``path`` (counts only) — for
    text leaves; a format-preserving id may equal another document's id by design."""
    seen = {v for d in source for v in values_at(d, path) if v is not None}
    return sum(1 for d in target for v in values_at(d, path) if v in seen)


def same_document(pairs: Pairs, path: str) -> int:
    """Leaves at ``path`` that kept their **own** source document's value (counts only)."""
    return sum(
        1
        for src, out in pairs
        for a, b in zip(values_at(src, path), values_at(out, path), strict=False)
        if a is not None and a == b
    )


def present(docs: list[dict[str, Any]], path: str) -> int:
    return sum(1 for d in docs for v in values_at(d, path) if v is not None)


def balance(doc: dict[str, Any]) -> str:
    """The first account's balance as text: kept by every run, unique per demo customer."""
    return str(doc["accounts"][0]["balance"])


def assert_masked(source: list[dict[str, Any]], target: list[dict[str, Any]], pairs: Pairs) -> None:
    """Source vs target, counts only (each count is bound before its assert, so a failure prints
    a path and a number, never a document): every sensitive leaf is present as often as in the
    source and kept no document's own value; no text leaf equals any source value."""
    for path in SENSITIVE:
        written, read = present([o for _, o in pairs], path), present([s for s, _ in pairs], path)
        assert written == read > 0, (path, written, read)
        same = same_document(pairs, path)
        assert same == 0, (path, same)
    for path in TEXT_LEAVES:
        anywhere = survivors(source, target, path)
        assert anywhere == 0, (path, anywhere)


def colliding(ctx: HandlerContext, snapshot: Any, ids: list[str]) -> tuple[dict[str, str], int]:
    """``COLLIDING_KEY``'s masked id per seeded id, and how many land on *another* seeded id; the
    premise that makes a test meet the cross-document equality: at least one, no fixed point."""
    mapping = masked_values(ctx, snapshot, derived_key(COLLIDING_KEY), ID_PATH, ids)
    seeded = set(ids)
    fixed = sum(1 for v in ids if mapping[v] == v)
    crossed = sum(1 for v in ids if mapping[v] != v and mapping[v] in seeded)
    assert (fixed, crossed > 0) == (0, True), (fixed, crossed)
    return mapping, crossed


async def _run(
    ctx: HandlerContext,
    conn: Connection,
    obj: ObjectRef,
    snap_id: str,
    target: dict[str, Any],
    *,
    key: dict[str, Any] | None = None,
) -> Any:
    extra = {"consistency": {"scope": "key", "key_material": key}} if key else {}
    sub = await submit_run(
        run_spec(conn.id, obj, snap_id, target, source={"partition_rows": 1000}, **extra),
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
    mapping, crossed = colliding(handler_ctx, snap, [d["customerId"] for d in source])
    target = {"write_mode": "table", "connection_id": conn.id, "object": {"name": "profiles_uat"}}
    job = await _run(handler_ctx, conn, obj, snap.id, target, key=derived_key(COLLIDING_KEY))
    assert job.state == JobState.succeeded, (job.error_code, job.error)  # strict verification
    assert job.progress.rows_written == ROWS and job.progress.partitions_done >= 2
    out = list(mongo_client[mongo_db]["profiles_uat"].find({}))
    assert len(out) == ROWS and {d["_id"] for d in out} == {d["_id"] for d in source}
    by_id = {d["_id"]: d for d in source}
    pairs = [(by_id[d["_id"]], d) for d in out]  # a table load keeps _id
    assert_masked(source, out, pairs)
    keyed = sum(1 for src, d in pairs if d["customerId"] == mapping[src["customerId"]])
    assert keyed == ROWS  # each id is its own source id under the key ...
    crossed_out = survivors(source, out, ID_PATH)
    assert crossed_out == crossed > 0  # ... and some equal another customer's id (not survivors)
    # free-text notes keep no source e-mail; kept fields are byte-identical (counts only)
    in_notes = sum(1 for src, d in pairs if src["contact"]["email"] in d["notes"])
    assert in_notes == 0
    changed = sum(
        1
        for src, d in pairs
        if (d["segment"], d["riskScore"], d["tags"])
        != (src["segment"], src["riskScore"], src["tags"])
        or type(d["riskScore"]) is not type(src["riskScore"])
    )
    assert changed == 0
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
    by_id = {d["_id"]: d for d in source}
    pairs = [(by_id[d["_id"]], d) for d in out]
    for path in TEXT_LEAVES:  # counts only
        same, anywhere = same_document(pairs, path), survivors(source, out, path)
        assert (same, anywhere) == (0, 0), (path, same, anywhere)
    kept = same_document(pairs, ID_PATH)
    assert kept == ROWS  # reviewed to keep
    # structure and every untouched leaf unchanged (counts only)
    changed = sum(
        1
        for src, d in pairs
        if set(d) != set(src)
        or set(d["profile"]) != set(src["profile"])
        or len(d["accounts"]) != len(src["accounts"])
        or balance(d) != balance(src)
        or (d["segment"], d["riskScore"], d["kyc"].get("verifiedAt"))
        != (src["segment"], src["riskScore"], src["kyc"].get("verifiedAt"))
    )
    assert changed == 0
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
    mapping, crossed = colliding(handler_ctx, snap, [d["customerId"] for d in source])
    target = {"write_mode": "append", "confirm_write": True, "pk_strategy": {"kind": "generate"}}
    job = await _run(handler_ctx, conn, obj, snap.id, target, key=derived_key(COLLIDING_KEY))
    assert job.state == JobState.succeeded, (job.error_code, job.error)  # strict verification
    assert job.progress.rows_written == ROWS
    coll = mongo_client[mongo_db]["profiles"]
    assert coll.count_documents({}) == 2 * ROWS
    old = {d["_id"] for d in source}
    appended = [d for d in coll.find({}) if d["_id"] not in old]
    assert len(appended) == ROWS
    # each appended document against its own source document: new _ids, the kept balance
    by_balance = {balance(d): d for d in source}
    pairs = [(by_balance[balance(d)], d) for d in appended if balance(d) in by_balance]
    assert len(pairs) == ROWS
    assert_masked(source, appended, pairs)
    keyed = sum(1 for src, d in pairs if d["customerId"] == mapping[src["customerId"]])
    assert keyed == ROWS
    crossed_out = survivors(source, appended, ID_PATH)
    assert crossed_out == crossed > 0
    ev = await get_evidence("t1", job.progress.evidence_id or "")
    assert ev is not None and ev.pack.verification["passed"]


async def test_an_inline_policy_run_on_mongo_finalizes_with_its_pack(
    handler_ctx: HandlerContext, mongo_client: Any, mongo_db: str, register_mongo: RegisterMongo
) -> None:
    """FINAL r1 B1: the job row keeps only the label of an inline policy (F-18); finalize opens
    the sealed body, so the run ends succeeded with a pack that names the policy and version."""
    from tests.connectors.test_run_sealed_policy import inline_policy

    source = seed_profiles(mongo_client, mongo_db, "profiles", rows=400)
    conn = await register_mongo("t1", database=mongo_db, roles={"source", "target"}, name="mdb")
    obj = ObjectRef(name="profiles")
    snap = await discover_and_accept(handler_ctx, "t1", conn.id, obj, overrides=ADDRESS_REVIEW)
    target = {"write_mode": "table", "connection_id": conn.id, "object": {"name": "profiles_in"}}
    policy = inline_policy("gdpr-pseudonymise", name="mongo-inline-kyc", version=6)
    sub = await submit_run(
        run_spec(conn.id, obj, snap.id, target, source={"partition_rows": 1000}, policy=policy),
        tenant_id="t1",
        principal=LOCAL_PRINCIPAL,
        settings=handler_ctx.settings,
        kek=handler_ctx.kek,
    )
    job = await drive(handler_ctx, "t1", sub.job.id, loops=4000)
    assert job.state == JobState.succeeded, (job.error_code, job.error)
    out = list(mongo_client[mongo_db]["profiles_in"].find({}))
    assert len(out) == 400
    for path in TEXT_LEAVES:
        anywhere = survivors(source, out, path)
        assert anywhere == 0, (path, anywhere)
    ev = await get_evidence("t1", job.progress.evidence_id or "")
    assert ev is not None and ev.pack.outcome == "succeeded"
    assert ev.pack.policy["name"] == "mongo-inline-kyc" and ev.pack.policy["version"] == "6"
    assert "error" not in ev.pack.verification["post_sample"]
    assert await db_key_enc(job.id) is None


# -- FINAL r1 l2-engines B2: an existing collection is no DDL ---------------------------------
#: A collection's shape is sampled (``describe``: at most 500 documents), so a leaf its documents
#: do not show yet is no missing column, and ``_id`` is every collection's unique key.
SMALL = 300


async def _small_source(
    ctx: HandlerContext, client: Any, database: str, register: RegisterMongo
) -> tuple[list[dict[str, Any]], Connection, Any]:
    source = seed_profiles(client, database, "profiles", rows=SMALL)
    conn = await register("t1", database=database, roles={"source", "target"}, name="mdb")
    snap = await discover_and_accept(
        ctx, "t1", conn.id, ObjectRef(name="profiles"), overrides=ADDRESS_REVIEW
    )
    return source, conn, snap


@pytest.mark.parametrize("load", ["truncate_insert", "upsert"])
async def test_a_table_load_into_an_existing_empty_collection(
    handler_ctx: HandlerContext,
    mongo_client: Any,
    mongo_db: str,
    register_mongo: RegisterMongo,
    load: str,
) -> None:
    source, conn, snap = await _small_source(handler_ctx, mongo_client, mongo_db, register_mongo)
    mongo_client[mongo_db].create_collection("profiles_pre")  # exists, holds no document
    target = {
        "write_mode": "table",
        "connection_id": conn.id,
        "object": {"name": "profiles_pre"},
        "table_load": load,
    }
    job = await _run(handler_ctx, conn, ObjectRef(name="profiles"), snap.id, target)
    assert job.state == JobState.succeeded, (job.error_code, job.error)
    out = list(mongo_client[mongo_db]["profiles_pre"].find({}))
    assert len(out) == SMALL and {d["_id"] for d in out} == {d["_id"] for d in source}
    by_id = {d["_id"]: d for d in source}
    assert_masked(source, out, [(by_id[d["_id"]], d) for d in out])
    ev = await get_evidence("t1", job.progress.evidence_id or "")
    assert ev is not None and ev.pack.verification["passed"], ev and ev.pack.verification


async def _append_into(
    ctx: HandlerContext, client: Any, database: str, register: RegisterMongo, existing: list[Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], Any]:
    """A named append (new ``_id``s) into ``profiles_app`` holding ``existing`` documents."""
    source, conn, snap = await _small_source(ctx, client, database, register)
    client[database].create_collection("profiles_app")
    if existing:
        client[database]["profiles_app"].insert_many(existing)
    target = {
        "write_mode": "append",
        "connection_id": conn.id,
        "object": {"name": "profiles_app"},
        "pk_strategy": {"kind": "generate"},
        "confirm_write": True,
        "expected_rows": SMALL,
    }
    job = await _run(ctx, conn, ObjectRef(name="profiles"), snap.id, target)
    assert job.state == JobState.succeeded, (job.error_code, job.error)
    before = {d["_id"] for d in existing}
    appended = [d for d in client[database]["profiles_app"].find({}) if d["_id"] not in before]
    return source, appended, job


async def test_a_named_append_into_an_empty_collection(
    handler_ctx: HandlerContext, mongo_client: Any, mongo_db: str, register_mongo: RegisterMongo
) -> None:
    source, appended, job = await _append_into(
        handler_ctx, mongo_client, mongo_db, register_mongo, []
    )
    assert len(appended) == SMALL and not {d["_id"] for d in appended} & {d["_id"] for d in source}
    by_balance = {balance(d): d for d in source}
    pairs = [(by_balance[balance(d)], d) for d in appended if balance(d) in by_balance]
    assert len(pairs) == SMALL
    assert_masked(source, appended, pairs)
    ev = await get_evidence("t1", job.progress.evidence_id or "")
    assert ev is not None and ev.pack.verification["passed"], ev and ev.pack.verification


async def test_a_named_append_into_a_collection_whose_documents_lack_a_leaf(
    handler_ctx: HandlerContext, mongo_client: Any, mongo_db: str, register_mongo: RegisterMongo
) -> None:
    # one document without tags, accounts or kyc: the sample shows none of those leaves
    sparse = [{"segment": "retail", "notes": "migrated record"}]
    source, appended, _ = await _append_into(
        handler_ctx, mongo_client, mongo_db, register_mongo, sparse
    )
    assert len(appended) == SMALL
    by_balance = {balance(d): d for d in source}
    pairs = [(by_balance[balance(d)], d) for d in appended if balance(d) in by_balance]
    assert len(pairs) == SMALL
    assert_masked(source, appended, pairs)
    assert mongo_client[mongo_db]["profiles_app"].count_documents({}) == SMALL + 1


async def test_an_incompatible_leaf_the_target_shows_is_still_refused(
    handler_ctx: HandlerContext, mongo_client: Any, mongo_db: str, register_mongo: RegisterMongo
) -> None:
    """A leaf the target's documents do show keeps the kind check (fails closed, no write)."""
    source, conn, snap = await _small_source(handler_ctx, mongo_client, mongo_db, register_mongo)
    kind = type(source[0]["riskScore"]).__name__
    assert kind in ("int", "Int64", "float", "Decimal128"), kind
    mongo_client[mongo_db]["profiles_bad"].insert_one({"riskScore": "high"})  # a text leaf
    target = {
        "write_mode": "append",
        "connection_id": conn.id,
        "object": {"name": "profiles_bad"},
        "pk_strategy": {"kind": "generate"},
        "confirm_write": True,
        "expected_rows": SMALL,
    }
    job = await _run(handler_ctx, conn, ObjectRef(name="profiles"), snap.id, target)
    assert job.state == JobState.failed and job.error_code == "target_invalid", job.error
    assert mongo_client[mongo_db]["profiles_bad"].count_documents({}) == 1
