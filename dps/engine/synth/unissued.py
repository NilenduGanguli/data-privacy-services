"""Never-issued ranges for government identifiers (spec §5.6, D-A).

A surrogate of a government identifier must never be somebody's real identifier. A keyed
permutation of the *issued* space (``reshape.py``) cannot promise that: it maps real numbers onto
other real numbers, about ``n² / |space|`` of a column's ``n`` values onto values of the same
column, and at bank scale most of its output would be issued to somebody. Every identifier with a
known range that the issuer never allocates is therefore drawn from that range here, valid in
layout (and in check digits where the scheme has them); :func:`never_issued` is the predicate the
synthesize run's post-write check applies to the target (``identifier_check``).

The ranges (each a published rule of the issuer):

* **NINO** — HMRC never uses D, F, I, Q, U or V as either prefix letter, nor O as the second;
  surrogates take such a prefix. ``QQ`` (HMRC's documentation example) is never drawn: it is what
  test fixtures use (the demo bank's NINOs are ``QQ``), so a surrogate there could equal one.
* **CURP / RFC (natural person)** — RENAPO and the SAT never issue a code whose first four letters
  spell a word on their "inconvenient words" list (the second letter becomes ``X``); surrogates
  start with such a word. Check digits (CURP) are recomputed.
* **NHS number** — numbers starting ``999`` are reserved for testing; mod-11 check digit valid.
* **EIN** — campus prefixes the IRS never assigns (``00``, ``07``-``09``, ``17``-``19``, ...).
* **PAN (India)** — the sequential part runs ``0001``-``9999``; surrogates use ``0000``.
* **SSN / ITIN** — area ``9xx`` is never an SSN; surrogates keep to the 9xx groups the IRS issues
  no ITIN or ATIN in (``01``-``49``, ``89``). **SIN** — never starts with ``0``. **Aadhaar** —
  never starts with ``0`` or ``1`` (both in ``surrogates.py``).
* **NATIONAL_ID** (a column of mixed or unknown issuers) — by recognised sub-shape: 12 digits as
  Aadhaar (leading ``1``), 9 digits as ``000…`` (never an SSN area, never a SIN), and the NINO,
  CURP, RFC and NHS shapes as above. Any other shape has no known range.

PASSPORT, DRIVER_LICENSE, BN, INE_CLAVE, VOTER_ID and MRZ document numbers have no range that no
issuer allocates: their surrogates stay reshaped, and :func:`never_issued` is ``False`` for every
value of them, so a synthesize run that writes such a column fails closed to review.
"""

from __future__ import annotations

import random
import re
from collections.abc import Callable

from stdnum.mx import curp as _curp
from stdnum.mx import rfc as _rfc

from dps.engine.detect.checksums import luhn_check_digit, verhoeff_check_digit

#: Letters HMRC never uses in a NINO prefix (first / second position).
_NINO_NEVER_FIRST = "DFIQUV"
_NINO_NEVER_SECOND = "DFIOQUV"
#: Prefixes HMRC never allocates although their letters are allowed (TN is the old temporary
#: reference: issued to people, so not "never issued").
_NINO_NEVER_PAIRS = frozenset({"BG", "GB", "NK", "KN", "NT", "ZZ"})
#: The prefixes surrogates draw from: never-used letters only, without QQ (test fixtures' prefix).
NINO_PREFIXES: tuple[str, ...] = tuple(
    a + b for a in _NINO_NEVER_FIRST for b in _NINO_NEVER_SECOND if a + b != "QQ"
)
#: EIN campus prefixes the IRS never assigns.
EIN_NEVER_PREFIXES: tuple[str, ...] = (
    "00",
    "07",
    "08",
    "09",
    "17",
    "18",
    "19",
    "28",
    "29",
    "49",
    "69",
    "70",
    "78",
    "79",
    "89",
    "96",
    "97",
)
#: 9xx groups that are neither SSNs (no SSN has area 9xx) nor ITINs/ATINs (ITINs use groups
#: 50-65, 70-88, 90-92 and 94-99; ATINs 93).
_TIN_FREE_GROUPS = frozenset(range(0, 50)) | {66, 67, 68, 69, 89}
#: The groups an SSN surrogate takes (01-49: below every ITIN and ATIN group).
_SSN_GROUPS = tuple(range(1, 50))
#: Words RENAPO and the SAT never issue as the first four letters (stdnum's lists).
CURP_NEVER_WORDS: tuple[str, ...] = tuple(sorted(_curp._name_blacklist))
RFC_NEVER_WORDS: tuple[str, ...] = tuple(sorted(_rfc._name_blacklist))
_CURP_STATES: tuple[str, ...] = tuple(sorted(_curp._valid_states))
_CONSONANTS = "BCDFGHJKLMNPQRSTVWXYZ"
_UPPER = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_HOMOCLAVE = "ABCDEFGHIJKLMNPQRSTUVWXYZ0123456789"

_NINO_SHAPE = re.compile(r"[A-Z]{2}\d{6}[A-D]?")
_CURP_SHAPE = re.compile(r"[A-Z]{4}\d{6}[HMX][A-Z]{2}[A-Z]{3}[A-Z0-9]\d")
_RFC_PERSON_SHAPE = re.compile(r"[A-ZÑ]{4}\d{6}[A-Z0-9]{3}")
_PAN_SHAPE = re.compile(r"[A-Z]{5}\d{4}[A-Z]")

#: Entity types whose surrogates come from a never-issued range (all of them, or by sub-shape).
UNISSUED_ENTITIES = frozenset(
    {
        "SSN",
        "ITIN",
        "SIN",
        "AADHAAR",
        "NINO",
        "CURP",
        "RFC",
        "NHS_NUMBER",
        "EIN",
        "PAN_IN",
        "NATIONAL_ID",
    }
)


def compact(value: str) -> str:
    """Letters and digits of ``value``, upper-cased (separators and spaces dropped)."""
    return "".join(ch for ch in value.upper() if ch.isalnum())


def into_layout(original: str, flat: str) -> str:
    """``flat`` written over the letters and digits of ``original`` (separators kept) when they
    are as many; else ``flat`` as it is. The original's case is kept when it is all lower."""
    slots = sum(ch.isalnum() for ch in original)
    out = flat
    if slots == len(flat):
        it = iter(flat)
        out = "".join(next(it) if ch.isalnum() else ch for ch in original)
    letters = [ch for ch in original if ch.isalpha()]
    if letters and all(ch.islower() for ch in letters):
        out = out.lower()
    return out


def _digits(rng: random.Random, n: int) -> str:
    return "".join(str(rng.randint(0, 9)) for _ in range(n))


# --- generators (a seeded rng in, a never-issued value out) -----------------------------------
def nino(original: str, rng: random.Random) -> str:
    """A NINO with a never-allocated prefix, in the original's layout."""
    # a NINO is sometimes written without its suffix letter (8 characters)
    suffix = "" if len(compact(original)) == 8 else rng.choice("ABCD")
    return into_layout(original, rng.choice(NINO_PREFIXES) + _digits(rng, 6) + suffix)


def curp(original: str, rng: random.Random) -> str:
    """A CURP that starts with an inconvenient word (never issued), check digit valid."""
    year = rng.randint(1940, 2005)
    month, day = rng.randint(1, 12), rng.randint(1, 28)
    body = (
        rng.choice(CURP_NEVER_WORDS)
        + f"{year % 100:02d}{month:02d}{day:02d}"
        + rng.choice("HM")
        + rng.choice(_CURP_STATES)
        + "".join(rng.choice(_CONSONANTS) for _ in range(3))
        + (str(rng.randint(0, 9)) if year < 2000 else rng.choice(_UPPER))
    )
    return into_layout(original, body + str(_curp.calc_check_digit(body)))


def rfc_person(original: str, rng: random.Random) -> str:
    """A natural person's RFC that starts with an inconvenient word (never issued)."""
    body = (
        rng.choice(RFC_NEVER_WORDS)
        + f"{rng.randint(40, 99):02d}{rng.randint(1, 12):02d}{rng.randint(1, 28):02d}"
        + "".join(rng.choice(_HOMOCLAVE) for _ in range(3))
    )
    return into_layout(original, body)


def nhs(original: str, rng: random.Random) -> str:
    """A ``999`` (test-reserved) NHS number with a valid mod-11 check digit."""
    while True:
        body = "999" + _digits(rng, 6)
        total = sum(int(d) * (10 - i) for i, d in enumerate(body))
        check = 11 - total % 11
        if check == 10:
            continue
        return into_layout(original, body + str(0 if check == 11 else check))


def ein(original: str, rng: random.Random) -> str:
    """An EIN on a campus prefix the IRS never assigns."""
    digits = rng.choice(EIN_NEVER_PREFIXES) + _digits(rng, 7)
    if sum(ch.isdigit() for ch in original) == 9:
        return into_layout(original, digits)
    return f"{digits[:2]}-{digits[2:]}"


def tin_digits(rng: random.Random, *, groups: tuple[int, ...] | None = None) -> str:
    """Nine digits in area 9xx and a group that no SSN, ITIN or ATIN has."""
    group = rng.choice(groups or _SSN_GROUPS)
    return f"9{rng.randint(0, 99):02d}{group:02d}{rng.randint(1, 9999):04d}"


def nine_digits(original: str, rng: random.Random) -> str:
    """Nine digits ``000…`` (SSN area 000 and SINs starting with 0 are never issued), Luhn-valid
    so the SIN check digit holds too, in the original's layout."""
    body = "000" + _digits(rng, 5)
    return into_layout(original, body + luhn_check_digit(body))


def twelve_digits(original: str, rng: random.Random) -> str:
    """Twelve digits starting with ``1`` (no Aadhaar starts with 0 or 1), Verhoeff-valid."""
    body = "1" + _digits(rng, 10)
    return into_layout(original, body + verhoeff_check_digit(body))


def national_shape(value: str) -> str | None:
    """The recognised sub-shape of a generic national identifier, or None."""
    flat = compact(value)
    if not flat:
        return None
    if flat.isdigit():
        return {12: "AADHAAR", 9: "TIN9", 10: "NHS"}.get(len(flat))
    if _CURP_SHAPE.fullmatch(flat):
        return "CURP"
    if len(flat) in (8, 9) and _NINO_SHAPE.fullmatch(flat):
        return "NINO"
    if len(flat) == 13 and _RFC_PERSON_SHAPE.fullmatch(flat):
        return "RFC"
    return None


_NATIONAL: dict[str, Callable[[str, random.Random], str]] = {
    "AADHAAR": twelve_digits,
    "TIN9": nine_digits,
    "NHS": nhs,
    "CURP": curp,
    "NINO": nino,
    "RFC": rfc_person,
}


def national_id(original: str, rng: random.Random) -> str | None:
    """A never-issued surrogate for a generic national identifier of a recognised sub-shape;
    None when the shape has no known range (the caller reshapes and the run reviews it)."""
    shape = national_shape(original)
    return _NATIONAL[shape](original, rng) if shape else None


# --- the predicate -----------------------------------------------------------------------------
def _nino_free(flat: str) -> bool:
    if not _NINO_SHAPE.fullmatch(flat):
        return False
    a, b = flat[0], flat[1]
    return a in _NINO_NEVER_FIRST or b in _NINO_NEVER_SECOND or flat[:2] in _NINO_NEVER_PAIRS


def _tin_free(flat: str) -> bool:
    """Nine digits that no SSN, ITIN or ATIN can be."""
    if len(flat) != 9 or not flat.isdigit():
        return False
    area, group = flat[:3], int(flat[3:5])
    return area in ("000", "666") or (area[0] == "9" and group in _TIN_FREE_GROUPS)


def _nhs_free(flat: str) -> bool:
    return len(flat) == 10 and flat.isdigit() and flat.startswith("999")


def _curp_free(flat: str) -> bool:
    return bool(_CURP_SHAPE.fullmatch(flat)) and flat[:4] in _curp._name_blacklist


def _rfc_free(flat: str) -> bool:
    return (
        len(flat) == 13
        and bool(_RFC_PERSON_SHAPE.fullmatch(flat))
        and flat[:4] in _rfc._name_blacklist
    )


def _national_free(flat: str) -> bool:
    shape = national_shape(flat)
    if shape == "AADHAAR":
        return flat[0] in "01"
    if shape == "TIN9":
        return flat.startswith("000")
    if shape == "NHS":
        return _nhs_free(flat)
    if shape == "CURP":
        return _curp_free(flat)
    if shape == "NINO":
        return _nino_free(flat)
    if shape == "RFC":
        return _rfc_free(flat)
    return False


_PREDICATES: dict[str, Callable[[str], bool]] = {
    "SSN": _tin_free,
    "ITIN": _tin_free,
    "SIN": lambda f: len(f) == 9 and f.isdigit() and f[0] == "0",
    "AADHAAR": lambda f: len(f) == 12 and f.isdigit() and f[0] in "01",
    "NINO": _nino_free,
    "CURP": _curp_free,
    "RFC": _rfc_free,
    "NHS_NUMBER": _nhs_free,
    "EIN": lambda f: len(f) == 9 and f.isdigit() and f[:2] in EIN_NEVER_PREFIXES,
    "PAN_IN": lambda f: bool(_PAN_SHAPE.fullmatch(f)) and f[5:9] == "0000",
    "NATIONAL_ID": _national_free,
}


def never_issued(entity_type: str, value: str) -> bool:
    """True when ``value`` lies in a range the issuer of ``entity_type`` never allocates.

    False for every value of a type without such a range (PASSPORT, DRIVER_LICENSE, ...), and for
    a generic national identifier whose sub-shape is not recognised: the caller cannot tell it is
    nobody's, so it reviews it.
    """
    predicate = _PREDICATES.get(entity_type)
    return predicate(compact(value)) if predicate is not None else False
