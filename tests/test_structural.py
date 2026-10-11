from __future__ import annotations

import pytest

from dps.engine.detect.structural import classify_key, normalize_key


@pytest.mark.parametrize(
    "key,expected",
    [
        ("customerSSN", "customer ssn"),
        ("date_of_birth", "date of birth"),
        ("$.card.tags[1]['cc-number']", "cc number"),
        ("EMAIL_ADDRESS", "email address"),
    ],
)
def test_normalize_key(key: str, expected: str) -> None:
    assert normalize_key(key) == expected


@pytest.mark.parametrize(
    "key,entity,min_score",
    [
        ("ssn", "SSN", 0.9),
        ("$.applicant.dateOfBirth", "DATE_OF_BIRTH", 0.9),
        ("aadhaar_no", "AADHAAR", 0.9),
        ("cc_number", "CARD_PAN", 0.9),
        ("correo_electronico", "EMAIL", 0.8),
        ("mobile_phone", "PHONE", 0.8),
        ("addr_line1", "STREET_ADDRESS", 0.7),
        ("api_key", "API_KEY", 0.9),
        ("employeeId", "EMPLOYEE_ID", 0.85),
    ],
)
def test_classify_key_top_candidate(key: str, entity: str, min_score: float) -> None:
    top = classify_key(key)[0]
    assert top.entity_type == entity and top.score >= min_score
    assert top.evidence.startswith("key:")


def test_ambiguous_and_unknown_keys() -> None:
    assert classify_key("pan")[0].entity_type == "PAN_IN" and classify_key("pan")[0].score == 0.5
    assert classify_key("name")[0].score < 0.6  # needs value evidence to pass the threshold
    assert classify_key("widget_count") == []


@pytest.mark.parametrize(
    "key,expected",
    [
        ("$", ""),
        ("$.tags[3]", "tags"),
        ("$['odd key']", "odd key"),
        ("$.a['b.c']", "b c"),
        ("customer.email", "email"),
        ("cc-number", "cc number"),
        ("userIPAddress", "user ip address"),
        ("customerSSNLast4", "customer ssn last4"),
        ("teléfono_móvil", "telefono movil"),
        ("$.cliente.dirección", "direccion"),
    ],
)
def test_normalize_key_edge_cases(key: str, expected: str) -> None:
    assert normalize_key(key) == expected


def test_acronyms_and_accents_reach_the_rules() -> None:
    assert classify_key("userIPAddress")[0].entity_type == "IP_ADDRESS"
    assert classify_key("$.cliente.teléfono")[0].entity_type == "PHONE"
    assert classify_key("$.cliente.dirección")[0].entity_type == "STREET_ADDRESS"


def test_evidence_names_the_rule_never_the_key_value() -> None:
    for cand in classify_key("$.customer.cc_number"):
        assert cand.evidence == "key:cc number"
