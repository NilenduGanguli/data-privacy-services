"""One synthetic customer space shared by the PostgreSQL, Oracle and MongoDB demo data (M3 Task 15).

Every personal value is produced by the M0 surrogate engine (seeded with
``sha256("dps-demo-<seed>")``, the M2 demo seed) from a per-customer template original, anchored on
the customer (``CUSTOMER_ID:<hash>``), so it is rule-valid and never real: RFC 2606 e-mails,
check-digit-valid CURPs, rule-valid PANs and fictional phone ranges.

The national ids sit in never-issued ranges that **no D-A surrogate draws from** (S2 gates r1 B1):
a synthesize run of the demo table must never write a value the demo source holds, or its post-write
identifier check (rightly) asks for review and the seeded-demo D-A tests fail at random. So the
Aadhaar-shaped numbers start with ``0`` (UIDAI never issues 0 or 1; AADHAAR and 12-digit
NATIONAL_ID surrogates start with ``1``), the SSNs are in area ``666`` (never issued; SSN and ITIN
surrogates take area 9xx, 9-digit NATIONAL_ID surrogates ``000``), the NINOs carry HMRC's example
prefix ``QQ`` (never a surrogate prefix), every CURP has ``X`` as its third letter (no inconvenient
word that CURP surrogates start with has one) and the PANs a sequence number ``0001``-``9999``
(surrogates: ``0000``). :data:`DEMO_MODEL_VERSION` marks what a seeded schema holds; the seed tools
reload a schema seeded by an older model. Customer ``i`` has id ``10_000_001 + i`` (8 digits, so ``fpe_digits`` has ≥ 6 free digits)
and every attribute of customer ``i`` depends only on ``(i, seed)`` — not on ``rows`` and not on
the other customers — so the same customer carries the same name, e-mail, phone, birth date and
national id in every target. That is what lets the three-way consistency join line up.
"""

from __future__ import annotations

import hashlib
import random
import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Final

CUSTOMER_ID_BASE: Final = 10_000_000
#: The demo data model's version, stored with every seed marker (PG ``_dps_seed_info``, Oracle
#: ``CRM.DPS_SEED_INFO``, MongoDB ``dps_seed_info``): a schema seeded by an older model is reloaded.
#: 2: national ids moved out of every range a D-A surrogate draws (S2 gates r1 B1).
DEMO_MODEL_VERSION: Final = 2
#: Area 666 SSNs: groups 01-99 x serials 0001-9999.
_SSN_666_SPACE: Final = 99 * 9_999
#: A prime that does not divide :data:`_SSN_666_SPACE` (3**4 * 11**2 * 101): customer ids map to
#: distinct SSNs for any run of fewer than 989,901 consecutive customers.
_SSN_STRIDE: Final = 104_729
#: (country, locale, weight %) — the M2 demo bank's customer mix.
COUNTRIES: Final = (
    ("IN", "en_IN", 60),
    ("US", "en_US", 20),
    ("GB", "en_GB", 10),
    ("MX", "es_MX", 10),
)
ID_TYPE: Final = {"IN": "AADHAAR", "US": "SSN", "GB": "NINO", "MX": "CURP"}
SEGMENTS: Final = ("retail", "retail", "retail", "premium", "private", "sme")
CURP_STATES: Final = ("DF", "JC", "NL", "PL", "MC")
ASTRAL: Final = "\U0001f600"
#: Every 50th customer's notes exceed 40,000 characters (an Oracle CLOB above 32 KiB) with
#: supplementary-plane characters in it (UTF-16 surrogate pairs).
BIG_NOTES_EVERY: Final = 50


def demo_seed_bytes(seed: int) -> bytes:
    """The surrogate engine's seed for ``--seed``: ``sha256("dps-demo-<seed>")`` (M2's)."""
    return hashlib.sha256(b"dps-demo-%d" % seed).digest()


def customer_anchor(customer_id: int) -> str:
    """The record anchor of a customer (M2's form: ``CUSTOMER_ID:<hash of the id>``)."""
    from dps.engine.findings import hash_value

    return f"CUSTOMER_ID:{hash_value(str(customer_id))}"


@dataclass(frozen=True)
class SeedCustomer:
    """One demo customer; every field is a rule-valid fake."""

    index: int
    customer_id: int
    locale: str
    country: str
    full_name: str
    first_name: str
    last_name: str
    email: str
    mobile: str
    dob: date
    id_type: str
    national_id: str
    tax_id: str | None
    addr_line1: str
    city: str
    pin_code: str
    segment: str
    risk_score: int
    created_at: datetime
    last_contact_at: str
    notes: str

    @property
    def mongo_customer_id(self) -> str:
        """``"CUST-<customer_id>"`` (mockup 07: MongoDB stores the prefixed form)."""
        return f"CUST-{self.customer_id}"

    @property
    def anchor(self) -> str:
        """The surrogate engine's record anchor for this customer."""
        return customer_anchor(self.customer_id)


def curp_for(persona: Any, dob: date, rng: random.Random) -> str:
    """A rule-valid CURP for ``persona`` (stdnum check digit)."""
    from stdnum.mx import curp

    from dps.engine.synth.personas import ascii_fold

    def letters(text: str) -> str:
        return re.sub(r"[^A-Z]", "", ascii_fold(text).upper()) or "X"

    last, first = letters(persona.last_name), letters(persona.first_name)
    vowel = next((c for c in last[1:] if c in "AEIOU"), "X")
    inner = (next((c for c in s[1:] if c not in "AEIOU"), "X") for s in (last, "X", first))
    body = (
        f"{last[0]}{vowel}X{first[0]}{dob:%y%m%d}{'M' if persona.gender == 'female' else 'H'}"
        f"{rng.choice(CURP_STATES)}{''.join(inner)}{'0' if dob.year < 2000 else 'A'}"
    )
    return body + str(curp.calc_check_digit(body))


def pan_for(persona: Any, key: str) -> str:
    """A rule-valid individual's PAN (holder type P, the surname's initial) for the *source*,
    drawn from its own per-record generator (``key``) so no other seeded value moves. The
    surrogate engine's PANs carry the never-issued sequence number 0000 (M5) and so no longer
    validate: the demo's ``tax_id`` would stop being discovered as a PAN."""
    from dps.engine.synth.personas import ascii_fold

    letters = "ABCDEFGHJKLMNPQRSTUVWXYZ"
    rng = random.Random(f"pan|{key}")
    initial = re.sub(r"[^A-Z]", "", ascii_fold(persona.last_name).upper())[:1] or "K"
    head = "".join(rng.choice(letters) for _ in range(3))
    return f"{head}P{initial}{rng.randint(1, 9999):04d}{rng.choice(letters)}"


def _country(rng: random.Random) -> tuple[str, str]:
    roll = rng.randrange(100)
    for code, locale, weight in COUNTRIES:
        if roll < weight:
            return code, locale
        roll -= weight
    return COUNTRIES[0][0], COUNTRIES[0][1]  # pragma: no cover - weights sum to 100


def _phone_template(country: str, cid: int) -> str:
    """A per-customer template original (the surrogate keeps its shape, never its digits)."""
    if country == "IN":
        return f"+91 98{cid % 1000:03d} {cid // 1000 % 100_000:05d}"
    if country == "US":
        return f"+1 202 555 {cid % 10_000:04d}"
    if country == "GB":
        return f"+44 7700 9{cid % 100_000:05d}"
    return f"+52 55 {cid % 10_000:04d} {cid // 10_000 % 10_000:04d}"


def demo_ssn(customer_id: int, seed_bytes: bytes) -> str:
    """An SSN in area ``666`` (never issued, and drawn by no surrogate), distinct per customer id
    for any run of fewer than 989,901 consecutive ids; the offset depends on the demo seed."""
    offset = int.from_bytes(hashlib.sha256(seed_bytes + b"|ssn").digest()[:8], "big")
    x = (customer_id * _SSN_STRIDE + offset) % _SSN_666_SPACE
    return f"666-{1 + x // 9_999:02d}-{1 + x % 9_999:04d}"


def _national_id(
    engine: Any, country: str, locale: str, anchor: str, cid: int, persona: Any, dob: date,
    rng: random.Random, seed_bytes: bytes,
) -> str:  # fmt: skip
    if country == "IN":  # 12 digits, leading 0 (never issued; surrogates take 1), Verhoeff valid
        from dps.engine.detect.checksums import verhoeff_check_digit

        drawn = str(
            engine.surrogate("AADHAAR", f"{2 + cid % 8}{cid % 10**11:011d}", locale, anchor)
        )
        body = "0" + re.sub(r"\D", "", drawn)[1:11]
        return body + verhoeff_check_digit(body)
    if country == "US":  # area 666 is never issued (surrogates take 9xx and 000)
        return demo_ssn(cid, seed_bytes)
    if country == "GB":  # HMRC's example prefix QQ is never issued (nor a surrogate prefix)
        return f"QQ{rng.randrange(1_000_000):06d}{rng.choice('ABCD')}"
    return curp_for(persona, dob, rng)


def seed_customers(rows: int, seed: int = 7) -> Iterator[SeedCustomer]:
    """``rows`` customers with ids ``10_000_001 …``; customer ``i`` depends only on ``(i, seed)``."""
    from dps.engine.synth.surrogates import SurrogateEngine

    sb = demo_seed_bytes(seed)
    engine = SurrogateEngine(sb, default_locale="en_IN")
    for i in range(rows):
        cid = CUSTOMER_ID_BASE + i + 1
        anchor = customer_anchor(cid)
        rng = random.Random(hashlib.sha256(sb + b"|customer|" + cid.to_bytes(8, "big")).digest())
        country, locale = _country(rng)
        name_template = f"Person {i}"
        engine.declare_record(anchor, [("PERSON_NAME", name_template)])
        full_name = str(engine.surrogate("PERSON_NAME", name_template, locale, anchor))
        first_name, _, last_name = full_name.partition(" ")
        dob = date.fromisoformat(
            engine.surrogate("DATE_OF_BIRTH", f"{1950 + i % 50}-06-15", locale, anchor)
        )
        persona = engine.persona(anchor, locale)
        national_id = _national_id(engine, country, locale, anchor, cid, persona, dob, rng, sb)
        tax_id = pan_for(persona, f"CUSTOMER:{cid}") if country == "IN" else None
        email = str(engine.surrogate("EMAIL", f"person{i}@example.com", locale, anchor))
        mobile = str(engine.surrogate("PHONE", _phone_template(country, cid), locale, anchor))
        street = str(engine.surrogate("STREET_ADDRESS", "1 Main Street", locale, anchor))
        created = datetime(2019, 1, 1) + timedelta(minutes=rng.randrange(3 * 365 * 24 * 60))
        last = created + timedelta(days=rng.randrange(600), seconds=rng.randrange(86_400))
        # GB mobiles are in Ofcom's drama range, which the free-text detector does not take for a
        # phone: a GB note carrying one would be refused as leakage (M2-DEVIATIONS, Task 27)
        reach = email if country == "GB" else f"{email} or {mobile}"
        notes = (
            f"KYC call with {first_name}; follow up at {reach}. "
            f"Prefers branch in {persona.city}. ✅ नमस्ते"
        )
        if i % BIG_NOTES_EVERY == 0:
            notes += (" " + "x" * 997 + ASTRAL) * 41
        yield SeedCustomer(
            index=i,
            customer_id=cid,
            locale=locale,
            country=country,
            full_name=full_name,
            first_name=first_name,
            last_name=last_name,
            email=email,
            mobile=mobile,
            dob=dob,
            id_type=ID_TYPE[country],
            national_id=national_id,
            tax_id=tax_id,
            addr_line1=street[:200],
            city=persona.city[:80],
            pin_code=persona.postal_code[:10],
            segment=SEGMENTS[rng.randrange(len(SEGMENTS))],
            risk_score=rng.randint(1, 999),
            created_at=created,
            last_contact_at=last.strftime("%Y-%m-%dT%H:%M:%S") + f".{rng.randrange(10**9):09d}",
            notes=notes,
        )
