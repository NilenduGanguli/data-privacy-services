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


@pytest.mark.parametrize(
    "path,entity",
    [
        ("$.contact.addresses[*].line1", "STREET_ADDRESS"),
        ("$.contact.addresses[*].line_2", "STREET_ADDRESS"),
        ("$.billingAddress.street", "STREET_ADDRESS"),
        ("$.contact.addresses[*].pin", "POSTAL_CODE"),
        ("$.address.zip", "POSTAL_CODE"),
        ("$.addr.postcode", "POSTAL_CODE"),
        ("$.phones[*].number", "PHONE"),
        ("$.passport.number", "PASSPORT"),
        ("$.contact.email", "EMAIL"),
        ("ADDR_LINE1", "STREET_ADDRESS"),
    ],
)
def test_classify_path_scores_parent_segments(path: str, entity: str) -> None:
    from dps.engine.detect.structural import classify_path

    top = classify_path(path)[0]
    assert top.entity_type == entity and top.score >= 0.6, (path, top)


@pytest.mark.parametrize(
    "path",
    [
        "$.contact.addresses[*].type",
        "$.contact.addresses[*].country",
        "$.ipAddress.pin",
        "$.order.number",  # a generic leaf inherits only from a classified parent
        "$.accounts[*].balance",
        "$.kyc.verifiedAt",
    ],
)
def test_classify_path_leaves_unrelated_leaves_alone(path: str) -> None:
    from dps.engine.detect.structural import classify_path

    assert [c for c in classify_path(path) if c.score >= 0.6] == [], path


def test_an_email_parent_adds_no_address_context() -> None:
    """``line1`` is an address-line cue by its own name (main's integrated verify r1 B1), so
    ``$.emailAddress.line1`` is classified — but only as its leaf name says: an e-mail parent is
    not an address context and raises nothing (WP-I m3)."""
    from dps.engine.detect.structural import classify_key, classify_path

    assert classify_path("$.emailAddress.line1") == classify_key("$.line1")
    assert classify_path("$.emailAddress.line1")[0].entity_type == "STREET_ADDRESS"


def test_classify_path_equals_classify_key_without_parents() -> None:
    from dps.engine.detect.structural import classify_path

    for key in ("ADDR_LINE1", "PIN_CODE", "$.notes", "$.customerId", "line1", "pin"):
        assert classify_path(key) == classify_key(key), key


@pytest.mark.parametrize(
    "key",
    [
        "firstname",
        "FIRSTNAME",
        "lastname",
        "LASTNAME",
        "givenname",
        "familyname",
        "middlename",
        "forename",
        "fname",
        "lname",
        "cust_lname",
        "$.nominee.fname",
    ],
)
def test_run_together_name_part_keys_are_names(key: str) -> None:
    # S8: the keys that hold one part of a person's name stand on their own, as their spaced
    # forms (first_name, last_name, given_name) do; otherwise a synthetic record keeps the value
    top = classify_key(key)[0]
    assert top.entity_type == "PERSON_NAME" and top.score >= 0.7


#: Every spelling of a key that holds one part of a person's name (``methods.name_field`` gives it
#: a role): run together, spaced, camel, upper case, plural, the split short forms, a number run on,
#: a bare part under a key that names a name. Each must be detected on its own key (review r1 B2:
#: ``given_names``, ``FName``/``LName`` and ``name.first`` had a role but were not detected with
#: NER off, so a synthesize run copied the real names).
NAME_PART_PATHS = [
    *(
        f"$.{key}"
        for key in (
            "first_name", "FIRST_NAME", "firstName", "FirstName", "firstname", "FIRSTNAME",
            "first_names", "firstNames", "FIRSTNAMES", "given_name", "GivenName", "givenname",
            "given_names", "givenNames", "GIVENNAMES", "forename", "fore_name", "foreName",
            "forenames", "FORENAMES", "fname", "FNAME", "f_name", "fName", "FName", "custFName",
            "middle_name", "middleName", "MIDDLENAME", "middle_names", "middlenames",
            "last_name", "LAST_NAME", "lastName", "lastname", "LASTNAME", "surname", "Surname",
            "SURNAME", "sur_name", "surName", "family_name", "familyName", "familyname",
            "lname", "LNAME", "l_name", "lName", "LName", "custLName", "cust_lname",
            "nominee_first_name", "nominee_given_name", "nominee_family_name", "NOMINEE_SURNAME",
            "FIRST_NAME_2", "first_name2", "firstname2", "FNAME1", "lastName2", "surname3",
        )
    ),
    "$['Last Name']",
    "$.customer.first_name",
    "$.nominee.fname",
    "$.name.first",
    "$.name.given",
    "$.name.middle",
    "$.name.last",
    "$.name.family",
    "$.customer.name.family",
    "$.customerName.first",
    "$.people[1].given_names",
]  # fmt: skip


@pytest.mark.parametrize("path", NAME_PART_PATHS)
def test_every_key_that_holds_a_name_part_is_detected_as_a_name(path: str) -> None:
    from dps.engine.detect.structural import classify_path
    from dps.engine.transform.methods import name_field

    assert name_field(path) is not None, path  # a key synthesis fills with one part of a name
    for top in (classify_key(path)[0], classify_path(path)[0]):  # L1 / the scanner, L2
        assert top.entity_type == "PERSON_NAME" and top.score >= 0.7, (path, top)
    if path.count(".") == 1 and "[" not in path:  # an L2 column name, no "$."
        top = classify_path(path[2:])[0]
        assert top.entity_type == "PERSON_NAME" and top.score >= 0.7, (path, top)


@pytest.mark.parametrize(
    "path",
    [
        "$.first",
        "$.last",
        "$.given",
        "$.address.last",
        "$.filename",
        "$.file_name",
        "$.name.last_updated",
        "$.username.first",
    ],
)
def test_words_near_a_name_part_are_not_names(path: str) -> None:
    from dps.engine.detect.structural import classify_path

    for found in (classify_key(path), classify_path(path)):
        assert not [c for c in found if c.entity_type == "PERSON_NAME" and c.score >= 0.7], path
