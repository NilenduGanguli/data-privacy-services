"""The leakage sweep (spec §5.7): no original sensitive value may survive in the output.

Originals and output are compared after normalisation (compatibility-decomposed, case-folded,
accents dropped, Unicode decimal digits read as ASCII, letters and digits only), so a
re-formatted SSN, a re-cased e-mail or a full-width copy still counts as a leak. Every string in
the output is searched — leaf values *and* object keys — each on its own, so JSON escaping (a
``\\n`` inside a value) cannot split a match. A hit names where in the output the original was
found, once per location and entity type.

Values whose normalised form is shorter than ``min_len`` are skipped (they would match by
accident). Locators the plan deliberately kept are skipped too, and their values are allowed
wherever they appear: a date kept in the notes may equal a birth date shifted elsewhere, and its
presence is the policy's choice, not a leak.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterator
from typing import Any

from pydantic import BaseModel, Field

from dps.engine.findings import Finding
from dps.engine.jsonpath import flatten, get_path


class Verification(BaseModel):
    """Outcome of the sweep: how many originals were checked and where any were found."""

    passed: bool
    checked: int
    hits: list[dict[str, str]] = Field(default_factory=list)


def _fold(ch: str) -> str:
    if ch.isdecimal():
        return str(unicodedata.decimal(ch))
    return ch if ch.isalnum() else ""


def normalise(text: str) -> str:
    """Letters and digits only, compatibility-decomposed, case-folded, accents dropped."""
    folded = unicodedata.normalize("NFKD", unicodedata.normalize("NFKD", text).casefold())
    return "".join(_fold(ch) for ch in folded)


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


def _strings(node: Any, path: str = "$") -> Iterator[tuple[str, str]]:
    """Every object key and every scalar leaf (as text) with the path it sits at."""
    if isinstance(node, dict):
        for key, value in node.items():
            child = flatten({key: None}, path)[0][0]  # flatten's own path syntax for the key
            yield child, str(key)
            yield from _strings(value, child)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _strings(value, f"{path}[{index}]")
    elif node is not None and not isinstance(node, bool):
        yield path, str(node)


def verify_output(
    originals: list[tuple[Finding, str]],
    output: Any,
    *,
    kept: set[tuple[str, int | None, int | None]] | None = None,
    min_len: int = 4,
) -> Verification:
    """Search every string in ``output`` for every original that was meant to be transformed."""
    haystacks = [(path, normalise(s)) for path, s in _strings(output)]
    kept = kept or set()
    allowed = {
        normalise(original)
        for finding, original in originals
        if (finding.locator.path, finding.locator.start, finding.locator.end) in kept
    }
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
        if needle in allowed:
            continue
        for path, haystack in haystacks:
            if needle in haystack and (path, finding.entity_type) not in seen:
                seen.add((path, finding.entity_type))
                hits.append({"path": path, "entity_type": finding.entity_type})
    return Verification(passed=not hits, checked=checked, hits=hits)
