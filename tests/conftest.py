"""Shared fixtures: settings isolated from any .env on disk."""

from __future__ import annotations

import pytest

from dps.config import Settings


@pytest.fixture
def settings() -> Settings:
    """Deterministic local settings; never reads a .env file."""
    return Settings(_env_file=None, env="local", auth_enabled=False, llm_provider="stub")
