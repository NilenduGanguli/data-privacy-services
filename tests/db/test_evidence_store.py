"""Evidence rows and artefacts in the metadata DB (append-only, tenant-scoped)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import asyncpg
import pytest

from dps import db, jobs
from dps.jobs import JobKind, JobPhase
from dps.layers.connectors.evidence import (
    build_pack,
    canonical_json,
    get_evidence,
    list_evidence,
    pack_sha256,
    save_evidence,
)
from dps.storage import ArtefactService, LocalObjectStore
from tests.connectors.factories import evidence_inputs

pytestmark = pytest.mark.db


async def test_save_get_list_and_append_only(migrated_db: Any, tmp_path: Path) -> None:
    inputs = evidence_inputs(mode="obfuscate")
    job = await jobs.create_job(
        tenant_id=inputs.job.tenant_id,
        kind=JobKind.run,
        phase=JobPhase.prepare,
        spec={},
        job_id=inputs.job.id,
    )
    pack = build_pack(inputs)
    artefacts = ArtefactService(LocalObjectStore(tmp_path), acquire=db.acquire)
    record = await save_evidence(pack, artefacts=artefacts, acquire=db.acquire, expires_at=None)
    assert record.html_artefact_id and record.json_artefact_id
    assert record.pack_sha256 == pack_sha256(pack)
    got = await get_evidence(job.tenant_id, record.id)
    assert got is not None and got.pack_sha256 == record.pack_sha256 and got.pack.job_id == job.id
    assert got.json_artefact_id == record.json_artefact_id
    assert got.html_artefact_id == record.html_artefact_id
    items, cursor = await list_evidence(job.tenant_id, job_id=job.id)
    assert [i.id for i in items] == [record.id] and items[0].passed and cursor is None
    assert items[0].write_mode == "table" and items[0].rows_written == 2500
    assert items[0].ticket == "CHG-48213" and items[0].source_object == "bank.customers"
    assert items[0].target == "bank_uat.customers" and items[0].html_artefact_id
    row, stream = await artefacts.open(job.tenant_id, record.json_artefact_id or "")
    assert row.kind == "evidence_json"
    body = b"".join([part async for part in stream])
    assert body == canonical_json(pack) and json.loads(body)["id"] == record.id
    html_row, html = await artefacts.open(job.tenant_id, record.html_artefact_id or "")
    assert html_row.content_type.startswith("text/html")
    assert record.pack_sha256.encode() in b"".join([part async for part in html])
    assert await get_evidence("t2", record.id) is None
    assert (await list_evidence("t2"))[0] == []
    async with db.acquire(job.tenant_id) as conn:
        with pytest.raises(asyncpg.RaiseError):
            await conn.execute(
                "UPDATE dps.dps_evidence SET pack_sha256 = 'x' WHERE id = $1", record.id
            )


async def test_list_pages_newest_first(migrated_db: Any, tmp_path: Path) -> None:
    artefacts = ArtefactService(LocalObjectStore(tmp_path), acquire=db.acquire)
    ids = []
    for _ in range(3):
        inputs = evidence_inputs(mode="synthesize")
        await jobs.create_job(
            tenant_id="t1", kind=JobKind.run, phase=JobPhase.prepare, spec={}, job_id=inputs.job.id
        )
        record = await save_evidence(
            build_pack(inputs), artefacts=artefacts, acquire=db.acquire, expires_at=None
        )
        ids.append(record.id)
    first, cursor = await list_evidence("t1", limit=2)
    assert [i.id for i in first] == ids[::-1][:2] and cursor is not None
    rest, end = await list_evidence("t1", limit=2, cursor=cursor)
    assert [i.id for i in rest] == [ids[0]] and end is None
    assert first[0].mode == "synthesize" and first[0].label.startswith("synthetic")
