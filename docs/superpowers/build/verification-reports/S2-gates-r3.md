# S2 — gates on branch `m4` after main was merged into it (GATES verifier, round 3)

**Verdict: PASS. Nothing blocks the merge of `m4` into main.** Every gate is green on `m4` @ `cbbac38`
(S2 fix round 2). Two runs were red once. Neither is caused by M4, and both passed when run again:

- **Gate 6a (M2 marker): 1 of 409 failed.** The test is main's three-way e2e
  (`test_m3_three_way.py::test_three_way_consistency_join`, MongoDB `$.customerId`). It counted an FF1
  customer id equal to *another* customer's id as a survivor; no document kept its own id (`same_row` 0).
  - Main already fixed this check in `d2fdc3f` ("about 6 % of runs at 2,400 customers").
  - `m4` does not touch the file, so the merge takes main's version automatically.
  - The full M2 gate passed 409/409 on the rerun.
- **Smoke 2: 48/49.** The PostgreSQL run of the M3 block failed closed with `leakage` after 9,000 of 20,000 rows.
  - **Cause: a rare chance match in main's strict per-row sweep (M2 engine code).** The engine is byte-identical on main, and M4 never touched it.
  - Nothing leaked. The failing batch was not written, and the 9,000 rows that were written have 0 same-row survivors.
  - Smoke 3 passed all 52 checks.
  - **This is a new finding for main (N1): fail-closed false positives that grow with table size.**

| | |
|---|---|
| Date | 2026-10-15, 06:53–08:25 IST |
| Tree verified | `dps-wt-m4`, branch `m4` @ `cbbac38` (= `origin/m4`). Clean at the start and at the end; no merge in progress. It contains main up to `144cc65` (M0–M3, M5), all of M4, and S2 fix rounds 1 and 2. |
| Python / tools | Lane `.venv`: Python 3.12.13, ruff 0.17.0, mypy 2.4.0, pytest 9.1.1, playwright 1.55.0, oracledb 26.0.0, pymongo 4.18.3. Node 22.20.0 / npm 10.9.3. |
| Endpoints | `./lane.sh` (`.env.lane`): app 8414, dps-db 5463, source-pg 5464, MinIO 9050/9051, Oracle 1524, Mongo 27020, OCR 5046, demo site 8420, tokenizer-mock 8424 |
| Logs, scripts, probes | `~/.cache/dps-build/s2-gates-r3/` (`logs/` numbered in run order; `run_gates.sh`, `run_rerun.sh`, `pre.sh`, `post.sh`, `smoke3.sh`, `identity.sh`, `log_scan.py`, `svt.py`, `ocr_probe.py`, `probe/`) |
| Files modified | **None in the repo.** As in every round (A-N2), the console tests rewrote the 54 tracked PNGs in `docs/superpowers/build/m3-shots/`. I copied them to `s2-gates-r3/m3-shots-from-run/` and restored them to `HEAD` by explicit pathspec (list in `restored-shots.txt`; their mtimes matched my gate runs). Afterwards `git status` was empty, HEAD was still `cbbac38`, and the set of git-ignored files had not changed. I did not read or touch `.env` or `.env.lane`. |

## 1. Stack (left running for the lens verifiers)

```bash
cd /Users/neelu/document_intelligence/dps-wt-m4
docker compose --profile docs --profile sources --profile oracle --profile scanner --profile tokenizer up --build -d
```

- **Docker:** 29.5.3, 16 CPUs, 35 GiB. Other projects running: `data-privacy-services` (main) and `dps-wt-fsec`. I did not touch them.
- **Compose config:** `config -q` printed no unset-variable warning, either for these five profiles or for `--profile full`.
  - Every published port comes from the lane `.env` and is bound to 127.0.0.1.
  - `DPS_PG_PORT`, `DPS_EXPORT_ROOT` and `DPS_WORKER_METRICS_PORT` are fixed in-container values, not host ports. The tokenizer-mock port is already 8424. So I added no variable.
- **Build:** every layer was cached (13 s). The image IDs changed but their creation times did not (03:03/03:04, fix round 2's final build).
  - Compose recreated app, worker, browser-worker, azure-ocr-mock, tokenizer-mock and the one-shots.
- **Readiness:** I polled `/readyz` and never used `--wait`. It reported `ready: true` on the first poll (06:56):
  - api-core up; llm-gateway stub, `coin_tls=verified`; ner patterns-only; fpe ff1;
  - tokenizer up (http); ocr up (azure-read-v3.2); **browser 1 online**;
  - db up, migrations verified; jobs ready; keys db+static.
  - The final poll (08:24) reported the same.
- **The containers run the verified code** (`logs/09-code-identity.txt`):
  - `app` and `worker`: all 195 files under `dps/` and `tools/`, plus `pyproject.toml` and `README.md`, have the same sha256 as the tree.
  - `browser-worker`: all 178 files are identical. It has no `tools/`, by design.
  - The console bundle the app serves (`index-BpQsOVd-.js`, `index-Bpk1daTG.css`) is byte-identical to my host build.
- **M4 DoD 3:** `browser-worker` has playwright 1.55.0 and Chromium 1187 under `/ms-playwright`, and runs as uid 10001.
- **Migrations** in `dps.dps_migration_ledger`: 001–010, 020, 030–031 and 040–045 (19 files), each applied once.
- **End state (08:24):**
  - All 11 long-running services are healthy, with 0 restarts and no OOM kill.
  - `migrate`, `minio-init`, `demo-secrets` and `seed-sources` exited 0.
  - Highest memory use: Oracle at 81 % of its limit.
- **Demo seeds:** `tools/seed_sources.py --target all --rows 20000 --seed 7`.
  - Before the gates, PG `bank` was already seeded. Oracle and Mongo were reloaded to demo size, because fix round 2's M3 tests had left them at test size.
  - After the gates I ran it twice. The first run reloaded Oracle `CRM.*`/`LOANS.APPLICANTS` and Mongo `crm.profiles`. The second left every table `unchanged`, so the seed is idempotent.

## 2. Gates

The pytest gates that use the stack ran one at a time, each in its own process (`run_gates.sh`, then `run_rerun.sh`).
I did not run them in parallel: the console tests are also marked `pg_source`/`oracle`/`mongo`, so every marker gate
drives the app with the same per-key rate limit, and the D4 tests assert counter deltas.

The following ran alongside them, because none of them uses the stack: the static gates, the frontend gate, the fresh
all-extras venv and its tests, and the probes.

| # | Gate | Result | Time |
|---|---|---|---|
| 1 | `ruff format --check dps tests tools` | 427 files already formatted (lane venv with cache and `--no-cache`; all-extras venv `--no-cache`) | — |
| 2 | `ruff check dps tests tools` | All checks passed (the same three ways) | — |
| 3 | `mypy dps` | No issues in 150 source files: lane venv with its warm cache, lane venv with a **fresh** cache, all-extras venv with a fresh cache | 1 s / 5 s / 18 s |
| 4 | `DPS_TEST_REQUIRE_SERVICES=1 pytest -q`, whole suite, stack up, tesseract wrapper (3510 collected) | **3502 passed, 8 skipped, 0 failed** | 22:49 |
| 5 | `… -m "docs or ocr"` (M1) | **139 passed, 2 skipped** (need a host tesseract binary) | 0:15 |
| 6a | `… -m "db or pg_source or s3"` (M2), run 1 | **408 passed, 1 failed**: main's flaky three-way check, fixed on main (§2.1) | 8:41 |
| 6b | Same gate, full rerun after gate 8 | **409 passed, 0 failed** | 8:45 |
| 7 | `… -m "oracle or mongo"` (M3) | **162 passed, 1 skipped** (MongoDB has no composite keys, by design). The 6a test passed here. | 4:20 |
| 8 | `… -m browser` (M4), with `DPS_TEST_TESSERACT_CMD` set to the app image's tesseract (`docker run --rm -i --network none`) | **238 passed, 0 skipped**. Includes the 53 `browser and tesseract` tests (capture OCR with unmasked positive controls, including fix round 2's 24 geometry tests) and the 5 `test_no_submit.py` tests. | 14:24 |
| 9 | `cd frontend && npm ci && npm test && npm run build` | npm ci OK. Node tests **44/44**. Vitest: 27 files, **366 passed**. `tsc --noEmit && vite build` OK. `check-bundle`: no dev fixture code. | 0:23 |
| 10 | `DPS_SMOKE_SCANNER=1 DPS_SMOKE_REQUIRE_OCR=1 python tools/smoke_test.py --m3`, before the pytest gates | **All 52 checks passed.** Scan: 21 pages, 63 fields (51 personal), 9 risk flags, 4/4 parallel-view shots, 3 captures (3 masked), 60 POST + 1 GET blocked, `non_get_allowed` 0. M3 runs 3/3 at 20,000 rows; consistency 3/3. | 2:50 |
| 11 | Same smoke after all gates and the re-seed | **48/49**: the PG run of the M3 block failed closed with `leakage` at 9,000 rows. Every scanner and M2 check passed. (§2.2) | 2:43 |
| 12 | Same smoke again (smoke 3) | **All 52 checks passed** (same figures as gate 10; M3 runs 3/3 at 20,000 rows) | 2:57 |
| + | Fresh venv with every extra (programme plan §2.5 step 3): `uv pip install -e ".[ner,fpe,vertex,docs,db,connectors,uitest,e2e,scanner,tabular,synth-ml,dev]"`. This branch does have an `e2e` extra. | 233 packages. Static checks clean, as listed above. Extras tests (`test_mostly.py`, `test_mostly_linked.py`, `test_ner_presidio.py`, `test_playground_samples.py`): **92 passed, 2 skipped**. Both skips are the spaCy English model, which is installed nowhere. This includes the real MOSTLY train/generate tests. | 4:37 |

**Whole-suite skips (8):** the same 8 as in every earlier round, all environmental or by design.
- 1 MongoDB composite-key case.
- 2 tests that need tesseract on the host PATH. The other tesseract tests ran through the wrapper.
- 5 tests whose optional extras are not in the lane venv: pandas, mostlyai ×2, presidio, and the `[ner]` spaCy model. They pass in the all-extras venv.

### 2.1 Gate 6a — main's flaky three-way check, already fixed on main

**What failed:** `tests/e2e/test_m3_three_way.py::test_three_way_consistency_join`.
- The assertion was `('mongo', '$.customerId', same_row 0, anywhere 1) == (…, 0, 0)`, over 2,400 rows.
- All three jobs wrote their rows.
- The same test passed in gates 4, 6b and 7.

**The cause is main's test oracle, not the product:**
- `same_row` 0 means no document kept its own customer id.
- `anywhere` 1 is a target id equal to *another* source id. FF1 (`fpe_digits`) under a fresh random group key can produce that.
- Main diagnosed exactly this and fixed it in **`d2fdc3f`**. The message says: "the three-way e2e's MongoDB customer id (5.8 % of runs at 2 400)". The diff turns `"$.customerId": True` into `False`, which makes the check row-aligned only.

**Why it does not block:**
- `git diff 144cc65 m4 -- tests/e2e/test_m3_three_way.py` is empty. The merge into main therefore takes main's fixed version with no conflict.
- This is the same class as gates-r2 N1 (the Oracle query-subset check, fixed in the same commit).

### 2.2 Smoke 2 — main's strict per-row sweep matched by chance and failed closed

**What happened:** job `j_01M4YPD09E5MX92DEKJS9MC8TA` (`bank.customers` → `bank_uat.customers_smoke`, obfuscate, `verify: strict`).
- `error_code` `leakage`, partition 0 failed.
- `rows_written` 9,000: 9 batches of 1,000 passed. The 10th batch raised `LeakageError` and was not written (`batch.py`: "nothing is written").
- The evidence pack is `ev_01M4YPDBREXBJNANPE92V6DKGM`: `verification.status` failed, 64,529 values checked, `errors: [{code: leakage, where: partition 0}]`.
- The Mongo and Oracle runs of the same group succeeded at 20,000 rows each.
- Smoke 1 before it and smoke 3 after it each wrote this table in full, at 20,000 rows.

**Nothing leaked into the target** (`svt.py`, counts only).
- The 9,000 PG rows that were written align 9,000/9,000 on the 8 kept columns.
- **0 same-row survivors** in every non-kept column: customer_id, full_name, date_of_birth, national_id, tax_id, email, mobile, notes.

**Why the run failed — probe on m4's own code** (`probe/leak_probe.py`, counts only):
- **How the probe rebuilds the run:**
  - It uses this run's transformer exactly: the job's stored `RunPlan`, the accepted snapshot's shape, policy `gdpr-pseudonymise`, `en_IN`, anchor `customer_id`, `unclassified: detect`, and the worker's engine settings (FPE on, NER off, LLM stub).
  - It sends all 20,000 source rows through in 1,000-row batches, with `verify="warn"`, so the sweep reports its hits instead of raising.
- **Result: 640 fresh random keys, 16 processes.**
  - **2 keys (0.31 %) trip the sweep. Each trips once, and both are chance digit matches between different columns of one row:**
    - `$.mobile | CUSTOMER_ID | contained`: the row's 8-digit customer id appears inside the masked mobile number.
    - `$.customer_id | DATE_OF_BIRTH | contained`: the row's date of birth (`YYYYMMDD`) appears inside the masked customer id.
  - The other 638 keys produce **0** hits across all 20,000 rows.
  - The probe never saw a `fixed_point` or `name_part` hit. A real copy would trip under every key, so this is chance, not a copy.
- **How the sweep matches** (`verify_row`, `CONTAIN_MIN` 8): any original value of at least 8 normalised characters, found anywhere inside any transformed column of the same row, is a hit. Under `strict`, one hit fails the partition.
- Smoke 2's key was one of these rare keys. The key is not recoverable (D2), so I cannot name its exact hit.

**Not M4, and the same on main:**
- `git diff 144cc65 m4` is empty for `dps/engine/verify.py`, `dps/engine/transform/` and `dps/engine/batch.py`.
- Between `m4` and main:
  - `verify_row`, the thresholds `EXACT_MIN, CONTAIN_MIN = 4, 8`, `verify.py` and `transform/` are byte-identical.
  - Main's later `batch.py` changes (`5c11e49`, `d2fdc3f`) concern only synthesize-mode row-value spans.
- `probe/equiv_probe.py` ran main's code (a `git archive main dps` export) and m4's code on the same 20,000 rows under the same two derived test keys. They produced **identical output** (the same sha256 over every output row) and the same method counts.
- The thresholds date from 2026-10-12 (`7196fe9`, M2).

**Why it matters anyway** (see N1): the chance of a trip grows with the number of rows, because each row is one more chance for a coincidence.
- For this table: about 0.3 % of runs at 20,000 rows. From the measured per-row rate, roughly 1.5 % at 100k, about 15 % at 1M and about 80 % at 10M rows.
- This is a rough extrapolation from 2 events. Tables with longer digit columns (card numbers, IBANs) would trip more often.
- It is an availability defect, not a privacy one: the job fails closed.

## 3. Proofs (counts only)

### D4 (demo site `/__received`, a counting server)

- **Smokes:** in all three smokes every delta was **0** (non_get, form_get, logout_hits, admin_hits) and `by_path` did not change. Each smoke's own scan had `non_get_allowed` 0, with 60 POST + 1 GET blocked.
- **Whole suite and browser gate:** each moved the counters by exactly **+8 / +2 / +1 / +1**, with the same `by_path` split as in earlier rounds:
  - `POST /api/draft` ×4, `/api/autosave` ×1, `/apply/identity/submit` ×2 and `/api/telemetry/form-start` ×1.
  - These are the deliberate positive controls of `test_demo_site.py` and `test_no_submit.py`.
  - The guarded tests assert their own zero deltas, and they passed.
- **Nothing else moved the counters:** the M1 gate, both M2 runs, the M3 gate, the re-seeds and the probes changed nothing.
- **Running totals:** 72/16/9/8 → **88/20/11/10**.

### LLM exposure

`dps_llm_raw_pii_sent_total 0.0` before and after every smoke. Each smoke's own `m3 raw pii to llm` check also reported 0, except smoke 2, which stopped before that check.

### Masked captures (independent OCR, `ocr_probe.py`)

I OCR'd every stored PNG of smoke 2's scan (`s_01M4YP9TP1MFPTVNBS8DCETKJV`, synthesize mode) with the app image's tesseract (psm 11 and 3). That is 69 images: 21 original, 21 annotated, 12 + 12 filled and 3 masked captures.

- **Masked captures, 3 of 3 `captured`:** 0 personal demo values (name, e-mail, mobile, PAN, Aadhaar, card, account, customer id, address).
  - The only read is `application_ref` on page 25 (`/apply/review`). That is the known N9: not a detected entity, carried by the captures lens in rounds 1 and 2.
  - The same holds for `original` and `annotated`.
- **Positive control:** OCR reads the scan's own display-persona values in 11 of the 12 `filled_synthesize` shots (221 needle hits). The filled shots read 0 demo values.
- The browser gate's 53 capture-OCR tests add pages with unmasked positive controls.

### Source vs target (independent; `svt.py`)

**Smoke 3's three M3 `table` runs, read after the run:**

| Run | Rows | Alignment | Same-row survivors in non-kept columns | Value also found elsewhere in the source column |
|---|---|---|---|---|
| PG `bank.customers` → `bank_uat.customers_smoke` | 20,000 → 20,000 | 20,000/20,000 rows, on the 8 kept columns | **0** | customer_id 3 (FF1 crossings); date_of_birth 12,565 (`date_shift`: dates have a small domain) |
| Mongo `crm.profiles` → `crm_uat.profiles_smoke` | 20,000 → 20,000 | 20,000/20,000 documents, on `_id` | **0** | `$.customerId` 3; `$.profile.dob` 12,556 |
| Oracle `CRM.CUSTOMERS` → `CRM_MASKED.CUSTOMERS_SMOKE` | 20,000 → 20,000 | 20,000/20,000 rows, on `LAST_CONTACT_AT` | **0** | CUSTOMER_ID 3; DOB 12,565 |

**Smoke 2** (§2.2):
- The PG partial target: 9,000 rows, 0 same-row survivors.
- Mongo and Oracle at 20,000: 0 same-row survivors.

The methods match earlier rounds:
- names, tax ids and iban: `fpe_text`;
- e-mail: `email`;
- mobile: `phone`;
- national id, notes and address line: `det_token`;
- pin: `generalize`.

### Logs (`log_scan.py`, all 15 compose services)

**Run window (since 06:55 IST):**
- 0 tracebacks.
- **0 hits** of the demo site's customer values (normalised), the demo session cookie, the compose demo API-key literal, PEM private-key markers or key-material field names.
- **App:** no error-level lines. Warnings:
  - 196 `rate_limited`: console tests, the known N3. 76 came from the whole suite and 120 from the marker gates. That is fewer than the 240 of round 2.
  - 3 `auth_rejected`: each smoke's deliberate check.
- **Worker and browser-worker:** no warnings or errors.
- **Database error lines:** only errors that negative tests provoke on purpose:
  - check and unique constraints, RLS;
  - permission denied on audit/evidence/policy/scan tables;
  - append-only and immutability triggers;
  - wrong-password logins, value too long, a read-only transaction, a statement timeout.

**Full container logs:**
- One extra "hit": a 7-digit `customer_id` needle in Mongo's log from **04:30 IST**, which is before this run.
- `hit_locate.py` shows it is an artefact of the scan. The scan concatenates every digit of a line, and the needle formed across separate numbers (a timestamp and `elapsedMillis`) of a Mongo internal message.
- There is no contiguous match and no match of the value's text form.

### Commits

`main..m4` holds 64 commits.
- All 64 are authored and committed by `NilenduGanguli <nilendu.ganguli@gmail.com>`.
- **0 trailers** (no `Co-Authored-By` or `Signed-off-by`).
- `origin/m4` = HEAD = `cbbac38`.

## 4. Notes

- **N1 — NEW, for main (not M4): the strict per-row leakage sweep fails runs on chance digit matches (§2.2).**
  - **Where:** `dps/engine/batch.py::verify_row`. Its `contained` rule compares every original of at least 8 normalised characters with every transformed column of the same row, so a masked mobile or masked id that happens to contain the row's own customer id or birth date is a "leak".
  - **Impact:** the run fails closed, so there is no privacy loss. But it is an availability defect that scales with rows: about 0.3 % of runs at 20k, and roughly 15 % at 1M rows for this table.
  - **Suggested owner:** main / F1 or F4. Options:
    - compare digit-only needles only with the columns of the same entity, or as whole tokens;
    - re-derive a colliding output;
    - or document the rate.
  - Do not relax `strict`.
  - It must be fixed on main; fixing it on `m4` would fork the engine.
- **N2 — main has moved (carried from round 2).**
  - Main has `5c11e49` and `d2fdc3f` (M3 post-merge r1/r2); `m4` does not.
  - A trial `git merge-tree --write-tree m4 main` (no ref or file written) still finds **1 textual conflict**: `docs/superpowers/plans/2026-10-11-m1-m5-contract.md`.
  - These 6 files auto-merge: `HANDOFF.md`, `jobs.py`, `evidence.py`, `Admin.tsx`, `EvidencePack.tsx` and `EvidencePack.test.tsx`.
  - The post-merge gates on main must cover those two commits. They include product changes to `batch.py` (synthesize row-value spans) and `jobs_run.py` (ledger clearing). They must also show gate 6a's test green under main's row-aligned check.
- **N3 — known: the console tests exceed the per-key rate limit.** 196 `rate_limited` warnings. The tests pass. Pace the walks, or check how the console shows a 429 (F4).
- **N4 — known (A-N2): the console tests write the 54 tracked PNGs in `m3-shots/`.** I restored them as described above. Fix suggested for F4: a `tmp_path` or env override for `SHOTS`.
- **N5 — known:**
  - `npm ci` reports 8 advisories (5 moderate, 1 high, 2 critical), the same as main.
  - Vite warns that the main chunk is 644.8 kB (its warning limit is 500 kB).
  - Every pytest run shows one `StarletteDeprecationWarning`.
  - The whole-suite output contains one `key_refresh_failed … gaierror` warning line. A test app's database-key refresh loop prints it against an unresolvable host. It is test noise, also present in fix round 2.
- **N6 — cosmetic:**
  - The lane's dps-db still holds `dps_test_85735`, a session database left by an earlier run.
  - The one-shots built on the app image (`migrate`, `demo-secrets`, `seed-sources`) show Docker health `unhealthy` after a clean `exit 0`.
- **N7 — my log scan's digit concatenation can join separate numbers.** That is the Mongo artefact above. A future scan should match contiguous digit runs.

## 5. State left for the lens verifiers

- **Stack:** up with the five profiles (`docs sources oracle scanner tokenizer`). It is healthy, `/readyz` reports ready, and the browser worker is online.
  - Any `docker compose up` here reads `.env`.
  - `ps`, `logs` and `exec` need no extra variable.
- **Demo sources:** demo size, model 2:
  - PG `bank`: 20,000 customers;
  - Oracle `CRM.*`;
  - Mongo `crm.profiles`: 20,000.
- **Left by the smokes:**
  - three more succeeded scans of the demo site under the demo session ref;
  - smoke groups `smoke-m3-<epoch>`, one per smoke;
  - smoke 2's failed PG run `j_01M4YPD09E5MX92DEKJS9MC8TA`, with its evidence pack;
  - `bank_uat.customers_smoke`, `crm_uat.profiles_smoke` and `CRM_MASKED.CUSTOMERS_SMOKE`, rewritten in full by smoke 3 and compared in §3.
  - The Reports page will show the failed PG run in its leakage figures.
- **Demo-site counters:** 88/20/11/10, all from the deliberate test controls. Take a fresh baseline before a D4 probe.
- **Tools:**
  - The tesseract wrapper is at `~/.cache/dps-build/s2-gates-r3/tess.sh`.
  - `./lane.sh python ~/.cache/dps-build/s2-gates-r3/svt.py <out.json>` compares the newest run into each smoke target (counts only).
  - `./lane.sh python ~/.cache/dps-build/s2-gates-r3/ocr_probe.py [scan_id]` OCRs a scan's stored screenshots (counts only).
  - The N1 probes are in `s2-gates-r3/probe/`.
- **Worktree:** clean at `cbbac38`.
