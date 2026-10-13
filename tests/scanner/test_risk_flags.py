"""Task 10: risk flags (value-free; at most one flag per kind per page)."""

from __future__ import annotations

from dps.layers.scanner.inventory import (
    field_risks,
    number_flags,
    rendered_risks,
    risk_codes_by_field,
    url_risks,
)
from dps.layers.scanner.models import FormFacts, RenderedFinding, RiskFlag
from tests.scanner.helpers import classification as C  # noqa: N812
from tests.scanner.helpers import field_facts as F  # noqa: N812


def _none(_: str) -> str | None:
    return None


def test_autocomplete_on_sensitive_fields_flags_once_per_page() -> None:
    fields = [
        (F(field_no=3, label="Aadhaar number"), C("AADHAAR", "CRITICAL")),
        (F(field_no=4, label="PAN", maxlength=10), C("PAN_IN", "CRITICAL")),
        (F(field_no=5, autocomplete="tel"), C("PHONE", "MEDIUM")),
        (F(field_no=6, autocomplete="off"), C("SSN", "CRITICAL")),
    ]
    flags = field_risks(2, fields, [])
    assert [(f.flag, f.field_nos, f.entity_types) for f in flags] == [
        ("autocomplete_enabled_sensitive", [3, 4], ["AADHAAR", "PAN_IN"])
    ]
    assert flags[0].severity == "high" and flags[0].page_no == 2


def test_autocomplete_rule_skips_hidden_non_text_and_named_fields() -> None:
    fields = [
        (F(field_no=1, visible=False), C("AADHAAR", "CRITICAL")),
        (F(field_no=2, input_type="hidden", visible=False), C("AADHAAR", "CRITICAL")),
        (F(field_no=3, tag="select", input_type="select-one"), C("NATIONALITY", "CRITICAL")),
        (F(field_no=4, input_type="checkbox"), C("CARD_PAN", "CRITICAL")),
        (F(field_no=5, autocomplete="cc-number"), C("CARD_PAN", "CRITICAL")),
        (F(field_no=6, autocomplete="on"), C("CARD_PAN", "CRITICAL")),
        (F(field_no=7, tag="textarea", input_type="textarea"), C("ACCOUNT_NUMBER", "HIGH")),
    ]
    (flag,) = field_risks(1, fields, [])
    assert flag.field_nos == [6, 7] and flag.entity_types == ["CARD_PAN", "ACCOUNT_NUMBER"]


def test_password_without_token_and_get_form() -> None:
    fields = [
        (F(field_no=1, input_type="password"), C("PASSWORD", "CRITICAL")),
        (
            F(field_no=2, input_type="password", autocomplete="current-password"),
            C("PASSWORD", "CRITICAL"),
        ),
        (F(field_no=3, form_index=2, input_type="email"), C("EMAIL", "MEDIUM")),
    ]
    forms = [
        FormFacts(
            form_index=2,
            method="get",
            action="/help/track/result",
            frame_index=0,
            in_shadow=False,
            field_count=1,
        )
    ]
    kinds = {f.flag: f for f in field_risks(5, fields, forms)}
    assert kinds["password_without_autocomplete_token"].field_nos == [1]
    assert kinds["password_without_autocomplete_token"].severity == "medium"
    assert kinds["sensitive_form_uses_get"].field_nos == [3]
    assert kinds["sensitive_form_uses_get"].where == "form"
    assert "autocomplete_enabled_sensitive" not in kinds  # credentials are excluded from that rule
    assert list(kinds) == ["password_without_autocomplete_token", "sensitive_form_uses_get"]


def test_a_post_form_and_low_fields_do_not_flag_get() -> None:
    fields = [
        (F(field_no=1, form_index=1, autocomplete="off"), C("EMAIL", "MEDIUM")),
        (F(field_no=2, form_index=2), C("POSTAL_CODE", "LOW")),
        (F(field_no=3, form_index=0, autocomplete="off"), C("EMAIL", "MEDIUM")),
    ]
    forms = [
        FormFacts(
            form_index=1, method="post", action="", frame_index=0, in_shadow=False, field_count=1
        ),
        FormFacts(
            form_index=2, method="get", action="", frame_index=0, in_shadow=False, field_count=1
        ),
    ]
    assert field_risks(1, fields, forms) == []


def test_pii_in_query_string_page_url_and_external_link() -> None:
    def cls(v: str) -> str | None:
        return "EMAIL" if "@" in v else None

    flags = url_risks(
        9,
        "http://h/apply/review?email=ananya.k%40example-mail.in&ref=APP-2026-0012",
        [],
        classify_value=cls,
    )
    assert [(f.flag, f.query_keys, f.entity_types, f.where) for f in flags] == [
        ("pii_in_query_string", ["email"], ["EMAIL"], "url")
    ]
    ext = url_risks(
        1, "http://h/", ["https://ads.example.net/p?ssn=900-12-3456"], classify_value=_none
    )
    assert (
        ext[0].where == "link" and ext[0].query_keys == ["ssn"] and ext[0].entity_types == ["SSN"]
    )
    assert url_risks(1, "http://h/help?lang=en", [], classify_value=_none) == []


def test_query_flags_are_value_free_and_merged_once_per_page() -> None:
    def cls(v: str) -> str | None:
        return "AADHAAR" if v.replace(" ", "").isdigit() and len(v.replace(" ", "")) == 12 else None

    flags = url_risks(
        4,
        "http://h/x?q=2345+6789+0123&501002345678=",
        ["https://t.example/p?aadhaar=2345%206789%200123", "https://t.example/p?lang=en"],
        classify_value=cls,
    )
    (flag,) = flags
    assert flag.where == "url" and flag.entity_types == ["AADHAAR"]
    # a key that is itself a value is never listed; a plain key is
    assert flag.query_keys == ["q", "aadhaar"]
    dumped = flag.model_dump_json()
    assert "2345" not in dumped and "501002345678" not in dumped


def test_a_hash_route_query_is_a_query_too() -> None:
    def cls(v: str) -> str | None:
        return "EMAIL" if "@" in v else None

    (flag,) = url_risks(3, "http://h/app#/review?mail=a.b%40example.org", [], classify_value=cls)
    assert flag.query_keys == ["mail"] and flag.entity_types == ["EMAIL"]


def test_a_low_value_or_an_empty_value_under_a_sensitive_key_does_not_flag() -> None:
    def cls(v: str) -> str | None:
        return "POSTAL_CODE" if v.isdigit() else None

    assert url_risks(1, "http://h/x?pin=560001&ssn=", [], classify_value=cls) == []


def test_rendered_unmasked_severity_and_where() -> None:
    r = [
        RenderedFinding(
            frame_index=0,
            i=4,
            start=0,
            end=19,
            entity_type="CARD_PAN",
            score=0.99,
            detectors=["pattern"],
            in_table=True,
        ),
        RenderedFinding(
            frame_index=0,
            i=7,
            start=0,
            end=24,
            entity_type="EMAIL",
            score=0.9,
            detectors=["pattern"],
            in_table=True,
        ),
    ]
    (flag,) = rendered_risks(11, r)
    assert (flag.flag, flag.severity, flag.where, flag.entity_types) == (
        "pii_rendered_unmasked",
        "high",
        "table",
        ["CARD_PAN"],
    )
    assert rendered_risks(11, [r[1]]) == []  # MEDIUM only: no flag
    iban = RenderedFinding(
        frame_index=0,
        i=9,
        start=0,
        end=22,
        entity_type="IBAN",
        score=0.95,
        detectors=["pattern"],
        in_table=False,
    )
    (high,) = rendered_risks(2, [iban])
    assert (high.severity, high.where) == ("medium", "text")


def test_numbering_and_field_codes() -> None:
    a = RiskFlag(
        flag="autocomplete_enabled_sensitive", severity="high", page_no=3, field_nos=[3, 4]
    )
    b = RiskFlag(flag="pii_in_query_string", severity="high", page_no=9, query_keys=["email"])
    c = RiskFlag(
        flag="password_without_autocomplete_token", severity="medium", page_no=3, field_nos=[2]
    )
    numbered = number_flags(
        [(9, "/apply/review?email=<EMAIL>", [b]), (3, "/apply/identity", [c, a])]
    )
    assert [(n.code, n.flag, n.path) for n in numbered] == [
        ("A1", "autocomplete_enabled_sensitive", "/apply/identity"),
        ("A2", "password_without_autocomplete_token", "/apply/identity"),
        ("A3", "pii_in_query_string", "/apply/review?email=<EMAIL>"),
    ]
    assert numbered[0].field_nos == [3, 4] and numbered[2].query_keys == ["email"]
    assert risk_codes_by_field([a, c]) == {
        3: ["autocomplete_enabled_sensitive"],
        4: ["autocomplete_enabled_sensitive"],
        2: ["password_without_autocomplete_token"],
    }
    assert number_flags([]) == []
