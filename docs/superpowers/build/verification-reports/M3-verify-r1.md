# M3 lane verification, round 1

- Lane: `dps-wt-m3`, branch `m3`, HEAD `392c1fe`.
- Date: 2026-10-14.
- Verifier: independent. No code was changed and nothing was committed.
- Probes and logs: `~/.cache/dps-build/reports/m3-verify-r1/`.

## Verdict: FAIL (2 blocking)

Every gate, tool, browser check and source-vs-target comparison is green. However, two of the five carried
group findings are **still open** at HEAD, and one of them is a reproduced end-to-end leak:

1. **B6, open, leak reproduced end to end.**
   - `selection.py` accepts PIVOT/UNPIVOT, MATCH_RECOGNIZE and PostgreSQL table-alias column lists.
   - An Oracle UNPIVOT run on the lane put `CRM.CUSTOMERS.EMAIL` under the kept column `CITY`.
   - The job **succeeded**. **20000/20000 target CITY values equal a source e-mail**, and the evidence says
     `verification.passed = true`.
2. **B3-r3, open.** `consistency.target_key` still keys PostgreSQL objects on `obj.fqn()` and does not
   normalise the owner to `public`. It still case-folds quoted PostgreSQL names.

The other three carried findings are fixed and verified:

- nested drift under a field sampled as a scalar;
- bracketed field names;
- pymongo DEBUG logging.

Because the verdict is not a pass:

- **I did not commit the screenshots.** They are untracked in `docs/screenshots/m3/`, 92 PNGs, 74 MB.
- **I did not run `down -v`.** I ran `docker compose down` and kept the volumes, so a fixer can rerun on the
  seeded data.

## Carried findings, re-checked at HEAD 392c1fe

### B6: selection alias / re-mapping. OPEN (blocker)

**Code at HEAD.**
- `_query_sources` and `_check_query_outputs` (`dps/layers/connectors/selection.py:651-735`) are unchanged.
- No `exp.Pivot`, `exp.MatchRecognize` or column-listed `TableAlias` check exists.
- `ALIAS_REFUSED` (`tests/connectors/test_selection.py`) has none of these shapes.
- Contract §5.2 still says "a plain table with the object's name".

**Offline check (`probes/b6_sel.py`): `validate_selection` with the shape ACCEPTS 7/7.**

| Engine | Accepted shapes |
|---|---|
| PostgreSQL (3) | `FROM crm.contacts AS c(contact_id, channel, email)` with `c.contact_id, c.channel`; the same with `*`; the same unqualified |
| Oracle (4) | `UNPIVOT (channel FOR src IN (email, channel)) WHERE src = :s`; `SELECT *` with UNPIVOT; `MATCH_RECOGNIZE … MEASURES email AS channel`; `PIVOT (… 'x' AS channel)` |

**End to end on the lane stack (`probes/b6_e2e.py`, output `probes/b6_e2e_r2.out`):**

- **Oracle.** I discovered and accepted `CRM.CUSTOMERS` with defaults: CITY → `keep`, EMAIL → `email`.
  - Query: `SELECT customer_id, …, city FROM crm.customers UNPIVOT (city FOR src IN (email, city)) WHERE
    src = :s`, with `s = 'EMAIL'`.
  - Run: table mode into `CRM_MASKED.PROBE_B6_*`.
  - The submit returned 202, and the job **succeeded** with 20000 rows.
  - **20000/20000 target CITY values are equal to a source EMAIL.**
  - Evidence `verification.passed = true`.
- **PostgreSQL** (`bank.addresses AS a(… city, line2, line1 …)`, which swaps line1 and city): the submit
  returned 202.
  - The run wrote 4000 rows. **4000/4000 target `city` values are equal to a source `line1`**.
  - The remaining partitions then retried on `target_error`, because some line1 values are longer than
    city's 80 characters.
  - On this table both columns are `keep` by default (see N-new-1), so here this is a re-mapping, not a
    method bypass.
- My first attempts failed only because of column widths:
  - On PG, `bank.contacts.channel` is varchar(12) and `target_error` retried.
  - On Oracle, `SEGMENT` is VARCHAR2(16) and the run failed with `length_overflow`.
  - Once I used a wide kept column, the leak goes through.
- I dropped all probe targets afterwards.

**Fix (unchanged from the carried finding):**
- Refuse with `selection_query_alias` any statement containing `exp.Pivot` or `exp.MatchRecognize`, or an
  object source whose `TableAlias` has a column list. Do this on both engines and test-first.
- Add these shapes to `ALIAS_REFUSED`.
- Add one E2E test per engine: 422 at submit, no target created, and discovery fails `selection_query_alias`.
- Amend contract §5.2.

### B3-r3: consistency `target_key` owner normalisation. OPEN (blocker)

- `dps/layers/connectors/consistency.py:436-440` still reads `name = obj.fqn().casefold() if fold else
  obj.fqn()`.
- Offline probe (`probes/b3_key.py`):
  - `target_key(pg, ObjectRef(name="probe_r3_cust"))` and the same table with `owner="public"` give
    **different keys**, so the result is False.
  - Quoted `public."Cust"` and `public."cust"` give the **same** key, so distinct PostgreSQL tables
    collapse.
- `derive_members` (line 349) and `_validate` (line 474, `member_duplicate`) both rely on this key.
- Effect: two refreshes of one PostgreSQL table, one spelled with an owner and one without, still join
  themselves and are reported as a cross-system pass. This is the scenario the round-3 reviewer reproduced
  through the API. I did not re-run the API reproduction, because the code path is byte-identical to the
  one reviewed.
- Fix (as carried):
  - Normalise the owner per engine (`owner or 'public'` for PostgreSQL, reusing `targets._owner`).
  - Case-fold for Oracle only.
  - Add unit tests for explicit pairs (422 `member_duplicate`) and for the derived check (collapse, then
    409).
  - Fix the docstring and the contract wording.

### Nested drift under a field sampled as a scalar (`batch.py _unclassified`): FIXED

- `_unclassified` now skips a flat name only for SQL rows, or for document rows when the name is planned.
  It flattens leaves and skips only paths that are in `by_path`.
- Test `test_a_scalar_sampled_field_holding_documents_is_searched` exists.
- Probe `probes/drift.py`, sentinel survivors:

| Case | Survivors |
|---|---|
| unplanned `notes`, `detect` | 0 |
| unplanned `notes`, `redact` | 0 |
| `notes` planned `det_token`, every mode | 0 |

- Residual (non-blocking, N-new-3): when the reviewer explicitly plans a **text** column `keep`
  (`text_column=True`, method keep), drift sub-documents under it are kept (3/3 sentinels).
  - That is the reviewer's explicit choice for that field.
  - The pipeline does not plan non-text `keep` columns (`targets.py:439`), so the default discovery path
    is not affected.

### Bracketed field names (`docpaths`): FIXED

- Probe `probes/brackets.py` covers `iban[1]`, `a[0]x`, nested `acct['no[2]']`, the adversarial key
  `acct['list[*]']` and `arr[*]['k[3]']`.
- `infer_shape` gives segment-aware paths.
- Result: **0/100 survivors** under both `detect` and `keep`, and `values_by_column` = 20 for each path.

### B4-r3: pymongo DEBUG logging: FIXED

- `_THIRD_PARTY_LEVELS` has `("pymongo", WARNING)`, and `test_third_party_loggers_are_capped` asserts it.
- Live probe (`probes/b4_debug.py`): `configure_logging("DEBUG")` plus a root capture handler, then a find
  of 200 profiles, `insert_many` and `bulk_write` against the lane's Mongo.
  - **0 pymongo records** and **0/600 values** in the captured logs.
- Sanity control: a DPS DEBUG record is captured, a pymongo DEBUG record is suppressed and a pymongo
  WARNING is captured.
- `test_no_values_in_logs_m2.py` passed in my full run, which was a single run.

## Gates (DoD §1 to §10)

| Gate | Result |
|---|---|
| `./lane.sh ruff format --check dps tests tools` | 303 files already formatted |
| `./lane.sh ruff check dps tests tools` | All checks passed |
| `./lane.sh mypy dps` | Success: no issues in 109 source files |
| `DPS_TEST_REQUIRE_SERVICES=1 ./lane.sh pytest -q -rs` (stack `--profile oracle --profile sources`, built from HEAD) | **2088 passed, 5 skipped, 3 errors**, 510 s. The 3 errors are OCR-mock fixtures under REQUIRE=1 (the docs profile was not started). I then started `azure-ocr-mock`, ran those three files and got **19 passed**. Skips: Mongo has no composite keys (capability) ×1; the tesseract binary ×2; `presidio_analyzer` / spaCy for the `[ner]` extra ×2 (heavy model extra, not installed in this lane) |
| DoD 4, `-m "oracle or mongo"` (162 tests) | contained in the full REQUIRE=1 run, all passed. It includes `test_oracle_clob_astral_roundtrip`, `test_oracle_char_key_paging` and `test_oracle_timestamp9_key_paging` |
| DoD 5, conformance on PostgreSQL, Oracle and Mongo | **29 passed, 1 skipped** (Mongo has no composite keys) |
| DoD 6, `-m "db or pg_source or s3"` (196 tests) | contained in the full run, all passed |
| Frontend: `npm ci`, `npm run typecheck`, `npm test`, `npm run build` | ci ok; typecheck ok; node tests 42/42 and vitest 91/91 (4 files); build ok (445 ms) |

## Stack, seed, smoke, bench (DoD §2, §3, §7, §9)

- `docker compose --profile oracle --profile sources up --build -d`:
  - All services were healthy.
  - `migrate`, `minio-init`, `seed-sources` and `demo-secrets` exited 0.
  - `/readyz` returned `ready: true` with db up (migrations verified) and jobs ready.
  - It reports ocr down and ner patterns-only, which is expected without the docs profile or NER.
- Seed `--target all --rows 20000 --seed 7`:
  - First run: PostgreSQL bank tables, `CRM.*` 20000 / 40000 rows, `LOANS.APPLICANTS` 5000,
    `crm.profiles` 20000.
  - Second run: everything `unchanged`.
  - I reseeded after pytest, which reduces the demo space to 200 or 2400 rows (G6-N9).
- `tools/smoke_test.py`: **all 32 checks passed, 2 skipped** (OCR).
- `tools/smoke_test.py --m3`: **all 39 passed, 2 skipped** (OCR):
  - connections 6/6 tested;
  - 3/3 snapshots;
  - runs 20000/20000/20000;
  - **m3 consistency 3/3**, 500 sampled, min coverage 1;
  - 3/3 packs verified under the group's kid;
  - `dps_llm_raw_pii_sent_total 0`.
- Benchmarks (`--rows 20000 --parallelism 4 --partition-rows 5000`, 1 worker):
  - Oracle 826.0 rows/s, 24.2 s;
  - Mongo 763.4 rows/s, 26.2 s.
  - Both are about 10% below the HANDOFF figures (942 / 860), which is within run noise with other stacks
    up.
  - The bench JSON outputs were written into `docs/technical/bench/`. I moved them to the report folder, so
    the tree is unchanged.

## Browser walk (private Playwright 1.55 at `pw-m3`, `probes/walk.py`)

All of it ran in the console against `http://127.0.0.1:8413`.

1. **New job group dialog.** I created group `m3-verify-6960bf` (`grp_01M4WFM100DDA1T7CAG82Z7MB1`).
   - The `.dpskey` was downloaded once into my scratch folder. I deleted it after the scans.
   - The stored checkbox and Done worked.
2. **Three wizard runs**, each going through Test connection ("n of n checks passed"), Select (owner and
   table, or collection), Check (rows or documents match), Run discovery (heat-map), Accept snapshot, Copy
   to a table, then step 5 "Reuse the key of a job group" with the key file loaded (**matched**), Ticket,
   Run job, Open job, and finally the Evidence pack with sections 1 to 5.

   | Engine | Source | Target | Result |
   |---|---|---|---|
   | PostgreSQL | `bank.customers` | `bank_uat.customers_v6960bf` | succeeded, 20000 rows |
   | MongoDB | `crm.profiles` | `crm_uat.profiles_v6960bf` | succeeded, 20000 documents |
   | Oracle | `CRM.CUSTOMERS` | `CRM_MASKED.CUSTOMERS_V6960BF` | succeeded, 20000 rows |

3. **Jobs page (`?group=`).**
   - 3 system cards, key node, then **Run consistency check**.
   - The verdict chip reads `passed`, **"3 / 3 consistent"**, with 3 pairs and 0 bad pairs.
   - Coverage per pair is 500/500.
   - The verdict survives a reload. The jobs table shows 3 `.grp` badges.
4. **Oracle job detail.**
   - Group crumb present, and the subtitle names the other two targets.
   - The "Cross-system joins" tile reads **3 / 3 consistent**.
   - "Open consistency check" lands on `#group`.
5. **Console errors and layout.**
   - **0 console errors and 0 page errors** across all pages: Overview, Data sources, Policies, Reports,
     wizard steps, details and evidence.
   - **No horizontal scroll at 390 px** on any of the 28 pages shot.
   - **0 of 150 seeded names and e-mails** visible on any page (25 per engine × 2 fields × 3 engines).
6. **Mockup comparison.** Mockups 03, 06, 07 and 10 were shot beside the pages; I compared them visually:
   - Group card: key node, wires, three systems, 3/3 verdict and pairs, all as in mockup 07.
   - Job detail: crumb, subtitle, buttons, stepper, classification table, partitions, Evidence-so-far and
     the joins tile, all as in mockup 03.
   - Wizard discover: heat-map, review table and job spec, as in mockup 06.
   - The intentional differences already logged still apply: a coverage table instead of masked sample
     rows; `NOTES` instead of `RM_NOTES`.
   - The evidence text in the job-detail classification table's EVIDENCE column is truncated at 1440 px. This
     is cosmetic.
7. **Screenshots** are in `docs/screenshots/m3/`: 92 files, desktop/mobile dark plus desktop light, and
   mockups. They are **not committed** because the verdict is FAIL.

## §6 proofs: source vs target, counts only

These are from `probes/compare.py`. The methods come from each job's accepted snapshot. "row_same" means the
same row joined on a kept unique column: Oracle `LAST_CONTACT_AT`, Mongo `_id`, PostgreSQL the composite of
8 kept columns (unique for 20000/20000).

### Walk targets

| Target | Rows src/tgt | Sensitive columns, row_same (all 0) | Set hits |
|---|---|---|---|
| Oracle `CUSTOMERS_V6960BF` | 20000/20000 | CUSTOMER_ID, FULL_NAME, NATIONAL_ID, TAX_ID, EMAIL, MOBILE, DOB, ADDR_LINE1, PIN_CODE, NOTES: **0 each** | 0, except CUSTOMER_ID 3 (FPE digit collisions with *other* ids) and DOB 12622 (date_shift domain; row_same 0) |
| PostgreSQL `customers_v6960bf` | 20000/20000 | customer_id, full_name, date_of_birth, national_id, tax_id, email, mobile, notes: **0 each** | same pattern (3 / 12622) |
| Mongo `profiles_v6960bf` | 20000/20000 | customerId, profile.fullName, profile.dob, contact.email, contact.mobile, addresses[*].line1, addresses[*].pin, kyc.taxId, accounts[*].iban, notes: **0 each** | customerId 3, dob 12556, all others 0 |

Kept by the preset: CITY, COUNTRY, SEGMENT, RISK_SCORE, timestamps, Mongo `_id`, `segment`, `riskScore`,
`balance`, `tags`, `addresses.type`, `verifiedAt`. G6-N7 (CITY kept) is unchanged.

### Smoke targets

| Target | Result |
|---|---|
| Oracle `CUSTOMERS_SMOKE` | row_same **0** on every sensitive column; set hits 0 except FPE id (8) and DOB |
| Mongo `profiles_smoke` | row_same **0** on every sensitive column; set hits 0 except FPE id (8) and DOB |
| PostgreSQL `customers_smoke` | set hits 0 on every high-cardinality sensitive column (no row join attempted) |

### Other checks

- **D3 confirmation:** the wizard required confirmation for the writes. Console M2/M3 tests passed.
- **Counting server (D4):** not applicable to M3, which has no scanner.

## Log, metrics and audit scan (`probes/logscan.py`)

- Scope: `docker compose logs` of **every container of project `dps-wt-m3`**, saved to `stack-logs.txt`. The
  containers are app, worker, dps-db, source-pg, mongo (17.5k lines), oracle, minio, migrate, minio-init,
  seed-sources and demo-secrets.
- Values scanned:
  - 224 088 source values of 6 or more characters: Oracle CRM customers sensitive columns; Mongo profile
    sensitive leaves; PostgreSQL customers, contacts value, addresses line1, accounts and cards.
  - The 6 key-material strings from the two group `.dpskey` files.
- Matching: token-normalised. A positive control finds 3 of 3 planted values.
- **0 value hits and 0 key hits in every container.**
- `/metrics`: `dps_llm_raw_pii_sent_total 0.0`, with 0 value hits and 0 key hits.
- **Audit:** 1235 `dps_audit_log` rows, with **0 value hits and 0 key hits**.

## New findings (non-blocking for M3; route to the owner WP)

- **N-new-1 (privacy, M2 or main discovery, outside M3 code): two PostgreSQL columns are classified
  `keep`.**
  - `bank.contacts.value`: 20000 e-mails plus 20000 phone numbers, `value_hit_rate 0`, no detectors.
  - `bank.addresses.line1`: street addresses.
  - With the default snapshot, a run copies them in clear.
  - Causes:
    - `classify_columns` scores each entity separately (`rate*mean` per entity), so a column that is
      50% EMAIL and 50% PHONE stays below `min_score` 0.6 for both.
    - In patterns-only mode, `line1` has no name rule.
  - The same logic is on main (`discovery.py:370`).
  - Suggested fixes:
    - combine whole-value hit rates across entities;
    - "mixed PII" means review required, not keep;
    - add a `line1`/`line2` name rule.
- **N-new-2: wording of `target_error`.** When a re-mapped value is too long for a created PostgreSQL target
  column, the partition retries 4 times with value-free `target_error` and never reaches a terminal state
  for about 10 minutes. Oracle's pre-run check gives `length_overflow`. Consider failing fast with
  `length_overflow` on PostgreSQL as well.
- **N-new-3:** explicit `keep` on a text column keeps drift sub-documents under it (see above).
- **Carried from G6, still open:**
  - N1: the test gate rewrites the 54 tracked `m3-shots` PNGs. The tree had them modified before I started,
    and my pytest run rewrote them again. I left them as found.
  - N2 to N4 and N8 to N10, unchanged.
- **Cross-system DATE_OF_BIRTH agreement is 1/500**, flagged "informational" in the coverage table: the
  date_shift values do not agree across systems. This is consistent with earlier rounds. Note it for the
  owner if DOB is expected to join.

## Working tree after verification

- HEAD is `392c1fe`, with no commits by me.
- Modified: 54 `docs/superpowers/build/m3-shots/*.png`, which were already dirty and were regenerated by
  the gate (G6-N1).
- Untracked: `docs/screenshots/m3/`, 92 PNGs from this walk, left for the fix round to recommit after a
  PASS.
- Stack: `docker compose --profile oracle --profile sources --profile docs down`, with the volumes kept and
  0 containers left.
- Cleanup:
  - The B6 probe tables were dropped: Oracle `PROBE_B6_*`; PostgreSQL `probe_b6_*` and `probe_ctl_*`.
  - The key files and values file in scratch were deleted.
