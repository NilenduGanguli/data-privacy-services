# M4 G4 review, round 3 (Tasks 14–18 after fix r2 `0cf0140`)

Reviewer: independent. I did not rely on M4-G4-impl.md or the fix reports; I re-ran the gates and the probes.
Worktree `/Users/neelu/document_intelligence/dps-wt-m4`, branch `m4`, HEAD `0cf0140`. The working tree was clean
before and after the review, and I changed no repository file. Probes are in
`/Users/neelu/.cache/dps-build/reports/probe-m4-G4/r3/`.

## Verdict: FAIL (1 blocking finding, narrow)

Fix r2 closes R2-B1 as reported. The reviewer's r2 probe now shows 0 hits on the denied route's API, and
case, encoding and dot variants of a hash route are refused too. One residual of the same defect class remains:
**the scan records still claim "skipped, deny list" for a denied route that the scanner's own click ran.**
This happens when the element's href is harmless (`href="#"`) and its handler routes to the denied path,
through `location.hash` or `history.pushState`. That is the R2-B1 symptom (false evidence) in a different
element shape. It is a narrow fix: classify where a click *landed* instead of feeding it to link discovery
(R3-B1).

## 1. Gates (run by the reviewer)

| Gate | Result |
|---|---|
| `./lane.sh ruff format --check dps tests tools` | 291 files already formatted |
| `./lane.sh ruff check dps tests tools` | All checks passed |
| `./lane.sh mypy dps` | Success: no issues found in 119 source files |
| `./lane.sh pytest -q` (lane `dps-db` up; `DPS_TEST_TESSERACT_CMD` = docker `--network none` tesseract wrapper) | **2105 passed, 71 skipped, 0 failed** (480 s). Log: `r3/pytest_full.log` |
| G4 integration with `DPS_TEST_REQUIRE_SERVICES=1 DPS_TEST_REQUIRE_TESSERACT=1`: test_crawler, test_no_submit, test_inventory_export, test_evidence_html_scan, test_scan_job_e2e, test_browser_worker, test_scans_api, test_scan_finalize, test_scope_rail, test_store | **80 passed, 0 skipped** (322 s). Log: `r3/pytest_g4.log` |
| Frontend | no frontend file changed in T14–T18 or the fixes: not applicable |

- The 71 skips are the same set as in r2. They come from profiles outside this lane's scanner stack (source-pg, MinIO, the app container, azure-ocr-mock, presidio, `[ner]`).
- Log scan: `#/logout`, `#!/admin` and `all=1` occur 0 times in both run logs.

## 2. Fix r2 (R2-B1) re-checked

- Code (`crawler.py` `_click`, `_click_verdict`, `_refuse_click`, `_NavFailed(skipped=)`; `dom.CLICK_TARGET_JS`):
  - The stamped element's `href`/`xlink:href` is resolved against `baseURI`, and the element is clicked only when `navigation_verdict(target) == "ok"`.
  - An unresolvable target gives `scheme`, which fails closed. A `<base href>` that points at another origin gives `other_origin`.
  - The one `.click(` site is unchanged, and the T15 static check passes.
- Re-run of the r2 probe (`r3/test_probe_r2_hash_route_rerun.py`, log `r3/probe_clicks.log`) **passes**: hits `{'/': 6, '/api/profile': 4}`, with no `/api/session/end`.
- Variants (`r3/test_probe_r3_clicks.py::variants`): `#/LogOut`, `#/%6Cogout`, `#//logout`, `#/./logout` and `#!/logout` against deny `/logout` are all refused. The result is 0 hits on the logout API, 5 `skipped/deny` rows and `spa/skipped/deny` events, and the control route is clicked.
- New tests: the discovery test and the replay test (flip of the same ordinal) assert 0 hits on the route handlers' API paths, with positive controls, value-free skip targets, and replay recorded as `skipped/deny`. They test the claim; they are not tautologies.
- Deviation-log entries for round 2: accepted.
  - The private `_click` signature and `_NavFailed.skipped` flag are not contract names.
  - `spa`/`skipped` uses the existing kind and free-text result vocabulary, so no schema change is needed.
  - A refused replay is recorded as `skipped`, not `failed`.
  - The residual paragraph is accepted for *prevention*; see R3-B1 for the *records*.

## 3. Blocking finding

### R3-B1. A discovery click whose handler routes to a denied path runs that route, and the records say "skipped / deny"

`_discover_clicks` clicks a clickable whose resolved target is allowed. It then passes the tab's resulting URL to
`_consider(url, …, via="spa_click")`. When that URL is denied, `_consider` writes the same `skipped`/`deny`
page row and `skip` event that an *unvisited* link gets. A URL the scanner reached by its own click is
recorded as though it had been refused.

This applies to every in-document navigation, which the network guard cannot see:
- `href="#"` with an `onclick` that sets `location.hash`;
- an `onclick` that calls `history.pushState`;
- `role="link"` and `javascript:` elements doing the same.

The `_click` docstring says these navigations "meet the guard", which is true only for network navigations.

Probe `r3/test_probe_r3_clicks.py` (log `r3/probe_clicks.log`) uses a counting server and `deny=("/logout",)`.

| Case | Page | Server hits | Records |
|---|---|---|---|
| `onclick_hash` | `<a href="#" onclick="location.hash='#/logout'">`, plus a hashchange router that does what an SPA logout does: GET `/api/session/end` (not itself denied) | `/api/session/end` = **2** | page 2 `('/', 'skipped', 'deny', 'link')`, event `('skip','skipped','deny')`, `scope_paths_reached` = 0 |
| `pushstate` | `<a href="#" onclick="history.pushState({},'','/logout'); fetch('/api/session/end')">` | `/api/session/end` = **2** | page 2 `('/logout', 'skipped', 'deny', 'link')` |

The evidence pack, the scan API and the console (`skipLabel('deny')` → "deny list") therefore report that the deny
list held for a route the scanner executed twice (discovery plus the fill/replay tabs). This is the same contradiction
r2 judged blocking for R2-B1:
- spec §8, safety rails "scope allow/deny lists";
- plan DoD, "the deny list held";
- the brief's requirement that records be truthful.

`href="#"` + `onclick` is the most common pattern of all and appeared in r1-B2's own probe. It is not in fix r2's
documented residual list, which names only `role="link"` and `javascript:`.

**Fix (narrow, no schema change).** Prevention of arbitrary page JS is not required; truthful records are. In
`_discover_clicks`, after the click and settle:
1. Compute `landed = self.rt.scope.navigation_verdict(<canonical page.url>)`.
2. If `landed != "ok"`, do **not** call `_consider`/`_skip` for that URL. Write a value-free event instead, e.g.
   `spa`/`reached` with `note=landed`, and the redacted URL or origin as target.
3. Count it in a run counter, e.g. `RunOutcome.scope_routes_reached`. Surface it in the evidence `scan` block
   next to `scope_paths_reached`, and in the HTML line "Requests to denied paths that left the browser" or a sibling
   line. The pack can then never say 0 when a denied route ran. Optionally add it to the job's risk flags.
4. Do the same check after the replay click in `_navigate`. If the replay lands on a denied route, stop processing
   that tab and record it as reached, not as a visited page.
5. Correct the `_click` docstring so the residual says what it is.
6. Add a test with the probe's `onclick_hash` and `pushstate` pages. Assert:
   - no `skipped`/`deny` row for the landed route;
   - a `spa`/`reached` event and a counter ≥ 1;
   - the counter appears in the evidence `scan` block.

   Keep the existing hash-route tests: a pre-click refusal still yields `skipped`/`deny`, which is truthful there.

## 4. Non-blocking findings and nits

- **N-r3-1. Hash routes without a slash.** A hash route without a slash (`<a href="#logout">`, Backbone-style routers)
  is neither refused nor recorded. `_HASH_ROUTE` requires `#/` or `#!/`, so `#logout` → `ok`, and the click runs the
  route. The probe's `noslash` case hit `/api/session/end` twice. The landing URL equals the base URL, because
  `canonical` drops non-route fragments, so nothing is recorded at all.
  - This is outside the documented hash-route semantics, so it is not blocking.
  - Recommended fail-closed rule for *clickables*: also check `"/" + fragment.lstrip("!/")` against the deny list.
    Refusing an in-page anchor whose name matches a denied path costs nothing.
  - At least document it next to the R3-B1 residual.
- **N-r3-2. Stamp swap onto a form button.** The click is not re-checked against the clickable predicate. A page
  that moves `data-dps-click` from the stamped anchor onto a submit button of a GET form (MutationObserver) gets
  that button clicked (probe `swap`).
  - With `HARDEN_JS` on, which is the production default (`scan_job` does not pass `harden_js`), the submit event is
    cancelled: 0 hits.
  - With hardening off, the GET form went out, `/api/formget` = 2 (the page's own default value, an unfilled tab, no
    DPS value).
  - Defence in depth only, and the scanned site is tenant-registered. Cheap hardening: `CLICK_TARGET_JS` (or a sibling
    check) should refuse unless `el.matches(CLICKABLE) && !el.closest('form')`.
- **N-r3-3. Refused hash routes are indistinguishable in the records.** Refused hash-route rows and events all show
  target `/`, because `_redact` drops the fragment, and the rows say `discovered_via='link'`. They are value-free but
  indistinguishable in the console. Consider the value-free route template, which `template()` already computes for
  hash routes, and `spa_click` as the source.
- **N-r2-1 (unchanged).** HTTPS redirect hops to denied paths are counted (`scope_paths_reached`) but still sent; real
  bank targets are HTTPS. This should be carried as an explicit accepted residual in the programme plan / lane
  verification.
- **N-r2-2, N-r2-3, r1 N1 (unchanged).**
  - N-r2-2: evidence label wording.
  - N-r2-3: `allowed` rows with reason `deny`.
  - r1 N1: `GET /scans/{id}/pages/{page_no}` with `page_no: int` unbounded gives an int4-overflow 500, value-free.
    Bound it with `Path(ge=1, le=2**31-1)`.
- **N3–N9 of r1 (unchanged).**
  - N3: `save_batch`/`save_artefact` unfenced.
  - N5: a timeout inside `_finish` loses one batch of records.
  - N6: `scan_finalize_job` with `host_not_registered`.
  - N7: the static check could ban `.type(`/`.press_sequentially(`/`.set_checked(`/`.hover(`.
  - N8: SEC-5 is owned by WP-I.
  - N9: an application reference is readable in `/apply/review` captures.

## 5. Other checks this round (no defect found)

- Scans API router re-read:
  - every route resolves the job through `jobs.get_job(tenant_of(request), id)`, giving 404 `scan_not_found` across tenants;
  - cursors are digits only with ≤ 9 characters, otherwise 400;
  - `format`/`state` are typed, giving 422;
  - limits are clamped;
  - an idempotent replay returns neither key nor seed;
  - `no-store` is set on create and downloads;
  - an inline storage state is redacted to `{"inline": true}`, and key, seed and wrap key are popped from the stored spec.
- `ScanStore` SQL: the only interpolation is the configured schema name; every input is a bind parameter.
- Finalize fence and idempotency (r1 B1 fix): re-read. Unchanged since r2's verification, and `test_scan_finalize.py` is green.

## 6. Privacy

- Probes use synthetic pages only. They printed paths and counts, never values.
- The new code adds no log line. Refusal events carry redacted URLs or origins and verdict codes.

## 7. Clean-up

`docker compose down` was run for this lane's project only. No other project was touched. The tree is clean at `0cf0140`.
