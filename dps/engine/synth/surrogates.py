"""Rule-valid, locale-aware, seeded surrogates (spec §5.6).

``SurrogateEngine.surrogate(entity_type, original, locale, anchor)`` is a pure function of
``(seed, entity type, canonical original)`` for value-level consistency (the canonical form is the
verifier's normalisation, so ``2341 2341 2346`` and ``234123412346`` get the same digits, each in
its own layout). Persona fields (name, e-mail, birth date, address, ...) also depend on the
record's ``anchor`` so one record stays coherent. Nothing depends on request order, and what else
the record holds matters only when a draw would reuse a part of it ("Never a part of the record"
below): the same seed gives the same fake across fields, tables, requests and systems (spec §5.6,
P3).

Generated identifiers are *valid in format* and *never issued* where an unissued range exists:
SSNs in the 900 area, ITINs in groups 89/93, SINs starting with 0, Aadhaar numbers starting with
1 (Verhoeff-valid; UIDAI never issues 0/1), cards on the 411111 test IIN, e-mails on RFC 2606
domains, NANP 555-01xx and Ofcom 07700 900xxx phones, private / documentation IP ranges and
locally administered MACs. Credentials are never synthesised; they are redacted.

Guarantees beyond the plan's generators:

* **Never revealing.** A surrogate never equals its original, nor contains it (the leakage
  sweep's own normalised test), except for low-cardinality attributes (gender, nationality)
  where forcing a different value would itself reveal the original. Retries are salted with the
  canonical original only, so they stay pure; if every attempt reveals, the last resort is a
  seeded token of the original's length that cannot contain it (never ``original + suffix``).
* **Never a part of the record.** A name surrogate shares no part with the name it replaces.
  Once the record's detected values are declared (:meth:`SurrogateEngine.declare_record`, which
  ``apply_plan`` and ``synthesize_text`` call before any field), the record's persona shares no
  part with its names (name, e-mail, username, street, city, MRZ holder), and any surrogate that
  would hold another of the record's values (the sweep's test: normalised, four characters or
  more) or a part of its names is drawn again, so a coincidence such as a phone surrogate holding
  a card expiry's four digits does not trip the leakage sweep. Under ``en_IN`` Faker otherwise
  draws the customer's own surname (``Krishnan``), or a surname or street holding it, in about
  one record in a hundred. If every draw of a value holds another of the record's values by
  construction, the first that does not reveal its own original is kept and the sweep rules on
  it. A record's declaration changes a surrogate only when a draw collides, so the same seed
  still gives the same fake across records and requests everywhere else.
* **Reshaped identifiers are a keyed derangement** (``reshape.py``): identifiers of unmapped
  types, CVVs, sort codes, IFSC branch codes, amounts, diagnosis codes and BICs keep their
  layout and have their digits and letters (every letter, in its own script) mapped by a
  seed-keyed, blockwise permutation of the shape's value space without fixed points. Two
  different originals of the same shape never share a surrogate, so foreign keys, joins and
  counts survive, in any order and across requests. In an identifier of an unmapped type only
  short codes stay (``CUST-``, ``MH14``: :func:`~dps.engine.synth.reshape.code_letters`); names
  and words next to digits are replaced like any other letters. Amounts keep their currency.
* **Documents and wallets are rebuilt, not reshaped** (``formats.py``): an ICAO MRZ carries
  the record's persona as holder (never one sharing a name part with the original), a fresh
  document number and optional data, the persona's birth date, a shifted expiry date and
  recomputed check digits; the document code and countries stay. Bitcoin and Ethereum
  addresses are fresh valid addresses of the original's kind.
* **Other generators** draw from a fixed output space (SSNs in the 900 area, ITINs, SINs,
  Aadhaar, test-IIN cards, fictional phone ranges, e-mails, IBANs, ...), so two originals can
  share a surrogate with a probability set by that space: about ``n²/2N`` for ``n`` originals
  in a space of ``N``. The tight ones are UK numbers (Ofcom's drama range has 1,000 per format),
  ITINs (~2·10⁶), SINs (10⁷) and medications (20 names).

An engine is not thread-safe; create one per request (the service does).
"""

from __future__ import annotations

import random
import re
import secrets
import unicodedata
from collections.abc import Callable, Container, Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

import phonenumbers
from pydantic import BaseModel, ConfigDict, Field
from stdnum import iban as stdnum_iban

from dps.engine.detect.checksums import luhn_check_digit, verhoeff_check_digit
from dps.engine.synth.formats import (
    crypto_surrogate,
    fill_layout,
    mrz_name,
    seal_mrz,
    split_mrz,
)
from dps.engine.synth.personas import (
    Persona,
    PersonaFactory,
    ascii_fold,
    faker_for,
    holds_name_part,
    normalise_locale,
    seed_int,
)
from dps.engine.synth.reshape import Reshaper, code_letters
from dps.engine.transform.crypto import b64u, unb64u
from dps.engine.verify import SWEEP_MIN_LEN, name_parts, normalise

SEED_ALG = "dps.synth.v1"
SEED_BYTES = 32
RFC2606_DOMAINS = ("example.com", "example.net", "example.org")
TEST_CARD_IIN = "411111"
REDACTED = "[REDACTED]"
_UPPER = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_PAN_LETTERS = "ABCDEFGHJKLMNPQRSTUVWXYZ"
_HEX = "0123456789abcdef"
_DIGIT = re.compile(r"\d")
_INT = re.compile(r"\d+")
_NATIONALITY = {
    "US": "American",
    "IN": "Indian",
    "MX": "Mexican",
    "CA": "Canadian",
    "GB": "British",
}
_SLASH_FORMATS = ("%d/%m/%Y", "%m/%d/%Y", "%d %b %Y", "%d %B %Y")
_ISO_DATE = re.compile(r"(\s*)([0-9]{4}-[0-9]{2}-[0-9]{2})([T ][0-9]{2}:[0-9]{2}\S*)?(\s*)")
_MEDICATIONS = (
    "Amlodipine",
    "Atorvastatin",
    "Azithromycin",
    "Cetirizine",
    "Clopidogrel",
    "Furosemide",
    "Gabapentin",
    "Ibuprofen",
    "Levothyroxine",
    "Lisinopril",
    "Losartan",
    "Metoprolol",
    "Montelukast",
    "Omeprazole",
    "Pantoprazole",
    "Paracetamol",
    "Rosuvastatin",
    "Sertraline",
    "Simvastatin",
    "Tamsulosin",
)
_ATTEMPTS = 32
_TOKEN_ATTEMPTS = 64
_MRZ_HEAD = re.compile(r"[A-Z<]{5}")  # document code and issuing state
_YYMMDD = re.compile(r"[0-9]{6}")

#: Credentials are never synthesised.
_REDACT = frozenset({"API_KEY", "PASSWORD", "JWT", "PRIVATE_KEY"})
#: Low-cardinality attributes: forcing "different from the original" would reveal it.
_IDENTITY_OK = _REDACT | {"GENDER", "NATIONALITY"}
#: The entity whose parts a persona avoids.
_NAME = "PERSON_NAME"
#: Declared values shorter than this are not avoided (the leakage sweep skips them too).
_MIN_DECLARED = SWEEP_MIN_LEN


@dataclass(frozen=True)
class _Record:
    """What a record declared: its names' parts and its normalised values (no raw values), the
    values indexed by their first ``_MIN_DECLARED`` characters so a check is linear in the
    surrogate's length, not in the size of the record."""

    parts: frozenset[str] = frozenset()
    index: dict[str, tuple[str, ...]] = field(default_factory=dict)

    @classmethod
    def of(cls, parts: frozenset[str], values: Iterable[str]) -> _Record:
        index: dict[str, tuple[str, ...]] = {}
        for value in set(values):
            index[value[:_MIN_DECLARED]] = (*index.get(value[:_MIN_DECLARED], ()), value)
        return cls(parts=parts, index=index)

    def held_by(self, canon: str, value: str) -> bool:
        """True when ``value`` holds another of the record's values or a part of its names."""
        folded = normalise(value)
        for i in range(len(folded) - _MIN_DECLARED + 1):
            for declared in self.index.get(folded[i : i + _MIN_DECLARED], ()):
                if declared != canon and folded.startswith(declared, i):
                    return True
        return holds_name_part(value, self.parts)


_NOTHING = _Record()


class SynthesisSeed(BaseModel):
    """The caller-held seed that makes synthesis repeatable (confidential: it enables linking)."""

    model_config = ConfigDict(hide_input_in_errors=True)

    alg: str = SEED_ALG
    seed: str = Field(repr=False)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


def generate_synthesis_seed() -> SynthesisSeed:
    """A fresh 256-bit seed."""
    return SynthesisSeed(seed=b64u(secrets.token_bytes(SEED_BYTES)))


def seed_bytes(seed: SynthesisSeed) -> bytes:
    """The raw 32 seed bytes; ``ValueError`` (naming the rule only) when malformed."""
    try:
        raw = unb64u(seed.seed)
    except ValueError:
        raise ValueError("synthesis seed must be unpadded base64url") from None
    if len(raw) != SEED_BYTES:
        raise ValueError(f"synthesis seed must be {SEED_BYTES} bytes")
    return raw


def seed_from_caller(payload: dict[str, Any]) -> SynthesisSeed:
    """Validate a caller-presented seed; errors never echo it."""
    seed = SynthesisSeed.model_validate(payload)
    if seed.alg != SEED_ALG:
        raise ValueError(f"unsupported synthesis seed alg; expected {SEED_ALG!r}")
    seed_bytes(seed)
    return seed


# --- shape helpers -----------------------------------------------------------------------------
def _digits(rng: random.Random, n: int) -> str:
    return "".join(str(rng.randint(0, 9)) for _ in range(n))


def _reshape_digits(original: str, digits: str) -> str:
    """Replace the digit positions of ``original`` in order with ``digits`` (same separators)."""
    it = iter(digits)
    return "".join(next(it, ch) if ch.isdigit() else ch for ch in original)


def _fit(original: str, digits: str) -> str:
    """``digits`` in the original's layout when it has exactly as many digits, else bare."""
    return (
        _reshape_digits(original, digits)
        if len(_DIGIT.findall(original)) == len(digits)
        else digits
    )


def _us_tin(original: str, digits: str) -> str:
    """Nine digits in the original's layout, else as ``AAA-GG-SSSS``."""
    if len(_DIGIT.findall(original)) == len(digits):
        return _reshape_digits(original, digits)
    return f"{digits[:3]}-{digits[3:5]}-{digits[5:]}"


def _reshape_hex(original: str, rng: random.Random) -> str:
    """Every letter or digit (any script) becomes a random hex digit; separators kept."""
    upper = any(c.isupper() for c in original) and not any(c.islower() for c in original)
    out = []
    for ch in unicodedata.normalize("NFKD", original):
        if unicodedata.combining(ch):
            continue  # an accent on a replaced letter
        if ch.isalnum():
            digit = rng.choice(_HEX)
            out.append(digit.upper() if upper else digit)
        else:
            out.append(ch)
    return "".join(out)


def _parse_date(text: str) -> tuple[date, Callable[[date], str]] | None:
    """The date in ``text`` and a renderer that writes another date in the same layout.

    ISO dates keep surrounding whitespace and any time-of-day suffix verbatim.
    """
    iso = _ISO_DATE.fullmatch(text)
    if iso:
        try:
            parsed = date.fromisoformat(iso.group(2))
        except ValueError:
            return None
        lead, suffix, trail = iso.group(1), iso.group(3) or "", iso.group(4)
        return parsed, lambda d: f"{lead}{d.isoformat()}{suffix}{trail}"
    stripped = text.strip()
    for fmt in _SLASH_FORMATS:
        try:
            parsed = datetime.strptime(stripped, fmt).date()
        except ValueError:
            continue
        return parsed, lambda d, fmt=fmt: d.strftime(fmt)
    return None


def _shift(day: date, days: int) -> date:
    try:
        return day + timedelta(days=days)
    except OverflowError:
        return day - timedelta(days=days)


def _redraw(field: str, rng: random.Random) -> str:
    """A letter for every letter and a digit for every digit; ``<`` fillers stay."""
    return "".join(
        rng.choice(_UPPER) if ch.isalpha() else str(rng.randint(0, 9)) if ch.isdigit() else ch
        for ch in field
    )


def _yymmdd(text: str, century: int) -> date | None:
    """An MRZ date in ``century`` (the next one when the day does not exist there)."""
    if not _YYMMDD.fullmatch(text):
        return None
    for base in (century, century + 100):
        try:
            return date(base + int(text[:2]), int(text[2:4]), int(text[4:]))
        except ValueError:
            continue
    return None


def _holds(name_field: str, part: str) -> bool:
    """True when an MRZ name field holds the original holder's name ``part``."""
    return part in name_field.split("<") or (len(part) >= 3 and part in name_field)


def _reveals(canon: str, value: str) -> bool:
    """True when ``value`` would let the leakage sweep find the original in it."""
    folded = normalise(value)
    return folded == canon or (len(canon) >= 4 and canon in folded)


class SurrogateEngine:
    """Implements ``SurrogateProvider`` for the ``surrogate`` method."""

    def __init__(self, seed: bytes, default_locale: str = "en_US") -> None:
        self._seed = seed
        self._default_locale = normalise_locale(default_locale)
        self._personas = PersonaFactory(seed)
        self._cache: dict[tuple[str, str, frozenset[str]], Persona] = {}
        self._records: dict[str, _Record] = {}
        self._reshaper = Reshaper(seed)
        self._generators: dict[str, Callable[[str, str, str, random.Random], str]] = {
            "PERSON_NAME": self._person_name,
            "DATE_OF_BIRTH": self._date_of_birth,
            "DATE": self._date,
            "EMAIL": self._email,
            "PHONE": self._phone_for,
            "SSN": self._ssn,
            "ITIN": self._itin,
            "AADHAAR": self._aadhaar,
            "PAN_IN": self._pan_in,
            "CARD_PAN": self._card_pan,
            "CARD_EXPIRY": self._card_expiry,
            "CVV": self._cvv,
            "IBAN": self._iban,
            "ROUTING_ABA": self._aba,
            "SIN": self._sin,
            "STREET_ADDRESS": self._street,
            "CITY": lambda o, loc, a, r: self.persona(a, loc).city,
            "LOCATION": lambda o, loc, a, r: self.persona(a, loc).city,
            "POSTAL_CODE": lambda o, loc, a, r: self.persona(a, loc).postal_code,
            "IP_ADDRESS": self._ip,
            "MAC_ADDRESS": self._mac,
            "URL": lambda o, loc, a, r: f"https://example.com/{r.getrandbits(32):08x}",
            "USERNAME": self._username,
            "DEVICE_ID": self._device_id,
            "COOKIE_ID": self._device_id,
            "GENDER": self._gender,
            "NATIONALITY": lambda o, loc, a, r: _NATIONALITY.get(
                self.persona(a, loc).country_code, "Unknown"
            ),
            "AGE": self._age,
            "ORG_NAME": lambda o, loc, a, r: str(faker_for(loc, r.getrandbits(64)).company()),
            "MEDICATION": lambda o, loc, a, r: r.choice(_MEDICATIONS),
            "MRZ": self._mrz,
            "CRYPTO_ADDRESS": self._crypto,
            # the currency code or symbol is format
            "AMOUNT": lambda o, loc, a, r: self._reshape("AMOUNT", o, r, False),
            # letters and digits: the ICD chapter letter alone reveals the condition class
            "DIAGNOSIS_CODE": lambda o, loc, a, r: self._reshape("DIAGNOSIS_CODE", o, r, True),
            # the country code (positions 4-5) stays
            "SWIFT_BIC": lambda o, loc, a, r: self._reshape(
                "SWIFT_BIC", o, r, True, keep=range(4, 6)
            ),
            # the bank code and the reserved fifth character "0" stay; the branch code goes
            "IFSC": lambda o, loc, a, r: self._reshape("IFSC", o, r, True, keep=range(0, 5)),
            "SORT_CODE": lambda o, loc, a, r: self._reshape("SORT_CODE", o, r, False),
        }

    def declare_record(self, anchor: str, originals: Iterable[tuple[str, str]]) -> None:
        """Declare the record's detected values, as ``(entity_type, original)`` pairs.

        Call it before synthesising any field of the record, so every field draws on the same
        persona. It replaces what was declared for ``anchor`` before (a record's surrogates
        depend on that record's values, never on another's); the engine's own retries of the
        record (``anchor|retry1``, ``anchor|mrz2``) inherit it. Only normalised forms and name
        parts are kept, and only in this engine.
        """
        pairs = list(originals)
        record = _Record.of(
            name_parts(original for entity, original in pairs if entity == _NAME),
            (canon for _, original in pairs if len(canon := normalise(original)) >= _MIN_DECLARED),
        )
        if record.parts or record.index:
            self._records[anchor] = record
        else:
            self._records.pop(anchor, None)

    def _declared(self, anchor: str) -> _Record:
        """What was declared for ``anchor``, or for the record it is a retry of."""
        while True:
            record = self._records.get(anchor)
            if record is not None:
                return record
            if "|" not in anchor:
                return _NOTHING
            anchor = anchor.rsplit("|", 1)[0]

    def persona(self, anchor: str, locale: str, avoid: Iterable[str] = ()) -> Persona:
        """The record's persona (cached), sharing no part with the record's declared names or
        with the names in ``avoid``."""
        loc = normalise_locale(locale)
        parts = self._declared(anchor).parts | name_parts(avoid)
        key = (anchor, loc, parts)
        if key not in self._cache:
            self._cache[key] = self._personas.for_anchor(anchor, loc, avoid=parts)
        return self._cache[key]

    def surrogate(self, entity_type: str, original: str, locale: str, anchor: str) -> str:
        """A synthetic stand-in for ``original`` (see the module docstring for the guarantees).

        A pure function of the seed, the entity type, the canonical original, what the record
        declared and (for persona fields) the anchor: it never depends on what this engine was
        asked before.
        """
        if not original.strip():
            return original
        loc = normalise_locale(locale or self._default_locale)
        canon = normalise(original)
        record = self._declared(anchor)
        kept: str | None = None
        for attempt in range(_ATTEMPTS):
            salt = "" if attempt == 0 else f"|retry{attempt}"
            rng = random.Random(seed_int(self._seed, f"{entity_type}|{canon}{salt}"))
            value = self._generate(entity_type, original, loc, anchor + salt, rng)
            if entity_type in _IDENTITY_OK:
                return value
            if _reveals(canon, value):
                continue
            if not record.held_by(canon, value):
                return value
            kept = kept if kept is not None else value
        if kept is not None:
            return kept  # every draw holds another value of the record: the sweep rules on it
        return self._last_resort(entity_type, original, canon, record)

    def _last_resort(self, entity_type: str, original: str, canon: str, record: _Record) -> str:
        """A seeded token of the original's length that does not reveal it (nor, if it can be
        helped, hold another of the record's values)."""
        kept: str | None = None
        for attempt in range(_TOKEN_ATTEMPTS):
            rng = random.Random(seed_int(self._seed, f"{entity_type}|{canon}|token{attempt}"))
            token = "".join(rng.choice(_HEX) for _ in original)
            if _reveals(canon, token):
                continue
            if not record.held_by(canon, token):
                return token
            kept = kept if kept is not None else token
        return kept if kept is not None else REDACTED  # 64 hex draws all revealing: unreachable

    def _reshape(
        self,
        entity: str,
        original: str,
        rng: random.Random,
        letters: bool,
        *,
        keep: Container[int] | None = None,
    ) -> str:
        return self._reshaper.reshape(entity, original, rng, letters=letters, keep=keep)

    # -- generators -----------------------------------------------------------------------------
    def _generate(
        self, entity: str, original: str, loc: str, anchor: str, rng: random.Random
    ) -> str:
        if entity in _REDACT:
            return REDACTED
        generator = self._generators.get(entity)
        if generator is not None:
            return generator(original, loc, anchor, rng)
        # identifiers of any other kind: keep the layout and replace every digit and letter (any
        # script), except the short codes of an identifier (CUST-, MH14: see code_letters)
        return self._reshape(entity, original, rng, True, keep=code_letters(original) or None)

    def _person_name(self, original: str, loc: str, anchor: str, rng: random.Random) -> str:
        # never a part of the name it replaces, even when the record's names were not declared
        # (declared names include it, so this is then the record's persona)
        p = self.persona(anchor, loc, avoid=(original,))
        name = p.first_name if len(original.split()) <= 1 else p.full_name
        return name.upper() if original.isupper() else name

    def _date_of_birth(self, original: str, loc: str, anchor: str, rng: random.Random) -> str:
        parsed = _parse_date(original)
        if parsed is None:
            return self.persona(anchor, loc).dob.isoformat()
        day, render = parsed
        return render(self._personas.birth_date(anchor, loc, day))

    def _date(self, original: str, loc: str, anchor: str, rng: random.Random) -> str:
        parsed = _parse_date(original)
        if parsed is None:  # unreadable: a plausible ISO date, never the original text
            return (date(2000, 1, 1) + timedelta(days=rng.randint(0, 11_322))).isoformat()
        day, render = parsed
        return render(_shift(day, rng.choice([d for d in range(-30, 31) if d != 0])))

    def _email(self, original: str, loc: str, anchor: str, rng: random.Random) -> str:
        local = self.persona(anchor, loc).email_local
        return f"{local}{rng.randint(1, 9999)}@{rng.choice(RFC2606_DOMAINS)}"

    def _phone_for(self, original: str, loc: str, anchor: str, rng: random.Random) -> str:
        return self._phone(original, self.persona(anchor, loc).country_code, rng)

    def _ssn(self, original: str, loc: str, anchor: str, rng: random.Random) -> str:
        group = f"{rng.randint(1, 99):02d}"
        return _us_tin(original, f"9{rng.randint(0, 99):02d}{group}{rng.randint(1, 9999):04d}")

    def _itin(self, original: str, loc: str, anchor: str, rng: random.Random) -> str:
        group = rng.choice(("89", "93"))  # outside every group the IRS issues ITINs in
        return _us_tin(original, f"9{rng.randint(0, 99):02d}{group}{rng.randint(1, 9999):04d}")

    def _aadhaar(self, original: str, loc: str, anchor: str, rng: random.Random) -> str:
        body = "1" + _digits(rng, 10)  # UIDAI never issues numbers starting with 0 or 1
        digits = body + verhoeff_check_digit(body)
        return _reshape_digits(original, digits) if _DIGIT.search(original) else digits

    def _pan_in(self, original: str, loc: str, anchor: str, rng: random.Random) -> str:
        letters = "".join(rng.choice(_PAN_LETTERS) for _ in range(3))
        initial = ascii_fold(self.persona(anchor, loc).last_name)[:1].upper() or "K"
        return f"{letters}P{initial}{rng.randint(1, 9999):04d}{rng.choice(_PAN_LETTERS)}"

    def _card_pan(self, original: str, loc: str, anchor: str, rng: random.Random) -> str:
        count = max(13, min(19, len(_DIGIT.findall(original)) or 16))
        body = TEST_CARD_IIN + _digits(rng, count - len(TEST_CARD_IIN) - 1)
        return _reshape_digits(original, body + luhn_check_digit(body))

    def _card_expiry(self, original: str, loc: str, anchor: str, rng: random.Random) -> str:
        month = rng.randint(1, 12)
        year = datetime.now(UTC).year + rng.randint(1, 4)
        sep = "-" if "-" in original else "/"
        if re.search(r"\d{4}\s*$", original):
            return f"{month:02d}{sep}{year}"
        return f"{month:02d}{sep}{year % 100:02d}"

    def _mrz(self, original: str, loc: str, anchor: str, rng: random.Random) -> str:
        """An ICAO MRZ rebuilt field by field for the record's persona, check digits valid."""
        parsed = split_mrz(original)
        if parsed is None:  # not an ICAO layout: every letter and digit but the issuer goes
            start = len(original) - len(original.lstrip())
            head = _MRZ_HEAD.fullmatch(original[start : start + 5])
            keep = range(start, start + 5) if head else None
            return self._reshape("MRZ", original, rng, True, keep=keep)
        flat, layout = parsed
        out = list(flat)
        holder = [part for part in flat[layout.name].split("<") if part]
        width = layout.name.stop - layout.name.start
        name = _redraw(flat[layout.name], rng)  # unreachable fallback: every persona collides
        persona = self.persona(anchor, loc)
        for attempt in range(_ATTEMPTS):
            candidate = self.persona(anchor if attempt == 0 else f"{anchor}|mrz{attempt}", loc)
            field = mrz_name(
                ascii_fold(candidate.last_name), ascii_fold(candidate.first_name), width
            )
            if not any(_holds(field, part) for part in holder):
                persona, name = candidate, field
                break
        out[layout.name] = name
        number = flat[layout.number]
        for _ in range(_ATTEMPTS):
            out[layout.number] = _redraw(number, rng)
            if "".join(out[layout.number]) != number:
                break
        for optional in layout.optional:
            out[optional] = _redraw(flat[optional], rng)
        birth = _yymmdd(flat[layout.birth], 1900)  # the century does not move the 5-year band
        if birth is None:
            out[layout.birth] = _redraw(flat[layout.birth], rng)
        else:
            out[layout.birth] = self._personas.birth_date(anchor, loc, birth).strftime("%y%m%d")
        expiry = _yymmdd(flat[layout.expiry], 2000)
        if expiry is None:
            out[layout.expiry] = _redraw(flat[layout.expiry], rng)
        else:
            shift = rng.choice((-1, 1)) * rng.randint(1, 730)
            out[layout.expiry] = _shift(expiry, shift).strftime("%y%m%d")
        if flat[layout.sex] in ("F", "M"):
            out[layout.sex] = persona.gender[0].upper()
        seal_mrz(out, flat, layout)
        return fill_layout(original, "".join(out))

    def _crypto(self, original: str, loc: str, anchor: str, rng: random.Random) -> str:
        """A fresh valid address of the original's kind; else every character is replaced."""
        fake = crypto_surrogate(original, rng.randbytes)
        return fake if fake is not None else self._reshape("CRYPTO_ADDRESS", original, rng, True)

    def _cvv(self, original: str, loc: str, anchor: str, rng: random.Random) -> str:
        if len(_DIGIT.findall(original)) in (3, 4):
            return self._reshape("CVV", original, rng, False)
        return _digits(rng, 3)

    def _iban(self, original: str, loc: str, anchor: str, rng: random.Random) -> str:
        compact = re.sub(r"\s", "", original).upper()
        if not re.fullmatch(r"[A-Z]{2}[0-9]{2}[A-Z0-9]{8,30}", compact):
            return self._reshape("IBAN", original, rng, True)
        country, shape = compact[:2], compact[4:]
        candidate = compact
        for _ in range(500):  # national check digits (BE, ES, NO, ME) hold ~1/97 of the time
            bban = "".join(
                rng.choice(_UPPER) if ch.isalpha() else str(rng.randint(0, 9)) for ch in shape
            )
            candidate = f"{country}{stdnum_iban.calc_check_digits(country + '00' + bban)}{bban}"
            if candidate != compact and stdnum_iban.is_valid(candidate):
                break
        it = iter(candidate)
        return "".join(ch if ch.isspace() else next(it, "") for ch in original)

    def _aba(self, original: str, loc: str, anchor: str, rng: random.Random) -> str:
        while True:
            candidate = _digits(rng, 9)
            n = [int(c) for c in candidate]
            if (
                3 * (n[0] + n[3] + n[6]) + 7 * (n[1] + n[4] + n[7]) + (n[2] + n[5] + n[8])
            ) % 10 == 0:
                return _fit(original, candidate)

    def _sin(self, original: str, loc: str, anchor: str, rng: random.Random) -> str:
        body = "0" + _digits(rng, 7)  # SINs starting with 0 are never assigned
        return _fit(original, body + luhn_check_digit(body))

    def _street(self, original: str, loc: str, anchor: str, rng: random.Random) -> str:
        p = self.persona(anchor, loc)
        if "," in original or len(original) > 40:
            return f"{p.street}, {p.city} {p.postal_code}"
        return p.street

    def _ip(self, original: str, loc: str, anchor: str, rng: random.Random) -> str:
        if ":" in original:  # IPv6 → the documentation prefix (RFC 3849)
            groups = ":".join(f"{rng.getrandbits(16):x}" for _ in range(4))
            return f"2001:db8:{groups}::{rng.getrandbits(16):x}"
        return f"10.{rng.randint(0, 255)}.{rng.randint(0, 255)}.{rng.randint(1, 254)}"

    def _mac(self, original: str, loc: str, anchor: str, rng: random.Random) -> str:
        sep = "-" if "-" in original else ":"
        octets = ["02", *(f"{rng.randint(0, 255):02x}" for _ in range(5))]  # locally administered
        mac = sep.join(octets)
        upper = any(c in "ABCDEF" for c in original) and not any(c in "abcdef" for c in original)
        return mac.upper() if upper else mac

    def _username(self, original: str, loc: str, anchor: str, rng: random.Random) -> str:
        handle = self.persona(anchor, loc).email_local.replace(".", "_") + _digits(rng, 2)
        return "@" + handle if original.startswith("@") else handle

    def _device_id(self, original: str, loc: str, anchor: str, rng: random.Random) -> str:
        if not any(c.isalnum() for c in original):
            return "".join(rng.choice(_HEX) for _ in range(16))
        return _reshape_hex(original, rng)

    def _gender(self, original: str, loc: str, anchor: str, rng: random.Random) -> str:
        gender = self.persona(anchor, loc).gender
        text = original.strip()
        value = gender[0] if len(text) == 1 else gender
        if text.isupper():
            return value.upper()
        if text.islower():
            return value.lower()
        return value.title()

    def _age(self, original: str, loc: str, anchor: str, rng: random.Random) -> str:
        match = _INT.search(original)
        if match is None:
            dob = self.persona(anchor, loc).dob
            return str((date.today() - dob).days // 365)
        age = int(match.group())
        band = age - age % 5
        new = band + rng.randint(0, 4)
        if new == age:
            new = band + (age - band + 1) % 5
        return original[: match.start()] + str(new) + original[match.end() :]

    @staticmethod
    def _phone(original: str, region: str, rng: random.Random) -> str:
        """A valid number in the original's country (else the persona's), in its format.

        NANP numbers use 555-01xx and UK mobiles the Ofcom drama range 07700 900xxx; other
        countries have no reserved fictional range, so a valid number is drawn near the
        country's example number.
        """
        country = phonenumbers.country_code_for_region(region)
        try:
            parsed = phonenumbers.parse(original, region)
            country = parsed.country_code or country
            region = phonenumbers.region_code_for_number(parsed) or region
        except phonenumbers.NumberParseException:
            pass
        international = original.strip().startswith(("+", "00"))
        fmt = (
            phonenumbers.PhoneNumberFormat.INTERNATIONAL
            if international
            else phonenumbers.PhoneNumberFormat.NATIONAL
        )
        if country == 44:  # GB and the Crown Dependencies share +44 and the Ofcom range
            tail = f"{rng.randint(0, 999):03d}"
            return f"+44 7700 900{tail}" if international else f"07700 900{tail}"
        if country == 1:  # 555-0100..0199 is fictional in every NANP area code
            for _ in range(64):
                area = f"{rng.randint(2, 9)}{rng.randint(0, 8)}{rng.randint(0, 9)}"
                number = phonenumbers.parse(f"+1{area}55501{rng.randint(0, 99):02d}", None)
                in_region = phonenumbers.region_code_for_number(number)
                if phonenumbers.is_valid_number(number) and (
                    in_region == region or region not in ("US", "CA")
                ):
                    return phonenumbers.format_number(number, fmt)
        example = phonenumbers.example_number_for_type(
            region, phonenumbers.PhoneNumberType.MOBILE
        ) or phonenumbers.example_number(region)
        if example is not None:
            base = str(example.national_number)
            for attempt in range(64):
                free = min(len(base) - 2, 6 if attempt < 48 else 4)  # keep the leading digits
                candidate = base[:-free] + _digits(rng, free)
                try:
                    number = phonenumbers.parse(f"+{example.country_code}{candidate}", None)
                except phonenumbers.NumberParseException:
                    continue
                if phonenumbers.is_valid_number(number):
                    return phonenumbers.format_number(number, fmt)
        return f"+1 201-555-01{rng.randint(0, 99):02d}"
