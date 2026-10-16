# S6a review, round 2: the B1/B2 fix and a full re-review of `fdemo`

**Reviewer:** independent review agent, round 2 · **Date:** 2026-10-14 19:25 IST · **Branch:** `fdemo` at
`2223661` (commits `3348fb2` script draft, `a717517` deck tool, tests and docs, `2223661` r1 fix; base
`main` `64c46e3`). **Reports read:** `S6a-impl.md`, `S6a-review-r1.md` and `S6a-fix-r1.md`.
**I changed no file in the worktree.** `git status` stays clean, and no `reports/` or `.deck-*` file
appeared. Probes, logs, decks and renders are in `~/.cache/dps-build/reports/s6a-review-r2/`.

## Verdict: PASS (0 blocking)

Both round-1 blocking findings are fixed, and I reproduced each fix independently:

- **B1.** Slide 1 now claims "ran end to end" only for a complete rehearsal.
- **B2.** The script no longer promises that every posture check passes. Slide 11 now shows the
  posture count together with where the checks are enforced.

The new tests fail on the code before the fix and pass on HEAD. Every gate is green.

I hunted for Keynote risks, invented figures and unsafe fonts and found no new blocking defect:

- **Keynote:** no chart, table, SmartArt, group or `graphicFrame` part.
- **Fonts:** only Helvetica Neue and Arial are named.
- **Glyphs:** every slide and notes glyph encodes as Mac Roman.
- **Figures:** every number on slides 5 to 11 traces to an input. The notes quote no figure.

Eight non-blocking items follow, mostly hand-offs to S6b. The Keynote PDF review itself is still open;
it belongs to S6b and the owner, as in round 1.

## Gates (run by me through `./lane.sh` in `dps-wt-fdemo`)

| Gate | Result | Log |
|---|---|---|
| `ruff check dps tests tools` | All checks passed | `ruff.log` |
| `ruff format --check dps tests tools` | 377 files already formatted | `ruff-format.log` |
| `mypy dps` | no issues in 134 source files | `mypy.log` |
| `mypy tools/build_deck.py tests/test_deck.py` | no issues in 2 source files | `mypy-deck.log` |
| `pytest -q` (full) | **2168 passed, 487 skipped**, 1 warning (starlette/httpx deprecation), 44.6 s | `pytest-full.log` |
| `pytest -q tests/test_deck.py` | **30 passed** | `pytest-deck.log` |

**The tests are meaningful.** I ran HEAD's `tests/test_deck.py` against the `build_deck.py` of `HEAD~1`
in a scratch copy: 17 failed and 13 passed (`oldcode-tests.log`). The failures are all six "no
end-to-end claim" cases, the skipped-steps case, the missing-step case, the six posture contexts and
the three "`enforced` absent" cases.

## Round-1 blocking findings, re-verified

### B1: slide 1 claims "ran end to end" only for a complete rehearsal. Fixed.

`Report.incomplete()` returns `None` only when two conditions hold: every scripted step S1 to S11 is
listed, and every listed step is `PASS` or `SKIP`. Otherwise slide 1 shows `placeholder:rehearsal`,
the reason goes into the missing ledger, and `--strict` exits 1 without writing. My scenarios
(`probe_structure.log`):

| Report | Slide 1 | `--strict` |
|---|---|---|
| S1 to S11 and S12 `PASS`, 431.6 s | "…the scripted demo ran end to end in 7:12." | exit 0 |
| S5 `FAIL`, S6 to S12 `NOT RUN`, 130.2 s | placeholder "a complete rehearsal (first failure: S5)" | refused (7 inputs listed missing) |
| every status lower-case `pass` | placeholder "first failure: S1, status unknown" | refused |
| S5 listed twice, `PASS` then `FAIL` | placeholder "first failure: S5" | refused |
| only S12 `FAIL` | placeholder "first failure: S12" | refused |
| a non-dict step entry | placeholder "a step entry without a step id" | refused |
| no `total_seconds` | placeholder "report generated_at and total_seconds" | refused |

The headless LibreOffice render of the failed-rehearsal deck (`render/hi-B-failed-p-01.png`) shows the
slate placeholder on the dark title slide. The footer names only the report's date and commit.

### B2: the posture count shown in its context. Fixed.

- **Script.** `DEMO-SCRIPT.md` changed in three places: the timeline proof cell, the Reports and Admin
  say-line and proof, and the slide 11 notes. None promises every check passing any more. They say
  the local stack only reports the checks, so the production-only ones fail by design. The two
  examples hold against `docker-compose.yml`: `DPS_ENV: local`, a default
  `DPS_BOOTSTRAP_API_KEY` literal, and `DPS_LLM_PROVIDER` defaulting to `stub`. The boot rule is the
  console's own wording (`PostureList.tsx`). That rule is accurate against `dps/posture.py`:
  `enforced = settings.is_production`, which is `env in ("staging", "prod")`, and
  `keys_rotated` and `migrations_verified` are `enforced_at_boot=False`. `create_app` and the worker
  call `assert_production_posture`.
- **Deck contract.** The S11 contract now reads `posture.{passed,total,enforced}` and an optional
  `env`. Those are `PostureReport`'s own fields (`dps/posture.py:269-276`). A missing or non-boolean
  `enforced` becomes a placeholder.
- **Slide 11.** The value is emerald when every check passed, amber when a check failed where it is
  not enforced, and rose only for an enforced failure.

The caption renders as follows (`D1` to `D6` in `probe_structure.log`):

| Input | Shown |
|---|---|
| local 5 of 11 | amber, "local stack, evaluated as if production; staging and production refuse to boot when a check enforced at boot fails" |
| prod 10 of 11 | rose, "enforced on this production stack: it refuses to boot when …" |
| no `env` | the generic caption |
| enforced with no `env` | "enforced on this stack: …" |

Rendered, the two-line caption fits inside the 1.4 in card, and the column ends above the footer
(`render/hi-D3-p-11.png`).

## Acceptance

### Worktree and git (re-checked)

- **Environment files.** `.env`, `.env.lane` and `lane.sh` are unchanged since round 1. They are
  ignored through `info/exclude` and `.gitignore`, never by an edit to `.gitignore`.
  `DPS_WORKER_METRICS_PORT` is in `.env` only, which is right because it is a `Settings` field.
- **Authorship.** All three commits are `NilenduGanguli <nilendu.ganguli@gmail.com>` with no trailer
  and no local identity override. Messages are conventional.
- **Push.** `origin/fdemo` is `2223661`: autopush pushed it at 19:07, gitleaks clean.

### M5 plan Task 26B, as far as no real rehearsal is needed

| Plan item | State |
|---|---|
| `tools/build_deck.py`, the plan's CLI (+ `--script`, `--strict`) | OK |
| Twelve 16:9 slides in the plan's order, title and notes on each | OK. The notes come from the `#### Slide N` blocks; I printed all twelve. |
| Charts as shapes, no chart parts (Keynote) | OK: 0 `ppt/charts`, `embeddings` or `diagrams` parts. A complete-input build has 215 `p:sp`, 17 `p:cxnSp`, 4 `p:pic` and no `grpSp`, `graphicFrame` or `mc:AlternateContent`. Geometries: `rect`, `roundRect`, `ellipse`, `line`. |
| Fonts: Helvetica Neue, Arial as fallback (no Cambria) | OK. Faces in the package are Helvetica Neue (runs) and Arial (both themes, master `buFont`), plus `+mj`/`+mn` references and empty `ea`/`cs`. The LibreOffice PDFs embed only HelveticaNeue and HelveticaNeue-Bold. |
| Vault tokens; 16:9 | OK. `sldSz` is 12191695 × 6858000 (but see R2-N2). |
| A missing input becomes a slate placeholder; never an invented number | OK. With no inputs, slides 5 to 11 show no digit except the chrome, and 13 inputs are listed missing. |
| `tests/test_deck.py`: the plan's six assertions | OK, plus 24 further tests. The plan's "1 passed" deviation is logged. |
| `deck` extra (dev only, never in an image) | OK. The pin is `python-pptx>=1.0,<2`. The Dockerfile installs `[fpe,docs,db,connectors,tabular]` only. |
| DEMO-SCRIPT "which slide with which beat" line | OK ("Slides and beats") |
| `docs/demo/dps-showcase.pptx` committed; Keynote PDF reviewed | Deferred to S6b, which needs the real rehearsal; this is logged in M5-DEVIATIONS. I accept it. |

**Task 26, the script draft.** It follows the plan's outline: before the room, the six beats, the
notes blocks, the Q&A crib, "If something fails" and the reset steps. Spot checks pass:

- These console labels exist as written: "Posture score", "Rotate", "Try the engine",
  "Original restored … DPS kept no copy.", "Live test on the engine", the scanner chips and the
  Light/Dark theme toggle.
- `gdpr-pseudonymise` treats AADHAAR with `fpe_digits` and `keep_suffix: 4`, and compose enables FPE.
  The line "the Aadhaar number keeps its last four digits" therefore holds on the demo stack.
- `demo-site:8410/apply` exists on `m4`.

### Programme plan §2.6, F3 row

"Deck test green" holds. The other two criteria belong to S6b and the owner: "e2e demo all steps PASS
under 10:00" and "Keynote export pages reviewed".

## Defect hunt: Keynote, invented figures, fonts

- **Package.** I parsed every XML part of a complete-input build that had four 2880 × 1800
  screenshots and the eight real bench configurations. The build is 76 KB. The zip entries carry the
  1980 timestamp. Two builds are byte-identical (SHA-1 `bc0273e26b6c…`), mode 0644, and `--strict`
  exits 0.
- **Glyphs.** Every `<a:t>` on the slides and in the notes encodes as Mac Roman.
- **Figures.** Every number token on slides 1 to 12 traces to a report or bench value. Exceptions:
  the chrome; slide 1's date and m:ss, which format `generated_at` and `total_seconds` and are
  tested; and the slide numbers. The only digit in any speaker note is the name "Layer 2". Slide 7's
  eight bars match the eight distinct bench configurations. The duplicated
  `postgres/obfuscate/table/1/4` configuration correctly takes the 2026-10-13 file. The caption
  "on one host with 18 CPUs" matches every file's `host.cpus`.
- **Renders.** I rendered seven scenarios through headless LibreOffice and paged through them as
  PNGs: complete, failed, no `env`, prod, all-skip, no inputs and skip-oracle
  (`render/sheet-A.png`, `render/hi-*.png`). Nothing overflows. Chips, bars, cards and the dark
  title slide all sit inside the safe area.
- **Keynote.** I did not run Keynote, for the same reason as round 1: from an agent session it hangs
  on a modal, per memory note `pptx-decks-keynote-rendering`, and could disturb the owner's desktop.
  The structural checks above cover the known Keynote failure modes; the visual review is still open.

## Non-blocking findings (new in round 2; recommended, none gates the merge)

- **R2-N1. Malformed values still crash the build.** A list or dict where the deck expects a name
  is looked up in a dict or set with `in`, which raises `TypeError: unhashable type`. The affected
  inputs are S6 `jobs[].engine`, S7 `members[].engine`, S11 `retention_state` and a bench file's
  `engine`, `mode` or `write_mode` (`probe_crash.log`). The build exits 1 with a traceback and
  writes no deck. Exit 1 is also the code documented for "inputs missing under `--strict`".
  - Round 1's fix closed this same crash class for `status` only.
  - The traceback prints no values, the run fails loudly, and the inputs come from DPS's own tools.
    That is why this is not blocking.
  - Fix: guard each lookup with `isinstance(x, str)`, or map unexpected exceptions in `main()` to
    exit 2.
- **R2-N2. `p:sldSz` still says `type="screen4x3"`.** The python-pptx template keeps
  `type="screen4x3"` while `cx` and `cy` are 16:9. PowerPoint and LibreOffice use `cx` and `cy`, and
  I expect Keynote does too. Removing the attribute is a one-line hardening fix:
  `prs.part._element.sldSz.attrib.pop("type", None)`. S6b's Keynote export confirms the slide size
  either way.
- **R2-N3. Slide 8's notes say "every pair matched".** Plan Task 26 asserts only "overlap > 0 for
  every pair" for S7. If S6b copies that assertion, S7 can PASS while the check reports
  `passed: false`, for example at coverage below the default `min_coverage` 1.0. The slide then
  shows a rose "Consistency check FAILED" while the notes claim a full match, and `--strict` does
  not catch it.
  - Fix for S6b: S7 asserts the check's `passed` and every pair's `consistent`.
  - The S8, S9 and S11 note claims ("zero", "rotated key stops working") hold, because the plan's
    assertions match them.
- **R2-N4. Screenshots are not tied to the report.** `find_screen` takes whatever PNG is in
  `reports/demo-screens/`. A rehearsal without `--console`, or one whose S12 failed early, can
  therefore ship an earlier run's screenshots beside this run's figures and footer date.
  - Fix for S6b: embed a screenshot only when S12 passed in this report, or when its mtime is not
    older than `generated_at`.
- **R2-N5. "DPS stores no key" overstates D2 for L2 jobs.** Slide 3, slide 12 (D2) and the slide 3
  notes say "DPS stores no key" or "keeps nothing". An L2 job's key is in fact kept sealed under
  the KEK in `dps_job.key_enc` for the job's life. It is purged at the terminal state, and retention
  nulls any leftovers (`dps/jobs.py`, `dps/retention.py`). The wording does match the spec's D2 text
  and the product's own evidence page, and the Q&A crib answers it correctly ("sealed for the job's
  life and purged with it").
  - For a data-protection audience, consider: "DPS keeps no key: L1 returns it, a job holds its key
    sealed only until the job ends".
- **R2-N6. Slide 1's m:ss uses `round()`.** A run of 599.6 s prints "10:00" against an "under 10:00"
  target, and a negative total prints "-1:55". The printed time can also differ by a second from
  the Markdown report's own "demo flow completed in m:ss".
  - Fix: use `floor` and S6b's formatter.
- **R2-N7. `SKIP` is accepted for every step.** A report with every step skipped claims "ran end to
  end … (S1, …, S12 skipped)". The claim is self-describing and degenerate, `--strict` refuses that
  deck, and e2e_demo only skips S3 (tokenizer), S9 (`--skip-scanner`) and parts under
  `--skip-oracle`. A stricter rule would require `PASS` for the steps that feed slides.
- **R2-N8. A deviation-log heading could read as "Task 26 done".** M5-DEVIATIONS has
  "## Task 26 — Presenter script draft (finish stage S6a …)". RESUME §3's mechanical rule counts a
  `## Task N` heading plus a commit as "done", but `demo_seed.py`, `e2e_demo.py` and the console test
  are not written yet. S6b should complete that section, or the heading should say "(partial)".

## Still open from round 1 (owned by S6b or the owner)

- **N2.** A non-strict run can overwrite the committed deck path.
- **N3.** `reports/` is not ignored.
- **N4.** The Keynote PDF review has not been done.
- **N8.** Slide 5's chip rows have no height limit.
- **N1 residual.** Slide 8's title and lede still say "three systems" in a two-member build. The
  slide shows a placeholder, and `--strict` refuses the deck.
- **N5 residual.** Bench configurations beyond eight are dropped without an entry in the missing
  ledger. The current bench directory has exactly eight.

## Hand-off to S6b (in addition to `S6a-fix-r1.md` §Hand-off)

1. Run `tools/build_deck.py --strict` only from a complete rehearsal: S1 to S11 all listed; statuses
   `PASS`, `FAIL`, `ERROR`, `NOT RUN` or `SKIP`.
2. Copy S11's `posture` block verbatim from `GET /admin/posture`, including `enforced` and `env`.
3. Make S7 assert `passed` (R2-N3).
4. Tie the screenshots to the run (R2-N4).
5. Do the Keynote export and page through it (N4). Check the slide size there too (R2-N2).

## Artefacts (`~/.cache/dps-build/reports/s6a-review-r2/`)

- **Gate logs:** `ruff.log`, `ruff-format.log`, `mypy.log`, `mypy-deck.log`, `pytest-full.log`,
  `pytest-deck.log`. The tests run against the pre-fix module are in `oldcode/` and
  `oldcode-tests.log`.
- **Probes:**
  - `probe_structure.py` and its log: package structure, faces, glyphs, figure tracing,
    determinism, and the B1, B2 and skip-oracle scenarios.
  - `probe_crash.py` and its log: R2-N1.
- **Decks and renders:** the scenario decks are in `decks/`. The LibreOffice PDFs and page PNGs are
  in `render/`, with `sheet-A.png` as the twelve-slide contact sheet of the complete-input deck.
- **Files reviewed:** `tools/build_deck.py` and `tests/test_deck.py` (in full);
  `docs/demo/DEMO-SCRIPT.md` (in full); `M5-DEVIATIONS.md` (Task 26B, Task 26 and the r1 fix
  sections); HANDOFF §1; contract §1; `pyproject.toml`; `dps/posture.py`; `dps/config.py`
  (`is_production`); `dps/layers/connectors/{consistency,evidence}.py` (the keys the deck reads);
  the presets; the console labels.
