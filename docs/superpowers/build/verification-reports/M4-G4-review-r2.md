# M4 G4 review, round 2 (Tasks 14–18 after fix r1 `6eac062`)

Reviewer: independent; did not rely on M4-G4-impl.md or M4-G4-fix-r1.md claims without re-running them.
Worktree `/Users/neelu/document_intelligence/dps-wt-m4`, branch `m4`, HEAD `6eac062`. Working tree was clean
before and after; no repository file was modified. Probes: `/Users/neelu/.cache/dps-build/reports/probe-m4-G4/r2/`.

## Verdict: FAIL (1 blocking finding)

B1 and B2 of round 1 are fixed as far as their stated scenarios go (verified by re-running the round-1
probes and the new tests). One residual of B2 is a real deny-list breach with false records: SPA discovery
clicks hash-route links (`<a href="#/logout">`) that the scope's own deny semantics name, and the page's
logout route handler runs (R2-B1 below).

## 1. Gates (run by the reviewer)

| Gate | Result |
|---|---|
| `./lane.sh ruff format --check dps tests tools` | 291 files already formatted |
| `./lane.sh ruff check dps tests tools` | All checks passed |
| `./lane.sh mypy dps` | Success: no issues found in 119 source files |
| `./lane.sh pytest -q` (lane `dps-db` up on 5463; `DPS_TEST_TESSERACT_CMD` = docker `--network none` tesseract wrapper) | **2103 passed, 71 skipped, 0 failed** (471 s). Skips are the other profiles (source-pg, MinIO, app container, azure-ocr-mock, presidio, [ner]) not part of this lane's scanner stack. Log: `r2/pytest_full.log` |
| G4 integration with `DPS_TEST_REQUIRE_SERVICES=1 DPS_TEST_REQUIRE_TESSERACT=1`: test_crawler, test_no_submit, test_inventory_export, test_evidence_html_scan, test_scan_job_e2e, test_browser_worker, test_scans_api, test_scan_finalize, test_scope_rail, test_store | **78 passed, 0 skipped** (316 s). Log: `r2/pytest_g4.log` |
| Frontend | no frontend file changed in T14–T18 or fix r1: not applicable |

The `test_guard.py::...unload_request_out[False]` flake reported by fix r1 did not reproduce in my full run.

## 2. Round-1 findings re-checked

### B1 (finalize fenced / idempotent): fixed
- Code: `finalize_scan` moves `scan → finalize/running` through `advance_job` before any write (returns
  `None` when fenced out); `jobs.cancel` refuses phase `finalize` (`job_finalizing`, unchanged M2 rule);
  the pack is inserted under `lock_leased_job` (`FOR UPDATE … locked_by=$me AND state='running'`) with a
  re-check; an existing pack is reused (its outcome wins) and only unreferenced `export_*`/`evidence_html`
  artefacts are removed; terminal `advance_job` return value checked; audit only after it applied.
  `existing_evidence` reads `pack->'target'->'exports'`, which `build_scan_evidence` does write
  (`target.exports`), and the pool has a jsonb codec, so `keep=` really covers the exports (the tests'
  `{"export_json":1,"export_csv":1,"evidence_html":1}` after reuse confirm it indirectly).
- Interleavings I walked through (stale owner in `finalize` phase vs. new owner writing; stale owner in the
  stored-pack branch; crash between insert and terminal advance; crash before insert): each ends with one
  pack, its HTML present, one export pair. A job never returns from phase `finalize` to `scan`, so the
  scan-phase `reset_scan`/`delete_for_job` cannot run once a fenced pack exists.
- `dps_job.plan` overwrite (`COALESCE($9, plan)`) is safe: scans never set `plan` before finalize.
- Re-run of round-1 probe `test_probe_finalize.py` (copied to `r2/test_probe_finalize_r1.py`, log
  `r2/probe_r2_run.log`): `test_lease_lost_before_finalize_double_evidence` **passes**;
  `test_allowed_post_fails_the_scan` passes; `test_crash_after_evidence_before_advance` fails only because
  the probe itself calls `reset_scan`+`delete_for_job` after the crash, a sequence the code no longer runs
  (judgement: accept fix r1's explanation; `test_scan_finalize.py` tests 3 and 5 drive the real retry paths).

### B2 (deny/allow on scanner-caused navigations): fixed for the reported cases, residuals below
- `decide(path=)`: `deny` refuses every request kind, `not_allowed` navigations; route uses
  `is_navigation_request()`, the scope proxy uses `is_navigation_hop(headers)` on every plain-HTTP hop.
- Round-1 probe `test_probe_deny_click.py` re-run: server hits `{'/': 5, '/settings': 2}` — no `/logout`,
  no `/admin/purge` (`r2/probe_r1_rerun.log`).
- New `test_scope_rail.py` browser tests pass in my G4 run (onclick logout, `javascript:` link, 302 to
  denied path, denied iframe; allow-list variant).

## 3. Blocking finding

### R2-B1. SPA discovery clicks hash-route links the deny list names; the page's logout route runs while the records say "skipped / deny"

`url_path_verdict` documents and implements that a hash route is a navigation checked against the deny
list (`/app#/logout` → `deny`), and the crawler records such a link as `skipped / deny`. But
`ScanRunner._discover_clicks` clicks every `a[href^="#"]` clickable (dom.py `CLICKABLE`) without checking
its target first. The fragment never reaches the network, so the new guard rail cannot see it; the
SPA's own route handler runs.

Probe `r2/test_probe_hash_route.py` (log `r2/probe_hash_route.log`): counting server on 127.0.0.1,
`deny=("/logout",)`, page with `<a href="#/logout">Sign out</a>` and `<a href="#/profile">`, and a
`hashchange` router where `#/logout` does what an SPA logout does (`fetch('/api/session/end')`,
`localStorage.clear()`).
- Server hits: `{'/': 6, '/api/session/end': 2, '/api/profile': 4}` — the denied logout route
  executed twice (control `/api/profile` shows the clicks ran).
- Page rows: `(2, '/', 'skipped', 'deny', 'link')` — the record says the denied route was skipped.

On a real authenticated scan of a hash-routed SPA (common in older Angular banking front-ends), this logs the
scanner out (client-side token removal and/or a GET logout API that is not itself on the deny list) and the
evidence says the deny list held. Same defect class as round-1 B2 (spec §8 "scope allow/deny lists"; plan
DoD "the deny list held"); fix r1 listed it as open issue 4 and declined the reviewer's suggested
pre-click check, reasoning that the elements "have no resolvable target", which is wrong for `#/…` links.

**Fix (narrow).** Before each discovery click (and the replay click), read the clickable's resolved target
(`el.href` for anchors; it is available in the frame) and skip the click when
`self.rt.scope.navigation_verdict(resolved)` is not `ok` (this covers hash-route deny via
`url_path_verdict`), recording a value-free skip event (`spa_click` / `deny`). Add the probe page as a test
(`#/logout` link + hashchange handler fetching a non-denied API path): 0 hits on that API path, controls
hit. `javascript:` links that set `location.hash` remain a documented residual.

## 4. Non-blocking findings and nits

- N-r2-1. **HTTPS redirect hops to denied paths are still sent** (fix r1 open issue 1). Documented in the
  guard docstring and counted (`scope_paths_reached`, evidence `scan` block, HTML), so the records are
  truthful. However: (a) real bank targets are HTTPS-only, so this is the common case, not an edge case;
  (b) the listener is tested only with a fake `Request` (no real-browser HTTPS test); (c) a scan with
  `scope_paths_reached > 0` still ends `succeeded` with no risk flag. Recommend at least failing or
  flagging the scan when `scope_paths_reached > 0`, and a lane-verification check on real targets; the
  full fix is `route.fetch(max_redirects=0)` + `fulfill` for navigations. The programme plan should carry
  this as an explicit accepted residual.
- N-r2-2. Evidence HTML label "Requests to denied paths that left the browser" also counts
  `not_allowed` hops; wording nit.
- N-r2-3. `allowed` rows with reason `deny`/`not_allowed` now exist in `dps_scan_request`; any consumer
  that equates `allowed` with `ok` (none found in the API or console) should be aware.
- Round-1 N1 still present: `GET /scans/{id}/pages/{page_no}` has `page_no: int` unbounded → int4 overflow
  500 (value-free). N3 (unfenced `save_batch`/`save_artefact`: a reaped or canceled crawler can write one
  more page's rows/screenshots after the cancel-finalize built the pack), N4–N9 unchanged and still non-blocking.

## 5. Deviation-log judgement (new section "G4 review round 1 — fix")

| Entry | Judgement |
|---|---|
| `finalize_scan → str \| None`, fence into `finalize/running`, `plan.scan_finalize` | accept (contract §3.1 amended; not a cross-lane name) |
| Idempotent reuse of earliest pack; `delete_for_job(kinds=, ids=, keep=)` additive filters | accept (default behaviour unchanged; retention unaffected) |
| Audit only after terminal transition; terminal progress clears `stage` | accept |
| `scan_finalize_job` serving cancel + interrupted-finalize retry | accept |
| `scan_job` pack check before retry clean-up | accept |
| Round-1 crash probe "fails by construction" | accept (verified) |
| Deny refuses every request; not_allowed refuses navigations; proxy hop rule | accept (stricter) |
| HTTPS redirect residual counted as `scope_paths_reached` | accept as documented residual, see N-r2-1 |
| "skip clickables whose resolved target is denied" not done | **reject**: `#/…` links do have a resolvable target, and R2-B1 shows the consequence |
| Allow list applying to other registered hosts' navigations (fail closed) | accept |
| Contract §7 `scope_paths_reached` key | accept (additive) |

## 6. Privacy checks
- New log lines (`scan_finalize_fenced`, `scan_evidence_exists`) carry only `job_id`; new counters carry
  reason/method/resource type/value-free host. The full suite's no-values-in-logs tests pass.
- Probes use synthetic pages only; nothing printed but paths and counts.

## 7. Clean-up
`docker compose down` for this lane's project after the runs; no other project touched; tree clean.
