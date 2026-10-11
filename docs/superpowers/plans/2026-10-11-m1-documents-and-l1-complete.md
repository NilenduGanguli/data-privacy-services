# DPS M1 — Documents and Layer 1 complete — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish Layer 1 of Data Privacy Services: documents in (PDF text layer, scanned PDF pages and images through the Azure Computer Vision Read v3.2 contract, DOCX, TXT, CSV), per-page findings with bounding boxes out, obfuscated/synthetic text that reveals with the caller's key, a **true** redacted PDF (rasterised pages with burned-in boxes, rebuilt image-only so no text survives, verified by re-extraction and an OCR re-read), synthetic rows from a schema / DDL / de-identified sample through a reusable generator spec, a measured detector evaluation harness with a committed report, the console Playground **Document** tab (mockup 08) and **Policies** page (mockup 05), OCR readiness, and the API reference — all offline in compose. The revision of 2026-10-11 adds the spec §5.3/§6 items no milestone owned: NDJSON streaming of large arrays (Task 19A), `Idempotency-Key` on the Layer 1 routes (19B), local Tesseract OCR when no Azure endpoint is configured (19C) and the opt-in T3 LLM metadata labelling tier (19D); contract §1, §9, §10, §12.1 and §15 already carry them.

**Architecture:** A new `dps/layers/documents` package turns an upload into an `ExtractedDocument` (pages of positioned words), runs OCR for pages that need it through an async `OcrClient` (Read v3.2 wire contract: the `azure-ocr-mock` container locally, the org gateway in prod), hands **one canonical text** (pages joined by `\n\f\n`; CSV as records) to the unchanged M0 `PrivacyEngine`, then maps the engine's findings and output segments back to pages, lines and boxes. Redaction renders each normalised page with pypdfium2 (serialised behind one process-wide lock, because PDFium is not thread-safe), paints solid boxes, and rebuilds an image-only PDF with reportlab; pypdf proves there is no text layer and the OCR client re-reads the output. Schema synthesis is a pure engine module (`dps/engine/synth/schema_gen.py`) that executes a validated generator spec deterministically from a 256-bit seed; the LLM may only *propose* a spec from a value-free structure profile. The evaluation harness (`dps/evaluation`) generates a labelled corpus from the surrogate engine and scores the detector ensemble per entity type. Errors gain stable codes (`{"detail","code"}`) through a framework-free `CodedError`.

**Tech Stack:** Python 3.12, FastAPI, pydantic v2, httpx (async), pypdf ≥ 5, pdfplumber ≥ 0.11.4, pypdfium2 5.x, reportlab ≥ 4.2, python-docx ≥ 1.1, Pillow ≥ 10.4 (the `docs` extra); Faker + python-stdnum (already core); Playwright 1.55.0 (Python, new `uitest` extra) for the console check; React 18 + TypeScript + Vite (no new npm dependency); Docker compose with the cached `document_intelligence-azure-ocr-mock:latest` image.

**Inputs:** spec `docs/specs/2026-10-11-data-privacy-services-design.md` (§5.3 evaluation harness, §5.6 synthesize from schema, §6 documents, §9 console, §10, §11 M1) · **binding contract** `docs/superpowers/plans/2026-10-11-m1-m5-contract.md` (§1 M1 rows, §9 M1 routes, §10 M1 settings, §11 `azure-ocr-mock`, §12 markers/gates, §14 mockups 05/08, §15) · M0 plan `docs/superpowers/plans/2026-10-11-m0-foundation-engine-api-playground.md` (authoritative M0 interfaces; read `docs/superpowers/build/M0-DEVIATIONS.md` for what changed while building) · mockups `docs/design/mockups/08-documents.html`, `05-policies.html`.

**Verified on this Mac (2026-10-11), so the code below uses real APIs:** pypdf 6.20 (`PageObject.transfer_rotation_to_content`, `add_transformation`, `PdfWriter.encrypt`, `pypdf.annotations.FreeText`), pdfplumber 0.11.10 (`Page.dedupe_chars`, `extract_words(return_chars=True)`, page coordinates top-left in points), pypdfium2 5.14 (`PdfDocument(bytes)`, `PdfPage.render(scale=…, may_draw_forms=False, draw_annots=False)`, `PdfPage.get_objects(filter=[raw.FPDF_PAGEOBJ_IMAGE])`, `PdfObject.get_bounds()` → `(left, bottom, right, top)`), reportlab 5.0 (`ImageReader(BytesIO(jpeg))` passes JPEG through as `DCTDecode`; every page stream starts with an empty `BT /F1 12 Tf 14.4 TL ET`, so "no text" is checked by text-*showing* operators, not by `BT`), python-docx 1.2 (`document.part.package.iter_parts()`), Faker providers `ssn/itin/ein` (en_US), `curp/rfc` (es_MX), `aadhaar_id` (en_IN) produce stdnum-valid values. Probes: after normalising a PDF (crop box moved to the origin, `/Rotate` transferred into the content) pdfplumber word boxes × `dpi/72` land exactly on the pypdfium2 raster (dark-pixel check), for cropped and rotated pages.

---

## M0 touch points (contract §0.7 — each changed together with its tests)

| M0 module | Change | Task |
|---|---|---|
| `pyproject.toml` | `docs` and `uitest` extras; markers `docs`, `ocr` | 1 |
| `dps/config.py`, `dps/posture.py`, `.env.example` | M1 settings (contract §10 + additive), OCR posture | 1 |
| `tests/conftest.py` | `ocr_url` fixture over `tests/services.py` (contract §12.2 table, all services) | 1 |
| `dps/layers/api/common.py`, `dps/layers/api/routers/{detect,transform,policies}.py` | `CodedHTTPException` + handler, engine errors carry codes, `policy_label()` (audit never records an inline policy body) | 2 |
| `dps/engine/transform/apply.py`, `dps/engine/synth/llm_text.py`, `dps/engine/service.py` | `OutputSegment`; `RequestOptions.return_segments`; `TransformResponse.segments` (additive, `null` unless asked) | 3 |
| `dps/observability.py` | log allow-list extended; document/OCR metrics; noisy third-party loggers capped at WARNING | 4, 7 |
| `dps/engine/detect/custom.py` | `RecognizerSpec.allow_list` (exact values that never match) | 14 |
| `dps/engine/policy.py` | `preset_summaries()` (name, description, version, hash, mode, reversible) | 14 |
| `dps/app.py` | routers, OCR client + readiness probe loop, per-path body limit, coded-error handler, `ocr_client=` injection | 10 |
| `frontend/vite.config.ts`, `frontend/index.html` | `base: '/'` and `/theme-init.js` so multi-segment routes (`/playground/documents`, `/policies/:name`) load their assets | 16 |
| `frontend/src/{App.tsx,lib/api.ts,lib/types.ts,pages/Playground.tsx,pages/Overview.tsx,components/Layout.tsx,styles.css}` | routes, typed `api.documents/schema/entities/policies`, Document seg, OCR readiness, M1 labels | 16–18 |
| `Dockerfile`, `docker-compose.yml`, `tools/smoke_test.py`, `HANDOFF.md`, `README.md` | node 22 stage, `.[fpe,docs]`, `azure-ocr-mock` (profiles `docs`, `full`), `/tmp` tmpfs, 127.0.0.1 ports, smoke extension; `tesseract-ocr` + `ENV DPS_OCR_PROVIDER=auto` (19C) | 19–20, 19C |
| `dps/layers/api/routers/{detect,transform}.py`, `dps/app.py` | NDJSON branches and body limit; `Idempotency-Key` replay; T3 `label_leftovers` before the engine call | 19A, 19B, 19D |
| `dps/engine/service.py`, `dps/engine/policy.py` | `PrivacyEngine.label_leftovers`, `extra_findings=` keyword on the engine operations; `llm_label` tier accepted | 19D |

## Conventions for every task

- Work in `/Users/neelu/document_intelligence/data-privacy-services` (its own git repo, branch `main`). **Never run git against the parent `~/document_intelligence`.**
- Environment: `source .venv/bin/activate && uv pip install -e ".[dev,fpe,docs]"` (Task 1 adds `docs`; `uitest` is installed only for the console check in Task 20).
- **Gates after every task** (contract §0.4): `ruff check dps tests tools && ruff format --check dps tests tools && mypy dps && pytest -q`; when `frontend/` changes also `cd frontend && npm run typecheck && npm run build`. Unit tests are offline; `ocr`-marked tests skip unless the mock is reachable (and **fail** when `DPS_TEST_REQUIRE_SERVICES=1`).
- Type hints everywhere, Google-style docstrings on public names, line length 100, no `Any` unless unavoidable.
- **Never log, print, return in an error, or put in a metric label a customer value, a key, a token, an OCR response body or a sample value.** Errors carry codes, counts, page numbers, limits and names only. Tests assert this (Task 20 adds `tests/test_no_values_in_logs_m1.py`).
- **Git (other agents share this working tree):** stage and commit only the paths the task lists, with an explicit pathspec — `git add <paths> && git commit -m "<msg>" -- <paths>`. Never `git add -A/.`, `commit -a`, `stash`, `reset`, `checkout --`, `clean`. If `.git/index.lock` exists, wait 3 s and retry (≤ 10 times). Author = the global identity (NilenduGanguli); **no `Co-Authored-By` or any other trailer**. Conventional commits.
- Record any deviation from this plan in `docs/superpowers/build/M1-DEVIATIONS.md` (one section per task, same format as the M0 log) and commit it with the task.

## File structure (what M1 creates)

| Path | Responsibility |
|---|---|
| `dps/errors.py` | `CodedError(code, detail, status)` — framework-free errors with a stable machine code |
| `dps/layers/documents/__init__.py` | `DocumentPipeline` façade (extract → OCR → engine → locate → redact → verify → previews), `DocumentOutcome`, `locate_findings` |
| `dps/layers/documents/models.py` | `BBox`, `Word`, `PageText`, `ExtractedDocument` (internal, carry text, never returned); `DocumentOptions`, `DocumentRequest`, `PageBox`, `PageFinding`, `PageSummary`, `RedactionReport`, `DocumentInfo` (public, value-free except replacement tokens) |
| `dps/layers/documents/errors.py` | `DocumentError(CodedError)` |
| `dps/layers/documents/layout.py` | lines from words, page text with exact word offsets, span → boxes (char-precise on text layers), hybrid OCR merge |
| `dps/layers/documents/extract.py` | `sniff_format`, `normalise_pdf`, PDF text layer (pdfplumber words, pypdf structure), DOCX, TXT, CSV, images → `ExtractedDocument` |
| `dps/layers/documents/render.py` | `PDFIUM_LOCK`, `PdfRenderer` (pypdfium2 rasterisation, image coverage), PNG/preview encoding |
| `dps/layers/documents/ocr.py` | `OcrClient` protocol, `AzureReadClient` (Read v3.2: analyze → poll, host-pinned Operation-Location, coded errors), `NullOcrClient`, `build_ocr_client`, `ocr_words_to_points`, `refresh_ocr_readiness` |
| `dps/layers/documents/redact.py` | `PageRedaction`, `redact_document` (raster + boxes → image-only PDF), `inspect_output_pdf` |
| `dps/engine/shapes.py` | `value_shape()` — letters→x/X, digits→9, separators kept, ≤ 24 chars (contract §5.4 shapes; reused by M2 discovery) |
| `dps/engine/synth/schema_gen.py` | `GeneratorSpec` (+ `FieldSpec`, constraints), `execute_spec` (seeded, deterministic), `rows_to_csv`, `SchemaError` |
| `dps/engine/synth/schema_profile.py` | `StructureProfile` (LLM-safe), profiles from JSON Schema / DDL / sample, `heuristic_spec`, `propose_spec` (LLM via gateway, P1 leak guard, stub → heuristic) |
| `dps/layers/api/routers/documents.py` | `POST /api/v1/documents/{detect,obfuscate,synthesize}` (multipart) |
| `dps/layers/api/routers/schema.py` | `POST /api/v1/schema/generator-spec`, `POST /api/v1/schema/synthesize` |
| `dps/layers/api/routers/entities.py` | `GET /api/v1/entities`, `POST /api/v1/policies/test-recognizer` |
| `dps/evaluation/{__init__,corpus,scoring}.py` | labelled synthetic corpus per jurisdiction; precision/recall/F1 per type, confusion, threshold sweep, markdown |
| `tools/eval_detectors.py` | CLI: `generate` the corpus, `run` the evaluation, write the report |
| `tools/make_demo_docs.py`, `demo/documents/*` | deterministic synthetic demo documents (text PDF, scanned PDF, PNG, DOCX, TXT, CSV) |
| `tools/console_check.py` | Playwright check of `/playground/documents` and `/policies` against mockups 08/05 (DoD) |
| `tests/services.py` | contract §12.2 service table, `service_url`, `require_service` |
| `tests/documents/{__init__,fixtures}.py` + `test_*.py` | synthetic fixtures (reportlab/pypdfium2/python-docx/Pillow) and document tests |
| `tests/eval/corpus/*.jsonl` | committed labelled corpus (generated, synthetic) |
| `docs/technical/detector-eval-m1.{json,md}` | committed evaluation report |
| `docs/technical/api-m1.md` | M1 API reference as built |
| `frontend/src/pages/{PlaygroundDocuments,Policies}.tsx`, `frontend/src/components/{OcrStatus,PageViewer}.tsx`, `frontend/src/lib/yaml.ts` | console: Document tab, Policies, OCR readiness, page viewer with boxes, YAML export |
| `dps/layers/api/ndjson.py` | NDJSON request/response streaming on detect/obfuscate/synthesize/reveal (Task 19A) |
| `dps/layers/api/idempotency.py` | `Idempotency-Key` parsing and the replica-local, value-free replay cache (Task 19B) |
| `dps/engine/detect/llm_label.py` | T3 metadata labelling: `FieldMeta`, `LabelVerdict`, `label_fields` (Task 19D) |
| `tests/test_{ndjson,idempotency_l1,llm_label}.py`, `tests/documents/test_ocr_tesseract.py` | tests for Tasks 19A–19D |

---

### Task 1: Dependencies, M1 settings, OCR posture, test service discovery

**Files:**
- Modify: `pyproject.toml`, `dps/config.py`, `dps/posture.py`, `.env.example`, `tests/conftest.py`, `tests/test_config.py`, `tests/test_posture.py`, `.gitignore`
- Create: `tests/services.py`, `tests/test_services.py`

**Interfaces introduced**

`pyproject.toml`:
```toml
[project.optional-dependencies]
# (existing ner / fpe / vertex / dev stay)
docs = ["pypdf>=5.0", "pdfplumber>=0.11.4", "pypdfium2>=5.0,<6", "reportlab>=4.2", "python-docx>=1.1", "pillow>=10.4"]
uitest = ["playwright==1.55.0"]   # console check only; matches mcr.microsoft.com/playwright:v1.55.0-noble (M4 pins the same)

[tool.pytest.ini_options]
markers = [
    # existing: ner, fpe, network, browser, integration
    "docs: needs the [docs] extra (pure Python; runs by default when installed)",
    "ocr: needs the Azure Read v3.2 mock at DPS_TEST_OCR_URL (docker compose --profile docs up -d azure-ocr-mock)",
]
```

`dps/config.py` — new fields in a `# --- documents and schema (M1) ---` block of `Settings` (contract §10 rows first, then the additive ones), validators and properties:
```python
    # --- documents and schema (M1, contract §10) ---
    ocr_provider: str = ""                  # "" | off (no OCR) | azure (Read v3.2: mock, org gateway, Azure) |
                                            # tesseract (local CLI) | auto (azure if configured, else tesseract) — Task 19C
    azure_vision_endpoint: str = ""         # http://azure-ocr-mock:5000 locally; the org gateway URL in prod
    azure_vision_key: str = ""              # DPS_AZURE_VISION_KEY_FILE in prod; header Ocp-Apim-Subscription-Key
    ocr_timeout_seconds: float = Field(default=60.0, gt=0, le=600)
    ocr_poll_interval_seconds: float = Field(default=0.5, gt=0, le=10)
    max_document_mb: int = Field(default=20, ge=1, le=512)
    max_document_pages: int = Field(default=200, ge=1, le=5000)
    pdf_render_dpi: int = Field(default=200, ge=72, le=600)
    schema_max_rows: int = Field(default=10_000, ge=1, le=1_000_000)
    # --- additive (M1) ---
    ocr_health_path: str = "/health"        # mock: /health · azure-gateway-api: /_gw/health · "" = no probe
    ocr_probe_interval_seconds: float = Field(default=30.0, ge=0, le=3600)   # 0 = probe once at startup
    ocr_max_concurrency: int = Field(default=4, ge=1, le=32)
    ocr_min_text_chars: int = Field(default=20, ge=0, le=10_000)   # fewer text-layer chars → page is OCR'd (auto)
    ocr_image_coverage: float = Field(default=0.25, ge=0.0, le=1.0)  # images covering this share → OCR'd too
    redact_image_format: str = "jpeg"       # jpeg | png — rasters inside the redacted PDF
    redact_jpeg_quality: int = Field(default=85, ge=50, le=95)
    redact_ocr_verify: bool = True          # re-read redacted pages with OCR when OCR is configured
    preview_dpi: int = Field(default=72, ge=36, le=150)
    max_preview_pages: int = Field(default=50, ge=0, le=500)

    @field_validator("ocr_provider")
    @classmethod
    def _valid_ocr_provider(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in ("", "off", "azure", "tesseract", "auto"):
            raise ValueError("ocr_provider must be one of '', off, azure, tesseract, auto")
        return v

    @field_validator("redact_image_format")
    @classmethod
    def _valid_image_format(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in ("jpeg", "png"):
            raise ValueError("redact_image_format must be 'jpeg' or 'png'")
        return v

    @field_validator("ocr_health_path")
    @classmethod
    def _valid_health_path(cls, v: str) -> str:
        if v and not re.fullmatch(r"/[A-Za-z0-9_./-]*", v):
            raise ValueError("ocr_health_path must be empty or an absolute path")
        return v

    @property
    def max_document_bytes(self) -> int:
        """``max_document_mb`` in bytes."""
        return self.max_document_mb * 1024 * 1024

    @property
    def ocr_configured(self) -> bool:
        """True when the azure provider has both an endpoint and a key."""
        return self.ocr_provider == "azure" and bool(self.azure_vision_endpoint and self.azure_vision_key)
```
Add `"azure_vision_endpoint"` to the existing `@field_validator("stellar_base_url", "coin_url")` list of `_sane_url` (same rule, never echoes the URL). Import `re` and `Field` (from `pydantic`).

`dps/posture.py` — appended inside `posture_problems` before `return problems`:
```python
    if settings.ocr_provider == "azure":
        if not settings.azure_vision_key:
            problems.append("DPS_AZURE_VISION_KEY (or DPS_AZURE_VISION_KEY_FILE) is required for azure OCR")
        if not settings.azure_vision_endpoint.startswith("https://"):
            problems.append("DPS_AZURE_VISION_ENDPOINT must be an https URL")
```

`tests/services.py` (contract §12.2 — the whole table, so M2–M4 only add fixtures):
```python
"""Where the integration containers are (contract §12.2). Defaults are compose host ports."""

from __future__ import annotations

import os
import socket
from urllib.parse import urlsplit

import pytest

#: name → (env var, default URL/DSN, compose profile, compose service)
SERVICES: dict[str, tuple[str, str, str, str]] = {
    "app": ("DPS_TEST_APP_URL", "http://127.0.0.1:8400", "", "app"),
    "ocr": ("DPS_TEST_OCR_URL", "http://127.0.0.1:5016", "docs", "azure-ocr-mock"),
    "pg": (
        "DPS_TEST_PG_DSN",
        "postgresql://postgres:postgres@127.0.0.1:5441/postgres",
        "",
        "dps-db",
    ),
    "source_pg": (
        "DPS_TEST_SOURCE_PG_DSN",
        "postgresql://postgres:postgres@127.0.0.1:5442/bank",
        "sources",
        "source-pg",
    ),
    "oracle": ("DPS_TEST_ORACLE_DSN", "127.0.0.1:1522/FREEPDB1", "oracle", "oracle"),
    "mongo": (
        "DPS_TEST_MONGO_URI",
        "mongodb://127.0.0.1:27018/?directConnection=true",
        "sources",
        "mongo",
    ),
    "s3": ("DPS_TEST_S3_ENDPOINT", "http://127.0.0.1:9010", "sources", "minio"),
    "demo_site": ("DPS_TEST_DEMO_SITE", "http://127.0.0.1:8410", "scanner", "demo-site"),
}
_DEFAULT_PORTS = {"http": 80, "https": 443, "postgresql": 5432, "postgres": 5432, "mongodb": 27017}


def service_url(name: str) -> str:
    """The URL/DSN for ``name``: its env var, else the compose default."""
    env, default, _, _ = SERVICES[name]
    return os.environ.get(env) or default


def host_port(url: str) -> tuple[str, int]:
    """Host and port of a URL, DSN or Oracle ``host:port/service`` string."""
    parts = urlsplit(url if "://" in url else "//" + url)
    host = parts.hostname or "127.0.0.1"
    return host, parts.port or _DEFAULT_PORTS.get(parts.scheme, 80)


def reachable(name: str, timeout: float = 2.0) -> bool:
    """TCP-probe the service for ``timeout`` seconds."""
    host, port = host_port(service_url(name))
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def require_service(name: str) -> str:
    """The service URL, or skip (fail under ``DPS_TEST_REQUIRE_SERVICES=1``) when unreachable."""
    if reachable(name):
        return service_url(name)
    _, _, profile, service = SERVICES[name]
    hint = f"start: docker compose {'--profile ' + profile + ' ' if profile else ''}up -d {service}"
    if os.environ.get("DPS_TEST_REQUIRE_SERVICES") == "1":
        pytest.fail(f"{name} unreachable — {hint}")
    pytest.skip(hint)
```

`tests/conftest.py` — append:
```python
from tests.services import require_service


@pytest.fixture(scope="session")
def ocr_url() -> str:
    """The Read v3.2 mock (compose profile ``docs``); skips when it is not running."""
    return require_service("ocr")
```

`.gitignore` — append `tests/eval/private/` (bank-labelled corpora never enter git) and `var/`.

`.env.example` — append the M1 block (values are local demo literals):
```bash
# Documents (M1). OCR goes through the Azure Read v3.2 contract: the compose mock locally,
# the org gateway (azure-gateway-api, health path /_gw/health) in prod.
DPS_OCR_PROVIDER=azure
DPS_AZURE_VISION_ENDPOINT=http://127.0.0.1:5016
DPS_AZURE_VISION_KEY=mock-key
DPS_OCR_HEALTH_PATH=/health
DPS_MAX_DOCUMENT_MB=20
DPS_MAX_DOCUMENT_PAGES=200
DPS_PDF_RENDER_DPI=200
DPS_REDACT_IMAGE_FORMAT=jpeg
DPS_SCHEMA_MAX_ROWS=10000
```

**Behaviour:** defaults keep OCR off (`ocr_provider=""`; the image sets `auto`, Task 19C); invalid values are refused without echoing input (M0 `hide_input_in_errors`); posture in staging/prod requires a key and an https endpoint when azure OCR is on. `tesseract` and `auto` are accepted here and implemented in Task 19C (until then they behave as `off`).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_config.py`:
```python
def test_m1_document_defaults() -> None:
    s = Settings(_env_file=None)
    assert (s.ocr_provider, s.max_document_mb, s.max_document_pages, s.pdf_render_dpi) == ("", 20, 200, 200)
    assert s.max_document_bytes == 20 * 1024 * 1024 and s.schema_max_rows == 10_000
    assert s.ocr_configured is False and s.redact_image_format == "jpeg" and s.ocr_health_path == "/health"
    on = Settings(_env_file=None, ocr_provider="AZURE", azure_vision_endpoint="http://ocr:5000",
                  azure_vision_key="k")
    assert on.ocr_provider == "azure" and on.ocr_configured is True


@pytest.mark.parametrize(
    "field,value",
    [("ocr_provider", "paddle"), ("redact_image_format", "tiff"), ("pdf_render_dpi", "30"),
     ("ocr_health_path", "health"), ("azure_vision_endpoint", "ftp://ocr"), ("max_document_mb", "0")],
)
def test_m1_settings_refuse_bad_values(field: str, value: str) -> None:
    with pytest.raises(ValueError):
        Settings(_env_file=None, **{field: value})


def test_bad_ocr_endpoint_is_not_echoed() -> None:
    with pytest.raises(ValueError) as exc:
        Settings(_env_file=None, azure_vision_endpoint="ftp://user:s3cret-pw@ocr")
    assert "s3cret-pw" not in str(exc.value)
```

Append to `tests/test_posture.py` (the module's existing `_prod(**kw)` helper builds a valid staging config):
```python
def test_azure_ocr_needs_key_and_https_in_production() -> None:
    problems = posture_problems(_prod(ocr_provider="azure", azure_vision_endpoint="http://gw:8080"))
    assert any("DPS_AZURE_VISION_KEY" in p for p in problems)
    assert any("https" in p for p in problems)
    ok = posture_problems(_prod(ocr_provider="azure", azure_vision_endpoint="https://gw.bank.internal",
                                azure_vision_key="k"))
    assert not any("AZURE" in p for p in ok)
```

`tests/test_services.py`:
```python
from __future__ import annotations

import pytest

from tests.services import host_port, require_service, service_url


def test_service_url_defaults_and_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DPS_TEST_OCR_URL", raising=False)
    assert service_url("ocr") == "http://127.0.0.1:5016"
    monkeypatch.setenv("DPS_TEST_OCR_URL", "http://ocr.test:5000")
    assert service_url("ocr") == "http://ocr.test:5000"


def test_host_port_parses_urls_and_dsns() -> None:
    assert host_port("http://127.0.0.1:5016") == ("127.0.0.1", 5016)
    assert host_port("postgresql://u:p@db:5441/x") == ("db", 5441)
    assert host_port("127.0.0.1:1522/FREEPDB1") == ("127.0.0.1", 1522)
    assert host_port("https://gw.bank") == ("gw.bank", 443)


def test_require_service_skips_or_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DPS_TEST_OCR_URL", "http://127.0.0.1:1")
    monkeypatch.delenv("DPS_TEST_REQUIRE_SERVICES", raising=False)
    with pytest.raises(pytest.skip.Exception, match="--profile docs up -d azure-ocr-mock"):
        require_service("ocr")
    monkeypatch.setenv("DPS_TEST_REQUIRE_SERVICES", "1")
    with pytest.raises(pytest.fail.Exception):
        require_service("ocr")
```

- [ ] **Step 2: Run to confirm failure**

Run: `pytest -q tests/test_config.py tests/test_posture.py tests/test_services.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'tests.services'` and `ValidationError`/`AttributeError` for the new settings.

- [ ] **Step 3: Implement** the `pyproject.toml`, `dps/config.py`, `dps/posture.py`, `tests/services.py`, `tests/conftest.py`, `.gitignore` and `.env.example` changes above, then install: `uv pip install -e ".[dev,fpe,docs]"`.

- [ ] **Step 4: Run the gates**

Run: `pytest -q tests/test_config.py tests/test_posture.py tests/test_services.py && ruff check dps tests tools && ruff format --check dps tests tools && mypy dps && pytest -q`
Expected: the three modules pass with 12 new test cases (6 of them parametrised), the full suite stays green, ruff and mypy clean. `python -c "import pypdf, pdfplumber, pypdfium2, reportlab, docx, PIL"` exits 0.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml dps/config.py dps/posture.py .env.example .gitignore tests/conftest.py tests/services.py tests/test_config.py tests/test_posture.py tests/test_services.py
git commit -m "feat: M1 settings for documents, OCR and schema synthesis; docs extra; test service discovery" -- pyproject.toml dps/config.py dps/posture.py .env.example .gitignore tests/conftest.py tests/services.py tests/test_config.py tests/test_posture.py tests/test_services.py
```

---

### Task 2: Coded errors and value-free policy labels in the API layer

**Files:**
- Create: `dps/errors.py`, `tests/test_api_errors.py`
- Modify: `dps/layers/api/common.py`, `dps/layers/api/routers/detect.py`, `dps/layers/api/routers/transform.py`, `dps/layers/api/routers/policies.py`

**Interfaces introduced**

`dps/errors.py`:
```python
"""Errors that carry a stable machine code, rendered as ``{"detail", "code"}`` (contract §8.2 shape).

Framework-free: engine and layer modules raise :class:`CodedError`; ``dps.layers.api.common``
renders it. A message carries codes, counts, page numbers, limits and names — never a customer
value, key, token or response body.
"""

from __future__ import annotations


class CodedError(Exception):
    """An error with an HTTP-ish ``status`` and a stable snake_case ``code``."""

    status: int = 400

    def __init__(self, code: str, detail: str, *, status: int | None = None) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
        if status is not None:
            self.status = status
```

`dps/layers/api/common.py` additions:
```python
class CodedHTTPException(HTTPException):
    """An HTTPException rendered as ``{"detail", "code"}``.

    M2's ``DpsHttpError`` (contract §8.2) has the same wire shape and should subclass this class.
    """

    def __init__(self, status_code: int, code: str, detail: str,
                 headers: dict[str, str] | None = None) -> None:
        super().__init__(status_code=status_code, detail=detail, headers=headers)
        self.code = code


async def coded_http_exception_handler(_request: Request, exc: Exception) -> JSONResponse:
    """Registered in ``create_app`` for :class:`CodedHTTPException`."""
    assert isinstance(exc, CodedHTTPException)
    return JSONResponse({"detail": exc.detail, "code": exc.code}, status_code=exc.status_code,
                        headers=exc.headers)


_PRESET_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


def policy_label(policy: str | dict[str, Any] | None, default: str) -> str:
    """What audit and logs record for a policy: a preset name or ``<preset>+inline:<hash12>``.

    Never the inline body — an inline policy may hold deny-list values or declared paths.
    """
    if policy is None:
        return default
    if isinstance(policy, str):
        return policy if _PRESET_NAME.fullmatch(policy) else "invalid"
    canon = json.dumps(policy, sort_keys=True, separators=(",", ":"), default=str)
    preset = policy.get("preset")
    base = preset if isinstance(preset, str) and _PRESET_NAME.fullmatch(preset) else "custom"
    return f"{base}+inline:{hashlib.sha256(canon.encode('utf-8')).hexdigest()[:12]}"
```
`run_engine` — the exception ladder becomes (order matters: `VerificationFailed` subclasses `EngineError`):
```python
    except VerificationFailed as exc:
        outcome = "verification_failed"
        raise CodedHTTPException(422, "leakage", str(exc)) from exc
    except EngineError as exc:
        outcome = "bad_request"
        raise CodedHTTPException(400, "bad_request", str(exc)) from exc
    except CodedError as exc:
        outcome = exc.code
        raise CodedHTTPException(exc.status, exc.code, exc.detail) from exc
    except HTTPException:
        outcome = "rejected"
        raise
```
M0 routers (`detect.py`, `transform.py`, `policies.py`) replace `policy_name=str(body.policy or engine.settings.default_policy)` with `policy_name=policy_label(body.policy, engine.settings.default_policy)`. `create_app` registers the handler in Task 10; Step 3 below registers it already (one line) so this task's tests run: `app.add_exception_handler(CodedHTTPException, coded_http_exception_handler)` (import from `dps.layers.api.common`). M0 bodies stay valid: `{"detail"}` plus an additive `"code"`.

- [ ] **Step 1: Write the failing tests** — `tests/test_api_errors.py`:
```python
from __future__ import annotations

import json

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from dps.app import create_app
from dps.audit import MemoryAuditSink
from dps.config import Settings
from dps.errors import CodedError
from dps.layers.api.common import policy_label, run_engine

KEYS = [{"name": "full", "key": "dps_full", "tenant_ids": ["*"], "scopes": ["*"]}]
H = {"X-API-KEY": "dps_full"}


@pytest.fixture
def app_and_sink():  # noqa: ANN201
    settings = Settings(
        _env_file=None,
        env="local",
        auth_enabled=True,
        api_keys=json.dumps(KEYS),
        llm_provider="stub",
        ner_enabled=False,
        rate_limit_enabled=False,
    )
    sink = MemoryAuditSink()
    app = create_app(settings, audit_sink=sink)

    async def boom(request: Request) -> dict[str, str]:
        def fail() -> None:
            raise CodedError("conflict_x", "nope", status=409)

        await run_engine(request, mode="test", policy_name="-", call=fail)
        return {}

    app.add_api_route("/api/v1/_boom", boom, methods=["POST"])
    return app, sink


def test_engine_errors_carry_codes(app_and_sink) -> None:  # noqa: ANN001
    app, _ = app_and_sink
    with TestClient(app) as c:
        r = c.post("/api/v1/detect", json={"data": {"a": 1}, "policy": "nope"}, headers=H)
        assert r.status_code == 400 and r.json()["code"] == "bad_request" and r.json()["detail"]


def test_coded_error_maps_status_and_code(app_and_sink) -> None:  # noqa: ANN001
    app, sink = app_and_sink
    with TestClient(app) as c:
        r = c.post("/api/v1/_boom", headers=H)
    assert r.status_code == 409 and r.json() == {"detail": "nope", "code": "conflict_x"}
    assert sink.events[-1].outcome == "conflict_x"


def test_policy_label_never_records_inline_bodies() -> None:
    inline = {
        "preset": "pci-dss",
        "detect": {
            "custom_recognizers": [
                {"name": "x", "supported_entity": "X_ID", "deny_list": ["Ananya"]}
            ]
        },
    }
    label = policy_label(inline, "gdpr-pseudonymise")
    assert (
        label.startswith("pci-dss+inline:")
        and "Ananya" not in label
        and len(label) == len("pci-dss+inline:") + 12
    )
    assert policy_label("gdpr-pseudonymise", "d") == "gdpr-pseudonymise"
    assert policy_label("x y!", "d") == "invalid" and policy_label(None, "d") == "d"
    assert policy_label({"treat": {}}, "d").startswith("custom+inline:")


def test_audit_policy_field_is_a_label(app_and_sink) -> None:  # noqa: ANN001
    app, sink = app_and_sink
    policy = {
        "preset": "gdpr-pseudonymise",
        "detect": {"declared_fields": [{"path": "$.secret_owner", "entity_type": "PERSON_NAME"}]},
    }
    with TestClient(app) as c:
        r = c.post(
            "/api/v1/obfuscate",
            json={"data": {"secret_owner": "Ananya Krishnan"}, "policy": policy},
            headers=H,
        )
    assert r.status_code == 200
    assert sink.events[-1].policy.startswith("gdpr-pseudonymise+inline:")
    assert "secret_owner" not in sink.events[-1].policy and "Ananya" not in sink.events[-1].policy
```

- [ ] **Step 2: Run to confirm failure** — `pytest -q tests/test_api_errors.py` → FAIL (`ModuleNotFoundError: dps.errors`).

- [ ] **Step 3: Implement** `dps/errors.py`, the `common.py` additions (imports: `hashlib`, `json`, `re`, `Any`, `JSONResponse`, `CodedError`), the router label change, and the handler registration in `dps/app.py` (`app.add_exception_handler(CodedHTTPException, coded_http_exception_handler)` right after `app.state.audit = …`).

- [ ] **Step 4: Gates** — `pytest -q tests/test_api_errors.py tests/test_api.py && ruff check dps tests tools && ruff format --check dps tests tools && mypy dps && pytest -q` → `4 passed` plus M0's API tests unchanged, suite green.

- [ ] **Step 5: Commit**
```bash
git add dps/errors.py dps/layers/api/common.py dps/layers/api/routers/detect.py dps/layers/api/routers/transform.py dps/layers/api/routers/policies.py dps/app.py tests/test_api_errors.py
git commit -m "feat(api): coded errors and value-free policy labels in audit" -- dps/errors.py dps/layers/api/common.py dps/layers/api/routers/detect.py dps/layers/api/routers/transform.py dps/layers/api/routers/policies.py dps/app.py tests/test_api_errors.py
```

---
### Task 3: Engine output segments (where each replacement landed)

Documents need, per finding, the replacement text and its position in the output (page-view overlays, token highlighting, synthetic validity). The engine already computes both while applying a plan; this task records them. Additive only: no method semantics change, M0 responses gain `"segments": null`.

**Files:**
- Modify: `dps/engine/transform/apply.py`, `dps/engine/synth/llm_text.py`, `dps/engine/service.py`
- Create: `tests/test_segments.py`

**Interfaces introduced**

`dps/engine/transform/apply.py`:
```python
class OutputSegment(BaseModel):
    """Where one replacement landed. Value-free except ``replacement``, which is already in the output."""

    path: str
    start: int | None = None        # input span within the leaf; None = whole value
    end: int | None = None
    out_start: int | None = None    # output span within the same leaf; None = whole value
    out_end: int | None = None
    entity_type: str
    method: str
    replacement: str                # the token / surrogate / mask actually written ("" for redact/null)


class ApplyResult(BaseModel):       # existing fields unchanged, plus:
    segments: list[OutputSegment] = Field(default_factory=list)
```
In `apply_plan`: keep a `segments: list[OutputSegment] = []`. In the whole-value branch, after `new_value = _typed(...)`:
```python
            segments.append(OutputSegment(
                path=path, entity_type=entry.finding.entity_type, method=outcome.method,
                replacement="" if outcome.value is None else str(outcome.value)))
```
In the span branch, after `out_start = start + delta` and `pieces.append(replacement)`:
```python
                segments.append(OutputSegment(
                    path=path, start=start, end=end, out_start=out_start,
                    out_end=out_start + len(replacement), entity_type=entry.finding.entity_type,
                    method=outcome.method, replacement=replacement))
```
and return `ApplyResult(..., segments=segments)`.

`dps/engine/synth/llm_text.py`: `PlaceholderText` gains `slots: list[tuple[int, int, str]] = field(default_factory=list)`; `to_placeholders` appends `(start, end, label)` next to `pieces.extend(...)`. New function, and one line in `synthesize_text`:
```python
def placeholder_segments(text: str, ph: PlaceholderText, output: str, surrogates: SurrogateProvider,
                         locale: str, anchor: str) -> list[OutputSegment]:
    """Output positions of every filled placeholder when the template was not rewritten.

    Surrogates are deterministic, so each one is recomputed and the output rebuilt from the
    original gaps; if the rebuild differs from ``output`` (a gap held text shaped like a
    placeholder, which ``fill_placeholders`` also rewrites) no segments are returned rather
    than wrong ones.
    """
    segments: list[OutputSegment] = []
    pieces: list[str] = []
    cursor = pos = 0
    for start, end, label in ph.slots:
        gap = text[cursor:start]
        pieces.append(gap)
        pos += len(gap)
        finding, original = ph.mapping[label]
        replacement = surrogates.surrogate(finding.entity_type, original, locale, anchor)
        segments.append(OutputSegment(path="$", start=start, end=end, out_start=pos,
                                      out_end=pos + len(replacement), entity_type=finding.entity_type,
                                      method="surrogate", replacement=replacement))
        pieces.append(replacement)
        pos += len(replacement)
        cursor = end
    pieces.append(text[cursor:])
    return segments if "".join(pieces) == output else []
```
In `synthesize_text`, replace the final `return fill_placeholders(...), info` with:
```python
    output = fill_placeholders(templated, ph.mapping, surrogates, locale, anchor)
    if not info["llm_used"]:
        info["segments"] = placeholder_segments(text, ph, output, surrogates, locale, anchor)
    return output, info
```
and change its docstring to "``info`` is safe to log except ``segments`` (replacement text), which the service pops".

`dps/engine/service.py`:
```python
class RequestOptions(BaseModel):    # existing fields, plus:
    return_segments: bool = False   # documents ask for it; M0 callers never see segments unless they do

class TransformResponse(BaseModel): # existing fields, plus:
    segments: list[OutputSegment] | None = None
```
`obfuscate`: `segments=applied.segments if options.return_segments else None`. `synthesize`: text path `segments = info.pop("segments", [])`, data path `segments = applied.segments`; pass `segments=segments if options.return_segments else None`.

- [ ] **Step 1: Write the failing tests** — `tests/test_segments.py`:
```python
from __future__ import annotations

from typing import Any

from dps.config import Settings
from dps.engine.detect.ensemble import Detector, DetectorConfig
from dps.engine.planner import build_plan
from dps.engine.policy import resolve_policy
from dps.engine.service import Payload, PrivacyEngine, RequestOptions
from dps.engine.synth.llm_text import synthesize_text
from dps.engine.transform.apply import ApplyOptions, ApplyResult, apply_plan
from dps.engine.transform.crypto import generate_key_material
from dps.llm.gateway import LlmGateway

TEXT = "Mail ananya.k@example-mail.in or call +91 98451 20377 today"


def _apply(*, text: str | None = None, data: Any = None, style: str = "natural") -> ApplyResult:
    policy = resolve_policy(
        {"preset": "gdpr-pseudonymise", "treat": {"EMAIL": {"method": "det_token"}}},
        default_name="gdpr-pseudonymise",
    )
    found = Detector(DetectorConfig()).detect(data=data, text=text)
    plan = build_plan(found.findings, policy, "obfuscate", fpe_enabled=False)
    options = ApplyOptions(
        policy=policy,
        key=generate_key_material(),
        surrogates=None,
        fpe_enabled=False,
        token_style=style,
    )
    return apply_plan(plan=plan, options=options, data=data, text=text)


def test_span_segments_slice_the_output() -> None:
    result = _apply(text=TEXT)
    spans = [s for s in result.segments if s.start is not None]
    assert {s.entity_type for s in spans} >= {"EMAIL", "PHONE"}
    for s in spans:
        assert result.output[s.out_start : s.out_end] == s.replacement
        assert s.replacement != TEXT[s.start : s.end]


def test_envelope_segments_cover_the_envelope() -> None:
    result = _apply(text=TEXT, style="envelope")
    email = next(s for s in result.segments if s.entity_type == "EMAIL")
    assert result.output[email.out_start : email.out_end].startswith("[[PII:EMAIL:")


def test_whole_value_segments() -> None:
    result = _apply(data={"email": "ananya.k@example-mail.in", "n": 1})
    seg = next(s for s in result.segments if s.path == "$.email")
    assert seg.start is None and seg.out_start is None and seg.replacement == result.output["email"]


class FakeSynth:
    def surrogate(self, entity_type: str, original: str, locale: str, anchor: str) -> str:
        return f"<{entity_type}:{len(original)}>"


async def test_placeholder_segments_match_the_output() -> None:
    text = "Mail a.b@example-mail.in or c.d@example-mail.in today"
    findings = Detector(DetectorConfig()).detect(text=text).findings
    output, info = await synthesize_text(
        text,
        findings,
        surrogates=FakeSynth(),
        locale="en_US",
        anchor="a",
        gateway=None,
        llm_rewrite=False,
    )
    segments = info["segments"]
    assert len(segments) == 2 and all(
        output[s.out_start : s.out_end] == s.replacement for s in segments
    )


async def test_ambiguous_templates_yield_no_segments() -> None:
    text = "Template {EMAIL_9} then a.b@example-mail.in"
    findings = Detector(DetectorConfig()).detect(text=text).findings
    _, info = await synthesize_text(
        text,
        findings,
        surrogates=FakeSynth(),
        locale="en_US",
        anchor="a",
        gateway=None,
        llm_rewrite=False,
    )
    assert info["segments"] == []


async def test_service_returns_segments_only_when_asked() -> None:
    settings = Settings(_env_file=None, llm_provider="stub", fpe_enabled=False, ner_enabled=False)
    engine = PrivacyEngine(settings, gateway=LlmGateway(settings))
    assert engine.obfuscate(Payload(text=TEXT), None, RequestOptions()).segments is None
    asked = engine.obfuscate(Payload(text=TEXT), None, RequestOptions(return_segments=True))
    assert asked.segments and all(
        asked.result[g.out_start : g.out_end] == g.replacement for g in asked.segments
    )
    syn = await engine.synthesize(
        Payload(text=TEXT), "synthetic-realistic", RequestOptions(return_segments=True)
    )
    assert syn.segments and all(
        syn.result[g.out_start : g.out_end] == g.replacement for g in syn.segments
    )
```

- [ ] **Step 2: Run to confirm failure** — `pytest -q tests/test_segments.py` → FAIL (`ApplyResult` has no `segments`; `RequestOptions` has no `return_segments`).

- [ ] **Step 3: Implement** the three module changes above (import `OutputSegment` into `llm_text.py` and `service.py` from `dps.engine.transform.apply`).

- [ ] **Step 4: Gates** — `pytest -q tests/test_segments.py tests/test_apply.py tests/test_llm_text.py tests/test_service.py tests/test_api.py && ruff check dps tests tools && ruff format --check dps tests tools && mypy dps && pytest -q` → `6 passed` in the new module, M0 modules unchanged and green.

- [ ] **Step 5: Commit**
```bash
git add dps/engine/transform/apply.py dps/engine/synth/llm_text.py dps/engine/service.py tests/test_segments.py
git commit -m "feat(engine): record output segments of every replacement (opt-in on responses)" -- dps/engine/transform/apply.py dps/engine/synth/llm_text.py dps/engine/service.py tests/test_segments.py
```

---
### Task 4: Document models, layout geometry, document metrics and log fields

**Files:**
- Create: `dps/layers/documents/__init__.py` (docstring only for now: `"""Layer 1 documents (M1). The DocumentPipeline arrives in Task 9."""`), `dps/layers/documents/models.py`, `dps/layers/documents/errors.py`, `dps/layers/documents/layout.py`, `tests/documents/__init__.py` (empty), `tests/documents/test_layout.py`, `tests/documents/test_models.py`
- Modify: `dps/observability.py`, `tests/test_observability.py`

**Interfaces introduced**

`dps/layers/documents/errors.py`:
```python
"""Document refusals and failures with stable codes (rendered as {"detail", "code"})."""

from __future__ import annotations

from dps.errors import CodedError


class DocumentError(CodedError):
    """A document the pipeline refuses (4xx) or cannot process right now (5xx)."""

    status = 422
```
Codes used across M1 (status): `unsupported_format` 415 · `empty_document` 422 · `corrupt_document` 422 · `encrypted_pdf` 422 · `unsupported_encoding` 422 · `csv_ragged_rows` 422 · `document_too_large` 413 · `too_many_pages` 413 · `image_too_large` 413 · `text_too_long` 413 · `too_many_values` 413 · `invalid_request` 422 · `redacted_pdf_not_allowed` 422 · `redaction_unsupported_format` 422 · `ocr_required` 422 · `redaction_needs_ocr` 422 · `redaction_unlocated` 422 · `leakage` 422 · `redaction_verify_failed` 500 · `ocr_unconfigured` 503 · `ocr_unavailable` 503.

`dps/layers/documents/models.py`:
```python
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
    out_span: list[int] | None = (
        None  # [start, end) of the replacement in ``result`` (text documents)
    )
    ocr_confidence: float | None = None  # lowest OCR confidence of the words holding it
    review: bool = False  # OCR confidence below REVIEW_OCR_CONFIDENCE


class PageSummary(BaseModel):
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
```

`dps/layers/documents/layout.py`:
```python
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
```

`dps/observability.py`:
- `_SAFE_FIELDS` gains `"format"`, `"pages"`, `"ocr_pages"`, `"unreadable_pages"`, `"boxes"`, `"words"`, `"rows"`, `"fields"`, `"generator"`, `"source"`, `"engine"`, `"phase"`, `"http_status"`, `"host"`, `"fallback"`, `"spec_hash"`, `"bytes"`, `"warnings"` (each a count, a name, a hash or an infrastructure host — never a value).
- metrics:
```python
DOCUMENT_PAGES = Counter("dps_document_pages_total", "Document pages processed", ["format", "source"])
REDACTED_BOXES = Counter("dps_redacted_boxes_total", "Boxes burned into redacted PDFs")
OCR_CALLS = Counter("dps_ocr_calls_total", "OCR calls by engine and outcome", ["engine", "outcome"])
OCR_SECONDS = Histogram("dps_ocr_seconds", "OCR call latency (analyze + polls)",
                        buckets=(0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0, 120.0))
```
- `configure_logging` ends with (third-party loggers bypass the structlog allow-list, and pdfminer's debug output quotes page content):
```python
    for name, lvl in (("pypdf", logging.ERROR), ("pdfminer", logging.ERROR), ("pdfplumber", logging.ERROR),
                      ("PIL", logging.ERROR), ("reportlab", logging.ERROR), ("faker", logging.ERROR),
                      ("httpx", logging.WARNING), ("httpcore", logging.WARNING)):
        logging.getLogger(name).setLevel(lvl)
```

- [ ] **Step 1: Write the failing tests**

`tests/documents/test_layout.py`:
```python
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


def test_line_of_offset_and_words_in_span() -> None:
    p = page([w("a", 72, 100), w("b", 72, 120)])
    assert line_of_offset(p, p.text.index("b")) == 2
    assert [x.text for _, x in words_in_span(p, 0, len(p.text))] == ["a", "b"]
    assert line_of_offset(PageText(page=1, width=1, height=1, source="text", text="x\ny"), 2) == 2
```

`tests/documents/test_models.py`:
```python
from __future__ import annotations

import pytest
from pydantic import ValidationError

from dps.layers.documents.models import (
    PAGE_BREAK,
    BBox,
    DocumentRequest,
    ExtractedDocument,
    PageText,
    validation_message,
)


def test_document_text_and_page_starts() -> None:
    doc = ExtractedDocument(
        format="pdf",
        pages=[
            PageText(page=1, width=1, height=1, source="ocr", text="ab"),
            PageText(page=2, width=1, height=1, source="ocr", text="cde"),
        ],
    )
    assert doc.text() == "ab" + PAGE_BREAK + "cde" and doc.page_starts() == [0, 5]
    assert doc.text()[doc.page_starts()[1] :] == "cde"


def test_bbox_helpers() -> None:
    a, b = BBox(x0=0, y0=0, x1=10, y1=10), BBox(x0=5, y0=5, x1=20, y1=20)
    assert (
        a.area() == 100
        and a.intersection_area(b) == 25
        and a.union(b) == BBox(x0=0, y0=0, x1=20, y1=20)
    )
    assert a.padded(3, 12, 12) == BBox(x0=0, y0=0, x1=12, y1=12)


def test_request_validation_messages_never_echo_inputs() -> None:
    with pytest.raises(ValidationError) as exc:
        DocumentRequest.model_validate(
            {"options": {"ocr": "sometimes-SECRET", "consistency": {"key_material": "DEK-SECRET"}}}
        )
    message = validation_message(exc.value)
    assert "options.ocr" in message and "SECRET" not in message
    assert DocumentRequest.model_validate({}).options.ocr == "auto"
    with pytest.raises(ValidationError):
        DocumentRequest.model_validate({"unknown": 1})
```

Append to `tests/test_observability.py`:
```python
def test_m1_fields_are_allowed_and_values_still_dropped() -> None:
    out = _drop_value_fields(None, "info", {"event": "document_processed", "pages": 3, "boxes": 9,
                                            "ocr_pages": 2, "text": "Ananya", "value": "x"})
    assert out == {"event": "document_processed", "pages": 3, "boxes": 9, "ocr_pages": 2}


def test_third_party_loggers_are_capped() -> None:
    configure_logging("DEBUG", json_output=True)
    assert logging.getLogger("pdfminer").level == logging.ERROR
    assert logging.getLogger("httpx").level == logging.WARNING
```
(import `logging` and `configure_logging` at the top of the module if not already imported).

- [ ] **Step 2: Run to confirm failure** — `pytest -q tests/documents tests/test_observability.py` → FAIL (`ModuleNotFoundError: dps.layers.documents.models`).

- [ ] **Step 3: Implement** `errors.py`, `models.py`, `layout.py`, the package docstring and the observability changes exactly as above.

- [ ] **Step 4: Gates** — `pytest -q tests/documents tests/test_observability.py && ruff check dps tests tools && ruff format --check dps tests tools && mypy dps && pytest -q` → `12 passed` in `tests/documents`, observability +2, suite green.

- [ ] **Step 5: Commit**
```bash
git add dps/layers/documents/__init__.py dps/layers/documents/models.py dps/layers/documents/errors.py dps/layers/documents/layout.py dps/observability.py tests/documents/__init__.py tests/documents/test_layout.py tests/documents/test_models.py tests/test_observability.py
git commit -m "feat(documents): page models, word layout and span-to-box geometry; document metrics" -- dps/layers/documents/__init__.py dps/layers/documents/models.py dps/layers/documents/errors.py dps/layers/documents/layout.py dps/observability.py tests/documents/__init__.py tests/documents/test_layout.py tests/documents/test_models.py tests/test_observability.py
```

---
### Task 5: PDF normalisation, text layer and serialised rasterisation

**Files:**
- Create: `dps/layers/documents/render.py`, `dps/layers/documents/extract.py` (PDF part), `tests/documents/fixtures.py`, `tests/documents/test_pdf.py`

**Interfaces introduced:** `PDFIUM_LOCK`; `PdfRenderer(data)` with `__len__`, `page_size(i)`, `render(i, dpi) -> PIL.Image`, `image_coverage(i) -> float`, `close()`; `encode_png(image)`, `preview_png(image, *, source_dpi, target_dpi)`, `b64(data)`; `normalise_pdf(content, *, max_pages) -> tuple[bytes, int]` (bytes, annotation count); `extract_pdf(content, settings, ocr_mode) -> ExtractedDocument`.

**Behaviour:** a page is `scan_suspect` when its text layer holds fewer than `ocr_min_text_chars` characters or images cover ≥ `ocr_image_coverage` of it; `needs_ocr` = `always`, or `auto` and suspect; `never` never OCRs (the pipeline later refuses a redacted PDF of a suspect page). Text-layer pages keep character boxes (one per code unit, so ligatures still align). Encrypted PDFs open only with an empty user password (`encrypted_pdf` otherwise); garbage → `corrupt_document`; > `max_document_pages` → `too_many_pages` (413). Annotations are counted and reported as the warning `annotations_not_scanned_or_drawn` (their text is not scanned and they are never drawn, see `render`). pypdf ≥ 6 deprecates editing pages not attached to a writer, so normalisation works on `PdfWriter(clone_from=reader).pages`.

`dps/layers/documents/render.py`:
```python
"""Rasterisation with pypdfium2 (no poppler, no PyMuPDF).

PDFium is not thread-safe and the pipeline runs CPU work in worker threads, so every call into
pypdfium2 — open, render, object walk, close — holds ``PDFIUM_LOCK``. Renders are therefore
serialised per process; scale out with replicas.
"""

from __future__ import annotations

import base64
import io
import threading

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c
from PIL import Image

from dps.layers.documents.errors import DocumentError

PDFIUM_LOCK = threading.RLock()


class PdfRenderer:
    """One open PDFium document; every call is serialised behind ``PDFIUM_LOCK``."""

    def __init__(self, data: bytes) -> None:
        with PDFIUM_LOCK:
            try:
                self._doc = pdfium.PdfDocument(data)
            except pdfium.PdfiumError as exc:
                raise DocumentError("corrupt_document", "the PDF could not be rendered") from exc
        self._closed = False

    def __enter__(self) -> PdfRenderer:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def __len__(self) -> int:
        with PDFIUM_LOCK:
            return len(self._doc)

    def page_size(self, index: int) -> tuple[float, float]:
        """Page ``index`` width and height in points."""
        with PDFIUM_LOCK:
            page = self._doc[index]
            try:
                width, height = page.get_size()
                return float(width), float(height)
            finally:
                page.close()

    def render(self, index: int, dpi: float) -> Image.Image:
        """Page ``index`` as an RGB image. Forms and annotations are not drawn: their content is
        never scanned, so it must never reach an output."""
        with PDFIUM_LOCK:
            if self._closed:
                raise DocumentError("corrupt_document", "the renderer was already closed")
            page = self._doc[index]
            try:
                bitmap = page.render(scale=dpi / 72.0, may_draw_forms=False, draw_annots=False)
                return bitmap.to_pil().convert("RGB")
            finally:
                page.close()

    def image_coverage(self, index: int) -> float:
        """Share of page ``index`` covered by image objects (0..1, overlaps not deduplicated)."""
        with PDFIUM_LOCK:
            page = self._doc[index]
            try:
                width, height = page.get_size()
                covered = 0.0
                for obj in page.get_objects(filter=[pdfium_c.FPDF_PAGEOBJ_IMAGE], max_depth=3):
                    left, bottom, right, top = obj.get_bounds()
                    covered += max(0.0, min(right, width) - max(left, 0.0)) * max(
                        0.0, min(top, height) - max(bottom, 0.0)
                    )
                return min(1.0, covered / max(width * height, 1.0))
            finally:
                page.close()

    def close(self) -> None:
        """Release the document (idempotent)."""
        if not self._closed:
            with PDFIUM_LOCK:
                self._doc.close()
            self._closed = True


def encode_png(image: Image.Image) -> bytes:
    """PNG bytes of ``image``."""
    buf = io.BytesIO()
    image.save(buf, "PNG")
    return buf.getvalue()


def preview_png(image: Image.Image, *, source_dpi: float, target_dpi: float) -> bytes:
    """A PNG downscaled from ``source_dpi`` to ``target_dpi`` (never upscaled)."""
    factor = min(1.0, target_dpi / source_dpi)
    if factor < 1.0:
        size = (max(1, round(image.width * factor)), max(1, round(image.height * factor)))
        image = image.resize(size, Image.Resampling.LANCZOS)
    return encode_png(image)


def b64(data: bytes) -> str:
    """Base64 (standard alphabet) text of ``data``."""
    return base64.b64encode(data).decode("ascii")
```

`dps/layers/documents/extract.py` (this task: everything up to the "other formats" marker; Task 6 appends the rest):
```python
"""Upload → ``ExtractedDocument``: format sniffing and PDF / DOCX / TXT / CSV / image extraction.

PDFs are first normalised (crop box at the origin, page rotation moved into the content) so that
pdfplumber word boxes and pypdfium2 rasters share one coordinate system: points, top-left origin.
pypdf handles structure (decryption check, normalisation); pdfplumber reads positioned words —
pypdf's own text extraction has no reliable word positions.
"""

from __future__ import annotations

import io
from typing import Any

import pdfplumber
from pypdf import PdfReader, PdfWriter, Transformation
from pypdf.errors import PyPdfError
from pypdf.generic import RectangleObject

from dps.config import Settings
from dps.layers.documents.errors import DocumentError
from dps.layers.documents.layout import build_page_text, group_lines
from dps.layers.documents.models import BBox, ExtractedDocument, OcrMode, PageText, Word
from dps.layers.documents.render import PdfRenderer

_PDF_ERRORS = (PyPdfError, ValueError, KeyError, TypeError, OSError, AssertionError)


def normalise_pdf(content: bytes, *, max_pages: int) -> tuple[bytes, int]:
    """Rewrite a PDF so every page's crop box starts at the origin and its rotation lives in the
    content. Afterwards pdfplumber coordinates (points, top-left) and pypdfium2 rasters agree
    exactly, which the redaction boxes rely on. Returns the bytes and the annotation count."""
    try:
        reader = PdfReader(io.BytesIO(content))
        if reader.is_encrypted and not reader.decrypt(""):
            raise DocumentError("encrypted_pdf", "password-protected PDFs are not supported")
        writer = PdfWriter(clone_from=reader)
        count = len(writer.pages)
    except DocumentError:
        raise
    except _PDF_ERRORS as exc:
        raise DocumentError("corrupt_document", "the PDF could not be parsed") from exc
    if count == 0:
        raise DocumentError("corrupt_document", "the PDF has no pages")
    if count > max_pages:
        raise DocumentError(
            "too_many_pages", f"the PDF has {count} pages; the limit is {max_pages}", status=413
        )
    annotations = 0
    try:
        for page in writer.pages:
            annots = page.get("/Annots")
            annotations += len(annots.get_object()) if annots is not None else 0
            if page.rotation % 360:
                page.transfer_rotation_to_content()
            box = page.cropbox
            llx, lly, urx, ury = (
                float(box.left),
                float(box.bottom),
                float(box.right),
                float(box.top),
            )
            if urx - llx <= 0 or ury - lly <= 0:
                raise DocumentError("corrupt_document", "a page has an empty crop box")
            if llx or lly:
                page.add_transformation(Transformation().translate(-llx, -lly))
            rect = RectangleObject((0, 0, urx - llx, ury - lly))
            page.mediabox = rect
            page.cropbox = rect
            page.trimbox = rect
            page.bleedbox = rect
            page.artbox = rect
        out = io.BytesIO()
        writer.write(out)
    except DocumentError:
        raise
    except _PDF_ERRORS as exc:
        raise DocumentError("corrupt_document", "the PDF could not be normalised") from exc
    return out.getvalue(), annotations


def _text_layer_words(plumber_page: Any) -> list[Word]:
    words: list[Word] = []
    deduped = plumber_page.dedupe_chars()
    for raw in deduped.extract_words(
        keep_blank_chars=False, use_text_flow=False, return_chars=True
    ):
        chars = raw.get("chars") or []
        text = "".join(c["text"] for c in chars) or raw["text"]
        if not text.strip():
            continue
        boxes: list[BBox] = []
        for c in chars:
            box = BBox(
                x0=float(c["x0"]), y0=float(c["top"]), x1=float(c["x1"]), y1=float(c["bottom"])
            )
            boxes.extend([box] * len(c["text"]))
        words.append(
            Word(
                text=text,
                bbox=BBox(
                    x0=float(raw["x0"]),
                    y0=float(raw["top"]),
                    x1=float(raw["x1"]),
                    y1=float(raw["bottom"]),
                ),
                source="text_layer",
                char_boxes=boxes if len(boxes) == len(text) else None,
            )
        )
    return words


def extract_pdf(content: bytes, settings: Settings, ocr_mode: OcrMode) -> ExtractedDocument:
    """Normalise, read the text layer, and flag pages that need OCR."""
    normalised, annotations = normalise_pdf(content, max_pages=settings.max_document_pages)
    with PdfRenderer(normalised) as renderer:
        coverage = [renderer.image_coverage(i) for i in range(len(renderer))]
    pages: list[PageText] = []
    try:
        with pdfplumber.open(io.BytesIO(normalised)) as pdf:
            for i, plumber_page in enumerate(pdf.pages):
                words = [] if ocr_mode == "always" else _text_layer_words(plumber_page)
                chars = sum(len(w.text) for w in words)
                page = PageText(
                    page=i + 1,
                    width=float(plumber_page.width),
                    height=float(plumber_page.height),
                    source="text_layer",
                    image_coverage=round(coverage[i], 4),
                )
                page.scan_suspect = (
                    chars < settings.ocr_min_text_chars
                    or coverage[i] >= settings.ocr_image_coverage
                )
                page.needs_ocr = ocr_mode == "always" or (ocr_mode == "auto" and page.scan_suspect)
                if words:
                    build_page_text(page, group_lines(words))
                else:
                    page.source = "unreadable" if page.scan_suspect else "empty"
                pages.append(page)
    except DocumentError:
        raise
    except Exception as exc:  # noqa: BLE001 - pdfminer raises many types; report the name only
        raise DocumentError(
            "corrupt_document", f"the PDF text layer could not be read ({type(exc).__name__})"
        ) from exc
    doc = ExtractedDocument(format="pdf", pages=pages, pdf=normalised)
    if annotations:
        doc.warnings.append("annotations_not_scanned_or_drawn")
    return doc
```

`tests/documents/fixtures.py` (complete; later tasks only use more of it — `FakeOcr` and `words_px` import the OCR and extract modules lazily):
```python
"""Synthetic test documents (no real data): every value is a fixture literal shared with the M0 tests."""

from __future__ import annotations

import io

from PIL import Image
from pypdf import PdfReader, PdfWriter
from pypdf.annotations import FreeText
from pypdf.generic import RectangleObject
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from dps.layers.documents.render import PdfRenderer

KYC_LINES = (
    "Customer: Ananya Krishnan",
    "SSN 123-45-6789 on file",
    "Email ananya.k@example-mail.in",
    "Card 4111 1111 1111 1111 expires 09/28",
)


def text_pdf(
    lines=KYC_LINES, *, pages: int = 1, size=(612.0, 792.0), font_size: int = 14, x: float = 72.0
) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=size, invariant=1)
    for n in range(pages):
        c.setFont("Helvetica", font_size)
        for i, line in enumerate(lines):
            c.drawString(
                x, size[1] - 72 - i * font_size * 2, line if n == 0 else f"Page {n + 1}: {line}"
            )
        c.showPage()
    c.save()
    return buf.getvalue()


def rewrite(
    data: bytes, *, rotate: int = 0, crop=None, encrypt=None, author=None, annotation=None
) -> bytes:
    reader = PdfReader(io.BytesIO(data))
    writer = PdfWriter()
    for page in reader.pages:
        if rotate:
            page.rotation = rotate
        if crop is not None:
            page.cropbox = RectangleObject(crop)
        writer.add_page(page)
    if author is not None:
        writer.add_metadata({"/Author": author})
    if annotation is not None:
        writer.add_annotation(
            page_number=0, annotation=FreeText(text=annotation, rect=(50, 550, 300, 600))
        )
    if encrypt is not None:
        writer.encrypt(user_password=encrypt, owner_password="owner")
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def rotated_pdf(lines=KYC_LINES) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(612, 792), invariant=1)
    c.saveState()
    c.translate(612, 0)
    c.rotate(90)
    c.setFont("Helvetica", 14)
    for i, line in enumerate(lines):
        c.drawString(72, 500 - i * 28, line)
    c.restoreState()
    c.showPage()
    c.save()
    return rewrite(buf.getvalue(), rotate=90)


def cropped_pdf(lines=KYC_LINES) -> bytes:
    return rewrite(text_pdf(lines, x=150), crop=(100, 100, 500, 700))


def encrypted_pdf(password: str = "pw") -> bytes:
    return rewrite(text_pdf(), encrypt=password)


def annotated_pdf() -> bytes:
    return rewrite(text_pdf(), author="Ananya Krishnan", annotation="SSN 123-45-6789")


def scanned_pdf(lines=KYC_LINES, *, dpi: int = 200, pages: int = 1) -> bytes:
    src = text_pdf(lines, pages=pages)
    buf = io.BytesIO()
    c = canvas.Canvas(buf, invariant=1)
    with PdfRenderer(src) as renderer:
        for i in range(len(renderer)):
            width, height = renderer.page_size(i)
            c.setPageSize((width, height))
            c.drawImage(ImageReader(renderer.render(i, dpi)), 0, 0, width=width, height=height)
            c.showPage()
    c.save()
    return buf.getvalue()


def hybrid_pdf(lines=KYC_LINES) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(612, 792), invariant=1)
    c.setFont("Helvetica", 14)
    for i, line in enumerate(lines):
        c.drawString(72, 720 - i * 28, line)
    c.drawImage(ImageReader(Image.new("RGB", (800, 600), "white")), 72, 72, width=468, height=351)
    c.showPage()
    c.save()
    return buf.getvalue()


def png_document(lines=KYC_LINES, *, dpi: int = 200) -> bytes:
    with PdfRenderer(text_pdf(lines)) as renderer:
        image = renderer.render(0, dpi)
    buf = io.BytesIO()
    image.save(buf, "PNG", dpi=(dpi, dpi))
    return buf.getvalue()


def jpeg_rotated_exif(width: int = 300, height: int = 100) -> bytes:
    exif = Image.Exif()
    exif[0x0112] = 6  # orientation: rotate 90 CW on display
    buf = io.BytesIO()
    Image.new("RGB", (width, height), "white").save(buf, "JPEG", exif=exif.tobytes())
    return buf.getvalue()


def huge_png(width: int = 8000, height: int = 6000) -> bytes:
    buf = io.BytesIO()
    Image.new("1", (width, height), 1).save(buf, "PNG")
    return buf.getvalue()


def docx_document() -> bytes:
    import docx

    document = docx.Document()
    document.sections[0].header.paragraphs[0].text = "Ref CUST-884120"
    document.add_paragraph(KYC_LINES[0])
    table = document.add_table(rows=2, cols=2)
    for r, (label, value) in enumerate((("SSN", "123-45-6789"), ("Card", "4111 1111 1111 1111"))):
        table.cell(r, 0).text = label
        table.cell(r, 1).text = value
    paragraph = document.add_paragraph("Mail ananya.k@example-mail.in")
    paragraph.add_run().add_break()
    paragraph.add_run("call +91 98451 20377")
    buf = io.BytesIO()
    document.save(buf)
    return buf.getvalue()


def csv_document(rows: list[dict[str, str]], *, delimiter: str = ",") -> bytes:
    import csv

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(rows[0]), delimiter=delimiter, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue().encode("utf-8")


CUSTOMERS = [
    {
        "customer_id": "CUST-884120",
        "full_name": "Ananya Krishnan",
        "email": "ananya.k@example-mail.in",
        "notes": "card 4111 1111 1111 1111, call later",
        "risk_score": "712",
    },
    {
        "customer_id": "CUST-884121",
        "full_name": "Rohan Desai",
        "email": "rohan.d@example-mail.in",
        "notes": "no remarks",
        "risk_score": "640",
    },
]


class FakeOcr:
    """An ``OcrClient`` double: each ``read`` answers with the next prepared page of words.

    Words are in pixels of the image DPS sends (``pdf_render_dpi`` for PDF pages)."""

    engine = "fake-ocr"

    def __init__(
        self,
        pages: list[list[tuple[str, tuple[float, float, float, float]]]],
        *,
        confidence: float = 0.97,
    ) -> None:
        self.pages = pages
        self.calls = 0
        self.images: list[bytes] = []
        self.confidence = confidence

    def configured(self) -> bool:
        return True

    async def read(self, image: bytes, *, content_type: str = "image/png"):  # noqa: ANN201
        from dps.layers.documents.ocr import OcrPage, OcrWord

        assert image.startswith(b"\x89PNG\r\n\x1a\n")
        self.images.append(image)
        words = self.pages[min(self.calls, len(self.pages) - 1)] if self.pages else []
        self.calls += 1
        return OcrPage(
            width=0.0,
            height=0.0,
            unit="pixel",
            angle=0.0,
            words=[
                OcrWord(
                    text=t,
                    polygon=(x0, y0, x1, y0, x1, y1, x0, y1),
                    confidence=self.confidence,
                    line=int(y0 // 20),
                )
                for t, (x0, y0, x1, y1) in words
            ],
        )

    async def probe(self) -> tuple[bool, str]:
        return True, "up"

    async def aclose(self) -> None:
        return None


def words_px(
    pdf: bytes, *, dpi: int = 200, drop: tuple[str, ...] = ()
) -> list[list[tuple[str, tuple[float, float, float, float]]]]:
    """Text-layer words of ``pdf`` per page in pixels at ``dpi``: what a perfect OCR returns for the
    scanned twin of ``pdf``. ``drop`` removes words (e.g. those a redaction painted over)."""
    from dps.config import Settings
    from dps.layers.documents.extract import extract_pdf

    doc = extract_pdf(pdf, Settings(_env_file=None), "auto")
    s = dpi / 72.0
    return [
        [
            (w.text, (w.bbox.x0 * s, w.bbox.y0 * s, w.bbox.x1 * s, w.bbox.y1 * s))
            for w in p.words
            if w.text not in drop
        ]
        for p in doc.pages
    ]
```

- [ ] **Step 1: Write the failing tests** — `tests/documents/test_pdf.py`:
```python
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

pytestmark = pytest.mark.docs
pytest.importorskip("pdfplumber")
pytest.importorskip("pypdfium2")

from dps.config import Settings  # noqa: E402
from dps.layers.documents.errors import DocumentError  # noqa: E402
from dps.layers.documents.extract import extract_pdf  # noqa: E402
from dps.layers.documents.models import ExtractedDocument, Word  # noqa: E402
from dps.layers.documents.render import PdfRenderer  # noqa: E402
from tests.documents.fixtures import (  # noqa: E402
    KYC_LINES,
    annotated_pdf,
    cropped_pdf,
    encrypted_pdf,
    hybrid_pdf,
    rewrite,
    rotated_pdf,
    scanned_pdf,
    text_pdf,
)

SET = Settings(_env_file=None)


def _dark(image, box) -> float:
    crop = image.convert("L").crop(box)
    return sum(crop.histogram()[:128]) / max(crop.width * crop.height, 1)


def _word(doc: ExtractedDocument, text: str) -> Word:
    return next(w for p in doc.pages for w in p.words if w.text == text)


def _ink(doc: ExtractedDocument, text: str) -> tuple[float, float]:
    word, s = _word(doc, text), 200 / 72
    with PdfRenderer(doc.pdf) as r:
        image = r.render(0, 200)
    on = (
        int(word.bbox.x0 * s),
        int(word.bbox.y0 * s),
        int(word.bbox.x1 * s),
        int(word.bbox.y1 * s),
    )
    off = (on[0], on[1] + int(200 * s), on[2], on[3] + int(200 * s))
    return _dark(image, on), _dark(image, off)


def test_text_layer_pages_read_exactly():
    page = extract_pdf(text_pdf(), SET, "auto").pages[0]
    assert page.source == "text_layer" and not page.needs_ocr and not page.scan_suspect
    assert page.text.splitlines() == list(KYC_LINES)
    assert all(w.char_boxes is not None and len(w.char_boxes) == len(w.text) for w in page.words)


def test_boxes_land_on_the_raster_for_cropped_pages():
    doc = extract_pdf(cropped_pdf(), SET, "auto")
    on, off = _ink(doc, "123-45-6789")
    assert on > 0.05 and off < 0.01
    assert (doc.pages[0].width, doc.pages[0].height) == (400.0, 600.0)


def test_rotated_pages_are_upright():
    doc = extract_pdf(rotated_pdf(), SET, "auto")
    assert (
        doc.pages[0].width > doc.pages[0].height and "SSN 123-45-6789 on file" in doc.pages[0].text
    )
    on, off = _ink(doc, "123-45-6789")
    assert on > 0.05 and off < 0.01


def test_scanned_pages_are_flagged_for_ocr():
    auto = extract_pdf(scanned_pdf(), SET, "auto").pages[0]
    assert (
        auto.needs_ocr
        and auto.scan_suspect
        and auto.source == "unreadable"
        and auto.image_coverage > 0.95
    )
    never = extract_pdf(scanned_pdf(), SET, "never").pages[0]
    assert never.scan_suspect and not never.needs_ocr


def test_hybrid_candidates_keep_their_text_layer():
    page = extract_pdf(hybrid_pdf(), SET, "auto").pages[0]
    assert page.needs_ocr and page.source == "text_layer" and "123-45-6789" in page.text
    assert page.image_coverage >= 0.25


def test_ocr_always_ignores_the_text_layer():
    page = extract_pdf(text_pdf(), SET, "always").pages[0]
    assert page.needs_ocr and page.text == "" and page.source == "unreadable"


@pytest.mark.parametrize(
    "make,code",
    [(encrypted_pdf, "encrypted_pdf"), (lambda: b"%PDF-1.7 garbage", "corrupt_document")],
)
def test_refusals(make, code):
    with pytest.raises(DocumentError) as exc:
        extract_pdf(make(), SET, "auto")
    assert (exc.value.code, exc.value.status) == (code, 422)


def test_empty_user_password_pdfs_are_read():
    assert "123-45-6789" in extract_pdf(rewrite(text_pdf(), encrypt=""), SET, "auto").pages[0].text


def test_page_limit():
    with pytest.raises(DocumentError) as exc:
        extract_pdf(text_pdf(pages=3), Settings(_env_file=None, max_document_pages=2), "auto")
    assert (exc.value.code, exc.value.status) == ("too_many_pages", 413)


def test_annotations_are_reported():
    assert "annotations_not_scanned_or_drawn" in extract_pdf(annotated_pdf(), SET, "auto").warnings


def test_renderer_serialises_concurrent_renders():
    data = text_pdf(pages=2)
    with PdfRenderer(data) as r, ThreadPoolExecutor(8) as pool:
        sizes = list(pool.map(lambda i: r.render(i % 2, 100).size, range(16)))
    assert len(set(sizes)) == 1 and sizes[0] == (850, 1100)
```

- [ ] **Step 2: Run to confirm failure** — `pytest -q tests/documents/test_pdf.py` → FAIL (`ModuleNotFoundError: dps.layers.documents.render`).

- [ ] **Step 3: Implement** `render.py` and the PDF part of `extract.py` as above.

- [ ] **Step 4: Gates** — `pytest -q tests/documents/test_pdf.py -W error::DeprecationWarning && ruff check dps tests tools && ruff format --check dps tests tools && mypy dps && pytest -q` → `12 passed` (verified against pypdf 6.20 / pdfplumber 0.11.10 / pypdfium2 5.14 while writing this plan). If `test_boxes_land_on_the_raster_for_cropped_pages` fails, print `doc.pages[0].width/height` and the pdfplumber `page.bbox` of the normalised PDF: anything but `(0, 0, w, h)` means the translate/box rewrite in `normalise_pdf` did not apply.

- [ ] **Step 5: Commit**
```bash
git add dps/layers/documents/render.py dps/layers/documents/extract.py tests/documents/fixtures.py tests/documents/test_pdf.py
git commit -m "feat(documents): PDF normalisation, positioned text layer, serialised pypdfium2 rendering" -- dps/layers/documents/render.py dps/layers/documents/extract.py tests/documents/fixtures.py tests/documents/test_pdf.py
```

---

### Task 6: DOCX, TXT, CSV and image extraction; format sniffing

**Files:**
- Modify: `dps/layers/documents/extract.py` (append the "other formats" part)
- Create: `tests/documents/test_extract.py`

**Interfaces introduced:** `sniff_format(content, filename, content_type) -> DocFormat` (magic bytes first: `%PDF-`, PNG, JPEG, a zip holding `word/document.xml`; then `.csv`/`text/csv`; then `.txt`/`text/*`/UTF-8 that is ≥ 95 % printable); `extract_text_file`, `extract_docx`, `extract_csv`, `extract_image`, `extract_document(content, fmt, settings, ocr_mode) -> ExtractedDocument`; constants `MAX_IMAGE_PIXELS = 40_000_000`, `MAX_IMAGE_SIDE = 10_000` (the Read v3.2 limit), `DEFAULT_IMAGE_DPI = 200.0`, `ZIP_RATIO_LIMIT = 10.0`, `ZIP_MAX_ENTRIES = 5_000`.

**Behaviour:** DOCX reads headers, the body (paragraphs; table rows as tab-joined cells; text boxes inline with their paragraph; `w:tab` → tab, `w:br`/`w:cr` → newline), footnotes, endnotes, comments and footers, in that order, through python-docx's package (parts it does not model are parsed with an XXE-safe lxml parser); one page, no geometry, so no boxes and no redacted PDF. TXT must be UTF-8 (BOM stripped, CRLF/CR → LF). CSV becomes records keyed by the header (blank names → `column_N`, duplicates suffixed `_2`), every cell a string, dialect sniffed among `, ; tab |`; a row longer than the header is refused (its extra cells would silently vanish from the output); rows × columns > `max_items` → 413. Images are refused above 40 M pixels **before** decoding, EXIF-transposed, converted to RGB, capped at 10000 px a side (dpi scaled with it) and re-encoded as PNG with their dpi; page size in points = pixels × 72 / dpi.

Append to `dps/layers/documents/extract.py`, and extend the module's imports with `import csv`, `import re`, `import zipfile`, `from PIL import Image, ImageOps, UnidentifiedImageError` and `DocFormat` in the `dps.layers.documents.models` import (Task 5 leaves them out because they are unused until now — ruff's F401 would fail Task 5's gate otherwise):
```python
# --- other formats ----------------------------------------------------------------------
MAX_IMAGE_PIXELS = 40_000_000      # refuse before decoding (Pillow's own bomb guard is 89M)
MAX_IMAGE_SIDE = 10_000            # Read v3.2 accepts at most 10000 x 10000
DEFAULT_IMAGE_DPI = 200.0
ZIP_RATIO_LIMIT = 10.0               # a DOCX may inflate to at most 10x the upload limit
ZIP_MAX_ENTRIES = 5_000

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_DOCX_PART = re.compile(r"^/word/(header\d*|document|footnotes|endnotes|comments|footer\d*)\.xml$")
_PART_ORDER = ("header", "document", "footnotes", "endnotes", "comments", "footer")


def sniff_format(content: bytes, filename: str | None, content_type: str | None) -> DocFormat:
    """Magic bytes first; the name and declared type only decide between CSV and plain text."""
    if not content:
        raise DocumentError("empty_document", "the uploaded file is empty")
    if content.startswith(b"%PDF-"):
        return "pdf"
    if content.startswith(b"\x89PNG\r\n\x1a\n") or content.startswith(b"\xff\xd8\xff"):
        return "image"
    if content.startswith(b"PK\x03\x04"):
        if _is_docx(content):
            return "docx"
        raise DocumentError("unsupported_format", "this zip container is not a .docx document", status=415)
    name = (filename or "").lower()
    ctype = (content_type or "").split(";")[0].strip().lower()
    if name.endswith(".csv") or ctype in ("text/csv", "application/csv"):
        return "csv"
    if name.endswith((".txt", ".text")) or ctype.startswith("text/") or _looks_like_text(content):
        return "text"
    raise DocumentError("unsupported_format", "supported formats: PDF, DOCX, PNG, JPEG, TXT, CSV", status=415)


def _is_docx(content: bytes) -> bool:
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            names = set(archive.namelist())
    except (zipfile.BadZipFile, OSError):
        return False
    return "word/document.xml" in names and "[Content_Types].xml" in names


def _looks_like_text(content: bytes) -> bool:
    if b"\x00" in content[:65536]:
        return False
    try:
        sample = content[:65536].decode("utf-8")
    except UnicodeDecodeError:
        return False
    printable = sum(ch.isprintable() or ch in "\t\r\n\f" for ch in sample)
    return printable / max(len(sample), 1) >= 0.95


def _decode_text(content: bytes) -> str:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise DocumentError("unsupported_encoding", "text documents must be UTF-8") from exc
    return text.replace("\r\n", "\n").replace("\r", "\n")


def extract_text_file(content: bytes) -> ExtractedDocument:
    page = PageText(page=1, width=0.0, height=0.0, source="text", text=_decode_text(content))
    return ExtractedDocument(format="text", pages=[page])


def _check_zip(content: bytes, limit_bytes: int) -> None:
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            infos = archive.infolist()
    except (zipfile.BadZipFile, OSError) as exc:
        raise DocumentError("corrupt_document", "the .docx container could not be read") from exc
    if len(infos) > ZIP_MAX_ENTRIES or sum(i.file_size for i in infos) > ZIP_RATIO_LIMIT * limit_bytes:
        raise DocumentError("document_too_large", "the .docx expands beyond the size limit", status=413)


def _paragraph_text(paragraph: Any) -> str:
    out: list[str] = []
    for node in paragraph.iter():
        tag = node.tag
        if tag == _W + "t":
            out.append(node.text or "")
        elif tag == _W + "tab":
            out.append("\t")
        elif tag in (_W + "br", _W + "cr"):
            out.append("\n")
        elif tag == _W + "noBreakHyphen":
            out.append("-")
    return "".join(out)


def _block_lines(element: Any, lines: list[str]) -> None:
    for child in element.iterchildren():
        if child.tag == _W + "p":
            text = _paragraph_text(child)
            if text.strip():
                lines.append(text)
        elif child.tag == _W + "tbl":
            for row in child.iter(_W + "tr"):
                cells = [" ".join(t for t in (_paragraph_text(p) for p in cell.iter(_W + "p")) if t.strip())
                         for cell in row.iterchildren(_W + "tc")]
                if any(cells):
                    lines.append("\t".join(cells))
        else:
            _block_lines(child, lines)


def extract_docx(content: bytes, settings: Settings) -> ExtractedDocument:
    """Headers, body (paragraphs, table rows as tab-joined cells, text boxes inline), footnotes,
    endnotes, comments and footers, in that order. DOCX has no page geometry: one page, no boxes."""
    import docx
    from lxml import etree

    _check_zip(content, settings.max_document_bytes)
    try:
        document = docx.Document(io.BytesIO(content))
    except Exception as exc:  # noqa: BLE001 - python-docx raises several types; name only
        raise DocumentError("corrupt_document", f"the .docx could not be opened ({type(exc).__name__})") from exc
    parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False)
    parts: list[tuple[int, str, Any]] = []
    for part in document.part.package.iter_parts():
        name = str(part.partname)
        match = _DOCX_PART.match(name)
        if not match:
            continue
        kind = re.sub(r"\d+$", "", match.group(1))
        element = getattr(part, "element", None)
        if element is None:
            element = etree.fromstring(part.blob, parser=parser)
        parts.append((_PART_ORDER.index(kind), name, element))
    lines: list[str] = []
    for _, _, element in sorted(parts, key=lambda p: (p[0], p[1])):
        body = element.find(_W + "body")
        _block_lines(body if body is not None else element, lines)
    page = PageText(page=1, width=0.0, height=0.0, source="docx", text="\n".join(lines))
    return ExtractedDocument(format="docx", pages=[page])


def extract_csv(content: bytes, settings: Settings) -> ExtractedDocument:
    """CSV as records: header row → keys, every cell a string, dialect sniffed (, ; tab |)."""
    text = _decode_text(content)
    try:
        dialect: Any = csv.Sniffer().sniff(text[:65536], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    rows = list(csv.reader(io.StringIO(text), dialect))
    rows = [r for r in rows if any(cell.strip() for cell in r)]
    if not rows:
        raise DocumentError("empty_document", "the CSV has no header row")
    header: list[str] = []
    for i, raw in enumerate(rows[0]):
        name = raw.strip() or f"column_{i + 1}"
        while name in header:
            name += "_2"
        header.append(name)
    records: list[dict[str, str]] = []
    for line_no, row in enumerate(rows[1:], start=2):
        if len(row) > len(header):
            raise DocumentError("csv_ragged_rows", f"CSV line {line_no} has more cells than the header")
        records.append({h: (row[i] if i < len(row) else "") for i, h in enumerate(header)})
    if len(records) * len(header) > settings.max_items:
        raise DocumentError("too_many_values", f"the CSV holds more than {settings.max_items} values", status=413)
    page = PageText(page=1, width=0.0, height=0.0, source="csv")
    return ExtractedDocument(format="csv", pages=[page], records=records, csv_header=header,
                             csv_delimiter=getattr(dialect, "delimiter", ","))


def _image_dpi(info: dict[Any, Any]) -> float:
    dpi = info.get("dpi")
    try:
        value = float(dpi[0]) if isinstance(dpi, tuple) else float(dpi) if dpi else DEFAULT_IMAGE_DPI
    except (TypeError, ValueError):
        value = DEFAULT_IMAGE_DPI
    return value if 72.0 <= value <= 1200.0 else DEFAULT_IMAGE_DPI


def extract_image(content: bytes) -> ExtractedDocument:
    """PNG/JPEG → one page: EXIF-transposed, RGB, at most 10000 px a side, re-encoded as PNG.
    Page size in points = pixels × 72 / dpi (dpi from the file, else 200)."""
    try:
        with Image.open(io.BytesIO(content)) as opened:
            if opened.width * opened.height > MAX_IMAGE_PIXELS:
                raise DocumentError("image_too_large", f"images are limited to {MAX_IMAGE_PIXELS} pixels", status=413)
            dpi = _image_dpi(opened.info)
            image = ImageOps.exif_transpose(opened).convert("RGB")
    except DocumentError:
        raise
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        raise DocumentError("corrupt_document", "the image could not be decoded") from exc
    longest = max(image.size)
    if longest > MAX_IMAGE_SIDE:
        factor = MAX_IMAGE_SIDE / longest
        image = image.resize((max(1, int(image.width * factor)), max(1, int(image.height * factor))),
                             Image.Resampling.LANCZOS)
        dpi *= factor
    buf = io.BytesIO()
    image.save(buf, "PNG", dpi=(dpi, dpi))
    page = PageText(page=1, width=image.width * 72.0 / dpi, height=image.height * 72.0 / dpi,
                    source="unreadable", scan_suspect=True, needs_ocr=True, image_coverage=1.0)
    return ExtractedDocument(format="image", pages=[page], image_png=buf.getvalue(), image_dpi=dpi)


def extract_document(content: bytes, fmt: DocFormat, settings: Settings, ocr_mode: OcrMode) -> ExtractedDocument:
    """Dispatch by format (``sniff_format`` decides ``fmt``)."""
    if fmt == "pdf":
        return extract_pdf(content, settings, ocr_mode)
    if fmt == "docx":
        return extract_docx(content, settings)
    if fmt == "csv":
        return extract_csv(content, settings)
    if fmt == "image":
        return extract_image(content)
    return extract_text_file(content)
```

- [ ] **Step 1: Write the failing tests** — `tests/documents/test_extract.py`:
```python
from __future__ import annotations

import io
import zipfile

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
```

- [ ] **Step 2: Run to confirm failure** — `pytest -q tests/documents/test_extract.py` → FAIL (`ImportError: cannot import name 'extract_document'`).

- [ ] **Step 3: Implement** the appended part of `extract.py`.

- [ ] **Step 4: Gates** — `pytest -q tests/documents && ruff check dps tests tools && ruff format --check dps tests tools && mypy dps && pytest -q` → `test_extract.py` `15 passed` (verified while writing), all document tests green.

- [ ] **Step 5: Commit**
```bash
git add dps/layers/documents/extract.py tests/documents/test_extract.py
git commit -m "feat(documents): DOCX, TXT, CSV and image extraction with format sniffing and size guards" -- dps/layers/documents/extract.py tests/documents/test_extract.py
```

---

### Task 7: OCR client (Azure Read v3.2 contract), readiness, mock integration test

**Files:**
- Create: `dps/layers/documents/ocr.py`, `tests/documents/test_ocr_client.py`, `tests/documents/test_ocr_mock.py`

**Interfaces introduced:** `OcrError(code, detail)`; `OcrWord`, `OcrPage` (frozen dataclasses); `OcrClient` protocol (`engine`, `configured()`, `async read(image, *, content_type)`, `async probe()`, `async aclose()`); `NullOcrClient`; `AzureReadClient(*, endpoint, key, timeout_s, poll_interval_s, health_path, max_concurrency, verify, transport, sleep, clock)`; `parse_read_result(body) -> OcrPage`; `ocr_words_to_points(page, *, points_per_px) -> list[Word]`; `build_ocr_client(settings) -> OcrClient`; `async refresh_ocr_readiness(ocr, settings) -> None`.

**Wire contract (Read v3.2, identical for the mock, azure-gateway-api and Azure):** `POST {endpoint}/vision/v3.2/read/analyze`, headers `Ocp-Apim-Subscription-Key`, `Content-Type: application/octet-stream`, body = PNG → `202` + `Operation-Location`; `GET` it (same key header) until `{"status": "succeeded" | "failed" | "running" | "notStarted"}`; result `analyzeResult.readResults[0]` with `width`, `height`, `unit` (`pixel` for images), `lines[].words[].{text, boundingBox[8], confidence}`. Submit retries on 429/503 honour `Retry-After` (≤ 3 attempts); poll 429 backs off by `Retry-After`; the overall deadline is `ocr_timeout_seconds`. The mock builds `Operation-Location` from the request's base URL, so the host check holds for `http://azure-ocr-mock:5000` inside compose and `http://127.0.0.1:5016` from the host; the gateway returns its own host (it does not trust forwarded headers by default).

`dps/layers/documents/ocr.py`:
```python
"""OCR through the Azure Computer Vision Read v3.2 wire contract.

``POST {endpoint}/vision/v3.2/read/analyze`` (image bytes) → ``202`` + ``Operation-Location`` →
``GET`` it until ``status`` is ``succeeded`` or ``failed``. The same client talks to the compose
mock (Tesseract inside), the org's azure-gateway-api and real Azure. DPS only ever sends single
page images (it rasterises PDFs itself), so results are read from ``readResults[0]``.

Safety: the subscription key is sent only to the configured endpoint's origin — an
``Operation-Location`` on another host is refused; errors carry a code, the stage and the HTTP
status, never a response body or the key.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urljoin, urlsplit

import httpx

from dps.config import Settings
from dps.layers.documents.models import BBox, Word
from dps.observability import OCR_CALLS, OCR_SECONDS, READINESS, get_logger

logger = get_logger(__name__)
ANALYZE_PATH = "/vision/v3.2/read/analyze"
KEY_HEADER = "Ocp-Apim-Subscription-Key"
MAX_SUBMIT_ATTEMPTS = 3


class OcrError(RuntimeError):
    """An OCR failure with a stable code: ocr_unconfigured, ocr_auth, ocr_rejected, ocr_throttled,
    ocr_unavailable, ocr_failed, ocr_timeout, ocr_protocol."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class OcrWord:
    text: str
    polygon: tuple[float, ...]  # x1,y1 … x4,y4 in ``OcrPage.unit``
    confidence: float | None
    line: int  # index of the service's line


@dataclass(frozen=True)
class OcrPage:
    width: float
    height: float
    unit: str  # "pixel" for images (always, for DPS) or "inch"
    angle: float
    words: list[OcrWord]


class OcrClient(Protocol):
    engine: str

    def configured(self) -> bool: ...

    async def read(self, image: bytes, *, content_type: str = "image/png") -> OcrPage: ...

    async def probe(self) -> tuple[bool, str]: ...

    async def aclose(self) -> None: ...


class NullOcrClient:
    """OCR switched off (``DPS_OCR_PROVIDER`` empty or incomplete)."""

    engine = "none"

    def configured(self) -> bool:
        return False

    async def read(self, image: bytes, *, content_type: str = "image/png") -> OcrPage:
        raise OcrError("ocr_unconfigured", "no OCR provider is configured")

    async def probe(self) -> tuple[bool, str]:
        return False, "unconfigured"

    async def aclose(self) -> None:
        return None


def _origin(url: str) -> tuple[str, str, int]:
    parts = urlsplit(url)
    port = parts.port or (443 if parts.scheme == "https" else 80)
    return parts.scheme, (parts.hostname or "").lower(), port


def _status_error(stage: str, status: int) -> OcrError:
    if status in (401, 403):
        code = "ocr_auth"
    elif status == 429:
        code = "ocr_throttled"
    elif status >= 500:
        code = "ocr_unavailable"
    elif 400 <= status < 500:
        code = "ocr_rejected"
    else:
        code = "ocr_protocol"
    return OcrError(code, f"{stage} returned HTTP {status}")


def _retry_after(response: httpx.Response, default: float) -> float:
    try:
        return min(max(float(response.headers.get("Retry-After", default)), 0.0), 30.0)
    except ValueError:
        return default


def parse_read_result(body: dict[str, Any]) -> OcrPage:
    """``analyzeResult.readResults[0]`` → :class:`OcrPage` (empty words for an empty page)."""
    results = (body.get("analyzeResult") or {}).get("readResults") or []
    if not results:
        return OcrPage(width=0.0, height=0.0, unit="pixel", angle=0.0, words=[])
    first = results[0]
    words: list[OcrWord] = []
    for index, line in enumerate(first.get("lines") or []):
        for word in line.get("words") or []:
            text = str(word.get("text") or "").strip()
            polygon = word.get("boundingBox") or []
            if not text or len(polygon) < 8:
                continue
            try:
                coords = tuple(float(v) for v in polygon[:8])
            except (TypeError, ValueError):
                continue
            conf = word.get("confidence")
            words.append(
                OcrWord(
                    text=text,
                    polygon=coords,
                    confidence=float(conf) if isinstance(conf, int | float) else None,
                    line=index,
                )
            )
    return OcrPage(
        width=float(first.get("width") or 0),
        height=float(first.get("height") or 0),
        unit=str(first.get("unit") or "pixel"),
        angle=float(first.get("angle") or 0.0),
        words=words,
    )


def ocr_words_to_points(page: OcrPage, *, points_per_px: float) -> list[Word]:
    """OCR words → layout words in PDF points (pixel unit × ``points_per_px``; inch × 72)."""
    scale = 72.0 if page.unit == "inch" else points_per_px
    out: list[Word] = []
    for w in page.words:
        xs, ys = w.polygon[0::2], w.polygon[1::2]
        out.append(
            Word(
                text=w.text,
                bbox=BBox(
                    x0=min(xs) * scale, y0=min(ys) * scale, x1=max(xs) * scale, y1=max(ys) * scale
                ),
                source="ocr",
                confidence=w.confidence,
                line_hint=w.line,
            )
        )
    return out


class AzureReadClient:
    """Async Read v3.2 client with bounded concurrency, polling, throttling and a deadline."""

    engine = "azure-read-v3.2"

    def __init__(
        self,
        *,
        endpoint: str,
        key: str,
        timeout_s: float,
        poll_interval_s: float,
        health_path: str = "/health",
        max_concurrency: int = 4,
        verify: str | bool = True,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._base = endpoint.rstrip("/")
        self._origin = _origin(self._base)
        self._key = key
        self._timeout = timeout_s
        self._poll = poll_interval_s
        self._health_path = health_path
        self._sem = asyncio.Semaphore(max_concurrency)
        self._sleep = sleep
        self._clock = clock
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(min(timeout_s, 30.0)),
            verify=verify,
            transport=transport,
            follow_redirects=False,
        )

    def configured(self) -> bool:
        return bool(self._base and self._key)

    async def read(self, image: bytes, *, content_type: str = "image/png") -> OcrPage:
        """OCR one page image. Raises :class:`OcrError`."""
        async with self._sem:
            started = self._clock()
            outcome = "error"
            try:
                page = await self._read(image, deadline=started + self._timeout)
                outcome = "ok"
                return page
            except OcrError as exc:
                outcome = exc.code
                raise
            finally:
                OCR_CALLS.labels(self.engine, outcome).inc()
                OCR_SECONDS.observe(max(self._clock() - started, 0.0))

    async def _read(self, image: bytes, *, deadline: float) -> OcrPage:
        headers = {KEY_HEADER: self._key}
        response: httpx.Response | None = None
        for _ in range(MAX_SUBMIT_ATTEMPTS):
            try:
                response = await self._client.post(
                    self._base + ANALYZE_PATH,
                    content=image,
                    headers={**headers, "Content-Type": "application/octet-stream"},
                )
            except httpx.HTTPError as exc:
                raise OcrError(
                    "ocr_unavailable", f"analyze request failed ({type(exc).__name__})"
                ) from None
            if response.status_code not in (429, 503) or self._clock() >= deadline:
                break
            await self._sleep(_retry_after(response, 1.0))
        assert response is not None
        if response.status_code != 202:
            raise _status_error("analyze", response.status_code)
        operation = self._same_origin(response.headers.get("Operation-Location", ""))
        delay = self._poll
        while True:
            if self._clock() >= deadline:
                raise OcrError("ocr_timeout", f"no result within {self._timeout:.0f}s")
            await self._sleep(delay)
            try:
                poll = await self._client.get(operation, headers=headers)
            except httpx.HTTPError as exc:
                raise OcrError("ocr_unavailable", f"poll failed ({type(exc).__name__})") from None
            if poll.status_code == 429:
                delay = _retry_after(poll, max(delay * 2, 1.0))
                continue
            if poll.status_code != 200:
                raise _status_error("poll", poll.status_code)
            try:
                body = poll.json()
            except ValueError:
                raise OcrError("ocr_protocol", "poll response was not JSON") from None
            status = str(body.get("status", "")).lower()
            if status == "succeeded":
                return parse_read_result(body)
            if status == "failed":
                raise OcrError("ocr_failed", "the OCR service reported status=failed")
            if status not in ("notstarted", "running"):
                raise OcrError("ocr_protocol", "unexpected OCR status")
            delay = self._poll

    def _same_origin(self, location: str) -> str:
        if not location:
            raise OcrError("ocr_protocol", "analyze response had no Operation-Location")
        url = urljoin(self._base + "/", location)
        if _origin(url) != self._origin:
            raise OcrError(
                "ocr_protocol",
                "Operation-Location points at another host; the key is not sent there",
            )
        return url

    async def probe(self) -> tuple[bool, str]:
        """``(ok, detail)`` for /readyz: GET the health path (``configured`` when there is none)."""
        if not self._health_path:
            return True, "configured"
        try:
            response = await self._client.get(self._base + self._health_path, timeout=3.0)
        except httpx.HTTPError:
            return False, "down"
        return (
            (True, "up") if response.status_code == 200 else (False, f"down:{response.status_code}")
        )

    async def aclose(self) -> None:
        await self._client.aclose()


def build_ocr_client(settings: Settings) -> OcrClient:
    """:class:`AzureReadClient` when the azure provider has an endpoint and a key, else
    :class:`NullOcrClient`. TLS to the gateway trusts ``DPS_SSL_CERT_FILE`` when set (the
    corporate CA the Stellar client already uses)."""
    if not settings.ocr_configured:
        return NullOcrClient()
    return AzureReadClient(
        endpoint=settings.azure_vision_endpoint,
        key=settings.azure_vision_key,
        timeout_s=settings.ocr_timeout_seconds,
        poll_interval_s=settings.ocr_poll_interval_seconds,
        health_path=settings.ocr_health_path,
        max_concurrency=settings.ocr_max_concurrency,
        verify=settings.ssl_cert_file or True,
    )


async def refresh_ocr_readiness(ocr: OcrClient, settings: Settings) -> None:
    """Set the ``ocr`` readiness component. It is never *required*: JSON and text need no OCR."""
    if not ocr.configured():
        READINESS.set("ocr", False, "unconfigured", provider=settings.ocr_provider or "none")
        return
    ok, detail = await ocr.probe()
    READINESS.set("ocr", ok, detail, provider=settings.ocr_provider, engine=ocr.engine)
    if not ok:
        logger.warning("ocr_probe_failed", engine=ocr.engine, status=detail)
```

`tests/documents/fixtures.py` already provides `FakeOcr` and `words_px` (Task 5).

- [ ] **Step 1: Write the failing tests** — `tests/documents/test_ocr_client.py`:
```python
from __future__ import annotations

import httpx
import pytest

from dps.config import Settings
from dps.layers.documents.ocr import (
    AzureReadClient,
    NullOcrClient,
    OcrError,
    OcrPage,
    OcrWord,
    build_ocr_client,
    ocr_words_to_points,
    parse_read_result,
    refresh_ocr_readiness,
)
from dps.observability import READINESS

BASE = "http://ocr.test:5000"
RESULT = {
    "status": "succeeded",
    "analyzeResult": {
        "version": "3.2.0",
        "readResults": [
            {
                "page": 1,
                "angle": 0.0,
                "width": 1700,
                "height": 2200,
                "unit": "pixel",
                "lines": [
                    {
                        "text": "SSN 123-45-6789",
                        "boundingBox": [200, 300, 600, 300, 600, 340, 200, 340],
                        "words": [
                            {
                                "text": "SSN",
                                "boundingBox": [200, 300, 280, 300, 280, 340, 200, 340],
                                "confidence": 0.99,
                            },
                            {
                                "text": "123-45-6789",
                                "boundingBox": [300, 300, 600, 300, 600, 340, 300, 340],
                                "confidence": 0.97,
                            },
                        ],
                    },
                    {
                        "text": "Ananya",
                        "boundingBox": [200, 400, 380, 400, 380, 440, 200, 440],
                        "words": [
                            {
                                "text": "Ananya",
                                "boundingBox": [200, 400, 380, 400, 380, 440, 200, 440],
                                "confidence": 0.71,
                            }
                        ],
                    },
                ],
            }
        ],
    },
}


class Script:
    """A MockTransport handler that replays responses and records requests."""

    def __init__(self, *responses: httpx.Response) -> None:
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.responses.pop(0)


def accepted(location: str = f"{BASE}/vision/v3.2/read/analyzeResults/op1") -> httpx.Response:
    return httpx.Response(202, headers={"Operation-Location": location})


def client(
    script: Script,
    *,
    sleeps: list[float] | None = None,
    now: list[float] | None = None,
    timeout: float = 60.0,
    key: str = "k-SECRET",
    health: str = "/health",
) -> AzureReadClient:
    clock = now if now is not None else [0.0]

    async def sleep(seconds: float) -> None:
        if sleeps is not None:
            sleeps.append(seconds)
        clock[0] += seconds

    return AzureReadClient(
        endpoint=BASE,
        key=key,
        timeout_s=timeout,
        poll_interval_s=0.5,
        health_path=health,
        transport=httpx.MockTransport(script),
        sleep=sleep,
        clock=lambda: clock[0],
    )


async def test_read_polls_until_succeeded() -> None:
    script = Script(
        accepted(),
        httpx.Response(200, json={"status": "running"}),
        httpx.Response(200, json=RESULT),
    )
    sleeps: list[float] = []
    page = await client(script, sleeps=sleeps).read(b"\x89PNG fake")
    assert [w.text for w in page.words] == ["SSN", "123-45-6789", "Ananya"]
    assert [w.line for w in page.words] == [0, 0, 1] and page.unit == "pixel"
    assert sleeps == [0.5, 0.5]
    assert all(r.headers["Ocp-Apim-Subscription-Key"] == "k-SECRET" for r in script.requests)
    assert script.requests[0].url.path == "/vision/v3.2/read/analyze"
    assert script.requests[0].headers["Content-Type"] == "application/octet-stream"


async def test_operation_location_must_stay_on_the_endpoint_host() -> None:
    script = Script(accepted("http://evil.example/op/1"))
    with pytest.raises(OcrError) as exc:
        await client(script).read(b"x")
    assert exc.value.code == "ocr_protocol" and len(script.requests) == 1


async def test_relative_operation_location_is_resolved() -> None:
    script = Script(
        accepted("/vision/v3.2/read/analyzeResults/op2"), httpx.Response(200, json=RESULT)
    )
    await client(script).read(b"x")
    assert str(script.requests[1].url) == f"{BASE}/vision/v3.2/read/analyzeResults/op2"


@pytest.mark.parametrize(
    "status,code", [(401, "ocr_auth"), (415, "ocr_rejected"), (500, "ocr_unavailable")]
)
async def test_status_errors_map_to_codes(status: int, code: str) -> None:
    with pytest.raises(OcrError) as exc:
        await client(Script(httpx.Response(status))).read(b"x")
    assert exc.value.code == code


async def test_failed_status_and_timeout() -> None:
    with pytest.raises(OcrError) as failed:
        await client(Script(accepted(), httpx.Response(200, json={"status": "failed"}))).read(b"x")
    assert failed.value.code == "ocr_failed"
    running = [httpx.Response(200, json={"status": "running"}) for _ in range(10)]
    with pytest.raises(OcrError) as slow:
        await client(Script(accepted(), *running), timeout=2.0).read(b"x")
    assert slow.value.code == "ocr_timeout"


async def test_throttling_honours_retry_after() -> None:
    script = Script(
        httpx.Response(429, headers={"Retry-After": "2"}),
        accepted(),
        httpx.Response(429, headers={"Retry-After": "3"}),
        httpx.Response(200, json=RESULT),
    )
    sleeps: list[float] = []
    await client(script, sleeps=sleeps).read(b"x")
    assert sleeps == [2.0, 0.5, 3.0]


async def test_errors_never_carry_key_or_body() -> None:
    script = Script(accepted(), httpx.Response(500, text="SECRET-BODY-4111 1111 1111 1111"))
    with pytest.raises(OcrError) as exc:
        await client(script, key="k-SECRET").read(b"x")
    assert "SECRET" not in str(exc.value) and "4111" not in str(exc.value)


async def test_null_client() -> None:
    null = NullOcrClient()
    assert not null.configured() and await null.probe() == (False, "unconfigured")
    with pytest.raises(OcrError) as exc:
        await null.read(b"x")
    assert exc.value.code == "ocr_unconfigured"


async def test_probe_reports_up_down_and_configured() -> None:
    assert await client(Script(httpx.Response(200))).probe() == (True, "up")
    assert await client(Script(httpx.Response(503))).probe() == (False, "down:503")
    assert await client(Script(), health="").probe() == (True, "configured")


def test_words_to_points() -> None:
    page = parse_read_result(RESULT)
    words = ocr_words_to_points(page, points_per_px=72 / 200)
    ssn = words[1]
    assert (round(ssn.bbox.x0, 2), round(ssn.bbox.y0, 2), round(ssn.bbox.x1, 2)) == (
        108.0,
        108.0,
        216.0,
    )
    assert ssn.source == "ocr" and ssn.line_hint == 0 and ssn.confidence == 0.97
    inch = OcrPage(
        width=8.5,
        height=11,
        unit="inch",
        angle=0,
        words=[OcrWord("x", (1, 1, 2, 1, 2, 1.5, 1, 1.5), None, 0)],
    )
    assert ocr_words_to_points(inch, points_per_px=0.1)[0].bbox.x1 == 144.0


def test_builder_picks_the_client() -> None:
    assert isinstance(build_ocr_client(Settings(_env_file=None)), NullOcrClient)
    on = build_ocr_client(
        Settings(
            _env_file=None, ocr_provider="azure", azure_vision_endpoint=BASE, azure_vision_key="k"
        )
    )
    assert isinstance(on, AzureReadClient) and on.configured()
    no_key = Settings(_env_file=None, ocr_provider="azure", azure_vision_endpoint=BASE)
    assert isinstance(build_ocr_client(no_key), NullOcrClient)


async def test_refresh_readiness() -> None:
    await refresh_ocr_readiness(NullOcrClient(), Settings(_env_file=None))
    assert READINESS.snapshot()["components"]["ocr"]["detail"] == "unconfigured"
    await refresh_ocr_readiness(
        client(Script(httpx.Response(200))), Settings(_env_file=None, ocr_provider="azure")
    )
    component = READINESS.snapshot()["components"]["ocr"]
    assert (
        component["ok"] is True
        and component["detail"] == "up"
        and component["engine"] == "azure-read-v3.2"
    )
```

`tests/documents/test_ocr_mock.py` (integration; the compose mock runs Tesseract — verified while writing this plan: a 200-dpi render of the KYC fixture comes back word for word):
```python
from __future__ import annotations

import pytest

pytestmark = [pytest.mark.docs, pytest.mark.ocr]

from dps.layers.documents.ocr import AzureReadClient  # noqa: E402
from dps.layers.documents.render import PdfRenderer, encode_png  # noqa: E402
from tests.documents.fixtures import text_pdf  # noqa: E402


async def test_mock_reads_a_rendered_page(ocr_url: str) -> None:
    client = AzureReadClient(endpoint=ocr_url, key="mock-key", timeout_s=60, poll_interval_s=0.2)
    try:
        assert await client.probe() == (True, "up")
        with PdfRenderer(text_pdf()) as renderer:
            png = encode_png(renderer.render(0, 200))
        page = await client.read(png)
    finally:
        await client.aclose()
    text = " ".join(w.text for w in page.words)
    assert page.unit == "pixel" and (page.width, page.height) == (1700.0, 2200.0)
    assert "123-45-6789" in text and "ananya.k@example-mail.in" in text
```

- [ ] **Step 2: Run to confirm failure** — `pytest -q tests/documents/test_ocr_client.py` → FAIL (`ModuleNotFoundError: dps.layers.documents.ocr`).

- [ ] **Step 3: Implement** `ocr.py` as above.

- [ ] **Step 4: Gates** — `pytest -q tests/documents/test_ocr_client.py` → `14 passed` (verified while writing). Then `docker compose --profile docs up -d azure-ocr-mock` (Task 19 adds the service; before Task 19 use `docker run -d --rm --name dps-ocr -p 127.0.0.1:5016:5000 document_intelligence-azure-ocr-mock:latest`) and `pytest -q -m ocr tests/documents/test_ocr_mock.py` → `1 passed`; stop the container. Full gates green.

- [ ] **Step 5: Commit**
```bash
git add dps/layers/documents/ocr.py tests/documents/test_ocr_client.py tests/documents/test_ocr_mock.py
git commit -m "feat(documents): async Read v3.2 OCR client with host-pinned polling, coded errors and readiness" -- dps/layers/documents/ocr.py tests/documents/test_ocr_client.py tests/documents/test_ocr_mock.py
```

---

### Task 8: True redaction — burned-in boxes, image-only rebuild, output inspection

**Files:**
- Create: `dps/layers/documents/redact.py`, `tests/documents/test_redact.py`

**Interfaces introduced:** `PageRedaction(index, width, height, boxes)`; `RedactedOutput(pdf, boxes)`; `OutputInspection(pages, text_layer_chars, text_operators, annotations, has_forms, has_outlines, has_attachments)` with `.clean`; `paint_boxes(image, boxes, *, width_pt, height_pt)`; `build_image_pdf(pages, *, image_format, jpeg_quality) -> bytes`; `redact_document(doc, plan, *, dpi, image_format, jpeg_quality) -> RedactedOutput`; `inspect_output_pdf(data) -> OutputInspection`.

**Behaviour:** every page of the source becomes one raster page at the same size in points; boxes are painted on the raster before encoding (JPEG blocks straddling a box edge cannot carry what was under it, because those pixels are already black); `invariant=1` makes the output byte-for-byte reproducible; metadata is emptied (Creator "Data Privacy Services"); forms and annotations are not drawn (Task 5); reportlab's empty `BT … ET` preamble is not a text layer, so the inspection counts text-*showing* operators. `pdftoppm` (poppler, present on this Mac) is an independent renderer used only as a test cross-check — DPS itself never shells out.

`dps/layers/documents/redact.py`:
```python
"""True redaction: rasterise, burn boxes in, rebuild an image-only PDF (no PyMuPDF).

Each normalised page is rendered with pypdfium2 at ``pdf_render_dpi`` (forms and annotations not
drawn), every box is painted solid black on the raster, and reportlab writes a new PDF whose pages
are just those images (JPEG passed through as DCTDecode, or PNG/Flate). Nothing from the source
survives: no text layer, fonts, annotations, forms, outlines, attachments or metadata. The
original values under the boxes are gone from the pixels, so no key brings them back.
"""

from __future__ import annotations

import io
import math
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass

from PIL import Image, ImageDraw
from pypdf import PdfReader
from reportlab import rl_config
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from dps.layers.documents.errors import DocumentError
from dps.layers.documents.models import BBox, ExtractedDocument
from dps.layers.documents.render import PdfRenderer

rl_config.useA85 = (
    0  # write image streams in binary (ASCII85 would add 25%); process-wide, set once
)
REDACT_FILL = (0, 0, 0)
#: A text-*showing* operator after a string or array operand. reportlab writes an empty
#: ``BT /F1 12 Tf 14.4 TL ET`` on every page, so ``BT`` alone proves nothing.
_SHOW_TEXT = re.compile(rb"[)\]>]\s*(?:Tj|TJ|'|\")")


@dataclass(frozen=True)
class PageRedaction:
    index: int  # 0-based page index
    width: float  # points
    height: float
    boxes: list[BBox]


@dataclass(frozen=True)
class RedactedOutput:
    pdf: bytes
    boxes: int


@dataclass(frozen=True)
class OutputInspection:
    pages: int
    text_layer_chars: int
    text_operators: int
    annotations: int
    has_forms: bool
    has_outlines: bool
    has_attachments: bool

    @property
    def clean(self) -> bool:
        """True when nothing but images can be in the file."""
        return (
            self.text_layer_chars == 0
            and self.text_operators == 0
            and self.annotations == 0
            and not (self.has_forms or self.has_outlines or self.has_attachments)
        )


def paint_boxes(
    image: Image.Image, boxes: Iterable[BBox], *, width_pt: float, height_pt: float
) -> None:
    """Fill every box (points) on ``image`` (pixels), rounding outward."""
    sx, sy = image.width / width_pt, image.height / height_pt
    draw = ImageDraw.Draw(image)
    for b in boxes:
        draw.rectangle(
            (
                math.floor(b.x0 * sx),
                math.floor(b.y0 * sy),
                math.ceil(b.x1 * sx),
                math.ceil(b.y1 * sy),
            ),
            fill=REDACT_FILL,
        )


def build_image_pdf(
    pages: Iterable[tuple[Image.Image, float, float]], *, image_format: str, jpeg_quality: int
) -> bytes:
    """An image-only PDF: one image per page at the given size in points; metadata emptied."""
    buf = io.BytesIO()
    pdf = canvas.Canvas(buf, invariant=1, pageCompression=1)
    pdf.setTitle("")
    pdf.setAuthor("")
    pdf.setSubject("")
    pdf.setKeywords("")
    pdf.setCreator("Data Privacy Services")
    for image, width, height in pages:
        encoded = io.BytesIO()
        if image_format == "jpeg":
            image.save(encoded, "JPEG", quality=jpeg_quality, optimize=True)
        else:
            image.save(encoded, "PNG")
        encoded.seek(0)
        pdf.setPageSize((width, height))
        pdf.drawImage(ImageReader(encoded), 0, 0, width=width, height=height)
        pdf.showPage()
    pdf.save()
    return buf.getvalue()


def redact_document(
    doc: ExtractedDocument,
    plan: list[PageRedaction],
    *,
    dpi: int,
    image_format: str,
    jpeg_quality: int,
) -> RedactedOutput:
    """Rasterise every page in ``plan`` (all pages, boxed or not), burn its boxes in, rebuild."""
    if doc.format == "pdf" and doc.pdf is not None:
        renderer = PdfRenderer(doc.pdf)

        def pdf_pages() -> Iterator[tuple[Image.Image, float, float]]:
            for page in plan:
                image = renderer.render(page.index, dpi)
                paint_boxes(image, page.boxes, width_pt=page.width, height_pt=page.height)
                yield image, page.width, page.height

        try:
            data = build_image_pdf(
                pdf_pages(), image_format=image_format, jpeg_quality=jpeg_quality
            )
        finally:
            renderer.close()
    elif doc.format == "image" and doc.image_png is not None:
        with Image.open(io.BytesIO(doc.image_png)) as opened:
            image = opened.convert("RGB")
        page = plan[0]
        paint_boxes(image, page.boxes, width_pt=page.width, height_pt=page.height)
        data = build_image_pdf(
            [(image, page.width, page.height)], image_format=image_format, jpeg_quality=jpeg_quality
        )
    else:
        raise DocumentError(
            "redaction_unsupported_format", "redacted PDFs are produced for PDF and image inputs"
        )
    return RedactedOutput(pdf=data, boxes=sum(len(p.boxes) for p in plan))


def inspect_output_pdf(data: bytes) -> OutputInspection:
    """Prove a produced PDF carries no text: pypdf extraction, text-showing operators, structure."""
    reader = PdfReader(io.BytesIO(data))
    chars = operators = annotations = 0
    for page in reader.pages:
        chars += len((page.extract_text() or "").strip())
        contents = page.get_contents()
        if contents is not None:
            operators += len(_SHOW_TEXT.findall(contents.get_data()))
        annots = page.get("/Annots")
        annotations += len(annots.get_object()) if annots is not None else 0
    root = reader.root_object
    return OutputInspection(
        pages=len(reader.pages),
        text_layer_chars=chars,
        text_operators=operators,
        annotations=annotations,
        has_forms="/AcroForm" in root,
        has_outlines="/Outlines" in root,
        has_attachments="/Names" in root,
    )
```

- [ ] **Step 1: Write the failing tests** — `tests/documents/test_redact.py`:
```python
from __future__ import annotations

import io
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.docs
pytest.importorskip("reportlab")

from PIL import Image  # noqa: E402
from pypdf import PdfReader  # noqa: E402

from dps.config import Settings  # noqa: E402
from dps.layers.documents.extract import extract_document  # noqa: E402
from dps.layers.documents.layout import span_boxes  # noqa: E402
from dps.layers.documents.models import ExtractedDocument  # noqa: E402
from dps.layers.documents.redact import PageRedaction, inspect_output_pdf, redact_document  # noqa: E402
from dps.layers.documents.render import PdfRenderer  # noqa: E402
from tests.documents.fixtures import annotated_pdf, png_document, text_pdf  # noqa: E402

SET = Settings(_env_file=None)


def _plan(doc: ExtractedDocument, needles: tuple[str, ...]) -> list[PageRedaction]:
    plan = []
    for p in doc.pages:
        boxes = []
        for needle in needles:
            at = p.text.find(needle)
            if at >= 0:
                boxes += span_boxes(p, at, at + len(needle))
        plan.append(PageRedaction(index=p.page - 1, width=p.width, height=p.height, boxes=boxes))
    return plan


def _ratio(image: Image.Image, box: tuple[int, int, int, int], *, dark: bool) -> float:
    crop = image.convert("L").crop(box)
    hist = crop.histogram()
    return sum(hist[:64] if dark else hist[:128]) / max(crop.width * crop.height, 1)


def _px(b, scale: float) -> tuple[int, int, int, int]:  # noqa: ANN001
    return (
        int(b.x0 * scale) + 1,
        int(b.y0 * scale) + 1,
        int(b.x1 * scale) - 1,
        int(b.y1 * scale) - 1,
    )


def _redact(doc: ExtractedDocument, plan: list[PageRedaction], fmt: str = "jpeg") -> bytes:
    return redact_document(doc, plan, dpi=200, image_format=fmt, jpeg_quality=85).pdf


def test_redacted_pdf_has_no_text_and_boxes_are_black() -> None:
    doc = extract_document(text_pdf(), "pdf", SET, "auto")
    plan = _plan(doc, ("Ananya Krishnan", "123-45-6789"))
    out = _redact(doc, plan)
    inspection = inspect_output_pdf(out)
    assert inspection.clean and inspection.pages == 1 and inspection.text_layer_chars == 0
    assert b"123-45-6789" not in out and b"Ananya" not in out
    with PdfRenderer(out) as r:
        image = r.render(0, 100)
    scale = 100 / 72
    for box in plan[0].boxes:
        assert _ratio(image, _px(box, scale), dark=True) > 0.98
    label = next(w for w in doc.pages[0].words if w.text == "SSN").bbox
    assert (
        0.02 < _ratio(image, _px(label, scale), dark=False) < 0.6
    )  # "SSN" still legible, not painted


def test_page_count_and_sizes_are_preserved() -> None:
    doc = extract_document(text_pdf(pages=3), "pdf", SET, "auto")
    reader = PdfReader(io.BytesIO(_redact(doc, _plan(doc, ("123-45-6789",)))))
    assert [(float(p.mediabox.width), float(p.mediabox.height)) for p in reader.pages] == [
        (612.0, 792.0)
    ] * 3


def test_metadata_annotations_and_forms_do_not_survive() -> None:
    doc = extract_document(annotated_pdf(), "pdf", SET, "auto")
    out = _redact(doc, _plan(doc, ("123-45-6789",)))
    inspection = inspect_output_pdf(out)
    assert inspection.clean and inspection.annotations == 0
    assert b"Ananya" not in out and b"123-45-6789" not in out
    assert (PdfReader(io.BytesIO(out)).metadata or {}).get("/Author") in (None, "")


@pytest.mark.parametrize("fmt,filter_name", [("jpeg", "/DCTDecode"), ("png", "/FlateDecode")])
def test_image_formats(fmt: str, filter_name: str) -> None:
    doc = extract_document(text_pdf(), "pdf", SET, "auto")
    page = PdfReader(io.BytesIO(_redact(doc, _plan(doc, ("123-45-6789",)), fmt))).pages[0]
    xobjects = page["/Resources"]["/XObject"]
    filters = {str(xobjects[k].get_object()["/Filter"]) for k in xobjects}
    assert filter_name in "".join(filters)


def test_image_documents_become_one_page_pdfs() -> None:
    doc = extract_document(png_document(dpi=200), "image", SET, "auto")
    page = doc.pages[0]
    from dps.layers.documents.models import BBox

    plan = [
        PageRedaction(
            index=0,
            width=page.width,
            height=page.height,
            boxes=[BBox(x0=72, y0=100, x1=300, y1=120)],
        )
    ]
    reader = PdfReader(io.BytesIO(_redact(doc, plan)))
    assert len(reader.pages) == 1 and float(reader.pages[0].mediabox.width) == pytest.approx(
        page.width, abs=0.5
    )


def test_output_is_deterministic() -> None:
    doc = extract_document(text_pdf(), "pdf", SET, "auto")
    plan = _plan(doc, ("123-45-6789",))
    assert _redact(doc, plan) == _redact(doc, plan)


@pytest.mark.skipif(shutil.which("pdftoppm") is None, reason="poppler pdftoppm not installed")
def test_pdftoppm_cross_check(tmp_path: Path) -> None:
    doc = extract_document(text_pdf(), "pdf", SET, "auto")
    plan = _plan(doc, ("123-45-6789",))
    (tmp_path / "out.pdf").write_bytes(_redact(doc, plan))
    subprocess.run(
        [
            "pdftoppm",
            "-r",
            "72",
            "-png",
            "-singlefile",
            str(tmp_path / "out.pdf"),
            str(tmp_path / "page"),
        ],
        check=True,
        capture_output=True,
        timeout=60,
    )
    with Image.open(tmp_path / "page.png") as image:
        assert _ratio(image, _px(plan[0].boxes[0], 1.0), dark=True) > 0.95
```

- [ ] **Step 2: Run to confirm failure** — `pytest -q tests/documents/test_redact.py` → FAIL (`ModuleNotFoundError: dps.layers.documents.redact`).

- [ ] **Step 3: Implement** `redact.py` as above.

- [ ] **Step 4: Gates** — `pytest -q tests/documents/test_redact.py` → `8 passed` (the pdftoppm cross-check skips where poppler is absent; verified while writing). Full gates green.

- [ ] **Step 5: Commit**
```bash
git add dps/layers/documents/redact.py tests/documents/test_redact.py
git commit -m "feat(documents): true redaction — burned-in boxes, image-only PDF rebuild, output inspection" -- dps/layers/documents/redact.py tests/documents/test_redact.py
```

---
### Task 9: The DocumentPipeline — extract, OCR, engine, locate, redact, verify, previews

**Files:**
- Modify: `dps/layers/documents/__init__.py` (replace the placeholder docstring with the façade)
- Create: `tests/documents/test_pipeline.py`

**Interfaces introduced:** `DocumentPipeline(engine, settings, ocr)` with `async run(*, mode, content, filename, content_type, request) -> DocumentOutcome`; `DocumentOutcome(mode, detect, transform, document, pages, redacted_pdf, result_csv)` with `.report`; `locate_findings(doc, findings, *, plan_rows, segments, mode) -> list[PageSummary]`; `records_to_csv(rows, header, delimiter)`; `synthetic_validity(pages)`; constants `REVIEW_OCR_CONFIDENCE = 0.8`, `RULE_VALID_SURROGATES`, `MIN_NEEDLE_CHARS = 4`.

**Behaviour (each line is tested below):**
1. Size (`document_too_large`, 413) → sniff → option checks: `redacted_pdf` on detect → `redacted_pdf_not_allowed`; on DOCX/TXT/CSV → `redaction_unsupported_format`; an image with `ocr=never` → `ocr_required`.
2. Extraction runs in a worker thread. Pages with `needs_ocr` are rendered at `pdf_render_dpi` (images are sent as normalised PNG) and read concurrently (≤ `ocr_max_concurrency` per request; the client also bounds the process). OCR words become points; a page that also has a text layer becomes `hybrid` (text-layer words win, OCR adds what they do not cover). Without OCR configured: images, `ocr=always` and any redaction request → 503 `ocr_unconfigured`; otherwise the page stays `unreadable`, contributes no text, and the response warns `ocr_unconfigured`. Any OCR failure → 503 `ocr_unavailable` (all pages finish before the renderer closes).
3. The engine sees **one canonical text** (`PAGE_BREAK`-joined pages) — or the CSV records — with `return_segments=True` and `llm_rewrite=False` (a document's layout must survive; asking for a rewrite adds the warning `llm_rewrite_not_applied_to_documents`). The canonical text is capped by `max_text_chars` (413 `text_too_long`). The engine's own leakage sweep and fail-closed rules apply unchanged.
4. `locate_findings` numbers findings in document order, maps each span to every page it touches, to lines and to boxes (`span_boxes`), carries the plan row (method, reversible), the replacement and its output span from the segments, the lowest OCR confidence of the words holding it, and `review` below 0.8. `redacted` = transform mode and a method other than `keep`.
5. Redaction refuses (`redaction_needs_ocr`) when any page looked scanned but was not OCR'd, and (`redaction_unlocated`) when a transformed finding has no box; renders and paints every page; then **verifies**: `inspect_output_pdf` must be clean with the same page count (else 500 `redaction_verify_failed`); when OCR is configured and `ocr_verify` (default `redact_ocr_verify`) holds, every boxed page of the *produced file* is re-rendered and OCR'd, and any redacted original still readable (normalised, token-bounded, ≥ 4 characters) fails the request with 422 `leakage` — nothing is returned. Without OCR the report says `ocr_checked: false` and the warning `redaction_not_ocr_verified` is added.
6. Previews (`options.previews`): source pages at `preview_dpi` (≤ `max_preview_pages`), and redacted pages rendered from the produced PDF. CSV output is re-serialised with the source header and delimiter. Synthesize reports how many rule-valid surrogates (cards, Aadhaar, SIN, IBAN, ABA, PAN) pass their checksums.
7. Logs carry counts only (`document_processed` with mode, format, pages, ocr_pages, boxes, duration_ms); metrics `dps_document_pages_total{format,source}` and `dps_redacted_boxes_total`.

`dps/layers/documents/__init__.py`:
```python
"""Layer 1 documents (spec §6): the ``DocumentPipeline`` façade.

extract → OCR (pages that need it) → the unchanged M0 engine over one canonical text (pages
joined by ``PAGE_BREAK``; CSV as records) → findings and output segments mapped back to pages,
lines and boxes → optional true redaction, verified twice (structure, OCR re-read) → optional
previews. Nothing persists: the upload, rasters and texts live for the request only.
"""

from __future__ import annotations

import asyncio
import contextlib
import csv
import hashlib
import io
import re
import time
from collections.abc import Iterator
from typing import Any, Literal

from PIL import Image
from pydantic import BaseModel, ConfigDict

from dps.config import Settings
from dps.engine.detect.checksums import validate
from dps.engine.findings import Finding
from dps.engine.report import Report
from dps.engine.service import (
    DetectResponse,
    Payload,
    PrivacyEngine,
    RequestOptions,
    TransformResponse,
)
from dps.engine.transform.apply import OutputSegment
from dps.engine.verify import normalise
from dps.layers.documents.errors import DocumentError
from dps.layers.documents.extract import extract_document, sniff_format
from dps.layers.documents.layout import (
    build_page_text,
    group_lines,
    line_of_offset,
    merge_ocr_words,
    span_boxes,
    words_in_span,
)
from dps.layers.documents.models import (
    BBox,
    DocumentInfo,
    DocumentOptions,
    DocumentRequest,
    ExtractedDocument,
    PageBox,
    PageFinding,
    PageSummary,
    PageText,
    RedactionReport,
)
from dps.layers.documents.ocr import OcrClient, OcrError, OcrPage, ocr_words_to_points
from dps.layers.documents.redact import PageRedaction, inspect_output_pdf, redact_document
from dps.layers.documents.render import PdfRenderer, b64, encode_png, preview_png
from dps.observability import DOCUMENT_PAGES, REDACTED_BOXES, get_logger

logger = get_logger(__name__)
Mode = Literal["detect", "obfuscate", "synthesize"]
REVIEW_OCR_CONFIDENCE = 0.8
#: Surrogate types the synthesis engine generates rule-valid (checksums pass by design).
RULE_VALID_SURROGATES = frozenset({"CARD_PAN", "AADHAAR", "SIN", "IBAN", "ROUTING_ABA", "PAN_IN"})
MIN_NEEDLE_CHARS = 4
_CSV_ROW = re.compile(r"^\$\[(\d+)\]")


class DocumentOutcome(BaseModel):
    """What the pipeline hands the router (the redacted PDF stays bytes until it is encoded)."""

    model_config = ConfigDict(arbitrary_types_allowed=True)
    mode: str
    detect: DetectResponse | None = None
    transform: TransformResponse | None = None
    document: DocumentInfo
    pages: list[PageSummary]
    redacted_pdf: bytes | None = None
    result_csv: str | None = None

    @property
    def report(self) -> Report:
        """The engine report (``run_engine`` reads it for the audit event)."""
        source = self.detect if self.detect is not None else self.transform
        assert source is not None
        return source.report


@contextlib.contextmanager
def _timed(timings: dict[str, float], name: str) -> Iterator[None]:
    started = time.perf_counter()
    try:
        yield
    finally:
        timings[name] = round(timings.get(name, 0.0) + (time.perf_counter() - started) * 1000, 1)


def _page_list(pages: list[int]) -> str:
    shown = ", ".join(str(p) for p in pages[:10])
    return shown + (", …" if len(pages) > 10 else "")


def locate_findings(
    doc: ExtractedDocument,
    findings: list[Finding],
    *,
    plan_rows: list[dict[str, Any]],
    segments: list[OutputSegment],
    mode: str,
) -> list[PageSummary]:
    """Map engine findings (and their plan rows and output segments) onto pages, lines and boxes.

    Findings are numbered in document order; one spanning two pages appears on both with the
    same ``n``. ``redacted`` marks findings whose method transforms the value (not ``keep``).
    """
    rows = {(r["path"], tuple(r["span"]) if r.get("span") else None): r for r in plan_rows}
    segs = {(s.path, s.start, s.end): s for s in segments}
    summaries = [
        PageSummary(
            page=p.page,
            width=p.width,
            height=p.height,
            source=p.source,
            words=len(p.words),
            lines=(p.word_lines[-1] if p.word_lines else (p.text.count("\n") + 1 if p.text else 0)),
            ocr_confidence=p.ocr_confidence,
        )
        for p in doc.pages
    ]
    text = "" if doc.format == "csv" else doc.text()
    starts = doc.page_starts()
    ordered = sorted(
        findings, key=lambda f: (f.locator.path, -1 if f.locator.start is None else f.locator.start)
    )
    for n, f in enumerate(ordered, start=1):
        loc = f.locator
        row = rows.get((loc.path, None if loc.whole else (loc.start, loc.end)))
        seg = segs.get((loc.path, loc.start, loc.end))
        method = str(row["method"]) if row else None
        common: dict[str, Any] = {
            "n": n,
            "entity_type": f.entity_type,
            "score": f.score,
            "sensitivity": f.sensitivity,
            "detectors": f.detectors,
            "evidence": f.evidence,
            "value_hash": f.value_hash,
            "path": loc.path,
            "method": method,
            "reversible": bool(row["reversible"]) if row else None,
            "redacted": mode != "detect" and method not in (None, "keep"),
            "replacement": seg.replacement if seg is not None and method != "keep" else None,
        }
        if doc.format == "csv":
            match = _CSV_ROW.match(loc.path)
            summaries[0].findings.append(
                PageFinding(
                    **common,
                    page=1,
                    line=int(match.group(1)) + 2 if match else None,
                    span=None if loc.whole else [loc.start or 0, loc.end or 0],
                )
            )
            continue
        start, end = (0, len(text)) if loc.whole else (loc.start or 0, loc.end or 0)
        out_span: list[int] | None = None
        if seg is not None and method != "keep":
            out_span = (
                [seg.out_start, seg.out_end]
                if seg.out_start is not None and seg.out_end is not None
                else [0, len(seg.replacement)]
            )
        for page, page_start in zip(doc.pages, starts, strict=True):
            page_end = page_start + len(page.text)
            if end <= page_start or start >= page_end:
                continue
            local_start, local_end = (
                max(start, page_start) - page_start,
                min(end, page_end) - page_start,
            )
            covered = [w for _, w in words_in_span(page, local_start, local_end)]
            confidences = [
                w.confidence for w in covered if w.source == "ocr" and w.confidence is not None
            ]
            confidence = round(min(confidences), 3) if confidences else None
            boxes = span_boxes(page, local_start, local_end) if page.words else []
            summaries[page.page - 1].findings.append(
                PageFinding(
                    **common,
                    page=page.page,
                    line=line_of_offset(page, local_start),
                    span=[start, end],
                    boxes=[
                        PageBox(page=page.page, x0=b.x0, y0=b.y0, x1=b.x1, y1=b.y1) for b in boxes
                    ],
                    out_span=out_span,
                    ocr_confidence=confidence,
                    review=confidence is not None and confidence < REVIEW_OCR_CONFIDENCE,
                )
            )
    return summaries


def records_to_csv(rows: list[dict[str, Any]], header: list[str], delimiter: str) -> str:
    """Re-serialise transformed CSV records with the source header and delimiter."""
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=delimiter, lineterminator="\n")
    writer.writerow(header)
    for row in rows:
        writer.writerow(["" if row.get(h) is None else str(row.get(h)) for h in header])
    return buf.getvalue()


def synthetic_validity(pages: list[PageSummary]) -> dict[str, int]:
    """How many surrogates of rule-valid types pass their checksum (should be all)."""
    seen: set[int] = set()
    checked = valid = 0
    for summary in pages:
        for f in summary.findings:
            if f.n in seen or f.replacement is None or f.entity_type not in RULE_VALID_SURROGATES:
                continue
            seen.add(f.n)
            checked += 1
            valid += validate(f.entity_type, f.replacement) is True
    return {"checked": checked, "valid": valid}


def _needles(summary: PageSummary, text: str) -> set[str]:
    """Normalised originals of a page's redacted findings, token by token (≥ 4 characters)."""
    out: set[str] = set()
    for f in summary.findings:
        if f.redacted and f.span:
            tokens = (normalise(t) for t in text[f.span[0] : f.span[1]].split())
            needle = " ".join(t for t in tokens if t)
            if len(needle.replace(" ", "")) >= MIN_NEEDLE_CHARS:
                out.add(needle)
    return out


class DocumentPipeline:
    """Documents in, findings / transformed text / redacted PDF out (contract §1, spec §6)."""

    def __init__(self, engine: PrivacyEngine, settings: Settings, ocr: OcrClient) -> None:
        self.engine = engine
        self.settings = settings
        self.ocr = ocr

    async def run(
        self,
        *,
        mode: Mode,
        content: bytes,
        filename: str | None,
        content_type: str | None,
        request: DocumentRequest,
    ) -> DocumentOutcome:
        """Process one upload. Raises :class:`DocumentError` (coded) or engine errors."""
        timings: dict[str, float] = {}
        started = time.perf_counter()
        options = request.options
        if len(content) > self.settings.max_document_bytes:
            raise DocumentError(
                "document_too_large",
                f"documents are limited to {self.settings.max_document_mb} MB",
                status=413,
            )
        fmt = sniff_format(content, filename, content_type)
        self._check_options(fmt, mode, options)
        with _timed(timings, "extract"):
            doc = await asyncio.to_thread(
                extract_document, content, fmt, self.settings, options.ocr
            )
        with _timed(timings, "ocr"):
            await self._ocr_pages(doc, options)
        payload = self._payload(doc)
        engine_options = self._engine_options(options, doc)
        detect: DetectResponse | None = None
        transform: TransformResponse | None = None
        with _timed(timings, "engine"):
            if mode == "detect":
                detect = await asyncio.to_thread(
                    self.engine.detect, payload, request.policy, engine_options
                )
            elif mode == "obfuscate":
                transform = await asyncio.to_thread(
                    self.engine.obfuscate, payload, request.policy, engine_options
                )
            else:
                transform = await self.engine.synthesize(payload, request.policy, engine_options)
        findings = (
            detect.findings if detect is not None else transform.findings if transform else []
        )
        pages = locate_findings(
            doc,
            findings,
            plan_rows=transform.plan if transform else [],
            segments=(transform.segments or []) if transform else [],
            mode=mode,
        )
        redacted: bytes | None = None
        redaction: RedactionReport | None = None
        if options.redacted_pdf:
            with _timed(timings, "redact"):
                redacted, redaction = await self._redact(
                    doc, pages, payload.text or "", options, timings
                )
        if options.previews:
            with _timed(timings, "preview"):
                await asyncio.to_thread(self._attach_previews, doc, pages, redacted)
        result_csv = None
        if transform is not None and doc.format == "csv":
            result_csv = records_to_csv(transform.result, doc.csv_header or [], doc.csv_delimiter)
        timings["total"] = round((time.perf_counter() - started) * 1000, 1)
        info = DocumentInfo(
            format=doc.format,
            pages=len(doc.pages),
            ocr_pages=[p.page for p in doc.pages if p.ocr_done],
            unreadable_pages=[p.page for p in doc.pages if p.source == "unreadable"],
            words=sum(len(p.words) for p in doc.pages),
            boxes=sum(
                len(f.boxes) for s in pages for f in s.findings if mode == "detect" or f.redacted
            ),
            ocr_engine=self.ocr.engine if any(p.ocr_done for p in doc.pages) else None,
            bytes=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
            warnings=sorted(set(doc.warnings)),
            timings_ms=timings,
            redaction=redaction,
            synthetic_validity=synthetic_validity(pages) if mode == "synthesize" else None,
        )
        for page in doc.pages:
            DOCUMENT_PAGES.labels(doc.format, page.source).inc()
        logger.info(
            "document_processed",
            mode=mode,
            format=doc.format,
            pages=info.pages,
            ocr_pages=len(info.ocr_pages),
            boxes=info.boxes,
            duration_ms=timings["total"],
        )
        return DocumentOutcome(
            mode=mode,
            detect=detect,
            transform=transform,
            document=info,
            pages=pages,
            redacted_pdf=redacted,
            result_csv=result_csv,
        )

    # -- stages ----------------------------------------------------------------------------------
    @staticmethod
    def _check_options(fmt: str, mode: str, options: DocumentOptions) -> None:
        if options.redacted_pdf and mode == "detect":
            raise DocumentError(
                "redacted_pdf_not_allowed",
                "a redacted PDF needs /documents/obfuscate or /documents/synthesize",
            )
        if options.redacted_pdf and fmt not in ("pdf", "image"):
            raise DocumentError(
                "redaction_unsupported_format",
                "redacted PDFs are produced for PDF and image inputs",
            )
        if fmt == "image" and options.ocr == "never":
            raise DocumentError(
                "ocr_required", "images are read with OCR; ocr=never cannot process them"
            )

    def _payload(self, doc: ExtractedDocument) -> Payload:
        if doc.format == "csv":
            return Payload(data=doc.records or [])
        text = doc.text()
        if len(text) > self.settings.max_text_chars:
            raise DocumentError(
                "text_too_long",
                f"the document text exceeds {self.settings.max_text_chars} characters",
                status=413,
            )
        return Payload(text=text)

    @staticmethod
    def _engine_options(options: DocumentOptions, doc: ExtractedDocument) -> RequestOptions:
        if options.llm_rewrite:
            doc.warnings.append("llm_rewrite_not_applied_to_documents")
        base = options.model_dump(include=set(RequestOptions.model_fields))
        base.update(return_segments=True, llm_rewrite=False)
        return RequestOptions.model_validate(base)

    @staticmethod
    def _page_png(
        doc: ExtractedDocument, renderer: PdfRenderer | None, index: int, dpi: int
    ) -> tuple[bytes, float]:
        if renderer is not None:
            return encode_png(renderer.render(index, dpi)), float(dpi)
        assert doc.image_png is not None and doc.image_dpi is not None
        return doc.image_png, doc.image_dpi

    async def _ocr_pages(self, doc: ExtractedDocument, options: DocumentOptions) -> None:
        targets = [p for p in doc.pages if p.needs_ocr]
        if not targets:
            return
        if not self.ocr.configured():
            if doc.format == "image" or options.ocr == "always" or options.redacted_pdf:
                raise DocumentError(
                    "ocr_unconfigured",
                    f"{len(targets)} page(s) need OCR and no OCR provider is configured",
                    status=503,
                )
            doc.warnings.append("ocr_unconfigured")
            return
        dpi = self.settings.pdf_render_dpi
        renderer = PdfRenderer(doc.pdf) if doc.format == "pdf" and doc.pdf is not None else None
        gate = asyncio.Semaphore(self.settings.ocr_max_concurrency)

        async def one(page: PageText) -> tuple[PageText, OcrPage, float]:
            async with gate:
                png, px_dpi = await asyncio.to_thread(
                    self._page_png, doc, renderer, page.page - 1, dpi
                )
                return page, await self.ocr.read(png), px_dpi

        try:
            # return_exceptions: every page finishes before the renderer closes
            results = await asyncio.gather(*(one(p) for p in targets), return_exceptions=True)
        finally:
            if renderer is not None:
                renderer.close()
        failures = [r for r in results if isinstance(r, BaseException)]
        if failures:
            first = failures[0]
            if isinstance(first, OcrError):
                raise DocumentError(
                    "ocr_unavailable",
                    f"OCR failed for {len(failures)} of {len(targets)} page(s) ({first.code})",
                    status=503,
                ) from None
            raise first
        for item in results:
            assert not isinstance(item, BaseException)
            page, result, px_dpi = item
            words = ocr_words_to_points(result, points_per_px=72.0 / px_dpi)
            if page.words:
                build_page_text(page, group_lines(merge_ocr_words(page.words, words)))
                page.source = "hybrid"
            else:
                build_page_text(page, group_lines(words))
                page.source = "ocr"
            confidences = [
                w.confidence for w in page.words if w.source == "ocr" and w.confidence is not None
            ]
            page.ocr_confidence = (
                round(sum(confidences) / len(confidences), 3) if confidences else None
            )
            page.ocr_done = True

    async def _redact(
        self,
        doc: ExtractedDocument,
        pages: list[PageSummary],
        text: str,
        options: DocumentOptions,
        timings: dict[str, float],
    ) -> tuple[bytes, RedactionReport]:
        suspects = [p.page for p in doc.pages if p.scan_suspect and not p.ocr_done]
        if suspects:
            raise DocumentError(
                "redaction_needs_ocr",
                f"page(s) {_page_list(suspects)} look scanned and were not read with OCR; "
                "a redacted PDF would expose them",
            )
        plan = [
            PageRedaction(
                index=p.page - 1,
                width=p.width,
                height=p.height,
                boxes=[
                    BBox(x0=b.x0, y0=b.y0, x1=b.x1, y1=b.y1)
                    for f in s.findings
                    if f.redacted
                    for b in f.boxes
                ],
            )
            for p, s in zip(doc.pages, pages, strict=True)
        ]
        unlocated = len({f.n for s in pages for f in s.findings if f.redacted and not f.boxes})
        if unlocated:
            raise DocumentError(
                "redaction_unlocated", f"{unlocated} finding(s) have no position on the page"
            )
        s = self.settings
        output = await asyncio.to_thread(
            redact_document,
            doc,
            plan,
            dpi=s.pdf_render_dpi,
            image_format=s.redact_image_format,
            jpeg_quality=s.redact_jpeg_quality,
        )
        checked, ocr_pages, hits, under = False, 0, 0, 0
        with _timed(timings, "verify"):
            inspection = await asyncio.to_thread(inspect_output_pdf, output.pdf)
            if not inspection.clean or inspection.pages != len(doc.pages):
                raise DocumentError(
                    "redaction_verify_failed",
                    "the produced PDF failed its structural check",
                    status=500,
                )
            verify = options.ocr_verify if options.ocr_verify is not None else s.redact_ocr_verify
            if verify and self.ocr.configured():
                checked = True
                ocr_pages, hits, under = await self._ocr_reread(output.pdf, plan, pages, text)
                if hits:
                    raise DocumentError(
                        "leakage",
                        f"an OCR re-read of the redacted PDF found {hits} original "
                        "value(s); nothing was returned",
                    )
            elif verify:
                doc.warnings.append("redaction_not_ocr_verified")
        REDACTED_BOXES.inc(output.boxes)
        return output.pdf, RedactionReport(
            boxes=output.boxes,
            pages=len(plan),
            dpi=s.pdf_render_dpi,
            image_format=s.redact_image_format,
            bytes=len(output.pdf),
            sha256=hashlib.sha256(output.pdf).hexdigest(),
            text_layer_chars=inspection.text_layer_chars,
            text_operators=inspection.text_operators,
            annotations=inspection.annotations,
            ocr_checked=checked,
            ocr_pages=ocr_pages,
            ocr_hits=hits,
            chars_under_boxes=under,
            passed=True,
        )

    async def _ocr_reread(
        self, pdf: bytes, plan: list[PageRedaction], pages: list[PageSummary], text: str
    ) -> tuple[int, int, int]:
        """OCR every boxed page of the produced PDF: originals still readable (hits) and characters
        read inside painted boxes."""
        dpi = self.settings.pdf_render_dpi
        targets = [r for r in plan if r.boxes]
        renderer = PdfRenderer(pdf)
        gate = asyncio.Semaphore(self.settings.ocr_max_concurrency)

        async def one(r: PageRedaction) -> tuple[PageRedaction, OcrPage]:
            async with gate:
                png = await asyncio.to_thread(lambda: encode_png(renderer.render(r.index, dpi)))
                return r, await self.ocr.read(png)

        try:
            results = await asyncio.gather(*(one(r) for r in targets), return_exceptions=True)
        finally:
            renderer.close()
        hits = under = 0
        for item in results:
            if isinstance(item, OcrError):
                raise DocumentError(
                    "ocr_unavailable",
                    f"the OCR re-read of the redacted PDF failed ({item.code})",
                    status=503,
                )
            if isinstance(item, BaseException):
                raise item
            r, result = item
            words = ocr_words_to_points(result, points_per_px=72.0 / dpi)
            haystack = " " + " ".join(normalise(w.text) for w in words) + " "
            hits += sum(1 for needle in _needles(pages[r.index], text) if f" {needle} " in haystack)
            under += sum(
                len(w.text)
                for w in words
                if any(
                    w.bbox.intersection_area(b) >= 0.5 * max(w.bbox.area(), 1e-6) for b in r.boxes
                )
            )
        return len(targets), hits, under

    def _attach_previews(
        self, doc: ExtractedDocument, pages: list[PageSummary], redacted: bytes | None
    ) -> None:
        limit, dpi = self.settings.max_preview_pages, self.settings.preview_dpi
        if doc.format == "pdf" and doc.pdf is not None:
            with PdfRenderer(doc.pdf) as renderer:
                for summary in pages[:limit]:
                    summary.preview_png_b64 = b64(
                        encode_png(renderer.render(summary.page - 1, dpi))
                    )
        elif doc.format == "image" and doc.image_png is not None and doc.image_dpi:
            with Image.open(io.BytesIO(doc.image_png)) as opened:
                pages[0].preview_png_b64 = b64(
                    preview_png(opened.convert("RGB"), source_dpi=doc.image_dpi, target_dpi=dpi)
                )
        if redacted is not None:
            with PdfRenderer(redacted) as renderer:
                for summary in pages[:limit]:
                    summary.redacted_preview_png_b64 = b64(
                        encode_png(renderer.render(summary.page - 1, dpi))
                    )
```

- [ ] **Step 1: Write the failing tests** — `tests/documents/test_pipeline.py` (uses the real M0 engine with FPE and NER off; FakeOcr answers with the text-layer words of the fixture's text twin, in pixels):
```python
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
from dps.layers.documents.ocr import NullOcrClient, OcrClient, OcrError  # noqa: E402
from dps.layers.documents.redact import inspect_output_pdf  # noqa: E402
from dps.llm.gateway import LlmGateway  # noqa: E402
from tests.documents.fixtures import (  # noqa: E402
    CUSTOMERS,
    FakeOcr,
    csv_document,
    docx_document,
    hybrid_pdf,
    png_document,
    scanned_pdf,
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
```

- [ ] **Step 2: Run to confirm failure** — `pytest -q tests/documents/test_pipeline.py` → FAIL (`ImportError: cannot import name 'DocumentPipeline'`).

- [ ] **Step 3: Implement** the façade as above.

- [ ] **Step 4: Gates** — `pytest -q tests/documents && ruff check dps tests tools && ruff format --check dps tests tools && mypy dps && pytest -q` → `test_pipeline.py` `16 passed`. (While writing this plan the module was run against a stand-in engine with the M0 interfaces — all 16 passed except the two reveal assertions, which need the real engine.) If `test_text_pdf_detect_maps_findings_to_boxes` misses `CARD_PAN`, check M0's card regex against `"Card 4111 1111 1111 1111 expires 09/28"`; if `test_csv_round_trip` reveals a different value type, check that `$[i].risk_score` stays a string end to end (CSV cells are strings).

- [ ] **Step 5: Commit**
```bash
git add dps/layers/documents/__init__.py tests/documents/test_pipeline.py
git commit -m "feat(documents): DocumentPipeline — OCR stage, findings to pages and boxes, verified redaction, previews" -- dps/layers/documents/__init__.py tests/documents/test_pipeline.py
```

---
### Task 10: Documents API, app wiring, OCR readiness loop

**Files:**
- Create: `dps/layers/api/routers/documents.py`, `tests/test_api_documents.py`, `tests/test_api_documents_ocr.py`
- Modify: `dps/app.py`

**HTTP routes (contract §9, M1):**

| Route | Scope | Request | 200 response |
|---|---|---|---|
| `POST /api/v1/documents/detect` | `detect` | `multipart/form-data`: `file` (PDF/DOCX/PNG/JPEG/TXT/CSV), optional `request` = JSON `DocumentRequest` | `DocumentDetectOut` = M0 `DetectOut` (`findings`, `record_sensitivity`, `report`, `trace_id`) + `document: DocumentInfo` + `pages: [PageSummary]` |
| `POST /api/v1/documents/obfuscate` | `obfuscate` | same; `options.redacted_pdf`, `options.ocr`, `options.previews`, `options.ocr_verify` + every M0 option (`consistency.key_material`, `wrap_key`, `token_style`, `locale`) | `DocumentTransformOut` = M0 `TransformOut` (`result` — the canonical text, or CSV records —, `findings`, `plan`, `key_material`, `reveal_hints`, `synthesis_seed`, `segments`, `report`, `trace_id`) + `document` + `pages` + `redacted_pdf_b64?` + `result_csv?` |
| `POST /api/v1/documents/synthesize` | `synthesize` | same | `DocumentTransformOut` (`synthesis_seed` echoed unless scope `request`) |

Errors are `{"detail", "code"}` (Task 2): 401/403 auth · 413 `payload_too_large` (middleware, by `Content-Length`) / `document_too_large` / `too_many_pages` / `image_too_large` / `text_too_long` / `too_many_values` · 415 `unsupported_format` · 422 `invalid_request` (validation message without inputs) / `empty_document` / `corrupt_document` / `encrypted_pdf` / `unsupported_encoding` / `csv_ragged_rows` / `redacted_pdf_not_allowed` / `redaction_unsupported_format` / `ocr_required` / `redaction_needs_ocr` / `redaction_unlocated` / `leakage` · 400 `bad_request` (policy, key material) · 500 `redaction_verify_failed` · 503 `ocr_unconfigured` / `ocr_unavailable`.

`dps/layers/api/routers/documents.py`:
```python
"""POST /api/v1/documents/{detect,obfuscate,synthesize} — multipart ``file`` + JSON form field ``request``."""

from __future__ import annotations

import base64
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from pydantic import ValidationError

from dps.auth import require_scope
from dps.layers.api.common import CodedHTTPException, policy_label, run_engine
from dps.layers.api.schemas import DetectOut, TransformOut
from dps.layers.documents import DocumentOutcome, DocumentPipeline
from dps.layers.documents.models import (
    DocumentInfo,
    DocumentRequest,
    PageSummary,
    validation_message,
)
from dps.ratelimit import enforce_rate_limit

router = APIRouter(prefix="/api/v1/documents", tags=["documents"])
READ_CHUNK = 1 << 20


class DocumentDetectOut(DetectOut):
    document: DocumentInfo
    pages: list[PageSummary]


class DocumentTransformOut(TransformOut):
    document: DocumentInfo
    pages: list[PageSummary]
    redacted_pdf_b64: str | None = None
    result_csv: str | None = None


def _parse(raw: str | None) -> DocumentRequest:
    if not raw:
        return DocumentRequest()
    try:
        return DocumentRequest.model_validate_json(raw)
    except ValidationError as exc:
        raise CodedHTTPException(422, "invalid_request", validation_message(exc)) from None


async def _read(file: UploadFile, limit: int) -> bytes:
    """Read the upload in chunks; a chunked upload without Content-Length is bounded here."""
    data = bytearray()
    while chunk := await file.read(READ_CHUNK):
        data += chunk
        if len(data) > limit:
            raise CodedHTTPException(
                413, "document_too_large", f"documents are limited to {limit} bytes"
            )
    return bytes(data)


async def _run(
    request: Request,
    mode: Literal["detect", "obfuscate", "synthesize"],
    file: UploadFile,
    raw: str | None,
) -> tuple[DocumentOutcome, str]:
    settings = request.app.state.settings
    pipeline: DocumentPipeline = request.app.state.documents
    body = _parse(raw)
    content = await _read(file, settings.max_document_bytes)
    return await run_engine(
        request,
        mode=f"document.{mode}",
        policy_name=policy_label(body.policy, settings.default_policy),
        call=lambda: pipeline.run(
            mode=mode,
            content=content,
            filename=file.filename,
            content_type=file.content_type,
            request=body,
        ),
    )


def _transform_out(outcome: DocumentOutcome, trace_id: str) -> DocumentTransformOut:
    assert outcome.transform is not None
    pdf = outcome.redacted_pdf
    return DocumentTransformOut(
        **outcome.transform.model_dump(),
        trace_id=trace_id,
        document=outcome.document,
        pages=outcome.pages,
        result_csv=outcome.result_csv,
        redacted_pdf_b64=base64.b64encode(pdf).decode("ascii") if pdf else None,
    )


@router.post(
    "/detect",
    response_model=DocumentDetectOut,
    dependencies=[Depends(require_scope("detect")), Depends(enforce_rate_limit)],
)
async def documents_detect(
    request: Request,
    file: Annotated[UploadFile, File()],
    request_json: Annotated[str | None, Form(alias="request")] = None,
) -> DocumentDetectOut:
    outcome, trace_id = await _run(request, "detect", file, request_json)
    assert outcome.detect is not None
    return DocumentDetectOut(
        **outcome.detect.model_dump(),
        trace_id=trace_id,
        document=outcome.document,
        pages=outcome.pages,
    )


@router.post(
    "/obfuscate",
    response_model=DocumentTransformOut,
    dependencies=[Depends(require_scope("obfuscate")), Depends(enforce_rate_limit)],
)
async def documents_obfuscate(
    request: Request,
    file: Annotated[UploadFile, File()],
    request_json: Annotated[str | None, Form(alias="request")] = None,
) -> DocumentTransformOut:
    outcome, trace_id = await _run(request, "obfuscate", file, request_json)
    return _transform_out(outcome, trace_id)


@router.post(
    "/synthesize",
    response_model=DocumentTransformOut,
    dependencies=[Depends(require_scope("synthesize")), Depends(enforce_rate_limit)],
)
async def documents_synthesize(
    request: Request,
    file: Annotated[UploadFile, File()],
    request_json: Annotated[str | None, Form(alias="request")] = None,
) -> DocumentTransformOut:
    outcome, trace_id = await _run(request, "synthesize", file, request_json)
    return _transform_out(outcome, trace_id)
```

`dps/app.py` changes:
```python
# imports
import asyncio
import contextlib
from dps.layers.api.common import CodedHTTPException, coded_http_exception_handler   # (Task 2 already)
from dps.layers.api.routers import detect, documents, ops, policies, transform
from dps.layers.documents import DocumentPipeline
from dps.layers.documents.ocr import OcrClient, build_ocr_client, refresh_ocr_readiness

DOCUMENT_MULTIPART_OVERHEAD = 1 << 20


class _BodyLimit(BaseHTTPMiddleware):
    """413 by Content-Length: ``max_payload_mb`` for JSON routes, ``max_document_mb`` + 1 MiB for
    ``/api/v1/documents/*`` (the router re-checks while reading chunked uploads)."""

    def __init__(self, app: Any, *, max_bytes: int, document_max_bytes: int) -> None:
        super().__init__(app)
        self.max_bytes = max_bytes
        self.document_max_bytes = document_max_bytes

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        limit = self.document_max_bytes if request.url.path.startswith("/api/v1/documents/") else self.max_bytes
        length = request.headers.get("content-length")
        if length and length.isdigit() and int(length) > limit:
            return JSONResponse({"detail": f"payload exceeds {limit} bytes", "code": "payload_too_large"},
                                status_code=413)
        return await call_next(request)


async def _ocr_probe_loop(ocr: OcrClient, settings: Settings) -> None:
    """Re-probe OCR readiness forever; one failed probe never stops the loop."""
    while True:
        await asyncio.sleep(settings.ocr_probe_interval_seconds)
        try:
            await refresh_ocr_readiness(ocr, settings)
        except Exception:  # noqa: BLE001 - readiness is best effort; the name is enough
            logger.warning("ocr_probe_error")
```
`create_app(settings=None, *, engine=None, audit_sink=None, ocr_client: OcrClient | None = None)`: after building the engine, `ocr = ocr_client or build_ocr_client(settings)`; in `lifespan`, after the M0 readiness lines:
```python
        await refresh_ocr_readiness(ocr, settings)
        probe = (asyncio.create_task(_ocr_probe_loop(ocr, settings))
                 if ocr.configured() and settings.ocr_probe_interval_seconds > 0 else None)
        try:
            yield
        finally:
            if probe is not None:
                probe.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await probe
            await ocr.aclose()
```
(the M0 bare `yield` becomes this block); `app.state.ocr = ocr`; `app.state.documents = DocumentPipeline(engine, settings, ocr)`; `app.add_middleware(_BodyLimit, max_bytes=settings.max_payload_bytes, document_max_bytes=settings.max_document_bytes + DOCUMENT_MULTIPART_OVERHEAD)`; `app.include_router(documents.router)` before `_mount_frontend(app)`. The lifespan logs `startup` with an extra allow-listed `engine=ocr.engine`.

- [ ] **Step 1: Write the failing tests**

`tests/test_api_documents.py`:
```python
from __future__ import annotations

import base64
import json
from typing import Any

import pytest

pytestmark = pytest.mark.docs
pytest.importorskip("pdfplumber")

from fastapi.testclient import TestClient  # noqa: E402

from dps.app import create_app  # noqa: E402
from dps.audit import MemoryAuditSink  # noqa: E402
from dps.config import Settings  # noqa: E402
from dps.layers.documents.ocr import OcrClient  # noqa: E402
from dps.layers.documents.redact import inspect_output_pdf  # noqa: E402
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
        ocr_probe_interval_seconds=0,
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
```

`tests/test_api_documents_ocr.py` (the M1 headline flow against the real mock):
```python
from __future__ import annotations

import base64
import json

import pytest

pytestmark = [pytest.mark.docs, pytest.mark.ocr]

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
```

- [ ] **Step 2: Run to confirm failure** — `pytest -q tests/test_api_documents.py` → FAIL (`ImportError` for `documents` router / `create_app()` has no `ocr_client`).

- [ ] **Step 3: Implement** the router and the `app.py` changes.

- [ ] **Step 4: Gates** — `pytest -q tests/test_api_documents.py tests/test_api.py tests/test_api_errors.py` → `9 passed` + M0/Task 2 API tests unchanged; with the mock running (`docker run -d --rm --name dps-ocr -p 127.0.0.1:5016:5000 document_intelligence-azure-ocr-mock:latest` until Task 19 adds it to compose) `pytest -q -m ocr` → `2 passed`; full gates green.

- [ ] **Step 5: Commit**
```bash
git add dps/layers/api/routers/documents.py dps/app.py tests/test_api_documents.py tests/test_api_documents_ocr.py
git commit -m "feat(api): /api/v1/documents detect, obfuscate, synthesize; OCR readiness and per-route body limits" -- dps/layers/api/routers/documents.py dps/app.py tests/test_api_documents.py tests/test_api_documents_ocr.py
```

---
### Task 11: Generator specs and the deterministic executor

**Files:**
- Create: `dps/engine/synth/schema_gen.py`, `tests/test_schema_gen.py`

**Interfaces introduced:** `SPEC_VERSION = "dps.genspec.v1"`; `GeneratorName`; `ENTITY_GENERATORS`; `ENTITY_TEMPLATES`; `SchemaError(CodedError)` (status 422); `FieldSpec(name, generator, params, null_rate, unique, entity_type)`; `OrderConstraint(kind="order", fields)`; `RuleCase(field, op, value, then)`; `RuleConstraint(kind="rule", field, when, else)`; `GeneratorSpec(spec_version, name, locale, fields, constraints, source, notes)` with `spec_hash()`; `SchemaResult(rows, spec_hash, generators_used, nulls, unique_retries)`; `execute_spec(spec, *, rows, seed: bytes) -> SchemaResult`; `rows_to_csv(rows, spec) -> str`.

**Generator catalogue (params; all validated before execution, unknown params refused):** `sequence{start=1, step=1, prefix="", width=0}` (unique by construction) · `uuid{}` (v4 from the seeded RNG) · `integer{min, max}` · `decimal{min, max, scale=2}` · `normal{mean, std, min?, max?, scale=2}` · `lognormal{mu, sigma, min?, max?, scale=2}` · `choice{values ≤ 200 scalars, weights?}` · `boolean{p_true=0.5}` · `date{start, end, format ∈ DATE_FORMATS}` · `datetime{start, end}` (naive = UTC) · `pattern{template}` (`A` upper, `a` lower, `9` digit, `X` upper-or-digit, `\` escapes) · `text{words_min=3, words_max=12}` (Faker lorem — no personal data) · `entity{entity_type ∈ ENTITY_GENERATORS, format?}` (the M0 surrogate engine; credentials are not in the set) · `constant{value}`. Constraints: `order` (2–5 fields of one sortable kind — numbers, ISO dates (`%Y-%m-%d`/`%Y%m%d`) or datetimes — ascending within each row) and `rule` (`field` = first matching case's `then`, else `else`; `status = f(balance)`).

**Determinism and coherence:** row `i` uses `row_seed = HMAC-SHA256(seed, "dps.schema|<spec_hash>|row|<i>")`; every field draws from `random.Random(seed_int(row_seed, "field|<name>"))`; PII fields use `SurrogateEngine(row_seed)` with the anchor `"row"`, so one persona per row (name, e-mail, DOB agree) and different personas across rows. Unique fields retry with a derived seed up to 50 times, then `unique_exhausted`. Measured while writing: ≈ 1 080 rows/s for the 16-field test spec on this Mac (10 000 rows ≈ 9 s — the router runs it in a thread).

`dps/engine/synth/schema_gen.py`:
```python
"""Synthesize rows from a generator spec (spec §5.6 "synthesize from schema").

A ``GeneratorSpec`` names one generator per field. The LLM may *propose* a spec (see
``schema_profile``); this module *executes* it, offline and deterministically: the same spec,
seed and row count give the same rows, byte for byte. PII-typed fields come from the surrogate
engine with one persona per row (name, e-mail and date of birth agree) and rule-valid, never
issued identifiers; nothing is drawn from real data. The spec is a reviewable, reusable artefact.
"""

from __future__ import annotations

import csv
import hashlib
import hmac
import io
import json
import operator
import random
import re
import uuid
from collections import Counter
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Any, Literal, NoReturn

from faker import Faker
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from dps.engine.synth.personas import normalise_locale, seed_int
from dps.engine.synth.surrogates import SurrogateEngine
from dps.errors import CodedError

SPEC_VERSION = "dps.genspec.v1"
GeneratorName = Literal[
    "sequence",
    "uuid",
    "integer",
    "decimal",
    "normal",
    "lognormal",
    "choice",
    "boolean",
    "date",
    "datetime",
    "pattern",
    "text",
    "entity",
    "constant",
]
#: Entity types the ``entity`` generator produces. Credentials are never synthesised.
ENTITY_GENERATORS = frozenset(
    {
        "PERSON_NAME",
        "DATE_OF_BIRTH",
        "GENDER",
        "NATIONALITY",
        "AGE",
        "EMAIL",
        "PHONE",
        "STREET_ADDRESS",
        "CITY",
        "POSTAL_CODE",
        "SSN",
        "ITIN",
        "AADHAAR",
        "PAN_IN",
        "SIN",
        "CARD_PAN",
        "CARD_EXPIRY",
        "CVV",
        "IBAN",
        "ROUTING_ABA",
        "IP_ADDRESS",
        "MAC_ADDRESS",
        "URL",
        "USERNAME",
        "DEVICE_ID",
        "ORG_NAME",
        "CUSTOMER_ID",
        "ACCOUNT_NUMBER",
        "EMPLOYEE_ID",
        "CASE_ID",
    }
)
#: Shape templates handed to the surrogate engine (it replaces digits and keeps separators).
ENTITY_TEMPLATES: dict[str, str] = {
    "PERSON_NAME": "First Last",
    "SSN": "000-00-0000",
    "ITIN": "900-70-0000",
    "AADHAAR": "0000 0000 0000",
    "PAN_IN": "AAAAA0000A",
    "SIN": "000 000 000",
    "CARD_PAN": "0000 0000 0000 0000",
    "CVV": "000",
    "ROUTING_ABA": "000000000",
    "PHONE": "+1 201 555 0100",
    "CUSTOMER_ID": "CUST-000000",
    "ACCOUNT_NUMBER": "00000000000000",
    "EMPLOYEE_ID": "EMP-000000",
    "CASE_ID": "CASE-000000",
    "DEVICE_ID": "0000000000000000",
    "STREET_ADDRESS": "1 Main Street",
    "CITY": "City",
    "POSTAL_CODE": "00000",
    "EMAIL": "first.last@example.com",
    "USERNAME": "user",
}
DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d %b %Y", "%Y%m%d")
SORTABLE_DATE_FORMATS = ("%Y-%m-%d", "%Y%m%d")
MAX_UNIQUE_RETRIES = 50
_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}(\.[A-Za-z_][A-Za-z0-9_]{0,62}){0,3}$")
_PREFIX = re.compile(r"^[A-Za-z0-9_-]{0,16}$")
_FORMAT = re.compile(r"^[0-9A-Za-z .+@_/-]{1,40}$")
_PATTERN_CLASSES = {
    "A": "ABCDEFGHJKLMNPQRSTUVWXYZ",
    "a": "abcdefghijkmnopqrstuvwxyz",
    "9": "0123456789",
    "X": "ABCDEFGHJKLMNPQRSTUVWXYZ0123456789",
}
_ALLOWED: dict[str, set[str]] = {
    "sequence": {"start", "step", "prefix", "width"},
    "uuid": set(),
    "integer": {"min", "max"},
    "decimal": {"min", "max", "scale"},
    "normal": {"mean", "std", "min", "max", "scale"},
    "lognormal": {"mu", "sigma", "min", "max", "scale"},
    "choice": {"values", "weights"},
    "boolean": {"p_true"},
    "date": {"start", "end", "format"},
    "datetime": {"start", "end"},
    "pattern": {"template"},
    "text": {"words_min", "words_max"},
    "entity": {"entity_type", "format"},
    "constant": {"value"},
}
_ORDER_KIND = {
    "sequence": "number",
    "integer": "number",
    "decimal": "number",
    "normal": "number",
    "lognormal": "number",
    "date": "date",
    "datetime": "datetime",
}
Scalar = str | int | float | bool | None


class SchemaError(CodedError):
    """A spec, profile or request the schema synthesizer refuses (422 unless stated)."""

    status = 422


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _iso_date(value: Any) -> date:
    return date.fromisoformat(str(value))


def _iso_datetime(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _check_params(name: str, generator: str, p: dict[str, Any]) -> None:  # noqa: C901 - one rule per generator
    def fail(why: str) -> NoReturn:
        raise ValueError(f"field {name!r} ({generator}): {why}")

    unknown = set(p) - _ALLOWED[generator]
    if unknown:
        fail(f"unknown params {sorted(unknown)}")
    if generator == "sequence":
        if not _is_int(p.get("start", 1)) or not _is_int(p.get("step", 1)) or p.get("step", 1) == 0:
            fail("start and step must be integers, step non-zero")
        if not isinstance(p.get("prefix", ""), str) or not _PREFIX.fullmatch(p.get("prefix", "")):
            fail("prefix must be ≤ 16 of [A-Za-z0-9_-]")
        if not _is_int(p.get("width", 0)) or not 0 <= p.get("width", 0) <= 18:
            fail("width must be 0..18")
    elif generator == "integer":
        if not (_is_int(p.get("min")) and _is_int(p.get("max"))) or p["min"] > p["max"]:
            fail("needs integer min ≤ max")
    elif generator == "decimal":
        if not (_is_number(p.get("min")) and _is_number(p.get("max"))) or p["min"] > p["max"]:
            fail("needs numeric min ≤ max")
    elif generator in ("normal", "lognormal"):
        centre, spread = ("mean", "std") if generator == "normal" else ("mu", "sigma")
        if not (_is_number(p.get(centre)) and _is_number(p.get(spread))) or p[spread] <= 0:
            fail(f"needs numeric {centre} and {spread} > 0")
        for bound in ("min", "max"):
            if bound in p and not _is_number(p[bound]):
                fail(f"{bound} must be numeric")
    elif generator == "choice":
        values, weights = p.get("values"), p.get("weights")
        if not isinstance(values, list) or not 1 <= len(values) <= 200:
            fail("values must be a list of 1..200 scalars")
        if any(isinstance(v, dict | list) for v in values):
            fail("values must be scalars")
        if weights is not None and (
            not isinstance(weights, list)
            or len(weights) != len(values)
            or any(not _is_number(w) or w < 0 for w in weights)
            or sum(weights) <= 0
        ):
            fail("weights must be non-negative numbers, one per value, not all zero")
    elif generator == "boolean":
        if not _is_number(p.get("p_true", 0.5)) or not 0 <= p.get("p_true", 0.5) <= 1:
            fail("p_true must be 0..1")
    elif generator == "date":
        try:
            first, last = _iso_date(p["start"]), _iso_date(p["end"])
        except (KeyError, ValueError):
            fail("needs ISO dates start and end")
        if first > last:
            fail("start must be ≤ end")
        if p.get("format", DATE_FORMATS[0]) not in DATE_FORMATS:
            fail(f"format must be one of {list(DATE_FORMATS)}")
    elif generator == "datetime":
        try:
            first_dt, last_dt = _iso_datetime(p["start"]), _iso_datetime(p["end"])
        except (KeyError, ValueError):
            fail("needs ISO datetimes start and end")
        if first_dt > last_dt:
            fail("start must be ≤ end")
    elif generator == "pattern":
        if not isinstance(p.get("template"), str) or not 1 <= len(p["template"]) <= 64:
            fail("template must be 1..64 characters")
    elif generator == "text":
        lo, hi = p.get("words_min", 3), p.get("words_max", 12)
        if not (_is_int(lo) and _is_int(hi)) or not 1 <= lo <= hi <= 200:
            fail("needs 1 ≤ words_min ≤ words_max ≤ 200")
    elif generator == "entity":
        if p.get("entity_type") not in ENTITY_GENERATORS:
            fail(f"entity_type must be one of {sorted(ENTITY_GENERATORS)}")
        if "format" in p and (
            not isinstance(p["format"], str) or not _FORMAT.fullmatch(p["format"])
        ):
            fail("format must be ≤ 40 of [0-9A-Za-z .+@_/-]")
    elif generator == "constant":
        if isinstance(p.get("value"), dict | list):
            fail("value must be a scalar")


class FieldSpec(BaseModel):
    """One output field: a generator, its parameters, a null rate and a uniqueness flag."""

    model_config = ConfigDict(extra="forbid")
    name: str
    generator: GeneratorName
    params: dict[str, Any] = Field(default_factory=dict)
    null_rate: float = Field(default=0.0, ge=0.0, le=0.95)
    unique: bool = False
    entity_type: str | None = None  # what DPS detected (informational; ``entity`` reads params)

    @field_validator("name")
    @classmethod
    def _valid_name(cls, value: str) -> str:
        if not _NAME.fullmatch(value):
            raise ValueError(
                "field names are identifiers, optionally dotted for nesting (≤ 4 levels)"
            )
        return value

    @model_validator(mode="after")
    def _valid_params(self) -> FieldSpec:
        _check_params(self.name, self.generator, self.params)
        return self


class OrderConstraint(BaseModel):
    """The named fields are ascending within every row (dates, datetimes or numbers)."""

    model_config = ConfigDict(extra="forbid")
    kind: Literal["order"] = "order"
    fields: list[str] = Field(min_length=2, max_length=5)


class RuleCase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field: str
    op: Literal["lt", "le", "gt", "ge", "eq", "ne"]
    value: str | int | float | bool
    then: str | int | float | bool | None


class RuleConstraint(BaseModel):
    """``field`` = the first matching case's ``then``, else ``else`` (``status = f(balance)``)."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    kind: Literal["rule"] = "rule"
    field: str
    when: list[RuleCase] = Field(min_length=1, max_length=10)
    otherwise: str | int | float | bool | None = Field(default=None, alias="else")


Constraint = Annotated[OrderConstraint | RuleConstraint, Field(discriminator="kind")]


class GeneratorSpec(BaseModel):
    """A reusable, reviewable recipe for synthetic rows (``dps.genspec.v1``)."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    spec_version: Literal["dps.genspec.v1"] = "dps.genspec.v1"
    name: str = Field(default="dataset", pattern=r"^[A-Za-z0-9_.-]{1,64}$")
    locale: str = "en_US"
    fields: list[FieldSpec] = Field(min_length=1, max_length=200)
    constraints: list[Constraint] = Field(default_factory=list)
    source: Literal["llm", "heuristic", "caller"] = "caller"
    notes: list[str] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def _consistent(self) -> GeneratorSpec:
        names = [f.name for f in self.fields]
        duplicates = sorted({n for n in names if names.count(n) > 1})
        if duplicates:
            raise ValueError(f"duplicate field names {duplicates}")
        by_name = {f.name: f for f in self.fields}
        for name in names:
            parts = name.split(".")
            for i in range(1, len(parts)):
                if ".".join(parts[:i]) in by_name:
                    raise ValueError(
                        f"field {name!r} nests under the scalar field {'.'.join(parts[:i])!r}"
                    )
        for constraint in self.constraints:
            if isinstance(constraint, OrderConstraint):
                kinds = set()
                for name in constraint.fields:
                    spec = by_name.get(name)
                    if spec is None:
                        raise ValueError(f"order constraint names unknown field {name!r}")
                    kind = _ORDER_KIND.get(spec.generator)
                    if kind is None or (
                        spec.generator == "date"
                        and spec.params.get("format", DATE_FORMATS[0]) not in SORTABLE_DATE_FORMATS
                    ):
                        raise ValueError(f"order constraint field {name!r} is not sortable")
                    kinds.add(kind)
                if len(kinds) != 1:
                    raise ValueError(
                        "order constraint fields must all be numbers, dates or datetimes"
                    )
            else:
                for name in (constraint.field, *(case.field for case in constraint.when)):
                    if name not in by_name:
                        raise ValueError(f"rule constraint names unknown field {name!r}")
        return self

    def spec_hash(self) -> str:
        """sha256 of the canonical spec, ignoring ``source`` and ``notes``."""
        canon = json.dumps(
            self.model_dump(mode="json", by_alias=True, exclude={"source", "notes"}),
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canon.encode("utf-8")).hexdigest()


class SchemaResult(BaseModel):
    rows: list[dict[str, Any]]
    spec_hash: str
    generators_used: dict[str, int]
    nulls: int
    unique_retries: int


def _pattern(template: str, rng: random.Random) -> str:
    out: list[str] = []
    escaped = False
    for ch in template:
        if escaped:
            out.append(ch)
            escaped = False
        elif ch == "\\":
            escaped = True
        elif ch in _PATTERN_CLASSES:
            out.append(rng.choice(_PATTERN_CLASSES[ch]))
        else:
            out.append(ch)
    return "".join(out)


def _bounded(value: float, p: dict[str, Any]) -> float | int:
    if "min" in p:
        value = max(value, float(p["min"]))
    if "max" in p:
        value = min(value, float(p["max"]))
    scale = int(p.get("scale", 2))
    return round(value, scale) if scale > 0 else int(round(value))


def _generate(
    spec: FieldSpec,
    row: int,
    rng: random.Random,
    surrogates: SurrogateEngine,
    fake: Faker,
    locale: str,
) -> Any:
    p, g = spec.params, spec.generator
    if g == "sequence":
        n = int(p.get("start", 1)) + row * int(p.get("step", 1))
        prefix, width = str(p.get("prefix", "")), int(p.get("width", 0))
        return f"{prefix}{str(n).zfill(width)}" if prefix or width else n
    if g == "uuid":
        return str(uuid.UUID(int=rng.getrandbits(128), version=4))
    if g == "integer":
        return rng.randint(int(p["min"]), int(p["max"]))
    if g == "decimal":
        return round(rng.uniform(float(p["min"]), float(p["max"])), int(p.get("scale", 2)))
    if g == "normal":
        return _bounded(rng.gauss(float(p["mean"]), float(p["std"])), p)
    if g == "lognormal":
        return _bounded(rng.lognormvariate(float(p["mu"]), float(p["sigma"])), p)
    if g == "choice":
        return rng.choices(p["values"], weights=p.get("weights"))[0]
    if g == "boolean":
        return rng.random() < float(p.get("p_true", 0.5))
    if g == "date":
        start, end = _iso_date(p["start"]), _iso_date(p["end"])
        return (start + timedelta(days=rng.randint(0, (end - start).days))).strftime(
            str(p.get("format", DATE_FORMATS[0]))
        )
    if g == "datetime":
        start_dt, end_dt = _iso_datetime(p["start"]), _iso_datetime(p["end"])
        seconds = rng.randint(0, int((end_dt - start_dt).total_seconds()))
        return (start_dt + timedelta(seconds=seconds)).isoformat()
    if g == "pattern":
        return _pattern(str(p["template"]), rng)
    if g == "text":
        fake.seed_instance(rng.getrandbits(64))
        words = rng.randint(int(p.get("words_min", 3)), int(p.get("words_max", 12)))
        return str(fake.sentence(nb_words=words, variable_nb_words=False))
    if g == "entity":
        entity = str(p["entity_type"])
        template = str(p.get("format") or ENTITY_TEMPLATES.get(entity, ""))
        return surrogates.surrogate(entity, template, locale, "row")
    return p.get("value")


_OPS: dict[str, Callable[[Any, Any], bool]] = {
    "lt": operator.lt,
    "le": operator.le,
    "gt": operator.gt,
    "ge": operator.ge,
    "eq": operator.eq,
    "ne": operator.ne,
}


def _apply_constraints(
    values: dict[str, Any], constraints: list[OrderConstraint | RuleConstraint]
) -> None:
    for constraint in constraints:
        if isinstance(constraint, OrderConstraint):
            present = [n for n in constraint.fields if values.get(n) is not None]
            for name, value in zip(present, sorted(values[n] for n in present), strict=True):
                values[name] = value
            continue
        result: Any = constraint.otherwise
        for case in constraint.when:
            current = values.get(case.field)
            if current is None:
                continue
            try:
                matched = _OPS[case.op](current, case.value)
            except TypeError:
                matched = False
            if matched:
                result = case.then
                break
        values[constraint.field] = result


def _nest(values: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name, value in values.items():
        node = out
        *parents, leaf = name.split(".")
        for part in parents:
            node = node.setdefault(part, {})
        node[leaf] = value
    return out


def _row_seed(seed: bytes, spec_hash: str, row: int) -> bytes:
    return hmac.new(seed, f"dps.schema|{spec_hash}|row|{row}".encode(), hashlib.sha256).digest()


def execute_spec(spec: GeneratorSpec, *, rows: int, seed: bytes) -> SchemaResult:
    """Generate ``rows`` rows. Deterministic in (spec, seed, rows); CPU-bound (run in a thread)."""
    if rows < 1:
        raise SchemaError("invalid_rows", "rows must be at least 1")
    spec_hash = spec.spec_hash()
    fake = Faker(normalise_locale(spec.locale))
    seen: dict[str, set[str]] = {f.name: set() for f in spec.fields if f.unique}
    used: Counter[str] = Counter()
    nulls = retries = 0
    out: list[dict[str, Any]] = []
    for row in range(rows):
        row_seed = _row_seed(seed, spec_hash, row)
        surrogates = SurrogateEngine(row_seed, default_locale=spec.locale)
        values: dict[str, Any] = {}
        for field in spec.fields:
            rng = random.Random(seed_int(row_seed, f"field|{field.name}"))
            if field.null_rate and not field.unique and rng.random() < field.null_rate:
                values[field.name] = None
                nulls += 1
                continue
            value = _generate(field, row, rng, surrogates, fake, spec.locale)
            if field.unique:
                attempt = 0
                while json.dumps(value, default=str) in seen[field.name]:
                    attempt += 1
                    retries += 1
                    if attempt > MAX_UNIQUE_RETRIES:
                        raise SchemaError(
                            "unique_exhausted",
                            f"field {field.name!r} cannot produce {rows} unique values",
                        )
                    retry = hmac.new(
                        row_seed, f"retry|{field.name}|{attempt}".encode(), hashlib.sha256
                    ).digest()
                    value = _generate(
                        field,
                        row,
                        random.Random(seed_int(retry, "field")),
                        SurrogateEngine(retry, default_locale=spec.locale),
                        fake,
                        spec.locale,
                    )
                seen[field.name].add(json.dumps(value, default=str))
            values[field.name] = value
            used[field.generator] += 1
        _apply_constraints(values, spec.constraints)
        out.append(_nest(values))
    return SchemaResult(
        rows=out,
        spec_hash=spec_hash,
        generators_used=dict(used),
        nulls=nulls,
        unique_retries=retries,
    )


def _flat(row: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in row.items():
        if isinstance(value, dict):
            out.update(_flat(value, f"{prefix}{key}."))
        else:
            out[f"{prefix}{key}"] = value
    return out


def rows_to_csv(rows: list[dict[str, Any]], spec: GeneratorSpec) -> str:
    """CSV with one column per spec field (dotted names for nested fields); null → empty cell."""
    header = [f.name for f in spec.fields]
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(header)
    for row in rows:
        flat = _flat(row)
        writer.writerow(["" if flat.get(h) is None else flat.get(h) for h in header])
    return buf.getvalue()
```

- [ ] **Step 1: Write the failing tests** — `tests/test_schema_gen.py`:
```python
from __future__ import annotations

import csv
import io
import re
from typing import Any

import pytest
from pydantic import ValidationError

from dps.engine.detect.checksums import validate
from dps.engine.synth.schema_gen import GeneratorSpec, SchemaError, execute_spec, rows_to_csv

SEED = bytes(range(32))
FIELDS: list[dict[str, Any]] = [
    {
        "name": "customer_id",
        "generator": "sequence",
        "params": {"prefix": "CUST-", "width": 6},
        "unique": True,
    },
    {"name": "full_name", "generator": "entity", "params": {"entity_type": "PERSON_NAME"}},
    {"name": "email", "generator": "entity", "params": {"entity_type": "EMAIL"}},
    {"name": "card", "generator": "entity", "params": {"entity_type": "CARD_PAN"}},
    {"name": "aadhaar", "generator": "entity", "params": {"entity_type": "AADHAAR"}},
    {"name": "iban", "generator": "entity", "params": {"entity_type": "IBAN"}},
    {"name": "ssn", "generator": "entity", "params": {"entity_type": "SSN"}},
    {"name": "score", "generator": "integer", "params": {"min": 300, "max": 900}},
    {"name": "balance", "generator": "decimal", "params": {"min": -500, "max": 5000, "scale": 2}},
    {
        "name": "status",
        "generator": "choice",
        "params": {"values": ["ACTIVE", "CLOSED"], "weights": [9, 1]},
    },
    {
        "name": "opened_on",
        "generator": "date",
        "params": {"start": "2015-01-01", "end": "2020-12-31"},
    },
    {
        "name": "closed_on",
        "generator": "date",
        "params": {"start": "2015-01-01", "end": "2026-10-01"},
    },
    {"name": "ref", "generator": "pattern", "params": {"template": "AA-9999"}},
    {"name": "address.city", "generator": "entity", "params": {"entity_type": "CITY"}},
    {"name": "vip", "generator": "boolean", "params": {"p_true": 0.3}},
    {"name": "memo", "generator": "text", "params": {"words_min": 3, "words_max": 6}},
]
CONSTRAINTS: list[dict[str, Any]] = [
    {"kind": "order", "fields": ["opened_on", "closed_on"]},
    {
        "kind": "rule",
        "field": "status",
        "when": [{"field": "balance", "op": "lt", "value": 0, "then": "OVERDRAWN"}],
        "else": "ACTIVE",
    },
]


def spec(**overrides: Any) -> GeneratorSpec:
    return GeneratorSpec.model_validate(
        {"name": "customers", "fields": FIELDS, "constraints": CONSTRAINTS, **overrides}
    )


def test_execution_is_deterministic() -> None:
    first = execute_spec(spec(), rows=50, seed=SEED)
    assert first.rows == execute_spec(spec(), rows=50, seed=SEED).rows
    assert first.rows != execute_spec(spec(), rows=50, seed=bytes(32)).rows
    assert first.spec_hash == spec().spec_hash() and first.generators_used["entity"] == 50 * 7


def test_generators_respect_their_params() -> None:
    rows = execute_spec(spec(), rows=500, seed=SEED).rows
    assert [r["customer_id"] for r in rows[:3]] == ["CUST-000001", "CUST-000002", "CUST-000003"]
    assert all(300 <= r["score"] <= 900 for r in rows)
    assert all(
        round(r["balance"], 2) == r["balance"] and -500 <= r["balance"] <= 5000 for r in rows
    )
    assert all(re.fullmatch(r"[A-Z]{2}-\d{4}", r["ref"]) for r in rows)
    assert 0.24 <= sum(r["vip"] for r in rows) / len(rows) <= 0.36
    assert all(3 <= len(r["memo"].rstrip(".").split()) <= 6 for r in rows)
    assert all(isinstance(r["address"]["city"], str) and r["address"]["city"] for r in rows)


def test_entity_values_are_rule_valid_and_coherent() -> None:
    rows = execute_spec(spec(), rows=200, seed=SEED).rows
    assert all(validate("CARD_PAN", r["card"]) is True for r in rows)
    assert all(validate("AADHAAR", r["aadhaar"]) is True for r in rows)
    assert all(validate("IBAN", r["iban"]) is True for r in rows)
    assert all(r["ssn"].startswith("9") for r in rows)  # never-issued area
    assert all(
        r["email"].split("@")[1] in ("example.com", "example.net", "example.org") for r in rows
    )
    coherent = sum(r["full_name"].split()[0].lower() in r["email"] for r in rows)
    assert coherent >= 0.95 * len(rows)  # one persona per row


def test_constraints_hold() -> None:
    rows = execute_spec(spec(), rows=300, seed=SEED).rows
    assert all(r["opened_on"] <= r["closed_on"] for r in rows)
    assert all(r["status"] == ("OVERDRAWN" if r["balance"] < 0 else "ACTIVE") for r in rows)


def test_csv_flattens_nested_fields() -> None:
    s = spec()
    text = rows_to_csv(execute_spec(s, rows=5, seed=SEED).rows, s)
    table = list(csv.reader(io.StringIO(text)))
    assert "address.city" in table[0] and len(table) == 6


def test_unique_and_null_rate() -> None:
    tiny = GeneratorSpec.model_validate(
        {
            "fields": [
                {"name": "d", "generator": "pattern", "params": {"template": "9"}, "unique": True}
            ]
        }
    )
    with pytest.raises(SchemaError) as exc:
        execute_spec(tiny, rows=11, seed=SEED)
    assert exc.value.code == "unique_exhausted"
    sparse = GeneratorSpec.model_validate(
        {
            "fields": [
                {
                    "name": "x",
                    "generator": "integer",
                    "params": {"min": 1, "max": 9},
                    "null_rate": 0.5,
                }
            ]
        }
    )
    result = execute_spec(sparse, rows=1000, seed=SEED)
    assert 400 <= result.nulls <= 600 and sum(r["x"] is None for r in result.rows) == result.nulls


@pytest.mark.parametrize(
    "bad",
    [
        {"fields": [{"name": "a", "generator": "teleport"}]},
        {"fields": [{"name": "a", "generator": "integer", "params": {"min": 5, "max": 1}}]},
        {
            "fields": [
                {
                    "name": "a",
                    "generator": "choice",
                    "params": {"values": ["x", "y"], "weights": [1]},
                }
            ]
        },
        {"fields": [{"name": "a", "generator": "uuid"}, {"name": "a", "generator": "uuid"}]},
        {"fields": [{"name": "a", "generator": "entity", "params": {"entity_type": "PASSWORD"}}]},
        {"fields": [{"name": "1bad name", "generator": "uuid"}]},
        {"fields": [{"name": "a", "generator": "uuid"}, {"name": "a.b", "generator": "uuid"}]},
        {
            "fields": [{"name": "t", "generator": "text"}, {"name": "u", "generator": "uuid"}],
            "constraints": [{"kind": "order", "fields": ["t", "u"]}],
        },
        {
            "fields": [
                {
                    "name": "a",
                    "generator": "integer",
                    "params": {"min": 1, "max": 2, "colour": "red"},
                }
            ]
        },
    ],
)
def test_invalid_specs_are_refused(bad: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        GeneratorSpec.model_validate(bad)


def test_spec_hash_ignores_notes_and_source() -> None:
    assert spec(notes=["x"], source="llm").spec_hash() == spec().spec_hash()
    assert spec(locale="en_IN").spec_hash() != spec().spec_hash()


def test_reversed_date_ranges_name_the_problem() -> None:
    with pytest.raises(ValidationError, match="start must be"):
        GeneratorSpec.model_validate(
            {
                "fields": [
                    {
                        "name": "d",
                        "generator": "date",
                        "params": {"start": "2020-01-02", "end": "2020-01-01"},
                    }
                ]
            }
        )
```

- [ ] **Step 2: Run to confirm failure** — `pytest -q tests/test_schema_gen.py` → FAIL (`ModuleNotFoundError: dps.engine.synth.schema_gen`).

- [ ] **Step 3: Implement** `schema_gen.py` as above.

- [ ] **Step 4: Gates** — `pytest -q tests/test_schema_gen.py` → `17 passed` (verified while writing against the M0 plan's surrogate, persona and checksum code). Full gates green.

- [ ] **Step 5: Commit**
```bash
git add dps/engine/synth/schema_gen.py tests/test_schema_gen.py
git commit -m "feat(synth): generator specs executed deterministically — seeded, persona-coherent, rule-valid" -- dps/engine/synth/schema_gen.py tests/test_schema_gen.py
```

---

### Task 12: Structure profiles and spec proposers (heuristic and LLM, P1-safe)

**Files:**
- Create: `dps/engine/shapes.py`, `dps/engine/synth/schema_profile.py`, `tests/test_schema_profile.py`

**Interfaces introduced:** `value_shape(value, *, max_chars=24)`; `FieldProfile`, `StructureProfile` (LLM-safe), `FieldStats` (private, never serialised into a prompt or response), `ProfileResult(profile, stats)`, `DdlColumn`; `profile_from_json_schema(schema, *, name)`, `parse_ddl(ddl) -> (table, columns)`, `profile_from_ddl(ddl, *, name)`, `profile_from_sample(rows, detector, *, name)`; `heuristic_spec(result, *, locale)`, `render_prompt(profile)`, `profile_originals(result)`, `merge_llm_spec(text, heuristic, result)`, `async propose_spec(result, gateway, *, locale, use_llm=True) -> (GeneratorSpec, info)`, `verify_against_sample(rows, result) -> Verification`.

**P1 rules, enforced in code and tests:** the gateway receives only `render_prompt(profile)` — a fixed system text plus the profile JSON (names, types, shapes, magnitudes as orders of ten, null/distinct rates, entity verdicts, safe labels). A field whose *name* looks sensitive is aliased `field_N` (and keeps that name in the spec, so the output never echoes it). Before any call, `assert_placeholder_safe` (M0) checks the profile JSON against every sample value of every field DPS judged sensitive (the profile carries no raw values by construction — this is defence in depth); a hit skips the model (`fallback: leak_guard`). Stub or unconfigured providers never call (deterministic heuristic, contract §9 "stub deterministic"). The model's reply is parsed leniently (first `{` to last `}`), validated, and merged: fields DPS detected as sensitive always keep the heuristic `entity` generator; invalid fields fall back one by one; invalid constraints are dropped with a note; an unusable reply → heuristic with `fallback: llm_invalid_spec`. `LLM_RAW_PII` is never incremented because nothing raw is ever sent.

**Heuristic rules:** dedicated entity types → `entity`; integer primary key → `sequence`; `uuid`; `boolean` (observed rate); integers → `integer` over the observed range rounded to two significant figures; numbers → `lognormal` when right-skewed (p90/median > 3, non-negative) else `decimal`; dates/datetimes → whole-year ranges; safe categorical labels → `choice` with observed weights; strings with shapes → `pattern` from the dominant shape (unique when every value is distinct over ≥ 20 rows or the field is a key); other entity strings → `entity`; otherwise `text`. DDL parsing understands quoted identifiers, comments, `NUMBER(p,s)`, `VARCHAR2(n CHAR)`, `TIMESTAMP(n) WITH TIME ZONE`, inline and table-level primary keys; it is parsed, never executed.

`dps/engine/shapes.py`:
```python
"""Value shapes (contract §5.4): letters → x/X, digits → 9, everything else kept, ≤ 24 chars.

A shape says what a value looks like (``XX-9999``) without saying what it is; it is the only
form in which sample values may appear in a profile, a snapshot or a prompt.
"""

from __future__ import annotations

MAX_SHAPE_CHARS = 24


def value_shape(value: str, *, max_chars: int = MAX_SHAPE_CHARS) -> str:
    """``"AB-1234"`` → ``"XX-9999"``; ``"ann.k@x.in"`` → ``"xxx.x@x.xx"``."""
    out: list[str] = []
    for ch in value[:max_chars]:
        if ch.isdigit():
            out.append("9")
        elif ch.isalpha():
            out.append("X" if ch.isupper() else "x")
        else:
            out.append(ch)
    return "".join(out)
```

`dps/engine/synth/schema_profile.py`:
```python
"""Structure profiles and generator-spec proposers for synthesize-from-schema (spec §5.6).

P1 holds: the LLM never sees a value. A :class:`StructureProfile` is all that may leave DPS —
field names (aliased when a name itself looks sensitive), inferred types, value *shapes*
(``XX-9999``), orders of magnitude, null and distinct rates, DPS's own entity verdicts, and
categorical labels only when they are safe (≤ 12 distinct, letters only, each seen at least twice,
nothing detected in them). Exact statistics stay in :class:`FieldStats`, which is never put in a
prompt; the rule-based proposer (no LLM) uses them, rounded to two significant figures.
"""

from __future__ import annotations

import json
import math
import re
import statistics
from collections import Counter
from dataclasses import dataclass, replace
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from dps.engine.detect.ensemble import Detector
from dps.engine.detect.structural import classify_key
from dps.engine.findings import Finding, Locator
from dps.engine.shapes import value_shape
from dps.engine.synth.llm_text import LeakToModelError, assert_placeholder_safe
from dps.engine.synth.schema_gen import ENTITY_GENERATORS, FieldSpec, GeneratorSpec, SchemaError
from dps.engine.taxonomy import entity_info
from dps.engine.verify import Verification, verify_output
from dps.llm.gateway import LlmCallError, LlmGateway, LlmUnavailable

FieldType = Literal["string", "integer", "number", "boolean", "date", "datetime", "uuid", "unknown"]
ENUM_MAX = 12
ENUM_MIN_COUNT = 2
MAX_SAMPLE_ROWS = 1000
MAX_DDL_CHARS = 100_000
STRUCTURAL_FLOOR = 0.7
VALUE_HIT_RATE = 0.5
#: Entity types whose surrogate is persona-coherent or rule-valid: always the ``entity`` generator.
DEDICATED_ENTITIES = frozenset(
    {
        "PERSON_NAME",
        "DATE_OF_BIRTH",
        "GENDER",
        "NATIONALITY",
        "AGE",
        "EMAIL",
        "PHONE",
        "STREET_ADDRESS",
        "CITY",
        "POSTAL_CODE",
        "SSN",
        "ITIN",
        "AADHAAR",
        "PAN_IN",
        "SIN",
        "CARD_PAN",
        "CARD_EXPIRY",
        "CVV",
        "IBAN",
        "ROUTING_ABA",
        "IP_ADDRESS",
        "MAC_ADDRESS",
        "URL",
        "USERNAME",
        "ORG_NAME",
    }
)
_SAFE_LABEL = re.compile(r"^[A-Za-z][A-Za-z _-]{0,23}$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DATETIME = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?$")
_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_NOT_IDENT = re.compile(r"[^A-Za-z0-9_]")
_PATTERN_LITERALS = set("Aa9X\\")

SPEC_SYSTEM = (
    "You design synthetic-data generators. You receive a JSON description of a table's structure: "
    "field names, types, value shapes (letters become x or X, digits 9), orders of magnitude, null rates "
    "and the entity type a privacy engine detected. Reply with ONE JSON object and nothing else: "
    '{"fields": [{"name": str, "generator": str, "params": {}, "null_rate": float, "unique": bool}], '
    '"constraints": [], "notes": [str]}. Generators and params: sequence{start,step,prefix,width}, uuid{}, '
    "integer{min,max}, decimal{min,max,scale}, normal{mean,std,min,max,scale}, "
    "lognormal{mu,sigma,min,max,scale}, choice{values,weights}, boolean{p_true}, "
    "date{start,end,format}, datetime{start,end}, pattern{template: A upper, a lower, 9 digit, "
    "X upper-or-digit}, text{words_min,words_max}, entity{entity_type,format}, constant{value}. Use "
    "entity for every field that has an entity_type. Constraints: {kind: order, fields: [...]} or "
    "{kind: rule, field, when: [{field, op: lt|le|gt|ge|eq|ne, value, then}], else}. Use exactly the "
    "field names given. Never write personal data."
)


class FieldProfile(BaseModel):
    """What may be said about one field to a model: structure, never values."""

    name: str
    type: FieldType
    nullable: bool = True
    null_rate: float | None = None
    distinct_ratio: float | None = None
    entity_type: str | None = None
    entity_source: Literal["structural", "values", "schema", "none"] = "none"
    shapes: list[str] = Field(default_factory=list)
    length: dict[str, int] | None = None
    magnitude: dict[str, Any] | None = None
    enum: list[str] | None = None
    year_range: list[int] | None = None
    primary_key: bool = False


class StructureProfile(BaseModel):
    source: Literal["json_schema", "ddl", "sample"]
    name: str = "dataset"
    fields: list[FieldProfile]
    rows_sampled: int | None = None


class FieldStats(BaseModel):
    """Exact statistics of a field — never serialised into a prompt or a response."""

    minimum: float | None = None
    maximum: float | None = None
    median: float | None = None
    p90: float | None = None
    decimals: int = 0
    true_rate: float | None = None
    enum_counts: dict[str, int] = Field(default_factory=dict)
    date_min: str | None = None
    date_max: str | None = None
    originals: list[str] = Field(default_factory=list)  # sensitive sample values (leakage checks)


class ProfileResult(BaseModel):
    profile: StructureProfile
    stats: dict[str, FieldStats] = Field(default_factory=dict)


@dataclass(frozen=True)
class DdlColumn:
    name: str
    type_name: str
    length: int | None
    precision: int | None
    scale: int | None
    nullable: bool
    primary_key: bool


# -- helpers --------------------------------------------------------------------------------------
def _ident(name: str, index: int) -> str:
    cleaned = _NOT_IDENT.sub("_", name.strip())[:63] or f"field_{index}"
    return cleaned if not cleaned[0].isdigit() else f"_{cleaned[:62]}"


def _structural(name: str) -> str | None:
    candidates = classify_key(f"$.{name}")
    return (
        candidates[0].entity_type
        if candidates and candidates[0].score >= STRUCTURAL_FLOOR
        else None
    )


def _order(value: float) -> int:
    return 0 if value == 0 else int(math.floor(math.log10(abs(value))))


def _magnitude(lo: float, hi: float, integer: bool) -> dict[str, Any]:
    return {
        "min_order": _order(lo),
        "max_order": _order(hi),
        "negative": lo < 0,
        "integer": integer,
    }


def _sig2(value: float) -> float:
    if value == 0:
        return 0.0
    return round(value, -_order(value) + 1)


def _decimals(values: list[float]) -> int:
    best = 0
    for v in values:
        text = repr(float(v))
        if "e" not in text and "." in text:
            best = max(best, len(text.split(".")[1].rstrip("0")))
    return min(best, 6)


def _infer_type(values: list[Any]) -> FieldType:
    if not values:
        return "unknown"
    if all(isinstance(v, bool) for v in values):
        return "boolean"
    if all(isinstance(v, int) and not isinstance(v, bool) for v in values):
        return "integer"
    if all(isinstance(v, int | float) and not isinstance(v, bool) for v in values):
        return "number"
    if any(isinstance(v, dict | list) for v in values):
        return "unknown"
    strings = [str(v) for v in values]
    for pattern, kind in ((_UUID, "uuid"), (_DATE, "date"), (_DATETIME, "datetime")):
        if all(pattern.match(s) for s in strings):
            return kind  # type: ignore[return-value]
    return "string"


def _flatten(row: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in row.items():
        if isinstance(value, dict):
            out.update(_flatten(value, f"{prefix}{key}."))
        else:
            out[f"{prefix}{key}"] = value
    return out


# -- JSON Schema ----------------------------------------------------------------------------------
def profile_from_json_schema(schema: dict[str, Any], *, name: str = "dataset") -> ProfileResult:
    """Profile a JSON Schema object (nested objects flatten to dotted names; arrays are skipped)."""
    if schema.get("type", "object") != "object" or not isinstance(schema.get("properties"), dict):
        raise SchemaError("schema_unsupported", "expected a JSON Schema object with properties")
    fields: list[FieldProfile] = []
    stats: dict[str, FieldStats] = {}

    def walk(properties: dict[str, Any], prefix: str, required: set[str]) -> None:
        for key, sub in properties.items():
            if not isinstance(sub, dict):
                continue
            kind = sub.get("type", "string")
            nullable = key not in required
            if isinstance(kind, list):
                nullable = nullable or "null" in kind
                kind = next((k for k in kind if k != "null"), "string")
            if kind == "object" and isinstance(sub.get("properties"), dict):
                walk(
                    sub["properties"],
                    f"{prefix}{_ident(key, len(fields) + 1)}.",
                    set(sub.get("required") or []),
                )
                continue
            if kind == "array":
                continue
            fmt = sub.get("format")
            ftype: FieldType = "string"
            if kind in ("integer", "number", "boolean"):
                ftype = kind
            if fmt == "date":
                ftype = "date"
            elif fmt == "date-time":
                ftype = "datetime"
            elif fmt == "uuid":
                ftype = "uuid"
            fname = f"{prefix}{_ident(key, len(fields) + 1)}"
            entity = "EMAIL" if fmt == "email" else _structural(key)
            profile = FieldProfile(
                name=fname,
                type=ftype,
                nullable=nullable,
                entity_type=entity,
                entity_source="schema" if fmt == "email" else "structural" if entity else "none",
            )
            st = FieldStats()
            enum = sub.get("enum")
            if isinstance(enum, list) and entity is None and enum and len(enum) <= 50:
                labels = [str(v) for v in enum]
                if all(_SAFE_LABEL.fullmatch(v) for v in labels):
                    profile.enum = labels
                    st.enum_counts = dict.fromkeys(labels, 1)
            lo, hi = sub.get("minimum"), sub.get("maximum")
            if (
                isinstance(lo, int | float)
                and isinstance(hi, int | float)
                and ftype in ("integer", "number")
            ):
                profile.magnitude = _magnitude(float(lo), float(hi), ftype == "integer")
                st.minimum, st.maximum = float(lo), float(hi)
            if isinstance(sub.get("maxLength"), int):
                profile.length = {"min": int(sub.get("minLength", 0)), "max": int(sub["maxLength"])}
            fields.append(profile)
            stats[fname] = st

    walk(schema["properties"], "", set(schema.get("required") or []))
    if not fields:
        raise SchemaError("schema_unsupported", "the schema has no scalar properties")
    return ProfileResult(
        profile=StructureProfile(source="json_schema", name=name, fields=fields), stats=stats
    )


# -- DDL ------------------------------------------------------------------------------------------
_CREATE = re.compile(
    r"create\s+(?:global\s+temporary\s+)?table\s+(?:if\s+not\s+exists\s+)?([^\s(]+)\s*\(",
    re.IGNORECASE,
)
_COLUMN = re.compile(
    r'^\s*("(?:[^"]|"")+"|`[^`]+`|\[[^\]]+\]|[A-Za-z_][A-Za-z0-9_$#]*)\s*(.*)$', re.DOTALL
)
_TYPE = re.compile(
    r"^([A-Za-z][A-Za-z0-9_]*(?:\s+(?:varying|precision))?)\s*"
    r"(?:\(\s*(\d+)\s*(?:,\s*(-?\d+)\s*)?(?:byte|char)?\s*\))?",
    re.IGNORECASE,
)
_TABLE_ITEMS = (
    "constraint",
    "primary key",
    "unique",
    "foreign key",
    "check",
    "key ",
    "index ",
    "exclude",
)


def _strip_comments(sql: str) -> str:
    return re.sub(r"--[^\n]*", " ", re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL))


def _split_top_level(body: str) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    depth, quote = 0, ""
    for ch in body:
        if quote:
            current.append(ch)
            if ch == quote:
                quote = ""
            continue
        if ch in "'\"`":
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "," and depth == 0:
            parts.append("".join(current).strip())
            current = []
            continue
        current.append(ch)
    parts.append("".join(current).strip())
    return [p for p in parts if p]


def _unquote(identifier: str) -> str:
    identifier = identifier.strip()
    if identifier[:1] in '"`[' and len(identifier) > 1:
        return identifier[1:-1].replace('""', '"')
    return identifier


def parse_ddl(ddl: str) -> tuple[str, list[DdlColumn]]:
    """``CREATE TABLE`` → (table name, columns). Parsed, never executed."""
    if len(ddl) > MAX_DDL_CHARS:
        raise SchemaError(
            "ddl_too_large", f"DDL is limited to {MAX_DDL_CHARS} characters", status=413
        )
    text = _strip_comments(ddl)
    match = _CREATE.search(text)
    if match is None:
        raise SchemaError("ddl_unparsed", "expected CREATE TABLE <name> ( … )")
    depth, i = 1, match.end()
    while i < len(text) and depth:
        depth += {"(": 1, ")": -1}.get(text[i], 0)
        i += 1
    if depth:
        raise SchemaError("ddl_unparsed", "unbalanced parentheses in CREATE TABLE")
    columns: list[DdlColumn] = []
    primary: set[str] = set()
    for item in _split_top_level(text[match.end() : i - 1]):
        lowered = item.lower()
        if lowered.startswith(_TABLE_ITEMS):
            pk = re.search(r"primary\s+key\s*\(([^)]*)\)", item, re.IGNORECASE)
            if pk:
                primary |= {_unquote(c) for c in pk.group(1).split(",")}
            continue
        column = _COLUMN.match(item)
        if column is None:
            continue
        kind = _TYPE.match(column.group(2))
        if kind is None:
            continue
        first, second = kind.group(2), kind.group(3)
        tail = column.group(2)[kind.end() :].lower()
        columns.append(
            DdlColumn(
                name=_unquote(column.group(1)),
                type_name=re.sub(r"\s+", " ", kind.group(1).lower()),
                length=int(first) if first and second is None else None,
                precision=int(first) if first else None,
                scale=int(second) if second else None,
                nullable="not null" not in tail and "primary key" not in tail,
                primary_key="primary key" in tail,
            )
        )
    if not columns:
        raise SchemaError("ddl_unparsed", "no columns found in CREATE TABLE")
    table = _unquote(match.group(1).split(".")[-1])
    return table, [
        replace(c, primary_key=True, nullable=False) if c.name in primary else c for c in columns
    ]


def _ddl_type(column: DdlColumn) -> FieldType:
    t = column.type_name
    if t in ("uuid", "uniqueidentifier"):
        return "uuid"
    if t in ("boolean", "bool", "bit"):
        return "boolean"
    if t == "date":
        return "date"
    if t.startswith(("timestamp", "datetime")):
        return "datetime"
    if t in (
        "int",
        "integer",
        "bigint",
        "smallint",
        "tinyint",
        "serial",
        "bigserial",
        "smallserial",
        "int2",
        "int4",
        "int8",
    ):
        return "integer"
    if t in ("number", "numeric", "decimal"):
        return "integer" if column.precision is not None and not column.scale else "number"
    if t in (
        "float",
        "real",
        "double precision",
        "double",
        "binary_double",
        "binary_float",
        "money",
    ):
        return "number"
    if t in (
        "char",
        "varchar",
        "varchar2",
        "nvarchar",
        "nvarchar2",
        "nchar",
        "text",
        "clob",
        "nclob",
        "string",
        "character",
        "character varying",
    ):
        return "string"
    return "unknown"


def profile_from_ddl(ddl: str, *, name: str | None = None) -> ProfileResult:
    """Profile a ``CREATE TABLE`` statement (types, lengths, precision, keys, nullability)."""
    table, columns = parse_ddl(ddl)
    fields: list[FieldProfile] = []
    stats: dict[str, FieldStats] = {}
    for index, column in enumerate(columns, start=1):
        fname = _ident(column.name, index)
        ftype = _ddl_type(column)
        entity = _structural(column.name)
        profile = FieldProfile(
            name=fname,
            type=ftype,
            nullable=column.nullable,
            primary_key=column.primary_key,
            entity_type=entity,
            entity_source="structural" if entity else "none",
        )
        st = FieldStats()
        if ftype == "string" and column.length:
            profile.length = {"min": 0, "max": column.length}
        if ftype in ("integer", "number") and column.precision:
            top = 10.0 ** (column.precision - (column.scale or 0)) - 1
            profile.magnitude = _magnitude(0.0, top, ftype == "integer")
            st.minimum, st.maximum, st.decimals = 0.0, top, column.scale or 0
        fields.append(profile)
        stats[fname] = st
    profile_name = name or _ident(table, 0)
    return ProfileResult(
        profile=StructureProfile(source="ddl", name=profile_name, fields=fields), stats=stats
    )


# -- sample ---------------------------------------------------------------------------------------
def _value_entity(values: list[Any], detector: Detector) -> str | None:
    votes: Counter[str] = Counter()
    checked = 0
    for value in values[:50]:
        text = str(value)
        if not text.strip():
            continue
        checked += 1
        for finding in detector.detect(text=text).findings:
            covered = (
                len(text)
                if finding.locator.whole
                else (finding.locator.end or 0) - (finding.locator.start or 0)
            )
            if covered >= 0.8 * len(text.strip()):
                votes[finding.entity_type] += 1
                break
    if not votes or not checked:
        return None
    entity, count = votes.most_common(1)[0]
    return entity if count / checked >= VALUE_HIT_RATE else None


def profile_from_sample(
    rows: list[dict[str, Any]], detector: Detector, *, name: str = "dataset"
) -> ProfileResult:
    """Profile de-identified (or not — DPS does not trust it) sample rows into an LLM-safe shape."""
    if not rows:
        raise SchemaError("sample_empty", "the sample has no rows")
    if len(rows) > MAX_SAMPLE_ROWS:
        raise SchemaError(
            "sample_too_large", f"samples are limited to {MAX_SAMPLE_ROWS} rows", status=413
        )
    flats = [_flatten(row) for row in rows]
    keys: list[str] = []
    for flat in flats:
        keys += [k for k in flat if k not in keys]
    columns = {key: [flat.get(key) for flat in flats] for key in keys}
    fields: list[FieldProfile] = []
    stats: dict[str, FieldStats] = {}
    for index, (key, values) in enumerate(columns.items(), start=1):
        sensitive_name = bool(detector.detect(text=key).findings)
        fname = (
            f"field_{index}"
            if sensitive_name
            else ".".join(_ident(p, index) for p in key.split("."))
        )
        present = [v for v in values if v is not None and v != ""]
        ftype = _infer_type(present)
        entity = None if sensitive_name else _structural(key.split(".")[-1])
        source: Literal["structural", "values", "schema", "none"] = (
            "structural" if entity else "none"
        )
        if entity is None and ftype in ("string", "integer", "date"):
            entity = _value_entity(present, detector)
            source = "values" if entity else "none"
        profile = FieldProfile(
            name=fname,
            type=ftype,
            null_rate=round(1 - len(present) / len(values), 3),
            nullable=len(present) < len(values),
            entity_type=entity,
            entity_source=source,
        )
        st = FieldStats()
        if present:
            profile.distinct_ratio = round(
                len({json.dumps(v, default=str) for v in present}) / len(present), 3
            )
        strings = [str(v) for v in present if isinstance(v, str)]
        if strings and ftype in ("string", "unknown"):
            profile.shapes = [s for s, _ in Counter(value_shape(v) for v in strings).most_common(3)]
            profile.length = {"min": min(map(len, strings)), "max": max(map(len, strings))}
        numbers = [
            float(v) for v in present if isinstance(v, int | float) and not isinstance(v, bool)
        ]
        if numbers and ftype in ("integer", "number"):
            ordered = sorted(numbers)
            profile.magnitude = _magnitude(ordered[0], ordered[-1], ftype == "integer")
            st.minimum, st.maximum = ordered[0], ordered[-1]
            st.median = statistics.median(ordered)
            st.p90 = ordered[min(len(ordered) - 1, int(0.9 * len(ordered)))]
            st.decimals = _decimals(numbers) if ftype == "number" else 0
        if ftype == "boolean":
            st.true_rate = sum(1 for v in present if v is True) / len(present)
        if ftype in ("date", "datetime") and strings:
            st.date_min, st.date_max = min(strings), max(strings)
            if entity != "DATE_OF_BIRTH":
                profile.year_range = [int(st.date_min[:4]), int(st.date_max[:4])]
        if entity is None and ftype == "string" and strings:
            counts = Counter(strings)
            if (
                len(counts) <= ENUM_MAX
                and all(_SAFE_LABEL.fullmatch(v) for v in counts)
                and all(n >= ENUM_MIN_COUNT for n in counts.values())
                and not any(detector.detect(text=v).findings for v in counts)
            ):
                profile.enum = sorted(counts)
                st.enum_counts = dict(counts)
        if entity is not None or sensitive_name:
            st.originals = [str(v) for v in present]
        fields.append(profile)
        stats[fname] = st
    structure = StructureProfile(source="sample", name=name, fields=fields, rows_sampled=len(rows))
    return ProfileResult(profile=structure, stats=stats)


# -- proposers ------------------------------------------------------------------------------------
def _template(shape: str) -> str:
    out: list[str] = []
    for ch in shape:
        if ch == "x":
            out.append("a")
        elif ch == "X":
            out.append("A")
        elif ch == "9":
            out.append("9")
        else:
            out.append("\\" + ch if ch in _PATTERN_LITERALS else ch)
    return "".join(out)


def _heuristic_field(p: FieldProfile, s: FieldStats, rows_sampled: int | None) -> FieldSpec:
    null_rate = round(min(p.null_rate or 0.0, 0.95), 2) if p.nullable else 0.0
    base: dict[str, Any] = {"name": p.name, "null_rate": null_rate, "entity_type": p.entity_type}
    if p.entity_type in DEDICATED_ENTITIES:
        return FieldSpec(**base, generator="entity", params={"entity_type": p.entity_type})
    if p.primary_key and p.type == "integer":
        return FieldSpec(
            **{**base, "null_rate": 0.0}, generator="sequence", params={"start": 1}, unique=True
        )
    if p.type == "uuid":
        return FieldSpec(**base, generator="uuid", unique=p.primary_key)
    if p.type == "boolean":
        return FieldSpec(
            **base,
            generator="boolean",
            params={"p_true": round(s.true_rate if s.true_rate is not None else 0.5, 2)},
        )
    if p.type == "integer":
        lo = int(_sig2(s.minimum)) if s.minimum is not None else 0
        hi = int(_sig2(s.maximum)) if s.maximum is not None else 1000
        return FieldSpec(
            **base, generator="integer", params={"min": min(lo, hi), "max": max(lo, hi)}
        )
    if p.type == "number":
        low = _sig2(s.minimum) if s.minimum is not None else 0.0
        high = _sig2(s.maximum) if s.maximum is not None else 1000.0
        scale = max(s.decimals, 0) or 2
        if (
            s.minimum is not None
            and s.minimum >= 0
            and s.median
            and s.p90
            and s.median > 0
            and s.p90 / s.median > 3
        ):
            sigma = round(math.log(s.p90 / s.median) / 1.2816, 3)
            return FieldSpec(
                **base,
                generator="lognormal",
                params={
                    "mu": round(math.log(s.median), 3),
                    "sigma": sigma,
                    "min": 0,
                    "max": high,
                    "scale": scale,
                },
            )
        return FieldSpec(
            **base,
            generator="decimal",
            params={"min": min(low, high), "max": max(low, high), "scale": scale},
        )
    if p.type in ("date", "datetime"):
        first = int((s.date_min or "")[:4] or (p.year_range or [2015])[0])
        last = int((s.date_max or "")[:4] or (p.year_range or [0, date.today().year])[-1])
        if p.type == "date":
            return FieldSpec(
                **base, generator="date", params={"start": f"{first}-01-01", "end": f"{last}-12-31"}
            )
        return FieldSpec(
            **base,
            generator="datetime",
            params={
                "start": f"{first}-01-01T00:00:00+00:00",
                "end": f"{last}-12-31T23:59:59+00:00",
            },
        )
    if p.enum:
        weights = [s.enum_counts.get(v, 1) for v in p.enum]
        return FieldSpec(**base, generator="choice", params={"values": p.enum, "weights": weights})
    if p.shapes:
        unique = p.primary_key or (p.distinct_ratio == 1.0 and (rows_sampled or 0) >= 20)
        return FieldSpec(
            **base, generator="pattern", params={"template": _template(p.shapes[0])}, unique=unique
        )
    if p.entity_type in ENTITY_GENERATORS:
        return FieldSpec(**base, generator="entity", params={"entity_type": p.entity_type})
    longest = (p.length or {}).get("max", 60)
    words_max = max(1, min(200, longest // 6))
    return FieldSpec(
        **base,
        generator="text",
        params={"words_min": max(1, words_max // 3), "words_max": words_max},
    )


def heuristic_spec(result: ProfileResult, *, locale: str) -> GeneratorSpec:
    """The rule-based proposal (no LLM): deterministic, uses the private statistics."""
    fields = [
        _heuristic_field(p, result.stats.get(p.name, FieldStats()), result.profile.rows_sampled)
        for p in result.profile.fields
    ]
    name = _NOT_IDENT.sub("_", result.profile.name)[:64] or "dataset"
    return GeneratorSpec(
        name=name,
        locale=locale,
        fields=fields,
        source="heuristic",
        notes=["proposed by DPS rules (no LLM)"],
    )


def render_prompt(profile: StructureProfile) -> list[dict[str, str]]:
    """The only messages the gateway ever receives for a spec proposal."""
    body = json.dumps(profile.model_dump(mode="json", exclude_none=True), sort_keys=True)
    return [{"role": "system", "content": SPEC_SYSTEM}, {"role": "user", "content": body}]


def profile_originals(result: ProfileResult) -> list[str]:
    """Every sample value of a field DPS judged sensitive (safe enum labels excluded): the leak
    guard checks the prompt against them before any call. The profile carries no raw values by
    construction; this is defence in depth."""
    allowed = {label for p in result.profile.fields for label in (p.enum or [])}
    out: list[str] = []
    for st in result.stats.values():
        out += [v for v in st.originals if v not in allowed]
    return out


def merge_llm_spec(text: str, heuristic: GeneratorSpec, result: ProfileResult) -> GeneratorSpec:
    """Parse the model's reply; keep its choices only for non-sensitive fields (a model cannot
    downgrade a detected SSN to an integer); invalid constraints are dropped with a note."""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object in the reply")
    data = json.loads(text[start : end + 1])
    proposed = {
        f["name"]: f
        for f in data.get("fields", [])
        if isinstance(f, dict) and isinstance(f.get("name"), str)
    }
    sensitive = {p.name for p in result.profile.fields if p.entity_type}
    fields: list[FieldSpec] = []
    for fallback in heuristic.fields:
        candidate = proposed.get(fallback.name)
        if candidate is None or fallback.name in sensitive:
            fields.append(fallback)
            continue
        try:
            fields.append(
                FieldSpec.model_validate({**candidate, "name": fallback.name, "entity_type": None})
            )
        except ValidationError:
            fields.append(fallback)
    notes = [str(n)[:200] for n in (data.get("notes") or [])][:20]
    base = {
        "name": heuristic.name,
        "locale": heuristic.locale,
        "fields": [f.model_dump() for f in fields],
        "source": "llm",
        "notes": notes,
    }
    try:
        return GeneratorSpec.model_validate({**base, "constraints": data.get("constraints") or []})
    except ValidationError:
        return GeneratorSpec.model_validate(
            {**base, "notes": [*notes, "llm constraints dropped (invalid)"][:50]}
        )


async def propose_spec(
    result: ProfileResult, gateway: LlmGateway | None, *, locale: str, use_llm: bool = True
) -> tuple[GeneratorSpec, dict[str, Any]]:
    """A generator spec for the profile: the model's (validated, merged) when a real provider is
    configured and the leak guard passes, else the heuristic one. ``info`` is log-safe."""
    info: dict[str, Any] = {"llm_calls": 0, "source": "heuristic", "fallback": None}
    heuristic = heuristic_spec(result, locale=locale)
    if (
        not use_llm
        or gateway is None
        or gateway.provider in ("", "stub")
        or not gateway.available()
    ):
        return heuristic, info
    messages = render_prompt(result.profile)
    try:
        assert_placeholder_safe(messages[1]["content"], profile_originals(result))
    except LeakToModelError:
        info["fallback"] = "leak_guard"
        return heuristic, info
    info["llm_calls"] = 1
    try:
        reply = await gateway.complete(
            messages, temperature=0.2, max_tokens=4096, purpose="genspec"
        )
        spec = merge_llm_spec(reply.text, heuristic, result)
    except (LlmUnavailable, LlmCallError) as exc:
        info["fallback"] = type(exc).__name__
        return heuristic, info
    except (ValueError, ValidationError):
        info["fallback"] = "llm_invalid_spec"
        return heuristic, info
    info["source"] = "llm"
    return spec, info


def verify_against_sample(rows: list[dict[str, Any]], result: ProfileResult) -> Verification:
    """Leakage sweep of generated rows against the sample's sensitive values (M0 verifier)."""
    originals: list[tuple[Finding, str]] = []
    for profile in result.profile.fields:
        stats = result.stats.get(profile.name)
        if stats is None or not stats.originals:
            continue
        entity = profile.entity_type or "BUSINESS_SENSITIVE"
        finding = Finding(
            locator=Locator(path=f"$.{profile.name}"),
            entity_type=entity,
            score=1.0,
            detectors=["sample"],
            sensitivity=entity_info(entity).sensitivity,
        )
        originals += [(finding, value) for value in stats.originals]
    return verify_output(originals, rows)
```

- [ ] **Step 1: Write the failing tests** — `tests/test_schema_profile.py`:
```python
from __future__ import annotations

import json
from typing import Any

import pytest

from dps.config import Settings
from dps.engine.detect.ensemble import Detector, DetectorConfig
from dps.engine.shapes import value_shape
from dps.engine.synth.schema_gen import SchemaError, execute_spec
from dps.engine.synth.schema_profile import (
    parse_ddl,
    profile_from_ddl,
    profile_from_json_schema,
    profile_from_sample,
    propose_spec,
    render_prompt,
    verify_against_sample,
)
from dps.engine.verify import normalise
from dps.llm.gateway import LlmGateway, LlmResult

DETECTOR = Detector(DetectorConfig())
SAMPLE: list[dict[str, Any]] = [
    {
        "customer_id": f"CUST-{884120 + i}",
        "full_name": name,
        "email": email,
        "ssn": ssn,
        "status": status,
        "balance": balance,
        "opened": opened,
        "vip": i % 3 == 0,
    }
    for i, (name, email, ssn, status, balance, opened) in enumerate(
        [
            (
                "Ananya Krishnan",
                "ananya.k@example-mail.in",
                "123-45-6789",
                "ACTIVE",
                1200.50,
                "2019-03-14",
            ),
            (
                "Rohan Desai",
                "rohan.d@example-mail.in",
                "234-56-7890",
                "ACTIVE",
                85000.00,
                "2020-07-01",
            ),
            ("Priya Nair", "priya.n@example-mail.in", "345-67-8901", "CLOSED", 15.75, "2018-11-30"),
            (
                "Rajesh Iyer",
                "rajesh.i@example-mail.in",
                "456-78-9012",
                "CLOSED",
                430.00,
                "2021-01-15",
            ),
            ("Meera Rao", "meera.r@example-mail.in", "567-89-0123", "ACTIVE", 999.99, "2017-05-05"),
            (
                "Arjun Mehta",
                "arjun.m@example-mail.in",
                "678-90-1234",
                "FROZEN",
                77.10,
                "2022-09-09",
            ),
        ]
    )
]
DDL = """
-- customers
CREATE TABLE IF NOT EXISTS crm."CUSTOMERS" (
  "CUSTOMER_ID" NUMBER(12,0) NOT NULL,
  full_name VARCHAR2(120 CHAR),
  email varchar(254),
  dob DATE,
  balance NUMBER(12,2),
  status CHAR(1) DEFAULT 'A', /* single-letter code, commas, inside (a, b) */
  created_at TIMESTAMP(6) WITH TIME ZONE,
  CONSTRAINT pk_customers PRIMARY KEY ("CUSTOMER_ID")
);
"""


class RecordingGateway(LlmGateway):
    """A configured provider whose replies are scripted and whose prompts are kept."""

    def __init__(self, reply: str) -> None:
        super().__init__(
            Settings(
                _env_file=None,
                llm_provider="stellar",
                stellar_base_url="https://stellar.test",
                coin_url="https://coin.test",
            )
        )
        self.reply = reply
        self.messages: list[list[dict[str, str]]] = []

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.2,
        max_tokens: int = 512,
        purpose: str = "rewrite",
    ) -> LlmResult:
        self.messages.append(messages)
        return LlmResult(text=self.reply, provider="stellar", model="test")


def test_value_shape() -> None:
    assert value_shape("AB-1234") == "XX-9999"
    assert value_shape("ananya.k@x.in") == "xxxxxx.x@x.xx"
    assert len(value_shape("x" * 40)) == 24


def test_json_schema_profile() -> None:
    schema = {
        "type": "object",
        "required": ["id"],
        "properties": {
            "id": {"type": "integer", "minimum": 1, "maximum": 99999},
            "email": {"type": "string", "format": "email"},
            "dob": {"type": "string", "format": "date"},
            "status": {"type": "string", "enum": ["ACTIVE", "CLOSED"]},
            "amount": {"type": ["number", "null"], "minimum": 0, "maximum": 25000},
            "address": {
                "type": "object",
                "properties": {"city": {"type": "string", "maxLength": 40}},
            },
            "tags": {"type": "array"},
        },
    }
    result = profile_from_json_schema(schema)
    by = {f.name: f for f in result.profile.fields}
    assert set(by) == {"id", "email", "dob", "status", "amount", "address.city"}
    assert by["email"].entity_type == "EMAIL" and by["dob"].entity_type == "DATE_OF_BIRTH"
    assert (
        by["status"].enum == ["ACTIVE", "CLOSED"]
        and by["id"].nullable is False
        and by["amount"].nullable
    )
    assert by["amount"].magnitude == {
        "min_order": 0,
        "max_order": 4,
        "negative": False,
        "integer": False,
    }
    with pytest.raises(SchemaError):
        profile_from_json_schema({"type": "array"})


def test_ddl_parsing_and_profile() -> None:
    table, columns = parse_ddl(DDL)
    assert table == "CUSTOMERS"
    by = {c.name: c for c in columns}
    assert list(by) == [
        "CUSTOMER_ID",
        "full_name",
        "email",
        "dob",
        "balance",
        "status",
        "created_at",
    ]
    assert by["CUSTOMER_ID"].primary_key and not by["CUSTOMER_ID"].nullable
    assert (by["full_name"].length, by["balance"].scale, by["created_at"].type_name) == (
        120,
        2,
        "timestamp",
    )
    profile = {f.name: f for f in profile_from_ddl(DDL).profile.fields}
    assert profile["CUSTOMER_ID"].type == "integer" and profile["balance"].type == "number"
    assert profile["dob"].type == "date" and profile["created_at"].type == "datetime"
    assert profile["full_name"].entity_type == "PERSON_NAME"
    for bad in ("DROP TABLE x", "CREATE TABLE t (a int"):
        with pytest.raises(SchemaError) as exc:
            parse_ddl(bad)
        assert exc.value.code == "ddl_unparsed"


def test_sample_profile_is_llm_safe() -> None:
    result = profile_from_sample(SAMPLE, DETECTOR)
    by = {f.name: f for f in result.profile.fields}
    assert by["full_name"].entity_type == "PERSON_NAME" and by["email"].entity_type == "EMAIL"
    assert by["ssn"].entity_type == "SSN" and by["ssn"].entity_source in ("structural", "values")
    assert by["status"].enum is None  # FROZEN occurs once: not a safe label set
    assert by["customer_id"].shapes == ["XXXX-999999"]
    prompt = normalise(render_prompt(result.profile)[1]["content"])
    for row in SAMPLE:
        for key in ("full_name", "email", "ssn", "customer_id"):
            assert normalise(str(row[key])) not in prompt
    assert "120050" not in prompt and "85000" not in prompt


def test_safe_enums_are_kept() -> None:
    rows = [dict(r, status=("ACTIVE" if i % 2 else "CLOSED")) for i, r in enumerate(SAMPLE)]
    by = {f.name: f for f in profile_from_sample(rows, DETECTOR).profile.fields}
    assert by["status"].enum == ["ACTIVE", "CLOSED"]


def test_sensitive_field_names_are_aliased() -> None:
    result = profile_from_sample(
        [{"ananya.k@example-mail.in": "x1"}, {"ananya.k@example-mail.in": "x2"}], DETECTOR
    )
    assert [f.name for f in result.profile.fields] == ["field_1"]
    assert "ananya" not in render_prompt(result.profile)[1]["content"]


async def test_heuristic_spec_from_sample_executes_without_leaks() -> None:
    result = profile_from_sample(SAMPLE, DETECTOR)
    spec, info = await propose_spec(result, None, locale="en_IN")
    assert (
        info == {"llm_calls": 0, "source": "heuristic", "fallback": None}
        and spec.source == "heuristic"
    )
    by = {f.name: f for f in spec.fields}
    assert by["full_name"].generator == "entity" and by["ssn"].params == {"entity_type": "SSN"}
    assert (
        by["customer_id"].generator == "pattern"
        and by["customer_id"].params["template"] == "AAAA-999999"
    )
    assert by["balance"].generator in ("decimal", "lognormal") and by["vip"].generator == "boolean"
    assert by["opened"].generator == "date"
    rows = execute_spec(spec, rows=50, seed=bytes(32)).rows
    assert verify_against_sample(rows, result).passed


async def test_llm_proposal_sees_the_profile_only() -> None:
    reply = json.dumps(
        {
            "fields": [
                {
                    "name": "balance",
                    "generator": "lognormal",
                    "params": {"mu": 6.5, "sigma": 1.2, "min": 0, "scale": 2},
                },
                {"name": "ssn", "generator": "integer", "params": {"min": 1, "max": 9}},
            ],
            "constraints": [],
            "notes": ["balances are right-skewed"],
        }
    )
    fence = "`" * 3  # a fenced reply, as models often send
    gateway = RecordingGateway(f"{fence}json\n{reply}\n{fence}")
    result = profile_from_sample(SAMPLE, DETECTOR)
    spec, info = await propose_spec(result, gateway, locale="en_IN")
    assert info["source"] == "llm" and info["llm_calls"] == 1 and spec.source == "llm"
    by = {f.name: f for f in spec.fields}
    assert by["balance"].generator == "lognormal"
    assert by["ssn"].generator == "entity"  # a model cannot downgrade a detected SSN
    sent = normalise(json.dumps(gateway.messages))
    assert all(
        normalise(r["email"]) not in sent and normalise(r["ssn"]) not in sent for r in SAMPLE
    )


async def test_invalid_llm_replies_fall_back() -> None:
    result = profile_from_sample(SAMPLE, DETECTOR)
    spec, info = await propose_spec(result, RecordingGateway("not json at all"), locale="en_US")
    assert spec.source == "heuristic" and info["fallback"] == "llm_invalid_spec"


async def test_stub_provider_never_calls() -> None:
    gateway = LlmGateway(Settings(_env_file=None, llm_provider="stub"))
    _, info = await propose_spec(profile_from_sample(SAMPLE, DETECTOR), gateway, locale="en_US")
    assert info["llm_calls"] == 0 and info["source"] == "heuristic"


async def test_leak_guard_blocks_the_call() -> None:
    rows = [
        {"full_name": "Status", "status": "ACTIVE"},
        {"full_name": "Status", "status": "CLOSED"},
    ]
    gateway = RecordingGateway("{}")
    spec, info = await propose_spec(profile_from_sample(rows, DETECTOR), gateway, locale="en_US")
    assert info["fallback"] == "leak_guard" and info["llm_calls"] == 0 and gateway.messages == []
    assert spec.source == "heuristic"
```

- [ ] **Step 2: Run to confirm failure** — `pytest -q tests/test_schema_profile.py` → FAIL (`ModuleNotFoundError: dps.engine.shapes`).

- [ ] **Step 3: Implement** `shapes.py` and `schema_profile.py` as above.

- [ ] **Step 4: Gates** — `pytest -q tests/test_schema_profile.py` → `11 passed` (verified while writing against the M0 plan's detector, structural dictionary, gateway and verifier code). Full gates green.

- [ ] **Step 5: Commit**
```bash
git add dps/engine/shapes.py dps/engine/synth/schema_profile.py tests/test_schema_profile.py
git commit -m "feat(synth): value-free structure profiles; heuristic and LLM generator-spec proposers with a leak guard" -- dps/engine/shapes.py dps/engine/synth/schema_profile.py tests/test_schema_profile.py
```

---
### Task 13: Schema API — generator spec and synthesis routes

**Files:**
- Create: `dps/layers/api/routers/schema.py`, `tests/test_api_schema.py`
- Modify: `dps/app.py` (`app.include_router(schema.router)`)

**HTTP routes (contract §9 M1, plus additive inputs/outputs flagged in "Open contract questions"):**

| Route | Scope | Request | 200 response |
|---|---|---|---|
| `POST /api/v1/schema/generator-spec` | `synthesize` | exactly one of `schema` (JSON Schema object), `ddl` (≤ 100 000 chars), `sample` (≤ 1 000 rows, ≤ `max_items` values); optional `name`, `locale`, `use_llm` (default true) | `{spec: GeneratorSpec, spec_hash, profile: StructureProfile, source: "llm"\|"heuristic", fallback, llm: {calls, raw_pii_sent: 0}, trace_id}` |
| `POST /api/v1/schema/synthesize` | `synthesize` | exactly one of `spec` \| `schema` \| `ddl` \| `sample`; `rows` (1..`schema_max_rows`, default 100); optional `seed` (an M0 `SynthesisSeed`), `format` `json`\|`csv`, `name`, `locale`, `use_llm` | `{rows \| csv, spec, spec_hash, synthesis_seed, report: SchemaReport, trace_id}` — the spec actually executed is always returned, so it can be stored, reviewed and replayed with the same seed |

Codes: 422 `exactly_one_input`, `schema_unsupported`, `ddl_unparsed`, `sample_empty`, `unique_exhausted`, `invalid_seed`, `leakage`, `invalid_rows`; 413 `too_many_rows`, `sample_too_large`, `ddl_too_large`; spec validation errors are FastAPI 422s naming the field and parameter.

`dps/layers/api/routers/schema.py`:
```python
"""POST /api/v1/schema/generator-spec and /api/v1/schema/synthesize (spec §5.6, contract §9)."""

from __future__ import annotations

import asyncio
import time
from typing import Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from dps.auth import require_scope
from dps.engine.detect.ensemble import Detector, DetectorConfig
from dps.engine.synth.schema_gen import GeneratorSpec, SchemaError, execute_spec, rows_to_csv
from dps.engine.synth.schema_profile import (
    ProfileResult,
    StructureProfile,
    profile_from_ddl,
    profile_from_json_schema,
    profile_from_sample,
    propose_spec,
    verify_against_sample,
)
from dps.engine.synth.surrogates import (
    SynthesisSeed,
    generate_synthesis_seed,
    seed_bytes,
    seed_from_caller,
)
from dps.engine.verify import Verification
from dps.layers.api.common import run_engine
from dps.ratelimit import enforce_rate_limit

router = APIRouter(prefix="/api/v1/schema", tags=["schema"])


class SchemaSource(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True, hide_input_in_errors=True)
    json_schema: dict[str, Any] | None = Field(default=None, alias="schema")
    ddl: str | None = Field(default=None, max_length=100_000)
    sample: list[dict[str, Any]] | None = Field(default=None, max_length=1000)
    name: str = Field(default="dataset", pattern=r"^[A-Za-z0-9_.-]{1,64}$")
    locale: str | None = None
    use_llm: bool = True


class GeneratorSpecRequest(SchemaSource):
    pass


class SchemaSynthesizeRequest(SchemaSource):
    spec: GeneratorSpec | None = None
    rows: int = Field(default=100, ge=1)
    seed: dict[str, Any] | None = None
    format: Literal["json", "csv"] = "json"


class LlmUsage(BaseModel):
    calls: int = 0
    raw_pii_sent: int = 0


class GeneratorSpecOut(BaseModel):
    spec: GeneratorSpec
    spec_hash: str
    profile: StructureProfile
    source: str
    fallback: str | None = None
    llm: LlmUsage
    trace_id: str


class SchemaReport(BaseModel):
    mode: str = "schema.synthesize"
    rows: int
    fields: int
    generators_used: dict[str, int]
    nulls: int
    unique_retries: int
    spec_hash: str
    source: str
    fallback: str | None = None
    llm: LlmUsage
    verification: Verification | None = None
    duration_ms: float


class SchemaSynthesizeOut(BaseModel):
    rows: list[dict[str, Any]] | None = None
    csv: str | None = None
    spec: GeneratorSpec
    spec_hash: str
    synthesis_seed: SynthesisSeed
    report: SchemaReport
    trace_id: str = ""


def _exactly_one(
    body: SchemaSource, spec: GeneratorSpec | None = None, *, allow_spec: bool = False
) -> None:
    given = sum(x is not None for x in (body.json_schema, body.ddl, body.sample, spec))
    if given != 1:
        options = "spec, schema, ddl or sample" if allow_spec else "schema, ddl or sample"
        raise SchemaError("exactly_one_input", f"give exactly one of {options}")


async def _profile(body: SchemaSource, request: Request) -> ProfileResult:
    settings, engine = request.app.state.settings, request.app.state.engine
    if body.json_schema is not None:
        return profile_from_json_schema(body.json_schema, name=body.name)
    if body.ddl is not None:
        return profile_from_ddl(body.ddl, name=None if body.name == "dataset" else body.name)
    assert body.sample is not None
    if sum(len(row) for row in body.sample) > settings.max_items:
        raise SchemaError(
            "sample_too_large", f"samples are limited to {settings.max_items} values", status=413
        )
    detector = Detector(
        DetectorConfig(
            min_score=settings.min_score,
            locale=body.locale or settings.default_locale,
            ner_enabled=engine.ner is not None,
        ),
        ner=engine.ner,
    )
    return await asyncio.to_thread(profile_from_sample, body.sample, detector, name=body.name)


@router.post(
    "/generator-spec",
    response_model=GeneratorSpecOut,
    dependencies=[Depends(require_scope("synthesize")), Depends(enforce_rate_limit)],
)
async def generator_spec(body: GeneratorSpecRequest, request: Request) -> GeneratorSpecOut:
    settings, engine = request.app.state.settings, request.app.state.engine

    async def call() -> tuple[GeneratorSpec, ProfileResult, dict[str, Any]]:
        _exactly_one(body)
        result = await _profile(body, request)
        spec, info = await propose_spec(
            result,
            engine.gateway,
            locale=body.locale or settings.default_locale,
            use_llm=body.use_llm,
        )
        return spec, result, info

    (spec, result, info), trace_id = await run_engine(
        request, mode="schema.generator-spec", policy_name="-", call=call
    )
    return GeneratorSpecOut(
        spec=spec,
        spec_hash=spec.spec_hash(),
        profile=result.profile,
        source=info["source"],
        fallback=info["fallback"],
        llm=LlmUsage(calls=info["llm_calls"]),
        trace_id=trace_id,
    )


@router.post(
    "/synthesize",
    response_model=SchemaSynthesizeOut,
    dependencies=[Depends(require_scope("synthesize")), Depends(enforce_rate_limit)],
)
async def schema_synthesize(body: SchemaSynthesizeRequest, request: Request) -> SchemaSynthesizeOut:
    settings, engine = request.app.state.settings, request.app.state.engine

    async def call() -> SchemaSynthesizeOut:
        started = time.perf_counter()
        _exactly_one(body, body.spec, allow_spec=True)
        if body.rows > settings.schema_max_rows:
            raise SchemaError(
                "too_many_rows", f"at most {settings.schema_max_rows} rows per request", status=413
            )
        result: ProfileResult | None = None
        if body.spec is not None:
            spec, info = body.spec, {"llm_calls": 0, "source": "caller", "fallback": None}
        else:
            result = await _profile(body, request)
            spec, info = await propose_spec(
                result,
                engine.gateway,
                locale=body.locale or settings.default_locale,
                use_llm=body.use_llm,
            )
        try:
            seed = seed_from_caller(body.seed) if body.seed else generate_synthesis_seed()
        except ValueError:
            raise SchemaError("invalid_seed", "the synthesis seed is not valid") from None
        generated = await asyncio.to_thread(
            execute_spec, spec, rows=body.rows, seed=seed_bytes(seed)
        )
        verification: Verification | None = None
        if result is not None and result.profile.source == "sample":
            verification = verify_against_sample(generated.rows, result)
            if not verification.passed:
                raise SchemaError(
                    "leakage", "generated rows matched a sample value; nothing was returned"
                )
        report = SchemaReport(
            rows=len(generated.rows),
            fields=len(spec.fields),
            generators_used=generated.generators_used,
            nulls=generated.nulls,
            unique_retries=generated.unique_retries,
            spec_hash=generated.spec_hash,
            source=info["source"],
            fallback=info["fallback"],
            llm=LlmUsage(calls=info["llm_calls"]),
            verification=verification,
            duration_ms=round((time.perf_counter() - started) * 1000, 1),
        )
        csv_text = rows_to_csv(generated.rows, spec) if body.format == "csv" else None
        return SchemaSynthesizeOut(
            rows=None if csv_text is not None else generated.rows,
            csv=csv_text,
            spec=spec,
            spec_hash=generated.spec_hash,
            synthesis_seed=seed,
            report=report,
        )

    out, trace_id = await run_engine(request, mode="schema.synthesize", policy_name="-", call=call)
    return out.model_copy(update={"trace_id": trace_id})
```

- [ ] **Step 1: Write the failing tests** — `tests/test_api_schema.py`:
```python
from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from dps.app import create_app
from dps.audit import MemoryAuditSink
from dps.config import Settings

KEYS = [
    {"name": "full", "key": "dps_full", "tenant_ids": ["*"], "scopes": ["*"]},
    {"name": "ro", "key": "dps_ro", "tenant_ids": ["retail"], "scopes": ["detect"]},
]
FULL, RO = {"X-API-KEY": "dps_full"}, {"X-API-KEY": "dps_ro"}
DDL = (
    "CREATE TABLE customers (customer_id INTEGER PRIMARY KEY, full_name VARCHAR(80), email VARCHAR(120), "
    "balance NUMERIC(12,2), opened DATE)"
)
SPEC: dict[str, Any] = {
    "name": "cards",
    "fields": [
        {
            "name": "id",
            "generator": "sequence",
            "params": {"prefix": "C-", "width": 4},
            "unique": True,
        },
        {"name": "holder", "generator": "entity", "params": {"entity_type": "PERSON_NAME"}},
        {"name": "pan", "generator": "entity", "params": {"entity_type": "CARD_PAN"}},
        {"name": "limit", "generator": "choice", "params": {"values": [500, 1000, 5000]}},
    ],
}
SAMPLE = [
    {"full_name": n, "email": e, "status": s, "balance": b}
    for n, e, s, b in [
        ("Ananya Krishnan", "ananya.k@example-mail.in", "ACTIVE", 1200.5),
        ("Rohan Desai", "rohan.d@example-mail.in", "CLOSED", 85000.0),
        ("Priya Nair", "priya.n@example-mail.in", "ACTIVE", 15.75),
        ("Rajesh Iyer", "rajesh.i@example-mail.in", "CLOSED", 430.0),
    ]
]


@pytest.fixture
def client() -> TestClient:
    settings = Settings(
        _env_file=None,
        env="local",
        auth_enabled=True,
        api_keys=json.dumps(KEYS),
        llm_provider="stub",
        ner_enabled=False,
        rate_limit_enabled=False,
        schema_max_rows=500,
    )
    return TestClient(create_app(settings, audit_sink=MemoryAuditSink()))


def test_generator_spec_from_ddl(client: TestClient) -> None:
    r = client.post("/api/v1/schema/generator-spec", headers=FULL, json={"ddl": DDL})
    body = r.json()
    assert r.status_code == 200, r.text
    assert [f["name"] for f in body["spec"]["fields"]] == [
        "customer_id",
        "full_name",
        "email",
        "balance",
        "opened",
    ]
    assert body["spec"]["fields"][0]["generator"] == "sequence" and body["source"] == "heuristic"
    assert body["llm"] == {"calls": 0, "raw_pii_sent": 0} and len(body["spec_hash"]) == 64
    assert body["profile"]["source"] == "ddl"


def test_exactly_one_input(client: TestClient) -> None:
    both = client.post(
        "/api/v1/schema/generator-spec",
        headers=FULL,
        json={"ddl": DDL, "schema": {"type": "object", "properties": {"a": {"type": "string"}}}},
    )
    assert both.status_code == 422 and both.json()["code"] == "exactly_one_input"
    none = client.post("/api/v1/schema/generator-spec", headers=FULL, json={})
    assert none.status_code == 422 and none.json()["code"] == "exactly_one_input"
    mixed = client.post("/api/v1/schema/synthesize", headers=FULL, json={"spec": SPEC, "ddl": DDL})
    assert mixed.status_code == 422 and mixed.json()["code"] == "exactly_one_input"


def test_synthesize_from_spec_is_reproducible(client: TestClient) -> None:
    first = client.post(
        "/api/v1/schema/synthesize", headers=FULL, json={"spec": SPEC, "rows": 20}
    ).json()
    again = client.post(
        "/api/v1/schema/synthesize",
        headers=FULL,
        json={"spec": SPEC, "rows": 20, "seed": first["synthesis_seed"]},
    ).json()
    assert (
        len(first["rows"]) == 20
        and first["rows"] == again["rows"]
        and first["spec_hash"] == again["spec_hash"]
    )
    assert first["report"]["source"] == "caller" and first["rows"][0]["id"] == "C-0001"


def test_synthesize_from_sample_returns_spec_and_csv(client: TestClient) -> None:
    r = client.post(
        "/api/v1/schema/synthesize",
        headers=FULL,
        json={"sample": SAMPLE, "rows": 30, "format": "csv"},
    )
    body = r.json()
    assert r.status_code == 200, r.text
    assert (
        body["rows"] is None
        and body["csv"].count("\n") == 31
        and body["spec"]["source"] == "heuristic"
    )
    assert body["report"]["verification"]["passed"] is True
    assert all(row["email"] not in body["csv"] for row in SAMPLE)


def test_limits_scopes_and_codes(client: TestClient) -> None:
    many = client.post("/api/v1/schema/synthesize", headers=FULL, json={"spec": SPEC, "rows": 501})
    assert many.status_code == 413 and many.json()["code"] == "too_many_rows"
    assert (
        client.post("/api/v1/schema/synthesize", headers=RO, json={"spec": SPEC}).status_code == 403
    )
    bad = client.post(
        "/api/v1/schema/synthesize",
        headers=FULL,
        json={"spec": {"fields": [{"name": "a", "generator": "teleport"}]}},
    )
    assert bad.status_code == 422
    ddl = client.post("/api/v1/schema/generator-spec", headers=FULL, json={"ddl": "DROP TABLE x"})
    assert ddl.status_code == 422 and ddl.json()["code"] == "ddl_unparsed"
    seed = client.post(
        "/api/v1/schema/synthesize",
        headers=FULL,
        json={"spec": SPEC, "seed": {"alg": "dps.synth.v1", "seed": "c2hvcnQ"}},
    )
    assert (
        seed.status_code == 422
        and seed.json()["code"] == "invalid_seed"
        and "c2hvcnQ" not in seed.text
    )
```

- [ ] **Step 2: Run to confirm failure** — `pytest -q tests/test_api_schema.py` → FAIL (404 for `/api/v1/schema/*`).

- [ ] **Step 3: Implement** the router and include it in `create_app` (after `documents.router`).

- [ ] **Step 4: Gates** — `pytest -q tests/test_api_schema.py` → `5 passed`; full gates green.

- [ ] **Step 5: Commit**
```bash
git add dps/layers/api/routers/schema.py dps/app.py tests/test_api_schema.py
git commit -m "feat(api): /api/v1/schema generator-spec and synthesize; the executed spec is returned as an artefact" -- dps/layers/api/routers/schema.py dps/app.py tests/test_api_schema.py
```

---

### Task 14: Entities route, recognizer test route, allow-lists, preset summaries

**Files:**
- Create: `dps/layers/api/routers/entities.py`, `tests/test_api_entities.py`
- Modify: `dps/engine/detect/custom.py`, `tests/test_custom.py`, `dps/engine/policy.py`, `tests/test_policy.py`, `dps/layers/api/routers/policies.py`, `dps/app.py`

**Interfaces introduced**

- `RecognizerSpec.allow_list: tuple[str, ...] = ()` — a DPS extension to Presidio's recognizer shape: exact matched texts that are never reported (seeded test fixtures). In `PolicyCustomDetector.__init__` keep `self._allow: dict[str, frozenset[str]] = {rec.name: frozenset(rec.allow_list) for rec in recognizers}`; in `scan`, skip any pattern or deny-list hit whose `text[m.start():m.end()]` is in `self._allow[rec.name]`. (Presidio's own `deny_list` keeps its meaning — values that are always flagged; the mockup's "deny list: values that never match" is this `allow_list`, see Open contract questions.)
- `dps/engine/policy.py`:
```python
#: Presets meant for the synthesize mode (all others are obfuscation presets).
PRESET_MODES: dict[str, str] = {"synthetic-realistic": "synthesize", "synthetic-strict": "synthesize"}
REVERSIBLE_METHODS = frozenset({"det_token", "fpe_digits", "fpe_text", "email", "phone", "date_shift"})


def preset_summaries() -> list[dict[str, Any]]:
    """``list_presets()`` plus version, config hash, intended mode and reversibility (console)."""
    out: list[dict[str, Any]] = []
    for item in list_presets():
        policy = load_preset(item["name"])
        mode = PRESET_MODES.get(policy.name, "obfuscate")
        reversible = mode == "obfuscate" and any(s.method in REVERSIBLE_METHODS for s in policy.treat.values())
        out.append({**item, "version": policy.version, "hash": policy.config_hash(), "mode": mode,
                    "reversible": reversible})
    return out
```
  `GET /api/v1/policies/presets` returns `preset_summaries()` (a superset of M0's `{name, description}`).
- Routes:

| Route | Scope | Request | 200 response |
|---|---|---|---|
| `GET /api/v1/entities` | `detect` | — | `{items: [{name, family, sensitivity, description, jurisdictions, builtin}], families: [str]}` (the live taxonomy incl. registered custom types) |
| `POST /api/v1/policies/test-recognizer` | `detect` | `{recognizer: RecognizerSpec (Presidio YAML shape + allow_list), text: ≤ 20 000 chars}` | `{matches: [{start, end, entity_type, score, evidence, context}], count, yaml}` — offsets only, the sample is neither stored nor echoed; `yaml` is the recognizer as Presidio-style YAML |

  Errors: 422 `invalid_recognizer` (validation message without inputs; any regex longer than 500 characters is refused — Python's `re` has no timeout, so the route bounds pattern and text size). The route never registers an entity tier (the spec's `sensitivity` is dropped before building the detector) — a test endpoint must not mutate the process-wide taxonomy.

`dps/layers/api/routers/entities.py`:
```python
"""GET /api/v1/entities and POST /api/v1/policies/test-recognizer (console Policies page)."""

from __future__ import annotations

from typing import Any

import yaml
from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from dps.auth import require_scope
from dps.engine.detect.custom import PolicyCustomDetector, RecognizerSpec
from dps.engine.taxonomy import TAXONOMY, Family, is_builtin_entity
from dps.layers.api.common import CodedHTTPException
from dps.layers.documents.models import validation_message
from dps.ratelimit import enforce_rate_limit

router = APIRouter(prefix="/api/v1", tags=["policies"])
MAX_PATTERN_CHARS = 500


class EntityOut(BaseModel):
    name: str
    family: str
    sensitivity: str
    description: str
    jurisdictions: list[str]
    builtin: bool


class EntitiesOut(BaseModel):
    items: list[EntityOut]
    families: list[str]


class RecognizerTestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    recognizer: dict[str, Any]
    text: str = Field(max_length=20_000)


class RecognizerMatch(BaseModel):
    start: int
    end: int
    entity_type: str
    score: float
    evidence: str
    context: bool


class RecognizerTestOut(BaseModel):
    matches: list[RecognizerMatch]
    count: int
    yaml: str


@router.get(
    "/entities", response_model=EntitiesOut, dependencies=[Depends(require_scope("detect"))]
)
def entities() -> EntitiesOut:
    items = sorted(TAXONOMY.values(), key=lambda e: (e.family.value, e.name))
    return EntitiesOut(
        items=[
            EntityOut(
                name=e.name,
                family=e.family.value,
                sensitivity=e.sensitivity.value,
                description=e.description,
                jurisdictions=list(e.jurisdictions),
                builtin=is_builtin_entity(e.name),
            )
            for e in items
        ],
        families=[f.value for f in Family],
    )


@router.post(
    "/policies/test-recognizer",
    response_model=RecognizerTestOut,
    dependencies=[Depends(require_scope("detect")), Depends(enforce_rate_limit)],
)
def test_recognizer(body: RecognizerTestRequest) -> RecognizerTestOut:
    try:
        spec = RecognizerSpec.model_validate(body.recognizer)
    except ValidationError as exc:
        raise CodedHTTPException(422, "invalid_recognizer", validation_message(exc)) from None
    if any(len(p.regex) > MAX_PATTERN_CHARS for p in spec.patterns):
        raise CodedHTTPException(
            422, "invalid_recognizer", f"patterns are limited to {MAX_PATTERN_CHARS} characters"
        )
    detector = PolicyCustomDetector([spec.model_copy(update={"sensitivity": None})], [])
    base = {p.name: p.score for p in spec.patterns}
    matches = [
        RecognizerMatch(
            start=h.start,
            end=h.end,
            entity_type=h.entity_type,
            score=h.score,
            evidence=h.evidence,
            context=h.evidence.startswith("custom-pattern:")
            and h.score > base.get(h.evidence.removeprefix("custom-pattern:"), h.score),
        )
        for h in sorted(detector.scan(body.text, None), key=lambda h: (h.start, h.end))
    ]
    document = {"recognizers": [spec.model_dump(mode="json", exclude_defaults=True)]}
    return RecognizerTestOut(
        matches=matches,
        count=len(matches),
        yaml=yaml.safe_dump(document, sort_keys=False, allow_unicode=True),
    )
```
Include in `create_app`: `app.include_router(entities.router)`. The M0 `policies.router` keeps its prefix `/api/v1/policies`; `/policies/test-recognizer` lives in `entities.py` (contract §1 lists `entities.py` as the M1 router).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_custom.py`:
```python
def test_allow_list_suppresses_exact_matches() -> None:
    rec = RecognizerSpec(name="emp", supported_entity="EMPLOYEE_ID",
                         patterns=(PatternSpecModel(name="emp", regex=r"\bEMP-\d{6}\b", score=0.9),),
                         allow_list=("EMP-000000",))
    text = "EMP-204417 escalated; fixture EMP-000000 is seeded"
    assert [text[h.start:h.end] for h in PolicyCustomDetector([rec], []).scan(text, None)] == ["EMP-204417"]
```
(import `PatternSpecModel`, `PolicyCustomDetector`, `RecognizerSpec` from `dps.engine.detect.custom` if the module does not already).

Append to `tests/test_policy.py`:
```python
def test_preset_summaries_carry_hash_mode_and_reversibility() -> None:
    items = {p["name"]: p for p in preset_summaries()}
    assert items["gdpr-pseudonymise"]["mode"] == "obfuscate" and items["gdpr-pseudonymise"]["reversible"] is True
    assert items["synthetic-strict"]["mode"] == "synthesize" and items["analytics-safe"]["reversible"] is False
    assert all(len(p["hash"]) == 64 and p["version"] >= 1 and p["description"] for p in items.values())
```

`tests/test_api_entities.py`:
```python
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from dps.app import create_app
from dps.audit import MemoryAuditSink
from dps.config import Settings
from dps.engine.taxonomy import entity_info

KEYS = [{"name": "ro", "key": "dps_ro", "tenant_ids": ["retail"], "scopes": ["detect"]}]
RO = {"X-API-KEY": "dps_ro"}
TEXT = (
    "Escalated by EMP-204417 (staff id) to the Pune branch. Fixture EMP-000000 is seeded. "
    "CHG-48213 also cites EMP-31882, which has five digits."
)
REC = {
    "name": "employee-id",
    "supported_entity": "EMPLOYEE_ID",
    "patterns": [{"name": "employee-id", "regex": r"\bEMP-\d{6}\b", "score": 0.85}],
    "context": ["staff id"],
    "allow_list": ["EMP-000000"],
}


@pytest.fixture
def client() -> TestClient:
    settings = Settings(
        _env_file=None,
        env="local",
        auth_enabled=True,
        api_keys=json.dumps(KEYS),
        llm_provider="stub",
        ner_enabled=False,
        rate_limit_enabled=False,
    )
    return TestClient(create_app(settings, audit_sink=MemoryAuditSink()))


def test_entities_lists_the_taxonomy(client: TestClient) -> None:
    body = client.get("/api/v1/entities", headers=RO).json()
    by = {i["name"]: i for i in body["items"]}
    assert len(by) >= 46 and by["SSN"]["sensitivity"] == "CRITICAL" and by["SSN"]["builtin"] is True
    assert by["SSN"]["family"] == "government_id" and "financial" in body["families"]


def test_recognizer_test_returns_offsets_only(client: TestClient) -> None:
    r = client.post(
        "/api/v1/policies/test-recognizer", headers=RO, json={"recognizer": REC, "text": TEXT}
    )
    body = r.json()
    assert r.status_code == 200 and body["count"] == 1
    match = body["matches"][0]
    assert (
        TEXT[match["start"] : match["end"]] == "EMP-204417"
        and match["entity_type"] == "EMPLOYEE_ID"
    )
    assert (
        "EMP-204417" not in r.text and "allow_list" in body["yaml"] and "EMP-000000" in body["yaml"]
    )


def test_invalid_recognizers_are_422_with_a_code(client: TestClient) -> None:
    broken = dict(REC, patterns=[{"name": "p", "regex": "(unclosed", "score": 0.5}])
    r = client.post(
        "/api/v1/policies/test-recognizer", headers=RO, json={"recognizer": broken, "text": "x"}
    )
    assert r.status_code == 422 and r.json()["code"] == "invalid_recognizer"
    huge = dict(REC, patterns=[{"name": "p", "regex": "a" * 501, "score": 0.5}])
    r = client.post(
        "/api/v1/policies/test-recognizer", headers=RO, json={"recognizer": huge, "text": "x"}
    )
    assert r.status_code == 422 and r.json()["code"] == "invalid_recognizer"


def test_recognizer_tests_never_register_entities(client: TestClient) -> None:
    rec = {
        "name": "t",
        "supported_entity": "TEMP_ENTITY_X",
        "sensitivity": "CRITICAL",
        "patterns": [{"name": "p", "regex": "Z{3}", "score": 0.9}],
    }
    assert (
        client.post(
            "/api/v1/policies/test-recognizer", headers=RO, json={"recognizer": rec, "text": "ZZZ"}
        ).json()["count"]
        == 1
    )
    assert entity_info("TEMP_ENTITY_X").sensitivity.value == "MEDIUM"


def test_presets_route_returns_summaries(client: TestClient) -> None:
    items = client.get("/api/v1/policies/presets", headers=RO).json()
    assert {"name", "description", "version", "hash", "mode", "reversible"} <= set(items[0])
```

- [ ] **Step 2: Run to confirm failure** — `pytest -q tests/test_api_entities.py tests/test_custom.py tests/test_policy.py` → FAIL (404 `/api/v1/entities`; `RecognizerSpec` has no `allow_list`; `preset_summaries` missing).

- [ ] **Step 3: Implement** the custom.py, policy.py, policies-router, entities-router and app changes.

- [ ] **Step 4: Gates** — `pytest -q tests/test_api_entities.py tests/test_custom.py tests/test_policy.py tests/test_api.py` → new tests `7 passed`, M0 tests unchanged; full gates green.

- [ ] **Step 5: Commit**
```bash
git add dps/layers/api/routers/entities.py dps/engine/detect/custom.py dps/engine/policy.py dps/layers/api/routers/policies.py dps/app.py tests/test_api_entities.py tests/test_custom.py tests/test_policy.py
git commit -m "feat(api): entity taxonomy and recognizer test routes; recognizer allow-lists; preset summaries" -- dps/layers/api/routers/entities.py dps/engine/detect/custom.py dps/engine/policy.py dps/layers/api/routers/policies.py dps/app.py tests/test_api_entities.py tests/test_custom.py tests/test_policy.py
```

---
### Task 15: Detector evaluation harness, labelled corpus, committed report

**Files:**
- Create: `dps/evaluation/__init__.py` (`"""Detector evaluation (spec §5.3): a labelled synthetic corpus and per-entity scoring."""`), `dps/evaluation/corpus.py`, `dps/evaluation/scoring.py`, `tools/eval_detectors.py`, `tests/test_eval_corpus.py`, `tests/test_eval_scoring.py`, `tests/test_eval_report.py`, `tests/eval/corpus/{us,ca,mx,in,uk,eu}.jsonl` (generated), `docs/technical/detector-eval-m1.json`, `docs/technical/detector-eval-m1.md` (generated)

**Interfaces introduced:** `CORPUS_VERSION`, `JURISDICTIONS` (US/CA/MX/IN/UK/EU → persona locales), `Label(path, start, end, entity_type, generator)`, `CorpusRecord(id, jurisdiction, locale, kind, text | data, labels)`, `ValueFactory(seed, jurisdiction).value(entity, record_no, slot_no) -> (value, generator)`, `generate_corpus(seed, records) -> dict[str, list[CorpusRecord]]`, `write_corpus`, `load_corpus(paths) -> (records, sha256)`; `Counts`, `TypeScore`, `EvalReport`, `score_record(record, findings) -> (counts by type, confusions)`, `evaluate(records, *, corpus_sha256, min_score=0.6, ner=False) -> EvalReport`, `render_markdown(report)`; CLI `tools/eval_detectors.py generate|run`.

**Behaviour:** the corpus is generated by the M0 surrogate engine (persona fields share one engine per record so they agree; every other value gets its own engine — one engine would repeat the same card in every record because a surrogate is a function of seed, type and template) plus seeded rule-valid generators where the surrogate is deliberately invalid or Faker is not seedable (verified while writing: Faker's es_MX `curp()`/`rfc()` use the global RNG, so CURP and RFC are built from stdnum check digits instead). Every checksum-validated value passes `checksums.validate`; template text carries no digits or addresses. Labels record their generator (`surrogate`, `faker`, `rule`). Generation is byte-identical across processes (verified with different `PYTHONHASHSEED`s). Scoring is strict on type with span IoU ≥ 0.5; the detector runs once at min score 0 and the threshold sweep filters its findings. The report holds numbers and type names only. The bank's own labelled samples use the same JSONL shape through `run --corpus <path>` and live under the git-ignored `tests/eval/private/`.

**What the first run measured (on the M0 plan's detector code, while writing this plan — the committed numbers come from the built M0 and may differ):** micro precision 1.000, micro recall 0.947 over 900 records / 3 983 labels at min score 0.6, NER off; every checksum type ≥ 0.98 precision and recall; `PERSON_NAME` recall 0.73 (free text needs the NER tier — JSON names are found structurally); two real detector defects surfaced: **IPv4 addresses at the end of a sentence are missed** (`IP_ADDRESS` recall 0.25: the M0 lookahead `(?![\d.])` rejects the full stop) and **a card number whose last 12 digits form a Verhoeff-valid Aadhaar is reported as `AADHAAR`** (2 of 343 cards: both hits are validated and the Aadhaar's 0.98 outranks the card's 0.95 in `resolve_overlaps`). Both are recorded in the report and raised as follow-ups (see Open contract questions); M1 does not change detector behaviour.

`dps/evaluation/corpus.py`:
```python
"""A labelled synthetic corpus per jurisdiction (spec §5.3) — no real data.

Values come from the M0 surrogate engine (personas, cards on a test IIN, Aadhaar-shaped numbers,
RFC 2606 e-mails …) and, for identifiers whose surrogates are deliberately *invalid* (never-issued
SSNs) or missing, from seeded Faker providers (US SSN/ITIN/EIN, EU IBANs) or check-digit arithmetic
(CURP, RFC, NINO, NHS) so every checksum-validated value is rule-valid — otherwise the evaluation would measure the validators, not the detectors. Each label
records which generator produced it. Records are text sentences or KYC-like JSON documents; every
inserted value is labelled, and template text carries no personal data.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import random
import re
from collections.abc import Iterable
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Literal

from faker import Faker
from pydantic import BaseModel, Field
from stdnum.mx import curp as mx_curp
from stdnum.mx import rfc as mx_rfc

from dps.engine.detect.checksums import validate
from dps.engine.synth.personas import seed_int
from dps.engine.synth.surrogates import SurrogateEngine

CORPUS_VERSION = "dps.evalcorpus.v1"
JURISDICTIONS: dict[str, str] = {
    "US": "en_US",
    "CA": "en_CA",
    "MX": "es_MX",
    "IN": "en_IN",
    "UK": "en_GB",
    "EU": "en_GB",
}
EU_IBAN_LOCALES = ("de_DE", "fr_FR", "es_ES", "nl_NL", "it_IT")
_SLOT = re.compile(r"\{([A-Z][A-Z_]*)(?:#(\d))?\}")
_NINO_FIRST = "ABCEGHJKLMNOPRSTWXYZ"
_NINO_SECOND = "ABCEGHJKLMNPRSTWXYZ"
_NINO_BAD_PREFIX = {"BG", "GB", "NK", "KN", "TN", "NT", "ZZ"}
_UPPER = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_CONSONANTS = "BCDFGHJKLMNPQRSTVWXYZ"
_CURP_STATES = (
    "AS",
    "BC",
    "BS",
    "CC",
    "CL",
    "CM",
    "CS",
    "CH",
    "DF",
    "DG",
    "GT",
    "GR",
    "HG",
    "JC",
    "MC",
    "MN",
    "MS",
    "NT",
    "NL",
    "OC",
    "PL",
    "QT",
    "QR",
    "SP",
    "SL",
    "SR",
    "TC",
    "TS",
    "TL",
    "VZ",
    "YN",
    "ZS",
    "NE",
)

#: Sentence templates; ``{TYPE}`` slots are filled and labelled, ``{TYPE#2}`` is a second value.
TEXT_TEMPLATES: dict[str, tuple[str, ...]] = {
    "US": (
        "{PERSON_NAME} asked us to update SSN {SSN} and the card {CARD_PAN} on file.",
        "Please reach {PERSON_NAME} at {EMAIL} or {PHONE} before {DATE}.",
        "Wire to routing {ROUTING_ABA}; the payer's ITIN is {ITIN}.",
        "The employer EIN {EIN} was verified for {PERSON_NAME} (DOB: {DATE_OF_BIRTH}).",
        "Customer mailed from {EMAIL}, server {IP_ADDRESS} logged the request.",
        "Card {CARD_PAN} expires {CARD_EXPIRY}; contact {PHONE}.",
    ),
    "CA": (
        "{PERSON_NAME} provided SIN {SIN} and email {EMAIL}.",
        "Call {PERSON_NAME} on {PHONE}; card {CARD_PAN} was blocked on {DATE}.",
        "Social insurance number {SIN} belongs to the applicant born {DATE_OF_BIRTH} (DOB).",
        "Statement sent to {EMAIL}; login came from {IP_ADDRESS}.",
    ),
    "MX": (
        "El cliente {PERSON_NAME} con CURP {CURP} actualizó su RFC {RFC}.",
        "Escribir a {EMAIL} o llamar al {PHONE}; tarjeta {CARD_PAN}.",
        "CURP {CURP} verificada el {DATE} para {PERSON_NAME}.",
        "RFC {RFC} registrado; correo {EMAIL}.",
    ),
    "IN": (
        "{PERSON_NAME} submitted Aadhaar {AADHAAR} and PAN {PAN_IN} for KYC.",
        "Refund to IFSC {IFSC}; call {PHONE} or mail {EMAIL}.",
        "Aadhaar {AADHAAR} matched the DOB {DATE_OF_BIRTH} on record.",
        "PIN code {POSTAL_CODE} for card {CARD_PAN}; PAN {PAN_IN}.",
    ),
    "UK": (
        "{PERSON_NAME} gave NI number {NINO} and NHS {NHS_NUMBER}.",
        "Pay IBAN {IBAN} by {DATE}; email {EMAIL}.",
        "National insurance {NINO} confirmed; phone {PHONE}.",
        "Postcode {POSTAL_CODE} on file; card {CARD_PAN}.",
    ),
    "EU": (
        "Transfer to IBAN {IBAN} from IBAN {IBAN#2} cleared on {DATE}.",
        "Kontakt: {EMAIL}, Telefon {PHONE}.",
        "Paiement par carte {CARD_PAN} depuis {IP_ADDRESS}.",
        "{PERSON_NAME} updated IBAN {IBAN}; contact {EMAIL}.",
    ),
}
#: JSON documents: key → entity type (None = a non-sensitive control field).
JSON_FIELDS: dict[str, tuple[tuple[str, str | None], ...]] = {
    "US": (
        ("customer_id", "CUSTOMER_ID"),
        ("full_name", "PERSON_NAME"),
        ("dob", "DATE_OF_BIRTH"),
        ("ssn", "SSN"),
        ("email", "EMAIL"),
        ("phone", "PHONE"),
        ("card_number", "CARD_PAN"),
        ("status", None),
        ("risk_score", None),
    ),
    "CA": (
        ("customer_id", "CUSTOMER_ID"),
        ("full_name", "PERSON_NAME"),
        ("sin", "SIN"),
        ("email", "EMAIL"),
        ("phone", "PHONE"),
        ("status", None),
    ),
    "MX": (
        ("customer_id", "CUSTOMER_ID"),
        ("nombre", "PERSON_NAME"),
        ("curp", "CURP"),
        ("rfc", "RFC"),
        ("correo", "EMAIL"),
        ("status", None),
    ),
    "IN": (
        ("customer_id", "CUSTOMER_ID"),
        ("full_name", "PERSON_NAME"),
        ("aadhaar", "AADHAAR"),
        ("pan", "PAN_IN"),
        ("email", "EMAIL"),
        ("phone", "PHONE"),
        ("status", None),
    ),
    "UK": (
        ("customer_id", "CUSTOMER_ID"),
        ("full_name", "PERSON_NAME"),
        ("nino", "NINO"),
        ("nhs_number", "NHS_NUMBER"),
        ("iban", "IBAN"),
        ("email", "EMAIL"),
        ("status", None),
    ),
    "EU": (
        ("customer_id", "CUSTOMER_ID"),
        ("full_name", "PERSON_NAME"),
        ("iban", "IBAN"),
        ("email", "EMAIL"),
        ("status", None),
    ),
}
NOTE_TEMPLATES = ("Follow-up with {EMAIL} requested.", "Card {CARD_PAN} replaced.", "No remarks.")


class Label(BaseModel):
    path: str = "$"
    start: int | None = None
    end: int | None = None
    entity_type: str
    generator: str = "surrogate"


class CorpusRecord(BaseModel):
    id: str
    jurisdiction: str
    locale: str
    kind: Literal["text", "json"]
    text: str | None = None
    data: dict[str, Any] | None = None
    labels: list[Label] = Field(default_factory=list)


def _nhs(rng: random.Random) -> str:
    while True:
        digits = [rng.randint(0, 9) for _ in range(9)]
        check = 11 - sum(d * w for d, w in zip(digits, range(10, 1, -1), strict=True)) % 11
        if check == 10:
            continue
        number = "".join(map(str, digits)) + str(0 if check == 11 else check)
        return f"{number[:3]} {number[3:6]} {number[6:]}"


def _born(rng: random.Random) -> date:
    return date(1950, 1, 1) + timedelta(days=rng.randint(0, 365 * 50))


def _curp(rng: random.Random) -> str:
    """A CURP with a valid date, state and check digit (Faker's es_MX provider is not seedable)."""
    born = _born(rng)
    body = (
        rng.choice(_UPPER)
        + rng.choice("AEIOUX")
        + rng.choice(_UPPER)
        + rng.choice(_UPPER)
        + born.strftime("%y%m%d")
        + rng.choice("HM")
        + rng.choice(_CURP_STATES)
        + "".join(rng.choice(_CONSONANTS) for _ in range(3))
        + (rng.choice("0123456789") if born.year < 2000 else rng.choice(_UPPER))
    )
    return body + mx_curp.calc_check_digit(body)


def _rfc(rng: random.Random) -> str:
    """A personal (13-character) RFC with a valid date and check digit."""
    body = (
        "".join(rng.choice(_UPPER) for _ in range(4))
        + _born(rng).strftime("%y%m%d")
        + "".join(rng.choice(_UPPER + "0123456789") for _ in range(2))
    )
    return body + mx_rfc.calc_check_digit(body)


def _nino(rng: random.Random) -> str:
    while True:
        prefix = rng.choice(_NINO_FIRST) + rng.choice(_NINO_SECOND)
        if prefix not in _NINO_BAD_PREFIX:
            body = "".join(str(rng.randint(0, 9)) for _ in range(6))
            return f"{prefix} {body[:2]} {body[2:4]} {body[4:]} {rng.choice('ABCD')}"


PERSONA_ENTITIES = frozenset(
    {"PERSON_NAME", "DATE_OF_BIRTH", "EMAIL", "PHONE", "STREET_ADDRESS", "CITY", "POSTAL_CODE"}
)
SURROGATE_TEMPLATES = {
    "CARD_PAN": "0000 0000 0000 0000",
    "AADHAAR": "0000 0000 0000",
    "SIN": "000 000 000",
    "ROUTING_ABA": "000000000",
    "PAN_IN": "AAAAA0000A",
    "CARD_EXPIRY": "01/30",
    "IP_ADDRESS": "0.0.0.0",
    "IBAN": "GB00XXXX00000000000000",
}


class ValueFactory:
    """Seeded labelled values for one jurisdiction.

    Persona fields of one record share a surrogate engine (name, e-mail and DOB agree); every
    other value gets its own engine, because a surrogate is a function of (seed, type, template)
    and one engine would repeat the same card number in every record.
    """

    def __init__(self, seed: bytes, jurisdiction: str) -> None:
        self.seed = seed
        self.jurisdiction = jurisdiction
        self.locale = JURISDICTIONS[jurisdiction]
        self._fakers: dict[str, Faker] = {}

    def _engine(self, label: str) -> SurrogateEngine:
        return SurrogateEngine(
            hmac.new(self.seed, label.encode(), hashlib.sha256).digest(), default_locale=self.locale
        )

    def _faker(self, locale: str, rng: random.Random) -> Faker:
        fake = self._fakers.setdefault(locale, Faker(locale))
        fake.seed_instance(rng.getrandbits(64))
        return fake

    def _raw(
        self, entity: str, record_no: int, slot_no: int, attempt: int, rng: random.Random
    ) -> tuple[str, str]:
        loc = self.locale
        if entity in PERSONA_ENTITIES:
            engine = self._engine(f"persona|{record_no}|{attempt}")
            persona = engine.persona("record", loc)
            if entity == "PERSON_NAME":
                return persona.full_name, "surrogate"
            if entity == "DATE_OF_BIRTH":
                return persona.dob.isoformat(), "surrogate"
            if entity == "PHONE":
                template = "+1 201 555 0100" if rng.random() < 0.5 else "201 555 0100"
                return engine.surrogate("PHONE", template, loc, "record"), "surrogate"
            return engine.surrogate(entity, "x", loc, "record"), "surrogate"
        if entity == "DATE":
            return (date(2020, 1, 1) + timedelta(days=rng.randint(0, 2400))).isoformat(), "rule"
        if entity in ("SSN", "ITIN", "EIN"):
            fake = self._faker("en_US", rng)
            return str({"SSN": fake.ssn, "ITIN": fake.itin, "EIN": fake.ein}[entity]()), "faker"
        if entity == "CURP":
            return _curp(rng), "rule"
        if entity == "RFC":
            return _rfc(rng), "rule"
        if entity == "NINO":
            return _nino(rng), "rule"
        if entity == "NHS_NUMBER":
            return _nhs(rng), "rule"
        if entity == "IBAN" and self.jurisdiction == "EU":
            return str(self._faker(rng.choice(EU_IBAN_LOCALES), rng).iban()), "faker"
        if entity == "IFSC":
            letters = "".join(rng.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZ") for _ in range(4))
            return f"{letters}0{rng.randint(0, 999999):06d}", "rule"
        if entity == "CUSTOMER_ID":
            return f"CUST-{rng.randint(100000, 999999)}", "rule"
        engine = self._engine(f"value|{entity}|{record_no}|{slot_no}|{attempt}")
        return engine.surrogate(
            entity, SURROGATE_TEMPLATES.get(entity, "x"), loc, "record"
        ), "surrogate"

    def value(self, entity: str, record_no: int, slot_no: int) -> tuple[str, str]:
        """A value of ``entity`` for this record; checksum types are always rule-valid."""
        for attempt in range(25):
            rng = random.Random(seed_int(self.seed, f"{entity}|{record_no}|{slot_no}|{attempt}"))
            value, generator = self._raw(entity, record_no, slot_no, attempt, rng)
            if validate(entity, value) is not False:
                return value, generator
        raise RuntimeError(f"no rule-valid {entity} after 25 attempts")


def _fill(template: str, factory: ValueFactory, record_no: int) -> tuple[str, list[Label]]:
    pieces: list[str] = []
    labels: list[Label] = []
    cursor = pos = 0
    for slot_no, match in enumerate(_SLOT.finditer(template)):
        literal = template[cursor : match.start()]
        pieces.append(literal)
        pos += len(literal)
        value, generator = factory.value(match.group(1), record_no, slot_no)
        labels.append(
            Label(start=pos, end=pos + len(value), entity_type=match.group(1), generator=generator)
        )
        pieces.append(value)
        pos += len(value)
        cursor = match.end()
    pieces.append(template[cursor:])
    return "".join(pieces), labels


def generate_corpus(seed: int, records: int) -> dict[str, list[CorpusRecord]]:
    """``records`` per jurisdiction, alternating text and JSON; deterministic in ``seed``."""
    root = hashlib.sha256(f"dps.eval|{seed}".encode()).digest()
    out: dict[str, list[CorpusRecord]] = {}
    for jurisdiction, locale in JURISDICTIONS.items():
        factory = ValueFactory(hashlib.sha256(root + jurisdiction.encode()).digest(), jurisdiction)
        items: list[CorpusRecord] = []
        for n in range(records):
            rid = f"{jurisdiction.lower()}-{n:04d}"
            templates = TEXT_TEMPLATES[jurisdiction]
            if n % 2 == 0:
                text, labels = _fill(templates[(n // 2) % len(templates)], factory, n)
                items.append(
                    CorpusRecord(
                        id=rid,
                        jurisdiction=jurisdiction,
                        locale=locale,
                        kind="text",
                        text=text,
                        labels=labels,
                    )
                )
                continue
            data: dict[str, Any] = {}
            labels = []
            for slot_no, (key, entity) in enumerate(JSON_FIELDS[jurisdiction]):
                if entity is None:
                    data[key] = "ACTIVE" if key == "status" else 700 + n % 200
                    continue
                value, generator = factory.value(entity, n, slot_no)
                data[key] = value
                labels.append(Label(path=f"$.{key}", entity_type=entity, generator=generator))
            note, note_labels = _fill(NOTE_TEMPLATES[n % len(NOTE_TEMPLATES)], factory, n + 100_000)
            data["notes"] = note
            labels += [label.model_copy(update={"path": "$.notes"}) for label in note_labels]
            items.append(
                CorpusRecord(
                    id=rid,
                    jurisdiction=jurisdiction,
                    locale=locale,
                    kind="json",
                    data=data,
                    labels=labels,
                )
            )
        out[jurisdiction] = items
    return out


def write_corpus(corpus: dict[str, list[CorpusRecord]], directory: Path) -> list[Path]:
    """One ``<jurisdiction>.jsonl`` per jurisdiction (sorted keys, one record per line)."""
    directory.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for jurisdiction, records in corpus.items():
        path = directory / f"{jurisdiction.lower()}.jsonl"
        lines = [
            json.dumps(
                r.model_dump(mode="json", exclude_none=True), sort_keys=True, ensure_ascii=False
            )
            for r in records
        ]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        paths.append(path)
    return paths


def corpus_files(paths: Iterable[Path]) -> list[Path]:
    """Expand directories to their ``*.jsonl`` files (sorted)."""
    out: list[Path] = []
    for path in paths:
        out += sorted(path.glob("*.jsonl")) if path.is_dir() else [path]
    return out


def load_corpus(paths: Iterable[Path]) -> tuple[list[CorpusRecord], str]:
    """Records from files or directories, and the sha256 over the file contents (sorted by name)."""
    files = corpus_files(paths)
    digest = hashlib.sha256()
    records: list[CorpusRecord] = []
    for path in sorted(files, key=lambda p: p.name):
        raw = path.read_bytes()
        digest.update(path.name.encode() + b"\0" + raw)
        records += [
            CorpusRecord.model_validate_json(line)
            for line in raw.decode("utf-8").splitlines()
            if line.strip()
        ]
    return records, digest.hexdigest()
```

`dps/evaluation/scoring.py`:
```python
"""Score detector findings against labels: precision, recall and F1 per entity type (spec §5.3).

Matching is strict on the entity type: a finding and a label match when they share a path and
their character spans overlap with IoU ≥ 0.5 (a whole-value label or finding spans the whole
leaf). A span match with a different type counts as a miss for the label's type, a false
positive for the predicted type, and one confusion entry. Reports carry numbers and type names
only — never a value.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from typing import Any

from pydantic import BaseModel, Field

from dps import __version__
from dps.engine.detect.ensemble import Detector, DetectorConfig
from dps.engine.detect.ner_presidio import build_ner
from dps.engine.findings import Finding
from dps.engine.jsonpath import flatten
from dps.evaluation.corpus import CorpusRecord

IOU_MATCH = 0.5
THRESHOLDS = (0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9)


class Counts(BaseModel):
    tp: int = 0
    fp: int = 0
    fn: int = 0

    def add(self, other: Counts) -> None:
        self.tp += other.tp
        self.fp += other.fp
        self.fn += other.fn


class TypeScore(BaseModel):
    entity_type: str
    tp: int
    fp: int
    fn: int
    support: int
    precision: float | None
    recall: float | None
    f1: float | None


class EvalReport(BaseModel):
    report_version: str = "dps.detector-eval.v1"
    engine_version: str = __version__
    corpus: dict[str, Any]
    config: dict[str, Any]
    overall: dict[str, float | None]
    by_type: list[TypeScore]
    by_jurisdiction: dict[str, dict[str, float | int | None]]
    confusion: list[dict[str, Any]] = Field(default_factory=list)
    thresholds: list[dict[str, float | None]] = Field(default_factory=list)


def _ratio(num: int, den: int) -> float | None:
    return round(num / den, 4) if den else None


def _f1(p: float | None, r: float | None) -> float | None:
    if p is None or r is None or p + r == 0:
        return None if p is None or r is None else 0.0
    return round(2 * p * r / (p + r), 4)


def _leaf_lengths(record: CorpusRecord) -> dict[str, int]:
    if record.kind == "text":
        return {"$": len(record.text or "")}
    return {path: len(str(value)) for path, value in flatten(record.data or {})}


def _span(
    path: str, start: int | None, end: int | None, lengths: dict[str, int]
) -> tuple[int, int]:
    return (0, lengths.get(path, 0)) if start is None else (start, end or start)


def _iou(a: tuple[int, int], b: tuple[int, int]) -> float:
    inter = max(0, min(a[1], b[1]) - max(a[0], b[0]))
    union = max(a[1], b[1]) - min(a[0], b[0])
    return inter / union if union else 0.0


def score_record(
    record: CorpusRecord, findings: list[Finding]
) -> tuple[dict[str, Counts], Counter[tuple[str, str]]]:
    """Per-type counts and (label type, predicted type) confusions for one record."""
    lengths = _leaf_lengths(record)
    pairs: list[tuple[float, int, int]] = []
    for li, label in enumerate(record.labels):
        ls = _span(label.path, label.start, label.end, lengths)
        for fi, finding in enumerate(findings):
            if finding.locator.path != label.path:
                continue
            iou = _iou(
                ls, _span(finding.locator.path, finding.locator.start, finding.locator.end, lengths)
            )
            if iou >= IOU_MATCH:
                pairs.append((iou, li, fi))
    counts: dict[str, Counts] = {}
    confusion: Counter[tuple[str, str]] = Counter()
    used_labels: set[int] = set()
    used_findings: set[int] = set()
    for _, li, fi in sorted(pairs, key=lambda p: -p[0]):
        if li in used_labels or fi in used_findings:
            continue
        used_labels.add(li)
        used_findings.add(fi)
        truth, predicted = record.labels[li].entity_type, findings[fi].entity_type
        if truth == predicted:
            counts.setdefault(truth, Counts()).tp += 1
        else:
            counts.setdefault(truth, Counts()).fn += 1
            counts.setdefault(predicted, Counts()).fp += 1
            confusion[(truth, predicted)] += 1
    for li, label in enumerate(record.labels):
        if li not in used_labels:
            counts.setdefault(label.entity_type, Counts()).fn += 1
    for fi, finding in enumerate(findings):
        if fi not in used_findings:
            counts.setdefault(finding.entity_type, Counts()).fp += 1
    return counts, confusion


def _detect(record: CorpusRecord, detectors: dict[str, Detector]) -> list[Finding]:
    detector = detectors[record.locale]
    if record.kind == "text":
        return detector.detect(text=record.text or "").findings
    return detector.detect(data=record.data or {}).findings


def evaluate(
    records: Iterable[CorpusRecord],
    *,
    corpus_sha256: str,
    min_score: float = 0.6,
    ner: bool = False,
) -> EvalReport:
    """Run the ensemble (min score 0, filtered per threshold) over every record and score it."""
    records = list(records)
    adapter = build_ner(("en", "es"), True) if ner else None
    detectors = {
        r.locale: Detector(
            DetectorConfig(min_score=0.0, locale=r.locale, ner_enabled=adapter is not None),
            ner=adapter,
        )
        for r in records
    }
    predictions = [(r, _detect(r, detectors)) for r in records]
    by_type: dict[str, Counts] = {}
    by_jurisdiction: dict[str, Counts] = {}
    confusion: Counter[tuple[str, str]] = Counter()
    for record, findings in predictions:
        counts, conf = score_record(record, [f for f in findings if f.score >= min_score])
        confusion += conf
        for entity, c in counts.items():
            by_type.setdefault(entity, Counts()).add(c)
            by_jurisdiction.setdefault(record.jurisdiction, Counts()).add(c)
    total = Counts()
    for c in by_type.values():
        total.add(c)
    scores = []
    for entity, c in sorted(by_type.items()):
        p, r = _ratio(c.tp, c.tp + c.fp), _ratio(c.tp, c.tp + c.fn)
        scores.append(
            TypeScore(
                entity_type=entity,
                tp=c.tp,
                fp=c.fp,
                fn=c.fn,
                support=c.tp + c.fn,
                precision=p,
                recall=r,
                f1=_f1(p, r),
            )
        )
    micro_p, micro_r = _ratio(total.tp, total.tp + total.fp), _ratio(total.tp, total.tp + total.fn)
    f1s = [s.f1 for s in scores if s.support and s.f1 is not None]
    thresholds = []
    for t in THRESHOLDS:
        sweep = Counts()
        for record, findings in predictions:
            for c in score_record(record, [f for f in findings if f.score >= t])[0].values():
                sweep.add(c)
        tp_, tr_ = _ratio(sweep.tp, sweep.tp + sweep.fp), _ratio(sweep.tp, sweep.tp + sweep.fn)
        thresholds.append({"min_score": t, "precision": tp_, "recall": tr_, "f1": _f1(tp_, tr_)})
    jurisdictions: dict[str, dict[str, float | int | None]] = {}
    for name, c in sorted(by_jurisdiction.items()):
        jp, jr = _ratio(c.tp, c.tp + c.fp), _ratio(c.tp, c.tp + c.fn)
        jurisdictions[name] = {
            "records": sum(1 for r in records if r.jurisdiction == name),
            "precision": jp,
            "recall": jr,
            "f1": _f1(jp, jr),
        }
    return EvalReport(
        corpus={
            "sha256": corpus_sha256,
            "records": len(records),
            "labels": sum(len(r.labels) for r in records),
        },
        config={"min_score": min_score, "ner": adapter is not None, "iou": IOU_MATCH},
        overall={
            "micro_precision": micro_p,
            "micro_recall": micro_r,
            "micro_f1": _f1(micro_p, micro_r),
            "macro_f1": round(sum(f1s) / len(f1s), 4) if f1s else None,
        },
        by_type=scores,
        by_jurisdiction=jurisdictions,
        confusion=[
            {"label": a, "predicted": b, "count": n} for (a, b), n in sorted(confusion.items())
        ],
        thresholds=thresholds,
    )


def _fmt(value: float | int | None) -> str:
    return "—" if value is None else f"{value:.3f}" if isinstance(value, float) else str(value)


def render_markdown(report: EvalReport) -> str:
    """The committed human-readable report (numbers and type names only)."""
    lines = [
        "# Detector evaluation — M1",
        "",
        f"Corpus `{report.corpus['sha256'][:16]}…` · {report.corpus['records']} records · "
        f"{report.corpus['labels']} labels · min score {report.config['min_score']} · "
        f"NER {'on' if report.config['ner'] else 'off'} · engine {report.engine_version}",
        "",
        "Generated by `python tools/eval_detectors.py run`; synthetic corpus from `tests/eval/corpus/` "
        "(surrogate engine + seeded rule-valid generators — no real data). Strict type matching, span IoU ≥ 0.5.",
        "",
        "## Overall",
        "",
        "| micro precision | micro recall | micro F1 | macro F1 |",
        "|---|---|---|---|",
        "| "
        + " | ".join(
            _fmt(report.overall[k])
            for k in ("micro_precision", "micro_recall", "micro_f1", "macro_f1")
        )
        + " |",
        "",
        "## By entity type",
        "",
        "| entity type | support | TP | FP | FN | precision | recall | F1 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    lines += [
        f"| {s.entity_type} | {s.support} | {s.tp} | {s.fp} | {s.fn} | {_fmt(s.precision)} | "
        f"{_fmt(s.recall)} | {_fmt(s.f1)} |"
        for s in report.by_type
    ]
    lines += [
        "",
        "## By jurisdiction",
        "",
        "| jurisdiction | records | precision | recall | F1 |",
        "|---|---|---|---|---|",
    ]
    lines += [
        f"| {name} | {v['records']} | {_fmt(v['precision'])} | {_fmt(v['recall'])} | {_fmt(v['f1'])} |"
        for name, v in report.by_jurisdiction.items()
    ]
    lines += [
        "",
        "## Threshold sweep (micro)",
        "",
        "| min score | precision | recall | F1 |",
        "|---|---|---|---|",
    ]
    lines += [
        f"| {t['min_score']} | {_fmt(t['precision'])} | {_fmt(t['recall'])} | {_fmt(t['f1'])} |"
        for t in report.thresholds
    ]
    if report.confusion:
        lines += ["", "## Type confusions", "", "| label | predicted | count |", "|---|---|---|"]
        lines += [f"| {c['label']} | {c['predicted']} | {c['count']} |" for c in report.confusion]
    return "\n".join(lines) + "\n"
```

`tools/eval_detectors.py`:
```python
"""Detector evaluation harness (spec §5.3).

    python tools/eval_detectors.py generate --seed 7 --records 150 --out tests/eval/corpus
    python tools/eval_detectors.py run --corpus tests/eval/corpus \\
        --json docs/technical/detector-eval-m1.json --markdown docs/technical/detector-eval-m1.md
    python tools/eval_detectors.py run --corpus /secure/bank-labelled.jsonl   # the bank's own slot

The bank's labelled samples use the same JSONL record shape and must never be committed
(``tests/eval/private/`` is git-ignored). Output is numbers and entity types only.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from dps.evaluation.corpus import generate_corpus, load_corpus, write_corpus
from dps.evaluation.scoring import evaluate, render_markdown


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)
    gen = sub.add_parser("generate", help="write the labelled synthetic corpus")
    gen.add_argument("--seed", type=int, default=7)
    gen.add_argument(
        "--records", type=int, default=150, help="records per jurisdiction (half text, half JSON)"
    )
    gen.add_argument("--out", type=Path, default=Path("tests/eval/corpus"))
    run = sub.add_parser("run", help="score the detectors against a corpus")
    run.add_argument("--corpus", type=Path, nargs="+", default=[Path("tests/eval/corpus")])
    run.add_argument("--min-score", type=float, default=0.6)
    run.add_argument(
        "--ner", action="store_true", help="enable the Presidio tier (needs the [ner] extra)"
    )
    run.add_argument("--json", type=Path)
    run.add_argument("--markdown", type=Path)
    args = parser.parse_args(argv)
    if args.command == "generate":
        for path in write_corpus(generate_corpus(args.seed, args.records), args.out):
            print(f"wrote {path}")
        return 0
    records, digest = load_corpus(args.corpus)
    report = evaluate(records, corpus_sha256=digest, min_score=args.min_score, ner=args.ner)
    if args.json:
        args.json.write_text(
            json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    markdown = render_markdown(report)
    if args.markdown:
        args.markdown.write_text(markdown, encoding="utf-8")
    sys.stdout.write(markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 1: Write the failing tests**

`tests/test_eval_corpus.py`:
```python
from __future__ import annotations

import re
from pathlib import Path

from dps.engine.detect.checksums import validate
from dps.evaluation.corpus import JURISDICTIONS, generate_corpus, load_corpus, write_corpus


def test_generation_is_deterministic(tmp_path: Path) -> None:
    a, b = tmp_path / "a", tmp_path / "b"
    write_corpus(generate_corpus(7, 6), a)
    write_corpus(generate_corpus(7, 6), b)
    assert [p.read_bytes() for p in sorted(a.iterdir())] == [
        p.read_bytes() for p in sorted(b.iterdir())
    ]
    assert load_corpus([a])[1] == load_corpus([b])[1]
    write_corpus(generate_corpus(8, 6), tmp_path / "c")
    assert load_corpus([tmp_path / "c"])[1] != load_corpus([a])[1]


def test_labels_slice_rule_valid_values() -> None:
    corpus = generate_corpus(7, 12)
    assert set(corpus) == set(JURISDICTIONS)
    for records in corpus.values():
        for record in records:
            for label in record.labels:
                if record.kind == "text":
                    value = (record.text or "")[label.start or 0 : label.end or 0]
                elif label.start is None:
                    value = str((record.data or {})[label.path[2:]])
                else:
                    value = str((record.data or {})["notes"])[label.start : label.end or 0]
                assert (
                    value and "{" not in value and validate(label.entity_type, value) is not False
                )


def test_values_vary_across_records() -> None:
    cards = {
        r.data["card_number"] for r in generate_corpus(7, 40)["US"] if r.kind == "json" and r.data
    }
    assert len(cards) == 20


def test_templates_hold_no_digits_or_addresses() -> None:
    from dps.evaluation.corpus import NOTE_TEMPLATES, TEXT_TEMPLATES

    for template in [t for ts in TEXT_TEMPLATES.values() for t in ts] + list(NOTE_TEMPLATES):
        assert (
            not re.search(r"\d", re.sub(r"\{[A-Z_]+(#\d)?\}", "", template)) and "@" not in template
        )
```

`tests/test_eval_scoring.py`:
```python
from __future__ import annotations

from dps.engine.findings import Finding, Locator
from dps.engine.taxonomy import Sensitivity
from dps.evaluation.corpus import CorpusRecord, Label
from dps.evaluation.scoring import evaluate, render_markdown, score_record

TEXT = "Ann Smith mailed a@b.co about card 4111 1111 1111 1111 on 2024-01-02"


def finding(
    entity: str, start: int | None, end: int | None, path: str = "$", score: float = 0.9
) -> Finding:
    return Finding(
        locator=Locator(path=path, start=start, end=end),
        entity_type=entity,
        score=score,
        detectors=["test"],
        sensitivity=Sensitivity.HIGH,
    )


def test_score_record_counts_tp_fp_fn_and_confusions() -> None:
    record = CorpusRecord(
        id="t",
        jurisdiction="US",
        locale="en_US",
        kind="text",
        text=TEXT,
        labels=[
            Label(start=0, end=9, entity_type="PERSON_NAME"),
            Label(start=17, end=23, entity_type="EMAIL"),
            Label(start=35, end=54, entity_type="CARD_PAN"),
            Label(start=58, end=68, entity_type="DATE"),
        ],
    )
    counts, confusion = score_record(
        record,
        [
            finding("EMAIL", 17, 23),  # exact → TP
            finding("CARD_PAN", 35, 50),  # IoU 15/19 → TP
            finding("AADHAAR", 58, 68),  # right span, wrong type → FN DATE, FP AADHAAR, confusion
            finding("PHONE", 24, 29),  # nothing there → FP
        ],
    )
    assert counts["EMAIL"].tp == 1 and counts["CARD_PAN"].tp == 1
    assert counts["PERSON_NAME"].fn == 1 and counts["DATE"].fn == 1
    assert counts["AADHAAR"].fp == 1 and counts["PHONE"].fp == 1
    assert confusion == {("DATE", "AADHAAR"): 1}


def test_whole_value_labels_match_whole_findings() -> None:
    record = CorpusRecord(
        id="j",
        jurisdiction="US",
        locale="en_US",
        kind="json",
        data={"ssn": "123-45-6789", "notes": "x"},
        labels=[Label(path="$.ssn", entity_type="SSN")],
    )
    counts, _ = score_record(record, [finding("SSN", None, None, path="$.ssn")])
    assert counts["SSN"].tp == 1 and counts["SSN"].fn == 0


def test_evaluate_runs_the_detector_and_renders() -> None:
    record = CorpusRecord(
        id="e",
        jurisdiction="US",
        locale="en_US",
        kind="text",
        text="mail a.b@example.com or call (415) 555-0132",
        labels=[
            Label(start=5, end=20, entity_type="EMAIL"),
            Label(start=29, end=43, entity_type="PHONE"),
        ],
    )
    report = evaluate([record], corpus_sha256="0" * 64)
    by = {s.entity_type: s for s in report.by_type}
    assert (
        by["EMAIL"].recall == 1.0
        and by["PHONE"].recall == 1.0
        and report.overall["micro_precision"] == 1.0
    )
    assert [t["min_score"] for t in report.thresholds] == [0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]
    markdown = render_markdown(report)
    assert "| EMAIL | 1 | 1 | 0 | 0 |" in markdown and "example.com" not in markdown
```

`tests/test_eval_report.py` (the gate that keeps the committed report honest: a detector or threshold change without a regenerated report fails CI, and checksum types keep a 0.95 floor):
```python
from __future__ import annotations

import json
from pathlib import Path

import pytest

from dps.evaluation.corpus import load_corpus
from dps.evaluation.scoring import evaluate

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "tests" / "eval" / "corpus"
REPORT = ROOT / "docs" / "technical" / "detector-eval-m1.json"
#: Checksum-validated types: the detectors must find them nearly perfectly on clean text.
FLOOR_TYPES = (
    "AADHAAR",
    "CARD_PAN",
    "CURP",
    "EMAIL",
    "IBAN",
    "NHS_NUMBER",
    "NINO",
    "PAN_IN",
    "RFC",
    "SIN",
    "SSN",
)
FLOOR = 0.95


@pytest.fixture(scope="module")
def current():  # noqa: ANN201
    records, digest = load_corpus([CORPUS])
    return evaluate(records, corpus_sha256=digest)


def test_committed_report_is_current(current) -> None:  # noqa: ANN001
    committed = json.loads(REPORT.read_text(encoding="utf-8"))
    fresh = current.model_dump(mode="json")
    assert committed["corpus"] == fresh["corpus"], "corpus changed: run tools/eval_detectors.py run"
    assert committed["overall"] == fresh["overall"] and committed["by_type"] == fresh["by_type"], (
        "detectors changed: regenerate docs/technical/detector-eval-m1.{json,md} and commit them"
    )


def test_checksum_types_meet_the_floor(current) -> None:  # noqa: ANN001
    by = {s.entity_type: s for s in current.by_type}
    for entity in FLOOR_TYPES:
        assert by[entity].precision is not None and by[entity].precision >= FLOOR, entity
        assert by[entity].recall is not None and by[entity].recall >= FLOOR, entity
```

- [ ] **Step 2: Run to confirm failure** — `pytest -q tests/test_eval_corpus.py tests/test_eval_scoring.py` → FAIL (`ModuleNotFoundError: dps.evaluation`).

- [ ] **Step 3: Implement** the package and the tool, then generate and evaluate:
```bash
python tools/eval_detectors.py generate --seed 7 --records 150 --out tests/eval/corpus
python tools/eval_detectors.py run --corpus tests/eval/corpus \
  --json docs/technical/detector-eval-m1.json --markdown docs/technical/detector-eval-m1.md
```
Expected: six JSONL files (≈ 550 KB in total, 900 records), the markdown table printed, both report files written. Read the report: if any `FLOOR_TYPES` entry is below 0.95, the cause is a detector bug or a template without the needed context word — fix the template (never lower the floor) and record it in `M1-DEVIATIONS.md`.

- [ ] **Step 4: Gates** — `pytest -q tests/test_eval_corpus.py tests/test_eval_scoring.py tests/test_eval_report.py` → `9 passed` (≈ 1.5 s; verified while writing); `python tools/eval_detectors.py run --corpus tests/eval/corpus --json docs/technical/detector-eval-m1.json --markdown docs/technical/detector-eval-m1.md && git diff --exit-code docs/technical/detector-eval-m1.json` → no diff (the report is reproducible). Full gates green.

- [ ] **Step 5: Commit**
```bash
git add dps/evaluation tools/eval_detectors.py tests/test_eval_corpus.py tests/test_eval_scoring.py tests/test_eval_report.py tests/eval/corpus docs/technical/detector-eval-m1.json docs/technical/detector-eval-m1.md
git commit -m "feat(eval): detector evaluation harness, synthetic labelled corpus and the committed M1 report" -- dps/evaluation tools/eval_detectors.py tests/test_eval_corpus.py tests/test_eval_scoring.py tests/test_eval_report.py tests/eval/corpus docs/technical/detector-eval-m1.json docs/technical/detector-eval-m1.md
```

---
### Task 16: Demo documents and the console foundation (routing base, typed API, OCR readiness)

**Files:**
- Create: `tools/make_demo_docs.py`, `demo/documents/{kyc-text.pdf,kyc-scanned.pdf,id-card.png,kyc.docx,kyc.txt,customers.csv}` (generated), `frontend/public/demo/kyc-scanned.pdf` (generated copy), `frontend/src/lib/yaml.ts`, `frontend/src/components/OcrStatus.tsx`
- Modify: `frontend/vite.config.ts`, `frontend/index.html`, `frontend/src/lib/api.ts`, `frontend/src/lib/types.ts`, `frontend/src/pages/Overview.tsx`, `frontend/src/components/Layout.tsx`

**Why the routing base changes:** M0 builds with `base: './'` (relative asset URLs), which only works one path segment deep. Contract §14 needs `/playground/documents` and `/policies/:name` now (and `/data-sources/jobs/:jobId` etc. later): with `./`, `/playground/documents` would request `/playground/assets/index.js`, the SPA fallback would answer with `index.html`, and the page would stay blank. M1 switches to `base: '/'` and `<script src="/theme-init.js">`; `dps/app.py` already serves `/assets/*` and root files from `frontend/dist`. (Hosting under a path prefix now needs a gateway rewrite — recorded in HANDOFF traps.)

**Demo documents (no real data):** `tools/make_demo_docs.py` builds a two-page KYC pack — an identification letter (name, address, PIN code, mobile, Aadhaar-shaped number, DOB, gender) and a savings statement (e-mail, phone, account number, customer id, PAN, a card on the test IIN, a payee name) — from the surrogate engine with a fixed seed, as a text PDF, a scanned twin (JPEG rasters, no text layer, built with `redact.build_image_pdf`), a PNG of page 1, a DOCX, a TXT and a 20-row CSV. Re-running is byte-identical (the DOCX zip is re-packed with fixed timestamps — python-docx stamps the current time otherwise). Verified while writing: the compose OCR mock reads both scanned pages word for word.

`tools/make_demo_docs.py`:
```python
"""Write the deterministic synthetic demo documents (no real data) for the console, the smoke test
and the ten-minute demo.

    python tools/make_demo_docs.py            # → demo/documents/*, frontend/public/demo/kyc-scanned.pdf

Every value comes from the surrogate engine (seed 7): personas, a Luhn-valid card on the test IIN,
a Verhoeff-valid Aadhaar-shaped number, RFC 2606 e-mail domains. Re-running produces identical
files, so they are committed.
"""

from __future__ import annotations

import csv
import hashlib
import io
import shutil
import zipfile
from pathlib import Path

import docx
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from dps.engine.synth.surrogates import SurrogateEngine
from dps.layers.documents.redact import build_image_pdf
from dps.layers.documents.render import PdfRenderer

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "demo" / "documents"
PUBLIC = ROOT / "frontend" / "public" / "demo"
SEED = hashlib.sha256(b"dps.demo.documents|7").digest()


def _values() -> dict[str, str]:
    engine = SurrogateEngine(SEED, default_locale="en_IN")
    persona = engine.persona("kyc", "en_IN")
    return {
        "name": persona.full_name,
        "street": persona.street,
        "city": persona.city,
        "pin": persona.postal_code,
        "dob": persona.dob.strftime("%d/%m/%Y"),
        "gender": persona.gender.title(),
        "email": engine.surrogate("EMAIL", "x", "en_IN", "kyc"),
        "phone": engine.surrogate("PHONE", "+91 98451 20377", "en_IN", "kyc"),
        "aadhaar": engine.surrogate("AADHAAR", "0000 0000 0000", "en_IN", "kyc"),
        "pan": engine.surrogate("PAN_IN", "AAAAA0000A", "en_IN", "kyc"),
        "card": engine.surrogate("CARD_PAN", "0000 0000 0000 0000", "en_IN", "kyc"),
        "account": engine.surrogate("ACCOUNT_NUMBER", "0000 0000 0000 00", "en_IN", "kyc"),
        "payee": engine.persona("payee", "en_IN").full_name,
    }


def kyc_pdf(v: dict[str, str]) -> bytes:
    """Two A4 pages: an Aadhaar-style letter and a savings statement (text layer)."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4, invariant=1)
    width, height = A4
    pages = [
        (
            "Unique Identification Letter",
            [
                f"To: {v['name']}",
                f"Address: {v['street']}, {v['city']} PIN code {v['pin']}",
                f"Mobile: {v['phone']}",
                f"Your Aadhaar No.: {v['aadhaar']}",
                f"DOB: {v['dob']}   Gender: {v['gender']}",
                "Aadhaar is proof of identity, not of citizenship.",
            ],
        ),
        (
            "Savings account statement",
            [
                f"Customer: {v['name']}",
                f"Email: {v['email']}   Phone: {v['phone']}",
                f"Account no.: {v['account']}   Customer ID: CUST-884120",
                f"PAN: {v['pan']}",
                f"02 Sep  NEFT/{v['payee']}/SALARY ADV            45,000.00",
                f"07 Sep  POS card {v['card']} BLR      6,120.00",
                "30 Sep  Closing balance                       1,16,946.50",
            ],
        ),
    ]
    for title, lines in pages:
        c.setFont("Helvetica-Bold", 18)
        c.drawString(56, height - 72, title)
        c.setFont("Helvetica", 12)
        for i, line in enumerate(lines):
            c.drawString(56, height - 120 - i * 26, line)
        c.showPage()
    c.save()
    return buf.getvalue()


def scanned(pdf: bytes, *, dpi: int = 200) -> bytes:
    """The same pages as JPEG rasters only — a 'scanned' PDF with no text layer."""
    with PdfRenderer(pdf) as renderer:
        pages = [(renderer.render(i, dpi), *renderer.page_size(i)) for i in range(len(renderer))]
    return build_image_pdf(pages, image_format="jpeg", jpeg_quality=80)


def id_png(pdf: bytes, *, dpi: int = 200) -> bytes:
    with PdfRenderer(pdf) as renderer:
        image = renderer.render(0, dpi)
    buf = io.BytesIO()
    image.save(buf, "PNG", dpi=(dpi, dpi))
    return buf.getvalue()


def kyc_docx(v: dict[str, str]) -> bytes:
    document = docx.Document()
    document.sections[0].header.paragraphs[0].text = "KYC review · Customer ID CUST-884120"
    document.add_paragraph(
        f"Customer {v['name']} ({v['email']}) asked to update the PAN {v['pan']}."
    )
    table = document.add_table(rows=3, cols=2)
    for row, (label, value) in enumerate(
        (("Aadhaar", v["aadhaar"]), ("Card", v["card"]), ("Phone", v["phone"]))
    ):
        table.cell(row, 0).text, table.cell(row, 1).text = label, value
    buf = io.BytesIO()
    document.save(buf)
    return _stable_zip(buf.getvalue())


def _stable_zip(data: bytes) -> bytes:
    """Re-pack a zip with fixed timestamps so the committed .docx is byte-for-byte reproducible."""
    out = io.BytesIO()
    with (
        zipfile.ZipFile(io.BytesIO(data)) as source,
        zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as target,
    ):
        for info in source.infolist():
            fixed = zipfile.ZipInfo(info.filename, date_time=(2026, 1, 1, 0, 0, 0))
            fixed.compress_type = zipfile.ZIP_DEFLATED
            target.writestr(fixed, source.read(info.filename))
    return out.getvalue()


def customers_csv(rows: int = 20) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(
        ["customer_id", "full_name", "email", "phone", "card_number", "status", "risk_score"]
    )
    for i in range(rows):
        engine = SurrogateEngine(
            hashlib.sha256(SEED + f"|row{i}".encode()).digest(), default_locale="en_IN"
        )
        persona = engine.persona("row", "en_IN")
        writer.writerow(
            [
                f"CUST-{884120 + i}",
                persona.full_name,
                engine.surrogate("EMAIL", "x", "en_IN", "row"),
                engine.surrogate("PHONE", "+91 98451 20377", "en_IN", "row"),
                engine.surrogate("CARD_PAN", "0000 0000 0000 0000", "en_IN", "row"),
                "ACTIVE" if i % 4 else "CLOSED",
                600 + (i * 37) % 300,
            ]
        )
    return buf.getvalue().encode("utf-8")


def main() -> int:
    v = _values()
    OUT.mkdir(parents=True, exist_ok=True)
    PUBLIC.mkdir(parents=True, exist_ok=True)
    text = kyc_pdf(v)
    files = {
        "kyc-text.pdf": text,
        "kyc-scanned.pdf": scanned(text),
        "id-card.png": id_png(text),
        "kyc.docx": kyc_docx(v),
        "customers.csv": customers_csv(),
        "kyc.txt": (
            f"Customer {v['name']} (DOB: {v['dob']}) mailed {v['email']} about card {v['card']}.\n"
            f"Aadhaar {v['aadhaar']} and PAN {v['pan']} were verified.\n"
        ).encode(),
    }
    for name, data in files.items():
        (OUT / name).write_bytes(data)
        print(f"wrote demo/documents/{name} ({len(data)} bytes)")
    shutil.copyfile(OUT / "kyc-scanned.pdf", PUBLIC / "kyc-scanned.pdf")
    print("wrote frontend/public/demo/kyc-scanned.pdf")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

`frontend/vite.config.ts` — replace the comment and `base`:
```ts
// Served by FastAPI (dps/app.py::_mount_frontend) from frontend/dist at the site root. `base: '/'`
// makes asset URLs absolute (/assets/…), so nested routes such as /playground/documents and
// /policies/:name load their bundles (M1 changed this from './', which only worked one segment deep).
// Hosting under a path prefix needs a gateway rewrite to the root.
export default defineConfig({
  base: '/',
```
`frontend/index.html`: `<script src="./theme-init.js"></script>` → `<script src="/theme-init.js"></script>`.

`frontend/src/lib/types.ts` — `TransformOut` gains `segments?: OutputSegment[] | null;` and the file gains (field for field with the pydantic models of Tasks 3, 4, 10, 14):
```ts
// --- M1: documents, entities, policy tooling (mirror the pydantic models field for field) ---
export interface OutputSegment {
  path: string; start: number | null; end: number | null; out_start: number | null; out_end: number | null;
  entity_type: string; method: string; replacement: string;
}
export type DocFormat = 'pdf' | 'docx' | 'image' | 'text' | 'csv';
export type PageSource = 'text_layer' | 'ocr' | 'hybrid' | 'docx' | 'text' | 'csv' | 'empty' | 'unreadable';
export type OcrMode = 'auto' | 'always' | 'never';
export interface PageBox { page: number; x0: number; y0: number; x1: number; y1: number }
export interface PageFinding {
  n: number; entity_type: string; score: number; sensitivity: Sensitivity; detectors: string[]; evidence: string[];
  value_hash: string | null; page: number; line: number | null; path: string; span: [number, number] | null;
  boxes: PageBox[]; method: string | null; reversible: boolean | null; redacted: boolean; replacement: string | null;
  out_span: [number, number] | null; ocr_confidence: number | null; review: boolean;
}
export interface PageSummary {
  page: number; width: number; height: number; unit: 'pt'; source: PageSource; words: number; lines: number;
  ocr_confidence: number | null; findings: PageFinding[]; preview_png_b64: string | null; redacted_preview_png_b64: string | null;
}
export interface RedactionReport {
  boxes: number; pages: number; dpi: number; image_format: string; bytes: number; sha256: string; text_layer_chars: number;
  text_operators: number; annotations: number; ocr_checked: boolean; ocr_pages: number; ocr_hits: number;
  chars_under_boxes: number; passed: boolean;
}
export interface DocumentInfo {
  format: DocFormat; pages: number; ocr_pages: number[]; unreadable_pages: number[]; words: number; boxes: number;
  ocr_engine: string | null; bytes: number; sha256: string; warnings: string[]; timings_ms: Record<string, number>;
  redaction: RedactionReport | null; synthetic_validity: { checked: number; valid: number } | null;
}
export interface DocumentDetectOut extends DetectOut { document: DocumentInfo; pages: PageSummary[] }
export interface DocumentTransformOut extends TransformOut {
  document: DocumentInfo; pages: PageSummary[]; redacted_pdf_b64: string | null; result_csv: string | null;
}
export interface DocumentOptions {
  redacted_pdf?: boolean; ocr?: OcrMode; previews?: boolean; ocr_verify?: boolean | null;
  token_style?: string; locale?: string; consistency?: Record<string, unknown>;
}
export interface PresetSummary extends Preset { version: number; hash: string; mode: 'obfuscate' | 'synthesize'; reversible: boolean }
export interface EntityInfo { name: string; family: string; sensitivity: Sensitivity; description: string; jurisdictions: string[]; builtin: boolean }
export interface EntitiesOut { items: EntityInfo[]; families: string[] }
export interface MethodSpec { method: string; params: Record<string, unknown> }
export interface RecognizerPattern { name: string; regex: string; score: number }
export interface RecognizerSpec {
  name: string; supported_entity: string; supported_language?: string; patterns: RecognizerPattern[];
  deny_list?: string[]; allow_list?: string[]; context?: string[]; sensitivity?: Sensitivity | null;
}
export interface DeclaredField { path: string; entity_type: string; score?: number; sensitivity?: Sensitivity | null }
export interface PolicyDoc {
  name: string; version: number; description: string;
  locale: { default: string; by_country_field: string | null };
  detect: { tiers: string[]; min_score: number; custom_recognizers: RecognizerSpec[]; declared_fields: DeclaredField[] };
  treat: Record<string, MethodSpec>;
  synthesize: { default: MethodSpec; overrides: Record<string, MethodSpec>; keep_non_sensitive: boolean; llm_rewrite_text: boolean };
  consistency: { scope: string; field_scoped: boolean }; output: { token_style: string }; verify: { leakage_check: string };
}
export interface PlanOut { findings: Finding[]; plan: PlanRow[]; record_sensitivity: Sensitivity; trace_id: string }
export interface RecognizerMatch { start: number; end: number; entity_type: string; score: number; evidence: string; context: boolean }
export interface RecognizerTestOut { matches: RecognizerMatch[]; count: number; yaml: string }
```

`frontend/src/lib/api.ts` (complete; M0's flat calls are kept, the M1 groups are added, errors carry the API `code`, multipart posts never set `Content-Type` so the browser writes the boundary):
```ts
import type {
  DetectOut, DocumentDetectOut, DocumentOptions, DocumentTransformOut, EntitiesOut, PlanOut, PolicyDoc, Preset,
  PresetSummary, Readiness, RecognizerSpec, RecognizerTestOut, RevealOut, TransformOut,
} from './types';

export class ApiError extends Error {
  constructor(public status: number, message: string, public code: string | null = null) { super(message); }
}

export interface Auth { apiKey: string; tenant: string }

function authHeaders(auth: Auth): Record<string, string> {
  const headers: Record<string, string> = {};
  if (auth.apiKey) headers['X-API-KEY'] = auth.apiKey;
  if (auth.tenant) headers['X-DPS-Tenant'] = auth.tenant;
  return headers;
}

async function toError(res: Response): Promise<ApiError> {
  let detail: unknown = res.statusText;
  let code: string | null = null;
  try {
    const body = (await res.json()) as { detail?: unknown; code?: unknown };
    detail = body.detail ?? detail;
    code = typeof body.code === 'string' ? body.code : null;
  } catch { /* non-JSON error body */ }
  return new ApiError(res.status, typeof detail === 'string' ? detail : JSON.stringify(detail), code);
}

async function call<T>(path: string, auth: Auth, init?: RequestInit): Promise<T> {
  const headers = { 'Content-Type': 'application/json', ...authHeaders(auth) };
  const res = await fetch(path, { ...init, headers: { ...headers, ...(init?.headers as Record<string, string> | undefined) } });
  if (!res.ok) throw await toError(res);
  return (await res.json()) as T;
}

/** Multipart POST: the browser sets the boundary, so no Content-Type header here. */
async function callForm<T>(path: string, auth: Auth, form: FormData): Promise<T> {
  const res = await fetch(path, { method: 'POST', body: form, headers: authHeaders(auth) });
  if (!res.ok) throw await toError(res);
  return (await res.json()) as T;
}

export type Policy = string | Record<string, unknown> | null;
export interface Body { data?: unknown; text?: string; policy?: Policy; options?: Record<string, unknown> }

function documentForm(file: File, policy: Policy, options: DocumentOptions): FormData {
  const form = new FormData();
  form.append('file', file, file.name);
  form.append('request', JSON.stringify({ policy, options }));
  return form;
}

const post = (body: unknown): RequestInit => ({ method: 'POST', body: JSON.stringify(body) });

export const api = {
  readyz: () => fetch('/readyz').then((r) => r.json() as Promise<Readiness>),
  metrics: () => fetch('/metrics').then((r) => r.text()),
  presets: (auth: Auth) => call<Preset[]>('/api/v1/policies/presets', auth),
  detect: (auth: Auth, body: Body) => call<DetectOut>('/api/v1/detect', auth, post(body)),
  obfuscate: (auth: Auth, body: Body) => call<TransformOut>('/api/v1/obfuscate', auth, post(body)),
  synthesize: (auth: Auth, body: Body) => call<TransformOut>('/api/v1/synthesize', auth, post(body)),
  reveal: (auth: Auth, body: { data?: unknown; text?: string; key_material: unknown; reveal_hints: unknown[] }) =>
    call<RevealOut>('/api/v1/reveal', auth, post(body)),
  documents: {
    detect: (auth: Auth, file: File, policy: Policy, options: DocumentOptions) =>
      callForm<DocumentDetectOut>('/api/v1/documents/detect', auth, documentForm(file, policy, options)),
    obfuscate: (auth: Auth, file: File, policy: Policy, options: DocumentOptions) =>
      callForm<DocumentTransformOut>('/api/v1/documents/obfuscate', auth, documentForm(file, policy, options)),
    synthesize: (auth: Auth, file: File, policy: Policy, options: DocumentOptions) =>
      callForm<DocumentTransformOut>('/api/v1/documents/synthesize', auth, documentForm(file, policy, options)),
  },
  entities: (auth: Auth) => call<EntitiesOut>('/api/v1/entities', auth),
  policies: {
    presets: (auth: Auth) => call<PresetSummary[]>('/api/v1/policies/presets', auth),
    validate: (auth: Auth, policy: Policy) => call<PolicyDoc>('/api/v1/policies/validate', auth, post({ policy })),
    plan: (auth: Auth, body: { data?: unknown; text?: string; policy: Policy; mode: 'obfuscate' | 'synthesize' }) =>
      call<PlanOut>('/api/v1/policies/plan', auth, post(body)),
    testRecognizer: (auth: Auth, recognizer: RecognizerSpec, text: string) =>
      call<RecognizerTestOut>('/api/v1/policies/test-recognizer', auth, post({ recognizer, text })),
  },
};

/** Sum a Prometheus counter family from the exposition text (good enough for the overview tiles). */
export function metricTotal(text: string, name: string): number {
  let total = 0;
  for (const line of text.split('\n')) {
    if (line.startsWith(name) && !line.startsWith('#')) {
      const value = Number(line.trim().split(/\s+/).pop());
      if (!Number.isNaN(value)) total += value;
    }
  }
  return total;
}

/** A human message for an ApiError (status, code, detail) — never includes request data. */
export function errorText(e: unknown): string {
  if (e instanceof ApiError) return `${e.status}${e.code ? ` ${e.code}` : ''}: ${e.message}`;
  return e instanceof Error ? e.message : String(e);
}

/** Decode base64 into a Blob (used for the redacted PDF download). */
export function base64Blob(b64: string, type: string): Blob {
  const raw = atob(b64);
  const bytes = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i += 1) bytes[i] = raw.charCodeAt(i);
  return new Blob([bytes], { type });
}
```

`frontend/src/lib/yaml.ts` (policy export; JSON-quoted strings are valid YAML):
```ts
/** A small YAML emitter for policy exports: JSON-shaped values in, block YAML out.
 *  Strings that are not plain identifiers are written JSON-quoted (valid YAML). */
const PLAIN = /^[A-Za-z_][A-Za-z0-9_.\-/]*$/;
const RESERVED = /^(true|false|null|yes|no|on|off|y|n|~)$/i;

function scalar(value: unknown): string {
  if (value === null || value === undefined) return 'null';
  if (typeof value === 'string') return PLAIN.test(value) && !RESERVED.test(value) ? value : JSON.stringify(value);
  return String(value);
}

function isScalar(value: unknown): boolean {
  return value === null || typeof value !== 'object';
}

function isEmpty(value: unknown): boolean {
  return Array.isArray(value) ? value.length === 0 : typeof value === 'object' && value !== null && Object.keys(value).length === 0;
}

function emit(value: unknown, indent: number): string {
  const pad = '  '.repeat(indent);
  if (isScalar(value)) return scalar(value);
  if (Array.isArray(value)) {
    if (value.length === 0) return '[]';
    return value.map((item) => `\n${pad}- ${isScalar(item) || isEmpty(item) ? emit(item, 0) : emit(item, indent + 1).trimStart()}`).join('');
  }
  const entries = Object.entries(value as Record<string, unknown>);
  if (entries.length === 0) return '{}';
  return entries
    .map(([k, v]) => `\n${pad}${scalar(k)}:${isScalar(v) || isEmpty(v) ? ` ${emit(v, 0)}` : emit(v, indent + 1)}`)
    .join('');
}

export function toYaml(value: unknown): string {
  return `${emit(value, 0).replace(/^\n/, '')}\n`;
}
```

`frontend/src/components/OcrStatus.tsx` (the OCR readiness component — reads `/readyz` `components.ocr`):
```tsx
import { useEffect, useState } from 'react';
import { api } from '../lib/api';
import type { Readiness } from '../lib/types';
import { Tone } from './Chip';

export interface OcrState { ok: boolean; detail: string; engine: string | null }

const ENGINE_LABEL: Record<string, string> = { 'azure-read-v3.2': 'Azure Read v3.2' };

export function ocrState(ready: Readiness | null): OcrState | null {
  const component = ready?.components?.ocr;
  if (!component) return null;
  return { ok: component.ok, detail: component.detail, engine: typeof component.engine === 'string' ? component.engine : null };
}

/** The OCR readiness component (`/readyz` → components.ocr): engine, up/down, or not configured. */
export function OcrStatus({ compact = false }: { compact?: boolean }): JSX.Element {
  const [state, setState] = useState<OcrState | null>(null);
  const [loaded, setLoaded] = useState(false);
  useEffect(() => {
    let alive = true;
    api.readyz().then((r) => { if (alive) { setState(ocrState(r)); setLoaded(true); } })
      .catch(() => { if (alive) setLoaded(true); });
    return () => { alive = false; };
  }, []);
  if (!loaded) return <span className="faint" data-testid="ocr-status">OCR …</span>;
  if (!state || state.detail === 'unconfigured') {
    return (
      <span data-testid="ocr-status"><Tone tone="slate">OCR not configured</Tone>
        {!compact && <span className="faint"> · scanned pages and images cannot be read</span>}</span>
    );
  }
  const name = (state.engine && ENGINE_LABEL[state.engine]) || state.engine || 'OCR';
  return (
    <span data-testid="ocr-status"><Tone tone={state.ok ? 'emerald' : 'rose'}>{name} · {state.ok ? 'up' : state.detail}</Tone>
      {!compact && <span className="faint"> · Read v3.2 contract (org gateway / local mock)</span>}</span>
  );
}
```

`frontend/src/pages/Overview.tsx`: the page-head sentence becomes "Milestone M1 · Layer 1 complete: JSON, text and documents (OCR, redacted PDFs), schema synthesis. Connectors and the UI scanner arrive with M2–M4." (the readiness table already lists `ocr` from `/readyz`). `frontend/src/components/Layout.tsx`: the sidebar foot reads `v0.2.0 · M1`.

- [ ] **Step 1: Generate the demo documents** — `python tools/make_demo_docs.py` → six files under `demo/documents/` and `frontend/public/demo/kyc-scanned.pdf`; run it twice and `shasum demo/documents/*` must not change.

- [ ] **Step 2: Implement the frontend changes** above.

- [ ] **Step 3: Gates** — `cd frontend && npm run typecheck && npm run build && grep -q 'src="/assets/' dist/index.html && grep -q 'src="/theme-init.js"' dist/index.html && cd ..` → clean build (verified while writing: 45 modules, ≈ 232 kB JS); Python gates green.

- [ ] **Step 4: Commit**
```bash
git add tools/make_demo_docs.py demo/documents frontend/public/demo/kyc-scanned.pdf frontend/vite.config.ts frontend/index.html frontend/src/lib/api.ts frontend/src/lib/types.ts frontend/src/lib/yaml.ts frontend/src/components/OcrStatus.tsx frontend/src/pages/Overview.tsx frontend/src/components/Layout.tsx
git commit -m "feat(console): absolute asset base for nested routes, typed M1 API, OCR readiness; synthetic demo documents" -- tools/make_demo_docs.py demo/documents frontend/public/demo/kyc-scanned.pdf frontend/vite.config.ts frontend/index.html frontend/src/lib/api.ts frontend/src/lib/types.ts frontend/src/lib/yaml.ts frontend/src/components/OcrStatus.tsx frontend/src/pages/Overview.tsx frontend/src/components/Layout.tsx
```

---
### Task 17: Console — Playground Document tab (mockup 08)

**Files:**
- Create: `frontend/src/components/PageViewer.tsx`, `frontend/src/pages/PlaygroundDocuments.tsx`
- Modify: `frontend/src/App.tsx`, `frontend/src/pages/Playground.tsx`, `frontend/src/styles.css`

**Page contract (built to `docs/design/mockups/08-documents.html`):** route `/playground/documents` (component `PlaygroundDocuments`, contract §14). Page head: title, policy segmented control (the obfuscation presets, plus "draft from Policies" when the Policies page handed one over), **Copy as curl** (uses `$DPS_API_KEY`, never the key in the settings). Input card: file chooser + drag-and-drop (PDF/DOCX/PNG/JPEG/TXT/CSV), **Load demo KYC scan** (`/demo/kyc-scanned.pdf`), the JSON/Text/Document segment (JSON and Text return to `/playground` with `state.kind`), OCR control = the `OcrStatus` readiness component plus an `auto|always|never` segment (replaces the mockup's "Azure / Local Tesseract" switch: DPS has one OCR contract — see Open contract questions), Output segment **Redact / Obfuscate text / Synthesize text** and the run button ("Redact N pages"). The stepper shows Upload → OCR (· N words, or text layer) → Detect (· N findings) → Redact (· N boxes) / Obfuscate / Synthesize (· N values) → Verify (· N leaks), with the timing line from `document.timings_ms`. Three columns: **Source** (page preview with numbered boxes coloured critical/sensitive/kept, hover links box ↔ finding, page strip of thumbnails with per-page counts), **Findings** (grouped by page, OCR engine and confidence per page, `l.N` line, evidence, "same value as K" from `value_hash`, "kept by policy", **review** for low OCR confidence), **Output** (redact: the produced PDF's page preview + **Download redacted PDF** (`<name>.redacted.pdf` from `redacted_pdf_b64`); obfuscate/synthesize: the replacement tokens laid over the source page at their boxes, font sized in container-height units). Below: **Text extraction** (the `result` text with tokens highlighted from `out_span`; CSV results show `result_csv`) and the key/plan/verify card (key material card with **Reveal text with key** → `POST /api/v1/reveal`, seed callout for synthesize, methods, consistency, synthetic validity, OCR, LLM "raw values sent: 0", warnings, and the **Verified** line built from `report.verification` and `document.redaction`, marked `data-testid="verify-ok"`). Key material and results live in React state only (contract §14). Every request sends `options.previews: true`; `redacted_pdf` only in Redact mode for PDF/image files.

`frontend/src/components/PageViewer.tsx` (box geometry: the API's boxes are PDF points with a top-left origin on the normalised page, so `left = x0 / width` etc. in percent; token text is sized in `cqh` against the frame, which declares `container-type: size`):
```tsx
import type { PageFinding, PageSummary } from '../lib/types';

export type ViewerMode = 'source' | 'redacted' | 'tokens';

function tone(f: PageFinding): string {
  if (!f.redacted && f.method === 'keep') return 'kept';
  return f.sensitivity === 'CRITICAL' ? 'crit' : 'sens';
}

/** One page image with finding boxes positioned in percent of the page (boxes are in PDF points,
 *  origin top-left, exactly as the API returns them). `tokens` lays each replacement over its box;
 *  `redacted` shows the produced PDF page, whose boxes are already burned in. */
export function PageViewer({ page, mode, hot, onHot, synthetic = false }: {
  page: PageSummary; mode: ViewerMode; hot: number | null; onHot: (n: number | null) => void; synthetic?: boolean;
}): JSX.Element {
  const image = mode === 'redacted' ? page.redacted_preview_png_b64 : page.preview_png_b64;
  const pct = (v: number, of: number): string => `${((v / of) * 100).toFixed(3)}%`;
  return (
    <div className="page-frame" style={{ aspectRatio: `${page.width} / ${page.height}` }} data-testid="page-frame">
      {image ? <img src={`data:image/png;base64,${image}`} alt={`Page ${page.page}`} />
        : <div className="page-missing">No preview for this page</div>}
      {mode !== 'redacted' && page.findings.flatMap((f) => f.boxes.map((b, i) => {
        const style = { left: pct(b.x0, page.width), top: pct(b.y0, page.height), width: pct(b.x1 - b.x0, page.width), height: pct(b.y1 - b.y0, page.height) };
        if (mode === 'tokens') {
          if (!f.replacement) return null;
          const fontSize = `${(((b.y1 - b.y0) / page.height) * 100 * 0.72).toFixed(3)}cqh`;
          return (
            <span key={`${f.n}-${i}`} className={`page-tok${synthetic ? ' syn' : ''}${hot === f.n ? ' hot' : ''}`} style={{ ...style, fontSize }}
              onMouseEnter={() => onHot(f.n)} onMouseLeave={() => onHot(null)} title={`${f.entity_type} · ${f.method ?? ''}`}>
              {i === 0 ? f.replacement : ''}
            </span>
          );
        }
        return (
          <span key={`${f.n}-${i}`} className={`page-box ${tone(f)}${hot === f.n ? ' hot' : ''}`} style={style} data-testid="page-box"
            onMouseEnter={() => onHot(f.n)} onMouseLeave={() => onHot(null)} title={`${f.n} · ${f.entity_type} · ${f.score.toFixed(2)}`}>
            {i === 0 && <b className="fbadge">{f.n}</b>}
          </span>
        );
      }))}
    </div>
  );
}
```

`frontend/src/pages/PlaygroundDocuments.tsx`:
```tsx
import { useEffect, useMemo, useRef, useState, type DragEvent } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { EntityChip, Tone } from '../components/Chip';
import { OcrStatus } from '../components/OcrStatus';
import { PageViewer } from '../components/PageViewer';
import { useSettings } from '../hooks/useSettings';
import { api, base64Blob, errorText, type Policy } from '../lib/api';
import type { DocumentTransformOut, OcrMode, PageFinding, PresetSummary } from '../lib/types';

type OutMode = 'red' | 'obf' | 'syn';
const ACCEPT = '.pdf,.docx,.png,.jpg,.jpeg,.txt,.csv';
const DEMO = '/demo/kyc-scanned.pdf';
const MODE_LABEL: Record<OutMode, string> = { red: 'Redact', obf: 'Obfuscate text', syn: 'Synthesize text' };

function extension(name: string): string { return (name.split('.').pop() ?? '').toUpperCase().slice(0, 4); }
function sizeText(bytes: number): string { return bytes > 1 << 20 ? `${(bytes / (1 << 20)).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`; }
function seconds(ms: number | undefined): string { return ms === undefined ? '—' : `${(ms / 1000).toFixed(1)} s`; }

/** One entry per finding number (a finding spanning two pages appears on both). */
function uniqueFindings(out: DocumentTransformOut): PageFinding[] {
  const seen = new Map<number, PageFinding>();
  for (const p of out.pages) for (const f of p.findings) if (!seen.has(f.n)) seen.set(f.n, f);
  return [...seen.values()].sort((a, b) => a.n - b.n);
}

/** The result text with each replacement wrapped as a token chip (by its output span). */
function TokenText({ text, findings, synthetic }: { text: string; findings: PageFinding[]; synthetic: boolean }): JSX.Element {
  const spans = findings.filter((f) => f.out_span && f.replacement).sort((a, b) => (a.out_span![0] - b.out_span![0]));
  const parts: JSX.Element[] = [];
  let cursor = 0;
  for (const f of spans) {
    const [start, end] = f.out_span!;
    if (start < cursor) continue;
    parts.push(<span key={`t${cursor}`}>{text.slice(cursor, start)}</span>);
    parts.push(<span key={`k${f.n}`} className={`tok${synthetic ? ' syn' : ''}`} title={`${f.n} · ${f.entity_type}`}>{text.slice(start, end)}</span>);
    cursor = end;
  }
  parts.push(<span key="tail">{text.slice(cursor)}</span>);
  return <>{parts}</>;
}

export function PlaygroundDocuments(): JSX.Element {
  const { apiKey, tenant } = useSettings();
  const auth = useMemo(() => ({ apiKey, tenant }), [apiKey, tenant]);
  const navigate = useNavigate();
  const location = useLocation();
  const inline = (location.state as { policy?: Policy } | null)?.policy ?? null;
  const input = useRef<HTMLInputElement>(null);
  const [presets, setPresets] = useState<PresetSummary[]>([]);
  const [policy, setPolicy] = useState<Policy>(inline ?? 'gdpr-pseudonymise');
  const [file, setFile] = useState<File | null>(null);
  const [mode, setMode] = useState<OutMode>('red');
  const [ocr, setOcr] = useState<OcrMode>('auto');
  const [busy, setBusy] = useState<'run' | 'reveal' | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [out, setOut] = useState<{ mode: OutMode; res: DocumentTransformOut } | null>(null);
  const [page, setPage] = useState(1);
  const [hot, setHot] = useState<number | null>(null);
  const [revealed, setRevealed] = useState<string | null>(null);
  const [copied, setCopied] = useState<string | null>(null);

  useEffect(() => { api.policies.presets(auth).then(setPresets).catch(() => setPresets([])); }, [auth]);

  const isPaged = (f: File | null): boolean => !!f && /\.(pdf|png|jpe?g)$/i.test(f.name);
  const res = out?.res ?? null;
  const findings = res ? uniqueFindings(res) : [];
  const current = res?.pages.find((p) => p.page === page) ?? res?.pages[0] ?? null;
  const ocrWords = res ? res.pages.filter((p) => p.source === 'ocr' || p.source === 'hybrid').reduce((n, p) => n + p.words, 0) : 0;
  const verified = !!res && (res.report.verification?.passed ?? false) && (res.document.redaction?.passed ?? true);
  const sameAs = useMemo(() => {
    const first = new Map<string, number>();
    const out_: Record<number, number> = {};
    for (const f of findings) {
      if (!f.value_hash) continue;
      const k = `${f.entity_type}:${f.value_hash}`;
      if (first.has(k)) out_[f.n] = first.get(k)!; else first.set(k, f.n);
    }
    return out_;
  }, [findings]);

  function choose(f: File | null): void { setFile(f); setOut(null); setError(null); setRevealed(null); setPage(1); }
  function onDrop(e: DragEvent<HTMLElement>): void { e.preventDefault(); choose(e.dataTransfer.files?.[0] ?? null); }
  async function loadDemo(): Promise<void> {
    const blob = await fetch(DEMO).then((r) => { if (!r.ok) throw new Error('demo scan missing'); return r.blob(); });
    choose(new File([blob], 'kyc-scanned.pdf', { type: 'application/pdf' }));
  }

  async function run(): Promise<void> {
    if (!file) return;
    setBusy('run'); setError(null); setRevealed(null);
    try {
      const options = { ocr, previews: true, redacted_pdf: mode === 'red' && isPaged(file) };
      const call = mode === 'syn' ? api.documents.synthesize : api.documents.obfuscate;
      setOut({ mode, res: await call(auth, file, policy, options) });
      setPage(1);
    } catch (e) { setError(errorText(e)); } finally { setBusy(null); }
  }

  async function reveal(): Promise<void> {
    if (!res || !res.key_material || typeof res.result !== 'string') return;
    setBusy('reveal'); setError(null);
    try {
      const r = await api.reveal(auth, { text: res.result, key_material: res.key_material, reveal_hints: res.reveal_hints });
      setRevealed(typeof r.result === 'string' ? r.result : JSON.stringify(r.result, null, 2));
    } catch (e) { setError(errorText(e)); } finally { setBusy(null); }
  }

  function copy(label: string, text: string): void {
    void navigator.clipboard?.writeText(text);
    setCopied(label); window.setTimeout(() => setCopied(null), 1400);
  }

  function download(): void {
    if (!res?.redacted_pdf_b64 || !file) return;
    const url = URL.createObjectURL(base64Blob(res.redacted_pdf_b64, 'application/pdf'));
    const a = document.createElement('a');
    a.href = url; a.download = `${file.name.replace(/\.[^.]+$/, '')}.redacted.pdf`; a.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 5000);
  }

  const endpoint = `/api/v1/documents/${mode === 'syn' ? 'synthesize' : 'obfuscate'}`;
  const curl = `curl -sS -X POST "${window.location.origin}${endpoint}" -H "X-API-KEY: $DPS_API_KEY"${tenant ? ` -H "X-DPS-Tenant: ${tenant}"` : ''} \\\n  -F "file=@${file?.name ?? 'document.pdf'}" \\\n  -F 'request=${JSON.stringify({ policy, options: { ocr, redacted_pdf: mode === 'red' } })}'`;
  const methods = res ? Object.entries(res.report.methods_used).map(([m, n]) => `${m} ×${n}`).join(' · ') : '';
  const repeats = Object.keys(sameAs).length;
  const timing = res ? `${seconds(res.document.timings_ms.total)} total · OCR ${seconds(res.document.timings_ms.ocr)} · engine ${seconds(res.document.timings_ms.engine)}${res.document.timings_ms.redact !== undefined ? ` · PDF ${seconds(res.document.timings_ms.redact)}` : ''}` : '';
  const syn = out?.mode === 'syn';

  return (
    <main className="page">
      <div className="page-head">
        <div><h1>Try the engine</h1><p>Drop a document. OCR runs through the Read v3.2 contract; detection, redaction and tokens run inside DPS. The LLM never sees a value.</p></div>
        <div className="head-actions">
          <span className="muted" style={{ fontSize: 12.5 }}>Policy</span>
          <div className="seg sm" role="group" aria-label="Policy">
            {(presets.length ? presets.map((p) => p.name) : ['gdpr-pseudonymise']).filter((n) => !n.startsWith('synthetic')).map((n) => (
              <button key={n} className={policy === n ? 'on' : ''} aria-pressed={policy === n} onClick={() => setPolicy(n)}>{n}</button>))}
            {inline !== null && <button className={typeof policy === 'object' ? 'on' : ''} onClick={() => setPolicy(inline)}>draft from Policies</button>}
          </div>
          <button className="btn btn-sm" onClick={() => copy('curl', curl)}>{copied === 'curl' ? 'Copied' : 'Copy as curl'}</button>
        </div>
      </div>
      {error && <div className="err" role="alert">{error}</div>}

      <section className="card" onDragOver={(e) => e.preventDefault()} onDrop={onDrop}>
        <div className="card-head"><div className="card-title">Input</div>
          <div className="head-actions">
            <button className="btn btn-sm" onClick={() => input.current?.click()}>{file ? 'Replace file' : 'Choose file'}</button>
            <button className="btn btn-sm btn-ghost" onClick={() => { void loadDemo().catch((e: unknown) => setError(errorText(e))); }}>Load demo KYC scan</button>
            <div className="seg sm" role="group" aria-label="Input kind">
              <button onClick={() => navigate('/playground', { state: { kind: 'json' } })}>JSON</button>
              <button onClick={() => navigate('/playground', { state: { kind: 'text' } })}>Text</button>
              <button className="on" aria-pressed="true">Document</button>
            </div>
          </div></div>
        <input ref={input} type="file" accept={ACCEPT} hidden data-testid="file-input" onChange={(e) => choose(e.target.files?.[0] ?? null)} />
        <div className="inrow">
          <div className="file">
            <div className="fileicon">{file ? extension(file.name) : 'DOC'}</div>
            <div>{file ? <><div className="name">{file.name}</div><div className="sub">{sizeText(file.size)}{res ? ` · ${res.document.pages} page${res.document.pages === 1 ? '' : 's'} · ${res.document.format}` : ''}</div></>
              : <><div className="name">Drop a PDF, DOCX, PNG, JPEG, TXT or CSV here</div><div className="sub">Up to the server's document limit · nothing is stored</div></>}</div>
          </div>
          <div className="ctl"><span className="lbl">OCR</span>
            <div className="row"><OcrStatus compact />
              <div className="seg sm" role="group" aria-label="OCR mode">
                {(['auto', 'always', 'never'] as OcrMode[]).map((m) => <button key={m} className={ocr === m ? 'on' : ''} aria-pressed={ocr === m} onClick={() => setOcr(m)}>{m}</button>)}
              </div></div></div>
          <div className="ctl"><span className="lbl">Output</span>
            <div className="runrow">
              <div className="seg sm" role="group" aria-label="Output">
                {(['red', 'obf', 'syn'] as OutMode[]).map((m) => <button key={m} data-testid={`mode-${m}`} className={mode === m ? 'on' : ''} aria-pressed={mode === m} onClick={() => setMode(m)}>{MODE_LABEL[m]}</button>)}
              </div>
              <button className="btn btn-primary" data-testid="run" disabled={!file || busy !== null} onClick={() => { void run(); }}>
                {busy === 'run' ? 'Working…' : mode === 'red' ? `Redact${res ? ` ${res.document.pages} page${res.document.pages === 1 ? '' : 's'}` : ''}` : MODE_LABEL[mode]}
              </button>
            </div></div>
        </div>
        <div className="stepwrap">
          <div className="stepper" aria-label="Progress">
            <div className={`step ${file ? 'done' : 'on'}`}><span className="n">{file ? '✓' : '1'}</span>Upload</div><div className="step-sep" />
            <div className={`step ${res ? 'done' : ''}`}><span className="n">{res ? '✓' : '2'}</span>OCR <span className="faint">· {res ? (res.document.ocr_pages.length ? `${ocrWords} words` : 'text layer') : '—'}</span></div><div className="step-sep" />
            <div className={`step ${res ? 'done' : ''}`}><span className="n">{res ? '✓' : '3'}</span>Detect <span className="faint">· {res ? `${findings.length} findings` : '—'}</span></div><div className="step-sep" />
            <div className={`step ${res ? 'done' : ''}`}><span className="n">{res ? '✓' : '4'}</span>{out ? MODE_LABEL[out.mode].split(' ')[0] : MODE_LABEL[mode].split(' ')[0]} <span className="faint">· {res ? (out?.mode === 'red' && res.document.redaction ? `${res.document.redaction.boxes} boxes` : `${findings.filter((f) => f.redacted).length} values`) : '—'}</span></div><div className="step-sep" />
            <div className={`step ${verified ? 'done' : ''}`} data-testid={verified ? 'verify-ok' : undefined}><span className="n">{verified ? '✓' : '5'}</span>Verify <span className="faint">· {res ? `${res.document.redaction?.ocr_hits ?? 0} leaks` : '—'}</span></div>
          </div>
          {res && <span className="timing">{timing}</span>}
        </div>
      </section>

      {res && current && (
        <div className="docgrid">
          <section className="card pcard">
            <div className="card-head"><div className="card-title">Source · page {current.page} of {res.pages.length}</div>
              <div className="legend"><span><i style={{ background: 'var(--rose)' }} />critical</span><span><i style={{ background: 'var(--amber)' }} />sensitive</span><span><i style={{ background: 'var(--ink-3)' }} />kept</span></div></div>
            <PageViewer page={current} mode="source" hot={hot} onHot={setHot} />
            <div className="strip">
              {res.pages.map((p) => (
                <button key={p.page} type="button" className={`thumb${p.page === current.page ? ' on' : ''}`} aria-label={`Show page ${p.page}`} onClick={() => setPage(p.page)}>
                  <span className="thumb-paper">{p.preview_png_b64 ? <img src={`data:image/png;base64,${p.preview_png_b64}`} alt="" /> : null}</span>
                  <span>p{p.page} · <b>{new Set(p.findings.map((f) => f.n)).size}</b></span>
                </button>))}
              <div className="strip-meta">{res.document.redaction ? `${res.document.redaction.dpi} dpi` : res.document.format}<br /><b>{res.document.boxes} boxes on {res.pages.length} page{res.pages.length === 1 ? '' : 's'}</b></div>
            </div>
          </section>

          <section className="card" data-testid="findings">
            <div className="card-head"><div className="card-title">Findings <span className="faint">· {findings.length}</span></div>
              <EntityChip type={res.report.record_sensitivity} sensitivity={res.report.record_sensitivity as PageFinding['sensitivity']} /></div>
            <div className="fhead-sub">Grouped by page. Numbers match the boxes on the page.</div>
            {res.pages.map((p) => (
              <div key={p.page} className={`grp${p.page === current.page ? ' open' : ''}`}>
                <button className="grp-h" aria-expanded={p.page === current.page} onClick={() => setPage(p.page)}>
                  <span className="t"><span className="chev">▶</span> Page {p.page}</span><span className="faint">{new Set(p.findings.map((f) => f.n)).size}</span>
                  <span className="faint grp-src">{p.source === 'ocr' || p.source === 'hybrid' ? `${res.document.ocr_engine ?? 'OCR'}${p.ocr_confidence !== null ? ` · conf ${p.ocr_confidence.toFixed(2)}` : ''}` : p.source.replace('_', ' ')}</span>
                </button>
                {p.page === current.page && <div className="grp-b">
                  {p.findings.map((f) => (
                    <div key={`${f.n}-${p.page}`} className={`fd${hot === f.n ? ' hot' : ''}`} tabIndex={0} onMouseEnter={() => setHot(f.n)} onMouseLeave={() => setHot(null)} onFocus={() => setHot(f.n)} onBlur={() => setHot(null)}>
                      <span className="fd-n">{f.n}</span><EntityChip type={f.entity_type} sensitivity={f.sensitivity} />
                      <span className="mono muted">{f.score.toFixed(2)}</span>
                      <span className="why">{f.line !== null ? <span className="mono">l.{f.line}</span> : null} · {f.evidence.join(' · ')}{sameAs[f.n] ? ` · same value as ${sameAs[f.n]}` : ''}{f.method === 'keep' ? ' · kept by policy' : ''}{f.review ? <> · <b>review</b></> : null}</span>
                    </div>))}
                  {p.findings.length === 0 && <p className="muted">Nothing found on this page.</p>}
                </div>}
              </div>))}
            {res.document.unreadable_pages.length > 0 && <div className="callout amber">Pages {res.document.unreadable_pages.join(', ')} could not be read (no OCR). Their content was not scanned.</div>}
          </section>

          <section className="card pcard">
            <div className="card-head"><div className="card-title">{out!.mode === 'red' ? 'Output · redacted PDF' : out!.mode === 'obf' ? 'Output · obfuscated text, page view' : 'Output · synthetic text, page view'}</div>
              {out!.mode === 'red' ? <Tone tone="teal">flattened</Tone> : syn ? <Tone tone="violet">no path back</Tone> : <Tone tone="teal">reversible</Tone>}</div>
            <PageViewer page={current} mode={out!.mode === 'red' && res.redacted_pdf_b64 ? 'redacted' : 'tokens'} hot={hot} onHot={setHot} synthetic={syn} />
            <div className="out-foot">
              <div className="meta">{out!.mode === 'red' && res.document.redaction
                ? <><span className="mono">{file?.name.replace(/\.[^.]+$/, '')}.redacted.pdf</span><br />{res.document.redaction.pages} pages · {sizeText(res.document.redaction.bytes)} · page {current.page} shown</>
                : <>Text laid over page {current.page} · no PDF is produced in this mode</>}</div>
              {out!.mode === 'red' && res.redacted_pdf_b64
                ? <button className="btn btn-primary btn-sm" onClick={download}>Download redacted PDF</button>
                : <button className="btn btn-sm" onClick={() => copy('text', String(res.result))}>{copied === 'text' ? 'Copied' : syn ? 'Copy synthetic text' : 'Copy obfuscated text'}</button>}
            </div>
            <p className="out-note">{out!.mode === 'red' ? `${res.document.redaction?.boxes ?? 0} boxes are burned into the page images and the output has no text layer. No key can bring them back.`
              : syn ? 'Every value is replaced by a rule-valid fake from one persona per document.' : 'Tokens keep the format of each value. Reveal restores the originals with the key below.'}</p>
          </section>
        </div>)}

      {res && (
        <div className="two">
          <section className="card">
            <div className="card-head"><div className="card-title">{syn ? 'Text extraction · synthetic' : 'Text extraction · obfuscated'} {syn ? <Tone tone="violet">no path back</Tone> : <Tone tone="teal">reversible with key</Tone>}</div>
              <div className="btnrow"><span className="faint">{res.document.pages} page{res.document.pages === 1 ? '' : 's'}</span>
                <button className="btn btn-sm" onClick={() => copy('text', typeof res.result === 'string' ? res.result : res.result_csv ?? JSON.stringify(res.result))}>{copied === 'text' ? 'Copied' : 'Copy text'}</button></div></div>
            <div className="pane textpane" data-testid="result-text">{revealed !== null ? revealed
              : typeof res.result === 'string' ? <TokenText text={res.result} findings={findings} synthetic={syn} /> : (res.result_csv ?? JSON.stringify(res.result, null, 2))}</div>
          </section>
          <section className="card">
            {res.key_material && (
              <div className="keycard card" style={{ padding: '12px 14px' }}>
                <div className="card-head" style={{ marginBottom: 6 }}><div className="card-title">Key material · returned once, never stored</div><Tone tone="teal">{res.key_material.alg}</Tone></div>
                <div className="mono" style={{ fontSize: 12, wordBreak: 'break-all' }}>kid {res.key_material.kid} · dek {res.key_material.dek ? `${res.key_material.dek.slice(0, 6)}…${res.key_material.dek.slice(-4)}` : 'wrapped'}<br /><span className="faint">reveal hints: {res.reveal_hints.length} spans · locators, methods and types, no originals</span></div>
                <div className="btnrow" style={{ marginTop: 8 }}>
                  <button className="btn btn-sm" onClick={() => copy('key', JSON.stringify(res.key_material))}>{copied === 'key' ? 'Key copied' : 'Copy key'}</button>
                  {revealed === null ? <button className="btn btn-sm btn-primary" disabled={busy !== null} onClick={() => { void reveal(); }}>{busy === 'reveal' ? 'Revealing…' : 'Reveal text with key'}</button>
                    : <span className="faint">original text restored · POST /api/v1/reveal</span>}
                </div>
                <div className="faint" style={{ fontSize: 12, marginTop: 6 }}>{out?.mode === 'red' ? 'Applies to the text extraction only. The redacted PDF has no key.' : 'Pass the same key back on the next call to get the same tokens.'}</div>
              </div>)}
            {res.synthesis_seed && <div className="callout amber" style={{ fontSize: 12 }}>Synthesis seed echoed for cross-call consistency. It is not a reveal key: generation is one-way.</div>}
            <div className="diffrow"><span className="k">methods</span><span>{methods || '—'}</span></div>
            <div className="diffrow"><span className="k">consistency</span><span>{repeats ? `${repeats} repeated value${repeats === 1 ? '' : 's'} share one token across pages` : 'no repeated values'}</span></div>
            {res.document.synthetic_validity && <div className="diffrow"><span className="k">validity</span><span>{res.document.synthetic_validity.valid} of {res.document.synthetic_validity.checked} rule-checked surrogates pass their checksums</span></div>}
            <div className="diffrow"><span className="k">ocr</span><span>{res.document.ocr_engine ? `${res.document.ocr_engine} · ${res.document.ocr_pages.length} page${res.document.ocr_pages.length === 1 ? '' : 's'} · ${ocrWords} words · ${seconds(res.document.timings_ms.ocr)}` : 'text layer only'}</span></div>
            <div className="diffrow"><span className="k">llm</span><span>{res.report.llm.calls ? `${res.report.llm.calls} calls` : 'not called for this document'} · raw values sent: <b>{res.report.llm.raw_pii_sent}</b></span></div>
            {res.document.warnings.length > 0 && <div className="diffrow"><span className="k">warnings</span><span className="mono">{res.document.warnings.join(' · ')}</span></div>}
            <div className={`verify${verified ? '' : ' fail'}`}>
              {verified ? <span><b>Verified.</b> Leakage sweep passed: 0 of {res.report.verification?.checked ?? 0} originals in the text output.{res.document.redaction ? ` The produced PDF has ${res.document.redaction.text_layer_chars} extractable characters${res.document.redaction.ocr_checked ? ` and an OCR re-read found ${res.document.redaction.ocr_hits} originals (${res.document.redaction.chars_under_boxes} characters read under ${res.document.redaction.boxes} boxes)` : ' (OCR re-read not available)'}.` : ''}</span>
                : <span><b>Not verified.</b> See the report below.</span>}
            </div>
          </section>
        </div>)}
    </main>
  );
}
```

`frontend/src/App.tsx`: import `PlaygroundDocuments` and add `<Route path="/playground/documents" element={<PlaygroundDocuments />} />` after `/playground`.

`frontend/src/pages/Playground.tsx`: import `useLocation, useNavigate` from `react-router-dom`; read the hand-over `const handoff = (useLocation().state as { kind?: Kind; policy?: Policy } | null) ?? null;`; initialise `kind` from `handoff?.kind ?? 'json'` and `policy` (now typed `Policy`) from `handoff?.policy ?? 'gdpr-pseudonymise'`; the policy `<select>` gains an option `draft from Policies` (value `__draft__`, shown only when `handoff.policy` is an object) and maps it back to the inline policy; the Input segment gains a third button `Document` → `navigate('/playground/documents', { state: { policy } })`.

`frontend/src/styles.css` — append (ported from the mockup's `<style>`, with the mockup's paper-drawing rules replaced by `.page-frame`/`.page-box`/`.page-tok` for real page images; the mockup's `.field` and `.fb` names are not reused because the console already has a `.field`):
```css
/* --- M1 · documents (mockup 08) --- */
.head-actions { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; min-width: 0; max-width: 100%; }
.head-actions .seg, .card-head .seg { max-width: 100%; overflow-x: auto; }
.seg.sm button { padding: 6px 11px; font-size: 12.5px; }
.inrow { display: flex; flex-wrap: wrap; gap: 16px 22px; align-items: flex-end; }
.file { display: flex; gap: 12px; align-items: center; flex: 1 1 280px; min-width: 0; }
.fileicon { width: 38px; height: 46px; flex: none; border-radius: 4px 12px 4px 4px; background: var(--surface-3); border: 1px solid var(--border-strong); display: grid; place-items: end center; padding-bottom: 6px; font: 800 9.5px var(--mono); color: var(--ink-2); letter-spacing: .06em; }
.file .name { font-weight: 700; font-family: var(--mono); font-size: 13px; overflow-wrap: anywhere; }
.file .sub { font-size: 12px; color: var(--ink-3); margin-top: 2px; }
.ctl { display: flex; flex-direction: column; gap: 6px; min-width: 0; max-width: 100%; }
.ctl .lbl { font-size: 11px; letter-spacing: .08em; text-transform: uppercase; color: var(--ink-3); font-weight: 700; }
.ctl .seg { max-width: 100%; overflow-x: auto; }
.runrow { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
.stepwrap { border-top: 1px solid var(--border); margin-top: 14px; padding-top: 12px; display: flex; justify-content: space-between; gap: 12px; align-items: center; flex-wrap: wrap; }
.stepwrap .stepper { flex-wrap: wrap; row-gap: 8px; }
.stepwrap .step-sep { width: 28px; margin: 0 8px; }
.timing { font-size: 12px; color: var(--ink-3); font-family: var(--mono); margin-left: auto; }
.docgrid { display: grid; grid-template-columns: minmax(0, 1fr) 312px minmax(0, 1fr); gap: 16px; align-items: start; }
.pcard { padding: 14px 14px 16px; }
.page-frame { position: relative; width: 100%; max-width: 560px; margin: 0 auto; container-type: size; background: #fff; border-radius: 2px; overflow: hidden; box-shadow: 0 0 0 1px var(--border-strong), 0 14px 30px -16px rgba(0,0,0,.6); }
.page-frame img { position: absolute; inset: 0; width: 100%; height: 100%; display: block; }
.page-missing { position: absolute; inset: 0; display: grid; place-items: center; color: #64748b; font-size: 12px; }
.page-box { position: absolute; --c: #b45309; box-shadow: 0 0 0 2px var(--c); background: color-mix(in srgb, var(--c) 14%, transparent); border-radius: 2px; transition: box-shadow .12s; }
.page-box.crit { --c: #be123c; } .page-box.sens { --c: #b45309; } .page-box.kept { --c: #64748b; }
.page-box.hot { box-shadow: 0 0 0 3px var(--c), 0 0 0 7px color-mix(in srgb, var(--c) 22%, transparent); z-index: 2; }
.fbadge { position: absolute; top: -9px; right: -9px; min-width: 16px; height: 16px; padding: 0 4px; border-radius: 99px; background: var(--c); color: #fff; font: 700 10px/16px var(--font); text-align: center; }
.page-tok { position: absolute; display: flex; align-items: center; overflow: hidden; white-space: nowrap; background: #e6f4f1; color: #0d5c56; font-family: var(--mono); line-height: 1; box-shadow: 0 0 0 1px rgba(15,118,110,.6); border-radius: 2px; padding: 0 1px; }
.page-tok.syn { background: #efe9fd; color: #5b21b6; box-shadow: 0 0 0 1px rgba(109,40,217,.6); }
.page-tok.hot { box-shadow: 0 0 0 2px #0f766e; z-index: 2; }
.strip { display: flex; gap: 12px; margin-top: 14px; padding: 0 4px; align-items: flex-end; flex-wrap: wrap; }
.thumb { background: none; border: 0; padding: 0; cursor: pointer; display: flex; flex-direction: column; gap: 6px; align-items: center; color: var(--ink-2); font: 600 11.5px var(--font); }
.thumb-paper { width: 58px; display: block; border-radius: 3px; overflow: hidden; box-shadow: 0 0 0 1px var(--border-strong); background: #fff; }
.thumb-paper img { width: 100%; display: block; }
.thumb.on .thumb-paper { box-shadow: 0 0 0 2px var(--teal); }
.thumb.on, .thumb b { color: var(--ink); }
.strip-meta { margin-left: auto; text-align: right; font-size: 12px; color: var(--ink-3); line-height: 1.5; }
.fhead-sub { font-size: 12px; color: var(--ink-3); margin: -6px 0 8px; }
.grp { border-top: 1px solid var(--border); }
.grp-h { width: 100%; display: grid; grid-template-columns: 1fr auto; gap: 3px 8px; align-items: center; padding: 9px 4px; background: none; border: 0; color: var(--ink); cursor: pointer; text-align: left; font: inherit; border-radius: 8px; }
.grp-h:hover { background: var(--surface-2); }
.grp-h .t { font-weight: 700; font-size: 13px; }
.grp-h .chev { color: var(--ink-3); font-size: 11px; display: inline-block; transition: transform .12s; }
.grp.open .chev { transform: rotate(90deg); }
.grp-src { grid-column: 1 / -1; font-size: 11.5px; }
.fd { display: grid; grid-template-columns: 22px auto 1fr; gap: 4px 8px; align-items: center; padding: 7px 4px; border-radius: 8px; font-size: 12.5px; }
.fd:hover, .fd.hot, .fd:focus-visible { background: var(--surface-2); outline: none; }
.fd-n { width: 20px; height: 20px; border-radius: 99px; background: var(--surface-3); display: grid; place-items: center; font: 700 10.5px var(--font); color: var(--ink-2); }
.fd .why { grid-column: 2 / -1; color: var(--ink-3); font-size: 11.5px; }
.out-foot { display: flex; justify-content: space-between; align-items: center; gap: 10px; margin-top: 12px; flex-wrap: wrap; }
.out-foot .meta { font-size: 12px; color: var(--ink-3); line-height: 1.5; }
.out-note { font-size: 12px; color: var(--ink-3); margin: 8px 0 0; }
.two { display: grid; grid-template-columns: minmax(0, 1.25fr) minmax(0, 1fr); gap: 16px; align-items: start; }
.btnrow { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.textpane { max-height: 420px; }
.tok.rev { background: var(--emerald-soft); color: var(--emerald); border-color: var(--emerald-border); }
.diffrow { display: grid; grid-template-columns: 96px minmax(0, 1fr); gap: 8px; font-size: 12.5px; padding: 7px 0; border-top: 1px solid var(--border); }
.diffrow .k { color: var(--ink-3); }
.verify { margin-top: 12px; padding: 10px 12px; border-radius: var(--r-md); background: var(--emerald-soft); border: 1px solid var(--emerald-border); font-size: 12.5px; }
.verify.fail { background: var(--rose-soft); border-color: var(--rose-border); }
@media (max-width: 1200px) { .docgrid, .two { grid-template-columns: minmax(0, 1fr); } }
```

- [ ] **Step 1: Implement** the files above.
- [ ] **Step 2: Gates** — `cd frontend && npm run typecheck && npm run build` → clean (verified while writing against React 18.3 / TS 5.6 / Vite 5.4). Python gates green.
- [ ] **Step 3: Manual check** — `DPS_AUTH_ENABLED=false DPS_LLM_PROVIDER=stub DPS_OCR_PROVIDER=azure DPS_AZURE_VISION_ENDPOINT=http://127.0.0.1:5016 DPS_AZURE_VISION_KEY=mock-key uvicorn dps.app:create_app --factory --port 8400` with the mock running; open `http://localhost:8400/playground/documents`, Load demo KYC scan, Redact → boxes on both pages, Verified line, PDF downloads; switch to Obfuscate text → tokens over the page and Reveal works; Synthesize → violet tokens and validity line. (Task 20 automates this check.)
- [ ] **Step 4: Commit**
```bash
git add frontend/src/components/PageViewer.tsx frontend/src/pages/PlaygroundDocuments.tsx frontend/src/App.tsx frontend/src/pages/Playground.tsx frontend/src/styles.css
git commit -m "feat(console): Playground Document tab — page boxes, findings by page, redacted PDF, token page view, reveal" -- frontend/src/components/PageViewer.tsx frontend/src/pages/PlaygroundDocuments.tsx frontend/src/App.tsx frontend/src/pages/Playground.tsx frontend/src/styles.css
```

---

### Task 18: Console — Policies page (mockup 05)

**Files:**
- Create: `frontend/src/pages/Policies.tsx`
- Modify: `frontend/src/App.tsx`, `frontend/src/styles.css`

**Page contract (built to `docs/design/mockups/05-policies.html`, scoped to what M1's routes back):** routes `/policies` and `/policies/:name` (component `Policies`). Left: **Presets** from `GET /policies/presets` (name, version, hash prefix, mode chip, reversible/irreversible/one-way), and the on-this-page index. Header card: name, mode chip, `v<version> shipped`, draft badge, description, preset hash with **Copy hash**, the six facts (locale, detection tiers, minimum score, consistency, token style, leakage check — from `POST /policies/validate {policy: name}`), and the pseudonymised-vs-synthetic legal callout. **Treatment per entity type**: rows = the policy's `treat` and `synthesize.overrides` entries, every CRITICAL entity and any drafted entity (or every entity with **Show all**), columns entity chip, family · jurisdictions (from `GET /entities`), sensitivity, **obfuscate method (editable select → session draft)** with params, synthesize method (keep for LOW when `keep_non_sensitive`), reversible; filters All/Critical/Changed in draft, search; FF1 state from `/readyz` `fpe`. **Declared fields**: preset + draft rows and an inline "Declare a field" row (path, entity type, treatment). **Custom recognizer**: name, score, entity type, pattern, context words, **allow list (never match)** and **deny list (always flag, Presidio semantics)**, test text; a live browser-regex preview plus **Run on the engine** (`POST /policies/test-recognizer` — authoritative Python `re`, offsets only, Presidio YAML) and **Add recognizer to draft**. **Plan preview**: the mockup's sample JSON, obfuscate/synthesize, **Re-run plan** → `POST /policies/plan` with the draft as an inline policy; method counts, fallback chips and the fallback legend. **Version history**: the shipped preset and the session draft only; **Download YAML** (inline policy via `toYaml`), **Use in Playground** (navigates with `state.policy`); **Publish** is disabled with a tooltip — publishing, versions, "jobs using it" and "declared by" need the policy store M5 adds (see Open contract questions). The draft lives in React state only.

`frontend/src/pages/Policies.tsx`:
```tsx
import { useEffect, useMemo, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { EntityChip, Tone } from '../components/Chip';
import { useSettings } from '../hooks/useSettings';
import { api, errorText } from '../lib/api';
import type {
  DeclaredField, EntityInfo, MethodSpec, PlanOut, PolicyDoc, PresetSummary, RecognizerSpec, RecognizerTestOut, Sensitivity,
} from '../lib/types';
import { toYaml } from '../lib/yaml';

/** Methods a draft may choose (the engine's METHOD_NAMES minus keep/surrogate, which have their own columns). */
const METHODS = ['det_token', 'fpe_digits', 'fpe_text', 'email', 'phone', 'date_shift', 'hmac_pseudonym', 'mask_partial',
  'redact', 'null', 'bucket', 'generalize', 'dictionary', 'noise', 'keep', 'external_tokenize'];
const REVERSIBLE = new Set(['det_token', 'fpe_digits', 'fpe_text', 'email', 'phone', 'date_shift']);
const SAMPLE = JSON.stringify({
  customer_id: '884120', full_name: 'Rohan Desai', dob: '1984-02-09',
  tax_ids: { in_pan: 'AGLPD7316K', uk_nino: 'QQ 12 34 56 C', us_ssn: '078-05-1120' },
  email: 'rohan.desai@example-mail.co.uk', cards: [{ pan: '4111 1111 1111 1111', cvv: '737' }, { pan: '3782 822463 10005' }],
  loan: { internal_score: 712, product: 'HL-FLOAT-20Y' }, branch_city: 'London',
  notes: 'Escalated by EMP-204417 (staff id) under CHG-48213',
}, null, 2);
const TEST_TEXT = 'Escalated by EMP-204417 (staff id) to the Pune branch on 3 Oct. Fixture EMP-000000 is seeded test data. CHG-48213 also cites EMP-31882, which has five digits.';

interface Draft { treat: Record<string, MethodSpec>; declared: DeclaredField[]; recognizers: RecognizerSpec[] }
const EMPTY: Draft = { treat: {}, declared: [], recognizers: [] };
type Filter = 'all' | 'critical' | 'draft';

function Params({ spec }: { spec: MethodSpec | undefined }): JSX.Element | null {
  if (!spec) return null;
  return <>{Object.entries(spec.params).map(([k, v]) => <span key={k} className="prm">{k} {Array.isArray(v) ? v.join(' · ') : String(v)}</span>)}</>;
}

function list(text: string): string[] { return text.split(',').map((s) => s.trim()).filter(Boolean); }

/** Inline policy for the API: the preset plus this session's draft (lists carry the base entries). */
function inlinePolicy(name: string, base: PolicyDoc | null, draft: Draft): Record<string, unknown> {
  return {
    preset: name, treat: draft.treat,
    detect: {
      declared_fields: [...(base?.detect.declared_fields ?? []), ...draft.declared],
      custom_recognizers: [...(base?.detect.custom_recognizers ?? []), ...draft.recognizers],
    },
  };
}

function draftChanges(draft: Draft): number { return Object.keys(draft.treat).length + draft.declared.length + draft.recognizers.length; }

export function Policies(): JSX.Element {
  const { apiKey, tenant } = useSettings();
  const auth = useMemo(() => ({ apiKey, tenant }), [apiKey, tenant]);
  const navigate = useNavigate();
  const { name = 'gdpr-pseudonymise' } = useParams();
  const [presets, setPresets] = useState<PresetSummary[]>([]);
  const [entities, setEntities] = useState<EntityInfo[]>([]);
  const [doc, setDoc] = useState<PolicyDoc | null>(null);
  const [draft, setDraft] = useState<Draft>(EMPTY);
  const [filter, setFilter] = useState<Filter>('all');
  const [query, setQuery] = useState('');
  const [showAll, setShowAll] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fpe, setFpe] = useState<boolean | null>(null);

  useEffect(() => {
    api.policies.presets(auth).then(setPresets).catch((e: unknown) => setError(errorText(e)));
    api.entities(auth).then((r) => setEntities(r.items)).catch((e: unknown) => setError(errorText(e)));
    api.readyz().then((r) => setFpe(r.components.fpe?.ok ?? null)).catch(() => setFpe(null));
  }, [auth]);
  useEffect(() => {
    setDraft(EMPTY);
    api.policies.validate(auth, name).then(setDoc).catch((e: unknown) => setError(errorText(e)));
  }, [auth, name]);

  const preset = presets.find((p) => p.name === name);
  const byName = useMemo(() => new Map(entities.map((e) => [e.name, e])), [entities]);
  const inline = useMemo(() => inlinePolicy(name, doc, draft), [name, doc, draft]);
  const changes = draftChanges(draft);

  // --- treatment matrix -------------------------------------------------------------------------
  const rows = useMemo(() => {
    if (!doc) return [];
    const names = new Set<string>([...Object.keys(doc.treat), ...Object.keys(doc.synthesize.overrides), ...Object.keys(draft.treat)]);
    names.delete('default_sensitive');
    if (showAll) entities.forEach((e) => names.add(e.name));
    else entities.filter((e) => e.sensitivity === 'CRITICAL').forEach((e) => names.add(e.name));
    return [...names].sort((a, b) => a.localeCompare(b)).map((entity) => {
      const info = byName.get(entity);
      const obf = draft.treat[entity] ?? doc.treat[entity] ?? doc.treat.default_sensitive ?? { method: 'det_token', params: {} };
      const syn = doc.synthesize.overrides[entity]
        ?? (doc.synthesize.keep_non_sensitive && info?.sensitivity === 'LOW' ? { method: 'keep', params: {} } : doc.synthesize.default);
      return { entity, info, obf, syn, changed: entity in draft.treat, inherited: !(entity in doc.treat) && !(entity in draft.treat) };
    });
  }, [doc, draft, entities, byName, showAll]);
  const visible = rows.filter((r) => (filter === 'all' || (filter === 'critical' ? r.info?.sensitivity === 'CRITICAL' : r.changed))
    && (!query || `${r.entity} ${r.obf.method} ${r.info?.family ?? ''}`.toUpperCase().includes(query.toUpperCase())));

  function setTreat(entity: string, method: string): void {
    setDraft((d) => ({ ...d, treat: { ...d.treat, [entity]: { method, params: {} } } }));
  }

  // --- declared fields --------------------------------------------------------------------------
  const [decl, setDecl] = useState({ path: '', entity_type: 'BUSINESS_SENSITIVE', method: 'bucket' });
  function declare(): void {
    if (!decl.path.trim()) return;
    setDraft((d) => ({ ...d, declared: [...d.declared, { path: decl.path.trim(), entity_type: decl.entity_type }],
      treat: { ...d.treat, [decl.entity_type]: { method: decl.method, params: decl.method === 'bucket' ? { ranges: [0, 300, 600, 900] } : {} } } }));
    setDecl({ ...decl, path: '' });
  }

  // --- recognizer builder -------------------------------------------------------------------------
  const [rec, setRec] = useState({ name: 'employee-id', entity: 'EMPLOYEE_ID', regex: '\\bEMP-\\d{6}\\b', score: '0.85', context: 'staff id, employee, emp no', deny: '', allow: 'EMP-000000' });
  const [testText, setTestText] = useState(TEST_TEXT);
  const [server, setServer] = useState<RecognizerTestOut | null>(null);
  const spec: RecognizerSpec = {
    name: rec.name, supported_entity: rec.entity, patterns: [{ name: rec.name, regex: rec.regex, score: Number(rec.score) || 0 }],
    context: list(rec.context), deny_list: list(rec.deny), allow_list: list(rec.allow),
  };
  const local = useMemo(() => {
    try {
      if (!rec.regex) return { error: 'Enter a pattern to test it.', hits: [] as { start: number; end: number; allowed: boolean }[] };
      const rx = new RegExp(rec.regex, 'g');
      const allow = new Set(list(rec.allow));
      const hits: { start: number; end: number; allowed: boolean }[] = [];
      for (let m = rx.exec(testText); m !== null; m = rx.exec(testText)) {
        if (m[0] === '') { rx.lastIndex += 1; continue; }
        hits.push({ start: m.index, end: m.index + m[0].length, allowed: allow.has(m[0]) });
      }
      return { error: null, hits };
    } catch {
      return { error: 'This pattern does not compile, so it matches nothing. Check for an unclosed bracket or a stray backslash.', hits: [] };
    }
  }, [rec.regex, rec.allow, testText]);
  useEffect(() => { setServer(null); }, [rec, testText]);
  async function runServer(): Promise<void> {
    try { setServer(await api.policies.testRecognizer(auth, spec, testText)); setError(null); } catch (e) { setError(errorText(e)); }
  }

  // --- plan preview -------------------------------------------------------------------------------
  const [sample, setSample] = useState(SAMPLE);
  const [planMode, setPlanMode] = useState<'obfuscate' | 'synthesize'>('obfuscate');
  const [plan, setPlan] = useState<PlanOut | null>(null);
  async function runPlan(): Promise<void> {
    try { setPlan(await api.policies.plan(auth, { data: JSON.parse(sample) as unknown, policy: inline, mode: planMode })); setError(null); }
    catch (e) { setError(e instanceof SyntaxError ? `Sample is not valid JSON: ${e.message}` : errorText(e)); }
  }
  const planCounts = plan ? Object.entries(plan.plan.reduce<Record<string, number>>((acc, r) => { acc[r.method] = (acc[r.method] ?? 0) + 1; return acc; }, {})) : [];
  const fallbacks = plan ? plan.plan.filter((r) => r.reason.includes('fallback:')).length : 0;

  const yaml = useMemo(() => toYaml(inline), [inline]);
  function downloadYaml(): void {
    const url = URL.createObjectURL(new Blob([yaml], { type: 'application/yaml' }));
    const a = document.createElement('a'); a.href = url; a.download = `${name}${changes ? '-draft' : ''}.yaml`; a.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 5000);
  }

  const toneFor = (s: Sensitivity | undefined): Sensitivity => s ?? 'MEDIUM';
  return (
    <main className="page">
      <div className="page-head">
        <div><h1>Policies</h1><p>A policy decides what is detected and what happens to each entity type. Every request, job and evidence pack records the hash it ran with.</p></div>
        <div className="head-actions">
          <button className="btn" disabled={!changes} onClick={() => setDraft(EMPTY)}>Discard draft</button>
          <a className="btn" href="#plan">Preview plan</a>
          <button className="btn btn-primary" disabled title="Publishing and version history need the policy store (M5). Use the draft inline: Download YAML or Use in Playground.">Publish</button>
        </div>
      </div>
      {error && <div className="err" role="alert">{error}</div>}
      <div className="pol">
        <div className="pol-side">
          <div className="card">
            <div className="card-head"><div className="card-title">Presets</div><Tone tone="slate">{presets.length}</Tone></div>
            <div className="plist">
              {presets.map((p) => (
                <Link key={p.name} to={`/policies/${p.name}`} className={`preset${p.name === name ? ' on' : ''}`}>
                  <div className="p-top"><span className="p-name">{p.name}</span><span className="p-ver">v{p.version}</span></div>
                  <div className="p-hash">sha256:{p.hash.slice(0, 8)}…{p.hash.slice(-4)}</div>
                  <div className="p-meta"><Tone tone={p.mode === 'synthesize' ? 'violet' : 'teal'}>{p.mode}</Tone>{p.mode === 'synthesize' ? 'one-way' : p.reversible ? 'reversible' : 'irreversible'}</div>
                </Link>))}
            </div>
          </div>
          <div className="card toc-card"><div className="nav-label">On this page</div>
            <nav className="toc"><a href="#matrix">Treatment per entity <span>{rows.length}</span></a><a href="#declared">Declared fields <span>{(doc?.detect.declared_fields.length ?? 0) + draft.declared.length}</span></a>
              <a href="#recognizer">Custom recognizer <span>{(doc?.detect.custom_recognizers.length ?? 0) + draft.recognizers.length}</span></a><a href="#plan">Plan preview <span>{plan?.plan.length ?? 0}</span></a><a href="#versions">Versions <span>{changes ? 2 : 1}</span></a></nav></div>
        </div>
        <div className="pol-main">
          <section className="card" data-testid="policy-header">
            <div className="ph-name">{name} {preset && <Tone tone={preset.mode === 'synthesize' ? 'violet' : 'teal'}>{preset.mode} · {preset.mode === 'synthesize' ? 'one-way' : preset.reversible ? 'reversible' : 'irreversible'}</Tone>}
              <Tone tone="emerald">v{doc?.version ?? 1} shipped</Tone>{changes > 0 && <Tone tone="amber">draft · {changes} change{changes === 1 ? '' : 's'}</Tone>}</div>
            <div className="ph-desc">{doc?.description}</div>
            {preset && <div className="ph-hash">Preset hash <span className="mono">sha256:{preset.hash.slice(0, 16)}…{preset.hash.slice(-12)}</span>
              <button className="btn btn-sm" onClick={() => { void navigator.clipboard?.writeText(preset.hash); }}>Copy hash</button></div>}
            {doc && <div className="facts">
              <div><div className="l">Locale</div><div className="v">{doc.locale.default}<small>{doc.locale.by_country_field ? `by ${doc.locale.by_country_field}` : 'fixed'}</small></div></div>
              <div><div className="l">Detection tiers</div><div className="v">{doc.detect.tiers.join(', ')}<small>{doc.detect.tiers.includes('llm_label') ? 'LLM labelling on' : 'LLM labelling off'}</small></div></div>
              <div><div className="l">Minimum score</div><div className="v">{doc.detect.min_score.toFixed(2)}<small>noisy-OR per locator</small></div></div>
              <div><div className="l">Consistency</div><div className="v">{doc.consistency.scope}<small>{doc.consistency.scope === 'key' ? 'caller holds the key' : doc.consistency.field_scoped ? 'field scoped' : 'per request'}</small></div></div>
              <div><div className="l">Token style</div><div className="v">{doc.output.token_style}<small>{doc.output.token_style === 'natural' ? 'reveal uses hints' : 'self-describing'}</small></div></div>
              <div><div className="l">Leakage check</div><div className="v">{doc.verify.leakage_check}<small>{doc.verify.leakage_check === 'strict' ? 'fails closed' : 'warns'}</small></div></div>
            </div>}
            <div className="callout" style={{ marginTop: 12 }}>{preset?.mode === 'synthesize'
              ? <>Synthetic output carries a <b>measured</b> privacy report rather than a claim of anonymity. Generation is one-way.</>
              : <>Obfuscated output is <b>pseudonymised and still personal data</b> (GDPR Art. 4(5), India's DPDP Act). Only the holder of the key can reverse it; DPS keeps no copy.</>}</div>
          </section>
          <section className="card" id="matrix" data-testid="matrix">
            <div className="card-head"><div className="card-title">Treatment per entity type {changes > 0 && <span className="faint">· draft</span>}</div>
              <div className="tools">
                <div className="seg sm" role="group" aria-label="Filter">{(['all', 'critical', 'draft'] as Filter[]).map((f) => <button key={f} className={filter === f ? 'on' : ''} aria-pressed={filter === f} onClick={() => setFilter(f)}>{f === 'draft' ? 'Changed in draft' : f === 'all' ? 'All' : 'Critical'}</button>)}</div>
                <input className="input" placeholder="Search entity or method" value={query} onChange={(e) => setQuery(e.target.value)} aria-label="Search entity types" />
              </div></div>
            <div className="tbl-scroll"><table className="tbl stack">
              <thead><tr><th>Entity type</th><th>Family</th><th>Sensitivity</th><th>Obfuscate</th><th>Synthesize</th><th>Reversible</th></tr></thead>
              <tbody>{visible.map((r) => (
                <tr key={r.entity} className={r.changed ? 'drafted' : ''}>
                  <td className="ent" data-l="Entity type"><EntityChip type={r.entity} sensitivity={toneFor(r.info?.sensitivity)} />{r.changed && <span className="chip amber" style={{ marginLeft: 6 }}>draft</span>}</td>
                  <td className="fam" data-l="Family">{r.info?.family.replace('_', ' ') ?? 'custom'}{r.info?.jurisdictions.length ? ` · ${r.info.jurisdictions.join(', ')}` : ''}</td>
                  <td data-l="Sensitivity"><EntityChip type={toneFor(r.info?.sensitivity)} sensitivity={toneFor(r.info?.sensitivity)} /></td>
                  <td data-l="Obfuscate"><select className="input m-select" value={r.obf.method} aria-label={`Obfuscate method for ${r.entity}`} onChange={(e) => setTreat(r.entity, e.target.value)}>
                    {METHODS.map((m) => <option key={m} value={m}>{m}</option>)}</select><Params spec={r.obf} />{r.inherited && <span className="syn-note">default_sensitive</span>}</td>
                  <td data-l="Synthesize"><span className="m">{r.syn.method}</span><Params spec={r.syn} /></td>
                  <td data-l="Reversible" className={`revcell${REVERSIBLE.has(r.obf.method) ? '' : ' no'}`}>{REVERSIBLE.has(r.obf.method) ? 'Yes' : 'No'}</td>
                </tr>))}
                {visible.length === 0 && <tr><td colSpan={6} className="muted">No entity type matches. Clear the search to see all {rows.length}.</td></tr>}
              </tbody></table></div>
            <div className="tbl-foot"><span>Showing {visible.length} of {showAll ? entities.length : rows.length} entity types. Others are kept, or tokenised by <span className="mono">default_sensitive → {doc?.treat.default_sensitive?.method ?? 'det_token'}</span>.</span>
              <button className="btn btn-sm" onClick={() => setShowAll((v) => !v)}>{showAll ? 'Show policy types only' : `Show all ${entities.length}`}</button></div>
            <div className="faint" style={{ fontSize: 12, marginTop: 8 }}><span className="mono">fpe_*</span> methods need at least 10⁶ free values; smaller ones run as <span className="mono">det_token</span> and the plan records <span className="mono">fallback:fpe_domain_too_small</span>. FF1 on this deployment: <b>{fpe === null ? 'unknown' : fpe ? 'on' : 'off'}</b>.</div>
          </section>
        </div>
        <div className="pol-rest">
          <section className="card" id="declared">
            <div className="card-head"><div className="card-title">Declared fields <span className="faint">· more than PII</span></div></div>
            <p className="card-sub">Owners name the paths and columns detectors cannot know about. Declarations apply before detection and win over inferred types; the plan records <span className="mono">declared:&lt;path&gt;</span>.</p>
            <div className="tbl-scroll"><table className="tbl stack"><thead><tr><th>Path or column</th><th>Declared as</th><th>Treatment</th><th>Source</th></tr></thead>
              <tbody>{[...(doc?.detect.declared_fields ?? []).map((d) => ({ d, draft: false })), ...draft.declared.map((d) => ({ d, draft: true }))].map(({ d, draft: isDraft }, i) => (
                <tr key={`${d.path}-${i}`} className={isDraft ? 'drafted' : ''}><td className="mono" data-l="Path">{d.path}</td>
                  <td data-l="Declared as"><EntityChip type={d.entity_type} sensitivity={toneFor(byName.get(d.entity_type)?.sensitivity)} /></td>
                  <td data-l="Treatment"><span className="m">{(draft.treat[d.entity_type] ?? doc?.treat[d.entity_type] ?? doc?.treat.default_sensitive)?.method}</span><Params spec={draft.treat[d.entity_type] ?? doc?.treat[d.entity_type]} /></td>
                  <td data-l="Source">{isDraft ? <Tone tone="amber">draft</Tone> : 'preset'}</td></tr>))}
                <tr><td data-l="Path"><input className="input mono" placeholder="$.loan.internal_score or OWNER.TABLE.COLUMN" value={decl.path} onChange={(e) => setDecl({ ...decl, path: e.target.value })} aria-label="Declared path" /></td>
                  <td data-l="Declared as"><input className="input mono" list="entity-names" value={decl.entity_type} onChange={(e) => setDecl({ ...decl, entity_type: e.target.value.toUpperCase() })} aria-label="Declared entity type" /></td>
                  <td data-l="Treatment"><select className="input" value={decl.method} onChange={(e) => setDecl({ ...decl, method: e.target.value })} aria-label="Declared treatment">{METHODS.map((m) => <option key={m}>{m}</option>)}</select></td>
                  <td data-l=""><button className="btn btn-sm" onClick={declare}>Declare a field</button></td></tr>
              </tbody></table></div>
            <datalist id="entity-names">{entities.map((e) => <option key={e.name} value={e.name} />)}</datalist>
          </section>
          <section className="card" id="recognizer" data-testid="recognizer">
            <div className="card-head"><div className="card-title">Custom recognizer <span className="faint">· {rec.name}</span></div>{draft.recognizers.some((r) => r.name === rec.name) && <Tone tone="amber">in draft</Tone>}</div>
            <div className="rb">
              <div>
                <div className="row2"><div className="rb-field"><label htmlFor="r-name">Name</label><input id="r-name" className="input mono" value={rec.name} onChange={(e) => setRec({ ...rec, name: e.target.value })} /></div>
                  <div className="rb-field"><label htmlFor="r-score">Score</label><input id="r-score" className="input mono" value={rec.score} onChange={(e) => setRec({ ...rec, score: e.target.value })} /></div></div>
                <div className="rb-field"><label htmlFor="r-ent">Entity type</label><input id="r-ent" className="input mono" list="entity-names" value={rec.entity} onChange={(e) => setRec({ ...rec, entity: e.target.value.toUpperCase() })} /></div>
                <div className="rb-field"><label htmlFor="r-rx">Pattern (regular expression)</label><input id="r-rx" className="input mono" value={rec.regex} onChange={(e) => setRec({ ...rec, regex: e.target.value })} />
                  <div className="hint">Python <span className="mono">re</span> syntax, as in Presidio recognizers. The live preview uses the browser's engine; <b>Run on the engine</b> is authoritative.</div>
                  {local.error && <div className="callout amber" style={{ marginTop: 8 }}>{local.error}</div>}</div>
                <div className="rb-field"><label htmlFor="r-ctx">Context words</label><input id="r-ctx" className="input" value={rec.context} onChange={(e) => setRec({ ...rec, context: e.target.value })} />
                  <div className="hint">A context word just before a match raises its score.</div></div>
                <div className="rb-field"><label htmlFor="r-allow">Allow list · never match</label><input id="r-allow" className="input mono" value={rec.allow} onChange={(e) => setRec({ ...rec, allow: e.target.value })} />
                  <div className="hint">Exact values that are never reported, such as seeded test fixtures (DPS extension).</div></div>
                <div className="rb-field"><label htmlFor="r-deny">Deny list · always flag</label><input id="r-deny" className="input mono" value={rec.deny} onChange={(e) => setRec({ ...rec, deny: e.target.value })} />
                  <div className="hint">Presidio semantics: exact words that are always reported as this entity.</div></div>
                <div className="row">
                  <button className="btn btn-primary btn-sm" onClick={() => setDraft((d) => ({ ...d, recognizers: [...d.recognizers.filter((r) => r.name !== spec.name), spec] }))}>Add recognizer to draft</button>
                  <button className="btn btn-sm" onClick={() => { void runServer(); }}>Run on the engine</button>
                  <button className="btn btn-sm" disabled={!server} onClick={() => { if (server) void navigator.clipboard?.writeText(server.yaml); }}>Copy as Presidio YAML</button>
                </div>
              </div>
              <div>
                <div className="rb-field"><label htmlFor="r-test">Test text <span className="faint">· not stored</span></label><textarea id="r-test" className="input" rows={4} value={testText} onChange={(e) => setTestText(e.target.value)} /></div>
                <div className="test-head"><span>Live preview</span><span className="faint">{local.hits.filter((h) => !h.allowed).length} match{local.hits.length === 1 ? '' : 'es'}</span></div>
                <div className="test-out">{(() => {
                  const parts: JSX.Element[] = []; let cursor = 0;
                  local.hits.forEach((h, i) => { parts.push(<span key={`s${i}`}>{testText.slice(cursor, h.start)}</span>); parts.push(<mark key={`m${i}`} className={h.allowed ? 'deny' : 'hit'}>{testText.slice(h.start, h.end)}</mark>); cursor = h.end; });
                  parts.push(<span key="tail">{testText.slice(cursor)}</span>); return parts;
                })()}</div>
                {server && <><div className="test-head"><span>Engine result</span><span className="faint">{server.count} match{server.count === 1 ? '' : 'es'}</span></div>
                  <div className="matches">{server.matches.map((m) => (
                    <div key={`${m.start}-${m.end}`} className="match"><span className="mono">{testText.slice(m.start, m.end)}</span><span className="chip amber">{m.entity_type} · {m.score.toFixed(2)}</span>
                      <span className="why">{m.evidence}{m.context ? ' + context word' : ''} · offsets {m.start}–{m.end}</span></div>))}</div>
                  <div className="test-head"><span>Presidio recognizer YAML</span></div><pre className="pane">{server.yaml}</pre></>}
              </div>
            </div>
          </section>
          <section className="card" id="plan" data-testid="plan">
            <div className="card-head"><div className="card-title">Plan preview <span className="faint">· {changes ? 'draft' : name} on a pasted sample</span></div>
              <div className="tools"><div className="seg sm" role="group" aria-label="Plan mode">{(['obfuscate', 'synthesize'] as const).map((m) => <button key={m} className={planMode === m ? 'on' : ''} aria-pressed={planMode === m} onClick={() => setPlanMode(m)}>{m}</button>)}</div>
                <button className="btn btn-sm btn-primary" onClick={() => { void runPlan(); }}>Re-run plan</button></div></div>
            <p className="card-sub">The resolved method per finding, with the reason. Nothing is transformed and the sample is not stored. <span className="mono">POST /api/v1/policies/plan</span></p>
            <div className="plan">
              <textarea className="input" rows={18} value={sample} onChange={(e) => setSample(e.target.value)} aria-label="Plan sample" spellCheck={false} />
              <div>
                {plan && <div className="plan-sum">{planCounts.map(([m, n]) => <span key={m} className="chip slate">{m} ×{n}</span>)}{fallbacks > 0 && <span className="chip amber">{fallbacks} fallback{fallbacks === 1 ? '' : 's'}</span>}</div>}
                <div className="tbl-scroll"><table className="tbl stack"><thead><tr><th>Locator</th><th>Entity · score</th><th>Method</th><th>Reason</th><th>Reversible</th></tr></thead>
                  <tbody>{plan ? plan.plan.map((r, i) => (
                    <tr key={i}><td className="loc" data-l="Locator">{r.path}{r.span ? `[${r.span[0]}:${r.span[1]}]` : ''}</td>
                      <td data-l="Entity"><EntityChip type={r.entity_type} sensitivity={toneFor(byName.get(r.entity_type)?.sensitivity)} /><span className="score">{r.score.toFixed(2)}</span></td>
                      <td data-l="Method"><span className="m">{r.method}</span><Params spec={{ method: r.method, params: r.params }} /></td>
                      <td className="rsn" data-l="Reason">{r.reason.split('|').map((p) => p.startsWith('fallback:') ? <span key={p} className="fbk">{p}</span> : <span key={p}>{p} </span>)}</td>
                      <td data-l="Reversible" className={`revcell${r.reversible ? '' : ' no'}`}>{r.reversible ? 'Yes' : 'No'}</td></tr>))
                    : <tr><td colSpan={5} className="muted">Run the plan to see one row per finding.</td></tr>}</tbody></table></div>
              </div>
            </div>
            {fallbacks > 0 && <div className="fb-legend">
              <div><b className="mono">fallback:fpe_domain_too_small</b><p>FF1 needs at least 10⁶ free values; such values run as <span className="mono">det_token</span>, still reversible.</p></div>
              <div><b className="mono">fallback:fpe_disabled</b><p>Every <span className="mono">fpe_*</span> method runs as <span className="mono">det_token</span> while <span className="mono">DPS_FPE_ENABLED=false</span>.</p></div></div>}
          </section>
          <section className="card" id="versions">
            <div className="card-head"><div className="card-title">Version history</div><span className="faint">Published versions and pinning arrive with the policy store (M5).</span></div>
            <div className="ver">
              <div className="vlist">
                {changes > 0 && <button aria-pressed="true"><div className="v-top"><span className="v-n">draft</span><Tone tone="amber">{changes} change{changes === 1 ? '' : 's'}</Tone></div><div className="v-by">this browser session · not stored</div><div className="v-hash">hash on publish</div></button>}
                <button aria-pressed={changes === 0}><div className="v-top"><span className="v-n">v{doc?.version ?? 1}</span><Tone tone="emerald">shipped preset</Tone></div><div className="v-by">DPS presets</div><div className="v-hash">{preset ? `sha256:${preset.hash.slice(0, 8)}…${preset.hash.slice(-4)}` : ''}</div></button>
              </div>
              <div>
                <div className="diff-head"><div><div className="t">{changes ? 'Draft changes (inline policy)' : 'Preset (inline policy form)'}</div><div className="s">Use it on any request as <span className="mono">"policy": {'{…}'}</span></div></div>
                  <div className="row"><button className="btn btn-sm" onClick={downloadYaml}>Download YAML</button><button className="btn btn-sm btn-primary" onClick={() => navigate('/playground', { state: { policy: inline } })}>Use in Playground</button></div></div>
                <pre className="diff-pane pane" data-testid="draft-yaml">{yaml}</pre>
              </div>
            </div>
          </section>
        </div>
      </div>
    </main>
  );
}
```

`frontend/src/App.tsx`: import `Policies`; replace the `/policies` placeholder route with `<Route path="/policies" element={<Policies />} />` and add `<Route path="/policies/:name" element={<Policies />} />`.

`frontend/src/styles.css` — append:
```css
/* --- M1 · policies (mockup 05) --- */
.pol { display: grid; grid-template-columns: 240px minmax(0, 1fr); gap: 16px; }
.pol-side { display: flex; flex-direction: column; gap: 16px; position: sticky; top: calc(var(--topbar-h) + 16px); align-self: start; }
.pol-main { display: flex; flex-direction: column; gap: 16px; min-width: 0; }
.pol-rest { grid-column: 1 / -1; display: flex; flex-direction: column; gap: 16px; min-width: 0; }
.card-sub { font-size: 12.5px; color: var(--ink-2); margin: -6px 0 14px; }
.preset { display: block; padding: 9px 10px; border-radius: var(--r-md); border: 1px solid transparent; }
.preset + .preset { margin-top: 3px; }
.preset:hover { background: var(--surface-2); }
.preset.on { background: var(--teal-soft); border-color: var(--teal-border); }
.preset .p-top { display: flex; justify-content: space-between; align-items: center; gap: 8px; }
.preset .p-name { font-family: var(--mono); font-weight: 600; font-size: 12.5px; color: var(--ink); }
.preset.on .p-name { color: var(--teal); }
.preset .p-ver { font-size: 11.5px; color: var(--ink-2); font-weight: 700; }
.preset .p-hash { font-family: var(--mono); font-size: 11px; color: var(--ink-3); margin-top: 2px; }
.preset .p-meta { display: flex; gap: 6px; align-items: center; margin-top: 6px; font-size: 11.5px; color: var(--ink-3); flex-wrap: wrap; }
.toc a { display: flex; justify-content: space-between; padding: 6px 10px; border-radius: 6px; color: var(--ink-2); font-size: 12.5px; }
.toc a:hover { background: var(--surface-2); color: var(--ink); }
.toc a span { color: var(--ink-3); }
.ph-name { font-family: var(--mono); font-size: 18px; font-weight: 700; display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.ph-name .chip { font-family: var(--font); }
.ph-desc { color: var(--ink-2); margin-top: 4px; font-size: 13px; }
.ph-hash { display: flex; align-items: center; gap: 8px; font-size: 12px; color: var(--ink-3); margin-top: 8px; flex-wrap: wrap; }
.facts { display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); gap: 1px; background: var(--border); border: 1px solid var(--border); border-radius: var(--r-md); overflow: hidden; margin-top: 14px; }
.facts > div { background: var(--surface); padding: 9px 12px; min-width: 0; }
.facts .l { font-size: 10.5px; text-transform: uppercase; letter-spacing: .08em; color: var(--ink-3); font-weight: 700; }
.facts .v { font-size: 12.5px; margin-top: 3px; }
.facts .v small { display: block; color: var(--ink-3); font-size: 11.5px; }
.tbl-scroll { overflow-x: auto; }
.tbl-scroll > table.tbl { min-width: 760px; }
.m { font-family: var(--mono); font-size: 12px; color: var(--ink); white-space: nowrap; }
.m-select { width: auto; padding: 4px 8px; font-family: var(--mono); font-size: 12px; }
.prm { font-family: var(--mono); font-size: 11px; color: var(--ink-2); background: var(--surface-2); border: 1px solid var(--border); border-radius: 5px; padding: 0 5px; margin: 2px 0 2px 4px; white-space: nowrap; display: inline-block; line-height: 17px; }
.syn-note { display: block; font-size: 11.5px; color: var(--ink-3); margin-top: 2px; }
.revcell { font-size: 12.5px; white-space: nowrap; } .revcell.no { color: var(--ink-3); }
.drafted td:first-child { box-shadow: inset 3px 0 0 var(--amber); }
.fam { color: var(--ink-2); font-size: 12.5px; white-space: nowrap; }
.tbl-foot { display: flex; justify-content: space-between; align-items: center; gap: 12px; margin-top: 12px; font-size: 12px; color: var(--ink-3); flex-wrap: wrap; }
.tools { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.tools .input { width: 200px; padding: 6px 10px; }
.rb { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1.15fr); gap: 18px; }
.rb-field { margin-bottom: 12px; }
.rb-field label { display: block; font-size: 12px; font-weight: 600; color: var(--ink-2); margin-bottom: 5px; }
.rb-field .hint { font-size: 11.5px; color: var(--ink-3); margin-top: 4px; }
.row2 { display: grid; grid-template-columns: minmax(0, 1fr) 92px; gap: 10px; }
.input.mono { font-family: var(--mono); font-size: 12.5px; }
.test-out { background: var(--bg-tint); border: 1px solid var(--border); border-radius: var(--r-md); padding: 10px 12px; font-family: var(--mono); font-size: 12.5px; line-height: 1.7; white-space: pre-wrap; word-break: break-word; min-height: 64px; }
mark.hit { background: var(--amber-soft); box-shadow: inset 0 -2px 0 var(--amber); color: inherit; border-radius: 3px; padding: 0 2px; }
mark.deny { background: var(--surface-3); color: var(--ink-3); text-decoration: line-through; border-radius: 3px; padding: 0 2px; }
.matches { margin-top: 10px; }
.match { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 4px 10px; padding: 7px 0; border-bottom: 1px solid var(--border); font-size: 12.5px; align-items: center; }
.match:last-child { border-bottom: 0; }
.match .why { grid-column: 1 / -1; color: var(--ink-3); font-size: 11.5px; }
.test-head { display: flex; justify-content: space-between; align-items: center; gap: 10px; margin: 12px 0 6px; font-size: 12px; font-weight: 600; color: var(--ink-2); flex-wrap: wrap; }
.plan { display: grid; grid-template-columns: 336px minmax(0, 1fr); gap: 16px; align-items: start; }
.plan textarea.input { min-height: 360px; font-size: 11.5px; }
.plan-sum { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 10px; }
.loc { font-family: var(--mono); font-size: 11.5px; color: var(--ink-2); white-space: nowrap; }
.score { font-family: var(--mono); font-size: 11.5px; color: var(--ink-3); display: block; margin-top: 3px; }
.rsn { font-family: var(--mono); font-size: 11.5px; color: var(--ink-2); overflow-wrap: break-word; }
.fbk { font-family: var(--mono); font-size: 11px; color: var(--amber); background: var(--amber-soft); border: 1px solid var(--amber-border); border-radius: 5px; padding: 0 5px; display: inline-block; margin-top: 3px; line-height: 17px; white-space: nowrap; }
.fb-legend { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; margin-top: 14px; }
.fb-legend > div { border: 1px solid var(--border); border-radius: var(--r-md); padding: 10px 12px; font-size: 12.5px; background: var(--surface-2); }
.fb-legend p { margin: 4px 0 0; color: var(--ink-2); }
.ver { display: grid; grid-template-columns: 290px minmax(0, 1fr); gap: 16px; align-items: start; }
.vlist button { display: block; width: 100%; text-align: left; background: transparent; border: 1px solid transparent; border-radius: var(--r-md); padding: 9px 10px; color: var(--ink); cursor: pointer; font: inherit; }
.vlist button + button { margin-top: 3px; }
.vlist button[aria-pressed="true"] { background: var(--surface-2); border-color: var(--border-strong); }
.vlist .v-top { display: flex; align-items: center; gap: 8px; justify-content: space-between; }
.vlist .v-n { font-weight: 700; font-size: 13px; }
.vlist .v-by { font-size: 12px; color: var(--ink-2); margin-top: 2px; }
.vlist .v-hash { font-family: var(--mono); font-size: 11px; color: var(--ink-3); margin-top: 2px; }
.diff-head { display: flex; justify-content: space-between; gap: 10px; align-items: flex-start; margin-bottom: 10px; flex-wrap: wrap; }
.diff-head .t { font-weight: 700; font-size: 13px; } .diff-head .s { font-size: 12px; color: var(--ink-3); }
.diff-pane { max-height: 360px; white-space: pre; }
@media (max-width: 1100px) { .pol, .rb, .plan, .ver { grid-template-columns: minmax(0, 1fr); } .pol-side { position: static; } .facts { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (max-width: 640px) { .facts { grid-template-columns: 1fr; } .tbl-scroll > table.tbl { min-width: 0; } }
```

- [ ] **Step 1: Implement** the page, routes and styles.
- [ ] **Step 2: Gates** — `cd frontend && npm run typecheck && npm run build` → clean (verified while writing). Python gates green.
- [ ] **Step 3: Manual check** — `/policies` lists six presets; `/policies/pci-dss` loads; editing CVV's obfuscate method marks the row "draft" and the draft badge counts it; the recognizer test with the default fields finds `EMP-204417` only (live and on the engine); Re-run plan fills the table; Download YAML saves `<preset>-draft.yaml`; Use in Playground opens `/playground` with "draft from Policies" selected.
- [ ] **Step 4: Commit**
```bash
git add frontend/src/pages/Policies.tsx frontend/src/App.tsx frontend/src/styles.css
git commit -m "feat(console): Policies page — presets, treatment matrix with session draft, declared fields, recognizer test, plan preview" -- frontend/src/pages/Policies.tsx frontend/src/App.tsx frontend/src/styles.css
```

---
### Task 19: Local stack (OCR mock), image, smoke test extension

**Files:**
- Modify: `docker-compose.yml`, `Dockerfile`, `.dockerignore`, `tools/smoke_test.py`

**`docker-compose.yml`** (contract §11: host ports on 127.0.0.1, healthchecks, `mem_limit`, profiles; the mock is the cached image, buildable from `../mock_azure_ocr` because DPS is nested in that directory):
```yaml
# Local stack. Default: the app (Layer 1). Profile `docs` (or `full`) adds the Azure Computer Vision
# Read v3.2 mock (Tesseract inside) that the documents routes use for scanned pages and images.
#
#   docker compose --profile docs up --build -d      # console + API on http://127.0.0.1:8400
#   python tools/smoke_test.py                       # end-to-end checks (OCR checks need --profile docs)
services:
  app:
    build: .
    image: data-privacy-services:local
    ports:
      - "127.0.0.1:${DPS_APP_PORT:-8400}:8400"
    environment:
      DPS_ENV: local
      DPS_LOG_LEVEL: INFO
      DPS_AUTH_ENABLED: "true"
      DPS_BOOTSTRAP_API_KEY: ${DPS_BOOTSTRAP_API_KEY:-dps_local_dev_key_change_me}
      DPS_DEFAULT_POLICY: gdpr-pseudonymise
      DPS_DEFAULT_LOCALE: ${DPS_DEFAULT_LOCALE:-en_IN}
      DPS_FPE_ENABLED: "true"
      DPS_LLM_PROVIDER: ${DPS_LLM_PROVIDER:-stub}
      DPS_LLM_MODEL: ${DPS_LLM_MODEL:-gemini-2.5-flash}
      DPS_LLM_TEXT_REWRITE: ${DPS_LLM_TEXT_REWRITE:-false}
      DPS_STELLAR_BASE_URL: ${DPS_STELLAR_BASE_URL:-}
      DPS_COIN_URL: ${DPS_COIN_URL:-}
      DPS_COIN_CLIENT_ID: ${DPS_COIN_CLIENT_ID:-}
      DPS_COIN_CLIENT_SECRET: ${DPS_COIN_CLIENT_SECRET:-}
      DPS_COIN_SCOPE: ${DPS_COIN_SCOPE:-}
      DPS_SSL_CERT_FILE: ${DPS_SSL_CERT_FILE:-}
      # Documents (M1). The key is a local demo literal; staging/prod read DPS_AZURE_VISION_KEY_FILE.
      DPS_OCR_PROVIDER: ${DPS_OCR_PROVIDER:-azure}
      DPS_AZURE_VISION_ENDPOINT: ${DPS_AZURE_VISION_ENDPOINT:-http://azure-ocr-mock:5000}
      DPS_AZURE_VISION_KEY: ${DPS_AZURE_VISION_KEY:-mock-key}
      DPS_OCR_HEALTH_PATH: ${DPS_OCR_HEALTH_PATH:-/health}
      DPS_MAX_DOCUMENT_MB: ${DPS_MAX_DOCUMENT_MB:-20}
      DPS_SCHEMA_MAX_ROWS: ${DPS_SCHEMA_MAX_ROWS:-10000}
    depends_on:
      azure-ocr-mock:
        condition: service_healthy
        required: false            # without --profile docs the app starts and reports ocr "down"
    tmpfs:
      - /tmp:size=256m,mode=1777   # multipart uploads above 1 MiB spool here — memory, never an image layer
    mem_limit: 1536m
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8400/health', timeout=3).status==200 else 1)"]
      interval: 15s
      timeout: 5s
      retries: 5
    labels:
      app: data-privacy-services
      component: api
      env: local

  azure-ocr-mock:
    build:
      context: ../mock_azure_ocr
    image: document_intelligence-azure-ocr-mock:latest
    profiles: ["docs", "full"]
    ports:
      - "127.0.0.1:5016:5000"
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:5000/health', timeout=3).status==200 else 1)"]
      interval: 10s
      timeout: 5s
      retries: 12
      start_period: 10s
    mem_limit: 768m
    labels:
      app: data-privacy-services
      component: ocr-mock
      env: local
```

**`Dockerfile`:** the console stage becomes `FROM node:22-slim AS frontend` (the cached image, contract §11.1); the runtime installs `.[fpe,docs]` (and `.[fpe,docs,ner]` under `WITH_NER=1`). No apt packages are needed: pypdfium2's manylinux wheel bundles PDFium; pdfplumber, pypdf, reportlab, python-docx, lxml and Pillow ship wheels. **`.dockerignore`** gains `demo` and `tests/eval` (the console's demo scan reaches the image through `frontend/public`).

**`tools/smoke_test.py`** — imports gain `base64`, `io`, `json`, `from pathlib import Path`, `from typing import Any`; add the helpers and checks below and call `m1_checks(c)` just before the existing "console served" check. Every line prints a verdict only — never a body.
```python
DEMO = Path(__file__).resolve().parents[1] / "demo" / "documents"
REQUIRE_OCR = os.environ.get("DPS_SMOKE_REQUIRE_OCR") == "1"


def skip(name: str, why: str) -> None:
    """A check that cannot run here; a failure under DPS_SMOKE_REQUIRE_OCR=1 (milestone gate)."""
    if REQUIRE_OCR:
        check(name, False, why)
    print(f"SKIP  {name} — {why}")


def upload(c: httpx.Client, name: str, mode: str, request: dict[str, Any] | None = None) -> httpx.Response:
    data = {"request": json.dumps(request)} if request is not None else {}
    return c.post(f"/api/v1/documents/{mode}", headers=H, files={"file": (name, (DEMO / name).read_bytes())},
                  data=data, timeout=180.0)


def text_layer_chars(pdf_b64: str) -> int | None:
    """Characters pypdf extracts from a produced PDF (None when pypdf is not installed here)."""
    try:
        from pypdf import PdfReader
    except ImportError:
        return None
    reader = PdfReader(io.BytesIO(base64.b64decode(pdf_b64)))
    return sum(len((page.extract_text() or "").strip()) for page in reader.pages)


def m1_checks(c: httpx.Client) -> None:
    entities = c.get("/api/v1/entities", headers=H)
    check("entities", entities.status_code == 200 and len(entities.json()["items"]) >= 46)
    det = upload(c, "kyc-text.pdf", "detect")
    body = det.json() if det.status_code == 200 else {}
    boxes = sum(len(f["boxes"]) for p in body.get("pages", []) for f in p["findings"])
    check("documents detect (text PDF)", det.status_code == 200 and boxes > 0,
          f"{body.get('report', {}).get('findings_total')} findings, {boxes} boxes")
    red = upload(c, "kyc-text.pdf", "obfuscate", {"options": {"redacted_pdf": True}})
    rbody = red.json() if red.status_code == 200 else {}
    redaction = rbody.get("document", {}).get("redaction") or {}
    chars = text_layer_chars(rbody["redacted_pdf_b64"]) if rbody.get("redacted_pdf_b64") else -1
    check("documents redacted PDF has no text", red.status_code == 200 and redaction.get("passed") is True
          and chars in (0, None), f"{redaction.get('boxes')} boxes, ocr_checked={redaction.get('ocr_checked')}")
    original = (DEMO / "kyc.txt").read_text(encoding="utf-8")
    obf = upload(c, "kyc.txt", "obfuscate").json()
    rev = c.post("/api/v1/reveal", headers=H, json={"text": obf["result"], "key_material": obf["key_material"],
                                                    "reveal_hints": obf["reveal_hints"]})
    check("documents text reveal round trip", rev.status_code == 200 and rev.json()["result"] == original
          and obf["result"] != original)
    source_csv = (DEMO / "customers.csv").read_text(encoding="utf-8")
    syn = upload(c, "customers.csv", "synthesize", {"policy": "synthetic-realistic"}).json()
    first_email = source_csv.splitlines()[1].split(",")[2]
    check("documents CSV synthesize", syn.get("result_csv", "").splitlines()[:1] == source_csv.splitlines()[:1]
          and first_email not in syn.get("result_csv", ""))
    ocr = c.get("/readyz").json()["components"].get("ocr", {})
    if ocr.get("ok"):
        scan = upload(c, "kyc-scanned.pdf", "obfuscate", {"options": {"redacted_pdf": True}})
        sbody = scan.json() if scan.status_code == 200 else {}
        sred = sbody.get("document", {}).get("redaction") or {}
        types = {f["entity_type"] for p in sbody.get("pages", []) for f in p["findings"]}
        check("scanned PDF via OCR, redacted and re-read", scan.status_code == 200
              and sbody["document"]["ocr_pages"] == [1, 2] and sred.get("ocr_checked") is True
              and sred.get("ocr_hits") == 0 and {"AADHAAR", "EMAIL"} <= types, ", ".join(sorted(types)))
        image = upload(c, "id-card.png", "detect")
        check("image via OCR", image.status_code == 200 and len(image.json()["findings"]) >= 3)
    else:
        skip("scanned PDF via OCR, redacted and re-read", f"ocr {ocr.get('detail', 'absent')}: docker compose --profile docs up -d")
        skip("image via OCR", "ocr not ready")
    ddl = ("CREATE TABLE customers (customer_id INTEGER PRIMARY KEY, full_name VARCHAR(80), email VARCHAR(120), "
           "balance NUMERIC(12,2))")
    spec = c.post("/api/v1/schema/generator-spec", headers=H, json={"ddl": ddl})
    check("schema generator-spec from DDL", spec.status_code == 200 and len(spec.json()["spec"]["fields"]) == 4)
    first = c.post("/api/v1/schema/synthesize", headers=H, json={"spec": spec.json()["spec"], "rows": 25}).json()
    again = c.post("/api/v1/schema/synthesize", headers=H,
                   json={"spec": spec.json()["spec"], "rows": 25, "seed": first["synthesis_seed"]}).json()
    check("schema synthesize is reproducible", len(first["rows"]) == 25 and first["rows"] == again["rows"])
    check("demo scan served to the console", c.get("/demo/kyc-scanned.pdf").status_code == 200)
```

- [ ] **Step 1: Implement** the compose, Dockerfile, `.dockerignore` and smoke-test changes.
- [ ] **Step 2: Build and run the stack**
```bash
docker compose --profile docs up --build -d
for i in $(seq 1 60); do curl -fsS http://127.0.0.1:8400/health >/dev/null && curl -fsS http://127.0.0.1:5016/health >/dev/null && break; sleep 2; done
DPS_SMOKE_REQUIRE_OCR=1 python tools/smoke_test.py
```
Expected: the image builds (console compiled in the node:22 stage, `[fpe,docs]` installed); `/readyz` shows `ocr` ok/up; `all 23 checks passed` (M0's 13 + 10 M1 checks, no SKIP; if M0's smoke grew during its build, the total is M0's count + 10). If the mock's build has no network for `ghcr.io/astral-sh/uv`, use the cached image: `docker compose --profile docs build app && docker compose --profile docs up -d` (no `--build`).
- [ ] **Step 3: Run the M1 integration gate against the stack** — `DPS_TEST_REQUIRE_SERVICES=1 pytest -q -m "docs or ocr"` → all pass, none skipped (contract §12.4).
- [ ] **Step 4: Commit** (leave the stack up for Task 20)
```bash
git add docker-compose.yml Dockerfile .dockerignore tools/smoke_test.py
git commit -m "feat: compose OCR mock (profile docs), [docs] in the image, smoke test covers documents, OCR and schema" -- docker-compose.yml Dockerfile .dockerignore tools/smoke_test.py
```

---
### Task 19A: NDJSON streaming of large arrays on the Layer 1 routes (spec §6)

**Files:**
- Create: `dps/layers/api/ndjson.py`, `tests/test_ndjson.py`
- Modify: `dps/layers/api/routers/{detect,transform}.py` (M0), `dps/layers/api/common.py` (stream audit), `dps/app.py` (per-content-type body limit), `dps/config.py` + `.env.example` (`ndjson_max_items`, `ndjson_chunk_items`, `max_ndjson_mb`, contract §10), `docs/technical/api-m1.md` (Task 20 documents it)

Spec §6: "Arrays stream as NDJSON when large." The JSON routes stay exactly as M0 built them; NDJSON is opt-in per request (contract §9):
- **Response streaming** — a JSON request whose `data` is an array, sent with `Accept: application/x-ndjson`, gets a `StreamingResponse(media_type="application/x-ndjson")`.
- **Request streaming** — a body with `Content-Type: application/x-ndjson`: line 1 is the envelope (`{"policy", "options"}`; for `/reveal` also `"key_material"`), every further line is one array item (for `/reveal`: an item line of an earlier obfuscate stream, `{"result", "reveal_hints"}`). An NDJSON request always gets an NDJSON response. Bodies up to `max_ndjson_mb` (256) are accepted on these four routes for this content type (the M0 body limit stays for JSON); at most `ndjson_max_items` (1 000 000) items; every item obeys M0's per-document limits (`max_items` values, `max_text_chars`).

**Semantics (exact):** each item is processed as its own document — the item line equals what the JSON route returns for `{"data": item, ...}` with the same key material / seed. Key material (obfuscate) or the synthesis seed (synthesize) is resolved **once per stream**: the caller's, or generated up front with M0's `generate_key_material(scope)` / `generate_synthesis_seed()` and passed to every item through `RequestOptions.consistency`, so all items share one key and one seed. Items are processed in chunks of `ndjson_chunk_items` (500) through `asyncio.to_thread`, so the event loop keeps serving.

Lines (UTF-8 JSON, one per line, `\n`):
```json
{"type": "header", "trace_id": "…", "mode": "obfuscate", "policy": {"name": "…", "hash": "…"}, "key_material": {…}}
{"type": "item", "index": 0, "result": {…}, "findings": [{"locator": {"path": "$.email", …}, …}], "reveal_hints": […]}
{"type": "report", "items": 2, "report": {"findings_total": 5, "findings_by_type": {…}, "methods_used": {…}, "verification": {"passed": true, "checked": 7}}}
```
`key_material` appears in the header only for obfuscate (wrapped when `options.wrap_key` is given, exactly as the JSON route returns it); `synthesis_seed` for synthesize when the scope is not `request` (M0 rule). Item paths are relative to the item (`$` = the item). `detect` items carry `findings` and `record_sensitivity`, no `result`. **Fail closed:** an item that fails verification under `strict` (or any engine error) produces `{"type": "error", "index": i, "code": "leakage" | "bad_request", "detail": "<value-free>"}` and ends the stream — no later item is processed; earlier item lines were each verified on their own. One audit event per stream (counts summed, `outcome` = `ok` | `verification_failed` | `bad_request`).

Interfaces (`dps/layers/api/ndjson.py`):
```python
NDJSON = "application/x-ndjson"
class NdjsonEnvelope(BaseModel):
    policy: str | dict[str, Any] | None = None
    options: RequestOptions = Field(default_factory=RequestOptions)
    key_material: dict[str, Any] | None = None          # /reveal only
def wants_ndjson(request: Request) -> bool               # Accept lists application/x-ndjson
def is_ndjson_request(request: Request) -> bool          # Content-Type is application/x-ndjson
async def read_ndjson(request: Request, settings: Settings) -> tuple[NdjsonEnvelope, list[Any]]
    # CodedHTTPException 400 ndjson_invalid (line number only, never the line), 413 ndjson_too_large
def resolve_stream_material(engine: PrivacyEngine, mode: str, policy_ref: Any, options: RequestOptions) -> RequestOptions
    # returns options whose consistency carries the stream's key material / seed (caller's or generated once)
def stream_lines(request: Request, *, mode: Literal["detect", "obfuscate", "synthesize", "reveal"],
                 envelope: NdjsonEnvelope, items: list[Any]) -> StreamingResponse
```
The four routes branch first: `if is_ndjson_request(request) or (wants_ndjson(request) and isinstance(body.data, list))` → `stream_lines(...)`; else the M0 path unchanged. For NDJSON request bodies the routes read the body themselves (`read_ndjson`); FastAPI's JSON parsing is skipped by declaring the body with `Body(default=None)` and an `openapi_extra` that still documents `EngineRequest`.

- [ ] **Step 1: Write the failing tests** — `tests/test_ndjson.py` (TestClient, `auth_enabled=False`, stub LLM):
- `test_json_array_streams_when_ndjson_is_accepted`: `POST /api/v1/obfuscate` `{"data": [{"email": "a.k@example-mail.in"}, {"email": "b.m@example-mail.in"}], "policy": "gdpr-pseudonymise"}` with `Accept: application/x-ndjson` → content type `application/x-ndjson`; lines = header, item 0, item 1, report; the header has `key_material`; each item's `result` equals the JSON route's result for that single item called with the header's key material.
- `test_ndjson_request_body_streams_items`: the same two items as an NDJSON body (envelope line + 2 item lines) → identical item lines to the previous test when the envelope passes the same key material.
- `test_one_key_and_one_seed_per_stream`: 1 200 items (3 chunks) of `{"email": …}` with repeated values → equal inputs give equal tokens across chunks; synthesize → equal inputs give equal surrogates across chunks.
- `test_reveal_stream_round_trips`: feed the obfuscate stream's item lines (as an NDJSON body with the key in the envelope) to `/api/v1/reveal` → every item's `result` equals the original item.
- `test_without_accept_json_is_unchanged`: the same array without the Accept header → M0's JSON response shape.
- `test_fail_closed_ends_the_stream`: monkeypatch the engine's verifier to fail on item 1 → lines: header, item 0, `{"type": "error", "index": 1, "code": "leakage"}`; nothing after; the body contains no input value.
- `test_limits_and_malformed_lines`: `ndjson_max_items=2` and 3 items → 413 `ndjson_too_large`; a non-JSON second line → 400 `ndjson_invalid` whose detail names line 2 and does not contain the line.
- `test_one_audit_event_per_stream`: `MemoryAuditSink` holds one event for a 3-item stream with `findings_total` summed.

- [ ] **Step 2: Run to confirm failure** — `pytest -q tests/test_ndjson.py` → FAIL (`ModuleNotFoundError: dps.layers.api.ndjson`).
- [ ] **Step 3: Implement** `ndjson.py`, the route branches, the body limit for `application/x-ndjson` on the four routes, the settings.
- [ ] **Step 4: Run** — `pytest -q tests/test_ndjson.py tests/test_api.py` → `8 passed` plus M0's API tests unchanged; unit gate green.
- [ ] **Step 5: Commit**

```bash
git add dps/layers/api/ndjson.py dps/layers/api/routers/detect.py dps/layers/api/routers/transform.py dps/layers/api/common.py dps/app.py dps/config.py .env.example tests/test_ndjson.py
git commit -m "feat(api): NDJSON streaming for large arrays on detect, obfuscate, synthesize and reveal" -- dps/layers/api/ndjson.py dps/layers/api/routers/detect.py dps/layers/api/routers/transform.py dps/layers/api/common.py dps/app.py dps/config.py .env.example tests/test_ndjson.py
```

---

### Task 19B: `Idempotency-Key` on the Layer 1 routes (spec §6)

**Files:**
- Create: `dps/layers/api/idempotency.py`, `tests/test_idempotency_l1.py`
- Modify: `dps/layers/api/routers/{detect,transform,documents,schema}.py`, `dps/layers/api/ndjson.py` (Task 19A: streams use the same hook), `dps/app.py` (one cache per app in `app.state.idempotency`; CORS `allow_headers` += `Idempotency-Key`, `expose_headers` += `Idempotent-Replay`), `dps/config.py` + `.env.example` (`idempotency_ttl_seconds`, `idempotency_max_entries`, contract §10)

Spec §6: "`Idempotency-Key` is honoured." Layer 1 is stateless (no database, spec §4), so L1 idempotency is a **replica-local replay cache** that stores no output and no value: per key it keeps the request body's SHA-256, the trace id and — only when DPS generated them — the key material / synthesis seed of the original call. A retry with the same key and the same body re-runs the engine with that material, which is deterministic (M0: AES-SIV tokens, FF1, seeded surrogates), so it returns the same result; a retry that lands on another replica cannot be recognised, so a caller who needs cross-replica idempotency passes its own `consistency.key_material` / `synthesis_seed` (documented in `api-m1.md`). Durable job routes keep M2's database idempotency (contract §9).

Rules (contract §9):
- Header `Idempotency-Key` matching `^[A-Za-z0-9_.:-]{1,128}$`, else 422 `idempotency_key_invalid`. Absent → M0 behaviour.
- Scope = `(tenant_id, key_id, route template, Idempotency-Key)`, stored as its SHA-256. Body hash = SHA-256 of the raw body (multipart: of the file bytes plus the `request` form field).
- Same scope + same body within `idempotency_ttl_seconds` (600) → replay: response header `Idempotent-Replay: true` (the header M2's job routes use, contract §9), the original `trace_id`, audit outcome `replayed`. Same scope + different body → 422 `idempotency_key_reused`. `idempotency_ttl_seconds = 0` disables the cache (the header is then accepted and ignored).
- Generated material: when the caller sent no key material (obfuscate, `documents/obfuscate`) or seed (synthesize, `documents/synthesize`, `schema/synthesize`), the route generates it **before** calling the engine (`generate_key_material(scope)` / `generate_synthesis_seed()`, M0) and passes it through `RequestOptions.consistency`, so the cache can keep it; a wrapped key (`wrap_key`) still returns wrapped — DPS keeps the clear material only in this in-process cache, never logs it, and drops it at TTL or eviction (LRU at `idempotency_max_entries`). HANDOFF records this custody note.
- A synthesize call that used the optional LLM rewrite (non-deterministic) is replayed with the deterministic fill and says so (`report.notes["idempotent_replay_without_llm"] = 1`).

Interfaces:
```python
KEY_RE: re.Pattern[str]
def parse_idempotency_key(request: Request) -> str | None          # CodedHTTPException(422, "idempotency_key_invalid")
@dataclass(frozen=True)
class IdemEntry: body_sha256: str; trace_id: str; material: dict[str, Any] | None; created: float
class IdempotencyCache:
    def __init__(self, *, ttl_seconds: int, max_entries: int, clock: Callable[[], float] = time.monotonic) -> None
    def get(self, scope: str, body_sha256: str) -> IdemEntry | None   # raises IdempotencyConflict on a body mismatch
    def put(self, scope: str, entry: IdemEntry) -> None
def scope_of(request: Request, key: str) -> str                      # sha256 of tenant, key id, route template, key
```
M2's `dps/layers/api/deps.py::idempotency_key` applies the same pattern and error code (contract §9); M2 does not import M1's module (M2 does not depend on M1).

- [ ] **Step 1: Write the failing tests** — `tests/test_idempotency_l1.py`:
- `test_replay_returns_identical_tokens`: `POST /api/v1/obfuscate` (no key material, `Idempotency-Key: k-1`) twice → identical `result` and `key_material`, second response `Idempotent-Replay: true` and the first `trace_id`.
- `test_without_the_header_each_call_gets_a_new_key`: two plain calls → different `key_material.kid`.
- `test_same_key_other_body_is_refused`: `k-2` with body A, then body B → 422 `{"code": "idempotency_key_reused"}`; the detail contains no body value.
- `test_scope_is_per_tenant_and_route`: `k-3` on `/obfuscate` and on `/synthesize` → both executed (no conflict); `k-3` under another tenant → executed.
- `test_wrapped_key_is_replayed_wrapped`: with `options.wrap_key` → both responses carry the same `wrapped_dek` and no `dek`.
- `test_synthesize_and_documents_replay`: `/synthesize` and `/documents/obfuscate` (a small text PDF from Task 6's fixtures) replay identically.
- `test_ttl_and_eviction`: with a fake clock, after `ttl + 1` s the same key runs fresh (new `kid`); `idempotency_max_entries=1` evicts the older key.
- `test_invalid_header`: `Idempotency-Key: "bad key!"` → 422 `idempotency_key_invalid`.
- `test_no_material_in_logs`: `capture_logs` around a replay → no log record contains the `dek` or any input value.

- [ ] **Step 2: Run to confirm failure** → `ModuleNotFoundError: dps.layers.api.idempotency`.
- [ ] **Step 3: Implement** the module, the route wiring (one helper `with_idempotency(request, body_bytes, run)` in `common.py` used by every L1 route and by `stream_lines`), the app state and CORS headers, the settings.
- [ ] **Step 4: Run** — `pytest -q tests/test_idempotency_l1.py tests/test_ndjson.py tests/test_api.py` → `9 passed` + Task 19A's + M0's; unit gate green.
- [ ] **Step 5: Commit**

```bash
git add dps/layers/api/idempotency.py dps/layers/api/common.py dps/layers/api/ndjson.py dps/layers/api/routers/detect.py dps/layers/api/routers/transform.py dps/layers/api/routers/documents.py dps/layers/api/routers/schema.py dps/app.py dps/config.py .env.example tests/test_idempotency_l1.py
git commit -m "feat(api): honour Idempotency-Key on the Layer 1 routes with a value-free replay cache" -- dps/layers/api/idempotency.py dps/layers/api/common.py dps/layers/api/ndjson.py dps/layers/api/routers/detect.py dps/layers/api/routers/transform.py dps/layers/api/routers/documents.py dps/layers/api/routers/schema.py dps/app.py dps/config.py .env.example tests/test_idempotency_l1.py
```

---

### Task 19C: Local Tesseract OCR when no Azure endpoint is configured (spec §6)

**Files:**
- Modify: `dps/layers/documents/ocr.py` (`TesseractOcrClient`, `build_ocr_client`), `dps/config.py` (`ocr_provider` values, `tesseract_cmd`, `tesseract_langs`, `tesseract_timeout_seconds`, property `ocr_engine`), `dps/posture.py`, `Dockerfile` (runtime stage: `apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng` and `ENV DPS_OCR_PROVIDER=auto`), `pyproject.toml` (marker `tesseract`), `.env.example`, `HANDOFF.md`, `frontend/src/components/OcrStatus.tsx` (engine label "Local Tesseract")
- Test: `tests/documents/test_ocr_tesseract.py`

Spec §6: scanned pages and images go "via the Azure Read/DI contract through the org's gateway … or local Tesseract when unset". `ocr_provider` (Task 1) takes five values (contract §10):

| `DPS_OCR_PROVIDER` | Engine |
|---|---|
| `""` (settings default) or `off` | none — `NullOcrClient` (unit tests and L1-only deployments stay OCR-free) |
| `azure` | `AzureReadClient` when endpoint and key are set, else none (Task 7) |
| `tesseract` | `TesseractOcrClient` (readiness `ocr: down, tesseract_missing` when the binary is absent) |
| `auto` (the **image** default) | `azure` when an endpoint and key are configured, else `tesseract` when `tesseract_cmd` is on `PATH`, else none |

`Settings.ocr_engine -> Literal["azure", "tesseract", "none"]` implements the table (the binary check is `shutil.which(self.tesseract_cmd)`); `ocr_configured` keeps its Task 1 meaning (azure configured). `build_ocr_client` dispatches on `ocr_engine`.

`TesseractOcrClient` (implements Task 7's `OcrClient`; no Python wrapper dependency — the CLI is called directly):
```python
class TesseractOcrClient:
    engine: str                                   # "tesseract-<major.minor>" from `tesseract --version`
    def __init__(self, *, cmd: str, langs: str, timeout_s: float, max_concurrency: int) -> None
    def configured(self) -> bool                  # binary resolvable
    async def read(self, image: bytes, *, content_type: str = "image/png") -> OcrPage
        # asyncio.create_subprocess_exec(cmd, "stdin", "stdout", "-l", langs, "--psm", "3", "tsv"),
        # image on stdin, ≤ timeout_s, bounded by a semaphore of max_concurrency (ocr_max_concurrency)
    async def probe(self) -> tuple[bool, str]     # `tesseract --version` exit 0 → (True, "up")
    async def aclose(self) -> None
def parse_tsv(tsv: str) -> OcrPage
    # level-1 row → width/height (unit "pixel"); level-5 rows → OcrWord(text, polygon from left/top/width/height,
    # confidence = conf/100 (None when conf < 0), line = ordinal of (block_num, par_num, line_num)); empty words skipped
```
Errors are `OcrError` codes only — `ocr_unconfigured` (binary missing), `ocr_timeout` (process killed after `timeout_s`), `ocr_failed` (non-zero exit) — never Tesseract's stderr (it can echo recognised text). The rest of the pipeline is unchanged: `ocr_words_to_points` and the hybrid merge consume `OcrPage` exactly as for Azure, and redaction re-reads output pages with whichever engine is configured.

Posture (staging/prod): `ocr_provider=tesseract` or `auto` resolving to Tesseract is allowed (local, no egress); `auto` resolving to `none` adds the problem "OCR is enabled (auto) but neither an Azure endpoint nor tesseract is available".

- [ ] **Step 1: Update Task 1's tests and write the new ones** — in `tests/test_config.py` the invalid-values case `("ocr_provider", "tesseract")` becomes `("ocr_provider", "paddle")` (Tesseract is now valid); add `test_ocr_engine_resolution` (monkeypatch `shutil.which`): `""`/`off` → `none`; `azure` without a key → `none`, with endpoint+key → `azure`; `tesseract` → `tesseract`; `auto` + endpoint+key → `azure`; `auto` + binary → `tesseract`; `auto` + nothing → `none`. `tests/documents/test_ocr_tesseract.py`:
  - `test_parse_tsv_builds_words_lines_and_page_size` (offline): a fixed TSV sample (two lines, five words, one `conf -1` row) → `width/height`, five `OcrWord`s, line ordinals `[0, 0, 0, 1, 1]`, polygons from the boxes, `confidence is None` for the `-1` row.
  - `test_missing_binary_is_unconfigured` (offline): `cmd="/nonexistent/tesseract"` → `configured() is False`, `read` raises `OcrError` code `ocr_unconfigured`; readiness detail `tesseract_missing`.
  - `test_stderr_never_leaks` (offline): a fake `cmd` script that prints a sentinel to stderr and exits 1 → `OcrError("ocr_failed")` whose text lacks the sentinel.
  - `@pytest.mark.tesseract test_reads_the_demo_scan`: skip unless `shutil.which("tesseract")` (fails under `DPS_TEST_REQUIRE_SERVICES=1` only when `DPS_TEST_REQUIRE_TESSERACT=1`); render page 1 of `demo/documents/kyc-scanned.pdf` at 200 dpi (Task 5's renderer) → ≥ 20 words, the fixture's synthetic name tokens among them.
  - `@pytest.mark.tesseract test_pipeline_redacts_with_tesseract`: `DocumentPipeline` with `TesseractOcrClient` on the scanned demo → `document.ocr_pages ≥ 1`, redacted PDF re-read finds 0 originals (the Task 9 assertions, engine swapped).
- [ ] **Step 2: Run to confirm failure** → `ImportError: cannot import name 'TesseractOcrClient'`.
- [ ] **Step 3: Implement**; Dockerfile runtime stage installs `tesseract-ocr tesseract-ocr-eng` (Debian bookworm packages; ~30 MB) and sets `ENV DPS_OCR_PROVIDER=auto` (compose still sets `azure` for the `docs` profile, contract §11); `pyproject.toml` marker `"tesseract: needs a tesseract binary on PATH"`; HANDOFF rows for the three settings and the provider table; `OcrStatus` shows "Local Tesseract" when `components.ocr.engine` starts with `tesseract`.
- [ ] **Step 4: Run** — `pytest -q tests/test_config.py tests/documents/test_ocr_tesseract.py tests/documents/test_ocr_client.py` → green (`2 skipped` on this Mac without tesseract); in the image: `docker compose run --rm -e DPS_OCR_PROVIDER=auto app python -m pytest -q -m tesseract tests/documents/test_ocr_tesseract.py` → `2 passed` (the image has the binary; tests are copied by the Dockerfile's test stage or bind-mounted — record which in M1-DEVIATIONS); `docker build` succeeds; unit gate green.
- [ ] **Step 5: Commit**

```bash
git add dps/layers/documents/ocr.py dps/config.py dps/posture.py Dockerfile pyproject.toml .env.example HANDOFF.md frontend/src/components/OcrStatus.tsx tests/test_config.py tests/documents/test_ocr_tesseract.py
git commit -m "feat(documents): local Tesseract OCR when no Azure endpoint is configured" -- dps/layers/documents/ocr.py dps/config.py dps/posture.py Dockerfile pyproject.toml .env.example HANDOFF.md frontend/src/components/OcrStatus.tsx tests/test_config.py tests/documents/test_ocr_tesseract.py
```

---

### Task 19D: T3 — LLM metadata labelling, opt-in, value-free (spec §5.3)

**Files:**
- Create: `dps/engine/detect/llm_label.py`, `tests/test_llm_label.py`
- Modify (M0 touch points): `dps/engine/service.py` (`PrivacyEngine.label_leftovers`; `detect`/`plan`/`obfuscate`/`synthesize` gain keyword `extra_findings: Sequence[Finding] = ()`), `dps/engine/policy.py` (`DetectConfig.tiers` accepts `llm_label` if M0 validates tier names), `dps/layers/api/routers/{detect,transform,policies}.py` and `dps/layers/api/ndjson.py` (await `label_leftovers` before the engine call), `dps/observability.py` (`LLM_LABEL_FIELDS` counter; allow-list `fields`, `verdicts`), `dps/config.py` (`llm_label_max_fields`, `llm_label_min_confidence`, contract §10)

Spec §5.3 tier T3: "the gateway classifies *field metadata* — names, types, a few de-identified or synthetic sample values — to resolve semantic types for ambiguous columns; it never receives raw values." M0 shipped T0–T2 only and M1's Policies page shows an "LLM labelling on/off" indicator (`detect.tiers` includes `llm_label`); this task makes that tier real for Layer 1 JSON. M5 (plan Task 8B) wires the same module into L2 discovery and L3 classification (contract §15). DPS sends **value shapes**, never values — stricter than the spec's "de-identified samples".

Interfaces (`dps/engine/detect/llm_label.py`):
```python
class FieldMeta(BaseModel):
    ref: str                                     # caller's handle, echoed in the verdict ("$.loan.ref_cd", "col:3")
    name: str                                    # key / column / field name; aliased "field_N" when it looks like a value
    data_type: str | None = None                 # "string", "number", "VARCHAR2(20)", "input[type=text]"
    shapes: list[str] = Field(default_factory=list, max_length=3)   # value_shape() outputs (dps/engine/shapes.py), ≤ 24 chars
    label: str | None = None                     # a human label, already redacted by the caller (L3)
    hints: dict[str, str | int | float | bool] = Field(default_factory=dict)
class LabelVerdict(BaseModel):
    ref: str; entity_type: str; confidence: float
async def label_fields(fields: list[FieldMeta], *, gateway: LlmGateway | None, settings: Settings,
                       protected: Sequence[str] = ()) -> list[LabelVerdict]
```
Rules: no call when the gateway is missing, unavailable or `stub` (deterministic: returns `[]`); at most `llm_label_max_fields` (40) fields per call (the rest are skipped and counted); a `name` or `label` in which M0's `Detector` finds a whole-value entity is replaced by `field_<n>`; the prompt (a fixed system text listing the taxonomy's entity type names, then the fields as compact JSON) passes `assert_placeholder_safe(prompt, protected)` (M0) — on `LeakToModelError` no call is made and `[]` is returned (counted as `fallback: leak_guard`); the reply is parsed leniently (first `[` to last `]`), each verdict kept only when `ref` is one of the sent refs, `entity_type` is in the taxonomy registry and `confidence ≥ llm_label_min_confidence` (0.7); `LlmUnavailable`/`LlmCallError` → `[]`. Nothing is logged but counts (`fields`, `verdicts`, `fallback`).

`PrivacyEngine.label_leftovers(payload, policy_ref, options) -> list[Finding]` (async): only when `"llm_label" in policy.detect.tiers` and the payload is JSON `data` (T3 labels fields, not free text). It runs the T0–T2 detector, collects leaf paths with no finding and a string/number value, groups them by path template (`[n]` → `[*]`), builds one `FieldMeta(ref=template, name=<last key>, data_type=<json type>, shapes=[value_shape(v) for up to 3 values])` per template with `protected` = those leaf values, calls `label_fields`, and returns one `Finding(locator=Locator(path=p), entity_type=e, score=min(conf, 0.85), detectors=["llm_label"], sensitivity=entity_info(e).sensitivity, evidence=["llm_label"], value_hash=hash_value(v))` per leaf of each labelled template (0.85 cap: T3 never outranks a checksum). The engine methods merge `extra_findings` into their findings before planning, so the plan, the transformation, verification and the report treat them like any other finding; `report.llm_calls` counts the labelling call.

- [ ] **Step 1: Write the failing tests** — `tests/test_llm_label.py` (fake gateways subclassing M0's `LlmGateway`, as in M0's `test_llm_text.py`; they record `messages`):
- `test_no_call_without_the_tier_or_with_the_stub`: policy tiers without `llm_label` → no call; with the tier but `llm_provider="stub"` → no call, `[]`.
- `test_leftover_json_field_is_labelled_and_transformed`: `{"loan": {"ref_cd": "AB-1234-77"}}` (no T0–T2 finding), policy with tiers `[structural, patterns, ner, llm_label]`, gateway reply `[{"ref": "$.loan.ref_cd", "entity_type": "CUSTOMER_ID", "confidence": 0.92}]` → `/api/v1/obfuscate` returns a finding with `detectors == ["llm_label"]`, `score == 0.85`, and the value transformed; the recorded prompt contains `ref_cd` and `XX-9999-99` but not `AB-1234-77`.
- `test_array_items_share_one_verdict`: `{"rows": [{"code": "A1"}, {"code": "B2"}]}` → one field `$.rows[*].code` sent; two findings returned.
- `test_value_like_names_are_aliased`: a key that is itself an e-mail address → the prompt shows `field_1`, not the address.
- `test_leak_guard_blocks_the_call`: force a value into the prompt (monkeypatch `value_shape` to identity) → no call made, `[]`, fallback counted.
- `test_bad_replies_are_ignored`: unknown entity type, unknown ref, confidence 0.3, non-JSON text → `[]` and no error.
- `test_raw_pii_counter_stays_zero`: after all of the above, M0's `LLM_RAW_PII` value is unchanged.
- [ ] **Step 2: Run to confirm failure** → `ModuleNotFoundError: dps.engine.detect.llm_label`.
- [ ] **Step 3: Implement** the module, the engine method and keyword, the route calls (each route: `extra = await engine.label_leftovers(payload, body.policy, body.options)` then `call=lambda: engine.<op>(payload, body.policy, body.options, extra_findings=extra)`), the settings and metrics. The Policies page indicator (Task 18) needs no change: it already reflects `detect.tiers`, which is now a working tier.
- [ ] **Step 4: Run** — `pytest -q tests/test_llm_label.py tests/test_llm_text.py tests/test_api.py` → `7 passed` + M0's unchanged; unit gate green.
- [ ] **Step 5: Commit**

```bash
git add dps/engine/detect/llm_label.py dps/engine/service.py dps/engine/policy.py dps/layers/api/routers/detect.py dps/layers/api/routers/transform.py dps/layers/api/routers/policies.py dps/layers/api/ndjson.py dps/observability.py dps/config.py tests/test_llm_label.py
git commit -m "feat(detect): opt-in T3 LLM metadata labelling of leftover JSON fields, value shapes only" -- dps/engine/detect/llm_label.py dps/engine/service.py dps/engine/policy.py dps/layers/api/routers/detect.py dps/layers/api/routers/transform.py dps/layers/api/routers/policies.py dps/layers/api/ndjson.py dps/observability.py dps/config.py tests/test_llm_label.py
```

---

### Task 20: No-values log scan, console browser check, API reference, handoff, release

**Files:**
- Create: `tests/test_no_values_in_logs_m1.py`, `tools/console_check.py`, `docs/technical/api-m1.md`, `docs/superpowers/build/m1-console/*.png` (screenshots from the check)
- Modify: `HANDOFF.md`, `README.md`, `dps/__init__.py` (`__version__ = "0.2.0"`), `pyproject.toml` (`version = "0.2.0"`), `frontend/package.json` (`"version": "0.2.0"`), `docs/superpowers/build/M1-DEVIATIONS.md`

`tests/test_no_values_in_logs_m1.py` (contract §0.1: every milestone adds a log scan for its modules):
```python
"""M1 modules never log, print or raise with a value, a key or a response body (contract §0.1)."""

from __future__ import annotations

import logging

import httpx
import pytest

pytestmark = pytest.mark.docs
pytest.importorskip("pdfplumber")

from dps.config import Settings  # noqa: E402
from dps.engine.detect.ensemble import Detector, DetectorConfig  # noqa: E402
from dps.engine.service import PrivacyEngine  # noqa: E402
from dps.engine.synth.schema_gen import execute_spec  # noqa: E402
from dps.engine.synth.schema_profile import profile_from_sample, propose_spec  # noqa: E402
from dps.layers.documents import DocumentPipeline  # noqa: E402
from dps.layers.documents.errors import DocumentError  # noqa: E402
from dps.layers.documents.models import DocumentRequest  # noqa: E402
from dps.layers.documents.ocr import AzureReadClient, OcrError  # noqa: E402
from dps.llm.gateway import LlmGateway, LlmResult  # noqa: E402
from dps.observability import configure_logging  # noqa: E402
from tests.documents.fixtures import (  # noqa: E402
    CUSTOMERS,
    FakeOcr,
    csv_document,
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
    for needle in VALUES + SECRETS:
        assert needle not in blob, needle
```

`tools/console_check.py` (verified while writing against a route-intercepted copy of the built console: every section, download, reveal, draft, recognizer and plan step passes at both sizes; the "boxes on page 1" check needs the real engine, which finds the Aadhaar number on page 1 of the demo scan):
```python
"""Browser check of the M1 console pages against their mockups (Definition of Done).

    uv pip install -e ".[uitest]"                    # playwright==1.55.0
    docker compose --profile docs up -d               # the stack under test (Task 19)
    DPS_TEST_APP_URL=http://127.0.0.1:8400 DPS_API_KEY=dps_local_dev_key_change_me python tools/console_check.py

Drives /playground/documents (demo KYC scan → Redact → download → reveal → Synthesize) and /policies
(draft edit, recognizer test on the engine, plan preview) at 1440×900 and 390×844 in dark and light;
asserts each mockup section is present, no horizontal scroll and no console error; writes app and
mockup screenshots side by side to docs/superpowers/build/m1-console/. Prints PASS/FAIL lines only.

playwright==1.55.0 expects Chromium build 1187. When that build is not installed (this Mac caches
1148/1234/1243), the newest cached chrome-headless-shell is used instead, or the binary named by
DPS_PLAYWRIGHT_CHROMIUM.
"""

from __future__ import annotations

import os
import re
import sys
from glob import glob
from pathlib import Path

from playwright.sync_api import Browser, Error, Page, Playwright, expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
BASE = os.environ.get("DPS_TEST_APP_URL", "http://127.0.0.1:8400").rstrip("/")
KEY = os.environ.get("DPS_API_KEY", "dps_local_dev_key_change_me")
OUT = ROOT / "docs" / "superpowers" / "build" / "m1-console"
SIZES = {"desktop": (1440, 900), "phone": (390, 844)}
MOCKUPS = {"documents": "08-documents.html", "policies": "05-policies.html"}
DOC_SECTIONS = ("Input", "Findings", "Output · redacted PDF", "Text extraction", "Key material")
POLICY_SECTIONS = (
    "Presets",
    "Treatment per entity type",
    "Declared fields",
    "Custom recognizer",
    "Plan preview",
    "Version history",
)
TIMEOUT_MS = 180_000
failures: list[str] = []


def check(name: str, ok: bool) -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {name}")
    if not ok:
        failures.append(name)


def _revision(path: str) -> int:
    match = re.search(r"headless_shell-(\d+)", path)
    return int(match.group(1)) if match else 0


def launch(p: Playwright) -> Browser:
    explicit = os.environ.get("DPS_PLAYWRIGHT_CHROMIUM")
    if explicit:
        return p.chromium.launch(executable_path=explicit)
    try:
        return p.chromium.launch()
    except Error:
        cache = Path(
            os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
            or Path.home() / "Library/Caches/ms-playwright"
        )
        shells = sorted(
            glob(str(cache / "chromium_headless_shell-*/*/chrome-headless-shell")), key=_revision
        )
        if not shells:
            raise
        return p.chromium.launch(executable_path=shells[-1])


def no_horizontal_scroll(page: Page) -> bool:
    return bool(page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"))


def documents_flow(page: Page, tag: str) -> None:
    page.goto(f"{BASE}/playground/documents")
    expect(page.get_by_test_id("ocr-status")).to_contain_text("up")
    page.get_by_role("button", name="Load demo KYC scan").click()
    page.get_by_test_id("mode-red").click()
    page.get_by_test_id("run").click()
    expect(page.get_by_test_id("verify-ok")).to_be_visible(timeout=TIMEOUT_MS)
    check(f"{tag} documents: boxes drawn on page 1", page.get_by_test_id("page-box").count() > 0)
    for title in DOC_SECTIONS:
        check(
            f"{tag} documents: '{title}'", page.get_by_text(title, exact=False).first.is_visible()
        )
    with page.expect_download() as download:
        page.get_by_role("button", name="Download redacted PDF").click()
    check(
        f"{tag} documents: redacted PDF downloads",
        download.value.suggested_filename.endswith(".redacted.pdf"),
    )
    page.get_by_role("button", name="Reveal text with key").click()
    expect(page.get_by_text("original text restored")).to_be_visible(timeout=30_000)
    check(f"{tag} documents: no horizontal scroll", no_horizontal_scroll(page))
    page.screenshot(path=str(OUT / f"documents-{tag}.png"), full_page=True)
    page.get_by_test_id("mode-syn").click()
    page.get_by_test_id("run").click()
    expect(page.get_by_text("Output · synthetic text, page view")).to_be_visible(timeout=TIMEOUT_MS)
    check(f"{tag} documents: synthesize renders", page.get_by_test_id("verify-ok").is_visible())


def policies_flow(page: Page, tag: str) -> None:
    page.goto(f"{BASE}/policies/gdpr-pseudonymise")
    expect(page.get_by_test_id("policy-header")).to_be_visible()
    for title in POLICY_SECTIONS:
        check(f"{tag} policies: '{title}'", page.get_by_text(title, exact=False).first.is_visible())
    page.get_by_label("Obfuscate method for CVV").select_option("det_token")
    check(
        f"{tag} policies: draft counted",
        page.get_by_text(re.compile(r"draft · 1 change")).first.is_visible(),
    )
    page.get_by_role("button", name="Run on the engine").click()
    expect(page.get_by_text("Engine result")).to_be_visible(timeout=30_000)
    page.get_by_role("button", name="Re-run plan").click()
    expect(page.locator("#plan tbody tr").nth(3)).to_be_visible(timeout=30_000)
    check(f"{tag} policies: plan rows", page.locator("#plan tbody tr").count() >= 4)
    check(f"{tag} policies: no horizontal scroll", no_horizontal_scroll(page))
    page.screenshot(path=str(OUT / f"policies-{tag}.png"), full_page=True)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = launch(p)
        for theme in ("dark", "light"):
            for size, (width, height) in SIZES.items():
                tag = f"{theme}-{size}"
                context = browser.new_context(
                    viewport={"width": width, "height": height},
                    color_scheme=theme,
                    accept_downloads=True,
                )
                context.add_init_script(
                    f"localStorage.setItem('dps.apiKey', {KEY!r}); "
                    f"localStorage.setItem('dps.theme', '{theme}');"
                )
                page = context.new_page()
                errors: list[str] = []
                page.on(
                    "console",
                    lambda m, errors=errors: errors.append(m.type) if m.type == "error" else None,
                )
                page.on("pageerror", lambda e, errors=errors: errors.append("pageerror"))
                documents_flow(page, tag)
                policies_flow(page, tag)
                check(f"{tag}: no console errors", not errors)
                for name, file in MOCKUPS.items():
                    query = "?light" if theme == "light" else ""
                    page.goto((ROOT / "docs" / "design" / "mockups" / file).as_uri() + query)
                    page.screenshot(path=str(OUT / f"mockup-{name}-{tag}.png"), full_page=True)
                context.close()
        browser.close()
    print(
        f"\n{'all checks passed' if not failures else f'{len(failures)} failed'} · screenshots in {OUT.relative_to(ROOT)}"
    )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
```

`docs/technical/api-m1.md` — the M1 API as built (write it from the routers; it must contain, at least):
1. A route table: the three `/api/v1/documents/*` routes, `/schema/generator-spec`, `/schema/synthesize`, `/entities`, `/policies/test-recognizer`, the extended `/policies/presets`, scopes, and the `ocr` component of `/readyz`.
2. The multipart contract (`file` + `request` JSON), every `DocumentOptions` field with its default, and the response shape (`document`, `pages[].findings[].boxes` in PDF points with a top-left origin, `replacement`/`out_span`, `redacted_pdf_b64`, `result_csv`, `segments`).
3. The error-code table from Tasks 4, 10, 13 and 14 (`{"detail", "code"}`), including the M0 codes `bad_request`/`leakage` now present on M0 routes.
4. Worked curl examples (use `$DPS_API_KEY`):
```bash
# redact a scanned KYC PDF and save the redacted copy
curl -sS -X POST http://127.0.0.1:8400/api/v1/documents/obfuscate -H "X-API-KEY: $DPS_API_KEY" \
  -F file=@demo/documents/kyc-scanned.pdf -F 'request={"options":{"redacted_pdf":true}}' \
  | python -c 'import sys,json,base64; b=json.load(sys.stdin); open("kyc.redacted.pdf","wb").write(base64.b64decode(b["redacted_pdf_b64"])); print(b["document"]["redaction"])'
# a generator spec from DDL, then 1 000 reproducible rows as CSV
curl -sS -X POST http://127.0.0.1:8400/api/v1/schema/generator-spec -H "X-API-KEY: $DPS_API_KEY" -H 'Content-Type: application/json' \
  -d '{"ddl":"CREATE TABLE customers (customer_id INTEGER PRIMARY KEY, full_name VARCHAR(80), email VARCHAR(120))"}'
curl -sS -X POST http://127.0.0.1:8400/api/v1/schema/synthesize -H "X-API-KEY: $DPS_API_KEY" -H 'Content-Type: application/json' \
  -d '{"ddl":"CREATE TABLE customers (customer_id INTEGER PRIMARY KEY, full_name VARCHAR(80))","rows":1000,"format":"csv"}'
```
5. Notes: redacted PDFs are image-only (not searchable, metadata emptied, annotations/forms not drawn); OCR verification semantics; `llm_rewrite` is ignored for documents; P1 for schema proposals (what the profile contains); the evaluation report location and how to regenerate it.
6. Tasks 19A–19D: NDJSON streaming (request/response line formats, limits, fail-closed `error` line, one key per stream), `Idempotency-Key` on the L1 routes (replica-local replay, conflict code, cross-replica advice: pass your own key material), the OCR provider table (`off`/`azure`/`tesseract`/`auto`) and the opt-in `llm_label` tier (what metadata is sent — names, types, value shapes — and what never is).

`HANDOFF.md` — add an "M1" section with: build (`.[fpe,docs]`, node 22 stage), run (`docker compose --profile docs up --build -d`, ports 8400 and 5016), the M1 configuration table (every setting of Task 1 with default and meaning; `DPS_AZURE_VISION_KEY_FILE` for prod; gateway health path `/_gw/health`), probes (`/readyz` `ocr` component: `unconfigured|up|down:<status>|configured`, never required), secrets (names only: `DPS_AZURE_VISION_KEY`), and **traps**: (1) PDFium is not thread-safe — all pypdfium2 calls go through `PDFIUM_LOCK`, so renders are serialised per process (scale with replicas); (2) the OCR client refuses an `Operation-Location` on another host than `DPS_AZURE_VISION_ENDPOINT` (a gateway behind a host-rewriting proxy must return its external host); (3) the console now builds with `base: '/'` — hosting under a path prefix needs a rewrite; (4) uploads above 1 MiB spool to `/tmp` — mount it as tmpfs in OpenShift too; (5) `reportlab.rl_config.useA85 = 0` is set process-wide by `redact.py`; (6) the OCR mock reads images only — DPS rasterises PDFs itself, never send it a PDF; (7) the redacted PDF has no text layer by design; (8) Faker's es_MX `curp()`/`rfc()` ignore `seed_instance` — never use them where determinism matters; (9) `playwright==1.55.0` wants Chromium 1187, which this Mac does not cache — `tools/console_check.py` falls back to the newest cached headless shell; (10) the detector report test fails when detectors change until the report is regenerated (by design). `README.md` — "Status: M1 shipped" with the run commands, the demo flow (Playground → Document → Load demo KYC scan → Redact) and links to `docs/technical/api-m1.md` and `docs/technical/detector-eval-m1.md`.

- [ ] **Step 1: Write the log-scan test** and run it: `pytest -q tests/test_no_values_in_logs_m1.py` → `1 passed`.
- [ ] **Step 2: Run the console check** against the stack from Task 19: `uv pip install -e ".[uitest]" && DPS_TEST_APP_URL=http://127.0.0.1:8400 DPS_API_KEY=dps_local_dev_key_change_me python tools/console_check.py` → `all checks passed`; open the screenshot pairs (`documents-*.png` beside `mockup-documents-*.png`, `policies-*.png` beside `mockup-policies-*.png`) and record in `M1-DEVIATIONS.md` every visible difference from the mockups and whether it is intended (e.g. real page images instead of the mockup's drawn paper; OCR control; versions card).
- [ ] **Step 3: Write `docs/technical/api-m1.md`, the HANDOFF and README sections; bump the version** to 0.2.0 in the three files.
- [ ] **Step 4: Gates** — Python gates + frontend gates green; `python tools/eval_detectors.py run --corpus tests/eval/corpus --json docs/technical/detector-eval-m1.json --markdown docs/technical/detector-eval-m1.md && git diff --exit-code docs/technical/detector-eval-m1.json`.
- [ ] **Step 5: Commit and tag**
```bash
git add tests/test_no_values_in_logs_m1.py tools/console_check.py docs/technical/api-m1.md docs/superpowers/build/m1-console HANDOFF.md README.md dps/__init__.py pyproject.toml frontend/package.json docs/superpowers/build/M1-DEVIATIONS.md
git commit -m "docs: M1 API reference, handoff and console check; log scan for M1 modules; v0.2.0" -- tests/test_no_values_in_logs_m1.py tools/console_check.py docs/technical/api-m1.md docs/superpowers/build/m1-console HANDOFF.md README.md dps/__init__.py pyproject.toml frontend/package.json docs/superpowers/build/M1-DEVIATIONS.md
git tag -a v0.2.0 -m "M1: documents and Layer 1 complete"
docker compose --profile docs down
```

---
## Task order and parallelism

Tasks run in order; each ends green and committed. Dependencies: 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9 → 10 (documents backend); 11 → 12 → 13 (schema) need only Tasks 1–3; 14 needs 2; 15 needs nothing from M1 but Task 1; 16 needs 5 and 8 (the demo generator renders and rebuilds PDFs); 17 needs 10 and 16; 18 needs 14 and 16; 19 needs 10, 13, 14 and 16; 19A needs 2 and 3 (coded errors, segments); 19B needs 19A (streams use the same replay hook) and 10/13 (documents and schema routes); 19C needs 7 and 9; 19D needs 2, 12 (P1 profile helpers) and 18 (the Policies indicator it makes real); 20 needs everything. With subagent-driven execution, {11–13}, {14}, {15} can run beside {4–10} in separate worktrees (each lane commits only its own paths; `dps/app.py` is touched by Tasks 2, 10, 13 and 14 — serialise those four commits).

## Definition of Done (M1)

All of the following, with their output summarised in the build report:

1. **Unit gates:** `ruff check dps tests tools && ruff format --check dps tests tools && mypy dps && pytest -q` → green; the only skips are service-gated (`ocr`, and M0's `ner`/`network` markers when those extras are absent).
2. **Console gates:** `cd frontend && npm run typecheck && npm run build` → clean; `frontend/dist/index.html` references `/assets/…` and `/theme-init.js`.
3. **Stack:** `docker compose --profile docs up --build -d` → `app` and `azure-ocr-mock` healthy; `curl -s http://127.0.0.1:8400/readyz` → `ready: true`, `components.ocr = {ok: true, detail: "up", engine: "azure-read-v3.2"}`.
4. **Integration gate (contract §12.4):** `DPS_TEST_REQUIRE_SERVICES=1 pytest -q -m "docs or ocr"` → all pass, none skipped.
5. **Smoke:** `DPS_SMOKE_REQUIRE_OCR=1 python tools/smoke_test.py` → every check PASS (M0's plus the ten M1 checks; no SKIP).
6. **Demo outcomes (spec §11 M1):** *redact a scanned KYC PDF* — `demo/documents/kyc-scanned.pdf` through `/documents/obfuscate` with `redacted_pdf: true` returns a PDF with 0 extractable characters, 0 text-showing operators, and an OCR re-read with 0 originals; *synthesize from a schema* — the DDL in `docs/technical/api-m1.md` → a generator spec → 1 000 rows, identical when replayed with the returned seed.
7. **Evaluation report committed:** `python tools/eval_detectors.py run --corpus tests/eval/corpus --json docs/technical/detector-eval-m1.json --markdown docs/technical/detector-eval-m1.md && git diff --exit-code docs/technical/detector-eval-m1.*` → no diff; `tests/test_eval_report.py` green (checksum types ≥ 0.95 precision and recall).
8. **Browser check against the mockups:** `python tools/console_check.py` → all checks PASS for dark/light × 1440×900/390×844 on `/playground/documents` and `/policies`; the screenshot pairs in `docs/superpowers/build/m1-console/` reviewed side by side with `08-documents.html` and `05-policies.html`, every difference listed in `M1-DEVIATIONS.md` as intended or fixed.
9. **No values anywhere:** `tests/test_no_values_in_logs_m1.py` green, and after the smoke run `docker compose logs app 2>&1 | grep -E '[0-9]{4} [0-9]{4} [0-9]{4}|@example\.(com|net|org)'` prints nothing (the demo's card/Aadhaar digit groups and e-mail domains never reach the log).
10. **Docs and release:** `docs/technical/api-m1.md`, `HANDOFF.md` (M1 section with configuration, probes, secrets by name, traps) and `README.md` committed; version 0.2.0 in `dps/__init__.py`, `pyproject.toml`, `frontend/package.json`; tag `v0.2.0`.
11. **Spec §6/§5.3 closures (Tasks 19A–19D):** `pytest -q tests/test_ndjson.py tests/test_idempotency_l1.py tests/test_llm_label.py tests/documents/test_ocr_tesseract.py` green (Tesseract cases skip on a host without the binary); inside the image `python -m pytest -q -m tesseract tests/documents/test_ocr_tesseract.py` → `2 passed`; `curl -s -H 'Accept: application/x-ndjson' …/api/v1/obfuscate` on a 3-item array prints 5 lines (header, 3 items, report); a repeated `Idempotency-Key` returns `Idempotent-Replay: true`; `api-m1.md` documents all four.

## Self-review against the spec and the contract

| Requirement | Where |
|---|---|
| Spec §6 documents: PDF text layer via pypdf, DOCX via python-docx, CSV as records, scanned pages/images via the Azure Read contract (org gateway in prod, mock locally) | Tasks 5 (pypdf structure + pdfplumber words), 6, 7, 9; compose Task 19 |
| Spec §6 `POST /api/v1/documents/{detect,obfuscate,synthesize}` multipart; per-page findings; optional redacted PDF | Tasks 9–10 (contract §9 shapes: `document {pages, ocr_pages, format}`, `pages [{page, findings}]`, `redacted_pdf_b64`) |
| Spec §6 true redaction without PyMuPDF (text removed, box drawn); "a redacted PDF is not reversible, by design" | Task 8: pypdfium2 raster + painted boxes + reportlab image-only rebuild; verified by pypdf extraction, text-operator scan, structure checks, OCR re-read (Task 9) and an independent pdftoppm render in tests |
| Spec §5.1 finding locator `page+bbox` | `PageFinding.boxes` (Task 4/9), additive — M0's `Locator` unchanged |
| Spec §5.6 synthesize from schema: LLM proposes a generator spec from schema/DDL/de-identified sample; engine executes deterministically; spec is a reusable artefact | Tasks 11–13 (P1: profile only, leak guard, stub → heuristic; spec + hash returned, replayable with the seed) |
| Spec §5.3 evaluation harness over a surrogate-generated labelled corpus; precision/recall per type; slot for bank samples | Task 15 (`tools/eval_detectors.py`, committed corpus and report, git-ignored private slot) |
| Spec §3 P1, P8; §10 value leakage in logs | segments carry tokens only; no prompt carries a value; strict leakage fails closed (text sweep, OCR re-read, sample verification); Task 20 log scan; third-party loggers capped (Task 4) |
| Spec §9 Playground document demo; console Policies page | Tasks 16–18 (mockups 08 and 05; contract §14 routes `/playground/documents`, `/policies`, `/policies/:name`) |
| Spec §11 M1: token styles, presets, policy plan endpoint | already shipped by M0 (`token_style`, six presets, `/policies/plan`); M1 adds preset summaries and the console surface over them (Tasks 14, 18) |
| Contract §1 M1 paths, §10 settings, §11 `azure-ocr-mock`, §12.1 markers `docs`/`ocr`, §12.2 service discovery, §12.4 gate, §15 boundaries | Tasks 1, 4–10, 19; no engine method semantics changed; M0 responses changed additively only (`segments`, `code`, preset summary fields) |
| Contract §0.1 log-scan test per milestone; §0.6 HANDOFF; §15 "every plan ends by updating HANDOFF, README, api-m<N>.md" | Task 20 |
| Readiness: OCR component | Task 7 (`refresh_ocr_readiness`), Task 10 (lifespan + probe loop), Task 16 (`OcrStatus`) |
| Spec §6 "arrays stream as NDJSON when large" | Task 19A (opt-in by `Accept`/`Content-Type`, one key per stream, fail closed per item, one audit event) |
| Spec §6 "`Idempotency-Key` is honoured" | Task 19B (L1 replay cache, value-free, generated material only; durable job idempotency stays M2's) |
| Spec §6 "or local Tesseract when unset" | Task 19C (`ocr_provider` `tesseract`/`auto`, image default `auto`, CLI client, stderr never surfaced) |
| Spec §5.3 T3 "LLM-assisted labelling (opt-in) … never receives raw values" | Task 19D (L1 JSON leftovers; value shapes only; leak guard; confidence capped 0.85); L2/L3 wiring in M5 Task 8B |

Placeholders: none — every task names its files, interfaces, tests (with assertions), commands, expected output and commit. Code that a task leaves to the executor is limited to prose-specified documents (`api-m1.md`, HANDOFF/README sections) whose required content is enumerated.

**Verified while writing this plan (2026-10-11, this Mac):** the layout, PDF, extraction, OCR-client, redaction and pipeline modules and their tests were run in a scratch package (pipeline against a stand-in engine with M0's interfaces: 75 of 77 document tests pass, the two failures are the reveal assertions that need the real engine); the schema generator, profiles/proposers and evaluation harness and their tests ran against the M0 plan's own surrogate, persona, checksum, structural, ensemble, gateway and verifier code (37 tests pass; corpus and report byte-identical across processes); the OCR client read a rendered page and both pages of the demo scan through the cached mock container word for word; the console (Tasks 16–18) type-checks and builds, and a route-intercepted Playwright run of the built console passed every console-check step at 1440 and 390 px in both themes (it caught and fixed a grid min-content overflow and a segmented-control overflow on phones). ruff and mypy are clean on every complete module in this plan.

## Open contract questions

Followed the contract in every case below; each is a concern or a gap to settle in the contract.

1. **§1 `extract.py` — "PDF text layer (`pypdf`)".** pypdf's text extraction has no reliable word positions, and boxes are the point of M1. M1 uses pypdf for structure (decryption check, crop/rotation normalisation, output verification) and pdfplumber (already in the `docs` extra) for positioned words. Suggest the contract row read "pypdf structure + pdfplumber words".
2. **§1 `redact.py` — "pdfplumber boxes + pypdf/reportlab rebuild"** and spec §6 "text removed, box drawn" with overlays. M1 rebuilds an **image-only** PDF (pypdfium2 raster, boxes burned in, reportlab writer): nothing of the source survives, but the output is not searchable. An optional invisible text layer of the *obfuscated* text would restore search; propose it as a later option, never the default.
3. **§1 `render.py` "pypdfium2 (no poppler)" vs the task brief's "pdftoppm/pypdfium2".** M1 rasterises with pypdfium2 only; `pdftoppm` is used solely as an independent test cross-check. PDFium is not thread-safe, so renders are serialised per process behind `PDFIUM_LOCK` — throughput scales with replicas, not threads; worth a line in §10/§11.
4. **§9 `/schema/synthesize {spec, rows, seed?} → {rows, synthesis_seed, report}`.** The task brief asks for "spec proposed by the LLM from a schema or de-identified sample, executed deterministically, spec returned as a reusable artefact". M1 keeps `/schema/generator-spec` as specified and **adds** to `/schema/synthesize`: `schema | ddl | sample` as alternatives to `spec`, `format: json|csv`, `name`, `locale`, `use_llm`, and `spec`, `spec_hash`, `csv` in the response. Additive; please fold into §9.
5. **§8.2 `DpsHttpError` is an M2 deliverable**, but M1 routes need stable codes now. M1 adds `dps.errors.CodedError` (framework-free) and `dps.layers.api.common.CodedHTTPException` with the same `{"detail","code"}` wire shape; M0 routes gain `code` (`bad_request`, `leakage`) additively. Recommend M2's `DpsHttpError` subclass `CodedHTTPException` (or replace it) rather than add a parallel class.
6. **§14 mockup 05 shows drafts, publish, version history, "jobs and API keys use it", "declared by".** No milestone before M5 stores tenant policies, and no §9 route exists for them. M1 renders shipped presets plus a browser-session draft usable inline (YAML download, "Use in Playground"); Publish is disabled. The M5 plan adds `043_policies.sql` and `dps/policy_store.py`; the contract should list those routes in §9 and assign the completion of `Policies.tsx` to M5 explicitly.
7. **Mockup 08's OCR switch "Azure Read v3.2 via org gateway / Local Tesseract".** Resolved 2026-10-11 (spec §6 requires the local fallback): Task 19C adds `TesseractOcrClient`, `ocr_provider` `tesseract`/`auto`, and `tesseract-ocr` in the app image (`DPS_OCR_PROVIDER=auto` by default there). The console still shows the configured engine (readiness component, now "Local Tesseract" when active) plus the `auto|always|never` OCR mode rather than a per-request engine switch.
8. **Mockup 05's "Deny list: exact values that never match"** contradicts Presidio's (and M0's) `deny_list`, which means *always flag*. M1 keeps `deny_list` semantics and adds `RecognizerSpec.allow_list` (never match) as a DPS extension, labelled in the console; the contract's custom-recognizer description should mention it (Presidio's YAML loader may reject the unknown key if these files are ever fed to Presidio directly).
9. **§14 routes are multi-segment** but M0 builds the console with `base: './'`, which only works one segment deep. M1 switches to `base: '/'`; hosting the console under a path prefix now needs a gateway rewrite — add to §11/HANDOFF.
10. **§1 row "`DocumentPipeline` façade (§7.4)"** — the contract has no §7.4; the façade's interface is defined in Task 9 of this plan.
11. **§2.4 audit `action` vocabulary lists `document.*`** but not schema routes. M1 records `mode` = `document.detect|obfuscate|synthesize`, `schema.generator-spec`, `schema.synthesize` in M0's `AuditEvent`; M2's `DbAuditSink` should accept the `schema.*` actions.
12. **§12.2 says "Playwright host tests use the cached browsers with `playwright==1.55.0`"**, but 1.55.0 expects Chromium build 1187, and this Mac caches 1148/1234/1243 only (verified: default launch fails, launch with the cached 1243 headless shell via `executable_path` works). `tools/console_check.py` falls back to the newest cached build; M4's host browser tests need the same fallback, `playwright install chromium` (network), or to run inside `mcr.microsoft.com/playwright:v1.55.0-noble`.
13. **`max_items` caps CSV documents** (rows × columns ≤ 10 000 by default, e.g. 1 000 rows × 10 columns). A separate `max_document_values` setting may be wanted for larger CSV uploads; M1 reuses `max_items` (posture-checked like the JSON limit).
14. **Detector defects surfaced by the evaluation harness (outside M1's boundary — M1 must not change engine semantics):** IPv4 addresses followed by a full stop are missed (M0 lookahead `(?![\d.])`), and a card number whose trailing 12 digits are Verhoeff-valid is reported as `AADHAAR` (both validated; the Aadhaar's 0.98 outranks the card's 0.95). Recommend an M0-module fix with tests in M2 (or a hotfix), followed by regenerating `detector-eval-m1.*` — the report test will demand it.
15. **The M1 settings beyond §10** (`ocr_health_path`, `ocr_probe_interval_seconds`, `ocr_max_concurrency`, `ocr_min_text_chars`, `ocr_image_coverage`, `redact_image_format`, `redact_jpeg_quality`, `redact_ocr_verify`, `preview_dpi`, `max_preview_pages`) and the `uitest` extra are additive; please list them in §10/§1. The org gateway's health path is `/_gw/health` (gate on `upstreams[].ok`, per the gateway's own handoff) — set `DPS_OCR_HEALTH_PATH=/_gw/health` in the OpenShift overlay (M5).
