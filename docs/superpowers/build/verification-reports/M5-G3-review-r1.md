# M5 G3 review, round 1 (Tasks 9 to 14)

- **Reviewer:** independent review. I did not rely on `M5-G3-impl.md`.
- **Working copy:** `/Users/neelu/document_intelligence/dps-wt-m5`, branch `m5`, HEAD `6392800`, clean tree.
- **Commits reviewed:** `f2c3ad2` (T9), `f32caed` (T10), `9da77d0` (T11), `23ddb0b` (T12), `5906e0f` (T13), `6392800` (T14).
- **Probes:** `/Users/neelu/.cache/dps-build/reports/probe-m5-G3/`. I modified no repository file.

## Verdict: FAIL, with one blocker

Every gate is green. Keys, tenants, quotas, the admin API, roll-ups and retention match the plan, the contract and
programme-plan row G3. They are free of leaks:
- No raw key or sha256 appears in any log, response or audit row.
- No customer value reaches the reports, the audit JSON, the CSV or the PDF.
- I found no tenant-isolation hole that the posture check does not already refuse in staging/prod.
- No grant escalates scopes or tenants.

One defect is blocking: **the evidence PDF crashes (HTTP 500) for any synthetic pack with 58 or more numeric
columns.**

## 1. Gates (run by me, lane wrapper, this lane's `dps-db` + `source-pg` + `minio`)

| Gate | Result |
|---|---|
| `./lane.sh ruff format --check dps tests tools` | 290 files already formatted (`--check`, nothing modified) |
| `./lane.sh ruff check dps tests tools` | All checks passed |
| `./lane.sh mypy dps` | Success: no issues found in 121 source files |
| `./lane.sh pytest -q -rs` (full, incl. `db` integration) | **1820 passed, 12 skipped** in 202 s. Log: `probe-m5-G3/pytest-full.log` |
| G3 suites + M2 retention/RLS/migrations with `-s` (for the log scan) | 103 passed (`test_auth_db`, `test_keys_cli`, `test_tenants`, `test_quota`, `test_admin_api`, `test_reports`, `test_retention_m5`, `test_evidence_pdf`, `tests/db/test_retention.py`, `test_rls.py`, `test_migrations.py`). Log: `probe-m5-G3/g3-tests-s.log` |
| Frontend | Not applicable: G3 changes no frontend file |

The 12 skips are the same as in G2 and none of them is a G3 test:
- console (3): no app container;
- OCR mock and tesseract (5);
- `mostlyai` (2): the `synth-ml` extra cannot be installed here because it needs torch;
- presidio and spaCy (2).

`tests/db/test_retention.py` (M2) is unchanged and green. `dps/worker.py` is untouched. I ran
`docker compose --profile sources down` afterwards, which touched only `dps-wt-m5`.

**Log scan** (`g3-tests-s.log` + `probe.log`, both with app logs on stdout):
- raw-key pattern `dps_[A-Za-z0-9_-]{40,}`: 0 hits;
- 64-hex strings (key hashes or pack hashes): 0;
- probe sentinels (name, SSN, e-mail, PAN): 0.

## 2. Blocking

### B1: the evidence PDF crashes for wide synthetic tables (Task 14)

- **Where:** `dps/layers/reports/pdf.py::_corr`, `width = 120 / max(1, len(names) - 1)` mm per column.
- **What happens:** with 58 or more numeric columns, a column gets narrower than the 3 pt + 3 pt cell padding. reportlab
  then raises `ValueError` ("flowable given negative availWidth").
- **Effect:** `GET /evidence/{id}/pdf` answers 500 `internal_error`. The error is value-free.
- **Why the input is realistic:** `dps/engine/metrics.py` puts **every** numeric column into
  `fidelity.corr_matrix`, with no cap. A bank table with around 60 numeric columns, for example monthly balances, is
  ordinary.
- **Deviation log:** it says the renderer "never trusts the pack's shape". Here it does trust it.
- **Origin:** the plan's code has the same formula, but Task 14 and the spec need a PDF for every pack.
- **Probes:**
  - `p8_isolate.py`: 40 columns render; 60 raise.
  - `p8b_threshold.py`: the first failing count is 58, whatever the names are.

**Fix:**
- Above about 20 columns, do not render the lower-triangle matrix.
- Render instead the top-k largest pair deltas as a table, or write "matrix omitted: N columns; max delta X, mean Y"
  (`corr_max` and `corr_delta` are already in the pack).
- Clamp every computed column width to a minimum of about 8 mm.
- Add a test that renders a pack with 80 numeric columns and with a linked `fidelity.tables` of the same width.

## 3. Checks that passed (with evidence)

**Keys (T9).** These tests passed: hash-only storage, expiry checked between refreshes, rotation with grace, grace 0
disabling the key, a rotated key never re-enabled, and a stale snapshot failing closed while static keys keep working.

Probe P7 ran 4 concurrent rotations of one key:
- 1 succeeded and 3 were refused with `key_rotated`;
- exactly 1 successor exists;
- the table holds the old hash and no raw key.

The no-escalation rules hold:
- scope: `*` is granted only by a `*` holder, and a scope only by a key that holds it;
- tenants: `*` is granted only by a `*` holder, and only granted tenants can be named;
- `_system` and malformed tenant ids are refused;
- rotate and enable need the key's scopes, and disable needs its tenants.

A tenant admin sees only keys within its grants. The CLI prints one JSON document and writes nothing key-like to
stderr. It writes `_system` audit rows.

**Tenants and quotas (T10).**
- The usage counter runs in the same transaction as the fenced checkpoint; a fenced-out call counts 0.
- The claim CTE skips tenants that are over quota.
- Run admission answers 429 `daily_row_quota_exceeded`, except for an idempotent replay.
- The directory returns ids only. `acquire(None)` sees 0 `dps_job` and 0 `dps_connection` rows.
- The RLS posture check accepts exactly `owner_directory` and `worker_usage`.
- Host errors are value-free (`hide_input_in_errors`; the message gives the entry index only).
- The retention re-stamp is atomic. The `dps_evidence` guard trigger allows `expires_at` to change.

**Admin API (T11).**
- All 10 routes need the admin scope; without a key they answer 401.
- The raw key appears in the 201 response only, with `no-store`.
- Audit counts carry no ids or names.
- Audit search runs one RLS query per tenant, with LIKE-escaped `action`/`q` and validated cursor and window.
- The CSV has a formula guard.
- `tenants` is checked against the caller's grants; `_system` is visible to wildcard keys only.

**Roll-ups and reports (T12). Probe P5 used the real L1 path and real audit rows.**
- Setup: 3 × `/obfuscate` + 1 × `/detect`.
- Audit `methods_used` without `keep` = 12, and `summary.tiles.values_protected` = 12. The match holds because
  M2's `col:`/`fallback:` counters are excluded.
- `by_mode` = `{obfuscate: 3, detect: 1}`.
- I compared the sentinel values against 7 outputs: summary, jobs, api-rollups, the roll-up document JSON, the roll-up
  PDF text, the audit JSON and the audit CSV. **0 occurrences.**
- All roll-up SQL is parameterised and runs under the tenant GUC.
- The advisory lock is tested.

**Retention (T13).**
- There is still one scheduler (`ensure_retention_job`): 4 concurrent calls create 1 job, and there is no
  `RetentionScheduler`.
- M2's five counter keys stay, and the M5 keys appear only when non-zero.
- `metrics.retention` and the `retention.run` audit row are written.
- The admin route needs a wildcard-tenant key.
- `dps_audit_log` is never touched.

**PDF (T14), apart from B1.**
- Every Paragraph string is XML-escaped. A `<link href>` in a pack field or in column params produces no `/URI` and no
  `/Annots` (P8, 40 columns).
- The PDF is rendered once and stored as an `evidence_pdf` artefact. A concurrent loser removes its own copy.
- The route follows the evidence scope rule. A different tenant gets 404, and `pdf_disabled` gives 404.

## 4. Deviation log: my judgement

| Entry | Judgement |
|---|---|
| T9: `invalid_tenant` for `_…`/malformed ids; `covers()`; grant check before state in rotate; `refresh()` binds the app's pool and keeps `last_used` on failure; `run()` catches `Exception`; `schema_ident` | Accept (stricter or more robust) |
| T9: refresher starts after the job platform and stops before the pools; lazy import; "no keys" warning | Accept |
| T9: tests avoid `migrated_db` on the app loop; CLI in `tmp_path`; migration pin split by range | Accept |
| T10: `registered_hosts ADD COLUMN IF NOT EXISTS` in 040 | Accept. **Integration:** M4's `031_tenant_hosts.sql` (in `dps-wt-m4`) is a plain `ADD COLUMN`. Make it `IF NOT EXISTS` at the merge, or recreate the lane DBs, as the log says. M4 reads lower-cased bare hosts, which matches `normalize_host`. |
| T10: `owner_directory … TO CURRENT_USER` | Accept with a note (N1) |
| T10: `hide_input_in_errors`; `null` resets an override; evidence `expires_at` re-stamped; 403 before 409; uncached quota at accept; checkpoint `RETURNING tenant_id` in a transaction; no quota refusal for replays | Accept |
| T11: `deps.audit_action` instead of `audit_admin`; refresh on create/enable; 422 `invalid_grace`; `/admin/queue` without the worker pool (same row shape, contract updated); `tenants=*` = directory + grants; `_system` audit readable by wildcard keys; window/cursor 422s | Accept |
| T12: no duplicate index (007 already has `dps_audit_tenant_ts`); `:`-keys excluded (P5 confirms the numbers); `::numeric::bigint`; schema in the lock key; trend/posture/risk_flags handling; zero-filled sweeps; evidence `q` exact/substring; roll-up doc hash without `generated_at` | Accept |
| T13: M5 counters only when non-zero (keeps M2's test unchanged); `on_tenant` fencing; `tenant_directory(worker=True)`; no refresh on dry run or for pseudo-tenants; wildcard key for `/admin/retention/run`; scan-cascade assertion conditional until M4 merges; audit partitions created in the test | Accept. Re-run `test_expired_jobs_their_objects_and_scan_rows_are_deleted` after the M4 merge so that the scan part executes. |
| T14: `_num()` defensive formatting; linked/DP rendering; canvas footer folded, not escaped; fixed regulatory wording; `evidence_pdf_router`; full pack in the route test; HANDOFF §4 | Accept, but the "never trusts the pack's shape" claim is false for wide matrices (B1) |

## 5. Non-blocking findings (fix or record for the owner)

**N1: `owner_directory TO CURRENT_USER` in the migration-login fallback.**
- **Probe P1** (`p1_fallback_owner_directory.py`): with `DPS_PG_MIGRATION_USER` unset (HANDOFF: falls back to
  `DPS_PG_USER`), the app login itself is the policy's target. The app bound to tenant `beta` read alpha's 1 job row and
  1 connection row.
- **Why it is not blocking:** M2's `assert_rls_posture` already reports that configuration ("runtime role has direct
  privileges on audit partitions"), so staging/prod refuse to boot. In `local` it is only a warning.
- **Recommendation:** have the posture check also fail when `current_user` (or a role it is a member of) is in
  `pg_policies.roles` of an `owner_directory` policy.

**N2: roll-ups of tenants outside the directory never age out.**
- **Probe P6:** report rows 500 days old for a tenant with no `dps_tenant` row, job or connection (an L1-only tenant
  that opened `/reports/summary`) stay after a real `run_retention`: 2 rows left. A tenant with a record: 0 left.
- **Status:** this follows the plan (the tenants come from the directory). HANDOFF §4 nevertheless says such rows "are
  deleted by the retention job".
- **Fix:**
  - add `dps_report_refresh` and `dps_tenant_usage` owners to the tenant list of `_age_out` (worker pool), or
  - run the age-out as one cross-tenant statement per table on the worker pool.

**N3: key lifetime is not bounded by the creator's (probes P2 and P3).**
- A 1-day admin key can mint a 365-day key.
- A key in its rotation grace window can still mint new wildcard keys (201).
- This is not an escalation of scope or tenant as the plan defines it. It does mean that a grace rotation does not
  contain a leaked admin key.
- **Recommendation:**
  - cap `expires_at` of a minted key at the creator's own expiry (DB keys);
  - refuse admin mutations by a key whose `rotated_at` is set;
  - document "use `grace_hours=0` for a compromised key".

**N4: a tenant-scoped admin can lift its own quota and caps (probe P4).**
- `PATCH /admin/tenants/qa` with a key granted only `qa` sets `daily_row_quota=null` and `max_running=256`: 200.
- The spec and contract do not tier admins, but quotas are usually a platform control.
- **Owner decision:** require a wildcard-tenant key for quota and cap fields.

**N5: audit failure after a successful key mutation.**
- `create`/`rotate` commit first and then audit. A strict-sink refusal (503) loses the raw key that was just minted.
- With `grace_hours=0` the old key is also already disabled.
- The CLI has the same ordering: key first, audit insert second.
- It is recoverable: rotate the successor. A better design writes the audit row in the same transaction.

**N6: CSV guard gaps.**
- `csv_cell` does not prefix `" =1+1"`, `"\n=1+1"` or the full-width `"＝"` (P9).
- No audit column the guard covers is attacker-controlled today: route templates, regex-validated names and ids,
  fixed modes. This is defence in depth only.

**N7: test gaps.**
- The `/admin/retention/run` route is not in the `test_every_admin_route_requires_admin` 403/401 walk.
- No test covers the PDF with a wide matrix (B1).
- The scan-cascade retention test currently skips its scan part on this lane.

**N8: `run_retention` loses counts on a later failure.**
- If `_age_out` or `_refresh_recent` raises a `PostgresError` after `_sweep` deleted rows, that tenant's counts are
  dropped from the totals (`continue` comes before the additions).
- This affects reporting only.

**N9: reports visible to detect-only keys.**
- `/reports/summary` and `/reports/jobs` with only the `detect` scope expose L2 job ids, object names and failed-sweep
  column paths. This follows the plan and is value-free.
- **Owner decision:** whether a pure L1 key should see L2 job metadata.

## 6. Probe index (`/Users/neelu/.cache/dps-build/reports/probe-m5-G3/`)

- `pytest-full.log`: the full suite.
- `static.log`: format, lint and types.
- `g3-tests-s.log`: the G3 suites with stdout, used for the log scan.
- `p1_fallback_owner_directory.py`: N1, run as `PYTHONPATH=. ./lane.sh python …`.
- `test_probe_g3.py` + `probe.log`: P2 to P9, run as `./lane.sh pytest -c pyproject.toml --rootdir=. -p tests.conftest -s <file>`.
- `p8_isolate.py`, `p8b_threshold.py`: B1.
