# S1-M3 — post-merge verification of `main` after the M3 merge: ENGINES lens, round 3

**Verdict: FAIL — 1 blocking finding (B1, new).** Both round-2 blocking findings are fixed:

- **r2 B1 (short own values in notes):** the 28 two-letter first names and the 34 three-letter cities
  are now replaced in every Oracle and MongoDB target, tables and files, both modes.
  - First names: 0 / 28 short and 0 / 19 972 longer stay in the notes.
  - Cities in synthetic Oracle notes: 0 / 34 and 0 / 19 966.
- **r2 B2 (write ledger of a key-less load on another login):** finalize now clears it.
  - Every key-less Oracle `table` load into a new `CRM_MASKED` table reports `write_ledger_cleared: true`.
  - This covers obfuscate, surrogate and copula, live and under kill.
  - 0 ledger rows are left.

Obfuscate on all three engines, synthesize, the three-way consistency check, the D-A key checks and
resume after kill all hold. Resume after kill covers 5 DoD cases and 17 probe cases.

The new blocking defect:

- **B1. The D-A post-write identifier check reads only the first 5 000 rows on Oracle and MongoDB,**
  on both the target and the source side.
- It still reports `scope: whole_table`.
- So a real identifier in any later row is never flagged, and the pack says `passed`.
- Live packs show this: on 20 000-row targets, Oracle `NATIONAL_ID` is `checked: 5000` while
  PostgreSQL `national_id` is `checked: 20000`.
- A scratch-data repro shows it deterministically. One real value at row 15 000 gives `in_source: 0`
  and `review_columns: []` as built, and `in_source: 1` and review once pages are not aliased.
- The defect was already present in round 2. Its packs say `checked: 5000` on the same columns.

| | |
|---|---|
| Date | 2026-10-15, 00:25–00:58 IST |
| Tree verified | `main` @ `d2fdc3f` (S1-M3 fix round 2, on top of the M3 merge `b878c50`); `git status` clean before and after, HEAD unchanged, `git status --ignored` listing unchanged |
| Stack | compose project `data-privacy-services`, left running by the gates verifier (r3). I did not restart, rebuild or `down` it. Ports: app 8400, dps-db 5441, source-pg 5442, MinIO 9010, Oracle 1522, Mongo 27018, tokenizer-mock 8430. `/readyz` ready. |
| Image = HEAD | sha256 of all 172 tracked files under `dps/` and `tools/` (without `tools/dev`) is identical in `app` and in `worker` and in the checkout (`image_vs_head.diff`, `worker_vs_head.diff` empty); image `cacb4f1d49a5`, 0 restarts |
| Python | `.venv` of the main checkout; contract §12.2 env (`env.sh`), `PYTHONDONTWRITEBYTECODE=1` |
| Probes and outputs | `/Users/neelu/.cache/dps-build/reports/S1-M3-engines-r3/`: `p1`–`p9`, `repro_*.py`, `kill/test_probe_m3_kill.py`; outputs `*.json`, `*.out`, `*.log`, `kill/kill_results.jsonl` |
| Repository files modified | none |
| Shared demo sources | unchanged: counts and seed markers equal before and after (`src_state.before.json` / `.after.json`; seed 7, 20 000 customers on PostgreSQL, Oracle and MongoDB) |
| Values in my outputs | 0: `selfscan.py` checked 149 732 source values, 865 first names (6+ letters, whole word) and the short first names / cities (case-sensitive whole word) against all 77 output files |

Only counts, codes, job ids and column names appear below. "Row-aligned" means each target row is
compared with its own source row. Rows are aligned through a kept column, or through the keyed
pseudonym, which is recomputed in the probe process's memory from the group seed. That seed is never
printed or stored.

## Round-2 findings re-verified

### r2 B1 — short own values in notes → **fixed**

`p5d_short_values.py` → `short_values_eng3.json` (tables) and `p7b_files_slots.py` →
`files_slots_eng3.json` (files). They count the note's name slot ("KYC call with …;") and city slot
("Prefers branch in …"), row-aligned.

| Target (all `succeeded`, `passed`, `review_required: false`) | Name slot = source first name: < 3 letters / 3+ | City slot = source city: < 4 / 4+ | Name slot = the target row's own first name |
|---|---|---|---|
| Oracle `CUSTOMERS_SYN_ENG3` (surrogate) | **0 / 28** · 0 / 19 972 | **0 / 34** · 0 / 19 966 | 28 / 28 · 19 972 / 19 972 |
| Oracle `CUSTOMERS_COP_ENG3` (copula) | **0 / 28** · 0 / 19 972 | **0 / 34** · 0 / 19 966 | 28 / 28 · 19 972 / 19 972 |
| Oracle `CUSTOMERS_ENG3` (obfuscate) | **0 / 28** · 0 / 19 972 | 34 · 19 912 (the policy keeps the `CITY` column) | — |
| MongoDB `profiles_syn_eng3` | **0 / 28** · 0 / 19 972 | 34 · 19 912 (no city field: N1, documented) | 28 / 28 · 19 972 / 19 972 |
| MongoDB `profiles_eng3` (obfuscate) | **0 / 28** · 0 / 19 972 | 34 · 19 912 (N1) | — |
| Files: Oracle obf/syn × parquet/csv; MongoDB obf/syn × jsonl/csv/parquet (10) | **0 / 28** · 0 / 19 972 in each | Oracle synthesize 0 / 34 · 0 / 19 966; the others as their tables | — |
| Control: source vs itself | 28 / 28 · 19 972 / 19 972 | 34 / 34 · 19 966 / 19 966 | — |

More checks:

- **Whole-note search** (`p5b`, `p5c`), every name length, case-sensitive below 3 letters. Own e-mail,
  phone digits, full name, first name, city, national id and tax id in the synthetic notes: **0** on
  Oracle surrogate, Oracle copula, PostgreSQL and MongoDB, 20 000 rows each. The control finds them,
  including the 28 short first names and the 34 short cities.
- **Obfuscate notes** (`p4`): first names 0 / 20 000 (short 0 / 28) on Oracle and MongoDB.
- **Every note template survives.** Name slot present 20 000 / 20 000 and city slot present
  20 000 / 20 000 in every target, so no 2-letter needle broke a note. 54 obfuscated Oracle notes
  have part of the kept city masked. That is the same 19 946 as round 2 and errs towards safety.
- **Offline at HEAD** (`repro_short_names.py`, `repro_short_values.py`):
  - `ROW_VALUE_MIN` = 2 and `NAME_PART_MIN` is gone.
  - Short first names kept: 0 / 28 in both modes.
  - Short cities kept: 0 / 34 in synthesize.
  - Strict verification passes.

### r2 B2 — key-less Oracle `table` load into a new table on a separate login → **fixed**

- **Isolated** (`p9_ledger.py` → `ledger_eng3.json`):

  | Run (`CRM.ADDRESS_HISTORY` → `CRM_MASKED`, 40 000 rows) | Target existed | `write_ledger_cleared` | Ledger rows of the job |
  |---|---|---|---|
  | obfuscate into `ADDR_ENG3` (control) | yes | `true` | 0 |
  | obfuscate into `ADDR_LEDGER_ENG3_004025` | no | **`true`** | **0** |
  | synthesize into `ADDR_SYN_LEDGER_ENG3_004025` | no | **`true`** | **0** |
  | copula into `ADDR_COP_LEDGER_ENG3_004025` | no | **`true`** | **0** |

  - `CRM_MASKED.DPS_WRITE_LEDGER` is present and holds 0 rows in total.
  - The target login still sees 0 columns of the source, so the old fallback really was out of reach.
- **Live group runs** say `write_ledger_cleared: true` for every key-less load into a new table:
  - `ADDR_ENG3` (p2);
  - `ADDR_SYN_ENG3` and `ADDR_COP_ENG3` (p3).
- **Under kill:** see §5. The cross-login cases (obfuscate, surrogate, copula) and the DoD
  `oracle-keyless-table-cross` all report `cleared: true` / `true` with 0 ledger rows.

## Blocking finding

### B1. The D-A identifier check reads one 5 000-row page on Oracle and MongoDB but reports `whole_table`

**Where.**

- `dps/layers/connectors/identifier_check.py` clears every page it consumes:
  - `key_floor`, `:218`;
  - `_tally_target`, `:240`;
  - the source loop of `check_identifiers`, `:290`.

  Each does `rows.clear()`. `_stream` passes the connector's own list through.
- The Oracle and MongoDB `read_partition` test that same list after the consumer resumes them:
  - `dps/layers/connectors/oracle.py:814-818`: `yield rows`, then `if len(rows) < size: return`
    (and `after = rows[-1]…`);
  - `dps/layers/connectors/mongo.py:650-655`: `out` is `docs` when nothing is sampled. After
    `yield out` comes `if len(docs) < batch: return`.
- The cleared page looks short, so paging stops after `CHECK_BATCH` = 5 000 rows.
- PostgreSQL yields a fresh list (`postgres.py:673`, `yield [dict(r) for r in rows]`) and is
  unaffected.
- The unit test `tests/test_identifier_check.py::test_check_identifiers_reads_every_row_of_both_sides`
  uses a fake connector that yields fresh lists, so it cannot see this.

**Contract it breaks.**

- The module docstring says the check runs over every row of the target (not a sample).
- HANDOFF says the run reads every written government or financial identifier value. Any count above 0
  makes the pack `review_required`.
- On two of the three engines, the D-A backstop covers 5 000 rows of each side. On a table of N rows,
  a real identifier is caught only if both its target row and its matching source row fall in the
  first page: about (5 000 / N)². That is roughly 6 % at the demo's 20 000 rows, and close to 0 at
  production sizes.
- An `append` also gets a wrong `key_floor`, because the largest key is taken from the first page
  only (`key_floor_equal_as_built_vs_copied: false` on Oracle `CRM.CUSTOMERS`).

**Evidence.**

1. **Live packs (this round, `syn_eng3.json`):** `identifier_check` with `scope: whole_table` on
   20 000-row synthesize targets.

   | Target | Column: checked / non-null values written |
   |---|---|
   | Oracle `CUSTOMERS_SYN_ENG3` | `NATIONAL_ID` **5 000** / 20 000; `TAX_ID` 2 994 / 11 989 |
   | Oracle `CUSTOMERS_COP_ENG3` | same |
   | Oracle `KYC_SYN_ENG3` | `DOC_NUMBER` **5 000** / 20 000 |
   | MongoDB `profiles_syn_eng3` | `$.accounts[*].iban` **5 000** / 20 000; `$.kyc.taxId` 3 002 / 11 989 |
   | PostgreSQL `customers_syn_eng3` / `_cop_eng3` (control) | `national_id` **20 000** / 20 000; `tax_id` 11 989 / 11 989 |
   | PostgreSQL `accounts_syn_eng3` (control) | `account_number` 30 000 / 30 000 |

   Round 2's packs (`S1-M3-engines-r2/syn_eng2.json`) show the same 5 000s, so this was not
   introduced by `d2fdc3f`.
2. **Read-only repro on the demo sources** (`repro_idcheck_paging.py` → `.json`). The check is run
   with the source as its own target, so every value should be `in_source`.

   | Object (20 000 rows) | `read_partition` pages / rows: page kept / page cleared as the check does | As built: checked / in_source | Pages copied: checked / in_source | Expected (distinct) |
   |---|---|---|---|---|
   | Oracle `CRM.CUSTOMERS` `NATIONAL_ID` | 4 / 20 000 · **1 / 5 000** | 5 000 / 5 000 | 20 000 / 19 998 | 19 998 |
   | Oracle `CRM.CUSTOMERS` `TAX_ID` | 4 / 20 000 · **1 / 5 000** | 3 019 / 3 019 | 11 989 / 11 989 | 11 989 |
   | Oracle `CRM.KYC_DOCUMENTS` `DOC_NUMBER` | 4 / 20 000 · **1 / 5 000** | 5 000 / 5 000 | 20 000 / 19 998 | 19 998 |
   | MongoDB `crm.profiles` `$.kyc.taxId` / `$.accounts[*].iban` | 4 / 20 000 · **1 / 5 000** | 3 019 / 3 019 · 5 000 / 5 000 | 11 989 / 11 989 · 20 000 / 20 000 | 11 989 · 20 000 |

3. **A missed real identifier, on scratch data** (`repro_idcheck_missed_hit.py` → `.json`).
   - Setup: a probe-owned Oracle user and MongoDB database, both dropped afterwards (confirmed). Each
     holds a target and a source of 20 000 generated `ACCOUNT_NUMBER` strings.
   - Exactly one shared value, at target key 15 000 and source key 18 000.

   | Engine | As built | Pages not aliased (copying wrapper) |
   |---|---|---|
   | Oracle | `scope: whole_table`, checked 5 000, **in_source 0, review_columns []** | checked 20 000, **in_source 1, review_columns [ACCOUNT_NUMBER]** |
   | MongoDB | `scope: whole_table`, checked 5 000, **in_source 0, review_columns []** | checked 20 000, **in_source 1, review_columns [acct]** |

   As built, a synthesize run whose target holds a real account number, IBAN or government id
   beyond row 5 000 would be sealed `passed` with `review_required: false`.
4. **Why it matters now.** The gates verifier (r3 B1) found that the demo's national-id surrogates
   collide with demo ids in about 1.4 % of runs. On PostgreSQL this check catches that collision.
   On Oracle and MongoDB it would miss roughly 94 % of such collisions at demo size.
   - This round's full-table sweeps (§3) found 0 real identifiers in every synthetic target. So the
     outputs written here are clean. The product's own proof of that is what is incomplete.

**Fix.**

- Make paging independent of what the consumer does with a page:
  - In `oracle.py` and `mongo.py` `read_partition`, take the page length and the next `after` key
    **before** `yield`, or yield a copy as PostgreSQL does.
  - In `identifier_check.py`, stop clearing pages the connector owns: delete each page's name binding,
    or iterate over a copy.
- **Tests:**
  1. A conformance rule for all three engines: `read_partition` with a consumer that clears each page
     still returns every row (for example batch 50 over 200 rows).
  2. `check_identifiers` on Oracle and on MongoDB with more rows than one page (or `CHECK_BATCH`
     patched small), with one shared value past the first page → `in_source` 1, review.
  3. `key_floor` returns the true largest key on a multi-page Oracle table.
  4. Make the fake in `tests/test_identifier_check.py` alias its pages as Oracle does.
- **Re-check live:** a 20 000-row Oracle and MongoDB synthesize must show `checked` equal to the
  non-null count of each column.

## What holds (evidence)

### 1. Obfuscate on all three engines → `table` targets, per column (`p2`, `p4` → `compare_obf_eng3.json`)

**Setup.**

- One group: `grp_01M4XWSWG4ZJGKT4BA1V75MV7R` (kid `k_9707db54bcce9390`).
- Default policy. All 9 objects were discovered and accepted at 00:31 (`p1`).
- All 9 jobs `succeeded`, verification `passed`, `review_required: false`, label "pseudonymised — still
  personal data", `kept_column_hits` 0.

| Source → target | Rows src / tgt | Aligned on | Row-aligned survivors (sensitive columns) |
|---|---|---|---|
| PG `bank.customers` → `bank_uat.customers_eng3` | 20 000 / 20 000 | 8 kept columns (20 000) | **0** (customer_id, full_name, date_of_birth, national_id, tax_id, email, mobile, notes) |
| PG `bank.accounts` → `accounts_eng3` | 30 000 / 30 000 | account_id | **0** (customer_id, account_number, iban, ifsc) |
| Oracle `CRM.CUSTOMERS` → `CRM_MASKED.CUSTOMERS_ENG3` | 20 000 / 20 000 | LAST_CONTACT_AT (20 000) | **0** (10 columns incl. NOTES, PIN_CODE) |
| Oracle `CRM.ADDRESS_HISTORY` (key-less) → `ADDR_ENG3` (new table, cross-login) | 40 000 / 40 000 | CITY+VALID_FROM (38 993) | **0**; 5 columns, no ROWID; `write_ledger_cleared: true` |
| Oracle `CRM.KYC_DOCUMENTS` (CHAR(12) key) → `KYC_ENG3` | 20 000 / 20 000 | DOC_REF | **0** |
| Oracle `CRM.INTERACTIONS` (TS(9) composite key, JSON CLOB) → `INTER_ENG3` | 40 000 / 40 000 | EVENT_TS+CHANNEL | **0** (CUSTOMER_ID, AGENT_EMAIL, PAYLOAD) |
| Oracle `LOANS.APPLICANTS` (string key) → `APPL_ENG3` | 5 000 / 5 000 | APPLICATION_ID | **0** |
| Oracle `CRM.CUSTOMER_EVENTS` (identity key) → `EVENTS_ENG3` | 40 000 / 40 000 | EVENT_ID | **0** |
| MongoDB `crm.profiles` → `crm_uat.profiles_eng3` | 20 000 / 20 000 | `_id` | **0** on all 10 sensitive leaves |

- **Set hits are all explained.** `fpe_digits` ids that land on another real id: 0–8 per table, never
  the row's own. `date_shift` DOBs that land on another date in the domain.
- **Identifier sweep.** Hits only in kept non-personal keys (`_id`, `account_id`, `DOC_REF`,
  `APPLICATION_ID`, `relationship_manager_id`) and in those FPE collisions.

### 2. Three-way consistency check (PG ↔ MongoDB ↔ Oracle), overlap > 0

**Members.**

- The anchor is Oracle `$.CUSTOMER_ID` (numeric).
- MongoDB `$.customerId` is `prefixed_digits` (width 8, prefix 5).
- PG `$.customer_id` is numeric.
- All three are `fpe_digits` with `params_hash` `44136fa355b3`.

| Check | HTTP | Result | Pairs consistent | Matched / checked per pair |
|---|---|---|---|---|
| derived, sample 1 000 (0.2 s) | 200 | passed | **3 / 3** | 1 000 / 1 000 (coverage 1.0) |
| derived, sample 10 000 (1.0 s) | 200 | passed | **3 / 3** | 10 000 / 10 000 (coverage 1.0) |
| explicit pair CUSTOMERS ↔ ADDRESS_HISTORY | 200 | passed | 1 / 1 | 1 000 / 1 000 |

- **Attributes:**
  - EMAIL, PERSON_NAME, PHONE and NATIONAL_ID agree 10 000 / 10 000.
  - STREET_ADDRESS agrees 10 000 / 10 000 and PAN_IN 6 052 / 6 052.
  - POSTAL_CODE agrees but does not count toward the pass.
  - DATE_OF_BIRTH `date_shift` agrees 17 / 10 000. This is informational and known: it depends on the
    anchor.
- **Group:** `last_check` passed. `methods_agree` true, 0 problems.
- **Independent containment:**
  - Obfuscated customer ids are identical on PG, MongoDB and Oracle (20 000 = 20 000 = 20 000).
  - The child-table ids of ADDRESS_HISTORY, KYC, INTERACTIONS, APPLICANTS and CUSTOMER_EVENTS are all
    contained in `CUSTOMERS_ENG3`: 20 000 / 20 000 (APPLICANTS 5 000 / 5 000).

### 3. Synthesize and D-A (`p3` → `syn_eng3.json`, `p5` → `compare_syn_eng3.json`, `p5b`, `p5c`, `p5d`)

**Setup.**

- One group: `grp_01M4XWSWQGDKPWDVTB0TA3PAB0`. Alignment was 100 % on every target, through the keyed
  pseudonym recomputed in memory.
- All 11 accepted jobs `succeeded`, `passed`, `review_required: false`, label "synthetic — measured,
  not claimed anonymous":
  - PG: customers surrogate and copula, accounts.
  - Oracle: CUSTOMERS surrogate and copula, ADDRESS_HISTORY surrogate and copula (both into new tables,
    `write_ledger_cleared: true`), KYC, APPLICANTS, CUSTOMER_EVENTS.
  - MongoDB: profiles.
- Copula privacy: `exact_row_matches` 0, NNDR passed, MIA AUC 0.497–0.503.

**Survivors.** Survivors are judged by each pack's **run-time** methods, not the snapshot's
`synthesize_method`. The snapshot says `keep` for CITY, PIN_CODE and the keys, which D-A replaces at run
time.

- `row_same` = 0 on every `surrogate` and `key_pseudonym` column of every target.
- No personal column was kept at run time.
- Kept non-personal columns are equal (surrogate mode) or redrawn (copula).
- Key-less ADDRESS_HISTORY, customer-group aligned: CUSTOMER_ID, ADDR_LINE1, CITY and PIN_CODE match 0
  (surrogate and copula).

**Keys (D-A).**

- **Integer pseudonyms:** 100 % in [10⁹, 2·10⁹) and **0 in the source** for:
  - `customer_id` / `CUSTOMER_ID` in 10 tables;
  - `account_id` (30 000 unique);
  - `EVENT_ID` (40 000 unique);
  - `relationship_manager_id` (0 real employee ids).
- **MongoDB `_id`:** 20 000 / 20 000 are ObjectIds, unique, **0 in the source**.
- **Other keys:** `customerId` 0 in the source; `APPLICATION_ID` 0.
- **ROWID:** in no Oracle target (tables, files, manifests).

**Identifier sweep** (13 classes, whole cells and tokens in text, all rows):

- 0 real customer ids, ObjectIds, national ids, tax ids / PANs, IBANs, account numbers, application ids
  or e-mails in any synthetic target.
- Remaining hits are explained (N5):
  - surrogate phones in the fictional range (2 253 / 20 000);
  - 6-digit PIN codes against NINO digit forms;
  - DOC_REF short-key collisions (N2).

**Joins under the group seed:**

- PG and Oracle customer pseudonyms are identical: 20 000 / 20 000.
- KYC, APPLICANTS, ADDR (surrogate and copula) and EVENTS are contained in `CUSTOMERS_SYN_ENG3`.

**Refused by design** (codes, nothing written; `p3`, `p6` → `inplace_eng3.json`):

| Run | Result |
|---|---|
| MongoDB copula | 422 `tabular_invalid` |
| Oracle `INTERACTIONS` synthesize | 422 `synthesize_pseudonym_unsupported` (N4) |
| `in_place` synthesize on a sensitive key | 422 `synthesize_in_place_sensitive_key` |
| `in_place` obfuscate without `confirm_write` | 422 `confirm_write_required` (Oracle and MongoDB) |

The probe-owned copies were unchanged, and the temporary connections were deleted (204).

### 4. `files` targets (`p7` → `files_eng3.json`, `p8` → `files8_eng3.json`)

**Runs.** All 24 runs `succeeded`, with no partition error and verification `passed`.

- **`p7` (14 runs):** 1 attempt per partition, 0 null pack values.
  - MongoDB: obfuscate and synthesize × jsonl, csv and parquet.
  - Oracle CUSTOMERS (TIMESTAMP(9)): obfuscate and synthesize × parquet and csv.
  - Oracle key-less ADDRESS_HISTORY: obfuscate and synthesize × parquet and jsonl.
- **`p8` (10 parquet runs):**
  - PG customers copula, accounts copula and obfuscate;
  - Oracle CUSTOMERS copula;
  - APPLICANTS obfuscate, synthesize and copula;
  - CUSTOMER_EVENTS and KYC synthesize;
  - INTERACTIONS obfuscate.

**Results.**

- Row-aligned survivors are 0 on every sensitive column or leaf. First names in notes: 0 at every length.
- Synthetic files:
  - `_id` 0 in the source (24-hex, unique);
  - `customerId` 0;
  - `CUSTOMER_ID` 100 % in band and 0 in the source;
  - `EVENT_ID`, `APPLICATION_ID` and `account_id` 0.
- `LAST_CONTACT_AT` is FF9 text that equals the source exactly in 20 000 / 20 000 rows.
- No ROWID in any file or manifest.
- Synthesize packs into files record `identifier_check: {"not_run": "files_target"}`, as documented
  (HANDOFF). D-A for files rests on this sweep: 0 real identifiers.

### 5. Resume after kill on Oracle and MongoDB

**DoD test on main.** `tests/e2e/test_m3_resume_after_kill.py`: **5 passed** in 118 s
(`dod_resume.log`).

- oracle / mongo × in_place / append;
- the new `oracle-keyless-table-cross`.

**Probe.** `kill/test_probe_m3_kill.py` (`kill/kill_results.jsonl`) ran **18 passed** in three parallel
sessions: MongoDB 6 in 364 s, Oracle A 6 in 304 s, Oracle B 6 in 129 s.

- Each case: 3 000 rows; the first worker is SIGKILLed after at least 5 committed batches; a second worker
  finishes the run.
- Each session has its own Oracle owner (plus a second owner, with no grant on the source, for the
  cross-login cases), its own MongoDB database and its own metadata database.
- Every case: the killed run `succeeded`, digest = reference, partitions resumed 3 / 3 (evidence 3),
  0 ledger rows, 0 survivors, verification passed, no ROWID written, 0 values in the worker logs.
- Key-less cases also checked ADDR_LINE1 = 0 in the source. Synthesize cases also checked CUSTOMER_ID
  0 in the source and 100 % in band.

| Case | Ledger table / `write_ledger_cleared` (kill, ref) |
|---|---|
| Oracle table obfuscate; files jsonl, parquet obfuscate; table synthesize; **table copula (new)** | not ledgered (keyed) |
| Oracle key-less table obfuscate / synthesize (same login) | present / `true`, `true` |
| Oracle key-less files parquet synthesize | not ledgered (files) |
| **Oracle key-less → created table on a separate login: obfuscate / synthesize / copula (new, r2 B2)** | present / `true`, `true` |
| MongoDB table obfuscate / synthesize; files jsonl, parquet obfuscate; files csv, **parquet (new)** synthesize | not ledgered |
| `test_keyless_oracle_synthesize[as-built]` (in process) | `succeeded`, 3 000 rows, 5 table columns, CUSTOMER_ID 0 in the source, ADDR_LINE1 0 in the source |

Synthetic full names that equal some seeded name occur 0–53 times per case. They come from the same
surrogate pool and none is row-aligned (§3 finds 0 aligned names).

### 6. Logs, metrics, residue

- **Container logs** (`logscan.py` → `logscan.json`; app, worker, tokenizer-mock and azure-ocr-mock
  since 19:00 UTC):
  - **0 hits** of 159 732 source values, **0** whole-word hits of 865 first names, 0 of the short first
    names and cities;
  - 0 tracebacks, 0 error-level and 0 warning-level lines;
  - the planted controls are found;
  - the worker log covers this round's 48 runs and 22 discovers (event names only).
- **Metrics:** `dps_llm_raw_pii_sent_total 0.0`.
- **Residue** (`residue.after.json`):
  - 0 `DPS_T_*` Oracle users, 0 `dps_t_*` MongoDB databases, 0 `dps_test_*` / `dps_fresh_*` databases,
    0 `t_*` source schemas;
  - the only ledger table is `CRM_MASKED.DPS_WRITE_LEDGER`, with 0 rows.

## Non-blocking notes

- **N1 (now documented).** MongoDB `crm.profiles` has no city field, so its notes keep the source city
  in both modes: 19 946 / 20 000 here, tables and files. HANDOFF now names this as a NER limitation.
  The synthesize pack still says `passed` with no coverage caveat; r1 and r2 suggested adding one.
- **N2 (carried).** `DOC_REF` text pseudonyms equal another document's real ref 370 / 20 000 times
  (table) and 389 times (parquet), never the row's own. The source refs are 2–6 characters long, so
  the key space is small. The identifier check does not cover keys.
- **N3 (carried).** Oracle synthesize keeps the unique TIMESTAMP(9) `LAST_CONTACT_AT`, surrogate and
  copula alike: 20 000 / 20 000. It is a row locator. `VALID_FROM` (surrogate) and MongoDB `balance` /
  `verifiedAt` / `riskScore` are likewise kept.
- **N4 (carried).** `CRM.INTERACTIONS` (a composite key with a TIMESTAMP part) refuses synthesize with
  422 `synthesize_pseudonym_unsupported`.
- **N5 (carried).** These set hits are explained:
  - FPE ids on other real ids;
  - date-domain DOBs;
  - phones in the fictional range;
  - NINO digit forms against PIN codes;
  - surrogate addresses that equal another row's address (6);
  - a copula `balance` digit form against a national-id digit form (2).
- **N6 (carried, slightly better).** A consistency check on a synthesize group returns 200
  `passed: false`, 36 / 55 pairs (`consistency_syn_group.json`).
  - It now names the cause: problem `join_form_mismatch` on the MongoDB member.
  - Members are still labelled `fpe_digits`, although the run used `key_pseudonym`.
- **N7.** HANDOFF says ledger rows of runs before the fix "stay in `CRM_MASKED.DPS_WRITE_LEDGER` until
  deleted by job id". At the start of this round that table did not exist; gates r3 saw the same. The
  sentence is stale (cosmetic).
- **N8.** `ADDR_ENG3` was overwritten by `p9`'s control run (another group key) after `p4` had compared
  it. The other `*_ENG3` targets hold the p2 / p3 outputs.

## Commands

```bash
cd /Users/neelu/document_intelligence/data-privacy-services
source ~/.cache/dps-build/reports/S1-M3-engines-r3/env.sh   # contract §12.2 + PYTHONDONTWRITEBYTECODE
DPS_TEST_REQUIRE_SERVICES=1 .venv/bin/python -m pytest -p no:cacheprovider -q -rA tests/e2e/test_m3_resume_after_kill.py
DPS_TEST_REQUIRE_SERVICES=1 .venv/bin/python -m pytest -c pyproject.toml --rootdir . -p tests.conftest -p no:cacheprovider -q -rA \
  "~/.cache/dps-build/reports/S1-M3-engines-r3/kill/test_probe_m3_kill.py::test_kill_resume[<case>]"   # 3 sessions, see kill/*.log
cd ~/.cache/dps-build/reports/S1-M3-engines-r3
python p1_setup.py; python p2_obfuscate.py eng3 & python p3_synthesize.py eng3; python p4_compare_obf.py eng3; python p9_ledger.py eng3
python p5_compare_syn.py eng3; python p5b_text_aligned.py eng3; python p5c_firstname.py eng3; python p5d_short_values.py eng3
python p7_files.py eng3; python p7b_files_slots.py eng3; python p8_files_extra.py eng3; python p6_inplace.py eng3
python repro_short_names.py; python repro_short_values.py; python repro_idcheck_paging.py; python repro_idcheck_missed_hit.py
python logscan.py 2026-10-14T19:00:00Z; python selfscan.py; python src_state.py
```

## State left behind

- **Targets on the shared stack**, named `*_eng3` / `*_ENG3`:
  - `bank_uat`: 5 tables;
  - `crm_uat`: 2 collections;
  - `CRM_MASKED`: 13 tables plus 3 `*_LEDGER_ENG3_004025` tables (p9), and `DPS_WRITE_LEDGER` (0 rows);
  - MinIO `dps-exports/eng-r3/eng3/…`: 24 file runs.
- **Job groups** `eng-r3-*`. The synthesize group's `last_check` is failed (N6 probe).
- **Removed:** the scratch Oracle user and MongoDB database of `repro_idcheck_missed_hit.py` (dropped,
  confirmed).
- **Not changed:** the repository (clean at `d2fdc3f`) and the demo sources. The stack is still
  running.
