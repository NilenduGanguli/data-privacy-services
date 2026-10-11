"""Checksum validators — python-stdnum wrappers plus Luhn, Verhoeff and ABA helpers.

Every function takes the raw matched string (spaces and dashes allowed) and returns a bool; a
validator never raises. ``validate`` returns ``None`` for entity types without a checksum.
"""

from __future__ import annotations

import importlib
import re
from collections.abc import Callable

_DIGITS = re.compile(r"\D")


def _stdnum(module: str, value: str, **kwargs: bool) -> bool:
    try:
        mod = importlib.import_module(module)
        return bool(mod.is_valid(value, **kwargs))
    except Exception:  # noqa: BLE001 - any parse/import problem means "not validated"
        return False


def luhn_valid(digits: str) -> bool:
    """Luhn mod-10 over a digit string (non-digits ignored)."""
    d = _DIGITS.sub("", digits)
    if len(d) < 2:
        return False
    total = 0
    for i, ch in enumerate(reversed(d)):
        n = int(ch)  # any Unicode decimal digit, read by value
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def luhn_check_digit(partial: str) -> str:
    """The digit that makes ``partial + digit`` Luhn-valid."""
    for candidate in "0123456789":
        if luhn_valid(partial + candidate):
            return candidate
    raise ValueError("unreachable: one digit always satisfies Luhn")


def verhoeff_check_digit(partial: str) -> str:
    """Verhoeff check digit (used by Aadhaar), via stdnum."""
    from stdnum import verhoeff

    return str(verhoeff.calc_check_digit(_DIGITS.sub("", partial)))


def valid_card_pan(value: str) -> bool:
    """13-19 digits, not all the same digit, Luhn-valid."""
    d = _DIGITS.sub("", value)
    return 13 <= len(d) <= 19 and len(set(d)) > 1 and luhn_valid(d)


def valid_aba(value: str) -> bool:
    """US ABA routing number checksum."""
    d = _DIGITS.sub("", value)
    if len(d) != 9:
        return False
    n = [int(c) for c in d]
    return (3 * (n[0] + n[3] + n[6]) + 7 * (n[1] + n[4] + n[7]) + (n[2] + n[5] + n[8])) % 10 == 0


_NINO = re.compile(r"^(?!BG|GB|NK|KN|TN|NT|ZZ)[A-CEGHJ-PR-TW-Z][A-CEGHJ-NPR-TW-Z]\d{6}[A-D]$")


def valid_nino(value: str) -> bool:
    """UK National Insurance number format rules (HMRC prefix exclusions; no checksum exists)."""
    return bool(_NINO.match(value.replace(" ", "").upper()))


VALIDATORS: dict[str, Callable[[str], bool]] = {
    "SSN": lambda v: _stdnum("stdnum.us.ssn", v),
    "ITIN": lambda v: _stdnum("stdnum.us.itin", v),
    "EIN": lambda v: _stdnum("stdnum.us.ein", v),
    "SIN": lambda v: _stdnum("stdnum.ca.sin", v),
    "CURP": lambda v: _stdnum("stdnum.mx.curp", v),
    # stdnum skips the RFC check digit by default; "validated" must mean the checksum held.
    "RFC": lambda v: _stdnum("stdnum.mx.rfc", v, validate_check_digits=True),
    "AADHAAR": lambda v: _stdnum("stdnum.in_.aadhaar", v),
    "PAN_IN": lambda v: _stdnum("stdnum.in_.pan", v),
    "NHS_NUMBER": lambda v: _stdnum("stdnum.gb.nhs", v),
    "IBAN": lambda v: _stdnum("stdnum.iban", v),
    "SWIFT_BIC": lambda v: _stdnum("stdnum.bic", v),
    "CARD_PAN": valid_card_pan,
    "NINO": valid_nino,
    "ROUTING_ABA": valid_aba,
}


def validate(entity_type: str, value: str) -> bool | None:
    """True/False when ``entity_type`` has a checksum, ``None`` otherwise."""
    fn = VALIDATORS.get(entity_type)
    return None if fn is None else fn(value)
