# FINAL — v1.0.0 candidate on `main` — GATES verifier, round 1

**Verdict: FAIL. One blocking defect (B1).** Everything else is green on the first run, with no reruns:
- the static gates;
- the whole suite with `DPS_TEST_REQUIRE_SERVICES=1`;
- every integration marker;
- the frontend;
- the smoke (60/60, M5 block present, 0 SKIP);
- the flow report (92/92).

DoD 7, the ten-minute demo, fails at S6. All three engine runs (PostgreSQL, MongoDB and Oracle) write their 20,000 rows. Then each one dies in `finalize` with `finalize_error` / `PrepareFailure`, so no evidence pack is produced. The demo stops at 3:39 with S7–S12 not run.

**Root cause:**
- `_finalize` reads the redacted row spec.
- Since fsec's F-18 (`7c3fd5c`), that row holds only a sealed policy *label* for any inline or `@name` policy.
- `_policy()` therefore raises `PrepareFailure("policy_invalid")`.
- `_best_effort` does not catch that exception, so the job retries until it is dead.

**Impact:** every L2 run that uses a stored (`@name`) or inline policy is affected.
- The smoke's runs and the test suites pass because they use preset policies (`spec.policy` is `None`).
- The 9 console-page tests of DoD 4 stay skipped for the same reason: their documented prerequisite is a finished demo run, scan and pack.

| | |
|---|---|
| Date | 2026-10-16, 08:23–09:58 IST |
| Tree verified | `/Users/neelu/document_intelligence/data-privacy-services`, `main` @ **`45be8ac`** (`chore(release): v1.0.0`) = `origin/main`. Clean at the start and at the end; no merge in progress; all ten finish branches (`fsec fsecc fimg fos fdemo fdocs fllm fpersona fspec fidcheck`) are ancestors of HEAD. |
| Tools | `.venv`: Python 3.12.13, ruff 0.17.0, mypy 2.4.0, pytest 9.1.1, playwright 1.55.0, oracledb 26.0.0, pymongo 4.18.3. Node 22.20.0 / npm 10.9.3. kustomize v5.8.1, kubeconform v0.8.0. |
| Endpoints | Contract §12.2 defaults (`~/.cache/dps-build/final-gates-r1/env.sh`): app 8400, dps-db 5441, source-pg 5442, MinIO 9010/9011, Oracle 1522, Mongo 27018, OCR 5016, demo site 8410, tokenizer-mock 8420 |
| Work dir | `~/.cache/dps-build/final-gates-r1/`: `logs/gates/` (one log per gate, plus D4, LLM and test-DB snapshots before and after each; `chain-summary.log`; `pytest-whole.xml`), `logs/` (static, frontend, side gates, identity, svt, OCR, log scans, end state, foreign watch), scripts (`run_all.sh`, `chain.sh`, `final.sh`, `static_gate.sh`, `frontend_gate.sh`, `side_gates.sh`, `identity.sh`, `post.sh`, `svt*.py`, `ocr_probe.py`, `log_scan*.py`, `junit_sum.py`) |
| Files modified | **None in the repo**: `git status` was empty before and after, and HEAD is unchanged. The set of git-ignored paths is unchanged. The gates refreshed their usual ignored outputs: `reports/flow-report.md`, `reports/e2e-demo-report.{md,json}`, `var/demo/demo-keys.json` + group file, `frontend/node_modules`, `frontend/dist`. The console tests did not rewrite any tracked screenshot this time (0 restored). |
| Resume | No earlier attempt (no `FINAL-gates-r1.md`, no work dir). Every gate is a first run. |
| Values | Only counts, ids, column names, methods and file names appear here and in the logs. No customer value, key or secret is printed. |

## 1. Stack (left running for the lens verifiers)

- The release step had just rebuilt the `full` stack from scratch (`down -v`, then `up --build -d`); the containers were created at 08:18:50 IST.
  - I did **not** rebuild it, because the tree has not changed since. I also did not re-run `up`, which would restart the four one-shots.
  - All 11 long-running services were healthy. `migrate`, `minio-init`, `demo-secrets` and `seed-sources` had exited 0.
- **`/readyz`** reported `ready: true` at the start and at the end (09:50). Components:
  - api-core up, version 1.0.0; db up, migrations verified; jobs ready; keys db+static;
  - llm-gateway stub, `coin_tls=verified`; ocr azure-read-v3.2; fpe ff1; tokenizer up (http); browser 1 online;
  - `ner` patterns-only, by design.
  - All 9 components that DoD 3 names are `ok`.
- **Code identity** (`identity.sh`, at the start and at the end; `logs/11-`, `logs/94-`):
  - `app` and `worker`: 206/206 files (`dps/`, `tools/`, `pyproject.toml`, `README.md`) have the same sha256 as the tree.
  - `browser-worker`: 184/184 files match; it has no `tools/`, by design.
  - The served console bundle (`index-DHVfp0FH.js`, `index-DCSeEcP4.css`) is byte-identical to my host build.
  - `browser-worker` runs as uid 10001 with playwright 1.55.0 and Chromium 1187.
- **End state (09:50):**
  - 11 services healthy, 0 restarts, no OOM kill.
  - No `dps_test_*` database left behind; every gate's session dropped its own.
  - Oracle at 82 % of its memory limit, Mongo at 46 %.

## 2. Gates

The stack gates ran **one at a time** (HANDOFF: the suites share the demo sources, bucket, rate limit and D4 counters), in the order `run_all.sh` → `chain.sh`:
1. seed;
2. smoke;
3. demo seed;
4. whole suite;
5. M1;
6. tesseract on PATH;
7. M2;
8. resume-after-kill;
9. M3;
10. M4;
11. DoD 9;
12. flow report;
13. demo seed;
14. e2e demo;
15. console pages.

Gates that do not use the stack ran beside them: static, frontend (finished before any pytest), deploy, kustomize, extras venv and deck.

| # | Gate (DoD) | Result | Time |
|---|---|---|---|
| 1 | `ruff format --check dps tests tools` (1) | 511 files already formatted; same with `--no-cache` | 0 s |
| 2 | `ruff check dps tests tools` (1) | All checks passed; same with `--no-cache` | 0 s |
| 3 | `mypy dps` (1) | No issues in 155 source files, with a warm cache and with a **fresh** cache | 0 s / 6 s |
| 4 | `DPS_TEST_REQUIRE_SERVICES=1 pytest -q`, whole suite, with `DPS_TEST_TESSERACT_CMD` (the app image's tesseract) and `DPS_TEST_IMAGE`/`DPS_TEST_BROWSER_IMAGE` = the `:local` images (1) | **4695 passed, 20 skipped, 0 failed** (4715 collected; skips in §3) | 30:45 |
| 5 | `… -m "docs or ocr"` (4, M1) | **139 passed**, 3 skipped (2 host-tesseract cases, see 5b; the deck module) | 0:15 |
| 5b | `tests/documents/test_ocr_tesseract.py` with the wrapper on `PATH` and `DPS_TEST_REQUIRE_TESSERACT=1` | **8 passed** | 0:03 |
| 6 | `… -m "db or pg_source or s3"` (4, M2 + M5 db) | **469 passed**, 2 skipped (key-less PG case by design; the deck module) | 10:09 |
| 6b | `… -m slow tests/connectors/test_resume_after_kill.py` (HANDOFF list) | **6 passed** | 1:41 |
| 7 | `… -m "oracle or mongo"` (4, M3) | **191 passed**, 2 skipped (Mongo has no composite key, by design; the deck module) | 5:32 |
| 8 | `… -m browser` with `DPS_TEST_TESSERACT_CMD` (4, M4) | **320 passed**, 10 skipped: 9 × `tests/console/test_console_pages.py` (**blocked by B1**, §4) and the deck module | 19:58 |
| 9 | `… -m deploy` (4, Task 25) | **62 passed** (`tests/deploy/test_openshift.py`), 1 skipped (the deck module) | 0:20 |
| 10 | `cd frontend && npm ci && npm run typecheck && npm test && npm run build` (2) | npm ci: 101 packages. `tsc --noEmit` clean. Node tests **46/46**. Vitest: 34 files, **418 passed**. `vite build` OK. `check-bundle`: no dev fixture code. | 0:12 |
| 11 | `DPS_SMOKE_REQUIRE_OCR=1 DPS_SMOKE_REQUIRE_M5=1 DPS_SMOKE_SCANNER=on python tools/smoke_test.py --m3`, after `seed_sources.py --target all --rows 20000 --seed 7` (5) | **All 60 checks passed, 0 SKIP**, details below the table | 2:57 |
| 12 | `python tools/flow_report.py --require-scanner` (6) | Exit 0. **92 calls, 92 as expected, 0 skipped**, in 16 route groups: ops, Layer 1, policies, documents, schema synthesis, taxonomy, connections, jobs, snapshots, job groups, evidence, artefacts, reports, policy store, admin, and UI scanner 10/10. Every row's status equals its expected status. | 0:17 |
| 13 | `python tools/demo_seed.py && python tools/e2e_demo.py --console` (7) | **FAIL.** S1–S5 PASS; **S6 FAIL** ("postgres run dead finalize_error"); S7–S12 NOT RUN. "FAIL: demo flow completed in 3:39". `reports/demo-screens/` is empty because S12 did not run. Seed: tenant, 3 keys, sources already at 20,000 (seed 7), 7 connections, policy `retail-kyc` v2, group `crm-uat-refresh`; the second seed reloaded Oracle and Mongo after the gates. | 3:39 |
| 14 | `DPS_TEST_REQUIRE_SERVICES=1 pytest -q tests/security tests/test_no_values_in_logs_m{1..5}.py` (9) | **459 passed** | 1:11 |
| 15 | `-m browser tests/console/test_console_pages.py` after the demo (their documented prerequisite) | **9 skipped**: the demo tenant has no finished run, scan or pack, because of B1 | 0:01 |
| + | `kustomize build deploy/openshift/overlays/{dev,prod}` + `kubeconform -summary -ignore-missing-schemas` (10) | dev: 33 resources (30 valid, 3 skipped); prod: 26 (23 valid, 3 skipped). **0 invalid, 0 errors.** | — |
| + | `python tools/settings_table.py --check` (11) | Current (186 settings) | — |
| + | `git ls-files \| xargs grep -nE "AKIA…\|BEGIN (RSA \|EC )?PRIVATE KEY"` (9) | 4 hits, all AWS's **documented example key** `AKIA…(AWS documentation example, redacted)` used as detector test input (`tests/test_patterns.py`, `tests/test_surrogates.py`, the M0 plan text). No PEM key. Same finding as I-m5-verify-r2 (N1). | — |
| + | Extras: `tests/test_mostly.py tests/test_mostly_linked.py` in the S1-M4 all-extras venv (editable install of this checkout; `dps` 1.0.0 imported from it) (1, 13) | **24 passed**, including the 3 cases that skip in `.venv` (pandas ×1, mostlyai ×2: DP + linked generation) | 3:13 |
| + | `tests/test_deck.py` with `python-pptx` 1.0.2 + XlsxWriter 3.2.9 in a private `--target` dir (`deck` extra; not installed in `.venv`) (11) | **43 passed** | 0:05 |
| + | Release facts (12) | Version 1.0.0 in `pyproject.toml`, `dps/__init__.py`, `frontend/package.json` and both `package-lock.json` fields. No `v1.0.0` tag yet, correctly, because it waits for the DoD. | — |

**Smoke details (gate 11):**
- Layer 1 and documents/OCR: the redacted PDF has 0 text characters and `ocr_checked`.
- Scanner:
  - 21 pages, 63 fields (51 personal), 9 risk flags;
  - non_get +0, 60 non-GET blocked;
  - 4/4 parallel-view shots; 3/3 captures masked;
  - persona export 0 demo values.
- M2 9/9 and M3 7/7: 20,000 rows ×3, consistency 3/3, evidence 3/3 verified, raw PII to LLM 0.
- **M5 block 8/8:**
  - policies with hashes;
  - reports summary (`raw_pii_sent_to_llm` 0);
  - posture report (11 checks; 5 pass on a dev stack);
  - db key lifecycle and db key disabled (401);
  - external tokenization round trip;
  - 422 never echoes input;
  - `Cache-Control: no-store`.

**DoD 4 named suites:**
- `tests/connectors/test_synthesize_copula.py` 7 passed. As built, it lives there, not in `tests/integration/`; see M5-DEVIATIONS.
- `tests/security/test_cross_tenant.py` passed.
- `tests/console/test_console_pages.py` 9 skipped (B1).
- DoD 13 extras, all passed in the whole suite: `test_llm_label_l2_l3.py` 27, `test_llm_egress.py` 14, `test_browser_image_runtime.py` 3, `test_e2e_demo.py` 35, `test_demo_seed.py` 10.

## 3. Whole-suite skips (20), every one accounted for

| Skips | Reason | Covered by |
|---|---|---|
| 9 | `test_console_pages.py`: the demo tenant has no finished run, scan or pack | **B1**: the demo's runs cannot finish |
| 2 | `test_ocr_tesseract.py`: no tesseract on the host PATH | gate 5b, 8 passed |
| 3 | pandas ×1, mostlyai ×2 (`ml`, the synth-ml extra) | extras venv, 24 passed |
| 1 | `test_deck.py` collection: no `pptx` in `.venv` | private deck run, 43 passed |
| 3 | no spaCy English model (`test_ner_presidio.py`, `test_playground_samples.py`, `test_schema_profile.py:813`) | not runnable here: the model is installed nowhere, the same as every earlier round. The third case is new since S1-M4. |
| 2 | conformance by design: MongoDB has no composite key; a key-less PostgreSQL table is not read | by design |

## 4. B1 — every L2 run with a stored (`@name`) or inline policy dies in `finalize` (blocking)

**Symptom.** `e2e_demo.py` S6 submits three `obfuscate` `table` runs under the group key with `policy: "@retail-kyc"`:
- `j_01M51EJW21P25HA8J8JC5YF0VE` (PG → `bank_uat.customers_demo`);
- `j_01M51EJW2BW9ENEXERBDAVWZZN` (Mongo → `profiles_demo`);
- `j_01M51EJW2N1K3K6X80Q81NQJZC` (Oracle → `CRM_MASKED.CUSTOMERS_DEMO`).

Each run did the following:
- prepared 4 partitions;
- read, transformed, verified (`checked` 143,340 for PG) and **wrote all 20,000 rows**;
- then logged `finalize_failed error=PrepareFailure` 3–4 times (04:12:56Z–04:16:06Z);
- ended `dead`, `error_code=finalize_error`, `evidence_id=null`.

The worker log has the 9 `finalize_failed` lines and nothing else at error level.

**Root cause** (`dps/layers/connectors/jobs_run.py`):
- `_finalize` (line 2156) builds `spec = RunSpec.model_validate(job.spec)`, "the redacted spec".
- Since F-18 (`7c3fd5c`, `fix(security): an inline policy body is sealed with the job KEK and job rows keep a value-free label`, on fsec, merged in `edb4480`), the row's `spec.policy` is the label `{sealed, sha256, name, version}`. The body travels only in `spec_enc`.
  - A `@name` reference is resolved to the stored body at accept time (`dps/layers/api/policy_refs.py`), so it is sealed too.
- `_post_sample` (line 1925) calls `_policy(ctx, spec, plan.policy_name)`, which calls `ctx.engine.policy(<label dict>)`. That raises `EngineError`, which becomes `PrepareFailure("policy_invalid")`.
- `_best_effort` catches `ConnectorError`, `StorageError`, `ConnectionStoreError`, `PatternTooSlow`, `SelectionError`, `SecretUnavailable` and `SealError`, but **not `PrepareFailure`**.
- So `finalize` treats the failure as transient, retries, and the job is dead-lettered.
- `prepare` and the partitions are unaffected because they use `_spec(ctx, job)`, which opens `spec_enc` and puts the body back. Discovery has its own opened spec, and S5 passed.
- `_store_evidence` (line 2418) has the same `_policy(ctx, spec, …)` call under `contextlib.suppress(PrepareFailure)`. Once the post-sample is fixed, a stored-policy pack would still silently lose `policy_version` unless that call gets the opened spec too.

**Proof (in process, read-only).** I took the dead PG run's stored spec from the API and called `jobs_run._policy(SimpleNamespace(engine=PrivacyEngine(Settings())), spec, "retail-kyc")`:
- `is_policy_label(spec.policy)` is True;
- the call raises `PrepareFailure code=policy_invalid`;
- with `spec.policy=None` it raises the same: `retail-kyc` is a tenant policy, not a preset.

The smoke's three M3 runs succeed because their `spec.policy` is `None` (a preset from the snapshot).

**Why it was not caught:**
- fdemo's two green rehearsals (`b57ec86`) ran without F-18: `fdemo` does not contain `7c3fd5c`.
- fsec's tests (`tests/api/test_inline_policy_sealed.py`) check what the row stores, not a run through `finalize`.
- No integration test runs an L2 job with a stored or inline policy to `succeeded`.
- `tests/test_e2e_demo.py` uses fakes.

**Scope:**
- Every L2 `run` (obfuscate or synthesize, any engine, any write mode) whose policy is `@name`, `@name:vN` or an inline body. These are the M5 policy store's main use and the demo's S6/S7/S8.
- Not checked here: whether L3 scans with `@name` policies have an analogous path (S9 did not run).
- **No privacy leak:** an independent source-vs-target count on the three dead targets (`svt_demo.py`, `logs/42-`) found:
  - 20,000/20,000 rows aligned in each engine;
  - **0** same-row survivors in any non-kept column.

  The failure is liveness and evidence: the target is fully written, but the job is `dead` with no pack.

**Fix (suggested):**
1. In `_finalize`, use the opened spec (`spec = _spec(ctx, job)`, still available before the purge) wherever the policy or selection is needed: `_post_sample`, `_store_evidence`'s `policy_version`, and `_identifier_check`, which today also receives the redacted spec. Keep the redacted `job.spec` for what goes into the evidence and audit (`EvidenceInputs(spec=job.spec, …)` already does).
2. Decide how a `SecretUnavailable` from `_spec` at finalize is reported.
3. Tests first:
   - an integration test (`db` + `pg_source`, ideally also Oracle/Mongo through the conformance harness) that submits a `table` run with a published `@name` policy, and one with an inline body, through a worker;
   - assert `succeeded`, an evidence pack, and the pack's policy name and version;
   - a unit test of `_post_sample`/`_finalize` with a sealed-label spec, red before the fix.
4. Re-verify:
   - `python tools/demo_seed.py && python tools/e2e_demo.py --console`: S1–S12 PASS, under 10:00, screenshots in `reports/demo-screens/`;
   - then `DPS_TEST_REQUIRE_SERVICES=1 pytest -q -m browser tests/console/test_console_pages.py`: 9 passed;
   - the M2/M3 markers and `tests/api`.

## 5. Proofs (counts only)

- **D4: the demo site's `/__received`, a counting server.**
  - Start 0/0/0/0.
  - The whole suite and the browser gate each moved it by exactly **+8 non_get / +2 form_get / +1 logout / +1 admin**: `POST /api/draft` ×4, `/apply/identity/submit` ×2, `/api/autosave` ×1, `/api/telemetry/form-start` ×1. These are the deliberate positive controls of `test_demo_site.py` / `test_no_submit.py`, the same split as every earlier round.
  - **Every other gate moved it by +0:** seed, smoke (its scan: non_get +0, 60 non-GET blocked), demo seeds, M1, M2, slow, M3, DoD 9, flow report, e2e, console pages.
  - End 16/4/2/2.
- **LLM exposure:** `dps_llm_raw_pii_sent_total 0.0` before and after every gate. The smoke's `m3 raw pii to llm` check and the demo's S1/S2 also report 0.
- **Source vs target** (`svt.py`, which reads source and target in full):
  - The smoke's M3 `table` runs, read right after the smoke and before the suite reseeded anything:
    - PG `bank.customers` → `bank_uat.customers_smoke`, 20,000/20,000 aligned on the 8 kept columns;
    - Mongo `crm.profiles` → `crm_uat.profiles_smoke`, 20,000/20,000 aligned on `_id`;
    - Oracle `CRM.CUSTOMERS` → `CRM_MASKED.CUSTOMERS_SMOKE`, 20,000/20,000 aligned on `LAST_CONTACT_AT`.
  - Result: **0 same-row survivors** in every non-kept column.
  - Values also found elsewhere in the same source column: customer id 4 per engine (FF1 crossings); date of birth 12,590–12,628 (`date_shift` over a small domain).
  - The B1 demo targets: also 0 survivors (§4).
- **Masked captures** (independent OCR, `ocr_probe.py`: the app image's tesseract, psm 11 + 3). For the smoke scan `s_01M51AC9E29XQ5XGM2N48KR9J7` (25 pages, 69 images):
  - masked captures 3/3 `captured`;
  - the only read is `application_ref` on page 25, the known N9 (not a detected entity), also read in the original and annotated shots;
  - filled shots: 0 demo values;
  - positive control: OCR reads the scan's own display-persona values in 11/12 `filled_synthesize` shots (220 hits) and 4/12 `filled_obfuscate` shots (12 hits).
- **Logs** (all 15 containers, since the stack start; `logs/95-`, `logs/96-`):
  - **0** demo-site values, **0** PEM private-key markers, **0** key-material field names.
  - **0** seeded source values: 129,792 needles from PG `bank.customers`, Oracle `CRM.CUSTOMERS` and Mongo `crm.profiles`; self-test 4/4.
  - **App:** 0 errors; warnings are 128 `rate_limited` (the console tests, the known N3) and 3 `auth_rejected` (deliberate checks).
  - **Worker:** 9 `finalize_failed` lines (B1). It also logged one asyncio traceback pair (`_SelectorSocketTransport._call_connection_lost: 'NoneType' object has no attribute 'connection_lost'`) at 04:12:32Z, right after a demo discovery finished (N2).
  - **Database error lines:** only those that negative tests provoke on purpose:
    - append-only and immutability triggers, permission denied, check and unique constraints;
    - wrong-password logins (`dps_owner`, `dps_reader`), value too long, a read-only transaction, a statement timeout.
- **Test databases:** no `dps_test_*`/`dps_fresh_*` database before or after any gate; each session dropped its own.
- **Foreign runs** (`watch_foreign.sh`, every 20 s, 449 lines):
  - Every connection to main's ports came from the main checkout or its pytest temp dirs.
  - One short-lived process (08:35:43) exited before `lsof` could read its cwd, as in S1-M4.
  - No `dps-wt-*` worktree connected.
- **Commits** `8ea220a..HEAD` (128):
  - all authored and committed by `nilendu.ganguli@gmail.com`;
  - **0** `Co-Authored-By`/`Signed-off-by` trailers;
  - no conflict markers in the tree.

## 6. Non-blocking notes

- **N1** The DoD 9 secret grep is not literally empty: it hits AWS's documented example key `AKIA…(AWS documentation example, redacted)` in two tests and the M0 plan. It is not a credential, and the auto-push gitleaks gate is clean. Either reword the gate (exclude the documented example) or accept it explicitly.
- **N2** The worker logged an asyncio transport-teardown traceback (`_call_connection_lost` on a `None` protocol and socket) right after a discovery. No job was affected (`discover_done` ×3). It is probably a driver closing a socket twice; worth a look.
- **N3** `_store_evidence` suppresses the same `PrepareFailure`. Fix it with B1, or stored-policy packs lose `policy_version`.
- **N4** DoD ordering: the console-page tests need a finished demo (run, scan, pack), but the HANDOFF gate list runs the browser gate before the demo. On a fresh stack they always skip. Either state "run `e2e_demo.py` before the browser gate" or re-run that file after the demo (I did, gate 15).
- **N5** `.venv` lacks the `deck` and `synth-ml` extras and a spaCy model, so 4 + 3 skips recur in every whole-suite run. Installing `deck` in the dev venv would remove one recurring "collection skipped".
- **N6** The posture report reads 5/11 on the dev stack, as expected for local posture; the smoke checks its presence only.

## 7. For the fix round and the owner

- B1 is the only blocker. After the fix (with its red-first tests), re-run:
  - `tests/api`, `-m "db or pg_source or s3"`, `-m "oracle or mongo"`;
  - `python tools/demo_seed.py && python tools/e2e_demo.py --console`, which must reach S12 in under 10:00;
  - then `-m browser tests/console/test_console_pages.py`.
- Rebuild the stack (`docker compose --profile full up --build -d`) so the worker runs the fix.
- The stack is **left running** in the state above:
  - it holds the three dead demo runs and their written `*_demo` targets;
  - sources are at 20,000 rows (seed 7);
  - the demo tenant, keys and group are seeded.
- No tag was created.
