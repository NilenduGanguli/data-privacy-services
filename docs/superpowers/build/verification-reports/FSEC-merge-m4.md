# FSEC-merge-m4 — `m4` merged into `fsec`, the security review's fixes made to hold on the scanner

**Verdict: DONE — merged, consistent, all gates green on the `fsec` stack, committed, stack down.**
Worktree `/Users/neelu/document_intelligence/dps-wt-fsec` (branch `fsec`), 2026-10-15 09:00–10:55 IST.

## 1. Starting state (checked first)

- `fsec` at `804ec7c`, working tree clean, no merge in progress; lane files (`.env`, `.env.lane`,
  `lane.sh`, `.venv`) already present from the S3 setup (ports 8416/5483/5484/9070-71/1526/27022/
  5066/8426/8436/9416), git identity `NilenduGanguli <nilendu.ganguli@gmail.com>` (global, no local
  override), no hooks.
- `m4` at `cbbac38` (S2 fix round 2), clean. The computed task's STATE said `m4` had a main→m4 merge
  in progress (MERGE_HEAD): that was stale — `m4` already contains main as of `144cc65`
  (`4e55da3`/`b34dd07`) and no MERGE_HEAD exists. The merge base of `fsec` and `m4` is `64c46e3`.
- No earlier attempt of this job (no report, no partial merge).

## 2. The merge — `c1d8c36` "merge: m4 into fsec (bring in the UI scanner for its security review and T8B)"

266 files from `m4`, 99 from `fsec`; 16 files touched on both sides. Three conflicts, both sides kept:

| File | Resolution |
|---|---|
| `docs/superpowers/plans/2026-10-11-m1-m5-contract.md` | migration table: M4's row (with `dps_scan_event`) + fsec's M5 row (with `046_job_group_kcv.sql`); §9: M4's scan-spec paragraph + fsec's S5 paragraph (wording: S5 wiring follows on `fsec` after this merge, with Task 8B) |
| `dps/layers/connectors/discovery.py` | union of the imports (`PatternTooSlow` + M4's `CHANNEL_NAMES`/`MIXED_*` names) |
| `dps/posture.py` | both rules kept; fsec's F-11 tokenizer allow-list rule gated by `serves` like M4's tokenizer-token rule (the browser-worker never tokenizes: `tokens_for` passes no adapter, `external_tokenize` fails closed) — without the gate M4's `test_the_browser_worker_needs_no_api_ocr_or_tokenizer_secret` fails |

Auto-merged hotspots reviewed against both parents: `dps/app.py` (fsec's `_CallScope`, S11 no-store,
S1 `validation.install`, pattern processes + M4's browser readiness probe, scans router, CSP `blob:`),
`config.py` (M4's scanner settings — no secret among them), `db.py` (scan tables in `TENANT_TABLES`),
`jobs.py`/`jobs_models.py`/`routers/jobs.py` (F-01 retry scope + M4's scan-kind scope checks),
`evidence.py`, `observability.py`, `pyproject.toml` (`regex` core dep + `scanner` extra), compose
(profiles docs, sources, oracle, scanner, tokenizer, full; every host port a variable), migrations
030, 031, 040–046 with no duplicate number.

## 3. Making `fsec`'s work hold on what came in (security review §3.24)

Every fix of the S3 review was checked for a twin on the scanner's paths. Failing-first evidence: the
7 new tests failed before the fixes (`test_scanner_paths.py` run at 09:25: F-07 `-2+3+cmd|…` kept
unguarded, F-10 `count_field_metrics` missing + raw label in the crawler, browser-worker without
`configure_pattern_processes`, F-14 scan left queued after the refused audit, F-18 the deny-list
sentinel in five reads, F-21 422 `idempotency_key_reused`; the F-02 scan-job case escaped the handler).

| Fix | Scanner path | Result |
|---|---|---|
| F-02 | pattern past budget in a crawl | **fixed**: `ScanRunner.run` records `RunOutcome.error_code`, the scan ends `failed`/`custom_pattern_too_slow` at once (no retry, key purged, failed pack names the code, counts saved, no pattern run after the refusal); browser-worker sizes matcher processes from `DPS_PATTERN_PROCESSES` |
| F-07 | scan inventory/export CSV | **fixed**: L2's `+`/`-` rule (lone sign still guarded) |
| F-10 | `dps_scan_fields_total{entity_type}` | **fixed**: custom types labelled `custom` |
| F-14 | `POST /scans` once-only key | **fixed**: `discard_unannounced` before the 503 |
| F-18 | scan policy body in `dps_job.spec` / reads | **fixed**: label in spec, body sealed in `spec_enc` (`seal_scan_policy`), reopened by `stored_policy` in `open_runtime`, `_policy_name`, finalize-without-runtime |
| F-21 | `POST /scans` replay across a KEK rotation | **fixed**: fingerprint alternates |
| F-11 | browser-worker posture | **fixed in the merge** (gate by `serves`) + test names the allow-list as the app's/worker's rule |
| T0 | contract-surface guard | strict `xfail` XPASSed after the merge → M4 modules/routes moved into `SURFACE`/`ROUTES` |
| S1 | scan-spec refusals | held (+ test); M4's codeless 422 became the app's scrubbed 422 (`code: invalid_request`): M4 test, contract, `api-m4.md` updated |
| S7 | scan object routes cross-tenant | held (+ `test_cross_tenant.py` walks a scan's routes) |
| S2, S6, S11, F-06, F-09/S13 | settings, route scopes, no-store, RLS posture, logging | held without change |
| F-01, F-08, F-12, F-13, F-19, F-20, F-22 | — | not applicable (reasons in review §3.24; F-20: a scan seed draws synthetic personas only, the DEK is wrapped) |

## 4. Commits on `fsec` (author NilenduGanguli, no trailer — verified)

`c1d8c36` merge · `cf4f66f` test: contract-surface guard lists M4 · `dae4665` fix: F-02/F-07/F-10/F-14/
F-18/F-21 on the scanner's paths (+ M4 test adaptations) · `9bf03d1` test: S7 scan routes, posture
allow-list · `c450a6e` docs: security review §3.24 (+ S1/S5/S7/F-03 rows, §3.23 commits, §5, §6), contract,
`docs/technical/api-m4.md`, HANDOFF (header + new §17 + pattern bullet), `M5-DEVIATIONS.md` section.
The owner's autopush loop already pushed `fsec` up to `9bf03d1` (autopush.log 10:52); gitleaks over the
remaining `c450a6e`: no leaks. I did not run `autopush.sh` myself.

## 5. Gates on the `fsec` stack (images `data-privacy-services:fsec` + `-browser:fsec` rebuilt; `docker compose --profile full down -v && up -d`; `/readyz` ready, browser 1 online, one-shots exited 0)

| Gate | Result |
|---|---|
| `ruff format --check dps tests tools` / `ruff check` / `mypy dps` | 461 files formatted / all passed / 155 files, no issues |
| `pytest -q` (whole suite, stack up) | **3796 passed, 64 skipped, 0 failed** (20 min) |
| `-m "docs or ocr"` (REQUIRE_SERVICES=1) | 139 passed, 2 skipped (host has no `tesseract` binary; the image does) |
| `-m "db or pg_source or s3"` | 425 passed, 1 failed → `tests/console/test_console_m2.py::test_in_place_on_a_table_with_an_entity_typed_key`: demo connection `bank-restore-rw` missing (the module says "run tools/smoke_test.py first"); after the smoke: `test_console_m2.py` **3 passed** |
| `-m "oracle or mongo"` | 162 passed, 1 skipped (by design: Mongo has no composite keys) |
| `-m browser` | 184 passed, 1 failed (the same console prerequisite, passed after the smoke), 53 skipped (tesseract) → with `DPS_TEST_TESSERACT_CMD` (wrapper `~/.cache/dps-build/fsec-m4/tess.sh`, `docker run --network none data-privacy-services:fsec tesseract`) and `DPS_TEST_REQUIRE_TESSERACT=1`: `test_capture_ocr.py` **33 passed**, `test_capture_geometry.py` + `test_crawler.py` **36 passed** |
| `DPS_SMOKE_SCANNER=1 tools/smoke_test.py --m3` (after `seed_sources.py --target all --rows 20000 --seed 7`) | **52/52 passed** — scan 21 pages/63 fields/51 personal; demo site `non_get +0, form_get +0, logout_hits +0, admin_hits +0`, `non_get_allowed 0`, blocked POST 60; masked captures 3/3; persona export 0 demo values; evidence submissions 0; M2 runs; M3 3/3 runs + consistency 3/3; `dps_llm_raw_pii_sent_total` 0 |
| frontend `npm ci && npm test && npm run build` | node tests 44/44, Vitest 366/366, build OK, `check-bundle`: no dev fixture code |
| log scan (app, worker, browser-worker logs, 520 lines) | 11 demo-site values: 0 hits; 21 367 distinct `bank.customers` text values: 1 hit, reviewed false positive (`id_type` category with 4 distinct values, equal to an entity-type name in `findings_by_type`); key material / `dps_` keys: 0 |
| gitleaks (`origin/fsec..fsec`) | no leaks |

Logs: `~/.cache/dps-build/fsec-m4/` (`unit-1.log` merge-only run: the 2 expected failures, `unit-2.log`,
`marker-*.log`, `ocr-*.log`, `smoke.log`, `npm-*.log`, `build-*.log`, `gitleaks.log`).

## 6. Housekeeping

- `tests/console/test_console_m3_*.py` rewrite the tracked `docs/superpowers/build/m3-shots/*.png` on
  every run (54 files): restored to HEAD (my own test by-products; not committed). Known polish item.
- `docker compose --profile full down` done (volumes kept); no other project touched.

## 7. For the next step on `fsec` (S3b: Task 8B + scanner part of Task 17)

- S5 wiring is still open (`check_scan_url` at `POST /scans`, `--host-resolver-rules`). Note: M4's
  model-level `url_invalid` already answers a non-http(s) scheme or userinfo before `check_scan_url`
  would (`host_forbidden`); decide which code wins and align the contract.
- `metric_entity_label` labels DPS's own structural types `MIXED_PII`/`FREE_TEXT` as `custom` on
  `/metrics` (L2 since F-10, now L3 alike) — bounded either way; consider allowing that fixed set.
- Gate order on a fresh stack: run the smoke before `test_console_m2.py`; give the OCR tests a
  tesseract (`DPS_TEST_TESSERACT_CMD`).
