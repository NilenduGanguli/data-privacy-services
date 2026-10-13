from __future__ import annotations

from collections.abc import Callable

import pytest
from pydantic import ValidationError

from dps.engine.detect.ensemble import Detector, DetectorConfig
from dps.layers.scanner.models import PageRecord
from dps.layers.scanner.urls import (
    CrawlScope,
    Origin,
    UrlError,
    canonical,
    normalise_path,
    path_matches,
    path_verdict,
    redact_origin,
    redact_url,
    template,
    template_is_value_free,
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
    assert template("http://h:8410/accounts/statements/2026-04?month=4&acct=7", _cls) == (
        "h:8410/accounts/statements/{id}?acct&month"
    )
    assert template("http://h/help/faq/7", _cls) == "h/help/faq/{id}"
    assert template("http://h/help/faq/7", _cls) == template("http://h/help/faq/8", _cls)
    assert template("http://h/app#/nominee", _cls) == "h/app#/nominee"


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

    def digits(text: str) -> str | None:  # a key made of plain characters can still be a value
        return "ACCOUNT_NUMBER" if text.isdigit() and len(text) >= 8 else None

    keyed = redact_url("http://h/x?50100234567812=on&page=2", digits)
    assert keyed == "http://h/x?<k>=<v>&page=<v>"
    assert "50100234567812" not in keyed


def test_a_malformed_port_is_never_echoed_and_never_raises() -> None:
    assert canonical("http://h:99999/x") is None
    assert template("http://h:99999/x", _cls) == "h/x"
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
    # a browser goes to evil.com for this one; urllib reads demo-site:8410 (userinfo "evil.com\\")
    assert scope.navigation_verdict("http://evil.com\\@demo-site:8410/") == "other_origin"
    assert scope.navigation_verdict("http://u:p@demo-site:8410/apply") == "other_origin"
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


def _none(text: str) -> str | None:  # a classifier that recognises nothing at all
    return None


def _real_detector() -> Callable[[str], str | None]:
    """The M0 detector as the classifier (en_IN): it misses prefixed account numbers, customer
    ids and session tokens, so the structural rules must catch them."""
    detector = Detector(DetectorConfig(locale="en_IN"))

    def classify(text: str) -> str | None:
        found = detector.detect(text=text).findings
        return found[0].entity_type if found else None

    return classify


_ID_URLS = [
    ("http://h/accounts/SB-50100234567812/statement", "http://h/accounts/{id}/statement"),
    ("http://h/customer/CUST0012345/view", "http://h/customer/{id}/view"),
    ("http://h/a/acct-123456789012", "http://h/a/{id}"),
    ("http://h/t/A1B2C3D4/x", "http://h/t/{id}/x"),
    ("http://h/c/ref9-2026x", "http://h/c/{id}"),
    (
        "http://h/app/profile;jsessionid=A1B2C3D4E5F6A7B8C9D0E1F2A3B4C5D6",
        "http://h/app/profile;jsessionid=<v>",
    ),
    ("http://h/app;JSESSIONID=abc/x;v=2/y", "http://h/app;JSESSIONID=<v>/x;v=<v>/y"),
    ("http://h/x;50100234567812/y", "http://h/x;<v>/y"),
    ("http://h/x;50100234567812=1;ananya.k%40x.in=2", "http://h/x;<k>=<v>;<k>=<v>"),
    ("http://h/profile/Ananya%20Kapoor", "http://h/profile/{id}"),
    ("http://h/customers/ananya.k@x.in/profile", "http://h/customers/{id}/profile"),
    ("http://h/search/q=ananya/results", "http://h/search/{id}/results"),
    # R2-B1: digit groups shorter than four are one number all the same
    ("http://h/acct/501-002-345-678/stmt", "http://h/acct/{id}/stmt"),
    ("http://h/acct/501.002.345.678/stmt", "http://h/acct/{id}/stmt"),
    ("http://h/acct/501%2F002%2F345%2F678/stmt", "http://h/acct/{id}/stmt"),
    ("http://h/phone/%2B91-987-654-321", "http://h/phone/{id}"),
    ("http://h/phone/+91%20987%20654%20321", "http://h/phone/{id}"),
    ("http://h/dob/12-05-90", "http://h/dob/{id}"),
    ("http://h/dob/1-5-90", "http://h/dob/{id}"),
    ("http://h/c/1a2b3c4d5e6f", "http://h/c/{id}"),
    ("http://h/acct/501/002/345/678/stmt", "http://h/acct/{id}/{id}/{id}/{id}/stmt"),
    ("http://h/dob/12/05/90/x", "http://h/dob/{id}/{id}/{id}/x"),
    ("http://h/acct/50-1/00-2/34-5", "http://h/acct/{id}/{id}/{id}"),
    ("http://h/x?501-002-345-678=1&page=2", "http://h/x?<k>=<v>&page=<v>"),
    ("http://h/x?12-05-90", "http://h/x?<k>="),
    ("http://h/x;501-002-345-678=1/y", "http://h/x;<k>=<v>/y"),
    # G2-r1 B3: a date of birth with a month name, in any order, with or without separators
    ("http://h/dob/12May90", "http://h/dob/{id}"),
    ("http://h/dob/12-may-90", "http://h/dob/{id}"),
    ("http://h/dob/May-12-90", "http://h/dob/{id}"),
    ("http://h/dob/12%20May%201990", "http://h/dob/{id}"),
    ("http://h/dob/1990-May-12", "http://h/dob/{id}"),
    ("http://h/dob/12th-September-1990", "http://h/dob/{id}"),
    ("http://h/dob/May%2012,%201990", "http://h/dob/{id}"),
    ("http://h/dob/12mayo90", "http://h/dob/{id}"),  # Spanish
    ("http://h/dob/2nd-Jan-99", "http://h/dob/{id}"),
    ("http://h/dob/2ndjan99", "http://h/dob/{id}"),
    # G2-r2 B2: a date split over path segments, or written with connector words
    ("http://h/dob/1/5/90", "http://h/dob/{id}/{id}/{id}"),
    ("http://h/dob/12/may/90", "http://h/dob/{id}/{id}/{id}"),
    ("http://h/dob/may/12/90", "http://h/dob/{id}/{id}/{id}"),
    ("http://h/dob/12/May/1990", "http://h/dob/{id}/{id}/{id}"),
    ("http://h/dob/12-de-mayo-de-90", "http://h/dob/{id}"),
    ("http://h/dob/the-12th-of-may-90", "http://h/dob/{id}"),
    ("http://h/dob/12/de/mayo/de/90/x", "http://h/dob/{id}/{id}/{id}/{id}/{id}/x"),
    ("http://h/dob/12th/of/May/1990", "http://h/dob/{id}/{id}/{id}/{id}"),
    ("http://h/dob/12-%E0%A4%AE%E0%A4%88-90", "http://h/dob/{id}"),  # Hindi: मई
    ("http://h/x?12May90=1&page=2", "http://h/x?<k>=<v>&page=<v>"),
    ("http://h/x;dob-12May90=1/y", "http://h/x;<k>=<v>/y"),
]


@pytest.mark.parametrize("classify", [_none, _real_detector()], ids=["none", "detector"])
@pytest.mark.parametrize("url,expected", _ID_URLS)
def test_redact_url_hides_account_numbers_customer_ids_and_session_tokens(
    url: str, expected: str, classify: Callable[[str], str | None]
) -> None:
    out = redact_url(url, classify)
    if classify is _none:
        assert out == expected
    for secret in (
        "50100234567812",
        "0012345",
        "123456789012",
        "A1B2C3D4",
        "ref9",
        "ananya",
        "Ananya",
        "Kapoor",
        "abc",
        "501",
        "345",
        "678",
        "987",
        "654",
        "12-05",
        "1-5-90",
        "1a2b",
        "34-5",
        "May",
        "may",
        "Sept",
        "Jan",
        "jan",
        "%E0%A4%AE",
        "मई",
    ):
        assert secret not in out


def test_redact_url_keeps_plain_route_segments() -> None:
    url = "http://h/apply/identity/step2/review;lang=en?x=1"
    assert redact_url(url, _none) == "http://h/apply/identity/step2/review;lang=<v>?x=<v>"
    assert redact_url("http://h/", _none) == "http://h/"
    # fewer than 6 digits and no date shape: routes and versions stay readable
    plain = "http://h/docs/v1.2.3/api/v2/faq/1/page/2/x86_64"
    assert redact_url(plain, _none) == plain
    assert template(plain, _none) == "h/docs/v1.2.3/api/v2/faq/{id}/page/{id}/x86_64"
    # a month name alone, or with a year but no day, is a route, not a date of birth
    months = "http://h/news/may-24/summary/mayor/march/dec2/sept-update/12-may/julio"
    assert redact_url(months, _none) == months


_TEMPLATE_LEAKS = [
    ("http://h/customers/ananya.k@x.in/profile", ("ananya", "x.in")),
    ("http://h/accounts/SB-50100234567812/statement", ("50100234567812",)),
    ("http://h/customer/CUST0012345/view", ("0012345",)),
    ("http://h/x?50100234567812=on&page=2", ("50100234567812",)),
    ("http://h/x?ananya.k%40x.in=1&page=2", ("ananya",)),
    ("http://h/profile/Ananya%20Kapoor", ("Ananya", "Kapoor")),
    ("http://h/app#/nominee/ananya.k@x.in", ("ananya",)),
    ("http://h/app#!/acct/SB-50100234567812?50100234567812=1", ("50100234567812",)),
    ("http://h/app/profile;jsessionid=A1B2C3D4E5F6A7B8C9D0E1F2A3B4C5D6", ("A1B2",)),
    ("http://h/acct/501-002-345-678/stmt", ("501", "345-678")),
    ("http://h/acct/501.002.345.678/stmt", ("501.002",)),
    ("http://h/phone/%2B91-987-654-321", ("987", "654")),
    ("http://h/dob/12-05-90", ("12-05",)),
    ("http://h/dob/1-5-90", ("1-5-90",)),
    ("http://h/acct/50-1/00-2/34-5", ("50-1", "34-5")),
    ("http://h/x?501-002-345-678=1&page=2", ("501",)),
    ("http://h/x;501-002-345-678=1/y", ("501",)),
    ("http://h/app#/acct/501-002-345-678?12-05-90=1", ("501", "12-05")),
    ("http://h/dob/12May90", ("May",)),
    ("http://h/dob/12-may-90", ("may",)),
    ("http://h/dob/May-12-90", ("May",)),
    ("http://h/dob/12%20May%201990", ("May",)),
    ("http://h/dob/12-diciembre-1990", ("diciembre",)),
    ("http://h/x?page=1&12May90=1", ("May",)),
    ("http://h/app#/dob/May-12-90?12-may-90=1", ("May", "may")),
    # G2-r2 B2
    ("http://h/dob/1/5/90", ("/1/5", "/90")),
    ("http://h/dob/12/may/90", ("may", "/12", "/90")),
    ("http://h/dob/may/12/90", ("may", "/12", "/90")),
    ("http://h/dob/12/May/1990", ("May", "/12", "1990")),
    ("http://h/dob/12-de-mayo-de-90", ("mayo", "12-de")),
    ("http://h/dob/the-12th-of-may-90", ("may", "12th")),
    ("http://h/app#/dob/12/may/90", ("may", "/12", "/90")),
]


@pytest.mark.parametrize("classify", [_none, _real_detector()], ids=["none", "detector"])
@pytest.mark.parametrize("url,secrets", _TEMPLATE_LEAKS)
def test_the_persisted_template_is_value_free(
    url: str, secrets: tuple[str, ...], classify: Callable[[str], str | None]
) -> None:
    tpl = template(url, classify)
    record = PageRecord(
        page_no=1,
        url_redacted=redact_url(url, classify),
        depth=0,
        parent_page_no=None,
        status="queued",
        discovered_via="start",
        template=tpl,
    )
    for secret in secrets:
        assert secret not in record.template
        assert secret not in record.url_redacted


def test_template_keeps_routes_and_shares_ids() -> None:
    assert template("http://h/app#/nominee/ananya.k@x.in", _cls) == "h/app#/nominee/<EMAIL>"
    assert template("http://h/app#!/acct/SB-501002345678?b=1&a=2", _none) == (
        "h/app#!/acct/{id}?a&b"
    )
    assert template("http://h/x?50100234567812=on&page=2", _none) == "h/x?<k>&page"
    assert template("http://h/app;jsessionid=X1/p", _none) == template("http://h/app/p", _none)
    assert template("http://h/acct/SB-50100234567812", _none) == template(
        "http://h/acct/SB-60100234567899", _none
    )


@pytest.mark.parametrize(
    "tpl",
    [
        "h/customers/ananya.k@x.in/profile",
        "h/accounts/SB-50100234567812/statement",
        "h/x?50100234567812",
        "h/profile/Ananya%20Kapoor",
        "h/app#/nominee/ananya.k@x.in",
        "h/app/profile;jsessionid=A1B2C3D4",
        "h/acct/501-002-345-678/stmt",
        "h/acct/501.002.345.678/stmt",
        "h/phone/%2B91-987-654-321",
        "h/dob/12-05-90",
        "h/dob/1-5-90",
        "h/x?501-002-345-678",
        "h/x?page&12-05-90",
        "h/acct/501/002/345/678/stmt",
        "h/acct/1",
        "h/acct/50-1/00-2/34-5",
        "h/app#/acct/501-002-345-678",
        "h/app#!/x?501-002-345-678",
        "h/dob/12May90",
        "h/dob/12-may-90",
        "h/dob/May-12-90",
        "h/dob/12%20May%201990",
        "h/dob/1990-May-12",
        "h/dob/12-dic-1990",
        "h/dob/2nd-Jan-99",
        "h/dob/Dec%201,%2099",
        "h/dob/12-%E0%A4%AE%E0%A4%88-90",
        "h/x?page&12May90",
        "h/app#/dob/May-12-90",
        # G2-r2 B2: dates split over segments or with connector words, raw or half-templated
        "h/dob/1/5/90",
        "h/dob/12/may/90",
        "h/dob/may/12/90",
        "h/dob/12/May/1990",
        "h/dob/12-de-mayo-de-90",
        "h/dob/the-12th-of-may-90",
        "h/dob/{id}/may/{id}",
        "h/dob/may/{id}/{id}",
        "h/dob/{id}/May/{id}",
        "h/dob/{id}/de/mayo/de/{id}",
        "h/app#/dob/{id}/may/{id}",
    ],
)
def test_a_page_record_refuses_a_raw_template(tpl: str) -> None:
    assert not template_is_value_free(tpl)
    with pytest.raises(ValidationError) as err:
        PageRecord(
            page_no=1,
            url_redacted="http://h/",
            depth=0,
            parent_page_no=None,
            status="queued",
            discovered_via="start",
            template=tpl,
        )
    assert "ananya" not in str(err.value) and "50100234567812" not in str(err.value)
    assert "501" not in str(err.value) and "12-05" not in str(err.value)
    assert "May" not in str(err.value) and "may-90" not in str(err.value)


def test_value_free_templates_pass_the_record_guard() -> None:
    for tpl in (
        "demo-site:8410/apply/identity",
        "h:8410/accounts/statements/{id}?acct&month",
        "h/customers/<EMAIL>/profile",
        "h/x?<k>&page",
        "h/app#!/acct/{id}?a&b",
        "127.0.0.1:8410/",
        "h/docs/v1.2.3/api/v2/faq/{id}/x86_64",
        "h/calendar/{id}/{id}/{id}",
        "h/news/may/{id}",
        "h/archive/{id}/may",
    ):
        assert template_is_value_free(tpl), tpl


DENY = ("/logout", "/admin")


@pytest.mark.parametrize(
    "path",
    [
        "/Logout",
        "/LOGOUT",
        "/ADMIN/users",
        "/logout;jsessionid=1",
        "/admin;x/users",
        "/%6Cogout",
        "/%6cogout",
        "/%61dmin/users",
        "//logout",
        "/apply/../logout",
        "/apply/%2E%2E/admin",
        "/apply/..;/admin",
        "/./admin",
        "/apply%2F..%2Fadmin",
        "/apply\\..\\admin",
        "/logout/",
        "/admin;x%2F../users",  # Tomcat strips ";x%2F.." up to the next "/": /admin/users
        "/%2E%2E/admin",
        "/a%5C..%5Cadmin",
        # N-a: IIS/Windows trim trailing dots and spaces; a NUL or an encoded ";" ends the name
        "/logout.",
        "/logout%2e",
        "/logout%20",
        "/logout.%20.",
        "/admin%00",
        "/admin%00.html",
        "/admin%3b/x",
        "/admin%3Bx=1/users",
        "/admin..;/",
        "/admin%252e",
    ],
)
def test_deny_cannot_be_bypassed_by_case_matrix_encoding_or_dots(path: str) -> None:
    assert path_matches(path, DENY, fold_case=True)
    assert path_verdict(path, (), DENY) == "deny"
    scope = CrawlScope(
        start=Origin.of("http://demo-site:8410/apply"),
        same_origin=True,
        registered_hosts=frozenset({"demo-site"}),
        deny=DENY,
    )
    assert scope.navigation_verdict("http://demo-site:8410" + path) == "deny"


def test_allow_is_matched_on_the_normalised_path_case_sensitively() -> None:
    allow = ("/apply/*",)
    assert path_verdict("/apply/identity", allow, ()) == "ok"
    assert path_verdict("/apply;s=1/identity", allow, ()) == "ok"
    assert path_verdict("/apply/../admin", allow, ()) == "not_allowed"
    assert path_verdict("/apply/%2E%2E/admin", allow, ()) == "not_allowed"
    assert path_verdict("/APPLY/identity", allow, ()) == "not_allowed"  # fail closed
    assert path_verdict("/apply//identity", allow, ()) == "ok"
    assert path_verdict("/help", (), DENY) == "ok"
    assert path_verdict("/logoutx", (), DENY) == "ok"  # a deny stem covers sub-paths only
    assert path_matches("/help/faq/", ["/help/faq/*"])
    # every reading must be allowed: Tomcat reads /apply;x%2F../admin as /apply/admin, a server
    # that splits %2F first reads it as /admin
    assert path_verdict("/apply;x%2F../admin", allow, ()) == "not_allowed"
    assert path_verdict("/apply/a%2Fb", allow, ()) == "ok"


def test_a_hash_route_is_a_navigation_the_deny_rail_covers() -> None:
    scope = CrawlScope(
        start=Origin.of("http://demo-site:8410/app"),
        same_origin=True,
        registered_hosts=frozenset({"demo-site"}),
        allow=("/app",),
        deny=DENY,
    )
    for url in (
        "http://demo-site:8410/app#/logout",
        "http://demo-site:8410/app#!/admin",
        "http://demo-site:8410/app#/Admin/users?x=1",
        "http://demo-site:8410/app#/x/../logout",
        "http://demo-site:8410/app#!/%61dmin",
    ):
        assert scope.navigation_verdict(url) == "deny", url
    # allow applies to the URL path; the route is checked against deny only
    assert scope.navigation_verdict("http://demo-site:8410/app#/apply/identity") == "ok"
    assert scope.navigation_verdict("http://demo-site:8410/app#logout") == "ok"  # not a route
    assert scope.navigation_verdict("http://demo-site:8410/help#/apply") == "not_allowed"


@pytest.mark.parametrize(
    "path,expected",
    [
        ("", "/"),
        ("/", "/"),
        ("//a///b", "/a/b"),
        ("/a/./b/../c", "/a/c"),
        ("/a/b/..", "/a/"),
        ("/../../a", "/a"),
        ("/a;x=1/b;y", "/a/b"),
        ("/%7Euser/%41%62c", "/~user/Abc"),
        ("/a%20b/%3B", "/a%20b/%3B"),
        ("/a%2fb%5Cc", "/a/b/c"),
        ("/a/", "/a/"),
    ],
)
def test_normalise_path(path: str, expected: str) -> None:
    assert normalise_path(path) == expected
