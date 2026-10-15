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


#: A component whose host is ``display:contents`` (declarative shadow root, so no script runs).
_CHIP = (
    '<template shadowrootmode="open"><style>:host{display:contents}</style>'
    "<b><slot></slot></b></template>"
)
#: An image or image button big enough for its alt text (the image itself does not exist).
_BOX = 'style="width:600px;height:40px;font-size:24px"'

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
    # M4 verify r1 B4: text whose parent element has no box of its own (display:contents), a
    # slotted text in a component whose host is display:contents, and a slot's fallback text
    "contents": _table(("Full name", CENSUS_NAME), ("Email", CENSUS_EMAIL))
    + f'<p>Welcome back, <span id="c-ct" style="display:contents">{CENSUS_NAME}</span></p>'
    + f'<div id="c-cd" style="display:contents">{CENSUS_EMAIL}</div>',
    "slotted": _table(("Full name", CENSUS_NAME), ("Email", CENSUS_EMAIL))
    + f'<p>Customer: <user-chip id="c-sl1">{_CHIP}{CENSUS_NAME}</user-chip></p>'
    + f'<p>Email on file: <user-chip id="c-sl2">{_CHIP}{CENSUS_EMAIL}</user-chip></p>',
    "slot_fallback": _table(("Full name", CENSUS_NAME))
    + '<p>Statement for <x-fallback id="c-fb"><template shadowrootmode="open">'
    + f"<b><slot>{CENSUS_NAME}</slot></b></template></x-fallback></p>",
    # the only copy in such a place: the extraction itself must read it to classify it
    "contents_only": f'<div id="c-co" style="display:contents">Mail: {CENSUS_EMAIL}</div>',
    "slotted_only": f'<p>Email on file: <user-chip id="c-so">{_CHIP}{CENSUS_EMAIL}</user-chip></p>',
    # M4 verify r1 B5: strings the browser draws from attributes
    "alt": _table(("Full name", CENSUS_NAME))
    + f'<p><img id="c-alt" src="/no-such-avatar.png" alt="{CENSUS_NAME}" {_BOX}></p>',
    "alt_only": f'<p><img id="c-ao" src="/no-such-card.png" alt="Card for {CENSUS_EMAIL}" {_BOX}></p>',
    "input_image": _table(("Full name", CENSUS_NAME))
    + f'<p><input id="c-ii" type="image" src="/no-such-go.png" alt="{CENSUS_NAME}" {_BOX}></p>',
    "optgroup": _table(("Full name", CENSUS_NAME))
    + '<p><select id="c-ogs" name="acct" size="4" style="width:600px">'
    + f'<optgroup id="c-og" label="{CENSUS_NAME}"><option>Savings</option>'
    + "<option>Current</option></optgroup></select></p>",
    "option_label": _table(("Full name", CENSUS_NAME))
    + '<p><select id="c-ols" name="payee" size="3" style="width:600px"><option>Savings</option>'
    + f'<option id="c-ol" label="{CENSUS_NAME}">payee-2</option></select></p>',
    "placeholder": _table(("Email", CENSUS_EMAIL))
    + f'<p><input id="c-ph" name="alt" style="width:600px" placeholder="{CENSUS_EMAIL}"></p>',
}
#: case -> the made-up values that page renders (OCR must read none of them where masked); the
#: first is the one rendered twice (except ``long``, whose second email is the one past 2000).
CENSUS_VALUES: dict[str, tuple[str, ...]] = {
    "button": (CENSUS_NAME, CENSUS_EMAIL),
    "input_button": (CENSUS_NAME,),
    "listbox": (CENSUS_NAME,),
    "digits": (CENSUS_PAN, CENSUS_EMAIL),
    "long": (CENSUS_EMAIL, CENSUS_EMAIL2),
    "contents": (CENSUS_NAME, CENSUS_EMAIL),
    "slotted": (CENSUS_NAME, CENSUS_EMAIL),
    "slot_fallback": (CENSUS_NAME,),
    "contents_only": (CENSUS_EMAIL,),
    "slotted_only": (CENSUS_EMAIL,),
    "alt": (CENSUS_NAME,),
    "alt_only": (CENSUS_EMAIL,),
    "input_image": (CENSUS_NAME,),
    "optgroup": (CENSUS_NAME,),
    "option_label": (CENSUS_NAME,),
    "placeholder": (CENSUS_EMAIL,),
}
#: cases whose second copy is a text node the extraction reads but no detector flags (a name with
#: no label next to it): an obfuscated capture rewrites only the flagged copy, finds the original
#: still in the text it read and is withheld (``leakage``) rather than shown
CENSUS_OBFUSCATED_WITHHELD = frozenset({"contents", "slotted", "slot_fallback"})
#: cases that render each value once (the others render the first value twice)
CENSUS_SINGLE_COPY = frozenset({"long", "contents_only", "slotted_only", "alt_only"})
#: Inserts the HTML (declarative shadow roots attached; no script runs) and waits until every
#: image and image button in it has loaded or failed (a failed one shows its alt text).
INJECT_JS = """async (html) => {
  const d = document.createElement('div');
  d.id = 'c-root';
  d.style.cssText = 'font-family:sans-serif;font-size:24px';
  d.setHTMLUnsafe(html);
  document.body.prepend(d);
  const pending = Array.from(d.querySelectorAll('img, input[type=image]'))
    .filter((el) => !(el instanceof HTMLImageElement && el.complete));
  await Promise.race([
    Promise.all(pending.map((el) => new Promise((done) => {
      el.addEventListener('load', done, { once: true });
      el.addEventListener('error', done, { once: true });
    }))),
    new Promise((done) => setTimeout(done, 3000)),
  ]);
  await new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done)));
  return true;
}"""


# --- S2 captures-privacy r1 B1: same-origin frames drawn away from their border box -------------
#: A frame document rendering two made-up values (each detected through its own text node: an
#: e-mail and a PAN), each in a ``<span>`` with an id; ``{lead}``/``{tail}`` pad it (a tall
#: document the frame scrolls in).
FRAME_DOC = (
    "<!doctype html><html><head><meta charset='utf-8'><style>"
    "body{font:22px/1.5 Arial,Helvetica,sans-serif;margin:24px;color:#111;background:#fff}"
    ".big{font-size:26px}</style></head><body><h2>Statement</h2>{lead}"
    f"<p class='big'>Statement for <span id='v-email'>{CENSUS_EMAIL}</span></p>"
    f"<p class='big'>PAN: <span id='v-pan'>{CENSUS_PAN}</span></p>{{tail}}</body></html>"
)
_TALL = "<div style='height:600px'>top of the statement</div>"
#: case -> (iframe style, wrapper style, lead, tail, scroll the frame to the e-mail): a frame
#: scrolled as a ``#fragment`` scrolls it, a 40 px border, 50/60 px padding, a ``scale(2)``
#: transform, and a frame nested in a bordered, padded frame.
FRAME_CASES: dict[str, tuple[str, str, str, str, bool]] = {
    "scrolled": ("width:900px;height:300px;border:0", "", _TALL, _TALL, True),
    "border": ("width:900px;height:260px;border:40px solid #ccc", "", "", "", False),
    "padding": (
        "width:900px;height:260px;border:0;padding:50px 0 0 60px;background:#eee",
        "",
        "",
        "",
        False,
    ),
    "scaled": (
        "width:600px;height:200px;border:0;transform:scale(2);transform-origin:0 0",
        "height:520px",
        "",
        "",
        False,
    ),
}
FRAME_VALUES = (CENSUS_EMAIL, CENSUS_PAN)
#: Inserts ``[html, style, wrapper, nested, scroll]``: an iframe (``srcdoc``, same origin) at the
#: top of the page; ``nested`` puts it inside an outer frame with a 30 px border and 20/30 px
#: padding; ``scroll`` scrolls the inner document to ``#v-email`` (what a fragment does).
FRAME_JS = """async ([html, style, wrapper, nested, scroll]) => {
  const load = (f) => new Promise((done) => { f.onload = () => done(true); });
  const holder = document.createElement('div');
  holder.style.cssText = wrapper;
  document.body.prepend(holder);
  let doc = document, host = holder;
  if (nested) {
    const outer = document.createElement('iframe');
    outer.srcdoc = "<!doctype html><html><body style='margin:12px;background:#fff'></body></html>";
    outer.style.cssText = 'width:1000px;height:420px;border:30px solid #ccc;padding:20px 0 0 30px;display:block;box-sizing:content-box';
    const ready = load(outer);
    holder.appendChild(outer);
    await ready;
    doc = outer.contentDocument;
    host = doc.body;
  }
  const f = doc.createElement('iframe');
  f.id = 'v-frame';
  f.srcdoc = html;
  f.style.cssText = style + ';display:block;box-sizing:content-box';  // the site sets border-box
  const ready = load(f);
  host.appendChild(f);
  await ready;
  if (scroll) f.contentDocument.getElementById('v-email').scrollIntoView({ block: 'start' });
  await new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done)));
  return true;
}"""


def frame_case_args(case: str, *, nested: bool = False) -> list[Any]:
    """The ``FRAME_JS`` argument of a ``FRAME_CASES`` case (the inner frame nested or not)."""
    style, wrapper, lead, tail, scroll = FRAME_CASES[case]
    html = FRAME_DOC.replace("{lead}", lead).replace("{tail}", tail)
    return [html, style, wrapper, nested, scroll]
