# S3c-fix-r1 — `fsecc`: fix for S3c review round 1 (R1-B1), plus N1 docs and N2

Worktree `/Users/neelu/document_intelligence/dps-wt-fsecc`, branch `fsecc`. Base `1532ef9`, fix commit
**`5e529bf`** `fix(fsecc): matcher processes go to waiting callers in arrival order`.
- Author and committer: `NilenduGanguli <nilendu.ganguli@gmail.com>`, with no trailer.
- The commit used an explicit pathspec, and the tree is clean.
- Not pushed: this is not a milestone commit, so `autopush.sh` was not run.

Evidence is in `~/.cache/dps-build/reports/S3c-fix-r1/`: gate logs, `image-build.log`, `gitleaks.log`
and `probes/*.out`. The probes print counts, statuses and timings only, and every text and term in
them is synthetic. The lane stack (dps-db, the sources profile, app, worker) was started for the
gates and stopped at the end.

## Verdict: R1-B1 fixed; N1 docs corrected; N2 fixed in passing; all gates green

## 1. R1-B1: matchers are handed out in arrival order

**Cause.** This was confirmed as the reviewer described it.
- `MatcherPool._acquire` waited in `Condition.wait_for`, and `_release` appended the matcher to
  `_idle` and called `notify()`.
- The releasing caller still held the GIL. Its next `_HEAVY`-routed run has no 5 ms slice in
  between, so it re-entered `_acquire` and popped the idle matcher before the woken waiter could
  run.

**Fix** (`dps/engine/detect/pattern_process.py`):
- **Waiter queue.** New `_Waiter`: an `Event`, a `granted` flag and the matcher handed over.
  `MatcherPool` keeps a `deque` of waiters under a plain `Lock`; the `Condition` is gone.
- **`_acquire`.** A caller queues if anyone already waits, or if no matcher is idle and the pool
  is full. So a caller that has just released a matcher goes behind the existing waiters.
  Otherwise it takes an idle matcher, or counts a slot and starts a child.
- **`_release`.** The matcher goes straight to the oldest waiter (`_grant_locked`). It returns to
  `_idle` only when nobody waits. A surplus matcher after a shrink is still closed.
  - Invariant: no matcher sits idle while callers wait.
- **Freed slots.** A slot freed by `_discard` (a failed run), by a failed start (`_free_slot`) or by
  a larger `resize` goes to the oldest waiter as a counted slot, and that waiter starts a child.
- **Timeout race.** When the bound passes, the waiter re-checks under the lock. If it was handed
  something meanwhile, it takes it, so no matcher leaks. Otherwise it leaves the queue and raises
  `MatcherBusy`.
- **Interrupted waiter.** An exception in the wait passes on whatever the waiter was handed
  (`_abandon`).
- **Fork.** `_after_fork` resets the lock, the queue, the idle list and the count.
- **Unchanged.** New read-only `MatcherPool.waiting` (queued callers). `run()`'s signature, the
  30 s `ACQUIRE_TIMEOUT_SECONDS` and the `PatternMatcherBusy` 503 mapping are unchanged.
- **Out of scope.** Per-tenant fairness (N-r3-2) stays open, as the review allows.

**Tests**, written first in `tests/security/test_event_loop_freeze.py`. They failed on `1532ef9`,
checked from a scratch tree of `git archive 1532ef9 dps` plus the new tests:

| Test | On `1532ef9` | On `5e529bf` |
|---|---|---|
| `test_callers_sharing_the_matchers_all_progress_without_refusals`: 4 callers, 2 matcher processes, back-to-back `_HEAVY`-routed 1000-term deny-list scans of about 600 KB through `PatternBudget(run_cap=5.0).run` for 4 s, bound shortened to 1 s (`regex_guard.POOL` patched with `partial(pool.run, wait=1.0)`). Asserts no refusal, at least 5 runs per caller, and min × 2 ≥ max. | **fails**: runs `[46, 47, 0, 0]`, refusals `[0, 0, 4, 4]` (a 1.2 MB first draft: `[24, 23, 1, 0]` / `[0, 0, 3, 4]`) | passes; passed 3 runs in a row, and again in the full gates |
| `test_a_released_matcher_goes_to_the_oldest_waiter` (stub matchers, no processes): the order is first, second, third, then the releaser | fails (no `waiting`) | passes |
| `test_a_caller_that_gives_up_leaves_the_queue`: after a refusal the queue is empty, and the next release goes idle, not to the dead waiter | fails | passes |
| `test_a_freed_slot_goes_to_the_oldest_waiter`: covers `_discard` and `resize(2)` | fails | passes |

**The reviewer's probes re-run on the fix** (`probes/`, 45 s each, 4 callers, 2 matchers):

| Probe | Review (`1532ef9`) | Fix |
|---|---|---|
| `pool_fairness_budget.py 4 45 3000000` | 1074 / 1092 / **1 / 1** scans; 2 callers `PatternMatcherBusy` at 30.0 s | **515 / 514 / 513 / 514**, 0 refusals, longest scan 0.1 s |
| `pool_fairness_budget.py 4 45 600000` | 5388 / 5347 / **1 / 1**; 2 refused at 30.0 s | **2650 / 2650 / 2649 / 2647**, 0 refusals, longest 0.1 s |
| `pool_fairness.py 4 45 650` (`MatcherPool.run`, 0.37 s runs) | 120 / 116 / **1 / 1**; 2 refused at 30 s | **57 / 57 / 56 / 56**, 0 refusals, longest call 0.9 s |

- Total throughput is about the same: 2056 against 2168 scans at 3 MB, and 10596 against 10737 at
  600 KB.
- The cost of the hand-over is the woken thread's scheduling latency. It is about 5 % at 3 MB and
  1 % at 600 KB, measured on a different run of the same Mac.

## 2. N2 (taken in passing): `/policies/test-recognizer` now answers 503 `pattern_matcher_busy`

- `dps/layers/api/routers/entities.py` caught only `PatternTooSlow`. It now catches
  `(PatternTooSlow, PatternMatcherBusy)` and maps both through `CodedHTTPException(exc.status,
  exc.code, exc.detail)`.
- New test `test_the_recognizer_test_route_answers_a_busy_pool_with_the_retryable_code` was red
  first: `(500, None)`.
- The reviewer's `busy_mapping.py` on the fix gives 503 `pattern_matcher_busy` on `/detect`,
  `/synthesize`, `/documents/synthesize` and `/policies/test-recognizer`. NDJSON gives an error line
  with code `pattern_matcher_busy` (`probes/busy_mapping.out`).
- The API reference documents the 503 for the route.

## 3. N1: the build-cost parity claims are corrected

The claims now read "about 1.2 times round 1, at most about 1.9 times measured". The figures come
from the reviewer's matrix:
- "First Last N" names 1.28×, Turkish names 1.89×, e-mails 1.18×, CJK 1.01×, account ids 0.93×;
- 1.18× end to end on the 2.7 MB request.

Where each claim was corrected:
- **Security review:**
  - §1 S3 row;
  - l.224 heading, which said "cost what round 1 did";
  - the §3.2 paragraph, with the matrix added;
  - the round-3 proof: "under twice" is now "under 1.6 times the flat compile plus 5 ms", the bound
    the test actually uses;
  - l.261, where "name-like lists … build below round 1's compile" is now false.
- **M5-DEVIATIONS:**
  - round-3 entry: "about round-1's compile" is now "about 1.2 times";
  - the l.1523 cost sentence, including "the test bounds it at twice", which is now 1.6× + 5 ms.
- **`custom.py` module docstring:** now "about 1.2 times … 0.9-1.9 times by the list's shape".
- **HANDOFF.md and the test docstrings** in `test_event_loop_freeze.py`.

The owner still has to explicitly accept or reject "about 1.2×, not at or below round 1 for every
list shape". The docs say so; no code change.

## 4. Other docs

- **Security review §3.2.**
  - The FIFO wait is described in "Every wait is bounded".
  - New proof block, "S3c review round 1, R1-B1 and N2", with the tests and probe numbers above.
  - §5 now says runs queue "in arrival order".
  - The browser-worker observation replaces "nothing changes": its crawl's long allowed runs pause
    its ops loop for at most the run cap plus a matcher wait.
- **M5-DEVIATIONS:** new section "S3c review round 1 fix R1-B1", covering the mechanism, the
  tests, N1, N2, and N3–N6 left open.
- **API reference:** the `pattern_matcher_busy` row, and the test-recognizer paragraph.
- **Contract §12 settings row:** "in arrival order".
- **HANDOFF:** the round-3 sentence.

## 5. Gates (all on the final code; image `data-privacy-services:fsecc` `71a99f0a5717` rebuilt from this tree)

The image's `pattern_process.py`, `entities.py` and `custom.py` were checked byte-identical to the
tree by sha256 inside the app container.

| Gate | Result (log) |
|---|---|
| `ruff format --check dps tests tools` · `ruff check` · `mypy dps` | 468 formatted · all checks passed · 155 files, no issues (`lint.log`) |
| `pytest -m "not integration"` (lane DB up) | **3598 passed, 65 skipped**, 12 min (`pytest-unit.raw.log`; 3593 + 5 new). It ran the fairness test's 1.2 MB draft; the 600 KB final version ran in the next gate |
| `DPS_TEST_REQUIRE_SERVICES=1`, r3's security/DB list (`tests/security` … `test_post_sample_review.py`), `-m "not oracle and not browser and not ocr"` | **604 passed, 1 skipped** (`pytest-security-db.raw.log`; 599 + 5 new) |
| `DPS_TEST_REQUIRE_SERVICES=1 -m "integration and not browser and not ocr and not slow and not oracle"` (dps-db, source-pg, MinIO, Mongo, app and worker on the rebuilt image) | **149 passed, 1 skipped** (`pytest-integration.raw.log`) |
| `-m "slow and not browser and not ocr and not oracle"` | **9 passed** (`pytest-slow.raw.log`) |
| gitleaks `1532ef9..HEAD` | no leaks (`gitleaks.log`) |

## 6. Still open (non-blocking, unchanged by this fix)

- **N3:** the egress verifier runs on the loop for the LLM rewrite.
- **N4:** `/readyz` and `/metrics` are sync routes.
- **N5:** NDJSON resolves and compiles the policy for every item, uncharged. Route this to the
  F1/owner list.
- **N6:** the default executor is shared with `getaddrinfo`.
- **N-r3-2:** per-tenant fairness.
- **N1:** owner acceptance of about 1.2×.

## 7. Re-run

```bash
cd /Users/neelu/document_intelligence/dps-wt-fsecc
./lane.sh python -m pytest -q tests/security/test_event_loop_freeze.py -k "progress_without_refusals or oldest_waiter or gives_up or freed_slot or recognizer_test_route"
R=/Users/neelu/.cache/dps-build/reports/S3c-review-r1/probes
./lane.sh python $R/pool_fairness_budget.py 4 45 3000000; ./lane.sh python $R/pool_fairness_budget.py 4 45 600000
./lane.sh python $R/pool_fairness.py 4 45 650; ./lane.sh python $R/busy_mapping.py
```
