"""Persona values, field formatting, tokens of persona values — and filling a page (Task 13).

A persona is a fixed set of attributes. Every attribute that names an entity type is produced by
the **M0 surrogate engine through ``apply_plan``** (method ``surrogate``, anchor ``persona:<n>``),
fed a seeded *exemplar*: a synthetic stand-in drawn from
``random.Random(seed_int(seed, "scanner|exemplar|<n>|<attr>"))``. The exemplar only gives the
generator a shape and per-persona variety (M0's identifier generators are deterministic in
``(seed, entity type, original)``). **No page value is ever an input.** All attributes are
computed every time, in sorted order, so persona ``n`` is identical in the setup preview, on every
page, in the export and in a later scan given the same seed. Derived attributes come from those
values or from the seeded RNG; ``credential`` and ``otp`` are typed into password/OTP inputs and
are never persisted or exported.

What is stored from a fill (``FieldRecord.fill_*``) is synthetic: persona values, tokens of
persona values, ``option:<n>`` for an option picked without a persona match (the option's own text
is page data) and ``None`` for credentials.

Playwright is imported only inside :func:`fill_page` (the API imports this module).
"""

from __future__ import annotations

import random
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal

import phonenumbers

from dps.engine.findings import Finding, Locator, Plan, PlanEntry, hash_value
from dps.engine.planner import build_plan
from dps.engine.policy import Policy
from dps.engine.service import PrivacyEngine
from dps.engine.synth.personas import normalise_locale, seed_int
from dps.engine.synth.surrogates import SurrogateEngine, SynthesisSeed, seed_bytes
from dps.engine.taxonomy import Sensitivity, entity_info
from dps.engine.transform.apply import ApplyOptions, RevealHint, apply_plan
from dps.engine.transform.crypto import KeyMaterial, b32, hmac_bytes
from dps.engine.verify import collect_originals, normalise, verify_output
from dps.layers.scanner.classify import (
    autocomplete_token,
    compile_html_pattern,
    pattern_accepts,
)
from dps.layers.scanner.models import FieldClassification, FieldFacts, OptionFacts


class LeakageError(RuntimeError):
    """A token still holds the persona value it replaces. ``str()`` is ``"leakage"``."""


# --- persona attributes -----------------------------------------------------------------------
def _d(rng: random.Random, n: int) -> str:
    return "".join(str(rng.randint(0, 9)) for _ in range(n))


def _letters(rng: random.Random, n: int) -> str:
    return "".join(rng.choice("ABCDEFGHJKLMNPQRSTUVWXYZ") for _ in range(n))


def _alnum(rng: random.Random, n: int) -> str:
    return "".join(rng.choice("ABCDEFGHJKLMNPQRSTUVWXYZ0123456789") for _ in range(n))


def rng_choice(rng: random.Random, chars: str) -> str:
    """One character of ``chars`` (exemplar helper)."""
    return rng.choice(chars)


#: Exemplar phone numbers per locale: ``+<country> <national digits>`` (the surrogate engine keeps
#: the country of what it replaces, so a random country code would give a foreign number).
_PHONE_EXEMPLAR: dict[str, Callable[[random.Random], str]] = {
    "en_IN": lambda r: "+91 9" + _d(r, 9),
    "en_US": lambda r: "+1 415" + _d(r, 7),
    "en_GB": lambda r: "+44 7700 900" + _d(r, 3),
    "en_CA": lambda r: "+1 416" + _d(r, 7),
    "es_MX": lambda r: "+52 55" + _d(r, 8),
}


@dataclass(frozen=True)
class AttrSpec:
    """A persona attribute: its entity type and its seeded exemplar ``(rng, locale) -> str``."""

    entity: str
    exemplar: Callable[[random.Random, str], str]


PERSONA_ATTRS: dict[str, AttrSpec] = {
    "name": AttrSpec("PERSON_NAME", lambda r, loc: "Asha Rao"),
    "dob": AttrSpec(
        "DATE_OF_BIRTH",
        lambda r, loc: date.fromordinal(
            date(1958, 1, 1).toordinal() + r.randint(0, 365 * 44)
        ).isoformat(),
    ),
    "email": AttrSpec("EMAIL", lambda r, loc: f"user{_d(r, 6)}@example.org"),
    "phone": AttrSpec(
        "PHONE", lambda r, loc: _PHONE_EXEMPLAR.get(loc, _PHONE_EXEMPLAR["en_US"])(r)
    ),
    "address": AttrSpec(
        "STREET_ADDRESS",
        lambda r, loc: f"{r.randint(1, 99)} Exemplar Road, Exampleton {_d(r, 6)}",
    ),
    "address_line1": AttrSpec("STREET_ADDRESS", lambda r, loc: f"{r.randint(1, 99)} Exemplar Road"),
    "city": AttrSpec("CITY", lambda r, loc: "Exampleton"),
    "postal_code": AttrSpec("POSTAL_CODE", lambda r, loc: _d(r, 6)),
    "gender": AttrSpec("GENDER", lambda r, loc: "Female"),
    "nationality": AttrSpec("NATIONALITY", lambda r, loc: "Exemplarian"),
    "username": AttrSpec("USERNAME", lambda r, loc: f"user_{_d(r, 4)}"),
    "organization": AttrSpec("ORG_NAME", lambda r, loc: f"Exemplar Works {_d(r, 3)}"),
    "aadhaar": AttrSpec("AADHAAR", lambda r, loc: str(r.randint(2, 9)) + _d(r, 11)),
    "pan": AttrSpec("PAN_IN", lambda r, loc: _letters(r, 5) + _d(r, 4) + _letters(r, 1)),
    "ssn": AttrSpec("SSN", lambda r, loc: f"9{_d(r, 2)}-{_d(r, 2)}-{_d(r, 4)}"),
    "itin": AttrSpec("ITIN", lambda r, loc: f"9{_d(r, 2)}-{_d(r, 2)}-{_d(r, 4)}"),
    "card_number": AttrSpec("CARD_PAN", lambda r, loc: "4" + _d(r, 15)),
    "card_expiry": AttrSpec(
        "CARD_EXPIRY", lambda r, loc: f"{r.randint(1, 12):02d}/{r.randint(27, 31):02d}"
    ),
    "card_csc": AttrSpec("CVV", lambda r, loc: _d(r, 3)),
    "account_number": AttrSpec("ACCOUNT_NUMBER", lambda r, loc: str(r.randint(1, 9)) + _d(r, 11)),
    "ifsc": AttrSpec("IFSC", lambda r, loc: _letters(r, 4) + "0" + _letters(r, 2) + _d(r, 4)),
    "routing": AttrSpec("ROUTING_ABA", lambda r, loc: _d(r, 9)),
    "iban": AttrSpec("IBAN", lambda r, loc: f"GB{_d(r, 2)}EXMP{_d(r, 14)}"),
    "passport": AttrSpec("PASSPORT", lambda r, loc: _letters(r, 1) + _d(r, 7)),
    "driver_license": AttrSpec("DRIVER_LICENSE", lambda r, loc: _letters(r, 1) + _d(r, 7)),
    "voter_id": AttrSpec("VOTER_ID", lambda r, loc: _letters(r, 3) + _d(r, 7)),
    "nino": AttrSpec("NINO", lambda r, loc: _letters(r, 2) + _d(r, 6) + rng_choice(r, "ABCD")),
    "sin": AttrSpec("SIN", lambda r, loc: f"{_d(r, 3)} {_d(r, 3)} {_d(r, 3)}"),
    "curp": AttrSpec(
        "CURP",
        lambda r, loc: (
            _letters(r, 4) + _d(r, 6) + rng_choice(r, "HM") + _letters(r, 5) + _alnum(r, 2)
        ),
    ),
    "rfc": AttrSpec("RFC", lambda r, loc: _letters(r, 4) + _d(r, 6) + _alnum(r, 3)),
    "employee_id": AttrSpec("EMPLOYEE_ID", lambda r, loc: f"EMP-{_d(r, 6)}"),
    "customer_id": AttrSpec("CUSTOMER_ID", lambda r, loc: f"CUST-{_d(r, 6)}"),
    "url": AttrSpec("URL", lambda r, loc: f"https://example.org/{_d(r, 6)}"),
    "amount": AttrSpec("AMOUNT", lambda r, loc: str(r.randint(1, 9)) + _d(r, 4)),
}


#: Every persona attribute -> its entity type (derived attributes included).
ATTR_ENTITY: dict[str, str] = {a: spec.entity for a, spec in PERSONA_ATTRS.items()} | {
    "given_name": "PERSON_NAME",
    "family_name": "PERSON_NAME",
    "address_line2": "STREET_ADDRESS",
    "region": "LOCATION",
    "country": "LOCATION",
    "age": "AGE",
    "credential": "PASSWORD",
    "otp": "PASSWORD",
}

#: Entity type -> persona attribute (a field classified so takes this attribute).
ENTITY_ATTR: dict[str, str] = {
    "PERSON_NAME": "name",
    "DATE_OF_BIRTH": "dob",
    "EMAIL": "email",
    "PHONE": "phone",
    "STREET_ADDRESS": "address",
    "CITY": "city",
    "LOCATION": "region",
    "POSTAL_CODE": "postal_code",
    "GENDER": "gender",
    "NATIONALITY": "nationality",
    "USERNAME": "username",
    "ORG_NAME": "organization",
    "AADHAAR": "aadhaar",
    "PAN_IN": "pan",
    "SSN": "ssn",
    "ITIN": "itin",
    "CARD_PAN": "card_number",
    "CARD_EXPIRY": "card_expiry",
    "CVV": "card_csc",
    "ACCOUNT_NUMBER": "account_number",
    "IFSC": "ifsc",
    "ROUTING_ABA": "routing",
    "IBAN": "iban",
    "PASSPORT": "passport",
    "DRIVER_LICENSE": "driver_license",
    "VOTER_ID": "voter_id",
    "NINO": "nino",
    "SIN": "sin",
    "CURP": "curp",
    "RFC": "rfc",
    "EMPLOYEE_ID": "employee_id",
    "CUSTOMER_ID": "customer_id",
    "URL": "url",
    "AMOUNT": "amount",
    "AGE": "age",
    "PASSWORD": "credential",
}

#: Autocomplete token -> persona attribute (more precise than the entity: given-name, line 2…).
TOKEN_ATTR: dict[str, str] = {
    "name": "name",
    "given-name": "given_name",
    "additional-name": "given_name",
    "family-name": "family_name",
    "nickname": "given_name",
    "username": "username",
    "new-password": "credential",
    "current-password": "credential",
    "one-time-code": "otp",
    "organization": "organization",
    "street-address": "address",
    "address-line1": "address_line1",
    "address-line2": "address_line2",
    "address-line3": "address_line2",
    "address-level2": "city",
    "address-level1": "region",
    "country": "country",
    "country-name": "country",
    "postal-code": "postal_code",
    "cc-name": "name",
    "cc-given-name": "given_name",
    "cc-family-name": "family_name",
    "cc-number": "card_number",
    "cc-exp": "card_expiry",
    "cc-csc": "card_csc",
    "transaction-amount": "amount",
    "bday": "dob",
    "sex": "gender",
    "url": "url",
    "tel": "phone",
    "tel-national": "phone",
    "email": "email",
    "impp": "username",
}

LOCALE_COUNTRY: dict[str, str] = {
    "en_US": "United States",
    "en_IN": "India",
    "en_GB": "United Kingdom",
    "en_CA": "Canada",
    "es_MX": "Mexico",
}
LOCALE_REGIONS: dict[str, tuple[str, ...]] = {
    "en_US": ("California", "Texas", "New York", "Illinois", "Washington"),
    "en_IN": ("Karnataka", "Maharashtra", "Tamil Nadu", "Delhi", "Telangana"),
    "en_GB": ("England", "Scotland", "Wales", "Northern Ireland"),
    "en_CA": ("Ontario", "Quebec", "British Columbia", "Alberta"),
    "es_MX": ("Jalisco", "Nuevo León", "Puebla", "Yucatán", "Ciudad de México"),
}
_PREVIEW: dict[str, list[tuple[str, str]]] = {
    "en_IN": [
        ("Name", "name"),
        ("Date of birth", "dob"),
        ("Aadhaar", "aadhaar"),
        ("PAN", "pan"),
        ("Mobile", "phone"),
    ],
    "en_US": [
        ("Name", "name"),
        ("Date of birth", "dob"),
        ("SSN", "ssn"),
        ("Phone", "phone"),
        ("Email", "email"),
    ],
    "en_GB": [
        ("Name", "name"),
        ("Date of birth", "dob"),
        ("National Insurance number", "nino"),
        ("Phone", "phone"),
        ("Email", "email"),
    ],
    "en_CA": [
        ("Name", "name"),
        ("Date of birth", "dob"),
        ("SIN", "sin"),
        ("Phone", "phone"),
        ("Email", "email"),
    ],
    "es_MX": [
        ("Name", "name"),
        ("Date of birth", "dob"),
        ("CURP", "curp"),
        ("RFC", "rfc"),
        ("Email", "email"),
    ],
}


def _rng(seed: bytes, *parts: object) -> random.Random:
    return random.Random(seed_int(seed, "scanner|" + "|".join(str(p) for p in parts)))


def persona_values(
    engine: PrivacyEngine, *, seed: SynthesisSeed, persona_no: int, locale: str, policy: Policy
) -> dict[str, str]:
    """Persona ``persona_no`` under ``seed``: every attribute, deterministic (see module)."""
    raw = seed_bytes(seed)
    loc = normalise_locale(locale)
    attrs = sorted(PERSONA_ATTRS)
    doc = {a: PERSONA_ATTRS[a].exemplar(_rng(raw, "exemplar", persona_no, a), loc) for a in attrs}
    entries = [
        PlanEntry(
            finding=Finding(
                locator=Locator(path=f"$.{a}"),
                entity_type=PERSONA_ATTRS[a].entity,
                score=1.0,
                detectors=["scanner:persona"],
                sensitivity=entity_info(PERSONA_ATTRS[a].entity).sensitivity,
                value_hash=hash_value(doc[a]),
            ),
            method="surrogate",
            params={},
            reason="scanner:persona",
            reversible=False,
        )
        for a in attrs
    ]
    plan = Plan(mode="synthesize", record_sensitivity=Sensitivity.CRITICAL, entries=entries)
    options = ApplyOptions(
        policy=policy,
        key=None,
        surrogates=SurrogateEngine(raw, default_locale=loc),
        fpe_enabled=False,
        cipher_factory=None,
        token_style="natural",
        locale=loc,
        anchor=f"persona:{persona_no}",
    )
    values: dict[str, str] = {
        k: str(v) for k, v in apply_plan(plan=plan, options=options, data=doc).output.items()
    }
    rng = _rng(raw, "derived", persona_no)
    parts = values["name"].split()
    values["given_name"], values["family_name"] = parts[0], parts[-1]
    values["address_line2"] = f"Floor {rng.randint(1, 20)}"
    values["region"] = rng.choice(LOCALE_REGIONS.get(loc, LOCALE_REGIONS["en_US"]))
    values["country"] = LOCALE_COUNTRY.get(loc, "United States")
    born = date.fromisoformat(values["dob"])
    values["age"] = str((date.today() - born).days // 365)
    values["credential"] = "Dps!" + b32(hmac_bytes(raw, f"scanner|credential|{persona_no}"))[:12]
    values["otp"] = f"{rng.randint(0, 999_999):06d}"
    return values


def preview_entities(locale: str) -> list[tuple[str, str]]:
    """``(label, attr)`` pairs the New-scan page previews for ``locale``."""
    return list(_PREVIEW.get(normalise_locale(locale), _PREVIEW["en_US"]))


# --- planning a field ---------------------------------------------------------------------------
FillKind = Literal["text", "date", "select", "check", "radio", "credential", "neutral_text", "skip"]
_OBFUSCATED_TIERS = frozenset({"MEDIUM", "HIGH", "CRITICAL"})


@dataclass(frozen=True)
class FieldPlan:
    """How a field is filled: the kind of action, the persona attribute, the stored status and
    whether obfuscate mode replaces its value by a token."""

    kind: FillKind
    persona_attr: str | None
    fill_status: str
    obfuscate: bool = False


def plan_field(facts: FieldFacts, cls: FieldClassification) -> FieldPlan:
    """The fill plan of one field. There is no branch for a button, an image or reset input
    (never extracted) or a link: only the field elements themselves are ever touched."""
    itype = facts.input_type
    if itype == "file":
        return FieldPlan("skip", None, "skipped_file")
    if itype == "hidden" or not facts.visible:
        return FieldPlan("skip", None, "skipped_hidden")
    if facts.readonly or facts.disabled:
        return FieldPlan("skip", None, "skipped_readonly")
    token, _ = autocomplete_token(facts.autocomplete)
    attr = TOKEN_ATTR.get(token) if token else None
    if attr is None and cls.entity_type is not None:
        attr = ENTITY_ATTR.get(cls.entity_type)
    if itype == "password" or attr in ("credential", "otp"):
        return FieldPlan(
            "credential", attr if attr in ("credential", "otp") else "credential", "filled"
        )
    if facts.tag == "select":
        return FieldPlan("select", attr, "filled" if attr else "neutral")
    if itype == "checkbox":
        return FieldPlan("check", None, "neutral" if facts.required else "not_filled")
    if itype == "radio":
        return FieldPlan("radio", attr, "filled" if attr else "neutral")
    if attr is None:
        return FieldPlan("neutral_text", None, "neutral")
    tier = cls.sensitivity or str(entity_info(ATTR_ENTITY[attr]).sensitivity)
    kind: FillKind = "date" if itype in ("date", "month") else "text"
    return FieldPlan(kind, attr, "filled", obfuscate=tier in _OBFUSCATED_TIERS)


# --- formatting ---------------------------------------------------------------------------------
SHAPES: dict[str, tuple[str, ...]] = {
    "aadhaar": ("#### #### ####", "############", "####-####-####"),
    "card_number": ("#### #### #### ####", "################", "####-####-####-####"),
    "ssn": ("###-##-####", "#########"),
    "itin": ("###-##-####", "#########"),
    "sin": ("### ### ###", "#########"),
    "postal_code": ("#####", "######"),
}
DATE_PLACEHOLDERS: dict[str, str] = {
    "dd/mm/yyyy": "%d/%m/%Y",
    "mm/dd/yyyy": "%m/%d/%Y",
    "yyyy-mm-dd": "%Y-%m-%d",
    "dd-mm-yyyy": "%d-%m-%Y",
    "dd.mm.yyyy": "%d.%m.%Y",
}


def apply_format(fmt: str, canonical: str) -> str | None:
    """``canonical`` rendered in ``fmt`` (``None``: it cannot be). ``ValueError`` for an unknown
    format name (the message names no value)."""
    if fmt == "as_is":
        return canonical
    if fmt == "digits":
        return re.sub(r"\D", "", canonical) or None
    if fmt == "national":
        try:
            return str(phonenumbers.parse(canonical, None).national_number)
        except phonenumbers.NumberParseException:
            return None
    if fmt.startswith("shape:"):
        shape, chars = fmt[6:], re.sub(r"[^A-Za-z0-9]", "", canonical)
        if len(chars) != shape.count("#"):
            return None
        it = iter(chars)
        return "".join(next(it) if ch == "#" else ch for ch in shape)
    if fmt.startswith("date:"):
        try:
            return date.fromisoformat(canonical[:10]).strftime(fmt[5:])
        except ValueError:
            return None
    if fmt.startswith("expiry:"):
        m = re.fullmatch(r"(\d{2})/(\d{2})", canonical)
        if m is None or not 1 <= int(m.group(1)) <= 12:
            return None
        return date(2000 + int(m.group(2)), int(m.group(1)), 1).strftime(fmt[7:])
    raise ValueError("unknown fill format")


def choose_format(attr: str, canonical: str, facts: FieldFacts, locale: str) -> str:
    """The first rendering of the persona value that the field's own rules accept (pattern,
    maxlength, input type). The choice is stored, so the export re-renders every persona the
    same way."""
    if facts.input_type == "date":
        return "date:%Y-%m-%d"
    if facts.input_type == "month":
        return "expiry:%Y-%m" if attr == "card_expiry" else "date:%Y-%m"
    candidates: list[str]
    if attr == "dob":
        ph = (facts.placeholder or "").strip().lower()
        candidates = [f"date:{DATE_PLACEHOLDERS[ph]}"] if ph in DATE_PLACEHOLDERS else []
        candidates += ["date:%m/%d/%Y" if locale == "en_US" else "date:%d/%m/%Y", "date:%Y-%m-%d"]
    elif attr == "card_expiry":
        candidates = ["as_is", "expiry:%m/%Y"]
    else:
        candidates = [
            "as_is",
            *(f"shape:{s}" for s in SHAPES.get(attr, ())),
            *(["national"] if attr == "phone" else []),
            "digits",
        ]
    pattern = compile_html_pattern(facts.pattern)
    for fmt in dict.fromkeys(candidates):
        value = apply_format(fmt, canonical)
        if value is None or (facts.maxlength and len(value) > facts.maxlength):
            continue
        if pattern is not None and not pattern_accepts(pattern, value):
            continue
        if facts.input_type == "number" and not value.isdigit():
            continue
        return fmt
    return candidates[0]


_PAT_TOKEN = re.compile(
    r"\\d|\\w|\\(.)|\[([^\]]+)\]|([^\\\[\](){}?*+|^$.])|(\{(\d+)(?:,(\d*))?\}|\?|\+|\*)"
)
_MAX_SAMPLE = 64


def _expand_class(body: str) -> str:
    if body.startswith("^"):
        return ""
    chars: list[str] = []
    i = 0
    while i < len(body):
        if i + 2 < len(body) and body[i + 1] == "-":
            lo, hi = ord(body[i]), ord(body[i + 2])
            if hi < lo or hi - lo > 256:
                return ""
            chars.extend(chr(c) for c in range(lo, hi + 1))
            i += 3
        elif body[i] == "\\" and i + 1 < len(body):
            chars.extend("0123456789" if body[i + 1] == "d" else body[i + 1])
            i += 2
        else:
            chars.append(body[i])
            i += 1
    return "".join(dict.fromkeys(chars))


def sample_for_pattern(pattern: str | None, rng: random.Random) -> str | None:
    """A neutral value for a non-personal field with a pattern (``APP-\\d{4}`` → ``APP-4821``).

    Supports literals, escapes, ``\\d``, ``\\w``, character classes with ranges and the
    quantifiers ``{n}``, ``{n,m}``, ``?``, ``+``, ``*``. Anything else (groups, alternation,
    anchors, negated classes) → ``None``. Only safe patterns are sampled
    (:func:`~dps.layers.scanner.classify.compile_html_pattern`)."""
    if not pattern:
        return None
    compiled = compile_html_pattern(pattern)
    if compiled is None or re.search(r"[()|^$]", pattern.replace("\\(", "").replace("\\)", "")):
        return None
    out: list[str] = []
    pos, last_pool = 0, None
    while pos < len(pattern):
        m = _PAT_TOKEN.match(pattern, pos)
        if m is None:
            return None
        tok = m.group(0)
        if m.group(4) is not None:  # a quantifier applies to the previous pool
            if last_pool is None or not out:
                return None
            reps = 0 if tok in ("?", "*") else (int(m.group(5)) if m.group(5) else 1)
            if reps > _MAX_SAMPLE:
                return None
            out.pop()
            out.extend(rng.choice(last_pool) for _ in range(reps))
            last_pool = None
        else:
            if tok == "\\d":
                pool = "0123456789"
            elif tok == "\\w":
                pool = "abcdefghjkmnpqrstuvwxyz23456789"
            elif m.group(1) is not None:
                pool = m.group(1)
            elif m.group(2) is not None:
                pool = _expand_class(m.group(2))
                if not pool:
                    return None
            else:
                pool = tok
            out.append(rng.choice(pool))
            last_pool = pool
        pos = m.end()
    value = "".join(out)
    return value if pattern_accepts(compiled, value) else None


_PLACEHOLDER_OPTION = re.compile(r"^\W*(choose|select|pick|please|--)", re.IGNORECASE)


def _usable(o: OptionFacts) -> bool:
    return not o.disabled and o.value.strip() != "" and not _PLACEHOLDER_OPTION.match(o.text)


def choose_option(facts: FieldFacts, wanted: str | None, rng: random.Random) -> OptionFacts | None:
    """The option matching the persona's ``wanted`` value (value or text, case and accents
    ignored), else a seeded choice among the real options (placeholders and disabled ones
    skipped); ``None`` when there is none."""
    usable = [o for o in facts.options if _usable(o)]
    if not usable:
        return None
    if wanted:
        target = normalise(wanted)
        for o in usable:
            if target and target in (normalise(o.value), normalise(o.text)):
                return o
    return rng.choice(usable)


# --- tokens of persona values -------------------------------------------------------------------
@dataclass
class TokenResult:
    """Tokens of persona values, their methods, the reveal hints and the tokenised document."""

    tokens: dict[str, str]
    methods: dict[str, str]
    reveal_hints: list[RevealHint]
    document: dict[str, str]


def tokens_for(
    engine: PrivacyEngine,
    items: dict[str, tuple[str, str]],
    *,
    key: KeyMaterial,
    policy: Policy,
    locale: str,
    persona_no: int,
) -> TokenResult:
    """Tokens of persona values (obfuscate mode) through the engine — policy methods, FPE when
    enabled, det_token fallback — verified so that no persona value survives its own token.

    ``items`` maps a field key to ``(entity type, persona value)``; it is positional too, so
    ``functools.partial(tokens_for, engine, key=…, …)`` is the ``tokens`` callable of
    :func:`prepare_fill`. Raises
    :class:`LeakageError` when the verification fails."""
    keys = list(items)
    doc = {f"f{i}": items[k][1] for i, k in enumerate(keys)}
    findings = [
        Finding(
            locator=Locator(path=f"$.f{i}"),
            entity_type=items[k][0],
            score=1.0,
            detectors=["scanner:fill"],
            sensitivity=entity_info(items[k][0]).sensitivity,
            value_hash=hash_value(items[k][1]),
        )
        for i, k in enumerate(keys)
    ]
    plan = build_plan(findings, policy, "obfuscate", fpe_enabled=engine.fpe_enabled)
    options = ApplyOptions(
        policy=policy,
        key=key,
        surrogates=None,
        fpe_enabled=engine.fpe_enabled,
        cipher_factory=engine.cipher_factory,
        token_style="natural",
        locale=normalise_locale(locale),
        anchor=f"persona:{persona_no}",
    )
    applied = apply_plan(plan=plan, options=options, data=doc)
    kept: set[tuple[str, int | None, int | None]] = {
        (e.finding.locator.path, None, None) for e in plan.entries if e.method == "keep"
    }
    if not verify_output(collect_originals(findings, data=doc), applied.output, kept=kept).passed:
        raise LeakageError("leakage")
    hint_method = {h.path: h.method for h in applied.reveal_hints}
    tokens = {k: str(applied.output[f"f{i}"]) for i, k in enumerate(keys)}
    methods = {k: hint_method.get(f"$.f{i}", plan.entries[i].method) for i, k in enumerate(keys)}
    return TokenResult(
        tokens=tokens,
        methods=methods,
        reveal_hints=list(applied.reveal_hints),
        document={k: str(v) for k, v in applied.output.items()},
    )


# --- preparing a fill ---------------------------------------------------------------------------
@dataclass
class FillAction:
    """One action on one field element (``value`` is synthetic; hidden from ``repr`` because a
    credential is never shown)."""

    key: str
    frame_index: int
    field_no: int
    kind: FillKind
    value: str | None = field(default=None, repr=False)
    option_value: str | None = field(default=None, repr=False)
    check: bool = False


@dataclass
class PersonaFill:
    """The actions of one fill and what each field shows (``shown`` is what is stored)."""

    actions: list[FillAction]
    shown: dict[str, str | None] = field(repr=False)
    methods: dict[str, str]
    formats: dict[str, str | None]
    needles: list[str] = field(repr=False)


_NEUTRAL_TEXT = {"textarea": "Sample text"}
_NEUTRAL_BY_TYPE = {
    "number": "1",
    "email": "sample@example.org",
    "url": "https://example.org/",
    "tel": "5550100",
    "date": "2025-01-15",
    "month": "2025-01",
    "week": "2025-W03",
    "time": "10:30",
    "datetime-local": "2025-01-15T10:30",
}


def _field_id(f: FieldFacts) -> str:
    """An RNG label for ``f`` (page text goes only into a hash, never anywhere else)."""
    return hash_value(f"{f.name}|{f.id}|{f.label}")


def _neutral_text(f: FieldFacts, rng: random.Random) -> str:
    sample = sample_for_pattern(f.pattern, rng)
    if sample is None:
        sample = _NEUTRAL_BY_TYPE.get(f.input_type) or _NEUTRAL_TEXT.get(f.tag, "Sample")
    if f.maxlength and len(sample) > f.maxlength:
        sample = sample[: f.maxlength]
    return sample


def prepare_fill(
    fields: list[FieldFacts],
    classes: dict[str, FieldClassification],
    plans: dict[str, FieldPlan],
    *,
    persona: dict[str, str],
    persona_no: int,
    seed: bytes,
    locale: str,
    mode: Literal["synthesize", "obfuscate"],
    tokens: Callable[[dict[str, tuple[str, str]]], TokenResult] | None,
) -> PersonaFill:
    """The actions that fill ``fields`` with persona ``persona_no`` (``synthesize``) or with
    tokens of its values (``obfuscate``: the fields planned ``obfuscate`` go through ``tokens``
    in one call; every other field keeps its synthesize value, method ``keep``).

    ``needles`` are the shown values of the ``obfuscate``-planned fields (sensitive values only;
    the guard looks for them in every URL)."""
    actions: list[FillAction] = []
    shown: dict[str, str | None] = {}
    methods: dict[str, str] = {}
    formats: dict[str, str | None] = {}
    radio_done: set[tuple[int, int, str]] = set()

    def act(f: FieldFacts, kind: FillKind, **kw: Any) -> None:
        actions.append(
            FillAction(key=f.key, frame_index=f.frame_index, field_no=f.field_no, kind=kind, **kw)
        )

    for f in fields:
        plan = plans.get(f.key)
        if plan is None or plan.kind == "skip":
            continue
        attr = plan.persona_attr
        if plan.kind in ("text", "date") and attr is not None:
            canonical = persona[attr]
            fmt = choose_format(attr, canonical, f, locale)
            value = apply_format(fmt, canonical)
            if value is None:
                fmt, value = "as_is", canonical
            act(f, plan.kind, value=value)
            shown[f.key], methods[f.key], formats[f.key] = value, "surrogate", fmt
        elif plan.kind == "credential":
            act(f, "credential", value=persona["otp" if attr == "otp" else "credential"])
            shown[f.key], methods[f.key], formats[f.key] = None, "credential", None
        elif plan.kind == "neutral_text":
            value = _neutral_text(f, _rng(seed, "neutral", persona_no, _field_id(f)))
            act(f, "neutral_text", value=value)
            shown[f.key], methods[f.key], formats[f.key] = value, "neutral", None
        elif plan.kind == "select":
            rng = _rng(seed, "option", persona_no, _field_id(f))
            wanted = persona.get(attr) if attr else None
            option = choose_option(f, wanted, rng)
            if option is None:
                continue
            matched = wanted is not None and normalise(wanted) in (
                normalise(option.value),
                normalise(option.text),
            )
            act(f, "select", option_value=option.value)
            usable = [o for o in f.options if _usable(o)]
            shown[f.key] = option.text if matched else f"option:{usable.index(option) + 1}"
            methods[f.key] = "surrogate" if matched else "neutral"
            formats[f.key] = None
        elif plan.kind == "radio":
            group_key = (f.frame_index, f.form_index, f.name or f.key)
            if group_key in radio_done:
                continue
            radio_done.add(group_key)
            group = [
                g
                for g in fields
                if plans.get(g.key) is not None
                and plans[g.key].kind == "radio"
                and (g.frame_index, g.form_index, g.name or g.key) == group_key
            ]
            wanted = persona.get(attr) if attr else None
            chosen = next(
                (
                    g
                    for g in group
                    if wanted is not None and normalise(g.label) == normalise(wanted)
                ),
                None,
            )
            matched = chosen is not None
            if chosen is None:
                chosen = _rng(seed, "radio", persona_no, _field_id(f)).choice(group)
            act(chosen, "radio", check=True)
            shown[chosen.key] = wanted if matched and wanted else "selected"
            methods[chosen.key] = "surrogate" if matched else "neutral"
            formats[chosen.key] = None
        elif plan.kind == "check":
            if plan.fill_status != "neutral":
                continue
            act(f, "check", check=True)
            shown[f.key], methods[f.key], formats[f.key] = "checked", "neutral", None

    sensitive = [
        f.key
        for f in fields
        if (p := plans.get(f.key)) is not None
        and p.obfuscate
        and p.kind in ("text", "date")
        and shown.get(f.key)
    ]
    if mode == "obfuscate":
        if tokens is None:
            raise ValueError("obfuscate mode needs a token function")
        for key in shown:
            if key not in sensitive:
                methods[key] = "keep"
        items: dict[str, tuple[str, str]] = {}
        for key in sensitive:
            cls = classes.get(key)
            attr = plans[key].persona_attr
            entity = (cls.entity_type if cls is not None else None) or ATTR_ENTITY[attr or "name"]
            items[key] = (entity, shown[key] or "")
        if items:
            result = tokens(items)
            for a in actions:
                if a.key in result.tokens:
                    a.value = result.tokens[a.key]
            for key, token in result.tokens.items():
                shown[key] = token
                methods[key] = result.methods[key]
    needles = [v for k in sensitive if (v := shown.get(k))]
    return PersonaFill(
        actions=actions, shown=shown, methods=methods, formats=formats, needles=needles
    )
