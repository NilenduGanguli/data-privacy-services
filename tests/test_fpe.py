from __future__ import annotations

import pytest

from dps.engine.transform.fpe import (
    DIGITS,
    LOWER,
    Ff1,
    domain_ok,
    fpe_available,
    fpe_digits_decrypt,
    fpe_digits_encrypt,
    fpe_text_decrypt,
    fpe_text_encrypt,
)


class RotCipher:
    """Stand-in for FF1 in tests: a fixed rotation within the alphabet (a permutation)."""

    def encrypt(self, alphabet: str, plaintext: str) -> str:
        return "".join(alphabet[(alphabet.index(c) + 3) % len(alphabet)] for c in plaintext)

    def decrypt(self, alphabet: str, ciphertext: str) -> str:
        return "".join(alphabet[(alphabet.index(c) - 3) % len(alphabet)] for c in ciphertext)


def test_domain_guard() -> None:
    assert domain_ok(10, 6) and not domain_ok(10, 5)
    assert domain_ok(26, 5) and not domain_ok(26, 4)


def test_digits_keep_prefix_suffix_and_format() -> None:
    out = fpe_digits_encrypt(RotCipher(), "4532 7811 2290 4417", keep_prefix=6, keep_suffix=4)
    assert out is not None and out.startswith("4532 78") and out.endswith("4417") and out[7] == "4"
    assert out.count(" ") == 3 and out != "4532 7811 2290 4417"  # out[7]: first free digit, 1 -> 4
    assert (
        fpe_digits_decrypt(RotCipher(), out, keep_prefix=6, keep_suffix=4) == "4532 7811 2290 4417"
    )
    assert (
        fpe_digits_encrypt(RotCipher(), "12-34", keep_prefix=0, keep_suffix=0) is None
    )  # 4 < 6 digits


def test_text_preserves_case_pattern_and_separators() -> None:
    out = fpe_text_encrypt(RotCipher(), "Ananya Krishnan-Rao")
    assert out is not None and out != "Ananya Krishnan-Rao"
    assert out[0].isupper() and out[1].islower() and out[6] == " " and out[15] == "-"
    assert fpe_text_decrypt(RotCipher(), out) == "Ananya Krishnan-Rao"
    assert fpe_text_encrypt(RotCipher(), "Li") is None  # 2 letters: domain too small
    assert fpe_text_encrypt(RotCipher(), "Ann 42") is None  # 3 letters, 2 digits: too small


@pytest.mark.fpe
def test_real_ff1_round_trip() -> None:
    pytest.importorskip("fastfpe")
    assert fpe_available()
    cipher = Ff1(bytes(range(32)), tweak="SSN")
    ct = cipher.encrypt(DIGITS, "123456789")
    assert ct != "123456789" and len(ct) == 9 and cipher.decrypt(DIGITS, ct) == "123456789"
    assert cipher.decrypt(LOWER, cipher.encrypt(LOWER, "ananyak")) == "ananyak"


# --- additions beyond the plan (see docs/superpowers/build/M0-DEVIATIONS.md, Task 14) ----------
@pytest.mark.fpe
def test_real_ff1_matches_nist_sp800_38g_samples() -> None:
    pytest.importorskip("fastfpe")
    key = bytes.fromhex("2B7E151628AED2A6ABF7158809CF4F3C")
    assert Ff1(key, tweak="").encrypt(DIGITS, "0123456789") == "2433477484"  # FF1 sample 1
    # sample 2: tweak bytes 39 38 … 30 are the ASCII text "9876543210"
    assert Ff1(key, tweak="9876543210").encrypt(DIGITS, "0123456789") == "6124200773"
    key256 = bytes.fromhex("2B7E151628AED2A6ABF7158809CF4F3CEF4359D8D580AA4F7F036D6F04FC6A94")
    assert Ff1(key256, tweak="").encrypt(DIGITS, "0123456789") == "6657667009"  # sample 7


@pytest.mark.fpe
def test_real_ff1_through_the_format_keeping_helpers() -> None:
    pytest.importorskip("fastfpe")
    cipher = Ff1(bytes(range(32)), tweak="PERSON_NAME|$.customer['full name']")
    name = fpe_text_encrypt(cipher, "Ananya Krishnan-Rao")
    assert name is not None and fpe_text_decrypt(cipher, name) == "Ananya Krishnan-Rao"
    assert fpe_text_encrypt(cipher, "Kumar") is not None  # 26**5 >= 10**6: smallest letter domain
    card = fpe_digits_encrypt(cipher, "4532-7811-2297-4417", keep_prefix=6, keep_suffix=4)
    assert card is not None and card[:7] == "4532-78" and card[-4:] == "4417"
    assert fpe_digits_decrypt(cipher, card, keep_prefix=6, keep_suffix=4) == "4532-7811-2297-4417"


def test_ff1_refuses_bad_key_sizes() -> None:
    with pytest.raises(ValueError):
        Ff1(b"short", tweak="SSN")


def test_negative_keeps_are_refused() -> None:
    with pytest.raises(ValueError):
        fpe_digits_encrypt(RotCipher(), "123456789", keep_prefix=-1)
    with pytest.raises(ValueError):
        fpe_digits_encrypt(RotCipher(), "123456789", keep_suffix=-2)


def test_keeps_that_cover_the_value_fall_back() -> None:
    assert fpe_digits_encrypt(RotCipher(), "1234567890", keep_prefix=6, keep_suffix=6) is None
    with pytest.raises(ValueError):
        fpe_digits_decrypt(RotCipher(), "1234567890", keep_prefix=6, keep_suffix=6)


def test_non_ascii_letters_pass_through() -> None:
    out = fpe_text_encrypt(RotCipher(), "José Núñez")
    assert out is not None and out[3] == "é" and out[6:8] == "úñ"
    assert fpe_text_decrypt(RotCipher(), out) == "José Núñez"
    assert fpe_text_encrypt(RotCipher(), "अनन्या") is None  # no ASCII letters: falls back
