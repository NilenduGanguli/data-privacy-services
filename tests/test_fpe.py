from __future__ import annotations

import pytest

from dps.engine.transform.fpe import (
    DIGITS,
    LOWER,
    Ff1,
    cycle_walk_leading_zero,
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


# --- M2 review G4 r1 (B1): a numeric column cannot hold a leading zero -----------------------
class AffineCipher:
    """A keyed permutation of n-digit strings, x -> (7x + 3) mod 10**n (7 is coprime with 10)."""

    def encrypt(self, alphabet: str, plaintext: str) -> str:
        n = len(plaintext)
        return f"{(7 * int(plaintext) + 3) % 10**n:0{n}d}"

    def decrypt(self, alphabet: str, ciphertext: str) -> str:
        n = len(ciphertext)
        return f"{(pow(7, -1, 10**n) * (int(ciphertext) - 3)) % 10**n:0{n}d}"


class StuckCipher:
    """Not a permutation: every input encrypts to all zeros."""

    def encrypt(self, alphabet: str, plaintext: str) -> str:
        return "0" * len(plaintext)

    decrypt = encrypt


def test_preserve_leading_zero_walks_past_a_zero_led_output() -> None:
    # RotCipher maps 7 -> 0: plain FF1 output would lose its first digit in an integer column.
    assert fpe_digits_encrypt(RotCipher(), "7123456") == "0456789"
    out = fpe_digits_encrypt(RotCipher(), "7123456", preserve_leading_zero=True)
    assert out == "3789012"  # one more step of the same permutation
    assert fpe_digits_decrypt(RotCipher(), out, preserve_leading_zero=True) == "7123456"
    assert fpe_digits_decrypt(RotCipher(), "0456789") == "7123456"  # v0.1.0 tokens still reverse
    # outputs that already start with a non-zero digit are the plain FF1 tokens
    assert fpe_digits_encrypt(RotCipher(), "-12345678", preserve_leading_zero=True) == "-45678901"


def test_cycle_walk_is_a_bijection_that_keeps_the_leading_zero_class() -> None:
    cipher = AffineCipher()
    domain = [f"{i:03d}" for i in range(1000)]
    coded = [cycle_walk_leading_zero(lambda d: cipher.encrypt(DIGITS, d), x) for x in domain]
    assert sorted(coded) == domain  # a permutation of the whole domain
    assert all((x[0] == "0") == (y[0] == "0") for x, y in zip(domain, coded, strict=True))
    back = [cycle_walk_leading_zero(lambda d: cipher.decrypt(DIGITS, d), y) for y in coded]
    assert back == domain
    assert any(cipher.encrypt(DIGITS, x)[0] == "0" and x[0] != "0" for x in domain)


def test_preserve_leading_zero_with_a_kept_prefix_is_plain_ff1() -> None:
    for text in ("4532 7811 2290 4417", "7123456789"):
        plain = fpe_digits_encrypt(RotCipher(), text, keep_prefix=1)
        assert plain == fpe_digits_encrypt(
            RotCipher(), text, keep_prefix=1, preserve_leading_zero=True
        )


def test_a_cipher_that_is_not_a_permutation_cannot_loop_forever() -> None:
    with pytest.raises(ValueError) as err:
        fpe_digits_encrypt(StuckCipher(), "12345678", preserve_leading_zero=True)
    assert "12345678" not in str(err.value)


@pytest.mark.fpe
def test_real_ff1_preserve_leading_zero_on_eight_digit_ids() -> None:
    pytest.importorskip("fastfpe")
    import random

    cipher = Ff1(bytes(range(32)), tweak="CUSTOMER_ID")
    rng = random.Random(20261012)
    ids = [str(rng.randrange(10_000_000, 100_000_000)) for _ in range(400)]
    plain = [fpe_digits_encrypt(cipher, i) for i in ids]
    kept = [fpe_digits_encrypt(cipher, i, preserve_leading_zero=True) for i in ids]
    assert sum(1 for p in plain if p and p[0] == "0") >= 10  # the defect: ~1 in 10
    assert all(k is not None and len(k) == 8 and k[0] != "0" for k in kept)
    assert len(set(kept)) == len(ids)
    # tokens that did not start with 0 are unchanged, so earlier tokens and joins stay valid
    assert all(k == p for k, p in zip(kept, plain, strict=True) if p and p[0] != "0")
    assert all(
        fpe_digits_decrypt(cipher, k, preserve_leading_zero=True) == i
        for i, k in zip(ids, kept, strict=True)
        if k
    )
    # a string with a leading zero keeps one (account numbers held as text)
    zero_led = [f"0{i[1:]}" for i in ids[:50]]
    coded = [fpe_digits_encrypt(cipher, z, preserve_leading_zero=True) for z in zero_led]
    assert all(c is not None and c[0] == "0" for c in coded)
    assert [fpe_digits_decrypt(cipher, c, preserve_leading_zero=True) for c in coded if c] == (
        zero_led
    )
