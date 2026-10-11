from __future__ import annotations

import hashlib
import math
import random
import re
import string
import time
import unicodedata
from datetime import date

import phonenumbers
import pytest
from stdnum import verhoeff

from dps.engine.detect.checksums import luhn_valid, valid_aba, validate
from dps.engine.synth.personas import (
    PersonaFactory,
    ascii_fold,
    holds_name_part,
    shares_name_part,
)
from dps.engine.synth.reshape import Derangement
from dps.engine.synth.surrogates import (
    SurrogateEngine,
    generate_synthesis_seed,
    seed_bytes,
    seed_from_caller,
)
from dps.engine.verify import name_parts, normalise

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
    eng.declare_record(anchor, [("PERSON_NAME", "Ananya Krishnan")])  # as apply_plan does
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
    assert " " in name and not shares_name_part(name, name_parts(["Ananya Krishnan"]))
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


def test_surrogates_do_not_depend_on_request_order() -> None:
    """Spec §5.6: the same original gets the same fake whatever else is asked, and in any order."""
    medications = ["Metformin", "Insulin", "Warfarin", "Digoxin", "Lithium", "Aspirin"] * 5
    cases: tuple[tuple[str, list[str]], ...] = (
        ("CVV", [f"{i:03d}" for i in range(300)]),  # a 1,000-value space
        ("CUSTOMER_ID", [f"CUST-{i:06d}" for i in range(0, 3_000_000, 1000)]),
        ("ACCOUNT_NUMBER", [str(i) for i in range(10)]),
        ("PHONE", [f"07911 123{i:03d}" for i in range(150)]),  # Ofcom: 1,000 fictional numbers
        ("MEDICATION", [f"{m}{'-XR' * (i % 2)}" for i, m in enumerate(medications)]),
        ("EMAIL", [f"user{i}@corp.example" for i in range(20)]),
    )
    for entity, originals in cases:
        forward, backward = SurrogateEngine(SEED), SurrogateEngine(SEED)
        one = [forward.surrogate(entity, o, "en_GB", "r1") for o in originals]
        two = [backward.surrogate(entity, o, "en_GB", "r1") for o in reversed(originals)][::-1]
        assert one == two, entity
        for original, fake in list(zip(originals, one, strict=True))[-5:]:  # asked alone
            assert SurrogateEngine(SEED).surrogate(entity, original, "en_GB", "r1") == fake


def test_reshaped_identifiers_are_a_keyed_derangement() -> None:
    """Digit- and letter-reshaped identifiers: distinct stays distinct, nothing maps to itself."""
    eng = SurrogateEngine(SEED)
    letters = [a + b for a in string.ascii_uppercase for b in string.ascii_uppercase]
    for entity, originals, whole_space in (
        ("CVV", [f"{i:03d}" for i in range(1000)], True),
        ("ACCOUNT_NUMBER", [str(i) for i in range(10)], True),  # a one-digit space
        ("EMPLOYEE_CODE", letters, True),  # letters only: every letter is replaced
        ("CUSTOMER_ID", [f"CUST-{i:06d}" for i in range(0, 3_000_000, 1000)], False),
        ("SORT_CODE", [f"40-{i:02d}-{i * 7 % 100:02d}" for i in range(100)], False),
    ):
        out = [eng.surrogate(entity, o, "en_US", "r1") for o in originals]
        assert len(set(out)) == len(originals), entity
        assert all(o != s for o, s in zip(originals, out, strict=True)), entity
        if whole_space:  # a permutation of the whole value space
            assert set(out) == set(originals), entity
    cust = eng.surrogate("CUSTOMER_ID", "CUST-884120", "en_US", "r1")
    assert cust.startswith("CUST-") and len(cust) == 11 and cust[5:].isdigit()
    other = SurrogateEngine(seed_bytes(generate_synthesis_seed()))
    assert [other.surrogate("CVV", f"{i:03d}", "en_US", "r1") for i in range(50)] != [
        eng.surrogate("CVV", f"{i:03d}", "en_US", "r1") for i in range(50)
    ]


@pytest.mark.parametrize(
    "radices", [(2,), (10,), (26,), (500,), (2, 2), (3, 7), (10, 10, 10), (26, 10)]
)
def test_the_derangement_is_a_permutation_without_fixed_points(radices: tuple[int, ...]) -> None:
    perm = Derangement(b"k" * 32, radices)
    image = [perm(x) for x in range(perm.size)]
    assert perm.size == math.prod(radices) and sorted(image) == list(range(perm.size))
    assert all(x != y for x, y in enumerate(image))
    assert image == [Derangement(b"k" * 32, radices)(x) for x in range(perm.size)]
    assert image != [Derangement(b"j" * 32, radices)(x) for x in range(perm.size)] or perm.size < 4


def _script(ch: str) -> str:
    return unicodedata.name(ch).split(" ", 1)[0]


@pytest.mark.parametrize(
    ("entity", "original"),
    [
        ("MOTHER_MAIDEN_NAME", "राम"),
        ("NATIONAL_ID", "ПАСПОРТ"),
        ("EMPLOYEE_ID", "Müller"),
        ("NATIONAL_ID", "Ελένη Παπαδοπούλου"),
        ("MOTHER_MAIDEN_NAME", "محمد"),
        ("MOTHER_MAIDEN_NAME", "山田太郎"),
        ("MOTHER_MAIDEN_NAME", "김민준"),
        ("MOTHER_MAIDEN_NAME", "தமிழ்செல்வி"),
        ("MOTHER_MAIDEN_NAME", "Łukasz Żółć"),
        ("EMPLOYEE_ID", "Ё"),
    ],
)
def test_non_ascii_letters_are_replaced_in_their_own_script(entity: str, original: str) -> None:
    """Every letter is replaced, in its own script and case; marks are rebuilt on new letters."""
    eng = SurrogateEngine(SEED)
    out = eng.surrogate(entity, original, "en_IN", "r1")
    assert out != original and original not in out and not out.startswith(original)
    assert normalise(out) != normalise(original) and normalise(original) not in normalise(out)
    before = unicodedata.normalize("NFKD", original)
    after = unicodedata.normalize("NFKD", out)
    assert len(after) == len(before)
    for a, b in zip(before, after, strict=True):
        if a.isalpha():
            assert b.isalpha() and _script(b) == _script(a) and b.isupper() == a.isupper()
        else:  # separators and combining marks (accents, vowel signs) stay
            assert b == a
    assert out == SurrogateEngine(SEED).surrogate(entity, original, "en_IN", "r1")


def test_accented_and_unindexed_latin_letters_are_replaced_not_kept() -> None:
    eng = SurrogateEngine(SEED)
    originals = [f"Mü{a}{b}er" for a in string.ascii_lowercase for b in "aeiouyrs"]
    outs = [eng.surrogate("EMPLOYEE_ID", o, "en_US", "r1") for o in originals]
    kept = sum(unicodedata.normalize("NFKD", out)[1] == "u" for out in outs)
    assert kept < len(originals) // 4  # a replaced letter is "u" again ~1 time in 26, not always
    for out in outs:  # the accent is rebuilt on the new base letter
        assert "\u0308" in unicodedata.normalize("NFKD", out)
    polish = eng.surrogate("MOTHER_MAIDEN_NAME", "Łukasz Łoś", "en_US", "r1")
    assert "Ł" not in polish and "ł" not in polish  # no ASCII base: drawn afresh, never kept


def test_the_last_resort_never_contains_the_original(monkeypatch: pytest.MonkeyPatch) -> None:
    """When every attempt reveals the original, the fallback is a token, never original + 1."""
    eng = SurrogateEngine(SEED)
    monkeypatch.setattr(eng, "_generate", lambda entity, original, *rest: original)
    for original in ("राम", "ПАСПОРТ", "AB-1234", "7"):
        out = eng.surrogate("NATIONAL_ID", original, "en_US", "r1")
        assert len(out) == len(original) and out != original
        assert normalise(original) not in normalise(out) or len(normalise(original)) < 4
        assert not out.startswith(original)


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
    # countries without a fictional range draw from ~10^6 numbers, not 10^4 near one example
    many = {eng.surrogate("PHONE", f"+91 98451 {20000 + i}", "en_IN", "r1") for i in range(40)}
    assert len({p.replace(" ", "")[:8] for p in many}) > 1


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


# --- fix round 2 (G4 review r2): long values, run time, letters of digit-bearing values ---------


def _same_positions(a: str, b: str) -> int:
    return sum(x == y for x, y in zip(a, b, strict=False))


def _longest_common_run(a: str, b: str) -> int:
    longest = run = 0
    for x, y in zip(a, b, strict=False):
        run = run + 1 if x == y else 0
        longest = max(longest, run)
    return longest


def test_long_values_are_mixed_in_every_position() -> None:
    """A long value shares only the chance rate of positions with its surrogate, nowhere a run."""
    rng = random.Random(7)
    letters = "".join(rng.choice(string.ascii_lowercase) for _ in range(1000))
    digits = "".join(rng.choice(string.digits) for _ in range(1000))
    cjk = "".join(chr(rng.randint(0x4E00, 0x9FA5)) for _ in range(300))
    eng = SurrogateEngine(SEED)
    for entity, original, most, run in (
        ("REMARKS", letters, 80, 8),  # chance: ~38 positions (1/26)
        ("ACCOUNT_NUMBER", digits, 160, 12),  # chance: ~100 positions (1/10)
        ("ADDRESS_CN", cjk, 3, 2),  # chance: ~0 positions (1/20,902)
    ):
        out = eng.surrogate(entity, original, "en_US", "r1")
        assert len(out) == len(original), entity
        assert _same_positions(original, out) < most, entity
        assert _longest_common_run(original, out) < run, entity
    # the long letters-only value is still a bijection of its shape: one letter apart stays apart
    other = letters[:500] + ("a" if letters[500] != "a" else "b") + letters[501:]
    assert eng.surrogate("REMARKS", other, "en_US", "r1") != eng.surrogate(
        "REMARKS", letters, "en_US", "r1"
    )


def test_every_block_of_a_long_value_changes_and_distinct_values_stay_distinct() -> None:
    """50 digits are deranged as two blocks of 25: both change, and the mapping is a bijection."""
    rng = random.Random(3)
    originals = list({"".join(rng.choice(string.digits) for _ in range(50)) for _ in range(300)})
    eng = SurrogateEngine(SEED)
    out = [eng.surrogate("ACCOUNT_NUMBER", o, "en_US", "r1") for o in originals]
    assert len(set(out)) == len(originals)
    for original, fake in zip(originals, out, strict=True):
        assert fake[:25] != original[:25] and fake[25:] != original[25:]
    # values sharing their first block share its fake: the blocks of a shape are fixed
    twins = ["1" * 25 + "2" * 25, "1" * 25 + "3" * 25]
    first, second = (eng.surrogate("ACCOUNT_NUMBER", t, "en_US", "r1") for t in twins)
    assert first[:25] == second[:25] and first[25:] != second[25:]


def test_a_derangement_refuses_a_domain_wider_than_its_round_output() -> None:
    with pytest.raises(ValueError):
        Derangement(b"k" * 32, [26] * 200)  # ~940 bits: the reshaper splits this into blocks


def test_reshaping_time_is_linear_in_the_value_length() -> None:
    """A 100,000-character value reshapes in well under a second (no quadratic work)."""
    eng = SurrogateEngine(SEED)
    for entity, original in (
        ("REMARKS", "abcdefghij" * 10_000),
        ("ACCOUNT_NUMBER", "0123456789" * 10_000),
        ("REFERENCE", "AB12-" * 20_000),
    ):
        start = time.perf_counter()
        out = eng.surrogate(entity, original, "en_US", "r1")
        assert time.perf_counter() - start < 1.0, entity
        assert len(out) == len(original) and out != original


TD3 = "P<INDKRISHNAN<<ANANYA<<<<<<<<<<<<<<<<<<<<<<<\nZ1234567<8IND8803143F3001011<<<<<<<<<<<<<<02"
TD2 = "I<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<\nD231458907UTO7408122F1204159<<<<<<<6"
TD1 = (
    "I<UTOD231458907<<<<<<<<<<<<<<<\n7408122F1204159UTO<<<<<<<<<<<6\nERIKSSON<<ANNA<MARIA<<<<<<<<<<"
)


def _icao(field: str) -> str:
    values = [int(c) if c.isdigit() else ord(c) - 55 if c.isalpha() else 0 for c in field]
    return str(sum(v * (7, 3, 1)[i % 3] for i, v in enumerate(values)) % 10)


def test_mrz_surrogates_carry_the_persona_and_valid_check_digits() -> None:
    eng = SurrogateEngine(SEED, default_locale="en_IN")
    out = eng.surrogate("MRZ", TD3, "en_IN", "r1")
    one, two = out.split("\n")
    assert len(one) == len(two) == 44 and re.fullmatch(r"[A-Z0-9<]+", one + two)
    assert "KRISHNAN" not in out and "ANANYA" not in out
    assert one[:5] == "P<IND" and two[10:13] == "IND"  # document code and countries stay
    assert two[:9] != "Z1234567<" and re.fullmatch(r"[A-Z]\d{7}<", two[:9])  # same shape
    assert two[9] == _icao(two[:9]) and two[19] == _icao(two[13:19])
    assert two[27] == _icao(two[21:27]) and two[42] == _icao(two[28:42])
    assert two[43] == _icao(two[:10] + two[13:20] + two[21:43])
    assert two[13:19] != "880314" and 85 <= int(two[13:15]) <= 89  # DOB in its five-year band
    assert two[20] in "FM"
    # an unusual name: the MRZ name is the record's persona, as in its other fields
    zz = eng.surrogate("MRZ", TD3.replace("KRISHNAN<<ANANYA", "ZZYZX<<QWERTYUIO"), "en_IN", "r1")
    persona = eng.persona("r1", "en_IN")
    surname, given = ascii_fold(persona.last_name).upper(), ascii_fold(persona.first_name).upper()
    assert zz.split("\n")[0][5:].startswith(f"{surname}<<{given}"[:39])
    assert zz.split("\n")[1][20] == persona.gender[0].upper()
    assert zz == SurrogateEngine(SEED, default_locale="en_IN").surrogate(
        "MRZ", TD3.replace("KRISHNAN<<ANANYA", "ZZYZX<<QWERTYUIO"), "en_IN", "r1"
    )


def test_td1_td2_and_unknown_mrz_layouts() -> None:
    eng = SurrogateEngine(SEED)
    for original in (TD1, TD2, TD1.replace("\n", ""), TD2.replace("\n", " ")):
        out = eng.surrogate("MRZ", original, "en_US", "r1")
        assert [len(line) for line in out.split()] == [len(line) for line in original.split()]
        for name in ("ERIKSSON", "ANNA", "MARIA", "D23145890"):
            assert name not in out, name
        flat = "".join(out.split())
        assert flat[:5] == "I<UTO"
        if len(flat) == 90:  # TD1
            assert flat[14] == _icao(flat[5:14]) and flat[36] == _icao(flat[30:36])
            assert flat[44] == _icao(flat[38:44]) and flat[45:48] == "UTO"
            assert flat[59] == _icao(flat[5:30] + flat[30:37] + flat[38:45] + flat[48:59])
        else:  # TD2
            assert flat[45] == _icao(flat[36:45]) and flat[55] == _icao(flat[49:55])
            assert flat[63] == _icao(flat[57:63]) and flat[46:49] == "UTO"
            assert flat[71] == _icao(flat[36:46] + flat[49:56] + flat[57:71])
    # not an ICAO layout: every letter and digit is replaced, the filler and the issuer stay
    odd = "P<INDKRISHNAN<<ANANYA<<<<Z1234567"
    out = eng.surrogate("MRZ", odd, "en_US", "r1")
    assert out[:5] == "P<IND" and len(out) == len(odd) and out[13:15] == "<<"
    assert "KRISHNAN" not in out and "ANANYA" not in out and "1234567" not in out


_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_BECH32 = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"


def _b58decode(text: str) -> bytes:
    number = 0
    for ch in text:
        number = number * 58 + _B58.index(ch)
    body = number.to_bytes((number.bit_length() + 7) // 8, "big")
    return b"\0" * (len(text) - len(text.lstrip("1"))) + body


def _bech32_valid(address: str) -> bool:
    """BIP-173 / BIP-350 reference check (bech32 for witness v0, bech32m for later versions)."""
    hrp, data = address.lower().rsplit("1", 1)
    values = [ord(c) >> 5 for c in hrp] + [0] + [ord(c) & 31 for c in hrp]
    values += [_BECH32.index(c) for c in data]
    chk = 1
    for v in values:
        top = chk >> 25
        chk = (chk & 0x1FFFFFF) << 5 ^ v
        for i, g in enumerate((0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3)):
            chk ^= g if (top >> i) & 1 else 0
    return chk == (1 if _BECH32.index(data[0]) == 0 else 0x2BC830A3)


def test_crypto_addresses_are_replaced_and_stay_valid() -> None:
    eng = SurrogateEngine(SEED)
    btc = "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa"
    out = eng.surrogate("CRYPTO_ADDRESS", btc, "en_US", "r1")
    raw = _b58decode(out)
    assert out[0] == "1" and len(raw) == 25 and raw[0] == 0
    assert hashlib.sha256(hashlib.sha256(raw[:21]).digest()).digest()[:4] == raw[21:]
    p2sh = eng.surrogate("CRYPTO_ADDRESS", "3J98t1WpEZ73CNmQviecrnyiWrnqRhWNLy", "en_US", "r1")
    assert p2sh[0] == "3" and _b58decode(p2sh)[0] == 5
    eth = "0x52908400098527886E0F7030069857D2E4169EE7"
    out_eth = eng.surrogate("CRYPTO_ADDRESS", eth, "en_US", "r1")
    assert re.fullmatch(r"0x[0-9a-f]{40}", out_eth)
    bech = "bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4"
    out_bech = eng.surrogate("CRYPTO_ADDRESS", bech, "en_US", "r1")
    assert out_bech.startswith("bc1q") and len(out_bech) == len(bech) and _bech32_valid(out_bech)
    taproot = "bc1p5d7rjq7g6rdk2yhzks9smlaqtedr4dekq08ge8ztwac72sfr9rusxg3297"
    out_tr = eng.surrogate("CRYPTO_ADDRESS", taproot, "en_US", "r1")
    assert out_tr.startswith("bc1p") and len(out_tr) == len(taproot) and _bech32_valid(out_tr)
    xrp = "rEb8TK3gBgk5auZkwc6sHnwrGVJH8DuaLh"  # no dedicated format: every character replaced
    for original in (btc, eth, bech, taproot, xrp):
        fake = eng.surrogate("CRYPTO_ADDRESS", original, "en_US", "r1")
        assert fake != original and _same_positions(original, fake) < len(original) / 3, fake
        assert fake == SurrogateEngine(SEED).surrogate("CRYPTO_ADDRESS", original, "en_US", "r1")


def test_digit_bearing_values_keep_only_short_codes() -> None:
    """Letters of an identifier stay only as a short code (CUST-, MH14); names never stay."""
    eng = SurrogateEngine(SEED)
    note = "Customer Ananya Krishnan called on 5 Jan about loan 4412 and asked for Rahul Mehta"
    out = eng.surrogate("CALL_NOTE", note, "en_US", "r1")
    for name in ("Ananya", "Krishnan", "Rahul", "Mehta", "Customer"):
        assert name not in out, name
    assert [len(w) for w in out.split(" ")] == [len(w) for w in note.split(" ")]
    assert sum(a == b for a, b in zip(note.split(), out.split(), strict=True)) <= 2
    cust = eng.surrogate("CUSTOMER_ID", "CUST-884120", "en_US", "r1")
    assert cust.startswith("CUST-") and cust[5:].isdigit() and cust != "CUST-884120"
    reg = eng.surrogate("VEHICLE_REG", "MH14AB1234", "en_US", "r1")
    assert reg[:2] == "MH" and reg[4:6] == "AB" and reg != "MH14AB1234"
    for original, name in (
        ("Ravi 12345678", "Ravi"),  # a word, not a code
        ("KRISHNAN-1234", "KRISHNAN"),  # longer than a code
        ("山田 12345678", "山田"),  # not a Latin code
        ("REF-ANANYA-77", "ANANYA"),
    ):
        fake = eng.surrogate("REFERENCE", original, "en_US", "r1")
        assert name not in fake and len(fake) == len(original), original
    ifsc = eng.surrogate("IFSC", "ABCD0EFGHIJ", "en_IN", "r1")  # an all-letter branch code
    assert ifsc[:5] == "ABCD0" and ifsc[5:].isalpha() and ifsc[5:] != "EFGHIJ"
    assert eng.surrogate("AMOUNT", "INR 12,500.00", "en_IN", "r1").startswith("INR ")


# --- a persona never carries a part of the record's own names (A-prepare review r1, B1) -------
# Under en_IN, Faker can draw the customer's own surname, or one that contains it. These seeds do
# so for UNIT_ANCHOR: 147 draws "Krishnan" itself and 31 draws "Ramakrishnan". Each test first
# checks that the bare persona really collides, so a Faker data change cannot make it vacuous.
UNIT_ANCHOR = "CUSTOMER_ID:regression"
NAME = "Ananya Krishnan"
COLLIDING = (147, 31)


def _collision_seed(i: int) -> bytes:
    return hashlib.sha256(f"dps-en-in-collision-{i}".encode()).digest()


def _no_name_part(text: str) -> bool:
    folded = normalise(text)
    return "ananya" not in folded and "krishnan" not in folded


@pytest.mark.parametrize("i", COLLIDING)
def test_an_en_in_persona_never_shares_a_name_part_with_the_record(i: int) -> None:
    seed = _collision_seed(i)
    bare = PersonaFactory(seed).for_anchor(UNIT_ANCHOR, "en_IN")
    assert "krishnan" in ascii_fold(bare.last_name), "seed no longer collides: pick another"
    eng = SurrogateEngine(seed, default_locale="en_IN")
    eng.declare_record(UNIT_ANCHOR, [("PERSON_NAME", NAME)])
    persona = eng.persona(UNIT_ANCHOR, "en_IN")
    assert not shares_name_part(persona.full_name, name_parts([NAME]))
    full = eng.surrogate("PERSON_NAME", NAME, "en_IN", UNIT_ANCHOR)
    first = eng.surrogate("PERSON_NAME", "Ananya", "en_IN", UNIT_ANCHOR)
    upper = eng.surrogate("PERSON_NAME", NAME.upper(), "en_IN", UNIT_ANCHOR)
    email = eng.surrogate("EMAIL", "ananya.k@example-mail.in", "en_IN", UNIT_ANCHOR)
    user = eng.surrogate("USERNAME", "@ananya_k", "en_IN", UNIT_ANCHOR)
    for out in (full, first, upper, email, user):
        assert _no_name_part(out), "a part of the customer's name survived"
    # still one coherent persona, and the same one from a fresh engine
    assert full == persona.full_name and first == persona.first_name and upper == full.upper()
    assert email.startswith(persona.email_local) and user[1:].startswith(
        persona.email_local.replace(".", "_")
    )
    fresh = SurrogateEngine(seed, default_locale="en_IN")
    fresh.declare_record(UNIT_ANCHOR, [("PERSON_NAME", NAME)])
    assert fresh.surrogate("EMAIL", "ananya.k@example-mail.in", "en_IN", UNIT_ANCHOR) == email
    assert fresh.surrogate("PERSON_NAME", NAME, "en_IN", UNIT_ANCHOR) == full


@pytest.mark.parametrize("i", COLLIDING)
def test_a_name_surrogate_never_shares_a_part_with_the_name_it_replaces(i: int) -> None:
    """Even when nobody declared the record's names, the name's own parts are avoided."""
    eng = SurrogateEngine(_collision_seed(i), default_locale="en_IN")
    assert _no_name_part(eng.surrogate("PERSON_NAME", NAME, "en_IN", UNIT_ANCHOR))
    assert _no_name_part(eng.surrogate("PERSON_NAME", "Krishnan", "en_IN", UNIT_ANCHOR))


def test_a_persona_that_does_not_collide_is_the_same_with_or_without_the_names() -> None:
    seed = _collision_seed(0)
    bare = PersonaFactory(seed).for_anchor(UNIT_ANCHOR, "en_IN")
    assert not shares_name_part(bare.full_name, name_parts([NAME]))
    assert PersonaFactory(seed).for_anchor(UNIT_ANCHOR, "en_IN", avoid=name_parts([NAME])) == bare
    eng = SurrogateEngine(seed, default_locale="en_IN")
    eng.declare_record(UNIT_ANCHOR, [("PERSON_NAME", NAME)])
    assert eng.persona(UNIT_ANCHOR, "en_IN") == bare


def test_a_declaration_replaces_the_earlier_one_and_reaches_the_retries() -> None:
    seed = _collision_seed(COLLIDING[0])
    eng = SurrogateEngine(seed, default_locale="en_IN")
    eng.declare_record(UNIT_ANCHOR, [("PERSON_NAME", NAME)])
    assert not shares_name_part(eng.persona(UNIT_ANCHOR, "en_IN").full_name, name_parts([NAME]))
    retry = f"{UNIT_ANCHOR}|retry1"
    assert eng.persona(retry, "en_IN") == PersonaFactory(seed).for_anchor(
        retry, "en_IN", avoid=name_parts([NAME])
    )
    eng.declare_record(UNIT_ANCHOR, [])
    assert eng.persona(UNIT_ANCHOR, "en_IN") == PersonaFactory(seed).for_anchor(
        UNIT_ANCHOR, "en_IN"
    )


def test_the_name_part_rule() -> None:
    parts = name_parts(["Ananya Krishnan", "A. Acuña"])
    assert parts == {"ananya", "krishnan", "a", "acuna"}
    assert shares_name_part("Krishnan", parts)  # equal
    assert shares_name_part("Ramakrishnan", parts)  # contains a part
    assert shares_name_part("Krish", parts) and shares_name_part("Anya", parts)  # inside a part
    assert shares_name_part("ACUNA", parts) and shares_name_part("Acuña", parts)  # folded
    assert shares_name_part("A", parts)  # an initial matches an equal part...
    assert not shares_name_part("Arjun Mehta", parts)  # ...and nothing else
    assert not shares_name_part("Kr", {"krishnan"})  # under three letters: equal only
    assert not shares_name_part("Krishnan", frozenset())
    # text that is not a name: a word holding a part, or the text run together holding one
    assert holds_name_part("12 Krishnan Road", parts) and holds_name_part(
        "Radhakrishnan Chowk", parts
    )
    assert holds_name_part("30, Krishna Nagar", parts)  # "krishnanagar"
    assert not holds_name_part("Krishna Road", parts)  # a word inside a part is a name's test


def test_a_name_draw_falls_back_deterministically() -> None:
    from dps.engine.synth.personas import _draw_name

    avoid = name_parts(["Krishnan"])
    assert _draw_name(lambda: "Krishnan", ("Alex",), avoid) == "Alex"
    # nor run together with the first name: "Sanan" + "Yash" holds "ananya"
    assert _draw_name(lambda: "Yash", ("Alex",), name_parts(["Ananya"]), before="Sanan") == "Alex"
    every = name_parts(["Krishnan Alex Robin"])
    got = _draw_name(lambda: "Krishnan", ("Alex", "Robin"), every)
    assert not shares_name_part(got, every) and got == _draw_name(
        lambda: "Krishnan", ("Alex", "Robin"), every
    )


@pytest.mark.parametrize(
    "i",
    [
        48,  # UNIT_ANCHOR's bare en_IN street: "03/03, Balakrishnan Street"
        4113,  # "30, Krishna Nagar": no word holds "krishnan", the normalised text does
    ],
)
def test_a_declared_record_moves_the_address_off_the_customer_s_name(i: int) -> None:
    seed = _collision_seed(i)
    bare = PersonaFactory(seed).for_anchor(UNIT_ANCHOR, "en_IN")
    assert "krishnan" in normalise(bare.street), "seed no longer collides: pick another"
    eng = SurrogateEngine(seed, default_locale="en_IN")
    eng.declare_record(UNIT_ANCHOR, [("PERSON_NAME", NAME)])
    persona = eng.persona(UNIT_ANCHOR, "en_IN")
    assert (persona.first_name, persona.last_name) == (bare.first_name, bare.last_name)
    street = eng.surrogate(
        "STREET_ADDRESS", "14 Lavelle Road, Bengaluru 560001", "en_IN", UNIT_ANCHOR
    )
    assert _no_name_part(street) and street.startswith(persona.street)


@pytest.mark.parametrize(
    ("i", "entity", "original"),
    [(2757, "PHONE", "+91 98451 20377"), (544, "CARD_PAN", "4111 1111 1111 1111")],
)
def test_a_surrogate_never_holds_another_value_of_the_record(
    i: int, entity: str, original: str
) -> None:
    """A phone or card surrogate holding the card expiry's digits used to trip the sweep."""
    seed = _collision_seed(i)
    bare = SurrogateEngine(seed).surrogate(entity, original, "en_IN", UNIT_ANCHOR)
    assert "0928" in normalise(bare), "seed no longer collides: pick another"
    eng = SurrogateEngine(seed, default_locale="en_IN")
    eng.declare_record(UNIT_ANCHOR, [("CARD_EXPIRY", "09/28"), (entity, original)])
    fake = eng.surrogate(entity, original, "en_IN", UNIT_ANCHOR)
    assert "0928" not in normalise(fake) and fake != bare
    if entity == "PHONE":
        assert phonenumbers.is_valid_number(phonenumbers.parse(fake, None))
    else:
        assert fake.startswith("4111 11") and luhn_valid(fake)
    # the same record declared again gives the same surrogate
    again = SurrogateEngine(seed, default_locale="en_IN")
    again.declare_record(UNIT_ANCHOR, [("CARD_EXPIRY", "09/28"), (entity, original)])
    assert again.surrogate(entity, original, "en_IN", UNIT_ANCHOR) == fake


def test_a_value_held_by_construction_keeps_the_first_draw() -> None:
    """Every card surrogate starts 411111: declaring "4111" cannot be avoided, and must not
    push the card to the last-resort token. The sweep rules on it instead."""
    original = "4532 7811 2290 4417"
    bare = SurrogateEngine(SEED).surrogate("CARD_PAN", original, "en_US", "r1")
    eng = SurrogateEngine(SEED)
    eng.declare_record("r1", [("PIN", "4111"), ("CARD_PAN", original)])
    assert eng.surrogate("CARD_PAN", original, "en_US", "r1") == bare and luhn_valid(bare)
