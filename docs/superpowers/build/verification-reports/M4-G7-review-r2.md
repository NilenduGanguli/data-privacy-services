# M4 G7 review, round 2: Tasks 24 (smoke extension, compose-level scanner gate) and 25 (docs as built)

Reviewer worktree: `/Users/neelu/document_intelligence/dps-wt-m4`, branch `m4`, HEAD `fabbd81`. The working
tree was clean before and after. I modified no repository file.

New since round 1: one commit, `fabbd81` "fix(m4): POST /scans answers model-level spec refusals with the
contract codes". It touches `dps/layers/api/routers/scans.py`, `dps/layers/scanner/models.py`, two test
files, `api-m4.md`, contract §9 and `M4-DEVIATIONS.md`. Tasks 24 and 25 (`a54745f`, `d4fc2b7`) are
unchanged since round 1, so I re-ran their gates rather than re-reading them line by line.

I didn't trust `M4-G7-impl.md` or `M4-G7-fix-r1.md`. Everything below comes from code I read and commands I
ran. Probes and outputs are in `/Users/neelu/.cache/dps-build/reports/probe-m4-G7/review-r2/`.

**Verdict: PASS.** Round 1's blocker B1 is closed. Every gate is green and I found no leak. Nothing is
blocking; the nits are in §5.

## 1. Gates (run by me)

| Gate | Result |
|---|---|
| `./lane.sh ruff format --check dps tests tools` | 293 files already formatted |
| `./lane.sh ruff check dps tests tools` | All checks passed |
| `./lane.sh mypy dps` | Success: no issues found in 119 source files |
| `./lane.sh pytest -q` (no stack) | **2008 passed, 241 skipped** (349 s). Output in `pytest-unit.txt` |
| `docker compose --profile scanner up -d --build` | exit 0 |
| `DPS_SMOKE_M2=off DPS_SMOKE_SCANNER=1 ./lane.sh python tools/smoke_test.py`, started seconds after `up` | **all 34 checks passed**, 2 skipped (OCR not up). Scan: 21 pages, 63 fields (51 personal), 9 risk flags. Site counters non_get, form_get, logout_hits and admin_hits all +0; `non_get_allowed` 0; blocked `{GET: 1, POST: 71}`; 3/3 masked captures; 0 demo values in the export; 1 evidence pack (`smoke-scanner.txt`) |
| `DPS_TEST_REQUIRE_SERVICES=1 ./lane.sh pytest -q -rs -m "browser or db" tests/scanner tests/test_no_values_in_logs_m4.py tests/test_tools_m4.py` | **148 passed, 16 skipped** (round 1: 135 passed; the +13 are the new db cases). All 16 skips are `test_capture_ocr.py` "needs tesseract" (no tesseract on the host; that's G3 territory) |
| The fix's new tests with services required (`-k "refusal or uncoded"` on `test_models.py` and `test_scans_api.py`) | 36 passed (13 + 9 + 1 model cases, 12 + 1 live API cases). The db cases ran; none were skipped |
| Frontend | No frontend file changed in G7 or the fix, so this didn't apply |
| `docker compose --profile scanner down` | done; 0 containers left for this project |

## 2. Round-1 B1: closed

B1 was that `scope_invalid` (depth, glob), `auth_invalid` and `fill_invalid` couldn't reach an HTTP caller,
yet `api-m4.md` and contract §9 documented them.

**The fix.** It is review option (a), the mapping variant:

- `spec_refusal_code(errors)` maps `RequestValidationError` errors by `loc`: `url` → `url_invalid`,
  `scope.*` → `scope_invalid`, `auth.*` → `auth_invalid`, `fill.*` → `fill_invalid`.
- When several sections are refused, the first in `validate_scan`'s order wins.
- It reads only `type` and `loc`, never `input`.
- Any `extra_forbidden` error, any other section, or any `loc` shorter than 2 returns `None`, and the
  route keeps FastAPI's codeless 422.
- A route class (`_CreateScanRoute`) wraps only `POST /scans`. It raises
  `rejected(request, "scan.create", DpsHttpError(422, code, "the scan was refused: <code>"))` with
  `from None`, so the refusal is audited and the exception chain is dropped.

**Live probe** (`probe_live.py` / `.out`, run against the lane app):

- **Request bodies.** Every body carried the demo site's ten customer values (in the start URL's query and
  in refused fields such as the glob, locale, `storage_state` and `mode`).
- **Coded refusals: 14/14 matched.** Each response was exactly
  `{"detail": "the scan was refused: <code>", "code": "<code>"}`:
  - `url_invalid` ×3: too long, not a string, and `ftp:` via `validate_scan`
  - `scope_invalid` ×4: depth 6, pages 0, pages 10000 via `validate_scan`, and a glob that is a value
  - `auth_invalid` ×3: a `..` ref, a ref plus a state, a state that isn't a dict
  - `fill_invalid` ×3: 51 personas, `display_persona` 3 > 2, a locale that is a value
  - `har_not_supported` ×1
- **Codeless refusals: 4/4 stayed codeless.** These were an extra key, a bad `mode`, a mixed body and
  `policy: 5`.
- **Values in responses:** 0 hits across all 18 responses, exact or squashed.
- **Order of checks:** a bad API key still gets 401 before validation, and malformed JSON stays a codeless
  422.
- **Audit:** `dps.dps_audit_log` holds exactly 14 `scan.create` / `rejected` / 422 rows on `/api/v1/scans`
  for the window, one per coded refusal. Codeless refusals are not audited, which matches the docs. The
  rows contain 0 demo values (`audit_rows.jsonl`, `valuescan.out`).
- **Logs:** app, worker and browser-worker logs for the session (110 lines) contain 0 demo values, exact
  or squashed (`compose.log`).

**Offline mapping probe** (`probe_map.py` / `.out`). I ran 19 edge bodies through the mapping:

- `url` missing or `None` → `url_invalid`.
- `scope: null`, `same_origin: "maybe"`, `deny` as a string and 101 deny globs → `scope_invalid`.
- `auth: []`, an upper-case ref and a string state → `auth_invalid`.
- `personas: "many"`, `display_persona: 0` and an integer locale → `fill_invalid`.
- `policy: 5`, an extra key in `consistency`, `purpose: "x"`, a list body and `url+mode` → `None`.

Every result agrees with the amended docs and contract. No coded refusal hides an uncoded error, because any
uncoded error makes the whole result `None`.

**Leak surface.** The app's `_validation_error` handler already strips `input` and `ctx` (`dps/app.py`
216–223), and every `ScanSpec` level sets `hide_input_in_errors=True`. The new `detail` is a constant
template filled with a code. `AuditEvent` has no free-text field. I found no route that could echo a value.

**Ordering and side effects.** The scope, rate-limit and DB dependencies still run before body validation,
as the 403 test and my 401 probe show. Validation fails before the handler body runs, so a refused body
never touches idempotency, the KEK, the job row or the seal. A strict audit sink's `audit_unavailable` 503
propagates exactly as it does for the other accept-time refusals.

**Wiring.** The route is registered via `router.add_api_route(..., route_class_override=_CreateScanRoute)`.
It keeps POST, 202, the same dependencies and the `scans` tag, and only this route uses the custom class.

**Console.** `frontend/src/lib/scans.ts` `createErrorText` keys on `code`, so the `CREATE_ERRORS` wording
for these codes now reaches the user. No frontend change was needed.

**Tests.** They test the claim and don't pass trivially:

- The API cases send `URL` with `ananya.k%40example-mail.in` in the query, then assert exact body
  equality, exactly one `rejected` audit event, and no value in the response or in the audit events.
- The deviation log says 12 of the 13 API cases failed before the fix. That is plausible: only `max_pages`
  above the cap was coded before.

## 3. Plan, contract, spec and programme-plan comparison

- **Task 24 (smoke).** Unchanged since round 1 and re-verified live: 34/34 checks, with no-submission backed
  by the demo site's counting `/__received` endpoint, and the export compared against the ten source values.
- **Task 25 (docs).**
  - `api-m4.md`'s accept-time table now matches behaviour for every code I could exercise.
  - The footnote lists the codeless cases accurately, as my offline and live probes confirmed.
  - The plan-name drift (`db_unconfigured` → `db_unavailable`, `consistency_invalid` split) is still
    explained.
  - README and HANDOFF are unchanged since round 1, where I accepted them.
- **Contract §9.** The amendment documents the mapping, the order, the codeless cases, and the bounds
  behind `scope_invalid`, `auth_invalid` and `fill_invalid`:
  - `scope_invalid`: `max_pages` 1..cap, `max_depth` 0..5, a bad glob.
  - `auth_invalid`: a ref plus a state, a ref containing `..`.
  - `fill_invalid`: personas 1..50, `display_persona` 1..`personas`, a locale in `SUPPORTED_LOCALES`.

  No code name changed, so no other lane's interface is broken. Lanes that treated these refusals as
  codeless lists now get a `{detail, code}` object, which is the contract's §8.2 wire shape. The M4
  console handles both.
- **Programme plan §2.3 row G7** ("smoke extension; docs as built"): met.

## 4. Deviation log judgments

- **Task 24 entries.** Unchanged and still accepted, for the round-1 reasons, which I re-verified with the
  live 34/34 run.
- **Task 25 entry.** Round 1 partly rejected it for the "as built" error table. The new "G7 review round 1
  — B1" entry corrects the record, and the code now makes the table true. **Accepted.**
- **New B1 entry:**
  - **Fix description: accepted.** The mapping variant leaves every model validator unchanged, so no
    check is weakened.
  - **"Deliberately left codeless": accepted.** Keeping D4 unknown keys and mixed bodies codeless is
    sound, because one code would hide the other problem.
  - **"Beyond the review's three codes" (`url` model refusals → `url_invalid`, `max_pages: 0` →
    `scope_invalid`): accepted.** It's consistent with contract §9's code list.
  - **Contract amendment: accepted.** No names changed.
  - **Tests: accepted,** verified as above.

## 5. Non-blocking (nits)

1. **`api-m4.md` overstates one shared shape.** It says "Every refusal in this table is
   `{"detail": "the scan was refused: <code>", ...}` and is audited (`scan.create`, `rejected`)". Two
   parts are off:
   - **Detail text.** That fixed detail holds only for the `validate_scan` and model-mapped codes. These
     use other texts: `policy_invalid` ("the policy does not resolve"), `key_material_invalid`,
     `wrap_key_invalid`, `kek_unconfigured`, `queue_full`, the idempotency codes and `db_unavailable`.
   - **Audit.** `db_unavailable` comes from the `require_db` dependency, outside the audited route body,
     so it isn't audited.

   The codes are right, and callers key on `code`. Suggested wording: "each refusal is
   `{"detail", "code"}`; all but `db_unavailable` are audited".
2. **Console wording for `auth_invalid` is now narrow.** The text ("The stored session reference is not
   valid for this tenant") also covers a malformed inline `storage_state` and a ref plus a state. A
   broader wording would help, but that's console cosmetics.
3. **Round-1 nits 1–6 are still open:**
   - the smoke export check should also match squashed forms;
   - `DPS_SMOKE_SCAN_TIMEOUT` is parsed at import time;
   - the DoD-5 literal command needs `DPS_SMOKE_M2=off` or `--profile sources`;
   - the name "masked capture served" (it checks the PNG magic number only);
   - long lines in HANDOFF and README;
   - the real `kid` in the `api-m4.md` example.

   None blocks.
4. **A missing `url` now answers `url_invalid`.** That's reasonable and documented implicitly ("a `url`
   the model refuses"), but it could be named explicitly.

## 6. Files

- **Probes:** `/Users/neelu/.cache/dps-build/reports/probe-m4-G7/review-r2/`
  - scripts: `probe_map.py`, `probe_live.py`, `scanfiles.py`
  - outputs: `probe_map.out`, `probe_live.out`, `valuescan.out`, `audit_rows.jsonl`, `compose.log`,
    `smoke-scanner.txt`, `integration.txt`, `pytest-unit.txt`, `up.log`
- **Reviewed:**
  - code: `dps/layers/api/routers/scans.py`, `dps/layers/scanner/models.py`, `dps/jobs_models.py` (ScanSpec),
    `dps/app.py` (`_validation_error`), `dps/layers/api/deps.py` (`rejected`, `audit_action`, `require_db`)
  - tests: `tests/scanner/test_models.py`, `tests/scanner/test_scans_api.py`
  - docs: `docs/technical/api-m4.md`, `docs/superpowers/plans/2026-10-11-m1-m5-contract.md` §9,
    `docs/superpowers/build/M4-DEVIATIONS.md`
  - console: `frontend/src/lib/scans.ts`
