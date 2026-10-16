# S3-fix-r1 — `fsec`: the three blocking findings of S3-review-r1 (B1, B2, B3)

Worktree `/Users/neelu/document_intelligence/dps-wt-fsec`, branch `fsec`. Base `4d2b234` (the head the
review read). Head **`5cfb597`**. Working tree clean. Logs and probes:
`/Users/neelu/.cache/dps-build/reports/S3-fix-r1/` (probe outputs print status codes, codes, tiers,
counts and timings only; every value is fake).

## Verdict: all three blocking findings fixed test-first; every gate green

| Commit | Content |
|---|---|
| `be2b326` | `fix(fsec): a connection that holds a production credential is changed or deleted only with admin` (B2) |
| `e7882af` | `fix(fsec): policy patterns run under a per-call CPU budget and custom entity tiers belong to the call` (B1 + B3; they share the new per-call scope) |
| `5cfb597` | `docs(fsec): the security review records the round-1 fixes, their proofs and what remains` |

All three are authored `NilenduGanguli <nilendu.ganguli@gmail.com>`, with no trailer, and use
explicit-pathspec commits. `gitleaks` 8.30.1 on `4d2b234..HEAD` found no leaks. Not pushed: the
orchestrator's autopush loop does that, and the repo's CLAUDE.md asks for no pushes without the owner.

## B1 (HIGH) — F-02/S3 ReDoS: fixed (bound by a CPU budget; the glob needs no regex)

**Design (`dps/engine/detect/regex_guard.py`, `custom.py`, new `dps/engine/call_scope.py`):**
- Policy patterns and deny lists now run on the **`regex` package** (`VERSION0`: `re` syntax and
  semantics, plus a matching `timeout`), never on `re`. `regex>=2024.11.6` is a new **core dependency**
  in `pyproject.toml`. The image installs it from pyproject, so the next image build picks it up.
- Each **call** has one `PatternBudget`, shared by all of the call's detectors. A call is an HTTP request
  (new pure-ASGI `_CallScope` middleware in `dps/app.py`, so every route) or a worker handler task
  (`Consumer._guard`). The budget is 1 s plus 50 µs per pattern run plus 1 µs per character per
  pattern. It counts the calling thread's CPU time, and the GIL is released while matching.
- A pattern that runs past the budget is stopped, and the call is refused with 422
  `custom_pattern_too_slow` (a value-free `CodedError`):
  - `run_engine` maps it on every engine route; NDJSON maps it per item; documents go through
    `run_engine`.
  - The sync `/policies/test-recognizer` route catches it.
  - In L2, the prepare, partition and discover handlers fail with that code and do not retry.
    `_best_effort` records it in the post-write sample.
  - Nothing is ever skipped silently.
- `check_pattern` stays as the first filter. It now also refuses a pattern that the `regex` module
  cannot compile.
- Declared-field globs: `path_glob` returns a `PathGlob` and uses no regex. The path is cut at
  `.`, `[` and `]`, and each name is matched by finding its literal pieces in order. The cost is
  linear in the path.

**Proof:**
- `tests/security/test_regex_guard.py` gains 6 test functions (15 test cases), and one is renamed. The review's four
  stacked patterns, two patterns that defeat the `regex` pre-checks, the separated-star glob and the
  reviewer's route probe all run in a child process with a hard time limit, so a regression fails
  the test instead of hanging the suite.
- The matcher's spans equal `re`'s spans for 10 patterns, Unicode text included.
- A differential check compares the glob with the old regex semantics on 6000 random globs and
  paths.
- One test checks that a call's detectors share one budget, and one checks the budget arithmetic.
- Probes (`S3-fix-r1/probes/`):
  - The reviewer's `probe_redos_api.py` answers HTTP 200 in 0.01 s on test-recognizer, detect and
    obfuscate (before: no answer within 45 s).
  - `probe_glob_redos.py` at 63 characters: 0.000 s (before: > 20 s).
  - `redos_dps_matcher.out`: the review's patterns finish in ≤ 1 ms through DPS's matcher.
    `\d+\d+\d+[x!]` on 2000 characters and `(\w|\d)+$` on 20 001 characters are refused in 1.0 s.
    On all three routes, `\d+\d+\d+[x!]` answers 422 `custom_pattern_too_slow` in 1.02–1.04 s.
  - The reviewer's `redos_bypass.py` still shows `re` timings. It calls `re` directly and only
    shows that the static filter accepts these patterns, which remains true by design.
- Cost: a pattern run on a short text costs about 2 µs instead of 0.3 µs, mostly in the `regex`
  timeout bookkeeping.

## B2 (HIGH) — F-01 incomplete: fixed

- `update_connection` and `delete_connection` (`dps/connections.py`) now load
  `production_refs(tenant)`. Without `admin`, they treat `holds_production_credentials(current, refs)`
  like the `production` tag: any PATCH (endpoint, secret, credentials, roles, name, tags) and any
  DELETE gets 403 `admin_scope_required`.
- The check runs under the row lock.
- The PATCH route answers the refusal before the TLS and SSRF checks of an endpoint change.
- The test `tests/api/test_production_resource_gate.py::test_a_jobs_key_cannot_change_or_delete_a_production_credential_holder`
  covers both ways a holder exists: a jobs key made it before the tag, or an admin made it.
  - All six PATCH variants and the DELETE give 403, and nothing changes.
  - A counting TCP listener at the requested endpoint receives 0 connections.
  - Admin can still rename and delete the holder, and a non-production holder keeps the jobs rules.
- The test failed before the fix (the exploit PATCH answered 200).
- Reviewer's probe re-run (`probes/f01_repoint.after.out`): in both cases, the re-point and the
  restated `secret_ref` give 403, and DELETE gives 403.

## B3 (MEDIUM) — F-10 cross-tenant denial of service: fixed (registry per call)

- Custom tiers now live in a per-call `taxonomy.CustomEntities`. A context variable holds it, bound by
  the same `_CallScope` middleware and `Consumer._guard`. `entity_info` and `register_entity` consult
  it transparently, so no engine signature changed.
- No tenant or request can fill a registry that another one needs.
- A tier no longer leaks between calls: a request that names a custom type without a tier reads
  MEDIUM.
- The worker's `RuntimeCache` shares a job's `BatchTransformer` across partition handler tasks.
  `custom.CustomTiers` registers the policy's tiers again in each call that reuses a detector or a
  transformer. Without this, later partitions would have read custom types as MEDIUM.
- The process-global `TAXONOMY`, capped at 5000, remains only for code outside a request or a job
  (tools and tests).
- Tests (`tests/security/test_custom_entity_bounds.py`, 6 new):
  - The reviewer's two-tenant probe: 12 filling requests all answer 200, nothing reaches the process
    registry, and the victim's new HIGH names answer 200 with tier HIGH on `/detect` and
    `/obfuscate`.
  - A call's registry is its own and capped.
  - No tier leaks between requests.
  - Two concurrent worker handler tasks see only their own tiers and budgets.
  - A reused detector and a cached transformer register their tiers in each later call. I checked
    that these two fail when re-registration is disabled.
- `tests/test_api_entities.py` used the process-wide registration as a precondition. It now asserts
  the request's finding tier and that the process registry stays empty; the listing assertions are
  unchanged.
- Probe (`probes/probe_f10_registry_exhaustion_after.py`, a bounded variant: the reviewer's probe
  loops until the registry is full, which no longer happens):
  - 20 attacker requests declare 11 000 names: all 200, and 0 names reach the process registry.
  - The victim's new HIGH names give 200 with tier HIGH on `/detect` and `/obfuscate` (before: 400).

## Gates (all run by me on the final code)

| Gate | Result |
|---|---|
| `ruff format --check` / `ruff check dps tests tools` / `mypy dps` | 403 files formatted; all checks passed; 137 source files with no issues (`lint.log`) |
| `pytest -m "not integration"` (lane DB up) | **2643 passed**, 10 skipped, 1 xfailed (`pytest-unit.log`). One earlier full run hit 1 failure in `tests/test_ndjson.py::test_one_audit_event_per_stream` (`pytest-unit-run1-ndjson-flake.log`). It is a pre-existing flake (see below); the test passed 25 of 25 times alone, and the next full run was green |
| `DPS_TEST_REQUIRE_SERVICES=1` security + DB set (the reviewer's list plus consumer, connections, egress TLS, entities, custom, taxonomy) | **416 passed**, 1 xfailed (`pytest-security-db.log`) |
| `-m "integration and not browser and not ocr and not slow"` on the fsec stack (dps-db, source-pg, MinIO, MongoDB, Oracle Free) | **237 passed**, 1 skipped (`pytest-integration.log`; the same as the review). I stopped MinIO, MongoDB and Oracle afterwards |
| `-m "slow and not browser and not ocr"` | **12 passed** (`pytest-slow.log`) |
| Frontend | not touched |
| Fails before the fix | B2's test answered 200 on the pre-fix code. Four round-1 behavioural tests that do not import round-1 APIs were run on `git archive 4d2b234` plus these tests (`basecheck-base.log`): all **4 failed** (the route probe got no answer within 60 s, the glob ran past 30 s, the victim got 400, a tier leaked). The same four **pass** on the fix (`basecheck-fixed.log`). The new test files that import `call_scope` fail at collection on the base |

## Documentation updated

- **Review doc** (`docs/security/2026-10-11-m5-security-review.md`):
  - §3.1: F-01's rule as built.
  - New §3.2 for F-02/S3: design, proof and probes.
  - §3.10: the registry per call.
  - §3.23: commits `be2b326` and `e7882af`.
  - §4: the glob candidate is no longer listed as refuted.
  - §5: the residual-risk text now gives the budget arithmetic instead of "length caps bound the work"
    (`max_text_chars` is 1 000 000), the non-production `secret_ref` residual and N1 as open.
  - §6: re-run commands.
- **HANDOFF**:
  - §6: the pattern budget, `custom_pattern_too_slow`, the `regex` dependency and per-call tiers,
    with a note for the M4 merge.
  - §7: the production-credential rule and the corrected residual (HANDOFF.md:633).
- **M5-DEVIATIONS**: a "Security review round 1 fixes B1–B3" section.
- **`docs/technical/api-m1.md`**: the new error code; the `/entities` and test-recognizer text.
- **Non-blocking items:**
  - Done as documentation: N2 (F-11 codebook residual), N3 (F-14 claim window), N7 (F-22 `bind_v`
    wording).
  - Still open and recorded: N1 (Oracle `https_proxy` in a DSN), N4 (L2 `ApplyOptions` without
    `tokenize_allowed`; not exploitable today), N5 (option keys echoed), N6 (the header test's name),
    N8 (T8B, the D4 sweep lens and the SEC-2..5 record, which belong to the F1 stream).

## Notes for the orchestrator

- **M4 merge.** `POST /scans` is an HTTP route, so the middleware covers it. The browser-worker runs
  `Consumer`, so `_guard` covers it. Any new entry point that runs the engine outside these two gets
  only the fallbacks: the global registry and a budget per detector. The merge will likely conflict in
  `dps/app.py` (middleware list) and `dps/worker.py` (`_guard`); keep both bindings.
- **Image.** `regex` is a new core dependency. The image must be rebuilt for deployments; no
  Dockerfile change is needed.
- **Pre-existing flake (not caused by this round).** `tests/test_ndjson.py::test_one_audit_event_per_stream`
  fails about 1.6% of the time:
  - Email tokens keep the domain and use a random 31-character base32 local part.
  - `verify.normalise` drops punctuation, so a token whose local part ends in the two letters of an
    original's two-character local part (`a.k`, `b.m`, …) contains that original after
    normalisation.
  - With 4 tokens against 4 needles, that is about 16/1024 per run, and the leakage sweep then fails
    the strict request.
  - The same false positive can hit real requests whose emails have very short local parts.
  - Worth a follow-up: longer local parts in the fixture, and a sweep rule that keeps the `@`
    boundary for emails.
