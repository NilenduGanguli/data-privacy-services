from __future__ import annotations

import json
import random
import re
from typing import Any

import pytest
from fastapi.testclient import TestClient

from dps.app import create_app
from dps.audit import MemoryAuditSink
from dps.config import Settings
from dps.layers.api.routers import schema as schema_router

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


FIXED_SEED = {"alg": "dps.synth.v1", "seed": "AQIDBAUGBwgJCgsMDQ4PEBESExQVFhcYGRobHB0eHyA"}


def test_the_returned_spec_replays_the_rows(client: TestClient) -> None:
    body = {"sample": SAMPLE, "rows": 15, "seed": FIXED_SEED}
    first = client.post("/api/v1/schema/synthesize", headers=FULL, json=body).json()
    assert first["report"]["sample_redraws"] == 0  # this seed draws no sample value
    replay = client.post(
        "/api/v1/schema/synthesize",
        headers=FULL,
        json={"spec": first["spec"], "seed": first["synthesis_seed"], "rows": 15},
    ).json()
    assert replay["rows"] == first["rows"] and replay["spec_hash"] == first["spec_hash"]
    assert replay["report"]["verification"] is None  # nothing to sweep against without a sample
    again = client.post("/api/v1/schema/synthesize", headers=FULL, json=body).json()
    assert again["rows"] == first["rows"]  # the sample, the spec and the seed fix the rows


def test_generated_values_the_sample_holds_are_drawn_again(client: TestClient) -> None:
    """A generated value that is also in the sample is drawn again, so the run succeeds without
    it (before S8 the same seed regenerated it and the sweep refused the run)."""
    sample = [{"full_name": r["full_name"], "email": r["email"]} for r in SAMPLE]
    first = client.post(
        "/api/v1/schema/synthesize", headers=FULL, json={"sample": sample, "rows": 5}
    ).json()
    produced = first["rows"][0]
    # the same spec (names and e-mails are entity fields whatever the values) with a sample that
    # now holds a generated row: the same seed would regenerate it, so it must be drawn again
    leaky = [*sample, {"full_name": produced["full_name"], "email": produced["email"]}]
    r = client.post(
        "/api/v1/schema/synthesize",
        headers=FULL,
        json={"sample": leaky, "rows": 5, "seed": first["synthesis_seed"]},
    )
    body = r.json()
    assert r.status_code == 200, r.text
    assert body["report"]["sample_redraws"] >= 1 and body["report"]["verification"]["passed"]
    assert body["rows"][1:] == first["rows"][1:] and body["rows"][0] != produced
    assert produced["full_name"] not in r.text and produced["email"] not in r.text


# -- S8: a realistic sample at volume, and real leaks still refused ------------------------------
LIVE = [
    {
        "cust_id": 1001 + i,
        "full_name": name,
        "email": email,
        "city": city,
        "segment": segment,
        "balance": balance,
        "opened": opened,
    }
    for i, (name, email, city, segment, balance, opened) in enumerate(
        [
            ("Arjun Mehta", "arjun.mehta@example.in", "Pune", "retail", 48250.50, "2019-04-11"),
            ("Priya Nair", "priya.nair@example.in", "Kochi", "premier", 812300.00, "2016-09-02"),
            ("Kavya Raghunathan", "kavya.r@example.in", "Chennai", "retail", 1520.75, "2022-01-19"),
            ("Rohan Kulkarni", "rohan.k@example.in", "Mumbai", "business", 2390000.0, "2014-06-30"),
        ]
    )
]


def test_a_realistic_sample_is_not_refused_by_accident(client: TestClient) -> None:
    """The live run's seven-column sample: 20 calls of 100 rows were refused 17-19 times (422
    leakage) before S8. Every call must now answer 200, its rows swept and passed."""
    statuses, redraws = [], 0
    for _ in range(20):
        r = client.post(
            "/api/v1/schema/synthesize",
            headers=FULL,
            json={"sample": LIVE, "rows": 100, "locale": "en_IN"},
        )
        statuses.append(r.status_code)
        if r.status_code == 200:
            report = r.json()["report"]
            assert report["verification"]["passed"] and report["rows"] == 100
            redraws += report["sample_redraws"]
    assert statuses == [200] * 20 and redraws > 0


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("email", "priya.nair@example.in"),  # a sampled e-mail address
        ("full_name", "Kavya Raghunathan"),  # a sampled name
        ("segment", "ARJUN MEHTA"),  # a sampled name, re-cased, in another field
        ("cust_id", 1003),  # a sampled ID
        ("balance", 1004.0),  # a sampled ID as an amount
        ("email", "c1002@example.org"),  # a sampled ID inside an e-mail address
        ("opened", "2016-09-02"),  # a sampled date
        ("segment", "1001, 1002"),  # two sampled IDs (review r1, B1: 200 with S8's first rule)
        ("segment", "1003 1004"),  # the same, without the comma
        ("opened", "2016-09-02 10:15:00"),  # a sampled date in a space-separated datetime
        ("balance", "1001.00"),  # a sampled ID as a numeric string
    ],
)
def test_a_sample_value_forced_into_the_output_is_refused(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, field: str, value: Any
) -> None:
    """A real leak — a sampled value written into the rows after the executor ran, where no redraw
    can reach it — still fails closed (422 leakage) and the answer does not echo it."""
    real = schema_router.execute_spec

    def leaky(*args: Any, **kwargs: Any) -> Any:
        result = real(*args, **kwargs)
        result.rows[7][field] = value
        return result

    monkeypatch.setattr(schema_router, "execute_spec", leaky)
    r = client.post(
        "/api/v1/schema/synthesize",
        headers=FULL,
        json={"sample": LIVE, "rows": 20, "locale": "en_IN"},
    )
    assert r.status_code == 422 and r.json()["code"] == "leakage"
    assert str(value) not in r.text and "Raghunathan" not in r.text


def test_a_spec_literal_holding_a_sample_value_is_refused(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A proposal that writes a sampled value itself (a ``constant``, which is never drawn again)
    is refused by the sweep, not steered around."""
    real = schema_router.propose_spec

    async def literal(*args: Any, **kwargs: Any) -> Any:
        spec, info = await real(*args, **kwargs)
        fields = [
            f.model_copy(
                update={"generator": "constant", "params": {"value": "rohan.k@example.in"}}
            )
            if f.name == "segment"
            else f
            for f in spec.fields
        ]
        return spec.model_copy(update={"fields": fields}), info

    monkeypatch.setattr(schema_router, "propose_spec", literal)
    r = client.post("/api/v1/schema/synthesize", headers=FULL, json={"sample": LIVE, "rows": 10})
    assert r.status_code == 422 and r.json()["code"] == "leakage"
    assert "rohan" not in r.text


def test_a_sampled_mobile_with_a_country_code_is_refused(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review r1 (B1): a sampled mobile written the way DPS's own PHONE generator writes one
    (``+91 9810010012``) answered 200 with the number in the body. An all-digit original of nine
    or more digits is found anywhere in the digits again (M0's rule)."""
    mobiles = ["9810010012", "9820020023", "9830030034", "9840040045"]
    sample = [dict(row, mobile=m) for row, m in zip(LIVE, mobiles, strict=True)]
    real = schema_router.execute_spec

    def leaky(*args: Any, **kwargs: Any) -> Any:
        result = real(*args, **kwargs)
        result.rows[3]["mobile"] = "+91-98200-20023"
        result.rows[5]["segment"] = "+91 9810010012"
        return result

    ok = client.post("/api/v1/schema/synthesize", headers=FULL, json={"sample": sample, "rows": 50})
    assert ok.status_code == 200, ok.text
    monkeypatch.setattr(schema_router, "execute_spec", leaky)
    r = client.post("/api/v1/schema/synthesize", headers=FULL, json={"sample": sample, "rows": 20})
    assert r.status_code == 422 and r.json()["code"] == "leakage"
    assert not [m for m in mobiles if m[-5:] in r.text]


def test_an_id_like_key_does_not_repeat_the_sample(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review r1 (N3): a generic ``id`` key is not swept, so a ``sequence`` proposed near the
    sample wrote the sampled IDs into every run, and nothing refused them. With a sample, an
    ID-like key avoids its sampled values (no 422: steered, not swept)."""
    sample = [dict(row, id=7001 + i) for i, row in enumerate(LIVE)]
    real = schema_router.propose_spec

    async def near(*args: Any, **kwargs: Any) -> Any:
        spec, info = await real(*args, **kwargs)
        fields = [
            f.model_copy(
                update={"generator": "sequence", "params": {"start": 7000}, "unique": True}
            )
            if f.name == "id"
            else f
            for f in spec.fields
        ]
        return spec.model_copy(update={"fields": fields}), info

    monkeypatch.setattr(schema_router, "propose_spec", near)
    r = client.post("/api/v1/schema/synthesize", headers=FULL, json={"sample": sample, "rows": 10})
    body = r.json()
    assert r.status_code == 200, r.text
    assert [row["id"] for row in body["rows"]][:3] == [7000, 7005, 7006]
    assert body["report"]["sample_redraws"] >= 4


def test_code_and_list_columns_do_not_repeat_a_sampled_id(client: TestClient) -> None:
    """Review r1 re-check (B1): ``_``, ``;``, quotes and brackets joined digits the way ``-`` does
    in a date, so a generated ``TXN_9999_9999`` code or ``["9999","9999"]`` list holding a sampled
    customer ID as a token answered 200 (176-208 values per column at 1 000 rows). A token is a
    copy: such a value is drawn again, so none is left. Samples are fictitious."""
    rng = random.Random(14)
    ids = rng.sample(range(1000, 10000), 300)
    sample = [
        {
            "cust_id": cust_id,
            "txn_ref": f"TXN_{rng.randint(1000, 9999)}_{rng.randint(1000, 9999)}",
            "related": json.dumps([str(i) for i in rng.sample(ids, 2)], separators=(",", ":")),
        }
        for cust_id in ids
    ]
    r = client.post(
        "/api/v1/schema/synthesize",
        headers=FULL,
        json={"sample": sample, "rows": 500, "locale": "en_IN"},
    )
    body = r.json()
    assert r.status_code == 200, r.text
    generators = {f["name"]: f["generator"] for f in body["spec"]["fields"]}
    assert generators["txn_ref"] == generators["related"] == "pattern"
    assert body["report"]["verification"]["passed"] and body["report"]["sample_redraws"] > 0
    tokens = [
        token
        for row in body["rows"]
        for column in ("txn_ref", "related")
        for token in re.findall(r"[0-9]+", str(row[column]))
    ]
    assert len(tokens) > 1000
    assert len(set(tokens) & {str(i) for i in ids}) == 0  # a count: no value in a message


def test_a_dense_id_next_to_a_swept_id_is_not_refused(client: TestClient) -> None:
    """Review r1 re-check (B2): an ``id`` whose sampled values fill its range (1000..1099, a later
    page of a table) gave up steering altogether once it could not avoid them, wrote the sampled
    customer IDs that lie inside its range, and every call was refused (422 ``leakage``;
    ``a46d1c5`` answered 200). It now stops avoiding its own values only."""
    rng = random.Random(16)
    cust = rng.sample(range(1000, 1100), 20) + rng.sample(range(2000, 10000), 80)
    rng.shuffle(cust)
    sample = [{"id": 1000 + i, "cust_id": c} for i, c in enumerate(cust)]
    for _ in range(3):
        r = client.post(
            "/api/v1/schema/synthesize", headers=FULL, json={"sample": sample, "rows": 300}
        )
        body = r.json()
        assert r.status_code == 200, r.text
        assert body["report"]["verification"]["passed"]
        assert len({row["id"] for row in body["rows"]} & set(cust)) == 0
