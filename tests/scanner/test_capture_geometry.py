"""S2 captures-privacy r2: masks land where the full-page screenshot draws the values, wherever the
top page is scrolled, and a closed select's drawn label is masked.

* B1 — root scroll snapping (``html { scroll-snap-type: y mandatory | proximity }`` under a header)
  kept the page at its first snap position after the capture's ``scrollTo(0, 0)``, and every box
  Playwright measured (fields, selects, read frames, unread frames) was taken as if the page were
  at 0,0: masks landed 174 px above the values.
* B2 — an RTL or ``vertical-rl`` document wider than the viewport scrolls from its right edge
  (``scrollX`` 0 there), while the full-page screenshot starts at the left edge of the overflow:
  every mask landed ``scrollWidth - clientWidth`` px left of its value.
* B3 — a closed ``<select>`` draws its selected option's ``label`` attribute, which nothing read.
  Same class, found while fixing it: a select the fill chooses shows one of the page's own options
  (a fill cannot put a persona value into a select), so a personal one stays masked in the filled
  screens.

Every page is served by a counting server (a non-GET request it receives is a submission); the
cross-origin frame comes from the same server under the host name ``localhost``, so no capture
ever reads it. Each value is made up (it stands for customer data) and is drawn on its own pastel
background: a plain screenshot of the page shows every colour, the masked capture none of them,
whatever arithmetic placed the masks. The OCR tests read the masked capture, both obfuscated
captures and every screen the crawler stores against the same plain screenshot as the control.
"""

from __future__ import annotations

import io
import socket
import threading
from collections import Counter
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest
from PIL import Image

from dps.config import Settings
from dps.engine.service import PrivacyEngine
from dps.engine.synth.surrogates import generate_synthesis_seed, seed_bytes
from dps.engine.transform.crypto import generate_key_material
from dps.layers.scanner.capture import capture_screen, obfuscated_capture, shown_values
from dps.layers.scanner.classify import (
    classify_field,
    classify_fields,
    classify_rendered,
    sensitive_prefilled,
    shown_text,
)
from dps.layers.scanner.dom import extract_page, frame_map
from dps.layers.scanner.models import OptionFacts
from dps.layers.scanner.runtime import MemoryScanSink
from tests.scanner.helpers import (
    CENSUS_EMAIL,
    CENSUS_PAN,
    field_facts,
    guarded_context,
    make_runtime,
)

#: Made-up values standing for customer data, one per place a page can show one (no two share a
#: word the OCR test looks for, so a reading names the place that leaked).
TEXT_EMAIL = CENSUS_EMAIL  # rendered text of the top page
INPUT_PAN = CENSUS_PAN  # a pre-filled input
SELECT_ACCOUNT = "50100736218845"  # the pre-selected option of an "Account number" select
FRAME_EMAIL = "marisol.vantreek@inbox.net"  # text in a same-origin frame
FRAME_PAN = "BRXPY7314L"
XO_EMAIL = "quillon.berrow@mailhub.net"  # text in a cross-origin frame (never read)
XO_PAN = "CSXPW5527M"
LABEL_PAN = "DTXPV2468N"  # the label attribute a closed select draws (B3)

#: Tesseract reads sparse text (11) and whole blocks (3): mode 11 alone skips text inside a bordered
#: box such as an input.
_PSM = ("11", "3")
#: Each value's background (unique on every page; pastel, so the OCR reads the text on it).
COLOURS: dict[str, tuple[int, int, int]] = {
    "text": (255, 214, 245),
    "input": (255, 236, 200),
    "select": (214, 245, 255),
    "frame": (225, 255, 214),
    "cross_origin": (240, 228, 255),
    "label": (255, 250, 205),
}
#: what each coloured place holds
HOLDS: dict[str, tuple[str, ...]] = {
    "text": (TEXT_EMAIL,),
    "input": (INPUT_PAN,),
    "select": (SELECT_ACCOUNT,),
    "frame": (FRAME_EMAIL, FRAME_PAN),
    "cross_origin": (XO_EMAIL, XO_PAN),
    "label": (LABEL_PAN,),
}


def _bg(place: str) -> str:
    r, g, b = COLOURS[place]
    return f"background:rgb({r},{g},{b})"


_CSS = (
    "body{font:22px/1.5 Arial,Helvetica,sans-serif;margin:24px;color:#111;background:#fff}"
    "table{border-collapse:collapse} th,td{padding:6px 14px;text-align:start}"
    "input,select{font:22px Arial,Helvetica,sans-serif;width:520px;padding:6px 10px}"
    "iframe{display:block;width:900px;height:200px;border:0;margin:12px 0}"
)


def _doc(body: str, *, head: str = "", html: str = "") -> str:
    return (
        f"<!doctype html><html{html}><head><meta charset='utf-8'><title>geometry</title>"
        f"<style>{_CSS}</style>{head}</head><body>{body}</body></html>"
    )


def _text_row() -> str:
    return (
        "<table><tbody><tr><th>Email</th>"
        f"<td><span style='{_bg('text')}'>{TEXT_EMAIL}</span></td></tr></tbody></table>"
    )


def _values(*, text: bool = True) -> str:
    """A pre-filled PAN, a pre-selected account number, a cross-origin frame (``{xo}`` is the
    cross-origin base URL) and a same-origin frame below it, with the e-mail row unless ``text`` is
    off. (Each frame sits where a mask of the other lands when the masks are off by the snap.)"""
    return (
        "<h2>Profile</h2>"
        + (_text_row() if text else "")
        + "<form method='post' action='/save'>"
        + "<p><label for='pan'>PAN</label><br>"
        + f"<input id='pan' name='pan' value='{INPUT_PAN}' style='{_bg('input')}'></p>"
        + "<p><label for='acct'>Account number</label><br>"
        + f"<select id='acct' name='account_number' style='{_bg('select')}'>"
        + f"<option value=''>Choose</option><option selected>{SELECT_ACCOUNT}</option></select></p>"
        + "</form><iframe src='{xo}/inner_xo'></iframe><iframe src='/inner'></iframe>"
    )


_SNAP_BODY = (
    "<header style='height:150px;background:#ddd'>Bank header</header>"
    "<section style='min-height:900px;scroll-snap-align:start'>{values}</section>"
    "<section style='height:900px;scroll-snap-align:start;background:#f6f6f6'><h2>More</h2></section>"
)
_FILLER = "".join("<p>Statement line, nothing personal.</p>" for _ in range(30))

#: case -> page (``{xo}``: the cross-origin base URL)
PAGES: dict[str, str] = {
    "snap_mandatory": _doc(
        _SNAP_BODY.replace("{values}", _values()),
        head="<style>html{scroll-snap-type:y mandatory}</style>",
    ),
    "snap_proximity": _doc(
        _SNAP_BODY.replace("{values}", _values()),
        head="<style>html{scroll-snap-type:y proximity}</style>",
    ),
    # snapping the capture's stylesheet cannot switch off (an inline !important declaration wins
    # over it): the page stays at its snap area, and the masks must follow it there
    "snap_inline": _doc(
        _SNAP_BODY.replace("{values}", _values()),
        html=" style='scroll-snap-type:y mandatory !important'",
    ),
    # a statement table 2400 px wide: the document scrolls sideways from its right edge, and the
    # e-mail sits in the table's last cell, at the far left of the overflow
    "rtl_wide": _doc(
        "<h2>Statement</h2><table style='width:2400px'><tbody><tr><td style='width:400px'>first"
        "</td><td style='width:1600px'>middle</td>"
        f"<td><span style='{_bg('text')}'>{TEXT_EMAIL}</span></td></tr></tbody></table>"
        + _values(text=False),
        html=" dir='rtl'",
    ),
    # a vertical-rl document: columns run right to left, the fields sit in a horizontal island
    # at the far left of the overflow
    "vertical_rl": _doc(
        "<h2>Statement</h2>" + _FILLER + f"<div style='writing-mode:horizontal-tb;width:960px'>"
        f"{_values()}</div>",
        head="<style>html{writing-mode:vertical-rl}</style>",
    ),
    # B3: the selected option's label is what a closed select draws; its text is neutral
    "select_label": _doc(
        "<h2>Branch</h2><form method='post' action='/save'><p><label for='br'>Branch</label><br>"
        f"<select id='br' name='branch' style='{_bg('label')}'><option value=''>Choose</option>"
        f"<option selected label='{LABEL_PAN}'>x</option></select></p></form>"
    ),
}
#: Made-up PANs a "PAN of the payee" select lists; its placeholder is selected when the page loads,
#: so the original shows none of them — only the fill picks one.
PAYEE_PANS = ("KQXPA1357B", "LRXPB2468C", "MSXPC3579D")
FILL_SELECT = _doc(
    "<h2>Payout</h2><form method='post' action='/save'><p><label for='pp'>PAN of the payee</label>"
    "<br><select id='pp' name='payee_pan'><option value=''>Choose a payee</option>"
    + "".join(f"<option value='{n}'>{v}</option>" for n, v in enumerate(PAYEE_PANS, 1))
    + "</select></p><p><label for='rf'>Reference</label><br><input id='rf' name='reference'></p>"
    "</form>"
)

#: the coloured places each case shows
PLACES: dict[str, tuple[str, ...]] = {
    "snap_mandatory": ("text", "input", "select", "frame", "cross_origin"),
    "snap_proximity": ("text", "input", "select", "frame", "cross_origin"),
    "snap_inline": ("text", "input", "select", "frame", "cross_origin"),
    "rtl_wide": ("text", "input", "select", "frame", "cross_origin"),
    # the cross-origin frame sits out of view at the far left: Chromium paints an out-of-process
    # frame only where it is in view, so no full-page screenshot draws it (nothing to mask)
    "vertical_rl": ("text", "input", "select", "frame"),
    "select_label": ("label",),
}
_INNER = _doc(
    f"<p>Statement for <span style='{_bg('frame')}'>{FRAME_EMAIL}</span></p>"
    f"<p>PAN: <span style='{_bg('frame')}'>{FRAME_PAN}</span></p>"
)
_INNER_XO = _doc(f"<p>Statement for {XO_EMAIL}</p><p>PAN: {XO_PAN}</p>").replace(
    "background:#fff}", "background:rgb({},{},{})}}".format(*COLOURS["cross_origin"]), 1
)


@pytest.fixture
def geometry_site() -> Iterator[tuple[str, Counter[str]]]:
    """The pages on 127.0.0.1 and the cross-origin frame on ``localhost`` (same server, so the
    frame is another origin); counts every request by method and path."""
    hits: Counter[str] = Counter()
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    xo = f"http://localhost:{port}"
    pages = {f"/{k}": v.replace("{xo}", xo).encode() for k, v in PAGES.items()}
    pages["/fill_select"] = FILL_SELECT.encode()
    pages["/inner"] = _INNER.encode()
    pages["/inner_xo"] = _INNER_XO.encode()

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

    srv = ThreadingHTTPServer(("127.0.0.1", port), Site)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{port}", hits
    finally:
        srv.shutdown()
        srv.server_close()


def _pixels(png: bytes, rgb: tuple[int, int, int], tol: int = 2) -> int:
    """How many pixels of ``png`` have colour ``rgb`` (each channel within ``tol``)."""
    with Image.open(io.BytesIO(png)) as im:
        colours = im.convert("RGB").getcolors(maxcolors=1 << 24) or []
    return sum(n for n, c in colours if all(abs(a - b) <= tol for a, b in zip(c, rgb, strict=True)))


async def _open(ctx: Any, base: str, case: str) -> Any:
    page = await ctx.new_page()
    await page.goto(f"{base}/{case}")  # "load": every frame has loaded too
    await page.wait_for_timeout(150)
    return page


async def _plain_shot(page: Any) -> bytes:
    """What the browser draws, nothing masked: the positive control."""
    png: bytes = await page.screenshot(full_page=True, type="png", scale="css")
    return png


async def _masked(page: Any, det: Any) -> tuple[bytes | None, list[str]]:
    """The masked capture of the open page, and the entity types it masks as pre-filled."""
    snap = await extract_page(page, iframes=True, shadow_dom=True, max_fields=400)
    frames = await frame_map(page, iframes=True)
    classes = classify_fields(snap.fields, detector=det, min_score=0.6)
    prefilled = sensitive_prefilled(snap.fields, classes, det)
    png = await capture_screen(
        page,
        frames,
        snap,
        rendered=classify_rendered(snap.texts, detector=det),
        prefilled=prefilled,
        max_height=12000,
        detector=det,
    )
    return png, sorted(e for _, e in prefilled)


async def _obfuscated(
    page: Any, det: Any, mode: str, engine: PrivacyEngine, guard: Any, settings: Settings
) -> Any:
    snap = await extract_page(page, iframes=True, shadow_dom=True, max_fields=400)
    frames = await frame_map(page, iframes=True)
    classes = classify_fields(snap.fields, detector=det, min_score=0.6)
    return await obfuscated_capture(
        page,
        frames,
        snap,
        rendered=classify_rendered(snap.texts, detector=det),
        prefilled=sensitive_prefilled(snap.fields, classes, det),
        engine=engine,
        policy=engine.policy(None),
        mode=mode,  # type: ignore[arg-type]
        key=generate_key_material("key") if mode == "obfuscate" else None,
        seed=seed_bytes(generate_synthesis_seed()),
        locale="en_IN",
        guard=guard,
        settings=settings,
        page_no=1,
    )


@pytest.mark.browser
@pytest.mark.parametrize("case", list(PAGES))
async def test_masks_cover_every_value_where_the_screenshot_draws_it(
    case: str,
    chromium: Any,
    scan_settings: Settings,
    scan_engine: PrivacyEngine,
    geometry_site: tuple[str, Counter[str]],
) -> None:
    """S2 captures-privacy r2 B1/B2/B3: not one pixel of any value's background is left in the
    masked capture — a pre-filled input, a pre-selected select, rendered text, text in a
    same-origin frame, a cross-origin frame and a closed select's drawn label — on a page whose
    root snaps (mandatory, proximity), an RTL page and a vertical-rl page wider than the viewport.
    The plain screenshot of the same page shows every one of them."""
    base, hits = geometry_site
    det = scan_engine.detector(scan_engine.policy(None), "en_IN")
    async with guarded_context(chromium, scan_settings, base) as (ctx, _g):
        page = await _open(ctx, base, case)
        snapped = await page.evaluate("() => window.scrollY")
        control = await _plain_shot(page)
        masked, prefilled = await _masked(page, det)
        after = await page.evaluate("() => window.scrollY")
    if case in ("snap_mandatory", "snap_proximity"):
        # the root snapped below the header; the capture switched snapping off and shot the top
        assert snapped > 100 and after == 0, (snapped, after)
    if case == "snap_inline":
        assert snapped > 100 and after == snapped  # snapping stayed on: masks followed the page
    shown = {p: _pixels(control, COLOURS[p]) for p in PLACES[case]}
    assert all(n > 100 for n in shown.values()), shown  # the control draws every place
    assert masked is not None, case
    left = {p: _pixels(masked, COLOURS[p]) for p in PLACES[case]}
    assert left == dict.fromkeys(PLACES[case], 0), (case, prefilled, left)
    assert [k for k in hits if not k.startswith("GET ")] == []  # D4: nothing submitted


@pytest.mark.browser
@pytest.mark.tesseract
@pytest.mark.parametrize("case", list(PAGES))
async def test_no_value_is_readable_in_any_capture_off_the_scroll_origin(
    case: str,
    chromium: Any,
    scan_settings: Settings,
    scan_engine: PrivacyEngine,
    geometry_site: tuple[str, Counter[str]],
) -> None:
    """S2 captures-privacy r2 B1/B2/B3 (OCR): the plain screenshot reads every value; the masked
    capture and both obfuscated captures read none (before the fix the masked capture showed the
    pre-filled PAN and the frames' text, and even the obfuscated captures showed the selected
    account number, the cross-origin frame and the select's label)."""
    from tests.scanner.test_capture_ocr import _ocr, _readable, _tesseract

    cmd = _tesseract()
    base, hits = geometry_site
    values = {v: v for p in PLACES[case] for v in HOLDS[p]}
    keys = tuple(values)
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
    assert _readable(_ocr(cmd, control, psm=_PSM), values, keys, strict=True) == list(keys), case
    for name, png in outputs.items():
        assert png is not None, (case, name)
        seen = _readable(_ocr(cmd, png, psm=_PSM), values, keys, strict=name == "masked")
        assert seen == [], (case, name, seen)
    assert [k for k in hits if not k.startswith("GET ")] == []  # D4: nothing submitted


@pytest.mark.browser
@pytest.mark.tesseract
@pytest.mark.parametrize("mode", ["synthesize", "obfuscate"])
@pytest.mark.parametrize("case", ["snap_mandatory", "snap_inline", "rtl_wide", "select_label"])
async def test_every_screen_the_crawler_stores_is_masked_off_the_scroll_origin(
    case: str,
    mode: str,
    chromium: Any,
    scan_settings: Settings,
    scan_engine: PrivacyEngine,
    geometry_site: tuple[str, Counter[str]],
) -> None:
    """S2 captures-privacy r2 B1/B2/B3 through the crawler (``ScanRunner``, both modes): no stored
    screen — original, annotated, both filled screens, the masked capture — shows a value of a
    snapping page, of a wide RTL page or a closed select's label; the plain screenshot of the
    page shows all of them, and the server received nothing but GET requests."""
    from dps.layers.scanner.crawler import ScanRunner
    from tests.scanner.test_capture_ocr import _ocr, _readable, _tesseract

    cmd = _tesseract()
    base, hits = geometry_site
    values = {v: v for p in PLACES[case] for v in HOLDS[p]}
    keys = tuple(values)
    sink = MemoryScanSink()
    runtime = make_runtime(
        base, scan_settings, scan_engine, path=f"/{case}", max_depth=0, mode=mode, cookies=False
    )
    await ScanRunner(
        settings=scan_settings, engine=scan_engine, sink=sink, browser=chromium, runtime=runtime
    ).run()
    page = sink.pages[1]
    expected = {"original", "annotated", "filled_synthesize", "filled_obfuscate", "masked_capture"}
    assert set(page.artefacts) >= expected, (page.status, sorted(page.artefacts))
    for variant in sorted(expected):
        _, png, _ = sink.artefacts[page.artefacts[variant]]
        strict = variant in ("original", "annotated")
        seen = _readable(_ocr(cmd, png, psm=_PSM), values, keys, strict=strict)
        assert seen == [], (case, mode, variant, seen)
    assert [k for k in hits if not k.startswith("GET ")] == []  # D4: nothing submitted
    async with guarded_context(chromium, scan_settings, base) as (ctx, _g):
        control = await _plain_shot(await _open(ctx, base, case))
    assert _readable(_ocr(cmd, control, psm=_PSM), values, keys, strict=True) == list(keys), case


@pytest.mark.browser
@pytest.mark.tesseract
@pytest.mark.parametrize("mode", ["synthesize", "obfuscate"])
async def test_a_select_the_fill_chooses_never_shows_a_personal_option(
    mode: str,
    chromium: Any,
    scan_settings: Settings,
    scan_engine: PrivacyEngine,
    geometry_site: tuple[str, Counter[str]],
) -> None:
    """S2 fix r2 (B3's class, found while fixing it): a fill cannot put a persona value into a
    select — it picks one of the page's own options, so a filled screen showed whichever personal
    option it picked (a payee's PAN) in clear, although the original showed only the placeholder.
    A select classified personal stays masked in both filled screens; the control selects each
    option in a plain page and reads it."""
    from dps.layers.scanner.crawler import ScanRunner
    from tests.scanner.test_capture_ocr import _ocr, _readable, _tesseract

    cmd = _tesseract()
    base, hits = geometry_site
    values = {v: v for v in PAYEE_PANS}
    keys = tuple(values)
    sink = MemoryScanSink()
    runtime = make_runtime(
        base, scan_settings, scan_engine, path="/fill_select", max_depth=0, mode=mode, cookies=False
    )
    await ScanRunner(
        settings=scan_settings, engine=scan_engine, sink=sink, browser=chromium, runtime=runtime
    ).run()
    page = sink.pages[1]
    expected = {"original", "annotated", "filled_synthesize", "filled_obfuscate"}
    assert set(page.artefacts) >= expected, (page.status, sorted(page.artefacts))
    for variant in sorted(expected):
        _, png, _ = sink.artefacts[page.artefacts[variant]]
        seen = _readable(_ocr(cmd, png, psm=_PSM), values, keys, strict=True)
        assert seen == [], (mode, variant, seen)
    assert [k for k in hits if not k.startswith("GET ")] == []  # D4: nothing submitted
    async with guarded_context(chromium, scan_settings, base) as (ctx, _g):
        control = await _open(ctx, base, "fill_select")
        read: list[str] = []
        for n in range(1, len(PAYEE_PANS) + 1):
            await control.select_option("#pp", str(n))  # what the fill does: pick an option
            shot = await _plain_shot(control)
            read += _readable(_ocr(cmd, shot, psm=_PSM), values, keys, strict=True)
    assert sorted(read) == sorted(keys)  # each option is readable once selected


# --- B3: what a closed select shows is its selected option's label --------------------------------
def test_a_closed_select_shows_and_is_classified_by_its_option_label(
    scan_engine: PrivacyEngine,
) -> None:
    """A selected option whose ``label`` attribute holds a PAN and whose text is neutral: the field
    shows the label (what the browser draws), is classified by it and is masked as pre-filled; an
    option without a label still shows its text."""
    det = scan_engine.detector(scan_engine.policy(None), "en_IN")
    branch = field_facts(
        key="0:1",
        tag="select",
        input_type="select-one",
        name="branch",
        label="Branch",
        label_source="label_for",
        options=[
            OptionFacts(value="", text="Choose", label="Choose", disabled=False, selected=False),
            OptionFacts(value="x", text="x", label=LABEL_PAN, disabled=False, selected=True),
        ],
    )
    assert shown_text(branch) == LABEL_PAN
    assert shown_values([(branch, "PAN_IN")]) == [(LABEL_PAN, "PAN_IN")]
    cls = classify_field(branch, detector=det, min_score=0.6)
    classes = classify_fields([branch], detector=det, min_score=0.6)
    assert classes["0:1"].entity_type == "PAN_IN", (cls.entity_type, classes["0:1"].evidence)
    assert sensitive_prefilled([branch], classes, det) == [(branch, "PAN_IN")]
    plain = branch.model_copy(
        update={
            "options": [
                OptionFacts(value="x", text="Savings", label="", disabled=False, selected=True)
            ]
        }
    )
    assert shown_text(plain) == "Savings"  # no label: the browser draws the text


@pytest.mark.browser
async def test_the_extraction_reads_what_each_option_draws(
    chromium: Any, scan_settings: Settings, geometry_site: tuple[str, Counter[str]]
) -> None:
    """``EXTRACT_JS`` keeps each option's label (what a select draws for it), which falls back to
    its text when the option has none."""
    base, _hits = geometry_site
    async with guarded_context(chromium, scan_settings, base) as (ctx, _g):
        page = await _open(ctx, base, "select_label")
        snap = await extract_page(page, iframes=True, shadow_dom=True, max_fields=400)
    (branch,) = [f for f in snap.fields if f.name == "branch"]
    assert [(o.text, o.label, o.selected) for o in branch.options] == [
        ("Choose", "Choose", False),
        ("x", LABEL_PAN, True),
    ]
    assert shown_text(branch) == LABEL_PAN
