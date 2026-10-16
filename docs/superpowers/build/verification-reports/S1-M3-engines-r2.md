# S1-M3 — post-merge verification of `main` after the M3 merge: ENGINES lens, round 2

**Verdict: FAIL — 2 blocking findings (B1, B2).** All four round-1 blocking findings are fixed in what
they named:

- key-less Oracle synthesize (r1 B1);
- MongoDB → `files` in every format (r1 B3);
- Oracle `TIMESTAMP(9)` → parquet (r1 B4);
- the first name in notes for every first name of 3+ letters (r1 B2).

Obfuscate on all three engines, the three-way consistency check, the D-A key and identifier checks and
resume after kill (16 cases) all hold. Two defects remain:

- **B1 (r1 B2 not fully fixed).** Short values of a row's own replaced columns still reach synthetic
  notes, because `_row_value_spans` has length floors. This covers **2-letter first names** (28 / 20 000
  rows on Oracle surrogate, Oracle copula and MongoDB; tables and files) and **3-letter cities**
  (34 / 20 000 on Oracle, where the `CITY` column itself is surrogated). The fix round's tests and live
  proofs skip names under 3 letters by construction.
- **B2 (new).** A key-less (ROWID-paged) Oracle source loaded into a **new** table on a separate target
  connection leaves its write-ledger row. This is the demo flow `CRM` → `CRM_MASKED` with
  `create_if_missing`. Finalize cannot clear the ledger, and the evidence says
  `write_ledger_cleared: false` next to `passed: true`.

| | |
|---|---|
| Date | 2026-10-14, 21:39–22:12 IST |
| Tree verified | `main` @ `5c11e49` (S1-M3 fix round 1, on top of the M3 merge `b878c50`); `git status` clean before and after |
| Stack | compose project `data-privacy-services`, started by the gates verifier, not restarted, no `down`. Ports: app 8400, dps-db 5441, source-pg 5442, MinIO 9010, Oracle 1522, Mongo 27018, tokenizer-mock 8430. `/readyz`: ready, NER `patterns-only`, FPE ff1, LLM stub. |
| Image = HEAD | The sha256 of all 172 tracked files under `dps/` and `tools/` (without `tools/dev`) is identical in the `app` container and the checkout (`image_vs_head.diff` is empty). The fixed modules are identical in `worker` too. |
| Python | `.venv` of the main checkout; contract §12.2 env (`env.sh`), `PYTHONDONTWRITEBYTECODE=1` |
| Probes and outputs | `/Users/neelu/.cache/dps-build/reports/S1-M3-engines-r2/`: scripts `p1`–`p9`, `repro_*.py`, `kill/test_probe_m3_kill.py`; outputs `*.json`, `*.out`, `*.log` |
| Repository files modified | none |
| Shared demo sources | unchanged: counts and seed markers are equal before and after (`src_state.before.json` = `src_state.after.json`; seed 7, 20 000 customers on PostgreSQL, Oracle and MongoDB) |
| Values in my outputs | 0. `selfscan.py` checked 149 732 source values and 865 first names (6+ letters, whole word) against the 62 output files. The same needles, the 2-letter first names and the cities, checked against this report, also give 0. |

Everything below is counts, codes, job ids and column names. Group keys and seeds stayed in the memory of
the probe process that created the group. "Row-aligned" means the target row is compared with **its own**
source row. Alignment is through a kept column, or through the keyed pseudonym recomputed in memory.

## Round-1 findings re-verified

| r1 | What was broken | Round 2 (live stack, demo data) | Status |
|---|---|---|---|
| B1 | Key-less Oracle synthesize failed `leakage` | See detail below. | **fixed** |
| B2 | Synthetic CRM notes kept the source first name (20 000 / 20 000) | First names of 3+ letters: **0 / 19 972** in every Oracle and MongoDB target, both modes, tables and files. The source-vs-itself control finds 19 972. **The 28 two-letter first names still survive.** | **partly fixed → B1** |
| B3 | MongoDB → `files` failed (`TypeError`) | See detail below. | **fixed** |
| B4 | Oracle `TIMESTAMP(9)` → parquet failed (`ArrowTypeError`) | See detail below. | **fixed** |

**B1 detail.**

- `CRM.ADDRESS_HISTORY` → table (surrogate and copula), → parquet and → jsonl all succeed with 40 000
  rows and verification `passed`.
- The targets hold the 5 table columns, with no ROWID in the table, the file or the manifest.
- `CUSTOMER_ID`: 40 000 / 40 000 in [10⁹, 2·10⁹), 0 source ids.
- Customer-group-aligned `ADDR_LINE1`, `CITY` and `PIN_CODE` matches: 0.

**B3 detail.**

- Obfuscate and synthesize × jsonl, csv and parquet: 6 / 6 succeed with 20 000 rows each, 1 attempt per
  partition.
- Manifest types are `document` for `accounts`, `contact`, `kyc`, `profile` and `tags`.
- Row-aligned survivors are 0 on every sensitive leaf.

**B4 detail.**

- Obfuscate and synthesize × parquet and csv: 4 / 4 succeed.
- `LAST_CONTACT_AT` is text in all 20 000 rows and equals the source FF9 exactly in 20 000 / 20 000.

## Blocking findings

### B1. Short own values of replaced columns are copied into synthetic notes (D-A; r1 B2 not fully fixed)

**Where.** `dps/engine/batch.py`:

- `_needles` searches a whole value only at `EXACT_MIN = 4` normalised characters or more (`:433`).
- It searches a name's words only at `NAME_PART_MIN = 3` letters or more (`:176`, `:436`).
- The sweep's `exact` rule uses the same `EXACT_MIN` (`:408`, `:523`), so nothing flags the copy.

The fix's own tests skip short names. `tests/e2e/test_m3_free_text_names.py::_keeps` and
`tests/test_da_free_text_spans.py::_first_name_kept` return `False` when `len(first) < 3`. The fix round's
live probe did the same, and so did my r1 probe. That is why "0 / 20 000" was reported against a control
of 19 972.

**Evidence (live; `p5c_firstname.py`, `p5d_short_values.py` → `short_values_eng2.json`,
`p7b_files_slots.py` → `files_slots_eng2.json`).**

- The demo has 28 customers with a 2-letter first name (shape `Aa`; 25 IN, 3 US) and 34 with a
  3-letter city.
- The note's name slot ("KYC call with …;") and city slot ("Prefers branch in ….") were compared with the
  aligned source row.

| Target (all `succeeded`, verification `passed`, `review_required: false`) | Name slot = source first name: first name < 3 letters / 3+ letters | City slot = source city: city < 4 / 4+ |
|---|---|---|
| Oracle `CUSTOMERS_SYN_ENG2` (surrogate) | **28 / 28** · 0 / 19 972 | **34 / 34** · 0 / 19 966 (the `CITY` column is surrogated) |
| Oracle `CUSTOMERS_COP_ENG2` (copula) | **28 / 28** · 0 / 19 972 | **34 / 34** · 0 / 19 966 |
| MongoDB `profiles_syn_eng2` | **28 / 28** · 0 / 19 972 | no city field (see N1) |
| Synthetic files: Oracle parquet and csv; MongoDB jsonl, csv and parquet | **28 / 28** in each · 0 | Oracle: **34 / 34** · 0 |

- **Each of these rows is linkable to its source row.** On Oracle, the synthetic row keeps the source
  `LAST_CONTACT_AT` (TIMESTAMP(9), unique per row; N3). On MongoDB it keeps `balance`, `verifiedAt` and
  `riskScore`.
- **The synthetic row contradicts its own note.** For those 28 rows the synthetic `FULL_NAME` is a
  different surrogate (`source_first_eq_synthetic_first` 0), so the note names the real customer.
- **Obfuscate** keeps the same 28 first names in Oracle and MongoDB notes. Its label is "still personal
  data", so it is not blocking on its own, but the same fix closes it.

**Offline repro** (`repro_short_values.py` → `.out`). Setup is that of `tests/test_da_free_text_spans.py`:
preset `gdpr-pseudonymise`, no NER, `verify="strict"`, `CITY` planned `surrogate`. Sample: the 62 demo
customers plus 200 controls.

| Run | First names kept | Cities kept | Verification | Controls |
|---|---|---|---|---|
| As built | 28 / 28 | 34 / 34 | passed | 0 kept |
| Row-value floor of 2 (`_needles` patched in the probe process only; the sweep's `EXACT_MIN` unchanged) | 0 / 28 | 0 / 34 | passed | unchanged |

`repro_short_names.py` shows the same for `NAME_PART_MIN = 2` alone (first names 28 → 0, both modes).

**Fix.**

- Give the whole-word row-value spans their own floor of 2 letters, for name words and for whole values.
  Whole-word, row-own and caseless matching already prevents substring hits, and over-masking a 2-letter
  word that equals the row's own name or city is harmless.
- Keep the sweep's `EXACT_MIN` / `CONTAIN_MIN` as they are.
- Add tests with a 2-letter first name and a 3-letter city, on the Oracle and MongoDB shapes, in both
  modes.
- Drop the `len(first) < 3` escape in the two test helpers so they count every name. Assert on the name
  slot, as `p5d` does.
- Correct HANDOFF ("words of 3+ letters") and the M3-DEVIATIONS claim "0 first names kept".

### B2. A key-less Oracle `table` load into a new table on another connection never clears its write ledger

**Where.** `dps/layers/connectors/jobs_run.py:2251` (`_clear_ledger`).

```python
shape = plan.target.target_shape or await connector.describe(spec.source.object)
```

- A key-less Oracle source makes a `table` load ledgered (`plan.prepare["ledger"]`).
- When prepare **creates** the target table, `target_shape` is `None`. Finalize then describes the
  **source** object on the **target** connection.
- The target login cannot see the source: `CRM_MASKED` sees 0 columns of `CRM.ADDRESS_HISTORY`. So
  `describe` raises, `_best_effort` swallows it, and the ledger rows stay.
- `OracleWriter.clear_ledger` itself needs no shape.
- The r1 kill probe and the DoD test use one connection as both source and target, so they never reach
  this path.

**Evidence.**

1. **After this round's live runs** (`CRM_MASKED.DPS_WRITE_LEDGER`, created 16:14:50 UTC by them), 3
   rows were left. Each is partition 0, batch 40, with the last source ROWID in `LAST_KEY`. They belong
   to the three key-less table loads into new tables:

   | Job | Run |
   |---|---|
   | `j_01M4XK3E2QSJ7GQ1E7SWT82CB0` | `ADDR_SYN_ENG2` |
   | `j_01M4XK3E3QT0BR2X4KGWTTE7JS` | `ADDR_COP_ENG2` |
   | `j_01M4XK9HHV9ZJ71N8TE6QKFPFK` | `ADDR_ENG2` obfuscate |

   Each evidence pack says `write_ledger_cleared: false`, `passed: true`. The keyed `CUSTOMERS` run used
   as a control has no ledger.
2. **Isolated in one group** (`p9_ledger.py` → `ledger_eng2.json`):

   | Run | `write_ledger_cleared` | Ledger rows left |
   |---|---|---|
   | Same key-less obfuscate into the **existing** `ADDR_ENG2` | `true` | 0 |
   | Same run into a **new** `ADDR_LEDGER_220646` | `false` | **1** |

3. **Consequences.**
   - The contract §12.3 / M2 exit property "ledger empty after finalize" fails for the default
     cross-connection flow.
   - A row of technical residue that holds a source row locator is left in the masked or UAT schema, per
     job. The fix round removed the ROWID from files for the same reason.
   - The rows accumulate with every run.
   - The pack still reports `passed` with no review.

**Fix.**

- Clear the ledger without the source shape: describe `plan.target.object` on the target connection (it
  exists by finalize), or let the writer's `clear_ledger` work from the owner alone.
- Add an integration test: key-less Oracle source → `table` with `create_if_missing` on a **separate**
  target connection. It must give `write_ledger_cleared: true` and 0 ledger rows.
- Extend the resume-after-kill matrix with that cross-connection case.

## What holds (evidence)

### 1. Obfuscate on all three engines → `table` targets, per column source vs target (`p2`, `p4` → `compare_obf_eng2.json`)

**Setup.** One group, `grp_01M4XK3BYSVGHHBE032XWR0MMK` (kid `k_25e762c962e10085`). Default policy, with
snapshots discovered and accepted right before submit. All 9 jobs ended `succeeded`, verification
`passed`, `review_required: false`, label "pseudonymised — still personal data", `kept_column_hits` 0.

| Source → target | Rows src / tgt | Aligned on | Row-aligned survivors on sensitive columns |
|---|---|---|---|
| PG `bank.customers` → `bank_uat.customers_eng2` | 20 000 / 20 000 | 8 kept columns (20 000) | **0** (customer_id, full_name, date_of_birth, national_id, tax_id, email, mobile, notes) |
| PG `bank.accounts` → `accounts_eng2` | 30 000 / 30 000 | account_id (30 000) | **0** (customer_id, account_number, iban, ifsc) |
| Oracle `CRM.CUSTOMERS` → `CRM_MASKED.CUSTOMERS_ENG2` | 20 000 / 20 000 | LAST_CONTACT_AT (20 000) | **0** (10 columns incl. NOTES, PIN_CODE) |
| Oracle `CRM.ADDRESS_HISTORY` (key-less) → `ADDR_ENG2` | 40 000 / 40 000 | CITY+VALID_FROM (38 993) | **0** (CUSTOMER_ID, ADDR_LINE1, PIN_CODE); 5 columns, no ROWID |
| Oracle `CRM.KYC_DOCUMENTS` (CHAR(12) key) → `KYC_ENG2` | 20 000 / 20 000 | DOC_REF | **0** |
| Oracle `CRM.INTERACTIONS` (TS(9) composite key, JSON CLOB) → `INTER_ENG2` | 40 000 / 40 000 | EVENT_TS+CHANNEL | **0** (CUSTOMER_ID, AGENT_EMAIL, PAYLOAD) |
| Oracle `LOANS.APPLICANTS` (string key) → `APPL_ENG2` | 5 000 / 5 000 | APPLICATION_ID | **0** |
| Oracle `CRM.CUSTOMER_EVENTS` (identity key; new in r2) → `EVENTS_ENG2` | 40 000 / 40 000 | EVENT_ID | **0** (CUSTOMER_ID, CONTACT_EMAIL) |
| MongoDB `crm.profiles` → `crm_uat.profiles_eng2` | 20 000 / 20 000 | `_id` | **0** on all 10 sensitive leaves |

- **Set hits are all explained:**
  - `fpe_digits` customer ids that land on another real id: 0–16 per table;
  - `date_shift` DOB collisions within the date domain.
- **Identifier sweep:** hits only in columns kept as non-personal keys (`_id`, `account_id`, `DOC_REF`,
  `APPLICATION_ID`, `relationship_manager_id`) and in those FPE collisions.
- **Free text, row-aligned** (`notes_text`):
  - Oracle and MongoDB notes keep the source first name **0 / 19 972** (control 19 972). The source
    e-mail, full name and mobile digits are kept 0 / 20 000.
  - The 28 two-letter names stay (B1).
  - Oracle's city stays in the notes as the policy keeps the `CITY` column (19 946).

### 2. Three-way consistency check (PG ↔ MongoDB ↔ Oracle), overlap > 0

**Members.** Oracle `$.CUSTOMER_ID` is the anchor (numeric). MongoDB `$.customerId` is `prefixed_digits`
(width 8, prefix 5). PG `$.customer_id` is numeric. All three are `fpe_digits` with `params_hash`
`44136fa355b3`.

| Check | HTTP | Result | Pairs consistent | Coverage |
|---|---|---|---|---|
| derived, sample 1 000 | 200 | passed | **3 / 3** | 1 000 / 1 000 each (1.0) |
| derived, sample 10 000 (1.4 s) | 200 | passed | **3 / 3** | 10 000 / 10 000 each (1.0) |
| explicit pair CUSTOMERS ↔ ADDRESS_HISTORY | 200 | passed | 1 / 1 | 1 000 / 1 000 |

- **Attributes:** EMAIL, PERSON_NAME and PHONE agree 10 000 / 10 000; NATIONAL_ID and STREET_ADDRESS
  10 000 / 10 000; PAN_IN 6 059 / 6 059; POSTAL_CODE agrees but does not count toward the pass.
  DATE_OF_BIRTH `date_shift` agrees 14 / 10 000; this is informational and known (it depends on the
  anchor).
- **Group:** `last_check` passed.
- **Independent containment:** obfuscated customer ids are identical on PG, MongoDB and Oracle
  (20 000 = 20 000 = 20 000). The child-table ids of ADDRESS_HISTORY, KYC, INTERACTIONS, APPLICANTS and
  CUSTOMER_EVENTS are all contained in `CUSTOMERS_ENG2` (20 000 / 20 000; APPLICANTS 5 000 / 5 000).

### 3. Synthesize and D-A (`p3`, `p5` → `compare_syn_eng2.json`, `p5b`, `p5c`, `p5d`)

**Setup.** One group, `grp_01M4XK3E1S0YNGEGFKX92QCWE1`. Pseudonyms were recomputed from the group seed in
memory to align rows; alignment was 100 % on every target.

**Jobs.** All 11 accepted jobs `succeeded`, `passed`, `review_required: false`, label "synthetic —
measured, not claimed anonymous":

- PG: customers surrogate and copula, accounts.
- Oracle: CUSTOMERS surrogate and copula, ADDRESS_HISTORY surrogate and copula, KYC, APPLICANTS,
  CUSTOMER_EVENTS.
- MongoDB: profiles.

**Evidence checks.** `identifier_check` `in_source` is 0 on every checked column. Copula
`exact_row_matches` is 0.

**Row-aligned results.**

- `row_same` = 0 on every surrogate and pseudonymised column of every target.
- Kept non-personal columns are equal, or redrawn by the copula.
- Key-less ADDRESS_HISTORY, customer-group aligned: `ADDR_LINE1`, `CITY`, `PIN_CODE` and `CUSTOMER_ID`
  match 0. `VALID_FROM` is kept by the surrogate run and modelled by the copula run (0).
- Own e-mail, phone and full name in synthetic notes: 0 on Oracle, PostgreSQL and MongoDB. The exceptions
  are the short values in B1.

**Keys (D-A).**

- **Integer pseudonyms:** 100 % in [10⁹, 2·10⁹) and **0 in the source** for:
  - `customer_id` / `CUSTOMER_ID` in 10 tables;
  - `account_id` (30 000 unique);
  - `EVENT_ID` (40 000 unique);
  - `relationship_manager_id` (0 real employee ids).
- **MongoDB `_id`:** 20 000 / 20 000 are ObjectIds, unique, **0 in the source**.
- **Other keys:** `customerId` 0 in the source; `APPLICATION_ID` 0.
- **ROWID:** written to no Oracle target (tables, files and manifests).

**Identifier sweep** (13 classes: ids, ObjectIds, national ids, tax ids / PANs, IBANs, account numbers,
application ids, e-mails, phones; whole cells and tokens in text):

- 0 real customer ids, ObjectIds, national ids, tax ids, IBANs, account numbers, application ids or
  e-mails in any synthetic target.
- Remaining hits are explained (N5): surrogate phones in the fictional range (about 11 %), 6-digit PIN
  codes matching the digit form of a NINO, and `DOC_REF` short-key collisions (N2).

**Joins under the group seed:**

- PG and Oracle synthetic customer pseudonyms are identical: 20 000 / 20 000.
- Child tables are contained in `CUSTOMERS_SYN_ENG2`: KYC, APPLICANTS, ADDR (surrogate and copula) and
  EVENTS, 20 000 / 20 000 (APPLICANTS 5 000 / 5 000).

**Refused by design** (codes, nothing written):

| Run | Result |
|---|---|
| MongoDB copula | 422 `tabular_invalid` |
| Oracle `INTERACTIONS` | 422 `synthesize_pseudonym_unsupported` (N4) |
| `in_place` synthesize on a sensitive key | 422 `synthesize_in_place_sensitive_key` |
| `in_place` without `confirm_write` | 422 `confirm_write_required` (Oracle and MongoDB) |

The probe-owned copies were unchanged. The temporary connections were deleted (204). Script `p6`.

### 4. `files` targets (`p7` → `files_eng2.json`, `p8` → `files8_eng2.json`)

All 24 runs `succeeded` with no partition error and verification `passed`. The 14 `p7` runs also record
1 attempt per partition and 0 null `values` in every pack.

**Coverage.**

- MongoDB: obfuscate and synthesize × jsonl, csv and parquet.
- Oracle CUSTOMERS: obfuscate and synthesize × parquet and csv; copula → parquet.
- Oracle ADDRESS_HISTORY (key-less): obfuscate and synthesize × parquet and jsonl.
- Into parquet: PG customers copula, PG accounts copula and obfuscate; Oracle APPLICANTS obfuscate,
  synthesize and copula (NUMBER(14,2)); CUSTOMER_EVENTS synthesize (identity NUMBER); KYC synthesize
  (CHAR(12) key); INTERACTIONS obfuscate (TS(9) composite key, JSON CLOB).

**Results.**

- Row-aligned survivors are 0 on every sensitive column or leaf.
- Synthetic files: `_id` 0 in the source (24-hex, unique), `customerId` 0, `CUSTOMER_ID` 100 % in band
  with 0 in the source, `EVENT_ID` / `APPLICATION_ID` / `account_id` 0 in the source.
- No ROWID in any file or manifest.

### 5. Resume after kill on Oracle and MongoDB

**DoD test on main.** `tests/e2e/test_m3_resume_after_kill.py`: **4 passed** in 104.6 s
(oracle / mongo × in_place / append; `dod_resume.log`).

**Extra cases.** `kill/test_probe_m3_kill.py` (`kill/kill_results.jsonl`), run as two parallel sessions:
Oracle 8 passed in 337 s, MongoDB 5 passed in 339 s. Each case uses 3 000 rows and SIGKILLs the first
worker after at least 5 committed batches; a second worker finishes the run. Each session is isolated:
its own Oracle owner, its own MongoDB database and its own metadata database. No residue is left
(0 `dps_test_*` databases, 0 `DPS_T_*` users, 0 `dps_t_*` databases).

| Case | Killed run | Digest = reference | Resumed partitions (evidence) | Ledger rows | Survivors | ROWID written | Values in worker logs |
|---|---|---|---|---|---|---|---|
| Oracle table obfuscate | succeeded | **equal** (3 000) | 3 / 3 (3) | 0 | 0 | no | 0 |
| Oracle key-less table obfuscate | succeeded | **equal** | 3 / 3 (3) | 0 | 0 | no | 0 |
| Oracle files jsonl obfuscate | succeeded | **equal** | 3 / 3 (3) | 0 | 0 | no | 0 |
| Oracle files **parquet** obfuscate (TS(9); r1 B4) | succeeded | **equal** | 3 / 3 (3) | 0 | 0 | no | 0 |
| Oracle table synthesize | succeeded | **equal** | 3 / 3 (3) | 0 | 0 | no | 0 |
| Oracle **key-less table synthesize** (r1 B1) | succeeded | **equal** | 3 / 3 (3) | 0 | 0 | no | 0 |
| Oracle **key-less files parquet synthesize** (r1 B1) | succeeded | **equal** | 3 / 3 (3) | 0 | 0 | no | 0 |
| MongoDB table obfuscate | succeeded | **equal** | 3 / 3 (3) | 0 | 0 | – | 0 |
| MongoDB **files jsonl** obfuscate (r1 B3) | succeeded | **equal** | 3 / 3 (3) | 0 | 0 | – | 0 |
| MongoDB **files parquet** obfuscate (r1 B3) | succeeded | **equal** | 3 / 3 (3) | 0 | 0 | – | 0 |
| MongoDB table synthesize | succeeded | **equal** | 3 / 3 (3) | 0 | 0 | – | 0 |
| MongoDB **files csv** synthesize (r1 B3) | succeeded | **equal** | 3 / 3 (3) | 0 | 0 | – | 0 |

- **Key-less synthesize in process** (`test_keyless_oracle_synthesize[as-built]`): succeeded, 3 000 rows,
  target columns are the 5 table columns. `CUSTOMER_ID` is 3 000 / 3 000 in band and 0 in the source;
  `ADDR_LINE1` 0 in the source; verification passed.
- **Survivors** counts seeded e-mails and national ids (plus names for obfuscate). The probe records
  synthetic full names that equal some seeded name separately; there are 2–44 per case, from the same
  surrogate pool, and none is a row-aligned copy.

### 6. Logs and metrics

- **Container logs** (`logscan.py` → `logscan.json`; app, worker and tokenizer-mock since 16:10 UTC, ANSI
  colour codes stripped):
  - **0 hits** of 159 732 source values and **0 whole-word hits** of 865 first names;
  - 0 tracebacks and 0 error-level lines;
  - the planted controls are found.
- **App warnings:** 1 `auth_rejected` (`reason=tenant`) and 2 `oracle_call_failed` (`driver_code=ORA-01722|ORA-01841`, `op=count`) at 16:26 UTC. None came from my probes (no tenant header, no selection counts); they carry no values.
- **Metrics:** `/metrics` `dps_llm_raw_pii_sent_total 0.0`.

## Non-blocking notes

- **N1 (decision needed). MongoDB notes keep the source city.** The `crm.profiles` documents have no city
  field; the city exists only in the notes ("Prefers branch in …"). Without NER no detector and no
  row-value span touches it:
  - synthetic notes keep the source city in **19 946 / 20 000** documents (tables and files);
  - obfuscated notes keep it too.

  This is the NER limitation class (HANDOFF names only "names of other people").

  - The synthesize pack still says `passed` with no coverage caveat; r1 N1's evidence suggestion is still
    open.
  - Options: in synthesize mode without NER, generate or redact free text instead of span-surrogating it,
    or mark free-text columns `review_required` / add a coverage caveat.
  - At least document it: undetected free text is copied verbatim into synthetic output.
- **N2 (carried). Short text keys collide with other real keys.** `DOC_REF` pseudonyms equal another
  document's real ref 376 / 20 000 times (table) and 374 times (parquet); none is the row's own. The
  synthesize `identifier_check` does not cover keys.
- **N3 (carried). Oracle synthesize copies the unique TIMESTAMP(9) `LAST_CONTACT_AT`.** This holds for
  the surrogate and copula runs, in tables and files: 20 000 / 20 000. It is a row locator, and it makes
  B1's rows linkable. `VALID_FROM` (surrogate) and MongoDB `balance` / `verifiedAt` / `riskScore` are
  likewise kept.
- **N4 (carried).** `CRM.INTERACTIONS` (a composite key with a TIMESTAMP part) refuses synthesize with
  422 `synthesize_pseudonym_unsupported`.
- **N5 (carried).** Explained set hits: FPE ids landing on other real ids, date-domain DOBs, phones in the
  fictional range, digit-only NINO forms against PIN codes, surrogate addresses equal to another row's
  address (5–11).
- **N6. The consistency check accepts synthesize groups and reports them misleadingly.** On the synthesize
  group it returned 200 `passed: false`, 36 / 55 pairs (`consistency_syn_group.json`):
  - members are labelled with the snapshot's obfuscate method (`fpe_digits`);
  - MongoDB `customerId` is found 0 times, because a text pseudonym (`AAAA-99999999`, letters and digits
    permuted) cannot join the integer pseudonym of `CUSTOMER_ID`.

  The cross-form join holds in obfuscate mode (§6.5) but not in synthesize mode, by the contract's
  text-pseudonym rule. Options: refuse synthesize members (`member_unsupported`) or evaluate pseudonyms,
  and document the limitation. This probe's run left `last_check` = failed on that probe group.
- **N7. Concurrent verifiers supersede snapshots (r1 N6).** Each phase discovered right before
  submitting; no 409s this round.

## Commands

```bash
cd /Users/neelu/document_intelligence/data-privacy-services
source ~/.cache/dps-build/reports/S1-M3-engines-r2/env.sh   # contract §12.2 + PYTHONDONTWRITEBYTECODE
DPS_TEST_REQUIRE_SERVICES=1 .venv/bin/python -m pytest -p no:cacheprovider -q -rA tests/e2e/test_m3_resume_after_kill.py
DPS_TEST_REQUIRE_SERVICES=1 .venv/bin/python -m pytest -c pyproject.toml --rootdir . -p tests.conftest -p no:cacheprovider -q -rA \
  ~/.cache/dps-build/reports/S1-M3-engines-r2/kill/test_probe_m3_kill.py -k oracle   # and -k mongo, in parallel
cd ~/.cache/dps-build/reports/S1-M3-engines-r2
python p1_setup.py; python p2_obfuscate.py eng2 & python p3_synthesize.py eng2; python p4_compare_obf.py eng2
python p5_compare_syn.py eng2; python p5b_text_aligned.py eng2; python p5c_firstname.py eng2; python p5d_short_values.py eng2
python p6_inplace.py eng2; python p7_files.py eng2; python p7b_files_slots.py eng2; python p8_files_extra.py eng2; python p9_ledger.py
python repro_short_names.py; python repro_short_values.py; python logscan.py 2026-10-14T16:10:00Z; python selfscan.py
```

(`p2` and `p3` printed their results; `obf_eng2.json` and `syn_eng2.json` hold them, because the two
processes shared `state.json`.)

## State left behind

- **Targets on the shared stack**, named `*_eng2` / `*_ENG2`:
  - `bank_uat`: 5 tables;
  - `crm_uat`: 2 collections;
  - `CRM_MASKED`: 13 tables plus `ADDR_LEDGER_220646` (p9);
  - MinIO `dps-exports/eng-r2/eng2/…`: 24 file runs.
- **Job groups** `eng-r2-*`.
- **`CRM_MASKED.DPS_WRITE_LEDGER`** holds the 4 orphaned ledger rows of B2. They are left as evidence.
- **Not changed:** the repository (clean at `5c11e49`) and the demo sources. The stack is still running.
