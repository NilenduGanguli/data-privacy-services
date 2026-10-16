# M4 G4 review, round 1 (Tasks 14–18): crawler, no-submission proof, scan jobs/evidence, browser worker, scans API

Reviewer: independent (did not rely on M4-G4-impl.md). Worktree `/Users/neelu/document_intelligence/dps-wt-m4`,
branch `m4`, HEAD `2a4fa35`. The working tree was clean before and after the review, and no repository file was changed.
Probes are in `/Users/neelu/.cache/dps-build/reports/probe-m4-G4/`.

## Verdict: FAIL (2 blocking findings)

## 1. Gates (run by the reviewer)

| Gate | Result |
|---|---|
| `./lane.sh ruff format --check dps tests tools` | 289 files already formatted |
| `./lane.sh ruff check dps tests tools` | All checks passed |
| `./lane.sh mypy dps` | Success: no issues in 119 source files |
| `./lane.sh pytest -q` (whole suite, without the 3 counter-sensitive G4 files, which ran separately to keep the demo counters serial) | **2047 passed, 82 skipped**, 0 failed (146 s) |
| G4 files with `DPS_TEST_REQUIRE_SERVICES=1 DPS_TEST_REQUIRE_TESSERACT=1` against this lane's containers (`dps-wt-m4-dps-db-1` on 5463, MinIO 9050, demo site auto-started on node) and tesseract through `probe-m4-G4/tesseract.sh` (`docker run --network none data-privacy-services:local tesseract`): test_crawler (10), test_no_submit (5), test_inventory_export (4), test_evidence_html_scan (2), test_scan_job_e2e (5), test_browser_worker (5), test_scans_api (11) | **42 passed**, 0 skipped (299 s) |
| Frontend | no frontend file changed in T14–T18, so not applicable |

## 2. Plan, contract, spec and programme-plan conformance

- T14 `runtime.py`/`crawler.py`: the plan's loop, with the G2/G3 amendments. Budgets are pages, depth, per-template, SPA clicks per page, skipped rows, events, fields per page, screenshot height and the job-time `asyncio.timeout`. Popups are never followed and dialogs are dismissed. Each mode is filled in a fresh tab armed before the fill, every tab is closed through `guard.close`, and the context is closed through `close_scan_context`.
- T15: the four layers are present. The positive control, the full crawls with hardening off and on (`/__received` `non_get`, `form_get`, `by_path` and `logout_hits` unchanged), the adversarial attempts and the static check all pass.
- T16: the handlers, `open_runtime`, `DbScanSink`, `finalize_scan`, the exports, the evidence pack with its `scan` block, the HTML section and the key purge are in place. The e2e tests do a normalised value search over the rows, exports and capture JSON, with a positive control.
- T17: the browser worker matches the plan, with readiness following Chromium. A real-process test runs a scan and drains on SIGTERM.
- T18: all routes are present. The contract §9 rows are updated: two added routes and the replay/error-code amendments.
- Programme plan §2.3 G4 acceptance: crawler budgets, the T15 proof against `/__received`, scan jobs and evidence, the browser-worker process and the scans API are all present and green, **except** the two blockers below. These break "jobs/evidence" (idempotent finalize) and the spec §8 deny-list safety rail.

### Deviation-log judgement (M4-DEVIATIONS.md, T14–T18)

| Entry | Judgement |
|---|---|
| T14 single `_click` call site and static check adapted | accept (stricter than the plan) |
| T14 `sensitive_prefilled`, `capture_screen(detector=)`, filled screens re-extracted with a sweep, no capture of a tab that navigated | accept (the plan's version would mask or leak the wrong things; the OCR test and my OCR probe back it) |
| T14 other-host targets via `stored_host`; links resolved against the page URL; form method cut; replay failure → `replay`; timeout marks in-flight `failed/timeout`; `guard.close` before `release` | accept |
| T14 `RunOutcome.fills_skipped`, `repr`-safe runtime/targets | accept (additive) |
| T15 obfuscate-mode crawls, extra counters, extra banned calls, `[rbf]?"""` docstring stripping | accept |
| T16 tenant-scoped storage state `DPS_SECRET_DIR/<tenant>/<ref>` | accept as interim; SEC-5 (main's `resolve_secret_ref(tenant_id, ref, settings)`, containment within the tenant folder) stays with WP-I as the programme plan says |
| T16 `open_runtime` extra refusals, dropped consistency flags, `max_pages` re-capped | accept |
| T16 `DbScanSink.progress` writes only three keys; audit `counts.page_no` | accept |
| T16 M2 touch point `jobs.cancel` (a started scan gets a cancel-finalize, a queued one is canceled at once) + contract §3.2 amendment | accept; M2's queue tests are green in the full run. However, it opens the race in B1 because the scan's finalize runs inside phase `scan` |
| T16 `ScanStore.all_pages` (the plan's `limit=100_000` was silently capped at 1000) | accept (real fix) |
| T16 export tokens per page and persona; neutral fields carry the display persona's value; `obfuscate: null` on leakage | accept |
| T16 evidence built from the stored redacted spec, `fills_skipped`, `config_hash` over sha(spec) | accept |
| T17 readiness follows Chromium, `serve_ops(host)`, port 0 disables, header-line cap, `init_pool`, `isinstance` audit start, canonical-module `__main__` | accept |
| T18 replay 200 + `Idempotent-Replay` (M2 convention), `idempotency_key_reused`, `key_material_invalid`/`wrap_key_invalid`/`db_unavailable` instead of plan codes | accept; the contract is amended, and the m4ui console (`frontend/src/lib/api.ts`, `types.ts`) does not depend on the old codes. The `ScanCreated`/`ScanSummary` shapes match the console types field for field |
| T18 `no-store` on create and downloads, cursor validation, OpenAPI test `{get, post}` on `/scans`, the import test split (Pillow is in the app image) | accept |

## 3. Blocking findings

### B1. `finalize_scan` is neither fenced nor idempotent: a cancel or crash during finalize leaves two evidence packs with contradictory outcomes, and a crash leaves an evidence row pointing at a deleted artefact

`dps/layers/scanner/__init__.py::finalize_scan` writes, in this order: the exports, the evidence HTML artefact, the `dps_evidence` row, the fenced `advance_job(..., purge_secret=True)` (its `False` return is ignored), then the `scan.finalize` audit event. The scan's finalize runs while the job is still `phase=scan, state=running`, so `jobs.cancel` accepts a cancel during it. Unlike M2's run finalize, which reuses `_existing_evidence`, it never checks for an existing pack.

Probe `probe-m4-G4/test_probe_finalize.py::test_lease_lost_before_finalize_double_evidence` (log `probe_finalize_a.log`) does three things. It claims a scan, cancels it while the claimed worker is about to finalize, runs `finalize_scan(... result="succeeded")` with the old claim, and then runs the queued cancel finalize. Result:
- after the fenced-out finalize, the job is `queued/finalize` and the key is **not** purged, yet an evidence row with `outcome: succeeded` already exists and the audit holds `scan.finalize outcome=succeeded`;
- final state: the job is `canceled`, with `progress.evidence_id` = the canceled pack, and **2 evidence rows** (`succeeded`, `canceled`): immutable packs that contradict each other. There are 2× `export_json`, 2× `export_csv` and 2× `evidence_html`, so the export the API streams is chosen arbitrarily.

Probe `test_crash_after_evidence_before_advance` (log `probe_finalize.log`) simulates a failure of the final `advance_job` after the evidence insert, followed by what a reaped retry's `scan_job` does (`reset_scan` + `delete_for_job`). The evidence row survives with `html_artefact_id` pointing at a deleted artefact (`art: None`), and the retry will then add a second pack.

This contradicts contract §3.1 (one finalize per job; finalize is the terminal step) and M2's idempotent-finalize behaviour, and the audit row records an outcome the job never had. It is a "write that double-applies after a crash or race" as the brief defines one.

**Fix.**
- Before finalizing, move the job into the finalize phase through the fence (for example, `advance_job(phase=JobPhase.finalize, state=JobState.running)`). Stop if that returns `False`. Once in that phase, `jobs.cancel` answers `job_finalizing`.
- Make `finalize_scan` idempotent: reuse a pack an earlier attempt of this job already stored (as M2's `_existing_evidence` does), and reuse or replace exports rather than duplicating them.
- Check the return value of the terminal `advance_job`, and write the `scan.finalize` audit only after it succeeds.
- `scan_job`'s retry clean-up must not delete artefacts referenced by an evidence row.
- Add tests for: cancel during finalize → exactly one pack (`canceled`) and one export pair; crash after insert → the retry yields one pack whose HTML artefact exists.

### B2. The scan's deny and allow lists are not enforced on navigations the scanner itself causes (SPA discovery clicks, redirect hops); the records then claim those paths were skipped

`ScanGuard` and the scope proxy check only method and host. The deny/allow verdict is applied only in `ScanRunner._consider`, after the navigation has already happened. The SPA discovery click (`_discover_clicks` → `_click`) clicks `a[href^="#"]`, `a[href^="javascript:"]` and `[role=link]:not([href])` elements. These are exactly what a site's "Sign out" control often is (`<a href="#" onclick="location='/logout'">`). A redirect hop to a denied path is followed the same way.

Probe `probe-m4-G4/test_probe_deny_click.py` (log `probe_deny_click.log`) uses a local counting server (127.0.0.1, registered) and `deny=("/logout", "/admin")`. The page has `<a href="#" onclick="location.href='/logout'">`, `<a href="javascript:location.assign('/admin/purge')">` and a `role=link` element to `/settings`. Result:
- the server received `GET /logout` = **1** and `GET /admin/purge` = **1**;
- the scan's page rows report both as `skipped / deny`, so the evidence says the deny list held while the denied endpoints were hit.

On a real authenticated scan this logs the session out mid-scan, and it reaches any state-changing GET endpoint the operator explicitly denied. It contradicts spec §8's safety rails ("scope allow/deny lists"), the plan's DoD wording ("the deny list held"), and the meaning of the value-free records. The demo site does not exercise it: none of its clickables navigates to `/logout`.

**Fix.**
- Enforce the scope's path verdict in the guard for document requests (main frame and sub-frames, every hop, including the proxy path for redirects). A navigation whose `CrawlScope.navigation_verdict` is `deny`/`not_allowed` is aborted and counted as blocked with reason `deny`/`not_allowed`.
- Alternatively or additionally, never click a clickable whose resolved target is denied. That alone does not cover onclick handlers, so the guard rule is the necessary part.
- Add a counting-server test with an `href="#"` + onclick logout link, a `javascript:` link to a denied path and a 302 to a denied path: 0 hits, counted as blocked.

## 4. Other probes (no defect found)

- **Cross-tenant:** every scan route (`GET /scans/{id}`, `/pages`, `/pages/1`, `/events`, `/inventory` JSON and CSV, `/export`, `POST /cancel`) called with tenant t2 for t1's scan → 404 `scan_not_found`. t2's list is empty, and t1's scan is still `queued` after t2's cancel attempt (`probe_api.log`).
- **SQL from input:** `/api/v1/scans/x' OR 1=1--` → 404; cursors other than digits → 400 `cursor_invalid`; a bad `format`/`state` → 422; limits are clamped.
- **`submission_detected`:** a seeded allowed POST row makes `finalize_scan` fail the job with `submission_detected`, record `non_get_allowed: 1` in the pack and purge the key (`probe_allowed_post.log`). The path works, but no repository test covers it (N2).
- **The guard counts allowed decisions too** (`guard.py` `_counts` for both verdicts), so `non_get_allowed == 0` is a measurement, not a tautology.
- **Full-crawl OCR:** 69 PNGs per mode, with no detector entity readable anywhere (see §6).

## 5. Non-blocking findings and nits

- N1. `GET /scans/{id}/pages/99999999999` → 500 `internal_error` (asyncpg int4 overflow; value-free). Bound `page_no` with `Path(ge=1, le=2**31-1)`.
- N2. `finalize_scan`'s `submission_detected` path has no repository test; my probe shows it works. Add the probe as a test.
- N3. `DbScanSink.save_batch` and `save_artefact` are not lease-fenced. A worker that stalls past its lease and is reaped can keep writing rows while the retry has already run `reset_scan` (only `progress()` is fenced, once per page). Consider fencing the batch transaction on `locked_by`.
- N4. The redirect check compares only the path. Under `same_origin=true`, a redirect to another *registered* host with the same path is processed as the target page. The host is registered, so nothing leaks, but the scope is wider than the spec says.
- N5. A job-time timeout that lands inside `_finish`'s `save_batch` loses that batch's skip rows and events. Queued and current rows are re-marked, so the loss is records, not data.
- N6. `scan_finalize_job` treats every `ScanSetupError` as "key purged". With `host_not_registered` (host removed after accept), the key still exists but no exports are written. Minor.
- N7. The T15 static check could also ban `.set_checked(`, `.type(`, `.press_sequentially(` and `.hover(`. None is used today.
- N9. Application reference rendered unmasked in the `/apply/review` captures (see §6); out of the detector contract.
- N8. SEC-5 is unchanged here (interim `f"{tenant}/{ref}"` with containment against the whole secret dir); it is owned by WP-I.

## 6. Full-crawl OCR probe

Probe `probe-m4-G4/test_probe_ocr_all.py` (log `probe_ocr_all.log`) ran a full demo crawl in each mode (obfuscate and synthesize). Each crawl stored 69 PNGs (original, annotated, both filled screens, masked capture), and every one was OCRed with docker tesseract. Each PNG was checked for all 11 demo values: strict pieces for original/annotated, the token-aware pieces for filled/masked.
- **No readable value for any detector-classified entity**: name, DOB, Aadhaar, PAN, card number, e-mail, mobile, address, account number and customer id all came back clean on every page and variant.
- One hit in both modes: the demo's **application reference** (shape `AAA-9999-9999`) is readable on `/apply/review` in `original`, `annotated` **and** `masked_capture`. It is not a taxonomy entity, so no detector finds it, and G3's own OCR test deliberately leaves it out of `/apply/review`'s keys. The value is therefore outside the masking contract, not a G4 defect. But the scan's URL redaction treats the same value as a value (`ref=<v>`), and the repository's own row-leak checks list `application_ref` as data that must not be stored. Recorded as **N9** for the lane verification and G3 owners: consider a policy recognizer for application or reference ids, or mask on the URL-value classifier's verdict too.

## 7. Clean-up

`docker compose down` for this lane's project was run after the probes. No other project's containers were touched.
