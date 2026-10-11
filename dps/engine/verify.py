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


def verify_output(
    originals: list[tuple[Finding, str]],
    output: Any,
    *,
    kept: set[tuple[str, int | None, int | None]] | None = None,
    min_len: int = 4,
) -> Verification:
    """Search every string in ``output`` for every original that was meant to be transformed.

    ``kept`` holds the ``(path, start, end)`` locators the plan kept on purpose. Their values are
    allowed only in the output leaf at that same path; a hit anywhere else still counts.
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
    return Verification(passed=not hits, checked=checked, hits=hits)
