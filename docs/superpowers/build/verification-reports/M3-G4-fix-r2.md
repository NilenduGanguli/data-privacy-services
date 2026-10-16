# M3 G4 fix, round 2: B2-r2 (branch `m3`, worktree `dps-wt-m3`)

- Base: `188914f` (fix r1). Fix commit: **`ac710bf`** `fix(m3): consistency members are distinct target columns; a refresh of one target cannot join itself`.
- Author `NilenduGanguli <nilendu.ganguli@gmail.com>`, no trailers. Committed with an explicit pathspec of 5 files. The working tree is clean afterwards.
- Artefacts: `~/.cache/dps-build/reports/probe-m3-G4/r2-fix/`.

## Finding B2-r2: a member is a target column, not a job

**Problem.** Two run jobs of one group that wrote the same target (a refresh) counted as distinct members. The check joined the target with itself, passed, and stored that as `last_check`.

### Fix (`dps/layers/connectors/consistency.py`)

**1. New `target_key(conn_id, connection, obj, path=None)`.** It gives the identity of a member's target, and of its column when `path` is given.
- **Endpoint:** engine, case-folded host, and port. When the port is unset, the engine default is used (5432 / 1521 / 27017). Database and service name are added, case-folded.
- **No host, or not a stored `Connection`:** the key falls back to the connection id. The unit fakes are in this case.
- **Object and path:** `obj.fqn()` plus the path. Both compare case-insensitively for PostgreSQL and Oracle, because unquoted identifiers fold. MongoDB names stay case-sensitive.
- **Documented limit:** host aliases are not resolved. A DNS name and its IP address, or two listeners of one instance, count as different endpoints. This is stated in the docstring, the deviation log and the contract.

**2. `_validate`.**
- It keeps the cheap `(job_id, path)` pre-check.
- It now also resolves every member's target and keys the member on `target_key(..., m.path)`. A repeat is refused with `422 member_duplicate`, before any connector opens.
- One target with two different paths (a self-join) is still allowed.

**3. `derive_members(jobs, snapshots, connections=None)`.**
- Eligible jobs that wrote the same target (the key without the path) collapse to the newest one, by `created_at` and then id. List order is kept.
- When fewer than 2 distinct targets remain, it raises `409 member_not_ready`.
- `run_consistency_check` now passes `connections`. The new parameter is optional, so existing callers are unchanged.

**4. Docs.**
- **Deviation log:** the "G4 fix round 1 / B2" entry is corrected. Its claims held only for job ids, not targets. A new section "M3 G4 fix round 2" was added.
- **Contract §6.5 amendment:** "distinct" now means distinct target columns, and the endpoint definition and the derived-mode collapse/409 rule are spelt out.
- No contract name changed.

### Tests, written first (all red before the fix, green after)

**`tests/connectors/test_consistency.py::test_two_jobs_writing_one_target_are_one_member`**
- Uses the r2 probe `World`, with the Mongo member drifted on every row.
- Explicit pairs `[mdb, rerun]`, `[ora, mdb, rerun]` and `[rerun, ora, mdb]` each give 422 `member_duplicate`, and no connector is opened.
- The derived check keeps `j_mdb_rerun` and still fails, with 3 pairs.
- When the group has only refreshes of one target, the result is 409 `member_not_ready`, both from `derive_members` and from the check.

**`::test_two_connection_records_for_one_database_are_one_target`**
- Duplicates refused with 422 and nothing opened:
  - a second Mongo connection record with host `MONGO.UAT` and the default port;
  - an Oracle object written `crm_masked.customers` with service `uat`.
- A different database on the same server runs.
- The derived check collapses accordingly.

**`tests/api/test_groups_consistency_api.py::test_a_rerun_writing_the_same_target_is_refused_and_not_stored`**
- Runs the real check behind the route. Job, snapshot and connection loading are patched, and the default opener records any open.
- A failing `last_check` is stored first.
- `[run1, run2]` and `[ora, run1, run2]` each give 422 `member_duplicate`, and no target is opened.
- `last_check` and `last_check_at` are unchanged.
- The audit shows `failed 200`, then `rejected 422` twice.

**Red runs observed.** The two unit tests failed with "DID NOT RAISE" / a passing self-join. The API test failed because the opener was called.

**One test correction while going green.** The derived member order follows the input list. My first expectation put the appended rerun in the middle. I fixed the expectation, not the code.

## Reviewer's own probe, re-run against the fix

`r2-fix/test_probe_same_target_api_fixed.py` and `same_target_api.log` are an adapted copy of `r2/test_probe_same_target_api.py`. It ran through the API with real workers on the lane's MongoDB.
- I copied it temporarily into `tests/e2e/` to get the fixtures, then removed it. It was never committed.

Results:
- `pairs=[run1, drift]`: 200, `passed=False`, 0/1 pairs, coverage 0.0, problems `join_form_mismatch` and `method_mismatch`.
- `pairs=[run1, rerun]`: **422 `member_duplicate`**. The stored `last_check.passed` stays False.
- Derived (no pairs): 2 members, the rerun and the drift. `passed=False`, 1 pair.
- The probe collections `crm_uat.profiles_probe_r2*` were dropped afterwards.

## Gates (at `ac710bf`'s content)

| Gate | Result |
|---|---|
| `./lane.sh ruff format dps tests tools` | 298 files left unchanged |
| `./lane.sh ruff check dps tests tools` | All checks passed |
| `./lane.sh mypy dps` | Success: no issues found in 109 source files |
| `./lane.sh pytest -q` (dps-db, sources, oracle up) | **2047 passed, 11 skipped** (333 s), `r2-fix/unit_full.log` |
| `DPS_TEST_REQUIRE_SERVICES=1 pytest tests/e2e/test_m3_three_way.py tests/api/test_groups_consistency_api.py tests/connectors/test_consistency.py` | **27 passed** (33 s), `r2-fix/e2e.log` |
| Adapted API probe with real workers | 1 passed, `r2-fix/same_target_api.log` |
| Log scan (3,000 seeded customers × 8 fields) over the three logs | all 0, `r2-fix/log_scan.txt` |
| Frontend | not touched, so not run |

- The pytest runs were captured with `-q`, except the probe (`-s`), so the logs are short.
- The e2e three-way targets are distinct collections and tables (`profiles_e2e` / `profiles_drift`, ...), so they are unaffected by the collapse.

**Containers.** I used only compose project `dps-wt-m3`: dps-db, plus the sources and oracle profiles. The sources profile also started app, worker and minio. I took them down with `docker compose --profile sources --profile oracle down`. I touched no other project.

## Deviations

- `derive_members` gained an optional third parameter, `connections`. This is additive.
- `target_key` is a new public helper.
- The API test patches `run_consistency_check.__kwdefaults__` to swap the default opener for a recording one, so it can prove that no target was opened. monkeypatch restores it.

## Open, non-blocking (carried, not addressed here)

- Review r2 §6 items 1 to 4 and §7 nits 1 to 3 are unchanged:
  - resume-after-kill tests for `table` modes on Oracle and Mongo;
  - the e2e log assertion should also cover the in-process API;
  - `surrogate` in obfuscate mode passes submit and then fails at prepare;
  - round-1 nits 1–5;
  - Oracle `CITY` is copied unchanged;
  - `_STREET_LEAF` / `_POSTAL_LEAF` are English-only.
- Review r2 §5 follow-up: the run-time drift detector's T0 tier still uses `classify_key`, so a drifted leaf gets no parent context.
- Endpoint identity does not resolve host aliases (documented limit).
