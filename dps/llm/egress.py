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

A system message that *equals* one of DPS's registered fixed prompts (:func:`fixed_system_prompts`:
the labelling, rewrite and spec instructions, built from constants; call data goes in the user
message) is not scanned at all: an instruction can look like a value. The labelling prompt's
entity-type list holds ``USERNAME``, which has a SWIFT BIC's shape and blocked every labelling call,
and its type names (``PASSPORT``, ``AADHAAR``, ``ACCOUNT_NUMBER``, ``PRIVATE_KEY``) are also common
category codes: a sampled ID type ``PASSPORT`` or a product ``ACCOUNT`` is a protected original,
and T3 never reached the provider on typical KYC data (FINAL r1 privacy-ops B1). Any other system
text is scanned in full, like call data.

A protected value written only in ``x``, ``X``, ``9`` and punctuation (``999``) is not looked for:
it cannot be told apart from the value shapes DPS sends (``"shapes": ["999"]``).

Each distinct value counts once. Under ``DPS_LLM_EGRESS_CHECK=strict`` (required in staging/prod)
a prompt with any raw value is never sent (:class:`LlmEgressBlocked`); under ``warn`` it is sent
and counted on the alarm counters. Only counts and purposes are logged — never the prompt.
"""

from __future__ import annotations

import functools
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


@functools.cache
def fixed_system_prompts() -> frozenset[str]:
    """DPS's own fixed system prompts (constants, no call data): the T3 labelling instructions,
    the M0 text rewrite and the M1 generator-spec proposer."""
    from dps.engine.detect.llm_label import system_prompt
    from dps.engine.synth.llm_text import REWRITE_SYSTEM
    from dps.engine.synth.schema_profile import SPEC_SYSTEM

    return frozenset({system_prompt(), REWRITE_SYSTEM, SPEC_SYSTEM})


def _scanned(messages: list[dict[str, str]]) -> str:
    """Every message's text but a registered fixed system prompt's."""
    fixed = fixed_system_prompts()
    return "\n".join(
        str(m.get("content", ""))
        for m in messages
        if not (m.get("role") == "system" and str(m.get("content", "")) in fixed)
    )


def _words(text: str) -> set[str]:
    folded = unicodedata.normalize("NFKD", text).casefold()
    return {normalise(w) for w in _WORD.findall(folded)}


def residual_values(
    messages: list[dict[str, str]], *, protected: Sequence[str] = (), region: str = "US"
) -> int:
    """How many distinct raw values the outgoing ``messages`` still hold (see the module rules).

    ``region`` is the default region for national-format phone numbers.
    """
    data = PLACEHOLDER.sub(" ", _scanned(messages))
    hay, words = normalise(data), _words(data)
    found: set[str] = set()
    for value in protected:
        needle = normalise(str(value))
        if len(needle) < MIN_PROTECTED or _SHAPE.fullmatch(str(value).strip()):
            continue  # too short to say anything, or a value shape (``999``) DPS sends itself
        if needle in words or (len(needle) >= _SUBSTRING_MIN and needle in hay):
            found.add(needle)
    for hit in [*scan_text(data), *scan_phones(data, region=region)]:
        if not (hit.validated or hit.entity_type in SELF_IDENTIFYING):
            continue
        raw = data[hit.start : hit.end]
        if _SHAPE.fullmatch(raw):
            continue  # a value shape (xxx.x@x.xx, 9999), not a value
        found.add(normalise(raw) or raw)
    return len(found)
