"""Rule-valid layouts for document and wallet surrogates (spec §5.6).

* **ICAO 9303 machine-readable zones** (TD1 ID cards, TD2, TD3 passports, MRV-A/B visas):
  the layout, the check-digit rule and the holder-name field, so a surrogate MRZ is rebuilt
  field by field (holder, document number, dates, optional data) with recomputed check digits.
* **Crypto addresses**: Bitcoin base58check (P2PKH, P2SH, testnet) and bech32/bech32m (SegWit
  v0, Taproot) addresses, and Ethereum hex addresses, rebuilt from fresh random bytes with
  valid checksums.

The helpers are pure: every random choice comes from the caller, so a surrogate stays a pure
function of its seed and original.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass

# --- ICAO 9303 machine-readable zones ------------------------------------------------------------

_MRZ_CHARS = re.compile(r"[A-Z0-9<]+")
_MRZ_SHAPES = {(3, 30), (2, 36), (2, 44)}  # (lines, characters per line)
_WEIGHTS = (7, 3, 1)


@dataclass(frozen=True)
class MrzLayout:
    """Field positions in an MRZ with its line breaks removed (ICAO 9303 parts 4-6)."""

    kind: str
    name: slice
    number: slice
    number_check: int
    birth: slice
    birth_check: int
    sex: int
    expiry: slice
    expiry_check: int
    optional: tuple[slice, ...]
    optional_check: int | None
    composite: int | None
    composite_over: tuple[slice, ...]

    @property
    def checks(self) -> tuple[tuple[int, tuple[slice, ...]], ...]:
        """Each check-digit position with the fields it covers (the composite last)."""
        out: list[tuple[int, tuple[slice, ...]]] = [
            (self.number_check, (self.number,)),
            (self.birth_check, (self.birth,)),
            (self.expiry_check, (self.expiry,)),
        ]
        if self.optional_check is not None:
            out.append((self.optional_check, self.optional))
        if self.composite is not None:
            out.append((self.composite, self.composite_over))
        return tuple(out)


_TD1 = MrzLayout(
    "TD1",
    name=slice(60, 90),
    number=slice(5, 14),
    number_check=14,
    birth=slice(30, 36),
    birth_check=36,
    sex=37,
    expiry=slice(38, 44),
    expiry_check=44,
    optional=(slice(15, 30), slice(48, 59)),
    optional_check=None,
    composite=59,
    composite_over=(slice(5, 30), slice(30, 37), slice(38, 45), slice(48, 59)),
)
_TD2 = MrzLayout(
    "TD2",
    name=slice(5, 36),
    number=slice(36, 45),
    number_check=45,
    birth=slice(49, 55),
    birth_check=55,
    sex=56,
    expiry=slice(57, 63),
    expiry_check=63,
    optional=(slice(64, 71),),
    optional_check=None,
    composite=71,
    composite_over=(slice(36, 46), slice(49, 56), slice(57, 71)),
)
_MRVB = MrzLayout(
    "MRV-B",
    name=slice(5, 36),
    number=slice(36, 45),
    number_check=45,
    birth=slice(49, 55),
    birth_check=55,
    sex=56,
    expiry=slice(57, 63),
    expiry_check=63,
    optional=(slice(64, 72),),
    optional_check=None,
    composite=None,
    composite_over=(),
)
_TD3 = MrzLayout(
    "TD3",
    name=slice(5, 44),
    number=slice(44, 53),
    number_check=53,
    birth=slice(57, 63),
    birth_check=63,
    sex=64,
    expiry=slice(65, 71),
    expiry_check=71,
    optional=(slice(72, 86),),
    optional_check=86,
    composite=87,
    composite_over=(slice(44, 54), slice(57, 64), slice(65, 87)),
)
_MRVA = MrzLayout(
    "MRV-A",
    name=slice(5, 44),
    number=slice(44, 53),
    number_check=53,
    birth=slice(57, 63),
    birth_check=63,
    sex=64,
    expiry=slice(65, 71),
    expiry_check=71,
    optional=(slice(72, 88),),
    optional_check=None,
    composite=None,
    composite_over=(),
)


def mrz_check_digit(field: str) -> str:
    """The ICAO 9303 check digit of ``field`` (weights 7, 3, 1; ``A`` = 10; ``<`` = 0)."""
    total = 0
    for i, ch in enumerate(field):
        value = int(ch) if ch.isdigit() else ord(ch) - 55 if "A" <= ch <= "Z" else 0
        total += value * _WEIGHTS[i % 3]
    return str(total % 10)


def split_mrz(original: str) -> tuple[str, MrzLayout] | None:
    """The MRZ's characters without line breaks, upper-cased, and its layout; else ``None``.

    Lines may be separated by any whitespace or not at all.
    """
    lines = original.split()
    flat = "".join(lines).upper()
    if not _MRZ_CHARS.fullmatch(flat):
        return None
    if len(lines) > 1 and (len(lines), len(lines[0])) not in _MRZ_SHAPES:
        return None
    if len(lines) > 1 and len({len(line) for line in lines}) != 1:
        return None
    visa = flat.startswith("V")
    layout = {90: _TD1, 72: _MRVB if visa else _TD2, 88: _MRVA if visa else _TD3}.get(len(flat))
    return None if layout is None else (flat, layout)


def seal_mrz(chars: list[str], original: str, layout: MrzLayout) -> None:
    """Recompute, in place, every check digit the original carries (a ``<`` check stays)."""
    for position, fields in layout.checks:
        if original[position].isdigit():
            chars[position] = mrz_check_digit("".join("".join(chars[f]) for f in fields))


def mrz_name(surname: str, given: str, width: int) -> str:
    """``SURNAME<<GIVEN<NAMES`` padded with ``<`` (``surname`` and ``given`` already A-Z)."""
    primary = re.sub(r"[^A-Z]+", "<", surname.upper()).strip("<")
    secondary = re.sub(r"[^A-Z]+", "<", given.upper()).strip("<")
    return f"{primary}<<{secondary}"[:width].ljust(width, "<")


def fill_layout(original: str, flat: str) -> str:
    """``flat`` written into the original's layout (its whitespace kept where it was)."""
    it = iter(flat)
    return "".join(ch if ch.isspace() else next(it, "") for ch in original)


# --- crypto addresses ---------------------------------------------------------------------------

_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_BECH32 = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
_B58_VERSIONS = {"1": 0x00, "3": 0x05, "m": 0x6F, "n": 0x6F, "2": 0xC4}
_BASE58_ADDRESS = re.compile(r"[13mn2][1-9A-HJ-NP-Za-km-z]{25,34}")
_BECH32_ADDRESS = re.compile(r"(bc|tb|bcrt)1([qpzry9x8gf2tvdw0s3jn54khce6mua7l]{8,87})")
_ETH_ADDRESS = re.compile(r"0x[0-9a-fA-F]{40}")
_BECH32_GENERATOR = (0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3)
_BECH32M = 0x2BC830A3


def base58check(version: int, payload: bytes) -> str:
    """A base58check string: version byte, payload, 4-byte double-SHA-256 checksum."""
    raw = bytes((version,)) + payload
    raw += hashlib.sha256(hashlib.sha256(raw).digest()).digest()[:4]
    number = int.from_bytes(raw, "big")
    digits: list[str] = []
    while number:
        number, remainder = divmod(number, 58)
        digits.append(_B58[remainder])
    return "1" * (len(raw) - len(raw.lstrip(b"\0"))) + "".join(reversed(digits))


def _bech32_polymod(values: list[int]) -> int:
    chk = 1
    for value in values:
        top = chk >> 25
        chk = (chk & 0x1FFFFFF) << 5 ^ value
        for i, generator in enumerate(_BECH32_GENERATOR):
            if (top >> i) & 1:
                chk ^= generator
    return chk


def _five_bit(data: bytes) -> list[int]:
    acc = bits = 0
    out: list[int] = []
    for byte in data:
        acc, bits = (acc << 8) | byte, bits + 8
        while bits >= 5:
            bits -= 5
            out.append((acc >> bits) & 31)
    if bits:
        out.append((acc << (5 - bits)) & 31)
    return out


def bech32_address(hrp: str, version: int, program: bytes) -> str:
    """A SegWit address: bech32 for witness version 0, bech32m (BIP-350) for later versions."""
    data = [version, *_five_bit(program)]
    expanded = [ord(c) >> 5 for c in hrp] + [0] + [ord(c) & 31 for c in hrp]
    polymod = _bech32_polymod([*expanded, *data, 0, 0, 0, 0, 0, 0])
    polymod ^= 1 if version == 0 else _BECH32M
    checksum = [(polymod >> 5 * (5 - i)) & 31 for i in range(6)]
    return f"{hrp}1" + "".join(_BECH32[d] for d in data + checksum)


def crypto_surrogate(original: str, randbytes: Callable[[int], bytes]) -> str | None:
    """A valid address of the original's kind from fresh random bytes; ``None`` if unknown.

    Ethereum addresses come back in lower case (valid, without the EIP-55 mixed-case checksum).
    Surrounding whitespace is kept.
    """
    text = original.strip()
    lead = original[: len(original) - len(original.lstrip())]
    trail = original[len(original.rstrip()) :]
    bech = _BECH32_ADDRESS.fullmatch(text.lower())
    if _ETH_ADDRESS.fullmatch(text):
        address = "0x" + randbytes(20).hex()
    elif _BASE58_ADDRESS.fullmatch(text):
        address = base58check(_B58_VERSIONS[text[0]], randbytes(20))
    elif bech is not None and (text.islower() or text.isupper()):
        hrp, data = bech.group(1), bech.group(2)
        version, groups = _BECH32.index(data[0]), len(data) - 7
        size = groups * 5 // 8
        if version > 16 or not 2 <= size <= 40 or -(-size * 8 // 5) != groups:
            return None
        address = bech32_address(hrp, version, randbytes(size))
        address = address.upper() if text.isupper() else address
    else:
        return None
    return f"{lead}{address}{trail}"
