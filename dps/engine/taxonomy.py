"""Entity taxonomy (spec §5.2): families, sensitivity tiers and the registry.

The registry is open: custom recognizers and declared fields may introduce entity names that are
not listed here; they default to ``Family.OTHER`` / ``Sensitivity.MEDIUM`` unless registered.

The registry is process-global and shared by every tenant, so it only ever raises protection:
built-in entities cannot be replaced, and a custom entity's tier never drops below what every
caller already sees for that name (fail closed, spec P8).
"""

from __future__ import annotations

import dataclasses
import threading
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType


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

#: The built-in entries, read-only; ``entity_info`` consults these before the open registry.
_BUILTIN: Mapping[str, EntityInfo] = MappingProxyType({e.name: e for e in _ENTRIES})

TAXONOMY: dict[str, EntityInfo] = dict(_BUILTIN)
_REGISTRY_LOCK = threading.Lock()

#: Entities whose co-occurrence raises a record's sensitivity (spec §5.2).
QUASI_IDENTIFIERS = frozenset(
    {"DATE_OF_BIRTH", "STREET_ADDRESS", "POSTAL_CODE", "CITY", "GEO_COORDINATE"}
)


def is_builtin_entity(name: str) -> bool:
    """True for the entity types defined by this module (they cannot be re-registered)."""
    return name in _BUILTIN


def entity_info(name: str) -> EntityInfo:
    """Look up an entity type; unknown names are MEDIUM / OTHER.

    Built-in entries always win, even if the ``TAXONOMY`` dict was mutated directly.
    """
    return (
        _BUILTIN.get(name)
        or TAXONOMY.get(name)
        or EntityInfo(name, Family.OTHER, Sensitivity.MEDIUM, "custom or declared")
    )


def register_entity(info: EntityInfo) -> None:
    """Register a custom entity type, or raise the tier of one already registered.

    The registry is shared by every caller in the process, so a registration can only raise
    protection, never lower it:

    * a built-in name (``SSN``, ``EMAIL`` …) is refused with ``ValueError`` — replacing it would
      change the tier every other tenant gets;
    * for a custom name the stored tier is the max of the requested tier and the tier the name
      already has, which for a name nobody registered yet is the ``MEDIUM`` default.

    Raises:
        ValueError: when ``info.name`` is a built-in entity type.
    """
    if is_builtin_entity(info.name):
        raise ValueError(f"built-in entity type {info.name!r} cannot be re-registered")
    with _REGISTRY_LOCK:
        current = entity_info(info.name).sensitivity
        tier = Sensitivity.max_of([current, info.sensitivity])
        TAXONOMY[info.name] = dataclasses.replace(info, sensitivity=tier)


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
