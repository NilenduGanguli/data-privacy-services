# M4 G7 fix, round 1: B1 (accept-time codes of `POST /scans`)

Worktree `/Users/neelu/document_intelligence/dps-wt-m4`, branch `m4`. Start HEAD `d4fc2b7` (clean tree,
no earlier fix attempt). Fix commit **`fabbd81`** `fix(m4): POST /scans answers model-level spec refusals
with the contract codes (url/scope/auth/fill_invalid)`. Author NilenduGanguli <nilendu.ganguli@gmail.com>,
no trailer.

## Finding

B1 (review `M4-G7-review-r1.md`): `ScanSpec`'s field constraints and model validators refused a bad
`max_depth`, a malformed glob, a ref with `..`, a ref plus an inline state, `personas` outside 1–50,
`display_persona > personas` and an unsupported locale **before** `validate_scan` ran. The caller got
FastAPI's codeless `[{type, loc, msg}]` 422. Because of that, `fill_invalid` and `auth_invalid` could not
be produced over HTTP, which contradicts contract §9 and the `api-m4.md` table.

## Fix: review option (a), mapping variant

I left the model validators as they were. Nothing was weakened or moved.

- `dps/layers/scanner/models.py`: new `spec_refusal_code(errors) -> str | None`. It reads only each
  error's `type` and `loc` and maps by location: `url` → `url_invalid`, `scope.*` → `scope_invalid`,
  `auth.*` → `auth_invalid`, `fill.*` → `fill_invalid`. When several sections are refused, the first in
  `validate_scan`'s order wins. It returns `None`, so the field list stays, when any error has no code:
  - an unknown key at any level (`extra_forbidden`, decision D4, including inside scope/auth/fill)
  - a refused field of another section (`mode`, `parallel_view`, `capture`, `consistency`, `policy`,
    `purpose`)
  - a body that is not an object, or a non-body location
  - a mix of these with a coded error

  I also updated the `validate_scan` docstring.
- `dps/layers/api/routers/scans.py`: `POST /scans` is now registered through
  `router.add_api_route(..., route_class_override=_CreateScanRoute)`, because `router.post` takes no
  route class. The path, status, dependencies and OpenAPI entry are unchanged. The route class catches
  `RequestValidationError`. When a code applies, it raises `rejected(request, "scan.create",
  DpsHttpError(422, code, "the scan was refused: <code>"))`. The answer is value-free
  `{detail, code}` and is audited `rejected` 422, like every other accept-time refusal. Without a code,
  it re-raises to the unchanged global handler. Dependencies still run first, so a caller without the
  `scan` scope still gets 403.
- Docs, in the same commit:
  - `docs/technical/api-m4.md`: corrected the url, scope, auth and fill rows. Replaced the
    contradicting footnote with the mapping and the exact list of cases that stay codeless.
  - Contract §9 (`docs/superpowers/plans/2026-10-11-m1-m5-contract.md`): amended with the mapping,
    the codeless cases and the rules behind each code. No code name changed.
  - `docs/superpowers/build/M4-DEVIATIONS.md`: new section "G7 review round 1 — B1".
- Frontend: no change needed. `createErrorText` already keys on `e.code`, so the console's
  `CREATE_ERRORS` wording now shows for these codes.

## Deviations (logged)

- **Wider than the review asked.** Model refusals of `url` (length outside 8–2048, not a string) now
  return `url_invalid`, and `max_pages: 0` returns `scope_invalid`. With this, every `validate_scan`
  code can be reached over HTTP.
- **Mixed bodies keep the codeless list.** A body that mixes a coded refusal with an uncoded one (for
  example an unknown key) gets the field list with no code, because a single code would hide the other
  problem. This is documented in the API doc and the contract.

## Tests (written first)

- `tests/scanner/test_scans_api.py` (db), `test_model_refusals_carry_the_contract_code`, 12 cases:
  url, scope ×4 (depth 6, max_pages 0, a deny glob without `/`, an allow glob with `?`), auth ×2,
  fill ×4, and scope before fill.
  - **Before the fix, all 12 failed** with a codeless 422.
  - After the fix, each answers exactly `{"detail": "the scan was refused: <code>", "code": <code>}`,
    with no URL value in the body and exactly one `scan.create` `rejected` 422 audit event that holds
    no values.
- `test_uncoded_model_refusals_stay_field_lists` (db): unknown keys in scope and fill, a wrong `mode`
  and a mixed body stay codeless field lists, and 403 still comes first. This is a guard and passed
  before the fix too.
- `tests/scanner/test_models.py`:
  - 13 refused bodies map to their codes. This failed before the fix with an ImportError.
  - 9 uncoded bodies return `None`: extra keys at three levels, `mode`, `capture`, `parallel_view` and
    two mixed bodies.
  - Non-body and empty error lists return `None`.

## Gates (after the fix, final tree)

| Gate | Result |
|---|---|
| `./lane.sh ruff format dps tests tools` | 293 files left unchanged |
| `./lane.sh ruff check dps tests tools` | All checks passed |
| `./lane.sh mypy dps` | Success: no issues found in 119 source files |
| `./lane.sh pytest -q` (lane `dps-db` up) | **2162 passed, 87 skipped** (441 s) |
| `DPS_TEST_REQUIRE_SERVICES=1 pytest -m db tests/scanner/test_scans_api.py tests/scanner/test_models.py tests/api tests/test_no_values_in_logs_m4.py` | 33 passed, 2 errors. Both errors were "source-pg unreachable" in M2 tests. With `--profile sources up -d source-pg`, both passed (2 passed) |
| `tests/scanner/test_scans_api.py` (db, services required) | 24 passed |
| Log scan: output of the new API tests (`-s`, log_cli DEBUG, 171 lines) | 0 hits for the URL's personal value (`ananya`, `example-mail`) |
| Frontend | no frontend file changed, so this didn't apply |
| `docker compose --profile sources down` | done; 0 containers left for this project |

## Open issues

- None for B1.
- I didn't rerun the browser and scanner compose profile (the smoke gate). This change touches only
  the accept path of `POST /scans`, which the db API tests cover live against this lane's database.
