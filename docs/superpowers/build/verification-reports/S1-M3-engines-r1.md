# S1-M3 — post-merge verification of `main` after the M3 merge: ENGINES lens, round 1

**Verdict: FAIL — 4 blocking findings (B1–B4).** Obfuscation on all three engines, the three-way
consistency join, the D-A key pseudonyms and resume-after-kill all hold. But four L2 paths of the
merged tree are broken, or leak, on Oracle and MongoDB:

- **B1.** Synthesize of a key-less (ROWID-paged) Oracle table always fails closed with `leakage`.
- **B2.** Synthetic CRM notes keep each source row's **real first name** (20 000 / 20 000 rows,
  Oracle and MongoDB, surrogate and copula).
- **B3.** MongoDB → `files` fails in every format (`TypeError` → `internal_error`).
- **B4.** Oracle → `files` in `parquet` (the default format) fails on `TIMESTAMP(9)` columns
  (`ArrowTypeError` → `internal_error`).

| | |
|---|---|
| Date | 2026-10-14, 17:45–18:36 IST |
| Tree verified | `main` @ `144cc65` (clean before and after; `git status` empty). This contains the M3 merge `b878c50`, the integration fixes `e672072..e298f70` and docs-only commits after them. |
| Stack | compose project `data-privacy-services`, started by the gates verifier and left running (not restarted, no `down`): app 8400, dps-db 5441, source-pg 5442, MinIO 9010, Oracle 1522, Mongo 27018, tokenizer-mock 8430. NER: `patterns-only` (`/readyz`). |
| Python | `/Users/neelu/document_intelligence/data-privacy-services/.venv`, contract §12.2 env (`env.sh` in the probe folder), `PYTHONDONTWRITEBYTECODE=1`, pytest with `-p no:cacheprovider` |
| Probes and outputs | `/Users/neelu/.cache/dps-build/reports/S1-M3-engines-r1/`: scripts `p1`–`p7`, `repro_rowid.py`, `kill/test_probe_m3_*.py`; outputs in `*.out`, `*.json`, `*.jsonl` and `*.log` |
| Repository files modified | none |
| Shared demo sources | unchanged: counts and seed markers are identical before and after (`src_state.before.json` = `src_state.after.json`; seed 7, 20 000 customers on PG, Oracle and Mongo). Nothing was reseeded and no test that reseeds was run. |
| Values in my outputs | 0. `selfscan.py` checked 149 732 source values against all 38 output files, after the last run. |

All counts below are counts only. No value, token, key or seed was printed or written. The group
keys and seeds stayed in memory inside the probe process that created the group.

## Blocking findings

### B1. Synthesize of a key-less Oracle table always fails: `leakage` at prepare, nothing written

**Where:** `dps/layers/connectors/jobs_run.py:1731` (`copied_personal_columns`).

```python
written = set(plan.target.write_columns)
if not plan.target.generated_key:
    written |= set(plan.key)          # plan.key == ["ROWID"] for a key-less Oracle table
replaced = {p.name for p in plan.columns} | {p.name for p in plan.pseudonyms} | sampled
return sorted((written & (classified | set(plan.key))) - replaced)   # -> ["ROWID"]
```

WP-I commit `ae09409` stopped pseudonymising the `ROWID` pseudo key in `targets._identifiers`, which
is correct: ROWID is read-only and never written. But the prepare guard still adds `plan.key` to the
written columns, so it reports ROWID as "a key it would write with its real value". The guard's own
comment says it is "never reached"; for every key-less Oracle table it is reached.

**Evidence:**

1. **Live stack.** `CRM.ADDRESS_HISTORY` → `CRM_MASKED.ADDR_SYN_ENG1` (surrogate, job
   `j_01M4X617WV3NCX42FS0GFSBR5P`) and → `ADDR_COP_ENG1` (copula, `j_01M4X617XNDTGG1W4F69PKZD9T`)
   both ended `failed / leakage`, with the message "1 classified column(s) or key(s) would be copied
   into synthetic rows; nothing was written". They had 0 partitions and 0 rows, and the evidence
   says `errors: [{code: leakage, where: prepare}]`.
2. **Offline repro** (`repro_rowid.py` / `.out`), on the accepted snapshot through the same planning
   functions:
   - `copied_personal_columns` → `["ROWID"]`;
   - with ROWID left out of the key → `[]`.
3. **Isolated, in process** (`kill/test_probe_m3_kill.py::test_keyless_oracle_synthesize`, results
   in `kill/kill_results.jsonl`):
   - **As built:** `failed / leakage`.
   - **With the guard ignoring pseudo keys** (the suggested fix, monkeypatched): `succeeded`, 3 000
     rows. The target holds `CUSTOMER_ID, ADDR_LINE1, CITY, PIN_CODE, VALID_FROM`, with no ROWID.
     `CUSTOMER_ID` has 0 values in the source and 3 000 / 3 000 in the pseudonym band
     [10⁹, 2·10⁹). `ADDR_LINE1` has 0 values in the source. Verification passed.
   - Nothing else in the pipeline blocks the run once the guard is fixed.

**Fix:**

- Leave pseudo key columns out of the written keys in `copied_personal_columns`:
  `written |= {k for k in plan.key if k not in pseudo}`, where pseudo is the set of
  `shape.columns` with `pseudo=True`.
- Add a test with a ROWID-keyed snapshot, asserting the guard returns `[]`.
- Add an integration test: synthesize a key-less Oracle table into `table`. It must succeed, write
  no ROWID column, and pseudonymise `CUSTOMER_ID`.
- The existing test `tests/test_da_m3_engines.py::test_a_rowid_pseudo_key_is_never_pseudonymised`
  only covers `pseudonym_columns`.

### B2. Synthetic notes carry the source row's real first name (D-A), on Oracle and MongoDB

**Where:** `dps/engine/batch.py:416` (`_row_value_spans`, from WP-I commit `3f75a0b`).

- In a synthesize job, `_row_value_spans` replaces a planned column's value only where the **whole
  value** repeats in the row's free text.
- The M3 demo notes are written as "KYC call with **<first name>**; follow up at <e-mail> or
  <phone>. Prefers branch in <city> …" (`tools/seed_model.py`). Only the first-name part of
  `FULL_NAME` occurs, so nothing matches it.
- Without NER (`patterns-only`, the stack default), no detector finds a first name in free text.
- The integration added this mechanism with the stated invariant "a synthetic table never carries
  a source value", and covered the city. The first name in the same sentence was missed.
- The synthetic row's own `FULL_NAME` is a different surrogate, so the note also contradicts its
  row.

**Evidence** (`p5b_text_aligned.py`, `p5c_firstname.py`; `compare_text_eng1.json`,
`compare_firstname_eng1.json`). Rows were aligned through columns the run keeps unchanged:
`LAST_CONTACT_AT` on Oracle, and balance + `verifiedAt` + `riskScore` on MongoDB. This agrees 100 %
with the alignment through the keyed `CUSTOMER_ID` / `_id` pseudonyms recomputed from the group
seed in `p3`.

| Synthetic target | Name slot = **source** first name | = synthetic first name | Own e-mail / phone / city in notes |
|---|---|---|---|
| Oracle `CUSTOMERS_SYN_ENG1` (surrogate) | **20 000 / 20 000** | 0 | 0 / 0 / 0 |
| Oracle `CUSTOMERS_COP_ENG1` (copula) | **20 000 / 20 000** | 0 | 0 / 0 / 0 |
| MongoDB `profiles_syn_eng1` (surrogate) | **20 000 / 20 000** | 0 | 0 / 0 / – |
| PostgreSQL `customers_syn_eng1` | – (the PG seed notes hold no names) | – | 0 |

- **Isolated repro** (`kill/test_probe_m3_firstname.py`, 600 rows, default policy, results in
  `kill/firstname_results.jsonl`): 599 / 600 rows keep the source first name on Oracle and on
  MongoDB.
- **Controls:** the source checked against itself finds 599 / 600, so the probe does see the
  pattern. 0 rows keep the e-mail or phone.
- **The row is also linkable.** The Oracle synthetic rows keep the source `LAST_CONTACT_AT`, a
  TIMESTAMP(9) that is unique per row (see N3). The MongoDB synthetic documents keep `balance`,
  `verifiedAt` and `riskScore`.
- **Nothing flags it.** The evidence pack says `verification.passed: true, review_required: false`.

**Fix (suggested):**

- In synthesize mode, also take the word tokens of planned `PERSON_NAME` values as `row_value` spans.
  Use first and last names of ≥ 3 characters, whole words, case-insensitive. Ideally do the same
  for other multi-word entities: street address parts.
- Or, when NER is unavailable, do not span-surrogate free text in synthesize mode: generate it or
  redact it.
- Add an e2e regression on the M3 demo shape that checks the row-aligned first name is absent from
  synthetic notes, on Oracle and MongoDB.
- The same root cause also affects obfuscate (non-blocking N1).

### B3. MongoDB → `files` fails in every format: `TypeError`, then dead partitions, then `internal_error`

**Where:** `dps/layers/connectors/files.py:76` (`_text_value`) and `:92` (`_json_value`).

- A document field that holds a sub-document or an array is passed whole to `json.dumps` with no
  converter for BSON / driver types. The jsonl writer and `_text_value` (csv, and parquet's string
  columns) then raise `TypeError` on:
  - a nested `datetime` (`profile.dob`, `kyc.verifiedAt`);
  - a nested `Decimal128` (`accounts[*].balance`).
- The parquet schema is also built from the field's first leaf kind: `profile` → `timestamp[us]`,
  `accounts` → `decimal`.

**Evidence:**

1. **Live stack** (`p7_files.py`, target `exports-s3`). `crm.profiles` obfuscate → jsonl and
   obfuscate → parquet were accepted (202). Their partitions were retried 5 times (`dead`) and both
   jobs ended `failed / internal_error` after about 12 min. The worker logged `partition_failed
   error=TypeError` only. The synthesize jobs → jsonl / parquet failed the same way; their final
   state is recorded in `files_eng1.json`.
2. **Isolated** (`kill/test_probe_m3_files.py`, `kill/files_results.jsonl`), with `_Spool._emit`
   instrumented to name the failing columns:
   - jsonl, csv and parquet all fail on `accounts` (leaf types `Decimal128`, `str`), `kyc`
     (`datetime`, `str`) and `profile` (`datetime`, `str`).
   - Controls succeed: PostgreSQL jsonl and parquet, Oracle jsonl and csv.
3. **Under kill:** `kill/test_probe_m3_kill.py[mongo-files-obfuscate]` fails at the reference run
   with `internal_error`.

HANDOFF.md line 139 (the MongoDB run-source note) says "`files` not yet tested for documents". The
path is not just untested: it is broken, and the API accepts it with 202.

**Fix:**

- Serialise nested values with a converter in the files writer: datetime → ISO 8601,
  `Decimal128` / `Decimal` → text, `ObjectId` → hex, `Int64` → int, bytes → hex.
- Give document fields a `string` (JSON text) Arrow type.
- Or refuse `files` for MongoDB sources at submit with a clear 422 until this is supported.
- Add tests for jsonl, csv and parquet with the demo document shape.

### B4. Oracle → `files` in `parquet` (the default `FilesSpec.format`) fails on TIMESTAMP(7–9): `ArrowTypeError`, then `internal_error`

**Where:** `dps/layers/connectors/files.py:102` (`_arrow_type`).

- The connector delivers Oracle `TIMESTAMP(7-9)` as FF9 **text**, by design
  (`oracle_sql.py`: "TIMESTAMP(7-9) crosses the driver as FF9 text").
- `_arrow_type` still maps `kind == "timestamp"` to `pa.timestamp("us")`, so `pa.Table.from_pydict`
  raises `ArrowTypeError` on `LAST_CONTACT_AT`.
- Every Oracle table with a TIMESTAMP(7-9) column cannot be written to the default files format:
  `CRM.CUSTOMERS` and `CRM.INTERACTIONS` in the demo.

**Evidence:**

1. **Live stack** (`p7_files.py`). `CRM.CUSTOMERS` obfuscate → parquet: `failed / internal_error`
   (job `j_01M4X746FDD4GKFRKMCJG51RPS`, 5 attempts, `partition_failed error=ArrowTypeError`).
   Synthesize → parquet fails the same way (`files_eng1.json`). The same table → jsonl succeeded,
   20 000 rows, for obfuscate and for synthesize.
2. **Isolated** (`kill/files_results.jsonl`): failing column `LAST_CONTACT_AT` (kind `timestamp`,
   `TIMESTAMP(9)`, Arrow `timestamp[us]`, value type `str`). jsonl and csv pass.

**Fix:**

- Map `ts_precision > 6`, and TIMESTAMP WITH TIME ZONE text, to `pa.string()` to keep FF9 exact,
  or to `timestamp("ns")` with a parser.
- Add a parquet test on the Oracle demo shape. A resume-after-kill case for `files` on Oracle
  already passes for jsonl (below).

**Live `files` matrix** (`p7_files.py`, `files_eng1.json`; group `grp_01M4X746E8B0TB8971WYBDP07E`,
target `exports-s3`; every run accepted with 202). Each failure took 5 attempts per partition (about
12–18 min) before the partition went `dead`. The evidence status of every failed run is
`incomplete`.

| Source | obfuscate → jsonl | obfuscate → parquet | synthesize → jsonl | synthesize → parquet |
|---|---|---|---|---|
| MongoDB `crm.profiles` | **failed `internal_error`** (TypeError) | **failed `internal_error`** (TypeError) | **failed `internal_error`** | **failed `internal_error`** |
| Oracle `CRM.CUSTOMERS` | succeeded, 20 000 rows, passed | **failed `internal_error`** (ArrowTypeError) | succeeded, 20 000 rows, passed | **failed `internal_error`** (ArrowTypeError) |

## What holds (evidence)

### 1. Obfuscate on all three engines → `table` targets, per-column source vs target

**Setup.** One job group (`grp_01M4X5XCA7V2JSFB3GWPCZ4YN7`, kid `k_8843d7e6ca7b9bfd`). The default
policy was used, with snapshots discovered and accepted through the API. All jobs `succeeded` with
verification `passed` and `review_required: false`. Script `p2_obfuscate.py`, comparison
`p4_compare_obf.py`, output `compare_obf_eng1.json`.

**Metrics.**

- *row_same* counts aligned rows where the target value equals the source value.
- *set* counts target values found anywhere in the same source column.
- A survivor is a sensitive column (method ≠ keep) with row_same > 0.

| Source → target | Rows src / tgt | Alignment (aligned) | Sensitive columns: row_same | set hits (all explained) |
|---|---|---|---|---|
| PG `bank.customers` → `bank_uat.customers_eng1` | 20 000 / 20 000 | 8 kept columns (20 000) | **0** on customer_id, full_name, date_of_birth, national_id, tax_id, email, mobile, notes | customer_id 2 (FPE onto another real id), date_of_birth 12 457 (date domain) |
| PG `bank.accounts` → `accounts_eng1` | 30 000 / 30 000 | account_id (30 000) | **0** on customer_id, account_number, iban, ifsc | customer_id 3 |
| Oracle `CRM.CUSTOMERS` → `CRM_MASKED.CUSTOMERS_ENG1` | 20 000 / 20 000 | LAST_CONTACT_AT (20 000) | **0** on CUSTOMER_ID, FULL_NAME, NATIONAL_ID, TAX_ID, EMAIL, MOBILE, DOB, ADDR_LINE1, PIN_CODE, NOTES | CUSTOMER_ID 2, DOB 12 457 |
| Oracle `CRM.ADDRESS_HISTORY` (key-less, ROWID) → `ADDR_ENG1` | 40 000 / 40 000 | CITY+VALID_FROM (38 993 unique) | **0** on CUSTOMER_ID, ADDR_LINE1, PIN_CODE | CUSTOMER_ID 4 |
| Oracle `CRM.KYC_DOCUMENTS` (CHAR(12) key) → `KYC_ENG1` | 20 000 / 20 000 | DOC_REF (20 000) | **0** on CUSTOMER_ID, DOC_NUMBER | CUSTOMER_ID 2 |
| Oracle `CRM.INTERACTIONS` (TS(9) composite key, JSON CLOB) → `INTER_ENG1` | 40 000 / 40 000 | EVENT_TS+CHANNEL (40 000) | **0** on CUSTOMER_ID, AGENT_EMAIL, PAYLOAD | CUSTOMER_ID 4 |
| Oracle `LOANS.APPLICANTS` (string key) → `APPL_ENG1` | 5 000 / 5 000 | APPLICATION_ID (5 000) | **0** on CUSTOMER_ID, APPLICANT_NAME, PAN, MOBILE | CUSTOMER_ID 1 |
| MongoDB `crm.profiles` → `crm_uat.profiles_eng1` | 20 000 / 20 000 | `_id` (20 000) | **0** on customerId, fullName, dob, email, mobile, addresses line1/pin, kyc.taxId, accounts iban, notes | customerId 2, dob 12 665 |

- **Identifier sweep** (whole cells and tokens in text, against 13 identifier classes of all three
  sources). Hits fall in two places, both expected for obfuscate:
  - columns the policy keeps as unclassified keys, which hold real `relationship_manager_id`,
    `account_id`, `DOC_REF`, `APPLICATION_ID` and `_id` values;
  - the 1–4 FPE customer-id collisions per table.

  The output is labelled "pseudonymised — still personal data".
- **Oracle key-less target:** 5 columns, no ROWID.
- **Free text:** see N1. The first names and the kept city survive inside Oracle and MongoDB notes.

### 2. Three-way consistency check (PG ↔ MongoDB ↔ Oracle), overlap > 0

**Derived members.**

- Oracle `$.CUSTOMER_ID`: numeric; the anchor.
- MongoDB `$.customerId`: `prefixed_digits`, width 8, prefix 5.
- PG `$.customer_id`: numeric.
- All three use `fpe_digits` with the same `params_hash`.

| Check | HTTP | Result | Pairs consistent | Coverage per pair | Time |
|---|---|---|---|---|---|
| derived, sample 1 000 | 200 | passed | **3 / 3** | 1000 / 1000 each (1.0) | 195 ms |
| derived, sample 10 000 | 200 | passed | **3 / 3** | 10000 / 10000 each (1.0) | 979 ms |
| explicit pair CUSTOMERS ↔ ADDRESS_HISTORY (same group) | 200 | passed | 1 / 1 | 1000 / 1000 | 70 ms |

- **Attributes** (sample of 10 000):
  - EMAIL, PERSON_NAME, PHONE and STREET_ADDRESS agreed 10 000 / 10 000;
  - NATIONAL_ID 10 000 / 10 000 (Oracle and PG);
  - PAN_IN 6 004 / 6 004;
  - POSTAL_CODE agrees but does not count toward the pass;
  - DATE_OF_BIRTH `date_shift` agreed 14 / 10 000. This is informational and known: it depends on
    the anchor.
- **Body content:** codes, counts and hashes only. The self-scan found no value.
- **`last_check`** on the group: passed.
- **Independent containment** (`compare_obf_eng1.json`):
  - The obfuscated customer-id sets are identical on PG, MongoDB and Oracle: 20 000 = 20 000 = 20 000.
  - Oracle ADDRESS_HISTORY, KYC, INTERACTIONS and APPLICANTS ids ⊆ `CUSTOMERS_ENG1` ids:
    20 000 / 20 000, 20 000 / 20 000, 20 000 / 20 000 and 5 000 / 5 000.
  - PG accounts ⊆ customers: 20 000 / 20 000.

### 3. Synthesize (where supported) and D-A identifier checks

**Setup.** One group (`grp_01M4X617VRC356GN4P60S44MXR`). Script `p3_synthesize.py`. The aligned
counts were computed in that process through the keyed pseudonyms recomputed from the group seed;
alignment was 100 % on every target. Checks in `p5_compare_syn.py` (`compare_syn_eng1.json`).

**Jobs that succeeded.** All had verification `passed` and `review_required: false`. The evidence
`identifier_check` reports `in_source 0` for every checked identifier column (NATIONAL_ID, TAX_ID,
DOC_NUMBER, PAN, IBAN, account_number).

- PG: customers surrogate, customers copula, accounts surrogate.
- Oracle: CUSTOMERS surrogate, CUSTOMERS copula, KYC_DOCUMENTS, APPLICANTS.
- MongoDB: profiles surrogate.

**Copula privacy section.** `exact_row_matches 0`, MIA AUC 0.507 (Oracle) / 0.512 (PG), NNDR
passed.

**Per column (row-aligned):**

- **row_same = 0** on every surrogate and pseudonymised column of every target. The kept
  non-personal columns are 100 % equal, as designed.
- **Exception:** the token-level first name in notes (**B2**).

**Keys (D-A):**

- **Integer pseudonyms:** 100 % lie in [10⁹, 2·10⁹), all are unique, and **0** are source keys:
  - `customer_id` (PG ×3 tables) and `CUSTOMER_ID` (Oracle ×4 tables);
  - `account_id`;
  - `relationship_manager_id` (an FK to employees; 0 are real employee ids).
- **MongoDB `_id`:** 20 000 / 20 000 are `ObjectId` instances, all unique, **0** in the source.
- **`customerId`:** 0 in the source.
- **`APPLICATION_ID`** (text pseudonym): 0 in the source.
- **`DOC_REF`:** see N2. There are 392 coincidental collisions with other real refs; none is the
  row's own ref.
- **ROWID:** never written in any Oracle target. The key-less synthesize path itself is B1.

**Identifier sweep** (whole cells and text tokens):

- **0** real customer ids, ObjectIds, national ids, tax ids / PANs, IBANs, account numbers,
  application ids or e-mails in any synthetic target.
- **Phones:** about 11 % equal *some* seeded phone (fictional-range collisions). The row-aligned
  own-phone count is 0 (see N5).

**Joins under the group seed:**

- PG and Oracle synthetic customer pseudonyms are identical: 20 000 / 20 000.
- KYC → CUSTOMERS 20 000 / 20 000, APPLICANTS → CUSTOMERS 5 000 / 5 000, PG accounts → customers
  20 000 / 20 000.
- The copula and surrogate runs carry the same pseudonyms: 20 000.

**Refused by design** (codes, nothing written):

| Run | Result |
|---|---|
| MongoDB copula | 422 `tabular_invalid` |
| Oracle `CRM.INTERACTIONS` synthesize | 422 `synthesize_pseudonym_unsupported`: a TIMESTAMP part of the composite key cannot be pseudonymised (N4) |
| `in_place` synthesize on a sensitive key | 422 `synthesize_in_place_sensitive_key` (Oracle, on a copy this probe owns) |
| `in_place` without `confirm_write` | 422 `confirm_write_required` (Oracle and MongoDB) |

The probe's copies were unchanged (fingerprints equal). Its temporary connections were deleted
(204). Script `p6_inplace.py`, output `inplace_eng1.json`.

### 4. Resume after kill on Oracle and MongoDB

**DoD test on main.** `tests/e2e/test_m3_resume_after_kill.py`: **4 passed** in 102 s
(oracle/mongo × in_place/append; `dod_resume.log`). It is isolated: per-session Oracle owner,
per-test MongoDB database, test metadata database. No shared data was touched.

**Extra cases.** The DoD covers only `in_place` and `append`, so I added `table`, `files` and
synthesize, plus a key-less Oracle table: `kill/test_probe_m3_kill.py`, same method, 3 000 rows,
SIGKILL after ≥ 5 committed batches, then a second worker. Results are in `kill/kill_results.jsonl`.

| Case | Killed run | Digest killed = reference | Resumed / retried partitions | Ledger rows | Evidence `resumed_partitions` | Values in worker logs |
|---|---|---|---|---|---|---|
| Oracle table obfuscate | succeeded | **equal** (3 000) | 3 / 3 | 0 | 3 | 0 |
| Oracle **key-less (ROWID)** table obfuscate | succeeded | **equal** (3 000) | 3 / 3 | 0 | 3 | 0 |
| Oracle files (jsonl) obfuscate | succeeded | **equal** (3 000) | 3 / 3 | 0 | 3 | 0 |
| Oracle table synthesize | succeeded | **equal** (3 000) | 3 / 3 | 0 | 3 | 0 |
| MongoDB table obfuscate | succeeded | **equal** (3 000) | 3 / 3 | 0 | 3 | 0 |
| MongoDB table synthesize | succeeded | **equal** (3 000) | 3 / 3 | 0 | 3 | 0 |
| MongoDB files (jsonl) obfuscate | **reference run failed** `internal_error` | – | – | – | – | – (B3) |

- **Survivor check.** Obfuscate cases had 0 cells equal to a seeded e-mail, full name or national
  id. The two synthesize cases counted 45 (Oracle) and 34 (MongoDB) in *both* the reference and the
  killed run. These are synthetic full names that coincide with seeded names, not copies:
  row-aligned `FULL_NAME` row_same is 0 on the 20 000-row live targets, with set hits 813 / 20 000.
  Those two probe asserts were too strict for synthesize. The resume properties all hold.

### 5. Logs and metrics

- **Logs** (`logscan.py`, `logscan.json`; app, worker and tokenizer-mock since 17:45 IST, ANSI
  colour codes stripped first):
  - **0 hits** of 159 732 source values;
  - 0 tracebacks;
  - the planted-value control is found;
  - app (415 lines): 0 error-level lines and 3 `auth_rejected` warnings, all from other verifiers'
    auth probes (I never sent a bad key or a tenant header);
  - worker (149 lines): 28 error-level lines, every one of them `partition_failed
    error=TypeError|ArrowTypeError` from my B3/B4 files jobs. They carry the exception class only,
    no value.
- **Metrics:** `/metrics` shows `dps_llm_raw_pii_sent_total 0.0` after every run.

## Non-blocking notes

### N1. Obfuscate leaves the first name, and the kept city, inside Oracle and MongoDB notes

**What I found.**

- Oracle `CUSTOMERS_ENG1` and MongoDB `profiles_eng1`: 19 972 / 20 000 notes keep the row's own
  first name. 19 880 Oracle notes keep the city, which the policy keeps as a column.
- Isolated: 599 / 600 rows on both engines.
- The NOTES method is `det_token`, but on a free-text column it applies to detected spans only.

**Why it is not blocking.** This is the documented limitation: HANDOFF says "Without the `ner`
image, names in free text are not detected". The output is labelled "still personal data".

**Why it still matters.**

- M2 deliberately kept names out of the PG notes for this reason (M2-DEVIATIONS); the M3 seed
  reintroduced them.
- The L2 evidence still says `passed` with no coverage caveat. L1 shows "Verified, with partial
  coverage" without NER.
- Earlier "0 survivors" verifications compared whole cells only.

**Suggestion:** the same `row_value`-token fix as B2 in obfuscate mode, or a seed without names in
notes. L2 evidence should state the NER coverage gap.

### N2. Short text keys collide with other real keys after pseudonymisation

- `CRM.KYC_DOCUMENTS.DOC_REF` (`K<index>`, CHAR(12)): **392 / 20 000** synthetic refs equal the
  real ref of *another* document. By length: 2 → 1, 3 → 4, 4 → 30, 5 → 320, 6 → 37.
- This matches the documented ~n²/|shape| residual of shape-keeping text pseudonyms (≈ 385
  expected). None is the row's own ref (row_same 0).
- The synthesize `identifier_check` does not check keys, so the evidence does not show it.
- **Suggestion:** extend the whole-table identifier check to pseudonymised text keys, and/or
  cycle-walk text pseudonyms out of the source key set, the way integer keys already avoid real
  keys.

### N3. The Oracle copula does not model TIMESTAMP(9) and copies it

- `LAST_CONTACT_AT` is copied: 20 000 / 20 000 row-aligned. The copula columns are only COUNTRY,
  CREATED_AT, RISK_SCORE and SEGMENT.
- The value is unique per row, so it is a locator back to the source row. PG's copula models its
  timestamps.
- **Suggestion:** model FF9 text timestamps, or list unique kept columns in the privacy section.

### N4. Composite keys with a TIMESTAMP part refuse synthesize

- `CRM.INTERACTIONS` (CUSTOMER_ID, EVENT_TS) gets 422 `synthesize_pseudonym_unsupported`.
- This fails closed under D-A and nothing is written. Classify it as a documented limitation, or
  allow the timestamp part to be kept when the identifying parts are pseudonymised.

### N5. Explained set hits

These are expected and are not copies:

- **FPE ids:** `fpe_digits` customer ids land on another real id 1–4 times per table.
- **DOB:** date-domain collisions for `date_shift` and surrogate DOBs.
- **Phones:** surrogate phones fall in small fictional ranges, so about 11 % equal some seeded phone.
- **NINO false positives:** the sweep's digit-only NINO form (`QQ` + 6 digits) matches 40–48
  six-digit postal codes.

### N6. Concurrent verifiers supersede each other's snapshots

- A later discovery of the same object supersedes an accepted snapshot. My later runs on
  `CRM.CUSTOMERS`, `crm.profiles` and `bank.customers` were refused with 409
  `snapshot_not_accepted`; re-discovering fixed it.
- This is correct behaviour, but lens verifiers sharing one stack should discover right before
  submitting.

### N7. MongoDB `in_place` synthesize keeps the real `_id`

The ObjectId is never classified, so the `synthesize_in_place_sensitive_key` refusal cannot apply.
This is by design (`_id` is immutable). It is listed for the D-A record; I did not run it.

## Commands

```bash
cd /Users/neelu/document_intelligence/data-privacy-services
source ~/.cache/dps-build/reports/S1-M3-engines-r1/env.sh   # contract §12.2 + PYTHONDONTWRITEBYTECODE
DPS_TEST_REQUIRE_SERVICES=1 .venv/bin/python -m pytest -p no:cacheprovider -q -rA tests/e2e/test_m3_resume_after_kill.py
cd ~/.cache/dps-build/reports/S1-M3-engines-r1
python p1_setup.py; python p2_obfuscate.py eng1; python p3_synthesize.py eng1; python p4_compare_obf.py eng1
python p5_compare_syn.py eng1; python p5b_text_aligned.py eng1; python p5c_firstname.py eng1; python p6_inplace.py eng1
python repro_rowid.py; python refresh_snaps.py mdb_profiles ora_customers; python p7_files.py eng1; python logscan.py
# isolated pytest probes (run from the repo root):
.venv/bin/python -m pytest -c pyproject.toml --rootdir . -p tests.conftest -p no:cacheprovider -q -rA \
  ~/.cache/dps-build/reports/S1-M3-engines-r1/kill/test_probe_m3_{kill,firstname,files}.py
```

## State left behind

- **Targets left on the shared stack for inspection.** They are named `*_eng1` / `*_ENG1` in
  `bank_uat`, `crm_uat` and `CRM_MASKED`, and `eng-r1/eng1/…` objects in the MinIO `dps-exports`
  bucket: the two successful Oracle jsonl runs. The failed `files` runs report 0 rows written.
- **Job groups** `eng-r1-*`.
- **Isolated test owners, databases and metadata DBs** were dropped by the fixtures.
- **Not changed:** the repository and the demo sources. The stack is still running.
