from __future__ import annotations

import json
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from dps.config import Settings
from dps.engine.service import (
    EngineError,
    Payload,
    PrivacyEngine,
    RequestOptions,
    VerificationFailed,
)
from dps.engine.transform.crypto import unwrap_dek
from dps.llm.gateway import LlmGateway, LlmResult

SAMPLE = {
    "customer_id": "CUST-884120",
    "full_name": "Ananya Krishnan",
    "dob": "1988-03-14",
    "aadhaar": "2341 2341 2346",
    "email": "ananya.k@example-mail.in",
    "phone": "+91 98451 20377",
    "risk_score": 712,
    "notes": "Mail ann.k@example-mail.in about 1988-03-14 soon",
}


class RotCipher:
    def encrypt(self, alphabet: str, plaintext: str) -> str:
        return "".join(alphabet[(alphabet.index(c) + 3) % len(alphabet)] for c in plaintext)

    def decrypt(self, alphabet: str, ciphertext: str) -> str:
        return "".join(alphabet[(alphabet.index(c) - 3) % len(alphabet)] for c in ciphertext)


@pytest.fixture
def engine() -> PrivacyEngine:
    s = Settings(
        _env_file=None,
        llm_provider="stub",
        fpe_enabled=True,
        ner_enabled=False,
        default_locale="en_IN",
    )
    return PrivacyEngine(s, gateway=LlmGateway(s), cipher_factory=lambda k, t: RotCipher())


def test_detect_and_plan(engine: PrivacyEngine) -> None:
    det = engine.detect(Payload(data=SAMPLE), None, RequestOptions())
    assert det.record_sensitivity == "CRITICAL" and det.report.findings_total >= 6
    plan = engine.plan(Payload(data=SAMPLE), "pci-dss", RequestOptions(), mode="obfuscate")
    assert any(r["entity_type"] == "AADHAAR" and r["method"] == "det_token" for r in plan.plan)


def test_obfuscate_reveal_round_trip_and_consistency(engine: PrivacyEngine) -> None:
    first = engine.obfuscate(Payload(data=SAMPLE), None, RequestOptions())
    assert first.key_material and first.key_material.has_dek() and first.result["risk_score"] == 712
    assert first.result["email"] != SAMPLE["email"] and first.report.verification.passed  # type: ignore[union-attr]
    back = engine.reveal(
        Payload(data=first.result),
        first.key_material.model_dump(mode="json"),
        [h.model_dump(mode="json") for h in first.reveal_hints],
    )
    assert back.result == SAMPLE and back.revealed >= 6
    again = engine.obfuscate(
        Payload(data=SAMPLE),
        None,
        RequestOptions(
            consistency={"scope": "key", "key_material": first.key_material.model_dump(mode="json")}
        ),
    )
    assert again.result["aadhaar"] == first.result["aadhaar"]
    assert again.key_material.kid == first.key_material.kid  # type: ignore[union-attr]


def test_wrap_key_with_rsa(engine: PrivacyEngine) -> None:
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = (
        private.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )
    out = engine.obfuscate(
        Payload(data=SAMPLE),
        None,
        RequestOptions(wrap_key={"type": "rsa-oaep-256", "public_key_pem": pem}),
    )
    assert out.key_material and out.key_material.dek is None and out.key_material.wrapped_dek
    private_pem = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    assert len(unwrap_dek(out.key_material.wrapped_dek, private_pem)) == 32


async def test_synthesize_json_and_text(engine: PrivacyEngine) -> None:
    out = await engine.synthesize(Payload(data=SAMPLE), "synthetic-realistic", RequestOptions())
    assert out.result["full_name"] != SAMPLE["full_name"] and out.result["risk_score"] == 712
    assert out.synthesis_seed is not None and out.reveal_hints == []
    assert out.report.verification.passed  # type: ignore[union-attr]
    assert "ananya" not in str(out.result).lower()
    text = await engine.synthesize(
        Payload(text="Call Ann at ann.k@example-mail.in"), "synthetic-realistic", RequestOptions()
    )
    assert "example-mail" not in text.result and text.report.llm["raw_pii_sent"] == 0
    strict = await engine.synthesize(Payload(data=SAMPLE), "synthetic-strict", RequestOptions())
    assert strict.synthesis_seed is None  # scope=request: no linkage echoed


def test_errors(engine: PrivacyEngine, monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(EngineError):
        engine.detect(Payload(data=SAMPLE), "no-such-preset", RequestOptions())
    with pytest.raises(ValueError):
        Payload(data={"a": 1}, text="both")
    import dps.engine.service as svc
    from dps.engine.verify import Verification

    monkeypatch.setattr(
        svc,
        "verify_output",
        lambda *a, **k: Verification(
            passed=False, checked=1, hits=[{"path": "$.x", "entity_type": "SSN"}]
        ),
    )
    with pytest.raises(VerificationFailed):
        engine.obfuscate(Payload(data=SAMPLE), None, RequestOptions())


# --- added beyond the plan ---------------------------------------------------------------------


def test_text_obfuscation_reveals_with_and_without_hints(engine: PrivacyEngine) -> None:
    text = "Ring +91 98451 20377 or mail ananya.k@example-mail.in, Aadhaar 2341 2341 2346."
    natural = engine.obfuscate(Payload(text=text), None, RequestOptions())
    assert "ananya" not in natural.result and natural.reveal_hints
    km = natural.key_material.model_dump(mode="json")  # type: ignore[union-attr]
    hints = [h.model_dump(mode="json") for h in natural.reveal_hints]
    assert engine.reveal(Payload(text=natural.result), km, hints).result == text
    enveloped = engine.obfuscate(
        Payload(text="Aadhaar 2341 2341 2346 on file"),
        "pci-dss",
        RequestOptions(token_style="envelope"),
    )
    assert "[[PII:AADHAAR:" in enveloped.result
    km2 = enveloped.key_material.model_dump(mode="json")  # type: ignore[union-attr]
    assert engine.reveal(Payload(text=enveloped.result), km2, []).result == (
        "Aadhaar 2341 2341 2346 on file"
    )


def test_bad_keys_hints_and_wraps_are_engine_errors_that_echo_nothing(
    engine: PrivacyEngine,
) -> None:
    first = engine.obfuscate(Payload(data=SAMPLE), None, RequestOptions())
    km = first.key_material.model_dump(mode="json")  # type: ignore[union-attr]
    dek = km["dek"]
    hints = [h.model_dump(mode="json") for h in first.reveal_hints]
    other = engine.obfuscate(Payload(data=SAMPLE), None, RequestOptions())
    wrong = other.key_material.model_dump(mode="json")  # type: ignore[union-attr]
    cases: list[Any] = [
        lambda: engine.reveal(Payload(data=first.result), {**km, "dek": dek[:-4]}, hints),
        lambda: engine.reveal(Payload(data=first.result), {**km, "alg": "rot13"}, hints),
        lambda: engine.reveal(Payload(data=first.result), wrong, hints),
        lambda: engine.reveal(Payload(data=first.result), km, [{"path": 7, "method": dek}]),
        lambda: engine.reveal(
            Payload(data=first.result), {**km, "dek": None, "wrapped_dek": "eA"}, hints
        ),
        lambda: engine.obfuscate(
            Payload(data=SAMPLE),
            None,
            RequestOptions(consistency={"key_material": {**km, "dek": dek[:-4]}}),
        ),
        lambda: engine.obfuscate(
            Payload(data=SAMPLE), None, RequestOptions(wrap_key={"public_key_pem": "nope"})
        ),
        lambda: engine.obfuscate(
            Payload(data=SAMPLE),
            None,
            RequestOptions(wrap_key={"type": "rsa-pkcs1", "public_key_pem": "x"}),
        ),
        lambda: engine.plan(Payload(data=SAMPLE), None, RequestOptions(), mode="reveal"),
        lambda: engine.detect(
            Payload(data=SAMPLE), {"treat": {"SSN": "teleport"}}, RequestOptions()
        ),
    ]
    for case in cases:
        with pytest.raises(EngineError) as err:
            case()
        assert dek not in str(err.value) and dek[:-4] not in str(err.value)
        assert "Ananya" not in str(err.value) and "2341" not in str(err.value)


def test_request_models_never_echo_their_input() -> None:
    with pytest.raises(ValueError) as err:
        Payload(data={"ssn": "123-45-6789"}, text="Ananya Krishnan")
    assert "123-45-6789" not in str(err.value) and "Ananya" not in str(err.value)
    with pytest.raises(ValueError) as err2:
        RequestOptions(consistency={"scope": "forever", "key_material": {"dek": "SECRETDEK"}})
    assert "SECRETDEK" not in str(err2.value) and "forever" not in str(err2.value)
    with pytest.raises(ValueError):
        RequestOptions(token_style="rot13")


async def test_a_caller_held_seed_reproduces_synthesis(engine: PrivacyEngine) -> None:
    first = await engine.synthesize(Payload(data=SAMPLE), "synthetic-realistic", RequestOptions())
    seed = first.synthesis_seed.model_dump(mode="json")  # type: ignore[union-attr]
    same = await engine.synthesize(
        Payload(data=SAMPLE),
        "synthetic-realistic",
        RequestOptions(consistency={"synthesis_seed": seed}),
    )
    assert same.result == first.result
    fresh = await engine.synthesize(Payload(data=SAMPLE), "synthetic-realistic", RequestOptions())
    assert fresh.result["full_name"] != first.result["full_name"] or (
        fresh.result["email"] != first.result["email"]
    )
    with pytest.raises(EngineError) as err:
        await engine.synthesize(
            Payload(data=SAMPLE),
            "synthetic-realistic",
            RequestOptions(consistency={"synthesis_seed": {**seed, "seed": seed["seed"][:-2]}}),
        )
    assert seed["seed"][:-2] not in str(err.value)


async def test_a_caller_held_seed_maps_each_value_the_same_in_any_request(
    engine: PrivacyEngine,
) -> None:
    """Spec §5.6/P3: same original → same fake whatever else a request holds, in any order."""
    policy = {
        "preset": "synthetic-realistic",
        "detect": {"declared_fields": [{"path": "$[*].ref", "entity_type": "BRANCH_REF"}]},
    }
    records = [{"ref": f"{i:03d}"} for i in range(300)]  # dense in a 1,000-value space
    first = await engine.synthesize(Payload(data=records), policy, RequestOptions())
    seed = first.synthesis_seed.model_dump(mode="json")  # type: ignore[union-attr]
    options = RequestOptions(consistency={"synthesis_seed": seed})
    mapping = {r["ref"]: o["ref"] for r, o in zip(records, first.result, strict=True)}
    assert len(set(mapping.values())) == len(records)  # distinct stays distinct (joins survive)
    assert all(original != fake for original, fake in mapping.items())
    backward = await engine.synthesize(Payload(data=records[::-1]), policy, options)
    assert {r["ref"]: o["ref"] for r, o in zip(records[::-1], backward.result, strict=True)} == (
        mapping
    )
    for record in records[-5:]:
        alone = await engine.synthesize(Payload(data=[record]), policy, options)
        assert alone.result[0]["ref"] == mapping[record["ref"]]


async def test_strict_synthesis_replaces_values_in_any_script(engine: PrivacyEngine) -> None:
    fields = {"maiden": "MOTHER_MAIDEN_NAME", "nid": "NATIONAL_ID", "emp": "EMPLOYEE_ID"}
    policy = {
        "preset": "synthetic-strict",
        "detect": {
            "declared_fields": [{"path": f"$.{k}", "entity_type": v} for k, v in fields.items()]
        },
    }
    doc = {"maiden": "राम", "nid": "ПАСПОРТ", "emp": "Müller"}
    out = await engine.synthesize(Payload(data=doc), policy, RequestOptions())
    assert out.report.verification.passed  # type: ignore[union-attr]
    for key, value in doc.items():
        assert out.result[key] != value and value not in out.result[key], key


async def test_text_synthesis_follows_the_plan(engine: PrivacyEngine) -> None:
    text = "Mail ann.k@example-mail.in by 2026-01-05 via https://portal.example-bank.in/x"
    realistic = await engine.synthesize(Payload(text=text), "synthetic-realistic", RequestOptions())
    assert (
        "2026-01-05" in realistic.result and "https://portal.example-bank.in/x" in realistic.result
    )
    assert "example-mail" not in realistic.result
    assert realistic.report.methods_used == {"surrogate": 1, "keep": 2}
    assert realistic.report.verification.passed  # type: ignore[union-attr]
    strict = await engine.synthesize(Payload(text=text), "synthetic-strict", RequestOptions())
    assert "2026-01-05" not in strict.result and "[REDACTED]" in strict.result
    whole = await engine.synthesize(
        Payload(text="ann.k@example-mail.in"), "synthetic-realistic", RequestOptions()
    )
    assert "example-mail" not in whole.result and "@example." in whole.result


class RecordingGateway(LlmGateway):
    def __init__(self, settings: Settings, reply: str | None = None) -> None:
        super().__init__(settings)
        self.reply = reply
        self.sent: list[str] = []

    async def complete(self, messages, **kw):  # noqa: ANN001, ANN003, ANN201
        self.sent.extend(m["content"] for m in messages)
        user = messages[-1]["content"]
        return LlmResult(text=self.reply or user, provider="stub", model="stub")


async def test_llm_rewrite_needs_both_switches_and_sees_placeholders_only() -> None:
    text = "Ananya at ann.k@example-mail.in asked about card 4012 8888 8888 1881 on 2026-01-05."
    on = Settings(_env_file=None, llm_provider="stub", ner_enabled=False, llm_text_rewrite=True)
    gw = RecordingGateway(on)
    eng = PrivacyEngine(on, gateway=gw)
    out = await eng.synthesize(
        Payload(text=text), "synthetic-realistic", RequestOptions(llm_rewrite=True)
    )
    assert out.report.llm["calls"] == 1 and out.report.llm["used"] is True
    assert out.report.llm["raw_pii_sent"] == 0
    sent = " ".join(gw.sent)
    assert "example-mail" not in sent and "4012" not in sent and "2026-01-05" not in sent
    assert "{EMAIL_1}" in sent and "{CARD_PAN_1}" in sent
    assert "example-mail" not in out.result and "4012 8888" not in out.result
    # the policy switch alone, or the request switch alone, is not enough
    off = Settings(_env_file=None, llm_provider="stub", ner_enabled=False)
    gw_off = RecordingGateway(off)
    no = await PrivacyEngine(off, gateway=gw_off).synthesize(
        Payload(text=text), "synthetic-realistic", RequestOptions(llm_rewrite=True)
    )
    assert no.report.llm["calls"] == 0 and gw_off.sent == []
    silent = await eng.synthesize(Payload(text=text), "synthetic-realistic", RequestOptions())
    assert silent.report.llm["calls"] == 0  # the preset leaves llm_rewrite_text off


async def test_an_inventive_rewrite_is_discarded(engine: PrivacyEngine) -> None:
    on = Settings(_env_file=None, llm_provider="stub", ner_enabled=False, llm_text_rewrite=True)
    gw = RecordingGateway(on, reply="{EMAIL_1} wrote to bob.lee@corp-mail.com")
    out = await PrivacyEngine(on, gateway=gw).synthesize(
        Payload(text="Mail ann.k@example-mail.in today"),
        "synthetic-realistic",
        RequestOptions(llm_rewrite=True),
    )
    assert "corp-mail" not in out.result and out.report.llm["used"] is False
    assert out.report.llm["fallback"] == "llm_introduced_entities"
    assert out.report.fallbacks.get("llm:llm_introduced_entities") == 1


def test_locale_precedence(engine: PrivacyEngine) -> None:
    inline = {"treat": {"default_sensitive": {"method": "det_token"}}}
    # an inline policy without a locale takes the service default (en_IN): an Indian number in
    # national format (trunk prefix 0) is a phone only under the IN region
    det = engine.detect(Payload(data={"note": "098451 20377"}), inline, RequestOptions())
    assert [f.entity_type for f in det.findings] == ["PHONE"]
    us = engine.detect(
        Payload(data={"note": "098451 20377"}), inline, RequestOptions(locale="en_US")
    )
    assert "PHONE" not in [f.entity_type for f in us.findings]
    # a policy that names its locale keeps it (every preset does: en_US)
    preset = engine.detect(Payload(data={"note": "098451 20377"}), None, RequestOptions())
    assert "PHONE" not in [f.entity_type for f in preset.findings]


def test_warn_policies_return_output_and_report_the_failure(
    engine: PrivacyEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    import dps.engine.service as svc
    from dps.engine.verify import Verification

    monkeypatch.setattr(
        svc,
        "verify_output",
        lambda *a, **k: Verification(
            passed=False, checked=1, hits=[{"path": "$.x", "entity_type": "SSN"}]
        ),
    )
    out = engine.obfuscate(
        Payload(data=SAMPLE),
        {"preset": "gdpr-pseudonymise", "verify": {"leakage_check": "warn"}},
        RequestOptions(),
    )
    assert out.report.verification is not None and out.report.verification.passed is False


async def test_a_synthesize_method_that_needs_a_key_is_an_engine_error(
    engine: PrivacyEngine,
) -> None:
    policy = {"preset": "synthetic-realistic", "synthesize": {"default": {"method": "det_token"}}}
    with pytest.raises(EngineError):
        await engine.synthesize(Payload(data=SAMPLE), policy, RequestOptions())
    with pytest.raises(EngineError):
        await engine.synthesize(
            Payload(text="mail ann.k@example-mail.in"), policy, RequestOptions()
        )


def test_reports_carry_no_values(engine: PrivacyEngine) -> None:
    out = engine.obfuscate(Payload(data=SAMPLE), None, RequestOptions())
    dumped = json.dumps(out.report.model_dump(mode="json")) + json.dumps(out.plan)
    for value in ("Ananya", "ananya", "2341", "98451", "example-mail", "884120", "1988"):
        assert value not in dumped
    hints = json.dumps([h.model_dump(mode="json") for h in out.reveal_hints])
    for value in ("Ananya", "2341 2341", "98451", "example-mail", "884120"):
        assert value not in hints
