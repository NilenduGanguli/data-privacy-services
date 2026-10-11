"""Keyed, format-preserving reshaping of identifier-like values (spec §5.6, P3).

A reshaped surrogate keeps the original's layout (separators, case, combining marks and, for an
identifier that carries digits, its letters) and replaces the characters that carry the
identity: every decimal digit and, when ``letters`` is set, every letter in any script.

The replaced characters are mapped by a **keyed derangement** of the shape's value space: a
permutation without fixed points, keyed by ``HMAC(seed, entity type ‖ shape)``. A reshaped
surrogate is therefore a pure function of ``(seed, entity type, canonical original)``:

* it does not depend on request order, on what else is in the request or on which engine
  computes it, so a caller-held seed gives the same fake across fields, tables and systems;
* it never equals the original (no fixed point);
* two different originals of the same shape never share a surrogate (a permutation is a
  bijection), so foreign keys, joins and counts survive.

Letters are mapped within their own script's alphabet: ASCII Latin, Greek, Cyrillic, Armenian,
Georgian, Hebrew, Arabic, the main Indic scripts, Sinhala, Thai, kana, CJK ideographs and Hangul
jamo. The alphabets are filtered with the frozen Unicode 3.2 database that ships with Python, so
they do not change when Python's Unicode version does. The input is NFKD-decomposed first, so
an accented letter is its base letter plus a combining mark: the base letter is replaced, the
mark is kept and the result is recomposed (``Müller`` → ``Xäqwer``); Hangul syllables become
jamo and recompose into other syllables. A letter outside these alphabets (``ß``, ``ł``, a letter
of another script or one added after Unicode 3.2) is replaced by a seeded random letter of the
same script (ASCII for Latin and unknown scripts), drawn per value from the caller's ``rng``;
two originals that differ *only* in such letters may share a surrogate.
"""

from __future__ import annotations

import hashlib
import hmac
import math
import random
import unicodedata
from collections.abc import Callable, Sequence
from functools import cache

#: Lower-case (or caseless) alphabets: (Unicode script word, first and last code point).
_SCRIPT_RANGES: tuple[tuple[str, int, int], ...] = (
    ("LATIN", 0x0061, 0x007A),
    ("GREEK", 0x03B1, 0x03C9),
    ("CYRILLIC", 0x0430, 0x045F),
    ("ARMENIAN", 0x0561, 0x0586),
    ("HEBREW", 0x05D0, 0x05EA),
    ("ARABIC", 0x0620, 0x06D3),
    ("DEVANAGARI", 0x0905, 0x0939),
    ("BENGALI", 0x0985, 0x09B9),
    ("GURMUKHI", 0x0A05, 0x0A39),
    ("GUJARATI", 0x0A85, 0x0AB9),
    ("ORIYA", 0x0B05, 0x0B39),
    ("TAMIL", 0x0B85, 0x0BB9),
    ("TELUGU", 0x0C05, 0x0C39),
    ("KANNADA", 0x0C85, 0x0CB9),
    ("MALAYALAM", 0x0D05, 0x0D39),
    ("SINHALA", 0x0D85, 0x0DC6),
    ("THAI", 0x0E01, 0x0E2E),  # consonants
    ("THAI", 0x0E30, 0x0E45),  # vowels written as letters
    ("GEORGIAN", 0x10D0, 0x10F6),
    ("HANGUL", 0x1100, 0x1112),  # initial consonants (choseong)
    ("HANGUL", 0x1161, 0x1175),  # vowels (jungseong)
    ("HANGUL", 0x11A8, 0x11C2),  # final consonants (jongseong)
    ("HIRAGANA", 0x3041, 0x3094),
    ("KATAKANA", 0x30A1, 0x30FA),
    ("CJK", 0x4E00, 0x9FA5),
)
_ROUNDS = 10
_RESHAPE_LABEL = "dps.synth.reshape.v1"


@cache
def _alphabets() -> tuple[tuple[str, ...], dict[str, tuple[int, int]], dict[str, int]]:
    """The alphabets, a letter → (alphabet, position) index and a script word → alphabet map."""
    ucd = unicodedata.ucd_3_2_0  # frozen: alphabets never move with Python's Unicode version
    alphabets: list[str] = []
    for _script, first, last in _SCRIPT_RANGES:
        letters = "".join(
            chr(cp)
            for cp in range(first, last + 1)
            if ucd.category(chr(cp)) in ("Ll", "Lo") and not ucd.decomposition(chr(cp))
        )
        alphabets.append(letters)
    index: dict[str, tuple[int, int]] = {}
    by_script: dict[str, int] = {}
    for number, ((script, _first, _last), letters) in enumerate(
        zip(_SCRIPT_RANGES, alphabets, strict=True)
    ):
        by_script.setdefault(script, number)
        for position, letter in enumerate(letters):
            index[letter] = (number, position)
    return tuple(alphabets), index, by_script


def _cased(letter: str, upper: bool) -> str:
    if not upper:
        return letter
    up = letter.upper()
    return up if len(up) == 1 else letter


def _fold(ch: str) -> str:
    """The key of ``ch`` in the letter index (its single-character lower case, if any)."""
    low = ch.lower()
    return low if len(low) == 1 else ch


def has_digits(text: str) -> bool:
    """True when ``text`` (compatibility-decomposed) holds a decimal digit of any script."""
    return any(ch.isdecimal() for ch in unicodedata.normalize("NFKD", text))


class Derangement:
    """A keyed permutation of ``range(size)`` with no fixed point (``size >= 2``).

    ``x → P⁻¹((P(x) + 1) mod size)`` for a keyed permutation ``P``: a conjugate of the cycle
    ``x → x + 1``, so it is a single ``size``-cycle and never maps a value to itself. ``P`` is a
    10-round alternating Feistel network with modular addition (FF1-style, keyed BLAKE2b as the
    round function) over ``Z_left × Z_right``. For a mixed-radix domain the split
    is between positions, balancing the halves; a single position uses a binary domain of at
    least ``size`` values and cycle-walks back into range.
    """

    def __init__(self, key: bytes, radices: Sequence[int]) -> None:
        if not radices or any(r < 2 for r in radices):
            raise ValueError("a derangement needs at least one position of radix 2 or more")
        self.size = math.prod(radices)
        if len(radices) == 1:
            half = max(1, ((self.size - 1).bit_length() + 1) // 2)
            self._left = self._right = 1 << half
        else:
            best = min(
                range(1, len(radices)),
                key=lambda k: max(math.prod(radices[:k]), math.prod(radices[k:])),
            )
            self._left, self._right = math.prod(radices[:best]), math.prod(radices[best:])
        width = max(self._left, self._right).bit_length()
        self._hash = hashlib.blake2b(key=key, digest_size=min(64, (width + 7) // 8 + 8))

    def __call__(self, x: int) -> int:
        return self._walk((self._walk(x, self._forward) + 1) % self.size, self._backward)

    def _walk(self, x: int, step: Callable[[int], int]) -> int:
        x = step(x)
        while x >= self.size:  # cycle-walking: only for a single position's binary domain
            x = step(x)
        return x

    def _round(self, i: int, value: int) -> int:
        h = self._hash.copy()
        h.update(bytes((i,)) + value.to_bytes((value.bit_length() + 7) // 8 or 1, "big"))
        return int.from_bytes(h.digest(), "big")

    def _forward(self, x: int) -> int:
        a, b = divmod(x, self._right)
        for i in range(_ROUNDS):
            if i % 2 == 0:
                a = (a + self._round(i, b)) % self._left
            else:
                b = (b + self._round(i, a)) % self._right
        return a * self._right + b

    def _backward(self, y: int) -> int:
        a, b = divmod(y, self._right)
        for i in reversed(range(_ROUNDS)):
            if i % 2 == 0:
                a = (a - self._round(i, b)) % self._left
            else:
                b = (b - self._round(i, a)) % self._right
        return a * self._right + b


class Reshaper:
    """Reshapes identifier-like values with seed-keyed derangements (cached per shape)."""

    def __init__(self, seed: bytes) -> None:
        self._seed = seed
        self._cache: dict[bytes, Derangement] = {}

    def reshape(
        self,
        entity_type: str,
        original: str,
        rng: random.Random,
        *,
        letters: bool,
        keep: range | None = None,
    ) -> str:
        """``original`` with its digits (and, with ``letters``, its letters) replaced.

        ``keep`` lists positions (of the NFKD form; the same as the original's for ASCII) that
        stay as they are. ``rng`` draws only the letters outside the supported alphabets. With
        nothing to replace (no digit, no letter, or all kept) the original comes back as it is:
        the caller decides what to do with a value that still reveals itself.
        """
        alphabets, index, by_script = _alphabets()
        work = unicodedata.normalize("NFKD", original)
        out = list(work)
        slots: list[tuple[int, int | None]] = []
        values: list[int] = []
        radices: list[int] = []
        shape: list[str] = []
        for i, ch in enumerate(work):
            if keep is not None and i in keep:
                if ch.isalnum():
                    shape.append("=" + _fold(ch))
            elif ch.isdecimal():
                slots.append((i, None))
                values.append(unicodedata.decimal(ch))
                radices.append(10)
                shape.append("#")
            elif letters and ch.isalpha():
                hit = index.get(_fold(ch))
                if hit is None:
                    script = unicodedata.name(ch, "LATIN").split(" ", 1)[0]
                    alphabet = alphabets[by_script.get(script, 0)]
                    out[i] = _cased(rng.choice(alphabet), ch.isupper())
                    shape.append("*")
                else:
                    number, position = hit
                    slots.append((i, number))
                    values.append(position)
                    radices.append(len(alphabets[number]))
                    shape.append(f"@{number}")
            elif ch.isalnum():
                shape.append("=" + _fold(ch))
        if slots:
            mode = "letters" if letters else "digits"
            label = "\x1f".join((_RESHAPE_LABEL, entity_type, mode, *shape))
            key = hmac.new(self._seed, label.encode("utf-8"), hashlib.sha256).digest()
            perm = self._cache.get(key)
            if perm is None:
                perm = self._cache[key] = Derangement(key, radices)
            x = 0
            for value, radix in zip(values, radices, strict=True):
                x = x * radix + value
            y = perm(x)
            replaced: list[int] = []
            for radix in reversed(radices):
                y, value = divmod(y, radix)
                replaced.append(value)
            for (i, alphabet_no), value in zip(slots, reversed(replaced), strict=True):
                if alphabet_no is None:
                    out[i] = str(value)
                else:
                    out[i] = _cased(alphabets[alphabet_no][value], work[i].isupper())
        return unicodedata.normalize("NFC", "".join(out))
