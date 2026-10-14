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
from tests.scanner.helpers import (
    CENSUS_OBFUSCATED_WITHHELD,
    CENSUS_PAGES,
    CENSUS_SINGLE_COPY,
    CENSUS_VALUES,
    FRAME_CASES,
    FRAME_JS,
    FRAME_VALUES,
    INJECT_JS,
    frame_case_args,
    guarded_context,
)

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


#: Made-up names standing for a customer: one ASCII, one with letters outside ASCII.
TWICE_NAMES: dict[str, str] = {
    "ascii": "Zebulon Quixotic Hartwell",
    "non_ascii": "Renée Østergård Blackwood",
}
TWICE_JS = """(name) => {
  const d = document.createElement('div');
  d.innerHTML = `<table><tbody><tr><th>Full name</th><td>${name}</td></tr></tbody></table>
                 <p style="font-size:22px">Welcome back, ${name}</p>`;
  document.body.prepend(d);
  return true;
}"""


@pytest.mark.parametrize("label", list(TWICE_NAMES))
async def test_a_value_rendered_twice_is_masked_in_both_places(
    label: str,
    chromium: Any,
    scan_settings: Settings,
    scan_engine: PrivacyEngine,
    demo_site_url: str,
) -> None:
    """M4-G3 review r1 B1: the detectors find the name through its row header only; the second,
    undetected copy in the greeting must not be readable in the masked original either."""
    cmd = _tesseract()
    name = TWICE_NAMES[label]
    words = {normalise(w) for w in name.split() if len(normalise(w)) >= 5}
    det = scan_engine.detector(scan_engine.policy(None), "en_IN")
    async with guarded_context(chromium, scan_settings, demo_site_url, cookies=True) as (ctx, _g):
        page = await ctx.new_page()
        await page.goto(f"{demo_site_url}/help")
        await page.evaluate(TWICE_JS, name)
        snap = await extract_page(page, iframes=True, shadow_dom=True, max_fields=400)
        frames = await frame_map(page, iframes=True)
        rendered = classify_rendered(snap.texts, detector=det)
        raw = await capture_screen(
            page, frames, snap, rendered=rendered, prefilled=[], max_height=12000, unmasked=True
        )
        masked = await capture_screen(
            page, frames, snap, rendered=rendered, prefilled=[], max_height=12000, detector=det
        )
    assert raw is not None and masked is not None
    control = _ocr(cmd, raw)
    family = normalise(name.split()[-1])
    assert control.count(family) >= 2  # the OCR reads both copies when nothing is masked
    seen = _ocr(cmd, masked)
    assert [w for w in words if w in seen] == []


async def _census_page(ctx: Any, url: str, case: str) -> tuple[Any, Any, dict[int, Any]]:
    page = await ctx.new_page()
    await page.goto(f"{url}/help")
    await page.wait_for_load_state("load")
    await page.evaluate(INJECT_JS, CENSUS_PAGES[case])
    snap = await extract_page(page, iframes=True, shadow_dom=True, max_fields=400)
    return page, snap, await frame_map(page, iframes=True)


@pytest.mark.parametrize("case", list(CENSUS_PAGES))
async def test_a_value_outside_readable_text_nodes_is_never_readable(
    case: str,
    chromium: Any,
    scan_settings: Settings,
    scan_engine: PrivacyEngine,
    demo_site_url: str,
) -> None:
    """M4-G3 review r2 B1/B3: a detected value copied into a button, an input button label, a
    listbox option, one character per node, or past the 2000th character of a text node is not
    readable in the masked capture nor in either obfuscated capture (or the capture is withheld:
    the per-character copy must be). The unmasked control reads every value, and the copy too.

    M4 verify r1 B4/B5: the same for text under a ``display:contents`` parent, slotted text in a
    component whose host is ``display:contents``, a slot's fallback text (with a second copy in a
    table, and as the only copy), and for strings the browser draws from attributes: a broken
    image's alt text, an image button's alt text, an ``<optgroup>`` label and an option's
    ``label`` in a listbox, and a placeholder."""
    cmd = _tesseract()
    values = {v: v for v in CENSUS_VALUES[case]}
    keys = tuple(values)
    det = scan_engine.detector(scan_engine.policy(None), "en_IN")
    policy = scan_engine.policy(None)
    outputs: dict[str, bytes | None] = {}
    async with guarded_context(chromium, scan_settings, demo_site_url, cookies=True) as (ctx, _g):
        page, snap, frames = await _census_page(ctx, demo_site_url, case)
        rendered = classify_rendered(snap.texts, detector=det)
        raw = await capture_screen(
            page, frames, snap, rendered=rendered, prefilled=[], max_height=12000, unmasked=True
        )
        outputs["masked"] = await capture_screen(
            page, frames, snap, rendered=rendered, prefilled=[], max_height=12000, detector=det
        )
    for mode in ("synthesize", "obfuscate"):
        async with guarded_context(chromium, scan_settings, demo_site_url, cookies=True) as (
            ctx,
            guard,
        ):
            page, snap, frames = await _census_page(ctx, demo_site_url, case)
            res = await obfuscated_capture(
                page,
                frames,
                snap,
                rendered=classify_rendered(snap.texts, detector=det),
                prefilled=[],
                engine=scan_engine,
                policy=policy,
                mode=mode,  # type: ignore[arg-type]
                key=generate_key_material("key") if mode == "obfuscate" else None,
                seed=seed_bytes(generate_synthesis_seed()),
                locale="en_IN",
                guard=guard,
                settings=scan_settings,
                page_no=1,
            )
            outputs[mode] = res.png if res.status == "captured" else None
    assert raw is not None
    control = _ocr(cmd, raw)
    assert _readable(control, values, keys, strict=True) == list(keys)  # the OCR reads them all
    if case not in CENSUS_SINGLE_COPY:  # the second copy is readable unmasked
        first = normalise(keys[0])
        assert control.count(first) >= 2, case
    if case == "digits":
        assert outputs == {"masked": None, "synthesize": None, "obfuscate": None}
        return
    for name, png in outputs.items():
        if png is None and name != "masked" and case in CENSUS_OBFUSCATED_WITHHELD:
            continue  # withheld (leakage): an unflagged copy in read text is never shown
        assert png is not None, name
        strict = name == "masked"
        assert _readable(_ocr(cmd, png), values, keys, strict=strict) == [], name


async def _frame_page(
    ctx: Any, url: str, case: str, nested: bool
) -> tuple[Any, Any, dict[int, Any]]:
    page = await ctx.new_page()
    await page.goto(f"{url}/help")
    await page.wait_for_load_state("load")
    await page.evaluate(FRAME_JS, frame_case_args(case, nested=nested))
    snap = await extract_page(page, iframes=True, shadow_dom=True, max_fields=400)
    return page, snap, await frame_map(page, iframes=True)


@pytest.mark.parametrize("nested", [False, True], ids=["frame", "nested"])
@pytest.mark.parametrize("case", list(FRAME_CASES))
async def test_text_in_a_same_origin_frame_is_never_readable(
    case: str,
    nested: bool,
    chromium: Any,
    scan_settings: Settings,
    scan_engine: PrivacyEngine,
    demo_site_url: str,
) -> None:
    """S2 captures-privacy r1 B1: an e-mail and a PAN rendered in a same-origin frame that is
    fragment-scrolled, has a 40 px border, 50/60 px padding or ``scale(2)`` (alone, and inside a
    bordered, padded frame) are readable in the unmasked control and in none of the masked capture
    and the two obfuscated captures (before the fix the masked capture showed both)."""
    cmd = _tesseract()
    values = {v: v for v in FRAME_VALUES}
    keys = tuple(values)
    det = scan_engine.detector(scan_engine.policy(None), "en_IN")
    outputs: dict[str, bytes | None] = {}
    async with guarded_context(chromium, scan_settings, demo_site_url, cookies=True) as (ctx, _g):
        page, snap, frames = await _frame_page(ctx, demo_site_url, case, nested)
        rendered = classify_rendered(snap.texts, detector=det)
        raw = await capture_screen(
            page, frames, snap, rendered=rendered, prefilled=[], max_height=12000, unmasked=True
        )
        outputs["masked"] = await capture_screen(
            page, frames, snap, rendered=rendered, prefilled=[], max_height=12000, detector=det
        )
    for mode in ("synthesize", "obfuscate"):
        async with guarded_context(chromium, scan_settings, demo_site_url, cookies=True) as (
            ctx,
            guard,
        ):
            page, snap, frames = await _frame_page(ctx, demo_site_url, case, nested)
            res = await obfuscated_capture(
                page,
                frames,
                snap,
                rendered=classify_rendered(snap.texts, detector=det),
                prefilled=[],
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
            assert res.status == "captured", (mode, res.reason)
            outputs[mode] = res.png
    assert raw is not None
    assert _readable(_ocr(cmd, raw), values, keys, strict=True) == list(keys)  # the control
    for name, png in outputs.items():
        assert png is not None, name
        assert _readable(_ocr(cmd, png), values, keys, strict=name == "masked") == [], name
