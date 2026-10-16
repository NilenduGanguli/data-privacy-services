# M4 G4 fix, round 2 (review M4-G4-review-r2, finding R2-B1)

Worktree `/Users/neelu/document_intelligence/dps-wt-m4`, branch `m4`. Started from a clean tree at `6eac062`.
One commit: `0cf0140 fix(m4): SPA discovery and replay never click a clickable whose resolved target the scope refuses (hash routes checked against the deny list)`.
Author NilenduGanguli <nilendu.ganguli@gmail.com>, no trailer, explicit pathspec. After the runs I took the lane stack down with `docker compose down` (this project only). The tree is clean.

## R2-B1: hash-route links the deny list names were still clicked

**The bug.** `ScanRunner._discover_clicks` and the replay click (`_navigate`, which goes through `_click`) clicked every stamped `a[href^="#"]`, `a[href^="javascript:"]` and `[role=link]` clickable without checking its target. A hash route never reaches the network, so the request guard could not refuse `#/logout`, and the page's own route handler ran.

**The fix** (`dps/layers/scanner/crawler.py`, `dps/layers/scanner/dom.py`):
- `dom.CLICK_TARGET_JS` (new) gives the element's resolved target:
  - it reads `href` or `xlink:href` and resolves it against the element's `baseURI`;
  - it returns `null` when there is no href and `''` when the href cannot be resolved.
- `_click(frame, frame_index, ordinal)` now returns `(verdict, target)`. It used to return `None`; the method is private.
  - It reads the target through the stamped `[data-dps-click=…]` locator and clicks only when `_click_verdict(target) == "ok"`.
  - `_click_verdict` calls `CrawlScope.navigation_verdict(target)`. That checks hash routes against the deny list through `url_path_verdict`. A `<base href>` that points at another origin gives `other_origin`. An unresolvable target gives `scheme`, so it fails closed.
  - `null` (role=link) and `javascript:` targets have nothing to check. Their network navigations still go through the guard.
- Discovery: a refused clickable is not clicked.
  - Every refusal writes a `spa`/`skipped` event with a value-free target (redacted URL or origin) and `note = verdict`.
  - Each refused target also gets one `skipped` page row with the verdict as reason, through `_skip`. This is the same row a refused link gets.
- Replay: a refused target raises `_NavFailed(verdict, skipped=True)`. `_NavFailed` gains a keyword-only flag. The queued SPA page is recorded as `skipped` with the verdict as reason, with event `navigate`/`skipped` and audit outcome `skipped`, and it is not clicked.
- The static no-submit check still holds unchanged: there is one `.click(` call site, on the stamped `data-dps-click` locator, reached from exactly two places. My first draft broke this check because it moved the locator into a variable. The full run caught it, and I restructured the code without changing the test.

**Tests** (`tests/scanner/test_scope_rail.py`):
- `test_spa_discovery_never_clicks_a_hash_route_the_deny_list_names` uses the reviewer's probe page plus a `#!/admin/purge?all=1` route.
  - It asserts 0 hits on `/api/session/end` and `/api/cache/purge`.
  - Control: `/api/profile` gets at least 1 hit.
  - There are 2 or more `skip`/`deny` events and 2 or more `spa`/`skipped`/`deny` events. None of their targets carries a `#` or `?`.
  - The visited `spa_click` pages are not marked skipped, and `skipped`/`deny` rows exist.
  - At `6eac062` it failed with `/api/session/end` = 2 and `/api/cache/purge` = 2.
- `test_the_replay_click_never_clicks_a_hash_route_the_deny_list_names`: the same ordinal points to `#/profile` at discovery and, through a localStorage flag, to `#/logout` at replay.
  - It asserts 0 hits on `/api/session/end`, that the SPA page row is `[("skipped","deny")]`, and that the `navigate`/`skipped` notes are `["deny"]`.
  - With the replay check removed, the test fails.
- I re-ran the reviewer's probe `r2/test_probe_hash_route.py` against the fix by copying it into a temporary test dir, which I deleted afterwards. It passes.
  - Server hits: `{'/': 6, '/api/profile': 4}`, with no `/api/session/end`.
  - Page rows: `(2,'/','skipped','deny','link')` and `(3,'/','done',None,'spa_click')`.

## Gates

| Gate | Result |
|---|---|
| `./lane.sh ruff format dps tests tools` | 291 files left unchanged |
| `./lane.sh ruff check dps tests tools` | All checks passed |
| `./lane.sh mypy dps` | Success: no issues found in 119 source files |
| `./lane.sh pytest -q` (lane `dps-db` up on 5463; `DPS_TEST_TESSERACT_CMD` = wrapper `docker run --rm -i --network none --entrypoint tesseract data-privacy-services:local`) | **2105 passed, 71 skipped, 0 failed** (482 s) |
| G4 integration with `DPS_TEST_REQUIRE_SERVICES=1 DPS_TEST_REQUIRE_TESSERACT=1`: test_crawler, test_no_submit, test_inventory_export, test_evidence_html_scan, test_scan_job_e2e, test_browser_worker, test_scans_api, test_scan_finalize, test_scope_rail, test_store | **80 passed, 0 skipped** (326 s). That is 78 from round 2 plus the 2 new tests. |
| Frontend | not applicable (no frontend file changed) |

The 71 skips are the same as in round 2 and come from profiles outside this lane's scanner stack: source-pg, MinIO, the app container, azure-ocr-mock, presidio, `[ner]`, and one test that looks for tesseract on PATH only.

## Deviations (logged in `docs/superpowers/build/M4-DEVIATIONS.md`, section "G4 review round 2 — fix")
- This round supersedes the round-1 entry "skip clickables whose resolved target is denied — not done".
- `_click` now returns `(verdict, target)` and `_NavFailed` gains `skipped=`. Both are private, so there is no contract change.
- New event combination `spa`/`skipped`, with the verdict as note. It uses the existing kind and result vocabulary (migration 030 CHECK and the contract §2.4 kind list), so there is no schema change.
- A replay refused by the scope is recorded as page status `skipped` rather than `failed`, and its audit outcome is `skipped`.

## Privacy
- Event targets are `_redact`ed (no fragment or query values) or reduced to the origin. Notes are verdict codes. No new log line was added.
- Log scan: the G4 run log has 0 occurrences of `#/logout`, `#!/admin` or `all=1`.
- No validator or guard was weakened. The no-submit static test is unchanged.

## Open issues / residuals (documented in `_click`'s docstring)
- An element without `href` (`role="link"`), or a `javascript:` link, that only sets `location.hash` to a denied route is not stopped. The reviewer accepts this as a documented residual.
- If a page script rewrites the `href` between the pre-click check and the click (same task, milliseconds), the rewrite is not caught.
- Round-2 non-blocking items are unchanged:
  - N-r2-1: HTTPS redirect hops to denied paths are counted but still sent.
  - N-r2-2: wording of the evidence HTML label.
  - N-r2-3: rows that are `allowed` with reason `deny`/`not_allowed`.
  - Round-1 N1 (unbounded `page_no`) and N3–N9.
