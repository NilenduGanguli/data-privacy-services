"""S1: a value pasted into the wrong field never comes back in an error body.

422 bodies are ``{"detail": [{"loc", "msg", "type"}], "code": "invalid_request"}`` (pydantic's
``input``/``ctx``/``url`` dropped, quoting messages replaced); the engine's 400 ``invalid policy``
answers and the recognizer test's 422 never quote the value either (S3 finder p03/p08).
"""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
from pydantic import ValidationError

from dps.app import create_app
from dps.config import Settings
from dps.jobs_models import RunSpec
from dps.layers.api.validation import scrubbed

SSN = "123-45-6789"
NUMBER = "123456789"


@pytest.fixture
def client() -> TestClient:
    return TestClient(
        create_app(Settings(_env_file=None, env="local", auth_enabled=False, llm_provider="stub"))
    )


def _assert_scrubbed(body: dict[str, object], *values: str) -> None:
    text = json.dumps(body)
    for value in values:
        assert value not in text
    assert body["code"] == "invalid_request"
    detail = body["detail"]
    assert isinstance(detail, list) and detail
    for item in detail:
        assert isinstance(item, dict) and set(item) == {"loc", "msg", "type"}


def test_422_never_echoes_the_input(client: TestClient) -> None:
    r = client.post(
        "/api/v1/detect",
        json={"data": {"x": 1}, "text": int(NUMBER), "options": {"locale": [SSN]}},
    )
    assert r.status_code == 422
    _assert_scrubbed(r.json(), NUMBER, SSN)


def test_a_dict_key_that_is_a_value_is_not_echoed(client: TestClient) -> None:
    """An unknown key of a strict model is named by its location: one that is not a field name
    is shown as ``<key>``."""
    r = client.post(
        "/api/v1/detect", json={"text": "x", "policy": {"preset": "gdpr-pseudonymise", SSN: 1}}
    )
    assert r.status_code == 400, r.text
    assert SSN not in r.text and "<key>" in r.text


def test_a_discriminator_tag_is_not_echoed() -> None:
    """p08: ``target.pk_strategy.kind`` set to an SSN gave ``union_tag_invalid`` quoting it."""
    spec = {
        "mode": "obfuscate",
        "snapshot_id": "s_1",
        "source": {"connection_id": "c_1", "object": {"owner": "bank", "name": "customers"}},
        "target": {"write_mode": "append", "pk_strategy": {"kind": SSN}},
        "purpose": {"ticket": "CHG-1", "legal_basis": "legitimate interest"},
    }
    with pytest.raises(ValidationError) as caught:
        RunSpec.model_validate(spec)
    assert SSN in json.dumps(caught.value.errors(), default=str)  # pydantic quotes it
    response = asyncio.run(scrubbed(None, RequestValidationError(caught.value.errors())))  # type: ignore[arg-type]
    assert response.status_code == 422
    _assert_scrubbed(json.loads(bytes(response.body)), SSN)


@pytest.mark.parametrize(
    "policy",
    [
        {  # custom.py: "entity type '<value>' must match …"
            "preset": "gdpr-pseudonymise",
            "detect": {
                "custom_recognizers": [
                    {
                        "name": "r",
                        "supported_entity": SSN,
                        "patterns": [{"name": "p", "regex": r"\d{3}", "score": 0.5}],
                    }
                ]
            },
        },
        {"preset": "gdpr-pseudonymise", "treat": {"SSN": {"method": SSN}}},  # "unknown method …"
    ],
)
def test_an_invalid_inline_policy_is_not_echoed(client: TestClient, policy: dict) -> None:  # type: ignore[type-arg]
    r = client.post("/api/v1/detect", json={"text": "hello", "policy": policy})
    assert r.status_code in (400, 422), r.text
    assert SSN not in r.text


def test_the_recognizer_test_never_echoes_the_entity(client: TestClient) -> None:
    r = client.post(
        "/api/v1/policies/test-recognizer",
        json={
            "recognizer": {
                "name": "r",
                "supported_entity": SSN,
                "patterns": [{"name": "p", "regex": r"\d{3}", "score": 0.5}],
            },
            "text": "nothing",
        },
    )
    assert r.status_code == 422
    assert SSN not in r.text
