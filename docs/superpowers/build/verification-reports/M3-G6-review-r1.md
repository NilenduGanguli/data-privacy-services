# M3 G6 review, round 1 (Tasks 20 and 21)

Reviewer: independent, lane `dps-wt-m3`, branch `m3`, HEAD `ba60834` (T20 `717df9a`, T21 `ba60834`).
Probes: `~/.cache/dps-build/reports/probe-m3-G6/`. No repository file modified (see "Side effect" below).

## Verdict: FAIL (1 blocking: values reach the app log through sqlglot)

## Gates I ran

| Gate | Result |
|---|---|
| `./lane.sh ruff format --check dps tests tools` | 303 files already formatted |
| `./lane.sh ruff check dps tests tools` | all checks passed |
| `./lane.sh mypy dps` | no issues in 109 source files |
| `./lane.sh pytest -q` (lane stack `--profile oracle --profile sources` up, built from HEAD) | **2086 passed, 8 skipped** in 8:13 |
| `DPS_TEST_REQUIRE_SERVICES=1 pytest -q tests/test_no_values_in_logs_m3.py tests/test_tools_m3.py tests/test_observability.py` | 44 passed |
| Mutation: the sentinel test with the driver caps removed (probe plugin `uncap_plugin.py`) | **fails** "a sentinel value reached the logs": the test detects the pymongo leak, as claimed |
| `seed_sources.py --target all --rows 20000 --seed 7` twice | counts; second run `unchanged` on Oracle and MongoDB |
| `python tools/smoke_test.py --m3` | **all 39 checks passed, 2 skipped (OCR)**; m3: 6/6 tested, 3/3 snapshots, 3/3 runs 20000 each, consistency 3/3, 500 sampled, min coverage 1; 3/3 packs under the group's kid; raw PII to LLM 0 |
| `bench_connector.py --engine oracle / mongo --rows 20000 --parallelism 4 --partition-rows 5000 --out <probe>` | Oracle 862.7 rows/s (23.2 s), MongoDB 827.4 rows/s (24.2 s), both succeeded. This matches the HANDOFF figures (942 / 860) within run-to-run noise |
| No frontend change in G6 | npm gate n/a |

Stack brought down afterwards (`docker compose --profile oracle --profile sources down`).

## Source vs target (counts only; probes `src_vs_tgt.py`, `rowwise.py`)

Row-wise survivors are target values equal to the same record's source value. I joined on kept
timestamps (Oracle, PG) and on `_id` (MongoDB). All 20000 rows joined.
- Oracle `CRM.CUSTOMERS → CRM_MASKED.CUSTOMERS_SMOKE`: 0 survivors in each of ADDR_LINE1,
  CUSTOMER_ID, DOB, EMAIL, FULL_NAME, MOBILE, NATIONAL_ID, NOTES (CLOB), PIN_CODE and TAX_ID. CITY,
  COUNTRY, SEGMENT, RISK_SCORE, CREATED_AT and LAST_CONTACT_AT are kept, which matches the preset.
- MongoDB `crm.profiles → crm_uat.profiles_smoke`: 0 survivors in `accounts[*].iban`,
  `contact.addresses[*].line1/.pin`, `contact.email`, `contact.mobile`, `customerId`,
  `kyc.taxId`, `notes`, `profile.dob` and `profile.fullName`. `_id`, balances, address type,
  `kyc.verifiedAt`, `riskScore`, `segment` and `tags` are kept.
- PG `bank.customers → bank_uat.customers_smoke`: 0 survivors in customer_id, date_of_birth,
  email, full_name, mobile, national_id, notes and tax_id.
- Set overlap (any source value): customer ids 3 per engine, which is FF1 landing on another
  customer's id. Dates overlap at about 12.5k, which is expected for date_shift within the domain.
  Masking claims hold.

## Log scans

- Stack log scan: I compared 506 394 seeded Oracle (CRM, LOANS, CRM_MASKED), MongoDB (crm, crm_uat)
  and PG values of 6 or more characters against `docker compose logs app worker`. There was 1 hit,
  and it is a false positive: a 6-digit microsecond fraction of a log timestamp.
- `/metrics`: no sentinel; the M3 counters' labels are engine, op and outcome only.

## Blocking

### B1. A refused selection's literal is written raw to the app log by sqlglot (Oracle and PostgreSQL)

`dps/observability.py` `_THIRD_PARTY_LEVELS` caps pymongo, botocore and the others, but not
`sqlglot`. `selection._parse` calls `sqlglot.parse(sql, read=…)`. For a statement that sqlglot
cannot parse natively, sqlglot logs the full SQL text at WARNING through stdlib logging:
`parser._warn_unsupported`, message `'<sql>' contains unsupported syntax. Falling back to parsing
as a 'Command'.` DPS only refuses the statement afterwards (`selection_not_select`).
`configure_logging` sends stdlib records raw to stderr (`basicConfig(format="%(message)s")`), so
they bypass `_drop_value_fields`.

- Offline (`probe-m3-G6/sqlglot_probe.py`): six of eight refused Oracle and PG `query` texts put
  their literal into the stdlib log, for example `EXPLAIN PLAN FOR … WHERE n = '<lit>'`,
  `ALTER SESSION …`, `CALL p('<lit>')`, `EXEC p('<lit>')` and `GRANT … IDENTIFIED BY '<lit>'`.
- Through the running lane API (`probe-m3-G6/api_sqlglot.py`):
  `POST /connections/{smoke-ora-src}/count` with `{"query": "EXPLAIN PLAN FOR SELECT * FROM
  CRM.CUSTOMERS WHERE FULL_NAME = 'SENTINEL-API-ORA'"}` returned 422 `selection_not_select`. The
  PG equivalent behaved the same. `docker compose logs app` then held 4 lines quoting
  `SENTINEL-API-ORA` and `SENTINEL-API-PG` verbatim.
- The literal in a selection is typically a customer value: a name, an id or a national id to
  filter on. This contradicts spec §10 and contract §0.1 ("no values in logs"). It also
  contradicts the new `docs/technical/api-m3.md`: "every literal in the text is lifted into a
  generated bind, so no literal reaches the statement or a log". Task 20's sentinel run did not
  exercise the Oracle selection path, so the log scan missed it. The PG half predates this lane
  (main has no `sqlglot` cap either), but Task 20 is where the third-party cap was extended, and
  the M3 Oracle selection path is affected.
- **Fix (test-first):**
  1. Add `("sqlglot", logging.CRITICAL)` to `_THIRD_PARTY_LEVELS`. CRITICAL rather than ERROR,
     because `parser.check_errors` logs `str(error)`, which quotes the SQL, at ERROR when an
     error level of WARN is used.
  2. Assert the cap in `tests/test_observability.py::test_third_party_loggers_are_capped`.
  3. Add an offline sentinel case to `tests/test_no_values_in_logs_m3.py`:
     `validate_selection(Selection(query="EXPLAIN PLAN FOR SELECT * FROM t WHERE n = 'SENTINEL-SEL'"), engine, None)`
     for `oracle` and `postgres`. It should raise `selection_not_select` with nothing
     sentinel-shaped in the captured stdlib or structlog output or in the error. Demonstrate that
     it fails without the cap.
  4. Note the fix in M3-DEVIATIONS (Task 20) and in the HANDOFF §12 "Logs" paragraph.

## Non-blocking findings

- **N1. The test gate rewrites tracked files.** `pytest -q` with the stack up runs
  `tests/console/test_console_m3_{groups,wizard}.py`, which overwrite the 54 committed PNGs in
  `docs/superpowers/build/m3-shots/`. My gate run left 54 modified tracked files, which I restored
  byte-for-byte from HEAD (`git show HEAD:<path> > <path>`; list in
  `probe-m3-G6/dirtied-shots.txt`). With roughly 30 MB of committed screenshots, every gate run
  dirties the tree, and a careless `git add` re-commits churn. This is the same class as main's
  A-N2. Suggestion: write shots to `DPS_SHOTS_DIR` (default `var/shots`, git-ignored) and copy the
  shots by hand when they should be refreshed. Alternatively, ignore the folder and keep the shots
  as an artefact.
- **N2.** `bench_connector.py --engine oracle|mongo` keeps the PG default `--rows 200000`. Without
  `--rows` it reseeds the demo customer space to 200 000 on one engine only. That breaks the smoke
  consistency check's equal-seed assumption (trap 6). Suggest defaulting to 20 000 for M3 engines,
  or refusing a size that differs from the current seed unless a flag allows it.
- **N3.** `tools/smoke_test.py` `ensure_m3_connections` sends `cursor=None` as a query param, which
  httpx encodes as an empty value. The server accepts it today; it is cleaner to omit the param.
- **N4.** HANDOFF §12 says "8 (smoke) and 7 (bench)" for `customerId` collisions; I measured 3 per
  engine on a fresh seed. The figure is run-dependent, so state it as "a handful (≈ 4 expected)".
- **N5 (G5 carry-over, observed in a committed shot).** In
  `06-wizard-mongo-evidence-desktop.png`, the evidence pack's classification table and its "kept"
  line name MongoDB columns by top-level field (`contact` ×4, `profile` ×2; "kept: …accounts,
  contact, kyc…") rather than by leaf path. The table reads as if `accounts` were both transformed
  and kept. This is not a leak, but the evidence wording is misleading, so it belongs in the lane
  verification or polish.
- **N6.** The app container logs with the console renderer (ANSI colours), not JSON. The allow-list
  processor still runs; this is only a note for log shipping.
- **N7.** `CITY` is kept by `gdpr-pseudonymise` (a quasi-identifier). The implementer already
  flagged it as a policy question for the owner.

## Plan, contract and programme-plan conformance

- Task 20 has the static AST scan (allow-listed fields, literal events, no positional args or
  splats, no exception text, literal `ConnectorError` codes, no print in services) and a dynamic
  sentinel run over (a) to (d), plus self-tests for the scanner and the capture. Both behave as
  claimed: the scanner self-test passes and the mutation probe proves sensitivity. The test files
  and M3 modules cover every M3 logger. M2's scan also globs `connectors/*.py`.
- Task 21 has the smoke `--m3` checks (7, names and counts only), the bench `--engine`, HANDOFF §12
  (services, ports, refs, Oracle and Mongo rules, FPE, traps, throughput, source vs target),
  README, `api-m3.md` and additive contract codes. I verified `api-m3.md` against the code: the
  Mongo operator allow-list, the allowed options, `selection_query_needs_key` (`oracle.py:596`),
  the request bounds (`sample` 10–10 000, default 1000; `min_coverage` in (0,1]; `pairs` 2–8) and
  the 503/502/504 mapping. Apart from B1's sentence about logs, the docs are accurate.
- Programme plan §2.2 G6 ("log-scan green; smoke + bench; HANDOFF/README/API reference updated"):
  met, except that the log scan misses B1.

## Deviation log (M3-DEVIATIONS Tasks 20, 21)

| Entry | Judgement |
|---|---|
| Driver-logger caps (pymongo, botocore, boto3, s3transfer, urllib3) | Correct and verified by the mutation probe; **incomplete** (sqlglot, B1) |
| AST exception-text check instead of the regex | Accepted; stricter and avoids the docstring false positive |
| `ConnectorError` code literal or the `code` parameter | Accepted (stricter than the plan) |
| No "call_failed event logged" assertion | Accepted; the refused paths map without a log line, and `consistency_checked` is asserted |
| `tools/seed_model.py` added to scope | Accepted |
| Secret refs `dps/...` and names shared via `tools/m2_api.py` | Accepted (matches compose as built; console harness pinned equal) |
| Smoke order PG, Mongo, Oracle (Oracle anchors); `bank-pg-ro`/`bank-uat` instead of `smoke-pg` | Accepted |
| Bench through the API, `gdpr-pseudonymise`, 4 × 5000, extra PG comparison file | Accepted (figure reproduced) |
| Tag `v0.4.0-m3` not created | Correct per programme plan §5 rule 5 and the job rules |
| 60 screenshots (~30 MB) committed | Per plan, but see N1 (the gate rewrites them) |
| `fallback:fallback:` label | Pre-existing; fixed on main `9d19f7b`; resolves at integration |

## Side effect of this review

`pytest -q` overwrote 54 tracked screenshots (N1). I restored them to HEAD content; `git status`
is clean. I made no other repository change.
