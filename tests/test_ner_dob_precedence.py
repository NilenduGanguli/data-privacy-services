"""S3 review F-04: a stronger detector must never weaken protection. Presidio reads a date of birth
as ``DATE_TIME`` (→ ``DATE``, LOW, planned ``keep``) with a higher score and a longer span than the
pattern tier's ``DATE_OF_BIRTH`` (MEDIUM), and ``resolve_overlaps`` used to keep the NER hit — the
birth date came back in clear in 3 of 4 phrasings with NER on. An NER hit that overlaps a typed
(pattern, checksum, custom) hit of a higher tier is dropped before the overlap ranking."""

from __future__ import annotations

import re

import pytest

from dps.config import Settings
from dps.engine.detect.patterns import Hit, resolve_overlaps
from dps.engine.service import Payload, PrivacyEngine, RequestOptions

PHRASINGS = [
    "Customer Quixote Vanderquack, date of birth 14/03/1971, account 4471.",
    "Customer Quixote Vanderquack, DOB: 14/03/1971, account 4471.",
    "Customer Quixote Vanderquack was born on 14 March 1971.",
    "Customer Quixote Vanderquack, birthday 14/03/1971.",
]
_DATE = re.compile(r"14/03/1971|14 March 1971")


class _PresidioLikeNer:
    """What Presidio answers for these texts: the date as DATE_TIME, a longer span (it takes the
    cue word too) and a higher score than the pattern tier's birth date."""

    languages = ("en",)

    def analyze(self, text: str, language: str) -> list[Hit]:
        m = _DATE.search(text)
        assert m is not None
        start = max(0, text.rfind(" ", 0, m.start() - 1) + 1)  # the word before the date too
        return [Hit("DATE", start, m.end(), 0.85, "ner:presidio", "ner:DATE_TIME:0.85")]


def test_resolve_overlaps_keeps_the_more_sensitive_typed_hit() -> None:
    dob = Hit("DATE_OF_BIRTH", 20, 30, 0.75, "pattern:date_slash", "pattern")
    ner_date = Hit("DATE", 14, 30, 0.85, "ner:presidio", "ner:DATE_TIME:0.85")
    assert resolve_overlaps([ner_date, dob]) == [dob]
    # an NER hit of the same or a higher tier still competes on score and length as before
    ner_person = Hit("PERSON_NAME", 0, 12, 0.85, "ner:presidio", "ner:PERSON:0.85")
    cue_person = Hit("PERSON_NAME", 0, 8, 0.6, "names:cue", "names")
    assert resolve_overlaps([cue_person, ner_person]) == [ner_person]
    # a typed hit of a lower tier is not preferred: the overlap ranking decides, as before
    city = Hit("CITY", 0, 6, 0.9, "pattern:city", "pattern")
    ner_name = Hit("PERSON_NAME", 0, 6, 0.85, "ner:presidio", "ner:PERSON:0.85")
    assert resolve_overlaps([city, ner_name]) == [city]


@pytest.mark.parametrize("text", PHRASINGS)
def test_with_ner_on_a_birth_date_stays_a_birth_date_and_is_never_kept(text: str) -> None:
    engine = PrivacyEngine(
        Settings(_env_file=None, env="dev", ner_enabled=False, llm_provider="stub")
    )
    engine.ner = _PresidioLikeNer()  # type: ignore[assignment]
    out = engine.obfuscate(Payload(text=text), "gdpr-pseudonymise", RequestOptions())
    types = {f.entity_type for f in out.findings}
    assert "DATE_OF_BIRTH" in types and "DATE" not in types
    original = _DATE.search(text)
    assert original is not None
    result = out.result if isinstance(out.result, str) else str(out.result)
    assert original.group(0) not in result  # source vs output: the birth date did not survive
