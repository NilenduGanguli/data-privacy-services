"""FINAL r1 privacy-ops B1: under ``DPS_LLM_EGRESS_CHECK=strict`` T3 labelling must reach the
provider when a categorical value is also a word of DPS's own fixed instructions.

The labelling system prompt lists the taxonomy's type names (``PASSPORT``, ``AADHAAR``,
``ACCOUNT_NUMBER``, ``PRIVATE_KEY``), and every sampled or shown value of a leftover field is a
protected original: a record or a page holding an ID-type code ``PASSPORT`` or a product
``ACCOUNT`` never reached the provider (``llm_blocked`` 1). A registered fixed system prompt is
DPS's constant text, not call data; any other system text keeps the full scan, and a protected
value in the call data still blocks. The provider is faked at ``LlmGateway._stellar`` only, so
the real ``complete()`` and its egress verifier run.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from dps.config import Settings
from dps.engine.detect.llm_label import LLM_LABEL_TIER, system_prompt
from dps.engine.policy import load_preset
from dps.engine.service import Payload, PrivacyEngine, RequestOptions
from dps.llm.egress import residual_values
from dps.llm.gateway import LlmGateway, LlmResult
from dps.observability import LLM_STATS, LlmStats

SENT: list[Any] = []
POLICY = {
    "preset": "gdpr-pseudonymise",
    "detect": {"tiers": ["structural", "patterns", LLM_LABEL_TIER]},
}


async def _seam(
    self: LlmGateway, messages: list[dict[str, str]], temperature: float, max_tokens: int
) -> LlmResult:
    SENT.append(messages)
    sent = json.loads(messages[-1]["content"])
    reply = [
        {"ref": e["ref"], "entity_type": "CUSTOMER_ID", "confidence": 0.93}
        for e in sent
        if e.get("name") in ("ref_cd", "member_ref")
    ]
    return LlmResult(text=json.dumps(reply), provider="stellar", model="fake")


@pytest.fixture(autouse=True)
def seam(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(LlmGateway, "_stellar", _seam)
    SENT.clear()


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        env="local",
        llm_provider="stellar",
        stellar_base_url="https://stellar.invalid",
        coin_url="https://coin.invalid",
        llm_egress_check="strict",
        fpe_enabled=False,
        ner_enabled=False,
    )


@pytest.mark.parametrize(
    "record",
    [
        {"kyc": {"doc_kind": "PASSPORT", "ref_cd": "AB-1234-77"}},
        {"kyc": {"doc_kind": "AADHAAR", "ref_cd": "AB-1234-77"}},
        {"kyc": {"segment": "PRIVATE", "ref_cd": "AB-1234-77"}},
        {"kyc": {"product": "ACCOUNT", "ref_cd": "AB-1234-77"}},
    ],
    ids=["passport", "aadhaar", "private", "account"],
)
async def test_l1_a_type_name_value_does_not_block_labelling(record: dict[str, Any]) -> None:
    settings = _settings()
    engine = PrivacyEngine(settings, gateway=LlmGateway(settings))
    token = LLM_STATS.set(LlmStats())
    try:
        found = await engine.label_leftovers(Payload(data=record), POLICY, RequestOptions())
        stats = LLM_STATS.get()
    finally:
        LLM_STATS.reset(token)
    assert stats is not None and stats.counts()["llm_blocked"] == 0
    assert len(SENT) == 1 and stats.counts()["llm_calls"] == 1
    assert [(f.locator.path, f.entity_type) for f in found] == [("$.kyc.ref_cd", "CUSTOMER_ID")]
    value = next(v for v in record["kyc"].values() if v != "AB-1234-77")
    assert value not in SENT[0][1]["content"]  # the call data never holds the value


@pytest.mark.parametrize("selected", ["PASSPORT", "PRIVATE"])
async def test_l3_a_selected_type_name_does_not_block_labelling(selected: str) -> None:
    from dps.layers.scanner.classify import classify_fields, label_leftover_fields, page_protected
    from dps.layers.scanner.models import OptionFacts, PageSnapshot, TextFacts
    from tests.scanner.helpers import field_facts

    settings = _settings()
    base = load_preset("gdpr-pseudonymise")
    tiers = [*base.detect.tiers, LLM_LABEL_TIER]
    policy = base.model_copy(update={"detect": base.detect.model_copy(update={"tiers": tiers})})
    engine = PrivacyEngine(settings, gateway=None)
    detector = engine.detector(policy, "en_IN")
    options = ("Passport", "Aadhaar") if selected == "PASSPORT" else ("Private", "Retail")
    opts = [
        OptionFacts(value="", text="Choose…", label="Choose…", disabled=False, selected=False),
        *(
            OptionFacts(
                value=o.upper(), text=o, label=o, disabled=False, selected=o.upper() == selected
            )
            for o in options
        ),
    ]
    select = field_facts(
        key="0:1",
        field_no=1,
        tag="select",
        input_type="select-one",
        name="doc_kind",
        label="Document",
        label_source="label_for",
        options=opts,
    )
    ref = field_facts(
        key="0:2", field_no=2, name="member_ref", label="Member reference", label_source="label_for"
    )
    snap = PageSnapshot(
        url="http://127.0.0.1/apply",
        title="Apply",
        forms=[],
        fields=[select, ref],
        texts=[TextFacts(frame_index=0, i=0, text="Document", in_table=False, label_hint=None)],
        links=[],
        clickables=[],
        frames=[],
    )
    fields = [select, ref]
    classes = classify_fields(fields, detector=detector, min_score=policy.detect.min_score)
    token = LLM_STATS.set(LlmStats())
    try:
        labelled, outcome = await label_leftover_fields(
            fields,
            classes,
            gateway=LlmGateway(settings),
            settings=settings,
            policy=policy,
            detector=detector,
            protected=page_protected(snap, detector=detector),
        )
        stats = LLM_STATS.get()
    finally:
        LLM_STATS.reset(token)
    assert stats is not None and stats.counts()["llm_blocked"] == 0
    assert len(SENT) == 1 and outcome is not None and outcome.llm_calls == 1
    assert labelled["0:2"].entity_type == "CUSTOMER_ID"


def test_only_a_registered_fixed_system_prompt_is_exempt() -> None:
    label_system = {"role": "system", "content": system_prompt()}
    assert "PASSPORT" in label_system["content"]
    user = {"role": "user", "content": '[{"name":"doc_kind","ref":"field_1"}]'}
    assert residual_values([label_system, user], protected=["PASSPORT", "ACCOUNT"]) == 0
    # the same value in the call data still blocks
    leaky = {"role": "user", "content": '[{"name":"doc_kind","label":"PASSPORT"}]'}
    assert residual_values([label_system, leaky], protected=["PASSPORT"]) == 1
    # any other system text is scanned in full: protected values and patterns
    other = {"role": "system", "content": system_prompt() + " Note: PASSPORT."}
    assert residual_values([other, user], protected=["PASSPORT"]) >= 1
    bic = {"role": "system", "content": "Entity types: SWIFT_BIC, USERNAME, VOTER_ID"}
    assert residual_values([bic, user]) == 1  # USERNAME has a SWIFT BIC's shape


def test_a_protected_value_shape_is_not_a_value() -> None:
    """A value written only in x, X, 9 and punctuation (``999``) cannot be told apart from the
    value shapes DPS sends (``"shapes": ["999"]``): it never blocks a call; a real number does."""
    user = {"role": "user", "content": '[{"name":"risk_score","shapes":["999","99"]}]'}
    assert residual_values([user], protected=["999", "9999", "XX-999"]) == 0
    assert residual_values([user], protected=["162"]) == 0
    hint = {"role": "user", "content": '[{"name":"risk_score","hints":{"null_rate":0.162}}]'}
    assert residual_values([hint], protected=["162"]) == 1  # why the rates are rendered short
