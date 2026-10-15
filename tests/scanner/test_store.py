from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from dps import db, jobs
from dps.jobs import JobKind, JobPhase
from dps.layers.connectors.evidence import EvidencePack, pack_sha256
from dps.layers.scanner.models import (
    FieldRecord,
    PageRecord,
    RequestCount,
    RiskFlag,
    ScanEvent,
    ValidationMessage,
)
from dps.layers.scanner.store import ScanStore

pytestmark = pytest.mark.db
T = "t-a"
STORE = ScanStore("dps")


async def _scan(tenant: str = T) -> str:
    job = await jobs.create_job(
        tenant_id=tenant,
        kind=JobKind.scan,
        phase=JobPhase.scan,
        spec={"url": "x"},
        mode="synthesize",
    )
    return job.id


def _page(page_no: int, status: str = "queued", **kw: object) -> PageRecord:
    data: dict[str, object] = dict(
        page_no=page_no,
        url_redacted=f"http://demo-site:8410/p{page_no}",
        depth=0 if page_no == 1 else 1,
        parent_page_no=None if page_no == 1 else 1,
        status=status,
        discovered_via="start" if page_no == 1 else "link",
        template=f"demo-site:8410/p{page_no}",
    )
    data.update(kw)
    return PageRecord.model_validate(data)


def _field(page_no: int, field_no: int, **kw: object) -> FieldRecord:
    data: dict[str, object] = dict(
        page_no=page_no,
        form_index=1,
        field_no=field_no,
        selector=f"form#apply > input:nth-of-type({field_no})",
        tag="input",
        input_type="text",
        name_attr=f"f{field_no}",
        id_attr=None,
        label="Email address",
        label_source="label",
        autocomplete="email",
        pattern=None,
        maxlength=80,
        required=True,
        options_count=None,
        entity_type="EMAIL_ADDRESS",
        sensitivity="high",
        confidence=0.95,
        detectors=["token", "label"],
        evidence=["autocomplete:email", "label:email"],
        fill_synthesize="persona.one@example.org",
        fill_obfuscate="tok_x@example.org",
        fill_status="filled",
        risk_codes=["A1"],
    )
    data.update(kw)
    return FieldRecord.model_validate(data)


def _req(
    method: str, decision: str, count: int, *, page_no: int | None = None, reason: str = ""
) -> RequestCount:
    return RequestCount(
        page_no=page_no,
        method=method,
        resource_type="fetch",
        decision=decision,  # type: ignore[arg-type]
        reason=reason or ("method" if decision == "blocked" else "ok"),
        host="demo-site",
        count=count,
    )


def _event(seq: int, kind: str = "navigate") -> ScanEvent:
    return ScanEvent(
        seq=seq,
        ts=datetime.now(UTC),
        kind=kind,  # type: ignore[arg-type]
        method="GET",
        result="200",
        page_no=1,
        target="http://demo-site:8410/apply",
        note_code="start",
        counts={"fields": seq},
    )


def test_schema_name_is_validated() -> None:
    for bad in ("Bad", "dps; DROP TABLE x", "1dps", ""):
        with pytest.raises(ValueError):
            ScanStore(bad)


async def test_page_upsert_then_update(migrated_db, queue, tenant_conn) -> None:  # noqa: ANN001
    job_id = await _scan()
    async with tenant_conn(T) as conn:
        await STORE.upsert_page(conn, T, job_id, _page(1))
        flag = RiskFlag(
            flag="pii_in_query_string", severity="high", page_no=1, query_keys=["email"]
        )
        await STORE.upsert_page(
            conn,
            T,
            job_id,
            _page(
                1,
                "done",
                artefacts={"original": "a1"},
                risk_flags=[flag],
                rendered_findings={"EMAIL_ADDRESS": 2},
                validation={"synthesize": {"checked": 3, "accepted": 3, "rejected": 0}},
                forms=[{"form_index": 1, "method": "post", "field_count": 3}],
                http_status=200,
                capture_status="captured",
                timings={"load_ms": 120},
                form_count=1,
                field_count=3,
            ),
        )
        page = await STORE.get_page(conn, job_id, 1)
        assert await STORE.get_page(conn, job_id, 2) is None
    assert page is not None and page.status == "done" and page.artefacts == {"original": "a1"}
    assert page.risk_flags == [flag] and page.rendered_findings == {"EMAIL_ADDRESS": 2}
    assert page.validation["synthesize"]["checked"] == 3 and page.forms[0]["method"] == "post"
    assert (page.http_status, page.capture_status, page.timings) == (
        200,
        "captured",
        {"load_ms": 120},
    )
    assert page.updated_at >= page.created_at
    async with tenant_conn("t-b") as conn:  # RLS: another tenant sees nothing
        assert await STORE.get_page(conn, job_id, 1) is None


async def test_list_pages_pages_by_page_no(migrated_db, queue, tenant_conn) -> None:  # noqa: ANN001
    job_id = await _scan()
    async with tenant_conn(T) as conn:
        for n in (3, 1, 2, 4):
            await STORE.upsert_page(conn, T, job_id, _page(n))
        assert [p.page_no for p in await STORE.list_pages(conn, job_id)] == [1, 2, 3, 4]
        tail = await STORE.list_pages(conn, job_id, after_page_no=1, limit=2)
    assert [p.page_no for p in tail] == [2, 3]


async def test_replace_fields_is_idempotent_per_page(migrated_db, queue, tenant_conn) -> None:  # noqa: ANN001
    job_id = await _scan()
    msg = ValidationMessage(mode="synthesize", source="native", codes=["typeMismatch"], text="x")
    async with tenant_conn(T) as conn:
        await STORE.replace_fields(conn, T, job_id, 2, [_field(2, 1), _field(2, 2)])
        await STORE.replace_fields(conn, T, job_id, 1, [_field(1, 1), _field(1, 2)])
        await STORE.replace_fields(
            conn, T, job_id, 1, [_field(1, 7, validation_messages=[msg], placeholder_shape="99")]
        )
        page1 = await STORE.list_fields(conn, job_id, 1)
        page2 = await STORE.list_fields(conn, job_id, 2)
        everything = await STORE.list_fields(conn, job_id)
        with pytest.raises(ValueError):
            await STORE.replace_fields(conn, T, job_id, 1, [_field(2, 1)])
    assert [f.field_no for f in page1] == [7] and [f.field_no for f in page2] == [1, 2]
    assert len(everything) == 3
    f = page1[0]
    assert f.validation_messages == [msg] and f.detectors == ["token", "label"]
    assert f.evidence == ["autocomplete:email", "label:email"] and f.risk_codes == ["A1"]
    assert f.placeholder_shape == "99" and f.fill_status == "filled"
    assert f.confidence == pytest.approx(0.95)


async def test_request_counts_accumulate(migrated_db, queue, tenant_conn) -> None:  # noqa: ANN001
    job_id = await _scan()
    async with tenant_conn(T) as conn:
        await STORE.add_request_counts(conn, T, job_id, [_req("POST", "blocked", 2)])
        # the same key twice in one batch is summed, not refused by ON CONFLICT
        await STORE.add_request_counts(
            conn, T, job_id, [_req("POST", "blocked", 1), _req("POST", "blocked", 2)]
        )
        await STORE.add_request_counts(conn, T, job_id, [_req("GET", "allowed", 4, page_no=1)])
        await STORE.add_request_counts(conn, T, job_id, [])
        rows = await STORE.list_requests(conn, job_id)
        page1 = await STORE.list_requests(conn, job_id, 1)
    posts = [r for r in rows if r.method == "POST"]
    assert len(posts) == 1 and posts[0].count == 5 and posts[0].page_no is None
    assert [(r.method, r.count) for r in page1] == [("GET", 4)]


async def test_events_page_by_seq(migrated_db, queue, tenant_conn) -> None:  # noqa: ANN001
    job_id = await _scan()
    async with tenant_conn(T) as conn:
        await STORE.append_events(conn, T, job_id, [_event(n) for n in range(1, 6)])
        await STORE.append_events(conn, T, job_id, [_event(3, "error")])  # ignored: seq taken
        await STORE.append_events(conn, T, job_id, [])
        events = await STORE.list_events(conn, job_id, after_seq=2, limit=2)
        third = (await STORE.list_events(conn, job_id, after_seq=2, limit=1))[0]
    assert [e.seq for e in events] == [3, 4]
    assert third.kind == "navigate" and third.target == "http://demo-site:8410/apply"
    assert third.counts == {"fields": 3} and third.note_code == "start"


async def test_summary_counts(migrated_db, queue, tenant_conn) -> None:  # noqa: ANN001
    job_id = await _scan()
    async with tenant_conn(T) as conn:
        empty = await STORE.summary_counts(conn, job_id)
        await STORE.upsert_page(
            conn,
            T,
            job_id,
            _page(
                1,
                "done",
                form_count=1,
                field_count=2,
                personal_field_count=2,
                critical_field_count=1,
                artefacts={"filled_synthesize": "a2"},
                capture_status="captured",
            ),
        )
        await STORE.upsert_page(conn, T, job_id, _page(2, "done", form_count=1, field_count=1))
        await STORE.upsert_page(conn, T, job_id, _page(3, "skipped", status_reason="deny"))
        await STORE.upsert_page(conn, T, job_id, _page(4))
        await STORE.replace_fields(conn, T, job_id, 1, [_field(1, 1), _field(1, 2)])
        await STORE.replace_fields(conn, T, job_id, 2, [_field(2, 1)])
        await STORE.add_request_counts(conn, T, job_id, [_req("POST", "blocked", 4)])
        await STORE.add_request_counts(conn, T, job_id, [_req("GET", "allowed", 9, page_no=1)])
        await STORE.append_events(conn, T, job_id, [_event(1), _event(2), _event(3, "fill")])
        summary = await STORE.summary_counts(conn, job_id)
    assert empty["by_status"] == {} and empty["blocked"] == {} and empty["pages_visited"] == 0
    assert summary["by_status"] == {"done": 2, "skipped": 1, "queued": 1}
    assert summary["pages_visited"] == 2 and summary["pages_discovered"] == 4
    assert summary["pages_queued"] == 1 and summary["classified"] == 2
    assert summary["filled"] == 1 and summary["captures"] == 1
    assert (summary["forms"], summary["fields"]) == (2, 3)
    assert (summary["personal"], summary["critical"]) == (2, 1)
    assert summary["blocked"] == {"POST": 4} and summary["non_get_allowed"] == 0
    assert summary["navigations"] == 2


async def test_summary_measures_non_get_requests_that_were_allowed(
    migrated_db,  # noqa: ANN001
    queue,  # noqa: ANN001
    tenant_conn,  # noqa: ANN001
) -> None:
    job_id = await _scan()
    async with tenant_conn(T) as conn:
        await STORE.add_request_counts(conn, T, job_id, [_req("POST", "allowed", 1)])
        await STORE.add_request_counts(conn, T, job_id, [_req("HEAD", "allowed", 3)])
        assert (await STORE.summary_counts(conn, job_id))["non_get_allowed"] == 1


async def test_summary_measures_requests_that_reached_a_scope_path(
    migrated_db,  # noqa: ANN001
    queue,  # noqa: ANN001
    tenant_conn,  # noqa: ANN001
) -> None:
    """Review B2: a refused scope path is blocked; one that left (an HTTPS redirect hop) is
    counted as reached, never hidden."""
    job_id = await _scan()
    async with tenant_conn(T) as conn:
        await STORE.add_request_counts(conn, T, job_id, [_req("GET", "blocked", 5, reason="deny")])
        assert (await STORE.summary_counts(conn, job_id))["scope_paths_reached"] == 0
        await STORE.add_request_counts(conn, T, job_id, [_req("GET", "allowed", 1, reason="deny")])
        await STORE.add_request_counts(
            conn, T, job_id, [_req("GET", "allowed", 2, reason="not_allowed")]
        )
        summary = await STORE.summary_counts(conn, job_id)
    assert summary["scope_paths_reached"] == 3 and summary["blocked"] == {"GET": 5}


async def test_reset_scan_clears_all_four_tables(migrated_db, queue, tenant_conn) -> None:  # noqa: ANN001
    job_id = await _scan()
    other = await _scan()
    async with tenant_conn(T) as conn:
        for jid in (job_id, other):
            await STORE.upsert_page(conn, T, jid, _page(1, "done"))
            await STORE.replace_fields(conn, T, jid, 1, [_field(1, 1)])
            await STORE.add_request_counts(conn, T, jid, [_req("POST", "blocked", 1)])
            await STORE.append_events(conn, T, jid, [_event(1)])
        await STORE.reset_scan(conn, job_id)
        assert await STORE.list_pages(conn, job_id) == []
        assert await STORE.list_fields(conn, job_id) == []
        assert await STORE.list_requests(conn, job_id) == []
        assert await STORE.list_events(conn, job_id) == []
        assert len(await STORE.list_pages(conn, other)) == 1  # other scans untouched
        assert len(await STORE.list_events(conn, other)) == 1


async def test_registered_hosts_and_workers_online(migrated_db, queue, tenant_conn) -> None:  # noqa: ANN001
    async with tenant_conn(T) as conn:
        assert await STORE.registered_hosts(conn, T) == []
        await conn.execute(
            "INSERT INTO dps.dps_tenant (tenant_id, registered_hosts) VALUES ($1, '{demo-site}')", T
        )
        assert await STORE.registered_hosts(conn, T) == ["demo-site"]
        assert await STORE.browser_workers_online(conn, heartbeat_seconds=60) == 0
    await jobs.register_worker("bw:1:aaaaaa", role="browser-worker", version="t")
    await jobs.register_worker("bw:2:bbbbbb", role="browser-worker", version="t")
    await jobs.register_worker("w:1:cccccc", role="worker", version="t")
    async with db.acquire_queue(None) as conn:
        await conn.execute(
            "UPDATE dps.dps_worker SET last_seen = now() - interval '1 hour' "
            "WHERE worker_id = 'bw:2:bbbbbb'"
        )
    async with tenant_conn(T) as conn:
        assert await STORE.browser_workers_online(conn, heartbeat_seconds=60) == 1


async def test_export_artefacts_and_evidence(migrated_db, queue, tenant_conn) -> None:  # noqa: ANN001
    job_id = await _scan()
    async with tenant_conn(T) as conn:
        assert await STORE.export_artefacts(conn, job_id) == {}
        for art, kind in (
            ("art_j1", "export_json"),
            ("art_c1", "export_csv"),
            ("art_s", "screenshot"),
        ):
            await conn.execute(
                "INSERT INTO dps.dps_artefact (id, tenant_id, job_id, kind, store, object_key, "
                "content_type, bytes, sha256) VALUES ($1, $2, $3, $4, 'local', 'k', 'x', 1, 'h')",
                art,
                T,
                job_id,
                kind,
            )
        assert await STORE.export_artefacts(conn, job_id) == {"json": "art_j1", "csv": "art_c1"}
        pack = EvidencePack(
            id="ev_scan1",
            tenant_id=T,
            job_id=job_id,
            job_kind="scan",
            outcome="succeeded",
            label="Synthetic UI scan",
            mode="synthesize",
            created_at=datetime.now(UTC),
            engine_version="test",
            source={"kind": "ui"},
            target={},
            policy={"name": "gdpr-pseudonymise"},
            snapshot={},
            consistency={},
            columns=[],
            reveal_map=[],
            counts={"pages": 1},
            verification={},
            scan={"pages": 1},
            timings={},
            partitions=[],
            errors=[],
            config_hash="h",
        )
        expires = datetime.now(UTC) + timedelta(days=30)
        sha = pack_sha256(pack)
        ev_id = await STORE.insert_evidence(
            conn, T, job_id, pack, pack_sha256=sha, html_artefact_id="art_h", expires_at=expires
        )
        row = await conn.fetchrow("SELECT * FROM dps.dps_evidence WHERE id = $1", ev_id)
        with pytest.raises(ValueError):
            await STORE.insert_evidence(
                conn, "t-b", job_id, pack, pack_sha256=sha, html_artefact_id=None, expires_at=None
            )
    assert ev_id == "ev_scan1" and row["pack_sha256"] == sha and row["html_artefact_id"] == "art_h"
    assert row["pack"]["scan"] == {"pages": 1} and row["job_id"] == job_id
