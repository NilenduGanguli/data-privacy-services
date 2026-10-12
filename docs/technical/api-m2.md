# Jobs and PostgreSQL API (M2)

Base path `/api/v1`, the same keys and headers as Layer 1 (`docs/technical/api-m0.md`): `X-API-KEY`,
`X-DPS-Tenant` (the job platform takes lower-case tenant ids only; a tenant id starting with `_` is
refused with 403 `tenant_reserved`), `X-Request-ID`. The live schema is `/api/v1/openapi.json`.

Every route below needs the metadata database (`DPS_PG_HOST`); while it is unset or down they answer
503 `db_unavailable` and the Layer 1 routes keep working. Every route is rate-limited per key like
Layer 1, and every mutating call writes one value-free audit row (`dps_audit_log`: action, status,
job id, counts — never a value, key or selection literal).

**Conventions.**

- Errors are `{"detail": str, "code": str, …}`; `detail` never quotes the request (no value, literal,
  password or connection string). 422 validation errors list `{type, loc, msg}` only.
- Lists take `limit` (1–200, default 50) and an opaque `cursor`, answer `{"items": [...],
  "next_cursor": str | null}` and are newest first. A cursor this API did not issue is 400
  `cursor_invalid`.
- `POST /jobs/discover`, `POST /jobs/run` and `POST /jobs/{id}/retry` honour `Idempotency-Key`
  (`^[A-Za-z0-9_.:-]{1,128}$`, else 422 `idempotency_key_invalid`; durable per tenant). A replay
  answers **200** with `Idempotent-Replay: true` and the job only — never the key again. The key
  is bound to its request (route and body, fingerprinted under a subkey of the job KEK; migration
  010): the same key with another body, or on the other route, is 422 `idempotency_key_reused` —
  the Layer 1 code and detail (contract §9).
- Ids: jobs `j_…` (scans `s_…`, M4), connections `c_…`, snapshots `snap_…`, groups `grp_…`, evidence
  `ev_…`, artefacts `art_…` (prefix + 26-character Crockford ULID).

## Routes

Scope `jobs` unless stated. "prod" = the connection carries the tag `production`.

| Method · path | Scope | Request → response | Error codes (besides 401/403/429/503) |
|---|---|---|---|
| `POST /connections` | jobs (+`admin` to tag prod) | `ConnectionSpec` → 201 connection | `connection_invalid` 422, `inline_secrets_disabled` 422, `connection_name_taken` 409, `admin_scope_required` 403, `kek_unconfigured` 503 |
| `GET /connections` | jobs | `?limit&cursor` → page of connections | `cursor_invalid` 400 |
| `GET /connections/{id}` | jobs | → connection | `connection_not_found` 404 |
| `PATCH /connections/{id}` | jobs (+`admin` to add or remove prod) | `ConnectionPatch` → connection | as `POST`, `connection_not_found` 404 |
| `DELETE /connections/{id}` | jobs | → 204 | `connection_in_use` 409 (a non-terminal job uses it) |
| `POST /connections/{id}/test` | jobs | `{role: source\|target, object?, write_mode?}` → `ConnectionTestResult` `{ok, engine, server_version, checks: [{name, ok, severity, detail?}], capabilities, error_code}` (stored as `last_test`); a failed test is a 200 with `ok: false` and an `error_code` such as `secret_unavailable` or `connection_failed` | `connection_not_found` 404, `engine_unavailable` 422 |
| `GET /connections/{id}/objects` | jobs | `?owner=` → `{items: [ObjectRef], next_cursor: null}` | `connection_failed` 502 |
| `GET /connections/{id}/describe` | jobs | `?object=OWNER.NAME` → `TableShape` | `object_invalid` 422, `object_not_found` 422 |
| `POST /connections/{id}/count` | jobs | `{object, selection}` → `{rows, checks, runs_as, binds}` | `selection_*` 422 (with `checks`; no query runs), `statement_timeout` 502, `connection_role` 422 |
| `POST /jobs/discover` | jobs | `DiscoverSpec` → 202 `{job}` | `connection_not_found` 404, `connection_role` 422, `selection_*` 422, `queue_full` 429 |
| `POST /jobs/run` | jobs (+`admin` for in place / append on prod) | `RunSpec` → 202 `{job, key_material?, synthesis_seed?}`, `Cache-Control: no-store` | the run-validation table below, `queue_full` 429 |
| `GET /jobs` | jobs | `?kind&state&group_id&limit&cursor` → page of jobs | `cursor_invalid` 400 |
| `GET /jobs/stats` | jobs | → `JobStats` | — |
| `GET /jobs/{id}` | jobs | → job (progress merged from its partitions; the console polls every 2 s) | `job_not_found` 404 |
| `GET /jobs/{id}/partitions` | jobs | → `{items: [PartitionView]}` | `job_not_found` 404 |
| `POST /jobs/{id}/cancel` | jobs | → job | `job_terminal` 409, `job_finalizing` 409 |
| `POST /jobs/{id}/pause` · `/resume` | jobs | → job | `job_not_pausable` 409, `job_not_paused` 409 |
| `POST /jobs/{id}/retry` | jobs | optional `{consistency: {key_material \| synthesis_seed}}` → job | `job_not_retryable` 409, `key_material_required` 409, `key_material_mismatch` 409 |
| `GET /snapshots` | jobs | `?connection_id&object&status&limit&cursor` → page of snapshots | — |
| `GET /snapshots/{id}` | jobs | → `ClassificationSnapshot` | `snapshot_not_found` 404 |
| `PATCH /snapshots/{id}` | jobs | `{overrides: [SnapshotOverride]}` → snapshot (overrides merge per path) | `snapshot_not_proposed` 409, `snapshot_conflict` 409, `override_unknown_path` 422, `override_method_required` 422, `override_anchor_invalid` 422 |
| `POST /snapshots/{id}/accept` | jobs | → snapshot (`accepted_by` = the key's name; an earlier accepted snapshot of the object becomes `superseded`) | `snapshot_not_proposed` 409 |
| `POST /job-groups` | jobs | `{name}` → 201 `{group, key_material, synthesis_seed}`, `Cache-Control: no-store` | `group_name_taken` 409 |
| `GET /job-groups` · `GET /job-groups/{id}` | jobs | page of groups · `{group, jobs}` (newest 200 jobs) | `group_not_found` 404 |
| `GET /evidence` | jobs or scan | `?job_id&limit&cursor` → page of `EvidenceSummary` (only the kinds the key may read) | — |
| `GET /evidence/{id}` | jobs (scan for scan packs) | → `{id, job_id, created_at, pack, pack_sha256, artefacts: [{id, kind, bytes, content_type}]}` | `evidence_not_found` 404, `scope_required` 403 |
| `GET /evidence/{id}/html` | jobs (scan for scan packs) | → the printable pack (`text/html`, CSP `default-src 'none'; style-src 'unsafe-inline'`) | as above |
| `GET /artefacts/{id}` | jobs or scan (by the owning job's kind) | → the bytes, streamed; `Content-Disposition` uses the artefact id | `artefact_not_found` 404, `storage_not_found` 404 |

**Discovery classification** (per column of a snapshot): `entity_type` is the best entity whose
`noisy_or(name score, hit rate × mean score)` reaches the policy's `min_score`. When none does,
the rows with *any* whole-value finding of MEDIUM or higher sensitivity count together: at or above
`min_score` the column is `MIXED_PII` (values of several types — an e-mail in one row, a phone in
the next; `type_note: "mixed_entities"`), and every value is replaced whole: `det_token` on an
unbounded column, `hmac_pseudonym` (16 characters) on a bounded one where a token could overflow,
`mask_partial` below 16 characters; `surrogate` in synthesize mode. A column called `value`
(`contact`, `detail`, …) beside a `channel` / `contact_type` column is a mixed contact value by its
name. Otherwise a text column with span findings in 5 % of the rows is `FREE_TEXT`. A column kept
with no entity whose values still carry MEDIUM+ findings in 5 % or more of the sampled rows keeps
`value_hit_rate`, `value_entities` (type names) and `type_note: "kept_with_hits"` — the console
lists it under *Needs a decision* and never as "not personal". `value_entities` names the MEDIUM+
types found in the sample for every column. Name cues include `*holder_name`, `<role>_name`
(payee, counterparty, beneficiary, …), `line1` / `address_line*`, and `doc_number` /
`document_number` (`NATIONAL_ID`).

Probes: `/readyz` gains `db` (`up` / `down` / `unconfigured`, `migrations: verified`) and `jobs` (ok
when the database is up, the migrations verify and the KEK is loaded); only `api-core` is required.
The worker serves `/health`, `/readyz` and `/metrics` on `DPS_WORKER_METRICS_PORT` (9400).

## Request bodies

```json
// POST /connections (ConnectionSpec): secret_ref names a directory under the caller's tenant
// folder, DPS_SECRET_DIR/<tenant_id>/<ref>/ (here the demo tenant `default`:
// /var/run/secrets/dps/default/dps/src/bank-pg-ro/), or _shared/<ref> for a folder whose
// `tenants` file lists the tenant. A ref whose first segment names another tenant's folder is 403
// secret_ref_foreign_tenant; an unlisted shared ref 403 secret_ref_not_shared; a malformed one 422
// secret_ref_invalid. Inline credentials (dev only) are sealed with DPS_JOB_KEK and expire after
// DPS_INLINE_SECRET_TTL_HOURS.
{"name": "bank-pg-ro", "engine": "postgres",
 "params": {"host": "source-pg", "port": 5432, "database": "bank"},
 "secret_ref": "dps/src/bank-pg-ro", "roles": ["source"], "tags": ["production"]}

// POST /jobs/discover (DiscoverSpec)
{"connection_id": "c_…", "objects": [{"owner": "bank", "name": "customers"}],
 "selection": {"where": "country_code = 'IN'"}, "sample_rows": 2000, "mode": "obfuscate"}

// POST /jobs/run (RunSpec)
{"mode": "obfuscate",
 "source": {"connection_id": "c_…", "object": {"owner": "bank", "name": "customers"},
            "selection": {"where": "country_code = :cc", "params": {"cc": "IN"}, "columns": null,
                          "max_rows": null, "sample": null},
            "parallelism": 4, "batch_rows": 1000, "partition_rows": 50000},
 "target": {"write_mode": "table", "connection_id": "c_…", "object": {"owner": "bank_uat", "name": "customers"},
            "create_if_missing": true, "table_load": "truncate_insert",
            "pk_strategy": null, "files": null, "confirm_write": false, "expected_rows": null},
 "snapshot_id": "snap_…", "policy": null,
 "consistency": {"scope": "key", "field_scoped": false, "key_material": null, "synthesis_seed": null, "wrap_key": null},
 "group_id": null,
 "purpose": {"ticket": "CHG-48213", "legal_basis": "Legitimate interest — UAT provisioning"},
 "options": {"dry_run": false, "verify": "strict", "unclassified": "detect", "tabular": "none"}}
```

- **Selection.** `where` (one boolean expression) or `query` (one read-only `SELECT`), parsed with
  sqlglot before anything is opened: no DML/DDL, `INTO`, procedure calls, database links, `FOR
  UPDATE`, locks, `set_config`, admin functions or positional `$n` binds; identifiers validated;
  literals are lifted into bound parameters (`:l1`, …) and named `:params` are bound. The job stores
  only the template (literals replaced) and the bind count; the selection itself is sealed.
  `columns` limits what is read (the key is always read); `max_rows` caps the rows; `sample`
  `{percent, seed}` reads a sample.
- **Write modes** (`target.write_mode`): `table` (created from the source definition when missing —
  no FKs, indexes or defaults — then `truncate_insert` or `upsert` on the key), `files`
  (`{format: csv|parquet|jsonl, prefix, compression, rows_per_file}` to an `s3` or `pvc` connection,
  with a manifest written last), `in_place` (rewrites the selected rows by key through the source
  connection, which needs the `target` role; keys are never rewritten) and `append` (inserts copies
  into the same or a named table; `pk_strategy` `{kind: generate}`, `{kind: offset, offset | prefix}`
  or `{kind: token}`). `in_place` and `append` need `confirm_write: true` (and `expected_rows`: the run
  fails `row_count_mismatch` when more rows match) and, on a prod connection, the `admin` scope.
- **Keys.** Without `consistency.key_material` the run generates a key and returns it **once** in the
  202 (wrapped when `wrap_key` `{type: rsa-oaep-256, public_key_pem}` is given). DPS holds it sealed
  only while the job runs and purges it at every terminal state; the evidence pack holds its `kid`.
  A job group's runs pass the group's key (`group_id` + `consistency.key_material`). Synthesize runs
  return a `synthesis_seed` the same way.

## Run validation and job error codes

| Code · status | When |
|---|---|
| `snapshot_not_accepted` 409 | the snapshot is not `accepted`, or not of the source connection and object |
| `confirm_write_required` 422 | `in_place`/`append` without `confirm_write: true` |
| `admin_scope_required` 403 | `in_place`/`append` to a prod connection without `admin`; tagging prod without `admin` |
| `in_place_invalid` / `pk_transform_in_place` 422 | in place through another connection or object, with a `query`, or with a transformed key column |
| `pk_strategy_required` / `pk_strategy_invalid` 422 | append without a strategy; a same-table append other than one integer key with `generate` or `offset`; `token` on a key whose method is not reversible (prepare also refuses an offset below `max(key) + 1 − min(selected key)` and a `generate` key without an owned, ascending, non-cycling sequence) |
| `target_invalid` 422 | `table` without connection/object or onto the source; `files` without `files` or to a non-file store; a `pk_strategy` on `table` |
| `connection_role` 422 | the source lacks `source`, the target lacks `target` |
| `group_key_mismatch` 409 · `group_not_found` 404 | the key's `kid` is not the group's, or `field_scoped` with a group; unknown group |
| `tabular_invalid` 422 · `engine_not_tabular` 422 | `options.tabular` other than `none` (M5) |
| `key_method_not_injective`, `method_needs_key`, `text_method_invalid`, `engine_unavailable`, `wrap_key_invalid`, `key_material_invalid` 422 | a reviewed method that cannot run here (a non-injective method on a key, a keyed method without a key, a text method on a non-text column, a missing engine), a bad wrap key, bad key material |
| `kek_unconfigured` 503 | sealing needed and `DPS_JOB_KEK` unset |
| `queue_full` 429 | the tenant's queued jobs reached `DPS_JOB_TENANT_MAX_QUEUED` |

Failures recorded on the job (`error_code`, value-free `error`): at prepare `snapshot_stale`,
`target_invalid`, `row_count_mismatch`, `pk_collision` (count only), `no_key`, `incompatible_method`,
`length_overflow`, `transactions_unsupported`, `connection_failed`, `secret_unavailable`,
`pk_strategy_invalid`; at run time `leakage` (the per-row sweep found a source value; nothing of that
batch is written), `source_error`, `target_error`, `statement_timeout`, `selection_param_invalid`,
`ledger_conflict`, `lease_lost`, `canceled`; discover jobs also `connection_missing`, `policy_invalid`,
`spec_invalid`. API-only codes: `connection_not_found`, `connection_name_taken`, `connection_in_use`,
`inline_secrets_disabled`, `job_not_found`, `job_terminal`, `job_finalizing`, `job_not_pausable`,
`job_not_paused`, `job_not_retryable`, `key_material_required`, `key_material_mismatch`,
`snapshot_not_found`, `snapshot_not_proposed`, `snapshot_conflict`, `override_*`, `evidence_not_found`,
`artefact_not_found`, `scope_required`, `db_unavailable`, `audit_unavailable` (503 when
`DPS_AUDIT_STRICT` and the audit write failed), `tenant_invalid`, `tenant_reserved`, `cursor_invalid`,
`idempotency_key_invalid`, `idempotency_key_reused`, `internal_error` (500; only the exception
class is logged).

## Job and partition state machine

| Kind | Phases | Notes |
|---|---|---|
| `discover` | `discover` | one proposed snapshot per object; no evidence pack |
| `run` | `prepare` → `partitions` → `finalize` | partitions are queue rows of their own |
| `retention` | `retention` | scheduled by the workers (owner `_system`) |

Job `state`: `queued` (claimable once `run_after <= now()`) → `running` (leased; heartbeats extend
the lease) → for a run's `prepare`: partitions inserted, `phase=partitions, state=waiting` (the job row
is not claimable; its partitions are) → when the last partition is terminal, `phase=finalize,
state=queued` → `running` → terminal `succeeded | failed | dead | canceled`. `paused` stops new
partition claims (running partitions stop after their batch and go back to `queued`); `resume` returns
to `waiting`. `dry_run` skips partitions (no writes, no DDL). A worker that loses its lease is fenced
(its writes match zero rows) and the reaper re-queues the row with backoff (`attempts` counted;
`dead` after `max_attempts`). Cancel: queued/waiting/paused at once, running holders at their next
fenced write; a cancel finalize still writes the evidence (`outcome: canceled`) and purges the key.
Retry: failed/dead run jobs only; failed/dead partitions are re-queued and resume from their
checkpoints (409 `key_material_required` when the key was purged and none is resubmitted).

Partition `state`: `queued → running → done`, or `failed` (retried) / `dead` / `canceled`. After every
committed batch the partition checkpoints its last key (stored only in the job tables, shown as a
keyed 8-hex fingerprint) together with a write-ledger mark in the target; on resume the ledger is
authoritative, so no batch is applied twice and no row is left half-written.

## Evidence pack (`pack_version: dps.evidence.v1`)

`id, tenant_id, job_id, job_kind, group_id, outcome (succeeded|failed|canceled|dry_run), label
("pseudonymised — still personal data" | "synthetic — measured, not claimed anonymous"), mode,
created_at, engine_version, image_digest, purpose {ticket, legal_basis}, requested_by (a key id),
source {engine, connection_id, connection_name, object, selection {where_template, binds, columns,
max_rows, sample}, server_version}, target {engine|kind, connection_id, object, write_mode,
table_load, pk_strategy, created, confirm_write, expected_rows, files {format, objects,
manifest_artefact_id}}, policy {name, hash}, snapshot {id, content_hash, accepted_by, accepted_at,
overrides}, consistency {scope, field_scoped, kid, seed_fingerprint, key_custody {returned, wrapped,
purged_at}}, columns [{path, entity_type, method, reversible, values, fallbacks}], reveal_map [{path,
entity_type, method, params, anchor_path, reveal}], counts {rows_estimated, rows_read, rows_written,
rows_skipped, partitions, batches, resumed_partitions}, verification {mode, batches, values_checked,
hits, passed, status (passed|review_required|failed), review_required, post_sample {rows,
detector_hits, kept_column_hits, kept_columns, declared_kept_hits}}, fidelity/privacy/utility (M5),
scan (M4), timings {started_at, finished_at, duration_s, rows_per_s}, partitions [{no, rows, attempts,
resumed, state}], errors [{code, where}], config_hash` (sha256 of policy hash + snapshot hash +
redacted spec).

**Post-write sample.** Finalize re-reads up to 200 written rows and runs the detectors over every
output column. `detector_hits` counts cells of transformed columns with a finding (expected:
format-preserving output still looks like its type); `kept_column_hits` counts cells with a MEDIUM+
finding in columns the accepted snapshot kept **as not personal** (no entity), and `kept_columns`
names them; `declared_kept_hits` counts those in columns classified but kept on purpose (a key kept
in place, `CITY: keep`). Any `kept_column_hits` make the verification `review_required` with
`passed: false` in either verify mode — personal data reached the target untransformed — and the
job's `progress.verification.kept_hits` carries the count to the job page; `failed` (sweep hits or a
leakage refusal) wins over it. `GET /evidence` rows carry `review_required`; the console shows
"review required" (amber), never "verified · 0 leaks".

`pack_sha256` = sha256 of the canonical JSON (sorted keys, compact separators). Each pack is stored
with a JSON and a printable HTML artefact (the console's Print / save as PDF uses the browser; the PDF
artefact arrives with M5). A pack holds counts, hashes, column names, method names and key ids — never
a value, key, seed or key position.

## Measured throughput

`tools/bench_connector.py --rows 200000` against the compose stack (image of `17e14a2`, tools of
`8204191`), 2026-10-12, Apple-silicon Mac (18 CPUs; Docker Desktop VM with 12 CPUs), FF1 on, 16
columns of which 8 transformed, 4 partitions of 50 000 rows, 1 000-row batches, worker concurrency 4.
Wall clock from submission to the terminal state; results in `docs/technical/bench/`. Measured, not
promised.

| Run | Workers | Rows/s | Wall |
|---|---|---|---|
| obfuscate → `table` | 1 | 3 004 | 66.6 s |
| obfuscate → `in_place` | 1 | 3 196 | 62.6 s |
| obfuscate → `files` (parquet to MinIO) | 1 | 2 912 | 68.7 s |
| synthesize → `table` | 1 | 2 021 | 99.0 s |
| obfuscate → `table` | 3 | 4 128 | 48.5 s |
| obfuscate → `table`, 12 partitions | 3 | 4 828 | 41.4 s |

One worker process is bound by one interpreter (the transform runs in a thread under the GIL):
throughput scales with worker replicas (`docker compose up -d --scale worker=N worker`), not with
partitions per worker.
