"""S3 review F-12: synthesize mode never runs a method that needs a key or an outside service.

``external_tokenize`` needed no DEK in synthesize mode, so ``/synthesize`` (and L2 free-text spans)
returned the bank service's deterministic token of the *real* value as "synthetic" data — joinable
to the bank's tokenized datasets, and a tokenization oracle for a ``synthesize``-only key. A policy's
``synthesize`` methods must be key-less (refused when the policy is validated: L1 JSON, NDJSON and
documents, stored policies, L2 runs), and ``build_plan`` refuses any other in synthesize mode."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from dps.app import create_app
from dps.config import Settings
from dps.engine.findings import Finding, Locator
from dps.engine.planner import KEYLESS_METHODS, build_plan
from dps.engine.policy import MethodSpec, load_preset, resolve_policy
from dps.engine.service import PrivacyEngine
from dps.engine.taxonomy import Sensitivity
from dps.errors import CodedError

SSN = "123-45-6789"
POLICY: dict[str, Any] = {
    "preset": "synthetic-realistic",
    "synthesize": {"overrides": {"SSN": {"method": "external_tokenize", "profile": "ssn"}}},
}


class _CountingTokenizer:
    name = "counting"

    def __init__(self) -> None:
        self.calls = 0

    def tokenize(
        self, entity_type: str, values: Sequence[str], *, profile: str | None
    ) -> list[str]:
        self.calls += 1
        return [f"tok-{i}" for i, _ in enumerate(values)]

    def detokenize(
        self, entity_type: str, tokens: Sequence[str], *, profile: str | None
    ) -> list[str]:
        self.calls += 1
        return list(tokens)

    def health(self) -> bool:
        return True

    def close(self) -> None:
        return None


@pytest.mark.parametrize(
    "method", ["external_tokenize", "det_token", "fpe_digits", "hmac_pseudonym"]
)
def test_a_policy_with_a_keyed_synthesize_method_is_invalid(method: str) -> None:
    with pytest.raises(ValidationError, match="key-less"):
        resolve_policy(
            {
                "preset": "synthetic-realistic",
                "synthesize": {"overrides": {"SSN": {"method": method}}},
            },
            default_name="x",
        )
    with pytest.raises(ValidationError, match="key-less"):
        resolve_policy(
            {"preset": "gdpr-pseudonymise", "synthesize": {"default": {"method": method}}},
            default_name="x",
        )


def test_every_preset_synthesizes_with_key_less_methods() -> None:
    from dps.engine.policy import preset_names

    for name in preset_names():
        syn = load_preset(name).synthesize
        assert {syn.default.method, *(m.method for m in syn.overrides.values())} <= KEYLESS_METHODS


def test_build_plan_refuses_a_keyed_method_in_synthesize_mode() -> None:
    policy = load_preset("synthetic-realistic")
    forged = policy.model_copy(
        update={
            "synthesize": policy.synthesize.model_copy(
                update={"overrides": {"SSN": MethodSpec(method="external_tokenize")}}
            )
        }
    )  # model_copy skips validation: the planner is the backstop
    finding = Finding(
        entity_type="SSN",
        locator=Locator(path="$.ssn"),
        score=0.9,
        detectors=["pattern"],
        sensitivity=Sensitivity.CRITICAL,
    )
    with pytest.raises(CodedError) as refused:
        build_plan([finding], forged, "synthesize", fpe_enabled=False)
    assert refused.value.code == "synthesize_method_invalid" and refused.value.status == 422
    assert build_plan([finding], forged, "obfuscate", fpe_enabled=False).entries  # obfuscate: fine


def test_l1_synthesize_routes_never_call_the_tokenizer() -> None:
    settings = Settings(
        _env_file=None, env="local", auth_enabled=False, llm_provider="stub", ner_enabled=False
    )
    tokenizer = _CountingTokenizer()
    engine = PrivacyEngine(settings, tokenizer=tokenizer)  # type: ignore[arg-type]
    with TestClient(create_app(settings, engine=engine)) as client:
        plain = client.post("/api/v1/synthesize", json={"data": {"ssn": SSN}, "policy": POLICY})
        assert plain.status_code in (400, 422), plain.text
        body = json.dumps({"policy": POLICY}) + "\n" + json.dumps({"ssn": SSN}) + "\n"
        stream = client.post(
            "/api/v1/synthesize",
            content=body,
            headers={"Content-Type": "application/x-ndjson", "Accept": "application/x-ndjson"},
        )
        assert SSN not in stream.text and "tok-" not in stream.text
        doc = client.post(
            "/api/v1/documents/synthesize",
            files={"file": ("note.txt", f"Customer SSN {SSN}".encode(), "text/plain")},
            data={"request": json.dumps({"policy": POLICY})},
        )
        assert doc.status_code in (400, 422), doc.text
    assert tokenizer.calls == 0
