"""A wrong key must fail reveal, never decrypt to plausible PII (M0 verification round 1, B2).

FF1 and the shape-keeping methods (``email``, ``phone``, ``date_shift``) carry no integrity check,
so a mistyped DEK under the original ``kid`` used to "reveal" a different name, a valid-looking
Aadhaar and another e-mail with HTTP 200. Key material and reveal hints now carry a key check
value (``kcv``) derived from the DEK and the kid; reveal compares it in constant time and refuses
a key that does not match before it decrypts anything.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from dps.app import create_app
from dps.audit import MemoryAuditSink
from dps.config import Settings
from dps.engine.detect.ensemble import Detector, DetectorConfig
from dps.engine.planner import build_plan
from dps.engine.policy import load_preset
from dps.engine.transform import fpe
from dps.engine.transform.apply import ApplyOptions, apply_plan, reveal
from dps.engine.transform.crypto import (
    KeyMaterial,
    generate_key_material,
    key_check_value,
    key_material_from_caller,
    wrap_key_material,
)
from dps.engine.transform.methods import MethodError

RECORD = {
    "full_name": "Ananya Krishnan",
    "aadhaar": "2341 2341 2346",
    "email": "ananya.k@example-mail.in",
    "phone": "+91 98451 20377",
    "dob": "1988-03-14",
}
KEYS = [{"name": "full", "key": "dps_full", "tenant_ids": ["*"], "scopes": ["*"]}]
FULL = {"X-API-KEY": "dps_full"}


def _flip(dek: str) -> str:
    """The same DEK with one base64url character changed (in the middle: every bit counts)."""
    i = len(dek) // 2
    return dek[:i] + ("A" if dek[i] != "A" else "B") + dek[i + 1 :]


class SpyCipher:
    """A key-blind rotation cipher that counts decryptions (none may happen under a wrong key)."""

    decrypts = 0

    def encrypt(self, alphabet: str, plaintext: str) -> str:
        return "".join(alphabet[(alphabet.index(c) + 3) % len(alphabet)] for c in plaintext)

    def decrypt(self, alphabet: str, ciphertext: str) -> str:
        SpyCipher.decrypts += 1
        return "".join(alphabet[(alphabet.index(c) - 3) % len(alphabet)] for c in ciphertext)


def _obfuscate(key: KeyMaterial) -> tuple[ApplyOptions, Any]:
    policy = load_preset("gdpr-pseudonymise")
    findings = Detector(DetectorConfig(locale="en_IN")).detect(data=RECORD).findings
    plan = build_plan(findings, policy, "obfuscate", fpe_enabled=True)
    options = ApplyOptions(
        policy=policy,
        key=key,
        surrogates=None,
        fpe_enabled=True,
        cipher_factory=lambda k, t: SpyCipher(),
        locale="en_IN",
    )
    return options, apply_plan(plan=plan, options=options, data=RECORD)


# --- the key check value ----------------------------------------------------------------------
def test_generated_key_material_carries_a_kcv_bound_to_the_dek_and_the_kid() -> None:
    km = generate_key_material("key")
    assert km.dek is not None and km.kcv is not None
    assert km.kcv == key_check_value(km) == key_check_value(km.model_copy())  # deterministic
    assert km.kcv.startswith("kcv_") and km.dek not in km.kcv
    assert key_check_value(km.model_copy(update={"dek": _flip(km.dek)})) != km.kcv
    assert key_check_value(km.model_copy(update={"kid": "k_" + "0" * 16})) != km.kcv
    assert generate_key_material().kcv != km.kcv


def test_caller_key_material_whose_kcv_does_not_match_its_dek_is_refused() -> None:
    km = generate_key_material("key")
    assert km.dek is not None
    dumped = km.model_dump(mode="json")
    assert key_material_from_caller(dumped).kcv == km.kcv  # the right key passes
    for altered in ({"dek": _flip(km.dek)}, {"kid": "k_" + "0" * 16}, {"kcv": "kcv_x"}):
        with pytest.raises(ValueError) as info:
            key_material_from_caller({**dumped, **altered})
        message = str(info.value)
        assert "check value" in message
        assert km.dek not in message and _flip(km.dek) not in message and km.kcv not in message


def test_caller_key_material_without_a_kcv_gets_one_computed() -> None:
    km = generate_key_material()
    bare = {k: v for k, v in km.model_dump(mode="json").items() if k != "kcv"}
    assert key_material_from_caller(bare).kcv == km.kcv


def test_wrapping_keeps_the_kcv_so_an_unwrapped_key_can_be_checked() -> None:
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_pem = (
        private.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )
    km = generate_key_material()
    wrapped = wrap_key_material(km, public_pem)
    assert wrapped.dek is None and wrapped.kcv == km.kcv


# --- reveal ----------------------------------------------------------------------------------
def test_reveal_hints_carry_the_kcv_of_the_key_they_were_made_under() -> None:
    key = generate_key_material("key")
    _, res = _obfuscate(key)
    assert res.reveal_hints and {h.kcv for h in res.reveal_hints} == {key.kcv}


def test_reveal_with_a_wrong_dek_and_the_right_kid_fails_before_decrypting() -> None:
    key = generate_key_material("key")
    assert key.dek is not None
    options, res = _obfuscate(key)
    wrong = KeyMaterial(kid=key.kid, dek=_flip(key.dek))  # no kcv of its own: the hints decide
    SpyCipher.decrypts = 0
    with pytest.raises(MethodError) as info:
        reveal(output=res.output, key=wrong, hints=res.reveal_hints, options=options)
    assert SpyCipher.decrypts == 0
    assert "key check value" in str(info.value)
    for value in RECORD.values():
        assert value not in str(info.value)
    assert reveal(output=res.output, key=key, hints=res.reveal_hints, options=options) == RECORD


def test_reveal_refuses_hints_that_carry_no_kid_or_kcv() -> None:
    key = generate_key_material("key")
    options, res = _obfuscate(key)
    for field in ("kcv", "kid"):
        stripped = [h.model_copy(update={field: None}) for h in res.reveal_hints]
        with pytest.raises(MethodError):
            reveal(output=res.output, key=key, hints=stripped, options=options)


# --- over HTTP ---------------------------------------------------------------------------------
def _settings(fpe_enabled: bool) -> Settings:
    return Settings(
        _env_file=None,
        env="local",
        auth_enabled=True,
        api_keys=json.dumps(KEYS),
        llm_provider="stub",
        fpe_enabled=fpe_enabled,
        ner_enabled=False,
        default_locale="en_IN",
        rate_limit_enabled=False,
    )


@pytest.mark.parametrize(
    "fpe_enabled",
    [
        False,
        pytest.param(
            True,
            marks=pytest.mark.skipif(not fpe.fpe_available(), reason="needs the [fpe] extra"),
        ),
    ],
)
@pytest.mark.parametrize("present_kcv", [True, False])
def test_api_reveal_with_a_wrong_dek_is_a_value_free_4xx(
    fpe_enabled: bool, present_kcv: bool
) -> None:
    sink = MemoryAuditSink()
    with TestClient(create_app(_settings(fpe_enabled), audit_sink=sink)) as c:
        obf = c.post("/api/v1/obfuscate", json={"data": RECORD}, headers=FULL)
        assert obf.status_code == 200, obf.text
        body = obf.json()
        km, hints = body["key_material"], body["reveal_hints"]
        assert km["kcv"] and {h["kcv"] for h in hints} == {km["kcv"]}
        wrong = {**km, "dek": _flip(km["dek"])}
        if not present_kcv:
            wrong.pop("kcv")  # the hints alone still catch it
        bad = c.post(
            "/api/v1/reveal",
            json={"data": body["result"], "key_material": wrong, "reveal_hints": hints},
            headers=FULL,
        )
        assert 400 <= bad.status_code < 500, bad.text
        assert "check value" in bad.json()["detail"]
        assert sink.events[-1].outcome == "bad_request"
        leaked = [*RECORD.values(), *map(str, body["result"].values()), km["dek"], wrong["dek"]]
        for value in leaked:
            assert value not in bad.text
        good = c.post(
            "/api/v1/reveal",
            json={"data": body["result"], "key_material": km, "reveal_hints": hints},
            headers=FULL,
        )
        assert good.status_code == 200 and good.json()["result"] == RECORD
