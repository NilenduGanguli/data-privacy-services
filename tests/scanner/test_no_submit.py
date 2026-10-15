"""M4 exit criterion (spec §11, contract §12.3, decision D4): DPS never submits anything — proven,
not claimed.

Four layers: (1) a positive control shows the demo site counts every POST and every GET-form
submission it receives; (2) full crawls of the whole demo site — also with the JavaScript
hardening switched off, so the page's own autosaves, beacon and auto-submitting select really fire
— leave those counters unchanged while the guard's log shows the blocked requests; (3) an
adversarial test fills forms and then itself tries every way to submit them — nothing arrives;
(4) a static check proves the scanner's source has no submit path to begin with.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Any

import httpx
import pytest

from dps.config import Settings
from dps.engine.service import PrivacyEngine
from dps.layers.scanner.runtime import MemoryScanSink
from tests.scanner.helpers import guarded_context, make_runtime, received

ROOT = Path(__file__).resolve().parents[2]
_ALLOWED_WORDS = re.compile(r"(submit_attempts|SUBMIT_ATTEMPTS_JS|submitAttempts|submissions)")


@pytest.mark.browser
def test_positive_control_the_demo_counts_what_it_receives(demo_site_url: str) -> None:
    before = received(demo_site_url)
    httpx.post(f"{demo_site_url}/apply/identity/submit", data={"fullName": "x"})
    httpx.get(f"{demo_site_url}/help/track/result", params={"email": "x"})
    after = received(demo_site_url)
    assert after["non_get"] == before["non_get"] + 1
    assert after["form_get"] == before["form_get"] + 1


@pytest.mark.browser
@pytest.mark.slow
@pytest.mark.parametrize("harden_js", [False, True])
async def test_full_scan_never_submits(
    harden_js: bool,
    chromium: Any,
    scan_settings: Settings,
    scan_engine: PrivacyEngine,
    demo_site_url: str,
) -> None:
    from dps.layers.scanner.crawler import ScanRunner

    before = received(demo_site_url)
    sink = MemoryScanSink()
    await ScanRunner(
        settings=scan_settings,
        engine=scan_engine,
        sink=sink,
        browser=chromium,
        harden_js=harden_js,
        runtime=make_runtime(demo_site_url, scan_settings, scan_engine, mode="obfuscate"),
    ).run()
    after = received(demo_site_url)
    assert after["non_get"] == before["non_get"] and after["form_get"] == before["form_get"]
    assert after["by_path"] == before["by_path"]
    assert after["logout_hits"] == before["logout_hits"]
    blocked: Counter[tuple[str, str]] = Counter()
    for r in sink.requests:
        if r.decision == "blocked":
            blocked[(r.method, r.reason)] += r.count
        else:
            assert r.method in ("GET", "HEAD"), r
    assert blocked[("POST", "method")] >= 3
    if not harden_js:  # the auto-submitting select on /apply/card fired a real form POST
        assert any(
            e.kind == "request" and e.method == "POST" and "/apply/card" in e.target
            for e in sink.events
        )


@pytest.mark.browser
async def test_adversarial_submission_attempts_on_filled_pages_all_fail(
    chromium: Any, scan_settings: Settings, demo_site_url: str
) -> None:
    from dps.layers.scanner.dom import extract_page

    before = received(demo_site_url)
    async with guarded_context(chromium, scan_settings, demo_site_url, harden_js=False) as (
        ctx,
        guard,
    ):
        for path, name in (("/apply/identity", "fullName"), ("/help/track", "email")):
            page = await ctx.new_page()
            await page.goto(f"{demo_site_url}{path}")
            await extract_page(page, iframes=True, shadow_dom=True, max_fields=400)
            assert await guard.arm(page, ["Meera Venkatesan", "meera.v@example.com"])
            await page.locator(f"[name={name}]").fill("meera.v@example.com")
            attempts = [
                "document.forms[0].requestSubmit()",
                "HTMLFormElement.prototype.submit.call(document.forms[0])",
                "fetch('/api/autosave', {method: 'POST', body: 'x'}).catch(() => 0)",
                "navigator.sendBeacon('/api/telemetry/x', 'x')",
                "location.href = '/help/track/result?email=meera.v%40example.com'",
            ]
            for js in attempts:
                try:
                    await page.evaluate(js)
                except Exception:  # noqa: BLE001 - a blocked navigation may tear the frame down
                    pass
                await page.wait_for_timeout(150)
            if not page.is_closed():
                try:
                    await page.locator(f"[name={name}]").press("Enter", timeout=1000)
                    await page.locator("button[type=submit]").click(
                        timeout=1000, no_wait_after=True
                    )
                except Exception:  # noqa: BLE001 - an error page has neither element
                    pass
                await page.wait_for_timeout(300)
            await guard.close(page)  # the demo flushes a draft on pagehide
        reasons = {c.reason for c in guard.drain_counts() if c.decision == "blocked"}
    after = received(demo_site_url)
    assert after["non_get"] == before["non_get"] and after["form_get"] == before["form_get"]
    assert after["by_path"] == before["by_path"]
    assert {"method", "filled_navigation"} <= reasons


def _code_only(text: str) -> str:
    """Python source without docstrings and triple-quoted strings (page-side JavaScript)."""
    return re.sub(r'[rbf]?"""[\s\S]*?"""', "", text)


def test_no_submission_code_path_exists_in_the_scanner() -> None:
    paths = [
        *sorted((ROOT / "dps/layers/scanner").glob("*.py")),
        ROOT / "dps/browser_worker.py",
        ROOT / "dps/layers/api/routers/scans.py",
    ]
    sources = {p: p.read_text(encoding="utf-8") for p in paths if p.exists()}
    code = {p: _code_only(s) for p, s in sources.items()}
    joined = "\n".join(code.values())
    for banned in (".press(", ".keyboard", ".mouse", ".dispatch_event(", ".tap(", ".check("):
        assert banned not in joined, banned
    assert "submit" not in _ALLOWED_WORDS.sub("", joined).lower()
    # one click call site in the whole scanner, in the crawler, on a stamped link-like element
    clicks = [
        (p.name, line) for p, c in code.items() for line in c.splitlines() if ".click(" in line
    ]
    assert len(clicks) == 1 and clicks[0][0] == "crawler.py", clicks
    assert "data-dps-click" in clicks[0][1]
    # ... reached from exactly two places: SPA discovery and SPA replay
    crawler = code[ROOT / "dps/layers/scanner/crawler.py"]
    assert len(re.findall(r"self\._click\(", crawler)) == 2
    guard_src = sources[ROOT / "dps/layers/scanner/guard.py"]
    assert joined.count("route.continue_(") == guard_src.count("route.continue_(") == 1
    assert 'ALLOWED_METHODS: frozenset[str] = frozenset({"GET", "HEAD"})' in guard_src
