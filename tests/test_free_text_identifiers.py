"""Account numbers, customer ids and payee names in free text (integrated verify r1, B2).

The scanned KYC demo's statement page reads ``Account no.: <14 digits>   Customer ID: CUST-…``
and a ``NEFT/<payee>/…`` line. Before this fix ACCOUNT_NUMBER and CUSTOMER_ID were found only
under a JSON key or column that named them, so the redacted PDF kept both in clear, and the
payee's name had no cue. Free text now finds them by their label (``A/c``, ``Account no``,
``Acct``, ``Customer ID``, ``CIF``) and the name by the payment narration; the coverage note
names every type that is still recognised by a field name only.
"""

from __future__ import annotations

import pytest

from dps.config import Settings
from dps.engine.detect.patterns import scan_text
from dps.engine.detect.structural import structural_entities
from dps.engine.service import Payload, PrivacyEngine, RequestOptions
from dps.engine.verify import NAME_ONLY_GAP_PREFIX, assess_coverage, field_name_only_entities

ENGINE = PrivacyEngine(
    Settings(_env_file=None, llm_provider="stub", ner_enabled=False, default_locale="en_IN")  # type: ignore[call-arg]
)
#: Synthetic numbers only: never a real account or customer.
STATEMENT = (
    "Savings account statement\n"
    "Customer: Kavya Raman\n"
    "Account no.: 4821 7730 1956 42   Customer ID: CUST-884120\n"
    "02 Sep  NEFT/Ishaan Chaudhary/SALARY ADV            45,000.00\n"
)


def _spans(text: str, entity: str) -> list[str]:
    return [text[h.start : h.end] for h in scan_text(text) if h.entity_type == entity]


@pytest.mark.parametrize(
    "text,value",
    [
        ("Account no.: 4821 7730 1956 42 on file", "4821 7730 1956 42"),
        ("A/c 48217730195642 credited", "48217730195642"),
        ("A/C No. 4821-7730-1956 debited", "4821-7730-1956"),
        ("Acct# 004821773019 closed", "004821773019"),
        ("transfer to account number 482177301956", "482177301956"),
        ("Account: 48217730 frozen", "48217730"),
    ],
)
def test_account_numbers_are_found_by_their_label(text: str, value: str) -> None:
    assert _spans(text, "ACCOUNT_NUMBER") == [value]


@pytest.mark.parametrize(
    "text",
    [
        "Reference 4821 7730 1956 42 attached",  # no label: a number is not an account by itself
        "account ending 1956",  # four digits are a hint, not the number
        "Account holder Kavya Raman",
        "accountable for 48217730195642 units",  # a word that only starts with "account"
    ],
)
def test_numbers_without_an_account_label_are_not_accounts(text: str) -> None:
    assert _spans(text, "ACCOUNT_NUMBER") == []


def test_a_labelled_account_number_wins_over_a_checksum_shape_inside_it() -> None:
    # 4111 1111 1111 1111 is Luhn-valid; 2341 2341 2346 is a Verhoeff-valid Aadhaar shape: the
    # account label decides what they are
    for number in ("4111 1111 1111 1111", "2341 2341 2346 00"):
        text = f"Account no.: {number} credited"
        hits = scan_text(text)
        assert [(h.entity_type, text[h.start : h.end]) for h in hits] == [
            ("ACCOUNT_NUMBER", number)
        ], [h.entity_type for h in hits]
        assert hits[0].evidence == "label:account_number"


@pytest.mark.parametrize(
    "text,value",
    [
        ("Customer ID: CUST-884120", "CUST-884120"),
        ("KYC review · Customer ID CUST-884120", "CUST-884120"),
        ("CIF 100234567 linked", "100234567"),
        ("Cust. No. 00012345 merged", "00012345"),
        ("client id: C-0042117", "C-0042117"),
        ("the record CUST-884120 was updated", "CUST-884120"),  # the CUST- prefix alone
    ],
)
def test_customer_ids_are_found_in_free_text(text: str, value: str) -> None:
    assert _spans(text, "CUSTOMER_ID") == [value]


@pytest.mark.parametrize(
    "text", ["Customer ID: pending", "customer number 7 called", "Customer Kavya Raman"]
)
def test_customer_labels_without_an_id_find_nothing(text: str) -> None:
    assert _spans(text, "CUSTOMER_ID") == []


def test_the_statement_page_finds_every_identifier_and_the_payee() -> None:
    result = ENGINE.detector(ENGINE.policy(None), "en_IN").detect(text=STATEMENT)
    found = {(f.entity_type, STATEMENT[f.locator.start : f.locator.end]) for f in result.findings}
    assert ("ACCOUNT_NUMBER", "4821 7730 1956 42") in found
    assert ("CUSTOMER_ID", "CUST-884120") in found
    assert ("PERSON_NAME", "Kavya Raman") in found
    assert ("PERSON_NAME", "Ishaan Chaudhary") in found  # the payee of a NEFT narration


def test_obfuscated_statement_keeps_none_of_them() -> None:
    out = ENGINE.obfuscate(Payload(text=STATEMENT), None, RequestOptions())
    text = str(out.result)
    for original in ("4821 7730 1956 42", "48217730195642", "884120", "Ishaan", "Chaudhary"):
        assert original not in text
    assert out.report.verification.passed


@pytest.mark.parametrize(
    "narration",
    [
        "NEFT/HDFC BANK/charges",  # one name word after a bank code: not a person
        "UPI/merchant@ybl/groceries",
        "POS card 4111 1111 1111 1111 BLR",
    ],
)
def test_narrations_without_a_two_word_name_find_no_person(narration: str) -> None:
    result = ENGINE.detector(ENGINE.policy(None), "en_IN").detect(text=narration)
    assert "PERSON_NAME" not in {f.entity_type for f in result.findings}


def test_coverage_names_every_type_recognised_by_field_name_only() -> None:
    off = field_name_only_entities(ner=False)
    # pinned so that a change in what free text can find shows up here
    assert off == (
        "AGE",
        "CASE_ID",
        "COOKIE_ID",
        "CVV",
        "DEVICE_ID",
        "DIAGNOSIS_CODE",
        "EMPLOYEE_ID",
        "GENDER",
        "MEDICAL_RECORD_NUMBER",
        "MEDICATION",
        "MRZ",
        "NATIONALITY",
        "NATIONAL_ID",
        "ORG_NAME",
        "VOTER_ID",
    )
    assert not {"ACCOUNT_NUMBER", "CUSTOMER_ID", "STREET_ADDRESS"} & set(off)
    assert set(off) <= structural_entities()
    on = field_name_only_entities(ner=True)
    assert "NATIONALITY" not in on and set(on) < set(off)
    cov = assess_coverage(
        text=STATEMENT, findings=[], kept=set(), detectors=["patterns"], ner="off"
    )
    gap = next(g for g in cov.gaps if g.startswith(NAME_ONLY_GAP_PREFIX))
    assert all(entity in gap for entity in off) and not cov.complete


def test_structured_json_has_no_name_only_gap() -> None:
    cov = assess_coverage(
        data={"account_number": "48217730195642", "status": "ACTIVE"},
        findings=[],
        kept=set(),
        detectors=["structural", "patterns"],
        ner="off",
    )
    assert cov.free_text_fields == 0 and cov.complete and cov.gaps == []


@pytest.mark.parametrize(
    "text,value",
    [
        ("Address: 70, Chand Nagar, Raebareli PIN code 229001", "70, Chand Nagar, Raebareli"),
        (
            "To: K\nAddress: 14 Lavelle Road, Bengaluru 560001\nMobile: x",
            "14 Lavelle Road, Bengaluru 560001",
        ),
        ("Residential address - Flat 4B, Sea View, Mumbai", "Flat 4B, Sea View, Mumbai"),
        ("Dirección: Av. Reforma 222, CDMX", "Av. Reforma 222, CDMX"),
        ("ADDRESS: 5 HIGH ST, LONDON Postcode SW1A 1AA", "5 HIGH ST, LONDON"),
    ],
)
def test_a_line_labelled_address_is_a_street_address(text: str, value: str) -> None:
    assert _spans(text, "STREET_ADDRESS") == [value]


@pytest.mark.parametrize(
    "text",
    [
        "Email address: kavya@example.com",
        "Our address is printed below",
        "Address:\n",
        "Address: PIN code",  # an OCR re-read of a redacted address line
        "Address: same as above",  # no digit, no comma: not an address
    ],
)
def test_address_words_without_a_line_label_are_not_addresses(text: str) -> None:
    assert _spans(text, "STREET_ADDRESS") == []
