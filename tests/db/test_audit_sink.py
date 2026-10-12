"""DbAuditSink against the metadata database: per-tenant rows under RLS, value-free failures."""

from __future__ import annotations

import asyncio

import pytest

from dps import db
from dps.audit import AuditEvent, DbAuditSink

pytestmark = pytest.mark.db


async def test_rows_land_per_tenant_under_rls(migrated_db) -> None:  # noqa: ANN001
    sink = DbAuditSink(db.acquire, migrated_db)
    sink.start()
    for tenant in ("t1", "t2", "t1"):
        sink.write(
            AuditEvent(
                trace_id="x",
                route="/api/v1/connections",
                tenant_id=tenant,
                key_id="k",
                mode="",
                policy="-",
                action="connection.create",
                status=201,
                counts={"columns": 3},
            )
        )
    await sink.stop()
    assert sink.healthy
    async with db.acquire("t1") as conn:
        assert await conn.fetchval("SELECT count(*) FROM dps.dps_audit_log") == 2
        row = await conn.fetchrow(
            "SELECT route, action, status, outcome, mode, policy, counts FROM dps.dps_audit_log "
            "LIMIT 1"
        )
    assert (row["route"], row["action"], row["status"], row["outcome"]) == (
        "/api/v1/connections",
        "connection.create",
        201,
        "ok",
    )
    assert row["mode"] is None and row["counts"]["columns"] == 3
    async with db.acquire("t2") as conn:
        assert await conn.fetchval("SELECT count(*) FROM dps.dps_audit_log") == 1


async def test_writer_flushes_while_running(migrated_db) -> None:  # noqa: ANN001
    sink = DbAuditSink(db.acquire, migrated_db.model_copy(update={"audit_flush_ms": 20}))
    sink.start()
    sink.write(
        AuditEvent(trace_id="x", route="/r", tenant_id="t1", key_id="k", mode="", policy="-")
    )
    for _ in range(100):
        async with db.acquire("t1") as conn:
            if await conn.fetchval("SELECT count(*) FROM dps.dps_audit_log"):
                break
        await asyncio.sleep(0.02)
    else:
        pytest.fail("the writer did not flush while running")
    await sink.stop()
    async with db.acquire("t1") as conn:
        assert await conn.fetchval("SELECT count(*) FROM dps.dps_audit_log") == 1


async def test_flush_failure_marks_unhealthy_without_detail(migrated_db, capsys) -> None:  # noqa: ANN001
    class Boom(Exception):
        pass

    def broken(_tenant):  # noqa: ANN001, ANN202
        raise Boom("customer value 123-45-6789")

    sink = DbAuditSink(broken, migrated_db)  # type: ignore[arg-type]
    sink.start()
    sink.write(
        AuditEvent(trace_id="x", route="/r", tenant_id="t1", key_id="k", mode="", policy="-")
    )
    await sink.stop()
    assert sink.healthy is False and sink.last_error == "Boom"
    assert "123-45-6789" not in "".join(capsys.readouterr())
