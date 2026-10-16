# M3 G4 review, round 2: Tasks 15, 16, 17, 19 after fix r1 (branch `m3`, worktree `dps-wt-m3`)

Reviewer: independent. I did not rely on `M3-G4-impl.md` or `M3-G4-fix-r1.md`.
- HEAD reviewed: `188914f` (fix r1 on top of `b725cc8`).
- The working tree was clean before and after. I modified no repository files.
- Fix commit authorship: author `NilenduGanguli <nilendu.ganguli@gmail.com>`, no trailers, conventional message.
- Probes and logs for this round are in `~/.cache/dps-build/reports/probe-m3-G4/r2/`.

## Verdict: FAIL (1 blocking)

- **B1** (MongoDB address lines and PIN codes leaked) is **fixed**. An independent probe over every column and leaf path confirms it.
- **B2** (a false consistency pass stored as group state) is **only partly fixed**. The fix refuses a repeated `(job_id, path)`. But it treats a member's identity as its *job*, not its *target column*. Two different run jobs of one group that write the same target are still two "distinct" members. The check then joins the target with itself, passes at coverage 1.0 and stores that as the group's `last_check`, even while a real cross-system member of the same group fails.
  - This is reproduced **through the API on the lane's MongoDB with real workers**.
  - The two jobs are an ordinary UAT refresh: the same spec submitted twice.
  - The fix commit says "a self-joined target can no longer pass and be stored as the group's `last_check`". That claim is false.

Every gate is green.

## 1. Gates (run by me)

| Gate | Result |
|---|---|
| `./lane.sh ruff format --check dps tests tools` | 298 files already formatted |
| `./lane.sh ruff check dps tests tools` | All checks passed |
| `./lane.sh mypy dps` | Success: no issues found in 109 source files |
| `./lane.sh pytest -q` (containers up) | **2041 passed, 14 skipped** (5 min 45 s), `r2/unit_full.log` |
| `DPS_TEST_REQUIRE_SERVICES=1 pytest -s`: `tests/e2e/test_m3_three_way.py`, `tests/e2e/test_m3_resume_after_kill.py`, `tests/connectors/test_mongo_pipeline.py` | **8 passed** (138 s), `r2/e2e.log` |
| `DPS_TEST_REQUIRE_SERVICES=1 pytest -rs -m "(db or pg_source or oracle or mongo) and not s3 and not browser and not ocr and not network"` | **322 passed, 1 skipped**, 1732 deselected (5 min 22 s). The skip is `conformance.py:196`: mongo has no composite keys, by design. Log: `r2/integration_all.log` |
| Frontend | not touched by G4, so not run |

**Containers.** I used `dps-db`, `source-pg`, `oracle` and `mongo` of compose project `dps-wt-m3` (ports from its `.env`). I dropped my two probe collections (`crm_uat.profiles_probe_r2*`) and ran `docker compose --profile sources --profile oracle down`. I touched no other project.

**Value-free three-way result** (`r2/three_way_summary.txt`):
- passed; sampled 500; 3/3 pairs matched 500 at coverage 1.0.
- Forms: postgres `numeric`, mongo `prefixed_digits` (width 8, prefix_len 5), oracle `numeric`.
- Counted attributes agree 500/500: EMAIL, PERSON_NAME, PHONE, NATIONAL_ID (oracle and pg), and STREET_ADDRESS (oracle and mongo, now `det_token` on both). PAN_IN agrees 301/301.
- Informational attributes: POSTAL_CODE `generalize` and DATE_OF_BIRTH `date_shift` (2/500).
- The drift member gives problems `join_form_mismatch` and `method_mismatch`.

## 2. Blocking finding

### B2-r2: two jobs writing the same target pass the check as two "distinct" members

**Where.** `dps/layers/connectors/consistency.py` `_validate`:
```python
if len({(m.job_id, m.path) for m in wanted}) != len(wanted):
    raise ConsistencyError(422, "member_duplicate")
```
Nothing in `submit_run` / `validate_run` stops a second run of a group from writing a target that an earlier run of the same group wrote. A refresh with `table_load: truncate_insert` re-runs the same spec.

**Evidence 1, unit fakes** (`r2/test_probe_same_target.py`, `r2/same_target.txt`). The setup is the `World` of `tests/connectors/test_consistency.py` with the Mongo tokens drifted on every row.
- The derived check over (ora, mdb, pg) gives `passed=False`, 1/3 pairs.
- `pairs=[j_mdb, j_mdb_rerun]`, where the second job has the same connection and object, gives **`passed=True`**, 1/1 pairs at coverage `[1.0]`, objects `['profiles', 'profiles']`.

**Evidence 2, through the API with real workers on the lane's MongoDB** (`r2/test_probe_same_target_api.py`, `r2/same_target_api.txt`). The probe ran:
1. discover and accept `crm.profiles`, then create a group;
2. run 1 to `crm_uat.profiles_probe_r2`;
3. run 2: the same spec again, to the same target;
4. run 3: a drifted `det_token` on `customerId`, to `profiles_probe_r2_drift`.

Results:
- `pairs=[run1, run3]` gives 200, `passed=False`, 0/1 pairs, coverage 0.0, problems `join_form_mismatch` and `method_mismatch`.
- `pairs=[run1, run2]` gives 200, **`passed=True`**, 1/1 pairs, coverage 1.0, members `['profiles_probe_r2', 'profiles_probe_r2']`, no problems.
- `GET /job-groups/{id}` then shows `last_check.passed == True`. The Task 18 console panel reads this.

**Why it blocks.** It is the same false verdict, persisted as group state, that round 1 blocked as B2. Only the route to it changed: two job ids instead of one. The contract §6.5 amendment says "2–8 distinct members". The fix defines "distinct" in a way that does not stop a target from joining itself.

**Fix (test-first):**
1. **`_validate`.** After `_target(job)`, key members on the resolved target column `(connection_id, object.fqn(), path)`, not on `(job_id, path)`. Refuse a repeat with `422 member_duplicate`, before any connector opens. Keep the self-join allowance: one target, two different paths (`CUSTOMER_ID` ↔ `REFERRER_ID`) stays allowed.
   - Better still, key on the connection's endpoint identity (engine plus host/port/service or database, from the stored `Connection` params) so that two connection records for one database do not slip through. At minimum, document that limit.
2. **`derive_members`.** Collapse eligible jobs that resolve to the same target to one member: the newest by `updated_at`/`created_at`, or the latest succeeded. If fewer than 2 distinct targets remain, raise `409 member_not_ready`. Otherwise a group whose only runs are refreshes of one table passes "cross-system" with one system.
3. **Tests.**
   - A unit test with a second job on the same connection and object (the `World` probe above), for both explicit `pairs` (422) and derived mode (collapsed, or 409).
   - An API test asserting 422 `member_duplicate`, `last_check` unchanged, and an audit row `rejected`.
4. **Docs.** Correct the deviation-log entry "M3 G4 fix round 1 / B2" and the contract §6.5 amendment wording: distinct means distinct target columns.

## 3. B1 re-verification (fixed)

**Code.**
- `structural.classify_path` keeps `classify_key`'s leaf candidates and adds context from the nearest classifying parent segment, with plurals folded:
  - address parent with a street-line leaf → STREET_ADDRESS 0.85;
  - address parent with a pin/zip/postcode leaf → POSTAL_CODE 0.85;
  - a generic leaf (`number`, `value`) inherits the parent's entity at 0.9×.
- A path with fewer than 2 named segments returns exactly `classify_key`, so SQL discovery is unchanged. There is a test for this.
- `discovery.classify_columns` is the only call site that changed. The fix touches no write or resume path: `git diff --stat b725cc8..HEAD -- dps` shows only `structural.py`, `consistency.py` and `discovery.py`.

**Independent source-vs-target comparison** (`r2/all_columns.txt`). I re-ran round 1's `probe_all_columns.py` over every column and leaf path of the three e2e targets after this round's run. Counts only.
- **MongoDB:** `contact.addresses[*].line1` and `pin` are now 0 same-row and 0 any-row (they were 2400/2400). customerId, fullName, email, mobile, taxId (1447), iban and notes are all 0/0.
  - `profile.dob` is 0 same-row and 282 any-row. A shifted date can equal another customer's date, so this is expected.
  - The values that survive unchanged are all non-personal: `_id`, `accounts[*].balance`, `addresses[*].type`, `kyc.verifiedAt`, `riskScore`, `segment` and `tags`.
- **Oracle:** every personal column has any-row 0, except DOB at 282, which is expected. **CITY survives 2400/2400.** See nit 1.
- **PostgreSQL:** every personal column has any-row 0, except `date_of_birth` at 282.

**The e2e test now checks an independent list.** The `SENSITIVE` dict holds 10 Oracle columns, 8 PostgreSQL columns and 10 Mongo paths.
- I cross-checked it against `docker/oracle/initdb/01_crm.sql`, `tools/seed_sources.py` (customers DDL) and `tools/seed_mongo.py`. Every personal field is listed except Oracle `CITY`, a low-sensitivity quasi-identifier.
- The test asserts that each field is present, that every table row lines up with its source row, 0 same-row survivors, and 0 any-row survivors for text.
- The red run quoted in the fix report fails on `$.contact.addresses[*].line1`, which is consistent with the code.

**Unit tests.** The `classify_path` parametrised cases and `test_document_leaves_are_classified_by_their_parent_path` on the `crm.profiles` shape are meaningful. They check entity, method other than `keep`, `structural` evidence, and that unrelated leaves stay unclassified.

**Log scan** (`r2/log_scan.txt`). I scanned the e2e, integration and unit logs, which include the in-process API, for 3,000 seeded customers' e-mail, full name, mobile, national id, `CUST-` id, tax id, address line and notes. All counts are 0.

## 4. Other checks this round

- **B2's own case.** `[m, m]` and `[a, m, a]` give 422 `member_duplicate` before any connector opens. The API test runs the real check, confirms the audit records `rejected` with 422, and confirms `last_check` stays null. A self-join on one job still runs. The test is correct for what it covers; the gap is in §2.
- **Tenant isolation, value-free errors and results, connector close, and injection safety** are unchanged since round 1, where I verified them. `consistency.py` changed only in `_validate` and docstrings.
- **Contract.** The §6.5 amendment adds `member_duplicate`, which is additive. No other lane's names changed.

## 5. Deviation log (entries changed in fix r1)

| Entry | Judgement |
|---|---|
| T19 "per-column source ≠ target for every sensitive column … corrected in G4 fix r1" | **Accepted.** It now states the programme rule, and the test implements it with an independent list. |
| Open item "Mongo addresses not discovered", struck through and marked fixed | **Accepted.** Verified in §3. |
| "M3 G4 fix round 1 / B1" (`classify_path`; `ensemble.py` and `schema_profile.py` still use `classify_key`) | **Accepted.** Note: a leaf that drifts in at run time under `unclassified: detect` goes through the M0 detector, whose T0 tier uses `classify_key`, so it gets no parent context. A drifted `addresses[*].line1` is found only by value patterns. Carry this to G6 or lane verification as a follow-up. It is not blocking, because discovery covers the accepted shape. |
| "M3 G4 fix round 1 / B2" (`member_duplicate` on `(job_id, path)`) | **Rejected as complete.** See B2-r2. The wording "so with `pairs`' `min_length=2` there are always ≥ 2 distinct members" is true only of job ids, not of targets. |
| Round-1 judgements of all other entries | Unchanged; see `M3-G4-review-r1.md` §5. |

## 6. Carried, non-blocking (unchanged from round 1; must not be dropped)

1. **Programme plan §2.2 G4 asks for "resume-after-kill on Oracle and Mongo for every write mode".**
   - The repository tests cover only `in_place` and `append`.
   - `table` (`truncate_insert`, `upsert`) on Oracle and Mongo is proven only by round 1's probe (`probe-m3-G4/test_probe_table_resume.py`, `table_resume.txt`: all four digests equal, 3 partitions resumed).
   - Fix r1 touched no write or resume code, so that evidence still holds at HEAD.
   - Add the four cases, plus Oracle `append` with a numeric offset, to `tests/e2e/test_m3_resume_after_kill.py` before lane verification.
2. Round-1 review nit 7: the e2e's own log assertion still covers only the worker logs. I scanned the in-process API separately (clean), but the test should assert it too.
3. A `surrogate` override in obfuscate mode passes submit and then fails at prepare (M2 behaviour). Route it to WP-H/F.
4. Round-1 nits 1–5 are still open:
   - DOB differs across systems because of the `date_shift` anchor;
   - an attribute with `compared == 0` is reported as consistent;
   - `list_jobs(limit=200)`;
   - one unrelated eligible job fails the derived check with `no_shared_entity`;
   - an Oracle call can outlive `wait_for`.

## 7. Nits (new, non-blocking)

1. **Oracle `CITY` is copied unchanged, 2400 of 2400 any-row.** CITY is a `QUASI_IDENTIFIER` with low sensitivity, so this is a policy choice, consistent with M2. Lane verification should confirm that the "every sensitive column" rule treats it that way, or the demo policy should generalise it.
2. **Postal codes use `generalize`** on Oracle and Mongo, so they are informational in the check. Any-row survivors are 0. That is fine; noted because the console shows it.
3. **`_STREET_LEAF` / `_POSTAL_LEAF` are English-only.** For example, Spanish `calle` / `codigo postal` under a `direccion` parent are not covered, although `classify_key` has Spanish rules. This matters for the four-country bank.

## 8. Artefacts (`~/.cache/dps-build/reports/probe-m3-G4/r2/`)

| File | What it is |
|---|---|
| `static_gates.log`, `unit_full.log`, `e2e.log`, `integration_all.log` | gate runs |
| `three_way_summary.txt` | value-free three-way result and per-field counts |
| `all_columns.txt` | B1 re-verification: every column and path, counts |
| `test_probe_same_target.py`, `same_target.txt` | B2-r2 with the unit fakes |
| `test_probe_same_target_api.py`, `same_target_api.txt` | B2-r2 through the API with real workers |
| `scan_log.py`, `log_scan.txt` | log scan, 0 hits |
