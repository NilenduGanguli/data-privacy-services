# M5 G4 review, round 2: Tasks 15, 16, 16B (branch `m5`, worktree `dps-wt-m5`)

Reviewer: independent. HEAD `791504a` (the r1 fix) on top of `534ba34` (T15), `2c219d6` (T16) and
`86b1920` (T16B). The working tree was clean at the start and is still clean; no repository file was
modified. Round-2 probes and outputs are in `/Users/neelu/.cache/dps-build/reports/probe-m5-G4/r2/`.
The fix report (`M5-G4-fix-r1.md`) and the implementation report were not relied on: every claim
below was re-checked by reading code and running it.

## Verdict: PASS (0 blocking)

- Round 1's only blocking issue (B1: the posture report leaked other tenants' key and policy names,
  and missed policy-only tenants) is fixed. The fix is correct, tested, and does not open new holes.
- All gates are green, including the integration run against this lane's containers.
- The G4 acceptance row of the programme plan holds:
  - the policy store keeps immutable versions;
  - strict verification is a boot rule;
  - each tenant has an LLM exposure counter;
  - an egress verifier runs before every LLM call;
  - the alert is documented, and its manifest is formally deferred to T25 / WP-F2.
- The non-blocking findings are listed in §4.

## 1. Gates (run by the reviewer)

| Gate | Result |
|---|---|
| `./lane.sh ruff format --check dps tests tools` | 301 files already formatted (exit 0) |
| `./lane.sh ruff check dps tests tools` | All checks passed (exit 0) |
| `./lane.sh mypy dps` | Success: no issues found in 126 source files |
| `./lane.sh pytest -q -rs` (only `dps-db` up) | **1828 passed, 83 skipped** (71.8 s) |
| `./lane.sh pytest -q -rs` (`dps-db` plus `--profile sources`: source-pg, MinIO, app and worker, all healthy) | **1899 passed, 12 skipped** (207.7 s) |
| G4 test files run alone (`test_policy_store`, `test_policy_diff`, `test_posture_m5`, `test_egress_tls`, `test_rekey`, `test_llm_egress`, `test_reports`, `test_posture`, `test_posture_m2`, `db/test_rls`, `db/test_migrations`, `test_llm_text`, `test_observability`, `test_tenants`) | **168 passed**, 0 skipped |
| Live posture report inside the app container (`posture_report(get_settings())`) | `rls_posture` ok ("FORCE + tenant_isolation"), so the 045 owner policy is accepted; `migrations_verified` ok ("ledger matches the image"). The other items fail as expected for a local stack (bootstrap key, stub LLM, no KEK file). |
| `docker compose exec worker python -m dps.rekey --dry-run` | `{"already_current": 0, "resealed": 0, "rows": 0, "unreadable": 0}`, exit 0 |
| Frontend | not touched by G4 (no `frontend/` paths in the four commits); npm gates not needed |
| `docker compose --profile sources down` | done; 0 lane containers left |

Notes on the runs:
- **Image freshness.** The image `data-privacy-services:m5` was built before the fix commit's
  timestamp. Its `dps/**/*.{py,sql,yaml}` files are byte-identical to HEAD, compared per file by
  sha256 (`r2/img.sums` against `r2/local.sums`). So the integration run exercised the fixed code,
  including migration 045.
- **The 12 skips.** None is on a G4 path.
  - The `mostlyai` and presidio/`[ner]` extras are not installed (synth-ml and ner).
  - The azure-ocr-mock is in the docs profile, which G4 does not touch.
  - There is no tesseract binary on the host.
  - Three console checks need the demo connections from `tools/smoke_test.py`.

## 2. Round-1 B1: verification of the fix (`791504a`)

### (a) Scoping

- **How it works.**
  - `posture_report(..., principal=)` passes the caller down.
  - `_tenant_warn_policies` skips tenants the caller cannot `can_access`.
  - `_stale_keys` keeps only keys the caller `covers()`, the same rule as `GET /admin/keys`.
  - The route passes `principal_of(request)`.
- **Probe `r2a`** (`r2/test_probe_g4_r2.py`; output in `r2/probe-r2.txt`). Setup:
  - tenants t1, t2 and t3 each have a warn policy, and t3 has policies only;
  - stale keys are granted t1, t2, `*`, and t1+t2;
  - there are two admin keys: a t1-only key, and a t1+t2 key.

  Results:

  | Caller | `keys_rotated` | Tenant policies in `leakage_strict` |
  |---|---|---|
  | t1-only admin | `t1-stale` only (not t2, not t12, not the `*` key) | `t1/t1-secret-pol` only |
  | t1+t2 admin | t1, t12 and t2 keys; not the `*` key | t1 and t2; not t3 |
  | wildcard | all four keys | t1, t2 and t3 |

  - The raw admin key never appears in any response.
  - `GET /admin/tenants`: the wildcard list includes t3; the t1-admin list includes neither t2
    nor t3 (filtered by grants, unchanged).
- **Round-1 probes p1 and p5 re-run** (`r2/probe-r1-rerun.txt`): the t1 admin sees no t2 policy name
  and no t2 key name.
- **Tests that bite.** `test_posture_shows_a_single_tenant_admin_only_its_tenants` asserts that the
  scoped body has no `t2-` or `t2/` and that `keys_rotated` is exactly the t1 key, and that the
  wildcard view has both tenants' names.

### (b) Policy-only tenants

- **Migration 045** does two things:
  - It adds an owner SELECT-only `owner_directory` policy on `dps_policy`. This is the same pattern
    as 040 on `dps_job`/`dps_connection`, which G3 accepted.
  - It redefines `dps_tenant_directory()` with `UNION SELECT tenant_id FROM dps_policy`. The
    function is still SECURITY DEFINER, keeps the pinned `search_path`, returns ids only, and keeps
    REVOKE PUBLIC / GRANT `dps_app_rw`. `CREATE OR REPLACE` keeps the existing grants.
- **No new cross-tenant read path.** `dps_tenant_directory` is the only SECURITY DEFINER function in
  the migrations.
- **RLS posture.** `ALLOWED_EXTRA_POLICIES["dps_policy"]` is updated, and the live
  `assert_rls_posture` passes.
- **Probes.**
  - Round-1 p3 re-run: `leakage_strict: False … tenant policy t1/gdpr-pseudonymise must use
    verify.leakage_check=strict`.
  - `r2a`: the wildcard view lists policy-only t3.
- **Request-time refusals are unchanged.** All five paths answer 422 `verify_warn_disallowed`: obfuscate,
  detect, synthesize, NDJSON and validate. Publishing a warn draft under strict is also 422.
- **Directory readers affected by 045** (admin tenant list, `*` audit expansion, queue, retention,
  rekey): each still filters by grants or runs as a platform operation. Seeing policy-only tenants
  is correct for all of them.
- **Migration numbering.** 045 is inside M5's 040–059 range. No other lane has a 04x file: m3, m4
  and m4ui end at 010 or 031.

## 3. Independent re-check of the G4 acceptance (programme plan §2.4 row G4)

| Acceptance | Finding |
|---|---|
| Policy store with immutable versions | **Holds.** <br>• **Migration 043 matches the plan.** A BEFORE UPDATE/DELETE trigger refuses every change, and the app role has only SELECT and INSERT on `dps_policy_version`. <br>• **Publishing is one transaction under `FOR UPDATE`.** Probe `r2b` fires four concurrent `publish()` calls on one draft: 1 succeeds, 3 get 409 `no_draft`, the versions are exactly [2, 1], and the active version is 2. <br>• **Stored bodies are fully resolved.** An `@name[:vN]` reference resolves at accept time to a `StoredPolicy`, labelled `name@vN`. An inline dict with `name` and `version` is labelled `custom+inline:<hash>`, so it cannot pose as a stored version (round-1 p4, re-run). <br>• **Admin audit rows hold counts only.** They contain no ticket and no note (p4 re-run). <br>• **Cross-tenant reads are 404** (p1 re-run). |
| Strict verification is a **boot** rule | **Holds.** <br>• `posture_problems` → `_m5_problems` → `_strict_verification_problems` checks `DPS_VERIFY_WARN_ALLOWED`, `DPS_LLM_EGRESS_CHECK` and the non-strict presets/default. <br>• It is reached from `assert_production_posture` in `create_app` and in the worker. <br>• The request-time and publish-time refusals were verified over HTTP. |
| Secrets from files | **Holds for the specified rule.** Literal `DPS_<SECRET>` environment variables are refused, and the messages name the variable only. Two bypasses remain, both non-blocking: a lower-case variable name (new, N10) and a `.env` file (N4). |
| TLS-only egress | **Holds.** <br>• **Endpoint settings:** the URLs in `EGRESS_URLS` must be https, and the CA bundle must be set. <br>• **Connections:** `require_tls_connection` runs at create, at PATCH and at use (`open_connector`, `open_target_store`). <br>• **Postgres:** `sslmode` is applied by the connector. |
| KEK rotation | **Holds.** <br>• **Coverage:** `dps.rekey` covers the only three sealed bytea columns in the schema (`dps_job.key_enc`, `dps_job.spec_enc`, `dps_connection.secret_enc`). <br>• **Crash-safe:** each update is optimistic, so a crash half-way is completed by a re-run, and a blob is never written twice. <br>• **Output:** counts only; it exits 3 when some blobs are unreadable. <br>• **Live dry-run:** exit 0. |
| Runtime posture report | **Holds after the fix** (§2). The shape is unchanged, so it still matches the m5ui `PostureReport` type. |
| Per-tenant LLM exposure counter | **Holds.** <br>• **Binding:** the tenant is bound per request (`begin_call`) and per job task (`Consumer._guard`). <br>• **Audit:** LLM counts are added only when the gateway was reached. <br>• **Roll-ups:** 044 adds the per-tenant LLM columns, which feed the summary tiles. |
| Egress verifier: **no-submission proof with a real counting server** | **Holds.** Probe `r2c` drives the full HTTP path against a real threaded HTTP server on 127.0.0.1 that counts every request (COIN mint and chat completion). Settings: stellar provider, strict, `POST /api/v1/synthesize` with `llm_rewrite`. <br>• **Control** (clean prompt): the server got **2** requests (mint and completion). <br>• **Forced leak** (a wrapper appends a protected original to the template): the server count stays at **2** (0 new requests). <br>• **Counters and report:** `dps_llm_exposure_blocked_by_tenant{tenant_id="t7"}` rose by **1**; `report.llm` reads `calls: 0`, `fallback: egress_blocked`. <br>• **Audit counts:** `{llm_calls: 1, llm_blocked: 0}` for the control and `{llm_calls: 0, llm_blocked: 1}` for the blocked call. <br>• **No values anywhere:** across all request bodies the server ever received, the response, the audit events and the captured stdout logs, a scan finds **0** occurrences of the source e-mail or phone. The log lines carry purpose and count only. <br>• **Verifier string cases** (round-1 p6 re-run): it catches protected values (plain, spaced or accented), Luhn cards (spaced or dashed), upper-case e-mail, IBAN, SSN, and US and IN phones. Shapes and placeholders count 0. |
| Alert | **Accepted as deferred.** The alert names and semantics are documented (HANDOFF §5, contract §8.3). The `PrometheusRule` is Task 25, which programme §1 defers to WP-F2. `M5-DEVIATIONS.md` "Deferred to final stage" now says so, which closes round-1 N6. |
| Contract names for other lanes | **Unchanged and intact:** <br>• the m5ui `PolicyList`, `TenantPolicyDetail`, `PolicyVersionView`, `PolicyDiff` and `PostureReport` shapes; <br>• `LlmGateway.complete(protected=())` (optional, so the M3/M4 callers are unaffected); <br>• M0's `common.policy_label`. <br>The contract is updated for 045 (§1, §2.1, §2.2) and for the posture scope (§9). The integration notes for WP-I (M4 `scans` must call `resolve`; M3 oracle/mongo go after the TLS check) still stand. |

## 4. Deviation log: one ruling per entry

**Task 15, Task 16 and Task 16B entries.**
- Every entry is **accepted**, with the same reasoning as in round 1.
- I re-read each entry against the code and found nothing that contradicts it.
- The one round-1 reservation on "`posture_report` details" (the directory and grants) is resolved
  by the fix.

**"G4 review r1 fix — B1".**
- **Accept.** It describes the change accurately.
- It states the one design consequence: a single-tenant admin's `leakage_strict` and `keys_rotated`
  are judged on that admin's tenants only. Contract §9 and HANDOFF §5 document this, and tell the
  reader to use a `*` admin key for the platform-wide view.

**"Deferred to final stage — Task 16B → Task 25".** **Accept.**

## 5. Non-blocking findings and nits

**Carried over from round 1** (the fix report says they are untouched, and I re-confirmed N1):

1. **N1: a 500 on an out-of-range integer.**
   - The cases: `GET /policies/{name}/versions/2147483648` (or `99999999999`) and
     `diff?from=99999999999`.
   - Result: still 500 `internal_error`. It is value-free, but it should be 404/422.
   - Fix: bound `version`/`from` (`le=999999`, like `to`).
2. **N2: publish has no optimistic check against the reviewed draft.** An admin can publish a draft
   that another admin replaced after the first one reviewed it.
3. **N3: the oracle `tcps` check is a substring test on the secret DSN.** M3 should parse the DSN
   protocol at WP-I.
4. **N4: a literal secret in a `.env` file passes the files-only rule.** `Settings` reads `.env`, but
   the rule only checks `os.environ`.
5. **N5: the immutability trigger has no TRUNCATE leg.** Only the owner could truncate.
6. **N7: a value already written in placeholder shape (`{SSN_123456789}`) is stripped and not
   counted.**
7. **N8 and N9 are by design:**
   - N8: a block with no bound tenant is counted as tenant `-`;
   - N9: a call is counted before the provider answers.

**New in round 2:**

8. **N10: a lower-case literal secret variable bypasses the files-only rule.**
   - Pydantic-settings is `case_sensitive=False`, so `dps_tokenizer_token=...` (lower case) is read
     into `Settings.tokenizer_token`.
   - `SECRET_ENV` is checked with `environ.get("DPS_…")`, which is case-sensitive. So the rule does
     not fire. The same applies to M2's `DPS_JOB_KEK` check and to `api_keys_from_file`.
   - Probe `r2/probe_lowercase_env.py`:
     - the setting is read: True;
     - the rule fires: False;
     - the sentinel value is in no message.
   - Only an operator's misconfiguration can trigger it, not an attacker. Same class as N4.
   - Fix: compare upper-cased names, e.g. `{k.upper() for k in environ if environ[k]}`.
9. **N11: the scoped report is by design but can mislead.** A single-tenant admin can see
   `leakage_strict: ok` / `passed 11/11` while another tenant has a warn version. This is documented
   (contract §9, HANDOFF). The m5ui Admin page could add a "scoped to your tenants" hint when the key
   is not a wildcard. Console nit for WP-I.
10. **N12: verifier limits.**
    - Availability: a protected original of 4 or more characters matches anywhere in the
      normalised text. A low-cardinality sample value such as `Male` therefore blocks any prompt
      that contains `female`. Callers fall back to the deterministic path, so this costs
      availability only, never privacy.
    - Coverage: national-format phones are parsed with region `US` only. Numbers without a `+`
      country prefix from other regions are not counted by the T1 part; they are still caught when
      the caller passes them in `protected`.
11. **Outside G4.** In probe `r2c`'s first draft, a person's surname passed through
    `/synthesize` unchanged. The lane runs with `engine=none` (no `[ner]` extra), so person names
    are not detected in Layer 1 text. This is M0's documented detector coverage, not a G4 defect.
    The final probe uses e-mail and phone only.

## 6. Evidence index (`probe-m5-G4/r2/`)

- `static.txt`: ruff format, ruff check and mypy output.
- `pytest-unit.txt`: the run with `dps-db` only (1828 passed, 83 skipped).
- `pytest-integration.txt`: the run with the sources profile, app and worker (1899 passed, 12 skipped).
- `img.sums` / `local.sums`: per-file sha256 of `dps/` in the image and at HEAD (identical).
- `test_probe_g4_r2.py`, output in `probe-r2.txt` (3 passed):
  - `r2a`: the posture scope matrix and directory side effects;
  - `r2b`: four concurrent publishes;
  - `r2c`: the counting-server no-submission proof, with the audit, counter and log value scan.
- `probe-r1-rerun.txt`: round-1 probes p1–p6 re-run on the fixed code (6 passed).
- `probe_lowercase_env.py`: the N10 bypass.
- How to run the probes, from the worktree:

  ```
  ./lane.sh pytest -q -s -p tests.conftest --rootdir=. -c pyproject.toml <probe file>
  ```

  `dps-db` must be up.
