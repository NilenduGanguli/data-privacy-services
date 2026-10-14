"""M4-G3 review r2 fixes: a capture's own census of every rendered string (B1-r2) and text nodes
read in full up to a read cap, with every capture withheld past it (B3).

* B1-r2: a copy of a detected value in a ``<button>``, an ``<input type=button>`` label or a
  visible listbox option is boxed; one rendered a character per node withholds. The same holds in
  both obfuscated captures (the census runs after the DOM replacement).
* B3: a value after the 2000th character of a text node is read, classified and masked; a text
  node longer than ``dom.TEXT_READ_CAP`` (and a field extraction cut at ``max_fields``) withholds
  every capture; a change in the tail of a long node after extraction is seen by the census.

The values are made up and stand for customer data; assertions look at pixels and counts only.
"""

from __future__ import annotations

import io
from typing import Any

import pytest
from PIL import Image

from dps.config import Settings
from dps.engine.service import PrivacyEngine
from dps.engine.synth.surrogates import generate_synthesis_seed, seed_bytes
from dps.engine.transform.crypto import generate_key_material
from dps.engine.verify import normalise
from dps.layers.scanner.capture import MASK_FILL, CaptureResult, capture_screen, obfuscated_capture
from dps.layers.scanner.classify import classify_field, classify_rendered, sensitive_prefilled
from dps.layers.scanner.dom import extract_page, frame_map
from dps.layers.scanner.models import PageSnapshot
from tests.scanner.helpers import (
    CENSUS_EMAIL,
    CENSUS_EMAIL2,
    CENSUS_FILLER,
    CENSUS_NAME,
    CENSUS_OBFUSCATED_WITHHELD,
    CENSUS_PAGES,
    FRAME_CASES,
    FRAME_JS,
    INJECT_JS,
    frame_case_args,
    guarded_context,
)

pytestmark = pytest.mark.browser

#: The box of the first text range holding ``needle`` under element ``id`` or its open shadow
#: root (document coords).
RANGE_JS = """([id, needle]) => {
  const el = document.getElementById(id);
  let n = null;
  for (const root of [el, el.shadowRoot].filter(Boolean)) {
    const it = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    n = it.nextNode();
    while (n && !n.nodeValue.includes(needle)) n = it.nextNode();
    if (n) break;
  }
  if (!n) return null;
  const at = n.nodeValue.indexOf(needle);
  const range = document.createRange();
  range.setStart(n, at); range.setEnd(n, at + needle.length);
  const r = range.getBoundingClientRect();
  return { x: r.left + window.scrollX, y: r.top + window.scrollY, w: r.width, h: r.height };
}"""
#: The box of element ``id`` (document coords).
BOX_JS = """(id) => {
  const r = document.getElementById(id).getBoundingClientRect();
  return { x: r.left + window.scrollX, y: r.top + window.scrollY, w: r.width, h: r.height };
}"""


def _covered(png: bytes, r: dict[str, float]) -> float:
    """Share of sampled pixels inside ``r`` that are mask fill (the entity label is drawn in
    another colour, so a fully masked box scores below 1.0 but far above 0)."""
    im = Image.open(io.BytesIO(png)).convert("RGB")
    xs = range(int(r["x"]) + 1, int(r["x"] + r["w"]) - 1, 2)
    ys = range(int(r["y"]) + 1, int(r["y"] + r["h"]) - 1, 2)
    points = [(x, y) for x in xs for y in ys if x < im.width and y < im.height]
    assert points
    return sum(1 for p in points if im.getpixel(p) == MASK_FILL) / len(points)


async def _page(ctx: Any, url: str, html: str | None) -> Any:
    page = await ctx.new_page()
    await page.goto(f"{url}/help")
    await page.wait_for_load_state("load")
    if html is not None:
        await page.evaluate(INJECT_JS, html)
    return page


async def _read(page: Any, *, max_fields: int = 400) -> tuple[PageSnapshot, dict[int, Any]]:
    snap = await extract_page(page, iframes=True, shadow_dom=True, max_fields=max_fields)
    return snap, await frame_map(page, iframes=True)


#: case -> the second copies to check: ("range", element id, needle) or ("box", element id, "").
COPIES: dict[str, list[tuple[str, str, str]]] = {
    "button": [("range", "c-b1", CENSUS_NAME), ("range", "c-b2", CENSUS_EMAIL)],
    "input_button": [("box", "c-in", "")],
    "listbox": [("box", "c-opt", "")],
    # M4 verify r1 B4: no box of the parent's own
    "contents": [("range", "c-ct", CENSUS_NAME), ("range", "c-cd", CENSUS_EMAIL)],
    "slotted": [("range", "c-sl1", CENSUS_NAME), ("range", "c-sl2", CENSUS_EMAIL)],
    "slot_fallback": [("range", "c-fb", CENSUS_NAME)],
    "contents_only": [("range", "c-co", CENSUS_EMAIL)],
    "slotted_only": [("range", "c-so", CENSUS_EMAIL)],
    # M4 verify r1 B5: drawn from attributes
    "alt": [("box", "c-alt", "")],
    "alt_only": [("box", "c-ao", "")],
    "input_image": [("box", "c-ii", "")],
    "optgroup": [("box", "c-og", "")],
    "option_label": [("box", "c-ol", "")],
    "placeholder": [("box", "c-ph", "")],
}
#: COPIES cases whose only copy is the one checked: nothing in a table finds it first
_ONLY = {"contents_only", "slotted_only", "alt_only"}


async def _copy_rects(
    page: Any, case: str, *, rewritten_ok: bool = False
) -> list[dict[str, float] | None]:
    """The boxes of ``case``'s copies. ``rewritten_ok`` (obfuscated captures): a text copy that is
    no longer in the DOM was read by the extraction and rewritten whole (``None``)."""
    out: list[dict[str, float] | None] = []
    for kind, id_, needle in COPIES[case]:
        rect = await (
            page.evaluate(RANGE_JS, [id_, needle])
            if kind == "range"
            else page.evaluate(BOX_JS, id_)
        )
        if rect is None and kind == "range" and rewritten_ok:
            out.append(None)
            continue
        assert rect is not None and rect["w"] > 0
        out.append(rect)
    return out


# --- B1-r2: copies outside the readable text nodes --------------------------------------------
@pytest.mark.parametrize("case", list(COPIES))
async def test_masked_capture_boxes_a_copy_in_a_control(
    case: str,
    chromium: Any,
    scan_settings: Settings,
    scan_engine: PrivacyEngine,
    demo_site_url: str,
) -> None:
    det = scan_engine.detector(scan_engine.policy(None), "en_IN")
    async with guarded_context(chromium, scan_settings, demo_site_url, cookies=True) as (ctx, _g):
        page = await _page(ctx, demo_site_url, CENSUS_PAGES[case])
        snap, frames = await _read(page)
        rendered = classify_rendered(snap.texts, detector=det)
        if case in _ONLY:  # the extraction reads it, or only the census classifies it (alt)
            assert case == "alt_only" or {r.entity_type for r in rendered} >= {"EMAIL"}
        else:
            assert {r.entity_type for r in rendered} & {"PERSON_NAME", "EMAIL"}  # table cell
        png = await capture_screen(
            page, frames, snap, rendered=rendered, prefilled=[], max_height=12000, detector=det
        )
        rects = await _copy_rects(page, case)
    assert png is not None
    for rect in rects:
        assert rect is not None and _covered(png, rect) > 0.5


async def test_a_copy_rendered_one_character_per_node_withholds(
    chromium: Any, scan_settings: Settings, scan_engine: PrivacyEngine, demo_site_url: str
) -> None:
    det = scan_engine.detector(scan_engine.policy(None), "en_IN")
    async with guarded_context(chromium, scan_settings, demo_site_url, cookies=True) as (ctx, _g):
        page = await _page(ctx, demo_site_url, CENSUS_PAGES["digits"])
        snap, frames = await _read(page)
        rendered = classify_rendered(snap.texts, detector=det)
        assert len(rendered) >= 2  # the email and the PAN in the table
        png = await capture_screen(
            page, frames, snap, rendered=rendered, prefilled=[], max_height=12000, detector=det
        )
    assert png is None  # one <span> per character cannot be boxed as one value: fail closed


async def _obfuscated(
    page: Any,
    snap: PageSnapshot,
    frames: dict[int, Any],
    *,
    mode: str,
    engine: PrivacyEngine,
    guard: Any,
    settings: Settings,
) -> CaptureResult:
    det = engine.detector(engine.policy(None), "en_IN")
    classes = {f.key: classify_field(f, detector=det, min_score=0.6) for f in snap.fields}
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


@pytest.mark.parametrize("mode", ["obfuscate", "synthesize"])
@pytest.mark.parametrize("case", list(COPIES))
async def test_obfuscated_capture_boxes_a_copy_in_a_control(
    case: str,
    mode: str,
    chromium: Any,
    scan_settings: Settings,
    scan_engine: PrivacyEngine,
    demo_site_url: str,
) -> None:
    async with guarded_context(chromium, scan_settings, demo_site_url, cookies=True) as (
        ctx,
        guard,
    ):
        page = await _page(ctx, demo_site_url, CENSUS_PAGES[case])
        snap, frames = await _read(page)
        res = await _obfuscated(
            page, snap, frames, mode=mode, engine=scan_engine, guard=guard, settings=scan_settings
        )
        rects = await _copy_rects(page, case, rewritten_ok=True)
    if res.status == "withheld":  # an unflagged copy in read text: never shown (fail closed)
        assert case in CENSUS_OBFUSCATED_WITHHELD and res.reason == "leakage", res.reason
        return
    assert res.status == "captured" and res.png is not None, res.reason
    for rect in rects:
        if rect is not None:  # still in the DOM: it must be boxed
            assert _covered(res.png, rect) > 0.5


# --- B3: long text nodes ----------------------------------------------------------------------
async def test_a_value_after_the_2000th_character_of_a_node_is_read_and_masked(
    chromium: Any, scan_settings: Settings, scan_engine: PrivacyEngine, demo_site_url: str
) -> None:
    det = scan_engine.detector(scan_engine.policy(None), "en_IN")
    async with guarded_context(chromium, scan_settings, demo_site_url, cookies=True) as (ctx, _g):
        page = await _page(ctx, demo_site_url, CENSUS_PAGES["long"])
        snap, frames = await _read(page)
        long_node = next(t for t in snap.texts if t.text.startswith(CENSUS_FILLER[:40]))
        at = long_node.text.find(CENSUS_EMAIL2)
        assert at > 2000 and not snap.truncated  # the whole node was read
        rendered = classify_rendered(snap.texts, detector=det)
        assert any(r.i == long_node.i and r.start <= at < r.end for r in rendered)
        png = await capture_screen(
            page, frames, snap, rendered=rendered, prefilled=[], max_height=12000, detector=det
        )
        rect = await page.evaluate(RANGE_JS, ["c-long", CENSUS_EMAIL2])
    assert png is not None and _covered(png, rect) > 0.5


async def test_a_change_after_the_2000th_character_is_seen_by_the_census(
    chromium: Any, scan_settings: Settings, scan_engine: PrivacyEngine, demo_site_url: str
) -> None:
    det = scan_engine.detector(scan_engine.policy(None), "en_IN")
    plain = f'<p id="c-long" style="font-size:14px;width:1200px">{CENSUS_FILLER} Contact: us</p>'
    change = (
        "(email) => { const n = document.getElementById('c-long').firstChild;"
        " n.nodeValue = n.nodeValue.replace('Contact: us', 'Contact: ' + email); return true; }"
    )
    async with guarded_context(chromium, scan_settings, demo_site_url, cookies=True) as (ctx, _g):
        page = await _page(ctx, demo_site_url, plain)
        snap, frames = await _read(page)
        rendered = classify_rendered(snap.texts, detector=det)
        assert await capture_screen(
            page, frames, snap, rendered=rendered, prefilled=[], max_height=12000
        )
        await page.evaluate(change, CENSUS_EMAIL2)
        blind = await capture_screen(
            page, frames, snap, rendered=rendered, prefilled=[], max_height=12000
        )
        seen = await capture_screen(
            page, frames, snap, rendered=rendered, prefilled=[], max_height=12000, detector=det
        )
        rect = await page.evaluate(RANGE_JS, ["c-long", CENSUS_EMAIL2])
    assert blind is None  # the node changed and nothing classified it: fail closed
    assert seen is not None and _covered(seen, rect) > 0.5


async def test_a_text_node_longer_than_the_read_cap_withholds_every_capture(
    chromium: Any, scan_settings: Settings, scan_engine: PrivacyEngine, demo_site_url: str
) -> None:
    from dps.layers.scanner.dom import TEXT_READ_CAP

    det = scan_engine.detector(scan_engine.policy(None), "en_IN")
    filler = CENSUS_FILLER * (TEXT_READ_CAP // len(CENSUS_FILLER) + 1)
    html = f'<p id="c-long" style="font-size:14px">{filler} Contact: {CENSUS_EMAIL2}</p>'
    async with guarded_context(chromium, scan_settings, demo_site_url, cookies=True) as (
        ctx,
        guard,
    ):
        page = await _page(ctx, demo_site_url, html)
        snap, frames = await _read(page)
        assert snap.truncated  # the node's tail was not read
        rendered = classify_rendered(snap.texts, detector=det)
        masked = await capture_screen(
            page, frames, snap, rendered=rendered, prefilled=[], max_height=12000, detector=det
        )
        obf = await _obfuscated(
            page,
            snap,
            frames,
            mode="synthesize",
            engine=scan_engine,
            guard=guard,
            settings=scan_settings,
        )
    assert masked is None
    assert obf.status == "withheld" and obf.png is None


async def test_a_long_node_grown_past_the_read_cap_after_extraction_withholds(
    chromium: Any, scan_settings: Settings, scan_engine: PrivacyEngine, demo_site_url: str
) -> None:
    from dps.layers.scanner.dom import TEXT_READ_CAP

    det = scan_engine.detector(scan_engine.policy(None), "en_IN")
    grow = (
        "(n) => { const t = document.getElementById('c-long').firstChild;"
        " t.nodeValue = t.nodeValue + ' x'.repeat(n); return true; }"
    )
    async with guarded_context(chromium, scan_settings, demo_site_url, cookies=True) as (ctx, _g):
        page = await _page(ctx, demo_site_url, CENSUS_PAGES["long"])
        snap, frames = await _read(page)
        rendered = classify_rendered(snap.texts, detector=det)
        await page.evaluate(grow, TEXT_READ_CAP)
        png = await capture_screen(
            page, frames, snap, rendered=rendered, prefilled=[], max_height=12000, detector=det
        )
    assert png is None


async def test_a_truncated_field_extraction_withholds(
    chromium: Any, scan_settings: Settings, scan_engine: PrivacyEngine, demo_site_url: str
) -> None:
    det = scan_engine.detector(scan_engine.policy(None), "en_IN")
    async with guarded_context(chromium, scan_settings, demo_site_url, cookies=True) as (ctx, _g):
        page = await ctx.new_page()
        await page.goto(f"{demo_site_url}/profile/contact")
        snap, frames = await _read(page, max_fields=1)
        assert snap.truncated
        classes = {f.key: classify_field(f, detector=det, min_score=0.6) for f in snap.fields}
        png = await capture_screen(
            page,
            frames,
            snap,
            rendered=classify_rendered(snap.texts, detector=det),
            prefilled=sensitive_prefilled(snap.fields, classes, det),
            max_height=12000,
            detector=det,
        )
    assert png is None  # pre-filled values in the fields not read are in no finding


@pytest.mark.parametrize("mode", ["obfuscate", "synthesize"])
async def test_obfuscated_capture_replaces_a_value_after_the_2000th_character(
    mode: str,
    chromium: Any,
    scan_settings: Settings,
    scan_engine: PrivacyEngine,
    demo_site_url: str,
) -> None:
    async with guarded_context(chromium, scan_settings, demo_site_url, cookies=True) as (
        ctx,
        guard,
    ):
        page = await _page(ctx, demo_site_url, CENSUS_PAGES["long"])
        snap, frames = await _read(page)
        res = await _obfuscated(
            page, snap, frames, mode=mode, engine=scan_engine, guard=guard, settings=scan_settings
        )
        shown = await page.evaluate("document.getElementById('c-long').textContent")
    assert res.status == "captured" and res.png is not None, res.reason
    assert normalise(CENSUS_EMAIL2) not in normalise(shown)
    # the node was rewritten whole: its text before and after the value is still there
    assert shown.startswith(CENSUS_FILLER.strip()) and "Contact:" in shown


# --- S2 captures-privacy r1 B1: text in a same-origin frame is masked where the frame draws it --
async def frame_page(ctx: Any, url: str, case: str, *, nested: bool) -> Any:
    """A demo page with a ``FRAME_CASES`` frame (``FRAME_JS``) at its top."""
    page = await ctx.new_page()
    await page.goto(f"{url}/help")
    await page.wait_for_load_state("load")
    await page.evaluate(FRAME_JS, frame_case_args(case, nested=nested))
    return page


async def frame_value_boxes(page: Any) -> list[dict[str, float]]:
    """Where the browser draws the frame's two values in the top page: Playwright's box, which maps
    through every frame's position, border, padding, scroll and transform (the capture's own
    arithmetic is what is under test, so it is not used here)."""
    for frame in page.frames:
        if frame is page.main_frame:
            continue
        if await (await frame.frame_element()).get_attribute("id") != "v-frame":
            continue
        out = []
        for element_id in ("v-email", "v-pan"):
            b = await frame.locator(f"#{element_id}").bounding_box()
            assert b is not None
            out.append({"x": b["x"], "y": b["y"], "w": b["width"], "h": b["height"]})
        return out
    raise AssertionError("the value frame is not on the page")


@pytest.mark.parametrize("nested", [False, True], ids=["frame", "nested"])
@pytest.mark.parametrize("case", list(FRAME_CASES))
async def test_text_in_a_same_origin_frame_is_masked_where_the_frame_draws_it(
    case: str,
    nested: bool,
    chromium: Any,
    scan_settings: Settings,
    scan_engine: PrivacyEngine,
    demo_site_url: str,
) -> None:
    """S2 captures-privacy r1 B1. The masks of text in a read child frame were placed with the
    frame's own scroll added (a fragment-scrolled frame: 730 px below the text), from the frame's
    border box instead of its content box (40 px border: 40 px up and left; 50/60 px padding) and
    ignoring a transform (``scale(2)``), so the stored original showed the values in clear. Each
    value's mask now covers the box the browser draws it in — a transformed frame is painted over
    whole (fail closed) — also when the frame sits inside another bordered, padded frame."""
    det = scan_engine.detector(scan_engine.policy(None), "en_IN")
    async with guarded_context(chromium, scan_settings, demo_site_url, cookies=True) as (ctx, _g):
        page = await frame_page(ctx, demo_site_url, case, nested=nested)
        snap, frames = await _read(page)
        rendered = classify_rendered(snap.texts, detector=det)
        found = {r.entity_type for r in rendered if r.frame_index > 0}
        assert found >= {"EMAIL", "PAN_IN"}, found  # precondition: both values are findings
        png = await capture_screen(
            page, frames, snap, rendered=rendered, prefilled=[], max_height=12000, detector=det
        )
        boxes = await frame_value_boxes(page)  # as shot: the page at 0,0, the frames as they are
    assert png is not None, (case, nested)
    for box in boxes:
        assert _covered(png, box) > 0.5, (case, nested, box)
