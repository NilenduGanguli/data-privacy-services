"""M2 audit additions (offline): row mapping, strict/lenient queue, 503, route templates."""

from __future__ import annotations

import json

import pytest
from fastapi import Request
from fastapi.testclient import TestClient
from prometheus_client import REGISTRY

from dps.app import create_app
from dps.audit import AuditEvent, AuditUnavailable, DbAuditSink, MemoryAuditSink, audit_row
from dps.config import Settings
from dps.engine.service import EngineError
from dps.layers.api.common import run_engine


def _event(**kw) -> AuditEvent:  # noqa: ANN003
    base = dict(
        trace_id="t",
        route="/api/v1/jobs/{job_id}/cancel",
        tenant_id="t1",
        key_id="k",
        mode="",
        policy="-",
        action="job.cancel",
        status=200,
        job_id="j_1",
        counts={"partitions": 4},
    )
    base.update(kw)
    return AuditEvent(**base)


def test_audit_row_maps_fields_and_counts() -> None:
    row = audit_row(_event(findings_total=3, methods_used={"det_token": 3}))
    ts, tenant, key, trace, route, action, mode, policy, outcome, status, job_id, counts = row
    assert (tenant, route, action, status, job_id, outcome) == (
        "t1",
        "/api/v1/jobs/{job_id}/cancel",
        "job.cancel",
        200,
        "j_1",
        "ok",
    )
    assert counts["partitions"] == 4 and counts["findings_total"] == 3
    assert counts["methods_used"] == {"det_token": 3}
    assert audit_row(_event(action="", mode="obfuscate"))[5] == "obfuscate"
    assert audit_row(_event(action="", mode="plan:synthesize"))[5] == "plan"
    assert audit_row(_event(action="", mode=""))[5] == "request"


def _dropped() -> float:
    return REGISTRY.get_sample_value("dps_audit_dropped_total") or 0.0


async def test_strict_sink_refuses_when_full_and_lenient_drops() -> None:
    def no_database(_tenant):  # noqa: ANN001, ANN202 - the writer's flush fails; stop() still returns
        raise AssertionError("no database in this test")

    strict = DbAuditSink(
        no_database,  # type: ignore[arg-type]
        Settings(_env_file=None, audit_queue_max=1, audit_strict=True),  # type: ignore[call-arg]
    )
    strict.start()
    strict.write(_event())
    with pytest.raises(AuditUnavailable):
        strict.write(_event())
    lenient = DbAuditSink(
        no_database,  # type: ignore[arg-type]
        Settings(_env_file=None, audit_queue_max=1, audit_strict=False),  # type: ignore[call-arg]
    )
    lenient.start()
    lenient.write(_event())
    before = _dropped()
    lenient.write(_event())  # dropped, counted, no exception
    assert _dropped() == before + 1
    await strict.stop(timeout=0.5)
    await lenient.stop(timeout=0.5)
    assert strict.healthy is False and strict.last_error == "AssertionError"


async def test_sink_without_a_running_writer_only_logs(capsys) -> None:  # noqa: ANN001
    def no_database(_tenant):  # noqa: ANN001, ANN202
        raise AssertionError("not reached: the writer was never started")

    sink = DbAuditSink(no_database, Settings(_env_file=None))  # type: ignore[arg-type,call-arg]
    sink.write(_event())  # the synchronous log line is the record; nothing is queued
    await sink.stop()
    assert sink.healthy and "job.cancel" in "".join(capsys.readouterr())


class _Raising:
    def write(self, event: AuditEvent) -> None:
        raise AuditUnavailable()


def test_api_returns_503_when_strict_audit_is_unavailable() -> None:
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        auth_enabled=False,
        llm_provider="stub",
        audit_strict=True,
        rate_limit_enabled=False,
    )
    with TestClient(create_app(settings, audit_sink=_Raising())) as client:
        r = client.post("/api/v1/detect", json={"data": {"email": "a@b.co"}})
    assert r.status_code == 503 and json.loads(r.text)["detail"] == "audit unavailable"
    assert "a@b.co" not in r.text


def test_audited_route_is_the_template_and_status_is_recorded() -> None:
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None, auth_enabled=False, llm_provider="stub", rate_limit_enabled=False
    )
    sink = MemoryAuditSink()
    app = create_app(settings, audit_sink=sink)

    def ok() -> dict[str, str]:
        return {"ok": "yes"}

    def fail() -> dict[str, str]:
        raise EngineError("unknown preset")

    async def probe(request: Request, kind: str) -> dict[str, str]:
        result, _ = await run_engine(
            request, mode="test", policy_name="-", call=ok if kind == "ok" else fail
        )
        return result

    app.add_api_route("/api/v1/_probe/{kind}", probe, methods=["POST"])
    with TestClient(app) as c:
        assert c.post("/api/v1/_probe/ok").status_code == 200
        assert c.post("/api/v1/_probe/customer-123").status_code == 400
        assert c.post("/api/v1/detect", json={"text": "x" * 10}).status_code == 200
    assert [(e.route, e.status) for e in sink.events] == [
        ("/api/v1/_probe/{kind}", 200),
        ("/api/v1/_probe/{kind}", 400),
        ("/api/v1/detect", 200),
    ]
    assert "customer-123" not in json.dumps([e.model_dump(mode="json") for e in sink.events])
