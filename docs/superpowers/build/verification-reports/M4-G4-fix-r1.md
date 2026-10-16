# M4 G4 fix, round 1 (review M4-G4-review-r1: B1, B2; N2 folded in)

Worktree `/Users/neelu/document_intelligence/dps-wt-m4`, branch `m4`. Start HEAD `2a4fa35` (clean tree,
no earlier attempt to resume). Commit: **`6eac062`** `fix(m4): fenced, idempotent scan finalize; deny and
allow lists enforced by the request guard on every hop` (author NilenduGanguli <nilendu.ganguli@gmail.com>,
no trailer, explicit pathspec). Lane stack brought down afterwards (`docker compose down`, this project only).

## B1: scan finalize fenced and idempotent

**Tests first** (`tests/scanner/test_scan_finalize.py`, new; DB-only, no navigation). Before the fix
**5 failed, 1 passed** (the N2 test passes on the old code, since it covers an existing path):
1. `test_cancel_before_the_finalize_fence_writes_nothing`: crawl done, cancel, then the worker
   finalizes with its old claim. Expected: returns `None`, 0 packs, 0 artefacts, no `scan.finalize`
   audit, key kept. The cancel-finalize then writes exactly 1 pack (`canceled`) whose HTML exists,
   1 `export_json`, 1 `export_csv`, 1 `evidence_html`, audit `["canceled"]`, and purges the key.
2. `test_a_cancel_during_finalize_is_refused`: a cancel issued from inside the first artefact save
   gets `job_finalizing`. The job ends `succeeded` with 1 pack and 1 export pair.
3. `test_a_crash_after_the_evidence_insert_is_retried_into_the_same_pack`: the terminal
   `advance_job` raises after the insert, so no audit row is written. The lease expires,
   `jobs.reap` requeues the job in phase `finalize`, it is claimed and `scan_finalize_job` runs. Result:
   the same pack id, its HTML artefact exists, 1 export pair, job `succeeded`, audit `["succeeded"]`, key purged.
4. `test_a_crash_before_the_evidence_insert_is_retried_with_the_crawl_outcome`: the insert fails
   after the exports and HTML are written. The retry produces 1 pack with
   `scan.fills_skipped == 2` (the crawl's `RunOutcome` survived on `dps_job.plan`) and 1 export pair
   (the crashed attempt's artefacts are removed).
5. `test_a_scan_retry_never_deletes_what_a_pack_references`: a scan-phase attempt of a job that
   already has a pack finalizes with that pack and keeps its artefacts.
6. `test_an_allowed_non_get_fails_the_scan_with_submission_detected` (review N2).

**Fix** (`dps/layers/scanner/__init__.py`, `store.py`, `dps/storage.py`):
- `finalize_scan(...) -> str | None`. A job still in phase `scan` first calls
  `advance_job(phase=finalize, state=running, progress.stage="finalize",
  plan={"scan_finalize": {result, outcome: RunOutcome}})`. If that returns `False`, it returns `None`
  before any write. M2's `jobs.cancel` already refuses phase `finalize` with `job_finalizing`, so
  `jobs.py` is unchanged.
- Idempotent: `ScanStore.existing_evidence` returns the earliest pack (id, outcome, HTML id,
  export ids). That pack is reused and its outcome becomes the terminal state. Artefacts of the export
  and HTML kinds that the pack does not reference are deleted.
- New pack: exports and HTML are written, then the pack is inserted in one transaction under
  `ScanStore.lock_leased_job` (`SELECT … FOR UPDATE WHERE locked_by=<me> AND state='running'`), with a
  re-check for an existing pack under the lock. Only the attempt that inserted the pack deletes the
  leftovers of earlier attempts (`ArtefactService.delete_for_job(kinds=, ids=, keep=)`, new keyword-only
  filters; the default behaviour is unchanged, so retention is unaffected). A fenced-out or beaten attempt deletes only the ids it wrote.
- The return value of the terminal `advance_job` is checked. The `scan.finalize` audit row is written only after it
  applies. Terminal progress clears `stage`.
- `scan_finalize_job` handles three cases: cancel (`progress.outcome == canceled`), a retry of an interrupted finalize
  (the result and `RunOutcome` come from `plan.scan_finalize`), and otherwise `failed` as before.
- `scan_job` checks for a pack before its retry clean-up. If one exists, it finalizes with it instead of crawling
  again, so `reset_scan`/`delete_for_job` never runs against artefacts that a pack references.

**Reviewer probe** `probe-m4-G4/test_probe_finalize.py` (copied temporarily into tests/ to run it):
- `test_lease_lost_before_finalize_double_evidence` **passes**. After the fenced-out finalize, the job
  is `queued/finalize` with no packs and no audit. The final state is `canceled` with 1 pack, and each
  of export_json, export_csv and evidence_html exists once.
- `test_allowed_post_fails_the_scan` passes.
- `test_crash_after_evidence_before_advance` still fails. The cause is the probe's design: after the
  crash it calls `reset_scan` + `delete_for_job` directly, and the code no longer runs that sequence.
  A finalize retry is now a finalize, and a scan-phase retry checks for a pack first. Tests 3 and 5
  above exercise the real retry paths.

## B2: deny and allow lists enforced in the request guard

**Tests first** (`tests/scanner/test_scope_rail.py`, new). Before the fix both browser tests failed:
- Deny run: the counting server received `/logout`=2, `/admin/purge`=1 and `/admin/frame`=5.
- Allow run: it received `/elsewhere`=1 and `/outside`=1.

After the fix, all **18 tests pass**:
- Browser, deny run. A counting server on 127.0.0.1 serves an `href="#"` + `onclick` logout, a
  `javascript:location.assign('/admin/purge')` link, a link to `/go` that answers 302 → `/logout`, an
  `<iframe src="/admin/frame">` and a `role=link` control. Hits on the denied paths are **0, 0 and 0**.
  The controls `/settings` and `/go` were hit (≥1). Blocked rows: reason `deny` ≥3 for `document` (the
  two clicks and the frame) and ≥1 `unrouted` (the redirect hop, refused at the scope proxy). 0
  allowed rows have reason `deny`/`not_allowed`. No page row for a denied path is anything but
  `skipped/deny`. At least 4 `request`/`deny` events are in the navigation log.
- Browser, allow run (`allow=("/home",)`). An onclick navigation to `/elsewhere` and a 302 from
  `/home/go` to `/outside` both get **0 hits**, blocked as `not_allowed` (document ≥1, unrouted ≥1). The image
  `/static/logo.png` (a sub-resource outside the allow list) is still served (≥1). The controls were hit.
- Pure tests:
  - `request_path_verdict` (case folding and percent-encoding included).
  - `decide(path=)`: deny refuses every resource type, navigating or not; not_allowed refuses navigations only; the method rule still wins.
  - `is_navigation_hop` header cases.
  - The proxy's `_check_hop` refuses and counts deny/not_allowed as `unrouted`.
  - The HTTPS-hop listener counts as reached only a redirected HTTPS hop to an allowed host on a
    denied path (or not-allowed path, navigating). First hops, plain HTTP and other hosts are not counted.
- `tests/scanner/test_store.py::test_summary_measures_requests_that_reached_a_scope_path`.

**Fix** (`guard.py`, `urls.py`, `crawler.py`):
- `CrawlScope.request_path_verdict(url)` (= `url_path_verdict(url, allow, deny)`), passed by the crawler as
  `ScanGuard(path_verdict=…)`.
- `decide(..., path=)` runs after scheme, method and host:
  - `deny` refuses **every** request. This is stricter than the review's "document requests": a script `fetch('/logout')` or an image under
    `/admin` is refused too.
  - `not_allowed` refuses navigations (main frame and sub-frames).
- The route applies it with `request.is_navigation_request()`. The scope proxy applies it to every
  plain-HTTP hop, redirects included. `is_navigation_hop(headers)` identifies a navigation by any of:
  `Sec-Fetch-Mode: navigate`, a document-like `Sec-Fetch-Dest`, an `Upgrade-Insecure-Requests` header,
  or an `Accept` that asks for HTML. It errs towards treating a hop as a navigation.
- **Residual, documented:** an HTTPS redirect hop to a denied path on an allowed host cannot be refused.
  The route sees only first hops, and the proxy sees only the tunnel. Such a hop is no longer
  hidden: a `context.on("request")` listener counts it as an *allowed* row with reason `deny`/`not_allowed`.
  `summary_counts` gains `scope_paths_reached`, the evidence pack's `scan` block carries it, and the
  evidence HTML prints it. Refusing such hops would require `route.fetch(max_redirects=0)` + `fulfill`
  for every navigation. That would change cookie and streaming semantics, so it is not done here (see open issues).
- The optional "skip clickables whose resolved target is denied" was not done. The elements in question
  (`href="#"` + onclick, `javascript:`) have no resolvable target, and the guard rule covers them.

**Reviewer probe** `probe-m4-G4/test_probe_deny_click.py` (copied temporarily): **passes**. Server hits are
`{'/': 5, '/settings': 2}`, with no `/logout` and no `/admin/purge`.

## Contract and deviations

- Contract §3.1: amendment for the fenced and idempotent scan finalize. Added a comment on
  `dps_job.plan` (scans: `scan_finalize`). Added the §7 `scan` key `scope_paths_reached`. No existing
  name changed. The only signature change is `finalize_scan → str | None`, which is not in the contract.
- `docs/superpowers/build/M4-DEVIATIONS.md`: new section "G4 review round 1 — fix", covering every item above.
  It includes the stricter deny-for-all-requests rule and the allow list applying to navigations on other
  registered hosts (fail closed).
- M2 touch point: `ArtefactService.delete_for_job` gained additive keyword-only filters.
- M2 touch point: the evidence HTML scan paragraph gained one counter.

## Gates (final code)

| Gate | Result |
|---|---|
| `./lane.sh ruff format --check dps tests tools` | 291 files already formatted |
| `./lane.sh ruff check dps tests tools` | All checks passed |
| `./lane.sh mypy dps` | Success: no issues found in 119 source files |
| `./lane.sh pytest -q` (lane dps-db up; tesseract through a `docker run --network none data-privacy-services:local` wrapper) | **2103 passed, 71 skipped**, 0 failed (467 s). Skips: source-pg 51+, minio 2, app container 3, azure-ocr-mock 3, tesseract-on-PATH-only 2, presidio 1, [ner] 1. These are not part of this lane's scanner stack and are unchanged from G4 impl |
| G4 integration with `DPS_TEST_REQUIRE_SERVICES=1 DPS_TEST_REQUIRE_TESSERACT=1` (lane dps-db, demo site auto-started): test_crawler, test_no_submit, test_inventory_export, test_evidence_html_scan, test_scan_job_e2e, test_browser_worker, test_scans_api, test_scan_finalize, test_scope_rail, test_store | **78 passed, 0 skipped** (313 s) |
| Frontend | not touched, so not applicable |

An earlier full run (before the last two small edits) had **1 failure**, in a test that was not changed:
`tests/scanner/test_guard.py::test_closing_a_page_through_the_guard_lets_no_unload_request_out[False]`.
The server counted `POST /ka-visibility…` / `beacon-unload`, which are exactly the requests that the
test's own positive control (an unguarded `page.close()`) sends. That guard has no `path_verdict`, so
the new code is inert there. It then passed in 4 single reruns, in 3 reruns of the whole file (56/56 each),
and in the final full run. My reading is that it is a timing flake: under full-suite load, the control's
late unload POSTs land after its 0.8 s drain. Recorded below.

Privacy:
- The only new log lines are `scan_finalize_fenced` and `scan_evidence_exists`, and their only field is `job_id`.
- No value, key or URL is logged or put in an exception.
- New counters hold a reason, method, resource type and a value-free host (`stored_host`). URLs stay in
  memory, as before.
- The tests print nothing.

## Open issues (non-blocking)

1. HTTPS redirect hops to a denied path cannot be refused, only counted (`scope_paths_reached`). A full
   fix would need manual redirect handling for navigations. Recommend a lane-verification check that
   `scope_paths_reached == 0` on real targets.
2. Possible flake: `test_guard.py::test_closing_a_page_through_the_guard_lets_no_unload_request_out[False]`.
   The control's drain could wait until the control's POSTs are counted, rather than sleeping 0.8 s.
3. Review N1, N3–N9 not addressed in this round, as they were outside the brief. N2 is now covered by a test.
4. Hash-route (`#/logout`) SPA navigations make no network request, so the guard cannot see them. They are
   still skipped by the crawler's `navigation_verdict`, but a click on them executes the page's own JS route
   handler (which might call an API on a non-denied path).
