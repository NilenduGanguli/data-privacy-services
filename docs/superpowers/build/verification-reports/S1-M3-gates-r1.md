# S1-M3 — post-merge gates on main after the M3 merge (GATES verifier, round 1)

**Verdict: PASS** — every gate green on the merged tree; no blocking finding.

| | |
|---|---|
| Date | 2026-10-14, 17:12–17:41 IST |
| Tree verified | `main` @ `144cc65` (clean). Contains the M3 merge `b878c50`, the integration fixes `e672072..e298f70` and docs-only commits after them (`73b2965`, `6dfa682`, `f6f8a46`, `6fa2da5`, `64c46e3`, `144cc65`). No `dps/`, `tools/`, `docker/`, `frontend/` or `pyproject.toml` change since the integrator's image build. |
| Checkout / Python | `/Users/neelu/document_intelligence/data-privacy-services`, `.venv` (Python 3.12.13; ruff 0.17.0, mypy 2.4.0, pytest 9.1.1, oracledb 26.0.0, pymongo 4.18.3); Node 22.20.0 / npm 10.9.3 |
| Test endpoints | contract §12.2 defaults (`~/.cache/dps-build/s1-m3-gates-r1/env.sh`: 5441 / 5442 / 1522 / 27018 / 9010 / 5016 / 8410 / 8400) |
| Logs | `~/.cache/dps-build/s1-m3-gates-r1/logs/` (one file per gate) |
| Files modified | none (see note N1: the console tests overwrite tracked screenshots; restored) |

## 1. Stack

```bash
DPS_TOKENIZER_MOCK_PORT=8430 docker compose --profile docs --profile sources --profile oracle --profile tokenizer up --build -d
```

- Docker 29.5.3, 16 CPUs, 35 GiB (ready on the first poll). Ports 8400/5441/5442/9010/9011/1522/27018/5016/8410/8430 were
  free before the start. Image build: every layer cached (same build context as the integrator's 16:05 build).
- `tokenizer-mock` is published on host **8430**, not 8420 (HANDOFF §12/§14 trap: the M4 lane's demo-site uses 8420).
  **The lens verifiers must pass `DPS_TOKENIZER_MOCK_PORT=8430` to any `docker compose up`** on this project, or compose
  recreates `tokenizer-mock` on 8420. (`ps`, `logs`, `exec` do not need it.)
- `/readyz` polled (never `--wait`): `ready: true` after ~10 s — `db` up (migrations verified), `jobs` ready,
  `tokenizer` up, `ocr` up (azure-read-v3.2), `fpe` ff1, `keys` db+static, `ner` patterns-only (no spaCy model).
- `docker compose ps -a` (start and end of the run): `app`, `worker`, `dps-db`, `source-pg`, `minio`, `oracle`, `mongo`,
  `azure-ocr-mock`, `tokenizer-mock` running + healthy; `migrate`, `minio-init`, `demo-secrets`, `seed-sources` exited 0.
  No restarts, no OOM kills (Oracle at 2.39 / 2.64 GiB).
- Migrations on main: 001–010 (M1–M2), 020 (M3), 040–045 (M5); each number once.
- **Left running** for the lens verifiers.

## 2. Gates

| # | Gate | Result | Time |
|---|---|---|---|
| 1 | `ruff format --check dps tests tools` | 375 files already formatted | — |
| 2 | `ruff check dps tests tools` | All checks passed | — |
| 3 | `mypy dps` | Success: no issues found in 134 source files | — |
| 4 | `DPS_TEST_REQUIRE_SERVICES=1 pytest -q` (whole suite, stack up; nothing deselected) | **2617 passed, 8 skipped**, 0 failed | 9:34 |
| 5 | `DPS_TEST_REQUIRE_SERVICES=1 pytest -q -m "docs or ocr"` (M1) | **139 passed, 2 skipped** | 0:13 |
| 6 | `DPS_TEST_REQUIRE_SERVICES=1 pytest -q -m "db or pg_source or s3"` (M2) | **341 passed** | 7:33 |
| 7 | `DPS_TEST_REQUIRE_SERVICES=1 pytest -q -m "oracle or mongo"` (M3) | **162 passed, 1 skipped** (capability skip) | 4:16 |
| 8 | `DPS_TEST_REQUIRE_SERVICES=1 pytest -q -m browser` (console checks on main) | **9 passed** (M2 ×3, M3 groups ×3, M3 wizard ×3) | 1:49 |
| 9 | Conformance on all three engines (M3 DoD 5): `test_conformance_{postgres,oracle,mongo}.py -m "pg_source or oracle or mongo"` | **29 passed, 1 skipped** (capability skip) | 0:03 |
| 10 | `cd frontend && npm ci && npm test && npm run build` | npm ci OK; node tests 42/42; vitest 16 files / **232 passed**; build (`tsc --noEmit && vite build`) OK | 0:10 |
| 11 | `tools/seed_sources.py --target all --rows 20000 --seed 7`, twice (M3 DoD 3) | run 1 restored CRM.* / LOANS.APPLICANTS / crm.profiles to demo size (the M3 tests reseed at test size); run 2: every Oracle and MongoDB table `unchanged`, PG `already seeded (seed 7, 20000 customers)` | 0:15 |
| 12 | `DPS_SMOKE_REQUIRE_OCR=1 python tools/smoke_test.py` (M0–M2) | **all 34 checks passed** | 0:25 |
| 13 | `DPS_SMOKE_REQUIRE_OCR=1 python tools/smoke_test.py --m3` | **all 41 checks passed** — m3 connections 6/6 tested, discover 3/3, runs 3/3 (20000 rows each, pg/mongo/oracle), **consistency 3/3** (500 sampled, min coverage 1), evidence 3/3 verified under the group's key id, raw PII to LLM 0 | 1:40 |
| 14 | `/metrics` after the smoke runs | `dps_llm_raw_pii_sent_total 0.0` | — |

M3 DoD-named tests confirmed inside gate 7: `test_oracle_clob_astral_roundtrip`, `test_oracle_char_key_paging`,
`test_oracle_timestamp9_key_paging`, `test_three_way_consistency_join`, `test_resume_after_kill[oracle|mongo × in_place|append]`,
and the keyless-table ledger resume. Gate 8 includes `test_console_m3_wizard.py` (3 passed, DoD 8's wizard check).

**Skips (8 in gate 4), all environmental or by design:** 1 capability skip (MongoDB has no composite keys —
`conformance.py:196`, by design per contract §12.3); 2 `tesseract` (no host binary; the app image has one); 3 MOSTLY/pandas
(`synth-ml` extra not in `.venv` — covered by the extra check below); 2 spaCy English model (not installed anywhere;
NER is optional, patterns-only by default). Identical skip set to the integrator's gate run.

### 2.1 Extra check — fresh venv with every extra (programme plan §2.5 step 3)

`uv venv` + `uv pip install -e ".[ner,fpe,vertex,docs,db,connectors,uitest,e2e,tabular,synth-ml,dev]"` at
`~/.cache/dps-build/s1-m3-gates-r1/venv-all` (same ruff/mypy/pytest versions; editable install writes nothing into the
source tree):

- `ruff format --check` / `ruff check`: clean; **`mypy dps` with every extra installed: no issues in 134 files**
  (the check that caught the Vertex typing gap at the M5 merge).
- `pytest -q tests/test_mostly.py tests/test_mostly_linked.py tests/test_ner_presidio.py tests/test_playground_samples.py`:
  **92 passed, 2 skipped** (spaCy model) — the real MOSTLY SDK train/generate tests (`ml` marker) and the pandas narrowing
  test that `.venv` skips all pass (4:52).

## 3. Logs (counts only)

After all gates and both smoke runs: `app` 231 lines, `worker` 131, `tokenizer-mock` 170, `azure-ocr-mock` 297 —
**0 tracebacks, 0 error/critical-level lines** in each. The only app warnings are 2 × `auth_rejected` (the smoke's "auth
required" check, once per smoke run). Content-level log scans for demo values / key material are the lens verifiers' job.

## 4. Comparison with the integrator's gate run (`~/.cache/dps-build/i-m3/logs/gates-r1-*`, 16:10–16:37)

Same counts on every gate: 2617/8 (whole suite), 139/2, 341, 162/1, 9 (browser), smoke 41/41, frontend 232; the test
collection is 2625 in both runs. This run is independent (own stack start after the Docker restart, own env file,
own logs) and adds the M0–M2-only smoke, the conformance subset, the double seed and the all-extras venv.

## 5. Non-blocking notes

- **N1 (A-N2, known; F4 polish).** The console browser tests write their screenshots into the **tracked**
  `docs/superpowers/build/m3-shots/` (hard-coded `SHOTS` in `tests/console/m3_console.py`), so every full or `browser`
  run leaves 54 modified PNGs. To keep "do not modify files", the 54 files this run wrote were copied to
  `~/.cache/dps-build/s1-m3-gates-r1/m3-shots-from-run/` (list: `../restored-shots.txt`) and then restored to `HEAD`
  with `git restore --source=HEAD --worktree -- <those 54 paths>` (explicit pathspec; nothing else changed). Main is
  clean at `144cc65`. Suggested fix: an env override (or `tmp_path`) for `SHOTS`, as M2's shots are git-ignored.
- **N2 (F1 security input).** `npm ci` reports 8 advisories in the console's dependency tree (5 moderate, 1 high,
  2 critical). With `--omit=dev`: 2 moderate (`react-router` / `react-router-dom` 6.x range); the rest are dev/test-only
  (`vitest` mocker, `tinypool`, `esbuild`). Not a DoD gate.
- **N3 (cosmetic).** Vite warns the main chunk is 558 kB (> 500 kB); one `StarletteDeprecationWarning` (httpx with
  `starlette.testclient`) in every pytest run.
- **N4 (state for the lens verifiers).** Demo sources are at demo size (seed 7, 20 000 customers on PG / Oracle /
  MongoDB). The `--m3` smoke created a new `smoke-m3-<epoch>` group and rewrote `bank_uat.customers_smoke`,
  `crm_uat.profiles_smoke` and `CRM_MASKED.CUSTOMERS_SMOKE` under its key (HANDOFF trap 7: re-run a fresh `--m3` smoke
  rather than re-checking an older group). The test runs left no `dps_test_*` database on `dps-db` and no `t_*`
  scratch schema in `bank` (both counted 0 after the gates).

## 6. Blocking findings

None.
