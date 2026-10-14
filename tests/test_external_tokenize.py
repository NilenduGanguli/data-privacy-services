"""M5 Task 8: ``external_tokenize`` through a bank tokenization service (adapter + vaultless mock).

Offline: the mock (``docker/tokenizer-mock/server.py``) runs in a thread on an ephemeral port;
``httpx.MockTransport`` stands in for broken services. No value, token or bearer token may reach an
error message.
"""

from __future__ import annotations

import importlib.util
import json
import ssl
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml
from fastapi.testclient import TestClient

from dps.app import create_app
from dps.config import Settings
from dps.engine.service import EngineError, Payload, PrivacyEngine, RequestOptions
from dps.engine.transform.external import ExternalServiceError, HttpTokenizer, build_tokenizer

ROOT = Path(__file__).resolve().parents[1]
MOCK = ROOT / "docker" / "tokenizer-mock" / "server.py"
TOKEN = "test-token"
POLICY = {
    "preset": "gdpr-pseudonymise",
    "treat": {"SSN": {"method": "external_tokenize", "profile": "ssn"}},
}
RECORD = {"customer_id": "CUST-884120", "ssn": "123-45-6789", "email": "ann.k@example-mail.in"}


@pytest.fixture(scope="module")
def mock_url() -> Iterator[str]:
    spec = importlib.util.spec_from_file_location("tokenizer_mock", MOCK)
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    server = module.make_server("127.0.0.1", 0, token=TOKEN, key="unit-test-key")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


#: SEC-4: external tokens are bound under a key derived from the job KEK (test literal).
KEK = "ERERERERERERERERERERERERERERERERERERERERERE="


def _settings(**kw: object) -> Settings:
    base: dict[str, object] = dict(
        _env_file=None, llm_provider="stub", ner_enabled=False, fpe_enabled=False, job_kek=KEK
    )
    base.update(kw)
    return Settings(**base)  # type: ignore[arg-type]


def test_round_trip_against_the_mock_service(mock_url: str) -> None:
    t = HttpTokenizer(mock_url, token=TOKEN)
    tokens = t.tokenize("SSN", ["123-45-6789", "987-65-4321"], profile="ssn")
    assert all(x.startswith("tk_") for x in tokens)
    assert tokens == t.tokenize("SSN", ["123-45-6789", "987-65-4321"], profile="ssn")
    assert t.tokenize("ITIN", ["123-45-6789"], profile="ssn") != tokens[:1]
    assert t.tokenize("SSN", ["123-45-6789"], profile="other") != tokens[:1]
    assert t.detokenize("SSN", tokens, profile="ssn") == ["123-45-6789", "987-65-4321"]
    assert t.health() and t.name == "http"


def test_batching_retries_and_response_validation() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(len(body["values"]))
        if len(calls) == 1:
            return httpx.Response(503)
        return httpx.Response(200, json={"tokens": [f"t{v[::-1]}" for v in body["values"]]})

    t = HttpTokenizer("http://svc", batch=2, retries=2, transport=httpx.MockTransport(handler))
    assert len(t.tokenize("SSN", ["a1", "b2", "c3", "d4", "e5"], profile=None)) == 5
    assert calls == [2, 2, 2, 1]  # first call retried once after a 503

    def echo(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"tokens": json.loads(request.content)["values"]})

    with pytest.raises(ExternalServiceError, match="untransformed"):
        HttpTokenizer("http://svc", transport=httpx.MockTransport(echo)).tokenize(
            "SSN", ["123-45-6789"], profile=None
        )
    short = httpx.MockTransport(lambda r: httpx.Response(200, json={"tokens": []}))
    with pytest.raises(ExternalServiceError, match="malformed"):
        HttpTokenizer("http://svc", transport=short).tokenize("SSN", ["x1"], profile=None)
    not_json = httpx.MockTransport(lambda r: httpx.Response(200, text="123-45-6789"))
    with pytest.raises(ExternalServiceError, match="malformed") as bad:
        HttpTokenizer("http://svc", transport=not_json).tokenize("SSN", ["x1"], profile=None)
    assert "123-45-6789" not in str(bad.value)
    refused = httpx.MockTransport(lambda r: httpx.Response(400, text="bad value 123-45-6789"))
    with pytest.raises(ExternalServiceError) as err:
        HttpTokenizer("http://svc", transport=refused).tokenize(
            "SSN", ["123-45-6789"], profile=None
        )
    assert "HTTP 400" in str(err.value) and "123-45-6789" not in str(err.value)


def test_the_bearer_token_is_sent_and_never_shown() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("Authorization", ""))
        return httpx.Response(503)

    t = HttpTokenizer(
        "http://svc", token="s3cr3t-bearer", retries=0, transport=httpx.MockTransport(handler)
    )
    with pytest.raises(ExternalServiceError) as err:
        t.tokenize("SSN", ["123-45-6789"], profile=None)
    assert seen == ["Bearer s3cr3t-bearer"]
    assert "s3cr3t" not in str(err.value) and "s3cr3t" not in repr(t)


def test_the_mock_requires_its_bearer_token(mock_url: str) -> None:
    with pytest.raises(ExternalServiceError, match="HTTP 401"):
        HttpTokenizer(mock_url, token="wrong", retries=0).tokenize(
            "SSN", ["123-45-6789"], profile=None
        )


def test_build_tokenizer_follows_the_settings(tmp_path: Path) -> None:
    assert build_tokenizer(_settings()) is None
    built = build_tokenizer(
        _settings(tokenizer_provider="http", tokenizer_url="http://tok.local:8420")
    )
    assert isinstance(built, HttpTokenizer)
    with pytest.raises(ValueError, match="https"):
        build_tokenizer(
            _settings(
                tokenizer_provider="http",
                tokenizer_url="http://tok.local:8420",
                egress_tls_required=True,
            )
        )
    assert isinstance(
        build_tokenizer(
            _settings(
                tokenizer_provider="http",
                tokenizer_url="https://tok.bank.example",
                egress_tls_required=True,
                tokenizer_ca_file=ssl.get_default_verify_paths().cafile or "",
            )
        ),
        HttpTokenizer,
    )


def test_engine_obfuscates_and_reveals_through_the_service(mock_url: str) -> None:
    engine = PrivacyEngine(_settings(), tokenizer=HttpTokenizer(mock_url, token=TOKEN))
    out = engine.obfuscate(Payload(data=RECORD), POLICY, RequestOptions())
    assert out.result["ssn"].startswith("tk_") and out.report.methods_used["external_tokenize"] == 1
    hints = [h.model_dump(mode="json") for h in out.reveal_hints]
    assert next(h for h in hints if h["method"] == "external_tokenize")["params"] == {
        "profile": "ssn"
    }
    back = engine.reveal(
        Payload(data=out.result),
        out.key_material.model_dump(mode="json"),  # type: ignore[union-attr]
        hints,
    )
    assert back.result == RECORD


def test_spans_in_free_text_are_tokenized_and_revealed(mock_url: str) -> None:
    engine = PrivacyEngine(_settings(), tokenizer=HttpTokenizer(mock_url, token=TOKEN))
    text = "Customer SSN 123-45-6789 called; second SSN 234-56-7891 on file."
    out = engine.obfuscate(Payload(text=text), POLICY, RequestOptions())
    assert "123-45-6789" not in out.result and out.result.count("tk_") == 2
    back = engine.reveal(
        Payload(text=out.result),
        out.key_material.model_dump(mode="json"),  # type: ignore[union-attr]
        [h.model_dump(mode="json") for h in out.reveal_hints],
    )
    assert back.result == text


def test_without_an_adapter_the_request_fails_closed() -> None:
    engine = PrivacyEngine(_settings(), tokenizer=None)
    plan = engine.plan(Payload(data=RECORD), POLICY, RequestOptions(), mode="obfuscate")
    assert any(
        r["method"] == "external_tokenize" and r["reason"].endswith("external:unconfigured")
        for r in plan.plan
    )
    with pytest.raises(EngineError, match="no tokenization adapter"):
        engine.obfuscate(Payload(data=RECORD), POLICY, RequestOptions())
    configured = PrivacyEngine(_settings(), tokenizer=HttpTokenizer("http://127.0.0.1:9"))
    plan = configured.plan(Payload(data=RECORD), POLICY, RequestOptions(), mode="obfuscate")
    assert not any("external:unconfigured" in r["reason"] for r in plan.plan)


def test_api_maps_an_outage_to_502() -> None:
    settings = _settings(auth_enabled=False, rate_limit_enabled=False)
    engine = PrivacyEngine(
        settings, tokenizer=HttpTokenizer("http://127.0.0.1:9", retries=0, timeout=0.2)
    )
    with TestClient(create_app(settings, engine=engine)) as client:
        r = client.post("/api/v1/obfuscate", json={"data": RECORD, "policy": POLICY})
        ready = client.get("/readyz").json()
    assert r.status_code == 502 and r.json() == {"detail": "tokenization service unavailable"}
    assert "123-45-6789" not in r.text
    tokenizer = ready["components"]["tokenizer"]
    assert tokenizer["ok"] is False and tokenizer["adapter"] == "http"


def test_batch_transformer_calls_the_service_once_per_batch() -> None:
    from dps.engine.batch import BatchTransformer, ColumnPlan
    from dps.engine.policy import MethodSpec, resolve_policy
    from dps.engine.transform.crypto import generate_key_material
    from dps.layers.connectors.base import ColumnInfo, ObjectRef, TableShape

    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        values = json.loads(request.content)["values"]
        calls.append(len(values))
        return httpx.Response(200, json={"tokens": ["tk_" + v[::-1] for v in values]})

    settings = _settings()
    engine = PrivacyEngine(
        settings, tokenizer=HttpTokenizer("http://svc", transport=httpx.MockTransport(handler))
    )
    shape = TableShape(
        object=ObjectRef(owner="bank", name="people"),
        key=["ID"],
        key_source="primary_key",
        columns=[
            ColumnInfo(path="$.ID", name="ID", data_type="int", kind="int", is_key=True),
            ColumnInfo(path="$.SSN", name="SSN", data_type="text", kind="text"),
        ],
    )
    plans = [
        ColumnPlan(
            path="$.SSN",
            name="SSN",
            entity_type="SSN",
            method=MethodSpec(method="external_tokenize", params={"profile": "ssn"}),
            text_column=False,
            reversible=True,
            kind="text",
            length=None,
        )
    ]
    t = BatchTransformer(
        engine,
        policy=resolve_policy(POLICY, default_name="gdpr-pseudonymise"),
        mode="obfuscate",
        columns=plans,
        shape=shape,
        key=generate_key_material("key"),
        seed=None,
        locale="en_US",
    )
    rows: list[dict[str, Any]] = [{"ID": i, "SSN": f"123-45-{6700 + i}"} for i in range(10)]
    rows.append({"ID": 99, "SSN": None})
    result = t.transform(rows)
    assert calls == [10] and all(r["SSN"].startswith("tk_") for r in result.rows[:10])
    assert result.rows[10]["SSN"] is None and result.values_by_method["external_tokenize"] == 10


def test_compose_runs_the_mock_under_its_profiles() -> None:
    services = yaml.safe_load((ROOT / "docker-compose.yml").read_text("utf-8"))["services"]
    mock = services["tokenizer-mock"]
    assert sorted(mock["profiles"]) == ["full", "tokenizer"]
    assert mock["image"] == "data-privacy-services:${DPS_IMAGE_TAG:-local}"
    assert mock["ports"] == ["127.0.0.1:${DPS_TOKENIZER_MOCK_PORT:-8420}:8420"]
    assert "http://localhost:8420/health" in " ".join(mock["healthcheck"]["test"])
    for name in ("app", "worker"):
        env = services[name]["environment"]
        assert env["DPS_TOKENIZER_URL"] == "${DPS_TOKENIZER_URL:-http://tokenizer-mock:8420}"
        assert env["DPS_TOKENIZER_PROVIDER"] == "${DPS_TOKENIZER_PROVIDER:-http}"


def test_a_document_path_is_tokenized_leaf_by_leaf_in_one_call() -> None:
    """WP-I (m3 merge): M5's batch pre-tokenization read ``row[name]``; a MongoDB plan names a
    leaf path (``$.ids[*].ssn``) whose name is the whole top-level field, so the tokenizer got the
    array's JSON as one "SSN". Every leaf the path matches is sent instead, in one call."""
    from dps.engine.batch import BatchTransformer, ColumnPlan
    from dps.engine.policy import MethodSpec, resolve_policy
    from dps.engine.transform.crypto import generate_key_material
    from dps.layers.connectors.base import ColumnInfo, ObjectRef, TableShape

    sent: list[list[str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        values = json.loads(request.content)["values"]
        sent.append(values)
        return httpx.Response(200, json={"tokens": ["tk_" + v[::-1] for v in values]})

    engine = PrivacyEngine(
        _settings(), tokenizer=HttpTokenizer("http://svc", transport=httpx.MockTransport(handler))
    )
    shape = TableShape(
        object=ObjectRef(name="people"),
        key=["_id"],
        key_source="primary_key",
        columns=[
            ColumnInfo(path="$._id", name="_id", data_type="int", kind="int", is_key=True),
            ColumnInfo(path="$.ids[*].ssn", name="ids", data_type="string", kind="text"),
        ],
    )
    plans = [
        ColumnPlan(
            path="$.ids[*].ssn",
            name="ids",
            entity_type="SSN",
            method=MethodSpec(method="external_tokenize", params={"profile": "ssn"}),
            text_column=False,
            reversible=True,
            kind="text",
            length=None,
        )
    ]
    t = BatchTransformer(
        engine,
        policy=resolve_policy(POLICY, default_name="gdpr-pseudonymise"),
        mode="obfuscate",
        columns=plans,
        shape=shape,
        key=generate_key_material("key"),
        seed=None,
        locale="en_US",
        unclassified="keep",
    )
    rows: list[dict[str, Any]] = [
        {"_id": i, "ids": [{"ssn": f"123-45-{6700 + i}"}, {"ssn": f"987-65-{4300 + i}"}]}
        for i in range(5)
    ]
    result = t.transform(rows)
    assert len(sent) == 1 and sorted(sent[0]) == sorted(
        leaf["ssn"] for r in rows for leaf in r["ids"]
    )
    for old, new in zip(rows, result.rows, strict=True):
        for a, b in zip(old["ids"], new["ids"], strict=True):
            assert b["ssn"].startswith("tk_") and b["ssn"] != a["ssn"]
    assert result.verification.passed
