"""Scanner fixtures: the demo site (auto-started with the host's node), Chromium, engines.

The demo site's URL comes from ``DPS_TEST_DEMO_SITE``, else from the lane's
``DPS_DEMO_SITE_PORT`` (contract §0 amendment 2026-10-12, §12.2) — never a literal port.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import time
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
import pytest

from dps.config import Settings
from dps.engine.service import PrivacyEngine
from tests.services import service_url

ROOT = Path(__file__).resolve().parents[2]
DEMO = ROOT / "demo" / "onboarding"


def _require() -> bool:
    return os.environ.get("DPS_TEST_REQUIRE_SERVICES") == "1"


def skip_or_fail(hint: str) -> None:
    """Skip with ``hint`` — or fail under ``DPS_TEST_REQUIRE_SERVICES=1`` (milestone gates)."""
    if _require():
        pytest.fail(f"required service unavailable — {hint}")
    pytest.skip(hint)


def _up(url: str) -> bool:
    try:
        return httpx.get(f"{url}/health", timeout=1.0).status_code == 200
    except httpx.HTTPError:
        return False


def _port_taken(port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


@pytest.fixture(scope="session")
def demo_site_url() -> Iterator[str]:
    """The demo site: the running one, or ``node demo/onboarding/server.mjs`` on its port."""
    url = service_url("demo_site").rstrip("/")
    if _up(url):
        yield url
        return
    node = shutil.which("node")
    parts = urlsplit(url)
    port = parts.port or 80
    if node is None or parts.hostname not in ("127.0.0.1", "localhost") or _port_taken(port):
        skip_or_fail(
            "start: docker compose --profile scanner up -d demo-site "
            "(or: node demo/onboarding/server.mjs --port $DPS_DEMO_SITE_PORT)"
        )
    proc = subprocess.Popen(
        [node, str(DEMO / "server.mjs"), "--port", str(port), "--host", "127.0.0.1"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(50):
            if _up(url):
                break
            time.sleep(0.2)
        else:
            skip_or_fail("demo site did not come up")
        yield url
    finally:
        proc.terminate()
        proc.wait(timeout=5)


@pytest.fixture(scope="session")
def demo_data() -> dict[str, str]:
    """The demo site's synthetic applicant (treated as customer data by the tests)."""
    data: dict[str, str] = json.loads((DEMO / "data.json").read_text(encoding="utf-8"))
    return data


@pytest.fixture
async def chromium() -> AsyncIterator[Any]:
    """A headless Chromium for one test, launched with the scanner's ``LAUNCH_ARGS`` (the request
    guard depends on them: no SharedWorker, WebRTC kept off unproxied UDP)."""
    pw = pytest.importorskip("playwright.async_api")
    from dps.layers.scanner.browser import LAUNCH_ARGS

    async with pw.async_playwright() as p:
        try:
            browser = await p.chromium.launch(headless=True, args=list(LAUNCH_ARGS))
        except pw.Error:
            skip_or_fail("run: python -m playwright install chromium (playwright==1.55.0 → 1187)")
        try:
            yield browser
        finally:
            await browser.close()


@pytest.fixture
def scan_settings(settings: Settings) -> Settings:
    """Local settings with short waits for browser tests."""
    return settings.model_copy(
        update={
            "scan_settle_ms": 150,
            "scan_page_timeout_seconds": 15,
            "scan_registered_hosts": "127.0.0.1,localhost,demo-site",
        }
    )


class RotCipher:
    """Deterministic FF1 stand-in: reverse, then rotate every character by 3.

    A plain rotation (the M0 test cipher) is degenerate under M0's leading-zero cycle walking:
    the first digit returns to ``0`` after exactly ten rounds, when every other digit has turned
    full circle too, so ``CUST0048213`` "encrypts" to itself (the capture's leakage check then
    rightly withholds it). Reversing first breaks that alignment."""

    def encrypt(self, alphabet: str, plaintext: str) -> str:
        return "".join(alphabet[(alphabet.index(c) + 3) % len(alphabet)] for c in plaintext[::-1])

    def decrypt(self, alphabet: str, ciphertext: str) -> str:
        return "".join(alphabet[(alphabet.index(c) - 3) % len(alphabet)] for c in ciphertext)[::-1]


@pytest.fixture
def scan_engine(scan_settings: Settings) -> PrivacyEngine:
    """FPE on with the deterministic test cipher."""
    return PrivacyEngine(
        scan_settings.model_copy(update={"fpe_enabled": True}),
        cipher_factory=lambda key, tweak: RotCipher(),
    )


@pytest.fixture
def det_engine(scan_settings: Settings) -> PrivacyEngine:
    """FPE off: every reversible method falls back to det_token (base32)."""
    return PrivacyEngine(scan_settings.model_copy(update={"fpe_enabled": False}))
