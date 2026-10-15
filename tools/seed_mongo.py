"""Seed the MongoDB CRM demo collection from the shared customer space (M3 Task 15).

    python tools/seed_sources.py --target mongo [--mongo-uri mongodb://…] [--rows N]

Fills ``crm.profiles`` (nested documents, an array of sub-documents, BSON ``Int64`` and
``Decimal128``; ``customerId = "CUST-<id>"``, the prefixed form mockup 07 shows) from
:func:`tools.seed_model.seed_customers`, in ``insert_many`` batches of 1000, with a unique index on
``customerId``. ``crm.dps_seed_info`` records ``(seed, rows)`` and the demo data model's version
(:data:`tools.seed_model.DEMO_MODEL_VERSION`): a repeat run with the same three is a no-op; another
pair, or an older model, replaces the data. Returns counts only — never a value, never the URI.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Final

from tools.seed_model import DEMO_MODEL_VERSION, SeedCustomer, demo_seed_bytes, seed_customers

DATABASE: Final = "crm"
COLLECTION: Final = "profiles"
MARKER: Final = "dps_seed_info"
BATCH: Final = 1000
UNCHANGED: Final = "unchanged"


def profile_document(c: SeedCustomer, iban: str) -> dict[str, Any]:
    """One customer profile (the shape of ``tests/connectors/mongo_fixtures.profile``)."""
    from bson import Decimal128, Int64

    kyc: dict[str, Any] = {"verifiedAt": dt.datetime(2024, 5, 1) + dt.timedelta(days=c.index % 90)}
    if c.tax_id is not None:
        kyc = {"taxId": c.tax_id, **kyc}
    return {
        "customerId": c.mongo_customer_id,
        "profile": {
            "fullName": c.full_name,
            "dob": dt.datetime(c.dob.year, c.dob.month, c.dob.day),
        },
        "contact": {
            "email": c.email,
            "mobile": c.mobile,
            "addresses": [{"type": "home", "line1": c.addr_line1, "pin": c.pin_code}],
        },
        "kyc": kyc,
        "accounts": [
            {"iban": iban, "balance": Decimal128(f"{(c.customer_id * 7919) % 5_000_000}.50")}
        ],
        "riskScore": Int64(c.risk_score),
        "segment": c.segment,
        "notes": c.notes,
        "tags": ["salary"] if c.index % 2 else [],
    }


def seed_mongo(uri: str, rows: int, seed: int) -> dict[str, int | str]:
    """Fill ``crm.profiles`` for ``rows`` customers; ``{"crm.profiles": n | "unchanged"}``."""
    import pymongo

    from dps.engine.synth.surrogates import SurrogateEngine

    name = f"{DATABASE}.{COLLECTION}"
    client: pymongo.MongoClient[dict[str, Any]] = pymongo.MongoClient(
        uri, serverSelectionTimeoutMS=10_000
    )
    try:
        db = client[DATABASE]
        marker = db[MARKER].find_one({"_id": "seed"})
        current = (seed, rows, DEMO_MODEL_VERSION)
        if (
            marker is not None
            and (
                marker.get("seed"),
                marker.get("rows"),
                marker.get("version", 1),  # a marker without a version holds model 1's documents
            )
            == current
        ):
            return {name: UNCHANGED}
        db[MARKER].delete_many({})
        profiles = db[COLLECTION]
        profiles.delete_many({})
        engine = SurrogateEngine(demo_seed_bytes(seed), default_locale="en_GB")
        batch: list[dict[str, Any]] = []
        total = 0
        for c in seed_customers(rows, seed):
            iban = engine.surrogate(
                "IBAN", f"GB82WEST1234{c.customer_id:010d}", "en_GB", f"ACCOUNT:{c.customer_id}"
            )
            batch.append(profile_document(c, iban))
            if len(batch) == BATCH:
                profiles.insert_many(batch, ordered=True)
                total += len(batch)
                batch = []
        if batch:
            profiles.insert_many(batch, ordered=True)
            total += len(batch)
        profiles.create_index("customerId", unique=True, name="customerId_unique")
        db[MARKER].insert_one(
            {"_id": "seed", "seed": seed, "rows": rows, "version": DEMO_MODEL_VERSION}
        )
        return {name: total}
    finally:
        client.close()
