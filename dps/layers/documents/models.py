"""Document-layer models.

Internal models (``Word``, ``PageText``, ``ExtractedDocument``) hold extracted text for one
request only: never logged, never returned. Public models carry geometry, counts, entity types
and replacement tokens (already present in the output) — never an original value.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from dps.engine.service import RequestOptions
from dps.engine.taxonomy import Sensitivity

DocFormat = Literal["pdf", "docx", "image", "text", "csv"]
PageSource = Literal["text_layer", "ocr", "hybrid", "docx", "text", "csv", "empty", "unreadable"]
OcrMode = Literal["auto", "always", "never"]
WordSource = Literal["text_layer", "ocr"]

#: Joins page texts into the canonical document text: the pdftotext form feed, padded with
#: newlines so no pattern can span two pages.
PAGE_BREAK = "\n\f\n"


class BBox(BaseModel):
    """Axis-aligned box in PDF points, origin at the top-left of the normalised page."""

    model_config = ConfigDict(frozen=True)
    x0: float
    y0: float
    x1: float
    y1: float

    def area(self) -> float:
        """Area in square points (0 for degenerate boxes)."""
        return max(self.x1 - self.x0, 0.0) * max(self.y1 - self.y0, 0.0)

    def union(self, other: BBox) -> BBox:
        """The smallest box holding both."""
        return BBox(
            x0=min(self.x0, other.x0),
            y0=min(self.y0, other.y0),
            x1=max(self.x1, other.x1),
            y1=max(self.y1, other.y1),
        )

    def intersection_area(self, other: BBox) -> float:
        """Overlap area with ``other``."""
        w = min(self.x1, other.x1) - max(self.x0, other.x0)
        h = min(self.y1, other.y1) - max(self.y0, other.y0)
        return w * h if w > 0 and h > 0 else 0.0

    def padded(self, pad: float, width: float, height: float) -> BBox:
        """Grown by ``pad`` on every side, clamped to the page."""
        return BBox(
            x0=max(0.0, self.x0 - pad),
            y0=max(0.0, self.y0 - pad),
            x1=min(width, self.x1 + pad),
            y1=min(height, self.y1 + pad),
        )


@dataclass
class Word:
    """One positioned word (internal; holds text)."""

    text: str
    bbox: BBox
    source: WordSource
    confidence: float | None = None
    char_boxes: list[BBox] | None = None  # one per code unit of ``text`` (text layer only)
    line_hint: int | None = None  # OCR line index from the service


@dataclass
class PageText:
    """One page's words laid out as text (internal; holds text)."""

    page: int  # 1-based
    width: float  # points
    height: float
    source: PageSource
    text: str = ""
    words: list[Word] = field(default_factory=list)
    word_starts: list[int] = field(default_factory=list)  # offset of words[i] in text
    word_lines: list[int] = field(default_factory=list)  # 1-based line of words[i]
    ocr_confidence: float | None = None
    image_coverage: float = 0.0
    scan_suspect: bool = False  # few text-layer characters, or images cover much of it
    needs_ocr: bool = False  # OCR will be attempted (ocr mode + suspicion)
    ocr_done: bool = False


@dataclass
class ExtractedDocument:
    """An upload as pages of positioned words, or CSV records (internal; holds text)."""

    format: DocFormat
    pages: list[PageText]
    pdf: bytes | None = None  # the normalised PDF (crop at origin, rotation in content)
    image_png: bytes | None = None  # the normalised image (EXIF-transposed, RGB, ≤ 10000 px)
    image_dpi: float | None = None
    records: list[dict[str, str]] | None = None
    csv_header: list[str] | None = None
    csv_delimiter: str = ","
    warnings: list[str] = field(default_factory=list)

    def text(self) -> str:
        """The canonical document text the engine sees."""
        return PAGE_BREAK.join(p.text for p in self.pages)

    def page_starts(self) -> list[int]:
        """Offset of each page's text inside :meth:`text`."""
        starts: list[int] = []
        pos = 0
        for p in self.pages:
            starts.append(pos)
            pos += len(p.text) + len(PAGE_BREAK)
        return starts


class DocumentOptions(RequestOptions):
    """M0 request options plus the document knobs."""

    model_config = ConfigDict(hide_input_in_errors=True)
    redacted_pdf: bool = False
    ocr: OcrMode = "auto"
    previews: bool = False  # page PNGs (source, and redacted) for the console
    ocr_verify: bool | None = None  # None = settings.redact_ocr_verify


class DocumentRequest(BaseModel):
    """The JSON in the multipart form field ``request``."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    policy: str | dict[str, Any] | None = None
    options: DocumentOptions = Field(default_factory=DocumentOptions)


class PageBox(BaseModel):
    """A box on one page, in PDF points with a top-left origin."""

    page: int
    x0: float
    y0: float
    x1: float
    y1: float


class PageFinding(BaseModel):
    """A finding located on one page (a finding spanning two pages appears on both, same ``n``)."""

    n: int  # 1-based, document order; matches the box labels
    entity_type: str
    score: float
    sensitivity: Sensitivity
    detectors: list[str]
    evidence: list[str] = Field(default_factory=list)
    value_hash: str | None = None
    page: int
    line: int | None = None  # page line (text) or CSV line number
    path: str = "$"
    span: list[int] | None = None  # [start, end) in the canonical text; CSV: within the cell
    boxes: list[PageBox] = Field(default_factory=list)
    method: str | None = None
    reversible: bool | None = None
    redacted: bool = False  # transformed (method other than keep) → boxed in a redacted PDF
    replacement: str | None = None  # token / surrogate written in the output (never for keep)
    out_span: list[int] | None = None  # [start, end) of the replacement in ``result`` (text)
    ocr_confidence: float | None = None  # lowest OCR confidence of the words holding it
    review: bool = False  # OCR confidence below REVIEW_OCR_CONFIDENCE


class PageSummary(BaseModel):
    """One page of the response: size, source, counts, findings and optional previews."""

    page: int
    width: float
    height: float
    unit: Literal["pt"] = "pt"
    source: PageSource
    words: int = 0
    lines: int = 0
    ocr_confidence: float | None = None
    findings: list[PageFinding] = Field(default_factory=list)
    preview_png_b64: str | None = None
    redacted_preview_png_b64: str | None = None


class RedactionReport(BaseModel):
    """How a redacted PDF was built and what its inspection found."""

    boxes: int
    pages: int
    dpi: int
    image_format: str
    bytes: int
    sha256: str
    text_layer_chars: int  # characters pypdf extracts from the output (must be 0)
    text_operators: int  # text-showing operators in the output (must be 0)
    annotations: int
    ocr_checked: bool
    ocr_pages: int
    ocr_hits: int  # originals found by the OCR re-read (must be 0)
    chars_under_boxes: int  # characters OCR read inside painted boxes (informational)
    passed: bool


class DocumentInfo(BaseModel):
    """The response's ``document`` block: format, page and word counts, OCR, timings."""

    format: DocFormat
    pages: int
    ocr_pages: list[int]
    unreadable_pages: list[int]
    words: int
    boxes: int
    ocr_engine: str | None
    bytes: int
    sha256: str
    warnings: list[str]
    timings_ms: dict[str, float]
    redaction: RedactionReport | None = None
    synthetic_validity: dict[str, int] | None = None  # {checked, valid} rule-valid surrogate types


def validation_message(exc: ValidationError) -> str:
    """``loc: msg; …`` without input values (safe to return; bounded to 500 characters)."""
    parts = (
        f"{'.'.join(str(p) for p in e['loc']) or 'request'}: {e['msg']}"
        for e in exc.errors(include_input=False, include_url=False)
    )
    return "; ".join(parts)[:500]
