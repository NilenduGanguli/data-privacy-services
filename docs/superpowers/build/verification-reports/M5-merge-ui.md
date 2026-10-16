# M5 — merge of the console sub-lane (m5ui) into m5, wired to the API as built

- Worktree `/Users/neelu/document_intelligence/dps-wt-m5`, branch `m5`.
- Author NilenduGanguli <nilendu.ganguli@gmail.com>. No trailer on either commit. Explicit pathspecs. No push. No tags.
- Resume check: the tree was clean at `791504a`, `m5ui` was not merged, and no earlier report existed, so I started from scratch.

## Commits

| Commit | What |
|---|---|
| `c238390` | `merge: M5 console`: `git merge --no-ff m5ui`. It merged cleanly with no conflicts: 53 frontend files plus `docs/superpowers/build/M5-DEVIATIONS-console.md`. |
| `5694280` | `fix(m5): wire the console to the API as built`: 23 files, frontend and backend. Every fix was written test-first. |

## How I wired and verified it

1. **Shapes.** I compared `frontend/src/lib/api.ts` and `types.ts` field by field with the backend models and routers:
   - models: `ApiKeyView`, `CreatedKey`, `TenantView`, `AuditRow`, `PostureReport`, `SummaryOut`, `ReportsJobsOut`, `EvidenceItem`, `ApiRollup`, the policy-store views, and the `fidelity`, `privacy`, `utility` and `tabular` blocks of the pack;
   - routers: `admin.py`, `reports.py` (including `/evidence/{id}/pdf`) and `policy_store.py`.

   I then probed every endpoint on the lane stack and printed key shapes only. The routes, scopes, query parameters and response shapes all matched. The only type drift was `WorkerRow.busy`, which the backend sends as an int, and the queue rows.
2. **Live data.** I ran the stack with `docker compose --profile sources`. It was seeded by `tools/smoke_test.py` (32 PASS, 2 skipped because OCR was not ready) and `tools/demo_m2.py` (13 jobs succeeded, 0 orphans). I also ran four copula synthesize jobs through the API.
3. **Browser walks.** These used a private Playwright 1.55.0 venv at `~/.cache/dps-build/pw-m5`. The scripts are in `~/.cache/dps-build/m5merge/`:
   - `sweep.py`: page loads; console errors, page errors and API responses ≥ 400; horizontal overflow; screenshots;
   - `admin_walk.py`;
   - `policies_walk.py`;
   - `reports_walk.py`.

   I ran them against the Vite dev server (proxying to `:8415`) while iterating, then against the production bundle served by the rebuilt app image on `:8415`.
4. **Mockups.** I compared the real-data screenshots with the mockup renders `~/.cache/dps-build/m5ui-shots/mock-{01,05,09,11}-1440.png`. The pages have the same sections, order and labels. Screenshots: `~/.cache/dps-build/m5merge/shots-r1/` (first walk) and `shots-final/` (36 loads).

## Mismatches found and fixed (all logged in M5-DEVIATIONS-console.md, section "Merge into m5")

### Console

| # | Mismatch | Fix |
|---|---|---|
| 1 | The rotation age was parsed from the posture check name ("…within 90 days"). The real name contains no day count, and the maximum age is configurable. | `rotationRule()` reads the key names out of the `keys_rotated` detail (`<name> is <n> days old`). Without that detail it falls back to the default of 90 days. |
| 2 | A key rotated with grace "now" is `disabled` + `rotated_at` on the backend (the fixture made it `expired`). The console offered Re-enable on it (→ 409 `key_rotated`) and Rotate on rotated or expired keys (→ 409 / 404). | `keyActions()` follows `auth_db.py`: active → rotate once and disable; rotated in its grace period → disable only; disabled → re-enable only if never rotated and not expired. `keyMeta` now reads "rotated, disabled …" and puts "rotated · stops …" before "new". |
| 3 | Key-name regex was `{2,62}` (3–63 characters); the backend's is `{1,62}`. Error words were mapped to codes the server never sends (`key_name_exists`, `invalid_scopes`). | Regex and words aligned with every `GrantError` and admin-router code. |
| 4 | Static keys with the `*` scope showed a bare `*` chip, "static…", and "none yet" under who may write. | `scopeChips` ("all scopes", privileged), "environment key", and `whoMayWrite` now honours `*`. |
| 5 | An invalid host got a FastAPI 422 with no `invalid_host` code, plus a console error. | `hostInputError()` pre-checks obvious slips; the server stays the judge. |
| 6 | Today's API roll-up was dated "14 Oct 00:05", which was in the future. Routes were rendered as `POST /api/v1/document.detect` and `artefact.read`. Counts read "1 calls". | Today's roll-up is dated now and marked "open until 00:05 UTC". `rollupRoute()` maps each action to its real route. Counts use `plural()`. |
| 7 | `job.finalize` audit outcomes `succeeded` and `dry_run` showed slate. | Now emerald. |
| 8 | Preset pages called `GET /policies/{name}` → 404 `policy_not_found`, which logged a console error. The plan preview sent a bare tenant policy name, which `/policies/plan` does not resolve. | The page asks the store only for tenant policies (`isTenantPolicy`). `planPolicy()` sends the shown body, then `@name`, then the preset name. The detail reload depends on a boolean, so a list reload no longer resets the version selection. |
| 9 | Fixtures (dev only) had drifted from the backend: posture names and boot flags, queue shape, key refusals. | Fixtures realigned with the backend. |

### Backend defects the walk exposed

| # | Defect | Fix |
|---|---|---|
| B1 | **`dps/reports.py`:** the roll-up read `progress.rows_written` and `progress.values_by_method` from the `dps_job` JSON. Real runs keep those counters on their partitions. After about 700K rows were written, Overview showed "Values protected 642", "Layer 2 · 0 rows" and no engine split. | Counters now come from the partitions when the job JSON has none. The engines query was fixed the same way. Overview now reads 1,638,029 values protected and "733K rows · PostgreSQL 100%". New test: `test_run_counters_come_from_the_partitions_as_the_worker_keeps_them`. |
| B2 | **`dps/audit_query.py`:** `OUTCOME_GROUPS["ok"]` lacked `succeeded`, `dry_run` and `replayed`, so the "OK" filter hid every finished job. | Added. New test: `test_audit_outcome_groups_cover_job_finalize_outcomes`. |
| B3 | **`dps/policy_store.py` `_job_counts`:** runs without `spec.policy`, which run their snapshot's policy, were not counted. Every preset showed "0 jobs" and Overview had no "policy pack" label. | They now count under `plan.policy_name`. New test: `test_job_counts_include_runs_on_the_snapshot_policy`. |

### Production bundle without fixtures

The fixture server is loaded only under `import.meta.env.DEV && VITE_DPS_FIXTURES === '1'`, via a dynamic import in `main.tsx`. I grepped the local `dist` and the image's `/app/frontend/dist` for `installFixtureServer`, `fixtureServer`, `retail-banking-apac`, `ledger-recon-batch` and `dps-fixture-server`: 0 files.

## Gates (final, on 5694280)

| Gate | Result |
|---|---|
| `./lane.sh ruff format dps tests tools` | 301 files unchanged; `--check` reports all formatted |
| `./lane.sh ruff check dps tests tools` | All checks passed |
| `./lane.sh mypy dps` | Success, 126 files |
| `./lane.sh pytest -q` | **1905 passed, 9 skipped**, 241 s. This includes the DB tests of the touched tasks against the lane's dps-db: `test_reports` 11, `test_admin_api` 11, `test_policy_store` 12, all passed. |
| `npm ci` | ok |
| `npm test` | node --test 42/42; Vitest **190/190**, 14 files (183 at the merge) |
| `npm run build` | ok; only Vite's >500 kB chunk advisory |

The 9 skips are the docs-profile OCR mock (3), the tesseract binary not on the host (2), `mostlyai` not installed (2), and the `[ner]` extra or spaCy model (2). None of these is in code this commit touched.

## Browser results against the real API (production bundle, :8415)

- **Sweep.** Pages: `/`, `/reports`, two evidence packs (copula synthesize, append synthesize), `/admin`, `/policies/dpdp-pseudonymise`, `/policies/gdpr-pseudonymise`, `/data-sources`, `/playground`. Each was loaded at 1440 and 390 px, dark and light: **36 loads, 0 console errors, 0 API responses ≥ 400, 0 px horizontal overflow**. The API key never appeared in page text.
- **Admin walk.**
  - Create key: the key is shown once and has the right shape. After "I have stored the key" it is gone from the DOM, local storage, session storage and the URL.
  - Rotate with 24 h grace leaves two rows: the successor (Rotate, Disable) and the old key (Disable only).
  - Rotate with grace "now": the old key shows "rotated, disabled", with no action.
  - Disable then re-enable works.
  - A one-letter name is refused inline.
  - Adding a tenant and editing its quota work.
  - A host is added and normalised by the server (`https://payments-uat.bank.internal`); an invalid host is refused inline; the host is removed.
  - 0 console errors, 0 failed responses.
- **Policies walk** (dpdp-pseudonymise, then pci-dss on the production bundle):
  - adopt → v1;
  - live recognizer test "1 match · 1 on the allow list";
  - the recognizer and a declared field go into the draft ("Draft v2 · 16 changes");
  - plan: 12 findings;
  - publish v2 with a ticket;
  - v1 → v2 diff shows 2 added items, rendered as mockup 05;
  - `…-v2.yaml` download contains the recognizer.
  - 0 console errors, 0 failed responses.
- **Reports walk.**
  - KPIs come from `/reports/jobs`.
  - CSV export: `dps-audit-…csv`, about 30 KB.
  - Roll-up JSON and PDF, and pack JSON and PDF: each file starts `%PDF-` or is JSON, with names from `Content-Disposition`.
  - Filters are held in the URL (`?layer=conn&outcome=ok`).
  - The "OK" audit filter now returns `ok` and `succeeded` rows.
  - The failed-pack filter works.
  - Scope "All tenants I can read" works; "Load 50 older rows" goes 50 → 100.
  - Searching for the ticket returns 2 packs.
  - Evidence page: JSON and PDF downloads work and all 8 sections render.
  - Overview: "policy pack gdpr-pseudonymise v1"; Layer 2 "733K rows · PostgreSQL 100%".
  - 0 console errors, 0 failed responses.
- **Privacy checks.**
  - Log scan: 10 raw keys created during the walks; **0 found** in the app and worker logs (about 150 KB scanned). The bootstrap literal does not appear in the logs either.
  - 0 audit rows contain a key-shaped string.
  - The raw keys were held only in a 0600 scratch file for the scan and were never printed.

## Deviations

They are all in `docs/superpowers/build/M5-DEVIATIONS-console.md` → "Merge into m5 — the console wired to the API as built". The commit also fixes three backend defects (B1–B3) in this lane's own M5 code. Each has a failing test written first. I fixed them here because each one made a page show wrong figures against the real API.

## Open issues

1. **Copula synthesize of `bank.customers` dies in prepare.** `options.tabular: "copula"` on `bank.customers` ends `dead` with `internal_error`. The worker logs `prepare_failed error=ValueError`. It fails the same way with a `table` target and a `files` target: jobs `j_01M4TZ8G8RXHA4CAY0350AJT6D` and `j_01M4TZDG5761VSYZTJ13BY4X6S`. The same option on `bank.accounts` succeeds (fidelity 0.95 and 0.98, 0 exact rows). This is backend (T6/D-A synth on a table with many personal columns) and was not investigated here. It needs a repro with the traceback.
2. **The copula `bank.accounts` pack reads "review".** Its post-write sample has "513 findings in kept columns · 225 in transformed columns" and `review_required: true`. This is the D-A kept-column rule working as designed. A reviewer should decide whether the IFSC-like kept column should be classified rather than kept.
3. **Integration item, unchanged from M5UI fix-r1.**
   - Switch `verificationChip` to main's `finishedOutcome`/`verificationTone` when merging onto main; those helpers are not on m5.
   - Scan packs need `summary.verification_passed` (M4).
4. **`layer_case` puts `artefact.read` and `external.detokenize` in the API layer**, so they appear as API roll-ups. The console now names their real routes. Moving `artefact.%` to `conn` is a backend choice for WP-I.
5. **Bundle size.** It is still above Vite's 500 kB advisory (about 520 kB); code-splitting is optional.
6. **The UI-scanner host → storage-state convention** (`host:<host>` tag) is still M4's to confirm.

## Environment left behind

- `docker compose --profile sources down` was run. No `dps-wt-m5` containers are left; the volumes are kept.
- The Vite dev server on :5186 is stopped.
- The demo group key file is in `var/demo/` (git-ignored).
