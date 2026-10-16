from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from dps.app import create_app
from dps.audit import MemoryAuditSink
from dps.config import Settings
from dps.engine.detect.custom import PolicyCustomDetector, RecognizerSpec
from dps.engine.taxonomy import Sensitivity, entity_info

KEYS = [{"name": "ro", "key": "dps_ro", "tenant_ids": ["retail"], "scopes": ["detect"]}]
RO = {"X-API-KEY": "dps_ro"}
TEXT = (
    "Escalated by EMP-204417 (staff id) to the Pune branch. Fixture EMP-000000 is seeded. "
    "CHG-48213 also cites EMP-31882, which has five digits."
)
REC = {
    "name": "employee-id",
    "supported_entity": "EMPLOYEE_ID",
    "patterns": [{"name": "employee-id", "regex": r"\bEMP-\d{6}\b", "score": 0.85}],
    "context": ["staff id"],
    "allow_list": ["EMP-000000"],
}


@pytest.fixture
def client() -> TestClient:
    settings = Settings(
        _env_file=None,
        env="local",
        auth_enabled=True,
        api_keys=json.dumps(KEYS),
        llm_provider="stub",
        ner_enabled=False,
        rate_limit_enabled=False,
    )
    return TestClient(create_app(settings, audit_sink=MemoryAuditSink()))


def test_entities_lists_the_taxonomy(client: TestClient) -> None:
    body = client.get("/api/v1/entities", headers=RO).json()
    by = {i["name"]: i for i in body["items"]}
    assert len(by) >= 46 and by["SSN"]["sensitivity"] == "CRITICAL" and by["SSN"]["builtin"] is True
    assert by["SSN"]["family"] == "government_id" and "financial" in body["families"]


def test_recognizer_test_returns_offsets_only(client: TestClient) -> None:
    r = client.post(
        "/api/v1/policies/test-recognizer", headers=RO, json={"recognizer": REC, "text": TEXT}
    )
    body = r.json()
    assert r.status_code == 200 and body["count"] == 1
    match = body["matches"][0]
    assert (
        TEXT[match["start"] : match["end"]] == "EMP-204417"
        and match["entity_type"] == "EMPLOYEE_ID"
    )
    assert (
        "EMP-204417" not in r.text and "allow_list" in body["yaml"] and "EMP-000000" in body["yaml"]
    )


def test_invalid_recognizers_are_422_with_a_code(client: TestClient) -> None:
    broken = dict(REC, patterns=[{"name": "p", "regex": "(unclosed", "score": 0.5}])
    r = client.post(
        "/api/v1/policies/test-recognizer", headers=RO, json={"recognizer": broken, "text": "x"}
    )
    assert r.status_code == 422 and r.json()["code"] == "invalid_recognizer"
    huge = dict(REC, patterns=[{"name": "p", "regex": "a" * 501, "score": 0.5}])
    r = client.post(
        "/api/v1/policies/test-recognizer", headers=RO, json={"recognizer": huge, "text": "x"}
    )
    assert r.status_code == 422 and r.json()["code"] == "invalid_recognizer"


def test_recognizer_tests_never_register_entities(client: TestClient) -> None:
    rec = {
        "name": "t",
        "supported_entity": "TEMP_ENTITY_X",
        "sensitivity": "CRITICAL",
        "patterns": [{"name": "p", "regex": "Z{3}", "score": 0.9}],
    }
    assert (
        client.post(
            "/api/v1/policies/test-recognizer", headers=RO, json={"recognizer": rec, "text": "ZZZ"}
        ).json()["count"]
        == 1
    )
    assert entity_info("TEMP_ENTITY_X").sensitivity.value == "MEDIUM"


def test_presets_route_returns_summaries(client: TestClient) -> None:
    items = client.get("/api/v1/policies/presets", headers=RO).json()
    assert {"name", "description", "version", "hash", "mode", "reversible"} <= set(items[0])


def test_too_many_patterns_and_value_free_errors(client: TestClient) -> None:
    many = dict(REC, patterns=[{"name": f"p{i}", "regex": "Z", "score": 0.5} for i in range(51)])
    r = client.post(
        "/api/v1/policies/test-recognizer", headers=RO, json={"recognizer": many, "text": "Z"}
    )
    assert r.status_code == 422 and r.json()["code"] == "invalid_recognizer"
    blank = dict(REC, deny_list=["Blue Falcon", " "])
    r = client.post(
        "/api/v1/policies/test-recognizer", headers=RO, json={"recognizer": blank, "text": "x"}
    )
    assert r.status_code == 422 and r.json()["code"] == "invalid_recognizer"
    assert "Blue Falcon" not in r.text and "EMP-000000" not in r.text


def test_entities_never_list_custom_types_any_tenant_registered() -> None:
    """B3: the taxonomy registry was process-wide (M0), so custom types are not listed until M2
    stores policies per tenant; before the fix tenant ``wealth`` saw ``retail``'s type. Since S3
    review round 1 a request's custom tiers stay in that request; the process-global registry
    (code outside a request, the second registration below) is still never listed."""
    keys = [
        {"name": "a", "key": "dps_a", "tenant_ids": ["retail"], "scopes": ["detect"]},
        {"name": "b", "key": "dps_b", "tenant_ids": ["wealth"], "scopes": ["detect"]},
    ]
    settings = Settings(
        _env_file=None,
        env="local",
        auth_enabled=True,
        api_keys=json.dumps(keys),
        llm_provider="stub",
        ner_enabled=False,
        rate_limit_enabled=False,
    )
    client = TestClient(create_app(settings, audit_sink=MemoryAuditSink()))
    recognizer = {
        "name": "falcon-project",
        "supported_entity": "M1_FALCON_PROJECT_ID",
        "sensitivity": "HIGH",
        "patterns": [{"name": "p", "regex": r"FAL-\d{4}", "score": 0.9}],
    }
    policy = {"preset": "gdpr-pseudonymise", "detect": {"custom_recognizers": [recognizer]}}
    r = client.post(
        "/api/v1/detect",
        headers={"X-API-KEY": "dps_a"},
        json={"data": {"t": "FAL-1234"}, "policy": policy},
    )
    assert r.status_code == 200
    found = [f for f in r.json()["findings"] if f["entity_type"] == "M1_FALCON_PROJECT_ID"]
    assert found and found[0]["sensitivity"] == "HIGH"  # registered for that request
    # S3 review round 1 B3: a request's custom tiers are its own, never process-wide
    assert entity_info("M1_FALCON_PROJECT_ID").sensitivity == Sensitivity.MEDIUM
    PolicyCustomDetector(
        [
            RecognizerSpec(
                name="m1-entities-test",
                supported_entity="M1_ENTITIES_TEST_TYPE",
                deny_list=("zzz",),
                sensitivity=Sensitivity.HIGH,
            )
        ],
        [],
    )
    for key in ("dps_b", "dps_a"):
        body = client.get("/api/v1/entities", headers={"X-API-KEY": key}).json()
        names = {i["name"] for i in body["items"]}
        assert "M1_FALCON_PROJECT_ID" not in names and "M1_ENTITIES_TEST_TYPE" not in names
        assert "falcon" not in json.dumps(body) and all(i["builtin"] for i in body["items"])
        assert "SSN" in names and len(names) >= 46
