"""NDJSON streaming of large arrays on the Layer 1 routes (M1 Task 19A, spec §6, contract §9)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from dps.app import create_app
from dps.audit import MemoryAuditSink
from dps.config import Settings
from dps.engine.service import PrivacyEngine, VerificationFailed

NDJSON = "application/x-ndjson"
POLICY = "gdpr-pseudonymise"
ITEMS = [{"email": "a.k@example-mail.in"}, {"email": "b.m@example-mail.in"}]


def _settings(**kw: Any) -> Settings:
    base: dict[str, Any] = dict(
        _env_file=None,
        env="local",
        auth_enabled=False,
        llm_provider="stub",
        fpe_enabled=False,
        ner_enabled=False,
        rate_limit_enabled=False,
    )
    return Settings(**{**base, **kw})


@pytest.fixture
def client() -> Iterator[tuple[TestClient, MemoryAuditSink]]:
    sink = MemoryAuditSink()
    with TestClient(create_app(_settings(), audit_sink=sink)) as c:
        yield c, sink


def lines(response: Any) -> list[dict[str, Any]]:
    assert response.headers["content-type"].startswith(NDJSON), response.text
    return [json.loads(line) for line in response.text.splitlines() if line.strip()]


def ndjson_body(envelope: dict[str, Any], items: list[Any]) -> bytes:
    rows = [json.dumps(envelope), *(json.dumps(item) for item in items)]
    return ("\n".join(rows) + "\n").encode()


def post_ndjson(c: TestClient, route: str, body: bytes, **headers: str) -> Any:
    return c.post(f"/api/v1/{route}", content=body, headers={"content-type": NDJSON, **headers})


def test_json_array_streams_when_ndjson_is_accepted(client) -> None:  # noqa: ANN001
    c, _ = client
    r = c.post(
        "/api/v1/obfuscate", json={"data": ITEMS, "policy": POLICY}, headers={"accept": NDJSON}
    )
    assert r.status_code == 200
    out = lines(r)
    assert [line["type"] for line in out] == ["header", "item", "item", "report"]
    header, items, report = out[0], out[1:3], out[3]
    assert header["mode"] == "obfuscate" and header["key_material"]["dek"]
    assert header["policy"]["name"] == POLICY and header["policy"]["hash"]
    assert [item["index"] for item in items] == [0, 1] and report["items"] == 2
    for item, original in zip(items, ITEMS, strict=True):
        assert item["result"]["email"] != original["email"]
        assert item["findings"][0]["locator"]["path"] == "$.email" and item["reveal_hints"]
        single = c.post(
            "/api/v1/obfuscate",
            json={
                "data": original,
                "policy": POLICY,
                "options": {"consistency": {"key_material": header["key_material"]}},
            },
        ).json()
        assert single["result"] == item["result"]
    assert report["report"]["findings_total"] == 2
    assert report["report"]["verification"]["passed"] is True
    assert "a.k@example-mail.in" not in r.text and "b.m@example-mail.in" not in r.text


def test_ndjson_request_body_streams_items(client) -> None:  # noqa: ANN001
    c, _ = client
    first = lines(
        c.post(
            "/api/v1/obfuscate", json={"data": ITEMS, "policy": POLICY}, headers={"accept": NDJSON}
        )
    )
    km = first[0]["key_material"]
    envelope = {"policy": POLICY, "options": {"consistency": {"key_material": km}}}
    r = post_ndjson(c, "obfuscate", ndjson_body(envelope, ITEMS))
    assert r.status_code == 200
    out = lines(r)
    assert [line["type"] for line in out] == ["header", "item", "item", "report"]
    assert out[0]["key_material"]["kid"] == km["kid"]
    assert out[1:3] == first[1:3]


def test_one_key_and_one_seed_per_stream(client) -> None:  # noqa: ANN001
    c, _ = client
    values = [f"user{n % 7}@example-mail.in" for n in range(1200)]
    items = [{"email": v} for v in values]
    obf = lines(post_ndjson(c, "obfuscate", ndjson_body({"policy": POLICY}, items)))
    assert len(obf) == 1202 and obf[-1]["type"] == "report" and obf[-1]["items"] == 1200
    tokens: dict[str, set[str]] = {}
    for line in obf[1:-1]:
        tokens.setdefault(values[line["index"]], set()).add(line["result"]["email"])
    assert len(tokens) == 7 and all(len(t) == 1 for t in tokens.values())
    syn = lines(
        post_ndjson(
            c,
            "synthesize",
            ndjson_body({"policy": POLICY, "options": {"consistency": {"scope": "key"}}}, items),
        )
    )
    assert syn[0]["synthesis_seed"] and syn[-1]["items"] == 1200
    surrogates: dict[str, set[str]] = {}
    for line in syn[1:-1]:
        surrogates.setdefault(values[line["index"]], set()).add(line["result"]["email"])
    assert len(surrogates) == 7 and all(len(s) == 1 for s in surrogates.values())
    assert all(v not in {next(iter(s)) for s in surrogates.values()} for v in values)


def test_reveal_stream_round_trips(client) -> None:  # noqa: ANN001
    c, _ = client
    obf = lines(post_ndjson(c, "obfuscate", ndjson_body({"policy": POLICY}, ITEMS)))
    body = ndjson_body({"key_material": obf[0]["key_material"]}, obf[1:-1])
    out = lines(post_ndjson(c, "reveal", body))
    assert [line["type"] for line in out] == ["header", "item", "item", "report"]
    assert [line["result"] for line in out[1:3]] == ITEMS
    assert out[-1]["report"]["revealed"] == 2
    # A JSON reveal of a JSON obfuscation (whole-document hints) streams its items too.
    whole = c.post("/api/v1/obfuscate", json={"data": ITEMS, "policy": POLICY}).json()
    streamed = lines(
        c.post(
            "/api/v1/reveal",
            json={
                "data": whole["result"],
                "key_material": whole["key_material"],
                "reveal_hints": whole["reveal_hints"],
            },
            headers={"accept": NDJSON},
        )
    )
    assert [line["result"] for line in streamed[1:-1]] == ITEMS


def test_without_accept_json_is_unchanged(client) -> None:  # noqa: ANN001
    c, _ = client
    r = c.post("/api/v1/obfuscate", json={"data": ITEMS, "policy": POLICY})
    assert r.status_code == 200 and r.headers["content-type"] == "application/json"
    body = r.json()
    assert isinstance(body["result"], list) and len(body["result"]) == 2
    assert {"result", "findings", "key_material", "reveal_hints", "report", "trace_id"} <= set(body)
    # An object (not an array) is never streamed, even when NDJSON is accepted.
    single = c.post(
        "/api/v1/detect", json={"data": ITEMS[0], "policy": POLICY}, headers={"accept": NDJSON}
    )
    assert single.headers["content-type"] == "application/json" and single.json()["findings"]


def test_fail_closed_ends_the_stream(client, monkeypatch) -> None:  # noqa: ANN001
    c, _ = client
    real = PrivacyEngine._verify
    calls = {"n": 0}

    def flaky(self, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN001
        calls["n"] += 1
        if calls["n"] == 2:
            raise VerificationFailed("output failed the leakage sweep; nothing was returned")
        return real(self, *args, **kwargs)

    monkeypatch.setattr(PrivacyEngine, "_verify", flaky)
    items = [*ITEMS, {"email": "c.z@example-mail.in"}]
    r = post_ndjson(c, "obfuscate", ndjson_body({"policy": POLICY}, items))
    out = lines(r)
    assert [line["type"] for line in out] == ["header", "item", "error"]
    assert out[1]["index"] == 0
    assert out[2]["index"] == 1 and out[2]["code"] == "leakage" and out[2]["detail"]
    assert calls["n"] == 2  # item 2 was never processed
    for item in items:
        assert item["email"] not in r.text


def test_limits_and_malformed_lines() -> None:
    with TestClient(create_app(_settings(ndjson_max_items=2))) as c:
        too_many = post_ndjson(c, "detect", ndjson_body({"policy": POLICY}, [*ITEMS, ITEMS[0]]))
        assert too_many.status_code == 413 and too_many.json()["code"] == "ndjson_too_large"
        array = c.post(
            "/api/v1/detect", json={"data": [*ITEMS, ITEMS[0]]}, headers={"accept": NDJSON}
        )
        assert array.status_code == 413 and array.json()["code"] == "ndjson_too_large"
    with TestClient(create_app(_settings())) as c:
        secret = '{"email": "x.y@example-mail.in"'
        broken = post_ndjson(c, "obfuscate", f'{{"policy": "{POLICY}"}}\n{secret}\n'.encode())
        assert broken.status_code == 400 and broken.json()["code"] == "ndjson_invalid"
        assert "line 2" in broken.json()["detail"] and "x.y@" not in broken.text
        bad_envelope = post_ndjson(c, "obfuscate", b'["not", "an", "envelope"]\n{"a": 1}\n')
        assert bad_envelope.status_code == 400 and "line 1" in bad_envelope.json()["detail"]
        empty = post_ndjson(c, "detect", b"")
        assert empty.status_code == 400 and empty.json()["code"] == "ndjson_invalid"
        null_item = post_ndjson(c, "detect", ndjson_body({"policy": POLICY}, [None]))
        assert null_item.status_code == 400 and "line 2" in null_item.json()["detail"]
        no_key = post_ndjson(c, "reveal", ndjson_body({}, [{"result": {"a": "b"}}]))
        assert no_key.status_code == 400 and no_key.json()["code"] == "ndjson_invalid"
        bad_policy = post_ndjson(c, "detect", ndjson_body({"policy": "nope"}, ITEMS))
        assert bad_policy.status_code == 400 and bad_policy.json()["code"] == "bad_request"


def test_one_audit_event_per_stream(client) -> None:  # noqa: ANN001
    c, sink = client
    before = len(sink.events)
    items = [*ITEMS, {"email": "c.z@example-mail.in", "note": "mail d.q@example-mail.in"}]
    out = lines(post_ndjson(c, "obfuscate", ndjson_body({"policy": POLICY}, items)))
    assert len(sink.events) == before + 1
    event = sink.events[-1]
    assert event.mode == "obfuscate" and event.outcome == "ok" and event.findings_total == 4
    assert event.findings_by_type == {"EMAIL": 4} and event.trace_id == out[0]["trace_id"]
    assert event.verification_passed is True and event.reversible_values == 4
    assert "example-mail" not in json.dumps(event.model_dump(mode="json"))
    refused = post_ndjson(c, "obfuscate", ndjson_body({"policy": "nope"}, items))
    assert refused.status_code == 400 and len(sink.events) == before + 2
    assert sink.events[-1].outcome == "bad_request" and sink.events[-1].policy == "unknown"


def test_detect_items_carry_findings_and_sensitivity_only(client) -> None:  # noqa: ANN001
    c, _ = client
    out = lines(
        c.post("/api/v1/detect", json={"data": ITEMS, "policy": POLICY}, headers={"accept": NDJSON})
    )
    assert [line["type"] for line in out] == ["header", "item", "item", "report"]
    assert "key_material" not in out[0] and "synthesis_seed" not in out[0]
    for item in out[1:3]:
        assert set(item) == {"type", "index", "findings", "record_sensitivity"}
        assert item["findings"][0]["entity_type"] == "EMAIL"
    assert out[-1]["report"]["verification"] is None and out[-1]["report"]["findings_total"] == 2


def test_ndjson_body_limit_is_separate_from_the_json_limit() -> None:
    with TestClient(create_app(_settings(max_payload_mb=1, max_ndjson_mb=4))) as c:
        items = [{"note": "x" * 1000} for _ in range(1500)]  # ~1.5 MB
        body = ndjson_body({"policy": POLICY}, items)
        assert len(body) > 1024 * 1024
        assert post_ndjson(c, "detect", body).status_code == 200
        as_json = json.dumps({"data": items}).encode()
        r = c.post(
            "/api/v1/detect",
            content=as_json,
            headers={"content-type": "application/json", "accept": NDJSON},
        )
        assert r.status_code == 413 and r.json()["code"] == "payload_too_large"
    with TestClient(create_app(_settings(max_payload_mb=1, max_ndjson_mb=1))) as c:
        r = post_ndjson(c, "detect", body)
        assert r.status_code == 413 and r.json()["code"] == "payload_too_large"


def test_ndjson_routes_keep_scopes_and_openapi_documents_them() -> None:
    keys = [{"name": "ro", "key": "dps_ro", "tenant_ids": ["*"], "scopes": ["detect"]}]
    settings = _settings(auth_enabled=True, api_keys=json.dumps(keys))
    with TestClient(create_app(settings)) as c:
        body = ndjson_body({"policy": POLICY}, ITEMS)
        assert post_ndjson(c, "obfuscate", body).status_code == 401
        assert post_ndjson(c, "obfuscate", body, **{"X-API-KEY": "dps_ro"}).status_code == 403
        assert post_ndjson(c, "detect", body, **{"X-API-KEY": "dps_ro"}).status_code == 200
        spec = c.get("/api/v1/openapi.json").json()
    for route in ("detect", "obfuscate", "synthesize", "reveal"):
        operation = spec["paths"][f"/api/v1/{route}"]["post"]
        assert {"application/json", NDJSON} <= set(operation["requestBody"]["content"])
        assert NDJSON in operation["responses"]["200"]["content"]


def test_accept_header_parsing() -> None:
    from starlette.requests import Request

    from dps.layers.api.ndjson import is_ndjson_request, wants_ndjson

    def request(**headers: str) -> Request:
        raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
        return Request({"type": "http", "headers": raw})

    assert wants_ndjson(request(accept="application/json, application/x-ndjson;q=0.5"))
    assert not wants_ndjson(request(accept="application/x-ndjson;q=0"))
    assert not wants_ndjson(request(accept="application/json"))
    assert not wants_ndjson(request())
    assert is_ndjson_request(request(**{"content-type": "Application/X-NDJSON; charset=utf-8"}))
    assert not is_ndjson_request(request(**{"content-type": "application/json"}))
