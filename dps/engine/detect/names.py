"""Tier T1 — person names in free text, found by the cue next to them (no model needed).

A run of capitalised words (``Ananya Krishnan``, ``A. Rao``, ``ANANYA KRISHNAN``, ``Juan de la
Cruz``) is a ``PERSON_NAME`` when a cue says so:

* **title** before it — ``Mr``, ``Mrs``, ``Ms``, ``Dr``, ``Shri``, ``Smt``, ``Sra.``, … (the
  title itself stays in clear);
* **lead-in** before it — ``name is``, ``name:``, ``named``, ``Dear``, ``s/o``, ``d/o``, ``w/o``,
  ``c/o``, ``Contact:``, ``From:``, ``To:``, ``me llamo``, ``mi nombre es``; weaker lead-ins (``customer``, ``account holder``,
  ``applicant``, ``beneficiary``, ``cliente``, ``titular``, …) need two or more name words;
* **contact** details right after it — ``Ananya Krishnan (ananya.k@…, +91 …)``, ``<…@…>``,
  ``, +91 …`` (two or more name words);
* **possessive** followed by a personal attribute — ``Ananya Krishnan's Aadhaar``;
* **payment narration** — the payee or remitter of ``NEFT/Ananya Krishnan/…`` (also ``IMPS``,
  ``RTGS``, ``UPI``, ``NACH``, ``ACH``; two or more name words, so ``NEFT/HDFC BANK/`` is not one).

Without a cue nothing is reported: capitalised words alone are headings, places and products as
often as names. NER (tier T2) is the general answer and is optional; with it off, these cues are
the only way a name in free text is found, and the verification report says so (``coverage``).
A cue hit (0.8-0.9) outranks an NER label for the same span (spaCy scores 0.85), so a name the
small English model calls a LOCATION is still treated as a name.

Hits carry the cue as evidence, never the value.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from dps.engine.detect.patterns import Hit

DETECTOR = "pattern:name_cue"
MAX_NAME_WORDS = 4
STRONG_SCORE = 0.9
POSSESSIVE_SCORE = 0.85
WEAK_SCORE = 0.8

#: A word: letters, with inner apostrophes or hyphens (O'Neil, Jean-Luc), an optional final dot.
_TOKEN = re.compile(r"[^\W\d_]+(?:['’\-][^\W\d_]+)*\.?")
_POSSESSIVE = re.compile(r"['’]s$", re.IGNORECASE)
#: Characters a name may follow directly (anything else — ``@``, ``{``, ``_``, ``/``, a digit —
#: means the word is part of an address, a placeholder or an identifier).
_LEFT_OK = frozenset(" \t\r\n(\"'“‘[<:,;")

TITLES = frozenset(
    {
        # English and Indian usage
        "mr", "mrs", "ms", "miss", "mx", "dr", "prof", "sir", "dame", "lady", "lord",
        "shri", "shree", "sri", "smt", "shrimati", "kumari", "km", "sushri",
        "capt", "col", "maj", "lt", "gen", "rev", "fr", "hon", "adv", "justice",
        # Spanish, French, German
        "sr", "sra", "srta", "don", "doña", "dña", "señor", "señora", "señorita",
        "mme", "mlle", "herr", "frau",
    }
)  # fmt: skip

#: Lead-ins that name the person on their own (one name word is enough).
_STRONG_LEADIN = re.compile(
    r"(?:\bname\s*(?:is|was|[:=\-])|\bnamed|\bdear|\bhi|\bhello|\battn\.?|\battention"
    r"|\b(?:contact(?:\s+person)?|from|to|cc)\s*:"
    r"|\b[sdwc]\s*/\s*o|\b(?:son|daughter|wife|husband|widow)\s+of"
    r"|\bsigned\s+by|\bregards,?|\bsincerely,?"
    r"|\bme\s+llamo|\bmi\s+nombre\s+es|\bnombre\s*[:=\-]|\bestimad[oa]|\bhola)"
    r"\s*[:,\-]?\s*$",
    re.IGNORECASE,
)
#: Role words that usually precede a name, but also a product or a team: two name words needed.
_WEAK_LEADIN = re.compile(
    r"(?:\bcustomer|\bclient|\b(?:co-)?applicant|\bborrower|\bguarantor|\bbeneficiary|\bnominee"
    r"|\bpayee|\bpayer|\bremitter|\bcard\s*holder|\baccount\s+holder|\bpolicy\s*holder"
    r"|\binsured|\bpatient|\bemployee|\bsubmitted\s+by|\brequested\s+by|\braised\s+by"
    r"|\bcliente|\btitular|\bbeneficiari[oa]|\bsolicitante)"
    r"\s*[:,\-]?\s*$",
    re.IGNORECASE,
)
#: A bank-statement narration code right before a name: ``NEFT/<name>/…`` (the slash ends it).
_NARRATION = re.compile(r"\b(?:NEFT|IMPS|RTGS|UPI|NACH|ACH)\s*/\s*$", re.IGNORECASE)
_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,24}")
_PHONE = re.compile(r"\+?\(?\d(?:[ \-.()]{0,2}\d){7,14}")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{2,4}")
#: After the name: an optional bracket or separator, an optional label, then an e-mail or phone.
_CONTACT = re.compile(
    r"\s*(?:[(<\[,:;]|\s[-–—])?\s*"
    r"(?:(?:e-?mail|mail|tel|phone|mobile|mob|ph|cell)\.?\s*[:\-]?\s*)?",
    re.IGNORECASE,
)
_PERSONAL_NOUN = re.compile(
    r"\s+(?:e-?mail|mail|phone|mobile|number|aadhaar|aadhar|pan|passport|account|card|address"
    r"|dob|date of birth|birthday|ssn|salary|loan|kyc|signature|wife|husband|son|daughter"
    r"|father|mother)\b",
    re.IGNORECASE,
)

#: Capitalised words that are not names (they break a run): greetings, roles, document words,
#: weekdays and the months that are not also first names, common acronyms.
STOPWORDS = frozenset(
    {
        "the", "a", "an", "and", "or", "of", "for", "to", "from", "in", "on", "at", "by",
        "with", "about", "as", "is", "re", "fw", "fwd", "subject", "dear", "hi", "hello",
        "hey", "thanks", "thank", "regards", "sincerely", "best", "kind", "warm", "please",
        "kindly", "note", "attn", "attention", "team", "support", "service", "services",
        "customer", "customers", "client", "care", "help", "helpdesk", "desk", "contact", "call",
        "us", "our", "we", "you", "your", "my", "i", "he", "she", "they", "it", "this", "that",
        "email", "e-mail", "mail", "phone", "mobile", "tel", "telephone", "fax", "address",
        "name", "account", "holder", "card", "bank", "branch", "office", "department", "dept",
        "ltd", "limited", "pvt", "inc", "llc", "corp", "plc", "co", "company", "group",
        "industries", "solutions", "technologies", "systems", "enterprises", "associates",
        "partners", "holdings", "insurance", "finance", "financial", "capital", "foundation",
        "trust", "university", "college", "school", "hospital", "clinic", "centre", "center",
        "store", "shop", "agency", "ministry", "government", "authority", "council", "board",
        "committee", "meeting", "manager", "officer", "director", "operations", "sales",
        "marketing", "legal", "compliance", "risk", "admin",
        "sir", "madam", "ma'am", "aadhaar", "aadhar", "pan", "kyc", "uid", "dob", "otp", "id",
        "ref", "no", "number", "date", "amount", "balance", "statement", "loan", "policy",
        "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
        "january", "february", "march", "july", "august", "september", "october", "november",
        "december", "today", "tomorrow", "yesterday",
        "el", "la", "le", "les", "los", "las", "un", "una", "estimado", "estimada", "hola", "gracias", "cliente", "servicio",
        "atención", "banco", "correo", "teléfono", "dirección", "nombre", "cuenta", "tarjeta",
    }
)  # fmt: skip
#: Lower-case particles allowed between name words (``Juan de la Cruz``, ``Ali bin Hassan``).
PARTICLES = frozenset(
    {"de", "da", "das", "do", "dos", "del", "della", "di", "du", "la", "le", "van", "von", "der",
     "den", "bin", "binti", "bint", "al", "el", "y"}
)  # fmt: skip


@dataclass(frozen=True, slots=True)
class _Word:
    start: int
    end: int  # without a final dot, except after an initial ("A.")
    core: str  # the word without its final dot or possessive
    dot: bool
    possessive: bool


def _words(text: str) -> list[_Word]:
    out: list[_Word] = []
    for m in _TOKEN.finditer(text):
        raw, start = m.group(0), m.start()
        if (
            start > 0
            and text[start - 1] not in _LEFT_OK
            and not (text[start - 1] == "/" and _NARRATION.search(text[max(0, start - 12) : start]))
        ):
            continue
        dot = raw.endswith(".")
        core = raw[:-1] if dot else raw
        possessive = bool(_POSSESSIVE.search(core)) and not dot
        if possessive:
            core = core[:-2]
        initial = dot and len(core) == 1
        out.append(_Word(start, start + len(core) + (1 if initial else 0), core, dot, possessive))
    return out


def _is_title(w: _Word) -> bool:
    return w.core.casefold() in TITLES


def _is_name_word(w: _Word) -> bool:
    core = w.core
    if not core or not core[0].isupper():
        return False
    if len(core) == 1:
        return w.dot  # an initial ("A."), even where the letter is also a word ("A", "I")
    if core.casefold() in STOPWORDS or _is_title(w):
        return False
    return any(c.islower() for c in core[1:]) or core.isupper()


def _is_particle(w: _Word) -> bool:
    return w.core in PARTICLES and not w.dot


def _runs(text: str, words: list[_Word]) -> list[list[int]]:
    """Maximal runs of name words (indices into ``words``), split at anything but a space."""
    runs: list[list[int]] = []
    current: list[int] = []
    pending: list[int] = []  # particles waiting for the next name word
    for i, w in enumerate(words):
        gap = text[words[i - 1].start + len(words[i - 1].core) : w.start] if i else ""
        joined = bool(current) and gap.strip(". ") == "" and "\n" not in gap and gap != ""
        if current and not joined:
            runs.append(current)
            current, pending = [], []
        if _is_name_word(w):
            current += [*pending, i]
            pending = []
            ends = w.possessive or (w.dot and len(w.core) > 1)
            if ends:  # "Krishnan's", or "Rao." at the end of a sentence
                runs.append(current)
                current = []
        elif current and _is_particle(w):
            pending.append(i)
        elif current:
            runs.append(current)
            current, pending = [], []
    if current:
        runs.append(current)
    return runs


def _contact_follows(text: str, end: int) -> bool:
    lead = _CONTACT.match(text, end)
    pos = lead.end() if lead else end
    if _EMAIL.match(text, pos):
        return True
    phone = _PHONE.match(text, pos)
    return phone is not None and _DATE.match(text, pos) is None


def _title_before(text: str, words: list[_Word], first: int) -> bool:
    """A title (``Mr``, ``Smt``, ``Sra.``) directly before ``words[first]``."""
    if first == 0:
        return False
    title = words[first - 1]
    between = text[title.start + len(title.core) : words[first].start]
    return _is_title(title) and between.strip(". ") == "" and "\n" not in between


def _cue(text: str, words: list[_Word], run: list[int], n_names: int) -> tuple[str, float] | None:
    """The strongest cue for ``run`` (evidence, score), or ``None``."""
    before = text[max(0, words[run[0]].start - 40) : words[run[0]].start]
    if _title_before(text, words, run[0]):
        return "cue:title", STRONG_SCORE
    if _STRONG_LEADIN.search(before):
        return "cue:lead-in", STRONG_SCORE
    if n_names < 2:
        return None
    if _WEAK_LEADIN.search(before):
        return "cue:lead-in", WEAK_SCORE
    if _NARRATION.search(before):
        return "cue:narration", WEAK_SCORE
    last = words[run[-1]]
    if last.possessive and _PERSONAL_NOUN.match(text, last.end + 2):
        return "cue:possessive", POSSESSIVE_SCORE
    if _contact_follows(text, last.end):
        return "cue:contact", STRONG_SCORE
    return None


def scan_names(text: str) -> list[Hit]:
    """``PERSON_NAME`` hits for names with a cue next to them (see the module docstring)."""
    words = _words(text)
    hits: list[Hit] = []
    for run in _runs(text, words):
        names = [i for i in run if not _is_particle(words[i])]
        if all(len(words[i].core) == 1 for i in names):
            continue  # initials alone are not a name
        cue = _cue(text, words, run, len(names))
        if cue is None:
            continue
        # At most MAX_NAME_WORDS name words: the first ones after a title or lead-in, the last
        # ones before contact details (a capitalised word that opens the sentence stays out).
        keep = names[-MAX_NAME_WORDS:] if cue[0] == "cue:contact" else names[:MAX_NAME_WORDS]
        start, end = words[keep[0]].start, words[keep[-1]].end
        hits.append(Hit("PERSON_NAME", start, end, cue[1], DETECTOR, cue[0]))
    return hits
