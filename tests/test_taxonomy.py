from __future__ import annotations

from dps.engine.taxonomy import (
    TAXONOMY,
    EntityInfo,
    Family,
    Sensitivity,
    entity_info,
    record_sensitivity,
    register_entity,
)


def test_core_entities_have_expected_tiers() -> None:
    assert entity_info("SSN").sensitivity is Sensitivity.CRITICAL
    assert entity_info("CARD_PAN").family is Family.FINANCIAL
    assert entity_info("EMAIL").sensitivity is Sensitivity.MEDIUM
    assert entity_info("API_KEY").sensitivity is Sensitivity.CRITICAL
    assert "AADHAAR" in TAXONOMY and "IN" in TAXONOMY["AADHAAR"].jurisdictions


def test_unknown_entities_default_to_medium_other() -> None:
    info = entity_info("EMPLOYEE_BADGE")
    assert info.family is Family.OTHER and info.sensitivity is Sensitivity.MEDIUM


def test_register_custom_entity() -> None:
    register_entity(
        EntityInfo("BUSINESS_SENSITIVE", Family.ORGANISATION, Sensitivity.HIGH, "owner-declared")
    )
    assert entity_info("BUSINESS_SENSITIVE").sensitivity is Sensitivity.HIGH


def test_record_sensitivity_uses_max_and_cooccurrence() -> None:
    assert record_sensitivity([]) is Sensitivity.LOW
    assert record_sensitivity(["EMAIL"]) is Sensitivity.MEDIUM
    assert record_sensitivity(["PERSON_NAME", "DATE_OF_BIRTH", "POSTAL_CODE"]) is Sensitivity.HIGH
    assert record_sensitivity(["PERSON_NAME", "POSTAL_CODE"]) is Sensitivity.MEDIUM
    assert record_sensitivity(["URL", "SSN"]) is Sensitivity.CRITICAL
    assert (
        Sensitivity.CRITICAL.rank
        > Sensitivity.HIGH.rank
        > Sensitivity.MEDIUM.rank
        > Sensitivity.LOW.rank
    )


def test_identifiers_of_a_natural_person_are_critical() -> None:
    # spec §5.2: any government or card identifier is CRITICAL; only business numbers (EIN, BN) are
    # HIGH. A fixed list, so entities registered by other tests cannot make this flaky.
    personal = [
        "SSN",
        "ITIN",
        "PASSPORT",
        "DRIVER_LICENSE",
        "SIN",
        "CURP",
        "RFC",
        "INE_CLAVE",
        "AADHAAR",
        "PAN_IN",
        "VOTER_ID",
        "NINO",
        "NHS_NUMBER",
        "NATIONAL_ID",
        "MRZ",
        "CARD_PAN",
        "CVV",
    ]
    for name in personal:
        assert entity_info(name).sensitivity is Sensitivity.CRITICAL, name
    assert entity_info("EIN").sensitivity is Sensitivity.HIGH
    assert entity_info("BN").sensitivity is Sensitivity.HIGH
