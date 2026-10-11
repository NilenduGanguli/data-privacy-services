"""Finding test containers by env var; skip (or fail under DPS_TEST_REQUIRE_SERVICES=1) when down.

Endpoints come only from the ``DPS_TEST_*`` variables (contract §12.2). When one is unset, the
default is built from the same ``DPS_*_PORT`` variable compose publishes the service on (contract
§0, amendment 2026-10-12), so a lane that exports ``DPS_DB_PORT=5451`` moves compose and the tests
together. No test, fixture or tool hard-codes a host port.
"""

from __future__ import annotations

import os
import socket
from urllib.parse import urlparse

import pytest

#: name -> (env var, default template, (port variable, contract port), compose profile).
#: ``{port}`` in the template is the port variable's value, else the contract port.
SERVICES: dict[str, tuple[str, str, tuple[str, int], str]] = {
    "dps-db": (
        "DPS_TEST_PG_DSN",
        "postgresql://postgres:postgres@127.0.0.1:{port}/postgres",
        ("DPS_DB_PORT", 5441),
        "",
    ),
    "source-pg": (
        "DPS_TEST_SOURCE_PG_DSN",
        "postgresql://postgres:postgres@127.0.0.1:{port}/bank",
        ("DPS_SOURCE_PG_PORT", 5442),
        "sources",
    ),
    "minio": (
        "DPS_TEST_S3_ENDPOINT",
        "http://127.0.0.1:{port}",
        ("DPS_MINIO_PORT", 9010),
        "sources",
    ),
    "app": ("DPS_TEST_APP_URL", "http://127.0.0.1:{port}", ("DPS_APP_PORT", 8400), ""),
    "azure-ocr-mock": (
        "DPS_TEST_OCR_URL",
        "http://127.0.0.1:{port}",
        ("DPS_OCR_PORT", 5016),
        "docs",
    ),
    "demo-site": (
        "DPS_TEST_DEMO_SITE",
        "http://127.0.0.1:{port}",
        ("DPS_DEMO_SITE_PORT", 8410),
        "scanner",
    ),
}


def service_url(name: str) -> str:
    """The URL/DSN of a test service (``DPS_TEST_*`` override, else the compose host port)."""
    env, template, (port_var, contract_port), _ = SERVICES[name]
    explicit = os.environ.get(env)
    if explicit:
        return explicit
    return template.format(port=os.environ.get(port_var) or contract_port)


def _reachable(url: str) -> bool:
    parsed = urlparse(url)
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((parsed.hostname or "127.0.0.1", port), timeout=2.0):
            return True
    except OSError:
        return False


def require_service(name: str) -> str:
    """Return the service URL, or skip/fail with the command that starts it.

    The message names the service and the start command only — never the URL, which may carry
    a password.
    """
    url = service_url(name)
    if _reachable(url):
        return url
    profile = SERVICES[name][3]
    start = f"docker compose {'--profile ' + profile + ' ' if profile else ''}up -d {name}"
    message = f"{name} unreachable — start: {start} (or tools/scratch_pg.sh start)"
    if os.environ.get("DPS_TEST_REQUIRE_SERVICES") == "1":
        pytest.fail(message)
    pytest.skip(message)
