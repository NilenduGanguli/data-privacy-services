from __future__ import annotations

import base64
import json

import pytest

pytestmark = [pytest.mark.docs, pytest.mark.ocr]
pytest.importorskip("pdfplumber")

from fastapi.testclient import TestClient  # noqa: E402

from dps.app import create_app  # noqa: E402
from dps.audit import MemoryAuditSink  # noqa: E402
from dps.config import Settings  # noqa: E402
from dps.layers.documents.redact import inspect_output_pdf  # noqa: E402
from tests.documents.fixtures import scanned_pdf  # noqa: E402


def test_scanned_kyc_pdf_is_redacted_end_to_end(ocr_url: str) -> None:
    settings = Settings(
        _env_file=None,
        env="local",
        auth_enabled=False,
        llm_provider="stub",
        ner_enabled=False,
        ocr_provider="azure",
        azure_vision_endpoint=ocr_url,
        azure_vision_key="mock-key",
        ocr_probe_interval_seconds=0,
    )
    with TestClient(create_app(settings, audit_sink=MemoryAuditSink())) as c:
        assert c.get("/readyz").json()["components"]["ocr"]["detail"] == "up"
        r = c.post(
            "/api/v1/documents/obfuscate",
            files={"file": ("kyc-scanned.pdf", scanned_pdf(), "application/pdf")},
            data={"request": json.dumps({"options": {"redacted_pdf": True}})},
        )
    body = r.json()
    assert r.status_code == 200, body
    assert {"SSN", "EMAIL"} <= {f["entity_type"] for f in body["pages"][0]["findings"]}
    redaction = body["document"]["redaction"]
    assert redaction["ocr_checked"] and redaction["ocr_hits"] == 0 and redaction["passed"]
    assert inspect_output_pdf(base64.b64decode(body["redacted_pdf_b64"])).clean
