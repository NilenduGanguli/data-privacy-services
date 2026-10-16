"""S11: no ``/api/`` response may be stored by a cache — several carry key material (a DEK, a
synthesis seed, the one-time API key) or revealed originals. ``Cache-Control: no-store`` (and
``Pragma: no-cache`` for HTTP/1.0 caches) on every ``/api/`` answer — JSON, NDJSON, errors,
refusals; never on ``/health``; the console keeps its own caching and CSP."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from dps.app import FRONTEND_DIST, create_app
from dps.config import Settings


@pytest.fixture
def client() -> TestClient:
    return TestClient(
        create_app(Settings(_env_file=None, env="local", auth_enabled=False, llm_provider="stub"))
    )


def _no_store(response) -> bool:  # noqa: ANN001
    return response.headers.get("cache-control") == "no-store" and (
        response.headers.get("pragma") == "no-cache"
    )


def test_engine_answers_carrying_key_material_are_never_cached(client: TestClient) -> None:
    text = "Customer John Smith, SSN 123-45-6789, email john.smith@example.com"
    obfuscated = client.post("/api/v1/obfuscate", json={"text": text})
    assert obfuscated.status_code == 200 and "key_material" in obfuscated.json()
    assert _no_store(obfuscated)
    assert obfuscated.headers["x-content-type-options"] == "nosniff"
    synthesized = client.post("/api/v1/synthesize", json={"text": text})
    assert synthesized.status_code == 200 and _no_store(synthesized)
    stream = client.post(
        "/api/v1/obfuscate",
        json={"data": [{"ssn": "123-45-6789"}]},
        headers={"Accept": "application/x-ndjson"},
    )
    assert stream.status_code == 200 and _no_store(stream)


@pytest.mark.parametrize(
    "method,path,body",
    [
        ("GET", "/api/v1/policies/presets", None),
        ("GET", "/api/v1/entities", None),
        ("POST", "/api/v1/detect", {"text": 1}),  # a 422
        ("GET", "/api/v1/jobs", None),  # 503 without a database
        ("GET", "/api/v1/nope", None),  # 404
    ],
)
def test_every_api_answer_is_no_store(
    client: TestClient,
    method: str,
    path: str,
    body: dict | None,  # type: ignore[type-arg]
) -> None:
    assert _no_store(client.request(method, path, json=body))


def test_an_explicit_route_header_is_kept_and_health_is_cacheable(client: TestClient) -> None:
    health = client.get("/health")
    assert "cache-control" not in health.headers and "pragma" not in health.headers


def test_the_console_keeps_its_csp() -> None:
    if not (FRONTEND_DIST / "index.html").is_file():
        pytest.skip("console not built")
    client = TestClient(
        create_app(Settings(_env_file=None, env="local", auth_enabled=False, llm_provider="stub"))
    )
    index = client.get("/")
    assert "content-security-policy" in index.headers
    assert index.headers["cache-control"] == "no-cache"
