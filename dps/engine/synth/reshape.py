"""Keyed, format-preserving reshaping of identifier-like values (spec §5.6, P3).

A reshaped surrogate keeps the original's layout (separators, case, combining marks and the
positions the caller keeps) and replaces the characters that carry the identity: every decimal
digit and, when ``letters`` is set, every letter in any script.

The replaced characters are mapped by a **keyed derangement** of the shape's value space: a
permutation without fixed points, keyed by ``HMAC(seed, entity type ‖ shape)``. A reshaped
surrogate is therefore a pure function of ``(seed, entity type, canonical original)``:

* it does not depend on request order, on what else is in the request or on which engine
  computes it, so a caller-held seed gives the same fake across fields, tables and systems;
* it never equals the original (no fixed point);
* two different originals of the same shape never share a surrogate (a permutation is a
  bijection), so foreign keys, joins and counts survive.

The replaced characters are deranged in consecutive **blocks** of at most ~200 bits (about 38
letters, 48 digits or 12 ideographs), each block with its own key ``HMAC(shape key, block
number)``. Every round of a block's cipher covers the whole of its half, so every position of a
long value is re-randomised (a single cipher over a wide domain would leave the leading
characters of each half untouched), every block changes (so the value never equals the
original) and the product of the block permutations is still a bijection of the shape. The
cost is linear in the value's length.

Which letters of an identifier are format and stay is the caller's decision (``keep``);
:func:`code_letters` gives the usual answer for a value that carries digits.

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
import random
import unicodedata
from collections.abc import Callable, Container, Sequence
from functools import cache
from itertools import accumulate
from operator import mul

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
_RESHAPE_LABEL = "dps.synth.reshape.v2"
#: A block's width, in whole bits per position (``ceil(log2(radix))``): integers, so block
#: boundaries are the same on every platform.
_BLOCK_BITS = 192
#: The widest half a round can cover: the round output is at most 64 bytes, 8 of them margin.
_MAX_HALF_BITS = 448
#: Shapes whose block ciphers are kept for reuse, and the most blocks a cached shape may have.
_CACHE_SHAPES = 4096
_CACHE_BLOCKS = 8
#: Letters of an identifier that read as a code (``CUST``, ``MH``, ``K``) are at most this long.
_CODE_LETTERS = 4


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


def code_letters(text: str) -> frozenset[int]:
    """Positions (in ``text``'s NFKD form) of the letters that are an identifier's format.

    In a value with at least as many digits as letters, a run of at most four ASCII letters in
    one case (``CUST-884120``, ``MH14AB1234``, ``K1234567``, ``cust-1``) is a code and stays.
    Every other letter carries identity and is replaced: a word or name (``Ravi 12345678``,
    ``KRISHNAN-1234``), a letter of another script (``山田 1234``), and every letter of a value
    with more letters than digits (free text, ``P<INDKRISHNAN<<…``). Combining marks belong to
    the run they sit in.
    """
    work = unicodedata.normalize("NFKD", text)
    letters = sum(ch.isalpha() for ch in work)
    if not letters or sum(ch.isdecimal() for ch in work) < letters:
        return frozenset()
    keep: set[int] = set()
    run: list[int] = []
    for i, ch in enumerate([*work, " "]):  # a trailing separator closes the last run
        if ch.isalpha():
            run.append(i)
        elif not unicodedata.combining(ch):
            chars = [work[j] for j in run]
            if (
                run
                and len(run) <= _CODE_LETTERS
                and all(c.isascii() for c in chars)
                and (all(c.isupper() for c in chars) or all(c.islower() for c in chars))
            ):
                keep.update(run)
            run = []
    return frozenset(keep)


def _blocks(radices: Sequence[int]) -> list[tuple[int, int]]:
    """Consecutive ``[start, end)`` blocks of about equal width, none wider than ~200 bits."""
    weights = [(radix - 1).bit_length() for radix in radices]
    total = sum(weights)
    count = max(1, -(-total // _BLOCK_BITS))
    blocks: list[tuple[int, int]] = []
    start = cumulative = 0
    for i, weight in enumerate(weights[:-1]):
        cumulative += weight
        if cumulative * count >= (len(blocks) + 1) * total:
            blocks.append((start, i + 1))
            start = i + 1
    blocks.append((start, len(weights)))
    return blocks


class Derangement:
    """A keyed permutation of ``range(size)`` with no fixed point (``size >= 2``).

    ``x → P⁻¹((P(x) + 1) mod size)`` for a keyed permutation ``P``: a conjugate of the cycle
    ``x → x + 1``, so it is a single ``size``-cycle and never maps a value to itself. ``P`` is a
    10-round alternating Feistel network with modular addition (FF1-style, keyed BLAKE2b as the
    round function) over ``Z_left × Z_right``. For a mixed-radix domain the split
    is between positions, balancing the halves; a single position uses a binary domain of at
    least ``size`` values and cycle-walks back into range.

    Each round output is 64 bits wider than the half it is added to, so it moves every digit of
    that half. A half wider than 448 bits cannot be covered and is refused (``ValueError``):
    derange a wide domain in blocks, as :class:`Reshaper` does.
    """

    def __init__(self, key: bytes, radices: Sequence[int]) -> None:
        if not radices or any(r < 2 for r in radices):
            raise ValueError("a derangement needs at least one position of radix 2 or more")
        if len(radices) == 1:
            self.size = radices[0]
            half = max(1, ((self.size - 1).bit_length() + 1) // 2)
            self._left = self._right = 1 << half
        else:
            prefix = list(accumulate(radices, mul))
            self.size = prefix[-1]
            best = min(
                range(1, len(radices)),
                key=lambda k: max(prefix[k - 1], self.size // prefix[k - 1]),
            )
            self._left, self._right = prefix[best - 1], self.size // prefix[best - 1]
        width = max(self._left, self._right).bit_length()
        if width > _MAX_HALF_BITS:
            raise ValueError("domain too wide for one derangement; derange it in blocks")
        self._hash = hashlib.blake2b(key=key, digest_size=(width + 7) // 8 + 8)

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
    """Reshapes identifier-like values with seed-keyed blockwise derangements."""

    def __init__(self, seed: bytes) -> None:
        self._seed = seed
        self._cache: dict[bytes, list[tuple[int, int, Derangement]]] = {}

    def _derangements(
        self, shape_key: bytes, radices: Sequence[int]
    ) -> list[tuple[int, int, Derangement]]:
        """One derangement per block of the shape (cached for short, frequent shapes)."""
        cached = self._cache.get(shape_key)
        if cached is not None:
            return cached
        perms = [
            (
                start,
                end,
                Derangement(
                    hmac.new(shape_key, number.to_bytes(4, "big"), hashlib.sha256).digest(),
                    radices[start:end],
                ),
            )
            for number, (start, end) in enumerate(_blocks(radices))
        ]
        if len(perms) <= _CACHE_BLOCKS and len(self._cache) < _CACHE_SHAPES:
            self._cache[shape_key] = perms
        return perms

    def reshape(
        self,
        entity_type: str,
        original: str,
        rng: random.Random,
        *,
        letters: bool,
        keep: Container[int] | None = None,
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
            shape_key = hmac.new(self._seed, label.encode("utf-8"), hashlib.sha256).digest()
            replaced: list[int] = []
            for start, end, perm in self._derangements(shape_key, radices):
                x = 0
                for value, radix in zip(values[start:end], radices[start:end], strict=True):
                    x = x * radix + value
                y = perm(x)
                block: list[int] = []
                for radix in reversed(radices[start:end]):
                    y, value = divmod(y, radix)
                    block.append(value)
                replaced.extend(reversed(block))
            for (i, alphabet_no), value in zip(slots, replaced, strict=True):
                if alphabet_no is None:
                    out[i] = str(value)
                else:
                    out[i] = _cased(alphabets[alphabet_no][value], work[i].isupper())
        return unicodedata.normalize("NFC", "".join(out))
