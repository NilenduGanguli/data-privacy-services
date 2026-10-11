from __future__ import annotations

import pytest

from dps.engine.policy import (
    METHOD_NAMES,
    MethodSpec,
    Policy,
    list_presets,
    load_preset,
    resolve_policy,
)


def test_all_presets_load_and_validate() -> None:
    names = {p["name"] for p in list_presets()}
    assert names == {
        "gdpr-pseudonymise",
        "pci-dss",
        "dpdp-pseudonymise",
        "analytics-safe",
        "synthetic-realistic",
        "synthetic-strict",
    }
    for name in names:
        policy = load_preset(name)
        assert isinstance(policy, Policy) and policy.name == name
        for spec in policy.treat.values():
            assert spec.method in METHOD_NAMES


def test_method_spec_flattens_extra_keys() -> None:
    spec = MethodSpec.model_validate({"method": "fpe_digits", "keep_suffix": 4})
    assert spec.method == "fpe_digits" and spec.params == {"keep_suffix": 4}
    with pytest.raises(ValueError):
        MethodSpec.model_validate({"method": "teleport"})


def test_method_for_both_modes() -> None:
    p = load_preset("gdpr-pseudonymise")
    assert p.method_for("SSN", "obfuscate").method == "fpe_digits"
    assert p.method_for("UNKNOWN_THING", "obfuscate").method == "det_token"  # default_sensitive
    assert p.method_for("SSN", "synthesize").method == "surrogate"
    strict = load_preset("synthetic-strict")
    assert strict.consistency.scope == "request" and strict.detect.min_score == 0.5


def test_resolve_policy_merges_overrides_and_hashes_stably() -> None:
    base = resolve_policy(None, default_name="gdpr-pseudonymise")
    merged = resolve_policy(
        {
            "preset": "gdpr-pseudonymise",
            "treat": {"EMAIL": {"method": "redact"}},
            "output": {"token_style": "envelope"},
        },
        default_name="gdpr-pseudonymise",
    )
    assert merged.method_for("EMAIL", "obfuscate").method == "redact"
    assert merged.method_for("SSN", "obfuscate").method == "fpe_digits"  # untouched
    assert merged.output.token_style == "envelope"
    assert (
        base.config_hash() == load_preset("gdpr-pseudonymise").config_hash() != merged.config_hash()
    )
    assert resolve_policy("pci-dss", default_name="x").name == "pci-dss"
    with pytest.raises(KeyError):
        resolve_policy("no-such-preset", default_name="x")


def test_preset_names_cannot_reach_other_files() -> None:
    for name in (
        "../presets/pci-dss",
        "pci-dss/../pci-dss",
        "/etc/hosts",
        "PCI-DSS",
        "",
        "pci-dss.yaml",
    ):
        with pytest.raises(KeyError):
            load_preset(name)
    with pytest.raises(KeyError) as err:
        resolve_policy("../../secret-file", default_name="x")
    assert "secret-file" not in str(err.value)  # only well-formed preset names are echoed


def test_an_override_replaces_a_method_spec_whole() -> None:
    # gdpr keeps the last 4 SSN digits; an override naming only the method must not inherit that.
    p = resolve_policy(
        {"preset": "gdpr-pseudonymise", "treat": {"SSN": {"method": "fpe_digits"}}},
        default_name="x",
    )
    assert p.method_for("SSN", "obfuscate").params == {}
    p = resolve_policy(
        {
            "preset": "gdpr-pseudonymise",
            "treat": {"CARD_PAN": {"method": "mask_partial", "keep_last": 4}},
        },
        default_name="x",
    )
    assert p.method_for("CARD_PAN", "obfuscate").params == {"keep_last": 4}
    s = resolve_policy(
        {
            "preset": "analytics-safe",
            "synthesize": {
                "default": {"method": "redact"},
                "overrides": {"DATE": {"method": "keep"}},
            },
        },
        default_name="x",
    )
    assert s.method_for("EMAIL", "synthesize").method == "redact"
    assert s.method_for("DATE", "synthesize").params == {}


def test_overridden_presets_are_named_as_such() -> None:
    merged = resolve_policy(
        {"preset": "pci-dss", "output": {"token_style": "envelope"}}, default_name="x"
    )
    assert merged.name == "pci-dss+overrides"
    named = resolve_policy(
        {"preset": "pci-dss", "name": "cards-team", "verify": {"leakage_check": "warn"}},
        default_name="x",
    )
    assert named.name == "cards-team"
    assert resolve_policy({"preset": "pci-dss"}, default_name="x").name == "pci-dss+overrides"


def test_cached_presets_are_not_shared_mutable_state() -> None:
    first = load_preset("pci-dss")
    first.treat["CARD_PAN"] = MethodSpec(method="keep")
    first.detect.min_score = 0.99
    again = load_preset("pci-dss")
    assert (
        again.method_for("CARD_PAN", "obfuscate").method == "fpe_digits"
        and again.detect.min_score == 0.6
    )
    assert (
        resolve_policy(None, default_name="pci-dss").method_for("CARD_PAN", "obfuscate").method
        == "fpe_digits"
    )


def test_policy_validation_errors_never_echo_inline_values() -> None:
    with pytest.raises(ValueError) as err:
        resolve_policy(
            {"preset": "pci-dss", "bogus": {"note": "Ann Smith 123-45-6789"}}, default_name="x"
        )
    assert "Ann Smith" not in str(err.value) and "6789" not in str(err.value)
    with pytest.raises(ValueError) as err:
        resolve_policy({"treat": {"SSN": {"method": "teleport"}}}, default_name="x")
    assert "teleport" in str(err.value)  # the method name is policy vocabulary, not a value


def test_preset_entity_keys_are_known_types() -> None:
    from dps.engine.taxonomy import is_builtin_entity

    for meta in list_presets():
        policy = load_preset(meta["name"])
        assert meta["description"]
        for key in [*policy.treat, *policy.synthesize.overrides]:
            assert key == "default_sensitive" or is_builtin_entity(key), (meta["name"], key)


def test_only_dpdp_pins_a_locale() -> None:
    pins = {name: load_preset(name).locale.default for name in (p["name"] for p in list_presets())}
    assert pins == {
        "dpdp-pseudonymise": "en_IN",
        "gdpr-pseudonymise": None,
        "pci-dss": None,
        "analytics-safe": None,
        "synthetic-realistic": None,
        "synthetic-strict": None,
    }
    # An inline policy and a preset with overrides that do not touch the locale stay unpinned.
    assert Policy().locale.default is None
    merged = resolve_policy(
        {"preset": "gdpr-pseudonymise", "treat": {"EMAIL": {"method": "redact"}}},
        default_name="x",
    )
    assert merged.locale.default is None
    assert resolve_policy({"preset": "dpdp-pseudonymise"}, default_name="x").locale.default == (
        "en_IN"
    )


def test_dpdp_policy_hash_is_unchanged_by_the_optional_locale() -> None:
    # The hash recorded before LocaleConfig.default became optional (v0.1.0-m0).
    assert load_preset("dpdp-pseudonymise").config_hash() == (
        "18f2ecaf465bc0e9e6127acf1811637f8c996fb23488a108a0efcf4fc7fcb98e"
    )
