# S3c-impl — S3 review round 3: R3-B1 and R3-B2 fixed (branch `fsecc`)

Worktree `/Users/neelu/document_intelligence/dps-wt-fsecc`, branch `fsecc` (cut from `fsec` at
`fee0041`). Lane files created (git-ignored via `info/exclude`): `.env` (ports 8641/5641/5642/9641/
9642/1641/27641/5643/8642/8643/9643), `.env.lane` (contract §12.2 test endpoints on those ports),
`lane.sh`, `.venv` (Python 3.12, extras `ner,fpe,vertex,docs,db,connectors,uitest,e2e,scanner,
tabular,dev`). Compose: only `dps-db` (+ the `sources` profile for the integration run), stopped
at the end. Evidence: `~/.cache/dps-build/reports/S3c-impl/` (red run, probes, gate logs).

**STATUS: DONE** — R3-B1 and R3-B2 fixed test-first, all gates green on `1532ef9`; commits `e0095ca`
(code + tests) and `1532ef9` (docs). Not pushed by hand (the owner's LaunchAgent autopush picks
`fsecc` up). Lane stack stopped.

## 1. Red first (the reviewer's probes as tests, on `fee0041`)

`tests/security/test_event_loop_freeze.py` written before any code change; run on `fee0041` with
the not-yet-existing names stubbed (`red/red-run.txt`, `red/red-details.txt`): **12 failed,
1 passed** (the wide-alphabet trie differential passes on round-2 code too — exactness was not
the defect).

| Test | `fee0041` |
|---|---|
| `/health` while `/synthesize` runs the 1000-pattern attack (2 s ceiling) | longest answer **2.12 s** |
| same through `/documents/synthesize` (text upload) | **2.04 s** |
| synthesize attack waiting for matchers busy with four `/detect` attacks | **4.16 s** |
| back-to-back synthesize attacks for 4 s | **14** health answers (≥ 50 expected) |
| `/synthesize` building 20 new 1000 × 50 deny lists | **16.96 s** |
| reviewer's 2.7 MB policy (50 new lists) under a 1 s ceiling | HTTP 200 after **41.2 s** (40.5 CPU-s) |
| build cost, trie vs flat compile (1000 × 50) | **17.8×** (1.05 s vs 0.059 s) |
| a build is charged to the call | spent **0.0** |
| a call past its ceiling builds no further list | did not raise |
| deny terms > 256 chars refused / child compile charged / no core dumps | missing |

## 2. R3-B1 — engine work never runs on the event loop

- `PrivacyEngine.synthesize`: the policy, the detector build (deny lists), detection, the plan,
  the transform and the verification run on worker threads (`asyncio.to_thread` copies the call
  scope); only the LLM rewrite is awaited on the loop. `synthesize_text` runs its templating, leak
  guard, `screen` (the request's detector) and rewrite checks on threads. New synchronous
  `PrivacyEngine.synthesize_data` (JSON) — NDJSON chunk threads call it; the per-chunk private
  event loop is gone. `/documents/synthesize` is covered through the engine.
- T3: `label_leftovers` / `label_templates` resolve the policy and build the detector inside their
  thread; value shapes (`leftover_fields`) and `_t3_findings` also off the loop.
- Policy validation (compiles every pattern: ~1 s for 1000 distinct 480-char patterns) moved off
  the loop where a request did it there: NDJSON `_prepare` (3 validations + key material + header),
  `POST /scans` (validation + the detector that classifies the start URL), `POST /policies/validate`,
  policy store `save_draft` / `publish`, job-run validation (`submit._validate_run` →
  `targets.validate_run`).
- `GET /health` is `async` (answered on the loop; never waits for a worker thread).
- **Guard:** the app's `_CallScope` binds `call_scope(serving=True)`, recording the loop's thread;
  `PatternBudget.run` and `custom.build_deny_list` call `regex_guard.ensure_off_serving_loop()` →
  `PatternOnEventLoop` (RuntimeError → value-free 500 / NDJSON `internal_error`, log
  `pattern_on_event_loop`). Thread identity, so the worker, the browser-worker and NDJSON threads
  are unaffected.
- **Bounded waits:** `MatcherPool.run(..., wait=ACQUIRE_TIMEOUT_SECONDS=30)` uses
  `Condition.wait_for(timeout)` → `MatcherBusy` → `PatternMatcherBusy` (503 `pattern_matcher_busy`,
  retryable; an L2 partition is retried by the generic handler).

## 3. R3-B2 — the trie build

- `deny_list_pattern`: an edge is one plain character under `IGNORECASE` when its text-character
  set equals that character's case matches (`_edge`), else a case-sensitive `(?-i:[…])` class
  (Turkish i's); a node with one term left writes the ending as text; compiled with `IGNORECASE`.
  New `flat_deny_list_pattern` (the fallback, and the tests' trie-vs-flat discriminator).
- Build cost (`trie_build_cost.out`, best of 3, 1000 terms of random letters): 1.00× flat at 20
  chars, 1.25× at 50, 1.08× at 140, 0.99× at 256, 1.61× at 8 (0.017 s); name-like lists build
  below the flat compile (shared prefixes). 50 lists × 1000 × 50 in-process: **3.58 s** vs round 1
  **3.17 s** vs round 2 **34.79 s** (same machine).
- **Charged:** `PolicyCustomDetector.__init__` → `build_deny_list(terms, budget)`: guard, refuse if
  `budget.left <= 0` (422 `custom_pattern_too_slow`), build, `PatternBudget.charge_build(thread
  CPU)` (credit + charge: counts toward the ceiling and `spent`, never toward the run allowance).
  First cut charged overruns past a per-character allowance to the run allowance; the full suite
  showed two false refusals — a generation-2 GC lands in the build's thread time (measured: 3 M
  live containers, one pass turned a 0.027 s build into 0.25 s) — so the ceiling is the only bound.
- Matcher child: reports its compile CPU (`ANSWER` `<BddQ`), `RLIMIT_CPU` backstop also before the
  compile, `RLIMIT_CORE` = 0 (N-r3-3); the server charges `compile_cpu` with `charge_build`.
- `RecognizerSpec.deny_list` terms ≤ 256 characters (`MAX_DENY_TERM`, value-free error).

## 4. Proof (green)

New tests `tests/security/test_event_loop_freeze.py` (19, all green): the five `/health` probes
(synthesize, documents synthesize, matchers busy with four `/detect` attacks, back-to-back
synthesize, `/synthesize` building 20 new lists — longest answer < 0.5 s), the guard (a run and a
build on the serving loop's thread refused, value-free; allowed off the loop and outside a
request), T3 labelling and `synthesize` (text + JSON) inside a request's scope, the bounded
matcher wait and its 503 mapping, the reviewer's 2.7 MB policy in a child process (1 s ceiling:
422 within 4 s; 20 new lists at the default ceiling: 200, < 4 CPU-s), build cost < 1.6× the flat
compile (GC paused, best of 3), a build is charged and leaves the run allowance, a call past its
ceiling builds no further list, terms > 256 refused value-free, the wide-alphabet trie
differential (600 cases), the child's compile charge, no core dumps. Round-2 tests in
`test_pattern_matcher.py` adapted where they told trie from flat by flags (now by pattern text /
`_alternation` calls) — same properties.

Gates on `1532ef9` (logs in `S3c-impl/`):

| Gate | Result |
|---|---|
| `ruff format --check` · `ruff check` · `mypy dps` | 468 files formatted · all checks passed · 155 files, no issues (`lint.log`) |
| `pytest -m "not integration"` (lane DB up) | **3593 passed**, 65 skipped (`pytest-unit.log`) |
| `DPS_TEST_REQUIRE_SERVICES=1` security/DB set (the reviewer's list, `-m "not oracle and not browser and not ocr"`) | **599 passed**, 1 skipped (`pytest-security-db.log`) |
| `DPS_TEST_REQUIRE_SERVICES=1 -m "integration and not browser and not ocr and not slow and not oracle"` (dps-db, source-pg, MinIO, MongoDB; app/worker image rebuilt from this tree) | **149 passed**, 1 skipped (`pytest-integration.log`) |
| `-m "slow and not browser and not ocr and not oracle"` | **9 passed** (`pytest-slow.log`) |
| gitleaks `fee0041..fsecc` | 2 commits, no leaks; author/committer NilenduGanguli, 0 trailers |

An earlier full run (before two fixes) had 3 failures, all understood: `.env.lane` exported
`DPS_WORKER_METRICS_PORT` (a config-default test; removed from the lane file), and two legitimate
scans refused because a build's GC pause overran the first cut's per-character allowance (§3: now
ceiling-only); the build-cost test then failed once on a GC pass (now GC-paused, best of 3).

Reviewer probes on the new code (`S3c-impl/probes/*.fsecc.out`, real uvicorn):

| Probe | `fee0041` / round 1 | `fsecc` |
|---|---|---|
| `/health` during `/synthesize` attack (1000 patterns × 10 KB) | 4.99 s | **0.00 s** (422 in 5.12 s) |
| `/health` during `/detect` building 20 new 1000 × 50 lists | 0.19 s, request 12.80 s | **0.05 s**, request **2.32 s** |
| `/health` with 6 `/detect` attacks then a `/synthesize` attack | 19.53 s | **0.04 s** (synthesize 422 after 20.4 s) |
| back-to-back `/synthesize` 30 s, 1 s-timeout health checks | 3 ok / 26 failed | **292 ok / 0 failed** |
| 2.7 MB `/detect` (50 new lists × 1000 × 50) | r1 200 (4.25 CPU-s), r2 200 (40.0) | **422 at the 5 s ceiling** (5.06 CPU-s); uncapped 200 at 4.85 CPU-s |
| 7.2 MB variant (140-char terms) | r2 200 (41.96 CPU-s) | **422 at the ceiling** (5.11 CPU-s) |
| R2-B1 concurrency (1/4/8/12 deny-list scans) · busy GIL | 0 refusals · 3/3 | **0 refusals · 3/3 ok** |
| pool queue (N-r3-2): 48 ceiling-spending `/detect` | ordinary waited 24.1 s; drained 120.3 s | ordinary 25.0 s; drained **51.6 s** (422 and 503) |

## 5. Not done / residuals

- **Build cost vs round 1:** 1.0–1.25× the flat compile for 1000 random-letter terms of 20–256
  characters (1.6× at 8), below it for name-like lists; 50 × 1000 × 50 in-process 3.58 s vs round 1
  3.17 s. Random letters share no prefixes, so the `regex` parser's per-character cost is the floor;
  "back to round-1 cost" holds as "about", not "≤", for that synthetic list. Since builds now count
  toward the ceiling, the reviewer's 2.7 MB policy is refused at the default 5 s ceiling where round
  1 answered 200 (the reviewer accepted 200 or 422 within about the ceiling).
- **Observation (pre-existing, round-2 design):** a generation-2 GC pass lands in whichever thread
  time it interrupts; a run's in-thread slice is charged its `thread_time`, so on a large heap a
  GC pass (measured 0.25 s with 3 M live containers) could refuse a small call's run. Builds are
  immune now (ceiling-only). Worth a look on the 1M-row benchmark (N-r2-2).
- N-r3-1 (verbose-mode comment hides `{e<=2}`), N-r3-2 (no per-tenant fairness; the wait is bounded
  but queued requests hold threadpool threads — the ordinary request still waited 25 s), N-r3-4
  (calibration on target hardware), N-r3-5 (process-wide caches keyed by caller content): open,
  recorded in the security review §5.
- The worker and the browser-worker still build detectors on their own event loops (not a serving
  loop; the guard is inactive there by design); their builds now cost about round 1's compile.
- Merge note: `fsecc` and `fsec` both append to `M5-DEVIATIONS.md`, the security review and
  HANDOFF §6; `scans.py` (scan creation) changed here in two small hunks.

## 6. Commits (branch `fsecc`, on `fee0041`)

- `e0095ca` fix(security): policy patterns never freeze the event loop; deny-list builds are charged
  — `dps/app.py`, `dps/engine/{call_scope,service}.py`, `dps/engine/detect/{custom,pattern_child,
  pattern_process,regex_guard}.py`, `dps/engine/synth/llm_text.py`, `dps/layers/api/ndjson.py`,
  `dps/layers/api/routers/{ops,policies,scans}.py`, `dps/layers/connectors/submit.py`,
  `dps/policy_store.py`, `tests/security/test_event_loop_freeze.py` (new),
  `tests/security/test_pattern_matcher.py`.
- `1532ef9` docs(fsecc): security review (S3/F-02 rows, §3.2 round 3, §5), contract config row,
  API reference (term cap, `pattern_matcher_busy`), HANDOFF (§6, config row), M5-DEVIATIONS
  (section "Security review round 3 fixes R3-B1, R3-B2 (finish stage, `fsecc`)").
