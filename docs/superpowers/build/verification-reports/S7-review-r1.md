# S7 review, round 1: finish-stage docs (M5 Task 27) and F4 polish, branch `fdocs`

- **Reviewed:** `/Users/neelu/document_intelligence/dps-wt-fdocs`, branch `fdocs` at `3dc5d73` (18 commits on `64c46e3`;
  `origin/fdocs` = `3dc5d73`). Implementer report: `S7-impl.md`.
- **Verdict: FAIL, with 2 blocking findings.** Both fixes are small. B1 is a contradiction between the docs and the
  code in HANDOFF §5. B2 is the Policies quick-switch item (M1-N / console review N4), which is only half closed: the
  stale detail stays on screen and can be edited, and an edit writes the previous policy's body into the new
  policy's draft. I reproduced B2 in a browser.
- All gates are green. The settings table is complete and correct, and api-m5.md matches the routes as built.
- I modified no repository file. Probes and logs are in `~/.cache/dps-build/reports/S7-review-r1/`.

## 1. Gates (run by me)

| Gate | Result |
|---|---|
| `./lane.sh ruff format --check dps tests tools` · `ruff check dps tests tools` | 385 files already formatted · all checks passed |
| `./lane.sh mypy dps` (+ `mypy tools/settings_table.py tests/test_handoff.py`) | no issues in 134 source files (+ 2) |
| `./lane.sh python tools/settings_table.py --check` | `settings table current (165 settings)`, exit 0 |
| `./lane.sh pytest -q -rs` (lane `dps-db` up on 5523, nothing else running) | **2382 passed, 266 skipped**, 1 warning (starlette/httpx deprecation), 100 s. The skips are services that were down (oracle 92, source-pg 91, mongo 60, app 9, minio 3, OCR mock 3), host tesseract 2, `mostlyai` and `pandas` 3, a spaCy model 2, and `deploy/openshift/base` absent 1. This matches the implementer's numbers exactly. |
| `tests/test_handoff.py` + S7's new tests (shots, demo group, policy bounds, evidence PDF, future day, bench, smoke cursor) | 22 passed, 1 skipped (manifests test, by design) |
| `cd frontend && npm test` | node `--test` 42/42 + Vitest **251/251** (20 files) |
| `cd frontend && npm run build` | `tsc --noEmit` + vite build OK (only the existing >500 kB chunk warning) |

Log files: `pytest-full.log`, `lint.log`, `frontend-test.log` and `frontend-build.log` in the probe folder.

## 2. Worktree setup (verified without reading `.env*`, whose reads are permission-denied)

- The worktree is on branch `fdocs`. `info/exclude` holds `lane.sh .env .env.lane .venv/ .lane-export/`, and
  `lane.sh` is identical to m3's.
- Compose ports and tags come out as lane-local in `docker compose --profile full config` (I printed only ports
  and tags): app 8422, dps-db 5523, source-pg 5524, MinIO 9110/9111, Oracle 1530, Mongo 27026, OCR 5106, tokenizer
  mock 8442. Every built image is tagged `fdocs` and the project is named `dps-wt-fdocs`.
- The test endpoints resolve to the lane (I printed only host and port from the fixtures):
  - PG 5523, source-pg 5524, MinIO 9110, app 8422, OCR 5106, demo site 8432.
  - Oracle 1530 and Mongo 27026.
  - main's running stack (5441, …) was not used.
- The venv runs Python 3.12.13 and imports every extra except `synth-ml`, as intended.
- `frontend/node_modules` is a real directory (from `npm ci`), not a symlink.
- The implementer left compose app settings (KEK, LLM provider, …) at their defaults on purpose, because `Settings`
  reads `.env`. That is sound: compose interpolates no `DPS_PG_PORT` or `DPS_EXPORT_ROOT` on this branch.

## 3. Acceptance

| Item | Status |
|---|---|
| `tools/settings_table.py` | Renders between the markers from `Settings.model_fields`. Secrets (`SECRET_ENV` plus `DPS_BOOTSTRAP_API_KEY`) show `—` and `secret (_FILE)`. Every field has `FIELD_DOCS`. `--check` exits 1 when the table is stale or an entry is missing or extra. **OK** |
| Settings missing from the table | **None.** 165/165, enforced by the test. Settings descriptions spot-checked against the code: about 60 claims (posture rules, error codes, ranges, fallbacks, KEK uses, retry formula, 10 000 px page limit, `coinscope`, `/readyz coin_tls`, …), all correct. |
| `tests/test_handoff.py` | The plan's four tests plus three for the tool. The manifests test skips until `deploy/openshift/base` exists. **Simulated against branch `fos`:** its base mounts `dps-api-keys`, `dps-coin`, `dps-connector-refs`, `dps-job-kek`, `dps-ocr`, `dps-pg-app`, `dps-pg-owner`, `dps-pg-worker`, `dps-s3` and `dps-tokenizer`, and all of them are named in HANDOFF, so the test will pass after the merge. Compose services of `m4`, `fimg`, `fsec`, `fos` and `fdemo` are all named in HANDOFF. |
| `docs/technical/api-m5.md` | Routes, scopes, status codes, field ranges and error codes checked against `admin.py`, `reports.py`, `policy_store.py`, `tenants.py`, `auth_db.py` and `policy_store` (core). **Accurate.** `host_forbidden` is correctly absent (it is in `fsec` only). |
| `CHANGELOG.md` draft | One paragraph per milestone (M0–M5). The exit evidence traces to the reports: 588/15/15; 1009, 131 and 84/84; 1665, 139/170 and 34/34; 3 004 rows/s; 942/860 rows/s; M4 2363/232/45/45/21 pages; M5 0.970 / NNDR 1.50. "Known limitations" is there. **OK** (B2 is missing from it). |
| README run/develop blocks | Correct against compose, vite and pyproject. Items from unmerged streams are marked †. **OK** |
| HANDOFF in its 13 final sections | Structure OK, and "Last verified" is the first line. Facts checked against the Dockerfile, compose, posture, worker, rekey, retention, reports and metrics: correct **except §5 (B1)**. |
| Programme plan §2.6 F5 ("`tests/test_handoff.py` green") | **Met** (green; one skip by design) |
| Programme plan §2.6 F4 ("listed items closed or explicitly deferred in CHANGELOG Known limitations") | **Not met for M1-N / N4 (B2).** A-N1 and A-N2 are closed, and so are the review nits fixed in §3 of the implementer report (each has a meaningful test). |
| Git hygiene | Every commit is by NilenduGanguli <nilendu.ganguli@gmail.com> with no trailer and a conventional message. The working tree is clean. A diff scan for key or secret patterns found nothing. |

## 4. Blocking findings

### B1. HANDOFF §5 contradicts the code: the worker needs `dps-pg-app` and `dps-api-keys`, and API keys are not optional

`HANDOFF.md:470` lists `dps-pg-app` → `DPS_PG_PASSWORD_FILE` as read by `dps-app` only. `HANDOFF.md:474` lists
`dps-api-keys` → `DPS_API_KEYS_FILE` "(static keys; optional once DB keys exist)" as read by `app`. The code says
otherwise:

- `dps/worker.py:582`: `main()` runs `assert_production_posture(settings)`. Under `DPS_ENV=staging|prod`,
  `dps/posture.py` `posture_problems()` requires a non-empty `DPS_API_KEYS`, with no exception for DB keys:
  "DPS_API_KEYS (or DPS_API_KEYS_FILE) must be set". The app does the same at `dps/app.py:484`. The secret is
  therefore **required at boot for both the app and the worker**, even when every key is DB-backed (the file can
  hold `[]`).
- `dps/worker.py:524`: `_amain()` opens the API pool (`db.init_pool(settings)`, login `dps_app`, password
  `DPS_PG_PASSWORD`) before the worker pool. **The worker needs `dps-pg-app` too.** `dps.rekey`
  (`dps/rekey.py:155-156`) opens both pools as well, which HANDOFF §9 does state.
- §3.3 of the same file says correctly that `DPS_API_KEYS` must be set, so §5 contradicts §3.3.
- Branch `fos` mounts `dps-api-keys` and `dps-pg-app` into the worker and the browser-worker. Its own comment says
  "the worker boots through the same posture guard as the app".
- The "optional" wording comes from the M5 plan's Task 25 sketch (`optional: true`). Here the code as built wins.

An operator who follows §5 deploys a staging/prod worker without these secrets. It then refuses to boot (posture)
or cannot log in to the metadata DB.

**Fix:** in §5, the `dps-pg-app` row is read by `dps-app`, `dps-worker`, `dps-browser-worker` (at the M4 merge)
and the operator's `dps.rekey` pod. The `dps-api-keys` row is "static keys; **required at boot in staging/prod by
the posture guard of the app and the worker** (it may hold `[]` once every key is DB-backed)", read by app, worker
and browser-worker. Also say in §3.3 that the worker runs the same boot guard.

### B2. Policies quick switch: the previous policy's detail stays on screen and editable, and an edit overwrites the new policy's draft

`frontend/src/lib/policyLoad.ts:40-50` changes nothing on screen for a tenant-policy load until `GET /policies/{name}`
answers. Only the preset branch clears the old body. During that wait the page
(`frontend/src/pages/Policies.tsx:57-59`) still renders the previous policy's `detail` under the new route name with
`editable = true`. `saveDraft` (`Policies.tsx:246`) then PUTs that body to `/policies/<new name>/draft`.

The tests in `policyLoad.test.ts` cover late answers only. They do not cover the in-flight window.

**Browser reproduction** (fixture mode, private Playwright 1.55.0, `probe_policy_switch.py`, output in
`probe_policy_switch.out.json`, screenshots `policy-switch-window.png` and `policy-switch-after.png`):

1. Adopt `pci-dss`, open `gdpr-pseudonymise`, delay `GET /api/v1/policies/pci-dss` by 4 s, click `pci-dss` in the
   list.
2. 600 ms later the URL and crumb read `pci-dss`, but the card shows gdpr's data: "v3 active", "draft v4 · 3
   changes", gdpr's hash `18d2493…`, "Based on … gdpr-pseudonymise", "14 jobs". 21 method buttons are enabled, and
   so are "Discard draft" and "Publish v4".
3. One "Save to draft" in that window sent `PUT /api/v1/policies/pci-dss/draft` with a body whose `name` is
   `gdpr-pseudonymise`.
4. Afterwards `pci-dss` holds "Draft v2 · **27 changes** not published", and its `treat` equals gdpr's.

A second probe (`probe_preset_to_tenant.py`) shows the read-only variant: preset `analytics-safe` → tenant
`gdpr-pseudonymise` displays analytics-safe's treatment under the gdpr name until the answer arrives.

This is exactly console review N4 ("a quick policy switch can show a stale detail") and the M1-N item S7 was asked
to close. It is neither closed nor listed in "Known limitations", so F4 acceptance is not met.

**Fix, test first:**

1. Never show or edit a detail whose name differs from the route name. Either clear `detail` and `presetBody`
   synchronously when a load starts for a different name than the one shown (keep them for a same-name reload, e.g.
   after publish), or guard at the page with `detail?.name === name`, applied to rendering, `editable` and every
   write action. Write actions must use the detail's own name.
2. Add a `loadPolicyDetail` or page test: tenant A → tenant B with B pending shows no A detail and allows no write.
   Add the preset → tenant case too.

## 5. Non-blocking findings (record or fix when convenient)

1. **HANDOFF §12 has no security gate.**
   - DoD 9 (`pytest -q tests/security tests/test_no_values_in_logs_m5.py`, both on `fsec`) is not listed, not even
     as "(at the fsec merge)".
   - `tests/test_no_values_in_logs_m4.py` (on `m4`) is missing from the log-scan line.
   - The integrator notes (`M5-DEVIATIONS` Task 27, impl report §5) do not mention these lines.
2. **api-m5.md has no note for `host_forbidden`.** The plan lists it, and `fsec` raises it (`dps/netguard.py`).
   Neither the doc nor the integrator notes say to add it at the fsec merge.
3. **The wizard offset check accepts fractions.** `offsetProblem` uses `parseCount`, which floors `"0.5"` to 0
   while still returning non-null. Step 4 accepts it and `pkStrategyOf` sends `offset: 0`, which the server refuses
   (`PkOffset.offset gt=0`, 422 at step 5): the same class as the "0"/"abc" bug. `"1.5"` silently becomes 1.
   Checked with node: `parseCount("0.5") → 0`. Suggested fix: require `/^\d[\d,\s_]*$/` before `parseCount`, and
   add `'0.5'` and `'1.5'` to the bad list in `offset.test.ts`.
4. **"Since" is off for three settings.** `DPS_COIN_CA_FILE`, `DPS_COIN_TLS_INSECURE` and
   `DPS_ALLOW_INSECURE_COIN_MINT` say M0. They first appear after `v0.1.0-m0` (SEC-1, inside `v0.2.0-m1`). The other
   162 match git history.
5. **The tool-variable paragraph** (`HANDOFF.md:273-289`) is missing `DPS_SMOKE_JOB_TIMEOUT` (smoke, default 180 s)
   and `DPS_TEST_REQUIRE_TESSERACT=1` (`tests/documents/test_ocr_tesseract.py`).
6. **Facts from the old HANDOFF are now documented nowhere.** This contradicts the "every current fact kept" claim.
   - `target.ledger_owner`: the write ledger in another schema (`jobs_models.py:183`).
   - The connection test's `no_sql_string_functions` advisory: give the source login no `dblink*` /
     `aws_s3.query_export_to_s3` …
   - The KEK generation command.
   - `DPS_COIN_CA_FILE`'s final fallback to certifi / `SSL_CERT_FILE` / `SSL_CERT_DIR`.
7. **The plan's §11 trap** "kustomize generator/component ordering and the copied dev mock" is not marked
   "(Task 25)".
8. **`tools/bench_connector.py` docstring:** the new defaults paragraph splits the usage block, so
   `[--source-dsn …] [--label …] [--out …]` now follows the prose. This is cosmetic.
9. **Implementer report wording:** "251 tests passed (node + Vitest)" is in fact 42 node + 251 Vitest.
10. **The `.env.example` limitation** is recorded correctly. Agents cannot write `.env*`; the owner must do it.

## 6. Housekeeping

- I started the lane's `dps-db` (project `dps-wt-fdocs`) for the db tests and ran `docker compose down` (no `-v`)
  afterwards, as the implementer left it. A fixture-mode Vite on 127.0.0.1:5196 was used for the probes and then
  stopped. No other compose project was touched.
- `git status` is clean. Only ignored build output was written (`frontend/dist`, caches).
