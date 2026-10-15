"""The shared demo customer space (offline): deterministic, rule-valid fakes, one id space."""

from __future__ import annotations

import random
import re
from collections.abc import Callable

from stdnum import verhoeff
from stdnum.mx import curp

from dps.engine.synth import unissued
from dps.engine.synth.surrogates import RFC2606_DOMAINS, SurrogateEngine
from dps.engine.synth.unissued import compact, never_issued
from tools.seed_model import CUSTOMER_ID_BASE, DEMO_MODEL_VERSION, demo_seed_bytes, seed_customers


def test_deterministic_and_dense_ids() -> None:
    a, b = list(seed_customers(120, 7)), list(seed_customers(120, 7))
    assert a == b
    assert [c.customer_id for c in a] == list(range(CUSTOMER_ID_BASE + 1, CUSTOMER_ID_BASE + 121))
    other = list(seed_customers(5, 8))
    assert [c.customer_id for c in other] == [c.customer_id for c in a[:5]]
    assert [(c.full_name, c.email) for c in other] != [(c.full_name, c.email) for c in a[:5]]


def test_a_customer_depends_only_on_its_index_and_the_seed() -> None:
    """``--rows 200`` is a prefix of ``--rows 2400``: the targets of one demo always line up."""
    assert list(seed_customers(30, 7)) == list(seed_customers(80, 7))[:30]


def test_values_are_rule_valid_and_never_real() -> None:
    seen: set[str] = set()
    customers = list(seed_customers(300, 7))
    for c in customers:
        seen.add(c.id_type)
        assert c.email.rsplit("@", 1)[1] in RFC2606_DOMAINS
        assert 10**7 <= c.customer_id < 10**8
        if c.id_type == "AADHAAR":  # UIDAI never issues a leading 0 (surrogates take the 1)
            assert re.fullmatch(r"0\d{11}", c.national_id) and verhoeff.is_valid(c.national_id)
        elif c.id_type == "SSN":  # area 666 is never issued (surrogates take 9xx)
            assert re.fullmatch(r"666-\d{2}-\d{4}", c.national_id), c.index
            assert c.national_id[4:6] != "00" and c.national_id[7:] != "0000"
        elif c.id_type == "NINO":  # HMRC's example prefix
            assert re.fullmatch(r"QQ\d{6}[A-D]", c.national_id)
        else:
            assert c.id_type == "CURP" and curp.is_valid(c.national_id)
        assert len(c.national_id) <= 18  # Oracle NATIONAL_ID VARCHAR2(18), PG varchar(18)
        if c.id_type != "CURP":  # a demo CURP is rule-valid, not from a never-issued range
            assert never_issued(c.id_type, c.national_id), c.index  # nobody's real identifier
        if c.country == "IN":
            assert c.tax_id is not None and re.fullmatch(r"[A-Z]{3}P[A-Z]\d{4}[A-Z]", c.tax_id)
        else:
            assert c.tax_id is None
        assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{9}", c.last_contact_at)
        assert c.email in c.notes and c.mongo_customer_id == f"CUST-{c.customer_id}"
        assert c.full_name == f"{c.first_name} {c.last_name}".strip()
        assert 1 <= c.risk_score <= 999
    assert seen == {"AADHAAR", "SSN", "NINO", "CURP"}
    for field in ("email", "mobile", "national_id", "full_name"):  # per-customer templates
        distinct = len({getattr(c, field) for c in customers})
        assert distinct >= 0.95 * len(customers), field


def test_large_astral_notes_every_fiftieth_customer() -> None:
    customers = list(seed_customers(120, 7))
    big = [c for c in customers if c.index % 50 == 0]
    assert len(big) == 3 and all(len(c.notes) > 40_000 and "\U0001f600" in c.notes for c in big)
    assert all(len(c.notes) < 1_000 for c in customers if c.index % 50)


# --- D-A: the demo ids lie where no surrogate is ever drawn (S2 gates r1 B1) --------------------
#: What the surrogate engine can draw for each identifier type the demo seeds, by compact form
#: (``unissued.py`` and ``SurrogateEngine``): the D-A ranges. A demo value inside one of them can be
#: written by a synthesize run of the demo table, which the run's identifier check then (rightly)
#: counts ``in_source`` — the seeded-demo D-A tests failed at random for that reason.
DRAWS: dict[str, Callable[[str], bool]] = {
    # SurrogateEngine._aadhaar and unissued.twelve_digits: 1 + 10 digits + Verhoeff
    "AADHAAR": lambda f: len(f) == 12 and f.isdigit() and f[0] == "1" and verhoeff.is_valid(f),
    # unissued.tin_digits: area 9xx, groups 01-49 (SSN) or 89 (ITIN), serial 0001-9999
    "SSN": lambda f: len(f) == 9 and f.isdigit() and f[0] == "9" and int(f[3:5]) in range(1, 50),
    "ITIN": lambda f: len(f) == 9 and f.isdigit() and f[0] == "9" and f[3:5] == "89",
    # unissued.nino: a never-allocated prefix other than QQ
    "NINO": lambda f: f[:2] in unissued.NINO_PREFIXES,
    # unissued.curp: an inconvenient word first
    "CURP": lambda f: f[:4] in unissued.CURP_NEVER_WORDS,
    # SurrogateEngine._pan_in: sequence number 0000
    "PAN_IN": lambda f: len(f) == 10 and f[5:9] == "0000",
}


def _national_draws(flat: str) -> bool:
    """Whether a generic NATIONAL_ID surrogate (``unissued.national_id``) can be ``flat``."""
    shape = unissued.national_shape(flat)
    if shape == "AADHAAR":
        return DRAWS["AADHAAR"](flat)
    if shape == "TIN9":  # unissued.nine_digits: 000 + 5 digits + Luhn
        return flat.startswith("000")
    if shape == "NHS":
        return flat.startswith("999")
    if shape == "CURP":
        return DRAWS["CURP"](flat)
    if shape == "NINO":
        return DRAWS["NINO"](flat)
    if shape == "RFC":
        return flat[:4] in unissued.RFC_NEVER_WORDS
    return False


def test_the_draw_ranges_hold_every_surrogate_the_engine_draws() -> None:
    """The ranges above are the engine's: 400 surrogates of each type, from four seeds, all lie in
    them (if a generator moves, this fails before the disjointness test can pass by mistake)."""
    originals = {
        "AADHAAR": "234123412346",
        "SSN": "123-45-6789",
        "ITIN": "912-70-1234",
        "NINO": "AB123456C",
        "CURP": "GOME800101HDFRLR05",
        "PAN_IN": "ABCPE1234F",
    }
    for s in range(4):
        eng = SurrogateEngine(random.Random(s).randbytes(32), default_locale="en_IN")
        for i in range(100):
            for entity, original in originals.items():
                fake = compact(eng.surrogate(entity, f"{original}", "en_IN", f"r{i}|{s}"))
                assert DRAWS[entity](fake), entity
                # vary the original so each draw is a fresh one
                originals[entity] = original[:-1] + str((int(original[-1], 36) + 1) % 10)
            # one original of each recognised sub-shape (Aadhaar, 9 digits, NINO, CURP), varied
            for varied in (
                f"2{i:011d}",
                f"{100 + i:03d}-45-6789",
                f"AB{i:06d}C",
                f"GOME80{i % 100:02d}01HDFRLR05",
            ):
                assert unissued.national_shape(varied) is not None
                fake = compact(eng.surrogate("NATIONAL_ID", varied, "en_IN", f"n{i}|{s}"))
                assert _national_draws(fake), unissued.national_shape(varied)


def test_no_seeded_identifier_lies_where_a_surrogate_can_draw() -> None:
    """The compose demo (seed 7, 20,000 customers) and two other seeds: no national id lies in a
    range a surrogate of its type or of NATIONAL_ID draws, no PAN in the PAN surrogates' range, and
    each is still never issued (nobody's identifier). So a synthesize run of the demo table can
    write no value the demo holds, whatever its seed: deterministic, not a 1-in-70 chance."""
    assert DEMO_MODEL_VERSION == 2
    for seed, rows in ((7, 20_000), (8, 3_000), (9, 3_000)):
        customers = list(seed_customers(rows, seed))
        assert {c.id_type for c in customers} == {"AADHAAR", "SSN", "NINO", "CURP"}
        for c in customers:
            flat = compact(c.national_id)
            assert not DRAWS[c.id_type](flat), (seed, c.index, c.id_type)
            assert not _national_draws(flat), (seed, c.index, c.id_type)
            if c.id_type == "SSN":
                assert not DRAWS["ITIN"](flat), (seed, c.index)
            if c.tax_id is not None:
                assert not DRAWS["PAN_IN"](compact(c.tax_id)), (seed, c.index)
        ssns = [c.national_id for c in customers if c.id_type == "SSN"]
        assert len(set(ssns)) == len(ssns)  # one SSN per customer


def test_demo_ssns_depend_on_the_seed_and_stay_distinct() -> None:
    from tools.seed_model import demo_ssn

    seven, eight = demo_seed_bytes(7), demo_seed_bytes(8)
    ids = range(CUSTOMER_ID_BASE + 1, CUSTOMER_ID_BASE + 200_001)
    assert len({demo_ssn(i, seven) for i in ids}) == len(ids)
    assert [demo_ssn(i, seven) for i in ids[:50]] != [demo_ssn(i, eight) for i in ids[:50]]
