"""Tier T4 — what the client declares: fields by path and custom recognizers.

Recognizers use Presidio's YAML recognizer shape (``name``, ``supported_entity``, ``patterns``,
``deny_list``, ``context``) so the files DES already maintains load unchanged; an optional
``sensitivity`` registers the custom entity's tier in the taxonomy (custom names only — the
taxonomy owns the tiers of built-in types, so ``sensitivity`` on one is refused at validation).
``allow_list`` is a DPS extension (M1): exact matched texts that are never reported, such as seeded
test fixtures. Presidio's ``deny_list`` keeps its meaning — terms that are always reported.

Deny-list entries and patterns are often the sensitive terms themselves, so validation errors
never echo the input (``hide_input_in_errors``) and a bad regex is reported without its text.

Patterns and deny lists run on the ``regex`` module under the call's CPU budget
(:mod:`dps.engine.detect.regex_guard`, S3 review round 1 B1): a pattern that backtracks past it
refuses the call (``custom_pattern_too_slow``) instead of pinning a CPU. Declared-field globs are
matched without any regex (:class:`PathGlob`): linear in the path whatever the stars.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import regex
import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from dps.engine.detect.patterns import CONTEXT_BONUS, CONTEXT_WINDOW, Hit
from dps.engine.detect.regex_guard import (
    PatternBudget,
    UnsafePattern,
    call_budget,
    check_pattern,
    compile_policy_pattern,
)
from dps.engine.detect.structural import KeyCandidate
from dps.engine.taxonomy import (
    EntityInfo,
    Family,
    Sensitivity,
    current_custom_entities,
    is_builtin_entity,
    register_entity,
)

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


#: The characters that end a name in a path; a glob ``*`` never crosses one.
_PATH_SEPARATORS = ".[]"
_SPLIT_PATH = re.compile(r"([.\[\]])")  # one character class: linear
#: The name between the brackets of ``[*]``: an array index, ``[0-9]+``.
_INDEX = "[*]"


def split_path(path: str) -> list[str]:
    """``path`` cut at ``.``, ``[`` and ``]``: names at even positions, separators at odd ones."""
    return _SPLIT_PATH.split(path)


def _name_matches(chunks: tuple[str, ...], name: str) -> bool:
    """``name`` against one glob name cut at its ``*`` (``chunks``): the first chunk starts it,
    the last ends it, and the others follow in order in between — leftmost placement is optimal,
    so no backtracking (S3 review round 1 B1)."""
    if len(chunks) == 1:
        return name == chunks[0]
    first, last = chunks[0], chunks[-1]
    if len(name) < len(first) + len(last) or not name.startswith(first):
        return False
    if not name.endswith(last):
        return False
    pos, end = len(first), len(name) - len(last)
    for chunk in chunks[1:-1]:
        found = name.find(chunk, pos, end)
        if found < 0:
            return False
        pos = found + len(chunk)
    return True


class PathGlob:
    """A declared-field path glob (see :class:`DeclaredField`), matched without a regex.

    The glob and the path are cut at ``.``, ``[`` and ``]``; separators must be equal and in the
    same places (``*`` never crosses one, and ``[*]`` is ``[`` + an index + ``]``), and each name is
    matched on its own by :func:`_name_matches`. The cost is linear in the path, whatever the
    number or the arrangement of the stars (S3 review round 1 B1: the regex form backtracked
    polynomially on ``$.*a*a*a*…b`` against a long name).
    """

    __slots__ = ("pattern", "_parts")

    def __init__(self, pattern: str) -> None:
        self.pattern = pattern
        parts: list[str | tuple[str, ...] | None] = []  # None: an index name (``[*]``)
        name: list[str] = []
        i = 0
        while i < len(pattern):
            if pattern.startswith(_INDEX, i):
                parts += [tuple("".join(name).split("*")), "[", None, "]"]
                name = []
                i += len(_INDEX)
            elif pattern[i] in _PATH_SEPARATORS:
                parts += [tuple("".join(name).split("*")), pattern[i]]
                name = []
                i += 1
            else:
                name.append(pattern[i])
                i += 1
        parts.append(tuple("".join(name).split("*")))
        self._parts = tuple(parts)

    def match_parts(self, parts: list[str]) -> bool:
        """True when the path cut by :func:`split_path` matches the whole glob."""
        if len(parts) != len(self._parts):
            return False
        for index, (want, have) in enumerate(zip(self._parts, parts, strict=True)):
            if index % 2:  # a separator
                if want != have:
                    return False
            elif want is None:  # ``[*]``: one or more ASCII digits
                if not (have.isascii() and have.isdigit()):
                    return False
            elif not _name_matches(want, have):  # type: ignore[arg-type]
                return False
        return True

    def fullmatch(self, path: str) -> bool:
        """True when ``path`` matches the whole glob."""
        return self.match_parts(split_path(path))


def path_glob(pattern: str) -> PathGlob:
    """Compile a declared-field path glob (see :class:`DeclaredField`)."""
    return PathGlob(pattern)


_UNREGISTERED = object()


class CustomTiers:
    """The tiers a policy's custom recognizers and declared fields declare (``sensitivity``),
    registered in every call that uses them (S3 review round 1 B3: a call's registry is its own).

    :meth:`ensure` registers them in the current call's registry the first time it runs there; an
    object built in one call and used in another (a worker caches a job's transformer across its
    partition handlers) registers them again in each.
    """

    __slots__ = ("_infos", "_registered_in")

    def __init__(self, recognizers: list[Any], declared: list[Any]) -> None:
        infos: list[EntityInfo] = []
        for rec in recognizers:
            spec = rec if isinstance(rec, RecognizerSpec) else RecognizerSpec.model_validate(rec)
            if spec.sensitivity is not None:
                infos.append(
                    EntityInfo(
                        spec.supported_entity, Family.OTHER, spec.sensitivity, f"custom:{spec.name}"
                    )
                )
        for item in declared:
            field = item if isinstance(item, DeclaredField) else DeclaredField.model_validate(item)
            if field.sensitivity is not None:
                infos.append(
                    EntityInfo(field.entity_type, Family.OTHER, field.sensitivity, "declared")
                )
        self._infos = tuple(infos)
        self._registered_in: object = _UNREGISTERED

    def ensure(self) -> None:
        """Register the tiers in the current call (once per call).

        Raises:
            ValueError: a registry is full (:data:`dps.engine.taxonomy.MAX_CUSTOM_ENTITIES`).
        """
        registry = current_custom_entities()
        if registry is self._registered_in or not self._infos:
            return
        for info in self._infos:
            register_entity(info)
        self._registered_in = registry


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
        #: this detector's own budget, used only outside a request or a worker handler (a call
        #: binds one budget for all its detectors: :mod:`dps.engine.call_scope`)
        self._budget = PatternBudget()
        # each matcher carries its recognizer's allow-list (per recognizer, not per name: two
        # recognizers may share a name)
        self._patterns: list[
            tuple[RecognizerSpec, PatternSpecModel, regex.Pattern[str], frozenset[str]]
        ] = []
        self._deny: list[tuple[RecognizerSpec, regex.Pattern[str], frozenset[str]]] = []
        #: the tiers this policy declares, in this call now and in any later call that uses it
        self._tiers = CustomTiers(recognizers, declared)
        self._tiers.ensure()
        for rec in recognizers:
            allowed = frozenset(rec.allow_list)
            for pat in rec.patterns:
                self._patterns.append((rec, pat, compile_policy_pattern(pat.regex), allowed))
            if rec.deny_list:
                alternation = "|".join(
                    re.escape(w) for w in sorted(rec.deny_list, key=len, reverse=True)
                )
                deny = rf"(?<!\w)(?:{alternation})(?!\w)"
                self._deny.append((rec, compile_policy_pattern(deny, regex.IGNORECASE), allowed))

    @property
    def text_entities(self) -> frozenset[str]:
        """The types its patterns and deny lists can find inside free text."""
        return frozenset(r.supported_entity for r, *_ in self._patterns) | frozenset(
            r.supported_entity for r, *_ in self._deny
        )

    def declared(self, path: str) -> KeyCandidate | None:
        """The first declaration whose path equals or glob-matches ``path``."""
        self._tiers.ensure()
        parts: list[str] | None = None
        for spec, glob in self._declared:
            if path != spec.path:
                parts = parts if parts is not None else split_path(path)
                if not glob.match_parts(parts):
                    continue
            return KeyCandidate(spec.entity_type, spec.score, f"declared:{spec.path}")
        return None

    def scan(self, text: str, key_hint: str | None) -> list[Hit]:
        """Custom-pattern and deny-list hits in ``text``; empty matches are ignored.

        Raises:
            PatternTooSlow: a pattern ran past the call's time budget (nothing is skipped).
        """
        self._tiers.ensure()
        budget = call_budget() or self._budget
        budget.earn(len(text), len(self._patterns) + len(self._deny))
        hits: list[Hit] = []
        for rec, pat, compiled, allowed in self._patterns:
            for m in budget.run(compiled, text):
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
        for rec, compiled, allowed in self._deny:
            for m in budget.run(compiled, text):
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
