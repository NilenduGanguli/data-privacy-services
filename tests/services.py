"""Where the integration containers are (contract §12.2).

An explicit ``DPS_TEST_*`` variable wins. Otherwise the default is built from the same ``DPS_*_PORT``
variable compose publishes the service on (the contract port when it is unset), so a lane that
exports ``DPS_OCR_PORT=5017`` moves compose and the tests together (contract §0, amendment
2026-10-12). No test, fixture or tool hard-codes a host port.
"""

from __future__ import annotations

import os
import socket
from typing import NamedTuple
from urllib.parse import urlsplit

import pytest


class Service(NamedTuple):
    env: str  # explicit endpoint, e.g. DPS_TEST_OCR_URL
    template: str  # the default, with {port} for the host port
    port_env: str  # the compose host-port variable, e.g. DPS_OCR_PORT
    port: int  # the contract port, used when port_env is unset
    profile: str  # compose profile ("" = default)
    service: str  # compose service


SERVICES: dict[str, Service] = {
    "app": Service("DPS_TEST_APP_URL", "http://127.0.0.1:{port}", "DPS_APP_PORT", 8400, "", "app"),
    "ocr": Service(
        "DPS_TEST_OCR_URL",
        "http://127.0.0.1:{port}",
        "DPS_OCR_PORT",
        5016,
        "docs",
        "azure-ocr-mock",
    ),
    "pg": Service(
        "DPS_TEST_PG_DSN",
        "postgresql://postgres:postgres@127.0.0.1:{port}/postgres",
        "DPS_DB_PORT",
        5441,
        "",
        "dps-db",
    ),
    "source_pg": Service(
        "DPS_TEST_SOURCE_PG_DSN",
        "postgresql://postgres:postgres@127.0.0.1:{port}/bank",
        "DPS_SOURCE_PG_PORT",
        5442,
        "sources",
        "source-pg",
    ),
    "oracle": Service(
        "DPS_TEST_ORACLE_DSN",
        "127.0.0.1:{port}/FREEPDB1",
        "DPS_ORACLE_PORT",
        1522,
        "oracle",
        "oracle",
    ),
    "mongo": Service(
        "DPS_TEST_MONGO_URI",
        "mongodb://127.0.0.1:{port}/?directConnection=true",
        "DPS_MONGO_PORT",
        27018,
        "sources",
        "mongo",
    ),
    "s3": Service(
        "DPS_TEST_S3_ENDPOINT",
        "http://127.0.0.1:{port}",
        "DPS_MINIO_PORT",
        9010,
        "sources",
        "minio",
    ),
    "demo_site": Service(
        "DPS_TEST_DEMO_SITE",
        "http://127.0.0.1:{port}",
        "DPS_DEMO_SITE_PORT",
        8410,
        "scanner",
        "demo-site",
    ),
}
_DEFAULT_PORTS = {"http": 80, "https": 443, "postgresql": 5432, "postgres": 5432, "mongodb": 27017}


def service_url(name: str) -> str:
    """The URL/DSN for ``name``: its ``DPS_TEST_*`` variable, else the compose default on the host
    port compose uses (``DPS_*_PORT``, the contract port when unset)."""
    svc = SERVICES[name]
    explicit = os.environ.get(svc.env)
    if explicit:
        return explicit
    return svc.template.format(port=os.environ.get(svc.port_env) or svc.port)


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
    svc = SERVICES[name]
    profile = f"--profile {svc.profile} " if svc.profile else ""
    hint = f"start: docker compose {profile}up -d {svc.service}"
    if os.environ.get("DPS_TEST_REQUIRE_SERVICES") == "1":
        pytest.fail(f"{name} unreachable — {hint}")
    pytest.skip(hint)
