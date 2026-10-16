# S6a — implementation report (finish stage F3, part a: deck builder + demo script draft)

**Worktree:** `/Users/neelu/document_intelligence/dps-wt-fdemo` · **branch:** `fdemo` (from `main` `64c46e3`)
**Date:** 2026-10-14 · **Result:** done; all gates green; two commits on `fdemo` (not pushed by me — the
owner's auto-push loop picks local branches up; it last ran 17:49).

## Commits

| Commit | Message | Paths |
|---|---|---|
| `3348fb2` | `docs(demo): presenter script draft for the ten-minute demo` | `docs/demo/DEMO-SCRIPT.md` |
| `a717517` | `docs(demo): showcase deck built from the rehearsal's measured outputs` (the plan's Task 26B message) | `tools/build_deck.py`, `tests/test_deck.py`, `pyproject.toml`, `HANDOFF.md`, contract §1, `docs/superpowers/build/M5-DEVIATIONS.md` |

Author `NilenduGanguli <nilendu.ganguli@gmail.com>` (global config, no local override), no trailers,
explicit pathspec commits. Working tree clean afterwards.

## Worktree preparation

- `git worktree add …/dps-wt-fdemo -b fdemo main` (branch did not exist).
- `.env` (git-ignored): `DPS_IMAGE_TAG=fdemo`, `DPS_APP_PORT=8418`, `DPS_DB_PORT=5503`,
  `DPS_SOURCE_PG_PORT=5504`, `DPS_MINIO_PORT=9090`, `DPS_MINIO_CONSOLE_PORT=9091`, `DPS_ORACLE_PORT=1528`,
  `DPS_MONGO_PORT=27024`, `DPS_OCR_PORT=5086`, `DPS_DEMO_SITE_PORT=8428`, `DPS_TOKENIZER_MOCK_PORT=8438`,
  `DPS_WORKER_METRICS_PORT=9418`; all ports checked free with `lsof`. Every host port main's compose
  interpolates is covered (plus m4's `DPS_DEMO_SITE_PORT`); `DPS_PG_PORT` / `DPS_EXPORT_ROOT` are literal
  container env values in compose (not interpolated), so they need no `.env` entry. Non-port interpolations
  keep their compose defaults.
- `.env.lane` (git-ignored): the §12.2 `DPS_TEST_*` set for these ports, m3's format. **Trap found and
  fixed:** exporting `DPS_WORKER_METRICS_PORT` in `.env.lane` changes `Settings().worker_metrics_port`
  (it is a real setting) and fails `tests/test_config_m2.py::test_m2_defaults_keep_l1_database_free`; it now
  lives in `.env` only (comment in `.env.lane` says why). Other lanes copying this format should do the same.
- `lane.sh` copied from `dps-wt-m3` (`chmod +x`; already in the shared `info/exclude`).
- `uv venv --python 3.12 .venv`; `./lane.sh uv pip install -e ".[dev,fpe,docs,db,connectors,tabular,e2e,deck]"`
  (python-pptx 1.0.2); `cd frontend && npm ci` (exit 0, no symlinks).
- No Docker needed for S6a; no compose project was started.

## What was built

### `tools/build_deck.py` (M5 Task 26B)
`python tools/build_deck.py [--report reports/e2e-demo-report.json] [--screens reports/demo-screens]
[--bench docs/technical/bench] [--script docs/demo/DEMO-SCRIPT.md] [--out docs/demo/dps-showcase.pptx] [--strict]`

- Twelve 16:9 slides in the plan's order (title; problem; principles P1/D2/P6; architecture; Playground;
  documents; L2 + bench bars; three-way consistency; synthetic metrics incl. MIA + utility; UI scanner;
  evidence + posture; D1–D14 + next steps), each with a title placeholder and speaker notes.
- **Speaker notes are parsed from the `#### Slide N` blocks of `docs/demo/DEMO-SCRIPT.md`** (fences skipped,
  wrapped lines joined); a missing block falls back to built-in notes and is reported as missing.
- **Never invents a number:** figures come only from the report and bench JSON; nothing is derived (no sums,
  averages or list counts). A missing input (no report, a step absent or not PASS, a missing fact, a missing
  screenshot or bench file) renders a named slate placeholder (`placeholder:<key>`) saying what is missing and
  how to get it, is listed on stderr, and `--strict` exits 1 **without writing** (an existing deck stays);
  a malformed / wrong-schema report exits 2. A step that did not PASS contributes no figures.
- **Value-free by construction:** prints only numbers, booleans as words and name-shaped strings (entity
  types `^[A-Z][A-Z0-9_]{1,31}$`, allow-listed engines/states/metrics, tenant slug, git sha, semver).
- **Keynote-safe:** only rectangles, rounded rectangles, ovals, straight connectors, text boxes and pictures —
  no chart part, no table, no `p:graphicFrame` at all; every shape drops its theme `p:style` (no inherited
  shadow/white text); every run names `Helvetica Neue`; the theme font scheme names `Arial` (the fallback;
  per-script theme fonts removed, so no other face is named anywhere); theme colours = Vault tokens; Latin-1 /
  Mac Roman glyphs only (no ✓ or → in text — arrows are connector shapes).
- **Deterministic:** fixed zip timestamps + document dates from the report → same inputs, byte-identical file
  (verified: two builds, same SHA-1). Output mode 0644; atomic write via temp file + `os.replace`.
- Bench chart: newest file per (engine, mode, write mode, workers, parallelism), ≤ 8 bars, drawn as rects with
  the measured rows/s printed; host CPU line only when all files agree.

### `tests/test_deck.py` — 12 tests (`pytest.importorskip("pptx")`)
The plan's fixture test (two-step report S8+S9, two 8×8 PNGs, one bench JSON): 12 slides, non-empty title
placeholders and notes, 16:9, no `ppt/charts/` or `ppt/embeddings/` part, no `p:graphicFrame`, every
`typeface` in the package ∈ {Helvetica Neue, Arial, theme refs}, theme major/minor fonts ∈ the two names,
every figure on slides 7–11 traceable to an input value (rounding-aware), slide 9 shows MIA + utility values,
placeholders for the missing steps/screens, one bench bar with its measured rate, M0 `Detector` finds no
CRITICAL entity in any slide text or notes, file < 20 MB. Plus: slide 9 placeholders without MIA/utility;
a complete S1–S11 report fills every slot (`missing == ()`, figures on slides 5–11 traceable); no inputs →
placeholders only, no figures, `--strict` exit 1; a FAIL step shows no figures; non-name strings (an SSN-shaped
string, a person-like name, a bogus sha) never reach a slide; malformed facts become placeholders; wrong-schema
report refused (exit 2, no file); notes equal the script's blocks; the notes parser (fences, wrapping,
quotes, out-of-range slides); notes fallback reported; the CLI.

### `docs/demo/DEMO-SCRIPT.md` (M5 Task 26, draft)
Presenter script per the plan's outline and spec §9: timeline table (clock, beat, console page, slides,
proof), "Before the room" commands (`docker compose --profile full up --build -d`, `/readyz` poll — never
`--wait`, `demo_seed.py`, `e2e_demo.py --console`, `build_deck.py --strict`), optional opening (slides 1–4),
the six beats with click-by-click steps using the console's **actual** labels (main + the m4 scanner pages),
say-lines, proof-on-screen, the `#### Slide N` notes blocks, Q&A crib (D8, D2, GDPR/DPDP, LLM boundary,
residency, OpenShift), "If something fails" table, reset between rehearsals. Quotes no figure.

### Docs
`pyproject.toml` `deck = ["python-pptx>=1.0"]` (dev only); contract §1 extras sentence and HANDOFF §1
extras row name it; `M5-DEVIATIONS.md` gained "Task 26B" and "Task 26" sections marked finish-stage S6a.

## Gates (in `dps-wt-fdemo`, via `./lane.sh`)

| Gate | Result |
|---|---|
| `ruff check dps tests tools` | All checks passed |
| `ruff format --check dps tests tools` | 377 files already formatted |
| `mypy dps` | no issues in 134 files (also `mypy tools/build_deck.py tests/test_deck.py`: clean) |
| `pytest -q` | **2150 passed, 487 skipped** (baseline before my work: 2138 passed, 487 skipped) |
| `pytest -q tests/test_deck.py` | 12 passed |

Frontend unchanged (no `npm run build` needed).

## Visual QA

- **Keynote: not achieved here.** `osascript … open … export … as PDF` timed out on `open` three times
  (AppleEvent -1712); a LaunchServices `open -g -a Keynote` also never produced a document. Keynote 14.4
  received the file (it stamped `com.apple.quarantine …;Keynote;`) but showed no document or window to
  AppleScript; System Events inspection is not permitted (no assistive access); no crash report exists.
  Most likely a modal dialog that needs a person. I quit the Keynote instance I had launched (no documents
  were open). **S6b / the owner must do the Keynote PDF review** (the structural test guards the known
  Keynote failure modes meanwhile).
- **LibreOffice headless renders** of all 12 slides, with complete fixture inputs and with no inputs, were
  reviewed page by page; fixes applied (chip widths, card shapes, spacing, screenshot alignment, slide-8
  layout). Contact sheets (fixture numbers, not measured — QA only):
  `~/.cache/dps-build/reports/probe-s6a/fixture-preview-complete-inputs.png`,
  `…/fixture-preview-no-inputs.png`, generator `…/make_preview.py`.

## Hand-off to S6b (must read)

1. **`tools/e2e_demo.py` must write `reports/e2e-demo-report.json` in the contract fixed in the
   `build_deck.py` docstring** (schema `dps.e2e-demo.v1`; facts per step mirror the API's own key paths: S6
   `jobs[]` from evidence packs, S7 the consistency-check result, S8 the synthetic pack's
   `fidelity/privacy/utility`, S9 the scan block + demo-site `posts_received`, …). Screenshots must be named
   `reports/demo-screens/<page>-light.png` with pages `playground`, `job`, `scan-parallel`, `evidence`
   (the S12 walk's `<page>-<theme>.png`).
2. Build the real deck with `python tools/build_deck.py --strict` so a missing input fails instead of
   shipping a placeholder; commit `docs/demo/dps-showcase.pptx` then.
3. Task 26's console test expects headings "Playground" and "Data sources & jobs", but the pages render
   `h1` "Try the engine" and "Jobs" (sidebar: "Data sources & jobs"); Overview "Protection posture",
   Policies "Policies", Reports "Evidence and audit", Admin "Admin" match.
4. The release venv needs the `deck` extra, or `tests/test_deck.py` skips (DoD 1 counts skips).
5. Confirm the DEMO-SCRIPT commands/flags against the tools S6b writes (`--skip-oracle`, `--reset-jobs`,
   the `demo-keys.json` shape) and replace "draft" in its status line after the rehearsal.

## Not done / open

- No `.pptx` committed (deliberate: fixtures are not measurements). Keynote QA pending (above).
- Plan step "`pytest -q tests/test_deck.py` → `1 passed`": it is 12 tests (recorded as a deviation).
