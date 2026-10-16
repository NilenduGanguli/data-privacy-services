# M5 G4 — implementation report (Tasks 15, 16, 16B)

Lane: worktree `/Users/neelu/document_intelligence/dps-wt-m5`, branch `m5`. Start state: clean tree at
`3fa5db6` (G3 + review fix); no earlier G4 work, so nothing had to be resumed. Programme plan §2.4 row G4:
"policy store with immutable versions; strict verification is a **boot** rule; per-tenant LLM exposure
counter + egress verifier + alert" — all three delivered.

## Commits (author NilenduGanguli <nilendu.ganguli@gmail.com>, no trailers)

| Commit | Task | Message |
|---|---|---|
| `534ba34` | 15 | feat(policies): tenant policies with drafts, immutable versions, diffs and @name references resolved at accept |
| `2c219d6` | 16 | feat(posture): strict verification at boot, secrets from files, TLS-only egress, explicit CORS, docs off in prod, KEK re-sealing, runtime posture report |
| `86b1920` | 16B | feat(llm): egress verifier and per-tenant LLM exposure counters, audit counts and roll-ups |

Each commit carries its contract amendments and its `docs/superpowers/build/M5-DEVIATIONS.md` section.

## Task 15 — policy store

- `dps/migrations/043_policies.sql`: `dps_policy` (draft, active version, reserved names refused by CHECK)
  and `dps_policy_version` (resolved body, sha256 hash, ticket/note CHECKs); versions immutable
  (BEFORE UPDATE/DELETE trigger for every role; app role SELECT/INSERT only), no DELETE on policies;
  RLS ENABLE+FORCE+`tenant_isolation`; both in `TENANT_TABLES`.
- `dps/policy_store.py`: `REF`, `flatten_policy`, `diff_policies`, `policy_yaml`, `list_policies`,
  `create_policy` (v1 = preset, note "shipped preset"), `get_policy`, `save_draft` (validated via
  `resolve_policy`; stores the resolved body), `discard_draft`, `publish` (one transaction, `FOR UPDATE`,
  refuses `verify.leakage_check: warn` with 422 `verify_warn_disallowed` when
  `DPS_VERIFY_WARN_ALLOWED=false`), `get_version`, `diff_versions`, `resolve_ref`, `active_versions`.
- `dps/layers/api/policy_refs.py`: `resolve` / `resolved_policy(..., mode=)` / `policy_label`
  (`name@vN` via a `StoredPolicy` dict subclass only the store produces), coded errors
  `invalid_policy_ref` 400, `policy_not_found` 404, `policy_not_published` 409,
  `policy_store_unavailable` 400 (503 `db_unavailable` while a configured DB is down).
- Routes `/api/v1/policies` (GET list, POST create, GET detail, PUT/DELETE draft, POST publish, GET
  versions/{v}?format=json|yaml, GET diff?from=&to=n|draft), registered after M0's policies router;
  mutations audited `admin.policy.<create|draft|discard|publish>` with `{version, changes}` only.
- `@name[:vN]` resolved at accept on `/detect`, `/obfuscate`, `/synthesize` (JSON + both NDJSON paths),
  `/policies/validate`, `/policies/plan`, `/documents/*`, `/jobs/discover`, `/jobs/run` (job spec stores
  `policy.version`). `reports.summary` `posture.policies_latest_pct` is now real.
- Contract: §2.2 (043), new §5.5 "Policy references", §9 rows.

## Task 16 — posture

- `dps/posture.py`: `SECRET_ENV` literal-env refusal, KEK rule (with `DPS_PG_HOST`), TLS-only egress
  (`DPS_EGRESS_TLS_REQUIRED`, https for 5 egress URLs, CA bundle), tokenizer token file, explicit CORS,
  **strict verification at boot** (`DPS_VERIFY_WARN_ALLOWED=false`, `DPS_LLM_EGRESS_CHECK=strict`,
  `non_strict_policies` over shipped presets + default policy); `posture_report()` with the 11 fixed
  checks (`keys_rotated` lists "<name> is <n> days old", `leakage_strict` adds every tenant's active
  stored policy), value-free; `GET /api/v1/admin/posture` (admin, no DB needed).
- Request time: `PrivacyEngine.policy` → `CodedError` 422 `verify_warn_disallowed`; `validate_run`
  refuses `options.verify="warn"` or a warn policy; `/policies/validate` maps it.
- `dps/netguard.require_tls_connection` (postgres verify-full/verify-ca, mongo tls, oracle tcps or
  tcps dsn, s3 https, pvc exempt) at `POST /connections`, `PATCH` with params, and at use
  (`open_connector`, `open_target_store` → `ConnectorError("tls_required")`, 422 on the API, not
  retried in jobs); `protocol` added to the options allow-list.
- `dps/app.py`: CORS from `DPS_CORS_ORIGINS` (none at all when empty), `/docs` + OpenAPI off in prod.
- `dps/rekey.py` (`python -m dps.rekey [--dry-run]`): re-seals `dps_job.key_enc`/`spec_enc` (worker
  pool) and `dps_connection.secret_enc` (per tenant), optimistic updates, JSON counts line, exit 3 on
  unreadable, exit 2 on config error. Runbook in HANDOFF §6.
- Contract §5.1, §5.3 (`tls_required`), §9 (posture shape), §10.

## Task 16B — LLM exposure

- `dps/llm/egress.py`: `residual_values` (protected originals ≥ 3 chars — 3-char ones as whole words
  — plus checksum-validated / e-mail / phone T1 hits outside `{TYPE_N}` placeholders, value shapes
  excluded), `LlmEgressBlocked(LlmCallError)`.
- `LlmGateway.complete(..., protected=())` runs it: strict blocks (nothing sent), warn sends and
  counts; `record_llm` → `dps_llm_calls_by_tenant_total`, `dps_llm_raw_pii_sent_by_tenant_total`,
  `dps_llm_exposure_blocked_by_tenant_total` (+ M0's process counter on raw values).
- `CURRENT_TENANT` / `LLM_STATS` bound in `begin_call` (every audited L1 call) and `Consumer._guard`
  (every job/partition task); audit `counts.llm_calls/llm_raw_values/llm_blocked` when the request
  reached the gateway; callers (`synthesize_text`, M1 spec proposer, M1 labelling) pass `protected` and
  fall back with `fallback: egress_blocked`.
- `044_llm_exposure.sql` + roll-up statements (audit counts, job `progress.metrics.llm`) + summary
  tiles `llm_calls`, `raw_pii_sent_to_llm`, `llm_blocked` per tenant. HANDOFF §5 names the metrics and
  the alert rules `DpsRawPiiSentToLlm` (critical) / `DpsLlmExposureBlocked` (warning) — the
  `PrometheusRule` itself is Task 25 (deferred, WP-F2).

## Gates (final, on `86b1920`)

- `./lane.sh ruff format dps tests tools` — 0 changes; `./lane.sh ruff check dps tests tools` — All
  checks passed; `./lane.sh mypy dps` — Success: no issues found in 126 source files.
- `./lane.sh pytest -q` with only `dps-db` up: **1826 passed, 83 skipped** (baseline before G4: 1761
  passed, 83 skipped).
- Integration: with `dps-db` + `source-pg` + `minio` up (profile `sources`; `seed-sources` not started
  because no `data-privacy-services:m5` image exists in this lane):
  `./lane.sh pytest -q tests/connectors tests/test_no_values_in_logs_m2.py` → **322 passed**; full suite
  → **1897 passed, 12 skipped** (skips: no `[ner]`/presidio, no `mostlyai`, no OCR mock, no tesseract,
  no app container for the console check — extras/services outside this group's scope).
- New tests: `tests/test_policy_diff.py` (4), `tests/test_policy_store.py` (10, db),
  `tests/test_posture_m5.py` (24, one db), `tests/test_egress_tls.py` (14, one db),
  `tests/test_rekey.py` (2, db), `tests/test_llm_egress.py` (8), `tests/test_reports.py` +1 (db).
  TDD: every new test module failed first on the missing module (`dps.policy_store`, `dps.rekey`,
  `dps.llm.egress`) or missing rule, then passed.
- Frontend not touched (no npm gates needed). `docker compose --profile sources down` run afterwards;
  no other project's containers touched.

## Privacy evidence

- Audit rows of policy mutations carry only `{version, changes}`; the test asserts the change ticket
  never appears in any audit event. Policy-store errors name fields/methods/rules only.
- Posture messages and the posture report name settings, policies and key names only; tests assert
  the KEK, the tokenizer token sentinel, URLs and the bootstrap key never appear.
- Egress decisions: `test_egress_decisions_log_no_values` scans stdout/stderr/caplog for both strict
  and warn decisions — the protected value and the e-mail never appear; `LlmEgressBlocked` messages are
  counts only. `dps.rekey` prints counts only (test asserts no password sentinel, no KEK).
- M0's `dps_llm_raw_pii_sent_total` stays 0 for the whole test session (the warn test counts into a
  private counter).

## Deviations (full list in M5-DEVIATIONS.md, sections Task 15 / 16 / 16B)

Highlights: policy refs raise `CodedHTTPException` (L1-safe) instead of `DpsHttpError`; `publish` takes
`settings`; extra codes `policy_exists`, `invalid_preset`, `version_not_found`,
`invalid_change_ticket`/`invalid_note`; KEK posture rule only with `DPS_PG_HOST`; audit/embedded-worker
rules left to M2; the plan's short-KEK posture case is unreachable (Settings refuses it first);
`require_tls_connection` takes any engine+params object and also guards `open_target_store`;
`open_connector` refuses unimplemented engines before opening credentials; `/admin/posture` needs no DB;
`residual_values(messages, *, protected, region)` instead of a `detector` argument, with shape and
3-char word guards; tenant binding in `begin_call`; LLM audit counts only when the gateway was reached;
M0/M2 posture test helpers gained the M5 production settings; gateway test doubles accept `protected`.

## Open issues / notes for later work packages

1. **WP-I (M4 merge):** `POST /scans` must resolve `@name[:vN]` (`body.policy = await resolve(request,
   body.policy)`), like `/jobs/run`.
2. **WP-I (M3 merge):** M3's oracle/mongo branches in `open_connector` must sit after the TLS check
   (`_CONNECTORS` lists implemented engines); the Oracle connector must map `options.protocol=tcps`.
3. Jobs sealed under the previous KEK cannot be opened by workers holding only the new KEK until
   `python -m dps.rekey` has run (workers open with the current KEK only) — documented in the runbook;
   making workers try `DPS_JOB_KEK_PREVIOUS` too would remove the window (not done here).
4. Task 8B (deferred) must copy `LlmStats.counts()` into `progress.metrics.llm` in discovery/scan
   labelling; the roll-ups already sum it.
5. Engine report `llm.raw_pii_sent` is still M0's constant 0 (only a warn deployment can send one).
6. Task 25 must add the `PrometheusRule` alerts `DpsRawPiiSentToLlm` (any increase of
   `dps_llm_raw_pii_sent_by_tenant_total`, critical) and `DpsLlmExposureBlocked` (warning).
7. Test output noise (pre-existing): `tests/test_playground_samples.py` builds settings from the compose
   file (`DPS_PG_HOST=dps-db`), whose key refresher logs `key_refresh_failed error=gaierror`; visible now
   because an earlier test configures console logging. Harmless; all tests pass.
