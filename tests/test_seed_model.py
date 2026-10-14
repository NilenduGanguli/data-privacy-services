"""The shared demo customer space (offline): deterministic, rule-valid fakes, one id space."""

from __future__ import annotations

import re

from stdnum import verhoeff
from stdnum.mx import curp

from dps.engine.synth.surrogates import RFC2606_DOMAINS
from tools.seed_model import CUSTOMER_ID_BASE, seed_customers


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
        if c.id_type == "AADHAAR":  # UIDAI never issues a leading 0 or 1
            assert re.fullmatch(r"1\d{11}", c.national_id) and verhoeff.is_valid(c.national_id)
        elif c.id_type == "SSN":  # the 9xx area is never issued
            assert re.fullmatch(r"9\d{2}-\d{2}-\d{4}", c.national_id)
        elif c.id_type == "NINO":  # HMRC's example prefix
            assert re.fullmatch(r"QQ\d{6}[A-D]", c.national_id)
        else:
            assert c.id_type == "CURP" and curp.is_valid(c.national_id)
        assert len(c.national_id) <= 18  # Oracle NATIONAL_ID VARCHAR2(18), PG varchar(18)
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
