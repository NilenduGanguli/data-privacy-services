"""The LLM egress verifier (M5 Task 16B, spec P1/§10): the last check before a prompt leaves.

Callers already template their prompts (placeholders, field metadata, value shapes) and run their
own leak guard; this is a second, independent check at the single egress point,
:meth:`dps.llm.gateway.LlmGateway.complete`. It counts the raw values still in the outgoing text:

* every *protected* original the caller names (normalised; at least 3 characters — a value of
  4+ characters matches anywhere in the normalised text, like M0's leak guard, a 3-character one
  only as a whole word, so ``Ann`` does not match ``planning``);
* every T1 pattern hit that identifies on its own — a checksum-validated identifier or card, an
  e-mail address or a valid phone number — outside DPS placeholders (``{EMAIL_1}``) and that is
  not a value *shape* (only ``x``/``X``/``9`` and punctuation: what M1's labelling prompts send).

The pattern scan reads the non-system messages only. A system message is DPS's own fixed
instructions — every caller builds it from constants (the labelling, rewrite and spec prompts) and
puts call data in the user message — and an instruction can look like a value: the labelling
prompt's entity-type list holds ``USERNAME``, which has a SWIFT BIC's shape (a valid ISO country,
``NA``, in positions 5-6), and blocked every labelling call. Protected originals are still looked
for in every message.

Each distinct value counts once. Under ``DPS_LLM_EGRESS_CHECK=strict`` (required in staging/prod)
a prompt with any raw value is never sent (:class:`LlmEgressBlocked`); under ``warn`` it is sent
and counted on the alarm counters. Only counts and purposes are logged — never the prompt.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence

from dps.engine.detect.patterns import scan_phones, scan_text
from dps.engine.verify import normalise
from dps.llm.gateway import LlmCallError

#: DPS placeholders (``{PERSON_NAME_1}``) never count as values.
PLACEHOLDER = re.compile(r"\{[A-Z][A-Z0-9_]*_\d+\}")
#: Entity types that identify on their own even without a checksum.
SELF_IDENTIFYING = frozenset({"EMAIL", "PHONE"})
_SHAPE = re.compile(r"^[xX9\W_]+$")
_WORD = re.compile(r"\w+")
MIN_PROTECTED = 3
_SUBSTRING_MIN = 4


class LlmEgressBlocked(LlmCallError):
    """A prompt contained a raw sensitive value; nothing was sent.

    A subclass of :class:`~dps.llm.gateway.LlmCallError`, so every caller's existing fallback
    (M0 text rewrite, M1 spec proposer and labelling) takes over.
    """


def _words(text: str) -> set[str]:
    folded = unicodedata.normalize("NFKD", text).casefold()
    return {normalise(w) for w in _WORD.findall(folded)}


def residual_values(
    messages: list[dict[str, str]], *, protected: Sequence[str] = (), region: str = "US"
) -> int:
    """How many distinct raw values the outgoing ``messages`` still hold (see the module rules).

    ``region`` is the default region for national-format phone numbers.
    """
    text = "\n".join(str(m.get("content", "")) for m in messages)
    clean = PLACEHOLDER.sub(" ", text)
    hay, words = normalise(clean), _words(clean)
    found: set[str] = set()
    for value in protected:
        needle = normalise(str(value))
        if len(needle) < MIN_PROTECTED:
            continue
        if needle in words or (len(needle) >= _SUBSTRING_MIN and needle in hay):
            found.add(needle)
    data = PLACEHOLDER.sub(
        " ", "\n".join(str(m.get("content", "")) for m in messages if m.get("role") != "system")
    )
    for hit in [*scan_text(data), *scan_phones(data, region=region)]:
        if not (hit.validated or hit.entity_type in SELF_IDENTIFYING):
            continue
        raw = data[hit.start : hit.end]
        if _SHAPE.fullmatch(raw):
            continue  # a value shape (xxx.x@x.xx, 9999), not a value
        found.add(normalise(raw) or raw)
    return len(found)
