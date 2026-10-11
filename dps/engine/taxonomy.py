"""Entity taxonomy (spec §5.2): families, sensitivity tiers and the registry.

The registry is open: custom recognizers and declared fields may introduce entity names that are
not listed here; they default to ``Family.OTHER`` / ``Sensitivity.MEDIUM`` unless registered.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum


class Sensitivity(StrEnum):
    """Sensitivity ladder, LOW < MEDIUM < HIGH < CRITICAL."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"

    @property
    def rank(self) -> int:
        """Position on the ladder: LOW=0 … CRITICAL=3."""
        return _RANK[self]

    @classmethod
    def max_of(cls, items: Iterable[Sensitivity]) -> Sensitivity:
        """The highest tier in ``items`` (LOW when empty)."""
        best = cls.LOW
        for item in items:
            if item.rank > best.rank:
                best = item
        return best


_RANK = {Sensitivity.LOW: 0, Sensitivity.MEDIUM: 1, Sensitivity.HIGH: 2, Sensitivity.CRITICAL: 3}


class Family(StrEnum):
    """Entity families (spec §5.2 table rows)."""

    IDENTITY = "identity"
    GOVERNMENT_ID = "government_id"
    FINANCIAL = "financial"
    CONTACT = "contact"
    TEMPORAL = "temporal"
    ONLINE = "online"
    SECRET = "secret"
    HEALTH = "health"
    ORGANISATION = "organisation"
    LOCATION = "location"
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class EntityInfo:
    """One entity type."""

    name: str
    family: Family
    sensitivity: Sensitivity
    description: str = ""
    jurisdictions: tuple[str, ...] = ()


_C, _H, _M, _L = Sensitivity.CRITICAL, Sensitivity.HIGH, Sensitivity.MEDIUM, Sensitivity.LOW
_ENTRIES: tuple[EntityInfo, ...] = (
    # identity
    EntityInfo("PERSON_NAME", Family.IDENTITY, _M, "a natural person's name"),
    EntityInfo("DATE_OF_BIRTH", Family.IDENTITY, _M, "birth date"),
    EntityInfo("GENDER", Family.IDENTITY, _L),
    EntityInfo("NATIONALITY", Family.IDENTITY, _L),
    EntityInfo("AGE", Family.IDENTITY, _L),
    # government identifiers — every identifier of a natural person is CRITICAL (spec §5.2);
    # business registration numbers (EIN, BN) identify organisations and are HIGH.
    EntityInfo("SSN", Family.GOVERNMENT_ID, _C, "US Social Security Number", ("US",)),
    EntityInfo("ITIN", Family.GOVERNMENT_ID, _C, "US taxpayer identification number", ("US",)),
    EntityInfo("EIN", Family.GOVERNMENT_ID, _H, "US employer identification number", ("US",)),
    EntityInfo("PASSPORT", Family.GOVERNMENT_ID, _C, "passport number"),
    EntityInfo("DRIVER_LICENSE", Family.GOVERNMENT_ID, _C, "driver licence number"),
    EntityInfo("SIN", Family.GOVERNMENT_ID, _C, "Canadian Social Insurance Number", ("CA",)),
    EntityInfo("BN", Family.GOVERNMENT_ID, _H, "Canadian business number", ("CA",)),
    EntityInfo("CURP", Family.GOVERNMENT_ID, _C, "Mexican CURP", ("MX",)),
    EntityInfo("RFC", Family.GOVERNMENT_ID, _C, "Mexican RFC", ("MX",)),
    EntityInfo("INE_CLAVE", Family.GOVERNMENT_ID, _C, "Mexican INE clave de elector", ("MX",)),
    EntityInfo("AADHAAR", Family.GOVERNMENT_ID, _C, "Indian Aadhaar number", ("IN",)),
    EntityInfo("PAN_IN", Family.GOVERNMENT_ID, _C, "Indian permanent account number", ("IN",)),
    EntityInfo("VOTER_ID", Family.GOVERNMENT_ID, _C, "Indian voter id (EPIC)", ("IN",)),
    EntityInfo("NINO", Family.GOVERNMENT_ID, _C, "UK national insurance number", ("GB",)),
    EntityInfo("NHS_NUMBER", Family.GOVERNMENT_ID, _C, "UK NHS number", ("GB",)),
    EntityInfo("NATIONAL_ID", Family.GOVERNMENT_ID, _C, "other national identifier"),
    EntityInfo("MRZ", Family.GOVERNMENT_ID, _C, "machine-readable zone"),
    # financial
    EntityInfo("CARD_PAN", Family.FINANCIAL, _C, "payment card number"),
    EntityInfo("CARD_EXPIRY", Family.FINANCIAL, _H),
    EntityInfo("CVV", Family.FINANCIAL, _C),
    EntityInfo("IBAN", Family.FINANCIAL, _H),
    EntityInfo("SWIFT_BIC", Family.FINANCIAL, _L),
    EntityInfo("ACCOUNT_NUMBER", Family.FINANCIAL, _H),
    EntityInfo("ROUTING_ABA", Family.FINANCIAL, _L, "", ("US",)),
    EntityInfo("IFSC", Family.FINANCIAL, _L, "", ("IN",)),
    EntityInfo("SORT_CODE", Family.FINANCIAL, _L, "", ("GB",)),
    EntityInfo("CRYPTO_ADDRESS", Family.FINANCIAL, _H),
    EntityInfo("AMOUNT", Family.FINANCIAL, _L),
    # contact
    EntityInfo("EMAIL", Family.CONTACT, _M),
    EntityInfo("PHONE", Family.CONTACT, _M),
    EntityInfo("STREET_ADDRESS", Family.CONTACT, _M),
    EntityInfo("POSTAL_CODE", Family.CONTACT, _L),
    EntityInfo("CITY", Family.CONTACT, _L),
    EntityInfo("GEO_COORDINATE", Family.CONTACT, _M),
    # temporal / online
    EntityInfo("DATE", Family.TEMPORAL, _L, "a date that is not known to be a birth date"),
    EntityInfo("IP_ADDRESS", Family.ONLINE, _M),
    EntityInfo("MAC_ADDRESS", Family.ONLINE, _M),
    EntityInfo("URL", Family.ONLINE, _L),
    EntityInfo("USERNAME", Family.ONLINE, _M),
    EntityInfo("DEVICE_ID", Family.ONLINE, _M),
    EntityInfo("COOKIE_ID", Family.ONLINE, _M),
    # secrets
    EntityInfo("API_KEY", Family.SECRET, _C),
    EntityInfo("PASSWORD", Family.SECRET, _C),
    EntityInfo("JWT", Family.SECRET, _C),
    EntityInfo("PRIVATE_KEY", Family.SECRET, _C),
    # health
    EntityInfo("MEDICAL_RECORD_NUMBER", Family.HEALTH, _H),
    EntityInfo("DIAGNOSIS_CODE", Family.HEALTH, _H),
    EntityInfo("MEDICATION", Family.HEALTH, _H),
    # organisation / location
    EntityInfo("ORG_NAME", Family.ORGANISATION, _L),
    EntityInfo("EMPLOYEE_ID", Family.ORGANISATION, _M),
    EntityInfo("CUSTOMER_ID", Family.ORGANISATION, _M),
    EntityInfo("CASE_ID", Family.ORGANISATION, _L),
    EntityInfo("LOCATION", Family.LOCATION, _L, "a place name from NER"),
)

TAXONOMY: dict[str, EntityInfo] = {e.name: e for e in _ENTRIES}

#: Entities whose co-occurrence raises a record's sensitivity (spec §5.2).
QUASI_IDENTIFIERS = frozenset(
    {"DATE_OF_BIRTH", "STREET_ADDRESS", "POSTAL_CODE", "CITY", "GEO_COORDINATE"}
)


def entity_info(name: str) -> EntityInfo:
    """Look up an entity type; unknown names are MEDIUM / OTHER."""
    return TAXONOMY.get(name) or EntityInfo(
        name, Family.OTHER, Sensitivity.MEDIUM, "custom or declared"
    )


def register_entity(info: EntityInfo) -> None:
    """Register (or replace) a custom entity type."""
    TAXONOMY[info.name] = info


def record_sensitivity(entity_types: Iterable[str]) -> Sensitivity:
    """Max tier across the record, raised for quasi-identifier co-occurrence."""
    types = set(entity_types)
    tier = Sensitivity.max_of(entity_info(t).sensitivity for t in types)
    quasi = types & QUASI_IDENTIFIERS
    if "PERSON_NAME" in types and "DATE_OF_BIRTH" in types and (quasi - {"DATE_OF_BIRTH"}):
        tier = Sensitivity.max_of([tier, Sensitivity.HIGH])
    elif "PERSON_NAME" in types and quasi:
        tier = Sensitivity.max_of([tier, Sensitivity.MEDIUM])
    return tier
