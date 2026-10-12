"""Job groups (key + seed returned once, only identifiers stored) and run submission rules."""

from __future__ import annotations

import json
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from dps import db, jobs
from dps.auth import LOCAL_PRINCIPAL
from dps.config import Settings
from dps.connections import create_connection
from dps.engine.transform.crypto import generate_key_material
from dps.groups import create_group, get_group, list_groups
from dps.jobs_models import (
    ConnectionParams,
    ConnectionSpec,
    ConsistencySpec,
    JobSecret,
    RunSpec,
    SpecError,
)
from dps.layers.connectors.submit import seal_retry_secret, submit_run
from dps.secrets import open_json, seed_fingerprint
from dps.snapshots import accept_snapshot, create_snapshot
from tests.connectors.factories import make_snapshot

pytestmark = pytest.mark.db


async def test_create_get_and_list_groups(migrated_db: Settings) -> None:
    group, key, seed = await create_group("t1", "bank-uat-refresh", created_by="k1")
    assert group.kid == key.kid and group.seed_fingerprint == seed_fingerprint(seed.seed)
    assert key.dek and seed.seed and group.created_by == "k1" and group.closed_at is None
    async with db.acquire("t1") as conn:
        row = await conn.fetchrow(
            f"SELECT * FROM {db.schema_ident()}.dps_job_group WHERE id = $1", group.id
        )
    assert row is not None
    assert set(row.keys()) == {
        "id",
        "tenant_id",
        "name",
        "kid",
        "seed_fingerprint",
        "created_by",
        "created_at",
        "closed_at",
    }
    stored = json.dumps({k: str(v) for k, v in row.items()})
    assert key.dek not in stored and seed.seed not in stored
    with pytest.raises(SpecError) as dup:
        await create_group("t1", "bank-uat-refresh", created_by=None)
    assert (dup.value.code, dup.value.status) == ("group_name_taken", 409)
    assert await get_group("t2", group.id) is None
    assert await get_group("t1", group.id) == group
    second, _, _ = await create_group("t1", "second-group", created_by=None)
    page, cursor = await list_groups("t1", limit=1)
    assert [g.id for g in page] == [second.id] and cursor is not None
    rest, end = await list_groups("t1", limit=1, cursor=cursor)
    assert [g.id for g in rest] == [group.id] and end is None
    with pytest.raises(ValueError):
        await create_group("t1", "Not a valid name!", created_by=None)


async def _source(settings: Settings, kek: bytes) -> tuple[str, str, str]:
    src = await create_connection(
        "t1",
        ConnectionSpec(
            name="bank-src",
            engine="postgres",
            params=ConnectionParams(host="127.0.0.1", database="bank"),
            secret_ref="dps/src/test-pg",
            roles={"source"},
        ),
        settings=settings,
        kek=kek,
        created_by="test",
    )
    uat = await create_connection(
        "t1",
        ConnectionSpec(
            name="bank-uat",
            engine="postgres",
            params=ConnectionParams(host="127.0.0.1", database="bank"),
            secret_ref="dps/src/test-pg-rw",
            roles={"target"},
        ),
        settings=settings,
        kek=kek,
        created_by="test",
    )
    snap = make_snapshot("t1", connection_id=src.id, object_ref="bank.customers")
    await create_snapshot(snap)
    await accept_snapshot("t1", snap.id, accepted_by="owner")
    return src.id, uat.id, snap.id


def _spec(src: str, uat: str, snap: str, **extra: Any) -> RunSpec:
    return RunSpec.model_validate(
        {
            "mode": "obfuscate",
            "snapshot_id": snap,
            "source": {
                "connection_id": src,
                "object": {"owner": "bank", "name": "customers"},
                "selection": {"where": "segment = 'retail' AND risk_score > 10"},
            },
            "target": {
                "write_mode": "table",
                "connection_id": uat,
                "object": {"owner": "bank_uat", "name": "customers"},
            },
            "purpose": {"ticket": "CHG-1", "legal_basis": "legitimate interest"},
            **extra,
        }
    )


async def _submit(spec: RunSpec, settings: Settings, kek: bytes, **kw: Any) -> Any:
    return await submit_run(
        spec, tenant_id="t1", principal=LOCAL_PRINCIPAL, settings=settings, kek=kek, **kw
    )


async def test_submit_run_with_a_group_key(migrated_db: Settings, kek: bytes) -> None:
    src, uat, snap = await _source(migrated_db, kek)
    group, key, _ = await create_group("t1", "g-one", created_by=None)
    other = generate_key_material("key").model_dump(mode="json")
    with pytest.raises(SpecError) as err:
        await _submit(
            _spec(src, uat, snap, group_id=group.id, consistency={"key_material": other}),
            migrated_db,
            kek,
        )
    assert (err.value.code, err.value.status) == ("group_key_mismatch", 409)
    with pytest.raises(SpecError) as missing:
        await _submit(_spec(src, uat, snap, group_id="grp_missing"), migrated_db, kek)
    assert (missing.value.code, missing.value.status) == ("group_not_found", 404)
    mine = {"key_material": key.model_dump(mode="json")}
    sub = await _submit(
        _spec(src, uat, snap, group_id=group.id, consistency=mine), migrated_db, kek
    )
    assert sub.key_material is None and not sub.replay  # the caller already holds the key
    assert sub.job.group_id == group.id and sub.job.kid == key.kid
    spec_text = json.dumps(sub.job.spec)
    assert "retail" not in spec_text and key.dek not in spec_text  # templates and ids only
    assert sub.job.spec["source"]["selection"]["params"] == {"l1": "<bound>", "l2": "<bound>"}


async def test_submission_keys_replays_and_wrapping(migrated_db: Settings, kek: bytes) -> None:
    src, uat, snap = await _source(migrated_db, kek)
    first = await _submit(_spec(src, uat, snap), migrated_db, kek, idempotency_key="idem-1")
    assert first.key_material and first.key_material["dek"] and not first.replay
    again = await _submit(_spec(src, uat, snap), migrated_db, kek, idempotency_key="idem-1")
    assert again.replay and again.job.id == first.job.id and again.key_material is None
    async with db.acquire_queue(None) as conn:
        sealed = await conn.fetchval(
            f"SELECT key_enc FROM {db.schema_ident()}.dps_job WHERE id = $1", first.job.id
        )
    secret = open_json(kek, bytes(sealed), JobSecret, aad=f"dps_job.key:{first.job.id}")
    assert secret.key_material and secret.key_material["kid"] == first.key_material["kid"]
    jobs_before = len((await jobs.list_jobs("t1"))[0])
    bad_wrap = {"wrap_key": {"type": "rsa-oaep-256", "public_key_pem": "not a pem"}}
    with pytest.raises(SpecError) as err:
        await _submit(_spec(src, uat, snap, consistency=bad_wrap), migrated_db, kek)
    assert (err.value.code, err.value.status) == ("wrap_key_invalid", 422)
    assert len((await jobs.list_jobs("t1"))[0]) == jobs_before  # refused before a job exists
    pem = (
        rsa.generate_private_key(public_exponent=65537, key_size=2048)
        .public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )
    wrapped = await _submit(
        _spec(
            src,
            uat,
            snap,
            consistency={"wrap_key": {"type": "rsa-oaep-256", "public_key_pem": pem}},
        ),
        migrated_db,
        kek,
    )
    assert wrapped.key_material and wrapped.key_material["dek"] is None
    assert wrapped.key_material["wrapped_dek"] and wrapped.job.spec["consistency"]["wrapped"]
    with pytest.raises(SpecError) as gone:
        await _submit(_spec("c_missing", uat, snap), migrated_db, kek)
    assert (gone.value.code, gone.value.status) == ("connection_not_found", 404)
    with pytest.raises(SpecError) as no_kek:
        await _submit(_spec(src, uat, snap), migrated_db, None)  # type: ignore[arg-type]
    assert (no_kek.value.code, no_kek.value.status) == ("kek_unconfigured", 503)
    bad_key = {"key_material": {"alg": "nope", "kid": "k_1", "dek": "x"}}
    with pytest.raises(SpecError) as invalid:
        await _submit(_spec(src, uat, snap, consistency=bad_key), migrated_db, kek)
    assert (invalid.value.code, invalid.value.status) == ("key_material_invalid", 422)


async def test_retry_secret_must_match_the_job(migrated_db: Settings, kek: bytes) -> None:
    src, uat, snap = await _source(migrated_db, kek)
    sub = await _submit(_spec(src, uat, snap), migrated_db, kek)
    assert sub.key_material is not None
    other = ConsistencySpec(key_material=generate_key_material("key").model_dump(mode="json"))
    with pytest.raises(SpecError) as err:
        await seal_retry_secret(sub.job, other, kek=kek)
    assert (err.value.code, err.value.status) == ("key_material_mismatch", 409)
    with pytest.raises(SpecError) as none:
        await seal_retry_secret(sub.job, ConsistencySpec(), kek=kek)
    assert none.value.code == "key_material_required"
    sealed = await seal_retry_secret(
        sub.job, ConsistencySpec(key_material=sub.key_material), kek=kek
    )
    secret = open_json(kek, sealed, JobSecret, aad=f"dps_job.key:{sub.job.id}")
    assert secret.key_material and secret.key_material["kid"] == sub.job.kid
