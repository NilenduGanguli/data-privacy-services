from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from dps.app import create_app
from dps.audit import MemoryAuditSink
from dps.config import Settings

KEYS = [
    {"name": "full", "key": "dps_full", "tenant_ids": ["*"], "scopes": ["*"]},
    {"name": "ro", "key": "dps_ro", "tenant_ids": ["retail"], "scopes": ["detect"]},
]
FULL, RO = {"X-API-KEY": "dps_full"}, {"X-API-KEY": "dps_ro"}
DDL = (
    "CREATE TABLE customers (customer_id INTEGER PRIMARY KEY, full_name VARCHAR(80), email VARCHAR(120), "
    "balance NUMERIC(12,2), opened DATE)"
)
SPEC: dict[str, Any] = {
    "name": "cards",
    "fields": [
        {
            "name": "id",
            "generator": "sequence",
            "params": {"prefix": "C-", "width": 4},
            "unique": True,
        },
        {"name": "holder", "generator": "entity", "params": {"entity_type": "PERSON_NAME"}},
        {"name": "pan", "generator": "entity", "params": {"entity_type": "CARD_PAN"}},
        {"name": "limit", "generator": "choice", "params": {"values": [500, 1000, 5000]}},
    ],
}
SAMPLE = [
    {"full_name": n, "email": e, "status": s, "balance": b}
    for n, e, s, b in [
        ("Ananya Krishnan", "ananya.k@example-mail.in", "ACTIVE", 1200.5),
        ("Rohan Desai", "rohan.d@example-mail.in", "CLOSED", 85000.0),
        ("Priya Nair", "priya.n@example-mail.in", "ACTIVE", 15.75),
        ("Rajesh Iyer", "rajesh.i@example-mail.in", "CLOSED", 430.0),
    ]
]


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
        schema_max_rows=500,
    )
    return TestClient(create_app(settings, audit_sink=MemoryAuditSink()))


def test_generator_spec_from_ddl(client: TestClient) -> None:
    r = client.post("/api/v1/schema/generator-spec", headers=FULL, json={"ddl": DDL})
    body = r.json()
    assert r.status_code == 200, r.text
    assert [f["name"] for f in body["spec"]["fields"]] == [
        "customer_id",
        "full_name",
        "email",
        "balance",
        "opened",
    ]
    assert body["spec"]["fields"][0]["generator"] == "sequence" and body["source"] == "heuristic"
    assert body["llm"] == {"calls": 0, "raw_pii_sent": 0} and len(body["spec_hash"]) == 64
    assert body["profile"]["source"] == "ddl"


def test_exactly_one_input(client: TestClient) -> None:
    both = client.post(
        "/api/v1/schema/generator-spec",
        headers=FULL,
        json={"ddl": DDL, "schema": {"type": "object", "properties": {"a": {"type": "string"}}}},
    )
    assert both.status_code == 422 and both.json()["code"] == "exactly_one_input"
    none = client.post("/api/v1/schema/generator-spec", headers=FULL, json={})
    assert none.status_code == 422 and none.json()["code"] == "exactly_one_input"
    mixed = client.post("/api/v1/schema/synthesize", headers=FULL, json={"spec": SPEC, "ddl": DDL})
    assert mixed.status_code == 422 and mixed.json()["code"] == "exactly_one_input"


def test_synthesize_from_spec_is_reproducible(client: TestClient) -> None:
    first = client.post(
        "/api/v1/schema/synthesize", headers=FULL, json={"spec": SPEC, "rows": 20}
    ).json()
    again = client.post(
        "/api/v1/schema/synthesize",
        headers=FULL,
        json={"spec": SPEC, "rows": 20, "seed": first["synthesis_seed"]},
    ).json()
    assert (
        len(first["rows"]) == 20
        and first["rows"] == again["rows"]
        and first["spec_hash"] == again["spec_hash"]
    )
    assert first["report"]["source"] == "caller" and first["rows"][0]["id"] == "C-0001"


def test_synthesize_from_sample_returns_spec_and_csv(client: TestClient) -> None:
    r = client.post(
        "/api/v1/schema/synthesize",
        headers=FULL,
        json={"sample": SAMPLE, "rows": 30, "format": "csv"},
    )
    body = r.json()
    assert r.status_code == 200, r.text
    assert (
        body["rows"] is None
        and body["csv"].count("\n") == 31
        and body["spec"]["source"] == "heuristic"
    )
    assert body["report"]["verification"]["passed"] is True
    assert all(row["email"] not in body["csv"] for row in SAMPLE)


def test_limits_scopes_and_codes(client: TestClient) -> None:
    many = client.post("/api/v1/schema/synthesize", headers=FULL, json={"spec": SPEC, "rows": 501})
    assert many.status_code == 413 and many.json()["code"] == "too_many_rows"
    assert (
        client.post("/api/v1/schema/synthesize", headers=RO, json={"spec": SPEC}).status_code == 403
    )
    bad = client.post(
        "/api/v1/schema/synthesize",
        headers=FULL,
        json={"spec": {"fields": [{"name": "a", "generator": "teleport"}]}},
    )
    assert bad.status_code == 422
    ddl = client.post("/api/v1/schema/generator-spec", headers=FULL, json={"ddl": "DROP TABLE x"})
    assert ddl.status_code == 422 and ddl.json()["code"] == "ddl_unparsed"
    seed = client.post(
        "/api/v1/schema/synthesize",
        headers=FULL,
        json={"spec": SPEC, "seed": {"alg": "dps.synth.v1", "seed": "c2hvcnQ"}},
    )
    assert (
        seed.status_code == 422
        and seed.json()["code"] == "invalid_seed"
        and "c2hvcnQ" not in seed.text
    )


@pytest.fixture
def audited() -> tuple[TestClient, MemoryAuditSink]:
    sink = MemoryAuditSink()
    settings = Settings(
        _env_file=None,
        env="local",
        auth_enabled=True,
        api_keys=json.dumps(KEYS),
        llm_provider="stub",
        ner_enabled=False,
        rate_limit_enabled=False,
    )
    return TestClient(create_app(settings, audit_sink=sink)), sink


def test_schema_routes_are_audited(audited: tuple[TestClient, MemoryAuditSink]) -> None:
    client, sink = audited
    assert client.post("/api/v1/schema/generator-spec", headers=FULL, json={"ddl": DDL}).is_success
    sample = {"sample": SAMPLE, "rows": 10}
    assert client.post("/api/v1/schema/synthesize", headers=FULL, json=sample).is_success
    spec_event, synth_event = list(sink.events)[-2:]
    assert (spec_event.mode, spec_event.outcome, spec_event.policy) == (
        "schema.generator-spec",
        "ok",
        "-",
    )
    assert (synth_event.mode, synth_event.outcome) == ("schema.synthesize", "ok")
    assert (
        synth_event.verification_passed is True and synth_event.route == "/api/v1/schema/synthesize"
    )


def test_oversized_inputs_are_coded_413(client: TestClient) -> None:
    rows = client.post(
        "/api/v1/schema/generator-spec", headers=FULL, json={"sample": [{"a": 1}] * 1001}
    )
    assert rows.status_code == 413 and rows.json()["code"] == "sample_too_large"
    ddl = "CREATE TABLE t (a INT) " + "-" * 100_000
    big = client.post("/api/v1/schema/generator-spec", headers=FULL, json={"ddl": ddl})
    assert big.status_code == 413 and big.json()["code"] == "ddl_too_large"


def test_an_empty_seed_is_invalid_not_ignored(client: TestClient) -> None:
    r = client.post("/api/v1/schema/synthesize", headers=FULL, json={"spec": SPEC, "seed": {}})
    assert r.status_code == 422 and r.json()["code"] == "invalid_seed"


def test_the_returned_spec_replays_the_rows(client: TestClient) -> None:
    first = client.post(
        "/api/v1/schema/synthesize", headers=FULL, json={"sample": SAMPLE, "rows": 15}
    ).json()
    replay = client.post(
        "/api/v1/schema/synthesize",
        headers=FULL,
        json={"spec": first["spec"], "seed": first["synthesis_seed"], "rows": 15},
    ).json()
    assert replay["rows"] == first["rows"] and replay["spec_hash"] == first["spec_hash"]
    assert replay["report"]["verification"] is None  # nothing to sweep against without a sample


def test_generated_rows_holding_a_sample_value_fail_closed(client: TestClient) -> None:
    sample = [{"full_name": r["full_name"], "email": r["email"]} for r in SAMPLE]
    first = client.post(
        "/api/v1/schema/synthesize", headers=FULL, json={"sample": sample, "rows": 5}
    ).json()
    produced = first["rows"][0]
    # the same spec (names and e-mails are entity fields whatever the values) with a sample that
    # now holds a generated row: the same seed regenerates it, so the sweep must find it
    leaky = [*sample, {"full_name": produced["full_name"], "email": produced["email"]}]
    r = client.post(
        "/api/v1/schema/synthesize",
        headers=FULL,
        json={"sample": leaky, "rows": 5, "seed": first["synthesis_seed"]},
    )
    assert r.status_code == 422 and r.json()["code"] == "leakage"
    assert produced["full_name"] not in r.text and produced["email"] not in r.text
