"""Seed the demo retail-bank source (contract §11 "Demo data"; M2 Task 27).

    python tools/seed_sources.py --target postgres [--dsn postgresql://…/bank] [--schema bank]
                                 [--rows 20000] [--seed 7] [--restore-copy] [--reset]

Every personal value comes from the M0 surrogate engine (seeded with ``sha256("dps-demo-<seed>")``)
applied to synthetic shape templates, anchored on the customer (``CUSTOMER_ID:<hash>``) so a
customer's name, e-mail, birth date, address and cards agree. The engine's rule-valid fakes
(never-issued SSNs in the 9xx area, Aadhaar-shaped numbers that UIDAI never issues, test-IIN card
numbers, RFC 2606 e-mail domains, fictional phone ranges) and HMRC's example NINO prefix ``QQ``
mean **no real PII ever exists in the demo source**. Non-personal columns come from
``random.Random(seed)``. The same seed always gives the same rows.

Idempotent: a ``<schema>._dps_seed_info`` row ``(seed, rows)`` marks a seeded schema ("already
seeded", exit 0; other parameters: exit 3 unless ``--reset``). ``--reset`` truncates the ten tables
and the marker first. ``--restore-copy`` copies the ten tables into ``bank_restore`` (owned by
``dps_writer``; the "restored production backup" for in-place demos) and puts 1 000 rows into
``bank_perf.transactions``. Another ``--schema`` uses ``<schema>_restore`` / ``<schema>_perf``, and
missing tables are created from :data:`TABLE_DDL` (the same DDL as
``docker/source-pg/initdb/01_bank_schema.sql``), so tests never touch the demo data.

Prints per-table row counts and the elapsed time — never a value, never the DSN.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import os
import random
import re
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

#: The ten demo tables, parents before children (contract §11).
TABLES: tuple[str, ...] = (
    "employees",
    "customers",
    "addresses",
    "contacts",
    "accounts",
    "cards",
    "transactions",
    "loans",
    "loan_applicants",
    "kyc_documents",
)
SCHEMA_RE = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
MARKER = "_dps_seed_info"

#: Column definitions of the ten tables (``{s}`` = the schema), as in 01_bank_schema.sql.
TABLE_DDL: dict[str, str] = {
    "employees": """
    employee_id   bigint PRIMARY KEY,
    full_name     varchar(120) NOT NULL,
    email         varchar(254) NOT NULL,
    phone         varchar(20),
    branch_code   varchar(11) NOT NULL,
    role          varchar(32) NOT NULL,
    hired_on      date NOT NULL""",
    "customers": """
    customer_id   bigint PRIMARY KEY,
    full_name     varchar(120) NOT NULL,
    date_of_birth date,
    national_id   varchar(18),
    id_type       varchar(12) NOT NULL,
    tax_id        char(10),
    email         varchar(254),
    mobile        varchar(20),
    country_code  char(2) NOT NULL,
    segment       varchar(16) NOT NULL,
    risk_score    smallint NOT NULL,
    kyc_status    varchar(12) NOT NULL,
    relationship_manager_id bigint REFERENCES {s}.employees,
    notes         text,
    created_at    timestamptz NOT NULL,
    updated_at    timestamptz NOT NULL""",
    "addresses": """
    address_id    bigint PRIMARY KEY,
    customer_id   bigint NOT NULL REFERENCES {s}.customers,
    kind          varchar(12) NOT NULL,
    line1         varchar(200) NOT NULL,
    line2         varchar(200),
    city          varchar(80) NOT NULL,
    state_code    varchar(8),
    postal_code   varchar(10) NOT NULL,
    country_code  char(2) NOT NULL""",
    "contacts": """
    contact_id    bigint PRIMARY KEY,
    customer_id   bigint NOT NULL REFERENCES {s}.customers,
    channel       varchar(12) NOT NULL,
    value         varchar(254) NOT NULL,
    is_primary    boolean NOT NULL DEFAULT false,
    verified_at   timestamptz""",
    "accounts": """
    account_id    bigint PRIMARY KEY,
    customer_id   bigint NOT NULL REFERENCES {s}.customers,
    account_number varchar(34) NOT NULL UNIQUE,
    iban          varchar(34),
    ifsc          varchar(11),
    product       varchar(24) NOT NULL,
    currency      char(3) NOT NULL,
    balance       numeric(14,2) NOT NULL,
    opened_on     date NOT NULL,
    status        varchar(10) NOT NULL""",
    "cards": """
    card_id       bigint PRIMARY KEY,
    account_id    bigint NOT NULL REFERENCES {s}.accounts,
    pan           varchar(19) NOT NULL,
    expiry        char(5) NOT NULL,
    cardholder_name varchar(120) NOT NULL,
    network       varchar(12) NOT NULL,
    status        varchar(10) NOT NULL,
    issued_on     date NOT NULL""",
    "transactions": """
    txn_id        bigint PRIMARY KEY,
    account_id    bigint NOT NULL REFERENCES {s}.accounts,
    booked_at     timestamptz NOT NULL,
    amount        numeric(14,2) NOT NULL,
    currency      char(3) NOT NULL,
    direction     char(1) NOT NULL,
    counterparty_name varchar(120),
    counterparty_iban varchar(34),
    channel       varchar(12) NOT NULL,
    description   varchar(200),
    merchant_category char(4)""",
    "loans": """
    loan_id       bigint PRIMARY KEY,
    customer_id   bigint NOT NULL REFERENCES {s}.customers,
    product       varchar(24) NOT NULL,
    principal     numeric(14,2) NOT NULL,
    rate          numeric(5,3) NOT NULL,
    term_months   smallint NOT NULL,
    status        varchar(12) NOT NULL,
    originated_on date NOT NULL,
    internal_score smallint NOT NULL""",
    "loan_applicants": """
    applicant_id  bigint PRIMARY KEY,
    loan_id       bigint NOT NULL REFERENCES {s}.loans,
    customer_id   bigint REFERENCES {s}.customers,
    role          varchar(12) NOT NULL,
    full_name     varchar(120) NOT NULL,
    date_of_birth date,
    employer_name varchar(120),
    annual_income numeric(14,2),
    pan_or_ssn    varchar(18)""",
    "kyc_documents": """
    doc_ref       varchar(40) PRIMARY KEY,
    customer_id   bigint NOT NULL REFERENCES {s}.customers,
    doc_type      varchar(16) NOT NULL,
    doc_number    varchar(32) NOT NULL,
    issued_on     date,
    expires_on    date,
    issuing_country char(2) NOT NULL,
    verified_by   bigint REFERENCES {s}.employees,
    notes         text""",
}
PERF_DDL = """
    txn_id        bigint GENERATED BY DEFAULT AS IDENTITY (START WITH 900000000001) PRIMARY KEY,
    account_id    bigint NOT NULL, booked_at timestamptz NOT NULL, amount numeric(14,2) NOT NULL,
    currency char(3) NOT NULL, direction char(1) NOT NULL, counterparty_name varchar(120),
    counterparty_iban varchar(34), channel varchar(12) NOT NULL, description varchar(200),
    merchant_category char(4)"""
PERF_ROWS = 1000


def columns_of(table: str) -> list[str]:
    """The column names of ``table`` in DDL order."""
    names = []
    for line in TABLE_DDL[table].strip().splitlines():
        name = line.strip().split()[0]
        names.append(name)
    return names


# -- generation (pure) ----------------------------------------------------------------------------

COUNTRIES = (("IN", "en_IN", 60), ("US", "en_US", 20), ("GB", "en_GB", 10), ("MX", "es_MX", 10))
CURRENCY = {"IN": "INR", "US": "USD", "GB": "GBP", "MX": "MXN"}
ID_TYPE = {"IN": "AADHAAR", "US": "SSN", "GB": "NINO", "MX": "CURP"}
PHONE_TEMPLATE = {
    "IN": "+91 98765 43210",
    "US": "+1 202 555 0143",
    "GB": "+44 7700 900123",
    "MX": "+52 55 1234 5678",
}
STATES = {
    "IN": ("MH", "KA", "DL", "TN", "GJ"),
    "US": ("CA", "NY", "TX", "WA", "IL"),
    "GB": ("ENG", "SCT", "WLS"),
    "MX": ("CDMX", "JAL", "NL"),
}
CURP_STATES = ("DF", "JC", "NL", "PL", "MC")
SEGMENTS = ("retail", "premium", "private", "sme")
PRODUCTS = ("savings", "current", "salary", "fixed_deposit")
LOAN_PRODUCTS = ("home", "auto", "personal", "education")
CHANNELS = ("upi", "card", "neft", "atm", "branch", "online")
DESCRIPTIONS = (
    "POS purchase",
    "Salary credit",
    "Utility bill",
    "ATM withdrawal",
    "Online transfer",
    "Card refund",
)
MCC = ("5411", "5812", "4111", "5999", "6011", "4900")
ROLES = ("relationship_manager", "teller", "branch_manager", "kyc_officer")
_EPOCH = datetime(2021, 1, 1, tzinfo=UTC)

#: Id bases: 8–12 digits so ``fpe_digits`` (domain ≥ 10^6) applies to every key.
CUSTOMER_BASE = 10_000_001
EMPLOYEE_BASE = 20_000_001
CARD_BASE = 30_000_001
LOAN_BASE = 50_000_001
ADDRESS_BASE = 60_000_001
CONTACT_BASE = 70_000_001
APPLICANT_BASE = 80_000_001
ACCOUNT_BASE = 100_000_001
TXN_BASE = 100_000_000_001


def demo_seed_bytes(seed: int) -> bytes:
    """The surrogate engine's seed for ``--seed``: ``sha256("dps-demo-<seed>")``."""
    return hashlib.sha256(b"dps-demo-%d" % seed).digest()


def _country(rng: random.Random) -> tuple[str, str]:
    roll = rng.randrange(100)
    for code, locale, weight in COUNTRIES:
        if roll < weight:
            return code, locale
        roll -= weight
    return COUNTRIES[0][0], COUNTRIES[0][1]  # pragma: no cover - weights sum to 100


def _curp(persona: Any, dob: date, rng: random.Random) -> str:
    """A rule-valid CURP for the persona (stdnum check digit)."""
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


@dataclass
class _Customer:
    id: int
    country: str
    locale: str
    anchor: str
    full_name: str
    dob: date
    national_id: str
    tax_id: str | None
    email: str
    mobile: str
    rm: int
    accounts: list[int] = field(default_factory=list)


def generate_rows(*, rows: int, seed: int) -> dict[str, list[dict[str, Any]]]:
    """The ten tables' rows for ``rows`` customers (pure, deterministic for ``seed``)."""
    from dps.engine.findings import hash_value
    from dps.engine.synth.surrogates import SurrogateEngine

    engine = SurrogateEngine(demo_seed_bytes(seed), default_locale="en_IN")
    rng = random.Random(seed)
    out: dict[str, list[dict[str, Any]]] = {t: [] for t in TABLES}

    n_employees = max(50, rows // 40)
    for e in range(n_employees):
        eid = EMPLOYEE_BASE + e
        anchor = f"EMPLOYEE_ID:{hash_value(str(eid))}"
        engine.declare_record(anchor, [("PERSON_NAME", f"Staff {e}")])
        name = engine.surrogate("PERSON_NAME", f"Staff {e}", "en_IN", anchor)
        out["employees"].append(
            {
                "employee_id": eid,
                "full_name": name,
                "email": engine.surrogate("EMAIL", f"staff{e}@example.org", "en_IN", anchor),
                "phone": engine.surrogate("PHONE", PHONE_TEMPLATE["IN"], "en_IN", anchor),
                "branch_code": f"DPSB0{rng.randrange(1, 999_999):06d}",
                "role": rng.choice(ROLES),
                "hired_on": date(2005, 1, 1) + timedelta(days=rng.randrange(6500)),
            }
        )

    customers: list[_Customer] = []
    for i in range(rows):
        cid = CUSTOMER_BASE + i
        country, locale = _country(rng)
        anchor = f"CUSTOMER_ID:{hash_value(str(cid))}"
        name_template = f"Person {i}"
        engine.declare_record(anchor, [("PERSON_NAME", name_template)])
        full_name = engine.surrogate("PERSON_NAME", name_template, locale, anchor)
        dob = date.fromisoformat(
            engine.surrogate("DATE_OF_BIRTH", f"{1950 + i % 50}-06-15", locale, anchor)
        )
        persona = engine.persona(anchor, locale)
        if country == "IN":
            national_id = engine.surrogate("AADHAAR", "234567890123", locale, anchor)
        elif country == "US":
            national_id = engine.surrogate("SSN", "123-45-6789", locale, anchor)
        elif country == "GB":  # HMRC's example prefix QQ is never issued
            national_id = f"QQ{rng.randrange(1_000_000):06d}{rng.choice('ABCD')}"
        else:
            national_id = _curp(persona, dob, rng)
        tax_id = (
            engine.surrogate("PAN_IN", "ABCPE1234F", locale, anchor) if country == "IN" else None
        )
        email = engine.surrogate("EMAIL", f"person{i}@example.com", locale, anchor)
        mobile = engine.surrogate("PHONE", PHONE_TEMPLATE[country], locale, anchor)
        c = _Customer(
            id=cid,
            country=country,
            locale=locale,
            anchor=anchor,
            full_name=full_name,
            dob=dob,
            national_id=national_id,
            tax_id=tax_id,
            email=email,
            mobile=mobile,
            rm=EMPLOYEE_BASE + rng.randrange(n_employees),
        )
        customers.append(c)
        created = _EPOCH + timedelta(minutes=rng.randrange(2_000_000))
        notes = None
        if rng.random() < 0.3:
            alt = engine.surrogate("EMAIL", f"alt{i}@example.net", locale, anchor)
            # GB mobiles are in the Ofcom drama range (+44 7700 900xxx), which the detector does
            # not take for a phone in free text (the per-row sweep then refuses the batch as
            # leakage): GB notes carry the alternative e-mail only (see M2-DEVIATIONS, Task 27).
            if country == "GB":
                notes = f"Prefers e-mail; alt email {alt}"
            else:
                notes = f"Prefers SMS on {mobile}; alt email {alt}"
        out["customers"].append(
            {
                "customer_id": cid,
                "full_name": full_name,
                "date_of_birth": dob,
                "national_id": national_id,
                "id_type": ID_TYPE[country],
                "tax_id": tax_id,
                "email": email,
                "mobile": mobile,
                "country_code": country,
                "segment": rng.choice(SEGMENTS),
                "risk_score": rng.randrange(1, 1000),
                "kyc_status": rng.choice(("verified", "verified", "pending", "expired")),
                "relationship_manager_id": c.rm,
                "notes": notes,
                "created_at": created,
                "updated_at": created + timedelta(days=rng.randrange(400)),
            }
        )
        homes = 2 if rng.random() < 0.1 else 1
        for kind in ("home", "work")[:homes]:
            line1 = engine.surrogate("STREET_ADDRESS", "1 Main Street", locale, anchor)
            out["addresses"].append(
                {
                    "address_id": ADDRESS_BASE + len(out["addresses"]),
                    "customer_id": cid,
                    "kind": kind,
                    "line1": line1 if kind == "home" else f"Floor {rng.randrange(1, 30)}, {line1}",
                    "line2": None,
                    "city": persona.city[:80],
                    "state_code": rng.choice(STATES[country]),
                    "postal_code": persona.postal_code[:10],
                    "country_code": country,
                }
            )
        for channel, value in (("email", email), ("mobile", mobile)):
            out["contacts"].append(
                {
                    "contact_id": CONTACT_BASE + len(out["contacts"]),
                    "customer_id": cid,
                    "channel": channel,
                    "value": value,
                    "is_primary": channel == "email",
                    "verified_at": created + timedelta(days=rng.randrange(30)),
                }
            )

    n_accounts = rows + rows // 2
    for j in range(n_accounts):
        owner = customers[j % rows] if j < rows else customers[rng.randrange(rows)]
        aid = ACCOUNT_BASE + j
        owner.accounts.append(aid)
        iban = (
            engine.surrogate("IBAN", "GB82WEST12345698765432", owner.locale, f"ACCOUNT:{aid}")
            if owner.country == "GB"
            else None
        )
        ifsc = (
            engine.surrogate("IFSC", "DPSB0001234", owner.locale, f"ACCOUNT:{aid}")
            if owner.country == "IN"
            else None
        )
        out["accounts"].append(
            {
                "account_id": aid,
                "customer_id": owner.id,
                "account_number": f"{owner.country}{aid:012d}{rng.randrange(100):02d}",
                "iban": iban,
                "ifsc": ifsc,
                "product": rng.choice(PRODUCTS),
                "currency": CURRENCY[owner.country],
                "balance": Decimal(rng.randrange(0, 50_000_000)) / 100,
                "opened_on": date(2010, 1, 1) + timedelta(days=rng.randrange(5000)),
                "status": rng.choice(("active", "active", "active", "dormant", "closed")),
            }
        )

    owners = {
        a["account_id"]: customers[(a["customer_id"] - CUSTOMER_BASE)] for a in out["accounts"]
    }
    n_cards = rows + rows // 5
    for n in range(n_cards):
        account = out["accounts"][n % n_accounts]
        owner = owners[account["account_id"]]
        card_id = CARD_BASE + n
        out["cards"].append(
            {
                "card_id": card_id,
                "account_id": account["account_id"],
                "pan": engine.surrogate(
                    "CARD_PAN", "4111111111111111", owner.locale, f"CARD:{card_id}"
                ),
                "expiry": engine.surrogate("CARD_EXPIRY", "12/27", owner.locale, f"CARD:{card_id}"),
                "cardholder_name": owner.full_name.upper()[:120],
                "network": rng.choice(("visa", "mastercard", "rupay")),
                "status": rng.choice(("active", "active", "blocked")),
                "issued_on": date(2018, 1, 1) + timedelta(days=rng.randrange(2500)),
            }
        )

    counterparties = [
        engine.surrogate("PERSON_NAME", f"Payee {p}", "en_IN", f"PAYEE:{p}") for p in range(500)
    ]
    for k in range(rows * 10):
        account = out["accounts"][rng.randrange(n_accounts)]
        owner = owners[account["account_id"]]
        out["transactions"].append(
            {
                "txn_id": TXN_BASE + k,
                "account_id": account["account_id"],
                "booked_at": _EPOCH + timedelta(seconds=rng.randrange(120_000_000)),
                "amount": Decimal(rng.randrange(100, 20_000_000)) / 100,
                "currency": account["currency"],
                "direction": rng.choice("DC"),
                "counterparty_name": rng.choice(counterparties) if rng.random() < 0.7 else None,
                "counterparty_iban": None,
                "channel": rng.choice(CHANNELS),
                "description": rng.choice(DESCRIPTIONS),
                "merchant_category": rng.choice(MCC),
            }
        )
        if owner.country == "GB" and rng.random() < 0.5:
            out["transactions"][-1]["counterparty_iban"] = engine.surrogate(
                "IBAN", "GB33BUKB20201555555555", "en_GB", f"PAYEE-IBAN:{k % 997}"
            )

    n_loans = rows // 4
    for m in range(n_loans):
        borrower = customers[rng.randrange(rows)]
        loan_id = LOAN_BASE + m
        out["loans"].append(
            {
                "loan_id": loan_id,
                "customer_id": borrower.id,
                "product": rng.choice(LOAN_PRODUCTS),
                "principal": Decimal(rng.randrange(5_000_000, 900_000_000)) / 100,
                "rate": Decimal(rng.randrange(6_000, 16_000)) / 1000,
                "term_months": rng.choice((12, 24, 36, 60, 120, 240)),
                "status": rng.choice(("active", "closed", "arrears")),
                "originated_on": date(2015, 1, 1) + timedelta(days=rng.randrange(3500)),
                "internal_score": rng.randrange(300, 900),
            }
        )
        applicants: list[tuple[str, _Customer | None]] = [("primary", borrower)]
        if rng.random() < 0.4:
            applicants.append((rng.choice(("co_applicant", "guarantor")), None))
        for role, who in applicants:
            aid = APPLICANT_BASE + len(out["loan_applicants"])
            if who is not None:
                name, dob, ident = who.full_name, who.dob, who.tax_id or who.national_id
                locale, anchor = who.locale, who.anchor
            else:
                anchor = f"APPLICANT:{aid}"
                locale = "en_IN"
                engine.declare_record(anchor, [("PERSON_NAME", f"Applicant {aid}")])
                name = engine.surrogate("PERSON_NAME", f"Applicant {aid}", locale, anchor)
                dob = date.fromisoformat(
                    engine.surrogate("DATE_OF_BIRTH", "1975-03-10", locale, anchor)
                )
                ident = engine.surrogate("PAN_IN", "ABCPE1234F", locale, anchor)
            out["loan_applicants"].append(
                {
                    "applicant_id": aid,
                    "loan_id": loan_id,
                    "customer_id": who.id if who is not None else None,
                    "role": role,
                    "full_name": name,
                    "date_of_birth": dob,
                    "employer_name": engine.surrogate("ORG_NAME", "Acme Ltd", locale, anchor)[:120],
                    "annual_income": Decimal(rng.randrange(200_000, 50_000_000)) / 100,
                    "pan_or_ssn": ident[:18],
                }
            )

    for c in customers:
        doc_type = {"IN": "AADHAAR", "US": "SSN_CARD", "GB": "NINO_LETTER", "MX": "CURP"}[c.country]
        issued = date(2012, 1, 1) + timedelta(days=rng.randrange(3000))
        notes = None
        if rng.random() < 0.2:  # no name: without NER a name in free text has no cue to find
            notes = f"Checked against the original in branch {c.rm % 1000:03d}; copy on file"
        out["kyc_documents"].append(
            {
                "doc_ref": f"KYC-{c.id}-{doc_type}",
                "customer_id": c.id,
                "doc_type": doc_type,
                "doc_number": c.national_id,
                "issued_on": issued,
                "expires_on": issued + timedelta(days=3650),
                "issuing_country": c.country,
                "verified_by": c.rm,
                "notes": notes,
            }
        )
    return out


# -- loading --------------------------------------------------------------------------------------


@dataclass
class SeedResult:
    """What a run did: ``already`` (nothing loaded), per-table counts, the elapsed time."""

    already: bool
    counts: dict[str, int]
    elapsed_s: float
    restored: bool = False


class SeedConflict(RuntimeError):
    """The schema was seeded with other parameters (``--reset`` reloads it)."""


def _q(schema: str) -> str:
    if not SCHEMA_RE.fullmatch(schema):
        raise ValueError("schema names must match ^[a-z_][a-z0-9_]{0,62}$")
    return f'"{schema}"'


def satellite_schemas(schema: str) -> tuple[str, str]:
    """``(restore, perf)`` schemas of ``schema`` (``bank_restore``/``bank_perf`` for ``bank``)."""
    return f"{schema}_restore", f"{schema}_perf"


async def _ensure_tables(conn: Any, schema: str) -> None:
    s = _q(schema)
    await conn.execute(f"CREATE SCHEMA IF NOT EXISTS {s}")
    for table in TABLES:
        ddl = TABLE_DDL[table].replace("{s}", s)
        await conn.execute(f"CREATE TABLE IF NOT EXISTS {s}.{table} ({ddl})")
    await conn.execute(
        f"CREATE TABLE IF NOT EXISTS {s}.{MARKER} (seed int NOT NULL, rows int NOT NULL, "
        "seeded_at timestamptz NOT NULL DEFAULT now())"
    )
    for role in ("dps_reader", "dps_writer"):
        exists = await conn.fetchval("SELECT 1 FROM pg_roles WHERE rolname = $1", role)
        if exists:
            await conn.execute(f"GRANT USAGE ON SCHEMA {s} TO {role}")
            await conn.execute(f"GRANT SELECT ON ALL TABLES IN SCHEMA {s} TO {role}")


async def _counts(conn: Any, schema: str, tables: Sequence[str]) -> dict[str, int]:
    s = _q(schema)
    return {t: int(await conn.fetchval(f"SELECT count(*) FROM {s}.{t}")) for t in tables}


async def _restore_copy(conn: Any, schema: str, *, force: bool) -> bool:
    """Copy the ten tables into the restore schema (when missing, stale or ``force``) and give
    the perf table its rows; returns whether anything was (re)copied."""
    src = _q(schema)
    restore, perf = (_q(x) for x in satellite_schemas(schema))
    owner = (
        "dps_writer"
        if await conn.fetchval("SELECT 1 FROM pg_roles WHERE rolname = 'dps_writer'")
        else None
    )
    auth = f" AUTHORIZATION {owner}" if owner else ""
    await conn.execute(f"CREATE SCHEMA IF NOT EXISTS {restore}{auth}")
    await conn.execute(f"CREATE SCHEMA IF NOT EXISTS {perf}{auth}")
    source_counts = await _counts(conn, schema, TABLES)
    copied = False
    for table in TABLES:
        present = await conn.fetchval("SELECT to_regclass($1)", f"{restore}.{table}")
        if present and not force:
            n = int(await conn.fetchval(f"SELECT count(*) FROM {restore}.{table}"))
            if n == source_counts[table]:
                continue
        await conn.execute(f"DROP TABLE IF EXISTS {restore}.{table} CASCADE")
        await conn.execute(f"CREATE TABLE {restore}.{table} (LIKE {src}.{table} INCLUDING ALL)")
        await conn.execute(f"INSERT INTO {restore}.{table} SELECT * FROM {src}.{table}")
        if owner:
            await conn.execute(f"ALTER TABLE {restore}.{table} OWNER TO {owner}")
        copied = True
    await conn.execute(f"CREATE TABLE IF NOT EXISTS {perf}.transactions ({PERF_DDL})")
    if owner:
        await conn.execute(f"ALTER TABLE {perf}.transactions OWNER TO {owner}")
    if force:
        await conn.execute(f"TRUNCATE {perf}.transactions RESTART IDENTITY")
    if not await conn.fetchval(f"SELECT count(*) FROM {perf}.transactions"):
        cols = [c for c in columns_of("transactions") if c != "txn_id"]
        names = ", ".join(cols)
        await conn.execute(
            f"INSERT INTO {perf}.transactions ({names}) SELECT {names} FROM {src}.transactions "
            f"ORDER BY txn_id LIMIT {PERF_ROWS}"
        )
        copied = True
    return copied


async def seed(
    dsn: str,
    *,
    rows: int,
    seed: int,
    schema: str = "bank",
    restore_copy: bool = False,
    reset: bool = False,
) -> SeedResult:
    """Load the ten tables into ``schema`` (see the module docstring for the rules).

    Raises:
        SeedConflict: the schema holds another seed's rows and ``reset`` is false.
    """
    import asyncpg

    started = time.monotonic()
    s = _q(schema)
    conn = await asyncpg.connect(dsn)
    try:
        await _ensure_tables(conn, schema)
        if reset:
            await conn.execute(
                f"TRUNCATE {', '.join(f'{s}.{t}' for t in TABLES)}, {s}.{MARKER} CASCADE"
            )
        marker = await conn.fetchrow(f"SELECT seed, rows FROM {s}.{MARKER} LIMIT 1")
        already = marker is not None
        if already and (marker["seed"], marker["rows"]) != (seed, rows):
            raise SeedConflict("the schema was seeded with other parameters: use --reset")
        if not already:
            occupied = await _counts(conn, schema, TABLES)
            if any(occupied.values()):
                raise SeedConflict("the tables hold rows but no seed marker: use --reset")
            data = await asyncio.to_thread(generate_rows, rows=rows, seed=seed)
            async with conn.transaction():
                for table in TABLES:
                    cols = columns_of(table)
                    await conn.copy_records_to_table(
                        table,
                        records=[tuple(r[c] for c in cols) for r in data[table]],
                        columns=cols,
                        schema_name=schema,
                    )
                await conn.execute(
                    f"INSERT INTO {s}.{MARKER} (seed, rows) VALUES ($1, $2)", seed, rows
                )
            del data
        restored = await _restore_copy(conn, schema, force=reset) if restore_copy else False
        counts = await _counts(conn, schema, TABLES)
    finally:
        await conn.close()
    return SeedResult(
        already=already, counts=counts, elapsed_s=time.monotonic() - started, restored=restored
    )


def default_dsn() -> str:
    """``DPS_TEST_SOURCE_PG_DSN``, else the lane's ``DPS_SOURCE_PG_PORT`` (contract §12.2)."""
    explicit = os.environ.get("DPS_TEST_SOURCE_PG_DSN")
    if explicit:
        return explicit
    port = os.environ.get("DPS_SOURCE_PG_PORT") or "5442"
    return f"postgresql://postgres:postgres@127.0.0.1:{port}/bank"  # local demo literal


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """The CLI (module docstring)."""
    p = argparse.ArgumentParser(
        prog="python tools/seed_sources.py", description=__doc__.split("\n\n")[0]
    )
    p.add_argument("--target", choices=("postgres", "oracle", "mongo"), required=True)
    p.add_argument(
        "--dsn", default=None, help="default: DPS_TEST_SOURCE_PG_DSN / DPS_SOURCE_PG_PORT"
    )
    p.add_argument("--schema", default="bank")
    p.add_argument("--rows", type=int, default=20_000)
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--restore-copy", action="store_true")
    p.add_argument("--reset", action="store_true")
    args = p.parse_args(argv)
    if args.rows < 1:
        p.error("--rows must be at least 1")
    if not SCHEMA_RE.fullmatch(args.schema):
        p.error("--schema must match ^[a-z_][a-z0-9_]{0,62}$")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI; returns the exit code (0 ok/already seeded, 2 not yet supported, 3 conflict,
    1 a database failure reported by class name)."""
    args = parse_args(argv)
    if args.target != "postgres":
        print(f"--target {args.target} arrives with M3", file=sys.stderr)
        return 2
    try:
        result = asyncio.run(
            seed(
                args.dsn or default_dsn(),
                rows=args.rows,
                seed=args.seed,
                schema=args.schema,
                restore_copy=args.restore_copy,
                reset=args.reset,
            )
        )
    except SeedConflict as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 3
    except Exception as exc:  # noqa: BLE001 - a driver message could quote the DSN
        print(f"seeding failed ({type(exc).__name__})", file=sys.stderr)
        return 1
    if result.already:
        print(f"already seeded: schema {args.schema} (seed {args.seed}, {args.rows} customers)")
    for table in TABLES:
        print(f"  {table:<16} {result.counts[table]:>9}")
    if args.restore_copy:
        print(f"restore copy: {'refreshed' if result.restored else 'up to date'}")
    print(f"done in {result.elapsed_s:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
