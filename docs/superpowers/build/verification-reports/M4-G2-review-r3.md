# M4 review: G1 and G2 (Tasks 1 to 8, G2-fix, fix r1 and fix r2), round 3

**Reviewer:** independent and read-only. **Branch:** `m4` @ `6cfdbdf` in `/Users/neelu/document_intelligence/dps-wt-m4`. The working tree was clean before and after the review.
**Verdict: FAIL.** All gates are green. There are 2 blockers:
- a D4/D-C race in `ScanGuard.arm`: popups opened while the first `arm` runs keep a live worker, and the fill value leaves on `wss://` (5 out of 5 runs);
- a value leak in the URL toolkit: short dates with `_ , : + ~ |` separators.

Both have small, verified fixes.

Probes are in `/Users/neelu/.cache/dps-build/reports/probe-m4-G2/r3/`:
- `static.txt`, `pytest_full.txt`, `pytest_integ.txt`
- `u1`..`u3` (URLs), `g1`..`g3` (guard), and `rerun_prior.txt` (the r1 and r2 probes re-run on `6cfdbdf`)

No repository file was modified: the candidate fixes were checked by monkeypatching from the probe directory. Lane `dps-db` was brought up for the tests and taken down with `docker compose down`. No other compose project was touched.

## 1. Gates (run by me)

| Gate | Result |
|---|---|
| `./lane.sh ruff format --check dps tests tools` | 266 files already formatted (exit 0). I used `--check` so the repo is not modified |
| `./lane.sh ruff check dps tests tools` | All checks passed |
| `./lane.sh mypy dps` | Success: no issues found in 111 source files |
| `./lane.sh pytest -q -rs` (full, lane `dps-db` up) | **1924 passed, 71 skipped**, 1 warning, exit 0 |
| Skip reasons | Only other milestones' services or extras: source-pg, minio, azure-ocr-mock, app, tesseract, spaCy/ner, presidio. There are 0 scanner, config_m4 or db skips |
| `DPS_TEST_REQUIRE_SERVICES=1 ./lane.sh pytest -q tests/scanner tests/test_config_m4.py tests/db` | **487 passed**, 0 skipped |
| frontend | not touched by Tasks 1 to 8, so not run |

## 2. Blocking findings

### B1: Popups opened while the first `arm` runs are neither in the snapshot nor locked at birth; their worker sends the fill value on `wss://` (D4 / D-C)

- **Where:** `dps/layers/scanner/guard.py`, `ScanGuard.arm` → `_open_pages` → `_lock`. The order of operations is:
  1. `arm` snapshots `context.pages` synchronously (`_open_pages`) and marks those pages filled.
  2. Only then, inside `_lock`, does it `await self._context.add_init_script(LOCK_JS)`. That is the first arm's lock-at-birth.
  3. Then it locks the snapshot's frames.

  A popup born after step 1 but before `LOCK_JS` is registered falls into neither set:
  - it is not in the snapshot, so it is never locked;
  - it was born too early, so it is not locked at birth.

  A worker it starts (or that its opener starts through `new w.Worker(...)` on its `about:blank` realm) survives.
- **Probe:** `r3/g1_blank_popup_worker.py`, over HTTPS (TLS counting server). The guard is installed as `install()` does, with the scope proxy.
  - The page runs `setInterval(3 ms)`. Each tick does `w = window.open('', name)`, then `new w.Worker(blob)`. No navigation request is made, so there is nothing to route.
  - After `arm` and the fill, the page posts the value to every worker. A worker opens `wss://127.0.0.1/leak-<tag>?d=<base64 value>`.
- **Result (guarded):**
  - `arm` returns **True** and `locks_failed == 0`.
  - The counting server receives **1 upgrade whose URL carries the base64 fill value** (`/leak-C72`, `C73` or `C74`). This happened in **5 out of 5 runs** (`g1_out.txt`).
  - Every popup born after registration is refused: `api_removed worker` 126–131.
- **Positive control (unguarded):** 59 upgrades carrying the value.
- **Iframes are not affected.** The same race with iframes (`g2_iframe_race.py`) gives 0 upgrades in 3 runs, because `page.frames` is read after the init-script `await`. Only popups (the page list) are exposed.
- **Why blocking:**
  - This is the same contract as r2-B1: D-C requires the worker and socket APIs to be gone on filled pages, and programme plan §2.3 requires "WebSocket → 0 received by the counting server".
  - The popup is filled by the guard's own rule (its opener is filled), and `arm` returned True, which the caller reads as "safe to fill".
  - A hostile page wins the race deterministically, because it does not need to know when `arm` is called.
- **Fix (small; verified by monkeypatch in `r3/g1b_fix_variants.py`):** on the first `arm`, register `LOCK_JS` **before** taking the page snapshot.
  - Then snapshot, mark filled and lock.
  - For robustness, add a short bounded loop: after locking, make one round trip (for example `await page.evaluate("0")`) so that page events in flight arrive. Then lock any page of `context.pages` that is not yet locked. Stop when a pass finds none, with a bounded number of rounds.
  - Both variants gave **0 upgrades** (init-first: 1 run; with the loop: 2 runs), with `locks_failed == 0`.
- **Test needed:** an HTTPS counting-server test of the g1 flow (continuous `about:blank` popups each with a worker, value posted to all after the fill). Expect 0 upgrades and 0 `/leak-*`, with the unguarded positive control. Update the "Locked documents" text in `guard.py` and in `M4-DEVIATIONS.md`.

### B2: Short dates with separators other than `- . / space` survive `redact_url` and the persisted `template`

- **Where:** `dps/layers/scanner/urls.py`.
  - `_DATE_SHAPE` admits only `[-./ ]` between parts.
  - `_PUNCT` (in `_MONTH_DATE`) admits only `[\s\-./_,']`.
  - A short date (4–5 digits) does not reach `_MIN_DIGITS`, and none of these shapes is a 4-digit run.
- **Probe:** `r3/u1_urls.py`, with both the None classifier and the real `Detector(DetectorConfig(locale="en_IN"))` (identical results). The table lists both outputs. `template_is_value_free` returns **True** for every template shown, so each one would be persisted:

| URL path | `redact_url` (stored as `url_redacted` and the like) | `template` (persisted) |
|---|---|---|
| `/dob/1_5_90` | `/dob/1_5_90` | `/dob/1_5_90` |
| `/dob/12_5_90` | unchanged | unchanged |
| `/dob/1,5,90`, `/dob/1:5:90`, `/dob/1~5~90`, `/dob/1+5+90`, `/dob/1%2B5%2B90`, `/dob/1|5|90` | unchanged (**full DOB**) | unchanged |
| `/dob/1/5_90` | `/dob/1/5_90` | `/dob/{id}/5_90` |
| `/dob/12:may:90`, `/dob/12+may+90` | unchanged | unchanged |
| `/dob/१_५_९०` (Devanagari digits) | unchanged | unchanged |
| `/x#/dob/1_5_90` | (fragment dropped) | `/x#/dob/1_5_90` |

- **Why blocking:**
  - This is the same class as r1-B3, r2-B1 and r2-B2, all three of which were treated as blocking.
  - It is also the module's own rule: "a date shape … is a value whatever its count". `_` is the most common slug separator.
- **Fix (one change closes the family):** let any one non-alphanumeric character separate date parts.
  - `_DATE_SHAPE = (?<!\d)\d{1,2}[\W_]\d{1,2}[\W_]\d{2,4}(?!\d)`
  - `_PUNCT = [\W_]{0,3}`

  I checked this by monkeypatch (`r3/u3_fix_check.py`, and the pytest plugin `r3/dpsfixplug.py`):
  - Every row above becomes `{id}`.
  - `tests/scanner/test_urls.py` and `test_models.py` still pass: **283 passed** with the patch.
  - Over-redaction is the accepted failure mode.
- **Tests:** add the rows above to `test_redact_url_hides_…`, `test_the_persisted_template_is_value_free` and `test_a_page_record_refuses_a_raw_template`, with both classifiers.

## 3. Verified correct in this round (fix r2 and earlier)

- **Fix r2 B1** (a popup opened at load relays the value to its worker):
  - The reviewer probe `r2/q6_popup_relay.py guarded` now gives `armed True`, 0 upgrades, and the value in no upgrade URL.
  - The new test `test_https_a_popup_opened_before_the_fill_relays_nothing` is genuine. Its unguarded control asserts ≥2 upgrades with the base64 value in the worker's socket URL and that `/other-fetch` arrives. The guarded run asserts 0 upgrades, 0 `/leak-*`, 0 non-GET, `filled_request` for the unrelated tab, and `api_removed` for websocket and worker.
  - The synchronous `about:blank` popup created **before** the fill (`g1` case A) is locked: `__dpsGuardLock` is present synchronously in that realm, and its worker is terminated.
- **Fix r2 B2:** all six r2 rows and the extra r2 rows now give `{id}`-only paths and templates with both classifiers (`rerun_prior.txt`).
  - Timing on hostile inputs (`u2_timing.py`): ≤ 0.09 s on 9–12 kB paths. The nested `{0,3}`/`{0,2}` quantifiers are bounded, so there is no ReDoS.
- **Earlier probes re-run on `6cfdbdf`:**
  - `p1` (noopener popups, harden on and off): 0 hits, `filled_navigation`.
  - `p3` (fresh realms on an armed page): 0 upgrades, `api_removed` websocket and worker.
  - `q4` (deny rail): unchanged from r2.
- **Deviation "every page open at `arm` is also marked filled":** accepted. It is stricter than asked and closes round-1 §5.2 for pages open at the fill.
- **Deviation "date windows up to 5 segments":** accepted. Over-redaction only.
- **Diff since r2:** `6cfdbdf` touched only `guard.py`, `urls.py`, two test files and the deviation log. No contract name or public signature changed, and the M4-internal `arm` signature is unchanged. Store, models, migrations, proxy, DOM and the demo site are unchanged since r2, and r2's verification of them stands.

## 4. Deviation log (`M4-DEVIATIONS.md`)
- **All earlier sections:** accepted as judged in rounds 1 and 2.
- **"G2 review round 2 — fix":**
  - Accepted as written, except one statement. It says no worker exists from the first fill on that could be handed a value. B1 above shows a popup born during the first `arm` breaks that claim.
  - It also claims date coverage. That holds for the listed separators only (B2).

## 5. Non-blocking
1. **`arm` hangs on a busy renderer** (`r3/g3_hung_popup.py`). A noopener tab open at the fill whose page runs `for(;;){}` makes `arm` never return: `frame.evaluate` has no timeout, and r2 made `arm` evaluate in every open page.
   - This fails safe, because the caller never fills.
   - Still, wrap `_lock_frame`'s evaluate in `asyncio.wait_for(…, ~2 s)` and treat a timeout as not locked (return False). Task 14 must bound `arm` in any case.
2. Values relayed to a page opened **after** the fill (for example through storage) can still ride in a header or an encoded URL of a GET to an allowed host from an unfilled, locked-at-birth page. This is the same residual class as r2 §5.1/§5.2 (images, prefetch, cookies). It belongs in the Task 14/15 docs.
3. The leftovers from r2 §5 are still open as assigned:
   - deny-rail edge readings (`/admin%09`, `/admin%0a`, `/ａdmin`, `/x#%2Fadmin`, `/logout%3f`, `/log%00out`, `/admin::$DATA`);
   - `redact_origin` keeps a foreign host;
   - no HTTPS unload positive control;
   - the Task 12 capture rules, N-e, N2, N-c, and the dedicated-worker `wss` residual while no page is filled.
4. Query keys and hash routes that are dates in other shapes are fixed by the same B2 change. `?1_5_90` (a bare key that is a value) becomes `<k>` once `_value_like` sees the date.
5. Style: the long docstring lines in `dom.py`, `urls.py` line ~21 and `guard.py` line ~33 (the "Locked documents" bullet runs past 100 characters).

## 6. Contract and interfaces
Unchanged since r2: no contract name is broken for the other lanes. The `jobs_models`, `config`, `observability` and `TENANT_TABLES` changes are additive, as checked in r1 and r2.
