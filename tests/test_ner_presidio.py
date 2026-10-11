from __future__ import annotations

import pytest

from dps.engine.detect.ner_presidio import (
    PRESIDIO_TO_DPS,
    PresidioNer,
    build_ner,
    presidio_available,
)


def test_mapping_covers_the_entities_we_request() -> None:
    for name in ("PERSON", "LOCATION", "NRP", "DATE_TIME"):
        assert name in PRESIDIO_TO_DPS
    assert PRESIDIO_TO_DPS["PERSON"] == "PERSON_NAME"


def test_build_ner_respects_the_switch() -> None:
    assert build_ner(("en",), enabled=False) is None
    auto = build_ner(("en",), enabled=None)
    assert (auto is None) is (not presidio_available())
    if not presidio_available():
        with pytest.raises(RuntimeError):
            build_ner(("en",), enabled=True)


def test_engine_errors_never_propagate(monkeypatch: pytest.MonkeyPatch) -> None:
    ner = PresidioNer(languages=("en",))
    monkeypatch.setattr(ner, "_engine", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    assert ner.analyze("Ann Smith", "en") == []


@pytest.mark.ner
def test_real_presidio_finds_a_person() -> None:
    pytest.importorskip("presidio_analyzer")
    spacy = pytest.importorskip("spacy")
    if not spacy.util.is_package("en_core_web_lg") and not spacy.util.is_package("en_core_web_sm"):
        pytest.skip("no spaCy English model installed")
    hits = PresidioNer(languages=("en",)).analyze(
        "My name is John Smith and I live in Seattle.", "en"
    )
    assert any(h.entity_type == "PERSON_NAME" for h in hits)


class _Result:
    def __init__(self, entity_type: str, start: int, end: int, score: float) -> None:
        self.entity_type, self.start, self.end, self.score = entity_type, start, end, score


class _FakeAnalyzer:
    def __init__(self) -> None:
        self.languages: list[str] = []

    def analyze(
        self, *, text: str, language: str, entities: list[str], score_threshold: float
    ) -> list[_Result]:
        self.languages.append(language)
        i = text.find("Ann Smith")
        return (
            [_Result("PERSON", i, i + 9, 0.85), _Result("SOMETHING_ELSE", 0, 1, 0.9)]
            if i >= 0
            else []
        )


def test_build_failure_is_cached_and_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    ner = PresidioNer(languages=("en",))
    calls: list[int] = []

    def broken() -> tuple[object, tuple[str, ...]]:
        calls.append(1)
        raise RuntimeError("no model")

    monkeypatch.setattr(ner, "_build", broken)
    assert ner.analyze("Ann Smith", "en") == [] and ner.analyze("Ann Smith", "en") == []
    assert len(calls) == 1  # a missing model is not retried per leaf


def test_language_falls_back_to_a_loaded_model(monkeypatch: pytest.MonkeyPatch) -> None:
    ner = PresidioNer(languages=("en", "es"))
    fake = _FakeAnalyzer()
    monkeypatch.setattr(ner, "_build", lambda: (fake, ("en",)))
    hits = ner.analyze("Hola, soy Ann Smith", "es")
    assert fake.languages == ["en"]  # no Spanish model loaded: use the one we have
    assert [(h.entity_type, h.start, h.end, h.detector) for h in hits] == [
        ("PERSON_NAME", 10, 19, "ner:presidio")
    ]
    assert "Ann" not in hits[0].evidence and hits[0].validated is False
