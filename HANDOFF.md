# HANDOFF — data-privacy-services (DPS)

Last verified: — (filled by the release, M5 Task 28: date, commit and the summary line of every gate in §12)

Build and deploy facts only, kept current (the Wega rule): update this file in the same change as
anything that alters an image, a port, a setting, an extra, a probe, a label or a deploy step. What
each milestone delivered and its exit evidence are in `CHANGELOG.md`; the per-task build records in
`docs/superpowers/build/`. §3's table is generated (`python tools/settings_table.py`), and
`tests/test_handoff.py` fails while a setting, a compose service, an OpenShift role or a mounted
secret is missing here.

**Scope of this revision (2026-10-14: M0, M1, M2, M3 and M5 on `main`).** The UI scanner (M4: `Dockerfile.browser`, `browser-worker`, `demo-site`, the scan
settings), the arbitrary-UID images and compose `full` work (M5 Task 24), the OpenShift manifests
(Task 25) and the ten-minute demo (Task 26) arrive through their own merges; the places their
facts go are marked **(at the M4 merge)** / **(Task 24/25/26)** below and are filled when they land.

## 1. What ships

| Artefact | Kind | Notes |
|---|---|---|
| `data-privacy-services` | container image | FastAPI API + compiled console in one process on port **8400**; non-root uid **10001**. One image, three roles: `app` (`uvicorn dps.app:create_app --factory`), `worker` (`python -m dps.worker`, ops port **9400**) and `migrate` (`python -m dps.migrate`, a one-shot / Job). Extras baked in: `fpe`, `docs`, `db`, `connectors`, `tabular`; `--build-arg WITH_NER=1` adds `ner` + `en_core_web_sm`. The `synth-ml` extra (MOSTLY AI, ≈ 2 GB with torch) is never in this image: a bank-side opt-in in a derived image after the licence and size review (Task 24 adds `--build-arg EXTRAS=synth-ml`) |
| `data-privacy-services-browser` | container image **(at the M4 merge)** | the `browser-worker` role (Chromium, ops port **9401**) built from `Dockerfile.browser` on `mcr.microsoft.com/playwright:v1.55.0-noble` with the `scanner` extra |
| `dps` | Python 3.12 package, version 0.2.0 (1.0.0 at the release) | Privacy Engine, Layer 1 API and documents, the job platform (PostgreSQL, Oracle, MongoDB), tabular synthesis, admin, reports, policy store. Extras: see the table below |
| `frontend/dist` | Vite + React build | compiled inside the image (stage 1) and served by the app at `/`; never committed (`.gitignore`, `.dockerignore`) |
| `deploy/openshift` | kustomize **(Task 25)** | base + components + `dev`/`prod` overlays: Deployments `dps-app`, `dps-worker`, `dps-browser-worker`, the Job `dps-migrate-<release>` (§7) |
| `tools/` (copied into the image at `/app/tools`) | operator scripts | `smoke_test.py` (end-to-end checks against a stack), `seed_sources.py` (+ `seed_model.py`, `seed_oracle.py`, `seed_mongo.py`: one synthetic customer space in PostgreSQL, Oracle and MongoDB), `demo_m2.py` (10-table UAT copy, in place, append), `bench_connector.py` (measured rows/s), `reveal_rows.py` (local reveal of output rows with the caller's key), `console_check.py` (browser check of the M1 pages), `eval_detectors.py`, `make_demo_docs.py`, `settings_table.py`, `scratch_pg.sh` (PostgreSQL 16 without Docker). Task 24 adds `demo_client.py` and `flow_report.py`, Task 26 `demo_seed.py` and `e2e_demo.py` |
| Operator modules | `python -m …` in the image | `dps.migrate` (DDL, `--verify`, `--maintain`), `dps.worker`, `dps.keys_cli` (DB-backed API keys), `dps.rekey` (KEK rotation) |

Python extras (`pyproject.toml`):

| Extra | Contents | In the image |
|---|---|---|
| `fpe` | `fastfpe` (FF1, NIST SP 800-38G); used only with `DPS_FPE_ENABLED=true` | yes |
| `docs` | pypdf, pdfplumber, pypdfium2, reportlab, python-docx, Pillow — the app imports the document pipeline at startup | yes |
| `db` | asyncpg (metadata database, PostgreSQL connector) | yes |
| `connectors` | sqlglot, pyarrow, boto3, `oracledb==26.0.0` (thin), pymongo (`AsyncMongoClient`) | yes |
| `tabular` | numpy, scipy (Gaussian copula, fidelity/privacy metrics) | yes |
| `ner` | presidio-analyzer, spaCy | only with `WITH_NER=1` |
| `vertex` | google-genai (laptop path to Gemini) | no |
| `synth-ml` | `mostlyai[local]`, pandas (optional MOSTLY AI engine) | never |
| `uitest`, `e2e` | `playwright==1.55.0` (console checks) | no |
| `dev` | pytest, pytest-asyncio, pytest-timeout, ruff, mypy, types-PyYAML | no |

## 2. Build

```bash
docker build -t data-privacy-services:local .                                      # what compose builds (tag ${DPS_IMAGE_TAG:-local})
docker build --platform linux/amd64 -t <registry>/data-privacy-services:<semver> .   # amd64 from a Mac
docker build --build-arg WITH_NER=1 -t <registry>/data-privacy-services:<semver>-ner .
```

Tag every image with the semver **and** the git sha; never deploy `latest` alone. The Dockerfile:

- stage 1 `node:22-slim`: `npm ci` (lockfile only, no `npm install` fallback) + `npm run build`;
- stage 2 `python:3.12-slim`: `tesseract-ocr` + `tesseract-ocr-eng` from apt and `ENV
  DPS_OCR_PROVIDER=auto` (local OCR when no Azure endpoint is configured); `uv` 0.11.21 pinned
  (`COPY --from=ghcr.io/astral-sh/uv:0.11.21`); `uv pip install --system --no-cache -e
  ".[fpe,docs,db,connectors,tabular]"` (`WITH_NER=1`: `.[fpe,ner,docs,db,connectors,tabular]` and
  `python -m spacy download en_core_web_sm`); the console at `/app/frontend/dist`; `tools/` at
  `/app/tools`; `/app/var/{artefacts,exports}` owned by uid 10001;
- the package is installed **editable** on purpose: `dps/app.py` serves `<source root>/frontend/dist`
  (a wheel install starts without the console: `frontend_disabled` in the log);
- `USER 10001`, `EXPOSE 8400`, a Python `urllib` `HEALTHCHECK` on `http://127.0.0.1:8400/health`
  (15 s interval and start period); `ENTRYPOINT docker/entrypoint.sh` (runs `python -m dps.migrate`
  first only when `DPS_MIGRATIONS_MODE=auto`, `DPS_PG_HOST` is set and the command is `uvicorn` —
  single-container runs; compose and OpenShift use the separate migrate one-shot/Job);
  `CMD uvicorn dps.app:create_app --factory --host 0.0.0.0 --port 8400 --no-access-log`.

**Arbitrary UID (Task 24).** OpenShift runs pods under an arbitrary UID in group 0 with a read-only
root filesystem: Task 24 adds `chgrp -R 0 /app && chmod -R g=u /app` and `ENV HOME=/tmp` (writes go
to `/tmp`, an `emptyDir`) and the same lines plus `/ms-playwright` to `Dockerfile.browser`.

Measured sizes (arm64): M1 lane 644 MB (apt layer 107 MB); the integrated M1 + M2 image 925 MB;
`WITH_NER=1` adds ≈ 370 MB. `.dockerignore` keeps `.git`, `.venv`, `docs`, `demo`, `tests`, `var`,
every `.env*` and key file out of the build context.

Development install and checks:

```bash
uv venv --python 3.12 .venv && source .venv/bin/activate
uv pip install -e ".[dev,fpe,docs,db,connectors,tabular,uitest,e2e]"    # + ner, vertex as needed
python -m playwright install chromium                                    # once, for the console checks
(cd frontend && npm ci && npm run typecheck && npm test && npm run build)
```

`npm test` runs both console suites: `node --test` over `frontend/tests/*.test.ts` and Vitest over
`frontend/src/**/*.test.{ts,tsx}` (server-side rendering, no DOM library).

## 3. Configuration

### 3.1 How settings are read

Every setting is a `DPS_<NAME>` environment variable read by `dps.config.Settings`. Precedence:
constructor arguments > a literal environment variable > `DPS_<NAME>_FILE` (the value read from a
mounted file; works for **every** setting) > a `.env` file in the working directory (kept out of
the image by `.dockerignore`) > the default. Validation errors name the setting, never its value.
In staging/prod the secret settings must come from `_FILE` (§3.3).

### 3.2 Every setting

<!-- settings-table:start -->
<!-- generated by `python tools/settings_table.py` from dps.config.Settings: do not edit -->

| Variable | Default | Secret | Since | What it does |
|---|---|---|---|---|
| `DPS_APP_NAME` | `data-privacy-services` |  | M0 | Declared, not read by the app. |
| `DPS_ENV` | `local` |  | M0 | `local`, `dev`, `staging` or `prod`; `staging`/`prod` enforce the posture guard at boot and serve no `/docs` or OpenAPI document; outside `local`: JSON logs and HSTS. |
| `DPS_LOG_LEVEL` | `INFO` |  | M0 | Log level of the allow-listed structured logs. |
| `DPS_HOST` | `0.0.0.0` |  | M0 | Address the worker's ops server (health, readiness, metrics) binds to. |
| `DPS_PORT` | `8400` |  | M0 | Declared, not read: the image's command fixes `--port 8400`. |
| `DPS_AUTH_ENABLED` | `true` |  | M0 | `false` only on a laptop (every request then runs as a wildcard `local-dev` principal); refused in staging/prod. |
| `DPS_API_KEYS` | — | secret (`_FILE`) | M0 | Static keys: a JSON list of `{name, key or key_sha256, tenant_ids, scopes}` (no implicit wildcard); staging/prod read it from `DPS_API_KEYS_FILE`. DB-backed keys come from `/admin/keys` and `python -m dps.keys_cli` (M5). |
| `DPS_BOOTSTRAP_API_KEY` | — | secret (`_FILE`) | M0 | One wildcard key (every tenant and scope) for local demos only; refused in staging/prod. |
| `DPS_MAX_PAYLOAD_MB` | `8` |  | M0 | JSON request body limit (413 `payload_too_large`). |
| `DPS_MAX_TEXT_CHARS` | `1000000` |  | M0 | Text characters per request, a document's extracted text included (413 beyond). |
| `DPS_MAX_ITEMS` | `10000` |  | M0 | JSON leaf values per request or per NDJSON item (413 beyond). |
| `DPS_RATE_LIMIT_ENABLED` | `true` |  | M0 | Token-bucket rate limit per API key, per process. |
| `DPS_RATE_LIMIT_RPS` | `50.0` |  | M0 | Refill rate of the bucket (requests per second); 429 + `Retry-After`. |
| `DPS_RATE_LIMIT_BURST` | `100` |  | M0 | Bucket size (requests). |
| `DPS_DEFAULT_POLICY` | `gdpr-pseudonymise` |  | M0 | Shipped preset used when a request names no policy (strict verification in staging/prod). |
| `DPS_MIN_SCORE` | `0.6` |  | M0 | Detector score floor for `POST /schema/generator-spec` samples; every other path uses the policy's `detect.min_score`. |
| `DPS_NER_ENABLED` | — |  | M0 | Unset = on when `presidio-analyzer` imports; `true` requires the `ner` extra and a spaCy model; `false` = patterns and cues only. |
| `DPS_NER_LANGUAGES` | `en,es` |  | M0 | NER languages; those with an installed model load (the `WITH_NER=1` image ships `en`). |
| `DPS_FPE_ENABLED` | `false` |  | M0 | FF1 format-preserving methods (`fastfpe`); `false` falls back to `det_token` and reports it. Flip only after the licence review (D8); compose sets `true`. |
| `DPS_DEFAULT_LOCALE` | `en_US` |  | M0 | Locale for phones, dates and surrogates unless the request or the policy sets one. |
| `DPS_LLM_PROVIDER` | — |  | M0 | `stellar` (production, VDI), `vertex` (laptop), `stub` (tests, demo; refused in staging/prod) or empty (no LLM). The gateway only ever sees typed placeholders. |
| `DPS_LLM_MODEL` | `gemini-2.5-flash` |  | M0 | Model name sent to the gateway. |
| `DPS_LLM_TIMEOUT_SECONDS` | `30.0` |  | M0 | Timeout of one LLM call. |
| `DPS_LLM_TEXT_REWRITE` | `false` |  | M0 | Global switch for the placeholder-only text rewrite; a request or policy can only narrow it. |
| `DPS_STELLAR_BASE_URL` | — |  | M0 | Stellar gateway base URL (`https` in staging/prod). |
| `DPS_COIN_URL` | — |  | M0 | COIN OAuth2 token endpoint (`https` in staging/prod). |
| `DPS_COIN_CLIENT_ID` | — | secret (`_FILE`) | M0 | COIN client id, base64-encoded (org convention). |
| `DPS_COIN_CLIENT_SECRET` | — | secret (`_FILE`) | M0 | COIN client secret, base64-encoded. |
| `DPS_COIN_SCOPE` | — |  | M0 | COIN scope, base64-encoded; sent as `coinscope<scope>`. |
| `DPS_COIN_TOKEN_TTL_SECONDS` | `840` |  | M0 | How long a minted COIN token is reused. |
| `DPS_SSL_CERT_FILE` | — |  | M0 | Organisation CA bundle for egress (Stellar, and the COIN mint without `DPS_COIN_CA_FILE`); required in staging/prod when any egress URL is set. |
| `DPS_COIN_CA_FILE` | — |  | M0 | CA bundle (file or directory) for the COIN token mint; else `DPS_SSL_CERT_FILE`. |
| `DPS_COIN_TLS_INSECURE` | `false` |  | M0 | Skip TLS verification on the COIN mint only (the VDI's intercepting proxy); `/readyz` then reports `coin_tls: insecure`. |
| `DPS_ALLOW_INSECURE_COIN_MINT` | `false` |  | M0 | The documented VDI exception: staging/prod refuse `DPS_COIN_TLS_INSECURE=true` without it. |
| `DPS_VERTEX_PROJECT` | — |  | M0 | Vertex AI project (laptop path; needs the `vertex` extra and ADC). |
| `DPS_VERTEX_LOCATION` | `global` |  | M0 | Vertex AI location. |
| `DPS_AUDIT_ENABLED` | `true` |  | M0 | One value-free audit event per request and job action; refused off in staging/prod. |
| `DPS_OCR_PROVIDER` | — |  | M1 | `off`/empty, `azure` (Read v3.2: mock, org gateway, Azure), `tesseract` (local CLI) or `auto` (azure when endpoint and key are set, else tesseract when on PATH; the image's default). |
| `DPS_AZURE_VISION_ENDPOINT` | — |  | M1 | Read v3.2 base URL (compose: the mock; production: the org OCR gateway, `https`). |
| `DPS_AZURE_VISION_KEY` | — | secret (`_FILE`) | M1 | `Ocp-Apim-Subscription-Key` of the Read v3.2 endpoint. |
| `DPS_OCR_TIMEOUT_SECONDS` | `60.0` |  | M1 | One OCR call (analyze plus polls). |
| `DPS_OCR_POLL_INTERVAL_SECONDS` | `0.5` |  | M1 | Spacing of the Read v3.2 result polls. |
| `DPS_MAX_DOCUMENT_MB` | `20` |  | M1 | Uploaded document limit (413 `document_too_large`; the body may be 1 MiB larger). |
| `DPS_MAX_DOCUMENT_PAGES` | `200` |  | M1 | Pages per document (413 beyond). |
| `DPS_PDF_RENDER_DPI` | `200` |  | M1 | Raster resolution for OCR and redaction (pages over 10,000 px a side are refused). |
| `DPS_SCHEMA_MAX_ROWS` | `10000` |  | M1 | Rows per `POST /schema/synthesize` call. |
| `DPS_OCR_HEALTH_PATH` | `/health` |  | M1 | Readiness probe path of the OCR endpoint (`/_gw/health` behind the org gateway; empty = no probe). |
| `DPS_OCR_PROBE_INTERVAL_SECONDS` | `30.0` |  | M1 | OCR readiness probe interval (`0` = once at startup). |
| `DPS_OCR_MAX_CONCURRENCY` | `4` |  | M1 | Parallel OCR calls per document; also bounds parallel Tesseract runs per process. |
| `DPS_OCR_MIN_TEXT_CHARS` | `20` |  | M1 | Under `ocr: auto`, a PDF page with fewer text-layer characters is OCR'd. |
| `DPS_OCR_IMAGE_COVERAGE` | `0.25` |  | M1 | Under `ocr: auto`, a PDF page whose images cover this share of it is OCR'd. |
| `DPS_REDACT_IMAGE_FORMAT` | `jpeg` |  | M1 | `jpeg` or `png` page rasters inside a redacted PDF. |
| `DPS_REDACT_JPEG_QUALITY` | `85` |  | M1 | JPEG quality of redacted page rasters. |
| `DPS_REDACT_OCR_VERIFY` | `true` |  | M1 | Re-read every page of a redacted PDF with OCR (when configured) and refuse it (422 `leakage`) if a redacted original is still readable. |
| `DPS_PREVIEW_DPI` | `72` |  | M1 | Console page-preview resolution. |
| `DPS_MAX_PREVIEW_PAGES` | `50` |  | M1 | Pages previewed in the console per document. |
| `DPS_TESSERACT_CMD` | `tesseract` |  | M1 | Tesseract binary (name on PATH or a path). |
| `DPS_TESSERACT_LANGS` | `eng` |  | M1 | Tesseract languages (`eng`, `eng+spa`, …; the image ships `eng`). |
| `DPS_TESSERACT_TIMEOUT_SECONDS` | `60.0` |  | M1 | One page's Tesseract time limit (the process group is killed: `ocr_timeout`). |
| `DPS_LLM_LABEL_MAX_FIELDS` | `40` |  | M1 | T3 labelling (opt-in tier `llm_label`): fields per call (names, types and value shapes only, never values). |
| `DPS_LLM_LABEL_MIN_CONFIDENCE` | `0.7` |  | M1 | T3 labelling: verdicts below this confidence are dropped. |
| `DPS_NDJSON_MAX_ITEMS` | `1000000` |  | M1 | Items per NDJSON stream (413 `ndjson_too_large`). |
| `DPS_NDJSON_CHUNK_ITEMS` | `500` |  | M1 | NDJSON items per worker-thread hop. |
| `DPS_MAX_NDJSON_MB` | `256` |  | M1 | Body limit of an `application/x-ndjson` request (held in memory while it streams). |
| `DPS_IDEMPOTENCY_TTL_SECONDS` | `600` |  | M1 | `Idempotency-Key` replay window on the Layer 1 routes (replica-local fingerprints only, never key material — D2); `0` disables the cache. |
| `DPS_IDEMPOTENCY_MAX_ENTRIES` | `10000` |  | M1 | Entries in the Layer 1 replay cache. |
| `DPS_PG_HOST` | — |  | M2 | Metadata PostgreSQL host; empty = Layer 1 only (no jobs, no keys from the database, no M2 posture checks). |
| `DPS_PG_PORT` | `5432` |  | M2 | Metadata PostgreSQL port. |
| `DPS_PG_DATABASE` | `dps` |  | M2 | Metadata database. |
| `DPS_PG_SCHEMA` | `dps` |  | M2 | Schema of the DPS tables (`^[a-z_][a-z0-9_]{0,62}$`). |
| `DPS_PG_USER` | `dps_app` |  | M2 | API pool login (`dps_app`). |
| `DPS_PG_PASSWORD` | — | secret (`_FILE`) | M2 | API pool password. |
| `DPS_PG_WORKER_USER` | — |  | M2 | Worker pool login (`dps_worker_login`); empty = `DPS_PG_USER`. |
| `DPS_PG_WORKER_PASSWORD` | — | secret (`_FILE`) | M2 | Worker pool password. |
| `DPS_PG_MIGRATION_USER` | — |  | M2 | DDL login for `python -m dps.migrate` (`dps_owner`); empty = `DPS_PG_USER`. |
| `DPS_PG_MIGRATION_PASSWORD` | — | secret (`_FILE`) | M2 | DDL login password (the migrate Job only). |
| `DPS_PG_POOL_MIN` | `2` |  | M2 | Minimum connections per pool. |
| `DPS_PG_POOL_MAX` | `16` |  | M2 | Maximum connections per pool. |
| `DPS_PG_SSL_ROOT_CERT` | — |  | M2 | CA file that turns on verified TLS to the metadata database (required in staging/prod). |
| `DPS_RLS_ENABLED` | `true` |  | M2 | Tenant row-level security; must stay `true` in staging/prod. |
| `DPS_MIGRATIONS_MODE` | `auto` |  | M2 | `auto` (apply at boot), `verify` (staging/prod: check only — while migrations are pending or changed the job routes answer 503 `db_unavailable`) or `off` (tests). |
| `DPS_JOB_KEK` | — | secret (`_FILE`) | M2 | Key-encryption key (base64 of 32 bytes): seals job keys, specs and inline credentials and keys request fingerprints and the external-token binding; staging/prod read it only from `DPS_JOB_KEK_FILE`. |
| `DPS_JOB_KEK_PREVIOUS` | — | secret (`_FILE`) | M2 | The previous KEK during a rotation; `python -m dps.rekey` re-seals what only it opens (M5). |
| `DPS_JOB_LEASE_SECONDS` | `300` |  | M2 | Lease of a claimed job (heartbeats renew it). |
| `DPS_PARTITION_LEASE_SECONDS` | `120` |  | M2 | Lease of a claimed partition. |
| `DPS_JOB_MAX_ATTEMPTS` | `3` |  | M2 | Attempts of a job before it is dead-lettered (its key purged). |
| `DPS_PARTITION_MAX_ATTEMPTS` | `5` |  | M2 | Attempts of a partition before it is dead-lettered (the run then fails). |
| `DPS_JOB_RETRY_BASE_SECONDS` | `30.0` |  | M2 | Retry back-off: `min(max, base × 2^attempts)` seconds with ±50 % jitter. |
| `DPS_JOB_RETRY_MAX_SECONDS` | `3600.0` |  | M2 | Ceiling of the retry back-off. |
| `DPS_JOB_POLL_INTERVAL_SECONDS` | `2.0` |  | M2 | Idle worker's queue poll interval. |
| `DPS_JOB_CLAIM_BATCH` | `4` |  | M2 | Most queue items a worker claims per poll. |
| `DPS_JOB_REAPER_INTERVAL_SECONDS` | `30.0` |  | M2 | How often each worker reaps expired leases. |
| `DPS_JOB_DRAIN_TIMEOUT_SECONDS` | `30.0` |  | M2 | On SIGTERM: time to finish claimed work before releasing it without charging an attempt. |
| `DPS_JOB_TENANT_MAX_RUNNING` | `8` |  | M2 | Running jobs per tenant (a `dps_tenant` row overrides). |
| `DPS_JOB_TENANT_MAX_QUEUED` | `1000` |  | M2 | Queued jobs per tenant (a `dps_tenant` row overrides). |
| `DPS_WORKER_CONCURRENCY` | `4` |  | M2 | Concurrent tasks per worker process. |
| `DPS_WORKER_METRICS_PORT` | `9400` |  | M2 | Worker ops server (`/health`, `/readyz`, `/metrics`); `0` turns it off. |
| `DPS_EMBEDDED_WORKER` | `false` |  | M2 | Run a worker inside the app process (local demos only; refused in staging/prod). |
| `DPS_BATCH_ROWS` | `1000` |  | M2 | Rows per transform-and-write batch (a run may set its own). |
| `DPS_PARTITION_ROWS` | `50000` |  | M2 | Rows per partition (a run may set its own). |
| `DPS_MAX_PARTITIONS` | `256` |  | M2 | Most partitions per run. |
| `DPS_DISCOVERY_SAMPLE_ROWS` | `2000` |  | M2 | Rows sampled per object by discovery. |
| `DPS_PREFLIGHT_ROWS` | `500` |  | M2 | Rows transformed at prepare, before any write. |
| `DPS_CONNECTOR_CONNECT_TIMEOUT_SECONDS` | `10.0` |  | M2 | Connect timeout to a source or target. |
| `DPS_CONNECTOR_STATEMENT_TIMEOUT_SECONDS` | `300.0` |  | M2 | Statement timeout of connector reads and writes. |
| `DPS_SECRET_DIR` | `/var/run/secrets/dps` |  | M2 | Root of the connection `secret_ref` folders, per tenant: `<dir>/<tenant_id>/<ref>/` (and `<dir>/_shared/<ref>/` for listed tenants). |
| `DPS_INLINE_SECRETS_ALLOWED` | `true` |  | M2 | Accept inline connection credentials (sealed with the KEK); refused in staging/prod. |
| `DPS_INLINE_SECRET_TTL_HOURS` | `24` |  | M2 | Inline credentials stop resolving after this. |
| `DPS_ORACLE_POOL_MAX` | `4` |  | M3 | Sessions per Oracle connection pool (1–64). |
| `DPS_ORACLE_CALL_TIMEOUT_MS` | `30000` |  | M3 | Per round trip to Oracle (1,000–3,600,000; a hung call becomes an error). |
| `DPS_ORACLE_MODULE` | `dps` |  | M3 | `v$session.module` of every DPS session, for the DBA's audit (1–48 of `[A-Za-z0-9_.-]`). |
| `DPS_MONGO_SERVER_SELECTION_TIMEOUT_MS` | `5000` |  | M3 | MongoDB server selection timeout (500–120,000). |
| `DPS_RETENTION_DAYS` | `30` |  | M2 | Days a finished job, its artefacts and evidence are kept (a tenant's own setting wins). |
| `DPS_RETENTION_INTERVAL_HOURS` | `6` |  | M2 | One retention job per interval across all workers (owner `_system`). |
| `DPS_AUDIT_RETENTION_MONTHS` | `13` |  | M2 | `python -m dps.migrate --maintain` drops audit partitions older than this. |
| `DPS_AUDIT_STRICT` | `false` |  | M2 | A request whose audit event cannot be written fails (503 `audit_unavailable`); required in staging/prod. |
| `DPS_AUDIT_QUEUE_MAX` | `10000` |  | M2 | Bound of the database audit sink's queue. |
| `DPS_AUDIT_FLUSH_BATCH` | `200` |  | M2 | Audit rows per insert. |
| `DPS_AUDIT_FLUSH_MS` | `250` |  | M2 | Audit flush interval. |
| `DPS_ARTEFACT_STORE` | `local` |  | M2 | `local` (a directory or volume) or `s3` (S3, MinIO). |
| `DPS_ARTEFACT_DIR` | `./var/artefacts` |  | M2 | Artefact directory of the `local` store. |
| `DPS_S3_ENDPOINT_URL` | — |  | M2 | S3 endpoint (empty = AWS; `https` in staging/prod). |
| `DPS_S3_REGION` | `us-east-1` |  | M2 | S3 region. |
| `DPS_S3_BUCKET` | `dps-artefacts` |  | M2 | Artefact bucket. |
| `DPS_S3_ACCESS_KEY_ID` | — | secret (`_FILE`) | M2 | S3 access key id. |
| `DPS_S3_SECRET_ACCESS_KEY` | — | secret (`_FILE`) | M2 | S3 secret access key. |
| `DPS_S3_FORCE_PATH_STYLE` | `true` |  | M2 | Path-style S3 addressing (MinIO). |
| `DPS_EXPORT_ROOT` | `./var/exports` |  | M2 | Root of `files` targets on a mounted volume (`pvc` connections). |
| `DPS_IMAGE_DIGEST` | — |  | M2 | Image digest stamped into every evidence pack. |
| `DPS_SYNTH_TABULAR_ENGINE` | `copula` |  | M5 | Enabled tabular engines, a comma list of `copula` and `mostly` (the `synth-ml` extra); empty = tabular synthesis off. |
| `DPS_COPULA_FIT_ROWS` | `100000` |  | M5 | Rows the copula is fitted on at prepare. |
| `DPS_COPULA_HOLDOUT_ROWS` | `20000` |  | M5 | Rows held out, never fitted, to measure privacy. |
| `DPS_COPULA_METRICS_ROWS` | `5000` |  | M5 | Preview rows run through the real batch transformer to measure fidelity and privacy. |
| `DPS_COPULA_RARE_FLOOR` | `0.01` |  | M5 | Categories below this share are never emitted (0–0.2). |
| `DPS_COPULA_MAX_RESAMPLE` | `8` |  | M5 | Redraws per row before a batch fails closed with `exact_row_match`. |
| `DPS_FIDELITY_COLUMN_LIMIT` | `0.05` |  | M5 | KS/TVD per column shown as within limit. |
| `DPS_FIDELITY_CORR_LIMIT` | `0.1` |  | M5 | Mean correlation delta shown as within limit. |
| `DPS_PRIVACY_NNDR_TARGET` | `0.95` |  | M5 | Nearest-neighbour distance ratio at or above which privacy passes. |
| `DPS_PRIVACY_NNDR_ENFORCE` | `false` |  | M5 | `true`: a ratio below the target fails prepare (`privacy_gate`). |
| `DPS_MOSTLY_POOL_ROWS` | `20000` |  | M5 | Rows in the optional MOSTLY AI engine's generated pool per job (repeated when the source is larger). |
| `DPS_MOSTLY_MAX_TRAINING_MINUTES` | `10` |  | M5 | MOSTLY AI training time limit. |
| `DPS_TOKENIZER_PROVIDER` | — |  | M5 | `http` = the bank tokenization service adapter (`external_tokenize`); empty = off. |
| `DPS_TOKENIZER_URL` | — |  | M5 | Base URL of the tokenization adapter (`/v1/tokenize`, `/v1/detokenize`, `/health`; `https` in staging/prod). |
| `DPS_TOKENIZER_TOKEN` | — | secret (`_FILE`) | M5 | Bearer token of the tokenization adapter. |
| `DPS_TOKENIZER_CA_FILE` | — |  | M5 | CA bundle for the adapter; else `DPS_SSL_CERT_FILE`. |
| `DPS_TOKENIZER_TIMEOUT_SECONDS` | `10.0` |  | M5 | Timeout of one tokenization call. |
| `DPS_TOKENIZER_BATCH` | `500` |  | M5 | Values per tokenize or detokenize call. |
| `DPS_TOKENIZER_RETRIES` | `2` |  | M5 | Retries on 429, 5xx and transport errors. |
| `DPS_KEY_CACHE_SECONDS` | `30` |  | M5 | DB-backed API keys are refreshed this often (a disable or rotation reaches other replicas within it). |
| `DPS_KEY_MAX_TTL_DAYS` | `365` |  | M5 | Longest lifetime of a DB-backed key. |
| `DPS_KEY_ROTATION_MAX_AGE_DAYS` | `90` |  | M5 | Keys older than this and never rotated fail the `keys_rotated` posture check. |
| `DPS_KEY_ROTATION_MAX_GRACE_HOURS` | `168` |  | M5 | Longest grace period of a key rotation. |
| `DPS_REPORT_PDF_ENABLED` | `true` |  | M5 | Evidence-pack and roll-up PDFs; `false` = 404 `pdf_disabled`. |
| `DPS_ROLLUP_RETENTION_DAYS` | `400` |  | M5 | Report roll-up and daily-usage rows older than this are deleted. |
| `DPS_EGRESS_TLS_REQUIRED` | `false` |  | M5 | TLS-only egress: every egress URL `https` and every connection TLS (422 `tls_required`); required in staging/prod. |
| `DPS_CORS_ORIGINS` | `*` |  | M5 | Allowed CORS origins, a comma list (empty = same-origin only); never `*` in staging/prod. |
| `DPS_VERIFY_WARN_ALLOWED` | `true` |  | M5 | Whether callers may relax the leakage sweep to `warn`; `false` in staging/prod (strict verification at boot). |
| `DPS_LLM_EGRESS_CHECK` | `strict` |  | M5 | Egress verifier on every LLM prompt: `strict` blocks a raw value (staging/prod), `warn` counts it. |
| `DPS_UTILITY_MAX_ROWS` | `5000` |  | M5 | Train-on-synthetic, test-on-real budget per job. |
| `DPS_MIA_MIN_HOLDOUT` | `50` |  | M5 | Holdout rows the membership-inference proxy needs, else not run. |
| `DPS_MIA_AUC_TARGET` | `0.6` |  | M5 | Membership-inference AUC at or below which the proxy passes. |
| `DPS_MOSTLY_DP_ENABLED` | `false` |  | M5 | MOSTLY AI differential-privacy training. |
| `DPS_MOSTLY_DP_MAX_EPSILON` | `5.0` |  | M5 | Largest epsilon a DP run may spend. |
| `DPS_MOSTLY_DP_DELTA` | `1e-05` |  | M5 | Delta of a DP run (0 < delta < 1). |
| `DPS_MOSTLY_LINKED_CHILD_ROWS` | `200000` |  | M5 | MOSTLY linked generation: most child rows read for training. |
<!-- settings-table:end -->

**Compose, test and tool variables (not read by the app).** `DPS_IMAGE_TAG` (default `local`; the
OCR mock `latest`) and the host ports `DPS_APP_PORT` 8400, `DPS_DB_PORT` 5441, `DPS_SOURCE_PG_PORT`
5442, `DPS_MINIO_PORT` 9010, `DPS_MINIO_CONSOLE_PORT` 9011, `DPS_OCR_PORT` 5016, `DPS_ORACLE_PORT`
1522, `DPS_MONGO_PORT` 27018, `DPS_TOKENIZER_MOCK_PORT` 8420 (and, at the M4 merge,
`DPS_DEMO_SITE_PORT` 8410); compose also passes through the app settings it names (e.g.
`DPS_JOB_KEK`, `DPS_FPE_ENABLED`, `DPS_LLM_PROVIDER`, the Stellar/COIN variables) with local
defaults. Tests and tools take endpoints only from `DPS_TEST_*` (contract §12.2), whose defaults
follow the same port variables: `DPS_TEST_APP_URL`, `DPS_TEST_PG_DSN`, `DPS_TEST_SOURCE_PG_DSN`,
`DPS_TEST_S3_ENDPOINT`, `DPS_TEST_OCR_URL`, `DPS_TEST_ORACLE_DSN` / `_USER` / `_PASSWORD`,
`DPS_TEST_MONGO_URI`, `DPS_TEST_DEMO_SITE`; `DPS_TEST_REQUIRE_SERVICES=1` turns a missing service
from a skip into a failure (every milestone gate sets it). The tools read `DPS_BASE_URL` /
`DPS_TEST_APP_URL` and `DPS_API_KEY` (default: the local demo key); `tools/smoke_test.py` also
`DPS_SMOKE_WAIT_SECONDS` (90), `DPS_SMOKE_REQUIRE_OCR=1` (fail instead of skipping its OCR checks),
`DPS_SMOKE_M2=auto|on|off` and `DPS_SMOKE_M3_TIMEOUT` (600 s for `--m3`); the console checks
`DPS_SHOTS_DIR` (screenshot root, default the git-ignored `reports/console/`) and
`DPS_PLAYWRIGHT_CHROMIUM` (a Chromium binary when Playwright's build 1187 is not cached);
`tools/scratch_pg.sh` `DPS_SCRATCH_DIR`.

### 3.3 Production posture (the boot guard)

`DPS_ENV=staging|prod` runs `dps.posture` before the app is built; a violation stops the process,
naming the setting, never its value. It requires:

- auth on, `DPS_BOOTSTRAP_API_KEY` unset, `DPS_API_KEYS` set (from `_FILE`), `DPS_LLM_PROVIDER` not
  `stub`; `stellar` needs `DPS_SSL_CERT_FILE` and both URLs; `DPS_COIN_TLS_INSECURE=true` only with
  `DPS_ALLOW_INSECURE_COIN_MINT=true`; audit on; with `DPS_OCR_PROVIDER=azure` (or `auto` resolving
  to it) a key and an `https` endpoint (`auto` resolving to nothing is refused; Tesseract is
  allowed);
- with `DPS_PG_HOST` set: the KEK from `DPS_JOB_KEK_FILE`, `DPS_INLINE_SECRETS_ALLOWED=false`,
  `DPS_RLS_ENABLED=true`, `DPS_MIGRATIONS_MODE=verify`, `DPS_AUDIT_STRICT=true`,
  `DPS_PG_SSL_ROOT_CERT`, `DPS_EMBEDDED_WORKER=false`;
- secrets only as mounted files: a literal `DPS_JOB_KEK`, `DPS_JOB_KEK_PREVIOUS`, `DPS_API_KEYS`,
  `DPS_COIN_CLIENT_ID`, `DPS_COIN_CLIENT_SECRET`, `DPS_PG_PASSWORD`, `DPS_PG_WORKER_PASSWORD`,
  `DPS_PG_MIGRATION_PASSWORD`, `DPS_TOKENIZER_TOKEN`, `DPS_AZURE_VISION_KEY`, `DPS_S3_ACCESS_KEY_ID`
  or `DPS_S3_SECRET_ACCESS_KEY` environment variable is refused (use `<NAME>_FILE`);
- TLS-only egress: `DPS_EGRESS_TLS_REQUIRED=true`, every egress URL (`DPS_STELLAR_BASE_URL`,
  `DPS_COIN_URL`, `DPS_AZURE_VISION_ENDPOINT`, `DPS_S3_ENDPOINT_URL`, `DPS_TOKENIZER_URL`) `https`
  and, when any is set, `DPS_SSL_CERT_FILE`; `DPS_TOKENIZER_TOKEN_FILE` with
  `DPS_TOKENIZER_PROVIDER=http`; `DPS_CORS_ORIGINS` without `*`;
- **strict verification at boot:** `DPS_VERIFY_WARN_ALLOWED=false`, `DPS_LLM_EGRESS_CHECK=strict`,
  and every shipped preset and `DPS_DEFAULT_POLICY` with `verify.leakage_check: strict`. At request
  time a policy (inline, preset override or stored) with `leakage_check: warn`, or a run with
  `options.verify=warn`, is then 422 `verify_warn_disallowed`, and publishing such a tenant policy
  is refused the same way;
- (at the M4 merge) `DPS_SCAN_STORE_UNMASKED_ORIGINALS=false` and the browser-worker rules.

Staging/prod serve no `/docs` and no `/api/v1/openapi.json`. **TLS-only connections**
(`DPS_EGRESS_TLS_REQUIRED=true`): PostgreSQL needs `options.sslmode=verify-full|verify-ca`, MongoDB
`options.tls=true`, Oracle `options.protocol=tcps` (or a secret `dsn` using `tcps`), S3 an `https`
`endpoint_url` (or none = AWS); `pvc` is exempt. Refused with 422 `tls_required` at `POST
/connections`, at a `PATCH` that changes `params`, and at use (a connection registered before the
switch fails its job with `tls_required`, not retried). A plain-http `DPS_TOKENIZER_URL` with TLS
required stops the app at boot: overlays must ship an `https` tokenizer URL or an empty provider.

Every response carries `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`,
`Referrer-Policy: no-referrer` (HSTS outside `local`); the console HTML a strict CSP (`script-src
'self'`, `frame-ancestors 'none'`). CORS allows only `DPS_CORS_ORIGINS` (empty = no CORS headers),
methods `GET POST PUT PATCH DELETE OPTIONS`, headers `Content-Type X-API-KEY X-DPS-Tenant
X-Request-ID Idempotency-Key`, and exposes `Idempotent-Replay` (keys travel in headers, never
cookies). Error bodies never quote the request: 422s list `{type, loc, msg}` only; an unexpected
error is a 500 `internal error` with only the exception class logged. The console keeps its API
key in `sessionStorage` ("Remember key on this device" moves it to `localStorage`).

## 4. Local stack

One `docker-compose.yml`; host ports bind `127.0.0.1` only and every one is a variable whose
default is the contract port; every image compose builds is tagged `${DPS_IMAGE_TAG:-local}`.

| Profile | Adds |
|---|---|
| (default) | `app`, `dps-db`, `migrate`, `worker` |
| `docs` | `azure-ocr-mock` |
| `sources` | `source-pg`, `minio`, `minio-init`, `demo-secrets`, `seed-sources`, `mongo` |
| `oracle` | `oracle` |
| `tokenizer` | `tokenizer-mock` |
| `scanner` | **(at the M4 merge)** `browser-worker`, `demo-site` |
| `full` | every service above |

| Service | Image | Host port → container | Notes |
|---|---|---|---|
| `app` | `data-privacy-services:${DPS_IMAGE_TAG:-local}` (build `.`) | `${DPS_APP_PORT:-8400}` → 8400 | console + API; `DPS_ENV=local`, the bootstrap key, `DPS_DEFAULT_LOCALE=en_IN`, `DPS_FPE_ENABLED=true`, `DPS_LLM_PROVIDER=stub`, OCR via the mock; `depends_on` dps-db, migrate and (optionally, `required: false`) the OCR mock; read-only root, 256 MB `/tmp` tmpfs (uploads over 1 MiB spool there), `cap_drop: [ALL]`, `no-new-privileges`, `mem_limit: 1536m` |
| `dps-db` | `postgres:16-alpine` | `${DPS_DB_PORT:-5441}` → 5432 | metadata database; `docker/dps-db/initdb/01_roles.sql` creates the roles of §6 (local passwords = role name) and the `dps` database (owner `dps_owner`, timezone UTC); volume `dps-db-data`; superuser `postgres`/`postgres` (local only) |
| `migrate` | the app image | — | one-shot `python -m dps.migrate` as `dps_owner` (`DPS_MIGRATIONS_MODE=auto`), exits 0; app and worker wait for it and run with `verify` |
| `worker` | the app image | — (9400 inside the network) | `python -m dps.worker`, `DPS_WORKER_CONCURRENCY=4`; scale with `--scale worker=N`; 512 MB `/tmp` tmpfs (files targets spool there), `mem_limit: 1g` |
| `azure-ocr-mock` | `document_intelligence-azure-ocr-mock:${DPS_IMAGE_TAG:-latest}` (build `../mock_azure_ocr`) | `${DPS_OCR_PORT:-5016}` → 5000 | the Azure Computer Vision Read v3.2 contract with Tesseract inside; the app calls it as `http://azure-ocr-mock:5000` with the local key `mock-key` |
| `source-pg` | `postgres:16` | `${DPS_SOURCE_PG_PORT:-5442}` → 5432 | demo source: `docker/source-pg/initdb/01_bank_schema.sql` creates the `bank` database (schema `bank`, 10 tables; target schemas `bank_uat`, `bank_restore`, `bank_perf`) and the logins `dps_reader` (SELECT on `bank`) and `dps_writer` (owns the target schemas) |
| `minio` / `minio-init` | `minio/minio:latest` / `minio/mc:latest` | `${DPS_MINIO_PORT:-9010}` → 9000, `${DPS_MINIO_CONSOLE_PORT:-9011}` → 9001 | artefacts and `files` exports; the one-shot creates the buckets `dps-artefacts` and `dps-exports`; root user `minioadmin`/`minioadmin` (local only) |
| `demo-secrets` | the app image (root) | — | one-shot: writes the demo `secret_ref` files under the demo tenant's folder `default/` of the `dps-secrets` volume (app and worker mount it read-only at `/var/run/secrets/dps`): `dps/src/bank-pg-ro`, `dps/tgt/bank-uat`, `dps/src/bank-restore-rw`, `dps/s3/exports`, the Oracle refs `dps/src/oracle-crm-ro`, `dps/src/oracle-crm-owner`, `dps/tgt/oracle-crm-masked`, `dps/src/oracle-loans-owner` (`username`, `password`, `dsn`) and the MongoDB refs `dps/src/mongo-crm`, `dps/tgt/mongo-crm-uat` (`uri`); removes an older volume's top-level `dps/` folder |
| `seed-sources` | the app image | — | one-shot: `python tools/seed_sources.py --target postgres --rows 20000 --seed 7 --restore-copy` (idempotent; the first run takes about a minute) |
| `oracle` | `gvenzl/oracle-free:23-slim-faststart` | `${DPS_ORACLE_PORT:-1522}` → 1521 | service `FREEPDB1`; `docker/oracle/initdb/01_crm.sql` runs once per fresh container as SYS: users `CRM` (`CUSTOMERS`, `INTERACTIONS`, `KYC_DOCUMENTS`, `ADDRESS_HISTORY`, `CUSTOMER_EVENTS`), `CRM_MASKED` (target), `LOANS` (`APPLICANTS`), `DPS_RO` (CREATE SESSION + SELECT on those six tables); passwords = user name, SYSTEM `dps_local_oracle` (local only); unhealthy until the init script finished (1–3 min); `mem_limit: 2700m`; no volume (`down` resets it) |
| `mongo` | `mongo:7` | `${DPS_MONGO_PORT:-27018}` → 27017 | one-member replica set `rs0` (`--replSet rs0 --bind_ip_all`; the healthcheck initiates it and is healthy only as a writable primary); clients use `directConnection=true`; no auth (local only); `mem_limit: 1g` |
| `tokenizer-mock` | the app image | `${DPS_TOKENIZER_MOCK_PORT:-8420}` → 8420 | vaultless mock of the bank tokenization service (`docker/tokenizer-mock/server.py`, mounted read-only): tokens `tk_` + base32(AES-SIV); bearer token `dps-local-tokenizer-token` (local demo literal); app and worker point `DPS_TOKENIZER_URL` at it; without it `/readyz` reports `tokenizer` down and `external_tokenize` answers 502 |
| `browser-worker`, `demo-site` | **(at the M4 merge)** | `demo-site` `${DPS_DEMO_SITE_PORT:-8410}` → 8410 | the scanner's worker (ops 9401 inside the network) and the demo onboarding site with its `/__received` counter |

Every service has a healthcheck and a `mem_limit`; labels `app=data-privacy-services`,
`component=<role>`, `env=local`. Compose environment shared by app, migrate and worker
(`x-dps-env`): `DPS_PG_HOST=dps-db`, logins `dps_app` / `dps_worker_login` / `dps_owner`,
`DPS_MIGRATIONS_MODE=verify` (`auto` for `migrate`), `DPS_JOB_KEK` = a local demo literal unless
the shell sets one, `DPS_SECRET_DIR=/var/run/secrets/dps`, `DPS_ARTEFACT_DIR=/app/var/artefacts`
and `DPS_EXPORT_ROOT=/app/var/exports` (volumes `dps-artefacts`, `dps-exports`),
`DPS_FPE_ENABLED` (app and worker agree), `DPS_NER_ENABLED=false`, the tokenizer variables.
`docker/compose.sources.env` switches artefacts to MinIO (`DPS_ARTEFACT_STORE=s3`,
`DPS_S3_ENDPOINT_URL=http://minio:9000`).

**Run the local stack:**

```bash
docker compose --env-file docker/compose.sources.env --profile full up -d --build
docker compose ps                    # poll until every service is healthy and the one-shots
                                     # (migrate, minio-init, demo-secrets, seed-sources) exited 0
curl -fsS 127.0.0.1:${DPS_APP_PORT:-8400}/readyz       # "ready": true; db, jobs ok (Oracle: 1-3 min)
python tools/seed_sources.py --target all --rows 20000 --seed 7   # + CRM.* on Oracle, crm.profiles on MongoDB
DPS_SMOKE_REQUIRE_OCR=1 python tools/smoke_test.py --m3           # 15 Layer 1 + 10 documents/OCR/schema
                                     # + 9 job platform + 7 Oracle/MongoDB checks; verdicts only
python tools/demo_m2.py              # 10 bank tables → bank_uat (one job group), in place, append
docker compose --profile full down                   # -v also drops the volumes (a clean slate)
```

(`--env-file docker/compose.sources.env` puts artefacts on MinIO; without it they go to the
`dps-artefacts` volume.) Task 24 adds the M5 compose settings, the M5 smoke block and
`tools/flow_report.py`; Task 26 `python tools/demo_seed.py` and `python tools/e2e_demo.py
--console`. Never use `up --wait`: it fails on the one-shots that exit 0 — poll
`docker compose ps` or `/readyz`. Console and API: `http://127.0.0.1:${DPS_APP_PORT:-8400}`, key
`dps_local_dev_key_change_me` (a local demo literal; staging/prod refuse it).

Smaller stacks: plain `docker compose up --build -d` (app, worker, metadata DB, migrate; then
`DPS_SMOKE_M2=off python tools/smoke_test.py`, the OCR checks skip); `--profile docs` adds the OCR
mock (without it the app starts with `ocr: down` and refuses scans, images and redacted PDFs with
503 `ocr_unavailable`); `docker compose up -d dps-db` alone serves the `db` tests; `docker compose
--profile sources up -d source-pg minio minio-init` the `pg_source` and `s3` tests; `docker compose
--profile oracle --profile sources up -d oracle mongo` the M3 tests. Without Docker,
`tools/scratch_pg.sh start meta|all` runs Homebrew PostgreSQL 16 on `${DPS_DB_PORT:-5441}` (and
`${DPS_SOURCE_PG_PORT:-5442}`) with the same roles (trust auth, timezone UTC; `stop`, `status`).

**Lane-safe stacks.** Parallel stacks on one host each set their own `COMPOSE_PROJECT_NAME` (or run
from their own worktree), `DPS_IMAGE_TAG` and port variables; a lane keeps them in `.env`, and
because `--env-file` replaces `.env` for interpolation it passes both files: `docker compose
--env-file .env --env-file docker/compose.sources.env …`.

```bash
COMPOSE_PROJECT_NAME=dps-lane2 DPS_IMAGE_TAG=lane2 DPS_APP_PORT=8401 DPS_DB_PORT=5451 DPS_OCR_PORT=5017 \
  DPS_SOURCE_PG_PORT=5452 DPS_MINIO_PORT=9020 DPS_MINIO_CONSOLE_PORT=9021 DPS_TOKENIZER_MOCK_PORT=8430 \
  docker compose --env-file docker/compose.sources.env --profile docs --profile sources up --build -d
DPS_APP_PORT=8401 python tools/smoke_test.py
```

**Migrations.** `python -m dps.migrate` applies `dps/migrations/NNN_*.sql` in order as the
migration login (`DPS_PG_MIGRATION_USER`) under a per-schema advisory lock, records each file's
sha256 in `<schema>.dps_migration_ledger` and creates the audit-log partitions for this month and
the next three; `--verify` only checks (runtime login, no DDL) and names every pending or changed
file; `--maintain` also drops audit partitions older than `DPS_AUDIT_RETENTION_MONTHS`. Exit codes:
0 ok, 1 drift/pending or a failed run (class name and SQLSTATE only), 2 `DPS_PG_HOST` unset.
Ranges: M1–M2 001–019 (001–010 used), M3 020–029 (020), M4 030–039, M5 040–059 (040–045). Applied
files are never edited — fix forward with a new number. A running stack picks up a new migration
when the `migrate` one-shot runs again (`docker compose up -d` recreates it). Against the compose
database from the host: `DPS_PG_HOST=127.0.0.1 DPS_PG_PORT=${DPS_DB_PORT:-5441}
DPS_PG_PASSWORD=dps_app DPS_PG_MIGRATION_USER=dps_owner DPS_PG_MIGRATION_PASSWORD=dps_owner python
-m dps.migrate`.

**Demo data.** `python tools/seed_sources.py --target postgres|oracle|mongo|all [--rows 20000]
[--seed 7] [--restore-copy] [--reset]` seeds one synthetic customer space (surrogate-engine values,
no real PII) from `tools/seed_model.py`: the same customer id carries the same person in all three
engines. DSNs default from `DPS_TEST_SOURCE_PG_DSN`, `DPS_TEST_ORACLE_DSN`, `DPS_TEST_MONGO_URI`.
A marker table/collection makes a repeat run with the same `(rows, seed)` print "already seeded" /
"unchanged"; a schema seeded with other parameters exits 3 unless `--reset`. Seed all three engines
with the same `--rows` before a three-way demo (the consistency check samples the newest member).
`tools/demo_m2.py` keeps its job group's key and seed in `var/demo/<group>.json` (git-ignored, mode
0600); after `down -v` it moves on to `<group>-1` and leaves the old file alone.

**Measured throughput** (spec §7; numbers measured on an Apple-silicon Mac, Docker Desktop VM with
12 CPUs and ~15 GiB, `DPS_FPE_ENABLED=true`, one worker with concurrency 4 unless stated; wall clock
from submission to the terminal state; results in `docs/technical/bench/`):

| Run | Rows | Workers | Rows/s | Wall |
|---|---|---|---|---|
| PostgreSQL obfuscate → `table` (16 columns, 8 transformed; 4 × 50 000-row partitions) | 200 000 | 1 | 3 004 | 66.6 s |
| PostgreSQL obfuscate → `in_place` | 200 000 | 1 | 3 196 | 62.6 s |
| PostgreSQL obfuscate → `files` (parquet to MinIO) | 200 000 | 1 | 2 912 | 68.7 s |
| PostgreSQL synthesize → `table` | 200 000 | 1 | 2 021 | 99.0 s |
| PostgreSQL obfuscate → `table` | 200 000 | 3 | 4 128 | 48.5 s |
| PostgreSQL obfuscate → `table`, 12 partitions | 200 000 | 3 | 4 828 | 41.4 s |
| Oracle `CRM.CUSTOMERS` (16 columns incl. a CLOB, 10 transformed) → `table`, 4 partitions | 20 000 | 1 | 942 | 21.2 s |
| MongoDB `crm.profiles` (nested documents and arrays) → `table`, 4 partitions | 20 000 | 1 | 860 | 23.3 s |
| PostgreSQL → `table`, same size and stack as the two rows above | 20 000 | 1 | 2 474 | 8.1 s |

One worker process is bound by one Python interpreter (the transform runs in a thread under the
GIL): more partitions per worker do not add throughput, more worker replicas do. At 20 000 rows the
fixed cost (prepare, preflight, finalize, evidence) is a large share of the wall clock.

## 5. Secrets (names only)

DPS stores no customer data, no reveal key and no synthesis seed: keys and seeds are returned to the
caller once (D2). What a deployment mounts:

| Kubernetes object (Task 25 plan; the manifests are the source of truth once merged) | Feeds | Read by |
|---|---|---|
| Secret `dps-pg-app` | `DPS_PG_PASSWORD_FILE` | `dps-app` |
| Secret `dps-pg-worker` | `DPS_PG_WORKER_PASSWORD_FILE` | `dps-worker`, `dps-browser-worker` |
| Secret `dps-pg-owner` | `DPS_PG_MIGRATION_PASSWORD_FILE` | the `dps-migrate` Job only |
| Secret `dps-job-kek` | `DPS_JOB_KEK_FILE` (and `DPS_JOB_KEK_PREVIOUS_FILE` during a rotation) | app, worker |
| Secret `dps-api-keys` | `DPS_API_KEYS_FILE` (static keys; optional once DB keys exist) | app |
| Secret `dps-coin` | `DPS_COIN_CLIENT_ID_FILE`, `DPS_COIN_CLIENT_SECRET_FILE`, `DPS_COIN_SCOPE_FILE` | app, worker |
| Secret `dps-tokenizer` | `DPS_TOKENIZER_TOKEN_FILE` | app, worker |
| Secret `dps-ocr` | `DPS_AZURE_VISION_KEY_FILE` | app, worker |
| Secret `dps-s3` | `DPS_S3_ACCESS_KEY_ID_FILE`, `DPS_S3_SECRET_ACCESS_KEY_FILE` | app, worker |
| Secret `dps-connector-refs` | the connection `secret_ref` folders under `DPS_SECRET_DIR` | app, worker, browser-worker |
| ConfigMap `dps-ca-bundle` | `DPS_SSL_CERT_FILE`, `DPS_PG_SSL_ROOT_CERT` (and optionally `DPS_COIN_CA_FILE`, `DPS_TOKENIZER_CA_FILE`) | every role |

`DPS_BOOTSTRAP_API_KEY` is a local demo literal and is never set outside a laptop.

**Connection credentials** (`dps/connections.py`, SEC-2). A connection's `secret_ref` names a folder
under the caller's tenant folder, `DPS_SECRET_DIR/<tenant_id>/<ref>/` (the tenant comes from the
authenticated request, never from the reference), from which only these files are read:
`username`, `password`, `dsn` (Oracle), `uri` (MongoDB), `access_key_id` + `secret_access_key`
(S3), `storage_state.json` (the scanner, at the M4 merge) and `ca.pem` (a source's CA bundle;
`options.sslrootcert_ref` follows the same rules). A reference `_shared/<ref>` names
`DPS_SECRET_DIR/_shared/<ref>/` and resolves only for the tenants listed, one per line (`#`
comments), in that folder's `tenants` file. Refused when a connection is created or updated: a
first segment naming another top-level folder (another tenant's) → 403
`secret_ref_foreign_tenant`; an unlisted or missing shared reference → 403 `secret_ref_not_shared`;
a malformed one (empty, `.`/`..`/hidden/`_` segments, upper case) → 422 `secret_ref_invalid`.
Loading applies the same rules again (and refuses a symlink that leaves the tenant's folder),
answering `secret_unavailable`. Inline credentials (dev only) are sealed with the KEK into
`dps_connection.secret_enc` and stop resolving after `DPS_INLINE_SECRET_TTL_HOURS`. Upgrade note:
references mounted at the old `DPS_SECRET_DIR/<ref>/` move to `DPS_SECRET_DIR/<tenant_id>/<ref>/`
(stored `secret_ref` values do not change).

**Who may change a connection (SEC-3).** Any `PATCH` or `DELETE` of a connection that is or becomes
tagged `production` needs the `admin` scope (403 `admin_scope_required`). On a connection holding
credentials, changing a param that decides where they are sent needs the credentials restated in
the same request (or `admin`), else 403 `connection_endpoint_change_requires_credentials`; the list
is `dps.connections.ENDPOINT_PARAMS` (postgres `host port database dbname options.sslmode
options.sslrootcert_ref`; oracle `host port service_name sid dsn` + `options.protocol` and the TLS
options; mongo `uri host port replica_set database options.replicaSet options.directConnection
options.tls`; s3 `endpoint_url bucket region`; pvc none), and `tests/test_connection_endpoints.py`
fails until a new `ConnectionParams` field is classified. Residual: a `jobs` key may still *create*
a connection naming any of its own tenant's refs with an endpoint of its choice — restrict the key
or mount fewer refs per tenant.

**Bootstrap of the first admin key** (staging/prod, DB-backed keys). With the metadata database's
credentials in the environment (any pod with the image): `python -m dps.keys_cli create --name
platform-admin --tenants '*' --scopes '*' --expires-in-days 90` prints one JSON document with the
raw key, once; store it in the secret manager and leave `DPS_BOOTSTRAP_API_KEY` unset. `list`,
`rotate ID [--grace-hours H]`, `disable ID` and `enable ID` manage keys the same way (exit 2 with
`{"error", "detail"}` on a refusal); every mutating command writes an audit row owned by `_system`.

## 6. Services and dependencies

| Dependency | Used by | Contract |
|---|---|---|
| Metadata PostgreSQL 16 (external in production; `dps-db` locally) | app, worker, browser-worker, migrate | schema `DPS_PG_SCHEMA` with tenant row-level security; roles below; `python -m dps.migrate` as `dps_owner`; verified TLS with `DPS_PG_SSL_ROOT_CERT` |
| Object storage (S3; MinIO locally) or a volume | app (artefacts, evidence PDFs), worker (`files` targets, artefacts) | `DPS_ARTEFACT_STORE=s3`: bucket `DPS_S3_BUCKET`, keys `t/<tenant>/<job>/<artefact>.<ext>`; `files` targets write `<prefix>/<job>/part-NNNNN-NNN.<ext>` + `manifest.json` (names never carry a value) |
| Stellar LLM gateway + COIN token mint | app, worker | `<base>/chat/completions` with a COIN OAuth2 token; **typed placeholders only** (P1): every prompt passes the egress verifier (`DPS_LLM_EGRESS_CHECK`) |
| OCR gateway (Azure Computer Vision Read v3.2 contract) or local Tesseract | app (documents) | `DPS_AZURE_VISION_ENDPOINT` + key, health path `/_gw/health` behind the org gateway; page images only (DPS rasterises PDFs) |
| Bank tokenization service | app (`external_tokenize`, reveal), worker | §8 |
| Source and target databases: PostgreSQL, Oracle (thin, 19c/23ai), MongoDB (replica set for `in_place`/`append`) | worker (reads, writes), app (connection tests, describe, count) | per-connection `secret_ref`; read-only transactions on sources; least-privilege checks in `POST /connections/{id}/test` |
| Scan targets (registered UAT hosts) | browser-worker **(at the M4 merge)** | GET/HEAD only; nothing is ever submitted (D4) |

Metadata database roles (`docker/dps-db/initdb/01_roles.sql` locally; platform-created in
production): `dps_owner` (owns the schema, runs migrations), `dps_app_rw` and `dps_worker` (NOLOGIN
privilege roles, no BYPASSRLS), `dps_app` (the API login, member of `dps_app_rw`) and
`dps_worker_login` (the worker login, member of both). The app role has no DELETE on API keys
(keys are disabled, never deleted).

Who talks to whom: `dps-app` → metadata DB, object storage, LLM gateway, OCR gateway,
tokenization service, source/target databases (tests, describe, count); `dps-worker` → metadata DB,
object storage, LLM gateway (opt-in labelling and text rewrite), OCR gateway, tokenization service,
source and target databases, the export volume; `dps-browser-worker` → metadata DB, object
storage, the registered scan hosts; `dps-migrate` → metadata DB only. Nothing calls DPS except
API clients and the console.

**Database specifics worth knowing.**

- PostgreSQL sources: every read runs in `BEGIN READ ONLY` with `statement_timeout`,
  `lock_timeout=5s` and `application_name = dps:<action>:<job id>`; sessions are reset on release
  (`pg_advisory_unlock_all()`, `RESET ALL`); binds are coerced by the prepared statement's types.
  `in_place`/`append` targets keep a write ledger `dps_write_ledger` in the target schema (the
  target login needs `CREATE` there, or a pre-created ledger); a failed job keeps its ledger rows
  for the resume.
- Oracle (python-oracledb 26.0.0 thin): synchronous pool used from worker threads (`min=0`,
  `max=DPS_ORACLE_POOL_MAX`, `expire_time=1`, `call_timeout`); sessions carry `module`, `action`
  and `client_identifier` = the job id, and `NLS_SORT = BINARY NLS_COMP = BINARY`; reads never lock;
  CLOBs come back whole, offsets in UTF-16 units; `CHAR` keys bind as `DB_TYPE_CHAR`,
  `TIMESTAMP(7–9)` keys page through `TO_TIMESTAMP(:v, '…FF9')`; key-less tables are read in ROWID
  ranges (read-only); NVARCHAR2/NCHAR keys run as one partition; `LONG`, `LONG RAW`, `BFILE`,
  `XMLTYPE`, `TIMESTAMP WITH LOCAL TIME ZONE`, `VECTOR`, object types and `ROWID` columns are refused
  (`column_unsupported`). Only driver codes leave the module (`ORA-00942`).
- MongoDB (pymongo `AsyncMongoClient` ≥ 4.13): keyset on `_id`; documents flow through the Layer 1
  JSON engine by leaf path (`$.contact.email`, `$.accounts[*].iban`); untouched values keep their
  BSON types; `in_place` = `$set` of transformed leaves in a transaction with the ledger collection;
  a created target gets the `_id` index only (`indexes_not_copied`); every operation carries
  `comment = "dps:<action>:<job id>"`; errors keep only `mongo:<code>`.
- Cross-system joins need format-preserving keys: Oracle `NUMBER`, PostgreSQL `bigint` and MongoDB
  `"CUST-…"` ids join under one job group only with `fpe_digits` (`DPS_FPE_ENABLED=true`).

## 7. OpenShift

**(Task 25)** `deploy/openshift/` in the Wega layout — base, components (`network-policies`,
`monitoring`, `dev-mocks`) and the `dev`/`prod` overlays with release placeholders (zero digests,
`.invalid` hosts, `192.0.2.0/24` CIDRs) listed in `deploy/openshift/README.md`, whose steps and
parameter table this section summarises once merged. The planned shape (M5 plan Task 25):

- One image, four workloads: Deployment `dps-app` (API + console, port 8400, Route with edge TLS),
  Deployment `dps-worker` (ops 9400, the `dps-exports` PVC at `DPS_EXPORT_ROOT`), Deployment
  `dps-browser-worker` (image `data-privacy-services-browser`, ops 9401, `/dev/shm` in memory), and
  the Job `dps-migrate-<release>` (the only workload that mounts `dps-pg-owner`; it runs with
  `DPS_MIGRATIONS_MODE=auto`). ServiceAccounts `dps-app`, `dps-worker`, `dps-browser-worker`,
  `dps-migrate`, all without a mounted token.
- The metadata database is the platform's PostgreSQL 16 behind the ExternalName Service `dps-db`;
  Oracle Free, MongoDB, MinIO and the source PostgreSQL are never deployed there (bank systems).
- Every pod `runAsNonRoot`, seccomp `RuntimeDefault`, no `runAsUser`; every container without
  privilege escalation, read-only root filesystem, all capabilities dropped, requests and limits;
  every secret a mounted file (§5); images pinned by digest.
- Release order: apply the overlay, wait for `dps-migrate-<release>` to complete, then roll the
  workers, then the app (`oc rollout status` for each); run `python tools/smoke_test.py` through the
  route with a key from `python -m dps.keys_cli`.

## 8. Tokenization service contract

For the bank's integration team: DPS calls an adapter in front of the bank's tokenization product
(Voltage, Protegrity, Thales or in-house) for the `external_tokenize` method.

- `POST {DPS_TOKENIZER_URL}/v1/tokenize` `{"profile", "entity_type", "values": [...]}` → 200
  `{"tokens": [...]}`; `POST …/v1/detokenize` `{"profile", "entity_type", "tokens": [...]}` → 200
  `{"values": [...]}`; `GET …/health` → 200.
- `Authorization: Bearer <DPS_TOKENIZER_TOKEN(_FILE)>`; TLS verified with `DPS_TOKENIZER_CA_FILE`
  (else `DPS_SSL_CERT_FILE`, else the system CAs); `https` required when
  `DPS_EGRESS_TLS_REQUIRED=true`.
- Answers keep order and length, at most `DPS_TOKENIZER_BATCH` items per call, deterministic per
  (profile, entity type, value); a token equal to its input, a wrong length or a non-string is
  refused as malformed. 429/5xx and transport errors are retried `DPS_TOKENIZER_RETRIES` times;
  Layer 1 then answers 502 and a job retries the batch (`tokenization_unavailable`).
- Policy: `treat: {SSN: {method: external_tokenize, profile: ssn-fpe}}` — `profile` is passed
  through. Reveal through DPS detokenizes in one call per entity type and profile.
- **SEC-4 (the token binding).** DPS holds the service's credentials, so it detokenizes only tokens
  it produced under the presented key: each external hint carries a `bind` tag keyed by the caller's
  DEK and a server key derived from `DPS_JOB_KEK`; an unbound or mismatched tag is 403
  `external_token_not_bound` before any call, and every batch is audited first (`action:
  external.detokenize`, entity type and count). `external_tokenize` therefore needs `DPS_JOB_KEK`
  on the app (else 400, plan reason `|external:unbound`); keep `DPS_JOB_KEK_PREVIOUS` after a KEK
  rotation for as long as callers may reveal tokens bound under the old KEK. `tools/reveal_rows.py`
  reveals `external_tokenize` columns with the key holder's own tokenizer client
  (`--tokenizer-url`, `--tokenizer-token-file`).

## 9. Runbooks

**API-key rotation.** `POST /api/v1/admin/keys/{id}/rotate {"grace_hours": H}` (or `python -m
dps.keys_cli rotate ID --grace-hours H`) mints the successor (raw key in that answer only,
`Cache-Control: no-store`); the old key keeps working for `H` hours (0–`DPS_KEY_ROTATION_MAX_GRACE_HOURS`,
default 24; `0` disables it now — use 0 for a compromised key). A key is rotated at most once and a
rotated or expired key cannot be re-enabled (409). DB keys propagate within `DPS_KEY_CACHE_SECONDS`
to other replicas (at once on the replica that served the change). The posture check
`keys_rotated` lists active keys older than `DPS_KEY_ROTATION_MAX_AGE_DAYS` that were never
rotated (names and ages only). Grants never exceed the creator's (403 `scope_escalation` /
`tenant_escalation`).

**KEK rotation** (`python -m dps.rekey [--dry-run]`). Mount the new KEK as `DPS_JOB_KEK_FILE` and
the old one as `DPS_JOB_KEK_PREVIOUS_FILE`; roll the app and worker deployments; run `python -m
dps.rekey` once from any pod with the image, the worker's database login and the API login (it
re-seals `dps_job.key_enc`/`spec_enc` through the worker pool and `dps_connection.secret_enc` per
tenant); confirm the printed JSON line `{rows, resealed, already_current, unreadable}` has
`"unreadable": 0` (exit 3 otherwise — those rows are unchanged; exit 2 = no KEK or no database);
remove `DPS_JOB_KEK_PREVIOUS_FILE` and roll again — but keep it while callers may reveal
`external_tokenize` tokens bound under it (§8). Until `dps.rekey` has run, jobs sealed under the
old KEK cannot be opened by workers holding only the new one: run it right after the first roll.
Rotating the KEK also changes job-route request fingerprints: a retry that straddles it with the
same `Idempotency-Key` is refused (`idempotency_key_reused`) — mint a new key.

**Retention.** Every worker schedules at most one `retention` job per
`DPS_RETENTION_INTERVAL_HOURS` across replicas (owner `_system`). It deletes terminal jobs past
their expiry (`DPS_RETENTION_DAYS`, or the tenant's own `retention_days`) with their artefacts,
stale proposed/superseded snapshots and expired inline secrets, purges leftover sealed keys, ages
out report roll-ups and daily usage older than `DPS_ROLLUP_RETENTION_DAYS`, refreshes the last
three days' roll-ups of tenants with recent jobs, records its counts in
`progress.metrics.retention` and writes one `retention.run` audit event. It never deletes the audit
log (`python -m dps.migrate --maintain` drops whole audit partitions past
`DPS_AUDIT_RETENTION_MONTHS`) or `files` exports. On demand: `POST /api/v1/admin/retention/run
{"dry_run": true}` with an admin key granted every tenant (`*`).

**Daily quotas.** `PATCH /api/v1/admin/tenants/{id} {"daily_row_quota": N}` (`null` = no quota).
Rows written count per tenant per UTC day; once they reach the quota `POST /jobs/run` answers 429
`daily_row_quota_exceeded` and queued partitions wait — new runs are accepted again after 00:00
UTC. Job caps: `max_running` / `max_queued` per tenant (defaults `DPS_JOB_TENANT_MAX_RUNNING` /
`_MAX_QUEUED`).

**Adding scanner hosts.** `PATCH /api/v1/admin/tenants/{id} {"registered_hosts": ["uat.bank.example"]}`
(bare hostnames or URLs, normalised to lower-case hosts; no credentials, no wildcards; at most 100).
A scan of any other host is refused **(at the M4 merge:** `422 host_not_registered`; the local
fallback list is `DPS_SCAN_REGISTERED_HOSTS`**)**; in OpenShift also widen the
`egress-scan-targets` NetworkPolicy parameter (Task 25).

**A failed leakage sweep.** A job whose post-write sweep finds a source value in its output fails
closed: the partition (or the Layer 1 request) fails with `leakage`, nothing more is written for
that batch, the evidence pack (when one is sealed) says `verification.passed: false` with the hit
count and paths (never the value), `GET /reports/jobs` lists it under `failed_sweeps`, the audit
row carries the outcome, and `dps_verification_failures_total` grows. A run whose written sample
shows personal data in columns kept as not personal is `review_required` instead (amber, never
"verified"). Re-run after fixing the cause (review the snapshot's methods, or the policy) with
`POST /jobs/{id}/retry` — a retry after the key was purged needs `consistency.key_material`
resubmitted (`key_material_required`).

**Database outage.** Layer 1 keeps serving (the engine and document routes never need the
database). A database that is down when the app starts is retried every 10 s while the
job-platform routes answer 503 `db_unavailable` and `/readyz` reports `db` down. A database lost
after start is not re-probed: routes that need it fail with a value-free 500 `internal_error`
until it returns (restart the pods if it does not recover; see `CHANGELOG.md` "Known
limitations"). The workers' `/readyz` checks the database on every probe (`SELECT 1`). DB-backed
API keys keep working from the in-memory snapshot and fail closed after ten failed refreshes
(static `DPS_API_KEYS` keep working). With `DPS_AUDIT_STRICT=true` a request whose audit row cannot
be written fails 503 `audit_unavailable`.

## 10. Probes, metrics, alerts

- `GET /health` — liveness `{"status": "ok"}`.
- `GET /readyz` — `{"ready", "components"}`: `api-core` (the only required component; false while
  stopping), `llm-gateway` (`provider`, `coin_tls verified|insecure`), `ner` (`presidio` /
  `patterns-only`), `fpe` (`ff1` / `det_token fallback`), `ocr` (`up`, `down`, `down:<status>`,
  `error`, `unconfigured`, `tesseract_missing`; `engine` `azure-read-v3.2` or `tesseract-<v>`;
  probed every `DPS_OCR_PROBE_INTERVAL_SECONDS`), `tokenizer` (informational), `db` (`up` / `down` /
  `unconfigured`, `migrations: verified`), `jobs` (ok when the database is up, migrations verify
  and the KEK is loaded), `keys` (`static` or `db+static`); (at the M4 merge) `browser` from the
  browser workers' heartbeats. 503 only when a required component is down.
- Worker ops server (`DPS_WORKER_METRICS_PORT`, 9400): `/health`, `/readyz` (`db` = `SELECT 1`,
  `migrations` verified, `kek` loaded, `queue` claiming) and `/metrics`. SIGTERM drains: stop
  claiming, wait `DPS_JOB_DRAIN_TIMEOUT_SECONDS` while heartbeats keep the leases, release the rest
  without charging an attempt, exit 0.
- `GET /metrics` (Prometheus): `dps_requests_total{route,outcome}`, `dps_request_seconds{route}`,
  `dps_findings_total{entity_type,detector}`, `dps_transforms_total{mode,method}`,
  `dps_verification_failures_total{mode}`, `dps_llm_calls_total{provider,purpose,outcome}`,
  **`dps_llm_raw_pii_sent_total` (must stay 0)**, `dps_llm_label_fields_total{outcome}`,
  `dps_document_pages_total{format,source}`, `dps_redacted_boxes_total`,
  `dps_ocr_calls_total{engine,outcome}`, `dps_ocr_seconds`; the job platform
  `dps_jobs_claimed_total{kind,phase}`, `dps_partitions_finished_total{state}`,
  `dps_leases_lost_total{what}`, `dps_rows_written_total{mode,write_mode}`,
  `dps_queue_depth{table,state}`, `dps_audit_dropped_total`,
  `dps_connector_calls_total{engine,op,outcome}`, `dps_consistency_checks_total{outcome}`; M5
  `dps_tabular_resamples_total`, `dps_external_token_calls_total{op,outcome}`,
  `dps_auth_keys_loaded`, `dps_llm_calls_by_tenant_total{tenant_id,purpose}`,
  **`dps_llm_raw_pii_sent_by_tenant_total{tenant_id}` (must stay 0)** and
  `dps_llm_exposure_blocked_by_tenant_total{tenant_id}` (tenant `-` outside a request or job).
  Counters, readiness and the rate limiter are per process: one uvicorn process per container,
  scale with replicas.
- Alerts (the PrometheusRule of Task 25): `DpsRawPiiSentToLlm` (critical, any increase of either
  raw-PII counter — must never fire), `DpsLlmExposureBlocked` (warning: the egress verifier stopped
  a prompt), plus `DpsVerificationFailures`, `DpsExactRowResamplesHigh`, `DpsTokenizerFailing`,
  `DpsNoDbKeysLoaded`, `DpsWorkersAbsent`.
- Audit: one value-free event per request and job action (trace id, route, tenant, key id, mode,
  policy label, outcome, counts, methods, verification, duration — never a value or a key), in the
  audit log table and as an `audit` log line; `GET /api/v1/admin/audit` (JSON or CSV with a formula
  guard) searches it. Logs go to stdout through an allow-list of field names (JSON outside `local`);
  driver loggers (`pymongo`, `botocore`, `boto3`, `s3transfer`, `urllib3`, `httpx`, `httpcore`,
  document libraries) are capped at WARNING and `sqlglot` at CRITICAL whatever `DPS_LOG_LEVEL` is.
- Runtime posture: `GET /api/v1/admin/posture` (admin; needs no database): eleven checks
  (`auth_enabled`, `bootstrap_key_absent`, `api_keys_from_file`, `tls_egress`, `audit_strict`,
  `leakage_strict`, `llm_not_stub`, `kek_from_secret`, `keys_rotated`, `rls_posture`,
  `migrations_verified`) evaluated as if production in every environment, `enforced` in
  staging/prod; tenant policies and keys scoped to the caller's tenants (a wildcard admin key sees
  the platform).
- `GET /docs` and `GET /api/v1/openapi.json` outside staging/prod (Swagger UI loads from a public
  CDN; on an air-gapped network use the JSON).

## 11. Traps

Build and run:

- `uvicorn dps.app:create_app --factory` — there is no module-level `app`.
- `docker compose up --wait` fails on the one-shots that exit 0: start, then poll `docker compose
  ps` / `/readyz`.
- `--env-file X` replaces `.env` for interpolation: a lane passes `--env-file .env` first, or its
  image tag and ports fall back to the defaults (another stack's).
- The root filesystem is read-only under compose: anything that writes uses `/tmp` (tmpfs) or a
  volume. Multipart uploads above 1 MiB spool to `/tmp` — mount it as an in-memory `emptyDir` in
  OpenShift too.
- The console is built with `base: '/'`: hosting it under a path prefix needs a gateway rewrite to
  the root.
- The access log is off (`--no-access-log`): it is stdlib logging and would bypass the allow-list.
  `DPS_ENV=local` logs are console-rendered with ANSI colours; use `dev` to see JSON.
- `tokenizer-mock` publishes `${DPS_TOKENIZER_MOCK_PORT:-8420}`; next to another stack on the same
  host give it another port.
- `../mock_azure_ocr` (the OCR mock's build context) is the directory this repo is nested in; its
  build pulls `ghcr.io/astral-sh/uv:latest`, so offline tag the cached image for the stack
  (`docker tag document_intelligence-azure-ocr-mock:latest
  document_intelligence-azure-ocr-mock:${DPS_IMAGE_TAG}`) and start without `--build`.

Documents and OCR:

- PDFium is not thread-safe: renders are serialised per process (`PDFIUM_LOCK`); scale with
  replicas. The OCR client refuses an `Operation-Location` on another origin than
  `DPS_AZURE_VISION_ENDPOINT`. OCR engines get page images only.
- A redacted PDF is image-only by design and hides what was **detected**: with NER off, values the
  patterns and cues miss stay visible in the raster, and the OCR re-read cannot flag them either.
  When OCR is configured every redacted page costs one more OCR call.
- With `DPS_OCR_PROVIDER=azure` (compose's default) and no `--profile docs`, OCR is configured but
  down: scans, images and redacted PDFs are refused with 503 `ocr_unavailable` (fail closed).
- `redact.py` sets `reportlab.rl_config.useA85 = 0` process-wide. Evidence and roll-up PDFs use
  reportlab's built-in Helvetica: Latin-1 only, other characters are folded; very long lists in one
  table cell are shortened in the PDF ("… and N more"), the JSON pack keeps every entry.
- `tests/test_eval_report.py` fails when the detectors change until the report is regenerated
  (`python tools/eval_detectors.py run …`) — by design. Faker's `es_MX` `curp()`/`rfc()` ignore the
  seed: never use them where determinism matters.

Engine and policies:

- FPE fallbacks: with `DPS_FPE_ENABLED=false` the plan turns `fpe_*` into `det_token`
  (`…|fallback:fpe_disabled`); a domain under 10⁶ falls back too (`fallback:fpe_domain_too_small`);
  `report.fallbacks` counts them, one per value.
- Presidio is recall-only; checksum-validated identifiers come from the pattern tier. Without the
  `ner` image names in free text are found by their cues only, and reports say so
  (`verification.coverage`).
- UK mobiles in the Ofcom drama range (`+44 7700 900xxx`, what the surrogate engine generates for
  GB) are not detected as phones in free text; a free-text column repeating such a number fails
  closed with `leakage`. The demo seed keeps GB phones out of free text.
- Synthesis under a `strict` policy is refused about once in 3 000–5 000 seeds when a reshaped
  identifier holds another value of the same record by construction; retry without the seed.
- `@name` policy references resolve when a request or job is accepted: a job records the body it
  ran with; publishing a new version changes only later runs (pinned `@name:vN` never).

Jobs and connectors:

- The key of a run (and of a job group) is returned **once**, in the create answer; keep it. A
  retry after the key was purged needs it resubmitted (`key_material_required`).
- `in_place` never rewrites a key column (`pk_transform_in_place`; review the key to `keep`) and
  refuses outputs that cannot fit an existing column (`length_overflow`, caught before a job exists
  by `POST /jobs/run/validate`, which wizard step 5 asks). An `append` to the source table needs an
  `offset` of at least `max(key) + 1 - min(selected key)` or `generate` on an identity or owned
  ascending sequence (`pk_strategy_invalid` otherwise).
- On resume the target's write ledger is authoritative over the partition checkpoint; a failed
  partition with `ledger_conflict` is never written again.
- Created target tables copy column definitions only (no foreign keys, indexes, defaults); Oracle
  created targets widen `det_token` text columns (`EMAIL`, `ADDR_LINE1` → CLOB, `CHAR` →
  `VARCHAR2`).
- Discovery's `MIXED_PII` columns are replaced whole; a snapshot accepted before a detector change
  keeps its old classification — run discovery again. A superseded snapshot still drives the jobs
  submitted with it; only a dropped or retyped column stops them (`snapshot_stale` at prepare).
- Wizard: when discovery maps an entity key (e.g. `bank_restore.customers.customer_id`) to
  `fpe_digits`, step 4 shows a failed key check for In place and stays closed — press **Change it
  in step 3**, **Run again**, set the key's method to `keep` and accept the new snapshot (an
  accepted snapshot never changes). Step 3's review table scrolls sideways inside its card at
  1440 px.
- Tenant ids starting with `_` are DPS pseudo-tenants (`_system`): every key is refused 403
  `tenant_reserved` for them; the job platform takes lower-case tenant ids only (400
  `tenant_invalid`).
- Selections are validated with sqlglot (pinned behaviour by `tests/connectors/test_selection.py`;
  after an upgrade add renamed node classes to `_node_types(...)`, never loosen a test). A
  qualified name whose last part starts with a denied prefix is refused (reference such columns
  unqualified).
- Oracle Free's first start takes 1–3 minutes; `docker compose down` resets it (no volume) — reseed
  with `tools/seed_sources.py --target oracle`. An Oracle container that reports `unhealthy` for
  good had its init script fail (it stops at the first error): read `docker compose logs oracle`,
  fix the script, `docker compose rm -sf oracle` and start it again (init runs once per fresh
  container); a container initialised before M3 Task 15 must be recreated for the widened
  `NATIONAL_ID`. In `docker/oracle/initdb/*.sql` a statement's `;` must end its line. Oracle 23ai
  reports a missing privilege as `ORA-41900` (19c `ORA-01031`). `date_shift` keeps the
  sub-microsecond digits of `TIMESTAMP(9)` values.
- python-oracledb 26.0.0 thin: a call over `call_timeout` normally fails with `DPY-4024`, but when
  the server cannot answer the break the driver closes the connection (`DPY-4011`) and setting
  `call_timeout` on that connection crashes the process (SIGBUS) — a session that failed inside a
  checkout is dropped, never reused. A bare `datetime` binds as DATE (fractions lost): every
  TIMESTAMP bind goes through `DB_TYPE_TIMESTAMP`. A MERGE refuses a LOB bind over 32,767 bytes,
  and an `executemany` mixing temporary LOBs with `str` stores NULL silently: a batch's LOB column
  binds as temporary LOBs for every row when any value is over the limit. `SAMPLE (p) SEED (s)` is
  not stable: a selection's `sample` is a hash of the key instead.
- Identity keys and same-table `append`: explicit inserts can run ahead of the identity sequence;
  check `identity next value > MAX(key)` before such a run (`pk_collision` otherwise).
- MongoDB transactions need a replica set (a standalone server gets `transactions_unsupported`);
  field names with `.` or a leading `$` cannot be addressed (`path_unsupported`); without NER
  discovery does not classify nested address fields (map them in review); `files` targets are not
  yet tested for MongoDB documents.
- The consistency check samples the newest member's target: re-running one member re-anchors it;
  `date_shift` and persona surrogates are anchor-dependent (informational in the check). A
  `smoke_test.py` run without `--m3` rewrites `bank_uat.customers_smoke` without the group key, so a
  later check of an older smoke group fails its PostgreSQL pairs: run a fresh `--m3` smoke instead.
- A full `pytest -q` reseeds the shared Oracle/MongoDB demo space at test sizes and the integration
  tests share one metadata DB, source DB and bucket: run the gates one at a time per stack, and
  reseed (`tools/seed_sources.py --target all --rows 20000 --seed 7`) before a demo or smoke run
  that follows them. Run `tools/smoke_test.py` before the M2 gate on a fresh stack: the console
  tests need the demo connections it creates.

Synthesis:

- Tabular synthesis needs ≥ 50 sampled rows and applies to `table`/`files` targets; the same seed
  reproduces a target only when the whole selection fits in `DPS_COPULA_FIT_ROWS +
  DPS_COPULA_HOLDOUT_ROWS`. A table whose every non-key combination is real fails prepare with
  `leakage`. Tabular synthesis is refused for a MongoDB source (`tabular_invalid`). The MOSTLY engine
  is not in the image; linked generation writes a new dataset with new keys to `files` only.
- D-A: synthesize jobs never copy a key or identifier — keys, join columns and kept MEDIUM+ columns
  become keyed pseudonyms under the job's seed (use one job group for tables that must join);
  integer pseudonyms lie in bands no real key reaches (`[10⁹, 2·10⁹)`, `[10¹⁸, 2·10¹⁸)`);
  `smallint`/narrow `numeric` identifiers are refused (`synthesize_pseudonym_unsupported`), as is
  an `in_place` synthesize of a table with a sensitive key. Government identifiers are drawn from
  never-issued ranges, and after a write the run checks every written identifier against the source
  (`post_sample.identifier_check`); any hit makes the pack `review_required`. Types without such a
  range (passport, driving licence, voter id, MRZ, …) are always `review_required` when written.
  A key the owner reviews as not personal is a technical id and is kept. A text pseudonym keeps the
  key's shape, so it may by chance equal another real key of that shape (nil for UUIDs and long
  mixed keys, not for short digit-only keys); the permutation never maps a value to itself. An
  `append` into a table that already held rows checks only the rows after the table's pre-run
  integer key (`plan.prepare.identifier_floor`). Tools that need rule-valid PANs (the demo seed,
  demo documents, the evaluation corpus) draw them themselves (`evaluation.corpus.rule_valid_pan`).
- Synthetic names come from a finite pool: compare a target with its source row for row, not by
  set (a name can exist in both by chance).
- DB-backed keys propagate within `DPS_KEY_CACHE_SECONDS`; roll-ups refresh on demand (60 s) and on
  each retention run; a roll-up for a future day does not exist (404).

Console and tooling:

- New console CSS goes under its page root (`.<name>-page`): short class names are shared across
  pages.
- The console checks write their screenshots to `reports/console/<name>/` (git-ignored);
  `DPS_SHOTS_DIR=docs/superpowers/build` refreshes the committed `m1-console`/`m3-shots` folders on
  purpose. `playwright==1.55.0` wants Chromium build 1187; `tools/console_check.py` falls back to
  the newest cached `chrome-headless-shell` (or `DPS_PLAYWRIGHT_CHROMIUM`).

## 12. Verification gates

The Definition of Done of the release (M5 plan), each with `DPS_TEST_REQUIRE_SERVICES=1` so a
missing service fails instead of skipping; the release records every summary line under "Last
verified" above.

```bash
ruff format --check dps tests tools && ruff check dps tests tools && mypy dps && pytest -q
python tools/settings_table.py --check                     # this file's table is current
(cd frontend && npm ci && npm run typecheck && npm test && npm run build)
docker compose --profile full up --build -d                # then poll /readyz until "ready": true
DPS_TEST_REQUIRE_SERVICES=1 pytest -q -m "docs or ocr"                 # M1
DPS_TEST_REQUIRE_SERVICES=1 pytest -q -m "db or pg_source or s3"       # M2 (+ M5 db tests)
DPS_TEST_REQUIRE_SERVICES=1 pytest -q -m slow tests/connectors/test_resume_after_kill.py
DPS_TEST_REQUIRE_SERVICES=1 pytest -q -m "oracle or mongo"             # M3 (tests/e2e incl.)
DPS_TEST_REQUIRE_SERVICES=1 pytest -q -m browser                      # M4 (at its merge)
DPS_TEST_REQUIRE_SERVICES=1 pytest -q -m deploy                       # Task 25 (kustomize)
DPS_SMOKE_REQUIRE_OCR=1 python tools/smoke_test.py --m3                # every block, no SKIP
python tools/flow_report.py                                          # Task 24
python tools/demo_seed.py && python tools/e2e_demo.py --console      # Task 26, under 10:00
pytest -q tests/test_no_values_in_logs_m1.py tests/test_no_values_in_logs_m2.py tests/test_no_values_in_logs_m3.py
git ls-files | xargs grep -nE "AKIA[0-9A-Z]{16}|BEGIN (RSA |EC )?PRIVATE KEY"   # nothing
```

The M3 end-to-end tests (`tests/e2e/`: the shared seed ids, the three-way consistency join through
the API and a worker subprocess, resume after kill on Oracle and MongoDB) need `dps-db`,
`source-pg`, `oracle` and `mongo` and take about 3 minutes; they seed `CRM.*`, `crm.profiles` and a
PostgreSQL copy in schema `m3e2e` themselves and leave `bank` alone.

Proofs a verifier produces beyond the tests: for every target written, a source-vs-target count of
each sensitive column's values found in the source (counts only; 0); the demo site's `/__received`
non-GET count (0); redacted PDFs with 0 extractable characters and an OCR re-read finding 0
originals; a scan of every container's logs for the demo values and key material (0 hits);
`dps_llm_raw_pii_sent_total` 0; the console walked against the mockups at 1440 and 390 px in light
and dark with 0 console errors. Accepted skips: `ml` (the `synth-ml` extra), `ner` without the
extra, `tesseract` without a host binary.

## 13. Release

`v1.0.0` (M5 Task 28): the version is set in `pyproject.toml`, `dps/__init__.py`,
`frontend/package.json` and its lock (`npm version 1.0.0 --no-git-tag-version`); every gate of §12
runs from a clean stack (`docker compose --profile full down -v && docker compose --profile full up
--build -d`); the summaries go under "Last verified"; the commit is `chore(release): v1.0.0` and the
annotated tag `v1.0.0`. Milestone tags on `main`: `v0.1.0-m0`, `v0.2.0-m1`, `v0.3.0-m2`,
`v0.4.0-m3`, `v0.5.0-m4` (M5 ships as `v1.0.0`). Images are tagged with the semver and the git sha
(`data-privacy-services:1.0.0`, `data-privacy-services-browser:1.0.0` locally:
`docker tag data-privacy-services:local data-privacy-services:1.0.0`); production deploys pin them
by digest in the release overlay (§7). Pushes go through the owner's checked script
(`tools/dev/autopush.sh`: author check, no trailers, gitleaks clean).
