from __future__ import annotations

from dps.layers.documents.layout import (
    build_page_text,
    group_lines,
    line_of_offset,
    merge_ocr_words,
    span_boxes,
    words_in_span,
)
from dps.layers.documents.models import BBox, PageText, Word


def w(
    text: str,
    x0: float,
    y0: float,
    *,
    source: str = "text_layer",
    chars: bool = True,
    line: int | None = None,
    cw: float = 6.0,
    h: float = 12.0,
) -> Word:
    box = BBox(x0=x0, y0=y0, x1=x0 + cw * len(text), y1=y0 + h)
    cb = (
        [BBox(x0=x0 + cw * i, y0=y0, x1=x0 + cw * (i + 1), y1=y0 + h) for i in range(len(text))]
        if chars
        else None
    )
    return Word(text=text, bbox=box, source=source, char_boxes=cb, line_hint=line)  # type: ignore[arg-type]


def page(words: list[Word]) -> PageText:
    p = PageText(page=1, width=612, height=792, source="text_layer")
    build_page_text(p, group_lines(words))
    return p


def test_group_lines_orders_reading_order() -> None:
    p = page(
        [
            w("SSN", 72, 100),
            w("Krishnan", 150, 80.5),
            w("Name:", 72, 80),
            w("123-45-6789", 100, 101),
            w("Ananya", 110, 79.6),
        ]
    )
    assert p.text == "Name: Ananya Krishnan\nSSN 123-45-6789"
    assert all(
        p.text[s : s + len(x.text)] == x.text for s, x in zip(p.word_starts, p.words, strict=True)
    )
    assert p.word_lines == [1, 1, 1, 2, 2]


def test_wide_gaps_become_tabs() -> None:
    assert page([w("Account", 72, 100), w("5010", 300, 100)]).text == "Account\t5010"


def test_span_boxes_are_character_precise_on_text_layers() -> None:
    p = page([w("SSN:123-45-6789", 72, 100)])
    start = p.text.index("123")
    (box,) = span_boxes(p, start, start + 11, pad=0)
    assert (box.x0, box.x1, box.y0) == (72 + 6 * 4, 72 + 6 * 15, 100)


def test_span_boxes_one_per_line_and_padded() -> None:
    p = page(
        [w("14", 72, 100), w("Lavelle", 90, 100), w("Road,", 140, 100), w("Bengaluru", 72, 120)]
    )
    boxes = span_boxes(p, 0, len(p.text))
    assert len(boxes) == 2 and boxes[0].x0 == 72 - 1.5 and boxes[1].y0 == 120 - 1.5


def test_ocr_words_redact_whole_words() -> None:
    p = page([w("SSN:123-45-6789", 72, 100, source="ocr", chars=False, line=0)])
    start = p.text.index("123")
    (box,) = span_boxes(p, start, start + 11, pad=0)
    assert (box.x0, box.x1) == (72, 72 + 6 * 15)


def test_padding_clamps_to_the_page() -> None:
    (box,) = span_boxes(page([w("edge", 0, 0)]), 0, 4)
    assert box.x0 == 0 and box.y0 == 0


def test_line_hints_survive_skew() -> None:
    words = [
        w("Name:", 72, 80, source="ocr", chars=False, line=0),
        w("Ananya", 110, 88, source="ocr", chars=False, line=0),
        w("SSN", 72, 96, source="ocr", chars=False, line=1),
    ]
    assert page(words).text == "Name: Ananya\nSSN"


def test_merge_ocr_words_prefers_the_text_layer() -> None:
    merged = merge_ocr_words(
        [w("SSN", 72, 100)],
        [
            w("SSN", 72.5, 100.5, source="ocr", chars=False, line=3),
            w("STAMP", 300, 400, source="ocr", chars=False, line=4),
        ],
    )
    assert [x.text for x in merged] == ["SSN", "STAMP"] and merged[1].line_hint is None


def test_merge_ocr_words_keeps_what_the_text_layer_reads_otherwise() -> None:
    """Review B1: an invisible OCR layer with errors under a page image. The OCR word is what the
    page shows, so it is kept, and its OCR line is kept whole on a line of its own: the value and
    its cue ("SSN") stay together and do not interleave with the text layer's reading."""
    text = [w("SSN", 72, 100), w("1Z3-4S-67B9", 100, 100), w("on", 172, 100)]
    ocr = [
        w("SSN", 72.4, 100.3, source="ocr", chars=False, line=5),
        w("123-45-6789", 100.5, 100.2, source="ocr", chars=False, line=5),
        w("on", 172.2, 100.1, source="ocr", chars=False, line=5),
    ]
    merged = merge_ocr_words(text, ocr)
    assert [x.text for x in merged] == ["SSN", "1Z3-4S-67B9", "on", "SSN", "123-45-6789", "on"]
    assert page(merged).text == "SSN 1Z3-4S-67B9 on\nSSN 123-45-6789 on"


def test_merge_ocr_words_keeps_a_line_an_overlay_hides() -> None:
    """A text layer that says "Specimen" over a drawn line: the whole OCR line survives."""
    text = [w("Specimen", 72, 100), w("Terms", 72, 140)]
    ocr = [
        w("Card", 72.2, 100.2, source="ocr", chars=False, line=0),
        w("4111", 104, 100.2, source="ocr", chars=False, line=0),
        w("1111", 134, 100.2, source="ocr", chars=False, line=0),
        w("Terms", 72.1, 140.1, source="ocr", chars=False, line=1),
    ]
    assert page(merge_ocr_words(text, ocr)).text == "Specimen\nCard 4111 1111\nTerms"


def test_merge_ocr_words_drops_duplicates_however_the_words_split() -> None:
    """The same reading in another segmentation, case or punctuation is a duplicate."""
    text = [w("Kri", 72, 100), w("shnan,", 90, 100), w("ananya.k@example-mail.in", 200, 100)]
    ocr = [
        w("KRISHNAN", 72.3, 100.2, source="ocr", chars=False, line=1),
        w("ananya.k@", 200.2, 100.2, source="ocr", chars=False, line=1),
        w("example-mail.in", 254.5, 100.2, source="ocr", chars=False, line=1),
    ]
    assert merge_ocr_words(text, ocr) == text


def test_line_of_offset_and_words_in_span() -> None:
    p = page([w("a", 72, 100), w("b", 72, 120)])
    assert line_of_offset(p, p.text.index("b")) == 2
    assert [x.text for _, x in words_in_span(p, 0, len(p.text))] == ["a", "b"]
    assert line_of_offset(PageText(page=1, width=1, height=1, source="text", text="x\ny"), 2) == 2
