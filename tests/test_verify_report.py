from __future__ import annotations

from dps.engine.findings import Finding, Locator, Plan, PlanEntry
from dps.engine.report import Report, build_report
from dps.engine.taxonomy import Sensitivity
from dps.engine.verify import Verification, collect_originals, normalise, verify_output


def _finding(path: str, entity: str, start: int | None = None, end: int | None = None) -> Finding:
    return Finding(
        locator=Locator(path=path, start=start, end=end),
        entity_type=entity,
        score=0.9,
        detectors=["t"],
        sensitivity=Sensitivity.MEDIUM,
    )


DATA = {
    "email": "ann.k@example-mail.in",
    "ssn": "123-45-6789",
    "notes": "mail ann.k@example-mail.in",
    "n": 42,
}
FINDINGS = [
    _finding("$.email", "EMAIL"),
    _finding("$.ssn", "SSN"),
    _finding("$.notes", "EMAIL", 5, 26),
]


def test_collect_originals_reads_whole_values_and_spans() -> None:
    originals = collect_originals(FINDINGS, data=DATA)
    assert [o for _, o in originals] == [
        "ann.k@example-mail.in",
        "123-45-6789",
        "ann.k@example-mail.in",
    ]


def test_verify_passes_clean_output_and_catches_leaks() -> None:
    originals = collect_originals(FINDINGS, data=DATA)
    clean = {
        "email": "x@example-mail.in",
        "ssn": "987-65-4321",
        "notes": "mail x@example-mail.in",
        "n": 42,
    }
    ok = verify_output(originals, clean)
    assert ok.passed and ok.checked == 3 and ok.hits == []
    leaky = {**clean, "notes": "mail ann.k@example-mail.in"}
    bad = verify_output(originals, leaky)
    assert not bad.passed and bad.hits == [{"path": "$.notes", "entity_type": "EMAIL"}]
    # formatting changes do not hide a leak
    assert not verify_output(originals, {**clean, "ssn": "123 45 6789"}).passed
    # deliberately kept locators and short values are skipped
    assert verify_output(originals, leaky, kept={("$.notes", 5, 26)}).passed
    assert verify_output([(_finding("$.x", "AGE"), "42")], {"x": "42"}).passed


def test_report_summarises_without_values() -> None:
    plan = Plan(
        mode="obfuscate",
        record_sensitivity=Sensitivity.CRITICAL,
        entries=[
            PlanEntry(
                finding=FINDINGS[1],
                method="fpe_digits",
                params={},
                reason="policy:SSN",
                reversible=True,
            )
        ],
    )
    rep = build_report(
        mode="obfuscate",
        policy_name="gdpr-pseudonymise",
        policy_hash="abc",
        findings=FINDINGS,
        plan=plan,
        methods_used={"fpe_digits": 1},
        notes={},
        reversible_values=1,
        verification=Verification(passed=True, checked=3, hits=[]),
        llm_calls=0,
        duration_ms=3.2,
    )
    assert (
        isinstance(rep, Report)
        and rep.findings_total == 3
        and rep.findings_by_type == {"EMAIL": 2, "SSN": 1}
    )
    assert rep.llm == {"calls": 0, "raw_pii_sent": 0} and rep.verification.passed  # type: ignore[union-attr]
    assert "6789" not in rep.model_dump_json()


# --- additions beyond the plan (see docs/superpowers/build/M0-DEVIATIONS.md, Task 18) ----------
def test_non_latin_originals_are_checked_too() -> None:
    name = "अनन्या कृष्णन"
    originals = [
        (_finding("$.name", "PERSON_NAME"), name),
        (_finding("$.city", "CITY"), "Ciudad de México"),
    ]
    leak = verify_output(originals, {"name": name, "city": "x"})
    assert (
        not leak.passed
        and leak.checked == 2
        and leak.hits == [{"path": "$.name", "entity_type": "PERSON_NAME"}]
    )
    # case, separators and accents are folded on both sides, so none of them hides a leak
    assert not verify_output(originals, {"name": "x", "city": "CIUDAD DE MEXICO"}).passed
    assert not verify_output(originals, {"name": "x", "city": "ciudad-de-méxico"}).passed
    assert not verify_output(originals, {"name": "अनन्या  कृष्णन!", "city": "x"}).passed
    assert verify_output(originals, {"name": "मीरा वेंकटेशन", "city": "Guadalajara"}).passed


def test_values_with_escaped_characters_are_still_found() -> None:
    address = "12 Main St\nSpringfield\t62704"
    originals = [
        (_finding("$.addr", "STREET_ADDRESS"), address),
        (_finding("$.q", "PERSON_NAME"), 'Ann "Annie" O\'Neil'),
    ]
    assert not verify_output(originals[:1], {"addr": address}).passed
    assert not verify_output(originals[1:], {"q": 'Ann "Annie" O\'Neil'}).passed


def test_full_width_and_compatibility_forms_are_folded() -> None:
    originals = [(_finding("$.ssn", "SSN"), "123-45-6789")]
    assert not verify_output(originals, {"ssn": "１２３-４５-６７８９"}).passed
    assert normalise("Ⅻ ＡＢＣ") == normalise("XII abc")


def test_a_value_kept_on_purpose_is_not_a_leak_wherever_it_appears() -> None:
    # DOB date-shifted in $.dob, but the same date kept (DATE: keep) inside the notes span
    data = {"dob": "1988-03-14", "notes": "seen 1988-03-14 at branch", "ssn": "123-45-6789"}
    findings = [
        _finding("$.dob", "DATE_OF_BIRTH"),
        _finding("$.notes", "DATE", 5, 15),
        _finding("$.ssn", "SSN"),
    ]
    originals = collect_originals(findings, data=data)
    out = {"dob": "1988-07-02", "notes": "seen 1988-03-14 at branch", "ssn": "123-45-6789"}
    result = verify_output(originals, out, kept={("$.notes", 5, 15)})
    assert not result.passed and result.hits == [{"path": "$.ssn", "entity_type": "SSN"}]


def test_originals_in_output_keys_are_found() -> None:
    originals = [(_finding("$.email", "EMAIL"), "ann.k@example-mail.in")]
    result = verify_output(originals, {"by_email": {"ann.k@example-mail.in": {"n": 1}}})
    assert not result.passed and result.hits == [
        {"path": "$.by_email['ann.k@example-mail.in']", "entity_type": "EMAIL"}
    ]


def test_text_output_and_numeric_leaves() -> None:
    text = "Call 9845120377 or mail ann.k@example-mail.in"
    findings = [_finding("$", "PHONE", 5, 15), _finding("$", "EMAIL", 24, 45)]
    originals = collect_originals(findings, text=text)
    assert [o for _, o in originals] == ["9845120377", "ann.k@example-mail.in"]
    assert verify_output(originals, "Call [[PII:PHONE:abcd]] or mail x@example-mail.in").passed
    assert verify_output(originals, "Call 98451 20377").hits == [
        {"path": "$", "entity_type": "PHONE"}
    ]
    numeric = collect_originals([_finding("$.n", "ACCOUNT_NUMBER")], data={"n": 50100234567812})
    assert numeric[0][1] == "50100234567812"
    assert not verify_output(numeric, {"n": 50100234567812}).passed
    assert verify_output(numeric, {"n": None, "flag": True}).passed


def test_hits_are_reported_once_per_output_location() -> None:
    originals = collect_originals(FINDINGS, data=DATA)
    result = verify_output(originals, DATA)
    assert result.hits == [
        {"path": "$.email", "entity_type": "EMAIL"},
        {"path": "$.notes", "entity_type": "EMAIL"},
        {"path": "$.ssn", "entity_type": "SSN"},
    ]


def test_detect_reports_without_a_plan() -> None:
    rep = build_report(
        mode="detect",
        policy_name="pci-dss",
        policy_hash="h",
        findings=[_finding("$.a", "EMAIL")],
        plan=None,
        methods_used={},
        notes={},
        reversible_values=0,
        verification=None,
        llm_calls=0,
        duration_ms=1.23456,
    )
    assert (
        rep.record_sensitivity == "LOW"
        and rep.duration_ms == 1.23
        and rep.findings_by_detector == {"t": 1}
    )
    assert rep.engine_version and rep.verification is None
