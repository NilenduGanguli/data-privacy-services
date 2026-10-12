"""Plain helpers for scanner tests (fixtures live in conftest.py)."""

from __future__ import annotations

from typing import Any

import httpx


def received(url: str) -> dict[str, Any]:
    """The demo site's counters of everything non-GET (and every GET-form submission) it received."""
    data: dict[str, Any] = httpx.get(f"{url}/__received", timeout=5.0).json()
    return data
