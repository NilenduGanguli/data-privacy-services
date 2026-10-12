"""The local stack follows contract §11 and its lane-safe amendment (contract §0, 2026-10-12).

Offline: reads ``docker-compose.yml``, ``Dockerfile`` and ``.dockerignore``; the stack itself is
exercised by ``tools/smoke_test.py`` and the ``docs or ocr`` integration gate (contract §12.4).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
HOST_PORT = re.compile(r"^127\.0\.0\.1:\$\{DPS_[A-Z_]+_PORT:-\d+\}:\d+$")


def _services() -> dict[str, dict[str, Any]]:
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text("utf-8"))
    services: dict[str, dict[str, Any]] = compose["services"]
    return services


def test_every_host_port_is_loopback_and_lane_safe() -> None:
    for name, service in _services().items():
        for port in service.get("ports", []):
            assert HOST_PORT.match(port), (name, port)


def test_every_service_has_a_healthcheck_a_memory_limit_and_labels() -> None:
    for name, service in _services().items():
        assert service.get("healthcheck", {}).get("test"), name
        assert service.get("mem_limit"), name
        assert service.get("labels", {}).get("app") == "data-privacy-services", name


def test_the_ocr_mock_is_the_cached_image_under_the_docs_and_full_profiles() -> None:
    mock = _services()["azure-ocr-mock"]
    assert mock["image"] == "document_intelligence-azure-ocr-mock:${DPS_IMAGE_TAG:-latest}"
    assert mock["build"]["context"] == "../mock_azure_ocr"
    assert sorted(mock["profiles"]) == ["docs", "full"]
    assert mock["ports"] == ["127.0.0.1:${DPS_OCR_PORT:-5016}:5000"]
    assert "http://localhost:5000/health" in " ".join(mock["healthcheck"]["test"])


def test_the_app_reads_ocr_from_the_mock_and_starts_without_it() -> None:
    app = _services()["app"]
    assert "profiles" not in app  # the default stack
    assert app["image"] == "data-privacy-services:${DPS_IMAGE_TAG:-local}"
    assert app["ports"] == ["127.0.0.1:${DPS_APP_PORT:-8400}:8400"]
    env = app["environment"]
    assert env["DPS_OCR_PROVIDER"] == "${DPS_OCR_PROVIDER:-azure}"
    assert env["DPS_AZURE_VISION_ENDPOINT"] == (
        "${DPS_AZURE_VISION_ENDPOINT:-http://azure-ocr-mock:5000}"
    )
    assert env["DPS_AZURE_VISION_KEY"] == "${DPS_AZURE_VISION_KEY:-mock-key}"
    assert env["DPS_OCR_HEALTH_PATH"] == "${DPS_OCR_HEALTH_PATH:-/health}"
    assert env["DPS_MAX_DOCUMENT_MB"] == "${DPS_MAX_DOCUMENT_MB:-20}"
    assert env["DPS_SCHEMA_MAX_ROWS"] == "${DPS_SCHEMA_MAX_ROWS:-10000}"
    # Without --profile docs the app still starts and reports the ocr component down.
    assert app["depends_on"]["azure-ocr-mock"] == {
        "condition": "service_healthy",
        "required": False,
    }


def test_the_app_keeps_its_m0_hardening() -> None:
    app = _services()["app"]
    assert app["read_only"] is True
    assert app["tmpfs"] == ["/tmp:size=256m,mode=1777"]  # uploads over 1 MiB spool here
    assert app["cap_drop"] == ["ALL"]
    assert app["security_opt"] == ["no-new-privileges:true"]


def test_the_image_installs_the_docs_extra_and_builds_the_console_on_node_22() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text("utf-8")
    assert "FROM node:22-slim AS frontend" in dockerfile
    assert '-e ".[fpe,docs]"' in dockerfile
    assert '-e ".[fpe,docs,ner]"' in dockerfile


def test_the_build_context_leaves_out_demo_documents_and_tests() -> None:
    ignored = {
        line.strip()
        for line in (ROOT / ".dockerignore").read_text("utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    }
    assert {"demo", "tests", "docs"} <= ignored
    # The console's demo scan reaches the image through frontend/public, which is not ignored.
    assert (ROOT / "frontend" / "public" / "demo" / "kyc-scanned.pdf").is_file()
    assert not any(entry.startswith("frontend") and entry != "frontend/dist" for entry in ignored)
