# S3c-review-r2 — `fsecc`: the fix for S3c review round 1 (R1-B1), and the stream as a whole

Reviewer: independent S3c reviewer, round 2. Date 2026-10-16. Worktree
`/Users/neelu/document_intelligence/dps-wt-fsecc`, branch `fsecc`, head **`5e529bf`** (fix commit on
`1532ef9`; the stream is `e0095ca` + `1532ef9` + `5e529bf` on `fee0041`). Inputs: `S3c-impl.md`,
`S3c-review-r1.md`, `S3c-fix-r1.md`, `S3-review-r3.md`. No repository file was modified, and the tree
is clean. Evidence is in `~/.cache/dps-build/reports/S3c-review-r2/`: gate logs, image and tree hashes,
and `probes/`. The probes print statuses, codes, counts and timings only, and every text, term and
pattern in them is synthetic. The lane stack (dps-db, the sources profile, app, worker) was started
for the gates and stopped at the end.

## Verdict: **PASS** — no blocking finding

- **R1-B1 is fixed.** `MatcherPool` now hands matchers to waiting callers in arrival order.
  - Code reading found no lost grant, double hand-out, leak or deadlock.
  - A randomized stress run of the real pool logic found no violation. It used stub matchers, with
    failed runs and starts, short waits, resizes and interrupted waits, over about 53 000 calls.
  - On real matcher processes, 4, 8 and 16 callers on 2 matchers made equal progress with
    **0 refusals**. Round 1 measured two callers starved and refused at 30 s.
- **R3-B1 still holds.** `/health` was never slower than **0.08 s** behind `/synthesize` or
  `/detect` runs. Back-to-back synthesize gave **291 ok, 0 failed**.
- **R3-B2 holds as a security property.** Builds are charged and bounded.
- **No regression of R2-B1 or R2-B2.**
- **N2 is fixed**, and every engine route maps a busy or failed matcher correctly.
- **One acceptance line is not met to the letter**: "trie build CPU at or below round-1 cost".
  - Measured: about 1.2 times end to end, and 1.05–1.87 times by list shape. It reaches 2.3 times
    on a first build with a cold case cache.
  - It is unchanged by this round and charged and bounded either way. The docs now state it.
  - Like round 1, I do not block on it. The owner must accept or reject it explicitly (§5, N1).

## 1. Hygiene

- Stream commits `e0095ca`, `1532ef9` and `5e529bf`:
  - author and committer `NilenduGanguli <nilendu.ganguli@gmail.com>`;
  - **0** trailers;
  - conventional messages.
- gitleaks `fee0041..HEAD` (3 commits): no leaks (`gitleaks.log`).
- Lane files are git-ignored. Ports 8641+ and the probe ports 18641/18643/18647 were free.
- Image `data-privacy-services:fsecc` `71a99f0a5717` (app and worker containers): all 155
  `dps/**/*.py` files are byte-identical to the tree by sha256 (`image-dps.sha256`, `tree-dps.sha256`).
- Stack logs after the integration gate: 0 `pattern_on_event_loop`, 0 `pattern_matcher_*`, 0 tracebacks.

## 2. Gates (run by the reviewer on `5e529bf`)

| Gate | Result (log) |
|---|---|
| `ruff format --check dps tests tools` · `ruff check dps tests tools` · `mypy dps` | 468 formatted · all checks passed · 155 files, no issues (`lint.log`) |
| `pytest -q -m "not integration"` (lane DB up) | **3598 passed, 65 skipped**, 12 min 41 s (`pytest-unit.raw.log`) |
| `DPS_TEST_REQUIRE_SERVICES=1`, r3's security/DB list (`tests/security` … `tests/test_post_sample_review.py`), `-m "not oracle and not browser and not ocr"` | **604 passed, 1 skipped** (`pytest-security-db.raw.log`) |
| `DPS_TEST_REQUIRE_SERVICES=1 -m "integration and not browser and not ocr and not slow and not oracle"` (dps-db, source-pg, MinIO, Mongo, app, worker) | **149 passed, 1 skipped** (`pytest-integration.raw.log`) |
| `DPS_TEST_REQUIRE_SERVICES=1 -m "slow and not browser and not ocr and not oracle"` | **9 passed** (`pytest-slow.raw.log`) |

## 3. Probes (`probes/*.out`)

| Probe | Before (r3 / S3c round 1) | `5e529bf` |
|---|---|---|
| r3 `probe_event_loop_freeze`: `/detect` 1000-pattern attack | 422 in 5.06 s, `/health` 0.00 s | 422 in 5.12 s, `/health` **0.01 s** |
| same, `/synthesize` | r3: `/health` **4.99 s** | 422 in 5.08 s, `/health` **0.01 s** |
| `/detect`, 20 new 1000 × 50 lists | r3 200 in 12.80 s | 200 in 1.98 s, `/health` 0.05 s |
| 6 × `/detect` attacks, then a `/synthesize` attack | r3 `/health` **19.53 s** | `/health` **0.08 s** (synthesize 422 after 19.8 s) |
| r3 `probe_synth_dutycycle`: back-to-back `/synthesize` 30 s, 1 s health timeout | r3 3 ok / 26 failed | **291 ok / 0 failed** |
| R1-B1 `pool_fairness_budget`, 3 MB texts, 2 matchers, 40 s (`PatternBudget(run_cap=5.0)`, `_HEAVY` routing) | round 1: 1074 / 1092 / **1 / 1**, two refused at 30 s | 4 callers 441/441/441/442; 8 callers 233–234 each; 16 callers 113–114 each; **0 refusals**, longest scan 0.2 s / 0.2 s / 0.5 s |
| R1-B1 `pool_fairness` (`MatcherPool.run`, 0.38 s runs, 4 callers) | round 1: 120 / 116 / **1 / 1** | 49 / 49 / 48 / 48, **0 refusals**, longest 1.1 s |
| R2-B1 `concurrency_false_refusal` · `gil_busy_false_refusal` | r3 0 refusals · 3/3 | **0/1, 0/4, 0/8, 0/12 · 3/3 ok** |
| Route mapping (`busy_mapping2.py`, pool patched to busy and to failed) | round 1: test-recognizer 500 | busy: **503 `pattern_matcher_busy`** on `/detect`, `/obfuscate`, `/synthesize` (text and JSON), `/policies/plan`, `/policies/test-recognizer`, `/documents/{detect,obfuscate,synthesize}`; NDJSON `/detect`, `/obfuscate`, `/synthesize`: error line with the same code. Failed: 422 `custom_pattern_too_slow` everywhere |
| N-r3-2 `probe_pool_queue`, 48 ceiling-spending `/detect` | r3 ordinary 24.1 s, drained 120 s; round 1 25.0 s / 51.6 s | ordinary **24.1 s**, drained 52.0 s (422 and 503). Unchanged: the wait is for a threadpool thread, not a matcher |
| Build matrix (round-1 reviewer's `trie_vs_round1_matrix`, best of 3, GC paused) | round 1: 0.93–1.89× | random ASCII 1.70× (8), 1.39× (20), 1.22× (50), 1.11× (140), 1.05× (256); names 1.17×; account ids 1.10×; e-mails 1.21×; **Turkish names 1.87×**; CJK 1.04× |
| Build with a cold per-character case cache (`cold_matches_build.py`, new) | not measured | CJK 1000 × 8 **2.32×** (warm 1.15×); CJK 1000 × 50 1.43×; Latin-extended 1.39×; Greek+Cyrillic 1.26×; ASCII 1.41× |
| 2.7 MB `/detect` (50 new lists × 1000 × 50), process CPU, interleaved | round 1 tree `5cfb597` | `5cfb597` **3.76 / 3.76 / 3.92 s**; `5e529bf` uncapped 4.40–4.65 s (**about 1.18×**); default 5 s ceiling: 200 at 4.28–4.84 s |
| FIFO pool stress (`pool_stress.py`, new; stub matchers; 24 threads; 15 s × 3 seeds) | — | about 17 000 calls per seed: ok, busy (waits 0–5 s), failed runs, failed starts and interrupted waits; **0 violations**. No matcher was used twice at once or closed twice; no slot leaked (count = idle at the end); no waiter was left; no thread hung |

## 4. Review of the fix (`5e529bf`)

### 4.1 `MatcherPool` (FIFO)

Invariant: whenever callers wait, no matcher is idle and `count >= size`.
- `_release` grants to the oldest waiter before it returns a matcher to `_idle`.
- `_acquire` queues whenever anyone already waits.
- `_free_slot` and `resize` grant counted slots while `count < size`.
- After a shrink, `count > size` makes `_release` close the surplus before any grant.

I checked each path:
- **Timeout race.** Grants pop the waiter under the lock. A timed-out waiter re-checks `granted`
  under the lock: it either takes the grant (matcher or slot) or removes itself. Nothing is lost
  and nothing is granted twice.
- **Interrupted wait.** `_abandon` passes a granted matcher to `_release`, and a granted slot to
  `_free_slot`, outside the lock (the lock is not re-entrant). Covered by the stress run's
  interrupted waits, which raise before and after grants.
- **Failed start or failed run.** A slot goes to the next waiter, which tries its own start, so a
  start failure cascades as `MatcherFailed` (fail closed, as before).
- **Fork.** `_after_fork` resets the lock, the queue, the idle list and the count.
- **No deadlock.** A thread holds at most one matcher and never waits while it holds one.

The hand-off costs the woken thread's scheduling latency. Throughput was unchanged: 1765, 1869 and
1818 scans in 40 s at 4, 8 and 16 callers.

### 4.2 The defect hunt

- **Moved block.** The releaser never blocks. A waiter blocks only its own worker thread. The event
  loop never waits, because the round-3 guard is intact and its log was empty under all gates.
- **Budget bypass.** None found.
  - The wait is wall time and spends nothing.
  - A run's limit is still `min(left, run_cap)`, and its compile CPU is charged.
  - No API call runs pattern work on two threads at once. Documents only parallelise OCR, and each
    NDJSON item has its own budget.
- **Refusals under concurrency.** None at 4, 8 or 16 callers with legitimate back-to-back runs.
  - A legitimate run is now refused only when the work queued ahead of it exceeds 30 s divided
    among the matchers.
  - That takes about 12 or more ceiling-spending runs queued ahead (N-r3-2, per-tenant fairness,
    still open).
- **R2-B1 / R2-B2.** No change to the slice, the child's clock or the ceilings. The probes and the
  load tests in the gates are green.
- **N2.** `/policies/test-recognizer` maps `PatternMatcherBusy` to 503. Every other engine route
  maps both refusals; the table in §3 covers 12 routes plus NDJSON.
- **Tests.** The four new pool tests and the N2 test are sound:
  - the fairness test patches `regex_guard.POOL` with a private 2-process pool and a 1 s bound;
  - the stub tests check order, abandonment and slot hand-off.

  They passed in the unit and security gates.

## 5. Non-blocking findings

- **N1 (carried; owner decision): the build is not at or below round-1 cost.**
  - Measured: about 1.2× end to end (2.7 MB policy), 1.05–1.87× by shape with a warm case cache.
  - New: up to **2.3×** for a first build whose characters the process has not seen. `_matches` is
    a per-character `regex` compile plus a scan of the cased table, cached process-wide (16 384
    entries). The round-1 matrix cleared the trie cache but not this one, so its best of 3 hid the
    cold cost. An adversary can cycle code points to keep it cold.
  - Every build is charged to the ceiling, so this is CPU at most about 2.3 times round 1's,
    bounded per request. It is not a security defect.
  - The docs' "at most about 1.9 times measured" (security review §1 and §3.2, HANDOFF,
    M5-DEVIATIONS, the `custom.py` docstring) understates first builds. Say "up to about 2.3 times
    on a first build of new characters".
  - The owner still has to accept "about 1.2×, up to 2.3×" or ask for a cheaper build.
- **N-r2c-2 (docs accuracy).** M5-DEVIATIONS l.1587 says "N3-N6 stay open (security review §5 and
  the F1/owner list)". §5 of the security review records none of them:
  - N3: the LLM egress verifier `residual_values` runs on the event loop, `gateway.py:177`.
  - N4: `/readyz` and `/metrics` are sync routes.
  - N5: NDJSON resolves and compiles the policy for every item, uncharged.
  - N6: `asyncio.to_thread` shares the default executor with `getaddrinfo`.

  The programme plan's F1 line requires the review doc to list every finding. Also,
  `PrivacyEngine.synthesize`'s docstring ("Every CPU step runs on a worker thread; only the LLM
  rewrite is awaited on the event loop") is contradicted by N3, which the round-1 review asked to
  fix or name.

  Fix: one §5 bullet per item, or point the deviation log at where they are actually tracked; and
  name the verifier in the docstring.
- **Carried, unchanged and confirmed:**
  - N3 (verified at `gateway.py:177`);
  - N4 (an ordinary request still waited 24.1 s behind 48 attack requests, so a sync `/readyz`
    would too);
  - N5, N6;
  - N-r3-1, N-r3-2, N-r3-4, N-r3-5;
  - the earlier N1/N4/N5/N6/N8 and N-r2-2/N-r2-3.

## 6. Acceptance

| Item | Status |
|---|---|
| R1-B1: no refusals of legitimate work at the default 4-on-2 concurrency; FIFO; test written first | **met** (0 refusals at 4/8/16 callers; equal progress; 4 new tests + stress run clean) |
| R3-B1: 0 failed `/health` under back-to-back synthesize; `/health` never blocked behind `/detect` runs | **met** (291/0; worst 0.08 s) |
| R3-B2: builds charged and bounded; 2.7 MB policy answers within about the ceiling | **met** (200 at 4.28–4.84 CPU-s at the default ceiling) |
| R3-B2: trie build CPU at or below round-1 cost | **not met to the letter**: about 1.18× end to end, 1.05–1.87× by shape, up to 2.32× on a cold first build. Non-blocking (N1); the owner must accept or reject it explicitly |
| No regression of R2-B1/R2-B2; no refusals under concurrency | **met** |
| N2 (500 → 503 on test-recognizer) | **met** |
| Gates (ruff, mypy, unit, security/DB, integration, slow) | **green** |

## 7. Re-run

```bash
cd /Users/neelu/document_intelligence/dps-wt-fsecc
P=/Users/neelu/.cache/dps-build/reports/S3-review-r3/probes; R=/Users/neelu/.cache/dps-build/reports/S3c-review-r1/probes
O=/Users/neelu/.cache/dps-build/reports/S3c-review-r2/probes; P2=/Users/neelu/.cache/dps-build/reports/S3-review-r2/probes
./lane.sh python $P/probe_event_loop_freeze.py 18641; ./lane.sh python $P/probe_synth_dutycycle.py 18643
./lane.sh python $P/probe_pool_queue.py 18647
for n in 4 8 16; do ./lane.sh python $R/pool_fairness_budget.py $n 40 3000000; done; ./lane.sh python $R/pool_fairness.py 4 40 650
./lane.sh python $O/pool_stress.py 24 15 1          # FIFO bookkeeping stress (stub matchers)
./lane.sh python $O/busy_mapping2.py busy; ./lane.sh python $O/busy_mapping2.py fail
./lane.sh python $P2/concurrency_false_refusal.py; ./lane.sh python $P2/gil_busy_false_refusal.py
./lane.sh python $R/trie_vs_round1_matrix.py; ./lane.sh python $O/cold_matches_build.py
./lane.sh python $R/e2e_build.py 50 50 1000 600     # round-1 baseline: ./lane.sh sh -c "cd <git archive 5cfb597 dps> && python $R/e2e_build.py 50 50 1000"
```
