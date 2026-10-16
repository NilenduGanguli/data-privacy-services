# FINAL — v1.0.0 candidate on `main` — L2-ENGINES verifier, round 2

**Verdict: FAIL — 1 blocking defect in this lens: R2-B1, the unfixed remainder of round 1's B2.**

**R2-B1.** MongoDB: a named `append`, or a `table` load (`truncate_insert` or `upsert`), into an existing collection is refused with `target_invalid` in one case: the collection's sampled documents show a field but lack its first nested leaf (in path order), while a sibling leaf of another kind is present. Example: `kyc` without `taxId`. The cause is that `_check_existing` looks MongoDB columns up by their **top-level name**. The run fails closed: nothing is written and no value leaks.

Everything else in the lens is green, and round 1's **B1 is fixed live**:

- **Obfuscate runs, every write mode, all three engines.** 12 runs of 20,000 rows on PostgreSQL, Oracle and MongoDB, plus 6 more MongoDB runs into existing collections. **0 same-row survivors in every transformed column.** Counts were made independently.
- **D3 typed confirmation is enforced.** This holds at submission, in prepare, through the admin scope on production connections, and in the console (78/78).
- **Consistency check.** Overlap 1000/1000 on each of 3/3 pairs, coverage 1.0.
- **Resume after kill.** 15/15: every write mode on every engine.
- **D-A identifier check.** On 20,000-row Oracle, MongoDB and PostgreSQL targets, `checked` equals the rows wherever the column is populated. A real identifier planted at row 15,000 is flagged, both in process and by a real run's own check.
- **B1 re-check.** 9/9 policy-body runs succeeded with packs: inline and `@name`, obfuscate and synthesize, on PG, Oracle and MongoDB. Each pack names the policy and its version.
- **B2 re-check.** Fixed for:
  - an empty collection;
  - a document missing a whole top-level field, such as round 1's empty `tags`.

  **Not** fixed for a missing nested leaf (R2-B1).

| | |
|---|---|
| Date | 2026-10-16, 12:30–12:50 IST |
| Tree | `/Users/neelu/document_intelligence/data-privacy-services`, `main` @ **`d033dd3`**, the same at the start and end. **No repository file modified by me.** `git status` shows only the untracked `docs/screenshots/v1.0.0/` (another verifier's), which I left untouched. |
| Stack | Main compose project, default ports, as the gates verifier left it: not restarted, not rebuilt, no `down`, no container stopped or killed. `readyz` `ready: true` at the start and end; 11 services up. |
| Code under test | `identifier_check.py`, `jobs_run.py`, `targets.py`, `mongo.py` and `consistency.py` have the same sha256 on the host, in `app` and in `worker`. Worker: 155/155 tracked `dps/*.py` identical at the end. |
| Probes / logs | `~/.cache/dps-build/reports/FINAL-l2-engines-r2/`. Scripts: `p1_setup.py` … `p9_cleanup.py`, `p7b_sparse_isolate.py`, `p38_evidence.py`, `probe_resume_more_test.py`, `svt_lib.py`, `log_scan_source.py`. Round 1's probes were retargeted to the `l2r2` prefix. p6 and p7 were rewritten for the B1/B2 re-checks. Outputs are in `logs/*.json`, `*.log`, `*.txt` and `*.xml`. |
| Values | Only counts, ids, column paths, methods, codes and kinds appear here and in the outputs. Keys returned by the API were dropped unread. The group key was held in memory only for the three submissions. The non-admin DB key was held in memory and disabled at the end (HTTP 200). |
| Isolation | Every write went to probe-owned scratch objects with the `l2r2` prefix: frozen 20,000-row copies of `bank.customers`, `CRM.CUSTOMERS` and `crm.profiles`. All were dropped at the end (§9). The resume tests used their own `dps_test_*` metadata DB and subprocess workers. |

## 1. Lens results

| Item | Result | Evidence |
|---|---|---|
| Obfuscate × {table, files, append, in_place} × {PG, Oracle, Mongo}, 20,000 rows | **12/12 succeeded**, `verification.passed`, **0 survivors** in every transformed column. This includes MongoDB append into an *empty named* collection, which round 1 refused. | §2; `logs/30-runs.json`, `40-svt.json`, `42-per-column.txt` |
| MongoDB into existing collections | Succeeded, 0 survivors: same collection, named empty, named covering, `table` truncate_insert/upsert into an empty collection, and round 1's empty-`tags` sparse case. **Refused: a sparse document missing a nested leaf (R2-B1).** | §8; `logs/70-mongo-existing.json`, `75-sparse-isolate.json`, `76-sparse-table.json` |
| D3 typed confirmation | All enforced | §3; `logs/30-runs.json` (`d3`), `35-d3-production.json`, `36-console-confirm-vitest.log`, `38-evidence-d3.*` |
| Consistency check | Passed. 3/3 pairs, each matched 1000/1000, coverage 1.0. | §4 |
| Resume after kill | **15/15 passed**: 11 shipped tests plus 4 Oracle/MongoDB `table`/`files` cases. | §5; `logs/10-resume-tests.*`, `15-resume-more.*` |
| D-A identifier check, 20,000 rows, plant at row 15,000 | `checked` equals the independent non-null count. `rows_read` 20,000/20,000. The plant is flagged in process and by a real run. | §6; `logs/50-da.json` |
| B1: policy body or `@name` | **Fixed.** 9/9 succeeded with packs; 0 `finalize_failed`. | §7; `logs/60-b1.json` |

## 2. Obfuscate runs in every write mode (`p2_runs.py`, `p4_svt.py`)

The setup is round 1's.

**Snapshots:**
- `src`: the default review, used by `table`, `files` and synthesize.
- `src2` / `ip`: the reviewed overrides for writes into existing narrow columns (append, in_place). The key is `keep`; `national_id` is `fpe_digits`; `tax_id` is `mask_partial`. Oracle also has `PIN_CODE` `mask_partial` and `ADDR_LINE1` `fpe_text`; MongoDB has address line and pin as `det_token`.

**Run settings:**
- `table`: one job group, `grp_01M51R8WK1CPRVCDWYZPSV0C3A`; key passed in `consistency.key_material`; `create_if_missing`; `truncate_insert`.
- `files`: JSONL to MinIO via `exports-s3`.
- `append`: confirmed with `expected_rows` 20,000. PG and Oracle use `offset` 90,000,000 into an empty pre-created table; MongoDB uses `generate` into the empty named collection `crm.l2r2_app`.
- `in_place`: confirmed with `expected_rows` 20,000.

**Comparison method.** The source copy and the target are read **in full**: from the database, or from the JSONL objects of the job's manifest in MinIO. Rows are aligned as follows:
- in place: by the kept key;
- PG/Oracle append: by key − offset;
- MongoDB append: by the kept `$.accounts[*].balance`, which is unique over 20,000;
- table and files: by kept columns (PG's 8, Oracle `LAST_CONTACT_AT`, MongoDB `_id`).

| Run | Job | State | Rows | src/tgt/aligned | Transformed cols | Survivors | Target values found elsewhere in the source column |
|---|---|---|---|---|---|---|---|
| PG table | `j_01M51R8WK6KDQM9A6PA04EQ38P` | succeeded | 20000 | 20000/20000/20000 | 8 | **0** | customer_id 2, date_of_birth 12510 |
| PG files | `j_01M51R8WKX8HT4WDWRFMRV6JG2` | succeeded | 20000 | 20000/20000/20000 | 8 | **0** | customer_id 3, date_of_birth 12535 |
| PG append | `j_01M51R8WM5D682SYT91ZZP4FD2` | succeeded | 20000 | 20000/20000/20000 | 7 | **0** | date_of_birth 12481, national_id 1 |
| PG in_place | `j_01M51R8WMBV3K2CWDYT9XVSEJP` | succeeded | 20000 | 20000/20000/20000 | 7 | **0** | date_of_birth 12678, national_id 1 |
| Oracle table | `j_01M51R8WKDTPR7HPA6MM9KZRKE` | succeeded | 20000 | 20000/20000/20000 | 10 | **0** | CUSTOMER_ID 2, DOB 12510 |
| Oracle files | `j_01M51R8WMHBH6S1CSF8F6W8JKW` | succeeded | 20000 | 20000/20000/20000 | 10 | **0** | CUSTOMER_ID 4, DOB 12613 |
| Oracle append | `j_01M51R8WMPZ37EQ1H9QFQN6W9N` | succeeded | 20000 | 20000/20000/20000 | 9 | **0** | DOB 12560 |
| Oracle in_place | `j_01M51R8WMTRW7NZEVAWEG29T43` | succeeded | 20000 | 20000/20000/20000 | 9 | **0** | DOB 12505, NATIONAL_ID 2 |
| Mongo table | `j_01M51R8WKMJJ0E4T6282D87XC2` | succeeded | 20000 | 20000/20000/20000 | 10 | **0** | $.customerId 2, $.profile.dob 12615 |
| Mongo files | `j_01M51R8WMZ1E6JF98P117SZ7EG` | succeeded | 20000 | 20000/20000/20000 | 10 | **0** | $.customerId 5, $.profile.dob 12483 |
| Mongo append (named, **empty**) | `j_01M51R8WN6ANFAWWBCY2FYVC7V` | **succeeded** (round 1: `target_invalid`) | 20000 | 20000/20000/20000 | 10 | **0** | $.customerId 10, $.profile.dob 12613 |
| Mongo in_place | `j_01M51R8WNBXAJ1Y3692ABR6RAF` | succeeded | 20000 | 20000/20000/20000 | 10 | **0** | $.customerId 6, $.profile.dob 12493 |

**Per-column counts** (`logs/42-per-column.txt`, survivors / non-null):
- Every transformed column of every run is `0/20000`.
- The exceptions are columns populated in fewer rows: tax ids `0/11989` and PG `notes` `0/5979`.

**Transformed columns:**
- PG: customer_id, date_of_birth, email, full_name, mobile, national_id, notes, tax_id. In append/in_place the key is reviewed `keep`.
- Oracle: ADDR_LINE1, CUSTOMER_ID, DOB, EMAIL, FULL_NAME, MOBILE, NATIONAL_ID, NOTES, PIN_CODE, TAX_ID.
- Mongo: iban, address line1/pin, email, mobile, customerId, taxId, notes, dob, fullName.

**Target values found elsewhere in the source column.** None of these is a same-row survivor:
- **customer ids, 2–10 per run.** These are FF1 crossings. Both id spaces are 8 digits, contiguous over 20,000 values (`logs/41-customer-id-domain.txt`), so about 4.0 per run is expected. The 11 runs average 4.7.
- **dates of birth, about 12,500.** `date_shift` over a small domain.
- **national_id 1–2.** From the reviewer-chosen `fpe_digits` on NINOs, as in round 1's N2.

## 3. D3 — typed confirmation and production writes

**At submission** (PG, Oracle, Mongo):
- `in_place` without `confirm_write` → **422 `confirm_write_required`** (3/3).
- `in_place` with `confirm_write: false` → **422** (3/3).
- `append` without `confirm_write` → **422** (3/3).
- `in_place` confirmed with `expected_rows: 100` (20,000 selected) → 202, then **failed `row_count_mismatch`** with 0 rows written:
  - PG `j_01M51R8SYPTSYW2MW45QR3FYEP`;
  - Oracle `j_01M51R8SZD5ZPSZMF009E2XGJP`;
  - Mongo `j_01M51R8T04TYP3J74B50FVFPR1`.
- The in-place copies' value-free digests were **unchanged** on all three engines.

**Packs** (`logs/38-evidence-d3.txt`):
- Every succeeded `append` and `in_place` pack records `confirm_write: true`, `expected_rows: 20000` and `write_ledger_cleared: true`. That is 6 runs from phase 2 and 3 MongoDB appends from phase 7.

**Production connections** (`p3_prod.py`). The probe used a DB key with scopes `obfuscate synthesize jobs detect` and no admin:
- The key `key_01M51R90VWQNCAWX1CV854SVTZ` was minted with `Cache-Control: no-store` and disabled at the end (HTTP 200).
- `l2r2-pg-prodtag` is tagged production; `l2r2-pg-prodclone` is production only through its credential.
- Results:
  - **non-admin:** in_place, same-table append, and obfuscate/synthesize `table` into either connection → **403 `admin_scope_required`** (8/8);
  - non-admin creating a connection that names the production credential → **403**;
  - **control:** the same non-admin key, dry run into non-production `bank-uat` → 202, `succeeded`/`dry_run`;
  - **admin:** passes the gate (202). The jobs then fail `target_invalid` because the login is read-only, as expected (round 1's N3);
  - non-admin `POST /jobs/{id}/retry` of the admin's production run → **403**.

**Console:**
- `ConfirmWriteModal` sends only through `canConfirmWrite(typed, objectFqn, blocked)`, and `ConfirmDialog` disables confirm until the typed text matches `requireText`.
- `vitest` on `state.test.ts` + `jobs.test.ts`: **78 passed**.

## 4. Consistency check (`POST /job-groups/{id}/consistency-check`, sample 1000)

**Result:** HTTP 200, **passed**, 3/3 consistent pairs, `all_members_matched` 1000, `methods_agree`, no problems.

**Anchor:** `CUSTOMER_ID` `fpe_digits`. The three members are:
- Mongo `$.customerId` (`prefixed_digits`, the anchor);
- Oracle `CUSTOMER_ID` (numeric);
- PG `customer_id` (numeric).

**Pairs:** (0,1), (0,2) and (1,2), each checked 1000, **matched 1000**, coverage 1.0.

**Attributes:**
- Agreed 1000/1000: EMAIL, PERSON_NAME, PHONE, NATIONAL_ID `det_token` (Oracle–PG), STREET_ADDRESS.
- Agreed 582/582: PAN_IN.
- Agreed 1000/1000 but not counted toward the pass: POSTAL_CODE `generalize`.
- DATE_OF_BIRTH `date_shift` agreed only **1/1000**. It does not count toward the pass, by design (round 1's N1, unchanged).

## 5. Resume after kill

**Shipped tests.** `DPS_TEST_REQUIRE_SERVICES=1 pytest -m slow` against the real containers, with their own metadata DB and subprocess workers. **11 passed** in 3:43:
- `tests/connectors/test_resume_after_kill.py` (PG): table truncate_insert, table upsert, in_place, append offset named, append generate same, files JSONL.
- `tests/e2e/test_m3_resume_after_kill.py`: Oracle in_place and append, Mongo in_place and append, Oracle key-less table cross-connection.

**`probe_resume_more_test.py`.** Round 1's probe for the modes no shipped test kills on Oracle/MongoDB. It reuses the e2e case builders unchanged, and those helpers are unchanged since round 1. **4 passed** in 2:13:

| Case | Rows a/b | Digests equal | Survivors a/b | Partitions / resumed |
|---|---|---|---|---|
| oracle-table | 3000/3000 | yes | 0/0 | 3/3 |
| oracle-files | 3000/3000 | yes | 0/0 | 3/3 |
| mongo-table | 3000/3000 | yes | 0/0 | 3/3 |
| mongo-files | 3000/3000 | yes | 0/0 | 3/3 |

Every write mode is killed and resumed on every engine, 15/15. As in round 1, I did not kill the shared compose worker.

## 6. D-A post-write identifier check (`p5_da.py`)

**A. Real synthesize `table` runs**, 20,000 rows each:

| Engine | Job / pack | Scope | rows_read t/s | checked / independent non-null / in_source / independent in source | Passed |
|---|---|---|---|---|---|
| Oracle | `j_01M51R9C50ARK4PAQJ3BA6C40E` / `ev_01M51RRXE6AW94VQW71JJA1KXA` | whole_table | 20000/20000 | NATIONAL_ID 20000/20000/0/0; TAX_ID 11989/11989/0/0 | yes |
| MongoDB | `j_01M51R9C58149C0E19MV8PZ22Q` / `ev_01M51RMC1SFDWX7SP30G9RXX4R` | whole_table | 20000/20000 | `$.accounts[*].iban` 20000/20000/0/0; `$.kyc.taxId` 11989/11989/0/0 | yes |
| PostgreSQL | `j_01M51R9C5F9XN0TA8GHNP5J91Z` / `ev_01M51RKX55PQWSSZTNFTF1343N` | whole_table | 20000/20000 | national_id 20000/20000/0/0; tax_id 11989/11989/0/0 | yes |

`checked` equals the rows (20,000) wherever the column is populated. The tax ids are populated in 11,989 source rows, and `checked` equals that number.

**B. Planted, in process.**
- **Plant.** A copy of each synthetic target, with the row at key order **15,000** carrying source row 15,000's real identifier. The value is copied inside Oracle, or in process for MongoDB, and never printed.
- **Independent check.** It locates exactly 1 source value in each copy, at position **[15000]**.
- **The worker's own `check_identifiers`** (same sha256 as the image):
  - Oracle NATIONAL_ID: **checked 20000, in_source 1**, rows_read 20000/20000, review_columns [NATIONAL_ID];
  - MongoDB `$.accounts[*].iban`: **checked 20000, in_source 1**, rows_read 20000/20000, review_columns [`$.accounts[*].iban`].

**C. Planted, through a real run.** A synthesize `upsert` of one row into a second planted copy:
- Oracle `j_01M51RS3F38NKB7QRQWCW1PR7J` / `ev_01M51RSFW1SZ5R4N9VGGAM2QT5`;
- MongoDB `j_01M51RS3F9G7AS6TEJXMWRK1ST` / `ev_01M51RSFTNT1HQHYGXHKWRW3BW`.

In each run's own pack:
- `rows_read` target **20,001** / source 20,000;
- the planted column has `checked` 20,001 and **in_source 1**, equal to the independent count;
- `review_columns` names it;
- `review_required: true`, **`passed: false`**.

## 7. B1 re-check — a policy body or `@name` (`p6_b1.py`, 500-row `table` runs)

The stored policy `l2r2-pol` was created from `gdpr-pseudonymise` (201).

| Case | Job | State | Rows | Pack | Pack policy {name, version} | Verification |
|---|---|---|---|---|---|---|
| PG obfuscate inline | `j_01M51RB0B5YYKRQS15K9DAFMQC` | succeeded | 500 | `ev_01M51RM8EWHQ22ZTZHWRC0NJ4M` | l2r2-inline, 1 | passed |
| PG obfuscate `@l2r2-pol` | `j_01M51RB0BEBH9NX5PZRVHPYT65` | succeeded | 500 | `ev_01M51RMEBBHZ7N27YJT9CJHQMP` | l2r2-pol, 1 | passed |
| Oracle obfuscate inline | `j_01M51RB0BMVDKS7YDYCMMRBN5C` | succeeded | 500 | `ev_01M51RMJ57872TQYHPBWT48YPN` | l2r2-inline, 1 | passed |
| Oracle obfuscate `@l2r2-pol` | `j_01M51RB0BTXZ12FPXV8WJZ8BCD` | succeeded | 500 | `ev_01M51RMMA2DMJVZ78RWMEBRKGB` | l2r2-pol, 1 | passed |
| Mongo obfuscate inline | `j_01M51RB0BZV1WR902VZK3JHPPZ` | succeeded | 500 | `ev_01M51RMNJ4XM26724F9EJ9ETFH` | l2r2-inline, 1 | passed |
| Mongo obfuscate `@l2r2-pol` | `j_01M51RB0C3EDX7GPKFWEC7ZXVB` | succeeded | 500 | `ev_01M51RMNJ3VFZM0JQ272VTSZHC` | l2r2-pol, 1 | passed |
| Oracle **synthesize** `@l2r2-pol` | `j_01M51RB0C92W41956KF78M7VVD` | succeeded | 500 | `ev_01M51RMTNPY2AQABRHZ000VBHC` | l2r2-pol, 1 | passed |
| Mongo **synthesize** inline | `j_01M51RB0CDQ26YGKCBMGD71XGT` | succeeded | 500 | `ev_01M51RMY1HSTC52TKB0Q06JR7D` | l2r2-inline, 1 | passed |
| Control: PG preset name | `j_01M51RB0CJ0MM9JVZC4ANH19XK` | succeeded | 500 | `ev_01M51RMWK139KMT12WHTXGKCES` | gdpr-pseudonymise, 1 | passed |

- The job rows keep the F-18 sealed label (`name`, `sealed`, `sha256`, and `version` for `@name`).
- **Worker:** 0 `finalize_failed` lines since the run started, against 3 per job in round 1.
- **Source vs target** on the 7 obfuscate targets: 500/500 aligned. Same-row equality occurs **only** in the default review's kept columns.
- **Coverage gap closed.** The Oracle and MongoDB `@name` cases and the Oracle/MongoDB synthesize cases had no dedicated test in the fix round; these runs prove them live.

## 8. R2-B1 — MongoDB: a sparse existing collection missing a *nested* leaf is refused (blocking)

**Symptom** (`p7_mongo_append.py`, `p7b_sparse_isolate.py`; all obfuscate, append confirmed):

| Case (target: an existing collection) | Job | Result |
|---|---|---|
| A: append into the source's own collection (20,000 + 20,000) | `j_01M51RH60BY3AGS31D62NV9MFR` | succeeded. 20000/20000/20000 aligned, 0 survivors, 20,000 originals untouched. |
| B: named, **empty** collection (round 1 B2) | `j_01M51RH60H4ERQDBZV18M731AV` | **succeeded**, 0 survivors |
| D: named, holding 1 obfuscated doc with every leaf | `j_01M51RH60V2005VNJJ4QCY1ZY9` | succeeded. 0 survivors, the doc kept (20,001). |
| E / F: `table` truncate_insert / upsert (500 rows) into an **empty** existing collection (round 1 B2) | `j_01M51RH6106HY3N26F6EBA4ANM` / `j_01M51RH616GKT3M1T5NDB5QAZE` | **succeeded**. 500/500 aligned, 0 survivors, `existing_rows` 0. |
| G: control, an existing doc holds the kept `$.riskScore` as text | `j_01M51RH61CN00FRDEK9YBHQEEA` | `target_invalid` "$.riskScore: a int value cannot go to a text column". Nothing written. **Correct.** |
| I2: named, 1 doc with `tags: []` (round 1's sparse case) | `j_01M51RWKHGXSZ3FD7REYV2PE64` | **succeeded** (500 rows) |
| I3: named, 1 doc without `kyc` at all | `j_01M51RWKHRGGMA6DMTWC36D9YT` | succeeded |
| I4: named, 1 doc whose `profile` lacks `dob` but has `fullName` | `j_01M51RWKHZAQ2RCPRSGDAN4149` | succeeded |
| **I1: named, 1 doc whose `kyc` lacks `taxId` but has `verifiedAt`** | `j_01M51RWKH7P6JR2DZN67EBEWQZ` | **failed `target_invalid` "$.kyc.taxId: a text value cannot go to a timestamp column"**, 0 written |
| **C: named, 1 doc with `tags: []` and no `kyc.taxId`** | `j_01M51RH60PAAM4GVRFXHMXRZ62` | **failed**, same error |
| **`table` truncate_insert / upsert into a collection whose 1 doc lacks `kyc.taxId`** | `j_01M51RXSGR9K7TEXR987NJ5X5Q` / `j_01M51RXVFXM4ZETM21EPHBWW7E` | **failed**, same error, 0 written |

**Pure reproduction** (`logs/75-sparse-isolate.json`, `77-sibling-kind.txt`). It uses the deployed `infer_shape` and `targets._check_existing` with made-up values. Against a source with `kyc.taxId` (text) and `kyc.verifiedAt` (timestamp), a target sample:
- lacking only `kyc.taxId` → **refused**;
- lacking `kyc`, having both, or lacking only `verifiedAt` → accepted.

Conversely, a target whose `kyc.verifiedAt` is **text** while `taxId` is present is **accepted**: the sibling leaf is never kind-checked. The same mismatch on top-level `riskScore` is refused.

**Root cause:**
- `mongo_docs.infer_shape` names every document column by its **top-level field** (`mongo_docs.py:143`, `name=top_level(g)`): `$.kyc.taxId` and `$.kyc.verifiedAt` are both `kyc`.
- `targets._check_existing` (`targets.py:996-1021`) iterates written *names*. It compares `source_shape.column(name)` with `target_shape.column(name)`, and `TableShape.column` (`base.py:147`) returns the **first** column with that name.
- So for each top-level field it compares the source's first leaf with whichever leaf the target sample shows first.
- When the sample lacks `$.kyc.taxId`, the source's text `taxId` is compared with the target's timestamp `verifiedAt` and refused.
- This contradicts the function's own contract (docstring: "a leaf its documents do not show yet is written as it is — and only the leaves it shows are kind-checked") and the fix round's claim of "named append into … a sparse collection".
- The call sites are `targets.py:1157` (`table`) and `:1187` (`append`/`in_place`).

**Why the tests miss it.** `test_a_named_append_into_a_collection_whose_documents_lack_a_leaf` removes whole top-level fields (`tags`, `accounts`, `kyc`). It never removes a nested leaf under a parent that is present.

**Impact:**
- **No leak, no write.** The run fails closed in prepare.
- **What gets refused.** In the demo data `kyc.taxId` is absent from 40 % of profiles (8,011 / 20,000). Any existing target collection whose sampled documents (at most 500) all lack it — a small seeded collection, or a UAT collection loaded from customers without tax ids — refuses every named append and `table` load from a source that has tax ids. The error message names a leaf the target does not have.
- **Missed check.** A wrong-kind sibling leaf goes unchecked. This is weaker validation only.
- **Workarounds:** append into the source collection, or into an empty or new collection.

**Fix (suggested).** For a sampled (MongoDB) target, check per leaf path:
1. For each written top-level name, iterate the source shape's columns of that name.
2. Look each one up by path with `column_at(target_shape, col.path, col.name)`. It already returns `None` for a sibling leaf of the same field.
3. Skip the leaves the sample does not show, and kind-check every leaf it shows.

**Tests (red first), through `prepare` on a real MongoDB:**
- a named append, and `table` truncate_insert/upsert, into a collection whose one document has `kyc` without `taxId`: red today;
- a collection whose document holds a kept nested non-first leaf with an incompatible kind (`kyc.verifiedAt` as text, with `taxId` present): must be `target_invalid`, accepted today.

## 9. Proofs and hygiene (counts only)

- **Logs.** I scanned every project container's logs since 07:00Z (`log_scan_source.py`):
  - 129,792 needles from the three sources (20,000 e-mails, 55,565 names, 54,227 digit forms); self-test 4/4;
  - **0 hits** in all 15 containers (app 1,392 lines, worker 186, mongo 6,453, oracle 112, …).
- **Worker since 07:00Z:** 0 warning or error events; 0 `finalize_failed`.
- **App since 07:00Z:**
  - 32 `rate_limited`, all on the shared static admin key: 28 `/api/v1/synthesize`, 2 `/obfuscate`, 2 `/jobs/run`. I call none of the first two, and none of my 45 submissions got a 429.
  - 2 `auth_rejected` on `/api/v1/detect`, which I never call.
  - Both come from other verifiers.
- **Database errors since 07:00Z:**
  - **source-pg:** exactly 1, `permission denied for schema bank_uat`. This is my admin probe through the read-only production login, and it was expected.
  - **dps-db:** 1, `column "scan_id" does not exist`. It came from a hand-written `select tenant_id, scan_id, count(*) …` by another verifier, not from product code.
- **LLM.** `dps_llm_raw_pii_sent_total 0.0`.
- **Write ledgers.** **0 rows** left for any of my 45 jobs in every `dps_write_ledger`: PG, Oracle and MongoDB.
- **Cleanup** (`p9_cleanup.py`, `logs/99-cleanup.json`):
  - 9 PG tables, 11 Oracle tables and 23 MongoDB collections dropped, all `l2r2`-prefixed;
  - 6 MinIO objects under `dps-exports/l2r2/` deleted;
  - the 4 probe connections deleted (204).
- **Left in place:** the disabled key `key_01M51R90VWQNCAWX1CV854SVTZ`, the stored policy `l2r2-pol` (there is no delete route), job group `grp_01M51R8WK1CPRVCDWYZPSV0C3A` and the job history. The stack is left running for the other verifiers.

## 10. Non-blocking notes

- **N1, unchanged.** `date_shift` dates of birth differ across engines for the same customer (consistency attribute 1/1000). This is excluded from the pass by contract.
- **N2, unchanged.** FF1 crossings: customer-id tokens equal another real customer's id 2–10 times per 20,000-row run. That is consistent with 8-digit contiguous id spaces (expected about 4.0, observed mean 4.7). The reviewer-chosen `fpe_digits` on NINOs gives 1–2.
- **N3, unchanged.** An admin run through a read-only production login fails with `target_invalid`. A `target_not_writable` code would be clearer.

## 11. For the fix round

1. Fix R2-B1 in `targets._check_existing` (per-leaf lookup for sampled shapes) with the red-first tests of §8.
2. Rebuild the app and worker.
3. Re-run `p7b_sparse_isolate.py`:
   - I1 must succeed;
   - the pure case `a` must be accepted;
   - the sibling-kind case of `77-sibling-kind.txt` must be refused.
4. Run `p1_setup.py` first, then `p7_mongo_append.py`: C must succeed with 0 survivors.
5. Re-run `-m "mongo"` and `tests/e2e/test_m3_resume_after_kill.py`.

The rest of this lens needs no re-run.
