# S3c-review-r1 — `fsecc`: the fixes for S3 review r3's R3-B1 and R3-B2

Reviewer: independent S3c reviewer, round 1. Date 2026-10-16 (00:00–00:30 IST). Worktree
`/Users/neelu/document_intelligence/dps-wt-fsecc`, branch `fsecc`, head **`1532ef9`**
(`e0095ca` code + tests, `1532ef9` docs, on `fee0041`). Inputs: `S3c-impl.md`, `S3-review-r3.md`.
No repository file was modified; the tree is clean. Evidence: `~/.cache/dps-build/reports/S3c-review-r1/`
(gate logs and `probes/`; probes print statuses, codes, counts and timings only, and every text,
term and pattern in them is synthetic). Lane stack (dps-db, the sources profile, app, worker) was
started for the gates and stopped at the end.

## Verdict: **FAIL** — 1 blocking finding

- **R3-B1 is fixed.** The reviewer's probes on a real uvicorn server:
  - `/health` answered within **0.01 s** during the `/synthesize` attack (r3: 4.99 s);
  - within **0.07 s** with six `/detect` attacks plus a `/synthesize` attack (r3: 19.53 s);
  - back-to-back synthesize for 30 s: **291 ok, 0 failed** (r3: 3 ok, 26 failed).
- **R3-B2 is fixed in substance.** Builds are charged and bounded by the ceiling, the child
  charges its compile, and terms are capped. The build costs **1.1–1.3×** the round-1 flat compile
  on the reviewer's cases (r3: 10×), so the acceptance line "at or below round-1 cost" is not met
  to the letter (N1).
- **New blocking finding R1-B1 (MEDIUM).** The fix adds a 30 s bound on the wait for a matcher
  process. The pool hands matchers out unfairly, so this bound now refuses legitimate work under
  ordinary concurrency (details below).

## 1. Hygiene

- Two commits, `e0095ca` and `1532ef9`:
  - author and committer `NilenduGanguli <nilendu.ganguli@gmail.com>`;
  - **0** trailers;
  - conventional messages.
- gitleaks `fee0041..HEAD`: no leaks (`gitleaks.log`).
- Lane files are git-ignored through `info/exclude`. Ports 8641+ were free before the run.
- The `data-privacy-services:fsecc` image was rebuilt from this tree. Every layer was a cache hit,
  which shows the 23:19 image already matched the HEAD code. The app container ran image
  `7694ec279387`.

## 2. Gates (run by the reviewer on `1532ef9`)

| Gate | Result (log) |
|---|---|
| `ruff format --check dps tests tools` · `ruff check` · `mypy dps` | 468 formatted · all checks passed · 155 files, no issues (`lint.log`) |
| `pytest -m "not integration"` (lane DB up) | **3593 passed, 65 skipped**, 13 min (`pytest-unit.raw.log`) |
| `DPS_TEST_REQUIRE_SERVICES=1` security/DB set (r3's list, `-m "not oracle and not browser and not ocr"`), includes `tests/security` | **599 passed, 1 skipped** (`pytest-security-db.raw.log`) |
| `DPS_TEST_REQUIRE_SERVICES=1 -m "integration and not browser and not ocr and not slow and not oracle"` (dps-db, source-pg, MinIO, Mongo, app and worker on the rebuilt image) | **149 passed, 1 skipped** (`pytest-integration.raw.log`) |
| `-m "slow and not browser and not ocr and not oracle"` | **9 passed** (`pytest-slow.raw.log`) |

## 3. Probes (r3's probes re-run, plus the reviewer's own; `probes/*.out`)

| Probe | r3 (`804ec7c`) / round 1 | `fsecc` |
|---|---|---|
| `/detect`, 1000-pattern attack | 422 in 5.06 s, `/health` 0.00 s | 422 in 5.11 s, **0.01 s** |
| `/synthesize`, the same attack | 422, `/health` **4.99 s** | 422 in 5.16 s, **0.01 s** |
| `/detect`, 20 new 1000 × 50 lists | 200 in 12.80 s, `/health` 0.19 s | 200 in **1.91 s**, 0.05 s |
| 6 × `/detect` attacks, then a `/synthesize` attack | `/health` **19.53 s** | **0.07 s** (synthesize 422 after 19.8 s) |
| Back-to-back `/synthesize`, 30 s, 1 s-timeout health checks | 3 ok / 26 failed | **291 ok / 0 failed** |
| 2.7 MB `/detect` (50 new lists × 1000 × 50), e2e process CPU | r2 34.2 s; round 1 **3.74–3.85 s** | default ceiling: 200 at **4.50 s**; uncapped: 4.44–4.57 s (**1.18×** round 1) |
| Per-list build, r3's `trie_vs_flat_build` | r2 3.8–11.9× | 1.1× (50), 1.1× (200), 1.3× (CJK 50), 0.9× (CJK 200); 6.6× at 8 characters, which includes the one-off `_cased()` table |
| Build matrix, best of 3, GC paused (`trie_vs_round1_matrix.out`) | — | random ASCII 1.66× (8), 1.34× (20), 1.20× (50), 1.12× (140), 1.13× (256); "First Last N" names **1.28×**; Turkish names **1.89×**; e-mails 1.18×; account ids 0.93×; CJK 1.01× |
| Trie exactness vs the flat alternation | 3000 cases, 0 mismatches | r3's wide differential, 3 seeds: **9000 cases, 0 mismatches**. Own long-term fuzz (shared prefixes, Turkish i's, `(?-i:…)` edges, single-term tails, endings below the depth limit): **3000 cases, 0 mismatches** |
| Turkish and ASCII name lists stay a trie (R2-B2 match cost) | — | yes: 0.014–0.092 µs/char (flat form: 0.17–0.59) |
| R2-B1: `concurrency_false_refusal` · `gil_busy_false_refusal` | 0 refusals · 3/3 | **0/1, 0/4, 0/8, 0/12 · 3/3 ok** |
| Route mapping when every matcher stays busy (`busy_mapping.out`) | — | `/detect`, `/synthesize`, `/documents/synthesize`: 503 `pattern_matcher_busy`; NDJSON: error line `pattern_matcher_busy`; **`/policies/test-recognizer`: 500, no code** (N2) |
| Matcher-pool fairness: 4 callers, 2 matchers, back-to-back legitimate scans (`pool_fairness_budget*.out`) | `fee0041`: 2 callers starve for 45 s, no refusal | **2 callers starve, then are refused (`PatternMatcherBusy`) at 30 s** (R1-B1) |

## 4. Blocking finding

### R1-B1 (MEDIUM, introduced by this round) — the 30 s matcher wait refuses legitimate work under ordinary concurrency, because `MatcherPool` hands matchers out unfairly

**Where.**
- `dps/engine/detect/pattern_process.py`:
  - `MatcherPool._acquire` waits in `Condition.wait_for(lambda: self._idle or …, wait)`;
  - `_release` appends the matcher to `_idle` and calls `notify()`.
- `dps/engine/detect/regex_guard.py`: `PatternBudget.run` maps `MatcherBusy` to
  `PatternMatcherBusy`.

**Mechanism.** The condition does not give matchers to waiters in order:
1. A caller that releases a matcher and starts its next run at once retakes the idle matcher. It
   still holds the GIL, so it gets there before the woken waiter can run.
2. The engine makes this the normal case. A pattern recorded in `_HEAVY` goes straight to the pool,
   with no 5 ms slice in between, so the caller is back at `_acquire` within microseconds.
3. Each caller that keeps issuing such runs therefore keeps its matcher. The other callers wait
   until the round-3 bound refuses them.

Before this round the same starvation existed but ended in latency. Now it ends in a refusal.

**Evidence** (`probes/pool_fairness_budget.py`):
- Setup: the engine's own path (`PatternBudget(run_cap=5.0)`, as a worker handler task binds it,
  with `_HEAVY` routing). 4 callers, which is `worker_concurrency`'s default; 2 matcher processes,
  `DPS_PATTERN_PROCESSES`' default. Each caller scans a large text with a legitimate 1000-name deny
  list, back to back.
- Every scan stays far inside its allowance:
  - 3 MB text: 0.038 s per scan against an allowance of 1.50 s;
  - 600 KB text: 0.008 s per scan against 0.30 s.

| Tree | Callers 0 and 1 | Callers 2 and 3 |
|---|---|---|
| `fsecc`, 3 MB texts | 1074 and 1092 scans | **1 scan each, then `PatternMatcherBusy` after 30.0 s** |
| `fsecc`, 600 KB texts | 5388 and 5347 scans | **1 scan each, then `PatternMatcherBusy` after 30.0 s** |
| `fee0041` (before the fix), 3 MB | 1400 and 1414 scans | 1 scan each, then waited **45 s** (until the others stopped); no refusal |

The same happens through `MatcherPool.run` directly with 0.38 s runs (`pool_fairness.out`): two
callers did 120 and 116 runs, and the other two were refused at 30 s.

**Impact.**
- **Worker.** L2 batch transforms run in `asyncio.to_thread`, so 4 handler threads share 2
  matchers.
  - A job whose free-text values are large enough to leave the 5 ms slice (about 400 K characters
    or more for a 1000-name trie; less for costlier custom patterns) starves two of its handler
    tasks. Their partitions then fail with `partition_failed` / `internal_error`.
  - They are retried with backoff (30 s base, 5 attempts). A long job can push a partition into
    the dead letter. The old code would have completed it, late.
- **API.**
  - Two concurrent requests that make back-to-back stage-2 runs (a 1 MB document with many custom
    patterns, or JSON with many large leaves) lock out every other request that needs a matcher.
  - Those requests get a 503 after 30 s while the two winners are served in milliseconds. This is
    the R2-B1 class: legitimate scans refused because of load, not because of their own cost.
- The doc's line "a run waits at most 30 s … then 503 (retryable: the call did nothing wrong)"
  holds. But the refusal lands on whichever callers lose the race, not on the overload's cause or
  the newest arrival.

**Fix.**
- Make acquisition FIFO. For example, give each waiter a ticket or its own `threading.Event`, and
  have `_release` hand the matcher directly to the oldest waiter (or return it to `_idle` only when
  nobody waits). New arrivals then queue behind existing waiters even when `_idle` is momentarily
  non-empty.
- The bound then refuses only under real overload: the queue position times the run time exceeds
  30 s.
- Add a test with 4 callers on a 2-process pool, each making back-to-back `_HEAVY`-routed runs for
  longer than the bound. Expect no `MatcherBusy`, and every caller to complete a comparable number
  of runs. The probe above is a ready template; shorten the bound in the test.
- Per-tenant fairness (N-r3-2) can stay open. FIFO is what keeps the bound from refusing
  legitimate work.

## 5. Non-blocking findings

- **N1: build cost against the acceptance line, and the docs' parity claims.**
  - The trie build is charged to the ceiling and bounded, so R3-B2's security property holds. Per
    request: the ceiling plus at most one list, ≤ 0.3 s at 1000 × 256 (`deny_list` is capped at
    1000 terms).
  - Its CPU is **1.1–1.3×** the round-1 flat compile on the reviewer's cases, **1.18×** end to end
    on the 2.7 MB request (200 at the default ceiling here; the implementer saw 422, so it is a
    coin-flip at 5 s), and up to 1.66× (8 characters) and **1.89× (Turkish names)** in the matrix.
  - "At or below round-1 cost" is therefore **not met to the letter**. I do not block on it: a
    charged, bounded build at about 1.2× is not a security defect, and strict parity for every list
    shape may not be reachable with a trie. The owner should accept or reject this explicitly.
  - The docs overclaim. Correct:
    - security review l.224, "Deny-list builds cost what round 1 did";
    - security review l.261 and M5-DEVIATIONS l.1523, "name-like lists … build below round 1's
      compile": false for "First Last N" names (1.28×) and Turkish names (1.89×), true for account
      ids (0.93×);
    - M5-DEVIATIONS, "the test bounds it at twice": the test bounds it at 1.6×;
    - the `custom.py` module docstring.
- **N2: `/policies/test-recognizer` answers 500 (no code) instead of 503
  `pattern_matcher_busy`.**
  - It catches only `PatternTooSlow` (`routers/entities.py:121-123`), while the API reference
    documents the 503 generally.
  - Fix: also catch `CodedError`, or `PatternMatcherBusy`.
- **N3: the LLM rewrite still runs CPU on the event loop.**
  - `LlmGateway.complete` runs the egress verifier (`residual_values`: normalisation, `scan_text`
    and `scan_phones` over the whole placeholder text) on the loop, at about 0.9 s per MB.
  - With `DPS_LLM_TEXT_REWRITE=true` and a 1 MB text (`max_text_chars`), `/health` paused
    **0.80–0.86 s** per request. Back to back for 30 s: 192 ok, 0 failed at a 1 s timeout
    (`rewrite_loop*.out`). This is not a policy pattern, and it is below the liveness timeout on
    the build Mac; slower production cores may not be.
  - The claim "every CPU step runs on a worker thread; only the LLM rewrite is awaited" should
    either move `residual_values` off the loop (`asyncio.to_thread` in `complete`) or name it.
- **N4: `/readyz` is still a sync route (threadpool).**
  - Under N-r3-2's threadpool starvation (48 concurrent ceiling-spending `/detect` delayed an
    ordinary request by 24–25 s), readiness (`timeoutSeconds` 1, period 10 s, threshold 3) fails.
    The replica leaves the Service; a single replica means an outage for every tenant.
  - `/health` is safe now. Making `/readyz` (and `/metrics`) `async` is a one-line change each.
- **N5 (new, pre-existing since round 1, outside R3's scope): NDJSON resolves and validates the
  policy, and compiles its patterns, for every item, uncharged.**
  - With 1000 distinct 480-character patterns this costs about **1.1–1.2 CPU-s per item**: 10
    items take 12.45 s on `fsecc` and 11.89 s on round-1 `5cfb597` (`ndjson_policy_per_item.out`).
  - `ndjson_max_items` is 1,000,000 and `max_ndjson_mb` 256, so one request can demand about 10⁶
    CPU-seconds on a threadpool thread. Even a 20-pattern policy costs about 8 ms per item.
  - The §5 sentence "An NDJSON stream spends at most its items' own allowances plus one 10 ms base"
    covers pattern runs only.
  - Recommend resolving the policy and building the detector once per stream (or per chunk),
    charged to the stream. Route this to the F1/owner list rather than this fix.
- **N6: `asyncio.to_thread` work lands on the loop's default executor.** That work is synthesize
  and T3, document steps, NDJSON preparation, scan creation and policy-store validation. The
  default executor has `min(32, cpu + 4)` threads and is shared with `loop.getaddrinfo`, which DNS
  resolution for DB, LLM and S3 connections uses. A flood of synthesize requests waiting up to
  30 s for matchers can fill it and delay those lookups. This is the N-r3-2 class; worth a
  dedicated executor or a limiter.
- **Observation (pre-existing, by design per the implementer):**
  - the browser-worker's crawl runs detection on the same event loop that answers its ops
    `/health` (`dps/browser_worker.py`, `crawler.py`);
  - a scan's long but allowed runs block it, up to the run cap plus a matcher wait;
  - with `browser_concurrency` 2 and 2 matchers the wait is short. Note it in §5 rather than
    "nothing changes".
- **Still open from r3, documented:** N-r3-1, N-r3-2, N-r3-4, N-r3-5, and the carried N1/N4/N5/N6/N8
  and N-r2-2/N-r2-3.

## 6. Verified as sound

- **R3-B1.**
  - Every synthesize CPU step outside the LLM call runs on threads that copy the request context:
    `_synthesis` and `_synthesis_response`, and the `template`, `guarded` and `judged` steps in
    `synthesize_text`.
  - T3 `collect` runs on threads; NDJSON chunk threads call `synthesize_data` (no private loop).
  - These also moved off the loop: the NDJSON `_prepare`, `POST /scans`, `/policies/validate`,
    `save_draft`, `publish` and `_validate_run`.
  - The custom-entity registry is a shared object bound by the middleware, so tiers registered in
    one hop are visible in the next.
  - The guard is thread-identity based. It cannot fire on worker threads, and it caught nothing in
    3593 + 599 + 149 + 9 tests.
  - The 503 and 422 mappings work on the engine routes, documents and NDJSON.
  - Edge payloads (`{}`, nulls) still give 422, not 500.
  - The LLM egress verifier uses built-in patterns only (no policy pattern on the loop).
- **R3-B2.**
  - `build_deny_list`: refuses when `left <= 0`, charges `thread_time` through `charge_build`
    (credit + charge, so the run allowance is unchanged), and every list is charged.
  - The child reports `compile_cpu` (also on timeout), sets the `RLIMIT_CPU` backstop before
    compiling (limit + 2 s slack, so no false kill at ≤ 0.3 s compiles) and sets `RLIMIT_CORE` 0.
  - Terms are capped at 256 characters and lists at 1000 terms, so one list's build is ≤ 0.3 s.
  - Exactness: 12 000 differential cases, 0 mismatches.
- **R2-B1 / R2-B2.** No refusals under concurrency or a busy GIL. NDJSON item budgets are
  unchanged. Name tries keep their match cost.
- **Tests.** `test_event_loop_freeze.py` uses `with TestClient(...)`, so the app runs on one
  portal loop and the `/health` probes would see a frozen loop. The heavy end-to-end case runs in a
  child process with a hard limit.

## 7. Acceptance

| Item | Status |
|---|---|
| R3-B1: 0 failed `/health` under back-to-back synthesize; `/health` never blocked behind `/detect` runs | **met** (291/0; worst 0.07 s) |
| R3-B2: builds charged and bounded; reviewer's 2.7 MB policy answers within about the ceiling | **met** (200 at 4.5 CPU-s; refused at a 1 s ceiling in the test) |
| R3-B2: trie build CPU at or below round-1 cost | **not met to the letter**: 1.1–1.3× on r3's cases, 1.18× e2e. Non-blocking for me (N1); owner to accept |
| No regression of R2-B1/R2-B2; no refusals under concurrency | **not met**: R1-B1 (bounded wait + unfair pool refuses legitimate work at the default 4-on-2 concurrency) |
| Gates (ruff, mypy, unit, security/DB, integration, slow) | **green** |

## 8. Re-run

```bash
cd /Users/neelu/document_intelligence/dps-wt-fsecc
P=/Users/neelu/.cache/dps-build/reports/S3-review-r3/probes; R=/Users/neelu/.cache/dps-build/reports/S3c-review-r1/probes
./lane.sh python $P/probe_event_loop_freeze.py 18641; ./lane.sh python $P/probe_synth_dutycycle.py 18643
./lane.sh python $R/pool_fairness_budget.py 4 45 3000000      # R1-B1 (callers 2-3 refused at 30 s)
./lane.sh python $R/pool_fairness.py 4 45 650                 # R1-B1 through MatcherPool.run
./lane.sh python $R/e2e_build.py 50 50 1000 [600]              # 2.7 MB build CPU (run from a round-1 tree for the baseline)
./lane.sh python $R/trie_vs_round1_matrix.py; ./lane.sh python $R/trie_differential_long.py 1
./lane.sh python $R/busy_mapping.py; ./lane.sh python $R/probe_rewrite_loop.py 18645
./lane.sh python $R/ndjson_policy_per_item.py 1000 10
```
