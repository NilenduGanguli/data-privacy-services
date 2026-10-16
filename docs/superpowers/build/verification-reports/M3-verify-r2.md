# M3 lane verification, round 2

- Lane: `dps-wt-m3`, branch `m3`. Verified HEAD `da470f9` (fix round r1: `de757f5`, `da470f9`).
- Date: 2026-10-14. Verifier: independent. No code was changed.
- Only commit: `13875fd docs: M3 verification screenshots` (92 PNGs in `docs/screenshots/m3/`, explicit pathspec).
  Author NilenduGanguli <nilendu.ganguli@gmail.com>, no trailers.
- Probes, logs and outputs: `~/.cache/dps-build/reports/m3-verify-r2/` (`probes/` holds every script and its `.out`).
- Stack: `docker compose --profile oracle --profile sources --profile docs` built from HEAD. I added the docs
  profile so that the OCR-mock tests run under REQUIRE=1 as well. It is now `down -v`: 0 containers and 0 volumes
  are left.

## Verdict: PASS (0 blocking)

All five carried findings are fixed. I verified each one offline and, where it applies, end to end on the lane's
containers. Every DoD gate, smoke run, benchmark and browser check is green. The §6 source-vs-target comparison
finds 0 survivors on every sensitive column of all 19 targets on the lane. The log, metrics and audit scans
find 0 values and 0 key strings. The non-blocking findings are listed at the end.

## Carried findings, re-checked

### B6: query re-mapping in `selection.py`. FIXED

- **Offline** (`probes/b6_sel.py`, `.out`): all 9 carried shapes are refused with `selection_query_alias`,
  0 accepted.
  - PostgreSQL (4): column-list alias ×3 and UNPIVOT.
  - Oracle (5): UNPIVOT with `:s`, `SELECT *` UNPIVOT, MATCH_RECOGNIZE, PIVOT and a column-list alias.
- **Adversarial shapes** (15 per engine):
  - UNION, UNION ALL and EXCEPT get `selection_not_select`.
  - LATERAL, NATURAL JOIN with a re-mapping subquery, a recursive CTE that shadows the object, JSON_TABLE,
    CROSS APPLY, a nested UNPIVOT in an IN subquery and a one-column alias list all get
    `selection_query_alias`.
  - MODEL gets `selection_parse_error`.
  - A derived table aliased with the object's own qualifier (`FROM crm.customers JOIN (SELECT … email AS
    city …) customers`) is **accepted** by the validator. This is a gap, but it does not leak:
    - Executing it directly fails with ORA-00918 on Oracle and SQLSTATE 42712 on PostgreSQL
      (`probes/dup_alias_db.py`).
    - Through the API on both engines, the job ends `failed` with `source_error` and no target is created
      (`probes/dupq_e2e.py`).
    - Non-blocking, see N-r2-2.
- **End to end** (`probes/b6_e2e.py`, `.out`):

  | Shape | Result |
  |---|---|
  | PostgreSQL column-list alias, column-list alias with `SELECT *`, UNPIVOT | 422 `selection_query_alias`, target not created |
  | Oracle UNPIVOT, UNPIVOT with `SELECT *`, MATCH_RECOGNIZE, PIVOT, column-list alias | 422 `selection_query_alias`, target not created |
  | Discovery with the PostgreSQL alias list, the Oracle UNPIVOT and MATCH_RECOGNIZE | job `failed` with `selection_query_alias` |

  In round 1 the Oracle UNPIVOT run succeeded with 20000/20000 target values equal to a source e-mail.
- Contract §5.2 is amended (2026-10-14) to match.

### B3-r3: consistency `target_key` owner normalisation. FIXED

- **Offline** (`probes/b3_key.py`):
  - PostgreSQL with no owner and with `public` give the same key.
  - Two connection records for one endpoint (host case, default port) give the same key.
  - `public."Cust"` and `public."cust"` stay distinct, and so do `$.Email` and `$.email`.
  - Another schema is distinct.
  - Oracle names are still case-folded.
- **API with real workers:** I re-ran the round-3 reviewer's probe (`probes/b3_api.out`).
  - Two refreshes of `public.probe_r3_cust` (400 rows each).
  - Explicit pairs return **422 `member_duplicate`**, and nothing is stored as `last_check`.
  - The derived check returns **409 `member_not_ready`**.
  - Before the fix, both returned 200 `passed=true`.
  - The temporary test file was removed and the probe's tables and schema were dropped.

### Nested drift under a scalar-sampled field (`batch.py`). FIXED

`probes/drift.py`. Survivors per case:

| Case | Survivors |
|---|---|
| Unplanned, `detect` | 0 |
| Unplanned, `redact` | 0 |
| `notes` planned `det_token`, every mode | 0 |
| `unclassified=keep` (explicit) | 3 |
| Explicit text `keep` on `notes` (residual N-new-3, the reviewer's choice) | 3 |

### Bracketed field names (`docpaths`). FIXED

- `probes/brackets.py` covers `iban[1]`, `a[0]x`, `acct['no[2]']`, `acct['list[*]']` and `arr[*]['k[3]']`.
- Paths are segment-aware.
- **0/100 survivors** under both `detect` and `keep`, and `values_by_column` = 20 for each path.

### B4-r3: pymongo DEBUG logging. FIXED

- `_THIRD_PARTY_LEVELS` caps pymongo at WARNING.
- Probe `probes/b4_drivers.py`: `configure_logging("DEBUG")` plus a root capture handler, then a Mongo find,
  `insert_many`, a duplicate-key insert (E11000), an asyncpg read and an oracledb read.
  - **0 records** from the pymongo, asyncpg and oracledb loggers.
  - **0 of 406 values** in the captured logs.
- Controls: a DPS DEBUG record is captured, a pymongo DEBUG record is suppressed and a pymongo WARNING is
  captured.
- asyncpg and oracledb (thin mode) emit nothing at DEBUG.
- `test_no_values_in_logs_m2.py` passed in both full runs.

## Gates

| Gate | Result |
|---|---|
| `./lane.sh ruff format --check dps tests tools` | 303 files already formatted |
| `./lane.sh ruff check dps tests tools` | All checks passed |
| `./lane.sh mypy dps` | Success: no issues in 109 source files |
| Full `DPS_TEST_REQUIRE_SERVICES=1 ./lane.sh pytest -q -rs` (run 1) | **1 failed, 2094 passed, 5 skipped** (433 s). The failure is `tests/test_ndjson.py::test_one_audit_event_per_stream`, a pre-existing M1 flake (N-r2-1). The file then passed 5 of 5 times in isolation |
| Full `./lane.sh pytest -q` (run 2, the plain gate) | **2095 passed, 5 skipped** (439 s) |
| Skips | Mongo has no composite keys (capability) ×1; tesseract binary ×2; `presidio_analyzer` / spaCy for the `[ner]` extra ×2 (heavy model extra, not installed in this lane) |
| DoD 4: `-m "oracle or mongo"` | **162 passed, 1 skipped** (capability). `test_oracle_clob_astral_roundtrip`, `test_oracle_char_key_paging` and `test_oracle_timestamp9_key_paging` are selected. Also included: `test_console_m3_wizard.py` 3 passed, `test_m3_resume_after_kill.py` 4 passed, `test_m3_three_way.py` 1 passed |
| DoD 5: conformance on PostgreSQL, Oracle and Mongo | **29 passed, 1 skipped** (Mongo composite keys), via `tests/connectors/test_conformance_{postgres,oracle,mongo}.py`. The DoD's literal command (`pytest tests/connectors/conformance.py -m …`) collects 0 tests, because that file is a harness, not a `test_` module (N-r2-4) |
| DoD 6: `-m "db or pg_source or s3"` | **198 passed** |
| Frontend | `npm ci` ok; `npm run typecheck` ok; `npm test` node 42/42 and vitest 91/91 (4 files); `npm run build` ok (463 ms) |

## Stack, seed, smoke, bench (DoD 2, 3, 7, 9)

- **`up --build -d`:**
  - All services were healthy.
  - `migrate`, `minio-init`, `seed-sources` and `demo-secrets` exited 0.
  - `/readyz` returned `ready: true`, with db up (migrations verified), jobs ready and ocr up. NER is
    patterns-only, which is expected.
- **Seed (`--target all --rows 20000 --seed 7`):**
  - First run: PostgreSQL bank already seeded with 20000 customers; `CRM.CUSTOMERS` 20000,
    `CRM.INTERACTIONS` / `ADDRESS_HISTORY` / `CUSTOMER_EVENTS` 40000, `KYC_DOCUMENTS` 20000,
    `LOANS.APPLICANTS` 5000, `crm.profiles` 20000.
  - Second run: Oracle and Mongo print `unchanged`. PostgreSQL prints "already seeded".
  - I reseeded after pytest, back to 20000.
- **`tools/smoke_test.py`:** all **34** checks passed. With the docs profile up, the OCR checks run instead of
  skipping.
- **`tools/smoke_test.py --m3`:** all **41** checks passed, including:
  - `m3 consistency 3/3`: HTTP 200, 3/3 pairs consistent, 500 sampled, min coverage 1;
  - `dps_llm_raw_pii_sent_total 0`.
- **Benchmarks** (`--rows 20000 --parallelism 4 --partition-rows 5000`, 1 worker):

  | Engine | Throughput | Time | HANDOFF figure |
  |---|---|---|---|
  | Oracle | **903.1 rows/s** | 22.2 s | 942 |
  | Mongo | **863.2 rows/s** | 23.2 s | 860 |

  The bench JSON files were moved out of `docs/technical/bench/`, so the tree is unchanged.

## Browser walk (private Playwright 1.55 at `pw-m3`, `probes/walk.py`, output `walk.json`)

The walk ran against `http://127.0.0.1:8413`.

1. **New job group dialog.** I created group `m3-verify-afa939` (`grp_01M4WK9NXBH2N6TMJBEASB7Z63`).
   - The `.dpskey` was downloaded once into scratch and deleted after the scans.
2. **Three wizard runs**, each through connect, test connection, select, check, discover, accept, "Copy to a
   table", then reuse the group key (matched), ticket, run, open job and evidence pack (sections 1 to 5).

   | Engine | Source | Target | Result |
   |---|---|---|---|
   | PostgreSQL | `bank.customers` | `bank_uat.customers_vafa939` | succeeded, 20000 rows |
   | MongoDB | `crm.profiles` | `crm_uat.profiles_vafa939` | succeeded, 20000 documents |
   | Oracle | `CRM.CUSTOMERS` | `CRM_MASKED.CUSTOMERS_VAFA939` | succeeded, 20000 rows |

   The Mongo select step shows the JSON filter with the operator allow-list.
3. **Jobs page (`?group=`).**
   - 3 system cards and a key node with the entity scope.
   - Run consistency check: chip `passed`, **"3 / 3 consistent"**, 3 pairs, 0 bad pairs, 500/500 per pair.
   - The verdict survives a reload. The jobs table shows 3 group badges.
4. **Oracle job detail.**
   - The crumb names the job group, and the subtitle names the other two targets.
   - The "Cross-system joins" tile reads **3 / 3 consistent**.
   - "Open consistency check" lands on `#group`.
5. **Console errors and layout.**
   - **0 console errors and 0 page errors** over 28 pages: Overview, Data sources, Policies, Reports, every
     wizard step, details and evidence.
   - **No horizontal scroll at 390 px** on any of them.
   - **0 of 150 seeded names and e-mails** visible on any page.
6. **Mockup comparison** (I looked at crops of 07, 03, 06 and 10 beside their mockups):
   - The group card matches mockup 07: key node, wires, three systems, the 3/3 verdict with pairs, and the
     coverage table.
   - The job detail matches mockup 03: crumb, subtitle, buttons, stepper, classification table, partitions,
     Evidence so far and the joins tile.
   - The discover step matches mockup 06: heat-map, needs-a-decision, review table and job specification.
   - The evidence pack matches mockup 10.
   - The intentional differences already logged are unchanged: a coverage table instead of masked sample rows;
     `NOTES` instead of `RM_NOTES`.
   - Cosmetic issues are listed in N-r2-3.
7. **Screenshots** were committed in `13875fd`. The round-1 untracked set was moved to
   `m3-verify-r1/screenshots-r1/`.

## §6 proofs: source vs target, counts only

### The three walk targets

Methods come from each job's accepted snapshot. "row_same" counts rows where the target value equals the source
value on the same row.

| Target | Join | Rows src/tgt | row_same on every sensitive column | Set hits |
|---|---|---|---|---|
| Oracle `CUSTOMERS_VAFA939` | `LAST_CONTACT_AT` | 20000/20000 | **0** on CUSTOMER_ID, FULL_NAME, NATIONAL_ID, TAX_ID, EMAIL, MOBILE, DOB, ADDR_LINE1, PIN_CODE, NOTES | 0 everywhere, except CUSTOMER_ID 2 (FPE digit collisions with other ids) and DOB 12665 (date_shift domain) |
| PostgreSQL `customers_vafa939` | composite of 8 kept columns, unique 20000/20000 (`probes/pg_rowjoin.py`) | 20000/20000 | **0** on all 8 sensitive columns | as for Oracle |
| Mongo `profiles_vafa939` | `_id` | 20000/20000 | **0** on all 10 sensitive paths | 0, except `customerId` 2 and `dob` 12649 |

Kept columns are 100% equal, as expected. CITY being kept is G6-N7, which is unchanged.

### Every other M3 target on the lane

`probes/compare_all.py`, output `compare-all.json`. For every target, sensitive columns have **0 unexpected
survivors**: row_same is 0, and set hits are 0 outside the FPE id and DOB domains.

| Engine | Targets |
|---|---|
| Oracle | `CUSTOMERS_BENCH`, `_CONSOLE`, `_E2E`, `_GRP_3642B9`, `_SMOKE` |
| Mongo | `profiles_bench`, `_console`, `_drift`, `_e2e`, `_grp_3642b9`, `_smoke` |
| PostgreSQL | `bank_uat.customers_smoke`, `_grp_3642b9`, `_console`, `_bench_obfuscate`, `m3e2e_uat.customers_e2e` |

### Other proofs

- **D3** (`probes/d3.py`, using temporary source+target connections on the masked stores, deleted afterwards):
  - `in_place` and `append` without `confirm_write` return **422 `confirm_write_required`** on both Oracle and
    Mongo.
  - The target fingerprints are unchanged.
  - The console wizard's confirmation is covered by the console M3 tests, which passed.
- **Counting server (D4):** not applicable to M3, which has no scanner.

## Log, metrics and audit scan (`probes/logscan.py`, `logscan.json`)

- Scope: every container of project `dps-wt-m3`, saved to `stack-logs.txt`:
  - app, worker, dps-db, source-pg, mongo (15.4k lines), oracle, minio, azure-ocr-mock;
  - the one-shots migrate, minio-init, seed-sources and demo-secrets.
- Values scanned: **224 088** source values, plus the 3 key-material strings from the walk group's `.dpskey`.
- Matching is token-normalised. The positive control finds **3/3** planted values.
- **0 value hits and 0 key hits** in every container.
- `/metrics`: `dps_llm_raw_pii_sent_total 0.0`, with 0 value hits and 0 key hits.
- Audit: **1474** `dps_audit_log` rows, with 0 value hits and 0 key hits.

## Non-blocking findings (route to owners)

### N-r2-1: flaky M1 gate test (pre-existing, outside M3 code)

- The failing test is `tests/test_ndjson.py::test_one_audit_event_per_stream`.
- **Rate:** about 1.2% (12 of 1000 in `probes/ndjson_flake.py`).
- **What happens:** the request's third item fails the leakage sweep, and the stream returns
  `{"code": "leakage"}`. The system fails closed: nothing is returned for that item.
- **Cause:**
  - `verify.normalise` keeps letters and digits only.
  - The `email` method keeps the domain.
  - So a short original such as `d.q@example-mail.in` normalises to `dqexamplemailin`.
  - That string is found inside a token that happens to end in `dq` followed by the same domain.
- The engine and verify code are unchanged since the M3 merge base, so the flake exists on main too.
- **Fix options:**
  - Use longer local parts in the test, or a fixed seed.
  - Or anchor the sweep for e-mail originals at the local-part start.

### N-r2-2: validator gap without a leak

- `_check_query_outputs` accepts a derived table or join source whose alias equals the object's own qualifier.
  Both engines refuse to run it (ORA-00918 / 42712), the job fails with `source_error`, and no target is written.
- Defence in depth: refuse when the qualifier is defined more than once.

### N-r2-3: cosmetic console issues

- The Mongo select step's "Selection checks" list uses SQL wording: "SELECT only", "No INTO, procedure calls,
  database links or FOR UPDATE".
- The page subtitle says "Protect a table" for a collection. The mockup says "table or collection".
- Some field chips are truncated ("accoun…", "c…").
- In the wizard review table, the METHOD column is cut off at 1440 px (`fpe_digi`). In the job-detail table,
  the EVIDENCE column is cut off.
- The sidebar footer reads "v0.2.0 · M2".
- In full-page screenshots, the fixed header and sidebar render mid-page. This is a capture artifact.

### N-r2-4: stale DoD 5 command

The DoD 5 command in the M3 plan collects 0 tests. The real conformance modules are `test_conformance_*.py`.

### Other notes

- **Carried, not re-examined in this round:**
  - N-new-1: PostgreSQL `bank.contacts.value` and `bank.addresses.line1` are classified `keep`. This is main
    discovery code.
  - N-new-2: PostgreSQL `target_error` retries instead of `length_overflow`.
  - N-new-3: an explicit text `keep` keeps drift sub-documents (re-observed, 3 sentinels).
  - G6 N2–N4 and N8–N10.
- **G6-N1:** the 54 tracked `docs/superpowers/build/m3-shots/*.png` were already modified before I started, and
  the gate rewrites them. I left them as found and did not commit them.
- **Oracle created targets widen text columns** (EMAIL and ADDR_LINE1 become CLOB, TAX_ID CHAR becomes
  VARCHAR2). This is documented in M3-DEVIATIONS (det_token worst-case width).
- **The evidence pack's "values checked"** (210,023) is below "values transformed" (210,025). This is by design:
  values that normalise to fewer than 4 characters are not sweep-checked (`batch._checked`).
- **Cross-system DATE_OF_BIRTH agreement is 0/500**, marked "informational". This is a documented
  anchor-dependent limit.
- **Tag `v0.4.0-m3`** is not present. Per programme plan §2.5/§5, the release step tags it after verification on
  main.

## Working tree after verification

- HEAD is `13875fd`, with the screenshots commit on top of `da470f9`.
- Remaining dirt: the 54 `m3-shots` PNGs (G6-N1), which were there before I started.
- Docker: `down -v`, with 0 containers and 0 volumes of `dps-wt-m3` left. No other project was touched.
- Scratch, holding the key file and the values file, was deleted.
