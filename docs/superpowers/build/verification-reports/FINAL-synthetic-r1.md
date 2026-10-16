# FINAL — v1.0.0 candidate on `main` — SYNTHETIC lens verifier, round 1

**Verdict: FAIL. Three blocking defects (B1–B3).** Every number in this report comes from live runs on the running `full` stack. No in-process fakes were used.

What passes:
- The copula job's measured metrics: fidelity **0.972 / 0.967** (two runs, 20,000 rows each), exact-row matches **0**, NNDR measured (**1.44 / 1.54**), MIA proxy AUC **0.484 / 0.499**.
- D-A on every written target: **0** source keys, **0** source government ids, all synthetic ids in never-issued ranges, and the in-place refusal works.
- Distinct people stay distinct and consistent, on L1 (400 requests) and on L2 PostgreSQL synthesize jobs (300-row tables). The checks:
  - a customer + nominee record gives two people whose e-mails follow them;
  - a first/last/e-mail record gives one consistent person;
  - free text with three titled people gives three distinct names.

What fails:
- **B1.** A copula pack can never show a `utility` block (DoD 13). `utility_label` exists only in a policy, and no preset sets it. So the run needs an inline or `@name` policy, and every such job dies in `finalize` (same root cause as FINAL-gates B1). This is confirmed live for synthesize.
- **B2.** D-A joins break for a foreign key without an entity type whose parent key has one. Example: `bank.customers.relationship_manager_id` → `bank.employees.employee_id` (EMPLOYEE_ID). Under one seed, **0 / 20,000** synthetic customers join a synthetic employee.
- **B3.** A titled name whose surname is one of the name-cue STOPWORDS (`Date`, `Le`, `Best`, `Branch`, `Holder`) keeps the **real surname** in synthetic (and obfuscated) text, and verification still passes:
  - L1: 10/10 per surname, both synthetic presets;
  - L2 memo column: 1 of 300 rows in each of 4 runs.

| | |
|---|---|
| Date | 2026-10-16, 04:25–04:52 UTC (09:55–10:22 IST) |
| Tree | `/Users/neelu/document_intelligence/data-privacy-services`, `main` @ `45be8ac` (`chore(release): v1.0.0`) |
| Stack | the gates verifier's running `full` stack (default ports), not restarted or rebuilt. `/readyz` ready (api-core up, llm stub, browser 1 online, ner patterns-only). 1 worker, shared with the other FINAL lens verifiers. |
| How | live API (`X-API-KEY` = the documented local dev key, local tenant). Connections: smoke's `bank-pg-ro` (source, production-tagged) → `bank-uat` (target). Comparisons read source-pg directly (`DPS_TEST_SOURCE_PG_DSN`). Key pseudonyms were aligned with the product's `KeyPseudonyms` and the run's returned seed, which was held in memory only and never printed or saved. |
| Data | probe source tables `fsyn1.{people_one,people_cn,memos}` (300 rows each): **invented** people (Faker en_IN/en_US, reserved domains). The copula ran on the demo `bank.customers` (20,000 rows, seed 7). |
| Values | counts, ids, column names, entity types and methods only. No value, key or seed appears in this report, its logs or the probe outputs. |
| Repo | **not modified** by this verifier (HEAD unchanged). `git status` shows an untracked `docs/screenshots/v1.0.0/` created at 04:40Z by another verifier, not by this one. |
| Probes | `~/.cache/dps-build/reports/FINAL-synthetic-r1/`: `l1_people.py` (`l1-people-n40.log`), `l2_setup.py`, `l2_jobs.py` (`l2-jobs-r1b.log`, `l2-result-r1b.json`), `l2_r2.py` (`l2-r2-r2a.json`), `l2_utility.py` (`l2-utility-u1.*`), `fk_parent.py`, `memo_aligned.py`, `memo_why.py`, `stopword_surnames.py`, `nndr_indep.py`, `ids_indep.py`, `inplace_refusal.py`, `log_scan.py`, `evidence-*.json` (value-free packs) |

## 1. Copula synthesize job — measured, gated, reported

Each run is `bank.customers` → `bank_uat.fsyn1_<tag>_customers_copula*`, with `options.tabular: copula`, write mode `table`, no policy (the snapshot's preset), discovery in `synthesize` mode and accepted as proposed.

| Run | Job / evidence | State | Fidelity | Exact rows | NNDR | MIA proxy | Utility |
|---|---|---|---|---|---|---|---|
| r1b | `j_01M51FT2E8MP4J8588KGT6S49C` / `ev_01M51FVRC494968QFK5SM7V24A` | succeeded, 20,000 rows, 55 s | **0.972** | **0** (resamples 0) | ratio **1.4358** vs target 0.95; holdout; zero-distance share 0.0; measured | AUC **0.4842**, run, `dcr_auc`, passed (target 0.6), 2,000 / 2,000 | `{"run": false, "reason": "no utility_label in the policy"}` |
| r2a | `ev_01M51G67VSYB0FXAC3KB5ZM2J2` | succeeded, 20,000 rows | **0.9668** | **0** | **1.5425**, passed | AUC **0.499**, passed | same "not run" |
| u1 (inline policy + `utility_label: $.segment`) | `j_01M51G02JVF3M3DHB95BHTQSA5` | **dead `finalize_error`**, 20,000 rows written, **no pack** | 0.9692 (job progress only) | — | — | AUC 0.4877 (progress only) | progress only: **run**, kNN-5 accuracy TSTR 0.2561 vs TRTR 0.2546, ratio 1.0059 → B1 |

- **Fidelity.** It is measured on a deterministic preview of the rows the job writes: 20,000 real rows, 4,024 holdout, 5,000 synthetic. The copula fitted 7 modelled columns. KS/TVD is 0.0027–0.0144 per column, all within the 0.05 limit. The largest correlation delta is 0.037.
- **Pack.** Label "synthetic — measured, not claimed anonymous"; verification `passed`, hits 0, 163,340 values checked. The HTML pack renders the fidelity, NNDR, membership, exact, utility and identifier blocks. The PDF is served (`%PDF`).
- **Independent re-measure** (`nndr_indep.py`, my own encoding of the 7 modelled columns: one-hot plus z-scores, leave-one-out):
  - NNDR **1.544** (synthetic→real median 0.232, real→real median 0.151);
  - **0** target rows at distance 0 from any source row on the modelled columns.
- **Independent exact-row check:** a SQL `INTERSECT` of all 15 non-key columns, source vs target = **0** rows, for r1b and for the dead u1 target.
- **Modelled columns redrawn:** `(segment, risk_score)` differs from the aligned source row in 19,994 of 20,000 rows.

## 2. D-A — no real key or government id in synthetic targets

| Check | Result |
|---|---|
| Pack `identifier_check` (r1b, r2a) | scope `whole_table`, rows read 20,000 / 20,000, `source_compared` true. `national_id` checked 20,000: in_source 0, issuable 0. `tax_id` (PAN_IN) checked 11,989: in_source 0, issuable 0. review_columns []. |
| Independent never-issued check (`ids_indep.py`, my own issuer rules by value shape) | target `national_id` **20,000 / 20,000** never-issued (Aadhaar leading 0/1, SSN area 9xx, NINO never-prefix, CURP inconvenient word); `tax_id` **11,989 / 11,989** PAN sequence `0000` |
| Source keys in the targets | `customer_id`: **0** of 20,000 (r1b, r2a, u1), of 300 (people_one, people_cn ×2), of 300 (`memo_id`, memos). Every target key aligns to exactly one source row through the keyed pseudonym (20,000/20,000, 300/300). |
| Government ids / e-mails anywhere | `national_id`, `tax_id`, `email`: any-row overlap **0** (r1b, r2a, u1). The tokens of every target cell, `notes` included, hold **0** source national id, tax id, e-mail, mobile or key. |
| Same-row survivors (r1b), every personal column | full_name, date_of_birth, national_id, tax_id, email, mobile, notes: **0** |
| FK `relationship_manager_id` | pseudonymised (`KEY`, `key_pseudonym`), 500 → 500 distinct, **0** values equal to a real employee id. But see **B2**: it does not join the synthetic employees. |
| In-place refusal | an `in_place` synthesize of `bank_restore.customers` (key `CUSTOMER_ID`), sent with `dry_run`, gets **422 `synthesize_in_place_sensitive_key`** and no job is created |
| Evidence packs | 0 string leaves and 0 tokens equal to a source value (copula pack, all 20,000 rows' names, e-mails, ids, mobiles) |
| B1-dead targets | `fsyn1_r1b_people_one_inline` and `fsyn1_u1_customers_utility` are fully written but have **no** identifier check (finalize died). Independent count: 0 source keys, 0 government ids, 0 e-mails, 0 exact rows. |

Chance overlaps (expected, from finite pools; the demo source was made by the same persona generator):
- full_name:
  - source vs run 1: 803 distinct names;
  - source vs run 2: 794;
  - run 1 vs run 2: 1,351.
  - CHANGELOG states "about 870 names per 20,000-row run".
- mobile:
  - source vs run 1: 936; source vs run 2: 975; run 1 vs run 2: 816.
- Same-row equality is 0 for every personal column.

## 3. Distinct people stay distinct and consistent

**L1** (`POST /api/v1/synthesize`, live app; 40 invented records per layout per preset; a fresh seed per request; `l1-people-n40.log`). The counts are the same for `synthetic-realistic` and `synthetic-strict`.

| Layout | Result per preset (of 40) |
|---|---|
| one person `{first_name, last_name, email}` | verified 40, originals kept **0**, e-mail = `<first>.<last>` **40**, first ≠ last 40, first is a first name 40 |
| customer + nominee, full names `{customer_name, email, nominee_name, nominee_email}` | verified 40, originals **0**, two different people **40**, first names differ 40, e-mail follows the customer **40**, nominee e-mail follows the nominee **40** (never the customer's) |
| customer + nominee, split fields (`nominee_first_name`/`_last_name`) | verified 40, originals 0, distinct 40, both e-mails follow their person 40 |
| text: "… Mr. A B met Mrs. C D; Dr. E F witnessed …" | verified 40, 3 PERSON_NAME findings 40, originals 0, template kept 40, **three distinct names 40** (first names distinct 40), Mr. → male first name 40, Mrs. → female 40 |
| record + notes with three titled third parties | verified 40, originals 0, e-mail follows 40, three distinct names 40, none is the customer 40, title genders 40 |

**L2 PostgreSQL synthesize jobs.** Source `fsyn1.*` → `bank_uat.fsyn1_*`, default policy unless noted. Rows are aligned by the keyed pseudonym. Counts are of 300 rows.

| Job | Result |
|---|---|
| people_one `j_01M51FT2ETW8KG3RKNZDVGT23C` | succeeded, verified; rows holding a source name word **0**; cells equal to source 0; e-mail follows the name **300**; first ≠ last 300; source e-mails 0 |
| people_cn `j_01M51FT2F6AZS501J73578Z21P` | succeeded, verified; source name words 0; cells equal 0; two people **300**; first names differ 300; e-mail follows the customer **300**; nominee e-mail follows the nominee **300**; source e-mails 0 |
| people_cn, `synthetic-strict` (`j_01M51FT2G19DWZVWB0MA2TQA36`) | same: 0 / 0 / 300 / 300 / 300 / 300 / 0 |
| memos (4 row-aligned runs: r2a, m0, m1, m2; the r1b run was not aligned, because its key label was unknown at the time), `memo` FREE_TEXT → spans surrogated | succeeded, verified; **three distinct names 300/300** in each run (first names distinct, ≥ 2 words); Mr. → male 300, Mrs. → female 300; `branch_code` kept; key pseudonymised. **Source name word surviving in the same row: 1/300 in each run** → **B3** (memo `43000146`, always the Mr.'s surname) |

## 4. Blocking findings

### B1 — a copula pack never shows a `utility` block (DoD 13); same root cause as FINAL-gates B1

- **Contract.** DoD 13: "a copula synthesize pack shows `privacy.membership_inference.run == true` **and a `utility` block when the policy names a label**".
- **Why it can't be met.** `utility_label` lives only in `policy.synthesize`, and no preset sets it. So the run must carry an inline body or an `@name` reference. Both are sealed by F-18, and `_finalize` → `_post_sample` → `_policy(label dict)` raises `PrepareFailure("policy_invalid")`. That exception is not caught by `_best_effort`, so the job is dead-lettered.
- **Live proof:**
  - `j_01M51G02JVF3M3DHB95BHTQSA5` (copula plus the inline `synthetic-realistic` body with `utility_label: $.segment`): prepare measured utility (`run: true`, ratio 1.0059) and wrote 20,000 rows, then died `finalize_error` after 3 `finalize_failed` attempts, with no pack.
  - `j_01M51FT2GEVQB0HVGMJDH0QCHE` (people_one with an inline policy): died the same way.
  - In the same window, 4 other jobs from another lens hit `finalize_failed` too.
- **Scope in this lens:** every synthesize run with a stored or inline policy. That covers constraints, quasi-identifiers and `utility_label` (all policy-only), and the M5 policy store.
- **Fix:** FINAL-gates B1 (use the opened spec in `_finalize`: `_post_sample`, `_store_evidence`'s `policy_version`, `_identifier_check`).
- **Tests to add:**
  - an integration test of a copula synthesize run with an inline policy naming `utility_label`, through the worker to `succeeded`;
  - it asserts `pack.utility.run`, the `membership_inference` block, `policy.name/version`, and that the `identifier_check` ran.

### B2 — D-A foreign keys do not join across one seed when the parent key is classified

- **Live proof** (`l2-r2-r2a.json`, `fk-parent-f1.json`):
  - `bank.customers` (copula) and then `bank.employees` were synthesized with the same `consistency.synthesis_seed`.
  - `relationship_manager_id` (declared `REFERENCES bank.employees(employee_id)`, no entity type) holds the pseudonym under label `KEY|bank.employees.employee_id` in **20,000 / 20,000** rows (0 under `EMPLOYEE_ID`).
  - The synthetic employees' `employee_id` (entity `EMPLOYEE_ID`) uses label `EMPLOYEE_ID`: 500 / 500 (0 under `KEY|…`).
  - Result: **0 of 20,000** synthetic customers' relationship managers exist in the synthetic employees table.
- **Root cause** (`dps/layers/connectors/targets.py::_identifiers`):
  - A column with an entity type gets label `None` (= its entity type).
  - A column without one that has a declared reference gets `KEY|<referenced column>`.
  - The child cannot see that the parent's key has an entity type.
- **Not covered by tests.** The existing tests cover entity type on both sides (`customer_id`) and none on either side (`loan_id`), not this mixed case.
- **Scope.** The demo schema has this case: `customers.relationship_manager_id` and `kyc_documents.verified_by` → `employees`. Any role-named FK to a CUSTOMER/EMPLOYEE/CASE key is affected.
- **Contract.** D-A says "consistent across the tables of one job group so joins and foreign keys still hold".
- **Fix (suggested):** label a declared FK the way its parent key is labelled. Either:
  - classify the referenced column (`classify_key(<referenced column name>)`, or the parent's accepted snapshot) and use the entity label when it is a `LINK_ENTITY`; or
  - label every link key by its reference.
- **Test first:** a pg_source integration test of parent `employees(employee_id)` with an entity type and child `customers.relationship_manager_id REFERENCES employees` without one, under one seed, asserting the join count equals the child's rows.

### B3 — a real surname that is a name-cue stopword survives in synthetic text, and verification still passes

- **L1 proof** (`stopword-surnames.out`): `"Branch visit: {Mr.|Mrs.|Dr.} <First> <Surname> confirmed …"`, with Surname ∈ {Date, Le, Best, Branch, Holder}; the first four are Faker en_IN/en_US surnames, and Le is one of the most common Vietnamese surnames.
  - The first name is replaced 10/10, but **the surname is kept 10/10**, for each surname under both `synthetic-realistic` and `synthetic-strict`.
  - `verification.passed` is true 10/10. Coverage reads `ner: off` and `complete: false`.
  - The same happens in obfuscate mode (`gdpr-pseudonymise`, `dpdp-pseudonymise`): surname kept 10/10, verified (`stopword-obfuscate.out`).
- **L2 proof:** the memos jobs keep memo `43000146`'s Mr. surname, which is a STOPWORDS member, in the aligned target row in every run: r2a, m0, m1 and m2. That is 3/900 rows in `memo-aligned.out`, all at "target a1 = source a1". The pack says verified.
- **Root cause** (`dps/engine/detect/names.py`): the title cue starts a run of capitalised words, but `STOPWORDS` (`date`, `le`, `best`, `branch`, `holder`, …) ends the run. The span covers the first name only (L1 finding 18–23 of a 10-character name). The leakage sweep never learns the surname is an original.
  - With the surname also declared in a field, the sweep catches it and the request is refused **422 `leakage`** 10/10 (fail closed; the S8 review saw this).
  - In free text alone, the surname is silently kept.
- **Release docs.** The CHANGELOG "Known limitations" does not state this.
- **Fix (suggested), test-first:** in a cue-started run, let a capitalised stopword that directly follows a name word (in a surname position, optionally confirmed by the persona surname pools) extend the span instead of ending it. Then:
  - re-run `stopword_surnames.py` (expected: surname kept 0, both modes);
  - re-run `memo_aligned.py` (expected: 0/900);
  - check that headings such as "Branch Office" are still not names.

## 5. Non-blocking notes

- **N1 (documented limitation, measured).** The copula redraws `id_type` and `country_code` while `national_id`, `tax_id` and `mobile` follow the source row's shape. Target `id_type` matches the national id's shape in **8,383/20,000** rows (source 20,000). `country_code` matches in 8,280. A PAN sits on an IN row in 7,089/11,989. CHANGELOG states it ("unless a `when` constraint says so"). A discovery-proposed constraint, or surrogates that follow the synthetic `id_type`/country, would close it.
- **N2.** A synthesize run that names no policy uses the snapshot's discovery preset. Its pack then reads `policy: gdpr-pseudonymise` (an obfuscation preset name) on a synthetic pack. The pack is correct (it is that preset's `synthesize` section) but confusing.
- **N3.** Synthetic mobiles collide with the source's on unrelated rows by chance: 936–975 distinct, run-vs-run 816, same-row 0. NANP and UK numbers use fictional ranges; IN and MX numbers are drawn "near the example number". The known-limitations line covers names and e-mails, not phones.
- **N4.** Utility needs `utility_label`, which no preset sets, so every default pack says "not run". Consider documenting it next to DoD 13, or a preset or discovery proposal.
- **N5.** B1-dead synthesize runs leave a fully written target with no identifier check and no pack. The independent count found 0 survivors on both (§2). This is worth stating in the B1 fix's test.

## 6. Privacy of this verification

- **Log scan** (`log_scan.py`, `log-scan.out`; app 3,101 lines, worker 155 lines since 04:25Z): **0** lines hold any probe or source value (92,664 needles: the probe tables, the memo people, `bank.customers` names, e-mails, ids and mobiles; the self-test passes).
- **Worker log:** 11 `finalize_failed` lines (B1): 3 are `j_01M51FT2GEVQB0HVGMJDH0QCHE`, 3 are `j_01M51G02JVF3M3DHB95BHTQSA5`, and 6 are another lens's jobs.
- **LLM:** every L1 response reports `llm.raw_pii_sent` 0 (llm stub).
- **Probe tables** in source-pg: `fsyn1.*` and `bank_uat.fsyn1_*`. Each one was created by this verifier and dropped at the end (§7). Their job, snapshot and evidence rows stay in dps-db, which cannot be deleted through the API. The shared demo tables were only read.

## 7. End state and re-verify after the fix round

- Stack: left running, not restarted. The probe tables were dropped.
- Re-run after B1–B3 are fixed and the stack is rebuilt. From `~/.cache/dps-build/reports/FINAL-synthetic-r1/`, with env.sh sourced:
  1. `l2_setup.py`;
  2. `l2_jobs.py <tag>` — expect `people_one_inline` to succeed with a pack;
  3. `l2_utility.py <tag>` — expect a pack whose `utility.run` is true;
  4. `l2_r2.py <tag>` — expect `fk.rm_in_synthetic_employees` = 20,000;
  5. `memo_aligned.py` — expect 0 source words;
  6. `stopword_surnames.py` — expect surname kept 0;
  7. `l1_people.py 40`.
