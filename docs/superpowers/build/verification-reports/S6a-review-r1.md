# S6a review, round 1: worktree prep + M5 Task 26B (deck builder) on `fdemo`

**Reviewer:** independent review agent · **Date:** 2026-10-14 · **Branch:** `fdemo` at `a717517`
(commits `3348fb2` DEMO-SCRIPT draft, `a717517` deck tool, test and docs; base `main` `64c46e3`).
**Implementer report:** `~/.cache/dps-build/reports/S6a-impl.md`. **I changed no file in the worktree.**
Probes, logs and renders: `~/.cache/dps-build/reports/s6a-review-r1/`.

## Verdict: FAIL (2 blocking items, both small)

The engineering is careful. The package is Keynote-safe by construction: no chart parts, no
`p:graphicFrame`, only the Helvetica Neue and Arial faces, valid DrawingML ordering, and a
byte-identical rebuild. Every gate is green. Nearly every figure path is traceable to an input.
Two claims are not backed by measurement, though, and the programme's bar is "measured, not claimed":

1. **The title slide states "the scripted demo ran end to end in m:ss" even when the rehearsal failed.**
2. **The script promises "all posture checks pass", but the demo stack passes 5 of 11 by design.**
   The deck's slide 11 will show that result as a red "5 of 11 passed" with no context.

## Gates (run by me, through `./lane.sh` in `dps-wt-fdemo`)

| Gate | Result |
|---|---|
| `ruff check dps tests tools` | All checks passed |
| `ruff format --check dps tests tools` | 377 files already formatted |
| `mypy dps` | no issues in 134 source files |
| `mypy tools/build_deck.py tests/test_deck.py` | no issues in 2 source files |
| `pytest -q` (full) | **2150 passed, 487 skipped**, 1 warning (starlette/httpx deprecation), 47 s |
| `pytest -q tests/test_deck.py` | **12 passed** |

Lane ports 8418/5503/5504/9090/9091/1528/27024/5086/8428/8438/9418 are free (`lsof`). No compose
project was started for this review.

## Acceptance

### Worktree preparation

| Requirement | State |
|---|---|
| `dps-wt-fdemo` on branch `fdemo` from `main` | OK (from `64c46e3`) |
| `.env`: the 12 required values | OK, all present. `docker-compose.yml` interpolates no other host-port variable. `DPS_PG_PORT` and `DPS_EXPORT_ROOT` are not interpolated, which I checked with a `${...}` grep. |
| `.env.lane`: every `DPS_TEST_*` of contract §12.2 for these ports | OK. `DPS_WORKER_METRICS_PORT` is deliberately kept in `.env` only, because it is a `Settings` field. No other exported variable collides with a `Settings` field (I grepped `dps/config.py`). |
| `lane.sh` copied from m3, executable | OK, identical to `dps-wt-m3/lane.sh` (`diff`) |
| Ignored through `info/exclude`, not `.gitignore` | OK (`git check-ignore -v`) |
| `.venv` with Python 3.12, extras installed, `npm ci` | OK: `[dev,fpe,docs,db,connectors,tabular,e2e,deck]` and python-pptx 1.0.2. `frontend/node_modules` is a real directory, not a symlink. `ner`, `vertex` and `synth-ml` are not installed; their tests skip (nit N12). |
| Authorship and trailers | OK: both commits are by `NilenduGanguli <nilendu.ganguli@gmail.com>`, no trailer, no local identity override. Autopush pushed `fdemo` `a717517` at 18:09, gitleaks clean. |

### M5 plan Task 26B

| Plan item | State |
|---|---|
| `tools/build_deck.py` with the plan's CLI | OK. It adds `--script` and `--strict`. |
| Twelve slides in the plan's order and content | OK. Each slide has a title and speaker notes taken from the `#### Slide N` blocks of `DEMO-SCRIPT.md`. |
| Keynote-safe output | OK, verified on a build with complete inputs. 244 `p:sp`, 17 `p:cxnSp` and 4 `p:pic`, 0 `p:graphicFrame` and 0 groups. Geometries are only `rect`, `roundRect`, `ellipse` and `line`. No `ppt/charts`, `ppt/embeddings` or diagram part, no `p:style` on any slide, no `mc:AlternateContent`. Element order inside `a:ln` and `p:spPr` follows the schema on every slide (lxml check). |
| Fonts | OK. Only Helvetica Neue and Arial are named, plus theme references and an empty `ea`/`cs`. Neither theme has a per-script `a:font`. The LibreOffice PDF embeds only HelveticaNeue and HelveticaNeue-Bold, both installed on this Mac. |
| Glyphs | OK: every character in slide and notes text encodes as Mac Roman |
| 16:9, Vault colours | OK. The colours match the console's light-theme tokens in `styles.css`. Ink and paper use the plan's values. |
| A missing input becomes a slate placeholder and is listed | OK. `--strict` exits 1 and leaves an existing deck untouched: I checked a sentinel file, and no `.deck-*` temp file was left behind. A malformed or wrong-schema report exits 2. |
| Deterministic build, mode 0644, deck under 20 MB | OK: same SHA-1 on two builds, mode `0o644`, 85 KB with four 2880×1800 screenshots |
| `tests/test_deck.py` covers the plan's six assertions | OK, plus 11 further tests. The plan expected "1 passed"; the deviation is logged. |
| `pyproject` extra `deck` (dev only, never in an image) | OK. Contract §1 and HANDOFF §1 name it. |
| DEMO-SCRIPT line mapping slides to beats | OK ("Slides and beats") |
| `docs/demo/dps-showcase.pptx` committed | Deferred to S6b on purpose, because a fixture deck would show fixture numbers. Logged in M5-DEVIATIONS. I accept this. |
| Keynote PDF export reviewed | **Not done.** Keynote would not open the file from an agent session (AppleEvent -1712). The programme plan §7 risk row allows this gap when it is reported; it stays with S6b and the owner. Non-blocking (N4). |

### Programme plan §2.6, F3 row (the S6a part)

"Deck test green" holds. The rest of the row belongs to S6b: "e2e demo all steps PASS under 10:00" and
"Keynote export pages reviewed".

### The input contract against the API as built

Spot checks only; the code wins.

- **S6 (`counts`, `timings`, `verification` keys):** match `EvidencePack` (`evidence.py:473-493`).
- **S7 (`members[].engine`, `pairs[].{left,right,checked,matched,consistent}`, `sampled`, `all_members_matched`, `passed`):** match `ConsistencyCheckResult` (`consistency.py:124-175`).
  The engine names `postgres`, `oracle` and `mongo` match the connectors.
- **S8 (`fidelity.score`, `privacy.exact_row_matches`, `privacy.nndr.{ratio,target,measured}`, `privacy.membership_inference.{run,auc,target}`, `utility.{run,ratio,metric}`):** match `tabular.py:460-500` and `metrics.py`.
  `nndr.measured` is the float `1.0` or `0.0`, which the deck handles. The tone rules are right:
  NNDR passes at or above its target, MIA AUC passes at or below its target.
- **S9 (`scan.{pages,fields,personas,submissions}`):** matches the m4 scan block (`inventory.py:692-700`).
- **S11 (`posture.{passed,total}`):** matches `PostureReport`. The contract leaves out the same report's `enforced` and `env` fields; see B2.
- **Slide 6 claim, "an OCR re-read of every page checks that no original is still readable":** true.
  `redact_ocr_verify` defaults to `True` (`config.py:162`), and `_ocr_reread` runs in `documents/__init__.py:643-648`.
- **Static claims:** P1, the D2 RSA-OAEP wrap (`transform/crypto.py`), P6, the four roles, and D1 to D13 all agree with spec §3 and §12 and with the code.
  The other next steps (UTC quota day, FF1 off by default in `config.py:115`) also agree.
  The D14 wording slightly misquotes the spec (N6).
- **DEMO-SCRIPT console labels:** all 32 labels from the main frontend and the 11 scanner labels in `dps-wt-m4/frontend` exist as written.
  "Light theme" is the toggle `{'Light'|'Dark'} theme`. The Playground key card shows only the first 6 and last 4 characters of the DEK, so screenshots cannot leak key material.

## Blocking findings

### B1. The title slide claims the rehearsal "ran end to end" whatever the step statuses

`tools/build_deck.py:859-869` (`_slide_title`) prints *"Built from the rehearsal of {date}: the scripted demo ran end to end in {m:ss}."*
whenever `generated_at` and `total_seconds` are present. It never looks at the steps.

The plan defines `e2e_demo.py` to exit at the first failed step, report later steps as `NOT RUN`, and
still write the wall time. So a failed rehearsal yields a deck whose headline states something untrue.
The duration is wrong too: it is the time until the failure.

The default build is not `--strict`, and that default is the M5 plan's own command (Task 26B Step 4
and DoD 11: `python tools/build_deck.py`). Strict mode would stop it, because the other slides become
placeholders, but the default would ship the claim.

Reproduced in `s6a-review-r1/scenarios.py`, case 1: S5 `FAIL` and S6 to S11 `NOT RUN`. The title
slide read *"…the scripted demo ran end to end in 2:10."*, and the build listed 11 missing inputs.
No test covers slide 1's sentence: the figure tests cover slides 5 to 11 only.

**Fix.** Print the sentence only when no step is `FAIL`, `NOT RUN`, `ERROR` or of unknown status. A
listed `SKIP` can be named, for example "(S3 skipped)". Otherwise render a placeholder such as "the
rehearsal did not complete (first failure: S5)" and list it as missing, so that `--strict` fails. Add a
test with a failed step that asserts the sentence is absent and a placeholder is present.

### B2. The script promises all posture checks pass; the deck has no context for the real "5 of 11"

`GET /admin/posture` evaluates its eleven checks as if in production in every environment
(HANDOFF §6, `posture.py:340-420`). On the local compose stack several checks fail by design:
the bootstrap key is set, the LLM is the stub, the KEK is a literal, egress is `http`, and
verify-warn is allowed. The M5 verifications recorded this repeatedly ("Posture checks: 5 of 11
pass… by design in `local`", in M5-verify-r1/r2/r3 and M5UI-UI-review-r2). The demo seed needs the
bootstrap admin key, so the demo stack can never reach 11 of 11.

- **The script states the opposite twice.** `docs/demo/DEMO-SCRIPT.md:37` lists the proof as
  "posture checks all passing". `docs/demo/DEMO-SCRIPT.md:244` says "**Proof:** all posture checks
  pass; a rotated key is refused." On screen, the Overview checklist and the Admin page will show the
  failures while the presenter says the opposite. The script also says it "never quotes a figure that
  was not measured", and this claim is unmeasured and false.
- **The deck shows the result with no context.** `tools/build_deck.py:1739-1753` renders
  "Posture checks: 5 of 11 passed" in rose (`BE123C`), on a slide whose lede says staging and
  production refuse to boot when a check fails. Reproduced in `scenarios.py`, case 3. The slide cannot
  explain the number, because the S11 contract reads only `posture.passed` and `posture.total` and
  leaves out the same report's `enforced` (and `env`). S6b copies the contract fixed here.

**Fix.**
1. Correct both script lines. Say that the local stack fails the production-only checks by design,
   and that staging and production refuse to boot unless every check passes. Quote no number.
2. Add `posture.enforced` (and optionally `posture.env`) to the S11 contract, in the docstring and in
   M5-DEVIATIONS.
3. When `enforced` is false, caption the KPI with words such as "local stack, evaluated as if
   production; enforced at boot in staging and production", and use a non-alarm tone. Keep rose only
   for an enforced failure.
4. Add a test.

## Non-blocking findings (recommended; the fixer may take any of them)

- **N1. The `--skip-oracle` path.** `build_deck.py:1491` prints "Found in all three systems" and the
  slide 8 title says "three systems" for any member count. With two members, case 2 printed "Found in
  all three systems: 500" next to a missing-pairs placeholder; `--strict` stops it. The script's
  fallback has a related gap. `DEMO-SCRIPT.md:54` says to rehearse with `--skip-oracle`, but
  `build_deck.py --strict` will then refuse to build. `DEMO-SCRIPT.md:281` says slide 8 shows "the
  rehearsal's three-way join", which is true only for a deck built from an earlier full rehearsal.
  Either make the wording depend on the member count, or tell the presenter to keep the deck from the
  last full rehearsal.
- **N2. A casual run can overwrite the committed deck.** The default build is not strict and the
  default `--out` is the committed `docs/demo/dps-showcase.pptx`. Once S6b commits the real deck, a run
  without inputs, such as on a fresh checkout where `reports/` is absent, overwrites it with a deck
  made of placeholders. Consider strict-by-default for that path, or an explicit `--allow-missing`.
- **N3. `reports/` is not ignored.** The e2e report, the screenshots and the Keynote `reports/deck.pdf`
  all live there, and the plan says the PDF is not committed. S6b should add `reports/` to
  `.gitignore` or `info/exclude` (explicit-pathspec commits mitigate the risk).
- **N4. Keynote PDF export still to do.** S6b or the owner must export through Keynote and page through
  the result before F3 is accepted. Memory and RESUME §10 both say LibreOffice-only QA once missed a
  Keynote drop. My LibreOffice render of the complete-input and no-input fixture decks looks clean:
  no overflow, and the bench bars fit above the footer with the eight real bench configurations.
- **N5. The bench caption names a Mac the data does not record.** `build_deck.py:1328` says "on one
  Mac with N CPUs"; the bench JSON records only `host.cpus` and `machine` (`arm64`). Also, bench
  configurations beyond eight are dropped without an entry in the missing ledger.
- **N6. D14 is misquoted.** `build_deck.py:816` reads "payload sizes, rows per job, jobs and documents
  per day". Spec §12 reads "typical L1 payload size, rows per job, concurrent jobs, docs per day".
- **N7. The "nothing is derived" claim has an exception.** The module docstring says nothing is
  derived, but slide 1 turns `total_seconds` into m:ss. That is formatting, but slide 1 sits outside
  the figure tests; B1's test should cover it.
- **N8. Entity chips can overflow.** Slide 5's chips have no height limit; five or more rows of long
  entity names would push the two status lines past the footer at 7.04 in. The KYC sample's real
  types give about four rows, which fit.
- **N9. MIA caption wording.** `build_deck.py:1608` says "a coin toss scores lowest". The proxy's AUC
  is not symmetrised and can fall below 0.5 (`metrics.py:396-420`); "a coin toss scores about half"
  would be accurate.
- **N10. Private python-pptx API.** The tool uses `package._rels`, `part._blob` and `part._element`,
  while the extra's pin `python-pptx>=1.0` has no upper bound. Pin `<2`.
- **N11. Misleading commit message.** Commit `a717517` carries the plan's message ("showcase deck
  built from the rehearsal's measured outputs") but contains no deck and no rehearsal. The deviation
  log explains it, so no action is needed.
- **N12. Extras missing from the lane venv.** `ner`, `vertex` and `synth-ml` are not installed, so
  their tests skip. That is fine for S6a; the release venv for the DoD counts should install every
  extra, including `deck`, as the implementer's hand-off item 4 says.
- **N13. Slide 1 notes overstate the source of the numbers.** They say the numbers "come from the
  rehearsal named in the footer", but slide 7's bars come from the bench files, which are dated
  separately.

## What I did not do

I did not try a Keynote export. The memory note `pptx-decks-keynote-rendering` (2026-10-14) records
that the same attempt hung from an agent session, and an unattended modal in Keynote could disturb
the owner's desktop. A Quick Look thumbnail (`qlmanage -t`) produced nothing, which is expected
because the deck ships no thumbnail.

## Artefacts

- `~/.cache/dps-build/reports/s6a-review-r1/`:
  - Gate logs: `ruff.log`, `ruff-format.log`, `mypy.log`, `mypy-deck.log`, `pytest-full.log`.
  - Probes: `review_build.py` checks the package, determinism and strict behaviour. `scenarios.py`
    holds the B1, N1 and B2 reproductions.
  - Renders of the fixture decks: `lo-render-fixture-complete.pdf` and `lo-render-no-inputs.pdf`
    (headless LibreOffice).
- Files reviewed: `tools/build_deck.py`, `tests/test_deck.py`, `docs/demo/DEMO-SCRIPT.md`,
  `pyproject.toml`, `HANDOFF.md` §1, contract §1, `docs/superpowers/build/M5-DEVIATIONS.md`
  (Task 26B and Task 26).
