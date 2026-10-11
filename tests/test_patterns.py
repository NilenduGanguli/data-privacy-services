from __future__ import annotations

import pytest

from dps.engine.detect.patterns import Hit, resolve_overlaps, scan_phones, scan_text


def _types(text: str, **kw: object) -> dict[str, Hit]:
    return {h.entity_type: h for h in scan_text(text, **kw)}  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "text,entity,validated",
    [
        ("ssn 123-45-6789", "SSN", True),
        ("ITIN 912-70-1234 on file", "ITIN", True),
        ("EIN: 12-3456789", "EIN", True),
        ("CURP BOXW310820HNERXN09", "CURP", True),
        ("Aadhaar 2341 2341 2346", "AADHAAR", True),
        ("PAN ACUPA7085R", "PAN_IN", True),
        ("card 4111 1111 1111 1111", "CARD_PAN", True),
        ("IBAN GB82 WEST 1234 5698 7654 32", "IBAN", True),
        ("mail me at ann.k@example-mail.in", "EMAIL", False),
        ("server 10.20.30.40 responded", "IP_ADDRESS", False),
        (
            "token eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk",
            "JWT",
            False,
        ),
        ("key AKIAIOSFODNN7EXAMPLE", "API_KEY", False),
        ("NI number AB 12 34 56 C", "NINO", True),
        ("NHS 943 476 5919", "NHS_NUMBER", True),
    ],
)
def test_catalogue_finds_and_validates(text: str, entity: str, validated: bool) -> None:
    hits = _types(text)
    assert entity in hits, hits
    assert hits[entity].validated is validated
    assert hits[entity].score >= 0.6


def test_itin_wins_over_ssn_shape_and_invalid_card_is_low() -> None:
    hits = scan_text("912-70-1234")
    assert [h.entity_type for h in hits] == ["ITIN"]
    assert all(
        h.entity_type != "CARD_PAN" or h.score < 0.3 for h in scan_text("4111 1111 1111 1112")
    )


def test_context_promotes_date_to_date_of_birth() -> None:
    assert _types("DOB: 1988-03-14")["DATE_OF_BIRTH"].score >= 0.8
    assert "DATE" in _types("shipped 2024-01-02")
    assert "DATE_OF_BIRTH" in _types("1988-03-14", key_hint="birth_date")


def test_needs_context_patterns_stay_silent_without_it() -> None:
    assert "POSTAL_CODE" not in _types("ref 56001 approved")
    assert "POSTAL_CODE" in _types("PIN code 560001")
    assert "ROUTING_ABA" not in _types("021000021")
    assert "ROUTING_ABA" in _types("routing 021000021")


def test_phone_matcher_uses_region() -> None:
    hits = scan_phones("call +91 98451 20377 or (415) 555-0132 today", region="US")
    assert len(hits) == 2 and all(h.entity_type == "PHONE" for h in hits)
    assert scan_phones("order 98765 shipped", region="US") == []


def test_resolve_overlaps_prefers_validated_then_score_then_length() -> None:
    a = Hit("SSN", 0, 11, 0.5, "pattern:x", "", False)
    b = Hit("ITIN", 0, 11, 0.98, "pattern:y", "", True)
    c = Hit("DATE", 20, 30, 0.6, "pattern:z", "", False)
    assert resolve_overlaps([a, b, c]) == [b, c]


@pytest.mark.parametrize(
    "text", ["number 4141 7058 6492 0836", "4167 8684 3353 2653", "ref 4174-0931-9254-4994 ok"]
)
def test_grouped_card_numbers_are_never_split_into_an_aadhaar(text: str) -> None:
    # Each card's first (or last) 12 digits happen to be Verhoeff-valid; an Aadhaar hit on them
    # used to win the overlap and leave the remaining digits in clear under the wrong type.
    hits = scan_text(text)
    assert [h.entity_type for h in hits] == ["CARD_PAN"]
    digits = "".join(c for c in text[hits[0].start : hits[0].end] if c.isdigit())
    assert len(digits) == 16 and hits[0].validated


def test_aadhaar_still_matches_on_its_own() -> None:
    hits = scan_text("Aadhaar 2341 2341 2346, issued 2019")
    assert [h.entity_type for h in hits][:1] == ["AADHAAR"] and hits[0].validated


def test_evidence_never_carries_the_value() -> None:
    text = "ssn 123-45-6789 mail ann.k@example-mail.in card 4111 1111 1111 1111"
    for hit in scan_text(text) + scan_phones("call (415) 555-0132", region="US"):
        assert "6789" not in hit.evidence and "@" not in hit.evidence and "1111" not in hit.evidence
        assert "0132" not in hit.evidence
