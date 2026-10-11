"""Tier T2 — Presidio NER (spaCy ``en``/``es``) mapped onto the DPS taxonomy.

Optional: the ``[ner]`` extra and a spaCy model. Everything is lazy and guarded so the engine
imports cleanly without them; a missing or failing engine yields no hits, never an exception.
Presidio is only asked for the entities the pattern catalogue cannot find (names, places,
nationalities, free-form dates); identifiers with checksums stay with tier T1.
"""

from __future__ import annotations

import importlib.util
import threading
from typing import Any

from dps.engine.detect.patterns import Hit
from dps.observability import get_logger

logger = get_logger(__name__)

PRESIDIO_TO_DPS: dict[str, str] = {
    "PERSON": "PERSON_NAME",
    "LOCATION": "LOCATION",
    "NRP": "NATIONALITY",
    "DATE_TIME": "DATE",
    "EMAIL_ADDRESS": "EMAIL",
    "PHONE_NUMBER": "PHONE",
    "CREDIT_CARD": "CARD_PAN",
    "IBAN_CODE": "IBAN",
    "US_SSN": "SSN",
    "US_ITIN": "ITIN",
    "US_PASSPORT": "PASSPORT",
    "US_DRIVER_LICENSE": "DRIVER_LICENSE",
    "IP_ADDRESS": "IP_ADDRESS",
    "URL": "URL",
    "CRYPTO": "CRYPTO_ADDRESS",
    "UK_NHS": "NHS_NUMBER",
    "IN_AADHAAR": "AADHAAR",
    "IN_PAN": "PAN_IN",
    "IN_PASSPORT": "PASSPORT",
    "IN_VOTER": "VOTER_ID",
    "ORGANIZATION": "ORG_NAME",
}
#: What we ask Presidio for — the recall-only entities.
NER_ENTITIES: tuple[str, ...] = ("PERSON", "LOCATION", "NRP", "DATE_TIME")
_MODEL_CANDIDATES = {
    "en": ("en_core_web_lg", "en_core_web_md", "en_core_web_sm"),
    "es": ("es_core_news_lg", "es_core_news_md", "es_core_news_sm"),
}


def presidio_available() -> bool:
    """True when ``presidio_analyzer`` is importable (the ``[ner]`` extra is installed)."""
    return importlib.util.find_spec("presidio_analyzer") is not None


def _installed_model(lang: str) -> str | None:
    import spacy.util  # lazy; only reached when presidio is importable

    for name in _MODEL_CANDIDATES.get(lang, ()):
        if spacy.util.is_package(name):
            return name
    return None


class PresidioNer:
    """Lazily built, thread-safe Presidio analyzer."""

    def __init__(
        self, languages: tuple[str, ...] = ("en", "es"), score_threshold: float = 0.4
    ) -> None:
        self.languages = languages
        self.score_threshold = score_threshold
        self._lock = threading.Lock()
        self._analyzer: Any | None = None
        self._loaded: tuple[str, ...] = ()
        self._build_failed = False
        self._warned = False

    def _build(self) -> tuple[Any, tuple[str, ...]]:
        """Build the analyzer over every language that has a spaCy model installed."""
        from presidio_analyzer import AnalyzerEngine
        from presidio_analyzer.nlp_engine import NlpEngineProvider

        models = []
        for lang in self.languages:
            model = _installed_model(lang)
            if model:
                models.append({"lang_code": lang, "model_name": model})
        if not models:
            raise RuntimeError("no spaCy model installed for " + ",".join(self.languages))
        provider = NlpEngineProvider(
            nlp_configuration={"nlp_engine_name": "spacy", "models": models}
        )
        loaded = tuple(m["lang_code"] for m in models)
        analyzer = AnalyzerEngine(
            nlp_engine=provider.create_engine(), supported_languages=list(loaded)
        )
        return analyzer, loaded

    @property
    def available(self) -> bool:
        """False once building the analyzer failed (no spaCy model, broken install)."""
        return not self._build_failed

    def _engine(self) -> Any:
        """The shared analyzer; a failed build is remembered, not retried for every value."""
        with self._lock:
            if self._analyzer is None:
                if self._build_failed:
                    raise RuntimeError("NER engine unavailable")
                try:
                    self._analyzer, self._loaded = self._build()
                except Exception:
                    self._build_failed = True
                    raise
            return self._analyzer

    def analyze(self, text: str, language: str) -> list[Hit]:
        """Catalogue-style hits for ``text``; empty on any engine problem.

        ``language`` falls back to the first language with a loaded model, so Spanish text is
        still read (by the English model) when only ``en`` is installed.
        """
        try:
            engine = self._engine()
            loaded = self._loaded or self.languages
            lang = language if language in loaded else loaded[0]
            results = engine.analyze(
                text=text,
                language=lang,
                entities=list(NER_ENTITIES),
                score_threshold=self.score_threshold,
            )
        except Exception as exc:  # noqa: BLE001 - NER is recall-only, never load-bearing
            if not self._warned:
                logger.warning("ner_unavailable", error=type(exc).__name__)
                self._warned = True
            return []
        hits: list[Hit] = []
        for r in results:
            entity = PRESIDIO_TO_DPS.get(r.entity_type)
            if entity is None:
                continue
            hits.append(
                Hit(
                    entity,
                    r.start,
                    r.end,
                    round(float(r.score), 3),
                    "ner:presidio",
                    f"ner:{r.entity_type}:{float(r.score):.2f}",
                )
            )
        return hits


def build_ner(languages: tuple[str, ...], enabled: bool | None) -> PresidioNer | None:
    """``enabled``: False → none; True → Presidio or raise; None → Presidio when importable."""
    if enabled is False:
        return None
    if presidio_available():
        return PresidioNer(languages=languages)
    if enabled is True:
        raise RuntimeError(
            "DPS_NER_ENABLED=true but presidio-analyzer is not installed (pip install '.[ner]')"
        )
    return None
