# S1-M3 — post-merge gates on main after the M3 merge (GATES verifier, round 2)

**Verdict: FAIL** — one blocking finding (B1). The M2 integration gate (`-m "db or pg_source or s3"`) went red on
its first run: 2 failed / 360 passed, both in `tests/connectors/test_mongo_pipeline.py`. Root cause: the test's
"survivors" check counts a masked customer id equal to *any* source id, and FF1 masking of an 8-digit id makes
such cross-row equalities happen in about 1.4 % of runs. The product behaved correctly: the failing jobs passed
the strict per-row verification, which fails a job if any row keeps its own id. A rerun of the gate passed
(362/362). The test is still flaky by construction, so every future gate round can fail at random until a
test-only fix lands. Every other gate passed with the same counts as fix round 1.

| | |
|---|---|
| Date | 2026-10-14, 20:51–21:40 IST |
| Tree verified | `main` @ `5c11e49` (clean before and after). `5c11e49` is the only commit since round 1 (`144cc65`): the S1-M3 fix round 1. It is authored by NilenduGanguli <nilendu.ganguli@gmail.com> and has no trailer. |
| Checkout / Python | `/Users/neelu/document_intelligence/data-privacy-services`, `.venv` (Python 3.12.13; ruff 0.17.0, mypy 2.4.0, pytest 9.1.1); Node 22.20.0 / npm 10.9.3 |
| Test endpoints | contract §12.2 defaults (`~/.cache/dps-build/s1-m3-gates-r2/env.sh`: 5441 / 5442 / 1522 / 27018 / 9010 / 5016 / 8410 / 8400) |
| Logs | `~/.cache/dps-build/s1-m3-gates-r2/logs/` (one file per gate; `chain-summary.log`) |
| Probes | `~/.cache/dps-build/s1-m3-gates-r2/probe/` (value-free pytest plugin, FF1 Monte Carlo, 60 session records), `logscan_r2.py` → `logscan.json` |
| Files modified | none. The console tests overwrote the 54 tracked `m3-shots` PNGs again (A-N2); they were copied to `m3-shots-from-run/` and restored to HEAD by explicit path (`restored-shots.txt`). |

## 1. Stack

```bash
DPS_TOKENIZER_MOCK_PORT=8430 docker compose --profile docs --profile sources --profile oracle --profile tokenizer up --build -d
```

- Docker 29.5.3, 16 CPUs, 35 GiB. The stack from fix round 1 was already up. Every build layer came from the
  cache, but the image ID changed from `0f590d70d250` to `c18bced81712` (image metadata only), so compose
  recreated `app`, `worker`, `tokenizer-mock`, `azure-ocr-mock` and the one-shots. **Image = HEAD:** the
  sha256 manifest of every tracked file under `dps/` and `tools/` is identical inside `app` and in the
  checkout.
- `/readyz` was polled, never `--wait`. It reported `ready: true` on the first poll (20:53:44): db up (migrations
  verified), jobs ready, tokenizer up (http adapter), ocr up (azure-read-v3.2), fpe ff1, keys db+static, ner
  patterns-only, llm stub.
- All one-shots exited 0: migrate (`applied 0 (none pending)`), demo-secrets, minio-init and seed-sources
  (`restore copy: up to date`). Migrations on main: 001–010, 020, 040–045, each number once.
- `tokenizer-mock` publishes host port **8430** because the m4 lane uses 8420. Lens verifiers must pass
  `DPS_TOKENIZER_MOCK_PORT=8430` to any `docker compose up` here.
- At the end, all 13 containers had 0 restarts and `OOMKilled=false`. Oracle uses 2.56 of 2.64 GiB, MongoDB
  0.77 of 1 GiB. **The stack is left running.**

## 2. Gates

| # | Gate | Result | Time |
|---|---|---|---|
| 1 | `ruff format --check dps tests tools` | 380 files already formatted | — |
| 2 | `ruff check dps tests tools` | All checks passed | — |
| 3 | `mypy dps` | no issues in 134 source files | — |
| 4 | `DPS_TEST_REQUIRE_SERVICES=1 pytest -q`, whole suite (2 673 collected; nothing deselected) | **2665 passed, 8 skipped**, 0 failed | 10:23 |
| 5 | M1: `… -m "docs or ocr"` | **139 passed, 2 skipped** (tesseract) | 0:16 |
| 6 | M2: `… -m "db or pg_source or s3"` | **2 failed, 360 passed** (B1) → **rerun 362 passed** (21:25–21:33) | 8:36 / 8:08 |
| 7 | M3: `… -m "oracle or mongo"` | **174 passed, 1 skipped** (capability skip). Includes both B1 tests, which passed. | 5:03 |
| 8 | `… -m browser` (console on main: M2 ×3, M3 groups ×3, M3 wizard ×3) | **9 passed** | 1:53 |
| 9 | Conformance on all three engines (`test_conformance_{postgres,oracle,mongo}.py -m "pg_source or oracle or mongo"`) | **29 passed, 1 skipped** (MongoDB has no composite keys) | 0:04 |
| 10 | `cd frontend && npm ci && npm test && npm run build` | npm ci OK; node tests 42/42; vitest 16 files / **234 passed**; build (`tsc --noEmit && vite build`) OK | 0:09 |
| 11 | `tools/seed_sources.py --target all --rows 20000 --seed 7`, twice | Run 1 rebuilt CRM.* / LOANS.APPLICANTS / crm.profiles at demo size (the M3 tests reseed them at test size). Run 2: everything `unchanged`. PG `already seeded (seed 7, 20000 customers)` both times. | 0:16 |
| 12 | `DPS_SMOKE_REQUIRE_OCR=1 python tools/smoke_test.py` (M0–M2) | **all 34 checks passed** | 0:24 |
| 13 | `DPS_SMOKE_REQUIRE_OCR=1 python tools/smoke_test.py --m3` | **all 41 checks passed**: m3 connections 6/6 tested, discover 3/3, group key only in the create answer, runs 3/3 (20 000 rows each, pg/mongo/oracle), **consistency 3/3** (500 sampled, min coverage 1), evidence 3/3 verified under the group's key id, raw PII to LLM 0 | 1:37 |
| 14 | `/metrics` after the smoke runs | `dps_llm_raw_pii_sent_total 0.0` | — |

**Skips (8 in gate 4).** All are environmental or by design and match rounds 1 and 2 of the fix work:
- 1 capability skip (MongoDB has no composite keys, `conformance.py:196`).
- 2 `tesseract` (no binary on the host; the app image has one).
- 3 MOSTLY/pandas (`synth-ml` extra is not in `.venv`; covered by §4.1).
- 2 spaCy English model (NER is optional).

The fix round's new tests ran where they should, by collect-only mapping:
- `test_production_write_gate.py` (9) runs in M2.
- `test_files_m3_engines.py` (6), `test_oracle_keyless_synthesize.py` (2) and `test_m3_free_text_names.py`
  (4) run in both M2 and M3.
- The offline `test_files_documents.py`, `test_da_free_text_spans.py`, `test_da_m3_engines.py`,
  `test_evidence.py` and `test_targets.py` run in the whole suite.

## 3. Blocking finding

### B1 — flaky source-vs-target oracle in `tests/connectors/test_mongo_pipeline.py` (M2 gate red on its first run)

**What failed (gate 6, first run, 21:05:42 and 21:05:49):**
- `test_mongo_profiles_are_discovered_and_masked_into_a_created_collection`: `assert survivors(source, out, "$.customerId") == 0` failed with **1**.
- `test_mongo_append_generate_on_the_same_collection`: `assert survivors(source, appended, "$.customerId") == 0` failed with **1**.
- Both jobs had already reached `succeeded`, with `finalize_done outcome=succeeded`.

**Root cause (test oracle, not product):**
- `survivors()` (line 103) counts target leaves equal to *any* source leaf at the path. It does not compare a
  document with its own source.
- Discovery plans `$.customerId` (entity CUSTOMER_ID) as `fpe_digits`: FF1 over the 8 digits, keeping the
  leading-zero rule. Each job uses a fresh random DEK (`submit._material` → `generate_key_material`), and the
  shape stays `AAAA-99999999` on both sides.
- FF1 is a keyed permutation of the 8-digit space. A masked id can therefore equal *another* of the 1 200 test
  customers' ids, and HANDOFF §13 already documents such FF1 collisions on MongoDB `customerId` as expected
  ("not survivors").

**Evidence (counts only):**
1. **The product's own strict check rules out a same-row survivor.**
   - Run specs default to `options.verify = "strict"` (`jobs_models.py:240`).
   - Under strict, `BatchTransformer` raises `LeakageError` and writes nothing when any row's planned value
     comes out unchanged (`fixed_point`, `batch.verify_row`). CUSTOMER_ID is not in `CHANCE_EQUAL_ENTITIES`.
   - Both failing jobs succeeded and wrote 1 200 documents, so no document kept its own id. Each hit was a
     cross-row equality.
2. **Engine Monte Carlo** (`probe/ff1_collision_mc.py`, the same transform and the same 1 200 seed-7 ids, one
   fresh DEK per trial):
   - 30 000 trials: **426 trials (1.42 %)** had at least one cross-row equality, 431 equalities in total.
   - **0 fixed points** in 36 000 000 encryptions.
   - Each of the two tests therefore fails about 1.4 % of the time. They run in three gate invocations per
     round (whole suite, M2, M3), so a gate round goes red about 8 % of the time.
3. **Pipeline probe** (`probe/s1m3r2_survivor_probe.py`, a passive plugin that records counts only and returns
   the original count):
   - 60 fresh sessions of the two tests: **120/120 passed**.
   - Row-aligned survivors were 0 in every table run.
   - The snapshot method was `fpe_digits` every time.
   - The two tests used **different keys in 60/60 sessions** (masked sets compared in-process; only a boolean
     is recorded). Both tests failing in one session (gate 6) was therefore a rare coincidence (about 2·10⁻⁴),
     not a shared key.
4. **Same gate, rerun:** 362 passed, 0 failed (`logs/m2-db-pg-s3-rerun.log`). The two tests also passed in
   gates 4 and 7.

**Why it blocks:** the M2 gate as specified failed on the merged tree. The oracle is nondeterministic by
construction, so every later gate round (M4 merge, release DoD, final verification) has about an 8 % chance of
going red for no product reason. A red-on-rerun-green gate is not "every gate green".

**Fix (test-only; do not relax the privacy property):**
- In `tests/connectors/test_mongo_pipeline.py`, make the identifier check **row-aligned**:
  - Table test: align documents by the kept `_id`.
  - Append test: align each appended document with its source document. Use the kept `accounts[].balance`, as
    the fix-round-1 live probes did, or reverse the masked id with the key material that `submit_run`
    returns.
- Assert **row-aligned survivors == 0**.
- Keep the any-row comparison only for textual leaves (names, e-mails, address lines), following
  `tests/e2e/test_m3_three_way.py` (`_same_row` plus textual-only `_survivors`).
- Optionally report cross-row collisions for FPE id paths with a statistical bound.
- Store the count in a variable before asserting. Pytest's assertion rewriting printed the seed-7 synthetic
  profiles (name, mobile, address) into the failure output. My copy of that log was redacted (2 lines).
- Same latent pattern at a lower rate (about 0.1 % per run): `tests/connectors/test_oracle_pipeline.py:310`
  checks `_survivors(..., "CUSTOMER_ID")` over 300 FF1-masked 8-digit ids (`create_customers` start
  10 000 001). Make it row-aligned too.

## 4. Extra checks

### 4.1 Fresh venv with every extra (programme plan §2.5 step 3)

I ran `uv venv` and then `uv pip install -e ".[ner,fpe,vertex,docs,db,connectors,uitest,e2e,tabular,synth-ml,dev]"`
in `~/.cache/dps-build/s1-m3-gates-r2/venv-all`, with a fresh install of 233 packages. The editable install
wrote nothing into the checkout: the `git status --ignored` listing was unchanged.

- `ruff format --check` / `ruff check`: clean.
- **`mypy dps` with every extra installed: no issues in 134 files.**
- `pytest -q tests/test_mostly.py tests/test_mostly_linked.py tests/test_ner_presidio.py tests/test_playground_samples.py`:
  **92 passed, 2 skipped** (spaCy model), 4:47. This includes the real MOSTLY SDK train/generate tests and the
  pandas narrowing test that `.venv` skips.

### 4.2 Logs (counts only; `logscan_r2.py` → `logscan.json`, container logs since the 20:53 recreate)

- **Needles:** 185 258 source values of 8+ characters from:
  - PG `bank` customers, accounts, addresses, cards, contacts, kyc_documents and loan_applicants;
  - Oracle `CRM.CUSTOMERS`;
  - MongoDB `crm.profiles` leaves.

  Also 1 730 first names of 6+ letters (whole word) and the 2 local demo secrets (bootstrap API key, tokenizer
  token).
- **Results:**
  - **0 value hits, 0 first-name hits and 0 secret hits** in `app` (231 lines), `worker` (129), `tokenizer-mock`
    (181) and `azure-ocr-mock` (313).
  - 0 traceback lines and 0 error- or critical-level lines.
  - Warnings: 2 × `auth_rejected` in `app` (the "auth required" check of each smoke run).
  - The planted control was found.

### 4.3 Residue (counts only)

- 0 `dps_test_*` / `dps_fresh_*` databases on dps-db, 0 `dps_t_*` MongoDB databases and 0 `DPS_T_*` Oracle
  users.
- 1 `t_*` schema remains in `bank`: `t_e4a47902`, 1 table, 400 rows. Its data file dates from 19:32 IST, which
  is during fix round 1 and before this round. It was left in place (see N4).

## 5. Comparison with round 1 and fix round 1

| Gate | gates r1 (`144cc65`) | fix r1 final | **gates r2 (`5c11e49`)** |
|---|---|---|---|
| whole suite | 2617 / 8 skip | 2665 / 8 | **2665 / 8** |
| M1 | 139 / 2 | 139 / 2 | **139 / 2** |
| M2 | 341 | 362 | **360 + 2 failed (B1); rerun 362** |
| M3 | 162 / 1 | 174 / 1 | **174 / 1** |
| browser | 9 | 9 | **9** |
| conformance | 29 / 1 | 29 / 1 | **29 / 1** |
| vitest | 232 | 234 | **234** |
| smoke M0–M2 / `--m3` | 34 / 41 | 34 / 41 | **34 / 41** |

B1 is not a regression introduced by `5c11e49`. The fix round did not touch `test_mongo_pipeline.py`, whose last
changes are `e672072` and `30e0595`. Its `batch.py` change adds name-word spans and leaves the FF1 path and the
`fixed_point` rule as they were. The flake has existed since the module was written. This round is the first recorded
failure: no `FAILED tests/connectors/test_mongo_pipeline.py` line appears in any log under `~/.cache/dps-build/`.

## 6. Non-blocking notes

- **N1 (A-N2, known; F4 polish).** The console tests still write their screenshots into the tracked
  `docs/superpowers/build/m3-shots/`. This run's 54 files were backed up and restored, and main is clean.
- **N2 (F1 input).** `npm ci` reports 8 advisories: 5 moderate, 1 high, 2 critical. The count is unchanged from
  round 1. Round 1's `--omit=dev` split found only 2 moderate advisories in the production tree, both
  `react-router`; I did not rerun that split.
- **N3 (cosmetic).** The Vite main chunk is 558 kB (above 500 kB). Every pytest run emits one
  `StarletteDeprecationWarning`.
- **N4.** The leftover scratch schema `bank.t_e4a47902` comes from fix round 1, probably its reverse-applied
  before-fix run. It is harmless and was not dropped. Drop it at the next cleanup.
- **N5 (state for the lens verifiers).**
  - The demo sources are at **demo size**. The rerun reseeded CRM.* and crm.profiles at test size, so I
    reseeded twice more; the last run printed `unchanged` everywhere.
  - The newest `--m3` smoke group is `smoke-m3-1791993108` (`grp_01M4XHSP1VSYR0KSB07JJAC5WT`, 21:21:48 IST). It
    rewrote `bank_uat.customers_smoke`, `crm_uat.profiles_smoke` and `CRM_MASKED.CUSTOMERS_SMOKE` under its key.
    HANDOFF trap 7 applies: rerun `--m3` rather than re-checking an older group.
  - The test runs also created test-named groups (`m3-dialog-*`, `crm-uat-refresh-*`) through the live app.
  - The stack is running on the final image `c18bced81712`. Pass `DPS_TOKENIZER_MOCK_PORT=8430` to any
    `compose up`.

## 7. Blocking findings

1. **B1:** `tests/connectors/test_mongo_pipeline.py` `survivors()` is not row-aligned, so FF1 cross-row
   collisions on `$.customerId` fail the M2, M3 and whole-suite gates about 1.4 % per test per run. The M2 gate
   was red on its first run and green on rerun. Fix: the test-only, row-aligned check in §3 (and
   `test_oracle_pipeline.py:310`), with row-aligned survivors still required to be 0.
