"""``Idempotency-Key`` on the Layer 1 routes (M1 Task 19B; contract §9, decision D2).

The replay cache holds fingerprints only — scope hash, body hash, original trace id, timestamp —
never key material, a seed, an output or a value; a replay re-runs the engine.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Iterator
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from structlog.testing import capture_logs

from dps.app import create_app
from dps.audit import MemoryAuditSink
from dps.config import Settings
from dps.layers.api.idempotency import (
    KEY_RE,
    IdemEntry,
    IdempotencyCache,
    IdempotencyConflict,
)

POLICY = "gdpr-pseudonymise"
DATA = {"full_name": "Ananya Krishnan", "email": "ananya.k@example-mail.in", "ssn": "123-45-6789"}
OTHER = {"email": "rohan.m@example-mail.in"}
KEYS = [
    {"name": "retail", "key": "dps_retail", "tenant_ids": ["retail", "wealth"], "scopes": ["*"]},
]
RETAIL = {"X-API-KEY": "dps_retail", "X-DPS-Tenant": "retail"}
WEALTH = {"X-API-KEY": "dps_retail", "X-DPS-Tenant": "wealth"}
REPLAY = "idempotent-replay"


def _settings(**kw: Any) -> Settings:
    base: dict[str, Any] = dict(
        _env_file=None,
        env="local",
        auth_enabled=True,
        api_keys=json.dumps(KEYS),
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


def post(
    c: TestClient, route: str, body: dict[str, Any], key: str | None = None, **headers: str
) -> Any:
    extra = {"Idempotency-Key": key} if key is not None else {}
    return c.post(f"/api/v1/{route}", json=body, headers={**RETAIL, **extra, **headers})


def test_replay_with_caller_key_material_is_identical(client) -> None:  # noqa: ANN001
    c, sink = client
    km = post(c, "obfuscate", {"data": OTHER, "policy": POLICY}).json()["key_material"]
    body = {
        "data": DATA,
        "policy": POLICY,
        "options": {"consistency": {"key_material": km}},
    }
    first, second = post(c, "obfuscate", body, "k-1"), post(c, "obfuscate", body, "k-1")
    assert first.status_code == second.status_code == 200
    assert REPLAY not in first.headers and second.headers[REPLAY] == "true"
    a, b = first.json(), second.json()
    assert a["result"] == b["result"] and a["key_material"]["kid"] == b["key_material"]["kid"]
    assert a["trace_id"] == b["trace_id"]
    assert sink.events[-1].outcome == "replayed" and sink.events[-1].trace_id == a["trace_id"]
    seed = post(c, "synthesize", {"data": OTHER, "policy": POLICY}).json()["synthesis_seed"]
    sbody = {"data": DATA, "policy": POLICY, "options": {"consistency": {"synthesis_seed": seed}}}
    s1, s2 = post(c, "synthesize", sbody, "k-1s"), post(c, "synthesize", sbody, "k-1s")
    assert s2.headers[REPLAY] == "true" and s1.json()["result"] == s2.json()["result"]
    assert s1.json()["trace_id"] == s2.json()["trace_id"]


def test_replay_without_caller_material_keeps_no_key_material(client) -> None:  # noqa: ANN001
    c, _ = client
    body = {"data": DATA, "policy": POLICY}
    o1, o2 = post(c, "obfuscate", body, "k-1b"), post(c, "obfuscate", body, "k-1b")
    assert REPLAY not in o1.headers and o2.headers[REPLAY] == "true"
    j1, j2 = o1.json(), o2.json()
    assert j1["trace_id"] == j2["trace_id"]
    assert j1["key_material"]["kid"] != j2["key_material"]["kid"]  # fresh material, not stored
    for j in (j1, j2):  # each response reveals with its own key
        back = post(
            c,
            "reveal",
            {
                "data": j["result"],
                "key_material": j["key_material"],
                "reveal_hints": j["reveal_hints"],
            },
        )
        assert back.status_code == 200 and back.json()["result"] == DATA
    s1, s2 = post(c, "synthesize", body, "k-1c"), post(c, "synthesize", body, "k-1c")
    assert s2.headers[REPLAY] == "true" and s1.json()["trace_id"] == s2.json()["trace_id"]
    assert s1.json()["synthesis_seed"]["seed"] != s2.json()["synthesis_seed"]["seed"]
    entries = c.app.state.idempotency.entries()
    assert entries and all(isinstance(e, IdemEntry) for e in entries.values())
    assert all(
        {f.name for f in dataclasses.fields(e)} == {"body_sha256", "trace_id", "created"}
        for e in entries.values()
    )
    dump = repr(entries)
    secrets = [j1["key_material"]["dek"], j2["key_material"]["dek"], j1["key_material"]["kcv"]]
    secrets += [s1.json()["synthesis_seed"]["seed"], s2.json()["synthesis_seed"]["seed"]]
    leaves = [*DATA.values()]
    for j in (j1, j2, s1.json(), s2.json()):
        leaves += [str(v) for v in j["result"].values()]
    for needle in [*secrets, *leaves, "dek", "seed", "kcv"]:
        assert needle not in dump, needle


def test_without_the_header_each_call_gets_a_new_key(client) -> None:  # noqa: ANN001
    c, _ = client
    body = {"data": DATA, "policy": POLICY}
    a, b = post(c, "obfuscate", body), post(c, "obfuscate", body)
    assert a.json()["key_material"]["kid"] != b.json()["key_material"]["kid"]
    assert REPLAY not in b.headers and a.json()["trace_id"] != b.json()["trace_id"]


def test_same_key_other_body_is_refused(client) -> None:  # noqa: ANN001
    c, sink = client
    assert post(c, "obfuscate", {"data": DATA, "policy": POLICY}, "k-2").status_code == 200
    r = post(c, "obfuscate", {"data": OTHER, "policy": POLICY}, "k-2")
    assert r.status_code == 422 and r.json()["code"] == "idempotency_key_reused"
    assert "rohan" not in r.text and "example-mail" not in r.text
    assert sink.events[-1].outcome == "idempotency_key_reused"


def test_scope_is_per_tenant_and_route(client) -> None:  # noqa: ANN001
    c, _ = client
    body = {"data": DATA, "policy": POLICY}
    assert post(c, "obfuscate", body, "k-3").status_code == 200
    synth = post(c, "synthesize", body, "k-3")
    assert synth.status_code == 200 and REPLAY not in synth.headers
    other_tenant = post(c, "obfuscate", body, "k-3", **WEALTH)
    assert other_tenant.status_code == 200 and REPLAY not in other_tenant.headers
    detect = post(c, "detect", {"data": OTHER, "policy": POLICY}, "k-3")
    assert detect.status_code == 200 and REPLAY not in detect.headers


def test_wrapped_key_is_replayed_wrapped(client) -> None:  # noqa: ANN001
    c, _ = client
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = (
        private.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )
    body = {
        "data": DATA,
        "policy": POLICY,
        "options": {"wrap_key": {"type": "rsa-oaep-256", "public_key_pem": pem}},
    }
    a, b = post(c, "obfuscate", body, "k-4"), post(c, "obfuscate", body, "k-4")
    assert b.headers[REPLAY] == "true"
    for r in (a, b):
        km = r.json()["key_material"]
        assert km["wrapped_dek"] and km.get("dek") is None


def test_synthesize_and_documents_replay(client) -> None:  # noqa: ANN001
    pytest.importorskip("pdfplumber")
    from tests.documents.fixtures import text_pdf

    c, _ = client
    seed = post(c, "synthesize", {"data": OTHER, "policy": POLICY}).json()["synthesis_seed"]
    sbody = {"text": "Mail ananya.k@example-mail.in today", "policy": POLICY}
    sbody["options"] = {"consistency": {"synthesis_seed": seed}}
    s1, s2 = post(c, "synthesize", sbody, "k-5"), post(c, "synthesize", sbody, "k-5")
    assert s2.headers[REPLAY] == "true" and s1.json()["result"] == s2.json()["result"]
    km = post(c, "obfuscate", {"data": OTHER, "policy": POLICY}).json()["key_material"]
    request = {"policy": POLICY, "options": {"consistency": {"key_material": km}}}

    def upload() -> Any:
        return c.post(
            "/api/v1/documents/obfuscate",
            headers={**RETAIL, "Idempotency-Key": "k-5d"},
            files={"file": ("kyc.pdf", text_pdf(), "application/pdf")},
            data={"request": json.dumps(request)},
        )

    d1, d2 = upload(), upload()
    assert d1.status_code == d2.status_code == 200, d1.text
    assert REPLAY not in d1.headers and d2.headers[REPLAY] == "true"
    assert d1.json()["result"] == d2.json()["result"]
    assert d1.json()["trace_id"] == d2.json()["trace_id"]
    other = c.post(
        "/api/v1/documents/obfuscate",
        headers={**RETAIL, "Idempotency-Key": "k-5d"},
        files={"file": ("kyc.pdf", text_pdf(("Customer: Rohan Mehta",)), "application/pdf")},
        data={"request": json.dumps(request)},
    )
    assert other.status_code == 422 and other.json()["code"] == "idempotency_key_reused"


def test_ttl_and_eviction(client) -> None:  # noqa: ANN001
    c, _ = client
    now = [1000.0]
    c.app.state.idempotency = IdempotencyCache(ttl_seconds=600, max_entries=1, clock=lambda: now[0])
    body = {"data": DATA, "policy": POLICY}
    first = post(c, "detect", body, "k-6")
    assert post(c, "detect", body, "k-6").headers[REPLAY] == "true"
    now[0] += 601
    late = post(c, "detect", body, "k-6")
    assert REPLAY not in late.headers and late.json()["trace_id"] != first.json()["trace_id"]
    assert post(c, "detect", body, "k-7").status_code == 200  # evicts k-6 (max_entries=1)
    assert REPLAY not in post(c, "detect", body, "k-6").headers
    cache = IdempotencyCache(ttl_seconds=10, max_entries=2, clock=lambda: now[0])
    cache.put("a", IdemEntry("h1", "t1", now[0]))
    with pytest.raises(IdempotencyConflict):
        cache.get("a", "h2")
    assert cache.get("a", "h1") == IdemEntry("h1", "t1", now[0])
    disabled = IdempotencyCache(ttl_seconds=0, max_entries=2)
    disabled.put("a", IdemEntry("h1", "t1", 0.0))
    assert disabled.get("a", "h2") is None and disabled.entries() == {}


def test_a_failed_call_is_not_recorded(client) -> None:  # noqa: ANN001
    c, sink = client
    body = {"data": DATA, "policy": "no-such-preset"}
    assert post(c, "obfuscate", body, "k-8").status_code == 400
    assert c.app.state.idempotency.entries() == {}
    retry = post(c, "obfuscate", body, "k-8")
    assert retry.status_code == 400 and REPLAY not in retry.headers
    assert sink.events[-1].outcome == "bad_request"


def test_invalid_header(client) -> None:  # noqa: ANN001
    c, sink = client
    for bad in ("bad key!", "x" * 129, ""):
        r = post(c, "obfuscate", {"data": DATA, "policy": POLICY}, bad)
        assert r.status_code == 422 and r.json()["code"] == "idempotency_key_invalid", bad
        assert bad not in r.json()["detail"] or bad == ""
    assert sink.events[-1].outcome == "idempotency_key_invalid"
    assert KEY_RE.fullmatch("order-42:retry_1.a") and not KEY_RE.fullmatch("a b")


def test_no_material_in_logs(client) -> None:  # noqa: ANN001
    c, _ = client
    body = {"data": DATA, "policy": POLICY}
    with capture_logs() as logs:
        a, b = post(c, "obfuscate", body, "k-9"), post(c, "obfuscate", body, "k-9")
    assert b.headers[REPLAY] == "true"
    text = json.dumps(logs, default=str)
    for needle in (
        a.json()["key_material"]["dek"],
        b.json()["key_material"]["dek"],
        *DATA.values(),
    ):
        assert needle not in text


def test_disabled_cache_accepts_and_ignores_the_header() -> None:
    with TestClient(create_app(_settings(idempotency_ttl_seconds=0))) as c:
        body = {"data": DATA, "policy": POLICY}
        a, b = post(c, "obfuscate", body, "k-10"), post(c, "obfuscate", body, "k-10")
        assert a.status_code == b.status_code == 200 and REPLAY not in b.headers
        assert a.json()["trace_id"] != b.json()["trace_id"]
        assert post(c, "obfuscate", body, "bad key!").status_code == 422


def test_streams_replay_and_cors_names_the_headers(client) -> None:  # noqa: ANN001
    c, sink = client
    ndjson = "application/x-ndjson"
    lines = "\n".join([json.dumps({"policy": POLICY}), json.dumps(DATA), json.dumps(OTHER)])
    headers = {**RETAIL, "content-type": ndjson, "Idempotency-Key": "k-11"}
    a = c.post("/api/v1/detect", content=lines.encode(), headers=headers)
    b = c.post("/api/v1/detect", content=lines.encode(), headers=headers)
    assert REPLAY not in a.headers and b.headers[REPLAY] == "true"
    ha, hb = (json.loads(r.text.splitlines()[0]) for r in (a, b))
    assert ha["trace_id"] == hb["trace_id"] and sink.events[-1].outcome == "replayed"
    pre = c.options(
        "/api/v1/obfuscate",
        headers={
            "Origin": "http://console.example",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "idempotency-key",
        },
    )
    assert (
        pre.status_code == 200
        and "idempotency-key" in pre.headers["access-control-allow-headers"].lower()
    )
    simple = c.post(
        "/api/v1/detect",
        json={"data": OTHER, "policy": POLICY},
        headers={**RETAIL, "Origin": "http://console.example", "Idempotency-Key": "k-12"},
    )
    assert "idempotent-replay" in simple.headers["access-control-expose-headers"].lower()
