# S6a fix, round 1: the review's two blocking findings (B1, B2) on `fdemo`

**Fixer:** S6a fix agent · **Date:** 2026-10-14 · **Worktree:** `/Users/neelu/document_intelligence/dps-wt-fdemo`
· **Branch:** `fdemo` · **Review:** `~/.cache/dps-build/reports/S6a-review-r1.md` (FAIL, B1 + B2).
**Commit:** `2223661` `fix(fdemo): the deck claims an end-to-end run only for a complete rehearsal and shows
posture where it is enforced` (author `NilenduGanguli <nilendu.ganguli@gmail.com>` from the global config, no
local override, no trailer; explicit pathspec of 6 files). Pushed by `tools/dev/autopush.sh` at 19:07:16
(gitleaks clean, fast-forward). Working tree clean afterwards. No Docker stack was needed or started.

## Result: both blocking findings fixed, failing tests first, every gate green

| Finding | State | Where |
|---|---|---|
| **B1** slide 1 claimed "ran end to end in m:ss" for a failed rehearsal | **fixed** | `tools/build_deck.py` `Report.incomplete()`, `Report.skipped()`, `_slide_title` |
| **B2** script promised all posture checks passing; slide 11 showed "5 of 11" in rose without context | **fixed** | `docs/demo/DEMO-SCRIPT.md`; `tools/build_deck.py` `_slide_evidence`, `_posture_caption`; S11 contract in the docstring and `M5-DEVIATIONS.md` |

## B1 — what changed

- The sentence "Built from the rehearsal of <date>: the scripted demo ran end to end in m:ss." is printed only
  when the report shows a **complete rehearsal**: every scripted step S1–S11 is listed **and** every listed step
  is `PASS` or `SKIP`. Skipped steps are named: "… in 7:12 (S3 skipped)." / "(S3 and S12 skipped)."
- Otherwise slide 1 shows the slate placeholder `placeholder:rehearsal`:
  "Not available: a complete rehearsal (first failure: S5)" + "The rehearsal did not complete: fix it and rehearse
  again (tools/e2e_demo.py --console), then rebuild the deck." The reason is, in order: the first step with
  `FAIL`, `ERROR` or an unknown status (`first failure: S5[, status ERROR|unknown]`), an entry without an `S<n>`
  id, the first `NOT RUN` step (`S11 not run`), or the first scripted step not listed (`no step S4 in the
  report`). It is appended to the missing ledger (`slide 1: a complete rehearsal (...)`), so **`--strict` exits 1**.
- Only `S<n>`-shaped ids and known statuses are ever printed. Found in passing: a status that is not a string
  (e.g. a list) crashed `_step` with `TypeError: unhashable type` (a set lookup); `_status_name()` now maps it to
  "unknown" (proved on the old code: `s6a-fix-r1/old-code-b1.log`).
- **Contract addition for S6b's `e2e_demo.py`** (docstring + `M5-DEVIATIONS.md` Task 26B bullet): statuses
  `PASS|FAIL|ERROR|NOT RUN|SKIP`; every scripted step S1–S11 is listed, a skipped one as `SKIP` (never left out),
  the ones after a failure as `NOT RUN`; S12 when `--console` ran. I chose "every S1–S11 listed" on top of the
  reviewer's status rule because "ran end to end" is not backed by a report that leaves a scripted step out.

## B2 — what changed

- **Script** (`docs/demo/DEMO-SCRIPT.md`): the timeline's proof cell (was "posture checks all passing", l.37) now
  reads "posture checks with the ones enforced at boot marked; rotated key refused". The Reports and Admin
  say-line and **Proof** (was "all posture checks pass", l.244) say the checks are evaluated as if production,
  only reported on the local stack, so the production-only ones fail here by design (examples: the bootstrap
  admin key the seed uses, the stub LLM — both confirmed in `docker-compose.yml`: `DPS_ENV: local`,
  `DPS_BOOTSTRAP_API_KEY` default literal, `DPS_LLM_PROVIDER` default `stub`), and that staging and production
  refuse to boot when a check marked "enforced at boot" fails. Slide 11's notes say the same. No count is quoted
  anywhere. The Overview beat's "posture checklist" became the **Posture score** card (the Overview has no
  checklist; the eleven checks are on the Admin page).
- **Deck contract S11:** `posture.{passed,total,enforced}` + optional `posture.env` — `GET /admin/posture`'s own
  keys (`PostureReport.env/enforced`, `dps/posture.py:269-276`, `enforced = settings.is_production`).
  `enforced` is required: absent, or not a boolean → `placeholder:posture`, listed missing (`--strict` fails).
- **Slide 11 KPI:** emerald when every check passed; **amber** (the console's own "N to fix" tone) when a check
  failed where it is not enforced; **rose only for an enforced failure**. Caption:
  - not enforced, `env` local/dev: "local stack, evaluated as if production; staging and production refuse to boot
    when a check enforced at boot fails"; unknown or contradicting `env`: "evaluated as if production, reported
    only here; staging and production refuse …" (an `env` is printed only from an allow-list that agrees with
    `enforced`);
  - enforced: "enforced on this production stack: it refuses to boot when a check enforced at boot fails".
  The card grew from 1.25 to 1.4 in and the LLM card shrank from 1.25 to 1.15 in (one-line caption); the column
  still ends at 6.75 in, above the footer. Lede and built-in notes: "…refuse to boot when a posture check
  enforced at boot fails".
- **Deliberate deviation from the review's literal wording:** the review suggested "staging/production refuse
  to boot unless all pass". That overclaims as well: `keys_rotated` and `migrations_verified` are **not**
  enforced at boot (`dps/posture.py` `CHECKS`, `enforced_at_boot=False`; `create_app` raises `PostureError` from
  `assert_production_posture` for the boot rules only). The deck and the script therefore use the console's own
  rule (`frontend/src/components/PostureList.tsx`: "Staging and production refuse to boot when a check marked
  'enforced at boot' fails").

## Non-blocking review items

| Item | Taken? | What |
|---|---|---|
| N1 `--skip-oracle` wording | yes | slide 8 summary "Found in every system of the group" (true for any member count: `all_members_matched` is the intersection of every member); the script says a `--skip-oracle` rehearsal cannot rebuild the deck under `--strict`, keep the last full rehearsal's deck (l.53-57, "If something fails" row) |
| N5 "one Mac" | yes | bench caption "on one host with N CPUs"; slide 7 notes "one machine" (the bench JSON records CPUs and machine type only). The silent drop beyond eight bars is not changed |
| N6 D14 misquote | yes | "payload size, rows per job, concurrent jobs, docs per day" (spec §12) |
| N7 slide 1 untested | yes | the m:ss is now asserted against `total_seconds` (431.6 s → 7:12) |
| N9 MIA caption | yes | "a coin toss scores about half" |
| N10 private API | yes | `deck = ["python-pptx>=1.0,<2"]` in `pyproject.toml` and contract §1 |
| N13 slide 1 notes | yes | the numbers were measured "by the rehearsal named in the footer and, for throughput, by the bench runs"; the script's **Deck** line names the bench results too |
| N2 strict by default for the committed path | no | S6b decides when it commits the real deck |
| N3 `reports/` ignore | no | S6b, with the rehearsal outputs |
| N4 Keynote PDF review | no | needs the owner's GUI session (as before) |
| N8 chip-row height | no | the KYC sample's types fit |
| N11, N12 | no | no action / release venv |

## Tests (failing first)

`tests/test_deck.py`: 12 → **30** tests. New: slide 1 states the measured time and `--strict` builds (positive
control); six statuses that block the claim (`FAIL` then `NOT RUN`, `ERROR`, `BROKEN`, `null`, a list, a lone
`NOT RUN`), each with the placeholder, the ledger entry and `--strict` exit 1; skipped steps named; a missing
scripted step and an entry whose id is a person-like string (never printed); six posture contexts asserting
value text, value colour and caption; `enforced` absent, `"false"` and `0` → placeholder + ledger.
`FULL_FACTS` gains `S3` and `S5` (a complete S1–S11 report) and the measured local posture shape
(`{"passed": 5, "total": 11, "enforced": false, "env": "local"}`); the complete-report test now expects "5 of 11".

- Red run before any implementation: 15 failed, 13 passed (`s6a-fix-r1/red-tests.log`).
- The final test file against the pre-fix `build_deck.py` (from `HEAD~1`, in a scratch copy): **17 failed, 13
  passed** — every new negative case fails, the list status with `TypeError: unhashable type: 'list'`
  (`s6a-fix-r1/old-code-b1.log`); the 12 earlier tests and the positive control pass on both.

## Gates (run by me, `./lane.sh` in `dps-wt-fdemo`; logs in `~/.cache/dps-build/reports/s6a-fix-r1/`)

| Gate | Result |
|---|---|
| `ruff check dps tests tools` | All checks passed |
| `ruff format --check dps tests tools` | 377 files already formatted |
| `mypy dps` | no issues in 134 source files |
| `mypy tools/build_deck.py tests/test_deck.py` | no issues in 2 source files |
| `pytest -q` (full) | **2168 passed, 487 skipped**, 1 warning (starlette/httpx deprecation), 48 s (review baseline 2150 + 18 new) |
| `pytest -q tests/test_deck.py` | 30 passed |

## Proofs

- **Reviewer's reproduction re-run** (`s6a-review-r1/scenarios.py`): before — case 1 title "…the scripted demo ran
  end to end in 2:10.", case 3 posture KPI `('5 of 11 passed', 'BE123C')`; after (`s6a-fix-r1/scenarios-after.log`) —
  case 1 "Not available: a complete rehearsal (first failure: S5)", 12 missing inputs; case 2 no "three systems"
  claim; case 3 (old contract, no `enforced`) is now a placeholder, the lede names "a posture check enforced at
  boot".
- **Renders** (headless LibreOffice, fixture numbers, QA only; `s6a-fix-r1/render/`): complete local report
  (slide 11 amber "5 of 11 passed" with the two-line caption inside the card), failed-at-S5 (slide 1
  placeholder), enforced failure (rose "10 of 11 passed"), skipped steps ("… in 7:12 (S3 and S12 skipped).").
  Slides 7, 8, 9 and 12 checked with the new wording: no overflow. PDFs `*.pdf`, pages `*.png`, generator
  `s6a-fix-r1/render_fixtures.py`.
- **Keynote-safety re-checked** on all four fixture decks: every slide and notes glyph encodes as Mac Roman, no
  `p:graphicFrame`, faces only Helvetica Neue / Arial (+ theme refs). Two builds of the same inputs are
  byte-identical (SHA-1 `28fd8858…`), mode 0644, 85.6 KB.

## Files changed (commit `2223661`)

`tools/build_deck.py`, `tests/test_deck.py`, `docs/demo/DEMO-SCRIPT.md`,
`docs/superpowers/build/M5-DEVIATIONS.md` (Task 26B contract bullet + new section "S6a review r1 fix"),
`docs/superpowers/plans/2026-10-11-m1-m5-contract.md` (§1 deck extra pin), `pyproject.toml` (deck extra pin).

## Hand-off to S6b

1. `e2e_demo.py` must list **every** scripted step S1–S11 (a skipped one as `SKIP`, the ones after a failure as
   `NOT RUN`) or slide 1 shows a placeholder and `--strict` refuses the deck.
2. S11 facts must carry `posture.enforced` (boolean) and should carry `posture.env`, copied from
   `GET /admin/posture` — without `enforced` the posture card is a placeholder.
3. Still open from the review: N2 (overwrite of the committed deck by a non-strict run), N3 (`reports/` ignore),
   N4 (Keynote PDF export review).
