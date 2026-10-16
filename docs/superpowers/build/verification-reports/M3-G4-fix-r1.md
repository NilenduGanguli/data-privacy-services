# M3 G4 fix, round 1: review findings B1 and B2 (branch `m3`, worktree `dps-wt-m3`)

Commit: `188914f fix(m3): discovery scores document leaves through their parent path; consistency check refuses repeated members`
(author NilenduGanguli <nilendu.ganguli@gmail.com>, no trailers). Base was `b725cc8` with a clean tree; no earlier fix attempt existed.
Evidence: `~/.cache/dps-build/reports/m3-g4-fix/` (`e2e_before_fix.log`, `e2e.log`, `log_scan.txt`, `unit_gates.log`, `integration_all.log`).

## B1: MongoDB address lines and PIN codes leaked by the three-way demo

**Cause.** `discovery.classify_columns` scored names with `classify_key(column.path)`, and `classify_key` reads only the last path segment. So `$.contact.addresses[*].line1` and `pin` got no entity, and without NER no value detector matched them either.

**Fix.**
- New `dps.engine.detect.structural.classify_path(path)` returns `classify_key`'s candidates plus context from the **nearest parent segment that classifies**. Plurals are folded, so `addresses` becomes `address` and `phones` becomes `phone`.
  - When that parent's best candidate is `STREET_ADDRESS` (so not `emailAddress` or `ipAddress`):
    - a street-line leaf (`line1`, `line_2`, `street`, `house no`, …) becomes `STREET_ADDRESS` at 0.85;
    - a `pin`, `zip`, `postcode` or `postal` leaf becomes `POSTAL_CODE` at 0.85.
  - A generic leaf (`number`, `no`, `value`) that classifies to nothing takes the parent's best entity at 0.9 × its score. For example `$.phones[*].number` becomes PHONE and `$.passport.number` becomes PASSPORT.
  - A path with no parents (a SQL column or a top-level field) gets exactly the same result as `classify_key`. This is tested, so SQL discovery does not change.
- `discovery.classify_columns` now uses `classify_path`.

**Tests. Each failed before the fix.**
- `tests/test_structural.py`, 18 new parametrised cases:
  - address, postcode and generic-leaf inheritance cases;
  - unrelated leaves stay unclassified: `addresses[*].type`, `.country`, `emailAddress.line1`, `ipAddress.pin`, `order.number`, `accounts[*].balance`, `kyc.verifiedAt`;
  - for paths without parents, `classify_path` equals `classify_key`.
- `tests/connectors/test_discovery_m3.py::test_document_leaves_are_classified_by_their_parent_path` runs on the `crm.profiles` shape:
  - `line1` becomes STREET_ADDRESS and `pin` becomes POSTAL_CODE; neither keeps its value, and both carry the `structural` detector;
  - `type` and `segment` stay unclassified.
  - Before the fix it failed with `(None, None)`.
- `tests/e2e/test_m3_three_way.py` now checks an **independent** `SENSITIVE` list taken from the seed model, not the accepted snapshot's columns:
  - **Fields checked:** name, e-mail, mobile, national id, tax id, DOB, address line, PIN, IBAN, notes and the customer id, wherever the member table has them. That is 10 Oracle columns, 8 PostgreSQL columns and 10 MongoDB leaf paths.
  - **Presence:** every listed field must be present.
  - **Survivors:** 0 same-row survivors for every field, and 0 any-row survivors for text fields. DOB and numeric ids are checked per row only.
  - **Row alignment:** every table row must line up with its source row.
  - The unused `_sensitive()` helper was removed (review nit 6).
  - **Red run** (`e2e_before_fix.log`): only the discovery wiring was reverted to `classify_key`. The test failed on `('mongo', '$.contact.addresses[*].line1', 2400, 2400)`. Oracle and PostgreSQL passed the new list.
  - **Green run** (`e2e.log`): all 28 fields are 0 / 0. `kyc.taxId` compared 1447 values; every other field compared 2400. The consistency check passed 3/3 pairs at coverage 1.0, and the drift member still fails with `join_form_mismatch` and `method_mismatch`.
- **Log scan** (`log_scan.txt`) of the captured `-s` run, which includes the in-process API: 0 hits for e-mail, full name, mobile, national id, `CUST-` id, tax id and address line across 3,000 seeded customers.

**Deviation log.** The T19 entry that claimed "per-column source ≠ target for every column each accepted snapshot transforms (programme verification rule)" now states the rule correctly: every sensitive column. It also records the correction. The "Open: Mongo addresses not discovered" item is struck through and points to the new section "M3 G4 fix round 1".

## B2: a repeated member gave a false pass that was stored as group state

**Fix.** `consistency._validate` now refuses a repeated `(job_id, path)` with **`422 member_duplicate`**, before any member is looked up or any connector is opened. `pairs` already requires at least 2 entries, so there are now always at least 2 distinct members. One job with two different paths (a self-join) is still allowed. The `ConsistencyCheckRequest` docstring and the `run_consistency_check` Raises section were updated.

**Contract.** The §6.5 amendment now says "2–8 distinct members…", and `member_duplicate` is added to the 422 error list. Contract names are unchanged; this is an additive code.

**Tests. Each failed before the fix.**
- `tests/connectors/test_consistency.py::test_a_repeated_member_is_refused`:
  - with the Mongo tokens drifted on every row, the derived check fails;
  - `[m, m]` and `[a, m, a]` both give 422 `member_duplicate`, and no connector is opened;
  - the self-join `CUSTOMER_ID`/`REFERRER_ID` on one job runs.
  - Before the fix this failed with "DID NOT RAISE", and the log showed `passed=True` for 1 pair.
- `tests/api/test_groups_consistency_api.py::test_a_repeated_member_is_refused_and_not_stored` runs the real check, not the canned one:
  - the response is 422 `member_duplicate`;
  - the audit records `rejected` with status 422;
  - `last_check` and `last_check_at` stay null;
  - the same job with two paths is not reported as a duplicate.
  - Before the fix it returned `member_not_in_group`.

## Gates (after the fix, all run by me)

| Gate | Result |
|---|---|
| `./lane.sh ruff format dps tests tools` | 298 files left unchanged |
| `./lane.sh ruff check dps tests tools` | All checks passed |
| `./lane.sh mypy dps` | Success: no issues found in 109 source files |
| `./lane.sh pytest -q` (containers up) | 2041 passed, 14 skipped (5 min 45 s) |
| `DPS_TEST_REQUIRE_SERVICES=1 pytest -s` three-way e2e + resume-after-kill + Mongo pipeline | 8 passed (133 s) |
| `DPS_TEST_REQUIRE_SERVICES=1 pytest -rs -m "(db or pg_source or oracle or mongo) and not s3 and not browser and not ocr and not network"` | 322 passed, 1 skipped (`conformance.py:196` mongo: no composite keys, by design) |
| Frontend | not touched, not run |

Containers used: `dps-db`, `source-pg`, `oracle` and `mongo` of compose project `dps-wt-m3`. All were stopped with `docker compose --profile full down`. A plain `down` leaves the profiled services running. No other project was touched.

## Open / not done in this round (not blocking per the review)
- **Review §3:** there are no kill/resume tests for `table` (`truncate_insert`, `upsert`) on Oracle and Mongo, and no test for Oracle `append` with a numeric offset. The reviewer's probe shows the behaviour is correct. This is outside this round's two findings.
- **Review nit 7:** the e2e's own log assertion still covers only the worker logs. I scanned the in-process API log separately (above) but did not add it to the test.
- **`classify_key` callers:** the M0 detector's T0 tier (`ensemble.py`) and `schema_profile.py` still use `classify_key`. They see top-level names; a follow-up can route them through `classify_path` if JSON payload detection needs parent context.
- **Pipeline and resume tests:** they keep their explicit `ADDRESS_REVIEW` overrides. These are now redundant but harmless, since a reviewer may still choose the method.
- **Still open (unchanged):** the `surrogate` override in obfuscate mode (it fails at prepare instead of being refused at submit, M2 behaviour) and review nits 1–5.
