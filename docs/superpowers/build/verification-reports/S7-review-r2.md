# S7 review, round 2: finish-stage docs (M5 Task 27) and F4 polish, branch `fdocs`

- **Reviewed:** `/Users/neelu/document_intelligence/dps-wt-fdocs`, branch `fdocs` at `f4970da`. That is 23 commits on
  `main`'s `64c46e3`, five of them added since round 1's head `3dc5d73`: `7bb202c`, `a6ebfa7`, `08949db`, `616f63a`
  and `f4970da`. `origin/fdocs` = `f4970da`.
- **Inputs:**
  - the implementer report `S7-impl.md`;
  - round 1's `S7-review-r1.md`;
  - the fix report `S7-fix-r1.md`;
  - the M5 plan's Task 27 and its DoD;
  - the programme plan §2.5–§3, §5 and §6, plus the §2.6 F4 and F5 rows;
  - RESUME §0–§3 and §10;
  - `M5-DEVIATIONS.md` (Task 27 and the F4 sections).
- **Verdict: PASS. Nothing blocks.**
  - Both blocking findings of round 1 are fixed. I verified each one independently, in the browser, by a mutation
    check and against the newest `fos` manifests.
  - Every gate is green.
  - The plan's Task 27 acceptance and programme plan §2.6 F5 are met. F4 is met: M1-N, A-N1 and A-N2 are closed,
    and the remaining items are either fixed or deferred in the CHANGELOG under "Known limitations".
  - I found seven non-blocking items (§5). The most useful are a late write *error* shown under the wrong policy
    (N1), the pool-size wording in the settings table (N2) and a lane-safe compose example that clashes on the
    MongoDB port (N3). §6 lists three heads-ups for the integrator.
- I modified no repository file. Probes, logs and screenshots are in `~/.cache/dps-build/reports/S7-review-r2/`.

## 1. Gates (run by me, in the lane)

| Gate | Result |
|---|---|
| `./lane.sh ruff format --check dps tests tools` · `ruff check dps tests tools` | 385 files already formatted · all checks passed |
| `./lane.sh mypy dps` · `mypy tools/settings_table.py tests/test_handoff.py` | no issues in 134 source files · no issues in 2 files |
| `./lane.sh python tools/settings_table.py --check` | `settings table current (165 settings)`, exit 0 |
| `./lane.sh pytest -q -rs` (lane `dps-db` up on 5523, nothing else running) | **2384 passed, 267 skipped**, 1 warning, 101.8 s (`pytest-full.log`); see the skip breakdown below. This matches the fix report exactly. |
| `tests/test_handoff.py` plus the S7 tests (shots, demo group, policy bounds, evidence PDF, future day, bench, smoke cursor) | 24 passed, 2 skipped (the two manifest tests). The three db tests and the three demo-group tests show `PASSED` one by one against the lane DB (`-rA`). |
| `cd frontend && npm test` | node `--test` **42/42** + Vitest **261/261** (20 files) (`frontend-test.log`) |
| `cd frontend && npm run build` | `tsc --noEmit` and the vite build are OK; only the existing warning about chunks over 500 kB remains (`frontend-build.log`) |

All skips are services that were down, plus three accepted cases:

| Skip reason | Count |
|---|---|
| Oracle down | 92 |
| source-pg down | 89 + 2 |
| Mongo down | 60 |
| App down | 6 + 3 |
| OCR mock down | 3 |
| MinIO down | 3 |
| Host tesseract missing | 2 |
| `deploy/openshift/base` absent (by design) | 2 |
| `mostlyai` not installed | 2 |
| `pandas` not installed | 1 |
| spaCy model missing | 2 |

**Manifest tests, run against the newest `fos`:**

- The two `test_handoff.py` tests that skip on this branch were run against `git archive fos deploy/openshift` at
  `866da65`, which is newer than the `0b6a1e0` the fix report used. The tool was the real test functions with `ROOT`
  repointed (`scratchpad/run_manifest_tests.py`).
- `test_handoff_lists_every_secret_the_manifests_mount` **PASS**.
- `test_handoff_names_every_workload_that_mounts_each_secret` **PASS**.

**Mutation check of the round 1 B1 tests:**

- With `HANDOFF.md` as of `3dc5d73`, `test_secret_rows_name_every_role_that_logs_in_with_them` fails, and so does
  `test_api_keys_secret_is_required_at_boot_by_the_app_and_the_worker`.
- Against `fos` `866da65`, the workload test lists exactly the missing reader pairs:
  - `dps-pg-app <- dps-worker` and `dps-api-keys <- dps-worker`;
  - four browser-worker rows;
  - `dps-pg-owner <- dps-migrate-maintain`.
- With the current HANDOFF, all three pass. The tests are not vacuous.

## 2. Round 1 blocking findings, re-checked

### B1: HANDOFF §5 and §3.3 against the code (fixed)

- **The code.** Each fact below was read in the code:
  - `worker.main()` runs `assert_production_posture` before `asyncio.run(_amain)`.
  - `_amain` opens `db.init_pool` (the API login), then `db.init_worker_pool`.
  - `rekey.amain` opens both pools.
  - `keys_cli` uses the API pool.
  - Only `app` and `worker` call the guard. `migrate`, `keys_cli` and `rekey` do not.
  - `posture_problems` refuses an empty `DPS_API_KEYS`. A file holding `[]` passes.
- **§5 rows.** They now name every reader:
  - `dps-pg-app`: app, worker, the browser-worker (at the M4 merge), `dps.keys_cli` and `dps.rekey`.
  - `dps-pg-worker`: adds `dps.rekey`.
  - `dps-job-kek`: adds the browser-worker and `dps.rekey`.
  - `dps-api-keys`: **required at boot** for app and worker, and may hold `[]`.
  - `dps-s3`: adds the browser-worker.
  - `dps-pg-owner`: adds the Job and the maintain CronJob.
- **§3.3.** It now says that the worker runs the same guard before it opens the database, and that the
  browser-worker runs it at the M4 merge without the API rules. Branch `m4`'s `posture.ROLES` and `serves` confirm
  the browser-worker part.

### B2: Policies quick switch (fixed)

**Code read.** `policyLoad.ts` and `Policies.tsx` (`7bb202c`) do the following:

- `onScreen` passes only a detail or preset body whose `name` equals the route.
- A switch clears the page synchronously before the first await. A same-policy reload with the same `auth` object
  keeps it (`loadedFor`).
- The pending state shows only "Loading <name>…".
- Every write names its policy (`editTarget`, `saveEdit`, and publish, discard and adopt by name). A write's answer
  and its follow-up apply only while `stillShown(policy)` holds.
- A route change closes an open dialog. A key or tenant change counts as a switch and resets `readOnly`.

**Re-runs of the fix report's three probes.** These ran on my own fixture-mode Vite on 127.0.0.1:5197 with private
Playwright 1.55.0 (`~/.cache/dps-build/pw-fdocs`). All three gave the same results as the fix report
(`rerun_probe_*.out.json`):

| Probe | Result |
|---|---|
| Switch window | The header reads "pci-dss / Loading pci-dss…". There is no matrix, 0 editable methods, no Publish, Discard, adopt, declare or add-recognizer, and no write is sent. Afterwards pci-dss's own v1 shows, with 20 editable methods and no draft created. |
| Preset to tenant, 1440 and 390 px | The loading line shows and nothing of the preset appears. There is no horizontal scroll. |
| A save in flight, then a switch | The PUT goes to `/policies/gdpr-pseudonymise/draft` with a body named `gdpr-pseudonymise`, and pci-dss stays on screen. A publish dialog closes on back and stays closed on forward. |

**Three new adversarial probes** (`probe_r2_extra.py`, output in `probe_r2_extra.out.json`):

- **S1.** B's GET answers 500. Inside the window B shows only "Loading"; then the error `500 internal_error: …`
  appears. Nothing of A is shown, no edit control appears and no write is sent.
- **S2.** A, then B (delayed 3 s), then quickly back to A. A is on screen with its draft (21 editable methods), and
  B's late answer never replaces it.
- **S3.** A publish of A is in flight (POST delayed 2.5 s) when the user goes back to B. The dialog closes, and B
  stays unchanged before and after A's answer. The POST went to `gdpr-pseudonymise/publish`; after going forward,
  A shows "v4 active".

There were 0 console errors and 0 page errors.

**Tests first.** `S7-fix-r1/red-frontend.log` shows the nine new tests failing before the fix: 5 in `policyLoad`
and 4 in `PoliciesView`.

## 3. Acceptance

| Item | Status |
|---|---|
| M5 plan Task 27: `tools/settings_table.py` | Renders between the markers from `Settings.model_fields`: `DPS_<NAME>`, the default, `—` for secrets, `None` and empty values, `secret (`_FILE`)` for `SECRET_ENV` plus `DPS_BOOTSTRAP_API_KEY`, the milestone and `FIELD_DOCS`. `--check` exits 1 when the table is stale or an entry is missing or extra. **OK** |
| Settings missing from the table | **None.** All 165 fields are present. No field has an alias or a `default_factory`, so every variable name is `DPS_<NAME>`. Every variable the docs name exists in the code, except the M4 ones, which are marked. Every snake_case code the docs name exists in the code, except `host_forbidden`, which is marked "at the security stream's merge". The integrator's 18 M4 `FIELD_DOCS` lines match branch `m4`'s `Settings` exactly (18 of 18). |
| HANDOFF in its 13 final sections, "Last verified" as the first line | **OK.** About 120 facts were re-read against the code, for example: the Dockerfile, compose, posture, worker, rekey and keys_cli, retention, roll-up freshness, the metrics and their labels, the readiness components, the CORS and security headers, the third-party log caps, the COIN mint trust chain, the key store failing closed after ten refreshes, the DB retry every 10 s, the integer pseudonym bands and `_SQL_STRING_*`. Two wording slips are N2 and N4. |
| `docs/technical/api-m5.md` | Routes, scopes, status codes, field ranges (keys, tenants, audit, policy names, ticket and note), report and evidence field names, and error codes were all checked against `admin.py`, `reports.py`, `policy_store.py`, `tenants.py`, `auth_db.py`, `policy_store` (core), `tabular.py` and `metrics.py`. **Accurate**, except one omission (N5). |
| `CHANGELOG.md` draft | One paragraph per milestone. The exit evidence traces to the reports: 588, 15/15, 1009/131/25/84, 1665/139/170/34, 3 004, 942/860, 2363/232/45/21 pages, and 0.970 with NNDR 1.50. "Known limitations" is present. **OK** |
| README | The links resolve and the run and develop blocks are correct. Items still to come are marked †. **OK** (N3 concerns the HANDOFF example, not the README). |
| `tests/test_handoff.py` | The plan's four tests plus five: three for the tool and two for B1. 8 passed and 2 skipped by design; both skipped tests pass against `fos`. **OK** |
| Programme plan §2.6 F5 ("`tests/test_handoff.py` green") | **Met** |
| Programme plan §2.6 F4 ("listed items closed or explicitly deferred in CHANGELOG Known limitations") | **Met.** M1-N/N4 is closed: the detail, editing and writes are guarded. N1 below is a residual about the error banner only. A-N1 and A-N2 are closed (`git ls-files -ci --exclude-standard` is empty, and `dps/layers/reports` is not ignored). The review nits are fixed test-first, and the rest are in "Known limitations". |
| Python code changes | They are bounded and tested: version bounds giving 422; a future-day roll-up giving 404 before any refresh; PDF list capping, where "…" folds to "..." in Latin-1 and every list cell is capped; the smoke cursor; the bench defaults; demo_m2's lost group; `tools/shots.py`. |
| Git hygiene | All 23 commits are authored and committed by `NilenduGanguli <nilendu.ganguli@gmail.com>`. There is no trailer, the messages are conventional and the fix commits touch only their own files. `git diff --check` is clean, and a pattern scan of the diff found no key or secret. The working tree is clean. |

## 4. Worktree and lane

- Branch `fdocs`. `info/exclude` holds `lane.sh .env .env.lane .venv/ .lane-export/`.
- The lane resolves to lane-local endpoints. I printed only host and port:

  | Service | Port |
  |---|---|
  | PG | 5523 |
  | Source PG | 5524 |
  | MinIO | 9110 |
  | App | 8422 |
  | OCR | 5106 |
  | Oracle | 1530 |
  | Mongo | 27026 |
  | Demo site | 8432 |
  | Tokenizer mock | 8442 |

- My `docker compose up -d dps-db` bound 127.0.0.1:5523 under project `dps-wt-fdocs`.
- `frontend/node_modules` is a real directory.
- `.env*` files cannot be read by agents (permission denied), so I checked them through their effects only.

## 5. Non-blocking findings (fix when convenient)

### N1. Policies: the late failure of a write made on the policy the user left shows on the next policy's page

- **Where.** `Policies.tsx` `write()` calls `fail(e)`, and so `setError(...)`, without the `stillShown(policy)`
  guard that the success path has.
- **Probe.** `probe_r2_late_error.py`, output in `probe_r2_late_error.out.json`, screenshot `r2-late-error.png`.
  1. Make gdpr's draft PUT answer 409 after 2.5 s.
  2. Save one matrix edit on gdpr, then switch to pci-dss.
  3. pci-dss's page then carries gdpr's error banner `409 …`. It does not go away when pci-dss's own detail loads,
     because the error is cleared only when a load starts.
- **Why it matters.** The server's real messages do not name the policy, for example `400 invalid_policy: …` or
  `409 no_draft: …`, so the user reads the error as pci-dss's.
- **Impact.** No data harm: the write went to the right policy and pci-dss's detail is correct.
- **Fix.**
  - Guard the error path the same way: `if (stillShown(policy)) fail(e)`. A 401 or 403 may keep setting `readOnly`,
    which is per key.
  - Add a test: a write on A rejects late after a switch to B, and B shows no error.

### N2. Two settings descriptions say more than the code does

- **Pool sizes.** `DPS_PG_POOL_MIN` and `DPS_PG_POOL_MAX` say "Minimum (Maximum) connections per pool".
  - Only the API pool reads them (`dps/db.py:144-145`).
  - The worker pool is sized `min_size=1, max_size=max(4, 2 × DPS_WORKER_CONCURRENCY + 4)`, which is 12 by default
    (`dps/db.py:169-170`).
  - Every worker pod opens both pools.
  - An operator who sizes the platform database's connections from the table, or caps the workers' connections
    with `DPS_PG_POOL_MAX`, gets numbers that do not match.
  - Suggested wording: "connections of the API pool (`dps_app`); the worker pool holds 1 to max(4, 2 ×
    `DPS_WORKER_CONCURRENCY` + 4)".
- **Egress TLS.** `DPS_EGRESS_TLS_REQUIRED` says "every egress URL `https`".
  - The flag itself enforces only the connections (`netguard.require_tls_connection`) and the tokenizer URL
    (`build_tokenizer`).
  - The https rule for the Stellar, COIN, OCR and S3 URLs is the staging/prod posture's, whatever the flag says.

### N3. HANDOFF §4's lane-safe example clashes on MongoDB's port

- **The problem.** The example passes `--profile docs --profile sources` but sets no `DPS_MONGO_PORT`. Since M3,
  `sources` includes `mongo`.
- **Proof.** `docker compose … config` with exactly the example's variables publishes `mongo` on 127.0.0.1:**27018**.
  That is the default stack's port, held on this host by `data-privacy-services-mongo-1`, so the example's `up`
  fails beside a default stack.
- **Fix.** Add e.g. `DPS_MONGO_PORT=27019`. The example's `DPS_TOKENIZER_MOCK_PORT` is unused there (no `tokenizer`
  or `full` profile) but harmless.
- **Source.** The defect was already in the old HANDOFF and was carried over.

### N4. HANDOFF §8 names the wrong default trust store

- **What it says.** "TLS verified with `DPS_TOKENIZER_CA_FILE` (else `DPS_SSL_CERT_FILE`, else the system CAs)".
- **What happens.** With neither setting, `HttpTokenizer` passes `verify=True`. httpx 0.28.1 then uses
  `SSL_CERT_FILE` or `SSL_CERT_DIR` from the environment, else certifi's bundle. It does not use the OS trust store.
  The `DPS_COIN_CA_FILE` row states this correctly.
- **Reach.** Outside staging/prod only, because the posture requires `DPS_SSL_CERT_FILE` whenever an egress URL is
  set.

### N5. api-m5.md lists `synthesize_pseudonym_unsupported` only as a prepare/job error

- **What the code does.** `targets._check_identifiers` refuses the run with **422 at submit**, through
  `validate_run`. Both `submit_run` (`POST /jobs/run`) and `POST /jobs/run/validate` call it.
- **Fix.**
  - Add the code to the 422 list of the `/jobs/run` row.
  - In the tabular table, write "422 at submit, or at prepare".

### N6. HANDOFF §12's DoD 9 grep is not "nothing" today

- **The command.** `git ls-files | xargs grep -nE "AKIA[0-9A-Z]{16}|BEGIN (RSA |EC )?PRIVATE KEY"   # nothing`.
- **What it finds.** Four hits of AWS's documented example key `AKIA…(AWS documentation example, redacted)`:
  - `tests/test_patterns.py`;
  - `tests/test_surrogates.py`;
  - the M0 plan, twice.
- **Source.** These are on `main` and the command comes from the plan's DoD, so S7 did not cause this. The release
  step, which runs every §12 gate, will trip on it.
- **What `fsec` does.** Its `tests/security/test_secrets_hygiene.py` scans only `dps tools deploy docker
  frontend/src`.
- **Fix.** Scope the §12 line the same way, or exclude the example key, at the fsec merge or the release.

### N7. Trivial omissions and marks

- The tool-variable paragraph lacks `DPS_TEST_API_KEY`, the console tests' key override (`tests/console/*`).
- The §5 `dps-connector-refs` row names the browser-worker without "(at the M4 merge)".
- The §6 metadata-database row does the same.

## 6. Notes for the integrator (informational; these are not defects of this branch)

### I1. `main` moved after fdocs' base

- `main` is now at `5c11e49` (M3 post-merge r1). Its HANDOFF adds four facts that fdocs' rewritten HANDOFF does not
  have yet:
  - MongoDB `files` exports in every format. fdocs' §11 still says "`files` targets are not yet tested for MongoDB
    documents".
  - **The production write gate.** Every write into a `production`-tagged connection needs `admin`: 403
    `admin_scope_required` from `/jobs/run` and `/jobs/run/validate`, and retry re-checks it.
  - Free-text name spans.
  - Key-less Oracle synthesize.
- When `main` and `fdocs` meet, move these into §5 (beside SEC-3) and §11, and into the CHANGELOG's M3 paragraph.
- The integrator note in `M5-DEVIATIONS` lists the other streams but not `main`'s own later commits.

### I2. `fsec` adds `DPS_TOKENIZER_PROFILES`

- The posture requires it with a tokenizer in staging/prod.
- `test_every_setting_is_documented` will name it at the merge.
- It needs a `FIELD_DOCS` line and a §3.3 bullet.

### I3. `fos` (`866da65`) changed since the plan

- It now gives each Deployment an init container that runs `python -m dps.migrate --verify` (a schema wait).
- It adds an eighth alert, `DpsAuditMaintenanceStale`.
- It has a dev-mocks Secret, `dps-tokenizer-mock`.
- §7 and §10 should take these at the fos merge. The two manifest tests already pass against it.

## 7. Housekeeping

- **Compose.** I started only the lane's `dps-db` (project `dps-wt-fdocs`) for the gates, then ran `docker compose
  down` without `-v`. No other compose project was touched.
- **Vite.** My fixture-mode Vite on 127.0.0.1:5197 is stopped.
- **Scratch files.** The fos manifest extract, the mutation and manifest runners and `field_docs.txt` are in the
  session scratchpad.
- **Repository.** No file was modified, and `git status` is clean.
- **Evidence** in `~/.cache/dps-build/reports/S7-review-r2/`:
  - `pytest-full.log`, `lint.log`, `frontend-test.log`, `frontend-build.log` and `vite.log`;
  - `rerun_probe_*.{py,out.json}`;
  - `probe_r2_extra.{py,out.json}` and `probe_r2_late_error.{py,out.json}`;
  - screenshots `r2-s1-error.png`, `r2-s3-after.png`, `r2-late-error.png`, `policy-switch-*.png` and
    `preset-to-tenant-window-*.png`.
