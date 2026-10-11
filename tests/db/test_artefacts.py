"""ArtefactService: rows in dps_artefact (RLS) + objects in a store."""

from __future__ import annotations

import pytest

from dps import db, jobs
from dps.jobs import JobKind, JobPhase
from dps.storage import ArtefactService, LocalObjectStore, StorageError

pytestmark = pytest.mark.db


async def test_save_open_isolate_and_delete(migrated_db, tmp_path) -> None:  # noqa: ANN001
    job = await jobs.create_job(tenant_id="t1", kind=JobKind.run, phase=JobPhase.prepare, spec={})
    svc = ArtefactService(LocalObjectStore(tmp_path), acquire=db.acquire)
    art_id = await svc.save(
        tenant_id="t1",
        job_id=job.id,
        kind="evidence_json",
        data=b"{}",
        content_type="application/json",
        meta={"pages": 1},
    )
    row, chunks = await svc.open("t1", art_id)
    assert row.object_key == f"t/t1/{job.id}/{art_id}.json"
    assert b"".join([c async for c in chunks]) == b"{}"
    assert row.bytes == 2 and row.store == "local" and row.meta == {"pages": 1}
    assert await svc.get_row("t2", art_id) is None
    with pytest.raises(StorageError) as err:
        await svc.open("t2", art_id)
    assert err.value.code == "storage_not_found"
    assert await svc.delete_for_job("t1", job.id) == 1
    assert await svc.get_row("t1", art_id) is None
    assert not (tmp_path / row.object_key).exists()


async def test_a_refused_row_leaves_no_orphan_object(migrated_db, tmp_path) -> None:  # noqa: ANN001
    job = await jobs.create_job(tenant_id="t1", kind=JobKind.run, phase=JobPhase.prepare, spec={})
    svc = ArtefactService(LocalObjectStore(tmp_path), acquire=db.acquire)
    with pytest.raises(Exception):  # noqa: B017 - the kind CHECK constraint refuses the row
        await svc.save(
            tenant_id="t1", job_id=job.id, kind="bogus", data=b"x", content_type="text/plain"
        )
    assert [p for p in tmp_path.rglob("*") if p.is_file()] == []
