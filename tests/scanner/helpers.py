"""Plain helpers for scanner tests (fixtures live in conftest.py)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx

from dps.config import Settings
from dps.layers.scanner.models import FieldClassification, FieldFacts
from dps.layers.scanner.urls import CrawlScope, Origin

#: data.json keys whose values the demo site renders (customer data for the leak tests).
DEMO_VALUE_KEYS = ("full_name", "aadhaar", "pan", "card_number", "email", "mobile", "address")


def received(url: str) -> dict[str, Any]:
    """The demo site's counters of everything non-GET (and every GET-form submission) it received."""
    data: dict[str, Any] = httpx.get(f"{url}/__received", timeout=5.0).json()
    return data


_FIELD_BASE: dict[str, Any] = dict(
    key="0:1",
    field_no=1,
    form_index=1,
    frame_index=0,
    in_shadow=False,
    tag="input",
    input_type="text",
    name=None,
    id=None,
    label="",
    label_source="none",
    placeholder=None,
    title=None,
    aria_label=None,
    autocomplete=None,
    pattern=None,
    maxlength=None,
    minlength=None,
    required=False,
    readonly=False,
    disabled=False,
    visible=True,
    inputmode=None,
    selector="input",
)


def field_facts(**kw: Any) -> FieldFacts:
    """A visible text input with no attributes, overridden by ``kw``."""
    return FieldFacts.model_validate(_FIELD_BASE | kw)


def classification(entity: str | None, sensitivity: str | None) -> FieldClassification:
    """A classification literal (``confidence=0.9``, detector ``label``) or an unclassified one."""
    if entity is None:
        return FieldClassification(
            entity_type=None, sensitivity=None, confidence=0.0, detectors=[], evidence=[]
        )
    return FieldClassification(
        entity_type=entity,
        sensitivity=sensitivity,
        confidence=0.9,
        detectors=["label"],
        evidence=[],
    )


def demo_cookie_state(host: str) -> dict[str, Any]:
    """The demo site's session cookie as a Playwright storage state."""
    return {
        "cookies": [
            {
                "name": "demo_session",
                "value": "demo-valid",
                "domain": host,
                "path": "/",
                "expires": -1,
                "httpOnly": True,
                "secure": False,
                "sameSite": "Lax",
            }
        ],
        "origins": [],
    }


@asynccontextmanager
async def guarded_context(
    browser: Any,
    settings: Settings,
    base_url: str,
    *,
    harden_js: bool = True,
    cookies: bool = False,
) -> AsyncIterator[tuple[Any, Any]]:
    """A scan-like context: same browser options, the scope proxy and the real guard, installed
    before any page; closed through ``close_scan_context`` (every page via ``guard.close``)."""
    from dps.layers.scanner.browser import close_scan_context, new_scan_context
    from dps.layers.scanner.guard import ScanGuard

    origin = Origin.of(base_url)
    scope = CrawlScope(
        start=origin,
        same_origin=True,
        registered_hosts=frozenset(settings.scan_registered_hosts_list),
    )
    guard = ScanGuard(host_allowed=scope.network_host_allowed)
    ctx = await new_scan_context(
        browser,
        settings,
        storage_state=demo_cookie_state(origin.host) if cookies else None,
        locale="en_IN",
        guard=guard,
        harden_js=harden_js,
    )
    try:
        yield ctx, guard
    finally:
        await close_scan_context(ctx, guard)
