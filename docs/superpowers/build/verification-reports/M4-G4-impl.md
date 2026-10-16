# M4 G4 implementation report — Tasks 14–18 (crawler, no-submission proof, scan jobs/evidence, browser-worker, scans API)

Worktree `/Users/neelu/document_intelligence/dps-wt-m4`, branch `m4`. Start state: clean tree at `d987144` (G3 fix r2); no earlier G4 attempt existed (no report, no WIP).

## Commits (author NilenduGanguli <nilendu.ganguli@gmail.com>, no trailers)

| Commit | Task | Message |
|---|---|---|
| `a080c35` | 14 | feat(scanner): ScanRunner — budgeted same-origin crawl, SPA click discovery, parallel view, captures |
| `8a98baf` | 15 | test(scanner): prove no submission ever leaves the scanner — control, full scans, adversarial attempts, static check |
| `17f4351` | 16 | feat(scanner): scan and finalize handlers, persona/field exports, scan evidence pack, key purge |
| `09e077b` | 17 | feat(scanner): browser-worker process — scan-only consumer, Chromium readiness, ops endpoints on 9401 |
| `2a4fa35` | 18 | feat(api): scans routes — create (key and seed once), pages, events, inventory, export, cancel, persona preview |

Each commit carries its section in `docs/superpowers/build/M4-DEVIATIONS.md` (Tasks 14–18). Contract changes landed with their code: §3.2 amendment (scan cancel → finalize) in `17f4351`; §9 rows (`GET /scans/{id}/events`, `POST /scans/personas/preview`, create/replay shapes and codes) in `2a4fa35`.

## What was built

- **T14** `dps/layers/scanner/runtime.py` (Playwright-free `ScanSink`, `MemoryScanSink`, `ScanRuntime` with secret fields hidden from `repr`, `RunOutcome` + `fills_skipped`, `LeaseLost`); `crawler.py` (`ScanRunner`, `Target`, `ClickReplay`): BFS with page/depth/template/time budgets, deny/scope verdicts, click-only SPA route discovery and replay, per-page pipeline (extract → classify → risk flags → masked original + annotated → synthesize and obfuscate fills in fresh armed tabs → filled screens → obfuscated capture → discovery), dialogs dismissed, popups closed through the guard, every tab closed through `ScanGuard.close`, context through `close_scan_context`. `inventory.field_records`. `/fixtures/dialogs` demo page; `make_runtime` helper.
- **T15** `tests/scanner/test_no_submit.py`: positive control, full crawls with JS hardening off and on (obfuscate mode), adversarial submission attempts on filled pages, static source check.
- **T16** `dps/layers/scanner/__init__.py` (`ScanSetupError`, `open_runtime`, `DbScanSink`, `scan_job`, `scan_finalize_job`, `finalize_scan`), `inventory.py` (`inventory_rows`, `inventory_csv`, `write_csv` with formula guard, `build_export` = `dps.scan-export.v1` JSON + CSV, `build_scan_evidence`), evidence HTML "UI scan" section, `ScanStore.all_pages`, `jobs.cancel` scan → finalize.
- **T17** `dps/browser_worker.py` (ops server `/health` `/readyz` `/metrics`, `build_consumer(kinds=(scan,), role="browser-worker")`, `amain`, `main`).
- **T18** `dps/layers/api/routers/scans.py` (10 routes), `dps/app.py` (router, CSP `img-src 'self' data: blob:`, `refresh_browser_readiness` + 15 s probe task).

## Gates (final tree, `2a4fa35`)

- `./lane.sh ruff format --check dps tests tools` → 289 files already formatted
- `./lane.sh ruff check dps tests tools` → All checks passed
- `./lane.sh mypy dps` → Success: no issues found in 119 source files
- `./lane.sh pytest -q` (with `DPS_TEST_TESSERACT_CMD` = wrapper around `docker run --rm -i --network none --entrypoint tesseract data-privacy-services:local`) → **2078 passed, 71 skipped** in 454 s. Skips: 51 source-pg, 3 minio, 3 app container, 3 OCR mock (not part of the M4 lane stack, not started), 1 `[ner]`, 1 presidio, 1 test that looks for tesseract on PATH only (pre-existing, not G4).
- Services used: this lane's `dps-db` (compose, port from `.env`), demo site via host `node` on the lane port; brought down afterwards (`docker compose down`; the node process stopped). No frontend files changed (no npm gates needed).

Per-task test counts: test_crawler 10 (incl. OCR test of every stored PNG in both modes), test_no_submit 5, test_inventory_export 4, test_evidence_html_scan 2, test_scan_job_e2e 5, test_browser_worker 5 (incl. real `python -m dps.browser_worker` process running a scan from the queue and exiting 0 on SIGTERM), test_scans_api 11.

## Programme acceptance G4 (§2.3 row) — evidence

- **Crawler budgets:** `test_page_budget_and_depth_limit` (4-page budget → `truncated="pages"`, `budget` rows; depth 0 → one page), time budget (`truncated="time"`, nothing left `queued`/`visiting`), template budget (exactly 3 of 8 FAQ pages), deny/other-origin/template skip rows.
- **Nothing is ever submitted (counting server):** demo site `/__received` `non_get`, `form_get`, `by_path`, `logout_hits`, `admin_hits` unchanged across: two full crawls (hardening off/on), the adversarial test, the full-crawl Task 14 test, the e2e job test and the browser-worker process test. Request log: blocked POST (reason `method`) ≥ 3 and 0 allowed non-GET; with hardening off the auto-submitting `/apply/card` select fired a real form POST, logged as blocked. Evidence pack: `scan.submissions = 0`, `scan.non_get_allowed = 0` (measured from `dps_scan_request`); a non-zero count fails the scan (`submission_detected`).
- **Scan jobs / evidence:** e2e — job `succeeded`, ≥ 15 `done` pages, four PNG screenshots on `/apply/identity` served by `ArtefactService.open`, evidence row + HTML ("Submissions: 0"), export JSON (3 personas; display persona's Aadhaar synth value and token equal the stored `fill_synthesize`/`fill_obfuscate`), `key_enc` NULL, one `scan.navigate` audit row per navigate event. Cancel of a running scan → finalize → `canceled` with evidence + exports + key purge; queued scan → canceled at once; missing storage state → `failed/secret_unavailable`, key purged; storage state resolved only under `DPS_SECRET_DIR/<tenant>/<ref>/`.
- **Leak checks (counts only, never printing values):** normalised search of every `dps_scan_*` row, events, request log, evidence pack, export JSON/CSV, evidence HTML, capture reveal maps and the job row for the demo values (name, Aadhaar, PAN, card, email, mobile, address, customer id, account number, application ref, dob) → 0 hits, with a positive control that the haystack holds the stored export. OCR (tesseract) of every PNG the crawler stores for `/profile` and `/profile/contact` (original, annotated, both filled screens, masked capture) in both modes → 0 readable demo values; control (unmasked originals) reads ≥ half of them.
- **Browser-worker process:** ops endpoints only (404 otherwise), scan-only consumer with role `browser-worker` (DB row checked, removed on drain), real-process run of a queued scan.
- **Scans API:** key + seed returned once (`Cache-Control: no-store`), stored spec holds neither and has `email=<EMAIL>`, replay returns no key, wrap key returns only wrapped material, 403 without scope `scan`, 404 across tenants, pagination/cursors, inventory CSV, export 409 → 200, cancel 409 `not_cancelable`, persona preview reproducible and equal to scan persona 1, OpenAPI has no submit route, app and scanner modules import without Playwright (scanner API modules also without Pillow).

## Deviations (full text in M4-DEVIATIONS.md)

1. One `.click(` call site (`ScanRunner._click`) used by discovery and replay; static check asserts 1 `.click(` + 2 `self._click(` calls (plan: 2 sites). One comment in `dom.py` reworded (static check bans the word "submit").
2. Filled screens are re-extracted after the fill; `capture_screen` gained additive `sweep=` and `capture.shown_values` is public (pre-fill values swept on the filled screen; persona values stay readable).
3. A fill whose tab navigated is never captured; navigation-log targets for unreachable hosts use `stored_host` form (value-free host).
4. `jobs.cancel` (M2 touch point): a started scan gets the cancel-finalize; a never-started scan is canceled at once (reconciles plan Task 16 and Task 18 tests); contract §3.2 amended.
5. Storage-state refs resolved tenant-scoped (`<tenant>/<ref>`, SEC-2 layout) — at integration swap to main's `resolve_secret_ref(tenant_id, ref, settings)`.
6. `ScanStore.all_pages` (plan's `limit=100_000` was silently capped at 1000).
7. Browser-worker `/readyz` not ready until Chromium is up; `serve_ops(port, host)`.
8. Scans API: replay = 200 + `Idempotent-Replay` (M2 convention); key-material codes reuse `key_material_invalid` / `wrap_key_invalid`; missing DB = M2's `db_unavailable`; OpenAPI test includes `GET /scans`; import test split (Pillow is in the app image via `[docs]`).
9. Process: `crawler.py` was written from the plan's complete code before its test file (tests then run against it, all green first time; behaviour cross-checked with a probe crawl dump and by viewing the PNGs).

## Open issues / notes for later groups

- Task 19 (log scan for M4) not done here — no "no values in logs" claim is made by this group; crawler logs only `page_no/status/fields/blocked`.
- Residuals carried from G3 (unchanged): input values changed by script after extraction (N8), closed shadow roots, CSS `content`/canvas text; with `scope.shadow_dom=false`, open-shadow text is only covered through the capture census classification.
- Integration (WP-I): `dps/layers/scanner/__init__.py::_storage_state` must call SEC-2's `resolve_secret_ref(tenant_id, ref, settings)`; `dps/jobs.py::cancel` and `dps/app.py` are merge hotspots.
- One pre-existing test skips because it looks for `tesseract` on PATH only (ignores `DPS_TEST_TESSERACT_CMD`).
