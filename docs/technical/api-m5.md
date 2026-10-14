# Admin, reports, policy store and tabular synthesis (M5)

M5 adds the routes below to the API of `api-m0.md` … `api-m3.md`, plus new options on the M2 job
routes. Every earlier convention holds: base `/api/v1`, JSON, the `X-API-KEY` header (and
`X-DPS-Tenant` for a key granted several tenants), the error shape `{"detail", "code"}`, list
envelopes `{"items", "next_cursor"}`, and answers that never carry a customer value, a key (except
once, where stated), a password or a URL with credentials. Every route below except
`GET /admin/posture` needs the metadata database (503 `db_unavailable` without it). Mutating admin
and policy routes write one value-free audit event (`admin.<object>.<verb>`, counts only); reads
are not audited.

Examples use placeholders — `$DPS` for the base URL, `$DPS_KEY` for a key — never real values.

```bash
curl -s -H "X-API-KEY: $DPS_KEY" "$DPS/api/v1/reports/summary?days=14"
```

## Routes

| Method · path | Scope | Answer |
|---|---|---|
| `GET /admin/keys` | admin | `{items: [ApiKeyView], next_cursor: null}` — DB and static keys whose tenants the caller covers (never a key or its hash) |
| `POST /admin/keys` | admin | 201 `CreatedKey` with `Cache-Control: no-store` — the raw key, **once** |
| `POST /admin/keys/{id}/rotate` | admin | 201 `{new: CreatedKey, old: ApiKeyView}`, `no-store` |
| `POST /admin/keys/{id}/disable` · `/enable` | admin | `ApiKeyView` |
| `GET /admin/tenants` | admin | `{items: [TenantView], next_cursor: null}` — the tenants the caller is granted |
| `POST /admin/tenants` | admin | 201 `TenantView` |
| `PATCH /admin/tenants/{tenant_id}` | admin | `TenantView` (only the fields sent change; `null` resets an override) |
| `GET /admin/audit` | admin | `{items: [AuditRow], next_cursor}`; `format=csv` streams `text/csv` |
| `GET /admin/queue` | admin | `{queues: [{table, state, n, oldest_s}], workers: [{worker_id, role, version, online, busy, draining, last_seen}]}` over the caller's tenants |
| `GET /admin/posture` | admin | `PostureReport` (no database needed) |
| `POST /admin/retention/run` | admin, key granted `*` | 202 `{job}` — an on-demand retention job owned by `_system` |
| `GET /reports/summary?days=14` | detect | `SummaryOut` (Overview) |
| `GET /reports/jobs?days=14` | detect | `ReportsJobsOut` (leakage sweeps) |
| `GET /reports/evidence?days=30&layer=&outcome=&q=&limit=&cursor=` | jobs (scan packs only with scan too) | `{items: [EvidenceItem], next_cursor}` |
| `GET /reports/api-rollups?days=14` | detect | `{items: [ApiRollup]}` |
| `GET /reports/api-rollups/{day}?action=&format=json\|pdf` | detect | the roll-up document as an attachment (`api-rollup-<day>-<action>.json\|pdf`) |
| `GET /evidence/{id}/pdf` | jobs, or scan for scan packs (the rule of `GET /evidence/{id}`) | `application/pdf` attachment `evidence-<id>.pdf` |
| `GET /policies` | detect | `{presets: [PresetView], tenant: [TenantPolicyView]}` |
| `POST /policies` | admin | 201 `TenantPolicyDetail` — adopt a preset under a name; version 1 is the preset |
| `GET /policies/{name}` | detect | `TenantPolicyDetail` |
| `PUT /policies/{name}/draft` · `DELETE /policies/{name}/draft` | admin | `TenantPolicyDetail` |
| `POST /policies/{name}/publish` | admin | 201 `PolicyVersionView` — the draft becomes the next immutable version and the active one |
| `GET /policies/{name}/versions/{version}?format=json\|yaml` | detect | the published body (JSON), or a `text/yaml` attachment `<name>-v<version>.yaml` |
| `GET /policies/{name}/diff?from=<n>&to=<n\|draft>` | detect | `{from, to, changes: [PolicyChange]}` |

`days` is 1–90 (422 outside). The policy-store routes are registered after M0's
`/policies/presets`, `/policies/validate`, `/policies/plan` and `/policies/test-recognizer`; those
four names can never be tenant policy names.

## Admin

**Keys.** `POST /admin/keys` body `{name, tenant_ids, scopes, expires_in_days}`: `name` matches
`^[a-z0-9][a-z0-9-]{1,62}$`; `tenant_ids` tenant ids or `*`; `scopes` from `detect obfuscate
synthesize reveal jobs scan admin` or `*`; `expires_in_days` 1–`DPS_KEY_MAX_TTL_DAYS`. A key never
grants more than its creator holds (403 `scope_escalation` / `tenant_escalation`). `rotate` takes
`{grace_hours}` (default 24, 0–`DPS_KEY_ROTATION_MAX_GRACE_HOURS`; 0 disables the old key now); a
key is rotated at most once and a rotated or expired key cannot be re-enabled (409). Changes take
effect at once on the replica that served them and within `DPS_KEY_CACHE_SECONDS` on the others.

- `ApiKeyView`: `id, name, key_prefix` (`dps_` + 4 characters), `tenant_ids, scopes, source`
  (`db` | `static`), `status` (`active` | `expired` | `disabled`), `created_by, created_at,
  expires_at, last_used_at, disabled_at, rotated_from, rotated_at, age_days`.
- `CreatedKey`: `{key, record: ApiKeyView}` — `key` appears in this answer only; DPS stores its
  SHA-256 and the 8-character prefix.

```bash
curl -s -X POST -H "X-API-KEY: $DPS_KEY" -H 'Content-Type: application/json' "$DPS/api/v1/admin/keys" \
  -d '{"name": "crm-uat-provisioner", "tenant_ids": ["retail-banking-apac"], "scopes": ["detect", "obfuscate", "jobs"], "expires_in_days": 90}'
```

**Tenants.** `TenantSpec` (create): `tenant_id` (`^[a-z0-9][a-z0-9_-]{0,62}$`), `display_name`
(≤ 120), `retention_days` (1–3650), `max_running` (1–256), `max_queued` (1–100 000),
`daily_row_quota` (≥ 0), `registered_hosts` (≤ 100 hostnames or URLs, stored as lower-case hosts;
no credentials, no wildcards), `note` (≤ 500). `TenantPatch` has the same fields, all optional.
`TenantView`: `tenant_id, display_name, note, overrides` (the stored values, `null` = the settings
default), `effective` (`retention_days, max_running, max_queued, daily_row_quota,
registered_hosts`), `usage` (`running, queued, rows_today` — UTC day), `keys` (counts), `exists,
created_at, updated_at`. Ids starting with `_` are DPS pseudo-tenants and never appear here.

**Audit.** `GET /admin/audit?from=&to=&action=&layer=&outcome=&q=&tenants=&limit=&cursor=&format=`:
`from`/`to` ISO 8601 with a time zone (default the last 14 days; at most 400 days apart, else 422
`invalid_window`); `action` matches the name or its `<action>.*` children; `layer` `api` | `conn` |
`ui` | `admin`; `outcome` `ok` | `failed` | `blocked` (groups of audit outcomes); `q` matches a job
id, policy, route or key name; `tenants` is a comma list or `*` (default: the request tenant; `*` =
the tenant directory plus the caller's grants; an ungranted tenant is 403 `tenant_forbidden`;
`_system` only for wildcard keys); `limit` 1–200. `AuditRow`: `id, ts, tenant_id, key_id, key_name,
trace_id, route, action, layer, mode, policy, outcome, status, job_id, findings, methods`.
`format=csv` streams every matching row with a formula-injection guard on each cell.

**Posture.** `PostureReport`: `env, enforced` (true in staging/prod), `passed, total, checked_at,
checks: [{id, name, ok, detail, enforced_at_boot}]` with the eleven ids `auth_enabled
bootstrap_key_absent api_keys_from_file tls_egress audit_strict leakage_strict llm_not_stub
kek_from_secret keys_rotated rls_posture migrations_verified`. Details name settings, policies and
key names, never values; tenant policies and stale keys cover the caller's tenants only (a wildcard
admin key sees every tenant); database checks read "database unavailable" while it is down.

**Retention.** `POST /admin/retention/run` body `{dry_run}` (default `false`) queues a `retention`
job owned by `_system`; it sweeps every tenant, so a key not granted `*` is 403 `tenant_forbidden`.
Follow it with `GET /api/v1/jobs/{id}` as any job; its counts are in `progress.metrics.retention`.

## Reports

- `SummaryOut`: `tenant_id, days, generated_at`, `tiles` (`values_protected`,
  `values_protected_prev`, `findings`, `entity_types`, `jobs_running`, `jobs_completed`,
  `jobs_failed`, `snapshots_awaiting_review`, `raw_pii_sent_to_llm`, `llm_calls`, `llm_blocked`, …),
  `trend: [{day, obfuscated, synthesized}]`, `findings_by_type: [{entity_type, findings,
  sensitivity}]`, `layers` (`api`, `conn`, `ui` cards), `posture` (`sweeps_passed_pct`,
  `key_held_by_caller_pct`, `snapshots_reviewed_pct`, `policies_latest_pct`, `score`),
  `recent_jobs: [{id, kind, mode, state, source, target, progress_pct, note}]`. The LLM tiles are
  this tenant's window sums.
- `ReportsJobsOut`: `days, sweeps: [{day, layer, passed, failed}], totals, failed_sweeps: [{where:
  job|api, day, job_id, partitions, route, error_code, paths}]`.
- `EvidenceItem`: `id, job_id, job_kind, outcome, created_at, pack_sha256, layer` (`conn` | `ui`),
  `summary` (`mode, policy, policy_version, ticket, source, target, write_mode, rows_written,
  verification_passed, review_required, verification_status, hits, fidelity_score,
  exact_row_matches, pages, submissions`) — summary keys only, never `reveal_map` or partitions.
  Filters: `layer`, `outcome` (`ok` = succeeded or dry run, `failed` = failed or canceled), `q`.
- `ApiRollup`: `day, action, calls, findings, values, verify_failed`. The document of one day and
  action adds `verify_passed, by_outcome, entities, generated_at, sha256`; 404 `rollup_not_found`
  when there is none (always for a day after today, which is never refreshed); `action` matches
  `^[a-z][a-z0-9_.:-]{0,63}$`.

Roll-ups are refreshed on demand for the requested window (days never refreshed, and today and
yesterday when older than 60 s) and by every retention run.

**PDFs.** `GET /evidence/{id}/pdf` renders the printable pack (reportlab, built-in Helvetica,
Latin-1 text with other characters folded; very long lists in one cell shortened with "… and N
more") on the first request, stores it as an `evidence_pdf` artefact linked from the pack, audits
`evidence.pdf`, and streams the stored bytes afterwards. `format=pdf` on a roll-up document renders
one page. With `DPS_REPORT_PDF_ENABLED=false` both answer 404 `pdf_disabled`.

## Policy store and `@name` references

A tenant adopts a shipped preset under a name (`POST /policies {name, base_preset}`; `name` 2–63
characters of `a-z 0-9 -`, starting with a letter or digit), edits a draft (`PUT …/draft {body}` —
a full policy, or `{"preset": …, …overrides}`; 400 `invalid_policy` names the broken rule) and
publishes it (`POST …/publish {change_ticket?, note?}`; the ticket 3–64 characters, the note ≤ 1000).
Published versions are immutable (a database trigger refuses updates and deletes).

- `PresetView`: `name, version, hash, description, mode` (`obfuscate` | `synthesize`), `reversible,
  jobs`.
- `TenantPolicyView`: `name, base_preset, active_version, active_hash, draft, draft_changes,
  published_at, published_by, jobs`; `TenantPolicyDetail` adds `active_body, draft_body, draft_by,
  draft_updated_at, versions: [PolicyVersionView]` (newest first).
- `PolicyVersionView`: `version, hash, change_ticket, note, published_by, published_at,
  jobs_pinned`.
- `PolicyChange` (diff): `{op: add|remove|change, path, before, after}` over dotted leaves (list
  items keyed by their `name` or `path`); `to=draft` without a draft is 409 `no_draft`. Version
  numbers are 1–999999 (`from`, `to` and the `versions/{version}` path; 422 outside).

Wherever a request takes `policy` — the Layer 1 routes, `POST /jobs/run`, `POST
/jobs/run/validate` — the string `@<name>` means the tenant's active published version and
`@<name>:v<N>` a pinned one. The reference is resolved when the request (or job) is accepted, so the
job records exactly the body it ran with; the audit labels it `<name>@v<N>`. Errors: malformed 400
`invalid_policy_ref`, unknown 404 `policy_not_found`, never published 409 `policy_not_published`, no
metadata database 400 `policy_store_unavailable`. With `DPS_VERIFY_WARN_ALLOWED=false` a body with
`verify.leakage_check: warn` is refused at publish and at use (422 `verify_warn_disallowed`).

```bash
curl -s -X POST -H "X-API-KEY: $DPS_KEY" -H 'Content-Type: application/json' "$DPS/api/v1/obfuscate" \
  -d '{"data": {"email": "a@example.com"}, "policy": "@retail-kyc:v2"}'
```

## Tabular synthesis on the job routes

`POST /jobs/run` with `mode: synthesize` takes `options.tabular`: `none` (default — surrogates per
column, non-sensitive columns copied), `copula` or `mostly` (enabled engines are
`DPS_SYNTH_TABULAR_ENGINE`; MOSTLY needs the `synth-ml` extra), plus `options.tabular_dp` (MOSTLY
differential privacy; default `DPS_MOSTLY_DP_ENABLED`) and `options.linked` (MOSTLY linked
generation: `{child, child_snapshot_id, parent_key, foreign_key, order_by?, subjects}`, written to
`files` only). The policy's `synthesize` block may add `constraints` (`{path, object?, min?, max?,
positive?, unique?, when?}` — `unique` stands alone), `quasi_identifiers` (column paths counted by
the uniqueness metric) and `utility_label` (the column the train-on-synthetic/test-on-real check
predicts). Prepare fits the model on a sample, writes nothing until a deterministic preview has been
measured, and refuses with:

| Code | When |
|---|---|
| 422 `tabular_invalid` | tabular synthesis outside synthesize mode or into an `in_place`/`append` target, an engine that is not enabled or installed, DP or linked options without `mostly`, a MongoDB (document) source |
| `leakage` (prepare) | a preview row equals a real row on every modelled column, or every non-key combination of the table is real |
| `privacy_gate` (prepare) | `DPS_PRIVACY_NNDR_ENFORCE=true` and the nearest-neighbour ratio is below `DPS_PRIVACY_NNDR_TARGET`, or the membership-inference AUC is above `DPS_MIA_AUC_TARGET` |
| `exact_row_match` (partition) | a batch still produced a real row after `DPS_COPULA_MAX_RESAMPLE` redraws (fails closed) |
| `incompatible_method` | a constraint cannot hold on the column's type (e.g. `unique` on more than one integer key) |
| 422 `synthesize_in_place_sensitive_key` | an `in_place` synthesize of a table whose key has an entity type (D-A) |
| `synthesize_pseudonym_unsupported` (prepare) | an identifier column no keyed pseudonym fits (`smallint`, `numeric(p ≤ 9, 0)`) |
| 429 `daily_row_quota_exceeded` | the tenant's rows today reached `daily_row_quota` (new runs accepted after 00:00 UTC) |
| 422 `verify_warn_disallowed` | `options.verify=warn` (or a warn policy) while `DPS_VERIFY_WARN_ALLOWED=false` |
| 422 `tls_required` | a connection that is not TLS while `DPS_EGRESS_TLS_REQUIRED=true` (create, endpoint change or use) |

The evidence pack of a synthesize run carries:

- `fidelity`: `engine, score` (1 − mean column distance − mean correlation delta), `corr_delta`,
  `limits` (`column`, `corr`), `measured_on` (`real_rows, synthetic_rows, holdout_rows, basis`),
  `columns: [{path, metric: ks|tvd, distance, null_rate_delta, within_limit}]`, `corr_matrix:
  {columns, delta}`;
- `privacy`: `enforced, engine, measured, exact_row_matches, exact_row_resamples` (preview) and
  `exact_row_resamples_run` (the partitions), `nndr: {ratio, synth_to_real_median,
  real_to_real_median, synth_rows, real_rows, holdout, zero_distance_share, measured, target,
  passed}`, `nndr_enforced`, `qi_uniqueness: {columns, source, synthetic, unique_overlap}`,
  `membership_inference: {run, method: dcr_auc, auc, members, non_members, target, passed}` or
  `{run: false, reason}`; with `tabular: none` only `{engine: none, measured: false, reason}`;
- `utility`: the train-on-synthetic / train-on-real comparison for `utility_label`, or `{run:
  false, reason}`;
- `tabular`: `engine, columns` (modelled), `excluded: [{path, reason}]`, `constraints`,
  `constraints_skipped`, `fit_rows, holdout_rows, preview_rows` (MOSTLY adds `pool_rows`, `dp`);
- `verification.post_sample.identifier_check` (D-A): per written government or financial
  identifier column the values checked, found in the same source column and outside a never-issued
  range — any count above 0 makes the pack `review_required`.

## `external_tokenize`

A policy maps an entity type to the bank's tokenization service:

```json
{"preset": "gdpr-pseudonymise", "treat": {"SSN": {"method": "external_tokenize", "profile": "ssn-fpe"}}}
```

`profile` is passed through to the service (wire contract: `HANDOFF.md` §8). The method needs
`DPS_TOKENIZER_PROVIDER=http` and `DPS_JOB_KEK` on the app; tokens come back in the output and the
reveal hints carry a `bind` tag keyed by the caller's key. `POST /reveal` detokenizes only tokens
bound to the presented key: anything else is 403 `external_token_not_bound` before the service is
called (`tools/reveal_rows.py` reveals job output locally with the key holder's own tokenizer
client). When the service is down a Layer 1 call
answers 502 (`tokenization service unavailable`) and a job batch retries with
`tokenization_unavailable`. `/readyz` shows the adapter as the informational `tokenizer` component.

## Error codes added by M5

| Status · code | Route(s) |
|---|---|
| 422 `invalid_name`, `invalid_expiry`, `invalid_grant`, `invalid_scope`, `invalid_tenant`, `invalid_grace` | `/admin/keys` |
| 403 `scope_escalation`, `tenant_escalation` | `/admin/keys` (create, rotate) |
| 404 `key_not_found` · 409 `key_rotated`, `key_expired` | `/admin/keys/{id}/…` |
| 404 `tenant_not_found` · 409 `tenant_exists` · 403 `tenant_forbidden` | `/admin/tenants`, `/admin/audit`, `/admin/retention/run` |
| 422 `invalid_window`, `invalid_cursor`, `invalid_tenant` | `/admin/audit` |
| 404 `rollup_not_found`, `pdf_disabled`, `evidence_not_found` | `/reports/api-rollups/{day}`, `/evidence/{id}/pdf` |
| 400 `invalid_policy`, `invalid_policy_ref`, `policy_store_unavailable` | policy store, `@name` references |
| 404 `policy_not_found`, `version_not_found` | policy store, `@name` references |
| 409 `no_draft`, `policy_exists`, `policy_not_published` | policy store, `@name` references |
| 422 `invalid_name`, `invalid_preset`, `invalid_change_ticket`, `invalid_note`, `verify_warn_disallowed` | `/policies` (create, publish) |
| 403 `external_token_not_bound` | `/reveal` |
| 429 `daily_row_quota_exceeded` · 422 `tabular_invalid`, `synthesize_in_place_sensitive_key`, `tls_required` | `/jobs/run`, `/jobs/run/validate`, `/connections` |
| `privacy_gate`, `leakage`, `exact_row_match`, `synthesize_pseudonym_unsupported`, `tokenization_unavailable` | job `error_code` (prepare or partition) |
