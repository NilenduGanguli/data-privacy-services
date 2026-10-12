"""Tier T0 — what a key, column or field name says about its value.

``classify_key`` returns candidates sorted by score. Scores are deliberately below the default
threshold for ambiguous words (``name``, ``pan``, ``ip``) so they only pass with value evidence
from the other tiers, while unambiguous names (``ssn``, ``aadhaar``, ``cvv``) stand on their own.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from dps.engine.jsonpath import parse_path

_SPLIT = re.compile(r"[^a-z0-9]+")
#: camelCase boundaries: ``customerSsn`` → ``customer Ssn``; ``SSNLast4`` / ``IPAddress`` → the
#: acronym and the next word separate.
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_SEGMENTS = re.compile(r"[.\[\]']+")


@dataclass(frozen=True, slots=True)
class KeyCandidate:
    """One structural suggestion: the entity a key name implies, how strongly, and which rule."""

    entity_type: str
    score: float
    evidence: str


def _last_segment(key: str) -> str:
    """The last named segment: ``$.card.tags[1]['cc-number']`` → ``cc-number``.

    JSON paths (and dotted column names) go through :func:`dps.engine.jsonpath.parse_path`, so
    quoted keys containing dots or brackets stay whole; array indices are skipped. Anything that
    does not parse falls back to the text after the last ``.``/``[``/``]``/``'``.
    """
    try:
        names = [
            p for p in parse_path(key if key.startswith("$") else f"$.{key}") if isinstance(p, str)
        ]
        return names[-1] if names else ""
    except ValueError:
        raw = key[1:] if key.startswith("$") else key
        parts = [p for p in _SEGMENTS.split(raw) if p and not p.isdigit()]
        return parts[-1] if parts else ""


def _fold_accents(text: str) -> str:
    """``teléfono`` → ``telefono`` so accented keys meet the (accent-free) rule phrases."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def normalize_key(key: str) -> str:
    """Last path segment, camelCase split, accents folded, lower-cased, non-alphanumerics → spaces."""
    spaced = _CAMEL.sub(" ", _fold_accents(_last_segment(key)))
    return " ".join(t for t in _SPLIT.split(spaced.lower()) if t)


# (phrase, entity, score). Phrases match as whole words inside the normalized key.
_RULES: tuple[tuple[str, str, float], ...] = (
    ("date of birth", "DATE_OF_BIRTH", 0.9),
    ("birth date", "DATE_OF_BIRTH", 0.9),
    ("birthdate", "DATE_OF_BIRTH", 0.9),
    ("dob", "DATE_OF_BIRTH", 0.85),
    ("birthday", "DATE_OF_BIRTH", 0.8),
    ("fecha nacimiento", "DATE_OF_BIRTH", 0.9),
    ("fecha nac", "DATE_OF_BIRTH", 0.85),
    ("social security number", "SSN", 0.95),
    ("social security", "SSN", 0.9),
    ("ssn", "SSN", 0.9),
    ("nss", "SSN", 0.6),
    ("itin", "ITIN", 0.9),
    ("ein", "EIN", 0.8),
    ("social insurance", "SIN", 0.9),
    ("sin", "SIN", 0.7),
    ("curp", "CURP", 0.95),
    ("rfc", "RFC", 0.9),
    ("clave elector", "INE_CLAVE", 0.9),
    ("ine", "INE_CLAVE", 0.6),
    ("aadhaar", "AADHAAR", 0.95),
    ("aadhar", "AADHAAR", 0.95),
    ("uid", "AADHAAR", 0.5),
    ("pan number", "PAN_IN", 0.7),
    ("pan", "PAN_IN", 0.5),
    ("voter id", "VOTER_ID", 0.85),
    ("epic", "VOTER_ID", 0.5),
    ("national insurance", "NINO", 0.9),
    ("nino", "NINO", 0.9),
    ("nhs number", "NHS_NUMBER", 0.9),
    ("nhs", "NHS_NUMBER", 0.7),
    ("passport", "PASSPORT", 0.85),
    ("pasaporte", "PASSPORT", 0.85),
    ("driver license", "DRIVER_LICENSE", 0.85),
    ("drivers license", "DRIVER_LICENSE", 0.85),
    ("driving licence", "DRIVER_LICENSE", 0.85),
    ("dl number", "DRIVER_LICENSE", 0.7),
    ("licencia", "DRIVER_LICENSE", 0.6),
    ("national id", "NATIONAL_ID", 0.8),
    # the number on an identity document (KYC): Aadhaar, SSN card, passport, … in one column
    ("doc number", "NATIONAL_ID", 0.75),
    ("doc no", "NATIONAL_ID", 0.7),
    ("document number", "NATIONAL_ID", 0.75),
    ("document no", "NATIONAL_ID", 0.75),
    ("id doc number", "NATIONAL_ID", 0.75),
    ("identity number", "NATIONAL_ID", 0.6),
    ("id number", "NATIONAL_ID", 0.5),
    ("mrz", "MRZ", 0.9),
    ("card number", "CARD_PAN", 0.9),
    ("credit card", "CARD_PAN", 0.9),
    ("debit card", "CARD_PAN", 0.9),
    ("cc number", "CARD_PAN", 0.9),
    ("ccnum", "CARD_PAN", 0.9),
    ("card no", "CARD_PAN", 0.85),
    ("card pan", "CARD_PAN", 0.9),
    ("expiry", "CARD_EXPIRY", 0.7),
    ("exp date", "CARD_EXPIRY", 0.7),
    ("expiration", "CARD_EXPIRY", 0.6),
    ("valid thru", "CARD_EXPIRY", 0.7),
    ("cvv", "CVV", 0.95),
    ("cvc", "CVV", 0.95),
    ("cvv2", "CVV", 0.95),
    ("security code", "CVV", 0.7),
    ("iban", "IBAN", 0.9),
    ("swift", "SWIFT_BIC", 0.9),
    ("bic", "SWIFT_BIC", 0.8),
    ("account number", "ACCOUNT_NUMBER", 0.85),
    ("account no", "ACCOUNT_NUMBER", 0.85),
    ("acct", "ACCOUNT_NUMBER", 0.6),
    ("routing", "ROUTING_ABA", 0.85),
    ("aba", "ROUTING_ABA", 0.7),
    ("ifsc", "IFSC", 0.9),
    ("sort code", "SORT_CODE", 0.9),
    ("wallet address", "CRYPTO_ADDRESS", 0.8),
    ("wallet", "CRYPTO_ADDRESS", 0.5),
    ("email", "EMAIL", 0.9),
    ("e mail", "EMAIL", 0.9),
    ("correo", "EMAIL", 0.85),
    ("mail", "EMAIL", 0.6),
    ("phone", "PHONE", 0.85),
    ("mobile", "PHONE", 0.85),
    ("telephone", "PHONE", 0.85),
    ("tel", "PHONE", 0.7),
    ("telefono", "PHONE", 0.85),
    ("cell", "PHONE", 0.7),
    ("msisdn", "PHONE", 0.9),
    ("whatsapp", "PHONE", 0.8),
    ("street address", "STREET_ADDRESS", 0.9),
    ("address line", "STREET_ADDRESS", 0.85),
    ("address", "STREET_ADDRESS", 0.75),
    ("addr", "STREET_ADDRESS", 0.7),
    ("direccion", "STREET_ADDRESS", 0.85),
    ("street", "STREET_ADDRESS", 0.6),
    # numbered address lines (``line1``, ``address_line2``, ``addr1``): a word with its digit
    ("address line1", "STREET_ADDRESS", 0.85),
    ("address line2", "STREET_ADDRESS", 0.85),
    ("address line3", "STREET_ADDRESS", 0.85),
    ("address1", "STREET_ADDRESS", 0.8),
    ("address2", "STREET_ADDRESS", 0.8),
    ("addr1", "STREET_ADDRESS", 0.75),
    ("addr2", "STREET_ADDRESS", 0.75),
    ("street1", "STREET_ADDRESS", 0.75),
    ("street2", "STREET_ADDRESS", 0.75),
    ("line1", "STREET_ADDRESS", 0.75),
    ("line2", "STREET_ADDRESS", 0.75),
    ("line3", "STREET_ADDRESS", 0.7),
    ("postal code", "POSTAL_CODE", 0.9),
    ("postcode", "POSTAL_CODE", 0.9),
    ("zip", "POSTAL_CODE", 0.85),
    ("pin code", "POSTAL_CODE", 0.9),
    ("pincode", "POSTAL_CODE", 0.9),
    ("codigo postal", "POSTAL_CODE", 0.9),
    ("city", "CITY", 0.7),
    ("ciudad", "CITY", 0.7),
    ("latitude", "GEO_COORDINATE", 0.8),
    ("longitude", "GEO_COORDINATE", 0.8),
    ("lat", "GEO_COORDINATE", 0.5),
    ("lng", "GEO_COORDINATE", 0.5),
    ("geo", "GEO_COORDINATE", 0.5),
    ("full name", "PERSON_NAME", 0.9),
    ("first name", "PERSON_NAME", 0.9),
    ("last name", "PERSON_NAME", 0.9),
    ("surname", "PERSON_NAME", 0.9),
    ("given name", "PERSON_NAME", 0.9),
    ("family name", "PERSON_NAME", 0.9),
    ("middle name", "PERSON_NAME", 0.85),
    ("nombre", "PERSON_NAME", 0.85),
    ("apellido", "PERSON_NAME", 0.85),
    # a person's role before "name" says whose name it is (``cardholder_name``, ``payee_name``)
    ("holder name", "PERSON_NAME", 0.85),
    ("cardholder name", "PERSON_NAME", 0.9),
    ("cardholder", "PERSON_NAME", 0.8),
    ("card holder", "PERSON_NAME", 0.8),
    ("account holder", "PERSON_NAME", 0.8),
    ("accountholder name", "PERSON_NAME", 0.9),
    ("counterparty name", "PERSON_NAME", 0.85),
    ("beneficiary name", "PERSON_NAME", 0.85),
    ("payee name", "PERSON_NAME", 0.85),
    ("payer name", "PERSON_NAME", 0.85),
    ("remitter name", "PERSON_NAME", 0.85),
    ("nominee name", "PERSON_NAME", 0.85),
    ("customer name", "PERSON_NAME", 0.85),
    ("client name", "PERSON_NAME", 0.85),
    ("applicant name", "PERSON_NAME", 0.85),
    ("borrower name", "PERSON_NAME", 0.85),
    ("guarantor name", "PERSON_NAME", 0.85),
    ("contact name", "PERSON_NAME", 0.85),
    ("owner name", "PERSON_NAME", 0.85),
    ("member name", "PERSON_NAME", 0.85),
    ("patient name", "PERSON_NAME", 0.85),
    ("employee name", "PERSON_NAME", 0.85),
    ("signatory name", "PERSON_NAME", 0.85),
    ("guardian name", "PERSON_NAME", 0.85),
    ("spouse name", "PERSON_NAME", 0.85),
    ("father name", "PERSON_NAME", 0.85),
    ("mother name", "PERSON_NAME", 0.85),
    ("maiden name", "PERSON_NAME", 0.85),
    ("name", "PERSON_NAME", 0.55),
    ("gender", "GENDER", 0.8),
    ("sex", "GENDER", 0.8),
    ("genero", "GENDER", 0.8),
    ("nationality", "NATIONALITY", 0.8),
    ("citizenship", "NATIONALITY", 0.75),
    ("age", "AGE", 0.6),
    ("ip address", "IP_ADDRESS", 0.9),
    ("ip", "IP_ADDRESS", 0.6),
    ("mac address", "MAC_ADDRESS", 0.9),
    ("device id", "DEVICE_ID", 0.85),
    ("imei", "DEVICE_ID", 0.9),
    ("user agent", "DEVICE_ID", 0.5),
    ("cookie", "COOKIE_ID", 0.7),
    ("session id", "COOKIE_ID", 0.6),
    ("username", "USERNAME", 0.9),
    ("user name", "USERNAME", 0.9),
    ("login", "USERNAME", 0.6),
    ("handle", "USERNAME", 0.5),
    ("url", "URL", 0.7),
    ("website", "URL", 0.6),
    ("api key", "API_KEY", 0.95),
    ("apikey", "API_KEY", 0.95),
    ("access key", "API_KEY", 0.9),
    ("secret", "API_KEY", 0.8),
    ("token", "API_KEY", 0.7),
    ("password", "PASSWORD", 0.95),
    ("passwd", "PASSWORD", 0.95),
    ("pwd", "PASSWORD", 0.8),
    ("private key", "PRIVATE_KEY", 0.95),
    ("jwt", "JWT", 0.9),
    ("bearer", "JWT", 0.7),
    ("medical record", "MEDICAL_RECORD_NUMBER", 0.9),
    ("mrn", "MEDICAL_RECORD_NUMBER", 0.85),
    ("diagnosis", "DIAGNOSIS_CODE", 0.8),
    ("icd", "DIAGNOSIS_CODE", 0.8),
    ("medication", "MEDICATION", 0.8),
    ("prescription", "MEDICATION", 0.7),
    ("employee id", "EMPLOYEE_ID", 0.9),
    ("emp id", "EMPLOYEE_ID", 0.85),
    ("staff id", "EMPLOYEE_ID", 0.85),
    ("customer id", "CUSTOMER_ID", 0.85),
    ("cust id", "CUSTOMER_ID", 0.85),
    ("client id", "CUSTOMER_ID", 0.8),
    ("case id", "CASE_ID", 0.8),
    ("ticket", "CASE_ID", 0.5),
    ("organization", "ORG_NAME", 0.7),
    ("employer", "ORG_NAME", 0.7),
    ("empresa", "ORG_NAME", 0.7),
    ("company", "ORG_NAME", 0.6),
)

_COMPILED: tuple[tuple[re.Pattern[str], str, float, str], ...] = tuple(
    (re.compile(rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])"), entity, score, phrase)
    for phrase, entity, score in _RULES
)


def structural_entities() -> frozenset[str]:
    """Every entity type a field name can suggest (the rules above)."""
    return frozenset(entity for _, entity, _ in _RULES)


def classify_key(key: str) -> list[KeyCandidate]:
    """Candidates for ``key`` (a JSON path, column or field name), best first, one per entity."""
    norm = normalize_key(key)
    if not norm:
        return []
    best: dict[str, KeyCandidate] = {}
    for regex, entity, score, phrase in _COMPILED:
        if regex.search(norm):
            current = best.get(entity)
            if current is None or score > current.score:
                best[entity] = KeyCandidate(entity, score, f"key:{phrase}")
    return sorted(best.values(), key=lambda c: -c.score)
