"""Task 11: persona values, field formatting and tokens of persona values."""

from __future__ import annotations

import functools
import random
import re
from datetime import date

import pytest
from stdnum import verhoeff

from dps.engine.detect.checksums import luhn_valid
from dps.engine.policy import load_preset
from dps.engine.service import Payload, PrivacyEngine
from dps.engine.synth.personas import SUPPORTED_LOCALES
from dps.engine.synth.surrogates import generate_synthesis_seed, seed_bytes
from dps.engine.transform.crypto import generate_key_material
from dps.layers.scanner.classify import compile_html_pattern
from dps.layers.scanner.models import FieldFacts, OptionFacts
from dps.layers.scanner.personas_fill import (
    ATTR_ENTITY,
    ENTITY_ATTR,
    PERSONA_ATTRS,
    TOKEN_ATTR,
    FillAction,
    LeakageError,
    apply_format,
    choose_format,
    choose_option,
    persona_values,
    plan_field,
    prepare_fill,
    preview_entities,
    sample_for_pattern,
    tokens_for,
)
from tests.scanner.helpers import classification as C  # noqa: N812
from tests.scanner.helpers import field_facts as F  # noqa: N812

SEED = generate_synthesis_seed()
POLICY = load_preset("gdpr-pseudonymise")


def _gender_select(key: str = "0:8", **kw: object) -> FieldFacts:
    return F(
        key=key,
        field_no=8,
        tag="select",
        input_type="select-one",
        autocomplete="sex",
        options=[
            OptionFacts(value="", text="Choose…", disabled=False, selected=True),
            OptionFacts(value="Female", text="Female", disabled=False, selected=False),
            OptionFacts(value="Male", text="Male", disabled=False, selected=False),
        ],
        **kw,
    )


def test_persona_values_are_deterministic_coherent_and_rule_valid(
    scan_engine: PrivacyEngine,
) -> None:
    p1 = persona_values(scan_engine, seed=SEED, persona_no=1, locale="en_IN", policy=POLICY)
    assert p1 == persona_values(scan_engine, seed=SEED, persona_no=1, locale="en_IN", policy=POLICY)
    p2 = persona_values(scan_engine, seed=SEED, persona_no=2, locale="en_IN", policy=POLICY)
    assert p1["name"] != p2["name"] and p1["aadhaar"] != p2["aadhaar"]
    assert p1["card_number"] != p2["card_number"] and p1["pan"] != p2["pan"]
    # M0 draws Aadhaar numbers UIDAI never issues (leading 1) with a valid Verhoeff check digit
    assert re.fullmatch(r"\d{12}", p1["aadhaar"]) and verhoeff.is_valid(p1["aadhaar"])
    assert luhn_valid(p1["card_number"]) and p1["card_number"].startswith("411111")
    assert re.fullmatch(r"[A-Z]{5}\d{4}[A-Z]", p1["pan"]) and p1["ssn"].startswith("9")
    assert re.fullmatch(r"[A-Z]{4}0[A-Z0-9]{6}", p1["ifsc"])
    assert p1["email"].split("@")[1] in {"example.com", "example.net", "example.org"}
    assert p1["given_name"] == p1["name"].split()[0] and p1["country"] == "India"
    assert date.fromisoformat(p1["dob"]) and p1["credential"].startswith("Dps!")
    assert p1["phone"].startswith("+91") and re.fullmatch(r"\d{6}", p1["otp"])
    assert set(PERSONA_ATTRS) <= set(p1)


def test_personas_differ_by_seed_and_follow_the_locale(scan_engine: PrivacyEngine) -> None:
    other = generate_synthesis_seed()
    a = persona_values(scan_engine, seed=SEED, persona_no=1, locale="en_US", policy=POLICY)
    b = persona_values(scan_engine, seed=other, persona_no=1, locale="en_US", policy=POLICY)
    assert a["ssn"] != b["ssn"] and a["name"] != b["name"]
    assert a["country"] == "United States" and a["phone"].startswith("+1")
    gb = persona_values(scan_engine, seed=SEED, persona_no=1, locale="en_GB", policy=POLICY)
    assert gb["phone"].startswith("+44") and gb["country"] == "United Kingdom"


def test_every_mapped_attribute_exists_in_a_persona(scan_engine: PrivacyEngine) -> None:
    for locale in SUPPORTED_LOCALES:
        p = persona_values(scan_engine, seed=SEED, persona_no=3, locale=locale, policy=POLICY)
        for attr in [*ENTITY_ATTR.values(), *TOKEN_ATTR.values()]:
            assert p.get(attr), (locale, attr)
    assert set(ENTITY_ATTR.values()) | set(TOKEN_ATTR.values()) <= set(ATTR_ENTITY)


def test_preview_entities_per_locale() -> None:
    assert preview_entities("en_IN") == [
        ("Name", "name"),
        ("Date of birth", "dob"),
        ("Aadhaar", "aadhaar"),
        ("PAN", "pan"),
        ("Mobile", "phone"),
    ]
    assert [a for _, a in preview_entities("en_US")] == ["name", "dob", "ssn", "phone", "email"]
    assert [a for _, a in preview_entities("en_GB")] == ["name", "dob", "nino", "phone", "email"]
    assert [a for _, a in preview_entities("en_CA")] == ["name", "dob", "sin", "phone", "email"]
    assert [a for _, a in preview_entities("es_MX")] == ["name", "dob", "curp", "rfc", "email"]


def test_formats_follow_the_field_rules() -> None:
    aad = F(pattern=r"\d{4} \d{4} \d{4}")
    assert choose_format("aadhaar", "917322064418", aad, "en_IN") == "shape:#### #### ####"
    assert apply_format("shape:#### #### ####", "917322064418") == "9173 2206 4418"
    dob = F(placeholder="DD/MM/YYYY", pattern=r"\d{2}/\d{2}/\d{4}")
    assert choose_format("dob", "1988-07-29", dob, "en_IN") == "date:%d/%m/%Y"
    assert choose_format("dob", "1988-07-29", F(input_type="date"), "en_US") == "date:%Y-%m-%d"
    assert choose_format("dob", "1988-07-29", F(), "en_US") == "date:%m/%d/%Y"
    assert (
        choose_format("phone", "+91 81234 56789", F(pattern=r"[6-9]\d{9}"), "en_IN") == "national"
    )
    assert apply_format("national", "+91 81234 56789") == "8123456789"
    assert choose_format("card_expiry", "04/29", F(input_type="month"), "en_IN") == "expiry:%Y-%m"
    assert apply_format("expiry:%Y-%m", "04/29") == "2029-04"
    assert choose_format("postal_code", "560001", F(input_type="number"), "en_IN") == "as_is"
    assert choose_format("aadhaar", "917322064418", F(maxlength=12), "en_IN") == "as_is"
    assert apply_format("digits", "+1 415-555-0132") == "14155550132"
    assert apply_format("shape:###", "12") is None and apply_format("date:%Y", "nope") is None
    with pytest.raises(ValueError):
        apply_format("bogus", "x")


def test_a_hostile_pattern_never_decides_a_format() -> None:
    hostile = F(pattern=r"\d*\d*\d*\d*\d*\d*\d*\d*\d*x")
    assert compile_html_pattern(hostile.pattern) is None
    assert choose_format("aadhaar", "917322064418", hostile, "en_IN") == "as_is"


def test_sample_for_pattern_and_unsupported_patterns() -> None:
    rng = random.Random(7)
    assert re.fullmatch(r"APP-\d{4}-\d{4}", sample_for_pattern(r"APP-\d{4}-\d{4}", rng) or "")
    assert re.fullmatch(
        r"[A-Z]{4}0[A-Z0-9]{6}", sample_for_pattern("[A-Z]{4}0[A-Z0-9]{6}", rng) or ""
    )
    assert re.fullmatch(r"EMP-\d{6}", sample_for_pattern(r"EMP-\d{6}", rng) or "")
    assert re.fullmatch(r"\+?\d{2,4}", sample_for_pattern(r"\+?\d{2,4}", rng) or "")
    assert sample_for_pattern("(?=x)y|z", rng) is None and sample_for_pattern(None, rng) is None
    assert sample_for_pattern("[^a]", rng) is None


def test_choose_option_matches_persona_and_skips_placeholders() -> None:
    f = _gender_select()
    assert choose_option(f, "female", random.Random(1)).value == "Female"
    picks = {choose_option(f, None, random.Random(s)).value for s in range(20)}
    assert picks <= {"Female", "Male"}
    assert choose_option(f, None, random.Random(3)) == choose_option(f, None, random.Random(3))
    empty = F(
        tag="select",
        input_type="select-one",
        options=[
            OptionFacts(value="", text="Select", disabled=False, selected=True),
            OptionFacts(value="x", text="Gone", disabled=True, selected=False),
        ],
    )
    assert choose_option(empty, None, random.Random(1)) is None


def test_plan_field_never_targets_files_hidden_readonly() -> None:
    assert plan_field(F(input_type="file"), C(None, None)).fill_status == "skipped_file"
    assert (
        plan_field(F(input_type="hidden", visible=False), C(None, None)).fill_status
        == "skipped_hidden"
    )
    assert plan_field(F(visible=False), C("EMAIL", "MEDIUM")).fill_status == "skipped_hidden"
    assert plan_field(F(readonly=True), C("EMAIL", "MEDIUM")).fill_status == "skipped_readonly"
    assert plan_field(F(disabled=True), C("EMAIL", "MEDIUM")).kind == "skip"
    assert plan_field(F(input_type="password"), C("PASSWORD", "CRITICAL")).kind == "credential"
    otp = plan_field(F(autocomplete="one-time-code"), C("PASSWORD", "CRITICAL"))
    assert (otp.kind, otp.persona_attr) == ("credential", "otp")
    assert plan_field(F(input_type="checkbox"), C(None, None)).fill_status == "not_filled"
    assert (
        plan_field(F(input_type="checkbox", required=True), C(None, None)).fill_status == "neutral"
    )
    p = plan_field(F(autocomplete="given-name"), C("PERSON_NAME", "MEDIUM"))
    assert (p.kind, p.persona_attr, p.obfuscate) == ("text", "given_name", True)
    low = plan_field(F(), C("POSTAL_CODE", "LOW"))
    assert (low.kind, low.persona_attr, low.obfuscate) == ("text", "postal_code", False)
    assert plan_field(F(input_type="date"), C("DATE_OF_BIRTH", "MEDIUM")).kind == "date"
    assert plan_field(F(), C(None, None)).kind == "neutral_text"
    sel = plan_field(_gender_select(), C("GENDER", "LOW"))
    assert (sel.kind, sel.fill_status) == ("select", "filled")
    assert (
        plan_field(F(tag="select", input_type="select-one"), C(None, None)).fill_status == "neutral"
    )


def test_tokens_of_persona_values_keep_shape_and_reveal_to_the_persona(
    scan_engine: PrivacyEngine,
) -> None:
    key = generate_key_material("key")
    items = {"0:3": ("AADHAAR", "9173 2206 4418"), "0:6": ("EMAIL", "meera.venkatesan@example.com")}
    res = tokens_for(scan_engine, key=key, policy=POLICY, locale="en_IN", persona_no=1, items=items)
    assert res.tokens["0:3"] != "9173 2206 4418" and re.fullmatch(
        r"\d{4} \d{4} \d{4}", res.tokens["0:3"]
    )
    assert res.tokens["0:6"].endswith("@example.com") and res.methods["0:3"] == "fpe_digits"
    revealed = scan_engine.reveal(
        Payload(data=res.document),
        key.model_dump(mode="json"),
        [h.model_dump() for h in res.reveal_hints],
    )
    assert revealed.result == {"f0": "9173 2206 4418", "f1": "meera.venkatesan@example.com"}


def test_det_token_fallback_without_fpe(det_engine: PrivacyEngine) -> None:
    res = tokens_for(
        det_engine,
        key=generate_key_material("key"),
        policy=POLICY,
        locale="en_IN",
        persona_no=1,
        items={"0:3": ("AADHAAR", "9173 2206 4418")},
    )
    assert res.methods["0:3"] == "det_token" and not re.fullmatch(
        r"\d{4} \d{4} \d{4}", res.tokens["0:3"]
    )


def test_tokens_refuse_to_return_a_value_that_survives(
    monkeypatch: pytest.MonkeyPatch, scan_engine: PrivacyEngine
) -> None:
    import dps.layers.scanner.personas_fill as pf

    def leaky(*, plan: object, options: object, data: dict[str, str]) -> object:
        class R:
            output = dict(data)
            reveal_hints: list[object] = []
            methods_used: dict[str, int] = {}

        return R()

    monkeypatch.setattr(pf, "apply_plan", leaky)
    with pytest.raises(LeakageError) as err:
        tokens_for(
            scan_engine,
            key=generate_key_material("key"),
            policy=POLICY,
            locale="en_IN",
            persona_no=1,
            items={"0:3": ("AADHAAR", "9173 2206 4418")},
        )
    assert str(err.value) == "leakage"


def test_prepare_fill_modes(scan_engine: PrivacyEngine) -> None:
    fields = [
        F(key="0:1", field_no=1, label="Full name", autocomplete="name"),
        F(key="0:3", field_no=3, label="Aadhaar number", pattern=r"\d{4} \d{4} \d{4}"),
        _gender_select(),
    ]
    classes = {
        "0:1": C("PERSON_NAME", "MEDIUM"),
        "0:3": C("AADHAAR", "CRITICAL"),
        "0:8": C("GENDER", "LOW"),
    }
    plans = {f.key: plan_field(f, classes[f.key]) for f in fields}
    persona = persona_values(scan_engine, seed=SEED, persona_no=1, locale="en_IN", policy=POLICY)
    syn = prepare_fill(
        fields,
        classes,
        plans,
        persona=persona,
        persona_no=1,
        seed=seed_bytes(SEED),
        locale="en_IN",
        mode="synthesize",
        tokens=None,
    )
    assert syn.shown["0:1"] == persona["name"]
    assert syn.shown["0:3"] == apply_format("shape:#### #### ####", persona["aadhaar"])
    assert syn.shown["0:8"] == persona["gender"].title()
    assert set(syn.needles) == {syn.shown["0:1"], syn.shown["0:3"]}
    assert syn.formats["0:3"] == "shape:#### #### ####" and syn.methods["0:1"] == "surrogate"
    acts = {a.key: a for a in syn.actions}
    assert (
        acts["0:8"].option_value == persona["gender"].title()
        and acts["0:3"].value == syn.shown["0:3"]
    )
    key = generate_key_material("key")
    obf = prepare_fill(
        fields,
        classes,
        plans,
        persona=persona,
        persona_no=1,
        seed=seed_bytes(SEED),
        locale="en_IN",
        mode="obfuscate",
        tokens=functools.partial(
            tokens_for, scan_engine, key=key, policy=POLICY, locale="en_IN", persona_no=1
        ),
    )
    assert obf.shown["0:3"] != syn.shown["0:3"] and obf.methods["0:3"] == "fpe_digits"
    assert obf.shown["0:8"] == syn.shown["0:8"] and obf.methods["0:8"] == "keep"
    assert set(obf.needles) == {obf.shown["0:1"], obf.shown["0:3"]}
    assert {a.key: a.value for a in obf.actions}["0:3"] == obf.shown["0:3"]


def test_prepare_fill_neutral_credentials_radios_and_skips(scan_engine: PrivacyEngine) -> None:
    fields = [
        F(key="0:1", field_no=1, input_type="password"),
        F(key="0:2", field_no=2, label="Reference", pattern=r"APP-\d{4}"),
        F(key="0:3", field_no=3, tag="textarea", input_type="textarea", maxlength=5),
        F(key="0:4", field_no=4, input_type="radio", name="g", label="Female"),
        F(key="0:5", field_no=5, input_type="radio", name="g", label="Male"),
        F(key="0:6", field_no=6, input_type="checkbox", required=True),
        F(key="0:7", field_no=7, input_type="checkbox"),
        F(key="0:8", field_no=8, input_type="file"),
        F(key="0:9", field_no=9, input_type="number"),
        F(
            key="0:10",
            field_no=10,
            tag="select",
            input_type="select-one",
            options=[
                OptionFacts(value="", text="Choose", disabled=False, selected=True),
                OptionFacts(value="a", text="Acct XXXX 1234", disabled=False, selected=False),
            ],
        ),
    ]
    classes = {f.key: C(None, None) for f in fields}
    classes["0:1"] = C("PASSWORD", "CRITICAL")
    classes["0:4"] = classes["0:5"] = C("GENDER", "LOW")
    plans = {f.key: plan_field(f, classes[f.key]) for f in fields}
    persona = persona_values(scan_engine, seed=SEED, persona_no=2, locale="en_IN", policy=POLICY)
    fill = prepare_fill(
        fields,
        classes,
        plans,
        persona=persona,
        persona_no=2,
        seed=seed_bytes(SEED),
        locale="en_IN",
        mode="synthesize",
        tokens=None,
    )
    acts = {a.key: a for a in fill.actions}
    assert acts["0:1"].value == persona["credential"] and fill.shown["0:1"] is None
    assert re.fullmatch(r"APP-\d{4}", acts["0:2"].value or "") and fill.methods["0:2"] == "neutral"
    assert len(acts["0:3"].value or "") <= 5 and acts["0:9"].value == "1"
    chosen = [k for k in ("0:4", "0:5") if k in acts]
    assert len(chosen) == 1 and acts[chosen[0]].check
    assert fill.shown[chosen[0]] == persona["gender"].title()
    assert acts["0:6"].check and "0:7" not in acts and "0:8" not in acts
    assert (
        acts["0:10"].option_value == "a" and fill.shown["0:10"] == "option:1"
    )  # page text never kept
    assert fill.needles == [] and "Dps!" not in repr(fill) and "Dps!" not in repr(acts["0:1"])
    assert all(isinstance(a, FillAction) for a in fill.actions)
