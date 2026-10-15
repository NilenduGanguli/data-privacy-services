"""Field and rendered-text classification for the UI scanner (spec §8 "Classify").

Signal families, strongest first: ``token`` (the HTML autocomplete token, mapped straight to an
entity type), ``label`` / ``name`` (the M0 structural dictionary over label, placeholder,
aria-label, title / name, id), ``type`` (input type), ``pattern`` (the ``pattern`` attribute tested
against synthetic probes), ``maxlength`` (supporting evidence only) and ``value`` (M0 value
detectors over a pre-filled value). Families combine per entity by noisy-OR; the token's entity
wins unless a checksum-validated value disagrees. Evidence strings are value-free.

A ``pattern`` attribute is a regular expression chosen by the page. It is compiled only when it
stays inside a safe subset (:func:`compile_html_pattern`): no look-arounds or back-references, no
quantified group whose body can vary, and at most :data:`MAX_PATTERN_BRANCHING` variable repeats
or alternations in all — so a hostile page cannot stall the worker with catastrophic
backtracking. Inputs tested against a compiled pattern are synthetic and at most
:data:`MAX_PATTERN_INPUT` characters long (:func:`pattern_accepts`).

**T3 metadata labelling** (M5 Task 8B, spec §5.3 and §8; opt-in: the policy lists ``llm_label``
in ``detect.tiers`` and a real LLM provider is configured). After every field of a page is
classified, the fields left without an entity — not a password, hidden or file input — are
labelled by the LLM gateway in one call per page (at most ``DPS_LLM_LABEL_MAX_FIELDS``), from
value-free metadata only: the ``name`` (or ``id``) attribute, the label as the inventory stores it
(detector-redacted), the placeholder's shape, the element and a known input type, the
autocomplete token from the fixed dictionary and the ``maxlength``/``pattern``-length/``required``
hints (:func:`leftover_field_meta`). The page's own values — what its fields hold or show, the
personal data the detectors find in its text, and every token of its text that holds a digit or
an ``@`` (:func:`page_protected`) — stay in worker memory and are checked against the prompt
before it is sent; a page label is metadata and is not protected. A verdict sets the field's
entity at a confidence capped at 0.85 with ``llm_label`` among its detectors and evidence;
persona attributes and fill formats then follow as for any other classifier.

Nothing here imports Playwright or Pillow (the API imports this module).
"""

from __future__ import annotations

import importlib
import math
import re
from collections.abc import Callable, Mapping, Sequence
from functools import lru_cache
from typing import TYPE_CHECKING, Any, Literal

from dps.engine.detect.ensemble import Detector
from dps.engine.detect.llm_label import (
    LLM_LABEL_TIER,
    SCORE_CAP,
    FieldMeta,
    LabelOutcome,
    gateway_usable,
    label,
)
from dps.engine.detect.structural import (
    CHANNEL_NAMES,
    MIXED_NAME_SCORE,
    MIXED_VALUE_NAMES,
    KeyCandidate,
    classify_key,
    normalize_key,
)
from dps.engine.findings import Finding
from dps.engine.taxonomy import Sensitivity, entity_info
from dps.layers.scanner.models import (
    FieldClassification,
    FieldFacts,
    PageSnapshot,
    RenderedFinding,
    TextFacts,
)
from dps.snapshots import FREE_TEXT, MIXED_PII

if TYPE_CHECKING:
    from dps.config import Settings
    from dps.engine.policy import Policy
    from dps.llm.gateway import LlmGateway

AutocompleteState = Literal["absent", "on", "off", "named", "unknown"]

AUTOCOMPLETE_ENTITY: dict[str, str | None] = {
    "name": "PERSON_NAME",
    "given-name": "PERSON_NAME",
    "additional-name": "PERSON_NAME",
    "family-name": "PERSON_NAME",
    "nickname": "PERSON_NAME",
    "honorific-prefix": None,
    "honorific-suffix": None,
    "username": "USERNAME",
    "new-password": "PASSWORD",
    "current-password": "PASSWORD",
    "one-time-code": "PASSWORD",
    "organization": "ORG_NAME",
    "organization-title": None,
    "street-address": "STREET_ADDRESS",
    "address-line1": "STREET_ADDRESS",
    "address-line2": "STREET_ADDRESS",
    "address-line3": "STREET_ADDRESS",
    "address-level4": "CITY",
    "address-level3": "CITY",
    "address-level2": "CITY",
    "address-level1": "LOCATION",
    "country": "LOCATION",
    "country-name": "LOCATION",
    "postal-code": "POSTAL_CODE",
    "cc-name": "PERSON_NAME",
    "cc-given-name": "PERSON_NAME",
    "cc-additional-name": "PERSON_NAME",
    "cc-family-name": "PERSON_NAME",
    "cc-number": "CARD_PAN",
    "cc-exp": "CARD_EXPIRY",
    "cc-exp-month": "CARD_EXPIRY",
    "cc-exp-year": "CARD_EXPIRY",
    "cc-csc": "CVV",
    "cc-type": None,
    "transaction-currency": None,
    "transaction-amount": "AMOUNT",
    "language": None,
    "bday": "DATE_OF_BIRTH",
    "bday-day": "DATE_OF_BIRTH",
    "bday-month": "DATE_OF_BIRTH",
    "bday-year": "DATE_OF_BIRTH",
    "sex": "GENDER",
    "url": "URL",
    "photo": "URL",
    "tel": "PHONE",
    "tel-country-code": "PHONE",
    "tel-national": "PHONE",
    "tel-area-code": "PHONE",
    "tel-local": "PHONE",
    "tel-local-prefix": "PHONE",
    "tel-local-suffix": "PHONE",
    "tel-extension": "PHONE",
    "email": "EMAIL",
    "impp": "USERNAME",
}
_MODIFIERS = frozenset(
    {"shipping", "billing", "home", "work", "mobile", "fax", "pager", "webauthn"}
)

#: Synthetic probes (never real values) used to read what a ``pattern`` attribute accepts.
PATTERN_PROBES: dict[str, tuple[str, ...]] = {
    "AADHAAR": ("2345 6789 0123", "234567890123"),
    "PAN_IN": ("ABCPK1234Q",),
    "SSN": ("900-12-3456", "900123456"),
    "CARD_PAN": ("4111 1111 1111 1111", "4111111111111111"),
    "CARD_EXPIRY": ("04/28", "04/2028"),
    "CVV": ("123",),
    "IFSC": ("ABCD0123456",),
    "POSTAL_CODE": ("560001", "94105", "94105-1234", "SW1A 1AA"),
    "PHONE": ("+91 98451 20377", "9845120377", "(415) 555-0132", "+1 415-555-0132"),
    "DATE_OF_BIRTH": ("14/03/1988", "1988-03-14", "03/14/1988"),
    "EMPLOYEE_ID": ("EMP-123456",),
    "CUSTOMER_ID": ("CUST-884120",),
    "NINO": ("QQ123456C", "QQ 12 34 56 C"),
    "SIN": ("046 454 286", "046454286"),
    "CURP": ("GODE561231HDFRRN09",),
    "RFC": ("GODE561231GR8",),
    "ACCOUNT_NUMBER": ("123456789012",),
    "ROUTING_ABA": ("021000021",),
}
MAXLENGTH_HINTS: dict[int, frozenset[str]] = {
    3: frozenset({"CVV"}),
    4: frozenset({"CVV"}),
    5: frozenset({"POSTAL_CODE"}),
    6: frozenset({"POSTAL_CODE"}),
    9: frozenset({"SSN", "ROUTING_ABA"}),
    10: frozenset({"PAN_IN", "PHONE"}),
    11: frozenset({"IFSC", "SSN"}),
    12: frozenset({"AADHAAR"}),
    14: frozenset({"AADHAAR"}),
    16: frozenset({"CARD_PAN"}),
    19: frozenset({"CARD_PAN"}),
}
#: Credentials: typed into password/OTP inputs, never part of the "autocomplete on" rule.
CREDENTIAL_ENTITIES = frozenset({"PASSWORD", "API_KEY", "JWT", "PRIVATE_KEY"})

#: Longest ``pattern`` attribute that is compiled at all.
MAX_PATTERN_LENGTH = 200
#: Most variable repeats plus alternations a compiled pattern may hold (backtracking budget).
MAX_PATTERN_BRANCHING = 4
#: Longest (synthetic) input ever tested against a page's pattern.
MAX_PATTERN_INPUT = 64

_TYPE_SIGNAL = {
    "email": ("EMAIL", 0.9),
    "tel": ("PHONE", 0.85),
    "password": ("PASSWORD", 0.95),
    "url": ("URL", 0.7),
    "date": ("DATE", 0.5),
    "month": ("DATE", 0.5),
}
#: Characters the structural dictionary reads as JSON-path syntax; in page text they are spaces.
_PATH_CHARS = re.compile(r"[.\[\]'\"$]+")
_MAX_KEY_TEXT = 200

_parser: Any = importlib.import_module("re._parser")
_c: Any = importlib.import_module("re._constants")
_REPEATS = frozenset({_c.MAX_REPEAT, _c.MIN_REPEAT})
_ATOMS = frozenset(
    {_c.LITERAL, _c.NOT_LITERAL, _c.IN, _c.ANY, _c.AT, _c.CATEGORY, _c.RANGE, _c.NEGATE}
)


class _Unsafe(Exception):
    pass


def _branching(items: Any) -> int:
    """Backtracking budget a parsed (sub)pattern uses; raises :class:`_Unsafe` outside the subset."""
    cost = 0
    for op, av in items:
        if op in _ATOMS:
            continue
        if op in _REPEATS:
            lo, hi, body = av
            inner = _branching(body)
            if lo == hi:
                cost += inner * max(1, min(hi, 8))
            elif inner:  # a variable repeat over a body that can itself vary: exponential
                raise _Unsafe
            else:
                cost += 1
        elif op == _c.SUBPATTERN:
            cost += _branching(av[-1])
        elif op == _c.BRANCH:
            alternatives = av[1]
            cost += len(alternatives) - 1 + max(_branching(a) for a in alternatives)
        else:  # look-arounds, back-references, conditionals, atomic groups, possessive repeats
            raise _Unsafe
        if cost > MAX_PATTERN_BRANCHING:
            raise _Unsafe
    return cost


def autocomplete_token(attr: str | None) -> tuple[str | None, AutocompleteState]:
    """The field-name token of an ``autocomplete`` attribute and the attribute's state."""
    if attr is None or not attr.strip():
        return None, "absent"
    tokens = attr.strip().lower().split()
    if tokens == ["on"]:
        return None, "on"
    if tokens == ["off"]:
        return None, "off"
    names = [t for t in tokens if not t.startswith("section-") and t not in _MODIFIERS]
    if names and names[0] in AUTOCOMPLETE_ENTITY:
        return names[0], "named"
    return None, "unknown"


@lru_cache(maxsize=512)
def compile_html_pattern(pattern: str | None) -> re.Pattern[str] | None:
    """The ``pattern`` attribute as a Python regex (HTML anchors it: use ``fullmatch``).

    ``None`` for an empty, long, invalid or unsafe pattern (see the module docstring): the
    pattern is a page's choice and must not be able to stall the worker.
    """
    if not pattern or len(pattern) > MAX_PATTERN_LENGTH:
        return None
    try:
        _branching(_parser.parse(pattern))
        return re.compile(f"(?:{pattern})")
    except (_Unsafe, re.error, RecursionError, OverflowError):
        return None


def pattern_accepts(compiled: re.Pattern[str], value: str) -> bool:
    """Whether the compiled page pattern accepts ``value`` (longer inputs are never tested)."""
    return len(value) <= MAX_PATTERN_INPUT and compiled.fullmatch(value) is not None


def pattern_entities(pattern: str | None) -> list[str]:
    """Entity types whose probes the pattern accepts (one or two; more is no signal)."""
    compiled = compile_html_pattern(pattern)
    if compiled is None:
        return []
    hits = sorted(
        e
        for e, probes in PATTERN_PROBES.items()
        if any(pattern_accepts(compiled, p) for p in probes)
    )
    return hits if 1 <= len(hits) <= 2 else []


def key_candidates(text: str) -> list[KeyCandidate]:
    """The structural dictionary over page text (a label is text, not a JSON path)."""
    return classify_key(_PATH_CHARS.sub(" ", text[:_MAX_KEY_TEXT]).strip())


def _noisy_or(scores: list[float]) -> float:
    acc = 1.0
    for s in scores:
        acc *= 1.0 - s
    return 1.0 - acc


def _whole_value_hits(detector: Detector, value: str) -> list[tuple[str, float, bool]]:
    """(entity, score, checksum_validated) for findings covering the whole (stripped) value."""
    lead, tail = len(value) - len(value.lstrip()), len(value.rstrip())
    out = []
    for f in detector.detect(text=value).findings:
        start = f.locator.start if f.locator.start is not None else 0
        end = f.locator.end if f.locator.end is not None else len(value)
        if start <= lead and end >= tail:
            out.append((f.entity_type, f.score, any(e.startswith("checksum") for e in f.evidence)))
    return out


def _unclassified(confidence: float) -> FieldClassification:
    return FieldClassification(
        entity_type=None, sensitivity=None, confidence=confidence, detectors=[], evidence=[]
    )


def classify_field(
    facts: FieldFacts, *, detector: Detector, min_score: float
) -> FieldClassification:
    """What ``facts`` asks for, with value-free evidence (see the module docstring)."""
    signals: dict[str, dict[str, tuple[float, str]]] = {}

    def add(entity: str, family: str, score: float, evidence: str) -> None:
        fams = signals.setdefault(entity, {})
        if family not in fams or score > fams[family][0]:
            fams[family] = (score, evidence)

    token, _ = autocomplete_token(facts.autocomplete)
    token_entity = AUTOCOMPLETE_ENTITY.get(token) if token else None
    if token_entity:
        add(token_entity, "token", 0.99, f"autocomplete:{token}")
    sources = (
        ("label", facts.label, 1.0, "label"),
        ("aria", facts.aria_label, 1.0, "label"),
        ("placeholder", facts.placeholder, 0.9, "label"),
        ("title", facts.title, 0.9, "label"),
        ("name", facts.name, 0.95, "name"),
        ("id", facts.id, 0.95, "name"),
    )
    for source, text, factor, family in sources:
        if text:
            for cand in key_candidates(text):
                phrase = cand.evidence.split(":", 1)[-1]  # the dictionary's phrase, not page text
                add(cand.entity_type, family, round(cand.score * factor, 3), f"{source}:{phrase}")
    if facts.input_type in _TYPE_SIGNAL:
        entity, score = _TYPE_SIGNAL[facts.input_type]
        add(entity, "type", score, f"type:{facts.input_type}")
    probed = pattern_entities(facts.pattern)
    for entity in probed:
        add(entity, "pattern", 0.75 if len(probed) == 1 else 0.6, f"pattern:{entity}")
    validated: set[str] = set()
    if facts.value and facts.input_type not in ("password", "hidden"):
        for entity, score, checked in _whole_value_hits(detector, facts.value):
            add(entity, "value", score, f"value:{entity}")
            if checked and score >= 0.95:
                validated.add(entity)
    hinted = MAXLENGTH_HINTS.get(facts.maxlength or 0, frozenset())
    for entity in list(signals):
        if entity in hinted:
            add(entity, "maxlength", 0.3, f"maxlength:{facts.maxlength}")
    if not signals:
        return _unclassified(0.0)
    scores = {e: _noisy_or([s for s, _ in fams.values()]) for e, fams in signals.items()}
    if token_entity and not (validated and token_entity not in validated):
        best = token_entity
    elif validated:
        best = max(validated, key=lambda e: scores[e])
    else:
        best = max(scores, key=lambda e: (scores[e], len(signals[e])))
    confidence = min(0.99, math.floor(scores[best] * 100) / 100)
    if scores[best] < min_score:
        return _unclassified(confidence)
    fams = signals[best]
    return FieldClassification(
        entity_type=best,
        sensitivity=str(entity_info(best).sensitivity),
        confidence=confidence,
        detectors=sorted(fams),
        evidence=[ev for _, ev in fams.values()],
    )


#: Field types whose value is never read to classify them (a password is never read at all).
_UNREAD_TYPES = frozenset({"password", "hidden", "file"})


def _names(facts: FieldFacts) -> set[str]:
    """The field's own names — label, aria-label, ``name`` and ``id`` — normalised as the structural
    dictionary reads them (page text, never stored)."""
    out: set[str] = set()
    for text in (facts.label, facts.aria_label, facts.name, facts.id):
        if text:
            norm = normalize_key(_PATH_CHARS.sub(" ", text[:_MAX_KEY_TEXT]).strip())
            if norm:
                out.add(norm)
    return out


def _named(names: set[str], phrases: frozenset[str]) -> bool:
    """Whether one of ``names`` is one of ``phrases``: a one-word phrase must be the whole name, a
    longer one may end it ("preferred contact method") — so "loan value" is no contact value."""
    return any(
        name == phrase or (" " in phrase and name.endswith(" " + phrase))
        for name in names
        for phrase in phrases
    )


def shown_text(facts: FieldFacts) -> str:
    """What a field shows: an input's or a textarea's value; for a select, what it draws for its
    selected option — the option's label attribute when it has one, else its text
    (``OptionFacts.drawn``; S2 captures-privacy r2 B3)."""
    if facts.tag == "select":
        chosen = [o for o in facts.options if o.selected and o.value.strip()]
        return chosen[0].drawn if chosen else ""
    return facts.value or ""


def _sensitive(entity: str) -> bool:
    return entity_info(entity).sensitivity.rank >= Sensitivity.MEDIUM.rank


def _shown_classification(
    detector: Detector, shown: str, min_score: float
) -> FieldClassification | None:
    """The classification of what a field shows (MEDIUM+ findings at the bar): a whole-value
    finding gives its entity, findings inside longer text ``FREE_TEXT``; ``None`` without any."""
    lead, tail = len(shown) - len(shown.lstrip()), len(shown.rstrip())
    whole: list[Finding] = []
    spans: list[Finding] = []
    for x in detector.detect(text=shown).findings:
        if x.score < min_score or not _sensitive(x.entity_type):
            continue
        start = x.locator.start if x.locator.start is not None else 0
        end = x.locator.end if x.locator.end is not None else len(shown)
        (whole if start <= lead and end >= tail else spans).append(x)
    if whole:
        best = max(whole, key=lambda x: x.score)
        return FieldClassification(
            entity_type=best.entity_type,
            sensitivity=str(entity_info(best.entity_type).sensitivity),
            confidence=min(0.99, math.floor(best.score * 100) / 100),
            detectors=["value"],
            evidence=[f"value:{best.entity_type}"],
        )
    if not spans:
        return None
    types = sorted({x.entity_type for x in spans})
    return FieldClassification(
        entity_type=FREE_TEXT,
        sensitivity=str(Sensitivity.max_of(entity_info(t).sensitivity for t in types)),
        confidence=min(0.99, math.floor(max(x.score for x in spans) * 100) / 100),
        detectors=["value"],
        evidence=[f"value_span:{t}" for t in types],
    )


def classify_fields(
    fields: list[FieldFacts], *, detector: Detector, min_score: float
) -> dict[str, FieldClassification]:
    """:func:`classify_field` for every field of a page, then main's mixed-PII rules (L2 discovery,
    integrated verify r1 B1) for the fields it leaves unclassified, so that no field is presented
    as "not personal" while its form or what it shows says otherwise:

    - a contact value (``value``, ``contact value``, ``contact details`` …) in the same form (and
      frame) as a channel field (``channel``, ``contact method`` …) is ``MIXED_PII`` by its name,
      at :data:`~dps.engine.detect.structural.MIXED_NAME_SCORE` when that reaches ``min_score`` — an
      e-mail on one record, a mobile number on the next — even while it is empty;
    - a field whose shown value (:func:`shown_text`) carries MEDIUM+ findings at the bar is
      classified by them: a whole-value finding gives its entity (a selected option that is an
      e-mail address), findings inside longer text make it ``FREE_TEXT``.

    Password, hidden and file inputs are never read. Evidence names dictionary phrases and entity
    types only.
    """
    classes = {f.key: classify_field(f, detector=detector, min_score=min_score) for f in fields}
    names = {f.key: _names(f) for f in fields}
    channel_forms = {
        (f.frame_index, f.form_index) for f in fields if _named(names[f.key], CHANNEL_NAMES)
    }
    for f in fields:
        if classes[f.key].entity_type is not None or f.input_type in _UNREAD_TYPES:
            continue
        own = names[f.key]
        if (
            MIXED_NAME_SCORE >= min_score
            and (f.frame_index, f.form_index) in channel_forms
            and _named(own, MIXED_VALUE_NAMES)
            and not _named(own, CHANNEL_NAMES)
        ):
            classes[f.key] = FieldClassification(
                entity_type=MIXED_PII,
                sensitivity=str(entity_info(MIXED_PII).sensitivity),
                confidence=MIXED_NAME_SCORE,
                detectors=["name"],
                evidence=["mixed:value_next_to_channel"],
            )
            continue
        shown = shown_text(f)
        if shown.strip():
            found = _shown_classification(detector, shown, min_score)
            if found is not None:
                classes[f.key] = found
    return classes


_T_PATH = re.compile(r"^\$\.t\[(\d+)\]$")


def classify_rendered(
    texts: list[TextFacts], *, detector: Detector, structural_floor: float = 0.7
) -> list[RenderedFinding]:
    """Personal data rendered as page text: value detectors over every text node, plus the row/
    column header, ``<dt>`` or ``Label:`` hint next to a value (the structural dictionary for
    rendered data — it catches names that patterns cannot)."""
    if not texts:
        return []
    out: list[RenderedFinding] = []
    whole: set[int] = set()
    for f in detector.detect(data={"t": [t.text for t in texts]}).findings:
        m = _T_PATH.match(f.locator.path)
        if m is None:
            continue
        k = int(m.group(1))
        t = texts[k]
        lead, tail = len(t.text) - len(t.text.lstrip()), len(t.text.rstrip())
        start = f.locator.start if f.locator.start is not None else lead
        end = f.locator.end if f.locator.end is not None else tail
        if start <= lead and end >= tail:
            whole.add(k)
        out.append(
            RenderedFinding(
                frame_index=t.frame_index,
                i=t.i,
                start=start,
                end=end,
                entity_type=f.entity_type,
                score=f.score,
                detectors=f.detectors,
                in_table=t.in_table,
            )
        )
    for k, t in enumerate(texts):
        if k in whole or not t.label_hint or sum(ch.isalnum() for ch in t.text) < 2:
            continue
        cands = key_candidates(t.label_hint)
        if cands and cands[0].score >= structural_floor:
            out = [r for r in out if not (r.frame_index == t.frame_index and r.i == t.i)]
            lead, tail = len(t.text) - len(t.text.lstrip()), len(t.text.rstrip())
            out.append(
                RenderedFinding(
                    frame_index=t.frame_index,
                    i=t.i,
                    start=lead,
                    end=tail,
                    entity_type=cands[0].entity_type,
                    score=cands[0].score,
                    detectors=["structural:label_hint"],
                    in_table=t.in_table,
                )
            )
    return sorted(out, key=lambda r: (r.frame_index, r.i, r.start))


def redact_text(text: str | None, detector: Detector, *, limit: int = 120) -> str | None:
    """Page text (labels, titles, validation messages, selectors) with detected values replaced
    by ``<ENTITY>`` — the only form in which such text is stored."""
    if text is None:
        return None
    clean = " ".join(text.split())[: limit * 2]
    spans = sorted(
        (
            (
                f.locator.start or 0,
                f.locator.end if f.locator.end is not None else len(clean),
                f.entity_type,
            )
            for f in detector.detect(text=clean).findings
        ),
        reverse=True,
    )
    for start, end, entity in spans:
        clean = clean[:start] + f"<{entity}>" + clean[end:]
    return clean[:limit]


def value_classifier(detector: Detector) -> Callable[[str], str | None]:
    """A ``str -> entity | None`` classifier of whole values (for URL redaction and risk flags)."""

    def classify(value: str) -> str | None:
        hits = _whole_value_hits(detector, value) if len(value.strip()) >= 3 else []
        return max(hits, key=lambda h: h[1])[0] if hits else None

    return classify


def placeholder_shape(text: str | None) -> str | None:
    """A placeholder's shape (digits ``9``, letters ``X``/``x``), at most 24 characters."""
    if not text:
        return None
    # every digit and letter of any script is replaced: a placeholder is page text
    return "".join(
        "9" if ch.isdigit() else "X" if ch.isupper() else "x" if ch.isalpha() else ch
        for ch in text[:24]
    )


def sensitive_prefilled(
    fields: list[FieldFacts],
    classes: dict[str, FieldClassification],
    detector: Detector,
) -> list[tuple[FieldFacts, str]]:
    """``(field, entity)`` for every field that *shows* a value a capture must hide: an input or
    textarea pre-filled with a value, and a select with a real option selected, when the field is
    classified or the detectors find something in what it shows. Password values are never read
    (``FieldFacts.value`` is ``None`` for them)."""
    out: list[tuple[FieldFacts, str]] = []
    for f in fields:
        if not f.visible:
            continue
        shown = shown_text(f)
        if not shown.strip():
            continue
        cls = classes.get(f.key)
        entity = cls.entity_type if cls is not None else None
        if entity is None:
            hits = detector.detect(text=shown).findings
            entity = hits[0].entity_type if hits else None
        if entity is not None:
            out.append((f, entity))
    return out


# --- T3 metadata labelling of the fields T0–T2 left unclassified (M5 Task 8B) --------------------
#: Input types T3 never labels: a credential's value is never read, a hidden input is not shown and
#: a file input holds no text.
UNLABELLED_TYPES = frozenset({"password", "hidden", "file"})
#: The ``type`` values a field record may name in a prompt; any other (page-chosen) value is sent
#: as ``text``, which is what a browser makes of it.
_PROMPT_TYPES = frozenset(
    {
        "text",
        "email",
        "tel",
        "number",
        "url",
        "search",
        "date",
        "datetime-local",
        "month",
        "week",
        "time",
        "color",
        "range",
        "checkbox",
        "radio",
        "select-one",
        "select-multiple",
        "textarea",
    }
)
#: A whitespace-delimited token of page text that holds a digit or an ``@``: an identifier, a
#: number, a date or an address no detector may name — a value, whatever words surround it.
_VALUE_TOKEN = re.compile(r"\S*[\d@]\S*")


def field_ref(facts: FieldFacts) -> str:
    """A field's handle in a labelling prompt and its verdict: ``field:<form>/<field_no>``."""
    return f"field:{facts.form_index}/{facts.field_no}"


def _prompt_autocomplete(attr: str | None) -> str:
    """The autocomplete hint a prompt may carry: a field-name token from the fixed dictionary,
    ``on`` or ``off``; anything else the page wrote is left out (``""``)."""
    token, state = autocomplete_token(attr)
    if token is not None:
        return token
    return state if state in ("on", "off") else ""


def leftover_field_meta(
    fields: Sequence[FieldFacts],
    classes: Mapping[str, FieldClassification],
    *,
    detector: Detector,
) -> list[FieldMeta]:
    """One value-free :class:`~dps.engine.detect.llm_label.FieldMeta` per field T3 may label: no
    entity after T0–T2, not a password, hidden or file input, and something to label it by (a
    name or id, a label or a placeholder). Never a value: the label is redacted as the inventory
    stores it, the placeholder is sent as its shape only."""
    out: list[FieldMeta] = []
    for f in fields:
        cls = classes.get(f.key)
        if cls is None or cls.entity_type is not None or f.input_type in UNLABELLED_TYPES:
            continue
        name = f.name or f.id or ""
        label_text = redact_text(f.label, detector) if f.label else None
        shape = placeholder_shape(f.placeholder)
        if not (name or label_text or shape):
            continue
        kind = f.input_type if f.input_type in _PROMPT_TYPES else "text"
        out.append(
            FieldMeta(
                ref=field_ref(f),
                name=name,
                data_type=f"{f.tag}[type={kind}]",
                label=label_text or None,
                shapes=[shape] if shape else [],
                hints={
                    "autocomplete": _prompt_autocomplete(f.autocomplete),
                    "maxlength": f.maxlength or 0,
                    "pattern_len": len(f.pattern or ""),
                    "required": f.required,
                },
            )
        )
    return out


def page_protected(
    snap: PageSnapshot, *, detector: Detector, rendered: list[RenderedFinding] | None = None
) -> list[str]:
    """The page's own values a labelling prompt must never carry (held in worker memory for this
    page only): what every field holds or shows (a pre-filled or hidden value, the selected
    option's value and text when it has a value), the personal data the detectors find in rendered
    text (spans, and whole texts under a label hint), and every token of rendered or option text
    that holds a digit or an ``@``. Words of page text are metadata, not values: a label rendered
    on the page (``Ref code``) or a placeholder option (``Choose…``) is not protected, so the
    prompt may name it — and the gateway's egress verifier, which looks for protected values in
    its fixed instructions too, is not handed common words that are no value. ``rendered``: the
    page's :func:`classify_rendered` findings when the caller has them (else computed here)."""
    out: set[str] = set()
    token_sources: list[str] = [t.text for t in snap.texts]
    for f in snap.fields:
        out.update(v for v in (f.value, shown_text(f)) if v)
        for option in f.options:
            # a selected option is a value when it has one (as ``shown_text`` reads it): a
            # placeholder option ("Choose…", value "") is page text, not a value
            if option.selected and option.value.strip():
                out.update(v for v in (option.value, option.text, option.label) if v)
            token_sources += [option.value, option.text, option.label]
    by_node = {(t.frame_index, t.i): t.text for t in snap.texts}
    found = rendered if rendered is not None else classify_rendered(snap.texts, detector=detector)
    for r in found:
        text = by_node.get((r.frame_index, r.i), "")
        out.add(text[r.start : r.end])
    for text in token_sources:
        out.update(_VALUE_TOKEN.findall(text or ""))
    return sorted(v for v in out if v.strip())


async def label_leftover_fields(
    fields: Sequence[FieldFacts],
    classes: Mapping[str, FieldClassification],
    *,
    gateway: LlmGateway | None,
    settings: Settings,
    policy: Policy,
    detector: Detector,
    protected: Sequence[str],
) -> tuple[dict[str, FieldClassification], LabelOutcome | None]:
    """T3 for the fields of one page that T0–T2 left unclassified (module docstring): one call.

    Returns the classifications by field key — a labelled field replaced, every other unchanged —
    and the call's :class:`~dps.engine.detect.llm_label.LabelOutcome`: ``None`` when the policy
    does not list ``llm_label`` (nothing requested); without a usable gateway or a leftover field,
    an empty outcome (no call). ``protected``: :func:`page_protected` of the page.
    """
    labelled = dict(classes)
    if LLM_LABEL_TIER not in policy.detect.tiers:
        return labelled, None
    metas = (
        leftover_field_meta(fields, classes, detector=detector) if gateway_usable(gateway) else []
    )
    if not metas:
        return labelled, LabelOutcome()
    outcome = await label(metas, gateway=gateway, settings=settings, protected=protected)
    keys = {field_ref(f): f.key for f in fields}
    for verdict in outcome.verdicts:
        key = keys.get(verdict.ref)
        confidence = min(SCORE_CAP, math.floor(verdict.confidence * 100) / 100)
        if key is None or key not in labelled or confidence < policy.detect.min_score:
            continue
        before = labelled[key]
        labelled[key] = FieldClassification(
            entity_type=verdict.entity_type,
            sensitivity=str(entity_info(verdict.entity_type).sensitivity),
            confidence=confidence,
            detectors=sorted({*before.detectors, LLM_LABEL_TIER}),
            evidence=[*before.evidence, LLM_LABEL_TIER],
        )
    return labelled, outcome
