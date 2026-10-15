"""M4-G3 review r1 fixes: a masked capture shows no copy of a value the scanner detected (B1),
frames are matched to the snapshot by identity and a frame the snapshot never read is boxed (B2),
and text the page renders after extraction is classified or the capture withheld (live census).

The values below are made up and stand for customer data; assertions look at pixels only.
"""

from __future__ import annotations

import io
from typing import Any

import pytest
from PIL import Image

from dps.config import Settings
from dps.engine.service import PrivacyEngine
from dps.layers.scanner.capture import MASK_FILL, capture_screen
from dps.layers.scanner.classify import classify_rendered
from dps.layers.scanner.dom import extract_page, frame_map
from tests.scanner.helpers import guarded_context

pytestmark = pytest.mark.browser

NAME = "Zebulon Quixotic"
EMAIL = "zebulon.quixotic@example.org"

TWICE_JS = """([name, split]) => {
  const d = document.createElement('div');
  const greet = split
    ? `Welcome back, <b>${name.split(' ')[0]}</b> ${name.split(' ').slice(1).join(' ')}`
    : `Welcome back, ${name}`;
  d.innerHTML = `<table><tbody><tr><th>Full name</th><td id="t-cell">${name}</td></tr></tbody></table>
                 <p id="t-greet" style="font-size:22px">${greet}</p>`;
  document.body.prepend(d);
  return true;
}"""

RECT_JS = """([id, needle]) => {
  const el = document.getElementById(id);
  const it = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
  let n = it.nextNode();
  while (n && !n.nodeValue.includes(needle)) n = it.nextNode();
  if (!n) return null;
  const at = n.nodeValue.indexOf(needle);
  const range = document.createRange();
  range.setStart(n, at); range.setEnd(n, at + needle.length);
  const r = range.getBoundingClientRect();
  return { x: r.left + window.scrollX, y: r.top + window.scrollY, w: r.width, h: r.height };
}"""

FRAME_JS = """([html, before]) => {
  const f = document.createElement('iframe');
  f.srcdoc = html;
  f.style.cssText = 'width:700px;height:160px;border:0;display:block';
  const anchor = before ? document.querySelector('iframe') : null;
  if (anchor) anchor.before(f); else document.body.prepend(f);
  return new Promise((r) => { f.onload = () => r(true); });
}"""

FRAME_HTML = (
    f'<p style="font-size:28px">Customer {NAME}</p><p style="font-size:28px">Mail {EMAIL}</p>'
)


def _covered(png: bytes, r: dict[str, float]) -> float:
    """Share of sampled pixels inside ``r`` that are mask fill (the entity label is drawn in
    another colour, so a fully masked box scores well below 1.0 but far above 0)."""
    im = Image.open(io.BytesIO(png)).convert("RGB")
    xs = range(int(r["x"]) + 1, int(r["x"] + r["w"]) - 1, 2)
    ys = range(int(r["y"]) + 1, int(r["y"] + r["h"]) - 1, 2)
    points = [(x, y) for x in xs for y in ys if x < im.width and y < im.height]
    assert points
    return sum(1 for p in points if im.getpixel(p) == MASK_FILL) / len(points)


async def _open(ctx: Any, url: str) -> Any:
    page = await ctx.new_page()
    await page.goto(url)
    await page.wait_for_load_state("load")
    return page


# --- B1: every copy of a detected value is boxed ---------------------------------------------
async def test_masked_capture_boxes_every_copy_of_a_detected_value(
    chromium: Any, scan_settings: Settings, scan_engine: PrivacyEngine, demo_site_url: str
) -> None:
    det = scan_engine.detector(scan_engine.policy(None), "en_IN")
    async with guarded_context(chromium, scan_settings, demo_site_url, cookies=True) as (ctx, _g):
        page = await _open(ctx, f"{demo_site_url}/help")
        await page.evaluate(TWICE_JS, [NAME, False])
        snap = await extract_page(page, iframes=True, shadow_dom=True, max_fields=400)
        frames = await frame_map(page, iframes=True)
        rendered = classify_rendered(snap.texts, detector=det)
        by_node = {(t.frame_index, t.i): t.text for t in snap.texts}
        hit_nodes = {by_node[(r.frame_index, r.i)] for r in rendered}
        # precondition: the detectors find the table cell (row header) but not the greeting
        assert NAME in hit_nodes and f"Welcome back, {NAME}" not in hit_nodes
        greet = await page.evaluate(RECT_JS, ["t-greet", NAME])
        cell = await page.evaluate(RECT_JS, ["t-cell", NAME])
        png = await capture_screen(
            page, frames, snap, rendered=rendered, prefilled=[], max_height=12000
        )
    assert png is not None
    assert _covered(png, cell) > 0.5
    assert _covered(png, greet) > 0.5  # the second, undetected copy is boxed too


async def test_masked_capture_withholds_a_copy_split_across_nodes(
    chromium: Any, scan_settings: Settings, scan_engine: PrivacyEngine, demo_site_url: str
) -> None:
    det = scan_engine.detector(scan_engine.policy(None), "en_IN")
    async with guarded_context(chromium, scan_settings, demo_site_url, cookies=True) as (ctx, _g):
        page = await _open(ctx, f"{demo_site_url}/help")
        await page.evaluate(TWICE_JS, [NAME, True])
        snap = await extract_page(page, iframes=True, shadow_dom=True, max_fields=400)
        frames = await frame_map(page, iframes=True)
        rendered = classify_rendered(snap.texts, detector=det)
        assert rendered
        png = await capture_screen(
            page, frames, snap, rendered=rendered, prefilled=[], max_height=12000
        )
    assert png is None  # "<b>Zebulon</b> Quixotic" cannot be boxed as one value: fail closed


# --- B2: frames by identity ------------------------------------------------------------------
async def test_a_frame_attached_after_extraction_is_boxed(
    chromium: Any, scan_settings: Settings, scan_engine: PrivacyEngine, demo_site_url: str
) -> None:
    det = scan_engine.detector(scan_engine.policy(None), "en_IN")
    async with guarded_context(chromium, scan_settings, demo_site_url, cookies=True) as (ctx, _g):
        page = await _open(ctx, f"{demo_site_url}/help")
        snap = await extract_page(page, iframes=True, shadow_dom=True, max_fields=400)
        await page.evaluate(FRAME_JS, [FRAME_HTML, False])
        frames = await frame_map(page, iframes=True)  # the caller's map now holds the new frame
        assert len(frames) == 2 and len(snap.frames) == 1
        box = await (await page.query_selector("iframe")).bounding_box()  # type: ignore[union-attr]
        png = await capture_screen(
            page,
            frames,
            snap,
            rendered=classify_rendered(snap.texts, detector=det),
            prefilled=[],
            max_height=12000,
        )
    assert png is not None
    assert (
        _covered(png, {"x": box["x"], "y": box["y"], "w": box["width"], "h": box["height"]}) > 0.9
    )


async def test_a_caller_frame_map_that_disagrees_with_the_snapshot_withholds(
    chromium: Any, scan_settings: Settings, demo_site_url: str
) -> None:
    async with guarded_context(chromium, scan_settings, demo_site_url, cookies=True) as (ctx, _g):
        page = await _open(ctx, f"{demo_site_url}/help")
        await page.evaluate(FRAME_JS, ["<p>first frame</p>", False])
        snap = await extract_page(page, iframes=True, shadow_dom=True, max_fields=400)
        read = await frame_map(page, iframes=True)
        await page.evaluate(FRAME_JS, ["<p>second frame</p>", True])
        other = next(f for f in page.frames if f is not page.main_frame and f is not read[1])
        swapped = {0: page.main_frame, 1: other, 2: read[1]}
        png = await capture_screen(page, swapped, snap, rendered=[], prefilled=[], max_height=12000)
    assert png is None


async def test_a_snapshot_of_a_reloaded_or_re_read_page_withholds(
    chromium: Any, scan_settings: Settings, demo_site_url: str
) -> None:
    async with guarded_context(chromium, scan_settings, demo_site_url, cookies=True) as (ctx, _g):
        page = await _open(ctx, f"{demo_site_url}/help")
        first = await extract_page(page, iframes=True, shadow_dom=True, max_fields=400)
        second = await extract_page(page, iframes=True, shadow_dom=True, max_fields=400)
        frames = await frame_map(page, iframes=True)
        stale = await capture_screen(
            page, frames, first, rendered=[], prefilled=[], max_height=12000
        )
        fresh = await capture_screen(
            page, frames, second, rendered=[], prefilled=[], max_height=12000
        )
        await page.reload()
        await page.wait_for_load_state("load")
        reloaded = await capture_screen(
            page,
            await frame_map(page, iframes=True),
            second,
            rendered=[],
            prefilled=[],
            max_height=12000,
        )
    assert stale is None and fresh is not None and reloaded is None


# --- text rendered after extraction ----------------------------------------------------------
async def test_text_rendered_after_extraction_is_classified_or_the_capture_withheld(
    chromium: Any, scan_settings: Settings, scan_engine: PrivacyEngine, demo_site_url: str
) -> None:
    det = scan_engine.detector(scan_engine.policy(None), "en_IN")
    late = (
        "() => { const p = document.createElement('p'); p.id = 't-late';"
        " p.style.fontSize = '22px'; p.textContent = 'Write to "
        + EMAIL
        + "'; document.body.prepend(p); return true; }"
    )
    async with guarded_context(chromium, scan_settings, demo_site_url, cookies=True) as (ctx, _g):
        page = await _open(ctx, f"{demo_site_url}/help")
        snap = await extract_page(page, iframes=True, shadow_dom=True, max_fields=400)
        frames = await frame_map(page, iframes=True)
        rendered = classify_rendered(snap.texts, detector=det)
        before = await capture_screen(
            page, frames, snap, rendered=rendered, prefilled=[], max_height=12000
        )
        await page.evaluate(late)
        blind = await capture_screen(
            page, frames, snap, rendered=rendered, prefilled=[], max_height=12000
        )
        seen = await capture_screen(
            page, frames, snap, rendered=rendered, prefilled=[], max_height=12000, detector=det
        )
        rect = await page.evaluate(RECT_JS, ["t-late", EMAIL])
    assert before is not None
    assert blind is None  # new text and no detector to classify it: fail closed
    assert seen is not None and _covered(seen, rect) > 0.5
