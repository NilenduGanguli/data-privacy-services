# M3 G4 review, round 1: Tasks 15, 16, 17, 19 (branch `m3`, worktree `dps-wt-m3`)

Reviewer: independent (did not rely on `M3-G4-impl.md`). HEAD reviewed: `b725cc8`. The working tree was clean before and after. No repository files were modified. Probes are in `~/.cache/dps-build/reports/probe-m3-G4/`.

## Verdict: FAIL (2 blocking)

Every gate and every test is green. The consistency check, its API and the resume behaviour are sound. Two problems block G4:

1. **B1, leak.** The three-way demo writes every MongoDB customer's street address and PIN code unmasked into the masked target `crm_uat.profiles_e2e`: 2,400 of 2,400 values survive, same row and any row. The e2e "source vs target per sensitive column" claim cannot see this. It only checks the columns the accepted snapshot already transforms, so the check uses the classifier's own list to verify the classifier.
2. **B2, false pass stored as group state.** `POST /job-groups/{id}/consistency-check` accepts the same `(job_id, path)` member twice. The result is `passed: true` with 1/1 pairs at coverage 1.0, and it is stored as the group's `last_check`, even when the real cross-system check fails.

## 1. Gates (run by me)

| Gate | Result |
|---|---|
| `./lane.sh ruff format --check dps tests tools` | 298 files already formatted |
| `./lane.sh ruff check dps tests tools` | All checks passed |
| `./lane.sh mypy dps` | Success: no issues found in 109 source files |
| `./lane.sh pytest -q` | **1704 passed, 330 skipped** (21 s) |
| G4 targeted integration, `DPS_TEST_REQUIRE_SERVICES=1` (test_consistency, test_lookup_values_m3, test_groups_consistency_api, db/test_groups, db/test_migrations, test_seed_model, e2e/test_seed_shared_ids, test_pipeline_seams_m3, test_docpaths, test_mongo_docs, test_batch_documents_m3, test_oracle_session_live, test_targets) | **118 passed**, 0 skipped |
| e2e, `-s`: `tests/e2e/test_m3_three_way.py`, `tests/e2e/test_m3_resume_after_kill.py`, `tests/connectors/test_mongo_pipeline.py` | **8 passed** (1 three-way, 4 resume, 3 Mongo pipeline), 134 s |
| Full lane integration: `-m "(db or pg_source or oracle or mongo) and not s3 and not browser and not ocr and not network"` (M2 resume, conformance on 3 engines, all M3) | **321 passed, 1 skipped**, 1712 deselected (5 min 22 s) |
| Frontend | not touched by G4, so not run |

Containers used: `dps-db`, `source-pg`, `oracle`, `mongo` of compose project `dps-wt-m3` (ports 5453 / 5454 / 1523 / 27019). I ran `docker compose down` afterwards. I touched no other project.

Value-free three-way result (from the `-s` run):
- passed; sampled 500; 3/3 pairs at coverage 1.0, matched 500 each; `all_members_matched` 500.
- Forms: postgres `numeric`, mongo `prefixed_digits` (width 8, prefix_len 5), oracle `numeric`.
- Counted attributes: EMAIL 500/500, PERSON_NAME 500/500, PHONE 500/500, NATIONAL_ID 500/500 (oracle and postgres), PAN_IN 309/309.
- DATE_OF_BIRTH (`date_shift`) is informational: 1/500.
- The drift member (Mongo `det_token` on `customerId`) gives problems `join_form_mismatch` and `method_mismatch`, matched 0.

## 2. Blocking findings

### B1: the three-way MongoDB target leaks address lines and PIN codes (by a source-vs-target comparison)

Probe: `probe-m3-G4/probe_all_columns.py`, output in `all_columns.txt`. It compares every column and leaf path of the three e2e targets with their sources (counts only). MongoDB documents are lined up by `_id`.

| path in `crm_uat.profiles_e2e` | compared | same-row survivors | any-row survivors |
|---|---|---|---|
| `$.contact.addresses[*].line1` | 2400 | **2400** | **2400** |
| `$.contact.addresses[*].pin` | 2400 | **2400** | **2400** |
| `$.contact.email`, `$.contact.mobile`, `$.profile.fullName`, `$.kyc.taxId`, `$.accounts[*].iban`, `$.notes`, `$.customerId` | 2400 (taxId 1447) | 0 | 0 |

The same customers' addresses are masked on Oracle (`ADDR_LINE1` 0 / `PIN_CODE` 0 any-row). The Mongo member is the only one that leaks.

Cause: discovery's name classification (`classify_key(column.path)`) does not recognise nested document leaves such as `addresses[*].line1` or `addresses[*].pin`. The value detectors (no NER) do not catch them either, so the accepted snapshot maps them to nothing and the run copies them as they are.

The deviation log knows this: "Open: … the three-way demo copies them unmasked unless the reviewer maps them". The pipeline and resume tests do apply `ADDRESS_REVIEW`; the three-way test does not. The test docstring and the deviation entry still claim "per-column source ≠ target for every column each accepted snapshot transforms (programme verification rule)". The programme rule (§2.2 Verify, §5 rule 6) is **every sensitive column**, not every transformed column. Under that rule this G4 flow fails.

Fix:
- **Discovery.** Make classification of document leaves path-aware: score the parent segments as well as the leaf name, e.g. `address(es)` with `line1|line2|street` → STREET_ADDRESS, and `pin|zip|postcode|postal` under an address → POSTAL_CODE. Add a unit test on the `crm.profiles` shape.
- **Three-way test.** Check every column and leaf path against an **independent** list of sensitive fields taken from the seed model (name, e-mail, mobile, national id, tax id, date of birth, address line, PIN/postcode, IBAN, notes on all three engines), not against the snapshot's own list. Assert 0 same-row survivors for all of them and 0 any-row survivors for the text ones.
- Correct the deviation-log wording.

### B2: a duplicated member makes the check pass and overwrites the group's stored verdict

`consistency._validate` and `ConsistencyCheckRequest` never reject repeated `(job_id, path)` members.

Probe: `probe-m3-G4/test_probe_duplicate_member.py`, run against the unit test's `World` fakes. The MongoDB member's tokens drifted on every row. Output in `duplicate_member.txt`:
- `pairs=[j_mdb $.customerId, j_mdb $.customerId]` gives `passed=True`, 1/1 pairs, coverage `[1.0]`.
- The derived check over the same group gives `passed=False`, 1/3.

The route then runs `save_last_check`, so `GET /job-groups/{id}` (and the Task 18 console panel) reports a passed consistency check for a group whose systems do not join. That is a false verdict persisted as group state and reachable through the public API with the `jobs` scope.

Fix: refuse repeated `(job_id, path)` with `422 member_duplicate`, and require at least two distinct members. Add a unit test and an API test. Contract §6.5: add the code. Do not forbid the same job with two different paths (a legitimate self-join such as `CUSTOMER_ID` ↔ `REFERRER_ID`).

## 3. Required but not blocking (acceptance evidence gap)

**Programme plan §2.2 G4** says "resume-after-kill on Oracle and Mongo for **every write mode**". `tests/e2e/test_m3_resume_after_kill.py` covers only `in_place` and `append` (generate), which are the M3 plan's four cases. `table` (`truncate_insert`, `upsert`) has no kill test on Oracle or MongoDB. Conformance replays batches but never kills a worker. The deviation log does not mention the gap.

I checked the behaviour with a probe, `probe-m3-G4/test_probe_table_resume.py`. It uses the same harness, the same key, a reference run, and a kill after 5 committed batches. Output in `table_resume.txt`:

| case | rows a / b | digests equal | resumed partitions | evidence |
|---|---|---|---|---|
| oracle truncate_insert | 3000 / 3000 | True | 3 | resumed 3, verified |
| oracle upsert | 3000 / 3000 | True | 3 | resumed 3, verified |
| mongo truncate_insert | 3000 / 3000 | True | 3 | resumed 3, verified |
| mongo upsert | 3000 / 3000 | True | 3 | resumed 3, verified |

So the behaviour is correct, but the acceptance proof is missing from the repository. Add these four cases (the probe is a ready template) and an Oracle `append` with a numeric `offset` before lane verification.

## 4. Checks that passed (what I verified, and how)

- **Tenant isolation.** Group, jobs, snapshots and connections are all loaded with the request tenant (`get_group`, `list_jobs`, `get_snapshot`, `get_connection`), and `save_last_check` updates by `tenant_id` and `id` under `db.acquire(tenant_id)`. The 404 cross-tenant test passes.
- **No injection.** The join path must equal a column path of the **described target shape**. Oracle uses typed binds in `IN` lists of 500 or fewer. PostgreSQL uses `= ANY($1)` with quoted identifiers. MongoDB uses `{field: {"$in": values}}`, where the field comes from the shape and the values are only canonical ints or strings rebuilt by `candidates`, never caller dicts.
- **Value-free.**
  - The result model carries ids, paths, forms, widths, `prefix_len` and counts.
  - The stored `last_check` equals the response (asserted in e2e).
  - The audit row carries only counts.
  - `_check_error` sends fixed details. Unknown exceptions go to `to_http` and become 500 with the type name only.
  - asyncpg `DataError` (whose message can quote bind values) is caught by `_fetch` as `InterfaceError` and becomes `source_error` with the class name.
  - Log scan of the captured three-way run, which includes the **in-process API** where the check runs (the e2e itself only scans the worker logs): 0 hits for e-mails, names, mobiles, national ids, `CUST-<id>` and tax ids of 3,000 seeded customers (`scan_log.py`).
- **Connectors closed.** Closed in `finally`, including on a describe failure or a bad path. A unit test checks this when a lookup raises.
- **Resume.**
  - `in_place` double-apply would change the digest; `append` duplicates would break the 6,000-row count. Both are checked.
  - Ledger rows are 0 after finalize.
  - Evidence `resumed_partitions` is at least 1.
  - The kill comes after 5 committed batches of 60, so the test cannot pass without a real resume.
- **Interfaces for other lanes.**
  - Migration `020` does not collide with main (001–010), m4 (030–031) or m5 (040–045).
  - The `JobGroup` fields are additive.
  - Contract §2.2 row 020, the §6.5 amendment and the §9 M3 row are updated.
  - `PIPELINE_ENGINES` now includes `mongo`. That is internal and covered by tests.

## 5. Deviation log, judged entry by entry

| Entry | Judgement |
|---|---|
| T15: the seed model keeps M2's four-country bank instead of the plan's India-only one; per-customer templates and randomness; Aadhaar unspaced | **Accepted.** The plan also requires M2's PG customers to come from the same generator, and M2 is four-country. The per-type rules in the test are the M0 rules, not weakened. The prefix property and the ≥95 % distinctness test strengthen it. |
| T15: `CRM.CUSTOMERS.NATIONAL_ID` widened to VARCHAR2(18) | **Accepted.** WP-I and F6 must recreate main's Oracle container. Carry this into the integration notes. |
| T15: seed tools return `int \| str`; TRUNCATE; DOC_TYPE by id type; PAN NULL outside IN; GB notes without phone; shared-ids test in its own schema | Accepted. |
| T16: typed signatures; log event `consistency_checked` | Accepted (contract §0.1 snake_case). |
| T16: free-text columns never join or compare; row locators refused | Accepted, and necessary (the plan's rule would fail every real check). |
| T16: members validated before any connector is opened; derived members only eligible jobs (≥ 2) | Accepted, but see B2: validation still lacks duplicate refusal. |
| T16: per-engine `lookup_values` details (Oracle chunking and dedup, Mongo top-level projection, PG `ANY`) | Accepted. Verified by the 4 lookup integration tests. |
| T16: implementation before unit tests, with mutation checks standing in for red | Accepted (the plan gave both). The extra "counted attribute disagrees" test is good. |
| T17: `save_last_check` in `dps/groups.py`; refusals audited too; KEK from `kek_of` | Accepted. |
| T19: Mongo enters the pipeline; `column_at` path lookup; Mongo created targets without DDL types; ObjectId `generate` append | Accepted. M2 touch points carry tests, and the full M2 integration and conformance suites are green (321 passed). |
| T19: G3 r3 B1/B2 fixes (`_unclassified` for documents, segment-aware paths) | Accepted. Tests exist and fail on the old code (per the log; the current suite is green). |
| T19: Oracle `client_identifier=""` drops thin sessions | Accepted. A real bug, with a regression test. |
| T19: e2e conftest reuses M2's `api_settings` (auth on) instead of auth off | Accepted (stronger). |
| T19: PG member in its own schema `m3e2e` | Accepted. |
| T19: "per-column source ≠ target for every column each accepted snapshot transforms (programme verification rule)" | **Rejected as stated** (B1). The programme rule is every sensitive column. |
| T19: drift member is MongoDB `det_token` instead of an Oracle `CUSTOMERS_DRIFT` | Accepted for the test. Side note: the stated reason (a `det_token` on an Oracle `NUMBER(12)` key would create a `NUMBER(12)` target) suggests the pre-run check may not refuse that combination, so it would fail at write time. Not run here; worth a test in G6 or verification. |
| T19: resume driven through `submit_run` in process rather than `POST /jobs/run` | Accepted (same submit code path). Covers only 2 of the write modes; see §3. |
| T19 open: Mongo addresses not discovered | **Not acceptable as "open" for G4.** It is B1. |
| T19 open: a `surrogate` override in obfuscate mode passes submit, then dies with `internal_error` | Agree it is M2 behaviour. Route it to WP-H or F as a finding (it should be refused at submit). Not blocking G4. |

## 6. Non-blocking observations and nits

1. **DATE_OF_BIRTH across systems.** Oracle and PG targets agree on 2,400 of 2,400. MongoDB agrees with neither on all but 2, because the `date_shift` anchor text is `CUST-<id>` there (`probe_dob_pairwise.py`). The plan (contract §6.2) makes this informational, so it is correct as specified. A UAT user will still see a different birth date for the same customer in Mongo. Consider canonicalising the anchor through the join form in a later milestone.
2. A counted attribute with `compared == 0` reports `consistent: true` (vacuous). Consider `consistent = compared > 0 and …` or a problem code.
3. `list_jobs(..., limit=200)`: a member beyond the first 200 run jobs of a group gets `member_not_in_group`. Fine for now; document it or paginate.
4. The derived check fails the whole group with `no_shared_entity` as soon as one eligible job is on an unrelated table (e.g. `LOANS.APPLICANTS`). This follows the plan; the console should suggest explicit `pairs`.
5. The `wait_for` timeout cancels the coroutine, but a running Oracle call in its thread runs on until `call_timeout`. Close may wait for it. Acceptable.
6. The `_sensitive()` helper in `test_m3_three_way.py` is unused.
7. The three-way log scan covers worker logs only. Add the API process (`caplog`/capsys) to the assertion so the in-process check is covered by the test, not only by this review.
8. The 1 skip in the full integration run was not identified (no `-rs`). It is likely a capability skip and is not G4-specific.

## 7. Artefacts

| Path | What it is |
|---|---|
| `probe-m3-G4/unit_gates.log` | static and unit gates |
| `probe-m3-G4/e2e.log` | e2e run with the value-free `M3_THREE_WAY` result |
| `probe-m3-G4/integration_all.log` | full lane integration run |
| `probe-m3-G4/all_columns.txt` | B1 evidence |
| `probe-m3-G4/duplicate_member.txt` | B2 evidence |
| `probe-m3-G4/table_resume.txt` | §3 evidence |
| `probe-m3-G4/*.py` | the probes |
