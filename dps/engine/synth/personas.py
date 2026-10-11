"""One coherent persona per record anchor (spec §5.6): name, gender, e-mail, DOB, address agree.

A persona is a pure function of ``(seed, anchor, locale)`` and of the name parts it must avoid.
Each group of attributes (gender, name, birth date, address) is drawn from its own HMAC-derived
sub-seed, so asking for one attribute never shifts another: a persona built with the record's
original birth date (to keep the surrogate DOB in the same five-year band) has the same name and
address as one built without it. Faker instances are cached per thread and locale and re-seeded
for every draw.

**Never a part of the record's own name.** Faker can draw the customer's own surname, or one
that holds it (``Krishnan``, ``Radhakrishnan`` for ``Ananya Krishnan`` under ``en_IN``), and the
e-mail and username follow the name; ``en_IN`` streets and cities are named after people too
(``Krishnan Road``, ``Radhakrishnan Chowk``). So the first and last name are re-drawn (a bounded
number of times, from the same seeded sequence) while they share a part with the record's names
(:func:`shares_name_part`), then a fixed fallback is used; the address is re-drawn while its
street or city holds one (:func:`holds_name_part`). A persona that does not collide is the same
whether or not names were given.
"""

from __future__ import annotations

import hashlib
import hmac
import random
import re
import string
import threading
import unicodedata
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import date

from faker import Faker

from dps.engine.detect.patterns import region_for_locale
from dps.engine.verify import SWEEP_MIN_LEN, normalise

SUPPORTED_LOCALES = ("en_US", "en_IN", "en_GB", "en_CA", "es_MX")
#: One name token in any script: letters only (no spaces, apostrophes, digits or hyphens).
_NAME_TOKEN = re.compile(r"[^\W\d_]+")
_NOT_EMAIL = re.compile(r"[^a-z0-9]")
_MAX_NAME_DRAWS = 32
_MAX_ADDRESS_DRAWS = 32
#: Fallbacks when every draw is unusable; the first of each is the one used before names were
#: avoided, so a persona that does not collide keeps its old fallback.
_FALLBACK_FIRST = ("Alex", "Robin", "Jamie", "Casey", "Quinn")
_FALLBACK_LAST = ("Morgan", "Hayes", "Ellis", "Brooks", "Wells")
#: A name part shorter than this matches another only when equal: an initial ``A`` must not rule
#: out every name with an ``a`` in it.
_MIN_CONTAINED = 3
_BIRTH_YEARS = (1950, 2004)

_local = threading.local()


def seed_int(seed: bytes, label: str) -> int:
    """A stable 64-bit integer from ``HMAC(seed, label)``."""
    return int.from_bytes(hmac.new(seed, label.encode("utf-8"), hashlib.sha256).digest()[:8], "big")


def normalise_locale(locale: str | None) -> str:
    """Map any locale spelling to a supported one (by exact match, then by region); else en_US."""
    if not locale:
        return "en_US"
    candidate = locale.replace("-", "_")
    if candidate in SUPPORTED_LOCALES:
        return candidate
    for supported in SUPPORTED_LOCALES:
        if supported.endswith("_" + candidate.split("_")[-1].upper()):
            return supported
    return "en_US"


def ascii_fold(text: str) -> str:
    """Lower-case ASCII letters and digits only, accents dropped (``Acuña`` → ``acuna``)."""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return _NOT_EMAIL.sub("", stripped)


def faker_for(locale: str, seed: int) -> Faker:
    """This thread's Faker for ``locale``, re-seeded with ``seed`` (never the global RNG)."""
    cache: dict[str, Faker] | None = getattr(_local, "fakers", None)
    if cache is None:
        cache = _local.fakers = {}
    fake = cache.get(locale)
    if fake is None:
        fake = cache[locale] = Faker(locale)
    fake.seed_instance(seed)
    return fake


def shares_name_part(name: str, parts: frozenset[str]) -> bool:
    """True when a part of ``name`` equals, holds or is held by one of ``parts``.

    ``parts`` are normalised name parts (:func:`dps.engine.verify.name_parts`). Holding counts
    only for a part of at least three letters; a shorter one (an initial) matches only an equal
    part.
    """
    if not parts:
        return False
    for word in _NAME_TOKEN.findall(name):
        mine = normalise(word)
        for part in parts:
            if (
                mine == part
                or (len(part) >= _MIN_CONTAINED and part in mine)
                or (len(mine) >= _MIN_CONTAINED and mine in part)
            ):
                return True
    return False


def _joined_holds(text: str, parts: frozenset[str]) -> bool:
    """True when normalised ``text`` (words run together, as the leakage sweep reads it) holds a
    part of at least the sweep's minimum length: ``Krishna Nagar`` holds ``krishnan``."""
    folded = normalise(text)
    return any(len(part) >= SWEEP_MIN_LEN and part in folded for part in parts)


def holds_name_part(text: str, parts: frozenset[str]) -> bool:
    """True when a word of ``text`` equals one of ``parts`` or holds one of three letters or more,
    or the normalised text holds one (:func:`_joined_holds`).

    The test for text that is not a name (a street, a city, a company): its words may be short
    or common, so only a word that carries a part counts, not one a part carries.
    """
    if not parts:
        return False
    for word in _NAME_TOKEN.findall(text):
        mine = normalise(word)
        if any(mine == part or (len(part) >= _MIN_CONTAINED and part in mine) for part in parts):
            return True
    return _joined_holds(text, parts)


def _two_letter_names() -> Iterator[str]:
    """``Aa`` … ``Zz``: under three letters, so each matches a name part only by being equal."""
    for first in string.ascii_uppercase:
        for second in string.ascii_lowercase:
            yield first + second


def _draw_name(
    draw: Callable[[], str], fallbacks: tuple[str, ...], avoid: frozenset[str], before: str = ""
) -> str:
    """A single-token name that folds to a non-empty e-mail part and shares no part with ``avoid``,
    not even run together with the name ``before`` it (``Sanan`` + ``Yash`` holds ``ananya``).

    Up to ``_MAX_NAME_DRAWS`` draws, then the first fallback that fits, then the first two-letter
    name that fits (a record would need hundreds of name parts to exhaust those; it then gets the
    first fallback).
    """

    def fits(name: str) -> bool:
        return not shares_name_part(name, avoid) and not _joined_holds(before + name, avoid)

    for _ in range(_MAX_NAME_DRAWS):
        name = str(draw())
        if _NAME_TOKEN.fullmatch(name) and ascii_fold(name) and fits(name):
            return name
    for name in (*fallbacks, *_two_letter_names()):
        if fits(name):
            return name
    return fallbacks[0]


def _one_address(fake: Faker) -> tuple[str, str, str]:
    street = str(fake.street_address()).replace("\n", ", ")
    return street, str(fake.city()), str(fake.postcode())


def _draw_address(fake: Faker, avoid: frozenset[str]) -> tuple[str, str, str]:
    """Street, city and postcode, re-drawn while the street or the city holds a name part.

    After ``_MAX_ADDRESS_DRAWS`` draws the first is kept; the surrogate engine then retries the
    field, and the leakage sweep still rules on the output.
    """
    first = _one_address(fake)
    address = first
    for _ in range(_MAX_ADDRESS_DRAWS):
        if not holds_name_part(f"{address[0]} {address[1]}", avoid):
            return address
        address = _one_address(fake)
    return first


def birth_date(sub_seed: int, original: date | None) -> date:
    """The persona's birth date: free, or in ``original``'s five-year band and never equal to it."""
    rng = random.Random(sub_seed)
    month, day = rng.randint(1, 12), rng.randint(1, 28)
    offset, free_year = rng.randint(0, 4), rng.randint(*_BIRTH_YEARS)
    if original is None:
        return date(free_year, month, day)
    band = original.year - original.year % 5
    year = min(9999, max(1, band + offset))
    result = date(year, month, day)
    if result == original:
        result = date(year, month, day % 28 + 1)
    return result


@dataclass(frozen=True)
class Persona:
    """A synthetic person whose attributes agree with each other."""

    locale: str
    country_code: str
    gender: str
    first_name: str
    last_name: str
    email_local: str
    dob: date
    street: str
    city: str
    postal_code: str

    @property
    def full_name(self) -> str:
        """``first last``."""
        return f"{self.first_name} {self.last_name}"


class PersonaFactory:
    """Deterministic personas: same seed + anchor + locale → same persona."""

    def __init__(self, seed: bytes) -> None:
        self._seed = seed

    def _sub(self, anchor: str, locale: str, group: str) -> int:
        return seed_int(self._seed, f"persona|{anchor}|{locale}|{group}")

    def birth_date(self, anchor: str, locale: str, original: date | None) -> date:
        """The persona's birth date alone (see :func:`birth_date`)."""
        return birth_date(self._sub(anchor, normalise_locale(locale), "dob"), original)

    def for_anchor(
        self,
        anchor: str,
        locale: str,
        *,
        original_dob: date | None = None,
        gender_hint: str | None = None,
        avoid: frozenset[str] = frozenset(),
    ) -> Persona:
        """The persona for ``anchor`` in ``locale``.

        ``original_dob`` only moves the birth date into the original's five-year band;
        ``gender_hint`` (``female``/``male``) only changes the gender and first name; ``avoid``
        (normalised name parts, :func:`dps.engine.verify.name_parts`) only changes a first or last
        name that shares a part with them (:func:`shares_name_part`) and an address whose street
        or city holds one (:func:`holds_name_part`).
        """
        loc = normalise_locale(locale)
        if gender_hint in ("female", "male"):
            gender = gender_hint
        else:
            gender = (
                "female"
                if random.Random(self._sub(anchor, loc, "gender")).random() < 0.5
                else "male"
            )
        fake = faker_for(loc, self._sub(anchor, loc, f"first|{gender}"))
        first_draw = fake.first_name_female if gender == "female" else fake.first_name_male
        first = _draw_name(first_draw, _FALLBACK_FIRST, avoid)
        fake = faker_for(loc, self._sub(anchor, loc, "last"))
        last = _draw_name(fake.last_name, _FALLBACK_LAST, avoid, before=first)
        fake = faker_for(loc, self._sub(anchor, loc, "address"))
        street, city, postal_code = _draw_address(fake, avoid)
        return Persona(
            locale=loc,
            country_code=region_for_locale(loc),
            gender=gender,
            first_name=first,
            last_name=last,
            email_local=f"{ascii_fold(first)}.{ascii_fold(last)}",
            dob=birth_date(self._sub(anchor, loc, "dob"), original_dob),
            street=street,
            city=city,
            postal_code=postal_code,
        )
