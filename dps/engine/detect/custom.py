"""Tier T4 — what the client declares: fields by path and custom recognizers.

Recognizers use Presidio's YAML recognizer shape (``name``, ``supported_entity``, ``patterns``,
``deny_list``, ``context``) so the files DES already maintains load unchanged; an optional
``sensitivity`` registers the custom entity's tier in the taxonomy (custom names only — the
taxonomy owns the tiers of built-in types, so ``sensitivity`` on one is refused at validation).
``allow_list`` is a DPS extension (M1): exact matched texts that are never reported, such as seeded
test fixtures. Presidio's ``deny_list`` keeps its meaning — terms that are always reported.

Deny-list entries and patterns are often the sensitive terms themselves, so validation errors
never echo the input (``hide_input_in_errors``) and a bad regex is reported without its text.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from dps.engine.detect.patterns import CONTEXT_BONUS, CONTEXT_WINDOW, Hit
from dps.engine.detect.regex_guard import UnsafePattern, check_pattern
from dps.engine.detect.structural import KeyCandidate
from dps.engine.taxonomy import EntityInfo, Family, Sensitivity, is_builtin_entity, register_entity

_ENTITY = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
DENY_LIST_SCORE = 0.9


def _check_entity_name(value: str) -> str:
    if not _ENTITY.match(value):
        # never the value: a customer value pasted into the wrong field must not come back
        raise ValueError(f"entity types must match {_ENTITY.pattern}")
    return value


def _check_custom_tier(entity: str, sensitivity: Sensitivity | None) -> None:
    if sensitivity is not None and is_builtin_entity(entity):
        raise ValueError(
            f"sensitivity cannot be set for built-in entity type {entity!r}; "
            "the taxonomy owns built-in tiers"
        )


class PatternSpecModel(BaseModel):
    """One regex of a custom recognizer (group 0 is the value)."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    name: str
    regex: str
    score: float = Field(ge=0.0, le=1.0)

    @field_validator("regex")
    @classmethod
    def _compiles(cls, value: str) -> str:
        try:
            re.compile(value)
        except re.error as exc:
            raise ValueError(f"invalid regex: {exc}") from None
        try:
            check_pattern(value)  # S3: no catastrophic backtracking on caller text (ReDoS)
        except UnsafePattern as exc:
            raise ValueError(f"unsafe regex: {exc}") from None
        return value


class RecognizerSpec(BaseModel):
    """A custom recognizer in Presidio's YAML shape, plus an optional tier for a custom type."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    name: str
    supported_entity: str
    supported_language: str = "en"
    patterns: tuple[PatternSpecModel, ...] = Field(default=(), max_length=20)
    deny_list: tuple[str, ...] = Field(default=(), max_length=1000)
    context: tuple[str, ...] = Field(default=(), max_length=50)
    sensitivity: Sensitivity | None = None
    #: Exact matched texts this recognizer never reports (pattern and deny-list hits alike).
    allow_list: tuple[str, ...] = Field(default=(), max_length=1000)

    @field_validator("supported_entity")
    @classmethod
    def _entity_name(cls, value: str) -> str:
        return _check_entity_name(value)

    @field_validator("deny_list", "allow_list")
    @classmethod
    def _no_blank_terms(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not term.strip() for term in value):
            raise ValueError("list entries must be non-blank")
        return value

    @model_validator(mode="after")
    def _has_matcher(self) -> RecognizerSpec:
        if not self.patterns and not self.deny_list:
            raise ValueError("a recognizer needs patterns or a deny_list")
        _check_custom_tier(self.supported_entity, self.sensitivity)
        return self


class DeclaredField(BaseModel):
    """A field the owner names explicitly by JSON path.

    ``path`` is an exact path (``$.loan.internal_score``) or a glob in the same syntax:
    ``[*]`` matches any array index and ``*`` matches any run of characters inside one
    dotted name (``$.*.tax_ref`` matches ``$.customer.tax_ref``, not ``$.a.b.tax_ref``).
    """

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    path: str
    entity_type: str
    score: float = Field(default=0.99, ge=0.0, le=1.0)
    sensitivity: Sensitivity | None = None

    @field_validator("entity_type")
    @classmethod
    def _entity_name(cls, value: str) -> str:
        return _check_entity_name(value)

    @model_validator(mode="after")
    def _custom_tier_only(self) -> DeclaredField:
        _check_custom_tier(self.entity_type, self.sensitivity)
        return self


def path_glob(pattern: str) -> re.Pattern[str]:
    """Compile a declared-field path glob (see :class:`DeclaredField`) for ``fullmatch``."""
    out: list[str] = []
    i = 0
    while i < len(pattern):
        if pattern.startswith("[*]", i):
            out.append(r"\[[0-9]+\]")
            i += 3
        elif pattern[i] == "*":
            out.append(r"[^.\[\]]*")
            i += 1
            while i < len(pattern) and pattern[i] == "*":  # ``**`` is ``*``: no stacked repeats
                i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out))


def load_recognizers_yaml(source: str | Path) -> list[RecognizerSpec]:
    """Read ``recognizers: [...]`` (Presidio's shape) from a YAML file."""
    data = yaml.safe_load(Path(source).read_text(encoding="utf-8")) or {}
    entries = data.get("recognizers") if isinstance(data, dict) else data
    if not isinstance(entries, list):
        raise ValueError(f"{source}: expected a 'recognizers' list")
    return [RecognizerSpec.model_validate(e) for e in entries]


class PolicyCustomDetector:
    """Implements :class:`dps.engine.detect.base.CustomDetector` from policy declarations."""

    def __init__(self, recognizers: list[RecognizerSpec], declared: list[DeclaredField]) -> None:
        self._declared = [(d, path_glob(d.path)) for d in declared]
        # each matcher carries its recognizer's allow-list (per recognizer, not per name: two
        # recognizers may share a name)
        self._patterns: list[
            tuple[RecognizerSpec, PatternSpecModel, re.Pattern[str], frozenset[str]]
        ] = []
        self._deny: list[tuple[RecognizerSpec, re.Pattern[str], frozenset[str]]] = []
        for rec in recognizers:
            allowed = frozenset(rec.allow_list)
            if rec.sensitivity is not None:
                register_entity(
                    EntityInfo(
                        rec.supported_entity, Family.OTHER, rec.sensitivity, f"custom:{rec.name}"
                    )
                )
            for pat in rec.patterns:
                self._patterns.append((rec, pat, re.compile(pat.regex), allowed))
            if rec.deny_list:
                alternation = "|".join(
                    re.escape(w) for w in sorted(rec.deny_list, key=len, reverse=True)
                )
                self._deny.append(
                    (rec, re.compile(rf"(?<!\w)(?:{alternation})(?!\w)", re.IGNORECASE), allowed)
                )
        for d in declared:
            if d.sensitivity is not None:
                register_entity(EntityInfo(d.entity_type, Family.OTHER, d.sensitivity, "declared"))

    @property
    def text_entities(self) -> frozenset[str]:
        """The types its patterns and deny lists can find inside free text."""
        return frozenset(r.supported_entity for r, *_ in self._patterns) | frozenset(
            r.supported_entity for r, *_ in self._deny
        )

    def declared(self, path: str) -> KeyCandidate | None:
        """The first declaration whose path equals or glob-matches ``path``."""
        for spec, glob in self._declared:
            if path == spec.path or glob.fullmatch(path):
                return KeyCandidate(spec.entity_type, spec.score, f"declared:{spec.path}")
        return None

    def scan(self, text: str, key_hint: str | None) -> list[Hit]:
        """Custom-pattern and deny-list hits in ``text``; empty matches are ignored."""
        hits: list[Hit] = []
        for rec, pat, regex, allowed in self._patterns:
            for m in regex.finditer(text):
                if m.end() == m.start() or m.group(0) in allowed:
                    continue
                score = pat.score
                before = text[max(0, m.start() - CONTEXT_WINDOW) : m.start()]
                window = f"{before} {key_hint or ''}".lower()
                if any(w.lower() in window for w in rec.context):
                    score = min(1.0, score + CONTEXT_BONUS)
                hits.append(
                    Hit(
                        rec.supported_entity,
                        m.start(),
                        m.end(),
                        round(score, 3),
                        f"custom:{rec.name}",
                        f"custom-pattern:{pat.name}",
                    )
                )
        for rec, regex, allowed in self._deny:
            for m in regex.finditer(text):
                if m.group(0) in allowed:
                    continue
                hits.append(
                    Hit(
                        rec.supported_entity,
                        m.start(),
                        m.end(),
                        DENY_LIST_SCORE,
                        f"custom:{rec.name}",
                        "deny-list",
                    )
                )
        return hits


def custom_detector_from(
    recognizers: list[Any], declared: list[Any]
) -> PolicyCustomDetector | None:
    """``None`` when the policy declares nothing (so the ensemble skips the tier)."""
    if not recognizers and not declared:
        return None
    return PolicyCustomDetector(
        [
            r if isinstance(r, RecognizerSpec) else RecognizerSpec.model_validate(r)
            for r in recognizers
        ],
        [d if isinstance(d, DeclaredField) else DeclaredField.model_validate(d) for d in declared],
    )
