"""One wire error shape across the integrated milestones (contract §8.2): M1's ``CodedError`` and
``CodedHTTPException``, ``dps.auth``'s ``CodedAuthHTTPException`` and M2's ``DpsHttpError`` all
answer ``{"detail", "code"}`` (``DpsHttpError`` plus its ``extra``), wherever they are raised — an
M1 error inside an M2 route included; a plain ``HTTPException`` keeps M0's ``{"detail"}``."""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi import HTTPException, Request
from fastapi.testclient import TestClient

from dps.app import create_app
from dps.audit import MemoryAuditSink
from dps.auth import CodedAuthHTTPException
from dps.config import Settings
from dps.errors import CodedError
from dps.layers.api.common import CodedHTTPException, run_engine
from dps.layers.api.deps import rejected
from dps.layers.api.errors import DpsHttpError, to_http

pytest.importorskip("asyncpg")  # the M2 routers (and their error handlers) need the db extra

KEYS = [{"name": "full", "key": "dps_full", "tenant_ids": ["*"], "scopes": ["*"]}]
H = {"X-API-KEY": "dps_full", "X-DPS-Tenant": "t1"}


def test_to_http_keeps_an_m1_error_s_status_and_code() -> None:
    err = to_http(CodedError("ocr_unavailable", "OCR is configured but down", status=503))
    assert (err.status, err.code, err.detail) == (
        503,
        "ocr_unavailable",
        "OCR is configured but down",
    )
    coded = to_http(CodedHTTPException(413, "payload_too_large", "payload exceeds 10 bytes"))
    assert (coded.status, coded.code, coded.detail) == (
        413,
        "payload_too_large",
        "payload exceeds 10 bytes",
    )
    auth = to_http(CodedAuthHTTPException(403, "tenant id is reserved", "tenant_reserved"))
    assert (auth.status, auth.code) == (403, "tenant_reserved")
    # anything else is still a value-free 500
    assert to_http(RuntimeError("a value")).body() == {
        "detail": "internal error",
        "code": "internal_error",
    }


@pytest.fixture
def client():  # noqa: ANN201
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        env="local",
        auth_enabled=True,
        api_keys=json.dumps(KEYS),
        llm_provider="stub",
        rate_limit_enabled=False,
    )
    app = create_app(settings, audit_sink=MemoryAuditSink())

    def raise_(exc: Exception):  # noqa: ANN202
        async def route(request: Request) -> dict[str, Any]:
            request.state.principal = None
            raise exc

        return route

    async def l1_coded(request: Request) -> dict[str, Any]:
        def fail() -> None:
            raise CodedError("conflict_x", "nope", status=409)

        await run_engine(request, mode="test", policy_name="-", call=fail)
        return {}

    async def m2_coded(request: Request) -> dict[str, Any]:
        request.state.tenant_id = "t1"
        try:
            raise CodedError("conflict_x", "nope", status=409)
        except Exception as exc:  # noqa: BLE001 - what every M2 route does
            raise rejected(request, "test.action", exc) from None

    routes = {
        "m1": raise_(CodedHTTPException(422, "invalid_request", "bad field")),
        "auth": raise_(CodedAuthHTTPException(403, "tenant id is reserved", "tenant_reserved")),
        "m2": raise_(DpsHttpError(409, "job_terminal", "the job is terminal", {"state": "failed"})),
        "plain": raise_(HTTPException(status_code=404, detail="not here")),
    }
    for name, route in routes.items():
        app.add_api_route(f"/api/v1/_shape/{name}", route, methods=["POST"])
    app.add_api_route("/api/v1/_shape/l1_coded", l1_coded, methods=["POST"])
    app.add_api_route("/api/v1/_shape/m2_coded", m2_coded, methods=["POST"])
    with TestClient(app) as c:
        yield c


@pytest.mark.parametrize(
    ("name", "status", "body"),
    [
        ("m1", 422, {"detail": "bad field", "code": "invalid_request"}),
        ("auth", 403, {"detail": "tenant id is reserved", "code": "tenant_reserved"}),
        ("m2", 409, {"detail": "the job is terminal", "code": "job_terminal", "state": "failed"}),
        ("l1_coded", 409, {"detail": "nope", "code": "conflict_x"}),
        ("m2_coded", 409, {"detail": "nope", "code": "conflict_x"}),
        ("plain", 404, {"detail": "not here"}),
    ],
)
def test_every_raise_site_answers_the_one_wire_shape(
    client: TestClient, name: str, status: int, body: dict[str, Any]
) -> None:
    r = client.post(f"/api/v1/_shape/{name}", headers=H)
    assert (r.status_code, r.json()) == (status, body)
