from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import Iterator
from typing import Any

import pytest

pytestmark = pytest.mark.docs
pytest.importorskip("pdfplumber")

from fastapi.testclient import TestClient  # noqa: E402

from dps.app import _ocr_probe_loop, create_app  # noqa: E402
from dps.audit import MemoryAuditSink  # noqa: E402
from dps.config import Settings  # noqa: E402
from dps.layers.documents.ocr import OcrClient  # noqa: E402
from dps.layers.documents.redact import inspect_output_pdf  # noqa: E402
from dps.observability import READINESS  # noqa: E402
from tests.documents.fixtures import (  # noqa: E402
    CUSTOMERS,
    FakeOcr,
    csv_document,
    encrypted_pdf,
    png_document,
    scanned_pdf,
    text_pdf,
    words_px,
)

KEYS = [
    {"name": "full", "key": "dps_full", "tenant_ids": ["*"], "scopes": ["*"]},
    {"name": "ro", "key": "dps_ro", "tenant_ids": ["retail"], "scopes": ["detect"]},
]
FULL, RO = {"X-API-KEY": "dps_full"}, {"X-API-KEY": "dps_ro"}


def make(ocr: OcrClient | None = None, **kw: Any) -> tuple[TestClient, MemoryAuditSink]:
    settings = Settings(
        _env_file=None,
        env="local",
        auth_enabled=True,
        api_keys=json.dumps(KEYS),
        llm_provider="stub",
        fpe_enabled=False,
        ner_enabled=False,
        rate_limit_enabled=False,
        ocr_probe_interval_seconds=kw.pop("ocr_probe_interval_seconds", 0),
        **kw,
    )
    sink = MemoryAuditSink()
    return TestClient(create_app(settings, audit_sink=sink, ocr_client=ocr)), sink


def upload(
    content: bytes,
    name: str = "kyc.pdf",
    ctype: str = "application/pdf",
    request: dict[str, Any] | None = None,
) -> dict[str, Any]:
    data = {"request": json.dumps(request)} if request is not None else {}
    return {"files": {"file": (name, content, ctype)}, "data": data}


def test_scopes_and_detect() -> None:
    client, _ = make()
    with client as c:
        assert (
            c.post("/api/v1/documents/obfuscate", headers=RO, **upload(text_pdf())).status_code
            == 403
        )
        r = c.post("/api/v1/documents/detect", headers=RO, **upload(text_pdf()))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["document"]["format"] == "pdf" and body["pages"][0]["findings"] and body["trace_id"]
    assert body["record_sensitivity"] == "CRITICAL" and body["pages"][0]["findings"][0]["boxes"]


def test_multipart_contract_errors() -> None:
    client, _ = make()
    with client as c:
        assert c.post("/api/v1/documents/detect", headers=FULL).status_code == 422
        bad = c.post(
            "/api/v1/documents/detect",
            headers=FULL,
            **upload(text_pdf(), request={"options": {"ocr": "maybe-SECRET"}}),
        )
        assert (
            bad.status_code == 422
            and bad.json()["code"] == "invalid_request"
            and "SECRET" not in bad.text
        )
        key = c.post(
            "/api/v1/documents/obfuscate",
            headers=FULL,
            **upload(
                text_pdf(), request={"options": {"consistency": {"key_material": "DEK-SECRET"}}}
            ),
        )
        assert key.status_code == 422 and "DEK-SECRET" not in key.text
        odd = c.post(
            "/api/v1/documents/detect",
            headers=FULL,
            **upload(b"\x00\x01binary", name="x.bin", ctype="application/octet-stream"),
        )
        assert odd.status_code == 415 and odd.json()["code"] == "unsupported_format"
        enc = c.post("/api/v1/documents/detect", headers=FULL, **upload(encrypted_pdf()))
        assert enc.status_code == 422 and enc.json()["code"] == "encrypted_pdf"


def test_upload_limit() -> None:
    client, _ = make(max_document_mb=1)
    with client as c:
        r = c.post(
            "/api/v1/documents/detect", headers=FULL, **upload(b"%PDF-1.7\n" + b"0" * (3 << 20))
        )
    assert r.status_code == 413 and r.json()["code"] == "payload_too_large"


def test_obfuscate_with_redacted_pdf_over_http() -> None:
    client, sink = make()
    with client as c:
        r = c.post(
            "/api/v1/documents/obfuscate",
            headers=FULL,
            **upload(
                text_pdf(),
                request={
                    "policy": "gdpr-pseudonymise",
                    "options": {"redacted_pdf": True, "previews": True},
                },
            ),
        )
    assert r.status_code == 200, r.text
    body = r.json()
    pdf = base64.b64decode(body["redacted_pdf_b64"])
    assert inspect_output_pdf(pdf).clean and b"123-45-6789" not in pdf
    assert body["document"]["redaction"]["passed"] is True and body["pages"][0]["preview_png_b64"]
    assert body["report"]["verification"]["passed"] is True and body["key_material"]["dek"]
    assert "123-45-6789" not in body["result"]
    event = sink.events[-1]
    assert (event.mode, event.policy, event.outcome) == (
        "document.obfuscate",
        "gdpr-pseudonymise",
        "ok",
    )
    assert "123-45-6789" not in json.dumps(event.model_dump(mode="json"))


def test_reveal_document_text_over_http() -> None:
    client, _ = make()
    with client as c:
        body = c.post("/api/v1/documents/obfuscate", headers=FULL, **upload(text_pdf())).json()
        back = c.post(
            "/api/v1/reveal",
            headers=FULL,
            json={
                "text": body["result"],
                "key_material": body["key_material"],
                "reveal_hints": body["reveal_hints"],
            },
        )
    assert back.status_code == 200 and "123-45-6789" in back.json()["result"]


def test_csv_synthesize_over_http() -> None:
    client, _ = make()
    with client as c:
        r = c.post(
            "/api/v1/documents/synthesize",
            headers=FULL,
            **upload(
                csv_document(CUSTOMERS),
                name="c.csv",
                ctype="text/csv",
                request={"policy": "synthetic-realistic"},
            ),
        )
    body = r.json()
    assert r.status_code == 200, r.text
    assert body["result_csv"].splitlines()[0] == ",".join(CUSTOMERS[0]) and body["synthesis_seed"]
    assert "ananya.k@example-mail.in" not in body["result_csv"]


def test_scanned_pdf_with_fake_ocr_over_http() -> None:
    client, _ = make(ocr=FakeOcr([words_px(text_pdf())[0], []]))
    with client as c:
        r = c.post(
            "/api/v1/documents/obfuscate",
            headers=FULL,
            **upload(scanned_pdf(), request={"options": {"redacted_pdf": True}}),
        )
    body = r.json()
    assert r.status_code == 200, r.text
    assert (
        body["document"]["ocr_pages"] == [1]
        and body["document"]["redaction"]["ocr_checked"] is True
    )


def test_readyz_reports_ocr_without_requiring_it() -> None:
    client, _ = make(ocr=FakeOcr([]))
    with client as c:
        component = c.get("/readyz").json()["components"]["ocr"]
    assert component["ok"] is True and component["detail"] == "up"
    client, _ = make()
    with client as c:
        ready = c.get("/readyz")
    assert (
        ready.status_code == 200 and ready.json()["components"]["ocr"]["detail"] == "unconfigured"
    )


def test_images_without_ocr_are_503() -> None:
    client, _ = make()
    with client as c:
        r = c.post(
            "/api/v1/documents/detect",
            headers=FULL,
            **upload(png_document(), name="id.png", ctype="image/png"),
        )
    assert r.status_code == 503 and r.json()["code"] == "ocr_unconfigured"


# --- beyond the plan ------------------------------------------------------------------------


def _chunks(body: bytes, size: int = 64 * 1024) -> Iterator[bytes]:
    for i in range(0, len(body), size):
        yield body[i : i + size]


def test_body_limits_are_per_route() -> None:
    """JSON routes keep ``max_payload_mb``; documents get ``max_document_mb`` + 1 MiB of
    multipart overhead, by Content-Length and while a chunked upload streams."""
    client, _ = make(max_payload_mb=1, max_document_mb=2, max_text_chars=5_000_000)
    big_json = json.dumps({"text": "a" * (1536 * 1024)}).encode()
    with client as c:
        json_route = c.post(
            "/api/v1/detect",
            content=big_json,
            headers={**FULL, "content-type": "application/json"},
        )
        assert json_route.status_code == 413 and json_route.json()["code"] == "payload_too_large"
        # 1.5 MB is over the JSON limit but within the document limit: it reaches the pipeline
        within = c.post(
            "/api/v1/documents/detect",
            headers=FULL,
            **upload(b"%PDF-1.7\n" + b"0" * (1536 * 1024)),
        )
        assert within.status_code == 422 and within.json()["code"] == "corrupt_document"
        boundary = "dpsboundary"
        head = (
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="a.pdf"\r\n'
            "Content-Type: application/pdf\r\n\r\n%PDF-1.7\n"
        ).encode()
        body = head + b"0" * (4 << 20) + f"\r\n--{boundary}--\r\n".encode()
        streamed = c.post(
            "/api/v1/documents/detect",
            content=_chunks(body),
            headers={**FULL, "content-type": f"multipart/form-data; boundary={boundary}"},
        )
    assert streamed.status_code == 413 and streamed.json()["code"] == "payload_too_large"


def test_rejections_are_audited_value_free() -> None:
    client, sink = make()
    with client as c:
        r = c.post(
            "/api/v1/documents/detect",
            headers=FULL,
            **upload(text_pdf(), request={"policy": "Ananya Krishnan", "options": {"ocr": 7}}),
        )
        assert r.status_code == 422 and r.json()["code"] == "invalid_request"
        rejected = sink.events[-1]
        assert (rejected.mode, rejected.policy, rejected.outcome) == (
            "document.detect",
            "-",
            "rejected",
        )
        enc = c.post("/api/v1/documents/detect", headers=FULL, **upload(encrypted_pdf()))
        assert enc.status_code == 422
        assert (sink.events[-1].mode, sink.events[-1].outcome) == (
            "document.detect",
            "encrypted_pdf",
        )
    assert "Ananya" not in json.dumps([e.model_dump(mode="json") for e in sink.events])


class _ClosingOcr(FakeOcr):
    def __init__(self) -> None:
        super().__init__([])
        self.closed = False
        self.probes = 0

    async def probe(self) -> tuple[bool, str]:
        self.probes += 1
        return True, "up"

    async def aclose(self) -> None:
        self.closed = True


def test_lifespan_probes_ocr_and_closes_the_client() -> None:
    ocr = _ClosingOcr()
    client, _ = make(ocr=ocr, ocr_probe_interval_seconds=3600)
    with client as c:
        assert c.get("/readyz").json()["components"]["ocr"]["engine"] == "fake-ocr"
        assert ocr.probes == 1 and not ocr.closed
    assert ocr.closed


class _FlakyProbe(FakeOcr):
    """Its first probe raises; later probes answer down, then up."""

    def __init__(self) -> None:
        super().__init__([])
        self.answers: list[tuple[bool, str]] = [(False, "down"), (True, "up")]
        self.probes = 0

    async def probe(self) -> tuple[bool, str]:
        self.probes += 1
        if self.probes == 1:
            raise RuntimeError("probe exploded")
        return self.answers[min(self.probes - 2, len(self.answers) - 1)]


async def test_a_failed_probe_never_stops_the_loop() -> None:
    ocr = _FlakyProbe()
    settings = Settings(_env_file=None, ocr_probe_interval_seconds=0.01)
    task = asyncio.create_task(_ocr_probe_loop(ocr, settings))
    try:
        for _ in range(500):
            if ocr.probes >= 3:
                break
            await asyncio.sleep(0.01)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert ocr.probes >= 3
    assert READINESS.snapshot()["components"]["ocr"]["detail"] == "up"


def test_a_probe_that_raises_at_startup_does_not_stop_the_app() -> None:
    client, _ = make(ocr=_FlakyProbe())
    with client as c:
        component = c.get("/readyz").json()["components"]["ocr"]
        assert c.get("/health").status_code == 200
    assert component["ok"] is False and component["detail"] == "error"
