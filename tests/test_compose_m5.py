"""The local stack's M5 settings and its ``full`` profile (M5 Task 24, contract §11).

Offline: reads ``docker-compose.yml`` and asks git which local paths are ignored. The running stack is
checked by ``tools/smoke_test.py`` (the M5 block) and ``tools/flow_report.py`` against
``docker compose --profile full up --build -d``.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from dps.config import Settings

ROOT = Path(__file__).resolve().parents[1]
#: M5 Task 24: the copula engine on, DB-backed keys propagate within 5 s (the local demo creates a
#: key and uses it at once), evidence PDFs on. Each stays overridable from the shell.
M5_ENV = {
    "DPS_SYNTH_TABULAR_ENGINE": "${DPS_SYNTH_TABULAR_ENGINE:-copula}",
    "DPS_KEY_CACHE_SECONDS": "${DPS_KEY_CACHE_SECONDS:-5}",
    "DPS_REPORT_PDF_ENABLED": "${DPS_REPORT_PDF_ENABLED:-true}",
}
DEFAULT = re.compile(r"^\$\{(?P<name>[A-Z0-9_]+):-(?P<default>[^}]*)\}$")


def _compose() -> dict[str, Any]:
    compose: dict[str, Any] = yaml.safe_load((ROOT / "docker-compose.yml").read_text("utf-8"))
    return compose


def _services() -> dict[str, dict[str, Any]]:
    services: dict[str, dict[str, Any]] = _compose()["services"]
    return services


def test_app_and_worker_carry_the_m5_settings() -> None:
    services = _services()
    for name in ("app", "worker", "migrate"):  # every role of the app image (x-dps-env)
        env = services[name]["environment"]
        for key, value in M5_ENV.items():
            assert env.get(key) == value, (name, key)


def test_the_m5_defaults_are_valid_settings() -> None:
    """The values compose passes when the shell sets nothing parse as the settings they name."""
    defaults = {}
    for key, value in M5_ENV.items():
        match = DEFAULT.match(value)
        assert match and match["name"] == key, value
        defaults[key.removeprefix("DPS_").lower()] = match["default"]
    s = Settings(_env_file=None, **defaults)
    assert s.synth_tabular_engine == "copula"
    assert s.key_cache_seconds == 5
    assert s.report_pdf_enabled is True


def test_the_full_profile_is_every_service() -> None:
    """Contract §11: ``full`` = all of the above — every service is either in the default stack
    (app, dps-db, migrate, worker) or names ``full`` among its profiles (M4's browser-worker and
    demo-site slot in the same way)."""
    services = _services()
    default = {name for name, s in services.items() if not s.get("profiles")}
    assert default == {"app", "dps-db", "migrate", "worker"}
    for name, service in services.items():
        if service.get("profiles"):
            assert "full" in service["profiles"], name
    assert {
        "tokenizer-mock",
        "azure-ocr-mock",
        "source-pg",
        "minio",
        "oracle",
        "mongo",
        "browser-worker",  # M4, merged: `full` runs the scanner and its demo site
        "demo-site",
    } <= set(services)


def test_every_role_of_the_app_image_runs_in_full() -> None:
    """The four roles of spec §4 that compose runs from the app image (app, worker, migrate; the
    browser-worker has its own image) plus the one-shots and the tokenizer mock reuse the image
    the ``app`` service builds, never pulling it."""
    services = _services()
    image = "data-privacy-services:${DPS_IMAGE_TAG:-local}"
    users = {name for name, s in services.items() if s.get("image") == image}
    assert {"app", "worker", "migrate", "tokenizer-mock", "demo-secrets", "seed-sources"} <= users
    for name in users - {"app"}:
        assert services[name].get("pull_policy") == "never", name
        assert "build" not in services[name], name
    assert services["app"]["build"] == "."


def test_the_app_waits_for_the_tokenizer_mock_when_it_runs() -> None:
    """``/readyz`` probes the tokenizer once at startup: under ``full`` the app starts after the
    mock is healthy (so the external tokenization checks run); without the profile it still
    starts and reports the tokenizer down."""
    app = _services()["app"]
    assert app["depends_on"]["tokenizer-mock"] == {
        "condition": "service_healthy",
        "required": False,
    }


def test_a_browser_worker_inherits_the_worker_settings() -> None:
    """M4's ``browser-worker`` extends ``worker`` (or carries the shared settings itself)."""
    services = _services()
    worker = services["browser-worker"]
    inherits = (worker.get("extends") or {}).get("service") == "worker"
    env = worker.get("environment") or {}
    assert inherits or all(env.get(k) == v for k, v in M5_ENV.items())
    assert "full" in worker["profiles"]
    # The Playwright base ships /ms-playwright mode 0777: only a read-only root keeps the browsers
    # unwritable (tests/test_images.py).
    assert worker.get("read_only") is True or (inherits and services["worker"]["read_only"] is True)


def _ignored(path: str) -> bool:
    out = subprocess.run(
        ["git", "-C", str(ROOT), "check-ignore", "-q", "--no-index", path],
        capture_output=True,
        timeout=10,
    )
    return out.returncode == 0


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_local_demo_outputs_never_get_committed() -> None:
    """``var/`` holds demo keys and group keys, ``reports/`` the flow and demo reports and the
    console screenshots; the package ``dps/layers/reports`` stays tracked."""
    inside = subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "--is-inside-work-tree"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if inside.stdout.strip() != "true":
        pytest.skip("not a git work tree")
    for path in ("var/demo/demo-keys.json", "reports/flow-report.md", "reports/demo-screens/a.png"):
        assert _ignored(path), path
    for path in ("dps/layers/reports/new_module.py", "tools/flow_report.py", "docs/demo/x.md"):
        assert not _ignored(path), path


def test_developer_tooling_never_reaches_an_image() -> None:
    """``tools/dev`` holds the owner's push automation and a macOS LaunchAgent with host paths;
    the image's ``COPY tools`` must not carry them (review S4 r2, R2-2)."""
    ignored = {
        line.strip()
        for line in (ROOT / ".dockerignore").read_text("utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    }
    assert "tools/dev" in ignored


def test_local_demo_outputs_never_reach_a_build_context() -> None:
    """Demo keys (``var/``) and the tool reports and screenshots (``reports/``) stay on the host."""
    ignored = {
        line.strip()
        for line in (ROOT / ".dockerignore").read_text("utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    }
    assert {"var", "reports", ".env", ".env.*"} <= ignored
