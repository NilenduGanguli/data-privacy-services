# FINAL — v1.0.0 candidate on `main` — GATES verifier, round 2

**Verdict: PASS. No blocking finding.** M5 Definition of Done 1–7 passed on HEAD `d033dd3`, along with
the extra gates round 1 also ran (DoD 9, 10, 11-check, 12-facts, 13). Round 1's blocker **B1 is fixed
live**:
- The demo's S6 runs under `@retail-kyc` succeed on all three engines (3 × 20,000 rows), and their
  evidence verifies.
- The demo completes S1–S12 in **3:02**.
- The 9 console-page tests pass after it.

Every proof is clean, on counts only:
- D4 is +0 outside the suite's deliberate controls.
- `dps_llm_raw_pii_sent_total` is 0 after every gate.
- Source vs target shows 0 same-row survivors.
- Masked-capture OCR finds only the known N9.
- All 15 containers' logs hold 0 demo or seeded source values.

| | |
|---|---|
| Date | 2026-10-16, 11:40–12:30 IST (chain 11:45:48–12:25:58) |
| Tree verified | `/Users/neelu/document_intelligence/data-privacy-services`, `main` @ **`d033dd3`** (= `origin/main`); round 1 verified `45be8ac`; since then: `c7ad14f` (fix round: finalize opens the sealed policy, Mongo targets, FK parents, stopword surnames, sampled keys, T3 egress) and `d033dd3` (deck + HANDOFF T3 paragraph) |
| Tools | `.venv`: Python 3.12.13, ruff 0.17.0, mypy 2.4.0, pytest 9.1.1, playwright 1.55.0, oracledb 26.0.0, pymongo 4.18.3. Node 22.20.0 / npm 10.9.3. kustomize v5.8.1, kubeconform v0.8.0 |
| Endpoints | contract §12.2 defaults (checked against the contract table): app 8400, dps-db 5441, source-pg 5442, MinIO 9010/9011, Oracle 1522, Mongo 27018, OCR 5016, demo site 8410 |
| Work dir | `~/.cache/dps-build/final-gates-r2/`: scripts (adapted from round 1's: `run_all.sh`, `chain.sh`, `final.sh`, `static_gate.sh`, `frontend_gate.sh`, `side_gates.sh`, `identity.sh`, `post.sh`, `svt*.py`, `ocr_probe.py`, `log_scan*.py`, `junit_sum.py`, plus `collect_markers.sh` and `marker_map.py`); `logs/gates/` (one log per gate with D4, LLM and test-DB snapshots before and after, `chain-summary.log`, `pytest-whole.xml`); `logs/` (static, frontend, side gates, identity, svt, OCR, marker map, log scans, end state, foreign watch) |
| Files modified | **None tracked.** `git status` is the same at start and end: only the untracked `docs/screenshots/v1.0.0/`, another verifier's, which I left untouched. 0 tracked changes, and the set of ignored paths is unchanged. The gates refreshed their usual ignored outputs: `reports/flow-report.md`, `reports/e2e-demo-report.{md,json}`, `reports/demo-screens/*.png` (18), `var/demo/demo-keys.json`, `frontend/node_modules`, `frontend/dist`. No test wrote a tracked screenshot; console shots go to the git-ignored `reports/`. |
| Resume | No `FINAL-gates-r2.md` and no r2 work dir existed, so every gate is a first run. Round 1's report and the fix report were read first. |
| Values | Only counts, ids, column names, methods and file names appear here and in the logs. No customer value, key or secret appears. |

## 1. Stack (left running for the lens verifiers)

- **Bring-up.** At the start every container was stopped (Docker Desktop restart).
  `docker compose --profile full up --build -d` exited 0 in 22 s:
  - every build step came from cache (layers from the fix round's 11:03 build of `c7ad14f`);
  - `d033dd3` changed only `HANDOFF.md` and the `.pptx`, both outside the image's COPY set;
  - the app, worker, browser-worker, tokenizer-mock, OCR mock and the one-shots were recreated.
- **`/readyz`** was polled with a bounded loop (never `--wait`).
  - On the first poll: `ready: true`, with all 9 components DoD 3 names `ok`: api-core (1.0.0), db
    (migrations verified), jobs, llm-gateway (stub, `coin_tls=verified`), ocr (azure-read-v3.2), fpe
    (ff1), browser (1 online), tokenizer (http), keys (db+static).
  - `ner` is patterns-only, by design.
- **Code identity** (`identity.sh`, at start and end; `logs/11-`, `logs/94-`):
  - app and worker: 206/206 files (`dps/`, `tools/`, `pyproject.toml`, `README.md`) have the tree's
    sha256;
  - browser-worker: 184/184 (it has no `tools/`, by design);
  - the served console bundle (`index-DHVfp0FH.js`, `index-DCSeEcP4.css`) is byte-identical to the host
    build;
  - browser-worker runs as uid 10001 with playwright 1.55.0 and Chromium 1187.
- **End state (12:25):**
  - `ready: true`; 11 services healthy, 0 restarts, 0 OOM kills; the four one-shots exited 0;
  - no `dps_test_*`/`dps_fresh_*` database left, before or after any gate;
  - Oracle at 77 % of its memory limit, Mongo at 43 %.
- **State left for the lenses:**
  - sources at 20,000 rows, seed 7 (`demo_seed.py` reloaded Oracle and Mongo after the suite);
  - demo tenant `retail-banking-apac` seeded, with this run's succeeded S6 runs, S8 copula run, S9 scan
    and packs;
  - the smoke's scan `s_01M51NMM49V3JFQF0J51V1YPM9` in the default tenant.

## 2. Gates

The stack gates ran **one at a time** (`run_all.sh` → `chain.sh`), because the suites share the demo
sources, bucket, rate limit and D4 counters:
1. seed → smoke → smoke source-vs-target → demo seed;
2. whole suite → M1 → tesseract → DoD 9 → flow report;
3. demo seed → e2e demo → console pages → demo source-vs-target.

The static, frontend and side gates ran beside the chain; none of them uses the stack. The frontend
build finished before any pytest started.

| # | Gate (DoD) | Result | Time |
|---|---|---|---|
| 1 | `ruff format --check dps tests tools` (1) | 515 files already formatted; same with `--no-cache` | 0 s |
| 2 | `ruff check dps tests tools` (1) | All checks passed; same with `--no-cache` | 0 s |
| 3 | `mypy dps` (1) | No issues in 155 source files, warm cache and **fresh** cache | 0 s / 7 s |
| 4 | `DPS_TEST_REQUIRE_SERVICES=1 pytest -q`, whole suite, with `DPS_TEST_TESSERACT_CMD` (the app image's tesseract, `--network none`) and `DPS_TEST_IMAGE`/`DPS_TEST_BROWSER_IMAGE` = the `:local` images (1, 4) | **4804 passed, 11 skipped, 0 failed** (4815 junit cases; skips in §3) | 31:32 |
| 5 | `… -m "docs or ocr"`, run on its own (4, M1) | **139 passed**, 3 skipped (2 host-tesseract → 5b; the deck module) | 0:17 |
| 5b | `tests/documents/test_ocr_tesseract.py`, wrapper on `PATH`, `DPS_TEST_REQUIRE_TESSERACT=1` | **8 passed** | 0:05 |
| 6 | DoD 4 `-m "db or pg_source or s3"` / `-m "oracle or mongo"` / `-m browser` / `-m slow` (incl. `tests/connectors/test_resume_after_kill.py`) | **Mapped from gate 4's junit at node-id level (§4):** 493/494 passed + 1 by-design skip; 198/199 + 1 by-design skip; **329/329**; **38/38** (resume-after-kill 6/6). Every selected test ran in gate 4 with `DPS_TEST_REQUIRE_SERVICES=1`. | — |
| 7 | `… -m deploy`, run on its own (4, 10) | **62 passed**, 1 skipped (the deck module) | 0:19 |
| 8 | `cd frontend && npm ci && npm run typecheck && npm test && npm run build` (2) | npm ci: 101 packages. `tsc --noEmit` clean. Node tests **46/46**. Vitest: 34 files, **418 passed**. `vite build` OK. `check-bundle`: no dev fixture code (6 markers). | 0:15 |
| 9 | `DPS_SMOKE_REQUIRE_OCR=1 DPS_SMOKE_REQUIRE_M5=1 DPS_SMOKE_SCANNER=on python tools/smoke_test.py --m3`, after `seed_sources.py --target all --rows 20000 --seed 7` (5) | **All 60 checks passed, 0 SKIP, 0 FAIL.** M5 block 8/8 present: policies with hashes; reports summary (`raw_pii_sent_to_llm` 0); posture report (5/11 pass, local); db key lifecycle; db key disabled (401); external tokenization round trip; 422 never echoes input; `Cache-Control: no-store`. Details below. | 2:52 |
| 10 | `python tools/flow_report.py --require-scanner` (6) | Exit 0. **92 calls, 92 as expected, 0 skipped, 0 unexpected**, in all 16 route groups (ops, Layer 1, policies, documents, schema synthesis, taxonomy, connections, jobs, snapshots, job groups, evidence, artefacts, reports, policy store, admin, UI scanner 10/10). | 0:16 |
| 11 | `python tools/demo_seed.py && python tools/e2e_demo.py --console` (7) | **PASS, S1–S12 all PASS, "demo flow completed in 3:02"** (under 10:00). 18 screenshots in `reports/demo-screens/` (9 pages × light/dark), all written in this run. The report records commit `d033dd3`, version 1.0.0. Steps below. | 0:15 + 3:03 |
| 12 | `DPS_TEST_REQUIRE_SERVICES=1 pytest -q -m browser tests/console/test_console_pages.py` after the demo (4) | **9 passed**. They also passed inside gate 4, against the fix round's demo state on this stack. | 0:07 |
| 13 | `DPS_TEST_REQUIRE_SERVICES=1 pytest -q tests/security tests/test_no_values_in_logs_m{1..5}.py` (9) | **459 passed** | 1:11 |
| + | `kustomize build deploy/openshift/overlays/{dev,prod}` + `kubeconform -summary -ignore-missing-schemas` (10) | dev: 33 resources (30 valid, 3 skipped); prod: 26 (23 valid, 3 skipped). **0 invalid, 0 errors.** | — |
| + | `python tools/settings_table.py --check` (11) | current (186 settings) | — |
| + | `git ls-files \| xargs grep -nE "AKIA…\|BEGIN (RSA \|EC )?PRIVATE KEY"` (9) | 4 hits, all AWS's **documented example key** `AKIA…(AWS documentation example, redacted)` used as detector test input (`tests/test_patterns.py`, `tests/test_surrogates.py`, the M0 plan text). 0 PEM markers. Same as round 1 (N1). | — |
| + | `tests/test_mostly.py tests/test_mostly_linked.py` in the all-extras venv (`s1-m4-gates-r1/venv-all`; editable install of this checkout, `dps` 1.0.0 imported from it) (1, 13) | **24 passed**, including the 3 cases that skip in `.venv` (pandas ×1; mostlyai ×2: DP and linked generation) | 3:06 |
| + | `tests/test_deck.py`, with python-pptx 1.0.2 + XlsxWriter 3.2.9 from round 1's private `--target` dir (11) | **43 passed** | 0:05 |
| + | Release facts (12) | Version 1.0.0 in `pyproject.toml`, `dps/__init__.py`, `frontend/package.json` and both `package-lock.json` fields. No `v1.0.0` tag, correctly: it waits for this verdict. | — |

**Smoke details (gate 9):**
- Layer 1 and documents/OCR: the redacted PDF has 0 text characters and `ocr_checked`; the scanned PDF
  and the image went through `azure-read-v3.2`.
- Scanner:
  - 21 pages, 63 fields (51 personal), 9 risk flags, 0 expected flag kinds missing;
  - D4: non_get +0, form_get +0, logout +0, admin +0; 60 non-GET blocked; `non_get_allowed` 0;
  - 4/4 parallel-view shots; 3/3 captures masked;
  - persona export: 0 demo values.
- M2 9/9: 20,000 rows, parquet to MinIO, dry run, workers online.
- M3 7/7: runs 3/3 succeeded at 20,000/20,000/20,000; consistency 3/3 (500 sampled); evidence 3/3
  verified under the group's key id; raw PII to LLM 0.

**Demo details (gate 11, `reports/e2e-demo-report.json`):**
- S1: ready, 9/10 components (ner off by design); raw PII to LLM 0.
- S2: 12 findings; reveal identical; the synthetic output carries 0 of 11 detected values.
- S3: tokenization round trip.
- S4: 2 OCR pages, 14 findings, 0 characters, 0 originals.
- S5: three engines discovered and accepted (sensitive/columns: PG 8/16, Mongo 10/17, Oracle 11/16).
- **S6:** three runs under the group key, 20,000 rows each, evidence verified (round 1's B1).
- S7: 3/3 pairs consistent.
- **S8** (copula):
  - fidelity 0.9712, exact-row matches 0, NNDR 1.5196, 20,000 rows;
  - the pack shows `privacy.membership_inference.run: true` (AUC 0.5035) and `utility.run: true`
    (accuracy ratio 0.9844). This satisfies DoD 13's copula item.
- S9: 25 pages, 12 with both fills; D4 +0/+0/+0/+0; 60 sends blocked.
- S10: 5 packs, PDF ok, 32 audit rows, 0 values.
- S11: posture 5/11 (local), rotated key 401, retention succeeded.
- S12: 9 pages × 2 themes, 18 screenshots, 0 console errors.

**DoD 4 and 13 named suites** (junit of gate 4):
- `tests/connectors/test_synthesize_copula.py` 8 passed; `tests/security/test_cross_tenant.py` 1;
  `tests/console/test_console_pages.py` 9.
- LLM suites: `tests/test_llm_label_l2_l3.py` 27, `tests/test_llm_egress.py` 22,
  `tests/test_llm_egress_t3_collisions.py` 8.
- Image and demo suites: `tests/test_browser_image_runtime.py` 3, `tests/test_e2e_demo.py` 35,
  `tests/test_demo_seed.py` 10, `tests/scanner/test_capture_ocr.py` 33, `tests/test_handoff.py` 10.
- The fix round's new suites:
  - `tests/test_finalize_sealed_policy.py` 4;
  - `tests/connectors/test_run_sealed_policy.py` 4;
  - `tests/connectors/test_stopword_surnames.py` 2;
  - `tests/connectors/test_resume_after_kill.py` 6.
- Every new LLM test fakes the provider at `LlmGateway._stellar` (the seam), never by overriding
  `complete()`.
- `/metrics`: `dps_llm_raw_pii_sent_by_tenant_total` has **no series** while the total is 0, which is
  what DoD 13 asks for.

## 3. Whole-suite skips (11), every one accounted for

| Skips | Reason | Covered by |
|---|---|---|
| 1 | `test_deck.py` collection: no `pptx` in `.venv` | gate "deck", 43 passed |
| 2 | `test_ocr_tesseract.py`: no tesseract on the host `PATH` | gate 5b, 8 passed |
| 3 | pandas ×1, mostlyai ×2 (`ml`, the synth-ml extra) | extras venv, 24 passed |
| 3 | no spaCy English model (`test_ner_presidio.py`, `test_playground_samples.py`, `test_schema_profile.py:827`) | accepted skip ("`ner` without the extra", HANDOFF §12); the model is installed nowhere, as in every earlier round |
| 2 | conformance, by design: MongoDB has no composite keys; a key-less PostgreSQL table is not read | by design |

Round 1 had 20 skips. The 9 console-page skips are gone, because the demo tenant now has finished runs,
a scan and a pack.

## 4. DoD 4 markers: how they were run (deviation from the literal list, owner's request for pace)

- **Run on their own**, with the literal command and `DPS_TEST_REQUIRE_SERVICES=1`: `"docs or ocr"`
  (139 passed), `deploy` (62 passed), DoD 9 (459 passed), the console pages after the demo (9 passed),
  and the tesseract-on-PATH file (8 passed).
- **Mapped from the whole suite** (gate 4, run once with `DPS_TEST_REQUIRE_SERVICES=1` and a superset
  environment): `"db or pg_source or s3"`, `"oracle or mongo"`, `browser`, `slow`. Method:
  - collect each marker expression's node ids (`collect_markers.sh`: `pytest --collect-only -m …`; no
    test runs);
  - map every id onto `pytest-whole.xml` (`marker_map.py`, `logs/50-marker-map.txt`).

| Set | Collected | Passed | Skipped | Failed | Not in junit |
|---|---|---|---|---|---|
| whole suite | 4814 (+1 deck module) | 4804 | 10 (+1) | 0 | 0 |
| `docs or ocr` | 141 | 139 | 2 (host tesseract → 5b) | 0 | 0 |
| `db or pg_source or s3` | 494 | 493 | 1 (key-less PG, by design) | 0 | 0 |
| `oracle or mongo` | 199 | 198 | 1 (Mongo composite key, by design) | 0 | 0 |
| `browser` | 329 | **329** | 0 | 0 | 0 |
| `deploy` | 62 | 62 | 0 | 0 | 0 |
| `slow` | 38 | 38 | 0 | 0 | 0 |
| DoD 9 set | 459 | 459 | 0 | 0 | 0 |

- **Why.** The owner asked for pace. Re-running those four marker sets on their own would repeat
  ~37 minutes of tests that had already passed in gate 4 on the same stack and code.
- **What this leaves unproven:** only isolation-order effects, i.e. a test that passes in the whole
  suite but not alone. Round 1 ran both forms on `45be8ac` and found them identical. The fix round ran
  the M2, M3 and browser sets on their own on `c7ad14f`: 430 / 186 / 228 + 138 tesseract-gated, 0
  failed.
- If the release owner wants the literal four runs anyway, they take ~37 min on this stack; the
  commands are in `chain.sh` (steps `m2`, `slow`, `m3`, `m4`).

## 5. Proofs (counts only)

**D4: the demo site's `/__received`, a counting server.**
- Start: 0/0/0/0.
- Every gate moved it by **+0** except the whole suite:
  - smoke (its own check also shows non_get +0, with 60 non-GET blocked), demo S9, flow report,
    console pages;
  - the two leftover scans of §6 (`logs/03-d4-after-leftover-scans.json`).
- The whole suite moved it by **+8 non_get / +2 form_get / +1 logout / +1 admin**. These are the
  deliberate positive controls of `test_demo_site.py` / `test_no_submit.py`: `POST /api/draft` ×4,
  `/apply/identity/submit` ×2, `/api/autosave` ×1, `/api/telemetry/form-start` ×1. This is the same
  split as round 1 and every earlier round.
- End: 8/2/1/1.

**LLM exposure.** `dps_llm_raw_pii_sent_total 0.0` before and after every gate (13 snapshots). The
smoke's `m3 raw pii to llm`, the reports summary and demo S1 also report 0.

**Source vs target** (`svt.py` / `svt_demo.py`; reads source and target in full; `logs/svt-*.json`):

| Runs | PG `bank.customers` → `bank_uat.customers_*` | Mongo `crm.profiles` → `crm_uat.profiles_*` | Oracle `CRM.CUSTOMERS` → `CRM_MASKED.CUSTOMERS_*` | Same-row survivors in non-kept columns |
|---|---|---|---|---|
| smoke M3 (`*_smoke`), read right after the smoke | 20,000/20,000 aligned on 8 kept columns | 20,000/20,000 on `_id` | 20,000/20,000 on `LAST_CONTACT_AT` | **0** in every engine |
| demo S6 (`*_demo`, `@retail-kyc`, succeeded) | 20,000/20,000 | 20,000/20,000 | 20,000/20,000 | **0** in every engine |

Values also found elsewhere in the same source column ("set hits") are the expected ones:
- customer id 2–5 per engine (FF1 crossings);
- date of birth 12,602–12,768 (`date_shift` over a small domain).

**Masked captures** (independent OCR, `ocr_probe.py`: the app image's tesseract with `--network none`,
psm 11 + 3). Smoke scan `s_01M51NMM49V3JFQF0J51V1YPM9`: 25 pages, 69 images.
- Masked captures: 3/3 `captured`. The only demo value read is `application_ref` on page 25
  (`/apply/review`). That is M4's known N9 (not a detected entity; HANDOFF records it), and it is also
  read in the original and annotated shots.
- Filled shots: **0** demo values.
- Positive control: OCR reads the scan's own display-persona values in 11/12 `filled_synthesize`
  shots (174 hits) and 4/12 `filled_obfuscate` shots (12 hits).

**Logs** (all 15 containers, since 06:10Z, the start of this stack; `logs/95-`, `logs/96-`):
- **0** demo-site values, **0** PEM private-key markers, **0** key-material field names, **0**
  tracebacks.
- **0** seeded source values. Needles: 129,792 values from PG `bank.customers`, Oracle `CRM.CUSTOMERS`
  and Mongo `crm.profiles` (20,000 e-mails, 55,565 names, 54,227 digit forms). The self-test found
  4/4.
- App, worker and browser-worker: **0 error-level events**. Warnings:
  - 110 `rate_limited`, from the console tests (round 1's known N3);
  - 7 `auth_rejected`, from deliberate 401/403 checks in the smoke, the flow report and demo S11.
- Worker: **0** `finalize_failed` (round 1's B1 symptom) and 0 asyncio teardown traces (round 1's N2
  did not recur).
- Database error lines: only the kinds negative tests provoke on purpose:
  - append-only and immutability triggers, permission denied, row-level security;
  - check and unique constraints, wrong-password logins;
  - on source-pg: a read-only transaction, a statement timeout, value too long.

**Test databases.** No `dps_test_*`/`dps_fresh_*` database before or after any gate; each session
dropped its own.

**Foreign runs** (`watch_foreign.sh`, every 20 s, 278 lines):
- 274 lines are connections from the main checkout.
- The 4 lines with no resolvable root are all mine: the two killed smokes of §6, the resume-after-kill
  test's worker subprocess (cwd in its pytest temp dir) and the suite's headless Chromium.
- No `dps-wt-*` worktree connected, and no other compose project ran.

**Commits** `8ea220a..HEAD` (130):
- all authored and committed by NilenduGanguli &lt;nilendu.ganguli@gmail.com&gt;;
- **0** `Co-Authored-By`/`Signed-off-by` trailers;
- 0 conflict markers in tracked text files.

## 6. Run notes and deviations

1. **Clean stack.** HANDOFF §13 and the M5 plan (T28 step 2) run the release gates from a clean stack
   (`down -v`). This verification did not reset the volumes:
   - the task said to bring the stack up, and asked to leave it for the lenses;
   - round 1 ran on the release step's clean stack (`down -v`, 08:18);
   - the images here are code-identical to HEAD (§1);
   - `c7ad14f` changed no tool or migration.

   **Residual:** a first-run-only path on an empty metadata DB was not re-exercised on `c7ad14f`. One
   example is the FK join label taken from the referenced column's name when no parent snapshot exists
   (`_reference_label`). That path has unit coverage, and on this stack the demo tenant took it the
   same way. If the owner wants the literal clean-stack DoD before tagging:
   `docker compose --profile full down -v && docker compose --profile full up --build -d`, then
   `run_all.sh` (~45 min).
2. **DoD 4 markers mapped, not re-run** (§4).
3. **A false start, with no effect on any result.** I first started the chain under a tool timeout,
   then stopped it at 11:44 to relaunch it detached.
   - The kill missed one child, a smoke that had just started (attempt 0). The relaunched chain's smoke
     (attempt 1) ran beside it for ~30 s.
   - I stopped both. Each had only submitted its scan (`s_01M51NHQVM7KV9PFWZ6JFRS05M`,
     `s_01M51NHWSC0PEKG5AK65W8R7MX`); they reached no M2/M3 run.
   - I let both scans finish (succeeded at 11:45:41; D4 still 0/0/0/0), then restarted the whole chain
     from the readyz poll at 11:45:48.
   - Every result above comes from that clean third chain. The attempt logs are archived under
     `logs/gates/attempt1/` and `*.attempt0.log`.

## 7. Non-blocking notes

- **N1** (round 1, unchanged) The DoD 9 secret grep is not literally empty: it hits AWS's documented
  example key in two tests and the M0 plan. It is not a credential, and autopush's gitleaks gate is
  clean. Either reword the gate or accept it explicitly.
- **N2** The **deck** (`docs/demo/dps-showcase.pptx`, slide 1: "3:01", `c7ad14f`) was built from the
  fix round's rehearsal. This run's `reports/e2e-demo-report.*` and `reports/demo-screens/` are newer
  (3:02, `d033dd3`); both are git-ignored outputs. The deck is still consistent with a passing
  rehearsal. Its Keynote PDF review is the owner's (DoD 11).
- **N3** `rate_limited` warnings (110) from the console tests on the shared bootstrap key: known since
  earlier rounds and harmless (the tests pass). A separate test key or a higher local limit would
  silence them.
- **N4** (round 1's N4, now moot) The HANDOFF gate list still runs the browser gate before the demo. On
  a truly fresh stack the 9 console-page tests would skip there. Keep the post-demo re-run (gate 12) in
  the procedure, or state the order.
- **N5** `.venv` still lacks the `deck` and `synth-ml` extras and a spaCy model, so 1 + 3 + 3 skips
  recur in every whole-suite run. Each is covered by a separate run (deck, extras) or accepted
  (`ner`).
- **N6** The posture report reads 5/11 on the dev stack, as expected for local posture; the smoke checks
  only that it is present.
- **N9** (M4, open) `application_ref` stays readable in the masked capture of `/apply/review`. It is
  documented in HANDOFF and is not a detected entity.

## 8. For the lens verifiers and the owner

- The stack is **running**, in the state of §1:
  - every service healthy;
  - sources at 20,000 rows (seed 7);
  - the demo tenant seeded, with this run's succeeded S6/S8 runs, the S9 scan and 5 packs;
  - `reports/demo-screens/` fresh (18 shots), for the mockup walk of DoD 8.
- No tag was created. If the panel agrees, the tag goes on `d033dd3`: `git tag -a v1.0.0`. Also
  `docker tag data-privacy-services:local data-privacy-services:1.0.0`, and the same for the browser
  image.
- Owner decisions carried from `release.md`, unchanged by this run:
  - the joined production-write rule;
  - S3c deny-list CPU;
  - the LLM free-text rewrite fallback;
  - the deck's Keynote PDF review.
