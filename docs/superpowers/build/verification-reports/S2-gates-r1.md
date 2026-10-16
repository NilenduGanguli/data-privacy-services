# S2 — gates on branch `m4` after main was merged into it (GATES verifier, round 1)

**Verdict: FAIL — one blocking item (B1).** Every gate is green except one test in the M2 marker gate
(`-m "db or pg_source or s3"`: 404 passed, **1 failed**). The failing test is a D-A check in main's M5
code (`tests/connectors/test_synthesize_copula.py::test_copula_job_on_the_seeded_demo_customers`). It
fails at random: 1 failure in 11 runs here. M4 did not change that code. It is listed as blocking because
a red D-A gate cannot be waved through, and the fix is small (see B1). Everything else is green: the
whole suite, the `docs or ocr`, `oracle or mongo` and `browser` gates, the frontend, two full smokes
(52/52 each), ruff, mypy, and an all-extras venv.

| | |
|---|---|
| Date | 2026-10-14, 20:29–21:31 IST |
| Tree verified | `dps-wt-m4`, branch `m4` @ `a0e8d26` (clean at start and at end). It contains main `144cc65` (merges `b34dd07`, `4e55da3`) plus the pre-merge adaptations `b6f7e82..a0e8d26`. |
| Python / tools | lane `.venv` (Python 3.12.13; ruff 0.17.0, mypy 2.4.0, pytest 9.1.1, playwright 1.55.0, oracledb 26.0.0, pymongo 4.18.3); Node 22.20.0 / npm 10.9.3 |
| Endpoints | `./lane.sh` (`.env.lane`): app 8414, dps-db 5463, source-pg 5464, MinIO 9050/9051, Oracle 1524, Mongo 27020, OCR 5046, demo site 8420, tokenizer-mock 8424 |
| Logs | `~/.cache/dps-build/s2-gates-r1/logs/` (one file per step, numbered in run order) |
| Files modified | **none.** The console tests rewrite 54 tracked PNGs in `docs/superpowers/build/m3-shots/` (known A-N2). I copied them to `~/.cache/dps-build/s2-gates-r1/m3-shots-from-run/` and restored them to `HEAD` by explicit pathspec (list: `../restored-shots.txt`). `git status` is empty and HEAD is still `a0e8d26`. |

## 1. Stack (left running for the lens verifiers)

```bash
cd /Users/neelu/document_intelligence/dps-wt-m4
docker compose --profile docs --profile sources --profile oracle --profile scanner --profile tokenizer up --build -d
```

- Docker 29.5.3, 16 CPUs, 35 GiB. Every m4 port was free before the start. All image layers were cached (`data-privacy-services:m4`, `data-privacy-services-browser:m4`, `document_intelligence-azure-ocr-mock:m4`), so the images match `a0e8d26`.
- `docker compose config -q` printed no unset-variable warning for these profiles, and `--profile full config -q` is OK. `.env` already sets `DPS_TOKENIZER_MOCK_PORT=8424`, so no port variable had to be added and neither `.env` file was touched.
- I polled `/readyz` (never `--wait`). It answered `ready: true` on the first poll: api-core up, llm-gateway stub (coin_tls verified), ner patterns-only, fpe ff1, tokenizer up (http), ocr up (azure-read-v3.2), **browser 1 online**, db up (migrations verified), jobs ready, keys db+static.
- End state, checked at 21:30: app, worker, browser-worker, dps-db, source-pg, minio, oracle, mongo, azure-ocr-mock, demo-site and tokenizer-mock are up and healthy. `migrate`, `minio-init`, `demo-secrets` and `seed-sources` exited 0. Every container shows 0 restarts and no OOM kill.
- M4 DoD 3: `exec browser-worker` reports playwright **1.55.0**, Chromium build 1187 under `/ms-playwright`, uid 10001 (non-root).
- Migrations 001–010, 020, 030–031, 040–045: each number appears once.
- Demo seeds: `tools/seed_sources.py --target all --rows 20000 --seed 7`, run twice before the gates and twice after them. The first post-gate run put Oracle `CRM.*`/`LOANS.APPLICANTS` and Mongo `crm.profiles` back at demo size (the M3 tests reseed them at test size). The second run left every table `unchanged`, so the seed is idempotent.

## 2. Gates

| # | Gate | Result | Time |
|---|---|---|---|
| 1 | `ruff format --check dps tests tools` | 425 files already formatted | — |
| 2 | `ruff check dps tests tools` | All checks passed | — |
| 3 | `mypy dps` | no issues in 150 source files | — |
| 4 | `DPS_TEST_REQUIRE_SERVICES=1 pytest -q` (whole suite, stack up, 3457 collected) | **3422 passed, 35 skipped, 0 failed** | 17:47 |
| 5 | `… -m "docs or ocr"` (M1) | **139 passed, 2 skipped** (host tesseract) | 0:16 |
| 6 | `… -m "db or pg_source or s3"` (M2) | **404 passed, 1 FAILED** → B1 | 9:17 |
| 7 | `… -m "oracle or mongo"` (M3) | **162 passed, 1 skipped** (Mongo has no composite keys, by design) | 4:21 |
| 8 | `… -m browser` (M4), with `DPS_TEST_TESSERACT_CMD` set to a wrapper around the app image's tesseract (`docker run --rm -i --network none`) | **194 passed, 0 skipped**. This includes the 27 capture-OCR tests (the masked captures OCR-checked) and `test_no_submit.py` | 10:57 |
| 9 | `cd frontend && npm ci && npm test && npm run build` | npm ci OK; node tests **42/42**; Vitest 25 files **356 passed**; build `tsc --noEmit && vite build` OK; `check-bundle` found no dev fixture code | 0:09 |
| 10 | `DPS_SMOKE_SCANNER=1 DPS_SMOKE_REQUIRE_OCR=1 python tools/smoke_test.py --m3`, before the pytest gates | **all 52 checks passed** (Layer 1, documents + OCR, scanner 11, M2 9, M3 7: runs 3/3 at 20 000 rows, consistency 3/3) | 2:48 |
| 11 | Same smoke after all the gates and the re-seed | **all 52 checks passed** | 3:03 |

The whole-suite gate had 35 skips, all environmental or by design: 1 capability skip (Mongo composite keys); 29 tesseract (2 docs tests need a host binary; the 27 capture-OCR tests ran and passed in gate 8); 5 optional extras not installed in the lane venv (pandas/mostlyai ×3, presidio, spaCy model), which pass in the extra check below.

### 2.1 Extra check — fresh venv with every extra (programme plan §2.5 step 3)

I built `~/.cache/dps-build/s2-gates-r1/venv-all` with `uv pip install -e ".[ner,fpe,vertex,docs,db,connectors,uitest,e2e,scanner,tabular,synth-ml,dev]"`. Its tool versions match the lane venv.
- ruff format and ruff check are clean. **mypy with every extra installed found no issues in 150 files.**
- `tests/test_mostly.py`, `test_mostly_linked.py`, `test_ner_presidio.py`, `test_playground_samples.py`: **92 passed, 2 skipped**. Both skips are the missing spaCy English model, which is not installed anywhere. This includes the real MOSTLY train/generate tests.

## 3. Proofs (counts only)

- **D4 (demo site `/__received`):** all four counters were 0 before the first smoke and still 0 after it (the smoke's own scan: 60 POST + 1 GET blocked, `non_get_allowed` 0, evidence submissions 0). Over the final smoke every delta was **0** (`non_get`, `form_get`, `logout_hits`, `admin_hits`) and `by_path` did not change. Between the two smokes the counters did move: non_get 16, form_get 4, logout 2, admin 2, on `POST /api/autosave` ×2, `/apply/identity/submit` ×4, `/api/draft` ×8 and `/api/telemetry/form-start` ×2. Those are the deliberate positive controls in `tests/scanner/test_demo_site.py` and the unguarded-page controls, which ran twice (whole suite and browser gate). Each count is even and matches those tests. The guarded no-submit tests assert their own zero deltas and passed.
- **LLM exposure:** `dps_llm_raw_pii_sent_total 0.0` before the smokes and at the end.
- **Logs** (`log_scan.py`, all 15 containers, after every run): **0 tracebacks**. There are **0 hits** of the demo site's customer values (normalised, as in the S2 probe) and 0 hits of the demo session cookie value. The app logged two warnings, both `auth_rejected` from the smoke's "auth required" check (one per smoke). The database logs hold only errors that negative tests provoke on purpose: append-only and immutability triggers, RLS, permission checks, check and unique constraints, a statement timeout, a read-only transaction, and wrong-password logins. Each count is even (the test ran twice). App, worker and browser-worker logged no error-level lines.

## 4. Blocking finding

### B1 — the D-A seeded-demo tests fail at random: a synthetic `national_id` equals a demo-source one

- **What failed:** gate 6, `test_copula_job_on_the_seeded_demo_customers`. Comparing source and target (normalised, counts only): **1** distinct target `national_id` equals a `bank.customers.national_id` (tax_id 0). The job state was `succeeded`. Log: `logs/22-pytest-db.log`.
- **It is random, and it predates M4:**
  - The same test passed in gate 4 and in 3 direct reruns (`logs/32-rerun-seeded-demo-tests.log`).
  - A probe (`~/.cache/dps-build/s2-gates-r1/probe/`, test-module helpers, nothing written to the repo) ran 6 copula and 6 surrogate-only jobs on the demo table. All had 0 survivors. The run's own check reported `in_source` 0 / `issuable` 0 and verification `passed` (`logs/probe-da-runs.jsonl`).
  - So this test failed 1 time in 11. S2-premerge saw its sibling `test_surrogate_job_on_the_seeded_demo_customers` fail about 1 time in 8. Both run in gate 4 and gate 6, so each of those gates turns red in roughly 1 run in 5–10.
  - `git diff main...m4` touches no synthesis, seed or identifier-check code. The only change under `dps/engine` is moving constants in `detect/structural.py`.
- **The product still flags a collision:** the run's post-write identifier check compares every target row with the source column after the same letters-and-digits normalisation. `test_identifier_check.py` and `tests/connectors/test_synthesize_identifiers.py::test_a_real_identifier_in_the_target_needs_review` both passed in gates 4 and 6, and they show a hit gives `in_source ≥ 1` → `review_required`, `passed: false`. So a collision is reviewed; it is never silently passed as "verified". The red gate comes from how the test data is built, not from a leak.
- **Root cause (by construction, counts only):** the demo seed (`tools/seed_model.py`) makes its national ids with the same surrogate engine that D-A uses.
  - **11 989** of 20 000 demo `bank.customers` rows are Aadhaar-type. All of them sit in the never-issued range (12 digits, leading `1`, Verhoeff-valid) that `unissued.twelve_digits` and `SurrogateEngine._aadhaar` draw from for a NATIONAL_ID column.
  - The demo NINOs (2 010, prefix `QQ`), SSNs (3 998, area 9xx, against TIN9 surrogates `000…`) and CURPs (2 003, not inconvenient words) all fall outside the ranges the NATIONAL_ID surrogates use.
  - The demo SSNs are also inside the 9xx range that SSN-typed surrogates draw from (`tin_digits`).
  - A synthesize run of the demo table can therefore write a value that the demo source also holds. The observed rate is higher than a uniform draw over 10¹⁰ predicts (about 1.4 % per run), so the fixer should confirm the exact path.
- **Fix (do not weaken the test):** keep the assertions `survivors == {national_id: 0, tax_id: 0}` and `(in_source, issuable) == (0, 0)` exactly as they are. Then make the demo ids disjoint from what the surrogates can produce, for example:
  - Aadhaar: give the demo the leading `0`, which is also never issued, and keep leading `1` for the surrogates (or the other way round).
  - SSN: give the demo area `666`, which is never issued and neither surrogate family draws.
  - Add a deterministic test that no seeded identifier lies in a range a D-A surrogate of its type, or of NATIONAL_ID, can draw.
  - Re-seed existing stacks with `--reset` or a seed-version bump.
  - Alternative product-side fix: the synthesize run redraws any identifier surrogate already present in the source column. That would make D-A hold even on UAT sources full of test ids, but it is a larger change in a hot path.

  Fixing this on `m4` brings it into main with the coming merge. Today it would turn main's gates red at the same rate.

## 5. Non-blocking notes

- **N1 — main moved after the pre-merge.** `5c11e49` (20:49, "M3 post-merge r1": `dps/engine/batch.py`, `connectors/{evidence,jobs_run,targets,files,docpaths}.py`, `routers/jobs.py`, 3 console pages, 25 files) is on main but not on `m4`. Both sides edit `dps/layers/connectors/evidence.py`. The `m4` → main merge must run the §2.5 gates again on the combined tree; this round's results cover `a0e8d26` only.
- **N2 — known (A-N2).** The console tests write their screenshots into tracked `docs/superpowers/build/m3-shots/` (54 PNGs per run). I restored them as described above. Suggested fix (F4): an env override or `tmp_path` for `SHOTS`.
- **N3.** `npm ci` reports 8 advisories (5 moderate, 1 high, 2 critical), as on main (S1 N2); most are dev/test-only. Vite warns that the main chunk is 641 kB (over its 500 kB warning limit). Every pytest run shows one `StarletteDeprecationWarning`.
- **N4 — state for the lens verifiers.**
  - The stack is up with the four profiles.
  - Demo sources are at demo size (seed 7, 20 000 customers on PG, Oracle and MongoDB).
  - The final smoke left a fresh scan of the demo site under the demo session ref, a new `smoke-m3-<epoch>` group, and rewrote `bank_uat.customers_smoke`, `crm_uat.profiles_smoke` and `CRM_MASKED.CUSTOMERS_SMOKE`.
  - The demo-site counters stand at non_get 16 / form_get 4 / logout 2 / admin 2 from the deliberate test controls above; take a fresh baseline before a D4 probe.
  - The tesseract wrapper is at `~/.cache/dps-build/s2-gates-r1/tess.sh`.
  - Any `docker compose up` on this project reads `.env` (tokenizer-mock 8424); `ps`, `logs` and `exec` need no extra variable.
