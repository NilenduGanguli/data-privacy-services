"""Keyed pseudonyms for identifiers in synthetic tables (programme plan decision D-A).

A synthetic table must not carry a real customer id, account number or employee id: it would
link every synthetic row back to a real person. Yet keys must stay unique and the tables of one
job group must still join. So a sensitive key (and every join identifier) is replaced by a
**keyed pseudonym**:

* **keyed** by the job's (group's) synthesis seed and the entity type — the same customer id gets
  the same pseudonym in ``customers.customer_id`` and ``accounts.customer_id`` of one job group,
  and nothing links it back without the seed;
* **injective** — a keyed permutation of the value's domain, so keys stay unique;
* **fixed-point free** — the map is ``p⁻¹(p(v) + 1 mod N)`` for a keyed permutation ``p``: one
  cycle through the whole domain, so no value ever maps to itself;
* **shape-keeping** — integers stay integers in a tier that does not depend on the column type
  (``[0, 10⁹)`` for values below 10⁹, so an ``integer`` column still holds them; otherwise the
  value's own digit count, up to 18 digits); text keeps its length and every character's class
  (digit, upper-case and lower-case ASCII letters are permuted, everything else is kept); a UUID
  stays a UUID.

Pure Python (no extra), value-free errors. The permutation is a balanced Feistel network with an
HMAC-SHA256 round function (four rounds, counter-mode output for wide halves), cycle-walked into
the domain.
"""

from __future__ import annotations

import hashlib
import hmac
import string
import uuid
from collections.abc import Sequence

#: Join identifiers that are pseudonymised wherever they are kept in synthesize mode (contract
#: §5.3 "synthesize-mode key rule"); also discovery's ``JOIN_ENTITIES``.
LINK_ENTITIES = frozenset({"CUSTOMER_ID", "EMPLOYEE_ID", "CASE_ID"})
#: Decimal digits an integer column type holds whole (PostgreSQL types carry no precision).
INT_DIGITS: dict[str, int] = {
    "smallint": 4,
    "int2": 4,
    "integer": 9,
    "int": 9,
    "int4": 9,
    "bigint": 18,
    "int8": 18,
}
#: An integer pseudonym of a value below 10⁹ is below 10⁹: the narrowest column that takes it.
MIN_PSEUDONYM_DIGITS = 9
MAX_PSEUDONYM_DIGITS = 18
_ROUNDS = 4
_SMALL_TIER = 10**MIN_PSEUDONYM_DIGITS
_CLASSES = (string.digits, string.ascii_uppercase, string.ascii_lowercase)


class PseudonymError(ValueError):
    """A value cannot be pseudonymised (outside the supported domain); value-free."""


def _round_bits(key: bytes, half: int, bits: int) -> int:
    width = (bits + 7) // 8 or 1
    data = half.to_bytes(width, "big")
    out = b""
    counter = 0
    while len(out) * 8 < bits:
        out += hmac.new(key, counter.to_bytes(4, "big") + data, hashlib.sha256).digest()
        counter += 1
    return int.from_bytes(out, "big") & ((1 << bits) - 1)


def _geometry(domain: int) -> tuple[int, int]:
    bits = max(2, (domain - 1).bit_length())
    bits += bits % 2
    half = bits // 2
    return half, (1 << half) - 1


def feistel_permute(x: int, domain: int, keys: Sequence[bytes]) -> int:
    """A keyed bijection on ``range(domain)`` (cycle-walked balanced Feistel network)."""
    if domain < 2 or not 0 <= x < domain:
        raise PseudonymError("value outside the permutation domain")
    half, mask = _geometry(domain)
    y = x
    while True:
        left, right = y >> half, y & mask
        for key in keys:
            left, right = right, left ^ _round_bits(key, right, half)
        y = (left << half) | right
        if y < domain:
            return y


def feistel_invert(y: int, domain: int, keys: Sequence[bytes]) -> int:
    """The inverse of :func:`feistel_permute` for the same ``domain`` and ``keys``."""
    if domain < 2 or not 0 <= y < domain:
        raise PseudonymError("value outside the permutation domain")
    half, mask = _geometry(domain)
    x = y
    while True:
        left, right = x >> half, x & mask
        for key in reversed(keys):
            left, right = right ^ _round_bits(key, left, half), left
        x = (left << half) | right
        if x < domain:
            return x


class KeyPseudonyms:
    """Keyed, injective, fixed-point-free pseudonyms of identifiers under one synthesis seed."""

    def __init__(self, seed: bytes) -> None:
        self._seed = seed
        self._keys: dict[str, list[bytes]] = {}

    def _round_keys(self, label: str) -> list[bytes]:
        keys = self._keys.get(label)
        if keys is None:
            keys = [
                hmac.new(
                    self._seed, f"dps.key_pseudonym|{label}|{r}".encode(), hashlib.sha256
                ).digest()
                for r in range(_ROUNDS)
            ]
            self._keys[label] = keys
        return keys

    def _cycle(self, x: int, domain: int, label: str) -> int:
        """``p⁻¹(p(x) + 1 mod domain)``: one keyed cycle through the domain (no fixed point)."""
        keys = self._round_keys(label)
        return feistel_invert((feistel_permute(x, domain, keys) + 1) % domain, domain, keys)

    def integer(self, entity_type: str, value: int) -> int:
        """The pseudonym of an integer identifier: values below 10⁹ stay below 10⁹, longer ones
        keep their digit count (at most 18 digits); the sign is kept.

        Raises:
            PseudonymError: more than 18 digits.
        """
        sign, magnitude = (-1, -value) if value < 0 else (1, value)
        if magnitude < _SMALL_TIER:
            low, digits = 0, MIN_PSEUDONYM_DIGITS
        else:
            digits = len(str(magnitude))
            if digits > MAX_PSEUDONYM_DIGITS:
                raise PseudonymError("an integer identifier has more than 18 digits")
            low = 10 ** (digits - 1)
        domain = 10**digits - low
        label = f"{entity_type}|int|{digits}|{'-' if sign < 0 else '+'}"
        return sign * (low + self._cycle(magnitude - low, domain, label))

    def text(self, entity_type: str, value: str, *, kind: str = "text") -> str:
        """The pseudonym of a text identifier: same length, same character class at every
        position (ASCII digits, upper- and lower-case letters permuted; others kept). ``kind
        "uuid"`` maps a UUID to a UUID.

        Raises:
            PseudonymError: nothing to permute (no ASCII letter or digit), or not a UUID.
        """
        if kind == "uuid":
            try:
                number = uuid.UUID(value).int
            except ValueError:
                raise PseudonymError("a uuid identifier is not a UUID") from None
            return str(uuid.UUID(int=self._cycle(number, 2**128, f"{entity_type}|uuid")))
        positions: list[tuple[int, str]] = []
        for i, ch in enumerate(value):
            for alphabet in _CLASSES:
                if ch in alphabet:
                    positions.append((i, alphabet))
                    break
        if not positions:
            raise PseudonymError("a text identifier has no letter or digit to pseudonymise")
        number, domain = 0, 1
        for i, alphabet in positions:
            number = number * len(alphabet) + alphabet.index(value[i])
            domain *= len(alphabet)
        shape = "".join(
            "9" if a is _CLASSES[0] else "A" if a is _CLASSES[1] else "a" for _, a in positions
        )
        mapped = self._cycle(number, domain, f"{entity_type}|text|{shape}")
        out = list(value)
        for i, alphabet in reversed(positions):
            mapped, digit = divmod(mapped, len(alphabet))
            out[i] = alphabet[digit]
        return "".join(out)
