# S2 — adaptations verifier, round 2 (branch `m4` after main was merged into it)

**Verdict: PASS. Nothing in this lens blocks.**

Every adaptation holds on the live stack and in-process. My probes are new and independent of round 1:
fresh tenants, fresh M5 database keys, and in-process checks against the worktree code. The round-1 blocker
(B1: a key without `scan` could cancel scans through `/api/v1/jobs/{id}/cancel`) is fixed. That fix holds on
every `/jobs/{job_id}…` operation, on `GET /jobs`, `/evidence`, `/artefacts` and `/reports/evidence`, with
a queued and a running scan whose states did not change.

Five non-blocking notes follow in §3. Two are new: the summary route shows scan briefs to `detect`-only keys,
and the browser-worker has a migrations-mode posture trap. The merge also dropped one cosmetic compose label.
I did not change any repository file.

| | |
|---|---|
| Date | 2026-10-15, 01:16–01:50 IST |
| Tree | `dps-wt-m4`, branch `m4` @ `a3cd667` (= `origin/m4`). `git status` was empty at the start and at the end. HEAD did not move. |
| Running code | `sha256` of all 170 tracked `dps/**/*.py`, `dps/migrations/*.sql` and `pyproject.toml` inside `app`, `worker` and `browser-worker` equals the worktree: 0 differing lines (`image_code_hashes.txt`). |
| Stack | Left as the gates verifier left it: up and healthy. I did not restart, rebuild or `down` it. |
| Probes | `~/.cache/dps-build/reports/S2-adaptations-r2/` (scripts and outputs; codes, counts and names only) |

## 1. Lens results

| # | Item | Result | Evidence |
|---|---|---|---|
| 1 | **SEC-5**: `storage_state_ref` resolved by main's `resolve_secret_ref(tenant_id, ref, settings)`, checked at ScanSpec accept, cross-tenant refused | **PASS** | `sec5_inprocess.txt` (34/34), `live_probe.txt`, `sec5_positive_control.txt` |
| 2 | **SEC-2** for scanner secrets and seeds | **PASS** | `sec2_volume.txt`, `live_probe.txt` (custody) |
| 3 | **M5 database keys and the `scan` scope** on every scans route, plus the S2-r1 B1 fix on `/jobs` | **PASS** | `live_probe.txt`, `key_rotation_probe.txt` (8/8), `keys_audit.txt` |
| 4 | **M5 posture rules, browser-worker role** | **PASS** | `posture_roles.txt`, `posture_roles_supp.txt` |
| 5 | **M5 `@name[:vN]` policy references in ScanSpec** | **PASS** | `live_probe.txt` (accept, pin, end to end) |
| 6 | **Discovery semantics in scanner classification** (mixed PII is never "not personal") | **PASS** | `discovery_semantics.txt` (13/13), `live_fields.txt` |
| 7 | **The merge kept both sides** (`b34dd07`, `4e55da3`), checked to HEAD | **PASS**; one cosmetic line lost (N3) | `merge/*` |

### 1.1 SEC-5

**Code (read).** `dps/layers/scanner/__init__.py::_storage_state` is the only place a storage state is resolved.
- It calls `resolve_secret_ref(job.tenant_id, ref, ctx.settings)`. `dps/secrets.py` is byte-identical to main's.
- `validate_scan(..., tenant_id=)` runs `check_secret_ref` for the caller's tenant.
- Every secret access in `dps/` goes through `dps/secrets.py`. No route lists secret folders.

**In-process** (`sec5_inprocess.py`). I built a throw-away `DPS_SECRET_DIR` with tenants acme, beta and gamma, a `_shared` folder, directory and file symlinks, and malformed states. Then I ran the real `open_runtime` with a sealed `JobSecret` and a stub host store.
- 28 accept-and-load pairs passed.
- Own folder: accepted, and loads the tenant's own state.
- Another tenant's relative name: accepted, then `secret_unavailable` at load.
- A ref naming another tenant's folder: 403 `secret_ref_foreign_tenant`.
- Shared folder that does not list the tenant: 403 `secret_ref_not_shared`.
- A directory symlink out of the folder is 422 at accept. A file symlink is refused at load.
- `..`, an absolute path, upper case or `\n`: refused by the model. A tampered stored row gives `spec_invalid`.
- An old pre-scoping top-level `dps/` folder: fails closed for every tenant.
- **Cross-tenant reads: 0.**
- A spy confirmed `open_runtime` calls `resolve_secret_ref` exactly once, with the job's own tenant, the stored ref and `ctx.settings`. It also asks for the registered hosts of the job's own tenant.

**Live** (tenant `s2r2a`, database key with `scan` only):
- `default/dps/ui/<demo>` and `default` → 403 `secret_ref_foreign_tenant`.
- `_shared/…` → 403 `secret_ref_not_shared`.
- Hidden or reserved segment → 422 `secret_ref_invalid`.
- `..`, absolute or upper case → 422 `auth_invalid`.
- 0 of 9 refusals echoed the ref, a tenant name or the session name. 0 jobs were queued, and +9 `scan.create rejected` audit rows were written.
- Tenant B's key naming tenant A → 403.
- `default`'s relative name `dps/ui/demo-onboarding`, sent from `s2r2a`, is accepted. The live browser-worker then fails it with **`secret_unavailable`, 0 pages, 0 navigations, key purged**.
- The stored spec holds only the ref (`storage_state: null` in all 7 specs; `live_probe_addendum.txt`).
- **Positive control:** the newest full smoke scan of `default` used the same ref. It reached `/profile`, `/app` and 11 more logged-in pages with 0 `auth_redirect`, and its evidence records only `source.auth = "storage_state_ref"`.

### 1.2 SEC-2 for scanner secrets and seeds

**Resolved compose** (`config`, profiles `scanner` and `sources`):
- `app`, `worker` and `browser-worker` mount `dps-secrets` at `/var/run/secrets/dps` **read-only**, with `DPS_SECRET_DIR=/var/run/secrets/dps`.
- The browser-worker `extends: worker`: read-only root, `cap_drop: ALL`.
- `demo-secrets` copies the committed demo session from the read-only `/seed/ui` bind to `default/dps/ui/demo-onboarding/`.

**Live containers:**
- The volume is mounted `RW=false` in all three. `os.access(W_OK)` is false.
- The only top-level folder is `default`.
- `default/dps` holds `s3 src tgt ui`, and `ui` holds `demo-onboarding`.
- No `storage_state.json` exists outside the volume in the app or browser images.
- `git ls-files docker/secrets` lists only the tenant-scoped demo literal, and `docker/secrets` is in `.dockerignore`.
- `Dockerfile.browser` copies only `pyproject.toml`, `README.md` and `dps/`.

**Custody of the key and seed** (14 `dek`/`seed` strings returned by 7 accepted scans, held in memory only):
- **0** occurrences in 175 tenant rows of `dps_job`, audit, scan pages/fields/requests/events, evidence and artefacts.
- **0** occurrences in 1,685 app, worker and browser-worker log lines, and 0 raw API keys or bootstrap literal in them.
- `key_enc` was purged on all 7 terminal scans.
- The create answer carries `Cache-Control: no-store`.

### 1.3 M5 database keys and the `scan` scope (live)

**Every operation under `/api/v1/scans` in the live OpenAPI (10):**
- A `scan` database key is never 401 or 403.
- A `jobs`+`detect` database key gets **403 on 10/10**.
- No key gets **401 on 10/10**.
- Tenant B's `scan` key gets **404 on 7/7** `{scan_id}` operations, and 403 everywhere when it names tenant A.

**S2-r1 B1 fix, with S1 running and S2 held queued by `max_running=1`.** A jobs-only database key got **403 `scope_required`** on all 12 of these:
- `GET /jobs/{id}` and `/partitions`;
- `POST cancel`, `pause`, `resume` and `retry`, on both S1 and S2.

The 8 controls were audited `job.<action> rejected` under that key's id. Neither state changed: S1 stayed running and S2 stayed queued. For the same key:
- `GET /jobs` lists 0 scans; `?state=queued` omits S2; `?kind=scan` → 403.
- A `jobs`+`scan` key lists both scans and reads S2.
- After the walk, the `scan` key cancelled queued S2 through `/scans/{id}/cancel`.

**Evidence, artefacts and reports for S1:**
- `/evidence/{id}`, `/html`, `/pdf` and `/artefacts/{screenshot}`: scan key 200, jobs-only key **403 `scope_required`**, tenant B 404.
- `/evidence?job_id=S1` lists nothing for the jobs-only key, and neither does `/reports/evidence`.
- A `jobs`+`scan` key sees the scan pack.

**Key lifecycle:**
- A disabled key goes 200 → **401 at once**.
- Rotation with grace 0: the old key is 401 at once, and the successor gets 200 on `/scans` and still 403 on `/jobs`.
- Rotation with grace 1 h: both keys work during the grace.
- A multi-tenant scan key with no `X-DPS-Tenant` → 403; with the header → 200; an ungranted tenant → 403.
- `created_by` on every probe scan and every audit row names the probe database key.

**M5 hosts:** tenant hosts `[portal.example.org]` → 422 `host_not_registered`; `[DEMO-SITE]` → accepted; restored to `[]`. Posture reports `scan_fallback_hosts` `['localhost','127.0.0.1','demo-site']` (env local).

### 1.4 M5 posture, browser-worker role (differential)

I took a production config the app accepts and changed one rule at a time, 30 mutations in all. For each, I evaluated the `app`, `worker` and `browser-worker` roles.
- `worker` == `app` on every mutation.
- `browser-worker` == `app` on every rule, except the components it never runs. Those are app-only: no API keys, the Azure OCR key, "OCR auto resolves to none", the tokenizer token and `CORS *`.
- An `http` OCR, tokenizer or S3 endpoint still refuses the browser-worker through the TLS-egress rule, with the same message.
- All 10 literal `SECRET_ENV` variables refuse the browser-worker too. `DPS_JOB_KEK` was checked with a well-formed literal.
- `migrations_mode=off` is refused for both roles. An unknown role raises `ValueError`. Local and dev apply no rule.
- `amain` raised `PostureError` before the database, the ops port or Chromium was touched.
- The scanner's `ApplyOptions` never carry a tokenizer.

### 1.5 M5 `@name` policy references (live)

**Setup:** in `s2r2a` I adopted v1 from a preset, then published v2 with `min_score` 0.55. The two hashes differ.

**At accept:**
- `@<name>` → 202, and the stored spec pins **v2** (0.55).
- `@<name>:v1` → 202, pins **v1** (0.6).
- `@<name>:v2` → 202, pins v2.
- `@s2r2-nope` and `:v9` → 404 `policy_not_found`. `@Not-A-Ref` → 400 `invalid_policy_ref`.
- From tenant B and from `default` → 404.
- Only the 3 resolvable references were queued.

**End to end:**
- S1 was accepted with `@<name>:v1` while v2 was active, and ran in the live browser-worker: succeeded, 24 pages.
- Its **evidence `policy.hash` equals the store's v1 hash, not v2's.** The evidence names the store policy and version 1.
- 0 submissions and `non_get_allowed` 0.

### 1.6 Discovery semantics in classification

**Shared constants:** `classify.py` and L2 `discovery.py` use the **same objects** (identity checked): `CHANNEL_NAMES`, `MIXED_VALUE_NAMES`, `MIXED_NAME_SCORE`, `MIXED_PII` and `FREE_TEXT`. `27cf286` moved them into `structural.py` with identical values.

**L2 parity:**
- L2 `contacts.value` and the scanner's `Value` next to `Channel` both give `MIXED_PII` (`mixed:value_next_to_channel`).
- With only the name cue, both are mixed at bar 0.6 and neither is at 0.8.
- `loan value` is not mixed on either side.

**Scanner grouping rules:**
- "Preferred contact method" + "Your contact details" → `MIXED_PII`, and so does "Contact type" + "Contact".
- A value in another form or frame → not mixed.
- A value outside any form, next to a channel outside any form → `MIXED_PII`.
- A password input is classified by its type and its value is never read.

**Property sweep:** 1,836 fields = 3 locales × 17 synthetic values × 12 neutral labels × 3 shapes (a pre-filled input, a textarea with the value inside a sentence, and a select whose chosen option is the value).
- **Every one of the 1,044 fields whose shown value the detector flags MEDIUM+ at the bar is classified. 0 are "not personal".**
- The rest are detector scope, not the rule: never-issued 9xx SSNs, `QQ` NINOs, plain dates, and locale-specific numbers.

**Negatives** stay "not personal": plain text, a quantity, a country choice, and hidden and file inputs.

**Live:**
- Newest full smoke scan: 63 fields, 51 personal. 1 field is classified only by the shown-value rule (`value:EMAIL`), and 0 pre-filled fields are "not personal".
- `default`: page `personal_field_count` sum 1,115 = 1,115 personal field rows = `/reports/summary` `layers.ui.personal_fields`.

## 2. Merge kept both sides (`merge/`)

**`4e55da3`:** its tree is **identical** to the clean auto-merge of its parents (`f823429`).

**`b34dd07`** (23 conflicted files, recomputed with `git merge-tree` in an isolated `--shared` clone):
- **Code hotspots lost 0 lines on either side:** `dps/app.py`, `dps/config.py`, `dps/db.py`, `dps/observability.py`, `pyproject.toml` and `tests/test_compose.py`.
- The other flagged lines are all resolved by meaning:
  - **Frontend:** M4's `fixtureServer.ts` content survives **100 %** (267/267 lines) in `scanFixtureServer.ts`, and its test 158/159 (only the import renamed). `main.tsx` chains both fixture servers. `render.tsx` keeps `html`, `render`, `text` (now a superset) and `occurrences`. `EvidencePack` keeps main's "unfinished" chip and M4's `isScanPack`/`ScanPackSections`. Main's vitest `include` (`src/**/*.test.ts`, `src/**/*.test.tsx`) equals M4's `{ts,tsx}` glob: 27 test files.
  - **Tests and tools:** `test_migrations` keeps an equal-strength M4 range assert, and `smoke_test.py` imports were merged into one line.
  - **Docs:** HANDOFF, README and contract facts were reworded; spot-checked present.
- **One real loss:** the `demo-site` compose service lost its `env: local` label (m4 side, b34dd07; see N3).
- **Edits beyond the auto-merge** in 13 non-conflicted files: frontend adaptations and additive tests. `check-bundle` markers were widened, which is stricter. `test_migrations` got a range assert. **No `dps/` source was touched outside the conflicts.**

**Survival to HEAD over every file**, not only the hotspots (`survival_all.txt`, `moved_check.txt`):

| Side | Lines added | Not at HEAD | Explained by a later adaptation or fix commit | Moved to another file | Absent everywhere |
|---|---|---|---|---|---|
| main (88eae2e..144cc65) | 44,117 | 82 | 67 | 3 | 12 |
| m4 (88eae2e..bdbdc0a) | 21,338 | 554 | 128 | 384 | 42 |

- I reviewed every line that is absent everywhere. They are reworded comments (`posture.py`, `scanner/models.py`, `dom.py`, `seed_model.py`), the merged frontend resolutions above, and the seed-model-2 rewrites of `a3cd667`.
- The fix round removed only dead-code tests (`isScanJobId`/`scanEvidenceRow`). It replaced the demo-id format assertions with stricter ones (`never_issued`).
- Adaptation commits reworked main's posture (`fb45a44`) and discovery constants (`27cf286`). The §1.4 and §1.6 probes show their behaviour is unchanged.

**Forward check (next merge, isolated clone): `m4` into current `main` (`d2fdc3f`)**
- It conflicts only in the contract §8.1 sentence. Main extends the `admin` requirement to every production write and to retry; m4 adds the job-kind rule. **Keep both.**
- `dps/layers/api/routers/jobs.py` merges cleanly. It keeps `_owned`/`_check_kind`/`exclude_kind` and main's `check_retry_scope` on retry.

## 3. Notes (non-blocking)

- **N1 — `/reports/summary` and scan briefs** (new, live). With a `detect`-only database key, `recent_jobs` listed 6 scans (id, state, `source` = scanned host), and `layers.ui.last_scan.id` is a scan id.
  - This is as the contract §9 M5 row documents: scope `detect`, the scan's `source` = host, S2 r1. Run briefs are shown the same way.
  - Nothing more is reachable with those ids (`GET /scans/{id}` → 403).
  - Owner decision: whether §8.1's job-kind rule should also filter `recent_jobs` and `last_scan`. `/jobs/stats` state counts include scans as aggregates only.
- **N2 — browser-worker migrations mode** (new). Posture refuses `DPS_MIGRATIONS_MODE` ≠ `verify` for the browser-worker in staging and prod; the authors' test asserts this. But the browser-worker never verifies migrations (`amain` has no such step, and `init_pool` does not either), and compose runs it with `off`.
  - For F-deploy: its OpenShift manifest must set `verify`, or the pod refuses to boot.
  - Better: have the browser-worker run `db.verify_migrations` when the mode is `verify`, as `dps/worker.py::_amain` does.
- **N3 — merge loss (cosmetic).** `docker-compose.yml` `demo-site.labels` lost `env: local` in `b34dd07`; it was present on `bdbdc0a`. Fix: add `env: local` under that service's `labels`. No test checks it: `test_compose` asserts `app` only.
- **N4 — carried from round 1, still open:** `policy_store._job_counts` counts `kind = 'run'` only. Live: 0 "jobs use it" after 5 scans pinned v1 and v2.
- **N5 — carried from round 1, unchanged:**
  - N2: a scan that fails at setup has no evidence pack.
  - N3: the `scan.finalize` audit names the policy by its body name without `@vN`, and `scan.create` rows carry no policy label; runs are the same.
  - N4: a top-level folder named like another tenant makes refs fail closed.
  - N5: lane-only `031` checksum.
  - N6: the L2 `worker` still needs `DPS_API_KEYS` in prod.
- **Outside this lens (for D4 colleagues).** The API applies no default deny list. The console pre-fills `['/logout','/admin']`, and the smoke and spec send it.
  - My end-to-end scan S1 sent none. It followed the demo site's "Log out" GET link once (`logout_hits` 6 → 7 at about 01:30 IST; page 8 `redirected`).
  - Its evidence: 0 submissions, `non_get_allowed` 0, POST ×10 blocked.
  - `non_get`, `form_get` and `admin` were +0 over my probe window. `non_get` 52 → 56 after 01:33 came from other verifiers' positive controls.

## 4. State left on the lane stack

- **Tenants:** `s2r2a` (`max_running` reset to null, hosts `[]`) and `s2r2b`. Policy `s2r2-pol-013014` v1 and v2 (policies are not deletable).
- **Probe scans** in `s2r2a`: 7 jobs (5 failed at load, by design; 1 succeeded, S1; 1 canceled, S2).
- **Keys:** 11 probe database keys `s2r2-…`, rotation successors included, **all disabled** (0 active, checked in the database).
- **Demo-site counters at the end:** 56/12/7/6.
- **Files:** no repository file modified; `git status` empty; HEAD `a3cd667`. The isolated clone used for `merge-tree` is under `merge/clone`.

## 5. Probe files (`~/.cache/dps-build/reports/S2-adaptations-r2/`)

| File | What it checks |
|---|---|
| `image_hashes.sh`, `image_code_hashes.txt` | Running code = worktree |
| `sec5_inprocess.py` / `.txt` | SEC-5: accept + load + `open_runtime` spy |
| `sec5_positive_control.txt` | `default` session resolves for `default` only |
| `sec2_volume.txt` | Mounts, read-only, layout, images |
| `live_probe.py` / `.txt`, `live_probe_addendum.txt` | SEC-5 live, policy refs, hosts, S1/S2 states, OpenAPI walk, `/jobs` rule, evidence, artefacts, reports, custody, D4 |
| `keys_audit.txt`, `key_rotation_probe.py` / `.txt` | Key ids in audit and `created_by`; rotation; multi-tenant keys |
| `posture_roles.py` / `.txt`, `posture_roles_supp.py` / `.txt` | Role differential |
| `discovery_semantics.py` / `.txt`, `live_fields.py` / `.txt` | Discovery semantics |
| `merge/both_sides.py` / `.txt` / `.json`, `b34dd07_*.txt`, `survival_all.py` / `.txt`, `moved_check.py` / `.txt`, `trial_m4_into_main.txt` | Merge both sides, survival, forward trial merge |
