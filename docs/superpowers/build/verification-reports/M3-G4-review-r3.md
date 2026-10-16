# M3 G4 review, round 3: Tasks 15, 16, 17 and 19 after fix r2 (branch `m3`, worktree `dps-wt-m3`)

Reviewer: independent. I did not rely on `M3-G4-impl.md` or either fix report. I verified by reading the code and running it.

- **HEAD reviewed:** `ac710bf` (fix r2, on top of `188914f` fix r1 and `b725cc8` impl).
- **Working tree:** clean before and after. I modified no repository files. The probes ran from outside the repository (`pytest -p tests.conftest -p tests.e2e.conftest <probe>`), so nothing was copied into `tests/`.
- **Fix commit:** author `NilenduGanguli <nilendu.ganguli@gmail.com>`, conventional message, no trailers.
- **Artefacts:** `~/.cache/dps-build/reports/probe-m3-G4/r3/`.

## Verdict: FAIL (2 blocking)

| # | Finding | Kind |
|---|---|---|
| **B3-r3** | PostgreSQL `{name: T}` and `{owner: "public", name: T}` are the same table but get different `target_key`s. Two refreshes of one table therefore pass the derived check "cross-system", and that result is stored as `last_check.passed = true`. Reproduced through the API with real workers. | Contradicts contract §6.5 (as amended in fix r2); false verdict persisted |
| **B4-r3** | With `DPS_LOG_LEVEL=DEBUG`, pymongo's command logger writes source customer values into the worker and app logs. The M2 no-values-in-logs gate test failed in one of my two full runs for this reason. | Leak, and a flaky gate |

**B2-r2 itself is fixed** for the case it named: the same spec submitted twice, and two connection records for one endpoint.

## 1. Gates (run by me)

| Gate | Result |
|---|---|
| `./lane.sh ruff format --check dps tests tools` | 298 files already formatted |
| `./lane.sh ruff check dps tests tools` | All checks passed |
| `./lane.sh mypy dps` | Success: no issues found in 109 source files |
| `./lane.sh pytest -q` (dps-db, sources, oracle and mongo up), run 1 | **1 failed, 2046 passed, 11 skipped** (346 s). The failure is `tests/test_no_values_in_logs_m2.py::test_a_full_job_logs_no_values`. See B4-r3. Log: `r3/unit_full.log` |
| `./lane.sh pytest -q`, run 2 | 2047 passed, 11 skipped (342 s). The failure is timing-dependent. Log: `r3/unit_full_rerun.log` |
| The failing test alone | 43 passed |
| `DPS_TEST_REQUIRE_SERVICES=1 pytest -s` over the G4 test files (list below) | **49 passed** (143 s), no skips. Log: `r3/e2e.log` |
| Frontend | Not touched by G4, so not run |

G4 test files in that run: `tests/e2e/test_m3_three_way.py`, `tests/e2e/test_m3_resume_after_kill.py`, `tests/e2e/test_seed_shared_ids.py`, `tests/connectors/test_mongo_pipeline.py`, `tests/connectors/test_lookup_values_m3.py`, `tests/connectors/test_consistency.py`, `tests/api/test_groups_consistency_api.py`, `tests/test_seed_model.py` and `tests/test_seed_sources.py`.

**Value-free three-way result** (`r3/three_way_summary.txt`):
- The check passed with 500 sampled, and 3/3 pairs matched 500 at coverage 1.0.
- Forms: PostgreSQL `numeric`, MongoDB `prefixed_digits` (width 8, prefix length 5), Oracle `numeric`.
- Counted attributes agree on every compared row: EMAIL, PERSON_NAME, PHONE, NATIONAL_ID and STREET_ADDRESS 500/500, PAN_IN 310/310.
- Informational attributes: DATE_OF_BIRTH (`date_shift`, 0/500) and POSTAL_CODE (`generalize`).
- The drift member reports `join_form_mismatch` and `method_mismatch`.

**Source-vs-target comparison.** This is the test's own independent `SENSITIVE` list, with counts only. Every column and leaf path in all three targets is `[2400, 0, 0]`: rows, same-row survivors, any-row survivors.

**Log scan** (`r3/log_scan.txt`). I scanned `e2e.log`, `unit_full.log` and the API-probe log for 3,000 seeded customers × 8 fields. Every count is 0. These runs logged at INFO; see B4-r3 for DEBUG.

**Containers.** I used only compose project `dps-wt-m3` (`--profile sources --profile oracle`) and took it down afterwards with `docker compose --profile sources --profile oracle down`. I touched no other project.

## 2. Blocking findings

### B3-r3: one PostgreSQL table, two spellings, two "distinct targets"

**Where.** `dps/layers/connectors/consistency.py` `target_key`:
```python
name = obj.fqn().casefold() if fold else obj.fqn()
```
`ObjectRef(name="t").fqn()` is `"t"`, while `ObjectRef(owner="public", name="t").fqn()` is `"public.t"`. Every PostgreSQL code path resolves no owner to `public`:
- `targets._owner` / `_same_object` / `_object_ref`;
- `postgres.py` lines 219, 437, 661, 717, 814 and 861.

So both spellings write and read the same table. The repository's own identity rule for "same object", `targets._same_object`, already treats them as equal. `target_key` does not.

Oracle is not affected, because `oracle_sql.qualified` requires an owner. MongoDB is not affected either, because the owner must be `None`.

**Evidence 1, unit fakes** (`r3/test_probe_owner_alias.py`, `r3/owner_alias_unit.txt`). The setup is the `World` of `tests/connectors/test_consistency.py` with the MongoDB member drifted on every row, and real `Connection` records with hosts.
- `target_key` of the two spellings: **not equal**.
- PostgreSQL ↔ drifted MongoDB: `passed=False`, 0/1.
- `pairs=[j_pg (public.payments), j_pg_refresh (payments)]`: **`passed=True`**, 1/1 at coverage 1.0. Objects: `['public.payments', 'payments']`.
- `derive_members` keeps both PostgreSQL jobs: `['j_ora', 'j_mdb', 'j_pg', 'j_pg_refresh']`.
- The derived check over a group that holds only the two PostgreSQL jobs returns **`passed=True`**, 1/1. Contract §6.5 requires `409 member_not_ready` here.

**Evidence 2, through the API with real workers on the lane's PostgreSQL** (`r3/test_probe_owner_alias_api.py`, `r3/owner_alias_api.log`).

Setup:
- Source: seeded schema `probe_r3` (400 rows), discovered and accepted.
- One job group.
- Run 1 with target `{"name": "probe_r3_cust"}`: 202, succeeded, 400 rows.
- Run 2, the refresh, with target `{"owner": "public", "name": "probe_r3_cust"}`: 202, succeeded, 400 rows.
- `public.probe_r3_cust` holds 400 rows, so this is one table.

Results:
- `pairs=[run1, run2]`: **200, `passed=True`**, 1/1, coverage 1.0, members `['probe_r3_cust', 'public.probe_r3_cust']`, no problems.
- `GET /job-groups/{id}` then shows **`last_check.passed == true`**. The Task 18 console panel reads this.
- Derived (no pairs): **200, `passed=True`**, 2 members, 1/1. This is the case that contract §6.5 (fix r2 amendment) says must be `409 member_not_ready` ("fewer than two distinct targets").
- The probe dropped its tables and schema afterwards.

**Why it blocks.** It is the B2 / B2-r2 false verdict again, persisted as group state, through an ordinary API spelling.
- The fix r2 amendment to contract §6.5 says: "a repeated target column, whether one job named twice or two jobs of the group that wrote the same target, is `422 member_duplicate`". It lists host aliases as the only unresolved case.
- The deviation log, fix round 2, says "So two connection records for one database name one target".
- Here the behaviour contradicts both.

**Fix (narrow, test-first):**
1. In `target_key`, normalise the object per engine before keying. PostgreSQL: `ObjectRef(owner=obj.owner or "public", name=obj.name)`. Reuse `targets._owner` or `_object_ref`; do not add a third rule.
2. Add unit tests over both explicit `pairs` (422 `member_duplicate`, nothing opened) and `derive_members` / the derived check (collapse; 409 when only one table remains).
3. **PostgreSQL case folding is wrong in the other direction (not blocking, fix at the same time).** `quote_ident(..., "postgres")` keeps case, so DPS always quotes PostgreSQL names. `public."Customers"` and `public.customers` are two tables, but `target_key` case-folds PostgreSQL object and path, so it refuses a legitimate pair with 422. Fold only for Oracle, whose `quote_ident` upper-cases.
4. Correct the docstring and the contract wording "PostgreSQL and Oracle fold unquoted identifiers" accordingly.
5. Document the remaining PostgreSQL alias: `params.database` unset (asyncpg then uses the login's name) versus set to that name.

### B4-r3: pymongo command logging writes customer values at DEBUG; the M2 log gate is flaky

**Where.** `dps/observability.py` `configure_logging`:
- It calls `logging.basicConfig(level=numeric, ...)` with the configured level.
- It caps only `_THIRD_PARTY_LEVELS`: pypdf, pdfminer, pdfplumber, PIL, reportlab, faker, httpx and httpcore.
- pymongo 4.18 has structured loggers `pymongo.command`, `pymongo.connection`, `pymongo.serverSelection` and `pymongo.topology`. At DEBUG, `pymongo.command` logs every command and its reply document (truncated to 1000 characters by default).
- `dps/worker.py:562` and `dps/app.py:463` call `configure_logging(settings.log_level)`, and `DPS_LOG_LEVEL` is an operator setting.
- MongoDB entered the worker pipeline in Task 19.

**Evidence** (`r3/probe_pymongo_debug_logs.py`, `r3/pymongo_debug_logs.txt`). The probe calls DPS's own `configure_logging` and the DPS `MongoConnector`: it describes and samples 50 documents of `crm.profiles`, captures the root log stream in memory and prints counts only. I discarded stderr so no value was printed.

| Level | Distinct string values read | Found in logs | Log bytes | "Command succeeded" events |
|---|---|---|---|---|
| INFO | 383 | 0 | 0 | 0 |
| **DEBUG** | 381 | **11** | 36,658 | 10 |

The same logger carries `find` filters and `$in` lists for selections, lookups and resume bounds, and bulk-write commands. So source values, and in the run path masked target values, reach the logs too.

**Gate impact.**
- `tests/test_no_values_in_logs_m2.py::test_a_full_job_logs_no_values` runs DPS logging at DEBUG ("as in production"). It asserts that every JSON record uses only allowed fields.
- In run 1 it failed on a `pymongo.topology` "Server heartbeat started" record: fields `topologyId`, `serverHost`, `serverPort`, `driverConnectionId`, and so on, with no customer value.
- The source is the session-scoped `mongo_client` fixture (`tests/fixtures/m3_services.py`). It stays open for the whole session and heartbeats every 10 s, so whether a heartbeat lands inside that test's window is a matter of timing.
- That makes the full-suite gate order- and timing-dependent: it failed in 1 of 2 runs. Earlier rounds were green by luck.

**Why it blocks.**
- It is a value leak into logs. Programme rule: never log a customer value, and the M2 log test establishes that DEBUG is in scope.
- It also makes a required gate flaky.

**Fix (test-first):**
1. Add `("pymongo", logging.WARNING)` to `_THIRD_PARTY_LEVELS`. Its child loggers inherit the cap.
2. Check `oracledb` and `asyncpg` the same way, and cap them if they emit anything at DEBUG.
3. Add a test that runs `configure_logging("DEBUG")`, reads `crm.profiles` through `MongoConnector` (sample, keyset page, `lookup_values`, one bulk write), and asserts:
   - 0 read values appear in the captured stream;
   - no record from a `pymongo.*` logger appears.
4. Optionally, make the e2e log assertions run at DEBUG too.

## 3. B2-r2 re-verification (fixed for the case reported)

**Code.** `_validate` resolves every member's `target_key(conn_id, connection, obj, path)` before any connector opens. It refuses a repeat with `422 member_duplicate`. A self-join of one target over two paths stays allowed.

`derive_members` collapses eligible jobs per `target_key(...)` (without the path) to the newest by `(created_at, id)`, keeping list order, and returns 409 when fewer than 2 targets remain.

The endpoint is: engine, case-folded host, port (the engine default when unset), database and service name. A connection without a host is keyed by its id, as documented.

**Tests.** I read all three new tests; each would fail on the r1 code.
- `test_two_jobs_writing_one_target_are_one_member`: explicit pairs give 422 with nothing opened; derived keeps the newest and still fails over 3 pairs; Mongo-only gives 409.
- `test_two_connection_records_for_one_database_are_one_target`.
- The API test runs the real check, with `__kwdefaults__` patched to a recording opener. It shows 422 twice, `last_check` unchanged, and audit `failed 200`, `rejected 422`, `rejected 422`.

These tests pass in my e2e run. The B2-r2 scenario itself (the same spec twice; MongoDB `{name}` targets) is closed. B3-r3 is a different spelling of the same class.

**Other properties re-checked** (`consistency.py` changed only in `_validate`, `derive_members` and `target_key`):
- `target_key` reads only `params`: no credentials and no `secret_ref` contents.
- Results still carry only ids, object names, paths, methods, a params hash, forms and counts.
- Tenant scoping in the route is unchanged: `get_group(tenant)`, `list_jobs(tenant, group_id)`, `get_connection(tenant)`, `save_last_check` by tenant and id.
- Nothing is stored or opened on a refusal.

## 4. Deviation log judgements

| Entry | Judgement |
|---|---|
| T15: seed model keeps M2's four-country bank; `NATIONAL_ID VARCHAR2(18)`; `seed_sources` touch points; return types; GB notes rule; `m3_seed_ids` schema | **Accepted** (unchanged since r1/r2; the seed tests pass). |
| T16: typed signatures; `consistency_checked` event name; free-text columns never join or compare; validation before open; eligible-only derivation; per-engine `lookup_values` | **Accepted.** |
| T17: `save_last_check` in `dps/groups.py`; every refusal audited; fixed error sentences; per-id loading; `kek_of`; migration list touch point | **Accepted.** Migration 020 is additive and value-free. |
| T19: path-based planning (`column_at`); created Mongo targets take no DDL types; ObjectId append with `generate`; Oracle pool without a job id; e2e conftest; independent `SENSITIVE` list; drift member on MongoDB; resume via in-process submit and worker subprocesses | **Accepted.** Programme §2.2 "every write mode" coverage gap is carried in §5 item 1. |
| G4 fix r1 / B1 (`classify_path`) | **Accepted** (as in r2). |
| G4 fix r1 / B2, corrected in fix r2 | **Accepted** as corrected. |
| **G4 fix r2 / B2-r2** ("So two connection records for one database name one target"; host aliases the only limit) | **Rejected as complete.** The PostgreSQL owner default is an unresolved alias (B3-r3), and case folding is wrong for PostgreSQL (B3-r3 fix item 3). |
| Open: `surrogate` in obfuscate mode passes submit and fails at prepare | **Accepted** as open; route to WP-H/F. |

## 5. Carried, non-blocking (must not be dropped)

1. **Resume-after-kill coverage.** Programme §2.2 G4 asks for "resume-after-kill on Oracle and Mongo for every write mode".
   - The repository tests cover only `in_place` and `append`.
   - The `table` modes (`truncate_insert`, `upsert`) are proven only by round 1's probe, `probe-m3-G4/test_probe_table_resume.py`. Write and resume code is unchanged since that probe: `git diff b725cc8 ac710bf` touches only `consistency.py`, `structural.py`, `discovery.py`, tests and docs.
   - Add those cases, plus Oracle `append` with a numeric offset, to `tests/e2e/test_m3_resume_after_kill.py` before lane verification.
2. The e2e log assertion covers only the worker logs, not the in-process API. It also runs only at INFO; see B4-r3.
3. Round-1 nits:
   - DOB differs across systems because of the `date_shift` anchor;
   - an attribute with `compared == 0` is reported as consistent;
   - `list_jobs(limit=200)` in the route: a group with more than 200 runs derives from the newest 200 only;
   - an unrelated eligible job fails the derived check with `no_shared_entity`;
   - an Oracle call can outlive `wait_for`.
4. Round-2 nits:
   - Oracle `CITY` is copied unchanged (policy);
   - postal codes use `generalize`;
   - `_STREET_LEAF` / `_POSTAL_LEAF` are English-only;
   - the run-time drift detector's T0 tier uses `classify_key`, so a drifted leaf gets no parent context.

## 6. Nits (new, non-blocking)

1. **Explicit `pairs` may name a superseded job.** If run 1 wrote target A and a later run 2 rewrote it, `pairs=[run1, other]` validates against run 1's snapshot and methods. Only derived mode picks the newest writer. The data comparison still catches drift in the join and in counted attributes, so this is not a false pass. Still, `methods_agree` / `params_hash` then describe a spec that no longer matches the target. Consider refusing (409) or flagging (`member_superseded`) a member that is not the newest writer of its target.
2. **MongoDB connections that use a URI secret have no `params.host` and are keyed by id.** That is documented, but it is the production path per `MongoConnector.from_secret`, so in production two records for one cluster are always "distinct". Conversely, two records whose stale `params.host` matches but whose URIs differ would be refused. Consider keying on the URI's hosts and database, parsed at load time without reading the credentials.
3. **The derived collapse is per target without the path.** That is correct today, because derived members always join on the same entity's preferred path. Keep it in step if derivation ever picks different paths per job.

## 7. Artefacts (`~/.cache/dps-build/reports/probe-m3-G4/r3/`)

| File | What it is |
|---|---|
| `static_gates.log` | format, lint and mypy |
| `unit_full.log`, `unit_full_rerun.log` | full suite, run 1 (1 failed, B4-r3) and run 2 (green) |
| `e2e.log`, `three_way_summary.txt` | G4 integration and e2e runs (49 passed); the value-free result and per-column survivors |
| `log_scan.txt` | seeded-value scan, all 0 |
| `test_probe_owner_alias.py`, `owner_alias_unit.txt` | B3-r3 with the unit fakes |
| `test_probe_owner_alias_api.py`, `owner_alias_api.log` | B3-r3 through the API with real workers |
| `probe_pymongo_debug_logs.py`, `pymongo_debug_logs.txt` | B4-r3, counts only |
