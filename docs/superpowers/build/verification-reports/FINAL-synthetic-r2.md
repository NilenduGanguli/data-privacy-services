# FINAL — v1.0.0 candidate on `main` — SYNTHETIC lens verifier, round 2

**Verdict: PASS. No blocking finding.** Round 1's three blockers are fixed live on the running stack:
- **B1:** inline and `@name` policy runs now finish with a pack.
- **B2:** the foreign key joins its parent, 20,000 / 20,000 rows.
- **B3:** stopword surnames no longer survive, 0 kept on L1 and L2.

Every item of the lens holds on live runs:
- copula fidelity >= 0.85;
- 0 exact rows;
- NNDR measured;
- utility and MIA proxy reported;
- D-A identifier check clean;
- distinct people stay distinct and consistent on L1 and on L2 PostgreSQL synthesize jobs.

No in-process fakes were used.

| | |
|---|---|
| Date | 2026-10-16, 07:00–07:13 UTC (12:30–12:43 IST) |
| Tree | `/Users/neelu/document_intelligence/data-privacy-services`, `main` @ `d033dd3` (round 1 verified `45be8ac`; fixes in `c7ad14f`) |
| Stack | The gates verifier's running `full` stack (default ports). I did not restart or rebuild it. `/readyz` reported `ready: true`. The sha256 of `names.py`, `targets.py`, `jobs_run.py`, `tabular.py` and `synth/keys.py` is identical on the host, the app and the worker. 1 worker, shared with the other lens verifiers. |
| How | Live API (`X-API-KEY` = the documented local dev key, local tenant). Source `bank-pg-ro` (production-tagged), target `bank-uat`. Comparisons read source-pg directly (`DPS_TEST_SOURCE_PG_DSN`, contract §12.2 default). Key pseudonyms were aligned through the product's `KeyPseudonyms` and each run's returned seed. Seeds were held in memory only, never printed or saved, and cleared. |
| Data | Probe tables `fsyn2.{people_one, people_cn, memos}` (300 rows each, the same Faker seeds as r1, so memo `43000146` again has a stopword surname) and **new** `fsyn2.memos_sw` (60 rows; every titled person's surname is one of Date/Le/Best/Branch/Holder/Sales). All people are **invented**, with reserved domains. The copula ran on the demo `bank.customers` (20,000 rows, seed 7). |
| Values | Counts, ids, column names, entity types, methods and dictionary stopwords only. |
| Repo | **Not modified.** HEAD is unchanged. `git status` shows only the untracked `docs/screenshots/v1.0.0/` (another verifier's). |
| Probes | `~/.cache/dps-build/reports/FINAL-synthetic-r2/`. Scripts: `l2_setup.py`, `l2_run.py` (→ `l2-run-a.log`, `l2-result-a.json`, `evidence-a-*.json`), `l1_people.py`, `stopword_surnames.py`, `memo_words.py`, `memo_sw_trace.py`, `nndr_indep.py`, `ids_indep.py`, `inplace_refusal.py`, `log_scan.py`. Outputs are the `*.out` files. |

## 1. Copula synthesize job: measured, gated, reported

Each run is `bank.customers` → `bank_uat.fsyn2_a_*`, with `options.tabular: copula` and write mode `table`. All discovery is in `synthesize` mode and accepted as proposed.

| Run | Job / evidence | State | Fidelity | Exact rows | NNDR (target 0.95) | MIA proxy (target 0.6) | Utility | Pack policy |
|---|---|---|---|---|---|---|---|---|
| copula (no policy) | `j_01M51RDJSPAMR6W385X1T0JF0M` / `ev_01M51RNR75B8R8RBYR1ARMX7NA` | succeeded, 20,000 | **0.9702** | **0** | **1.509** measured, passed, holdout, zero-distance 0.0 | AUC **0.494**, run, `dcr_auc`, passed, 2,000/2,000 | `run: false`, "no utility_label in the policy" | `gdpr-pseudonymise` v1 (N2) |
| inline policy + `utility_label: $.segment` | `j_01M51RDJTG4NX35XE3M23ZDHZA` / `ev_01M51RNZPF250G7FWQBP9NHPNP` | succeeded, 20,000 | **0.9752** | **0** | **1.4156** passed | AUC **0.4962**, run, passed | **run**, classification, knn5, TSTR 0.2493 vs TRTR 0.2525, ratio **0.9872**, 4,024 test rows | `synthetic-realistic` v1 |
| stored `@fsyn2-util-a` (create 201 → draft 200 → publish 201, v2) | `j_01M51RDKTCXXJS9RFSN5SYEV6X` / `ev_01M51RP2JSTWCC4W7VFXE5M8RK` | succeeded, 20,000 | **0.9691** | **0** | **1.444** passed | AUC **0.493**, run, passed | **run**, ratio **1.003** (0.2582 vs 0.2574) | `fsyn2-util-a` **v2** |

- **Pack.** All three runs share the following:
  - label "synthetic — measured, not claimed anonymous";
  - `tabular.engine` copula, 7 modelled columns, holdout of about 4,000 rows, preview of 5,000;
  - verification `passed`, hits 0;
  - `review_required` false.
- **Render.** The HTML pack is served (200) and holds the fidelity, NNDR, membership, exact, utility and identifier blocks. The PDF is served (200, `%PDF`).
- **Independent re-measure** (`nndr-indep-a.out`). I used my own encoding of the modelled columns (18 features), leave-one-out:
  - NNDR **1.4971** (synthetic→real median 0.2254, real→real 0.1505);
  - **0** of 20,000 target rows at distance 0 from a source row.
- **Independent exact rows.** A SQL `INTERSECT` of all 15 non-key columns, source vs target, gives **0** for each of the three targets.
- **Modelled columns are redrawn.** `(segment, risk_score)` differs from the aligned source row in 19,992 of 20,000 rows.

## 2. D-A: no real key or government id in synthetic targets

| Check | Result |
|---|---|
| Pack `identifier_check` (all 3 copula runs) | `scope: whole_table`, rows read 20,000/20,000, `source_compared: true`. `national_id`: 20,000 checked, in_source 0, issuable 0. `tax_id` (PAN_IN): 11,989 checked, in_source 0, issuable 0. `review_columns: []`. The inline and `@name` runs carry it too (round 1: no check, because finalize died). |
| Independent never-issued (`ids-indep-a.out`) | `national_id` **20,000 / 20,000** never-issued; `tax_id` **11,989 / 11,989** PAN sequence `0000` |
| Source keys in targets | `customer_id`: **0 / 20,000** in each copula target, and every target key aligns to exactly one source row (20,000/20,000). `customer_id` / `memo_id`: **0** in every people and memo target. Every target aligns 300/300 (or 60/60) through the keyed pseudonym (`CUSTOMER_ID`, `KEY\|fsyn2.<table>.memo_id`). |
| Government ids, e-mails, mobiles, keys anywhere | `national_id`, `tax_id`, `email`, `relationship_manager_id`: any-row overlap **0**. The tokens of every target cell, `notes` included, match **0** source ids, e-mails, mobiles or keys. The notes of each target hold 0 source customer names and 0 employee names. |
| Same-row survivors, every personal column (3 copula targets) | full_name, date_of_birth, national_id, tax_id, email, mobile, notes: **0** |
| FK `relationship_manager_id` → `employees` (B2) | `bank.employees` was synthesized under the copula run's seed. **20,000 / 20,000** synthetic customers join a synthetic employee (round 1: 0). Both sides are keyed `EMPLOYEE_ID` (20,000 and 500/500; 0 under `KEY\|…`). **0** FK values and **0** synthetic employee ids equal a real employee id. |
| Employees target | 0 ids, e-mails or phones overlap the source; 0 exact rows; `hired_on` (DATE, kept) is surrogated (0/500 equal). See N5. |
| In-place refusal | An `in_place` synthesize of `bank_restore.customers` (key `CUSTOMER_ID`, `dry_run`) gets **422 `synthesize_in_place_sensitive_key`**, and no job is created. |
| Evidence packs | The 13 saved packs hold **0** string leaves and **0** substrings equal to any probe value, `bank.customers` value or `bank.employees` value (93,533 needles, `evidence-scan.out`). |

Chance overlaps, expected from finite pools (the demo source was made by the same persona generator):
- full_name 779–830 distinct;
- mobile 949–964;
- date_of_birth about 8,100.

Same-row equality is 0 for every one of these columns.

## 3. Distinct people stay distinct and consistent

**L1** (`POST /api/v1/synthesize`, live app; 40 invented records per layout per preset, a fresh seed per request; `l1-people-n40.log`). The results are identical for `synthetic-realistic` and `synthetic-strict`:

| Layout | Result per preset (of 40) |
|---|---|
| one person `{first_name, last_name, email}` | verified 40, originals kept **0**, e-mail follows `<first>.<last>` **40**, first ≠ last 40, first is a first name 40 |
| customer + nominee, full names | verified 40, originals **0**, two different people **40**, first names differ 40, e-mail follows the customer **40**, nominee e-mail follows the nominee **40** (never the customer 40) |
| customer + nominee, split fields | verified 40, originals 0, distinct 40, both e-mails follow their person 40 |
| text "Mr. A B met Mrs. C D; Dr. E F witnessed …" | verified 40, 3 PERSON_NAME findings 40, originals 0, **three distinct names 40** (distinct first names 40), Mr. → male 40, Mrs. → female 40 |
| record + notes with three titled third parties | verified 40, originals 0, e-mail follows 40, three distinct names 40, none is the customer 40 |

**L2 PostgreSQL synthesize jobs.** Source `fsyn2.*` → `bank_uat.fsyn2_a_*`. All succeeded and verified, with hits 0. Rows are aligned through the keyed pseudonym.

| Job (rows) | Result |
|---|---|
| people_one, default (300) `j_01M51RDKTXRT4N4Y4FJ5JCGS1S` | source name words in the same row **0**; cells equal 0; e-mail follows the name **300**; first ≠ last 300; source e-mails 0 |
| people_one, **inline policy** (300) `j_01M51RDKW9NJ9AK6AAFM8HW877` | same, **300 / 0 / 0**; has a pack (round 1: dead) |
| people_cn, default (300) `j_01M51RDKVC0749YCM29FCT9SZT` | name words 0; cells equal 0; two people **300**; first names differ 300; e-mail follows the customer **300**; nominee e-mail follows the nominee **300**, never the customer's 300; source e-mails 0 |
| people_cn, `synthetic-strict` (300) `j_01M51RDKVV4014AQ828THQ86ZX` | same: 0 / 0 / 300 / 300 / 300 / 300 / 300 / 0 |
| memos ×3 (300 each) `…KWS9C…`, `…MWMZ9…`, `…MXBG4…`; plus 2 traced runs | template kept 300; **three distinct names 300** (distinct first names 300, ≥ 2 words 300); Mr. → male 300; Mrs. → female 300. Name-slot words equal to the aligned source **0** (round 1: 1/300 each run, memo `43000146`). No word outside template + names. |
| memos_sw, realistic and strict (60 each) | every source surname is a stopword: same-slot surname kept **0 / 60** in both; three distinct names 60; genders 60 |

The generic per-row word check reports 300/300 for memos. That comes only from the kept `branch_code` (`BRnn` → "br"): `memo_words.py` finds no target word outside the template and the name slots, and the name-slot check is 0.

## 4. Round-1 blockers re-verified

| r1 | Live result on `d033dd3` |
|---|---|
| **B1** inline / `@name` policy runs die in finalize; no utility block | 3 inline or `@name` synthesize runs succeeded with packs. `policy.name/version` are correct (`fsyn2-util-a` v2). `utility.run` is true (0.9872 / 1.003) and the MIA proxy ran. `identifier_check` is present. The worker log has **0** `finalize_failed` lines since 07:00Z. |
| **B2** an unclassified FK does not join its classified parent | **20,000 / 20,000** join; label `EMPLOYEE_ID` on both sides |
| **B3** stopword surnames survive | L1 (`stopword-surnames.out`): **0 kept** out of 36 requests per form. Forms: r1's template, end of sentence, comma, three-word name, two people, semicolon. Presets: both synthetic presets, plus obfuscate (`gdpr-pseudonymise`, 3 forms). Verified 36/36 each. L2: 0 / 900 memos (row `43000146`), 0 / 120 memos_sw. |

The stopword surnames that do appear in targets are pool draws. `memo_sw_trace.py` found 3 synthetic slots ending in "Date" (the en_IN pool has it). Each one sits on a source person whose surname is not a stopword, and none equals its source slot.

## 5. Non-blocking notes

- **N1 (documented in the CHANGELOG, unchanged from r1).** The copula redraws `id_type` and `country_code`, while ids follow the source row's shape. Measured on the target:
  - `id_type` matches the id's shape in 8,401 / 20,000 rows;
  - `country_code` matches in 8,429;
  - a PAN sits on an IN row in 7,220 / 11,989.
- **N2 (r1).** A synthesize run that names no policy uses the snapshot's discovery preset, so its synthetic pack reads `policy: gdpr-pseudonymise`.
- **N3 (r1).** Chance mobile collisions with the source: 949–964 distinct values, same-row 0. Names and e-mails are in the known limitations; phones are not.
- **N4 (r1).** Utility needs a `utility_label` in a policy; no preset sets one, so default packs say "not run" with the reason. Inline and `@name` now work.
- **N5 (new, design).** A non-tabular synthesize keeps unclassified columns. In the demo, `bank.employees.branch_code` is unique per employee (500/500), so it links each synthetic employee row to its real employee. Through that link:
  - `role`, which is kept, is equal in 500/500 rows;
  - name, e-mail, phone and the id are synthetic (0 overlap);
  - `hired_on` is surrogated (0/500).

  This is consistent with the label "measured, not claimed anonymous" and with QI uniqueness being reported but not gated. It is worth one line in the known limitations: a unique non-personal column re-exposes row correspondence outside the copula path.
- **N6 (display).** The pack's column entry for `relationship_manager_id` shows `entity_type: KEY`, while its pseudonym is keyed by `EMPLOYEE_ID` (proved above). It is cosmetic only.

## 6. Privacy of this verification

- **Log scan** (`log-scan.out`; app 1,386 lines and worker 171 lines since 07:00Z): **0** lines hold any probe or demo source value. That is 93,877 needles: the probe tables, the memo people, and `bank.customers` / `bank.employees` names, e-mails, ids and mobiles. The self-test passes. Both logs have 0 error-level lines and 0 `finalize_failed` lines.
- `dps_llm_raw_pii_sent_total` = **0** (`/metrics`).
- **Cleanup** (`cleanup.out`): 15 target tables and 4 probe source tables were dropped, along with the `fsyn2` schema. 0 left, and round 1's `fsyn1` left 0 too. The demo tables were only read.
- **Left in dps-db** (no API delete exists): the job, snapshot and evidence rows, and the published test policy `fsyn2-util-a` (v2) in the local tenant.

## 7. End state

The stack is left running as found: not restarted, not rebuilt, no `docker compose` command run. The repository is untouched.
