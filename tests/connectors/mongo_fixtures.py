"""Synthetic profile documents and connector helpers for MongoDB tests.

Values are synthetic only: RFC 2606 e-mails (``example.com``), ``GB00TEST`` IBANs, ``CUST-`` ids.
"""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING, Any

from bson import Decimal128, Int64, ObjectId

if TYPE_CHECKING:
    from dps.layers.connectors.mongo import MongoConnector


def profile(i: int, *, oid: bool = True) -> dict[str, Any]:
    """One customer profile: nested documents, an array of sub-documents and BSON numbers."""
    return {
        "_id": ObjectId() if oid else i,
        "customerId": f"CUST-{10_000_000 + i}",
        "profile": {
            "fullName": f"Person {i:05d}",
            "dob": dt.datetime(1980, 1, 1) + dt.timedelta(days=i),
        },
        "contact": {
            "email": f"person.{i}@example.com",
            "mobile": f"+91 98000 {i:05d}",
            "addresses": [{"type": "home", "line1": f"{i} Test Road", "pin": "560001"}],
        },
        "kyc": {"taxId": f"ABCDE{i % 10000:04d}F", "verifiedAt": dt.datetime(2024, 5, 1)},
        "accounts": [{"iban": f"GB00TEST{i:010d}", "balance": Decimal128(f"{i}.50")}],
        "riskScore": Int64(500 + i % 400),
        "segment": "premium" if i % 3 == 0 else "retail",
        "notes": f"Call person.{i}@example.com about the card",
        "tags": ["salary"] if i % 2 else [],
    }


def connector(uri: str, database: str, **settings: Any) -> MongoConnector:
    """A :class:`MongoConnector` on ``database`` (action ``test``, job ``j_test``)."""
    from dps.config import Settings
    from dps.jobs_models import ConnectionParams
    from dps.layers.connectors.mongo import MongoConnector

    return MongoConnector.from_secret(
        {"uri": uri},
        ConnectionParams(database=database),
        Settings(_env_file=None, **settings),
        action="test",
        client_id="j_test",
    )
