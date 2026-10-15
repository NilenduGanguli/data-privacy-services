"""Task 14: the crawler — budgets, SPA clicks, the per-page pipeline, sinks. Every browser test
counts what the demo site received."""

from __future__ import annotations

import json
from collections import Counter
from typing import Any
from urllib.parse import urlsplit

import pytest

from dps.config import Settings
from dps.engine.service import PrivacyEngine
from dps.engine.verify import normalise
from dps.layers.scanner.models import PageRecord
from dps.layers.scanner.runtime import LeaseLost, MemoryScanSink, RunOutcome, ScanRuntime
from tests.scanner.helpers import DEMO_VALUE_KEYS, make_runtime, received


def _by_path(sink: MemoryScanSink) -> dict[str, PageRecord]:
    return {urlsplit(p.url_redacted).path: p for p in sink.pages.values() if p.status != "skipped"}


def _runner(**kw: Any) -> Any:
    from dps.layers.scanner.crawler import ScanRunner

    return ScanRunner(**kw)


def test_runtime_and_targets_never_show_secrets_or_urls(
    scan_settings: Settings, scan_engine: PrivacyEngine
) -> None:
    rt = make_runtime(
        "http://127.0.0.1:8410", scan_settings, scan_engine, path="/apply?email=ananya%40x.in"
    )
    shown = repr(rt)
    assert "ananya" not in shown and rt.key.dek not in shown and rt.seed.seed not in shown
    assert "demo-valid" not in shown and "127.0.0.1:8410" in shown
    assert isinstance(rt, ScanRuntime) and RunOutcome().fills_skipped == 0
    crawler = pytest.importorskip("dps.layers.scanner.crawler")
    t = crawler.Target(page_no=1, url=rt.start_url, depth=0, parent_page_no=None, via="start")
    assert "ananya" not in repr(t)


async def test_memory_sink_stops_after_n_progress_calls() -> None:
    sink = MemoryScanSink(stop_after_pages=2)
    assert await sink.progress({}) is True
    assert await sink.progress({}) is False
    assert await sink.save_artefact(kind="x", data=b"1", content_type="t", meta={}) == "a1"


@pytest.mark.browser
@pytest.mark.slow
async def test_full_demo_crawl_in_memory(
    chromium: Any,
    scan_settings: Settings,
    scan_engine: PrivacyEngine,
    demo_site_url: str,
    demo_data: dict[str, str],
) -> None:
    before = received(demo_site_url)
    sink = MemoryScanSink()
    runtime = make_runtime(demo_site_url, scan_settings, scan_engine)
    outcome = await _runner(
        settings=scan_settings, engine=scan_engine, sink=sink, browser=chromium, runtime=runtime
    ).run()
    after = received(demo_site_url)
    pages = _by_path(sink)
    assert {
        "/apply",
        "/",
        "/apply/identity",
        "/apply/address",
        "/apply/employment",
        "/apply/card",
        "/apply/documents",
        "/apply/us",
        "/apply/review",
        "/login",
        "/profile",
        "/profile/contact",
        "/app",
        "/app/nominee",
        "/app/preferences",
        "/help",
        "/help/contact-us",
        "/help/track",
    } <= set(pages)
    assert pages["/app/preferences"].discovered_via == "spa_click" and outcome.spa_routes >= 1
    skipped = {
        (p.status_reason, p.url_redacted) for p in sink.pages.values() if p.status == "skipped"
    }
    assert {r for r, _ in skipped} >= {"deny", "other_origin", "template"}
    faq_done = [
        p for p in sink.pages.values() if "/help/faq/" in p.url_redacted and p.status == "done"
    ]
    assert len(faq_done) == 3
    # nothing was submitted, the deny list held, and the demo's POSTs were blocked by the guard
    keys = ("non_get", "form_get", "logout_hits", "admin_hits")
    assert [after[k] for k in keys] == [before[k] for k in keys]
    assert after["by_path"] == before["by_path"]
    blocked: Counter[tuple[str, str]] = Counter()
    for r in sink.requests:
        if r.decision == "blocked":
            blocked[(r.method, r.reason)] += r.count
    assert blocked[("POST", "method")] >= 3 and blocked[("GET", "host")] >= 1  # autosaves; pixel
    assert not [
        r for r in sink.requests if r.decision == "allowed" and r.method not in ("GET", "HEAD")
    ]
    # the identity page: classification, parallel view, validation evidence
    ident = pages["/apply/identity"]
    fields = sink.fields[ident.page_no]
    assert [f.entity_type for f in fields] == [
        "PERSON_NAME",
        "DATE_OF_BIRTH",
        "AADHAAR",
        "PAN_IN",
        "PHONE",
        "EMAIL",
        "STREET_ADDRESS",
        "GENDER",
        "NATIONALITY",
    ]
    assert set(ident.artefacts) >= {
        "original",
        "annotated",
        "filled_synthesize",
        "filled_obfuscate",
    }
    assert ident.validation["synthesize"]["rejected"] == 0
    assert all(f.fill_synthesize and f.fill_obfuscate for f in fields)
    assert all(f.fill_status == "filled" for f in fields)
    aadhaar = fields[2]
    assert aadhaar.fill_synthesize != aadhaar.fill_obfuscate and aadhaar.obfuscate_method
    for aid in ident.artefacts.values():
        kind, data, meta = sink.artefacts[aid]
        assert (
            kind == "screenshot"
            and data.startswith(b"\x89PNG")
            and meta["page_no"] == ident.page_no
        )
    # risk flags where the demo planted them
    kinds = {
        (urlsplit(p.url_redacted).path, f.flag) for p in sink.pages.values() for f in p.risk_flags
    }
    assert {
        ("/apply/identity", "autocomplete_enabled_sensitive"),
        ("/apply/review", "pii_in_query_string"),
        ("/profile", "pii_rendered_unmasked"),
        ("/login", "password_without_autocomplete_token"),
        ("/help/track", "sensitive_form_uses_get"),
    } <= kinds
    assert pages["/profile"].capture_status == "captured"
    assert "masked_capture" in pages["/profile"].artefacts
    shadow = next(f for f in sink.fields[pages["/apply/address"].page_no] if f.in_shadow)
    card = [f for f in sink.fields[pages["/apply/card"].page_no] if f.frame_index == 1]
    assert shadow.entity_type == "POSTAL_CODE"
    assert {f.entity_type for f in card} >= {"CARD_PAN", "CVV", "CARD_EXPIRY"}
    # the credential is filled but never shown
    login = sink.fields[pages["/login"].page_no]
    password = next(f for f in login if f.input_type == "password")
    assert password.fill_synthesize is None and password.fill_obfuscate is None
    # every visit was audited and logged as a navigation event
    navigations = [e for e in sink.events if e.kind == "navigate"]
    assert sink.navigations == len(navigations) >= 18
    assert [e.seq for e in sink.events] == sorted({e.seq for e in sink.events})
    # no page value anywhere in what would be persisted (rows, events, request log, capture maps)
    persisted = normalise(
        json.dumps(
            [
                [p.model_dump(mode="json") for p in sink.pages.values()],
                [[f.model_dump(mode="json") for f in fs] for fs in sink.fields.values()],
                [e.model_dump(mode="json") for e in sink.events],
                [r.model_dump(mode="json") for r in sink.requests],
            ]
        )
    )
    persisted += normalise(
        " ".join(
            d.decode() for _, d, m in sink.artefacts.values() if m.get("variant") == "reveal_map"
        )
    )
    for k in (*DEMO_VALUE_KEYS, "customer_id", "account_number", "application_ref"):
        if k in demo_data:
            assert normalise(demo_data[k]) not in persisted, k


@pytest.mark.browser
async def test_page_budget_and_depth_limit(
    chromium: Any, scan_settings: Settings, scan_engine: PrivacyEngine, demo_site_url: str
) -> None:
    sink = MemoryScanSink()
    runtime = make_runtime(
        demo_site_url, scan_settings, scan_engine, max_pages=4, spa=False, screenshots=False
    )
    out = await _runner(
        settings=scan_settings, engine=scan_engine, sink=sink, browser=chromium, runtime=runtime
    ).run()
    assert out.pages_visited == 4 and out.truncated == "pages"
    assert any(p.status_reason == "budget" for p in sink.pages.values())
    assert not [p for p in sink.pages.values() if p.status in ("queued", "visiting")]
    sink0 = MemoryScanSink()
    runtime0 = make_runtime(demo_site_url, scan_settings, scan_engine, max_depth=0)
    await _runner(
        settings=scan_settings, engine=scan_engine, sink=sink0, browser=chromium, runtime=runtime0
    ).run()
    assert [p.status for p in sink0.pages.values()] == ["done"]


@pytest.mark.browser
async def test_without_a_session_profile_redirects_to_login(
    chromium: Any, scan_settings: Settings, scan_engine: PrivacyEngine, demo_site_url: str
) -> None:
    sink = MemoryScanSink()
    runtime = make_runtime(
        demo_site_url,
        scan_settings,
        scan_engine,
        path="/profile",
        cookies=False,
        max_pages=2,
        spa=False,
        screenshots=False,
    )
    await _runner(
        settings=scan_settings, engine=scan_engine, sink=sink, browser=chromium, runtime=runtime
    ).run()
    first = sink.pages[1]
    assert (first.status, first.status_reason) == ("redirected", "auth_redirect")
    assert "/login" in (first.final_url_redacted or "")
    login = next(p for p in sink.pages.values() if p.discovered_via == "redirect")
    assert login.status == "done" and login.depth == 0


@pytest.mark.browser
async def test_lease_loss_stops_the_crawl(
    chromium: Any, scan_settings: Settings, scan_engine: PrivacyEngine, demo_site_url: str
) -> None:
    sink = MemoryScanSink(stop_after_pages=1)
    runtime = make_runtime(demo_site_url, scan_settings, scan_engine, screenshots=False)
    with pytest.raises(LeaseLost):
        await _runner(
            settings=scan_settings, engine=scan_engine, sink=sink, browser=chromium, runtime=runtime
        ).run()
    assert sum(1 for p in sink.pages.values() if p.status == "done") == 1


@pytest.mark.browser
async def test_dialogs_dismissed_and_popups_closed(
    chromium: Any, scan_settings: Settings, scan_engine: PrivacyEngine, demo_site_url: str
) -> None:
    sink = MemoryScanSink()
    runtime = make_runtime(
        demo_site_url, scan_settings, scan_engine, path="/fixtures/dialogs", max_depth=0, spa=False
    )
    out = await _runner(
        settings=scan_settings, engine=scan_engine, sink=sink, browser=chromium, runtime=runtime
    ).run()
    assert out.dialogs_dismissed >= 1 and sink.pages[1].status == "done"
    assert out.popups_closed in (0, 1)  # Chromium's popup blocker may stop window.open first
    assert [p.status for p in sink.pages.values()] == ["done"]  # the popup was never followed
    assert any(e.kind == "dialog" and e.note_code == "alert" for e in sink.events)


@pytest.mark.browser
async def test_spa_route_is_replayed_and_the_time_budget_holds(
    chromium: Any, scan_settings: Settings, scan_engine: PrivacyEngine, demo_site_url: str
) -> None:
    sink = MemoryScanSink()
    runtime = make_runtime(
        demo_site_url, scan_settings, scan_engine, path="/app", max_depth=1, screenshots=False
    )
    out = await _runner(
        settings=scan_settings, engine=scan_engine, sink=sink, browser=chromium, runtime=runtime
    ).run()
    pages = _by_path(sink)
    prefs = pages["/app/preferences"]
    assert prefs.discovered_via == "spa_click" and prefs.status == "done" and out.spa_routes == 1
    assert sink.fields[prefs.page_no] and any(e.kind == "spa" for e in sink.events)
    # a scan that runs out of time marks what it did not reach and never raises
    quick = scan_settings.model_copy(update={"scan_job_timeout_seconds": 1})
    sink2 = MemoryScanSink()
    out2 = await _runner(
        settings=quick, engine=scan_engine, sink=sink2, browser=chromium, runtime=runtime
    ).run()
    assert out2.truncated == "time"
    assert not [p for p in sink2.pages.values() if p.status in ("queued", "visiting")]


@pytest.mark.browser
@pytest.mark.tesseract
@pytest.mark.parametrize("mode", ["synthesize", "obfuscate"])
async def test_every_screen_the_crawler_stores_holds_no_readable_value(
    mode: str,
    chromium: Any,
    scan_settings: Settings,
    scan_engine: PrivacyEngine,
    demo_site_url: str,
    demo_data: dict[str, str],
) -> None:
    """OCR of every PNG a crawl of the pre-filled and the rendering pages stores (original,
    annotated, both filled screens, the masked capture): no demo value is readable. Positive
    control: the same crawl with unmasked originals (local only) shows them to the OCR."""
    from tests.scanner.test_capture_ocr import PAGES, _ocr, _readable, _tesseract

    cmd = _tesseract()
    for path in ("/profile/contact", "/profile"):
        keys = PAGES[path]
        sink = MemoryScanSink()
        runtime = make_runtime(
            demo_site_url, scan_settings, scan_engine, path=path, max_depth=0, mode=mode
        )
        await _runner(
            settings=scan_settings, engine=scan_engine, sink=sink, browser=chromium, runtime=runtime
        ).run()
        page = sink.pages[1]
        expected = {"original", "annotated", "masked_capture"}
        if path == "/profile/contact":
            expected |= {"filled_synthesize", "filled_obfuscate"}
        assert set(page.artefacts) >= expected, sorted(page.artefacts)
        for variant in expected:
            _, png, _ = sink.artefacts[page.artefacts[variant]]
            strict = variant in ("original", "annotated")
            assert _readable(_ocr(cmd, png), demo_data, keys, strict=strict) == [], variant
        control = MemoryScanSink()
        raw = scan_settings.model_copy(update={"scan_store_unmasked_originals": True})
        await _runner(
            settings=raw, engine=scan_engine, sink=control, browser=chromium, runtime=runtime
        ).run()
        _, png, _ = control.artefacts[control.pages[1].artefacts["original"]]
        seen = _readable(_ocr(cmd, png), demo_data, keys, strict=True)
        assert len(seen) >= max(2, len(keys) // 2), seen


# --- S2 captures-privacy r1 B1: a fill that scrolls a same-origin frame ---------------------------
#: A card step whose form is in a same-origin frame, below that frame's fold, with an e-mail and a
#: PAN rendered above the field (made-up values). Filling the field scrolls the frame, so both
#: filled screens show the values: their masks were placed as if the frame were not scrolled.
_CARD_STEP = (
    b"<!doctype html><html><head><meta charset='utf-8'><title>card step</title><style>"
    b"body{font:22px/1.5 Arial,Helvetica,sans-serif;margin:24px;color:#111;background:#fff}"
    b"</style></head><body><h2>card step</h2><p>Enter your card details below.</p>"
    b"<iframe src='/card/widget' style='width:900px;height:360px;border:0'></iframe></body></html>"
)


def _card_widget() -> bytes:
    from tests.scanner.helpers import CENSUS_EMAIL, CENSUS_PAN

    return (
        "<!doctype html><html><head><meta charset='utf-8'><title>card widget</title><style>"
        "body{font:22px/1.5 Arial,Helvetica,sans-serif;margin:24px;color:#111;background:#fff}"
        "input{font-size:22px;width:500px} .big{font-size:26px}</style></head><body>"
        "<h2>card widget</h2><div style='height:700px'>Card terms and conditions apply.</div>"
        f"<p class='big'>Statement for {CENSUS_EMAIL}</p><p class='big'>PAN: {CENSUS_PAN}</p>"
        "<form method='post' action='/card/save'><p><label for='cn'>Name on card</label><br>"
        "<input id='cn' name='ccName' autocomplete='cc-name'></p></form>"
        "<div style='height:40px'></div></body></html>"
    ).encode()


@pytest.fixture
def card_site() -> Any:
    """A counting server for the card step (any non-GET request it receives is a submission)."""
    import socket
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    hits: Counter[str] = Counter()
    pages = {"/card": _CARD_STEP, "/card/widget": _card_widget()}

    class Site(BaseHTTPRequestHandler):
        def _count(self) -> None:
            hits[f"{self.command} {self.path.split('?')[0]}"] += 1

        def do_GET(self) -> None:  # noqa: N802 - http.server's name
            self._count()
            body = pages.get(self.path.split("?")[0], b"")
            self.send_response(200 if body else 404)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:  # noqa: N802
            self._count()
            self.send_response(204)
            self.end_headers()

        def log_message(self, *a: Any) -> None:
            pass

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    srv = ThreadingHTTPServer(("127.0.0.1", port), Site)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{port}", hits
    finally:
        srv.shutdown()
        srv.server_close()


@pytest.mark.browser
@pytest.mark.tesseract
@pytest.mark.parametrize("mode", ["synthesize", "obfuscate"])
async def test_a_fill_that_scrolls_a_frame_never_shows_its_values(
    mode: str,
    chromium: Any,
    scan_settings: Settings,
    scan_engine: PrivacyEngine,
    card_site: tuple[str, Counter[str]],
) -> None:
    """S2 captures-privacy r1 B1 (``iframe_fill_scroll``): the crawler fills the card form in a
    same-origin frame, which scrolls that frame onto the e-mail and PAN above the field. No stored
    screen — original, annotated, both filled screens, the masked capture — shows either value; the
    control (the same page, the field filled the same way, shot unmasked) shows both, so the frame
    really is scrolled onto them. Nothing is submitted."""
    from tests.scanner.helpers import CENSUS_EMAIL, CENSUS_PAN
    from tests.scanner.test_capture_ocr import _ocr, _readable, _tesseract

    cmd = _tesseract()
    base, hits = card_site
    values = {"email": CENSUS_EMAIL, "pan": CENSUS_PAN}
    keys = tuple(values)
    sink = MemoryScanSink()
    runtime = make_runtime(
        base, scan_settings, scan_engine, path="/card", max_depth=0, mode=mode, cookies=False
    )
    await _runner(
        settings=scan_settings, engine=scan_engine, sink=sink, browser=chromium, runtime=runtime
    ).run()
    page = sink.pages[1]
    expected = {"original", "annotated", "filled_synthesize", "filled_obfuscate"}
    assert set(page.artefacts) >= expected, sorted(page.artefacts)
    stored = expected | ({"masked_capture"} & set(page.artefacts))
    for variant in sorted(stored):
        _, png, _ = sink.artefacts[page.artefacts[variant]]
        strict = variant in ("original", "annotated")
        assert _readable(_ocr(cmd, png), values, keys, strict=strict) == [], variant
    assert [k for k in hits if not k.startswith("GET ")] == []  # D4: nothing submitted
    ctx = await chromium.new_context(viewport={"width": 1280, "height": 900})
    try:
        control = await ctx.new_page()
        await control.goto(f"{base}/card")
        widget = next(f for f in control.frames if f.url.endswith("/card/widget"))
        await widget.locator("#cn").fill("Jordan Ellis")  # what the fill does: it scrolls the frame
        shot = await control.screenshot(full_page=True)
    finally:
        await ctx.close()
    assert _readable(_ocr(cmd, shot), values, keys, strict=True) == list(keys)
