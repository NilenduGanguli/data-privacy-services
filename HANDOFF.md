# HANDOFF — data-privacy-services (DPS)

**M3 (Oracle + MongoDB, cross-system job groups) integrated on `main` (2026-10-14)** — built on
lane `dps-wt-m3` (branch `m3`, lane verification r2 PASS) and merged with `--no-ff`; §13 holds the
run commands, the Oracle and MongoDB rules, traps and measured throughput, §14 the merge, the
adaptations to main's newer behaviour and the gates of the merged tree. The `v0.4.0-m3` tag follows
the verification on main (programme plan §2.5).

**Integrated on `main` (2026-10-12): M1 (v0.2.0, branch `m1`) and M2 (`v0.1.0-m2`, branch `m2`)**
— merged with `--no-ff`; the gates of the integrated tree are recorded in §11, and the fixes of the
integrated verification round 1 (raw PII kept as "not personal"; account numbers and customer ids
in free text) with their gates in §11.1. The lane records of each milestone and of M0 follow
unchanged.

**M1 (v0.2.0) verified 2026-10-12 on branch `m1`** (lane `dps-wt-m1`: `DPS_APP_PORT=8401`,
`DPS_OCR_PORT=5016`, `DPS_IMAGE_TAG=m1`) — see §10: `ruff format --check` / `ruff check` on
`dps tests tools`, `mypy dps`, `pytest -q` (1009 passed, 4 skipped: `tesseract` ×2 without a host
binary, `ner` ×2), `cd frontend && npm test && npm run build`, `docker compose --profile docs up
--build -d` (both services healthy, `/readyz` `ocr: up, engine azure-read-v3.2`),
`DPS_TEST_REQUIRE_SERVICES=1 pytest -q -m "docs or ocr"` (131 passed, 2 skipped: the same
`tesseract` cases, which pass inside the image), `DPS_SMOKE_REQUIRE_OCR=1 python
tools/smoke_test.py` (25/25), `python tools/console_check.py` (84/84) and the detector report
regenerated without a change in its numbers.

**M2 verified 2026-10-12** at the commit tagged `v0.1.0-m2` (M2 complete: `fca6cff`, after the
verify r1 fixes `a77d38a` (selections refuse denied functions in attribute notation) and `fca6cff`
(the wizard keeps In place closed while the snapshot rewrites a key), + this HANDOFF update; the tag
moved here from `99ec8c0`; M0 base tag `v0.1.0-m0`) · gates re-run at `fca6cff` on this lane's
rebuilt stack (`COMPOSE_PROJECT_NAME` `dps-wt-m2`, image `data-privacy-services:m2`): `ruff check dps
tests tools`, `ruff format dps tests tools` (no change), `mypy dps` (82 files), `pytest -q` with the
stack up (1152 passed, 2 skipped: the `ner` extra), console `npm run typecheck && npm test && npm run
build` (58 Vitest cases), `docker compose --env-file .env --env-file docker/compose.sources.env
--profile sources up -d --build` (app, worker, dps-db, source-pg, minio healthy; migrate, minio-init,
demo-secrets, seed-sources exited 0; app `/readyz` `api-core`, `db`, `jobs` ok; worker
`:9400/readyz` 200), `DPS_TEST_REQUIRE_SERVICES=1 pytest -q -m "db or pg_source or s3"` (161
passed), the six resume-after-kill cases, `python tools/smoke_test.py` (24/24), `python
tools/demo_m2.py` (12 jobs succeeded, verification passed, 0 orphans), and the M2 log scan with the
console browser check (`tests/test_no_values_in_logs_m2.py tests/console/test_console_m2.py`, 40
passed, the console check's 3 among them). Carried from the first milestone-gate run (compose files,
image build and tools as of `6ea0738`; since then product code changed only in
`dps/layers/connectors/selection.py` and the console wizard): a plain `docker compose up -d --build`
(no profile) under its own project and ports: app, worker, dps-db healthy, migrate exited 0,
`/readyz` `db`/`jobs` ok, `GET /api/v1/jobs` 200, `DPS_SMOKE_M2=off python tools/smoke_test.py`
15/15. Counts and timings in the M2 build report. The M0-only checks of that release (`WITH_NER=1`
image, `linux/amd64` build, `DPS_FPE_ENABLED=false` stack, `coin_tls: verified`) were not repeated
for M2.

Last verified (M0): 2026-10-12 at `629f4db` = tag `v0.1.0-m0` (`48d9425`) + the post-M0 fixes (SEC-1 COIN
mint TLS, O1/O2 locale and phone evidence, O5 session-only console key, lane-safe compose, the D2
idempotency amendment) + the A-prepare review r1 fixes (B1: surrogates never reuse a part of the
record and the sweep checks name parts; B2: lane-safe test endpoints in the plans; this file's refresh
on top changes no code) · gates: `ruff format dps tests`, `ruff check dps tests`, `mypy dps`,
`pytest -q` (588 passed, 2 skipped: the two tests that need the `ner` extra; 5 consecutive runs, plus
40 runs of `pytest -q tests/test_playground_samples.py -k synthesize`, all green), `cd frontend && npm
run build`, `docker compose up --build -d` + `python tools/smoke_test.py` (15/15). Checked at `1bac79b`
and not repeated since: `/readyz` `coin_tls: verified` and a headless browser check of the console's
"Remember key on this device" storage behaviour. Checked on 2026-10-11 during the M0 verification and
not repeated since: the `WITH_NER=1` image (`ner=ok`), a `linux/amd64` build, and the stack with
`DPS_FPE_ENABLED=false` (`det_token` fallbacks reported).

**Update rule (the Wega rule):** update this file in the same change as anything that alters build or
deploy facts — image, ports, env, extras, probes, labels.

## 1. What this repo ships

| Artefact | Kind | Notes |
|---|---|---|
| `data-privacy-services` image | container | FastAPI app + compiled console; one process, port **8400**; non-root uid **10001**. The same image runs three roles: `app` (uvicorn), `worker` (`python -m dps.worker`, M2) and `migrate` (`python -m dps.migrate`, M2) |
| M2 job platform | API + console | `/api/v1` connections, jobs (discover, run with `table` / `files` / `in_place` / `append`, controls), snapshots, job groups, evidence and artefacts (`docs/technical/api-m2.md`); console pages Data sources & jobs (07), job detail (03), the five-step New-job wizard (06), the evidence pack (10) and the evidence list of Reports (09) |
| `dps` package | Python 3.12, version 0.2.0 (M1) | Layer 1 engine + API + documents + the job platform; extras `fpe`, `docs`, `db`, `connectors` (baked in; the app needs `docs` from M1 Task 10 on), `ner` (build-arg `WITH_NER=1`), `vertex`, `uitest` / `e2e` (Playwright 1.55.0 for `tools/console_check.py` and `tests/console/test_console_m2.py`, never in the image), `deck` (python-pptx for `tools/build_deck.py`, the showcase deck; dev only, never in the image) |
| `frontend/dist` | built inside the image | never committed (`.gitignore`, `.dockerignore`) |
| `tools/smoke_test.py` | script | M0's 15 Layer 1 checks + 10 for documents, OCR and schema (M1; the two OCR checks skip when `/readyz` says OCR is not ready, fail under `DPS_SMOKE_REQUIRE_OCR=1`) + 9 job-platform checks when `/readyz` reports the metadata database (M2; rerunnable). `DPS_SMOKE_M2=off` skips the job checks — the plain `docker compose up` stack has the metadata database but no demo source, so they cannot pass there (`on` forces them); prints verdicts, never bodies |
| `tools/seed_sources.py` | script | seeds the 10-table demo `bank` schema (surrogate-engine values, no real PII) and the `bank_restore` copy; idempotent (M2). M3: `--target oracle\|mongo\|all` seeds the Oracle CRM tables (`tools/seed_oracle.py`) and `crm.profiles` (`tools/seed_mongo.py`) from the one customer space of `tools/seed_model.py` — the same customer id carries the same person in all three engines |
| `tools/bench_connector.py`, `tools/demo_m2.py`, `tools/reveal_rows.py` | scripts | measured rows/s per write mode (results in `docs/technical/bench/`; M3: `--engine oracle\|mongo`, §13); the 10-table demo (group key and seed kept in `var/demo/`, git-ignored); local reveal of output rows with the caller's key (checks the evidence's key id; no network, except for `external_tokenize` columns: the key holder's own bank-tokenizer client, `--tokenizer-url` / `--tokenizer-token-file`) |

## 2. Build

```bash
docker build -t data-privacy-services:local .                                      # what compose builds (tag = ${DPS_IMAGE_TAG:-local})
docker build --platform linux/amd64 -t <registry>/data-privacy-services:<semver> .   # amd64 from a Mac
docker build --build-arg WITH_NER=1 -t <registry>/data-privacy-services:<semver>-ner .
```

Tag semver + git sha. The image (app, worker and migrate all run it):

- stage 1 `node:22-slim`: `npm ci` (lockfile only, no `npm install` fallback) + `npm run build`;
- stage 2 `python:3.12-slim`: `tesseract-ocr` + `tesseract-ocr-eng` from apt and
  `ENV DPS_OCR_PROVIDER=auto` (M1: local OCR when no Azure endpoint is configured),
  `uv` 0.11.21 (pinned, copied from `ghcr.io/astral-sh/uv:0.11.21`),
  `uv pip install --system --no-cache -e ".[fpe,docs,db,connectors,tabular]"` (`tabular` = M5's
  `numpy>=2.0` + `scipy>=1.13` for the Gaussian-copula engine and the fidelity/privacy metrics;
  `ner` + `en_core_web_sm` added when `WITH_NER=1`; the optional `synth-ml` extra, MOSTLY AI, is
  never installed in an image built from this Dockerfile: adding it is a bank-side opt-in after the
  licence and image-size review (torch ≈ 2 GB; `uv pip install -e ".[synth-ml]"` in a derived
  image), and without it `options.tabular="mostly"` is refused with `tabular_invalid`), the
  console dist at `/app/frontend/dist`, `tools/` at `/app/tools`, and `/app/var/{artefacts,exports}`
  owned by uid 10001;
- `USER 10001`, `EXPOSE 8400`, a Python `urllib` `HEALTHCHECK` on `http://127.0.0.1:8400/health`
  (15 s interval, 15 s start period);
- `ENTRYPOINT docker/entrypoint.sh` (runs `python -m dps.migrate` first when `DPS_MIGRATIONS_MODE=auto`,
  `DPS_PG_HOST` is set and the command is `uvicorn` — single-container app runs; compose uses the
  `migrate` one-shot) and
  `CMD uvicorn dps.app:create_app --factory --host 0.0.0.0 --port 8400 --no-access-log`.

Sizes on 2026-10-11 (arm64): 407 MB; with `WITH_NER=1` 775 MB. On 2026-10-12 (arm64, M1 lane: the
`docs` extra and Tesseract 5.5 with `eng`): 644 MB, of which the apt layer is 107 MB. M2 lane
(2026-10-12, arm64, without M1): 687 MB. The integrated image is in §11.

Development install: `uv pip install -e ".[dev,fpe,docs,db,connectors,tabular,uitest,e2e]"` (`tabular`
is M5's numpy + scipy; the unit tests of the copula engine and the metrics skip without it; `uitest` and
`e2e` both pin Playwright 1.55.0, for the M1 and M2 console checks; then `python -m playwright install
chromium`, a no-op when `~/Library/Caches/ms-playwright/chromium-1187` exists). The console's unit
suites (`cd frontend && npm test`: `node --test` over `frontend/tests/*.test.ts` for M1, then Vitest
over `frontend/src/**/*.test.ts` for the job helpers and the wizard reducer, M2) run in the gates, not
in the image build.

## 3. Run locally (compose)

```bash
docker compose --env-file docker/compose.sources.env --profile docs --profile sources up -d --build
docker compose ps                     # poll until dps-db, source-pg, minio, azure-ocr-mock, app, worker are
                                      # healthy and migrate, minio-init, demo-secrets, seed-sources exited 0
DPS_SMOKE_REQUIRE_OCR=1 python tools/smoke_test.py   # waits for /health (DPS_SMOKE_WAIT_SECONDS, default 90),
                                      # then 34 checks: 15 Layer 1 + 10 documents/OCR/schema (M1) + 9 job platform (M2)
docker compose up -d --scale worker=3 worker                         # more workers
docker compose --profile docs --profile sources down
```

That is the whole local stack (profile `full` is the same set). Smaller stacks: plain `docker compose
up --build -d` runs app, worker, metadata DB and migrate (then `DPS_SMOKE_M2=off python
tools/smoke_test.py`: 25 checks with the two OCR checks skipped — the job checks need the
`sources` profile's demo source); `--profile docs` adds the OCR mock, `--profile sources` the demo
source, MinIO and the demo secrets. M3's sources alone: `docker compose --profile oracle --profile sources up -d oracle mongo`, then poll `docker compose ps` until both are healthy (never `up --wait`, which fails on the exited one-shots) and run `DPS_TEST_REQUIRE_SERVICES=1 pytest -q -m "oracle or mongo"` with the lane's port variables exported.
The M3 end-to-end tests (`tests/e2e/`: shared seed ids, the three-way consistency join through the API
and a worker subprocess, resume after kill on Oracle and MongoDB for `in_place` and `append`) need
`dps-db`, `source-pg`, `oracle` and `mongo` (`docker compose --profile oracle --profile sources up -d
dps-db source-pg oracle mongo`), then `DPS_TEST_REQUIRE_SERVICES=1 pytest -q tests/e2e`
(about 3 minutes). They seed `CRM.*`, `crm.profiles` and a PostgreSQL copy in schema `m3e2e`
themselves and leave `bank` alone. MongoDB is a run source since M3 Task 19 (discovery; `table`, `in_place`, `append` proven; `files` not yet tested for documents); without NER, discovery does not classify nested address fields of a document (no
column name to go by): map them in review.


**Measured throughput (M2, 2026-10-12; image of `17e14a2`, tools of `8204191`)** — `python
tools/bench_connector.py --rows 200000 …` against this compose stack on an Apple-silicon Mac (18 CPUs; Docker Desktop VM with 12 CPUs and ~15 GiB),
`DPS_FPE_ENABLED=true` (FF1), 200 000 synthetic customers (16 columns, 8 transformed), 4 partitions
of 50 000 rows, 1 000-row batches, worker concurrency 4. Wall clock from submission to the terminal
state (prepare, preflight and finalize included); results in `docs/technical/bench/`. The numbers
are measured, not promised (spec §7):

| Run | Workers | Rows/s | Wall |
|---|---|---|---|
| obfuscate → `table` (created UAT table) | 1 | 3 004 | 66.6 s |
| obfuscate → `in_place` (`bench_restore` copy) | 1 | 3 196 | 62.6 s |
| obfuscate → `files` (parquet to MinIO) | 1 | 2 912 | 68.7 s |
| synthesize → `table` | 1 | 2 021 | 99.0 s |
| obfuscate → `table` | 3 | 4 128 | 48.5 s |
| obfuscate → `table`, 12 partitions (`--parallelism 12 --partition-rows 17000`) | 3 | 4 828 | 41.4 s |

One worker process is bound by one Python interpreter (the transform runs in a thread under the
GIL): more partitions per worker do not add throughput, more worker replicas do. Oracle and MongoDB
figures (M3): §13 "Measured throughput (M3)". `python
tools/demo_m2.py` (10 tables into `bank_uat` under one job group, then in place and append) took
30 s with 3 workers; every job succeeded with verification passed and every foreign key of the UAT
copy found its parent.

Ports (host side binds `127.0.0.1` only; every one is a lane variable, see "Lane-safe stacks"):
`${DPS_APP_PORT:-8400}` app, `${DPS_DB_PORT:-5441}` dps-db, `${DPS_SOURCE_PG_PORT:-5442}` source-pg,
`${DPS_MINIO_PORT:-9010}` / `${DPS_MINIO_CONSOLE_PORT:-9011}` MinIO, `${DPS_OCR_PORT:-5016}` the OCR mock;
the worker's ops port 9400 stays inside the compose network.

The M2 demo and checks against a running full stack:

```bash
python tools/smoke_test.py                                   # 24 checks (15 Layer 1 + 9 job platform)
python tools/demo_m2.py                                      # 10 bank tables → bank_uat (one job group), in place, append
python tools/reveal_rows.py --help                           # local reveal of output rows with the key (no network but the key holder's own tokenizer for external_tokenize)
DPS_TEST_REQUIRE_SERVICES=1 pytest -q -m "db or pg_source or s3"                      # integration gate
DPS_TEST_REQUIRE_SERVICES=1 pytest -q -m slow tests/connectors/test_resume_after_kill.py
DPS_TEST_REQUIRE_SERVICES=1 pytest -q tests/console/test_console_m2.py                # console vs mockups
```

The console check needs the demo connections the smoke test creates (`bank-pg-ro`, `bank-uat`,
`bank-restore-rw`), runs a real wizard job into `bank_uat.customers_console`, walks In place on
`bank_restore.customers` (blocked while the snapshot rewrites `customer_id`, then a dry run once the
key is kept, so the restored copy is not written), and writes screenshots (dark and light, 1440 and 390
px, beside the mockups) to `docs/superpowers/build/m2-console/` (git-ignored).

Never `up --wait` (it fails on the one-shots that exit 0). `--env-file` replaces the default `.env`:
a lane that keeps its ports and image tag in `.env` passes both files (`--env-file .env --env-file
docker/compose.sources.env`). The first `seed-sources` run takes about a minute (20 000 customers,
~370 000 rows); later runs print "already seeded".

Services (host ports bind `127.0.0.1` only):

| Service | Image | Host port → container | Notes |
|---|---|---|---|
| `app` | `data-privacy-services:${DPS_IMAGE_TAG:-local}` | `${DPS_APP_PORT:-8400}` → 8400 | console + API |
| `azure-ocr-mock` (M1, profiles `docs`/`full`) | `document_intelligence-azure-ocr-mock:${DPS_IMAGE_TAG:-latest}` (build `../mock_azure_ocr`) | `${DPS_OCR_PORT:-5016}` → 5000 | Azure Computer Vision Read v3.2 contract with Tesseract inside; healthcheck `/health`, `mem_limit: 768m` |
| `dps-db` (M2) | `postgres:16-alpine` | `${DPS_DB_PORT:-5441}` → 5432 | metadata DB; `docker/dps-db/initdb/01_roles.sql` creates the contract roles (`dps_owner`, `dps_app_rw`, `dps_worker`, `dps_app`, `dps_worker_login`; local passwords = role name) and the `dps` database (owner `dps_owner`, timezone UTC) on first start; volume `dps-db-data`; superuser `postgres`/`postgres` (local only); healthy once `pg_isready -h 127.0.0.1 -d dps` answers |
| `source-pg` (M2, profiles `sources`/`full`) | `postgres:16` | `${DPS_SOURCE_PG_PORT:-5442}` → 5432 | demo source; `docker/source-pg/initdb/01_bank_schema.sql` creates the `bank` database (schema `bank`: the 10 demo tables; `bank_uat`, `bank_restore`, `bank_perf` for table/in-place/append targets) and the local logins `dps_reader` (SELECT on `bank`) and `dps_writer` (owns the target schemas), password = role name, on first start; volume `source-pg-data`; superuser `postgres`/`postgres` (local only); healthy once `pg_isready -h 127.0.0.1 -d bank` answers |
| `minio` (M2, profiles `sources`/`full`) | `minio/minio:latest` | `${DPS_MINIO_PORT:-9010}` → 9000, `${DPS_MINIO_CONSOLE_PORT:-9011}` → 9001 (console) | artefacts and `files` exports (`DPS_ARTEFACT_STORE=s3`); root user `minioadmin`/`minioadmin` (local only); volume `minio-data`; healthcheck `curl /minio/health/live` (the cached image ships curl) |
| `minio-init` (M2, profiles `sources`/`full`) | `minio/mc:latest` | — | one-shot after `minio` is healthy: creates buckets `dps-artefacts` and `dps-exports`, exits 0 |
| `migrate` (M2) | `data-privacy-services:${DPS_IMAGE_TAG:-local}` | — | one-shot `python -m dps.migrate` as `dps_owner` after `dps-db` is healthy, exits 0; app and worker wait for it (`service_completed_successfully`) and run with `DPS_MIGRATIONS_MODE=verify` |
| `worker` (M2) | `data-privacy-services:${DPS_IMAGE_TAG:-local}` | — (9400 inside the network) | `python -m dps.worker`, `DPS_WORKER_CONCURRENCY=4`; healthcheck urllib `:9400/health`; scalable (`--scale worker=N`); read-only root, 512 MB `/tmp` tmpfs (files spools), `mem_limit: 1g` |
| `demo-secrets` (M2, profiles `sources`/`full`) | `data-privacy-services:${DPS_IMAGE_TAG:-local}` | — | one-shot (root): writes the demo `secret_ref` files `dps/src/bank-pg-ro` (`dps_reader`), `dps/tgt/bank-uat` and `dps/src/bank-restore-rw` (`dps_writer`), `dps/s3/exports` (MinIO) under the demo tenant's folder `default/` of the `dps-secrets` volume (refs are tenant-scoped), which app and worker mount read-only at `/var/run/secrets/dps`; M3 adds `dps/src/oracle-crm-ro` (`dps_ro`), `dps/src/oracle-crm-owner` (`crm`), `dps/tgt/oracle-crm-masked` (`crm_masked`), `dps/src/oracle-loans-owner` (`loans`) — each with `username`, `password` and `dsn` `oracle:1521/FREEPDB1` — and `dps/src/mongo-crm`, `dps/tgt/mongo-crm-uat` (`uri` only, `mongodb://mongo:27017/<db>?directConnection=true`), under `default/` as well; it removes an older volume's top-level `dps/` folder |
| `tokenizer-mock` (M5, profiles `tokenizer`/`full`) | `data-privacy-services:${DPS_IMAGE_TAG:-local}` | `${DPS_TOKENIZER_MOCK_PORT:-8420}` → 8420 | vaultless mock of a bank tokenization service for `external_tokenize` (`docker/tokenizer-mock/server.py`, mounted read-only): tokens `tk_` + base32(AES-SIV(value, entity\|profile)); bearer token `dps-local-tokenizer-token` and key are local demo literals; logs request lines only; healthcheck urllib `/health`, `mem_limit: 128m`. App and worker point `DPS_TOKENIZER_URL` at it (`DPS_TOKENIZER_PROVIDER=http`); without the profile the app reports `tokenizer` down and `external_tokenize` answers 502 |
| `seed-sources` (M2, profiles `sources`/`full`) | `data-privacy-services:${DPS_IMAGE_TAG:-local}` | — | one-shot after `source-pg` is healthy: `python tools/seed_sources.py --target postgres --rows 20000 --seed 7 --restore-copy`; idempotent |
| `oracle` (M3, profiles `oracle`/`full`) | `gvenzl/oracle-free:23-slim-faststart` (Oracle Database 23.26 Free) | `${DPS_ORACLE_PORT:-1522}` → 1521 | service `FREEPDB1` (`127.0.0.1:${DPS_ORACLE_PORT:-1522}/FREEPDB1` on the host, `oracle:1521/FREEPDB1` in the network); `docker/oracle/initdb/01_crm.sql` runs once per **fresh container** as SYS: users `CRM` (owner of `CUSTOMERS`, `INTERACTIONS`, `KYC_DOCUMENTS`, `ADDRESS_HISTORY`, `CUSTOMER_EVENTS`), `CRM_MASKED` (table-load target), `LOANS` (`APPLICANTS`) and `DPS_RO` (CREATE SESSION + SELECT on those six tables only); tables are created empty. Passwords = user name, SYSTEM `dps_local_oracle` (local only). Healthy once `healthcheck.sh` passes, which returns 4 until the init script has finished (faststart: ~30 s, allow 1–3 min); `mem_limit: 2700m`; no volume, so `docker compose down` resets the demo |
| `mongo` (M3, profiles `sources`/`full`) | `mongo:7` | `${DPS_MONGO_PORT:-27018}` → 27017 | `--replSet rs0 --bind_ip_all`: a one-member replica set (the member is `localhost:27017`, so clients use `directConnection=true`); the healthcheck runs `rs.initiate` on first start and is healthy only as a writable primary (transactions need it: `in_place`/`append`). No auth (local only); `mem_limit: 1g`; data lives in the image's anonymous volumes (`docker compose rm -sfv mongo` resets it) |

The app talks to the mock as `http://azure-ocr-mock:5000` (`DPS_OCR_PROVIDER=azure`, key `mock-key`,
a local demo literal) and `depends_on` it with `required: false`: **without `--profile docs` the app
still starts** and `/readyz` reports `ocr: down` (never required); JSON, text, text-layer PDFs and
DOCX/TXT/CSV documents work, while a document with pages to OCR (a scan, an image) and any redacted
PDF are refused with 503 `ocr_unavailable` — OCR is configured, so DPS fails closed rather than
skip it (a redacted PDF's OCR re-read included; `options.ocr_verify: false` asks for the
structural checks only).
`DPS_OCR_PROVIDER=tesseract docker compose up -d` uses the image's own Tesseract instead of the mock.
`tools/smoke_test.py` skips its two OCR checks when `/readyz` says OCR is not ready, and fails on them
under `DPS_SMOKE_REQUIRE_OCR=1` (the M1 milestone gate). `../mock_azure_ocr` is the directory this
repo is nested in; its build pulls `ghcr.io/astral-sh/uv:latest`, so offline, tag the cached image
for the lane (`docker tag document_intelligence-azure-ocr-mock:latest
document_intelligence-azure-ocr-mock:${DPS_IMAGE_TAG}`) and start without `--build`.

`docker compose up -d dps-db` starts the metadata database alone (the `db` tests need nothing else).
`docker compose --profile sources up -d source-pg` starts the demo source for the `pg_source` tests
(`DPS_TEST_SOURCE_PG_DSN`, default `postgresql://postgres:postgres@127.0.0.1:${DPS_SOURCE_PG_PORT:-5442}/bank`;
each test works in its own `t_<hex>` schema and drops it).
`docker compose --profile sources up -d minio minio-init` starts object storage for the `s3` tests
(`DPS_TEST_S3_ENDPOINT`, default `http://127.0.0.1:${DPS_MINIO_PORT:-9010}`, bucket `dps-artefacts`).
Without Docker, `tools/scratch_pg.sh start meta` runs a Homebrew PostgreSQL 16 cluster on
`${DPS_DB_PORT:-5441}` with the same roles (trust auth, timezone UTC, data under
`${DPS_SCRATCH_DIR:-~/.cache/dps-scratch-pg}/meta-<port>`); `stop` and `status` likewise. `db`
tests find the server through `DPS_TEST_PG_DSN` (default
`postgresql://postgres:postgres@127.0.0.1:${DPS_DB_PORT:-5441}/postgres`); without it they skip,
and with `DPS_TEST_REQUIRE_SERVICES=1` they fail.

**Migrations (M2).** `python -m dps.migrate` applies `dps/migrations/NNN_*.sql` in order as the
migration login (`DPS_PG_MIGRATION_USER`, `dps_owner` locally) under a per-schema advisory lock,
records each file's sha256 in `<schema>.dps_migration_ledger`, then creates the audit-log partitions
for this month and the next three. `--verify` only checks (runtime login, no DDL) and names every
pending or changed file; `--maintain` also drops audit partitions older than
`DPS_AUDIT_RETENTION_MONTHS`. Exit codes: 0 ok, 1 drift/pending or a failed run (class name and
SQLSTATE only), 2 `DPS_PG_HOST` unset. Applied files are never edited: fix forward with a new number
(M2 owns 001–019). `010_job_idempotency_fp.sql` (M1+M2 integration) adds `dps_job.idempotency_fp`: a
durable `Idempotency-Key` is bound to a keyed fingerprint of its request, so the same key with another
body is 422 `idempotency_key_reused` on the job routes, as on Layer 1 (contract §9); a running stack
picks it up when the `migrate` one-shot runs again (`docker compose up -d` recreates it). M3 adds
`020_job_group_last_check.sql`: `dps_job_group.last_check` / `last_check_at` hold the latest result of
`POST /api/v1/job-groups/{id}/consistency-check` (counts and codes only; returned by `GET /job-groups`
and `GET /job-groups/{id}`); a stack on an older schema needs the `migrate` one-shot again. Against the compose database:
`DPS_PG_HOST=127.0.0.1 DPS_PG_PORT=${DPS_DB_PORT:-5441} DPS_PG_PASSWORD=dps_app DPS_PG_MIGRATION_USER=dps_owner DPS_PG_MIGRATION_PASSWORD=dps_owner python -m dps.migrate`.
**M2 compose environment** (`x-dps-env`, shared by app, migrate and worker): `DPS_PG_HOST=dps-db`, logins
`dps_app` / `dps_worker_login` / `dps_owner` (local passwords = role name), `DPS_MIGRATIONS_MODE=verify`
(`auto` for `migrate`), `DPS_JOB_KEK` = a local demo literal unless the shell sets one, `DPS_SECRET_DIR=/var/run/secrets/dps`,
`DPS_ARTEFACT_DIR=/app/var/artefacts` and `DPS_EXPORT_ROOT=/app/var/exports` (named volumes
`dps-artefacts`, `dps-exports`; the image creates `/app/var` owned by uid 10001), `DPS_FPE_ENABLED`
(app and worker agree), `DPS_NER_ENABLED=false`. `docker/compose.sources.env` switches artefacts to
MinIO (`DPS_ARTEFACT_STORE=s3`, `DPS_S3_ENDPOINT_URL=http://minio:9000`, `minioadmin`).
Demo seeding: `python tools/seed_sources.py --target postgres [--dsn …] [--schema bank] [--rows 20000]
[--seed 7] [--restore-copy] [--reset]` (DSN default from `DPS_TEST_SOURCE_PG_DSN` /
`DPS_SOURCE_PG_PORT`; a schema seeded with other parameters exits 3 unless `--reset`; another
`--schema` gets `<schema>_restore`/`<schema>_perf`). M3: `--target oracle|mongo|all [--oracle-dsn …]
[--mongo-uri …]` (defaults from `DPS_TEST_ORACLE_DSN`/`DPS_ORACLE_PORT` and
`DPS_TEST_MONGO_URI`/`DPS_MONGO_PORT`) seeds `CRM.*`/`LOANS.APPLICANTS` (as the demo owners) and
`crm.profiles`; markers `CRM.DPS_SEED_INFO` / `crm.dps_seed_info` make a repeat run with the same
`(rows, seed)` print `<table>: unchanged`, another pair replaces the data. The customers' personal
fields of all three engines come from `tools/seed_model.py` (M3 changed the values a fresh
PostgreSQL seed produces: reseed `bank` with `--reset` before a three-way demo).
`CRM.CUSTOMERS.NATIONAL_ID` is `VARCHAR2(18)` (a CURP): an Oracle container initialised before M3
Task 15 must be recreated (`docker compose rm -sfv oracle`).
Hardening: `read_only: true` with a 256 MB `tmpfs` on `/tmp`, `cap_drop: [ALL]`,
`no-new-privileges`, `mem_limit: 1536m`; labels `app=data-privacy-services`, `component=api`,
`env=local`. Compose sets `DPS_ENV=local`, the bootstrap key, `DPS_DEFAULT_LOCALE=en_IN`,
`DPS_FPE_ENABLED=true`, `DPS_LLM_PROVIDER=stub`, the M1 document settings (`DPS_OCR_PROVIDER=azure`,
`DPS_AZURE_VISION_ENDPOINT`, `DPS_AZURE_VISION_KEY`, `DPS_OCR_HEALTH_PATH=/health`,
`DPS_MAX_DOCUMENT_MB=20`, `DPS_SCHEMA_MAX_ROWS=10000`) and passes the Stellar/COIN variables through
(each overridable from the shell; `DPS_COIN_TLS_INSECURE` defaults to `false`). The `/tmp` tmpfs is
also where multipart uploads above 1 MiB spool. The smoke test reads `DPS_BASE_URL` or `DPS_TEST_APP_URL` (default
`http://127.0.0.1:${DPS_APP_PORT:-8400}`) and `DPS_API_KEY` (default the local demo key).

**Lane-safe stacks** (contract §0, amendment 2026-10-12). Parallel milestone lanes each run their own
stack on this host, so `docker-compose.yml` fixes neither a host port nor an image tag:

- every host port is `127.0.0.1:${DPS_<NAME>_PORT:-<contract port>}:<container port>`: `DPS_APP_PORT`
  8400 and `DPS_OCR_PORT` 5016 today; as services arrive, `DPS_DB_PORT` 5441, `DPS_SOURCE_PG_PORT` 5442, `DPS_MINIO_PORT` 9010,
  `DPS_MINIO_CONSOLE_PORT` 9011, `DPS_ORACLE_PORT` 1522, `DPS_MONGO_PORT` 27018, `DPS_OCR_PORT` 5016,
  `DPS_DEMO_SITE_PORT` 8410, `DPS_TOKENIZER_MOCK_PORT` 8420 (worker metrics ports likewise if published);
- every image compose builds is tagged `${DPS_IMAGE_TAG:-local}` (`data-privacy-services`, later
  `data-privacy-services-browser`; the OCR mock `${DPS_IMAGE_TAG:-latest}`);
- tests and tools read endpoints only from `DPS_TEST_*` (contract §12.2), whose defaults follow the same
  port variables; `tools/smoke_test.py` reads `DPS_TEST_APP_URL` / `DPS_APP_PORT`;
- give each lane its own `COMPOSE_PROJECT_NAME` so containers, networks and volumes stay apart:

```bash
COMPOSE_PROJECT_NAME=dps-lane2 DPS_IMAGE_TAG=lane2 DPS_APP_PORT=8401 DPS_DB_PORT=5451 DPS_OCR_PORT=5017 \
  DPS_SOURCE_PG_PORT=5452 DPS_MINIO_PORT=9020 DPS_MINIO_CONSOLE_PORT=9021 \
  docker compose --env-file docker/compose.sources.env --profile docs --profile sources up --build -d
DPS_APP_PORT=8401 python tools/smoke_test.py      # plain stack (no --profile sources): DPS_SMOKE_M2=off
COMPOSE_PROJECT_NAME=dps-lane2 docker compose --profile docs --profile sources down
```

## 4. Configuration (all `DPS_*`; `DPS_<NAME>_FILE` reads the value from a mounted file)

A literal `DPS_<NAME>` wins over `DPS_<NAME>_FILE`; both win over a `.env` in the working directory
(kept out of the image by `.dockerignore`). Validation errors never echo the value.

| Variable | Default | Notes |
|---|---|---|
| `DPS_ENV` | `local` | `local`, `dev`, `staging`, `prod`; `staging`/`prod` enable the posture guard (§6); outside `local`: JSON logs and HSTS |
| `DPS_LOG_LEVEL` | `INFO` | |
| `DPS_AUTH_ENABLED` | `true` | `false` only on a laptop (every request then runs as a wildcard `local-dev` principal) |
| `DPS_API_KEYS` | — | JSON list of `{name, key \| key_sha256, tenant_ids, scopes}`; `tenant_ids` and `scopes` are required (no implicit wildcard), extra fields are refused; scopes `detect obfuscate synthesize reveal jobs scan admin` or `*`; prefer `DPS_API_KEYS_FILE` |
| `DPS_BOOTSTRAP_API_KEY` | — | one wildcard key (every tenant, every scope) for local demos; refused in staging/prod |
| `DPS_DEFAULT_POLICY` | `gdpr-pseudonymise` | used when a request names no policy |
| `DPS_DEFAULT_LOCALE` | `en_US` | locale for phone parsing, date reading and surrogates unless the request sets `options.locale` or the policy pins `locale.default`; only `dpdp-pseudonymise` pins one (`en_IN`) |
| `DPS_FPE_ENABLED` | `false` | FF1 methods fall back to `det_token` until set; flip only after the license review (spec D8) |
| `DPS_NER_ENABLED` | auto | unset = on when `presidio-analyzer` imports; `true` requires the `ner` extra and a spaCy model |
| `DPS_NER_LANGUAGES` | `en,es` | languages with an installed model are loaded; the image ships `en` only (Spanish text is read by the English model) |
| `DPS_LLM_PROVIDER` | `""` | `stellar` (prod, VDI), `vertex` (laptop), `stub` (tests/demo; refused in staging/prod), `""` = none |
| `DPS_LLM_MODEL` / `DPS_LLM_TIMEOUT_SECONDS` | `gemini-2.5-flash` / 30 | |
| `DPS_LLM_TEXT_REWRITE` | `false` | global switch for the placeholder-only text rewrite (a request or policy can only narrow it) |
| `DPS_STELLAR_BASE_URL`, `DPS_COIN_URL` | — | Stellar calls `<base>/chat/completions`; both must be `https` in staging/prod |
| `DPS_COIN_CLIENT_ID`, `DPS_COIN_CLIENT_SECRET`, `DPS_COIN_SCOPE` | — | **base64**-encoded (org convention), decoded at use; the scope is sent as `coinscope<scope>` |
| `DPS_COIN_TOKEN_TTL_SECONDS` | 840 | COIN token reuse window |
| `DPS_SSL_CERT_FILE` | — | CA bundle for Stellar calls (and the COIN mint when `DPS_COIN_CA_FILE` is unset); required for `stellar` in staging/prod |
| `DPS_COIN_CA_FILE` | — | CA bundle (file or directory) for the COIN token mint; else `DPS_SSL_CERT_FILE`, else httpx's default store (certifi, or `SSL_CERT_FILE`/`SSL_CERT_DIR` from the environment) |
| `DPS_COIN_TLS_INSECURE` | `false` | `true` skips TLS verification **on the COIN token mint only** (the VDI's intercepting proxy); logs one `coin_tls_insecure` warning at startup and `/readyz` reports `coin_tls: insecure`; Stellar API calls still verify |
| `DPS_ALLOW_INSECURE_COIN_MINT` | `false` | the documented VDI exception: staging/prod refuse `DPS_COIN_TLS_INSECURE=true` unless this is `true` too |
| `DPS_VERTEX_PROJECT` / `DPS_VERTEX_LOCATION` | — / `global` | laptop path; needs the `vertex` extra and ADC |
| `DPS_MAX_PAYLOAD_MB` / `DPS_MAX_TEXT_CHARS` / `DPS_MAX_ITEMS` | 8 / 1,000,000 / 10,000 | 413 beyond (JSON-route body bytes by header or while streaming, `payload_too_large`; text characters, documents' canonical text included; JSON leaf values) |
| `DPS_RATE_LIMIT_ENABLED` / `_RPS` / `_BURST` | `true` / 50 / 100 | token bucket per API key, per process; 429 + `Retry-After` |
| `DPS_AUDIT_ENABLED` | `true` | `false` only locally; refused in staging/prod |
| `DPS_OCR_PROVIDER` | `""` (the image: `auto`) | M1 documents — `""`/`off`: no OCR; `azure`: Read v3.2 (the compose mock, the org gateway, Azure) when endpoint and key are set, else none; `tesseract`: the local CLI (`/readyz` `ocr: tesseract_missing` without the binary); `auto` (the image's `ENV`): azure when endpoint and key are set, else tesseract when `DPS_TESSERACT_CMD` is on `PATH`, else none. Staging/prod: azure (also via `auto`) needs a key and an `https` endpoint; `auto` that resolves to none is refused; Tesseract is allowed (local, no egress) |
| `DPS_TESSERACT_CMD` / `DPS_TESSERACT_LANGS` / `DPS_TESSERACT_TIMEOUT_SECONDS` | `tesseract` / `eng` / 60 | local OCR: the binary (name on `PATH` or a path), its languages (`eng`, `eng+spa`, …; the image ships `eng` only) and one page's time limit (the process group is killed beyond it: `ocr_timeout`). Page image on stdin, TSV on stdout; Tesseract's stderr is never read (it can echo recognised text). `DPS_OCR_MAX_CONCURRENCY` bounds parallel runs. `/readyz` `ocr.engine` is `tesseract-<major.minor>` |
| `DPS_AZURE_VISION_ENDPOINT` / `DPS_AZURE_VISION_KEY` | — | Read v3.2 base URL (locally `http://127.0.0.1:${DPS_OCR_PORT:-5016}`, in compose `http://azure-ocr-mock:5000`) and its `Ocp-Apim-Subscription-Key`; prefer `DPS_AZURE_VISION_KEY_FILE` |
| `DPS_OCR_TIMEOUT_SECONDS` / `DPS_OCR_POLL_INTERVAL_SECONDS` | 60 / 0.5 | one OCR call (analyze + polls) / poll spacing |
| `DPS_OCR_HEALTH_PATH` / `DPS_OCR_PROBE_INTERVAL_SECONDS` | `/health` / 30 | readiness probe of the OCR endpoint (`/_gw/health` behind azure-gateway-api; `""` = no probe; interval `0` = once at startup) |
| `DPS_OCR_MAX_CONCURRENCY` / `DPS_OCR_MIN_TEXT_CHARS` / `DPS_OCR_IMAGE_COVERAGE` | 4 / 20 / 0.25 | parallel OCR calls; a PDF page with fewer text-layer characters, or images covering this share of it, is OCR'd under `ocr: auto`; when a redacted PDF is requested (and OCR is configured) every page carrying any image is OCR'd too, else the response warns `images_not_ocr_read` |
| `DPS_MAX_DOCUMENT_MB` / `DPS_MAX_DOCUMENT_PAGES` / `DPS_PDF_RENDER_DPI` | 20 / 200 / 200 | upload limits (413 beyond: the `/api/v1/documents/*` body may be this + 1 MiB of multipart overhead, by header or while streaming, `payload_too_large`; the file itself, `document_too_large`) and the raster resolution of OCR and redaction; a PDF page that would render over 10,000 px a side (the Read v3.2 limit) at the higher of this and `DPS_PREVIEW_DPI` is refused with 413 `image_too_large` (3,600 pt at 200 dpi) |
| `DPS_REDACT_IMAGE_FORMAT` / `DPS_REDACT_JPEG_QUALITY` / `DPS_REDACT_OCR_VERIFY` | `jpeg` / 85 / `true` | page rasters inside a redacted PDF (`jpeg` or `png`); when OCR is configured, re-read **every** page of the produced PDF with OCR and refuse it (422 `leakage`, nothing returned) if a redacted original is still readable on any page or the engine, with the request's policy, finds a value it would transform in what OCR read (one extra OCR call per page). On a page with a text layer and OCR (`hybrid`), OCR words the text layer reads otherwise are kept (their whole OCR line), so the engine sees what the page shows |
| `DPS_PREVIEW_DPI` / `DPS_MAX_PREVIEW_PAGES` | 72 / 50 | console page previews |
| `DPS_SCHEMA_MAX_ROWS` | 10,000 | rows per `/schema/synthesize` call |
| `DPS_LLM_LABEL_MAX_FIELDS` / `DPS_LLM_LABEL_MIN_CONFIDENCE` | 40 / 0.7 | T3 metadata labelling, **opt-in per policy** (`detect.tiers` lists `llm_label`) and only with a real provider (`stellar`/`vertex`; never `stub`): JSON leaves T0–T2 left without a finding are grouped by path template (`[n]` → `[*]`) and sent as field name, JSON type and ≤ 3 value **shapes** (`AB-1234` → `XX-9999`) — never a value; value-like names (and paths with a quoted key) go as `field_<n>`; the prompt passes the leak guard against the leaf values or no call is made. Fields per call / the lowest confidence kept; a labelled leaf becomes a finding with detector `llm_label` and score ≤ 0.85, treated like any other (plan, transform, verification, report `llm.calls`). One call per request, one per NDJSON stream (from its first chunk) |
| `DPS_IDEMPOTENCY_TTL_SECONDS` / `DPS_IDEMPOTENCY_MAX_ENTRIES` | 600 / 10,000 | `Idempotency-Key` on `/detect`, `/obfuscate`, `/synthesize`, `/reveal`, `/documents/*`, `/schema/synthesize`: a **replica-local** replay cache holding, per `sha256(tenant, key id, route, key)`, only the body's SHA-256, the original trace id and a timestamp — never key material, a seed or an output (decision D2). Same key + body within the TTL re-runs the engine and answers `Idempotent-Replay: true` with the original `trace_id` (audit outcome `replayed`); identical output only when the caller sent its own `consistency.key_material` / `synthesis_seed`. Same key + another body → 422 `idempotency_key_reused`; a malformed key → 422 `idempotency_key_invalid`. A retry on another replica, or after the TTL, runs as a first call. `0` disables the cache (a well-formed header is accepted and ignored) |
| `DPS_NDJSON_MAX_ITEMS` / `DPS_NDJSON_CHUNK_ITEMS` / `DPS_MAX_NDJSON_MB` | 1,000,000 / 500 / 256 | NDJSON streaming on `/detect`, `/obfuscate`, `/synthesize`, `/reveal` (an `application/x-ndjson` body, or a JSON `data` array with `Accept: application/x-ndjson`): items per stream (413 `ndjson_too_large`), items per worker-thread hop, and the body limit for an NDJSON body on those four routes (JSON bodies keep `DPS_MAX_PAYLOAD_MB`); every item obeys `DPS_MAX_ITEMS`. The whole body is held in memory while it streams out, so keep `DPS_MAX_NDJSON_MB` well under the container's memory limit |

**M2 settings (jobs + PostgreSQL; contract §10).** All default so Layer 1 runs without a database:
an empty `DPS_PG_HOST` means no metadata database, no jobs and no M2 posture checks.

| Variable | Default | Notes |
|---|---|---|
| `DPS_PG_HOST` / `_PORT` / `_DATABASE` / `_SCHEMA` | `""` / 5432 / `dps` / `dps` | schema must match `^[a-z_][a-z0-9_]{0,62}$` |
| `DPS_PG_USER` / `_PASSWORD` | `dps_app` / — | API pool login |
| `DPS_PG_WORKER_USER` / `_PASSWORD` | → `DPS_PG_USER` | worker pool login (`dps_worker_login` in compose) |
| `DPS_PG_MIGRATION_USER` / `_PASSWORD` | → `DPS_PG_USER` | DDL login (`dps_owner` in compose) |
| `DPS_PG_POOL_MIN` / `_MAX`, `DPS_PG_SSL_ROOT_CERT` | 2 / 16, — | the CA file turns on verified TLS (required in staging/prod) |
| `DPS_RLS_ENABLED` | `true` | must stay `true` in staging/prod |
| `DPS_MIGRATIONS_MODE` | `auto` | `auto` (apply at boot) \| `verify` (staging/prod) \| `off` (tests) |
| `DPS_JOB_KEK` / `DPS_JOB_KEK_FILE`, `DPS_JOB_KEK_PREVIOUS` | — | base64 of exactly 32 bytes (`python -c 'import os,base64;print(base64.b64encode(os.urandom(32)).decode())'`); staging/prod read it **only** from `_FILE` |
| `DPS_JOB_LEASE_SECONDS`, `DPS_PARTITION_LEASE_SECONDS` | 300, 120 | queue leases |
| `DPS_JOB_MAX_ATTEMPTS`, `DPS_PARTITION_MAX_ATTEMPTS` | 3, 5 | |
| `DPS_JOB_RETRY_BASE_SECONDS` / `_MAX_SECONDS`, `DPS_JOB_POLL_INTERVAL_SECONDS`, `DPS_JOB_CLAIM_BATCH` | 30 / 3600, 2, 4 | |
| `DPS_JOB_REAPER_INTERVAL_SECONDS`, `DPS_JOB_DRAIN_TIMEOUT_SECONDS` | 30, 30 | |
| `DPS_JOB_TENANT_MAX_RUNNING` / `_MAX_QUEUED` | 8 / 1000 | per-tenant caps (a `dps_tenant` row overrides) |
| `DPS_WORKER_CONCURRENCY`, `DPS_WORKER_METRICS_PORT`, `DPS_EMBEDDED_WORKER` | 4, 9400, `false` | metrics port 0 disables the worker's ops server; embedded worker refused in staging/prod |
| `DPS_BATCH_ROWS`, `DPS_PARTITION_ROWS`, `DPS_MAX_PARTITIONS`, `DPS_DISCOVERY_SAMPLE_ROWS`, `DPS_PREFLIGHT_ROWS` | 1000, 50000, 256, 2000, 500 | |
| `DPS_CONNECTOR_CONNECT_TIMEOUT_SECONDS` / `_STATEMENT_TIMEOUT_SECONDS` | 10 / 300 | |
| `DPS_SECRET_DIR`, `DPS_INLINE_SECRETS_ALLOWED`, `DPS_INLINE_SECRET_TTL_HOURS` | `/var/run/secrets/dps`, `true`, 24 | inline credentials refused in staging/prod |
| `DPS_RETENTION_DAYS`, `DPS_RETENTION_INTERVAL_HOURS`, `DPS_AUDIT_RETENTION_MONTHS` | 30, 6, 13 | |
| `DPS_AUDIT_STRICT`, `DPS_AUDIT_QUEUE_MAX`, `DPS_AUDIT_FLUSH_BATCH`, `DPS_AUDIT_FLUSH_MS` | `false`, 10000, 200, 250 | strict audit required in staging/prod |
| `DPS_ARTEFACT_STORE`, `DPS_ARTEFACT_DIR`, `DPS_EXPORT_ROOT` | `local`, `./var/artefacts`, `./var/exports` | `local` \| `s3`; `var/` is git-ignored |
| `DPS_S3_ENDPOINT_URL`, `_REGION`, `_BUCKET`, `_ACCESS_KEY_ID`, `_SECRET_ACCESS_KEY`, `_FORCE_PATH_STYLE` | —, `us-east-1`, `dps-artefacts`, —, —, `true` | |
| `DPS_IMAGE_DIGEST` | — | stamped into evidence packs |

**M5 settings (contract §10; tabular synthesis, tokenization, keys, posture).** All have defaults;
the posture rules that tighten them in staging/prod arrive with M5's posture task.

| Variable | Default | Notes |
|---|---|---|
| `DPS_SYNTH_TABULAR_ENGINE` | `copula` | comma list of enabled tabular engines: `copula`, `mostly` (needs the `synth-ml` extra); `""` = tabular synthesis off; anything else is refused |
| `DPS_COPULA_FIT_ROWS` / `_HOLDOUT_ROWS` / `_METRICS_ROWS` | 100,000 / 20,000 / 5,000 | rows fitted at prepare / extra rows held out (never fitted) to measure privacy / preview rows run through the real batch transformer |
| `DPS_COPULA_RARE_FLOOR` / `DPS_COPULA_MAX_RESAMPLE` | 0.01 / 8 | categories below this share are never emitted (within [0, 0.2]); redraws per row before a batch fails closed with `exact_row_match` (≥ 1) |
| `DPS_FIDELITY_COLUMN_LIMIT` / `DPS_FIDELITY_CORR_LIMIT` | 0.05 / 0.10 | KS/TVD per column and mean correlation delta shown as "within limit" in the evidence |
| `DPS_PRIVACY_NNDR_TARGET` / `DPS_PRIVACY_NNDR_ENFORCE` | 0.95 / `false` | `true`: an NNDR ratio below the target fails prepare (`privacy_gate`) |
| `DPS_MOSTLY_POOL_ROWS` / `DPS_MOSTLY_MAX_TRAINING_MINUTES` | 20,000 / 10 | optional MOSTLY engine |
| `DPS_MOSTLY_DP_ENABLED` / `_DP_MAX_EPSILON` / `_DP_DELTA` | `false` / 5.0 / 1e-5 | MOSTLY differential-privacy training (epsilon > 0, 0 < delta < 1) |
| `DPS_MOSTLY_LINKED_CHILD_ROWS` | 200,000 | MOSTLY linked generation: most child rows read for training (children of the sampled parents, keyset pages) |
| `DPS_UTILITY_MAX_ROWS` / `DPS_MIA_MIN_HOLDOUT` / `DPS_MIA_AUC_TARGET` | 5,000 / 50 / 0.60 | train-on-synthetic/test-on-real budget; the membership-inference proxy needs this many holdout rows and passes at or below the AUC target |
| `DPS_TOKENIZER_PROVIDER` | `""` | `external_tokenize` adapter: `""` (off) or `http` |
| `DPS_TOKENIZER_URL` / `DPS_TOKENIZER_TOKEN` (`_FILE`) / `DPS_TOKENIZER_CA_FILE` | — | the bank tokenization service (http(s) with a host; the error never echoes the URL), its bearer token (prefer `DPS_TOKENIZER_TOKEN_FILE`) and CA bundle |
| `DPS_TOKENIZER_TIMEOUT_SECONDS` / `_BATCH` / `_RETRIES` | 10 / 500 / 2 | |
| `DPS_KEY_CACHE_SECONDS` / `DPS_KEY_MAX_TTL_DAYS` / `DPS_KEY_ROTATION_MAX_AGE_DAYS` / `DPS_KEY_ROTATION_MAX_GRACE_HOURS` | 30 / 365 / 90 / 168 | DB-backed API keys |
| `DPS_REPORT_PDF_ENABLED` / `DPS_ROLLUP_RETENTION_DAYS` | `true` / 400 | PDFs: `GET /api/v1/evidence/{id}/pdf` (rendered once with reportlab's built-in Helvetica, stored as an `evidence_pdf` artefact, streamed afterwards) and `format=pdf` on `/reports/api-rollups/{day}`; `false` → 404 `pdf_disabled`. Report roll-up and daily usage rows older than the retention days are deleted by the retention job |
| `DPS_EGRESS_TLS_REQUIRED` / `DPS_CORS_ORIGINS` | `false` / `*` | posture: `true` and explicit origins in staging/prod; origins are a comma list, `""` = same-origin only |
| `DPS_VERIFY_WARN_ALLOWED` / `DPS_LLM_EGRESS_CHECK` | `true` / `strict` | posture: `false` / `strict` in staging/prod; `strict` or `warn` only |

**M3 settings (Oracle and MongoDB connectors; contract §10).** Oracle and MongoDB credentials never
go in env vars: a connection's `secret_ref` directory supplies `username`, `password` and `dsn`
(Oracle) or `uri` (Mongo).

| Variable | Default | Notes |
|---|---|---|
| `DPS_ORACLE_POOL_MAX` | 4 | sessions per Oracle connection pool (1–64) |
| `DPS_ORACLE_CALL_TIMEOUT_MS` | 30000 | per round trip (1000–3600000; the Wega rule: a hung call becomes an error) |
| `DPS_ORACLE_MODULE` | `dps` | `v$session.module` of every DPS session (1–48 of `[A-Za-z0-9_.-]`), for the DBA's audit |
| `DPS_MONGO_SERVER_SELECTION_TIMEOUT_MS` | 5000 | 500–120000 |

Declared but not read by the app: `DPS_HOST` (only the worker's ops server binds to it), `DPS_PORT` (the image's `CMD` fixes `0.0.0.0:8400`),
`DPS_MIN_SCORE` (the policy's `detect.min_score` applies), `DPS_APP_NAME`. The M1 document, OCR and
schema settings above are validated (and posture-checked) and read by the document pipeline and routes.

Compose-only variables (read by `docker-compose.yml` and the tools, not by the app): `DPS_IMAGE_TAG`
(default `local`; the OCR mock `latest`) and the host-port variables `DPS_APP_PORT` (8400),
`DPS_DB_PORT` (5441), `DPS_SOURCE_PG_PORT` (5442), `DPS_MINIO_PORT` (9010), `DPS_MINIO_CONSOLE_PORT`
(9011), `DPS_OCR_PORT` (5016), `DPS_ORACLE_PORT` (1522) and `DPS_MONGO_PORT` (27018; the others arrive with their services, §3 "Lane-safe stacks"). The M3 tests read `DPS_TEST_ORACLE_DSN` / `DPS_TEST_ORACLE_USER` / `DPS_TEST_ORACLE_PASSWORD` (default `127.0.0.1:${DPS_ORACLE_PORT:-1522}/FREEPDB1`, `system`, `dps_local_oracle`) and `DPS_TEST_MONGO_URI` (default `mongodb://127.0.0.1:${DPS_MONGO_PORT:-27018}/?directConnection=true`).
`DPS_SMOKE_REQUIRE_OCR=1` makes `tools/smoke_test.py` fail instead of skipping its OCR checks;
`DPS_SMOKE_M2=auto|on|off` selects its job-platform checks; `--m3` adds the Oracle + MongoDB
checks, bounded by `DPS_SMOKE_M3_TIMEOUT` (600 s).

## 5. Probes and ops surfaces

- `GET /health` — liveness, `{"status": "ok"}`.
- `GET /readyz` — `{"ready", "components"}` with `api-core`, `llm-gateway` (`provider`, `coin_tls`
  `verified` / `insecure`), `ner`
  (`presidio` / `patterns-only`), `fpe` (`ff1` / `det_token fallback`), from M1 `ocr`
  (`up` / `down` / `down:<status>` / `error` / `unconfigured` / `tesseract_missing`, with `provider`
  and `engine` — `azure-read-v3.2` or `tesseract-<major.minor>`; probed at
  startup and every `DPS_OCR_PROBE_INTERVAL_SECONDS` while OCR is configured; never required, since
  JSON and text need no OCR), and from M2 `db` (`up` / `down` / `unconfigured`, with `migrations:
  verified`) and `jobs` (ok when the DB is up, the migrations verify and the KEK is loaded); **503**
  only when a required component is down, and only `api-core` is required (it turns false on
  shutdown) — the engine and document routes never depend on the database. A database that is down
  at boot is retried every 10 s; meanwhile the `/api/v1` job-platform routes answer 503 `{"detail",
  "code": "db_unavailable"}`. RLS posture problems are logged as `rls_posture_problem` locally and
  refuse boot in staging/prod.
- Errors are one wire shape (contract §8.2): `{"detail", "code", …}` — M1's `CodedHTTPException` and
  `dps.errors.CodedError`, M2's `DpsHttpError` (plus its `extra` fields) and the auth refusals with a
  code are rendered by one handler; M0 routes keep `{"detail"}` where they have no code. A tenant id
  starting with `_` (`_system`) is refused with 403 `tenant_reserved`, wildcard keys included; the
  job platform takes lower-case tenant ids only (400 `tenant_invalid`). CORS allows
  `GET POST PATCH DELETE OPTIONS`, the `Idempotency-Key` header, and exposes `Idempotent-Replay`.
  `DPS_EMBEDDED_WORKER=true` (local only) runs a worker `Consumer` inside the app process.
- `GET /metrics` — Prometheus: `dps_requests_total{route,outcome}`,
  `dps_request_seconds{route}`, `dps_findings_total{entity_type,detector}`,
  `dps_transforms_total{mode,method}`, `dps_llm_calls_total{provider,purpose,outcome}`,
  `dps_verification_failures_total{mode}` and **`dps_llm_raw_pii_sent_total`, which must stay 0**;
  M1 adds `dps_document_pages_total{format,source}`, `dps_redacted_boxes_total`,
  `dps_ocr_calls_total{engine,outcome}` (engine `azure-read-v3.2` or `tesseract`),
  `dps_ocr_seconds` and `dps_llm_label_fields_total{outcome}` (T3 labelling: `labelled`,
  `unlabelled`, `skipped` over the per-call cap, `blocked` by the leak guard, `failed`; the call
  itself is `dps_llm_calls_total{purpose="label"}`). M5 (spec P1, Task 16B) adds the tenant
  dimension: `dps_llm_calls_by_tenant_total{tenant_id,purpose}`,
  **`dps_llm_raw_pii_sent_by_tenant_total{tenant_id}` (must stay 0; alert `DpsRawPiiSentToLlm`,
  critical, on any increase)** and `dps_llm_exposure_blocked_by_tenant_total{tenant_id}` (alert
  `DpsLlmExposureBlocked`, warning) — tenant `-` for calls outside a request or job. The gateway's
  **egress verifier** (`dps/llm/egress.py`) checks every prompt before it leaves: a protected original
  the caller names, or a self-identifying T1 value (checksum-validated id or card, e-mail, phone)
  outside a `{TYPE_N}` placeholder. `DPS_LLM_EGRESS_CHECK=strict` (staging/prod) blocks the prompt
  — nothing is sent, the caller falls back to its deterministic path (`fallback: egress_blocked`);
  `warn` (local diagnosis) sends it and counts it on the raw counters. Each request's audit event
  carries `counts.llm_calls`, `llm_raw_values`, `llm_blocked` when it reached the gateway; the daily
  roll-ups sum them per tenant (migration 044) into the Overview tiles `llm_calls`,
  `raw_pii_sent_to_llm`, `llm_blocked`; a job handler that calls the LLM records the same three in
  `progress.metrics.llm`.
- Audit: one `audit` log line per engine request, rejections included (trace id, route, tenant, key
  id, mode, policy label, outcome, counts, methods, verification, duration — never a value or a key).
  Document routes audit mode `document.detect|obfuscate|synthesize`; the outcome is the error code
  when a document is refused (`encrypted_pdf`, `ocr_unavailable`, `leakage`, …), and the policy
  is `-` when the `request` form field could not be parsed. The `document_processed` log line
  carries counts only (format, pages, OCR pages, boxes, duration).
  Schema routes (M1) audit mode `schema.generator-spec|schema.synthesize` with policy `-`;
  `verification_passed` is the sample leakage sweep's verdict (absent for a schema, DDL or spec),
  and a spec proposal through the gateway counts as `dps_llm_calls_total{purpose="genspec"}`.
  The policy label is a shipped preset name, `unknown`/`invalid` for any other string, or
  `<preset|custom>+inline:<hash12>` for an inline policy (M1; never the inline body).
  Logs go to stdout through an allow-list of field names; outside `local` they are JSON.
- **API keys (M5).** With a metadata database, keys come from `DPS_API_KEYS` (static, checked first)
  **and** from `dps_api_key` (migration 042: sha256 + an 8-character prefix only, never the key).
  The app serves DB keys from an in-memory snapshot refreshed every `DPS_KEY_CACHE_SECONDS` (30);
  expiry is checked on every lookup, a disable/rotation on the same replica takes effect at once and
  on the others within one refresh, and after ten failed refreshes DB keys fail closed (static keys
  keep working). `/readyz` gains `keys` (`static` or `db+static`, informational);
  `dps_auth_keys_loaded` is the snapshot size. Keys are never deleted (disabled; the app role has no
  DELETE on the table); a key is rotated at most once (`grace_hours` 0–`DPS_KEY_ROTATION_MAX_GRACE_HOURS`,
  0 = disabled now) and never grants more scopes or tenants than its creator holds.
  **Bootstrap path (staging/prod):** with the metadata DB credentials in the environment run
  `python -m dps.keys_cli create --name platform-admin --tenants '*' --scopes '*' --expires-in-days 90`
  (one JSON document on stdout — the raw key, once), store the key in the secret manager, and leave
  `DPS_BOOTSTRAP_API_KEY` unset. `list`, `rotate ID [--grace-hours H]`, `disable ID` and `enable ID`
  manage keys the same way (exit 2 with `{"error", "detail"}` on a refusal); every mutating command
  writes an audit row owned by `_system` (`key_id = route = cli`).
- `GET /docs` (Swagger UI) and `GET /api/v1/openapi.json` (not served in staging/prod, M5). Probe and
  metrics routes are not in the schema.
- **Worker (M2).** `python -m dps.worker` claims `discover`, `run` (prepare/finalize, partitions)
  and `retention` jobs with `DPS_WORKER_CONCURRENCY` tasks; it needs `DPS_PG_HOST` and `DPS_JOB_KEK`
  (refuses to start without either, exit 1, no traceback). It serves `GET /health`, `GET /readyz`
  (`db` = `SELECT 1`, `migrations` verified, `kek` loaded, `queue` claiming; 503 otherwise) and
  `GET /metrics` on `${DPS_HOST}:${DPS_WORKER_METRICS_PORT}` (9400; 0 = off). SIGTERM/SIGINT drain:
  stop claiming, wait `DPS_JOB_DRAIN_TIMEOUT_SECONDS` while heartbeats keep the leases, cancel and
  release the rest without charging an attempt, unregister, exit 0. Every worker runs the reaper and
  schedules at most one retention job per `DPS_RETENTION_INTERVAL_HOURS` across replicas (owner
  `_system`); retention deletes expired terminal jobs with their artefacts, stale proposed/superseded
  snapshots and expired inline secrets, purges leftover sealed keys, never the audit log or exports.
  M5 extends the same job (no second scheduler): it also ages out report roll-ups and daily usage rows
  older than `DPS_ROLLUP_RETENTION_DAYS` (400), refreshes the last three days' roll-ups of tenants
  with recent jobs, records its counts in `progress.metrics.retention` and writes one `retention.run`
  audit event owned by `_system`. `POST /api/v1/admin/retention/run {"dry_run": true}` (admin key
  granted every tenant) queues an on-demand run.

## 6. Security posture

- **Posture guard** (`DPS_ENV=staging|prod`, checked before the app is built, the process exits on a
  violation): auth on, bootstrap key unset, `DPS_API_KEYS` set, provider not `stub`, `stellar` needs
  `DPS_SSL_CERT_FILE` and both URLs, Stellar/COIN URLs `https`, `DPS_COIN_TLS_INSECURE=true` only
  with `DPS_ALLOW_INSECURE_COIN_MINT=true`, audit on, and with `DPS_OCR_PROVIDER=azure` a
  `DPS_AZURE_VISION_KEY` and an `https` `DPS_AZURE_VISION_ENDPOINT` (M1). Messages name the setting,
  never its value. With `DPS_PG_HOST` set (M2) it also requires: the job KEK from `DPS_JOB_KEK_FILE`
  (a literal `DPS_JOB_KEK` env var is refused), `DPS_INLINE_SECRETS_ALLOWED=false`,
  `DPS_RLS_ENABLED=true`, `DPS_MIGRATIONS_MODE=verify`, `DPS_AUDIT_STRICT=true`,
  `DPS_PG_SSL_ROOT_CERT` set and `DPS_EMBEDDED_WORKER=false`.
- **M5 posture** (`dps/posture.py`, same guard): secrets only as mounted files — a literal
  `DPS_JOB_KEK`, `DPS_JOB_KEK_PREVIOUS`, `DPS_API_KEYS`, `DPS_COIN_CLIENT_ID`/`_SECRET`,
  `DPS_PG_PASSWORD`, `DPS_PG_WORKER_PASSWORD`, `DPS_PG_MIGRATION_PASSWORD`, `DPS_TOKENIZER_TOKEN`,
  `DPS_AZURE_VISION_KEY`, `DPS_S3_ACCESS_KEY_ID` or `DPS_S3_SECRET_ACCESS_KEY` environment variable
  is refused (use `<NAME>_FILE`); with `DPS_PG_HOST` the KEK must be set; `DPS_EGRESS_TLS_REQUIRED=true`,
  every egress URL (`DPS_STELLAR_BASE_URL`, `DPS_COIN_URL`, `DPS_AZURE_VISION_ENDPOINT`,
  `DPS_S3_ENDPOINT_URL`, `DPS_TOKENIZER_URL`) `https` and, when any is set, `DPS_SSL_CERT_FILE` (the
  organisation CA bundle); `DPS_TOKENIZER_TOKEN_FILE` with `DPS_TOKENIZER_PROVIDER=http`;
  `DPS_CORS_ORIGINS` without `*`; and **strict verification at boot**: `DPS_VERIFY_WARN_ALLOWED=false`,
  `DPS_LLM_EGRESS_CHECK=strict`, every shipped preset and `DPS_DEFAULT_POLICY` with
  `verify.leakage_check: strict`. At request time a policy (inline, preset override or stored) with
  `leakage_check: warn` or a run with `options.verify=warn` is refused with 422
  `verify_warn_disallowed` while `DPS_VERIFY_WARN_ALLOWED=false`, and publishing such a tenant policy
  is refused the same way. In staging/prod `/docs` and `/api/v1/openapi.json` are not served.
- **TLS-only connections** (`DPS_EGRESS_TLS_REQUIRED=true`): postgres needs
  `options.sslmode=verify-full|verify-ca`, mongo `options.tls=true`, oracle `options.protocol=tcps`
  (or a `dsn` secret file using tcps), s3 an `https` `endpoint_url` (or none = AWS); pvc is exempt.
  Refused with 422 `tls_required` at `POST /connections`, at a `PATCH` that changes `params`, and at
  use (a connection registered before the switch fails its job with `tls_required`, not retried).
- **Runtime posture** — `GET /api/v1/admin/posture` (admin): eleven checks (`auth_enabled`,
  `bootstrap_key_absent`, `api_keys_from_file`, `tls_egress`, `audit_strict`, `leakage_strict` incl.
  every tenant's active policy, `llm_not_stub`, `kek_from_secret`, `keys_rotated` (names and ages of
  active keys older than `DPS_KEY_ROTATION_MAX_AGE_DAYS` never rotated), `rls_posture`,
  `migrations_verified`), evaluated as if production in every environment; `enforced` is true in
  staging/prod. Database checks read "database unavailable" while it is down. The tenant policies
  of `leakage_strict` and the keys of `keys_rotated` are scoped to the caller's tenants (keys as in
  `GET /admin/keys`); use a wildcard (`*`) admin key for the platform-wide view.
- **KEK rotation runbook** (`python -m dps.rekey [--dry-run]`): mount the new KEK as
  `DPS_JOB_KEK_FILE` and the old one as `DPS_JOB_KEK_PREVIOUS_FILE`; roll the app and worker
  deployments; run `python -m dps.rekey` once (any pod with the image, the worker's database login
  and the API login: it re-seals `dps_job.key_enc`/`spec_enc` through the worker pool and
  `dps_connection.secret_enc` per tenant); confirm the printed JSON line has `"unreadable": 0` (exit
  code 3 otherwise — those rows are unchanged; exit 2 = no KEK or no database); remove
  `DPS_JOB_KEK_PREVIOUS_FILE` and roll again. Keep the previous KEK as long as callers may reveal
  `external_tokenize` tokens bound under it (§8). Until `dps.rekey` has run, jobs sealed under the
  old KEK cannot be opened by workers that hold only the new one — run it right after the first roll.
- **COIN token mint TLS** (SEC-1): verified by default (`DPS_COIN_CA_FILE` → `DPS_SSL_CERT_FILE` →
  default store). **VDI exception:** the org's intercepting proxy re-signs the COIN endpoint with a
  certificate no bundle we hold verifies, so on the VDI set `DPS_COIN_TLS_INSECURE=true` and, in
  staging/prod, `DPS_ALLOW_INSECURE_COIN_MINT=true` (record the approval with the deployment).
  Prefer `DPS_COIN_CA_FILE` with the proxy's CA whenever it can be obtained.
- Every response: `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`,
  `Referrer-Policy: no-referrer`; HSTS outside `local`. The console HTML carries a strict CSP
  (`script-src 'self'`, `frame-ancestors 'none'`).
- Console API key: kept in `sessionStorage` (gone with the tab); the header's "Remember key on this
  device" moves it to `localStorage` and unticking removes it again. A key left in `localStorage` by
  the M0 console is moved into the tab's session on first load.
- CORS (M5): only the origins in `DPS_CORS_ORIGINS` (comma list; `""` = same-origin, no CORS
  headers at all; local default `*`, refused in staging/prod), methods
  `GET POST PUT PATCH DELETE OPTIONS`, headers `Content-Type X-API-KEY X-DPS-Tenant X-Request-ID
  Idempotency-Key` (keys travel in headers, not cookies).
- Error bodies never quote the request: 422s list `{type, loc, msg}` only, unexpected errors are a
  500 `internal error` with only the exception class logged.

## 7. Secrets (names only)

`DPS_API_KEYS`, `DPS_COIN_CLIENT_ID`, `DPS_COIN_CLIENT_SECRET`, `DPS_COIN_SCOPE`, from M1
`DPS_AZURE_VISION_KEY` — mount them and use the `_FILE` form. M2 adds `DPS_JOB_KEK(_FILE)` (`_FILE`
only in staging/prod; `DPS_JOB_KEK_PREVIOUS` arrives with M5's rotation), `DPS_PG_PASSWORD(_FILE)`,
`DPS_PG_WORKER_PASSWORD(_FILE)`, `DPS_PG_MIGRATION_PASSWORD(_FILE)` and
`DPS_S3_SECRET_ACCESS_KEY(_FILE)` (with `DPS_S3_ACCESS_KEY_ID`), plus the connection `secret_ref`
directories under each tenant's folder `DPS_SECRET_DIR/<tenant_id>/`: `dps/src/*` (sources),
`dps/tgt/*` (targets), `dps/s3/*` (file stores; M3's local demo refs are `dps/src/oracle-crm-ro`,
`dps/src/oracle-crm-owner`, `dps/tgt/oracle-crm-masked`, `dps/src/oracle-loans-owner`,
`dps/src/mongo-crm`, `dps/tgt/mongo-crm-uat` under the demo tenant `default/`; an Oracle ref adds a
`dsn` file, a Mongo ref a `uri` file), and `DPS_SECRET_DIR/_shared/*` for a secret
several tenants may use. Nothing else. `DPS_BOOTSTRAP_API_KEY` is a local demo literal and must never be
set outside a laptop. DPS never stores customer data, reveal keys or synthesis seeds.

Connection credentials (M2, `dps/connections.py`): a connection's `secret_ref` names a directory
**under the caller's tenant folder**, `DPS_SECRET_DIR/<tenant_id>/<ref>/` (the tenant comes from the
authenticated request, never from the reference), from which only these files are read:
`username`, `password`, `dsn` (Oracle), `uri` (Mongo), `access_key_id` + `secret_access_key` (S3),
`storage_state.json` (M4) and `ca.pem` (a source's CA bundle; `options.sslrootcert_ref` follows the
same rules). A reference `_shared/<ref>` names `DPS_SECRET_DIR/_shared/<ref>/` and resolves only
for the tenants listed, one per line (`#` comments), in that folder's `tenants` file. Refused when
a connection is created or updated: a reference whose first segment names another top-level folder
(another tenant's) → 403 `secret_ref_foreign_tenant`; an unlisted or missing shared reference → 403
`secret_ref_not_shared`; a malformed one (empty, `.`/`..`/hidden/`_` segments, upper case) → 422
`secret_ref_invalid`. Loading credentials applies the same rules again (and refuses a symlink that
leaves the tenant's folder), answering `secret_unavailable`. A tenant-scoped reference need not exist
when the connection is registered. **Upgrade note:** move every mounted reference from
`DPS_SECRET_DIR/<ref>/` to `DPS_SECRET_DIR/<tenant_id>/<ref>/` (the stored `secret_ref` values do not
change); a leftover top-level folder such as `dps/` makes every tenant's `dps/…` reference refused
as another tenant's. Inline credentials (dev only) are sealed with `DPS_JOB_KEK` into
`dps_connection.secret_enc` and stop resolving after `DPS_INLINE_SECRET_TTL_HOURS`.

Who may change a connection (security fix SEC-3): **any** `PATCH` or `DELETE` of a connection that
is or becomes tagged `production` (a rename, roles, tags, a credential rotation) needs the `admin`
scope (403 `admin_scope_required`). On a connection that holds credentials (a sealed secret or a
`secret_ref`), changing a param that decides where the credentials are sent needs the credentials
again in the same request (new `credentials`, or the `secret_ref` restated) or `admin`; otherwise 403
`connection_endpoint_change_requires_credentials` and nothing changes. The list is one engine-keyed
table, `dps.connections.ENDPOINT_PARAMS`: postgres `host port database dbname options.sslmode
options.sslrootcert_ref`; oracle `host port service_name sid dsn` (+ TLS options); mongo `uri host
port replica_set database options.replicaSet options.directConnection options.tls`; s3
`endpoint_url bucket region`; pvc none. A new `ConnectionParams` field fails
`tests/test_connection_endpoints.py` until it is classified there or in `NOT_ENDPOINT_PARAMS`. The
`connection.update` audit event names the changed fields (`counts["field.params.host"]`, …), never a
value. `POST /connections/{id}/test` takes no param overrides, so it always tests the stored
endpoint. Residual: a `jobs` key may still *create* a connection that names any of its own tenant's
`secret_ref`s with an endpoint of its choice; restrict the key or mount fewer refs per tenant.

## 8. Traps

- `uvicorn dps.app:create_app --factory` — there is no module-level `app`.
- The image installs `dps` **editable** on purpose: `dps/app.py` serves `<source root>/frontend/dist`.
  A wheel install into site-packages starts without the console (`frontend_disabled` in the log).
- The console builds with `base: '/'` (M1): asset URLs are absolute (`/assets/…`, `/theme-init.js`)
  so every nested route (`/playground/documents`, `/policies/:name`, `/data-sources/jobs/new`,
  `/reports/evidence/<id>`) loads its bundles; the app also still resolves `…/assets/<file>` and root
  files under any route prefix (M2 Task 24). Hosting the console under a path prefix
  (`https://host/dps/`) needs a gateway rewrite of that prefix to the root; M0's `./` only worked one
  segment deep.
- FPE fallbacks: with `DPS_FPE_ENABLED=false` the plan turns `fpe_*` into `det_token` with reason
  `…|fallback:fpe_disabled`; `phone` stays `phone` in the plan and runs as `det_token`
  (`fallback:fpe_unavailable`). With FPE on, a domain under 10⁶ still falls back
  (`fallback:fpe_domain_too_small`; e.g. an SSN with `keep_suffix: 4`). `report.fallbacks` counts
  all three, one per value.
- `gdpr-pseudonymise` keeps generic `DATE`s. A date in free text that equals the date-shifted DOB
  stays in clear: the leakage sweep allows a kept value in the leaf where the policy kept it.
- The COIN token mint verifies TLS since SEC-1 (M0 skipped it). A VDI deployment that relied on
  the old behaviour now fails the mint with `LlmCallError: coin mint failed: ConnectError` until it
  sets `DPS_COIN_CA_FILE` (preferred) or `DPS_COIN_TLS_INSECURE=true` (+ the staging/prod exception
  flag, §6). Stellar calls verify against `DPS_SSL_CERT_FILE` (httpx's default store when unset).
- Presidio is recall-only; identifiers with checksums come from the pattern tier regardless.
  Without the `ner` image, names in free text are not detected (key names still are).
- The access log is off (`--no-access-log`): it is stdlib logging and bypasses the allow-list.
- UK mobiles in the Ofcom drama range (`+44 7700 900xxx`, what the surrogate engine generates for
  GB) are not detected as phones **in free text**. A job whose free-text column repeats such a
  number from another column fails closed with `leakage` (the per-row sweep finds the column's
  original in the output). The demo seed therefore keeps GB phones out of `customers.notes`; real
  numbers are valid and detected. Open item for the detector owner (M2-DEVIATIONS, Task 27).
- `docker compose --env-file X` replaces `.env` for interpolation: a lane passes `--env-file .env`
  first, or its image tag and host ports fall back to the defaults (another lane's).
  Per-request evidence is the `audit` line and `/metrics`. Uvicorn's startup lines and, in the NER
  image, Presidio's `Loaded recognizer` lines are plain text (they carry no values).
- `DPS_ENV=local` logs are console-rendered with ANSI colours in `docker compose logs`; use `dev` to
  see the JSON format (it also turns HSTS on).
- The rate limiter, readiness and metrics are per process: run one uvicorn process per container and
  scale with replicas.
- The root filesystem is read-only under compose: anything that writes must use `/tmp` (tmpfs) or a
  volume.
- `/docs` loads Swagger UI from a public CDN; on an air-gapped network use `/api/v1/openapi.json`.
- Selections are validated with **sqlglot 30.22.0** (`connectors` extra, `sqlglot>=26`). Node class
  names drift between sqlglot releases (`AlterTable` → `Alter`, `walk()` yielding tuples, `Expr`
  as the base type): `dps/layers/connectors/selection.py` looks classes up by name and its tests
  pin the rejection codes, so after an upgrade run `pytest tests/connectors/test_selection.py`
  and add renamed classes to `_node_types(...)` — never loosen a test. Oracle renders `DATE '…'` as
  `TO_DATE(:l1, 'YYYY-MM-DD')` and intervals as `NUMTODSINTERVAL(:l1, 'DAY')`; those format/unit
  strings are the only literals left in a compiled selection (plus `ORDER BY` positions and type
  parameters). Denied functions (`DENIED_FUNCTION_PREFIXES`) are refused as calls and in PostgreSQL
  attribute notation, where `t.f` and `(expr).f` call `f(t)`/`f(expr)`: a **qualified** name whose
  last part starts with a denied prefix is `selection_function_denied`, so a qualified column such
  as `a.utl_rate` or `c.ts_status` is refused too — reference it unqualified (an unqualified name
  is never a call). `lo_*` calls are refused; in attribute position only the real large-object
  function names are (`a.lo_limit` stays usable).
- PostgreSQL sources (M2, `dps/layers/connectors/postgres.py`): every read runs in `BEGIN READ ONLY` on a
  session with `statement_timeout` = `DPS_CONNECTOR_STATEMENT_TIMEOUT_SECONDS`, `lock_timeout=5s` and
  `application_name = dps:<action>:<job id>` (what a DBA sees in `pg_stat_activity`). The pool
  resets a session when a read releases it (`pg_advisory_unlock_all()`, `RESET ALL`), so no
  session lock or setting outlives a read. Bind values are
  coerced by the prepared statement's parameter types (a JSON string for a `date` column works; one
  that is not a date fails with `selection_param_invalid`, no value in the message). A refused login
  is `connection_failed` and not retried; network failures are. The connection test's advisory
  `no_sql_string_functions` counts SQL-string/export functions (`dblink*`, `xpath_table`,
  `pg_background_launch`, `aws_s3.query_export_to_s3`, …) the source login can execute: give the
  source login none.
- PostgreSQL targets (M2): `in_place`/`append` keep a write ledger, table `dps_write_ledger` in the
  target schema (or `target.ledger_owner`), created at prepare — the target login needs `CREATE` on
  that schema (or a pre-created ledger with SELECT/INSERT/UPDATE/DELETE). A finished job deletes its
  ledger rows; a **failed** job keeps them, because a retry resumes from the ledger and must never
  re-apply a batch. `in_place` refuses a transformed key (`pk_transform_in_place`: review the key to
  `keep`) and any output that cannot fit an existing column: `det_token` on a bounded column is
  `length_overflow`, and FPE needs ≥ 5 letters and 0 or ≥ 6 digits (a 4-digit PAN falls back to
  `det_token`, which cannot fit `char(10)`). Since verify r2 issue 2 this is caught **before a job
  exists**: discovery never suggests such a method silently (digits-only values with FPE on get
  `fpe_digits`, note `length_kept`; anything else keeps the policy's method under the note
  `length_overflow`, listed under *Needs a decision* with the reason), and `POST /jobs/run` (and
  `POST /jobs/run/validate`, which wizard step 5 asks) refuses an In place or same-table Append with
  422 `length_overflow` naming every column whose static worst case exceeds its length or whose
  `length_overflow` suggestion the reviewer kept. Prepare still checks existing `table` targets and
  names every overflowing column. Discovery predicts FPE fallbacks with
  `fpe.text_domain_ok` / `fpe.digits_domain_ok` (keeps included: SSN `keep_suffix: 4` leaves 5 digits). `table` targets create planned text
  columns as `text`, so they take any method. An `append` to the source table needs an `offset` of at
  least `max(key) + 1 - min(selected key)`, or `generate` on an identity or an owned ascending sequence
  that does not cycle; anything else fails prepare with `pk_strategy_invalid` (new rows would land in
  key ranges still to be read). `ledger_conflict` on a *failed* partition means its ledger rows were
  removed while the job ran: which batches were applied is unknown, so it is never written again.
- Port 8400 busy: `DPS_APP_PORT=8401` for both `docker compose up` and `tools/smoke_test.py` (see
  "Lane-safe stacks", §3).
- Synthesis under a `strict` policy is still refused, rarely, when a reshaped identifier (a keyed
  derangement such as `CUSTOMER_ID`) holds another value of the same record by construction:
  `CUST-xx0928` next to a card expiry `09/28`. The derangement cannot be re-drawn without breaking
  joins, so the sweep fails closed (422). With the Playground JSON sample it happens for about one seed
  in 3,000 to 5,000; retry without the seed (or with another one). Every other surrogate is re-drawn
  when it would hold another value of the record or a part of its names.

- **M2 traps.**
  - asyncpg does not coerce a JSON string into a typed parameter (a `date` bind given as
    `"2024-01-01"` is refused by the driver); DPS coerces every bind by the prepared statement's
    parameter types, and a value that does not parse is `selection_param_invalid` (no value in the
    message).
  - The contract's `DPS_WRITE_LEDGER` is the table `dps_write_ledger` (PostgreSQL folds unquoted names
    to lower case); `ledger_owner` moves it to another schema.
  - On resume the target's write ledger is authoritative over the partition checkpoint: a batch
    committed with its ledger mark but not yet checkpointed is not applied again.
  - `fpe_digits` on a value with fewer than 6 digits falls back to `det_token`, whose output does not
    fit an integer column; discovery therefore suggests `keep` for such numeric keys
    (`numeric_key_kept`) and `det_token` for short text identifiers (`fpe_domain_too_small`).
  - Created target tables (`table` with `create_if_missing`) copy the column definitions only — no
    foreign keys, indexes, defaults or constraints beyond the key.
  - A superseded snapshot (an object accepted again) still drives the jobs that were submitted with
    it; only a dropped or retyped column stops them (`snapshot_stale` at prepare).
  - Homebrew PostgreSQL scratch clusters need `timezone = 'UTC'` (`tools/scratch_pg.sh` sets it);
    tests compare timestamps across the driver and the server.
  - The in-place (and every other generated) key is returned **once**, in the `POST /jobs/run`
    answer; keep it. The evidence pack holds only its `kid`; a retry after the key was purged needs
    it resubmitted (`key_material_required`).
  - `DPS_EMBEDDED_WORKER=true` runs a worker inside the app process for local demos only; posture
    refuses it in staging/prod.
  - The console's step 3 review table has eight columns and scrolls sideways inside its card at
    1440 px (Method, Anchor and Text pipeline are to the right).
  - In place never rewrites a key column (`pk_transform_in_place`), and discovery maps an entity key
    such as `bank_restore.customers.customer_id` (CUSTOMER_ID) to `fpe_digits`. The wizard's step 4
    then shows a failed key check and stays closed; press **Change it in step 3**, **Run again**,
    set the key's method to `keep` and accept the new snapshot (an accepted snapshot never changes).
  - Run the integration gates one at a time against a lane's stack: the service tests share one
    metadata database, one source database and one MinIO bucket with fixed keys (two concurrent runs
    collide, e.g. on `tests/big.bin` in `tests/connectors/test_storage_s3.py`).
  - **M5 `external_tokenize` — the wire contract for the bank's integration team.** DPS calls an
    adapter in front of the bank's tokenization product (Voltage, Protegrity, Thales, in-house):
    `POST {DPS_TOKENIZER_URL}/v1/tokenize` `{"profile", "entity_type", "values": [...]}` → 200
    `{"tokens": [...]}`, `POST …/v1/detokenize` `{"profile", "entity_type", "tokens": [...]}` → 200
    `{"values": [...]}`, `GET …/health` → 200; `Authorization: Bearer <DPS_TOKENIZER_TOKEN(_FILE)>`,
    TLS verified with `DPS_TOKENIZER_CA_FILE` (else `DPS_SSL_CERT_FILE`, else system CAs; https is
    required when `DPS_EGRESS_TLS_REQUIRED=true`). Answers keep order and length, at most
    `DPS_TOKENIZER_BATCH` items per call, deterministic per (profile, entity type, value); a token
    equal to its input, a wrong length or a non-string is refused as malformed. 429/5xx and
    transport errors are retried (`DPS_TOKENIZER_RETRIES`); L1 then answers 502 and L2 retries the
    batch with code `tokenization_unavailable`. Policy: `treat: {SSN: {method: external_tokenize,
    profile: ssn-fpe}}` — `profile` is passed through. Reveal through DPS detokenizes in one call
    per entity type and profile (after the caller's key matched the hints). **SEC-4:** DPS holds
    the service's credentials, so it detokenizes only tokens it produced under the presented key:
    each external hint carries a `bind` tag keyed by the caller's DEK and a server key derived from
    `DPS_JOB_KEK`; an unbound or mismatched tag is refused (403 `external_token_not_bound`) before
    any call, and every batch is audited first (`action: external.detokenize`, entity type and
    count). Consequences: `external_tokenize` needs `DPS_JOB_KEK` on the app (else 400, plan
    reason `|external:unbound`); after a KEK rotation keep `DPS_JOB_KEK_PREVIOUS` for as long as
    callers may reveal external tokens bound under the old KEK — once it is dropped those tokens
    can no longer be revealed through DPS (the bank's own detokenize path still works).
  - **M5 tabular synthesis** (`options.tabular`): the copula model is fitted on a sample of the
    selection; the same seed reproduces the same target only when the whole selection fits in the
    sample (`DPS_COPULA_FIT_ROWS + DPS_COPULA_HOLDOUT_ROWS`), because a larger table is sampled at
    random. A table whose every non-key value combination occurs in the data cannot be synthesized
    row by row (every draw equals a real row): prepare fails with `leakage` and says so.
  - **M5 linked generation** (`options.linked`, MOSTLY engine) writes a *new* dataset with new
    keys (`1..n`, `S00000001…`, `C00000001…`): joins back to production keys are impossible by
    design, and free-text and other unmodelled, non-sensitive columns are written as null. It needs
    the `synth-ml` extra (never in the default image).
  - **M5 synthetic identifiers (D-A).** Synthesize jobs (copula and surrogate) never copy a key or
    identifier: a key with an entity type, a join column (`CUSTOMER_ID`, `EMPLOYEE_ID`, `CASE_ID`)
    and any kept MEDIUM+ column become **keyed pseudonyms** under the job's synthesis seed — use one
    job group (one seed) for tables that must join, and their foreign keys still match. Integer
    pseudonyms lie in bands no real key reaches: a value below 10⁹ maps into `[10⁹, 2·10⁹)` (ten
    digits; an `integer` column holds them), a value of 10 to 18 digits into `[10¹⁸, 2·10¹⁸)` (a
    `bigint`); so a synthetic key is never a real key — unless one column holds keys below 10⁹ *and*
    keys in `[10⁹, 2·10⁹)`. A `smallint` or `numeric(p ≤ 9, 0)` identifier is refused
    (`synthesize_pseudonym_unsupported`); a `numeric(p, 0)` too narrow for a 19-digit pseudonym of a
    long key fails the run closed (`incompatible_method`). Also refused: an `in_place` synthesize
    of a table whose key has an entity type (`synthesize_in_place_sensitive_key`) and an `offset`
    append of such a key; use `table`, `files` or `append` + `generate`. A key the owner
    reviews as *not personal* (entity type cleared) is a technical id and is kept. A column reviewed
    as not personal is copied as it is — if its written sample shows personal data the pack says
    `review_required` (not "passed"). A text pseudonym keeps the key's shape, so it may by chance
    equal another real key of that shape (probability ~n²/|shape|: nil for UUIDs and long mixed
    keys, not for short digit-only text keys); the permutation guarantees that no value maps to
    itself. Columns the run pseudonymises (identifiers, written keys, declared foreign keys) are
    never modelled by the copula. **Government identifiers** (M5 verify r2) are surrogated from
    ranges their issuers never allocate (`dps/engine/synth/unissued.py`: NINO prefixes HMRC never
    uses — not `QQ`, which test data uses —, CURP/RFC inconvenient-word prefixes, NHS `999`, EIN
    unassigned campuses, PAN sequence `0000`, SSN 9xx groups 01–49, ITIN group 89; a generic
    `NATIONAL_ID` by recognised sub-shape); PASSPORT, DRIVER_LICENSE, VOTER_ID, INE_CLAVE, BN, MRZ
    and unrecognised national-id shapes have no such range and are reshaped. After a `table`,
    `append` or `in_place` synthesize the run reads **every** written government/financial
    identifier value and counts (`post_sample.identifier_check`) those found in the same source
    column (keyed 64-bit hashes of the normalised value) and those outside a never-issued range;
    any count above 0, or a check that could not run, makes the pack `review_required`. An
    `append` into a table that already held rows reads only the rows after the table's pre-run
    integer key (`plan.prepare.identifier_floor`, `scope: written_rows`): the target's own rows
    are not the run's output (`bank_perf.transactions` holds 1 000 seed rows with the source's
    IBAN). Fewer rows after that key than the run wrote → every row (`scope: whole_table`,
    `scope_reason: rows_below_floor`). Files targets record `not_run: files_target`. Tools that need *rule-valid* PANs (the demo seed, the
    demo documents, the eval corpus) draw them themselves (`evaluation.corpus.rule_valid_pan`).
- **M3 stack traps.**
  - `docker compose up --wait` fails on the exited one-shots (contract §11): start, then poll
    health. Oracle reports `unhealthy` for good when its init script failed (the entrypoint stops
    at the first error under `WHENEVER SQLERROR EXIT`): read `docker compose logs oracle`, fix the
    script, then `docker compose rm -sf oracle` and start it again (init runs once per fresh
    container).
  - In `docker/oracle/initdb/*.sql` a statement's `;` must end its line: a comment after it keeps
    SQL*Plus buffering, and Oracle 23ai refuses the joined text with `ORA-03405`.
  - Oracle 23ai reports a missing object privilege as `ORA-41900` (`missing INSERT privilege`,
    …), 19c as `ORA-01031`; tests accept both.
  - python-oracledb 26.0.0 thin: a call over `call_timeout` normally fails with `DPY-4024` and the
    session stays usable, but when the server cannot answer the break (PL/SQL asleep) the driver
    closes the connection (`DPY-4011`), and **setting `call_timeout` on that connection crashes the
    process (SIGBUS)**. `OraclePool.acquire` drops a session whose rollback fails; code must never
    reuse a connection after a driver error inside a checkout. Oracle 23ai's `ORA-00001` message
    quotes the duplicate row's values, which is why only driver codes leave `dps/layers/connectors/
    oracle.py` (`connector_error`).
  - python-oracledb 26.0.0 thin binds a bare `datetime` as DATE, dropping the fractional seconds:
    TIMESTAMP(1-6) writes silently lose microseconds and a keyset over a TIMESTAMP(6) key re-reads
    the same rows forever. Every TIMESTAMP(0-6) bind (values and key bounds) goes through
    `oracle_sql.input_type` → `"timestamp"` → `setinputsizes(oracledb.DB_TYPE_TIMESTAMP)`.
  - python-oracledb 26.0.0 thin on 23ai: a `MERGE … USING (SELECT :v … FROM dual)` refuses a CLOB
    or BLOB bind longer than 32,767 bytes (`ORA-01461`; INSERT and UPDATE take such strings), and an
    `executemany` whose column mixes temporary LOBs (`conn.createlob`) with `str` values **stores
    NULL for the LOBs without an error**. `OracleWriter` binds a MERGE batch's LOB column as
    temporary LOBs for every row when any value is over the limit, never for some rows only.
  - Oracle `SAMPLE (p) SEED (s)` is not one stable row set: `COUNT(*)` and keyset pages of the same
    clause saw 81 and 72 of 200 rows. A selection's `sample` is a hash of the key instead
    (`oracle_sql.sample_filter`), so counts, partitions and resumed pages agree.

## 9. Next milestones

M1 documents (done: v0.2.0, §10) → **M2 jobs + PostgreSQL (done: tag `v0.1.0-m2`; Postgres, worker,
migrations, the jobs API and console)** → integrated on `main` (§11) → **M3 Oracle + MongoDB (lane `m3`, §13;
integrated on `main`, §14; tag `v0.4.0-m3` pending)** → M4 UI scanner (browser worker image) →
M5 hardening + showcase. Each adds a section here.

## 10. M1 — documents and Layer 1 complete (v0.2.0)

What M1 adds: `POST /api/v1/documents/{detect,obfuscate,synthesize}` (PDF text layer, scanned pages
and images through OCR, DOCX, TXT, CSV; per-page findings with boxes; a true redacted PDF verified by
structure and an OCR re-read), `POST /api/v1/schema/{generator-spec,synthesize}`, `GET /api/v1/entities`,
`POST /api/v1/policies/test-recognizer`, preset summaries, NDJSON streaming, `Idempotency-Key`, local
Tesseract, the opt-in T3 `llm_label` tier, the detector evaluation harness and report, and the
console's Playground **Document** tab and **Policies** page. API reference:
`docs/technical/api-m1.md`; evaluation: `docs/technical/detector-eval-m1.md`.

**Build.** Unchanged commands (§2). The image's console stage is `node:22-slim`; the runtime installs
`.[fpe,docs]` (`.[fpe,docs,ner]` with `WITH_NER=1`) plus `tesseract-ocr` + `tesseract-ocr-eng` and
sets `DPS_OCR_PROVIDER=auto`. pypdfium2's wheel bundles PDFium; no other system package is needed.

**Run.**

```bash
docker compose --profile docs up --build -d       # app on 127.0.0.1:${DPS_APP_PORT:-8400}, OCR mock on 127.0.0.1:${DPS_OCR_PORT:-5016}
DPS_SMOKE_REQUIRE_OCR=1 python tools/smoke_test.py  # 25 checks: M0's 15 + documents, OCR, schema
DPS_TEST_REQUIRE_SERVICES=1 pytest -q -m "docs or ocr"   # the M1 integration gate (contract §12.4)
python tools/console_check.py                     # browser check of /playground/documents and /policies
docker compose --profile docs down
```

`tools/console_check.py` needs `playwright==1.55.0` (`uv pip install -e ".[uitest]"`, or any venv
holding it) and writes screenshots to `docs/superpowers/build/m1-console/`. The demo flow: open the
console, paste the key, Playground → **Document** → *Load demo KYC scan* → **Redact**.

**Configuration (M1).** All of these are in §4 with their full notes; defaults are the app's.

| Variable | Default | Meaning |
|---|---|---|
| `DPS_OCR_PROVIDER` | `""` (image `auto`, compose `azure`) | `off`/`""`, `azure` (Read v3.2), `tesseract` (local CLI), `auto` (azure when configured, else tesseract, else none) |
| `DPS_AZURE_VISION_ENDPOINT` | — | Read v3.2 base URL: compose `http://azure-ocr-mock:5000`; prod the org gateway (`azure-gateway-api`), `https` |
| `DPS_AZURE_VISION_KEY` | — | `Ocp-Apim-Subscription-Key`; **prod: `DPS_AZURE_VISION_KEY_FILE`** (a mounted secret) |
| `DPS_OCR_HEALTH_PATH` / `DPS_OCR_PROBE_INTERVAL_SECONDS` | `/health` / 30 | readiness probe of the OCR endpoint; behind the org gateway use **`/_gw/health`**; `""` = no probe |
| `DPS_OCR_TIMEOUT_SECONDS` / `DPS_OCR_POLL_INTERVAL_SECONDS` | 60 / 0.5 | one OCR call (analyze + polls) / poll spacing |
| `DPS_OCR_MAX_CONCURRENCY` | 4 | parallel OCR calls per request |
| `DPS_OCR_MIN_TEXT_CHARS` / `DPS_OCR_IMAGE_COVERAGE` | 20 / 0.25 | when a PDF page is OCR'd under `ocr: auto` |
| `DPS_MAX_DOCUMENT_MB` / `DPS_MAX_DOCUMENT_PAGES` | 20 / 200 | upload limits (413) |
| `DPS_PDF_RENDER_DPI` | 200 | raster resolution for OCR and redaction (pages over 10,000 px a side are refused) |
| `DPS_REDACT_IMAGE_FORMAT` / `DPS_REDACT_JPEG_QUALITY` / `DPS_REDACT_OCR_VERIFY` | `jpeg` / 85 / `true` | redacted page rasters; OCR re-read of every produced page |
| `DPS_PREVIEW_DPI` / `DPS_MAX_PREVIEW_PAGES` | 72 / 50 | console page previews |
| `DPS_SCHEMA_MAX_ROWS` | 10,000 | rows per `/schema/synthesize` |
| `DPS_TESSERACT_CMD` / `DPS_TESSERACT_LANGS` / `DPS_TESSERACT_TIMEOUT_SECONDS` | `tesseract` / `eng` / 60 | local OCR |
| `DPS_NDJSON_MAX_ITEMS` / `DPS_NDJSON_CHUNK_ITEMS` / `DPS_MAX_NDJSON_MB` | 1,000,000 / 500 / 256 | NDJSON streams |
| `DPS_IDEMPOTENCY_TTL_SECONDS` / `DPS_IDEMPOTENCY_MAX_ENTRIES` | 600 / 10,000 | replica-local replay cache (fingerprints only, decision D2) |
| `DPS_LLM_LABEL_MAX_FIELDS` / `DPS_LLM_LABEL_MIN_CONFIDENCE` | 40 / 0.7 | T3 labelling (opt-in per policy) |

`.env.example` still lacks the M1 block (the build sessions could not write `.env*` files); the lines
to append are in `docs/superpowers/build/M1-DEVIATIONS.md` (Tasks 1, 19A–19D).

**Probes.** `/readyz` `components.ocr` = `{ok, detail, provider, engine}`, `detail` one of `up`,
`down`, `down:<status>`, `error`, `unconfigured`, `tesseract_missing`; `engine` `azure-read-v3.2` or
`tesseract-<major.minor>`. OCR is **never required**: `/readyz` stays 200 when it is down, and
JSON/text keep working. Metrics added: `dps_document_pages_total{format,source}`,
`dps_redacted_boxes_total`, `dps_ocr_calls_total{engine,outcome}`, `dps_ocr_seconds`,
`dps_llm_label_fields_total{outcome}` (§5).

**Secrets (names only).** `DPS_AZURE_VISION_KEY` (mount it, read it as `DPS_AZURE_VISION_KEY_FILE`).
The compose `mock-key` is a local literal the mock ignores.

**Traps.**

1. PDFium is not thread-safe: every pypdfium2 call goes through `PDFIUM_LOCK`, so renders
   (OCR rasters, redaction, previews) are serialised per process. Scale with replicas, not threads.
2. The OCR client refuses an `Operation-Location` on another origin than
   `DPS_AZURE_VISION_ENDPOINT` (the key is never sent elsewhere). A gateway behind a host-rewriting
   proxy must return its external host in that header.
3. The console builds with `base: '/'` (asset URLs `/assets/…`, `/theme-init.js`): hosting it under
   a path prefix needs a gateway rewrite to the root (§8).
4. Multipart uploads above 1 MiB spool to `/tmp`: compose mounts it as a 256 MB tmpfs; mount `/tmp`
   as an in-memory `emptyDir` in OpenShift too (never a disk-backed layer).
5. `redact.py` sets `reportlab.rl_config.useA85 = 0` process-wide (binary image streams). Anything
   else in the process that uses reportlab gets the same setting.
6. The OCR engine is sent page **images** only: DPS rasterises PDFs itself. Never point a client at
   the mock with a PDF; it reads images.
7. A redacted PDF is image-only by design: no text layer (not searchable), no metadata, annotations
   and forms not drawn, and no key brings anything back. It hides what was **detected**: with NER
   off (the compose image), values the patterns and cues miss — the demo statement's account number,
   street address and payee name — stay visible in the raster, and the OCR re-read cannot flag them
   either (it looks for detected originals and for what the engine finds again).
8. Faker's `es_MX` `curp()` and `rfc()` ignore `seed_instance` (checked with Faker 40.43): never use
   them where determinism matters (the schema executor and the evaluation corpus do not).
9. `playwright==1.55.0` wants Chromium build 1187, which this Mac does not cache:
   `tools/console_check.py` falls back to the newest cached `chrome-headless-shell` (or
   `DPS_PLAYWRIGHT_CHROMIUM`).
10. `tests/test_eval_report.py` fails when the detectors change until the report is regenerated
    (`python tools/eval_detectors.py run …`, `docs/technical/api-m1.md`): by design.
11. With compose's default `DPS_OCR_PROVIDER=azure` and no `--profile docs`, OCR is configured but
    down: scanned pages, images and every redacted PDF are refused with 503 `ocr_unavailable`
    (fail closed), while text-layer PDFs, DOCX, TXT and CSV work. Start the profile, or
    `DPS_OCR_PROVIDER=tesseract` to use the image's own Tesseract.
12. When OCR is configured, a redacted PDF costs one OCR call per produced page (the re-read) plus
    one per page that needs reading; and when a redacted PDF is asked for, every page carrying an
    image is OCR'd (an ID photo or stamp is otherwise burned in unread).

## 11. M1 + M2 integrated on `main` (2026-10-12)

`merge: M1 documents and Layer 1 complete` (`80c26ca`) and `merge: M2 jobs and PostgreSQL`
(`d8f7678`), both `--no-ff`, then the `fix(integrate)` commits below. No tag was made (v0.2.0 stays
the M1 version; `v0.1.0-m2` is M2's lane tag).

**What the integration changed** (each test-first; the contract records §8.2 and §9):

- **One wire error shape.** One handler (`dps.auth.coded_http_exception_handler`, registered by
  `create_app` for every `HTTPException`) renders M1's `CodedHTTPException` and `dps.auth`'s
  `CodedAuthHTTPException` as `{"detail", "code"}`; M2's `DpsHttpError` adds `**extra`; M2's
  `to_http` now keeps an M1 `CodedError`'s (or a coded `HTTPException`'s) status and code instead of
  a 500; a strict audit refusal is 503 `audit_unavailable` on the Layer 1 routes too.
- **One `Idempotency-Key` contract.** The job routes take the header regex, codes and details from
  Layer 1 (`dps.errors`, `dps.layers.api.idempotency`); `Idempotent-Replay` is exposed through CORS.
  Migration `010_job_idempotency_fp.sql` binds a durable key to a keyed fingerprint of its request,
  so the same key with another body (or on the other job route) is 422 `idempotency_key_reused`, as
  on Layer 1, instead of a silent replay of a different job. The wizard mints a new key whenever the
  run request changes.
- **NDJSON under strict audit.** A stream is audited before its `report` line; a strict sink that
  refuses ends the stream with an `audit_unavailable` error line (no report, not recorded for replay).
- **Console.** Every page of both milestones is routed and in the nav (Overview, Playground with the
  Document tab, Policies, Data sources & jobs, the wizard, job detail, Reports, the evidence pack);
  `api.ts` keeps M1's `call`/`callForm` and M2's `request`/`json`/`post`/`qs`; `ApiError.code` is
  `code?: string` (contract §14); `npm test` runs both suites (`node --test` for `frontend/tests`,
  Vitest for `src/**/*.test.ts`). M1's page CSS is scoped to `.documents-page` / `.policies-page`
  (its global `.tbl-scroll > table.tbl { min-width: 760px }`, `.m`, `.prm`, `.plan`, `.two`, `.grp`
  and phone `table.stack` rules restyled the job pages), and there is one disabled-button look
  (mockup 06's greyed `.btn[disabled]`).
- **Compose.** One file: `app`, `dps-db`, `migrate`, `worker` (default), `azure-ocr-mock` (`docs`,
  `full`), `source-pg`, `minio`, `minio-init`, `demo-secrets`, `seed-sources` (`sources`, `full`);
  every host port and built image through the lane-safe variables; the app `depends_on` the
  metadata DB, `migrate` and (optionally) the OCR mock. The image installs
  `.[fpe,docs,db,connectors,tabular]` plus Tesseract; 925 MB on arm64.

**Gates on the integrated tree** (stack: `docker compose --env-file docker/compose.sources.env
--profile docs --profile sources up -d --build` from this checkout, default ports, project
`data-privacy-services`, image `data-privacy-services:local`; fresh `.venv` with
`uv pip install -e ".[dev,fpe,docs,db,connectors,uitest,e2e]"`):

| Gate | Result |
|---|---|
| `ruff format --check dps tests tools` · `ruff check dps tests tools` · `mypy dps` | 247 files formatted · all checks passed · no issues in 103 files |
| `pytest -q` (stack up) | 1593 passed, 4 skipped (`tesseract` ×2 without a host binary, `ner` ×2) |
| `DPS_TEST_REQUIRE_SERVICES=1 pytest -q -m "docs or ocr"` (M1 gate) | 138 passed, 2 skipped (the `tesseract` cases; in the image: 2 passed) |
| `DPS_TEST_REQUIRE_SERVICES=1 pytest -q -m "db or pg_source or s3"` (M2 gate) | 164 passed (after the smoke test created the demo connections the console tests need) |
| `pytest -m slow tests/connectors/test_resume_after_kill.py` | 6 passed |
| `cd frontend && npm ci && npm run typecheck && npm test && npm run build` | 42 node tests + 60 Vitest passed, build ok |
| `DPS_SMOKE_REQUIRE_OCR=1 python tools/smoke_test.py` | 34/34 (15 Layer 1 + 10 documents/OCR/schema + 9 job platform) |
| `python tools/demo_m2.py` | 12 jobs succeeded, verification passed, 0 orphans |
| `python tools/console_check.py` (M1 pages) · `tests/console/test_console_m2.py` (M2 pages) | 84/84 · 3 passed |
| compose logs scan (values, key material) · `/metrics` `dps_llm_raw_pii_sent_total` | 0 hits · 0 |

**Traps (integration).**

1. Run `tools/smoke_test.py` before the M2 gate on a fresh stack: `tests/console/test_console_m2.py`
   (marker `db`) needs the demo connections the smoke test creates, and fails under
   `DPS_TEST_REQUIRE_SERVICES=1` without them.
2. An older stack picks up migration 010 only when the `migrate` one-shot runs again (`docker compose
   up -d` recreates it); until then the app reports `migrations pending or drifted`.
3. Rotating the job KEK changes request fingerprints: a retry that straddles a rotation with the
   same `Idempotency-Key` is refused as `idempotency_key_reused`; mint a new key.
4. New console CSS goes under its page root (`.<name>-page`); the M1 and M2 pages share short class
   names (`.m`, `.plan`, `.two`, `.grp`, `.stack`), so a global rule leaks into the other pages.

### 11.1 Integrated verification r1 fixes (2026-10-12)

Two `fix(integrate)` commits on top of `88eae2e`. Both findings were reproduced first (the new tests fail on the
`88eae2e` tree: 6/6 in `tests/connectors/test_demo_pii_survival.py`; the statement text finds only
one name).

- **B1 — discovery kept PII columns as "not personal" and packs still said "verified · 0 leaks".**
  - Discovery: when no single type reaches `min_score`, rows with *any* MEDIUM+ whole-value finding
    count together; at the bar the column is `MIXED_PII` (`type_note: mixed_entities`,
    `value_entities` names the types) and every value is replaced whole — `det_token` unbounded,
    `hmac_pseudonym` (16) on a bounded column (a token would overflow it in place), `mask_partial`
    below 16; `surrogate` when synthesizing. `value` beside `channel`/`contact_type` is mixed by its
    name. New name cues: `*holder_name` and `<role>_name` (payee, counterparty, …), `line1` /
    `address_line*` / `addr1`, `doc_number` / `document_no` (`NATIONAL_ID`). A kept column whose
    sample still holds MEDIUM+ findings in ≥ 5 % of rows gets `kept_with_hits` (never "not
    personal"); numeric columns below the bar are noise (random 12-digit ids pass Verhoeff 1 in 10).
  - Finalize: the post-write sample counts MEDIUM+ findings in columns the snapshot kept *as not
    personal* (`kept_column_hits`, `kept_columns`; numeric columns only at discovery's bar; keys the
    target generates are not sampled). Any of them → `verification.status: review_required`,
    `passed: false` (either verify mode); `review_required` is on `GET /evidence` rows and
    `progress.verification.kept_hits` on the job. Columns classified but kept on purpose (`CITY:
    keep`, a key kept in place) are `declared_kept_hits` and never gate.
  - Console: wizard step 3 lists mixed and kept-with-hits columns under *Needs a decision*, the
    Kept tile says "no personal data found" / "N with personal data found", a kept column with hits
    shows its hit rate; the evidence chip, job detail, Reports chip and KPI (passed / review required
    / failed) show amber "review required", never "verified · 0 leaks"; the pack's kept list splits
    "kept, no entity" from "kept by the policy or the review". `tools/demo_m2.py` fails on a review.
  - The demo (`bank-uat-refresh-vfix3`) after the fix: `bank_uat.contacts.value`, `kyc_documents
    .doc_number`, `cards.cardholder_name`, `addresses.line1`, `transactions.counterparty_name` 0
    values equal to a source value; in place `bank_restore.contacts` 0 of 40 000 values unchanged.
    Kept by policy and shown as such: `addresses.city` (`CITY: keep`), `cards.expiry`,
    `loan_applicants.employer_name` (`ORG_NAME: keep`).
- **B2 — the redacted scanned KYC kept page 2's account number and customer id.**
  `dps/engine/detect/patterns.py` gains *labelled* patterns — `A/c`, `Acct#`, `Account no.:`,
  `cuenta` + 8–18 digits (`ACCOUNT_NUMBER`), `Customer ID`, `Cust. No.`, `Client id`, `CIF` + an id
  (`CUSTOMER_ID`, also a bare `CUST-`/`CIF` prefix), and a line that starts `Address:`
  (`STREET_ADDRESS`, to a `PIN code`/`Postcode`/`ZIP` cue or the line end); a labelled hit is
  decisive over a checksum shape inside it. Names after a `NEFT/IMPS/RTGS/UPI/NACH/ACH /` narration
  are a name cue. The coverage gaps of a verification now also name every type recognised only by
  a field name (`types recognised only by a field name …`) and the types free text shows only
  next to their label (`types found inside free text only next to their label …`: account
  numbers, customer ids, addresses). The eval corpus has free-text account
  numbers and customer ids, and the committed report an *In free text* table (`ACCOUNT_NUMBER`
  85/85, `CUSTOMER_ID` 70/70). Page 2 (and page 1's address) of the redacted scan re-read by OCR:
  0 findings.

**Gates (final image, `down -v` then `up -d --build`, all profiles).** `ruff format --check` /
`ruff check` on `dps tests tools` (251 files) · `mypy dps` (103 files) · `pytest -q` 1665 passed, 4
skipped (`tesseract` ×2 on the host, presidio, NER model; the tesseract file in the image: 8 passed)
· M1 gate `-m "docs or ocr"` 139 passed, 2 skipped · M2 gate `-m "db or pg_source or s3"` 170 passed
(the 3 console tests among them) · resume-after-kill 6 passed · `npm run typecheck && npm test &&
npm run build` (42 node + 62 Vitest) · `DPS_SMOKE_REQUIRE_OCR=1 tools/smoke_test.py` 34/34 ·
`tools/demo_m2.py` 12 jobs passed, 0 orphans · `tools/console_check.py` 84/84 · eval report and
corpus regenerate byte-identically · compose logs: 0 demo or source values, 0 key material;
`dps_llm_raw_pii_sent_total 0`.

**Traps (r1).**

5. `MIXED_PII` and the labelled patterns change what discovery proposes: a snapshot accepted before
   this fix keeps its old classification; run discovery again to get the new one.
6. Synthetic names come from a finite pool: an appended synthesized table can hold a name that also
   exists in the source (0 rows keep their own original); compare row for row, not by set.

## 12. M5 integrated on `main` (2026-10-14, WP-I)

`git merge --no-ff m5` (`8378da7`): 11 files conflicted (HANDOFF, contract, corpus, connections and
jobs routers, `jobs_run.py`, `targets.py`, `types.ts`, `Reports.tsx`, two test files); both sides kept.
Migrations: M2 001–010, M5 040–045 (no collision; M3 020–029 and M4 030–039 stay free). Compose adds
`tokenizer-mock` (profiles `tokenizer`, `full`); every host port stays a defaulted variable.

**Adaptations on main** (each test-first, commits `fix(integrate): …`):

- `jobs_run` keeps main's `scan_written`/`verification_outcome` and M5's D-A checks together: in a
  synthesize run, keyed pseudonyms count as written by DPS and tabular-sampled columns as synthetic;
  `apply_synthetic_review` adds D-A review columns without ever downgrading `failed`/`incomplete`
  or clearing main's post-write review. Mixed-PII columns are planned (never kept) by synthesize;
  a column reviewed as not personal that holds personal data gives `review_required` in both checks.
- `POST /jobs/run/validate` resolves `@name[:vN]` policies as `POST /jobs/run` does.
- PATCH of a production connection: the admin rule answers (403) before the TLS rule (422).
- SEC-3: Oracle's `options.protocol` (tcp/tcps) is endpoint-defining.
- `GET /reports/evidence` summaries carry `review_required`/`verification_status`; Reports shows
  "review required" (amber).
- The identifier check of a synthesize append reads only the rows it wrote (see §8 above).
- `dps/llm/gateway.py`: the Vertex `contents` list is typed (mypy with every extra installed).

**Traps.** `tokenizer-mock` publishes `${DPS_TOKENIZER_MOCK_PORT:-8420}`, the M4 lane's demo-site
port; on a machine running that lane start main with `DPS_TOKENIZER_MOCK_PORT=8430`. `tools/demo_m2.py`
keeps the group's key in `var/demo/<group>.json`; after `down -v` pass a new `--group` (A-N1).
Gates and results: `~/.cache/dps-build/reports/I-m5.md`.


## 13. M3 — Oracle and MongoDB, cross-system job groups (lane `dps-wt-m3`, 2026-10-14)

M3 adds the `oracle` and `mongo` engines to the job platform (connect → discover → review → run in
`table`, `in_place`, `append`; `files` stays M2's engine-agnostic writer), job groups that share one
caller-held key across systems, and the group consistency check (`POST
/job-groups/{id}/consistency-check`, migration 020). API reference: `docs/technical/api-m3.md`.
Services, ports and the demo secret refs are in §3's table (`oracle`, `mongo`, `demo-secrets`);
settings in §4 "M3 settings".

**Run it (lane ports from `.env`; the defaults are 8400 / 1522 / 27018):**

```bash
docker compose --profile oracle --profile sources up --build -d
docker compose ps        # poll: app, worker, dps-db, source-pg, minio, oracle (allow 1-3 min), mongo healthy;
                         # migrate, minio-init, demo-secrets, seed-sources exited 0 (never `up --wait`)
curl -s 127.0.0.1:${DPS_APP_PORT:-8400}/readyz        # ready: true, db and jobs ok
python tools/seed_sources.py --target all --rows 20000 --seed 7   # PG bank (already seeded by the one-shot),
                         # CRM.* + LOANS.APPLICANTS on Oracle, crm.profiles on MongoDB; a 2nd run prints "unchanged"
python tools/smoke_test.py --m3                       # M0-M2 checks + 7 M3 checks (see below)
python tools/bench_connector.py --engine oracle --rows 20000 --parallelism 4 --partition-rows 5000
python tools/bench_connector.py --engine mongo  --rows 20000 --parallelism 4 --partition-rows 5000
DPS_TEST_REQUIRE_SERVICES=1 pytest -q -m "oracle or mongo"          # M3 integration gate
DPS_TEST_REQUIRE_SERVICES=1 pytest -q tests/test_no_values_in_logs_m3.py
docker compose --profile oracle --profile sources down              # also resets Oracle's demo data (no volume)
```

`smoke_test.py --m3` (after the M0–M2 checks; `DPS_SMOKE_M3_TIMEOUT`, default 600 s, bounds the three
runs together) prints one verdict per check, counts only: `m3 connections` (creates or reuses
`smoke-ora-src` = `dps/src/oracle-crm-ro`, `smoke-ora-tgt` = `dps/tgt/oracle-crm-masked`,
`smoke-mdb-src` = `dps/src/mongo-crm`, `smoke-mdb-tgt` = `dps/tgt/mongo-crm-uat`, and tests them with
M2's `bank-pg-ro`/`bank-uat`), `m3 discover` (`bank.customers`, `crm.profiles`, `CRM.CUSTOMERS`),
`m3 group` (a new `smoke-m3-<epoch>` group; its key is in the create answer only and is dropped after
the three submissions), `m3 runs` (three `table` runs under the group key into
`bank_uat.customers_smoke`, `crm_uat.profiles_smoke`, `CRM_MASKED.CUSTOMERS_SMOKE`; Oracle is submitted
last so it anchors the check), `m3 consistency 3/3`, `m3 evidence` (each pack verified under the
group's key id) and `m3 raw pii to llm`. The seeds must agree: the check samples the newest member
(Oracle), so Oracle may not hold customers the other two lack (seed all three with the same `--rows`).

**Oracle rules (python-oracledb thin, `dps/layers/connectors/oracle.py`, `oracle_sql.py`).**

- Pool (`OraclePool`): synchronous, used only from worker threads; `min=0`, `max=DPS_ORACLE_POOL_MAX`,
  `expire_time=1` (dead peer found within a minute), `call_timeout=DPS_ORACLE_CALL_TIMEOUT_MS` per
  round trip, `tcp_connect_timeout`, a 30 s timed wait for a session; every session carries
  `module=DPS_ORACLE_MODULE`, `action` (`read`/`write`/`test`/…) and `client_identifier` = the job id
  for the DBA's audit. Sessions start with `NLS_SORT = BINARY NLS_COMP = BINARY`, so key order in the
  database is Python's code-point order (keyset paging and partition bounds rely on it). A session
  that failed inside a checkout is dropped, never reused.
- Reads never lock (`FOR UPDATE` is refused by the selection validator and never generated);
  `FETCH FIRST` only on non-locking reads; `arraysize = prefetchrows = batch`.
- Fetch types: CLOB/NCLOB come back whole as `str` (a per-cursor output type handler, not the
  process-global `fetch_lobs`), `IS JSON` columns as text; CLOB offsets are UTF-16 units, so astral
  characters round-trip (pinned by `test_oracle_clob_astral_roundtrip`).
- Binds: `CHAR`/`NCHAR` keys bind as `DB_TYPE_CHAR`/`NCHAR` (a VARCHAR2 bind compares without blank
  padding and finds nothing: `test_oracle_char_key_paging`); `TIMESTAMP(7–9)` keys page through
  `TO_TIMESTAMP(:v, '…FF9')` text so nanoseconds survive (`test_oracle_timestamp9_key_paging`);
  datetimes bind as `DB_TYPE_TIMESTAMP` (a bare `datetime` binds as DATE and drops fractions).
- Keys: the primary key or a unique not-null index; a key-less table is read in **ROWID ranges**
  (`ROWID` pseudo column, read-only — `in_place`, upsert and `query` selections are refused there).
  NVARCHAR2/NCHAR keys run as **one partition** (Oracle orders them by UTF-16 code units).
- Writes: `table` = MERGE upserts or truncate-insert via `executemany`; `in_place` = `UPDATE … WHERE
  key = :k` of the reviewed columns only; `append` with `generate`/`offset`/`token` keys and a
  collision pre-check. Every committed batch advances `DPS_WRITE_LEDGER` (in the target owner's
  schema, created by prepare, cleared at finalize) in the same transaction: a replayed or skipped
  batch is `ledger_conflict` and writes nothing.
- Refused column types for copies: `LONG`, `LONG RAW`, `BFILE`, `XMLTYPE`, `TIMESTAMP WITH LOCAL
  TIME ZONE`, `VECTOR`, object types, `ROWID` columns (`column_unsupported`; project them out).
- Driver errors: only the code leaves the module (`ORA-00942`); Oracle 23ai's `ORA-00001` message
  quotes the duplicate row and is never read.

**MongoDB rules (pymongo `AsyncMongoClient` ≥ 4.13, `mongo.py`, `mongo_docs.py`, `docpaths.py`).**

- The demo server is a one-member replica set (`rs0`) reached with `directConnection=true`;
  transactions (hence `in_place` and every `append`) need a replica set — a standalone server gets
  `transactions_unsupported` at prepare.
- Reads: keyset on `_id` (one BSON type per collection for partitions and `in_place`), whole
  top-level fields projected so array positions stay exact; discovery uses `$sample`; selections
  are allow-listed filter documents (`api-m3.md`). Documents flow through the L1 JSON engine: leaf
  paths (`$.contact.email`, `$.accounts[*].iban`) are classified and transformed; untouched values
  keep their BSON types byte for byte.
- Writes: `table` = `bulk_write(ordered=False)` upserts by `_id` (truncate-insert or upsert);
  `in_place` = `$set` of the transformed leaves by `_id` inside a transaction with the
  `dps_write_ledger` collection; `append` inserts new `_id`s in a transaction with the ledger.
  Field names with `.` or a leading `$` cannot be addressed (`path_unsupported`). A created
  target gets the `_id` index only — **secondary indexes are not copied** (`indexes_not_copied`
  in the prepare report).
- Every operation carries `comment = "dps:<action>:<job id>"` (the profiler/`currentOp` analogue of
  Oracle's module/action); errors keep only `mongo:<code>`; E11000 is `pk_collision`.

**Cross-system joins need FPE.** Oracle `NUMBER` ids, PostgreSQL `bigint` ids and MongoDB
`"CUST-10000123"` strings join only when the key column is protected with a format-preserving method
(`fpe_digits`) under the group's key: `DPS_FPE_ENABLED=true` is the compose default; without it the
FPE methods fall back (reported as fallbacks) and ids stored in different forms no longer join. Date shifts and
persona surrogates are anchor-dependent and are reported as informational in the check.

**Logs.** `configure_logging` caps the driver loggers `pymongo`, `botocore`, `boto3`, `s3transfer`
and `urllib3` at WARNING (with `httpx`/`httpcore` and the document libraries): pymongo's DEBUG
command log writes every document of a write and the server's E11000 reply (which quotes the
duplicate key), botocore's writes request bodies. Found by the M3 log scan's sentinel run (§13
gates); `DPS_LOG_LEVEL=DEBUG` is now safe on app and worker. The `sqlglot` logger is capped at
CRITICAL (it never logs there, so it is silent): parsing a refused selection such as
`EXPLAIN PLAN FOR SELECT … WHERE n = '…'` made sqlglot log the whole statement, literal included,
at WARNING on stderr (M3 G6 review r1; `test_a_refused_selection_never_logs_its_literal`).

**Traps (M3).**

1. Oracle Free's first start takes 1–3 minutes (`healthcheck.sh` returns 4 until the init script
   finished); poll `docker compose ps`, never `up --wait`.
2. `docker compose down` resets Oracle (no volume): reseed with `tools/seed_sources.py --target
   oracle` afterwards; an Oracle container initialised before M3 Task 15 must be recreated
   (`docker compose rm -sfv oracle`) for the widened `NATIONAL_ID`.
3. NVARCHAR2/NCHAR keys run as a single partition; key-less (ROWID) tables cannot be written in
   place or upserted.
4. `date_shift` keeps TIMESTAMP(9) digits: values with more than 6 fractional digits keep their
   sub-microsecond digits after the shift (values with ≤ 6 digits are unchanged in form).
5. Identity keys (`GENERATED BY DEFAULT AS IDENTITY`) and same-table `append`: earlier explicit
   inserts can run ahead of the identity sequence, so appended rows could collide (`ORA-00001` →
   `pk_collision`); check `identity next value > MAX(key)` before such a run.
6. The consistency check samples the newest member's target: re-running one member re-anchors
   it; seed the three systems with the same `--rows` or lower `min_coverage`.
7. A run of `smoke_test.py` without `--m3` rewrites `bank_uat.customers_smoke` without the group
   key; a later consistency check of an older smoke group then fails its PG pairs — run a fresh
   `--m3` smoke instead of re-checking an old group.

**Measured throughput (M3, 2026-10-14; lane image of `717df9a`, tools of this change)** — `python
tools/bench_connector.py --engine <e> --rows 20000 --parallelism 4 --partition-rows 5000 --batch 1000`
against this lane's compose stack on the same Apple-silicon Mac as M2 (host 18 CPUs; Docker Desktop
29.5.3 VM with 12 CPUs and ~15 GiB; Oracle Free 23.26 capped at 2.7 GB, MongoDB 7 at 1 GB),
`DPS_FPE_ENABLED=true`, discovery with the `gdpr-pseudonymise` preset, one worker (concurrency 4),
`table` writes into scratch targets (`CRM_MASKED.CUSTOMERS_BENCH`, `crm_uat.profiles_bench`,
`bank_uat.customers_bench_obfuscate`). Wall clock from submission to the terminal state (prepare,
preflight, partitions, finalize and evidence included — at 20 000 rows the fixed cost is a large
share); results in `docs/technical/bench/m3-2026-10-13-*.json` and the PostgreSQL run at the same
size in `m2-2026-10-13-obfuscate-table-1-worker.json` (dates in UTC):

| Engine · run | Rows | Partitions | Workers | Rows/s | Wall |
|---|---|---|---|---|---|
| Oracle `CRM.CUSTOMERS` (16 columns incl. a CLOB, 10 transformed) → `table` | 20 000 | 4 | 1 | 942 | 21.2 s |
| MongoDB `crm.profiles` (nested documents and arrays) → `table` | 20 000 | 4 | 1 | 860 | 23.3 s |
| PostgreSQL `bench.customers_bench` → `table` (same size and stack, for comparison) | 20 000 | 4 | 1 | 2 474 | 8.1 s |
| PostgreSQL (the M2 figure above) | 200 000 | 4 | 1 | 3 004 | 66.6 s |

Source vs target (counts only, after the smoke and benchmark runs): in `CRM_MASKED.CUSTOMERS_SMOKE`,
`CRM_MASKED.CUSTOMERS_BENCH`, `crm_uat.profiles_smoke`, `crm_uat.profiles_bench` and
`bank_uat.customers_smoke` no target value of a name, national id, tax id, e-mail, mobile, address
line, PIN, IBAN or notes field equals any source value of that field (0 of 20 000 per field; tax id
0 of 11 989). Kept by the preset as non-personal labels: `SEGMENT`/`segment`, `id_type`,
`kyc_status`, address `type`, `tags`, and `CITY` (the gdpr preset keeps CITY; PIN is generalised).
MongoDB `customerId`: 8 (smoke) and 7 (bench) of 20 000 masked ids equal *some other* customer's
source id — expected FF1 collisions in an 8-digit domain (≈ 20 000² / 9·10⁷ ≈ 4.4), not survivors.

**Gates of the G6 build (Tasks 20, 21; 2026-10-14, lane stack up):** `ruff format`/`ruff check dps
tests tools`, `mypy dps`, `pytest -q` (see `docs/superpowers/build/M3-DEVIATIONS.md` "Task 21" for
counts), `pytest -q tests/test_no_values_in_logs_m3.py` and the `oracle and mongo` sentinel run,
`python tools/seed_sources.py --target all --rows 20000 --seed 7` twice (second run `unchanged`),
`python tools/smoke_test.py --m3` (39/39 passed, 2 OCR checks skipped without `--profile docs`),
the two benchmarks above. The lane verification (M3 Definition of Done 1–10) and the tag follow.

## 14. M3 integrated on `main` (2026-10-14, WP-I)

`git merge --no-ff m3` (`b878c50`): 23 files conflicted (HANDOFF, contract, compose, `config.py`,
`batch.py`, connector `base`/`discovery`/`jobs_run`/`registry`/`targets`, `observability.py`,
`pyproject.toml`, `tools/seed_sources.py`, four test files and seven console files); both sides kept.
Migrations: M1–M2 001–010, M3 020, M5 040–045 (each number once, every file in its range). Compose:
`oracle` (profiles `oracle`, `full`) and `mongo` (`sources`, `full`) join M5's `tokenizer-mock`; every
host port stays a defaulted variable. Console: no new page (M3 extends the wizard, the jobs list and
the job page); nav and routes cover every merged page.

**Conflict resolutions worth knowing.** `registry.open_connector` loads the credentials and runs M5's
TLS rule once for every engine, then opens the PostgreSQL, Oracle or MongoDB connector.
`BatchTransformer` takes M5's `tabular`/`constraints`/`pseudonyms` and M3's `leaf_restore`; plans on
document paths are checked against `by_path`. The run pipeline keeps main's `scan_written` (the
review-required gate) and drops M3's `post_sample_hits`, whose document reading moved into it. The
demo seed draws the customers from M3's `tools/seed_model.py` and keeps M5's rule-valid PANs
(`seed_model.pan_for`).

**Adaptations on main** (each test-first, commits `fix(integrate): …` / `test(integrate): …`):

- SEC-2: the Oracle/MongoDB demo refs live under `default/` (compose already wrote them there); M3's
  tests write their refs under each test tenant's folder. Through the API, another tenant's folder
  is `secret_ref_foreign_tenant` and an escaping path 422 (`tests/api/test_api_connections_m3_security.py`).
- SEC-3: a jobs key cannot change or delete a production Oracle/MongoDB connection; every Oracle and
  MongoDB endpoint param (host, port, service name, database, replica set, TLS, Oracle protocol)
  needs the credentials restated (same test file).
- Oracle TLS: `options.protocol=tcps` now really dials `tcps://` (before, M5's rule approved it and the
  connector dialled plain TCP); a secret `dsn` decides the rule when present, by its protocol, not by
  a `tcps` substring.
- Main's discovery fix on M3 data: the mixed-PII name cue reads a MongoDB leaf and its siblings; the
  post-write review, the synthesize kept review and the identifier check key MongoDB leaves by path
  (a classified leaf never vouches for an unclassified sibling; `$.kyc.taxId` is read as a PAN, not
  as the `kyc` sub-document); a date/timestamp counts only as a whole value — `TIMESTAMP(9)`
  fractions read as a "phone" one time in nine and made every Oracle CRM run `review_required`.
- D-A on M3 engines: a MongoDB `ObjectId` key gets a keyed ObjectId pseudonym (every MongoDB
  synthesize into a table or files was refused before); the Oracle `ROWID` pseudo key is never
  pseudonymised; tabular synthesis is refused for a MongoDB source (`tabular_invalid`); a synthesize
  job replaces a replaced column's value where the row's free text repeats it (Oracle `NOTES` repeat
  the customer's city; D-A surrogates `CITY`, and the sweep failed every Oracle synthesize closed);
  external tokenization pre-tokenizes each leaf a document path matches.
- Tests: the migration test checks each milestone range and unique numbers; an address-line leaf keeps
  main's own `line1` cue under an e-mail parent.

**Traps.** The M3 integration tests reseed the shared demo `CRM.*` and `crm.profiles` at test sizes
(2 400 rows); reseed with `tools/seed_sources.py --target all --rows 20000 --seed 7` before a demo or
a source-vs-target count. Console tests overwrite the tracked screenshots under
`docs/superpowers/build/m3-shots/` (A-N2). Start main with `DPS_TOKENIZER_MOCK_PORT=8430` while the
M4 lane holds 8420. Gates and results: `~/.cache/dps-build/reports/I-m3.md`.
