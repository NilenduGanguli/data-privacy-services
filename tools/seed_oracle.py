"""Seed the Oracle CRM demo schemas from the shared customer space (M3 Task 15).

    python tools/seed_sources.py --target oracle [--oracle-dsn host:port/FREEPDB1] [--rows N]

Connects as the demo owners ``CRM`` and ``LOANS`` (local demo literals, docker/oracle/initdb) and
fills ``CRM.CUSTOMERS``, ``CRM.INTERACTIONS`` (two per customer, ``EVENT_TS`` a burst inside one
microsecond), ``CRM.KYC_DOCUMENTS`` (``DOC_REF = 'K<index>'``, blank-padded by its CHAR(12)),
``CRM.ADDRESS_HISTORY`` (key-less), ``CRM.CUSTOMER_EVENTS`` (identity key) and ``LOANS.APPLICANTS``
(every 4th customer) from :func:`tools.seed_model.seed_customers`, in ``executemany`` batches of
1000 with typed CLOB binds. ``CRM.DPS_SEED_INFO`` records ``(seed, rows)``: a repeat run with the
same pair is a no-op, another pair replaces the data. Returns counts only — never a value, never
the DSN.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Final

from tools.seed_model import SeedCustomer, seed_customers

BATCH: Final = 1000
#: Demo owners and their local literal passwords (docker/oracle/initdb/01_crm.sql, contract §11).
CRM_LOGIN: Final = ("crm", "crm")  # local demo literal
LOANS_LOGIN: Final = ("loans", "loans")  # local demo literal
#: Tables in delete order (children first), with the login that owns them.
TABLES: Final = (
    "LOANS.APPLICANTS",
    "CRM.CUSTOMER_EVENTS",
    "CRM.ADDRESS_HISTORY",
    "CRM.KYC_DOCUMENTS",
    "CRM.INTERACTIONS",
    "CRM.CUSTOMERS",
)
UNCHANGED: Final = "unchanged"
_TS9: Final = "'YYYY-MM-DD\"T\"HH24:MI:SS.FF9'"
_MARKER_DDL: Final = """
BEGIN
  EXECUTE IMMEDIATE 'CREATE TABLE CRM.DPS_SEED_INFO (SEED NUMBER(10) NOT NULL,
    ROWS_N NUMBER(10) NOT NULL, SEEDED_AT TIMESTAMP(6) DEFAULT SYSTIMESTAMP NOT NULL)';
EXCEPTION WHEN OTHERS THEN IF SQLCODE != -955 THEN RAISE; END IF;
END;"""
_CHANNELS: Final = ("phone", "branch", "email", "chat")
_EVENTS: Final = ("login", "statement_viewed", "address_changed", "card_blocked")
_STATUS: Final = ("submitted", "approved", "rejected", "disbursed")


def _batches(rows: Iterable[dict[str, Any]]) -> Iterator[list[dict[str, Any]]]:
    batch: list[dict[str, Any]] = []
    for row in rows:
        batch.append(row)
        if len(batch) == BATCH:
            yield batch
            batch = []
    if batch:
        yield batch


def burst_twin(ts: str) -> str:
    """``ts`` (``…SS.fffffffff``) moved by one nanosecond inside the same microsecond."""
    head, frac = ts.rsplit(".", 1)
    nanos = int(frac)
    twin = nanos + 1 if nanos % 1000 < 999 else nanos - 1
    return f"{head}.{twin:09d}"


def customer_row(c: SeedCustomer) -> dict[str, Any]:
    """The ``CRM.CUSTOMERS`` binds of one customer."""
    return {
        "CUSTOMER_ID": c.customer_id,
        "FULL_NAME": c.full_name,
        "NATIONAL_ID": c.national_id,
        "TAX_ID": c.tax_id,
        "EMAIL": c.email,
        "MOBILE": c.mobile,
        "DOB": datetime(c.dob.year, c.dob.month, c.dob.day),
        "ADDR_LINE1": c.addr_line1,
        "CITY": c.city,
        "PIN_CODE": c.pin_code,
        "COUNTRY": c.country,
        "SEGMENT": c.segment,
        "RISK_SCORE": c.risk_score,
        "NOTES": c.notes,
        "CREATED_AT": c.created_at,
        "LAST_CONTACT_AT": c.last_contact_at,
    }


def interaction_rows(c: SeedCustomer) -> list[dict[str, Any]]:
    """Two interactions inside one microsecond (the TIMESTAMP(9) paging pitfall)."""
    out = []
    for n, ts in enumerate((c.last_contact_at, burst_twin(c.last_contact_at))):
        agent = f"agent.{(c.index + n) % 40}@example.org"
        out.append(
            {
                "CUSTOMER_ID": c.customer_id,
                "EVENT_TS": ts,
                "CHANNEL": _CHANNELS[(c.index + n) % len(_CHANNELS)],
                "AGENT_EMAIL": agent,
                "SUMMARY": f"Follow-up {n + 1} on the KYC review",
                "PAYLOAD": json.dumps({"channel": _CHANNELS[c.index % 4], "agent": agent}),
            }
        )
    return out


def _day(d: date) -> datetime:
    return datetime(d.year, d.month, d.day)


def kyc_row(c: SeedCustomer) -> dict[str, Any]:
    """A short ``DOC_REF`` that ``CHAR(12)`` blank-pads (the CHAR bind pitfall)."""
    issued = date(2012, 1, 1) + timedelta(days=(c.customer_id * 37) % 3000)
    return {
        "DOC_REF": f"K{c.index}",
        "CUSTOMER_ID": c.customer_id,
        "DOC_TYPE": c.id_type.lower(),
        "DOC_NUMBER": c.national_id,
        "ISSUED_ON": _day(issued),
        "EXPIRES_ON": _day(issued + timedelta(days=3650)),
    }


def address_rows(c: SeedCustomer) -> list[dict[str, Any]]:
    """The current address and one older (a key-less table)."""
    since = _day(c.created_at.date())
    return [
        {
            "CUSTOMER_ID": c.customer_id,
            "ADDR_LINE1": c.addr_line1,
            "CITY": c.city,
            "PIN_CODE": c.pin_code,
            "VALID_FROM": since,
        },
        {
            "CUSTOMER_ID": c.customer_id,
            "ADDR_LINE1": f"Flat {c.index % 90 + 1}, {c.addr_line1}"[:200],
            "CITY": c.city,
            "PIN_CODE": c.pin_code,
            "VALID_FROM": since - timedelta(days=900),
        },
    ]


def event_rows(c: SeedCustomer) -> list[dict[str, Any]]:
    """Two events; ``EVENT_ID`` is the identity column (omitted)."""
    return [
        {
            "CUSTOMER_ID": c.customer_id,
            "EVENT_TYPE": _EVENTS[(c.index + n) % len(_EVENTS)],
            "CONTACT_EMAIL": c.email,
            "OCCURRED_AT": c.created_at + timedelta(days=30 * (n + 1)),
        }
        for n in range(2)
    ]


def applicant_row(c: SeedCustomer) -> dict[str, Any]:
    """``LOANS.APPLICANTS`` (every 4th customer; a string key in a second schema)."""
    return {
        "APPLICATION_ID": f"APP-{c.index:07d}",
        "CUSTOMER_ID": c.customer_id,
        "APPLICANT_NAME": c.full_name,
        "PAN": c.tax_id,
        "MOBILE": c.mobile,
        "INCOME": Decimal(200_000 + (c.customer_id * 7919) % 4_800_000) / 100,
        "STATUS": _STATUS[c.index % len(_STATUS)],
        "APPLIED_AT": c.created_at + timedelta(days=60),
    }


def _insert_sql(table: str, columns: list[str], *, ts9: frozenset[str] = frozenset()) -> str:
    values = ", ".join(f"TO_TIMESTAMP(:{c}, {_TS9})" if c in ts9 else f":{c}" for c in columns)
    return f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({values})"  # noqa: S608


def _load(
    conn: Any,
    table: str,
    rows: Iterable[dict[str, Any]],
    *,
    ts9: frozenset[str] = frozenset(),
    clobs: frozenset[str] = frozenset(),
) -> int:
    import oracledb

    total = 0
    sql: str | None = None
    with conn.cursor() as cur:
        for batch in _batches(rows):
            if sql is None:
                sql = _insert_sql(table, list(batch[0]), ts9=ts9)
            cur.setinputsizes(**{c: oracledb.DB_TYPE_CLOB for c in clobs})
            cur.executemany(sql, batch)
            conn.commit()
            total += len(batch)
    return total


def seed_oracle(dsn: str, rows: int, seed: int) -> dict[str, int | str]:
    """Fill the six demo tables for ``rows`` customers; ``{table: rows | "unchanged"}``.

    Idempotent per ``(rows, seed)`` (``CRM.DPS_SEED_INFO``); another pair replaces the data.
    """
    import oracledb

    crm = oracledb.connect(user=CRM_LOGIN[0], password=CRM_LOGIN[1], dsn=dsn)
    loans = oracledb.connect(user=LOANS_LOGIN[0], password=LOANS_LOGIN[1], dsn=dsn)
    try:
        with crm.cursor() as cur:
            cur.execute(_MARKER_DDL)
            cur.execute("SELECT SEED, ROWS_N FROM CRM.DPS_SEED_INFO")
            marker = cur.fetchall()
        if marker == [(seed, rows)]:
            return dict.fromkeys(reversed(TABLES), UNCHANGED)
        for table in TABLES:
            owner = loans if table.startswith("LOANS.") else crm
            with owner.cursor() as cur:
                cur.execute(f"TRUNCATE TABLE {table}")
        with crm.cursor() as cur:
            cur.execute("DELETE FROM CRM.DPS_SEED_INFO")
        crm.commit()
        customers = list(seed_customers(rows, seed))
        counts: dict[str, int | str] = {}
        counts["CRM.CUSTOMERS"] = _load(
            crm,
            "CRM.CUSTOMERS",
            (customer_row(c) for c in customers),
            ts9=frozenset({"LAST_CONTACT_AT"}),
            clobs=frozenset({"NOTES"}),
        )
        counts["CRM.INTERACTIONS"] = _load(
            crm,
            "CRM.INTERACTIONS",
            (r for c in customers for r in interaction_rows(c)),
            ts9=frozenset({"EVENT_TS"}),
            clobs=frozenset({"PAYLOAD"}),
        )
        counts["CRM.KYC_DOCUMENTS"] = _load(
            crm, "CRM.KYC_DOCUMENTS", (kyc_row(c) for c in customers)
        )
        counts["CRM.ADDRESS_HISTORY"] = _load(
            crm, "CRM.ADDRESS_HISTORY", (r for c in customers for r in address_rows(c))
        )
        counts["CRM.CUSTOMER_EVENTS"] = _load(
            crm, "CRM.CUSTOMER_EVENTS", (r for c in customers for r in event_rows(c))
        )
        counts["LOANS.APPLICANTS"] = _load(
            loans, "LOANS.APPLICANTS", (applicant_row(c) for c in customers if c.index % 4 == 0)
        )
        with crm.cursor() as cur:
            cur.execute(
                "INSERT INTO CRM.DPS_SEED_INFO (SEED, ROWS_N) VALUES (:s, :r)", s=seed, r=rows
            )
        crm.commit()
        return counts
    finally:
        loans.close()
        crm.close()
