"""The envelope token style: ``[[PII:ENTITY_TYPE:base32token]]`` (spec §5.5).

Self-describing, so ``reveal`` needs no hints for text; the inner token is lower-case base32
(``a-z2-7``) and the entity name is upper snake case.
"""

from __future__ import annotations

import re

ENVELOPE_RE = re.compile(r"\[\[PII:([A-Z][A-Z0-9_]{0,63}):([a-z2-7]{2,})\]\]")


def make_envelope(entity_type: str, token: str) -> str:
    """Wrap ``token`` as ``[[PII:<entity_type>:<token>]]``.

    Raises:
        ValueError: when the result would not parse back (entity not upper snake case, token not
            lower-case base32) — an envelope that ``parse_envelopes`` cannot read is not
            self-describing, so callers must keep the bare token and rely on reveal hints.
    """
    envelope = f"[[PII:{entity_type}:{token}]]"
    if ENVELOPE_RE.fullmatch(envelope) is None:
        raise ValueError("entity type or token does not fit the envelope grammar")
    return envelope


def parse_envelopes(text: str) -> list[tuple[int, int, str, str]]:
    """``(start, end, entity_type, token)`` for every envelope in ``text``, in order."""
    return [(m.start(), m.end(), m.group(1), m.group(2)) for m in ENVELOPE_RE.finditer(text)]


def is_envelope(value: str) -> bool:
    """True when ``value`` is exactly one envelope."""
    return ENVELOPE_RE.fullmatch(value) is not None
