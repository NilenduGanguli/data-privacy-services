# M4 G7 — implementation report (Tasks 24, 25)

Worktree `/Users/neelu/document_intelligence/dps-wt-m4`, branch `m4`. Lane: COMPOSE_PROJECT_NAME
`dps-wt-m4`, DPS_IMAGE_TAG `m4`, app 8414, demo-site 8420. Base `9aef742` (merge of the console sub-lane
plus the wiring fix). Resume check at start: worktree clean, no G7 commits, no earlier G7 report, so
nothing was redone.

## Commits (author NilenduGanguli <nilendu.ganguli@gmail.com>, no trailers)

| Commit | Task | Paths |
|---|---|---|
| `a54745f` test(smoke): scanner checks — scan the demo site end to end, prove zero submissions, captures, export, evidence | 24 | tools/smoke_test.py, tests/test_tools_m4.py (new), docs/superpowers/build/M4-DEVIATIONS.md |
| `d4fc2b7` docs: M4 UI scanner — API as built, run instructions, handoff | 25 | HANDOFF.md, README.md, docs/technical/api-m4.md (new), docs/superpowers/build/M4-DEVIATIONS.md |

The commit messages are the plan's. The worktree was clean after the second commit. Nothing was pushed and no tag was touched.

## Task 24: smoke extension and compose-level scanner gate

**Test-first.** I wrote `tests/test_tools_m4.py` first: 12 offline tests that drive `scanner_checks` against an `httpx.MockTransport` fake app and fake demo site.

- Before the code existed, 11 of them failed with `AttributeError`, which was the expected reason.
- The 12th test, the readiness wait, came after the first live run (see below). It failed too (9 failed) until the wait was implemented.

The tests cover:
- A clean scan passes all 11 checks. The output contains none of the demo values, the DEK or the seed, and the scan request has the right shape.
- The scan fails if any of the four site counters moves (`non_get`, `form_get`, `logout_hits`, `admin_hits`). This is parametrised over all four.
- A demo value in the export fails the scan, and the verdict does not print the value.
- A scan that is still running at the deadline fails.
- If no browser-worker is online, the check fails and no scan is created.
- Readiness that turns ok on the third poll passes.
- The scanner block runs only when `DPS_SMOKE_SCANNER` is exactly `1`.
- The demo address is lane-safe.

**Implementation.** In `tools/smoke_test.py`, `main()` runs the scanner block when `DPS_SMOKE_SCANNER=1`. It runs before the final "console served" check.

The block is `scanner_checks(c, demo, *, timeout, interval, browser_wait)` with 11 checks:
1. browser worker online
2. scan accepted with key and seed
3. scan succeeded
4. classification and risk flags
5. no submission reached the site
6. parallel view captured
7. screenshot served
8. masked capture served
9. persona export
10. evidence: submissions 0
11. scanner console served

The output is check names and counts only. The key and seed are dropped from the create response right after the check reads them.

**Deviations** (all logged in `M4-DEVIATIONS.md`, Task 24):
- There are 11 checks, not 10. "masked capture served" was added.
- The demo-site address is lane-safe: `DPS_TEST_DEMO_SITE`, otherwise `DPS_DEMO_SITE_PORT`. The plan's literal 8410 would read another stack's counters on a lane.
- The deny-list counters `logout_hits` and `admin_hits` must not move either.
- The export is checked against all 10 demo customer values. The plan listed 5 literals. The birth date is left out because a generated persona can share it.
- **Browser readiness is awaited for up to 60 s.** The first live run failed "browser worker online" with `unknown`: the app refreshes that component every 15 s from heartbeats, and the smoke ran right after `up`.
- `DPS_SMOKE_SCAN_TIMEOUT` is configurable.
- The seed check reads `synthesis_seed.seed`.

## Task 25: documentation as built

**`docs/technical/api-m4.md` (new)** covers:
- Every scans route and the artefact and evidence routes used by scans, with the `ScanSpec` field table.
- Request and response examples taken from a live scan on this lane. They contain synthetic persona values and tokens only; the key and seed are placeholders.
- The accept-time error table **as built**:
  - The plan's `db_unconfigured` is `db_unavailable`.
  - The plan's `consistency_invalid` is split into `key_material_invalid` and `wrap_key_invalid`.
  - Added codes: `auth_invalid`, `idempotency_*`, `queue_full`.
- The worker-side `error_code`s, the read-side codes and `not_cancelable`.
- The scan lifecycle (queued → running → succeeded/failed; cancel → finalize → canceled; truncation by budget).
- The shapes of pages, fields, requests, events and inventory.
- The risk-flag rules, the artefact variants and fail-closed captures.
- The formats `dps.scan-export.v1` (JSON and CSV) and `dps.capture.v1`, with the `POST /reveal` call.
- The evidence `scan` block. The doc states that the scan pack's `verification` is not a sweep over page data.
- The D4 guard rules and the counting-server proof, persona preview, readiness and metrics.

**`README.md`:**
- Status paragraph and an API reference row.
- A "Scan a web front end (M4)" section: compose profile, `/scanner/new`, the demo URL and session, the smoke gate.
- The `scanner` extra plus `playwright install chromium`, the `browser` marker notes and the M4 integration gate command.

**`HANDOFF.md`.** The M4 settings table (Task 1) and §12 (Task 20) already existed. I added:
- An M4 status line.
- §1 rows: browser image, scanner API and console, smoke checks, `scanner` extra.
- §4: the compose-only and smoke variables (`DPS_DEMO_SITE_PORT`, `DPS_SMOKE_SCANNER`, `DPS_SMOKE_DEMO_URL`, `DPS_TEST_DEMO_SITE`, `DPS_SMOKE_SCAN_TIMEOUT`).
- §5: the `browser` readiness component, the browser-worker ops surface on 9401 and the `dps_scan_*` metrics.
- §7: tenant-scoped `storage_state.json` refs (`dps/ui/<name>`) and the browser-worker's need for `DPS_JOB_KEK`.
- §9.
- §12: traps 8 (readiness `unknown` after start) and 9 (lane demo port), and the smoke gate with this lane's measured run.

Every factual claim in the new doc was checked against the code, for example: config names (`DPS_JOB_TENANT_MAX_QUEUED`), fill statuses, skip verdicts, risk-flag rules, artefact kinds `export_json`/`export_csv`, `redact_url` rules and the cancel semantics in `jobs.cancel`. Several first-draft claims were wrong and were corrected before the commit.

**Contract:** Tasks 24 and 25 changed no contract name, so the contract was not edited.

## Gates (exact)

| When | Result |
|---|---|
| Before both commits | `./lane.sh ruff format dps tests tools`: 293 files unchanged (the two new or edited files had been formatted earlier) |
| Before both commits | `./lane.sh ruff check dps tests tools`: All checks passed |
| Before both commits | `./lane.sh mypy dps`: Success, no issues found in 119 source files |
| Before commit 1 | `./lane.sh pytest -q`, lane scanner stack up: **2126 passed, 87 skipped**, 1 warning (Starlette deprecation), 438 s |
| Before commit 2 | `./lane.sh pytest -q`: **2126 passed, 87 skipped**, 440 s |
| | `./lane.sh pytest -q tests/test_tools_m4.py tests/test_tools_m2.py`: 16 passed |

- The previous baseline was 2114 passed; the difference is the 12 new tests.
- The 87 skips are integration markers for services this lane does not run: source-pg, minio, ocr, oracle, mongo, the tesseract binary, `ner`/presidio and the demo connections. No M4 code path is skipped.
- No frontend file changed, so the npm gates were not needed.

## Live verification

- `docker compose --profile scanner up -d --build`: app, worker, browser-worker, demo-site and dps-db healthy; migrate exited.
- First live run: FAIL at "browser worker online — unknown". This is the readiness race, fixed test-first as described above.
- After the fix I ran `docker compose restart app` to reproduce a fresh-readiness start, then `DPS_SMOKE_M2=off DPS_SMOKE_SCANNER=1 ./lane.sh python tools/smoke_test.py` with the API key taken from the app container's environment (never printed):
  - Result: **all 34 checks passed** (23 Layer 1 + 11 scanner; 2 OCR checks skipped without `--profile docs`).
  - The scan succeeded: 21 pages, 63 fields, 51 personal, 9 risk flags with the 3 expected kinds present.
  - Demo-site counters: `non_get +0, form_get +0, logout_hits +0, admin_hits +0`.
  - `non_get_allowed 0`, blocked `{'GET': 1, 'POST': 71}`.
  - Identity page: 4/4 parallel-view screenshots; screenshot served as PNG; 3 masked captures, one served as PNG.
  - Persona export: 5 personas, 0 demo values found. Evidence `scan.submissions` 0 and `non_get_allowed` 0. `/scanner/new` served.
  - Output saved to `probe-m4-G7-smoke-scanner.txt`.
- Without `DPS_SMOKE_SCANNER`: 23 checks passed, as before.
- **Log scan** (`probe-m4-G7/logscan.py`; needles: the 10 demo customer values other than dob, both name parts and the demo session cookie value; exact and squashed alphanumeric matching):
  - Compose logs of app, worker and browser-worker: 0 hits.
  - The smoke output: 0 hits.
  - The new docs (api-m4.md, README, the added HANDOFF lines): 0 hits.
- The probe responses used for the doc examples are in `probe-m4-G7/ex_*.json`. They contain no key material; the preview seed was masked in the doc.
- `docker compose --profile scanner down`: no container of `dps-wt-m4` is left. No other project was touched.

## Open issues (not in G7 scope; carried from earlier reports)

1. The scan pack's `verification` block is not a measured sweep (`hits: 0` is a constant). The doc now says so; the backend fix belongs to verification or WP-F.
2. Scan finalize stores no `evidence_json` artefact.
3. `NumberedRiskFlag.path` and `InventoryRow.path` hold the redacted URL, not a path. Documented as built.
4. The browser image is 4.22 GB, and Chromium runs `--no-sandbox` in the container. Both are on the M5 hardening backlog.
5. `HANDOFF.md` §7 still says connection `secret_ref`s are not tenant-scoped (the M2 note). That is true on this lane, which predates SEC-2. Scanner storage-state refs are tenant-scoped here. SEC-5 (WP-I) replaces the scanner's copy with main's resolver at merge.
