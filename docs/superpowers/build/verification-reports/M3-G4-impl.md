# M3 G4 implementation report — Tasks 15, 16, 17, 19

Worktree `/Users/neelu/document_intelligence/dps-wt-m3`, branch `m3`. Started from a clean tree at
`d5b734b` (G3 r2 fix); no earlier G4 attempt existed (no report, no uncommitted files). Author of every
commit: `NilenduGanguli <nilendu.ganguli@gmail.com>`, no trailers, explicit pathspecs.

## Commits

| Commit | Task | Message |
|---|---|---|
| `11be77c` | 15 | feat(tools): one synthetic customer space seeded into PostgreSQL, Oracle and MongoDB |
| `2cc6c9d` | 15 | docs(handoff): three-engine demo seeding and the widened Oracle NATIONAL_ID |
| `3a8a79e` | 16 | feat(connectors): cross-system consistency check — format-aware joins over masked targets, counts only |
| `f7d3ce5` | 17 | feat(api): job-group consistency check route; last result stored on the group (migration 020) (also carries the T16 + T17 deviation entries and the contract amendment) |
| `4d4ad4e` | 17 | docs(handoff): migration 020 and the job-group consistency check |
| `30e0595` | 19 | feat(pipeline): MongoDB runs end to end — document columns planned and checked by path, segment-aware paths, nested drift searched, Oracle sessions without a job id |
| `b725cc8` | 19 | test(e2e): three-way consistency join through the API; resume after kill on Oracle and Mongo (+ T19 deviations, HANDOFF) |

## What was built

**Task 15 — one customer space.** `tools/seed_model.py` (`SeedCustomer`, `seed_customers`): customer
`i` = id `10_000_001 + i`, every attribute a pure function of `(i, seed)` (so `--rows 200` is a prefix of
`--rows 2400`), values from the M0 surrogate engine with per-customer template originals (≥ 95 %
distinct e-mails/phones/national ids/names; M2's generator had 4 distinct mobiles per 300 customers).
`tools/seed_oracle.py` (six tables as the demo owners, `executemany` 1000, typed CLOB binds, FF9
timestamps, `CRM.DPS_SEED_INFO` marker), `tools/seed_mongo.py` (`crm.profiles`, unique `customerId`
index, `crm.dps_seed_info`), `tools/seed_sources.py --target postgres|oracle|mongo|all` with
`--oracle-dsn/--mongo-uri` (lane-safe defaults), PG customers' personal fields from the same model.

**Task 16 — consistency check.** `dps/layers/connectors/consistency.py` (plan interfaces: request /
result models, `detect_form`, `canonical`, `comparable`, `candidates`, `attribute_text`,
`derive_members`, `run_consistency_check`, `ValueLookup`), `lookup_values` on Oracle (`lookup_sql`,
≤ 500 typed binds per statement, CHAR padding), MongoDB (`$in`, top-level projection, majority read,
`maxTimeMS`) and PostgreSQL (`= ANY($1)` in the read-only transaction). Results are value-free.

**Task 17 — API.** Migration `020_job_group_last_check.sql`; `JobGroup.last_check/last_check_at`;
`groups.save_last_check`; `POST /api/v1/job-groups/{id}/consistency-check` (scope `jobs`, rate limit,
`wait_for(connector_statement_timeout_seconds)`, codes 404/409/422/502/503/504 with fixed details,
audit `group.consistency_check` passed/failed + counts; refusals audited via M2's `rejected`).
Contract §2.2, §6.5 (as-built amendment), §9 updated.

**Task 19 — end to end.** MongoDB entered `PIPELINE_ENGINES`. Required pipeline changes (all
test-first): path-based column lookup in the run handler (`targets.column_at`; `_check_snapshot`,
`build_column_plans`, `_target_plans`, `_check_lengths`, `_unbounded_plans`), no DDL types for created
Mongo targets, same-collection `append` + `generate` on an ObjectId key. Fixed the two open G3 r3
blockers (B1 nested drift under a scalar-sampled field; B2 field names containing `[digits]`) because
they became reachable with Mongo in the pipeline. Found and fixed an Oracle bug: every Oracle
connection test through the API failed (empty `client_identifier` → DPY-4011 in thin mode). New tests:
`tests/connectors/test_mongo_pipeline.py` (in-process table / in_place / same-collection append),
`tests/e2e/conftest.py`, `tests/e2e/test_m3_three_way.py`, `tests/e2e/test_m3_resume_after_kill.py`.

## Gate results (all through `./lane.sh`, lane containers dps-db, source-pg, oracle, mongo)

| Gate | Result |
|---|---|
| `ruff format dps tests tools` / `ruff check` | 298 files unchanged / All checks passed |
| `mypy dps` | Success: no issues found in 109 source files |
| `pytest -q` (full, final tree before the last commit pair; log `~/.cache/dps-build/reports/m3-g4/t19_full.log`) | **2020 passed, 14 skipped** (before G4: 1977 passed per G3 r3; T15 run 1979 / 14 skipped) |
| `pytest -q -rs` (final committed tree `b725cc8`, `m3-g4/final_full.log`) | **2020 passed, 14 skipped** |
| `DPS_TEST_REQUIRE_SERVICES=1 pytest -m "oracle or mongo"` | **154 passed, 1 skipped** (Mongo composite-key capability), log `m3-g4/oracle_mongo_required.log` |
| `DPS_TEST_REQUIRE_SERVICES=1` on the G4 files (e2e ×6, API ×6, consistency ×15, lookups ×4, seed model/sources, migrations, groups) | **54 passed**, log `m3-g4/g4_files_required.log` |
| `python -m dps.migrate` then `--verify` on a scratch DB of the lane's dps-db | 11 applied (020 last), `migrations: verified` |
| frontend | not touched (no npm gates) |

Skips: none of the G4 tests skip. The 14 full-suite skips (final run): MinIO not started (3:
`test_files_s3`, `test_storage_s3` ×2), app container not started (3 `test_console_m2`), azure-ocr-mock
not started (3), no tesseract binary (2), `presidio_analyzer` / spaCy `ner` extra not installed (2), the
Mongo composite-key capability case (1). None touch G4.

## Programme acceptance G4 (§2.2)

- **One customer space across PG/Oracle/Mongo**: `tests/e2e/test_seed_shared_ids.py` — the id sets of
  `m3_seed_ids.customers`, `CRM.CUSTOMERS` and `crm.profiles` are equal (200 each), 10 sampled names
  agree in all three, a second run prints `unchanged` for all 7 Oracle/Mongo tables and "already
  seeded" for PG.
- **Consistency check reports overlap > 0 per pair with counts only** (three-way e2e, 2,400 customers
  per system, value-free result in `m3-g4/three_way_result.json`): passed; pairs (0,1), (0,2), (1,2)
  each checked 500 / matched 500 / coverage 1.0; `all_members_matched` 500; forms postgres numeric,
  mongo prefixed_digits (width 8, prefix_len 5), oracle numeric; join `CUSTOMER_ID`/`fpe_digits` in all
  three; counted attributes EMAIL 500/500, PERSON_NAME 500/500, PHONE 500/500, PAN_IN 304/304,
  NATIONAL_ID 500/500 (Oracle + PG); DATE_OF_BIRTH informational (date_shift, anchor-dependent).
  Drift member (Mongo `customerId` det_token): `passed: false`, problems `join_form_mismatch` +
  `method_mismatch`, matched 0. The stored `last_check` equals the response.
- **Resume after kill on Oracle and Mongo for every write mode the plan names** (`in_place`,
  `append`; `table` resume is M2's and Oracle's earlier key-less ledger test): 4/4 pass — first worker
  SIGKILLed after ≥ 5 committed batches, a second worker finishes; A equals the reference B (in place:
  every row; append: the multiset of appended rows without generated keys; 6,000 rows each); ledger
  rows of both jobs 0; evidence `resumed_partitions ≥ 1` and verification passed; 0 seeded e-mails /
  names left in the written rows; 0 seeded values in the worker logs.
- **Source ≠ target per sensitive column (counts only)**: three-way targets, every column the accepted
  snapshot transforms, rows lined up through the masked key: Oracle 10 columns, PostgreSQL 8, MongoDB 8
  leaf paths — 2,400 rows each, **0 same-row survivors, 0 any-row survivors** for text columns.
  In-process Mongo pipeline: 7 leaf paths, 0 survivors in `table`, `in_place`, `append`.
- **No values in logs**: e2e worker logs scanned for e-mail, name, phone, national id and
  `CUST-<id>` of 200 seeded customers: 0 hits; resume workers: 0 hits (100 customers × 3 fields).

## Deviations (full text in `docs/superpowers/build/M3-DEVIATIONS.md`, sections Task 15/16/17/19)

1. Seed model keeps M2's four-country customer model (AADHAAR/SSN/NINO/CURP by country, PAN for IN only)
   instead of the plan's India-only model, so M2's PG tests and demo stay valid; Oracle
   `NATIONAL_ID` widened to `VARCHAR2(18)` (a CURP) — **the lane's Oracle container was recreated**;
   any other stack with the old init must be too. M2's `generate_rows` takes the customers' personal
   fields from the model (fresh PG seeds change values; reseed `bank` with `--reset` for a demo).
2. Free-text columns are never joined or compared in the check (else every real check fails on notes).
3. Members validated before any connector opens; derivation takes only eligible jobs.
4. `save_last_check` in `dps/groups.py`; refusals of the route are audited too.
5. e2e uses M2's auth-on `api_settings` + `worker_env`; PG member in its own schema `m3e2e`; the drift
   member is MongoDB (the plan's Oracle `det_token` key would land in a `NUMBER` column by the code);
   resume test reuses M2's handler-level harness (`submit_run`) instead of `POST /jobs/run`.
6. M2 touch points with their tests: `test_seed_sources.py` (CLI), `test_migrations.py` (020),
   `test_groups.py` (column set), `test_targets.py` (engine gate now an s3 source),
   `test_pipeline_seams_m3.py` (gate asserts three engines).

## Open issues

- **Discovery accuracy (no NER):** MongoDB `contact.addresses[*].line1` / `pin` are not classified (no
  column name to go by), so the three-way demo copies them unmasked unless a reviewer maps them (the
  pipeline test does). Oracle's `ADDR_LINE1`/`PIN_CODE` are found by name.
- **M2 gap:** a `surrogate` override in obfuscate mode passes submit and fails every prepare with
  `internal_error` (MethodError, no seed) until the job is dead; should be a submit-time refusal.
- **Mongo `files` write mode** is not exercised with document sources.
- G3 r3 non-blocking notes and the Task 12 clock-skew residual are unchanged.
- `.env.example` still lacks the M3 settings (session rules deny `.env*` edits; documented in HANDOFF §4).
- The implementation of Task 16's module preceded its unit tests (the plan gives both); mutation checks
  stand in for the red step (one surviving mutant led to an added test).

## Docker

Started only `dps-db source-pg oracle mongo` of this lane's compose project; Oracle recreated once
(`rm -sfv oracle`, no volume) for the widened column. `docker compose --profile oracle --profile
sources down` at the end (this project only).
