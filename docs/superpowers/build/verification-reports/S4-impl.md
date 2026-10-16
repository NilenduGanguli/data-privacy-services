# S4 — M5 Task 24 (images, full stack, smoke M5 block, demo client, flow report) — implementation report

**Stream:** S4 (finish stage, programme plan §2.6 F2 / M5 plan Task 24) · **Worktree:**
`/Users/neelu/document_intelligence/dps-wt-fimg` · **Branch:** `fimg` (from `main` `64c46e3`) ·
**Commit:** `74625d6` "feat: arbitrary-UID images, M5 compose settings, smoke-test M5 checks, value-free
flow report" (author NilenduGanguli <nilendu.ganguli@gmail.com>, no trailer, explicit pathspec, 17 files) ·
**Date:** 2026-10-14

## Verdict

Task 24 is implemented on the code as merged so far (M0–M3 + M5) and committed. Every planned
deliverable is in, test-first, with proofs on a live `--profile full` stack run both as uid 10001 and
with every role of the app image as an OpenShift-style arbitrary UID (1000770000:0, read-only root).

**One planned verification is not green, by dependency, not by defect:** the smoke M5 block's last
check, `api responses are not cacheable` (`Cache-Control: no-store` on every `/api/` response), fails
because that header is M5 Task 17 **S11** (security review), owned by stream F1 / lane `fsec`, which
has not reached S11 yet (checked at the end of this run: `git log main..fsec` = only `dc85450` Task 0,
no uncommitted files, no `no-store` middleware). The check is implemented exactly as the plan writes it (not weakened, no skip
switch); its positive case is unit-tested. Everything else passes: smoke 41/42 (48/49 with `--m3`),
flow report exit 0. **Once `fsec` merges, the smoke should be all-green — re-run it after that merge.**

## Worktree preparation

- `git worktree add …/dps-wt-fimg -b fimg main`; git identity verified (global, no local override).
- Git-ignored lane files (patterns already in the shared `info/exclude`): `.env` (DPS_IMAGE_TAG=fimg,
  APP 8417, DB 5493, SOURCE_PG 5494, MINIO 9080/9081, ORACLE 1527, MONGO 27023, OCR 5076, DEMO_SITE
  8427, TOKENIZER_MOCK 8437, WORKER_METRICS 9417, BROWSER_WORKER_METRICS 9427 — all free per `lsof`;
  compose interpolates no other variable that needs a lane value: `DPS_PG_PORT`/`DPS_EXPORT_ROOT` are
  literals inside the compose env, not interpolations), `.env.lane` (m3's format, ports changed, plus
  `DPS_TOKENIZER_MOCK_PORT`; `DPS_WORKER_METRICS_PORT` deliberately **not** exported to the test
  process: it is a Settings field and would change `worker_metrics_port` defaults), `lane.sh` (copied,
  +x).
- `uv venv --python 3.12 .venv`; `./lane.sh uv pip install -e ".[dev,fpe,docs,db,connectors,tabular,e2e,uitest,ner,vertex]"`
  (every extra tests import, mirroring main's venv; not `synth-ml`: never installed by default).
- `cd frontend && npm ci` (no symlink).
- Baseline before any change: ruff format/check clean, `mypy dps` clean, `pytest -q` 2138 passed, 487 skipped.

## What was built (files)

| File | Change |
|---|---|
| `Dockerfile` | `ARG EXTRAS` on both install lines (`…,tabular${EXTRAS:+,$EXTRAS}`) with a `case` guard (exit 2, value never echoed); arbitrary UID: `useradd --uid 10001 --gid 0`, `chgrp -R 0 /app && chmod -R g+rX,g-w /app && chmod -R g=u /app/var && find /app/var -type d -exec chmod g+s {} +`, `ENV HOME=/tmp`; console stage `FROM --platform=$BUILDPLATFORM node:22-slim`; stale `[tabular]` comment fixed |
| `docker/entrypoint.sh` | `umask 0002` (+ note: keep the ENTRYPOINT on OpenShift — `args`, not `command`) |
| `docker-compose.yml` | `DPS_SYNTH_TABULAR_ENGINE`, `DPS_KEY_CACHE_SECONDS=5`, `DPS_REPORT_PDF_ENABLED` in the shared `x-dps-env` anchor (overridable); `app depends_on tokenizer-mock` (`service_healthy`, `required: false`); header documents the `full` run |
| `.gitignore` / `.dockerignore` | `/reports/` (anchored — `dps/layers/reports/` is a tracked package) / `reports` |
| `tools/smoke_test.py` | M5 block, 8 checks (always last), `skip_m5`, `DPS_SMOKE_REQUIRE_M5=1`, key-propagation poll (`DPS_SMOKE_KEY_WAIT_SECONDS`) |
| `tools/demo_client.py` | `DemoError`, `DpsClient` (plan signatures exactly, test-checked), `load_keys`, `route_template` |
| `tools/flow_report.py` | `EXPECT` (85 rows: 75 routes main serves + M4's 10 scanner routes), `TENANT_KEY_EXPECT`, the flow, value-free Markdown report, exit codes |
| `tests/test_images.py`, `tests/test_image_runtime.py`, `tests/test_compose_m5.py`, `tests/test_demo_client.py`, `tests/test_flow_report.py`, `tests/test_tools_m5.py` | 70 new tests; `tests/test_compose.py` adapted to the new FROM line and the `${EXTRAS…}` suffix |
| `HANDOFF.md` | §1 (image, smoke, two new tools), §2 (EXTRAS, arbitrary UID, amd64, sizes), §3 (`full` run), §6 (container runtime), §8 (traps incl. the M4 browser recipe), new §15 (this task, verified results) |
| `docs/superpowers/build/M5-DEVIATIONS.md` | new section "Task 24 … (finish stage, lane `fimg`)" |

## Test-first evidence

1. New tests written first and run red: `test_images.py` (FROM line, EXTRAS lines, guard, perms —
   failures shown), `test_compose_m5.py` (M5 env missing, `tokenizer-mock` dependency missing,
   `reports/` not ignored), `test_tools_m5.py` (`REQUIRE_M5`/`m5_checks` missing), `test_demo_client.py`
   (module missing: ImportError). Then implemented → green.
2. `tests/test_image_runtime.py` (opt-in `DPS_TEST_IMAGE`) **fails on main's pre-change image**
   (`data-privacy-services:local`): arbitrary UID gets `HOME=/` (unwritable) and cannot write
   `/app/var/artefacts` or `/app/var/exports`.
3. **A real gap found on the live stack and fixed test-first:** with only the plan's lines, artefacts
   written by uid 10001 (dirs `10001:10001 0755`) refused a later arbitrary UID: smoke `m2 table run`
   "succeeded storage_error", `m2 parquet export` **dead storage_error** (`finalize_failed StorageError`).
   New tests (static `test_files_written_at_runtime_stay_writable_for_any_uid_of_group_0`, runtime
   `test_a_new_uid_keeps_writing_where_the_previous_one_wrote`) failed (`('0o755', '10001')`), then
   `--gid 0` + setgid + `umask 0002` made them pass, and the stack re-run passed.
4. Flow report: first live run 88/90 — fixed two flow bugs (single grouped run → consistency check
   409 `member_not_ready`: now two grouped runs; evidence record exposes `artefacts[]`, not
   `html_artefact_id`). The fake-stack tests were corrected to the real shapes.

## Gates (worktree, final)

- `./lane.sh ruff format --check dps tests tools` (383 files) · `ruff check dps tests tools` · `mypy dps` (134 files): clean. New tools also pass `mypy tools/demo_client.py tools/flow_report.py`.
- `./lane.sh pytest -q` (stack down): **2203 passed, 492 skipped** (baseline 2138/487; +65 passing).
- Stack up (`--profile full`, every role as 1000770000:0): `DPS_TEST_REQUIRE_SERVICES=1 pytest -q -m "db or pg_source or s3"` **341 passed**; `-m "docs or ocr or oracle or mongo"` **301 passed, 3 skipped** (2× host has no `tesseract`, 1× MongoDB by-design "no composite keys" conformance case).
- Frontend unchanged (no frontend gate needed).

## Images

| Build | Result | Time | Size |
|---|---|---|---|
| `docker compose build app` (arm64, tag `data-privacy-services:fimg`) | exit 0 | 449 s cold (downloads), ~2 s warm | 1.18 GB unpacked, 286 MB compressed; perms layer 86 kB (same as main's) |
| `docker build --platform linux/amd64 -t data-privacy-services:fimg-amd64 .` | exit 0 | 591 s cold (`uv pip install` 368 s), ~1 s warm | 292 MB compressed, 1.17 GB unpacked |

The amd64 build's `frontend` steps were all `CACHED` from the native build → the console stage really
runs on `$BUILDPLATFORM`. `DPS_TEST_IMAGE=… pytest -q tests/test_image_runtime.py`: **3 passed on
arm64 and 3 passed on amd64** (emulated): uid 1000770000:0, `--read-only`, `--cap-drop ALL`,
`no-new-privileges`: every extra imports (dps.app, numpy, scipy, fastfpe, asyncpg, sqlglot, pyarrow,
boto3, oracledb, pymongo, pypdf, pdfplumber, pypdfium2, reportlab, docx, PIL), `tesseract` runs, the
console dist is there, `/tmp`/HOME and both volumes are writable, `/app`, `/app/dps`,
`/app/frontend/dist`, `/app/tools` and site-packages are not, no group-writable file under `/app`
(outside `/app/var`), `/usr/local` or `/etc`; `/health` answers; a new UID writes where uid 10001
wrote. Group-0-writable paths elsewhere in the image: 5 (Debian man dirs, debconf) — identical in
main's image, and the root is read-only anyway. `EXTRAS=synth-ml` was **not** built (torch ≈ 2 GB);
the EXTRAS expansion and guard are tested by running the Dockerfile's real RUN line under `sh`.

## Live stack proofs (lane project `dps-wt-fimg`, `docker compose --profile full up --build -d`)

- `/readyz` ready: api-core, llm-gateway (stub), fpe (ff1), **tokenizer up** (the app now starts after
  the mock is healthy), ocr up (azure-read-v3.2), db up (migrations verified), jobs ready, keys
  db+static; ner patterns-only (no NER in the image, by design). One-shots migrate, minio-init,
  demo-secrets, seed-sources exited 0. Oracle and Mongo healthy.
- **Smoke as uid 10001** (`DPS_SMOKE_REQUIRE_OCR=1 DPS_SMOKE_REQUIRE_M5=1`): 41/42 — all M0, M1 (incl.
  OCR scan + image), M2 (9) and M5 checks pass: `policies with hashes` (6 presets), `reports summary`
  (raw_pii_sent_to_llm=0), `posture report` (11 checks, 5 pass in local), `db key lifecycle` (201,
  `no-store`, authenticated within the 5 s cache, disabled 200), `db key disabled` (401),
  `external tokenization round trip` (`tk_…` via the **tokenizer-mock container from this image** —
  closes the Task 8 deferred item), `422 never echoes input`; FAIL `api responses are not cacheable`
  (S11, see verdict).
- **Every role as uid 1000770000:0** (compose override `user:` on app, worker, migrate, tokenizer-mock,
  seed-sources; all `ReadonlyRootfs=true`; `/proc/1/status`: Umask 0002, Gid 0), same volumes uid 10001
  wrote first: readyz ready; smoke `--m3` **48/49** (M3 block: 6/6 connections tested, 3/3 discovered,
  3/3 runs × 20 000 rows pg/mongo/oracle, consistency 3/3 pairs, 3/3 packs verified, raw PII to LLM 0;
  only S11 fails); flow report exit 0. Runtime-created dirs on the volume: `2775 10001:0`.
  No `PermissionError` / `Permission denied` / `Read-only file system` / `StorageError` in the logs of
  app, worker, migrate, tokenizer-mock, seed-sources, demo-secrets, OCR mock (dps-db logs 7 deliberate
  grant refusals from the integration suite's append-only tests).
- **Flow report** (`python tools/flow_report.py`, operator key): `PASS 92 calls, 0 unexpected` — 82
  as expected, 10 scanner calls skipped (no browser worker on main), as uid 10001 and as the arbitrary
  UID (copies: `~/.cache/dps-build/s4/flow-report-uid10001.md`, `…/flow-report-arbitrary-uid.md`).
  Controls on a finished job answer the documented 409s: `job_not_pausable`, `job_not_paused`,
  `job_terminal`, `job_not_retryable`; both runs succeeded, connection test ok, run spec valid,
  consistency passed. Wall time ~4–5 s.
- **Log scan** (every container log of the lane, 907 kB, plus the flow report): 2 000 sampled source
  values each of full_name, email, mobile, national_id, tax_id → **0** in logs, 0 in the report; the
  smoke/flow sample record, the SSN probe, the bootstrap key, the tokenizer token and key, the demo KEK,
  raw DB keys (`dps_` + 32) and `tk_` tokens → **0**. Saved smoke/flow outputs: 0 hits.
- **Source vs target (counts only)** on `bank_uat.customers_smoke` (20 000), `customers_flow` and
  `customers_flow_b` (500 each) vs `bank.customers`: target values equal to a source value of the
  same column = **0** for full_name, national_id, tax_id, email, mobile, notes in all three. Dates:
  62.0 % of shifted DOBs fall on a day some customer has, vs 63.7 % of the source span's days
  occupied (domain collisions at the base rate; target span extends beyond the source's → shifted).
  FF1 customer_id: 3/20 000 collide with other customers' ids in the smoke table, 0 in the flow tables.
  Kept columns (country_code, segment, risk_score, kyc_status, id_type, relationship_manager_id) are
  kept by policy (non-personal).

## M4 slot-in (browser-worker and compose scanner services)

- Compose: `full` already covers every profiled service; `tests/test_compose_m5.py` pins it and pins
  that a `browser-worker` inherits the M5 env and a read-only root (M4's `extends: worker` does both).
- `tests/test_images.py::test_the_browser_image_is_arbitrary_uid_safe` (skipped until
  `Dockerfile.browser` exists on the branch) **will fail at the M4 merge until the recipe is applied**
  — checked: it fails on branch `m4`'s file and on the plan-literal variant, passes with the recipe.
  The recipe (HANDOFF §8), as a diff against `m4:Dockerfile.browser`:

  ```diff
  -RUN useradd --create-home --uid 10001 appuser \
  +RUN useradd --create-home --uid 10001 --gid 0 appuser \
       && mkdir -p /app/var/artefacts /app/var/exports \
       && chown -R appuser /app/var
  +COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
  +RUN chmod 0755 /usr/local/bin/entrypoint.sh \
  +    && chgrp -R 0 /app && chmod -R g+rX,g-w /app && chmod -R g=u /app/var \
  +    && find /app/var -type d -exec chmod g+s {} +
  +ENV HOME=/tmp
  +ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
   USER 10001
  ```
- Probe image built from `git archive m4` + recipe (scratch tree, not committed; tag removed after):
  Chromium (Playwright 1.55.0) launches as uid 10001 **and** as 1000770000:0 with `--read-only`,
  `--cap-drop ALL`, `no-new-privileges`, shm 1g; umask 0002, HOME /tmp, artefact volume writable,
  `/ms-playwright` not writable; permissions layer 20 kB, image 4.24 GB (= m4's). **`m4`'s own image as
  an arbitrary UID runs Chromium but cannot write the artefact volume** (HOME `/`, umask 0022) — a real
  M4 gap that the recipe fixes.
- **Finding:** the Playwright base image ships `/ms-playwright` mode **0777** throughout (631 entries,
  incl. the chrome binary). The plan's `chgrp -R 0 /ms-playwright && chmod -R g=u /ms-playwright`
  (also `go-w`) copies every browser file into a new layer: **measured +1.46 GB (4.24 → 6.2 GB)**. So
  the recipe leaves `/ms-playwright` alone and the browser-worker **must** run with a read-only root
  (compose inherits `read_only: true`; Task 25 must set `readOnlyRootFilesystem: true`).

## Deviations (details in `M5-DEVIATIONS.md` "Task 24")

1. Arbitrary UID: `g=u` only on `/app/var` (plan: all of `/app` — would make code writable to every OpenShift UID); code `g+rX,g-w`.
2. Added `--gid 0`, setgid `/app/var`, entrypoint `umask 0002` (measured storage_error otherwise); `docker/entrypoint.sh` not in the plan's file list.
3. Console stage on `$BUILDPLATFORM` (addition); EXTRAS on both install lines + input guard.
4. `Dockerfile.browser` not on main → guard test + recipe; no recursive chmod of `/ms-playwright` (+1.46 GB).
5. Compose M5 settings in the shared anchor, overridable (plan: literals on app and worker); `app depends_on tokenizer-mock` added.
6. `/reports/` anchored (plan: `reports/`); `.dockerignore` gains `reports`.
7. Smoke: 8th check `db key disabled` (plan's code has 7 for "8 checks"); poll instead of `sleep(6)`; 64-hex hash check; `DPS_SMOKE_REQUIRE_M5`.
8. demo_client: signatures exact; additions (`route_template`, `url_path`, properties, `close`/context manager, `_transport` hook); error code in the message; `load_keys` also refuses world-writable and accepts a `{"keys": …}` wrapper.
9. flow_report: operator key by default, demo keys opt-in (`--demo-keys`, tenant-scoped overrides); outcome checks; two grouped runs; error codes in the report; tools import `tools.demo_client` (repo root on `sys.path`, as `smoke_test.py` does).
10. Contract unchanged (plan lists no contract change for Task 24).

## Observations for other streams (not fixed here — outside Task 24)

- **F1 / fsec (T17 S11):** the smoke's last check needs `Cache-Control: no-store` on every `/api/`
  response; currently only a few routes set it (admin keys, artefacts, jobs run, groups, reports PDFs).
- **F2 OpenShift (T25):** keep the image ENTRYPOINT (use `args`, not `command`) or `umask 0002` is
  skipped; set `readOnlyRootFilesystem: true` on every role — mandatory for the browser-worker
  (`/ms-playwright` is 0777); arbitrary UIDs need nothing else from the manifests (no `runAsUser`).
- **M4 merge:** apply the Dockerfile.browser recipe (guard test fails until then).
- **F4 polish / M2:** a finalize `StorageError` (evidence artefacts not stored) leaves a run
  `succeeded` with `error_code storage_error`, and the smoke's `m2 table run with evidence` still
  passes because the HTML route re-renders from the pack — consider failing that check on
  `error_code == "storage_error"` (not changed here: a succeeded job may keep an older transient code).
- Volumes written by images older than this change hold `10001:10001 0755` dirs: re-permission once
  (HANDOFF §8 command) or `down -v` before running another UID on them.
- Known trap unchanged: `DPS_TOKENIZER_MOCK_PORT` default 8420 = the m4 lane's demo-site port.
- **Every stream that merges after this branch:** a new API route needs a `tools/flow_report.py`
  `EXPECT` row (`tests/test_flow_report.py::test_every_route_the_app_serves_is_in_the_table` fails
  otherwise); M4's scanner rows are the templates of branch `m4` (`/api/v1/scans/{scan_id}/…`,
  `/scans/personas/preview`, `/events`) — if the M4 pre-merge renames one, fix the row.
- **F3 (fdemo):** `tools/demo_client.py` matches the plan's `DpsClient`/`DemoError`/`load_keys`
  signatures exactly (test-pinned); `load_keys` reads `{name: key}` (or `{"keys": {…}}`) and refuses a
  file other users can read or write — `demo_seed.py` should write `var/demo/demo-keys.json` 0600 in
  that shape.

## State left behind

- Branch `fimg` at `74625d6`, worktree clean (the 54 tracked M3 screenshots the M3 console tests
  rewrote during the integration run were restored byte-for-byte from `HEAD`; nothing of them committed).
- Lane stack **down** (`down -v`, project `dps-wt-fimg` only); no lane containers or volumes remain.
  Images kept: `data-privacy-services:fimg`, `data-privacy-services:fimg-amd64`,
  `document_intelligence-azure-ocr-mock:fimg` (a tag of the cached `:latest`).
- **Not pushed:** `tools/dev/autopush.sh` was not run (the project CLAUDE.md requires the owner's
  explicit request for pushes; the owner's autopush loop pushes `fimg` when it runs). Its checks were
  run by hand and pass: author `nilendu.ganguli@gmail.com`, no trailer, gitleaks on `main..fimg`
  "no leaks found" (exit 0); `git show HEAD` has no AKIA / private-key / raw `dps_`+32 key pattern.
- Logs and artefacts: `~/.cache/dps-build/s4/` (build logs, smoke/flow outputs, pytest logs, the
  arbitrary-UID compose override, the log-scan script).
