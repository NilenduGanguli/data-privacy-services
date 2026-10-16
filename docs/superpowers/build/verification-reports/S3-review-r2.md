# S3-review-r2 — `fsec`: the round-1 fixes (B1–B3) and the stream as a whole

Reviewer: independent S3 reviewer, round 2. Date 2026-10-15. Worktree
`/Users/neelu/document_intelligence/dps-wt-fsec`, branch `fsec`, head **`5cfb597`** (base main `64c46e3`,
21 commits; round 1 = `be2b326`, `e7882af`, `5cfb597` on top of `4d2b234`). No repository file was
modified. Logs and probes: `/Users/neelu/.cache/dps-build/reports/S3-review-r2/` (probe outputs print
status codes, error codes, counts and timings only; every text and name is synthetic).

## Verdict: **FAIL** — 2 blocking findings, both in the round-1 B1 fix (the per-call pattern budget)

- **R2-B1 (HIGH, regression).** Under ordinary concurrent load, the pattern budget refuses *legitimate*
  requests with 422 `custom_pattern_too_slow`. Shown end to end on a real uvicorn server.
  - Cause 1: the `regex` module's `timeout` counts **process** CPU, i.e. all threads. The docs say it
    counts the calling thread's CPU.
  - Cause 2: with `concurrent=True`, any thread running Python starves the matcher.
  - Result: custom recognizers and deny lists (the bank's own term lists) fail intermittently. L2
    partitions and discovery fail with no retry.
- **R2-B2 (MEDIUM).** The budget still lets one `detect` key pin CPUs at about 250× the legitimate
  cost. The review doc's residual-risk text misstates this.
  - Measured: a 1 MB `/detect` with a 1000-pattern policy burned **1001.5 CPU-seconds** before the
    422. The same request with linear patterns takes 3.97 s.
  - Every small request with one stacked pattern burns about 1 s, and the rate limit allows 50 such
    requests per second per key.

B2 (F-01) and B3 (F-10) are fixed and proven: their tests fail on the base and pass on the head. All
gates are green. The glob half of B1 is fixed.

---

## 1. State and hygiene

- `git status` is clean.
- Commits: 21 since `64c46e3`. Author and committer are all `NilenduGanguli <nilendu.ganguli@gmail.com>`,
  with 0 `Co-Authored-By`/`Signed-off-by` trailers and conventional messages (`fix(fsec):`, `docs(fsec):`).
- `gitleaks git --log-opts=4d2b234..HEAD`: 3 commits scanned, no leaks.
- `frontend/` is untouched over the whole stream (0 files since `64c46e3`), so no frontend build was
  needed.
- Lane state afterwards: the reviewer started MinIO, MongoDB and Oracle for the integration run, then
  stopped them again (`compose stop`). `dps-db` and `source-pg` are up, as found.

## 2. Gates (run by the reviewer on `5cfb597`)

| Gate | Result |
|---|---|
| `ruff format --check dps tests tools` · `ruff check dps tests tools` · `mypy dps` | 403 files formatted · all checks passed · 137 source files, no issues (`lint.log`) |
| `pytest -m "not integration"` (lane DB up) | **2643 passed**, 10 skipped, 1 xfailed (strict M4 surface), 106 s (`pytest-unit.log`) |
| `DPS_TEST_REQUIRE_SERVICES=1 pytest tests/security tests/test_no_values_in_logs_m5.py tests/test_contract_surface.py tests/api tests/test_api_entities.py tests/db tests/test_custom.py tests/test_taxonomy*.py tests/test_ndjson.py tests/test_egress_tls.py tests/test_connection_endpoints.py` | **482 passed**, 1 xfailed (`pytest-security-db.log`) |
| `DPS_TEST_REQUIRE_SERVICES=1 pytest -m "integration and not browser and not ocr and not slow"` on the fsec stack (dps-db, source-pg, MinIO, MongoDB, Oracle Free) | **237 passed**, 1 skipped (`pytest-integration.log`) |
| `DPS_TEST_REQUIRE_SERVICES=1 pytest -m "slow and not browser and not ocr"` | **12 passed** (`pytest-slow.log`) |
| Round-1 tests fail before their fix | B2 test against base `4d2b234` `dps/` with the head's `tests/`: both parameters **FAIL** (the exploit PATCH answered `200`, `basecheck-b2.log`). B1/B3 behaviour, re-stated without the new imports (`probes/test_r2_basecheck.py`): on base, **4/4 fail** (B3 registry exhaustion: the victim gets 400; a tier leaks; the route probe gives no answer within 30 s; the glob runs past 10 s). On head, 4/4 pass (`basecheck-b1-b3.log`) |
| Round-1 reviewer probes on head | F-01 re-point: clone, re-point and restated `secret_ref` all give 403 and DELETE gives 403, in both holder cases (`probes/f01_repoint.r2.out`). Route ReDoS probe: 200 in ≤ 0.02 s on all three routes (`redos_api.r2.out`). 63-character glob: 0.000 s (`glob_redos.r2.out`) |

## 3. The round-1 findings, re-checked

- **B2 / F-01: fixed.**
  - `update_connection` and `delete_connection` (`dps/connections.py:470-475`, `:560-565`) load
    `production_refs` under the row lock and refuse any change or delete of a holder without `admin`.
  - The PATCH route answers the refusal before TLS/SSRF.
  - The test covers both ways a holder exists and all six PATCH shapes plus the DELETE. A counting
    listener receives 0 connections.
  - It fails on the base, because the exploit PATCH answered 200.
  - `secret_ref` aliasing is not possible: segments must match `^[a-z0-9][a-z0-9_.-]*$`, so there are
    no empty, `.` or `..` segments.
  - No job spec carries endpoint overrides.
- **B3 / F-10: fixed.**
  - The per-call `CustomEntities` is bound by `_CallScope` (pure ASGI, innermost; no
    `BaseHTTPMiddleware` in the stack) and by `Consumer._guard` (per handler task).
  - Context reaches the engine's threads, because `to_thread` and the threadpool copy it. There is no
    `run_in_executor` or `ThreadPoolExecutor` in `dps/`, and no websocket or background tasks.
  - `CustomTiers.ensure()` re-registers a policy's tiers in each call that reuses a detector or a
    cached transformer. Its identity check is safe: `_registered_in` is set only after every tier is
    registered.
  - Every protection decision that reads a custom tier compares `rank >= MEDIUM`, or `is LOW` for
    discovery and planner suggestions. A custom type never reads below MEDIUM, so reading MEDIUM
    instead of HIGH outside the declaring call changes no protection decision. What changes is
    display and record sensitivity.
  - The F-04 NER-yield check runs inside the scan, after `ensure()`.
  - The `test_api_entities` edit strengthens the test; it does not weaken it.
- **B1 / F-02, glob half: fixed.**
  - `PathGlob` matches without backtracking; the greedy leftmost algorithm is correct for `*`-only
    globs.
  - A differential test runs 6000 random cases.
- **B1 / F-02, pattern half: bounded, but see R2-B1 and R2-B2.**
  - The B1 tests prove the 400-character probe answers in under 1 s.
  - They exercise neither concurrency nor scale.

## 4. Blocking findings

### R2-B1 (HIGH) — legitimate pattern scans are refused under ordinary load (regression from round 1)

`dps/engine/detect/regex_guard.py:123-141` (`PatternBudget.run`; line 135) calls
`pattern.finditer(text, concurrent=True, timeout=left)` and books `time.thread_time()`. The docstring,
M5-DEVIATIONS and the commit all say the budget is the calling thread's CPU time. Two measured facts
contradict that:

1. **`regex`'s timeout counts process CPU, all threads included** (`probes/regex_timeout_semantics.out`).
   With 3 other GIL-free threads busy, a `timeout=1.0` run was cut at **0.257 s** wall and 0.25 s of its
   own CPU, since 4 × 0.25 s = 1 s.
2. **With `concurrent=True`, one thread running Python starves the matcher.** The matcher re-acquires
   the GIL to check signals and the timeout.
   - Same file: the matcher got 0.04 s of CPU in 0.97 s before being cut.
   - `probes/concurrent_flag.out`: a 200-name deny list on 100 KB with one busy Python thread gives
     `TimeoutError` at 1.108 s (own CPU 0.006 s) with `concurrent=True`. With `concurrent=False` it is
     `ok` in 0.017 s.

Effect on DPS's own matcher and routes:

| Probe | Alone | Under load |
|---|---|---|
| `concurrency_false_refusal.out`: 400 KB text, 1000-name deny list, each call in its own `call_scope` (allowance 1.40 s) | ok, 0.20 s | 4 concurrent calls: **4/4 refused**; 8: **8/8**; 12: **12/12** (own CPU 0.35 / 0.18 / 0.12 s) |
| `gil_busy_false_refusal.out`: 100 KB, 200 or 1000 terms; 50 KB, 1000 terms | ok, 0.01–0.05 s | one busy Python thread: **refused** after 1.06–1.11 s with 0.01 s own CPU (all 3 cases) |
| `e2e_false_refusal.out` (real `uvicorn dps.app:create_app`, env local, no DB): `/detect`, 100 KB text, 200-term deny list | 200 in 0.07 s (3/3) | while 4 ordinary `/detect` requests run (default preset, no custom pattern, 1 MB text, 0.63 s each alone): **422 `custom_pattern_too_slow` in 2 of 3 tries** |

Impact:

- **L1.** A tenant's custom recognizers and deny lists fail intermittently, depending on what other
  tenants send.
- **L2 partitions and discovery.** These fail terminally:
  - `PartitionState.failed` and `_fail(...)`, "deterministic, a retry would refuse again".
  - This happens on large text cells (documents, CLOBs) under worker concurrency.
- **Post-write sample.** A refused post-write sample becomes `{"error": "custom_pattern_too_slow"}`,
  and the verification still says `passed` (`verification_outcome` reads only `kept_column_hits`).
- **Cross-tenant.** Any tenant can cause these refusals in other tenants' requests just by sending
  heavy ordinary requests.
- **Docs and tests.** The docs' claims ("thread CPU", "a linear pattern runs one to two orders of
  magnitude under that rate") do not hold under load. No test covers concurrency.

**Fix:** stop enforcing a per-call CPU budget through `regex`'s process-wide timeout and the starving
`concurrent=True`. Options:

- (a) Run policy patterns in a small single-threaded subprocess pool, where process CPU is the task's
  own CPU (`RLIMIT_CPU` or a CPU timer per task).
- (b) Use a linear-time engine (`google-re2`) and refuse what it cannot run at validation. Deny lists
  need no regex at all: escaped literals plus a word-boundary check on a linear multi-string matcher.
- (c) At minimum, after a `TimeoutError`, compare the thread's own CPU with what is `left` and resume
  or retry instead of refusing. Also drop `concurrent=True`, or show that the matcher is not starved.

Whichever is chosen, add tests:
- N concurrent legitimate calls plus one busy Python thread all answer 200, through the real ASGI
  app.
- The F-02 attack is still refused.

Correct the docstring, M5-DEVIATIONS, HANDOFF §6 and review doc §3.2. Make a post-write sample that
could not run fail closed (`review_required`), as `identifier_check` already does.

### R2-B2 (MEDIUM) — the bound is about 250× the legitimate work, and §5 says otherwise

The budget is 1 s per call plus, for every pattern, 50 µs per run and 1 µs per character. A policy may
carry 50 × 20 patterns plus 50 deny lists. The whole remaining allowance is offered to every run
(`timeout=left`), and an NDJSON stream accrues allowance per item for up to 256 MB.

Measured through `/detect` (`probes/budget_scaling.out`, `probes/budget_1mb.out`). Each call runs in its
own process. The linear policy uses five realistic patterns in rotation; the attack replaces pattern 0
with `\d+\d+\d+[x!]`:

| Policy | Text | Linear patterns | Attack |
|---|---|---|---|
| 1 pattern | 100 KB | 200, 0.24 s CPU | 422 after 1.20 s CPU |
| 100 patterns | 10 KB | 200, 0.06 s | 422 after 2.04 s |
| 1000 patterns | 10 KB | 200, 0.15 s | 422 after 11.1 s |
| 1000 patterns | 50 KB | 200, 0.23 s | 422 after **51.1 s** (222×) |
| 1000 patterns | 1 MB | 200, 3.97 s | 422 after **1001.5 s CPU / 1007 s wall** (≈ 250×) |

- Review doc §5 (lines 342-349) says 1000 CPU-seconds is "the same order as the legitimate work of
  such a request". The legitimate work is 3.97 s.
- It also says "Rate limits bound how many such calls arrive". The limit is 50 rps with a burst of 100
  per key.
- The base allowance alone makes every request of about 2 KB with one stacked pattern burn about 1 s
  (the implementer's own probe: 1.01–1.04 s). That is 50 CPU-s/s per key at the rate limit.
- The Starlette threadpool holds 40 threads, so about 40 large requests occupy all engine threads for
  about 17 minutes per wave.
- The §3.2 title "policy patterns cannot pin a CPU" does not hold at the input caps (`max_text_chars`
  1,000,000; `max_payload_mb` 8; `max_ndjson_mb` 256).

**Fix:** calibrate, ideally together with R2-B1's redesign:
- a much smaller base allowance (tens to hundreds of ms);
- an absolute per-call ceiling for single-shot L1 calls (a setting);
- per-item budgets for NDJSON;
- credit deny lists per term, or match them linearly. A 1000-name deny list legitimately costs
  0.87–0.94 µs/char against the 1 µs/char it earns (`probes/deny_list_rate.out`,
  `probes/deny_list_cost.out`).

Add a scale test (for example 1000 patterns × 50 KB is refused within the ceiling). Correct §5, §3.2
and HANDOFF §6 with measured numbers. If the owner chooses to keep the linear bound, §5 must state the
measured per-request CPU and the amplification, and record it as an owner-accepted residual.

## 5. Non-blocking findings

- **N-r2-1: the `re`/`regex` semantics are not identical.**
  - `check_pattern` parses with `re`, but `regex` runs the pattern. The docs say "the `re` syntax and
    semantics; spans are identical".
  - Accepted patterns that differ (`probes/re_regex_divergence.out`):
    - `(?:ACCT1234){e<=2}`: `re` reads a literal `{e<=2}`, while `regex` runs a fuzzy match (spans
      `(0,8)`, `(12,21)`).
    - `[[:digit:]]+`: a POSIX class in `regex`, a set plus `]` in `re`.
    - `\d+\s\d+` over `\x1c`: `re` matches, `regex` does not. This one can leave a value in clear, and
      FS/GS/RS/US separators do occur in legacy bank extracts.
    - `\w+` before a combining mark gives a different span.
  - Fix: refuse `{e`-style fuzzy constraints and `[[:` in `check_pattern`, and state the
    Unicode-class differences.
- **N-r2-2: the matcher is slower on short texts.** About 2 µs per run on a short text instead of
  0.3 µs (the implementer's figure). `list(finditer(...))` now materialises every match of a run at
  once; before the fix it was lazy. This is an L2 throughput cost; measure it on the 1M-row
  benchmark.
- **N-r2-3: production status is per tenant.** A `_shared/` ref that is a production credential in
  tenant A is not one in tenant B, so B's `jobs` key can still re-point a B connection that names it.
  This holds only when the operator shares a production login across tenants. Record it as a
  residual.
- **Carried from round 1, still open and recorded in the docs:**
  - N1: Oracle `https_proxy` in a DSN.
  - N4: L2 `ApplyOptions` without `tokenize_allowed`.
  - N5: option keys are echoed.
  - N6: the header test's name.
  - N8: T8B, the D4 sweep lens and the SEC-2..5 record. These are F1 programme items outside this
    task; the orchestrator should keep them on the F1 stream.

## 6. Verified as sound in this round

- **Round-1 commits.** Each has a clear scope, and their tests fail on the base.
- **Docs.** HANDOFF §7 states the production-credential rule as built, and the residual
  (HANDOFF.md:659-661) is corrected. The review doc's §4 glob entry is corrected.
- **The B3 two-tenant test is the reviewer's probe.** 12 filler requests give 0 entries in the
  process registry, and the victim gets 200 with tier HIGH on `/detect` and `/obfuscate`.
- **The L2 handlers map `PatternTooSlow`.** `prepare`, `run_partition` and `discover_job` handle it, and
  finalize's `_best_effort` records it. `finalize` itself retries other exceptions, so it does not
  loop forever.
- **The custom-type listing and S1–S13 tests are unchanged and green.**
  - `GET /entities` still lists only built-in types.
  - The S1–S13 and F-03…F-22 tests are untouched by round 1 and green, and the round-1 review's §5
    verification stands.

## 7. Plan acceptance

| Item | Status |
|---|---|
| M5 Task 0 | **met** (unchanged since round 1) |
| M5 Task 17 | S1, S2, S4, S6–S13 met; S5 functions met, with the wiring due at the M4 merge (recorded); **S3 not met**: bounded, but R2-B1 and R2-B2 apply |
| Programme plan §2.6 F1 ("review doc lists every finding with fix + test; `tests/security/*` green") | `tests/security/*` green. The review doc's F-02/S3 entry claims a property (thread-CPU budget, "cannot pin a CPU", a residual "of the same order as legitimate work") that the measurements refute. T8B, D4 and the SEC-2..5 record are outside this task (N8) |

## 8. Re-run

```bash
cd /Users/neelu/document_intelligence/dps-wt-fsec
P=/Users/neelu/.cache/dps-build/reports/S3-review-r2/probes
./lane.sh python $P/regex_timeout_semantics.py         # process-CPU timeout, starvation
./lane.sh python $P/concurrent_flag.py                 # concurrent=True vs False under one busy thread
./lane.sh python $P/concurrency_false_refusal.py       # 1/4/8/12 concurrent legit calls
./lane.sh python $P/gil_busy_false_refusal.py          # one busy Python thread
./lane.sh python $P/probe_e2e_false_refusal.py 18518   # real uvicorn server (free port), /detect
./lane.sh python -c "import os,runpy;[os.environ.pop(k) for k in list(os.environ) if k.startswith('DPS_') and not k.startswith('DPS_TEST_')];runpy.run_path('$P/probe_budget_scaling.py',run_name='__main__')"
# (probe_budget_1mb.py: same harness, 1000 patterns x 1 MB; the attack case takes ~17 min of one core)
./lane.sh python $P/deny_list_rate.py; ./lane.sh python $P/deny_list_cost.py
./lane.sh python $P/re_regex_divergence.py
# base check (round-1 tests on base 4d2b234): scratch tree = git archive 4d2b234 + HEAD tests/,
# run with PYTHONPATH=<scratch> and the lane env; the B1/B3 re-stated checks: $P/test_r2_basecheck.py
```
