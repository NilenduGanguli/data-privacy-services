# FINAL — v1.0.0 candidate on `main` — L2-ENGINES verifier, round 1

**Verdict: FAIL — 2 blocking defects in this lens.**

- **B1 (confirmed independently; first found by FINAL-gates-r1).** Every L2 run whose policy is an inline body or a stored `@name` dies in `finalize`, on all three engines. All rows are written, then the job ends `dead` / `finalize_error` with no evidence pack.
- **B2 (new).** On MongoDB, a `table` load or a named-collection `append` into a collection that already exists is checked against a *sampled* document shape as if it were DDL:
  - an existing **empty** collection is always refused with `target_invalid` ("the target has no column $._id");
  - so is any collection whose sampled documents lack a leaf the run writes.

  The job fails closed: no leak and no write.

Everything else in the lens is green:
- **Obfuscate runs in every write mode** on PostgreSQL, Oracle and MongoDB: 20,000 rows each, **0 same-row survivors** in every transformed column, counted independently.
- **D3:** confirmation and the admin scope on production connections are enforced.
- **Consistency check:** overlap 1000/1000 on every pair.
- **Resume after kill:** every write mode on every engine. 15/15 cases, including 4 Oracle/MongoDB cases that no shipped test covers.
- **D-A identifier check:** `checked` = the rows on 20,000-row Oracle, MongoDB and PostgreSQL targets. A real identifier planted at row 15,000 is flagged, both in process and by a real run's own check.

| | |
|---|---|
| Date | 2026-10-16, 10:00–10:23 IST |
| Tree | `/Users/neelu/document_intelligence/data-privacy-services`, `main` @ **`45be8ac`** (`chore(release): v1.0.0`). HEAD unchanged. **No repository file modified by me.** An untracked `docs/screenshots/v1.0.0/` appeared at about 10:11, written by another verifier. I left it untouched. |
| Stack | Main compose project, default ports. Left running as found: not restarted, not rebuilt, no `down`, no container killed. `readyz` ready at the end. |
| Code under test | sha256 is identical in host tree = `app` = `worker` for `identifier_check.py`, `jobs_run.py`, `targets.py`, `mongo.py` and `consistency.py`. Full 206-file identity: FINAL-gates-r1, 09:50. |
| Probes / logs | `~/.cache/dps-build/reports/FINAL-l2-engines-r1/`: scripts `p1_setup.py` … `p9_cleanup.py`, `probe_resume_more_test.py`, `svt_lib.py`, `log_scan_source.py`; outputs in `logs/*.json`, `*.log`, `*.xml` |
| Values | Only counts, ids, column names, methods, codes and value *shapes* (`AA999999A`) appear here and in the probe outputs. Keys returned by the API were dropped unread. The group key was held in memory only for the three submissions. |
| Isolation | Every write went to probe-owned scratch objects with the `l2r1` prefix: frozen 20,000-row copies of `bank.customers`, `CRM.CUSTOMERS` and `crm.profiles`. No shared demo object was written. Everything was dropped at the end (§9). The test-harness runs used their own `dps_test_*` metadata DB and subprocess workers. |

## 1. Lens results

| Item | Result | Evidence |
|---|---|---|
| Obfuscate × {table, files, append, in_place} × {PG, Oracle, Mongo}, 20,000 rows | 11/12 + 2 MongoDB append variants succeeded, `verification.passed`. **0 same-row survivors in every non-keep column.** MongoDB named append into an empty collection was refused (B2). | §2, `logs/30-runs.json`, `40-svt.json`, `70-`, `80-` |
| D3 typed confirmation | All enforced | §3, `logs/30-runs.json` (`d3`), `35-d3-production.json`, `36-console-confirm-vitest.log`, `38-evidence-d3.txt` |
| Consistency check | Passed, 3/3 pairs, matched 1000/1000 on each pair, coverage 1.0 | §4 |
| Resume after kill | 15/15 passed, covering every write mode on all three engines | §5, `logs/10-resume-tests.log`, `15-resume-more.log` |
| D-A identifier check, 20,000 rows, plant at row 15,000 | `checked` = non-null values (independent count); `rows_read` 20,000/20,000; plant flagged in process and by a real run | §6, `logs/50-da.json` |
| B1, policy body → dead finalize | Reproduced on PG (inline and `@name`), Oracle (inline), Mongo (inline); the preset control succeeds | §7, `logs/60-b1.json` |
| B2, MongoDB existing collection | `table` truncate_insert and upsert into an existing empty collection refused; named append into an empty or sparse collection refused | §8, `logs/70-`, `80-mongo-*.json` |

## 2. Obfuscate runs in every write mode (`p2_runs.py`, `p4_svt.py`, `p7_*`, `p8_*`)

**Setup** (`p1_setup.py`):
- Frozen copies `bank_restore.l2r1_{src,src2,ip}`, `CRM.L2R1_{SRC,SRC2,IP}` and `crm.l2r1_{src,src2,ip}`, 20,000 rows each, with keys.
- Empty targets with the same DDL for append.
- Snapshots:
  - `src`: the default review;
  - `src2` / `ip`: the reviewed overrides the shipped integration tests use for writes into existing narrow columns (key `keep`, `national_id` `fpe_digits`, `tax_id` `mask_partial`; Oracle also `PIN_CODE` `mask_partial`, `ADDR_LINE1` `fpe_text`; MongoDB address line and pin `det_token`).
- New connections:
  - `l2r1-ora-owner`: `dps/src/oracle-crm-owner`, source + target;
  - `l2r1-mdb-rw`: `dps/src/mongo-crm`, source + target.
- PostgreSQL uses `bank-restore-rw` / `bank-uat`; files go to `exports-s3` (JSONL, uncompressed).

**Run settings:**
- `table` runs: one job group (`grp_01M51FY30434JPGWZFR9B0ZK40`), the group key passed in `consistency.key_material`, `create_if_missing`, `truncate_insert`.
- `append`: confirmed with `expected_rows` 20,000; `offset` 90,000,000 on PG and Oracle, `generate` on MongoDB.
- `in_place`: confirmed with `expected_rows` 20,000.

**How the comparison works:**
- I read the source copy and the target **in full**: the database, or the JSONL objects of the job's manifest read back from MinIO.
- Rows are aligned as follows:
  - in place: by the kept key;
  - PG/Oracle append: by key − offset;
  - MongoDB append: by the kept `$.accounts[*].balance`, unique over all 20,000;
  - table and files: by kept columns (PG's 8 kept columns, Oracle `LAST_CONTACT_AT`, MongoDB `_id`).
- **Survivors** = aligned rows whose transformed column still holds the source row's value.

| Run | Job | State | Rows written | src / tgt / aligned (by) | Non-keep cols | Survivors | Target values found elsewhere in the source column |
|---|---|---|---|---|---|---|---|
| PG table | `j_01M51FY309S12HWMVD0X9MCWBC` | succeeded | 20000 | 20000/20000/20000 (8 kept cols) | 8 | **0** | customer_id 5, date_of_birth 12653 |
| PG files | `j_01M51FY30YX5WFDH8MYY3ADQNM` | succeeded | 20000 | 20000/20000/20000 (8 kept cols) | 8 | **0** | customer_id 3, date_of_birth 12601 |
| PG append | `j_01M51FY315KXBE62H36MTA7MAZ` | succeeded | 20000 | 20000/20000/20000 (key − offset) | 7 | **0** | date_of_birth 12608, national_id 3 |
| PG in_place | `j_01M51FY31D6CKTE6327P1VN5VC` | succeeded | 20000 | 20000/20000/20000 (customer_id) | 7 | **0** | date_of_birth 12596, national_id 3 |
| Oracle table | `j_01M51FY30FZA1BHVBTKFXBYKGQ` | succeeded | 20000 | 20000/20000/20000 (LAST_CONTACT_AT) | 10 | **0** | CUSTOMER_ID 5, DOB 12653 |
| Oracle files | `j_01M51FY31NHYJHRTW4P6KTT3XP` | succeeded | 20000 | 20000/20000/20000 (LAST_CONTACT_AT) | 10 | **0** | CUSTOMER_ID 4, DOB 12551 |
| Oracle append | `j_01M51FY31T90P4CN6J9NYSRSX2` | succeeded | 20000 | 20000/20000/20000 (key − offset) | 9 | **0** | DOB 12682, NATIONAL_ID 1 |
| Oracle in_place | `j_01M51FY31ZFHXFERVE2KCGNSZ8` | succeeded | 20000 | 20000/20000/20000 (CUSTOMER_ID) | 9 | **0** | DOB 12621 |
| Mongo table | `j_01M51FY30P1P9XTMMYQ4TA1NKM` | succeeded | 20000 | 20000/20000/20000 (_id) | 10 | **0** | $.customerId 5, $.profile.dob 12639 |
| Mongo files | `j_01M51FY32474XSDDTF3QWA8C2E` | succeeded | 20000 | 20000/20000/20000 (_id) | 10 | **0** | $.customerId 6, $.profile.dob 12471 |
| Mongo in_place | `j_01M51FY32FAQVB8Z5AYQNS2X2K` | succeeded | 20000 | 20000/20000/20000 (_id) | 10 | **0** | $.customerId 3, $.profile.dob 12532 |
| Mongo append, same collection (`generate`) | `j_01M51GCR55CG1P5V1PTSSXJJPX` | succeeded | 20000 | 20000/20000/20000 (balance) | 10 | **0** | $.customerId 2, $.profile.dob 12621 |
| Mongo append, named collection holding 1 covering doc | `j_01M51GJ2X2FX7H3FS0K7T06EKW` | succeeded | 20000 | 20000/20000/20000 (balance) | 10 | **0** | $.customerId 2, $.profile.dob 12656 |
| Mongo append, named **empty** collection | `j_01M51FY32ANSCNS1NZPANZTJXM` | **failed** `target_invalid` | 0 | — | — | — | B2 |

**Non-keep columns** (the transformed ones):
- PG: customer_id, date_of_birth, email, full_name, mobile, national_id, notes, tax_id.
- Oracle: ADDR_LINE1, CUSTOMER_ID, DOB, EMAIL, FULL_NAME, MOBILE, NATIONAL_ID, NOTES, PIN_CODE, TAX_ID.
- Mongo: iban, address line1/pin, email, mobile, customerId, taxId, notes, dob, fullName.

**Other checks on these runs:**
- Every success has `verification.passed`.
- Every in-place and append pack records `confirm_write: true`, `expected_rows: 20000` and `write_ledger_cleared: true`.
- The same-collection MongoDB append left the 20,000 original documents untouched: 20,000/20,000 identical.

**Target values found elsewhere in the source column** — none of these is a same-row survivor:
- **customer id, 2–6 per target.** These are FF1 crossings: the token lands on another real id in a dense 8-digit range. The expected number is about 4.
- **dates of birth, about 12,600.** `date_shift` over a small domain.
- **national_id 3 / NATIONAL_ID 1.** All are UK NINOs of shape `AA999999A`:
  - the reviewer-chosen `fpe_digits` encrypts only the 6 digits;
  - about 2,000 NINOs share a 10⁶ domain, so a few tokens equal *another* person's real NINO (N2);
  - this appears only where the review picked `fpe_digits` to fit `varchar(18)` in place; the default `det_token` gives 0.

## 3. D3 — typed confirmation and production writes

**At submission** (all three engines, before anything wrote the in-place copies):
- `in_place` without `confirm_write` → **422 `confirm_write_required`**.
- `in_place` with `confirm_write: false` → **422**.
- `append` without `confirm_write` → **422**.
- `in_place` confirmed with `expected_rows: 100` (20,000 selected) → 202, then the job **failed in prepare with `row_count_mismatch`** (PG `j_01M51FY0DCWFDF1B3Z233X33Q6`, Oracle `j_01M51FY0DV6GVW8MDVCRQG6XHG`, Mongo `j_01M51FY0ECDE4QDRFC11W7JAT7`). The in-place copies' sha256 over every row was **unchanged** on all three engines.

**Production connections** (`p3_prod.py`), with a DB key that has scopes `obfuscate synthesize jobs detect` and no admin:
- The key was minted with `Cache-Control: no-store` and disabled at the end (HTTP 200).
- `l2r1-pg-prodtag` is tagged production; `l2r1-pg-prodclone` is untagged and production only through its credential `dps/src/bank-pg-ro`. Both use the already-production credential, so the tenant's production-credential set did not change.
- Results:
  - **non-admin:** `in_place`, same-table `append`, `table` into it (obfuscate and synthesize) → **403 `admin_scope_required`** for both connections (8/8);
  - non-admin creating a connection that names the production credential → **403**;
  - **control:** the same non-admin `table` spec into non-production `bank-uat` (dry run) → 202, `succeeded`/`dry_run`;
  - **admin:** passes the gate (202). The jobs then fail `target_invalid` because the login is read-only, as expected;
  - non-admin `POST /jobs/{id}/retry` of the admin's production run → **403 `admin_scope_required`**.

**Console.** The wizard sends `confirm_write: true` (with `expected_rows`) only once the typed object name matches (`ConfirmDialog requireText`, `canConfirmWrite`). `vitest` on `state.test.ts` + `jobs.test.ts`: **78 passed**. This includes "never sends confirm_write: true without expected_rows".

## 4. Consistency check (`POST /job-groups/{id}/consistency-check`, sample 1000)

**Result:** HTTP 200, **passed**, 3/3 consistent pairs, sampled 1000, `all_members_matched` 1000, `methods_agree`, no problems.

**Anchor:** `CUSTOMER_ID` `fpe_digits`. The three members are:
- Mongo `$.customerId` (prefixed digits, the anchor);
- Oracle `CUSTOMER_ID` (numeric);
- PG `customer_id` (numeric).

**Pairs:** (0,1), (0,2) and (1,2), each checked 1000, **matched 1000**, coverage 1.0.

**Attributes:**
- Agreed 1000/1000: EMAIL, PERSON_NAME, PHONE, NATIONAL_ID `det_token`, STREET_ADDRESS.
- Agreed 564/564: PAN_IN.
- Agreed 1000/1000 but not counted toward the pass: POSTAL_CODE `generalize`.
- DATE_OF_BIRTH `date_shift` agreed only **1/1000**. It does not count toward the pass, by design (N1).

## 5. Resume after kill

**Shipped tests**, run independently with `DPS_TEST_REQUIRE_SERVICES=1 pytest -m slow` against real containers, their own metadata DB and subprocess workers. **11 passed** in 3:50:
- `tests/connectors/test_resume_after_kill.py` (PG): table truncate_insert, table upsert, in_place, append offset named, append generate same, files JSONL.
- `tests/e2e/test_m3_resume_after_kill.py`: Oracle in_place and append, Mongo in_place and append, Oracle key-less table cross-connection.

**Gap.** No shipped test kills Oracle or MongoDB `table` (keyed) or `files`.

**New probe** (`probe_resume_more_test.py`). It reuses the e2e case builders and helpers unchanged and runs from the repo with `-p tests.conftest`. **4 passed** in 2:04:

| Case | Rows a/b | Digests equal | Survivors a/b | Partitions / resumed |
|---|---|---|---|---|
| oracle-table | 3000/3000 | yes | 0/0 | 3/3 |
| oracle-files | 3000/3000 | yes | 0/0 | 3/3 |
| mongo-table | 3000/3000 | yes | 0/0 | 3/3 |
| mongo-files | 3000/3000 | yes | 0/0 | 3/3 |

In each case:
- the killed run's target equals the uninterrupted reference;
- the evidence counts `resumed_partitions` ≥ 1;
- the verification passes.

So every write mode is now killed and resumed on every engine.

I did not kill the shared compose worker, which would have disturbed the other lens verifiers on this stack. All 15 cases run the host `.venv` code; the gates verifier showed it identical to the image.

## 6. D-A post-write identifier check (`p5_da.py`)

**A. Real synthesize `table` runs**, 20,000 rows each, from the frozen copies:

| Engine | Job / pack | Scope | rows_read t/s | Column: checked / independent non-null / in_source / independent in source | Passed |
|---|---|---|---|---|---|
| Oracle | `j_01M51G5F054ES2FPPKNDKNWHVX` / `ev_01M51G94A6HV2Q1Y2B6X5SGS6B` | whole_table | 20000/20000 | NATIONAL_ID 20000/20000/0/0; TAX_ID 11989/11989/0/0 | yes |
| MongoDB | `j_01M51G5F09C9PJ97GTRXCV8M26` / `ev_01M51G7K74QF948GEK5JNQKACQ` | whole_table | 20000/20000 | `$.accounts[*].iban` 20000/20000/0/0; `$.kyc.taxId` 11989/11989/0/0 | yes |
| PostgreSQL | `j_01M51G5F0E6NE9GDY7QX54WWDP` / `ev_01M51G856BQS8X8D8NWSBN9TVQ` | whole_table | 20000/20000 | national_id 20000/20000/0/0; tax_id 11989/11989/0/0 | yes |

`checked` equals the rows wherever the column is populated (20,000). The tax ids are populated in 11,989 of the 20,000 source rows, and `checked` equals that number.

**B. Planted, in process.**
- **Plant.** A copy of each synthetic target, with the row at key order **15,000** carrying the real identifier of source row 15,000. The value is copied inside Oracle (`UPDATE … SELECT`) or in process for MongoDB, and never printed.
- **Independent check.** It locates exactly 1 source value in each copy, at position **[15000]**.
- **The worker's own `check_identifiers`** (same sha256 as the image), run against the deployed engines:

| Engine | Column | checked | in_source | rows_read t/s | Review columns |
|---|---|---|---|---|---|
| Oracle | NATIONAL_ID | **20000** | **1** | 20000/20000 | [NATIONAL_ID] |
| MongoDB | `$.accounts[*].iban` | **20000** | **1** | 20000/20000 | [iban] |

The other identifier column (`TAX_ID` / `$.kyc.taxId`) was checked 11,989 times with in_source 0.

**C. Planted, through a real run.** A second planted copy, then a synthesize `upsert` run of one row into it (`max_rows: 1`):
- Oracle `j_01M51G9AKS11R3XG10V7PK4VW5` / `ev_01M51G9CH3W2F8ZS5N9WRJ6EE3`;
- MongoDB `j_01M51G9AM1RJYE61MS672R0519` / `ev_01M51G9C8XN3Y3KWCSRXH232EZ`.

In each run's own pack:
- `rows_read` target **20,001** / source 20,000;
- the planted column has `checked` 20,001 and **in_source 1**, equal to the independent count;
- `review_columns` names it;
- `review_required: true`, **`passed: false`**.

## 7. B1 — a policy body or `@name` kills every L2 run in `finalize` (blocking, confirmed)

**Probe** (`p6_b1.py`). `table` runs of 500 rows from the frozen copies. The stored policy `l2r1-pol` was created from `gdpr-pseudonymise` (201).

| Case | Job | Row's `spec.policy` | State | Rows written | Evidence | Worker `finalize_failed` |
|---|---|---|---|---|---|---|
| PG inline `{preset, name}` | `j_01M51GBJFG05F2Y147QZ9YKR74` | sealed label {name, sealed, sha256} | **dead `finalize_error`** | 500 | none | 3 × `error=PrepareFailure` |
| PG `@l2r1-pol` | `j_01M51GBJFYDZDSC1WD5P37E736` | label + version | **dead `finalize_error`** | 500 | none | 3 |
| Oracle inline | `j_01M51GBJGCHD0ZNEAY8ZFZ9CS9` | label | **dead `finalize_error`** | 500 | none | 3 |
| Mongo inline | `j_01M51GBJGQCDECQAW1W1QK9009` | label | **dead `finalize_error`** | 500 | none | 3 |
| Control: PG `policy: "gdpr-pseudonymise"` (preset name) | `j_01M51GBJH1R61FAB0CP0MM9886` | plain string | succeeded | 500 | `ev_01M51GBSC9EG6AJ9TM27J4MR7W` | 0 |

**No leak.** Source vs target on all five targets: 500/500 aligned, and same-row equality only in kept columns (0 in every transformed column). The failure is liveness and evidence: the target is written, but the job is dead with no pack.

**Corroboration.** Two `synthesize` `table` runs of another verifier in tenant `default` died the same way (`j_01M51FT2GEVQB0HVGMJDH0QCHE`, `j_01M51G02JVF3M3DHB95BHTQSA5`: stored-policy label with version, 3 × `finalize_failed error=PrepareFailure` each). So synthesize mode is affected too.

**Root cause and fix.** As FINAL-gates-r1 §4:
- `_finalize` validates the *redacted* `job.spec`, whose policy is the F-18 sealed label.
- `_post_sample` → `_policy` → `PrepareFailure("policy_invalid")`, which `_best_effort` does not catch.
- Fix: use the opened spec (`_spec(ctx, job)`) for `_post_sample`, `_store_evidence`'s `policy_version` and `_identifier_check`.
- Tests: an integration run with an inline body and one with `@name`, through a worker, to `succeeded` with a pack, on PG, Oracle and Mongo.

## 8. B2 — MongoDB: an existing collection is checked like a table's DDL (blocking, new)

**Symptom** (`p7_mongo_append.py`, `p8_mongo_targets.py`):

| Run (MongoDB, obfuscate) | Job | Result |
|---|---|---|
| `table` `truncate_insert` into an existing **empty** collection `crm_uat.l2r1_tbl_pre` (500 rows) | in `logs/80-mongo-targets.json` | **failed `target_invalid`**: "the target has no column $._id" |
| `table` `upsert` into the same empty collection | in `logs/80-mongo-targets.json` | **failed `target_invalid`**: "the target has no column $._id" |
| `append`, named **empty** collection (`generate`, confirmed) | `j_01M51FY32ANSCNS1NZPANZTJXM` | **failed `target_invalid`**: "the target has no column $.accounts[*].balance" |
| `append`, named collection holding 1 document whose sample lacks one written leaf (empty `tags`) | in `logs/70-mongo-append.json` | **failed `target_invalid`**: "the target has no column $.tags[*]" |
| `append`, named collection holding 1 document that has every leaf | `j_01M51GJ2X2FX7H3FS0K7T06EKW` | succeeded, 0 survivors (§2) |
| `append`, same collection | `j_01M51GCR55CG1P5V1PTSSXJJPX` | succeeded, 0 survivors (§2) |

PostgreSQL and Oracle `append` into pre-created **empty** tables succeed (§2), because their shapes come from the catalogue.

**Root cause:**
- `MongoConnector.describe` (`dps/layers/connectors/mongo.py:504-520`) infers a shape from a `$sample` of at most 500 documents. An empty collection gives a shape with no columns and no key.
- `targets.py` then treats that shape as DDL for any existing target:
  - `_check_existing` (`:949`) raises `PrepareFailure("target_invalid", "the target has no column …")` for every written leaf the sample lacks;
  - for `table` it also requires `target_shape.key == source key` (`:1097`).
- This happens for `table` (`:1096`) and for `append` (`:1123`).

**Impact:**
- The console offers both paths for MongoDB: "Named collection" for append (`StepTarget.tsx:278`) and "Destination collection" for copies.
- Loading into a collection a DBA pre-created (empty, with indexes or a validator), or one that someone emptied, is always refused.
- A sparse target (optional fields, empty arrays) can be refused depending on what the sample happens to contain.
- Fails closed: nothing is written and no value leaks. Workarounds: drop the collection and let DPS create it, or append to the same collection.
- No shipped pipeline test covers an existing empty or sparse MongoDB target. The writer tests (`test_mongo_writer.py`) bypass `targets.resolve`.

**Fix (suggested):**
1. For engine `mongo`, do not refuse leaves the sampled target shape lacks: a collection has no column DDL, which the create branch already says at `:1080`.
2. Check kind compatibility only for leaves that the sample does show.
3. Take `_id` as the key of every collection.
4. Tests first, through `prepare` on a real MongoDB:
   - `table` truncate_insert and upsert into an existing empty collection;
   - named append into an empty collection;
   - named append into one whose documents lack an optional leaf.

## 9. Proofs and hygiene (counts only)

- **Logs.** I scanned the logs of all 15 project containers since 04:30Z (`log_scan_source.py`, the gates verifier's scanner):
  - 129,792 needles from the three demo sources (20,000 e-mails, 55,565 names, 54,227 digit forms); self-test 4/4;
  - **0 hits** in every container.
- **Worker errors since 04:30Z.** Exactly 18 lines, all `finalize_failed error=PrepareFailure`: 4 jobs of mine (B1) and 2 of another verifier (B1).
- **Database errors since 04:30Z:**
  - **source-pg:** exactly 1, `permission denied for schema bank_uat`. This is my admin probe through the read-only production login, and it was expected.
  - **dps-db:** 3. All come from other verifiers' hand-written SQL against the metadata DB (`select id from dps.dps_tenant`, `… dps.dps_idempotency`), not from product code.
- **LLM.** `dps_llm_raw_pii_sent_total 0.0` at the end.
- **Write ledgers.** 0 rows left for any of my 34 jobs in every `dps_write_ledger`: PG, Oracle and MongoDB.
- **Cleanup** (`p9_cleanup.py`, `logs/99-cleanup.json`):
  - 9 PG tables, 9 Oracle tables and 14 MongoDB collections dropped, all `l2r1`-prefixed;
  - 6 MinIO objects under `dps-exports/l2r1/` deleted;
  - the 4 probe connections deleted (204).
- **Left in place:** the disabled key `key_01M51FZW222SJ38MDF5NRE83ZY`, the stored policy `l2r1-pol` (there is no delete route), job group `grp_01M51FY30434JPGWZFR9B0ZK40` and the job history.

## 10. Non-blocking notes

- **N1** `date_shift` dates of birth differ across engines for the same customer: the consistency attribute agreed 1/1000. The likely cause is that a PG `date`, an Oracle `DATE` and a MongoDB datetime have different canonical texts, so they get different shifts. The contract excludes `date_shift` from the pass, but testers joining PG, Oracle and Mongo customers will see different birth dates. Worth documenting, or canonicalising dates before the shift.
- **N2** `fpe_digits` on a UK NINO (`AA999999A`) encrypts 6 digits. About 0.15 % of tokens equal another real customer's NINO (3/2,010 PG, 1 Oracle). This is not a same-row survivor, and the leakage verifier passes. Suggest that the review guidance prefer `det_token`/`hmac_pseudonym` for short-domain ids where the column allows.
- **N3** The admin's dry-run `in_place` through the read-only production clone fails in prepare with `target_invalid`. The gate passed; the login simply cannot write. A clearer code, such as `target_not_writable`, would help operators.
- **N4** B1 also hits `synthesize` runs (another verifier's two jobs, §7). The fix round should test both modes.

## 11. For the fix round

1. Fix B1 and B2 with red-first tests.
2. Rebuild the stack so the app and worker run the fix.
3. Re-run, against the new image:
   - `p6_b1.py`: all 5 runs `succeeded` with packs; the `@name` pack has the policy name and version;
   - `p8_mongo_targets.py` and the named-empty case of `p2_runs.py`/`p7`: `succeeded`, 0 survivors.
   These scripts recreate their scratch objects only with `p1_setup.py` first.
4. Re-check the rest of the lens:
   - `tests/connectors/test_resume_after_kill.py` and `tests/e2e/test_m3_resume_after_kill.py`;
   - `probe_resume_more_test.py` (`-p tests.conftest`);
   - `-m "oracle or mongo"`.
