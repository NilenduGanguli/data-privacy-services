# M4 G7 review, round 1: Tasks 24 (smoke extension, compose-level scanner gate) and 25 (docs as built)

Reviewer worktree: `/Users/neelu/document_intelligence/dps-wt-m4`, branch `m4`, HEAD `d4fc2b7`.
Commits reviewed: `a54745f` (Task 24: `tools/smoke_test.py`, `tests/test_tools_m4.py`, deviation log) and
`d4fc2b7` (Task 25: `HANDOFF.md`, `README.md`, `docs/technical/api-m4.md`, deviation log).
I did not use the impl report's claims. Everything below comes from code I read and commands I ran.
Probes and outputs are in `/Users/neelu/.cache/dps-build/reports/probe-m4-G7/review-r1/`. No repository
file was modified, and the working tree was clean before and after.

**Verdict: FAIL.** There is one blocking item: the docs' error table describes behaviour the API doesn't
have, and that behaviour also contradicts contract §9. Everything else passes, including the smoke gate
on a fresh lane stack.

## 1. Gates (run by me)

| Gate | Result |
|---|---|
| `./lane.sh ruff format --check dps tests tools` | 293 files already formatted |
| `./lane.sh ruff check dps tests tools` | All checks passed |
| `./lane.sh mypy dps` | Success: no issues found in 119 source files |
| `./lane.sh pytest -q` (no stack) | **1985 passed, 228 skipped** (351 s) |
| `docker compose --profile scanner up -d --build` (lane `dps-wt-m4`) | exit 0. app, worker, browser-worker, demo-site and dps-db healthy; migrate exited 0 |
| `DPS_SMOKE_M2=off DPS_SMOKE_SCANNER=1 ./lane.sh python tools/smoke_test.py`, started seconds after `up` (fresh `browser: unknown` race) | **all 34 checks passed**, 2 OCR checks skipped. Scan succeeded with 21 pages, 63 fields (51 personal) and 9 risk flags. Every site counter +0, `non_get_allowed` 0, blocked `{GET: 1, POST: 72}`, 3 masked captures, 0 demo values in the export, 1 evidence pack |
| same, with `--profile sources` also up and `DPS_SMOKE_M2` unset | **all 43 checks passed** (23 Layer 1 + 11 scanner + 9 M2). This confirms the HANDOFF claim and shows the smoke can be rerun on a stack that has already been scanned |
| `./lane.sh python tools/smoke_test.py` (no scanner flag, no `DPS_SMOKE_M2=off`, no sources) | fails at `m2 connections tested`, the same as before M4. The scanner block isn't entered, so "behaves exactly as before" holds |
| `DPS_TEST_REQUIRE_SERVICES=1 ./lane.sh pytest -q -m "browser or db" tests/scanner tests/test_no_values_in_logs_m4.py tests/test_tools_m4.py` | **135 passed, 16 skipped** (387 s). `-rs` shows all 16 skips are `test_capture_ocr.py` "needs tesseract": the host has no tesseract. That's G3 territory and doesn't affect G7 |
| Frontend | G7 changed no frontend files, so this didn't apply |
| `docker compose --profile scanner --profile sources down` | done; no containers left for this project |

## 2. Probes (counts and codes only)

The probe script is `review-r1/probe.py` and its output is `review-r1/probe.out`.

- **Obfuscate scan through the API** (start `/apply`, deny `/logout` and `/admin`, 3 `en_IN` personas):
  succeeded, 21 pages, 3 captures, `non_get_allowed` 0. Demo-site counters `non_get`, `form_get`,
  `logout_hits` and `admin_hits` all moved by **+0**.
- **Value scan of every read surface** of that scan: detail, pages, every page detail, inventory JSON and
  CSV, events, export JSON and CSV, scan list, evidence pack and evidence HTML. I checked for the demo
  site's ten customer values (excluding the birth date), the name parts and the demo session cookie,
  both exact and squashed to alphanumerics. Result: **0 hits on every surface**, and **0** occurrences of
  the scan's DEK or seed.
- **The capture map and reveal flow in `api-m4.md` work as written.** Each of the 3 `capture_json`
  artefacts is `dps.capture.v1` with 0 demo values. `POST /api/v1/reveal` with
  `{data, key_material, reveal_hints}` and the scan's key returned 200 three times and recovered 21
  demo-value occurrences, so the documented reveal path is real.
- **Idempotency, as documented:** the replay returns 200 with `Idempotent-Replay: true`, the same id,
  `key_material`/`synthesis_seed` null and no DEK in the body. The same key with another body returns 422
  `idempotency_key_reused`. The first answer has `Cache-Control: no-store`.
- **Tenant-scoped storage state** (HANDOFF §7 claim): a scan by tenant `othertenant` using ref
  `dps/ui/demo-onboarding` was accepted, then **failed with `secret_unavailable`** after 0 pages.
  Reading another tenant's scan returns 404 `scan_not_found`.
- **Log scans**:
  - Compose logs after all runs (331 lines): 0 exact and 0 squashed demo-value or cookie hits.
  - Smoke outputs: 0 hits.
  - The probe's own scan DEK and seed in the compose logs, smoke output and probe output: 0 hits. The
    secrets file was deleted afterwards.
- **Docs:** `api-m4.md` and the added HANDOFF/README lines contain 0 demo values. Key and seed appear
  only as placeholders.
- **Error codes against the doc's tables:**
  - These match the doc: `har_not_supported`, `host_not_registered`, `url_invalid` (userinfo),
    `start_url_denied`, `scope_invalid` (max_pages 501), `policy_invalid`, `key_material_invalid`,
    `cursor_invalid` 400, `scan_not_found` 404, `page_not_found` 404, `not_cancelable` 409 (cancel of a
    finished scan), `seed_invalid` 422, preview `count: 6` → 422.
  - **These do not match the doc** (see B1): `max_depth: 6`, `display_persona > personas`, locale
    `fr_FR`, `personas: 51`, ref containing `..`, both ref and inline state, and a malformed deny glob.
    Each returns FastAPI's 422 with `code` **absent** and a `[{type, loc, msg}]` detail.

## 3. Blocking

**B1. The `api-m4.md` accept-time error table lists codes the API never returns, and the API breaks contract §9.**

- Contract §9 (`2026-10-11-m1-m5-contract.md` around l.1390) lists the accept-time codes as "all 422,
  `dps.layers.scanner.models.validate_scan`" and includes `scope_invalid` (a bad glob), `auth_invalid` and
  `fill_invalid`.
- `docs/technical/api-m4.md` ("Accept-time and job error codes") documents:
  - `scope_invalid` for "`max_depth` outside 0–5, a malformed glob"
  - `auth_invalid` for "both a ref and an inline state, or a ref containing `..`"
  - `fill_invalid` for "personas outside 1–50, `display_persona` above `personas`, an unsupported locale"
- The actual behaviour is different. `ScanSpec`'s own field constraints and model validators
  (`dps/layers/scanner/models.py`) reject all of these first, so `validate_scan`'s re-checks
  (models.py:579–607) never run on an HTTP request. Its docstring admits this: "The model refuses most of
  these already; the re-check covers programmatic construction." The caller gets a codeless FastAPI 422.
  `fill_invalid` and `auth_invalid` **can't be produced over HTTP at all**, and `scope_invalid` only
  appears for `max_pages` above the setting.
- The doc's footnote ("a locale or persona count outside the model's bounds is FastAPI's 422") directly
  contradicts its own `fill_invalid` row.
- The Task 25 deviation entry says the table is "as built" and doesn't mention this.
- The console (`frontend/src/lib/scans.ts` `CREATE_ERRORS`) has text for these codes, but over HTTP it
  falls back to the raw validation detail. It degrades gracefully but never shows the intended wording.
- **Fix: either one closes it.**
  - (a) Preferred, because it matches the contract. Make `POST /scans` return the contract codes. Either
    move the cross-field and bounds checks (`max_depth`, globs, `display_persona`, locale, persona count,
    auth ref/inline) from the `ScanSpec` validators into `validate_scan`, or map a
    `RequestValidationError` on this route by `loc` (`scope.*` → `scope_invalid`, `auth*` →
    `auth_invalid`, `fill*` → `fill_invalid`) to a value-free `{detail, code}`. Add one API test per code.
  - (b) Correct the `api-m4.md` table, amend contract §9 to say which refusals are model-level and carry
    no code, and add an `M4-DEVIATIONS.md` entry.

## 4. Plan and acceptance comparison

- **Task 24 against the plan:**
  - The plan's scanner block is present behind `DPS_SMOKE_SCANNER == "1"` and runs before the final
    "console served" check, with `import time`. Output is check names and counts only. I verified this
    on the live output and in `test_tools_m4.py`, which checks for no DEK, seed or demo value.
  - The "no submission" claim is backed by a counting server: the demo site's `/__received`, read before
    and after.
- **Task 25:**
  - All three documents exist.
  - `api-m4.md` covers every route, the lifecycle, `dps.scan-export.v1`, `dps.capture.v1` with the reveal
    call, the evidence `scan` block and D4 with the guard rules. The route list matches
    `dps/layers/api/routers/scans.py`. Pagination is clamped to 1–200 as stated, and `events` defaults
    to 100.
  - The evidence `scan` block keys from my live scan match the doc's example. The export JSON keys and
    the CSV header (`page_no,path,form_index,field_no,label,entity_type,persona_attr,persona,synthesize_value,obfuscate_value,obfuscate_method`)
    match the doc.
  - README has the "Scan a web front end" section with the run commands.
  - HANDOFF has every `DPS_BROWSER_*` and `DPS_SCAN_*` setting with correct defaults and ranges (checked
    against `dps/config.py` 253–272), the browser image, ports 8410/9401, secrets by name only, the
    posture rules and the Task 20 traps. Task 20's traps were already in §12, and §12 adds traps 8–9.
  - The error table is wrong (B1).
- **Programme plan §2.3 row G7** ("smoke extension; docs as built"): the smoke extension is met. "Docs as
  built" is met except for B1.
- **Contract names for other lanes:** Tasks 24–25 renamed nothing.

## 5. Deviation log judgments (`docs/superpowers/build/M4-DEVIATIONS.md`)

**Task 24**

| Entry | Judgment |
|---|---|
| Eleven checks, not ten ("masked capture served") | Accepted. More coverage. It checks that a PNG is served, not that it is masked; G3's OCR tests cover masking. |
| Lane-safe demo address (`DPS_TEST_DEMO_SITE`, else `DPS_DEMO_SITE_PORT`) | Accepted and necessary: the plan's literal 8410 would read another stack's counters. |
| Stronger no-submission check (`logout_hits`, `admin_hits`, per-counter deltas) | Accepted. Stronger than the plan. |
| Export check uses all ten demo values from `data.json` | Accepted. Stronger than the plan. |
| Browser readiness polled for up to 60 s | Accepted. I reproduced it: the smoke started seconds after `up` and passed. A missing worker still fails, with no scan created (unit test). |
| Key and seed dropped; seed check reads `.seed`; `DPS_SMOKE_SCAN_TIMEOUT` | Accepted. |
| Testable signature and helpers, plus the offline unit tests | Accepted. The tests exercise each failure path and don't just pass trivially: every counter, an export value, a timeout and no browser-worker each make it exit 1. |
| Live "all 34 checks passed"; "plan's ten checks predates M1" | Accepted. I reproduced 34, and 43 with `sources`. |

**Task 25**

| Entry | Judgment |
|---|---|
| Error table "as built": plan list plus `auth_invalid`, `key_material_invalid`, `wrap_key_invalid`, idempotency codes and `queue_full`; `db_unconfigured` → `db_unavailable`; `consistency_invalid` split | Partly rejected. The renames and additions are right: `db_unavailable` (`deps.require_db`), `queue_full` (`jobs.py`), `key_material_invalid` and `wrap_key_invalid` were all verified. But the entry presents the table as built while `fill_invalid`, `auth_invalid` and the depth/glob `scope_invalid` are unreachable over HTTP (B1). |
| README and HANDOFF additions | Accepted. The claims I verified held: `scanner` extra in `pyproject.toml`, settings table, tenant-scoped refs, `browser` readiness behaviour, smoke variables. |
| "Contract: no name changed by Tasks 24–25" | Accepted for 24–25. The B1 contract discrepancy comes from Tasks 4/18 code; only this doc exposes it. |

## 6. Non-blocking (nits and suggestions)

1. The smoke's "persona export" check matches demo values as raw lowercase substrings. A digits-only
   Aadhaar or card number (`234123412346`) would get through. Suggest also matching squashed
   alphanumeric forms, as the impl's own `logscan.py` does. My squashed probe of the export found 0, so
   nothing leaks today.
2. `SCAN_TIMEOUT = float(os.environ.get("DPS_SMOKE_SCAN_TIMEOUT") or "300")` and the `data.json` read run
   at import time. A malformed `DPS_SMOKE_SCAN_TIMEOUT` crashes even a non-scanner run, with an uncaught
   traceback outside `main()`'s handler. Parse it lazily inside `scanner_checks`.
3. The plan DoD 5 literal command (`DPS_SMOKE_SCANNER=1 python tools/smoke_test.py`) fails on the scanner
   profile alone, because M2's auto mode runs the job checks without a demo source. This is M2 behaviour
   from before M4 and is documented: use `DPS_SMOKE_M2=off`, or add `--profile sources`, which I verified
   gives 43/43. The milestone report should quote the working command.
4. The `masked capture served` verdict counts captures and checks the PNG magic number only. Its name
   could say "served", not imply masking. Cosmetic.
5. HANDOFF §7 has one very long line (the "Nothing else. `DPS_BOOTSTRAP_API_KEY`…" join) and the README
   test-notes paragraph has an odd line break before "Without a service its tests skip". Style only.
6. `api-m4.md`'s example `kid` (`k_424284cf3ec721ac`) comes from a real lane scan. A kid is a public
   fingerprint and appears in evidence and exports, so it's fine, but a placeholder would be cleaner.

## 7. Files

- Probes: `/Users/neelu/.cache/dps-build/reports/probe-m4-G7/review-r1/` (`probe.py`, `probe.out`,
  `smoke-scanner.txt`, `smoke-full.txt`, `smoke-plain.txt`, `smoke-dod5-literal.txt`, `integration.txt`,
  `compose.log`, `compose-final.log`, `up.log`, `docs-added.txt`)
- Reviewed: `tools/smoke_test.py`, `tests/test_tools_m4.py`, `docs/technical/api-m4.md`, `HANDOFF.md`,
  `README.md`, `docs/superpowers/build/M4-DEVIATIONS.md`, `dps/layers/api/routers/scans.py`,
  `dps/layers/scanner/models.py`
