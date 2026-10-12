from __future__ import annotations

import pytest

from dps.layers.scanner.urls import (
    CrawlScope,
    Origin,
    UrlError,
    canonical,
    path_matches,
    redact_origin,
    redact_url,
    template,
    validate_glob,
)


def _cls(text: str) -> str | None:  # stand-in for the detector: only e-mails are "PII" here
    return "EMAIL" if "@" in text else None


def test_canonical_normalises_and_drops_userinfo_fragment_default_port() -> None:
    assert canonical("HTTP://User:pw@Demo-Site:80/apply?b=2&a=1#top") == (
        "http://demo-site/apply?a=1&b=2"
    )
    assert canonical("/apply/identity", base="http://demo-site:8410/apply") == (
        "http://demo-site:8410/apply/identity"
    )
    assert canonical("https://h/app#/nominee") == "https://h/app#/nominee"  # hash routes are routes
    for bad in (
        "mailto:a@b.co",
        "javascript:void(0)",
        "tel:+1555",
        "data:text/html,x",
        "ftp://h/x",
    ):
        assert canonical(bad) is None


def test_template_collapses_identifiers_and_query_values() -> None:
    assert template("http://h:8410/accounts/statements/2026-04?month=4&acct=7") == (
        "h:8410/accounts/statements/{id}?acct&month"
    )
    assert template("http://h/help/faq/7") == "h/help/faq/{id}"
    assert template("http://h/help/faq/7") == template("http://h/help/faq/8")
    assert template("http://h/app#/nominee") == "h/app#/nominee"


def test_globs() -> None:
    assert path_matches("/logout", ["/logout"]) and path_matches("/admin/users", ["/admin"])
    assert path_matches("/help/faq/3", ["/help/faq/*"]) and not path_matches(
        "/apply", ["/admin", "/logout"]
    )
    validate_glob("/payments/*")
    for bad in ("payments", "/a b", "/x?y=1"):
        with pytest.raises(UrlError):
            validate_glob(bad)


def test_redact_url_masks_query_values_ids_and_entities() -> None:
    out = redact_url(
        "http://h/apply/review?ref=APP-2026-0012&email=ananya.k%40x.in&lang=en#x", _cls
    )
    assert out == "http://h/apply/review?email=<EMAIL>&lang=<v>&ref=<v>"
    assert redact_url("http://h/customers/ananya.k@x.in/profile", _cls) == (
        "http://h/customers/<EMAIL>/profile"
    )
    assert redact_url("http://u:p@h/accounts/12345678", _cls) == "http://h/accounts/{id}"
    assert "ananya" not in out and "2026" not in out
    assert redact_origin("https://cdn.example.org/app.js?v=1") == "https://cdn.example.org"


def test_redaction_never_keeps_a_query_key_that_is_itself_a_value() -> None:
    out = redact_url("http://h/x?ananya.k%40x.in=1&a%20b=2&ok=3", _cls)
    assert out == "http://h/x?<k>=<v>&<k>=<v>&ok=<v>"
    assert "ananya" not in out


def test_a_malformed_port_is_never_echoed_and_never_raises() -> None:
    assert canonical("http://h:99999/x") is None
    assert template("http://h:99999/x") == "h/x"
    assert redact_url("http://h:99999/x?q=1", _cls) == "http://h/x?q=<v>"
    assert redact_origin("http://h:99999/x") == "http://h"
    with pytest.raises(UrlError) as err:
        Origin.of("http://h:99999/x")
    assert str(err.value) == "url_invalid"


def test_scope_verdicts() -> None:
    scope = CrawlScope(
        start=Origin.of("http://demo-site:8410/apply"),
        same_origin=True,
        registered_hosts=frozenset({"demo-site", "cdn.bank.internal"}),
        deny=("/logout", "/admin"),
    )
    assert scope.navigation_verdict("http://demo-site:8410/apply/identity") == "ok"
    assert scope.navigation_verdict("http://demo-site:8410/logout") == "deny"
    assert scope.navigation_verdict("http://cdn.bank.internal/x") == "other_origin"
    assert scope.navigation_verdict("http://tracker.invalid/p.gif") == "other_origin"
    assert scope.navigation_verdict("mailto:a@b.co") == "scheme"
    assert scope.network_host_allowed("cdn.bank.internal")
    assert not scope.network_host_allowed("tracker.invalid")
    wide = CrawlScope(
        start=scope.start,
        same_origin=False,
        registered_hosts=scope.registered_hosts,
        allow=("/apply/*",),
    )
    assert wide.navigation_verdict("http://cdn.bank.internal/apply/x") == "ok"
    assert wide.navigation_verdict("http://tracker.invalid/apply/x") == "unregistered_host"
    assert wide.navigation_verdict("http://demo-site:8410/help") == "not_allowed"
    with pytest.raises(UrlError):
        Origin.of("not a url")


def test_url_errors_carry_the_code_only() -> None:
    with pytest.raises(UrlError) as err:
        Origin.of("ftp://secret-host.example/ananya")
    assert str(err.value) == "url_invalid" and err.value.code == "url_invalid"
    with pytest.raises(UrlError) as gerr:
        validate_glob("/ananya k")
    assert str(gerr.value) == "glob_invalid" and "ananya" not in repr(gerr.value)
