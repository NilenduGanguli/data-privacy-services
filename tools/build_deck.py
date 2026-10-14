"""Build the DPS showcase deck from the ten-minute demo's measured outputs (M5 Task 26B).

    python tools/build_deck.py [--report reports/e2e-demo-report.json]
        [--screens reports/demo-screens] [--bench docs/technical/bench]
        [--script docs/demo/DEMO-SCRIPT.md] [--out docs/demo/dps-showcase.pptx] [--strict]

Needs the dev-only ``deck`` extra (``uv pip install -e ".[deck]"``, python-pptx; never in an image).
Twelve 16:9 slides that accompany the demo of spec §9; each slide's speaker notes are the
``#### Slide N`` block of the matching beat in ``docs/demo/DEMO-SCRIPT.md``.

**Every figure was measured.** Numbers come only from the e2e demo report and the bench JSON. A
missing input (no report, a step that is absent or did not PASS, a fact the step lacks, a missing
screenshot or bench file) renders a slate placeholder box that says what is missing and how to get
it, is listed on stderr, and fails the build under ``--strict``. Nothing is derived (no sums, no
averages, no counts of lists), so every printed figure can be traced to an input value.

**Value-free.** The inputs carry ids, counts, durations, rates and scores only; screenshots are of
the demo stack (synthetic seed data). The builder still prints only numbers, booleans as words and
strings shaped like names (entity types, engines, tenant slugs, a git sha, a semver) — any other
string in the report is dropped.

**Keynote-safe** (the owner presents from Keynote, which drops PowerPoint chart parts and lacks
Cambria Bold): charts and diagrams are plain shapes (rectangles, ovals, straight connectors) and
text boxes — never chart parts, tables or SmartArt; every run names ``Helvetica Neue`` and the
theme font scheme names ``Arial`` (the fallback for anything inherited); shapes carry no theme
style, so no inherited shadow or font colour; solid fills only; Latin-1 / Mac Roman glyphs only.
The same inputs give byte-identical files (fixed zip timestamps, document dates from the report).

Input contract — ``reports/e2e-demo-report.json`` written by ``tools/e2e_demo.py``::

    {"schema": "dps.e2e-demo.v1", "generated_at": "<ISO-8601 UTC>", "dps_version": "<semver>",
     "commit": "<git sha>", "tenant": "<tenant id>", "total_seconds": <float>,
     "steps": [{"id": "S1", "title": "...", "status": "PASS|FAIL|NOT RUN|SKIP", "seconds": <float>,
                "facts": {...}}, ...]}

Facts the deck reads (dotted paths inside a step's ``facts``; anything else is ignored)::

    S1   raw_pii_sent_to_llm                         (reports summary tile)
    S2   findings, entity_types[], reveal_identical, raw_pii_sent_to_llm
    S4   ocr_pages, findings, extractable_chars, originals_left
    S6   jobs[]: engine, counts.rows_written, counts.partitions, timings.rows_per_s,
         verification.passed                         (subset of each run's evidence pack)
    S7   members[].engine, pairs[]: left, right, checked, matched; sampled,
         all_members_matched, passed                 (subset of the consistency-check result)
    S8   counts.rows_written, fidelity.score, privacy.exact_row_matches,
         privacy.nndr.{ratio,target,measured}, privacy.membership_inference.{run,auc,target},
         utility.{run,ratio,metric}                  (subset of the synthetic evidence pack)
    S9   scan.{pages,fields,personas,submissions}, posts_received (demo-site /__received)
    S10  packs_listed, audit_rows, pdf_ok
    S11  posture.{passed,total}, rotated_key_status, retention_state

Screenshots — ``reports/demo-screens/<page>-<theme>.png`` from ``e2e_demo.py --console``; the deck
uses the light theme of ``playground`` (slide 5), ``job`` (7), ``scan-parallel`` (10) and
``evidence`` (11). Bench — every ``*.json`` of ``tools/bench_connector.py`` in ``--bench``.

QA (manual, on the Mac): export through Keynote and page through the PDF —
``osascript -e 'tell application "Keynote" to export (open POSIX file "<abs pptx>") to POSIX file
"<abs>/reports/deck.pdf" as PDF'``: every chart visible, no font substitution, no text overflow.
"""

from __future__ import annotations

import argparse
import io
import json
import math
import os
import re
import sys
import tempfile
import zipfile
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT = ROOT / "reports" / "e2e-demo-report.json"
DEFAULT_SCREENS = ROOT / "reports" / "demo-screens"
DEFAULT_BENCH = ROOT / "docs" / "technical" / "bench"
DEFAULT_SCRIPT = ROOT / "docs" / "demo" / "DEMO-SCRIPT.md"
DEFAULT_OUT = ROOT / "docs" / "demo" / "dps-showcase.pptx"

REPORT_SCHEMA = "dps.e2e-demo.v1"
SLIDES = 12
FONT = "Helvetica Neue"  # every run; Keynote's native sans
FALLBACK_FONT = "Arial"  # the theme font scheme: whatever a run does not name
MAX_IMAGE_PX = 2400  # wider screenshots are downscaled before embedding (deck stays small)
MAX_BARS = 8

# Vault tokens as fixed colours (spec §9): ink, paper, and the four signals of the light theme.
INK = "0E141B"
PAPER = "F4F6F8"
SURFACE = "FFFFFF"
BORDER = "E0E5F0"
INK_2 = "4D5875"
INK_3 = "7E89A6"
TEAL = "0F766E"  # protected
AMBER = "B45309"  # detected / needs a decision
EMERALD = "047857"  # verified
ROSE = "BE123C"  # critical / leak
TEAL_BRIGHT = "2DD4BF"  # signals on ink (the dark theme's values)
AMBER_BRIGHT = "FBBF24"
EMERALD_BRIGHT = "34D399"
ROSE_BRIGHT = "FB7185"
INK_ON_DARK = "A9B4CC"
SLATE_FILL = "E2E8F0"  # placeholders: slate, dashed
SLATE_LINE = "94A3B8"
SLATE_TEXT = "475569"

STEP_STATUSES = {"FAIL", "NOT RUN", "SKIP", "ERROR"}  # anything else that is not PASS: "unknown"
ENGINES = {"postgres": "PostgreSQL", "oracle": "Oracle", "mongo": "MongoDB"}
WRITE_MODES = {"table": "table", "files": "files", "in_place": "in place", "append": "append"}
MODES = {"obfuscate": "obfuscate", "synthesize": "synthesize"}
STATES = {"succeeded", "failed", "canceled", "dead", "running", "waiting", "queued"}
UTILITY_METRICS = {"accuracy": "accuracy", "r2": "R squared"}
ENTITY_NAME = re.compile(r"^[A-Z][A-Z0-9_]{1,31}$")
TENANT_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")
COMMIT_NAME = re.compile(r"^[0-9a-f]{7,40}$")
VERSION_NAME = re.compile(r"^\d{1,4}\.\d{1,4}\.\d{1,4}$")
SLIDE_HEADING = re.compile(r"^#{4}\s+Slide\s+(\d{1,2})\b")

EMU_PER_INCH = 914400


class DeckInputError(ValueError):
    """An input exists but is not what the deck reads (wrong schema, unreadable JSON)."""


class MissingInputs(DeckInputError):
    """``strict`` and at least one input is missing; nothing was written."""

    def __init__(self, missing: tuple[str, ...]) -> None:
        super().__init__(f"{len(missing)} inputs missing")
        self.missing = missing


@dataclass(frozen=True)
class BuildResult:
    """What the build wrote and which inputs were missing (each one is a placeholder on a slide)."""

    out: Path
    slides: int
    missing: tuple[str, ...]


# =================================================================================================
# Inputs
# =================================================================================================
class _Absent:
    """A fact that is not available, with the reason shown on the slide's placeholder."""

    def __init__(self, what: str, how: str) -> None:
        self.what = what
        self.how = how


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value) if math.isfinite(value) else None


def _name(value: Any, pattern: re.Pattern[str]) -> str | None:
    return value if isinstance(value, str) and pattern.fullmatch(value) else None


@dataclass
class Report:
    """The e2e demo report, read defensively (only shapes the deck understands are used)."""

    path: Path
    doc: dict[str, Any] | None

    @classmethod
    def load(cls, path: Path | None) -> Report:
        """Read ``path``; an absent file is an empty report, a malformed one an error."""
        if path is None or not path.is_file():
            return cls(path or DEFAULT_REPORT, None)
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DeckInputError(f"report {path.name} is not readable JSON") from exc
        if not isinstance(doc, dict) or doc.get("schema") != REPORT_SCHEMA:
            raise DeckInputError(f"report {path.name} is not a {REPORT_SCHEMA} document")
        if not isinstance(doc.get("steps"), list):
            raise DeckInputError(f"report {path.name} has no steps list")
        return cls(path, doc)

    def _step(self, sid: str) -> dict[str, Any] | _Absent:
        if self.doc is None:
            return _Absent(
                "the e2e demo report",
                f"No {self.path.name}: run tools/e2e_demo.py --console, then rebuild the deck.",
            )
        for step in self.doc["steps"]:
            if isinstance(step, dict) and step.get("id") == sid:
                status = step.get("status")
                if status != "PASS":
                    shown = status if status in STEP_STATUSES else "unknown"
                    return _Absent(
                        f"step {sid} (status {shown})",
                        f"Step {sid} did not pass in the rehearsal: fix it and rehearse again.",
                    )
                facts = step.get("facts")
                return facts if isinstance(facts, dict) else {}
        return _Absent(f"step {sid}", f"The rehearsal report has no step {sid}.")

    def step_ok(self, sid: str) -> _Absent | None:
        """``None`` when step ``sid`` passed, else why its figures cannot be shown."""
        got = self._step(sid)
        return got if isinstance(got, _Absent) else None

    def raw(self, sid: str, path: str) -> Any:
        """The value at ``path`` in step ``sid``'s facts, or an :class:`_Absent`."""
        got: Any = self._step(sid)
        if isinstance(got, _Absent):
            return got
        for part in path.split("."):
            if not isinstance(got, dict) or part not in got:
                return _Absent(
                    f"{sid} facts.{path}", f"The report's step {sid} lacks facts.{path}."
                )
            got = got[part]
        return got

    def number(self, sid: str, path: str) -> float | _Absent:
        """A finite number (never a bool) at ``path``, or an :class:`_Absent`."""
        got = self.raw(sid, path)
        if isinstance(got, _Absent):
            return got
        num = _number(got)
        if num is None:
            return _Absent(f"{sid} facts.{path}", f"{sid} facts.{path} is not a number.")
        return num

    def flag(self, sid: str, path: str) -> bool | _Absent:
        """A boolean at ``path``, or an :class:`_Absent`."""
        got = self.raw(sid, path)
        if isinstance(got, _Absent):
            return got
        if not isinstance(got, bool):
            return _Absent(f"{sid} facts.{path}", f"{sid} facts.{path} is not true or false.")
        return got

    def top(self, key: str, pattern: re.Pattern[str]) -> str | None:
        """A name-shaped top-level string (tenant, commit, version), else ``None``."""
        return None if self.doc is None else _name(self.doc.get(key), pattern)

    def generated_at(self) -> datetime | None:
        """When the rehearsal ran (UTC), when the report says so."""
        raw = None if self.doc is None else self.doc.get("generated_at")
        if not isinstance(raw, str):
            return None
        try:
            stamp = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
        return stamp.astimezone(UTC) if stamp.tzinfo else stamp.replace(tzinfo=UTC)

    def total_seconds(self) -> float | None:
        """The demo flow's wall time."""
        return None if self.doc is None else _number(self.doc.get("total_seconds"))


@dataclass(frozen=True)
class Bar:
    """One bench result drawn as a bar."""

    key: tuple[int, str, str, int, int]
    label: str
    value: float
    host_cpus: float | None


def load_bench(directory: Path | None) -> list[Bar]:
    """The succeeded bench runs in ``directory``: newest file per configuration, at most eight."""
    if directory is None or not directory.is_dir():
        return []
    latest: dict[tuple[int, str, str, int, int], Bar] = {}
    order = list(ENGINES)
    for path in sorted(directory.glob("*.json")):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if not isinstance(doc, dict) or doc.get("state") != "succeeded":
            continue
        rate = _number(doc.get("rows_per_second"))
        engine = doc.get("engine", "postgres")
        mode, write_mode = doc.get("mode"), doc.get("write_mode")
        workers, parallelism = _number(doc.get("workers_online")), _number(doc.get("parallelism"))
        if (
            rate is None
            or rate <= 0
            or engine not in ENGINES
            or mode not in MODES
            or write_mode not in WRITE_MODES
            or workers is None
            or parallelism is None
        ):
            continue
        n_workers, n_par = int(workers), int(parallelism)
        label = (
            f"{ENGINES[engine]} {MODES[mode]} into {WRITE_MODES[write_mode]} · "
            f"{n_workers} worker{'s' if n_workers != 1 else ''} · parallelism {n_par}"
        )
        host = doc.get("host")
        cpus = _number(host.get("cpus")) if isinstance(host, dict) else None
        key = (order.index(engine), mode, write_mode, n_workers, n_par)
        latest[key] = Bar(key=key, label=label, value=rate, host_cpus=cpus)  # sorted: newest wins
    return [latest[k] for k in sorted(latest)][:MAX_BARS]


def find_screen(directory: Path | None, page: str) -> Path | None:
    """``<page>-light.png`` (else ``<page>.png``) in the screenshots directory."""
    if directory is None or not directory.is_dir():
        return None
    for name in (f"{page}-light.png", f"{page}.png"):
        candidate = directory / name
        if candidate.is_file():
            return candidate
    return None


def script_notes(path: Path | None) -> dict[int, str]:
    """Speaker notes per slide: the ``#### Slide N`` blocks of the demo script.

    A block runs from its heading to the next heading (fenced code is skipped). Blockquote markers,
    emphasis and code ticks are dropped; wrapped lines join into one paragraph per spoken line (a
    paragraph starts at a blank line, an opening quote or a list item) and a paragraph wrapped in
    straight quotes loses them. Paragraphs are separated by newlines.
    """
    if path is None or not path.is_file():
        return {}
    notes: dict[int, list[list[str]]] = {}
    current: int | None = None
    fenced = False
    for raw in path.read_text(encoding="utf-8").splitlines():
        if raw.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if fenced:
            continue
        heading = SLIDE_HEADING.match(raw)
        if heading:
            current = int(heading.group(1))
            notes[current] = [[]]
            continue
        if raw.startswith("#"):
            current = None
            continue
        if current is None:
            continue
        line = raw.strip()
        while line.startswith(">"):
            line = line[1:].strip()
        line = line.replace("**", "").replace("`", "")
        paras = notes[current]
        if not line:
            if paras[-1]:
                paras.append([])
            continue
        if line.startswith("- "):
            line = "• " + line[2:]
        if paras[-1] and (line.startswith(('"', "• "))):
            paras.append([])
        paras[-1].append(line)
    out: dict[int, str] = {}
    for number, paras in notes.items():
        texts = []
        for words in paras:
            text = " ".join(words)
            if len(text) > 1 and text.startswith('"') and text.endswith('"'):
                text = text[1:-1]
            if text:
                texts.append(text)
        if texts and 1 <= number <= SLIDES:
            out[number] = "\n".join(texts)
    return out


# =================================================================================================
# Drawing (python-pptx is imported lazily: the deck extra is dev-only)
# =================================================================================================
def _emu(inches: float) -> int:
    return int(round(inches * EMU_PER_INCH))


def _fmt_count(value: float) -> str:
    return f"{value:,.0f}"


def _fmt_rate(value: float) -> str:
    return f"{value:,.1f}"


def _fmt_ratio(value: float) -> str:
    return f"{value:.2f}"


@dataclass
class Run:
    """A run of text with its look."""

    text: str
    size: float = 14
    color: str = INK
    bold: bool = False


@dataclass
class Para:
    """A paragraph of runs."""

    runs: list[Run]
    align: str = "left"
    space_after: float = 0


@dataclass
class Deck:
    """The presentation being built, plus the missing-input ledger."""

    prs: Any
    missing: list[str] = field(default_factory=list)
    slide_no: int = 0

    # -- primitives -------------------------------------------------------------------------------
    def _plain(self, shape: Any) -> Any:
        from pptx.oxml.ns import qn  # noqa: PLC0415

        style = shape._element.find(qn("p:style"))
        if style is not None:  # no inherited theme fill, outline, shadow or font colour
            shape._element.remove(style)
        return shape

    def _fill(
        self, shape: Any, fill: str | None, line: str | None, width: float, dash: bool
    ) -> None:
        from pptx.dml.color import RGBColor  # noqa: PLC0415
        from pptx.enum.dml import MSO_LINE_DASH_STYLE  # noqa: PLC0415
        from pptx.util import Pt  # noqa: PLC0415

        if fill is None:
            shape.fill.background()
        else:
            shape.fill.solid()
            shape.fill.fore_color.rgb = RGBColor.from_string(fill)
        if line is None:
            shape.line.fill.background()
        else:
            shape.line.color.rgb = RGBColor.from_string(line)
            shape.line.width = Pt(width)
            if dash:
                shape.line.dash_style = MSO_LINE_DASH_STYLE.DASH

    def write(
        self, frame: Any, paras: Sequence[Para], *, anchor: str = "top", inset: float = 0.08
    ) -> None:
        """Fill a text frame with explicit fonts on every run (Keynote-safe)."""
        from pptx.dml.color import RGBColor  # noqa: PLC0415
        from pptx.enum.text import MSO_ANCHOR, MSO_AUTO_SIZE, PP_ALIGN  # noqa: PLC0415
        from pptx.util import Pt  # noqa: PLC0415

        frame.word_wrap = True
        frame.auto_size = MSO_AUTO_SIZE.NONE
        frame.vertical_anchor = {
            "top": MSO_ANCHOR.TOP,
            "middle": MSO_ANCHOR.MIDDLE,
            "bottom": MSO_ANCHOR.BOTTOM,
        }[anchor]
        frame.margin_left = frame.margin_right = _emu(inset)
        frame.margin_top = frame.margin_bottom = _emu(inset * 0.6)
        aligns = {"left": PP_ALIGN.LEFT, "center": PP_ALIGN.CENTER, "right": PP_ALIGN.RIGHT}
        for index, para in enumerate(paras):
            p = frame.paragraphs[0] if index == 0 else frame.add_paragraph()
            p.alignment = aligns[para.align]
            p.space_after = Pt(para.space_after)
            for item in para.runs:
                run = p.add_run()
                run.text = item.text
                run.font.name = FONT
                run.font.size = Pt(item.size)
                run.font.bold = item.bold
                run.font.color.rgb = RGBColor.from_string(item.color)

    def text(
        self,
        slide: Any,
        box: tuple[float, float, float, float],
        paras: Sequence[Para] | str,
        *,
        size: float = 14,
        color: str = INK,
        bold: bool = False,
        align: str = "left",
        anchor: str = "top",
        name: str | None = None,
        inset: float = 0.0,
    ) -> Any:
        """A text box (no fill, no outline)."""
        x, y, w, h = box
        shape = slide.shapes.add_textbox(_emu(x), _emu(y), _emu(w), _emu(h))
        if isinstance(paras, str):
            paras = [Para([Run(paras, size, color, bold)], align)]
        self.write(shape.text_frame, paras, anchor=anchor, inset=inset)
        if name:
            shape.name = name
        return shape

    def rect(
        self,
        slide: Any,
        box: tuple[float, float, float, float],
        *,
        fill: str | None = SURFACE,
        line: str | None = BORDER,
        width: float = 0.75,
        radius: float | None = 0.06,
        dash: bool = False,
        name: str | None = None,
        paras: Sequence[Para] | None = None,
        anchor: str = "top",
        inset: float = 0.16,
        oval: bool = False,
    ) -> Any:
        """A (rounded) rectangle or oval, optionally holding text."""
        from pptx.enum.shapes import MSO_SHAPE  # noqa: PLC0415

        x, y, w, h = box
        kind = (
            MSO_SHAPE.OVAL
            if oval
            else (MSO_SHAPE.ROUNDED_RECTANGLE if radius else MSO_SHAPE.RECTANGLE)
        )
        shape = self._plain(slide.shapes.add_shape(kind, _emu(x), _emu(y), _emu(w), _emu(h)))
        if radius and not oval:
            shape.adjustments[0] = radius
        self._fill(shape, fill, line, width, dash)
        if paras is not None:
            self.write(shape.text_frame, paras, anchor=anchor, inset=inset)
        if name:
            shape.name = name
        return shape

    def line(
        self,
        slide: Any,
        start: tuple[float, float],
        end: tuple[float, float],
        *,
        color: str = INK_3,
        width: float = 1.25,
        arrow: bool = True,
        dash: bool = False,
    ) -> Any:
        """A straight connector, with a triangle head at ``end``."""
        from lxml import etree  # noqa: PLC0415
        from pptx.dml.color import RGBColor  # noqa: PLC0415
        from pptx.enum.dml import MSO_LINE_DASH_STYLE  # noqa: PLC0415
        from pptx.enum.shapes import MSO_CONNECTOR  # noqa: PLC0415
        from pptx.oxml.ns import qn  # noqa: PLC0415
        from pptx.util import Pt  # noqa: PLC0415

        shape = self._plain(
            slide.shapes.add_connector(
                MSO_CONNECTOR.STRAIGHT, _emu(start[0]), _emu(start[1]), _emu(end[0]), _emu(end[1])
            )
        )
        shape.line.color.rgb = RGBColor.from_string(color)
        shape.line.width = Pt(width)
        if dash:
            shape.line.dash_style = MSO_LINE_DASH_STYLE.DASH
        if arrow:
            tail = etree.SubElement(shape.line._get_or_add_ln(), qn("a:tailEnd"))
            tail.set("type", "triangle")
            tail.set("w", "med")
            tail.set("len", "med")
        return shape

    def absent(
        self, slide: Any, box: tuple[float, float, float, float], key: str, why: _Absent
    ) -> Any:
        """A slate placeholder box saying what is missing and how to get it (and log it)."""
        self.missing.append(f"slide {self.slide_no}: {why.what}")
        height = box[3]
        if height < 0.5:  # a one-line slot: what is missing, no room for how
            paras = [Para([Run(f"Not available: {why.what}", 9, SLATE_TEXT, True)])]
        else:
            small = height < 1.0
            paras = [
                Para(
                    [Run(f"Not available: {why.what}", 10 if small else 12, SLATE_TEXT, True)],
                    "center",
                    2,
                ),
                Para([Run(why.how, 9 if small else 10.5, SLATE_TEXT)], "center"),
            ]
        return self.rect(
            slide,
            box,
            fill=SLATE_FILL,
            line=SLATE_LINE,
            width=1.0,
            dash=True,
            radius=0.04,
            name=f"placeholder:{key}",
            anchor="middle",
            inset=0.12 if height >= 0.5 else 0.06,
            paras=paras,
        )

    def picture(
        self, slide: Any, box: tuple[float, float, float, float], path: Path | None, slot: str
    ) -> Any:
        """A screenshot fitted into ``box`` (aspect kept, top-left), else a placeholder."""
        from PIL import Image, UnidentifiedImageError  # noqa: PLC0415
        from pptx.dml.color import RGBColor  # noqa: PLC0415
        from pptx.util import Pt  # noqa: PLC0415

        if path is None:
            return self.absent(
                slide,
                box,
                f"screen-{slot}",
                _Absent(
                    f"screenshot {slot}-light.png",
                    "Run tools/e2e_demo.py --console (it writes reports/demo-screens), "
                    "then rebuild.",
                ),
            )
        try:
            with Image.open(path) as img:
                img.load()
                width, height = img.size
                blob: io.BytesIO | str = str(path)
                if width > MAX_IMAGE_PX:
                    scaled = img.convert("RGB").resize(
                        (MAX_IMAGE_PX, round(height * MAX_IMAGE_PX / width))
                    )
                    buffer = io.BytesIO()
                    scaled.save(buffer, format="PNG")
                    buffer.seek(0)
                    blob = buffer
                    width, height = scaled.size
        except (OSError, UnidentifiedImageError):
            return self.absent(
                slide,
                box,
                f"screen-{slot}",
                _Absent(
                    f"screenshot {path.name} (unreadable)", "Re-run the console walk to rewrite it."
                ),
            )
        x, y, w, h = box
        scale = min(w / width, h / height)
        pw, ph = width * scale, height * scale
        # top-left aligned, so it lines up with the column beside it
        pic = slide.shapes.add_picture(blob, _emu(x), _emu(y), _emu(pw), _emu(ph))
        pic.line.color.rgb = RGBColor.from_string(BORDER)
        pic.line.width = Pt(0.75)
        pic.name = f"picture:screen-{slot}"
        return pic

    # -- composites -------------------------------------------------------------------------------
    def kpi(
        self,
        slide: Any,
        box: tuple[float, float, float, float],
        key: str,
        label: str,
        value: str,
        *,
        tone: str = TEAL,
        caption: str = "",
        value_size: float = 34,
    ) -> Any:
        """A white card: label, big value in the signal colour, caption."""
        paras = [
            Para([Run(label, 11, INK_2, True)], space_after=2),
            Para([Run(value, value_size, tone, True)], space_after=2),
        ]
        if caption:
            paras.append(Para([Run(caption, 10, INK_3)]))
        return self.rect(slide, box, name=f"kpi:{key}", paras=paras, inset=0.16)

    def chips(
        self, slide: Any, x: float, y: float, width: float, names: Iterable[str], tone: str = TEAL
    ) -> float:
        """Entity-type pills that wrap within ``width``; returns the y below the last row."""
        soft = {TEAL: "E7F1F0", AMBER: "F7EEE6", EMERALD: "E6F2EE", ROSE: "F8E7EB"}[tone]
        cx, cy, row_h = x, y, 0.34
        for name in names:
            w = 0.34 + 0.1 * len(name)  # bold capitals at 10 pt, generous: a pill never wraps
            if cx + w > x + width and cx > x:
                cx, cy = x, cy + row_h + 0.08
            self.rect(
                slide,
                (cx, cy, w, row_h),
                fill=soft,
                line=tone,
                width=0.75,
                radius=0.5,
                name=f"chip:{name}",
                anchor="middle",
                inset=0.06,
                paras=[Para([Run(name, 10, tone, True)], "center")],
            )
            cx += w + 0.08
        return cy + row_h

    def status(
        self, slide: Any, x: float, y: float, ok: bool, text: str, *, size: float = 12
    ) -> None:
        """A coloured dot and a one-line statement."""
        self.rect(
            slide, (x, y + 0.07, 0.16, 0.16), fill=EMERALD if ok else ROSE, line=None, oval=True
        )
        self.text(slide, (x + 0.26, y, 4.2, 0.34), text, size=size, color=INK)

    # -- slide frame ------------------------------------------------------------------------------
    def new_slide(self, *, kicker: str, title: str, lede: str = "", dark: bool = False) -> Any:
        """A slide with the Title Only layout, a kicker, the title placeholder and a lede."""
        from pptx.dml.color import RGBColor  # noqa: PLC0415

        self.slide_no += 1
        slide = self.prs.slides.add_slide(self.prs.slide_layouts[5])  # "Title Only"
        background = slide.background.fill
        background.solid()
        background.fore_color.rgb = RGBColor.from_string(INK if dark else PAPER)
        title_shape = slide.shapes.title
        if dark:
            title_shape.left, title_shape.top = _emu(0.9), _emu(2.35)
            title_shape.width, title_shape.height = _emu(11.5), _emu(1.3)
            self.write(
                title_shape.text_frame,
                [Para([Run(title, 54, "FFFFFF", True)])],
                anchor="bottom",
                inset=0,
            )
        else:
            title_shape.left, title_shape.top = _emu(0.6), _emu(0.66)
            title_shape.width, title_shape.height = _emu(12.13), _emu(0.72)
            self.write(
                title_shape.text_frame, [Para([Run(title, 28, INK, True)])], anchor="top", inset=0
            )
            self.text(
                slide, (0.6, 0.36, 9.0, 0.3), kicker, size=11, color=TEAL, bold=True, name="kicker"
            )
            if lede:
                self.text(slide, (0.6, 1.34, 12.1, 0.62), lede, size=15, color=INK_2, name="lede")
        return slide

    def chrome(self, slide: Any, footer: str, *, dark: bool = False) -> None:
        """Footer and page number (named ``chrome:*``: not figures, excluded from the checks)."""
        tone = INK_ON_DARK if dark else INK_3
        self.text(slide, (0.6, 7.04, 9.5, 0.28), footer, size=9, color=tone, name="chrome:footer")
        self.text(
            slide,
            (11.13, 7.04, 1.6, 0.28),
            f"{self.slide_no} / {SLIDES}",
            size=9,
            color=tone,
            align="right",
            name="chrome:page",
        )


# =================================================================================================
# The twelve slides
# =================================================================================================
#: Notes used only when the demo script lacks a slide's block (the build lists that as missing).
DEFAULT_NOTES = {
    1: "One Privacy Engine, three entry points, two outcomes, measured privacy. "
    "No LLM ever sees a raw value.",
    2: "Production data keeps reaching lower environments, vendors and LLM pipelines. DPS offers "
    "three doors: the API, database connectors and the UI scanner.",
    3: "Three promises: the LLM never sees raw PII; the caller holds the key and DPS stores none; "
    "every job leaves evidence, not claims.",
    4: "One image runs four roles: app, worker, browser-worker and migrate. The metadata database "
    "never holds a customer value.",
    5: "The Playground: paste a KYC record, see every finding with its detector evidence, "
    "obfuscate, keep the key, reveal.",
    6: "Documents: a scanned KYC page is read by OCR, the findings are burned out of page images, "
    "and nothing remains in a text layer.",
    7: "Layer 2: discover, review, run, watch the partitions, open the evidence pack. Throughput "
    "was measured, not estimated.",
    8: "One job group key across Oracle, MongoDB and PostgreSQL: the same customer id becomes the "
    "same token in every system.",
    9: "Synthetic data is measured before anything is written: fidelity, exact-row matches, "
    "nearest-neighbour distance, membership inference and utility.",
    10: "Layer 3: the scanner fills every screen with synthetic or obfuscated data in a parallel "
    "view. Nothing is ever submitted.",
    11: "Every job seals an evidence pack with a PDF; staging and production refuse to boot when a "
    "posture check fails.",
    12: "Decisions D1 to D4 were taken by the owner, D5 to D13 stand at their defaults, D14 is "
    "still to be set together.",
}

DECISIONS: tuple[tuple[str, str, str, str], ...] = (
    ("D1", "Repository", "own git repo, nested in document_intelligence", "Decided"),
    ("D2", "Key custody", "stateless: the key is returned, DPS stores none", "Decided"),
    ("D3", "Write modes", "in place, append, table, files; confirmed writes", "Decided"),
    ("D4", "UI scanner", "inventory and parallel view; no submissions at all", "Decided"),
    ("D5", "Jurisdictions", "US, CA, MX, IN, UK and EU; NER in EN and ES", "Default"),
    ("D6", "LLM path", "Stellar with COIN OAuth2; Vertex on laptops; stub in CI", "Default"),
    ("D7", "Synthesis", "surrogates and the built-in copula; MOSTLY AI optional", "Default"),
    ("D8", "Reversible tokens", "AES-SIV by default; FF1 after a licence review", "Default"),
    ("D9", "Deployment", "compose locally; OpenShift through kustomize", "Default"),
    ("D10", "Documents", "obfuscated text and findings; true redacted PDF", "Default"),
    ("D11", "Retention", "job metadata and evidence per tenant; raw values never kept", "Default"),
    ("D12", "Tenancy", "X-API-KEY with tenant grants and scopes; SSO later", "Default"),
    ("D13", "Naming", "Data Privacy Services, Privacy Console, Vault", "Default"),
    ("D14", "Scale targets", "payload sizes, rows per job, jobs and documents per day", "Open"),
)
NEXT_STEPS = (
    "Set the scale targets together (D14): they size batches, caps and the benchmark goals.",
    "Run the FF1 licence review before format-preserving encryption is switched on (D8).",
    "Connect the bank's tokenization service through the external_tokenize adapter.",
    "Front the console with the bank's SSO and agree the quota day boundary (UTC today).",
)


def _footer(report: Report) -> str:
    parts = ["Data Privacy Services · Privacy Console"]
    stamp = report.generated_at()
    if stamp is not None:
        parts.append(f"rehearsal {stamp:%Y-%m-%d %H:%M} UTC")
    commit = report.top("commit", COMMIT_NAME)
    if commit:
        parts.append(f"commit {commit[:7]}")
    return " · ".join(parts)


def _slide_title(deck: Deck, report: Report) -> Any:
    version = report.top("dps_version", VERSION_NAME)
    slide = deck.new_slide(kicker="", title="Data Privacy Services", dark=True)
    deck.text(
        slide,
        (0.9, 1.75, 9.0, 0.4),
        "PRIVACY CONSOLE" + (f" · v{version}" if version else ""),
        size=13,
        color=TEAL_BRIGHT,
        bold=True,
        name="kicker",
    )
    deck.text(
        slide,
        (0.9, 3.75, 11.0, 1.1),
        "One Privacy Engine, three entry points, two outcomes, measured privacy. "
        "No LLM ever sees a raw value.",
        size=22,
        color=INK_ON_DARK,
    )
    for i, tone in enumerate((TEAL_BRIGHT, AMBER_BRIGHT, EMERALD_BRIGHT, ROSE_BRIGHT)):
        deck.rect(slide, (0.9 + i * 0.55, 5.15, 0.45, 0.08), fill=tone, line=None, radius=None)
    stamp, total = report.generated_at(), report.total_seconds()
    if stamp is not None and total is not None:
        seconds = round(total)
        deck.text(
            slide,
            (0.9, 5.45, 11.0, 0.4),
            f"Built from the rehearsal of {stamp:%d %B %Y}: the scripted demo ran end to end "
            f"in {seconds // 60}:{seconds % 60:02d}.",
            size=14,
            color=INK_ON_DARK,
        )
    elif report.doc is None:
        deck.absent(
            slide,
            (0.9, 5.45, 7.2, 0.75),
            "rehearsal",
            _Absent(
                "the e2e demo report", "Run tools/e2e_demo.py --console, then rebuild the deck."
            ),
        )
    else:
        deck.absent(
            slide,
            (0.9, 5.45, 7.2, 0.75),
            "rehearsal",
            _Absent(
                "report generated_at and total_seconds", "The report does not say when or how long."
            ),
        )
    deck.chrome(slide, _footer(report), dark=True)
    return slide


def _slide_problem(deck: Deck, report: Report) -> Any:
    slide = deck.new_slide(
        kicker="WHY DPS",
        title="Customer data keeps reaching places it should not",
        lede="Test, UAT, analytics, vendors and LLM pipelines all want realistic data. DPS "
        "hands them a protected copy instead, through whichever door the data already uses.",
    )
    cards = (
        (
            "L1 · API",
            "JSON, text and documents",
            "One HTTPS call de-identifies a payload, redacts a scanned PDF or mints test fixtures.",
        ),
        (
            "L2 · Connectors",
            "Oracle, PostgreSQL, MongoDB",
            "Batch jobs: discover, review, run, evidence. Masked or synthetic UAT copies, "
            "consistent across systems.",
        ),
        (
            "L3 · UI scanner",
            "Web front ends",
            "What each screen collects, and a parallel view filled with safe data. "
            "Nothing is ever submitted.",
        ),
    )
    for i, (kick, head, body) in enumerate(cards):
        deck.rect(
            slide,
            (0.6 + i * 4.1, 2.2, 3.9, 2.45),
            name=f"card:{kick}",
            inset=0.22,
            paras=[
                Para([Run(kick.upper(), 11, TEAL, True)], space_after=6),
                Para([Run(head, 19, INK, True)], space_after=8),
                Para([Run(body, 13, INK_2)]),
            ],
        )
    outcomes = (
        (TEAL, "Obfuscated", "format kept, reversible only with the key the caller holds"),
        (EMERALD, "Synthetic", "realistic and consistent, with no path back to the original"),
    )
    for i, (tone, head, body) in enumerate(outcomes):
        deck.rect(
            slide,
            (0.6 + i * 6.15, 4.95, 5.95, 0.95),
            fill={TEAL: "E7F1F0", EMERALD: "E6F2EE"}[tone],
            line=tone,
            name=f"outcome:{head}",
            anchor="middle",
            inset=0.22,
            paras=[Para([Run(f"{head}  ", 16, tone, True), Run(body, 13, INK_2)])],
        )
    deck.text(
        slide,
        (0.6, 6.1, 12.1, 0.5),
        "Three entry points, two outcomes, one policy model, one detector stack, "
        "one transformation engine.",
        size=13,
        color=INK_3,
    )
    deck.chrome(slide, _footer(report))
    return slide


def _slide_principles(deck: Deck, report: Report) -> Any:
    slide = deck.new_slide(
        kicker="PRINCIPLES",
        title="Three promises, built to be proved",
        lede="Each promise has a mechanism in the code and a measurement in the evidence.",
    )
    cards = (
        (
            TEAL,
            "P1",
            "The LLM never sees raw PII",
            "Detection and transformation run locally. The gateway receives typed placeholders "
            "only, a verifier checks every call, and a per-tenant counter of raw values sent must "
            "read zero.",
            "the counter on Overview, in Reports and in every API report block",
        ),
        (
            AMBER,
            "D2",
            "The caller holds the key",
            "The data key is returned in the response, optionally wrapped with the caller's "
            "public key. DPS stores no key; reveal works only with the key the caller passes back.",
            "the key card in the Playground; every evidence pack records key custody",
        ),
        (
            EMERALD,
            "P6",
            "Evidence, not claims",
            "Every job seals an evidence pack: findings, methods, verification, fidelity and "
            "privacy measurements, and the hash of the configuration it ran with.",
            "the evidence pack and its PDF, for every job and every scan",
        ),
    )
    for i, (tone, tag, head, body, proof) in enumerate(cards):
        x = 0.6 + i * 4.1
        deck.rect(
            slide,
            (x, 2.2, 3.9, 3.6),
            radius=None,
            name=f"card:{tag}",
            inset=0.24,
            paras=[
                Para([Run(tag, 13, tone, True)], space_after=6),
                Para([Run(head, 20, INK, True)], space_after=10),
                Para([Run(body, 14, INK_2)], space_after=14),
                Para([Run("Proved by: ", 12, tone, True), Run(proof, 12, INK_2)]),
            ],
        )
        deck.rect(
            slide, (x, 2.2, 3.9, 0.09), fill=tone, line=None, radius=None, name=f"accent:{tag}"
        )
    deck.chrome(slide, _footer(report))
    return slide


def _slide_architecture(deck: Deck, report: Report) -> Any:
    slide = deck.new_slide(
        kicker="ARCHITECTURE",
        title="One engine, four roles, one metadata database",
        lede="One container image runs every role. The metadata database holds jobs, hashed keys, "
        "evidence and the audit log, never a customer value.",
    )

    def node(
        box: tuple[float, float, float, float],
        head: str,
        sub: str,
        *,
        tone: str = INK,
        fill: str = SURFACE,
        line: str = BORDER,
    ) -> None:
        deck.rect(
            slide,
            box,
            fill=fill,
            line=line,
            name=f"node:{head}",
            anchor="middle",
            inset=0.12,
            paras=[
                Para([Run(head, 13, tone, True)], "center", 1),
                Para([Run(sub, 10, INK_2)], "center"),
            ],
        )

    callers = (
        ("Apps and pipelines", "JSON, text, documents"),
        ("Data owners", "owner, table, selection"),
        ("QA and privacy teams", "a front-end URL"),
    )
    for i, (head, sub) in enumerate(callers):
        node((0.6, 2.25 + i * 1.25, 2.3, 0.95), head, sub)
    # the platform
    deck.rect(
        slide, (3.35, 2.05, 6.15, 4.85), fill="E7F1F0", line=TEAL, width=1.0, name="frame:dps"
    )
    deck.text(
        slide,
        (3.5, 2.1, 5.8, 0.3),
        "DATA PRIVACY SERVICES · ONE IMAGE",
        size=10,
        color=TEAL,
        bold=True,
    )
    node((3.6, 2.5, 2.25, 0.95), "app", "API and console; L1 needs no database", tone=TEAL)
    node((3.6, 3.75, 2.25, 0.95), "worker", "L2 jobs: partitions, checkpoints", tone=TEAL)
    node((3.6, 5.0, 2.25, 0.95), "browser-worker", "L3 scans in Chromium", tone=TEAL)
    deck.rect(
        slide,
        (6.2, 2.5, 3.05, 2.2),
        name="node:Privacy Engine",
        inset=0.14,
        paras=[Para([Run("Privacy Engine", 14, TEAL, True)], "center", 6)],
    )
    for i, stage in enumerate(("detect", "classify", "plan", "transform", "verify", "report")):
        col, row = i % 2, i // 2
        deck.rect(
            slide,
            (6.4 + col * 1.38, 3.05 + row * 0.52, 1.26, 0.4),
            fill="E7F1F0",
            line=TEAL,
            radius=0.5,
            name=f"stage:{stage}",
            anchor="middle",
            inset=0.04,
            paras=[Para([Run(stage, 11, TEAL, True)], "center")],
        )
    node((6.2, 4.95, 3.05, 0.6), "migrate", "DDL as the owner role, one-shot", tone=TEAL)
    node(
        (3.6, 6.12, 5.65, 0.62),
        "Metadata PostgreSQL",
        "job queue, hashed keys, checkpoints, evidence, audit",
        tone=INK,
    )
    # outside
    outside = (
        ("LLM gateway", "typed placeholders only", ROSE),
        ("OCR gateway", "scanned pages and images", INK),
        ("Oracle · PostgreSQL · MongoDB", "sources and masked targets", INK),
        ("S3 or PVC exports", "files write mode", INK),
        ("Bank tokenization service", "external_tokenize adapter", INK),
    )
    for i, (head, sub, tone) in enumerate(outside):
        y = 2.05 + i * 0.97
        node((9.95, y, 2.78, 0.8), head, sub, tone=tone)
        # the platform talks out; the LLM line is dashed rose: placeholders only, never a value
        deck.line(
            slide, (9.5, y + 0.4), (9.95, y + 0.4), color=ROSE if i == 0 else INK_3, dash=i == 0
        )
    # callers into the app; the three roles into the engine
    for i in range(3):
        deck.line(slide, (2.9, 2.72 + i * 1.25), (3.6, 2.98))
    deck.line(slide, (5.85, 2.98), (6.2, 2.98))
    deck.line(slide, (5.85, 4.22), (6.2, 4.22))
    deck.line(slide, (5.85, 5.47), (6.2, 4.6))
    deck.chrome(slide, _footer(report))
    return slide


def _slide_playground(deck: Deck, report: Report, screens: Path | None) -> Any:
    slide = deck.new_slide(
        kicker="L1 · PLAYGROUND",
        title="The Playground: findings, tokens and the key",
        lede="Paste a KYC record: every finding names its detector and score; obfuscation keeps "
        "formats; the key goes back to the caller.",
    )
    deck.picture(slide, (0.6, 2.1, 7.3, 4.7), find_screen(screens, "playground"), "playground")
    x, w = 8.2, 4.53
    gate = report.step_ok("S2")
    if gate is not None:
        deck.absent(slide, (x, 2.1, w, 4.7), "s2", gate)
        deck.chrome(slide, _footer(report))
        return slide
    findings = report.number("S2", "findings")
    if isinstance(findings, _Absent):
        deck.absent(slide, (x, 2.1, w, 1.3), "findings", findings)
    else:
        deck.kpi(
            slide,
            (x, 2.1, w, 1.3),
            "findings",
            "Findings in the sample KYC record",
            _fmt_count(findings),
            tone=AMBER,
            value_size=36,
        )
    types = report.raw("S2", "entity_types")
    names = [t for t in types if _name(t, ENTITY_NAME)] if isinstance(types, list) else []
    if names:
        deck.text(slide, (x, 3.6, w, 0.3), "Entity types found", size=11, color=INK_2, bold=True)
        below = deck.chips(slide, x, 3.95, w, sorted(set(names))[:12])
    else:
        why = (
            types
            if isinstance(types, _Absent)
            else _Absent("S2 facts.entity_types", "The report lists no entity type names.")
        )
        deck.absent(slide, (x, 3.6, w, 1.1), "entity-types", why)
        below = 4.7
    y = max(below + 0.35, 4.75)
    reveal = report.flag("S2", "reveal_identical")
    if isinstance(reveal, _Absent):
        deck.absent(slide, (x, y, w, 0.6), "reveal", reveal)
    else:
        deck.status(
            slide,
            x,
            y,
            reveal,
            "Reveal with the caller's key: identical input"
            if reveal
            else "Reveal did not return the input",
        )
    sent = report.number("S2", "raw_pii_sent_to_llm")
    if isinstance(sent, _Absent):
        deck.absent(slide, (x, y + 0.7, w, 0.6), "llm", sent)
    else:
        deck.status(slide, x, y + 0.5, sent == 0, f"Raw values sent to an LLM: {_fmt_count(sent)}")
    deck.chrome(slide, _footer(report))
    return slide


def _slide_documents(deck: Deck, report: Report) -> Any:
    slide = deck.new_slide(
        kicker="L1 · DOCUMENTS",
        title="Documents: true redaction, not a box over text",
        lede="Pages are rebuilt from images with the findings burned out, so no text layer "
        "survives; an OCR re-read of every page checks that no original is still readable.",
    )
    steps = ("Scanned KYC page", "OCR reads the page", "Findings per page", "Redacted PDF")
    for i, label in enumerate(steps):
        x = 0.6 + i * 3.15
        deck.rect(
            slide,
            (x, 2.25, 2.7, 0.95),
            fill="E7F1F0" if i == 3 else SURFACE,
            line=TEAL if i == 3 else BORDER,
            name=f"flow:{label}",
            anchor="middle",
            paras=[Para([Run(label, 14, TEAL if i == 3 else INK, True)], "center")],
        )
        if i < 3:
            deck.line(slide, (x + 2.72, 2.72), (x + 3.13, 2.72))
    gate = report.step_ok("S4")
    if gate is not None:
        deck.absent(slide, (0.6, 3.6, 12.13, 2.4), "s4", gate)
    else:
        tiles = (
            ("ocr-pages", "ocr_pages", "OCR pages read", TEAL, "pages the OCR contract read"),
            (
                "doc-findings",
                "findings",
                "Findings redacted",
                AMBER,
                "sensitive values found on the pages",
            ),
            (
                "extractable",
                "extractable_chars",
                "Characters left to extract",
                EMERALD,
                "a text extractor on the redacted PDF",
            ),
            (
                "originals",
                "originals_left",
                "Originals still readable",
                EMERALD,
                "an OCR re-read of the redacted pages",
            ),
        )
        for i, (key, path, label, tone, caption) in enumerate(tiles):
            box = (0.6 + i * 3.06, 3.6, 2.9, 2.0)
            got = report.number("S4", path)
            if isinstance(got, _Absent):
                deck.absent(slide, box, key, got)
            else:
                deck.kpi(slide, box, key, label, _fmt_count(got), tone=tone, caption=caption)
    deck.text(
        slide,
        (0.6, 6.15, 12.1, 0.6),
        "The obfuscated text and the findings come back with the key, so the caller can still "
        "reveal the text output; the redacted PDF is irreversible by design.",
        size=12,
        color=INK_3,
    )
    deck.chrome(slide, _footer(report))
    return slide


def _slide_layer2(deck: Deck, report: Report, screens: Path | None, bench: list[Bar]) -> Any:
    slide = deck.new_slide(
        kicker="L2 · CONNECTORS",
        title="Databases: masked copies at scale, with evidence",
        lede="Discover, review the heat-map, run: partitions with checkpoints, a verification "
        "sweep and a sealed evidence pack for every job.",
    )
    deck.picture(slide, (0.6, 2.1, 6.4, 3.75), find_screen(screens, "job"), "job")
    gate = report.step_ok("S6")
    jobs = report.raw("S6", "jobs") if gate is None else gate
    if isinstance(jobs, _Absent) or not isinstance(jobs, list) or not jobs:
        why = (
            jobs
            if isinstance(jobs, _Absent)
            else _Absent("S6 facts.jobs", "The report lists no run jobs for step S6.")
        )
        deck.absent(slide, (0.6, 6.0, 6.4, 0.85), "s6", why)
    else:
        shown = [j for j in jobs if isinstance(j, dict) and j.get("engine") in ENGINES][:3]
        if not shown:
            deck.absent(
                slide,
                (0.6, 6.0, 6.4, 0.85),
                "s6",
                _Absent("S6 facts.jobs[].engine", "No S6 job names postgres, oracle or mongo."),
            )
        width = (6.4 - 0.1 * (len(shown) - 1)) / max(len(shown), 1)
        for i, job in enumerate(shown):
            box = (0.6 + i * (width + 0.1), 6.0, width, 0.85)
            rows = _number(_dig(job, "counts.rows_written"))
            parts = _number(_dig(job, "counts.partitions"))
            rate = _number(_dig(job, "timings.rows_per_s"))
            passed = _dig(job, "verification.passed")
            if rows is None or parts is None or rate is None or not isinstance(passed, bool):
                deck.absent(
                    slide,
                    box,
                    f"s6-{job['engine']}",
                    _Absent(
                        f"S6 {job['engine']} job counts",
                        "A run job lacks counts, timings or verification.",
                    ),
                )
                continue
            volume = f"{_fmt_count(rows)} rows · {_fmt_count(parts)} partitions"
            speed = f"{_fmt_rate(rate)} rows/s · {'verified' if passed else 'NOT verified'}"
            deck.rect(
                slide,
                box,
                name=f"job:{job['engine']}",
                inset=0.1,
                anchor="middle",
                paras=[
                    Para(
                        [Run(ENGINES[job["engine"]], 12, EMERALD if passed else ROSE, True)],
                        space_after=1,
                    ),
                    Para([Run(volume, 10, INK_2)]),
                    Para([Run(speed, 10, INK_2)]),
                ],
            )
    # throughput bars (shapes, never a chart part)
    x, y, w = 7.35, 2.1, 5.38
    deck.text(
        slide, (x, y, w, 0.3), "Measured throughput, rows per second", size=12, color=INK, bold=True
    )
    if not bench:
        deck.absent(
            slide,
            (x, y + 0.45, w, 1.4),
            "bench",
            _Absent(
                "bench results",
                "No bench JSON in docs/technical/bench: run tools/bench_connector.py.",
            ),
        )
    else:
        cpus = {b.host_cpus for b in bench}
        host = cpus.pop() if len(cpus) == 1 else None
        caption = "tools/bench_connector.py" + (
            f" on one Mac with {_fmt_count(host)} CPUs" if host else ""
        )
        deck.text(slide, (x, y + 0.3, w, 0.28), caption, size=10, color=INK_3)
        top = max(b.value for b in bench)
        track = w - 1.5
        for i, bar in enumerate(bench):
            by = y + 0.72 + i * 0.5
            deck.text(slide, (x, by, w, 0.24), bar.label, size=9.5, color=INK_2)
            length = max(track * bar.value / top, 0.05)
            deck.rect(
                slide,
                (x, by + 0.25, length, 0.17),
                fill=TEAL,
                line=None,
                radius=None,
                name=f"bar:{bar.label}",
            )
            deck.text(
                slide,
                (x + length + 0.08, by + 0.19, 1.45, 0.28),
                _fmt_rate(bar.value),
                size=10,
                color=INK,
                bold=True,
            )
    deck.chrome(slide, _footer(report))
    return slide


def _dig(doc: Any, path: str) -> Any:
    for part in path.split("."):
        if not isinstance(doc, dict):
            return None
        doc = doc.get(part)
    return doc


def _slide_consistency(deck: Deck, report: Report) -> Any:
    slide = deck.new_slide(
        kicker="L2 · JOB GROUPS",
        title="One key, three systems, the same tokens",
        lede="A job group shares one key across Oracle, MongoDB and PostgreSQL: the same customer "
        "id becomes the same token everywhere, so joins still work, with no lookup table.",
    )
    # the triangle on the left, the summary card on the right; lines first so boxes sit on top
    nodes = {"postgres": (4.75, 2.6), "oracle": (2.0, 5.85), "mongo": (7.5, 5.85)}
    subtitle = {
        "postgres": "bank customers",
        "oracle": "CRM customers",
        "mongo": "customer profiles",
    }
    edges = {
        frozenset({"postgres", "oracle"}): ((4.2, 3.05), (2.4, 5.4)),
        frozenset({"postgres", "mongo"}): ((5.3, 3.05), (7.1, 5.4)),
        frozenset({"oracle", "mongo"}): ((3.3, 5.85), (6.2, 5.85)),
    }
    for start, end in edges.values():
        deck.line(slide, start, end, arrow=False, color=TEAL, width=2.0)
    for engine, (cx, cy) in nodes.items():
        deck.rect(
            slide,
            (cx - 1.3, cy - 0.45, 2.6, 0.9),
            fill="E7F1F0",
            line=TEAL,
            width=1.0,
            name=f"system:{engine}",
            anchor="middle",
            paras=[
                Para([Run(ENGINES[engine], 15, TEAL, True)], "center", 1),
                Para([Run(subtitle[engine], 10.5, INK_2)], "center"),
            ],
        )
    card = (9.3, 2.15, 3.43, 4.15)
    gate = report.step_ok("S7")
    if gate is not None:
        deck.absent(slide, card, "s7", gate)
        deck.chrome(slide, _footer(report))
        return slide
    members = report.raw("S7", "members")
    pairs = report.raw("S7", "pairs")
    engines: list[str] = (
        [m["engine"] if isinstance(m, dict) and m.get("engine") in ENGINES else "" for m in members]
        if isinstance(members, list)
        else []
    )
    labelled: set[frozenset[str]] = set()
    if isinstance(pairs, list):
        for pair in pairs:
            if not isinstance(pair, dict):
                continue
            left, right = pair.get("left"), pair.get("right")
            if not (
                isinstance(left, int)
                and isinstance(right, int)
                and 0 <= left < len(engines)
                and 0 <= right < len(engines)
            ):
                continue
            ends = frozenset({engines[left], engines[right]})
            checked, matched = _number(pair.get("checked")), _number(pair.get("matched"))
            if (
                len(ends) != 2
                or not ends <= set(nodes)
                or ends in labelled
                or checked is None
                or matched is None
            ):
                continue
            labelled.add(ends)
            (ax, ay), (bx, by) = edges[ends]
            mx, my = (ax + bx) / 2, (ay + by) / 2
            ok = pair.get("consistent") is True
            deck.rect(
                slide,
                (mx - 1.0, my - 0.27, 2.0, 0.54),
                fill=PAPER,
                line=EMERALD if ok else ROSE,
                radius=0.5,
                name="pair:" + "-".join(sorted(ends)),
                anchor="middle",
                inset=0.04,
                paras=[
                    Para(
                        [
                            Run(
                                f"{_fmt_count(matched)} of {_fmt_count(checked)} matched",
                                11,
                                EMERALD if ok else ROSE,
                                True,
                            )
                        ],
                        "center",
                    )
                ],
            )
    if len(labelled) < len(edges):
        deck.absent(
            slide,
            (0.6, 6.42, 8.3, 0.45),
            "s7-pairs",
            _Absent(
                "S7 facts.pairs for every system pair", "The check should report all three pairs."
            ),
        )
    sampled = report.number("S7", "sampled")
    everywhere = report.number("S7", "all_members_matched")
    passed = report.flag("S7", "passed")
    if (
        isinstance(sampled, _Absent)
        or isinstance(everywhere, _Absent)
        or isinstance(passed, _Absent)
    ):
        why = next(v for v in (sampled, everywhere, passed) if isinstance(v, _Absent))
        deck.absent(slide, card, "s7-summary", why)
    else:
        deck.rect(
            slide,
            card,
            name="kpi:consistency",
            inset=0.22,
            paras=[
                Para([Run("Customer ids sampled", 12, INK_2, True)], space_after=2),
                Para([Run(_fmt_count(sampled), 34, TEAL, True)], space_after=12),
                Para([Run("Found in all three systems", 12, INK_2, True)], space_after=2),
                Para(
                    [Run(_fmt_count(everywhere), 34, EMERALD if passed else ROSE, True)],
                    space_after=12,
                ),
                Para(
                    [
                        Run(
                            f"Consistency check {'passed' if passed else 'FAILED'}",
                            13,
                            EMERALD if passed else ROSE,
                            True,
                        )
                    ]
                ),
            ],
        )
    deck.chrome(slide, _footer(report))
    return slide


def _slide_synthetic(deck: Deck, report: Report) -> Any:
    slide = deck.new_slide(
        kicker="L2 · SYNTHETIC",
        title="Synthetic data: measured, not claimed anonymous",
        lede="The copula engine is fitted, previewed and measured on the rows the job will write, "
        "before anything is written; a real row is rejected in flight.",
    )
    gate = report.step_ok("S8")
    if gate is not None:
        deck.absent(slide, (0.6, 2.2, 12.13, 4.5), "s8", gate)
        deck.chrome(slide, _footer(report))
        return slide

    def at(col: int, row: int) -> tuple[float, float, float, float]:
        return (0.6 + col * 4.09, 2.2 + row * 2.25, 3.95, 2.05)

    score = report.number("S8", "fidelity.score")
    if isinstance(score, _Absent):
        deck.absent(slide, at(0, 0), "fidelity", score)
    else:
        deck.kpi(
            slide,
            at(0, 0),
            "fidelity",
            "Fidelity score",
            _fmt_ratio(score),
            tone=TEAL,
            caption="shape of the real columns and their correlations",
        )
        deck.rect(
            slide,
            (0.76, 3.85, 3.63, 0.1),
            fill="E7F1F0",
            line=None,
            radius=None,
            name="gauge:track",
        )
        deck.rect(
            slide,
            (0.76, 3.85, max(3.63 * min(score, 1.0), 0.02), 0.1),
            fill=TEAL,
            line=None,
            radius=None,
            name="gauge:fill",
        )
    exact = report.number("S8", "privacy.exact_row_matches")
    if isinstance(exact, _Absent):
        deck.absent(slide, at(1, 0), "exact-rows", exact)
    else:
        deck.kpi(
            slide,
            at(1, 0),
            "exact-rows",
            "Exact-row matches",
            _fmt_count(exact),
            tone=EMERALD if exact == 0 else ROSE,
            caption="synthetic rows identical to a real row",
        )
    ratio, target = (
        report.number("S8", "privacy.nndr.ratio"),
        report.number("S8", "privacy.nndr.target"),
    )
    measured = report.number("S8", "privacy.nndr.measured")
    if (
        isinstance(ratio, _Absent)
        or isinstance(target, _Absent)
        or isinstance(measured, _Absent)
        or not measured
    ):
        why = next(
            (v for v in (ratio, target, measured) if isinstance(v, _Absent)), None
        ) or _Absent("S8 NNDR (not measured)", "privacy.nndr.measured is zero in the report.")
        deck.absent(slide, at(2, 0), "nndr", why)
    else:
        deck.kpi(
            slide,
            at(2, 0),
            "nndr",
            "Nearest-neighbour distance ratio",
            _fmt_ratio(ratio),
            tone=EMERALD if ratio >= target else AMBER,
            caption=f"target at least {_fmt_ratio(target)}; higher is safer",
        )
    mia_run = report.flag("S8", "privacy.membership_inference.run")
    auc, auc_target = (
        report.number("S8", "privacy.membership_inference.auc"),
        report.number("S8", "privacy.membership_inference.target"),
    )
    if mia_run is True and not isinstance(auc, _Absent) and not isinstance(auc_target, _Absent):
        deck.kpi(
            slide,
            at(0, 1),
            "mia",
            "Membership inference (MIA) AUC",
            _fmt_ratio(auc),
            tone=EMERALD if auc <= auc_target else AMBER,
            caption=f"target at most {_fmt_ratio(auc_target)}; a coin toss scores lowest",
        )
    elif mia_run is False:
        deck.absent(
            slide,
            at(0, 1),
            "mia",
            _Absent(
                "S8 MIA (membership inference did not run)",
                "The rehearsal's holdout was too small for the MIA proxy.",
            ),
        )
    else:
        why = next(v for v in (mia_run, auc, auc_target) if isinstance(v, _Absent))
        deck.absent(slide, at(0, 1), "mia", _Absent(f"S8 MIA: {why.what}", why.how))
    util_run = report.flag("S8", "utility.run")
    util = report.number("S8", "utility.ratio")
    metric = report.raw("S8", "utility.metric")
    if util_run is True and not isinstance(util, _Absent):
        named = UTILITY_METRICS.get(metric) if isinstance(metric, str) else None
        deck.kpi(
            slide,
            at(1, 1),
            "utility",
            "Utility, synthetic vs real",
            _fmt_ratio(util),
            tone=TEAL,
            caption="train on synthetic, test on real" + (f" ({named})" if named else ""),
        )
    elif util_run is False:
        deck.absent(
            slide,
            at(1, 1),
            "utility",
            _Absent("S8 utility (not measured)", "Name a utility label in the synthesize policy."),
        )
    else:
        why = next(v for v in (util_run, util) if isinstance(v, _Absent))
        deck.absent(slide, at(1, 1), "utility", _Absent(f"S8 utility: {why.what}", why.how))
    rows = report.number("S8", "counts.rows_written")
    paras = [
        Para([Run("How it was measured", 12, INK, True)], space_after=4),
        Para(
            [
                Run(
                    "On a deterministic preview of the rows this job writes, before writing; "
                    "exact copies of real rows are rejected in flight.",
                    11,
                    INK_2,
                )
            ],
            space_after=4,
        ),
    ]
    if not isinstance(rows, _Absent):
        paras.append(Para([Run(f"Rows written: {_fmt_count(rows)}", 11, INK, True)], space_after=2))
    label = "Label: synthetic — measured, not claimed anonymous"
    paras.append(Para([Run(label, 11, TEAL, True)]))
    deck.rect(
        slide, at(2, 1), fill="E7F1F0", line=TEAL, name="card:method", paras=paras, inset=0.18
    )
    if isinstance(rows, _Absent):
        deck.missing.append(f"slide {deck.slide_no}: {rows.what}")
    deck.chrome(slide, _footer(report))
    return slide


def _slide_scanner(deck: Deck, report: Report, screens: Path | None) -> Any:
    slide = deck.new_slide(
        kicker="L3 · UI SCANNER",
        title="UI scanner: every screen filled, nothing submitted",
        lede="The scanner maps the onboarding journey, classifies every field and shows each "
        "screen beside a copy filled with synthetic or obfuscated data.",
    )
    deck.picture(
        slide, (0.6, 2.1, 7.3, 4.7), find_screen(screens, "scan-parallel"), "scan-parallel"
    )
    x, w = 8.2, 4.53
    gate = report.step_ok("S9")
    if gate is not None:
        deck.absent(slide, (x, 2.1, w, 4.7), "s9", gate)
        deck.chrome(slide, _footer(report))
        return slide
    posts = report.number("S9", "posts_received")
    if isinstance(posts, _Absent):
        deck.absent(slide, (x, 2.1, w, 1.55), "posts", posts)
    else:
        deck.kpi(
            slide,
            (x, 2.1, w, 1.55),
            "posts",
            "POST requests received by the site",
            _fmt_count(posts),
            tone=EMERALD if posts == 0 else ROSE,
            caption="counted by the demo site itself",
            value_size=40,
        )
    tiles = (
        ("pages", "scan.pages", "Pages crawled"),
        ("fields", "scan.fields", "Fields classified"),
        ("personas", "scan.personas", "Personas filled in"),
    )
    for i, (key, path, label) in enumerate(tiles):
        box = (x, 3.75 + i * 1.02, w, 0.92)
        got = report.number("S9", path)
        if isinstance(got, _Absent):
            deck.absent(slide, box, key, got)
            continue
        deck.rect(
            slide,
            box,
            name=f"kpi:{key}",
            anchor="middle",
            inset=0.16,
            paras=[
                Para([Run(f"{label}  ", 12, INK_2, True), Run(_fmt_count(got), 22, TEAL, True)])
            ],
        )
    deck.chrome(slide, _footer(report))
    return slide


def _slide_evidence(deck: Deck, report: Report, screens: Path | None) -> Any:
    slide = deck.new_slide(
        kicker="EVIDENCE · POSTURE",
        title="Evidence for every job, posture at every boot",
        lede="Packs name the policy hash, the methods and the verification; staging and "
        "production refuse to boot when a posture check fails.",
    )
    deck.picture(slide, (0.6, 2.1, 7.3, 4.7), find_screen(screens, "evidence"), "evidence")
    x, w = 8.2, 4.53
    passed, total = report.number("S11", "posture.passed"), report.number("S11", "posture.total")
    if isinstance(passed, _Absent):
        deck.absent(slide, (x, 2.1, w, 1.25), "posture", passed)
    elif isinstance(total, _Absent):
        deck.absent(slide, (x, 2.1, w, 1.25), "posture", total)
    else:
        deck.kpi(
            slide,
            (x, 2.1, w, 1.25),
            "posture",
            "Posture checks",
            f"{_fmt_count(passed)} of {_fmt_count(total)} passed",
            tone=EMERALD if passed == total else ROSE,
            value_size=24,
        )
    llm = report.number("S1", "raw_pii_sent_to_llm")
    if isinstance(llm, _Absent):
        deck.absent(slide, (x, 3.45, w, 1.25), "llm-zero", llm)
    else:
        tenant = report.top("tenant", TENANT_NAME)
        deck.kpi(
            slide,
            (x, 3.45, w, 1.25),
            "llm-zero",
            "Values of raw PII sent to an LLM",
            _fmt_count(llm),
            tone=EMERALD if llm == 0 else ROSE,
            caption=f"tenant {tenant}, from the reports summary"
            if tenant
            else "this tenant, from the reports summary",
            value_size=24,
        )
    rows: list[tuple[str, Callable[[], tuple[bool, str] | _Absent]]] = [
        ("packs", lambda: _count_line(report, "S10", "packs_listed", "Evidence packs listed")),
        ("audit", lambda: _count_line(report, "S10", "audit_rows", "Audit rows for the demo jobs")),
        (
            "pdf",
            lambda: _flag_line(
                report, "S10", "pdf_ok", "Evidence PDF rendered", "Evidence PDF did not render"
            ),
        ),
        ("rotation", lambda: _status_line(report)),
        ("retention", lambda: _retention_line(report)),
    ]
    for i, (key, line) in enumerate(rows):
        got = line()
        y = 4.85 + i * 0.39
        if isinstance(got, _Absent):
            deck.absent(slide, (x, y, w, 0.34), key, got)
        else:
            deck.status(slide, x, y, got[0], got[1], size=11.5)
    deck.chrome(slide, _footer(report))
    return slide


def _count_line(report: Report, sid: str, path: str, label: str) -> tuple[bool, str] | _Absent:
    got = report.number(sid, path)
    return got if isinstance(got, _Absent) else (True, f"{label}: {_fmt_count(got)}")


def _flag_line(
    report: Report, sid: str, path: str, yes: str, no: str
) -> tuple[bool, str] | _Absent:
    got = report.flag(sid, path)
    return got if isinstance(got, _Absent) else (got, yes if got else no)


def _status_line(report: Report) -> tuple[bool, str] | _Absent:
    got = report.number("S11", "rotated_key_status")
    if isinstance(got, _Absent):
        return got
    return (got == 401, f"A rotated key is refused: HTTP {_fmt_count(got)}")


def _retention_line(report: Report) -> tuple[bool, str] | _Absent:
    got = report.raw("S11", "retention_state")
    if isinstance(got, _Absent):
        return got
    if got not in STATES:
        return _Absent("S11 facts.retention_state", "The retention run's state is not a job state.")
    return (got == "succeeded", f"Retention run (dry run): {got}")


def _slide_decisions(deck: Deck, report: Report) -> Any:
    slide = deck.new_slide(
        kicker="DECISIONS",
        title="Decisions taken, and what comes next",
        lede="The owner decided D1 to D4; D5 to D13 stand at the defaults of the design record; "
        "D14 is still open.",
    )
    tones = {"Decided": EMERALD, "Default": TEAL, "Open": AMBER}
    soft = {"Decided": "E6F2EE", "Default": "E7F1F0", "Open": "F7EEE6"}
    for i, (tag, topic, choice, state) in enumerate(DECISIONS):
        col, row = i // 7, i % 7
        x, y = 0.6 + col * 4.2, 2.15 + row * 0.66
        deck.rect(
            slide,
            (x, y, 0.62, 0.3),
            fill=soft[state],
            line=tones[state],
            radius=0.5,
            name=f"decision:{tag}",
            anchor="middle",
            inset=0.02,
            paras=[Para([Run(tag, 10, tones[state], True)], "center")],
        )
        deck.text(
            slide,
            (x + 0.72, y - 0.06, 3.38, 0.62),
            [
                Para([Run(f"{topic} · ", 11, INK, True), Run(state, 11, tones[state], True)]),
                Para([Run(choice, 10, INK_2)]),
            ],
        )
    deck.rect(
        slide,
        (9.1, 2.15, 3.63, 4.55),
        fill=SURFACE,
        line=BORDER,
        name="card:next",
        inset=0.2,
        paras=[Para([Run("Next steps", 16, INK, True)], space_after=8)]
        + [Para([Run(f"• {step}", 12, INK_2)], space_after=8) for step in NEXT_STEPS],
    )
    deck.chrome(slide, _footer(report))
    return slide


# =================================================================================================
# Package-level finishing: theme fonts and colours, metadata, deterministic zip
# =================================================================================================
THEME_COLOURS = {
    "dk1": INK,
    "lt1": SURFACE,
    "dk2": INK_2,
    "lt2": PAPER,
    "accent1": TEAL,
    "accent2": AMBER,
    "accent3": EMERALD,
    "accent4": ROSE,
    "accent5": "6D28D9",
    "accent6": "A16207",
    "hlink": TEAL,
    "folHlink": INK_2,
}


def _patch_theme(xml: Any) -> None:
    """Fonts: Arial for both slots, no per-script fonts; colours: the Vault tokens."""
    from lxml import etree  # noqa: PLC0415
    from pptx.oxml.ns import qn  # noqa: PLC0415

    for slot in ("a:majorFont", "a:minorFont"):
        for font in xml.iter(qn(slot)):
            for child in list(font):
                font.remove(child)
            font.text = None
            for tag, face in (("a:latin", FALLBACK_FONT), ("a:ea", ""), ("a:cs", "")):
                etree.SubElement(font, qn(tag)).set("typeface", face)
    for scheme in xml.iter(qn("a:fontScheme")):
        scheme.set("name", "DPS Vault")
    for scheme in xml.iter(qn("a:clrScheme")):
        scheme.set("name", "DPS Vault")
        for name, rgb in THEME_COLOURS.items():
            slot = scheme.find(qn(f"a:{name}"))
            if slot is None:
                continue
            for child in list(slot):
                slot.remove(child)
            slot.text = None
            etree.SubElement(slot, qn("a:srgbClr")).set("val", rgb)


def _finish_package(prs: Any, stamp: datetime) -> None:
    from lxml import etree  # noqa: PLC0415
    from pptx.opc.constants import RELATIONSHIP_TYPE as RT  # noqa: PLC0415
    from pptx.opc.package import XmlPart  # noqa: PLC0415

    package = prs.part.package
    for part in package.iter_parts():
        if not part.content_type.endswith("theme+xml"):
            continue
        if isinstance(part, XmlPart):
            _patch_theme(part._element)
        else:
            xml = etree.fromstring(part.blob)
            _patch_theme(xml)
            part._blob = etree.tostring(
                xml, xml_declaration=True, encoding="UTF-8", standalone=True
            )
    # the template's stale thumbnail ("Click to add title") is dropped rather than shipped
    for rid, rel in list(package._rels.items()):
        if rel.reltype == RT.THUMBNAIL:
            package._rels.pop(rid)
    props = prs.core_properties
    props.title = "Data Privacy Services — showcase"
    props.subject = "The ten-minute demo, measured"
    props.author = "Data Privacy Services"
    props.last_modified_by = "tools/build_deck.py"
    props.keywords = "DPS; privacy; obfuscation; synthetic data; evidence"
    props.comments = (
        "Built by tools/build_deck.py from the e2e demo report; every figure was measured."
    )
    props.revision = 1
    naive = stamp.astimezone(UTC).replace(tzinfo=None)
    props.created = naive
    props.modified = naive
    props.last_printed = naive


def _normalise_zip(path: Path) -> None:
    """Rewrite the package with fixed entry timestamps: the same inputs give the same bytes."""
    with zipfile.ZipFile(path) as src:
        entries = [(info.filename, src.read(info.filename)) for info in src.infolist()]
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as dst:
        for name, data in entries:
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            dst.writestr(info, data)


# =================================================================================================
# Entry points
# =================================================================================================
def build_deck(
    *,
    out: Path,
    report: Path | None = None,
    screens: Path | None = None,
    bench: Path | None = None,
    script: Path | None = None,
    strict: bool = False,
) -> BuildResult:
    """Build the twelve-slide deck into ``out`` (atomically); see the module docstring.

    Raises :class:`DeckInputError` for a malformed report, and :class:`MissingInputs` for any
    missing input when ``strict``; in both cases nothing is written (an existing ``out`` stays).
    """
    try:
        from pptx import Presentation  # noqa: PLC0415
        from pptx.util import Emu  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - the deck extra is dev-only
        raise DeckInputError('python-pptx is missing: uv pip install -e ".[deck]"') from exc

    data = Report.load(report)
    bars = load_bench(bench)
    notes = script_notes(script)
    prs = Presentation()
    prs.slide_width, prs.slide_height = Emu(_emu(13.333)), Emu(_emu(7.5))  # 16:9
    deck = Deck(prs)
    builders: list[Callable[[], Any]] = [
        lambda: _slide_title(deck, data),
        lambda: _slide_problem(deck, data),
        lambda: _slide_principles(deck, data),
        lambda: _slide_architecture(deck, data),
        lambda: _slide_playground(deck, data, screens),
        lambda: _slide_documents(deck, data),
        lambda: _slide_layer2(deck, data, screens, bars),
        lambda: _slide_consistency(deck, data),
        lambda: _slide_synthetic(deck, data),
        lambda: _slide_scanner(deck, data, screens),
        lambda: _slide_evidence(deck, data, screens),
        lambda: _slide_decisions(deck, data),
    ]
    if script is None or not notes:
        deck.missing.append(
            f"speaker notes: no demo script at {script}"
            if script
            else "speaker notes: no demo script"
        )
    for number, build in enumerate(builders, start=1):
        slide = build()
        text = notes.get(number)
        if text is None:
            if notes:
                deck.missing.append(f"speaker notes: the demo script has no Slide {number} block")
            text = DEFAULT_NOTES[number]
        _notes(deck, slide, text)
    missing = tuple(dict.fromkeys(deck.missing))
    if strict and missing:
        raise MissingInputs(missing)
    _finish_package(prs, data.generated_at() or datetime.now(UTC))
    out.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".deck-", suffix=".pptx", dir=out.parent)
    os.close(fd)
    try:
        prs.save(tmp)
        _normalise_zip(Path(tmp))
        umask = os.umask(0)
        os.umask(umask)
        os.chmod(tmp, 0o666 & ~umask)  # a normal document, not mkstemp's 0600
        os.replace(tmp, out)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return BuildResult(out=out, slides=len(prs.slides), missing=missing)


def _notes(deck: Deck, slide: Any, text: str) -> None:
    frame = slide.notes_slide.notes_text_frame
    frame.text = ""
    deck.write(frame, [Para([Run(line, 12, INK)]) for line in text.split("\n")], inset=0.08)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Command-line options (module docstring)."""
    p = argparse.ArgumentParser(
        prog="python tools/build_deck.py", description=__doc__.split("\n")[0]
    )
    p.add_argument("--report", type=Path, default=DEFAULT_REPORT, help="e2e demo report JSON")
    p.add_argument(
        "--screens", type=Path, default=DEFAULT_SCREENS, help="console screenshots directory"
    )
    p.add_argument("--bench", type=Path, default=DEFAULT_BENCH, help="bench results directory")
    p.add_argument(
        "--script", type=Path, default=DEFAULT_SCRIPT, help="demo script (speaker notes)"
    )
    p.add_argument("--out", type=Path, default=DEFAULT_OUT, help="the .pptx to write")
    p.add_argument(
        "--strict", action="store_true", help="fail (write nothing) when any input is missing"
    )
    return p.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Build the deck; exit 0 written, 1 inputs missing under --strict, 2 unusable input."""
    args = parse_args(argv)
    try:
        result = build_deck(
            out=args.out,
            report=args.report,
            screens=args.screens,
            bench=args.bench,
            script=args.script,
            strict=args.strict,
        )
    except MissingInputs as exc:
        _print_missing(exc.missing)
        print("deck not written: --strict and inputs are missing", file=sys.stderr)
        return 1
    except DeckInputError as exc:
        print(f"deck not built: {exc}", file=sys.stderr)
        return 2
    _print_missing(result.missing)
    print(f"deck written: {result.out} ({result.slides} slides)")
    return 0


def _print_missing(missing: Sequence[str]) -> None:
    if missing:
        print(f"{len(missing)} inputs missing (placeholders on the slides):", file=sys.stderr)
        for item in missing:
            print(f"  - {item}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
