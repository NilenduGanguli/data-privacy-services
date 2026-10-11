"""Page geometry → text and back (pure functions).

``build_page_text`` lays words out as lines (``\\n``) and words (a space, or a tab across a wide
gap such as a table column) and records every word's offset, so a finding's span in the page text
maps back to exactly the words — and on a text layer the characters — that hold it.
"""

from __future__ import annotations

import bisect
import dataclasses
import statistics

from dps.layers.documents.models import BBox, PageText, Word

REDACT_PAD_PT = 1.5  # boxes grow by this on every side (anti-aliasing, OCR jitter)
COLUMN_GAP_CHARS = 2.5  # a gap wider than this many median character widths becomes a tab …
COLUMN_GAP_MIN_PT = 6.0  # … and wider than this many points
OCR_OVERLAP = 0.5  # an OCR word covered this much by a text-layer word is a duplicate


def _cy(box: BBox) -> float:
    return (box.y0 + box.y1) / 2.0


def _h(box: BBox) -> float:
    return max(box.y1 - box.y0, 0.1)


def group_lines(words: list[Word]) -> list[list[Word]]:
    """Words → visual lines in reading order (top to bottom, left to right).

    OCR words carry the service's line grouping (``line_hint``), which survives skew; mixed or
    text-layer words are clustered by vertical centre: a word joins the current line when its
    centre lies within half the smaller height of the line's running centre.
    """
    if not words:
        return []
    lines: list[list[Word]]
    if all(w.line_hint is not None for w in words):
        by_hint: dict[int, list[Word]] = {}
        for w in words:
            by_hint.setdefault(w.line_hint or 0, []).append(w)
        lines = list(by_hint.values())
    else:
        lines = []
        centres: list[float] = []
        heights: list[float] = []
        for w in sorted(words, key=lambda w: (_cy(w.bbox), w.bbox.x0)):
            cy, h = _cy(w.bbox), _h(w.bbox)
            if lines and abs(cy - centres[-1]) <= 0.5 * min(h, heights[-1]):
                lines[-1].append(w)
                centres[-1] += (cy - centres[-1]) / len(lines[-1])
                heights[-1] = max(heights[-1], h)
            else:
                lines.append([w])
                centres.append(cy)
                heights.append(h)
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


def merge_ocr_words(
    text_words: list[Word], ocr_words: list[Word], *, overlap: float = OCR_OVERLAP
) -> list[Word]:
    """Hybrid pages: every text-layer word, plus the OCR words the text layer does not cover."""
    extra = [
        dataclasses.replace(o, line_hint=None)
        for o in ocr_words
        if not any(
            o.bbox.intersection_area(t.bbox) >= overlap * max(o.bbox.area(), 1e-6)
            for t in text_words
        )
    ]
    return [*text_words, *extra]
