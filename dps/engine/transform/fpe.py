"""Format-preserving encryption (FF1, NIST SP 800-38G) behind an optional extra.

``fastfpe`` (MIT) provides FF1; it is installed only with the ``[fpe]`` extra and used only when
``DPS_FPE_ENABLED=true`` (spec D8: license review first). Everything else here is pure Python:
the **domain guard** (``radix**length >= 1_000_000``, NIST's revised minimum) and the
split/rebuild logic that encrypts only the digit or letter positions of a value while keeping
separators, case pattern and kept prefixes/suffixes. When the guard fails, callers fall back to
``det_token`` and record the reason in the plan.

Non-ASCII letters (``Ñ``, accented characters) pass through unchanged — a documented v1 limit;
names dominated by them should be routed to ``det_token`` by policy.

**Leading zeros** (contract §6.5, amendment 2026-10-12). Plain FF1 turns about one value in ten
into a token that starts with ``0``; an integer or decimal column drops that digit and the stored
token can no longer be reversed. With ``preserve_leading_zero`` the digits are *cycle-walked*:
FF1 is applied again to its own output until the first digit is ``0`` exactly when the input's
is. FF1 is a permutation, so the walk is a permutation of each of the two classes (a bijection,
reversed by walking back with the decryption), the length is kept, and a token that plain FF1
already produced with a non-zero first digit is unchanged. A kept prefix already keeps the first
digit, so the walk applies only when ``keep_prefix`` is 0.
"""

from __future__ import annotations

import importlib.util
from collections.abc import Callable
from string import ascii_lowercase, ascii_uppercase
from typing import Protocol

MIN_DOMAIN = 1_000_000
DIGITS = "0123456789"
LOWER = ascii_lowercase
UPPER = ascii_uppercase
#: Upper bound on cycle-walk steps. FF1 needs ~1.1 steps on average from a value without a
#: leading zero and ~10 from one with it; 1000 steps fail with probability 0.9**1000 < 1e-45,
#: so only a cipher that is not a permutation (a broken test double) can reach the bound.
MAX_WALK = 1000


class Cipher(Protocol):
    """A keyed permutation of strings over an alphabet (FF1 in production)."""

    def encrypt(self, alphabet: str, plaintext: str) -> str: ...

    def decrypt(self, alphabet: str, ciphertext: str) -> str: ...


def fpe_available() -> bool:
    """True when the optional ``fastfpe`` package is importable."""
    return importlib.util.find_spec("fastfpe") is not None


def domain_ok(radix: int, length: int) -> bool:
    """NIST SP 800-38G rev. 1 minimum: ``radix ** length >= 1_000_000``."""
    return length > 0 and radix**length >= MIN_DOMAIN


class Ff1:
    """FF1 over an arbitrary alphabet via ``fastfpe`` (key: 32 bytes → AES-256).

    ``fastfpe`` takes the key and the tweak hex-encoded; the tweak is the UTF-8 bytes of
    ``tweak`` (entity type, plus the field path when field-scoped).
    """

    def __init__(self, key: bytes, tweak: str) -> None:
        if len(key) not in (16, 24, 32):
            raise ValueError("FF1 key must be 16, 24 or 32 bytes")
        from fastfpe import ff1  # lazy: optional extra

        self._ff1 = ff1
        self._key_hex = key.hex()
        self._tweak_hex = tweak.encode("utf-8").hex()

    def encrypt(self, alphabet: str, plaintext: str) -> str:
        """FF1-encrypt ``plaintext`` (every character must be in ``alphabet``)."""
        return str(self._ff1.encrypt(self._key_hex, self._tweak_hex, alphabet, plaintext))

    def decrypt(self, alphabet: str, ciphertext: str) -> str:
        """FF1-decrypt ``ciphertext``."""
        return str(self._ff1.decrypt(self._key_hex, self._tweak_hex, alphabet, ciphertext))


# --- digits -----------------------------------------------------------------------------------
def _digit_positions(text: str) -> list[int]:
    return [i for i, ch in enumerate(text) if ch in DIGITS]


def _rebuild_digits(text: str, positions: list[int], digits: str) -> str:
    chars = list(text)
    for pos, digit in zip(positions, digits, strict=True):
        chars[pos] = digit
    return "".join(chars)


class CycleWalkError(ValueError):
    """The cycle walk did not converge: the cipher is not a permutation (no detail)."""


def cycle_walk_leading_zero(step: Callable[[str], str], digits: str) -> str:
    """Apply ``step`` (FF1 encryption, or decryption to reverse) until the result's first digit is
    ``0`` exactly when ``digits``' first digit is (module docstring).

    Raises:
        CycleWalkError: no such result within :data:`MAX_WALK` steps (``step`` is not a
            permutation). The message carries no digits.
    """
    zero = digits[:1] == "0"
    out = step(digits)
    for _ in range(MAX_WALK):
        if (out[:1] == "0") == zero:
            return out
        out = step(out)
    raise CycleWalkError("fpe_digits cycle walk did not converge: the cipher is not a permutation")


def _fpe_digits(
    cipher: Cipher,
    text: str,
    keep_prefix: int,
    keep_suffix: int,
    *,
    decrypt: bool,
    preserve_leading_zero: bool,
) -> str | None:
    if keep_prefix < 0 or keep_suffix < 0:
        raise ValueError("keep_prefix and keep_suffix must not be negative")
    positions = _digit_positions(text)
    digits = "".join(text[i] for i in positions)
    end = len(digits) - keep_suffix
    if end < keep_prefix:
        return None  # the kept digits cover the value: nothing left to encrypt
    middle = digits[keep_prefix:end]
    if not domain_ok(10, len(middle)):
        return None

    def step(part: str) -> str:
        return cipher.decrypt(DIGITS, part) if decrypt else cipher.encrypt(DIGITS, part)

    if preserve_leading_zero and keep_prefix == 0:
        coded = cycle_walk_leading_zero(step, middle)
    else:
        coded = step(middle)
    return _rebuild_digits(text, positions, digits[:keep_prefix] + coded + digits[end:])


def fpe_digits_encrypt(
    cipher: Cipher,
    text: str,
    *,
    keep_prefix: int = 0,
    keep_suffix: int = 0,
    preserve_leading_zero: bool = False,
) -> str | None:
    """Encrypt the digit positions between a kept prefix and suffix.

    With ``preserve_leading_zero`` the token's first digit is ``0`` exactly when the value's is
    (cycle walking, module docstring); without it this is plain FF1 (v0.1.0 tokens).

    Returns ``None`` when fewer than six digits remain to encrypt (domain too small).
    """
    return _fpe_digits(
        cipher,
        text,
        keep_prefix,
        keep_suffix,
        decrypt=False,
        preserve_leading_zero=preserve_leading_zero,
    )


def fpe_digits_decrypt(
    cipher: Cipher,
    text: str,
    *,
    keep_prefix: int = 0,
    keep_suffix: int = 0,
    preserve_leading_zero: bool = False,
) -> str:
    """Inverse of :func:`fpe_digits_encrypt` (same keeps and ``preserve_leading_zero``);
    ``ValueError`` if the domain is too small."""
    out = _fpe_digits(
        cipher,
        text,
        keep_prefix,
        keep_suffix,
        decrypt=True,
        preserve_leading_zero=preserve_leading_zero,
    )
    if out is None:
        raise ValueError("ciphertext digit domain too small — not produced by fpe_digits")
    return out


# --- text (letters + digits, case pattern kept) ----------------------------------------------
def _fpe_text(cipher: Cipher, text: str, *, decrypt: bool) -> str | None:
    letter_pos = [i for i, ch in enumerate(text) if ch in LOWER or ch in UPPER]
    digit_pos = _digit_positions(text)
    letters = "".join(text[i].lower() for i in letter_pos)
    digits = "".join(text[i] for i in digit_pos)
    if not domain_ok(26, len(letters)):
        return None
    if digits and not domain_ok(10, len(digits)):
        return None
    op = cipher.decrypt if decrypt else cipher.encrypt
    coded_letters = op(LOWER, letters)
    coded_digits = op(DIGITS, digits) if digits else ""
    chars = list(text)
    for pos, ch in zip(letter_pos, coded_letters, strict=True):
        chars[pos] = ch.upper() if text[pos].isupper() else ch
    for pos, ch in zip(digit_pos, coded_digits, strict=True):
        chars[pos] = ch
    return "".join(chars)


def fpe_text_encrypt(cipher: Cipher, text: str) -> str | None:
    """Encrypt letters (radix 26, case pattern kept) and digits (radix 10) in place.

    Returns ``None`` when there are fewer than five ASCII letters, or one to five digits.
    """
    return _fpe_text(cipher, text, decrypt=False)


def fpe_text_decrypt(cipher: Cipher, text: str) -> str:
    """Inverse of :func:`fpe_text_encrypt`; ``ValueError`` if the domain is too small."""
    out = _fpe_text(cipher, text, decrypt=True)
    if out is None:
        raise ValueError("ciphertext domain too small — not produced by fpe_text")
    return out
