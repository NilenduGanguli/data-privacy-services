# M5 G4 review, round 1: Tasks 15, 16, 16B (branch `m5`, worktree `dps-wt-m5`)

Reviewer: independent. Commits reviewed: `534ba34` (T15 policy store), `2c219d6` (T16 posture),
`86b1920` (T16B LLM egress). Working tree was clean at the start and is unchanged; nothing in the
repository was modified. Probes are in `/Users/neelu/.cache/dps-build/reports/probe-m5-G4/`.

## Verdict: FAIL (1 blocking issue, made of two defects in the same posture-report function)

All gates are green, and the plan's core claims hold: published versions are immutable, strict
verification is enforced at boot, TLS-only egress is enforced, KEK re-sealing works, and the
egress verifier blocks with nothing sent. The one blocking issue is the runtime posture report
(`GET /api/v1/admin/posture`, `dps/posture.py`):

### B1. The posture report shows other tenants' key and policy names, and misses some tenants' policies

`dps/posture.py` `_stale_keys` (around line 297) and `_tenant_warn_policies` (around line 278).

**(a) Other tenants can see the names (tenant isolation hole).**
- `_stale_keys` lists every stale DB key across all tenants, with name and age.
- `_tenant_warn_policies` lists `<tenant>/<policy name>` for every tenant in the directory.
- Neither filters by the caller's grants.
- A key that is admin for t1 only reads `GET /admin/posture` and sees:
  - `keys_rotated -> t2-ledger-recon is 200 days old`
  - `tenant policy t2/t2-merger-project-zeta must use verify.leakage_check=strict`, once t2 has a `dps_tenant` row
- Probes: `test_p1_*` and `test_p5_*` in `probe-m5-G4/test_probe_g4.py`.
- The same key gets the expected isolation everywhere else:
  - `GET /admin/keys` hides t2's key (`covers()` filter).
  - `GET /policies/<t2 policy>` is 404.
  - Contract §9 scopes queue and keys to "the caller's tenants" / "visible to the caller".
- The posture route breaks that rule. It discloses names only (no customer values), but it is a
  cross-tenant metadata leak.

**(b) `leakage_strict` gives a false "ok".**
- The report walks `tenant_directory()`. Its SQL function (`dps_tenant_directory`, migration 040)
  unions `dps_tenant`, `dps_job` and `dps_connection`, but not `dps_policy`.
- So a tenant that has policies but no tenant row, job or connection is never checked.
- Probe `test_p3_*`:
  - t1 has an active policy with `verify.leakage_check: warn` (published while warn was allowed).
  - The app then runs with `verify_warn_allowed=False`.
  - The report says `leakage_strict: ok, "strict everywhere"`.
  - In `test_p1`, t2's warn policy is likewise missing from the detail.
- Plan T16 says this runtime check "adds every tenant's active policy from the DB" and "covers
  anything older" than the publish-time refusal. It is the only backstop for old warn versions on
  the Admin page, so the false green contradicts the plan.
- Requests that use such a policy are still refused (`verify_warn_disallowed`, verified over HTTP
  for obfuscate, detect, synthesize, NDJSON and validate). This is a reporting defect, not a leak.

**Fix:**
- Pass the principal to `posture_report`, or filter in the route:
  - keep stale keys only where `covers(principal, key.tenant_ids)`;
  - keep tenant policies only for tenants where `principal.can_access(tenant)`;
  - a wildcard key keeps the full view.
- Enumerate tenants from `dps_policy` too, in either of these ways:
  - a new migration `045` that redefines `dps_tenant_directory()` to `UNION SELECT tenant_id FROM dps_policy`;
  - or a SECURITY DEFINER helper that returns tenant ids having a non-strict active version.
- Tests:
  - a t1-only admin sees neither t2 key names nor t2 policy names;
  - a policy-only tenant's warn version makes `leakage_strict` fail.

## 1. Gates (run by the reviewer)

| Gate | Result |
|---|---|
| `./lane.sh ruff format --check dps tests tools` | 301 files already formatted (exit 0) |
| `./lane.sh ruff check dps tests tools` | All checks passed |
| `./lane.sh mypy dps` | Success: no issues found in 126 source files |
| `./lane.sh pytest -q` (only `dps-db` up) | **1826 passed, 83 skipped** (69.8 s) |
| `./lane.sh pytest -q -rs` with `dps-db` + `--profile sources` (source-pg, MinIO, app, worker; image `data-privacy-services:m5` built after HEAD) | **1897 passed, 12 skipped** (202 s). Skips: `[ner]`/presidio and `mostlyai` extras are not installed; azure-ocr-mock (docs profile, not touched by G4); tesseract binary; 3 console checks need the demo connections from `tools/seed`. None of these are G4 paths. |
| G4 test files on their own (`test_policy_store`, `test_policy_diff`, `test_posture_m5`, `test_egress_tls`, `test_rekey`, `test_llm_egress`, `test_reports`, `test_posture`, `test_posture_m2`, `db/test_rls`, `db/test_migrations`, `test_llm_text`, `test_observability`) | 159 passed, 0 skipped (`db` tests ran) |
| `docker compose exec worker python -m dps.rekey --dry-run` (live lane stack) | `{"already_current": 0, "resealed": 0, "rows": 0, "unreadable": 0}`, exit 0 |
| Frontend | not touched by G4 (no `frontend/` paths in the three commits), so npm gates were not needed |
| `docker compose --profile sources down` | done; no lane containers left running |

## 2. Plan / contract / programme acceptance (§2.4 row G4)

| Acceptance | Finding |
|---|---|
| Policy store with immutable versions | **Holds.** Migration 043 matches the plan: a BEFORE UPDATE/DELETE trigger, and the app role has only SELECT and INSERT on `dps_policy_version` (the test asserts InsufficientPrivilege for the app role and RaiseError for the owner with the GUC bound). Publish runs in one transaction under `FOR UPDATE`, so concurrent publishes serialise and the second gets 409 `no_draft`. Version bodies are fully resolved, with no `preset` key, so a pinned version never changes when presets change. `@name[:vN]` resolves at accept time (stored in `dps_job.spec`; the test checks it through the owner connection). `StoredPolicy` labels audit rows `name@vN`. An inline dict that carries `name`/`version` is labelled `custom+inline:<hash>` (probe p4), so it cannot pose as a stored version. Admin audit rows hold counts only: no ticket, note or body (probe p4). Tenant isolation holds through RLS and `tenant_id` filters (cross-tenant 404). |
| Strict verification is a **boot** rule | **Holds.** `posture_problems` → `_strict_verification_problems` (`DPS_VERIFY_WARN_ALLOWED`, `DPS_LLM_EGRESS_CHECK`, non-strict presets/default) is called at the top of both `create_app` and `dps.worker.main` (`assert_production_posture`). The request-time refusal (`PrivacyEngine.policy`, `validate_run`) and the publish refusal are verified over HTTP (probe p3). |
| Secrets from files | Holds: literal values in `SECRET_ENV` are refused (messages name the variable only; the sentinel test passes). |
| TLS-only egress | Holds: the posture URL checks and `require_tls_connection` at create, at PATCH and at use (`open_connector`, `open_target_store`). The postgres connector does honour `sslmode` verify-full/verify-ca (`postgres.py::_ssl`). |
| KEK rotation | Holds: `dps.rekey` covers all three sealed bytea columns in the schema (`dps_job.key_enc`, `spec_enc`, `dps_connection.secret_enc`; grep of `seal_json`/`bytea`). Updates are optimistic (`... AND col = $old`), so a re-run is idempotent. It prints counts only and exits 3 on unreadable blobs. `tenant_directory(include_system=True)` covers connections. |
| Runtime posture report | Shape matches contract §9 and the m5ui `PostureReport` type, with the eleven ids. **B1 above.** |
| Per-tenant LLM exposure counter | Holds: `dps_llm_calls_by_tenant`, `dps_llm_raw_pii_sent_by_tenant` and `dps_llm_exposure_blocked_by_tenant` are bound per request (`begin_call`) and per job task (`Consumer._guard`). Audit counts are added only when the gateway was reached. 044 roll-ups feed the summary tiles (per-tenant test in `test_reports.py`). |
| Egress verifier | Holds. **Counting proof** (`probe_counting_llm2.py`, `httpx.MockTransport` counting every outbound request, stellar provider, strict): a blocked prompt gives **0 outbound requests**; the control (clean prompt) gives 2 (mint + completion). On probe strings (`test_p6`) it catches protected values even when spaced or accented, Luhn cards (spaced or dashed), upper-case e-mail, IBAN, SSN, US and IN phones; shapes and placeholders count 0. The logs carry purpose and count only. A scan of the test and container logs found no sample values. |
| Alert | Alert names and semantics are documented (HANDOFF §5, contract §8.3). The `PrometheusRule` itself is Task 25 by the plan's own text, and T25 is deferred to WP-F2 (programme §1.2). **Not blocking**, but the M5 deviation log's "Deferred" list should say so explicitly. |
| Contract names for other lanes | m5ui `types.ts`/`api.ts` match `PolicyList`, `TenantPolicyDetail`, `PolicyVersionView`, `PolicyDiff` and `PostureReport` field for field. `LlmGateway.complete(protected=())` is optional, so the M3/M4 callers are unaffected. M0's `common.policy_label` is kept. M3 (oracle/mongo branches after the TLS check in `open_connector`) and M4 (`POST /scans` must call `resolve`) are written up as integration notes for WP-I. |

## 3. Deviation log: one ruling per entry

**Task 15**
- `CodedHTTPException` instead of `DpsHttpError` in `policy_refs`: **accept.** Same wire shape, and it keeps Layer 1 free of asyncpg.
- `StoredPolicy` dict subclass: **accept.** It is stronger than the plan (stops a caller from spoofing the label).
- `resolve` / `resolved_policy(mode=)` split and 503 `db_unavailable`: **accept.**
- NDJSON paths resolve: **accept**, tested.
- `publish(settings=)`: **accept.**
- v1 named after the tenant policy: **accept.**
- Draft stored resolved: **accept.**
- Extra codes (`policy_exists`, `invalid_preset`, `version_not_found`, `invalid_change_ticket`/`invalid_note`): **accept.**
- `schema/*` takes no policy: **accept** (verified: `schema.py` passes `policy_name="-"`).
- M4 `scans` deferred to WP-I: **accept**, logged as an integration note.
- `policies_latest_pct` computed: **accept.** Nit: an inline dict with a stored name and version counts as "pinned".

**Task 16**
- KEK rule only with `DPS_PG_HOST`: **accept.** It matches contract §10 and does not refuse L1-only deployments.
- Short-KEK case moved to Settings construction: **accept.**
- Audit/embedded-worker rules not repeated: **accept.** M2 enforces them with `pg_host`, and the parametrised cases still fire.
- `non_strict_policies` uses `preset_names()`: **accept.**
- `posture_report` details: **accept, except B1.** The entry says "every directory tenant", which is accurate, but the directory misses policy-only tenants, and grants are not applied.
- `validate_run` also refuses a policy-level warn: **accept** (stronger than planned).
- `ConnectorError("tls_required")` at use, the s3 target check, `engine_unavailable` before credentials: **accept.**
- `/admin/posture` needs no DB: **accept.**
- `rekey` blob-level counts and exit 2: **accept.**
- HANDOFF section placement: **accept.**

**Task 16B**
- `residual_values` without a `Detector` argument (T1 scanners plus shape and short-word guards): **accept.** It is an internal signature, and the probes show sound coverage.
- `LlmEgressBlocked` in `egress.py`: **accept.**
- `record_llm(blocked=True)` counts the block only: **accept.**
- Binding in `begin_call`: **accept** (wider coverage).
- `schema_profile.propose_spec` is the real proposer: **accept.**
- No L2 LLM handler on this lane: **accept.**
- Engine report `llm.raw_pii_sent` stays 0: **accept for now**, as logged; only `warn` can send, and posture forbids warn in staging/prod.
- Test adjustments: **accept.**

## 4. Non-blocking findings and nits

1. **N1: a 500 on an out-of-range integer.** `GET /policies/{name}/versions/2147483648` (or larger) and `diff?from=99999999999` return 500 `internal_error`, because asyncpg raises `DataError` and `to_http` falls through to the 500 branch. It is value-free, but it should be 404/422: bound `version`/`from` to `le=2147483647` (or `le=999999` like `to`).
2. **N2: publish has no optimistic check against the reviewed draft.** Admin B can overwrite the draft after admin A reviews it, and A then publishes B's body. Consider sending the draft's `draft_updated_at` or hash with publish, and answering 409 on a mismatch.
3. **N3: the oracle TLS rule is a substring test.** It accepts a secret DSN that merely contains "tcps" (for example a service name `tcps_svc` over `tcp://`). It is plan-mandated and the oracle connector is M3, so at WP-I parse the DSN scheme/protocol instead.
4. **N4: secrets in a `.env` file are not caught.** `SECRET_ENV` checks only `os.environ`, but `Settings` also reads `.env` (`env_file=".env"`), so a literal secret there passes the files-only rule. Consider refusing a present `.env` file (or `env_file` itself) in staging/prod.
5. **N5: the immutability trigger has no TRUNCATE leg.** It is row-level BEFORE UPDATE/DELETE only. The app role has no TRUNCATE grant, so only the owner could do it. Optionally add a statement-level BEFORE TRUNCATE trigger; test fixtures that truncate would then need the owner to disable it.
6. **N6: the alert manifest is deferred without a log entry.** The `DpsRawPiiSentToLlm` / `DpsLlmExposureBlocked` `PrometheusRule` lives in deferred T25 (F2); add that to "Deferred to final stage" in `M5-DEVIATIONS.md`.
7. **N7: a placeholder can hide a value.** The verifier strips anything shaped like `{TYPE_<digits>}`, so a value already written in that shape in the caller's input (for example `{SSN_123456789}`) is not counted. This is contrived, but stripping only placeholders the caller actually issued would close it.
8. **N8: blocks without a bound tenant.** `record_llm` counts a blocked prompt for tenant `-` when called outside a request or job (by design, documented).
9. **N9: calls are counted before they complete.** `record_llm` counts a call when the verifier lets it through, before the provider answers; a failed provider call still counts as a call. This is fine for exposure accounting.

## 5. Evidence index (probe directory)

- `static.txt`: the ruff and mypy output.
- `pytest-unit.txt`: the run with `dps-db` only.
- `pytest-integration.txt`: the run with the sources profile up.
- `test_probe_g4.py`, which covers:
  - p1/p5: cross-tenant posture disclosure;
  - p2: integer overflow → 500;
  - p3: a warn version under strict, plus the false `leakage_strict`;
  - p4: audit value-freedom and labels;
  - p6: the verifier string cases.
- How to run `test_probe_g4.py`:
  `./lane.sh pytest -q -s -p tests.conftest --rootdir=. -c pyproject.toml <probe>`
- `probe_counting_llm2.py`: counting transport; blocked prompt → 0 outbound requests, control → 2.
- `probe_counting_llm.py`: an earlier attempt using a plain-http counting server. The stellar
  provider failed before any request (the test COIN credentials were not base64), so its control
  is not meaningful; superseded by `probe_counting_llm2.py`.
