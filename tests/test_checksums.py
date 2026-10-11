from __future__ import annotations

import pytest

from dps.engine.detect.checksums import (
    VALIDATORS,
    luhn_check_digit,
    luhn_valid,
    valid_aba,
    validate,
    verhoeff_check_digit,
)


@pytest.mark.parametrize(
    "entity,good,bad",
    [
        ("SSN", "123-45-6789", "666-12-3456"),
        ("ITIN", "912-70-1234", "123-45-6789"),
        ("EIN", "12-3456789", "07-1234567"),
        ("SIN", "123 456 782", "123 456 783"),
        ("CURP", "BOXW310820HNERXN09", "BOXW310820HNERXN00"),
        ("RFC", "GODE561231GR8", "GODE561231GR0"),
        ("AADHAAR", "2341 2341 2346", "2341 2341 2345"),
        ("PAN_IN", "ACUPA7085R", "ACUP17085R"),
        ("NHS_NUMBER", "943 476 5919", "943 476 5910"),
        ("IBAN", "GB82 WEST 1234 5698 7654 32", "GB82 WEST 1234 5698 7654 33"),
        ("SWIFT_BIC", "DEUTDEFF", "DEUTDEF"),
        ("CARD_PAN", "4111 1111 1111 1111", "4111 1111 1111 1112"),
        ("NINO", "AB123456C", "BG123456C"),
        ("ROUTING_ABA", "021000021", "021000022"),
    ],
)
def test_validators_accept_known_good_and_reject_bad(entity: str, good: str, bad: str) -> None:
    assert entity in VALIDATORS
    assert validate(entity, good) is True
    assert validate(entity, bad) is False


def test_unknown_entity_has_no_validator() -> None:
    assert validate("PERSON_NAME", "Ann") is None


def test_luhn_and_verhoeff_helpers() -> None:
    assert luhn_valid("4111111111111111") and not luhn_valid("4111111111111112")
    assert luhn_check_digit("411111111111111") == "1"
    assert verhoeff_check_digit("23412341234") == "6"
    assert valid_aba("021000021") and not valid_aba("123456789")


@pytest.mark.parametrize(
    "value", ["", " ", "-", "x" * 5000, "٤١١١ ١١١١ ١١١١ ١١١١", "\x00\n", "ÑÑÑ"]
)
def test_validators_never_raise(value: str) -> None:
    for entity in VALIDATORS:
        assert validate(entity, value) in (True, False)


def test_luhn_reads_unicode_decimal_digits_as_their_value() -> None:
    # ``\d`` in the pattern catalogue matches any Unicode decimal digit, so the checksum must read
    # them by value (Arabic-Indic 4111 1111 1111 1111 is the same card number).
    assert luhn_valid("٤١١١١١١١١١١١١١١١")
    assert not luhn_valid("٤١١١١١١١١١١١١١١٢")
