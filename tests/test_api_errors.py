from __future__ import annotations

import json

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from dps.app import create_app
from dps.audit import MemoryAuditSink
from dps.config import Settings
from dps.errors import CodedError
from dps.layers.api.common import policy_label, run_engine

KEYS = [{"name": "full", "key": "dps_full", "tenant_ids": ["*"], "scopes": ["*"]}]
H = {"X-API-KEY": "dps_full"}


@pytest.fixture
def app_and_sink():  # noqa: ANN201
    settings = Settings(
        _env_file=None,
        env="local",
        auth_enabled=True,
        api_keys=json.dumps(KEYS),
        llm_provider="stub",
        ner_enabled=False,
        rate_limit_enabled=False,
    )
    sink = MemoryAuditSink()
    app = create_app(settings, audit_sink=sink)

    async def boom(request: Request) -> dict[str, str]:
        def fail() -> None:
            raise CodedError("conflict_x", "nope", status=409)

        await run_engine(request, mode="test", policy_name="-", call=fail)
        return {}

    app.add_api_route("/api/v1/_boom", boom, methods=["POST"])
    return app, sink


def test_engine_errors_carry_codes(app_and_sink) -> None:  # noqa: ANN001
    app, _ = app_and_sink
    with TestClient(app) as c:
        r = c.post("/api/v1/detect", json={"data": {"a": 1}, "policy": "nope"}, headers=H)
        assert r.status_code == 400 and r.json()["code"] == "bad_request" and r.json()["detail"]


def test_coded_error_maps_status_and_code(app_and_sink) -> None:  # noqa: ANN001
    app, sink = app_and_sink
    with TestClient(app) as c:
        r = c.post("/api/v1/_boom", headers=H)
    assert r.status_code == 409 and r.json() == {"detail": "nope", "code": "conflict_x"}
    assert sink.events[-1].outcome == "conflict_x"


def test_policy_label_never_records_inline_bodies() -> None:
    inline = {
        "preset": "pci-dss",
        "detect": {
            "custom_recognizers": [
                {"name": "x", "supported_entity": "X_ID", "deny_list": ["Ananya"]}
            ]
        },
    }
    label = policy_label(inline, "gdpr-pseudonymise")
    assert (
        label.startswith("pci-dss+inline:")
        and "Ananya" not in label
        and len(label) == len("pci-dss+inline:") + 12
    )
    assert policy_label("gdpr-pseudonymise", "d") == "gdpr-pseudonymise"
    assert policy_label("x y!", "d") == "invalid" and policy_label(None, "d") == "d"
    assert policy_label({"treat": {}}, "d").startswith("custom+inline:")


def test_audit_policy_field_is_a_label(app_and_sink) -> None:  # noqa: ANN001
    app, sink = app_and_sink
    policy = {
        "preset": "gdpr-pseudonymise",
        "detect": {"declared_fields": [{"path": "$.secret_owner", "entity_type": "PERSON_NAME"}]},
    }
    with TestClient(app) as c:
        r = c.post(
            "/api/v1/obfuscate",
            json={"data": {"secret_owner": "Ananya Krishnan"}, "policy": policy},
            headers=H,
        )
    assert r.status_code == 200
    assert sink.events[-1].policy.startswith("gdpr-pseudonymise+inline:")
    assert "secret_owner" not in sink.events[-1].policy and "Ananya" not in sink.events[-1].policy
