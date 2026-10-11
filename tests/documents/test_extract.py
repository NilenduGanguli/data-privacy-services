from __future__ import annotations

import contextlib
import io
import time
import zipfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.docs
pytest.importorskip("docx")
pytest.importorskip("PIL")

from dps.config import Settings  # noqa: E402
from dps.layers.documents import extract  # noqa: E402
from dps.layers.documents.errors import DocumentError  # noqa: E402
from dps.layers.documents.extract import extract_document, sniff_format  # noqa: E402
from tests.documents.fixtures import (  # noqa: E402
    CUSTOMERS,
    csv_document,
    docx_document,
    huge_png,
    jpeg_rotated_exif,
    png_document,
    text_pdf,
)

SET = Settings(_env_file=None)
W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _with_footnotes(footnotes_xml: bytes) -> bytes:
    """The fixture .docx with a footnotes part (a part python-docx does not model)."""
    source = zipfile.ZipFile(io.BytesIO(docx_document()))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as target:
        for item in source.infolist():
            data = source.read(item.filename)
            if item.filename == "[Content_Types].xml":
                data = data.replace(
                    b"</Types>",
                    b'<Override PartName="/word/footnotes.xml" ContentType="application/'
                    b'vnd.openxmlformats-officedocument.wordprocessingml.footnotes+xml"/></Types>',
                )
            elif item.filename == "word/_rels/document.xml.rels":
                data = data.replace(
                    b"</Relationships>",
                    b'<Relationship Id="rId99" Type="http://schemas.openxmlformats.org/'
                    b'officeDocument/2006/relationships/footnotes" Target="footnotes.xml"/>'
                    b"</Relationships>",
                )
            target.writestr(item, data)
        target.writestr("word/footnotes.xml", footnotes_xml)
    return out.getvalue()


@pytest.mark.parametrize(
    "content,name,ctype,expected",
    [
        (b"%PDF-1.7 ...", None, None, "pdf"),
        (b"\x89PNG\r\n\x1a\nxxxx", None, None, "image"),
        (b"\xff\xd8\xff\xe0xxxx", None, None, "image"),
        (b"a,b\n1,2\n", "customers.csv", None, "csv"),
        (b"a;b\n1;2\n", None, "text/csv; charset=utf-8", "csv"),
        (b"Dear Ananya,\nyour SSN", None, None, "text"),
    ],
)
def test_sniff_by_magic_then_name(
    content: bytes, name: str | None, ctype: str | None, expected: str
) -> None:
    assert sniff_format(content, name, ctype) == expected


def test_sniff_docx_and_refusals() -> None:
    assert sniff_format(docx_document(), "x.bin", None) == "docx"
    other = io.BytesIO()
    with zipfile.ZipFile(other, "w") as z:
        z.writestr("a.txt", "x")
    for content, code, status in (
        (other.getvalue(), "unsupported_format", 415),
        (b"\x00\x01\x02binary", "unsupported_format", 415),
        (b"", "empty_document", 422),
    ):
        with pytest.raises(DocumentError) as exc:
            sniff_format(content, None, None)
        assert (exc.value.code, exc.value.status) == (code, status)


def test_docx_reads_headers_tables_and_breaks() -> None:
    doc = extract_document(docx_document(), "docx", SET, "auto")
    assert doc.pages[0].source == "docx"
    assert doc.pages[0].text.splitlines() == [
        "Ref CUST-884120",
        "Customer: Ananya Krishnan",
        "SSN\t123-45-6789",
        "Card\t4111 1111 1111 1111",
        "Mail ananya.k@example-mail.in",
        "call +91 98451 20377",
    ]


def test_docx_zip_bomb_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        extract, "ZIP_RATIO_LIMIT", 0.25
    )  # 1 MB limit → 256 KB inflated; this .docx inflates past it
    with pytest.raises(DocumentError) as exc:
        extract_document(
            docx_document(), "docx", Settings(_env_file=None, max_document_mb=1), "auto"
        )
    assert (exc.value.code, exc.value.status) == ("document_too_large", 413)


def test_text_files_are_utf8_with_newlines_normalised() -> None:
    doc = extract_document("﻿line one\r\nline two\rline three".encode(), "text", SET, "auto")
    assert doc.pages[0].text == "line one\nline two\nline three"
    with pytest.raises(DocumentError) as exc:
        extract_document("caf\xe9".encode("latin-1"), "text", SET, "auto")
    assert exc.value.code == "unsupported_encoding"


def test_csv_records_dialect_and_limits() -> None:
    doc = extract_document(csv_document(CUSTOMERS, delimiter=";"), "csv", SET, "auto")
    assert (
        doc.records == CUSTOMERS
        and doc.csv_header == list(CUSTOMERS[0])
        and doc.csv_delimiter == ";"
    )
    dup = extract_document(b"name,name,\nA,B,C\n", "csv", SET, "auto")
    assert dup.csv_header == ["name", "name_2", "column_3"]
    with pytest.raises(DocumentError) as ragged:
        extract_document(b"a,b\n1,2,3\n", "csv", SET, "auto")
    assert ragged.value.code == "csv_ragged_rows"
    with pytest.raises(DocumentError) as many:
        extract_document(
            csv_document(CUSTOMERS), "csv", Settings(_env_file=None, max_items=5), "auto"
        )
    assert (many.value.code, many.value.status) == ("too_many_values", 413)


def test_png_keeps_dpi_and_page_size() -> None:
    doc = extract_document(png_document(dpi=200), "image", SET, "auto")
    page = doc.pages[0]
    assert (
        doc.image_dpi == pytest.approx(200, abs=1)
        and page.needs_ocr
        and page.source == "unreadable"
    )
    assert page.width == pytest.approx(612, abs=1) and page.height == pytest.approx(792, abs=1)


def test_jpeg_exif_orientation_is_applied() -> None:
    from PIL import Image

    doc = extract_document(jpeg_rotated_exif(300, 100), "image", SET, "auto")
    with Image.open(io.BytesIO(doc.image_png)) as im:
        assert im.size == (100, 300)


def test_huge_images_are_refused_before_decoding() -> None:
    with pytest.raises(DocumentError) as exc:
        extract_document(huge_png(), "image", SET, "auto")
    assert (exc.value.code, exc.value.status) == ("image_too_large", 413)


def test_dispatch_pdf() -> None:
    assert extract_document(text_pdf(), "pdf", SET, "auto").format == "pdf"


@pytest.mark.parametrize("delimiter", [",", ";", "\t", "|"])
def test_csv_delimiters_are_detected(delimiter: str) -> None:
    doc = extract_document(csv_document(CUSTOMERS, delimiter=delimiter), "csv", SET, "auto")
    assert doc.csv_delimiter == delimiter and doc.records == CUSTOMERS
    single = extract_document(b"name\nAnanya\n", "csv", SET, "auto")
    assert single.csv_delimiter == "," and single.records == [{"name": "Ananya"}]


def test_csv_delimiter_detection_is_linear_on_crafted_input() -> None:
    # csv.Sniffer's quote regexes backtrack quadratically on this 64 KB sample: 6.5 s on the
    # build Mac (0.4 s at 16 KB), against about 6 ms for the linear detection used instead.
    crafted = (",'" * 32_768).encode()
    started = time.perf_counter()
    with contextlib.suppress(DocumentError):
        extract_document(crafted, "csv", SET, "auto")
    assert time.perf_counter() - started < 1.0


def test_csv_parse_errors_are_coded() -> None:
    with pytest.raises(DocumentError) as exc:
        extract_document(b"a,b\n" + b"x" * 200_000 + b",1\n", "csv", SET, "auto")
    assert exc.value.code == "corrupt_document" and "xxxx" not in str(exc.value)


def test_sniff_text_with_a_multibyte_character_across_the_sample_end() -> None:
    content = b"a" * 65_535 + "é".encode() + b" end"
    assert sniff_format(content, None, "application/octet-stream") == "text"


def test_only_png_and_jpeg_are_decoded() -> None:
    from PIL import Image

    gif = io.BytesIO()
    Image.new("RGB", (20, 10), "white").save(gif, "GIF")
    with pytest.raises(DocumentError) as exc:
        extract_document(gif.getvalue(), "image", SET, "auto")
    assert exc.value.code == "corrupt_document"


def test_docx_unmodelled_parts_are_read_and_malformed_ones_refused() -> None:
    note = (
        f'<w:footnotes xmlns:w="{W_NS}"><w:footnote w:id="1"><w:p><w:r>'
        "<w:t>Footnote CUST-884121</w:t></w:r></w:p></w:footnote></w:footnotes>"
    ).encode()
    lines = extract_document(_with_footnotes(note), "docx", SET, "auto").pages[0].text.splitlines()
    assert lines[-1] == "Footnote CUST-884121"
    with pytest.raises(DocumentError) as exc:
        extract_document(_with_footnotes(b"<w:footnotes><unclosed>"), "docx", SET, "auto")
    assert exc.value.code == "corrupt_document"


def test_docx_parts_never_resolve_external_entities(tmp_path: Path) -> None:
    secret = tmp_path / "outside.txt"
    secret.write_text("XXE-LEAK-MARKER")
    note = (
        f'<?xml version="1.0"?><!DOCTYPE w:footnotes [<!ENTITY x SYSTEM "{secret.as_uri()}">]>'
        f'<w:footnotes xmlns:w="{W_NS}"><w:footnote w:id="1"><w:p><w:r>'
        "<w:t>Note &x; end</w:t></w:r></w:p></w:footnote></w:footnotes>"
    ).encode()
    text = extract_document(_with_footnotes(note), "docx", SET, "auto").pages[0].text
    assert "Note" in text and "XXE-LEAK-MARKER" not in text


def test_csv_duplicate_header_names_are_numbered_in_linear_time() -> None:
    doc = extract_document(b"a,a,a,a_2\n1,2,3,4\n", "csv", SET, "auto")
    assert doc.csv_header == ["a", "a_2", "a_3", "a_2_2"]
    started = time.perf_counter()
    wide = extract_document(b",".join([b"x"] * 20_000) + b"\n", "csv", SET, "auto")
    assert time.perf_counter() - started < 1.0
    assert wide.csv_header is not None and wide.csv_header[-1] == "x_20000"


def _png_with_broken_second_idat() -> bytes:
    """A PNG whose image data is split over two IDAT chunks, the second with an invalid chunk
    type: Pillow's decoder raises ``SyntaxError`` (not ``OSError``) when it reaches it."""
    import struct
    import zlib

    def chunk(kind: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(kind + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", crc)

    raw = b"".join(b"\x00" + bytes([(x * 7 + y) % 256 for x in range(64 * 3)]) for y in range(48))
    data = zlib.compress(raw)
    header = chunk(b"IHDR", struct.pack(">IIBBBBB", 64, 48, 8, 2, 0, 0, 0))
    half = len(data) // 2
    return (
        b"\x89PNG\r\n\x1a\n"
        + header
        + chunk(b"IDAT", data[:half])
        + chunk(b"\x15DAT", data[half:])
        + chunk(b"IEND", b"")
    )


def test_broken_png_chunks_are_coded() -> None:
    from PIL import Image

    broken = _png_with_broken_second_idat()
    with pytest.raises(SyntaxError), Image.open(io.BytesIO(broken)) as raw:
        raw.load()
    with pytest.raises(DocumentError) as exc:
        extract_document(broken, "image", SET, "auto")
    assert exc.value.code == "corrupt_document"
