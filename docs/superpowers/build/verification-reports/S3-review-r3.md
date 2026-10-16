# S3-review-r3 — `fsec`: the round-2 fixes (R2-B1, R2-B2) and the stream as a whole

Reviewer: independent S3 reviewer, round 3. Date 2026-10-15. Worktree
`/Users/neelu/document_intelligence/dps-wt-fsec`, branch `fsec`, head **`804ec7c`** (base main `64c46e3`,
23 commits; round 2 = `c371d2f` + `804ec7c` on top of `5cfb597`). No repository file was modified.
Logs and probes: `/Users/neelu/.cache/dps-build/reports/S3-review-r3/` (probes print status codes,
error codes, counts and timings only; every text, term and pattern subject is synthetic).

## Verdict: **FAIL** — 2 blocking findings

- **R3-B1 (HIGH): F-02 is still exploitable through `/synthesize` and `/documents/synthesize`.**
  These routes run detection on the event loop. A policy-pattern run that leaves the 5 ms slice
  therefore blocks the loop:
  - first while it waits for a matcher process (an unbounded `Condition.wait`);
  - then for the whole run (up to the 5 s ceiling).

  Measured: one client sending synthesize requests back to back made **26 of 29** `/health` checks
  fail (1 s timeout). Round-2 also makes other callers' runs freeze the loop: `/health` was blocked
  for **19.5 s** while the synthesize run waited behind six `/detect` runs.
- **R3-B2 (MEDIUM, a regression from round 2): building a deny-list trie is outside every budget and
  costs about 10 times the round-1 compile.** One 2.7 MB `/detect` with 50 inline deny lists costs
  **34.2 CPU-seconds** (round-1 code: 3.4 s), all of it before any pattern runs. On `/synthesize`
  that time freezes the event loop.

R2-B1 and R2-B2 hold as specified for `/detect`, `/obfuscate`, NDJSON and the recognizer test:
- concurrency no longer causes refusals;
- the per-request ceiling holds;
- NDJSON items get their own budgets;
- the trie is exact (0 mismatches in 3000 adversarial differential cases);
- a post-write sample that could not run now fails closed.

All gates are green.

---

## 1. State and hygiene

- Working tree clean at `804ec7c`.
- All 23 commits since `64c46e3`:
  - author and committer `NilenduGanguli <nilendu.ganguli@gmail.com>`;
  - **0** `Co-Authored-By`/`Signed-off-by` trailers;
  - conventional messages.
- `gitleaks git --log-opts=64c46e3..HEAD`: no leaks. Round 2 alone: 2 commits, no leaks.
- `frontend/`: 0 files changed over the stream, so no frontend build was needed.
- Lane afterwards: I started MinIO, MongoDB and Oracle for the integration run and stopped them
  again (`compose stop`). `dps-db` and `source-pg` are up, as I found them.

## 2. Gates (run by the reviewer on `804ec7c`)

| Gate | Result |
|---|---|
| `ruff format --check dps tests tools` · `ruff check dps tests tools` · `mypy dps` | 407 files formatted · all checks passed · 139 source files, no issues (`lint.log`) |
| `pytest -m "not integration"` (lane DB up) | **2689 passed**, 10 skipped, 1 xfailed (strict M4 surface), 124 s (`pytest-unit.log`) |
| `DPS_TEST_REQUIRE_SERVICES=1 pytest tests/security tests/test_no_values_in_logs_m5.py tests/test_contract_surface.py tests/api tests/test_api_entities.py tests/db tests/test_custom.py tests/test_taxonomy*.py tests/test_ndjson.py tests/test_egress_tls.py tests/test_connection_endpoints.py tests/test_post_sample_review.py` | **545 passed**, 1 xfailed (`pytest-security-db.log`) |
| `DPS_TEST_REQUIRE_SERVICES=1 pytest -m "integration and not browser and not ocr and not slow"` on the fsec stack (dps-db, source-pg, MinIO, MongoDB, Oracle Free) | **237 passed**, 1 skipped (`pytest-integration.log`) |
| `DPS_TEST_REQUIRE_SERVICES=1 pytest -m "slow and not browser and not ocr"` | **12 passed** (`pytest-slow.log`) |
| Round-2 tests against round-1 code: scratch tree `git archive 5cfb597 dps` + HEAD `tests/` | `test_pattern_budget_load.py`: **4 of 5 fail** (refusals under load; 51 s attack; the NDJSON attack item passed). `test_post_sample_review.py`: **both fail-closed cases fail** (`passed` instead of `review_required`). 6 failed / 18 passed (`basecheck-r2-tests-on-5cfb597.log`). The same tests pass on HEAD |
| Round-2 reviewer probes re-run on HEAD | `concurrency_false_refusal`: refused 0/1, 0/4, 0/8, 0/12. `gil_busy_false_refusal`: 3/3 ok (`probes/r2_*.r3.out`) |

## 3. Blocking findings

### R3-B1 (HIGH) — `/synthesize` runs policy patterns on the event loop, so F-02 still freezes the server

**Where.**
- `dps/layers/api/routers/transform.py:101`: `return await engine.synthesize(...)`, with no
  `run_in_threadpool`. `/detect` (`detect.py:54`) and `/obfuscate` (`transform.py:61`) both use it.
- `dps/layers/documents/__init__.py:398`: `transform = await self.engine.synthesize(...)`. In the same
  pipeline, detect and obfuscate go through `asyncio.to_thread`.
- `PrivacyEngine.synthesize` (`dps/engine/service.py:678-699`) calls `self._detector(...)` and
  `self._detect(...)` synchronously inside the coroutine. Custom recognizers and deny lists
  therefore run on the event-loop thread.

**Mechanism.** With round 2, a run that leaves its 5 ms slice calls `POOL.run` on the loop thread.
The loop then blocks twice:
- in `MatcherPool._acquire` (`pattern_process.py:173-176`): `Condition.wait()` has no timeout, and it
  waits in the queue behind every other thread that is waiting for a matcher;
- in `_Matcher._read`'s `poll()`, for the run itself (up to `DPS_PATTERN_CPU_SECONDS`).

While the loop is blocked, the process accepts no connections and answers nothing, `/health` and
`/readyz` included.

**Evidence.** Real `uvicorn dps.app:create_app`, env local, auth off, NER off.
`probes/probe_event_loop_freeze.py` → `event_loop_freeze.head.out`;
`probes/probe_synth_dutycycle.py` → `synth_dutycycle.*.out`.

| Case | Answer | Longest `/health` answer |
|---|---|---|
| `/detect`, 1000-pattern policy with one stacked pattern, 10 KB digit text | 422 `custom_pattern_too_slow` in 5.06 s | **0.00 s** (threadpool: server unaffected) |
| `/synthesize`, the same body | 422 in 5.03 s | **4.99 s** (the whole process frozen) |
| 6 such `/detect` requests in flight, then 1 `/synthesize` | synthesize 422 after 19.56 s | **19.53 s**: the loop waited for 6 other runs on 2 matchers, then ran its own |
| One client sending that `/synthesize` back to back for 30 s | 7 answers, all 422 | **26 of 29** health checks failed (1 s timeout) |
| Same, round-1 code `5cfb597` | 3 answers | 27 of 28 failed |

**Impact.**
- One `synthesize`-scoped key, at about 0.2 requests per second, keeps a replica unresponsive about
  90% of the time. Liveness probes fail and the pod restarts, which takes every tenant's in-flight
  requests and streams down with it.
- This is the F-02 exploit (a policy pattern pins the server), still open on these two routes after
  rounds 1 and 2. Round 1 already froze the loop for its own budget.
- Round 2 adds a cross-request coupling: a legitimate synthesize call whose deny list needs stage 2
  (for example a 1000-name list on about 200 KB or more of text) now freezes the whole process for as
  long as **other callers'** long runs hold the two matchers.
- The deny-list build of R3-B2 also runs on this loop: one new 1000 × 50 list froze `/health` for
  0.59 s, so 50 lists freeze it for about 30 s.

**Docs and tests contradicted.**
- The `regex_guard` module docstring says no thread, "the event loop included", waits longer than the
  slice.
- The `pattern_process` docstring says "the server's GIL and event loop stay free while it runs".
- The review doc §3.2 says "(the event loop keeps its turns)".
- No test covers `/synthesize` or documents synthesize with a pattern run outside the slice. The
  round-1 and round-2 route probes covered `/detect`, `/obfuscate` and test-recognizer only.

**Fix.**
1. Run synthesize's synchronous engine work off the loop, as detect and obfuscate do. For example:
   - inside `PrivacyEngine.synthesize`, `await asyncio.to_thread(...)` the detector build, `_detect`
     and the transform; or
   - run the coroutine in the threadpool with its own loop, as the NDJSON runner does.

   The `call_scope` context is copied in both cases. Do the same in `DocumentPipeline.run` (line 398)
   and for the detector builds in `label_leftovers` / `label_templates` (built on the loop when T3 is
   on).
2. Make `PatternBudget.run` (or `MatcherPool.run`) refuse when the current thread is running an
   event loop, fail closed and value-free, so the mistake cannot come back unnoticed.
3. Add end-to-end tests through the ASGI app or a real server:
   - `/health` answers within about 0.5 s while a `/synthesize` and a `/documents/synthesize`
     stage-2 run is in progress, and while the pool is busy with other requests' runs;
   - back-to-back synthesize attacks do not starve health checks.

### R3-B2 (MEDIUM, regression from round 2) — the deny-list trie build is uncharged and about 10 times the round-1 compile

**Where.**
- `dps/engine/detect/custom.py:343-357`: `compile_deny_list`, `deny_list_pattern`, `_emit`.
- `PolicyCustomDetector.__init__` (`custom.py:419-438`) calls it.
- Each cased term character becomes a character class (`[Aa]`). The trie is compiled **without**
  `IGNORECASE` by the `regex` module's pure-Python parser, which holds the GIL.
- The cache (`lru_cache(128)` on the term tuple) helps only stored or repeated lists. An inline policy
  with new lists pays the full cost on every request.
- None of `PatternBudget`, the 5 s ceiling or the slice applies to the build.
- The stage-2 child compiles the same pattern again, also uncharged: `pattern_child.py` starts the
  CPU clock and the `RLIMIT_CPU` backstop after `regex.compile`, and its cache holds 64 patterns.

**Evidence.** `probes/probe_deny_build_e2e.py`: each call in a fresh process, a 1000-term inline list
per recognizer, random ASCII terms, tiny text.

| Request | HEAD `804ec7c` | Round-1 code `5cfb597` |
|---|---|---|
| 50 lists × 1000 × 50 chars (2.70 MB body; under `max_payload_mb` 8) | HTTP 200, **34.19 CPU-s** / 34.41 s wall | 3.37 CPU-s |
| 50 lists × 1000 × 140 chars (7.20 MB) | HTTP 200, **41.96 CPU-s** | 9.17 CPU-s |

- **Profile** (`profile_deny_build.out`): the whole request is `compile_deny_list`. Most of it is
  `regex._regex_core` parsing, including 2.47 M `parse_set` calls; the rest is `_emit`.
- **One list, 1000 × 50** (`trie_build_split.out`, `trie_vs_flat_build.out`): 0.08 s to build the
  pattern text plus 0.49 s to compile it, against 0.05 s to compile the round-1 flat form.
- **On `/detect`**: the build runs in the threadpool, so `/health` stays responsive (worst 0.19 s
  during a 12.8 s build), but one key at the 50 rps rate limit can demand about 1700 GIL-seconds per
  second.
- **On `/synthesize`**: the build freezes the loop (R3-B1).

**Docs contradicted.**
- Review doc §3.2 says the trie is "compiled per term set once".
- §5 says "never more than `DPS_PATTERN_CPU_SECONDS` (5 s)" and "at most `DPS_PATTERN_PROCESSES`
  CPUs … plus 5 ms GIL slices in request threads".
- M5-DEVIATIONS says "never an unbounded build outside any budget". The build is linear, but at about
  12 µs per term character, outside every budget, and about 10 times round 1.

**Fix (any combination that bounds it).**
- Charge each list's build (thread CPU) to the call's budget and its ceiling, and check `left` before
  building the next list.
- Cap the total deny-list characters per policy, and the length of a term.
- Emit a cheaper pattern: a scoped `(?i:…)` run where a character's case class is the plain fold, and
  explicit classes only for the irregular characters such as the Turkish i's.
- Charge and limit the child's compile.
- Add a test with the 2.7 MB policy above: it answers (200 or 422) within about the ceiling.
- Correct §3.2, §5, HANDOFF §6 and M5-DEVIATIONS.

## 4. Non-blocking findings

- **N-r3-1: the N-r2-1 refusal can be bypassed with a verbose-mode comment.**
  - `(?x) #[\n(?:ACCT1234){e<=2}` is accepted by `check_pattern` and `RecognizerSpec`, because
    `_engine_only_syntax` reads the `[` inside the comment as the start of a set and stops checking
    `{`.
  - `regex` then runs a fuzzy match: spans `(0,8)`, `(9,17)`, where `re` finds none
    (`probes/nr21_verbose_bypass.out`).
  - Effect: more matches, so more masking, and fuzzy cost that stays under the budget. No value is
    left in clear.
  - Fix: skip comments when the pattern is verbose, refuse the `x` flag, or detect fuzzy nodes from
    `regex`'s own parse.
- **N-r3-2: the matcher pool has no fairness and no bounded wait.**
  - A request waiting for a matcher keeps its threadpool thread.
  - 48 concurrent ceiling-spending `/detect` requests from one key (within the burst of 100) made an
    ordinary `/detect` from another caller (no custom pattern, short text) wait **24.1 s**. The
    attack drained after 120 s. With 8 such requests: no effect (`probes/pool_queue.head.out`).
  - A flood of heavy ordinary requests has a similar effect, so this is the general lack of a
    per-tenant concurrency limit, not specific to patterns.
  - §5's "latency, not refusal" should say that waiting requests occupy server threads and delay
    every tenant's engine routes. Consider a bounded wait (a value-free 503) and per-tenant fairness.
- **N-r3-3: the CPU backstop can dump core.**
  - The child sets `RLIMIT_CPU` but not `RLIMIT_CORE=0`. SIGXCPU's default action dumps core, and
    where `core_pattern` pipes to a host collector, a core of the child would carry request texts.
  - The backstop is practically unreachable: the `regex` timeout covers the whole `finditer`, cut at
    5 ms across 1000 slow matches (`probes/timeout_scope.out`).
  - Hardening only: set `RLIMIT_CORE` to 0 in the child.
- **N-r3-4: per-character calibration on wide deny lists.**
  - 1000-term trie costs measured on the build Mac (`probes/trie_rate_scripts.out`,
    `probes/turkish_list.out`):

    | Synthetic list | µs per character | Share of the 0.5 µs allowance |
    |---|---|---|
    | CJK-like, very wide first-character fan-out | 0.42 | 0.84× |
    | Latin-extended | 0.21 | — |
    | Devanagari | 0.085 | — |
    | Realistic Turkish names (stays a trie) | 0.057 | — |

  - On a slower production CPU the widest lists could be refused on large texts. Calibrate on the
    target hardware (the owner's calibration item in §5).
- **N-r3-5: process-wide caches are shared across tenants.**
  - The caches keyed by caller content are: the `_compile_deny_list` LRU of term tuples, `_HEAVY`
    (by pattern source), `_matches`, and the child's 64 compiled patterns.
  - They keep deny-list terms (often customer names) in memory after the request ends.
  - They also give a weak cross-tenant timing oracle on reuse of an exact list.
  - Low risk: key them by tenant, or record this in §5.
- **Carried from earlier rounds, still open and documented:**
  - N1: Oracle `https_proxy` in a DSN.
  - N4: L2 `ApplyOptions` without `tokenize_allowed`.
  - N5: option keys are echoed.
  - N6: the header test's name.
  - N8: T8B, the D4 sweep lens and the SEC-2..5 record. These are F1 programme items outside this
    task; keep them on the F1 stream.
  - N-r2-2: the per-run cost on short texts, not yet measured on the 1M-row benchmark.
  - N-r2-3: recorded.

## 5. Verified as sound in this round

- **R2-B1 at the matcher and on `/detect`.**
  - A run is stopped on its own CPU. The regex timeout spans the whole iteration.
  - The R2 probes now give 0 refusals at 1, 4, 8 and 12 concurrent calls, and 3/3 succeed beside a
    busy thread.
  - The fail-first check reproduces: 4/5 load tests fail on round-1 code.
  - A `/detect` attack leaves `/health` at 0.00 s.
- **R2-B2.**
  - The per-request ceiling holds for runs: 5.06 s.
  - NDJSON items spend only their own earnings plus the stream's base (`for_stream`/`item` read
    correctly).
  - The worker's run cap is bound in `Consumer._guard`.
- **Trie exactness.**
  - My differential fuzz ran 3000 cases on alphabets with multi-way case classes and found **0
    mismatches** against the flat case-insensitive alternation (`probes/trie_differential_wide.out`).
  - Alphabets covered: Greek symbol variants (ϐ ϑ ϕ ϖ ϰ ϱ ϵ ς), titlecase digraphs (Ǆ ǅ ǆ …),
    Kelvin, Angstrom, Ohm and micro signs, ypogegrammeni, Cherokee, Georgian, the Turkish i's, long s
    and ẛ.
  - A realistic Turkish name list stays a trie and is cheap.
- **Matcher pool.**
  - An answer is never reused across requests: a failing, late or out-of-protocol child is killed
    before release.
  - Children start with `close_fds`, an empty environment, `python -I` and stderr discarded, and the
    pool has an at-fork reset.
  - Texts travel as UTF-8 with `surrogatepass`, so spans are exact.
  - The new modules' logs are value-free.
- **Post-write sample.** `verification_outcome` treats `{"error": …}` as `review_required` and never as
  `passed`. The evidence HTML wording matches, and the console chip already shows "review required".
- **Earlier rounds.** Rounds 0 to 1 (F-01…F-22, B1–B3) are unchanged by round 2 apart from the spans
  API. Their tests are green in the gates above.

## 6. Plan acceptance

| Item | Status |
|---|---|
| M5 Task 0 | **met** (unchanged since round 1) |
| M5 Task 17 | S1, S2, S4 and S6–S13 met. S5: functions met, wiring due at the M4 merge (recorded). **S3 not met**: R3-B1 (the F-02 exploit still freezes the server through `/synthesize` and documents synthesize) and R3-B2 (an uncharged deny-list build, 10 times round 1) |
| Programme plan §2.6 F1 ("review doc lists every finding with fix + test; `tests/security/*` green") | `tests/security/*` is green. The review doc marks F-02 "fixed + test (rounds 1–2)", and its §3.2/§5 claims (event loop free, 5 s per request, "compiled once") are refuted by R3-B1/R3-B2. T8B, D4 and the SEC-2..5 record are outside this task (N8) |

## 7. Re-run

```bash
cd /Users/neelu/document_intelligence/dps-wt-fsec
P=/Users/neelu/.cache/dps-build/reports/S3-review-r3/probes
./lane.sh python $P/probe_event_loop_freeze.py 18531    # R3-B1: /health during /detect vs /synthesize runs
./lane.sh python $P/probe_synth_dutycycle.py 18533      # R3-B1: back-to-back synthesize vs health checks
./lane.sh python -c "import os,runpy,sys;[os.environ.pop(k) for k in list(os.environ) if k.startswith('DPS_') and not k.startswith('DPS_TEST_')];sys.argv=['x','50','50'];runpy.run_path('$P/probe_deny_build_e2e.py',run_name='__main__')"   # R3-B2
./lane.sh python $P/profile_deny_build.py; ./lane.sh python $P/trie_build_split.py; ./lane.sh python $P/trie_vs_flat_build.py
./lane.sh python $P/trie_differential_wide.py 1; ./lane.sh python $P/trie_rate_scripts.py; ./lane.sh python $P/turkish_list.py
./lane.sh python $P/nr21_verbose_bypass.py; ./lane.sh python $P/probe_pool_queue.py 18537; ./lane.sh python $P/timeout_scope.py
# round-1 comparison: a scratch tree = git archive 5cfb597 dps (+ HEAD tests/ pyproject.toml), run from it:
#   ./lane.sh sh -c 'cd <scratch> && python -c "...runpy.run_path(<probe>)..."'   (cwd first on sys.path)
```
