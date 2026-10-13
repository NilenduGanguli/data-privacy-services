"""Programme acceptance G3: OCR the captures — no source value is readable where masked.

Every captured PNG is read back with tesseract (upscaled 2x for recall). A positive control —
the same page captured ``unmasked`` — proves the OCR reads the demo values at all, so the masked
assertions are not vacuous. Values are compared in M0's ``normalise`` form (letters and digits).

Tesseract: ``DPS_TEST_TESSERACT_CMD`` (any executable taking tesseract's arguments, e.g. a
wrapper around ``docker run … data-privacy-services:local tesseract``), else ``tesseract`` on
``PATH``; the test skips without one (fails under ``DPS_TEST_REQUIRE_SERVICES=1`` with
``DPS_TEST_REQUIRE_TESSERACT=1``).
"""

from __future__ import annotations

import io
import os
import re
import shutil
import subprocess
from typing import Any

import pytest
from PIL import Image

from dps.config import Settings
from dps.engine.service import PrivacyEngine
from dps.engine.synth.surrogates import generate_synthesis_seed, seed_bytes
from dps.engine.transform.crypto import generate_key_material
from dps.engine.verify import normalise
from dps.layers.scanner.capture import capture_screen, obfuscated_capture
from dps.layers.scanner.classify import classify_field, classify_rendered, sensitive_prefilled
from dps.layers.scanner.dom import extract_page, frame_map
from tests.scanner.helpers import guarded_context

pytestmark = [pytest.mark.browser, pytest.mark.tesseract]

#: The demo values each page renders (data.json keys); /apply/review masks the Aadhaar itself.
PAGES: dict[str, tuple[str, ...]] = {
    "/profile": (
        "full_name",
        "dob",
        "customer_id",
        "aadhaar",
        "pan",
        "account_number",
        "card_number",
        "email",
        "mobile",
        "address",
    ),
    "/profile/contact": ("email", "mobile", "address"),
    "/apply/review": ("full_name", "email", "mobile", "pan"),
}


def _tesseract() -> str:
    cmd = os.environ.get("DPS_TEST_TESSERACT_CMD") or shutil.which("tesseract")
    if not cmd:
        if os.environ.get("DPS_TEST_REQUIRE_SERVICES") == "1" and (
            os.environ.get("DPS_TEST_REQUIRE_TESSERACT") == "1"
        ):
            pytest.fail("tesseract is required but neither DPS_TEST_TESSERACT_CMD nor PATH has it")
        pytest.skip("needs tesseract (PATH or DPS_TEST_TESSERACT_CMD; the app image has one)")
    return cmd


def _ocr(cmd: str, png: bytes) -> str:
    with Image.open(io.BytesIO(png)) as im:
        big = im.convert("RGB").resize((im.width * 2, im.height * 2), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    big.save(buf, format="PNG")
    out = subprocess.run(
        [cmd, "stdin", "stdout", "--psm", "11"],
        input=buf.getvalue(),
        capture_output=True,
        timeout=180,
        check=True,
    )
    return normalise(out.stdout.decode("utf-8", "replace"))


def _pieces(value: str, *, strict: bool) -> set[str]:
    """Parts of a value that must not be readable: the whole value, alphabetic runs of 5+
    letters and digit runs of 4+ (strict: masked originals) or 6+ (tokens and surrogates keep
    policy prefixes/suffixes and test IINs such as 4111; an email token keeps its domain)."""
    out = {normalise(value)}
    if not strict and "@" in value:
        value = value.rpartition("@")[0]  # the policy keeps an email's domain (method email)
    for run in re.findall(r"[A-Za-z0-9]+", value):
        if run.isdigit():
            if len(run) >= (4 if strict else 6):
                out.add(run)
        elif sum(ch.isalpha() for ch in run) >= 5:
            out.add(normalise(run))
    return out


def _readable(
    ocr: str, values: dict[str, str], keys: tuple[str, ...], *, strict: bool
) -> list[str]:
    """The keys whose value (or one of its pieces) the OCR text holds (names only)."""
    return [k for k in keys if any(p in ocr for p in _pieces(values[k], strict=strict))]


async def _open(ctx: Any, url: str) -> tuple[Any, Any, dict[int, Any]]:
    page = await ctx.new_page()
    await page.goto(url)
    snap = await extract_page(page, iframes=True, shadow_dom=True, max_fields=400)
    frames = await frame_map(page, iframes=True)
    return page, snap, frames


@pytest.mark.parametrize("path", list(PAGES))
async def test_masked_originals_hold_no_readable_value(
    path: str,
    chromium: Any,
    scan_settings: Settings,
    scan_engine: PrivacyEngine,
    demo_site_url: str,
    demo_data: dict[str, str],
) -> None:
    cmd = _tesseract()
    keys = PAGES[path]
    det = scan_engine.detector(scan_engine.policy(None), "en_IN")
    async with guarded_context(chromium, scan_settings, demo_site_url, cookies=True) as (ctx, _g):
        page, snap, frames = await _open(ctx, f"{demo_site_url}{path}")
        rendered = classify_rendered(snap.texts, detector=det)
        classes = {f.key: classify_field(f, detector=det, min_score=0.6) for f in snap.fields}
        prefilled = sensitive_prefilled(snap.fields, classes, det)
        raw = await capture_screen(
            page,
            frames,
            snap,
            rendered=rendered,
            prefilled=prefilled,
            max_height=12000,
            unmasked=True,
        )
        masked = await capture_screen(
            page, frames, snap, rendered=rendered, prefilled=prefilled, max_height=12000
        )
    assert raw is not None and masked is not None
    control = _readable(_ocr(cmd, raw), demo_data, keys, strict=True)
    assert len(control) >= max(2, len(keys) // 2), control  # the OCR reads this page's values
    assert _readable(_ocr(cmd, masked), demo_data, keys, strict=True) == []


@pytest.mark.parametrize("mode", ["obfuscate", "synthesize"])
@pytest.mark.parametrize("path", ["/profile", "/profile/contact"])
async def test_obfuscated_captures_hold_no_readable_value(
    mode: str,
    path: str,
    chromium: Any,
    scan_settings: Settings,
    scan_engine: PrivacyEngine,
    demo_site_url: str,
    demo_data: dict[str, str],
) -> None:
    cmd = _tesseract()
    keys = PAGES[path]
    det = scan_engine.detector(scan_engine.policy(None), "en_IN")
    async with guarded_context(chromium, scan_settings, demo_site_url, cookies=True) as (
        ctx,
        guard,
    ):
        page, snap, frames = await _open(ctx, f"{demo_site_url}{path}")
        rendered = classify_rendered(snap.texts, detector=det)
        classes = {f.key: classify_field(f, detector=det, min_score=0.6) for f in snap.fields}
        res = await obfuscated_capture(
            page,
            frames,
            snap,
            rendered=rendered,
            prefilled=sensitive_prefilled(snap.fields, classes, det),
            engine=scan_engine,
            policy=scan_engine.policy(None),
            mode=mode,  # type: ignore[arg-type]
            key=generate_key_material("key") if mode == "obfuscate" else None,
            seed=seed_bytes(generate_synthesis_seed()),
            locale="en_IN",
            guard=guard,
            settings=scan_settings,
            page_no=1,
        )
    assert res.status == "captured" and res.png is not None, res.reason
    assert _readable(_ocr(cmd, res.png), demo_data, keys, strict=False) == []
