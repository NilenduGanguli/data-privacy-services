"""``POST /api/v1/job-groups/{id}/consistency-check`` (contract §9, M3 Task 17) and migration 020:
route, stored ``last_check``, audit, value-free error mapping. The check itself is replaced by a
canned coroutine here (it is tested offline in ``tests/connectors/test_consistency.py`` and end to
end in ``tests/e2e/test_m3_three_way.py``)."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import asyncpg
import pytest

from dps.config import Settings
from dps.layers.api.routers import groups as groups_router
from dps.layers.connectors.base import ConnectorError
from dps.layers.connectors.consistency import (
    ConsistencyCheckResult,
    ConsistencyError,
    MemberResult,
    PairResult,
)
from tests.fixtures.db import api_settings

pytestmark = pytest.mark.db
JOBS = {"X-API-KEY": "dps_jobs", "X-DPS-Tenant": "t1"}
T2 = {"X-API-KEY": "dps_jobs", "X-DPS-Tenant": "t2"}
DETECT = {"X-API-KEY": "dps_detect", "X-DPS-Tenant": "t1"}
SENTINEL = "SENTINEL-driver-text-4111111111111111"


def canned(group_id: str, *, passed: bool = True) -> ConsistencyCheckResult:
    members = [
        MemberResult(
            job_id=f"job_{i}", engine=engine, connection_id=f"conn_{i}", object=obj,
            path=path, entity_type="CUSTOMER_ID", method="fpe_digits", params_hash="0" * 12,
            form=form, width=8 if form == "prefixed_digits" else None,
            prefix_len=5 if form == "prefixed_digits" else None, found=500, anchor=i == 0,
        )
        for i, (engine, obj, path, form) in enumerate(
            [
                ("oracle", "CRM_MASKED.CUSTOMERS", "$.CUSTOMER_ID", "numeric"),
                ("mongo", "profiles", "$.customerId", "prefixed_digits"),
                ("postgres", "bank_uat.customers", "$.customer_id", "numeric"),
            ]
        )
    ]  # fmt: skip
    pairs = [
        PairResult(left=a, right=b, checked=500, matched=500, coverage=1.0, consistent=passed)
        for a, b in ((0, 1), (0, 2), (1, 2))
    ]
    return ConsistencyCheckResult(
        group_id=group_id, checked_at=datetime(2026, 10, 13, tzinfo=UTC), sample_requested=500,
        sampled=500, anchor=0, members=members, pairs=pairs, all_members_matched=500,
        attributes=[], methods_agree=True, problems=[], passed=passed,
        consistent_pairs=3 if passed else 0, total_pairs=3, min_coverage=1.0, duration_ms=12.5,
    )  # fmt: skip


@pytest.fixture
def client(db_settings: Settings, tmp_path: Path) -> Iterator[tuple[Any, Any]]:
    """An app with a 1-second connector timeout (the check's ``wait_for`` bound)."""
    from fastapi.testclient import TestClient

    from dps.app import create_app
    from dps.audit import MemoryAuditSink
    from tests.fixtures.db import _truncate

    settings = api_settings(db_settings, tmp_path, connector_statement_timeout_seconds=1.0)
    sink = MemoryAuditSink()
    try:
        with TestClient(create_app(settings, audit_sink=sink)) as c:
            yield c, sink
    finally:
        asyncio.run(_truncate(db_settings))


def _group(c: Any, name: str = "crm-uat-refresh", headers: dict[str, str] = JOBS) -> str:
    made = c.post("/api/v1/job-groups", json={"name": name}, headers=headers)
    assert made.status_code == 201
    return str(made.json()["group"]["id"])


def _check(c: Any, group_id: str, body: dict[str, Any] | None = None, **kw: Any) -> Any:
    headers = kw.pop("headers", JOBS)
    return c.post(
        f"/api/v1/job-groups/{group_id}/consistency-check", json=body or {}, headers=headers
    )


def test_migration_020_adds_the_columns(db_settings: Settings) -> None:
    async def run() -> dict[str, str]:
        conn = await asyncpg.connect(
            host=db_settings.pg_host, port=db_settings.pg_port, user=db_settings.pg_user,
            password=db_settings.pg_password, database=db_settings.pg_database,
        )  # fmt: skip
        try:
            rows = await conn.fetch(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_schema = $1 AND table_name = 'dps_job_group' "
                "AND column_name IN ('last_check', 'last_check_at')",
                db_settings.pg_schema,
            )
            return {r["column_name"]: r["data_type"] for r in rows}
        finally:
            await conn.close()

    assert asyncio.run(run()) == {
        "last_check": "jsonb",
        "last_check_at": "timestamp with time zone",
    }


def test_requires_jobs_scope(client: tuple[Any, Any]) -> None:
    c, _ = client
    gid = _group(c)
    assert _check(c, gid, headers=DETECT).status_code == 403
    assert _check(c, gid, headers={}).status_code == 401


def test_unknown_or_foreign_group_is_404(client: tuple[Any, Any]) -> None:
    c, sink = client
    missing = _check(c, "grp_missing")
    assert missing.status_code == 404 and missing.json()["code"] == "group_not_found"
    gid = _group(c)
    foreign = _check(c, gid, headers=T2)
    assert foreign.status_code == 404 and foreign.json()["code"] == "group_not_found"
    refused = [e for e in sink.events if e.action == "group.consistency_check"]
    assert [(e.outcome, e.status) for e in refused] == [("rejected", 404), ("rejected", 404)]


def test_result_is_stored_returned_and_audited(
    client: tuple[Any, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    c, sink = client
    gid = _group(c)
    seen: dict[str, Any] = {}

    async def fake(**kw: Any) -> ConsistencyCheckResult:
        seen.update(kw)
        return canned(kw["group"].id)

    monkeypatch.setattr(groups_router, "run_consistency_check", fake)
    before = c.get(f"/api/v1/job-groups/{gid}", headers=JOBS).json()["group"]
    assert before["last_check"] is None and before["last_check_at"] is None
    r = _check(c, gid, {"sample": 500})
    assert r.status_code == 200
    expected = canned(gid).model_dump(mode="json")
    assert r.json() == expected
    assert seen["request"].sample == 500 and seen["jobs"] == [] and seen["group"].id == gid
    shown = c.get(f"/api/v1/job-groups/{gid}", headers=JOBS).json()["group"]
    assert shown["last_check"] == expected and shown["last_check_at"] is not None
    events = [e for e in sink.events if e.action == "group.consistency_check"]
    assert len(events) == 1 and (events[0].outcome, events[0].status) == ("passed", 200)
    assert events[0].counts == {"members": 3, "pairs": 3, "consistent_pairs": 3, "sampled": 500}

    async def failing(**kw: Any) -> ConsistencyCheckResult:
        return canned(kw["group"].id, passed=False)

    monkeypatch.setattr(groups_router, "run_consistency_check", failing)
    assert _check(c, gid).json()["passed"] is False
    last = [e for e in sink.events if e.action == "group.consistency_check"][-1]
    assert last.outcome == "failed" and last.counts["consistent_pairs"] == 0


def test_check_errors_map_to_codes(
    client: tuple[Any, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    c, _ = client
    gid = _group(c)

    def raising(exc: BaseException) -> Any:
        async def run(**kw: Any) -> ConsistencyCheckResult:
            raise exc

        return run

    async def slow(**kw: Any) -> ConsistencyCheckResult:
        await asyncio.sleep(3)
        return canned(gid)

    cases = [
        (raising(ConsistencyError(409, "member_not_ready")), 409, "member_not_ready"),
        (raising(ConsistencyError(422, "no_shared_entity")), 422, "no_shared_entity"),
        (
            raising(ConnectorError("connection_failed", SENTINEL, retryable=True)),
            503,
            "connection_failed",
        ),  # fmt: skip
        (raising(ConnectorError("source_error", SENTINEL)), 502, "source_error"),
        (slow, 504, "check_timeout"),
    ]
    for fn, status, code in cases:
        monkeypatch.setattr(groups_router, "run_consistency_check", fn)
        r = _check(c, gid)
        assert (r.status_code, r.json()["code"]) == (status, code)
        assert set(r.json()) >= {"detail", "code"} and SENTINEL not in r.text
    assert c.get(f"/api/v1/job-groups/{gid}", headers=JOBS).json()["group"]["last_check"] is None


def test_request_validation(client: tuple[Any, Any]) -> None:
    c, _ = client
    gid = _group(c)
    one = [{"job_id": "job_1", "path": "$.CUSTOMER_ID"}]
    for body in ({"sample": 5}, {"pairs": one}, {"min_coverage": 0}, {"min_coverage": 1.5}):
        assert _check(c, gid, body).status_code == 422, body


def test_a_repeated_member_is_refused_and_not_stored(client: tuple[Any, Any]) -> None:
    """The real check (not the canned one): the same ``(job_id, path)`` twice is ``422
    member_duplicate``, audited as a refusal, and leaves the group's ``last_check`` unset."""
    c, sink = client
    gid = _group(c)
    member = {"job_id": "job_1", "path": "$.customerId"}
    r = _check(c, gid, {"pairs": [member, dict(member)]})
    assert (r.status_code, r.json()["code"]) == (422, "member_duplicate")
    shown = c.get(f"/api/v1/job-groups/{gid}", headers=JOBS).json()["group"]
    assert shown["last_check"] is None and shown["last_check_at"] is None
    refused = [e for e in sink.events if e.action == "group.consistency_check"]
    assert [(e.outcome, e.status) for e in refused] == [("rejected", 422)]
    # one job with two different paths is a legitimate self-join: not a duplicate (the job is
    # simply not in this group)
    self_join = [member, {"job_id": "job_1", "path": "$.referrerId"}]
    assert _check(c, gid, {"pairs": self_join}).json()["code"] == "member_not_in_group"


def test_a_rerun_writing_the_same_target_is_refused_and_not_stored(
    client: tuple[Any, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The real check over two run jobs of the group that write the same target (a refresh: the
    same spec submitted twice) is ``422 member_duplicate`` before any target is opened, audited
    as a refusal, and leaves the stored ``last_check`` as it was (M3 G4 review r2, B2-r2)."""
    from dps.jobs import Job
    from tests.connectors.test_consistency import MDB_OBJ, ORA_OBJ, World, _rerun, make_job

    c, sink = client
    gid = _group(c)

    async def stored(**kw: Any) -> ConsistencyCheckResult:
        return canned(kw["group"].id, passed=False)

    monkeypatch.setattr(groups_router, "run_consistency_check", stored)
    assert _check(c, gid).status_code == 200
    before = c.get(f"/api/v1/job-groups/{gid}", headers=JOBS).json()["group"]
    assert before["last_check"]["passed"] is False
    monkeypatch.undo()

    w = World(20)
    run_jobs = [
        make_job("job_ora", "c_ora", ORA_OBJ, "s_ora", group_id=gid),
        make_job("job_run1", "c_mdb", MDB_OBJ, "s_mdb", group_id=gid),
        _rerun("job_run2", "c_mdb", MDB_OBJ, "s_mdb").model_copy(update={"group_id": gid}),
    ]
    snapshots = w.snapshots()
    opened: list[str] = []

    async def list_jobs(tenant: str, **kw: Any) -> tuple[list[Job], None]:
        assert kw["group_id"] == gid
        return run_jobs, None

    async def get_snapshot(tenant: str, sid: str) -> Any:
        return snapshots.get(sid)

    async def get_connection(tenant: str, cid: str) -> Any:
        from tests.connectors.test_consistency import _connection

        return _connection(cid, "mongo", host=f"{cid}.uat", port=27017, database="crm_uat")

    async def opener(*a: Any, **kw: Any) -> Any:
        opened.append("x")
        raise AssertionError("no target may be opened for a refused check")

    monkeypatch.setattr(groups_router.jobs, "list_jobs", list_jobs)
    monkeypatch.setattr(groups_router, "get_snapshot", get_snapshot)
    monkeypatch.setattr(groups_router, "get_connection", get_connection)
    monkeypatch.setattr("dps.layers.connectors.consistency.open_connector", opener)
    monkeypatch.setattr(
        "dps.layers.connectors.consistency.run_consistency_check.__kwdefaults__",
        {"opener": opener},
    )
    for pairs in (["job_run1", "job_run2"], ["job_ora", "job_run1", "job_run2"]):
        body = {"pairs": [{"job_id": j, "path": "$.customerId"} for j in pairs]}
        body["pairs"][0]["path"] = "$.CUSTOMER_ID" if pairs[0] == "job_ora" else "$.customerId"
        r = _check(c, gid, body)
        assert (r.status_code, r.json()["code"]) == (422, "member_duplicate"), pairs
    assert opened == []
    after = c.get(f"/api/v1/job-groups/{gid}", headers=JOBS).json()["group"]
    assert after["last_check"] == before["last_check"]
    assert after["last_check_at"] == before["last_check_at"]
    events = [e for e in sink.events if e.action == "group.consistency_check"]
    assert [(e.outcome, e.status) for e in events] == [
        ("failed", 200),
        ("rejected", 422),
        ("rejected", 422),
    ]
