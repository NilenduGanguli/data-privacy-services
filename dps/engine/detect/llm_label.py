"""T3 — LLM metadata labelling, opt-in and value-free (spec §5.3, M1 plan Task 19D).

Spec §5.3: "the gateway classifies *field metadata* — names, types, a few de-identified or synthetic
sample values — to resolve semantic types for ambiguous columns; it never receives raw values."
DPS is stricter: it sends value **shapes** (``AB-1234`` → ``XX-9999``, :func:`value_shape`), never a
value. A policy opts in with ``detect.tiers: [..., llm_label]``; Layer 1 labels the JSON leaves
T0–T2 left without a finding (``PrivacyEngine.label_leftovers``); M5 wires the same module into L2
discovery and L3 classification.

Rules (:func:`label_fields`):

- no call when the gateway is missing, unavailable or ``stub`` (deterministic: no verdicts);
- at most ``llm_label_max_fields`` fields per call (the rest are skipped and counted);
- a ``name`` or ``label`` in which the T0/T1 detector finds a whole-value entity, or a ``ref`` in
  which it finds any entity (a value used as a JSON key ends up in the path), is replaced by
  ``field_<n>`` in the prompt and mapped back in the reply;
- the prompt's field list passes M0's :func:`assert_placeholder_safe` against the leaf values
  (``protected``) — on :class:`LeakToModelError` no call is made (counted ``blocked``); the system
  text is fixed and holds entity type names only;
- the reply is read leniently (first ``[`` to last ``]``); a verdict is kept only when its ``ref``
  was sent, its ``entity_type`` is a built-in taxonomy type (never a tenant's custom type: the
  process-wide registry holds every tenant's) and its ``confidence`` is at least
  ``llm_label_min_confidence``; ``LlmUnavailable`` / ``LlmCallError`` → no verdicts.

Nothing is logged but counts (``fields``, ``verdicts``) and the fallback reason.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from pydantic import BaseModel, ConfigDict, Field, field_validator

from dps.config import Settings
from dps.engine.detect.ensemble import Detector, DetectorConfig
from dps.engine.jsonpath import Scalar
from dps.engine.shapes import MAX_SHAPE_CHARS, value_shape
from dps.engine.synth.llm_text import LeakToModelError, assert_placeholder_safe
from dps.engine.taxonomy import TAXONOMY, is_builtin_entity
from dps.llm.gateway import LlmCallError, LlmGateway, LlmUnavailable
from dps.observability import LLM_LABEL_FIELDS, get_logger

logger = get_logger(__name__)

#: The ``purpose`` label of the gateway call (``dps_llm_calls_total``).
LABEL_PURPOSE = "label"
#: The tier name a policy lists in ``detect.tiers`` to opt in.
LLM_LABEL_TIER = "llm_label"
MAX_SHAPES = 3
_INDEX = re.compile(r"\[\d+\]")
#: A field name sent as is: identifier-like (as M1 Task 12's sample keys), no run of four digits.
_PLAIN_NAME = re.compile(r"^(?![^\n]*\d{4})[A-Za-z_][A-Za-z0-9_-]{0,62}$")
#: A quoted path segment (``['…']``): a key that is not an identifier, often data used as a key.
_QUOTED_SEGMENT = re.compile(r"\['")
_LAST_KEY = re.compile(r"(?:\.([A-Za-z_][A-Za-z0-9_]*)|\['((?:[^'\\]|\\.)*)'\])(?:\[\*\])*$")


class FieldMeta(BaseModel):
    """What the model may learn about one field: metadata and value shapes, never a value."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    ref: str  # the caller's handle, echoed in the verdict ("$.loan.ref_cd", "col:3")
    name: str  # key / column / field name; aliased "field_N" when it looks like a value
    data_type: str | None = None  # "string", "number", "VARCHAR2(20)", "input[type=text]"
    shapes: list[str] = Field(default_factory=list, max_length=MAX_SHAPES)
    label: str | None = None  # a human label, already redacted by the caller (L3)
    hints: dict[str, str | int | float | bool] = Field(default_factory=dict)

    @field_validator("shapes")
    @classmethod
    def _short_shapes(cls, shapes: list[str]) -> list[str]:
        if any(len(s) > MAX_SHAPE_CHARS for s in shapes):
            raise ValueError(f"a value shape is at most {MAX_SHAPE_CHARS} characters")
        return shapes


class LabelVerdict(BaseModel):
    """The model's semantic type for one field (by the caller's ``ref``)."""

    ref: str
    entity_type: str
    confidence: float = Field(ge=0.0, le=1.0)


@dataclass
class LabelOutcome:
    """Verdicts plus what it took: the gateway calls made (0 or 1) and the fallback, if any."""

    verdicts: list[LabelVerdict] = field(default_factory=list)
    llm_calls: int = 0
    fallback: str | None = None


def template_of(path: str) -> str:
    """``$.rows[3].code`` → ``$.rows[*].code``: array items share one field."""
    return _INDEX.sub("[*]", path)


def field_name(template: str) -> str:
    """The last key of a path template (``value`` for the document root or a bare array)."""
    match = _LAST_KEY.search(template)
    if match is None:
        return "value"
    ident, quoted = match.groups()
    return ident if ident is not None else quoted.replace("\\'", "'").replace("\\\\", "\\")


def _json_type(values: Sequence[Scalar]) -> str:
    return "string" if any(isinstance(v, str) for v in values) else "number"


def leftover_fields(groups: Mapping[str, Sequence[Scalar]]) -> list[FieldMeta]:
    """One :class:`FieldMeta` per path template: its last key, its JSON type and up to three
    distinct value shapes of its leaves (the values themselves never leave this function)."""
    out: list[FieldMeta] = []
    for template, values in groups.items():
        shapes: list[str] = []
        for value in values:
            shape = value_shape(str(value))
            if shape and shape not in shapes:
                shapes.append(shape)
            if len(shapes) == MAX_SHAPES:
                break
        out.append(
            FieldMeta(
                ref=template,
                name=field_name(template),
                data_type=_json_type(values),
                shapes=shapes,
            )
        )
    return out


def label_types() -> list[str]:
    """The entity types the model may answer with: the built-in taxonomy, sorted."""
    return sorted(name for name in TAXONOMY if is_builtin_entity(name))


def system_prompt() -> str:
    """The fixed instructions (no call data): the task, the answer format and the type list."""
    return (
        "You label data fields for a privacy engine. You receive field metadata only: a ref, a "
        "name, a JSON or column type and up to three value shapes (letters are x or X, digits "
        "are 9, other characters are kept) - never a value. For each field that holds personal "
        "or sensitive data, choose the one entity type from the list below that fits best and "
        "give your confidence between 0 and 1. Leave out fields that hold no sensitive data. "
        'Answer with a JSON array only: [{"ref": "<the field ref>", "entity_type": "<TYPE>", '
        '"confidence": 0.9}].\n'
        "Entity types: " + ", ".join(label_types())
    )


def _finds(detector: Detector, text: str, *, whole: bool) -> bool:
    """True when the detector finds an entity in ``text`` (covering all of it, if ``whole``)."""
    stripped = text.strip()
    if not stripped:
        return False
    for finding in detector.detect(text=stripped).findings:
        start, end = finding.locator.start, finding.locator.end
        if not whole or start is None or (start == 0 and end == len(stripped)):
            return True
    return False


def prompt_fields(
    fields: Sequence[FieldMeta], detector: Detector
) -> tuple[list[dict[str, object]], dict[str, str]]:
    """The fields as the model sees them, and each prompt ref → the caller's ref.

    Replaced by ``field_<n>``: a name that is not identifier-like or in which the detector finds
    a whole value (a person's name or an e-mail address used as a key), a label with a
    whole-value finding, and a ref with a quoted segment or any finding (data used as a key
    upstream ends up in the path) — a field whose name is aliased is sent under the alias as its
    ref too.
    """
    sent: list[dict[str, object]] = []
    refs: dict[str, str] = {}
    aliases = 0
    for meta in fields:
        name_like = not _PLAIN_NAME.fullmatch(meta.name) or _finds(detector, meta.name, whole=True)
        label_like = meta.label is not None and _finds(detector, meta.label, whole=True)
        ref_like = bool(_QUOTED_SEGMENT.search(meta.ref)) or _finds(detector, meta.ref, whole=False)
        alias = ""
        if name_like or label_like or ref_like:
            aliases += 1
            alias = f"field_{aliases}"
        ref = alias if (name_like or ref_like) else meta.ref
        if ref in refs:  # two caller refs never share a prompt ref
            aliases += 1
            ref = f"field_{aliases}"
        refs[ref] = meta.ref
        entry: dict[str, object] = {"ref": ref, "name": alias if name_like else meta.name}
        if meta.data_type is not None:
            entry["data_type"] = meta.data_type
        if meta.shapes:
            entry["shapes"] = list(meta.shapes)
        if meta.label is not None:
            entry["label"] = alias if label_like else meta.label
        if meta.hints:
            entry["hints"] = dict(meta.hints)
        sent.append(entry)
    return sent, refs


def render_prompt(sent: list[dict[str, object]]) -> list[dict[str, str]]:
    """The only messages the gateway receives for a labelling call."""
    body = json.dumps(sent, separators=(",", ":"), sort_keys=True, ensure_ascii=False)
    return [{"role": "system", "content": system_prompt()}, {"role": "user", "content": body}]


def parse_verdicts(text: str, refs: Mapping[str, str], min_confidence: float) -> list[LabelVerdict]:
    """Verdicts from the model's reply, read leniently; anything malformed is dropped."""
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end < start:
        return []
    try:
        raw = json.loads(text[start : end + 1])
    except (ValueError, RecursionError):
        return []
    if not isinstance(raw, list):
        return []
    best: dict[str, LabelVerdict] = {}
    for item in raw:
        if not isinstance(item, dict):
            continue
        ref, entity, confidence = item.get("ref"), item.get("entity_type"), item.get("confidence")
        if not isinstance(ref, str) or ref not in refs:
            continue
        if not isinstance(entity, str) or not is_builtin_entity(entity):
            continue
        if isinstance(confidence, bool) or not isinstance(confidence, int | float):
            continue
        value = float(confidence)
        if not math.isfinite(value) or not 0.0 <= value <= 1.0 or value < min_confidence:
            continue
        caller_ref = refs[ref]
        current = best.get(caller_ref)
        if current is None or value > current.confidence:
            best[caller_ref] = LabelVerdict(ref=caller_ref, entity_type=entity, confidence=value)
    return list(best.values())


def gateway_usable(gateway: LlmGateway | None) -> bool:
    """A real, configured provider (never ``stub``: labelling must stay deterministic offline)."""
    return gateway is not None and gateway.provider not in ("", "stub") and gateway.available()


async def label(
    fields: Sequence[FieldMeta],
    *,
    gateway: LlmGateway | None,
    settings: Settings,
    protected: Sequence[str] = (),
) -> LabelOutcome:
    """:func:`label_fields` with what it took (gateway calls, fallback) for the engine's report."""
    if not fields or not gateway_usable(gateway):
        return LabelOutcome()
    assert gateway is not None
    sent_fields = list(fields[: settings.llm_label_max_fields])
    skipped = len(fields) - len(sent_fields)
    if skipped:
        LLM_LABEL_FIELDS.labels("skipped").inc(skipped)
    detector = Detector(DetectorConfig(locale=settings.default_locale))
    sent, refs = prompt_fields(sent_fields, detector)
    messages = render_prompt(sent)
    try:
        assert_placeholder_safe(messages[1]["content"], [str(p) for p in protected])
    except LeakToModelError:
        LLM_LABEL_FIELDS.labels("blocked").inc(len(sent))
        logger.warning("llm.label", fields=len(sent), verdicts=0, fallback="leak_guard")
        return LabelOutcome(fallback="leak_guard")
    try:
        reply = await gateway.complete(
            messages, temperature=0.0, max_tokens=2048, purpose=LABEL_PURPOSE
        )
    except (LlmUnavailable, LlmCallError) as exc:
        LLM_LABEL_FIELDS.labels("failed").inc(len(sent))
        logger.warning("llm.label", fields=len(sent), verdicts=0, fallback=type(exc).__name__)
        return LabelOutcome(llm_calls=1, fallback=type(exc).__name__)
    verdicts = parse_verdicts(reply.text, refs, settings.llm_label_min_confidence)
    LLM_LABEL_FIELDS.labels("labelled").inc(len(verdicts))
    LLM_LABEL_FIELDS.labels("unlabelled").inc(len(sent) - len(verdicts))
    logger.info("llm.label", fields=len(sent), verdicts=len(verdicts))
    return LabelOutcome(verdicts=verdicts, llm_calls=1)


async def label_fields(
    fields: list[FieldMeta],
    *,
    gateway: LlmGateway | None,
    settings: Settings,
    protected: Sequence[str] = (),
) -> list[LabelVerdict]:
    """Semantic types for ``fields`` from the LLM gateway, value-free (see the module rules).

    ``protected`` are the values the fields were taken from: the prompt is checked against them
    before any call.
    """
    outcome = await label(fields, gateway=gateway, settings=settings, protected=protected)
    return outcome.verdicts
