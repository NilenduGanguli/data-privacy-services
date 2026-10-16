# M3 G6 fix round 1 — sqlglot logger leak (Tasks 20, 21)

Branch `m3`, worktree `/Users/neelu/document_intelligence/dps-wt-m3`. Review: `M3-G6-review-r1.md`.
Commit: `392c1fe fix(m3): cap the sqlglot logger so a refused selection never logs its literal`
(author NilenduGanguli <nilendu.ganguli@gmail.com>, no trailers).

## Finding: sqlglot logger not capped (values-in-logs leak)

**Root cause.** `dps/layers/connectors/selection.py::_parse` hands user SQL to `sqlglot.parse`. For a
statement such as `EXPLAIN PLAN FOR SELECT … WHERE n = '<lit>'`, sqlglot's `parser._warn_unsupported`
logs the full SQL text, literal included, at WARNING through the stdlib logger `"sqlglot"`. Its
`parser.check_errors` also logs `str(error)` (which quotes SQL) at ERROR under the WARN error level.
Stdlib output goes raw to stderr and bypasses `_drop_value_fields`. Every sqlglot module uses the one
logger name `"sqlglot"`, and sqlglot never logs at CRITICAL, so capping it there silences it fully.
The app, worker and migrate entrypoints all call `configure_logging`, which applies the cap.

**Test first (all failed before the fix):**
- `tests/test_observability.py::test_third_party_loggers_are_capped` now asserts that `sqlglot` is at
  CRITICAL and is not enabled for ERROR. It failed before the fix.
- New offline test: `tests/test_no_values_in_logs_m3.py::test_a_refused_selection_never_logs_its_literal[oracle|postgres]`.
  - It runs `validate_selection(Selection(query="EXPLAIN PLAN FOR SELECT * FROM t WHERE n = 'SENTINEL-SEL'"), engine, None)`
    under the production JSON/DEBUG config, using the existing `json_logs` fixture (structlog stdout and stderr plus a stdlib root handler).
  - It asserts that the error code is `selection_not_select` and that no sentinel appears in the captured text or in the error's str, repr, args or cause.
  - Both cases failed before the fix: pytest showed the sqlglot WARNING line quoting `SENTINEL-SEL`.
  - All three tests pass after the fix.

**Fix.** `dps/observability.py` adds `("sqlglot", logging.CRITICAL)` to `_THIRD_PARTY_LEVELS`, with a
comment on why. No contract names changed.

**Docs.**
- `docs/superpowers/build/M3-DEVIATIONS.md`: a new bullet at the end of Task 20 covers the cause, the test-first evidence, the live re-check and the gates.
- `HANDOFF.md` §12 **Logs** now covers the sqlglot cap.

## Live re-check (rebuilt lane image)
- Stack: `docker compose --profile oracle --profile sources up --build -d`, then `tools/seed_sources.py --target all --rows 20000 --seed 7`, then `tools/smoke_test.py --m3`.
- Smoke: 39 passed, 2 skipped (OCR not ready, because the docs profile was not started).
- Request: `POST /api/v1/connections/{id}/count` with `selection.query = "EXPLAIN … WHERE … = 'SENTINEL-LIVE-…'"`, sent to `smoke-ora-src` (object `CRM.CUSTOMERS`) and to the PG source `bank-restore-rw` (`public.customers`).
- Both returned 422 `selection_not_select`, with no sentinel in the response bodies.
- Log scan: `docker compose logs --since <t> app` had 0 sentinel lines. The window held 2 app lines, both `audit action=connection.count`, plus one `ocr_probe_failed`. The worker had 0 sentinel lines.
- Before the fix the reviewer saw 4 sentinel lines.
- Stack torn down afterwards with `docker compose --profile oracle --profile sources down`; 0 containers left.

## Gates (exact)
- `./lane.sh ruff format dps tests tools`: 303 files left unchanged.
- `./lane.sh ruff check dps tests tools`: all checks passed.
- `./lane.sh mypy dps`: no issues in 109 source files.
- `./lane.sh pytest -q` (lane stack up: app, worker, dps-db, source-pg, minio, oracle, mongo): **2088 passed, 8 skipped**, 1 warning, 431 s. A `-m "not integration" -rs` re-run gives the reasons for 7 of the 8 skips (the 8th is an integration skip, not inspected):
  - the OCR mock (`--profile docs` not started), 3 tests;
  - the tesseract binary, not on the host, 2 tests;
  - the `[ner]` extra: `presidio_analyzer` and a spaCy model are not installed, 2 tests;
  - none of these touches the changed code.
- `DPS_TEST_REQUIRE_SERVICES=1 ./lane.sh pytest -q tests/test_no_values_in_logs_m3.py tests/test_observability.py`: 38 passed. This includes the Oracle + Mongo runtime sentinel test.
- Frontend: not touched, so it was not run.

## Open issues / notes
- **Stray working-tree changes, not mine to commit.** The full `pytest -q` run includes the console browser tests, which rewrote 54 tracked screenshots under `docs/superpowers/build/m3-shots/*.png`. They show as modified in `git status`. I did not commit them or revert them (the operational rules forbid `checkout --`). Either commit them as refreshed evidence or restore them before merging.
- **`main` has the same gap.** The PostgreSQL half of the leak predates M3, and `main` has no sqlglot cap either. The fix reaches `main` when `m3` merges; any lane that branches from `main` before then still has the leak.
