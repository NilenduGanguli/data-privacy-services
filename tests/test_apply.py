from __future__ import annotations

import copy
import json
from collections.abc import Iterable

import pytest

from dps.engine.detect.ensemble import Detector, DetectorConfig
from dps.engine.findings import Finding, Locator, Plan, PlanEntry, hash_value
from dps.engine.planner import build_plan
from dps.engine.policy import Policy, load_preset
from dps.engine.taxonomy import Sensitivity
from dps.engine.transform.apply import ApplyOptions, RevealHint, anchor_for, apply_plan, reveal
from dps.engine.transform.crypto import generate_key_material
from dps.engine.transform.methods import MethodError

SAMPLE = {
    "customer_id": "CUST-884120",
    "full_name": "Ananya Krishnan",
    "dob": "1988-03-14",
    "aadhaar": "2341 2341 2346",
    "email": "ananya.k@example-mail.in",
    "phone": "+91 98451 20377",
    "risk_score": 712,
    "ssn_int": 123456789,
    "account_number": 50100234567812,
    "notes": "Mail ann.k@example-mail.in about 1988-03-14 soon",
}


class RotCipher:
    def __init__(self, shift: int = 3) -> None:
        self.shift = shift

    def encrypt(self, alphabet: str, plaintext: str) -> str:
        n = len(alphabet)
        return "".join(alphabet[(alphabet.index(c) + self.shift) % n] for c in plaintext)

    def decrypt(self, alphabet: str, ciphertext: str) -> str:
        n = len(alphabet)
        return "".join(alphabet[(alphabet.index(c) - self.shift) % n] for c in ciphertext)


class FakeSynth:
    def surrogate(self, entity_type: str, original: str, locale: str, anchor: str) -> str:
        return {"PERSON_NAME": "Meera Venkatesan", "EMAIL": "meera@example.com"}.get(
            entity_type, f"<{entity_type}>"
        )


def _plan(data, policy_name: str, mode: str):  # noqa: ANN001, ANN202
    policy = load_preset(policy_name)
    findings = (
        Detector(DetectorConfig(locale="en_IN", min_score=policy.detect.min_score))
        .detect(data=data)
        .findings
    )
    return policy, build_plan(findings, policy, mode, fpe_enabled=True)


def _opts(policy, key=None, **kw):  # noqa: ANN001, ANN003, ANN202
    base = dict(
        policy=policy,
        key=key,
        surrogates=None,
        fpe_enabled=True,
        cipher_factory=lambda k, t: RotCipher(),
        token_style=policy.output.token_style,
        locale="en_IN",
    )
    base.update(kw)
    return ApplyOptions(**base)  # type: ignore[arg-type]


def test_obfuscate_then_reveal_round_trips_natural_style() -> None:
    policy, plan = _plan(SAMPLE, "gdpr-pseudonymise", "obfuscate")
    key = generate_key_material("key")
    res = apply_plan(data=SAMPLE, plan=plan, options=_opts(policy, key))
    out = res.output
    assert (
        out["risk_score"] == 712
        and out["customer_id"] != "CUST-884120"
        and out["customer_id"].startswith("CUST-")
    )
    assert out["email"].endswith("@example-mail.in") and not out["email"].startswith("ananya.k")
    # numbers stay numbers when the method's output is numeric (FPE over a 14-digit account) ...
    assert isinstance(out["account_number"], int) and out["account_number"] != 50100234567812
    # ... and a 9-digit SSN with keep_suffix=4 leaves 5 free digits (< 10**6): det_token fallback
    assert isinstance(out["ssn_int"], str) and out["ssn_int"] != "123456789"
    assert res.notes["fallback:fpe_domain_too_small"] == 1
    assert out["notes"].startswith("Mail ") and "ann.k@example-mail.in" not in out["notes"]
    assert res.methods_used["fpe_digits"] >= 2 and res.reveal_hints
    assert reveal(output=out, key=key, hints=res.reveal_hints, options=_opts(policy, key)) == SAMPLE
    assert SAMPLE["notes"].startswith("Mail ann.k")  # input untouched


def test_envelope_style_text_round_trips_without_hints() -> None:
    # (plan: a model_copy(update={"treat": <dicts>}) first, which pydantic warns about and the
    # next statement overwrote anyway)
    policy = load_preset("analytics-safe")
    policy = type(policy).model_validate(
        {
            **policy.model_dump(mode="json"),
            "treat": {
                "default_sensitive": {"method": "det_token"},
                "EMAIL": {"method": "det_token"},
            },
            "output": {"token_style": "envelope"},
        }
    )
    text = "Reach ann.k@example-mail.in or 10.20.30.40 today"
    findings = Detector(DetectorConfig()).detect(text=text).findings
    plan = build_plan(findings, policy, "obfuscate", fpe_enabled=False)
    key = generate_key_material()
    res = apply_plan(
        text=text, plan=plan, options=_opts(policy, key, fpe_enabled=False, token_style="envelope")
    )
    assert res.output.startswith("Reach [[PII:EMAIL:") and "[[PII:IP_ADDRESS:" in res.output
    assert (
        reveal(output=res.output, key=key, hints=[], options=_opts(policy, key, fpe_enabled=False))
        == text
    )


def test_synthesize_replaces_without_hints_and_keeps_shape() -> None:
    policy, plan = _plan(SAMPLE, "synthetic-realistic", "synthesize")
    res = apply_plan(data=SAMPLE, plan=plan, options=_opts(policy, None, surrogates=FakeSynth()))
    assert (
        res.output["full_name"] == "Meera Venkatesan" and res.output["email"] == "meera@example.com"
    )
    assert res.output["risk_score"] == 712 and res.reveal_hints == []
    assert "ann.k@example-mail.in" not in res.output["notes"]
    assert copy.deepcopy(SAMPLE) == SAMPLE


# --- additions beyond the plan (see docs/superpowers/build/M0-DEVIATIONS.md, Task 17) ----------
def _obfuscate(data=SAMPLE, key=None, policy_name="gdpr-pseudonymise", **kw):  # noqa: ANN001, ANN003, ANN202
    policy, plan = _plan(data, policy_name, "obfuscate")
    key = key or generate_key_material("key")
    return policy, plan, key, apply_plan(data=data, plan=plan, options=_opts(policy, key, **kw))


def test_reveal_hints_carry_no_value_derived_data() -> None:
    _, plan, key, res = _obfuscate()
    blob = json.dumps([h.model_dump(mode="json") for h in res.reveal_hints])
    for value in SAMPLE.values():
        assert hash_value(value) not in blob
    assert all(e.finding.value_hash and e.finding.value_hash not in blob for e in plan.entries)
    raw = anchor_for(SAMPLE, plan)
    assert raw.startswith("CUSTOMER_ID:") and raw not in blob and raw.split(":")[1] not in blob
    assert {h.anchor for h in res.reveal_hints} == {res.anchor} and res.anchor.startswith("a_")
    assert {h.kid for h in res.reveal_hints} == {key.kid}


def test_the_anchor_is_keyed_and_stable_per_key_and_business_key() -> None:
    key = generate_key_material("key")
    first = _obfuscate(key=key)[3]
    again = _obfuscate(data={**SAMPLE, "notes": "other"}, key=key)[3]
    other_key = _obfuscate()[3]
    assert first.anchor == again.anchor and first.output["dob"] == again.output["dob"]
    assert other_key.anchor != first.anchor


def test_int_tokens_that_would_lose_a_leading_zero_stay_strings() -> None:
    rot5 = lambda k, t: RotCipher(5)  # noqa: E731 - 5 -> 0: the 14-digit account starts with 0
    policy, _, key, res = _obfuscate(cipher_factory=rot5)
    assert res.output["account_number"] == "05655789012367"
    back = reveal(
        output=res.output,
        key=key,
        hints=res.reveal_hints,
        options=_opts(policy, key, cipher_factory=rot5),
    )
    assert back == SAMPLE and isinstance(back["account_number"], int)


def test_reveal_refuses_hints_from_another_key() -> None:
    policy, _, _, res = _obfuscate()
    stranger = generate_key_material("key")
    with pytest.raises(MethodError):
        reveal(
            output=res.output, key=stranger, hints=res.reveal_hints, options=_opts(policy, stranger)
        )


@pytest.mark.parametrize(
    "change",
    [
        {"path": "$.no_such_field"},
        {"path": "$.notes.x"},  # indexing into a string
        {"path": "not a path"},
        {"start": 40, "end": 400},
        {"start": 9, "end": 3},
        {"start": 0, "end": None},
        {"method": "teleport"},
        {"method": "hmac_pseudonym"},
    ],
)
def test_malformed_hints_are_method_errors(change: dict) -> None:
    policy, _, key, res = _obfuscate()
    span_hint = next(h for h in res.reveal_hints if h.start is not None)
    bad = span_hint.model_copy(update=change)
    with pytest.raises(MethodError):
        reveal(output=res.output, key=key, hints=[bad], options=_opts(policy, key))


def test_whole_value_hints_must_point_at_a_scalar() -> None:
    policy, _, key, res = _obfuscate(data={"customer": {"email": "ananya.k@example-mail.in"}})
    hint = res.reveal_hints[0].model_copy(update={"path": "$.customer"})
    with pytest.raises(MethodError):
        reveal(output=res.output, key=key, hints=[hint], options=_opts(policy, key))


def test_reveal_does_not_mutate_the_callers_options() -> None:
    policy, _, key, res = _obfuscate()
    options = _opts(policy, None)
    assert reveal(output=res.output, key=key, hints=res.reveal_hints, options=options) == SAMPLE
    assert options.key is None


def _envelope_policy(field_scoped: bool = False) -> Policy:
    return Policy.model_validate(
        {
            "treat": {"default_sensitive": {"method": "det_token"}},
            "output": {"token_style": "envelope"},
            "consistency": {"scope": "key", "field_scoped": field_scoped},
        }
    )


def test_foreign_envelopes_survive_with_hints_and_fail_loudly_without() -> None:
    policy = _envelope_policy()
    old_key, new_key = generate_key_material(), generate_key_material()
    first_text = "Reach ann.k@example-mail.in today"
    first_plan = build_plan(
        Detector(DetectorConfig()).detect(text=first_text).findings,
        policy,
        "obfuscate",
        fpe_enabled=False,
    )
    first = apply_plan(
        text=first_text,
        plan=first_plan,
        options=_opts(policy, old_key, fpe_enabled=False, token_style="envelope"),
    )
    text = first.output + " and 10.20.30.40"
    plan = build_plan(
        Detector(DetectorConfig()).detect(text=text).findings,
        policy,
        "obfuscate",
        fpe_enabled=False,
    )
    second = apply_plan(
        text=text,
        plan=plan,
        options=_opts(policy, new_key, fpe_enabled=False, token_style="envelope"),
    )
    opts = _opts(policy, new_key, fpe_enabled=False)
    assert (
        reveal(output=second.output, key=new_key, hints=second.reveal_hints, options=opts) == text
    )
    with pytest.raises(MethodError):
        reveal(output=second.output, key=new_key, hints=[], options=opts)


def test_field_scoped_tokens_reveal_by_hint_whatever_the_reveal_policy() -> None:
    scoped = Policy.model_validate(
        {
            **load_preset("gdpr-pseudonymise").model_dump(mode="json"),
            "consistency": {"scope": "key", "field_scoped": True},
        }
    )
    findings = Detector(DetectorConfig(locale="en_IN")).detect(data=SAMPLE).findings
    plan = build_plan(findings, scoped, "obfuscate", fpe_enabled=True)
    key = generate_key_material("key")
    res = apply_plan(data=SAMPLE, plan=plan, options=_opts(scoped, key))
    assert all(h.field_scoped for h in res.reveal_hints)
    unscoped = load_preset("gdpr-pseudonymise")
    assert (
        reveal(output=res.output, key=key, hints=res.reveal_hints, options=_opts(unscoped, key))
        == SAMPLE
    )


def test_envelope_walk_reads_field_scoped_tokens_at_their_json_path() -> None:
    policy = _envelope_policy(field_scoped=True)
    data = {"a": {"notes": "Reach ann.k@example-mail.in today"}, "b": ["ping 10.20.30.40 now"]}
    plan = build_plan(
        Detector(DetectorConfig()).detect(data=data).findings,
        policy,
        "obfuscate",
        fpe_enabled=False,
    )
    key = generate_key_material()
    res = apply_plan(
        data=data, plan=plan, options=_opts(policy, key, fpe_enabled=False, token_style="envelope")
    )
    assert "[[PII:EMAIL:" in res.output["a"]["notes"] and "[[PII:IP_ADDRESS:" in res.output["b"][0]
    assert (
        reveal(output=res.output, key=key, hints=[], options=_opts(policy, key, fpe_enabled=False))
        == data
    )


def _span_plan(
    path: str, start: int, end: int, entity: str, method: str, params: dict | None = None
) -> Plan:
    finding = Finding(
        locator=Locator(path=path, start=start, end=end),
        entity_type=entity,
        score=0.9,
        detectors=["t"],
        sensitivity=Sensitivity.HIGH,
    )
    entry = PlanEntry(
        finding=finding, method=method, params=params or {}, reason="t", reversible=True
    )
    return Plan(mode="obfuscate", record_sensitivity=Sensitivity.HIGH, entries=[entry])


def _whole_entry(path: str, entity: str, method: str, params: dict) -> PlanEntry:
    finding = Finding(
        locator=Locator(path=path),
        entity_type=entity,
        score=0.9,
        detectors=["t"],
        sensitivity=Sensitivity.HIGH,
    )
    return PlanEntry(finding=finding, method=method, params=params, reason="t", reversible=True)


@pytest.mark.parametrize("field_scoped", [False, True])
def test_all_dates_in_a_record_shift_by_the_same_days_and_reveal(field_scoped: bool) -> None:
    from datetime import date

    data = {
        "dob": "1988-03-14",
        "opened": "2019-07-01",
        "history": [{"on": "2020-01-15"}],
        "notes": "Opened on 2019-07-01 at the branch",
    }
    window = {"window_days": 365}
    entries = [
        _whole_entry("$.dob", "DATE_OF_BIRTH", "date_shift", window),
        _whole_entry("$.opened", "DATE", "date_shift", window),
        _whole_entry("$.history[0].on", "DATE", "date_shift", window),
    ]
    entries += _span_plan("$.notes", 10, 20, "DATE", "date_shift", window).entries
    plan = Plan(mode="obfuscate", record_sensitivity=Sensitivity.HIGH, entries=entries)
    policy = _envelope_policy(field_scoped=field_scoped).model_copy(
        update={"output": load_preset("gdpr-pseudonymise").output}
    )
    key = generate_key_material("key")
    res = apply_plan(data=data, plan=plan, options=_opts(policy, key))
    out = res.output
    assert res.methods_used["date_shift"] == 4
    shifts = {
        (date.fromisoformat(out["dob"]) - date.fromisoformat(data["dob"])).days,
        (date.fromisoformat(out["opened"]) - date.fromisoformat(data["opened"])).days,
        (
            date.fromisoformat(out["history"][0]["on"])
            - date.fromisoformat(data["history"][0]["on"])
        ).days,
        (date.fromisoformat(out["notes"][10:20]) - date.fromisoformat("2019-07-01")).days,
    }
    assert len(shifts) == 1 and 0 not in shifts  # one offset for the whole record
    assert out["notes"].startswith("Opened on ") and out["notes"].endswith(" at the branch")
    assert reveal(output=out, key=key, hints=res.reveal_hints, options=_opts(policy, key)) == data


def test_spans_on_numeric_leaves_are_transformed_not_skipped() -> None:
    data = {"ref": 99912345678900042}
    plan = _span_plan("$.ref", 3, 14, "ACCOUNT_NUMBER", "fpe_digits")
    policy = load_preset("gdpr-pseudonymise")
    key = generate_key_material()
    res = apply_plan(data=data, plan=plan, options=_opts(policy, key))
    assert res.output["ref"] != 99912345678900042 and str(res.output["ref"]).startswith("999")
    assert (
        reveal(output=res.output, key=key, hints=res.reveal_hints, options=_opts(policy, key))
        == data
    )


def test_plan_spans_that_do_not_fit_the_document_are_refused() -> None:
    policy = load_preset("gdpr-pseudonymise")
    with pytest.raises(MethodError):
        apply_plan(
            data={"notes": "short"},
            plan=_span_plan("$.notes", 2, 40, "EMAIL", "det_token"),
            options=_opts(policy, generate_key_material()),
        )


def test_envelopes_are_only_made_for_names_the_parser_can_read() -> None:
    policy = _envelope_policy()
    key = generate_key_material()
    plan = _span_plan("$", 6, 9, "Odd-Type", "det_token")
    res = apply_plan(
        text="value abc here", plan=plan, options=_opts(policy, key, token_style="envelope")
    )
    assert "[[PII:" not in res.output and len(res.reveal_hints) == 1
    assert (
        reveal(output=res.output, key=key, hints=res.reveal_hints, options=_opts(policy, key))
        == "value abc here"
    )


def test_reveal_hints_validate_and_never_echo_their_input() -> None:
    with pytest.raises(ValueError) as info:
        RevealHint.model_validate(
            {
                "path": "$.x",
                "entity_type": "SSN",
                "method": "det_token",
                "original_type": "123-45-6789",
            }
        )
    assert "123-45-6789" not in str(info.value)


def test_whole_value_entries_must_point_at_a_scalar() -> None:
    plan = _span_plan("$.customer", 0, 0, "EMAIL", "det_token")
    entry = plan.entries[0]
    whole = entry.model_copy(
        update={"finding": entry.finding.model_copy(update={"locator": Locator(path="$.customer")})}
    )
    plan = plan.model_copy(update={"entries": [whole]})
    with pytest.raises(MethodError):
        apply_plan(
            data={"customer": {"email": "ann@example.com"}},
            plan=plan,
            options=_opts(load_preset("pci-dss"), generate_key_material()),
        )


class DeclaringSynth(FakeSynth):
    def __init__(self) -> None:
        self.declared: list[tuple[str, list[tuple[str, str]]]] = []

    def declare_record(self, anchor: str, originals: Iterable[tuple[str, str]]) -> None:
        self.declared.append((anchor, list(originals)))


def test_synthesis_declares_the_record_before_any_field() -> None:
    """The persona must know the record's own names (A-prepare review r1, B1)."""
    policy, plan = _plan(SAMPLE, "synthetic-realistic", "synthesize")
    synth = DeclaringSynth()
    res = apply_plan(data=SAMPLE, plan=plan, options=_opts(policy, None, surrogates=synth))
    assert len(synth.declared) == 1 and synth.declared[0][0] == res.anchor
    declared = synth.declared[0][1]
    assert len(declared) == len(plan.entries) and ("PERSON_NAME", "Ananya Krishnan") in declared
    assert ("EMAIL", "ananya.k@example-mail.in") in declared
