# S2: regression lens, round 1 (branch `m4` after main was merged into it)

**Verdict: PASS. No blocking finding in this lens.** M4 does not regress M1, M2, M3 or M5 on the merged code:

- **Obfuscate.** One job each on PostgreSQL, Oracle and MongoDB. Each wrote 20 000 rows and its evidence says `passed`. Every sensitive column has **0 survivors**.
- **Consistency check.** 3 of 3 pairs agree, coverage 1.0 at sample sizes 1 000 and 10 000, so the overlap is > 0.
- **Synthesize.** Four jobs: PostgreSQL surrogate, PostgreSQL copula, Oracle and MongoDB.
  - Every whole-cell surrogate column has **0** row-aligned survivors.
  - D-A holds: 0 source keys, 0 real national, tax or employee ids, and identifier-check `in_source` is 0.
  - Copula: fidelity 0.96, 0 exact rows, MIA AUC 0.48 and NNDR within their targets.
- **Redacted PDFs** (text layer and scanned): **0** extractable characters. The OCR re-read finds **0** of the transformed values, while the OCR of the original PDF finds all of them.
- **Logs.** All 15 containers: **0** hits for 308 267 demo values.
- **LLM metric.** `dps_llm_raw_pii_sent_total` is **0.0** in app, worker and browser-worker.
- **Keys.** The keys and seeds of this run appear 0 times in the logs and 0 times in the metadata database.
- **Discovery.** The classification of 85 columns over 8 objects is identical to main's.

One D-A defect is visible on `m4`, but **it is not caused by M4**. Synthetic and obfuscated Oracle and MongoDB notes keep the source row's first name. This is S1-M3 engines B2/N1, and it behaves exactly as on main `144cc65`. Main fixed it in `5c11e49`, and `m4` does not contain that commit yet.

I checked how this resolves with a read-only trial merge of `m4` into the current main. The merge has no conflicts, and it carries main's fix files byte-identical. The post-merge verification on main must re-run this check (note N1).

| | |
|---|---|
| Date | 2026-10-14, 21:37–21:55 IST |
| Tree verified | `dps-wt-m4`, branch `m4` @ `a0e8d26`. `git status` was clean at start and end. HEAD is unchanged. No worktree file was created or modified (`find -newer` printed nothing). |
| Stack | compose project `dps-wt-m4`, as the gates verifier left it: up and healthy, not restarted, no `up` and no `down` by me. Ports: app 8414, dps-db 5463, source-pg 5464, MinIO 9050, Oracle 1524, Mongo 27020, OCR mock 5046, demo site 8420, tokenizer-mock 8424. `/readyz`: ready. NER: `patterns-only`, LLM: stub, OCR: azure-read-v3.2, browser: 1 online. |
| Python | lane `.venv` via `./lane.sh` (`.env.lane`), with `PYTHONDONTWRITEBYTECODE=1` |
| Probes and outputs | `~/.cache/dps-build/reports/S2-regression-r1/`: scripts `p1`–`p10`, `lib.py`, `src_state.py`, `selfscan.py`. Outputs are in the `*.json` / `*.out` files. |
| Values in my outputs | **0**. `selfscan.py` searched the 35 output files for the 308 267 demo values. |
| Shared demo sources | unchanged. Counts and seed markers match before and after (`src_state.before.json` = `src_state.after.json`): seed 7, 20 000 customers on PostgreSQL, Oracle and MongoDB. |
| Repository files modified | none |

All figures are counts. No value, token, key or seed was printed or stored. The group key and seeds stayed in the memory of the probe that created them.

## 1. L2 obfuscate: one job per engine, table targets, per-column source vs target

Setup:

- One job group: `grp_01M4XJWXNJEPQYR2RTJCEPVE6Z`, kid `k_630ff0c68ce44c6d`.
- Default policy. Snapshots were discovered and accepted through the API (`p1_setup.py`).
- Write mode `table`, `truncate_insert` into new targets.
- Runs: `p2_obfuscate.py`. Comparison: `p5_compare_obf.py` → `p5_compare_obf_s2reg1.json`.
- All three jobs: `succeeded`, 20 000 read and 20 000 written, 1 of 1 partitions. Evidence `passed`, `review_required: false`, label "pseudonymised — still personal data". The post-sample (200 rows) has kept-column hits 0 and declared-kept hits 0.

Metrics:

- *row_same*: the number of aligned rows where the target value equals the source value. A sensitive column (method ≠ keep) with row_same > 0 is a survivor.
- *set*: the number of target values found anywhere in the source column.

| Source → target (job) | Rows src / tgt | Aligned on | Sensitive columns: **row_same** | set hits (all explained) |
|---|---|---|---|---|
| PG `bank.customers` → `bank_uat.customers_s2reg1` (`j_01M4XJWXNSJN59XN6RAXVQ5XPV`) | 20 000 / 20 000 | the 8 kept columns: 20 000 / 20 000 | **0** on customer_id, full_name, date_of_birth, national_id, tax_id, email, mobile, notes | customer_id 2 (FPE onto *another* real id), date_of_birth 12 616 (date domain) |
| Oracle `CRM.CUSTOMERS` → `CRM_MASKED.CUSTOMERS_S2REG1` (`j_01M4XJX0M7K8543XFTHNFJTA5C`) | 20 000 / 20 000 | `LAST_CONTACT_AT`: 20 000 / 20 000 | **0** on CUSTOMER_ID, FULL_NAME, NATIONAL_ID, TAX_ID, EMAIL, MOBILE, DOB, ADDR_LINE1, PIN_CODE, NOTES | CUSTOMER_ID 2, DOB 12 616 |
| MongoDB `crm.profiles` → `crm_uat.profiles_s2reg1` (`j_01M4XJWZ52H36M05FM18TWVVBT`) | 20 000 / 20 000 | `_id`: 20 000 / 20 000 | **0** on customerId, profile.fullName, profile.dob, contact.email, contact.mobile, addresses[].line1, addresses[].pin, kyc.taxId, accounts[].iban, notes | customerId 2, dob 12 558 |

These match main `144cc65` as measured by S1-M3 engines r1:

- FPE id collisions: 2 per engine on both trees.
- Date-shift domain hits: about 12.5 k on both trees.

Oracle and MongoDB notes keep the first name in **19 972 / 19 972** rows, which is N1 below.

## 2. Consistency check across the three engines (overlap > 0)

Script: `p2_obfuscate.py`. Endpoint: `POST /job-groups/{id}/consistency-check`, derived members.

- **Members**:
  - Oracle `$.CUSTOMER_ID`, numeric: the anchor.
  - MongoDB `$.customerId`: `prefixed_digits`, width 8, prefix 5.
  - PostgreSQL `$.customer_id`: numeric.
  - All three use `fpe_digits` with the same `params_hash`.

| Sample | HTTP | passed | Pairs consistent | Coverage per pair | Time |
|---|---|---|---|---|---|
| 1 000 | 200 | true | **3 / 3** | 1000 / 1000 each (min_coverage 1.0) | 219 ms |
| 10 000 | 200 | true | **3 / 3** | 10000 / 10000 each (min_coverage 1.0) | 1.1 s |

- **Attributes** (sample 10 000):
  - EMAIL, PERSON_NAME, PHONE and STREET_ADDRESS agreed 10 000 / 10 000.
  - NATIONAL_ID agreed 10 000 / 10 000 (Oracle and PostgreSQL).
  - PAN_IN agreed 5 947 / 5 947.
  - POSTAL_CODE agreed 10 000 / 10 000; it does not count toward the pass.
  - DATE_OF_BIRTH `date_shift` agreed 16 / 10 000. This is informational and known: it depends on the anchor, as on main.
- **Group** `last_check.passed` is true. Problems: none.
- **Independent overlap** (`p5`): the obfuscated customer-id sets are identical on all three targets: PG 20 000 = Oracle 20 000 = Mongo 20 000, with **20 000** in the intersection of all three.

## 3. Synthesize (D-A): per-column counts, keys and identifier check

Setup:

- Group: `grp_01M4XJWZVAQSQH6MT39EH66TNA`. Scripts: `p3_synthesize.py` and `p6_mongo_syn_align.py`.
- Alignment:
  - PostgreSQL and Oracle rows were aligned through the keyed pseudonyms, recomputed in memory from the group seed: 20 000 / 20 000 (100 %).
  - MongoDB was aligned without the seed, on the fields the run keeps (balances, verifiedAt, riskScore, segment): 20 000 / 20 000, with all 20 000 source keys unique.
- All four jobs: `succeeded`, 20 000 rows written, evidence `passed`, `review_required: false`, label "synthetic — measured, not claimed anonymous".

| Job | Surrogate columns, row-aligned **row_same** | Keys (D-A) | Evidence `identifier_check` |
|---|---|---|---|
| PG `bank.customers` surrogate → `bank_uat.customers_syn_s2reg1` | **0** on full_name, date_of_birth, national_id, tax_id, email, mobile, notes | customer_id: 20 000 / 20 000 in [10⁹, 2·10⁹), all unique, **0** equal a source key. relationship_manager_id: **0** real employee ids. national_id **0** and tax_id **0** in the source after letters-and-digits normalisation. | national_id 20 000 checked, `in_source` 0, `issuable` 0; tax_id 11 989, 0, 0 |
| PG `bank.customers` copula → `customers_cop_s2reg1` | **0** on the same columns | same: 20 000 in band, 0 source keys, 0 real employee ids, national_id / tax_id 0 in source | as above, 0 / 0 |
| Oracle `CRM.CUSTOMERS` surrogate → `CRM_MASKED.CUSTOMERS_SYN_S2REG1` | **0** on FULL_NAME, NATIONAL_ID, TAX_ID, EMAIL, MOBILE, DOB, ADDR_LINE1, NOTES (whole cell). Notes keep the first name: N1. | CUSTOMER_ID: 20 000 in band, unique, **0** source keys | NATIONAL_ID 5 000 checked, 0 / 0; TAX_ID 2 941, 0 / 0 |
| MongoDB `crm.profiles` surrogate → `crm_uat.profiles_syn_s2reg1` | **0** on fullName, dob, email, mobile, addresses[].line1, kyc.taxId, accounts[].iban, notes (whole cell). Notes keep the first name: N1. | `_id`: 20 000 unique, **0** equal a source `_id`. customerId: **0** equal a source value. | iban 5 000 checked, 0 / 0; taxId 3 043, 0 / 0 |

- **Copula privacy and fidelity** (M5):
  - fidelity score **0.9608** (target ≥ 0.85); corr_max 0.036, within the 0.1 limit;
  - `exact_row_matches` **0**;
  - MIA (`dcr_auc`) AUC **0.4814**, passed against the 0.6 target;
  - NNDR passed, ratio 1.48;
  - QI uniqueness overlap 0.
- **Expected set hits** (values that coincide with *other* source rows; row_same is 0 in every case): full_name 837, mobile 2 243, date_of_birth 13 849, ADDR_LINE1 9, Mongo line1 11.
- **S2-gates B1 flake** (a synthetic national_id equal to a demo-source value) did not occur in these runs: PostgreSQL national_id and tax_id `in_source` were 0 for both jobs. One run is no evidence either way; the finding and its fix belong to the gates report.

## 4. M1 documents: redacted PDF and OCR re-read

Script: `p4_documents.py`. The originals are the deterministic synthetic demo values from `tools/make_demo_docs._values()`, kept in memory only. OCR used tesseract from the `m4` app image with `--network none`, at 300 dpi.

| Document | HTTP | Redaction (API) | Redacted PDF text layer | Raw bytes | OCR of redacted PDF | OCR control (original PDF) |
|---|---|---|---|---|---|---|
| `kyc-text.pdf` | 200 | passed, 14 boxes, ocr_checked, ocr_hits 0 | **0** chars (pypdf), **0** chars (pdfplumber); the original has 601 | 0 value hits | **0** of the 13 transformed value classes; only `gender` (not an entity, kept by design) | all 14 classes found |
| `kyc-scanned.pdf` (OCR pages 1, 2) | 200 | passed, 14 boxes, ocr_checked, ocr_hits 0 | **0** / **0** | 0 | **0** transformed; only `gender` | all 14 found |

- **Findings redacted** (both documents): AADHAAR, ACCOUNT_NUMBER, CARD_PAN, CUSTOMER_ID, DATE_OF_BIRTH, EMAIL, PAN_IN, PERSON_NAME ×3, PHONE ×2, POSTAL_CODE and STREET_ADDRESS, each with `redacted: true`.
- **Text round trip**: `kyc.txt` obfuscate → reveal returns the original exactly. The obfuscated text holds 0 demo values.

## 5. Discovery parity (code M4 touched)

M4 moved the mixed-contact name constants from `connectors/discovery.py` into `engine/detect/structural.py`. I compared discovery on `m4` with main's output (`p1`, `p10`).

- Discovered and accepted: the 8 demo objects of S1-M3 engines r1 (PG customers and accounts; Oracle CUSTOMERS, ADDRESS_HISTORY, KYC_DOCUMENTS, INTERACTIONS and APPLICANTS; Mongo profiles).
- For each column I compared entity, sensitivity, key, text flag, obfuscate method, synthesize method and kind.
- **85 columns compared, 0 differences.** That includes the ROWID pseudo key of ADDRESS_HISTORY and the composite TIMESTAMP key of INTERACTIONS.
- `bank.contacts.value` (next to `channel`) → `MIXED_PII` / MEDIUM / `hmac_pseudonym` / `surrogate`. Main's discovery semantics hold.

The other non-scanner changes M4 makes over `144cc65` are additive, and the defaults keep the old behaviour (read):

- `jobs.cancel`: the run path is unchanged; only started scans get a finalize.
- `storage.delete_for_job`: new optional filters. With the defaults it deletes the same rows; M5 retention calls it bare.
- `evidence.render_html`: adds a section only for scan packs.
- `reports.summary`: adds `personal_fields`.
- New log allow-list fields and new scan metrics.

## 6. Logs, LLM metric, keys (D2), metadata database, evidence and reports

- **Logs** (`p7_logscan.py`; the whole log of all 15 containers of the project, running and exited):
  - **0 hits all-time, 0 since the probe started.** The needles were 308 267 normalised demo values (8 characters or more):
    - PG customers: full_name, national_id, tax_id, email, mobile;
    - Oracle CUSTOMERS: the same five plus ADDR_LINE1;
    - Mongo profiles: fullName, email, mobile, taxId, iban, line1;
    - the KYC document values and the demo site's customer values.
  - Positive control: a planted needle of each of the 36 classes was found (36 / 36).
  - **0** tracebacks and **0** error-level lines in every container.
  - The app logged 5 warnings: 2 `auth_rejected` from the gates verifier's smokes, and 3 `rate_limited` at 16:12–16:14 UTC. The `rate_limited` ones were on `/scans/…/pages/…` and `/artefacts/…`, from another lens's scanner probes; they carry no value. My probes never came near the 50 rps limit.
- **LLM**:
  - `dps_llm_raw_pii_sent_total 0.0` in the **app** before and after every phase.
  - The same `0.0` in the **worker** (ops 9400) and the **browser-worker** (ops 9401), read twice: after the jobs and at the end.
  - `dps_llm_raw_pii_sent_by_tenant_total` has no series, because no LLM call was made (stub).
  - The reports summary says `raw_pii_sent_to_llm` 0 and `submissions` 0.
- **D2 (DPS stores no key)**:
  - The DEKs and seeds of both job groups and of the three document calls (7 strings) appear **0** times in the logs of all containers.
  - They also appear **0** times in a `pg_dump --data-only` of every database in dps-db (`dps` 3.6 MB, plus `postgres` and a leftover `dps_test_*`).
- **Metadata database stores no values** (`p8_dbscan.py`): the same 308 267 needles over the `dps` dump give **0** hits. The dump covers 25 tables: jobs, partitions, snapshots, evidence, audit 2026-10 to 2027-01, scan_*, policy store, reports, keys and tenants. It contains this run's job and group ids, so it reflects the live data.
- **Evidence and reports** (`p9`):
  - All 7 packs of this run, JSON and printable HTML: 200, 0 values, no `<script>`, regulatory note present.
  - L2 packs carry **no** "UI scan" section (M4 adds it for scan packs only).
  - `/reports/summary`, `/reports/jobs`, `/reports/evidence` (86 items, all 7 of mine listed) and `/reports/api-rollups`: all 200 with 0 values. The roll-up document returns 200; its `format=pdf` returns 200 and a `%PDF-` body.

## 7. Non-blocking notes

### N1. First name kept in Oracle and MongoDB notes: inherited from main, already fixed there

This is S1-M3 engines B2 and N1.

**On `m4`:**

- Oracle `CUSTOMERS_S2REG1` (obfuscate), Oracle `CUSTOMERS_SYN_S2REG1` (synthesize), Mongo `profiles_s2reg1` (obfuscate) and Mongo `profiles_syn_s2reg1` (synthesize) each keep the source row's first name in **19 972 / 19 972** notes. The check is whole-word, caseless, on first names of 3 letters or more.
- Control: 19 972 source notes hold it.
- The evidence still says `passed`.
- PostgreSQL is not affected: its seed notes hold no names.

**Why it is not an M4 regression:**

- `m4`'s `dps/engine/batch.py` is byte-identical to main `144cc65`'s.
- The figures agree with S1's on `144cc65`: obfuscate 19 972 / 20 000 notes; synthesize 20 000 / 20 000 rows (S1 counted the name slot of the note template, a slightly different method).

**Where it is fixed:** main `5c11e49` ("M3 post-merge r1"), which S1-M3-fix-r1 measured at 0 / 20 000. That commit is the only main commit not in `m4`.

**Read-only trial merge** of `m4` into main `5c11e49` (`git merge-tree`; base `144cc65`):

- 0 conflicted files.
- The 5 files both sides changed merge cleanly: HANDOFF, contract, `connectors/evidence.py`, `EvidencePack.tsx` and its test.
- The merged tree carries main's `batch.py`, `files.py`, `jobs_run.py`, `targets.py`, `docpaths.py` and `routers/jobs.py` byte-identical.

**Action:** no fix on `m4`; a second fix would only conflict with main's. Then either:

- merge `m4` into main, or
- first merge main into `m4`, if other streams (S3b, S4b, S6b) will consume `m4` before main does.

After that, the post-merge verification on main must re-run the first-name check: Oracle and Mongo, obfuscate and synthesize, expect 0. It must also re-run the 3-way-merged `evidence.py` tests (`tests/test_evidence.py`, the EvidencePack Vitest).

### N2. Other defects inherited from main, carried to main's fixes by the same merge

S1-M3 engines B1, B3 and B4 and security B1 are present on `m4` by code identity. I did not re-run them; this lens ran one job per engine.

- B1: key-less Oracle synthesize fails with `leakage`.
- B3: MongoDB → `files` fails.
- B4: Oracle TIMESTAMP(7–9) → parquet fails.
- Security B1: `table` / `files` writes into a production-tagged connection need no admin.

`m4`'s `jobs_run.py`, `files.py`, `docpaths.py` and `targets.py` are byte-identical to main `144cc65`'s. All four are fixed in `5c11e49`. The same post-merge re-run applies.

### N3. Observations, no action needed

- `gender` stays in the document output (text and redacted PDF). It is not a detected entity, and main behaves the same way.
- Other lens verifiers were active on the same stack during my run:
  - `rate_limited` warnings on scanner routes;
  - their targets `S2CON_330088`, `s2con_3b5ffb` and `s2con_syn_3a557f`;
  - ephemeral containers of the `m4` image, which carry the compose project label.

  None of this affected my runs.
- The trial merge used `git merge-tree --write-tree` once (exit 0). That writes unreferenced tree objects into the shared object store; no ref, index, worktree or tracked file changed. The diff text from the legacy-mode call held repository fixture values. I deleted it from my probe folder, and the self-scan then showed 0.

## 8. Left on the stack

New targets: `bank_uat.customers_s2reg1`, `customers_syn_s2reg1`, `customers_cop_s2reg1`; `CRM_MASKED.CUSTOMERS_S2REG1`, `CUSTOMERS_SYN_S2REG1`; `crm_uat.profiles_s2reg1`, `profiles_syn_s2reg1`. They hold obfuscated or synthetic rows only.

Metadata: two job groups, 7 run jobs with evidence, 9 discovery jobs and their accepted snapshots.

Sources unchanged. Nothing reseeded. No `docker compose up` or `down` was run.

## Reproduce

```bash
cd /Users/neelu/document_intelligence/dps-wt-m4 && export PYTHONDONTWRITEBYTECODE=1
P=~/.cache/dps-build/reports/S2-regression-r1
./lane.sh python $P/src_state.py > $P/src_state.before.json
./lane.sh python $P/p1_setup.py              # discover + accept 3 snapshots (state.json)
./lane.sh python $P/p2_obfuscate.py s2reg1 & ./lane.sh python $P/p3_synthesize.py s2reg1 & ./lane.sh python $P/p4_documents.py; wait
./lane.sh python $P/p5_compare_obf.py s2reg1; ./lane.sh python $P/p6_mongo_syn_align.py s2reg1
./lane.sh python $P/p7_logscan.py; ./lane.sh python $P/p8_dbscan.py; ./lane.sh python $P/p9_evidence_reports.py
./lane.sh python $P/p10_discovery_parity.py; ./lane.sh python $P/selfscan.py
./lane.sh python $P/src_state.py > $P/src_state.after.json
```
(A re-run needs a new tag argument, or it rewrites the same target tables.)
