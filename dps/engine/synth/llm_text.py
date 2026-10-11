"""Free-text synthesis (spec §5.6): findings → typed placeholders → optional LLM rewrite → fill → verify.

The model only ever sees ``{PERSON_NAME_1}``-style placeholders. Before any call,
``assert_placeholder_safe`` proves that no detected original is left in the text; if it is not
safe the model is skipped (never sent) and the deterministic path runs. A rewrite is accepted
only if every placeholder survives, the rewrite introduces no new detectable values (when a
``screen`` is given), and the filled result brings back no original, and no part of a detected
name, that the deterministic fill had removed; any failure falls back to the deterministic fill
(spec §5.6 step 5: "fails closed to the deterministic path"). DPS chooses the surrogates itself
(after declaring the text's detected values to the engine, so no surrogate reuses one of them
or a part of a name), so the output is consistent with the rest of the record whether or not the
model ran.

Every detected value is replaced by a placeholder, including values the plan keeps: the model
sees none of them, and the fill step puts a kept value back afterwards.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from dps.engine.findings import Finding, Plan, PlanEntry
from dps.engine.transform.methods import (
    SurrogateProvider,
    TransformContext,
    declare_record,
    run_method,
)
from dps.engine.verify import NAME_ENTITY, collect_originals, name_parts, normalise
from dps.llm.gateway import LlmCallError, LlmGateway, LlmUnavailable

PLACEHOLDER_RE = re.compile(r"\{([A-Z][A-Z0-9_]*?)_(\d+)\}")
REWRITE_TEMPERATURE = 0.7
REWRITE_MAX_TOKENS = 8192
REWRITE_SYSTEM = (
    "You rewrite short business texts for test data. The text contains placeholders like "
    "{PERSON_NAME_1} or {DATE_2}. Produce a different but equally plausible text with the same "
    "structure, tone and approximate length. Keep EVERY placeholder exactly as written, do not add "
    "new placeholders, and do not invent names, numbers, addresses or identifiers. Reply with the "
    "rewritten text only."
)


class LeakToModelError(RuntimeError):
    """A raw sensitive value was about to be sent to the model."""


class LlmRewriteRejected(RuntimeError):
    """The model's rewrite dropped or altered placeholders."""


@dataclass
class PlaceholderText:
    """The templated text, each label's first finding and original, and findings per label."""

    text: str
    mapping: dict[str, tuple[Finding, str]] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)


def _span(text: str, finding: Finding) -> tuple[int, int]:
    """A finding's span in a root-level text; a whole-value finding covers the stripped text."""
    if finding.locator.whole:
        start = len(text) - len(text.lstrip())
        return start, max(start, len(text.rstrip()))
    return finding.locator.start or 0, finding.locator.end or 0


def to_placeholders(text: str, findings: list[Finding]) -> PlaceholderText:
    """Replace every root-level finding (span or whole value) with a typed placeholder.

    Equal values of one type share one placeholder. Labels never reuse a placeholder-shaped
    string that is already in the text.
    """
    located = sorted(
        ((_span(text, f), f) for f in findings if f.locator.path == "$"),
        key=lambda item: (item[0][0], -item[0][1]),
    )
    taken = {m.group(0) for m in PLACEHOLDER_RE.finditer(text)}
    counters: Counter[str] = Counter()
    by_value: dict[tuple[str, str], str] = {}
    out = PlaceholderText(text="")
    pieces: list[str] = []
    cursor = 0
    for (start, end), f in located:
        if start < cursor or end <= start or end > len(text):
            continue  # overlapping (already replaced) or not inside this text
        original = text[start:end]
        key = (f.entity_type, normalise(original) or original)
        label = by_value.get(key)
        if label is None:
            while label is None or label in taken:
                counters[f.entity_type] += 1
                label = f"{{{f.entity_type}_{counters[f.entity_type]}}}"
            by_value[key] = label
            out.mapping[label] = (f, original)
        out.counts[label] = out.counts.get(label, 0) + 1
        pieces.extend([text[cursor:start], label])
        cursor = end
    pieces.append(text[cursor:])
    out.text = "".join(pieces)
    return out


def assert_placeholder_safe(templated: str, originals: list[str], *, min_len: int = 4) -> None:
    """Raise if any original (normalised, len ≥ min_len) still appears in the text for the model."""
    hay = normalise(templated)
    for original in originals:
        needle = normalise(original)
        if len(needle) >= min_len and needle in hay:
            raise LeakToModelError("refusing to send text that still contains an original value")


def _names(ph: PlaceholderText) -> list[str]:
    """The text's detected names (one per placeholder)."""
    return [
        original for finding, original in ph.mapping.values() if finding.entity_type == NAME_ENTITY
    ]


def _entry_key(f: Finding) -> tuple[str, int | None, int | None, str]:
    return (f.locator.path, f.locator.start, f.locator.end, f.entity_type)


@dataclass
class _Fill:
    replacements: dict[str, str]
    methods: Counter[str]
    notes: Counter[str]


def _resolve(
    ph: PlaceholderText,
    surrogates: SurrogateProvider,
    locale: str,
    anchor: str,
    plan: Plan | None,
) -> _Fill:
    """One replacement per label: the plan's method for that finding, else a surrogate."""
    entries: dict[tuple[str, int | None, int | None, str], PlanEntry] = (
        {_entry_key(e.finding): e for e in plan.entries} if plan is not None else {}
    )
    declare_record(surrogates, anchor, [(f.entity_type, o) for f, o in ph.mapping.values()])
    fill = _Fill({}, Counter(), Counter())
    for label, (finding, original) in ph.mapping.items():
        entry = entries.get(_entry_key(finding))
        if entry is None:
            value: str | None = surrogates.surrogate(finding.entity_type, original, locale, anchor)
            method, note = "surrogate", None
        else:
            ctx = TransformContext(
                entity_type=finding.entity_type,
                path=finding.locator.path,
                mode="synthesize",
                params=entry.params,
                anchor=anchor,
                locale=locale,
                synth=surrogates,
            )
            outcome = run_method(entry.method, original, ctx)
            value, method, note = outcome.value, outcome.method, outcome.note
        fill.replacements[label] = value if value is not None else ""
        fill.methods[method] += ph.counts.get(label, 1)
        if note:
            fill.notes[note] += ph.counts.get(label, 1)
    return fill


def _substitute(templated: str, replacements: dict[str, str]) -> str:
    """Fill known placeholders; leave anything else that merely looks like one untouched."""
    return PLACEHOLDER_RE.sub(lambda m: replacements.get(m.group(0), m.group(0)), templated)


def fill_placeholders(
    templated: str,
    mapping: dict[str, tuple[Finding, str]],
    surrogates: SurrogateProvider,
    locale: str,
    anchor: str,
    *,
    plan: Plan | None = None,
) -> str:
    """Fill ``templated`` with one replacement per placeholder (the plan's method, else a surrogate).

    Raises:
        MethodError: the plan names a method that cannot run in synthesize mode (e.g. one that
            needs key material).
    """
    ph = PlaceholderText(templated, mapping, {label: 1 for label in mapping})
    return _substitute(templated, _resolve(ph, surrogates, locale, anchor, plan).replacements)


async def rewrite_placeholders(
    templated: str, gateway: LlmGateway, *, temperature: float = REWRITE_TEMPERATURE
) -> str:
    """Ask the model for a different but plausible text; reject it if any placeholder changed."""
    result = await gateway.complete(
        [{"role": "system", "content": REWRITE_SYSTEM}, {"role": "user", "content": templated}],
        temperature=temperature,
        max_tokens=min(REWRITE_MAX_TOKENS, max(256, len(templated))),
        purpose="rewrite",
    )
    if set(PLACEHOLDER_RE.findall(templated)) != set(PLACEHOLDER_RE.findall(result.text)):
        raise LlmRewriteRejected("placeholders_missing")
    return result.text


def _brings_back(
    originals: list[str],
    deterministic: str,
    output: str,
    min_len: int = 4,
    names: list[str] | None = None,
) -> bool:
    """True when ``output`` holds an original that the deterministic fill had removed, or holds
    a part of one of ``names`` more often than the deterministic fill does."""
    det, out = normalise(deterministic), normalise(output)
    for original in originals:
        needle = normalise(original)
        if len(needle) >= min_len and needle in out and needle not in det:
            return True
    return any(out.count(part) > det.count(part) for part in name_parts(names or [], min_len))


async def synthesize_text(
    text: str,
    findings: list[Finding],
    *,
    surrogates: SurrogateProvider,
    locale: str,
    anchor: str,
    gateway: LlmGateway | None,
    llm_rewrite: bool,
    plan: Plan | None = None,
    screen: Callable[[str], int] | None = None,
) -> tuple[str, dict[str, Any]]:
    """The full pipeline; returns ``(output_text, info)`` with ``info`` safe to log.

    ``plan`` (optional) decides each placeholder's fill (``keep`` puts the original back,
    ``redact`` writes the label, ...); without it every value gets a surrogate. ``screen``
    (optional) counts detectable values in a text: a rewrite that scores higher than the
    template it came from invented values and is discarded.

    ``info``: ``llm_calls``, ``llm_used``, ``fallback`` (why the model's text was not used:
    ``leak_guard``, ``placeholders_missing``, ``llm_introduced_entities``, ``verification`` or the
    gateway error type), ``placeholders``, ``methods_used``, ``notes``.

    Raises:
        MethodError: the plan names a method that cannot run in synthesize mode.
    """
    ph = to_placeholders(text, findings)
    fill = _resolve(ph, surrogates, locale, anchor, plan)
    deterministic = _substitute(ph.text, fill.replacements)
    info: dict[str, Any] = {
        "llm_calls": 0,
        "llm_used": False,
        "fallback": None,
        "placeholders": len(ph.mapping),
        "methods_used": dict(fill.methods),
        "notes": dict(fill.notes),
    }
    if not (llm_rewrite and gateway is not None and ph.mapping and gateway.available()):
        return deterministic, info
    originals = [original for _, original in collect_originals(findings, text=text)]
    try:
        assert_placeholder_safe(ph.text, originals)
    except LeakToModelError:
        info["fallback"] = "leak_guard"  # never sent; the verifier rules on the deterministic text
        return deterministic, info
    info["llm_calls"] = 1
    try:
        rewritten = await rewrite_placeholders(ph.text, gateway)
    except LlmRewriteRejected as exc:
        info["fallback"] = str(exc)
        return deterministic, info
    except (LlmCallError, LlmUnavailable) as exc:
        info["fallback"] = type(exc).__name__
        return deterministic, info
    if screen is not None and screen(rewritten) > screen(ph.text):
        info["fallback"] = "llm_introduced_entities"
        return deterministic, info
    output = _substitute(rewritten, fill.replacements)
    if _brings_back(originals, deterministic, output, names=_names(ph)):
        info["fallback"] = "verification"
        return deterministic, info
    info["llm_used"] = True
    return output, info
