"""The synthetic demo documents (``tools/make_demo_docs.py``, committed under ``demo/documents``).

They must be reproducible (re-running the tool changes nothing), committed as generated,
synthetic only (rule-valid surrogates, RFC 2606 e-mail domains, a card on the test IIN) and
useful: the engine finds what the demo is meant to show in every format, and the scanned twin
has no text layer, so the console's "Load demo KYC scan" exercises OCR.
"""

from __future__ import annotations

import base64
import csv
import importlib.util
import io
import json
import zipfile
from functools import cache
from pathlib import Path
from types import ModuleType

import pytest

pytestmark = pytest.mark.docs
pytest.importorskip("pdfplumber")

from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image  # noqa: E402
from pypdf import PdfReader  # noqa: E402
from stdnum import luhn  # noqa: E402
from stdnum.in_ import aadhaar, pan  # noqa: E402

from dps.app import create_app  # noqa: E402
from dps.audit import MemoryAuditSink  # noqa: E402
from dps.config import Settings  # noqa: E402
from dps.engine.service import PrivacyEngine  # noqa: E402
from dps.engine.synth.surrogates import RFC2606_DOMAINS, TEST_CARD_IIN  # noqa: E402
from dps.layers.documents import DocumentOutcome, DocumentPipeline  # noqa: E402
from dps.layers.documents.models import DocumentRequest  # noqa: E402
from dps.layers.documents.ocr import NullOcrClient  # noqa: E402
from dps.layers.documents.redact import inspect_output_pdf  # noqa: E402
from dps.llm.gateway import LlmGateway  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "demo" / "documents"
PUBLIC_SCAN = ROOT / "frontend" / "public" / "demo" / "kyc-scanned.pdf"
FILES = ("kyc-text.pdf", "kyc-scanned.pdf", "id-card.png", "kyc.docx", "kyc.txt", "customers.csv")
#: Built from text only: byte-identical on every platform for the pinned library versions.
TEXT_BUILT = ("kyc-text.pdf", "kyc.docx", "kyc.txt", "customers.csv")
#: Rasters (PDFium rendering, JPEG/PNG encoding) are reproducible on one platform; another
#: platform's codec build may differ by a few bytes, so they are checked by structure.
RASTERS = ("kyc-scanned.pdf", "id-card.png")
#: What the demo shows on every format: checksum-valid identifiers and contact details.
SHOWN = {"AADHAAR", "PAN_IN", "CARD_PAN", "EMAIL", "PHONE"}
A4 = (595.2756, 841.8898)


@cache
def tool() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "make_demo_docs", ROOT / "tools" / "make_demo_docs.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@cache
def generated() -> dict[str, bytes]:
    return dict(tool().build())


def test_the_tool_builds_the_six_documents_reproducibly() -> None:
    first = generated()
    assert sorted(first) == sorted(FILES)
    assert dict(tool().build()) == first  # a second run in the same process: identical bytes


@pytest.mark.parametrize("name", TEXT_BUILT)
def test_committed_documents_are_what_the_tool_writes(name: str) -> None:
    assert (DEMO / name).read_bytes() == generated()[name], (
        f"demo/documents/{name} is stale: run python tools/make_demo_docs.py"
    )


def test_committed_rasters_have_the_generated_structure() -> None:
    scan = PdfReader(io.BytesIO((DEMO / "kyc-scanned.pdf").read_bytes()))
    assert len(scan.pages) == 2
    for page in scan.pages:
        assert (float(page.mediabox.width), float(page.mediabox.height)) == pytest.approx(A4)
    image = Image.open(DEMO / "id-card.png")
    fresh = Image.open(io.BytesIO(generated()["id-card.png"]))
    assert image.size == fresh.size == (1654, 2339)  # A4 at 200 dpi
    assert PUBLIC_SCAN.read_bytes() == (DEMO / "kyc-scanned.pdf").read_bytes()


@pytest.mark.parametrize(
    "data",
    [lambda: (DEMO / "kyc-scanned.pdf").read_bytes(), lambda: generated()["kyc-scanned.pdf"]],
)
def test_the_scanned_twin_has_no_text_layer(data) -> None:  # noqa: ANN001
    inspection = inspect_output_pdf(data())
    assert inspection.text_layer_chars == 0 and inspection.text_operators == 0
    assert inspection.annotations == 0


def test_values_are_synthetic_and_rule_valid() -> None:
    v = tool()._values()
    assert aadhaar.is_valid(v["aadhaar"])
    assert pan.is_valid(v["pan"])
    card = v["card"].replace(" ", "")
    assert luhn.is_valid(card) and card.startswith(TEST_CARD_IIN)
    assert v["email"].rsplit("@", 1)[1] in RFC2606_DOMAINS
    assert v["name"] != v["payee"]
    rows = list(csv.DictReader(io.StringIO(generated()["customers.csv"].decode())))
    for row in rows:
        assert row["email"].rsplit("@", 1)[1] in RFC2606_DOMAINS
        number = row["card_number"].replace(" ", "")
        assert luhn.is_valid(number) and number.startswith(TEST_CARD_IIN)


def test_customers_csv_has_twenty_rows_and_the_documented_columns() -> None:
    rows = list(csv.reader(io.StringIO(generated()["customers.csv"].decode())))
    assert rows[0] == [
        "customer_id",
        "full_name",
        "email",
        "phone",
        "card_number",
        "status",
        "risk_score",
    ]
    assert len(rows) == 21 and all(len(r) == 7 for r in rows)
    assert len({r[1] for r in rows[1:]}) == 20  # one persona per row


def test_the_docx_is_repacked_with_fixed_timestamps() -> None:
    with zipfile.ZipFile(io.BytesIO(generated()["kyc.docx"])) as z:
        assert {i.date_time for i in z.infolist()} == {(2026, 1, 1, 0, 0, 0)}
        assert "word/document.xml" in z.namelist()


def _settings() -> Settings:
    return Settings(_env_file=None, llm_provider="stub", ner_enabled=False, fpe_enabled=False)  # type: ignore[call-arg]


async def _detect(name: str, data: bytes) -> DocumentOutcome:
    settings = _settings()
    pipeline = DocumentPipeline(
        PrivacyEngine(settings, gateway=LlmGateway(settings)), settings, NullOcrClient()
    )
    return await pipeline.run(
        mode="detect",
        content=data,
        filename=name,
        content_type=None,
        request=DocumentRequest.model_validate({"options": {"ocr": "never"}}),
    )


@pytest.mark.parametrize("name", ["kyc-text.pdf", "kyc.docx", "kyc.txt", "customers.csv"])
async def test_the_engine_finds_what_the_demo_shows(name: str) -> None:
    out = await _detect(name, generated()[name])
    found = {f.entity_type for page in out.pages for f in page.findings}
    expected = {
        "customers.csv": SHOWN - {"AADHAAR", "PAN_IN"},  # customer rows: contact and card only
        "kyc.txt": SHOWN - {"PHONE"},  # the note names no phone
    }.get(name, SHOWN)
    assert expected <= found, sorted(expected - found)


async def test_without_ocr_the_scanned_twin_reads_as_unreadable_pages() -> None:
    out = await _detect("kyc-scanned.pdf", generated()["kyc-scanned.pdf"])
    assert out.document.unreadable_pages == [1, 2]


def _by_page(pages: list[dict[str, object]]) -> list[set[str]]:
    return [{str(f["entity_type"]) for f in p["findings"]} for p in pages]  # type: ignore[attr-defined]


@pytest.mark.ocr
def test_the_ocr_mock_reads_the_scanned_demo_like_its_text_twin(ocr_url: str) -> None:
    """The console's "Load demo KYC scan": OCR finds what the text layer holds, page for page,
    and the redaction it produces passes its OCR re-read."""
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        env="local",
        auth_enabled=False,
        llm_provider="stub",
        ner_enabled=False,
        fpe_enabled=False,
        ocr_provider="azure",
        azure_vision_endpoint=ocr_url,
        azure_vision_key="mock-key",
        ocr_probe_interval_seconds=0,
    )
    files = generated()
    with TestClient(create_app(settings, audit_sink=MemoryAuditSink())) as c:
        text = c.post(
            "/api/v1/documents/detect",
            files={"file": ("kyc-text.pdf", files["kyc-text.pdf"], "application/pdf")},
        )
        scan = c.post(
            "/api/v1/documents/obfuscate",
            files={"file": ("kyc-scanned.pdf", PUBLIC_SCAN.read_bytes(), "application/pdf")},
            data={"request": json.dumps({"options": {"redacted_pdf": True}})},
        )
        image = c.post(
            "/api/v1/documents/detect",
            files={"file": ("id-card.png", files["id-card.png"], "image/png")},
        )
    assert text.status_code == scan.status_code == image.status_code == 200
    text_pages, scan_body = _by_page(text.json()["pages"]), scan.json()
    assert scan_body["document"]["ocr_pages"] == [1, 2]
    assert _by_page(scan_body["pages"]) == text_pages
    assert _by_page(image.json()["pages"]) == text_pages[:1]
    redaction = scan_body["document"]["redaction"]
    assert redaction["passed"] and redaction["ocr_checked"] and redaction["ocr_hits"] == 0
    assert redaction["boxes"] >= len(text_pages[0] | text_pages[1])
    assert inspect_output_pdf(base64.b64decode(scan_body["redacted_pdf_b64"])).clean
