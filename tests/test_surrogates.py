from __future__ import annotations

import re
from datetime import date

import phonenumbers
import pytest
from stdnum import verhoeff

from dps.engine.detect.checksums import luhn_valid, valid_aba, validate
from dps.engine.synth.personas import PersonaFactory
from dps.engine.synth.surrogates import (
    SurrogateEngine,
    generate_synthesis_seed,
    seed_bytes,
    seed_from_caller,
)

SEED = seed_bytes(generate_synthesis_seed())


def test_seed_round_trip_and_validation() -> None:
    s = generate_synthesis_seed()
    assert seed_from_caller(s.model_dump(mode="json")).seed == s.seed and len(seed_bytes(s)) == 32
    with pytest.raises(ValueError):
        seed_from_caller(
            {"alg": "dps.synth.v1", "seed": "c2hvcnQ", "created_at": "2026-01-01T00:00:00Z"}
        )


def test_persona_is_coherent_and_deterministic() -> None:
    f = PersonaFactory(SEED)
    a = f.for_anchor("CUSTOMER_ID:abc", "en_IN")
    assert a == f.for_anchor("CUSTOMER_ID:abc", "en_IN") and a != f.for_anchor(
        "CUSTOMER_ID:xyz", "en_IN"
    )
    assert a.first_name.lower() in a.email_local and a.last_name.lower() in a.email_local
    assert a.country_code == "IN" and a.gender in {"female", "male"}


def test_surrogates_are_rule_valid_and_consistent() -> None:
    eng = SurrogateEngine(SEED, default_locale="en_IN")
    anchor = "CUSTOMER_ID:abc"
    card = eng.surrogate("CARD_PAN", "4532 7811 2290 4417", "en_IN", anchor)
    assert card.startswith("4111 11") and card.count(" ") == 3 and luhn_valid(card)
    assert card != "4532 7811 2290 4417"
    # Aadhaar: Verhoeff-valid and Aadhaar-shaped, but outside issued space (spec §5.6: UIDAI never
    # issues a number starting with 0 or 1), so stdnum's issuance-aware check rejects it.
    aad = eng.surrogate("AADHAAR", "2341 2341 2346", "en_IN", anchor)
    digits = aad.replace(" ", "")
    assert aad.count(" ") == 2 and aad != "2341 2341 2346" and len(digits) == 12
    assert digits[0] == "1" and verhoeff.is_valid(digits) and validate("AADHAAR", aad) is False
    ssn = eng.surrogate("SSN", "123-45-6789", "en_US", anchor)
    assert ssn[0] == "9" and ssn.count("-") == 2 and validate("SSN", ssn) is False  # never issued
    assert valid_aba(eng.surrogate("ROUTING_ABA", "021000021", "en_US", anchor))
    email = eng.surrogate("EMAIL", "ananya.k@example-mail.in", "en_IN", anchor)
    assert email.split("@")[1] in {"example.com", "example.net", "example.org"}
    phone = eng.surrogate("PHONE", "+91 98451 20377", "en_IN", anchor)
    assert phone.startswith("+91") and phonenumbers.is_valid_number(phonenumbers.parse(phone, None))
    dob = eng.surrogate("DATE_OF_BIRTH", "1988-03-14", "en_IN", anchor)
    assert dob != "1988-03-14" and len(dob) == 10 and 1985 <= int(dob[:4]) <= 1989  # same band
    name = eng.surrogate("PERSON_NAME", "Ananya Krishnan", "en_IN", anchor)
    assert " " in name and name != "Ananya Krishnan"
    assert name == eng.surrogate("PERSON_NAME", "Ananya Krishnan", "en_IN", anchor)  # same persona
    assert eng.surrogate("PERSON_NAME", "Ananya", "en_IN", anchor) == name.split(" ")[0]
    assert eng.surrogate("API_KEY", "AKIAIOSFODNN7EXAMPLE", "en_US", anchor) == "[REDACTED]"
    cust = eng.surrogate("CUSTOMER_ID", "CUST-884120", "en_IN", anchor)
    assert cust.startswith("CUST-") and cust != "CUST-884120" and cust[5:].isdigit()


def test_value_consistency_and_never_identity() -> None:
    eng = SurrogateEngine(SEED)
    a = eng.surrogate("EMAIL", "x@y.co", "en_US", "r1")
    assert a == eng.surrogate("EMAIL", "x@y.co", "en_US", "r1")
    assert a != eng.surrogate("EMAIL", "z@y.co", "en_US", "r1")
    for original in ("12", "7", "ab"):
        assert eng.surrogate("ACCOUNT_NUMBER", original, "en_US", "r1") != original
    assert eng.surrogate("CITY", "Bengaluru", "xx_ZZ", "r1")  # unknown locale falls back


# --- added beyond the plan ---------------------------------------------------------------------


def test_the_birth_date_stays_in_its_band_whatever_was_asked_first() -> None:
    """The persona cache must not freeze a DOB drawn before the original DOB was known."""
    for first in ("EMAIL", "CITY", "PERSON_NAME", "AGE"):
        eng = SurrogateEngine(SEED, default_locale="en_IN")
        eng.surrogate(first, {"AGE": "36"}.get(first, "Ananya Krishnan"), "en_IN", "r1")
        dob = eng.surrogate("DATE_OF_BIRTH", "1988-03-14", "en_IN", "r1")
        assert 1985 <= int(dob[:4]) <= 1989 and dob != "1988-03-14"
        fresh = SurrogateEngine(SEED, default_locale="en_IN")
        assert dob == fresh.surrogate("DATE_OF_BIRTH", "1988-03-14", "en_IN", "r1")


def test_persona_fields_do_not_depend_on_the_original_dob() -> None:
    f = PersonaFactory(SEED)
    plain = f.for_anchor("r1", "en_IN")
    banded = f.for_anchor("r1", "en_IN", original_dob=date(1961, 7, 30))
    assert (plain.first_name, plain.last_name, plain.street, plain.city, plain.postal_code) == (
        banded.first_name,
        banded.last_name,
        banded.street,
        banded.city,
        banded.postal_code,
    )
    assert 1960 <= banded.dob.year <= 1964 and banded.dob != date(1961, 7, 30)


def test_same_seed_same_surrogates_across_engines_and_formats() -> None:
    one, two = SurrogateEngine(SEED), SurrogateEngine(SEED)
    for entity, original in (
        ("SSN", "123-45-6789"),
        ("EMAIL", "x@y.co"),
        ("PHONE", "+1 212 555 0199"),
        ("PERSON_NAME", "Jane Roe"),
        ("IBAN", "DE89 3704 0044 0532 0130 00"),
    ):
        assert one.surrogate(entity, original, "en_US", "r9") == two.surrogate(
            entity, original, "en_US", "r9"
        )
    other = SurrogateEngine(seed_bytes(generate_synthesis_seed()))
    assert other.surrogate("SSN", "123-45-6789", "en_US", "r9") != one.surrogate(
        "SSN", "123-45-6789", "en_US", "r9"
    )
    # the same identifier written two ways maps to the same digits, each in its own format
    assert one.surrogate("AADHAAR", "2341 2341 2346", "en_IN", "r1").replace(
        " ", ""
    ) == one.surrogate("AADHAAR", "234123412346", "en_IN", "r1")


def test_distinct_identifiers_never_share_a_surrogate_within_an_engine() -> None:
    eng = SurrogateEngine(SEED)
    for entity, make, count in (
        ("EMAIL", lambda i: f"user{i}@corp.example", 20),
        ("ACCOUNT_NUMBER", lambda i: str(i), 6),  # a one-digit space: collisions are likely
        ("CVV", lambda i: f"{i:03d}", 20),
    ):
        seen: dict[str, str] = {}
        for i in range(count):
            original = make(i)
            out = eng.surrogate(entity, original, "en_US", "same-record")
            assert seen.setdefault(out, original) == original, entity


def test_never_issued_ranges() -> None:
    eng = SurrogateEngine(SEED)
    for i in range(30):
        itin = eng.surrogate("ITIN", f"912-7{i % 10}-{1000 + i}", "en_US", "r1")
        assert itin[0] == "9" and itin[4:6] in {"89", "93"} and validate("ITIN", itin) is False
        sin = eng.surrogate("SIN", f"046 454 {286 + i:03d}", "en_CA", "r1")
        assert sin[0] == "0" and luhn_valid(sin) and validate("SIN", sin) is False
        us = eng.surrogate("PHONE", f"+1 212 555 {1000 + i}", "en_US", f"r{i}")
        assert re.fullmatch(r"\+1 \d{3}-555-01\d\d", us), us
        assert phonenumbers.is_valid_number(phonenumbers.parse(us, None))
        gb = eng.surrogate("PHONE", f"+44 7911 123{i:03d}", "en_GB", f"r{i}")
        assert re.fullmatch(r"\+44 7700 900\d{3}", gb), gb  # Ofcom drama range
        assert eng.surrogate("PHONE", f"07911 123{i:03d}", "en_GB", f"r{i}").startswith("07700 900")


def test_phone_keeps_the_original_country_and_format() -> None:
    eng = SurrogateEngine(SEED)
    intl = eng.surrogate("PHONE", "+91 98451 20377", "en_US", "r1")  # locale says US, number IN
    assert intl.startswith("+91 ") and phonenumbers.is_valid_number(phonenumbers.parse(intl, None))
    national = eng.surrogate("PHONE", "(212) 555-0142", "en_US", "r1")
    assert national.startswith("(") and "555-01" in national
    mx = eng.surrogate("PHONE", "+52 222 123 4567", "es_MX", "r1")
    assert mx.startswith("+52 ") and phonenumbers.is_valid_number(phonenumbers.parse(mx, None))


def test_iban_keeps_country_and_shape_and_is_valid() -> None:
    eng = SurrogateEngine(SEED)
    for original in (
        "DE89 3704 0044 0532 0130 00",
        "GB29NWBK60161331926819",
        "FR1420041010050500013M02606",
    ):
        out = eng.surrogate("IBAN", original, "en_IN", "r1")
        assert out[:2] == original[:2] and len(out) == len(original) and out != original
        assert [c.isalpha() for c in out[4:]] == [c.isalpha() for c in original[4:]]
        assert validate("IBAN", out) is True


def test_health_and_other_values_are_replaced_not_returned() -> None:
    eng = SurrogateEngine(SEED)
    for entity, original in (
        ("MEDICATION", "Metformin"),
        ("DIAGNOSIS_CODE", "E11.9"),
        ("SWIFT_BIC", "HDFCINBBXXX"),
        ("DATE", "the fourteenth of March"),
        ("PASSPORT", "K1234567"),
        ("MEDICAL_RECORD_NUMBER", "MRN-00042"),
    ):
        out = eng.surrogate(entity, original, "en_US", "r1")
        assert out and out != original
        assert original.lower() not in out.lower(), entity
    code = eng.surrogate("DIAGNOSIS_CODE", "E11.9", "en_US", "r1")
    assert re.fullmatch(r"[A-Z]\d\d\.\d", code)
    assert eng.surrogate("MEDICATION", "Metformin", "en_US", "r1").isalpha()


def test_shapes_follow_the_original() -> None:
    eng = SurrogateEngine(SEED)
    assert eng.surrogate("GENDER", "F", "en_US", "r1") in {"F", "M"}
    assert eng.surrogate("GENDER", "female", "en_US", "r1") in {"female", "male"}
    assert len(eng.surrogate("CVV", "1234", "en_US", "r1")) == 4
    assert re.fullmatch(r"\d\d/\d{4}", eng.surrogate("CARD_EXPIRY", "09/2027", "en_US", "r1"))
    assert re.fullmatch(r"\d\d/\d\d", eng.surrogate("CARD_EXPIRY", "09/27", "en_US", "r1"))
    assert eng.surrogate("USERNAME", "@ananya_k", "en_US", "r1").startswith("@")
    uuid = eng.surrogate("DEVICE_ID", "550e8400-e29b-41d4-a716-446655440000", "en_US", "r1")
    assert re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", uuid)
    assert re.fullmatch(  # locally administered (02:...), separator and case kept
        r"02(-[0-9A-F]{2}){5}", eng.surrogate("MAC_ADDRESS", "3C-22-FB-11-22-33", "en_US", "r1")
    )
    assert re.fullmatch(
        r"02(:[0-9a-f]{2}){5}", eng.surrogate("MAC_ADDRESS", "3c:22:fb:11:22:33", "en_US", "r1")
    )
    assert eng.surrogate("IP_ADDRESS", "2001:4860:4860::8888", "en_US", "r1").startswith(
        "2001:db8:"
    )
    assert eng.surrogate("IP_ADDRESS", "49.205.1.7", "en_US", "r1").startswith("10.")
    assert eng.surrogate("PERSON_NAME", "ANANYA KRISHNAN", "en_IN", "r1").isupper()
    age = int(eng.surrogate("AGE", "36", "en_US", "r1"))
    assert 35 <= age <= 39 and age != 36
    assert eng.surrogate("DATE", "2024-02-10T09:30:00Z", "en_US", "r1").endswith("T09:30:00Z")
    pan = eng.surrogate("PAN_IN", "ABCPK1234F", "en_IN", "r1")
    assert validate("PAN_IN", pan) is True and pan != "ABCPK1234F"


def test_names_are_single_tokens_in_every_locale() -> None:
    f = PersonaFactory(SEED)
    for loc in ("en_US", "en_IN", "en_GB", "en_CA", "es_MX"):
        for i in range(40):
            p = f.for_anchor(f"r{i}", loc)
            assert " " not in p.first_name and " " not in p.last_name
            assert re.fullmatch(r"[a-z]+\.[a-z]+", p.email_local), p.email_local


def test_the_seed_never_shows_in_repr_or_errors() -> None:
    s = generate_synthesis_seed()
    assert s.seed not in repr(s)
    bad = {"alg": "dps.synth.v1", "seed": s.seed[:-3] + "!!!", "created_at": "2026-01-01T00:00:00Z"}
    with pytest.raises(ValueError) as err:
        seed_from_caller(bad)
    assert s.seed[:-3] not in str(err.value)
    with pytest.raises(ValueError) as err2:
        seed_from_caller({"alg": "dps.synth.v1", "seed": 12345678901234567890})
    assert "12345678901234567890" not in str(err2.value)
