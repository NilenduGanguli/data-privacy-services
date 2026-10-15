"""Screenshots, numbered overlays, mask boxes and obfuscated captures (spec §8 "Capture").

Rules:

* An unmasked image of a page that renders personal data (or shows sensitive pre-filled values)
  never leaves memory: :func:`capture_screen` paints a solid box (labelled with the entity type)
  over every rendered finding and every pre-filled field that shows a value *before* PNG
  encoding, and returns ``None`` (withheld, fail closed) when any of them cannot be located.
  Every *other* copy of those values the page renders (``SWEEP_MIN_CHARS``+ letters/digits,
  compared normalised) is boxed too: the capture takes its own census of every string the read
  frames render now (``dom.render_census``: every rendered text node with no minimum length,
  button text and text under a ``display:contents`` parent or in a slot included, visible listbox
  options and option labels, ``<optgroup>`` labels, ``input[type=button|submit|reset]`` labels,
  ``<img>``/``input[type=image]`` alt text and the placeholder of an empty field) and boxes every
  copy in it, plus every visible field showing one; a copy that is not wholly
  inside located copies (split across nodes, e.g. one character per ``<span>``) withholds
  (M4-G3 review r1 B1, r2 B1). With a detector, census strings the classification list does not
  hold (buttons, options, input labels, alt text, placeholders, short nodes) are classified and
  their hits boxed too (M4 verify r1 B4/B5).
* A page whose extraction did not read everything it renders (``PageSnapshot.truncated``: a text
  node longer than ``dom.TEXT_READ_CAP``, more than ``max_fields`` fields, more text than the read
  budget) is never captured (M4-G3 review r2 B3, N9).
* Frames are those the snapshot's extraction read, matched **by identity** (``dom.Extraction``),
  never by the caller's positional frame numbers; a read frame that no longer shows the document
  read (reloaded, navigated, read again) withholds. Every capture paints the box of every live
  child frame that was not read (cross-origin, iframes off, attached after the extraction) and is
  withheld when a frame failed while it was read or an unread frame cannot be located (M4-G2 fix,
  D-C; M4-G3 review r1 B2).
* Text the read frames render that the extraction did not read (added or changed later) is
  classified with the caller's detector and its hits boxed; without a detector such text withholds.
* Boxes are measured on the live page right before the screenshot and again right after it
  (child-frame offsets included, CSS animations, transitions and scroll snapping off); if anything
  moved while the screenshot was taken the capture is withheld rather than masked in the wrong
  place. Every box is placed in the screenshot's own coordinates: a box measured in the top
  page's viewport (a client rect, a Playwright box) is moved by where the browser reports that
  viewport to be (``dom.viewport_box``), never by ``scrollX``/``scrollY`` — root scroll snapping
  can keep the page off 0,0 after ``scrollTo(0, 0)`` (S2 captures-privacy r2 B1), and an RTL or
  vertical-rl document wider than the viewport scrolls from its right edge while the screenshot
  starts at its left (r2 B2). The page must be where it was measured when it is shot (else the
  capture is withheld: a fixed or sticky box would be drawn elsewhere).
* A read child frame draws its document's viewport at its content box (inside its border and
  padding), wherever that document is scrolled to: rects inside it are measured in its viewport,
  mapped through the content box and cut to what the frame (and every frame around it) shows. A
  read frame whose pixels are not the page's — transformed, zoomed, or a content box that is not
  its document's viewport — is painted over whole like an unread one (``dom.FrameGeometry``;
  S2 captures-privacy r1 B1).
* :func:`obfuscated_capture` replaces rendered values and pre-filled input values **in the DOM**
  (tokens with a reversible map in ``obfuscate`` mode, surrogates in ``synthesize`` mode), arms
  the guard first (a modified page must never navigate), rewrites each node whole (only while it
  still holds the text read), re-reads what the page renders now, boxes every original that
  survived (in buttons, options, input labels, or values split across nodes) through the same
  census and is withheld when one cannot be boxed. A select cannot show a token, so a sensitive
  pre-selected option is boxed instead.
"""

from __future__ import annotations

import io
import json
import re
from collections import defaultdict
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from PIL import Image, ImageDraw, ImageFont
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Frame, Page

from dps.config import Settings
from dps.engine.detect.ensemble import Detector
from dps.engine.findings import Finding, Locator, hash_value
from dps.engine.planner import build_plan
from dps.engine.policy import Policy
from dps.engine.service import PrivacyEngine
from dps.engine.synth.surrogates import SurrogateEngine
from dps.engine.taxonomy import entity_info
from dps.engine.transform.apply import ApplyOptions, apply_plan
from dps.engine.transform.crypto import KeyMaterial
from dps.engine.verify import collect_originals, normalise, verify_output
from dps.layers.scanner.classify import classify_rendered, draws_placeholder, shown_texts
from dps.layers.scanner.dom import (
    REPLACE_TEXT_JS,
    SCROLL_JS,
    SET_VALUES_JS,
    CensusItem,
    Extraction,
    FrameGeometry,
    census_rects,
    extract_page,
    extraction_of,
    frame_geometry,
    late_texts,
    render_census,
    stamped,
    text_rects,
    viewport_box,
)
from dps.layers.scanner.guard import ScanGuard
from dps.layers.scanner.models import (
    FieldFacts,
    FrameFacts,
    PageSnapshot,
    Rect,
    RenderedFinding,
    TextFacts,
)

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
#: Injected into every read frame before anything is measured: no animation or transition, no
#: caret, no smooth scrolling, and no scroll snapping — a root that snaps keeps the page at its
#: first snap area after ``scrollTo(0, 0)`` (S2 captures-privacy r2 B1; masks are placed with the
#: page's real position either way, ``dom.viewport_box``).
_NO_MOTION_CSS = (
    "*, *::before, *::after { animation: none !important; transition: none !important; "
    "caret-color: transparent !important; scroll-behavior: auto !important; "
    "scroll-snap-type: none !important; }"
)
_NO_MOTION_JS = (
    "(css) => { if (Array.from(document.querySelectorAll('style[data-dps-capture]'))"
    ".some((x) => x.textContent === css)) return true;"
    " const s = document.createElement('style'); s.setAttribute('data-dps-capture', '');"
    " s.textContent = css; (document.head || document.documentElement).appendChild(s); return true; }"
)
_ZERO = Rect(x=0, y=0, w=0, h=0)
#: A value is swept for (every copy the page renders is boxed) from this many letters/digits on.
SWEEP_MIN_CHARS = 4
#: ``(frame_index, text node i, start, end, label)``
Span = tuple[int, int, int, int, str]
#: ``(position in the census list, start, end, label)``
CensusSpan = tuple[int, int, int, str]


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
async def _element_box(frame: Frame | None, key: str, origin: Rect) -> Rect | None:
    """A field element's box in document coordinates: Playwright's box (the top page's viewport,
    through every frame) moved by ``origin``, where that viewport is (``dom.viewport_box``)."""
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
    return Rect(x=box["x"] + origin.x, y=box["y"] + origin.y, w=box["width"], h=box["height"])


async def _frame_box(frame: Frame, origin: Rect) -> Rect | None:
    """A child frame's box now, in document coordinates (``origin`` as for ``_element_box``;
    zero-size: not rendered; ``None``: cannot be determined)."""
    try:
        element = await frame.frame_element()
        box = await element.bounding_box()
    except PlaywrightError:
        return None
    if box is None:
        return _ZERO
    return Rect(x=box["x"] + origin.x, y=box["y"] + origin.y, w=box["width"], h=box["height"])


async def _read_frames(
    page: Page, frames: Mapping[int, Frame], snapshot: PageSnapshot
) -> Extraction | None:
    """The frames ``snapshot`` read, by identity (``dom.Extraction``), when each of them still shows
    the document that was read and the caller's ``frames`` agree with them; ``None``: withhold.

    The caller's map is positional (``dom.frame_map`` numbers ``page.frames`` in order), so a frame
    that attached or detached after the extraction shifts it: such a map never decides which frame
    is measured. A frame in it that the snapshot did not read is treated like every other live
    child frame that was not read (boxed); one that names a read frame under another number, or
    another frame under a read frame's number, withholds."""
    ext = extraction_of(snapshot)
    if ext is None or snapshot.frames_failed or ext.frames.get(0) is not page.main_frame:
        return None
    for index, frame in frames.items():
        read = ext.frames.get(index)
        if read is not None and read is not frame:
            return None
        if read is None and any(frame is f for f in ext.frames.values()):
            return None
    for index, frame in ext.frames.items():
        if not await stamped(frame, ext.token, index):
            return None  # reloaded, navigated or read again by a later extraction
    return ext


def _intersect(a: Rect, b: Rect) -> Rect:
    """The overlap of ``a`` and ``b`` (zero-size when they do not overlap)."""
    x, y = max(a.x, b.x), max(a.y, b.y)
    return Rect(
        x=x,
        y=y,
        w=max(0.0, min(a.x + a.w, b.x + b.w) - x),
        h=max(0.0, min(a.y + a.h, b.y + b.h) - y),
    )


def _shown_part(rects: Sequence[Rect], clip: Rect | None) -> list[Rect]:
    """The parts of ``rects`` a frame shows (``clip``: the box it shows; ``None``: the top page,
    whose whole document is shot). A rect scrolled out of its frame's view is not drawn at all."""
    if clip is None:
        return list(rects)
    out = [_intersect(r, clip) for r in rects]
    return [r for r in out if r.w > 0 and r.h > 0]


@dataclass(frozen=True)
class _LiveFrames:
    """The page as a capture measures it now. ``snapshot``: the read frames' offsets measured now
    (the top page's viewport, child frames' content boxes); ``unread``: the boxes of every live
    child frame that was not read
    (always painted over); ``opaque``: the boxes of the read frames whose pixels are not the page's
    (``FrameGeometry.flat``), painted over whenever values are masked; ``whole``: the read frames
    inside such a box, whose text and fields are not masked one by one; ``shows``: the box each
    other read child frame shows (its content box cut to its ancestors')."""

    snapshot: PageSnapshot
    ext: Extraction
    unread: list[Rect]
    opaque: list[Rect]
    whole: frozenset[int]
    shows: dict[int, Rect]


async def _fresh_frames(
    page: Page, frames: Mapping[int, Frame], snapshot: PageSnapshot, origin: Rect
) -> _LiveFrames | None:
    """The live frames of ``snapshot`` (by identity; measured now, ``origin``: where the top page's
    viewport is, ``dom.viewport_box``). ``None``: withhold (a frame failed while it was read, a
    read frame changed or cannot be located, an unread frame cannot be located)."""
    ext = await _read_frames(page, frames, snapshot)
    if ext is None:
        return None
    by_index = {f.frame_index: f for f in snapshot.frames if f.read}
    index_of = {id(frame): index for index, frame in ext.frames.items()}
    geometry: dict[int, FrameGeometry] = {}
    for index, frame in ext.frames.items():
        if index == 0:
            continue
        measured = await frame_geometry(frame, (origin.x, origin.y))
        if measured is None:
            return None
        geometry[index] = measured
    fresh: list[FrameFacts] = []
    opaque: list[Rect] = []
    whole: set[int] = set()
    shows: dict[int, Rect] = {}
    for index, frame in ext.frames.items():
        facts = by_index.get(index)
        if facts is None:
            return None
        if index == 0:
            fresh.append(facts.model_copy(update={"offset": origin}))
            continue
        geo = geometry[index]
        fresh.append(facts.model_copy(update={"offset": geo.content}))
        shown, mapped, parent = geo.content, geo.flat, frame.parent_frame
        while mapped and parent is not None and parent is not page.main_frame:
            above = index_of.get(id(parent))
            if above is None or not geometry[above].flat:
                mapped = False  # inside a frame painted over whole (unread, or not flat)
                break
            shown = _intersect(shown, geometry[above].content)
            parent = parent.parent_frame
        if mapped:
            shows[index] = shown
            continue
        whole.add(index)
        if not geo.flat and geo.border.w > 0 and geo.border.h > 0:
            opaque.append(geo.border)  # transformed or zoomed: no rect inside maps onto the page
    read = list(ext.frames.values())
    unread: list[Rect] = []
    for frame in page.frames:
        if frame is page.main_frame or any(frame is r for r in read):
            continue
        box = await _frame_box(frame, origin)
        if box is None:
            if frame.is_detached():
                continue
            return None
        if box.w > 0 and box.h > 0:
            unread.append(box)
    return _LiveFrames(
        snapshot=snapshot.model_copy(update={"frames": fresh}),
        ext=ext,
        unread=unread,
        opaque=opaque,
        whole=frozenset(whole),
        shows=shows,
    )


# --- every copy of a value --------------------------------------------------------------------
def _flat(text: str) -> tuple[str, list[int]]:
    """``normalise(text)`` built character by character, with the code-point index in ``text`` of
    every character of it."""
    chars: list[str] = []
    where: list[int] = []
    for k, ch in enumerate(text):
        for c in normalise(ch):
            chars.append(c)
            where.append(k)
    return "".join(chars), where


def _shown(f: FieldFacts) -> list[str]:
    """What a field shows: its value, or what a select draws (``classify.shown_texts``: a
    customizable select's button, each selected option's label — its label attribute when not
    empty, else its text — whatever its value and wherever it sits, and every option of a listbox;
    S2 captures-privacy r2 B3, r3 B1, S1-M4 r1 B1/B3)."""
    return shown_texts(f)


def shown_values(fields: Sequence[tuple[FieldFacts, str]]) -> list[tuple[str, str]]:
    """``(value, entity)`` for what each field shows (memory only: these are page values) — not
    a select's drawn placeholder (a whole placeholder phrase such as "Choose…" from a listed option
    without a value, ``classify.draws_placeholder``): that select is boxed, but its placeholder is
    no value to hunt for elsewhere on the page. A real value that merely starts like a placeholder
    is swept (S1-M4 captures-privacy r1 B2)."""
    return [
        (shown, entity) for f, entity in fields if not draws_placeholder(f) for shown in _shown(f)
    ]


def _needles(values: list[tuple[str, str]]) -> dict[str, str]:
    """normalised value -> label, for every value with ``SWEEP_MIN_CHARS``+ letters/digits."""
    needles: dict[str, str] = {}
    for value, label in values:
        n = normalise(value)
        if len(n) >= SWEEP_MIN_CHARS:
            needles.setdefault(n, label)
    return needles


def _occurrences(hay: str, needle: str) -> list[int]:
    """Every start of ``needle`` in ``hay``, overlapping ones included."""
    out: list[int] = []
    pos = hay.find(needle)
    while pos >= 0:
        out.append(pos)
        pos = hay.find(needle, pos + 1)
    return out


def _sweep(
    texts: list[str], whole: list[bool], values: list[tuple[str, str]]
) -> list[CensusSpan] | None:
    """Every copy of every ``(value, label)`` (``SWEEP_MIN_CHARS``+ letters/digits, compared in
    ``normalise`` form) in the strings ``texts``, rendered in this order: ``(piece, start, end,
    label)`` with code-point offsets into the piece; a ``whole`` piece (measured as one box) is
    returned once as ``(piece, 0, len, label)``.

    ``None`` (fail closed): an occurrence of a value in the normalised concatenation of all pieces
    is not wholly inside the copies located in single pieces — a copy split across pieces (one
    character per node, or a periodic value straddling a located copy) — or a piece's
    character-wise mapping does not reproduce its normalised form."""
    needles = _needles(values)
    if not needles:
        return []
    normed = [normalise(t) for t in texts]
    starts: list[int] = []
    at = 0
    for n in normed:
        starts.append(at)
        at += len(n)
    haystack = "".join(normed)
    out: list[CensusSpan] = []
    for needle, label in needles.items():
        hits = _occurrences(haystack, needle)
        if not hits:
            continue
        size = len(needle)
        covered: set[int] = set()
        for piece, (text, n) in enumerate(zip(texts, normed, strict=True)):
            if needle not in n:
                continue
            flat, where = _flat(text)
            if flat != n:
                return None
            base = starts[piece]
            if whole[piece]:
                out.append((piece, 0, len(text), label))
                covered.update(range(base, base + len(n)))
                continue
            for pos in _occurrences(n, needle):
                out.append((piece, where[pos], where[pos + size - 1] + 1, label))
                covered.update(range(base + pos, base + pos + size))
        for h in hits:
            if any(q not in covered for q in range(h, h + size)):
                return None  # a copy split across pieces cannot be boxed as one value
    return out


def _swept_fields(
    fields: list[FieldFacts], values: list[tuple[str, str]]
) -> list[tuple[FieldFacts, str]]:
    """The visible fields showing a copy of one of ``values`` (``SWEEP_MIN_CHARS``+)."""
    out: list[tuple[FieldFacts, str]] = []
    for needle, label in _needles(values).items():
        out += [
            (f, label)
            for f in fields
            if f.visible and any(needle in normalise(s) for s in _shown(f))
        ]
    return out


def sweep_copies(
    texts: list[TextFacts], fields: list[FieldFacts], values: list[tuple[str, str]]
) -> tuple[list[Span], list[tuple[FieldFacts, str]]] | None:
    """Every copy of every ``(value, label)`` with at least ``SWEEP_MIN_CHARS`` letters/digits in
    the text nodes ``texts`` and the visible fields: text spans ``(frame, i, start, end, label)``
    and fields showing it, compared in ``normalise`` form (case, accents, spacing and punctuation
    do not hide a copy). ``None`` (fail closed): a copy cannot be located — split across text
    nodes, or in a form the character-wise mapping does not reproduce (``_sweep``). Captures sweep
    their census (``dom.render_census``), which holds every rendered string, not only ``texts``."""
    found = _sweep([t.text for t in texts], [False] * len(texts), values)
    if found is None:
        return None
    spans: list[Span] = [
        (texts[p].frame_index, texts[p].i, start, end, label) for p, start, end, label in found
    ]
    return spans, _swept_fields(fields, values)


def _unique_spans(spans: list[Span]) -> list[Span]:
    seen: set[tuple[int, int, int, int]] = set()
    out: list[Span] = []
    for s in spans:
        if s[:4] not in seen:
            seen.add(s[:4])
            out.append(s)
    return out


def _unique_census(spans: list[CensusSpan]) -> list[CensusSpan]:
    seen: set[tuple[int, int, int]] = set()
    out: list[CensusSpan] = []
    for s in spans:
        if s[:3] not in seen:
            seen.add(s[:3])
            out.append(s)
    return out


def _unique_fields(fields: list[tuple[FieldFacts, str]]) -> list[tuple[FieldFacts, str]]:
    seen: set[str] = set()
    out: list[tuple[FieldFacts, str]] = []
    for f, label in fields:
        if f.key not in seen:
            seen.add(f.key)
            out.append((f, label))
    return out


@dataclass(frozen=True)
class _MaskPlan:
    """What a capture masks: ``spans`` and ``fields`` as given and — with ``census`` — every copy
    of ``values`` in the page's census of rendered strings and in its visible fields, text
    rendered after the extraction (classified with ``detector``; withheld without one) and, with a
    ``detector``, the hits in census strings the classification list does not hold."""

    spans: list[Span]
    fields: list[tuple[FieldFacts, str]]
    values: list[tuple[str, str]]
    detector: Detector | None
    census: bool
    #: ``capture_screen(unmasked=True)``: no value is masked, so a read frame is never painted over
    unmasked: bool = False


@dataclass(frozen=True)
class _Measured:
    """The masks of one measurement, and where the top page's viewport was while they were
    measured (document coordinates, ``dom.viewport_box``)."""

    masks: list[MaskBox]
    origin: Rect


Measure = Callable[[], Awaitable[_Measured | None]]


def _measurer(
    page: Page, frames: Mapping[int, Frame], snapshot: PageSnapshot, plan: _MaskPlan
) -> Measure:
    """Measure the masks of ``plan`` on the live page; ``None`` when one of them (or a frame, or the
    page's viewport) cannot be located, or text appeared that cannot be checked."""

    async def measure() -> _Measured | None:
        await page.evaluate(SCROLL_JS)  # where it lands is read, not assumed (``viewport_box``)
        origin = await viewport_box(page)
        if origin is None:
            return None
        state = await _fresh_frames(page, frames, snapshot, origin)
        if state is None:
            return None
        live, ext = state.snapshot, state.ext
        painted = state.unread if plan.unmasked else state.unread + state.opaque
        masks = [MaskBox(rect=r, label=FRAME_LABEL) for r in painted]
        spans, fields, values = list(plan.spans), list(plan.fields), list(plan.values)
        if plan.census:
            late = await late_texts(ext)
            if late is None:
                return None
            if late:
                if plan.detector is None:
                    return None  # text nobody classified: fail closed
                changed = {(t.frame_index, t.i) for t, c in late if c}
                if any((s[0], s[1]) in changed for s in spans):
                    return None  # a finding's node changed: its offsets are stale
                new = [t for t, _ in late]
                keys = {(t.frame_index, t.i) for t in new}
                kept = [t for t in live.texts if (t.frame_index, t.i) not in keys]
                live = live.model_copy(update={"texts": kept + new})
                text_of = {(t.frame_index, t.i): t.text for t in new}
                for r in classify_rendered(new, detector=plan.detector):
                    spans.append((r.frame_index, r.i, r.start, r.end, r.entity_type))
                    values.append((text_of[(r.frame_index, r.i)][r.start : r.end], r.entity_type))
        items: list[CensusItem] = []
        census: list[CensusSpan] = []
        if plan.census and (values or plan.detector is not None):
            got = await render_census(ext)
            if got is None:
                return None  # a frame changed or renders more than can be checked
            items = got
            if plan.detector is not None:
                census += _classify_census(items, plan.detector, values)
            if values:
                swept = _sweep([it.text for it in items], [it.box for it in items], values)
                if swept is None:
                    return None
                census += swept
                fields += _swept_fields(live.fields, values)
        # a frame painted over whole needs no mask inside it (and none could be placed there)
        spans = [s for s in _unique_spans(spans) if s[0] not in state.whole]
        fields = [(f, e) for f, e in _unique_fields(fields) if f.frame_index not in state.whole]
        census = [c for c in _unique_census(census) if items[c[0]].frame_index not in state.whole]
        if spans:
            rects = await text_rects(page, dict(ext.frames), live, [s[:4] for s in spans])
            for span, found in zip(spans, rects, strict=True):
                if not found:
                    return None
                shown = _shown_part(found, state.shows.get(span[0]))
                masks += [MaskBox(rect=r, label=span[4]) for r in shown]
        if census:
            offsets = {f.frame_index: f.offset for f in live.frames if f.read and f.offset}
            rects = await census_rects(ext.frames, offsets, items, [c[:3] for c in census])
            for c, found in zip(census, rects, strict=True):
                if not found:
                    return None
                shown = _shown_part(found, state.shows.get(items[c[0]].frame_index))
                masks += [MaskBox(rect=r, label=c[3]) for r in shown]
        for f, entity in fields:
            box = await _element_box(ext.frames.get(f.frame_index), f.key, origin)
            if box is None:
                return None
            shown = _shown_part([box], state.shows.get(f.frame_index))
            masks += [MaskBox(rect=r, label=entity) for r in shown]
        return _Measured(masks=masks, origin=origin)

    return measure


def _classify_census(
    items: list[CensusItem], detector: Detector, values: list[tuple[str, str]]
) -> list[CensusSpan]:
    """Detector hits in the census strings the classification text list does not hold (button
    text, input button labels, listbox options, nodes under two characters): their spans (a box
    item whole); each hit's value is appended to ``values`` so its other copies are swept too."""
    extra = [
        TextFacts(frame_index=it.frame_index, i=pos, text=it.text, in_table=False, label_hint=None)
        for pos, it in enumerate(items)
        if not it.known
    ]
    out: list[CensusSpan] = []
    for r in classify_rendered(extra, detector=detector):
        it = items[r.i]
        out.append(
            (r.i, 0, len(it.text), r.entity_type)
            if it.box
            else (r.i, r.start, r.end, r.entity_type)
        )
        values.append((it.text[r.start : r.end], r.entity_type))
    return out


def _same_place(a: Rect, b: Rect) -> bool:
    """Whether the top page's viewport is at the same place in ``a`` and ``b``."""
    return abs(a.x - b.x) <= _MOVE_TOLERANCE and abs(a.y - b.y) <= _MOVE_TOLERANCE


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


async def _stop_motion(frames: Mapping[int, Frame]) -> None:
    for frame in frames.values():
        try:
            await frame.evaluate(_NO_MOTION_JS, _NO_MOTION_CSS)
        except PlaywrightError:
            continue


async def _full_page_png(page: Page, max_height: int) -> tuple[bytes, Rect] | None:
    """The full-page PNG and where the top page's viewport was while it was taken (the same just
    before and just after it; ``None`` otherwise, or when it cannot be read)."""
    await page.evaluate(SCROLL_JS)
    before = await viewport_box(page)
    png = await page.screenshot(
        full_page=True, type="png", animations="disabled", caret="hide", scale="css"
    )
    after = await viewport_box(page)
    if before is None or after is None or not _same_place(before, after):
        return None
    return crop_height(png, max_height), before


async def _shoot(
    page: Page, frames: Mapping[int, Frame], measure: Measure, max_height: int
) -> bytes | None:
    """Measure, screenshot, measure again; masks painted before encoding. ``None``: withheld — also
    when the page was not at the same place for both measurements and the screenshot (a fixed or
    sticky box would be drawn where it was not measured)."""
    await _stop_motion(frames)
    await page.wait_for_timeout(SETTLE_BEFORE_SHOT_MS)
    first = await measure()
    if first is None:
        return None
    shot = await _full_page_png(page, max_height)
    if shot is None:
        return None
    png, at = shot
    again = await measure()
    if again is None or not _same(first.masks, again.masks):
        return None  # the page moved while it was taken: never a mask in the wrong place
    if not (_same_place(first.origin, at) and _same_place(at, again.origin)):
        return None  # the page scrolled between the measurements and the screenshot
    return draw_masks(png, first.masks) if first.masks else png


async def field_boxes(frames: dict[int, Frame], fields: list[FieldFacts]) -> list[OverlayBox]:
    """Numbered boxes of the visible fields (document coordinates of the top page), measured as a
    capture measures (no motion, no scroll snapping, placed with ``dom.viewport_box``); none when
    the page's viewport cannot be read (the outlines are drawn on a capture, never needed)."""
    await _stop_motion(frames)
    await frames[0].evaluate(SCROLL_JS)
    origin = await viewport_box(frames[0].page)
    if origin is None:
        return []
    out: list[OverlayBox] = []
    for f in fields:
        if not f.visible or f.frame_index not in frames:
            continue
        box = await _element_box(frames[f.frame_index], f.key, origin)
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
    detector: Detector | None = None,
    sweep: Sequence[tuple[str, str]] = (),
) -> bytes | None:
    """A full-page PNG with every rendered finding, every pre-filled field in ``prefilled``, every
    other copy of their values the page renders (any rendered string or field) and every child
    frame the snapshot did not read painted over; ``None`` = withheld (the extraction did not read
    everything the page renders (``snapshot.truncated``), something could not be located, a copy is
    split across nodes, the page moved, a read frame no longer shows what was read, or text
    rendered after the extraction could not be classified).

    Frames are those ``snapshot`` read, by identity (``frames`` must agree with them). Text the page
    renders that the extraction did not read is classified with ``detector`` and its hits boxed;
    without a ``detector`` such text withholds. ``sweep`` adds ``(value, label)`` pairs whose
    copies are boxed wherever the page still renders them (a filled screen: the originals the page
    showed before the fill replaced them). ``unmasked`` (local only; posture refuses it in
    staging/prod) skips the value masks but still paints unread frames."""
    ext = extraction_of(snapshot)
    if ext is None:
        return None
    if snapshot.truncated and not unmasked:
        return None  # text or fields the extraction did not read are in no finding
    if unmasked:
        plan = _MaskPlan(spans=[], fields=[], values=[], detector=None, census=False, unmasked=True)
    else:
        text_of = {(t.frame_index, t.i): t.text for t in snapshot.texts}
        values = [
            (text_of[(r.frame_index, r.i)][r.start : r.end], r.entity_type)
            for r in rendered
            if (r.frame_index, r.i) in text_of
        ]
        values += shown_values(prefilled)
        values += [(v, label) for v, label in sweep if v]
        plan = _MaskPlan(
            spans=[(r.frame_index, r.i, r.start, r.end, r.entity_type) for r in rendered],
            fields=list(prefilled),
            values=values,
            detector=detector,
            census=True,
        )
    measure = _measurer(page, frames, snapshot, plan)
    return await _shoot(page, ext.frames, measure, max_height)


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
    if snapshot.truncated:  # text or fields the extraction did not read are in no finding
        return CaptureResult(status="withheld", reason="truncated")
    ext = await _read_frames(page, frames, snapshot)
    if ext is None:  # not from extract_page, a read frame changed, or the caller's map disagrees
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
            # a node is rewritten whole, and only while it still holds the text that was read
            if await ext.frames[frame_index].evaluate(REPLACE_TEXT_JS, pairs) != len(pairs):
                return CaptureResult(status="withheld", reason="residual")
        value_pairs: dict[int, list[list[str]]] = defaultdict(list)
        for j, (f, _) in enumerate(inputs):
            value_pairs[f.frame_index].append([f.key, str(out_v[j])])
        for frame_index, pairs in value_pairs.items():
            await ext.frames[frame_index].evaluate(SET_VALUES_JS, pairs)
        # Residual sweep over what the page renders NOW: every copy of an original that is
        # still there is boxed; one that cannot be located (split across nodes) withholds.
        after = await extract_page(
            page,
            iframes=ext.iframes,
            shadow_dom=ext.shadow_dom,
            max_fields=settings.scan_max_fields_per_page,
        )
    except (PlaywrightError, KeyError):
        return CaptureResult(status="withheld", reason="residual")
    if after.truncated:
        return CaptureResult(status="withheld", reason="truncated")
    values = [
        (original, finding.entity_type)
        for finding, original in originals
        if (finding.locator.path, finding.locator.start, finding.locator.end) not in kept
    ]
    # a select cannot show a token: it is boxed, and every copy of what it draws is swept, as the
    # masked capture sweeps it (S2 captures-privacy r3 B1)
    values += shown_values(selects)
    if sweep_copies(after.texts, after.fields, values) is None:
        return CaptureResult(status="withheld", reason="residual")
    plan_masks = _MaskPlan(
        spans=[],
        fields=list(selects),
        values=values,
        detector=engine.detector(policy, locale),
        census=True,
    )
    measure = _measurer(page, ext.frames, after, plan_masks)
    png = await _shoot(page, ext.frames, measure, settings.scan_screenshot_max_height)
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
