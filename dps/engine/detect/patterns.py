"""Tier T1 — the regex catalogue with checksum validation and context words.

Each :class:`PatternSpec` names an entity, a compiled regex (group 1 = the value), a base score,
an optional validator (by entity key in :mod:`.checksums`), context words that raise the score,
and whether the pattern is too generic to fire without context. ``scan_text`` returns
non-overlapping :class:`Hit` objects; phones are matched by ``phonenumbers`` in ``scan_phones``.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass

import phonenumbers

from dps.engine.detect.checksums import validate

CONTEXT_WINDOW = 40
CONTEXT_BONUS = 0.1
PROMOTION_BONUS = 0.25
INVALID_FACTOR = 0.45


@dataclass(frozen=True, slots=True)
class Hit:
    """A detector hit: span, type, score and evidence (never the value)."""

    entity_type: str
    start: int
    end: int
    score: float
    detector: str
    evidence: str
    validated: bool = False


@dataclass(frozen=True, slots=True)
class PatternSpec:
    """One catalogue entry: entity, regex (group 1 = value), scoring and context rules."""

    entity_type: str
    name: str
    regex: re.Pattern[str]
    base_score: float
    validator: str | None = None
    validated_score: float = 0.98
    context: tuple[str, ...] = ()
    needs_context: bool = False
    promote_to: str | None = None
    promote_context: tuple[str, ...] = ()
    overlapping: bool = False  # try every start, so a number just before cannot shadow a match


def _p(entity: str, name: str, pattern: str, base: float, **kw: object) -> PatternSpec:
    return PatternSpec(entity, name, re.compile(pattern), base, **kw)  # type: ignore[arg-type]


_DOB_CTX = ("dob", "birth", "born", "nacimiento", "fecha de nac", "birthday", "janm")

CATALOGUE: tuple[PatternSpec, ...] = (
    _p(
        "SSN",
        "us_ssn",
        r"(?<![\d-])(\d{3}-\d{2}-\d{4})(?![\d-])",
        0.7,
        validator="SSN",
        context=("ssn", "social security", "seguro social"),
    ),
    _p(
        "SSN",
        "us_ssn_bare",
        r"(?<!\d)(\d{9})(?!\d)",
        0.4,
        validator="SSN",
        needs_context=True,
        context=("ssn", "social security"),
    ),
    _p(
        "ITIN",
        "us_itin",
        r"(?<![\d-])(9\d{2}-\d{2}-\d{4})(?![\d-])",
        0.6,
        validator="ITIN",
        context=("itin", "taxpayer"),
    ),
    _p(
        "EIN",
        "us_ein",
        r"(?<![\d-])(\d{2}-\d{7})(?![\d-])",
        0.5,
        validator="EIN",
        context=("ein", "employer id", "tax id"),
    ),
    _p(
        "SIN",
        "ca_sin",
        r"(?<![\d-])(\d{3}[- ]\d{3}[- ]\d{3})(?![\d-])",
        0.5,
        validator="SIN",
        context=("sin", "social insurance", "nas"),
    ),
    _p(
        "CURP",
        "mx_curp",
        r"\b([A-Z][AEIOUX][A-Z]{2}\d{6}[HM][A-Z]{5}[0-9A-Z]\d)\b",
        0.8,
        validator="CURP",
        context=("curp", "renapo"),
    ),
    _p(
        "RFC",
        "mx_rfc",
        r"\b([A-ZÑ&]{3,4}\d{6}[0-9A-Z]{3})\b",
        0.5,
        validator="RFC",
        context=("rfc", "registro federal", "sat"),
    ),
    _p(
        "INE_CLAVE",
        "mx_ine",
        r"\b([A-Z]{6}\d{8}[HM]\d{3})\b",
        0.55,
        context=("clave de elector", "ine", "credencial"),
    ),
    _p(
        "AADHAAR",
        "in_aadhaar",
        # 12 digits of a card printed in fours also match: ``_settle_aadhaar_vs_card`` decides
        r"(?<!\d)([2-9]\d{3}[ -]?\d{4}[ -]?\d{4})(?!\d)",
        0.6,
        validator="AADHAAR",
        context=("aadhaar", "aadhar", "uidai", "uid"),
        overlapping=True,  # in "2019 2341 2341 2346" the window starting at 2019 must not win
    ),
    _p(
        "PAN_IN",
        "in_pan",
        r"\b([A-Z]{3}[ABCFGHLJPT][A-Z]\d{4}[A-Z])\b",
        0.6,
        validator="PAN_IN",
        context=("pan", "permanent account", "income tax"),
    ),
    _p(
        "NINO",
        "gb_nino",
        r"\b([A-CEGHJ-PR-TW-Z]{2} ?\d{2} ?\d{2} ?\d{2} ?[A-D])\b",
        0.6,
        validator="NINO",
        context=("national insurance", "nino", "ni number"),
    ),
    _p(
        "NHS_NUMBER",
        "gb_nhs",
        r"(?<!\d)(\d{3} ?\d{3} ?\d{4})(?!\d)",
        0.3,
        validator="NHS_NUMBER",
        needs_context=True,
        context=("nhs",),
    ),
    _p(
        "CARD_PAN",
        "card_pan",
        r"(?<![\d-])((?:\d[ -]?){12,18}\d)(?![\d-])",
        0.45,
        validator="CARD_PAN",
        validated_score=0.95,
        context=("card", "credit", "debit", "visa", "mastercard", "tarjeta", "pan"),
    ),
    _p(
        "CARD_EXPIRY",
        "card_expiry",
        r"\b((?:0[1-9]|1[0-2])/(?:\d{2}|\d{4}))\b",
        0.5,
        needs_context=True,
        context=("exp", "expiry", "valid thru", "vencimiento"),
    ),
    _p(
        "IBAN",
        "iban",
        r"\b([A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,4})?)\b",
        0.5,
        validator="IBAN",
        context=("iban",),
    ),
    _p(
        "SWIFT_BIC",
        "bic",
        r"\b([A-Z]{6}[A-Z2-9][A-NP-Z0-9](?:[A-Z0-9]{3})?)\b",
        0.3,
        validator="SWIFT_BIC",
        needs_context=True,
        context=("swift", "bic", "bank identifier"),
    ),
    _p(
        "ROUTING_ABA",
        "aba",
        r"(?<!\d)(\d{9})(?!\d)",
        0.4,
        validator="ROUTING_ABA",
        needs_context=True,
        context=("routing", "aba"),
    ),
    _p("IFSC", "ifsc", r"\b([A-Z]{4}0[A-Z0-9]{6})\b", 0.6, needs_context=True, context=("ifsc",)),
    _p(
        "SORT_CODE",
        "sort_code",
        r"\b(\d{2}-\d{2}-\d{2})\b",
        0.5,
        needs_context=True,
        context=("sort code",),
    ),
    _p(
        "EMAIL",
        "email",
        r"(?<![A-Za-z0-9._%+\-])([A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,24})",
        0.9,
        context=("email", "e-mail", "correo", "mail"),
    ),
    _p(
        "IP_ADDRESS",
        "ipv4",
        r"(?<![\d.])((?:25[0-5]|2[0-4]\d|1?\d?\d)(?:\.(?:25[0-5]|2[0-4]\d|1?\d?\d)){3})(?![\d.])",
        0.85,
    ),
    _p("IP_ADDRESS", "ipv6", r"\b((?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4})\b", 0.8),
    _p("MAC_ADDRESS", "mac", r"\b([0-9A-Fa-f]{2}(?:[:-][0-9A-Fa-f]{2}){5})\b", 0.8),
    _p("URL", "url", r"\b(https?://[^\s<>\"']+)", 0.7),
    _p(
        "DATE",
        "date_iso",
        r"(?<!\d)(\d{4}-\d{2}-\d{2})(?!\d)",
        0.6,
        promote_to="DATE_OF_BIRTH",
        promote_context=_DOB_CTX,
    ),
    _p(
        "DATE",
        "date_slash",
        r"(?<!\d)(\d{1,2}/\d{1,2}/\d{4})(?!\d)",
        0.55,
        promote_to="DATE_OF_BIRTH",
        promote_context=_DOB_CTX,
    ),
    _p(
        "DATE",
        "date_text",
        r"\b(\d{1,2} (?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*,? \d{4})\b",
        0.6,
        promote_to="DATE_OF_BIRTH",
        promote_context=_DOB_CTX,
    ),
    _p(
        "POSTAL_CODE",
        "us_zip",
        r"\b(\d{5}(?:-\d{4})?)\b",
        0.5,
        needs_context=True,
        context=("zip", "postal", "pin code", "pincode", "pin"),
    ),
    _p(
        "POSTAL_CODE",
        "in_pin",
        r"\b([1-9]\d{5})\b",
        0.5,
        needs_context=True,
        context=("pin code", "pincode", "pin", "postal"),
    ),
    _p(
        "POSTAL_CODE",
        "gb_postcode",
        r"\b([A-Z]{1,2}\d[A-Z\d]? ?\d[A-Z]{2})\b",
        0.6,
        context=("postcode", "postal"),
    ),
    _p("POSTAL_CODE", "ca_postal", r"\b([A-Z]\d[A-Z] ?\d[A-Z]\d)\b", 0.6, context=("postal",)),
    _p("API_KEY", "aws_access_key", r"\b(AKIA[0-9A-Z]{16})\b", 0.95),
    _p("API_KEY", "generic_sk", r"\b((?:sk|pk|rk)_(?:live|test)_[A-Za-z0-9]{16,})\b", 0.9),
    _p("API_KEY", "github_token", r"\b(gh[pousr]_[A-Za-z0-9]{36,})\b", 0.95),
    _p("API_KEY", "google_api_key", r"\b(AIza[0-9A-Za-z_\-]{35})\b", 0.95),
    _p("API_KEY", "slack_token", r"\b(xox[baprs]-[A-Za-z0-9-]{10,})\b", 0.95),
    _p(
        "JWT",
        "jwt",
        r"\b(eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,})\b",
        0.95,
    ),
    _p(
        "PRIVATE_KEY",
        "pem_private_key",
        r"(-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----)",
        0.99,
    ),
    _p("PASSWORD", "password_assignment", r"(?i)password\s*[:=]\s*(\S{4,})", 0.8),
    _p(
        "CRYPTO_ADDRESS",
        "btc",
        r"\b([13][a-km-zA-HJ-NP-Z1-9]{25,34}|bc1[a-z0-9]{39,59})\b",
        0.6,
        context=("btc", "bitcoin", "wallet"),
    ),
    _p("CRYPTO_ADDRESS", "eth", r"\b(0x[a-fA-F0-9]{40})\b", 0.7),
    _p("GEO_COORDINATE", "latlon", r"(-?\d{1,2}\.\d{3,},\s*-?\d{1,3}\.\d{3,})", 0.6),
    _p(
        "PASSPORT",
        "passport_generic",
        r"\b([A-Z]{1,2}\d{6,8})\b",
        0.5,
        needs_context=True,
        context=("passport", "pasaporte"),
    ),
    _p(
        "DRIVER_LICENSE",
        "dl_generic",
        r"\b([A-Z]{1,2}\d{5,8})\b",
        0.45,
        needs_context=True,
        context=("driver", "licence", "license", "dl no"),
    ),
    _p("USERNAME", "handle", r"(?<!\w)(@[A-Za-z0-9_]{3,30})\b", 0.5, context=("user", "handle")),
)

_REGION_BY_LOCALE = {"US": "US", "IN": "IN", "MX": "MX", "CA": "CA", "GB": "GB"}


def region_for_locale(locale: str) -> str:
    """``en_IN`` → ``IN``; unknown → ``US``."""
    tail = locale.replace("-", "_").split("_")[-1].upper()
    return _REGION_BY_LOCALE.get(tail, "US")


def _context(text: str, start: int, end: int, key_hint: str | None) -> str:
    before = text[max(0, start - CONTEXT_WINDOW) : start]
    after = text[end : end + CONTEXT_WINDOW]
    return f"{before} {after} {key_hint or ''}".lower()


def resolve_overlaps(hits: list[Hit]) -> list[Hit]:
    """Keep the best hit per overlapping region: validated, then score, then length."""
    ranked = sorted(hits, key=lambda h: (not h.validated, -h.score, -(h.end - h.start), h.start))
    kept: list[Hit] = []
    for hit in ranked:
        if all(hit.end <= k.start or hit.start >= k.end for k in kept):
            kept.append(hit)
    return sorted(kept, key=lambda h: h.start)


_GROUP_SEPARATOR = re.compile(r"[ -]")


def _printed_in_fours(value: str) -> bool:
    """A card number as cards are printed: 15+ digits in fours, the last group 1-4 long.

    4-4-4-3 (15), 4-4-4-4 (16) and 4-4-4-4-1..3 (17-19) qualify. An Aadhaar with a short number
    beside it (4-4-4-1, 4-4-4-2, 1-4-4-4, 4-4-4-6, 8-4-4) does not.
    """
    groups = _GROUP_SEPARATOR.split(value)
    return (
        len(groups) >= 4
        and all(len(g) == 4 for g in groups[:-1])
        and 1 <= len(groups[-1]) <= 4
        and sum(map(len, groups)) >= 15
    )


def _settle_aadhaar_vs_card(text: str, hits: list[Hit]) -> list[Hit]:
    """Decide each validated AADHAAR that overlaps a validated CARD_PAN; drop the loser.

    Two cases look alike. About one 12-digit window in ten is Verhoeff-valid, so a card printed
    in fours (``4141 7058 6492 0836``) often holds a "valid Aadhaar"; that is still a card, and
    an Aadhaar hit would leave its other digits in clear under the wrong type. An Aadhaar next to
    another number (``2341 2341 2346 7 copies``) forms a Luhn-valid 13-19 digit window about one
    time in ten; that is still an Aadhaar, and as a CARD_PAN its first six digits would stay in
    clear under the card presets (``keep_prefix: 6``). The card's grouping decides, before
    ``resolve_overlaps``, so a tie in the context scores can never flip it. An Aadhaar followed by
    a 3- or 4-digit group stays ambiguous: when the run is Luhn-valid it counts as a card.
    """
    cards = [h for h in hits if h.entity_type == "CARD_PAN" and h.validated]
    if not cards:
        return hits

    def overlaps(a: Hit, b: Hit) -> bool:
        return a.start < b.end and b.start < a.end

    printed = [c for c in cards if _printed_in_fours(text[c.start : c.end])]
    aadhaars = [h for h in hits if h.entity_type == "AADHAAR" and h.validated]
    losers = [a for a in aadhaars if any(overlaps(a, c) for c in printed)]
    survivors = [a for a in aadhaars if a not in losers]
    losers += [c for c in cards if c not in printed and any(overlaps(c, a) for a in survivors)]
    return [h for h in hits if h not in losers]


def _matches(spec: PatternSpec, text: str) -> Iterator[re.Match[str]]:
    """``finditer``, or with ``overlapping`` a search from every start after the last match's."""
    if not spec.overlapping:
        yield from spec.regex.finditer(text)
        return
    pos = 0
    while (match := spec.regex.search(text, pos)) is not None:
        yield match
        pos = match.start(1) + 1


def scan_text(text: str, *, key_hint: str | None = None) -> list[Hit]:
    """Run the catalogue over ``text``; ``key_hint`` (a JSON key or column name) counts as context."""
    hits: list[Hit] = []
    for spec in CATALOGUE:
        for match in _matches(spec, text):
            start, end = match.span(1)
            value = match.group(1)
            ctx = _context(text, start, end, key_hint)
            has_ctx = any(word in ctx for word in spec.context)
            if spec.needs_context and not has_ctx:
                continue
            score, validated, evidence = spec.base_score, False, f"pattern:{spec.name}"
            if spec.validator:
                ok = validate(spec.validator, value)
                if ok:
                    score, validated, evidence = (
                        spec.validated_score,
                        True,
                        f"checksum:{spec.validator}",
                    )
                elif ok is False:
                    score *= INVALID_FACTOR
            entity = spec.entity_type
            if spec.promote_to and any(word in ctx for word in spec.promote_context):
                entity, score = spec.promote_to, min(1.0, score + PROMOTION_BONUS)
            if has_ctx:
                score = min(1.0, score + CONTEXT_BONUS)
            hits.append(
                Hit(
                    entity, start, end, round(score, 3), f"pattern:{spec.name}", evidence, validated
                )
            )
    return resolve_overlaps(_settle_aadhaar_vs_card(text, hits))


def scan_phones(text: str, *, region: str = "US") -> list[Hit]:
    """Phone numbers via ``phonenumbers`` (valid numbers only, national or international)."""
    hits: list[Hit] = []
    try:
        matcher = phonenumbers.PhoneNumberMatcher(
            text, region, leniency=phonenumbers.Leniency.VALID
        )
        for m in matcher:
            hits.append(
                Hit("PHONE", m.start, m.end, 0.85, "pattern:phone", f"phone:{region}", True)
            )
    except Exception:  # noqa: BLE001 - a matcher error must never fail detection
        return []
    return hits
