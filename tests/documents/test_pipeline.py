from __future__ import annotations

import base64
import csv
import io

import pytest

pytestmark = pytest.mark.docs
pytest.importorskip("pdfplumber")

from dps.config import Settings  # noqa: E402
from dps.engine.service import Payload, PrivacyEngine  # noqa: E402
from dps.layers.documents import DocumentPipeline  # noqa: E402
from dps.layers.documents.errors import DocumentError  # noqa: E402
from dps.layers.documents.extract import extract_document  # noqa: E402
from dps.layers.documents.models import DocumentRequest  # noqa: E402
from dps.layers.documents.ocr import NullOcrClient, OcrClient, OcrError, OcrPage  # noqa: E402
from dps.layers.documents.redact import inspect_output_pdf  # noqa: E402
from dps.llm.gateway import LlmGateway  # noqa: E402
from tests.documents.fixtures import (  # noqa: E402
    CUSTOMERS,
    GARBLED_LAYER,
    KYC_LINES,
    SPECIMEN_LAYER,
    FakeOcr,
    ScreenOcr,
    broken_tounicode_pdf,
    csv_document,
    docx_document,
    hybrid_pdf,
    png_document,
    scanned_pdf,
    searchable_scan,
    small_image_pdf,
    text_pdf,
    words_px,
)


def _settings(**kw: object) -> Settings:
    return Settings(_env_file=None, llm_provider="stub", ner_enabled=False, fpe_enabled=False, **kw)  # type: ignore[arg-type]


@pytest.fixture
def settings() -> Settings:
    return _settings()


@pytest.fixture
def engine(settings: Settings) -> PrivacyEngine:
    return PrivacyEngine(settings, gateway=LlmGateway(settings))


def pipe(
    engine: PrivacyEngine, settings: Settings, ocr: OcrClient | None = None
) -> DocumentPipeline:
    return DocumentPipeline(engine, settings, ocr or NullOcrClient())


def req(policy: str | None = None, **options: object) -> DocumentRequest:
    return DocumentRequest.model_validate({"policy": policy, "options": options})


def _reveal(engine: PrivacyEngine, result: object, transform) -> object:  # noqa: ANN001
    payload = Payload(text=result) if isinstance(result, str) else Payload(data=result)
    return engine.reveal(
        payload,
        transform.key_material.model_dump(mode="json"),
        [h.model_dump(mode="json") for h in transform.reveal_hints],
    ).result


async def test_text_pdf_detect_maps_findings_to_boxes(
    engine: PrivacyEngine, settings: Settings
) -> None:
    out = await pipe(engine, settings).run(
        mode="detect",
        content=text_pdf(),
        filename="kyc.pdf",
        content_type="application/pdf",
        request=req(),
    )
    page = out.pages[0]
    assert {"SSN", "EMAIL", "CARD_PAN"} <= {f.entity_type for f in page.findings}
    assert all(f.boxes for f in page.findings)
    assert [f.n for f in page.findings] == list(range(1, len(page.findings) + 1))
    ssn = next(f for f in page.findings if f.entity_type == "SSN")
    word = next(
        w
        for w in extract_document(text_pdf(), "pdf", settings, "auto").pages[0].words
        if w.text == "123-45-6789"
    )
    assert ssn.boxes[0].x0 <= word.bbox.x0 and ssn.boxes[0].x1 >= word.bbox.x1 and ssn.line == 2
    assert out.detect is not None and out.transform is None and out.document.ocr_pages == []
    assert out.document.boxes == sum(len(f.boxes) for f in page.findings)


async def test_obfuscate_with_redaction_and_reveal(
    engine: PrivacyEngine, settings: Settings
) -> None:
    out = await pipe(engine, settings).run(
        mode="obfuscate",
        content=text_pdf(),
        filename="kyc.pdf",
        content_type=None,
        request=req(redacted_pdf=True),
    )
    transform = out.transform
    assert transform is not None and out.redacted_pdf is not None
    redaction = out.document.redaction
    assert redaction is not None and redaction.passed and redaction.text_layer_chars == 0
    assert not redaction.ocr_checked and "redaction_not_ocr_verified" in out.document.warnings
    assert inspect_output_pdf(out.redacted_pdf).clean and b"123-45-6789" not in out.redacted_pdf
    ssn = next(f for f in out.pages[0].findings if f.entity_type == "SSN")
    assert ssn.redacted and ssn.replacement and ssn.out_span
    assert transform.result[ssn.out_span[0] : ssn.out_span[1]] == ssn.replacement
    assert "123-45-6789" not in transform.result
    original = extract_document(text_pdf(), "pdf", settings, "auto").text()
    assert _reveal(engine, transform.result, transform) == original


async def test_scanned_pdf_is_read_redacted_and_ocr_verified(
    engine: PrivacyEngine, settings: Settings
) -> None:
    ocr = FakeOcr([words_px(text_pdf())[0], []])
    out = await pipe(engine, settings, ocr).run(
        mode="obfuscate",
        content=scanned_pdf(),
        filename=None,
        content_type=None,
        request=req(redacted_pdf=True),
    )
    assert out.document.ocr_pages == [1] and out.document.ocr_engine == "fake-ocr"
    assert out.pages[0].source == "ocr" and out.pages[0].ocr_confidence == 0.97
    assert any(f.entity_type == "SSN" and f.boxes for f in out.pages[0].findings)
    redaction = out.document.redaction
    assert redaction is not None and redaction.ocr_checked and redaction.ocr_hits == 0
    assert redaction.ocr_pages == 1 and ocr.calls == 2


async def test_ocr_reread_fails_closed_on_a_leak(engine: PrivacyEngine, settings: Settings) -> None:
    words = words_px(text_pdf())[0]
    with pytest.raises(DocumentError) as exc:
        await pipe(engine, settings, FakeOcr([words, words])).run(
            mode="obfuscate",
            content=scanned_pdf(),
            filename=None,
            content_type=None,
            request=req(redacted_pdf=True),
        )
    assert (exc.value.code, exc.value.status) == ("leakage", 422)


async def test_unconfigured_ocr(engine: PrivacyEngine, settings: Settings) -> None:
    plain = await pipe(engine, settings).run(
        mode="detect", content=scanned_pdf(), filename=None, content_type=None, request=req()
    )
    assert plain.document.unreadable_pages == [1] and "ocr_unconfigured" in plain.document.warnings
    assert plain.pages[0].findings == []
    with pytest.raises(DocumentError) as exc:
        await pipe(engine, settings).run(
            mode="obfuscate",
            content=scanned_pdf(),
            filename=None,
            content_type=None,
            request=req(redacted_pdf=True),
        )
    assert (exc.value.code, exc.value.status) == ("ocr_unconfigured", 503)


async def test_ocr_never_refuses_to_redact_scanned_pages(
    engine: PrivacyEngine, settings: Settings
) -> None:
    with pytest.raises(DocumentError) as exc:
        await pipe(engine, settings, FakeOcr([])).run(
            mode="obfuscate",
            content=scanned_pdf(),
            filename=None,
            content_type=None,
            request=req(redacted_pdf=True, ocr="never"),
        )
    assert (exc.value.code, exc.value.status) == ("redaction_needs_ocr", 422)


async def test_ocr_outage_is_503(engine: PrivacyEngine, settings: Settings) -> None:
    class Down(FakeOcr):
        async def read(self, image: bytes, *, content_type: str = "image/png"):  # noqa: ANN201
            raise OcrError("ocr_unavailable", "analyze returned HTTP 500")

    with pytest.raises(DocumentError) as exc:
        await pipe(engine, settings, Down([])).run(
            mode="detect", content=scanned_pdf(), filename=None, content_type=None, request=req()
        )
    assert (exc.value.code, exc.value.status) == ("ocr_unavailable", 503)


@pytest.mark.parametrize(
    "mode,options,make,code",
    [
        ("detect", {"redacted_pdf": True}, text_pdf, "redacted_pdf_not_allowed"),
        ("obfuscate", {"redacted_pdf": True}, docx_document, "redaction_unsupported_format"),
        ("detect", {"ocr": "never"}, png_document, "ocr_required"),
    ],
)
async def test_option_refusals(
    engine: PrivacyEngine,
    settings: Settings,
    mode: str,
    options: dict[str, object],
    make,
    code: str,
) -> None:  # noqa: ANN001
    with pytest.raises(DocumentError) as exc:
        await pipe(engine, settings, FakeOcr([])).run(
            mode=mode,
            content=make(),
            filename=None,  # type: ignore[arg-type]
            content_type=None,
            request=req(**options),
        )
    assert (exc.value.code, exc.value.status) == (code, 422)


async def test_docx_outputs_text_without_boxes(engine: PrivacyEngine, settings: Settings) -> None:
    out = await pipe(engine, settings).run(
        mode="obfuscate",
        content=docx_document(),
        filename="kyc.docx",
        content_type=None,
        request=req(),
    )
    assert out.document.format == "docx" and out.transform is not None
    assert "123-45-6789" not in out.transform.result
    assert out.pages[0].findings and all(not f.boxes for f in out.pages[0].findings)


async def test_csv_round_trip(engine: PrivacyEngine, settings: Settings) -> None:
    out = await pipe(engine, settings).run(
        mode="obfuscate",
        content=csv_document(CUSTOMERS),
        filename="c.csv",
        content_type="text/csv",
        request=req(),
    )
    assert out.result_csv is not None and out.transform is not None
    rows = list(csv.DictReader(io.StringIO(out.result_csv)))
    assert list(rows[0]) == list(CUSTOMERS[0]) and len(rows) == len(CUSTOMERS)
    assert rows[0]["email"] != CUSTOMERS[0]["email"] and rows[0]["risk_score"] == "712"
    assert {f.line for f in out.pages[0].findings} <= {2, 3}
    assert _reveal(engine, out.transform.result, out.transform) == CUSTOMERS


async def test_synthesize_reports_replacements_and_validity(
    engine: PrivacyEngine, settings: Settings
) -> None:
    out = await pipe(engine, settings).run(
        mode="synthesize",
        content=text_pdf(),
        filename=None,
        content_type=None,
        request=req("synthetic-realistic"),
    )
    assert out.transform is not None
    card = next(f for f in out.pages[0].findings if f.entity_type == "CARD_PAN")
    assert card.replacement and card.out_span
    assert out.transform.result[card.out_span[0] : card.out_span[1]] == card.replacement
    validity = out.document.synthetic_validity
    assert (
        validity is not None
        and validity["checked"] >= 1
        and validity["valid"] == validity["checked"]
    )


async def test_previews_are_pngs_and_capped() -> None:
    s = _settings(max_preview_pages=2)
    out = await pipe(PrivacyEngine(s, gateway=LlmGateway(s)), s).run(
        mode="obfuscate",
        content=text_pdf(pages=3),
        filename=None,
        content_type=None,
        request=req(previews=True, redacted_pdf=True),
    )
    assert [p.preview_png_b64 is not None for p in out.pages] == [True, True, False]
    assert base64.b64decode(out.pages[0].redacted_preview_png_b64 or "").startswith(b"\x89PNG")


async def test_text_limit() -> None:
    s = _settings(max_text_chars=20)
    with pytest.raises(DocumentError) as exc:
        await pipe(PrivacyEngine(s, gateway=LlmGateway(s)), s).run(
            mode="detect", content=text_pdf(), filename=None, content_type=None, request=req()
        )
    assert (exc.value.code, exc.value.status) == ("text_too_long", 413)


async def test_hybrid_pages_merge_ocr_words(engine: PrivacyEngine, settings: Settings) -> None:
    extra = [
        ("Ref", (300.0, 1500.0, 360.0, 1530.0)),
        ("CUST-884120", (370.0, 1500.0, 600.0, 1530.0)),
    ]
    out = await pipe(engine, settings, FakeOcr([words_px(hybrid_pdf())[0] + extra])).run(
        mode="detect", content=hybrid_pdf(), filename=None, content_type=None, request=req()
    )
    text_words = len(extract_document(hybrid_pdf(), "pdf", settings, "auto").pages[0].words)
    assert out.pages[0].source == "hybrid" and out.pages[0].words == text_words + 2


# --- beyond the plan ------------------------------------------------------------------------


class _Resized(FakeOcr):
    """An OCR that reports a page size other than the image sent (a rescaling gateway) on the
    calls listed in ``wrong``."""

    def __init__(self, pages, *, wrong: set[int]) -> None:  # noqa: ANN001
        super().__init__(pages)
        self.wrong = wrong

    async def read(self, image: bytes, *, content_type: str = "image/png") -> OcrPage:
        call = self.calls
        page = await super().read(image, content_type=content_type)
        if call not in self.wrong:
            return page
        return OcrPage(
            width=page.width / 2, height=page.height / 2, unit="pixel", angle=0.0, words=page.words
        )


async def test_an_ocr_page_size_other_than_the_image_sent_is_refused(
    engine: PrivacyEngine, settings: Settings
) -> None:
    """Review N8: words in another pixel space would put every box in the wrong place."""
    with pytest.raises(DocumentError) as exc:
        await pipe(engine, settings, _Resized([words_px(text_pdf())[0]], wrong={0})).run(
            mode="detect", content=scanned_pdf(), filename=None, content_type=None, request=req()
        )
    assert (exc.value.code, exc.value.status) == ("ocr_unavailable", 503)
    assert "ocr_protocol" in exc.value.detail


async def test_the_reread_refuses_a_page_size_other_than_the_image_sent(
    engine: PrivacyEngine, settings: Settings
) -> None:
    ocr = _Resized([words_px(text_pdf())[0], []], wrong={1})
    with pytest.raises(DocumentError) as exc:
        await pipe(engine, settings, ocr).run(
            mode="obfuscate",
            content=scanned_pdf(),
            filename=None,
            content_type=None,
            request=req(redacted_pdf=True),
        )
    assert (exc.value.code, exc.value.status) == ("ocr_unavailable", 503)
    assert "ocr_protocol" in exc.value.detail and ocr.calls == 2


#: An e-mail printed inside the small image of ``small_image_pdf`` (pixels at 200 dpi).
_IN_IMAGE = [("rohan.d@example-mail.in", (1120.0, 1800.0, 1380.0, 1840.0))]


async def test_a_redacted_pdf_reads_pages_with_small_images(
    engine: PrivacyEngine, settings: Settings
) -> None:
    """Review §5: an image below ``ocr_image_coverage`` on a text page is OCR'd when a redacted
    PDF is asked for, so a value printed in it is detected and boxed rather than rasterised."""
    doc = extract_document(small_image_pdf(), "pdf", settings, "auto")
    assert 0 < doc.pages[0].image_coverage < settings.ocr_image_coverage
    assert not doc.pages[0].needs_ocr
    ocr = FakeOcr([words_px(small_image_pdf())[0] + _IN_IMAGE, []])
    out = await pipe(engine, settings, ocr).run(
        mode="obfuscate",
        content=small_image_pdf(),
        filename=None,
        content_type=None,
        request=req(redacted_pdf=True),
    )
    assert out.pages[0].source == "hybrid" and out.document.ocr_pages == [1] and ocr.calls == 2
    emails = [f for f in out.pages[0].findings if f.entity_type == "EMAIL"]
    in_image = [b for f in emails for b in f.boxes if b.y0 >= 620]
    assert in_image and out.document.redaction is not None and out.document.redaction.passed
    assert "images_not_ocr_read" not in out.document.warnings


async def test_small_images_are_not_ocr_read_without_a_redaction(
    engine: PrivacyEngine, settings: Settings
) -> None:
    ocr = FakeOcr([])
    out = await pipe(engine, settings, ocr).run(
        mode="obfuscate",
        content=small_image_pdf(),
        filename=None,
        content_type=None,
        request=req(),
    )
    assert out.pages[0].source == "text_layer" and ocr.calls == 0


@pytest.mark.parametrize("ocr_mode,configured", [("auto", False), ("never", True)])
async def test_unread_images_in_a_redaction_are_a_warning(
    engine: PrivacyEngine, settings: Settings, ocr_mode: str, configured: bool
) -> None:
    ocr: OcrClient = FakeOcr([]) if configured else NullOcrClient()
    out = await pipe(engine, settings, ocr).run(
        mode="obfuscate",
        content=small_image_pdf(),
        filename=None,
        content_type=None,
        request=req(redacted_pdf=True, ocr=ocr_mode),
    )
    assert out.redacted_pdf is not None and "images_not_ocr_read" in out.document.warnings


async def test_csv_findings_are_numbered_in_row_order(
    engine: PrivacyEngine, settings: Settings
) -> None:
    rows = [{**CUSTOMERS[i % 2], "customer_id": f"CUST-88{i:04d}"} for i in range(12)]
    out = await pipe(engine, settings).run(
        mode="detect",
        content=csv_document(rows),
        filename="c.csv",
        content_type=None,
        request=req(),
    )
    lines = [f.line for f in sorted(out.pages[0].findings, key=lambda f: f.n)]
    assert lines == sorted(lines) and max(lines) == 13


# --- review B1 (M1-G3 r1): the redacted raster keeps what the page shows ---------------------

#: Words of the KYC fixture a redaction must hide (the card's expiry is kept by the policy).
_SENSITIVE = {"Ananya", "Krishnan", "123-45-6789", "ananya.k@example-mail.in", "4111", "1111"}


@pytest.mark.parametrize("hidden", [GARBLED_LAYER, SPECIMEN_LAYER], ids=["garbled", "specimen"])
async def test_a_searchable_scan_is_redacted_by_what_it_shows(
    engine: PrivacyEngine, settings: Settings, hidden: tuple[str, ...]
) -> None:
    """A page image under an invisible text layer that reads otherwise (OCR errors, or
    "Specimen"): the OCR words the text layer contradicts reach the engine, so every value the
    page shows is boxed, and the re-read of the produced PDF shows none of them."""
    ocr = ScreenOcr(words_px(text_pdf())[0])
    out = await pipe(engine, settings, ocr).run(
        mode="obfuscate",
        content=searchable_scan(hidden),
        filename=None,
        content_type=None,
        request=req(redacted_pdf=True),
    )
    assert ocr.calls == 2 and ocr.reads[0] and not set(ocr.reads[-1]) & _SENSITIVE
    assert "09/28" in ocr.reads[-1]  # kept by the policy: shown, and no leak
    assert out.pages[0].source == "hybrid"
    redacted = {f.entity_type for f in out.pages[0].findings if f.redacted}
    assert {"SSN", "CARD_PAN", "EMAIL", "PERSON_NAME"} <= redacted
    redaction = out.document.redaction
    assert redaction is not None and redaction.passed and redaction.ocr_checked
    assert redaction.ocr_hits == 0


@pytest.mark.parametrize("mode", ["obfuscate", "synthesize"])
async def test_values_the_text_layer_cannot_read_fail_the_reread(
    engine: PrivacyEngine, settings: Settings, mode: str
) -> None:
    """A /ToUnicode map that reads digits as X: the text layer holds no SSN or card number, the
    page (no image) is not OCR'd, but the produced raster shows both. The re-read runs the
    engine over what it reads and fails closed; the error names types, never values."""
    ocr = ScreenOcr(words_px(text_pdf())[0])
    with pytest.raises(DocumentError) as exc:
        await pipe(engine, settings, ocr).run(
            mode=mode,  # type: ignore[arg-type]
            content=broken_tounicode_pdf(),
            filename=None,
            content_type=None,
            request=req(redacted_pdf=True),
        )
    assert (exc.value.code, exc.value.status) == ("leakage", 422) and ocr.calls == 1
    assert "SSN" in exc.value.detail and "CARD_PAN" in exc.value.detail
    assert not any(value in exc.value.detail for value in _SENSITIVE | {"6789", "09/28"})


async def test_the_reread_passes_a_redaction_that_hides_every_value(
    engine: PrivacyEngine, settings: Settings
) -> None:
    """Control: the same page with a sound text layer redacts and passes the re-read."""
    ocr = ScreenOcr(words_px(text_pdf())[0])
    out = await pipe(engine, settings, ocr).run(
        mode="obfuscate",
        content=text_pdf(),
        filename=None,
        content_type=None,
        request=req(redacted_pdf=True),
    )
    redaction = out.document.redaction
    assert redaction is not None and redaction.passed and redaction.ocr_hits == 0
    assert ocr.calls == 1 and not set(ocr.reads[0]) & _SENSITIVE and "SSN" in ocr.reads[0]


@pytest.mark.parametrize("lines", [KYC_LINES, ("Customer: Ananya Krishnan", "Premier Banking")])
async def test_what_ocr_reads_inside_a_box_is_not_a_leak(
    engine: PrivacyEngine, settings: Settings, lines: tuple[str, ...]
) -> None:
    """A real OCR reads a black box as letters ("RR") or as nothing. Either way a cue must not
    reach past a redacted value: with the name boxed, "Customer:" would otherwise name the next
    capitalised words ("SSN RR", "Premier Banking") and fail a sound redaction. Each painted box
    is read as one mark, and what OCR saw inside it is dropped."""
    for reads_as in ("RR", None):
        ocr = ScreenOcr(words_px(text_pdf(lines))[0], box_reads_as=reads_as)
        out = await pipe(engine, settings, ocr).run(
            mode="obfuscate",
            content=text_pdf(lines),
            filename=None,
            content_type=None,
            request=req(redacted_pdf=True),
        )
        redaction = out.document.redaction
        assert redaction is not None and redaction.passed and ocr.calls == 1
        assert (redaction.chars_under_boxes > 0) == (reads_as is not None)


async def test_every_page_of_a_redacted_pdf_is_reread(
    engine: PrivacyEngine, settings: Settings
) -> None:
    """A page without boxes can still show a value the engine never saw: it is re-read too."""
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(612, 792), invariant=1)
    for lines in (KYC_LINES, ("Terms and conditions apply",)):
        c.setFont("Helvetica", 14)
        for i, line in enumerate(lines):
            c.drawString(72, 720 - i * 28, line)
        c.showPage()
    c.save()
    ocr = FakeOcr([[]])
    out = await pipe(engine, settings, ocr).run(
        mode="obfuscate",
        content=buf.getvalue(),
        filename=None,
        content_type=None,
        request=req(redacted_pdf=True),
    )
    assert out.pages[1].findings == []
    redaction = out.document.redaction
    assert redaction is not None and redaction.ocr_pages == 2 and ocr.calls == 2
    leak = [("SSN", (300.0, 300.0, 520.0, 330.0)), ("123-45-6789", (540.0, 300.0, 800.0, 330.0))]
    with pytest.raises(DocumentError) as exc:
        await pipe(engine, settings, FakeOcr([leak])).run(
            mode="obfuscate",
            content=buf.getvalue(),
            filename=None,
            content_type=None,
            request=req(redacted_pdf=True),
        )
    assert exc.value.code == "leakage"


async def test_a_redacted_value_another_page_shows_is_a_leak(
    engine: PrivacyEngine, settings: Settings
) -> None:
    """The name is detected and boxed on page 1; page 2 shows it too, where its text layer does
    not say it (here: an OCR double reading it at the same place on every page). No cue on page
    2 names it, so only the needle check sees it — and it checks every page against every
    redacted original, not just that page's own."""
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(612, 792), invariant=1)
    for lines in (KYC_LINES, ("Terms and conditions apply",)):
        c.setFont("Helvetica", 14)
        for i, line in enumerate(lines):
            c.drawString(72, 720 - i * 28, line)
        c.showPage()
    c.save()
    name = [w for w in words_px(text_pdf())[0] if w[0] in ("Ananya", "Krishnan")]
    ocr = ScreenOcr(name)
    with pytest.raises(DocumentError) as exc:
        await pipe(engine, settings, ocr).run(
            mode="obfuscate",
            content=buf.getvalue(),
            filename=None,
            content_type=None,
            request=req(redacted_pdf=True),
        )
    assert exc.value.code == "leakage" and "page(s) 2;" in exc.value.detail
    assert sorted(len(r) for r in ocr.reads) == [0, 2]  # page 1 boxed it, page 2 shows it
