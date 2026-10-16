# S7 — finish-stage docs (M5 Task 27) and F4 polish — implementation report

- **Stream / worktree:** S7, `/Users/neelu/document_intelligence/dps-wt-fdocs`, branch `fdocs`, created from
  `main` `64c46e3` (M0, M1, M2, M3, M5 merged; M4 and the other finish streams not on the branch).
- **Head:** `3dc5d73` — 18 commits on top of `64c46e3`, all authored `NilenduGanguli <nilendu.ganguli@gmail.com>`,
  no trailer, explicit pathspecs. Local gitleaks scan of `64c46e3..fdocs` (12 commits at the time, the Docker image
  autopush uses): no leaks. Not pushed by me (autopush is the owner's script; see the end).
- **Verdict of my own gates:** green (below). Nothing left uncommitted; the lane's compose project is down.

## 1. Worktree setup

- `git worktree add …/dps-wt-fdocs -b fdocs main` (branch did not exist).
- Git-ignored files (patterns in the shared `info/exclude`: `lane.sh`, `.env`, `.env.lane`, `.venv/`; `.env*` were
  already ignored by `.gitignore`):
  - `.env`: `DPS_IMAGE_TAG=fdocs`, ports app 8422, db 5523, source-pg 5524, MinIO 9110/9111, Oracle 1530, Mongo 27026,
    OCR 5106, demo site 8432, tokenizer mock 8442, worker metrics 9422, browser-worker metrics 9423 (all free per
    `lsof`). The other variables compose interpolates are app settings with in-container defaults (KEK, FPE, LLM
    provider, tokenizer URL, …); I left them at their compose defaults on purpose, because `Settings` reads `.env`
    from the working directory and a `.env` carrying e.g. `DPS_JOB_KEK` would change what host-side tools and any
    test that builds `Settings()` without `_env_file=None` see.
  - `.env.lane`: the `DPS_TEST_*` endpoints of contract §12.2 for those ports (m3's format); `lane.sh` copied from m3.
- `uv venv --python 3.12 .venv`; `./lane.sh uv pip install -e ".[ner,fpe,vertex,docs,db,connectors,uitest,e2e,tabular,dev]"`
  (every extra the tests use; not `synth-ml`, which is never installed — its 2 tests skip as everywhere else).
- `cd frontend && npm ci`. Baseline before any change: ruff/mypy clean, `pytest -q` 2138 passed, 487 skipped (no
  services).
- Private Playwright for the browser check: `~/.cache/dps-build/pw-fdocs` (`playwright==1.55.0`).

## 2. M5 Task 27 — HANDOFF, settings table, README, api-m5, CHANGELOG (`4e7204c`, `954863c`, `3dc5d73`)

- `tools/settings_table.py` renders HANDOFF §3.2 between the markers from `Settings.model_fields` (165 settings):
  *Variable · Default · Secret · Since · What it does*; secrets (`posture.SECRET_ENV` + `DPS_BOOTSTRAP_API_KEY`)
  render `—` and `secret (_FILE)`; `FIELD_DOCS` documents every field (milestone + one line), each description
  checked against the code. `--check` exits 1 on a stale table, a missing `FIELD_DOCS` entry or a stale one, and
  names them. The plan's commit message is used.
- `tests/test_handoff.py`: the plan's four tests + three for the tool. **One skip by design:**
  `test_handoff_lists_every_secret_the_manifests_mount` skips while `deploy/openshift/base` is absent (Task 25 is on
  `fos`); after that merge it runs. HANDOFF §5 lists the plan's secret names (`dps-pg-app`, `dps-pg-worker`,
  `dps-pg-owner`, `dps-job-kek`, `dps-api-keys`, `dps-coin`, `dps-tokenizer`, `dps-ocr`, `dps-s3`,
  `dps-connector-refs`, ConfigMap `dps-ca-bundle`) so it passes if `fos` keeps them.
- `HANDOFF.md` rewritten to the final 13 sections (What ships · Build · Configuration (3.1 how read, 3.2 generated
  table, 3.3 production posture) · Local stack · Secrets · Services and dependencies · OpenShift · Tokenization
  contract · Runbooks · Probes/metrics/alerts · Traps · Verification gates · Release). "Last verified" is the first
  line after the title, for Task 28. Every current fact of the old 1,310-line file was kept (traps regrouped by
  area); milestone history and gate records moved to the CHANGELOG. Facts of unmerged streams are marked "(at the M4
  merge)" / "(Task 24/25/26)". Corrections found while checking against the code: `DPS_MIN_SCORE` *is* read
  (schema generator-spec samples), `DPS_DEFAULT_POLICY` takes a shipped preset, `verify` mode with pending
  migrations turns the job routes to 503, one-shot services have no healthcheck of their own, and a metadata DB lost
  **after** start is not re-probed (500 `internal_error`, `/readyz` keeps its startup value) — written into the
  runbook and the known limitations (not fixed: app lifecycle change).
- `docs/technical/api-m5.md`: admin, reports, evidence PDF, policy store, `@name` references, tabular options and
  refusals, the evidence `fidelity`/`privacy`/`utility`/`tabular` blocks, `external_tokenize`, the M5 error codes —
  as built. The plan's `host_forbidden` does not exist in the code (scanner SSRF code comes with Task 17 in `fsec`).
- `CHANGELOG.md` draft: `## v1.0.0 — unreleased`, one paragraph per milestone M0–M5 with exit evidence (M4's from
  branch `m4`'s lane verification r3, marked to confirm after its merge), HTML comments where the release fills in,
  and "Known limitations" (privacy/synthesis residuals, platform, security hardening left for the owner, console,
  UI scanner) gathered from every review/verification report.
- `README.md` final shape: status line, run block (`--profile full`, readyz poll, seed, smoke, demo seed/e2e †),
  console tour, develop block, links to api-m0…m5, HANDOFF, CHANGELOG, demo script and security review — "†" marks
  what arrives with the finish-stage merges.

## 3. Polish and review nits fixed (each test-first; the test failed before the fix)

| Item (source) | Commit · test |
|---|---|
| **M1-N / M5UI N4** Policies page showed a previous policy's late answer after a quick switch; `readOnly` never reset on a new key | `2df5da9` · `frontend/src/lib/policyLoad.test.ts` (`Latest` guard, `loadPolicyDetail`, live flags on the diff effects) |
| M5UI N3 refused draft save = unhandled rejection; R2-5 `declaredSince` and `Object.prototype` names | `2df5da9` · same file |
| M5UI N1, R2-1 must-stay-zero tiles said "placeholders only"/"no row equals" beside non-zero numbers | `7934d2a` · `frontend/src/components/zeroTiles.test.tsx` (rose `Kpi alert`, wording from the number) |
| M5UI N2 key custody said twice in the evidence pack | `423c716` · `frontend/src/pages/EvidencePack.custody.test.tsx` |
| H2 open issue 2 — wizard step 4 accepted `0`/`abc` as the append key offset | `28130d4` · `frontend/src/pages/wizard/offset.test.ts` (mirrors `PkOffset`); `b30d452` gives the MongoDB append readiness test a complete strategy |
| **A-N1** `demo_m2.py` stopped on a stale `var/demo/<group>.json` | `2cba6e8` · `tests/test_demo_m2_group.py` (stale file never overwritten — it holds the old key) |
| **A-N2** `console_check.py` and the M3 console tests rewrote committed screenshots | `9c1c8bb` + `dbd6d8a` · `tests/test_shots_dir.py` (`tools/shots.py`, `reports/console/<name>/`, `DPS_SHOTS_DIR`; `/reports/` anchored after I caught that an unanchored `reports/` hid `dps/layers/reports` — the test now asserts no tracked file is ignored) |
| M5 G4 N1 out-of-range policy version → 500 | `adff249` · `tests/test_policy_store_bounds.py` (422; db test, ran on the lane DB) |
| M5 G3 r2 N10 evidence PDF with very long lists → 500 | `4ac7d59` · `tests/test_evidence_pdf_long_lists.py` (20 entries + "… and N more") |
| M5 G3 r2 N2 (part) future-day roll-up left a marker that never ages out | `e2ad31f` · `tests/test_reports_future_day.py` (db test; the first version was vacuous under FORCE RLS — fixed to read as the tenant, then it failed 2≠0 before the fix) |
| M3 G6 N2 bench defaults for Oracle/MongoDB | `753e2e4` · `tests/test_bench_defaults.py` |
| M3 G6 N3 smoke sent `cursor=` | `dadeb7c` · `tests/test_smoke_cursor.py` |

Browser check (private Playwright, console in fixture mode, 1440/390 px): with the `pci-dss` answer delayed 1.5 s and
marked, a quick switch to `analytics-safe` shows no late answer on the fixed page and **does** show it on the page as
of `64c46e3` (the check catches the bug); Overview/Policies: 0 console errors, no horizontal scroll. Screenshots:
`~/.cache/dps-build/reports/S7-shots/`.

Not fixed (owner decisions, contract changes, security items owned by `fsec` — CSV guard, secret-env rules,
`SelectionError` context, key/tenant hardening — or larger than polish) → CHANGELOG "Known limitations".
`.env.example` stays unupdated: reading/writing `.env*` is denied to agents.

## 4. Gates (final, at `954863c`; README-only commit after)

| Gate | Result |
|---|---|
| `./lane.sh ruff format --check dps tests tools` · `ruff check` · `mypy dps` | 385 files formatted · all checks passed · no issues in 134 files |
| `./lane.sh python tools/settings_table.py --check` | settings table current (165 settings) |
| `./lane.sh pytest -q` (lane `dps-db` up, other services down) | **2382 passed, 266 skipped** — skips: services not running (oracle 89, source-pg 75+1, mongo 56, app 9, minio 3, OCR mock 3), `mostlyai`/`pandas` 3, spaCy 2, host tesseract 1, `deploy/openshift/base` absent 1 |
| `tests/test_handoff.py` | 6 passed, 1 skipped (the manifests test, by design until Task 25 merges) |
| `cd frontend && npm test && npm run build` | 251 tests passed (node + Vitest), build ok |

## 5. For the integrator / release (also in M5-DEVIATIONS "Task 27")

1. HANDOFF.md will conflict with every stream that edited it: keep this file's 13-section structure and move the
   merged facts in (M4 → §1, §3.3, §4, §5, §6, §9, §10, §11, §12; Task 24 → §1/§2/§4; Task 25 → §5/§7/§10; Task 26 →
   §4 and README "†"; fsec → README link, §3.3), drop the "(at the …)"/"†" marks that became true, then
   `python tools/settings_table.py`.
2. `tests/test_handoff.py::test_every_setting_is_documented` will name M4's 18 settings after the M4 merge: ready
   `FIELD_DOCS` lines are in `docs/superpowers/build/M5-DEVIATIONS.md` (Task 27 section).
3. CHANGELOG: set the date, fill the M5 finish-stage sentence, confirm the M4 paragraph after its post-merge
   verification, drop known limitations the merges closed.
4. `reports/` is now git-ignored (anchored `/reports/`); Task 24 planned the same line — keep one.

## 6. Housekeeping

- Lane compose project `dps-wt-fdocs`: only `dps-db` was started; `docker compose down` done. No other project touched.
- Autopush: `tools/dev/autopush.sh` run from the main checkout — `pushed fdocs 3dc5d73` (author, trailer and
  gitleaks checks passed); `origin/fdocs` = `3dc5d73`.
