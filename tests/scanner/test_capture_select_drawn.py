"""S2 captures-privacy r3 B1: a closed ``<select>`` is masked by what it draws, in three more shapes.

Round 2 made a select's shown value the label its selected option draws, but read it only for a
classic select whose selected option had a non-empty ``value`` and sat among the first 300
options. Each shape below drew a value that no capture read or masked — in the masked capture,
both obfuscated captures and every screen the crawler stores — even when the select's own label
("Account number") classified it as personal:

* **B1a** — the selected option's ``value`` is empty or blank (a server-rendered default such as
  ``<option value="" selected>`` holding the customer's account): what it draws was never read.
* **B1b** — the selected option sits past the first 300 options (long branch, beneficiary or
  account lists): the extraction never read it.
* **B1c** — a customizable select (``appearance: base-select``) draws its ``<button>`` — a
  ``<selectedcontent>`` clone of the selected option's *content*, or the author's own text — not
  the option's ``label``, and no census read text under a select.

Every value is made up (it stands for customer data) and every select is drawn on its own pastel
background: the plain screenshot shows the colour, a masked capture none of it. Pages are served by
a counting server (a non-GET request is a submission). The OCR tests read the masked capture, both
obfuscated captures and every screen the crawler stores against the plain screenshot as control.
"""

from __future__ import annotations

import socket
import threading
from collections import Counter
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from dps.config import Settings
from dps.engine.service import PrivacyEngine
from dps.layers.scanner.capture import shown_values
from dps.layers.scanner.classify import (
    classify_fields,
    draws_placeholder,
    sensitive_prefilled,
    shown_text,
    shown_texts,
)
from dps.layers.scanner.dom import extract_page, extraction_of, render_census
from dps.layers.scanner.models import OptionFacts
from dps.layers.scanner.runtime import MemoryScanSink
from tests.scanner.helpers import field_facts, guarded_context, make_runtime
from tests.scanner.test_capture_geometry import (
    _PSM,
    _masked,
    _obfuscated,
    _open,
    _pixels,
    _plain_shot,
)

#: Made-up values standing for customer data (none shares a word or a 4-digit run with the page).
SEL_PAN = "EVXPT1593Q"
SEL_EMAIL = "corvina.halloway@example.org"
SEL_ACCOUNT = "50100987654323"  # no detector reads it (not a card number either)
SEL_NAME = "Orsolya Brannigan"
#: rendered text that makes the crawler take an obfuscated capture of a page (tokenised there)
PAGE_EMAIL = "tamsin.dulverton@example.org"

#: the select's background (unique on every page; pastel, so the OCR reads the text on it)
SELECT_BG = (214, 245, 255)
_CSS = (
    "body{font:22px/1.5 Arial,Helvetica,sans-serif;margin:24px;color:#111;background:#fff}"
    "select{font:22px Arial,Helvetica,sans-serif;width:560px;padding:6px 10px;"
    "background:rgb(214,245,255)}"
)
#: a customizable select (Chromium 135+): the closed select draws its <button>
_CUSTOM = "<style>select.cs, select.cs::picker(select){appearance:base-select}</style>"


def _doc(body: str, *, head: str = "", statement: bool = False) -> str:
    """A page with one select in a form (and a rendered e-mail when ``statement``)."""
    lead = f"<p>Statement for {PAGE_EMAIL}</p>" if statement else ""
    return (
        "<!doctype html><html><head><meta charset='utf-8'><title>select</title>"
        f"<style>{_CSS}</style>{head}</head><body><h2>Transfer</h2>{lead}"
        f"<form method='post' action='/pay'><p>{body}</p></form></body></html>"
    )


def _select(label: str, name: str, options: str, *, attrs: str = "", cls: str = "") -> str:
    klass = f" class='{cls}'" if cls else ""
    return (
        f"<label for='s'>{label}</label><br>"
        f"<select id='s' name='{name}'{klass}{attrs}>{options}</select>"
    )


def _many(count: int, chosen: int, word: str, value: str) -> str:
    """``count`` options ``<word> NNNN`` with ``value`` selected at index ``chosen``."""
    return "".join(
        f"<option value='o{n}' selected>{value}</option>"
        if n == chosen
        else f"<option value='o{n}'>{word} {n:04d}</option>"
        for n in range(count)
    )


#: case -> (select markup, head, the value the select draws)
SELECTS: dict[str, tuple[str, str, str]] = {
    # B1a: the selected option's value is empty or blank
    "empty_value_pan": (
        _select(
            "Branch",
            "branch",
            f"<option value='' selected>{SEL_PAN}</option><option value='b2'>Other branch</option>",
        ),
        "",
        SEL_PAN,
    ),
    "blank_value_email": (
        _select(
            "Send to",
            "notify",
            f"<option value=' ' selected>{SEL_EMAIL}</option><option value='d2'>Desk two</option>",
        ),
        "",
        SEL_EMAIL,
    ),
    "empty_value_label": (
        _select(
            "Branch",
            "branch",
            f"<option value='' selected label='{SEL_PAN}'>x</option>"
            "<option value='b2'>Other branch</option>",
        ),
        "",
        SEL_PAN,
    ),
    "empty_value_account": (
        _select(
            "Account number",
            "account_number",
            f"<option value='' selected>{SEL_ACCOUNT}</option>"
            "<option value='x2'>Other account</option>",
        ),
        "",
        SEL_ACCOUNT,
    ),
    # B1b: the selected option sits past the first 300 options
    "over300_pan": (
        _select("Branch", "branch", _many(400, 350, "Branch", SEL_PAN)),
        "",
        SEL_PAN,
    ),
    "over300_email": (
        _select("Send to", "notify", _many(420, 333, "Desk", SEL_EMAIL)),
        "",
        SEL_EMAIL,
    ),
    "over300_account": (
        _select("Account number", "account_number", _many(400, 350, "Savings", SEL_ACCOUNT)),
        "",
        SEL_ACCOUNT,
    ),
    "over300_name": (
        _select(
            "Beneficiary name",
            "beneficiary_name",
            _many(400, 320, "Payee", SEL_NAME),
            attrs=" autocomplete='name'",
        ),
        "",
        SEL_NAME,
    ),
    # the control: the same list with the account selected at index 299 (masked before the fix)
    "at299_account": (
        _select("Account number", "account_number", _many(400, 299, "Savings", SEL_ACCOUNT)),
        "",
        SEL_ACCOUNT,
    ),
    # B1c: a customizable select draws its button, not the option's label
    "custom_selectedcontent": (
        _select(
            "Branch",
            "branch",
            "<button><selectedcontent></selectedcontent></button>"
            "<option value='a' label='Main branch'><span>Main branch</span></option>"
            f"<option value='b' label='Branch B' selected><span>Ref {SEL_PAN}</span></option>",
            cls="cs",
        ),
        _CUSTOM,
        SEL_PAN,
    ),
    "custom_button": (
        _select(
            "Branch",
            "branch",
            f"<button>Debit from {SEL_PAN}</button>"
            "<option value='a'>Main branch</option><option value='b' selected>Branch B</option>",
            cls="cs",
        ),
        _CUSTOM,
        SEL_PAN,
    ),
    # the label names nothing personal and no detector reads a bare account number: a
    # customizable select draws author content, so it is boxed whenever it draws text
    "custom_account": (
        _select(
            "Debit from",
            "debit_from",
            "<button><selectedcontent></selectedcontent></button>"
            "<option value='s' label='Savings'><span>Savings</span></option>"
            f"<option value='c' label='Current' selected><span>Current · {SEL_ACCOUNT}</span>"
            "</option>",
            cls="cs",
        ),
        _CUSTOM,
        SEL_ACCOUNT,
    ),
}
PAGES: dict[str, str] = {
    case: _doc(markup, head=head) for case, (markup, head, _) in SELECTS.items()
}
#: the same selects on a page that also renders an e-mail, so the crawler stores an obfuscated
#: capture of it whatever it decides about the select (the verifier's r3c pages)
STATEMENT_PAGES: dict[str, str] = {
    f"{case}_statement": _doc(markup, head=head, statement=True)
    for case, (markup, head, _) in SELECTS.items()
}
CRAWLED = ("empty_value_account", "over300_account", "custom_account", "custom_button")


@pytest.fixture
def select_site() -> Iterator[tuple[str, Counter[str]]]:
    """The pages on 127.0.0.1, counting every request by method and path."""
    hits: Counter[str] = Counter()
    pages = {f"/{k}": v.encode() for k, v in (PAGES | STATEMENT_PAGES).items()}

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


def _submitted(hits: Counter[str]) -> list[str]:
    return [k for k in hits if not k.startswith("GET ")]


# --- what a select shows, as classification and the masks read it ------------------------------
def _branch(options: list[OptionFacts], **kw: Any) -> Any:
    """A visible "Branch" select with ``options``, overridden by ``kw``."""
    base: dict[str, Any] = {
        "key": "0:1",
        "tag": "select",
        "input_type": "select-one",
        "name": "branch",
        "label": "Branch",
        "label_source": "label_for",
        "options": options,
    }
    return field_facts(**(base | kw))


def test_a_selected_option_without_a_value_still_shows_what_it_draws(
    scan_engine: PrivacyEngine,
) -> None:
    """B1a: an empty or blank ``value`` does not make a selected option invisible — the field
    shows its label (else its text), is classified by it and is masked as pre-filled. Whether the
    option is a real choice is the fill's rule (``personas_fill._usable``), not the capture's."""
    det = scan_engine.detector(scan_engine.policy(None), "en_IN")
    for value, text, label, entity in (
        ("", SEL_PAN, "", "PAN_IN"),
        (" ", SEL_EMAIL, "", "EMAIL"),
        ("", "x", SEL_PAN, "PAN_IN"),
    ):
        branch = _branch(
            [
                OptionFacts(value=value, text=text, label=label, disabled=False, selected=True),
                OptionFacts(value="b2", text="Other", label="", disabled=False, selected=False),
            ]
        )
        drawn = label or text
        assert shown_text(branch) == drawn and shown_texts(branch) == [drawn]
        classes = classify_fields([branch], detector=det, min_score=0.6)
        assert classes["0:1"].entity_type == entity, (value, entity, classes["0:1"].evidence)
        assert sensitive_prefilled([branch], classes, det) == [(branch, entity)]
        assert shown_values([(branch, entity)]) == [(drawn, entity)]
    # a select classified by its own label masks what it draws, even a placeholder-like text
    account = branch.model_copy(
        update={
            "label": "Account number",
            "name": "account_number",
            "options": [
                OptionFacts(value="", text=SEL_ACCOUNT, disabled=False, selected=True),
                OptionFacts(value="x2", text="Other account", disabled=False, selected=False),
            ],
        }
    )
    classes = classify_fields([account], detector=det, min_score=0.6)
    assert classes["0:1"].entity_type == "ACCOUNT_NUMBER"
    assert sensitive_prefilled([account], classes, det) == [(account, "ACCOUNT_NUMBER")]


def test_a_drawn_placeholder_is_masked_but_is_no_value_of_the_page(
    scan_engine: PrivacyEngine,
) -> None:
    """A personal select that draws its placeholder ("Choose…", an empty value) is masked with
    what it draws — whether an option is a real choice is the fill's rule — but a placeholder is no
    value the page shows: it is not hunted for elsewhere on the page (no other select or text that
    reads "Choose…" is boxed for it) and it alone does not make the page "arrive pre-filled" (no
    obfuscated capture of a form page for it). A value behind an empty ``value`` is not a
    placeholder, and neither is anything a customizable select draws."""
    det = scan_engine.detector(scan_engine.policy(None), "en_IN")
    gender = _branch(
        [
            OptionFacts(value="", text="Choose…", label="Choose…", disabled=False, selected=True),
            OptionFacts(value="F", text="Female", label="", disabled=False, selected=False),
        ],
        label="Gender",
        name="gender",
        autocomplete="sex",
    )
    classes = classify_fields([gender], detector=det, min_score=0.6)
    assert classes["0:1"].entity_type == "GENDER"
    assert sensitive_prefilled([gender], classes, det) == [(gender, "GENDER")]  # masked
    assert draws_placeholder(gender)
    assert shown_values([(gender, "GENDER")]) == []  # not swept elsewhere
    account = gender.model_copy(
        update={
            "label": "Account number",
            "name": "account_number",
            "autocomplete": None,
            "options": [OptionFacts(value="", text=SEL_ACCOUNT, disabled=False, selected=True)],
        }
    )
    assert not draws_placeholder(account)
    assert shown_values([(account, "ACCOUNT_NUMBER")]) == [(SEL_ACCOUNT, "ACCOUNT_NUMBER")]
    chosen = gender.model_copy(
        update={"options": [OptionFacts(value="F", text="Female", disabled=False, selected=True)]}
    )
    assert not draws_placeholder(chosen)  # a real choice is a value
    custom = gender.model_copy(update={"custom_select": True, "drawn": ["Choose…"]})
    assert not draws_placeholder(custom)
    past_the_list = gender.model_copy(update={"options": [], "drawn": [SEL_ACCOUNT]})
    assert not draws_placeholder(past_the_list)  # selected past the 300 options listed


def test_the_extraction_reads_what_a_select_draws_wherever_its_option_sits(
    scan_engine: PrivacyEngine,
) -> None:
    """B1b/B1c as classification sees them: ``FieldFacts.drawn`` — what the extraction read the
    select draw (every selected option, whatever its index, and a customizable select's button) —
    is what the field shows; a customizable select that draws text is masked whole even when
    nothing classifies what it draws."""
    det = scan_engine.detector(scan_engine.policy(None), "en_IN")
    capped = [
        OptionFacts(value=f"o{n}", text=f"Savings {n:04d}", disabled=False, selected=False)
        for n in range(300)
    ]
    account = _branch(capped, label="Account number", name="account_number", drawn=[SEL_ACCOUNT])
    assert shown_texts(account) == [SEL_ACCOUNT]
    classes = classify_fields([account], detector=det, min_score=0.6)
    assert sensitive_prefilled([account], classes, det) == [(account, "ACCOUNT_NUMBER")]
    custom = _branch(
        [OptionFacts(value="c", text="Current", label="Current", disabled=False, selected=True)],
        label="Debit from",
        name="debit_from",
        drawn=[f"Current · {SEL_ACCOUNT}", "Current"],
        custom_select=True,
    )
    classes = classify_fields([custom], detector=det, min_score=0.6)
    assert classes["0:1"].entity_type is None  # nothing names or detects a bare account number
    assert sensitive_prefilled([custom], classes, det) == [(custom, "SELECT")]
    assert shown_values([(custom, "SELECT")]) == [
        (f"Current · {SEL_ACCOUNT}", "SELECT"),
        ("Current", "SELECT"),
    ]
    nothing = custom.model_copy(update={"drawn": []})
    assert sensitive_prefilled([nothing], classes, det) == []  # draws nothing: nothing to mask


@pytest.mark.browser
async def test_the_extraction_reads_every_selected_option_and_a_customizable_button(
    chromium: Any, scan_settings: Settings, select_site: tuple[str, Counter[str]]
) -> None:
    """``EXTRACT_JS`` reads what each closed select draws: an option past the 300 it lists, an
    empty-valued option's text, and a customizable select's button (its ``<selectedcontent>``
    clone or the author's text); the census reads the text that button draws."""
    base, hits = select_site
    seen: dict[str, tuple[list[str] | None, bool, int]] = {}
    census: dict[str, bool] = {}
    async with guarded_context(chromium, scan_settings, base) as (ctx, _g):
        for case in (
            "empty_value_pan",
            "over300_account",
            "custom_selectedcontent",
            "custom_button",
        ):
            page = await _open(ctx, base, case)
            snap = await extract_page(page, iframes=True, shadow_dom=True, max_fields=400)
            (field,) = snap.fields
            seen[case] = (field.drawn, field.custom_select, len(field.options))
            ext = extraction_of(snap)
            items = await render_census(ext) if ext is not None else None
            census[case] = items is not None and any(SEL_PAN in it.text for it in items)
            await page.close()
    assert seen["empty_value_pan"] == ([SEL_PAN], False, 2)
    assert seen["over300_account"] == ([SEL_ACCOUNT], False, 300)  # the list stops at 300
    drawn, custom, _ = seen["custom_selectedcontent"]
    assert custom and drawn is not None and drawn[0] == f"Ref {SEL_PAN}" and "Branch B" in drawn
    drawn, custom, _ = seen["custom_button"]
    assert custom and drawn is not None and drawn[0] == f"Debit from {SEL_PAN}"
    assert census == {
        "empty_value_pan": False,  # a classic select's options are drawn by the select itself
        "over300_account": False,
        "custom_selectedcontent": True,
        "custom_button": True,
    }
    assert _submitted(hits) == []


@pytest.mark.browser
@pytest.mark.parametrize("case", list(SELECTS))
async def test_a_select_is_masked_by_what_it_draws(
    case: str,
    chromium: Any,
    scan_settings: Settings,
    scan_engine: PrivacyEngine,
    select_site: tuple[str, Counter[str]],
) -> None:
    """S2 captures-privacy r3 B1a/B1b/B1c: not one pixel of the select's background is left in
    the masked capture; the plain screenshot of the same page draws it."""
    base, hits = select_site
    det = scan_engine.detector(scan_engine.policy(None), "en_IN")
    async with guarded_context(chromium, scan_settings, base) as (ctx, _g):
        page = await _open(ctx, base, case)
        control = await _plain_shot(page)
        masked, prefilled = await _masked(page, det)
    assert _pixels(control, SELECT_BG) > 1000, case  # the control draws the select
    assert masked is not None, case
    assert _pixels(masked, SELECT_BG) == 0, (case, prefilled)
    assert _submitted(hits) == []


@pytest.mark.browser
@pytest.mark.tesseract
@pytest.mark.parametrize("case", list(SELECTS))
async def test_no_capture_reads_what_a_select_draws(
    case: str,
    chromium: Any,
    scan_settings: Settings,
    scan_engine: PrivacyEngine,
    select_site: tuple[str, Counter[str]],
) -> None:
    """S2 captures-privacy r3 B1 (OCR): the plain screenshot reads the select's value; the masked
    capture and both obfuscated captures read none of it (before the fix every one of them read
    it, except the index-299 control)."""
    from tests.scanner.test_capture_ocr import _ocr, _readable, _tesseract

    cmd = _tesseract()
    base, hits = select_site
    value = SELECTS[case][2]
    values, keys = {"select": value}, ("select",)
    det = scan_engine.detector(scan_engine.policy(None), "en_IN")
    outputs: dict[str, bytes | None] = {}
    async with guarded_context(chromium, scan_settings, base) as (ctx, _g):
        page = await _open(ctx, base, case)
        control = await _plain_shot(page)
        outputs["masked"], _ = await _masked(page, det)
    for mode in ("synthesize", "obfuscate"):
        async with guarded_context(chromium, scan_settings, base) as (ctx, guard):
            page = await _open(ctx, base, case)
            res = await _obfuscated(page, det, mode, scan_engine, guard, scan_settings)
            assert res.status == "captured", (case, mode, res.reason)
            outputs[mode] = res.png
    assert _readable(_ocr(cmd, control, psm=_PSM), values, keys, strict=True) == ["select"], case
    for name, png in outputs.items():
        assert png is not None, (case, name)
        seen = _readable(_ocr(cmd, png, psm=_PSM), values, keys, strict=True)
        assert seen == [], (case, name)
    assert _submitted(hits) == []


@pytest.mark.browser
@pytest.mark.tesseract
@pytest.mark.parametrize("mode", ["synthesize", "obfuscate"])
@pytest.mark.parametrize("case", CRAWLED)
async def test_no_screen_the_crawler_stores_reads_what_a_select_draws(
    case: str,
    mode: str,
    chromium: Any,
    scan_settings: Settings,
    scan_engine: PrivacyEngine,
    select_site: tuple[str, Counter[str]],
) -> None:
    """S2 captures-privacy r3 B1 through the crawler (``ScanRunner``, both modes): no stored
    screen — original, annotated, both filled screens, the masked capture — reads what the select
    draws; the plain screenshot of the page reads it, and the server received only GETs."""
    from dps.layers.scanner.crawler import ScanRunner
    from tests.scanner.test_capture_ocr import _ocr, _readable, _tesseract

    cmd = _tesseract()
    base, hits = select_site
    path = f"/{case}_statement"
    values, keys = {"select": SELECTS[case][2]}, ("select",)
    sink = MemoryScanSink()
    runtime = make_runtime(
        base, scan_settings, scan_engine, path=path, max_depth=0, mode=mode, cookies=False
    )
    await ScanRunner(
        settings=scan_settings, engine=scan_engine, sink=sink, browser=chromium, runtime=runtime
    ).run()
    page = sink.pages[1]
    expected = {"original", "annotated", "filled_synthesize", "filled_obfuscate", "masked_capture"}
    assert set(page.artefacts) >= expected, (page.status, sorted(page.artefacts))
    for variant in sorted(expected):
        _, png, _ = sink.artefacts[page.artefacts[variant]]
        seen = _readable(_ocr(cmd, png, psm=_PSM), values, keys, strict=True)
        assert seen == [], (case, mode, variant)
    assert _submitted(hits) == []
    async with guarded_context(chromium, scan_settings, base) as (ctx, _g):
        control = await _plain_shot(await _open(ctx, base, path.lstrip("/")))
    assert _readable(_ocr(cmd, control, psm=_PSM), values, keys, strict=True) == ["select"]
