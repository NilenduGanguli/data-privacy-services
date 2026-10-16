# M3 G6 review, round 2 (Tasks 20 and 21)

Reviewer: independent, lane `dps-wt-m3`, branch `m3`, HEAD `392c1fe` (round-1 fix on top of T20 `717df9a`
and T21 `ba60834`). Probes: `~/.cache/dps-build/reports/probe-m3-G6/r2/`. No repository file was edited by
me (see "Working tree" below).

## Verdict: PASS (0 blocking)

Round 1's only blocker (B1: sqlglot logs a refused selection's literal on stderr) is fixed, tested
and verified live. All gates are green. I found no new leak, broken interface or plan contradiction.

## B1 from round 1: verified fixed

- The fix (`dps/observability.py`) adds `("sqlglot", logging.CRITICAL)` to `_THIRD_PARTY_LEVELS`.
- I grepped sqlglot 30.22.0 for loggers. Every module uses the single logger name `"sqlglot"`
  (`__init__`, helper, lineage, parser, generator, dialect and optimizer/*). None of them calls
  `logger.critical`, `fatal` or `exception`, so the CRITICAL cap silences sqlglot completely, and no
  child logger escapes it.
- Mutation check: I wrote a probe plugin (`r2/uncap_sqlglot.py`) that removes only the sqlglot cap.
  With it, `test_a_refused_selection_never_logs_its_literal[oracle]`, `[postgres]` and
  `test_third_party_loggers_are_capped` all **fail**. Without it, they all pass. The new tests are
  therefore sensitive to the defect.
- Offline sweep (`r2/sel_sweep.py`) under the production DEBUG/JSON config plus a root handler. It
  covers 28 Oracle/PG `query` texts and 9 `where` texts with sentinel literals. Those include
  EXPLAIN, CALL/EXEC, GRANT, MERGE/INSERT, PL/SQL blocks, unterminated strings, `q'[...]'`,
  `N'...'`, `$$...$$`, MODEL, PIVOT, MATCH_RECOGNIZE, FOR UPDATE, INTO, denied functions, `?` binds
  and garbage tokens, plus 16 MongoDB filters (`r2/mongo_sweep.py`).
  - Result: **0 sentinels** in the captured logs.
  - Result: **0** in any raised error's `str`, `repr`, `args` or `detail`.
  - The only place a sentinel survives is the *suppressed* `__context__` of a
    `SelectionError(... ) from None`, which holds sqlglot's ParseError/TokenError (see N8).
- Live check on a lane stack rebuilt from HEAD (`r2/api_live.py`). I sent 9 Oracle/PG selections
  with sentinel literals to `POST /connections/{id}/count` against `smoke-ora-src` and `bank-pg-ro`:
  - refused ones returned 422 `selection_not_select` or `selection_parse_error`;
  - accepted ones returned 200;
  - I also sent 4 MongoDB filters to `smoke-mdb-src`.
  - No response body contained a sentinel. `docker compose logs app worker` held **0 sentinel lines**
    in the window, and 0 in the whole stack log. `/metrics` held 0.

## Gates I ran (all green)

| Gate | Result |
|---|---|
| `./lane.sh ruff format --check dps tests tools` | 303 files already formatted |
| `./lane.sh ruff check dps tests tools` | All checks passed |
| `./lane.sh mypy dps` | no issues in 109 source files |
| `./lane.sh pytest -q -rs` (stack `--profile oracle --profile sources` up, `--build` from HEAD, seeded 20 000) | **2088 passed, 8 skipped**, 430 s (`r2/pytest-full.log`). The skips are the OCR mock (docs profile not started) ×3, the tesseract binary ×2, `presidio_analyzer` / the spaCy `[ner]` extra ×2, and Mongo's "no composite keys" conformance case ×1. None of them touches G6 |
| `DPS_TEST_REQUIRE_SERVICES=1 pytest -q tests/test_no_values_in_logs_m3.py tests/test_tools_m3.py tests/test_observability.py` | 46 passed |
| `tools/seed_sources.py --target all --rows 20000 --seed 7` | ok |
| `tools/smoke_test.py --m3` (after reseeding to 20 000) | **all 39 checks passed, 2 skipped (OCR)**. M3 results: 6/6 connections tested ok; 3/3 snapshots; 3/3 runs at 20000/20000/20000; consistency 3/3 pairs, 500 sampled, min coverage 1; 3/3 packs verified under the group's kid; `dps_llm_raw_pii_sent_total` 0 (`r2/smoke2.log`) |
| `bench_connector.py --engine oracle --rows 20000 --parallelism 4 --partition-rows 5000` | 942.4 rows/s, 21.2 s, succeeded |
| `bench_connector.py --engine mongo` (same arguments) | 860.5 rows/s, 23.2 s, succeeded. Both match the HANDOFF figures |
| Frontend | not changed in G6 or in the fix, so not run |

The stack was brought down afterwards (`docker compose --profile oracle --profile sources down`;
0 containers left).

## Source vs target (counts only; round-1 probe `rowwise.py` re-run → `r2/rowwise.out`)

| Engine | Target | Joined on | Rows joined | Survivors (0 in each) | Kept as per the preset |
|---|---|---|---|---|---|
| Oracle | `CUSTOMERS_SMOKE` | timestamps | 20000 | ADDR_LINE1, CUSTOMER_ID, DOB, EMAIL, FULL_NAME, MOBILE, NATIONAL_ID, NOTES, PIN_CODE, TAX_ID | CITY, COUNTRY, SEGMENT, RISK_SCORE and the timestamps |
| MongoDB | `profiles_smoke` | `_id` | 20000 | iban, address line1/pin, email, mobile, customerId, kyc.taxId, notes, dob, fullName | — |
| PG | `customers_smoke` | timestamps | 20000 | customer_id, date_of_birth, email, full_name, mobile, national_id, notes, tax_id | — |

## Log scan of the whole stack

- `r2/stack-logs.txt` is the output of `docker compose logs app worker` after pytest, smoke, bench and
  the probes.
- I compared it against 841 523 seeded source and target values of 6 or more characters (Oracle CRM,
  LOANS and CRM_MASKED; Mongo crm and crm_uat; PG).
- There were 7 hits. All 7 are 6-digit numeric substrings inside log timestamps or trace ids, so they
  are false positives (contexts in the probe output). There is no real value in the logs.

## Plan, contract and programme-plan conformance

- **The fix commit:**
  - It touches only `dps/observability.py`, the two tests, M3-DEVIATIONS (Task 20 bullet) and
    HANDOFF §12 "Logs". The text of both documents is accurate.
  - No contract name changed.
  - The author is NilenduGanguli <nilendu.ganguli@gmail.com>, with no trailers.
- **`api-m3.md`:** its statement "no literal reaches the statement or a log" now holds. I verified
  this both offline and live.
- **Programme plan §2.2 G6:** the acceptance is "log-scan green; smoke + bench; HANDOFF/README/API
  reference updated". It is **met**.
- **Round 1 conformance findings:** I re-checked the static scan scope (9 M3 modules) and the
  dynamic sentinel run, and they still hold.

## Deviation log (Tasks 20 and 21)

| Entry | Judgement |
|---|---|
| Round-1 judgements of every T20 and T21 entry | Unchanged; see `M3-G6-review-r1.md` |
| Driver-logger caps | Now complete for the known value-quoting libraries. sqlglot is added, and oracledb has no stdlib logger |
| New T20 bullet "Review G6 round 1 fix" | **Accepted.** The cause, the test-first evidence, the live re-check and the gates are stated correctly. I reproduced the gates: 2088 passed, 8 skipped, 39/2 smoke |

## Non-blocking findings

- **N1 (open from round 1): the test gate still rewrites the tracked screenshots.**
  - At the start of this review the tree already had 54 modified
    `docs/superpowers/build/m3-shots/*.png` files, left by the fixer's gate run. My `pytest -q` run
    rewrote them again.
  - I left them as found and did not restore them, because the rules forbid `checkout --` and I was
    told not to modify repository files.
  - `git status` therefore shows those 54 PNGs modified and nothing else.
  - Before merging, someone must either restore them from HEAD or decide to commit them. The lasting
    fix is to write shots to an ignored `DPS_SHOTS_DIR`.
- **N2 (open): `bench_connector.py` still defaults to `--rows 200000`** for `--engine oracle|mongo`.
- **N3 (open): smoke still sends `cursor=None`** as a query parameter.
- **N4 (open): the HANDOFF §12 collision counts are run-dependent.**
- **N5, N6, N7 (open):** the Mongo evidence table names columns by top-level field; the app logs
  with the console renderer (ANSI), not JSON; CITY is kept by the preset (a policy question).
- **N8 (new):** `SelectionError` is raised `from None`, so `__cause__` is empty and tracebacks hide
  the context. However, `__context__` still holds sqlglot's ParseError or TokenError, whose message
  quotes the SQL, literal included.
  - Nothing in DPS walks `__context__`: structlog drops `exc_info`, and `_ValueFreeErrors` returns a
    value-free 500.
  - It would only matter if an error reporter that walks chained exceptions (for example Sentry) were
    added later.
  - Optional hardening: raise from a helper outside the `except` block, or set
    `err.__context__ = None`.
- **N9 (new): test pollution of the demo space.** The full `pytest -q` run reseeds the Oracle and
  MongoDB demo space to 200 rows (the e2e and console tests).
  - A smoke run straight after the gate therefore reports runs of `20000/200/200` and 200 sampled.
    It still passes, but it is not the documented 20 000 / 500 scenario.
  - HANDOFF could say "reseed with `seed_sources.py --rows 20000` before smoke if pytest ran last".
- **N10 (new, wording/status): MongoDB connector input errors are reported as 502.** A connector
  `object_invalid`, `database_required` or `path_unsupported` maps to 502 through
  `errors._connector_status` ("any other connector code").
  - Example: `count` on a Mongo object given with an `owner` → `502 object_invalid`.
  - The route-level `object_invalid` is 422 (api-m2).
  - This follows contract §9's "502 any other connector code", so it is not a contract break.
  - Still, a client mistake reported as a gateway error invites retries. Consider mapping these codes
    to 422 at the next contract revision.

## Working tree

HEAD `392c1fe`. The only changes are the 54 test-regenerated screenshots (N1). They were already
dirty when I started; I made no other change.
