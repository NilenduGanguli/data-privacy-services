"""Person names in free text found by the cue next to them (tier T1, no model needed).

The M0 Playground's text sample ("Ananya Krishnan (ananya.k@…, +91 …) asked …") left the name in
clear with NER off, and en_core_web_sm labels it LOCATION with NER on (M0 verification round 1,
B1). These cues find it offline, and win over a weaker NER label for the same span.
"""

from __future__ import annotations

import pytest

from dps.engine.detect.ensemble import Detector, DetectorConfig
from dps.engine.detect.names import scan_names, title_gender
from dps.engine.detect.patterns import Hit

FOUND = [
    # contact details right after the name
    (
        "Ananya Krishnan (ananya.k@example-mail.in, +91 98451 20377) asked to update her Aadhaar.",
        "Ananya Krishnan",
        "cue:contact",
    ),
    (
        "Write to Ananya Krishnan <ananya.k@example-mail.in> today.",
        "Ananya Krishnan",
        "cue:contact",
    ),
    ("Escalated by Rahul Mehta, +91 98451 20377, on Monday.", "Rahul Mehta", "cue:contact"),
    ("Juan de la Cruz (juan@example.com) llamó ayer.", "Juan de la Cruz", "cue:contact"),
    # a title before it (the title itself stays)
    ("Please call Mr. Ravi Kumar tomorrow.", "Ravi Kumar", "cue:title"),
    ("Smt Lakshmi Devi visited the branch", "Lakshmi Devi", "cue:title"),
    ("Spoke to Dr. A. Rao.", "A. Rao", "cue:title"),
    ("La Sra. María José García López firmó.", "María José García López", "cue:title"),
    # a lead-in before it
    ("My name is Ananya Krishnan and I need help.", "Ananya Krishnan", "cue:lead-in"),
    ("Name: ANANYA KRISHNAN", "ANANYA KRISHNAN", "cue:lead-in"),
    ("Dear Ananya,\nthanks for your mail.", "Ananya", "cue:lead-in"),
    ("Rahul Verma s/o Suresh Verma, resident of Pune", "Suresh Verma", "cue:lead-in"),
    ("Hola, me llamo Lucía Fernández.", "Lucía Fernández", "cue:lead-in"),
    ("Account holder Ananya Krishnan requested a new card.", "Ananya Krishnan", "cue:lead-in"),
    ("Contact: Ravi Kumar\nBranch: Indiranagar", "Ravi Kumar", "cue:lead-in"),
    # a possessive followed by a personal attribute
    ("Ananya Krishnan's Aadhaar was updated.", "Ananya Krishnan", "cue:possessive"),
]

NOT_FOUND = [
    "Called on 2026-03-14 about card ending 1111; mail ananya.k@example-mail.in.",
    "Contact Customer Care (1800-123-4567) for help.",
    "Board Meeting (2026-03-14) moved to Friday.",
    "Dear Customer, your statement is ready.",
    "update her Aadhaar 2341 2341 2346 before 2026-03-14.",
    "Mr. 2341 and Ms.",
    "{PERSON_NAME_1} ({EMAIL_2}, {PHONE_3}) asked about {AADHAAR_4}.",
    "Mumbai (+91 22 2345 6789) is the head office.",
    "the name is not known",
    "Customer Ananya",  # a weak lead-in needs two name words
    "Globex Industries (+1 415 555 0100) and Risk Committee (risk@example.com) signed off.",
    "Write to support@example.com or call 1800 202 6161.",
]


@pytest.mark.parametrize(("text", "name", "cue"), FOUND)
def test_names_next_to_a_cue_are_found(text: str, name: str, cue: str) -> None:
    hits = scan_names(text)
    assert [(text[h.start : h.end], h.entity_type) for h in hits] == [(name, "PERSON_NAME")]
    assert hits[0].evidence == cue and hits[0].detector == "pattern:name_cue"
    assert name not in hits[0].evidence and hits[0].score >= 0.75


@pytest.mark.parametrize("text", NOT_FOUND)
def test_capitalised_words_without_a_cue_are_not_names(text: str) -> None:
    assert scan_names(text) == []


def test_the_playground_sample_finds_the_name_with_ner_off() -> None:
    text = (
        "Ananya Krishnan (ananya.k@example-mail.in, +91 98451 20377) asked to update her "
        "Aadhaar 2341 2341 2346 before 2026-03-14."
    )
    findings = Detector(DetectorConfig(locale="en_IN")).detect(text=text).findings
    names = [f for f in findings if f.entity_type == "PERSON_NAME"]
    assert [(f.locator.start, f.locator.end) for f in names] == [(0, 15)]
    assert {f.entity_type for f in findings} >= {"PERSON_NAME", "EMAIL", "PHONE", "AADHAAR"}


class _LocationNer:
    """What en_core_web_sm does with the sample: the name comes back as a LOCATION."""

    languages = ("en",)

    def analyze(self, text: str, language: str) -> list[Hit]:
        i = text.find("Ananya Krishnan")
        if i < 0:
            return []
        return [Hit("LOCATION", i, i + 15, 0.85, "ner:presidio", "ner:LOCATION:0.85")]


def test_a_name_cue_beats_an_ner_location_label_for_the_same_span() -> None:
    text = "Ananya Krishnan (ananya.k@example-mail.in) asked for a callback."
    det = Detector(DetectorConfig(locale="en_IN", ner_enabled=True), ner=_LocationNer())
    by_type = {f.entity_type: f for f in det.detect(text=text).findings}
    assert "LOCATION" not in by_type
    assert (by_type["PERSON_NAME"].locator.start, by_type["PERSON_NAME"].locator.end) == (0, 15)


def test_names_in_a_json_notes_field_become_span_findings() -> None:
    data = {"notes": "Spoke to Mrs. Meera Iyer about the card", "full_name": "Ananya Krishnan"}
    findings = Detector(DetectorConfig(locale="en_IN")).detect(data=data).findings
    spans = [f for f in findings if f.locator.path == "$.notes"]
    assert [(f.entity_type, f.locator.start, f.locator.end) for f in spans] == [
        ("PERSON_NAME", 14, 24)
    ]


# --- S8: the gender a title before a name says (the title is read, never the name) ------------
@pytest.mark.parametrize(
    ("text", "gender"),
    [
        ("Mr. Arjun", "male"),
        ("MR ARJUN", "male"),
        ("met Shri Arjun", "male"),
        ("Sr. Arjun", "male"),
        ("Mrs. Arjun", "female"),
        ("Mrs.Arjun", "female"),
        ("Ms Arjun", "female"),
        ("Miss Arjun", "female"),
        ("Smt. Arjun", "female"),
        ("Sra. Arjun", "female"),
        ("Dr. Arjun", None),
        ("Prof. Arjun", None),
        ("Mx. Arjun", None),
        ("Hamr. Arjun", None),  # a word ending in a title is no title
        ("Mrs.\nArjun", None),  # a title on the line before is no title
        ("Arjun", None),
    ],
)
def test_title_gender_reads_the_title_before_a_name(text: str, gender: str | None) -> None:
    assert title_gender(text, len(text) - len("Arjun")) == gender
