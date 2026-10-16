# M5 G3 implementation report — Tasks 9–14 (worktree `dps-wt-m5`, branch `m5`)

Date: 2026-10-13. Programme plan §2.4 row G3: DB keys (rotation, expiry, no privilege escalation),
tenants/quotas, admin API, roll-ups, retention extending M2's one scheduler, evidence PDF.

## Resume state

At start: `git status` clean, HEAD `7c97bde` (G2 fixes), no earlier G3 report, no uncommitted G3
work. Nothing to resume; all six tasks built from scratch.

## Commits (author NilenduGanguli <nilendu.ganguli@gmail.com>, no trailers — verified)

| Task | Commit | Message |
|---|---|---|
| 9 | `f2c3ad2` | feat(auth): DB-backed API keys with rotation grace, expiry, no-escalation grants and an operator CLI |
| 10 | `f32caed` | feat: tenants with quotas, daily usage counted at checkpoint and enforced at accept and claim, tenant directory |
| 11 | `9da77d0` | feat(api): admin routes for keys, tenants, audit search with CSV export, and queue health |
| 12 | `23ddb0b` | feat(reports): value-free daily roll-ups and the summary, sweeps, evidence and API roll-up endpoints |
| 13 | `5906e0f` | feat: extend the M2 retention job - M5 tables, roll-up refresh, counts, on-demand admin run |
| 14 | `6392800` | feat(reports): printable evidence-pack and API roll-up PDFs with escaped, Latin-1-safe text |

Every commit carries its contract amendments (`docs/superpowers/plans/2026-10-11-m1-m5-contract.md`)
and its `docs/superpowers/build/M5-DEVIATIONS.md` section (Tasks 9–14 sections added before
"Deferred to final stage").

## What was built

- **Task 9** — `042_key_rotation.sql`; `dps/auth_db.py` (`DbKeyStore` snapshot with per-lookup
  expiry, invalidate, ten-period stale fail-closed; `ChainKeyStore` static-first; create/list/rotate/
  disable/enable with `check_grant` no-escalation; wildcard only from wildcard holders; reserved and
  malformed tenant ids refused); `dps/keys_cli.py` (one JSON document on stdout, exit 2 on refusal,
  `_system` audit row per mutation); `StaticKeyStore.principals()`; app wiring (refresher started
  after the job platform connects, stopped before pools close; readiness `keys`).
- **Task 10** — `040_tenant_quotas.sql` (`daily_row_quota`, `registered_hosts IF NOT EXISTS`,
  `dps_tenant_usage` with RLS + `worker_usage`, `owner_directory` policies `TO CURRENT_USER`,
  `dps_tenant_directory()` SECURITY DEFINER returning ids only); `dps/netguard.py::normalize_host`;
  `dps/tenants.py`; M2 touch points: `checkpoint` (fenced, same transaction, `RETURNING tenant_id`),
  `claim_partitions` quota predicate, `POST /jobs/run` 429 `daily_row_quota_exceeded` (not for
  idempotent replays); `ALLOWED_EXTRA_POLICIES`, `TENANT_TABLES`.
- **Task 11** — `dps/audit_query.py` (per-tenant RLS queries merged on `(ts, id)`, keyset cursor,
  outcome groups, layer CASE shared with roll-ups, CSV formula guard, streamed CSV);
  `dps/layers/api/routers/admin.py` (keys, tenants, audit json/csv, queue) — admin scope + rate limit +
  DB; every mutation audited value-free.
- **Task 12** — `041_report_rollups.sql`; `dps/reports.py` (one-transaction, try-locked, idempotent
  roll-up refresh; `ensure_fresh`; summary/jobs/evidence/api-rollup queries);
  `dps/layers/api/routers/reports.py`.
- **Task 13** — `dps/retention.py` extended in place: `RetentionCounts`, `run_retention`
  (directory tenants incl. `_system`, M5 age-out of roll-up/refresh/usage rows, roll-up refresh,
  per-tenant DB error isolation), `retention_job` wrapper keeps M2's counters + adds
  `progress.metrics.retention` + one `retention.run` audit event; `enqueue_retention`;
  `POST /admin/retention/run` (wildcard-tenant admin only). No second scheduler; `dps/worker.py` and
  `dps/jobs.py` untouched by this task.
- **Task 14** — `dps/layers/reports/pdf.py` (all mockup-10 sections, defensive number formatting,
  linked/DP/scan sections, escaped + Latin-1-folded text); `GET /evidence/{id}/pdf` (render once,
  store `evidence_pdf` artefact, link, audit, stream afterwards; race-safe link) and
  `format=pdf` on `/reports/api-rollups/{day}`; `DPS_REPORT_PDF_ENABLED=false` → 404 `pdf_disabled`.

## Gate results (final run, after the Task 14 commit's content)

- `./lane.sh ruff format dps tests tools` → 290 files unchanged (after formatting); `ruff check` →
  All checks passed; `./lane.sh mypy dps` → Success: no issues found in 121 source files.
- `./lane.sh pytest -q` with this lane's `dps-db`, `source-pg` and `minio` up →
  **1820 passed, 12 skipped** (205 s). Skips: the extras/services not run here (OCR mock, browser,
  tesseract, MOSTLY `ml` extra, `deploy`).
- Integration (`-m "pg_source or s3 or integration"`, after Task 10's queue changes):
  **72 passed, 3 skipped** (135 s) on the lane's `source-pg` + MinIO.
- New G3 tests (73): `test_auth_db.py` 20, `test_keys_cli.py` 3, `test_tenants.py` 7,
  `test_quota.py` 3, `test_admin_api.py` 9, `test_reports.py` 8, `test_retention_m5.py` 12,
  `test_evidence_pdf.py` 11. M2's `tests/db/test_retention.py` unchanged and green.
- Frontend: no file under `frontend/` changed → npm gates not applicable.
- Docker: only `dps-db`, `source-pg`, `minio` of this lane were started (`--no-deps`; the app image
  was not built); `docker compose --profile sources down` at the end (0 `dps-wt-m5` containers left).

## Privacy evidence

- **No values in logs (log scan):** the eight G3 test files run with `-s` (140 log lines captured):
  sentinels `Zebulon`, `Quixote`, `123-45-6789`, `secretpw`, `SENTINEL-TRACE` → 0 hits each; raw-key
  pattern `dps_[A-Za-z0-9_-]{40,}` → 0; 64-hex (key hashes) → 0. The CLI test asserts its stderr
  holds no `dps_`.
- **Keys at rest:** `test_create_lookup_and_hash_only_storage` dumps the whole `dps_api_key` table:
  the raw key never appears, its sha256 does. List/admin responses contain neither key nor hash.
- **Audit/report outputs:** `test_audit_never_contains_values` (an obfuscate call with an SSN and a
  name → audit JSON and CSV contain neither); reports tests assert fixture sentinels (job `error`,
  audit `trace_id`, a `reveal_map` entry) never reach summary/jobs/evidence/roll-up answers, and
  evidence summaries never contain `reveal_map` or `partitions`.
- **RLS:** the tenant directory proves `dps_job`/`dps_connection` stay empty for an unbound GUC;
  RLS posture check accepts exactly the new policies; `tests/db/test_rls.py` per-table isolation
  covers the four new tenant tables; roll-ups are tenant-isolated (HTTP + direct read).
- **No privilege escalation:** 5 parametrised escalation cases + tenant-admin vs wildcard key
  (disable/enable/rotate/list) + over HTTP; retention run needs a key for every tenant.
- No masking/synthesis or no-submission claims are made by this group (none apply).

## Deviations (full list in `docs/superpowers/build/M5-DEVIATIONS.md`)

Highlights: `registered_hosts` added by 040 with `IF NOT EXISTS` (M4's 031 owns it — see open issue
1); `owner_directory` policies `TO CURRENT_USER`; quota read uncached at accept; admin queue computed
per tenant under RLS instead of M2's worker-pool `queue_stats()`; audit `tenants=*` = directory +
grants (`_system` readable by wildcard keys only); M5 retention counter keys only when non-zero (so
M2's pinned test stays unchanged); roll-up job statement skips `col:`/`fallback:`/`verify:` counter
keys (the plan double-counted); defensive PDF rendering incl. linked/DP shapes; retention run limited
to wildcard-tenant admins; `test_files_are_the_contract_set` checks M2 and M5 ranges separately;
shared fixtures truncate/seed the M5 tables.

## Open issues / for later stages

1. **WP-I (integration):** M4's `031_tenant_hosts.sql` should become `ADD COLUMN IF NOT EXISTS`
   (or lane databases recreated): a DB migrated on `m5` before the merge would otherwise apply 031
   after 040 and fail on the existing column.
2. Audit `tenants=*` does not include tenants that only ever called Layer 1 (no directory entry);
   they must be named explicitly or given a tenant record.
3. Summary tiles `raw_pii_sent_to_llm`/`llm_calls`/`llm_blocked` are constant 0 and
   `policies_latest_pct` is 100 until Tasks 16B (migration 044) and 15 (policy store).
4. `layers.ui.risk_flags` and the retention scan-cascade check activate only once M4's 030 is merged.
5. The PDF has been checked by text extraction and a visual page render here; the console download
   button is Task 21 (UI lane).
