"""Plain helpers for scanner tests (fixtures live in conftest.py)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx

from dps.config import Settings
from dps.engine.service import PrivacyEngine
from dps.engine.synth.surrogates import generate_synthesis_seed
from dps.engine.transform.crypto import generate_key_material
from dps.jobs_models import FillSpec, ScanScope, ScanSpec
from dps.layers.scanner.models import FieldClassification, FieldFacts
from dps.layers.scanner.runtime import ScanRuntime
from dps.layers.scanner.urls import CrawlScope, Origin, canonical

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


def make_runtime(
    base_url: str,
    settings: Settings,
    engine: PrivacyEngine,
    *,
    path: str = "/apply",
    deny: tuple[str, ...] = ("/logout", "/admin"),
    allow: tuple[str, ...] = (),
    cookies: bool = True,
    personas: int = 3,
    max_pages: int = 30,
    max_depth: int = 3,
    mode: str = "synthesize",
    spa: bool = True,
    screenshots: bool = True,
) -> ScanRuntime:
    """A scan of the demo site as the worker would open it (fresh key and seed, demo cookie)."""
    spec = ScanSpec.model_validate(
        {
            "url": base_url + path,
            "mode": mode,
            "scope": ScanScope(
                max_pages=max_pages,
                max_depth=max_depth,
                allow=list(allow),
                deny=list(deny),
                spa_clicks=spa,
            ).model_dump(),
            "fill": FillSpec(personas=personas, locale="en_IN").model_dump(),
            "capture": {"screenshots": screenshots},
        }
    )
    origin = Origin.of(spec.url)
    return ScanRuntime(
        spec=spec,
        start_url=canonical(spec.url) or spec.url,
        key=generate_key_material("key"),
        seed=generate_synthesis_seed(),
        storage_state=demo_cookie_state(origin.host) if cookies else None,
        policy=engine.policy(None),
        locale="en_IN",
        max_pages=max_pages,
        max_depth=max_depth,
        scope=CrawlScope(
            start=origin,
            same_origin=True,
            registered_hosts=frozenset(settings.scan_registered_hosts_list),
            allow=allow,
            deny=deny,
        ),
    )


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


# --- M4-G3 review r2: pages that render a detected value outside readable text nodes -----------
#: Made-up values standing for customer data (a name, two emails and a PAN-shaped code).
CENSUS_NAME = "Zebulon Quixotic"
CENSUS_EMAIL = "zebulon.quixotic@example.org"
CENSUS_EMAIL2 = "other.person.zq@example.org"
CENSUS_PAN = "AQXPZ4821K"
#: Boilerplate that pushes a value past the 2000th character of one text node.
CENSUS_FILLER = "Terms and conditions apply to every account. " * 48  # 2160 characters


def _table(*rows: tuple[str, str]) -> str:
    cells = "".join(f"<tr><th>{h}</th><td>{v}</td></tr>" for h, v in rows)
    return f"<table><tbody>{cells}</tbody></table>"


#: case -> HTML prepended to a demo page's body (``INJECT_JS``): a table the detectors find
#: through its row headers, plus a second copy of the value somewhere the r1 sweep did not look.
CENSUS_PAGES: dict[str, str] = {
    "button": _table(("Full name", CENSUS_NAME), ("Email", CENSUS_EMAIL))
    + f'<p><button id="c-b1" type="button">Continue as {CENSUS_NAME}</button></p>'
    + f'<p><button id="c-b2" type="button">Send statement to {CENSUS_EMAIL}</button></p>',
    "input_button": _table(("Full name", CENSUS_NAME))
    + f'<p><input id="c-in" type="button" value="Continue as {CENSUS_NAME}"></p>',
    "listbox": _table(("Full name", CENSUS_NAME))
    + '<p><select id="c-list" name="payee" size="3" style="width:600px"><option>Savings</option>'
    + f'<option id="c-opt">{CENSUS_NAME}</option><option>Current</option></select></p>',
    "digits": _table(("Email", CENSUS_EMAIL), ("PAN", CENSUS_PAN))
    + '<p id="c-digits" style="letter-spacing:4px">Your PAN: '
    + "".join(f"<span>{ch}</span>" for ch in CENSUS_PAN)
    + "</p>",
    "long": _table(("Email", CENSUS_EMAIL))
    + f'<p id="c-long" style="font-size:14px;width:1200px">{CENSUS_FILLER} '
    + f"Contact: {CENSUS_EMAIL2}</p>",
}
#: case -> the made-up values that page renders (OCR must read none of them where masked); the
#: first is the one rendered twice (except ``long``, whose second email is the one past 2000).
CENSUS_VALUES: dict[str, tuple[str, ...]] = {
    "button": (CENSUS_NAME, CENSUS_EMAIL),
    "input_button": (CENSUS_NAME,),
    "listbox": (CENSUS_NAME,),
    "digits": (CENSUS_PAN, CENSUS_EMAIL),
    "long": (CENSUS_EMAIL, CENSUS_EMAIL2),
}
INJECT_JS = """(html) => {
  const d = document.createElement('div');
  d.id = 'c-root';
  d.style.cssText = 'font-family:sans-serif;font-size:24px';
  d.innerHTML = html;
  document.body.prepend(d);
  return true;
}"""
