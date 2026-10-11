"""The detector ensemble: run the tiers over a JSON document or a text and merge the evidence.

Merging rules (spec §5.3):
* a hit covering the whole value and a structural candidate for the same entity combine by
  noisy-OR; a whole-value hit for a different entity wins over the key's suggestion;
* a structural candidate alone passes only at or above ``structural_only_floor``;
* partial hits inside a longer string become span findings unless a whole-value finding exists;
* findings below ``min_score`` are dropped; one finding per locator.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from dps.engine.detect.base import CustomDetector, NerAdapter
from dps.engine.detect.names import scan_names
from dps.engine.detect.patterns import (
    Hit,
    region_for_locale,
    resolve_overlaps,
    scan_phones,
    scan_text,
)
from dps.engine.detect.structural import KeyCandidate, classify_key, normalize_key
from dps.engine.findings import Finding, Locator, hash_value
from dps.engine.jsonpath import flatten
from dps.engine.taxonomy import Sensitivity, entity_info, record_sensitivity


@dataclass(frozen=True)
class DetectorConfig:
    """Thresholds and switches for one :class:`Detector`."""

    min_score: float = 0.6
    locale: str = "en_US"
    ner_enabled: bool = False
    ner_min_chars: int = 3
    structural_only_floor: float = 0.7


class DetectionResult(BaseModel):
    """Findings for one document or text, plus the record's overall sensitivity."""

    findings: list[Finding]
    record_sensitivity: Sensitivity


def _noisy_or(*scores: float) -> float:
    acc = 1.0
    for s in scores:
        acc *= 1.0 - s
    return round(1.0 - acc, 3)


def _language(locale: str) -> str:
    return locale.split("_")[0].split("-")[0].lower() or "en"


class Detector:
    """Composes T0 (structural), T1 (patterns, phones, name cues), T2 (NER) and T4 (custom)."""

    def __init__(
        self,
        config: DetectorConfig,
        ner: NerAdapter | None = None,
        custom: CustomDetector | None = None,
    ) -> None:
        self.config = config
        self.ner = ner
        self.custom = custom
        self.region = region_for_locale(config.locale)
        self.language = _language(config.locale)

    # -- public -------------------------------------------------------------------------------
    def detect(self, *, data: Any | None = None, text: str | None = None) -> DetectionResult:
        """Detect over a JSON document (``data``) or a text; exactly one must be given."""
        if (data is None) == (text is None):
            raise ValueError("pass exactly one of data or text")
        findings: list[Finding] = []
        if text is not None:
            findings.extend(self._text_findings("$", text, key_hint=None, structural=[]))
        else:
            for path, value in flatten(data):
                findings.extend(self._leaf_findings(path, value))
        findings = self._dedupe(f for f in findings if f.score >= self.config.min_score)
        return DetectionResult(
            findings=findings,
            record_sensitivity=record_sensitivity(f.entity_type for f in findings),
        )

    # -- internals ----------------------------------------------------------------------------
    def _value_hits(self, text: str, key_hint: str | None) -> list[Hit]:
        hits = (
            scan_text(text, key_hint=key_hint)
            + scan_phones(text, region=self.region)
            + scan_names(text)
        )
        if (
            self.ner is not None
            and self.config.ner_enabled
            and len(text) >= self.config.ner_min_chars
        ):
            hits += self.ner.analyze(text, self.language)
        if self.custom is not None:
            hits += self.custom.scan(text, key_hint)
        return resolve_overlaps(hits)

    def _leaf_findings(self, path: str, value: Any) -> list[Finding]:
        if value is None or isinstance(value, bool):
            return []
        key_hint = normalize_key(path)
        structural = classify_key(path)
        if self.custom is not None:
            declared = self.custom.declared(path)
            if declared is not None:
                structural = [declared, *structural]
        return self._text_findings(path, str(value), key_hint=key_hint, structural=structural)

    def _text_findings(
        self, path: str, text: str, *, key_hint: str | None, structural: list[KeyCandidate]
    ) -> list[Finding]:
        hits = self._value_hits(text, key_hint)
        stripped_start = len(text) - len(text.lstrip())
        stripped_end = len(text.rstrip())
        whole_hits = [h for h in hits if h.start <= stripped_start and h.end >= stripped_end]
        partial_hits = [h for h in hits if h not in whole_hits]
        top_key = structural[0] if structural else None
        out: list[Finding] = []

        if whole_hits:
            hit = max(whole_hits, key=lambda h: (h.validated, h.score))
            same_key = next((c for c in structural if c.entity_type == hit.entity_type), None)
            if same_key is not None:
                entity, score = hit.entity_type, _noisy_or(hit.score, same_key.score)
                detectors = [hit.detector, "structural"]
                evidence = [hit.evidence, same_key.evidence]
            elif (
                top_key is not None
                and top_key.score > hit.score
                and top_key.score >= self.config.structural_only_floor
            ):
                entity, score = top_key.entity_type, top_key.score
                detectors, evidence = ["structural"], [top_key.evidence]
            else:
                entity, score = hit.entity_type, hit.score
                detectors, evidence = [hit.detector], [hit.evidence]
                if top_key is not None:
                    evidence.append(f"key-suggested:{top_key.entity_type}")
            out.append(self._finding(path, None, None, entity, score, detectors, evidence, text))
            return out

        if (
            top_key is not None
            and top_key.score >= self.config.structural_only_floor
            and text.strip()
        ):
            out.append(
                self._finding(
                    path,
                    None,
                    None,
                    top_key.entity_type,
                    top_key.score,
                    ["structural"],
                    [top_key.evidence],
                    text,
                )
            )
            return out

        for hit in partial_hits:
            out.append(
                self._finding(
                    path,
                    hit.start,
                    hit.end,
                    hit.entity_type,
                    hit.score,
                    [hit.detector],
                    [hit.evidence],
                    text[hit.start : hit.end],
                )
            )
        return out

    @staticmethod
    def _finding(
        path: str,
        start: int | None,
        end: int | None,
        entity: str,
        score: float,
        detectors: list[str],
        evidence: list[str],
        value: str,
    ) -> Finding:
        return Finding(
            locator=Locator(path=path, start=start, end=end),
            entity_type=entity,
            score=min(1.0, score),
            detectors=detectors,
            sensitivity=entity_info(entity).sensitivity,
            evidence=evidence,
            value_hash=hash_value(value),
        )

    @staticmethod
    def _dedupe(findings: Iterable[Finding]) -> list[Finding]:
        best: dict[tuple[str, int | None, int | None], Finding] = {}
        for f in findings:
            key = (f.locator.path, f.locator.start, f.locator.end)
            if key not in best or f.score > best[key].score:
                best[key] = f
        return sorted(best.values(), key=lambda f: (f.locator.path, f.locator.start or -1))
