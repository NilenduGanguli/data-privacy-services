"""The showcase deck (M5 Task 26B): built from the demo's value-free outputs, Keynote-safe.

Every figure on a slide must come from an input (the e2e demo report, the bench JSON); a missing
input renders a slate placeholder box saying what is missing, never a made-up number. Keynote (the
owner's viewer) drops PowerPoint chart parts and lacks some fonts, so the deck draws charts as plain
shapes and names only Helvetica Neue and Arial.
"""

from __future__ import annotations

import json
import re
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("pptx")

from PIL import Image  # noqa: E402  (pptx imported above implies Pillow)
from pptx import Presentation  # noqa: E402

from dps.engine.detect.ensemble import Detector, DetectorConfig  # noqa: E402
from dps.engine.taxonomy import Sensitivity  # noqa: E402
from tools import build_deck  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "docs" / "demo" / "DEMO-SCRIPT.md"
ALLOWED_FONTS = {"Helvetica Neue", "Arial"}
#: A figure: digits not glued to a letter, digit, underscore or dot (labels such as S8, L2, D4, P1,
#: e2e and v1 are names, not figures).
NUMBER = re.compile(r"(?<![A-Za-z0-9_.])\d[\d,]*(?:\.\d+)?")

BENCH = {
    "batch_rows": 1000,
    "engine": "oracle",
    "engine_version": "0.2.0",
    "fpe_enabled": True,
    "host": {"cpus": 18, "machine": "arm64"},
    "job_id": "j_01M4VSVE8QSM41YZT9B9N83PH8",
    "label": "1-worker",
    "mode": "obfuscate",
    "parallelism": 4,
    "partition_rows_per_second": {"p50": 246.9, "p95": 246.9},
    "partitions": 4,
    "rows": 20000,
    "rows_per_second": 941.6,
    "state": "succeeded",
    "wall_seconds": 21.24,
    "workers_online": 1,
    "write_mode": "table",
}

S8_FACTS: dict[str, Any] = {
    "counts": {"rows_written": 1987},
    "fidelity": {"score": 0.9312},
    "privacy": {
        "exact_row_matches": 0,
        "nndr": {"ratio": 1.163, "target": 1.0, "measured": 1, "passed": True},
        "membership_inference": {"run": True, "auc": 0.5134, "target": 0.6, "passed": True},
    },
    "utility": {"run": True, "metric": "accuracy", "ratio": 0.9721},
}
S9_FACTS: dict[str, Any] = {
    "scan": {"pages": 3, "fields": 17, "personas": 3, "submissions": 0},
    "posts_received": 0,
}
FULL_FACTS: dict[str, dict[str, Any]] = {
    "S1": {"raw_pii_sent_to_llm": 0},
    "S2": {
        "findings": 13,
        "entity_types": ["AADHAAR", "EMAIL", "PAN_IN", "PERSON", "PHONE"],
        "reveal_identical": True,
        "raw_pii_sent_to_llm": 0,
    },
    "S4": {"ocr_pages": 2, "findings": 9, "extractable_chars": 0, "originals_left": 0},
    "S6": {
        "jobs": [
            {
                "engine": "oracle",
                "counts": {"rows_written": 2000, "partitions": 4},
                "timings": {"rows_per_s": 812.4},
                "verification": {"passed": True},
            },
            {
                "engine": "mongo",
                "counts": {"rows_written": 2000, "partitions": 4},
                "timings": {"rows_per_s": 1430.2},
                "verification": {"passed": True},
            },
            {
                "engine": "postgres",
                "counts": {"rows_written": 2000, "partitions": 4},
                "timings": {"rows_per_s": 2210.9},
                "verification": {"passed": True},
            },
        ]
    },
    "S7": {
        "members": [{"engine": "oracle"}, {"engine": "mongo"}, {"engine": "postgres"}],
        "pairs": [
            {"left": 0, "right": 1, "checked": 500, "matched": 500, "consistent": True},
            {"left": 0, "right": 2, "checked": 500, "matched": 500, "consistent": True},
            {"left": 1, "right": 2, "checked": 500, "matched": 500, "consistent": True},
        ],
        "sampled": 500,
        "all_members_matched": 500,
        "passed": True,
    },
    "S8": S8_FACTS,
    "S9": S9_FACTS,
    "S10": {"packs_listed": 5, "audit_rows": 42, "pdf_ok": True},
    "S11": {
        "posture": {"passed": 11, "total": 11},
        "rotated_key_status": 401,
        "retention_state": "succeeded",
    },
}
SCREENS_ALL = ("playground", "job", "scan-parallel", "evidence")


# -- fixtures -------------------------------------------------------------------------------------
def _report(steps: dict[str, dict[str, Any]], **top: Any) -> dict[str, Any]:
    return {
        "schema": "dps.e2e-demo.v1",
        "generated_at": "2026-10-14T15:04:05Z",
        "dps_version": "1.0.0",
        "commit": "64c46e3",
        "tenant": "retail-banking-apac",
        "total_seconds": 431.6,
        "steps": [
            {"id": sid, "title": f"step {sid}", "status": "PASS", "seconds": 12.5, "facts": facts}
            for sid, facts in steps.items()
        ],
        **top,
    }


def _inputs(
    tmp_path: Path,
    steps: dict[str, dict[str, Any]],
    *,
    screens: tuple[str, ...] = ("playground", "scan-parallel"),
    bench: bool = True,
    report_top: dict[str, Any] | None = None,
) -> dict[str, Path]:
    report = tmp_path / "e2e-demo-report.json"
    report.write_text(json.dumps(_report(steps, **(report_top or {}))), encoding="utf-8")
    shots = tmp_path / "demo-screens"
    shots.mkdir()
    for name in screens:
        Image.new("RGB", (8, 8), (15, 118, 110)).save(shots / f"{name}-light.png")
    bench_dir = tmp_path / "bench"
    bench_dir.mkdir()
    if bench:
        (bench_dir / "m3-2026-10-13-oracle-obfuscate-table-1-worker.json").write_text(
            json.dumps(BENCH), encoding="utf-8"
        )
    return {"report": report, "screens": shots, "bench": bench_dir}


def _build(tmp_path: Path, paths: dict[str, Path]) -> tuple[Any, build_deck.BuildResult]:
    out = tmp_path / "deck.pptx"
    result = build_deck.build_deck(
        out=out,
        report=paths["report"],
        screens=paths["screens"],
        bench=paths["bench"],
        script=SCRIPT,
    )
    return Presentation(str(out)), result


def _shapes(container: Any) -> Iterator[Any]:
    for shape in container.shapes:
        yield shape
        if shape.shape_type is not None and hasattr(shape, "shapes"):  # group
            yield from _shapes(shape)


def _texts(slide: Any, *, chrome: bool = False) -> list[str]:
    return [
        s.text_frame.text
        for s in _shapes(slide)
        if s.has_text_frame and (chrome or not s.name.startswith("chrome:"))
    ]


def _input_numbers(*docs: Any) -> list[float]:
    def walk(obj: Any) -> Iterator[float]:
        if isinstance(obj, bool):
            return
        if isinstance(obj, int | float):
            yield float(obj)
        elif isinstance(obj, dict):
            for v in obj.values():
                yield from walk(v)
        elif isinstance(obj, list):
            for v in obj:
                yield from walk(v)

    return [n for doc in docs for n in walk(doc)]


def _traceable(token: str, numbers: list[float]) -> bool:
    whole, _, frac = token.rstrip(",").replace(",", "").partition(".")
    printed = f"{int(whole)}.{frac}" if frac else str(int(whole))
    return any(f"{v:.{len(frac)}f}" == printed for v in numbers)


def _names(slide: Any) -> set[str]:
    return {s.name for s in _shapes(slide)}


# -- the plan's acceptance (Task 26B step 1) ------------------------------------------------------
def test_deck_from_fixture_inputs(tmp_path: Path) -> None:
    paths = _inputs(tmp_path, {"S8": S8_FACTS, "S9": S9_FACTS})
    prs, result = _build(tmp_path, paths)
    slides = list(prs.slides)

    # 16:9, exactly twelve slides, each with a title placeholder and speaker notes
    assert abs(prs.slide_width / prs.slide_height - 16 / 9) < 0.01
    assert len(slides) == 12 and result.slides == 12
    for slide in slides:
        assert slide.shapes.title is not None and slide.shapes.title.text.strip()
        assert slide.has_notes_slide and slide.notes_slide.notes_text_frame.text.strip()

    # no chart parts (Keynote drops them), no embedded workbooks, no graphic frames at all
    with zipfile.ZipFile(result.out) as z:
        names = z.namelist()
        assert not [n for n in names if n.startswith(("ppt/charts/", "ppt/embeddings/"))]
        xml = {n: z.read(n).decode("utf-8") for n in names if n.endswith(".xml")}
    for name, text in xml.items():
        if name.startswith("ppt/slides/slide"):
            assert "<p:graphicFrame" not in text, name
        # only Helvetica Neue and Arial are ever named (the theme references +mj-lt and +mn-lt
        # resolve to the theme's fonts, checked below)
        faces = set(re.findall(r'typeface="([^"]*)"', text))
        assert faces <= ALLOWED_FONTS | {""} | {f for f in faces if f.startswith("+m")}, name
    themes = [t for n, t in xml.items() if n.startswith("ppt/theme/")]
    assert themes
    for theme in themes:
        major_minor = re.findall(r"<a:(?:major|minor)Font>\s*<a:latin typeface=\"([^\"]+)\"", theme)
        assert major_minor and set(major_minor) <= ALLOWED_FONTS

    # every figure printed on slides 7-11 comes from an input (no invented numbers)
    numbers = _input_numbers(json.loads(paths["report"].read_text(encoding="utf-8")), BENCH)
    for slide in slides[6:11]:
        for text in _texts(slide):
            for token in NUMBER.findall(text):
                assert _traceable(token, numbers), (token, text)

    # slide 9 shows the MIA and utility values the report has
    nine = slides[8]
    shapes9 = {s.name: s for s in _shapes(nine)}
    assert "MIA" in shapes9["kpi:mia"].text_frame.text
    assert "0.51" in shapes9["kpi:mia"].text_frame.text
    assert "0.97" in shapes9["kpi:utility"].text_frame.text
    assert "utility" in shapes9["kpi:utility"].text_frame.text.lower()
    assert not {"placeholder:mia", "placeholder:utility"} & set(shapes9)

    # the steps the report lacks and the screenshots it lacks are placeholders, never figures
    assert "placeholder:screen-job" in _names(slides[6])
    assert "placeholder:screen-evidence" in _names(slides[10])
    assert "placeholder:s7" in _names(slides[7])
    assert "picture:screen-playground" in _names(slides[4])
    assert "picture:screen-scan-parallel" in _names(slides[9])
    assert result.missing  # and the build says so
    assert any("S7" in m for m in result.missing)

    # the bench file is drawn as a bar (a plain rectangle), labelled with its measured rate
    bars = [s for s in _shapes(slides[6]) if s.name.startswith("bar:")]
    assert len(bars) == 1
    assert any("941.6" in t for t in _texts(slides[6]))

    # no CRITICAL entity anywhere in the deck's words (slides and notes)
    words = [t for slide in slides for t in _texts(slide, chrome=True)]
    words += [slide.notes_slide.notes_text_frame.text for slide in slides]
    found = Detector(DetectorConfig()).detect(text="\n".join(words)).findings
    assert not [f.entity_type for f in found if f.sensitivity is Sensitivity.CRITICAL]

    assert result.out.stat().st_size < 20 * 1024 * 1024


def test_slide_nine_shows_placeholders_without_mia_and_utility(tmp_path: Path) -> None:
    facts = json.loads(json.dumps(S8_FACTS))
    facts["privacy"]["membership_inference"] = {"run": False, "reason": "holdout too small"}
    del facts["utility"]
    prs, result = _build(tmp_path, _inputs(tmp_path, {"S8": facts}))
    nine = list(prs.slides)[8]
    names = _names(nine)
    assert {"placeholder:mia", "placeholder:utility"} <= names
    assert not {"kpi:mia", "kpi:utility"} & names
    assert "kpi:fidelity" in names and "kpi:exact-rows" in names
    assert any("MIA" in m for m in result.missing)


def test_complete_report_fills_every_slot(tmp_path: Path) -> None:
    paths = _inputs(tmp_path, FULL_FACTS, screens=SCREENS_ALL)
    prs, result = _build(tmp_path, paths)
    slides = list(prs.slides)
    assert result.missing == ()
    for slide in slides:
        assert not [n for n in _names(slide) if n.startswith("placeholder:")], slide.slide_id
    numbers = _input_numbers(json.loads(paths["report"].read_text(encoding="utf-8")), BENCH)
    for slide in slides[4:11]:  # slides 5-11 carry the rehearsal's figures
        for text in _texts(slide):
            for token in NUMBER.findall(text):
                assert _traceable(token, numbers), (token, text)
    text10 = "\n".join(_texts(slides[9]))
    assert "POST requests received" in text10
    eleven = "\n".join(_texts(slides[10]))
    assert "11 of 11" in eleven and "raw PII sent to an LLM" in eleven
    eight = "\n".join(_texts(slides[7]))
    assert "500" in eight and "Oracle" in eight and "MongoDB" in eight and "PostgreSQL" in eight


def test_without_inputs_every_figure_is_a_placeholder(tmp_path: Path) -> None:
    out = tmp_path / "deck.pptx"
    result = build_deck.build_deck(
        out=out,
        report=tmp_path / "absent.json",
        screens=tmp_path / "absent-screens",
        bench=tmp_path / "absent-bench",
        script=SCRIPT,
    )
    slides = list(Presentation(str(out)).slides)
    assert len(slides) == 12
    for slide in slides[4:11]:
        assert [n for n in _names(slide) if n.startswith("placeholder:")]
        for text in _texts(slide):
            assert not NUMBER.findall(text), text
    assert any("report" in m for m in result.missing)
    # --strict turns any missing input into a failed build
    code = build_deck.main(
        [
            "--report",
            str(tmp_path / "absent.json"),
            "--screens",
            str(tmp_path / "absent-screens"),
            "--bench",
            str(tmp_path / "absent-bench"),
            "--script",
            str(SCRIPT),
            "--out",
            str(tmp_path / "strict.pptx"),
            "--strict",
        ]
    )
    assert code == 1


def test_a_step_that_did_not_pass_shows_no_figures(tmp_path: Path) -> None:
    paths = _inputs(tmp_path, {"S8": S8_FACTS})
    doc = json.loads(paths["report"].read_text(encoding="utf-8"))
    doc["steps"][0]["status"] = "FAIL"
    paths["report"].write_text(json.dumps(doc), encoding="utf-8")
    prs, result = _build(tmp_path, paths)
    nine = list(prs.slides)[8]
    assert not [n for n in _names(nine) if n.startswith("kpi:")]
    assert "placeholder:s8" in _names(nine)
    assert any("S8" in m and "FAIL" in m for m in result.missing)
    for text in _texts(nine):
        assert "0.93" not in text


def test_strings_that_are_not_names_never_reach_a_slide(tmp_path: Path) -> None:
    """The report is value-free by contract; the deck still prints only name-shaped strings."""
    s2 = dict(FULL_FACTS["S2"], entity_types=["EMAIL", "123-45-6789", "Jane Q Customer"])
    paths = _inputs(
        tmp_path,
        {"S2": s2},
        report_top={"tenant": "Jane Q Customer 123-45-6789", "commit": "not a sha!"},
    )
    prs, _ = _build(tmp_path, paths)
    words = "\n".join(t for slide in prs.slides for t in _texts(slide, chrome=True))
    assert "123-45-6789" not in words and "Jane Q Customer" not in words
    assert "not a sha" not in words
    assert "EMAIL" in words


def test_facts_of_an_unexpected_shape_become_placeholders(tmp_path: Path) -> None:
    steps = {
        "S2": dict(FULL_FACTS["S2"], entity_types=["not a name"], findings="13"),
        "S6": {"jobs": [{"engine": "sqlite", "counts": {"rows_written": 5}}]},
        "S7": dict(FULL_FACTS["S7"], pairs=[{"left": 0, "right": 9, "checked": 1, "matched": 1}]),
    }
    prs, result = _build(tmp_path, _inputs(tmp_path, steps, screens=SCREENS_ALL))
    slides = list(prs.slides)
    assert {"placeholder:findings", "placeholder:entity-types"} <= _names(slides[4])
    assert "placeholder:s6" in _names(slides[6])
    assert "placeholder:s7-pairs" in _names(slides[7])
    assert not [n for n in _names(slides[7]) if n.startswith("pair:")]
    assert any("S2 facts.findings" in m for m in result.missing)


def test_a_report_of_another_shape_is_refused(tmp_path: Path) -> None:
    bad = tmp_path / "report.json"
    bad.write_text(json.dumps({"schema": "something-else", "steps": []}), encoding="utf-8")
    with pytest.raises(build_deck.DeckInputError):
        build_deck.build_deck(out=tmp_path / "x.pptx", report=bad, script=SCRIPT)
    bad.write_text("{not json", encoding="utf-8")
    code = build_deck.main(["--report", str(bad), "--out", str(tmp_path / "y.pptx")])
    assert code == 2
    assert not (tmp_path / "y.pptx").exists()


def test_speaker_notes_come_from_the_demo_script(tmp_path: Path) -> None:
    notes = build_deck.script_notes(SCRIPT)
    assert sorted(notes) == list(range(1, 13))
    assert all(text.strip() for text in notes.values())
    prs, result = _build(tmp_path, _inputs(tmp_path, {"S8": S8_FACTS}))
    for number, slide in enumerate(prs.slides, start=1):
        assert slide.notes_slide.notes_text_frame.text == notes[number]
    assert not any("script" in m for m in result.missing)


def test_script_notes_join_wrapped_lines_and_skip_code(tmp_path: Path) -> None:
    script = tmp_path / "script.md"
    script.write_text(
        "# Title\n\n"
        "#### Slide 2 · Two\n\n"
        '> "First spoken line that wraps\n'
        '> onto a second line."\n'
        '> "Second **line** with `code`."\n'
        ">\n"
        "> - a list item\n\n"
        "```bash\n"
        "# a shell comment inside a fence is not a heading\n"
        "echo nothing\n"
        "```\n"
        "Still slide two.\n\n"
        "## Next beat\n\n"
        "Not a note.\n\n"
        "#### Slide 13 · out of range\n\nignored\n",
        encoding="utf-8",
    )
    assert build_deck.script_notes(script) == {
        2: "First spoken line that wraps onto a second line.\n"
        "Second line with code.\n"
        "• a list item\n"
        "Still slide two."
    }


def test_without_the_script_the_notes_fall_back_and_say_so(tmp_path: Path) -> None:
    out = tmp_path / "deck.pptx"
    result = build_deck.build_deck(out=out, script=tmp_path / "no-script.md")
    for slide in Presentation(str(out)).slides:
        assert slide.notes_slide.notes_text_frame.text.strip()
    assert any("script" in m for m in result.missing)


def test_cli_builds_the_deck(tmp_path: Path) -> None:
    paths = _inputs(tmp_path, {"S8": S8_FACTS, "S9": S9_FACTS})
    out = tmp_path / "cli.pptx"
    code = build_deck.main(
        [
            "--report",
            str(paths["report"]),
            "--screens",
            str(paths["screens"]),
            "--bench",
            str(paths["bench"]),
            "--script",
            str(SCRIPT),
            "--out",
            str(out),
        ]
    )
    assert code == 0 and out.stat().st_size > 0
