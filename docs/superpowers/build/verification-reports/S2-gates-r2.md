# S2 — gates on branch `m4` after main was merged into it (GATES verifier, round 2)

**Verdict: PASS. Nothing blocks.** Every gate is green on `m4` @ `a3cd667`, with one exception. The first run of
the M2 marker gate failed 1 of 408 tests. That test is main's own M3 test
(`test_oracle_pipeline.py::test_a_query_subset_creates_and_writes_only_the_projected_columns`). M4 does not touch it.
Its pass/fail check fails at random, about 1 run in 870, and main has already fixed that check in `d2fdc3f`.
A probe on m4's code reproduced the exact failing value: no row keeps its own value, and the job still passes its own
strict verification. 100 instrumented reruns found 0 survivors. A full rerun of the M2 gate passed 408/408.
`m4` does not modify that test file, so the merge into main picks up main's fixed version on its own (see N1).
Main also has 2 commits that `m4` does not have; the post-merge gates on main must cover them (N2).

| | |
|---|---|
| Date | 2026-10-15, 00:01–01:15 IST |
| Tree verified | `dps-wt-m4`, branch `m4` @ `a3cd667` (= `origin/m4`). Clean at the start and at the end; no merge in progress. It contains main up to `144cc65` plus all of M4 and the S2 fix round 1. |
| Python / tools | lane `.venv`: Python 3.12.13, ruff 0.17.0, mypy 2.4.0, pytest 9.1.1, playwright 1.55.0, oracledb 26.0.0, pymongo 4.18.3. Node 22.20.0 / npm 10.9.3. |
| Endpoints | `./lane.sh` (`.env.lane`): app 8414, dps-db 5463, source-pg 5464, MinIO 9050/9051, Oracle 1524, Mongo 27020, OCR 5046, demo site 8420, tokenizer-mock 8424 |
| Logs, scripts, probes | `~/.cache/dps-build/s2-gates-r2/` (`logs/` numbered in run order, `run_gates.sh`, `post.sh`, `svt.py`, `log_scan.py`, `tess.sh`, `scratch-tree/` probe) |
| Files modified | **None in the repo.** As in earlier rounds (A-N2), the console tests rewrote the 54 tracked PNGs in `docs/superpowers/build/m3-shots/` (00:44–00:45). I copied them to `s2-gates-r2/m3-shots-from-run/` and restored them to `HEAD` by explicit pathspec (list in `restored-shots.txt`). Afterwards `git status` was empty, HEAD was still `a3cd667`, and the set of git-ignored files had not changed. `.env` and `.env.lane` were not touched. |

## 1. Stack (left running for the lens verifiers)

```bash
cd /Users/neelu/document_intelligence/dps-wt-m4
docker compose --profile docs --profile sources --profile oracle --profile scanner --profile tokenizer up --build -d
```

- **Docker:** 29.5.3, 16 CPUs, 35 GiB.
- **Compose config:** `config -q` printed no unset-variable warning for these profiles or for `--profile full`.
  - Every published port comes from the lane `.env` and is bound to 127.0.0.1: app 8414, OCR 5046, demo site 8420, dps-db 5463, MinIO 9050/9051, Mongo 27020, Oracle 1524, source-pg 5464, tokenizer-mock 8424.
  - The merged compose needs no other port variable (no worker metrics port, `DPS_PG_PORT` or `DPS_EXPORT_ROOT`), so I added nothing.
- **Build:** every layer was cached (11 s). The image IDs changed but their creation times did not. Compose recreated app, worker, browser-worker, azure-ocr-mock and tokenizer-mock.
- **Readiness:** I polled `/readyz` and never used `--wait`. It reported `ready: true` on the first poll:
  - api-core up; llm-gateway stub, `coin_tls=verified`; ner patterns-only; fpe ff1;
  - tokenizer up (http); ocr up (azure-read-v3.2); **browser 1 online**;
  - db up, migrations verified; jobs ready; keys db+static.
  - At the end it still reported ready with the same components.
- **The images run the verified code:**
  - In `app` and `worker`, all 194 files under `dps/`, `tools/` and `pyproject.toml` have the same sha256 as the tree.
  - In `browser-worker`, all 176 `dps/` files and `pyproject.toml` are identical. It has no `tools/`, by design.
  - The console bundle the app serves (`index-rKicyhlO.js`, `index-Bpk1daTG.css`) has the same content hash as my host build.
- **M4 DoD 3:** `browser-worker` has playwright **1.55.0** and Chromium 1187 under `/ms-playwright`, and runs as uid 10001 (non-root).
- **Migrations** in `dps.dps_migration_ledger`: 001–010, 020, 030–031 and 040–045 (19 files), each applied once.
- **End state (01:10):**
  - All 11 long-running services are healthy, with 0 restarts and no OOM kill.
  - `migrate`, `minio-init`, `demo-secrets` and `seed-sources` exited 0.
  - Highest memory use: Oracle at 84 % of its limit.
- **Demo seeds:** `tools/seed_sources.py --target all --rows 20000 --seed 7`, run before the gates and twice after them.
  - Before the gates every table was `unchanged`: model 2, demo size.
  - The first post-gate run reloaded Oracle `CRM.*`/`LOANS.APPLICANTS` and Mongo `crm.profiles` to demo size, because the M3 tests reseed them at test size.
  - The second post-gate run left every table `unchanged`, so the seed is idempotent.

## 2. Gates

The pytest gates ran one at a time, each as its own process (`run_gates.sh`). The frontend gate, the static gates and
the all-extras checks ran alongside them; none of them uses the stack.

| # | Gate | Result | Time |
|---|---|---|---|
| 1 | `ruff format --check dps tests tools` | 426 files already formatted (lane and all-extras venv, `--no-cache`) | — |
| 2 | `ruff check dps tests tools` | All checks passed (both venvs, `--no-cache`) | — |
| 3 | `mypy dps` | No issues in 150 source files: lane venv with its warm cache, lane venv with a **fresh** cache, all-extras venv with a fresh cache | 1 s / 5 s / 19 s |
| 4 | `DPS_TEST_REQUIRE_SERVICES=1 pytest -q`, whole suite, stack up, tesseract wrapper (3485 collected) | **3477 passed, 8 skipped, 0 failed** | 20:55 |
| 5 | `… -m "docs or ocr"` (M1) | **139 passed, 2 skipped** (need a host tesseract binary) | 0:16 |
| 6a | `… -m "db or pg_source or s3"` (M2), run 1 | **407 passed, 1 failed** → N1 (main's flaky check, fixed on main) | 9:40 |
| 6b | Same gate, full rerun after the other gates | **408 passed, 0 failed** | 9:07 |
| 7 | `… -m "oracle or mongo"` (M3) | **162 passed, 1 skipped** (MongoDB has no composite keys, by design). The N1 test passed here. | 4:29 |
| 8 | `… -m browser` (M4), with `DPS_TEST_TESSERACT_CMD` set to the app image's tesseract (`docker run --rm -i --network none`) | **215 passed, 0 skipped**. Includes the 37 capture-OCR tests (`browser and tesseract`: masked, obfuscated and crawler captures OCR-checked, with unmasked positive controls) and the 5 `test_no_submit.py` tests. | 11:56 |
| 9 | `cd frontend && npm ci && npm test && npm run build` | npm ci OK. Node tests **44/44**. Vitest: 27 files, **364 passed**. `tsc --noEmit && vite build` OK; `check-bundle` found no dev fixture code. | 0:09 |
| 10 | `DPS_SMOKE_SCANNER=1 DPS_SMOKE_REQUIRE_OCR=1 python tools/smoke_test.py --m3`, before the pytest gates | **All 52 checks passed.** Scan: 21 pages, 63 fields (51 personal), 3/3 captures masked, 60 POST + 1 GET blocked, `non_get_allowed` 0. M3 runs 3/3 at 20,000 rows; consistency 3/3. | 3:01 |
| 11 | Same smoke after all gates and the re-seed | **All 52 checks passed** (same figures) | 2:50 |

**Whole-suite skips (8):** all are environmental or by design. They are the same 8 as in fix round 1.
- 1 MongoDB composite-key case.
- 2 tests that need a tesseract binary on the host. The other 37 tesseract tests ran through the wrapper.
- 5 tests whose optional extras are not in the lane venv: pandas, mostlyai ×2, presidio, and the `[ner]` spaCy model. They pass in the all-extras venv (§2.2).

### 2.1 The one red run (gate 6a) — why it is not an M4 or product defect

**What failed:** gate 6a, `tests/connectors/test_oracle_pipeline.py::test_a_query_subset_creates_and_writes_only_the_projected_columns`.
- The assertion was `_survivors(…, "CUSTOMER_ID") == 0`. It got `1 == 0`.
- The job state was `succeeded`.
- The same test passed in gates 4, 6b and 7.

**The cause is main's test check, not the product:**
- `_survivors` counts target ids equal to **any** source id.
- CUSTOMER_ID is obfuscated with `fpe_digits`, which is FF1 under a fresh random run key. FF1 can map one masked id onto **another** customer's real id, even though no row keeps its own value.
- Main diagnosed exactly this in S1-M3 r2 (gates B1) and fixed it in **`d2fdc3f`** ("row-aligned test oracles … Same for the Oracle query-subset test"). That commit is on main, not on `m4`.
- The test is from M3 (`bd18b15`), and `git diff main...m4` does not touch it, its fixtures or the FPE/batch code.

**Probe on m4's code.** A scratch export of `a3cd667` ran with the lane's services; nothing was written to the repo. The test is `s2-gates-r2/scratch-tree/tests/connectors/test_s2r2_probe_collision.py`. Logs 27–29 hold counts only.

- **Deterministic reproduction.** I ran the m4 test's own fixture (300 ids, `fpe_digits`) under the key main pins for this case (`oracle-query-collision-145`):
  - FF1 maps 1 of the 300 ids onto another source id. There are 0 fixed points.
  - The job **succeeded**: 300 rows, columns CUSTOMER_ID/EMAIL/SEGMENT.
  - The target ids equal the predicted ones exactly.
  - m4's set-based check gives **CUSTOMER_ID = 1**, the exact failing value, and EMAIL = 0.
  - The row-aligned count is **0**.
- **Rate.** I tried 20,000 random keys with the product's own column plan and `BatchTransformer`:
  - 23 keys (**0.115 %**, about 1 in 870) cross an id onto another one.
  - 0 keys produce a fixed point.
  - The test runs in three gates per round (4, 6, 7), so a round turns red this way in about 0.35 % of rounds.
- **100 instrumented reruns** of the m4 test, each with its own random key, which `submit_run` returns:
  - 100/100 jobs succeeded, with 300 rows each.
  - Each of the 30,000 target ids is the FF1 image of its own source id, so no row was left untransformed.
  - Row-aligned survivors: CUSTOMER_ID **0**, EMAIL **0**.
  - The set-based check gave 0 in all 100 runs, and in every run it equalled the predicted crossings.

**Why it does not block:** `m4` does not modify this test file or `tests/connectors/helpers.py`. A merge of `m4` into main therefore
takes main's row-aligned version (`d2fdc3f`) automatically, with no conflict on those files. Fixing it on `m4`
would only duplicate main's commit.

### 2.2 Extra check — fresh venv with every extra (programme plan §2.5 step 3)

- **Venv:** `~/.cache/dps-build/s2-gates-r2/venv-all`, built with `uv pip install -e ".[ner,fpe,vertex,docs,db,connectors,uitest,scanner,tabular,synth-ml,dev]"`.
  - 233 packages.
  - This branch has no `e2e` extra.
  - The editable build wrote nothing into the tree.
- **Static checks:** ruff format and ruff check clean; **mypy, with every extra installed and a fresh cache: no issues in 150 files.**
- **Extras tests:** `tests/test_mostly.py`, `test_mostly_linked.py`, `test_ner_presidio.py` and `test_playground_samples.py`, run through `./lane.sh`:
  - **92 passed, 2 skipped.** Both skips are the missing spaCy English model, which is not installed anywhere.
  - This includes the real MOSTLY train/generate tests.

## 3. Proofs (counts only)

### D4 (demo site `/__received`, a counting server)

- **Smokes:** in both smokes every delta was **0** (non_get, form_get, logout_hits, admin_hits) and `by_path` did not change. Each smoke's own scan had `non_get_allowed` 0 and 0 submissions.
- **Whole suite and browser gate:** each moved the counters by exactly **+8 / +2 / +1 / +1**, with the same `by_path` split:
  - `POST /apply/identity/submit` ×2, `/api/draft` ×4, `/api/autosave` ×1 and `/api/telemetry/form-start` ×1.
  - These are the deliberate positive controls: `test_demo_site.py` (counter test, form-post test, the unguarded-page draft flush) and `test_no_submit.py::test_positive_control…`.
  - The guarded tests assert their own zero deltas, and they passed.
- **Nothing else moved the counters:** the M1/M2/M3 gates, the M2 rerun, the probes and the re-seed changed nothing.
- **Running totals:** 36/8/4/4 → 52/12/6/6.

### LLM exposure

`dps_llm_raw_pii_sent_total 0.0` before and after both smokes. The smokes' own `m3 raw pii to llm` check also reported 0.

### Logs

`log_scan.py`, all 15 compose services, over the window since 00:00 IST and over their full logs:
- **0 tracebacks.**
- **0 hits** of the demo site's customer values (normalised), the demo session cookie value, the compose demo API-key literal, PEM private-key markers, or key-material field names.
- **App:** no error-level lines. Warnings:
  - 2 `auth_rejected`: each smoke's deliberate "auth required" check;
  - 240 `rate_limited`: see N3.
- **Worker and browser-worker:** no error or warning lines.
- **Database error lines:** only errors that negative tests provoke on purpose:
  - check and unique constraints, RLS;
  - permission denied on audit/evidence/policy/scan tables;
  - append-only and immutability triggers;
  - wrong-password logins, a statement timeout, a read-only transaction, a too-long value.

### Source vs target (independent; `svt.py`; smoke 2's three M3 `table` runs, read after the run)

| Run | Rows | Alignment | Same-row survivors in non-kept columns | Value also found elsewhere in the source column |
|---|---|---|---|---|
| PG `bank.customers` → `bank_uat.customers_smoke` | 20,000 → 20,000 | 20,000/20,000 rows, on the 8 kept columns | **0** | customer_id 4 (FF1 crossings, see §2.1); date_of_birth 12,511 (`date_shift`: dates have a small domain) |
| Mongo `crm.profiles` → `crm_uat.profiles_smoke` | 20,000 → 20,000 | 20,000/20,000 documents, on `_id` | **0** | `$.customerId` 4; `$.profile.dob` 12,628 |
| Oracle `CRM.CUSTOMERS` → `CRM_MASKED.CUSTOMERS_SMOKE` | 20,000 → 20,000 | 20,000/20,000 rows, on `LAST_CONTACT_AT` | **0** | CUSTOMER_ID 4; DOB 12,511 |

The methods come from each run's accepted snapshot. These columns are all checked:
- names: `fpe_text`;
- emails: `email`;
- phones: `phone`;
- national ids and notes: `det_token`;
- tax ids: `fpe_text`;
- pin: `generalize`;
- iban: `fpe_text`.

Every one has 0 same-row survivors. The other 4 "found elsewhere" ids show the same FF1 property as the flake: all three runs use the group's key and the demo's shared ids, which is why each shows 4.

### Masked captures

- Smokes: 3/3 captures masked, 0 demo values in the persona export.
- Browser gate: 37 capture-OCR tests passed. OCR reads 0 values in the masked and obfuscated captures, and the unmasked positive controls read them.

### Commits

`main..m4` holds 63 commits.
- All 63 are authored and committed by `NilenduGanguli <nilendu.ganguli@gmail.com>`.
- **0 trailers** (no `Co-Authored-By` or `Signed-off-by`).
- `origin/m4` = `a3cd667`.

## 4. Non-blocking notes

- **N1 — main's flaky query-subset check (gate 6a; §2.1).**
  - The fix is main's `d2fdc3f`, and the merge of `m4` into main takes it automatically.
  - After the merge, the post-merge M2/M3 gates on main must show this test green under main's row-aligned check.
  - In this round it is the only red run, and it is not M4's.
- **N2 — main has moved since the pre-merge.**
  - Main has `5c11e49` and `d2fdc3f` ("M3 post-merge r1/r2": 34 files, +2942/−153). `m4` does not; its merge-base with main is `144cc65`.
  - A trial merge (`git merge-tree --write-tree m4 main`, which writes no ref and no worktree file) finds **1 textual conflict**: `docs/superpowers/plans/2026-10-11-m1-m5-contract.md`.
  - These 6 files are changed on both sides and auto-merge: `HANDOFF.md`, `dps/layers/api/routers/jobs.py`, `dps/layers/connectors/evidence.py`, `frontend/src/pages/Admin.tsx`, `EvidencePack.tsx` and `EvidencePack.test.tsx`.
  - This round's results cover `a3cd667` only. Programme plan §2.5 already requires every gate to run again on the combined tree after the merge.
- **N3 — the console tests exceed the app's per-key rate limit.**
  - The app logged 240 `rate_limited` warnings (HTTP 429) for the static key, in two bursts: 106 at 00:14 IST (the console tests of the whole suite) and 134 at 00:45 IST (the console tests of the browser gate).
  - The routes are console API calls: connections, snapshots, jobs, reports, admin.
  - Running `tests/console/test_console_nav.py` alone (3 tests, 15 s) produced **92** of them. That is the drawer walk added in S2 fix round 1: 7 pages × 7 sections × 2 widths, against `rate_limit_rps` 50 and burst 100.
  - The tests pass, because they check navigation, focus and scrolling, not the data on the page.
  - Earlier gate reports did not show this because their log scans covered only a smoke run after a restart.
  - For the console lens or F4: pace that walk, or check how the console shows a 429.
- **N4 — known (A-N2).** The console tests write their screenshots into tracked `docs/superpowers/build/m3-shots/` (54 PNGs per run). They are restored as described above. Suggested fix (F4): an env override or `tmp_path` for `SHOTS`.
- **N5 — known.**
  - `npm ci` reports 8 advisories (5 moderate, 1 high, 2 critical), the same as main.
  - Vite warns that the main chunk is 644 kB (its warning limit is 500 kB).
  - Every pytest run shows one `StarletteDeprecationWarning`.
- **N6 — cosmetic.**
  - The lane's dps-db still holds `dps_test_85735` (about 10 MB). It is a session database left by an earlier run; each session normally drops its own.
  - The one-shots built on the app image (`migrate`, `demo-secrets`, `seed-sources`) show Docker health `unhealthy` after a clean `exit 0`, because they inherit the image's HEALTHCHECK.

## 5. State left for the lens verifiers

- **Stack:** up with the five profiles (`docs sources oracle scanner tokenizer`). It is healthy, `/readyz` reports ready, and the browser worker is online.
  - Any `docker compose up` here reads `.env`.
  - `ps`, `logs` and `exec` need no extra variable.
- **Demo sources:** demo size, model 2:
  - PG `bank`: 20,000 customers;
  - Oracle `CRM.*`;
  - Mongo `crm.profiles`: 20,000.
- **Left by smoke 2:**
  - a fresh scan of the demo site under the demo session ref;
  - a new `smoke-m3-<epoch>` group;
  - rewritten `bank_uat.customers_smoke`, `crm_uat.profiles_smoke` and `CRM_MASKED.CUSTOMERS_SMOKE` (compared in §3).
- **Demo-site counters:** 52/12/6/6, all from the deliberate test controls. Take a fresh baseline before a D4 probe.
- **Tools:**
  - The tesseract wrapper is at `~/.cache/dps-build/s2-gates-r2/tess.sh`.
  - The source-vs-target probe is `./lane.sh python ~/.cache/dps-build/s2-gates-r2/svt.py <out.json>`. It reads the newest run into each smoke target and writes counts only.
- **Worktree:** clean at `a3cd667`.
