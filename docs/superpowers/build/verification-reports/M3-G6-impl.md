# M3 G6 — Tasks 20, 21 (implementation report)

Lane `dps-wt-m3`, branch `m3`. Author NilenduGanguli <nilendu.ganguli@gmail.com>, no trailers.

## Commits

- `717df9a` test: M3 log scan — allow-listed fields, no exception text, runtime sentinels (Task 20;
  includes the `dps/observability.py` driver-logger cap and its unit test)
- `ba60834` docs: M3 handoff, API reference, smoke and benchmark for Oracle and MongoDB (Task 21)

## Task 20 — `tests/test_no_values_in_logs_m3.py`

- Static: every `logger.<level>` call in `oracle.py`, `oracle_sql.py`, `mongo.py`, `mongo_docs.py`,
  `docpaths.py`, `consistency.py`, `tools/seed_oracle.py`, `tools/seed_mongo.py`, `tools/seed_model.py`
  has a literal snake_case event, one positional arg, no splat, allow-listed fields only; no
  exception turned into text (AST: `str`/`repr`/`format`/f-string of an exception name,
  `.message`/`.errmsg`; `.details` only in `mongo.py`); `ConnectorError` codes literal or the
  `code` parameter; no `print` in service modules. Self-tests prove the scanner and the capture.
- Dynamic (`integration`, `oracle`, `mongo`): (a) Oracle login with the sentinel password →
  `connection_failed`; (b) `CHAR(16)`-keyed table of sentinel keys/holders/CLOB notes: count,
  sample, keyset pages of 7, in-place write of 15 rows, stale ledger mark → `ledger_conflict`
  (replay wrote nothing — counted 15); (c) MongoDB duplicate-key write of `SENTINEL-DUP` →
  `pk_collision`; (d) consistency checks over fakes with `SENTINEL-TOKEN-*` (passed 3/3; 10 % drift
  → failed; failing lookup → RuntimeError). Nothing sentinel-shaped in structlog stdout/stderr/stdlib
  root output, nor in any raised error's str/repr/args/cause.
- **Real finding fixed (privacy):** pymongo's `pymongo.command` DEBUG logger wrote whole documents
  of every write and the E11000 reply (`dup key: { customerId: "…" }`) through the stdlib root
  logger: with `DPS_LOG_LEVEL=DEBUG` the app/worker would have logged customer values.
  `_THIRD_PARTY_LEVELS` now caps `pymongo`, `botocore`, `boto3`, `s3transfer`, `urllib3` at
  WARNING (test-first in `tests/test_observability.py`; the sentinel test fails with the pymongo
  cap removed and passes with it — demonstrated).

## Task 21

- `tools/smoke_test.py --m3`: 7 checks (`m3 connections`, `m3 discover`, `m3 group`, `m3 runs`,
  `m3 consistency 3/3`, `m3 evidence`, `m3 raw pii to llm`), counts only, key dropped after the
  submissions; pure helpers (`parse_args`, `M3_MEMBERS`, `m3_run_spec`, `consistency_verdict`)
  unit-tested in `tests/test_tools_m3.py`.
- `tools/bench_connector.py --engine postgres|oracle|mongo` (table writes; seeded demo source;
  `gdpr-pseudonymise`; result `m3-<date>-<engine>-…json`); `tools/m2_api.py` gained
  `M3_CONNECTIONS` (shared with smoke/bench; pinned equal to the console harness) and
  `discover_and_accept(owner: str | None, policy=)`.
- Docs: new `docs/technical/api-m3.md`; HANDOFF banner, §1 tools row, §3 throughput pointer, §4
  smoke env, §9, new §12 (run, Oracle rules, Mongo rules, FPE, logs, traps, throughput, source vs
  target, gates); README (status, summary, API table, M3 run block, markers, gates); contract: M3
  codes folded into the "Further M2 validation codes" paragraph; M3-DEVIATIONS Task 20 + Task 21.
- Screenshots `docs/superpowers/build/m3-shots/` (60 PNGs, ~30 MB, from G5) committed per plan.

## Evidence

- Stack: `docker compose --profile oracle --profile sources up --build -d` → app, worker, dps-db,
  source-pg, minio, oracle, mongo healthy; migrate, minio-init, demo-secrets, seed-sources exited 0;
  `/readyz` ready, db and jobs ok.
- Seed: `seed_sources.py --target all --rows 20000 --seed 7` → PG already seeded (20000 customers),
  CRM.CUSTOMERS 20000, INTERACTIONS 40000, KYC_DOCUMENTS 20000, ADDRESS_HISTORY 40000,
  CUSTOMER_EVENTS 40000, LOANS.APPLICANTS 5000, crm.profiles 20000; second run all `unchanged`.
- Smoke `--m3`: **all 39 checks passed, 2 skipped (OCR, no docs profile)**; m3: 0 created/4 reused,
  6/6 tested ok; 3/3 snapshots; group key in create answer only; 3/3 runs succeeded 20000/20000/20000;
  consistency 3/3 pairs, 500 sampled, min coverage 1; 3/3 packs verified under the group kid;
  raw PII to LLM 0. Log: `~/.cache/dps-build/reports/m3-g6-smoke.log`.
- Bench (1 worker, 4 partitions × 5000, batch 1000, FPE on): Oracle 941.6 rows/s (21.24 s), MongoDB
  860.3 rows/s (23.25 s), PostgreSQL same size 2474.0 rows/s (8.08 s). Log
  `~/.cache/dps-build/reports/m3-g6-bench.log`; JSON in `docs/technical/bench/`.
- Source vs target (counts only): CRM_MASKED.CUSTOMERS_SMOKE/_BENCH, crm_uat.profiles_smoke/_bench,
  bank_uat.customers_smoke: 0 target values equal to any source value for every name, national id,
  tax id, e-mail, mobile, address line, PIN, IBAN and notes field (0/20000; tax id 0/11989). Kept
  labels: segment, id_type, kyc_status, address type, tags, CITY (preset keeps it). Mongo
  customerId 8/7 of 20000 masked ids equal another customer's id (expected FF1 collisions ≈ 4.4).
- Log scan of the stack: 149 484 seeded Oracle/Mongo values (≥ 6 chars) vs `docker compose logs app
  worker`: 0 found.

## Gates (lane stack up, before the Task 21 commit)

- `./lane.sh ruff format dps tests tools` 303 unchanged · `ruff check` all passed · `mypy dps` 109 files OK
- `./lane.sh pytest -q` **2086 passed, 8 skipped** (7 identified: OCR mock not started ×3+, no host
  tesseract, `ner` extra not installed) in 7:04. Before Task 21 (only Oracle+Mongo up): 1881 passed,
  205 skipped.
- `DPS_TEST_REQUIRE_SERVICES=1 pytest -q -m "oracle and mongo" tests/test_no_values_in_logs_m3.py` 1 passed;
  required-mode run of the touched test files: 49 passed.
- No frontend changes (npm gate not applicable). Stack brought down after the group
  (`docker compose --profile oracle --profile sources down`).

## Deviations (all in docs/superpowers/build/M3-DEVIATIONS.md, Tasks 20 and 21)

- AST-based exception-text check (the plan's regex matched a docstring); `ConnectorError` code must be
  a literal or the `code` parameter; plan's "call_failed event logged" not asserted (those paths map
  without a log line) — `consistency_checked` asserted instead; `tools/seed_model.py` added to scope.
- Observability cap added for pymongo/botocore/boto3/s3transfer/urllib3 (M0 touch point, test-first).
- Real secret refs `dps/...` instead of the plan's `local/...`; connection specs shared via `tools/m2_api.py`.
- Benchmark via the API (like M2), seeded demo source, `gdpr-pseudonymise`, 4 partitions × 5000; extra PG run
  at the same size committed for comparison.
- **Tag `v0.4.0-m3` NOT created** (programme plan §5 rule 5 and this job's instructions); left to the
  lane verification / release step.
- 60 screenshots (~30 MB) committed per plan; flagged as a repo-size question for integration.

## Open issues

- `fallback:fallback:fpe_domain_too_small` doubled prefix visible in values_by_method — fixed on main
  (`9d19f7b`), resolves at integration.
- Lane verification still to run the full M3 DoD (DoD 4 `-m "oracle or mongo"` gate, DoD 5 conformance,
  DoD 6 M2 gate, DoD 8 browser check — screenshots exist from G5), plus the per-column source≠target
  check required by programme plan §2.2 Verify.
- `tools/smoke_test.py` keeps two pre-existing mypy errors (tools are not in the `mypy dps` gate).
- CITY is kept by the `gdpr-pseudonymise` preset (quasi-identifier) — a policy question for the owner.
