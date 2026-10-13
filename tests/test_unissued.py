"""Government-identifier surrogates come from never-issued ranges (M5 verify r2, D-A §2.4).

A keyed permutation of the issued space maps real identifiers onto other real ones: the demo's
``bank.customers`` synthesize runs wrote 0-3 real source NINOs per run into the target. Every
assertion here compares surrogates with their originals (counts only).
"""

from __future__ import annotations

import random
import re

import pytest
from stdnum.mx import curp as stdnum_curp

from dps.engine.detect.checksums import luhn_valid, validate, verhoeff_check_digit
from dps.engine.synth import unissued
from dps.engine.synth.surrogates import SurrogateEngine
from dps.engine.synth.unissued import national_shape, never_issued
from dps.engine.verify import normalise

SEED = b"\x07" * 32


def _demo_ninos(n: int, seed: int = 11) -> list[str]:
    """NINOs the way the demo seed draws them (HMRC's example prefix QQ)."""
    rng = random.Random(seed)
    return sorted({f"QQ{rng.randrange(1_000_000):06d}{rng.choice('ABCD')}" for _ in range(n)})


def _real_format_ninos(n: int) -> list[str]:
    rng = random.Random(5)
    first, second = "ABCEGHJKLMNOPRSTWXYZ", "ABCEGHJKLMNPRSTWXYZ"
    out: set[str] = set()
    while len(out) < n:
        prefix = rng.choice(first) + rng.choice(second)
        if prefix in {"BG", "GB", "NK", "KN", "TN", "NT", "ZZ"}:
            continue
        out.add(f"{prefix}{rng.randrange(1_000_000):06d}{rng.choice('ABCD')}")
    return sorted(out)


@pytest.mark.parametrize("entity", ["NINO", "NATIONAL_ID"])
def test_nino_surrogates_never_land_on_a_source_nino(entity: str) -> None:
    """20,000 demo-style QQ NINOs: no surrogate is one of them (the old reshape kept QQ and
    landed on ~n²/4·10⁶ ≈ 100 of them), and every surrogate is never issued."""
    originals = _demo_ninos(20_000)
    eng = SurrogateEngine(SEED)
    fakes = [eng.surrogate(entity, o, "en_GB", f"r{i}") for i, o in enumerate(originals)]
    survivors = len(set(fakes) & set(originals))
    assert survivors == 0
    assert all(never_issued("NINO", f) and never_issued(entity, f) for f in fakes)
    assert all(re.fullmatch(r"[A-Z]{2}\d{6}[A-D]", f) for f in fakes)
    assert not any(f.startswith("QQ") for f in fakes)  # test fixtures' prefix is avoided too
    assert all(validate("NINO", f) is False for f in fakes[:500])  # HMRC never allocates them
    assert len(set(fakes)) > len(fakes) * 0.99  # a value-level map with few chance collisions


def test_real_format_ninos_map_outside_the_issued_space() -> None:
    originals = _real_format_ninos(3000)
    assert all(validate("NINO", o) is True for o in originals)
    eng = SurrogateEngine(SEED)
    fakes = [eng.surrogate("NINO", o, "en_GB", "r") for o in originals]
    assert not any(validate("NINO", f) for f in fakes)
    assert all(never_issued("NINO", f) for f in fakes)
    assert not any(never_issued("NINO", o) for o in originals)


def test_nino_layout_and_value_consistency() -> None:
    eng = SurrogateEngine(SEED)
    spaced = eng.surrogate("NINO", "AB 12 34 56 C", "en_GB", "r1")
    assert re.fullmatch(r"[A-Z]{2} \d\d \d\d \d\d [A-D]", spaced), spaced
    assert normalise(spaced) == normalise(eng.surrogate("NINO", "AB123456C", "en_GB", "r9"))
    assert re.fullmatch(r"[A-Z]{2}\d{6}", eng.surrogate("NINO", "AB123456", "en_GB", "r1"))


def test_curp_surrogates_start_with_a_word_renapo_never_issues() -> None:
    rng = random.Random(3)
    eng = SurrogateEngine(SEED)
    for _ in range(300):
        body = (
            "".join(rng.choice("BCDGLMPRS") for _ in range(4))
            + f"{rng.randint(50, 99):02d}0{rng.randint(1, 9)}1{rng.randint(0, 9)}"
            + rng.choice("HM")
            + rng.choice(("DF", "NL", "JC"))
            + "BCD0"
        )
        original = body + str(stdnum_curp.calc_check_digit(body))
        for entity in ("CURP", "NATIONAL_ID"):
            fake = eng.surrogate(entity, original, "es_MX", "r1")
            assert fake != original and len(fake) == 18
            assert fake[:4] in unissued.CURP_NEVER_WORDS
            assert fake[-1] == str(stdnum_curp.calc_check_digit(fake[:-1]))
            assert never_issued(entity, fake) and validate("CURP", fake) is False


def test_generic_national_ids_by_sub_shape() -> None:
    eng = SurrogateEngine(SEED)
    aad = eng.surrogate("NATIONAL_ID", "2345 6789 0124", "en_IN", "r1")
    assert re.fullmatch(r"1\d{3} \d{4} \d{4}", aad), aad
    flat = aad.replace(" ", "")
    assert flat[-1] == verhoeff_check_digit(flat[:-1])
    tin = eng.surrogate("NATIONAL_ID", "912-34-5678", "en_US", "r1")
    assert re.fullmatch(r"000-\d\d-\d{4}", tin) and luhn_valid(tin.replace("-", ""))
    sin = eng.surrogate("NATIONAL_ID", "046 454 286", "en_CA", "r1")
    assert re.fullmatch(r"000 \d{3} \d{3}", sin)
    nhs = eng.surrogate("NATIONAL_ID", "943 476 5919", "en_GB", "r1")
    assert nhs.startswith("999 ") and validate("NHS_NUMBER", nhs) is True
    for fake in (aad, tin, sin, nhs):
        assert never_issued("NATIONAL_ID", fake), fake
    for original in ("234567890124", "912345678", "9434765919", "QQ123456A"):
        assert national_shape(original) is not None
    # no known range: reshaped (the post-write check reviews it), never claimed unissued
    passport_like = eng.surrogate("NATIONAL_ID", "K1234567", "en_IN", "r1")
    assert passport_like != "K1234567" and national_shape("K1234567") is None
    assert not never_issued("NATIONAL_ID", passport_like)


def test_tins_keep_out_of_every_issued_ssn_itin_and_atin_group() -> None:
    eng = SurrogateEngine(SEED)
    for i in range(300):
        ssn = eng.surrogate("SSN", f"123-45-{1000 + i}", "en_US", "r1")
        itin = eng.surrogate("ITIN", f"912-7{i % 10}-{1000 + i}", "en_US", "r1")
        assert ssn[0] == "9" and 1 <= int(ssn[4:6]) <= 49, ssn
        assert itin[0] == "9" and itin[4:6] == "89", itin  # 93 is the ATIN group
        assert never_issued("SSN", ssn) and never_issued("ITIN", itin)
    assert not never_issued("SSN", "123-45-6789") and not never_issued("ITIN", "912-70-1234")


def test_nhs_ein_pan_and_rfc_ranges() -> None:
    eng = SurrogateEngine(SEED)
    for i in range(200):
        nhs = eng.surrogate("NHS_NUMBER", f"943 476 {5900 + i:04d}", "en_GB", "r1")
        assert nhs.startswith("999") and validate("NHS_NUMBER", nhs) is True
        ein = eng.surrogate("EIN", f"12-34{i:05d}", "en_US", "r1")
        assert re.fullmatch(r"\d\d-\d{7}", ein) and ein[:2] in unissued.EIN_NEVER_PREFIXES
        pan = eng.surrogate("PAN_IN", f"ABCPK{1000 + i:04d}F", "en_IN", "r1")
        assert re.fullmatch(r"[A-Z]{3}P[A-Z]0000[A-Z]", pan) and validate("PAN_IN", pan) is False
        rfc = eng.surrogate("RFC", f"GODE56{i % 9 + 1:02d}{i % 27 + 1:02d}AB1", "es_MX", "r1")
        assert rfc[:4] in unissued.RFC_NEVER_WORDS and len(rfc) == 13
        for entity, fake in (("NHS_NUMBER", nhs), ("EIN", ein), ("PAN_IN", pan), ("RFC", rfc)):
            assert never_issued(entity, fake), entity


def test_types_without_a_never_issued_range_are_never_claimed_unissued() -> None:
    eng = SurrogateEngine(SEED)
    for entity, original in (
        ("PASSPORT", "K1234567"),
        ("DRIVER_LICENSE", "MH14 20110062821"),
        ("VOTER_ID", "ABC1234567"),
    ):
        fake = eng.surrogate(entity, original, "en_IN", "r1")
        assert fake != original
        assert never_issued(entity, fake) is False and never_issued(entity, original) is False
    assert "PASSPORT" not in unissued.UNISSUED_ENTITIES


def test_the_demo_mixed_national_id_column_has_no_survivor() -> None:
    """The demo's ``national_id`` mix (Aadhaar, SSN, QQ NINO, CURP): no surrogate is a source
    value and every surrogate is in a never-issued range."""
    rng = random.Random(9)
    originals = _demo_ninos(2000) + ["123412341234", "912-34-5678"]
    for _ in range(500):
        body = f"GOME{rng.randint(50, 99):02d}0{rng.randint(1, 9)}1{rng.randint(0, 9)}HDFRLR0"
        originals.append(body + str(stdnum_curp.calc_check_digit(body)))
    eng = SurrogateEngine(SEED)
    fakes = [eng.surrogate("NATIONAL_ID", o, "en_GB", f"r{i}") for i, o in enumerate(originals)]
    survivors = len({normalise(f) for f in fakes} & {normalise(o) for o in originals})
    issuable = sum(not never_issued("NATIONAL_ID", f) for f in fakes)
    assert (survivors, issuable) == (0, 0)
