"""S3 review F-13: a retry and a job-group run bind the DEK, not only its public ``kid``.

``GET /jobs/{id}`` shows the kid to any ``jobs`` caller, and caller key material without a ``kcv``
gets one computed, so ``{kid: <job kid>, dek: <anything>}`` used to pass: a retry re-sealed another
DEK under the owner's kid (rows then revealed to wrong values silently), and a group run carried a
foreign DEK. The key check value (public, binds the DEK to the kid) is now stored with the job's
spec and the group's row, and compared in constant time; legacy rows without one keep the kid rule."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from dps.engine.transform.crypto import generate_key_material, key_check_value
from dps.jobs import Job, JobKind, JobPhase, JobState
from dps.jobs_models import ConsistencySpec, SpecError
from dps.layers.connectors.submit import seal_retry_secret
from tests.api.test_api_connections import JOBS
from tests.api.test_api_jobs import _run, seeded  # noqa: F401 - the fixture

KEK = bytes(range(32))


def _job(kid: str, kcv: str | None) -> Job:
    now = datetime.now(UTC)
    consistency: dict[str, Any] = {"kid": kid}
    if kcv is not None:
        consistency["kcv"] = kcv
    return Job(
        id="j_1",
        tenant_id="t1",
        kind=JobKind.run,
        state=JobState.failed,
        phase=JobPhase.prepare,
        mode="obfuscate",
        kid=kid,
        spec={"consistency": consistency, "target": {"write_mode": "table"}},
        created_at=now,
        updated_at=now,
    )


def _forged(km_json: dict[str, Any]) -> dict[str, Any]:
    other = generate_key_material("key").model_dump(mode="json")
    return {**km_json, "dek": other["dek"], "kcv": None}  # the owner's kid, another DEK


async def test_a_retry_with_another_dek_under_the_jobs_kid_is_refused() -> None:
    km = generate_key_material("key")
    owner = km.model_dump(mode="json")
    job = _job(km.kid, key_check_value(km))
    with pytest.raises(SpecError) as refused:
        await seal_retry_secret(job, ConsistencySpec(key_material=_forged(owner)), kek=KEK)
    assert (refused.value.code, refused.value.status) == ("key_material_mismatch", 409)
    assert await seal_retry_secret(job, ConsistencySpec(key_material=owner), kek=KEK)
    legacy = _job(km.kid, None)  # a job admitted before the kcv was stored: the kid rule
    assert await seal_retry_secret(legacy, ConsistencySpec(key_material=owner), kek=KEK)


@pytest.mark.db
def test_a_group_run_with_another_dek_under_the_groups_kid_is_refused(seeded) -> None:  # noqa: ANN001, F811
    c, src, uat = seeded["c"], seeded["src"], seeded["uat"]
    made = c.post("/api/v1/job-groups", json={"name": "bank-uat-kcv"}, headers=JOBS)
    assert made.status_code == 201, made.text
    group, key = made.json()["group"], made.json()["key_material"]
    assert group["kcv"] == key["kcv"]  # public: binds the DEK to the kid
    table = {
        "write_mode": "table",
        "connection_id": uat,
        "object": {"owner": "bank_uat", "name": "customers"},
    }

    def run(material: dict[str, Any]) -> Any:  # noqa: ANN401
        body = _run(src, seeded["snap_src"], table, group_id=group["id"])
        body["consistency"] = {"key_material": material}
        return c.post("/api/v1/jobs/run/validate", json=body, headers=JOBS)

    forged = run(_forged(key))
    assert (forged.status_code, forged.json()["code"]) == (409, "group_key_mismatch")
    assert run(key).status_code == 200
    queued = c.post(
        "/api/v1/jobs/run",
        json={
            **_run(src, seeded["snap_src"], table, group_id=group["id"]),
            "consistency": {"key_material": key},
        },
        headers=JOBS,
    )
    assert queued.status_code == 202, queued.text
    assert queued.json()["job"]["spec"]["consistency"]["kcv"] == key["kcv"]
