"""M1 modules never log, print or raise with a value, a key or a response body (contract §0.1).

``test_m1_paths_log_no_values`` drives the M1 modules directly (the plan's scan: the document
pipeline with OCR, redaction, previews and a fail-closed leak, the OCR client's error path, schema
profiling, an LLM spec proposal and execution). ``test_m1_routes_log_and_audit_no_values`` drives
every M1 route through the app — documents, schema, entities and the recognizer test, NDJSON
streams, ``Idempotency-Key`` replays and the opt-in T3 labelling tier — and also scans the audit
events and every refusal's body for values and for the keys and seeds the responses returned.
``test_local_tesseract_failures_log_no_values`` makes a stand-in ``tesseract`` print values on
stdout and stderr.
"""

from __future__ import annotations

import json
import logging
import stat
from pathlib import Path
from typing import Any

import httpx
import pytest

pytestmark = pytest.mark.docs
pytest.importorskip("pdfplumber")

from fastapi.testclient import TestClient  # noqa: E402

from dps.app import create_app  # noqa: E402
from dps.audit import MemoryAuditSink  # noqa: E402
from dps.config import Settings  # noqa: E402
from dps.engine.detect.ensemble import Detector, DetectorConfig  # noqa: E402
from dps.engine.service import PrivacyEngine  # noqa: E402
from dps.engine.synth.schema_gen import execute_spec  # noqa: E402
from dps.engine.synth.schema_profile import profile_from_sample, propose_spec  # noqa: E402
from dps.layers.documents import DocumentPipeline  # noqa: E402
from dps.layers.documents.errors import DocumentError  # noqa: E402
from dps.layers.documents.models import DocumentRequest  # noqa: E402
from dps.layers.documents.ocr import AzureReadClient, OcrError, TesseractOcrClient  # noqa: E402
from dps.llm.gateway import LlmGateway, LlmResult  # noqa: E402
from dps.observability import configure_logging  # noqa: E402
from tests.documents.fixtures import (  # noqa: E402
    CUSTOMERS,
    FakeOcr,
    csv_document,
    png_document,
    scanned_pdf,
    text_pdf,
    words_px,
)

VALUES = (
    "Ananya Krishnan",
    "123-45-6789",
    "ananya.k@example-mail.in",
    "4111 1111 1111 1111",
    "rohan.d@example-mail.in",
    "Rohan Desai",
)
SECRETS = ("k-SECRET-KEY", "BODY-SECRET")
#: A leftover JSON value only the T3 tier labels (no pattern, no structural key name).
LEFTOVER = "ZQ-8841-55"
API_KEY = "dps_k-SECRET-KEY-routes"
KEYS = [{"name": "full", "key": API_KEY, "tenant_ids": ["*"], "scopes": ["*"]}]
H = {"X-API-KEY": API_KEY}
T3_POLICY = {
    "preset": "gdpr-pseudonymise",
    "detect": {"tiers": ["structural", "patterns", "ner", "llm_label"]},
}


class ConfiguredGateway(LlmGateway):
    """A 'real' provider whose reply is unusable — exercises the LLM path without a network."""

    def __init__(self) -> None:
        super().__init__(
            Settings(
                _env_file=None,
                llm_provider="stellar",
                stellar_base_url="https://stellar.test",
                coin_url="https://coin.test",
            )
        )

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.2,
        max_tokens: int = 512,
        purpose: str = "rewrite",
    ) -> LlmResult:
        return LlmResult(text="no json here", provider="stellar", model="test")


class LabellingGateway(ConfiguredGateway):
    """Labels ``$.loan.ref_cd`` when asked to (T3); any other purpose gets an unusable reply."""

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.2,
        max_tokens: int = 512,
        purpose: str = "rewrite",
    ) -> LlmResult:
        if purpose != "label":
            return LlmResult(text="no json here", provider="stellar", model="test")
        verdict = [{"ref": "$.loan.ref_cd", "entity_type": "CUSTOMER_ID", "confidence": 0.92}]
        return LlmResult(text=json.dumps(verdict), provider="stellar", model="test")


def _assert_absent(blob: str, needles: tuple[str, ...]) -> None:
    for needle in needles:
        assert needle not in blob, needle


async def test_m1_paths_log_no_values(
    capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    configure_logging("DEBUG", json_output=True)
    caplog.set_level(logging.DEBUG)
    settings = Settings(_env_file=None, llm_provider="stub", ner_enabled=False, fpe_enabled=False)
    engine = PrivacyEngine(settings, gateway=LlmGateway(settings))
    words = words_px(text_pdf())[0]
    ok = DocumentPipeline(engine, settings, FakeOcr([words, []]))
    await ok.run(
        mode="obfuscate",
        content=scanned_pdf(),
        filename="kyc.pdf",
        content_type=None,
        request=DocumentRequest.model_validate(
            {"options": {"redacted_pdf": True, "previews": True}}
        ),
    )
    await ok.run(
        mode="synthesize",
        content=csv_document(CUSTOMERS),
        filename="c.csv",
        content_type="text/csv",
        request=DocumentRequest(),
    )
    with pytest.raises(DocumentError) as leak:
        await DocumentPipeline(engine, settings, FakeOcr([words, words])).run(
            mode="obfuscate",
            content=scanned_pdf(),
            filename="kyc.pdf",
            content_type=None,
            request=DocumentRequest.model_validate({"options": {"redacted_pdf": True}}),
        )
    client = AzureReadClient(
        endpoint="http://ocr.test",
        key="k-SECRET-KEY",
        timeout_s=5,
        poll_interval_s=0.1,
        transport=httpx.MockTransport(
            lambda r: httpx.Response(500, text="BODY-SECRET 123-45-6789")
        ),
    )
    with pytest.raises(OcrError) as ocr_error:
        await client.read(b"\x89PNG")
    await client.aclose()
    result = profile_from_sample([dict(r) for r in CUSTOMERS], Detector(DetectorConfig()))
    spec, info = await propose_spec(result, ConfiguredGateway(), locale="en_US")
    execute_spec(spec, rows=10, seed=bytes(32))
    captured = capsys.readouterr()
    blob = "\n".join(
        (captured.out, captured.err, caplog.text, str(leak.value), str(ocr_error.value), str(info))
    )
    assert leak.value.code == "leakage" and ocr_error.value.code
    _assert_absent(blob, VALUES + SECRETS)


def _upload(
    content: bytes, name: str, ctype: str, request: dict[str, Any] | str | None = None
) -> dict[str, Any]:
    raw = request if isinstance(request, str) else json.dumps(request) if request else None
    return {
        "files": {"file": (name, content, ctype)},
        "data": {"request": raw} if raw is not None else {},
    }


def test_m1_routes_log_and_audit_no_values(
    capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    settings = Settings(
        _env_file=None,
        env="dev",  # JSON logs
        log_level="DEBUG",
        auth_enabled=True,
        api_keys=json.dumps(KEYS),
        llm_provider="stellar",
        stellar_base_url="https://stellar.test",
        coin_url="https://coin.test",
        ner_enabled=False,
        fpe_enabled=False,
        rate_limit_enabled=False,
        ocr_probe_interval_seconds=0,
    )
    sink = MemoryAuditSink()
    words = words_px(text_pdf())[0]
    ocr = FakeOcr([])
    engine = PrivacyEngine(settings, gateway=LabellingGateway())
    app = create_app(settings, engine=engine, audit_sink=sink, ocr_client=ocr)
    refusals: list[str] = []
    returned: list[str] = []  # keys and seeds the responses handed back: never logged

    def ok(response: httpx.Response) -> Any:
        assert response.status_code == 200, response.status_code
        body = response.json()
        if isinstance(body, dict):
            seed = body.get("synthesis_seed")
            seed = seed.get("seed") if isinstance(seed, dict) else seed
            dek = (body.get("key_material") or {}).get("dek")
            returned.extend(v for v in (dek, seed) if isinstance(v, str) and v)
        return body

    def refused(response: httpx.Response, code: str) -> None:
        assert response.status_code >= 400 and response.json()["code"] == code, response.text
        refusals.append(response.text)

    record = {
        "full_name": "Ananya Krishnan",
        "ssn": "123-45-6789",
        "email": "ananya.k@example-mail.in",
        "card": "4111 1111 1111 1111",
        "loan": {"ref_cd": LEFTOVER},
    }
    with TestClient(app) as c:
        # Documents: an OCR'd scan, redacted and re-read; a leak that fails closed; an image; CSV.
        ocr.pages, ocr.calls = [words, []], 0
        scan = _upload(
            scanned_pdf(),
            "kyc.pdf",
            "application/pdf",
            {"options": {"redacted_pdf": True, "previews": True}},
        )
        ok(c.post("/api/v1/documents/obfuscate", headers=H, **scan))
        ocr.pages, ocr.calls = [words, words], 0
        refused(c.post("/api/v1/documents/obfuscate", headers=H, **scan), "leakage")
        ocr.pages, ocr.calls = [words], 0
        ok(c.post("/api/v1/documents/detect", headers=H, **_upload(png_document(), "id.png", "")))
        csv = _upload(csv_document(CUSTOMERS), "c.csv", "text/csv", {"policy": "pci-dss"})
        ok(c.post("/api/v1/documents/synthesize", headers=H, **csv))
        bad = _upload(
            text_pdf(), "kyc.pdf", "application/pdf", '{"options": {"ocr": "Rohan Desai"}}'
        )
        refused(c.post("/api/v1/documents/detect", headers=H, **bad), "invalid_request")
        # Idempotency-Key: a replay, then the same key with another document.
        idem = {**H, "Idempotency-Key": "m1-log-scan-0001"}
        first = c.post(
            "/api/v1/documents/obfuscate", headers=idem, **_upload(text_pdf(), "a.pdf", "")
        )
        again = c.post(
            "/api/v1/documents/obfuscate", headers=idem, **_upload(text_pdf(), "a.pdf", "")
        )
        ok(first), ok(again)
        assert again.headers.get("Idempotent-Replay") == "true"
        other = _upload(csv_document(CUSTOMERS), "c.csv", "text/csv")
        refused(
            c.post("/api/v1/documents/obfuscate", headers=idem, **other), "idempotency_key_reused"
        )
        # T3 labelling of a leftover JSON field, and NDJSON streams (an array, a malformed body).
        t3 = ok(c.post("/api/v1/obfuscate", headers=H, json={"data": record, "policy": T3_POLICY}))
        assert any(f["detectors"] == ["llm_label"] for f in t3["findings"])
        stream = c.post(
            "/api/v1/obfuscate",
            headers={**H, "Accept": "application/x-ndjson"},
            json={"data": [record, {**record, "full_name": "Rohan Desai"}], "policy": T3_POLICY},
        )
        lines = [json.loads(line) for line in stream.text.splitlines()]
        assert stream.status_code == 200 and lines[-1]["type"] == "report", stream.text
        returned.append(lines[0]["key_material"]["dek"])
        broken = b'{"policy": "gdpr-pseudonymise"}\n{"ssn": "123-45-6789"\n'
        refused(
            c.post(
                "/api/v1/detect",
                headers={**H, "Content-Type": "application/x-ndjson"},
                content=broken,
            ),
            "ndjson_invalid",
        )
        # Schema: a spec from a sample (the LLM proposal falls back), synthesis with the sweep.
        sample = [dict(r) for r in CUSTOMERS]
        ok(c.post("/api/v1/schema/generator-spec", headers=H, json={"sample": sample}))
        ok(c.post("/api/v1/schema/synthesize", headers=H, json={"sample": sample, "rows": 20}))
        # Entities and the recognizer test over text holding values.
        ok(c.get("/api/v1/entities", headers=H))
        recognizer = {
            "name": "ssn-like",
            "supported_entity": "STAFF_REF",
            "patterns": [{"name": "p", "regex": r"\b\d{3}-\d{2}-\d{4}\b", "score": 0.9}],
        }
        text = "Ananya Krishnan, SSN 123-45-6789, ananya.k@example-mail.in"
        tested = ok(
            c.post(
                "/api/v1/policies/test-recognizer",
                headers=H,
                json={"recognizer": recognizer, "text": text},
            )
        )
        assert tested["count"] == 1
        ok(c.get("/api/v1/policies/presets", headers=H))
    captured = capsys.readouterr()
    audit = "\n".join(event.model_dump_json() for event in sink.events)
    assert len(sink.events) >= 13 and audit  # every engine call, refusals included
    blob = "\n".join((captured.out, captured.err, caplog.text, audit, *refusals))
    assert '"event"' in captured.out  # the app's JSON log lines were captured
    _assert_absent(blob, (*VALUES, LEFTOVER, API_KEY, *returned))


def _script(tmp_path: Path, body: str) -> str:
    path = tmp_path / "fake-tesseract"
    path.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


async def test_local_tesseract_failures_log_no_values(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    configure_logging("DEBUG", json_output=True)
    caplog.set_level(logging.DEBUG)
    said = " ".join(VALUES)
    errors: list[str] = []
    for body in (
        f'cat > /dev/null; echo "{said}" >&2; echo "{said}"; exit 1',  # ocr_failed
        f'cat > /dev/null; echo "{said}" >&2; echo "level\t{said}"',  # ocr_protocol
    ):
        client = TesseractOcrClient(
            cmd=_script(tmp_path, body), langs="eng", timeout_s=5, max_concurrency=1
        )
        with pytest.raises(OcrError) as exc:
            await client.read(b"\x89PNG fake")
        errors.append(f"{exc.value.code} {exc.value}")
    assert errors[0].startswith("ocr_failed") and errors[1].startswith("ocr_protocol")
    captured = capsys.readouterr()
    _assert_absent("\n".join((captured.out, captured.err, caplog.text, *errors)), VALUES)
