# S3-fix-r2 — `fsec`: the two blocking findings of S3-review-r2 (R2-B1, R2-B2)

Worktree `/Users/neelu/document_intelligence/dps-wt-fsec`, branch `fsec`. Base `5cfb597` (the head the
round-2 review read). Head **`804ec7c`**. Working tree clean. Logs and probes:
`/Users/neelu/.cache/dps-build/reports/S3-fix-r2/` (probe outputs print status codes, codes, counts and
timings only; every text and name is synthetic).

## Verdict: both blocking findings fixed test-first; every gate green

| Commit | Content |
|---|---|
| `c371d2f` | `fix(fsec): policy patterns are charged their own CPU under a small capped allowance, and deny lists are matched as a trie` (R2-B1 + R2-B2, the post-write sample failing closed, N-r2-1's refusals; code and tests) |
| `804ec7c` | `docs(fsec): the security review states the round-2 pattern bound, its measured cost and what remains` |

Both authored `NilenduGanguli <nilendu.ganguli@gmail.com>` (identity from `~/.gitconfig`, no local
override), no trailer, explicit-pathspec commits. `gitleaks git --log-opts=5cfb597..HEAD`: 2 commits,
no leaks (`gitleaks.log`). Not pushed (the orchestrator's autopush loop does that).

## R2-B1 (HIGH) — legitimate scans refused under load: fixed

Root cause confirmed: `regex`'s `_regex.so` calls `clock()` (process CPU, all threads), and with
`concurrent=True` a busy Python thread starved the matcher (measured: own CPU 0.003 s in 0.2 s of
process CPU). Design as built (`dps/engine/detect/regex_guard.py`, new `pattern_process.py` and
`pattern_child.py`):

- **Stage 1, in the calling thread, holding the GIL** (`concurrent=False`) for at most 5 ms
  (`SLICE_SECONDS`). While the GIL is held no other Python thread runs, so the process clock is the
  run's own (measured with a spinning thread: own 0.200 s vs process 0.206 s). Legitimate runs
  finish here (1.2–2.3 µs per run on short texts; 0.002–0.1 µs per character).
- **Stage 2, a matcher process** for runs that outgrow the slice: a pool of
  `DPS_PATTERN_PROCESSES` (new setting, default 2) single-threaded children of `pattern_child.py`,
  started on first use (`python -I`, `env={}`, stderr discarded, `close_fds`), one run at a time,
  so the `regex` timeout is exactly that run's CPU. Binary protocol (UTF-8 `surrogatepass`, spans
  back as int64 pairs); `poll()` with a wall-clock backstop (`select` fails above FD 1024: the image's
  soft limit is 1,048,576); `RLIMIT_CPU` per run as a kernel backstop. A dead, hung or out-of-protocol
  child is killed and replaced and its run refused (`PatternTooSlow`, fail closed). A pattern whose
  last stage-2 run predicts it would outgrow the slice again starts in stage 2 directly (routing
  only; every run is charged what it took).
- Long runs therefore occupy at most `DPS_PATTERN_PROCESSES` CPUs per server process, and the GIL is
  never held more than 5 ms by a run. `PatternBudget.run` returns spans; `custom.scan` adapted.
- **Post-write sample fails closed:** `verification_outcome` makes a sample that returned
  `{"error": code}` `review_required` (like `identifier_check`); the evidence page says the sample
  did not run (not "found personal data in …").

Proof:
- New `tests/security/test_pattern_budget_load.py` (5 tests; **4 fail on `5cfb597`**,
  `failfirst-base.log`: 8/8 concurrent scans refused; through the ASGI app the deny-list requests got
  422; the 1000 × 50 KB attack ran 51.3 s; the NDJSON attack item ran 11 s and passed). On the fix:
  5 passed in 11 s. Includes N concurrent legitimate calls + a busy Python thread through the real
  ASGI app → all 200, and the F-02 attack still 422.
- New `tests/security/test_pattern_matcher.py` (21 tests): stage-2 spans equal `re`'s (astral and
  lone-surrogate texts), forced escalation keeps spans, own-CPU charging beside a busy thread
  (0.25 ≤ spent ≤ 0.4 for a 0.3 s allowance), run cap without ceiling, a matcher SIGKILLed mid-run
  refuses and is replaced, item budgets, routing, deny-list trie (differential, deep lists, cost,
  bounded construction).
- `tests/test_post_sample_review.py`: the fail-closed case (failed on the base).
- Reviewer probes re-run (`probes/*.after.out`): concurrency 0/1, 0/4, 0/8, 0/12 refused (was 0/1, 4/4,
  8/8, 12/12); busy-thread 3/3 ok (was 3/3 refused); real uvicorn server: every deny-list request
  200 with 1, 2 and 4 ordinary requests in flight (was 2 of 3 refused with 4).
- Linux check: the lane image (`data-privacy-services:fsec`, Python 3.12, uid 10001, `--read-only`,
  `--network none`) with this branch's code mounted: stage-2 spans correct, attack stopped at its
  0.3 s limit, 8/8 concurrent deny-list scans beside a spinning thread answered.

## R2-B2 (MEDIUM) — the bound was ~250× the legitimate work: fixed

- Allowance per call 1 s + 50 µs/run + 1 µs/char/pattern → **10 ms + 20 µs/run + 0.5 µs/char/pattern**.
- **Ceiling** `DPS_PATTERN_CPU_SECONDS` (new setting, default 5 s): an API request's total
  (`_CallScope`), each NDJSON item's total, and every single run anywhere (L2 handlers via
  `Consumer._guard`).
- **NDJSON per-item budgets**: `PatternBudget.for_stream()` / `item()` — an item spends only its own
  earnings plus the stream's 10 ms base (the stream total ≤ base + Σ items' earnings).
- **Deny lists matched linearly**: a trie of exact case classes taken from the matcher's own
  case-insensitive comparison (it is not transitive for I/i/ı/İ; checked over every character with
  case forms: these four are the only exception), edges disjoint, cached per term set, construction bounded
  (lists of Turkish-i variants fall back to the flat form). 1000 names: 0.034–0.042 µs/char charged
  vs 0.49–0.68 for the round-1 flat form (`probes/deny_list_trie_rate.out`); identical spans
  (400-case differential test).
- Measured through `/detect` (reviewer's harness, each call in its own process; `budget_scaling.after.out`,
  `budget_1mb.after.out`):

| Policy × text | Linear (before → after) | Attack refused after (before → after) |
|---|---|---|
| 1 × 100 KB | 200, 0.24 s → 0.13 s | 1.20 s → **0.14 s** |
| 100 × 10 KB | 200, 0.06 s → 0.03 s | 2.09 s → **0.56 s** |
| 1000 × 10 KB | 200, 0.17 s → 0.07 s | 11.6 s → **5.06 s** (ceiling) |
| 1000 × 50 KB | 200, 0.23 s → 0.21 s | 51.7 s → **5.09 s** (ceiling) |
| 1000 × 1 MB | 200, 3.99 s → 3.86 s | 1007 s wall / 1001.5 CPU-s → **5.46 s** (ceiling) |

- Scale test in the suite: 1000 patterns × 50 KB refused at a 2 s test ceiling (2.08 s), linear twin 200.
- Review doc §5 now states the measured per-request CPU and amplification (5–250× the per-character
  cost of real patterns; ~11 ms per 2 KB attack request; 5 s ceiling; ≤ `DPS_PATTERN_PROCESSES` CPUs
  for all long runs) and marks the calibration for the owner to confirm; §3.2 title and text corrected.

## Non-blocking items of the review

- **N-r2-1 (partly fixed):** `check_pattern` now refuses an unescaped `{` that is not a repeat count
  (fuzzy constraint in `regex`) and an unescaped `[` inside a set (POSIX class); probe
  `re_regex_divergence.after.out`: fuzzy and posix refused. `\s` vs `\x1c`–`\x1f` and combining marks
  in `\w` remain, documented in §5 with the workaround.
- **N-r2-2 (open):** per-run cost on short texts (~1.2–2.3 µs measured) not re-benchmarked on the
  1M-row run.
- **N-r2-3:** recorded as a residual in §5.

## Gates (all on the final code)

| Gate | Result |
|---|---|
| `ruff format --check` · `ruff check dps tests tools` · `mypy dps` | 407 files formatted · all checks passed · 139 source files, no issues (`lint.log`) |
| `pytest -m "not integration"` (lane DB up) | **2689 passed**, 10 skipped, 1 xfailed (`pytest-unit.log`) |
| `DPS_TEST_REQUIRE_SERVICES=1` security + DB set (reviewer's list + post-sample tests) | **548 passed**, 1 xfailed (`pytest-security-db.log`) |
| `-m "integration and not browser and not ocr and not slow"` (dps-db, source-pg, MinIO, MongoDB, Oracle) | **237 passed**, 1 skipped (`pytest-integration.log`) |
| `-m "slow and not browser and not ocr"` | **12 passed** (`pytest-slow.log`) |
| Frontend | not touched |

## Docs updated

Review doc §1 (S3 row), §3 (F-02 row), §3.2 (rewritten), §3.23 (`c371d2f`), §5 (pattern residual with
numbers; N-r2-1, N-r2-3), §6 (round-2 re-run commands); HANDOFF §4 (two settings) and §6 (the
policy-pattern bullet); M5-DEVIATIONS (round-2 section); contract (settings row, file rows);
`docs/technical/api-m1.md` (error-code row).

## Notes for the orchestrator

- **M4 merge:** `_CallScope` now takes `pattern_cpu_seconds`; `Consumer._guard` reads
  `self.settings.pattern_cpu_seconds`; `create_app` and `worker.main` call
  `configure_pattern_processes`. Keep all three when `dps/app.py` / `dps/worker.py` conflict.
- **Image/ops:** `data-privacy-services:fsec` rebuilt from the committed code. Each matcher process is
  ~17 MB RSS (2 per server process by default); children run `sys.executable -I
  dps/engine/detect/pattern_child.py` (present in the editable install).
- **Pre-existing flake (not this change):** `tests/connectors/test_synthesize_copula.py::
  test_surrogate_job_on_the_seeded_demo_customers` failed once in two integration runs (1 surrogate
  `national_id` equal to a demo value), then passed 3/3 alone and in the final full run. Cause: all
  11,989 demo Aadhaar-like `national_id`s start with `1` — the same never-issued range the surrogates
  draw from — so about 12k × 12k / 10^10 ≈ 1.4% of runs collide. Fix belongs to the M5/demo owners:
  seed demo IDs outside the surrogate range, or re-draw a surrogate that equals a source value.
