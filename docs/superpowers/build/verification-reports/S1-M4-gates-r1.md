# S1 — main after the M4 merge — GATES verifier, round 1

**Verdict: PASS. Nothing blocks.** Every gate is green on `main` @ `8ea220a` on the first run, with no reruns:
- the static gates;
- the whole suite with `DPS_TEST_REQUIRE_SERVICES=1`;
- the four milestone markers;
- the frontend;
- the all-extras venv;
- two smokes.

Both open S2 r3 findings are fixed and verified live on main's stack:
- **Console B1 (`932075a`): a stopped run is now worded by its batch.** On the real Reports page and evidence packs, at 1440 dark and 390 light, 22 of 22 failed packs read correctly against the job API.
- **Captures-privacy B1a–c (`40d8b34`): closed selects are masked.** The S2 r3 verifier's own select pages were run three ways: in process, through the crawler and in the deployed browser-worker image. None of the masked or obfuscated screens reads any of their made-up values, and every unmasked control reads its value.

| | |
|---|---|
| Date | 2026-10-15, 19:46–21:06 IST |
| Tree verified | `/Users/neelu/document_intelligence/data-privacy-services`, `main` @ **`8ea220a`** (= `origin/main`). Clean at the start and at the end; no merge in progress; `m4` fully merged. `40d8b34..8ea220a` changes only `HANDOFF.md`, `RESUME.md` and `M4-DEVIATIONS.md`. |
| Tools | `.venv`: Python 3.12.13, ruff 0.17.0, mypy 2.4.0, pytest 9.1.1, playwright 1.55.0, oracledb 26.0.0, pymongo 4.18.3. Node 22.20.0 / npm 10.9.3. Private browser venv: `~/.cache/dps-build/pw-s1-m4-gates` (playwright 1.55.0). |
| Endpoints | Contract §12.2 defaults (`~/.cache/dps-build/s1-m4-gates-r1/env.sh`): app 8400, dps-db 5441, source-pg 5442, MinIO 9010/9011, Oracle 1522, Mongo 27018, OCR 5016, demo site 8410, **tokenizer-mock 8430**. 8430 is HANDOFF §17's "Run main with the scanner" setting: the `m4` lane's demo site owns 8420. |
| Work dir | `~/.cache/dps-build/s1-m4-gates-r1/`: `logs/` (numbered in run order; `logs/gates/` per gate, with D4/LLM/test-DB snapshots before and after each), scripts (`chain.sh`, `run_all.sh`, `static_gate.sh`, `frontend_gate.sh`, `venv_all.sh`, `identity.sh`, `log_scan*.py`, `svt.py`, `ocr_probe.py`, `console_stopped_runs.py`, `watch_foreign.sh`, `post.sh`), `hostile/` (B1 probes), `console_shots/`, `m3-shots-from-run/` |
| Files modified | **None in the repo.** As in every round (A-N2), the console tests rewrote the 54 tracked PNGs in `docs/superpowers/build/m3-shots/`. Their mtimes (20:36:04–20:36:38) fall inside my browser gate. I copied them to `m3-shots-from-run/` and restored them to `HEAD` by explicit pathspec (list: `logs/90-restored-shots.txt`). Afterwards `git status` was empty, HEAD was still `8ea220a`, and the set of git-ignored paths had not changed. No `.env` exists in the main checkout. |
| Resume | An earlier attempt of this job (17:59, before the owner's pause) had only brought the stack up and polled `/readyz`; its two logs are kept as `logs/00-earlier-attempt-*`. Nothing else had run, so every gate here is a first run. |

## 1. Stack (left running for the lens verifiers)

```bash
cd /Users/neelu/document_intelligence/data-privacy-services
DPS_TOKENIZER_MOCK_PORT=8430 docker compose --profile docs --profile sources --profile oracle --profile scanner --profile tokenizer up --build -d
# or: ~/.cache/dps-build/s1-m4-gates-r1/dc.sh <compose args>
```

**Setup**
- **Docker:** 29.5.3, 16 CPUs, 35.2 GiB. Every compose project was stopped at the start. I touched only `data-privacy-services`.
- **Compose config:**
  - `config` printed no warning;
  - every published port is bound to 127.0.0.1 (`logs/03-stack-config.txt`);
  - the browser-worker publishes no host port.
- **Build:** all layers were cached; `up` exited 0 in about 10 s.

**Readiness**
- I polled `/readyz` and never used `--wait`. It reported `ready: true` at 19:46:41 and `browser 1 online` at 19:46:57; Oracle was healthy by then (existing volume).
- `/readyz` reported the same at the start and at the end (21:02):
  - api-core up; llm-gateway stub, `coin_tls=verified`; ner patterns-only; fpe ff1;
  - tokenizer up (http); ocr up (azure-read-v3.2); **browser 1 online**;
  - db up, migrations verified; jobs ready; keys db+static.

**The containers run the verified code** (`identity.sh`, at the start and again at the end: `logs/04-`, `logs/92-code-identity*.txt`)
- `app` and `worker`: all 195 files under `dps/` and `tools/`, plus `pyproject.toml` and `README.md`, have the same sha256 as the tree.
- `browser-worker`: all 178 files are identical. It has no `tools/`, by design.
- The served console bundle (`index-_9Of_JHk.js`, `index-Bpk1daTG.css`) is byte-identical to my host build from the frontend gate.
- `browser-worker` runs as uid 10001 with playwright 1.55.0 and Chromium 1187.

**Database**
- Migrations in `dps.dps_migration_ledger`: 19 rows for the 19 files in the tree (001–010, 020, 030–031, 040–045), each applied once.

**End state (21:02)**
- All 11 long-running services are healthy, with 0 restarts and no OOM kill.
- `migrate`, `minio-init`, `demo-secrets` and `seed-sources` exited 0.
- Highest memory use: Oracle at 81 % of its limit, Mongo at 63 %.

**Demo sources (data model 2, 20,000 rows)**
- The first seed found every table `unchanged`.
- The M3 gate left Oracle and Mongo at test size, as in earlier rounds; seed 2 reloaded them before smoke 2.

## 2. Gates

**How they ran**
- The pytest gates that use the stack ran one at a time, each in its own process (`run_all.sh` → `chain.sh`). The suites share the demo sources, the app's per-key rate limit and the D4 counters.
- The frontend gate ran beside seed + smoke 1 and finished before any pytest gate. Under TestClient the app serves the host's `frontend/dist`, so no rebuild may overlap a pytest run.
- The static gates and the all-extras venv ran alongside the gates; neither uses the stack.

**Concurrent load.** Other agents ran full pytest suites in `dps-wt-fsec`, `dps-wt-fpersona` and `dps-wt-fidcheck` at the same time, on their own lane stacks. That load slowed my whole suite; it did not change any result (§3.6).

| # | Gate | Result | Time |
|---|---|---|---|
| 1 | `ruff format --check dps tests tools` | 435 files already formatted. Same in `.venv` with its cache, with `--no-cache`, and in the all-extras venv. | — |
| 2 | `ruff check dps tests tools` | All checks passed (the same three ways) | — |
| 3 | `mypy dps` | No issues in 150 source files: warm cache, a **fresh** cache, and the all-extras venv with a fresh cache | 0 s / 6 s / 15 s |
| 4 | `DPS_TEST_REQUIRE_SERVICES=1 pytest -q`, whole suite, stack up, `DPS_TEST_TESSERACT_CMD` = the app image's tesseract (3606 collected) | **3598 passed, 8 skipped, 0 failed** | 26:07 |
| 5 | `… -m "docs or ocr"` (M1) | **139 passed, 2 skipped** (host tesseract on PATH). Those 2 pass separately, see gate 5b. | 0:15 |
| 5b | `tests/documents/test_ocr_tesseract.py` with the app image's tesseract on `PATH` (wrapper `bin/tesseract`) and `DPS_TEST_REQUIRE_TESSERACT=1` | **8 passed**, including the 2 that otherwise skip | 0:04 |
| 6 | `… -m "db or pg_source or s3"` (M2) | **434 passed, 0 skipped** | 10:42 |
| 7 | `… -m "oracle or mongo"` (M3) | **178 passed, 1 skipped** (MongoDB has no composite keys, by design) | 5:40 |
| 8 | `… -m browser` (M4), with `DPS_TEST_TESSERACT_CMD` | **271 passed, 0 skipped**. This includes the 36 tests of `tests/scanner/test_capture_select_drawn.py` (the B1a–c regression tests from `40d8b34`). | 17:58 |
| 9 | `cd frontend && npm ci && npm test && npm run build` | npm ci OK. Node tests **44/44**. Vitest: 28 files, **377 passed**, including the 10 new or updated B1 cases of `932075a`. `tsc --noEmit && vite build` OK. `check-bundle`: no dev fixture code. | 0:10 |
| 10 | `DPS_SMOKE_SCANNER=1 DPS_SMOKE_REQUIRE_OCR=1 python tools/smoke_test.py --m3`, before the pytest gates | **All 52 checks passed.** Scan: 21 pages, 63 fields (51 personal), 9 risk flags, 4/4 parallel-view shots, 3 captures (3 masked), 60 POST + 1 GET blocked, `non_get_allowed` 0. M3 runs 3/3 at 20,000 rows, consistency 3/3, evidence 3/3 verified. | 2:58 |
| 11 | The same smoke after all gates and seed 2 | **All 52 checks passed** (same figures) | 3:12 |
| + | Fresh venv with every extra (programme plan §2.5 step 3): `uv pip install -e ".[ner,fpe,vertex,docs,db,connectors,uitest,e2e,scanner,tabular,synth-ml,dev]"` | 233 packages. Static checks clean (rows 1–3). Extras tests (`test_mostly.py`, `test_mostly_linked.py`, `test_ner_presidio.py`, `test_playground_samples.py`): **92 passed, 2 skipped**. Both skips are the spaCy English model, which is installed nowhere. Both `ml` tests (real MOSTLY train/generate) passed. | 4:27 |

**Whole-suite skips (8):** the same 8 as in every earlier round (S2 gates r3, the merge agent's run at `40d8b34`).
- 1 MongoDB composite-key case.
- 2 tests that need tesseract on the host PATH. Gate 5b runs them green.
- pandas ×1 and mostlyai ×2: they pass in the all-extras venv.
- spaCy ×2.

**Marker coverage on main**
- `deploy`: 0 tests (OpenShift is on `fos`).
- `network`: 0 tests.
- `ml`: 2 tests, run in the all-extras venv.
- `ner`: 2 tests, which need a spaCy model.
- `tesseract`: 75 tests, run in the whole suite and the browser gate.
- `slow`: 30 tests, run in the whole suite.
- `integration`: 279 tests, run in the whole suite.

## 3. Proofs (counts only)

### 3.1 D4: the demo site's `/__received`, a counting server

- **Start:** 0/0/0/0. The demo site restarted with Docker, so its in-memory counters were reset.
- **Whole suite:** moved the counters by exactly **+8 / +2 / +1 / +1**.
- **Browser gate:** moved them by exactly **+8 / +2 / +1 / +1**.
- Both moves have the same `by_path` split as in every earlier round:
  - `POST /api/draft` ×4, `/apply/identity/submit` ×2, `/api/autosave` ×1 and `/api/telemetry/form-start` ×1;
  - these are the deliberate positive controls of `test_demo_site.py` and `test_no_submit.py`;
  - the guarded tests assert their own zero deltas, and they passed.
- **Nothing else moved the counters:**
  - both smokes, the M1, M2 and M3 gates, both seeds, all probes and the console check: **+0**;
  - each smoke's own scan also reported `non_get_allowed` 0, with 60 POST + 1 GET blocked.
- **End:** 16/4/2/2.

### 3.2 LLM exposure

`dps_llm_raw_pii_sent_total 0.0` before and after every gate. Each smoke's own `m3 raw pii to llm` check also reported 0.

### 3.3 Source vs target (`svt.py`, independent: reads source and target in full)

Both smokes' three M3 `table` runs were checked, each read after its run (`logs/40-`, `logs/41-svt-*.txt`, `svt-smoke*.json`).

| Run | Rows | Alignment | Same-row survivors in non-kept columns | Value also found elsewhere in the source column |
|---|---|---|---|---|
| PG `bank.customers` → `bank_uat.customers_smoke` | 20,000 → 20,000 (both smokes) | 20,000/20,000 rows, on the 8 kept columns | **0** | customer_id 3 / 4 (FF1 crossings); date_of_birth 12,556 / 12,632 (`date_shift`: dates have a small domain) |
| Mongo `crm.profiles` → `crm_uat.profiles_smoke` | 20,000 → 20,000 | 20,000/20,000 documents, on `_id` | **0** | `$.customerId` 3 / 4; `$.profile.dob` 12,620 / 12,646 |
| Oracle `CRM.CUSTOMERS` → `CRM_MASKED.CUSTOMERS_SMOKE` | 20,000 → 20,000 | 20,000/20,000 rows, on `LAST_CONTACT_AT` | **0** | CUSTOMER_ID 3 / 4; DOB 12,556 / 12,632 |

The methods match earlier rounds:
- names, tax ids and iban: `fpe_text`;
- customer ids: `fpe_digits`;
- e-mail: `email`;
- mobile: `phone`;
- national id, notes and address line: `det_token`;
- pin: `generalize`;
- every other column: `keep`.

### 3.4 Masked captures (independent OCR, `ocr_probe.py`, the app image's tesseract, psm 11 + 3)

I OCR'd every stored PNG of both smoke scans: `s_01M4ZYWD5S1SJ6AJ5F1J27HG0B` and `s_01M502HWY6NDMXFAVM55NYSGMJ`, synthesize mode. That is 69 images per scan.

- **Masked captures, 3 of 3 `captured`:** 0 personal demo values (name, e-mail, mobile, PAN, Aadhaar, card, account, customer id, address).
  - The only read is `application_ref` on page 25 (`/apply/review`). That is the known N9 of earlier rounds: not a detected entity.
  - The same single read appears in `original` and `annotated`.
- **Filled shots:** 0 demo values.
- **Positive control:** OCR reads the scan's own display-persona values in 11 of 12 `filled_synthesize` shots (221 and 219 needle hits).

### 3.5 Logs (all 15 containers; run window since 14:16Z = 19:46 IST)

**Health**
- 0 tracebacks.
- 0 PEM private-key markers and 0 key-material field names.
- **App:** no error-level lines. Warnings:
  - 70 `rate_limited`: the console tests, the known N3;
  - 2 `auth_rejected`: each smoke's deliberate check.
- **Worker and browser-worker:** no warnings or errors.
- **Database error lines:** only errors that negative tests provoke on purpose:
  - append-only and immutability triggers (`dps_audit_log`, `dps_evidence`, `dps_policy_version`);
  - permission denied, check constraints, unique keys, RLS;
  - wrong-password logins, value too long, a read-only transaction, a statement timeout.

**Value scans** (`log_scan.py`, `log_scan_tail.py`, `log_scan_source.py`)
- **Demo-site values:** **0** hits, line by line, in every container.
- **Seeded source values:** **0** hits. The scan used 129,792 needles from PG `bank.customers`, Oracle `CRM.CUSTOMERS` and Mongo `crm.profiles`:
  - 20,000 e-mails;
  - 55,565 names and alphanumeric ids;
  - 54,227 digit ids of 9 or more digits.

  A hit must be a contiguous token, so separate numbers are never joined (S2's N7). An in-memory positive control finds 4/4.
- **New scan-method note (N2):** for main's Mongo container, `docker logs --since` and any forward read stop at `2026-10-14T11:10:14Z`. I therefore read Mongo's 31,292 run-window lines from the end (`--tail 300000`). They hold **0** hits by every scan above.

### 3.6 Foreign runs against main's default ports

A watcher (`watch_foreign.sh`) logged every host process with a connection to main's published ports every 20 s, with its cwd and its ancestor's cwd (`logs/foreign-watch.log`, 421 lines).

- **No process from any `dps-wt-*` worktree connected.** The worktrees without `.env.lane` (fllm, m4ui, m5ui) ran nothing against main. `fidcheck` gained its own lane ports (86xx) at 19:48.
- **Four short-lived processes could not be attributed:** they exited before `lsof` read their cwd. They appeared at 19:59:58, 20:25:01 and 20:27:24, while my own suites were spawning subprocess workers.
- **No foreign test database was created:**
  - the `dps_test_*` list was identical before and after every gate;
  - each session dropped its own database;
  - only two leftovers from before my run remain.
- Every D4 delta is the expected one.

### 3.7 Commits

- `d2fdc3f..HEAD` (the M3 post-merge head to now) holds 68 commits, including the M4 merge `5959a75` and its 3 integration commits.
- All 68 are authored and committed by `nilendu.ganguli@gmail.com`.
- **0 trailers** (no `Co-Authored-By` or `Signed-off-by`).
- `origin/main` = HEAD = `8ea220a`.
- `git grep` finds no conflict marker in the tree. Contract §8.1, the merge's one textual conflict, reads coherently with both sides kept.

## 4. The S2 r3 open findings: fixed

### 4.1 Console B1 — a stopped run is worded by its batch (`932075a`)

**Unit tests:** the 10 new or updated Vitest cases cover:
- `failedSweepText` with `paths ['partition 0']`;
- failed and canceled packs in `EvidenceTable`;
- the `EvidencePack` post-write row for failed and canceled runs, and for runs that wrote 0 rows.

All of them are among the 377 passing tests (gate 9).

**Live check on main's stack** (`console_stopped_runs.py`, private Playwright, 1440 dark and 390 light; `logs/50-console-stopped-runs.{txt,json}`, `console_shots/`).

Truth from the API:
- 22 run jobs ended failed; none ended canceled or dead.
- 2 of them wrote rows. Both were stopped by the leakage sweep on 14 Oct, 9,000 rows each:
  - `j_01M4XD46S7VEDXE10YTMM0YHJX`, Oracle `CUSTOMERS_SMOKE`;
  - `j_01M4XD46RY4DE9TGKM2RE97D52`, Mongo `profiles_smoke`.
- For all 22, each pack's `summary.rows_written` equals its job's `progress.rows_written` (0 mismatches).
- `/reports/jobs` lists the 2 runs as failed sweeps with `paths: ['partition 0']`.

| Surface | Result at 1440 and 390 |
|---|---|
| Reports › Evidence packs (Layer Connectors, Outcome Failed): 22 rows, each matched to its job | **22/22 correct.** The 2 runs that wrote rows read "stopped · 9,000 rows written before it" with the "failed closed" chip. The 20 runs that wrote 0 rows read "stopped, nothing written", which is true for them. |
| Reports › Failed sweeps | Both entries: "The leakage sweep found a source value in a batch of partition 0. That batch was not written (fail closed); batches committed before it may already be in the target. Open the job to see what was written." The fix line is "Open the job to see what was written, then re-run it.", with no "Review the columns named above" when no column is named. "nothing reached the target" and "The partitions were discarded before writing" appear nowhere. |
| Evidence pack › Verification › Post-write sample (both runs) | "not run · The job ended failed before the post-write sample; the 9,000 written rows were not re-read. · skipped", next to "Row counts 9,000 read · 9,000 written". |
| Console errors / horizontal scroll | 0 / none |

No canceled run that wrote rows exists on main's stack. The Vitest cases cover that path.

### 4.2 Captures-privacy B1a–c — a closed select is masked by what it draws (`40d8b34`)

**Method**
- I re-ran the S2 r3 verifier's own hostile pages (made-up values only; `hostile/`, copied byte-identical from `S2-captures-privacy-r3/hostile/site`).
- The probes are the same, pointed at main's code and image.
- OCR is the app image's tesseract, 2× upscale, psm 11 + 3. A count of 2 means one readable copy.

| Where | Cases | Result |
|---|---|---|
| Direct, in process, main's code with host Chromium 1187: masked, synthesize and obfuscate captures plus an unmasked control | All 24 select pages: B1a `r3_sel_empty_value`, `_ws_value`, `_empty_value_label`, `r3b_sel_empty_value_acct`; B1b `r3_sel_over300`, `_over300_email`, `r3b_sel_over300_acct`, `_over300_name`, plus the index-299 control; B1c `r3_sel_base_label`, `_base_button`, `r3b_sel_base_acct`, `_named`; the `r3c_*` pages; the round 2 select cases | **72 non-control captures: 0 reads, except `r3_sel_late`** (carried N6 below). 23 of 24 controls read their value; `r3_sel_fill_choice` draws no value before the fill, as in S2 r3. On `cbbac38` the same probe read 2 in every B1 capture. |
| Crawler (`ScanRunner` + `MemoryScanSink`), both modes plus an unmasked-originals control | 19 `r3*_sel*` pages | **186 stored product screens (`original`, `annotated`, `filled_synthesize`, `filled_obfuscate`, `masked_capture`): 0 reads.** That includes every `r3c_*` obfuscated `masked_capture`, which read acct 2 on `cbbac38`. Control originals read their value in 17 of 19; the other 2 draw none at read time. |
| **Deployed image** `data-privacy-services-browser:local` (`--network none`, uid 10001, Chromium 140.0.7339.16): direct and crawler | 17 direct cases and 6 crawler cases × 2 modes | **128 PNGs. All 17 controls read their value; 111 non-control images: 0 reads.** 0 non-GET requests received. |
| Regression tests | `tests/scanner/test_capture_select_drawn.py`, 36 tests | All pass (whole suite and browser gate) |

**On main the inventory now reports these selects:**
- `prefilled` for the B1 pages is `PAN_IN`, `ACCOUNT_NUMBER`, `EMAIL`, `PERSON_NAME`, `SELECT` or `FREE_TEXT`.
- `api-m4.md` (§ captures) documents the rule as built.

## 5. Notes (non-blocking)

- **N1 (carried from S2 gates r3 N1, for main): the strict per-row leakage sweep fails runs on chance digit matches.**
  - The two stopped runs on main's stack (14 Oct, 9,000 of 20,000 rows each) are this class.
  - Neither of my two smokes tripped it.
  - Availability only: the run fails closed. The owner is still main / F1 or F4.
- **N2 (new, verification method):** main's Mongo container log can no longer be read forward past `2026-10-14T11:10:14Z`.
  - `docker logs`, `docker logs --since …` and `docker compose logs mongo` return only the 63,158 lines before that point, and nothing for the run window. That is a Docker json-file reader issue; the container logs normally (`--tail N` with N below the total works).
  - **The merge agent's log scan, which HANDOFF §17 quotes ("the logs of all 15 containers hold … 0 demo values"), therefore never covered Mongo's recent logs.**
  - This report's tail-based scan now backs that claim for this run window: 31,292 lines, 0 hits (§3.5).
  - Later log scans of main's Mongo should read with `--tail` (`log_scan_tail.py`), or the owner can recreate the container to reset its log.
- **N3 (known):** the console tests exceed the per-key rate limit (70 `rate_limited` warnings). The tests pass.
- **N4 (known, A-N2):** the console tests rewrite the 54 tracked PNGs in `m3-shots/`. I restored them as described above.
- **N5 (known, test noise and tooling):**
  - `npm ci` reports 8 advisories (5 moderate, 1 high, 2 critical), the same as before.
  - Vite warns that the main chunk is 646 kB (its warning limit is 500 kB).
  - Every pytest run shows one `StarletteDeprecationWarning`.
  - The whole-suite output contains 3 `key_refresh_failed` and 1 `audit_flush_failed` warning lines (`gaierror`). Test apps point at an unresolvable host; the timing-dependent count went up under the parallel load.
- **N6 (carried, S2 r3 N3): a select that page script fills after the page was read** (`r3_sel_late`) stays readable in every direct capture. `api-m4.md` lists "an input value a script sets after the page was read" as a known limit, but not selects.
- **N7 (carried, N9):** `application_ref` is readable on `/apply/review` in `original`, `annotated` and `masked_capture`. It is not a detected entity.
- **N8 (cosmetic):** dps-db still holds `dps_test_29095` and `dps_test_32198`, session databases left before this run.

## 6. State left for the lens verifiers

- **Stack:** up with the five profiles (`docs sources oracle scanner tokenizer`, tokenizer-mock on 8430). It is healthy, `/readyz` reports ready, and the browser worker is online.
  - Use `~/.cache/dps-build/s1-m4-gates-r1/dc.sh <args>` (it exports `DPS_TOKENIZER_MOCK_PORT=8430`), or set that variable before any `docker compose up` here; otherwise compose recreates tokenizer-mock on 8420.
  - `ps`, `logs` and `exec` need no extra variable.
- **Endpoints for tests and tools:** `source ~/.cache/dps-build/s1-m4-gates-r1/env.sh` (contract §12.2 defaults).
- **Demo sources:** demo size, model 2: PG `bank` with 20,000 customers; Oracle `CRM.*` and `LOANS.APPLICANTS`; Mongo `crm.profiles` with 20,000 documents.
- **Left by the smokes:**
  - two more succeeded scans of the demo site (`s_01M4ZYWD5S1SJ6AJ5F1J27HG0B`, `s_01M502HWY6NDMXFAVM55NYSGMJ`);
  - smoke groups `smoke-m3-<epoch>`;
  - `bank_uat.customers_smoke`, `crm_uat.profiles_smoke` and `CRM_MASKED.CUSTOMERS_SMOKE`, last written in full by smoke 2 and compared in §3.3.
  - The Reports page still shows the two stopped runs of 14 Oct in its failed figures.
- **Demo-site counters:** 16/4/2/2, all from the deliberate test controls. Take a fresh baseline before a D4 probe.
- **Tools:**
  - `bin/tesseract` and `tess.sh`: the app image's tesseract with no network.
  - `svt.py <out.json>`: compares the newest run into each smoke target.
  - `ocr_probe.py <scan_id>`: OCRs a scan's stored screenshots.
  - `log_scan.py`, `log_scan_tail.py <container> <since>`, `log_scan_source.py <since>`.
  - `console_stopped_runs.py`.
  - `hostile/` (`probe_direct.py`, `probe_runner.py`, `run_container.sh`, `summ_*.py`).
- **Repo:** clean at `8ea220a`. The all-extras venv (1.9 GB with the MOSTLY stack) is at `~/.cache/dps-build/s1-m4-gates-r1/venv-all`.
