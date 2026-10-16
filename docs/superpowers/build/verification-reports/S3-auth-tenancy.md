# S3 security finding report — lens "auth-tenancy"

Branch `fsec` (= main `64c46e3`: M0–M3 + M5). Worktree `/Users/neelu/document_intelligence/dps-wt-fsec`.
Date 2026-10-14. No repository file was modified. Probes and raw results are under
`/Users/neelu/.cache/dps-build/reports/S3-auth-tenancy/` (probe_routes.py, probe_live.py,
probe_admin.py, probe_posture.py, probe_targets.py; `*-results.json`, `routes.txt`). All probes ran
against this lane's own compose stack (`dps-db` :5483, `source-pg` :5484), on throwaway databases
(`dps_s3probe*`, since dropped). Lane containers stopped afterwards; no other compose project touched.
Keys and values were never printed; outputs are status codes, error codes, counts and booleans.

Scope of the lens: (A) authentication and route scopes — every `/api/v1` route behind `require_scope`;
M5 DB keys, rotation, no privilege/tenant escalation; (B) tenancy and object-level access — every object
route 404s for another tenant; RLS on every tenant table; cross-tenant secret refs. Also the M5 plan
Task 17 S1–S13 items that fall in this lens.

## What is sound (verified, no finding)

- **S6 route-scope coverage.** All 76 `/api/v1` methods are reachable only through `require_scope` /
  `require_any_scope`; an anonymous call to every one returns 401 (`probe_live.py` `S6_anonymous_walk`:
  `not_401 == []`). The only unscoped routes are the public ones (`/health`, `/readyz`, `/metrics`, the
  SPA fallback). NB FastAPI 0.143 keeps included routers as `_IncludedRouter`, so `app.routes` holds only
  2 APIRoutes — a route-walk test must use `fastapi.routing.iter_route_contexts`, not `app.routes`
  (the M5 plan's `test_route_auth.py` snippet iterates `app.routes` and would silently check almost nothing).
- **S7 object-level cross-tenant access.** With a tenant-B key holding every scope, all 27 of tenant A's
  object routes (connections, jobs, partitions, snapshots, job-groups, consistency-check, evidence,
  evidence/html, evidence/pdf, artefacts, policies, policy versions/diff/draft/publish) return **404**
  (`probe_live.py` `S7_cross_tenant.non_404` lists only `PUT …/draft` → 400 `invalid_policy`, i.e. body
  validation fires before the tenant lookup and still never 200s). List filters by another tenant's id
  return 0 rows; a discover/run naming tenant A's connection/snapshot is 404; a tenant-B key sending
  `X-DPS-Tenant: ta` is 403. RLS (`db.acquire(tenant)` GUC + FORCE on every `TENANT_TABLES` entry) is the
  gate and holds.
- **S8 no key escalation.** `check_grant` refuses a tenant admin minting a wider key: `tenant_escalation`,
  `scope_escalation`, `*`-scope all 403 (`probe_admin.py` `tenant_admin_cross_tenant`). A tenant admin sees
  only its own tenants/keys; `/admin/audit?tenants=*` returns only granted tenants; `/admin/retention/run`
  is 403 for a non-wildcard key. DB-key lifecycle is correct: disable takes effect immediately on this
  replica, a rotated key cannot be re-enabled (409 `key_rotated`), grace=0 kills the old key at once.

## Findings

### 1. (HIGH) The production-write admin gate is bypassable by a non-admin `jobs` key via an untagged clone
`dps/connections.py` (create path) + `dps/layers/connectors/targets.py:657` (gate keys off the connection row's `production` tag).

Contract §8.1 / spec D3: `in_place`/`append` writes to a `production`-tagged connection "additionally
require" the `admin` scope. The gate (`validate_run` → `written.production and not principal.has_scope("admin")`)
reads the *target connection row's* tag. A `jobs`-only key cannot write in-place to the tagged connection
(403 `admin_scope_required`, confirmed), **but** it can create a second connection to the same database
with the same credentials and no `production` tag, then run `in_place` through the clone.

Demonstrated end to end (`probe_live.py` `D3_production_gate`): a `jobs`,`detect`-scoped key (no `admin`)
— `jobs_key_patch_prod`/`delete_prod`/`in_place_on_prod_*` all 403 — then `GET /connections/{prod}` returns
`params` and `secret_ref` (`secret_ref_visible_in_GET: true`), `POST /connections` creates `ta-prod-clone`
(201) with those same `params`+`secret_ref` and `roles:[source,target]` and no tags, discover+accept on the
clone, `POST /jobs/run` `in_place` → 202 → job **succeeded**, and the underlying table's `email` column is
rewritten in all 40 rows (`prod_table_rows_before.email_changed: 0` → `after: 40`). A non-admin key thus
performed exactly the irreversible production mutation the admin gate exists to stop.

Known residual: contract §5.3 notes a jobs key "may still create a connection that names any of its own
tenant's secret_refs with an endpoint of its choice" and suggests restricting the key. That note is about
*where credentials are sent*; it does not state that D3's admin-for-production control is defeated by it.
The escalation here is specifically the in-place rewrite of production data by a non-admin key, so the
documented mitigation ("mount fewer refs") does not hold when the tenant legitimately needs that ref for
non-production jobs.

Fix: bind the "production" designation to the resource, not the row — e.g. mark a `secret_ref` (or
host/database) as production in tenant config and require `admin` for any `in_place`/`append` whose
*resolved credentials/endpoint* are production, regardless of the connection row's tag; or require `admin`
to create/patch any connection that reuses a `secret_ref` already held by a production-tagged connection.
Add a test: a `jobs` key that clones a production connection and runs `in_place` is 403.

### 2. (MEDIUM) M5 Task 17 S11 unimplemented: L1 engine responses carrying key material/seeds/revealed originals have no `Cache-Control`
`dps/app.py` `_SecurityHeaders` (no `/api/` no-store rule); `dps/layers/api/routers/transform.py`, `schema.py`.

The M5 plan Task 17 S11 prescribes `Cache-Control: no-store` on **every** `/api/` response (plan text:
`_SecurityHeaders`: `if request.url.path.startswith("/api/"): response.headers.setdefault("Cache-Control","no-store")`,
with `tests/security/test_headers.py`). It was never added — `_SecurityHeaders` sets only nosniff/DENY/
referrer/HSTS. Only the M2/M5 routes that return a key set `no-store` by hand (`/jobs/run`, `/job-groups`,
`/admin/keys*`, artefacts, evidence, reports PDFs). The **L1 engine routes do not**: `probe_admin.py`
`S11_cache_control_L1` shows `cache_control: null` on `POST /obfuscate` (returns `key_material` incl. the
DEK), `/reveal` (returns revealed originals), `/synthesize` and `/schema/synthesize` (return
`synthesis_seed`), and the NDJSON stream (`has_key_line: true`). A shared forward proxy or browser cache on
the bank's network could store a response body containing a DEK or revealed PII.

Also unimplemented from Task 17 (whole pass is absent): `dps/layers/api/validation.py`,
`dps/engine/detect/regex_guard.py`, `tests/security/*`, `docs/security/2026-10-11-m5-security-review.md`,
and the `require_scope` `__dps_scope__` marker. S1/S3/S5 (422-input scrubbing, ReDoS guard, SSRF DNS guard)
are outside this lens but are likewise unaddressed and should be picked up by the injection/SSRF lenses.

Fix: implement the plan's global `/api/` `no-store` in `_SecurityHeaders` (setdefault, so the explicit
per-route ones are unchanged) and add `test_headers.py` asserting it on an engine route and its absence on
`/health`.

### 3. (MEDIUM) RLS posture validator is blind to the `owner_directory` policy's target role → silent cross-tenant SELECT when the migration login equals the API login
`dps/db.py` `evaluate_rls_posture` / `ALLOWED_EXTRA_POLICIES`; migrations `040_tenant_quotas.sql`, `045_policy_directory.sql`.

Migrations 040/045 create `owner_directory` as `FOR SELECT ... USING (true) TO CURRENT_USER` on `dps_job`,
`dps_connection`, `dps_policy`, where `CURRENT_USER` is whoever runs migrations — intended to be the
separate `dps_owner`. But `Settings.pg_migration_login` **silently falls back to the API login**
(`pg_user`/`pg_password`) when `DPS_PG_MIGRATION_USER` is unset. In that (mis)configuration the
`USING(true)` SELECT policy is granted to the runtime API role, and the API role owns the tables, so the
app reads **every tenant's** rows on those three tables.

Demonstrated (`probe_posture.py`, migration-user unset → equals app login): `migration_login_is_api_login:
true`, `owner_directory_policies` all `TO s3p_single_app` (the runtime role), `tables_owned_by_runtime_role:
22`, and under the GUC bound to tenant `tb` the app reads tenant `ta`'s rows: `tb_sees_ta_jobs: 1`,
`tb_sees_ta_connections: 1`. `assert_rls_posture` did **not** name this; it allows `owner_directory` on
those tables via `ALLOWED_EXTRA_POLICIES` **regardless of target role**, and only incidentally returned the
unrelated audit-partition-privilege problem (which makes it fatal in staging/prod, but it is logged as a
warning only in `local`/`dev`, where the cross-tenant hole is then live). `evaluate_rls_posture` never
checks that `owner_directory` targets the migration owner rather than the runtime login.

Fix: in `assert_rls_posture`/`evaluate_rls_posture`, treat an `owner_directory` policy whose role is the
runtime login (or `public`) as a violation — it must be `TO <migration owner>` only; and make the posture
check fail on it independently of the audit-partition check. Alternatively refuse boot when
`pg_migration_login` resolves to the API login while a metadata DB is configured. Add a test with the two
logins coinciding → posture non-empty, naming `owner_directory`.

### 4. (LOW) `pvc` file-export targets are not tenant-namespaced under `DPS_EXPORT_ROOT`
`dps/storage.py` `target_store` (pvc branch): path is `<export_root>/<base_path>`, no tenant segment.

The artefact store namespaces objects by tenant (`t/<tenant_id>/<job_id>/…`), but a `pvc` `files` target
resolves to `<DPS_EXPORT_ROOT>/<connection.base_path>` with no tenant component. Two different tenants whose
`pvc` connections name the same `base_path` write into the **same** directory (`probe_targets.py`
`pvc_same_folder_for_two_tenants: true`). Each tenant still only reaches its own connection row (RLS), so
this needs both tenants to configure a colliding `base_path`, but it means `files` exports are not
tenant-isolated on disk the way artefacts are — a cross-tenant overwrite/disclosure on the shared export
volume is possible by configuration. (Related, out-of-lens → SSRF: an `s3` target whose `secret_ref` has no
key files falls back to boto3's ambient/instance credentials while taking bucket+endpoint from tenant
params — `s3_target_uses_ambient_env_credentials: true`; flag for the SSRF lens.)

Fix: include the tenant id in the pvc export key prefix (`<export_root>/t/<tenant_id>/<base_path>/…`), as the
artefact store does, or validate that a tenant's `base_path` is unique to it.

## Residual / notes
- S6/S7/S8 behaviours are correct even though Task 17's dedicated markers/tests do not exist; the gaps are
  the missing hardening (Finding 2) and the posture blind spot (Finding 3), not the live auth/tenancy path.
- `/metrics` is public and exposed no tenant-labelled series in this run (`public_metrics`: 0 series) — the
  M5 per-tenant LLM counters are not emitted on an idle stack; re-check under load that `/metrics` (public,
  unauthenticated) does not leak tenant ids as label values.
