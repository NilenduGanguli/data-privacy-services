"""Page geometry → text and back (pure functions).

``build_page_text`` lays words out as lines (``\\n``) and words (a space, or a tab across a wide
gap such as a table column) and records every word's offset, so a finding's span in the page text
maps back to exactly the words — and on a text layer the characters — that hold it.
"""

from __future__ import annotations

import bisect
import dataclasses
import statistics

from dps.engine.verify import normalise
from dps.layers.documents.models import BBox, PageText, Word

REDACT_PAD_PT = 1.5  # boxes grow by this on every side (anti-aliasing, OCR jitter)
COLUMN_GAP_CHARS = 2.5  # a gap wider than this many median character widths becomes a tab …
COLUMN_GAP_MIN_PT = 6.0  # … and wider than this many points
#: An OCR word and a text-layer word overlapping this much of the smaller one hold the same place;
#: the OCR words there are duplicates only when both sides read the same (``merge_ocr_words``).
OCR_OVERLAP = 0.5
_BAND_PT = 24.0  # height of the row bands that index text-layer words for the hybrid merge


def _cy(box: BBox) -> float:
    return (box.y0 + box.y1) / 2.0


def _h(box: BBox) -> float:
    return max(box.y1 - box.y0, 0.1)


def group_lines(words: list[Word]) -> list[list[Word]]:
    """Words → visual lines in reading order (top to bottom, left to right).

    Words with a ``line_hint`` (OCR words: the service's line grouping, which survives skew) form
    one line per hint; words without one (text-layer words, and the OCR words a hybrid merge adds
    to them) are clustered by vertical centre: a word joins the current line when its centre lies
    within half the smaller height of the line's running centre.
    """
    if not words:
        return []
    by_hint: dict[int, list[Word]] = {}
    for w in words:
        if w.line_hint is not None:
            by_hint.setdefault(w.line_hint, []).append(w)
    lines: list[list[Word]] = list(by_hint.values())
    clustered: list[list[Word]] = []
    centres: list[float] = []
    heights: list[float] = []
    free = (w for w in words if w.line_hint is None)
    for w in sorted(free, key=lambda w: (_cy(w.bbox), w.bbox.x0)):
        cy, h = _cy(w.bbox), _h(w.bbox)
        if clustered and abs(cy - centres[-1]) <= 0.5 * min(h, heights[-1]):
            clustered[-1].append(w)
            centres[-1] += (cy - centres[-1]) / len(clustered[-1])
            heights[-1] = max(heights[-1], h)
        else:
            clustered.append([w])
            centres.append(cy)
            heights.append(h)
    lines.extend(clustered)
    ordered = [sorted(line, key=lambda w: w.bbox.x0) for line in lines]
    return sorted(ordered, key=lambda line: (min(w.bbox.y0 for w in line), line[0].bbox.x0))


def _median_char_width(line: list[Word]) -> float:
    return statistics.median((w.bbox.x1 - w.bbox.x0) / max(len(w.text), 1) for w in line)


def build_page_text(page: PageText, lines: list[list[Word]]) -> None:
    """Fill ``page.text``, ``words``, ``word_starts`` and ``word_lines`` from ordered lines."""
    parts: list[str] = []
    words: list[Word] = []
    starts: list[int] = []
    numbers: list[int] = []
    pos = 0
    for number, line in enumerate(lines, start=1):
        if number > 1:
            parts.append("\n")
            pos += 1
        char_w = _median_char_width(line)
        for i, w in enumerate(line):
            if i:
                gap = w.bbox.x0 - line[i - 1].bbox.x1
                parts.append(
                    "\t" if gap > max(COLUMN_GAP_CHARS * char_w, COLUMN_GAP_MIN_PT) else " "
                )
                pos += 1
            starts.append(pos)
            words.append(w)
            numbers.append(number)
            parts.append(w.text)
            pos += len(w.text)
    page.text = "".join(parts)
    page.words, page.word_starts, page.word_lines = words, starts, numbers


def words_in_span(page: PageText, start: int, end: int) -> list[tuple[int, Word]]:
    """``(index, word)`` for every word overlapping ``[start, end)`` of ``page.text``."""
    out: list[tuple[int, Word]] = []
    k = max(bisect.bisect_right(page.word_starts, start) - 1, 0)
    while k < len(page.words):
        ws, w = page.word_starts[k], page.words[k]
        if ws >= end:
            break
        if ws + len(w.text) > start:
            out.append((k, w))
        k += 1
    return out


def span_boxes(page: PageText, start: int, end: int, *, pad: float = REDACT_PAD_PT) -> list[BBox]:
    """Boxes covering ``[start, end)``: one per line touched.

    Character-precise when the word has character boxes (text layer); the whole word otherwise
    (OCR), because over-redaction is the safe side.
    """
    per_line: dict[int, BBox] = {}
    for k, w in words_in_span(page, start, end):
        ws = page.word_starts[k]
        lo, hi = max(start, ws) - ws, min(end, ws + len(w.text)) - ws
        if w.char_boxes and len(w.char_boxes) == len(w.text) and (lo > 0 or hi < len(w.text)):
            box = w.char_boxes[lo]
            for cb in w.char_boxes[lo + 1 : hi]:
                box = box.union(cb)
        else:
            box = w.bbox
        line = page.word_lines[k]
        per_line[line] = per_line[line].union(box) if line in per_line else box
    return [per_line[n].padded(pad, page.width, page.height) for n in sorted(per_line)]


def line_of_offset(page: PageText, offset: int) -> int:
    """1-based line number holding ``offset`` of ``page.text``."""
    if page.words:
        k = max(bisect.bisect_right(page.word_starts, offset) - 1, 0)
        return page.word_lines[k]
    return page.text.count("\n", 0, offset) + 1


def _linked(a: BBox, b: BBox, overlap: float) -> bool:
    """Whether two word boxes hold the same place (overlap of the smaller one)."""
    return a.intersection_area(b) >= overlap * max(min(a.area(), b.area()), 1e-6)


def _reading(words: list[Word]) -> str:
    """What words say, left to right, normalised (letters and digits, case-folded)."""
    return normalise("".join(w.text for w in sorted(words, key=lambda w: w.bbox.x0)))


def merge_ocr_words(
    text_words: list[Word], ocr_words: list[Word], *, overlap: float = OCR_OVERLAP
) -> list[Word]:
    """Hybrid pages: every text-layer word, plus every OCR word the text layer does not say.

    OCR words and text-layer words that hold the same place (boxes overlapping ``overlap`` of the
    smaller box) are grouped, transitively, so a word split differently on each side stays one
    group. A group whose OCR words read what its text-layer words read (normalised) is a
    duplicate: its OCR words are dropped. Otherwise the text layer is not what the page shows (an
    invisible OCR layer with errors, a "Specimen" overlay, a broken ``/ToUnicode`` map under an
    image), and since a redacted raster keeps what the page shows, the engine must see the OCR
    reading too (review B1): every OCR line holding such a word is kept whole, with its line
    hint, so it becomes a line of its own — a value split over words (a card number) stays with
    its cue ("Card") instead of interleaving with the text layer's reading. OCR words over no
    text-layer word join the text-layer lines (``line_hint`` cleared).
    """
    n = len(ocr_words)
    parent = list(range(n + len(text_words)))

    def root(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    bands: dict[int, list[int]] = {}
    for j, t in enumerate(text_words):
        for band in range(int(t.bbox.y0 // _BAND_PT), int(t.bbox.y1 // _BAND_PT) + 1):
            bands.setdefault(band, []).append(j)
    linked = [False] * n
    for i, o in enumerate(ocr_words):
        seen: set[int] = set()
        for band in range(int(o.bbox.y0 // _BAND_PT), int(o.bbox.y1 // _BAND_PT) + 1):
            for j in bands.get(band, ()):
                if j in seen:
                    continue
                seen.add(j)
                if _linked(o.bbox, text_words[j].bbox, overlap):
                    linked[i] = True
                    parent[root(i)] = root(n + j)
    groups: dict[int, tuple[list[Word], list[Word]]] = {}
    for i, o in enumerate(ocr_words):
        if linked[i]:
            groups.setdefault(root(i), ([], []))[0].append(o)
    for j, t in enumerate(text_words):
        if root(n + j) in groups:
            groups[root(n + j)][1].append(t)
    conflicting = {
        i
        for i in range(n)
        if linked[i] and _reading(groups[root(i)][0]) != _reading(groups[root(i)][1])
    }
    line_of: dict[int, int] = {}
    for number, line in enumerate(group_lines(list(ocr_words))):
        for o in line:
            line_of[id(o)] = number
    shown_lines = {line_of[id(ocr_words[i])] for i in conflicting}
    kept: list[Word] = []
    for i, o in enumerate(ocr_words):
        number = line_of[id(o)]
        if number in shown_lines:
            kept.append(dataclasses.replace(o, line_hint=number))
        elif not linked[i]:
            kept.append(dataclasses.replace(o, line_hint=None))
    return [*text_words, *kept]
