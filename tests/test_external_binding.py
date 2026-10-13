"""SEC-4 (programme plan D-B): external tokens are bound to the caller's key, and every
detokenization batch is audited.

DPS holds the bank tokenizer's credentials, so ``/reveal`` must never act as a confused deputy:
a caller who names a token DPS did not produce under the presented key must be refused **before**
the tokenization service is called. The hint of an ``external_tokenize`` value therefore carries a
tag ``bind`` — an HMAC of the token under a key derived from the caller's DEK *and* a server-held
binding key (derived from ``DPS_JOB_KEK``). The caller holds its DEK, so a tag under the DEK alone
could be forged; the server key makes it unforgeable outside DPS.

The tokenizer here is the vaultless mock (``docker/tokenizer-mock/server.py``) on an ephemeral
port, with a request counter on the server side: "not called" is proven by the server's own count.
"""

from __future__ import annotations

import base64
import importlib.util
import json
import threading
from collections import Counter
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from dps.app import create_app
from dps.audit import AuditEvent, AuditUnavailable, MemoryAuditSink
from dps.config import Settings
from dps.engine.service import EngineError, Payload, PrivacyEngine, RequestOptions
from dps.engine.transform import crypto
from dps.engine.transform.crypto import generate_key_material
from dps.engine.transform.external import HttpTokenizer
from dps.errors import CodedError
from dps.layers.api.ndjson import NDJSON

ROOT = Path(__file__).resolve().parents[1]
MOCK = ROOT / "docker" / "tokenizer-mock" / "server.py"
TOKEN = "binding-test-token"
KEK = base64.b64encode(b"\x11" * 32).decode("ascii")
OTHER_KEK = base64.b64encode(b"\x22" * 32).decode("ascii")
POLICY = {
    "preset": "gdpr-pseudonymise",
    "treat": {"SSN": {"method": "external_tokenize", "profile": "ssn"}},
}
RECORD = {"customer_id": "CUST-884120", "ssn": "123-45-6789", "email": "ann.k@example-mail.in"}
FOREIGN = "987-65-4320"  # a customer's SSN that DPS never tokenized for this caller


class _Mock:
    """The tokenizer mock in a thread, counting the POSTs it receives per path."""

    def __init__(self) -> None:
        spec = importlib.util.spec_from_file_location("tokenizer_mock_binding", MOCK)
        module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
        spec.loader.exec_module(module)  # type: ignore[union-attr]
        self.server = module.make_server("127.0.0.1", 0, token=TOKEN, key="binding-key")
        self.received: Counter[str] = Counter()
        received = self.received
        base = self.server.RequestHandlerClass

        class Counting(base):  # type: ignore[misc, valid-type]
            def do_POST(self) -> None:  # noqa: N802
                received[self.path] += 1
                super().do_POST()

            def log_message(self, fmt: str, *args: Any) -> None:
                pass

        self.server.RequestHandlerClass = Counting
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tokenizer(self) -> HttpTokenizer:
        return HttpTokenizer(self.url, token=TOKEN, retries=0)

    @property
    def detokenize_calls(self) -> int:
        return self.received["/v1/detokenize"]


@pytest.fixture
def mock() -> Iterator[_Mock]:
    m = _Mock()
    yield m
    m.server.shutdown()


def _settings(**kw: Any) -> Settings:
    base: dict[str, Any] = dict(
        _env_file=None,
        env="local",
        llm_provider="stub",
        ner_enabled=False,
        fpe_enabled=False,
        auth_enabled=False,
        rate_limit_enabled=False,
        job_kek=KEK,
    )
    base.update(kw)
    return Settings(**base)  # type: ignore[arg-type]


def _hints(out: Any) -> list[dict[str, Any]]:
    return [h.model_dump(mode="json") for h in out.reveal_hints]


def _external(hints: list[dict[str, Any]]) -> dict[str, Any]:
    return next(h for h in hints if h["method"] == "external_tokenize")


def _dek_only_tag(key: dict[str, Any], token: str) -> str:
    """The tag the plan's first formula would accept: HMAC under a subkey of the DEK alone —
    which the caller can compute, because it holds its DEK."""
    km = crypto.key_material_from_caller(key)
    sub = crypto.subkey(km, "external_tokenize|SSN|ssn")
    return crypto.b32(crypto.hmac_bytes(sub, token))[:26]


def test_obfuscate_binds_each_external_token_and_the_round_trip_still_works(mock: _Mock) -> None:
    engine = PrivacyEngine(_settings(), tokenizer=mock.tokenizer())
    out = engine.obfuscate(Payload(data=RECORD), POLICY, RequestOptions())
    hint = _external(_hints(out))
    tag = hint["reverse_params"]["bind"]
    assert isinstance(tag, str) and len(tag) == 26 and "123" not in tag
    key = out.key_material.model_dump(mode="json")  # type: ignore[union-attr]
    back = engine.reveal(Payload(data=out.result), key, _hints(out))
    assert back.result == RECORD and mock.detokenize_calls == 1


def test_a_foreign_token_with_a_valid_looking_hint_is_refused_and_the_tokenizer_not_called(
    mock: _Mock,
) -> None:
    engine = PrivacyEngine(_settings(), tokenizer=mock.tokenizer())
    out = engine.obfuscate(Payload(data=RECORD), POLICY, RequestOptions())
    key = out.key_material.model_dump(mode="json")  # type: ignore[union-attr]
    legit = _external(_hints(out))
    # a bank token for someone else's SSN, obtained outside DPS (the bank's own vault output)
    foreign = mock.tokenizer().tokenize("SSN", [FOREIGN], profile="ssn")[0]
    before = mock.detokenize_calls
    forged: list[dict[str, Any]] = [
        {k: v for k, v in legit.items() if k != "reverse_params"},  # no tag at all
        {**legit, "reverse_params": {"bind": _dek_only_tag(key, foreign)}},  # DEK-only tag
        {**legit, "reverse_params": {"bind": "a" * 26}},  # a made-up tag
        legit,  # the tag of another token (the legit one), replayed
        {**legit, "reverse_params": {"bind": 7}},  # not even a string
    ]
    for hint in forged:
        doc = {**out.result, "ssn": foreign}
        with pytest.raises(CodedError) as err:
            engine.reveal(Payload(data=doc), key, [hint])
        assert err.value.code == "external_token_not_bound" and err.value.status == 403
        assert FOREIGN not in str(err.value) and foreign not in str(err.value)
    # the legit token under another profile: the tag binds the profile too
    with pytest.raises(CodedError, match="external_token_not_bound"):
        engine.reveal(Payload(data=out.result), key, [{**legit, "params": {"profile": "other"}}])
    # one forged hint among legit ones refuses the whole request before any call
    with pytest.raises(CodedError, match="external_token_not_bound"):
        engine.reveal(
            Payload(data={**out.result, "extra": foreign}),
            key,
            [*_hints(out), {**legit, "path": "$.extra", "reverse_params": {"bind": "b" * 26}}],
        )
    assert mock.detokenize_calls == before  # the counting server saw no detokenize request


def test_a_tag_made_under_another_dps_deployment_is_refused(mock: _Mock) -> None:
    ours = PrivacyEngine(_settings(), tokenizer=mock.tokenizer())
    theirs = PrivacyEngine(_settings(job_kek=OTHER_KEK), tokenizer=mock.tokenizer())
    out = theirs.obfuscate(Payload(data=RECORD), POLICY, RequestOptions())
    key = out.key_material.model_dump(mode="json")  # type: ignore[union-attr]
    with pytest.raises(CodedError, match="external_token_not_bound"):
        ours.reveal(Payload(data=out.result), key, _hints(out))
    assert mock.detokenize_calls == 0


def test_binding_survives_a_kek_rotation_while_the_previous_kek_is_kept(mock: _Mock) -> None:
    old = PrivacyEngine(_settings(job_kek=OTHER_KEK), tokenizer=mock.tokenizer())
    out = old.obfuscate(Payload(data=RECORD), POLICY, RequestOptions())
    key = out.key_material.model_dump(mode="json")  # type: ignore[union-attr]
    rotated = PrivacyEngine(
        _settings(job_kek=KEK, job_kek_previous=OTHER_KEK), tokenizer=mock.tokenizer()
    )
    assert rotated.reveal(Payload(data=out.result), key, _hints(out)).result == RECORD


def test_without_a_binding_key_external_tokenize_fails_closed(mock: _Mock) -> None:
    engine = PrivacyEngine(_settings(job_kek=""), tokenizer=mock.tokenizer())
    plan = engine.plan(Payload(data=RECORD), POLICY, RequestOptions(), mode="obfuscate")
    assert any(
        r["method"] == "external_tokenize" and r["reason"].endswith("external:unbound")
        for r in plan.plan
    )
    with pytest.raises(EngineError, match="cannot be bound"):
        engine.obfuscate(Payload(data=RECORD), POLICY, RequestOptions())
    # a key generated elsewhere cannot reveal anything external on this deployment either
    hint = {
        "path": "$.ssn",
        "entity_type": "SSN",
        "method": "external_tokenize",
        "params": {"profile": "ssn"},
        "reverse_params": {"bind": "c" * 26},
    }
    km = generate_key_material()
    hint |= {"kid": km.kid, "kcv": km.kcv}
    with pytest.raises(CodedError, match="external_token_not_bound"):
        engine.reveal(Payload(data={"ssn": "tk_x"}), km.model_dump(mode="json"), [hint])
    assert mock.detokenize_calls == 0


class _Strict(MemoryAuditSink):
    """A strict sink that refuses only the per-batch detokenization events."""

    def write(self, event: AuditEvent) -> None:
        if event.action == "external.detokenize":
            raise AuditUnavailable()
        super().write(event)


def test_api_audits_each_detokenization_batch_and_refuses_unbound_tokens(mock: _Mock) -> None:
    sink = MemoryAuditSink()
    settings = _settings()
    engine = PrivacyEngine(settings, tokenizer=mock.tokenizer())
    with TestClient(create_app(settings, engine=engine, audit_sink=sink)) as client:
        two = {"a": {"ssn": "123-45-6789"}, "b": {"ssn": "234-56-7891"}}
        r = client.post("/api/v1/obfuscate", json={"data": two, "policy": POLICY})
        assert r.status_code == 200, r.text
        out = r.json()
        body = {
            "data": out["result"],
            "key_material": out["key_material"],
            "reveal_hints": out["reveal_hints"],
        }
        ok = client.post("/api/v1/reveal", json=body)
        assert ok.status_code == 200 and ok.json()["result"] == two
        assert mock.detokenize_calls == 1  # one batch for the one (entity, profile) group
        batches = [e for e in sink.events if e.action == "external.detokenize"]
        assert len(batches) == 1
        batch = batches[0]
        assert batch.mode == "reveal" and batch.tenant_id and batch.key_id
        assert batch.findings_by_type == {"SSN": 2} and batch.counts == {"tokens": 2}
        assert batch.trace_id == ok.json()["trace_id"]
        dump = json.dumps([e.model_dump(mode="json") for e in sink.events])
        assert "123-45-6789" not in dump and "tk_" not in dump

        foreign = mock.tokenizer().tokenize("SSN", [FOREIGN], profile="ssn")[0]
        legit = next(h for h in out["reveal_hints"] if h["method"] == "external_tokenize")
        bad = {
            "data": {"a": {"ssn": foreign}},
            "key_material": out["key_material"],
            "reveal_hints": [{**legit, "path": "$.a.ssn", "reverse_params": {"bind": "d" * 26}}],
        }
        refused = client.post("/api/v1/reveal", json=bad)
        assert refused.status_code == 403
        assert refused.json()["code"] == "external_token_not_bound"
        assert foreign not in refused.text and FOREIGN not in refused.text
        assert mock.detokenize_calls == 1  # unchanged: refused before the service
        assert sink.events[-1].outcome == "external_token_not_bound"

        # the NDJSON twin of /reveal (an array streamed) is bound and audited the same way
        arr = client.post("/api/v1/obfuscate", json={"data": [RECORD], "policy": POLICY}).json()
        streamed = client.post(
            "/api/v1/reveal",
            json={
                "data": arr["result"],
                "key_material": arr["key_material"],
                "reveal_hints": arr["reveal_hints"],
            },
            headers={"accept": NDJSON},
        )
        lines = [json.loads(x) for x in streamed.text.splitlines() if x.strip()]
        assert lines[1]["result"] == RECORD and mock.detokenize_calls == 2
        assert len([e for e in sink.events if e.action == "external.detokenize"]) == 2
        forged = [{**h, "reverse_params": {"bind": "e" * 26}} for h in arr["reveal_hints"]]
        streamed = client.post(
            "/api/v1/reveal",
            json={
                "data": [{**arr["result"][0], "ssn": foreign}],
                "key_material": arr["key_material"],
                "reveal_hints": forged,
            },
            headers={"accept": NDJSON},
        )
        lines = [json.loads(x) for x in streamed.text.splitlines() if x.strip()]
        assert lines[-1]["type"] == "error" and lines[-1]["code"] == "external_token_not_bound"
        assert mock.detokenize_calls == 2


def test_a_strict_sink_that_refuses_the_batch_event_stops_the_call(mock: _Mock) -> None:
    settings = _settings(audit_strict=True)
    engine = PrivacyEngine(settings, tokenizer=mock.tokenizer())
    out = engine.obfuscate(Payload(data=RECORD), POLICY, RequestOptions())
    with TestClient(create_app(settings, engine=engine, audit_sink=_Strict())) as client:
        r = client.post(
            "/api/v1/reveal",
            json={
                "data": out.result,
                "key_material": out.key_material.model_dump(mode="json"),  # type: ignore[union-attr]
                "reveal_hints": _hints(out),
            },
        )
    assert r.status_code == 503 and r.json()["code"] == "audit_unavailable"
    assert mock.detokenize_calls == 0  # the event is written before the service is called
