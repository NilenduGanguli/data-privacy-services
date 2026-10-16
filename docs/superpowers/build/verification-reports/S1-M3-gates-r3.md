# S1-M3 — post-merge gates on main after the M3 merge (GATES verifier, round 3)

**Verdict: FAIL** — one blocking finding (B1). The M2 integration gate (`-m "db or pg_source or s3"`) failed
on its first run: **1 failed, 365 passed**. The failing test is
`tests/connectors/test_synthesize_copula.py::test_surrogate_job_on_the_seeded_demo_customers`. Its
source-vs-target check found **1** synthetic `national_id` equal to a demo-source one (counts only; `tax_id` 0).

This is the known **seeded-demo D-A flake** that S2 gates r1 reported as B1 for `m4`:
- Main's demo seed (model 1) puts all **11 989** Aadhaar-shaped demo national ids in the same range that the
  NATIONAL_ID surrogates draw from.
- So each of the two seeded-demo tests fails in about **1.4 %** of runs.
- The fix ("demo model 2") is on `m4` only, in `a3cd667`. It is **not on main**.

The failure is not an M3 regression: `d2fdc3f` changes no seed, surrogate or identifier-check code. Two rerun
sessions of the two seeded-demo tests passed (2/2 each).

Every other gate is green:
- the whole suite (2 677 passed, 8 known skips);
- the M1, M3, browser and conformance gates;
- the frontend;
- both smokes (34/34 and 41/41);
- ruff and mypy, in `.venv` and in a fresh venv with every extra.

Round 2's B1 (the MongoDB FF1 test oracle) is fixed. Its row-aligned tests passed in all three runs that
include them: the whole suite, M2 and M3.

| | |
|---|---|
| Date | 2026-10-14 23:46 → 2026-10-15 00:24 IST |
| Tree verified | `main` @ `d2fdc3f` (clean before and after). It is the only commit since round 2 (`5c11e49`): the S1-M3 fix round 2. Author NilenduGanguli <nilendu.ganguli@gmail.com>, no trailer. |
| Checkout / Python | `/Users/neelu/document_intelligence/data-privacy-services`, `.venv` (Python 3.12.13; ruff 0.17.0, mypy 2.4.0, pytest 9.1.1); Node 22.20.0 / npm 10.9.3; uv 0.11.21 |
| Test endpoints | contract §12.2 defaults (`~/.cache/dps-build/s1-m3-gates-r3/env.sh`: 5441 / 5442 / 1522 / 27018 / 9010 / 5016 / 8410 / 8400) |
| Logs | `~/.cache/dps-build/s1-m3-gates-r3/logs/` (one file per gate; `chain-summary.log`; driver `chain.sh`) |
| Probes | `~/.cache/dps-build/s1-m3-gates-r3/probe/` (`demo_id_ranges.py/.json`, `residue.py` + JSON, image manifests), `logscan_r3.py` → `logscan.json` |
| Files modified | none. As in earlier rounds (A-N2), the console tests overwrote the 54 tracked `m3-shots` PNGs. I copied them to `m3-shots-from-run/`, then restored them to HEAD by explicit path (`restored-shots.txt`). `git status` is empty and HEAD is unchanged. |

## 1. Stack

```bash
DPS_TOKENIZER_MOCK_PORT=8430 docker compose --profile docs --profile sources --profile oracle --profile tokenizer up --build -d
```

- **Docker:** 29.5.3, 16 CPUs, 35 GiB.
- **Build:** every layer came from the cache, but the image ID changed to `cacb4f1d49a5`. Compose recreated
  `app`, `worker`, `tokenizer-mock` and `azure-ocr-mock`, plus the one-shots. The data stores kept running.
- **Image = HEAD:** the sha256 of every tracked file under `dps/` and `tools/` (175 files) is identical inside
  `app` (`/app`) and in the checkout (`probe/manifest-{checkout,image}.txt`).
- **Readiness:** I polled `/readyz`, never used `--wait`. It reported `ready: true` on the first poll (23:46:43):
  - db up, migrations verified; jobs ready;
  - tokenizer up (http); ocr up (azure-read-v3.2);
  - fpe ff1; keys db+static;
  - ner patterns-only; llm stub (coin_tls verified).
- **One-shots:** all exited 0. migrate printed `applied 0 (none pending)`; seed-sources printed `restore copy: up
  to date`; demo-secrets and minio-init also exited 0.
- **Migrations:** 001–010, 020, 040–045, each number used once.
- **Tokenizer port:** `tokenizer-mock` still publishes host port **8430**. The m4 lane now uses 8424, so 8420 is
  free. I kept 8430 so the container config, and the lens verifiers' commands, stay as they were. Keep passing
  `DPS_TOKENIZER_MOCK_PORT=8430` to any `docker compose up` here.
- **At the end:** all 9 services were running and healthy, with 0 restarts and `OOMKilled=false`. Oracle uses
  2.44 of 2.64 GiB and MongoDB 0.52 of 1 GiB.
- **The stack is left running.**

## 2. Gates

| # | Gate | Result | Time |
|---|---|---|---|
| 1 | `ruff format --check dps tests tools` | 382 files already formatted | — |
| 2 | `ruff check dps tests tools` | All checks passed | — |
| 3 | `mypy dps` | no issues in 134 source files | — |
| 4 | `DPS_TEST_REQUIRE_SERVICES=1 pytest -q`, whole suite (2 685 collected; nothing deselected) | **2677 passed, 8 skipped**, 0 failed | 13:04 |
| 5 | M1: `… -m "docs or ocr"` (141) | **139 passed, 2 skipped** (tesseract) | 0:16 |
| 6 | M2: `… -m "db or pg_source or s3"` (366) | **1 failed, 365 passed** (B1) | 8:49 |
| 6b | Rerun of the failing module's seeded-demo tests (`test_synthesize_copula.py -k seeded_demo_customers`), 2 sessions | **2 passed + 2 passed** | 0:35 + 0:34 |
| 7 | M3: `… -m "oracle or mongo"` (179) | **178 passed, 1 skipped** (capability skip) | 5:29 |
| 8 | `… -m browser` (console on main: M2 ×3, M3 groups ×3, M3 wizard ×3) | **9 passed** | 1:50 |
| 9 | Conformance on all three engines (`test_conformance_{postgres,oracle,mongo}.py -m "pg_source or oracle or mongo"`) | **29 passed, 1 skipped** (MongoDB has no composite keys) | 0:04 |
| 10 | `cd frontend && npm ci && npm test && npm run build` | npm ci OK (101 packages); node tests **42/42**; vitest 16 files / **234 passed**; build (`tsc --noEmit && vite build`) OK | 0:16 |
| 11 | `tools/seed_sources.py --target all --rows 20000 --seed 7`, twice | Run 1: PG `already seeded (seed 7, 20000 customers)`, CRM.* / LOANS.APPLICANTS / crm.profiles rebuilt at demo size (the M3 tests reseed them at test size). Run 2: everything `unchanged`. | 0:16 |
| 12 | `DPS_SMOKE_REQUIRE_OCR=1 python tools/smoke_test.py` (M0–M2) | **all 34 checks passed** | 0:27 |
| 13 | `DPS_SMOKE_REQUIRE_OCR=1 python tools/smoke_test.py --m3` | **all 41 checks passed**. See the breakdown below. | 1:58 |
| 14 | `/metrics` after the smoke runs (and again at the end) | `dps_llm_raw_pii_sent_total 0.0` | — |

**Gate 13 breakdown:**
- connections: 6/6 tested.
- discover: 3/3 accepted.
- group key: returned in the create answer only.
- runs: 3/3, 20 000 rows each (pg/mongo/oracle).
- consistency: 3/3 pairs (500 sampled, min coverage 1).
- evidence: 3/3 verified under the group's key id.
- raw PII to the LLM: 0.

**Not applicable on main:** `tools/flow_report.py`, part of contract §12.4's M5 gate, does not exist on main
yet. `-m browser` here covers the console only, because the scanner is M4.

**Skips (8 in gate 4):** the same as round 2, all environmental or by design.
- 1 capability skip (MongoDB composite keys, `conformance.py:196`).
- 2 `tesseract`: there is no binary on the host; the app image has one.
- 3 MOSTLY/pandas: the `synth-ml` extra is not in `.venv`. §4.1 covers them.
- 2 spaCy English model: NER is optional.

**Collect-only mapping:** whole 2 685 (+12 since round 2), M1 141, M2 366 (+4), M3 179 (+4), browser 9. Every
collected test passed or was one of the known skips, except the B1 test in gate 6. Fix round 2's new tests ran
where they should:
- `test_clear_ledger.py` and the extended `test_da_free_text_spans.py`: offline.
- `test_oracle_ledger_cross_connection.py` and the cross-connection resume-after-kill case: oracle.
- The row-aligned `test_mongo_pipeline.py` / `test_oracle_pipeline.py` / `test_m3_three_way.py` /
  `test_console_m3_wizard.py`: whole suite, M2 and M3, plus browser for the wizard test.

## 3. Blocking finding

### B1 — the seeded-demo D-A test failed the M2 gate: a synthetic `national_id` equals a demo-source one

**What failed (gate 6, first run):**
- Test: `test_surrogate_job_on_the_seeded_demo_customers`, a surrogate-only synthesize of the 20 000-row demo
  `bank.customers` into a scratch schema.
- The job succeeded: `finalize_done outcome=succeeded`, 20 000 rows.
- The test's source-vs-target check (`_assert_no_real_identifier`, normalised letters and digits, counts
  only) returned `{'national_id': 1, 'tax_id': 0}`.
- The assertion output in the log holds only column names and counts.
- The same test passed in gate 4 and in both rerun sessions (6b). Its sibling
  `test_copula_job_on_the_seeded_demo_customers` passed all four times.

**Root cause: test data, already diagnosed and fixed on `m4`:**
- `S2-gates-r1.md` (B1) found it. `S2-fix-r1.md` §2.6 confirmed the path by simulation: 17 of 1 200 runs had
  a hit, i.e. **1.4 %**, all Aadhaar, which matches 11 989²/10¹⁰.
- The mechanism: the demo seed (`tools/seed_model.py`, model 1) gives every Aadhaar-shaped demo id a leading
  `1`.
- That is exactly the never-issued range `unissued.twelve_digits` draws NATIONAL_ID surrogates from: leading
  `1`, ten free digits, Verhoeff check.
- A run therefore writes 11 989 surrogates into a 10¹⁰ space that also holds the 11 989 demo ids.

**Confirmed on main's stack (`probe/demo_id_ranges.json`, counts only):**

| Field | Count |
|---|---|
| `bank.customers` rows | 20 000 |
| AADHAAR-shaped national ids | 11 989 |
| TIN9 | 3 998 |
| NINO | 2 010 |
| CURP | 2 003 |
| **AADHAAR with leading 1 (in `twelve_digits`' range)** | **11 989 of 11 989** |
| TIN9 in the `000…` range | 0 |

**The fix is not on main:**
- `a3cd667` ("fix(m4): S2 fix round 1 — … demo ids outside D-A ranges") is contained only in `m4` and
  `origin/m4`.
- Main's `tools/seed_model.py`, `seed_sources.py`, `seed_oracle.py` and `seed_mongo.py`, and their tests
  (`tests/test_seed_model.py`, `tests/test_seed_sources.py`, `tests/connectors/test_seed_sources_pg.py`,
  `tests/e2e/test_seed_shared_ids.py`), are **byte-identical** to `m4` just before that fix (`a0e8d26`).
  `git diff main a0e8d26 -- <those files>` is empty.

**Not a product leak:**
- The surrogates come from never-issued ranges, so they cannot equal a real issued Aadhaar. Only the demo,
  whose ids are themselves never-issued, can collide.
- The product's backstop flags such a collision. `test_synthesize_identifiers.py::test_a_real_identifier_in_the_target_needs_review`
  passed in gates 4 and 6: `in_source ≥ 1` gives `review_required` and `passed: false`.
- So a collision is never reported as "verified".

**Why it blocks:**
- The M2 gate as specified went red on the merged tree.
- These two tests run in the whole suite and in M2. Together that is about **5–6 %** odds that a gate round
  goes red for no product reason.
- This round and S2 gates r1 both hit it.

**Fix (test data only; keep both assertions exactly as they are).** Either option works:
1. **Port demo model 2 to main now:** take only the seed-model hunks of `a3cd667`:
   - `tools/seed_model.py`, `tools/seed_sources.py`, `tools/seed_oracle.py`, `tools/seed_mongo.py`;
   - `tests/test_seed_model.py`, `tests/test_seed_sources.py`, `tests/connectors/test_seed_sources_pg.py`,
     `tests/e2e/test_seed_shared_ids.py`.

   Port them byte-identically, so the later `m4` merge sees the same change on both sides and stays clean. Then:
   - Add a short note to HANDOFF and the deviation log.
   - Reload the main stack's demo sources. The model-2 marker reloads an older stack without `--reset`.
   - Rerun the whole suite and the M2 gate.
2. **Take it with the M4 merge**, which already carries `a3cd667`, and rerun the whole-suite and M2 gates on
   main after that merge.

With either option, `test_no_seeded_identifier_lies_where_a_surrogate_can_draw` (from `a3cd667`) makes the
premise deterministic.

## 4. Extra checks

### 4.1 Fresh venv with every extra (programme plan §2.5 step 3)

I ran `uv venv --python 3.12` and then
`uv pip install -e ".[ner,fpe,vertex,docs,db,connectors,uitest,e2e,tabular,synth-ml,dev]"` in
`~/.cache/dps-build/s1-m3-gates-r3/venv-all`, a fresh install of 233 packages. The editable install wrote
nothing into the checkout: the `git status --ignored` listing was unchanged.

- `ruff format --check` / `ruff check`: clean.
- **`mypy dps` with every extra installed: no issues in 134 files.**
- `pytest -q tests/test_mostly.py tests/test_mostly_linked.py tests/test_ner_presidio.py tests/test_playground_samples.py`:
  **92 passed, 2 skipped** (spaCy model), 6:00. This includes the real MOSTLY SDK train/generate tests and the
  pandas narrowing test that `.venv` skips.

### 4.2 Logs (counts only; `logscan_r3.py` → `logscan.json`)

The scan covered container logs since the 23:46 recreate, plus this round's own gate logs.

- **Needles:**
  - 185 258 source values of 8+ characters from:
    - PG `bank` customers, accounts, addresses, cards, contacts, kyc_documents and loan_applicants;
    - Oracle `CRM.CUSTOMERS`;
    - MongoDB `crm.profiles` leaves.
  - 1 730 first names of 6+ letters (whole word).
  - The 2 local demo secrets (bootstrap API key, tokenizer token).
- **Container results:**
  - **0 value hits, 0 first-name hits and 0 secret hits** in `app` (231 lines), `worker` (129),
    `tokenizer-mock` (203) and `azure-ocr-mock` (343).
  - 0 traceback lines and 0 error- or critical-level lines.
  - Warnings: 2 × `auth_rejected` in `app`, the deliberate "auth required" check of each smoke run.
  - The planted control was found.
- **Gate logs:** all 20 files have **0 hits** of each kind. The B1 failure output holds column names and counts
  only, so nothing needed redaction this round.

### 4.3 Residue (counts only; `probe/residue-after-*.json`)

- After the chain and again after the reruns, the stack had:
  - 0 `dps_test_*` / `dps_fresh_*` databases on dps-db;
  - **0** `t_*` schemas in `bank` (round 2's leftover `t_e4a47902` is gone);
  - 0 `DPS_T_*` Oracle users;
  - 0 `dps_t_*` MongoDB databases.
- No `DPS_WRITE_LEDGER` table exists in any Oracle schema that SYSTEM can see.
- While the chain ran, the only test database on main's dps-db was this round's own (`dps_test_<pid>` of my
  pytest). So no other lane's suite reached main's stack.

## 5. Comparison with earlier rounds

| Gate | gates r1 (`144cc65`) | gates r2 (`5c11e49`) | **gates r3 (`d2fdc3f`)** |
|---|---|---|---|
| whole suite | 2617 / 8 skip | 2665 / 8 | **2677 / 8** |
| M1 | 139 / 2 | 139 / 2 | **139 / 2** |
| M2 | 341 | 360 + 2 failed (FF1 oracle); rerun 362 | **365 + 1 failed (B1, seeded-demo D-A); reruns of the failing tests green** |
| M3 | 162 / 1 | 174 / 1 | **178 / 1** |
| browser | 9 | 9 | **9** |
| conformance | 29 / 1 | 29 / 1 | **29 / 1** |
| vitest / node | 232 / — | 234 / 42 | **234 / 42** |
| smoke M0–M2 / `--m3` | 34 / 41 | 34 / 41 | **34 / 41** |
| fresh venv (mypy / extras tests) | — | clean / 92+2 | **clean / 92+2** |

The whole suite took 13:04 here against 10:23 in round 2. During it, the m4 and fsec lanes' suites and this
round's fresh-venv MOSTLY tests ran on the same host. There were no timeouts and no failures.

## 6. Non-blocking notes

- **N1 (A-N2, known; F4 polish).** The console tests still write their screenshots into the tracked
  `docs/superpowers/build/m3-shots/`. This run's 54 files were backed up and restored, and main is clean.
- **N2 (F1 input).** `npm ci` reports 8 advisories: 5 moderate, 1 high, 2 critical. The count is unchanged
  since round 1.
- **N3 (cosmetic).**
  - The Vite main chunk is 558 kB, above 500 kB.
  - Every pytest run emits one `StarletteDeprecationWarning`.
  - The fresh-venv run printed two value-free `key_refresh_failed` / `audit_flush_failed` (`gaierror`)
    warnings from an offline test.
- **N4.** Three probe target tables from earlier rounds remain in `CRM_MASKED`: `ADDR_LEDGER_220646`,
  `ADDR_LEDGER_FIX2_232239` and `ADDR_SYN_LEDGER_FIX2_232239`, 40 000 rows each. Their suffixes match 22:06
  (engines r2) and 23:22 (fix r2). They hold masked or synthetic output, not demo source data. They were left
  in place: drop them at the next cleanup.
- **N5 (state for the lens verifiers).**
  - The demo sources are at **demo size**: PG seed 7 / 20 000 customers; Oracle and MongoDB 20 000. The last
    reseed printed `unchanged`.
  - The newest `--m3` smoke group is `smoke-m3-1792003743` (`grp_01M4XVY7TZ9XTYVRW603DB9CHE`, 00:19:03 IST).
    HANDOFF trap 7 applies: rerun `--m3` rather than re-checking an older group.
  - The stack runs on image `cacb4f1d49a5`, which equals HEAD for `dps/` and `tools/`. Pass
    `DPS_TOKENIZER_MOCK_PORT=8430` to any `compose up`.

## 7. Blocking findings

1. **B1:** the M2 gate is red. `tests/connectors/test_synthesize_copula.py::test_surrogate_job_on_the_seeded_demo_customers`
   found 1 synthetic `national_id` equal to a demo-source one.
   - **Cause:** main's demo seed model 1 places all 11 989 Aadhaar-shaped ids in the NATIONAL_ID surrogates'
     own range. Each seeded-demo test fails in about 1.4 % of runs.
   - **Fix:** port demo model 2 from `m4` `a3cd667`, byte-identical (seed tools plus their 4 test files),
     reload the demo sources, then rerun the whole-suite and M2 gates. Alternatively, rerun those gates after
     the M4 merge, which carries the fix.
   - Do not relax `_assert_no_real_identifier`.
