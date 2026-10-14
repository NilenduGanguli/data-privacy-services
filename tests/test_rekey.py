"""KEK rotation (M5 Task 16): ``python -m dps.rekey`` re-seals job and connection blobs that only
``DPS_JOB_KEK_PREVIOUS`` opens, leaves unreadable rows alone and prints counts only."""

from __future__ import annotations

import base64
import json

import pytest
from pydantic import SecretStr

from dps import db, jobs
from dps.config import Settings
from dps.connections import Credentials, create_connection, load_credentials
from dps.jobs import JobKind, JobPhase
from dps.jobs_models import ConnectionParams, ConnectionSpec, InlineCredentials
from dps.rekey import EXIT_UNREADABLE, amain, rekey
from dps.secrets import SealError, open_sealed, seal

pytestmark = pytest.mark.db
KEK_A, KEK_B, KEK_C = (bytes([n]) * 32 for n in (0xA1, 0xB2, 0xC3))  # test-only constants
SENTINEL = "Sentinel-Password-7f3e"


def _b64(kek: bytes) -> str:
    return base64.b64encode(kek).decode()


def _with(settings: Settings, current: bytes, previous: bytes | None = None) -> Settings:
    return settings.model_copy(
        update={
            "job_kek": _b64(current),
            "job_kek_previous": _b64(previous) if previous else "",
            "inline_secrets_allowed": True,
        }
    )


async def _job_sealed_with(kek: bytes, tenant: str = "t1") -> tuple[str, bytes]:
    job = await jobs.create_job(tenant_id=tenant, kind=JobKind.run, phase=JobPhase.prepare, spec={})
    blob = seal(kek, b'{"key_material": null}', aad=f"dps_job.key:{job.id}")
    async with db.acquire(tenant) as conn:
        await conn.execute(
            f"UPDATE {db.schema_ident()}.dps_job SET key_enc = $2 WHERE id = $1", job.id, blob
        )
    return job.id, blob


async def _key_enc(job_id: str) -> bytes:
    async with db.acquire_queue(None) as conn:
        value = await conn.fetchval(
            f"SELECT key_enc FROM {db.schema_ident()}.dps_job WHERE id = $1", job_id
        )
    return bytes(value)


async def _connection(settings: Settings) -> str:
    spec = ConnectionSpec(
        name="inline-src",
        engine="postgres",
        params=ConnectionParams(host="db.example", database="bank"),
        credentials=InlineCredentials(username="u", password=SecretStr(SENTINEL)),
    )
    conn = await create_connection("t1", spec, settings=settings, kek=KEK_A, created_by="tester")
    return conn.id


async def _creds(settings: Settings, connection_id: str, kek: bytes) -> Credentials:
    from dps.connections import get_connection

    conn = await get_connection("t1", connection_id)
    assert conn is not None
    return await load_credentials(conn, settings=settings, kek=kek)


async def test_rekey_moves_previous_kek_blobs_to_the_current_kek(migrated_db: Settings) -> None:
    old = _with(migrated_db, KEK_A)
    job_id, _ = await _job_sealed_with(KEK_A)
    connection_id = await _connection(old)  # sealed by M2's own create path with KEK A
    rotated = _with(migrated_db, KEK_B, KEK_A)
    counts = await rekey(rotated)
    assert (counts.rows, counts.resealed, counts.already_current, counts.unreadable) == (2, 2, 0, 0)
    blob = await _key_enc(job_id)
    open_sealed(KEK_B, blob, aad=f"dps_job.key:{job_id}")
    with pytest.raises(SealError):
        open_sealed(KEK_A, blob, aad=f"dps_job.key:{job_id}")
    creds = await _creds(rotated, connection_id, KEK_B)
    assert creds.password.get_secret_value() == SENTINEL
    with pytest.raises(Exception):  # noqa: B017 - SecretUnavailable: KEK A no longer opens it
        await _creds(old, connection_id, KEK_A)
    again = await rekey(rotated)
    assert (again.resealed, again.already_current, again.unreadable) == (0, 2, 0)


async def test_rekey_dry_run_and_unreadable_rows(
    migrated_db: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    job_a, blob_a = await _job_sealed_with(KEK_A)
    job_c, blob_c = await _job_sealed_with(KEK_C)  # a KEK nobody holds any more
    rotated = _with(migrated_db, KEK_B, KEK_A)
    dry = await rekey(rotated, dry_run=True)
    assert (dry.rows, dry.resealed, dry.unreadable) == (2, 1, 1)
    assert await _key_enc(job_a) == blob_a and await _key_enc(job_c) == blob_c  # nothing changed
    code = await amain([], settings=rotated)  # closes the pools when it ends
    assert code == EXIT_UNREADABLE
    line = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert line == {"already_current": 0, "resealed": 1, "rows": 2, "unreadable": 1}
    await db.init_pool(migrated_db)
    await db.init_worker_pool(migrated_db)
    assert await _key_enc(job_c) == blob_c  # the unreadable row is unchanged
    open_sealed(KEK_B, await _key_enc(job_a), aad=f"dps_job.key:{job_a}")
    out = capsys.readouterr()
    assert SENTINEL not in out.out + out.err and _b64(KEK_A) not in out.out + out.err
