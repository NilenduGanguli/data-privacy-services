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


#: The one-shots of contract §11 (M2): they run once, exit 0 and are waited for with
#: ``condition: service_completed_successfully`` — their exit code is their health check.
ONE_SHOTS = {"migrate", "minio-init", "demo-secrets", "seed-sources"}


def test_every_service_has_a_healthcheck_a_memory_limit_and_labels() -> None:
    services = _services()
    # A one-shot is a service that never restarts; each one is named here, so a new one is a
    # deliberate change (and keeps every long-running service under the healthcheck rule).
    assert {name for name, s in services.items() if s.get("restart") == "no"} == ONE_SHOTS
    for name, service in services.items():
        assert service.get("mem_limit"), name
        assert service.get("labels", {}).get("app") == "data-privacy-services", name
        if name in ONE_SHOTS:
            assert "ports" not in service and not service.get("healthcheck"), name
        else:
            assert service.get("healthcheck", {}).get("test"), name


def test_one_shots_are_awaited_until_they_exit_0() -> None:
    services = _services()
    for name, service in services.items():
        for dep, rule in (service.get("depends_on") or {}).items():
            if dep in ONE_SHOTS:
                assert rule["condition"] == "service_completed_successfully", (name, dep)
    # app and worker only verify migrations (contract §11.1): both wait for the migrate one-shot.
    for name in ("app", "worker"):
        assert services[name]["depends_on"]["migrate"]["condition"] == (
            "service_completed_successfully"
        )


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


def _installed_extras(dockerfile: str) -> list[set[str]]:
    """The extras of every ``uv pip install … -e ".[…]"`` line of the Dockerfile."""
    return [set(m.split(",")) for m in re.findall(r'-e "\.\[([a-z_,-]+)\]"', dockerfile)]


def test_the_image_installs_the_docs_extra_and_builds_the_console_on_node_22() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text("utf-8")
    assert "FROM node:22-slim AS frontend" in dockerfile
    installs = _installed_extras(dockerfile)
    # Both branches of the WITH_NER switch: M1's documents (contract §11.1) and M2's job
    # platform (app, worker and migrate run this one image); [ner] only with WITH_NER=1.
    assert len(installs) == 2
    for extras in installs:
        assert {"fpe", "docs", "db", "connectors"} <= extras, extras
    assert sum("ner" in extras for extras in installs) == 1


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


# ---------------------------------------------------------------- M4: browser-worker and demo site


def test_the_browser_worker_extends_the_worker_under_the_scanner_profile() -> None:
    """Contract §11/§11.1 (M4): its own image, the worker's DB, KEK, artefact store and volumes
    (``extends``), scans only, ops endpoints on 9401 inside the network (no host port)."""
    services = _services()
    bw = services["browser-worker"]
    assert bw["extends"] == {"service": "worker"}
    assert bw["build"] == {"context": ".", "dockerfile": "Dockerfile.browser"}
    assert bw["image"] == "data-privacy-services-browser:${DPS_IMAGE_TAG:-local}"
    assert bw["command"] == ["python", "-m", "dps.browser_worker"]
    assert sorted(bw["profiles"]) == ["full", "scanner"]
    assert "ports" not in bw and "ports" not in services["worker"]
    assert bw["depends_on"]["migrate"]["condition"] == "service_completed_successfully"
    assert bw["depends_on"]["dps-db"]["condition"] == "service_healthy"
    env = bw["environment"]
    assert env["DPS_BROWSER_METRICS_PORT"] == "9401" and env["DPS_BROWSER_CONCURRENCY"] == "2"
    assert env["DPS_MIGRATIONS_MODE"] == "off"  # the migrate one-shot owns DDL
    assert set(env["DPS_SCAN_REGISTERED_HOSTS"].split(",")) == {
        "localhost",
        "127.0.0.1",
        "demo-site",
    }
    # the local storage-state references: the repo's docker/secrets, read-only
    assert env["DPS_SECRET_DIR"] == "/var/run/secrets/dps-local"
    assert "./docker/secrets:/var/run/secrets/dps-local:ro" in bw["volumes"]
    assert bw["shm_size"] and bw["init"] is True and bw["mem_limit"] == "2g"
    assert "http://localhost:9401/health" in " ".join(bw["healthcheck"]["test"])
    assert bw["labels"]["component"] == "browser-worker"
    # what it inherits: the worker's hardening and the shared KEK / artefact store
    worker = services["worker"]
    assert worker["read_only"] is True and worker["cap_drop"] == ["ALL"]
    assert "DPS_JOB_KEK" in worker["environment"] and "DPS_ARTEFACT_STORE" in worker["environment"]
    assert "dps-artefacts:/app/var/artefacts" in worker["volumes"]


def test_the_demo_site_runs_the_onboarding_app_on_a_lane_safe_port() -> None:
    site = _services()["demo-site"]
    assert site["image"] == "node:22-slim"
    assert sorted(site["profiles"]) == ["full", "scanner"]
    assert site["command"] == [
        "node",
        "/srv/demo/server.mjs",
        "--host",
        "0.0.0.0",
        "--port",
        "8410",
    ]
    assert site["volumes"] == ["./demo/onboarding:/srv/demo:ro"]
    assert site["ports"] == ["127.0.0.1:${DPS_DEMO_SITE_PORT:-8410}:8410"]
    assert "http://127.0.0.1:8410/health" in " ".join(site["healthcheck"]["test"])
    assert site["labels"]["component"] == "demo-site"


def test_the_browser_image_is_the_playwright_base_with_the_scanner_extra() -> None:
    """Contract §11.1: Chromium 1187 baked in the base matches playwright==1.55.0 (never a
    ``playwright install``), uv pinned, non-root uid 10001 (the owner of the shared artefact and
    export volumes), the scanner extra without docs or tabular, health on 9401."""
    dockerfile = "\n".join(
        line
        for line in (ROOT / "Dockerfile.browser").read_text("utf-8").splitlines()
        if not line.lstrip().startswith("#")
    )
    assert dockerfile.count("FROM ") == 1
    assert "FROM mcr.microsoft.com/playwright:v1.55.0-noble" in dockerfile
    assert "playwright install" not in dockerfile
    assert re.search(r"COPY --from=ghcr\.io/astral-sh/uv:\d+\.\d+\.\d+ ", dockerfile)
    installs = [set(m.split(",")) for m in re.findall(r'"\.\[([a-z_,-]+)\]"', dockerfile)]
    assert len(installs) == 2
    for extras in installs:
        assert {"fpe", "db", "connectors", "scanner"} <= extras, extras
        assert not extras & {"docs", "tabular", "dev", "uitest", "e2e"}, extras
    assert sum("ner" in extras for extras in installs) == 1
    assert "m.version('playwright') == '1.55.0'" in dockerfile
    assert "PLAYWRIGHT_BROWSERS_PATH=/ms-playwright" in dockerfile
    assert "USER 10001" in dockerfile
    assert "http://localhost:9401/health" in dockerfile
    assert 'CMD ["python", "-m", "dps.browser_worker"]' in dockerfile
    # the extra pins the same Playwright as the base image
    pyproject = (ROOT / "pyproject.toml").read_text("utf-8")
    assert re.search(r'^scanner = \["playwright==1\.55\.0", "pillow>=10\.4"\]', pyproject, re.M)


def test_the_demo_storage_state_is_a_tenant_scoped_local_literal() -> None:
    """``storage_state_ref`` ``dps/ui/demo-onboarding`` of the default tenant (SEC-2 layout
    ``DPS_SECRET_DIR/<tenant>/<ref>/``): the demo site's session cookie for host ``demo-site``."""
    import json

    path = ROOT / "docker/secrets/default/dps/ui/demo-onboarding/storage_state.json"
    state = json.loads(path.read_text("utf-8"))
    assert state["origins"] == []
    (cookie,) = state["cookies"]
    assert (cookie["name"], cookie["domain"], cookie["path"]) == ("demo_session", "demo-site", "/")
    assert cookie["value"] == "demo-valid"  # the demo site's documented local literal
    # compose bind-mounts it; it never enters an image's build context
    ignored = (ROOT / ".dockerignore").read_text("utf-8").splitlines()
    assert "docker/secrets" in ignored


def test_demo_secrets_are_written_under_the_demo_tenant() -> None:
    """Secret refs are tenant-scoped (SEC-2): the demo files live under the bootstrap key's
    tenant, and the pre-scoping top-level ``dps`` folder of an older volume is removed (it would
    be refused as another tenant's folder)."""
    from dps.auth import DEFAULT_TENANT

    script = " ".join(_services()["demo-secrets"]["command"])
    assert f"d=/var/run/secrets/dps/{DEFAULT_TENANT}/dps;" in script
    assert "rm -rf /var/run/secrets/dps/dps;" in script
    for ref in ("src/bank-pg-ro", "tgt/bank-uat", "src/bank-restore-rw", "s3/exports"):
        assert ref in script
