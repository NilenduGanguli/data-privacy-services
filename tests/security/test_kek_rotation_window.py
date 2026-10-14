"""S3 review F-21: during a KEK rotation (``DPS_JOB_KEK`` = new, ``DPS_JOB_KEK_PREVIOUS`` = old,
before ``python -m dps.rekey`` ran) every sealed blob still opens — job keys and specs, inline
connection credentials — and an ``Idempotency-Key`` replay admitted under the old KEK is still a
replay. Contract §6.3: the previous KEK is "tried on open". ``dps.rekey`` keeps telling the two apart.
"""

from __future__ import annotations

import base64

import pytest

from dps.config import Settings
from dps.secrets import SealError, load_kek, open_sealed, seal
from tests.api.test_api_jobs import seeded  # noqa: F401 - the fixture

OLD = bytes(range(32))
NEW = bytes(range(1, 33))


def _settings(**kw: object) -> Settings:
    base: dict[str, object] = {
        "_env_file": None,
        "llm_provider": "stub",
        "job_kek": base64.b64encode(NEW).decode(),
        "job_kek_previous": base64.b64encode(OLD).decode(),
    }
    base.update(kw)
    return Settings(**base)  # type: ignore[arg-type]


def test_a_blob_sealed_under_the_previous_kek_opens_during_the_rotation() -> None:
    blob = seal(OLD, b"job secret", aad="dps_job.key:j1")
    kek = load_kek(_settings())  # what the app and the worker load
    assert kek == NEW
    assert open_sealed(kek, blob, aad="dps_job.key:j1") == b"job secret"
    with pytest.raises(SealError):  # the AAD still binds it to its row
        open_sealed(kek, blob, aad="dps_job.key:j2")
    with pytest.raises(SealError):  # rekey (and anyone asking strictly) can tell them apart
        open_sealed(kek, blob, aad="dps_job.key:j1", fallback=False)
    stranger = seal(bytes(32), b"x", aad="dps_job.key:j1")
    with pytest.raises(SealError):  # a third key never opens
        open_sealed(kek, stranger, aad="dps_job.key:j1")


def test_without_a_previous_kek_nothing_else_opens() -> None:
    blob = seal(OLD, b"job secret", aad="dps_job.key:j1")
    kek = load_kek(
        _settings(job_kek_previous="", job_kek=base64.b64encode(bytes(range(2, 34))).decode())
    )
    assert kek is not None
    with pytest.raises(SealError):
        open_sealed(kek, blob, aad="dps_job.key:j1")


def test_an_idempotency_replay_admitted_under_the_previous_kek_is_still_a_replay() -> None:
    from dps.layers.api.deps import IDEMPOTENCY_FP_INFO, fingerprint_alternates
    from dps.secrets import keyed_fingerprint

    body, route = b'{"x": 1}', "/api/v1/jobs/run"
    old_fp = keyed_fingerprint(OLD, info=IDEMPOTENCY_FP_INFO, salt=route, data=body, hex_chars=64)
    assert old_fp in fingerprint_alternates(_settings(), route=route, body=body)
    assert fingerprint_alternates(_settings(job_kek_previous=""), route=route, body=body) == []


@pytest.mark.db
def test_a_rotated_app_replays_and_opens_jobs_admitted_under_the_old_kek(seeded) -> None:  # noqa: ANN001, F811
    """End to end: a run admitted under the old KEK (the fixture's), then an app on the new KEK
    with the old one as previous: the same ``Idempotency-Key`` request is a replay (not 422
    ``idempotency_key_reused``) and the job's sealed key opens."""
    import asyncio

    import asyncpg

    from dps.jobs_models import JobSecret
    from dps.secrets import open_json
    from tests.api.test_api_connections import JOBS
    from tests.api.test_api_jobs import _run
    from tests.fixtures.db import TEST_KEK

    assert TEST_KEK == OLD  # the fixture's app seals under the old KEK
    c = seeded["c"]
    table = {
        "write_mode": "table",
        "connection_id": seeded["uat"],
        "object": {"owner": "bank_uat", "name": "customers"},
    }
    body = _run(seeded["src"], seeded["snap_src"], table)
    headers = {**JOBS, "Idempotency-Key": "rotation-1"}
    first = c.post("/api/v1/jobs/run", json=body, headers=headers)
    assert first.status_code == 202, first.text
    # the rotation: the same app restarted on the new KEK with the old one as previous (one app per
    # test process owns the pools, so the running app's settings and KEK are swapped in place)
    rotated = c.app.state.settings.model_copy(
        update={
            "job_kek": base64.b64encode(NEW).decode(),
            "job_kek_previous": base64.b64encode(OLD).decode(),
        }
    )
    c.app.state.settings, c.app.state.kek = rotated, load_kek(rotated)
    again = c.post("/api/v1/jobs/run", json=body, headers=headers)
    assert again.status_code == 200 and again.headers["idempotent-replay"] == "true", again.text
    assert again.json()["job"]["id"] == first.json()["job"]["id"]

    async def key_enc() -> bytes:
        s = seeded["settings"]
        conn = await asyncpg.connect(
            host=s.pg_host,
            port=s.pg_port,
            user=s.pg_user,
            password=s.pg_password,
            database=s.pg_database,
        )
        try:
            await conn.execute("SELECT set_config('app.tenant_id', 't1', false)")
            blob = await conn.fetchval(
                "SELECT key_enc FROM dps.dps_job WHERE id = $1", first.json()["job"]["id"]
            )
            assert isinstance(blob, bytes)
            return blob
        finally:
            await conn.close()

    kek = load_kek(rotated)
    assert kek == NEW
    secret = open_json(
        kek, asyncio.run(key_enc()), JobSecret, aad=f"dps_job.key:{first.json()['job']['id']}"
    )
    assert secret.key_material is not None
