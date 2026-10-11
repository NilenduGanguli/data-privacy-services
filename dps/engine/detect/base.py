"""Protocols the ensemble composes: NER (T2) and custom detectors (T4)."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from dps.engine.detect.patterns import Hit
from dps.engine.detect.structural import KeyCandidate


@runtime_checkable
class NerAdapter(Protocol):
    """Named-entity recognition over free text, returning catalogue-style hits."""

    languages: tuple[str, ...]

    def analyze(self, text: str, language: str) -> list[Hit]: ...


@runtime_checkable
class CustomDetector(Protocol):
    """Client-declared fields and custom recognizers from the policy."""

    def declared(self, path: str) -> KeyCandidate | None: ...

    def scan(self, text: str, key_hint: str | None) -> list[Hit]: ...
