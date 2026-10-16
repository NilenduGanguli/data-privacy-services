# S8-idcheck review r1: the D-A identifier check reads every row on every engine

**Verdict: PASS.** No blocking finding in the work under review (`9fe68f4`, `794007e`, `762418d` on `fidcheck`).

- The S1-M3 engines r3 B1 blocker is closed. The check now reads every row of the target and of the source on PostgreSQL, Oracle and MongoDB. My live tests ran product jobs over 12 000 rows on Oracle and on MongoDB. In every evidence pack, the check's counts match counts I took independently.
- The three defects found by the earlier (interrupted) review r1 are closed:
  - Quoted mixed-case Oracle columns are now read.
  - Field names the selection validator refuses (Oracle `"Tax Id"`, MongoDB `tax-id`) no longer make finalize fail until the job is dead.
  - A MongoDB field name that contains a dot is now read.
- All gates are green.
- One real defect sits outside the changed code (N1, below). On an Oracle table whose primary key is `ENABLE NOVALIDATE` and holds duplicate keys, the check reports `whole_table` but has read only part of the table. I rate it non-blocking for this stream:
  - The defect is in the connector's `describe`, which existed before this branch.
  - It cannot hide a row that the run wrote, because the enabled constraint rejects new duplicate keys.
  - It does affect every Oracle run that reads such a table, so it should get its own follow-up.

| | |
|---|---|
| Date | 2026-10-15, 22:50-23:15 IST |
| Tree | `fidcheck` @ `762418d` (on `5cef802`): 3 commits, 13 files. `git status` was clean at the start and is clean at the end. |
| Commits | All 3 commits have author and committer `NilenduGanguli <nilendu.ganguli@gmail.com>`. None has a trailer. All are conventional commits. |
| Stack | Compose project `dps-wt-fidcheck`, profiles `sources` and `oracle`, started with `up -d --no-build`. Ports: app 8601, dps-db 5601, source-pg 5602, MinIO 9601, Oracle 1601, MongoDB 27601. The stack is **stopped** at the end (`--profile '*' stop`) and its volumes are kept. |
| Image = HEAD | For all 150 `dps/*.py` files, the sha256 inside the running `app` and `worker` containers equals the tree at `762418d` (both diffs are empty). |
| Repository files modified | None. The M3 gate's console tests overwrote 54 tracked PNGs in `docs/superpowers/build/m3-shots/` (a known side effect). I restored them with `git restore`; the tree was clean before my runs. |
| Outputs | Gate logs and JUnit XML: `~/.cache/dps-build/s8-idcheck/review-r1/`. Probes and their outputs: `~/.cache/dps-build/reports/S8-idcheck-review-r1/r1b_*`. The other `probe_r1_*` files in that folder come from the interrupted first attempt. |
| Values | Only counts, column names, codes and job ids appear here. Every probe value was generated. |

## 1. Gates (all run by me on this lane, through `./lane.sh`)

| Gate | Result |
|---|---|
| `ruff format --check dps tests tools` | 437 files already formatted |
| `ruff check dps tests tools` | All checks passed |
| `mypy dps` | Success: no issues in 150 source files |
| `pytest -q -m "not (db or pg_source or oracle or mongo or s3)"` (the part of the whole suite that needs no service) | **2 980 passed, 84 skipped, 0 failed** (11:03) |
| `DPS_TEST_REQUIRE_SERVICES=1 pytest -q -m "oracle or mongo"` | **190 passed, 1 skipped, 0 failed** (6:56). The skip is by design: MongoDB conformance has no composite key. |
| `DPS_TEST_REQUIRE_SERVICES=1 pytest -q -m "db or pg_source or s3"` | **439 passed, 1 skipped, 0 failed** (12:59). The skip is by design: a key-less PostgreSQL table is never read (`no_key`). |

- These three pytest runs together make up the whole suite. Tests that carry two markers ran twice.
- Every identifier-check test ran with services required:
  - `test_identifier_check_engines`: the 4 Oracle and MongoDB tests in the M3 gate, the PostgreSQL test in the M2 gate.
  - `test_identifier_check_runs`: both tests.
  - `test_synthesize_identifiers`: all 8 tests.
  - The unit tests: 63 in the run that needs no service.

## 2. Acceptance

**B1: every row on every engine.** I confirmed it in the code, in the gates and live.

The code (`identifier_check._every_row`):
- The check drives its own keyset paging. Every read starts after the last key it has read.
- A side ends only when a read after that key returns no row.
- A read that does not advance past its start key fails closed (`identifier_check_incomplete`). So does a row that comes back without its key.
- A key-less object is read in one pass and its row count must match the object's.

The connectors:
- Oracle and MongoDB now take a page's length and the next page's bound before they hand the page on.
- PostgreSQL already yielded a fresh list for each page.
- The conformance rule covering this passes on all three engines.

Live product runs, through the API on the image = HEAD, against scratch objects (all dropped afterwards):

| Run (CHECK_BATCH 5 000) | Pack: `scope` / `rows_read` (target, source) | Pack: `checked` / `in_source` / `issuable` | Independent count (target values / distinct target values in the source) |
|---|---|---|---|
| Oracle `CRM.S8V_MIX` (`"NationalId"`, `"acctNo"`), synthesize table, 12 000 rows | whole_table / 12 000, 12 000 | 12 000 / 0 / 0 on both columns; passed | 12 000 / 0 |
| ... then 1 row with source values planted, then an upsert | whole_table / 24 001, 12 000 | `NationalId` 24 001 / 1 / 1; `acctNo` 24 001 / 1 / 0; **review_required**, not passed | 24 001 / 1 |
| Oracle `CRM.S8V_UP` (`NATIONALID`, `ACCTNO`), the control: same two runs | the same | the same | the same |
| MongoDB `crm.s8v_prof` (`$['tax-id']`, `$.accounts[*].number`), 12 000 docs | whole_table / 12 000, 12 000 | 12 000 / 0 / 0 on both; passed | 12 000 / 0 |
| ... then 1 doc with source values planted, then an upsert | whole_table / 24 001, 12 000 | `tax-id` 24 001 / 1 / 1; accounts 24 001 / 1 / 0; **review_required** | 24 001 / 1 |

- Job ids: `j_01M50A8727A7C6HBW7JEJK84YJ`, `j_01M50A872D6VRHN0581RZJ0DQQ`, `j_01M50A8B7FVCSE3YR182YY7V36`, `j_01M50A8B7NARPSC5BJ1J7H668T`, `j_01M50AA4PQ2BWWQ0W66VD35EHW`, `j_01M50AA7RN7N9Q3R58QXPT1HMM`.
- My first MongoDB upsert attempt (`j_01M50A8M3VMHJ9PGR14MQP3S5K`) was a mistake in my probe, not in the product. I planted an ObjectId `_id` into a target keyed by integers, which made the target key-less. The upsert was then refused at prepare (`target_invalid`), which is correct. Its pack says `incomplete`, not passed. On the rerun I planted a same-type `_id`.

**The defects found by the interrupted review r1.**

- **Mixed-case Oracle columns.** A SQL read now lists no column: it narrows the shape to the key plus the identifier columns, and `qcol` quotes each name exactly as the catalogue spells it.
  - Live: the 12 000-row `"NationalId"`/`"acctNo"` run above.
  - Gate: the 20 000-row engine test (`"RowKey"`/`"acctNo"`/`"National Id"`).
- **Names the validator refuses.** These no longer reach the selection validator. As a fallback, `_best_effort` now records a `SelectionError` as `{"error": code}`.
  - Live: the `$['tax-id']` run above succeeded and produced a pack.
- **A MongoDB field with a dot.** Any field that is not a plain top-level name is read from whole documents.
  - Gate: the engine test with `acct.no` / `tax-id` over 20 000 docs.
  - Unit tests.

**Fail closed.**
- A SQL row returned without a requested column raises `IncompleteCheck`.
- A missing SQL column raises `IncompleteCheck`.
- An `{"error"}` result puts every written identifier column under review (`_store_evidence`).

**Logs.** I scanned the app and worker logs from the time I started the stack:
- 0 matches of probe values (account-number and 12-digit national-id patterns).
- 0 tracebacks, 0 `finalize_failed`, 0 `identifier_check_incomplete`.
- All 8 of my probe job ids were found, which shows the scan covered my runs (`r1b_logscan.json`).

## 3. Defect hunt: paging matrix

Probe: `r1b_test_paging_probes.py` → `r1b_paging_probes.jsonl`.

- Each case is run at the connector level, with `CHECK_BATCH` patched to 37. That puts page boundaries (both the connector's own pages and the check's outer reads) all through each object.
- Each case uses a 2 000-row target. Eight target rows, at positions in key order (first, around the first boundary, middle, last), carry real source values. The rest are values from never-issued ranges.
- The truth is an independent SQL or MongoDB count of: rows, non-null values, and distinct target values that also appear in the source.

| Engine: key | Result |
|---|---|
| Oracle: composite PK `(GRP, SUB)`. The first column repeats across pages; `SUB` includes binary-order traps (`a`/`B`/`Z`/`é`/`_`/leading space/`中`). | matches truth: 2 000 / 2 000, in_source 8 / 8 |
| Oracle: `TIMESTAMP(9)` PK, keys 1 ns apart (many keys share one microsecond) | matches |
| Oracle: `CHAR(10)` PK with values of variable length | matches |
| Oracle: `NUMBER(20,6)` PK with 20 significant digits (Decimal) | matches |
| Oracle: unique NOT NULL `NVARCHAR2` key, no PK (includes supplementary and BMP-high characters) | matches |
| Oracle: key-less (`ROWID`), every third row deleted (gaps) | matches: 2 000 / 2 000 |
| Oracle: key-less append floor (`key_floor` on `ROWID`), with and without freed space | falls back to the whole table (`rows_below_floor`), 2 000 and 1 300 = truth |
| Oracle: **`ENABLE NOVALIDATE` PK over duplicate keys** | **rows_read 1 948 of 2 000, `checked` 1 948, in_source 7 of 8, `scope: whole_table`** (N1) |
| MongoDB: string `_id`s with traps (`"10"` < `"9"`, case, `é`, emoji, `_`) | matches |
| MongoDB: int32 and Int64 `_id`s interleaved, including negatives | matches |
| MongoDB: random ObjectIds, inserted in shuffled order | matches |
| MongoDB: date `_id`s | matches |
| MongoDB: case-insensitive default collation over string `_id`s | matches |
| MongoDB: double, Decimal128 or mixed-type `_id`s | key-less (the `_id`-bounds rule); one pass checked against the count; matches |
| MongoDB: one double among int `_id`s (the sample missed it) | `key_type_mixed`, so it fails closed (no scope claimed) |
| PostgreSQL: composite `(int, text)` with the same traps | matches |
| PostgreSQL: `numeric(20,6)` | matches |
| PostgreSQL: key-less target | `no_key`, so it fails closed (N2) |

Oracle refuses a `TIMESTAMP WITH TIME ZONE` primary key (ORA-02329), so that key type cannot occur.

## 4. Findings

None is blocking.

### N1. An Oracle key that does not enforce uniqueness: `whole_table` claimed on a partial read

This is a real defect. It existed before this branch and lies outside the diff.

- **What happens.** `OracleConnector._describe_sync` takes a key from `_KEYS_SQL`, which filters on `status = 'ENABLED'` but not on `validated = 'VALIDATED'`. An `ENABLE NOVALIDATE` primary key or unique constraint, backed by a non-unique index, can sit over rows with duplicate keys.
- **Why rows are skipped.** Keyset paging (`key > :af`) skips every row whose key equals the last key of a page. The check's outer reads use the same predicate, and so does its rule that "an empty read after the last key proves the end".
- **The probe result.** On a 2 000-row target, 52 rows were never read. One planted real identifier pair was missed. The result still said `scope: whole_table` with `rows_read.target` 1 948, and no error.

What it can and cannot reach:
- **Rows the run wrote are never skipped.** An enabled constraint rejects new duplicate keys, and a target that DPS creates gets a validated primary key.
- **It can skip:**
  - rows that were already in an existing target;
  - source rows, which lowers `in_source`.
- **The same keyset paging (`read_partition`) is what every Oracle run uses to read a source**, so a run reading such a table would skip the same kind of rows. I did not verify this live.

How to fix it (follow-up):
- In `_KEYS_SQL`, add `AND c.validated = 'VALIDATED'`. Consider also refusing UNUSABLE unique indexes in `_UIDX_SQL`, which has no status filter.
- Optionally, make the check compare the target's `rows_read` with `count()` and fail closed on a mismatch. The target is quiescent after the run. The source is not, so a count check there would raise false reviews on a busy source.

### N2. A PostgreSQL key-less target always ends in review

This existed before the fix and the behaviour is unchanged.

- `read_partition` raises `no_key`, so the check records `{"error": "no_key"}` and every written identifier column is reviewed. This fails closed, which is correct.
- The consequence: a table load into an existing PostgreSQL table without a key can never pass D-A. It is worth a line in HANDOFF.

### N3. A failed post-write sample leaves the pack passed

This is a pre-existing gap. `762418d` widened its trigger slightly, but there is no practical path to it.

- `_best_effort` now also records a `SelectionError` from `_post_sample` and `_clear_ledger`.
- `verification_outcome` reads only `kept_column_hits`. So `post_sample: {"error": ...}` leaves `passed: true`, and the D-A kept-column check (`synthetic_kept`) is silently missing. This was already the case for a `ConnectorError`.
- `_post_sample` reads with `Selection()`, so I found no realistic path to a `SelectionError` there. No new fail-open in practice.
- Suggestion: treat `post_sample.error` like the identifier check's error, so that it makes the result `review_required`.

### N4. The append floor on a `ROWID`-keyed table rarely applies (information only)

- Conventional inserts reuse free space below the floor's ROWID, so the check usually falls back to the whole table (`rows_below_floor`). Both of my probes did.
- This is safe; it just means the floor saves no reading in that case.

### N5. A sibling of the fixed bug in the consistency check (not verified by me)

- The implementer noted (§9.6) that `consistency.py:559,567` builds `Selection(columns=[join.name])` from catalogue names, the same pattern this branch removed from the identifier check.
- A quoted mixed-case Oracle join column is probably dropped from the consistency read.
- This is outside D-A and outside this review's scope. It is worth its own check.

## 5. State left behind

- Lane stack `dps-wt-fidcheck` stopped (`docker compose --profile '*' stop`), volumes kept. The Oracle container keeps its data while stopped.
- No scratch objects are left. Confirmed:
  - 0 `DPS_T_*` Oracle users;
  - 0 `S8V_*`/`P_*` tables in `CRM`/`CRM_MASKED`;
  - 0 `dps_t_*` MongoDB databases;
  - 0 `s8v*` collections;
  - 0 `t_*` PostgreSQL schemas.
- The demo sources are as the gates' tests leave them: Oracle `CRM.CUSTOMERS` and `CRM.KYC_DOCUMENTS` and MongoDB `crm.profiles` hold 200 rows each (the M3 gate's tests reseed them; a known effect), and PostgreSQL `bank.customers` holds 20 000. The implementer left them at 20 000; reseed with `tools/seed_sources.py` before any 20 000-row demo.
- The worktree is clean at `762418d`. I pushed nothing and committed nothing.
