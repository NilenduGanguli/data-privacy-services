# S3-review-r1 — `fsec`: M5 Task 0, Task 17 and the confirmed security findings F-01…F-22

Reviewer: independent S3 reviewer, round 1. Date 2026-10-14. Worktree
`/Users/neelu/document_intelligence/dps-wt-fsec`, branch `fsec`, head **`4d2b234`** (base main `64c46e3`,
18 commits). No repository file was modified. Reviewer logs and probes:
`/Users/neelu/.cache/dps-build/reports/S3-review-r1/` (probe outputs print status codes, codes, booleans,
counts and timings only; every value is fake).

## Verdict: **FAIL** — 3 blocking findings

1. **B1 (HIGH) — F-02/S3 ReDoS is not fixed.** The static guard refuses only the nested-quantifier
   shape. Patterns with stacked quantifiers still pin a CPU, and so do declared-field globs whose stars
   are not adjacent. Shown through the real API.
2. **B2 (HIGH) — F-01 is incomplete.** A key without `admin` can re-point any untagged connection that
   holds a production credential (it re-supplies the same `secret_ref`). DPS then sends the production
   login and password to an endpoint the caller chose. The same key can also delete that connection.
3. **B3 (MEDIUM) — the F-10 fix adds a cross-tenant denial of service.** One tenant's `detect` key
   fills the process-wide custom-entity registry in 10 requests. After that, every other tenant's new
   HIGH-tier custom recognizer fails with 400 until the process restarts.

The gates are green and the other fixes hold (details below), but these three defeat or regress the
properties their findings were meant to secure.

---

## 1. State and hygiene

- `git status` is clean. All 18 commits are by `NilenduGanguli <nilendu.ganguli@gmail.com>`, with
  conventional messages and **0** `Co-Authored-By`/`Signed-off-by` trailers.
- Task 0 is committed as `dc85450` with the plan's message. Task 17 is `6061767`, also with the plan's
  message. Each of F-01…F-22 has its own commit (see the review doc §3.23).
- Migration `046_job_group_kcv.sql` is in the M5 range (040–059), with no collision.

## 2. Gates (run by the reviewer)

| Gate | Result |
|---|---|
| `ruff format --check dps tests tools` / `ruff check dps tests tools` / `mypy dps` | clean (402 files formatted; mypy: 136 source files, no issues) |
| `pytest -m "not integration"` (lane DB up) | **2620 passed**, 10 skipped, 1 xfailed (M4 surface, strict), 93 s. The skips are the OCR mock, tesseract, `mostlyai`/`pandas` and the spaCy model, none of them security tests |
| `DPS_TEST_REQUIRE_SERVICES=1 pytest tests/security tests/test_no_values_in_logs_m5.py tests/test_contract_surface.py` + the touched `db` tests (production gate, connection SSRF API, sealed policy, once-only key, `tests/db/test_rls.py`, `tests/db/test_groups.py`, RLS posture unit, NER DOB, `tests/connectors/test_files.py`, observability) | **316 passed**, 1 xfailed |
| `DPS_TEST_REQUIRE_SERVICES=1 pytest -m "integration and not browser and not ocr and not slow"` on the fsec stack (dps-db, source-pg, MinIO, MongoDB, Oracle Free) | **237 passed**, 1 skipped (conformance: mongo composite keys) |
| `DPS_TEST_REQUIRE_SERVICES=1 pytest -m "slow and not browser and not ocr"` | **12 passed** |
| Frontend | not touched by this stream (no `frontend/` file in the diff), so not run |
| "Each test fails before its fix" | the new tests were run against the **base** `dps/` (`git archive 64c46e3 dps` plus the fsec `tests/`, in scratch). Every F-test file fails there: 8 files error at collection (their new API does not exist yet) and 54 tests fail in the others. The control tests S4/S7 pass on base, as expected (`basecheck.log`) |
| Real Presidio F-04 (finder p06, `en_core_web_sm` 3.8.0) | the date of birth is in clear in **0 of 8** runs, NER on and off (`probes/p06_rerun.out`) |

The implementer's gate numbers reproduce exactly. I stopped the MinIO, MongoDB and Oracle services
again after the integration run (`compose stop`), so the lane is back to dps-db + source-pg up.

## 3. Blocking findings

### B1 (HIGH) — the ReDoS guard (F-02 / S3) only hides one symptom

`dps/engine/detect/regex_guard.py:32` (`check_pattern`, `_walk`) refuses an unbounded repeat inside a
repeat bounded above 3, backreferences, large bounds and empty alternation branches. Python's `re`
backtracks polynomially, O(n^k), on **sequences** of overlapping unbounded quantifiers, and the guard
accepts all of them. `PolicyCustomDetector.scan` (`custom.py:202`) runs them over the whole request
text. `max_text_chars` defaults to **1,000,000**, so the residual-risk sentence "input length caps …
bound the work" (`docs/security/2026-10-11-m5-security-review.md:264`) does not hold.

Evidence (`probes/redos_bypass.out`, `probes/redos_api.out`):

| Pattern (accepted by `check_pattern` and `RecognizerSpec`) | Text | Time |
|---|---|---|
| `\w*\w*\w*\w*\w*!` (17 chars) | 500 × `a` | > 20 s (killed) |
| `.*a.*a.*a.*b` | 500 × `a` | > 20 s (killed) |
| `\d+\d+\d+x` | 2,000 × `1` | > 20 s (killed) |
| `\d*\d*x` | 20,000 × `1` | > 20 s (killed) |

Through the real routes (in-process app, child process killed at 45 s), the pattern
`\w*\w*\w*\w*\w*!` with a 400-character text gave no answer within 45 s on
`POST /api/v1/policies/test-recognizer`, on `/api/v1/detect` and on `/api/v1/obfuscate` (inline
policy). With 50 characters the same call answers in 0.13 s.

A stored policy carrying such a pattern pins a shared CPU on every request that uses it. This is the
exact exploit of F-02.

**The refuted glob candidate is still open.** Review doc §4 says "fixed by S3; adjacent `*` now
collapse". But `path_glob` (`custom.py:128`) builds its own regex, which `check_pattern` never sees,
and only *adjacent* stars collapse. `DeclaredField(path="$.*a*a*a*a*a*a*a*a*a*b")` is accepted, and
`fullmatch` against a 63-character name segment (a valid PostgreSQL column name) takes > 20 s; 40
characters take 6.5 s (`probes/glob_redos.out`).

**Fix:**
- Match policy-supplied patterns in linear time, or under a deadline. Options: `google-re2` (refuse
  lookarounds and backreferences at validation), the `regex` module's `timeout=` with a value-free
  refusal or verification failure, or a subprocess with a time budget. Keep `check_pattern` as a first
  filter.
- Compile globs with a non-backtracking matcher (two-pointer / DP glob match), or cap them at one `*`
  per segment.
- Correct the residual-risk text and the §4 refutation in the review doc.
- Add tests with the four stacked patterns above and the separated-star glob, asserting a bounded
  wall clock (or a refusal).

### B2 (HIGH) — F-01: a production credential can still be re-pointed without `admin`

`dps/connections.py:455-462` (`update_connection`) and `:536` (`delete_connection`). F-01 makes a
`secret_ref` held by a production-tagged connection a *production credential*. The commit says it
"needs admin wherever it is used". Two checks are missing:

- The new create/patch check only fires when a patch **switches to** that ref
  (`patch.secret_ref != current.secret_ref`).
- SEC-3's endpoint rule ("an endpoint change needs credentials in the same request") accepts the same
  `secret_ref` re-supplied.

So on a connection that already holds the production ref and has no `production` tag, a `jobs` key
sends `PATCH {params: {host: <its host>, port: …}, secret_ref: <the same ref>}`. The answer is **200**,
and the next use dials the new host with the production login and password.

Such a holder exists in both cases the implementer's own test names: an admin made it, or it predates
the rule (a `jobs` key created it before the production connection was tagged).

`delete_connection` also checks only the tag, so the same key deletes the holder (204).

Evidence (`probes/test_probe_f01_repoint.py`, `probes/f01_repoint.out`; lane DB, a fake PostgreSQL
listener that asks for a clear-text password; booleans only). Both cases ("jobs key made it before the
production tag" and "admin made it") gave the same results:

| Step | Result |
|---|---|
| Clone of the production ref (the F-01 test) | 403 `admin_scope_required` |
| Re-point without credentials | 403 `connection_endpoint_change_requires_credentials` |
| Re-point re-supplying the same production `secret_ref` | **200** |
| `POST …/test` | 200 |
| What the fake server received | the production login **and password** (`True` / `True`) |
| `DELETE` of the holder | **204** |

Outside `env=local` the attacker needs a host it controls on an allowed (RFC 1918) address. With
`DPS_EGRESS_TLS_REQUIRED` it also needs a certificate the configured CA trusts. The production
password then lets it bypass D3 entirely.

**Fix:** in `update_connection` and `delete_connection`, load `production_refs(tenant_id)` and treat
`holds_production_credentials(current, refs)` like `current.production`: any change (at least
endpoint/params, secret, roles) and any delete needs `admin`. Add the probe as a test: a `jobs` key's
re-point is 403, and its delete is 403. Update HANDOFF §7 (`HANDOFF.md:633`), whose residual ("a `jobs`
key may still *create* a connection that names any of its own tenant's `secret_ref`s") is now partly
false and should say what remains.

### B3 (MEDIUM) — the F-10 fix lets one tenant break other tenants' custom recognizers

`dps/engine/taxonomy.py:153,202` (`MAX_CUSTOM_ENTITIES`, fail-closed) and `dps/engine/detect/custom.py:170,186`.
The registry is process-wide and is filled at detector build time by any request.

- One policy registers up to 50 recognizer names and 500 declared-field names at a non-default
  (HIGH) tier.
- So 10 requests from a key that has only `detect` fill all 5000 slots, in about 50 ms in-process.
- After that, any **other** tenant's request whose policy names a *new* HIGH-tier custom entity fails
  with 400 on `/detect` and on `/obfuscate`, until the process restarts. Names registered earlier keep
  working.
- On a freshly started replica, an attacker who fills the registry first breaks the other tenants'
  **stored** policies on that replica.

The fix chose fail-closed (to avoid downgrading tiers), which is right for privacy. But it turned
"unbounded memory, needs very many requests" into "a deterministic cross-tenant denial of service in
10 requests", and neither the review doc nor the deviation log mentions it.

Evidence (`probes/probe_f10_registry_exhaustion.py`, `probes/f10_registry.out`):

| Step | Result |
|---|---|
| Victim (tenant `tb`) before the attack | 200 |
| Attacker (tenant `ta`, `detect` only) | 10 requests; registry 5000/5000 |
| Victim, already-registered name | 200 |
| Victim, new HIGH name on `/detect` | **400** |
| Victim, new HIGH name on `/obfuscate` | **400** |

**Fix:** make custom tiers request-scoped (or tenant-scoped) instead of process-global. For example,
an overlay in a `ContextVar` that `entity_info` consults, bound when the detector is built (L1 and L2
both have `CURRENT_TENANT` / the policy at hand). Alternatively, keep per-tenant registries with
per-tenant caps, so a tenant can only exhaust its own. Add a two-tenant test like the probe.

## 4. Non-blocking findings (fix when convenient; record what remains)

- **N1 (LOW) — F-08 misses Oracle proxies.** python-oracledb 26 honours `https_proxy` /
  `HTTPS_PROXY` in an Easy Connect string or a descriptor, and dials the proxy first.
  `netguard._dsn_hosts` reads only `HOST=` and the Easy Connect host. So a `dsn` such as
  `tcps://<allowed host>:2484/svc?https_proxy=169.254.169.254&https_proxy_port=80` is **accepted** by
  `check_connection_endpoint`, and the driver dials the metadata address (`probes/f08_oracle_proxy.out`).
  This is reachable only through inline credentials, which the production posture forbids, or through
  operator secret files.
  Fix: parse with the driver's own parser (`oracledb.ConnectParams().parse_connect_string`) and check
  `host` (every address) and `https_proxy`, or refuse proxy parameters.
  Also document the server-directed residuals: MongoDB members advertised by a seed server, and Oracle
  listener redirects.
- **N2 — F-11 residual misstated.** Review doc line 146 says the allow-list "covers" the low-entropy
  refusal. It does not. Within an allowed (entity, profile), any `obfuscate` caller of that tenant can
  still build a dictionary codebook (for example, every date of birth) and invert that data element's
  tokens (such as those an L2 job wrote) without the `reveal` scope. Record this as a residual risk,
  or add a minimum-domain rule.
- **N3 — F-14 residual not documented.** If a worker claims the job between the commit and the refused
  creation audit, partitions may already be written (`in_place`) before the cancel. Those rows are then
  under a key nobody holds. The window is small; add it to the residual-risk list.
- **N4 — defence in depth.** The L2 `BatchTransformer.transform` builds its `ApplyOptions`
  (`batch.py:779`) without `tokenize_allowed`. It is not exploitable today: obfuscate text columns are
  `det_token`-only (`text_method_invalid`), and synthesize is key-less. Pass `tokenize_allowed` anyway,
  so a future span path cannot skip F-11.
- **N5 — S1 residual.** `ConnectionParams._allowed_options` (`jobs_models.py:87`) still echoes
  caller-chosen option *keys*, because a container input is not scanned by `safe_error_message`. Low
  risk: keys, not values.
- **N6 — test naming.** `tests/security/test_headers.py::test_an_explicit_route_header_is_kept_and_health_is_cacheable`
  only checks `/health`. The "explicit route header is kept" half (the `setdefault` semantics, e.g.
  the console's `no-cache`) is not asserted on an `/api/` route.
- **N7 — F-22 deviation wording.** M5-DEVIATIONS says "a v2 hint can never be checked as v1", but
  `bind_v` is caller-supplied. It is harmless: the entity-type rule leaves only the documented
  `None`/`""` v1 ambiguity. The sentence should say that.
- **N8 — §2.6 F1 items outside this round.** The programme plan §2.6 F1 row and RESUME §8 also list:
  T8B (T3 labelling in L2 discovery + L3 classify), the D4 lens of the sweep, and an explicit
  "confirm SEC-2..SEC-5" record. None of them is in this round's work or in the review doc. S5 wiring
  (`POST /scans`, Chromium resolver rules) is correctly recorded as a blocking item of the M4 merge.
  The orchestrator should keep these on the F1 stream.

## 5. What was verified as sound

- **Task 0:** `tests/test_contract_surface.py` keeps every plan name. `validate_run` is listed where it
  moved (`dps.layers.connectors.targets`). The M4 names sit in a strict-xfail test, and missing
  `PARAMS` targets are reported. The walk uses `iter_route_contexts`. Passes.
- **S1 / F-17:** 422s return `{"detail": [{loc, msg, type}], "code": "invalid_request"}`.
  `union_tag_invalid`/`uuid_*`/`url_*` and messages that quote the scalar input are replaced. The
  policy validators name the rule. The engine and the policy store use the same scrubbing. Edited old
  tests assert non-echo; none was weakened.
- **S2 / F-16:** 13 secret settings have `repr=False`. `ApplyOptions`/`TransformContext` hide the
  binding keys and token maps. `KeyMaterial.dek`, `SynthesisSeed.seed` and the surrogate engine do
  not appear in reprs (checked).
- **S5 / F-03 functions:** `check_scan_url` refuses octal/decimal/hex hosts, `127.000.000.001`,
  `::ffff:7f00:1`, `::1`, `0`, mapped/NAT64/6to4 metadata, `fd00:ec2::254`, `100.100.100.200`,
  `192.0.0.192`, names resolving to metadata or loopback, and `user@`/`#@`/`\@` tricks.
  `ok.example.`, upper-case names and ports are normalised (`probes/scan_url_forms.out`).
- **S6:** the scope marker is on both scope dependencies. The walk sees more than 40 routes, and every
  `/api/v1` route answers 401 anonymously.
- **S7, S8, S9, S10, S12:** they hold as designed (tests pass; S7 is DB-backed).
- **S11 / F-05:** `no-store` + `Pragma` on every `/api/` answer (JSON, NDJSON, errors, 404/503), not on
  `/health`.
- **S13 / F-09 / F-15:** the stdlib root logger never goes below WARNING, and Presidio/spaCy are
  capped. DPS's own INFO events still print (structlog). The static and dynamic M5 log scans pass.
- **F-04:** an NER hit yields to an overlapping typed hit of a higher tier, at the single merge point
  (`ensemble._value_hits` → `resolve_overlaps`). Real Presidio gives 0/8 dates of birth in clear.
- **F-06:** a policy open to the runtime login is named (`pg_has_role` … `MEMBER`/`PUBLIC`) and boot is
  refused everywhere. Compose sets `DPS_PG_MIGRATION_USER`.
- **F-07:** the CSV formula guard keeps phone and number shapes as they are.
- **F-08 (apart from N1):** the guard runs at create, patch and use, `/test` answers 422, and RFC 1918
  is allowed.
- **F-11:** it is checked before the first call on every L1 route (JSON, NDJSON, documents) and on
  whole-value L2 columns. Audit runs before the call. The production posture requires the setting.
- **F-12:** key-less methods are enforced at policy validation and in `build_plan`, where the check
  sits before the FPE and external fallbacks.
- **F-13:** retry and group admission compare the key check value recomputed from the presented DEK,
  in constant time. A caller-supplied `kcv` cannot be used to bypass the check.
- **F-14:** an unclaimed job or a group is deleted, and a claimed job is canceled, before the 503.
- **F-18:** only `policy_label` is stored. The body is sealed with the AAD-bound `spec_enc`; evidence
  holds the name and hash only; workers fail closed when the blob is missing.
- **F-19:** every cache is keyed by `(entity, profile, value/token)` with one normalisation.
- **F-20:** the seed is wrapped on `/synthesize` (JSON, NDJSON header, documents), `jobs/run` and
  `job-groups` (`extra="forbid"`). A bad PEM fails before any work.
- **F-21:** `load_kek` registers the fallback in both the app and the worker. `rekey` stays strict.
  Idempotency alternates are compared.
- **F-22:** v2 injective tags. `bind_v` is validated (bool and unknown versions refused). External
  entity types match `^[A-Z][A-Z0-9_]{0,63}$`.

## 6. Plan acceptance

| Item | Status |
|---|---|
| M5 Task 0 | **met** |
| M5 Task 17 | S1, S2, S4, S6, S7–S13 met; S5 functions met (wiring at the M4 merge, recorded); **S3 not met (B1)** |
| Programme plan §2.6 F1 acceptance ("review doc lists every finding with fix + test; `tests/security/*` green") | formally met, but the doc marks F-02 "fixed" and the glob candidate "refuted" (both wrong, B1), and states F-01's rule more broadly than it is built (B2). T8B, D4 and the SEC-2..5 confirmation are outside this round (N8) |

## 7. Re-run

```bash
cd /Users/neelu/document_intelligence/dps-wt-fsec
./lane.sh python /Users/neelu/.cache/dps-build/reports/S3-review-r1/probes/redos_bypass.py
./lane.sh python /Users/neelu/.cache/dps-build/reports/S3-review-r1/probes/probe_redos_api.py
./lane.sh python /Users/neelu/.cache/dps-build/reports/S3-review-r1/probes/probe_glob_redos.py
PYTHONPATH=. DPS_TEST_REQUIRE_SERVICES=1 ./lane.sh pytest -q -s -p tests.fixtures.services \
  -p tests.fixtures.db -p tests.fixtures.sources -c pyproject.toml --rootdir=. \
  /Users/neelu/.cache/dps-build/reports/S3-review-r1/probes/test_probe_f01_repoint.py
./lane.sh python -c "import os,runpy;[os.environ.pop(k) for k in list(os.environ) if k.startswith('DPS_') and not k.startswith('DPS_TEST_')];runpy.run_path('/Users/neelu/.cache/dps-build/reports/S3-review-r1/probes/probe_f10_registry_exhaustion.py',run_name='__main__')"
./lane.sh python /Users/neelu/.cache/dps-build/reports/S3-review-r1/probes/probe_f08_oracle_proxy.py
```
