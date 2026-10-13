"""Screenshots, numbered overlays, mask boxes and obfuscated captures (spec §8 "Capture").

Rules:

* An unmasked image of a page that renders personal data (or shows sensitive pre-filled values)
  never leaves memory: :func:`capture_screen` paints a solid box (labelled with the entity type)
  over every rendered finding and every pre-filled field that shows a value *before* PNG
  encoding, and returns ``None`` (withheld, fail closed) when any of them cannot be located.
* Every capture also paints the box of every child frame that was never read (cross-origin, or
  iframes off) and is withheld when a frame failed while it was read or an unread frame cannot be
  located (``dom.unread_frame_boxes`` / ``PageSnapshot.frames_failed``; M4-G2 fix, D-C).
* Boxes are measured on the live page right before the screenshot and again right after it
  (child-frame offsets included, CSS animations and transitions off); if anything moved while the
  screenshot was taken the capture is withheld rather than masked in the wrong place.
* :func:`obfuscated_capture` replaces rendered values and pre-filled input values **in the DOM**
  (tokens with a reversible map in ``obfuscate`` mode, surrogates in ``synthesize`` mode), arms
  the guard first (a modified page must never navigate), re-reads what the page renders now,
  boxes every original that survived (values split across nodes) and is withheld when one cannot
  be boxed. A select cannot show a token, so a sensitive pre-selected option is boxed instead.
"""

from __future__ import annotations

import io
import json
import re
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from PIL import Image, ImageDraw, ImageFont
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Frame, Page

from dps.config import Settings
from dps.engine.findings import Finding, Locator, hash_value
from dps.engine.planner import build_plan
from dps.engine.policy import Policy
from dps.engine.service import PrivacyEngine
from dps.engine.synth.surrogates import SurrogateEngine
from dps.engine.taxonomy import entity_info
from dps.engine.transform.apply import ApplyOptions, apply_plan
from dps.engine.transform.crypto import KeyMaterial
from dps.engine.verify import collect_originals, normalise, verify_output
from dps.layers.scanner.dom import (
    REPLACE_TEXT_JS,
    SCROLL_JS,
    SET_VALUES_JS,
    extract_page,
    text_rects,
)
from dps.layers.scanner.guard import ScanGuard
from dps.layers.scanner.models import FieldFacts, FrameFacts, PageSnapshot, Rect, RenderedFinding

VARIANT_COLOUR: dict[str, tuple[int, int, int]] = {
    "original": (251, 191, 36),
    "synthesize": (167, 139, 250),
    "obfuscate": (45, 212, 191),
}
MASK_FILL: tuple[int, int, int] = (15, 21, 36)
MASK_TEXT: tuple[int, int, int] = (169, 180, 204)
FRAME_LABEL = "FRAME"
#: Locating one field element must not wait for the page (it is there or the capture is withheld).
LOCATE_TIMEOUT_MS = 2000
#: Settling time after animations are switched off, before boxes are measured.
SETTLE_BEFORE_SHOT_MS = 50
#: Boxes measured before and after the screenshot may differ by this much (sub-pixel rounding).
_MOVE_TOLERANCE = 0.5
_NO_MOTION_CSS = (
    "*, *::before, *::after { animation: none !important; transition: none !important; "
    "caret-color: transparent !important; scroll-behavior: auto !important; }"
)
_NO_MOTION_JS = (
    "(css) => { const s = document.createElement('style'); s.setAttribute('data-dps-capture', '');"
    " s.textContent = css; (document.head || document.documentElement).appendChild(s); return true; }"
)
_ZERO = Rect(x=0, y=0, w=0, h=0)


@dataclass(frozen=True)
class OverlayBox:
    """A numbered outline drawn around a field on a filled screen."""

    number: int
    rect: Rect


@dataclass(frozen=True)
class MaskBox:
    """A solid box painted over a value (``label`` = the entity type, never the value)."""

    rect: Rect
    label: str


@dataclass
class CaptureResult:
    """An obfuscated capture, or why it was withheld (``reason`` is a code)."""

    status: Literal["captured", "withheld"]
    png: bytes | None = field(default=None, repr=False)
    capture_json: bytes | None = field(default=None, repr=False)
    reason: str | None = None
    methods_used: dict[str, int] = field(default_factory=dict)


# --- Pillow -----------------------------------------------------------------------------------
def _open_image(png: bytes) -> Image.Image:
    with Image.open(io.BytesIO(png)) as im:
        return im.convert("RGB")


def _encode(im: Image.Image) -> bytes:
    buf = io.BytesIO()
    im.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _clamp(rect: Rect, im: Image.Image, pad: int) -> tuple[int, int, int, int] | None:
    x0, y0 = max(0, int(rect.x) - pad), max(0, int(rect.y) - pad)
    x1 = min(im.width - 1, int(rect.x + rect.w + 0.999) + pad)
    y1 = min(im.height - 1, int(rect.y + rect.h + 0.999) + pad)
    return (x0, y0, x1, y1) if x1 > x0 and y1 > y0 else None


def crop_height(png: bytes, max_height: int) -> bytes:
    """``png`` cut to ``max_height`` pixels (unchanged when it is not taller)."""
    im = _open_image(png)
    return png if im.height <= max_height else _encode(im.crop((0, 0, im.width, max_height)))


def draw_masks(png: bytes, masks: list[MaskBox], *, pad: int = 2) -> bytes:
    """Paint every mask solid (``MASK_FILL``), labelled with its entity type when it fits."""
    im = _open_image(png)
    draw = ImageDraw.Draw(im)
    font = ImageFont.load_default(size=11)
    for m in masks:
        box = _clamp(m.rect, im, pad)
        if box is None:
            continue
        draw.rectangle(box, fill=MASK_FILL)
        if box[3] - box[1] >= 12 and box[2] - box[0] >= 24:
            text = m.label[: (box[2] - box[0] - 6) // 6]
            draw.text((box[0] + 3, box[1] + 1), text, fill=MASK_TEXT, font=font)
    return _encode(im)


def draw_overlays(png: bytes, boxes: list[OverlayBox], *, variant: str) -> bytes:
    """Outline every box in the variant's colour with its field number in a pill."""
    colour = VARIANT_COLOUR[variant]
    im = _open_image(png)
    draw = ImageDraw.Draw(im)
    font = ImageFont.load_default(size=11)
    for b in boxes:
        box = _clamp(b.rect, im, 1)
        if box is None:
            continue
        draw.rectangle(box, outline=colour, width=2)
        label = str(b.number)
        w = 7 * len(label) + 9
        bx0, by0 = max(0, box[2] - w - 2), max(0, box[1] - 9)
        draw.rounded_rectangle((bx0, by0, bx0 + w, by0 + 16), radius=8, fill=colour)
        draw.text((bx0 + 4, by0 + 2), label, fill=(6, 17, 15), font=font)
    return _encode(im)


# --- measuring the live page ------------------------------------------------------------------
async def _element_box(frame: Frame | None, key: str) -> Rect | None:
    """A field element's box in document coordinates (the page is scrolled to 0,0)."""
    if frame is None:
        return None
    try:
        box = await frame.locator(f'[data-dps-field="{key}"]').bounding_box(
            timeout=LOCATE_TIMEOUT_MS
        )
    except PlaywrightError:
        return None
    if box is None:
        return None
    return Rect(x=box["x"], y=box["y"], w=box["width"], h=box["height"])


async def _frame_box(frame: Frame) -> Rect | None:
    """A child frame's box now (zero-size: not rendered; ``None``: cannot be determined)."""
    try:
        element = await frame.frame_element()
        box = await element.bounding_box()
    except PlaywrightError:
        return None
    if box is None:
        return _ZERO
    return Rect(x=box["x"], y=box["y"], w=box["width"], h=box["height"])


async def _fresh_frames(
    page: Page, frames: dict[int, Frame], snapshot: PageSnapshot
) -> tuple[PageSnapshot, list[Rect]] | None:
    """``snapshot`` with the read frames' offsets measured now, and the boxes of every child
    frame that is not read (measured now too). ``None``: withhold (a frame failed while it was
    read, or a frame cannot be located)."""
    if snapshot.frames_failed:
        return None
    by_index = {f.frame_index: f for f in snapshot.frames}
    fresh: list[FrameFacts] = []
    for index, frame in frames.items():
        facts = by_index.get(index)
        if facts is None:
            continue
        offset = _ZERO if index == 0 else await _frame_box(frame)
        if offset is None:
            return None
        fresh.append(facts.model_copy(update={"offset": offset}))
    read = list(frames.values())
    unread: list[Rect] = []
    for frame in page.frames:
        if frame is page.main_frame or frame in read:
            continue
        box = await _frame_box(frame)
        if box is None:
            if frame.is_detached():
                continue
            return None
        if box.w > 0 and box.h > 0:
            unread.append(box)
    return snapshot.model_copy(update={"frames": fresh}), unread


Measure = Callable[[], Awaitable[list[MaskBox] | None]]


def _measurer(
    page: Page,
    frames: dict[int, Frame],
    snapshot: PageSnapshot,
    spans: list[tuple[int, int, int, int, str]],
    fields: list[tuple[FieldFacts, str]],
) -> Measure:
    """Measure the masks for ``spans`` (frame, text node, start, end, label) and ``fields`` on the
    live page; ``None`` when one of them (or a frame) cannot be located."""

    async def measure() -> list[MaskBox] | None:
        await page.evaluate(SCROLL_JS)
        state = await _fresh_frames(page, frames, snapshot)
        if state is None:
            return None
        live, unread = state
        masks = [MaskBox(rect=r, label=FRAME_LABEL) for r in unread]
        if spans:
            rects = await text_rects(page, frames, live, [s[:4] for s in spans])
            for span, found in zip(spans, rects, strict=True):
                if not found:
                    return None
                masks += [MaskBox(rect=r, label=span[4]) for r in found]
        for f, entity in fields:
            box = await _element_box(frames.get(f.frame_index), f.key)
            if box is None:
                return None
            masks.append(MaskBox(rect=box, label=entity))
        return masks

    return measure


def _same(a: list[MaskBox], b: list[MaskBox]) -> bool:
    if len(a) != len(b):
        return False
    for x, y in zip(a, b, strict=True):
        if x.label != y.label:
            return False
        for p, q in ((x.rect.x, y.rect.x), (x.rect.y, y.rect.y), (x.rect.w, y.rect.w)):
            if abs(p - q) > _MOVE_TOLERANCE:
                return False
        if abs(x.rect.h - y.rect.h) > _MOVE_TOLERANCE:
            return False
    return True


async def _stop_motion(page: Page, frames: dict[int, Frame]) -> None:
    for frame in frames.values():
        try:
            await frame.evaluate(_NO_MOTION_JS, _NO_MOTION_CSS)
        except PlaywrightError:
            continue


async def _full_page_png(page: Page, max_height: int) -> bytes:
    await page.evaluate(SCROLL_JS)
    png = await page.screenshot(
        full_page=True, type="png", animations="disabled", caret="hide", scale="css"
    )
    return crop_height(png, max_height)


async def _shoot(
    page: Page, frames: dict[int, Frame], measure: Measure, max_height: int
) -> bytes | None:
    """Measure, screenshot, measure again; masks painted before encoding. ``None``: withheld."""
    await _stop_motion(page, frames)
    await page.wait_for_timeout(SETTLE_BEFORE_SHOT_MS)
    masks = await measure()
    if masks is None:
        return None
    png = await _full_page_png(page, max_height)
    again = await measure()
    if again is None or not _same(masks, again):
        return None  # the page moved while it was taken: never a mask in the wrong place
    return draw_masks(png, masks) if masks else png


async def field_boxes(frames: dict[int, Frame], fields: list[FieldFacts]) -> list[OverlayBox]:
    """Numbered boxes of the visible fields (document coordinates of the top page)."""
    await frames[0].evaluate(SCROLL_JS)  # bounding boxes at scroll 0 == document coordinates
    out: list[OverlayBox] = []
    for f in fields:
        if not f.visible or f.frame_index not in frames:
            continue
        box = await _element_box(frames[f.frame_index], f.key)
        if box is not None:
            out.append(OverlayBox(number=f.field_no, rect=box))
    return out


async def capture_screen(
    page: Page,
    frames: dict[int, Frame],
    snapshot: PageSnapshot,
    *,
    rendered: list[RenderedFinding],
    prefilled: list[tuple[FieldFacts, str]],
    max_height: int,
    unmasked: bool = False,
) -> bytes | None:
    """A full-page PNG with every rendered finding, every pre-filled field in ``prefilled`` and
    every unread frame painted over; ``None`` = withheld (something could not be located, or the
    page moved). ``unmasked`` (local only; posture refuses it in staging/prod) skips the value
    masks but still paints unread frames."""
    spans = (
        [] if unmasked else [(r.frame_index, r.i, r.start, r.end, r.entity_type) for r in rendered]
    )
    fields = [] if unmasked else prefilled
    return await _shoot(page, frames, _measurer(page, frames, snapshot, spans, fields), max_height)


_T_INDEX = re.compile(r"^\$\.t\[(\d+)\]")


async def obfuscated_capture(
    page: Page,
    frames: dict[int, Frame],
    snapshot: PageSnapshot,
    *,
    rendered: list[RenderedFinding],
    prefilled: list[tuple[FieldFacts, str]],
    engine: PrivacyEngine,
    policy: Policy,
    mode: Literal["obfuscate", "synthesize"],
    key: KeyMaterial | None,
    seed: bytes,
    locale: str,
    guard: ScanGuard,
    settings: Settings,
    page_no: int,
) -> CaptureResult:
    """The page as it would look obfuscated (see the module docstring). ``capture_json`` (obfuscate
    mode only) holds the tokens of the text nodes that had a finding (every other node ``null``),
    the tokenised pre-filled values, the reveal hints and the key id — never an original."""
    if unread_failed(snapshot):
        return CaptureResult(status="withheld", reason="frame")
    texts = snapshot.texts
    g_of = {(t.frame_index, t.i): g for g, t in enumerate(texts)}
    inputs = [(f, e) for f, e in prefilled if f.tag != "select"]
    selects = [(f, e) for f, e in prefilled if f.tag == "select"]
    doc: dict[str, list[str]] = {
        "t": [t.text for t in texts],
        "v": [f.value or "" for f, _ in inputs],
    }
    findings: list[Finding] = []
    for r in rendered:
        g = g_of.get((r.frame_index, r.i))
        if g is None:
            return CaptureResult(status="withheld", reason="residual")
        text = texts[g].text
        whole = r.start <= len(text) - len(text.lstrip()) and r.end >= len(text.rstrip())
        findings.append(
            Finding(
                locator=Locator(
                    path=f"$.t[{g}]",
                    start=None if whole else r.start,
                    end=None if whole else r.end,
                ),
                entity_type=r.entity_type,
                score=r.score,
                detectors=r.detectors,
                sensitivity=entity_info(r.entity_type).sensitivity,
                value_hash=hash_value(text[r.start : r.end]),
            )
        )
    for j, (f, entity) in enumerate(inputs):
        findings.append(
            Finding(
                locator=Locator(path=f"$.v[{j}]"),
                entity_type=entity,
                score=1.0,
                detectors=["scanner:prefilled"],
                sensitivity=entity_info(entity).sensitivity,
                value_hash=hash_value(f.value or ""),
            )
        )
    plan = build_plan(findings, policy, mode, fpe_enabled=engine.fpe_enabled)
    options = ApplyOptions(
        policy=policy,
        key=key if mode == "obfuscate" else None,
        surrogates=SurrogateEngine(seed, default_locale=locale) if mode == "synthesize" else None,
        fpe_enabled=engine.fpe_enabled,
        cipher_factory=engine.cipher_factory,
        token_style="natural",
        locale=locale,
    )
    applied = apply_plan(plan=plan, options=options, data=doc)
    kept: set[tuple[str, int | None, int | None]] = {
        (e.finding.locator.path, e.finding.locator.start, e.finding.locator.end)
        for e in plan.entries
        if e.method == "keep"
    }
    originals = collect_originals(findings, data=doc)
    if not verify_output(originals, applied.output, kept=kept).passed:
        return CaptureResult(status="withheld", reason="leakage")
    if not await guard.arm(page, ()):  # a modified page must never navigate
        return CaptureResult(status="withheld", reason="guard_lock")
    out_t, out_v = applied.output["t"], applied.output["v"]
    try:
        text_pairs: dict[int, list[list[Any]]] = defaultdict(list)
        for g, t in enumerate(texts):
            if out_t[g] != t.text:
                text_pairs[t.frame_index].append([t.i, out_t[g]])
        for frame_index, pairs in text_pairs.items():
            await frames[frame_index].evaluate(REPLACE_TEXT_JS, pairs)
        value_pairs: dict[int, list[list[str]]] = defaultdict(list)
        for j, (f, _) in enumerate(inputs):
            value_pairs[f.frame_index].append([f.key, str(out_v[j])])
        for frame_index, pairs in value_pairs.items():
            await frames[frame_index].evaluate(SET_VALUES_JS, pairs)
        # Residual sweep over what the page renders NOW: any original still present is boxed;
        # one that cannot be located as text (split across nodes, inside an input) withholds.
        after = await extract_page(
            page,
            iframes=len(frames) > 1,
            shadow_dom=True,
            max_fields=settings.scan_max_fields_per_page,
        )
    except (PlaywrightError, KeyError):
        return CaptureResult(status="withheld", reason="residual")
    haystack = normalise(
        json.dumps({"t": [t.text for t in after.texts], "v": [f.value or "" for f in after.fields]})
    )
    spans: list[tuple[int, int, int, int, str]] = []
    for finding, original in originals:
        loc = finding.locator
        needle = original.strip()
        if (
            (loc.path, loc.start, loc.end) in kept
            or len(normalise(needle)) < 4
            or normalise(needle) not in haystack
        ):
            continue
        located = [
            (
                t.frame_index,
                t.i,
                t.text.find(needle),
                t.text.find(needle) + len(needle),
                finding.entity_type,
            )
            for t in after.texts
            if needle in t.text
        ]
        if not located:
            return CaptureResult(status="withheld", reason="residual")
        spans += located
    measure = _measurer(page, frames, after, spans, selects)
    png = await _shoot(page, frames, measure, settings.scan_screenshot_max_height)
    if png is None:
        return CaptureResult(status="withheld", reason="mask")
    capture_json: bytes | None = None
    if mode == "obfuscate":
        touched = {
            int(m.group(1)) for e in plan.entries if (m := _T_INDEX.match(e.finding.locator.path))
        }
        data = {
            "t": [out_t[g] if g in touched else None for g in range(len(texts))],
            "v": list(out_v),
        }
        capture_json = json.dumps(
            {
                "format": "dps.capture.v1",
                "page_no": page_no,
                "mode": mode,
                "kid": key.kid if key is not None else None,
                "data": data,
                "reveal_hints": [h.model_dump(mode="json") for h in applied.reveal_hints],
                "methods_used": applied.methods_used,
            },
            sort_keys=True,
        ).encode("utf-8")
    return CaptureResult(
        status="captured", png=png, capture_json=capture_json, methods_used=applied.methods_used
    )


def unread_failed(snapshot: PageSnapshot) -> bool:
    """Whether no capture of ``snapshot``'s page may be emitted (a frame failed while it was
    read)."""
    return bool(snapshot.frames_failed)
