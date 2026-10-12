"""Shared fixtures: settings isolated from any .env on disk."""

from __future__ import annotations

import pytest

from dps.config import Settings
from tests.services import require_service


@pytest.fixture
def settings() -> Settings:
    """Deterministic local settings; never reads a .env file."""
    return Settings(_env_file=None, env="local", auth_enabled=False, llm_provider="stub")


@pytest.fixture(scope="session")
def ocr_url() -> str:
    """The Read v3.2 mock (compose profile ``docs``); skips when it is not running."""
    return require_service("ocr")
