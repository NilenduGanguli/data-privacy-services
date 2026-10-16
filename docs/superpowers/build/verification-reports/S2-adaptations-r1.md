# S2: adaptations verifier, round 1 (branch `m4` after main was merged into it)

**Verdict: FAIL. One blocking item (B1): a key without the `scan` scope can cancel scans.**

The seven checks in this lens all hold. Each was checked with probes I wrote myself and run on the live lane stack, not by re-running the authors' tests. Those checks are SEC-5, SEC-2 for the scanner, M5 database keys on every `/api/v1/scans` route, M5 posture for the browser-worker, M5 `@name` policy references, the discovery rules in scanner classification, and both sides kept in the merge.

While walking the scope rule I found one real gap. It sits next to the scans router: `POST /api/v1/jobs/{id}/cancel` cancels a **scan** for a database key that holds `jobs` but not `scan`. This breaks contract §8.1 ("`scan` covers scans"). It predates the merge (M4 lane code), the fix is small, and its severity is low to medium (details under B1). I did not change any repository file.

| | |
|---|---|
| Date | 2026-10-14, 21:33–22:06 IST |
| Tree | `dps-wt-m4`, branch `m4` @ `a0e8d26`. `git status` was clean at the start and at the end. HEAD did not move. |
| Running code | `sha256` of 15 lens files inside `app`, `worker` and `browser-worker` matches the worktree (`image_code_hashes.txt`), so the live probes exercise exactly `a0e8d26`. |
| Stack | Left as the gates verifier left it: up, healthy, not restarted or rebuilt, no `down`. |
| Probes | `~/.cache/dps-build/reports/S2-adaptations-r1/` (scripts + outputs; counts, codes and names only) |

## 1. Lens results

| # | Item | Result | Evidence (probe files) |
|---|---|---|---|
| 1 | **Merge kept both sides** (`b34dd07`, `4e55da3`) | **PASS** | See §2 |
| 2 | **SEC-5**: `storage_state_ref` resolved by main's `resolve_secret_ref(tenant_id, ref, settings)`, validated at accept, cross-tenant refused | **PASS** | `sec5_inprocess.txt`, `live_probe.txt` |
| 3 | **SEC-2** for scanner secrets and seeds | **PASS** | Compose/volume checks below, `custody_probe.txt` |
| 4 | **M5 DB keys + scope `scan` on every scans route** | **PASS on `/api/v1/scans/*`; B1 on `/api/v1/jobs/{id}/cancel`** | `live_probe.txt`, `jobs_route_scope_probe.txt` |
| 5 | **M5 posture, browser-worker role** | **PASS** | `posture_roles.txt` |
| 6 | **M5 `@name[:vN]` policy refs in ScanSpec** | **PASS** | `live_probe.txt`, `live_probe2.txt` |
| 7 | **Discovery rules in scanner classification** (mixed PII is never "not personal") | **PASS** | `discovery_semantics.txt`, `live_fields.txt` |

### 1.2 SEC-5

**Code.** `_storage_state` is the only place a storage state is resolved (`grep`: one call site, `open_runtime`).
- It resolves with `resolve_secret_ref(job.tenant_id, ref, ctx.settings)`. The tenant comes from the claimed job row.
- `validate_scan(..., tenant_id=)` runs `check_secret_ref`, and `POST /scans` passes the caller's tenant.
- `ScanAuth` takes `SECRET_REF_PATTERN`, and `..` is still refused.
- No other route creates scan jobs: `/jobs` only creates discover and run jobs, and `retry` refuses non-run kinds.

**In-process probe** (throw-away secret tree with tenants t1 and t2, `_shared` listed and unlisted, directory and file symlinks into t2; 20 cases, accept and load):
- The tenant's own session is accepted and loads its own session.
- A ref that names t2's folder gets 403 `secret_ref_foreign_tenant` at accept and is refused at load.
- A shared folder that does not list the tenant gets 403 `secret_ref_not_shared`.
- A hidden or reserved segment, or a malformed tenant, gets 422 `secret_ref_invalid`.
- `..`, an absolute path or upper case is refused by the model.
- A directory symlink into t2 gets 422 at accept. A file symlink is accepted at accept (no file is read then) and refused at load.
- **Cross-tenant reads: 0** (four t2 spellings were also sent straight to `resolve_secret_ref`).

**Live** (app on 8414; probe tenant `s2aprobe`, database key with `scan`):
- `default/dps/ui/demo-onboarding` (the real demo session's folder) → **403 `secret_ref_foreign_tenant`**.
- `_shared/ui/demo-onboarding` → 403 `secret_ref_not_shared`.
- `dps/.hidden/x` → 422 `secret_ref_invalid`.
- `..` traversal or an absolute path → 422 `auth_invalid`.
- No response echoed the ref, `default` or `demo-onboarding`. Nothing was queued (scan jobs 0 → 0). Each refusal was audited as `scan.create rejected` (+5).
- The same relative name that `default` uses (`dps/ui/demo-onboarding`), sent from `s2aprobe`, is accepted (202). The live browser-worker then fails it at load with **`secret_unavailable`, 0 pages visited, 0 navigations**. The stored spec holds only the ref (`storage_state: null`; no key or seed in `consistency`).

### 1.3 SEC-2 for scanner secrets and seeds

- **Compose:** `app`, `worker` and `browser-worker` all mount the same `dps-secrets` volume **read-only** with `DPS_SECRET_DIR=/var/run/secrets/dps`.
  - The browser-worker `extends: worker`. The old `./docker/secrets` bind mount and the `DPS_SECRET_DIR` override are gone.
  - `demo-secrets` (profiles `sources`, `scanner`, `full`) differs from main only by the copy of the demo session to `default/dps/ui/demo-onboarding/` (from a read-only `/seed/ui` bind) and by the extra profile. It still runs `rm -rf …/dps/dps`.
- **Live volume:** the only top-level folder is `default` (no pre-scoping `dps/`). `default/dps/ui/demo-onboarding/storage_state.json` is present in all three containers.
- **Read-only:** a write test failed with "read-only file system" in all three.
- **Images:** no `storage_state.json` is baked into the app or browser images, and `docker/secrets` is in `.dockerignore`.
- **Key and seed custody** (`custody_probe.txt`): a scan's returned key material and seed were kept in memory only.
  - **0** occurrences in 8 metadata tables (job, audit, scan pages/fields/requests/events, evidence, artefacts).
  - `key_enc` was purged after the terminal state.
  - **0** occurrences in 1,519 log lines of app, worker and browser-worker. The probe database keys' prefixes and the bootstrap literal also appear 0 times.
  - The create answer carries `Cache-Control: no-store`.

### 1.4 M5 database keys and the `scan` scope (live)

Database keys were minted through `/admin/keys`.

**Every operation under `/api/v1/scans` in the live OpenAPI (10 operations):**
- A database key with `scan` is never 401 or 403.
- A database key with `jobs`+`detect` gets **403 on all 10**.
- No key gets **401 on all 10**.
- Another tenant's `scan` key gets 404 on every `{scan_id}` route, and 403 when it names `default`.
- The `POST` bodies were refused or stateless, so no scan was queued.

**Scan evidence and artefacts:**
- `/evidence/{id}`, `/html`, `/pdf` (M5) and `/artefacts/{id}` (an `original` capture): the `scan` key gets 200 and the jobs-only key gets **403 `scope_required`**.
- From another tenant, the same IDs give 404.
- `/reports/evidence` shows a jobs-only key no scan pack.
- Audit rows name the database key ID. A disabled key went **200 → 401 at once**.

**M5 tenant settings:** hosts set on M5's `/admin/tenants` decide what the tenant may scan.
- `[portal.example.org]` → 422 `host_not_registered`.
- `[DEMO-SITE]`, normalised by M5 → accepted.
- Restored to `[]` afterwards.

The queue quota counts scans (`jobs.create_job`, every kind).

### 1.5 M5 posture, browser-worker role

The probe checks a shared production config one rule at a time.
- The browser-worker has **no** problems, while the app's rule set is unchanged. The app alone flags API keys, the OCR key and the tokenizer token.
- 18 single-rule violations flagged the browser-worker exactly as they flagged the app. These cover auth, the bootstrap key, the LLM stub, audit, unmasked originals, a headed browser, inline secrets, RLS, strict audit, the PG root certificate, the embedded worker, egress TLS, http tokenizer/OCR/S3 URLs, the CA bundle, verify-warn and the LLM egress check.
- Only `CORS *` and `OCR auto with nothing` are app-only, as intended.
- A literal `DPS_TOKENIZER_TOKEN` env var is still refused for the browser-worker. An unknown role raises `ValueError`.
- The skipped http-OCR and http-tokenizer cases stay covered by the kept TLS-egress rule.
- The browser-worker never calls OCR or the tokenizer. Every scanner `ApplyOptions` has no tokenizer, so `external_tokenize` fails closed.
- `amain` calls `assert_production_posture(settings, role=BROWSER_WORKER)` before the database is touched (image hash matches).

### 1.6 M5 `@name` policy references (live)

**Accept time** (probe tenant; policy adopted from a preset, then v2 published with `min_score` 0.55):
- `@<name>` → 202 and the stored spec pins **v2** (`min_score` 0.55).
- `@<name>:v1` → 202 and pins **v1** (0.6).
- `@nope` → 404 `policy_not_found`; `:v9` → 404; `@Not-A-Ref` → 400 `invalid_policy_ref`. Each refusal was audited (+3).
- `default` naming the probe tenant's policy → **404** and nothing queued.

**End to end** (`live_probe2.txt`): a scan accepted with `@<name>:v1` while v2 was active ran in the live browser-worker and succeeded.
- Its evidence names the store name and **version "1"**.
- **The evidence `policy.hash` equals the store's v1 hash, not v2's.**
- 0 submissions, 0 non-GET requests allowed, demo-site counters +0.
- `verify_warn_disallowed` comes from a `CodedError`. `to_http` keeps its status and code at accept, and `open_runtime`/`_policy_name` catch `CodedError` (code read).

### 1.7 Discovery rules in scanner classification

`classify_fields` is the crawler's entry point. The other `classify_field` call sits only on the filled-screen masking path, where `sensitive_prefilled` masks any detector hit.

**In-process probe** (the crawler's detector, policy bar 0.6):
- `Value` next to a `Channel` select gives `MIXED_PII` (`mixed:value_next_to_channel`), even when empty. The same holds for "Preferred contact method" + "Your contact details".
- A value in another form, or "Loan value", is not mixed.
- A textarea showing a phone number inside text gives `FREE_TEXT` (`value_span:PHONE`).
- A select whose chosen option is an e-mail gives `EMAIL`, and so does an unnamed input pre-filled with one.
- Role names ("Nominee name", "Account holder", "Beneficiary name") give `PERSON_NAME`.
- A field showing personal data is both classified and masked in captures.
- L2 discovery on `contacts(channel, value)` gives `value: MIXED_PII` through the same shared constants.

**Live:** the newest real demo-site scan has 2 fields classified only by the new shown-value rule (`value:PHONE`, `value:EMAIL`). Across `default`'s succeeded scans there are 727 personal and 168 not-personal fields; `/reports/summary` `layers.ui.personal_fields` is also 727.

## 2. Merge kept both sides (`merge/`)

**`b34dd07` (23 conflicted files, recomputed with `git merge-tree`).** I re-derived every hunk with `git merge-file --diff3` and checked that each side's lines survive.
- **Code hotspots lost 0 lines:** `docker-compose.yml`, `dps/app.py`, `dps/config.py`, `dps/db.py`, `dps/observability.py`, `pyproject.toml`, `tests/test_compose.py`.
- `tools/smoke_test.py` lost only one import line per side, which was merged into a single import.
- **Frontend conflicts were checked by meaning, not by line.** Main's M5 Overview, Reports and EvidencePack pages replace M4's simpler versions but already handle scans:
  - Scan rows (`lib/reports.ts`, M4 job-ID heuristic) and the scan pack's canonical JSON download are kept, the download moved into `api.evidence.json`.
  - `isScanPack`/`ScanPackSections` are still rendered, Overview keeps the scan-aware links, and main's "unfinished" verification chip is kept.
  - Both fixture servers are chained in `main.tsx`, and both `render.tsx` helpers are kept.
  - M4's `jobs.test.ts` scan tests are present. `package.json` build keeps `check-bundle`. Main's `EvidenceSummary` is a superset of M4's.
- **Contract, HANDOFF, README:** the changed rows are reworded into richer forms (migration-range rows checked).
- **Edits beyond conflict resolution** (merge vs auto-merge tree): frontend adaptations (`scanFixtureServer` rename, `api.evidence.json` scan fallback plus its test, `fromSummary` scan layer) and a `test_migrations` range assert of equal strength. No Python source was touched outside the conflicts.

**`4e55da3`** equals the clean auto-merge (empty diff).

**Survival to HEAD.** Every addition of either parent to 25 hotspot files is present at `a0e8d26`, except 36 lines that are intended adaptations or reworded comments:
- the posture `serves` signature;
- the M4 bind mount → the SEC-2 volume;
- `SECRET_REF` → `SECRET_REF_PATTERN`;
- the risk-flags query that now also sums personal fields;
- the M4 compose test assertions replaced by SEC-2 ones.

The adaptation commits changed 8 tests. None was weakened: the changes are additive asserts, signature updates, and a longer wait only on the popup positive controls.

## 3. Blocking finding

### B1: a database key without `scan` cancels scans through `POST /api/v1/jobs/{id}/cancel` (contract §8.1)

**Reproduced live** (`jobs_route_scope_probe.txt`). Probe tenant with `max_running` 1, a `scan` key and a key with **only** `jobs`:
- The jobs-only key is refused by the scans router: `GET /scans/{id}` and `POST /scans/{id}/cancel` both give **403**.
- But **`POST /api/v1/jobs/{queued scan}/cancel` gives 200 → `canceled`**.
- And **`POST /api/v1/jobs/{running scan}/cancel` gives 200 → `finalize/queued` → `canceled`**.
- The audit table holds 2 `job.cancel ok` rows by that key (`s2a-jr-jobs`, scopes `[jobs]`).
- The same key also reads scan job rows through `GET /jobs/{scan id}` and `GET /jobs?kind=scan` (8 rows). Those rows are value-free, and M4 documented that `GET /jobs` lists every kind.
- `retry`, `pause` and `resume` refuse scans by kind or phase, so cancel is the only state change.

**Why it matters.** Contract §8.1 says `jobs` covers connections, discovery, snapshots, runs, groups and evidence, and `scan` covers scans. M4 already applies the job kind's scope to evidence and artefacts (`require_kind_scope`). M5 adds per-integration database keys whose point is least privilege (e.g. a "crm-provisioner" key with `jobs`+`detect`). That key can stop the tenant's UI scans: it can stop a scan, but it cannot read a scan's content, start one, or touch another tenant.
- **Severity:** low to medium (integrity and availability inside one tenant).
- **Origin:** the M4 lane, not introduced by the merge. a3e6751's "every scans route" test walks only `/api/v1/scans*`.

**Fix (small; do not weaken any existing test):**
1. In `dps/layers/api/routers/jobs.py::_control` (cancel/pause/resume), and preferably in `get` and `partitions` too, read the job's kind with `job_kinds(tenant, [job_id])`, as `artefacts.py` does.
2. Call `require_kind_scope(principal_of(request), kind)` before acting. A scan then needs `scan`, otherwise 403 `scope_required`, audited `job.cancel rejected`.
3. Optional, matching `/reports/evidence`'s `include_scans=has_scope("scan")`: leave scans out of `GET /jobs` for keys without `scan`. The console's Jobs table already filters scans out.
4. Add a test: a jobs-only database key gets 403 on `/jobs/{scan_id}/cancel` and the scan stays queued or running. A `scan` key still cancels through `/scans/{id}/cancel`.
5. Extend `test_database_keys_with_the_scan_scope_drive_every_scans_route` with `/api/v1/jobs/{scan_id}*`.

## 4. Non-blocking notes

- **N1:** M5's policy store counts pinned jobs with `kind = 'run'` only (`dps/policy_store.py::_job_counts`). Scans now pin `@name:vN` versions but are missing from "N jobs use it" and "N jobs pinned to vX" on the Policies page (live: 0 after 3 pinned scans). Suggest counting scans, or wording the label as "jobs (scans not counted)".
- **N2:** A scan that fails at setup (`secret_unavailable`, `policy_invalid`, `host_not_registered`, …) ends `failed` with its code and the key purged, but **without an evidence pack**. This is M4's design in `scan_job`, unchanged here. Cancel and normal finishes write packs.
- **N3:** The `scan.finalize` audit row labels the policy with the body's `name` (`<name>`, not M5's `<name>@v<N>`; an inline dict's own name otherwise). Navigation rows keep `policy` empty for dict policies. Evidence carries the hash (and run evidence also takes `version` from the body), so attestation holds; this is a label consistency point only.
- **N4:** Main's SEC-2 rule refuses a ref whose first segment names any top-level folder other than the caller's tenant. That is fail-closed. A tenant literally named `dps` would make every other tenant's `dps/…` refs `secret_ref_foreign_tenant`. This is a deployment naming note on main's rule.
- **N5:** b6f7e82 changed `031_tenant_hosts.sql`'s checksum. Any dev database that applied the lane's old 031 refuses to migrate (`MigrationDrift`) until it is recreated. The lane database has no drift (19 files, the ledger matches the worktree). Main and production never applied the old 031.
- **N6** (carried from S2-premerge item 2): the L2 `worker` still calls `assert_production_posture(settings)` with the app's rules, so it needs `DPS_API_KEYS` in prod. This is for F-deploy.

## 5. State left on the lane stack (for the other verifiers)

- **Probe tenant and objects:** tenant `s2aprobe` (registered hosts and `max_running` restored to null) and its policy `s2a-pol-994859` v1/v2 (policies cannot be deleted, by design).
- **Probe jobs:** 8 jobs in `s2aprobe` (5 failed at load, 1 succeeded, 2 canceled), plus their audit rows.
- **Probe keys:** 12 database keys named `s2a-…`, **all disabled** (0 active).
- **Demo-site counters:** every probe window of mine was **+0** (`non_get`, `form_get`, `logout_hits`, `admin_hits`, `by_path` unchanged).
  - Between my windows, `non_get` went 16 → 20 (`POST /api/draft` +4). That is a positive-control path, and other verifiers were active then (S2-d4-network-r1 probes 21:43–21:55).
  - Take a fresh baseline before any D4 delta.
- **Files:** no repository file modified; `git status` empty.

## 6. Probe files

| File | What it checks |
|---|---|
| `merge/both_sides.py`, `b34dd07_both_sides.txt`, `where_moved.py`, `b34dd07_where_moved.txt`, `survival.py`, `hotspot_survival.txt` | Merge both-sides checks |
| `sec5_inprocess.py` / `.txt` | SEC-5 accept and load, in-process |
| `live_probe.py` / `.txt` | SEC-5 live, policy refs, DB-key route walk, kind scopes, cross-tenant IDs |
| `live_probe2.py` / `.txt` | `@name:v1` end to end |
| `custody_probe.py` / `.txt` | Key, seed and API-key custody in the DB and logs |
| `posture_roles.py` / `.txt` | Posture role differential |
| `discovery_semantics.py` / `.txt` | Discovery rules in classification |
| `live_fields.txt` | Live field classifications and summary counts |
| `live_hosts.txt` | M5 hosts drive scan accept |
| `jobs_route_scope_probe.py` / `.txt` | B1 |
| `image_code_hashes.txt` | Running code matches the worktree |
