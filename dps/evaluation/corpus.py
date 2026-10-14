"""A labelled synthetic corpus per jurisdiction (spec §5.3) — no real data.

Values come from the M0 surrogate engine (personas, cards on a test IIN, RFC 2606 e-mails …) and,
for identifiers whose surrogates are deliberately *invalid* (never-issued SSNs, SINs and Aadhaar
numbers) or missing, from seeded Faker providers (US SSN/ITIN/EIN, EU IBANs) or check-digit
arithmetic (CURP, RFC, NINO, NHS, SIN, Aadhaar), so every checksum-validated value is rule-valid —
otherwise the evaluation would measure the validators, not the detectors. Each label records which
generator produced it. Records are text sentences or KYC-like JSON documents; every inserted value
is labelled, and template text carries no personal data.

``load_corpus`` also reads the bank's own labelled samples (real customer data), so a record that
does not parse is reported by file name, line number and pydantic error *type* only: the models
hide their input in errors, and no exception holding the line is chained.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import random
import re
from collections.abc import Iterable
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Literal

import phonenumbers
from faker import Faker
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from stdnum.mx import curp as mx_curp
from stdnum.mx import rfc as mx_rfc

from dps.engine.detect.checksums import luhn_check_digit, validate, verhoeff_check_digit
from dps.engine.synth.personas import ascii_fold, seed_int
from dps.engine.synth.surrogates import SurrogateEngine

CORPUS_VERSION = "dps.evalcorpus.v1"
JURISDICTIONS: dict[str, str] = {
    "US": "en_US",
    "CA": "en_CA",
    "MX": "es_MX",
    "IN": "en_IN",
    "UK": "en_GB",
    "EU": "en_GB",
}
EU_IBAN_LOCALES = ("de_DE", "fr_FR", "es_ES", "nl_NL", "it_IT")
_SLOT = re.compile(r"\{([A-Z][A-Z_]*)(?:#(\d))?\}")
_NINO_FIRST = "ABCEGHJKLMNOPRSTWXYZ"
_NINO_SECOND = "ABCEGHJKLMNPRSTWXYZ"
_NINO_BAD_PREFIX = {"BG", "GB", "NK", "KN", "TN", "NT", "ZZ"}
_UPPER = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_CONSONANTS = "BCDFGHJKLMNPQRSTVWXYZ"
_CURP_STATES = (
    "AS",
    "BC",
    "BS",
    "CC",
    "CL",
    "CM",
    "CS",
    "CH",
    "DF",
    "DG",
    "GT",
    "GR",
    "HG",
    "JC",
    "MC",
    "MN",
    "MS",
    "NT",
    "NL",
    "OC",
    "PL",
    "QT",
    "QR",
    "SP",
    "SL",
    "SR",
    "TC",
    "TS",
    "TL",
    "VZ",
    "YN",
    "ZS",
    "NE",
)

#: Sentence templates; ``{TYPE}`` slots are filled and labelled, ``{TYPE#2}`` is a second value.
TEXT_TEMPLATES: dict[str, tuple[str, ...]] = {
    "US": (
        "{PERSON_NAME} asked us to update SSN {SSN} and the card {CARD_PAN} on file.",
        "Please reach {PERSON_NAME} at {EMAIL} or {PHONE} before {DATE}.",
        "Wire to routing {ROUTING_ABA}; the payer's ITIN is {ITIN}.",
        "The employer EIN {EIN} was verified for {PERSON_NAME} (DOB: {DATE_OF_BIRTH}).",
        "Customer mailed from {EMAIL}, server {IP_ADDRESS} logged the request.",
        "Card {CARD_PAN} expires {CARD_EXPIRY}; contact {PHONE}.",
        "Debit from account no. {ACCOUNT_NUMBER} for customer ID {CUSTOMER_ID} was reversed.",
    ),
    "CA": (
        "{PERSON_NAME} provided SIN {SIN} and email {EMAIL}.",
        "Call {PERSON_NAME} on {PHONE}; card {CARD_PAN} was blocked on {DATE}.",
        "Social insurance number {SIN} belongs to the applicant born {DATE_OF_BIRTH} (DOB).",
        "Statement sent to {EMAIL}; login came from {IP_ADDRESS}.",
        "Acct# {ACCOUNT_NUMBER} belongs to client no. {CUSTOMER_ID}.",
    ),
    "MX": (
        "El cliente {PERSON_NAME} con CURP {CURP} actualizó su RFC {RFC}.",
        "Escribir a {EMAIL} o llamar al {PHONE}; tarjeta {CARD_PAN}.",
        "CURP {CURP} verificada el {DATE} para {PERSON_NAME}.",
        "RFC {RFC} registrado; correo {EMAIL}.",
        "Transferencia a la cuenta {ACCOUNT_NUMBER} del cliente {PERSON_NAME}.",
    ),
    "IN": (
        "{PERSON_NAME} submitted Aadhaar {AADHAAR} and PAN {PAN_IN} for KYC.",
        "Refund to IFSC {IFSC}; call {PHONE} or mail {EMAIL}.",
        "Aadhaar {AADHAAR} matched the DOB {DATE_OF_BIRTH} on record.",
        "PIN code {POSTAL_CODE} for card {CARD_PAN}; PAN {PAN_IN}.",
        "A/c {ACCOUNT_NUMBER} of Customer ID {CUSTOMER_ID} was debited via NEFT.",
    ),
    "UK": (
        "{PERSON_NAME} gave NI number {NINO} and NHS {NHS_NUMBER}.",
        "Pay IBAN {IBAN} by {DATE}; email {EMAIL}.",
        "National insurance {NINO} confirmed; phone {PHONE}.",
        "Postcode {POSTAL_CODE} on file; card {CARD_PAN}.",
        "Account number {ACCOUNT_NUMBER} closed; CIF {CUSTOMER_ID} archived.",
    ),
    "EU": (
        "Transfer to IBAN {IBAN} from IBAN {IBAN#2} cleared on {DATE}.",
        "Kontakt: {EMAIL}, Telefon {PHONE}.",
        "Paiement par carte {CARD_PAN} depuis {IP_ADDRESS}.",
        "{PERSON_NAME} updated IBAN {IBAN}; contact {EMAIL}.",
        "Statement for A/C No. {ACCOUNT_NUMBER}, customer ref {CUSTOMER_ID}.",
    ),
}
#: JSON documents: key → entity type (None = a non-sensitive control field).
JSON_FIELDS: dict[str, tuple[tuple[str, str | None], ...]] = {
    "US": (
        ("customer_id", "CUSTOMER_ID"),
        ("full_name", "PERSON_NAME"),
        ("dob", "DATE_OF_BIRTH"),
        ("ssn", "SSN"),
        ("email", "EMAIL"),
        ("phone", "PHONE"),
        ("card_number", "CARD_PAN"),
        ("status", None),
        ("risk_score", None),
    ),
    "CA": (
        ("customer_id", "CUSTOMER_ID"),
        ("full_name", "PERSON_NAME"),
        ("sin", "SIN"),
        ("email", "EMAIL"),
        ("phone", "PHONE"),
        ("status", None),
    ),
    "MX": (
        ("customer_id", "CUSTOMER_ID"),
        ("nombre", "PERSON_NAME"),
        ("curp", "CURP"),
        ("rfc", "RFC"),
        ("correo", "EMAIL"),
        ("status", None),
    ),
    "IN": (
        ("customer_id", "CUSTOMER_ID"),
        ("full_name", "PERSON_NAME"),
        ("aadhaar", "AADHAAR"),
        ("pan", "PAN_IN"),
        ("email", "EMAIL"),
        ("phone", "PHONE"),
        ("status", None),
    ),
    "UK": (
        ("customer_id", "CUSTOMER_ID"),
        ("full_name", "PERSON_NAME"),
        ("nino", "NINO"),
        ("nhs_number", "NHS_NUMBER"),
        ("iban", "IBAN"),
        ("email", "EMAIL"),
        ("status", None),
    ),
    "EU": (
        ("customer_id", "CUSTOMER_ID"),
        ("full_name", "PERSON_NAME"),
        ("iban", "IBAN"),
        ("email", "EMAIL"),
        ("status", None),
    ),
}
NOTE_TEMPLATES = ("Follow-up with {EMAIL} requested.", "Card {CARD_PAN} replaced.", "No remarks.")


class CorpusError(ValueError):
    """A corpus line that cannot be read; the message never carries the line's content."""


class Label(BaseModel):
    model_config = ConfigDict(hide_input_in_errors=True)

    path: str = "$"
    start: int | None = None
    end: int | None = None
    entity_type: str
    generator: str = "surrogate"


class CorpusRecord(BaseModel):
    model_config = ConfigDict(hide_input_in_errors=True)

    id: str
    jurisdiction: str
    locale: str
    kind: Literal["text", "json"]
    text: str | None = None
    data: dict[str, Any] | None = None
    labels: list[Label] = Field(default_factory=list)


def _nhs(rng: random.Random) -> str:
    while True:
        digits = [rng.randint(0, 9) for _ in range(9)]
        check = 11 - sum(d * w for d, w in zip(digits, range(10, 1, -1), strict=True)) % 11
        if check == 10:
            continue
        number = "".join(map(str, digits)) + str(0 if check == 11 else check)
        return f"{number[:3]} {number[3:6]} {number[6:]}"


def _born(rng: random.Random) -> date:
    return date(1950, 1, 1) + timedelta(days=rng.randint(0, 365 * 50))


def _curp(rng: random.Random) -> str:
    """A CURP with a valid date, state and check digit (Faker's es_MX provider is not seedable)."""
    born = _born(rng)
    body = (
        rng.choice(_UPPER)
        + rng.choice("AEIOUX")
        + rng.choice(_UPPER)
        + rng.choice(_UPPER)
        + born.strftime("%y%m%d")
        + rng.choice("HM")
        + rng.choice(_CURP_STATES)
        + "".join(rng.choice(_CONSONANTS) for _ in range(3))
        + (rng.choice("0123456789") if born.year < 2000 else rng.choice(_UPPER))
    )
    return body + mx_curp.calc_check_digit(body)


def _rfc(rng: random.Random) -> str:
    """A personal (13-character) RFC with a valid date and check digit."""
    body = (
        "".join(rng.choice(_UPPER) for _ in range(4))
        + _born(rng).strftime("%y%m%d")
        + "".join(rng.choice(_UPPER + "0123456789") for _ in range(2))
    )
    return body + mx_rfc.calc_check_digit(body)


def _sin(rng: random.Random) -> str:
    """A Luhn-valid SIN on an issued first digit (the surrogate engine's start with 0)."""
    body = rng.choice("1234567") + "".join(str(rng.randint(0, 9)) for _ in range(7))
    number = body + luhn_check_digit(body)
    return f"{number[:3]} {number[3:6]} {number[6:]}"


def _aadhaar(rng: random.Random) -> str:
    """A Verhoeff-valid Aadhaar starting 2-9 (the surrogate engine's start with 1: never issued)."""
    body = str(rng.randint(2, 9)) + "".join(str(rng.randint(0, 9)) for _ in range(10))
    number = body + verhoeff_check_digit(body)
    return f"{number[:4]} {number[4:8]} {number[8:]}"


def _nino(rng: random.Random) -> str:
    while True:
        prefix = rng.choice(_NINO_FIRST) + rng.choice(_NINO_SECOND)
        if prefix not in _NINO_BAD_PREFIX:
            body = "".join(str(rng.randint(0, 9)) for _ in range(6))
            return f"{prefix} {body[:2]} {body[2:4]} {body[4:]} {rng.choice('ABCD')}"


_PAN_LETTERS = "ABCDEFGHJKLMNPQRSTUVWXYZ"


def rule_valid_pan(engine_seed: bytes, surname: str, template: str = "aaaaa0000a") -> str:
    """A rule-valid individual's PAN (holder type ``P``, the surname's initial), drawn exactly as
    the M0 surrogate engine drew one for the normalised ``template`` before its PAN surrogates
    moved to the never-issued sequence number ``0000`` (M5 verify r2, ``unissued.py``).
    Synthetic material that must *validate* (this corpus, the demo documents) keeps its
    committed values."""
    rng = random.Random(seed_int(engine_seed, f"PAN_IN|{template}"))
    letters = "".join(rng.choice(_PAN_LETTERS) for _ in range(3))
    initial = ascii_fold(surname)[:1].upper() or "K"
    return f"{letters}P{initial}{rng.randint(1, 9999):04d}{rng.choice(_PAN_LETTERS)}"


PERSONA_ENTITIES = frozenset(
    {"PERSON_NAME", "DATE_OF_BIRTH", "EMAIL", "PHONE", "STREET_ADDRESS", "CITY", "POSTAL_CODE"}
)
SURROGATE_TEMPLATES = {
    "CARD_PAN": "0000 0000 0000 0000",
    "AADHAAR": "0000 0000 0000",
    "SIN": "000 000 000",
    "ROUTING_ABA": "000000000",
    "PAN_IN": "AAAAA0000A",
    "IP_ADDRESS": "0.0.0.0",
    "IBAN": "GB00XXXX00000000000000",
}


def _region(locale: str) -> str:
    return locale.split("_")[1]


class ValueFactory:
    """Seeded labelled values for one jurisdiction.

    Persona fields of one record share a surrogate engine (name, e-mail and DOB agree); every
    other value gets its own engine, because a surrogate is a function of (seed, type, template)
    and one engine would repeat the same card number in every record.
    """

    def __init__(self, seed: bytes, jurisdiction: str) -> None:
        self.seed = seed
        self.jurisdiction = jurisdiction
        self.locale = JURISDICTIONS[jurisdiction]
        self._fakers: dict[str, Faker] = {}

    def _engine_seed(self, label: str) -> bytes:
        return hmac.new(self.seed, label.encode(), hashlib.sha256).digest()

    def _engine(self, label: str) -> SurrogateEngine:
        return SurrogateEngine(self._engine_seed(label), default_locale=self.locale)

    def _faker(self, locale: str, rng: random.Random) -> Faker:
        fake = self._fakers.setdefault(locale, Faker(locale))
        fake.seed_instance(rng.getrandbits(64))
        return fake

    def _raw(
        self, entity: str, record_no: int, slot_no: int, attempt: int, rng: random.Random
    ) -> tuple[str, str]:
        loc = self.locale
        if entity in PERSONA_ENTITIES:
            engine = self._engine(f"persona|{record_no}|{attempt}")
            persona = engine.persona("record", loc)
            if entity == "PERSON_NAME":
                return persona.full_name, "surrogate"
            if entity == "DATE_OF_BIRTH":
                return persona.dob.isoformat(), "surrogate"
            if entity == "PHONE":
                return self._phone(engine, rng)
            return engine.surrogate(entity, "x", loc, "record"), "surrogate"
        if entity == "DATE":
            return (date(2020, 1, 1) + timedelta(days=rng.randint(0, 2400))).isoformat(), "rule"
        if entity == "CARD_EXPIRY":  # the surrogate's year follows the clock: not reproducible
            return f"{rng.randint(1, 12):02d}/{rng.randint(27, 34)}", "rule"
        if entity in ("SSN", "ITIN", "EIN"):
            fake = self._faker("en_US", rng)
            return str({"SSN": fake.ssn, "ITIN": fake.itin, "EIN": fake.ein}[entity]()), "faker"
        if entity == "CURP":
            return _curp(rng), "rule"
        if entity == "RFC":
            return _rfc(rng), "rule"
        if entity == "NINO":
            return _nino(rng), "rule"
        if entity == "SIN":
            return _sin(rng), "rule"
        if entity == "AADHAAR":
            return _aadhaar(rng), "rule"
        if entity == "NHS_NUMBER":
            return _nhs(rng), "rule"
        if entity == "IBAN" and self.jurisdiction == "EU":
            return str(self._faker(rng.choice(EU_IBAN_LOCALES), rng).iban()), "faker"
        if entity == "IFSC":
            letters = "".join(rng.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZ") for _ in range(4))
            return f"{letters}0{rng.randint(0, 999999):06d}", "rule"
        if entity == "CUSTOMER_ID":
            return f"CUST-{rng.randint(100000, 999999)}", "rule"
        if entity == "ACCOUNT_NUMBER":  # 9-16 digits, run together or printed in fours
            digits = "".join(str(rng.randint(0, 9)) for _ in range(rng.randint(9, 16)))
            if rng.random() < 0.5:
                digits = " ".join(digits[i : i + 4] for i in range(0, len(digits), 4))
            return digits, "rule"
        label = f"value|{entity}|{record_no}|{slot_no}|{attempt}"
        engine = self._engine(label)
        if entity == "PAN_IN":  # surrogate PANs carry the never-issued 0000: not rule-valid
            surname = engine.persona("record", loc).last_name
            return rule_valid_pan(self._engine_seed(label), surname), "surrogate"
        return engine.surrogate(
            entity, SURROGATE_TEMPLATES.get(entity, "x"), loc, "record"
        ), "surrogate"

    def _phone(self, engine: SurrogateEngine, rng: random.Random) -> tuple[str, str]:
        """A number valid in the jurisdiction's region, national or international format.

        The surrogate engine keeps the template's country, so the template is the region's own
        example number. UK surrogates sit in Ofcom's drama *mobile* range (07700 900xxx), which
        libphonenumber does not accept as a valid number; the detector rightly skips it, so UK
        numbers come from Ofcom's drama *landline* range (020 7946 0xxx, valid and fictional).
        """
        region = _region(self.locale)
        international = rng.random() < 0.5
        if region == "GB":
            tail = f"{rng.randint(0, 999):03d}"
            return (f"+44 20 7946 0{tail}" if international else f"020 7946 0{tail}"), "rule"
        example = phonenumbers.example_number_for_type(region, phonenumbers.PhoneNumberType.MOBILE)
        if example is None:
            raise RuntimeError(f"no example mobile number for region {region}")
        fmt = (
            phonenumbers.PhoneNumberFormat.INTERNATIONAL
            if international
            else phonenumbers.PhoneNumberFormat.NATIONAL
        )
        template = phonenumbers.format_number(example, fmt)
        return engine.surrogate("PHONE", template, self.locale, "record"), "surrogate"

    def _valid(self, entity: str, value: str) -> bool:
        if entity == "PHONE":
            try:
                return phonenumbers.is_valid_number(phonenumbers.parse(value, _region(self.locale)))
            except phonenumbers.NumberParseException:
                return False
        return validate(entity, value) is not False

    def value(self, entity: str, record_no: int, slot_no: int) -> tuple[str, str]:
        """A value of ``entity`` for this record; checksum types are always rule-valid and phone
        numbers valid in the jurisdiction's region."""
        for attempt in range(25):
            rng = random.Random(seed_int(self.seed, f"{entity}|{record_no}|{slot_no}|{attempt}"))
            value, generator = self._raw(entity, record_no, slot_no, attempt, rng)
            if self._valid(entity, value):
                return value, generator
        raise RuntimeError(f"no rule-valid {entity} after 25 attempts")


def _fill(template: str, factory: ValueFactory, record_no: int) -> tuple[str, list[Label]]:
    pieces: list[str] = []
    labels: list[Label] = []
    cursor = pos = 0
    for slot_no, match in enumerate(_SLOT.finditer(template)):
        literal = template[cursor : match.start()]
        pieces.append(literal)
        pos += len(literal)
        value, generator = factory.value(match.group(1), record_no, slot_no)
        labels.append(
            Label(start=pos, end=pos + len(value), entity_type=match.group(1), generator=generator)
        )
        pieces.append(value)
        pos += len(value)
        cursor = match.end()
    pieces.append(template[cursor:])
    return "".join(pieces), labels


def generate_corpus(seed: int, records: int) -> dict[str, list[CorpusRecord]]:
    """``records`` per jurisdiction, alternating text and JSON; deterministic in ``seed``."""
    root = hashlib.sha256(f"dps.eval|{seed}".encode()).digest()
    out: dict[str, list[CorpusRecord]] = {}
    for jurisdiction, locale in JURISDICTIONS.items():
        factory = ValueFactory(hashlib.sha256(root + jurisdiction.encode()).digest(), jurisdiction)
        items: list[CorpusRecord] = []
        for n in range(records):
            rid = f"{jurisdiction.lower()}-{n:04d}"
            templates = TEXT_TEMPLATES[jurisdiction]
            if n % 2 == 0:
                text, labels = _fill(templates[(n // 2) % len(templates)], factory, n)
                items.append(
                    CorpusRecord(
                        id=rid,
                        jurisdiction=jurisdiction,
                        locale=locale,
                        kind="text",
                        text=text,
                        labels=labels,
                    )
                )
                continue
            data: dict[str, Any] = {}
            labels = []
            for slot_no, (key, entity) in enumerate(JSON_FIELDS[jurisdiction]):
                if entity is None:
                    data[key] = "ACTIVE" if key == "status" else 700 + n % 200
                    continue
                value, generator = factory.value(entity, n, slot_no)
                data[key] = value
                labels.append(Label(path=f"$.{key}", entity_type=entity, generator=generator))
            note, note_labels = _fill(NOTE_TEMPLATES[n % len(NOTE_TEMPLATES)], factory, n + 100_000)
            data["notes"] = note
            labels += [label.model_copy(update={"path": "$.notes"}) for label in note_labels]
            items.append(
                CorpusRecord(
                    id=rid,
                    jurisdiction=jurisdiction,
                    locale=locale,
                    kind="json",
                    data=data,
                    labels=labels,
                )
            )
        out[jurisdiction] = items
    return out


def write_corpus(corpus: dict[str, list[CorpusRecord]], directory: Path) -> list[Path]:
    """One ``<jurisdiction>.jsonl`` per jurisdiction (sorted keys, one record per line)."""
    directory.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for jurisdiction, records in corpus.items():
        path = directory / f"{jurisdiction.lower()}.jsonl"
        lines = [
            json.dumps(
                r.model_dump(mode="json", exclude_none=True), sort_keys=True, ensure_ascii=False
            )
            for r in records
        ]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        paths.append(path)
    return paths


def corpus_files(paths: Iterable[Path]) -> list[Path]:
    """Expand directories to their ``*.jsonl`` files (sorted)."""
    out: list[Path] = []
    for path in paths:
        out += sorted(path.glob("*.jsonl")) if path.is_dir() else [path]
    return out


def _parse_lines(name: str, raw: bytes) -> list[CorpusRecord]:
    """One record per non-blank line; a bad line raises a value-free :class:`CorpusError`.

    Lines are split on ``\\n`` / ``\\r`` only (``bytes.splitlines``): U+2028, U+2029 and U+0085 may
    sit unescaped inside a JSON string and must not split a record. The error is raised outside the
    ``except`` block, so neither the pydantic error nor the ``UnicodeDecodeError`` (whose ``object``
    is the whole line) is attached as ``__context__``.
    """
    records: list[CorpusRecord] = []
    for line_no, line in enumerate(raw.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            records.append(CorpusRecord.model_validate_json(line.decode("utf-8")))
            continue
        except UnicodeDecodeError:
            problem = "not valid UTF-8"
        except ValidationError as err:
            types = sorted({e["type"] for e in err.errors(include_url=False, include_input=False)})
            problem = "not a valid corpus record: " + ", ".join(types)
        raise CorpusError(f"{name} line {line_no}: {problem} (content not shown)")
    return records


def load_corpus(paths: Iterable[Path]) -> tuple[list[CorpusRecord], str]:
    """Records from files or directories, and the sha256 over the file contents (sorted by name).

    Raises :class:`CorpusError` naming the file and line of the first record that does not parse.
    """
    files = corpus_files(paths)
    digest = hashlib.sha256()
    records: list[CorpusRecord] = []
    for path in sorted(files, key=lambda p: p.name):
        raw = path.read_bytes()
        digest.update(path.name.encode() + b"\0" + raw)
        records += _parse_lines(path.name, raw)
    return records, digest.hexdigest()
