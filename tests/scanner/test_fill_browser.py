"""Task 13: filling a page field by field — no key press, no click — and recording what the
page's own validation said. Every test counts what the demo site received."""

from __future__ import annotations

import functools
from typing import Any

import pytest

from dps.config import Settings
from dps.engine.policy import load_preset
from dps.engine.service import PrivacyEngine
from dps.engine.synth.surrogates import generate_synthesis_seed, seed_bytes
from dps.engine.transform.crypto import KeyMaterial, generate_key_material
from dps.layers.scanner.classify import classify_field
from dps.layers.scanner.dom import extract_page, frame_map
from dps.layers.scanner.personas_fill import (
    FillAction,
    FillResult,
    fill_page,
    persona_values,
    plan_field,
    prepare_fill,
    tokens_for,
    validation_counts,
    validation_records,
)
from tests.scanner.helpers import guarded_context, received

pytestmark = pytest.mark.browser
POLICY = load_preset("gdpr-pseudonymise")


async def _prepare(
    page: Any, engine: PrivacyEngine, mode: str, key: KeyMaterial | None = None
) -> Any:
    snap = await extract_page(page, iframes=True, shadow_dom=True, max_fields=400)
    frames = await frame_map(page, iframes=True)
    det = engine.detector(POLICY, "en_IN")
    classes = {f.key: classify_field(f, detector=det, min_score=0.6) for f in snap.fields}
    plans = {f.key: plan_field(f, classes[f.key]) for f in snap.fields}
    seed = generate_synthesis_seed()
    persona = persona_values(engine, seed=seed, persona_no=1, locale="en_IN", policy=POLICY)
    tokens = (
        functools.partial(tokens_for, engine, key=key, policy=POLICY, locale="en_IN", persona_no=1)
        if key
        else None
    )
    fill = prepare_fill(
        snap.fields,
        classes,
        plans,
        persona=persona,
        persona_no=1,
        seed=seed_bytes(seed),
        locale="en_IN",
        mode=mode,
        tokens=tokens,  # type: ignore[arg-type]
    )
    return snap, frames, classes, fill


async def test_identity_form_takes_the_persona_and_the_page_accepts_it(
    chromium: Any, scan_settings: Settings, scan_engine: PrivacyEngine, demo_site_url: str
) -> None:
    before = received(demo_site_url)
    async with guarded_context(chromium, scan_settings, demo_site_url) as (ctx, guard):
        page = await ctx.new_page()
        await page.goto(f"{demo_site_url}/apply/identity")
        snap, frames, classes, fill = await _prepare(page, scan_engine, "synthesize")
        assert [classes[f.key].entity_type for f in snap.fields] == [
            "PERSON_NAME",
            "DATE_OF_BIRTH",
            "AADHAAR",
            "PAN_IN",
            "PHONE",
            "EMAIL",
            "STREET_ADDRESS",
            "GENDER",
            "NATIONALITY",
        ]
        assert await guard.arm(page, fill.needles)
        results = await fill_page(page, frames, fill.actions, settle_ms=400)
        assert len(results) == 9 and all(r.status == "filled" for r in results.values())
        assert all(
            r.messages == [] for r in results.values()
        )  # the page's rules accept every value
        for f in snap.fields:
            if f.tag != "select":
                value = await frames[0].locator(f'[data-dps-field="{f.key}"]').input_value()
                assert value == fill.shown[f.key]
        assert guard.blocked_total >= 1  # the autosave POST and the beacon were stopped
        assert page.url == f"{demo_site_url}/apply/identity"  # still the page, not an error page
        det = scan_engine.detector(POLICY, "en_IN")
        records = validation_records(results, mode="synthesize", detector=det)
        assert validation_counts(records) == {"checked": 9, "accepted": 9, "rejected": 0}
    after = received(demo_site_url)
    assert after["non_get"] == before["non_get"]


async def test_det_tokens_break_the_aadhaar_pattern_and_that_is_recorded(
    chromium: Any, scan_settings: Settings, det_engine: PrivacyEngine, demo_site_url: str
) -> None:
    before = received(demo_site_url)
    async with guarded_context(chromium, scan_settings, demo_site_url) as (ctx, guard):
        page = await ctx.new_page()
        await page.goto(f"{demo_site_url}/apply/identity")
        snap, frames, classes, fill = await _prepare(
            page, det_engine, "obfuscate", key=generate_key_material("key")
        )
        assert await guard.arm(page, fill.needles)
        results = await fill_page(page, frames, fill.actions, settle_ms=400)
        aadhaar = next(f for f in snap.fields if f.name == "aadhaar")
        native = [m for m in results[aadhaar.key].messages if m["source"] == "native"]
        assert native and "patternMismatch" in native[0]["codes"]
        assert any(
            m["source"] == "aria" for m in results[aadhaar.key].messages
        )  # the demo's message
        records = validation_records(
            results, mode="obfuscate", detector=det_engine.detector(POLICY, "en_IN")
        )
        kinds = {m.source for m in records[aadhaar.key]}
        assert {"native", "aria", "aria-invalid"} <= kinds
        assert all(m.mode == "obfuscate" and len(m.text) <= 200 for m in records[aadhaar.key])
        counts = validation_counts(records)
        assert counts["rejected"] >= 1 and counts["checked"] == len(results)
    assert received(demo_site_url)["non_get"] == before["non_get"]


async def test_file_hidden_and_buttons_are_never_touched(
    chromium: Any, scan_settings: Settings, scan_engine: PrivacyEngine, demo_site_url: str
) -> None:
    before = received(demo_site_url)
    async with guarded_context(chromium, scan_settings, demo_site_url) as (ctx, guard):
        for path in ("/fixtures/labels", "/apply/documents"):
            page = await ctx.new_page()
            await page.goto(f"{demo_site_url}{path}")
            snap, frames, classes, fill = await _prepare(page, scan_engine, "synthesize")
            touched = {a.key for a in fill.actions}
            for f in snap.fields:
                if f.input_type in ("file", "hidden"):
                    assert f.key not in touched
            assert await guard.arm(page, fill.needles)
            await fill_page(page, frames, fill.actions, settle_ms=150)
            if path == "/fixtures/labels":
                assert (
                    await page.evaluate("document.querySelector('[name=csrf]').value") == "tok-123"
                )
            else:
                assert (
                    await page.evaluate("document.querySelector('[type=file]').files.length") == 0
                )
            assert page.url == f"{demo_site_url}{path}"
            assert await guard.close(page)
    assert received(demo_site_url)["non_get"] == before["non_get"]


async def test_an_autosubmitting_select_and_a_checkbox_submit_nothing(
    chromium: Any, scan_settings: Settings, scan_engine: PrivacyEngine, demo_site_url: str
) -> None:
    before = received(demo_site_url)
    async with guarded_context(chromium, scan_settings, demo_site_url) as (ctx, guard):
        page = await ctx.new_page()
        await page.goto(f"{demo_site_url}/apply/card")
        snap, frames, classes, fill = await _prepare(page, scan_engine, "synthesize")
        delivery = next(f for f in snap.fields if f.name == "delivery")
        assert any(a.key == delivery.key and a.kind == "select" for a in fill.actions)
        assert await guard.arm(page, fill.needles)
        results = await fill_page(page, frames, fill.actions, settle_ms=300)
        assert results[delivery.key].status == "filled"
        assert page.url == f"{demo_site_url}/apply/card"
        await page.set_content(
            "<form method=post action=/x><a href=/elsewhere><input type=checkbox data-dps-field='0:1'>"
            "</a><input type=radio name=r data-dps-field='0:2'></form>"
        )
        frames = await frame_map(page, iframes=False)
        acts = [
            FillAction(key="0:1", frame_index=0, field_no=1, kind="check", check=True),
            FillAction(key="0:2", frame_index=0, field_no=2, kind="radio", check=True),
        ]
        res = await fill_page(page, frames, acts, settle_ms=100)
        assert all(r.status == "filled" for r in res.values())
        assert await page.evaluate("document.querySelector('[data-dps-field=\"0:1\"]').checked")
        assert page.url == f"{demo_site_url}/apply/card"  # no click: the wrapping link never fired
    assert received(demo_site_url)["non_get"] == before["non_get"]


async def test_a_missing_field_or_frame_fails_that_field_only(
    chromium: Any, scan_settings: Settings, demo_site_url: str
) -> None:
    async with guarded_context(chromium, scan_settings, demo_site_url) as (ctx, guard):
        page = await ctx.new_page()
        await page.goto(f"{demo_site_url}/apply")
        snap = await extract_page(page, iframes=True, shadow_dom=True, max_fields=400)
        frames = await frame_map(page, iframes=True)
        name = next(f for f in snap.fields if f.name == "fullName")
        assert await guard.arm(page, ())
        acts = [
            FillAction(
                key=name.key, frame_index=0, field_no=name.field_no, kind="text", value="Asha Rao"
            ),
            FillAction(key="0:999", frame_index=0, field_no=999, kind="text", value="x"),
            FillAction(key="7:1", frame_index=7, field_no=1, kind="text", value="x"),
        ]
        res = await fill_page(page, frames, acts, settle_ms=50, timeout_ms=500)
        assert res[name.key].status == "filled"
        assert res["0:999"].status == "failed" and res["7:1"].status == "failed"
        assert "Asha" not in repr(acts[0]) and "messages" not in repr(FillResult(status="filled"))
