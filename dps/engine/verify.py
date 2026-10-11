"""The leakage sweep (spec §5.7): no original sensitive value may survive in the output.

Originals and output are compared after normalisation (compatibility-decomposed, case-folded,
accents dropped, Unicode decimal digits read as ASCII, letters and digits only), so a
re-formatted SSN, a re-cased e-mail or a full-width copy still counts as a leak. Every string in
the output is searched — leaf values *and* object keys — each on its own, so JSON escaping (a
``\\n`` inside a value) cannot split a match. A hit names where in the output the original was
found, once per location and entity type.

Values whose normalised form is shorter than ``min_len`` are skipped (they would match by
accident). Locators the plan deliberately kept are skipped too. A kept value is allowed **only in
the output leaf it was kept in**: a date kept in the notes may equal a birth date that was
shifted, and its presence *in the notes* is the policy's choice, not a leak. The same value
anywhere else (the birth-date field left untransformed, a copy in another field or list, an
object key) is still a leak; keys are never kept, so a kept value never excuses a key. The
allowance is per leaf, not per span: inside the leaf that holds the kept span, the value is
allowed wherever it occurs.

**Name parts.** A name is also checked part by part, because a surrogate (or a rewrite) can bring
back one part of it — ``Utkarsh Krishnan`` for ``Ananya Krishnan`` — while the whole name is gone.
When the sweep is given the input, every part of a detected, transformed name with at least
``min_len`` characters is counted in each output string, normalised like the whole values
(``Krishnan``, ``Radhakrishnan``, ``krishnan12``, ``Krishna Nagar`` all hold ``krishnan``). A
string whose output holds a part more often than its input did *outside* the detected names is a
hit: a part that was already there for another reason (``prices rose`` next to ``Rose Mehta``) is
not one, a part that appears from nowhere is.

**Coverage.** The sweep can only look for values the detectors found: a name nobody detected is
never checked. :func:`assess_coverage` says what the sweep could see — the detectors that ran,
whether NER was on, how many free-text fields there were — and names each gap. With NER off a
name in free text is found only next to a cue (title, "name is", contact details), so free text
swept without NER is reported as a gap and the verdict is ``partial``, not ``passed``.
``status`` is ``failed`` (an original was found), ``partial`` (none found, coverage has gaps) or
``passed`` (none found, no gaps); ``passed`` stays the plain "no hits" flag.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Iterator
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from dps.engine.findings import Finding
from dps.engine.jsonpath import flatten, get_path

NerState = Literal["on", "off", "unavailable"]

#: The gap statement per NER state (constant text: a report never carries a value).
NER_GAP: dict[str, str] = {
    "off": (
        "free-text names and places: NER is off; only names next to a cue (a title, "
        "'name is', contact details) are detected and checked"
    ),
    "unavailable": (
        "free-text names and places: NER is configured but its model did not load; only names "
        "next to a cue (a title, 'name is', contact details) are detected and checked"
    ),
}
#: A JSON string with at least this many words is free text (a name could hide in it); a
#: shorter one is a code, a label or a value its key describes (the structural tier's domain).
FREE_TEXT_MIN_WORDS = 2
_WORD = re.compile(r"[^\W\d_]{2,}")
#: Normalised values shorter than this are not checked (they would match by accident).
SWEEP_MIN_LEN = 4
#: The entity whose parts are checked one by one (see "Name parts" above).
NAME_ENTITY = "PERSON_NAME"
#: A part of a name: letters only, any script.
_NAME_PART = re.compile(r"[^\W\d_]+")


class Coverage(BaseModel):
    """What the sweep could see. It checks the values the detectors found, nothing else."""

    basis: Literal["detected-values"] = "detected-values"
    detectors: list[str]
    ner: NerState
    free_text_fields: int
    complete: bool
    gaps: list[str] = Field(default_factory=list)


class Verification(BaseModel):
    """Outcome of the sweep: how many originals were checked, where any were found, and what
    the sweep could not see. ``status`` is derived (``failed`` / ``partial`` / ``passed``)."""

    passed: bool
    status: Literal["passed", "partial", "failed"] = "passed"
    checked: int
    hits: list[dict[str, str]] = Field(default_factory=list)
    coverage: Coverage | None = None

    @model_validator(mode="after")
    def _derive_status(self) -> Verification:
        if not self.passed:
            self.status = "failed"
        elif self.coverage is not None and not self.coverage.complete:
            self.status = "partial"
        else:
            self.status = "passed"
        return self


def _fold(ch: str) -> str:
    if ch.isdecimal():
        return str(unicodedata.decimal(ch))
    return ch if ch.isalnum() else ""


def normalise(text: str) -> str:
    """Letters and digits only, compatibility-decomposed, case-folded, accents dropped."""
    folded = unicodedata.normalize("NFKD", unicodedata.normalize("NFKD", text).casefold())
    return "".join(_fold(ch) for ch in folded)


def name_parts(names: Iterable[str], min_len: int = 1) -> frozenset[str]:
    """The normalised parts of ``names`` with at least ``min_len`` characters."""
    return frozenset(
        part
        for name in names
        for word in _NAME_PART.findall(name)
        if len(part := normalise(word)) >= min_len
    )


def collect_originals(
    findings: list[Finding], *, data: Any | None = None, text: str | None = None
) -> list[tuple[Finding, str]]:
    """The original string for each finding (whole value or span), read from the input."""
    out: list[tuple[Finding, str]] = []
    for f in findings:
        loc = f.locator
        source: Any = text if loc.path == "$" and text is not None else get_path(data, loc.path)
        value = str(source)
        out.append((f, value if loc.whole else value[loc.start : loc.end]))
    return out


def _strings(node: Any, path: str = "$") -> Iterator[tuple[str, bool, str]]:
    """Every object key and every scalar leaf (as text) with the path it sits at.

    The flag is ``True`` for an object key (reported at the path of the value under it).
    """
    if isinstance(node, dict):
        for key, value in node.items():
            child = flatten({key: None}, path)[0][0]  # flatten's own path syntax for the key
            yield child, True, str(key)
            yield from _strings(value, child)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _strings(value, f"{path}[{index}]")
    elif node is not None and not isinstance(node, bool):
        yield path, False, str(node)


def _free_text(text: str, min_words: int) -> bool:
    count = 0
    for _ in _WORD.finditer(text):
        count += 1
        if count >= min_words:
            return True
    return False


def assess_coverage(
    *,
    data: Any | None = None,
    text: str | None = None,
    findings: Iterable[Finding],
    kept: set[tuple[str, int | None, int | None]],
    detectors: list[str],
    ner: NerState,
) -> Coverage:
    """What the sweep could see for this input (counts and fixed statements, never a value).

    Free text is the text payload (when it has a word), or each JSON string leaf of at least
    :data:`FREE_TEXT_MIN_WORDS` words that was not replaced whole (a leaf replaced whole hides
    nothing; one kept in clear, or with span findings only, still could). Without NER (off or
    failed to load), free text is a gap: a name there without a cue is never detected.
    """
    if text is not None:
        free = 1 if _free_text(text, 1) else 0
    else:
        replaced = {
            f.locator.path
            for f in findings
            if f.locator.whole and (f.locator.path, f.locator.start, f.locator.end) not in kept
        }
        free = sum(
            1
            for path, is_key, value in _strings(data)
            if not is_key and path not in replaced and _free_text(value, FREE_TEXT_MIN_WORDS)
        )
    gaps = [NER_GAP[ner]] if free and ner != "on" else []
    return Coverage(
        detectors=detectors, ner=ner, free_text_fields=free, complete=not gaps, gaps=gaps
    )


def _outside(text: str, spans: list[tuple[int, int] | None]) -> list[str]:
    """The pieces of ``text`` outside ``spans`` (``None``: the whole value), kept apart so that
    removing a name never joins the words on either side of it."""
    if any(span is None for span in spans):
        return []
    pieces: list[str] = []
    cursor = 0
    for start, end in sorted(span for span in spans if span is not None):
        pieces.append(text[cursor:start])
        cursor = max(cursor, end)
    pieces.append(text[cursor:])
    return pieces


def _name_part_hits(
    originals: list[tuple[Finding, str]],
    output: Any,
    source: Any,
    kept: set[tuple[str, int | None, int | None]],
    min_len: int,
) -> list[str]:
    """Output locations holding a part of a transformed name more often than the input did
    outside the detected names (see "Name parts" in the module docstring)."""
    names: list[str] = []
    name_spans: dict[str, list[tuple[int, int] | None]] = {}
    for finding, original in originals:
        loc = finding.locator
        if finding.entity_type != NAME_ENTITY or (loc.path, loc.start, loc.end) in kept:
            continue
        names.append(original)
        span = None if loc.whole else (loc.start or 0, loc.end or 0)
        name_spans.setdefault(loc.path, []).append(span)
    parts = name_parts(names, min_len)
    if not parts:
        return []
    before: dict[tuple[str, bool], list[str]] = {}  # normalised input pieces per location
    for path, is_key, value in _strings(source):
        named = not is_key and path in name_spans
        pieces = _outside(value, name_spans[path]) if named else [value]
        before[(path, is_key)] = [normalise(piece) for piece in pieces]
    hits: list[str] = []
    for path, is_key, value in _strings(output):
        after, held = normalise(value), before.get((path, is_key), [])
        if any(after.count(part) > sum(h.count(part) for h in held) for part in parts):
            hits.append(path)
    return hits


def verify_output(
    originals: list[tuple[Finding, str]],
    output: Any,
    *,
    kept: set[tuple[str, int | None, int | None]] | None = None,
    min_len: int = SWEEP_MIN_LEN,
    coverage: Coverage | None = None,
    source: Any | None = None,
) -> Verification:
    """Search every string in ``output`` for every original that was meant to be transformed.

    ``kept`` holds the ``(path, start, end)`` locators the plan kept on purpose. Their values are
    allowed only in the output leaf at that same path; a hit anywhere else still counts.
    ``coverage`` (from :func:`assess_coverage`) is attached as is and decides ``partial``.
    ``source`` is the input (the JSON document or the text); with it, names are also checked
    part by part (see "Name parts" in the module docstring).
    """
    haystacks = [(path, is_key, normalise(s)) for path, is_key, s in _strings(output)]
    kept = kept or set()
    # output leaf path -> normalised values kept there on purpose (output paths equal input paths:
    # apply rewrites leaves in place and never renames keys)
    allowed: dict[str, set[str]] = {}
    for finding, original in originals:
        loc = finding.locator
        if (loc.path, loc.start, loc.end) in kept:
            allowed.setdefault(loc.path, set()).add(normalise(original))
    hits: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    checked = 0
    for finding, original in originals:
        loc = finding.locator
        if (loc.path, loc.start, loc.end) in kept:
            continue
        needle = normalise(original)
        if len(needle) < min_len:
            continue
        checked += 1
        for path, is_key, haystack in haystacks:
            if needle not in haystack or (path, finding.entity_type) in seen:
                continue
            if not is_key and needle in allowed.get(path, ()):
                continue  # the value the policy kept in this very leaf
            seen.add((path, finding.entity_type))
            hits.append({"path": path, "entity_type": finding.entity_type})
    if source is not None:
        for path in _name_part_hits(originals, output, source, kept, min_len):
            if (path, NAME_ENTITY) not in seen:
                seen.add((path, NAME_ENTITY))
                hits.append({"path": path, "entity_type": NAME_ENTITY})
    return Verification(passed=not hits, checked=checked, hits=hits, coverage=coverage)
