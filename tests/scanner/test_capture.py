"""Task 12: masked screenshots, numbered overlays, obfuscated captures (fail closed)."""

from __future__ import annotations

import io
import json
from typing import Any

import pytest
from PIL import Image

from dps.config import Settings
from dps.engine.service import Payload, PrivacyEngine
from dps.engine.synth.surrogates import generate_synthesis_seed, seed_bytes
from dps.engine.transform.crypto import generate_key_material
from dps.engine.verify import normalise
from dps.layers.scanner.capture import (
    MASK_FILL,
    VARIANT_COLOUR,
    MaskBox,
    OverlayBox,
    capture_screen,
    crop_height,
    draw_masks,
    draw_overlays,
    field_boxes,
    obfuscated_capture,
)
from dps.layers.scanner.classify import classify_field, classify_rendered, sensitive_prefilled
from dps.layers.scanner.dom import extract_page, frame_map, text_rects
from dps.layers.scanner.models import Rect, RenderedFinding
from tests.scanner.helpers import DEMO_VALUE_KEYS, guarded_context


def _png(w: int = 200, h: int = 100) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (255, 255, 255)).save(buf, format="PNG")
    return buf.getvalue()


def test_overlays_draw_numbered_outlines_in_the_variant_colour() -> None:
    out = Image.open(
        io.BytesIO(
            draw_overlays(
                _png(), [OverlayBox(7, Rect(x=10, y=20, w=80, h=30))], variant="synthesize"
            )
        )
    )
    assert out.size == (200, 100) and out.getpixel((9, 35)) == VARIANT_COLOUR["synthesize"]
    assert out.getpixel((150, 80)) == (255, 255, 255)


def test_masks_fill_rects_and_crop_limits_height() -> None:
    masked = Image.open(
        io.BytesIO(draw_masks(_png(), [MaskBox(Rect(x=40, y=40, w=60, h=20), "AADHAAR")]))
    )
    assert masked.getpixel((45, 58)) == MASK_FILL
    # the far edge of a fractional box is covered too (no sliver of the value left)
    edge = Image.open(
        io.BytesIO(draw_masks(_png(), [MaskBox(Rect(x=10.4, y=10.4, w=20.3, h=5.3), "X")], pad=0))
    )
    assert edge.getpixel((30, 15)) == MASK_FILL
    assert Image.open(io.BytesIO(crop_height(_png(200, 100), 60))).size == (200, 60)
    assert crop_height(_png(200, 50), 60) == _png(200, 50)


@pytest.mark.browser
async def test_original_of_a_page_rendering_pii_is_masked(
    chromium: Any, scan_settings: Settings, scan_engine: PrivacyEngine, demo_site_url: str
) -> None:
    async with guarded_context(chromium, scan_settings, demo_site_url, cookies=True) as (ctx, _g):
        page = await ctx.new_page()
        await page.goto(f"{demo_site_url}/profile")
        snap = await extract_page(page, iframes=True, shadow_dom=True, max_fields=400)
        frames = await frame_map(page, iframes=True)
        det = scan_engine.detector(scan_engine.policy(None), "en_IN")
        rendered = classify_rendered(snap.texts, detector=det)
        assert {r.entity_type for r in rendered} >= {"PERSON_NAME", "AADHAAR", "CARD_PAN", "EMAIL"}
        png = await capture_screen(
            page, frames, snap, rendered=rendered, prefilled=[], max_height=12000
        )
        assert png is not None
        rects = await text_rects(
            page, frames, snap, [(r.frame_index, r.i, r.start, r.end) for r in rendered]
        )
        im = Image.open(io.BytesIO(png))
        for found in rects:
            r = found[0]  # sample inside the 2px padding, left of the entity label the mask carries
            assert im.getpixel((int(r.x) - 1, int(r.y + r.h / 2))) == MASK_FILL


@pytest.mark.browser
async def test_prefilled_values_are_masked(
    chromium: Any, scan_settings: Settings, scan_engine: PrivacyEngine, demo_site_url: str
) -> None:
    async with guarded_context(chromium, scan_settings, demo_site_url, cookies=True) as (ctx, _g):
        page = await ctx.new_page()
        await page.goto(f"{demo_site_url}/profile/contact")
        snap = await extract_page(page, iframes=True, shadow_dom=True, max_fields=400)
        frames = await frame_map(page, iframes=True)
        det = scan_engine.detector(scan_engine.policy(None), "en_IN")
        classes = {f.key: classify_field(f, detector=det, min_score=0.6) for f in snap.fields}
        prefilled = sensitive_prefilled(snap.fields, classes, det)
        assert len(prefilled) == 3
        png = await capture_screen(
            page, frames, snap, rendered=[], prefilled=prefilled, max_height=12000
        )
        assert png is not None
        im = Image.open(io.BytesIO(png))
        for f, _ in prefilled:
            box = await page.locator(f'[data-dps-field="{f.key}"]').bounding_box()
            assert box is not None
            assert im.getpixel((int(box["x"] + 2), int(box["y"] + box["height"] - 3))) == MASK_FILL


@pytest.mark.browser
async def test_obfuscated_capture_tokens_the_dom_and_reveals_with_the_key(
    chromium: Any,
    scan_settings: Settings,
    scan_engine: PrivacyEngine,
    demo_site_url: str,
    demo_data: dict[str, str],
) -> None:
    key = generate_key_material("key")
    async with guarded_context(chromium, scan_settings, demo_site_url, cookies=True) as (
        ctx,
        guard,
    ):
        page = await ctx.new_page()
        await page.goto(f"{demo_site_url}/profile")
        snap = await extract_page(page, iframes=True, shadow_dom=True, max_fields=400)
        frames = await frame_map(page, iframes=True)
        det = scan_engine.detector(scan_engine.policy(None), "en_IN")
        rendered = classify_rendered(snap.texts, detector=det)
        res = await obfuscated_capture(
            page,
            frames,
            snap,
            rendered=rendered,
            prefilled=[],
            engine=scan_engine,
            policy=scan_engine.policy(None),
            mode="obfuscate",
            key=key,
            seed=seed_bytes(generate_synthesis_seed()),
            locale="en_IN",
            guard=guard,
            settings=scan_settings,
            page_no=1,
        )
        shown = normalise(await page.evaluate("document.body.innerText"))
    assert res.status == "captured" and res.png and res.capture_json, res.reason
    for k in DEMO_VALUE_KEYS:
        assert normalise(demo_data[k]) not in shown, k
    cap = json.loads(res.capture_json)
    assert cap["format"] == "dps.capture.v1" and cap["kid"] == key.kid
    revealed = scan_engine.reveal(
        Payload(data=cap["data"]), key.model_dump(mode="json"), cap["reveal_hints"]
    ).result
    restored = normalise(json.dumps(revealed))
    for k in DEMO_VALUE_KEYS:
        assert normalise(demo_data[k]) in restored, k
    # the capture JSON carries tokens only: no original anywhere in it
    raw = normalise(res.capture_json.decode())
    for k in DEMO_VALUE_KEYS:
        assert normalise(demo_data[k]) not in raw, k


@pytest.mark.browser
async def test_synthesize_capture_has_no_reveal_map(
    chromium: Any,
    scan_settings: Settings,
    scan_engine: PrivacyEngine,
    demo_site_url: str,
    demo_data: dict[str, str],
) -> None:
    async with guarded_context(chromium, scan_settings, demo_site_url, cookies=True) as (
        ctx,
        guard,
    ):
        page = await ctx.new_page()
        await page.goto(f"{demo_site_url}/profile")
        snap = await extract_page(page, iframes=True, shadow_dom=True, max_fields=400)
        frames = await frame_map(page, iframes=True)
        det = scan_engine.detector(scan_engine.policy(None), "en_IN")
        rendered = classify_rendered(snap.texts, detector=det)
        res = await obfuscated_capture(
            page,
            frames,
            snap,
            rendered=rendered,
            prefilled=[],
            engine=scan_engine,
            policy=scan_engine.policy(None),
            mode="synthesize",
            key=None,
            seed=seed_bytes(generate_synthesis_seed()),
            locale="en_IN",
            guard=guard,
            settings=scan_settings,
            page_no=1,
        )
        shown = normalise(await page.evaluate("document.body.innerText"))
    assert res.status == "captured" and res.png is not None, res.reason
    assert res.capture_json is None
    for k in DEMO_VALUE_KEYS:
        assert normalise(demo_data[k]) not in shown, k


@pytest.mark.browser
async def test_obfuscated_capture_of_prefilled_inputs(
    chromium: Any,
    scan_settings: Settings,
    scan_engine: PrivacyEngine,
    demo_site_url: str,
    demo_data: dict[str, str],
) -> None:
    key = generate_key_material("key")
    async with guarded_context(chromium, scan_settings, demo_site_url, cookies=True) as (
        ctx,
        guard,
    ):
        page = await ctx.new_page()
        await page.goto(f"{demo_site_url}/profile/contact")
        snap = await extract_page(page, iframes=True, shadow_dom=True, max_fields=400)
        frames = await frame_map(page, iframes=True)
        det = scan_engine.detector(scan_engine.policy(None), "en_IN")
        classes = {f.key: classify_field(f, detector=det, min_score=0.6) for f in snap.fields}
        prefilled = sensitive_prefilled(snap.fields, classes, det)
        res = await obfuscated_capture(
            page,
            frames,
            snap,
            rendered=[],
            prefilled=prefilled,
            engine=scan_engine,
            policy=scan_engine.policy(None),
            mode="obfuscate",
            key=key,
            seed=seed_bytes(generate_synthesis_seed()),
            locale="en_IN",
            guard=guard,
            settings=scan_settings,
            page_no=2,
        )
        values = await page.evaluate(
            "() => Array.from(document.querySelectorAll('input,textarea')).map((e) => e.value)"
        )
    assert res.status == "captured", res.reason
    joined = normalise(json.dumps(values))
    for k in ("email", "mobile", "address"):
        assert normalise(demo_data[k]) not in joined, k


@pytest.mark.browser
async def test_capture_fails_closed_when_a_finding_cannot_be_located(
    chromium: Any, scan_settings: Settings, demo_site_url: str
) -> None:
    async with guarded_context(chromium, scan_settings, demo_site_url, cookies=True) as (ctx, _g):
        page = await ctx.new_page()
        await page.goto(f"{demo_site_url}/profile")
        snap = await extract_page(page, iframes=True, shadow_dom=True, max_fields=400)
        frames = await frame_map(page, iframes=True)
        ghost = RenderedFinding(
            frame_index=0,
            i=99_999,
            start=0,
            end=4,
            entity_type="SSN",
            score=1.0,
            detectors=["test"],
            in_table=False,
        )
        assert (
            await capture_screen(
                page, frames, snap, rendered=[ghost], prefilled=[], max_height=12000
            )
            is None
        )


@pytest.mark.browser
async def test_unread_frames_are_painted_and_a_failed_frame_withholds(
    chromium: Any, scan_settings: Settings, demo_site_url: str
) -> None:
    async with guarded_context(chromium, scan_settings, demo_site_url) as (ctx, _g):
        page = await ctx.new_page()
        await page.goto(f"{demo_site_url}/apply/card")
        await page.wait_for_load_state("load")
        snap = await extract_page(page, iframes=False, shadow_dom=True, max_fields=400)
        frames = await frame_map(page, iframes=False)
        (unread,) = snap.unread_frames
        assert unread.offset is not None and unread.offset.w > 0
        png = await capture_screen(page, frames, snap, rendered=[], prefilled=[], max_height=12000)
        assert png is not None
        im = Image.open(io.BytesIO(png))
        o = unread.offset
        assert im.getpixel((int(o.x + o.w / 2), int(o.y + o.h / 2))) == MASK_FILL
        # even an "unmasked" original never shows a frame that was not read
        raw = await capture_screen(
            page, frames, snap, rendered=[], prefilled=[], max_height=12000, unmasked=True
        )
        assert raw is not None
        assert (
            Image.open(io.BytesIO(raw)).getpixel((int(o.x + o.w / 2), int(o.y + o.h / 2)))
            == MASK_FILL
        )
        failed = snap.model_copy(update={"frames_failed": [1]})
        assert (
            await capture_screen(page, frames, failed, rendered=[], prefilled=[], max_height=12000)
            is None
        )


@pytest.mark.browser
async def test_field_boxes_number_visible_fields(
    chromium: Any, scan_settings: Settings, demo_site_url: str
) -> None:
    async with guarded_context(chromium, scan_settings, demo_site_url) as (ctx, _g):
        page = await ctx.new_page()
        await page.goto(f"{demo_site_url}/apply/identity")
        snap = await extract_page(page, iframes=True, shadow_dom=True, max_fields=400)
        frames = await frame_map(page, iframes=True)
        boxes = await field_boxes(frames, snap.fields)
        assert [b.number for b in boxes] == [f.field_no for f in snap.fields if f.visible]
        assert all(b.rect.w > 0 and b.rect.h > 0 for b in boxes)
