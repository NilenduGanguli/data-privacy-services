"""Local Tesseract OCR (M1 Task 19C, spec §6 "local Tesseract when unset").

The offline tests need no binary. The ``tesseract``-marked tests run where a ``tesseract`` binary
is on ``PATH`` (the app image has one; the Mac may not): they skip otherwise, and fail instead when
``DPS_TEST_REQUIRE_SERVICES=1`` and ``DPS_TEST_REQUIRE_TESSERACT=1``.
"""

from __future__ import annotations

import os
import shutil
import stat
from pathlib import Path

import pytest

from dps.config import Settings
from dps.layers.documents.ocr import (
    OcrError,
    TesseractOcrClient,
    build_ocr_client,
    parse_tsv,
    refresh_ocr_readiness,
)
from dps.observability import READINESS

HEADER = (
    "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"
)
TSV = "\n".join(
    [
        HEADER,
        "1\t1\t0\t0\t0\t0\t0\t0\t1700\t2200\t-1\t",
        "2\t1\t1\t0\t0\t0\t100\t100\t900\t120\t-1\t",
        "3\t1\t1\t1\t0\t0\t100\t100\t900\t120\t-1\t",
        "4\t1\t1\t1\t1\t0\t100\t100\t900\t40\t-1\t",
        "5\t1\t1\t1\t1\t1\t100\t100\t200\t40\t96.5\tCustomer:",
        "5\t1\t1\t1\t1\t2\t320\t100\t180\t40\t91\tAnanya",
        "5\t1\t1\t1\t1\t3\t520\t100\t240\t40\t-1\tKrishnan",
        "5\t1\t1\t1\t1\t4\t780\t100\t10\t40\t95\t ",
        "4\t1\t1\t1\t2\t0\t100\t180\t500\t40\t-1\t",
        "5\t1\t1\t1\t2\t1\t100\t180\t100\t40\t88.25\tSSN",
        "5\t1\t1\t1\t2\t2\t220\t180\t300\t40\t90\t123-45-6789",
        "",
    ]
)
SENTINEL = "SENTINEL-Ananya-Krishnan-123-45-6789"


def test_parse_tsv_builds_words_lines_and_page_size() -> None:
    page = parse_tsv(TSV)
    assert (page.width, page.height, page.unit, page.angle) == (1700, 2200, "pixel", 0)
    assert [w.text for w in page.words] == ["Customer:", "Ananya", "Krishnan", "SSN", "123-45-6789"]
    assert [w.line for w in page.words] == [0, 0, 0, 1, 1]
    assert page.words[0].polygon == (100, 100, 300, 100, 300, 140, 100, 140)
    assert page.words[0].confidence == pytest.approx(0.965)
    assert page.words[2].confidence is None and page.words[3].confidence == pytest.approx(0.8825)


def test_parse_tsv_fails_closed_on_an_unknown_shape() -> None:
    for broken in ("", "not\ta\ttsv", HEADER + "\n5\t1\t1\t1\t1\t1\tx\t100\t200\t40\t96\tWord"):
        with pytest.raises(OcrError) as exc:
            parse_tsv(broken)
        assert exc.value.code == "ocr_protocol" and "Word" not in str(exc.value)


async def test_missing_binary_is_unconfigured() -> None:
    client = TesseractOcrClient(
        cmd="/nonexistent/tesseract", langs="eng", timeout_s=5, max_concurrency=1
    )
    assert client.configured() is False and client.engine.startswith("tesseract")
    with pytest.raises(OcrError) as exc:
        await client.read(b"\x89PNG")
    assert exc.value.code == "ocr_unconfigured"
    assert await client.probe() == (False, "tesseract_missing")
    settings = Settings(_env_file=None, ocr_provider="tesseract", tesseract_cmd="/nonexistent/x")
    await refresh_ocr_readiness(client, settings)
    component = READINESS.snapshot()["components"]["ocr"]
    assert component["ok"] is False and component["detail"] == "tesseract_missing"
    assert isinstance(build_ocr_client(settings), TesseractOcrClient)


def _script(tmp_path: Path, body: str) -> str:
    path = tmp_path / "fake-tesseract"
    path.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


async def test_stderr_never_leaks(tmp_path: Path) -> None:
    cmd = _script(tmp_path, f'cat > /dev/null; echo "{SENTINEL}" >&2; echo "{SENTINEL}"; exit 1')
    client = TesseractOcrClient(cmd=cmd, langs="eng", timeout_s=5, max_concurrency=1)
    assert client.configured() is True
    with pytest.raises(OcrError) as exc:
        await client.read(b"\x89PNG fake")
    assert exc.value.code == "ocr_failed"
    assert SENTINEL not in str(exc.value) and SENTINEL not in exc.value.detail


async def test_a_slow_tesseract_is_killed_at_the_timeout(tmp_path: Path) -> None:
    cmd = _script(tmp_path, "sleep 5")  # a child of the script: the whole group is killed
    client = TesseractOcrClient(cmd=cmd, langs="eng", timeout_s=0.3, max_concurrency=1)
    with pytest.raises(OcrError) as exc:
        await client.read(b"\x89PNG fake")
    assert exc.value.code == "ocr_timeout"


async def test_the_cli_is_called_with_stdin_stdout_and_tsv(tmp_path: Path) -> None:
    args = tmp_path / "args.txt"
    tsv = tmp_path / "out.tsv"
    tsv.write_text(TSV, encoding="utf-8")
    cmd = _script(
        tmp_path,
        f'if [ "$1" = "--version" ]; then echo "tesseract 5.3.0"; exit 0; fi\n'
        f'echo "$@" > "{args}"; cat > /dev/null; cat "{tsv}"',
    )
    client = TesseractOcrClient(cmd=cmd, langs="eng+spa", timeout_s=5, max_concurrency=2)
    assert await client.probe() == (True, "up") and client.engine == "tesseract-5.3"
    page = await client.read(b"\x89PNG fake")
    assert len(page.words) == 5
    assert args.read_text().split() == ["stdin", "stdout", "-l", "eng+spa", "--psm", "3", "tsv"]


def _require_tesseract() -> str:
    found = shutil.which("tesseract")
    if found is None:
        required = os.environ.get("DPS_TEST_REQUIRE_SERVICES") == "1" and (
            os.environ.get("DPS_TEST_REQUIRE_TESSERACT") == "1"
        )
        if required:
            pytest.fail("tesseract is required (DPS_TEST_REQUIRE_TESSERACT=1) but not on PATH")
        pytest.skip("needs a tesseract binary on PATH (the app image has one)")
    return found


#: A synthetic scanned KYC page with more than 20 words (fixture values only).
SCAN_LINES = (
    "Customer: Ananya Krishnan",
    "SSN 123-45-6789 on file",
    "Email ananya.k@example-mail.in",
    "Card 4111 1111 1111 1111 expires 09/28",
    "Branch review completed for the savings account",
    "Nominee details were verified by the branch officer",
)


@pytest.mark.tesseract
@pytest.mark.docs
async def test_reads_the_demo_scan() -> None:
    pytest.importorskip("pdfplumber")
    from dps.layers.documents.render import PdfRenderer, encode_png
    from tests.documents.fixtures import scanned_pdf

    cmd = _require_tesseract()
    client = TesseractOcrClient(cmd=cmd, langs="eng", timeout_s=60, max_concurrency=1)
    assert (await client.probe())[0] and client.engine.startswith("tesseract-")
    with PdfRenderer(scanned_pdf(SCAN_LINES)) as renderer:
        image = encode_png(renderer.render(0, 200))
    page = await client.read(image)
    texts = [w.text for w in page.words]
    assert len(texts) >= 20 and "Ananya" in texts and "Krishnan" in texts
    assert page.unit == "pixel" and page.width > 1000


@pytest.mark.tesseract
@pytest.mark.docs
async def test_pipeline_redacts_with_tesseract() -> None:
    pytest.importorskip("pdfplumber")
    from dps.engine.service import PrivacyEngine
    from dps.layers.documents import DocumentPipeline
    from dps.layers.documents.models import DocumentRequest
    from dps.layers.documents.redact import inspect_output_pdf
    from dps.llm.gateway import LlmGateway
    from tests.documents.fixtures import scanned_pdf

    cmd = _require_tesseract()
    settings = Settings(
        _env_file=None,
        llm_provider="stub",
        ner_enabled=False,
        fpe_enabled=False,
        ocr_provider="tesseract",
        tesseract_cmd=cmd,
    )
    engine = PrivacyEngine(settings, gateway=LlmGateway(settings))
    ocr = build_ocr_client(settings)
    assert isinstance(ocr, TesseractOcrClient)
    out = await DocumentPipeline(engine, settings, ocr).run(
        mode="obfuscate",
        content=scanned_pdf(SCAN_LINES),
        filename="kyc.pdf",
        content_type="application/pdf",
        request=DocumentRequest.model_validate({"options": {"redacted_pdf": True}}),
    )
    assert out.document.ocr_pages == [1] and out.document.ocr_engine.startswith("tesseract")
    found = {f.entity_type for f in out.pages[0].findings}
    assert {"SSN", "EMAIL", "CARD_PAN"} <= found
    redaction = out.document.redaction
    assert redaction is not None and redaction.passed and redaction.ocr_checked
    assert redaction.ocr_hits == 0 and out.redacted_pdf is not None
    assert inspect_output_pdf(out.redacted_pdf).clean
    assert out.transform is not None and "123-45-6789" not in out.transform.result
