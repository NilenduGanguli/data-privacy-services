"""S3 review F-11: ``external_tokenize`` is no open tokenization oracle under DPS's bank
credentials. ``DPS_TOKENIZER_PROFILES`` (JSON ``{"<tenant>|*": ["ENTITY:profile", …]}``) lists
the entity types and data elements (profiles) each tenant may tokenize; anything else is refused
**before** the service is called (403 ``tokenizer_profile_not_allowed`` on Layer 1, the job fails
with that code on Layer 2). Every tokenization batch is audited (``external.tokenize``) before the
call. Staging/prod refuse to boot with a tokenizer and no allow-list."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

import pytest
from fastapi.testclient import TestClient

from dps.app import create_app
from dps.audit import MemoryAuditSink
from dps.config import Settings
from dps.engine.service import Payload, PrivacyEngine, RequestOptions
from dps.errors import CodedError
from dps.observability import CURRENT_TENANT
from dps.posture import posture_problems

KEK = "ERERERERERERERERERERERERERERERERERERERERERE="
SSN = "123-45-6789"


def _policy(profile: str, entity: str = "SSN") -> dict[str, Any]:
    return {
        "preset": "gdpr-pseudonymise",
        "treat": {entity: {"method": "external_tokenize", "profile": profile}},
    }


class _CountingTokenizer:
    name = "counting"

    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None, int]] = []

    def tokenize(
        self, entity_type: str, values: Sequence[str], *, profile: str | None
    ) -> list[str]:
        self.calls.append((entity_type, profile, len(values)))
        return [f"tk_{i}" for i, _ in enumerate(values)]

    def detokenize(
        self, entity_type: str, tokens: Sequence[str], *, profile: str | None
    ) -> list[str]:
        return list(tokens)

    def health(self) -> bool:
        return True

    def close(self) -> None:
        return None


def _settings(**kw: object) -> Settings:
    base: dict[str, object] = dict(
        _env_file=None,
        env="local",
        llm_provider="stub",
        ner_enabled=False,
        fpe_enabled=False,
        job_kek=KEK,
        auth_enabled=False,
        rate_limit_enabled=False,
        tokenizer_profiles=json.dumps({"ta": ["SSN:dps-ssn"], "*": ["CARD_PAN:dps-pan"]}),
    )
    base.update(kw)
    return Settings(**base)  # type: ignore[arg-type]


def test_the_allow_list_is_per_tenant_entity_and_profile() -> None:
    s = _settings()
    assert s.tokenizer_profile_allowed("ta", "SSN", "dps-ssn")
    assert s.tokenizer_profile_allowed("ta", "CARD_PAN", "dps-pan")  # "*" applies to every tenant
    assert s.tokenizer_profile_allowed("tb", "CARD_PAN", "dps-pan")
    assert not s.tokenizer_profile_allowed("tb", "SSN", "dps-ssn")
    assert not s.tokenizer_profile_allowed("ta", "SSN", "dob")
    assert not s.tokenizer_profile_allowed("ta", "DATE_OF_BIRTH", "dps-ssn")
    assert not s.tokenizer_profile_allowed("ta", "SSN", None)
    unrestricted = _settings(tokenizer_profiles="")
    assert unrestricted.tokenizer_profile_allowed("anyone", "SSN", "anything")


@pytest.mark.parametrize(
    "raw",
    ['["SSN:x"]', '{"ta": "SSN:x"}', '{"ta": ["ssn:x"]}', '{"ta": ["SSN:bad profile"]}', "{"],
)
def test_a_malformed_allow_list_is_refused_without_echo(raw: str) -> None:
    with pytest.raises(ValueError) as refused:
        _settings(tokenizer_profiles=raw)
    assert "bad profile" not in str(refused.value) and "ssn:x" not in str(refused.value)


def test_a_profile_outside_the_allow_list_never_reaches_the_service() -> None:
    tokenizer = _CountingTokenizer()
    engine = PrivacyEngine(_settings(), tokenizer=tokenizer)  # type: ignore[arg-type]
    token = CURRENT_TENANT.set("ta")
    try:
        out = engine.obfuscate(Payload(data={"ssn": SSN}), _policy("dps-ssn"), RequestOptions())
        assert out.result["ssn"].startswith("tk_") and tokenizer.calls == [("SSN", "dps-ssn", 1)]
        with pytest.raises(CodedError) as refused:  # the oracle probe: the bank's DOB element
            engine.obfuscate(
                Payload(data={"dob": "1971-03-14"}),
                _policy("dob", "DATE_OF_BIRTH"),
                RequestOptions(),
            )
        assert refused.value.code == "tokenizer_profile_not_allowed" and refused.value.status == 403
    finally:
        CURRENT_TENANT.reset(token)
    assert len(tokenizer.calls) == 1


def test_over_http_the_batch_is_audited_and_a_refusal_is_403() -> None:
    settings = _settings(tokenizer_profiles=json.dumps({"default": ["SSN:dps-ssn"]}))
    tokenizer = _CountingTokenizer()
    sink = MemoryAuditSink()
    engine = PrivacyEngine(settings, tokenizer=tokenizer)  # type: ignore[arg-type]
    with TestClient(create_app(settings, engine=engine, audit_sink=sink)) as client:
        ok = client.post(
            "/api/v1/obfuscate", json={"data": {"ssn": SSN}, "policy": _policy("dps-ssn")}
        )
        assert ok.status_code == 200, ok.text
        refused = client.post(
            "/api/v1/obfuscate", json={"data": {"ssn": SSN}, "policy": _policy("ops")}
        )
        assert (refused.status_code, refused.json()["code"]) == (
            403,
            "tokenizer_profile_not_allowed",
        )
        stream = client.post(
            "/api/v1/obfuscate",
            content=json.dumps({"policy": _policy("ops")}) + "\n" + json.dumps({"ssn": SSN}) + "\n",
            headers={"Content-Type": "application/x-ndjson", "Accept": "application/x-ndjson"},
        )
        assert "tk_" not in stream.text and SSN not in stream.text
    assert tokenizer.calls == [("SSN", "dps-ssn", 1)]
    batches = [e for e in sink.events if e.action == "external.tokenize"]
    assert len(batches) == 1 and batches[0].counts == {"tokens": 1, "profile:dps-ssn": 1}
    assert batches[0].findings_by_type == {"SSN": 1} and SSN not in json.dumps(
        batches[0].model_dump(mode="json")
    )


def test_l2_batches_are_refused_before_the_service_is_called() -> None:
    from dps.engine.batch import BatchTransformer, ColumnPlan, TokenizerProfileRefused
    from dps.engine.policy import MethodSpec, resolve_policy
    from dps.engine.transform.crypto import generate_key_material
    from dps.layers.connectors.base import ColumnInfo, ObjectRef, TableShape

    tokenizer = _CountingTokenizer()
    engine = PrivacyEngine(_settings(), tokenizer=tokenizer)  # type: ignore[arg-type]
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
            method=MethodSpec(method="external_tokenize", params={"profile": "ops"}),
            text_column=False,
            reversible=True,
            kind="text",
            length=None,
        )
    ]
    t = BatchTransformer(
        engine,
        policy=resolve_policy(_policy("ops"), default_name="gdpr-pseudonymise"),
        mode="obfuscate",
        columns=plans,
        shape=shape,
        key=generate_key_material("key"),
        seed=None,
        locale="en_US",
    )
    token = CURRENT_TENANT.set("ta")
    try:
        with pytest.raises(TokenizerProfileRefused) as refused:
            t.transform([{"ID": 1, "SSN": SSN}])
    finally:
        CURRENT_TENANT.reset(token)
    assert refused.value.code == "tokenizer_profile_not_allowed" and tokenizer.calls == []


def test_production_needs_the_allow_list_with_a_tokenizer() -> None:
    from tests.test_posture_m5 import _prod

    problems = posture_problems(_prod(tokenizer_profiles=""))
    assert any("DPS_TOKENIZER_PROFILES" in p for p in problems)
    assert not any("DPS_TOKENIZER_PROFILES" in p for p in posture_problems(_prod()))
