"""T3 — LLM metadata labelling of leftover JSON fields (M1 Task 19D, spec §5.3).

The gateway receives field metadata only — names, JSON types, value *shapes* — never a value.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

import dps.engine.detect.llm_label as llm_label
from dps.app import create_app
from dps.config import Settings
from dps.engine.detect.llm_label import FieldMeta, label_fields
from dps.engine.service import Payload, PrivacyEngine, RequestOptions
from dps.llm.gateway import LlmCallError, LlmGateway, LlmResult
from dps.observability import LLM_LABEL_FIELDS, LLM_RAW_PII

TIERS = ["structural", "patterns", "ner", "llm_label"]
POLICY = {"preset": "gdpr-pseudonymise", "detect": {"tiers": TIERS}}
LOAN = {"loan": {"ref_cd": "AB-1234-77", "status": "OPEN"}}


def _settings(**kw: Any) -> Settings:
    base: dict[str, Any] = dict(
        _env_file=None,
        env="local",
        auth_enabled=False,
        llm_provider="stellar",
        stellar_base_url="https://stellar.test",
        coin_url="https://coin.test",
        fpe_enabled=False,
        ner_enabled=False,
        rate_limit_enabled=False,
    )
    return Settings(**{**base, **kw})


class RecordingGateway(LlmGateway):
    """A configured provider whose replies are scripted and whose prompts are kept."""

    def __init__(self, reply: str | Exception, settings: Settings | None = None) -> None:
        super().__init__(settings or _settings())
        self.reply = reply
        self.messages: list[list[dict[str, str]]] = []
        self.purposes: list[str] = []

    async def complete(self, messages, **kw):  # noqa: ANN001, ANN003, ANN201
        self.messages.append(messages)
        self.purposes.append(kw.get("purpose", ""))
        if isinstance(self.reply, Exception):
            raise self.reply
        return LlmResult(text=self.reply, provider="stellar", model="test")

    def prompt(self) -> str:
        return json.dumps(self.messages)


class StubSpy(RecordingGateway):
    def __init__(self) -> None:
        super().__init__("[]", Settings(_env_file=None, llm_provider="stub"))


def verdict(ref: str, entity: str = "CUSTOMER_ID", confidence: float = 0.92) -> str:
    return json.dumps([{"ref": ref, "entity_type": entity, "confidence": confidence}])


def engine_with(gateway: LlmGateway | None, **kw: Any) -> PrivacyEngine:
    return PrivacyEngine(_settings(**kw), gateway=gateway)


async def test_no_call_without_the_tier_or_with_the_stub() -> None:
    gateway = RecordingGateway(verdict("$.loan.ref_cd"))
    engine = engine_with(gateway)
    preset = await engine.label_leftovers(Payload(data=LOAN), "gdpr-pseudonymise", RequestOptions())
    assert preset == [] and gateway.messages == []
    stub = StubSpy()
    stubbed = await engine_with(stub).label_leftovers(Payload(data=LOAN), POLICY, RequestOptions())
    assert stubbed == [] and stub.messages == []
    assert (
        await engine_with(None).label_leftovers(Payload(data=LOAN), POLICY, RequestOptions()) == []
    )
    text = await engine.label_leftovers(Payload(text="ref AB-1234-77"), POLICY, RequestOptions())
    assert text == [] and gateway.messages == []
    field = FieldMeta(ref="$.a", name="a", data_type="string", shapes=["XX-99"])
    assert await label_fields([field], gateway=None, settings=_settings()) == []


def test_leftover_json_field_is_labelled_and_transformed() -> None:
    gateway = RecordingGateway(verdict("$.loan.ref_cd"))
    settings = _settings()
    with TestClient(create_app(settings, engine=PrivacyEngine(settings, gateway=gateway))) as c:
        r = c.post("/api/v1/obfuscate", json={"data": LOAN, "policy": POLICY})
    assert r.status_code == 200, r.text
    body = r.json()
    t3 = [f for f in body["findings"] if f["detectors"] == ["llm_label"]]
    assert len(t3) == 1 and t3[0]["score"] == 0.85 and t3[0]["entity_type"] == "CUSTOMER_ID"
    assert t3[0]["locator"]["path"] == "$.loan.ref_cd" and t3[0]["evidence"] == ["llm_label"]
    assert body["result"]["loan"]["ref_cd"] != "AB-1234-77"
    assert body["report"]["llm"]["calls"] == 1 and body["report"]["verification"]["passed"]
    assert len(gateway.messages) == 1 and gateway.purposes == ["label"]
    prompt = gateway.prompt()
    assert "ref_cd" in prompt and "XX-9999-99" in prompt
    assert "AB-1234-77" not in prompt and "OPEN" not in prompt


async def test_array_items_share_one_verdict() -> None:
    gateway = RecordingGateway(verdict("$.rows[*].code", "ACCOUNT_NUMBER"))
    data = {"rows": [{"code": "A1"}, {"code": "B2"}]}
    found = await engine_with(gateway).label_leftovers(Payload(data=data), POLICY, RequestOptions())
    sent = json.loads(gateway.messages[0][1]["content"])
    assert [f["ref"] for f in sent] == ["$.rows[*].code"] and sent[0]["name"] == "code"
    assert sorted(f.locator.path for f in found) == ["$.rows[0].code", "$.rows[1].code"]
    assert all(f.entity_type == "ACCOUNT_NUMBER" and f.value_hash for f in found)
    assert found.llm_calls == 1


async def test_value_like_names_are_aliased() -> None:
    gateway = RecordingGateway(verdict("field_1", "ACCOUNT_NUMBER"))
    data = {"ananya.k@example-mail.in": "QX-7Z"}
    found = await engine_with(gateway).label_leftovers(Payload(data=data), POLICY, RequestOptions())
    prompt = gateway.prompt()
    assert "field_1" in prompt and "ananya" not in prompt and "example-mail" not in prompt
    assert [f.locator.path for f in found] == ["$['ananya.k@example-mail.in']"]


async def test_leak_guard_blocks_the_call(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm_label, "value_shape", lambda value: value)
    gateway = RecordingGateway(verdict("$.loan.ref_cd"))
    before = LLM_LABEL_FIELDS.labels("blocked")._value.get()  # noqa: SLF001
    found = await engine_with(gateway).label_leftovers(Payload(data=LOAN), POLICY, RequestOptions())
    assert found == [] and gateway.messages == [] and found.llm_calls == 0
    assert LLM_LABEL_FIELDS.labels("blocked")._value.get() > before  # noqa: SLF001


@pytest.mark.parametrize(
    "reply",
    [
        verdict("$.loan.ref_cd", "MAGIC_NUMBER"),
        verdict("$.loan.elsewhere"),
        verdict("$.loan.ref_cd", confidence=0.3),
        "I think it is a customer id",
        '[{"ref": "$.loan.ref_cd"}]',
        '{"ref": "$.loan.ref_cd", "entity_type": "CUSTOMER_ID", "confidence": "high"}',
        LlmCallError("stellar call failed: HTTPStatusError 502"),
    ],
)
async def test_bad_replies_are_ignored(reply: str | Exception) -> None:
    gateway = RecordingGateway(reply)
    found = await engine_with(gateway).label_leftovers(Payload(data=LOAN), POLICY, RequestOptions())
    assert found == [] and len(gateway.messages) == 1


async def test_at_most_max_fields_are_sent() -> None:
    gateway = RecordingGateway("[]")
    data = {f"c{n}": f"Q{n}-Z" for n in range(5)}
    engine = engine_with(gateway, llm_label_max_fields=3)
    skipped = LLM_LABEL_FIELDS.labels("skipped")._value.get()  # noqa: SLF001
    await engine.label_leftovers(Payload(data=data), POLICY, RequestOptions())
    assert len(json.loads(gateway.messages[0][1]["content"])) == 3
    assert LLM_LABEL_FIELDS.labels("skipped")._value.get() == skipped + 2  # noqa: SLF001


def test_streams_label_once_per_stream() -> None:
    gateway = RecordingGateway(verdict("$.loan.ref_cd"))
    settings = _settings()
    items = [{"loan": {"ref_cd": f"AB-12{n}4-77"}} for n in range(3)]
    lines = [json.dumps({"policy": POLICY}), *(json.dumps(i) for i in items)]
    with TestClient(create_app(settings, engine=PrivacyEngine(settings, gateway=gateway))) as c:
        r = c.post(
            "/api/v1/detect",
            content="\n".join(lines).encode(),
            headers={"content-type": "application/x-ndjson"},
        )
    out = [json.loads(line) for line in r.text.splitlines()]
    assert len(gateway.messages) == 1 and out[-1]["type"] == "report"
    assert out[-1]["report"]["llm_calls"] == 1
    for line in out[1:-1]:
        assert [f["detectors"] for f in line["findings"]] == [["llm_label"]]
    assert all(i["loan"]["ref_cd"] not in gateway.prompt() for i in items)


async def test_raw_pii_counter_stays_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    before = LLM_RAW_PII._value.get()  # noqa: SLF001
    for reply in (verdict("$.loan.ref_cd"), "nonsense"):
        await engine_with(RecordingGateway(reply)).label_leftovers(
            Payload(data=LOAN), POLICY, RequestOptions()
        )
    monkeypatch.setattr(llm_label, "value_shape", lambda value: value)
    await engine_with(RecordingGateway("[]")).label_leftovers(
        Payload(data=LOAN), POLICY, RequestOptions()
    )
    assert LLM_RAW_PII._value.get() == before  # noqa: SLF001
