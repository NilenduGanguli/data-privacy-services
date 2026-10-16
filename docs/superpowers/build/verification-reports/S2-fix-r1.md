# S2 — fix round 1 on branch `m4` (main merged into it)

**Verdict: all six blocking findings fixed, test-first. Every gate is green. Committed and pushed.**

| | |
|---|---|
| Date | 2026-10-14, 22:20 – 23:59 IST |
| Tree | `dps-wt-m4`, branch `m4`: `a0e8d26` → **`a3cd667`** `fix(m4): S2 fix round 1 — …` (44 files, +1574/−146). Author `NilenduGanguli <nilendu.ganguli@gmail.com>`, no trailer. Pushed by `tools/dev/autopush.sh` (log line `23:59:01 pushed m4 a3cd667`, gitleaks clean). Worktree clean at the end. |
| Findings fixed | `S2-gates-r1` B1 · `S2-captures-privacy-r1` B1 · `S2-adaptations-r1` B1 · `S2-console-r1` B1, B2, B3 |
| Stack | Lane `m4` (app 8414, demo site 8420, …). Images rebuilt from the final tree, stack restarted with profiles `docs sources oracle scanner tokenizer`, **left up and healthy**. `sha256` of the changed modules in `app` and `browser-worker` = worktree. |
| Logs and probes | `~/.cache/dps-build/s2-fix-r1/` (`logs/` numbered in run order, `probes/`, `console/`, `run_gates.sh`, `run_old.sh`, `log_scan.py`, `tess.sh`) |
| Deviation logs | `M4-DEVIATIONS.md` and `M4-DEVIATIONS-console.md`, sections "S2 fix round 1"; `HANDOFF.md` §16.1; contract §8.1 and §9 (M5 rows) |

## 1. Test-first proof

The new tests were run against the code of `a0e8d26` (`git archive` of HEAD with the new tests copied on
top, `run_old.sh`, log `logs/01-new-tests-on-old-code.log`). **Every one failed:**

- API, posture, seeds: the 3 scans-API tests, the posture test and `test_identifiers_are_rule_valid_fakes`
  failed. `test_seed_model.py` and `test_seed_sources_pg.py` failed at import, because `DEMO_MODEL_VERSION`
  did not exist. Separately, `logs/07-demo-ids-in-draw-ranges.txt` shows the old model put **11,989 Aadhaar
  and 3,998 SSN** demo ids inside a surrogate draw range; model 2 puts **0** there.
- Frame pixel tests: 8/8 failed. Frame OCR tests: 8/8 failed. Crawler fill-scroll test: 2/2 failed.
- Console navigation browser test: failed on the pre-fix image (no menu button).

## 2. The fixes

### 2.1 Captures: masks of text in same-origin frames (captures-privacy B1)

**Cause (all three confirmed):**
- `TEXT_RECTS_JS` / `CENSUS_RECTS_JS` added the child document's own scroll.
- The offset was the iframe's border box, not its content box.
- Transforms were ignored.

**Fix** (`dom.py`, `capture.py`, `models.py`):
- Child-frame rects are taken in the child's **viewport**. Whether a frame is the top frame comes from
  Playwright's frame tree, not from page globals: `window.parent` is replaceable by a page.
- Rects are mapped through `dom.FrameGeometry.content`: the border box plus `clientLeft`/`clientTop` and
  padding. For nested frames this composes through Playwright's border box of each frame element.
- Every mask in a child frame is **cut to what that frame and the frames around it show**. A value
  scrolled out of view gets no stray mask over unrelated content.
- **Fail closed:** a read frame is painted over whole as `FRAME` (and nothing inside it is masked one by
  one) when any of these holds:
  - the frame element or an ancestor in its document has a transform other than a translation, a
    `scale`/`rotate`, a `zoom` or a motion path;
  - its border box is not its layout size;
  - its content box is not the child's `innerWidth`/`innerHeight`.
- Unread frames keep their border box. The local-only `unmasked` control paints unread frames only.
- `FrameFacts.offset` of a read frame is now its content box.

**Tests:**
- `test_capture_census.py::test_text_in_a_same_origin_frame_is_masked_where_the_frame_draws_it`: pixels at
  the browser's own box of each value. Cases: fragment-scrolled, 40 px border, 50/60 px padding,
  `scale(2)`, each alone and nested in a bordered, padded frame.
- `test_capture_ocr.py::test_text_in_a_same_origin_frame_is_never_readable`: masked capture plus both
  obfuscated captures, against an unmasked control that reads both values.
- `test_crawler.py::test_a_fill_that_scrolls_a_frame_never_shows_its_values`: `ScanRunner` in both modes on
  an iframe card form below the fold.
  - OCR of `original`, `annotated`, both `filled_*` and `masked_capture` reads **0**.
  - The control fills the field the same way, shoots unmasked and reads both values.
  - The server received 0 non-GET requests.

**Regression check:** the existing capture, DOM, fill and OCR modules all pass (143). In the smoke test
the demo scan still captures **3/3 masked, 0 withheld**.

**Still open, unchanged (not in this round):** captures-privacy N1 (late text in an obfuscated capture) and
N2 (closed shadow roots, CSS generated content, canvas text, script-set input values). They are now listed
in `docs/technical/api-m4.md` as known limits.

### 2.2 `/jobs` routes and the `scan` scope (adaptations B1)

**Fix:**
- `_owned` (get, partitions, retry) and a kind lookup before cancel, pause and resume call
  `require_kind_scope`. A scan needs `scan`: otherwise 403 `scope_required`, audited `job.<action>`
  rejected.
- `GET /jobs` leaves scans out for a key without `scan` (`list_jobs(exclude_kind=…)`, so pages stay full).
  `GET /jobs?kind=scan` is 403.

**Tests:**
- The existing every-scans-route test now also walks every `/api/v1/jobs/{job_id}…` operation of the
  OpenAPI document.
- A new test uses a static key and a database key, each jobs-only. The scan stays `queued`, two rejected
  audit rows are written, and a key with both scopes still cancels.

**Live** (`probes/jobs_scope_probe.json`), with a database key holding `jobs`+`detect`:
- cancel, pause, resume, retry, `GET /jobs/{scan}` and `/partitions`: all **403 `scope_required`**;
- `GET /jobs`: 0 scan rows for that key (the full key sees 18);
- `?kind=scan`: 403;
- the scan state is unchanged; the audit table holds `job.cancel|pause|resume|retry rejected 403` ×1 each;
- the probe key was disabled afterwards.

### 2.3 Scan rows on Overview and Reports (console B2)

**Fix:**
- `reports.scan_host()` gives `host[:port]` of the redacted origin.
- `_brief` uses it for a scan. `_evidence_summary` uses `source.object` or that host, and adds `forms` and
  `personas`.
- `EvidenceTable` UI-scanner rows read `UI scan of <host>` and
  `N pages · N forms · N personas · N submissions`.
- The dead `scanEvidenceRow`/`isScanJobId` code is deleted, with its test.

**Tests:**
- API test with a pack from the real `build_scan_evidence`.
- Vitest in `ScanEvidence.test.tsx`, `Reports.test.tsx` and `Overview.test.tsx`.

**Live:** the API summary names the host for 2/2 recent scans and 17/17 UI packs, all with forms and
personas. In the console, before the smoke, Overview showed 2/2 scan rows with the host and Reports 17/17;
"UI scan of —" appeared 0 times.

### 2.4 Admin › UI-scanner hosts (console B3)

**Fix:**
- `Settings.scan_fallback_hosts` is the single rule that `POST /scans` and the browser-worker apply:
  `DPS_SCAN_REGISTERED_HOSTS` on local/dev, empty elsewhere.
- `GET /admin/posture` reports it as `scan_fallback_hosts`.
- `HostsCard` takes `env` and the fallback list. On local/dev with no host it lists the hosts as
  "service default" and says staging and production refuse scans until a host is registered. On staging
  and prod it keeps "Scans are refused until one is." An unknown environment never claims a refusal.

**Tests:**
- `test_posture_m5.py` (local, dev, staging and prod).
- `HostsCard.test.tsx` (6 cases).

**Live:** posture `env local`, `scan_fallback_hosts ['localhost','127.0.0.1','demo-site']`. The card lists
the 3 hosts as service default and does not say "refused" (`console/admin-hosts-1440.png`).

### 2.5 Navigation below 1100 px (console B1)

**Fix:**
- `Layout.tsx` gains a topbar `button.nav-toggle` with `aria-controls="sidebar"`, `aria-expanded` and an
  "Open/Close navigation" label.
- The sidebar opens as a drawer: `.shell.nav-open` plus a `.nav-scrim`.
- Escape and a scrim click close it and return focus to the button. Any navigation also closes it.
  Opening it focuses the active section.
- The `vault.css` rules are ported verbatim.

**Tests:**
- `tests/console/test_console_nav.py`, in the browser gate, at 390 and 1024 px:
  - from each of the 7 pages, each of the 7 sections is reached through the drawer (Policies may open
    `/policies/<name>`);
  - no horizontal scroll while the drawer is open;
  - Escape, scrim click and focus behave as above; 0 page errors;
  - at 1440 px the sidebar is shown and the button is not.
- `Layout.test.tsx` checks the markup. `frontend/tests/layout.test.ts` checks that the drawer CSS equals
  the mockup's.

**Live:** at both 390 and 1024 px, all 7 pages have 1 menu button, the open drawer shows 7 sections, there
is no horizontal scroll, and there are 0 page or console errors (`console/check.json`,
`drawer-open-390.png`).

### 2.6 D-A seeded-demo flake — demo model 2 (gates B1)

**Path confirmed:**
- The demo `national_id` column is NATIONAL_ID.
- Its 11,989 Aadhaar-shaped values sit in the 10¹⁰ space that `unissued.twelve_digits` (and
  `SurrogateEngine._aadhaar`) draw from.
- 1,200 simulated runs of the run's own surrogate path gave **17 runs with a hit (1.4 %), all Aadhaar**
  (`logs/06-da-sim-model1-*.log`). That is exactly 11,989²/10¹⁰. The higher rates the verifiers saw are
  small-sample noise.
- The demo SSNs also shared the SSN surrogates' 9xx range. NATIONAL_ID maps 9-digit values to `000…`, so
  the demo column did not hit there, but an SSN-typed column would have.

**Fix** (`tools/seed_model.py`):
- Demo Aadhaar-shaped ids now start with **0**: never issued, while surrogates take 1. The same ten digits
  are kept and the Verhoeff digit is recomputed.
- Demo SSNs use area **666**, which is never issued and drawn by no surrogate. A seed-keyed stride makes
  every customer's SSN distinct; the source now has 20,000 distinct national ids instead of 19,998.
- NINO (`QQ`), CURP (third letter `X`) and PAN (sequence 0001–9999) were already outside every draw range.

**Version marker:**
- `DEMO_MODEL_VERSION = 2` goes into every seed marker: PG `_dps_seed_info.version`, Oracle
  `CRM.DPS_SEED_INFO.MODEL_VERSION`, MongoDB `dps_seed_info.version`.
- A stack seeded by an older model with the same parameters is **reloaded** without `--reset`; in PG the
  restore copy is refreshed too.
- On this lane, `docker compose up` printed "reloaded: schema bank held an older demo data model" and
  "restore copy: refreshed". `--target all` reloaded Oracle and MongoDB.

**Tests:**
- Deterministic `test_no_seeded_identifier_lies_where_a_surrogate_can_draw`: seed 7 × 20,000 rows, plus
  two more seeds.
- `test_the_draw_ranges_hold_every_surrogate_the_engine_draws`: the ranges are the engine's.
- `test_demo_ssns_depend_on_the_seed_and_stay_distinct`.
- The PG reload test and the Oracle/MongoDB reload checks in `test_seed_shared_ids.py`.
- The D-A test assertions are **unchanged**. Format assertions follow the new demo ranges.

**After the fix:** 300 more simulated runs had **0** hits (`logs/06-da-sim-model2.log`). Both seeded-demo
D-A tests passed in the whole suite and in the M2 gate.

## 3. Gates (all on the final code; the four marker runs went sequentially after the whole suite)

| # | Gate | Result |
|---|---|---|
| 1 | `ruff format --check` / `ruff check` / `mypy dps` | 426 files formatted / all checks passed / no issues in 150 files |
| 2 | Whole suite, `DPS_TEST_REQUIRE_SERVICES=1`, tesseract wrapper set | **3477 passed, 8 skipped, 0 failed** (20:10) |
| 3 | `-m "docs or ocr"` | **139 passed**, 2 skipped (host tesseract) |
| 4 | `-m "db or pg_source or s3"` | **408 passed, 0 failed** (8:57). It was 404 passed + 1 failed. |
| 5 | `-m "oracle or mongo"` | **162 passed**, 1 skipped (MongoDB has no composite keys) |
| 6 | `-m browser`, tesseract wrapper | **215 passed, 0 skipped** (11:57). It was 194; +21 new. |
| 7 | Frontend: `npm ci && npm test && npm run build` | node 44/44; Vitest 27 files, **364 passed**; build plus `check-bundle` OK |
| 8 | `DPS_SMOKE_SCANNER=1 DPS_SMOKE_REQUIRE_OCR=1 tools/smoke_test.py --m3`, on the final images | **all 52 checks passed**. The scan covered 21 pages, 63 fields (51 personal); 3 masked captures; 60 POST and 1 GET blocked; `non_get_allowed` 0; M3 runs 3/3 at 20,000 rows; consistency 3/3. |

**Whole-suite skips**, all environmental or by design:
- 1 Mongo capability skip;
- 1 test needing a host tesseract;
- 5 optional extras not installed in the lane venv (pandas, mostlyai ×2, presidio, the spaCy model).

The S2 gates report found that a venv with every extra passes these.

## 4. Proofs (counts only)

**D4** (demo-site `/__received`):
- Before the gates: 20/4/2/2 (non_get, form_get, logout, admin).
- After the gates: 36/8/4/4. The delta, 16/4/2/2, is exactly the deliberate positive controls, which ran
  twice (whole suite and browser gate).
- Over the smoke on the final images every delta was **0** and `by_path` did not change. The smoke's own
  scan had `non_get_allowed` 0 and 0 submissions.

**LLM exposure:** `dps_llm_raw_pii_sent_total 0.0` before and after the smoke.

**Logs** (`logs/53-log-scan.txt`, all 15 containers, after everything):
- **0 tracebacks**; **0** demo-site values and **0** session-cookie hits.
- The app's only warning is the smoke's deliberate `auth_rejected` 401.
- The database error lines are the negative tests' own: append-only and immutability triggers, RLS,
  permission checks, unique constraints, a statement timeout, a read-only transaction, wrong-password
  logins.

**Source vs target, D-A:**
- The seeded-demo copula and surrogate tests assert `survivors == {national_id: 0, tax_id: 0}` and
  `(in_source, issuable) == (0, 0)`. Both passed in gates 2 and 4.
- By construction no demo id can now be drawn, as checked by the deterministic test.

**Masked captures:** OCR read 0 values in every frame case and in every crawler-stored screen, with
positive controls reading them.

## 5. Notes

- **N1 — main has moved** (`d2fdc3f`, after `5c11e49`). This round covers `m4` only. The m4 → main merge
  must rerun the §2.5 gates on the combined tree. When main's stack next runs `docker compose up`, its
  `seed-sources` one-shot reloads `bank` to model 2 automatically. Oracle and MongoDB need
  `tools/seed_sources.py --target all`. Any screenshots or notes that quote demo ids show model-1 values.
- **N2 — tracked screenshots (known A-N2).** The console tests rewrote the 54 tracked PNGs in
  `docs/superpowers/build/m3-shots/`. They are copied to `s2-fix-r1/m3-shots-from-run/` and restored to
  HEAD by explicit pathspec (list: `restored-shots.txt`).
- **N3 — current Overview.** It shows no scan row now, because the smoke's six M3 jobs are the most recent.
  Before the smoke it showed 2/2 scan rows naming the host.
- **N4 — stack state left for the verifiers:**
  - demo sources at demo size, model 2 (PG `bank` 20,000 customers, Oracle `CRM.*`, Mongo
    `crm.profiles`);
  - demo-site counters at 36/8/4/4 — take a fresh baseline;
  - the disabled probe key `s2fix-r1-jobs-only`;
  - a smoke group `smoke-m3-<epoch>` and the rewritten `*_smoke` targets.
- **N5 — one decision to note.** For gates B1 the round took the "move the demo ids" option. It did not
  take the alternative of having a synthesize run redraw any surrogate that is already in the source
  column. A UAT source full of never-issued test ids can therefore still give a review-required run;
  that run fails closed, as designed.
