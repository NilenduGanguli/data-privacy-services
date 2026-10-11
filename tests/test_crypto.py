from __future__ import annotations

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from dps.engine.transform.crypto import (
    KeyMaterial,
    b32,
    dek_bytes,
    derive_int,
    det_decrypt,
    det_encrypt,
    generate_key_material,
    key_material_from_caller,
    subkey,
    unb32,
    unwrap_dek,
    wrap_key_material,
)


def _rsa_pems(bits: int = 2048) -> tuple[str, str]:
    private = rsa.generate_private_key(public_exponent=65537, key_size=bits)
    public_pem = (
        private.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )
    private_pem = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    return public_pem, private_pem


def test_key_material_shape() -> None:
    km = generate_key_material("key")
    assert km.alg == "dps.v1" and km.kid.startswith("k_") and km.consistency_scope == "key"
    assert len(dek_bytes(km)) == 32 and km.has_dek()
    assert generate_key_material().kid != km.kid


def test_subkeys_are_deterministic_and_separated() -> None:
    km = generate_key_material()
    assert subkey(km, "det_token|EMAIL") == subkey(km, "det_token|EMAIL")
    assert subkey(km, "det_token|EMAIL") != subkey(km, "det_token|SSN")
    other = KeyMaterial(kid="k_other", dek=km.dek, created_at=km.created_at)
    assert subkey(other, "det_token|EMAIL") != subkey(km, "det_token|EMAIL")  # kid is the salt


def test_deterministic_encryption_round_trip_and_aad_binding() -> None:
    key = subkey(generate_key_material(), "det_token|EMAIL")
    ct1 = det_encrypt(key, b"ann@example.com", "EMAIL")
    assert ct1 == det_encrypt(key, b"ann@example.com", "EMAIL")  # deterministic
    assert ct1 != det_encrypt(key, b"ann@example.com", "PERSON_NAME")  # bound to the type
    assert det_decrypt(key, ct1, "EMAIL") == b"ann@example.com"
    with pytest.raises(Exception):  # noqa: B017 - cryptography raises InvalidTag
        det_decrypt(key, ct1, "PERSON_NAME")


def test_base32_tokens_round_trip_and_are_url_safe() -> None:
    raw = bytes(range(0, 37))
    tok = b32(raw)
    assert tok == tok.lower() and "=" not in tok and unb32(tok) == raw


def test_derive_int_is_in_range_and_stable() -> None:
    key = subkey(generate_key_material(), "date_shift|DATE_OF_BIRTH")
    values = {derive_int(key, "anchor-1", 731) for _ in range(3)}
    assert len(values) == 1 and 0 <= values.pop() < 731


def test_wrap_and_unwrap_with_rsa() -> None:
    public_pem, private_pem = _rsa_pems()
    km = generate_key_material()
    wrapped = wrap_key_material(km, public_pem)
    assert wrapped.dek is None and wrapped.wrapped_dek and wrapped.wrap["type"] == "rsa-oaep-256"  # type: ignore[index]
    assert not wrapped.has_dek()
    with pytest.raises(KeyError):
        dek_bytes(wrapped)
    assert unwrap_dek(wrapped.wrapped_dek, private_pem) == dek_bytes(km)  # type: ignore[arg-type]


def test_caller_supplied_key_material_is_validated() -> None:
    km = generate_key_material()
    again = key_material_from_caller(km.model_dump(mode="json"))
    assert dek_bytes(again) == dek_bytes(km)
    with pytest.raises(ValueError):
        key_material_from_caller(
            {"alg": "dps.v1", "kid": "k_x", "dek": "c2hvcnQ", "created_at": "2026-01-01T00:00:00Z"}
        )


# --- additions beyond the plan (see docs/superpowers/build/M0-DEVIATIONS.md, Task 12) ----------
def test_key_material_errors_never_echo_the_key() -> None:
    km = generate_key_material()
    secret = km.dek
    assert secret is not None
    bad_inputs = [
        {"kid": "k_x", "dek": secret, "created_at": "not a date"},
        {"kid": "k_x", "dek": secret, "wrap": "not a dict"},
        {"kid": ["k_x"], "dek": secret},
        {"alg": "dps.v0", "kid": "k_x", "dek": secret},
        {"kid": "k_x", "dek": secret + "AAAA"},  # 35 bytes
        {"kid": "k_x", "dek": "!!!" + secret},  # not base64url
    ]
    for payload in bad_inputs:
        with pytest.raises(ValueError) as info:
            key_material_from_caller(payload)
        assert secret not in str(info.value) and secret[:12] not in repr(info.value)


def test_wrapped_only_material_is_refused_for_reveal() -> None:
    public_pem, _ = _rsa_pems()
    wrapped = wrap_key_material(generate_key_material(), public_pem)
    with pytest.raises(ValueError):
        key_material_from_caller(wrapped.model_dump(mode="json"))


def test_wrap_refuses_weak_or_unusable_public_keys() -> None:
    weak_public, _ = _rsa_pems(1024)
    km = generate_key_material()
    with pytest.raises(ValueError):
        wrap_key_material(km, weak_public)
    for junk in ("", "not a pem", "-----BEGIN PUBLIC KEY-----\nAAAA\n-----END PUBLIC KEY-----\n"):
        with pytest.raises(ValueError) as info:
            wrap_key_material(km, junk)
        assert km.dek is not None and km.dek not in str(info.value)


def test_longer_subkeys_are_domain_separated() -> None:
    km = generate_key_material()
    short, long = subkey(km, "det_token|EMAIL"), subkey(km, "det_token|EMAIL", length=64)
    assert len(short) == 32 and len(long) == 64
    assert long[:32] != short  # HKDF would otherwise reuse the 32-byte key as a prefix
    key = subkey(km, "det_token|EMAIL", length=64)  # AES-256-SIV
    assert det_decrypt(key, det_encrypt(key, b"x", "EMAIL"), "EMAIL") == b"x"


def test_key_material_repr_never_shows_the_dek() -> None:
    km = generate_key_material()
    assert km.dek is not None and km.dek not in repr(km) and km.dek not in str(km)
