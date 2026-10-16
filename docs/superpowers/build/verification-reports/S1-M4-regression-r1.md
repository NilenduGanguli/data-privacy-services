# S1 — main after the M4 merge — REGRESSION lens, round 1

**Verdict: FAIL — 1 blocking finding (B1, new, pre-existing since M2; not caused by the merge).**

The merge does not regress M1, M2, M3 or M5 on any path this lens exercised:

- **Obfuscate, one job per engine** (PostgreSQL, Oracle, MongoDB → `table` targets): 20 000 rows each, evidence `passed`. Every sensitive column has **0 survivors**, row-aligned on kept columns.
- **Consistency check:** 3 of 3 pairs agree, coverage 1.0 at samples 1 000 and 10 000. Independent **overlap**: 20 000 obfuscated customer ids are common to all three targets.
- **Synthesize** (PostgreSQL surrogate + copula, Oracle surrogate, MongoDB surrogate): **0** row-aligned survivors in every surrogate column.
  - **D-A**, measured independently over every row: 0 source keys, 0 real national, tax or employee ids, 0 source IBANs.
  - Copula: fidelity 0.9728, exact-row matches 0, MIA AUC 0.4932, NNDR passed.
- **Free text:** the source row's own name, e-mail, phone, national id and tax id appear **0** times in any obfuscated or synthetic note. That covers the 28 short first names and the 34 short cities.
  - The S2 regression r1 N1 re-check asked for after this merge holds: first names stay in 0 of 19 972 notes on Oracle and MongoDB, both modes.
- **M1 documents:** redacted text-layer and scanned PDFs have **0** extractable characters. An OCR re-read finds 0 of the transformed values; the OCR control finds all 14 classes. The text obfuscate → reveal round trip is exact.
- **Discovery:** classification parity with main @ `d2fdc3f` (pre-merge): **96 columns over 10 objects, 0 differences.**
- **Logs, keys and metric:** 0 demo values in the logs of all 17 containers during the run window (308 271 needles, positive control 36/36). 0 key or seed strings in logs or in the metadata database. `dps_llm_raw_pii_sent_total` is **0.0** in app, worker and browser-worker, before and after.

**The blocking defect (B1).** A canceled run's evidence pack, job progress and Reports row under-count what the target holds by the batch that was in flight when the cancel landed.

- Reproduced 3 of 3 times. The pack says 1 000 / 3 000 / 4 000 rows written; the new target tables hold 2 000 / 4 000 / 5 000 rows.
- One case is a ledgered Oracle load. Its cancel finalize also clears the ledger mark that recorded the batch.
- This is M2 code (`jobs.cancel` fences the holder's checkpoint). The merge touched `jobs.cancel` only for scans.

**The S1-M3 engines r3 B1 identifier-check defect is still live on main** (Oracle/MongoDB check 5 000 of 20 000 rows, reported as `whole_table`). It is **not counted as blocking here**: the fix is committed on `fidcheck` (`9fe68f4`), which is due to merge via `fllm`. See N1.

| | |
|---|---|
| Date | 2026-10-15, 21:09–21:45 IST |
| Tree verified | `/Users/neelu/document_intelligence/data-privacy-services`, `main` @ **`8ea220a`**, HEAD unchanged. I modified no repository file. |
| Code identity | app and worker: all 194 files under `dps/`, `tools/` and `pyproject.toml` have the same sha256 as the tree. browser-worker: 177 of 177. Files: `identity.*.txt`. |
| Stack | compose project `data-privacy-services`, left running by the gates verifier. I ran no `up`, `down`, restart or rebuild. `/readyz` reported ready (stub LLM, `coin_tls=verified`, ner patterns-only, fpe ff1, ocr azure-read-v3.2, browser 1 online, migrations verified). Contract §12.2 ports; tokenizer-mock on 8430. |
| Python | main's `.venv` through `run.sh` (contract §12.2 env, `PYTHONDONTWRITEBYTECODE=1`). Nothing was written into the repo. |
| Probes and outputs | `~/.cache/dps-build/reports/S1-M4-regression-r1/`: `lib.py`, `p1`–`p12`, `p5d`–`p5g`, `p7b`, `p8b`, `p8c`, `repro_idcheck_missed_hit.py`, `metrics.py`, `reports_snap.py`, `selfscan2.py`; outputs `*.json` / `*.out`. |
| Values in my outputs | **0** (`selfscan2.py`): 218 922 needles and 865 first names of 6 or more letters, over 88 files. The only "first name" match is a Docker-generated container name in a container list. |
| Shared demo sources | Unchanged by me. Counts and seed markers were equal before (21:11) and after (21:30) my comparisons. Afterwards a concurrent verifier reloaded Oracle and MongoDB at test size (N5). |

Only counts, codes, ids and column names appear below. The group keys and seeds stayed in the memory of the probe that created them; none was printed or stored.

## 1. L2 obfuscate — one job per engine, `table` targets (`p2b`, `p5`)

**Setup**
- Group `grp_01M503W26K9AAF7AZG12T4ZAV2` (kid `k_d84301dab67f9e29`), default policy, `truncate_insert` into new tables.
- Jobs: PG `j_01M503W83ANSTBKNA1VZ5K0C1E`, Mongo `j_01M503W9JKN9R80AGH9G0EK49B`, Oracle `j_01M503WB1SB2S8P89E2MN80715`.
- All three ended `succeeded`: 20 000 read and 20 000 written, 1 of 1 partitions.
- Evidence: `passed`, `review_required: false`, "pseudonymised — still personal data".
- Post-sample (200 rows): kept-column hits 0, declared-kept hits 0.
- Key custody: `returned: false`, `stored_by_dps: false`, purged.

**Results.** *row_same* = aligned rows whose target value equals the source value. A sensitive column with row_same above 0 would be a survivor.

| Source → target | Rows (src / tgt) | Aligned on | Sensitive columns, **row_same** | Set hits (all explained) |
|---|---|---|---|---|
| PG `bank.customers` → `bank_uat.customers_s1m4reg1b` | 20 000 / 20 000 | the 8 kept columns: 20 000 / 20 000 | **0** on customer_id, full_name, date_of_birth, national_id, tax_id, email, mobile, notes | customer_id 4 (FF1 crossings onto another real id, as in every round); date_of_birth 12 569 (date domain) |
| Oracle `CRM.CUSTOMERS` → `CRM_MASKED.CUSTOMERS_S1M4REG1B` | 20 000 / 20 000 | `LAST_CONTACT_AT`: 20 000 / 20 000 | **0** on CUSTOMER_ID, FULL_NAME, NATIONAL_ID, TAX_ID, EMAIL, MOBILE, DOB, ADDR_LINE1, PIN_CODE, NOTES | CUSTOMER_ID 4, DOB 12 569 |
| Mongo `crm.profiles` → `crm_uat.profiles_s1m4reg1b` | 20 000 / 20 000 | `_id`: 20 000 / 20 000 | **0** on customerId, profile.fullName, profile.dob, contact.email, contact.mobile, addresses[].line1, addresses[].pin, kyc.taxId, accounts[].iban, notes | customerId 4, dob 12 536 |

**Earlier attempt.** The first attempt (`p2`, group `grp_01M503RVR9VCW7FB5BD8N71N3X`) ran the same Oracle and Mongo jobs: `succeeded`, `passed`. Its PG member was refused 409 `snapshot_not_accepted`, because a concurrent verifier had accepted a newer snapshot of `bank.customers` (N5). `p2b` re-ran all three engines in one group, rediscovering the snapshot on that 409. The refreshed snapshot's classification is identical to the original.

## 2. Consistency check across the three engines (`p2b`)

**Group check:** `POST /job-groups/{id}/consistency-check` with derived members.
- Oracle `$.CUSTOMER_ID` is the anchor. Mongo `$.customerId` is `prefixed_digits` (width 8, prefix 5). PG `$.customer_id` is numeric.
- All three use `fpe_digits` with the same `params_hash`.

| Sample | HTTP | Passed | Pairs consistent | Coverage per pair | Time |
|---|---|---|---|---|---|
| 1 000 | 200 | true | **3 / 3** | 1.0 (1 000 matched each) | 0.3 s |
| 10 000 | 200 | true | **3 / 3** | 1.0 (10 000 matched each) | 1.8 s |

**Attributes at sample 10 000**
- EMAIL, PERSON_NAME and PHONE agreed 10 000 / 10 000.
- NATIONAL_ID and STREET_ADDRESS agreed 10 000 / 10 000.
- PAN_IN agreed 6 001 / 6 001.
- POSTAL_CODE agreed 10 000 / 10 000; it does not count toward the pass.
- DATE_OF_BIRTH `date_shift` agreed 15 / 10 000. This is informational: the shift depends on the anchor, as in every round.
- Problems: none. `methods_agree` is true.

**Independent overlap** (`p5`): the obfuscated customer-id sets are identical on PG, Oracle and Mongo, with **20 000 in the intersection of all three**.

## 3. Synthesize and D-A (`p3`, `p3b`, `p6`)

**Setup**
- Groups: `grp_01M503RVXRPFWMAM6DZC1PACAK` (Oracle, Mongo) and `grp_01M503YFH1KSRWAQNRDNKFX3CN` (PG, re-run after the same 409).
- All four jobs ended `succeeded` with 20 000 rows. Evidence `passed`, `review_required: false`, "synthetic — measured, not claimed anonymous".
- Alignment:
  - PG and Oracle through the keyed pseudonyms, recomputed in memory from the group seed: 20 000 / 20 000.
  - Mongo without the seed, on the kept fields (balances, verifiedAt, riskScore, segment): 20 000 / 20 000, all source keys unique.

| Job | Surrogate columns: row-aligned **row_same** | D-A (my own full-table count) | Pack `identifier_check`: checked / non-null written |
|---|---|---|---|
| PG surrogate `j_01M503ZYK92BN3PNDSVKAJ3WA1` → `customers_syn_s1m4reg1` | **0** on full_name, date_of_birth, national_id, tax_id, email, mobile, notes | customer_id: 20 000 in [10⁹, 2·10⁹), unique, **0** source keys. relationship_manager_id: **0** real employee ids. national_id and tax_id: **0** in the source (letters-and-digits normalised). | national_id 20 000 / 20 000; tax_id 11 989 / 11 989. `in_source` 0, `issuable` 0. |
| PG copula `j_01M503ZYKWH4N71NY1P65XSZ89` → `customers_cop_s1m4reg1` | **0** on the same columns | the same: 0 source keys, 0 employee ids, 0 ids in the source | the same, 0 / 0 |
| Oracle surrogate `j_01M503RVYR94J18FFNF3HJF7Y6` → `CUSTOMERS_SYN_S1M4REG1` | **0** on FULL_NAME, NATIONAL_ID, TAX_ID, EMAIL, MOBILE, DOB, ADDR_LINE1, NOTES | CUSTOMER_ID: 20 000 in band, unique, **0** source keys. NATIONAL_ID and TAX_ID: **0** in the source. | **NATIONAL_ID 5 000 / 20 000; TAX_ID 3 030 / 11 989** (N1) |
| Mongo surrogate `j_01M503RVZ1ND5SVHT8ZW19JMFQ` → `profiles_syn_s1m4reg1` | **0** on fullName, dob, email, mobile, addresses[].line1, kyc.taxId, accounts[].iban, notes | `_id`: 20 000 unique, **0** equal a source `_id`. customerId: **0**. iban and taxId: **0** in the source. | **iban 5 000 / 20 000; taxId 2 972 / 11 989** (N1) |

**Copula privacy and fidelity (M5)**
- Fidelity score **0.9728** (target 0.85); corr_max 0.0372, within the limit.
- `exact_row_matches` **0**.
- MIA (`dcr_auc`) AUC **0.4932**, passed (target 0.6).
- NNDR passed, ratio 1.448.
- QI uniqueness overlap 0. Utility not run: the policy has no `utility_label`.

**Expected set hits** (values equal to *other* source rows; row_same is 0 in every case):
- full_name 816 to 835;
- mobile 2 288 to 2 298;
- dob about 13 800;
- ADDR_LINE1 11, Mongo line1 8.

These are surrogate-pool collisions: the demo was generated with the same generators.

## 4. Free text: own values in notes (`p5d`, `p5e`, `p5f`, `p5g`)

**Row-aligned check.** Each target note is compared with its own source row, without any key (Oracle `LAST_CONTACT_AT`, PG `created_at+updated_at`, Mongo `_id` or kept fields).

| Target | Own e-mail / phone / full name / first name / national id / tax id in the note | Name slot = source first name (< 3 letters · 3 or more) | City slot = source city (< 4 · 4 or more) |
|---|---|---|---|
| Oracle obfuscate (both runs) | **0** | **0 / 28 · 0 / 19 972** | 34 · 19 872. Expected: the policy keeps `CITY` (r3: same). |
| Oracle synthesize | **0** | **0 / 28 · 0 / 19 972**. The slot holds the row's own synthetic first name 28/28 and 19 972/19 972. | **0 / 34 · 0 / 19 966** |
| Mongo obfuscate (both runs) / synthesize | **0** | **0 / 28 · 0 / 19 972** | 34 · 19 912. Known: Mongo has no city field, so the city stays in the notes (r3 N1, documented). |
| PG obfuscate / surrogate | **0** (5 979 notes) | (PG notes hold no name slot) | — |
| Control: source vs itself | finds them all (e-mail 20 000, first names 19 972 + 28, …) | 28 · 19 972 | 34 · 19 926 to 19 966 |

**Unaligned check.** Any source value of 8 or more normalised characters, in any note.
- Synthetic notes: 468 PG notes (surrogate and copula) and 1 528 Oracle notes match. All of them disappear once the row's *own* synthetic name, e-mail and phone are cut out (cross-row pool collisions).
- Of Mongo's 1 539 matching notes, 1 537 are explained the same way. The other 2 sit on the city slot: a city spelled like some demo customer's squashed full name.
- Obfuscated notes: 0 (PG), 2 (Oracle) and 2 (Mongo). All 4 overlap the kept or source city; none is the row's own name.

## 5. M1 documents (`p4`)

OCR used tesseract from main's app image, `--network none`, 300 dpi.

| Document | HTTP | Redaction (API) | Text layer of the redacted PDF | Raw bytes | OCR of the redacted PDF | OCR control (original) |
|---|---|---|---|---|---|---|
| `kyc-text.pdf` | 200 | passed, 14 boxes, `ocr_checked`, `ocr_hits` 0 | **0** chars with pypdf and **0** with pdfplumber (the original has 601) | 0 value hits | **0** of the transformed classes. Only `gender`, which is not an entity and is kept by design. | all 14 classes |
| `kyc-scanned.pdf` (OCR pages 1–2) | 200 | passed, 14 boxes, `ocr_checked`, `ocr_hits` 0 | **0** / **0** | 0 | **0**, only `gender` | all 14 |

- Every finding has `redacted: true`: AADHAAR, ACCOUNT_NUMBER, CARD_PAN, CUSTOMER_ID, DATE_OF_BIRTH, EMAIL, PAN_IN, PERSON_NAME ×3, PHONE ×2, POSTAL_CODE, STREET_ADDRESS.
- `kyc.txt`: obfuscate → reveal returns the original exactly, and the obfuscated text holds 0 demo values.

## 6. Discovery parity across the merge (`p10`)

M4 moved discovery's mixed-contact name constants into `engine/detect/structural.py` and the merge edited `discovery.py`. I re-discovered the 9 demo objects that S1-M3 engines r3 discovered on main @ `d2fdc3f`, plus `bank.contacts`.

- The 9 objects: PG customers and accounts; Oracle CUSTOMERS, ADDRESS_HISTORY (ROWID), KYC_DOCUMENTS, INTERACTIONS (composite key), CUSTOMER_EVENTS and LOANS.APPLICANTS; Mongo profiles.
- `bank.contacts` was compared with S2 regression r1's output on `m4`.
- Each new snapshot was left **proposed**, never accepted: an accept would supersede snapshots other verifiers use.

**Result: 96 columns compared, 0 differences, 0 missing or new columns, 0 shape differences.** `bank.contacts.value` (next to `channel`) is still `MIXED_PII` / MEDIUM / `hmac_pseudonym` / `surrogate`.

## 7. Job routes and cancel, on code the merge changed (`p11`, `p12`)

The merge changed three places here:
- `routers/jobs.py`: the per-kind scope check;
- `list_jobs(exclude_kind)`;
- `jobs.cancel`: started scans get a finalize; the run path itself is unchanged.

**A DB-backed key with only `jobs`** was minted (201, `no-store`) and disabled at the end:
- `GET /jobs`: 200; the first 200 items are 102 discover and 98 run jobs, with no scan.
- `kind=scan`: 403 `scope_required`.
- Own run job and its evidence: 200. A scan job: 403 `scope_required`.
- It canceled a run job (200), and the job list shows both canceled runs.

**Cancel before start** (`j_01M504CDXC5VR1WF558Q98X9SV`): `canceled`. Evidence `outcome: canceled`, `status: incomplete`, 0 rows. No target was created.

**Cancel mid-run: the counts are short by one batch (B1).**

| Run (cancel sent once N rows were checkpointed) | Job `rows_written` = pack `counts.rows_written` (batches) | Reports row `summary.rows_written` | **Rows in the new target** | Survivors |
|---|---|---|---|---|
| PG `table`, N = 1 000 (`j_01M504CGWSFMMCEJCFSVPPBFZ3` → `customers_s1m4reg1_cxl1`) | 1 000 (1) | 1 000 | **2 000** (2 000 distinct source rows aligned) | 0 |
| PG `table`, N = 3 000 (`j_01M504GNCZHRPR7VV1WWTZ77KA` → `customers_s1m4reg1_cxl3`) | 3 000 (3) | 3 000 | **4 000** (4 000 aligned) | 0 |
| Oracle key-less (ledgered) `table`, N = 3 000 (`j_01M504H0RZ95BNG3MPQGJJ2F5W` → `CRM_MASKED.ADDR_S1M4REG1_CXL`) | 4 000 (4) | 4 000 | **5 000** | — |

The Oracle run's pack also says `write_ledger_cleared: true`, and the ledger holds 0 rows for that job.

## 8. Evidence and reports (`p9`, the M2/M5 code the merge edited)

**Evidence packs.** All 9 packs of this lens were checked, as JSON and as printable HTML.
- 200, with 0 values.
- No `<script>`.
- The regulatory note is present.
- There is **no "UI scan" section** in any L2 pack (M4 adds it to scan packs only).

**Reports** (all 200, 0 values):
- `/reports/summary`: `raw_pii_sent_to_llm` 0, `submissions` 0, `personal_fields` 778.
- `/reports/jobs`.
- `/reports/evidence`: all 9 of my packs are listed.
- `/reports/api-rollups`: its document is 200 and `format=pdf` returns a `%PDF-` body.

**Before and after the run** (shared stack): conn sweeps passed +9, sweeps failed 0, failed_sweeps 0.

## 9. Logs, LLM metric, keys (D2), metadata database

**Logs** (`p7`): all 17 containers of the project, running and exited. Two of them are ephemeral containers of the captures-privacy lens, which carry the project label.

*Needles and coverage*
- 308 271 needles in 36 classes (≥ 8 normalised characters):
  - PG customers: full_name, national_id, tax_id, email, mobile;
  - Oracle CUSTOMERS: the same five plus ADDR_LINE1;
  - Mongo profiles: fullName, email, mobile, taxId, iban, line1;
  - the KYC document values and the demo site's values.
- Positive control: 36 / 36.
- Mongo's forward read stops at 2026-10-14T11:10:14Z (S1-M4 gates N2). I merged it with the largest working tail (448 887 lines from 11:42:17Z), so only the 32 minutes between them on 14 Oct are unread; the run window is fully read.

*Results*
- **Run window (since 15:39:37Z): 0 value hits in every container.** 0 tracebacks and 0 error-level lines.
- App warnings in the window are all `auth_rejected`: 74 scope, 29 key, 13 tenant. They come from the scope probes of other lenses and my 2 checks, and are value-free.
- 0 PEM private-key markers and 0 key-material field names.
- **All-time: 2 hits**, on one `source-pg` line from 2026-10-14 16:26:51Z. This is the known S1-M3 privacy-console r2 R2-N1 (N3).

**LLM metric**
- `dps_llm_raw_pii_sent_total 0.0` in the **app**, the **worker** (ops 9400) and the **browser-worker** (ops 9401), at 21:14 and at 21:30.
- Every probe also read the app metric itself before and after: 0.0.

**D2: DPS stores no key**
- The DEKs and seeds of the 4 job groups and 3 document calls appear **0** times in all container logs, both in the window and all-time.
- They appear **0** times in a `pg_dump --data-only` of every dps-db database.

**Metadata database values** (`p8b`)
- The needles were rebuilt from the deterministic seed model: the 20 000 customers of `seed_customers(20000, 7)` plus `seed_mongo`'s IBAN rule, the live PG table, the documents and the site. That gives 218 922 needles.
- The model reproduces PG's 19 325 names and 20 000 e-mails exactly. Positive control 31 / 31.
- Over the `dps` dump (17.3 MB, 25 tables) and the 3 `dps_test_*` leftovers: **0 hits in every table and column except `dps_scan_field.fill_synthesize`** (29; see N4).

## Blocking finding

### B1. A canceled run's evidence, progress and Reports row omit the batch in flight when the cancel landed

**Where**
- `dps/jobs.py`:
  - `cancel` (:1324–1330) sets every `queued` or `running` partition of the job to `canceled` at once. Its comment: running holders' "next fenced write fails and they abandon".
  - `checkpoint` (:1000) is fenced on `state = 'running'`.
- `dps/layers/connectors/jobs_run.py` `_run_partition` (about :1470–1505):
  - The holder commits the batch it is writing to the target (`write_batch`). For a ledgered load that includes the batch's ledger mark.
  - Its `checkpoint` then matches zero rows, and the holder calls `abandon_partition` and stops.
  - The committed batch is never added to the partition's `rows_written` or `batch_no`, or to `dps_tenant_usage`. `checkpoint` is the only place that charges the M5 daily row quota (code reading).
- `_finalize` then builds the pack from the partition rows.
- For a ledgered plan (`in_place`, `append`, key-less Oracle `table`), `_finalize` clears the target's write ledger for `outcome: canceled`. That deletes the only record of the extra batch.
- Pause does not have this problem: it flips only the job state, so the holder's checkpoint succeeds and it stops at `partition_control`.

**Contract it breaks**
- `api-m2.md` says every committed batch is checkpointed, and that cancel stops "running holders at their next fenced write".
- The evidence pack's `counts.rows_written` and the console built on it state what reached the target:
  - "Row counts N read · N written" (with a `pass` chip) on the pack;
  - "N rows · canceled" on Reports;
  - since `932075a`, "N rows written before it" for a stopped run.
- For a canceled `table` load, these under-state the pseudonymised personal data in the target by one batch per running partition. For a ledgered `in_place` or `append` run, they under-state the rows changed in place, and the ledger is gone.

**Evidence**
- §7: 3 of 3 live reproductions on main @ `8ea220a`.
  - Pack, job and Reports say 1 000 / 3 000 / 4 000; the targets hold 2 000 / 4 000 / 5 000 rows. Each target is a new table that only that job wrote.
  - The partition rows show `attempts 1` and `state canceled`, with `batch_no` 1 / 3 / 4.
  - The extra rows are obfuscated (0 survivors on the PG targets, row-aligned). This is an evidence-accuracy defect, not a leak.
- The existing test `tests/connectors/test_run_failures.py::test_cancel_mid_run_ends_canceled_with_evidence` cancels right after `prepare`, before any batch, so it cannot see this.

**Not caused by the merge**
- `jobs_run.py` is byte-identical to `d2fdc3f`.
- The merge's `jobs.cancel` change only adds the started-scan branch.
- So it has been present since M2. It is not fixed on any finish branch either: I checked the diffs of `fsec`, `fimg`, `fos`, `fdemo`, `fdocs`, `fllm`, `fpersona`, `fspec` and `fidcheck` against main for `jobs.py` and `jobs_run.py`.

**Fix**
Let the holder record the batch it committed under its lease, then stop. Either:
- (a) `checkpoint` accepts `state IN ('running', 'canceled')` for the same `locked_by` holder and returns whether to stop; or
- (b) `cancel` marks only the job canceled for running partitions (as pause does), and the holder sets its partition `canceled` in `abandon_partition` after its checkpoint.

`_finalize` already waits for canceled partitions with live leases (`_live_canceled`), so it would see complete counts. In both cases the usage row is charged.

For ledgered plans, `_finalize` could cross-check the ledger marks against the partition rows before clearing the ledger, and use the ledger's larger value.

**Tests**
- A run canceled between `write_batch` and `checkpoint`: patch the writer to call `jobs.cancel` after it commits. Then the pack's `counts.rows_written` must equal the target's row count, for a plain PG `table` load and for a ledgered one (`in_place`, or an Oracle key-less load).
- The same with 2 running partitions.
- `dps_tenant_usage` must be charged for the batch.
- Live re-check: cancel a 20 000-row PG `table` load and an Oracle `ADDRESS_HISTORY` load mid-run; the pack and Reports rows must equal `count(*)` of the target.

## Non-blocking notes

- **N1 (carried, S1-M3 engines r3 B1; fix pending on `fidcheck` `9fe68f4`).** The D-A post-write identifier check reads one 5 000-row page on Oracle and MongoDB, yet reports `scope: whole_table`.
  - Live on this HEAD: Oracle NATIONAL_ID checked 5 000 of 20 000 non-null values written, TAX_ID 3 030 of 11 989; Mongo iban 5 000 of 20 000, taxId 2 972 of 11 989. The PG control checks 20 000 of 20 000 and 11 989 of 11 989.
  - r3's scratch-data repro re-run here (`repro_idcheck_missed_hit.py`): one shared account number at target row 15 000 is missed as built (checked 5 000, `in_source` 0, review `[]`) and flagged with pages copied (20 000, 1, review). The scratch Oracle user and Mongo database were dropped (confirmed).
  - My own full-table D-A counts (§3) show this run's outputs are clean, so only the product's proof is short.
  - Not blocking here, as S2 regression r1 decided for a main-side fix: `fidcheck` already commits the fix ("the D-A identifier check reads every row on every engine…"), and a second fix on main would conflict. The post-merge verification of `fllm`/`fidcheck` must re-run a 20 000-row Oracle and Mongo synthesize: checked must equal the non-null values written.
- **N2 (known, documented).** MongoDB notes keep the source city (34 · 19 912): there is no city field to anchor it (r3 N1). Oracle obfuscate keeps the city because the policy keeps `CITY`.
  - Oracle synthesize writes a CITY and PIN that differ from the source row (row_same 0), although the snapshot says `keep`. Mongo pin is the same. Unchanged since r3.
- **N3 (known, S1-M3 privacy-console r2 R2-N1).** The 2 all-time hits are on one source-pg `ERROR: invalid input syntax for type integer: "<a demo e-mail>"` line from 14 Oct 16:26:51Z.
  - The S1-M3 security probe caused it deliberately with `CAST(email AS integer)` in a selection. PostgreSQL quotes the row value.
  - It is outside DPS's logs. The recommendation stays with F1: refuse text-to-non-text casts in PG selections.
- **N4 (information).** The 29 metadata-database hits are all in `dps_scan_field.fill_synthesize`, which by contract holds the display persona's synthetic value, never page data.
  - They come from 3 scan jobs: PERSON_NAME 12, EMAIL 12 (the name inside a persona e-mail), PHONE 3, USERNAME 2, out of 976 synthetic fill values.
  - `fill_obfuscate`: 0. The demo site's own values: 0.
  - These are the same surrogate-pool collisions as §3: the scanner never reads the bank tables. The S2 regression r1 dump (`m4`) had none because far fewer scans had run.
- **N5 (shared-stack interference; no effect on the results above).**
  - (a) Another verifier accepted a newer `bank.customers` snapshot at 21:14:11, so my first PG submissions got 409 `snapshot_not_accepted`, which is correct product behaviour.
    - My own re-accepts of `bank.customers` (21:16:15 and 21:18:17; the p11 resubmit at 21:25:06 also went through a rediscover-and-accept; p12 at 21:27:24 reused the accepted one) may in turn have superseded another verifier's snapshot.
  - (b) After 21:30, the d4-network lens's pytest reruns (`S1-M4-d4-network-r1/race_reruns.sh`, cwd = main checkout) reloaded main's Oracle and MongoDB demo sources at **test size** (2 400 customers, seed 7). Every comparison above ran on the 20 000-row sources before that; `src_state.after.json` is from 21:30. The metadata-DB scan was therefore redone with model-regenerated needles (`p8b`).
  - (c) The same pytest run rewrote the 54 tracked `docs/superpowers/build/m3-shots/*.png` (mtimes 21:31:44–21:32:19; known A-N2). Not by this lens: I ran no test, and I did not restore them, because that run was still in progress. Whoever finishes it should restore them to HEAD.
- **N6 (method).** Main's Mongo log cannot be read forward past 2026-10-14T11:10:14Z (gates N2). `lib.container_lines` merges the forward read with the largest working `--tail`, so a 32-minute window on 14 Oct (11:10–11:42Z) remains unreadable.

## Left on the stack

- **New targets** (obfuscated or synthetic rows only):
  - PG `bank_uat`: `customers_s1m4reg1b`, `customers_syn_s1m4reg1`, `customers_cop_s1m4reg1`, `customers_s1m4reg1_cxl1`, `customers_s1m4reg1_cxl3`.
  - Oracle `CRM_MASKED`: `CUSTOMERS_S1M4REG1`, `CUSTOMERS_S1M4REG1B`, `CUSTOMERS_SYN_S1M4REG1`, `ADDR_S1M4REG1_CXL`. My key-less Oracle load also created `DPS_WRITE_LEDGER`; it holds 0 rows for my job.
  - Mongo `crm_uat`: `profiles_s1m4reg1`, `profiles_s1m4reg1b`, `profiles_syn_s1m4reg1`.
- **Metadata:**
  - 5 job groups, one of them without jobs (my first `p3b` attempt died on a probe bug before submitting);
  - 13 run jobs with evidence: 9 succeeded, 4 canceled;
  - 13 discovery jobs: 6 snapshots accepted by me (`bank.customers` ×4, `CRM.CUSTOMERS`, `profiles`; the older ones are now superseded) and 7 left proposed (`p10`).
- **API key:** `key_01M504C9X1XE90Z4H2GNZ2GAYX` (jobs only), **disabled**.
- **Repository and stack:** no repository file changed by me. No compose `up`, `down`, restart or reseed by me. The stack is left running for the other lenses.

## Reproduce

```bash
P=~/.cache/dps-build/reports/S1-M4-regression-r1; cd $P   # run.sh = main .venv + contract §12.2 env
./run.sh src_state.py > src_state.before.json; ./run.sh metrics.py before; ./run.sh reports_snap.py before
./run.sh p1_setup.py                                          # discover + accept PG/Oracle/Mongo customers
./run.sh p2b_obfuscate.py <tag> & ./run.sh p3_synthesize.py <tag> & ./run.sh p4_documents.py; wait
./run.sh p3b_synthesize.py <tag>                               # PG surrogate + copula (snapshot refresh on 409)
./run.sh p5_compare_obf.py <tag>; ./run.sh p6_mongo_syn_align.py <tag>; ./run.sh p5d_notes.py <tag> <tag>
./run.sh p5e_notes_any_value.py; ./run.sh p5f_note_hits_explained.py; ./run.sh p5g_mongo_syn_city.py
./run.sh p10_discovery_parity.py; ./run.sh p11_cancel_scope.py; ./run.sh p12_cancel_more.py   # B1
./run.sh repro_idcheck_missed_hit.py                           # N1 (scratch Oracle user + Mongo DB, dropped)
./run.sh p9_evidence_reports.py; ./run.sh metrics.py after; ./run.sh reports_snap.py after
./run.sh p7_logscan.py; ./run.sh p8b_dbscan_full.py; ./run.sh p8c_scan_field_hits.py; ./run.sh selfscan2.py
```
(Target names derive from the tag. A rerun needs new tags and new `cxl` table names, because `p11` and `p12` use fixed ones.)

## Resume addendum (22:03–22:08 IST)

The run that wrote this report ended before it returned its verdict. This attempt re-checked it without repeating any probe:

- **Tree and stack unchanged.** `main` is still at `8ea220a`, and the only tracked change is `.serena/project.yml`, which is not mine. All 11 services of `data-privacy-services` are running. I did no restart.
- **N5(c) resolved.** The 54 `docs/superpowers/build/m3-shots/*.png` now show 0 modified files in `git status`.
- **B1 re-confirmed by reading the code at HEAD.**
  - `jobs.cancel` (`dps/jobs.py` :1324–1330) sets every queued or running partition to `canceled` at once.
  - `jobs.checkpoint` (:1000–1049) is fenced on `state = 'running'`. It charges `dps_tenant_usage` only when that fence matches.
  - `_run_partition` (`jobs_run.py` :1475–1499) commits `write_batch` (with its ledger mark) before `checkpoint`. When `checkpoint` returns False, it calls `abandon_partition` and raises `_Lost`. The committed batch is never counted.
  - The probe outputs agree:
    - `p12_cancel_more.json` A: partition `batch_no 3`, `attempts 1`, pack 3 000, target 4 000, aligned 4 000, 0 survivors;
    - the `p11` mid-run case: 1 000 against 2 000.
  - No sibling lens reports this defect: not console r1 (its B1 and B2 are other M2 defects), and not gates r1, d4-network r1 or adaptations r1.
- **The S2 r3 open findings, from this lens.**
  - Console B1 (`932075a`) changes only wording, and the console and gates lenses verified it live.
    - The new wording "N rows written before it" uses `counts.rows_written`.
    - For a failed-closed (leakage) stop that number is exact: the sweep raises in `dps/engine/batch.py` (:996 and :1118), inside `transform`, before `write_batch`.
    - For a **canceled** run it is one batch short per running partition. That is this report's B1, so the fixed wording still shows a count that is too low for a cancel.
  - Captures-privacy B1a–c (`40d8b34`) is outside this lens's method. The gates verifier (r1 §4.2) and the captures-privacy lens r1 verified it; the latter also reports three new edge findings of its own.

Verdict unchanged: **FAIL, 1 blocking finding (B1)**. It is M2 code that has been on main since M2, not a merge regression.
