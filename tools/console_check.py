"""Browser check of the M1 console pages against their mockups (Definition of Done).

    uv pip install -e ".[uitest]"                    # playwright==1.55.0 (or any venv holding it)
    docker compose --profile docs up -d               # the stack under test (Task 19)
    DPS_API_KEY=dps_local_dev_key_change_me python tools/console_check.py
    # the app: DPS_TEST_APP_URL, else http://127.0.0.1:${DPS_APP_PORT:-8400} (as tools/smoke_test.py)

Drives /playground/documents (demo KYC scan → Redact → download → reveal → Synthesize) and /policies
(draft edit, recognizer test on the engine, plan preview) at 1440×900 and 390×844 in dark and light;
asserts each mockup section is present, no horizontal scroll and no console error; writes app and
mockup screenshots side by side to docs/superpowers/build/m1-console/. Prints PASS/FAIL lines only.

The key goes into the tab's ``sessionStorage`` (the console's default, O5), never ``localStorage``.

playwright==1.55.0 expects Chromium build 1187. When that build is not installed (this Mac caches
1148/1234/1243), the newest cached chrome-headless-shell is used instead, or the binary named by
DPS_PLAYWRIGHT_CHROMIUM.
"""

from __future__ import annotations

import json
import os
import re
import sys
from glob import glob
from pathlib import Path

from playwright.sync_api import Browser, Error, Page, Playwright, expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
BASE = (
    os.environ.get("DPS_TEST_APP_URL")
    or f"http://127.0.0.1:{os.environ.get('DPS_APP_PORT') or '8400'}"
).rstrip("/")
KEY = os.environ.get("DPS_API_KEY", "dps_local_dev_key_change_me")
OUT = ROOT / "docs" / "superpowers" / "build" / "m1-console"
SIZES = {"desktop": (1440, 900), "phone": (390, 844)}
MOCKUPS = {"documents": "08-documents.html", "policies": "05-policies.html"}
DOC_SECTIONS = ("Input", "Findings", "Output · redacted PDF", "Text extraction", "Key material")
POLICY_SECTIONS = (
    "Presets",
    "Treatment per entity type",
    "Declared fields",
    "Custom recognizer",
    "Plan preview",
    "Version history",
)
TIMEOUT_MS = 180_000
failures: list[str] = []


def check(name: str, ok: bool) -> None:
    """Print one verdict (never a value) and remember a failure."""
    print(f"{'PASS' if ok else 'FAIL'}  {name}", flush=True)
    if not ok:
        failures.append(name)


def _revision(path: str) -> int:
    match = re.search(r"headless_shell-(\d+)", path)
    return int(match.group(1)) if match else 0


def launch(p: Playwright) -> Browser:
    """Playwright's own Chromium, else the newest cached headless shell (see the module docstring)."""
    explicit = os.environ.get("DPS_PLAYWRIGHT_CHROMIUM")
    if explicit:
        return p.chromium.launch(executable_path=explicit)
    try:
        return p.chromium.launch()
    except Error:
        cache = Path(
            os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
            or Path.home() / "Library/Caches/ms-playwright"
        )
        shells = sorted(
            glob(str(cache / "chromium_headless_shell-*/*/chrome-headless-shell")), key=_revision
        )
        if not shells:
            raise
        return p.chromium.launch(executable_path=shells[-1])


def no_horizontal_scroll(page: Page) -> bool:
    """The page is no wider than the viewport (1 px of rounding allowed)."""
    return bool(page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"))


def shoot(page: Page, name: str) -> None:
    """A full-page screenshot from the top: fixed and sticky parts (the header, the sidebar, the
    Policies side column) are drawn where the page is scrolled to, so scroll back first. The key
    line (kid and the shortened DEK) is masked: screenshots are committed, key material never is."""
    page.evaluate("window.scrollTo(0, 0)")
    page.wait_for_timeout(150)
    page.screenshot(path=str(OUT / name), full_page=True, mask=[page.locator(".keycard .mono")])


def documents_flow(page: Page, tag: str) -> None:
    """Playground → Document: the demo scan through Redact, download, reveal, then Synthesize."""
    page.goto(f"{BASE}/playground/documents")
    expect(page.get_by_test_id("ocr-status")).to_contain_text("up")
    page.get_by_role("button", name="Load demo KYC scan").click()
    page.get_by_test_id("mode-red").click()
    page.get_by_test_id("run").click()
    expect(page.get_by_test_id("verify-ok")).to_be_visible(timeout=TIMEOUT_MS)
    check(f"{tag} documents: boxes drawn on page 1", page.get_by_test_id("page-box").count() > 0)
    for title in DOC_SECTIONS:
        check(
            f"{tag} documents: '{title}'", page.get_by_text(title, exact=False).first.is_visible()
        )
    with page.expect_download() as download:
        page.get_by_role("button", name="Download redacted PDF").click()
    check(
        f"{tag} documents: redacted PDF downloads",
        download.value.suggested_filename.endswith(".redacted.pdf"),
    )
    page.get_by_role("button", name="Reveal text with key").click()
    expect(page.get_by_text("original text restored")).to_be_visible(timeout=30_000)
    check(
        f"{tag} documents: revealed text is labelled as the original",
        page.get_by_text("Text extraction · revealed original").count() == 1
        and page.get_by_text("Text extraction · obfuscated").count() == 0,
    )
    check(f"{tag} documents: no horizontal scroll", no_horizontal_scroll(page))
    shoot(page, f"documents-{tag}.png")
    page.get_by_test_id("mode-syn").click()
    page.get_by_test_id("run").click()
    expect(page.get_by_text("Output · synthetic text, page view")).to_be_visible(timeout=TIMEOUT_MS)
    check(f"{tag} documents: synthesize renders", page.get_by_test_id("verify-ok").is_visible())


def policies_flow(page: Page, tag: str) -> None:
    """Policies: sections, a draft change, the recognizer test on the engine, the plan preview."""
    page.goto(f"{BASE}/policies/gdpr-pseudonymise")
    expect(page.get_by_test_id("policy-header")).to_be_visible()
    for title in POLICY_SECTIONS:
        check(f"{tag} policies: '{title}'", page.get_by_text(title, exact=False).first.is_visible())
    publish = page.get_by_role("button", name="Publish", exact=True)
    check(
        f"{tag} policies: Publish is disabled and looks it (policy store: M5)",
        bool(publish.evaluate("b => b.disabled && Number(getComputedStyle(b).opacity) < 1")),
    )
    page.get_by_label("Obfuscate method for CVV").select_option("det_token")
    check(
        f"{tag} policies: draft counted",
        page.get_by_text(re.compile(r"draft · 1 change")).first.is_visible(),
    )
    page.get_by_role("button", name="Run on the engine").click()
    expect(page.get_by_text("Engine result")).to_be_visible(timeout=30_000)
    page.get_by_role("button", name="Re-run plan").click()
    expect(page.locator("#plan tbody tr").nth(3)).to_be_visible(timeout=30_000)
    check(f"{tag} policies: plan rows", page.locator("#plan tbody tr").count() >= 4)
    check(f"{tag} policies: no horizontal scroll", no_horizontal_scroll(page))
    shoot(page, f"policies-{tag}.png")


def _init_script(theme: str) -> str:
    # Storage can throw (file:// mockups, blocked site data): the console then asks for the key.
    return (
        "try { sessionStorage.setItem('dps.apiKey', "
        f"{json.dumps(KEY)}); localStorage.setItem('dps.theme', {json.dumps(theme)}); "
        "} catch (e) {}"
    )


def main() -> int:
    """Run both flows at every size and theme; screenshot the app and the mockups."""
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = launch(p)
        for theme in ("dark", "light"):
            for size, (width, height) in SIZES.items():
                tag = f"{theme}-{size}"
                context = browser.new_context(
                    viewport={"width": width, "height": height},
                    color_scheme=theme,
                    accept_downloads=True,
                )
                context.add_init_script(_init_script(theme))
                page = context.new_page()
                errors: list[str] = []
                page.on(
                    "console",
                    lambda m, errors=errors: errors.append(m.type) if m.type == "error" else None,
                )
                page.on("pageerror", lambda e, errors=errors: errors.append("pageerror"))
                try:
                    documents_flow(page, tag)
                    policies_flow(page, tag)
                except (Error, AssertionError) as exc:  # a step failed: name the class only
                    check(f"{tag}: flow completed ({type(exc).__name__})", False)
                check(f"{tag}: no console errors", not errors)
                for name, file in MOCKUPS.items():
                    query = "?light" if theme == "light" else ""
                    page.goto((ROOT / "docs" / "design" / "mockups" / file).as_uri() + query)
                    page.screenshot(path=str(OUT / f"mockup-{name}-{tag}.png"), full_page=True)
                context.close()
        browser.close()
    verdict = "all checks passed" if not failures else f"{len(failures)} failed"
    print(f"\n{verdict} · screenshots in {OUT.relative_to(ROOT)}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
