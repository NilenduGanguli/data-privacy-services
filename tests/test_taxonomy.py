from __future__ import annotations

from collections.abc import Iterator

import pytest

from dps.engine.taxonomy import (
    TAXONOMY,
    EntityInfo,
    Family,
    Sensitivity,
    entity_info,
    is_builtin_entity,
    record_sensitivity,
    register_entity,
)


@pytest.fixture
def restore_taxonomy() -> Iterator[None]:
    """The registry is process-global: undo whatever a test registers."""
    saved = dict(TAXONOMY)
    yield
    TAXONOMY.clear()
    TAXONOMY.update(saved)


def test_core_entities_have_expected_tiers() -> None:
    assert entity_info("SSN").sensitivity is Sensitivity.CRITICAL
    assert entity_info("CARD_PAN").family is Family.FINANCIAL
    assert entity_info("EMAIL").sensitivity is Sensitivity.MEDIUM
    assert entity_info("API_KEY").sensitivity is Sensitivity.CRITICAL
    assert "AADHAAR" in TAXONOMY and "IN" in TAXONOMY["AADHAAR"].jurisdictions


def test_unknown_entities_default_to_medium_other() -> None:
    info = entity_info("EMPLOYEE_BADGE")
    assert info.family is Family.OTHER and info.sensitivity is Sensitivity.MEDIUM


@pytest.mark.usefixtures("restore_taxonomy")
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


@pytest.mark.usefixtures("restore_taxonomy")
def test_builtin_entities_cannot_be_replaced_or_downgraded() -> None:
    # One caller's inline policy must never lower another tenant's protection (spec P8).
    assert is_builtin_entity("SSN") and not is_builtin_entity("PROJECT_CODE")
    with pytest.raises(ValueError, match="SSN"):
        register_entity(EntityInfo("SSN", Family.OTHER, Sensitivity.LOW, "custom:evil"))
    assert entity_info("SSN").sensitivity is Sensitivity.CRITICAL
    assert entity_info("SSN").family is Family.GOVERNMENT_ID
    assert record_sensitivity(["SSN"]) is Sensitivity.CRITICAL
    with pytest.raises(ValueError, match="EMAIL"):
        register_entity(EntityInfo("EMAIL", Family.CONTACT, Sensitivity.CRITICAL, "declared"))
    assert entity_info("EMAIL").sensitivity is Sensitivity.MEDIUM


@pytest.mark.usefixtures("restore_taxonomy")
def test_builtins_survive_direct_registry_mutation() -> None:
    TAXONOMY["SSN"] = EntityInfo("SSN", Family.OTHER, Sensitivity.LOW, "tampered")
    assert entity_info("SSN").sensitivity is Sensitivity.CRITICAL
    assert record_sensitivity(["SSN"]) is Sensitivity.CRITICAL


@pytest.mark.usefixtures("restore_taxonomy")
def test_custom_tiers_only_ever_rise() -> None:
    register_entity(EntityInfo("PROJECT_CODE", Family.OTHER, Sensitivity.HIGH, "custom:a"))
    register_entity(EntityInfo("PROJECT_CODE", Family.OTHER, Sensitivity.LOW, "custom:b"))
    assert entity_info("PROJECT_CODE").sensitivity is Sensitivity.HIGH
    register_entity(EntityInfo("PROJECT_CODE", Family.OTHER, Sensitivity.CRITICAL, "custom:c"))
    assert entity_info("PROJECT_CODE").sensitivity is Sensitivity.CRITICAL


@pytest.mark.usefixtures("restore_taxonomy")
def test_custom_entity_cannot_go_below_the_unregistered_default() -> None:
    # An unregistered custom name is MEDIUM for every caller; a first registration at LOW would
    # downgrade the tenants that rely on that default, so it is held at MEDIUM.
    assert entity_info("LOAN_REF").sensitivity is Sensitivity.MEDIUM
    register_entity(EntityInfo("LOAN_REF", Family.OTHER, Sensitivity.LOW, "declared"))
    assert entity_info("LOAN_REF").sensitivity is Sensitivity.MEDIUM
