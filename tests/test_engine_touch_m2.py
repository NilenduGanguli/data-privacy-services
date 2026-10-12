"""M0 touch points made by M2 Task 16 (contract §6.2, §6.4)."""

from __future__ import annotations

import subprocess
import sys

from dps.config import Settings
from dps.engine.findings import Finding, Locator, Plan, PlanEntry
from dps.engine.jsonpath import child_path, parse_path
from dps.engine.policy import load_preset
from dps.engine.service import PrivacyEngine
from dps.engine.synth.surrogates import generate_synthesis_seed
from dps.engine.transform.apply import ApplyOptions, _keyed_anchor, apply_plan
from dps.engine.transform.crypto import generate_key_material


def test_child_path() -> None:
    assert child_path("$", "full_name") == "$.full_name"
    assert child_path("$", "odd key") == "$['odd key']"
    assert child_path("$.a", "it's") == "$.a['it\\'s']"
    assert parse_path(child_path("$", "odd key")) == ["odd key"]


def _dob_plan() -> Plan:
    finding = Finding(
        locator=Locator(path="$.dob"),
        entity_type="DATE_OF_BIRTH",
        score=1.0,
        detectors=["snapshot"],
        sensitivity="HIGH",
    )
    return Plan(
        mode="obfuscate",
        record_sensitivity="HIGH",
        entries=[
            PlanEntry(
                finding=finding,
                method="date_shift",
                params={"window_days": 365},
                reason="t",
                reversible=True,
            )
        ],
    )


def test_apply_plan_uses_the_given_anchor() -> None:
    policy = load_preset("gdpr-pseudonymise")
    plan = _dob_plan()
    key = generate_key_material("key")
    base = ApplyOptions(policy=policy, key=key, surrogates=None, fpe_enabled=False)
    a = apply_plan(plan=plan, options=base, data={"dob": "1988-03-14"})
    given = ApplyOptions(
        policy=policy, key=key, surrogates=None, fpe_enabled=False, anchor="CUSTOMER_ID:abc"
    )
    b = apply_plan(plan=plan, options=given, data={"dob": "1988-03-14"})
    # The given anchor replaces anchor_for; it is still keyed before use (M0: the anchor travels
    # in reveal hints, and a salt-free hash of a short id is no secret).
    assert b.anchor == _keyed_anchor("CUSTOMER_ID:abc", key) and a.anchor != b.anchor
    assert b.anchor != "CUSTOMER_ID:abc"
    c = apply_plan(plan=plan, options=given, data={"dob": "1988-03-14", "other": "x"})
    assert c.output["dob"] == b.output["dob"]  # the same anchor shifts the same days


def test_apply_options_keep_their_m0_positional_order() -> None:
    policy = load_preset("gdpr-pseudonymise")
    opts = ApplyOptions(policy, None, None, False)
    assert opts.anchor is None and opts.token_style == "natural" and opts.locale == "en_US"


def test_engine_exposes_detector_and_surrogates() -> None:
    engine = PrivacyEngine(Settings(_env_file=None, llm_provider="stub", ner_enabled=False))
    policy = load_preset("gdpr-pseudonymise")
    assert engine.detector(policy, "en_IN").detect(text="mail ann.k@example-mail.in").findings
    seed = generate_synthesis_seed()
    one = engine.surrogate_engine(seed, "en_US").surrogate("EMAIL", "a@b.co", "en_US", "row:1")
    assert one == engine.surrogate_engine(seed, "en_US").surrogate(
        "EMAIL", "a@b.co", "en_US", "row:1"
    )


def test_dictionary_without_key_is_stable_across_processes() -> None:
    code = (
        "from dps.engine.transform.methods import TransformContext, run_method;"
        "print(run_method('dictionary', 'alpha', TransformContext(entity_type='X', path='$.x', "
        "mode='synthesize', params={'values': ['p','q','r','s','t','u','v']})).value)"
    )
    outs = {
        subprocess.run(
            [sys.executable, "-c", code],
            env={"PYTHONHASHSEED": seed},
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        for seed in ("1", "2", "3")
    }
    assert len(outs) == 1
